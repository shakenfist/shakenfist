# Copyright 2026 Michael Still and contributors
#
# Tests for the data migration that drains residual /sf/event/*
# etcd keys into the MariaDB event_dlq table.

from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd
from shakenfist.util import json as util_json


EVENT_A = {
    'timestamp': 1000.0,
    'event_type': 'audit',
    'object_type': 'instance',
    'object_uuid': 'uuid-aaa',
    'fqdn': 'node1',
    'duration': None,
    'message': 'instance created',
    'extra': None,
    'correlation_id': None,
}

EVENT_B = {
    'timestamp': 2000.0,
    'event_type': 'mutate',
    'object_type': 'network',
    'object_uuid': 'uuid-bbb',
    'fqdn': 'node2',
    'duration': 0.5,
    'message': 'network deleted',
    'extra': {'reason': 'cleanup'},
    'correlation_id': 'corr-123',
}


class EventDlqDrainTestCase(base.ShakenFistTestCase):
    """Exercises _migrate_etcd_event_dlq."""

    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

        self.enqueued = []

        def _fake_enqueue(object_type, object_uuid,
                          event_timestamp, event_json):
            self.enqueued.append({
                'object_type': object_type,
                'object_uuid': object_uuid,
                'event_timestamp': event_timestamp,
                'event_json': event_json,
            })

        self.enqueue_patch = mock.patch(
            'shakenfist.mariadb._direct_enqueue_event_dlq',
            side_effect=_fake_enqueue)
        self.enqueue_patch.start()
        self.addCleanup(self.enqueue_patch.stop)

    def _seed_etcd(self, path, data):
        self.mock_etcd.db[path] = util_json.json_dump(data).encode()

    def test_happy_path_two_events(self):
        """Two well-formed events are migrated."""
        self._seed_etcd(
            '/sf/event/instance/uuid-aaa/1000.0', EVENT_A)
        self._seed_etcd(
            '/sf/event/network/uuid-bbb/2000.0', EVENT_B)

        result = mariadb._migrate_etcd_event_dlq(None)

        self.assertEqual(result['migrated_count'], 2)
        self.assertEqual(result['error_count'], 0)
        self.assertEqual(len(self.enqueued), 2)

        # Verify parsed correctly
        types = {e['object_type'] for e in self.enqueued}
        self.assertIn('instance', types)
        self.assertIn('network', types)

        # Verify etcd keys deleted
        self.assertNotIn(
            '/sf/event/instance/uuid-aaa/1000.0',
            self.mock_etcd.db)
        self.assertNotIn(
            '/sf/event/network/uuid-bbb/2000.0',
            self.mock_etcd.db)

    def test_empty_etcd_is_noop(self):
        result = mariadb._migrate_etcd_event_dlq(None)
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 0)

    def test_idempotency(self):
        self._seed_etcd(
            '/sf/event/instance/uuid-aaa/1000.0', EVENT_A)

        result1 = mariadb._migrate_etcd_event_dlq(None)
        self.assertEqual(result1['migrated_count'], 1)

        result2 = mariadb._migrate_etcd_event_dlq(None)
        self.assertEqual(result2['migrated_count'], 0)

    def test_malformed_payload_skipped(self):
        self.mock_etcd.db['/sf/event/bad/key/1.0'] = (
            b'"just a string"')

        result = mariadb._migrate_etcd_event_dlq(None)
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 1)
        self.assertIn(
            '/sf/event/bad/key/1.0', self.mock_etcd.db)

    def test_bad_key_format_skipped(self):
        """A key with too few parts is skipped."""
        self._seed_etcd('/sf/event/short', EVENT_A)

        result = mariadb._migrate_etcd_event_dlq(None)
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 1)

    def test_bad_timestamp_skipped(self):
        """A key with a non-float timestamp is skipped."""
        self._seed_etcd(
            '/sf/event/instance/uuid/not_a_float', EVENT_A)

        result = mariadb._migrate_etcd_event_dlq(None)
        self.assertEqual(result['migrated_count'], 0)
        self.assertEqual(result['error_count'], 1)

    def test_event_data_preserved(self):
        """Event JSON payload is preserved through migration."""
        self._seed_etcd(
            '/sf/event/network/uuid-bbb/2000.0', EVENT_B)

        mariadb._migrate_etcd_event_dlq(None)

        self.assertEqual(len(self.enqueued), 1)
        row = self.enqueued[0]
        self.assertEqual(row['object_type'], 'network')
        self.assertEqual(row['object_uuid'], 'uuid-bbb')
        self.assertAlmostEqual(row['event_timestamp'], 2000.0)
        self.assertEqual(row['event_json']['message'],
                         'network deleted')
        self.assertEqual(row['event_json']['extra'],
                         {'reason': 'cleanup'})
