# Copyright 2019 Michael Still and contributors
import json
from unittest import mock

import flask

from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import instance as api_instance
from shakenfist.instance import Instance
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class CoerceIntTestCase(base.ShakenFistTestCase):
    """The shared coercion for integer body parameters.

    Every endpoint reading an integer out of a request body goes
    through this, because log_request merges JSON body values into
    handler kwargs with no type checking (issue 3612) and a wrong
    guard leaks the interpreter's own message to the client as a 400
    or a 500 (issue 3609).
    """

    def test_integers_pass_through(self):
        for value in (0, 5, -1, 2 ** 40):
            self.assertEqual(value, api_base.coerce_int(value))

    def test_numeric_strings_are_coerced(self):
        self.assertEqual(5, api_base.coerce_int('5'))
        self.assertEqual(-1, api_base.coerce_int('-1'))

    def test_floats_are_truncated(self):
        self.assertEqual(5, api_base.coerce_int(5.9))

    def test_type_errors_are_rejected(self):
        """int() raises TypeError, not ValueError, for these."""
        for value in (None, [], {}, [5], {'a': 1}):
            self.assertIsNone(api_base.coerce_int(value),
                              'coerce_int(%r) should be None' % (value,))

    def test_value_errors_are_rejected(self):
        for value in ('', 'banana', '5.5', float('nan')):
            self.assertIsNone(api_base.coerce_int(value),
                              'coerce_int(%r) should be None' % (value,))

    def test_infinities_are_rejected(self):
        """int(float('inf')) raises OverflowError, which is neither of
        the two exceptions an obvious guard catches. Python's JSON
        parser accepts the non-standard Infinity literal, so a client
        really can send this."""
        for value in (float('inf'), float('-inf')):
            self.assertIsNone(api_base.coerce_int(value),
                              'coerce_int(%r) should be None' % (value,))

    def test_booleans_are_rejected(self):
        """bool subclasses int, so int(True) is 1 -- answering
        malformed input with a plausible number rather than an error."""
        for value in (True, False):
            self.assertIsNone(api_base.coerce_int(value),
                              'coerce_int(%r) should be None' % (value,))


class InstanceMetadataAffinityTestCase(base.ShakenFistTestCase):
    """Affinity metadata values must be integers, checked cleanly.

    The validator guarded int() with ValueError only, so a null or
    Infinity affinity value raised TypeError or OverflowError and
    escaped to handle_authorization_exceptions, which handed the
    interpreter's message to the client.
    """

    def setUp(self):
        super().setUp()
        self.app = flask.Flask(__name__)

    def _validate(self, value):
        # sf_api.error builds a Flask response, so it needs a request
        # context even though the validator itself does not.
        with self.app.test_request_context():
            return api_instance._validate_instance_metadata(
                Instance.METADATA_KEY_AFFINITY, value)

    def test_integer_values_are_accepted(self):
        self.assertIsNone(self._validate({'cpu': 5, 'disk': '-1'}))

    def test_malformed_values_are_a_clean_400(self):
        for value in ({'cpu': None}, {'cpu': []}, {'cpu': float('inf')},
                      {'cpu': 'banana'}, {'cpu': True}):
            err = self._validate(value)
            self.assertIsNotNone(err, 'affinity %r should be rejected' % value)
            self.assertEqual(400, err.status_code)
            body = json.loads(err.get_data(as_text=True))
            self.assertEqual(
                'affinity dictionary values should be integers', body['error'])


class InstanceAgentPutModeTestCase(base.ShakenFistTestCase):
    """A missing or wrong-typed file mode must not leak.

    mode is documented as required but nothing enforces that, so it
    defaults to None -- and both int(None) and
    symbolic_to_numeric_permissions(None) raise TypeError. This was
    therefore reachable by simply omitting the field, with no
    malformed value required.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        # The client must be created after all the mocks, or the mocks
        # are not correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.instance = self.mock_mariadb.create_instance(
            'agentmode', namespace='system')
        self.instance.agent_state = 'ready'

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def _put(self, body):
        return self.client.post(
            '/instances/%s/agent/put' % self.instance.uuid,
            headers={'Authorization': self.auth_token},
            data=json.dumps(body))

    def test_missing_mode_is_a_clean_406(self):
        resp = self._put({'blob_uuid': 'b', 'path': '/tmp/f'})

        self.assertEqual(406, resp.status_code)
        self.assertEqual('invalid mode: must be a string or integer',
                         resp.get_json()['error'])

    def test_wrong_typed_mode_is_a_clean_406(self):
        for mode in (None, [], {}, True, 0.5):
            resp = self._put(
                {'blob_uuid': 'b', 'path': '/tmp/f', 'mode': mode})

            self.assertEqual(406, resp.status_code, 'mode %r' % (mode,))
            self.assertEqual('invalid mode: must be a string or integer',
                             resp.get_json()['error'])

    def test_valid_mode_reaches_the_blob_lookup(self):
        """The type check must not reject input the handler used to
        accept, so a well formed mode gets through to the next step.

        That step is also asserted: it used to call self.api_error,
        which does not exist on Resource, so an unknown blob raised
        AttributeError and returned a 500 carrying an interpreter
        message rather than a 404. Blob.from_db is mocked because
        MockMariaDB does not cover blobs."""
        with mock.patch('shakenfist.external_api.instance.Blob.from_db',
                        return_value=None):
            for mode in ('0755', 493, 'u+x'):
                resp = self._put(
                    {'blob_uuid': '9d3e3f7b-05d2-4a0f-8bbe-9c56e6ebd3e6',
                     'path': '/tmp/f', 'mode': mode})

                self.assertEqual(404, resp.status_code, 'mode %r' % (mode,))
                self.assertEqual('blob not found', resp.get_json()['error'])
