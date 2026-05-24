# Copyright 2026 Michael Still and contributors
"""Unit tests for the eventlog drainer thread.

The thread itself is straightforward; the interesting bits are
the batch-building (translates spool payload dicts into the
gRPC ``EventMultiBatchRequest`` shape) and the failure-handling
branches (RPC failure backoff, ack=false fall-through to DLQ).
"""
import tempfile
from unittest import mock

import grpc

from shakenfist import eventlog_drainer
from shakenfist import eventlog_spool
from shakenfist.tests import base


class _SpoolRootMixin:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix='sf-drainer-test-')
        self._original_root = eventlog_spool.SPOOL_ROOT
        eventlog_spool.SPOOL_ROOT = self.tmp
        eventlog_spool.reset_for_tests()
        self.addCleanup(eventlog_spool.reset_for_tests)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        eventlog_spool.SPOOL_ROOT = self._original_root


class BuildBatchRequestTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """Translation of spool dicts into the gRPC batch message."""

    def setUp(self):
        super().setUp()
        self.thread = eventlog_drainer._DrainerThread()

    def test_minimal_event_translates_cleanly(self):
        payload = {
            'event_type': 'audit',
            'fqdn': 'sf-1',
            'message': 'hello',
            'extra': '{}',
            'timestamp': 1234.5,
            'objects': [],
        }
        req = self.thread._build_batch_request([(7, payload)])

        self.assertEqual(1, len(req.events))
        ev = req.events[0]
        self.assertEqual('audit', ev.event_type)
        self.assertEqual('sf-1', ev.fqdn)
        self.assertEqual('hello', ev.message)
        self.assertEqual(1234.5, ev.timestamp)
        # ``duration`` is optional so left at proto default
        # when payload had None.
        self.assertEqual(0, len(ev.objects))

    def test_multi_object_event_translates_all_objects(self):
        payload = {
            'event_type': 'audit',
            'fqdn': 'sf-1',
            'message': 'multi',
            'extra': '{}',
            'timestamp': 1.0,
            'objects': [
                {'object_type': 'instance', 'object_uuid': 'u1'},
                {'object_type': 'network', 'object_uuid': 'u2'},
            ],
        }
        req = self.thread._build_batch_request([(1, payload)])
        ev = req.events[0]
        self.assertEqual(2, len(ev.objects))
        self.assertEqual('instance', ev.objects[0].object_type)
        self.assertEqual('u1', ev.objects[0].object_uuid)
        self.assertEqual('network', ev.objects[1].object_type)
        self.assertEqual('u2', ev.objects[1].object_uuid)

    def test_multiple_events_in_one_batch(self):
        payloads = [
            (1, {
                'event_type': 'audit', 'fqdn': 's', 'message': 'a',
                'extra': '{}', 'timestamp': 1.0, 'objects': [],
            }),
            (2, {
                'event_type': 'mutate', 'fqdn': 's', 'message': 'b',
                'extra': '{}', 'timestamp': 2.0, 'objects': [],
            }),
        ]
        req = self.thread._build_batch_request(payloads)
        self.assertEqual(2, len(req.events))
        self.assertEqual('a', req.events[0].message)
        self.assertEqual('b', req.events[1].message)

    def test_duration_populated_when_not_none(self):
        payload = {
            'event_type': 'usage', 'fqdn': 's', 'message': 'x',
            'extra': '{}', 'timestamp': 1.0, 'objects': [],
            'duration': 3.5,
        }
        req = self.thread._build_batch_request([(1, payload)])
        self.assertAlmostEqual(3.5, req.events[0].duration, places=5)


class DrainOneBatchTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """Happy path, RPC failure, and ack=false branches."""

    def setUp(self):
        super().setUp()
        eventlog_spool.initialise('test-daemon')
        self.thread = eventlog_drainer._DrainerThread()

        # Pre-build a stub stub so we can swap responses
        # without standing up an actual gRPC server.
        self.mock_stub = mock.MagicMock()
        self.stub_patcher = mock.patch(
            'shakenfist.eventlog_drainer.event_pb2_grpc.'
            'EventServiceStub',
            return_value=self.mock_stub)
        self.stub_patcher.start()
        self.addCleanup(self.stub_patcher.stop)
        # The channel itself is never used directly by the
        # tests; stub-out so we don't try to open a connection.
        self.channel_patcher = mock.patch.object(
            self.thread, '_get_channel',
            return_value=mock.MagicMock())
        self.channel_patcher.start()
        self.addCleanup(self.channel_patcher.stop)

    def _enqueue(self, n):
        spool = eventlog_spool.get_spool()
        for i in range(n):
            spool.enqueue({
                'event_type': 'audit', 'fqdn': 's',
                'message': f'msg-{i}',
                'extra': '{}', 'timestamp': float(i),
                'objects': [],
            })

    def test_empty_spool_returns_zero(self):
        self.assertEqual(0, self.thread._drain_one_batch())
        self.mock_stub.RecordMultiEventBatch.assert_not_called()

    def test_happy_path_acks_and_deletes(self):
        self._enqueue(3)
        self.mock_stub.RecordMultiEventBatch.return_value = (
            mock.MagicMock(ack=True))

        drained = self.thread._drain_one_batch()
        self.assertEqual(3, drained)
        # All rows gone from the spool.
        self.assertEqual(0, eventlog_spool.get_spool().count())
        # One RPC for the batch.
        self.mock_stub.RecordMultiEventBatch.assert_called_once()

    def test_rpc_failure_holds_batch_and_backs_off(self):
        self._enqueue(2)
        rpc_err = grpc.RpcError()
        rpc_err.code = mock.MagicMock(
            return_value=mock.MagicMock(name='UNAVAILABLE'))
        self.mock_stub.RecordMultiEventBatch.side_effect = rpc_err

        with mock.patch.object(self.thread._stop_event, 'wait'):
            drained = self.thread._drain_one_batch()
        self.assertEqual(0, drained)
        # Rows still in spool.
        self.assertEqual(2, eventlog_spool.get_spool().count())

    def test_ack_false_falls_back_to_dlq(self):
        self._enqueue(2)
        self.mock_stub.RecordMultiEventBatch.return_value = (
            mock.MagicMock(ack=False))

        with mock.patch.object(self.thread._stop_event, 'wait'), \
                mock.patch(
                    'shakenfist.eventlog_drainer.mariadb.'
                    'enqueue_event_dlq') as mock_dlq:
            drained = self.thread._drain_one_batch()

        self.assertEqual(0, drained)
        # Both events handed to the DLQ as a last-ditch.
        self.assertEqual(2, mock_dlq.call_count)
        # And cleared from the local spool because the DLQ took
        # them (the contract is "events from this batch are
        # now someone else's problem, don't keep replaying
        # them").
        self.assertEqual(0, eventlog_spool.get_spool().count())

    def test_backoff_resets_after_successful_batch(self):
        self.thread._backoff = 16.0  # simulate prior failures

        self._enqueue(1)
        self.mock_stub.RecordMultiEventBatch.return_value = (
            mock.MagicMock(ack=True))
        self.thread._drain_one_batch()

        self.assertEqual(
            eventlog_drainer.BACKOFF_INITIAL,
            self.thread._backoff)

    def test_backoff_grows_on_repeated_failures(self):
        self._enqueue(1)
        rpc_err = grpc.RpcError()
        rpc_err.code = mock.MagicMock(
            return_value=mock.MagicMock(name='UNAVAILABLE'))
        self.mock_stub.RecordMultiEventBatch.side_effect = rpc_err

        with mock.patch.object(self.thread._stop_event, 'wait'):
            self.thread._drain_one_batch()
            after_one = self.thread._backoff
            self.thread._drain_one_batch()
            after_two = self.thread._backoff

        self.assertGreater(after_one, eventlog_drainer.BACKOFF_INITIAL)
        self.assertGreater(after_two, after_one)
        self.assertLessEqual(after_two, eventlog_drainer.BACKOFF_MAX)


class BatchSizeCapTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """A drain reads no more than DRAIN_BATCH_SIZE rows per RPC."""

    def setUp(self):
        super().setUp()
        eventlog_spool.initialise('test-daemon')
        self.thread = eventlog_drainer._DrainerThread()
        self.mock_stub = mock.MagicMock()
        self.mock_stub.RecordMultiEventBatch.return_value = (
            mock.MagicMock(ack=True))
        self.stub_patcher = mock.patch(
            'shakenfist.eventlog_drainer.event_pb2_grpc.'
            'EventServiceStub',
            return_value=self.mock_stub)
        self.stub_patcher.start()
        self.addCleanup(self.stub_patcher.stop)
        self.channel_patcher = mock.patch.object(
            self.thread, '_get_channel',
            return_value=mock.MagicMock())
        self.channel_patcher.start()
        self.addCleanup(self.channel_patcher.stop)

    def test_single_batch_is_capped(self):
        spool = eventlog_spool.get_spool()
        # Enqueue more than DRAIN_BATCH_SIZE.
        n = eventlog_drainer.DRAIN_BATCH_SIZE + 50
        for i in range(n):
            spool.enqueue({
                'event_type': 'audit', 'fqdn': 's',
                'message': f'msg-{i}',
                'extra': '{}', 'timestamp': float(i),
                'objects': [],
            })

        drained = self.thread._drain_one_batch()
        self.assertEqual(eventlog_drainer.DRAIN_BATCH_SIZE, drained)
        # The remainder is left for the next call.
        self.assertEqual(50, spool.count())
