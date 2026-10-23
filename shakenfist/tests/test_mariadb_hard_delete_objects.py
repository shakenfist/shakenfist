# Copyright 2026 Michael Still and contributors

"""The batch hard delete: validation, routing, the wrappers and the servicer.

What the statements delete is shown against a real MariaDB by
test_mariadb_hard_delete_objects_live.py. These tests cover what does
not need one: which requests are refused, where a call goes, what the
transaction runs first, how each failure is reported, and that the
per-call fallback calls the same public functions the batch mirrors.
"""

from unittest import mock
from uuid import UUID
from uuid import uuid4

import grpc
from sqlalchemy.dialects.mysql.mariadb import MariaDBDialect
from sqlalchemy.exc import OperationalError

from shakenfist.daemons.database import main as daemons_database_main
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.protos import database_pb2
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


FINAL_STATES = ['deleted', 'error']
PER_OBJECT_TYPES = [ObjectType.INSTANCE, ObjectType.NAMESPACE,
                    ObjectType.NAMESPACE_CLAIM, ObjectType.NODE]


class FakeRpcError(grpc.RpcError):
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


def _uuids(count):
    return [str(uuid4()) for _ in range(count)]


# The dialect production uses. Only MariaDB's reports DELETE ... RETURNING.
DIALECT = MariaDBDialect()


def _sql(statement):
    return str(statement.compile(dialect=DIALECT))


def _bound(statement):
    """The values a statement binds, as strings."""
    compiled = statement.compile(
        dialect=DIALECT, compile_kwargs={'render_postcompile': True})
    return {str(value) for value in compiled.params.values()}


class MappingTestCase(base.ShakenFistTestCase):
    def test_every_name_has_a_statement_and_a_public_function(self):
        for object_type, names in mariadb.HARD_DELETE_BATCH_FUNCTIONS.items():
            self.assertTrue(names, object_type)
            for name in names:
                self.assertIn(name, mariadb.HARD_DELETE_STATEMENTS)
                self.assertTrue(callable(getattr(mariadb, name)), name)

    def test_every_statement_is_used(self):
        used = {name for names in mariadb.HARD_DELETE_BATCH_FUNCTIONS.values()
                for name in names}
        self.assertEqual(used, set(mariadb.HARD_DELETE_STATEMENTS))

    def test_per_object_types_are_not_batched(self):
        for object_type in PER_OBJECT_TYPES:
            self.assertNotIn(object_type, mariadb.HARD_DELETE_BATCH_FUNCTIONS)

    def test_base_deletes_are_not_in_the_mapping(self):
        # The state, metadata and event deletes are run for every type by
        # the batch itself, so a type naming them would run them twice.
        for name in ('delete_state', 'delete_object_metadata',
                     'delete_object_events'):
            self.assertNotIn(name, mariadb.HARD_DELETE_STATEMENTS)

    def test_builders_key_on_every_uuid(self):
        uuids = _uuids(3)
        for name, entry in mariadb.HARD_DELETE_STATEMENTS.items():
            statement = entry.build(ObjectType.ARTIFACT, uuids)
            sql = _sql(statement)
            self.assertTrue(sql.startswith('DELETE FROM '), name)
            self.assertIn(' IN (', sql, name)
            bound = _bound(statement)
            for uuid in uuids:
                self.assertIn(uuid, bound, name)


class RequestValidationTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch('shakenfist.mariadb._use_database_service')
        self.mock_route = patcher.start()
        self.addCleanup(patcher.stop)

    def _refused(self, *args, **kwargs):
        self.assertRaises(ValueError, mariadb.hard_delete_objects,
                          *args, **kwargs)
        self.mock_route.assert_not_called()

    def test_guarded_without_states_is_refused(self):
        self._refused(ObjectType.BLOB, _uuids(1), updated_before=1000.0)
        self._refused(ObjectType.BLOB, _uuids(1), state_values=[],
                      updated_before=1000.0)

    def test_guarded_without_an_age_is_refused(self):
        self._refused(ObjectType.BLOB, _uuids(1), state_values=FINAL_STATES)
        self._refused(ObjectType.BLOB, _uuids(1), state_values=FINAL_STATES,
                      updated_before=0)

    def test_unconditional_with_states_is_refused(self):
        self._refused(ObjectType.BLOB, _uuids(1), state_values=FINAL_STATES,
                      unconditional=True)

    def test_unconditional_with_an_age_is_refused(self):
        self._refused(ObjectType.BLOB, _uuids(1), updated_before=1000.0,
                      unconditional=True)

    def test_too_many_uuids_is_refused(self):
        self._refused(ObjectType.BLOB,
                      _uuids(mariadb.HARD_DELETE_OBJECTS_MAX + 1),
                      unconditional=True)

    def test_per_object_types_are_refused(self):
        for object_type in PER_OBJECT_TYPES:
            self._refused(object_type, _uuids(1), unconditional=True)

    def test_malformed_uuid_is_refused(self):
        self._refused(ObjectType.BLOB, ['not-a-uuid'], unconditional=True)

    def test_empty_list_is_no_call(self):
        self.assertEqual(
            [], mariadb.hard_delete_objects(
                ObjectType.BLOB, [], state_values=FINAL_STATES,
                updated_before=1000.0))
        self.assertEqual(
            [], mariadb.hard_delete_objects(
                ObjectType.BLOB, [], unconditional=True))
        self.mock_route.assert_not_called()

    def test_empty_list_still_needs_a_valid_mode(self):
        self._refused(ObjectType.BLOB, [])


class RoutingTestCase(base.ShakenFistTestCase):
    def test_guarded_routes_to_grpc(self):
        uuids = _uuids(2)
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=True), \
                mock.patch('shakenfist.mariadb._grpc_hard_delete_objects',
                           return_value=uuids[:1]) as mock_grpc:
            result = mariadb.hard_delete_objects(
                ObjectType.NODE_INST_OP, uuids, state_values=FINAL_STATES,
                updated_before=1000.0)

        self.assertEqual(uuids[:1], result)
        mock_grpc.assert_called_once_with(
            ObjectType.NODE_INST_OP, uuids, FINAL_STATES, 1000.0, False)

    def test_unconditional_routes_to_direct(self):
        uuids = _uuids(2)
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=False), \
                mock.patch('shakenfist.mariadb._direct_hard_delete_objects',
                           return_value=uuids) as mock_direct:
            result = mariadb.hard_delete_objects(
                ObjectType.UPLOAD, uuids, unconditional=True)

        self.assertEqual(uuids, result)
        mock_direct.assert_called_once_with(
            ObjectType.UPLOAD, uuids, [], None, True)

    def test_unavailable_propagates(self):
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=True), \
                mock.patch('shakenfist.mariadb._grpc_hard_delete_objects',
                           side_effect=exceptions.HardDeleteBatchUnavailable(
                               'old')):
            self.assertRaises(
                exceptions.HardDeleteBatchUnavailable,
                mariadb.hard_delete_objects, ObjectType.BLOB, _uuids(1),
                unconditional=True)


