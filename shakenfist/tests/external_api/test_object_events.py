# Copyright 2019 Michael Still and contributors
from unittest import mock

import flask
import flask_restful

from shakenfist.external_api import base as api_base
from shakenfist.schema.event import EventReadRow
from shakenfist.tests import base


EVENT_ROWS = [
    EventReadRow(
        event_uuid='7c11373a-1fcb-4e29-8a25-8f8384b5b3e7',
        event_type='audit', timestamp=1685330702.492032,
        fqdn='sf-1', message='node created')
]


class ObjectEventsLimitTestCase(base.ShakenFistTestCase):
    """The events endpoints must coerce limit before using it.

    Issue 3609: log_request merges JSON body values into handler kwargs
    verbatim, so a caller sending {'limit': '5'} delivers a str. The
    range check in mariadb.get_object_events then raised TypeError,
    which handle_authorization_exceptions returned to the client as
    400 "'<=' not supported between instances of 'str' and 'int'".
    """

    def setUp(self):
        super().setUp()

        app = flask.Flask(__name__)
        api = flask_restful.Api(app)

        class _Events(api_base.Resource):
            # Marked public because this app is a bare Flask app with no
            # JWT configuration -- authentication would fail before
            # get() ever runs. What is being tested is the body-to-kwargs
            # merge in log_request and limit coercion in
            # object_events_response, which are the same whether or not
            # the caller was authenticated.
            @api_base.public
            def get(self, event_type=None, limit=100):
                return api_base.object_events_response(
                    'node', 'sf-1', limit, event_type)

        api.add_resource(_Events, '/events')
        app.testing = True
        self.client = app.test_client()

        self.mock_get_object_events = mock.patch(
            'shakenfist.external_api.base.mariadb.get_object_events',
            return_value=EVENT_ROWS)
        self.get_object_events = self.mock_get_object_events.start()
        self.addCleanup(self.mock_get_object_events.stop)

    def test_string_limit_is_coerced(self):
        # This is the request shape from issue 3609: limit arrives as a
        # JSON string, not an integer.
        resp = self.client.get('/events', json={'limit': '5'})

        self.assertEqual(200, resp.status_code)
        self.assertEqual(1, len(resp.get_json()))
        self.get_object_events.assert_called_once_with(
            'node', 'sf-1', limit=5, event_type=None)

    def test_integer_limit_passes_through(self):
        resp = self.client.get('/events', json={'limit': 5})

        self.assertEqual(200, resp.status_code)
        self.get_object_events.assert_called_once_with(
            'node', 'sf-1', limit=5, event_type=None)

    def test_default_limit(self):
        resp = self.client.get('/events')

        self.assertEqual(200, resp.status_code)
        self.get_object_events.assert_called_once_with(
            'node', 'sf-1', limit=100, event_type=None)

    def test_non_numeric_limit_is_a_clean_400(self):
        resp = self.client.get('/events', json={'limit': 'banana'})

        self.assertEqual(400, resp.status_code)
        error = resp.get_json()['error']
        self.assertEqual('limit must be an integer', error)
        self.get_object_events.assert_not_called()

    def test_null_limit_is_a_clean_400(self):
        resp = self.client.get('/events', json={'limit': None})

        self.assertEqual(400, resp.status_code)
        error = resp.get_json()['error']
        self.assertEqual('limit must be an integer', error)
        self.get_object_events.assert_not_called()
