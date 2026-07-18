import json
import tempfile
import uuid
from unittest import mock

from shakenfist_utilities import logs  # noreorder

from shakenfist import eventlog
from shakenfist import eventlog_spool
from shakenfist.tests import base


LOG, _ = logs.setup(__name__)


class _SpoolRootMixin:
    """Redirect ``SPOOL_ROOT`` to a tempdir and initialise a fresh spool."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix='sf-eventlog-test-')
        self._original_root = eventlog_spool.SPOOL_ROOT
        eventlog_spool.SPOOL_ROOT = self.tmp
        eventlog_spool.reset_for_tests()
        self.addCleanup(eventlog_spool.reset_for_tests)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        eventlog_spool.SPOOL_ROOT = self._original_root


class AddEventMultiSpoolPayloadTestCase(
        _SpoolRootMixin, base.ShakenFistTestCase):
    """``add_event_multi`` produces the correct spool payload shape.

    Phase 2a guarantees:
    - ``event_uuid`` is always present at the top level.
    - ``request_id`` is a top-level key (not buried in ``extra``).
    - ``extra`` no longer contains a ``request-id`` key.
    """

    def setUp(self):
        super().setUp()
        # Stop the base-class mock so the real add_event_multi runs.
        self.mock_add_event_multi.stop()
        # Initialise the spool so the fast path is taken.
        eventlog_spool.initialise('test-daemon')
        # Patch config to avoid real node-name lookups.
        config_patcher = mock.patch('shakenfist.eventlog.config')
        self.mock_config = config_patcher.start()
        self.mock_config.NODE_NAME = 'test-node'
        self.addCleanup(config_patcher.stop)

    def _dequeue_one(self):
        """Drain and return the first (and only) spool payload."""
        spool = eventlog_spool.get_spool()
        batch = spool.dequeue_batch(1)
        self.assertEqual(1, len(batch), 'Expected exactly one spool row')
        return batch[0][1]

    def test_event_uuid_is_always_present(self):
        """A fresh UUID is generated for every call to add_event_multi."""
        eventlog.add_event_multi(
            'audit', [('instance', 'inst-uuid-1')], 'test event')

        payload = self._dequeue_one()
        self.assertIn('event_uuid', payload)
        self.assertIsNotNone(payload['event_uuid'])
        self.assertGreater(len(payload['event_uuid']), 0)

    def test_request_id_is_top_level_key(self):
        """When a Flask request id is present it surfaces as a top-level key."""
        fake_request_id = 'req-abcdef-0001'
        with mock.patch('shakenfist.eventlog.flask') as mock_flask:
            mock_flask.request.environ.get.return_value = fake_request_id
            eventlog.add_event_multi(
                'audit', [('instance', 'inst-uuid-2')], 'request id test')

        payload = self._dequeue_one()
        self.assertEqual(fake_request_id, payload.get('request_id'))

    def test_request_id_absent_outside_flask_context(self):
        """Outside a Flask request context request_id is None."""
        with mock.patch('shakenfist.eventlog.flask') as mock_flask:
            mock_flask.request.environ.get.side_effect = RuntimeError(
                'outside app context')
            eventlog.add_event_multi(
                'audit', [('instance', 'inst-uuid-3')], 'no request id')

        payload = self._dequeue_one()
        self.assertIsNone(payload.get('request_id'))

    def test_extra_does_not_contain_request_id_key(self):
        """``extra`` no longer carries a ``request-id`` key (phase 2a change)."""
        fake_request_id = 'req-phase2a-check'
        with mock.patch('shakenfist.eventlog.flask') as mock_flask:
            mock_flask.request.environ.get.return_value = fake_request_id
            eventlog.add_event_multi(
                'audit', [('instance', 'inst-uuid-4')], 'extra key check',
                extra={'some_key': 'some_value'})

        payload = self._dequeue_one()
        # extra is JSON-encoded in the spool payload.
        extra_raw = payload.get('extra')
        if extra_raw:
            extra_decoded = json.loads(extra_raw)
            self.assertNotIn('request-id', extra_decoded)
        # Top-level request_id should have the value.
        self.assertEqual(fake_request_id, payload.get('request_id'))

    def test_objects_are_serialised_as_dicts(self):
        """objects list uses the dict format expected by the drainer."""
        eventlog.add_event_multi(
            'audit',
            [('instance', 'inst-uuid-5'), ('network', 'net-uuid-5')],
            'objects test')

        payload = self._dequeue_one()
        objects = payload.get('objects', [])
        self.assertEqual(2, len(objects))
        # Each element is a dict with object_type and object_uuid.
        self.assertEqual('instance', objects[0]['object_type'])
        self.assertEqual('inst-uuid-5', objects[0]['object_uuid'])
        self.assertEqual('network', objects[1]['object_type'])
        self.assertEqual('net-uuid-5', objects[1]['object_uuid'])

    def test_suppressed_event_still_reaches_spool(self):
        """suppress_event_logging mutes the log echo, not the store.

        Billing statistics (and other high-volume callers) add their
        events with suppress_event_logging=True. The flag must only
        skip the 'Added event' Loki echo; the event itself still has
        to reach the spool, and from there the events table. A
        regression here silently discards all instance usage data.
        """
        self.mock_config.LOG_EVENTS_TO_LOKI = True
        with mock.patch.object(eventlog, 'LOG') as mock_log:
            eventlog.add_event_multi(
                'usage', [('instance', 'inst-uuid-6')], 'usage',
                extra={'cpu usage': {'cpu time ns': 12345}},
                suppress_event_logging=True)
            mock_log.with_fields.assert_not_called()

        payload = self._dequeue_one()
        self.assertEqual('usage', payload['event_type'])
        self.assertEqual(
            'inst-uuid-6', payload['objects'][0]['object_uuid'])
        self.assertEqual(
            {'cpu usage': {'cpu time ns': 12345}},
            json.loads(payload['extra']))

    def test_unsuppressed_event_is_echoed_to_log(self):
        """Without the flag the 'Added event' echo is emitted."""
        self.mock_config.LOG_EVENTS_TO_LOKI = True
        with mock.patch.object(eventlog, 'LOG') as mock_log:
            eventlog.add_event_multi(
                'audit', [('instance', 'inst-uuid-7')], 'echoed event')
            mock_log.with_fields.assert_called()

        payload = self._dequeue_one()
        self.assertEqual('audit', payload['event_type'])

    def test_uuid_object_uuid_is_logged_as_str(self):
        """A uuid.UUID object uuid must be stringified before it is logged.

        add_event is typed to accept a uuid.UUID and obj.uuid is one. The
        'Added event' fields are JSON-encoded by the log shipper, and a raw
        UUID is not JSON serializable -- it made the handler raise mid-emit
        and silently drop the record. The value logged for the object must
        be a JSON-serializable str.
        """
        emitted = []

        class _RecordingLogger:
            # The object field is attached via a chained with_fields() call,
            # so record the fully-merged fields the emit finally sees.
            def __init__(self, fields=None):
                self._fields = dict(fields or {})

            def with_fields(self, fields):
                merged = dict(self._fields)
                merged.update(fields)
                return _RecordingLogger(merged)

            def info(self, _message):
                emitted.append(self._fields)

            def error(self, _message):
                emitted.append(self._fields)

        obj_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
        with mock.patch.object(eventlog, 'LOG', _RecordingLogger()):
            eventlog.add_event_multi(
                'audit', [('instance', obj_uuid)], 'uuid coercion test')

        self.assertEqual(1, len(emitted))
        fields = emitted[0]
        self.assertIsInstance(fields.get('instance'), str)
        self.assertEqual(str(obj_uuid), fields['instance'])
        # The log shipper JSON-encodes these fields; this must not raise.
        json.dumps(fields)

        # The spool payload agrees.
        payload = self._dequeue_one()
        self.assertEqual(str(obj_uuid), payload['objects'][0]['object_uuid'])
