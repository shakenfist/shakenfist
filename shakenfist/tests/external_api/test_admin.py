# Copyright 2026 Michael Still and contributors
#
# Tests for the /admin/vditokenpubkey and /admin/resources endpoints.

import json
import logging
import sys
from unittest import mock

from shakenfist import mariadb
from shakenfist.external_api import admin as api_admin
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class AdminEndpointTestCase(base.ShakenFistTestCase):
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


class AdminVDITokenPublicKeyEndpointTestCase(AdminEndpointTestCase):
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

    @mock.patch(
        'shakenfist.external_api.admin.vdi_tokens.get_signing_material')
    def test_get_returns_500_with_message_on_corrupt_material(
            self, mock_material):
        # A stored row that is valid JSON but missing a member must not
        # escape as a bare KeyError; it becomes a 500 naming the problem.
        mock_material.return_value = {'active_kid': 'abcd1234'}

        resp = self.client.get(
            '/admin/vditokenpubkey',
            headers={'Authorization': self.auth_token})

        self.assertEqual(500, resp.status_code)
        self.assertIn('corrupt', resp.get_json()['error'])
        self.assertIn("'keys'", resp.get_json()['error'])


class AdminResourcesEndpointTestCase(AdminEndpointTestCase):
    """The published example has to be the shape the endpoint returns.

    Client scripts are written against the example, so a key the
    summary gains or loses without the example following is a contract
    quietly broken.
    """

    def test_the_example_has_every_key_the_response_has(self):
        example = json.loads(api_admin.admin_resources_get_example)

        self.mock_mariadb.set_node_metrics_same()
        stale = self.mock_mariadb.node_uuids['node2']
        self.mock_mariadb.node_metrics_store[stale]['timestamp'] = 0
        for fqdn in ['node3', 'node4']:
            self.mock_mariadb.set_node_capacity(
                self.mock_mariadb.node_uuids[fqdn], limit_cpus=12,
                limit_memory_mb=60000, limit_disk_gb=100)
        counters = {
            k: v for k, v in example['cluster_capacity'].items()
            if k in mariadb.CLUSTER_CAPACITY_COUNTERS}
        self.mock_mariadb.cluster_capacity_read = mariadb.ClusterCapacityRead(
            row=mariadb._cluster_capacity_dict(
                counters, example['cluster_capacity']['updated_at']),
            degraded=False)

        resp = self.client.get(
            '/admin/resources', headers={'Authorization': self.auth_token})
        self.assertEqual(200, resp.status_code)
        body = resp.get_json()

        self.assertEqual(set(example), set(body))
        self.assertEqual(set(example['total']), set(body['total']))
        example_node = next(iter(example['per_node'].values()))
        for node in body['per_node'].values():
            self.assertEqual(set(example_node), set(node))
        self.assertEqual(
            set(next(iter(example['excluded'].values()))),
            set(body['excluded'][stale]))
        # The example's ledger is the real derivation of its counters.
        self.assertEqual(example['cluster_capacity'], body['cluster_capacity'])
