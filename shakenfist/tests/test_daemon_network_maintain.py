# Copyright 2019 Michael Still and contributors

"""Tests for shakenfist.daemons.network.maintain.

Phase 6 rewrote maintain.py as a discovery-only pass governed by the
five-guard pipeline: queue-depth, pending-op, cooldown, circuit-breaker
and (on success) enqueue at PRIORITY.background. These tests exercise
each guard.
"""

from unittest import mock

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
        maintain.EXTRA_VLANS_HISTORY.clear()
        maintain.EXTRA_VLANS_WARNED.clear()

    def _run_one_iteration(self, *, network_node=True,
                           queue_depth_per_queue=0,
                           pending_op=False, recent_history=None,
                           networks=None, floating_network=None,
                           vxid_to_mac=None, db_network_vxids=None,
                           attached_vxids=None):
        """Drive Job.execute() through exactly one pass of the outer loop
        and return a dict of the mocks that callers will most likely
        want to assert on.

        ``attached_vxids`` models networks which an instance on this node
        is attached to but which the maintain pass does not maintain --
        an instance which has errored, or is still building. These are
        the networks the stray reaper must not touch on a hypervisor.
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

            active['network'].Networks.return_value = networks
            # Network.from_db should return the matching mock by uuid
            uuid_to_net = {n.uuid: n for n in networks}
            active['network'].Network.from_db.side_effect = (
                lambda uuid, **kw: uuid_to_net.get(uuid))
            active['network'].floating_network.return_value = floating_network

            # For the non-network-node path, surface one instance whose
            # interfaces reference each test network.
            healthy_instances = []
            if not network_node:
                fake_inst = mock.MagicMock()
                fake_inst.state.value = 'created'
                fake_inst.interfaces = []
                for net in networks:
                    ni = mock.MagicMock()
                    ni.network_uuid = net.uuid
                    fake_inst.interfaces.append(ni)
                healthy_instances.append(fake_inst)

            # Instances which exist on this node but are not healthy, so
            # they never reach host_networks and their networks look
            # stray to the extra-vxlan check.
            unhealthy_instances = []
            for vxid in attached_vxids or []:
                net_uuid = 'attached-net-%06x' % vxid
                attached_net = mock.MagicMock()
                attached_net.uuid = net_uuid
                attached_net.vxid = vxid
                uuid_to_net[net_uuid] = attached_net

                fake_inst = mock.MagicMock()
                fake_inst.state.value = 'error'
                ni = mock.MagicMock()
                ni.network_uuid = net_uuid
                fake_inst.interfaces = [ni]
                unhealthy_instances.append(fake_inst)

            def _instances(_filters, prefilter=None, **kwargs):
                if prefilter == 'healthy':
                    return healthy_instances
                return healthy_instances + unhealthy_instances

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
            claimed = set(db_network_vxids)
            mar.find_network_vxids.side_effect = (
                lambda vxids: {v for v in vxids if v in claimed})

            active['node'].from_db.return_value = mock.MagicMock()

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
        fake_fn.ipam.get_reservation.return_value = fake_resv

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
        # just the bridge and the vxlan interface.
        for device in ['br-vxlan-000123', 'vxlan-000123',
                       'veth-000123-o', 'egr-000123-o']:
            active['util_concurrency'].execute.assert_any_call(
                'ip link delete %s' % device)
        node = active['node'].from_db.return_value
        node.add_event.assert_called_once()
        self.assertIn(
            'reaped stray vxlan', node.add_event.call_args.args[1])
        # The stray is forgotten so a reappearance gets a fresh grace
        # period.
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)
        # The node is only looked up once, not once per reaped vxid.
        active['node'].from_db.assert_called_once()

    def test_stray_vxlan_claimed_by_network_warned_once_not_reaped(self):
        """A stray vxlan whose vxid is still claimed by a network row in
        the database must not be deleted, and is warned about once per
        stray episode rather than on every pass."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
        )

        active['util_concurrency'].execute.assert_not_called()
        active['log'].with_fields.return_value.warning.\
            assert_called_once_with('Extra vxlan present!')
        self.assertIn(0x123, maintain.EXTRA_VLANS_WARNED)

        # A second pass over the same stray must not warn again.
        active = self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
        )
        active['util_concurrency'].execute.assert_not_called()
        active['log'].with_fields.return_value.warning.assert_not_called()

    def test_stray_vxlan_disappearance_resets_tracking(self):
        """When a stray vxlan disappears from the host its history and
        warned state are dropped, so a reappearance is a new episode."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        maintain.EXTRA_VLANS_WARNED.add(0x123)
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
        maintain.EXTRA_VLANS_WARNED.add(0x123)

        self._run_one_iteration(
            network_node=True,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[],
        )

        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_WARNED)

    def test_failed_device_delete_does_not_abort_the_pass(self):
        """``ip link delete`` racing the net-worker to a device which is
        already gone must not kill the maintain thread. The vxid stays
        in the history so the next pass retries, and no reap event is
        recorded."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400

        def _explode(command):
            raise Exception('Cannot find device "br-vxlan-000123"')

        patches = _patch_maintain_module()
        active = {name: p.start() for name, p in patches.items()}
        try:
            mc = active['config']
            mc.NODE_IS_NETWORK_NODE = True
            mc.NODE_UUID = 'node-uuid-test'
            mc.NODE_NAME = 'node-name-test'
            mc.MAINTAIN_QUEUE_DEPTH_THRESHOLD = 50
            mc.MAINTAIN_STRAY_VXLAN_GRACE_SECONDS = 300

            active['daemon'].check_abort_path.side_effect = [True, False]
            active['time'].time.return_value = 10_000.0
            active['util_network'].discover_interfaces.return_value = (
                None, None, {0x123: '02:00:00:aa:bb:cc'})
            active['network'].Networks.return_value = []
            active['network'].floating_network.return_value = None
            active['get_node_network_queues'].return_value = ['q-a']
            active['get_all_network_queues'].return_value = []
            active['mariadb'].get_work_queue_length.return_value = (0, 0, 0)
            active['mariadb'].find_network_vxids.return_value = set()
            active['util_concurrency'].execute.side_effect = _explode

            from shakenfist.daemons.network.maintain import Job
            job = Job.__new__(Job)
            job.name = 'test-maintain'
            job.abort_path = '/run/sf/net-test-maintain.abort'

            # The pass completes rather than raising out of the worker.
            job.execute()

            node = active['node'].from_db.return_value
            node.add_event.assert_not_called()
            active['log'].with_fields.return_value.warning.\
                assert_called_once()
            self.assertIn(0x123, maintain.EXTRA_VLANS_HISTORY)
        finally:
            for p in patches.values():
                p.stop()

    def test_claimed_stray_reaped_on_hypervisor_with_no_local_instance(self):
        """On a hypervisor a stray whose network still exists is reapable
        when no instance on this node is attached to that network -- the
        device is residue from per-instance teardown which was missed."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
        )

        active['util_concurrency'].execute.assert_any_call(
            'ip link delete vxlan-000123')
        node = active['node'].from_db.return_value
        self.assertIn(
            'no instance on this node', node.add_event.call_args.args[1])
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)

    def test_claimed_stray_protected_by_unhealthy_local_instance(self):
        """An instance on this node which is not healthy -- still
        building, or errored with a domain which may still be running --
        keeps its network out of host_networks, so its vxlan looks
        stray. It must not be reaped."""
        from shakenfist.daemons.network import maintain

        maintain.EXTRA_VLANS_HISTORY[0x123] = 10_000.0 - 400
        active = self._run_one_iteration(
            network_node=False,
            vxid_to_mac={0x123: '02:00:00:aa:bb:cc'},
            db_network_vxids=[0x123],
            attached_vxids=[0x123],
        )

        active['util_concurrency'].execute.assert_not_called()
        active['log'].with_fields.return_value.warning.\
            assert_called_once_with('Extra vxlan present!')
        self.assertIn(0x123, maintain.EXTRA_VLANS_HISTORY)

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
        self.assertNotIn(0x123, maintain.EXTRA_VLANS_HISTORY)
