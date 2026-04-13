# Copyright 2026 Michael Still and contributors
#
# Tests for shakenfist.schema.operations.util.enqueue_cluster_operation,
# the new high-level helper from phase 4 of the etcd-removal
# ops-queues plan. The helper replaces base_mutations() + enqueue()
# and wraps mariadb.create_and_enqueue_cluster_operation() +
# eventlog.add_event_multi().

from unittest import mock

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.schema.operations import util
from shakenfist.tests import base


OP_UUID = '11111111-1111-4111-8111-111111111111'
NODE_UUID = 'aaaa1111-1111-4111-8111-111111111111'
INSTANCE_UUID = 'bbbb1111-1111-4111-8111-111111111111'
NETWORK_UUID = 'cccc1111-1111-4111-8111-111111111111'


class _FakeObjectType:
    """Stand-in for ClusterOperation enum members.

    util.enqueue_cluster_operation only cares about `.name` —
    using a real ClusterOperation would pull in a lot of
    unrelated baseclass state that this test does not need.
    """

    def __init__(self, name):
        self.name = name


class EnqueueClusterOperationTestCase(base.ShakenFistTestCase):

    def setUp(self):
        super().setUp()
        self.mock_create_and_enqueue = mock.patch(
            'shakenfist.mariadb.create_and_enqueue_cluster_operation'
        ).start()
        self.addCleanup(mock.patch.stopall)

        self.mock_add_event_multi = mock.patch(
            'shakenfist.schema.operations.util.eventlog'
            '.add_event_multi'
        ).start()

        # Freeze time so the queue_name and created_at values are
        # predictable across tests.
        self.mock_time = mock.patch(
            'shakenfist.schema.operations.util.time.time',
            return_value=1000.0,
        ).start()

    def _metadata(self, **overrides):
        md = {
            'uuid': OP_UUID,
            'node_uuid': NODE_UUID,
            'instance_uuid': INSTANCE_UUID,
            'priority': 'user_waiting',
            'tasks': ['fetch_image', 'provision_interfaces'],
        }
        md.update(overrides)
        return md

    def test_happy_path_calls_rpc_and_emits_audit_event(self):
        self.mock_create_and_enqueue.return_value = True

        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata())

        self.mock_create_and_enqueue.assert_called_once_with(
            op_uuid=OP_UUID,
            operation_type='node_inst_op',
            metadata=self._metadata(),
            created_at=1000.0,
            queue_name=(
                f'{NODE_UUID}-clusteroperation-user_waiting'),
        )

        self.mock_add_event_multi.assert_called_once()
        args, kwargs = self.mock_add_event_multi.call_args
        self.assertEqual(args[0], EVENT_TYPE_AUDIT)
        # Targets: the operation itself plus each _uuid key.
        targets = args[1]
        self.assertIn(('node_inst_op', OP_UUID), targets)
        self.assertIn(('node', NODE_UUID), targets)
        self.assertIn(('instance', INSTANCE_UUID), targets)
        # Message contains the tasks list.
        self.assertIn('fetch_image', args[2])
        self.assertIn('provision_interfaces', args[2])
        # Extra dict carries op_uuid and op_type rename.
        extra = kwargs['extra']
        self.assertEqual(extra['op_uuid'], OP_UUID)
        self.assertEqual(extra['op_type'], 'node_inst_op')
        self.assertNotIn('uuid', extra)

    def test_target_override_changes_queue_name(self):
        self.mock_create_and_enqueue.return_value = True

        util.enqueue_cluster_operation(
            _FakeObjectType('NET_OP'),
            self._metadata(),
            target='networknode')

        call_kwargs = (
            self.mock_create_and_enqueue.call_args.kwargs)
        self.assertEqual(
            call_kwargs['queue_name'],
            'networknode-clusteroperation-user_waiting')

    def test_missing_node_uuid_and_no_target_raises(self):
        md = self._metadata()
        del md['node_uuid']

        self.assertRaises(
            KeyError,
            util.enqueue_cluster_operation,
            _FakeObjectType('NODE_INST_OP'),
            md)
        self.mock_create_and_enqueue.assert_not_called()
        self.mock_add_event_multi.assert_not_called()

    def test_multiple_uuid_keys_produce_fanout_targets(self):
        self.mock_create_and_enqueue.return_value = True

        md = self._metadata()
        md['network_uuid'] = NETWORK_UUID

        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_NET_OP'), md)

        targets = self.mock_add_event_multi.call_args[0][1]
        self.assertIn(('node_net_op', OP_UUID), targets)
        self.assertIn(('node', NODE_UUID), targets)
        self.assertIn(('instance', INSTANCE_UUID), targets)
        self.assertIn(('network', NETWORK_UUID), targets)
        # uuid itself is not re-emitted as a ('', OP_UUID) target.
        self.assertNotIn(('', OP_UUID), targets)

    def test_rpc_failure_suppresses_audit_event(self):
        self.mock_create_and_enqueue.return_value = False

        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata())

        self.mock_create_and_enqueue.assert_called_once()
        # No audit event on failure -- phase 3's contract is that
        # audit events are caller-side and only happen after the
        # RPC returns True.
        self.mock_add_event_multi.assert_not_called()
