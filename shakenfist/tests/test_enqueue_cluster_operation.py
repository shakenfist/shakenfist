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
        self.mock_create_and_enqueue.return_value = (True, '')

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
            targets=[],
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
        self.mock_create_and_enqueue.return_value = (True, '')

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
        self.mock_create_and_enqueue.return_value = (True, '')

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
        self.mock_create_and_enqueue.return_value = (
            False, 'MariaDB error: deadlock')

        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata())

        self.mock_create_and_enqueue.assert_called_once()
        # No audit event on failure -- phase 3's contract is that
        # audit events are caller-side and only happen after the
        # RPC returns True.
        self.mock_add_event_multi.assert_not_called()

    def test_rpc_failure_logs_actionable_diagnostics(self):
        # Issue 3524: a bare 'Failed to enqueue cluster operation'
        # with only the operation uuid and queue name cannot be
        # actioned. The failure log must carry the operation type,
        # the derived targets and the underlying error.
        self.mock_create_and_enqueue.return_value = (
            False, 'MariaDB error: deadlock')

        with mock.patch(
                'shakenfist.schema.operations.util.LOG') as mock_log:
            util.enqueue_cluster_operation(
                _FakeObjectType('NODE_INST_OP'),
                self._metadata(),
                model_class=_NullableTargetModel)

        mock_log.with_fields.assert_called_once()
        fields = mock_log.with_fields.call_args[0][0]
        self.assertEqual(OP_UUID, fields['operation_uuid'])
        self.assertEqual('node_inst_op', fields['operation_type'])
        self.assertEqual(
            f'{NODE_UUID}-clusteroperation-user_waiting',
            fields['queue_name'])
        self.assertEqual(
            [f'instance:{INSTANCE_UUID}'], fields['targets'])
        self.assertEqual('MariaDB error: deadlock', fields['error'])
        mock_log.with_fields.return_value.error.assert_called_once_with(
            'Failed to enqueue cluster operation')
        self.mock_add_event_multi.assert_not_called()


class EnqueueClusterOperationAutoTargetTestCase(base.ShakenFistTestCase):
    """Tests for the auto-target derivation.

    enqueue_cluster_operation iterates the model_class's
    target_fields ClassVar and passes one ``(ObjectType,
    target_uuid)`` pair per non-None target to
    create_and_enqueue_cluster_operation via its ``targets``
    argument, so the target rows are written in the same
    transaction as the operation. These tests exercise the four
    cases called out in the phase plan: single-target,
    multi-target, nullable, and missing-declaration. They also
    assert the retired non-atomic writer
    (mariadb.create_cluster_operation_target) is never called
    from this path.
    """

    def setUp(self):
        super().setUp()
        self.mock_create_and_enqueue = mock.patch(
            'shakenfist.mariadb.create_and_enqueue_cluster_operation'
        ).start()
        self.mock_create_and_enqueue.return_value = (True, '')

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

    def _targets_arg(self):
        return self.mock_create_and_enqueue.call_args.kwargs['targets']

    def test_single_target_writes_one_row(self):
        util.enqueue_cluster_operation(
            _FakeObjectType('NET_OP'),
            self._metadata(network_uuid=NETWORK_UUID),
            target='networknode',
            model_class=_NetOnlyModel)

        self.assertEqual(
            [(ObjectType.NETWORK, NETWORK_UUID)],
            self._targets_arg())
        # The retired non-atomic writer must not be used any more.
        self.mock_create_target.assert_not_called()

    def test_multi_target_writes_three_rows(self):
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_NET_IFACE_OP'),
            self._metadata(
                instance_uuid=INSTANCE_UUID,
                network_uuid=NETWORK_UUID,
                interface_uuid=INTERFACE_UUID,
            ),
            model_class=_MultiTargetModel)

        self.assertEqual(
            {(ObjectType.INSTANCE, INSTANCE_UUID),
             (ObjectType.NETWORK, NETWORK_UUID),
             (ObjectType.INTERFACE, INTERFACE_UUID)},
            set(self._targets_arg()))
        self.mock_create_target.assert_not_called()

    def test_nullable_target_field_is_skipped(self):
        # instance_uuid present in metadata but None -- should be
        # treated as "no target on this field" and produce no row.
        util.enqueue_cluster_operation(
            _FakeObjectType('ARTIFACT_FETCH_OP'),
            self._metadata(instance_uuid=None),
            model_class=_NullableTargetModel)

        self.assertEqual([], self._targets_arg())
        self.mock_create_target.assert_not_called()

    def test_missing_declaration_is_a_noop(self):
        # A model class without target_fields must not crash --
        # the derived targets list is simply empty.
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata(instance_uuid=INSTANCE_UUID),
            model_class=_NoDeclarationModel)

        self.assertEqual([], self._targets_arg())
        self.mock_create_target.assert_not_called()

    def test_no_model_class_argument_is_a_noop(self):
        # Backwards compatibility: a caller that does not pass
        # model_class still works, just without auto-targeting.
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata(instance_uuid=INSTANCE_UUID))

        self.assertEqual([], self._targets_arg())
        self.mock_create_target.assert_not_called()


