# Copyright 2019 Michael Still and contributors
import logging
from unittest import mock

import flask
import flask_restful

from shakenfist import constants
from shakenfist import eventlog
from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class _CaptureHandler(logging.Handler):
    """Collect emitted log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class _RealAppMixin:
    """Drive the real API app without resolve_node_uuid reaching MariaDB.

    The hook runs ahead of everything for a non-probe request and only
    looks the node up when NODE_UUID is unset, so pinning it is enough.
    """

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, config, 'NODE_UUID', config.NODE_UUID)
        config.NODE_UUID = 'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        external_api.app.testing = True
        self.client = external_api.app.test_client()


class _CaptureMixin:
    def _capture(self, logger_name, level=logging.DEBUG):
        capture = _CaptureHandler()
        logger = logging.getLogger(logger_name)
        logger.addHandler(capture)
        self.addCleanup(logger.removeHandler, capture)

        original_level = logger.level
        logger.setLevel(level)
        self.addCleanup(logger.setLevel, original_level)
        return capture

    def _records(self, capture, message):
        return [r for r in capture.records if r.getMessage() == message]


class RequestParsedLogLevelTestCase(_CaptureMixin, base.ShakenFistTestCase):
    """'API request parsed' is a DEBUG line on every path.

    Issue 4602: this line carries the whole request -- headers,
    arguments and parsed body -- and was emitted once per request at
    INFO, which is the level the Loki shipper ships. On sfcbr that was
    about 215MB a day on its own. It was already DEBUG for the health
    probe paths for exactly this reason; nothing about a polling client
    makes it cheaper.
    """

    def setUp(self):
        super().setUp()

        app = flask.Flask(__name__)
        api = flask_restful.Api(app)

        class _Thing(api_base.Resource):
            # Public because this bare Flask app has no JWT
            # configuration; log_request runs for public and
            # authenticated routes alike.
            @api_base.public
            def get(self):
                return {}

        api.add_resource(_Thing, '/thing')
        app.testing = True
        self.client = app.test_client()
        self.capture = self._capture('shakenfist.external_api.base')

    def test_request_parsed_is_logged_at_debug(self):
        self.assertEqual(200, self.client.get('/thing').status_code)

        records = self._records(self.capture, 'API request parsed')
        self.assertEqual(1, len(records))
        self.assertEqual(logging.DEBUG, records[0].levelno)

        # The detail is still there for an operator reading journald,
        # it is only the level that moved.
        self.assertEqual('/thing', records[0].extra_fields['path'])


class RequestSummaryLineTestCase(_RealAppMixin, _CaptureMixin,
                                 base.ShakenFistTestCase):
    """One INFO line per request, in place of the four it displaces.

    The four per-request lifecycle lines were a third of sfcbr's log
    stream while saying nothing the events table did not hold. What an
    operator reading the shipped stream actually needs -- who asked for
    what, what they got, and how long it took -- is one line, and this
    is it.
    """

    def setUp(self):
        super().setUp()
        self.capture = self._capture('shakenfist.external_api.app')

    def test_summary_is_one_info_line_with_the_request_facts(self):
        resp = self.client.get('/instances')

        records = self._records(self.capture, 'API request handled')
        self.assertEqual(1, len(records))
        self.assertEqual(logging.INFO, records[0].levelno)

        fields = records[0].extra_fields
        self.assertEqual('GET', fields['method'])
        self.assertEqual('/instances', fields['path'])
        self.assertEqual('/instances', fields['route'])
        self.assertEqual(resp.status_code, fields['status'])
        self.assertIsNotNone(fields['request-id'])

        # A duration, measured from the first before_request hook, and
        # not a negative one.
        self.assertIsNotNone(fields['duration'])
        self.assertTrue(fields['duration'] >= 0)

    def test_health_probe_gets_no_summary_line(self):
        self.assertEqual(200, self.client.get('/livez').status_code)
        self.assertEqual(
            [], self._records(self.capture, 'API request handled'))

    def test_refused_request_is_still_summarised_with_a_duration(self):
        """A before_request refusal never reaches a handler.

        after_request hooks still run for a response a before_request
        hook returned, so the summary is emitted -- and because the
        start time is stashed by the first hook of all rather than by
        the audit hook, the duration survives too.
        """
        resp = self.client.post(
            '/auth/federated', headers={'Transfer-Encoding': 'chunked'})
        self.assertEqual(411, resp.status_code)

        records = self._records(self.capture, 'API request handled')
        self.assertEqual(1, len(records))
        self.assertEqual(411, records[0].extra_fields['status'])
        self.assertIsNotNone(records[0].extra_fields['duration'])


class RequestAuditEventEchoTestCase(_RealAppMixin, base.ShakenFistTestCase):
    """The request and response audit events are written, not shipped.

    Audit reads them out of MariaDB, so the events themselves must not
    change. Only their 'Added event' log echo moves to DEBUG.
    """

    def _api_request_events(self):
        events = []
        for call in eventlog.add_event_multi.mock_calls:
            if not call.args:
                continue
            event_type, objects, message = call.args[:3]
            if objects and objects[0][0] == ObjectType.API_REQUESTS:
                events.append((event_type, message, call.kwargs))
        return events

    def test_request_and_response_events_echo_at_debug(self):
        self.client.get('/instances')

        events = self._api_request_events()
        messages = [e[1] for e in events]
        self.assertIn('GET api request received', messages)
        self.assertIn('GET api response sent', messages)

        for event_type, message, kwargs in events:
            self.assertEqual(constants.EVENT_TYPE_AUDIT, event_type)
            # The echo is quietened, the event is not suppressed.
            self.assertTrue(kwargs['log_as_debug'], message)
            self.assertFalse(kwargs['suppress_event_logging'], message)


class TokenUseEventTestCase(base.ShakenFistTestCase):
    """A token use echoes at DEBUG; a refusal keeps its INFO line.

    One of these happens on every authenticated request and the other
    only when something is wrong, so they are not the same volume and
    do not get the same level.
    """

    def test_token_use_echoes_at_debug(self):
        ns = mock.MagicMock()

        @api_base.log_token_use
        def handler():
            return 'answered'

        with external_api.app.test_request_context('/instances'), \
                mock.patch.object(api_base, 'parse_jwt_identity',
                                  return_value=('ns', 'keyname')), \
                mock.patch.object(api_base.Namespace, 'from_db',
                                  return_value=ns):
            self.assertEqual('answered', handler())

        ns.add_event.assert_called_once_with(
            constants.EVENT_TYPE_AUDIT, 'token used to authenticate request',
            extra=mock.ANY, log_as_debug=True)

    def test_token_use_event_defaults_to_info(self):
        ns = mock.MagicMock()

        with external_api.app.test_request_context('/instances'):
            api_base._token_use_event(ns, 'keyname', 'some other token use')

        self.assertFalse(
            ns.add_event.call_args.kwargs['log_as_debug'])
