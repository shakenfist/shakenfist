# Copyright 2026 Michael Still and contributors
#
# Observational operations are invisible to the "current state" readers.
#
# get_latest_cluster_operation_target() (which backs last_cluster_operation)
# and has_pending_cluster_operation_target() skip target rows stamped
# observational, so a background sweep cannot shadow the mutating op a
# caller is waiting on, nor make an object look busy. Every other reader
# still sees those rows. See docs/plans/PLAN-cluster-op-visibility.md.
#
# The behaviour test here drives the real enqueue path --
# enqueue_cluster_operation() deriving the flag, and
# _direct_create_and_enqueue_cluster_operation() writing the
# cluster_operations, object_states, work_queue and
# cluster_operation_targets rows -- into sqlite tables built from
# mariadb.py's own definitions, then asks the production readers. Two
# accommodations are made for sqlite, neither touching the rows under
# test:
#
# * The object_states write is MariaDB's INSERT ... ON DUPLICATE KEY
#   UPDATE, which sqlite cannot compile. It is translated to sqlite's
#   equivalent upsert on the same primary key (see _SqliteUpsert).
# * sqlite only autoincrements INTEGER primary keys, so the BIGINT
#   AUTO_INCREMENT keys are narrowed on the test's copy of the tables
#   (dbfixture's autoincrement_bigint_keys).

from enum import Enum
import json
from typing import ClassVar
from unittest import mock
from uuid import UUID

from pydantic import BaseModel
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite as sqlite_dialect

from shakenfist.daemons.database import main as database_main
from shakenfist import mariadb
from shakenfist.protos import database_pb2
from shakenfist.schema.cluster_operation_target import ClusterOperationTargetData
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations import node_blob_op
from shakenfist.schema.operations import node_inst_op
from shakenfist.schema.operations import util
from shakenfist.tests import base
from shakenfist.tests import dbfixture


NODE_UUID = 'aaaa2222-2222-4222-8222-222222222222'
INSTANCE_UUID = 'bbbb2222-2222-4222-8222-222222222222'
MUTATING_OP = '11112222-2222-4222-8222-000000000001'
OBSERVING_OP = '11112222-2222-4222-8222-000000000002'
MIXED_OP = '11112222-2222-4222-8222-000000000003'
BLOB_UUID = 'cccc2222-2222-4222-8222-222222222222'
DELETE_OP = '11112222-2222-4222-8222-000000000004'
SWEEP_OP = '11112222-2222-4222-8222-000000000005'


class _Tasks(Enum):
    check = 1
    mutate = 2


class _ObservingModel(BaseModel):
    """A test-local op model declaring one observational task.

    The mechanism is exercised through this model rather than a production
    one, so these tests do not change when a production task is
    reclassified.
    """
    target_fields: ClassVar[dict[str, ObjectType]] = {
        'instance_uuid': ObjectType.INSTANCE,
    }
    observational_tasks: ClassVar[frozenset[_Tasks]] = frozenset({
        _Tasks.check,
    })


class _FakeObjectType:
    """Stand-in for a ClusterOperation enum member; only .name is read."""

    def __init__(self, name):
        self.name = name


class _SqliteUpsert:
    """Translate the MySQL upsert mariadb.py builds into sqlite's.

    Stands in for sqlalchemy.dialects.mysql.insert for the duration of a
    test. ``insert(t).values(...).on_duplicate_key_update(...)`` becomes
    ``INSERT ... ON CONFLICT (<primary key>) DO UPDATE SET ...``, which
    has the same effect when, as for object_states, the duplicate key is
    the primary key.
    """

    def __init__(self, table):
        self._table = table
        self._stmt = sqlite_dialect.insert(table)

    def values(self, **kwargs):
        self._stmt = self._stmt.values(**kwargs)
        return self

    def on_duplicate_key_update(self, **kwargs):
        return self._stmt.on_conflict_do_update(
            index_elements=list(self._table.primary_key.columns),
            set_=kwargs)


