# Copyright 2019 Michael Still and contributors

"""Tests for shakenfist.daemons.network.maintain.

Phase 6 rewrote maintain.py as a discovery-only pass governed by the
five-guard pipeline: queue-depth, pending-op, cooldown, circuit-breaker
and (on success) enqueue at PRIORITY.background. These tests exercise
each guard.
"""

from unittest import mock

from shakenfist.exceptions import ProcessExecutionError
from shakenfist.tests import base


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
                           find_network_vxids_error=None):
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
        to it, and ``bridge_members_error`` makes
        ``get_bridge_members()`` raise. Both drive the host side
        cross-check the reaper performs before it mutates anything;
        the default is a bridge with no members at all.
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

            md = active['daemon']
            md.check_abort_path.side_effect = [True, False]
            md.clear_abort_path.return_value = None

            mt = active['time']
            mt.time.return_value = 10_000.0
            mt.sleep.return_value = None

            active['util_network'].discover_interfaces.return_value = (
                None, None, vxid_to_mac)
            if present_devices is not None:
                active['util_network'].check_for_interface.side_effect = (
                    lambda device: device in present_devices)

            if bridge_members_error is not None:
                active['util_network'].get_bridge_members.side_effect = (
                    bridge_members_error)
            else:
                members = bridge_members or {}
                active['util_network'].get_bridge_members.side_effect = (
                    lambda bridge: list(members.get(bridge, [])))

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
        active['log'].with_fields.return_value.info.assert_called_once()
        message = active['log'].with_fields.return_value.info.call_args.args[0]
        self.assertNotIn('Extra vxlan present', message)
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
        active['log'].with_fields.return_value.info.assert_not_called()

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
        active['log'].with_fields.return_value.info.assert_called_once()
        message = active['log'].with_fields.return_value.info.call_args.args[0]
        self.assertNotIn('Extra vxlan present', message)
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
        active['log'].with_fields.return_value.info.assert_called_once()
        self.assertIn(
            'reaped stray vxlan',
            active['log'].with_fields.return_value.info.call_args.args[0])
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
        fields = active['log'].with_fields.call_args.args[0]
        self.assertEqual(['vnet7'], fields['enslaved'])
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
        active['log'].with_fields.return_value.info.assert_called_once()
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
    ``util_network.get_bridge_members()`` rather than a stub.

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
