# Copyright 2019 Michael Still and contributors
import json
from unittest import mock

from shakenfist.artifact import Artifact
from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class ArtifactIntegerParameterTestCase(base.ShakenFistTestCase):
    """The artifact endpoints must reject non-integer body parameters.

    Same defect class as issue 3609: a JSON null reaches int() as
    None, and guarding only ValueError let that TypeError escape and
    return the interpreter's message to the client.
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

    def test_infinite_max_versions_is_a_clean_400(self):
        # int(float('inf')) raises OverflowError rather than TypeError
        # or ValueError, so this used to be a 500 with an interpreter
        # message in the body. The raw body is sent because Python's
        # JSON parser accepts the non-standard Infinity literal.
        resp = self.client.post(
            '/artifacts/%s/versions' % self.artifact.uuid,
            headers=self._headers(),
            data='{"max_versions": Infinity}',
            content_type='application/json')

        self.assertEqual(400, resp.status_code)
        self.assertEqual('max version is not an integer',
                         resp.get_json()['error'])

    def test_infinite_version_id_is_a_clean_400(self):
        resp = self.client.delete(
            '/artifacts/%s/versions/1' % self.artifact.uuid,
            headers=self._headers(),
            data='{"version_id": -Infinity}',
            content_type='application/json')

        self.assertEqual(400, resp.status_code)
        self.assertEqual('version index is not an integer',
                         resp.get_json()['error'])

    def test_negative_max_versions_is_a_clean_400(self):
        """Destructive, not just meaningless: delete_old_versions()
        computes sorted(indexes)[:-max], so -1 deletes the oldest
        version on every index add with no error to the caller."""
        for value in (-1, '-2'):
            resp = self.client.post(
                '/artifacts/%s/versions' % self.artifact.uuid,
                headers=self._headers(),
                data=json.dumps({'max_versions': value}))

            self.assertEqual(400, resp.status_code, 'max_versions %r' % value)
            self.assertEqual('max version must not be negative',
                             resp.get_json()['error'])

    def test_zero_max_versions_reverts_to_the_default(self):
        """Zero has always meant 'use the configured default', so it
        must not be caught by the negative check."""
        resp = self.client.post(
            '/artifacts/%s/versions' % self.artifact.uuid,
            headers=self._headers(),
            data=json.dumps({'max_versions': 0}))

        self.assertEqual(200, resp.status_code)
        self.assertEqual(config.ARTIFACT_MAX_VERSIONS_DEFAULT,
                         self.artifact.max_versions)

    def test_a_negative_stored_maximum_is_harmless(self):
        """A row written before the API rejected negatives still has
        to be safe, so the getter treats it the way it treats zero."""
        self.artifact._update_attributes(max_versions=-1)

        self.assertEqual(config.ARTIFACT_MAX_VERSIONS_DEFAULT,
                         self.artifact.max_versions)

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

    def test_negative_version_id_is_a_404(self):
        """A negative index is well formed but cannot match, so it
        falls through to not-found. Pinned because the missing range
        check is deliberate: unlike max_versions, an out of range
        version id does nothing."""
        resp = self.client.delete(
            '/artifacts/%s/versions/-1' % self.artifact.uuid,
            headers=self._headers())

        self.assertEqual(404, resp.status_code)

    def test_non_numeric_version_id_is_a_clean_400(self):
        resp = self.client.delete(
            '/artifacts/%s/versions/banana' % self.artifact.uuid,
            headers=self._headers())

        self.assertEqual(400, resp.status_code)
        self.assertEqual('version index is not an integer',
                         resp.get_json()['error'])
