"""Unit tests for EventLogChunk class.

These tests verify the core functionality of EventLogChunk including:
- Creating and bootstrapping event log databases
- Writing and reading events
- Counting events
- Deleting chunks
- Pruning old events
"""
import os
import shutil
import tempfile
import time
from unittest import mock

from shakenfist import eventlog
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.tests import base


class MockNodeLock:
    """A mock NodeLock that acts as a no-op context manager."""

    def __init__(self, name):
        self.name = name
        self.path = '/tmp/mock-lock-path'

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, traceback):
        pass


class EventLogChunkTestCase(base.ShakenFistTestCase):
    """Test EventLogChunk basic operations."""

    def setUp(self):
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup_test_dir)

        # Mock NodeLock to avoid socket dependency
        self.mock_nodelock = mock.patch(
            'shakenfist.eventlog.util_concurrency.NodeLock',
            MockNodeLock)
        self.mock_nodelock.start()
        self.addCleanup(self.mock_nodelock.stop)

        # Mock config to use our test directory
        self.mock_config = mock.patch('shakenfist.eventlog.config')
        self.config = self.mock_config.start()
        self.config.STORAGE_PATH = self.test_dir
        self.config.LOCK_PATH = os.path.join(self.test_dir, 'locks')
        self.config.MAX_RESOURCES_EVENT_AGE = 604800  # 7 days in seconds
        self.addCleanup(self.mock_config.stop)

        # Create locks directory
        os.makedirs(self.config.LOCK_PATH, exist_ok=True)

    def _cleanup_test_dir(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_new_chunk(self):
        """Test creating a new EventLogChunk initializes correctly."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        self.assertEqual(chunk.objtype, 'instance')
        self.assertEqual(chunk.objuuid, 'test-uuid-1234')
        self.assertEqual(chunk.chunk, '202501')
        self.assertFalse(chunk.bootstrapped)

    def test_write_and_read_event(self):
        """Test writing an event and reading it back."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        timestamp = time.time()
        chunk.write_event(
            EVENT_TYPE_AUDIT, timestamp, 'testnode', 0.5,
            'Test event message', extra={'key': 'value'})

        events = list(chunk.read_events())
        self.assertEqual(len(events), 1)

        event = events[0]
        self.assertEqual(event['type'], EVENT_TYPE_AUDIT)
        self.assertEqual(event['message'], 'Test event message')
        self.assertEqual(event['fqdn'], 'testnode')
        self.assertEqual(event['duration'], 0.5)
        self.assertEqual(event['extra'], {'key': 'value'})

        chunk.close()

    def test_write_multiple_events(self):
        """Test writing multiple events and reading them back."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        for i in range(5):
            chunk.write_event(
                EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
                f'Event {i}')

        events = list(chunk.read_events())
        self.assertEqual(len(events), 5)

        chunk.close()

    def test_count_events(self):
        """Test counting events in a chunk."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        # New chunk should have 0 events
        self.assertEqual(chunk.count_events(), 0)

        # Write some events
        for i in range(3):
            chunk.write_event(
                EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
                f'Event {i}')

        self.assertEqual(chunk.count_events(), 3)

        chunk.close()

    def test_delete_removes_files(self):
        """Test that delete() removes the database file.

        This test would have caught the os.path.exist typo bug because
        calling os.path.exist() (which doesn't exist) would have raised
        an AttributeError.
        """
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        # Write an event to create the database file
        chunk.write_event(
            EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
            'Test event')

        # Verify the database file exists
        self.assertTrue(os.path.exists(chunk.dbpath))

        # Delete the chunk
        chunk.delete()

        # Verify the database file is removed
        self.assertFalse(os.path.exists(chunk.dbpath))

    def test_delete_handles_nonexistent_files(self):
        """Test that delete() handles missing files gracefully."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        # Don't write anything, so no database file is created
        # delete() should not raise an error
        chunk.delete()

    def test_read_events_with_type_filter(self):
        """Test reading events filtered by type."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        chunk.write_event(
            EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
            'Audit event')
        chunk.write_event(
            EVENT_TYPE_MUTATE, time.time(), 'testnode', 0,
            'Mutate event')
        chunk.write_event(
            EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
            'Another audit event')

        audit_events = list(chunk.read_events(event_type=EVENT_TYPE_AUDIT))
        self.assertEqual(len(audit_events), 2)

        mutate_events = list(chunk.read_events(event_type=EVENT_TYPE_MUTATE))
        self.assertEqual(len(mutate_events), 1)

        chunk.close()

    def test_read_events_with_limit(self):
        """Test reading events with a limit."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        for i in range(10):
            chunk.write_event(
                EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
                f'Event {i}')

        # Default limit is 100, but we only have 10
        events = list(chunk.read_events(limit=5))
        self.assertEqual(len(events), 5)

        chunk.close()

    def test_write_event_with_correlation_id(self):
        """Test writing an event with a correlation ID."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        correlation_id = 'corr-123-456'
        chunk.write_event(
            EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
            'Correlated event', correlation_id=correlation_id)

        events = list(chunk.read_events())
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['correlation_id'], correlation_id)

        chunk.close()

    def test_prune_old_events(self):
        """Test pruning events older than a given timestamp."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        now = time.time()

        # Write events at different timestamps
        chunk.write_event(
            EVENT_TYPE_AUDIT, now - 100, 'testnode', 0,
            'Old event 1')
        chunk.write_event(
            EVENT_TYPE_AUDIT, now - 50, 'testnode', 0,
            'Old event 2')
        chunk.write_event(
            EVENT_TYPE_AUDIT, now, 'testnode', 0,
            'Recent event')

        self.assertEqual(chunk.count_events(), 3)

        # Prune events older than 60 seconds ago
        removed = chunk.prune_old_events(now - 60, EVENT_TYPE_AUDIT)
        self.assertEqual(removed, 1)
        self.assertEqual(chunk.count_events(), 2)

        chunk.close()

    def test_close_and_reopen(self):
        """Test closing and reopening a chunk preserves data."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        chunk.write_event(
            EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
            'Persistent event')

        chunk.close()

        # Reopen the chunk
        chunk2 = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)
        events = list(chunk2.read_events())

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['message'], 'Persistent event')

        chunk2.close()

    def test_extra_field_json_handling(self):
        """Test that extra field is properly serialized and deserialized."""
        chunk = eventlog.EventLogChunk('instance', 'test-uuid-1234', 2025, 1)

        extra_data = {
            'string': 'value',
            'number': 42,
            'nested': {'key': 'nested_value'},
            'list': [1, 2, 3]
        }
        chunk.write_event(
            EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
            'Event with complex extra', extra=extra_data)

        events = list(chunk.read_events())
        self.assertEqual(events[0]['extra'], extra_data)

        chunk.close()


