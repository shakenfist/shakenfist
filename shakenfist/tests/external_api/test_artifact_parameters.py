# Copyright 2019 Michael Still and contributors
import json
from unittest import mock

from shakenfist.artifact import Artifact
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class ArtifactIntegerParameterTestCase(base.ShakenFistTestCase):
    """The artifact endpoints must reject non-integer body parameters.

    Same defect class as issue 3609: log_request merges JSON body
    values into handler kwargs verbatim, so a JSON null reaches int()
    as None. Guarding only ValueError let that TypeError escape to
    handle_authorization_exceptions, which returned the interpreter's
    own message to the client as a 400.
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

        self.artifact = Artifact.new(
            Artifact.TYPE_IMAGE, 'https://example.com/cirros.img',
            namespace='system')

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth_token = 'Bearer %s' % resp.get_json()['access_token']

    def _headers(self):
        return {'Authorization': self.auth_token}

    def test_null_max_versions_is_a_clean_400(self):
        resp = self.client.post(
            '/artifacts/%s/versions' % self.artifact.uuid,
            headers=self._headers(),
            data=json.dumps({'max_versions': None}))

        self.assertEqual(400, resp.status_code)
        self.assertEqual('max version is not an integer',
                         resp.get_json()['error'])

    def test_non_numeric_max_versions_is_a_clean_400(self):
        resp = self.client.post(
            '/artifacts/%s/versions' % self.artifact.uuid,
            headers=self._headers(),
            data=json.dumps({'max_versions': 'banana'}))

        self.assertEqual(400, resp.status_code)
        self.assertEqual('max version is not an integer',
                         resp.get_json()['error'])

    def test_max_versions_is_set(self):
        with mock.patch.object(Artifact, 'max_versions',
                               new_callable=mock.PropertyMock) as mv:
            resp = self.client.post(
                '/artifacts/%s/versions' % self.artifact.uuid,
                headers=self._headers(),
                data=json.dumps({'max_versions': '7'}))

            self.assertEqual(200, resp.status_code)
            mv.assert_called_with(7)

    def test_null_version_id_is_a_clean_400(self):
        # The route carries version_id in the path, but log_request
        # overwrites path kwargs with body values of the same name, so
        # a body null reaches int() regardless of what the URL said.
        resp = self.client.delete(
            '/artifacts/%s/versions/1' % self.artifact.uuid,
            headers=self._headers(),
            data=json.dumps({'version_id': None}))

        self.assertEqual(400, resp.status_code)
        self.assertEqual('version index is not an integer',
                         resp.get_json()['error'])

    def test_non_numeric_version_id_is_a_clean_400(self):
        resp = self.client.delete(
            '/artifacts/%s/versions/banana' % self.artifact.uuid,
            headers=self._headers())

        self.assertEqual(400, resp.status_code)
        self.assertEqual('version index is not an integer',
                         resp.get_json()['error'])
