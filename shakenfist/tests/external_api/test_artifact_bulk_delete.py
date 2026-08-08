# Copyright 2019 Michael Still and contributors
"""The bulk DELETE /artifacts response must reach the client.

`ArtifactsEndpoint.delete` documents a 200 response of "a list of
artifact uuids that were deleted", but it used to build that list from
raw `uuid.UUID` objects. flask_restful serializes the return value
after the view function has finished, so `json.dumps` raised TypeError
and the caller received a 500 -- after the deletions had already been
committed (issue 3657). The sibling bulk endpoints (instances,
networks) stringify their uuids; the artifacts variant had diverged and
had no test on this path at all.

Every test here asserts on the decoded response body rather than just
the status code, because a response carrying UUID objects dies during
serialization and never produces a body to decode.
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

    def test_the_owner_gets_the_documented_uuid_list(self):
        resp = self._bulk_delete('owner', {'confirm': True})

        self.assertEqual(200, resp.status_code)
        self.assertEqual([str(self.artifact.uuid)], resp.get_json())
        self.assertEqual(
            Artifact.STATE_DELETED,
            Artifact.from_db(self.artifact.uuid).state.value)

    def test_a_system_caller_naming_the_namespace_gets_the_same_list(self):
        resp = self._bulk_delete(
            'system', {'confirm': True, 'namespace': 'owner'})

        self.assertEqual(200, resp.status_code)
        self.assertEqual([str(self.artifact.uuid)], resp.get_json())

    def test_an_unconfirmed_request_deletes_nothing(self):
        # The control: the 400 guard still fires before any deletion,
        # so a fixture change that broke the happy-path tests above
        # would show up here as a deleted artifact.
        resp = self._bulk_delete('owner', {})

        self.assertEqual(400, resp.status_code)
        self.assertNotEqual(
            Artifact.STATE_DELETED,
            Artifact.from_db(self.artifact.uuid).state.value)
