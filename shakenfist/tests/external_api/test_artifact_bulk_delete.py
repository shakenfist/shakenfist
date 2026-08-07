"""The bulk `DELETE /artifacts` route must return a serializable body.

The handler returns a plain list of the deleted artifact uuids rather
than an external view, so nothing between it and flask_restful's JSON
encoder stringifies them. When `BaseObject.uuid` became a `uuid.UUID`
the deletions kept committing but the response crashed with
`TypeError: Object of type UUID is not JSON serializable`, failing
namespace deletes after their artifacts were already gone (issue 3656).

These tests go through the flask test client so the real flask_restful
representation runs -- asserting on the handler's return value alone
would not have caught this.
"""

import json

from shakenfist.artifact import Artifact
from shakenfist.tests.external_api.test_artifact_access import (
    ArtifactAccessFixture)


class ArtifactBulkDeleteTestCase(ArtifactAccessFixture):
    def _bulk_delete(self, namespace, body):
        return self.client.delete(
            '/artifacts', data=json.dumps(body),
            headers={'Authorization': self._token(namespace)})

    def test_owner_bulk_delete_returns_deleted_uuids(self):
        resp = self._bulk_delete('owner', {'confirm': True})
        self.assertEqual(200, resp.status_code)
        self.assertEqual([str(self.artifact.uuid)], resp.get_json())
        self.assertEqual(
            Artifact.STATE_DELETED,
            Artifact.from_db(self.artifact.uuid).state.value)

    def test_bulk_delete_requires_confirm(self):
        resp = self._bulk_delete('owner', {})
        self.assertEqual(400, resp.status_code)
        self.assertNotEqual(
            Artifact.STATE_DELETED,
            Artifact.from_db(self.artifact.uuid).state.value)

    def test_empty_namespace_returns_empty_list(self):
        resp = self._bulk_delete('stranger', {'confirm': True})
        self.assertEqual(200, resp.status_code)
        self.assertEqual([], resp.get_json())