class EnqueueClusterOperationFamilyTestCase(base.ShakenFistTestCase):
    """Tests for the family parameter on enqueue_cluster_operation.

    Verifies that:
    - Omitting family defaults to 'clusteroperation' (existing behaviour).
    - Passing family='network' produces {target}-network-{priority}.
    - The audit-event fan-out and cluster_operation_targets writes are
      unaffected by the family parameter.
    """

    def setUp(self):
        super().setUp()
        self.mock_create_and_enqueue = mock.patch(
            'shakenfist.mariadb.create_and_enqueue_cluster_operation'
        ).start()
        self.mock_create_and_enqueue.return_value = (True, '')

        self.mock_create_target = mock.patch(
            'shakenfist.mariadb.create_cluster_operation_target'
        ).start()

        self.mock_add_event_multi = mock.patch(
            'shakenfist.schema.operations.util.eventlog'
            '.add_event_multi'
        ).start()

        self.mock_time = mock.patch(
            'shakenfist.schema.operations.util.time.time',
            return_value=3000.0,
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

    def test_default_family_produces_clusteroperation_queue_name(self):
        """Omitting family preserves today's queue name format."""
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata())

        call_kwargs = self.mock_create_and_enqueue.call_args.kwargs
        self.assertEqual(
            call_kwargs['queue_name'],
            f'{NODE_UUID}-clusteroperation-user_waiting')

    def test_explicit_default_family_matches_omitted(self):
        """Passing family='clusteroperation' explicitly is identical to omitting it."""
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_INST_OP'),
            self._metadata(),
            family='clusteroperation')

        call_kwargs = self.mock_create_and_enqueue.call_args.kwargs
        self.assertEqual(
            call_kwargs['queue_name'],
            f'{NODE_UUID}-clusteroperation-user_waiting')

    def test_network_family_produces_network_queue_name(self):
        """family='network' produces {target}-network-{priority}."""
        node_uuid = 'node1111-1111-4111-8111-111111111111'
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_NET_OP'),
            self._metadata(node_uuid=node_uuid),
            family='network')

        call_kwargs = self.mock_create_and_enqueue.call_args.kwargs
        self.assertEqual(
            call_kwargs['queue_name'],
            f'{node_uuid}-network-user_waiting')

    def test_network_family_with_explicit_target(self):
        """family='network' with an explicit target uses the target, not node_uuid."""
        explicit_target = 'target-node-uuid-1111-111111111111'
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_NET_OP'),
            self._metadata(),
            target=explicit_target,
            family='network')

        call_kwargs = self.mock_create_and_enqueue.call_args.kwargs
        self.assertEqual(
            call_kwargs['queue_name'],
            f'{explicit_target}-network-user_waiting')

    def test_family_does_not_affect_audit_event_fan_out(self):
        """The family parameter does not change audit event targets."""
        md = self._metadata(network_uuid=NETWORK_UUID)
        util.enqueue_cluster_operation(
            _FakeObjectType('NODE_NET_OP'), md, family='network')

        self.mock_add_event_multi.assert_called_once()
        targets = self.mock_add_event_multi.call_args[0][1]
        self.assertIn(('node_net_op', OP_UUID), targets)
        self.assertIn(('node', NODE_UUID), targets)
        self.assertIn(('network', NETWORK_UUID), targets)

    def test_family_does_not_affect_cluster_operation_targets_writes(self):
        """The family parameter does not change cluster_operation_targets writes."""
        util.enqueue_cluster_operation(
            _FakeObjectType('NET_OP'),
            self._metadata(network_uuid=NETWORK_UUID),
            target='networknode',
            model_class=_NetOnlyModel,
            family='network')

        self.assertEqual(
            [(ObjectType.NETWORK, NETWORK_UUID)],
            self.mock_create_and_enqueue.call_args.kwargs['targets'])
        self.mock_create_target.assert_not_called()
