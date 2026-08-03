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

    def test_non_scalar_limit_is_a_clean_400(self):
        """A list or a dict is what the new `except TypeError` is for --
        int() raises TypeError rather than ValueError for these."""
        for value in ([], {}, [5]):
            self.get_object_events.reset_mock()
            resp = self.client.get('/events', json={'limit': value})

            self.assertEqual(400, resp.status_code,
                             'limit=%r should be a 400' % (value,))
            self.assertEqual('limit must be an integer',
                             resp.get_json()['error'])
            self.get_object_events.assert_not_called()

    def test_oversized_limit_is_capped_not_a_500(self):
        """A limit larger than int32 used to be coerced successfully and
        then blow up in protobuf message construction with
        'ValueError: Value out of range', which escaped as a 500 -- the
        same failure class issue 3609 is about, with a different
        exception name in the body. Clamping happens in the API layer so
        the value handed to mariadb is always serialisable."""
        resp = self.client.get('/events', json={'limit': 2 ** 40})

        self.assertEqual(200, resp.status_code)
        self.get_object_events.assert_called_once_with(
            'node', 'sf-1', limit=1000, event_type=None)

    def test_limit_above_the_cap_is_capped(self):
        resp = self.client.get('/events', json={'limit': 5000})

        self.assertEqual(200, resp.status_code)
        self.get_object_events.assert_called_once_with(
            'node', 'sf-1', limit=1000, event_type=None)

    def test_negative_limit_becomes_the_default(self):
        """Matches the hardening documented on
        mariadb._direct_get_object_events, so the two layers agree."""
        resp = self.client.get('/events', json={'limit': -1})

        self.assertEqual(200, resp.status_code)
        self.get_object_events.assert_called_once_with(
            'node', 'sf-1', limit=100, event_type=None)

    def test_non_string_event_type_is_a_clean_400(self):
        """event_type arrives through the same unvalidated body merge as
        limit and lands in a protobuf string field, so a non-string
        raised TypeError and leaked the interpreter message by exactly
        the route issue 3609 describes."""
        resp = self.client.get('/events', json={'event_type': 5})

        self.assertEqual(400, resp.status_code)
        self.assertEqual('event_type must be a string',
                         resp.get_json()['error'])
        self.get_object_events.assert_not_called()

    def test_string_event_type_passes_through(self):
        resp = self.client.get('/events', json={'event_type': 'audit'})

        self.assertEqual(200, resp.status_code)
        self.get_object_events.assert_called_once_with(
            'node', 'sf-1', limit=100, event_type='audit')


class ObjectEventsSharingTestCase(base.ShakenFistTestCase):
    """Every events endpoint must route through the shared helper.

    The coercion above only protects an endpoint which actually calls
    object_events_response. This pins that all five do, so a future
    endpoint which open-codes the read cannot quietly reintroduce
    issue 3609 without this failing.
    """

    def test_all_events_endpoints_use_the_shared_helper(self):
        import inspect

        from shakenfist.external_api import artifact
        from shakenfist.external_api import blob
        from shakenfist.external_api import instance
        from shakenfist.external_api import network
        from shakenfist.external_api import node

        endpoints = [
            (node, 'NodeEventsEndpoint'),
            (instance, 'InstanceEventsEndpoint'),
            (artifact, 'ArtifactEventsEndpoint'),
            (blob, 'BlobEventsEndpoint'),
            (network, 'NetworkEventsEndpoint'),
        ]

        for module, name in endpoints:
            endpoint = getattr(module, name)
            source = inspect.getsource(endpoint)
            self.assertIn(
                'object_events_response', source,
                '%s.%s must read events through '
                'api_base.object_events_response' % (module.__name__, name))
            self.assertIn(
                "(400, 'The limit must be an integer.', None)", source,
                '%s.%s must declare the 400 the shared helper can '
                'return' % (module.__name__, name))
