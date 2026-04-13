# Copyright 2026 Michael Still and contributors
#
# Tests for the public mariadb.enqueue_work_item /
# dequeue_work_item / resolve_work_item / get_work_queue_length /
# restart_work_queue wrappers introduced in phase 6 of the
# etcd-removal ops-queues plan.
#
# These cover the three-layer dispatch (direct vs. gRPC branches)
# and the defaulting behaviour of the public wrappers. The
# underlying _direct_work_queue_* and _grpc_work_queue_* helpers
# are tested separately.

from unittest import mock

from shakenfist import mariadb
from shakenfist.tests import base


class PublicWorkQueueDispatchTestCase(base.ShakenFistTestCase):
    """Verify the three-layer dispatch for the queue public API."""

    @mock.patch('shakenfist.mariadb._grpc_work_queue_enqueue')
    @mock.patch('shakenfist.mariadb._direct_work_queue_enqueue')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    def test_enqueue_service_mode_goes_to_grpc(
            self, _svc, direct, grpc):
        mariadb.enqueue_work_item('q', {'a': 1}, delay=2.5)
        grpc.assert_called_once_with('q', {'a': 1}, 2.5)
        direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_enqueue')
    @mock.patch('shakenfist.mariadb._direct_work_queue_enqueue')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_enqueue_direct_mode_goes_to_direct(
            self, _svc, direct, grpc):
        mariadb.enqueue_work_item('q', {'a': 1})
        direct.assert_called_once_with('q', {'a': 1}, 0.0)
        grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_dequeue')
    @mock.patch('shakenfist.mariadb._direct_work_queue_dequeue')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    def test_dequeue_service_mode_goes_to_grpc(
            self, _svc, direct, grpc):
        grpc.return_value = ('42', {'payload': True})
        result = mariadb.dequeue_work_item('q')
        self.assertEqual(('42', {'payload': True}), result)
        grpc.assert_called_once_with('q')
        direct.assert_not_called()

    @mock.patch('shakenfist.mariadb.config')
    @mock.patch('shakenfist.mariadb._grpc_work_queue_dequeue')
    @mock.patch('shakenfist.mariadb._direct_work_queue_dequeue')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_dequeue_direct_mode_defaults_worker_to_node_name(
            self, _svc, direct, grpc, mock_config):
        mock_config.NODE_NAME = 'node-alpha'
        direct.return_value = ('7', {'op': 'x'})

        result = mariadb.dequeue_work_item('q')

        self.assertEqual(('7', {'op': 'x'}), result)
        direct.assert_called_once_with('q', 'node-alpha')
        grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_work_queue_dequeue')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_dequeue_returns_none_when_empty(self, _svc, direct):
        direct.return_value = None
        self.assertIsNone(mariadb.dequeue_work_item('q'))

    @mock.patch('shakenfist.mariadb._grpc_work_queue_resolve')
    @mock.patch('shakenfist.mariadb._direct_work_queue_resolve')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    def test_resolve_service_mode_goes_to_grpc(
            self, _svc, direct, grpc):
        mariadb.resolve_work_item('q', '42')
        grpc.assert_called_once_with('q', '42')
        direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_resolve')
    @mock.patch('shakenfist.mariadb._direct_work_queue_resolve')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_resolve_direct_mode_goes_to_direct(
            self, _svc, direct, grpc):
        mariadb.resolve_work_item('q', '42')
        direct.assert_called_once_with('q', '42')
        grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_length')
    @mock.patch('shakenfist.mariadb._direct_work_queue_length')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    def test_length_service_mode_goes_to_grpc(
            self, _svc, direct, grpc):
        grpc.return_value = (1, 2, 3)
        self.assertEqual(
            (1, 2, 3), mariadb.get_work_queue_length('q'))
        direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_length')
    @mock.patch('shakenfist.mariadb._direct_work_queue_length')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_length_direct_mode_goes_to_direct(
            self, _svc, direct, grpc):
        direct.return_value = (4, 5, 6)
        self.assertEqual(
            (4, 5, 6), mariadb.get_work_queue_length('q'))
        grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_restart')
    @mock.patch('shakenfist.mariadb._direct_work_queue_restart')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    def test_restart_service_mode_goes_to_grpc(
            self, _svc, direct, grpc):
        mariadb.restart_work_queue('q')
        grpc.assert_called_once_with('q')
        direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_restart')
    @mock.patch('shakenfist.mariadb._direct_work_queue_restart')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_restart_direct_mode_goes_to_direct(
            self, _svc, direct, grpc):
        mariadb.restart_work_queue('q')
        direct.assert_called_once_with('q')
        grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_list_stuck')
    @mock.patch('shakenfist.mariadb._direct_work_queue_list_stuck')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    def test_list_stuck_service_mode_goes_to_grpc(
            self, _svc, direct, grpc):
        grpc.return_value = [{'id': 1}]
        self.assertEqual(
            [{'id': 1}], mariadb.list_stuck_work_queue_rows(60.0))
        grpc.assert_called_once_with(60.0)
        direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_list_stuck')
    @mock.patch('shakenfist.mariadb._direct_work_queue_list_stuck')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_list_stuck_direct_mode_goes_to_direct(
            self, _svc, direct, grpc):
        direct.return_value = []
        self.assertEqual([], mariadb.list_stuck_work_queue_rows(30.0))
        direct.assert_called_once_with(30.0)
        grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_clear_claim')
    @mock.patch('shakenfist.mariadb._direct_work_queue_clear_claim')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    def test_clear_claim_service_mode_goes_to_grpc(
            self, _svc, direct, grpc):
        grpc.return_value = True
        self.assertTrue(mariadb.clear_work_queue_claim(42))
        grpc.assert_called_once_with(42)
        direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_clear_claim')
    @mock.patch('shakenfist.mariadb._direct_work_queue_clear_claim')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_clear_claim_direct_mode_goes_to_direct(
            self, _svc, direct, grpc):
        direct.return_value = False
        self.assertFalse(mariadb.clear_work_queue_claim(42))
        direct.assert_called_once_with(42)
        grpc.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_delete_row')
    @mock.patch('shakenfist.mariadb._direct_work_queue_delete_row')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=True)
    def test_delete_row_service_mode_goes_to_grpc(
            self, _svc, direct, grpc):
        grpc.return_value = True
        self.assertTrue(mariadb.delete_work_queue_row(42))
        grpc.assert_called_once_with(42)
        direct.assert_not_called()

    @mock.patch('shakenfist.mariadb._grpc_work_queue_delete_row')
    @mock.patch('shakenfist.mariadb._direct_work_queue_delete_row')
    @mock.patch(
        'shakenfist.mariadb._use_database_service',
        return_value=False)
    def test_delete_row_direct_mode_goes_to_direct(
            self, _svc, direct, grpc):
        direct.return_value = True
        self.assertTrue(mariadb.delete_work_queue_row(42))
        direct.assert_called_once_with(42)
        grpc.assert_not_called()