class ClusterOperationTablesFixture(dbfixture.MariaDBTableFixture):
    """The real enqueue path and readers over sqlite cluster operation tables.

    Mix in ahead of base.ShakenFistTestCase. Also used by
    test_object_cluster_operations.
    """

    def setUp(self):
        super().setUp()
        self.engine = self.build_engine(
            [mariadb._get_cluster_operations_table,
             mariadb._get_object_states_table,
             mariadb._get_work_queue_table,
             mariadb._get_cluster_operation_targets_table],
            autoincrement_bigint_keys=True)

        for p in [
                mock.patch('shakenfist.mariadb._get_engine',
                           return_value=self.engine),
                mock.patch('shakenfist.mariadb._use_database_service',
                           return_value=False),
                # This only takes effect because mariadb.py resolves
                # sa.dialects.mysql.insert at call time. If that ever
                # becomes a module-level "from ... import insert", patch
                # the name mariadb.py binds instead, or the test fails
                # with a sqlite compile error rather than a clear message.
                mock.patch('sqlalchemy.dialects.mysql.insert', _SqliteUpsert),
                mock.patch('shakenfist.schema.operations.util.eventlog'
                           '.add_event_multi')]:
            p.start()
        self.addCleanup(mock.patch.stopall)

    def _set_op_state(self, op_uuid, state_value):
        states = mariadb._get_object_states_table()
        with self.engine.begin() as conn:
            result = conn.execute(
                sa.update(states)
                .where(states.c.object_uuid == op_uuid)
                .values(state_value=state_value))
        self.assertEqual(1, result.rowcount)

    def _op_state(self, op_uuid):
        states = mariadb._get_object_states_table()
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(states.c.state_value)
                .where(states.c.object_uuid == op_uuid)).scalar()


