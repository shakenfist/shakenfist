# Copyright 2026 Michael Still and contributors
#
# Tests for the stuck-job reaper introduced in phase 7 of the
# etcd-removal ops-queues plan. The reaper walks mariadb
# work_queue rows whose claim has gone stale and either returns
# them to the queue or rejects them after max_attempts claims.

import time
from unittest import mock

from prometheus_client import REGISTRY

from shakenfist.config import config
from shakenfist.daemons.cluster import scheduled_tasks
from shakenfist.exceptions import InvalidStateException
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


def _counter_value(name):
    return REGISTRY.get_sample_value(name) or 0.0


class StuckJobReaperTestCase(base.ShakenFistTestCase):
    """Exercises the reaper against MockMariaDB's work_queue_store."""

    def setUp(self):
        super().setUp()

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        self.config_patches = [
            mock.patch.object(
                config, 'CLUSTER_OP_STUCK_THRESHOLD', 1800),
            mock.patch.object(
                config, 'CLUSTER_OP_MAX_ATTEMPTS', 5),
        ]
        for p in self.config_patches:
            p.start()
            self.addCleanup(p.stop)

        self.requeued_start = _counter_value(
            'cluster_op_reaper_requeued_total')
        self.rejected_start = _counter_value(
            'cluster_op_reaper_rejected_total')

    def _add_row(self, row_id, queue_name, claimed_at,
                 claimed_by='worker-a', attempts=1, payload=None):
        row = {
            'id': row_id,
            'queue_name': queue_name,
            'scheduled_at': claimed_at - 5.0,
            'claimed_at': claimed_at,
            'claimed_by': claimed_by,
            'attempts': attempts,
            'payload': payload or {},
            'created_at': claimed_at - 10.0,
        }
        self.mock_mariadb.work_queue_store.append(row)
        return row

    def _requeued_delta(self):
        return (_counter_value('cluster_op_reaper_requeued_total')
                - self.requeued_start)

    def _rejected_delta(self):
        return (_counter_value('cluster_op_reaper_rejected_total')
                - self.rejected_start)

    def test_empty_store_is_a_noop(self):
        scheduled_tasks.reap_stuck_cluster_operation_jobs()
        self.assertEqual(0, self._requeued_delta())
        self.assertEqual(0, self._rejected_delta())

    def test_fresh_claim_is_skipped(self):
        now = time.time()
        row = self._add_row(
            row_id=1, queue_name='q', claimed_at=now - 1.0,
            attempts=1)

        scheduled_tasks.reap_stuck_cluster_operation_jobs()

        self.assertEqual(0, self._requeued_delta())
        self.assertEqual(0, self._rejected_delta())
        # Row untouched.
        self.assertEqual(row['claimed_at'], now - 1.0)

    def test_stuck_under_max_attempts_is_requeued(self):
        now = time.time()
        row = self._add_row(
            row_id=42, queue_name='q',
            claimed_at=now - 3600.0, attempts=2,
            payload={
                'operation_type': 'node_blob_op',
                'operation_uuid': 'aaaa',
            })

        scheduled_tasks.reap_stuck_cluster_operation_jobs()

        self.assertEqual(1, self._requeued_delta())
        self.assertEqual(0, self._rejected_delta())
        # The reaper clears claim but does NOT touch attempts.
        self.assertIsNone(row['claimed_at'])
        self.assertIsNone(row['claimed_by'])
        self.assertEqual(2, row['attempts'])
        # And it did NOT delete the row.
        self.assertEqual(1, len(self.mock_mariadb.work_queue_store))

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_stuck_at_max_attempts_is_rejected_and_errored(
            self, mock_get_class):
        mock_op = mock.MagicMock()
        mock_class = mock.MagicMock()
        mock_class.from_db.return_value = mock_op
        mock_get_class.return_value = mock_class

        now = time.time()
        self._add_row(
            row_id=42, queue_name='q',
            claimed_at=now - 3600.0, attempts=5,
            payload={
                'operation_type': 'node_blob_op',
                'operation_uuid': 'aaaa',
            })

        scheduled_tasks.reap_stuck_cluster_operation_jobs()

        self.assertEqual(0, self._requeued_delta())
        self.assertEqual(1, self._rejected_delta())
        # Row is gone.
        self.assertEqual(0, len(self.mock_mariadb.work_queue_store))
        # State transition and audit event fired on the op.
        mock_class.from_db.assert_called_once_with('aaaa')
        self.assertEqual(
            BaseClusterOperation.STATE_ERROR, mock_op.state)
        mock_op.add_event.assert_called_once()
        event_args = mock_op.add_event.call_args
        self.assertIn('exceeded', event_args[0][1])
        self.assertEqual(5, event_args.kwargs['extra']['attempts'])

    @mock.patch(
        'shakenfist.daemons.cluster.scheduled_tasks.get_object_class')
    def test_rejection_handles_invalid_state_gracefully(
            self, mock_get_class):
        # If the underlying operation is already in a terminal
        # state, the state transition raises. The reaper should
        # log and continue, not abort the whole tick.
        mock_op = mock.MagicMock()
        type(mock_op).state = mock.PropertyMock(
            side_effect=InvalidStateException('already terminal'))
        mock_class = mock.MagicMock()
        mock_class.from_db.return_value = mock_op
        mock_get_class.return_value = mock_class

        now = time.time()
        self._add_row(
            row_id=1, queue_name='q', claimed_at=now - 3600.0,
            attempts=5,
            payload={
                'operation_type': 'node_blob_op',
                'operation_uuid': 'xyz',
            })
        self._add_row(
            row_id=2, queue_name='q', claimed_at=now - 3600.0,
            attempts=2,
            payload={
                'operation_type': 'node_blob_op',
                'operation_uuid': 'pqr',
            })

        scheduled_tasks.reap_stuck_cluster_operation_jobs()

        # Row 1 was rejected (counter incremented, row deleted)
        # even though the state transition raised.
        self.assertEqual(1, self._rejected_delta())
        # Row 2 continued to be re-queued.
        self.assertEqual(1, self._requeued_delta())
        remaining_ids = [
            r['id'] for r in self.mock_mariadb.work_queue_store]
        self.assertEqual([2], remaining_ids)

    def test_persistently_crashing_job_is_rejected_on_cycle_n(self):
        # Simulates a persistently-crashing job: every cycle it
        # gets dequeued, crashes (claimed_at stays stuck), the
        # reaper notices, re-queues it. On the Nth cycle its
        # attempts column reaches CLUSTER_OP_MAX_ATTEMPTS and
        # the reaper rejects it.
        now = time.time()
        row = self._add_row(
            row_id=99, queue_name='q',
            claimed_at=now - 3600.0, attempts=1,
            payload={
                'operation_type': 'node_blob_op',
                'operation_uuid': 'crashy',
            })

        with mock.patch(
                'shakenfist.daemons.cluster.scheduled_tasks'
                '.get_object_class') as mock_get_class:
            mock_op = mock.MagicMock()
            mock_class = mock.MagicMock()
            mock_class.from_db.return_value = mock_op
            mock_get_class.return_value = mock_class

            for cycle in range(1, config.CLUSTER_OP_MAX_ATTEMPTS):
                # Row currently has attempts=cycle, under the limit.
                scheduled_tasks.reap_stuck_cluster_operation_jobs()
                # Reaper re-queued it. Now simulate "a worker
                # picked it up again and immediately crashed"
                # by advancing attempts and re-stamping
                # claimed_at into the stuck past.
                self.assertIsNone(row['claimed_at'])
                row['claimed_at'] = time.time() - 3600.0
                row['claimed_by'] = 'worker-retry'
                row['attempts'] = cycle + 1

            self.assertEqual(
                config.CLUSTER_OP_MAX_ATTEMPTS - 1,
                self._requeued_delta())
            self.assertEqual(0, self._rejected_delta())

            # Final cycle: attempts has now reached max.
            scheduled_tasks.reap_stuck_cluster_operation_jobs()

            self.assertEqual(1, self._rejected_delta())
            self.assertEqual(
                0, len(self.mock_mariadb.work_queue_store))
            self.assertEqual(
                BaseClusterOperation.STATE_ERROR, mock_op.state)
