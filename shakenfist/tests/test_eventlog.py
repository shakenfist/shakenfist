import uuid
from unittest import mock

from shakenfist_utilities import logs  # noreorder

from shakenfist import eventlog
from shakenfist.protos import event_pb2
from shakenfist.protos import event_pb2_grpc
from shakenfist.tests import base


LOG, _ = logs.setup(__name__)


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
