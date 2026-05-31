# Copyright 2019 Michael Still and contributors
#
# Unit tests for the events and event_objects schema helpers and the
# three-layer accessor stack in shakenfist/mariadb.py.
#
# Patterns mirrored:
#   - test_mariadb_migrations.py  (schema-up via mock engine + patched helpers)
#   - test_event_dlq.py           (direct-path writes via _MockEngine/_MockConnection)
#   - test_cluster_operation_targets.py  (public-router tests)

from unittest import mock

from sqlalchemy.exc import IntegrityError

from shakenfist import mariadb
from shakenfist.schema.event import EventRecord
from shakenfist.tests import base

EVENT_UUID_1 = 'aaaabbbb-0000-4000-8000-000000000001'
EVENT_UUID_2 = 'aaaabbbb-0000-4000-8000-000000000002'
OBJ_UUID_1 = 'ccccdddd-1111-4111-8111-111111111101'
OBJ_UUID_2 = 'ccccdddd-1111-4111-8111-111111111102'
OBJ_UUID_3 = 'ccccdddd-1111-4111-8111-111111111103'

_SAMPLE_RECORD = EventRecord(
    event_uuid=EVENT_UUID_1,
    event_type='audit',
    timestamp=1_234_567_890.0,
    fqdn='test-node.example.com',
    duration=None,
    message='unit test event',
    extra=None,
    request_id=None,
    objects=[('instance', OBJ_UUID_1)],
)


# ---------------------------------------------------------------------------
# Mock helpers (mirror test_event_dlq.py _MockResult / _MockConnection /
# _MockEngine pattern, with begin() added for the transaction path).
# ---------------------------------------------------------------------------

class _MockResult:
    def __init__(self, scalar_val=0):
        self._scalar = scalar_val

    def scalar(self):
        return self._scalar


class _MockConnection:
    """Minimal connection stub that records every execute() call.

    Supports both context-manager entry (for engine.connect()) and the
    transaction commit path (engine.begin()).  Every execute() call
    appends the statement to self.executed and returns self.result.
    """

    def __init__(self, result=None):
        self.result = result or _MockResult()
        self.executed = []

    def execute(self, stmt):
        self.executed.append(stmt)
        return self.result

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _MockEngine:
    """Engine stub that returns a fixed connection for both connect() and begin()."""

    def __init__(self, connection):
        self._connection = connection

    def connect(self):
        return self._connection

    def begin(self):
        return self._connection


# ---------------------------------------------------------------------------
# Schema-up tests  (_ensure_events_schema and _ensure_event_objects_schema)
# ---------------------------------------------------------------------------

class EnsureEventsSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_events_schema() schema creation and idempotency."""

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_engine_creates_table_and_returns_version_1(
            self, mock_get_version, mock_set_version):
        """Starting from version 0: schema creates the table, returns target=1 and migrated=True."""
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            result = mariadb._ensure_events_schema(mock_engine)

        self.assertEqual(result['table'], 'events')
        self.assertEqual(result['target_version'], 1)
        self.assertEqual(result['start_version'], 0)
        self.assertEqual(result['end_version'], 1)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(mock_engine, 'events', 1)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_already_at_version_1_is_noop(
            self, mock_get_version, mock_set_version):
        """Table already at v1: no DDL, no version bump, migrated=False."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_events_schema(mock_engine)

        self.assertEqual(result['table'], 'events')
        self.assertEqual(result['target_version'], 1)
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()


class EnsureEventObjectsSchemaTestCase(base.ShakenFistTestCase):
    """Tests for _ensure_event_objects_schema() schema creation and idempotency."""

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=0)
    def test_fresh_engine_creates_table_and_returns_version_1(
            self, mock_get_version, mock_set_version):
        """Starting from version 0: schema creates the table, returns target=1 and migrated=True."""
        mock_engine = mock.MagicMock()

        with mock.patch('sqlalchemy.MetaData.create_all'):
            result = mariadb._ensure_event_objects_schema(mock_engine)

        self.assertEqual(result['table'], 'event_objects')
        self.assertEqual(result['target_version'], 1)
        self.assertEqual(result['start_version'], 0)
        self.assertEqual(result['end_version'], 1)
        self.assertTrue(result['migrated'])
        mock_set_version.assert_called_once_with(mock_engine, 'event_objects', 1)

    @mock.patch('shakenfist.mariadb._set_table_version')
    @mock.patch('shakenfist.mariadb._get_table_version', return_value=1)
    def test_already_at_version_1_is_noop(
            self, mock_get_version, mock_set_version):
        """Table already at v1: no DDL, no version bump, migrated=False."""
        mock_engine = mock.MagicMock()

        result = mariadb._ensure_event_objects_schema(mock_engine)

        self.assertEqual(result['table'], 'event_objects')
        self.assertEqual(result['target_version'], 1)
        self.assertFalse(result['migrated'])
        mock_set_version.assert_not_called()


