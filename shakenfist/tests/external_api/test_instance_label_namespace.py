"""Which namespace a `label:<name>` on instance create means.

POST /instances takes a `namespace` which need not be the caller's own
-- `system`, and any namespace the target trusts, may create an instance
somewhere else -- and two body values accept a `label:` shorthand: a
diskspec's `base`, and `nvram_template`. Both used to build the artifact
URL from `request_namespace()` and then resolve it scoped to the target
namespace, so the URL named one namespace while the visibility predicate
was evaluated against another. A `system` caller creating an instance in
`tenant` looked for `sf://label/system/<name>` inside `tenant`, which
Artifact.from_url() does not create, so the request answered 404
"artifact not found" for a label both namespaces could have (issue
4830).

The shorthand now means a label in the namespace the instance is being
created in, so the URL and the scope agree. These tests assert the
*artifact that was found* rather than only the status code, because both
answers to the question are a 404 when the label is missing and only the
object identity says which namespace was consulted.

resolve_to_blob is patched to return None throughout. The harness has no
blobs, so the labels here would resolve to nothing anyway; patching it
with autospec is what records which artifact the handler reached, and
the resulting 404 is a different refusal from the safety check's one,
which is what makes "it found the label" visible from the outside.
"""

import json
from unittest import mock

from shakenfist.artifact import LABEL_URL
from shakenfist.artifact import Artifact
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import instance as instance_api
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class InstanceLabelNamespaceTestCase(base.ShakenFistTestCase):
    """A label named `thing`, in up to two namespaces at once."""

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for ns in ['system', 'tenant', 'stranger']:
            self.mock_mariadb.create_namespace(ns, 'key1', '%skey' % ns)

        self.client = external_api.app.test_client()

    def _label(self, namespace, name='thing'):
        a = Artifact.new(
            Artifact.TYPE_LABEL, '%s%s/%s' % (LABEL_URL, namespace, name),
            name=name, namespace=namespace)
        a.state = Artifact.STATE_CREATED
        return a

    def _token(self, namespace):
        secret = '%skey' % namespace
        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': namespace, 'key': secret}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _create(self, requestor, body):
        """POST /instances, with blob resolution stubbed out.

        SCHEDULER is contained the way test_instance_create_validation
        contains it: the handler caches one in a module global, and a
        request which reached placement would leave a real one behind
        for every later test in this worker process.
        """
        with mock.patch.object(instance_api, 'SCHEDULER', None), \
                mock.patch.object(
                    Artifact, 'resolve_to_blob', autospec=True,
                    return_value=None) as resolve, \
                mock.patch.object(
                    api_base.util_exceptions, 'record_exception',
                    return_value={'exception-record': 'test'}) as recorded:
            resp = self.client.post(
                '/instances', data=json.dumps(body),
                content_type='application/json',
                headers={'Authorization': self._token(requestor)})
        recorded.assert_not_called()
        return resp, resolve

    def _disk_body(self, namespace=None, base_value='label:thing'):
        body = {
            'name': 'labeltest',
            'cpus': 1,
            'memory': 1024,
            'disk': [{'size': 8, 'base': base_value}]
        }
        if namespace:
            body['namespace'] = namespace
        return body

    def _nvram_body(self, namespace=None, template='label:thing'):
        body = {
            'name': 'labeltest',
            'cpus': 1,
            'memory': 1024,
            'disk': [{'size': 8}],
            'nvram_template': template
        }
        if namespace:
            body['namespace'] = namespace
        return body

    def assertResolvedTo(self, resolve, artifact):
        """The handler reached this artifact and no other."""
        resolve.assert_called_once()
        self.assertEqual(str(artifact.uuid),
                         str(resolve.call_args[0][0].uuid))


