"""Granting a trust names a namespace which has to exist.

Issue 4829. A trust names a namespace by name, and a name with no
namespace behind it is free for anybody to create, so granting a trust
for a name which does not exist grants it to whoever takes the name
next. That is the same hazard revoke_inbound_trust() closes on the
delete side, arriving by the other door: the grant handler used to
write any string the caller sent into the trust list.
"""

import json
import logging
import sys

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NamespaceTrustGrantTestCase(base.ShakenFistTestCase):
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
        self.mock_mariadb.create_namespace('truster', 'key1', 'trustkey')
        self.mock_mariadb.create_namespace('trustee', 'key1', 'trusteekey')
        self.mock_mariadb.create_namespace('doomed', 'key1', 'doomedkey')

        self.client = external_api.app.test_client()

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.token = 'Bearer %s' % resp.get_json()['access_token']

    def _grant(self, external_namespace):
        return self.client.post(
            '/auth/namespaces/truster/trust',
            headers={'Authorization': self.token},
            data=json.dumps({'external_namespace': external_namespace}))

    def test_grant_to_a_live_namespace_works(self):
        resp = self._grant('trustee')

        self.assertEqual(200, resp.status_code)
        self.assertEqual(['system', 'trustee'],
                         resp.get_json()['trust']['full'])
        self.assertIn('trustee', Namespace.from_db('truster').trust)

    def test_grant_to_a_name_which_does_not_exist_is_refused(self):
        resp = self._grant('never-was')

        self.assertEqual(404, resp.status_code)
        self.assertEqual('external namespace not found',
                         resp.get_json()['error'])
        self.assertEqual(['system'], Namespace.from_db('truster').trust)

    def test_grant_to_a_deleted_namespace_is_refused(self):
        # Deleting the namespace already revoked every trust naming it,
        # so letting a caller add one back would reinstate exactly the
        # entry the delete path went out of its way to remove.
        Namespace.from_db('doomed').state = dbo.STATE_DELETED

        resp = self._grant('doomed')

        self.assertEqual(404, resp.status_code)
        self.assertEqual(['system'], Namespace.from_db('truster').trust)

    def test_an_empty_external_namespace_is_still_a_400(self):
        # The pre-existing refusal, which the new check must not have
        # turned into a 404.
        resp = self._grant('')

        self.assertEqual(400, resp.status_code)
        self.assertEqual('no external namespace specified',
                         resp.get_json()['error'])
