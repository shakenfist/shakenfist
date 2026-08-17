# Tests for Blob class and related functionality.
#
# This module tests:
# - BlobData Pydantic model
# - Blob class initialization and methods
# - upgrade_pydantic_data() in DatabaseBackedObject
# - _persist_pydantic_upgrade() override in Blob
# - mariadb blob functions

import os
import tempfile
from typing import Any
from unittest import mock
from uuid import UUID

from pydantic import ValidationError
import testtools

from shakenfist.config import BaseSettings
from shakenfist.exceptions import HashFailed
from shakenfist.schema.blob_data import BlobData
from shakenfist.schema.object_state import State
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist import blob
from shakenfist import exceptions
from shakenfist import mariadb


class FakeConfig(BaseSettings):
    STORAGE_PATH: str = '/srv/shakenfist'


class BlobDataTestCase(base.ShakenFistTestCase):
    """Tests for the BlobData Pydantic model."""

    def test_create_from_kwargs(self):
        """Test creating BlobData from keyword arguments."""
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=10
        )
        self.assertEqual(str(data.uuid), '12345678-1234-4321-8234-123456789012')
        self.assertEqual(data.modified, 1234567890.0)
        self.assertEqual(data.fetched_at, 1234567891.0)
        self.assertEqual(data.version, 10)

    def test_create_from_uuid_object(self):
        """Test creating BlobData with a UUID object."""
        uuid_obj = UUID('12345678-1234-4321-8234-123456789012')
        data = BlobData(
            uuid=uuid_obj,
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=10
        )
        self.assertEqual(data.uuid, uuid_obj)

    def test_immutable(self):
        """Test that BlobData is immutable (frozen)."""
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=10
        )
        with testtools.ExpectedException(ValidationError):
            data.modified = 9999999999.0

    def test_invalid_uuid(self):
        """Test that invalid UUID raises ValidationError."""
        with testtools.ExpectedException(ValidationError):
            BlobData(
                uuid='not-a-uuid',
                modified=1234567890.0,
                fetched_at=1234567891.0,
                version=10
            )

    def test_model_dump(self):
        """Test that model_dump() produces expected output."""
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=10
        )
        dumped = data.model_dump()
        self.assertEqual(dumped['modified'], 1234567890.0)
        self.assertEqual(dumped['fetched_at'], 1234567891.0)
        self.assertEqual(dumped['version'], 10)
        # UUID is a UUID object in the dump
        self.assertEqual(
            str(dumped['uuid']), '12345678-1234-4321-8234-123456789012')


class UpgradePydanticDataBlobTestCase(base.ShakenFistTestCase):
    """Tests for the upgrade_pydantic_data() method with Blob."""

    def test_no_upgrade_needed(self):
        """Test that data at current version is returned unchanged."""
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=blob.Blob.current_version
        )

        result = blob.Blob.upgrade_pydantic_data(data, BlobData)

        # Should be the same object (no upgrade needed)
        self.assertIs(result, data)

    @mock.patch('shakenfist.baseobject.get_minimum_object_version')
    def test_upgrade_applies_steps(self, mock_get_min_version):
        """Test that upgrade steps are applied sequentially."""
        # Set cluster not ready so we don't try to persist
        mock_get_min_version.return_value = 1

        # Create a test subclass with upgrade steps
        class TestBlobWithUpgrade(blob.Blob):
            initial_version = 1
            current_version = 3

            @classmethod
            def _upgrade_step_1_to_2(cls, values: dict[str, Any]) -> None:
                values['modified'] = values['modified'] + 100.0

            @classmethod
            def _upgrade_step_2_to_3(cls, values: dict[str, Any]) -> None:
                values['modified'] = values['modified'] + 200.0

        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=1
        )

        result = TestBlobWithUpgrade.upgrade_pydantic_data(data, BlobData)

        # Should be a new object
        self.assertIsNot(result, data)
        # Version should be updated
        self.assertEqual(result.version, 3)
        # Upgrade steps should have been applied (100 + 200 = 300)
        self.assertEqual(result.modified, 1234567890.0 + 300.0)

    @mock.patch('shakenfist.baseobject.get_minimum_object_version')
    @mock.patch('shakenfist.mariadb.update_blob')
    def test_upgrade_persists_when_cluster_ready(self, mock_update, mock_get_min):
        """Test that upgrade is persisted when cluster min == current version."""
        mock_get_min.return_value = blob.Blob.current_version
        mock_update.return_value = True

        # Create data at an older version
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=blob.Blob.current_version - 1
        )

        # Create a subclass with an upgrade step
        class TestBlob(blob.Blob):
            current_version = blob.Blob.current_version

            @classmethod
            def _upgrade_step_10_to_11(cls, values: dict[str, Any]) -> None:
                # No-op upgrade step
                pass

        TestBlob.initial_version = blob.Blob.current_version - 1

        TestBlob.upgrade_pydantic_data(data, BlobData)

        # Should have called update_blob with the upgraded data
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0][0]
        self.assertEqual(call_args.version, blob.Blob.current_version)


