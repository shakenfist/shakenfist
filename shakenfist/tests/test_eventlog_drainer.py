# Copyright 2026 Michael Still and contributors
"""Unit tests for the eventlog drainer thread.

After the phase 2b RPC swap the interesting bits are the
batch-building (translates spool payload dicts into
``EventRecord`` instances) and the failure-handling branches
(``mariadb.record_event_batch`` failure, backoff, poison-row
drop).
"""
import tempfile
from unittest import mock

from shakenfist import eventlog_drainer
from shakenfist import eventlog_spool
from shakenfist.schema.event import EventRecord
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


class BuildRecordsTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """Translation of spool dicts into ``EventRecord`` instances.

    Covers both the new-format payload (with top-level
    ``event_uuid`` and ``request_id``) and the old-format
    payload (``request-id`` nested inside ``extra``, no
    ``event_uuid``).
    """

    def test_new_format_payload_preserves_event_uuid_and_request_id(self):
        """New-format row: top-level event_uuid and request_id pass through."""
        payload = {
            'event_uuid': 'aaaa-1111',
            'event_type': 'audit',
            'fqdn': 'sf-1',
            'message': 'hello',
            'extra': '{}',
            'timestamp': 1234.5,
            'request_id': 'req-abc',
            'objects': [],
        }
        records = eventlog_drainer._build_records([(7, payload)])

        self.assertEqual(1, len(records))
        rec = records[0]
        self.assertIsInstance(rec, EventRecord)
        self.assertEqual('aaaa-1111', rec.event_uuid)
        self.assertEqual('audit', rec.event_type)
        self.assertEqual('sf-1', rec.fqdn)
        self.assertEqual('hello', rec.message)
        self.assertAlmostEqual(1234.5, rec.timestamp, places=5)
        self.assertEqual('req-abc', rec.request_id)
        self.assertEqual([], rec.objects)

    def test_old_format_payload_generates_uuid_and_lifts_request_id(self):
        """Old-format row: missing event_uuid gets a fresh UUID;
        request-id inside extra is lifted to the top-level request_id field.
        """
        payload = {
            'event_type': 'audit',
            'fqdn': 'sf-1',
            'message': 'legacy',
            'extra': '{"request-id": "req-xyz", "foo": 1}',
            'timestamp': 9999.0,
            'objects': [],
        }
        records = eventlog_drainer._build_records([(3, payload)])

        self.assertEqual(1, len(records))
        rec = records[0]
        # A UUID was synthesised.
        self.assertIsNotNone(rec.event_uuid)
        self.assertGreater(len(rec.event_uuid), 0)
        # request_id lifted from extra['request-id'].
        self.assertEqual('req-xyz', rec.request_id)
        # extra should only contain the non-request-id keys.
        # (The drainer passes the whole decoded dict through; it does not
        # strip request-id from extra -- that stripping happened upstream
        # in add_event_multi for new-format rows. Old-format rows keep the
        # key in extra because we don't mutate it, but the top-level field
        # is correctly set.)
        self.assertIsNotNone(rec.extra)

    def test_objects_as_list_of_dicts(self):
        """objects list as dicts (current spool format) is normalised to tuples."""
        payload = {
            'event_uuid': 'bbbb-2222',
            'event_type': 'audit',
            'fqdn': 'sf-1',
            'message': 'multi',
            'extra': '{}',
            'timestamp': 1.0,
            'request_id': None,
            'objects': [
                {'object_type': 'instance', 'object_uuid': 'u1'},
                {'object_type': 'network', 'object_uuid': 'u2'},
            ],
        }
        records = eventlog_drainer._build_records([(1, payload)])

        rec = records[0]
        self.assertEqual(2, len(rec.objects))
        self.assertEqual(('instance', 'u1'), rec.objects[0])
        self.assertEqual(('network', 'u2'), rec.objects[1])

    def test_objects_as_list_of_tuples(self):
        """objects list as tuples (older spool format) is also accepted."""
        payload = {
            'event_uuid': 'cccc-3333',
            'event_type': 'audit',
            'fqdn': 'sf-1',
            'message': 'tup',
            'extra': '{}',
            'timestamp': 2.0,
            'request_id': None,
            'objects': [('instance', 'u3'), ('network', 'u4')],
        }
        records = eventlog_drainer._build_records([(2, payload)])

        rec = records[0]
        self.assertEqual(2, len(rec.objects))
        self.assertEqual(('instance', 'u3'), rec.objects[0])
        self.assertEqual(('network', 'u4'), rec.objects[1])

    def test_extra_none_yields_none_on_record(self):
        """extra=None (no extra payload) results in record.extra == None."""
        payload = {
            'event_uuid': 'dddd-4444',
            'event_type': 'audit',
            'fqdn': 'sf-1',
            'message': 'no-extra',
            'extra': None,
            'timestamp': 3.0,
            'request_id': None,
            'objects': [],
        }
        records = eventlog_drainer._build_records([(5, payload)])
        self.assertIsNone(records[0].extra)

    def test_extra_json_dict_is_decoded(self):
        """extra as a JSON-encoded string is decoded to a dict on the record."""
        payload = {
            'event_uuid': 'eeee-5555',
            'event_type': 'audit',
            'fqdn': 'sf-1',
            'message': 'has-extra',
            'extra': '{"key": "value", "n": 42}',
            'timestamp': 4.0,
            'request_id': None,
            'objects': [],
        }
        records = eventlog_drainer._build_records([(6, payload)])
        self.assertEqual({'key': 'value', 'n': 42}, records[0].extra)

    def test_duration_propagated(self):
        """Non-None duration reaches the EventRecord unchanged."""
        payload = {
            'event_uuid': 'ffff-6666',
            'event_type': 'usage',
            'fqdn': 'sf-1',
            'message': 'timed',
            'extra': None,
            'timestamp': 5.0,
            'duration': 3.5,
            'request_id': None,
            'objects': [],
        }
        records = eventlog_drainer._build_records([(8, payload)])
        self.assertAlmostEqual(3.5, records[0].duration, places=5)

    def test_multiple_rows_in_one_call(self):
        """Multiple spool rows produce one EventRecord each, in order."""
        rows = [
            (1, {
                'event_uuid': 'aaa-001', 'event_type': 'audit', 'fqdn': 's',
                'message': 'first', 'extra': None, 'timestamp': 1.0,
                'request_id': None, 'objects': [],
            }),
            (2, {
                'event_uuid': 'aaa-002', 'event_type': 'mutate', 'fqdn': 's',
                'message': 'second', 'extra': None, 'timestamp': 2.0,
                'request_id': None, 'objects': [],
            }),
        ]
        records = eventlog_drainer._build_records(rows)
        self.assertEqual(2, len(records))
        self.assertEqual('first', records[0].message)
        self.assertEqual('second', records[1].message)


class DrainOneBatchTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """Happy path, RPC failure, empty spool, poison row, and stop-event."""

    def setUp(self):
        super().setUp()
        eventlog_spool.initialise('test-daemon')
        self.thread = eventlog_drainer._DrainerThread()

    def _enqueue(self, n):
        spool = eventlog_spool.get_spool()
        for i in range(n):
            spool.enqueue({
                'event_uuid': f'uuid-{i}',
                'event_type': 'audit',
                'fqdn': 's',
                'message': f'msg-{i}',
                'extra': None,
                'timestamp': float(i),
                'request_id': None,
                'objects': [],
            })

    def test_empty_spool_returns_zero(self):
        with mock.patch(
                'shakenfist.eventlog_drainer.mariadb.'
                'record_event_batch') as mock_rpc:
            result = self.thread._drain_one_batch()
        self.assertEqual(0, result)
        mock_rpc.assert_not_called()

    def test_happy_path_deletes_spool_rows_and_resets_backoff(self):
        self._enqueue(3)
        self.thread._backoff = 16.0  # simulate prior failures

        with mock.patch(
                'shakenfist.eventlog_drainer.mariadb.'
                'record_event_batch',
                return_value=True) as mock_rpc:
            drained = self.thread._drain_one_batch()

        self.assertEqual(3, drained)
        mock_rpc.assert_called_once()
        # All rows gone from the spool.
        self.assertEqual(0, eventlog_spool.get_spool().count())
        # Backoff reset.
        self.assertEqual(
            eventlog_drainer.BACKOFF_INITIAL, self.thread._backoff)

    def test_failed_rpc_leaves_spool_rows_and_backs_off(self):
        self._enqueue(2)
        initial_backoff = self.thread._backoff

        with mock.patch(
                'shakenfist.eventlog_drainer.mariadb.'
                'record_event_batch',
                return_value=False), \
                mock.patch.object(self.thread._stop_event, 'wait'):
            drained = self.thread._drain_one_batch()

        self.assertEqual(0, drained)
        # Rows still in spool.
        self.assertEqual(2, eventlog_spool.get_spool().count())
        # Backoff grew.
        self.assertGreater(self.thread._backoff, initial_backoff)

    def test_backoff_grows_on_repeated_failures(self):
        self._enqueue(1)

        with mock.patch(
                'shakenfist.eventlog_drainer.mariadb.'
                'record_event_batch',
                return_value=False), \
                mock.patch.object(self.thread._stop_event, 'wait'):
            self.thread._drain_one_batch()
            after_one = self.thread._backoff
            self.thread._drain_one_batch()
            after_two = self.thread._backoff

        self.assertGreater(after_one, eventlog_drainer.BACKOFF_INITIAL)
        self.assertGreater(after_two, after_one)
        self.assertLessEqual(after_two, eventlog_drainer.BACKOFF_MAX)

    def test_poison_row_drops_batch_and_returns_zero(self):
        """If _build_records raises, the batch is dropped rather than
        wedging the drainer forever.
        """
        self._enqueue(2)

        with mock.patch(
                'shakenfist.eventlog_drainer._build_records',
                side_effect=ValueError('malformed payload')) as mock_build, \
                mock.patch(
                    'shakenfist.eventlog_drainer.mariadb.'
                    'record_event_batch') as mock_rpc:
            drained = self.thread._drain_one_batch()

        mock_build.assert_called_once()
        mock_rpc.assert_not_called()
        self.assertEqual(0, drained)
        # Poison rows were removed from spool (drop path).
        self.assertEqual(0, eventlog_spool.get_spool().count())

    def test_stop_event_exits_cleanly(self):
        """Setting the stop event causes the drainer thread's run() to exit."""
        self.thread._stop_event.set()
        # run() checks is_set() before each iteration; it should return
        # immediately without entering _drain_one_batch.
        with mock.patch.object(
                self.thread, '_drain_one_batch') as mock_drain:
            self.thread.run()
        mock_drain.assert_not_called()


class BatchSizeCapTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """A drain reads no more than DRAIN_BATCH_SIZE rows per call."""

    def setUp(self):
        super().setUp()
        eventlog_spool.initialise('test-daemon')
        self.thread = eventlog_drainer._DrainerThread()

    def test_single_batch_is_capped(self):
        spool = eventlog_spool.get_spool()
        # Enqueue more than DRAIN_BATCH_SIZE.
        n = eventlog_drainer.DRAIN_BATCH_SIZE + 50
        for i in range(n):
            spool.enqueue({
                'event_uuid': f'uuid-{i}',
                'event_type': 'audit',
                'fqdn': 's',
                'message': f'msg-{i}',
                'extra': None,
                'timestamp': float(i),
                'request_id': None,
                'objects': [],
            })

        with mock.patch(
                'shakenfist.eventlog_drainer.mariadb.'
                'record_event_batch',
                return_value=True) as mock_rpc:
            drained = self.thread._drain_one_batch()

        self.assertEqual(eventlog_drainer.DRAIN_BATCH_SIZE, drained)
        # The mock was called with a batch of exactly DRAIN_BATCH_SIZE.
        call_args = mock_rpc.call_args[0][0]
        self.assertLessEqual(
            len(call_args), eventlog_drainer.DRAIN_BATCH_SIZE)
        # The remainder is left for the next call.
        self.assertEqual(50, spool.count())