class DiskBaseLabelTestCase(InstanceLabelNamespaceTestCase):
    """`base: label:thing` on a diskspec."""

    def test_the_target_namespaces_label_is_used(self):
        # The issue's case. `system` creates in `tenant`, and the label
        # exists only in `tenant`: before the fix the URL said
        # sf://label/system/thing and nothing matched, so this was a 404
        # "artifact not found" with resolve_to_blob never called.
        tenants = self._label('tenant')
        resp, resolve = self._create('system', self._disk_body('tenant'))

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertEqual('Could not resolve label thing to a blob',
                         resp.get_json()['error'])
        self.assertResolvedTo(resolve, tenants)

    def test_the_callers_own_label_is_not_what_it_means(self):
        # The other half of the decision, and the reason the status code
        # alone is not the assertion: a 404 here is the right answer for
        # a label the target namespace does not have, even though the
        # caller has one by that name. `system`'s label is not shared,
        # so it is not visible in `tenant` either.
        self._label('system')
        resp, resolve = self._create('system', self._disk_body('tenant'))

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertEqual('artifact not found', resp.get_json()['error'])
        resolve.assert_not_called()

    def test_the_target_is_chosen_when_both_namespaces_have_one(self):
        # The sharpest form of the question: with a label of the same
        # name either side, the namespace in the URL decides which one
        # boots, and a wrong answer here is a tenant instance silently
        # booting the administrator's image.
        self._label('system')
        tenants = self._label('tenant')
        resp, resolve = self._create('system', self._disk_body('tenant'))

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertResolvedTo(resolve, tenants)

    def test_a_caller_in_its_own_namespace_is_unchanged(self):
        # The control. Caller and target are the same namespace in
        # essentially every real request, and that case must not have
        # moved.
        tenants = self._label('tenant')
        resp, resolve = self._create('tenant', self._disk_body())

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertResolvedTo(resolve, tenants)

    def test_a_trusted_caller_also_gets_the_target_namespaces_label(self):
        # Not a system-only rule. `tenant` trusting `stranger` is what
        # authorises the create at all, and the shorthand means the same
        # thing for that caller as it does for an administrator -- which
        # also means `stranger`'s private label does not reach a
        # `tenant` instance.
        Namespace.from_db('tenant').add_trust('stranger')
        self._label('stranger')
        tenants = self._label('tenant')
        resp, resolve = self._create('stranger', self._disk_body('tenant'))

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertResolvedTo(resolve, tenants)

    def test_a_shared_label_may_still_be_named_in_full(self):
        # What a caller does instead when it really did mean another
        # namespace's label: the explicit URL form, which resolves by
        # visibility and so needs the label shared with (or its
        # namespace trusted by) the target namespace. Asserted so the
        # narrowing above is on the record as having an escape hatch.
        systems = self._label('system')
        systems.shared = True
        resp, resolve = self._create(
            'system',
            self._disk_body('tenant',
                            base_value='%ssystem/thing' % LABEL_URL))

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertResolvedTo(resolve, systems)


class NvramTemplateLabelTestCase(InstanceLabelNamespaceTestCase):
    """`nvram_template: label:thing`.

    The same defect, in the second place the shorthand is parsed. The
    diskspecs here carry no base, so the only artifact resolution in
    these requests is the NVRAM one.
    """

    def test_the_target_namespaces_label_is_used(self):
        tenants = self._label('tenant')
        resp, resolve = self._create('system', self._nvram_body('tenant'))

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertEqual('Could not resolve label thing to a blob',
                         resp.get_json()['error'])
        self.assertResolvedTo(resolve, tenants)

    def test_the_callers_own_label_is_not_what_it_means(self):
        self._label('system')
        resp, resolve = self._create('system', self._nvram_body('tenant'))

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertEqual('artifact not found', resp.get_json()['error'])
        resolve.assert_not_called()

    def test_the_target_is_chosen_when_both_namespaces_have_one(self):
        self._label('system')
        tenants = self._label('tenant')
        resp, resolve = self._create('system', self._nvram_body('tenant'))

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertResolvedTo(resolve, tenants)

    def test_a_caller_in_its_own_namespace_is_unchanged(self):
        tenants = self._label('tenant')
        resp, resolve = self._create('tenant', self._nvram_body())

        self.assertEqual(404, resp.status_code, resp.get_json())
        self.assertResolvedTo(resolve, tenants)