class CacheEvictionTestCase(base.ShakenFistTestCase):
    """The batch evicts what the public delete functions evict."""

    def _delete(self, object_type, uuids, deleted):
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=False), \
                mock.patch('shakenfist.mariadb._direct_hard_delete_objects',
                           return_value=deleted), \
                mock.patch('shakenfist.mariadb._object_cache_evict') as evict:
            mariadb.hard_delete_objects(
                object_type, uuids, state_values=FINAL_STATES,
                updated_before=1000.0)
        return evict

    def test_deleted_objects_are_evicted(self):
        uuids = _uuids(3)
        evict = self._delete(ObjectType.BLOB, uuids, uuids[:1])
        evict.assert_called_once_with('blob', uuids[0])

    def test_every_cache_a_type_uses_is_evicted(self):
        uuids = _uuids(1)
        evict = self._delete(ObjectType.AGENTOPERATION, uuids, uuids)
        evict.assert_called_once_with('agentoperation', uuids[0])

    def test_a_type_with_no_cache_evicts_nothing(self):
        uuids = _uuids(1)
        evict = self._delete(ObjectType.NAMESPACE_KEY, uuids, uuids)
        evict.assert_not_called()

    def test_failed_call_evicts_every_uuid_named(self):
        uuids = _uuids(2)
        evict = self._delete(ObjectType.UPLOAD, uuids, None)
        self.assertEqual(
            [mock.call('upload', uuid) for uuid in uuids],
            evict.call_args_list)


class GrpcWrapperTestCase(base.ShakenFistTestCase):
    def _call(self, *args, reply=None, side_effect=None):
        with mock.patch('shakenfist.mariadb._get_database_stub'), \
                mock.patch('shakenfist.mariadb._grpc_call', return_value=reply,
                           side_effect=side_effect) as mock_call:
            result = mariadb._grpc_hard_delete_objects(*args)
        return result, mock_call

    def test_guarded_request_and_reply(self):
        uuids = _uuids(3)
        result, mock_call = self._call(
            ObjectType.NET_OP, uuids, FINAL_STATES, 1000.0, False,
            reply=database_pb2.HardDeleteObjectsReply(
                deleted_uuids=uuids[1:]))

        request = mock_call.call_args[0][1]
        self.assertEqual(ObjectType.NET_OP.proto_id, request.object_type)
        self.assertEqual(uuids, list(request.object_uuids))
        self.assertEqual(FINAL_STATES, list(request.state_values))
        self.assertEqual(1000.0, request.updated_before)
        self.assertFalse(request.unconditional)
        self.assertEqual(uuids[1:], result)

    def test_unconditional_request(self):
        uuids = _uuids(1)
        result, mock_call = self._call(
            ObjectType.NET_OP, uuids, [], None, True,
            reply=database_pb2.HardDeleteObjectsReply(deleted_uuids=uuids))

        request = mock_call.call_args[0][1]
        self.assertTrue(request.unconditional)
        self.assertEqual([], list(request.state_values))
        self.assertEqual(0, request.updated_before)
        self.assertEqual(uuids, result)

    def test_nothing_eligible_is_an_empty_list_not_a_failure(self):
        result, _ = self._call(
            ObjectType.NET_OP, _uuids(1), FINAL_STATES, 1000.0, False,
            reply=database_pb2.HardDeleteObjectsReply())
        self.assertEqual([], result)

    @mock.patch('shakenfist.mariadb.LOG')
    def test_unimplemented_raises_unavailable(self, mock_log):
        self.assertRaises(
            exceptions.HardDeleteBatchUnavailable, self._call,
            ObjectType.NET_OP, _uuids(1), FINAL_STATES, 1000.0, False,
            side_effect=FakeRpcError(grpc.StatusCode.UNIMPLEMENTED))
        mock_log.warning.assert_called_once()

    @mock.patch('shakenfist.mariadb.LOG')
    def test_other_rpc_errors_are_a_failed_call(self, mock_log):
        for code in (grpc.StatusCode.ABORTED, grpc.StatusCode.INTERNAL,
                     grpc.StatusCode.INVALID_ARGUMENT):
            mock_log.reset_mock()
            result, _ = self._call(
                ObjectType.NET_OP, _uuids(1), FINAL_STATES, 1000.0, False,
                side_effect=FakeRpcError(code))
            self.assertIsNone(result, code)
            mock_log.error.assert_called_once()