class BlobClassTestCase(base.ShakenFistTestCase):
    """Tests for the Blob class."""

    @mock.patch('shakenfist.baseobject.get_minimum_object_version',
                return_value=blob.Blob.current_version)
    @mock.patch('shakenfist.mariadb.get_state',
                return_value=State(value='created', update_time=1234567890.0))
    def test_init_from_blob_data(self, mock_get_state, mock_get_min):
        """Test creating Blob from BlobData."""
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=blob.Blob.current_version
        )

        b = blob.Blob(data)

        self.assertEqual(str(b.uuid), '12345678-1234-4321-8234-123456789012')
        self.assertEqual(b.modified, 1234567890.0)
        self.assertEqual(b.fetched_at, 1234567891.0)
        self.assertEqual(b.version, blob.Blob.current_version)

    @mock.patch('shakenfist.baseobject.get_minimum_object_version',
                return_value=blob.Blob.current_version)
    @mock.patch('shakenfist.mariadb.update_blob', return_value=True)
    @mock.patch('shakenfist.mariadb.get_state',
                return_value=State(value='created', update_time=1234567890.0))
    def test_persist_pydantic_upgrade_called(
            self, mock_get_state, mock_update, mock_get_min):
        """Test that _persist_pydantic_upgrade is called during upgrade."""
        # Create data at an older version
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=blob.Blob.current_version - 1
        )

        # Temporarily modify Blob to have a simple upgrade step
        original_version = blob.Blob.initial_version
        blob.Blob.initial_version = blob.Blob.current_version - 1

        try:
            # Define the missing upgrade step
            def upgrade_step(cls, values):
                pass
            step_name = '_upgrade_step_%d_to_%d' % (
                blob.Blob.current_version - 1,
                blob.Blob.current_version
            )
            setattr(blob.Blob, step_name, classmethod(upgrade_step))

            # Create the blob - this should trigger upgrade and persist
            blob.Blob(data)

            # Verify update_blob was called
            mock_update.assert_called_once()

        finally:
            blob.Blob.initial_version = original_version
            if hasattr(blob.Blob, step_name):
                delattr(blob.Blob, step_name)