class ObservationalCurrentStateTestCase(
        ClusterOperationTablesFixture, base.ShakenFistTestCase):
    """Decision 8's behaviour test, against real SQL."""

    def _enqueue(self, op_uuid, tasks, model_class=_ObservingModel,
                 op_type='NODE_INST_OP', target_field='instance_uuid',
                 target_uuid=INSTANCE_UUID):
        util.enqueue_cluster_operation(
            _FakeObjectType(op_type),
            {
                'uuid': op_uuid,
                'node_uuid': NODE_UUID,
                target_field: target_uuid,
                'priority': 'background',
                'tasks': tasks,
            },
            model_class=model_class)

    def _enqueue_inst(self, op_uuid, task_names):
        """Enqueue against the production node_inst_op model."""
        self._enqueue(op_uuid, [t.name for t in task_names],
                      model_class=node_inst_op.model)

    def _enqueue_blob(self, op_uuid, task_names):
        """Enqueue against the production node_blob_op model."""
        self._enqueue(op_uuid, [t.name for t in task_names],
                      model_class=node_blob_op.model,
                      op_type='NODE_BLOB_OP', target_field='blob_uuid',
                      target_uuid=BLOB_UUID)

    def _latest(self):
        latest = mariadb.get_latest_cluster_operation_target(
            ObjectType.INSTANCE, INSTANCE_UUID)
        return latest.operation_uuid if latest else None

    def _pending(self):
        return mariadb.has_pending_cluster_operation_target(
            ObjectType.INSTANCE, INSTANCE_UUID)

    def test_observational_op_does_not_shadow_or_count_as_pending(self):
        # (a) A mutating op becomes the latest, and is pending while queued.
        self._enqueue(MUTATING_OP, ['mutate'])
        self.assertEqual(MUTATING_OP, self._latest())
        self.assertTrue(self._pending())

        # (b) An all-observational op enqueued after it does not move the
        # pointer.
        self._enqueue(OBSERVING_OP, ['check'])
        self.assertEqual(MUTATING_OP, self._latest())
        self.assertTrue(self._pending())

        # Once the mutating op completes nothing is pending, although the
        # observational op is still queued. Its row exists and carries the
        # flag -- it is being filtered out, not missing.
        self._set_op_state(MUTATING_OP, 'complete')
        self.assertEqual('queued', self._op_state(OBSERVING_OP))
        self.assertFalse(self._pending())
        self.assertEqual(MUTATING_OP, self._latest())

        rows = mariadb.get_cluster_operation_targets_for_object(
            ObjectType.INSTANCE, INSTANCE_UUID)
        self.assertEqual(
            [(MUTATING_OP, False), (OBSERVING_OP, True)],
            [(r.operation_uuid, r.observational) for r in rows])

        # (c) A mixed op is mutating: it moves the pointer and is pending.
        self._enqueue(MIXED_OP, ['check', 'mutate'])
        self.assertEqual(MIXED_OP, self._latest())
        self.assertTrue(self._pending())

        self._set_op_state(MIXED_OP, 'complete')
        self.assertFalse(self._pending())

    def test_only_observational_ops_means_no_latest_and_nothing_pending(self):
        self._enqueue(OBSERVING_OP, ['check'])
        self.assertEqual('queued', self._op_state(OBSERVING_OP))
        self.assertIsNone(self._latest())
        self.assertFalse(self._pending())

        # The single-row reader is unfiltered and reports the flag.
        row = mariadb.get_cluster_operation_target(OBSERVING_OP)
        self.assertIsNotNone(row)
        self.assertTrue(row.observational)

    def test_by_target_listing_reports_every_op_with_its_flag(self):
        self._enqueue(MUTATING_OP, ['mutate'])
        self._enqueue(OBSERVING_OP, ['check'])
        self._enqueue(MIXED_OP, ['check', 'mutate'])

        ops = mariadb.list_cluster_operations_for_target(
            ObjectType.INSTANCE, INSTANCE_UUID)
        self.assertEqual(
            {MUTATING_OP: False, OBSERVING_OP: True, MIXED_OP: False},
            {op['uuid']: op['observational'] for op in ops})

    def test_pending_only_listing_filters_on_state_in_sql(self):
        ops = {
            'queued': '11112222-2222-4222-8222-000000000011',
            'executing': '11112222-2222-4222-8222-000000000012',
            'complete': '11112222-2222-4222-8222-000000000013',
            'error': '11112222-2222-4222-8222-000000000014',
        }
        for state, op_uuid in ops.items():
            self._enqueue(op_uuid, ['mutate'])
            if state != 'queued':
                self._set_op_state(op_uuid, state)

        everything = mariadb.get_cluster_operation_targets_for_object(
            ObjectType.INSTANCE, INSTANCE_UUID)
        self.assertEqual(list(ops.values()),
                         [r.operation_uuid for r in everything])

        pending = mariadb.get_cluster_operation_targets_for_object(
            ObjectType.INSTANCE, INSTANCE_UUID, pending_only=True)
        self.assertEqual([ops['queued'], ops['executing']],
                         [r.operation_uuid for r in pending])
        self.assertEqual(
            sorted(r.sequence_number for r in pending),
            [r.sequence_number for r in pending])

    def test_pending_only_listing_keeps_observational_ops(self):
        self._enqueue(OBSERVING_OP, ['check'])
        self.assertEqual('queued', self._op_state(OBSERVING_OP))

        rows = mariadb.get_cluster_operation_targets_for_object(
            ObjectType.INSTANCE, INSTANCE_UUID, pending_only=True)
        self.assertEqual(
            [(OBSERVING_OP, True)],
            [(r.operation_uuid, r.observational) for r in rows])

    # The tests below use the production models, so they fail if a
    # declaration is dropped or reclassified without this being noticed.

    def test_sweep_does_not_shadow_a_queued_instance_delete(self):
        sweep_tasks = [
            node_inst_op.model_tasks.collect_billing_statistics,
            node_inst_op.model_tasks.health_check_kvm_process]

        self._enqueue_inst(DELETE_OP, [node_inst_op.model_tasks.instance_delete])
        self._enqueue_inst(SWEEP_OP, sweep_tasks)

        # The pointer stays on the delete, which is pending.
        self.assertEqual(DELETE_OP, self._latest())
        self.assertTrue(self._pending())

        # Once the delete completes nothing is pending, although the sweep
        # is still queued.
        self._set_op_state(DELETE_OP, 'complete')
        self.assertEqual('queued', self._op_state(SWEEP_OP))
        self.assertFalse(self._pending())
        self.assertEqual(DELETE_OP, self._latest())

    def test_sweep_alone_leaves_no_pointer_and_nothing_pending(self):
        self._enqueue_inst(
            SWEEP_OP,
            [node_inst_op.model_tasks.collect_billing_statistics,
             node_inst_op.model_tasks.health_check_kvm_process])

        self.assertEqual('queued', self._op_state(SWEEP_OP))
        self.assertIsNone(self._latest())
        self.assertFalse(self._pending())

    def test_blob_checksum_op_is_observational_but_still_listed(self):
        self._enqueue_blob(
            OBSERVING_OP, [node_blob_op.model_tasks.verify_size_and_checksum])

        row = mariadb.get_cluster_operation_target(OBSERVING_OP)
        self.assertIsNotNone(row)
        self.assertEqual(ObjectType.BLOB.value, row.target_object_type)
        self.assertTrue(row.observational)

        # The blob sweep's duplicate guard reads this unfiltered listing.
        rows = mariadb.get_cluster_operation_targets_for_object(
            ObjectType.BLOB, BLOB_UUID)
        self.assertEqual(
            [(OBSERVING_OP, True)],
            [(r.operation_uuid, r.observational) for r in rows])

    def test_blob_ensure_local_op_is_mutating(self):
        self._enqueue_blob(
            MUTATING_OP, [node_blob_op.model_tasks.ensure_local])

        row = mariadb.get_cluster_operation_target(MUTATING_OP)
        self.assertIsNotNone(row)
        self.assertFalse(row.observational)


