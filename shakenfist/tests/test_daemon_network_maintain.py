# Copyright 2019 Michael Still and contributors

"""Tests for shakenfist.daemons.network.maintain.

maintain.py is a discovery-only pass governed by the five-guard
pipeline: queue-depth, pending-op, cooldown, circuit-breaker
and (on success) enqueue at PRIORITY.background. These tests exercise
each guard.
"""

from unittest import mock

from shakenfist.exceptions import ProcessExecutionError
from shakenfist.tests import base
from shakenfist.util import network as real_util_network


def _info_messages(active):
    """The ``LOG.with_fields(...).info()`` messages one pass emitted, less
    the pass-cost line.

    Every pass reports what it cost (issue 4971), so a test which cares
    about one particular line has to name that line rather than assert
    the pass logged exactly once. Excluding the cost line here keeps
    each such test asserting the thing it is actually about.
    """
    return [
        call.args[0]
        for call in active['log'].with_fields.return_value.info.call_args_list
        if call.args and call.args[0] != 'Maintenance pass complete']


def _pass_cost_fields(active):
    """The fields the pass-cost log line carried, or None if it was absent.

    Found by looking for the ``pass_seconds`` key rather than by
    position, because the pass logs through ``with_fields`` in several
    other places and ``with_fields`` hands back the same mock each time.
    """
    for call in active['log'].with_fields.call_args_list:
        if (call.args and isinstance(call.args[0], dict)
                and 'pass_seconds' in call.args[0]):
            return call.args[0]
    return None


def _build_mock_network(uuid='net-uuid-001', vxid=42, state='created',
                        is_okay=False, is_mesh_okay=True, interfaces=None):
    n = mock.MagicMock()
    n.uuid = uuid
    n.vxid = vxid
    n.state.value = state
    n.state.update_time = 0.0
    n.is_okay.return_value = is_okay
    n.is_mesh_okay.return_value = is_mesh_okay
    n.networkinterfaces = interfaces if interfaces is not None else []
    return n


def _patch_maintain_module(network_node=True, queue_depth_per_queue=0,
                           pending_op=False, recent_history=None,
                           networks=None, floating_network=None):
    """Build the standard set of mock.patch context managers for one
    pass through Job.execute(). Returns the context manager objects so
    callers can use them with ``contextlib.ExitStack`` or by manually
    entering them.
    """
    patches = {
        'config': mock.patch('shakenfist.daemons.network.maintain.config'),
        'daemon': mock.patch('shakenfist.daemons.network.maintain.daemon'),
        'network': mock.patch('shakenfist.daemons.network.maintain.network'),
        'util_network': mock.patch(
            'shakenfist.daemons.network.maintain.util_network'),
        'util_concurrency': mock.patch(
            'shakenfist.daemons.network.maintain.util_concurrency'),
        'time': mock.patch('shakenfist.daemons.network.maintain.time'),
        'mariadb': mock.patch('shakenfist.daemons.network.maintain.mariadb'),
        'node': mock.patch('shakenfist.daemons.network.maintain.Node'),
        'get_node_network_queues': mock.patch(
            'shakenfist.daemons.network.maintain.get_node_network_queues'),
        'get_all_network_queues': mock.patch(
            'shakenfist.daemons.network.maintain.get_all_network_queues'),
        'net_create_and_enqueue': mock.patch(
            'shakenfist.daemons.network.maintain.net_create_and_enqueue'),
        'nn_create_and_enqueue': mock.patch(
            'shakenfist.daemons.network.maintain.nn_create_and_enqueue'),
        'net_ip_create_and_enqueue': mock.patch(
            'shakenfist.daemons.network.maintain.net_ip_create_and_enqueue'),
        'instance': mock.patch(
            'shakenfist.daemons.network.maintain.instance'),
        'util_exceptions': mock.patch(
            'shakenfist.daemons.network.maintain.util_exceptions'),
        'log': mock.patch('shakenfist.daemons.network.maintain.LOG'),
    }
    return patches