class DirectTransactionTestCase(base.ShakenFistTestCase):
    """What the transaction runs, in what order, against a mocked engine."""

    def setUp(self):
        super().setUp()
        self.conn = mock.MagicMock()
        engine = mock.MagicMock()
        engine.begin.return_value.__enter__.return_value = self.conn
        patcher = mock.patch('shakenfist.mariadb._get_engine',
                             return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _returning(self, uuids):
        result = mock.MagicMock()
        result.fetchall.return_value = [
            mock.MagicMock(object_uuid=uuid) for uuid in uuids]
        return result

    def _statements(self):
        return [call.args[0] for call in self.conn.execute.call_args_list]

    def test_guarded_opens_with_the_guarded_state_delete(self):
        uuids = _uuids(3)
        self.conn.execute.return_value = self._returning(uuids[:1])

        result = mariadb._direct_hard_delete_objects(
            ObjectType.BLOB, uuids, FINAL_STATES, 1000.0, False)

        self.assertEqual(uuids[:1], result)
        statements = self._statements()
        first = _sql(statements[0])
        self.assertTrue(first.startswith('DELETE FROM object_states'), first)
        self.assertIn('object_states.object_type = ', first)
        self.assertIn('object_states.object_uuid IN', first)
        self.assertIn('object_states.state_value IN', first)
        self.assertIn('object_states.update_time < ', first)
        self.assertIn('RETURNING object_states.object_uuid', first)
        self.assertEqual(
            ['blob_hashes', 'blob_transfers', 'blob_attributes', 'blobs',
             'object_metadata', 'event_objects'],
            [statement.table.name for statement in statements[1:]])

    def test_later_statements_key_on_what_the_guard_returned(self):
        uuids = _uuids(3)
        self.conn.execute.return_value = self._returning(uuids[1:2])

        mariadb._direct_hard_delete_objects(
            ObjectType.UPLOAD, uuids, FINAL_STATES, 1000.0, False)

        for statement in self._statements()[1:]:
            bound = _bound(statement)
            self.assertIn(uuids[1], bound)
            self.assertNotIn(uuids[0], bound)
            self.assertNotIn(uuids[2], bound)

    def test_guarded_with_nothing_eligible_runs_nothing_else(self):
        self.conn.execute.return_value = self._returning([])

        result = mariadb._direct_hard_delete_objects(
            ObjectType.NODE_INST_OP, _uuids(2), FINAL_STATES, 1000.0, False)

        self.assertEqual([], result)
        self.assertEqual(1, self.conn.execute.call_count)

    def test_unconditional_deletes_every_uuid_named(self):
        uuids = _uuids(3)
        # No state row came back at all, as for objects with none.
        self.conn.execute.return_value = self._returning([])

        result = mariadb._direct_hard_delete_objects(
            ObjectType.NODE_INST_OP, uuids, [], None, True)

        self.assertEqual(uuids, result)
        statements = self._statements()
        first = _sql(statements[0])
        self.assertTrue(first.startswith('DELETE FROM object_states'), first)
        self.assertNotIn('state_value', first)
        self.assertNotIn('update_time', first)
        self.assertEqual(
            ['cluster_operation_targets', 'cluster_operation_errors',
             'cluster_operations', 'object_metadata', 'event_objects'],
            [statement.table.name for statement in statements[1:]])

    @mock.patch('shakenfist.mariadb.LOG')
    def test_operational_error_is_a_failed_call(self, mock_log):
        self.conn.execute.side_effect = OperationalError(
            'DELETE', {}, Exception(1054, 'Unknown column'))

        result = mariadb._direct_hard_delete_objects(
            ObjectType.BLOB, _uuids(1), FINAL_STATES, 1000.0, False)

        self.assertIsNone(result)
        mock_log.warning.assert_called_once()

    @mock.patch('shakenfist.mariadb.time.sleep')
    def test_deadlock_is_retried(self, mock_sleep):
        uuids = _uuids(1)
        returning = self._returning(uuids)
        self.conn.execute.side_effect = [
            OperationalError('DELETE', {}, Exception(1213, 'Deadlock')),
            returning, None, None, None, None, None, None]

        result = mariadb._direct_hard_delete_objects(
            ObjectType.BLOB, uuids, FINAL_STATES, 1000.0, False)

        self.assertEqual(uuids, result)


class PerCallTestCase(base.ShakenFistTestCase):
    def _calls(self, object_type, object_uuid):
        names = (list(mariadb.HARD_DELETE_STATEMENTS)
                 + ['delete_state', 'delete_object_metadata',
                    'delete_object_events'])
        manager = mock.MagicMock()
        patchers = [mock.patch(f'shakenfist.mariadb.{name}',
                               getattr(manager, name)) for name in names]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        mariadb.hard_delete_objects_per_call(object_type, object_uuid)
        return manager.mock_calls

    def test_cluster_operation(self):
        uuid = str(uuid4())
        self.assertEqual(
            [mock.call.delete_cluster_operation_target(uuid),
             mock.call.delete_cluster_operation_error(uuid),
             mock.call.delete_cluster_operation(uuid),
             mock.call.delete_state(ObjectType.NODE_INST_OP, uuid),
             mock.call.delete_object_metadata(ObjectType.NODE_INST_OP, uuid),
             mock.call.delete_object_events(ObjectType.NODE_INST_OP, uuid)],
            self._calls(ObjectType.NODE_INST_OP, uuid))

    def test_agent_operation(self):
        uuid = str(uuid4())
        self.assertEqual(
            [mock.call.delete_agent_operation_attributes(UUID(uuid)),
             mock.call.delete_agent_operation(UUID(uuid)),
             mock.call.remove_all_references_from(
                 ObjectType.AGENTOPERATION, uuid),
             mock.call.delete_state(ObjectType.AGENTOPERATION, uuid),
             mock.call.delete_object_metadata(
                 ObjectType.AGENTOPERATION, uuid),
             mock.call.delete_object_events(ObjectType.AGENTOPERATION, uuid)],
            self._calls(ObjectType.AGENTOPERATION, uuid))

    def test_network(self):
        uuid = str(uuid4())
        self.assertEqual(
            [mock.call.delete_network_attributes(UUID(uuid)),
             mock.call.delete_network(UUID(uuid)),
             mock.call.delete_cluster_operation_targets_for_object(
                 ObjectType.NETWORK, uuid),
             mock.call.delete_state(ObjectType.NETWORK, uuid),
             mock.call.delete_object_metadata(ObjectType.NETWORK, uuid),
             mock.call.delete_object_events(ObjectType.NETWORK, uuid)],
            self._calls(ObjectType.NETWORK, uuid))

    def test_every_type_calls_its_mapping_then_the_base_three(self):
        for object_type, names in mariadb.HARD_DELETE_BATCH_FUNCTIONS.items():
            uuid = str(uuid4())
            called = [call[0] for call in self._calls(object_type, uuid)]
            self.assertEqual(
                list(names) + ['delete_state', 'delete_object_metadata',
                               'delete_object_events'],
                called, object_type)

    def test_per_object_type_is_refused(self):
        for object_type in PER_OBJECT_TYPES:
            self.assertRaises(
                ValueError, mariadb.hard_delete_objects_per_call,
                object_type, str(uuid4()))


class ServicerTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())
        self.uuids = _uuids(2)
        self.request = database_pb2.HardDeleteObjectsRequest(
            object_type=ObjectType.BLOB.proto_id, object_uuids=self.uuids,
            state_values=FINAL_STATES, updated_before=1000.0)

    @mock.patch('shakenfist.mariadb.hard_delete_objects')
    def test_deleted_uuids_are_returned(self, mock_delete):
        mock_delete.return_value = self.uuids[:1]
        context = mock.MagicMock()

        reply = self.servicer.HardDeleteObjects(self.request, context)

        context.set_code.assert_not_called()
        self.assertEqual(self.uuids[:1], list(reply.deleted_uuids))
        mock_delete.assert_called_once_with(
            ObjectType.BLOB, self.uuids, state_values=FINAL_STATES,
            updated_before=1000.0, unconditional=False)

    @mock.patch('shakenfist.mariadb.hard_delete_objects')
    def test_unconditional_has_no_age(self, mock_delete):
        mock_delete.return_value = self.uuids
        context = mock.MagicMock()

        reply = self.servicer.HardDeleteObjects(
            database_pb2.HardDeleteObjectsRequest(
                object_type=ObjectType.BLOB.proto_id,
                object_uuids=self.uuids, unconditional=True),
            context)

        context.set_code.assert_not_called()
        self.assertEqual(self.uuids, list(reply.deleted_uuids))
        mock_delete.assert_called_once_with(
            ObjectType.BLOB, self.uuids, state_values=[],
            updated_before=None, unconditional=True)

    @mock.patch('shakenfist.mariadb.hard_delete_objects', return_value=[])
    def test_nothing_eligible_is_not_an_error(self, mock_delete):
        context = mock.MagicMock()

        reply = self.servicer.HardDeleteObjects(self.request, context)

        context.set_code.assert_not_called()
        self.assertEqual([], list(reply.deleted_uuids))

    @mock.patch('shakenfist.mariadb.hard_delete_objects', return_value=None)
    def test_failed_transaction_is_aborted(self, mock_delete):
        context = mock.MagicMock()

        self.servicer.HardDeleteObjects(self.request, context)

        context.set_code.assert_called_once_with(grpc.StatusCode.ABORTED)

    @mock.patch('shakenfist.mariadb.hard_delete_objects',
                side_effect=ValueError('boom'))
    def test_unexpected_exception_is_internal(self, mock_delete):
        context = mock.MagicMock()

        self.servicer.HardDeleteObjects(self.request, context)

        context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)

    def _assert_bad_request(self, request):
        context = mock.MagicMock()
        with mock.patch('shakenfist.mariadb.hard_delete_objects') as delete:
            self.servicer.HardDeleteObjects(request, context)
        context.set_code.assert_called_once_with(
            grpc.StatusCode.INVALID_ARGUMENT)
        delete.assert_not_called()

    def test_unknown_object_type_is_a_bad_request(self):
        self._assert_bad_request(database_pb2.HardDeleteObjectsRequest(
            object_type=0, object_uuids=self.uuids, unconditional=True))

    def test_per_object_type_is_a_bad_request(self):
        self._assert_bad_request(database_pb2.HardDeleteObjectsRequest(
            object_type=ObjectType.INSTANCE.proto_id,
            object_uuids=self.uuids, unconditional=True))

    def test_guarded_without_states_is_a_bad_request(self):
        self._assert_bad_request(database_pb2.HardDeleteObjectsRequest(
            object_type=ObjectType.BLOB.proto_id, object_uuids=self.uuids,
            updated_before=1000.0))

    def test_guarded_without_an_age_is_a_bad_request(self):
        self._assert_bad_request(database_pb2.HardDeleteObjectsRequest(
            object_type=ObjectType.BLOB.proto_id, object_uuids=self.uuids,
            state_values=FINAL_STATES))

    def test_unconditional_with_states_is_a_bad_request(self):
        self._assert_bad_request(database_pb2.HardDeleteObjectsRequest(
            object_type=ObjectType.BLOB.proto_id, object_uuids=self.uuids,
            state_values=FINAL_STATES, unconditional=True))

    def test_too_many_uuids_is_a_bad_request(self):
        self._assert_bad_request(database_pb2.HardDeleteObjectsRequest(
            object_type=ObjectType.BLOB.proto_id,
            object_uuids=_uuids(mariadb.HARD_DELETE_OBJECTS_MAX + 1),
            unconditional=True))