# ---------------------------------------------------------------------------
# Direct write tests (_direct_record_event_batch)
# ---------------------------------------------------------------------------

class DirectRecordEventBatchTestCase(base.ShakenFistTestCase):
    """Tests for _direct_record_event_batch() using mock engine/connection."""

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_single_event_one_object_writes_two_rows(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """One event with one object: expect exactly two conn.execute calls."""
        import sqlalchemy as sa
        metadata = sa.MetaData()
        events_table = sa.Table(
            'events', metadata,
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
            sa.Column('event_type', sa.String(32), nullable=False),
            sa.Column('timestamp', sa.Double(), nullable=False),
            sa.Column('fqdn', sa.String(255), nullable=False),
            sa.Column('duration', sa.Double(), nullable=True),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('extra', sa.JSON(), nullable=True),
            sa.Column('request_id', sa.String(64), nullable=True),
        )
        event_objects_table = sa.Table(
            'event_objects', metadata,
            sa.Column('object_type', sa.String(32), nullable=False),
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
        )
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_record_event_batch([_SAMPLE_RECORD])

        self.assertTrue(result)
        # One events insert + one event_objects insert = two execute calls.
        self.assertEqual(len(conn.executed), 2)

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_single_event_three_objects_writes_four_rows(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """One event with three objects: one events row + three event_objects rows = four execute calls."""
        import sqlalchemy as sa
        metadata = sa.MetaData()
        events_table = sa.Table(
            'events', metadata,
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
            sa.Column('event_type', sa.String(32), nullable=False),
            sa.Column('timestamp', sa.Double(), nullable=False),
            sa.Column('fqdn', sa.String(255), nullable=False),
            sa.Column('duration', sa.Double(), nullable=True),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('extra', sa.JSON(), nullable=True),
            sa.Column('request_id', sa.String(64), nullable=True),
        )
        event_objects_table = sa.Table(
            'event_objects', metadata,
            sa.Column('object_type', sa.String(32), nullable=False),
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
        )
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)

        record = EventRecord(
            event_uuid=EVENT_UUID_1,
            event_type='audit',
            timestamp=1_234_567_890.0,
            fqdn='test-node.example.com',
            duration=None,
            message='multi-object event',
            extra=None,
            request_id=None,
            objects=[
                ('instance', OBJ_UUID_1),
                ('network', OBJ_UUID_2),
                ('interface', OBJ_UUID_3),
            ],
        )

        result = mariadb._direct_record_event_batch([record])

        self.assertTrue(result)
        # 1 events insert + 3 event_objects inserts = 4 execute calls.
        self.assertEqual(len(conn.executed), 4)

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_three_objects_use_single_transaction(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """All four inserts for a three-object event are inside one begin() call."""
        import sqlalchemy as sa
        metadata = sa.MetaData()
        events_table = sa.Table(
            'events', metadata,
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
            sa.Column('event_type', sa.String(32), nullable=False),
            sa.Column('timestamp', sa.Double(), nullable=False),
            sa.Column('fqdn', sa.String(255), nullable=False),
            sa.Column('duration', sa.Double(), nullable=True),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('extra', sa.JSON(), nullable=True),
            sa.Column('request_id', sa.String(64), nullable=True),
        )
        event_objects_table = sa.Table(
            'event_objects', metadata,
            sa.Column('object_type', sa.String(32), nullable=False),
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
        )
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        # Use a MagicMock engine so we can assert begin() is called exactly once.
        conn = _MockConnection()
        mock_engine = mock.MagicMock()
        mock_engine.begin.return_value.__enter__ = mock.Mock(return_value=conn)
        mock_engine.begin.return_value.__exit__ = mock.Mock(return_value=False)
        mock_get_engine.return_value = mock_engine

        record = EventRecord(
            event_uuid=EVENT_UUID_1,
            event_type='audit',
            timestamp=1_234_567_890.0,
            fqdn='test-node.example.com',
            duration=None,
            message='multi-object event',
            extra=None,
            request_id=None,
            objects=[
                ('instance', OBJ_UUID_1),
                ('network', OBJ_UUID_2),
                ('interface', OBJ_UUID_3),
            ],
        )

        result = mariadb._direct_record_event_batch([record])

        self.assertTrue(result)
        # Only one transaction was opened for the entire batch.
        mock_engine.begin.assert_called_once()
        self.assertEqual(len(conn.executed), 4)

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_duplicate_event_uuid_is_idempotent(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """Duplicate event_uuid PK violation is treated as success (idempotent)."""
        import sqlalchemy as sa
        metadata = sa.MetaData()
        events_table = sa.Table(
            'events', metadata,
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
            sa.Column('event_type', sa.String(32), nullable=False),
            sa.Column('timestamp', sa.Double(), nullable=False),
            sa.Column('fqdn', sa.String(255), nullable=False),
            sa.Column('duration', sa.Double(), nullable=True),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('extra', sa.JSON(), nullable=True),
            sa.Column('request_id', sa.String(64), nullable=True),
        )
        event_objects_table = sa.Table(
            'event_objects', metadata,
            sa.Column('object_type', sa.String(32), nullable=False),
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
        )
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        # Raise IntegrityError mentioning event_uuid so the handler treats it
        # as an idempotent duplicate.
        conn.execute = mock.Mock(
            side_effect=IntegrityError(
                'INSERT INTO events', {}, Exception('Duplicate entry for event_uuid')
            )
        )
        mock_get_engine.return_value = _MockEngine(conn)

        with mock.patch.object(mariadb.LOG, 'info') as mock_log_info:
            result = mariadb._direct_record_event_batch([_SAMPLE_RECORD])

        self.assertTrue(result)
        mock_log_info.assert_called_once()
        logged_msg = str(mock_log_info.call_args[0][0])
        self.assertIn('idempotent', logged_msg.lower())

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_other_integrity_error_returns_false_and_warns(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """A non-duplicate IntegrityError (e.g. NOT NULL) surfaces as False with a warning."""
        import sqlalchemy as sa
        metadata = sa.MetaData()
        events_table = sa.Table(
            'events', metadata,
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
            sa.Column('event_type', sa.String(32), nullable=False),
            sa.Column('timestamp', sa.Double(), nullable=False),
            sa.Column('fqdn', sa.String(255), nullable=False),
            sa.Column('duration', sa.Double(), nullable=True),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('extra', sa.JSON(), nullable=True),
            sa.Column('request_id', sa.String(64), nullable=True),
        )
        event_objects_table = sa.Table(
            'event_objects', metadata,
            sa.Column('object_type', sa.String(32), nullable=False),
            sa.Column('object_uuid', sa.String(36), nullable=False),
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
        )
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        # The error message does NOT contain 'event_uuid' so the handler
        # must treat it as a real bug.
        conn.execute = mock.Mock(
            side_effect=IntegrityError(
                'INSERT INTO events', {}, Exception("Column 'event_type' cannot be null")
            )
        )
        mock_get_engine.return_value = _MockEngine(conn)

        with mock.patch.object(mariadb.LOG, 'warning') as mock_log_warn:
            result = mariadb._direct_record_event_batch([_SAMPLE_RECORD])

        self.assertFalse(result)
        mock_log_warn.assert_called_once()


# ---------------------------------------------------------------------------
# Public router tests (record_event_batch)
# ---------------------------------------------------------------------------

class RecordEventBatchRoutingTestCase(base.ShakenFistTestCase):
    """record_event_batch routes to _grpc_* or _direct_* correctly."""

    @mock.patch('shakenfist.mariadb._grpc_record_event_batch', return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc_when_service_mode(
            self, mock_use_svc, mock_grpc):
        """_use_database_service() == True -> _grpc_record_event_batch is called."""
        result = mariadb.record_event_batch([_SAMPLE_RECORD])
        self.assertTrue(result)
        mock_grpc.assert_called_once_with([_SAMPLE_RECORD])

    @mock.patch('shakenfist.mariadb._direct_record_event_batch', return_value=True)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct_when_not_service_mode(
            self, mock_use_svc, mock_direct):
        """_use_database_service() == False -> _direct_record_event_batch is called."""
        result = mariadb.record_event_batch([_SAMPLE_RECORD])
        self.assertTrue(result)
        mock_direct.assert_called_once_with([_SAMPLE_RECORD])


# ---------------------------------------------------------------------------
# Count read test (_direct_get_events_count)
# ---------------------------------------------------------------------------

class DirectGetEventsCountTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_events_count()."""

    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_scalar_from_connection(
            self, mock_get_engine, mock_get_events_table):
        """The row count returned by the DB is forwarded unchanged."""
        import sqlalchemy as sa
        metadata = sa.MetaData()
        events_table = sa.Table(
            'events', metadata,
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
        )
        mock_get_events_table.return_value = events_table

        conn = _MockConnection(result=_MockResult(scalar_val=42))
        mock_get_engine.return_value = _MockEngine(conn)

        count = mariadb._direct_get_events_count()
        self.assertEqual(count, 42)

    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_zero_on_database_error(
            self, mock_get_engine, mock_get_events_table):
        """OperationalError during the count query returns 0 without raising."""
        from sqlalchemy.exc import OperationalError
        import sqlalchemy as sa
        metadata = sa.MetaData()
        events_table = sa.Table(
            'events', metadata,
            sa.Column('event_uuid', sa.CHAR(36), nullable=False),
        )
        mock_get_events_table.return_value = events_table

        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception('db error'))
        )
        mock_get_engine.return_value = _MockEngine(conn)

        count = mariadb._direct_get_events_count()
        self.assertEqual(count, 0)
