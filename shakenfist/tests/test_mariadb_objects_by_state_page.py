# Copyright 2026 Michael Still and contributors

"""The client side of paging GetObjectsByState.

Two properties matter more than the paging itself. The unpaged wrapper
must keep sending page_size 0, because get_active_blob_uuids() treats
its result as a complement set and a page reaching it would delete
blobs. And the paged wrapper must survive an sf-database which predates
the paging fields: such a server ignores them and answers with every
match, unordered, which must not be trimmed into a page and used as a
keyset cursor.
"""

from unittest import mock

import grpc

from shakenfist import mariadb
from shakenfist.protos import database_pb2
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class FakeRpcError(grpc.RpcError):
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


class UnpagedWrapperTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb._grpc_call')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_unpaged_read_sends_page_size_zero(self, mock_stub, mock_call):
        mock_call.return_value = database_pb2.GetObjectsByStateReply(
            object_uuids=['uuid-1'])

        result = mariadb._grpc_get_objects_by_state(
            ObjectType.BLOB, ['created'])

        request = mock_call.call_args[0][1]
        self.assertEqual(0, request.page_size)
        self.assertEqual('', request.after_uuid)
        self.assertEqual(['uuid-1'], result)


class PagedWrapperTestCase(base.ShakenFistTestCase):
    def _call(self, reply, **kwargs):
        with mock.patch('shakenfist.mariadb._get_database_stub'), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           return_value=reply) as mock_call:
            result = mariadb._grpc_get_objects_by_state_page(
                ObjectType.BLOB, ['deleted'], 2, **kwargs)
        return result, mock_call.call_args[0][1]

    def test_fields_are_passed_through(self):
        result, request = self._call(
            database_pb2.GetObjectsByStateReply(
                object_uuids=['uuid-2', 'uuid-3'], more_available=True),
            after_uuid='uuid-1', updated_before=1000.0)

        self.assertEqual(ObjectType.BLOB.proto_id, request.object_type)
        self.assertEqual(['deleted'], list(request.state_values))
        self.assertEqual(2, request.page_size)
        self.assertEqual('uuid-1', request.after_uuid)
        self.assertEqual(1000.0, request.updated_before)
        self.assertEqual(
            mariadb.ObjectsByStatePage(
                uuids=['uuid-2', 'uuid-3'], more_available=True),
            result)

    def test_defaults_start_from_the_beginning_without_an_age_filter(self):
        result, request = self._call(
            database_pb2.GetObjectsByStateReply(object_uuids=['uuid-1']))

        self.assertEqual('', request.after_uuid)
        self.assertEqual(0, request.updated_before)
        self.assertEqual(
            mariadb.ObjectsByStatePage(uuids=['uuid-1'],
                                       more_available=False),
            result)

    def test_long_reply_from_an_old_server_is_returned_whole(self):
        # An older sf-database ignores page_size and returns every match
        # in no particular order. Trimming that to two would build a
        # cursor on an unordered list, so the whole list comes back as a
        # complete walk.
        result, _ = self._call(
            database_pb2.GetObjectsByStateReply(
                object_uuids=['uuid-9', 'uuid-1', 'uuid-5']))

        self.assertEqual(
            mariadb.ObjectsByStatePage(
                uuids=['uuid-9', 'uuid-1', 'uuid-5'], more_available=False),
            result)

    @mock.patch('shakenfist.mariadb.LOG')
    def test_rpc_error_is_a_failed_read(self, mock_log):
        with mock.patch('shakenfist.mariadb._get_database_stub'), \
                mock.patch('shakenfist.mariadb._grpc_call',
                           side_effect=FakeRpcError(
                               grpc.StatusCode.INTERNAL)):
            result = mariadb._grpc_get_objects_by_state_page(
                ObjectType.BLOB, ['deleted'], 2)

        self.assertIsNone(result)
        mock_log.error.assert_called_once()


class PublicPagedReadTestCase(base.ShakenFistTestCase):
    def test_page_size_zero_is_rejected(self):
        # 0 is the wire value for an unbounded read, which belongs to
        # get_objects_by_state().
        with mock.patch('shakenfist.mariadb._use_database_service') as svc:
            self.assertRaises(
                ValueError, mariadb.get_objects_by_state_page,
                ObjectType.BLOB, ['deleted'], 0)
        svc.assert_not_called()

    def test_negative_page_size_is_rejected(self):
        self.assertRaises(
            ValueError, mariadb.get_objects_by_state_page,
            ObjectType.BLOB, ['deleted'], -1)

    def test_routes_to_grpc(self):
        page = mariadb.ObjectsByStatePage(uuids=[], more_available=False)
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=True), \
                mock.patch('shakenfist.mariadb._grpc_get_objects_by_state_page',
                           return_value=page) as mock_grpc:
            result = mariadb.get_objects_by_state_page(
                ObjectType.BLOB, ['deleted'], 5, after_uuid='uuid-1',
                updated_before=1000.0)

        self.assertIs(page, result)
        mock_grpc.assert_called_once_with(
            ObjectType.BLOB, ['deleted'], 5, after_uuid='uuid-1',
            updated_before=1000.0)

    def test_routes_to_direct(self):
        page = mariadb.ObjectsByStatePage(uuids=[], more_available=False)
        with mock.patch('shakenfist.mariadb._use_database_service',
                        return_value=False), \
                mock.patch(
                    'shakenfist.mariadb._direct_get_objects_by_state_page',
                    return_value=page) as mock_direct:
            result = mariadb.get_objects_by_state_page(
                ObjectType.BLOB, ['deleted'], 5)

        self.assertIs(page, result)
        mock_direct.assert_called_once_with(
            ObjectType.BLOB, ['deleted'], 5, after_uuid='',
            updated_before=None)
