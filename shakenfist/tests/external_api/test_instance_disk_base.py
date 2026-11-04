# Copyright 2026 Michael Still and contributors

"""What POST /instances answers for an ambiguous disk base.

A bare well-known shorthand such as centos:7 names an image the cluster
auto-fetches from config.IMAGE_DOWNLOAD_URL. It can also name a local
label, and the create handler used to silently prefer the auto-fetch, so
a label the caller had uploaded for that name was ignored with no signal
(issue #3271). Both readings are now refused as ambiguous.

These drive real authenticated requests through the whole decorator stack,
for the reason AuthenticatedStackTestCase documents.
"""

import json
import uuid
from unittest import mock

from shakenfist.artifact import Artifact
from shakenfist.external_api import base as api_base
from shakenfist.external_api import instance as instance_api
from shakenfist.tests.external_api.test_request_validation import (
    AuthenticatedStackTestCase)


class InstanceCreateAmbiguousBaseTestCase(AuthenticatedStackTestCase):
    """A bare shorthand that also names a local label is refused (#3271).

    A bare name:tag is genuinely ambiguous when both a well-known
    auto-fetchable image and a local label of that name exist. Preferring
    the auto-fetch silently shadows the label, and the mismatch surfaces
    much later as a boot failure rather than at create time.
    """

    mode = 'enforce'

    def _post(self, body):
        with mock.patch.object(instance_api, 'SCHEDULER', None), \
                mock.patch.object(
                    api_base.util_exceptions, 'record_exception',
                    return_value={'exception-record': 'test'}) as recorded:
            response = self.client.post(
                '/instances', data=json.dumps(body),
                content_type='application/json',
                headers={'Authorization': self.token})
        return response, recorded

    def test_a_shorthand_shadowing_a_label_is_refused(self):
        """Both readings valid, so the request is refused as ambiguous.

        The label is resolved exactly as the "label:" shorthand resolves a
        bare name, so a usable, resolvable label of this name is a
        competing reading of the base. Artifact.from_url is stubbed to
        stand in for that label, with the state, visibility and blob a
        real resolvable label would have.
        """
        label = mock.MagicMock()
        label.state.value = Artifact.STATE_CREATED
        label.shared = True
        label.namespace = 'system'
        label.resolve_to_blob.return_value = str(uuid.uuid4())

        body = {'name': 'ambig', 'cpus': 1, 'memory': 1024,
                'disk': [{'size': 10, 'base': 'centos'}]}
        with mock.patch.object(instance_api.Artifact, 'from_url',
                               return_value=label):
            response, recorded = self._post(body)

        self.assertEqual(400, response.status_code, response.get_json())
        error = response.get_json()['error']
        self.assertIn('is ambiguous', error)
        self.assertIn('sf://label/system/centos', error)
        recorded.assert_not_called()

    def test_a_shorthand_with_no_label_is_not_ambiguous(self):
        """The common case is unaffected: with no label of that name, the
        shorthand resolves to the well-known image and reaches placement
        (507 on a cluster with no hypervisor)."""
        body = {'name': 'noshadow', 'cpus': 1, 'memory': 1024,
                'disk': [{'size': 10, 'base': 'centos'}]}
        response, recorded = self._post(body)

        self.assertEqual(507, response.status_code, response.get_json())
        recorded.assert_not_called()

    def test_an_unresolvable_label_does_not_trigger_the_refusal(self):
        """A label that exists but resolves to no blob is not a competing
        reading, so the shorthand is still fetched rather than refused."""
        label = mock.MagicMock()
        label.state.value = Artifact.STATE_CREATED
        label.shared = True
        label.namespace = 'system'
        label.resolve_to_blob.return_value = None

        body = {'name': 'deadlabel', 'cpus': 1, 'memory': 1024,
                'disk': [{'size': 10, 'base': 'centos'}]}
        with mock.patch.object(instance_api.Artifact, 'from_url',
                               return_value=label):
            response, recorded = self._post(body)

        self.assertEqual(507, response.status_code, response.get_json())
        recorded.assert_not_called()
