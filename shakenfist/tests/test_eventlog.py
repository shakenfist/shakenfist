import json
import tempfile
import uuid
from unittest import mock

from shakenfist_utilities import logs  # noreorder

from shakenfist import eventlog
from shakenfist import eventlog_spool
from shakenfist.protos import event_pb2_grpc
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
        self.mock_config.EVENTLOG_SUPPRESS_GRPC = False
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


class EventLogProtobufSerializationTestCase(base.ShakenFistTestCase):
    """Test that eventlog correctly serializes objects to protobuf.

    These tests verify that UUID objects and other non-string types are
    correctly converted to strings before being assigned to protobuf fields.
    This prevents TypeError exceptions like "bad argument type for built-in
    operation".
    """

    def setUp(self):
        super().setUp()

        # Stop the mock from base class so we can test the actual function
        self.mock_add_event_multi.stop()

    @mock.patch('shakenfist.eventlog.get_eventlog_client')
    @mock.patch('shakenfist.eventlog.config')
    def test_add_event_multi_inner_with_uuid_objects(
            self, mock_config, mock_get_client):
        """Test that UUID objects are correctly converted to strings."""
        mock_config.NODE_NAME = 'testnode'

        # Create a mock channel and stub
        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel
        mock_stub = mock.MagicMock()

        # Make the stub return an ack
        mock_response = mock.MagicMock()
        mock_response.ack = True
        mock_stub.RecordMultiEvent.return_value = mock_response

        with mock.patch.object(
                event_pb2_grpc, 'EventServiceStub',
                return_value=mock_stub):
            # Create a UUID object (not a string)
            test_uuid = uuid.uuid4()
            simpler_objects = [('instance', test_uuid)]

            log = LOG.with_fields({})

            # This should not raise a TypeError
            eventlog._add_event_multi_inner(
                'mutate', log, 1234567890.0, simpler_objects,
                'test message', duration=None, extra={'test': 'value'})

            # Verify the stub was called
            mock_stub.RecordMultiEvent.assert_called_once()

            # Get the request that was passed to RecordMultiEvent
            call_args = mock_stub.RecordMultiEvent.call_args
            request = call_args[0][0]

            # Verify the object was added with string values
            self.assertEqual(1, len(request.objects))
            self.assertEqual('instance', request.objects[0].object_type)
            self.assertEqual(str(test_uuid), request.objects[0].object_uuid)

    @mock.patch('shakenfist.eventlog.get_eventlog_client')
    @mock.patch('shakenfist.eventlog.config')
    def test_add_event_multi_inner_with_string_uuid(
            self, mock_config, mock_get_client):
        """Test that string UUIDs still work correctly."""
        mock_config.NODE_NAME = 'testnode'

        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel
        mock_stub = mock.MagicMock()

        mock_response = mock.MagicMock()
        mock_response.ack = True
        mock_stub.RecordMultiEvent.return_value = mock_response

        with mock.patch.object(
                event_pb2_grpc, 'EventServiceStub',
                return_value=mock_stub):
            # Use a string UUID
            test_uuid_str = 'a1b2c3d4-e5f6-7890-abcd-ef1234567890'
            simpler_objects = [('instance', test_uuid_str)]

            log = LOG.with_fields({})

            eventlog._add_event_multi_inner(
                'mutate', log, 1234567890.0, simpler_objects,
                'test message', duration=None, extra={'test': 'value'})

            mock_stub.RecordMultiEvent.assert_called_once()

            call_args = mock_stub.RecordMultiEvent.call_args
            request = call_args[0][0]

            self.assertEqual(1, len(request.objects))
            self.assertEqual('instance', request.objects[0].object_type)
            self.assertEqual(test_uuid_str, request.objects[0].object_uuid)

    @mock.patch('shakenfist.eventlog.get_eventlog_client')
    @mock.patch('shakenfist.eventlog.config')
    def test_add_event_multi_inner_with_multiple_objects(
            self, mock_config, mock_get_client):
        """Test multiple objects with mixed UUID types."""
        mock_config.NODE_NAME = 'testnode'

        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel
        mock_stub = mock.MagicMock()

        mock_response = mock.MagicMock()
        mock_response.ack = True
        mock_stub.RecordMultiEvent.return_value = mock_response

        with mock.patch.object(
                event_pb2_grpc, 'EventServiceStub',
                return_value=mock_stub):
            # Mix of UUID object and string
            uuid_obj = uuid.uuid4()
            uuid_str = 'b2c3d4e5-f6a7-8901-bcde-f23456789012'
            simpler_objects = [
                ('instance', uuid_obj),
                ('network', uuid_str),
            ]

            log = LOG.with_fields({})

            eventlog._add_event_multi_inner(
                'mutate', log, 1234567890.0, simpler_objects,
                'test message', duration=None, extra=None)

            mock_stub.RecordMultiEvent.assert_called_once()

            call_args = mock_stub.RecordMultiEvent.call_args
            request = call_args[0][0]

            self.assertEqual(2, len(request.objects))
            self.assertEqual('instance', request.objects[0].object_type)
            self.assertEqual(str(uuid_obj), request.objects[0].object_uuid)
            self.assertEqual('network', request.objects[1].object_type)
            self.assertEqual(uuid_str, request.objects[1].object_uuid)

    @mock.patch('shakenfist.eventlog.get_eventlog_client')
    @mock.patch('shakenfist.eventlog.config')
    def test_add_event_multi_inner_skips_none_uuid(
            self, mock_config, mock_get_client):
        """Test that None UUIDs are skipped."""
        mock_config.NODE_NAME = 'testnode'

        mock_channel = mock.MagicMock()
        mock_get_client.return_value = mock_channel
        mock_stub = mock.MagicMock()

        mock_response = mock.MagicMock()
        mock_response.ack = True
        mock_stub.RecordMultiEvent.return_value = mock_response

        with mock.patch.object(
                event_pb2_grpc, 'EventServiceStub',
                return_value=mock_stub):
            # Include a None UUID which should be skipped
            valid_uuid = uuid.uuid4()
            simpler_objects = [
                ('instance', valid_uuid),
                ('network', None),
            ]

            log = LOG.with_fields({})

            eventlog._add_event_multi_inner(
                'mutate', log, 1234567890.0, simpler_objects,
                'test message', duration=None, extra=None)

            mock_stub.RecordMultiEvent.assert_called_once()

            call_args = mock_stub.RecordMultiEvent.call_args
            request = call_args[0][0]

            # Only the valid UUID should be included
            self.assertEqual(1, len(request.objects))
            self.assertEqual('instance', request.objects[0].object_type)
            self.assertEqual(str(valid_uuid), request.objects[0].object_uuid)
