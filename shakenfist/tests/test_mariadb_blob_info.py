# Copyright 2026 Michael Still and contributors
#
# Direct SQL tests for the blobs-with-info query (issue #4965).
#
# observe_local_blobs() reads this once per pass to decide which local
# blobs it can skip hydrating, so what matters is which rows the
# statement matches -- not that it has the right shape. These run it
# against a real (sqlite) database built from mariadb.py's own table
# definition, with the MariaDB JSON function shims the fixture already
# provides for the coalescing queries.
#
# An attributes row is created with an empty info dict and filled the
# first time the blob is observed, so "has info" is a JSON_LENGTH
# question rather than a NULL one, and getting that wrong would skip the
# backfill for every blob which has never been identified.

import uuid as uuid_module
from unittest import mock

import grpc
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.protos import database_pb2
from shakenfist.tests import base
from shakenfist.tests import dbfixture


WITH_INFO = '11111111-1111-4111-8111-111111111111'
EMPTY_INFO = '22222222-2222-4222-8222-222222222222'
NULL_INFO = '33333333-3333-4333-8333-333333333333'
NO_ROW = '44444444-4444-4444-8444-444444444444'


class BlobAttributesTables(dbfixture.MariaDBTableFixture):
    """The blob_attributes table, and a way to fill it."""

    def _build_engine(self):
        return self.build_engine(
            [mariadb._get_blob_attributes_table], json_shims=True)

    def _insert(self, conn, blob_uuid, info):
        # The uuid goes in as a UUID object, which is what the sa.Uuid
        # bind processor production writes through expects; it is stored
        # undashed.
        table = mariadb._get_blob_attributes_table()
        conn.execute(table.insert().values(
            uuid=uuid_module.UUID(blob_uuid), size=1024, info=info,
            last_used=None, expires_at=0.0))


class BlobUuidsWithInfoSQLTestCase(BlobAttributesTables,
                                   base.ShakenFistTestCase):
    def test_only_a_blob_with_info_is_named(self):
        engine = self._build_engine()
        with engine.connect() as conn:
            self._insert(conn, WITH_INFO, {'virtual size': 1024,
                                           'mime-type': 'application/x-qemu'})
            self._insert(conn, EMPTY_INFO, {})
            self._insert(conn, NULL_INFO, None)
            conn.commit()

        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            self.assertEqual(
                [WITH_INFO], mariadb._direct_get_blob_uuids_with_info())

    def test_a_blob_with_no_attributes_row_is_not_named(self):
        # Absence has to mean "hydrate it": a blob with no attributes row
        # has no info either, and observe() is what creates the row.
        engine = self._build_engine()
        with engine.connect() as conn:
            self._insert(conn, WITH_INFO, {'virtual size': 1024})
            conn.commit()

        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            found = mariadb._direct_get_blob_uuids_with_info()

        self.assertNotIn(NO_ROW, found)

    def test_the_uuids_are_the_dashed_form(self):
        # The caller intersects these with blob file names and with the
        # reference rows, both of which are dashed. The column stores the
        # undashed form, so this is the trap described in
        # test_mariadb_coalescing.py by another route.
        engine = self._build_engine()
        with engine.connect() as conn:
            self._insert(conn, WITH_INFO, {'virtual size': 1024})
            conn.commit()

            stored = conn.execute(
                sa.text('select uuid from blob_attributes')).scalar()
        self.assertEqual(WITH_INFO.replace('-', ''), stored)

        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            self.assertEqual(
                [WITH_INFO], mariadb._direct_get_blob_uuids_with_info())

    def test_a_failed_read_is_none_not_empty(self):
        # [] means "no blob has info", which the caller acts on by
        # skipping no hydration at all. The two must not be the same
        # value: see the comment in observe_local_blobs().
        engine = mock.MagicMock()
        engine.connect.side_effect = OperationalError(
            'stmt', {}, Exception())

        with mock.patch('shakenfist.mariadb._get_engine',
                        return_value=engine):
            self.assertIsNone(mariadb._direct_get_blob_uuids_with_info())


class BlobUuidsWithInfoClientTestCase(base.ShakenFistTestCase):
    """The client half of the read, including what a failure returns."""

    @mock.patch('shakenfist.mariadb._grpc_call',
                side_effect=grpc.RpcError())
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_failed_rpc_is_none(self, mock_stub, mock_call):
        self.assertIsNone(mariadb._grpc_get_blob_uuids_with_info())

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_the_reply_is_returned_as_a_list(self, mock_stub):
        reply = database_pb2.GetBlobUuidsWithInfoReply(
            uuids=[WITH_INFO, EMPTY_INFO])
        with mock.patch('shakenfist.mariadb._grpc_call',
                        return_value=reply):
            self.assertEqual([WITH_INFO, EMPTY_INFO],
                             mariadb._grpc_get_blob_uuids_with_info())

    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_the_tier_is_used_when_one_is_configured(self, mock_use):
        with mock.patch('shakenfist.mariadb._grpc_get_blob_uuids_with_info',
                        return_value=[WITH_INFO]) as mock_grpc:
            with mock.patch(
                    'shakenfist.mariadb._direct_get_blob_uuids_with_info'
            ) as mock_direct:
                self.assertEqual([WITH_INFO],
                                 mariadb.get_blob_uuids_with_info())

        mock_grpc.assert_called_once_with()
        mock_direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_direct_access_is_used_without_a_tier(self, mock_use):
        with mock.patch('shakenfist.mariadb._grpc_get_blob_uuids_with_info'
                        ) as mock_grpc:
            with mock.patch(
                    'shakenfist.mariadb._direct_get_blob_uuids_with_info',
                    return_value=None) as mock_direct:
                # None is passed through rather than flattened: the
                # caller decides what a failed read means.
                self.assertIsNone(mariadb.get_blob_uuids_with_info())

        mock_direct.assert_called_once_with()
        mock_grpc.assert_not_called()