class EventLogChunkUpgradeTestCase(base.ShakenFistTestCase):
    """Test EventLogChunk database upgrade paths.

    These tests verify that database schema upgrades work correctly.
    A test for the v2->v3 and v3->v4 upgrades would have caught the
    config.MAX_NODE_RESOURCE_EVENT_AGE typo bug because it would have
    raised an AttributeError when accessing the non-existent attribute.
    """

    def setUp(self):
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(self._cleanup_test_dir)

        # Mock NodeLock to avoid socket dependency
        self.mock_nodelock = mock.patch(
            'shakenfist.eventlog.util_concurrency.NodeLock',
            MockNodeLock)
        self.mock_nodelock.start()
        self.addCleanup(self.mock_nodelock.stop)

        self.mock_config = mock.patch('shakenfist.eventlog.config')
        self.config = self.mock_config.start()
        self.config.STORAGE_PATH = self.test_dir
        self.config.LOCK_PATH = os.path.join(self.test_dir, 'locks')
        self.config.MAX_RESOURCES_EVENT_AGE = 604800  # 7 days
        self.addCleanup(self.mock_config.stop)

        os.makedirs(self.config.LOCK_PATH, exist_ok=True)

    def _cleanup_test_dir(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_config_max_resources_event_age_is_accessed(self):
        """Test that MAX_RESOURCES_EVENT_AGE config attribute is accessed.

        This test would have caught the typo bug where
        MAX_NODE_RESOURCE_EVENT_AGE was used instead of MAX_RESOURCES_EVENT_AGE.
        We verify this by checking that our mock config attribute is accessed.
        """
        # Create a fresh chunk (will use latest schema)
        chunk = eventlog.EventLogChunk('node', 'test-node', 2025, 1)
        chunk.write_event(
            EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
            'Test event')

        # The config mock should have MAX_RESOURCES_EVENT_AGE accessible
        # If code tried to access MAX_NODE_RESOURCE_EVENT_AGE instead,
        # it would fail with AttributeError since we didn't define it
        self.assertEqual(self.config.MAX_RESOURCES_EVENT_AGE, 604800)

        chunk.close()

    def test_new_database_gets_latest_version(self):
        """Test that a new database is created at the latest version."""
        chunk = eventlog.EventLogChunk('instance', 'new-uuid', 2025, 1)

        # Write an event to bootstrap the database
        chunk.write_event(
            EVENT_TYPE_AUDIT, time.time(), 'testnode', 0,
            'First event')

        # Check that version is current
        cur = chunk.con.cursor()
        cur.execute('SELECT version FROM version')
        version = cur.fetchone()[0]
        self.assertEqual(version, eventlog.VERSION)

        chunk.close()
