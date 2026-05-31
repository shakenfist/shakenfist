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
from sqlalchemy.exc import OperationalError

from shakenfist import mariadb
from shakenfist.config import config
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
    """Minimal result stub supporting both scalar() (for SELECT) and rowcount (for DELETE)."""

    def __init__(self, scalar_val=0, rowcount=0):
        self._scalar = scalar_val
        self.rowcount = rowcount

    def scalar(self):
        return self._scalar


class _MockConnection:
    """Minimal connection stub that records every execute() call.

    Supports both context-manager entry (for engine.connect()) and the
    transaction commit path (engine.begin()).  Every execute() call
    appends the statement to self.executed and returns self.result.

    Pass ``result`` for a fixed result on every call.  For multi-call
    scenarios where different rowcounts are needed on successive
    iterations, replace ``conn.execute`` with a ``mock.Mock(side_effect=[...])``.
    """

    def __init__(self, result=None):
        self.result = result or _MockResult()
        self.executed = []

    def execute(self, stmt, *args, **kwargs):
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


# ---------------------------------------------------------------------------
# EVENTS_INSERTED counter tests
# ---------------------------------------------------------------------------

def _make_events_and_objects_tables():
    """Helper that returns a pair of minimal SQLAlchemy table objects.

    Re-used by the counter tests to avoid building them inline each time.
    """
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
    return events_table, event_objects_table


