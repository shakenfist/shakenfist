# Tests for the mariadb blob transfer functions.
#
# This module tests:
# - _direct_create_blob_transfer() creating transfer records
# - _direct_get_blob_transfer() retrieving a specific transfer
# - _direct_update_blob_transfer() updating transfer state/port/percentage
# - _direct_delete_blob_transfer() deleting transfer records
# - _cleanup_etcd_blob_transfers() cleanup function

from unittest import mock

from pydantic import SecretStr
import sqlalchemy as sa

from shakenfist import mariadb
from shakenfist.config import BaseSettings
from shakenfist.protos import database_pb2
from shakenfist.schema.blob_transfer import BlobTransfer
from shakenfist.schema.sqlalchemy import pydantic_to_sqlalchemy_table
from shakenfist.tests import base


class FakeConfig(BaseSettings):
    MARIADB_GATEWAY_HOSTS: list[str] = ['192.168.1.1']
    MARIADB_GATEWAY_PORT: int = 13005
    MARIADB_HOST: str = 'localhost'
    NODE_NAME: str = 'testnode'


fake_config = FakeConfig()


class CreateBlobTransferTestCase(base.ShakenFistTestCase):
    """Tests for _direct_create_blob_transfer() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_blob_transfer_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        transfer = BlobTransfer(
            source_node='sf-1',
            transfer_name='test-transfer-123',
            requesting_node='192.168.1.100',
            blob_uuid='blob-uuid-456',
            token='auth-token-789',
            server_state='initial',
            port=None,
            percentage=0.0,
            created_at=1234567890.0,
            updated_at=1234567890.0
        )

        result = mariadb._direct_create_blob_transfer(transfer)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_create_blob_transfer_error(self, mock_get_engine):
        from sqlalchemy.exc import OperationalError
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('DB error'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        transfer = BlobTransfer(
            source_node='sf-1',
            transfer_name='test-transfer-123',
            requesting_node='192.168.1.100',
            blob_uuid='blob-uuid-456',
            token='auth-token-789',
            server_state='initial',
            port=None,
            percentage=0.0,
            created_at=1234567890.0,
            updated_at=1234567890.0
        )

        result = mariadb._direct_create_blob_transfer(transfer)

        self.assertFalse(result)


class UpdateBlobTransferTestCase(base.ShakenFistTestCase):
    """Tests for _direct_update_blob_transfer() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('time.time')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_server_state_and_port(self, mock_get_engine, mock_time):
        mock_time.return_value = 1234567890.0
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_update_blob_transfer(
            'sf-1', 'test-transfer-123',
            server_state='created', port=12345)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('time.time')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_update_percentage(self, mock_get_engine, mock_time):
        mock_time.return_value = 1234567890.0
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_update_blob_transfer(
            'sf-1', 'test-transfer-123', percentage=50.5)

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()

    def test_update_no_fields(self):
        result = mariadb._direct_update_blob_transfer(
            'sf-1', 'test-transfer-123')

        self.assertFalse(result)


class DeleteBlobTransferTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_blob_transfer() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_blob_transfer_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 1
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_blob_transfer('sf-1', 'test-transfer')

        self.assertTrue(result)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_nonexistent_transfer(self, mock_get_engine):
        # delete returns True even if record doesn't exist (idempotent delete)
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 0
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_blob_transfer('sf-1', 'missing')

        self.assertTrue(result)


class DeleteStaleTransfersTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_stale_transfers() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_stale_transfers(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 3
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        # older_than is now a timestamp, not max_age
        older_than = 1234567290.0
        result = mariadb._direct_delete_stale_transfers(older_than)

        self.assertEqual(result, 3)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()


class DeleteBlobTransfersForBlobTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_blob_transfers_for_blob() function."""

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_transfers_for_blob_success(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 2
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_blob_transfers_for_blob('blob-uuid-123')

        self.assertEqual(result, 2)
        mock_conn.execute.assert_called_once()
        mock_conn.commit.assert_called_once()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_transfers_for_blob_none_exist(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_result = mock.MagicMock()
        mock_result.rowcount = 0
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_blob_transfers_for_blob('blob-uuid-123')

        # 0 is a valid success case (no transfers existed)
        self.assertEqual(result, 0)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_delete_transfers_for_blob_error(self, mock_get_engine):
        from sqlalchemy.exc import OperationalError
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_conn.execute.side_effect = OperationalError(
            'statement', {}, Exception('DB error'))
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine

        result = mariadb._direct_delete_blob_transfers_for_blob('blob-uuid-123')

        # -1 indicates error
        self.assertEqual(result, -1)


class BlobTransferExternalViewTestCase(base.ShakenFistTestCase):
    """external_view() must not carry the transfer's authorisation token.

    Every caller puts the result somewhere a credential must not go: two
    audit events in blob.py, and the log fields in
    daemons/transfers/main.py. Events reach MariaDB and the log stream,
    and the log stream ships to Loki, so while the token was included a
    live credential left the cluster on every blob transfer. Found by a
    sweep for secret-carrying fields. The field is a SecretStr as well
    now (issue 4961), so this omission and the type each cover what the
    other does not -- see BlobTransferTokenTypeTestCase below.
    """

    def _transfer(self):
        return BlobTransfer(
            source_node='sf-1',
            transfer_name='test-transfer-123',
            requesting_node='192.168.1.100',
            blob_uuid='blob-uuid-456',
            token='auth-token-789',
            server_state='initial',
            port=None,
            percentage=0.0,
            created_at=1234567890.0,
            updated_at=1234567890.0
        )

    def test_external_view_omits_the_token(self):
        view = self._transfer().external_view()

        self.assertNotIn('token', view)
        self.assertNotIn('auth-token-789', str(view))

    def test_external_view_keeps_the_diagnostic_fields(self):
        # The events and log lines this feeds are how a transfer is
        # debugged, so removing the credential must not have cost the
        # fields which make the rest of it useful.
        view = self._transfer().external_view()

        for field in ['source_node', 'transfer_name', 'requesting_node',
                      'blob_uuid', 'server_state', 'port', 'percentage',
                      'created_at', 'updated_at']:
            self.assertIn(field, view)

    def test_the_token_is_still_reachable_on_the_model(self):
        # The transfers daemon authenticates the inbound connection with
        # it, so removing it from the serialised view must not have made
        # it unavailable where it is actually needed. Unwrapped
        # deliberately: a SecretStr never compares equal to a str, so
        # the un-unwrapped spelling of this assertion would pass while
        # checking nothing.
        self.assertEqual(
            'auth-token-789', self._transfer().token.get_secret_value())


class BlobTransferTokenTypeTestCase(base.ShakenFistTestCase):
    """The transfer token is a SecretStr, not a plain str.

    Holding the token is enough to read the blob a transfer is sending,
    so it is a bearer credential. It escaped once already, into two
    audit events and the transfers daemon's log fields, and that was
    closed by removing it from external_view(). The type is what stops
    the next one: an accidental f-string or extra={} renders
    '**********' instead of the credential. Issue 4961.
    """

    def _transfer(self, token='auth-token-789'):
        return BlobTransfer(
            source_node='sf-1',
            transfer_name='test-transfer-123',
            requesting_node='192.168.1.100',
            blob_uuid='blob-uuid-456',
            token=token,
            server_state='initial',
            port=None,
            percentage=0.0,
            created_at=1234567890.0,
            updated_at=1234567890.0
        )

    def test_a_plain_str_is_wrapped_on_the_way_in(self):
        # Every writer hands the model a str, from sf_random.random_id()
        # or from a database row, and the model is where it becomes a
        # secret.
        self.assertIsInstance(self._transfer().token, SecretStr)

    def test_stringifying_the_token_redacts_it(self):
        transfer = self._transfer()
        secret = transfer.token.get_secret_value()

        for rendering in [str(transfer.token), repr(transfer.token),
                          f'{transfer.token}', '%s' % transfer.token,
                          str(transfer), repr(transfer)]:
            self.assertNotIn(secret, rendering)

    def test_serialising_the_model_redacts_the_token(self):
        # A caller which serialises the model itself rather than calling
        # external_view() -- a future event, a debug dump -- gets the
        # redaction from the type.
        transfer = self._transfer()

        self.assertNotIn(
            transfer.token.get_secret_value(), transfer.model_dump_json())

    def test_the_declared_length_limit_survived_the_wrapping(self):
        from pydantic import ValidationError

        # The column is VARCHAR(255) but the field declares 64, which is
        # what the minted token actually needs. Wrapping a field must
        # not quietly widen it.
        self.assertRaises(ValidationError, self._transfer, 'x' * 65)

    def test_the_generated_column_is_unchanged(self):
        # SecretStr maps to the same String(255) a str does, so this is
        # a Python-side change with no migration. If that mapping were
        # lost the column would become LONGTEXT on fresh installs only,
        # with no schema version change to notice it.
        table = pydantic_to_sqlalchemy_table(
            BlobTransfer, 'blob_transfers_test', sa.MetaData(),
            primary_key_fields=['source_node', 'transfer_name'])

        self.assertIsInstance(table.c.token.type, sa.String)
        self.assertEqual(255, table.c.token.type.length)


class BlobTransferTokenUnwrapBoundaryTestCase(base.ShakenFistTestCase):
    """The plaintext token crosses exactly the boundaries it must.

    SQL and protobuf both take strings, so each of those encoders is an
    unwrap site, and each read is a wrap site. A missed unwrap stores
    or sends the literal '**********' -- which authenticates nothing --
    and a missed wrap hands a plain str back to callers which expect to
    unwrap it.
    """

    def setUp(self):
        super().setUp()
        self.config = mock.patch('shakenfist.mariadb.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    def _transfer(self):
        return BlobTransfer(
            source_node='sf-1',
            transfer_name='test-transfer-123',
            requesting_node='192.168.1.100',
            blob_uuid='blob-uuid-456',
            token=SecretStr('auth-token-789'),
            server_state='initial',
            port=None,
            percentage=0.0,
            created_at=1234567890.0,
            updated_at=1234567890.0
        )

    def _row(self):
        row = mock.MagicMock()
        row.source_node = 'sf-1'
        row.transfer_name = 'test-transfer-123'
        row.requesting_node = '192.168.1.100'
        row.blob_uuid = 'blob-uuid-456'
        row.token = 'auth-token-789'
        row.server_state = 'initial'
        row.port = None
        row.percentage = 0.0
        row.created_at = 1234567890.0
        row.updated_at = 1234567890.0
        return row

    def _conn(self, mock_get_engine):
        mock_engine = mock.MagicMock()
        mock_conn = mock.MagicMock()
        mock_engine.connect.return_value.__enter__ = mock.Mock(
            return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = mock.Mock(
            return_value=False)
        mock_get_engine.return_value = mock_engine
        return mock_conn

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_the_sql_write_stores_the_plaintext(self, mock_get_engine):
        mock_conn = self._conn(mock_get_engine)

        self.assertTrue(
            mariadb._direct_create_blob_transfer(self._transfer()))

        stmt = mock_conn.execute.call_args.args[0]
        self.assertEqual(
            'auth-token-789', stmt.compile().params['token'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_the_sql_read_wraps_the_column(self, mock_get_engine):
        mock_conn = self._conn(mock_get_engine)
        mock_conn.execute.return_value.fetchone.return_value = self._row()

        transfer = mariadb._direct_get_blob_transfer('sf-1', 't1')

        self.assertIsInstance(transfer.token, SecretStr)
        self.assertEqual('auth-token-789', transfer.token.get_secret_value())

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_the_list_reads_wrap_the_column(self, mock_get_engine):
        mock_conn = self._conn(mock_get_engine)
        mock_conn.execute.return_value = [self._row()]

        for lookup in [mariadb._direct_get_blob_transfers_for_node,
                       mariadb._direct_get_blob_transfers_for_blob]:
            transfers = lookup('sf-1')
            self.assertEqual(1, len(transfers))
            self.assertEqual(
                'auth-token-789', transfers[0].token.get_secret_value())

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_the_grpc_encoder_sends_the_plaintext(self, mock_stub):
        # protobuf refuses a SecretStr outright, so a missed unwrap here
        # would raise rather than ship asterisks -- but the assertion is
        # still worth having, because the fix for such a raise is an
        # unwrap and not a str().
        with mock.patch('shakenfist.mariadb._grpc_call') as mock_call:
            mock_call.return_value = mock.MagicMock(success=True)
            self.assertTrue(
                mariadb._grpc_create_blob_transfer(self._transfer()))

        request = mock_call.call_args.args[1]
        self.assertEqual('auth-token-789', request.transfer.token)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_the_grpc_decoder_wraps_the_reply(self, mock_stub):
        reply = mock.MagicMock()
        reply.found = True
        reply.transfer = database_pb2.BlobTransferData(
            source_node='sf-1',
            transfer_name='test-transfer-123',
            requesting_node='192.168.1.100',
            blob_uuid='blob-uuid-456',
            token='auth-token-789',
            server_state='initial',
            port=0,
            percentage=0.0,
            created_at=1234567890.0,
            updated_at=1234567890.0
        )

        with mock.patch('shakenfist.mariadb._grpc_call', return_value=reply):
            transfer = mariadb._grpc_get_blob_transfer('sf-1', 't1')

        self.assertIsInstance(transfer.token, SecretStr)
        self.assertEqual('auth-token-789', transfer.token.get_secret_value())