class MariaDBBlobFunctionsTestCase(base.ShakenFistTestCase):
    """Tests for mariadb blob functions."""

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_update_blob', return_value=True)
    def test_update_blob_direct(self, mock_direct, mock_use_db):
        """Test update_blob routes to direct function."""
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=10
        )

        result = mariadb.update_blob(data)

        self.assertTrue(result)
        mock_direct.assert_called_once_with(data)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    @mock.patch('shakenfist.mariadb._grpc_update_blob', return_value=True)
    def test_update_blob_grpc(self, mock_grpc, mock_use_db):
        """Test update_blob routes to gRPC function."""
        data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=10
        )

        result = mariadb.update_blob(data)

        self.assertTrue(result)
        mock_grpc.assert_called_once_with(data)

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_get_blob')
    def test_get_blob_direct(self, mock_direct, mock_use_db):
        """Test get_blob routes to direct function."""
        expected_data = BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=10
        )
        mock_direct.return_value = expected_data

        result = mariadb.get_blob(
            UUID('12345678-1234-4321-8234-123456789012'))

        self.assertEqual(result, expected_data)
        mock_direct.assert_called_once()

    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    @mock.patch('shakenfist.mariadb._direct_delete_blob', return_value=True)
    def test_delete_blob_direct(self, mock_direct, mock_use_db):
        """Test delete_blob routes to direct function."""
        result = mariadb.delete_blob(
            UUID('12345678-1234-4321-8234-123456789012'))

        self.assertTrue(result)
        mock_direct.assert_called_once()


class GetActiveBlobUuidsTestCase(base.ShakenFistTestCase):
    """Tests for get_active_blob_uuids() function."""

    @mock.patch('shakenfist.mariadb._direct_get_objects_by_state')
    def test_get_active_blob_uuids(self, mock_get_by_state):
        """Test get_active_blob_uuids returns UUIDs in active states."""
        mock_get_by_state.return_value = [
            'uuid-1', 'uuid-2', 'uuid-3'
        ]

        result = mariadb.get_active_blob_uuids()

        self.assertEqual(result, ['uuid-1', 'uuid-2', 'uuid-3'])
        mock_get_by_state.assert_called_once_with(
            ObjectType.BLOB, ['initial', 'created'], updated_before=None)

    @mock.patch('shakenfist.mariadb._direct_get_objects_by_state')
    def test_failed_read_raises_rather_than_returning_empty(
            self, mock_get_by_state):
        """A failed read must not be indistinguishable from no blobs.

        The underlying accessor returns None when the read failed, which
        this function used to flatten with `or []`. Its callers include
        the cleaner, which deletes every blob file not named in the
        list, so "the read failed" arriving as "nothing is active" is an
        instruction to empty the node's blob store (#3638).
        """
        mock_get_by_state.return_value = None

        self.assertRaises(
            exceptions.DatabaseUnavailable, mariadb.get_active_blob_uuids)

    @mock.patch('shakenfist.mariadb._direct_get_objects_by_state')
    def test_genuinely_empty_is_not_an_error(self, mock_get_by_state):
        """[] still means what it says: there are no active blobs."""
        mock_get_by_state.return_value = []

        self.assertEqual([], mariadb.get_active_blob_uuids())


class ObserveLocalBlobsTestCase(base.ShakenFistTestCase):
    """observe_local_blobs() must only treat UUID-named files as blobs.

    The blob store also contains _version markers, .partial transfers,
    and the resource health _heartbeat sentinel; the sentinel wedged the
    cleaner's scheduler cluster-wide when it was parsed as a blob UUID
    (github issue 3490).
    """

    @mock.patch('shakenfist.blob.Blob.from_db', return_value=None)
    def test_non_uuid_names_are_skipped(self, mock_from_db):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)

        blob_dir = os.path.join(tempdir.name, 'blobs')
        shard = os.path.join(blob_dir, 'ab')
        os.makedirs(shard)

        blob_uuid = '12345678-1234-4321-8234-123456789012'
        for path in [os.path.join(blob_dir, '_heartbeat'),
                     os.path.join(shard, '_version'),
                     os.path.join(shard, f'{blob_uuid}.partial'),
                     os.path.join(shard, blob_uuid)]:
            with open(path, 'w') as f:
                f.write('...')

        with mock.patch('shakenfist.blob.config',
                        FakeConfig(STORAGE_PATH=tempdir.name)):
            blob.observe_local_blobs()

        mock_from_db.assert_called_once_with(
            blob_uuid, suppress_failure_audit=True)