class EventsInsertedCounterTestCase(base.ShakenFistTestCase):
    """``EVENTS_INSERTED`` counter increments once per event in a batch.

    Counter reset strategy: ``EVENTS_INSERTED`` is a module-scope
    prometheus_client Counter in a single process-wide registry.
    Previous tests (or the module import itself) may have already
    incremented label combinations we touch here.  We read the current
    value *before* each call and assert that it grew by exactly the
    expected delta, making the test independent of preceding state.
    """

    def _counter_value(self, event_type: str) -> float:
        """Read the current value for a specific ``event_type`` label."""
        return mariadb.EVENTS_INSERTED.labels(
            event_type=event_type)._value.get()

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_single_event_increments_counter_by_one(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """One event in a batch -> counter for its event_type += 1."""
        events_table, event_objects_table = _make_events_and_objects_tables()
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._counter_value('counter_test_single')
        record = EventRecord(
            event_uuid=EVENT_UUID_1,
            event_type='counter_test_single',
            timestamp=1_000_000.0,
            fqdn='test-node',
            duration=None,
            message='counter test',
            extra=None,
            request_id=None,
            objects=[('instance', OBJ_UUID_1)],
        )
        result = mariadb._direct_record_event_batch([record])

        self.assertTrue(result)
        after = self._counter_value('counter_test_single')
        self.assertAlmostEqual(1.0, after - before, places=9)

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_three_events_increment_counter_by_three(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """Three events in one batch -> counter += 3 total."""
        events_table, event_objects_table = _make_events_and_objects_tables()
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._counter_value('counter_test_triple')
        records = [
            EventRecord(
                event_uuid=f'aaaabbbb-0000-4000-8000-{i:012d}',
                event_type='counter_test_triple',
                timestamp=float(1_000_000 + i),
                fqdn='test-node',
                duration=None,
                message=f'counter test {i}',
                extra=None,
                request_id=None,
                objects=[('instance', OBJ_UUID_1)],
            )
            for i in range(3)
        ]
        result = mariadb._direct_record_event_batch(records)

        self.assertTrue(result)
        after = self._counter_value('counter_test_triple')
        self.assertAlmostEqual(3.0, after - before, places=9)

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_mixed_event_types_increment_separate_label_buckets(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """Events with different event_types increment independent counters."""
        events_table, event_objects_table = _make_events_and_objects_tables()
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)

        before_a = self._counter_value('counter_test_mixed_a')
        before_b = self._counter_value('counter_test_mixed_b')

        records = [
            EventRecord(
                event_uuid='aaaabbbb-0000-4000-8000-000000000010',
                event_type='counter_test_mixed_a',
                timestamp=2_000_000.0,
                fqdn='test-node',
                duration=None,
                message='event a',
                extra=None,
                request_id=None,
                objects=[],
            ),
            EventRecord(
                event_uuid='aaaabbbb-0000-4000-8000-000000000011',
                event_type='counter_test_mixed_b',
                timestamp=2_000_001.0,
                fqdn='test-node',
                duration=None,
                message='event b',
                extra=None,
                request_id=None,
                objects=[],
            ),
            EventRecord(
                event_uuid='aaaabbbb-0000-4000-8000-000000000012',
                event_type='counter_test_mixed_a',
                timestamp=2_000_002.0,
                fqdn='test-node',
                duration=None,
                message='event a2',
                extra=None,
                request_id=None,
                objects=[],
            ),
        ]
        result = mariadb._direct_record_event_batch(records)

        self.assertTrue(result)
        after_a = self._counter_value('counter_test_mixed_a')
        after_b = self._counter_value('counter_test_mixed_b')
        self.assertAlmostEqual(2.0, after_a - before_a, places=9)
        self.assertAlmostEqual(1.0, after_b - before_b, places=9)

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_counter_not_incremented_on_integrity_error(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """If the insert raises IntegrityError (non-PK), counter stays at zero delta."""
        events_table, event_objects_table = _make_events_and_objects_tables()
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=IntegrityError(
                'INSERT INTO events', {},
                Exception("Column 'event_type' cannot be null"),
            )
        )
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._counter_value('counter_test_error')
        record = EventRecord(
            event_uuid=EVENT_UUID_2,
            event_type='counter_test_error',
            timestamp=3_000_000.0,
            fqdn='test-node',
            duration=None,
            message='should fail',
            extra=None,
            request_id=None,
            objects=[],
        )
        result = mariadb._direct_record_event_batch([record])

        self.assertFalse(result)
        after = self._counter_value('counter_test_error')
        # The insert never succeeded so the counter must not have moved.
        self.assertAlmostEqual(0.0, after - before, places=9)


# ---------------------------------------------------------------------------
# _direct_prune_events_by_type tests  (Stage A)
# ---------------------------------------------------------------------------

class DirectPruneEventsByTypeTestCase(base.ShakenFistTestCase):
    """Tests for _direct_prune_events_by_type() using mock engine/connection.

    The function executes a DELETE in a loop until rowcount < _PRUNE_BATCH_SIZE
    (10000). We drive the loop by controlling the rowcount returned from each
    conn.execute() call via mock.Mock(side_effect=[...]).
    """

    def _events_pruned_value(self, event_type: str) -> float:
        """Read the current value of EVENTS_PRUNED for a given event_type label."""
        return mariadb.EVENTS_PRUNED.labels(event_type=event_type)._value.get()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_deletes_only_matching_event_type_rows_returns_count(
            self, mock_get_engine):
        """First batch returns rowcount=5 (< 10000), loop exits; total == 5."""
        conn = _MockConnection()
        result_small = _MockResult(rowcount=5)
        conn.execute = mock.Mock(side_effect=[result_small])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_events_by_type('audit', 3600.0)

        self.assertEqual(5, total)
        self.assertEqual(1, conn.execute.call_count)
        # The SQL text should be passed as the first argument.
        called_stmt = conn.execute.call_args[0][0]
        stmt_text = str(called_stmt)
        self.assertIn('event_type', stmt_text)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_loops_until_batch_undershoots(self, mock_get_engine):
        """Two full batches (10000) then a partial (4321) = 24321 total, 3 calls."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _MockResult(rowcount=10000),
            _MockResult(rowcount=10000),
            _MockResult(rowcount=4321),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_events_by_type('mutate', 3600.0)

        self.assertEqual(24321, total)
        self.assertEqual(3, conn.execute.call_count)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_increments_labeled_counter_by_rowcount(self, mock_get_engine):
        """Counter EVENTS_PRUNED for the given label grows by exactly rowcount."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[_MockResult(rowcount=42)])
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._events_pruned_value('prune_counter_test')
        mariadb._direct_prune_events_by_type('prune_counter_test', 3600.0)
        after = self._events_pruned_value('prune_counter_test')

        self.assertAlmostEqual(42.0, after - before, places=9)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_returns_partial_count(self, mock_get_engine):
        """First batch returns rowcount=100; second raises OperationalError.
        Function returns 100 (partial) without re-raising.
        """
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _MockResult(rowcount=100),
            OperationalError('stmt', {}, Exception('db error')),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        # rowcount=100 is less than _PRUNE_BATCH_SIZE (10000), so the loop
        # terminates after the first call without ever reaching the OperationalError.
        # To exercise the error path we need a full-batch first result.
        conn2 = _MockConnection()
        conn2.execute = mock.Mock(side_effect=[
            _MockResult(rowcount=10000),
            OperationalError('stmt', {}, Exception('db error')),
        ])
        mock_get_engine.return_value = _MockEngine(conn2)

        total = mariadb._direct_prune_events_by_type('status', 3600.0)

        # First batch contributed 10000; second raised, so partial is 10000.
        self.assertEqual(10000, total)


# ---------------------------------------------------------------------------
# _direct_prune_api_request_events tests  (Stage B)
# ---------------------------------------------------------------------------

class DirectPruneApiRequestEventsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_prune_api_request_events()."""

    def _api_request_counter_value(self) -> float:
        return mariadb.EVENTS_PRUNED.labels(event_type='api-request')._value.get()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_deletes_api_request_object_type_rows(self, mock_get_engine):
        """SQL statement contains the 'api-request' literal object_type filter."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[_MockResult(rowcount=7)])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_api_request_events(86400.0)

        self.assertEqual(7, total)
        self.assertEqual(1, conn.execute.call_count)
        stmt_text = str(conn.execute.call_args[0][0])
        self.assertIn('api-request', stmt_text)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_counter_label_is_api_request_synthetic(self, mock_get_engine):
        """EVENTS_PRUNED{event_type='api-request'} increments by rowcount."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[_MockResult(rowcount=33)])
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._api_request_counter_value()
        mariadb._direct_prune_api_request_events(86400.0)
        after = self._api_request_counter_value()

        self.assertAlmostEqual(33.0, after - before, places=9)


# ---------------------------------------------------------------------------
# _direct_prune_orphan_events tests  (Stage C)
# ---------------------------------------------------------------------------

class DirectPruneOrphanEventsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_prune_orphan_events()."""

    def _orphan_counter_value(self) -> float:
        return mariadb.ORPHAN_EVENTS_PRUNED._value.get()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_increments_orphan_counter(self, mock_get_engine):
        """ORPHAN_EVENTS_PRUNED increments by the rowcount of each batch."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[_MockResult(rowcount=15)])
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._orphan_counter_value()
        mariadb._direct_prune_orphan_events()
        after = self._orphan_counter_value()

        self.assertAlmostEqual(15.0, after - before, places=9)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_loops_until_undershoot(self, mock_get_engine):
        """Two full batches then a partial: loop exits, total is sum of all batches."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _MockResult(rowcount=10000),
            _MockResult(rowcount=10000),
            _MockResult(rowcount=999),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_orphan_events()

        self.assertEqual(20999, total)
        self.assertEqual(3, conn.execute.call_count)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_orphan_sql_uses_left_join_antijoin_shape(self, mock_get_engine):
        """The DELETE statement uses a LEFT JOIN ... WHERE ... IS NULL anti-join."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[_MockResult(rowcount=0)])
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_prune_orphan_events()

        stmt_text = str(conn.execute.call_args[0][0])
        # Verify the anti-join shape: LEFT JOIN and IS NULL guard.
        self.assertIn('LEFT JOIN', stmt_text)
        self.assertIn('IS NULL', stmt_text)


# ---------------------------------------------------------------------------
# _direct_prune_events orchestrator tests
# ---------------------------------------------------------------------------

class DirectPruneEventsOrchestratorTestCase(base.ShakenFistTestCase):
    """Tests for _direct_prune_events() orchestrator.

    The orchestrator delegates to the three lower-level functions, so we
    patch those rather than the engine — this keeps the tests focused on
    orchestration logic (skip-on-minus-one, summation) and avoids having
    to replicate the full config iteration in mock setup.
    """

    @mock.patch('shakenfist.mariadb._direct_prune_orphan_events', return_value=0)
    @mock.patch('shakenfist.mariadb._direct_prune_api_request_events', return_value=0)
    @mock.patch('shakenfist.mariadb._direct_prune_events_by_type', return_value=0)
    def test_skips_event_types_with_max_age_minus_one(
            self, mock_by_type, mock_api_request, mock_orphan):
        """Event types whose MAX_*_EVENT_AGE == -1 are skipped; others still run."""
        # Patch only audit to -1; mutate keeps its default (non-minus-one).
        with mock.patch.object(config, 'MAX_AUDIT_EVENT_AGE', -1), \
                mock.patch.object(config, 'MAX_MUTATE_EVENT_AGE', 3600):
            mariadb._direct_prune_events()

        called_event_types = [
            call[0][0] for call in mock_by_type.call_args_list
        ]
        self.assertNotIn('audit', called_event_types)
        self.assertIn('mutate', called_event_types)

    @mock.patch('shakenfist.mariadb._direct_prune_orphan_events', return_value=30)
    @mock.patch('shakenfist.mariadb._direct_prune_api_request_events', return_value=20)
    @mock.patch('shakenfist.mariadb._direct_prune_events_by_type', return_value=10)
    def test_calls_all_three_stages_and_sums(
            self, mock_by_type, mock_api_request, mock_orphan):
        """Orchestrator returns sum of all stage totals.

        With seven event_types each returning 10, plus api-request 20,
        plus orphan 30, the total should be (7 * 10) + 20 + 30 = 120.
        """
        total = mariadb._direct_prune_events()

        # Seven per-type calls + one api-request + one orphan.
        self.assertEqual(7, mock_by_type.call_count)
        mock_api_request.assert_called_once()
        mock_orphan.assert_called_once()
        self.assertEqual(120, total)

    @mock.patch('shakenfist.mariadb._direct_prune_orphan_events', return_value=0)
    @mock.patch('shakenfist.mariadb._direct_prune_api_request_events', return_value=0)
    @mock.patch('shakenfist.mariadb._direct_prune_events_by_type', return_value=0)
    def test_skips_api_request_when_max_age_minus_one(
            self, mock_by_type, mock_api_request, mock_orphan):
        """api-request stage is skipped when MAX_API_REQUEST_EVENT_AGE == -1."""
        with mock.patch.object(config, 'MAX_API_REQUEST_EVENT_AGE', -1):
            mariadb._direct_prune_events()

        mock_api_request.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_prune_orphan_events')
    @mock.patch('shakenfist.mariadb._direct_prune_api_request_events')
    @mock.patch('shakenfist.mariadb._direct_prune_events_by_type')
    def test_multi_object_semantic_via_orphan_sweep(
            self, mock_by_type, mock_api_request, mock_orphan):
        """Multi-object retention: event row survives until last object ref is gone.

        Scenario: An event has refs from both an instance and an api-request.
        After stage A drops only the api-request's event_objects row, the
        event_objects row for the instance still exists, so stage C sees a
        non-NULL join and does NOT delete the events row.  Only once stage A
        also drops the instance's event_objects row does stage C find the
        events row unreferenced and delete it.

        We verify this semantic by checking that _direct_prune_orphan_events
        is always called (it runs regardless), and that the orchestrator does
        not short-circuit stage C when prior stages return zero.
        """
        mock_by_type.return_value = 0
        mock_api_request.return_value = 1   # One api-request ref dropped.
        mock_orphan.return_value = 0        # Instance ref still present; no orphan.

        total_first_cycle = mariadb._direct_prune_events()

        # Orphan sweep ran even though only the api-request stage deleted rows.
        mock_orphan.assert_called_once()
        # Event row was not deleted (orphan returned 0).
        self.assertEqual(1, total_first_cycle)

        # Second prune cycle: instance ref is now also dropped.
        mock_by_type.return_value = 1
        mock_api_request.return_value = 0
        mock_orphan.return_value = 1        # Event row now orphaned; deleted.
        mock_orphan.reset_mock()

        total_second_cycle = mariadb._direct_prune_events()

        mock_orphan.assert_called_once()
        # 7 per-type (each = 1) + 0 api-request + 1 orphan = 8
        self.assertEqual(8, total_second_cycle)


# ---------------------------------------------------------------------------
# Public router test for prune_events
# ---------------------------------------------------------------------------

class PruneEventsRoutingTestCase(base.ShakenFistTestCase):
    """prune_events routes to _grpc_* or _direct_* correctly."""

    @mock.patch('shakenfist.mariadb._grpc_prune_events', return_value=42)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc_when_service_mode(self, mock_use_svc, mock_grpc):
        """_use_database_service() == True -> _grpc_prune_events is called."""
        result = mariadb.prune_events()
        self.assertEqual(42, result)
        mock_grpc.assert_called_once()

    @mock.patch('shakenfist.mariadb._direct_prune_events', return_value=99)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct_when_not_service_mode(
            self, mock_use_svc, mock_direct):
        """_use_database_service() == False -> _direct_prune_events is called."""
        result = mariadb.prune_events()
        self.assertEqual(99, result)
        mock_direct.assert_called_once()
