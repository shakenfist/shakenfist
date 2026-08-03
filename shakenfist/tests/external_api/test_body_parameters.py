# Copyright 2019 Michael Still and contributors
import json
import os
import shutil
import tempfile
from unittest import mock

import flask

from shakenfist.config import config
from shakenfist.config import SFConfig
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

    def test_integral_floats_are_accepted(self):
        self.assertEqual(5, api_base.coerce_int(5.0))
        self.assertEqual(-2, api_base.coerce_int(-2.0))

    def test_fractional_floats_are_rejected(self):
        """int() would truncate 5.9 to 5. The string '5.5' has always
        been rejected, so accepting the float form would mean the two
        JSON spellings of the same value disagreed."""
        for value in (5.9, -0.5, 0.1):
            self.assertIsNone(api_base.coerce_int(value),
                              'coerce_int(%r) should be None' % (value,))

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

    def test_missing_mode_is_a_clean_400(self):
        """A missing required parameter is a 400 like every other one.
        A present but unusable mode keeps this endpoint's existing 406,
        so a client can tell the two apart."""
        for body in ({'blob_uuid': 'b', 'path': '/tmp/f'},
                     {'blob_uuid': 'b', 'path': '/tmp/f', 'mode': None}):
            resp = self._put(body)

            self.assertEqual(400, resp.status_code, '%r' % (body,))
            self.assertEqual('no mode specified', resp.get_json()['error'])

    def test_wrong_typed_mode_is_a_clean_406(self):
        for mode in ([], {}, True, 0.5):
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


class UploadTruncateOffsetTestCase(base.ShakenFistTestCase):
    """The truncate offset must be bounded at both ends.

    os.truncate() is not safe for an arbitrary integer: beyond a C
    long it raises OverflowError, beyond the filesystem's maximum file
    size it raises OSError(EFBIG), and in between it succeeds and
    grows the upload into a large sparse file. All three are reachable
    from a URL path segment with no request body at all.
    """

    UPLOAD_UUID = '4f2b6d5a-9e4c-4d0e-9f2a-1b7c5d3e8a90'
    CONTENT = b'0123456789'

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.storage_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.storage_path, ignore_errors=True)
        os.makedirs(os.path.join(self.storage_path, 'uploads'))
        self.upload_path = os.path.join(
            self.storage_path, 'uploads', self.UPLOAD_UUID)
        with open(self.upload_path, 'wb') as f:
            f.write(self.CONTENT)

        fake_config = SFConfig(STORAGE_PATH=self.storage_path)
        p = mock.patch('shakenfist.external_api.upload.config', fake_config)
        p.start()
        self.addCleanup(p.stop)

        # MockMariaDB does not cover uploads, so the object lookup the
        # decorator does is mocked directly. node matches this node so
        # redirect_upload_request does not proxy the request away.
        self.upload = mock.MagicMock()
        self.upload.uuid = self.UPLOAD_UUID
        self.upload.node = config.NODE_NAME
        p = mock.patch('shakenfist.external_api.base.Upload.from_db',
                       return_value=self.upload)
        p.start()
        self.addCleanup(p.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

        # The client must be created after all the mocks, or the mocks
        # are not correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def _truncate(self, offset):
        return self.client.post(
            '/upload/%s/truncate/%s' % (self.UPLOAD_UUID, offset),
            headers={'Authorization': self.auth_token})

    def _size(self):
        return os.stat(self.upload_path).st_size

    def test_truncate_within_the_upload(self):
        resp = self._truncate(4)

        self.assertEqual(200, resp.status_code)
        self.assertEqual(4, self._size())

    def test_offset_at_the_end_is_allowed(self):
        """Truncating to exactly the current length is a no-op rather
        than an error, so a client which has just sent that many bytes
        does not have to special-case it."""
        resp = self._truncate(len(self.CONTENT))

        self.assertEqual(200, resp.status_code)
        self.assertEqual(len(self.CONTENT), self._size())

    def test_oversized_offsets_are_a_clean_400(self):
        # 2**70 is beyond a C long (OverflowError), 2**50 is beyond
        # most filesystems' maximum file size (OSError EFBIG), and 11
        # is merely past the end of this file -- which used to succeed
        # and leave a sparse file behind.
        for offset in (2 ** 70, 2 ** 50, len(self.CONTENT) + 1):
            resp = self._truncate(offset)

            self.assertEqual(400, resp.status_code, 'offset %s' % offset)
            self.assertEqual('offset is beyond the end of the upload',
                             resp.get_json()['error'])
            self.assertEqual(len(self.CONTENT), self._size(),
                             'offset %s changed the file' % offset)

    def test_negative_offset_is_a_clean_400(self):
        resp = self._truncate(-1)

        self.assertEqual(400, resp.status_code)
        self.assertEqual('offset must not be negative',
                         resp.get_json()['error'])

    def test_non_numeric_offset_is_a_clean_400(self):
        resp = self._truncate('banana')

        self.assertEqual(400, resp.status_code)
        self.assertEqual('offset is not an integer',
                         resp.get_json()['error'])

    def test_upload_with_no_data_is_a_404(self):
        os.unlink(self.upload_path)
        resp = self._truncate(1)

        self.assertEqual(404, resp.status_code)
        self.assertEqual('upload has no data', resp.get_json()['error'])

    def test_truncating_an_empty_upload_to_zero_succeeds(self):
        """A no-op rather than an error, so a client which resets
        before writing -- or retries a reset -- does not have to
        special case the 404."""
        os.unlink(self.upload_path)
        resp = self._truncate(0)

        self.assertEqual(200, resp.status_code)
        self.assertFalse(os.path.exists(self.upload_path))