class BlobFromDbMalformedUuidTestCase(base.ShakenFistTestCase):
    """A lookup by a non-UUID name is a miss, not a ValueError.

    from_db() has a not-found contract and storage scanners pass raw
    filenames to it; an unexpected name must not raise (github issue
    3490).
    """

    def test_from_db_malformed_uuid_returns_none(self):
        self.assertIsNone(
            blob.Blob.from_db('_heartbeat', suppress_failure_audit=True))


class VerifyChecksumHashFailedTestCase(base.ShakenFistTestCase):
    """A hash failure during checksum verification must not be invisible.

    verify_checksum() previously let a bare HashFailed escape to the
    background task wrapper, so a replica whose checksum could not be
    verified failed with no blob uuid, no cause, and no consequence for
    the replica (github issue 3744).
    """

    class FakeNodeConfig(BaseSettings):
        STORAGE_PATH: str = '/srv/shakenfist'
        NODE_NAME: str = 'sf-test-node'

    def setUp(self):
        super().setUp()

        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        mock_config = mock.patch(
            'shakenfist.blob.config',
            self.FakeNodeConfig(STORAGE_PATH=tempdir.name))
        mock_config.start()
        self.addCleanup(mock_config.stop)

        mock_get_min = mock.patch(
            'shakenfist.baseobject.get_minimum_object_version',
            return_value=blob.Blob.current_version)
        mock_get_min.start()
        self.addCleanup(mock_get_min.stop)

        mock_get_state = mock.patch(
            'shakenfist.mariadb.get_state',
            return_value=State(value='created', update_time=1234567890.0))
        mock_get_state.start()
        self.addCleanup(mock_get_state.stop)

        self.b = blob.Blob(BlobData(
            uuid='12345678-1234-4321-8234-123456789012',
            modified=1234567890.0,
            fetched_at=1234567891.0,
            version=blob.Blob.current_version
        ))

        self.mock_add_event = mock.patch.object(blob.Blob, 'add_event')
        self.add_event = self.mock_add_event.start()
        self.addCleanup(self.mock_add_event.stop)

        self.mock_remove = mock.patch.object(blob.Blob, '_remove_corrupt_blob')
        self.remove_corrupt = self.mock_remove.start()
        self.addCleanup(self.mock_remove.stop)

    def test_file_not_found_drops_replica(self):
        # The replica this node claims to hold is not on disk, so the
        # location record is wrong and must be dropped.
        with mock.patch(
                'shakenfist.blob.util_concurrency.hash_file',
                side_effect=HashFailed(
                    'FILE_NOT_FOUND', '', '/some/blob', 'sha512')):
            self.assertFalse(self.b.verify_checksum())

        self.remove_corrupt.assert_called_once_with()
        self.add_event.assert_called_once()
        event_args = self.add_event.call_args
        self.assertEqual('blob checksum verification error', event_args[0][1])
        self.assertEqual('FILE_NOT_FOUND', event_args[1]['extra']['error'])

    @mock.patch('shakenfist.mariadb.get_blob_hashes', return_value=[])
    def test_transient_failure_keeps_replica(self, mock_get_hashes):
        # A hasher failure (I/O error, missing hasher) might be transient:
        # the replica must be kept and the exception re-raised so the
        # operation errors visibly and the periodic sweep retries. This
        # exercises the extra-algorithms loop, the call site observed in
        # production.
        with mock.patch(
                'shakenfist.blob.util_concurrency.hash_file',
                side_effect=HashFailed(
                    'ALGORITHM_FAILED', 'Input/output error',
                    '/some/blob', 'xxh128')):
            exc = self.assertRaises(
                HashFailed, self.b.verify_checksum,
                hash='cafebeef', urgent=False)

        self.assertEqual('ALGORITHM_FAILED', exc.error)
        self.remove_corrupt.assert_not_called()
        self.add_event.assert_called_once()
        event_args = self.add_event.call_args
        self.assertEqual('blob checksum verification error', event_args[0][1])
        self.assertEqual('Input/output error',
                         event_args[1]['extra']['error_text'])
