# Copyright 2026 Michael Still and contributors
#
# Tests for shakenfist.schema.operations.util.enqueue_cluster_operation,
# the new high-level helper from phase 4 of the etcd-removal
# ops-queues plan. The helper replaces base_mutations() + enqueue()
# and wraps mariadb.create_and_enqueue_cluster_operation() +
# eventlog.add_event_multi().

from typing import ClassVar
from unittest import mock

from pydantic import BaseModel

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations import util
from shakenfist.tests import base


INTERFACE_UUID = 'dddd1111-1111-4111-8111-111111111111'


# Minimal stand-in models used by the auto-target tests below. The
# real schema models pull in pydantic field validators and PRIORITY
# enums that are unrelated to the target write logic, so we use
# bare BaseModel subclasses that only declare target_fields.
class _NetOnlyModel(BaseModel):
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'network_uuid': ObjectType.NETWORK,
    }


class _MultiTargetModel(BaseModel):
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'instance_uuid': ObjectType.INSTANCE,
        'network_uuid': ObjectType.NETWORK,
        'interface_uuid': ObjectType.INTERFACE,
    }


class _NullableTargetModel(BaseModel):
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'instance_uuid': ObjectType.INSTANCE,
    }


class _NoDeclarationModel(BaseModel):
    pass


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


class EnqueueClusterOperationAutoTargetTestCase(base.ShakenFistTestCase):
    """Tests for the phase 3a auto-target writes.

    enqueue_cluster_operation iterates the model_class's
    target_fields ClassVar and writes one
    cluster_operation_targets row per non-None target. These
    tests exercise the four cases called out in the phase plan:
    single-target, multi-target, nullable, and missing-
    declaration.
    """

    def setUp(self):
        super().setUp()
        self.mock_create_and_enqueue = mock.patch(
            'shakenfist.mariadb.create_and_enqueue_cluster_operation'
        ).start()
        self.mock_create_and_enqueue.return_value = True

        self.mock_create_target = mock.patch(
            'shakenfist.mariadb.create_cluster_operation_target'
        ).start()

        self.mock_add_event_multi = mock.patch(
            'shakenfist.schema.operations.util.eventlog'
            '.add_event_multi'
        ).start()

        self.mock_time = mock.patch(
            'shakenfist.schema.operations.util.time.time',
            return_value=2000.0,
        ).start()

        self.addCleanup(mock.patch.stopall)

    def _metadata(self, **overrides):
        md = {
            'uuid': OP_UUID,
            'node_uuid': NODE_UUID,
            'priority': 'user_waiting',
            'tasks': ['t'],
        }
        md.update(overrides)
        return md

    def test_single_target_writes_one_row(self):
        util.enqueue_cluster_operation(
            _FakeObjectType('NET_OP'),
            self._metadata(network_uuid=NETWORK_UUID),
            target='networknode',
            model_class=_NetOnlyModel)

        self.mock_create_target.assert_called_once_with(
            operation_uuid=OP_UUID,
            operation_type='net_op',
            target_object_type=ObjectType.NETWORK,
            target_uuid=NETWORK_UUID,
            created_at=2000.0,
        )

    def test_multi_target_writes_three_rows(self):
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_NET_IFACE_OP'),
            self._metadata(
                instance_uuid=INSTANCE_UUID,
                network_uuid=NETWORK_UUID,
                interface_uuid=INTERFACE_UUID,
            ),
            model_class=_MultiTargetModel)

        self.assertEqual(3, self.mock_create_target.call_count)
        target_types = {
            call.kwargs['target_object_type']
            for call in self.mock_create_target.call_args_list
        }
        self.assertEqual(
            {ObjectType.INSTANCE, ObjectType.NETWORK,
             ObjectType.INTERFACE},
            target_types)
        target_uuids = {
            call.kwargs['target_uuid']
            for call in self.mock_create_target.call_args_list
        }
        self.assertEqual(
            {INSTANCE_UUID, NETWORK_UUID, INTERFACE_UUID},
            target_uuids)
        for call in self.mock_create_target.call_args_list:
            self.assertEqual(OP_UUID, call.kwargs['operation_uuid'])
            self.assertEqual(
                'node_inst_net_iface_op',
                call.kwargs['operation_type'])

    def test_nullable_target_field_is_skipped(self):
        # instance_uuid present in metadata but None -- should be
        # treated as "no target on this field" and produce no row.
        util.enqueue_cluster_operation(
            _FakeObjectType('ARTIFACT_FETCH_OP'),
            self._metadata(instance_uuid=None),
            model_class=_NullableTargetModel)

        self.mock_create_target.assert_not_called()

    def test_missing_declaration_is_a_noop(self):
        # A model class without target_fields must not crash --
        # the central writer simply does nothing.
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata(instance_uuid=INSTANCE_UUID),
            model_class=_NoDeclarationModel)

        self.mock_create_target.assert_not_called()

    def test_no_model_class_argument_is_a_noop(self):
        # Backwards compatibility: a caller that does not pass
        # model_class still works, just without auto-targeting.
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata(instance_uuid=INSTANCE_UUID))

        self.mock_create_target.assert_not_called()
