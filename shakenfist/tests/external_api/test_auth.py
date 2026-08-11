import base64
import json
import logging
import sys
import time
from unittest import mock
from uuid import uuid4

import bcrypt

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.config import SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.util import credentials


def _clean_traceback(resp):
    if 'traceback' in resp:
        del resp['traceback']
    return resp


class FakeScheduler:
    def find_candidates(self, *args, **kwargs):
        return config.NODE_NAME


class AuthTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

    def test_post_auth_no_args(self):
        resp = self.client.post('/auth', data=json.dumps({}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'missing namespace in request',
                'status': 400
            },
            resp.get_json())

    def test_post_auth_no_key(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana'}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'missing key in request',
                'status': 400
            },
            resp.get_json())

    def test_post_auth_bad_parameter(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'keyyy': 'pwd'}))
        self.assertEqual(400, resp.status_code)

    def test_post_auth_key_non_string(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 1234}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'key is not a string',
                'status': 400
            },
            resp.get_json())

    def test_post_auth(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
        self.assertEqual(200, resp.status_code)
        self.assertIn('access_token', resp.get_json())

    def test_post_auth_not_authorized(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'hamster'}))
        self.assertEqual(401, resp.status_code)
        self.assertEqual(
            {
                'error': 'unauthorized',
                'status': 401
            },
            resp.get_json())

    def test_no_auth_header(self):
        resp = self.client.post('/auth/namespaces',
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(401, resp.status_code)
        self.assertEqual(
            {
                'error': 'Missing Authorization Header',
                'status': 401
            },
            _clean_traceback(resp.get_json()))

    def test_auth_header_wrong(self):
        resp = self.client.post('/auth/namespaces',
                                headers={
                                    'Accept': 'application/json',
                                    'Authorization': 'l33thacker'
                                    },
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(
            {
                'error': ("Missing 'Bearer' type in 'Authorization' header. Expected "
                          "'Authorization: Bearer <JWT>'"),
                'status': 401
            },
            _clean_traceback(resp.get_json()))
        self.assertEqual(401, resp.status_code)

    def test_auth_header_bad_jwt(self):
        resp = self.client.post('/auth/namespaces',
                                headers={
                                    'Accept': 'application/json',
                                    'Authorization': 'Bearer l33thacker'
                                    },
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(
            {
                'error': 'invalid JWT in Authorization header',
                'status': 401
            },
            _clean_traceback(resp.get_json()))
        self.assertEqual(401, resp.status_code)


class AuthDatabaseUnavailableTestCase(base.ShakenFistTestCase):
    """Issue 3522: an unreadable namespace key set must surface as a 503,
    not as a 401 telling the client its credentials are bad."""

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

    def _get_token(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def test_token_verification_surfaces_outage_as_503(self):
        auth_token = self._get_token()

        # Token verification point reads the JWT's key via
        # get_namespace_key_by_name (auth federation phase 2), so that
        # is the read which must not conflate an outage with "no such
        # key".
        with mock.patch(
                'shakenfist.mariadb.get_namespace_key_by_name',
                side_effect=exceptions.DatabaseUnavailable('keys unreadable')):
            resp = self.client.get(
                '/auth/namespaces', headers={'Authorization': auth_token})

        self.assertEqual(503, resp.status_code)
        self.assertEqual(
            {
                'error': 'database unavailable, please retry',
                'status': 503
            },
            _clean_traceback(resp.get_json()))

    def test_login_surfaces_outage_as_503(self):
        # /auth lists the namespace's keys via find_namespace_keys
        # (auth federation phase 2), so that is the read which must not
        # conflate an outage with "the namespace has no usable keys".
        with mock.patch(
                'shakenfist.mariadb.find_namespace_keys',
                side_effect=exceptions.DatabaseUnavailable('keys unreadable')):
            resp = self.client.post(
                '/auth',
                data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))

        self.assertEqual(503, resp.status_code)

    def test_token_verification_works_once_database_recovers(self):
        auth_token = self._get_token()
        resp = self.client.get(
            '/auth/namespaces', headers={'Authorization': auth_token})
        self.assertEqual(200, resp.status_code)


class AuthWithServiceKeyTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        ns = Namespace.new('banana')
        ns.add_key('_service_key', 'cheese')
        ns.add_key('key1', 'bacon')
        ns.add_key('key2', 'sausage')

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

    def test_post_auth_service_key(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'cheese'}))
        self.assertEqual(200, resp.status_code)
        self.assertIn('access_token', resp.get_json())

    def test_service_key_bypasses_existence_check(self):
        # Pin the legacy exact-name '_service_key' bypass in verify_token
        # (base.py:195): a token whose keyname is exactly '_service_key'
        # skips the nonce/existence check entirely, so it keeps validating
        # even after the underlying key is removed from the namespace.
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'cheese'}))
        self.assertEqual(200, resp.status_code)
        service_token = 'Bearer %s' % resp.get_json()['access_token']

        # The token validates on a verify_token-guarded endpoint.
        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': service_token})
        self.assertEqual(200, resp.status_code)

        # Remove the underlying key entirely.
        Namespace.from_db('banana').remove_key('_service_key')

        # The bypass means the token STILL validates despite the key being
        # gone -- verify_token never looks it up for the exact name.
        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': service_token})
        self.assertEqual(200, resp.status_code)

    def test_ordinary_key_does_not_bypass_existence_check(self):
        # Contrast with the bypass above: a token minted from an ordinary key
        # is rejected once that key is removed (base.py:196-200).
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
        self.assertEqual(200, resp.status_code)
        ordinary_token = 'Bearer %s' % resp.get_json()['access_token']

        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': ordinary_token})
        self.assertEqual(200, resp.status_code)

        Namespace.from_db('banana').remove_key('key1')

        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': ordinary_token})
        self.assertEqual(401, resp.status_code)


