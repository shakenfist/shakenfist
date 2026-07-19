# Copyright 2026 Michael Still and contributors
#
# Tests for the /admin/vditokenpubkey endpoint.

import json
import logging
import sys
from unittest import mock

from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class AdminVDITokenPublicKeyEndpointTestCase(base.ShakenFistTestCase):
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

        # The client must be created after all the mocks, or the mocks are
        # not correctly applied.
        self.client = external_api.app.test_client()

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    @mock.patch(
        'shakenfist.external_api.admin.vdi_tokens.get_signing_material')
    def test_get_returns_404_when_no_key_configured(self, mock_material):
        mock_material.return_value = None

        resp = self.client.get(
            '/admin/vditokenpubkey',
            headers={'Authorization': self.auth_token})

        self.assertEqual(404, resp.status_code)
        self.assertIn(
            'sf-ctl ensure-kerbside-signing-key', resp.get_json()['error'])

    @mock.patch(
        'shakenfist.external_api.admin.vdi_tokens.get_signing_material')
    def test_get_returns_public_view_when_key_configured(
            self, mock_material):
        mock_material.return_value = {
            'active_kid': 'abcd1234',
            'keys': [
                {
                    'kid': 'abcd1234',
                    'private_pem': '-----BEGIN PRIVATE KEY-----\nfoo\n'
                                   '-----END PRIVATE KEY-----\n',
                    'public_pem': '-----BEGIN PUBLIC KEY-----\nbar\n'
                                  '-----END PUBLIC KEY-----\n',
                    'created': 1789000000,
                },
            ],
        }

        resp = self.client.get(
            '/admin/vditokenpubkey',
            headers={'Authorization': self.auth_token})

        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual('abcd1234', body['active_kid'])
        self.assertEqual(1, len(body['keys']))
        self.assertEqual('abcd1234', body['keys'][0]['kid'])
        self.assertEqual('EdDSA', body['keys'][0]['alg'])
        self.assertEqual(
            {'kid', 'alg', 'public_pem', 'created'},
            set(body['keys'][0].keys()))

        serialised = json.dumps(body)
        self.assertNotIn('private', serialised.lower())