class ObservationalOnTheWireTestCase(base.ShakenFistTestCase):
    """The flag survives each proto hop on the read paths."""

    def _target(self, observational):
        return ClusterOperationTargetData(
            operation_uuid=MUTATING_OP,
            operation_type='node_inst_op',
            target_object_type=ObjectType.INSTANCE.value,
            target_uuid=INSTANCE_UUID,
            sequence_number=3,
            created_at=1000.0,
            observational=observational)

    def _servicer(self):
        servicer = database_main.DatabaseService.__new__(
            database_main.DatabaseService)
        servicer.monitor = mock.MagicMock()
        return servicer

    def test_target_round_trip(self):
        servicer = self._servicer()
        for observational in (True, False):
            proto = servicer._target_to_proto(self._target(observational))
            self.assertIs(observational, proto.observational)
            self.assertEqual(
                self._target(observational),
                mariadb._target_from_proto(proto))

    def test_target_from_older_service_is_mutating(self):
        proto = database_pb2.ClusterOperationTargetProto(
            operation_uuid=MUTATING_OP,
            operation_type='node_inst_op',
            target_object_type=ObjectType.INSTANCE.proto_id,
            target_uuid=INSTANCE_UUID,
            sequence_number=3,
            created_at=1000.0)
        self.assertFalse(mariadb._target_from_proto(proto).observational)

    def _listing(self, observational):
        op = {
            'uuid': MUTATING_OP,
            'operation_type': 'node_inst_op',
            'created_at': 1000.0,
            'tasks': ['check'],
            'observational': observational,
        }
        with mock.patch(
                'shakenfist.mariadb._direct_list_cluster_operations_for_target',
                return_value=[op]):
            reply = self._servicer().ListClusterOperationsForTarget(
                database_pb2.ListClusterOperationsForTargetRequest(
                    target_object_type=ObjectType.INSTANCE.proto_id,
                    target_uuid=INSTANCE_UUID),
                mock.MagicMock())
        self.assertEqual(1, len(reply.items))
        return reply.items[0]

    def test_listing_handler_sets_the_field(self):
        for observational in (True, False):
            item = self._listing(observational)
            self.assertTrue(item.HasField('observational'))
            self.assertIs(observational, item.observational)
            # The flag travels in its own field; metadata_json keeps the
            # shape the other cluster operation RPCs return.
            metadata = json.loads(item.metadata_json)
            self.assertNotIn('observational', metadata)
            self.assertEqual(['check'], metadata['tasks'])

    def test_other_rpcs_leave_the_field_unset(self):
        proto = self._servicer()._cluster_operation_to_proto({
            'uuid': MUTATING_OP,
            'operation_type': 'node_inst_op',
            'created_at': 1000.0,
        })
        self.assertFalse(proto.HasField('observational'))

    def test_listing_client_reads_the_field_only_when_set(self):
        reply = database_pb2.ListClusterOperationsForTargetReply(items=[
            database_pb2.ClusterOperationData(
                uuid=MUTATING_OP, operation_type='node_inst_op',
                created_at=2000.0,
                metadata_json=json.dumps({'uuid': MUTATING_OP}),
                observational=True),
            # An older database service never sets the field.
            database_pb2.ClusterOperationData(
                uuid=OBSERVING_OP, operation_type='node_inst_op',
                created_at=1000.0,
                metadata_json=json.dumps({'uuid': OBSERVING_OP})),
        ])
        with mock.patch('shakenfist.mariadb._get_database_stub'), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply):
            ops = mariadb._grpc_list_cluster_operations_for_target(
                ObjectType.INSTANCE, INSTANCE_UUID)
        self.assertEqual(
            [{'uuid': MUTATING_OP, 'observational': True},
             {'uuid': OBSERVING_OP}],
            ops)

    def test_listing_round_trip(self):
        # The daemon's reply, read by the client wrapper, gives back what
        # the direct function returned.
        item = self._listing(True)
        reply = database_pb2.ListClusterOperationsForTargetReply(items=[item])
        with mock.patch('shakenfist.mariadb._get_database_stub'), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply):
            ops = mariadb._grpc_list_cluster_operations_for_target(
                ObjectType.INSTANCE, INSTANCE_UUID)
        self.assertEqual(1, len(ops))
        self.assertIs(True, ops[0]['observational'])
        self.assertEqual(MUTATING_OP, str(UUID(ops[0]['uuid'])))

    def test_targets_wrapper_sends_pending_only(self):
        reply = database_pb2.GetClusterOperationTargetsForObjectReply()
        for pending_only in (True, False):
            with mock.patch('shakenfist.mariadb._get_database_stub'), \
                    mock.patch('shakenfist.mariadb._grpc_call',
                               return_value=reply) as call:
                mariadb._grpc_get_cluster_operation_targets_for_object(
                    ObjectType.INSTANCE, INSTANCE_UUID, pending_only)
            request = call.call_args.args[1]
            self.assertIs(pending_only, request.pending_only)
            self.assertEqual(INSTANCE_UUID, request.target_uuid)

    def test_targets_handler_forwards_pending_only(self):
        for pending_only in (True, False):
            with mock.patch(
                    'shakenfist.mariadb'
                    '._direct_get_cluster_operation_targets_for_object',
                    return_value=[self._target(False)]) as direct:
                reply = self._servicer().GetClusterOperationTargetsForObject(
                    database_pb2.GetClusterOperationTargetsForObjectRequest(
                        target_object_type=ObjectType.INSTANCE.proto_id,
                        target_uuid=INSTANCE_UUID,
                        pending_only=pending_only),
                    mock.MagicMock())
            direct.assert_called_once_with(
                ObjectType.INSTANCE, INSTANCE_UUID, pending_only)
            self.assertEqual(1, len(reply.targets))

    def test_targets_request_from_older_client_is_unfiltered(self):
        request = database_pb2.GetClusterOperationTargetsForObjectRequest(
            target_object_type=ObjectType.INSTANCE.proto_id,
            target_uuid=INSTANCE_UUID)
        self.assertFalse(request.pending_only)