class MaintainPipelineTest(base.ShakenFistTestCase):
    """Exercise the five-guard pipeline in
    shakenfist.daemons.network.maintain.Job.execute().
    """

    def setUp(self):
        super().setUp()
        from shakenfist.daemons.network import maintain

        # These are module level, so a test which leaves an entry
        # behind would change the behaviour of every later test --
        # including tests in other classes in this file. Clear on the
        # way in and, via addCleanup, on the way out as well so a
        # failing test cannot leak state into its successors.
        def _clear():
            maintain.EXTRA_VLANS_HISTORY.clear()
            maintain.EXTRA_VLANS_WARNED.clear()
            maintain.STRAY_VXLAN_HANDLING_FAILING = False
            maintain.QUIESCE_REPORTED.clear()
            maintain.HEALTH_CHECK_LAST.clear()
            maintain.BUDGET_DEFERRAL_REPORTED = False

        _clear()
        self.addCleanup(_clear)

    def _run_one_iteration(self, *, network_node=True,
                           queue_depth_per_queue=0,
                           pending_op=False, recent_history=None,
                           networks=None, floating_network=None,
                           vxid_to_mac=None, db_network_vxids=None,
                           attached_vxids=None, attached_state='error',
                           node_missing=False, execute_side_effect=None,
                           present_devices=None, bridge_members=None,
                           bridge_members_error=None,
                           find_network_vxids_error=None,
                           namespace_routes=None,
                           root_routes=None,
                           namespace_routes_error=None,
                           clock_step=0.0,
                           health_check_budget=10.0,
                           busy_networks=None):
        """Drive Job.execute() through exactly one pass of the outer loop
        and return a dict of the mocks that callers will most likely
        want to assert on.

        ``attached_vxids`` models networks which an instance on this node
        is attached to but which the maintain pass does not maintain --
        an instance which has errored, or is still building. These are
        the networks the stray reaper must not touch on a hypervisor.
        ``attached_state`` is the state those instances are in, which
        decides whether they protect their network at all.

        ``node_missing`` makes ``Node.from_db()`` return None, and
        ``execute_side_effect`` is installed on
        ``util_concurrency.execute`` so tests can fail individual
        device deletes. ``present_devices``, when supplied, is the
        exact set of device names ``check_for_interface()`` reports as
        existing; the default is that every device asked about exists.

        ``bridge_members`` maps a bridge name to the devices enslaved
        to it, and ``bridge_members_error`` makes the membership read
        raise. Both drive the host side cross-check the reaper performs
        before it mutates anything; the default is a bridge with no
        members at all. The devices which are ours come from the real
        ``util_network.bridge_members_we_own()`` so that a test naming
        our own devices still exercises the production filter rather
        than a copy of it.

        ``namespace_routes`` is the set of host route destinations
        ``get_host_routes()`` reports from inside a network namespace,
        and ``root_routes`` the set it reports for the bridge in the
        root namespace. Between them they are how the pass decides
        whether a routed address still has both of its routes; the
        default is none in the namespace, and whatever the namespace has
        in the root, so a test which only cares about one side does not
        have to say anything about the other.
        ``namespace_routes_error`` makes the read raise instead, which
        is what a namespace that is not there looks like.
        """
        if networks is None:
            networks = []
        if recent_history is None:
            recent_history = []
        if vxid_to_mac is None:
            vxid_to_mac = {}
        if db_network_vxids is None:
            db_network_vxids = []

        patches = _patch_maintain_module()
        active = {name: p.start() for name, p in patches.items()}
        try:
            mc = active['config']
            mc.NODE_IS_NETWORK_NODE = network_node
            mc.NODE_UUID = 'node-uuid-test'
            mc.NODE_NAME = 'node-name-test'
            mc.MAINTAIN_QUEUE_DEPTH_THRESHOLD = 50
            mc.MAINTAIN_RECONCILE_COOLDOWN_SECONDS = 60
            mc.MAINTAIN_RECONCILE_CIRCUIT_K = 5
            mc.MAINTAIN_STRAY_VXLAN_GRACE_SECONDS = 300
            mc.MAINTAIN_HEALTH_CHECK_BUDGET_SECONDS = health_check_budget

            md = active['daemon']
            md.check_abort_path.side_effect = [True, False]
            md.clear_abort_path.return_value = None

            mt = active['time']
            if clock_step:
                # Each time.time() call advances the clock by clock_step,
                # so a pass can be given a measurable duration. Every
                # interval the pass reads is bracketed by exactly two
                # calls, which makes the durations it computes
                # predictable rather than merely non-zero.
                clock = {'now': 10_000.0}

                def _advancing_clock():
                    clock['now'] += clock_step
                    return clock['now']

                mt.time.side_effect = _advancing_clock
            else:
                mt.time.return_value = 10_000.0
            mt.sleep.return_value = None

            active['util_network'].discover_interfaces.return_value = (
                None, None, vxid_to_mac)
            if namespace_routes_error is not None:
                active['util_network'].get_host_routes.side_effect = (
                    namespace_routes_error)
            else:
                in_namespace = set(namespace_routes or [])
                in_root = (in_namespace if root_routes is None
                           else set(root_routes))
                active['util_network'].get_host_routes.side_effect = (
                    lambda netns, device:
                        in_root if netns is None else in_namespace)
            if present_devices is not None:
                active['util_network'].check_for_interface.side_effect = (
                    lambda device: device in present_devices)

            if bridge_members_error is not None:
                active['util_network'].foreign_bridge_members.side_effect = (
                    bridge_members_error)
            else:
                members = bridge_members or {}
                active['util_network'].foreign_bridge_members.side_effect = (
                    lambda vxid: sorted(
                        set(members.get('br-vxlan-%06x' % vxid, [])) -
                        real_util_network.bridge_members_we_own(vxid)))

            active['network'].Networks.return_value = networks
            # Network.from_db should return the matching mock by uuid
            uuid_to_net = {n.uuid: n for n in networks}
            active['network'].Network.from_db.side_effect = (
                lambda uuid, **kw: uuid_to_net.get(uuid))
            active['network'].floating_network.return_value = floating_network

            # ``local_vxids`` maps a vxid an instance on this node is
            # attached to, to the state of that instance. The stray
            # reaper reads this through a single indexed query which
            # applies the state filter in SQL, so the mock does the same
            # against the states it is handed.
            local_vxids = {}

            # For the non-network-node path, surface one instance whose
            # interfaces reference each test network.
            healthy_instances = []
            if not network_node and networks:
                fake_inst = mock.MagicMock()
                fake_inst.state.value = 'created'
                fake_inst.interfaces = []
                for net in networks:
                    ni = mock.MagicMock()
                    ni.network_uuid = net.uuid
                    fake_inst.interfaces.append(ni)
                    local_vxids[net.vxid] = 'created'
                healthy_instances.append(fake_inst)

            # Instances which exist on this node but are not healthy, so
            # they never reach host_networks and their networks look
            # stray to the extra-vxlan check.
            for vxid in attached_vxids or []:
                net_uuid = 'attached-net-%06x' % vxid
                attached_net = mock.MagicMock()
                attached_net.uuid = net_uuid
                attached_net.vxid = vxid
                uuid_to_net[net_uuid] = attached_net
                local_vxids[vxid] = attached_state

            def _instances(_filters, prefilter=None, **kwargs):
                return healthy_instances

            active['instance'].Instances.side_effect = _instances
            active['instance'].Instance.STATE_PREFLIGHT = 'preflight'

            active['get_node_network_queues'].return_value = [
                'q-node-a', 'q-node-b']
            active['get_all_network_queues'].return_value = [
                'q-cluster-a']

            mar = active['mariadb']
            mar.get_work_queue_length.return_value = (
                queue_depth_per_queue, 0, 0)
            mar.has_pending_cluster_operation_target.return_value = pending_op
            mar.get_recent_terminal_op_states_for_target.return_value = (
                recent_history)
            # Which networks have an interface attached, read once per
            # pass to order the health checks.
            mar.find_network_interfaces.return_value = [
                mock.MagicMock(network_uuid=uuid)
                for uuid in (busy_networks or [])]
            # find_network_vxids returns the claiming network's uuid as
            # well, because the reaper routes teardown of a network
            # which still exists through a cluster operation targeting
            # it rather than deleting devices itself.
            claimed = set(db_network_vxids)
            if find_network_vxids_error is not None:
                mar.find_network_vxids.side_effect = find_network_vxids_error
            else:
                mar.find_network_vxids.side_effect = (
                    lambda vxids: {v: 'claiming-net-%06x' % v
                                   for v in vxids if v in claimed})

            # The protecting-instance question is a single query which
            # filters on instance state in SQL, so the mock applies the
            # states the caller asked for rather than every state.
            mar.get_node_instance_vxids.side_effect = (
                lambda node_uuid, states: {
                    vxid for vxid, inst_state in local_vxids.items()
                    if inst_state in states})

            if execute_side_effect is not None:
                active['util_concurrency'].execute.side_effect = (
                    execute_side_effect)

            if node_missing:
                active['node'].from_db.return_value = None
            else:
                fake_node = mock.MagicMock()
                fake_node.uuid = 'node-uuid-test'
                active['node'].from_db.return_value = fake_node

            from shakenfist.daemons.network.maintain import Job
            job = Job.__new__(Job)
            job.name = 'test-maintain'
            job.abort_path = '/run/sf/net-test-maintain.abort'
            job.execute()

            return active
        finally:
            for p in patches.values():
                p.stop()

    def test_pass_cost_is_logged_every_pass(self):
        """Every pass reports what it cost: how many networks it
        considered, how long it took, and how much of that was health
        checks. Nothing measured any of this, so there was no way to
        say whether a pass still fits inside its own cadence (issue
        4971).
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1,
                                    is_okay=True, is_mesh_okay=True),
                _build_mock_network(uuid='net-b', vxid=2,
                                    is_okay=True, is_mesh_okay=True)]
        active = self._run_one_iteration(network_node=True, networks=nets)

        fields = _pass_cost_fields(active)
        self.assertIsNotNone(fields)
        self.assertEqual(2, fields['networks'])
        self.assertIn('health_check_seconds', fields)
        self.assertIn('Maintenance pass complete',
                      [c.args[0] for c
                       in active['log'].with_fields.return_value
                       .info.call_args_list if c.args])

    def test_health_check_time_is_attributed_to_the_pass(self):
        """``health_check_seconds`` is wall time spent inside the
        per-network health checks, not a constant. Each network's checks
        are bracketed by exactly two clock reads, so with a clock which
        advances a known amount per read the figure is the number of
        networks times that amount -- which a constant, or a bracket
        around the wrong call, cannot produce.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1,
                                    is_okay=True, is_mesh_okay=True),
                _build_mock_network(uuid='net-b', vxid=2,
                                    is_okay=True, is_mesh_okay=True),
                _build_mock_network(uuid='net-c', vxid=3,
                                    is_okay=True, is_mesh_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets, clock_step=0.25)

        fields = _pass_cost_fields(active)
        self.assertIsNotNone(fields)
        self.assertEqual(3, fields['networks'])
        self.assertEqual(0.75, fields['health_check_seconds'])

    def test_a_pass_which_outgrows_its_interval_is_reported_on_the_node(self):
        """A pass slower than its own interval delays every repair the
        next pass would have made, and the loop's sleep cannot claw the
        time back. That is the condition an operator can act on, so it
        is an event on the node -- which no faster pass writes, because
        one row every thirty seconds for the life of a healthy node says
        nothing the first did not.
        """
        from shakenfist.daemons.network import maintain

        nets = [_build_mock_network(uuid='net-a', vxid=1,
                                    is_okay=True, is_mesh_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=maintain.PASS_INTERVAL_SECONDS)

        node = active['node'].from_db.return_value
        node.add_event.assert_called_once()
        message = node.add_event.call_args.args[1]
        self.assertIn('longer than its %d second interval'
                      % maintain.PASS_INTERVAL_SECONDS, message)
        self.assertEqual(
            1, node.add_event.call_args.kwargs['extra']['networks'])

    def test_a_pass_inside_its_interval_writes_no_node_event(self):
        """The companion to the test above: the overrun event must be the
        exception rather than the steady state, and a pass which does not
        overrun must not read the node row at all.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1,
                                    is_okay=True, is_mesh_okay=True)]
        active = self._run_one_iteration(network_node=True, networks=nets)

        active['node'].from_db.assert_not_called()

    def test_health_check_budget_defers_the_rest_of_the_pass(self):
        """Once a pass has spent its health check budget it stops
        checking networks. Each network's checks cost 6 seconds here
        against a 10 second budget, so the third network is the first
        one not reached.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True),
                _build_mock_network(uuid='net-c', vxid=3, is_okay=True),
                _build_mock_network(uuid='net-d', vxid=4, is_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=6.0, health_check_budget=10.0)

        self.assertTrue(nets[0].is_okay.called)
        self.assertTrue(nets[1].is_okay.called)
        self.assertFalse(nets[2].is_okay.called)
        self.assertFalse(nets[3].is_okay.called)

        fields = _pass_cost_fields(active)
        self.assertEqual(2, fields['networks'])
        self.assertEqual(2, fields['networks_deferred'])

    def test_a_zero_budget_checks_every_network(self):
        """Zero disables the bound, which is how an operator gets the
        old unbounded behaviour back.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True),
                _build_mock_network(uuid='net-c', vxid=3, is_okay=True),
                _build_mock_network(uuid='net-d', vxid=4, is_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=6.0, health_check_budget=0.0)

        for n in nets:
            self.assertTrue(n.is_okay.called, n.uuid)
        self.assertEqual(0, _pass_cost_fields(active)['networks_deferred'])

    def test_deferred_networks_are_checked_first_next_pass(self):
        """The bound defers work rather than starving it. A network the
        budget stopped this pass has waited longest by definition, so
        the next pass starts with it -- without which the same networks
        at the head of the list would be checked every pass and the tail
        never.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True),
                _build_mock_network(uuid='net-c', vxid=3, is_okay=True)]
        self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=6.0, health_check_budget=10.0)

        # net-c was the one deferred, so it is the next pass's first
        # business.
        for n in nets:
            n.is_okay.reset_mock()
        active = self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=6.0, health_check_budget=10.0)

        checked = [n.uuid for n in nets if n.is_okay.called]
        self.assertIn('net-c', checked)
        first_loaded = [
            call.args[0]
            for call in active['network'].Network.from_db.call_args_list]
        self.assertEqual('net-c', first_loaded[0])

    def test_a_deferred_network_is_not_reaped_as_a_stray(self):
        """A network the budget deferred must still count as a network
        this node is on. The stray reaper works from the vxids the pass
        saw, so a deferred network left out of that set would have its
        devices deleted for no reason other than the pass running out of
        time to check it.
        """
        from shakenfist.daemons.network import maintain

        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True),
                _build_mock_network(uuid='net-c', vxid=3, is_okay=True)]
        self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=6.0, health_check_budget=10.0,
            vxid_to_mac={1: '02:00:00:00:00:01',
                         2: '02:00:00:00:00:02',
                         3: '02:00:00:00:00:03'})

        # vxid 3 belongs to the deferred net-c. A stray would have been
        # recorded in the history cache on its way to being reaped.
        self.assertEqual({}, maintain.EXTRA_VLANS_HISTORY)

    def test_spending_the_budget_is_reported_on_the_node(self):
        """Deferring networks means some were not checked at all, which
        an operator should be able to see without reading the logs.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True),
                _build_mock_network(uuid='net-c', vxid=3, is_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=6.0, health_check_budget=10.0)

        node = active['node'].from_db.return_value
        messages = [c.args[1] for c in node.add_event.call_args_list]
        self.assertTrue(
            any('deferred 1 networks to a later pass' in m
                for m in messages), messages)

    def _deferral_messages(self, active):
        node = active['node'].from_db.return_value
        return [c.args[1] for c in node.add_event.call_args_list
                if 'health check budget' in c.args[1]]

    def test_a_run_of_deferring_passes_is_reported_once_each_way(self):
        """The budget is only reached while the node is degraded, which
        lasts many passes. Reporting every one would be an event row
        every thirty seconds for the length of the incident, at the
        moment the database is least able to take it, so the run is
        reported on the way in and on the way out and the passes between
        are left to the log line.
        """
        def nets():
            return [
                _build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True),
                _build_mock_network(uuid='net-c', vxid=3, is_okay=True)]

        first = self._run_one_iteration(
            network_node=True, networks=nets(),
            clock_step=6.0, health_check_budget=10.0)
        messages = self._deferral_messages(first)
        self.assertEqual(1, len(messages), messages)
        self.assertIn('deferred 1 networks', messages[0])

        second = self._run_one_iteration(
            network_node=True, networks=nets(),
            clock_step=6.0, health_check_budget=10.0)
        self.assertEqual([], self._deferral_messages(second))
        # Still deferring, and the log line still says so.
        self.assertEqual(1, _pass_cost_fields(second)['networks_deferred'])

        recovered = self._run_one_iteration(
            network_node=True, networks=nets())
        messages = self._deferral_messages(recovered)
        self.assertEqual(1, len(messages), messages)
        self.assertIn('reached every network', messages[0])

        # And a pass after recovery is quiet again.
        quiet = self._run_one_iteration(network_node=True, networks=nets())
        quiet['node'].from_db.assert_not_called()

    def test_a_degraded_pass_reads_the_node_once(self):
        """A pass which both starts deferring and overruns writes both
        events from one node read, since that is the case the feature
        exists for.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True),
                _build_mock_network(uuid='net-c', vxid=3, is_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=10.0, health_check_budget=10.0)

        node = active['node'].from_db.return_value
        messages = [c.args[1] for c in node.add_event.call_args_list]
        self.assertEqual(2, len(messages), messages)
        self.assertTrue(any('health check budget' in m for m in messages))
        self.assertTrue(any('longer than its' in m for m in messages))
        active['node'].from_db.assert_called_once()

    def test_a_disabled_budget_skips_the_interface_read(self):
        """Which networks have guests only orders the health checks, and
        with the bound disabled every network is checked whatever the
        order. An operator who sets the budget to zero is asking for the
        old behaviour, and should get the old cost with it.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets, health_check_budget=0.0,
            busy_networks=['net-a'])

        active['mariadb'].find_network_interfaces.assert_not_called()
        self.assertNotIn('networks_with_guests', _pass_cost_fields(active))

    def test_the_pass_reports_how_many_of_its_networks_have_guests(self):
        """The busy read degrades to an empty set on a database error,
        and with it the order to longest-waiting. The count on the pass
        cost line is what makes that visible. It counts this node's
        networks only: an interface on a network the node is not on
        orders nothing here.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets,
            busy_networks=['net-a', 'net-elsewhere'])

        self.assertEqual(
            1, _pass_cost_fields(active)['networks_with_guests'])

    def test_a_failed_interface_read_reports_no_networks_with_guests(self):
        """find_network_interfaces() logs and returns nothing on a
        database error, which is indistinguishable here from a cluster
        with no guests. The zero is the signal, so it must be reported
        rather than left out.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets, busy_networks=[])

        self.assertEqual(
            0, _pass_cost_fields(active)['networks_with_guests'])

    def test_pass_seconds_includes_the_stray_vxlan_handling(self):
        """``pass_seconds`` is the whole pass, through to the stray vxlan
        handling at its end. That handling is an RPC, instance reads,
        execs and enqueues, and a pass which overran only there would
        otherwise report itself inside its cadence. With a clock which
        advances on every read, a pass which also reaps a stray must
        report longer than the same pass without one.
        """
        from shakenfist.daemons.network import maintain

        quiet = self._run_one_iteration(
            network_node=True, clock_step=1.0,
            vxid_to_mac={}, db_network_vxids=[])
        quiet_seconds = _pass_cost_fields(quiet)['pass_seconds']

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        reaping = self._run_one_iteration(
            network_node=True, clock_step=1.0,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[])
        self.assertTrue(reaping['util_concurrency'].execute.called)
        self.assertGreater(
            _pass_cost_fields(reaping)['pass_seconds'], quiet_seconds)

    def test_busy_networks_are_health_checked_before_empty_ones(self):
        """When the budget is scarce it is spent on the networks which
        have guests on them. An empty network has nothing which can fail
        to get a DHCP lease, so it yields to a busy one even when the
        busy one was checked more recently -- which is what makes many
        unused networks close to free for the network node.
        """
        nets = [_build_mock_network(uuid='net-empty', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-busy', vxid=2, is_okay=True)]

        # A budget which affords exactly one check, and an empty network
        # whose uuid would otherwise sort first.
        active = self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=6.0, health_check_budget=5.0,
            busy_networks=['net-busy'])

        self.assertTrue(nets[1].is_okay.called)
        self.assertFalse(nets[0].is_okay.called)
        self.assertEqual(
            'net-busy',
            active['network'].Network.from_db.call_args_list[0].args[0])

    def test_a_busy_network_beats_an_empty_one_checked_longer_ago(self):
        """Having an interface outranks having waited longer. Without
        that the rotation would hand the scarce budget back to empty
        networks as soon as they were the oldest, which is the state
        they are in most of the time.
        """
        from shakenfist.daemons.network import maintain

        nets = [_build_mock_network(uuid='net-empty', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-busy', vxid=2, is_okay=True)]

        # The empty network has waited far longer than the busy one.
        maintain.HEALTH_CHECK_LAST['net-empty'] = 1.0
        maintain.HEALTH_CHECK_LAST['net-busy'] = 9_000.0

        self._run_one_iteration(
            network_node=True, networks=nets,
            clock_step=6.0, health_check_budget=5.0,
            busy_networks=['net-busy'])

        self.assertTrue(nets[1].is_okay.called)
        self.assertFalse(nets[0].is_okay.called)

    def test_the_interface_read_is_one_query_per_pass(self):
        """The busy set is one read of the table, not one per network.
        The per-network form would be a networks-per-thirty-seconds
        database sweep, which is the shape this pass has had to have
        removed from it before.
        """
        nets = [_build_mock_network(uuid='net-a', vxid=1, is_okay=True),
                _build_mock_network(uuid='net-b', vxid=2, is_okay=True),
                _build_mock_network(uuid='net-c', vxid=3, is_okay=True)]
        active = self._run_one_iteration(
            network_node=True, networks=nets, busy_networks=['net-a'])

        self.assertEqual(
            1, active['mariadb'].find_network_interfaces.call_count)

    def test_queue_depth_guard_skips_pass(self):
        """When summed queue depth exceeds the threshold the per-network
        loop is skipped entirely and an audit event fires on the Node.
        """
        n = _build_mock_network()
        # Threshold is 50; three queues each reporting 25 sums to 75.
        active = self._run_one_iteration(
            network_node=True,
            queue_depth_per_queue=25,
            networks=[n],
        )

        # No enqueues happened.
        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()
        active['net_ip_create_and_enqueue'].assert_not_called()
        # Network.from_db never reached -- the pass was skipped before
        # discovery.
        active['network'].Network.from_db.assert_not_called()
        # Audit event recorded on the Node.
        node = active['node'].from_db.return_value
        node.add_event.assert_called_once()

    def test_pending_op_gate_skips_network(self):
        """If a cluster operation is in flight for a network we do not
        enqueue another reconciliation."""
        n = _build_mock_network()
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            pending_op=True,
        )

        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_cooldown_gate_skips_recent_error(self):
        """If the most recent terminal op ended in ERROR within the
        cooldown window we skip the network and emit an audit event."""
        n = _build_mock_network()
        # update_time well within the 60-second cooldown window of
        # the patched time.time() == 10_000.0
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            recent_history=[('op-uuid-1', 'error', 9_995.0)],
        )

        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()
        n.add_event.assert_called()

    def test_circuit_breaker_fires_after_k_failures(self):
        """K consecutive ERROR terminal ops quiesce the network with a
        prominent audit event."""
        n = _build_mock_network()

        # First helper call (cooldown, limit=1) sees an old error -- no
        # cooldown skip -- but the circuit-breaker call (limit=K) sees K
        # back-to-back errors.
        history_old = [('op-old', 'error', 0.0)]
        history_circuit = [
            ('op-1', 'error', 0.0),
            ('op-2', 'error', 0.0),
            ('op-3', 'error', 0.0),
            ('op-4', 'error', 0.0),
            ('op-5', 'error', 0.0),
        ]

        def helper_side_effect(*, target_object_type, target_uuid, limit,
                               op_type=None):
            if limit == 1:
                return history_old
            return history_circuit

        patches = _patch_maintain_module()
        active = {name: p.start() for name, p in patches.items()}
        try:
            mc = active['config']
            mc.NODE_IS_NETWORK_NODE = True
            mc.NODE_UUID = 'node-uuid-test'
            mc.NODE_NAME = 'node-name-test'
            mc.MAINTAIN_QUEUE_DEPTH_THRESHOLD = 50
            mc.MAINTAIN_RECONCILE_COOLDOWN_SECONDS = 60
            mc.MAINTAIN_RECONCILE_CIRCUIT_K = 5
            mc.MAINTAIN_STRAY_VXLAN_GRACE_SECONDS = 300
            mc.MAINTAIN_HEALTH_CHECK_BUDGET_SECONDS = 10.0

            active['daemon'].check_abort_path.side_effect = [True, False]
            active['daemon'].clear_abort_path.return_value = None
            active['time'].time.return_value = 10_000.0
            active['time'].sleep.return_value = None
            active['util_network'].discover_interfaces.return_value = (
                None, None, {})
            active['network'].Networks.return_value = [n]
            active['network'].Network.from_db.return_value = n
            active['network'].floating_network.return_value = None
            active['get_node_network_queues'].return_value = ['q-a']
            active['get_all_network_queues'].return_value = []
            active['mariadb'].get_work_queue_length.return_value = (0, 0, 0)
            active['mariadb'].has_pending_cluster_operation_target.\
                return_value = False
            active['mariadb'].get_recent_terminal_op_states_for_target.\
                side_effect = helper_side_effect

            from shakenfist.daemons.network.maintain import Job
            job = Job.__new__(Job)
            job.name = 'test-maintain'
            job.abort_path = '/run/sf/net-test-maintain.abort'
            job.execute()

            active['net_create_and_enqueue'].assert_not_called()
            active['nn_create_and_enqueue'].assert_not_called()
            # The most recent add_event call should be the
            # circuit-breaker message.
            event_messages = [
                call.args[1] for call in n.add_event.call_args_list]
            self.assertTrue(
                any('quiesced pending operator attention' in m
                    for m in event_messages),
                f'expected circuit-breaker event, got: {event_messages}')
        finally:
            for p in patches.values():
                p.stop()

    def test_happy_path_enqueues_at_background_priority(self):
        """A drifting network with no in-flight op and no recent errors
        is reconciled by enqueueing the appropriate task at background
        priority."""
        n = _build_mock_network()
        active = self._run_one_iteration(
            network_node=False,
            networks=[n],
        )

        # Hypervisor-side reconciliation goes through
        # nn_create_and_enqueue with network_apply_create_hypervisor.
        active['nn_create_and_enqueue'].assert_called_once()
        call = active['nn_create_and_enqueue'].call_args
        # Positional arguments: (node_uuid, network_uuid, tasks, priority)
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY
        from shakenfist.schema.operations.node_net_op \
            import model_tasks as nn_tasks
        self.assertEqual(call.args[2], [nn_tasks.network_apply_create_hypervisor])
        self.assertEqual(call.args[3], PRIORITY.background)

        # The mesh ensure-call should also fire at background priority.
        mesh_calls = [
            c for c in active['net_create_and_enqueue'].call_args_list
            if c.kwargs.get('priority') == PRIORITY.background]
        self.assertTrue(mesh_calls)

    def test_delete_wait_no_interfaces_enqueues_delete_network_node(self):
        """A delete_wait network with no interfaces triggers
        network_apply_delete_network_node and then skips maintenance."""
        from shakenfist.schema.operations.net_op \
            import model_tasks as net_tasks

        # Use the canonical 'delete-wait' value from dbo.STATE_DELETE_WAIT.
        n = _build_mock_network(state='delete-wait', interfaces=[])
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
        )

        # Must enqueue a delete op.
        active['net_create_and_enqueue'].assert_called_once()
        call = active['net_create_and_enqueue'].call_args
        self.assertIn(
            net_tasks.network_apply_delete_network_node,
            call.kwargs.get('tasks', call.args[1]
                            if len(call.args) > 1 else []))
        # No hypervisor or mesh ops enqueued.
        active['nn_create_and_enqueue'].assert_not_called()

    def test_delete_wait_with_interfaces_skips_maintenance_only(self):
        """A delete_wait network that still has interfaces is skipped
        without enqueueing the delete op."""
        ni = mock.MagicMock()
        n = _build_mock_network(
            state='delete-wait', interfaces=[ni])
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
        )

        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_delete_wait_no_interfaces_with_pending_op_skips_enqueue(self):
        """A delete_wait network with no interfaces but an in-flight op
        does NOT get a second delete op enqueued. The REST DELETE
        handler enqueues the op when transitioning to DELETE_WAIT and
        the maintainer must not race a duplicate against it.
        """
        n = _build_mock_network(state='delete-wait', interfaces=[])
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            pending_op=True,
        )

        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_recent_state_change_skips_network(self):
        """Networks whose state changed within the last 60 seconds are
        skipped (the 60-second punt guard)."""
        n = _build_mock_network(is_okay=False)
        # state.update_time must be within 60 s of time.time() == 10_000.
        n.state.update_time = 9_990.0

        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
        )

        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_is_okay_skips_reconciliation(self):
        """When is_okay() and is_mesh_okay() both return True no
        reconciliation is enqueued."""
        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
        )

        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_mesh_drift_enqueues_targeted_ensure_mesh(self):
        """When the network itself is okay but the vxlan mesh has
        drifted, the maintainer enqueues only the targeted ensure_mesh
        repair on this node -- not a full recreate."""
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY
        from shakenfist.schema.operations.net_op \
            import model_tasks as net_tasks

        n = _build_mock_network(is_okay=True, is_mesh_okay=False)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
        )

        # No full recreate on either node type.
        active['nn_create_and_enqueue'].assert_not_called()

        active['net_create_and_enqueue'].assert_called_once()
        kwargs = active['net_create_and_enqueue'].call_args.kwargs
        self.assertEqual([net_tasks.network_ensure_mesh], kwargs['tasks'])
        self.assertEqual(PRIORITY.background, kwargs['priority'])
        self.assertEqual('node-uuid-test', kwargs['target'])
        self.assertEqual('network', kwargs['family'])

    def test_mesh_drift_respects_pending_op_gate(self):
        """A mesh-drift repair must not race an in-flight cluster
        operation for the same network."""
        n = _build_mock_network(is_okay=True, is_mesh_okay=False)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            pending_op=True,
        )

        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_mesh_audit_skipped_when_network_not_okay(self):
        """When is_okay() is already False the full reconcile owns the
        repair (it ends with an ensure_mesh) -- the separate mesh audit
        is not consulted."""
        n = _build_mock_network(is_okay=False)
        self._run_one_iteration(
            network_node=True,
            networks=[n],
        )

        n.is_mesh_okay.assert_not_called()

    def test_hypervisor_node_enqueues_network_apply_create_hypervisor(self):
        """On a non-network-node (hypervisor) a drifting network must be
        reconciled via nn_create_and_enqueue with the
        network_apply_create_hypervisor task at background priority."""
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY
        from shakenfist.schema.operations.node_net_op \
            import model_tasks as nn_tasks

        n = _build_mock_network(is_okay=False)
        active = self._run_one_iteration(
            network_node=False,
            networks=[n],
        )

        active['nn_create_and_enqueue'].assert_called_once()
        call = active['nn_create_and_enqueue'].call_args
        self.assertEqual(
            [nn_tasks.network_apply_create_hypervisor], call.args[2])
        self.assertEqual(PRIORITY.background, call.args[3])

    def test_network_node_re_enqueues_floating_ips(self):
        """When a network node finds a drifting network it also re-enqueues
        floating-IP restoration for each interface that has one."""
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY
        from shakenfist.schema.operations.net_op \
            import model_tasks as net_tasks

        ni = mock.MagicMock()
        ni.floating = {'floating_address': '10.0.0.5'}
        ni.ipv4 = '192.168.1.10'
        n = _build_mock_network(is_okay=False, interfaces=[ni])

        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
        )

        calls = active['net_create_and_enqueue'].call_args_list
        floating_calls = [
            c for c in calls
            if net_tasks.network_add_floating_ip in
            c.kwargs.get('tasks', c.args[1] if len(c.args) > 1 else [])
        ]
        self.assertTrue(
            floating_calls,
            'Expected network_add_floating_ip enqueue, '
            f'got calls: {calls}')
        # The floating-IP enqueue must carry the correct addresses.
        fc = floating_calls[0]
        self.assertEqual('10.0.0.5', fc.kwargs.get('floating_address'))
        self.assertEqual('192.168.1.10', fc.kwargs.get('inner_address'))
        self.assertEqual(PRIORITY.background, fc.kwargs.get('priority'))

    def test_network_node_re_enqueues_routed_ips(self):
        """When a network node finds a drifting network it also re-enqueues
        routed-IP restoration for all routed addresses on that network."""
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY
        from shakenfist.schema.operations.net_ip_op \
            import model_tasks as net_ip_tasks

        n = _build_mock_network(is_okay=False, interfaces=[])

        # Build a fake floating network whose IPAM reports one routed
        # reservation for our test network.
        fake_addr = '10.10.10.1'
        fake_resv = mock.MagicMock()
        from shakenfist.schema.ipam_reservation import ReservationType
        fake_resv.reservation_type = ReservationType.ROUTED
        fake_resv.user_uuid = n.uuid

        fake_fn = mock.MagicMock()
        fake_fn.ipam.in_use = [fake_addr]
        # The sweep reads the whole reservation table once rather than
        # one address at a time (issue 3655).
        fake_fn.ipam.get_all_reservations.return_value = {
            fake_addr: fake_resv}

        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=fake_fn,
        )

        calls = active['net_ip_create_and_enqueue'].call_args_list
        routed_calls = [
            c for c in calls
            if net_ip_tasks.route_address in
            c.kwargs.get('tasks', c.args[2] if len(c.args) > 2 else [])
        ]
        self.assertTrue(
            routed_calls,
            f'Expected route_address enqueue, got calls: {calls}')
        rc = routed_calls[0]
        self.assertEqual(fake_addr, rc.kwargs.get('ip'))
        self.assertEqual(PRIORITY.background, rc.kwargs.get('priority'))

    def _routed_floating_network(self, network, address):
        """A floating network whose IPAM has one routed address."""
        from shakenfist.schema.ipam_reservation import ReservationType

        reservation = mock.MagicMock()
        reservation.reservation_type = ReservationType.ROUTED
        reservation.user_uuid = network.uuid

        fn = mock.MagicMock()
        fn.ipam.in_use = [address]
        fn.ipam.get_all_reservations.return_value = {address: reservation}
        return fn

    def test_a_missing_namespace_route_is_restored_on_its_own(self):
        """A routed address with no in-namespace route is re-routed.

        A routed address is two routes: one in the network node's root
        namespace so the world can reach it, and one in the network's own
        namespace so the network's instances can (issue 3662). Nothing
        else re-applies the second one while the network is healthy, so a
        cluster which upgraded into it while everything was well would
        never acquire it for the addresses it already had.

        Re-routing is idempotent and installs both routes, so this must
        not drag the whole network through a rebuild to get there.
        """
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY
        from shakenfist.schema.operations.net_ip_op \
            import model_tasks as net_ip_tasks

        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes=[],
        )

        active['net_ip_create_and_enqueue'].assert_called_once()
        call = active['net_ip_create_and_enqueue'].call_args
        self.assertEqual('10.10.10.1', call.kwargs.get('ip'))
        self.assertEqual(
            [net_ip_tasks.route_address], call.kwargs.get('tasks'))
        self.assertEqual(PRIORITY.background, call.kwargs.get('priority'))

        # Nothing was rebuilt to achieve it.
        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_a_present_namespace_route_is_left_alone(self):
        """The pass is a no-op when both of the routes are already there."""
        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes=['10.10.10.1'],
        )

        active['net_ip_create_and_enqueue'].assert_not_called()
        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_a_clean_network_ends_its_quiesce_episode(self):
        """A network which is clean on a pass is reported afresh next time.

        Without this a network which quiesced once would never tell its
        instances about a later quiesce (issue 520).
        """
        from shakenfist.daemons.network import maintain

        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        maintain.QUIESCE_REPORTED.add(str(n.uuid))
        self._run_one_iteration(network_node=True, networks=[n])

        self.assertNotIn(str(n.uuid), maintain.QUIESCE_REPORTED)

    def test_a_clean_routed_network_ends_its_quiesce_episode_too(self):
        """Routed addresses take the pass down a second clean exit.

        A network with routed addresses has its routes read before the
        pass can call it clean, and that exit once left the episode
        open, so a healthy routed network which quiesced once was never
        reported again.
        """
        from shakenfist.daemons.network import maintain

        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        maintain.QUIESCE_REPORTED.add(str(n.uuid))
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes=['10.10.10.1'],
        )

        # This is the clean exit, not a route repair.
        active['net_ip_create_and_enqueue'].assert_not_called()
        self.assertNotIn(str(n.uuid), maintain.QUIESCE_REPORTED)

    def test_a_missing_root_route_is_restored_too(self):
        """The root namespace route is audited alongside the namespace one.

        The root route is how the world reaches a routed address, and it
        can be lost while the bridge it points at survives -- in which
        case ``is_created()`` stays true, the network looks perfectly
        healthy, and the address is silently unreachable from outside
        the cluster. That is the same drift this pass reads the
        namespace for, in the other direction, and re-routing installs
        both so the repair does not care which one went missing.
        """
        from shakenfist.schema.operations.net_ip_op \
            import model_tasks as net_ip_tasks

        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes=['10.10.10.1'],
            root_routes=[],
        )

        active['net_ip_create_and_enqueue'].assert_called_once()
        call = active['net_ip_create_and_enqueue'].call_args
        self.assertEqual('10.10.10.1', call.kwargs.get('ip'))
        self.assertEqual(
            [net_ip_tasks.route_address], call.kwargs.get('tasks'))

        # Both tables really were read, rather than the namespace read
        # answering for both.
        self.assertEqual(
            {None, str(n.uuid)},
            {c.args[0] for c in
             active['util_network'].get_host_routes.call_args_list})

        # And nothing was rebuilt to get there.
        active['net_create_and_enqueue'].assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()

    def test_a_drifted_mesh_defers_the_route_probe(self):
        """A network heading for a mesh repair is not probed for routes.

        The mesh repair branch discards whatever the probe would have
        said -- any missing route waits for the pass after the mesh
        comes back -- so paying an exec per pass for the answer is the
        same waste the pending-operation gate already avoids.
        """
        n = _build_mock_network(is_okay=True, is_mesh_okay=False)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes=[],
        )

        active['util_network'].get_host_routes.assert_not_called()
        active['net_ip_create_and_enqueue'].assert_not_called()
        # The mesh repair itself still happens.
        active['net_create_and_enqueue'].assert_called_once()

    def test_only_the_network_node_reads_namespace_routes(self):
        """A hypervisor has no namespace to read, and must not look.

        The namespaces exist only on the network node, so asking on a
        hypervisor would be one wasted exec per network per pass, and
        would report every routed address as missing.
        """
        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        active = self._run_one_iteration(
            network_node=False,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes=[],
        )

        active['util_network'].get_host_routes.assert_not_called()
        active['net_ip_create_and_enqueue'].assert_not_called()

    def test_a_missing_namespace_is_left_to_the_rebuild(self):
        """A namespace which cannot be read is not a missing route.

        ``ip route list`` inside a namespace which is not there fails,
        and that is a larger drift than one absent route -- rebuilding
        the network is what fixes it, and re-applies the routes on the
        way past. Reporting every address as missing here would enqueue a
        re-route per address per pass against a namespace which cannot
        accept any of them.
        """
        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes_error=ProcessExecutionError(
                'Cannot open network namespace'),
        )

        active['util_network'].get_host_routes.assert_called_once()
        active['net_ip_create_and_enqueue'].assert_not_called()

    def test_namespace_routes_are_not_read_while_an_op_is_pending(self):
        """A network already being reconciled is not probed.

        The in-flight operation will re-route the address when it runs,
        and the pending-op gate discards whatever the probe would have
        said, so paying an exec per pass for it is pure waste.
        """
        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes=[],
            pending_op=True,
        )

        active['util_network'].get_host_routes.assert_not_called()
        active['net_ip_create_and_enqueue'].assert_not_called()

    def test_a_failing_route_repair_is_guarded_by_its_own_history(self):
        """The route repair is a NetIPOp, so its guards read NetIPOp state.

        A re-route which can never succeed -- an absent veth, a namespace
        in a state nothing here can fix -- would otherwise be enqueued
        again on every pass forever, with a fresh event on the network
        each time, because the network's NetOp history says nothing about
        whether its NetIPOps have been failing.
        """
        n = _build_mock_network(is_okay=True, is_mesh_okay=True)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            floating_network=self._routed_floating_network(n, '10.10.10.1'),
            namespace_routes=[],
            recent_history=[('op-uuid-1', 'error', 9_995.0)],
        )

        active['net_ip_create_and_enqueue'].assert_not_called()
        active['net_create_and_enqueue'].assert_not_called()

        op_types = {
            call.kwargs.get('op_type') for call in
            active['mariadb'].get_recent_terminal_op_states_for_target
            .call_args_list}
        self.assertEqual({'net_ip_op'}, op_types)

    def test_a_rebuild_is_still_guarded_by_net_op_history(self):
        """Structural drift is repaired with a NetOp, and guarded as one."""
        n = _build_mock_network(is_okay=False)
        active = self._run_one_iteration(
            network_node=True,
            networks=[n],
            recent_history=[('op-uuid-1', 'error', 9_995.0)],
        )

        active['net_create_and_enqueue'].assert_not_called()

        op_types = {
            call.kwargs.get('op_type') for call in
            active['mariadb'].get_recent_terminal_op_states_for_target
            .call_args_list}
        self.assertEqual({'net_op'}, op_types)

    def test_stray_vxlan_within_grace_period_not_touched(self):
        """A stray vxlan seen for less than five minutes is only tracked,
        never deleted or warned about."""
        from shakenfist.daemons.network import maintain

        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
        )

        # First sighting is recorded at the (mocked) current time.
        self.assertEqual(10_000.0, maintain.EXTRA_VLANS_HISTORY[0x123])
        # No devices deleted, and the database was not consulted.
        active['util_concurrency'].execute.assert_not_called()
        active['mariadb'].find_network_vxids.assert_not_called()
        active['log'].with_fields.return_value.warning.assert_not_called()

    def test_stray_vxlan_reaped_after_grace_period(self):
        """A vxlan stray for more than five minutes whose vxid no network
        in the database claims is reaped, with an audit event on the
        node."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
        )

        # Every device Shaken Fist names from the vxid is removed, not
        # just the bridge and the vxlan interface. The vxlan interface
        # goes last, because it is the only device rediscovery keys on.
        self.assertEqual(
            [mock.call('ip link delete br-vxlan-000123'),
             mock.call('ip link delete veth-000123-o'),
             mock.call('ip link delete egr-000123-o'),
             mock.call('ip link delete vxlan-000123')],
            active['util_concurrency'].execute.call_args_list)
        node = active['node'].from_db.return_value
        node.add_event.assert_called_once()
        self.assertIn(
            'reaped stray vxlan', node.add_event.call_args.args[1])
        # The audit event names what was actually removed.
        self.assertEqual(
            ['br-vxlan-000123', 'veth-000123-o', 'egr-000123-o',
             'vxlan-000123'],
            node.add_event.call_args.kwargs['extra']['devices'])
        # The stray is forgotten so a reappearance gets a fresh grace
        # period.
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)
        # The node is only looked up once, not once per reaped vxid.
        active['node'].from_db.assert_called_once()

    def test_stray_vxlan_claimed_by_network_noted_once_not_reaped(self):
        """A stray vxlan whose vxid is still claimed by a network row in
        the database must not be deleted. The network node carries a
        device for every active network by design, so this is the
        expected steady state -- it is noted at INFO once per stray
        episode, never warned about, and the message must not contain
        the CI forbidden string 'Extra vxlan present' (issue 3837)."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
        )

        active['util_concurrency'].execute.assert_not_called()
        active['log'].with_fields.return_value.warning.assert_not_called()
        messages = _info_messages(active)
        self.assertEqual(1, len(messages), messages)
        self.assertNotIn('Extra vxlan present', messages[0])
        self.assertIn(0x123, maintain.EXTRA_VLANS_WARNED)
        # Note-only is the steady state for a stray we never touch, and
        # it has nothing to record, so it must not read the node row on
        # every pass.
        active['node'].from_db.assert_not_called()

        # A second pass over the same stray must not report again.
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
        )
        active['util_concurrency'].execute.assert_not_called()
        active['log'].with_fields.return_value.warning.assert_not_called()
        self.assertEqual([], _info_messages(active))

    def test_stray_vxlan_disappearance_resets_tracking(self):
        """When a stray vxlan disappears from the host its history and
        warned state are dropped, so a reappearance is a new episode."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        maintain.EXTRA_VLANS_WARNED[0x123] = {'stray: any old reason'}
        self._run_one_iteration(
            network_node=True,
            vxid_to_mac={},
        )

        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_WARNED)

    def test_reaping_a_warned_stray_clears_the_warned_entry(self):
        """A stray which was warned about while its network existed, and
        is then reaped once the row goes away, must not leave an entry
        behind in EXTRA_VLANS_WARNED. vxids are randomly allocated and
        can be reissued, and a stale entry would silently suppress the
        warning for the next network to hold this vxid."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        maintain.EXTRA_VLANS_WARNED[0x123] = {
            'stray: an instance on this node is attached to it'}

        self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
        )

        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_WARNED)

    def test_failed_device_delete_does_not_abort_the_pass(self):
        """``ip link delete`` racing the net-worker to a device which is
        already gone must not kill the maintain thread. Every device is
        still attempted, no reap event is recorded, and the grace period
        is re-armed so the retry happens on a later pass rather than
        every thirty seconds."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400

        def _explode(command):
            raise Exception('Cannot find device')

        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            execute_side_effect=_explode,
        )

        # One failing device does not abandon the others.
        self.assertEqual(
            [mock.call('ip link delete br-vxlan-000123'),
             mock.call('ip link delete veth-000123-o'),
             mock.call('ip link delete egr-000123-o')],
            active['util_concurrency'].execute.call_args_list)
        node = active['node'].from_db.return_value
        node.add_event.assert_not_called()
        self.assertEqual(
            3,
            active['log'].with_fields.return_value.warning.call_count)
        self.assertEqual(10_000.0, maintain.EXTRA_VLANS_HISTORY[0x123])

    def test_partial_delete_failure_leaves_the_vxlan_interface_alone(self):
        """If a sibling device cannot be deleted, ``vxlan-%06x`` must be
        left in place. ``discover_interfaces()`` only reports a vxid when
        that interface exists, so deleting it while a leftover survives
        would hide the leftover from every future pass -- no
        rediscovery, therefore no retry.

        The devices which did go were still removed from the host, so
        the reap is recorded as a partial one: the audit event is the
        only durable record of which devices are gone, and a warning
        naming the device which failed does not name those."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400

        def _fail_on_egress(command):
            if command.endswith('egr-000123-o'):
                raise Exception('Cannot delete device')

        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            execute_side_effect=_fail_on_egress,
        )

        deletes = [c.args[0]
                   for c in active['util_concurrency'].execute.call_args_list]
        # The bridge and veth are gone, the egress device failed, and
        # the vxlan interface -- the rediscovery handle -- survives.
        self.assertEqual(
            ['ip link delete br-vxlan-000123',
             'ip link delete veth-000123-o',
             'ip link delete egr-000123-o'], deletes)

        node = active['node'].from_db.return_value
        node.add_event.assert_called_once()
        self.assertIn(
            'partially reaped stray vxlan',
            node.add_event.call_args.args[1])
        extra = node.add_event.call_args.kwargs['extra']
        self.assertEqual(
            ['br-vxlan-000123', 'veth-000123-o'], extra['devices'])
        self.assertEqual(['egr-000123-o'], extra['failed'])

        # Re-armed rather than dropped, so the next pass rediscovers the
        # surviving devices and retries.
        self.assertEqual(10_000.0, maintain.EXTRA_VLANS_HISTORY[0x123])

    def test_stray_vxid_naming_no_devices_is_not_reported_as_reaped(self):
        """``discover_interfaces()`` parses a vxid out of any interface
        named ``vxlan-*``, but we reconstruct device names with '%06x'.
        A non-canonical device such as an operator's 'vxlan-1' therefore
        maps to names which do not exist. Claiming a reap would be a
        lie, and re-reporting it every grace period forever is the log
        storm this change removes -- so it is warned about once."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x1] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x1: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            present_devices=set(),
        )

        active['util_concurrency'].execute.assert_not_called()
        node = active['node'].from_db.return_value
        node.add_event.assert_not_called()
        active['log'].with_fields.return_value.warning.\
            assert_called_once_with('Extra vxlan present!')
        self.assertEqual(10_000.0, maintain.EXTRA_VLANS_HISTORY[0x1])
        self.assertIn(0x1, maintain.EXTRA_VLANS_WARNED)

    def test_claimed_stray_teardown_enqueued_on_hypervisor(self):
        """On a hypervisor a stray whose network still exists and which
        no local instance uses is cleaned up by enqueueing a
        node_net_op, not by deleting devices here. The network object
        exists, so there is no reason to mutate host state outside the
        dispatcher."""
        from shakenfist.daemons.network import maintain
        from shakenfist.schema.operations.baseclusteroperation import PRIORITY
        from shakenfist.schema.operations.node_net_op import model_tasks

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            # An instance on this node, but on a different network.
            attached_vxids=[0x456],
        )

        active['util_concurrency'].execute.assert_not_called()
        active['nn_create_and_enqueue'].assert_called_once_with(
            'node-uuid-test', 'claiming-net-000123',
            [model_tasks.network_destroy], PRIORITY.background)
        node = active['node'].from_db.return_value
        self.assertIn(
            'enqueued teardown of stray vxlan',
            node.add_event.call_args.args[1])
        # Re-armed so the operation has a full grace period to run
        # before this vxid is considered again.
        self.assertEqual(10_000.0, maintain.EXTRA_VLANS_HISTORY[0x123])

    def test_claimed_stray_teardown_not_enqueued_while_op_pending(self):
        """A cluster operation already targeting the network will do the
        teardown, so a second one must not be piled on top of it.

        The grace period is re-armed anyway. Leaving the vxid overdue
        would make every 30 second pass repeat the whole candidate
        evaluation for it -- the vxid lookup, the instance hydration and
        an ip link call -- for as long as the other operation runs."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            pending_op=True,
        )

        active['nn_create_and_enqueue'].assert_not_called()
        active['util_concurrency'].execute.assert_not_called()
        self.assertEqual(10_000.0, maintain.EXTRA_VLANS_HISTORY[0x123])

    def test_claimed_stray_protected_by_unhealthy_local_instance(self):
        """An instance on this node which is not healthy -- still
        building, or errored with a domain which may still be running --
        keeps its network out of host_networks, so its vxlan looks
        stray. It must not be torn down.

        The device being in use by a local instance is a positive
        finding, not a problem, so it is noted at INFO rather than
        warned about, and without the CI forbidden string 'Extra vxlan
        present' (issue 3837)."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            attached_vxids=[0x123],
        )

        active['util_concurrency'].execute.assert_not_called()
        active['nn_create_and_enqueue'].assert_not_called()
        active['log'].with_fields.return_value.warning.assert_not_called()
        messages = _info_messages(active)
        self.assertEqual(1, len(messages), messages)
        self.assertNotIn('Extra vxlan present', messages[0])
        self.assertIn(0x123, maintain.EXTRA_VLANS_HISTORY)

    def test_delete_wait_error_instance_protects_its_vxlan(self):
        """delete-wait-error is not in Instance.ACTIVE_STATES, but it
        means teardown did not complete -- so a domain may well still be
        attached to the bridge. Such an instance must protect its
        network's devices."""
        from shakenfist.daemons.network import maintain
        from shakenfist.instance import Instance

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            attached_vxids=[0x123],
            attached_state=Instance.STATE_DELETE_WAIT_ERROR,
        )

        self.assertNotIn(
            Instance.STATE_DELETE_WAIT_ERROR, Instance.ACTIVE_STATES)
        active['nn_create_and_enqueue'].assert_not_called()
        active['util_concurrency'].execute.assert_not_called()
        self.assertIn(0x123, maintain.EXTRA_VLANS_HISTORY)

    def test_deleted_instance_does_not_protect_its_vxlan(self):
        """A placement reference which outlived its instance must not
        protect a device forever."""
        from shakenfist.baseobject import DatabaseBackedObject as dbo
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            attached_vxids=[0x123],
            attached_state=dbo.STATE_DELETED,
        )

        active['nn_create_and_enqueue'].assert_called_once()

    def test_claimed_stray_torn_down_on_node_hosting_no_instances(self):
        """A node which is neither the network node nor currently a
        hypervisor has no business carrying a vxlan device at all, so
        every claimed stray on it is torn down."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
        )

        # The protecting-vxid query was asked about this node, and
        # returned nothing because nothing is placed here.
        call = active['mariadb'].get_node_instance_vxids.call_args
        self.assertEqual('node-uuid-test', call.args[0])
        self.assertEqual(
            set(),
            active['mariadb'].get_node_instance_vxids.side_effect(*call.args))
        active['nn_create_and_enqueue'].assert_called_once()

    def test_claimed_stray_protected_when_this_node_row_is_missing(self):
        """Without this node's row we cannot list its instances, so we
        cannot tell a leaked device from a live one. Protect it."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            node_missing=True,
        )

        active['nn_create_and_enqueue'].assert_not_called()
        active['util_concurrency'].execute.assert_not_called()
        active['log'].with_fields.return_value.warning.\
            assert_called_once_with('Extra vxlan present!')
        # Untouched, so the stray is reconsidered as soon as the node
        # row can be read again.
        self.assertEqual(10_000.0 - 400, maintain.EXTRA_VLANS_HISTORY[0x123])

        # A missing row is looked up on every pass for as long as the
        # stray survives, and from_db() logs one as an error level audit
        # event. The _warn_once() report above is the rate limited
        # operator signal; the audit event would not be.
        for call in active['node'].from_db.call_args_list:
            self.assertEqual(True, call.kwargs.get('suppress_failure_audit'))

    def test_reap_without_node_row_falls_back_to_logging(self):
        """An unclaimed stray is still reaped when this node's row
        cannot be read -- the decision does not depend on it -- but the
        audit event has nowhere to go, so it is logged instead."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            node_missing=True,
        )

        active['util_concurrency'].execute.assert_any_call(
            'ip link delete vxlan-000123')
        messages = _info_messages(active)
        self.assertEqual(1, len(messages), messages)
        self.assertIn('reaped stray vxlan', messages[0])
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)

    def test_unclaimed_stray_reaped_even_with_local_instances(self):
        """A vxid no network row claims is reapable on a hypervisor
        without consulting the instance list at all -- the row is
        written before the device is created, so there is nothing a
        local instance could legitimately be using it for."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            attached_vxids=[0x456],
        )

        active['util_concurrency'].execute.assert_any_call(
            'ip link delete vxlan-000123')
        active['mariadb'].get_node_instance_vxids.assert_not_called()
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)

    def test_guest_tap_on_the_bridge_protects_an_unclaimed_stray(self):
        """The database saying no network claims this vxid is a record,
        not an observation. A device on the bridge which Shaken Fist did
        not put there is a guest tap, which is proof that a domain is
        attached right now -- so the host gets a veto over the record."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            bridge_members={
                'br-vxlan-000123': ['vxlan-000123', 'veth-000123-o', 'vnet7']},
        )

        active['util_concurrency'].execute.assert_not_called()
        active['log'].with_fields.return_value.warning.\
            assert_called_once_with('Extra vxlan present!')
        # The enslaved device is named in the log fields so an operator
        # can work out which domain is holding the stray open.
        fields = [c.args[0] for c in active['log'].with_fields.call_args_list
                  if c.args and 'enslaved' in c.args[0]]
        self.assertEqual(1, len(fields), fields)
        self.assertEqual(['vnet7'], fields[0]['enslaved'])
        # Untouched, so it is reconsidered as soon as the tap goes.
        self.assertEqual(10_000.0 - 400, maintain.EXTRA_VLANS_HISTORY[0x123])

    def test_our_own_devices_on_the_bridge_do_not_protect_a_stray(self):
        """The vxlan interface and the veths are enslaved to the bridge
        by Shaken Fist itself, so they are exactly what a leaked network
        looks like. They must not be mistaken for a guest."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            bridge_members={
                'br-vxlan-000123': ['vxlan-000123', 'veth-000123-o',
                                    'egr-000123-o']},
        )

        active['util_concurrency'].execute.assert_any_call(
            'ip link delete vxlan-000123')
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)

    def test_guest_tap_on_the_bridge_blocks_teardown_enqueue(self):
        """The teardown path deletes the same bridge, by way of a
        node_net_op, so it needs the same host side veto -- this is the
        path whose failure mode is a live domain losing its network."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            bridge_members={'br-vxlan-000123': ['vnet3']},
        )

        active['nn_create_and_enqueue'].assert_not_called()
        active['util_concurrency'].execute.assert_not_called()
        self.assertEqual(10_000.0 - 400, maintain.EXTRA_VLANS_HISTORY[0x123])

    def test_unreadable_bridge_membership_protects_a_stray(self):
        """"Could not ask the host" is not "the host says nobody is
        using it". A failing query also protects the stray, so it
        persists and is re-queried on every pass -- the failure must be
        reported once per episode, not once per pass, or it becomes the
        log storm this reaper exists to end."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            bridge_members_error=Exception('ip is having a bad day'),
        )

        active['util_concurrency'].execute.assert_not_called()
        self.assertEqual(10_000.0 - 400, maintain.EXTRA_VLANS_HISTORY[0x123])

        # A second pass over the same still-failing stray, with a
        # different exception text: nothing further is logged. The
        # first pass emitted both the query failure warning and the
        # protection warning; keying on the bridge name (not the
        # exception text) is what keeps a varying error message from
        # defeating the suppression.
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            bridge_members_error=Exception('ip is having a worse day'),
        )
        active['log'].with_fields.return_value.warning.assert_not_called()
        self.assertEqual(10_000.0 - 400, maintain.EXTRA_VLANS_HISTORY[0x123])

    def test_stray_with_no_bridge_at_all_is_reaped(self):
        """The canonical interrupted teardown residue: the bridge is
        already gone and only the vxlan interface and its veths survive.
        ``_apply_delete_on_hypervisor`` deletes the bridge first and the
        vxlan interface second, so this is the shape a teardown which
        died in the middle actually leaves behind -- and
        ``discover_interfaces()`` keys stray detection on the interface
        which survives, so maintain sees it on every pass.

        A bridge which does not exist has nothing enslaved to it, so the
        host side veto must return "no members" rather than "could not
        ask". ``MaintainBridgeVetoTest`` pins that answer against the
        real iproute2 failure; this test pins what maintain does with
        it."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            present_devices={'veth-000123-o', 'egr-000123-o', 'vxlan-000123'},
        )

        # The absent bridge is skipped rather than attempted, and
        # everything which did survive goes.
        self.assertEqual(
            [mock.call('ip link delete veth-000123-o'),
             mock.call('ip link delete egr-000123-o'),
             mock.call('ip link delete vxlan-000123')],
            active['util_concurrency'].execute.call_args_list)
        node = active['node'].from_db.return_value
        self.assertIn(
            'reaped stray vxlan', node.add_event.call_args.args[1])
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)

    def test_partial_reap_retries_the_survivors_on_a_later_pass(self):
        """A partial reap deletes the bridge and then fails, so the next
        pass sees a stray with no bridge. That must not become "the
        members of the bridge could not be determined" -- otherwise the
        survivors are protected forever and the documented retry never
        happens."""
        from shakenfist.daemons.network import maintain

        def _fail_on_egress(command):
            if command.endswith('egr-000123-o'):
                raise Exception('Cannot delete device')

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            execute_side_effect=_fail_on_egress,
        )
        # Re-armed, and the vxlan interface survives so the stray is
        # still discoverable.
        self.assertEqual(10_000.0, maintain.EXTRA_VLANS_HISTORY[0x123])

        # A later pass, by which time the grace period has expired
        # again. The bridge and veth went last time; only the egress
        # device and the vxlan interface are left.
        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
            present_devices={'egr-000123-o', 'vxlan-000123'},
        )

        self.assertEqual(
            [mock.call('ip link delete egr-000123-o'),
             mock.call('ip link delete vxlan-000123')],
            active['util_concurrency'].execute.call_args_list)
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)

    def test_a_changed_reason_is_reported_again(self):
        """Suppression is per reason, not per vxid. A stray which stops
        being protected for one reason and starts being protected for
        another is a different thing for an operator to look at, and
        must not be silenced by the earlier report -- even when the
        earlier report was a benign INFO note and the new one is a
        genuine warning."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            attached_vxids=[0x123],
        )
        self.assertEqual(1, len(_info_messages(active)))
        active['log'].with_fields.return_value.warning.assert_not_called()

        # The same stray, but now this node's row cannot be read, so the
        # reason it is protected has changed -- and the reaper is flying
        # blind, which is worth a warning.
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            node_missing=True,
        )
        active['log'].with_fields.return_value.warning.\
            assert_called_once_with('Extra vxlan present!')
        self.assertEqual(
            {'stray: an instance on this node is attached to it',
             'stray: the instances on this node could not be determined'},
            maintain.EXTRA_VLANS_WARNED[0x123])

    def test_undeletable_device_is_only_reported_once(self):
        """A device which persistently refuses to be deleted is retried
        once per grace period forever. Warning about it every time would
        be a slower version of the log storm this reaper exists to
        end."""
        from shakenfist.daemons.network import maintain

        def _explode(command):
            raise Exception('Device or resource busy')

        for _ in range(2):
            maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
            active = self._run_one_iteration(
                network_node=True,
                vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
                db_network_vxids=[],
                execute_side_effect=_explode,
            )

        # Three devices attempted on the second pass as well, but every
        # one of them was already reported on the first.
        self.assertEqual(
            3, active['util_concurrency'].execute.call_count)
        active['log'].with_fields.return_value.warning.assert_not_called()

    def test_stray_handling_failure_does_not_kill_the_pass(self):
        """The stray check is the least urgent thing maintain does, and
        the only part of it which depends on an RPC added after the
        daemon shipped. An sf-database which does not implement
        FindNetworkVxids yet answers UNIMPLEMENTED, which is not
        retryable -- that must not take the maintain thread down every
        thirty seconds for the length of a rolling upgrade."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400

        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            find_network_vxids_error=Exception('UNIMPLEMENTED'),
        )

        # The pass completed rather than raising, nothing was deleted on
        # the strength of an answer we did not get, and the stray is
        # still tracked so it is reconsidered once the database tier
        # catches up.
        active['mariadb'].find_network_vxids.assert_called_once()
        active['util_concurrency'].execute.assert_not_called()
        self.assertEqual(10_000.0 - 400, maintain.EXTRA_VLANS_HISTORY[0x123])
        active['util_exceptions'].ignore_exception.assert_called_once()

    def test_stray_handling_failure_is_recorded_on_transition_only(self):
        """The guard fires on every 30 second pass for the length of a
        mixed version window or database outage. Recording the
        exception each time -- an ERROR with a traceback and a spooled
        exception file -- would be a louder version of the log storm
        this reaper exists to end, so it is recorded only on the
        transition into failure, and re-armed by a successful pass."""
        from shakenfist.daemons.network import maintain

        for i in range(2):
            maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
            active = self._run_one_iteration(
                network_node=True,
                vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
                find_network_vxids_error=Exception('UNIMPLEMENTED'),
            )
            if i == 0:
                active['util_exceptions'].ignore_exception.assert_called_once()
            else:
                active['util_exceptions'].ignore_exception.assert_not_called()

        # A successful pass re-arms the report...
        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
        )
        self.assertFalse(maintain.STRAY_VXLAN_HANDLING_FAILING)

        # ... so a fresh failure episode is recorded again.
        maintain.EXTRA_VLANS_HISTORY[0x456] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x456: '02:00:00:aa:bb:cc'},
            find_network_vxids_error=Exception('UNIMPLEMENTED'),
        )
        active['util_exceptions'].ignore_exception.assert_called_once()


class MaintainBridgeVetoTest(base.ShakenFistTestCase):
    """The host side veto, exercised through the real
    ``util_network.foreign_bridge_members()`` and the ``ip`` invocation
    underneath it rather than a stub.

    The tests above patch ``util_network`` wholesale, which is right for
    testing what maintain does with each answer but means they can never
    observe how the helper behaves against a real ``ip`` invocation.
    The distinction between "no members" and "could not ask" decides
    whether devices are deleted, so the seam between the two is worth
    pinning directly.
    """

    def _job(self):
        from shakenfist.daemons.network.maintain import Job
        job = Job.__new__(Job)
        job.name = 'test-maintain'
        return job

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        side_effect=ProcessExecutionError(
            stdout='',
            stderr=('Error: argument "br-vxlan-000123" is wrong: Device '
                    'does not exist\n'),
            exit_code=255,
            cmd='ip -pretty -json link show master br-vxlan-000123'))
    def test_absent_bridge_reports_no_foreign_members(self, mock_execute):
        """An interrupted hypervisor teardown leaves the vxlan interface
        with no bridge, which is the shape maintain most often sees. The
        veto must answer "nothing is attached", not "I could not ask" --
        the latter protects the residue forever."""
        self.assertEqual([], self._job()._foreign_bridge_members(0x123))

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        side_effect=ProcessExecutionError(
            stdout='', stderr='RTNETLINK answers: Operation not permitted',
            exit_code=255,
            cmd='ip -pretty -json link show master br-vxlan-000123'))
    def test_unanswerable_question_still_protects(self, mock_execute):
        """Any other failure is genuinely unanswerable and must keep the
        stray on the protect path."""
        self.assertIsNone(self._job()._foreign_bridge_members(0x123))

    @mock.patch(
        'shakenfist.util.concurrency.execute',
        return_value=(
            """[ {},{
        "ifindex": 19,
        "ifname": "vxlan-000123",
        "link_type": "ether",
        "master": "br-vxlan-000123"
    },{
        "ifindex": 22,
        "ifname": "vnet7",
        "link_type": "ether",
        "master": "br-vxlan-000123"
    } ]""", ''))
    def test_guest_tap_is_reported_and_our_own_devices_are_not(
            self, mock_execute):
        """Only devices Shaken Fist did not put on the bridge count as
        evidence that a domain is attached."""
        self.assertEqual(['vnet7'], self._job()._foreign_bridge_members(0x123))
        mock_execute.assert_called_with(
            'ip -pretty -json link show master br-vxlan-000123',
            check_exit_code=[0, 1], netns=None, suppress_command_logging=True)