class MockEtcdWorkQueueTestCase(base.ShakenFistTestCase):
    """Verify the MockEtcd work_queue_store round trip.

    These tests exercise the mock's own implementation of the
    public queue API end to end through mariadb.enqueue_work_item
    / dequeue_work_item / resolve_work_item, so every consumer
    that drives work through MockEtcd continues to see a working
    queue after phase 6.
    """

    def setUp(self):
        super().setUp()
        # Import here to avoid a cycle at module load time.
        from shakenfist.tests.mock_etcd import MockEtcd
        self.mock_etcd = MockEtcd(self, node_count=1)
        self.mock_etcd.setup()

    def test_enqueue_dequeue_resolve_round_trip(self):
        mariadb.enqueue_work_item('q1', {'task': 't1'})
        mariadb.enqueue_work_item('q1', {'task': 't2'})
        mariadb.enqueue_work_item('q2', {'task': 'other'})

        processing, queued, deferred = mariadb.get_work_queue_length(
            'q1')
        self.assertEqual((0, 2, 0), (processing, queued, deferred))

        job1 = mariadb.dequeue_work_item('q1')
        self.assertIsNotNone(job1)
        name1, payload1 = job1
        self.assertEqual({'task': 't1'}, payload1)

        processing, queued, deferred = mariadb.get_work_queue_length(
            'q1')
        self.assertEqual((1, 1, 0), (processing, queued, deferred))

        job2 = mariadb.dequeue_work_item('q1')
        self.assertIsNotNone(job2)
        _, payload2 = job2
        self.assertEqual({'task': 't2'}, payload2)

        self.assertIsNone(mariadb.dequeue_work_item('q1'))

        mariadb.resolve_work_item('q1', name1)
        processing, queued, deferred = mariadb.get_work_queue_length(
            'q1')
        self.assertEqual((1, 0, 0), (processing, queued, deferred))

    def test_deferred_job_is_not_dequeued_until_ready(self):
        mariadb.enqueue_work_item('q', {'task': 'later'}, delay=60.0)
        self.assertIsNone(mariadb.dequeue_work_item('q'))
        processing, queued, deferred = mariadb.get_work_queue_length(
            'q')
        self.assertEqual((0, 0, 1), (processing, queued, deferred))

    def test_restart_clears_claims(self):
        mariadb.enqueue_work_item('q', {'task': 't'})
        name, _ = mariadb.dequeue_work_item('q')
        self.assertIsNotNone(name)

        mariadb.restart_work_queue('q')
        processing, queued, _ = mariadb.get_work_queue_length('q')
        self.assertEqual(0, processing)
        self.assertEqual(1, queued)

        name2, _ = mariadb.dequeue_work_item('q')
        self.assertIsNotNone(name2)
