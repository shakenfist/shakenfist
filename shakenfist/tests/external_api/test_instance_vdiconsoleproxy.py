# Copyright 2026 Michael Still and contributors
#
# Tests for the /instances/<ref>/vdiconsoleproxy endpoint
# (InstanceVDIProxyConsoleHelperEndpoint).

import json
import logging
import sys
from unittest import mock
from uuid import UUID
from uuid import uuid4

from shakenfist import eventlog
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import SFConfig
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.util import vdi_tokens

SPICE_VIDEO = {'model': 'cirrus', 'memory': 16384, 'vdi': 'spice'}
VNC_VIDEO = {'model': 'cirrus', 'memory': 16384, 'vdi': 'vnc'}


class InstanceVDIProxyConsoleHelperEndpointTestCase(base.ShakenFistTestCase):
    """End to end tests for GET /instances/<ref>/vdiconsoleproxy.

    Uses a real Flask test client, MockMariaDB backed namespaces and
    instances, and real JWT auth tokens minted via /auth, matching the
    harness in test_network.py's NetworkDeleteEnqueueTaskTestCase. Config
    is overridden per-test by patching the ``config`` name imported into
    ``shakenfist.external_api.instance``.
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
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')
        self.mock_mariadb.create_namespace('other', 'key1', 'bar')

        self.instance_uuid = str(uuid4())
        self.mock_mariadb.create_instance(
            'vdi-instance', uuid=self.instance_uuid, namespace='foo',
            video=SPICE_VIDEO, set_state=dbo.STATE_CREATED)

        self.client = external_api.app.test_client()

        self.owner_token = self._auth('foo', 'bar')
        self.other_token = self._auth('other', 'bar')
        self.system_token = self._auth('system', 'bar')

        self.fake_config = SFConfig(
            KERBSIDE_URL='https://kerbside.example.com',
            KERBSIDE_TOKEN_DURATION=300,
            ZONE='zone-a',
        )
        self.config_patch = mock.patch(
            'shakenfist.external_api.instance.config', self.fake_config)
        self.mock_config = self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def _auth(self, namespace, key):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': namespace, 'key': key}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _get(self, token, instance_uuid=None):
        return self.client.get(
            '/instances/%s/vdiconsoleproxy' % (
                instance_uuid or self.instance_uuid),
            headers={'Authorization': token})

    def test_feature_off_returns_404(self):
        with mock.patch(
                'shakenfist.external_api.instance.config',
                SFConfig(KERBSIDE_URL='', ZONE='zone-a')):
            resp = self._get(self.owner_token)

        self.assertEqual(404, resp.status_code)
        self.assertIn(
            'kerbside integration is not configured',
            resp.get_json()['error'])

    @mock.patch(
        'shakenfist.external_api.instance.vdi_tokens.mint_console_token')
    def test_happy_path_returns_url_and_expiry(self, mock_mint):
        mock_mint.return_value = {
            'token': 'header.payload.signature',
            'jti': 'jti-123',
            'kid': 'kid-123',
            'expires_at': 1789000300,
        }

        resp = self._get(self.owner_token)

        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(
            'https://kerbside.example.com/sf-console.vv?'
            'token=header.payload.signature',
            body['url'])
        self.assertEqual(1789000300, body['expires_at'])
        self.assertIsInstance(body['expires_at'], int)
        self.assertEqual({'url', 'expires_at'}, set(body.keys()))

        serialised = json.dumps(body)
        self.assertNotIn('private', serialised.lower())

        # The endpoint must pass the instance uuid as a str, not the
        # uuid.UUID that instance_from_db.uuid returns, or PyJWT cannot
        # serialise it into the sub claim.
        mock_mint.assert_called_once_with(
            self.instance_uuid, 'foo',
            audience='https://kerbside.example.com', issuer='zone-a',
            duration=300)

        eventlog.add_event_multi.assert_any_call(
            EVENT_TYPE_AUDIT, [('instance', UUID(self.instance_uuid))],
            'vdi console proxy token minted', duration=None,
            extra={
                'jti': 'jti-123',
                'kid': 'kid-123',
                'namespace': 'foo',
                'expires_at': 1789000300,
            },
            suppress_event_logging=False, log_as_error=False)

    @mock.patch(
        'shakenfist.external_api.instance.vdi_tokens.mint_console_token')
    def test_signing_key_absent_returns_500(self, mock_mint):
        mock_mint.side_effect = vdi_tokens.SigningKeyError(
            'no signing key is configured')

        resp = self._get(self.owner_token)

        self.assertEqual(500, resp.status_code)
        self.assertIn(
            'sf-ctl ensure-kerbside-signing-key', resp.get_json()['error'])

    @mock.patch(
        'shakenfist.external_api.instance.vdi_tokens.mint_console_token')
    def test_non_owner_is_refused(self, mock_mint):
        resp = self._get(self.other_token)

        self.assertEqual(404, resp.status_code)
        self.assertEqual(
            'instance not found', resp.get_json()['error'])
        mock_mint.assert_not_called()

    @mock.patch(
        'shakenfist.external_api.instance.vdi_tokens.mint_console_token')
    def test_system_namespace_bypasses_ownership(self, mock_mint):
        mock_mint.return_value = {
            'token': 'header.payload.signature',
            'jti': 'jti-123',
            'kid': 'kid-123',
            'expires_at': 1789000300,
        }

        resp = self._get(self.system_token)

        self.assertEqual(200, resp.status_code)
        mock_mint.assert_called_once_with(
            self.instance_uuid, 'foo',
            audience='https://kerbside.example.com', issuer='zone-a',
            duration=300)

    @mock.patch(
        'shakenfist.external_api.instance.vdi_tokens.mint_console_token')
    def test_instance_not_created_returns_406(self, mock_mint):
        not_ready_uuid = str(uuid4())
        self.mock_mariadb.create_instance(
            'vdi-instance-not-ready', uuid=not_ready_uuid, namespace='foo',
            video=SPICE_VIDEO, set_state=dbo.STATE_INITIAL)

        resp = self._get(self.owner_token, instance_uuid=not_ready_uuid)

        self.assertEqual(406, resp.status_code)
        mock_mint.assert_not_called()

    @mock.patch(
        'shakenfist.external_api.instance.vdi_tokens.mint_console_token')
    def test_non_spice_vdi_returns_409(self, mock_mint):
        vnc_uuid = str(uuid4())
        self.mock_mariadb.create_instance(
            'vdi-instance-vnc', uuid=vnc_uuid, namespace='foo',
            video=VNC_VIDEO, set_state=dbo.STATE_CREATED)

        resp = self._get(self.owner_token, instance_uuid=vnc_uuid)

        self.assertEqual(409, resp.status_code)
        self.assertIn(
            'instance does not have a SPICE console',
            resp.get_json()['error'])
        mock_mint.assert_not_called()