class AuthWithLingeringInstance(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('foo', 'key1', 'banana')
        self.mock_mariadb.create_instance(
            name='fooinst', namespace='foo')

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def test_delete_namespace_with_instances(self):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with instances',
                'status': 400
            },
            resp.get_json())


class AuthWithLingeringNetwork(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('foo', 'key1', 'banana')

        self.network_id = str(uuid4())
        self.mock_mariadb.create_network(
            name='foonet', uuid=self.network_id, namespace='foo')

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def test_delete_namespace_with_networks(self):
        resp = self.client.delete('/auth/namespaces/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete a namespace with networks',
                'status': 400
            },
            resp.get_json())


class AuthKeysTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.add_event = mock.patch('shakenfist.eventlog.add_event')
        self.add_event.start()
        self.addCleanup(self.add_event.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def test_auth_add_key_missing_args(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({}))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'no namespace specified',
                'status': 400
            },
            resp.get_json())

    def test_auth_add_key_missing_keyname(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'namespace': 'foo'
                                }))
        self.assertEqual(200, resp.status_code)
        self.assertEqual({
            'keys': [],
            'metadata': {},
            'name': 'foo',
            'state': 'created',
            'trust': {'full': ['system']},
            'version': 7
        }, resp.get_json())

    def test_auth_add_key_missing_key(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'namespace': 'foo',
                                    'key_name': 'bernard'
                                }))
        self.assertEqual(400, resp.status_code)
        self.assertEqual(
            {
                'error': 'no key specified',
                'status': 400
            },
            resp.get_json())

    def test_auth_add_key_illegal_keyname(self):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'namespace': 'foo',
                                    'key_name': 'service_key',
                                    'key': 'cheese'
                                }))
        self.assertEqual(
            {
                'error': 'illegal key name',
                'status': 403
            },
            resp.get_json())
        self.assertEqual(403, resp.status_code)

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_add_key_rejects_service_key_prefix(self, mock_lock):
        # Pin _namespace_keys_putpost's rejection of any _service_key-prefixed
        # name with a 403 (auth.py:288-289). The existing suite only covers
        # the exact name 'service_key' on the namespace-create path.
        resp = self.client.post('/auth/namespaces/system/keys',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'key_name': '_service_key_smuggled',
                                    'key': 'cheese'
                                }))
        self.assertEqual(403, resp.status_code)
        self.assertEqual(
            {
                'error': 'illegal key name',
                'status': 403
            },
            resp.get_json())

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_add_key_rejects_bare_service_key_prefix(self, mock_lock):
        # The prefix check also rejects the bare '_service_key' name.
        resp = self.client.post('/auth/namespaces/system/keys',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'key_name': '_service_key',
                                    'key': 'cheese'
                                }))
        self.assertEqual(403, resp.status_code)
        self.assertEqual(
            {
                'error': 'illegal key name',
                'status': 403
            },
            resp.get_json())

    def test_delete_namespace_key_missing_args(self):
        resp = self.client.delete('/auth/namespaces/system/',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)
        self.assertEqual(None, resp.get_json())

    def test_delete_namespace_key_missing_key(self):
        resp = self.client.delete('/auth/namespaces/system/keys/mykey',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(404, resp.status_code)
        self.assertEqual(
            {
                'error': 'key name not found in namespace',
                'status': 404
            },
            resp.get_json())

    # The expiry body parameter is new in the phase which made keys
    # first class objects. It is additive: absent means no expiry,
    # which is what every pre-existing client sends.

    def _add_key(self, key_name, key, expiry=None):
        body = {'key_name': key_name, 'key': key}
        if expiry is not None:
            body['expiry'] = expiry
        return self.client.post(
            '/auth/namespaces/system/keys',
            headers={'Authorization': self.auth_token},
            data=json.dumps(body))

    def test_add_key_accepts_a_future_expiry(self):
        expiry = time.time() + 3600
        resp = self._add_key('expiring', 'sekrit', expiry=expiry)

        # This endpoint has always answered with the bare key name.
        self.assertEqual(200, resp.status_code)
        self.assertEqual('expiring', resp.get_json())

        stored = Namespace.from_db('system').lookup_key('expiring')
        self.assertEqual(expiry, stored.expiry)

    def test_add_key_without_expiry_stores_none(self):
        resp = self._add_key('forever', 'sekrit')

        self.assertEqual(200, resp.status_code)
        self.assertIsNone(
            Namespace.from_db('system').lookup_key('forever').expiry)

    def test_add_key_rejects_an_expiry_in_the_past(self):
        resp = self._add_key('stale', 'sekrit', expiry=time.time() - 1)

        self.assertEqual(400, resp.status_code)
        self.assertEqual('expiry must be in the future',
                         resp.get_json()['error'])

    def test_add_key_rejects_a_non_numeric_expiry(self):
        resp = self._add_key('bogus', 'sekrit', expiry='tomorrow')

        self.assertEqual(400, resp.status_code)
        self.assertEqual('expiry is not a number', resp.get_json()['error'])

    def test_add_key_rejects_a_boolean_expiry(self):
        # bool is a subclass of int, so "expiry": true would otherwise
        # sneak past a naive isinstance check and become 1970.
        resp = self._add_key('boolean', 'sekrit', expiry=True)

        self.assertEqual(400, resp.status_code)
        self.assertEqual('expiry is not a number', resp.get_json()['error'])

    # The key update endpoint had two bugs which meant it never worked:
    # it tested membership against the wrong level of the keys dict, and
    # it handed a namespace name to a helper which expected the object.
    # Both are fixed, so these tests pin behaviour that has no history.

    def test_update_key_replaces_the_secret(self):
        self._add_key('rotate-me', 'original')
        original = Namespace.from_db('system').lookup_key('rotate-me')

        resp = self.client.put(
            '/auth/namespaces/system/keys/rotate-me',
            headers={'Authorization': self.auth_token},
            data=json.dumps({'key': 'replacement'}))
        self.assertEqual(200, resp.status_code)

        rotated = Namespace.from_db('system').lookup_key('rotate-me')
        self.assertNotEqual(original.nonce, rotated.nonce)
        self.assertTrue(bcrypt.checkpw(
            'replacement'.encode('utf-8'), base64.b64decode(rotated.key)))
        self.assertFalse(bcrypt.checkpw(
            'original'.encode('utf-8'), base64.b64decode(rotated.key)))

    def test_update_key_accepts_an_expiry(self):
        self._add_key('rotate-me', 'original')
        expiry = time.time() + 3600

        resp = self.client.put(
            '/auth/namespaces/system/keys/rotate-me',
            headers={'Authorization': self.auth_token},
            data=json.dumps({'key': 'replacement', 'expiry': expiry}))

        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            expiry,
            Namespace.from_db('system').lookup_key('rotate-me').expiry)

    def test_update_key_rejects_an_unknown_key(self):
        resp = self.client.put(
            '/auth/namespaces/system/keys/never-existed',
            headers={'Authorization': self.auth_token},
            data=json.dumps({'key': 'replacement'}))

        self.assertEqual(404, resp.status_code)
        self.assertEqual('key does not exist', resp.get_json()['error'])

    def test_update_key_without_a_secret_is_refused(self):
        # Secret generation belongs to the create path only. A rotation
        # which forgot its body must not silently replace a live
        # credential with a generated one -- the caller's existing
        # secret would stop working on a typo.
        self._add_key('rotate-me', 'original')
        original = Namespace.from_db('system').lookup_key('rotate-me')

        resp = self.client.put(
            '/auth/namespaces/system/keys/rotate-me',
            headers={'Authorization': self.auth_token},
            data=json.dumps({}))

        self.assertEqual(400, resp.status_code)
        self.assertEqual('no key specified', resp.get_json()['error'])

        unchanged = Namespace.from_db('system').lookup_key('rotate-me')
        self.assertEqual(original.nonce, unchanged.nonce)
        self.assertTrue(bcrypt.checkpw(
            'original'.encode('utf-8'), base64.b64decode(unchanged.key)))


class ExternalApiTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.recorded_op = mock.patch(
            'shakenfist.util.general.RecordedOperation')
        self.recorded_op.start()
        self.addCleanup(self.recorded_op.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('banana', 'key1', 'cheese')

        self.scheduler = mock.patch(
            'shakenfist.scheduler.Scheduler', FakeScheduler)
        self.mock_scheduler = self.scheduler.start()
        self.addCleanup(self.scheduler.stop)

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='node1',
        )
        self.config = mock.patch('shakenfist.instance.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        # The client must be created after all the mocks, or the mocks are not
        # correctly applied.
        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_mariadb.create_namespace('two', 'key1', 'space')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'two', 'key': 'space'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token_two = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_mariadb.create_namespace('three', 'key1', 'pass')
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'three', 'key': 'pass'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token_three = 'Bearer %s' % resp.get_json()['access_token']

        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

    def test_get_namespaces(self):
        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        self.assertEqual([
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'banana',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 7
            },
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'foo',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 7
            },
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'system',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 7
            },
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'three',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 7
            },
            {
                'keys': ['key1'],
                'metadata': {},
                'name': 'two',
                'state': 'created',
                'trust': {'full': ['system']},
                'version': 7
            }
        ], resp.get_json())

    def test_delete_namespace_missing_args(self):
        resp = self.client.delete('/auth/namespaces',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(405, resp.status_code)
        self.assertEqual(
            {
                'message': 'The method is not allowed for the requested URL.'
            },
            resp.get_json())

    def test_delete_namespace_system(self):
        resp = self.client.delete('/auth/namespaces/system',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(403, resp.status_code)
        self.assertEqual(
            {
                'error': 'you cannot delete the system namespace',
                'status': 403
            },
            resp.get_json())

    def test_get_namespace_metadata(self):
        # Set up metadata in MariaDB mock
        self.mock_mariadb.object_metadata['namespace/system'] = {
            'metadata': {'a': 'a', 'b': 'b'}
        }
        resp = self.client.get(
            '/auth/namespaces/system/metadata', headers={'Authorization': self.auth_token})
        self.assertEqual({'a': 'a', 'b': 'b'}, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual('application/json', resp.content_type)

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_put_namespace_metadata(self, mock_get_lock):
        resp = self.client.put('/auth/namespaces/system/metadata/foo',
                               headers={'Authorization': self.auth_token},
                               data=json.dumps({
                                   'key': 'foo',
                                   'value': 'bar'
                               }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            self.mock_mariadb.object_metadata['namespace/system']['metadata'])

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_post_namespace_metadata(self, mock_get_lock):
        resp = self.client.post('/auth/namespaces/system/metadata',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'key': 'foo',
                                    'value': 'bar'
                                }))
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'foo': 'bar'},
            self.mock_mariadb.object_metadata['namespace/system']['metadata'])

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_delete_namespace_metadata(self, mock_get_lock):
        # Set up metadata in MariaDB mock
        self.mock_mariadb.object_metadata['namespace/system'] = {
            'metadata': {'foo': 'bar', 'real': 'smart'}
        }
        resp = self.client.delete('/auth/namespaces/system/metadata/foo',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)
        self.assertEqual(
            {'real': 'smart'},
            self.mock_mariadb.object_metadata['namespace/system']['metadata'])

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_delete_namespace_metadata_bad_key(self, mock_get_lock):
        # We now just silently ignore deletes of things which don't exist
        resp = self.client.delete('/auth/namespaces/system/metadata/wrong',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_delete_namespace_metadata_no_keys(self, mock_get_lock):
        # We now just silently ignore deletes of things which don't exist
        resp = self.client.delete('/auth/namespaces/system/metadata/wrong',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(None, resp.get_json())
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.artifact.Artifact.from_url')
    @mock.patch('shakenfist.network.network.Network._db_get_attribute',
                return_value={'value': dbo.STATE_CREATED, 'update_time': 2})
    @mock.patch('shakenfist.locks.ClusterLock')
    def test_post_instance_only_system_specifies_namespaces(
            self, mock_lock, mock_net_attribute, mock_get_artifact):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'cheese'}))
        self.assertEqual(200, resp.status_code)
        non_system_auth_header = 'Bearer %s' % resp.get_json()[
            'access_token']

        resp = self.client.post('/instances',
                                headers={
                                    'Authorization': non_system_auth_header},
                                data=json.dumps({
                                    'name': 'test-instance',
                                    'cpus': 1,
                                    'memory': 1024,
                                    'network': [
                                        {'network_uuid': '87c15186-5f73-4947-a9fb-2183c4951efc'}],
                                    'disk': [{'size': 8,
                                              'base': 'cirros'}],
                                    'ssh_key': None,
                                    'user_data': None,
                                    'placed_on': None,
                                    'namespace': 'gerkin',
                                }))
        self.assertEqual(
            {'error': 'namespace not found',
             'status': 404},
            resp.get_json())
        self.assertEqual(404, resp.status_code)

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_delete_namespace_key(self, mock_lock):
        resp = self.client.delete('/auth/namespaces/system/keys/key1',
                                  headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)

    @mock.patch('shakenfist.locks.ClusterLock')
    @mock.patch('bcrypt.hashpw', return_value=b'terminator')
    def test_auth_add_key_new_namespace(self, mock_hashpw, mock_lock):
        resp = self.client.post('/auth/namespaces',
                                headers={'Authorization': self.auth_token},
                                data=json.dumps({
                                    'namespace': 'foo-unique',
                                    'key_name': 'bernard',
                                    'key': 'cheese'
                                }))
        self.assertEqual(200, resp.status_code)
        self.assertEqual({
            'keys': ['bernard'],
            'metadata': {},
            'name': 'foo-unique',
            'state': 'created',
            'trust': {'full': ['system']},
            'version': 7
        }, resp.get_json())


