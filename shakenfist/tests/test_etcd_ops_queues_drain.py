# Copyright 2026 Michael Still and contributors
#
# Tests for the phase 8 data migration that drains residual
# /sf/{op_type}/*, /sf/queue/* and /sf/processing/* etcd keys into
# the MariaDB cluster_operations and work_queue tables.
#
# These tests seed MockEtcd's fake etcd dict and patch
# _direct_create_cluster_operation / _direct_work_queue_enqueue so
# the migration writes into in-memory stores instead of a real
# MariaDB. The stored shape matches what phase 1/2 _direct_* functions
# would have written.

import time
from unittest import mock
from uuid import uuid4

from shakenfist import mariadb
from shakenfist.constants import OPERATION_NAMES_TO_CLASSES
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd
from shakenfist.util import json as util_json


class EtcdOpsQueuesDrainTestCase(base.ShakenFistTestCase):
    """Exercises _migrate_etcd_cluster_operations / _migrate_etcd_work_queue."""

    def setUp(self):
        super().setUp()

        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

        self.created_ops = {}
        self.enqueued_rows = []

        def _fake_create_cluster_operation(
                op_uuid, operation_type, metadata, created_at):
            key = str(op_uuid)
            if key in self.created_ops:
                return False
            self.created_ops[key] = {
                'operation_type': operation_type,
                'metadata': dict(metadata),
                'created_at': created_at,
            }
            return True

        def _fake_work_queue_enqueue(queue_name, payload, delay=0.0):
            self.enqueued_rows.append({
                'queue_name': queue_name,
                'payload': dict(payload),
                'delay': delay,
                'enqueued_at': time.time(),
            })

        self.create_patch = mock.patch(
            'shakenfist.mariadb._direct_create_cluster_operation',
            side_effect=_fake_create_cluster_operation)
        self.create_patch.start()
        self.addCleanup(self.create_patch.stop)

        self.enqueue_patch = mock.patch(
            'shakenfist.mariadb._direct_work_queue_enqueue',
            side_effect=_fake_work_queue_enqueue)
        self.enqueue_patch.start()
        self.addCleanup(self.enqueue_patch.stop)

    def _seed_etcd(self, path, data):
        """Write a json-encoded entry to MockEtcd's fake store."""
        self.mock_etcd.db[path] = util_json.json_dump(data).encode()

    # ------------------------------------------------------------
    # cluster_operations drain
    # ------------------------------------------------------------

    def test_cluster_operations_drain_happy_path(self):
        uuids = []
        for op_type in OPERATION_NAMES_TO_CLASSES:
            u = uuid4()
            uuids.append((op_type, u))
            self._seed_etcd(
                f'/sf/{op_type}/{u}',
                {
                    'uuid': str(u),
                    'operation_type': op_type,
                    'created_at': 1000.0,
                    'priority': 'user_facing',
                })

        result = mariadb._migrate_etcd_cluster_operations(
            mock.MagicMock())

        self.assertEqual(
            len(OPERATION_NAMES_TO_CLASSES), result['migrated_count'])
        self.assertEqual(0, result['error_count'])
        self.assertEqual(0, result['skipped_count'])
        self.assertEqual(len(OPERATION_NAMES_TO_CLASSES), len(self.created_ops))
        for op_type, u in uuids:
            self.assertIn(str(u), self.created_ops)
            self.assertEqual(
                op_type, self.created_ops[str(u)]['operation_type'])
            self.assertNotIn(f'/sf/{op_type}/{u}', self.mock_etcd.db)

    def test_cluster_operations_drain_is_idempotent(self):
        u = uuid4()
        self._seed_etcd(
            f'/sf/node_blob_op/{u}',
            {
                'uuid': str(u),
                'operation_type': 'node_blob_op',
                'created_at': 1000.0,
            })

        first = mariadb._migrate_etcd_cluster_operations(mock.MagicMock())
        self.assertEqual(1, first['migrated_count'])
        self.assertEqual(1, len(self.created_ops))

        # Second run has no etcd keys left, so migrated stays 0.
        second = mariadb._migrate_etcd_cluster_operations(mock.MagicMock())
        self.assertEqual(0, second['migrated_count'])
        self.assertEqual(0, second['skipped_count'])
        self.assertEqual(1, len(self.created_ops))

        # If a stray key with the same uuid were seeded again, the
        # insert would return False and the drain would count it as
        # skipped (but still clean up the etcd key).
        self._seed_etcd(
            f'/sf/node_blob_op/{u}',
            {
                'uuid': str(u),
                'operation_type': 'node_blob_op',
                'created_at': 1000.0,
            })
        third = mariadb._migrate_etcd_cluster_operations(mock.MagicMock())
        self.assertEqual(0, third['migrated_count'])
        self.assertEqual(1, third['skipped_count'])
        self.assertNotIn(f'/sf/node_blob_op/{u}', self.mock_etcd.db)

    # ------------------------------------------------------------
    # work_queue drain
    # ------------------------------------------------------------

    def test_work_queue_drain_moves_queued_rows(self):
        now = time.time()
        self._seed_etcd(
            f'/sf/queue/node-a/{now - 10.0}-abc',
            {'operation_type': 'node_blob_op', 'operation_uuid': 'uuid-1'})
        self._seed_etcd(
            f'/sf/queue/node-a/{now + 300.0}-def',
            {'operation_type': 'node_blob_op', 'operation_uuid': 'uuid-2'})

        result = mariadb._migrate_etcd_work_queue(mock.MagicMock())

        self.assertEqual(2, result['migrated_count'])
        self.assertEqual(0, result['error_count'])
        self.assertEqual(2, len(self.enqueued_rows))

        rows_by_op = {
            r['payload']['operation_uuid']: r for r in self.enqueued_rows}
        self.assertIn('uuid-1', rows_by_op)
        self.assertIn('uuid-2', rows_by_op)
        self.assertEqual('node-a', rows_by_op['uuid-1']['queue_name'])
        # A legacy timestamp in the past collapses to delay=0.
        self.assertEqual(0.0, rows_by_op['uuid-1']['delay'])
        # A legacy timestamp in the future keeps its scheduling.
        self.assertGreater(rows_by_op['uuid-2']['delay'], 100.0)

        # Both keys were removed from etcd.
        self.assertEqual(
            [], [k for k in self.mock_etcd.db if k.startswith('/sf/queue/')])

    def test_work_queue_drain_requeues_processing_rows(self):
        now = time.time()
        self._seed_etcd(
            f'/sf/processing/node-a/{now - 60.0}-xyz',
            {
                'operation_type': 'node_blob_op',
                'operation_uuid': 'in-flight',
            })

        result = mariadb._migrate_etcd_work_queue(mock.MagicMock())

        self.assertEqual(1, result['migrated_count'])
        self.assertEqual(1, len(self.enqueued_rows))
        row = self.enqueued_rows[0]
        self.assertEqual('node-a', row['queue_name'])
        self.assertEqual('in-flight', row['payload']['operation_uuid'])
        # Re-queued as a fresh enqueue: no claimed_at, no claimed_by.
        # _direct_work_queue_enqueue always inserts with attempts=0 /
        # claimed_at=None, which is what we are asserting by only
        # verifying the call parameters (the stub has no claim state).
        self.assertEqual(0.0, row['delay'])
        self.assertEqual(
            [], [k for k in self.mock_etcd.db
                 if k.startswith('/sf/processing/')])

    def test_work_queue_drain_ignores_cluster_operation_keys(self):
        now = time.time()
        u = uuid4()
        self._seed_etcd(
            f'/sf/queue/node-a/{now}-abc',
            {'operation_type': 'node_blob_op', 'operation_uuid': 'uuid-1'})
        self._seed_etcd(
            f'/sf/node_blob_op/{u}',
            {
                'uuid': str(u),
                'operation_type': 'node_blob_op',
                'created_at': now,
            })

        result = mariadb._migrate_etcd_work_queue(mock.MagicMock())

        self.assertEqual(1, result['migrated_count'])
        self.assertEqual(1, len(self.enqueued_rows))
        # The cluster_operations drain was not run, so the
        # /sf/node_blob_op/{u} key is untouched.
        self.assertIn(f'/sf/node_blob_op/{u}', self.mock_etcd.db)
        self.assertEqual(0, len(self.created_ops))
