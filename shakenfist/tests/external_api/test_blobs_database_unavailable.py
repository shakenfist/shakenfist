# Copyright 2026 Michael Still and contributors
#
# GET /blobs used to answer 200 with an empty list when the active-blob
# read failed, which is indistinguishable from a cluster with no blobs.
# Now that mariadb.get_active_blob_uuids() raises DatabaseUnavailable
# rather than flattening a failed read to [] (#3638), the endpoint has to
# answer 503 -- an observable REST contract change, so it is pinned here
# and declared in the endpoint's swagger responses rather than left to
# prose.

import json
import logging
import sys
from unittest import mock

from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class BlobsEndpointDatabaseUnavailableTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')

        # The client must be created after all the mocks, or the mocks are
        # not correctly applied.
        self.client = external_api.app.test_client()

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    @mock.patch('shakenfist.external_api.blob.mariadb.get_active_blob_uuids')
    def test_unreadable_blob_list_is_a_503_not_an_empty_list(
            self, mock_get_active):
        mock_get_active.side_effect = DatabaseUnavailable(
            'could not read the list of active blobs')

        resp = self.client.get(
            '/blobs', headers={'Authorization': self.auth_token})

        self.assertEqual(503, resp.status_code)
        self.assertIn('database unavailable', resp.get_json()['error'])

    @mock.patch('shakenfist.external_api.blob.Blob.from_db')
    @mock.patch('shakenfist.external_api.blob.mariadb.get_active_blob_uuids')
    def test_genuinely_empty_is_still_a_200(
            self, mock_get_active, mock_from_db):
        # The counter-case: an empty result set is a real answer and must
        # not be confused with the failure above.
        mock_get_active.return_value = []

        resp = self.client.get(
            '/blobs', headers={'Authorization': self.auth_token})

        self.assertEqual(200, resp.status_code)
        self.assertEqual([], resp.get_json())
        mock_from_db.assert_not_called()