class AuthExpiredKeyTestCase(base.ShakenFistTestCase):
    """Pin that an expired key can neither mint nor validate.

    Covers brief item (ii): because both POST /auth and verify_token read
    through the read-time-filtered `keys` accessor, an expired key can no
    longer mint new tokens nor validate outstanding ones. time.time is
    mocked in the namespace module to step across the key's expiry; this
    does not disturb JWT signature/expiry validation, which uses
    datetime.now rather than time.time.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        # key1 (secret 'bacon') never expires; expkey expires at epoch 2000.
        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')
        Namespace.from_db('banana').add_key('expkey', 'expsecret', expiry=2000)

        self.client = external_api.app.test_client()

    def test_expired_key_cannot_mint(self):
        # Before the expiry, the key mints a token.
        with mock.patch('shakenfist.namespace.time.time', return_value=1000):
            resp = self.client.post(
                '/auth',
                data=json.dumps({'namespace': 'banana', 'key': 'expsecret'}))
            self.assertEqual(200, resp.status_code)

        # After the expiry, the same secret is rejected -- the key is hidden
        # from the accessor that /auth iterates.
        with mock.patch('shakenfist.namespace.time.time', return_value=3000):
            resp = self.client.post(
                '/auth',
                data=json.dumps({'namespace': 'banana', 'key': 'expsecret'}))
            self.assertEqual(401, resp.status_code)
            self.assertEqual(
                {'error': 'unauthorized', 'status': 401}, resp.get_json())

            # The never-expiring key1 still mints even past that time.
            resp = self.client.post(
                '/auth',
                data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
            self.assertEqual(200, resp.status_code)

    def test_expired_key_cannot_validate_outstanding_token(self):
        # Mint a token from the key while it is still valid.
        with mock.patch('shakenfist.namespace.time.time', return_value=1000):
            resp = self.client.post(
                '/auth',
                data=json.dumps({'namespace': 'banana', 'key': 'expsecret'}))
            self.assertEqual(200, resp.status_code)
            token = 'Bearer %s' % resp.get_json()['access_token']

            # While valid it authenticates a verify_token-guarded endpoint.
            resp = self.client.get(
                '/auth/namespaces', headers={'Authorization': token})
            self.assertEqual(200, resp.status_code)

        # Once the key has expired the outstanding token no longer validates,
        # even though the JWT itself is still within its own lifetime.
        with mock.patch('shakenfist.namespace.time.time', return_value=3000):
            resp = self.client.get(
                '/auth/namespaces', headers={'Authorization': token})
            self.assertEqual(401, resp.status_code)


class AuthNonceMismatchTestCase(base.ShakenFistTestCase):
    """Pin nonce-based revocation on key rotation.

    Covers brief item (iii): rotating a key (add_key with the same name
    generates a fresh nonce) invalidates every token minted from the old
    nonce -- replaying such a token against a verify_token-guarded endpoint
    returns 401 (base.py:196-210).
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        self.client = external_api.app.test_client()

    def test_rotating_key_invalidates_outstanding_token(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
        self.assertEqual(200, resp.status_code)
        token = 'Bearer %s' % resp.get_json()['access_token']

        # The freshly-minted token validates.
        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': token})
        self.assertEqual(200, resp.status_code)

        # Rotate the key: re-adding it under the same name generates a new
        # nonce, so the old token's nonce claim no longer matches.
        Namespace.from_db('banana').add_key('key1', 'bacon')

        resp = self.client.get('/auth/namespaces',
                               headers={'Authorization': token})
        self.assertEqual(401, resp.status_code)


class AuthRejectionLoggingTestCase(base.ShakenFistTestCase):
    """A rejected token must say who presented it, and not shout.

    Issue 3606: 'JWT token has incorrect nonce' was logged at ERROR
    with no attribution at all -- the fields were built with a set
    literal rather than a dict, which with_fields() silently discards
    -- so a rotation induced 401 read as a cluster fault that nobody
    could trace to a client.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        self.events = []
        patcher = mock.patch(
            'shakenfist.eventlog.add_event',
            side_effect=lambda event_type, object_type, object_uuid, message,
            duration=None, extra=None, **kwargs: self.events.append(
                (message, extra or {})))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.client = external_api.app.test_client()

    def _mint(self):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _replay(self, token):
        """Replay a now invalid token, returning the (level, fields, message)
        triples base.py logged while doing so.

        with_fields() hands back a fresh logger per call so that each
        message can be paired with the fields it was actually logged
        with, rather than with whatever the last caller happened to
        pass.
        """
        logged = []

        def _with_fields(fields):
            logger = mock.MagicMock()
            for level in ('debug', 'info', 'warning', 'error'):
                getattr(logger, level).side_effect = (
                    lambda message, _level=level, _fields=fields:
                    logged.append((_level, _fields, message)))
            return logger

        with mock.patch('shakenfist.external_api.base.LOG') as mock_log:
            mock_log.with_fields.side_effect = _with_fields
            resp = self.client.get(
                '/auth/namespaces', headers={'Authorization': token},
                environ_base={'REMOTE_ADDR': '10.0.0.5'})
        self.assertEqual(401, resp.status_code)
        return logged

    def _fields_for(self, logged, message):
        """The fields logged alongside ``message``, as a dict."""
        matching = [(level, fields) for level, fields, logged_message
                    in logged if logged_message == message]
        self.assertNotEqual(
            [], matching,
            f'{message!r} was never logged, so this test is not '
            f'exercising what it claims to')

        (level, fields) = matching[0]

        # A rejected credential is the client's problem, not the
        # cluster's, so it must not be logged at ERROR.
        self.assertEqual('info', level)
        self.assertIsInstance(
            fields, dict,
            'fields were not a dict, so with_fields() discarded them')
        return fields

    def _assert_attributed(self, fields):
        self.assertEqual('banana', fields['namespace'])
        self.assertEqual('key1', fields['keyname'])
        self.assertEqual('GET', fields['method'])
        self.assertEqual('/auth/namespaces', fields['path'])
        self.assertEqual('10.0.0.5', fields['remote-address'])

    def test_nonce_mismatch_is_attributed_and_not_an_error(self):
        token = self._mint()

        # Rotating the key mints a new nonce, so the outstanding token
        # is now a stale replay -- the routine case from issue 3606.
        Namespace.from_db('banana').add_key('key1', 'bacon')

        logged = self._replay(token)
        self._assert_attributed(
            self._fields_for(logged, 'JWT token has incorrect nonce'))

    def test_nonce_mismatch_is_audited_on_the_namespace(self):
        token = self._mint()
        Namespace.from_db('banana').add_key('key1', 'bacon')

        self.events = []
        self._replay(token)

        audited = [extra for message, extra in self.events
                   if message == 'JWT token has incorrect nonce']
        self.assertEqual(1, len(audited))
        self._assert_attributed(audited[0])

    def test_nonce_mismatch_does_not_log_the_nonce(self):
        token = self._mint()
        Namespace.from_db('banana').add_key('key1', 'bacon')
        nonce = Namespace.from_db('banana').lookup_key('key1').nonce

        logged = self._replay(token)
        fields = self._fields_for(logged, 'JWT token has incorrect nonce')
        for name, value in fields.items():
            self.assertNotIn(nonce, str(value),
                             f'the nonce leaked into the log in {name!r}')

    def test_removed_key_is_attributed_and_not_an_error(self):
        token = self._mint()
        Namespace.from_db('banana').remove_key('key1')

        logged = self._replay(token)
        self._assert_attributed(
            self._fields_for(logged, 'JWT token uses non-existent key'))

    def test_deleted_namespace_is_attributed_and_not_an_error(self):
        token = self._mint()
        Namespace.from_db('banana').state = dbo.STATE_DELETED

        logged = self._replay(token)
        self._assert_attributed(
            self._fields_for(logged, 'JWT token is for deleted namespace'))


class EventSecretsTestCase(base.ShakenFistTestCase):
    """Audit events must never carry tokens or key material.

    An event is readable by anyone who can read the object it belongs
    to, which is a weaker bar than the credential itself clears. A JWT
    sitting in an event log is replayable until it expires, and a
    stored key hash is offline-attackable, so neither belongs there.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        # Intercept at the eventlog rather than on any one object, so
        # that an event added by some other object type in the same
        # request is caught too.
        self.events = []
        patcher = mock.patch(
            'shakenfist.eventlog.add_event',
            side_effect=lambda event_type, object_type, object_uuid, message,
            duration=None, extra=None, **kwargs: self.events.append(
                (message, extra or {})))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.client = external_api.app.test_client()

    def _extras_for(self, fragment):
        """Every recorded extra dict whose message contains ``fragment``."""
        found = [extra for message, extra in self.events if fragment in message]
        self.assertNotEqual(
            [], found,
            f'no event matching {fragment!r} was recorded, so this test '
            f'is not exercising what it claims to')
        return found

    def _assert_absent_everywhere(self, secret):
        """``secret`` appears in no recorded event, under any key."""
        for message, extra in self.events:
            for name, value in extra.items():
                self.assertNotIn(
                    secret, str(value),
                    f'event {message!r} leaked a secret in {name!r}')

    def _mint(self, namespace='banana', key='bacon'):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': namespace, 'key': key}))
        self.assertEqual(200, resp.status_code)
        return resp.get_json()['access_token']

    def test_minting_a_token_does_not_log_the_token(self):
        token = self._mint()

        for extra in self._extras_for('token created from key'):
            self.assertNotIn('token', extra)
            self.assertEqual('key1', extra['keyname'])
        self._assert_absent_everywhere(token)

    def test_minting_a_token_does_not_log_the_nonce(self):
        # The nonce is the revocation handle. Publishing it tells a
        # reader which of their captured tokens are still live, and
        # confirms that a rotation has not happened yet.
        self._mint()

        for extra in self._extras_for('token created from key'):
            self.assertNotIn('nonce', extra)

        nonce = Namespace.from_db('banana').lookup_key('key1').nonce
        self._assert_absent_everywhere(nonce)

    def test_using_a_token_does_not_log_the_token(self):
        token = self._mint()
        self.events.clear()

        resp = self.client.get(
            '/auth/namespaces',
            headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(200, resp.status_code)

        for extra in self._extras_for('token used to authenticate request'):
            self.assertNotIn('token', extra)
            # The key name still identifies which credential was used,
            # which is the part an audit reader actually needs.
            self.assertEqual('key1', extra['keyname'])
        self._assert_absent_everywhere(token)

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_creating_a_namespace_does_not_log_the_token(self, mock_lock):
        token = self._mint(namespace='system', key='bar')
        self.events.clear()

        resp = self.client.post(
            '/auth/namespaces',
            headers={'Authorization': f'Bearer {token}'},
            data=json.dumps({'namespace': 'freshly-made'}))
        self.assertEqual(200, resp.status_code)

        # Both the invoking namespace's event and the new namespace's
        # own event are checked here.
        extras = self._extras_for('token used to create namespace')
        self.assertEqual(2, len(extras))
        for extra in extras:
            self.assertNotIn('token', extra)
            self.assertEqual('key1', extra['keyname'])
        self._assert_absent_everywhere(token)

    def test_a_malformed_key_does_not_log_the_key_body(self):
        # The key body held the stored hash and the nonce. Provoke the
        # ValueError path by making the bcrypt comparison itself fail.
        with mock.patch('bcrypt.checkpw',
                        side_effect=ValueError('invalid salt')):
            resp = self.client.post(
                '/auth',
                data=json.dumps({'namespace': 'banana', 'key': 'bacon'}))
        self.assertEqual(401, resp.status_code)

        for extra in self._extras_for('namespace key is invalid'):
            self.assertNotIn('key-body', extra)
            # The error and the key name are what make the malformed key
            # findable, and neither is secret.
            self.assertEqual('key1', extra['key_name'])
            self.assertIn('invalid salt', extra['error'])

        stored = Namespace.from_db('banana').lookup_key('key1')
        self._assert_absent_everywhere(stored.key)
        self._assert_absent_everywhere(stored.nonce)

    def test_the_request_trace_does_not_log_a_plaintext_key(self):
        # The API request tracing events in app.py log request and
        # response bodies verbatim. On /auth that request body is the
        # namespace's plaintext key -- worse than a token, because it
        # does not expire.
        self._mint()

        for extra in self._extras_for('api request received'):
            self.assertNotIn('bacon', str(extra['body']))
            # The URL survives, so the trace still says who called what.
            self.assertIn('/auth', extra['url'])
        self._assert_absent_everywhere('bacon')

    def test_the_request_trace_still_logs_ordinary_bodies(self):
        # The redaction is scoped to /auth, so it must not blind the
        # trace everywhere else.
        token = self._mint()
        self.events.clear()

        resp = self.client.get(
            '/instances', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(200, resp.status_code)

        bodies = [extra['body']
                  for extra in self._extras_for('api response sent')]
        for body in bodies:
            self.assertNotIn('credentials', str(body))


class ReservedKeyPrefixTestCase(base.ShakenFistTestCase):
    """The sfk_ prefix belongs to the cluster, not to operators.

    Reserving it is what makes rejecting a bad checksum at /auth sound:
    /auth cannot tell which stored key a presented secret is meant to
    match until it bcrypt compares against each one, so early rejection
    is only safe if no legitimate operator secret can carry the prefix
    and fail the checksum.
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False
        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        self.client = external_api.app.test_client()

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def _add_key(self, name, key=None, expiry=None):
        body = {'key_name': name}
        if key is not None:
            body['key'] = key
        if expiry is not None:
            body['expiry'] = expiry
        return self.client.post(
            '/auth/namespaces/banana/keys',
            headers={'Authorization': self.auth_token},
            data=json.dumps(body))

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_operator_cannot_supply_a_reserved_prefix(self, mock_lock):
        resp = self._add_key('mine', credentials.generate())
        self.assertEqual(400, resp.status_code)
        self.assertIn('reserved', resp.get_json()['error'])

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_reservation_covers_malformed_lookalikes_too(self, mock_lock):
        # Not just valid cluster secrets: anything wearing the prefix,
        # or the reservation would have a hole in exactly the shape
        # early rejection cares about.
        resp = self._add_key('mine', 'sfk_i-made-this-up')
        self.assertEqual(400, resp.status_code)

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_ordinary_operator_secrets_are_unaffected(self, mock_lock):
        resp = self._add_key('mine', 'a perfectly ordinary secret')
        self.assertEqual(200, resp.status_code)
        self.assertEqual('mine', resp.get_json())

    @mock.patch('shakenfist.locks.ClusterLock')
    def test_omitting_the_key_generates_one(self, mock_lock):
        resp = self._add_key('generated')
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual('generated', body['key_name'])
        self.assertTrue(credentials.looks_valid(body['key']))

        # And it actually authenticates, which is the only proof that
        # what was returned is what was stored.
        resp = self.client.post(
            '/auth',
            data=json.dumps({'namespace': 'banana', 'key': body['key']}))
        self.assertEqual(200, resp.status_code)

    def test_malformed_cluster_key_is_refused_at_auth(self):
        resp = self.client.post(
            '/auth',
            data=json.dumps({'namespace': 'banana',
                             'key': 'sfk_not_a_real_key_at_all'}))
        self.assertEqual(401, resp.status_code)

    @mock.patch('shakenfist.util.credentials.looks_valid')
    def test_early_rejection_skips_the_bcrypt_comparisons(self, mock_valid):
        # The point of checking the checksum first is to avoid the
        # per-key bcrypt work, so assert the comparison never happens
        # rather than only that the response is a 401.
        mock_valid.return_value = False
        with mock.patch('bcrypt.checkpw') as mock_checkpw:
            resp = self.client.post(
                '/auth',
                data=json.dumps({'namespace': 'banana',
                                 'key': 'sfk_bogus'}))
        self.assertEqual(401, resp.status_code)
        mock_checkpw.assert_not_called()

    def test_early_rejection_does_not_amplify_event_writes(self):
        # /auth is public, so anyone who knows a namespace name can
        # reach the checksum check. If it wrote its own event, a caller
        # could drive writes into that namespace's audit log for less
        # work than the bcrypt path costs -- cheaper per event than the
        # exposure that already exists.
        events = []
        patcher = mock.patch(
            'shakenfist.eventlog.add_event',
            side_effect=lambda event_type, object_type, object_uuid,
            message, duration=None, extra=None, **kwargs: events.append(
                message))
        patcher.start()
        self.addCleanup(patcher.stop)

        self.client.post('/auth', data=json.dumps(
            {'namespace': 'banana', 'key': 'sfk_bogus'}))
        malformed = list(events)

        events.clear()
        self.client.post('/auth', data=json.dumps(
            {'namespace': 'banana', 'key': 'just-wrong'}))
        ordinary = list(events)

        self.assertEqual(
            len(ordinary), len(malformed),
            'a malformed cluster key wrote a different number of events '
            f'({malformed}) than an ordinary wrong key ({ordinary})')
