# Copyright 2019 Michael Still and contributors
#
# Unit tests for the events and event_objects schema helpers and the
# three-layer accessor stack in shakenfist/mariadb.py.
#
# Patterns mirrored:
#   - test_mariadb_migrations.py  (schema-up via mock engine + patched helpers)
#   - test_event_dlq.py           (direct-path writes via _MockEngine/_MockConnection)
#   - test_cluster_operation_targets.py  (public-router tests)

import time
from unittest import mock

import grpc
from sqlalchemy.exc import OperationalError

from shakenfist import exceptions
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

class _MockRow:
    """Minimal row stub with attribute access for column values.

    Passed as elements of the list returned by ``_MockResult.fetchall()``.
    Construct with keyword arguments for each column that the caller will
    access as an attribute.
    """

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _MockResult:
    """Minimal result stub supporting scalar(), fetchall(), and rowcount."""

    def __init__(self, scalar_val=0, rowcount=0, rows=None):
        self._scalar = scalar_val
        self.rowcount = rowcount
        self._rows = rows if rows is not None else []

    def scalar(self):
        return self._scalar

    def fetchall(self):
        return self._rows


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
    def test_uses_insert_ignore_prefix(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """INSERTs on both tables are prefixed with IGNORE so a duplicate
        event_uuid is silently skipped per-row rather than rolling the whole
        transaction back. Compiling each captured statement against the
        MariaDB dialect renders the literal ``INSERT IGNORE INTO`` form.
        """
        from sqlalchemy.dialects import mysql
        events_table, event_objects_table = _make_events_and_objects_tables()
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
            message='ignore-prefix probe',
            extra=None,
            request_id=None,
            objects=[('instance', OBJ_UUID_1)],
        )

        result = mariadb._direct_record_event_batch([record])

        self.assertTrue(result)
        self.assertEqual(len(conn.executed), 2)
        for stmt in conn.executed:
            rendered = str(stmt.compile(dialect=mysql.dialect()))
            self.assertIn('INSERT IGNORE INTO', rendered)

    @mock.patch('shakenfist.mariadb._get_event_objects_table')
    @mock.patch('shakenfist.mariadb._get_events_table')
    @mock.patch('shakenfist.mariadb._get_engine')
    def test_mixed_batch_with_duplicate_does_not_lose_siblings(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """A retry-shaped batch where one event_uuid is already in the table
        does not lose its sibling events. With INSERT IGNORE each INSERT
        either lands or no-ops; the mock cannot model the server-side row
        check, but it can prove that every event's INSERT was executed (no
        rollback truncates the loop) and that all three return success.
        """
        events_table, event_objects_table = _make_events_and_objects_tables()
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        mock_get_engine.return_value = _MockEngine(conn)

        # Three single-object events. The middle one stands in for a
        # duplicate that would previously have raised IntegrityError and
        # rolled back the whole batch.
        records = [
            EventRecord(
                event_uuid=u, event_type='audit',
                timestamp=t, fqdn='test-node', duration=None,
                message=f'event {i}', extra=None, request_id=None,
                objects=[('instance', OBJ_UUID_1)],
            )
            for i, (u, t) in enumerate([
                (EVENT_UUID_1, 1_000_000.0),
                (EVENT_UUID_2, 2_000_000.0),
                (EVENT_UUID_1, 3_000_000.0),
            ])
        ]

        result = mariadb._direct_record_event_batch(records)

        self.assertTrue(result)
        # 3 events x (1 events insert + 1 event_objects insert) = 6 calls.
        # All inserts were executed -- the loop is not short-circuited.
        self.assertEqual(len(conn.executed), 6)


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

    @mock.patch('shakenfist.mariadb._get_bounded_engine')
    def test_returns_estimate_from_information_schema(
            self, mock_get_bounded_engine):
        """The information_schema TABLE_ROWS estimate is forwarded as an int.

        The gauge refresh must not run an exact COUNT(*) -- the full scan
        of a table larger than the buffer pool is what starved the systemd
        watchdog in issue 3586 -- and must use the bounded engine so it
        cannot stall the database daemon's main loop.
        """
        conn = _MockConnection(result=_MockResult(scalar_val=42))
        mock_get_bounded_engine.return_value = _MockEngine(conn)

        count = mariadb._direct_get_events_count()
        self.assertEqual(count, 42)

        self.assertEqual(1, len(conn.executed))
        self.assertIn('information_schema.tables', str(conn.executed[0]))
        self.assertNotIn('count', str(conn.executed[0]).lower())

    @mock.patch('shakenfist.mariadb._get_bounded_engine')
    def test_returns_zero_when_table_absent(self, mock_get_bounded_engine):
        """No information_schema row (scalar None) is reported as 0."""
        conn = _MockConnection(result=_MockResult(scalar_val=None))
        mock_get_bounded_engine.return_value = _MockEngine(conn)

        count = mariadb._direct_get_events_count()
        self.assertEqual(count, 0)

    @mock.patch('shakenfist.mariadb._get_bounded_engine')
    def test_returns_zero_on_database_error(self, mock_get_bounded_engine):
        """OperationalError during the estimate query returns 0 without raising.

        This includes the read/connect timeouts the bounded engine raises
        as OperationalError when the server stalls.
        """
        from sqlalchemy.exc import OperationalError

        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('stmt', {}, Exception('db error'))
        )
        mock_get_bounded_engine.return_value = _MockEngine(conn)

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
    def test_counter_not_incremented_on_operational_error(
            self, mock_get_engine, mock_get_events_table,
            mock_get_event_objects_table):
        """If the transaction aborts on OperationalError mid-batch the
        EVENTS_INSERTED counter must not move. The fix for review item #2
        defers .inc() to after the ``with engine.begin()`` block exits
        cleanly so a rollback never leaves phantom increments behind.
        """
        events_table, event_objects_table = _make_events_and_objects_tables()
        mock_get_events_table.return_value = events_table
        mock_get_event_objects_table.return_value = event_objects_table

        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError(
                'INSERT INTO events', {},
                Exception('lost connection during write'),
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
        # The insert never committed so the counter must not have moved.
        self.assertAlmostEqual(0.0, after - before, places=9)


# ---------------------------------------------------------------------------
# _direct_prune_events_by_type tests  (Stage A)
# ---------------------------------------------------------------------------

def _select_result(*rows):
    """A _MockResult whose fetchall() yields the given _MockRow objects."""
    return _MockResult(rows=list(rows))


class DirectPruneEventsByTypeTestCase(base.ShakenFistTestCase):
    """Tests for _direct_prune_events_by_type() using mock engine/connection.

    Each batch is a candidate SELECT followed by a delete-by-key; the loop
    exits when the SELECT returns no rows. We drive the loop by supplying
    alternating select/delete results via mock.Mock(side_effect=[...]).
    """

    def _events_pruned_value(self, event_type: str) -> float:
        """Read the current value of EVENTS_PRUNED for a given event_type label."""
        return mariadb.EVENTS_PRUNED.labels(event_type=event_type)._value.get()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_deletes_only_matching_event_type_rows_returns_count(
            self, mock_get_engine):
        """One batch of 5 candidates, then an empty select ends the loop."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(
                _MockRow(event_uuid=EVENT_UUID_1, timestamp=100.0),
                _MockRow(event_uuid=EVENT_UUID_2, timestamp=101.0)),
            _MockResult(rowcount=5),
            _select_result(),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_events_by_type('audit', 3600.0)

        self.assertEqual(5, total)
        self.assertEqual(3, conn.execute.call_count)
        select_text = str(conn.execute.call_args_list[0][0][0])
        self.assertIn('SELECT', select_text)
        self.assertIn('event_type', select_text)
        # The delete is by key only: no join to walk (and lock), and no
        # LIMIT, which bounds rows deleted rather than rows examined
        # (issue 3982).
        delete_text = str(conn.execute.call_args_list[1][0][0])
        self.assertIn('DELETE FROM event_objects', delete_text)
        self.assertNotIn('JOIN', delete_text)
        self.assertNotIn('LIMIT', delete_text)
        self.assertEqual(
            {EVENT_UUID_1, EVENT_UUID_2},
            set(conn.execute.call_args_list[1][0][1]['event_uuids']))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_loops_until_select_empty_and_advances_cursor(
            self, mock_get_engine):
        """Two batches then an empty select: totals sum, cursor advances."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(_MockRow(event_uuid=EVENT_UUID_1, timestamp=50.0)),
            _MockResult(rowcount=10000),
            _select_result(_MockRow(event_uuid=EVENT_UUID_2, timestamp=60.0)),
            _MockResult(rowcount=4321),
            _select_result(),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_events_by_type('mutate', 3600.0)

        self.assertEqual(14321, total)
        self.assertEqual(5, conn.execute.call_count)
        # The first select starts from the epoch; later selects resume from
        # the previous batch's last timestamp so the sweep is one index pass.
        self.assertEqual(0.0, conn.execute.call_args_list[0][0][1]['cursor'])
        self.assertEqual(50.0, conn.execute.call_args_list[2][0][1]['cursor'])
        self.assertEqual(60.0, conn.execute.call_args_list[4][0][1]['cursor'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_increments_labeled_counter_by_rowcount(self, mock_get_engine):
        """Counter EVENTS_PRUNED for the given label grows by exactly rowcount."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(_MockRow(event_uuid=EVENT_UUID_1, timestamp=1.0)),
            _MockResult(rowcount=42),
            _select_result(),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._events_pruned_value('prune_counter_test')
        mariadb._direct_prune_events_by_type('prune_counter_test', 3600.0)
        after = self._events_pruned_value('prune_counter_test')

        self.assertAlmostEqual(42.0, after - before, places=9)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_returns_partial_count(self, mock_get_engine):
        """First batch deletes 10000; the next select raises OperationalError.
        Function returns 10000 (partial) without re-raising.
        """
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(_MockRow(event_uuid=EVENT_UUID_1, timestamp=1.0)),
            _MockResult(rowcount=10000),
            OperationalError('stmt', {}, Exception('db error')),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_events_by_type('status', 3600.0)

        self.assertEqual(10000, total)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_expired_deadline_stops_before_any_batch(self, mock_get_engine):
        """An already-expired deadline issues no queries (issue 4034)."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=AssertionError('no queries'))
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_events_by_type(
            'audit', 3600.0, deadline=time.time() - 1.0)

        self.assertEqual(0, total)
        self.assertEqual(0, conn.execute.call_count)


# ---------------------------------------------------------------------------
# _direct_prune_api_request_events tests  (Stage B)
# ---------------------------------------------------------------------------

class DirectPruneApiRequestEventsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_prune_api_request_events()."""

    def _api_request_counter_value(self) -> float:
        return mariadb.EVENTS_PRUNED.labels(event_type='api-request')._value.get()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_deletes_api_request_object_type_rows(self, mock_get_engine):
        """Both statements carry the 'api-request' literal object_type filter."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(_MockRow(event_uuid=EVENT_UUID_1)),
            _MockResult(rowcount=7),
            _select_result(),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_api_request_events(86400.0)

        self.assertEqual(7, total)
        self.assertEqual(3, conn.execute.call_count)
        select_text = str(conn.execute.call_args_list[0][0][0])
        self.assertIn('SELECT', select_text)
        self.assertIn('api-request', select_text)
        # Delete-by-key only: no join, no LIMIT (issue 3982).
        delete_text = str(conn.execute.call_args_list[1][0][0])
        self.assertIn('api-request', delete_text)
        self.assertNotIn('JOIN', delete_text)
        self.assertNotIn('LIMIT', delete_text)
        self.assertEqual(
            [EVENT_UUID_1],
            conn.execute.call_args_list[1][0][1]['event_uuids'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_counter_label_is_api_request_synthetic(self, mock_get_engine):
        """EVENTS_PRUNED{event_type='api-request'} increments by rowcount."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(_MockRow(event_uuid=EVENT_UUID_1)),
            _MockResult(rowcount=33),
            _select_result(),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._api_request_counter_value()
        mariadb._direct_prune_api_request_events(86400.0)
        after = self._api_request_counter_value()

        self.assertAlmostEqual(33.0, after - before, places=9)


# ---------------------------------------------------------------------------
# _direct_prune_orphan_events tests  (Stage C)
# ---------------------------------------------------------------------------

@mock.patch('shakenfist.mariadb._save_orphan_prune_cursor')
@mock.patch('shakenfist.mariadb._load_orphan_prune_cursor', return_value='')
class DirectPruneOrphanEventsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_prune_orphan_events()."""

    def _orphan_counter_value(self) -> float:
        return mariadb.ORPHAN_EVENTS_PRUNED._value.get()

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_increments_orphan_counter(
            self, mock_get_engine, mock_load, mock_save):
        """ORPHAN_EVENTS_PRUNED increments by the rowcount of each batch."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(_MockRow(event_uuid=EVENT_UUID_1)),
            _MockResult(rowcount=15),
            _select_result(),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        before = self._orphan_counter_value()
        mariadb._direct_prune_orphan_events()
        after = self._orphan_counter_value()

        self.assertAlmostEqual(15.0, after - before, places=9)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_loops_until_select_empty_and_advances_cursor(
            self, mock_get_engine, mock_load, mock_save):
        """Two batches then an empty select: totals sum, uuid cursor advances."""
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(_MockRow(event_uuid=EVENT_UUID_1)),
            _MockResult(rowcount=10000),
            _select_result(_MockRow(event_uuid=EVENT_UUID_2)),
            _MockResult(rowcount=999),
            _select_result(),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_orphan_events()

        self.assertEqual(10999, total)
        self.assertEqual(5, conn.execute.call_count)
        # The select walks the events PK from a uuid cursor so the sweep is
        # a single pass rather than a rescan from the table start per batch.
        self.assertEqual('', conn.execute.call_args_list[0][0][1]['cursor'])
        self.assertEqual(
            EVENT_UUID_1, conn.execute.call_args_list[2][0][1]['cursor'])
        self.assertEqual(
            EVENT_UUID_2, conn.execute.call_args_list[4][0][1]['cursor'])
        # The final position is persisted for the next sweep to resume from.
        mock_save.assert_called_once_with(EVENT_UUID_2)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_orphan_antijoin_is_select_only_delete_is_by_pk(
            self, mock_get_engine, mock_load, mock_save):
        """The anti-join lives in the non-locking SELECT; the DELETE is by PK.

        Issue 3982: a DELETE ... LEFT JOIN ... LIMIT next-key-locks every
        row the anti-join examines (measured at 15.8M rows per batch), so
        the DELETE must not contain the join or a LIMIT.
        """
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            _select_result(_MockRow(event_uuid=EVENT_UUID_1)),
            _MockResult(rowcount=1),
            _select_result(),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_prune_orphan_events()

        select_text = str(conn.execute.call_args_list[0][0][0])
        self.assertIn('SELECT', select_text)
        self.assertIn('LEFT JOIN', select_text)
        self.assertIn('IS NULL', select_text)
        delete_text = str(conn.execute.call_args_list[1][0][0])
        self.assertIn('DELETE FROM events', delete_text)
        self.assertNotIn('JOIN', delete_text)
        self.assertNotIn('LIMIT', delete_text)
        self.assertEqual(
            [EVENT_UUID_1],
            conn.execute.call_args_list[1][0][1]['event_uuids'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_expired_deadline_stops_before_any_batch(
            self, mock_get_engine, mock_load, mock_save):
        """An already-expired deadline issues no queries and keeps the cursor.

        Issue 4034: the sweep must stop cleanly between batches once its
        time budget is spent, persisting its position so the next daily
        sweep resumes rather than restarting from the table head.
        """
        mock_load.return_value = EVENT_UUID_1
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=AssertionError('no queries'))
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_orphan_events(
            deadline=time.time() - 1.0)

        self.assertEqual(0, total)
        self.assertEqual(0, conn.execute.call_count)
        mock_save.assert_called_once_with(EVENT_UUID_1)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_resumed_walk_wraps_once_and_stops_at_start(
            self, mock_get_engine, mock_load, mock_save):
        """A walk resumed mid-table wraps to the head and stops at its start.

        Issue 4034: resuming from the persisted cursor covers the tail
        first, then wraps to '' to cover the head that resuming skipped,
        and finishes once it reaches the start position again rather
        than re-walking the tail a second time.
        """
        start = 'cccccccc-0000-0000-0000-000000000000'
        head = 'aaaaaaaa-0000-0000-0000-000000000000'
        tail = 'eeeeeeee-0000-0000-0000-000000000000'
        past_start = 'dddddddd-0000-0000-0000-000000000000'
        mock_load.return_value = start
        conn = _MockConnection()
        conn.execute = mock.Mock(side_effect=[
            # Tail batch from the resumed cursor, then the end of the PK.
            _select_result(_MockRow(event_uuid=tail)),
            _MockResult(rowcount=3),
            _select_result(),
            # Wrapped pass from the head; the second batch reaches a uuid
            # at or beyond the start position, so the walk is complete.
            _select_result(_MockRow(event_uuid=head)),
            _MockResult(rowcount=2),
            _select_result(_MockRow(event_uuid=past_start)),
            _MockResult(rowcount=1),
        ])
        mock_get_engine.return_value = _MockEngine(conn)

        total = mariadb._direct_prune_orphan_events()

        self.assertEqual(6, total)
        self.assertEqual(7, conn.execute.call_count)
        self.assertEqual(
            start, conn.execute.call_args_list[0][0][1]['cursor'])
        self.assertEqual(
            '', conn.execute.call_args_list[3][0][1]['cursor'])
        self.assertEqual(
            head, conn.execute.call_args_list[5][0][1]['cursor'])
        mock_save.assert_called_once_with(past_start)


# ---------------------------------------------------------------------------
# Orphan prune cursor persistence tests
# ---------------------------------------------------------------------------

class OrphanPruneCursorTestCase(base.ShakenFistTestCase):
    """The orphan sweep cursor round-trips through cluster_config."""

    @mock.patch('shakenfist.mariadb._direct_get_all_cluster_config',
                return_value={})
    def test_load_returns_empty_string_when_unset(self, mock_get):
        self.assertEqual('', mariadb._load_orphan_prune_cursor())

    @mock.patch('shakenfist.mariadb._direct_get_all_cluster_config',
                return_value={
                    mariadb._PRUNE_ORPHAN_CURSOR_KEY: EVENT_UUID_1})
    def test_load_returns_persisted_cursor(self, mock_get):
        self.assertEqual(EVENT_UUID_1, mariadb._load_orphan_prune_cursor())

    @mock.patch('shakenfist.mariadb._direct_set_cluster_config')
    def test_save_writes_cluster_config_key(self, mock_set):
        mariadb._save_orphan_prune_cursor(EVENT_UUID_2)
        mock_set.assert_called_once_with(
            mariadb._PRUNE_ORPHAN_CURSOR_KEY, EVENT_UUID_2)


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

        With eight event_types each returning 10, plus api-request 20,
        plus orphan 30, the total should be (8 * 10) + 20 + 30 = 130.
        """
        total = mariadb._direct_prune_events()

        # Eight per-type calls + one api-request + one orphan.
        self.assertEqual(8, mock_by_type.call_count)
        mock_api_request.assert_called_once()
        mock_orphan.assert_called_once()
        self.assertEqual(130, total)

    @mock.patch('shakenfist.mariadb._direct_prune_orphan_events', return_value=0)
    @mock.patch('shakenfist.mariadb._direct_prune_api_request_events', return_value=0)
    @mock.patch('shakenfist.mariadb._direct_prune_events_by_type', return_value=0)
    def test_stages_share_one_time_budget(
            self, mock_by_type, mock_api_request, mock_orphan):
        """All three stages receive the same wall-clock deadline.

        Issue 4034: the sweep bounds itself at PRUNE_EVENTS_TIME_BUDGET
        so the PruneEvents reply always beats the client's RPC deadline.
        """
        before = time.time()
        mariadb._direct_prune_events()
        after = time.time()

        deadlines = set()
        for call in mock_by_type.call_args_list:
            deadlines.add(call[1]['deadline'])
        deadlines.add(mock_api_request.call_args[1]['deadline'])
        deadlines.add(mock_orphan.call_args[1]['deadline'])

        self.assertEqual(1, len(deadlines))
        deadline = deadlines.pop()
        self.assertGreaterEqual(
            deadline, before + mariadb.PRUNE_EVENTS_TIME_BUDGET)
        self.assertLessEqual(
            deadline, after + mariadb.PRUNE_EVENTS_TIME_BUDGET)

    def test_rpc_timeout_exceeds_server_budget(self):
        """The client deadline must outlast the server's sweep budget.

        Issue 4034: if the RPC timeout is not comfortably above the
        server's self-imposed budget, the client abandons a healthy
        sweep and reports phantom failure while the server keeps
        deleting rows.
        """
        self.assertGreaterEqual(
            mariadb.PRUNE_EVENTS_RPC_TIMEOUT,
            mariadb.PRUNE_EVENTS_TIME_BUDGET + 60.0)

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
        # 8 per-type (each = 1) + 0 api-request + 1 orphan = 9
        self.assertEqual(9, total_second_cycle)


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


class GrpcPruneEventsTestCase(base.ShakenFistTestCase):
    """_grpc_prune_events must raise on failure, never return zero.

    Issue 3849: a DEADLINE_EXCEEDED prune returned 0, so the cluster
    maintainer logged the success line "removed 0 rows" for a sweep
    that never ran. A return value from _grpc_prune_events now always
    means the sweep ran.
    """

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_success_returns_rows_pruned(self, mock_stub):
        future = mock_stub.return_value.PruneEvents.future
        future.return_value.result.return_value = mock.Mock(
            success=True, error='', rows_pruned=42)

        self.assertEqual(42, mariadb._grpc_prune_events())
        _, kwargs = future.call_args
        self.assertEqual(
            mariadb.PRUNE_EVENTS_RPC_TIMEOUT, kwargs['timeout'])
        # The wait on the future is chunked so the watchdog can be
        # petted between chunks (issue 3919).
        _, kwargs = future.return_value.result.call_args
        self.assertEqual(
            mariadb.PRUNE_EVENTS_PET_INTERVAL, kwargs['timeout'])

    @mock.patch('shakenfist.mariadb.LOG')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_rpc_error_raises_database_unavailable(
            self, mock_stub, mock_log):
        future = mock_stub.return_value.PruneEvents.future
        future.return_value.result.side_effect = grpc.RpcError()

        self.assertRaises(
            exceptions.DatabaseUnavailable, mariadb._grpc_prune_events)

        # The failure line must carry the elapsed time and the RPC
        # deadline so an operator can see a timeout for what it is.
        mock_log.error.assert_called_once()
        logged = mock_log.error.call_args[0][0]
        self.assertIn('after', logged)
        self.assertIn(
            f'{mariadb.PRUNE_EVENTS_RPC_TIMEOUT:.0f}s deadline', logged)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_server_side_failure_raises_write_exception(self, mock_stub):
        future = mock_stub.return_value.PruneEvents.future
        future.return_value.result.return_value = mock.Mock(
            success=False, error='mid-sweep explosion', rows_pruned=0)

        exc = self.assertRaises(
            exceptions.WriteException, mariadb._grpc_prune_events)
        self.assertIn('mid-sweep explosion', str(exc))


# ---------------------------------------------------------------------------
# Phase 4 tests — direct get/delete object events helpers
# ---------------------------------------------------------------------------

_OBJ_UUID_X = 'ccccdddd-2222-4222-8222-222222222201'

# Three sample DB rows returned by conn.execute(...).fetchall() for the
# get-events path.  Timestamps are intentionally out of order to verify
# that the function returns whatever the DB gives (ordering is done in SQL).
_ROWS_3 = [
    _MockRow(
        event_uuid='eeeeeeee-0000-4000-8000-000000000001',
        event_type='audit',
        timestamp=1_000_003.0,
        fqdn='node-a.example.com',
        duration=0.25,
        message='third event',
        extra=None,
        request_id='req-003',
    ),
    _MockRow(
        event_uuid='eeeeeeee-0000-4000-8000-000000000002',
        event_type='mutate',
        timestamp=1_000_002.0,
        fqdn='node-a.example.com',
        duration=None,
        message='second event',
        extra='{"key": "value"}',
        request_id=None,
    ),
    _MockRow(
        event_uuid='eeeeeeee-0000-4000-8000-000000000003',
        event_type='audit',
        timestamp=1_000_001.0,
        fqdn='node-b.example.com',
        duration=1.5,
        message='first event',
        extra=None,
        request_id='req-001',
    ),
]


class DirectGetObjectEventsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_get_object_events() using mock engine/connection."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_returns_event_read_row_list_in_timestamp_desc_order(
            self, mock_get_engine):
        """fetchall() returns 3 rows; function returns 3 EventReadRow objects."""
        conn = _MockConnection(result=_MockResult(rows=_ROWS_3))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_get_object_events('instance', _OBJ_UUID_X)

        self.assertEqual(3, len(results))
        # First row maps through unchanged.
        self.assertEqual('eeeeeeee-0000-4000-8000-000000000001', results[0].event_uuid)
        self.assertEqual('audit', results[0].event_type)
        self.assertAlmostEqual(1_000_003.0, results[0].timestamp)
        self.assertEqual('node-a.example.com', results[0].fqdn)
        self.assertAlmostEqual(0.25, results[0].duration)
        self.assertEqual('third event', results[0].message)
        self.assertIsNone(results[0].extra)
        self.assertEqual('req-003', results[0].request_id)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_empty_result_returns_empty_list(self, mock_get_engine):
        """fetchall() returns []; function returns []."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_get_object_events('network', _OBJ_UUID_X)

        self.assertEqual([], results)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_event_type_filter_passed_to_sql(self, mock_get_engine):
        """event_type='audit' sets event_type_filter='audit' in bound params;
        event_type=None sets event_type_filter='' (match-all sentinel).
        """
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)
        conn.execute = mock.Mock(return_value=_MockResult(rows=[]))

        # With explicit event_type filter.
        mariadb._direct_get_object_events('instance', _OBJ_UUID_X, event_type='audit')
        # Bound params are passed as the second positional argument (a dict).
        params_with_filter = conn.execute.call_args[0][1]
        self.assertEqual('audit', params_with_filter['event_type_filter'])

        # Without event_type filter (None -> empty-string sentinel).
        mariadb._direct_get_object_events('instance', _OBJ_UUID_X, event_type=None)
        params_without_filter = conn.execute.call_args[0][1]
        self.assertEqual('', params_without_filter['event_type_filter'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_limit_clamped_to_default_when_zero(self, mock_get_engine):
        """limit=0 is replaced with the default 100 before binding."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)
        conn.execute = mock.Mock(return_value=_MockResult(rows=[]))

        mariadb._direct_get_object_events('instance', _OBJ_UUID_X, limit=0)

        params = conn.execute.call_args[0][1]
        self.assertEqual(100, params['limit'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_limit_clamped_to_default_when_negative(self, mock_get_engine):
        """limit=-1 (legacy REST API 'all rows') is replaced with 100."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)
        conn.execute = mock.Mock(return_value=_MockResult(rows=[]))

        mariadb._direct_get_object_events('instance', _OBJ_UUID_X, limit=-1)

        params = conn.execute.call_args[0][1]
        self.assertEqual(100, params['limit'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_limit_capped_at_1000_when_too_high(self, mock_get_engine):
        """limit=5000 is capped at 1000."""
        conn = _MockConnection(result=_MockResult(rows=[]))
        mock_get_engine.return_value = _MockEngine(conn)
        conn.execute = mock.Mock(return_value=_MockResult(rows=[]))

        mariadb._direct_get_object_events('instance', _OBJ_UUID_X, limit=5000)

        params = conn.execute.call_args[0][1]
        self.assertEqual(1000, params['limit'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_extra_json_string_decoded_to_dict(self, mock_get_engine):
        """A JSON string in row.extra is decoded to a dict on EventReadRow."""
        row = _MockRow(
            event_uuid='eeeeeeee-0000-4000-8000-000000000010',
            event_type='audit',
            timestamp=1_000_000.0,
            fqdn='node-a',
            duration=None,
            message='event with extra',
            extra='{"foo": 1}',
            request_id=None,
        )
        conn = _MockConnection(result=_MockResult(rows=[row]))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_get_object_events('instance', _OBJ_UUID_X)

        self.assertEqual(1, len(results))
        self.assertEqual({'foo': 1}, results[0].extra)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_extra_corrupt_json_falls_back_to_none(self, mock_get_engine):
        """Corrupt JSON in row.extra is silently dropped; EventReadRow.extra is None."""
        row = _MockRow(
            event_uuid='eeeeeeee-0000-4000-8000-000000000011',
            event_type='audit',
            timestamp=1_000_000.0,
            fqdn='node-a',
            duration=None,
            message='event with bad extra',
            extra='not json',
            request_id=None,
        )
        conn = _MockConnection(result=_MockResult(rows=[row]))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_get_object_events('instance', _OBJ_UUID_X)

        self.assertEqual(1, len(results))
        self.assertIsNone(results[0].extra)

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_returns_empty_list(self, mock_get_engine):
        """OperationalError during execute returns [] without re-raising."""
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('SELECT', {}, Exception('db down')))
        mock_get_engine.return_value = _MockEngine(conn)

        results = mariadb._direct_get_object_events('instance', _OBJ_UUID_X)

        self.assertEqual([], results)


# ---------------------------------------------------------------------------
# Phase 4 tests — direct delete object events helper
# ---------------------------------------------------------------------------

class DirectDeleteObjectEventsTestCase(base.ShakenFistTestCase):
    """Tests for _direct_delete_object_events() using mock engine/connection."""

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_executes_correct_delete_sql(self, mock_get_engine):
        """Executed SQL contains DELETE FROM event_objects with correct bound params."""
        conn = _MockConnection()
        conn.execute = mock.Mock(return_value=_MockResult())
        mock_get_engine.return_value = _MockEngine(conn)

        mariadb._direct_delete_object_events('instance', _OBJ_UUID_X)

        self.assertEqual(1, conn.execute.call_count)
        stmt, params = conn.execute.call_args[0]
        stmt_text = str(stmt)
        self.assertIn('DELETE FROM event_objects', stmt_text)
        self.assertEqual('instance', params['object_type'])
        self.assertEqual(_OBJ_UUID_X, params['object_uuid'])

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_is_swallowed(self, mock_get_engine):
        """OperationalError during DELETE does not propagate; returns None."""
        conn = _MockConnection()
        conn.execute = mock.Mock(
            side_effect=OperationalError('DELETE', {}, Exception('db down')))
        mock_get_engine.return_value = _MockEngine(conn)

        result = mariadb._direct_delete_object_events('blob', _OBJ_UUID_X)

        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Phase 4 tests — get_object_events routing
# ---------------------------------------------------------------------------

class GetObjectEventsRoutingTestCase(base.ShakenFistTestCase):
    """get_object_events routes to _grpc_* or _direct_* correctly."""

    @mock.patch('shakenfist.mariadb._direct_get_object_events', return_value=[])
    @mock.patch('shakenfist.mariadb._grpc_get_object_events', return_value=[])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc_when_service_mode(
            self, mock_use_svc, mock_grpc, mock_direct):
        """_use_database_service() == True -> _grpc_get_object_events is called."""
        mariadb.get_object_events('instance', _OBJ_UUID_X)
        mock_grpc.assert_called_once_with('instance', _OBJ_UUID_X, 100, None)
        mock_direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_get_object_events', return_value=[])
    @mock.patch('shakenfist.mariadb._grpc_get_object_events', return_value=[])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct_when_not_service_mode(
            self, mock_use_svc, mock_grpc, mock_direct):
        """_use_database_service() == False -> _direct_get_object_events is called."""
        mariadb.get_object_events('instance', _OBJ_UUID_X)
        mock_direct.assert_called_once_with('instance', _OBJ_UUID_X, 100, None)
        mock_grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_get_object_events', return_value=[])
    @mock.patch('shakenfist.mariadb._grpc_get_object_events', return_value=[])
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_uuid_object_is_coerced_to_str(
            self, mock_use_svc, mock_grpc, mock_direct):
        """REST endpoints pass ``DatabaseBackedObject.uuid`` (a uuid.UUID
        instance) straight through; the public function must coerce to
        str because the gRPC layer's proto string field rejects UUID
        objects with a TypeError. CI surfaced this as HTTP 400 on
        /artifacts/<u>/events.
        """
        import uuid as _uuid
        u = _uuid.UUID('b2d0e260-423f-4387-9f5e-179603c254ba')
        mariadb.get_object_events('artifact', u)
        mock_grpc.assert_called_once_with(
            'artifact', str(u), 100, None)
        # ``str(u)`` round-trips the UUID to its canonical hyphenated form.
        self.assertEqual(
            'b2d0e260-423f-4387-9f5e-179603c254ba',
            mock_grpc.call_args[0][1])


class GrpcGetObjectEventsFailureLoggingTestCase(base.ShakenFistTestCase):
    """RPC failures must name the object they were reading."""

    @mock.patch('shakenfist.mariadb.LOG')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_rpc_failure_log_names_the_object(self, mock_stub, mock_log):
        # A RESOURCE_EXHAUSTED oversized-reply failure (#3638) can only
        # be traced to the object whose event history blew the message
        # cap if the error log carries the object id.
        mock_stub.return_value.GetObjectEvents.side_effect = grpc.RpcError()

        result = mariadb._grpc_get_object_events('instance', _OBJ_UUID_X)

        self.assertEqual([], result)
        mock_log.error.assert_called_once()
        logged = mock_log.error.call_args[0][0]
        self.assertIn('instance', logged)
        self.assertIn(_OBJ_UUID_X, logged)


# ---------------------------------------------------------------------------
# Phase 4 tests — delete_object_events routing
# ---------------------------------------------------------------------------

class DeleteObjectEventsRoutingTestCase(base.ShakenFistTestCase):
    """delete_object_events routes to _grpc_* or _direct_* correctly."""

    @mock.patch('shakenfist.mariadb._direct_delete_object_events', return_value=None)
    @mock.patch('shakenfist.mariadb._grpc_delete_object_events', return_value=None)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_routes_to_grpc_when_service_mode(
            self, mock_use_svc, mock_grpc, mock_direct):
        """_use_database_service() == True -> _grpc_delete_object_events is called."""
        mariadb.delete_object_events('instance', _OBJ_UUID_X)
        mock_grpc.assert_called_once_with('instance', _OBJ_UUID_X)
        mock_direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_delete_object_events', return_value=None)
    @mock.patch('shakenfist.mariadb._grpc_delete_object_events', return_value=None)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=False)
    def test_routes_to_direct_when_not_service_mode(
            self, mock_use_svc, mock_grpc, mock_direct):
        """_use_database_service() == False -> _direct_delete_object_events is called."""
        mariadb.delete_object_events('instance', _OBJ_UUID_X)
        mock_direct.assert_called_once_with('instance', _OBJ_UUID_X)
        mock_grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_delete_object_events', return_value=None)
    @mock.patch('shakenfist.mariadb._grpc_delete_object_events', return_value=None)
    @mock.patch('shakenfist.mariadb._use_database_service', return_value=True)
    def test_uuid_object_is_coerced_to_str(
            self, mock_use_svc, mock_grpc, mock_direct):
        """``hard_delete`` already stringifies its caller, but the public
        API is permissive for symmetry with ``get_object_events``: a
        ``uuid.UUID`` is coerced before reaching the gRPC layer.
        """
        import uuid as _uuid
        u = _uuid.UUID('b2d0e260-423f-4387-9f5e-179603c254ba')
        mariadb.delete_object_events('instance', u)
        mock_grpc.assert_called_once_with('instance', str(u))


# ---------------------------------------------------------------------------
# Phase 4 tests — hard_delete calls delete_object_events
# ---------------------------------------------------------------------------

class HardDeleteEventsCleanupTestCase(base.ShakenFistTestCase):
    """hard_delete() must call mariadb.delete_object_events for the object."""

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    @mock.patch('shakenfist.mariadb.delete_object_metadata', return_value=True)
    @mock.patch('shakenfist.mariadb.delete_state', return_value=True)
    def test_hard_delete_calls_delete_object_events(
            self, mock_del_state, mock_del_meta, mock_del_events, mock_event):
        """hard_delete() calls delete_object_events(object_type, str(uuid))."""
        from shakenfist.baseobject import DatabaseBackedObject

        TEST_UUID = '12345678-1234-4321-8234-123456789099'
        obj = DatabaseBackedObject(TEST_UUID)

        obj.hard_delete()

        mock_del_events.assert_called_once_with(obj.object_type, TEST_UUID)
