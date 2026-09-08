import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


# Text that must never appear in a validation refusal body. Before D14's
# unknown=RAISE, an undeclared body key which reached its handler answered
# with something like "AuthEndpoint.post() got an unexpected keyword
# argument 'zzz'" -- issue #3612 in its purest form. The enforced shape is
# {"error": "<name>: not declared by this endpoint", "status": 400} instead.
_INTERPRETER_TEXT_MARKERS = (
    'got an unexpected keyword argument',
    'Traceback',
    'TypeError',
)


class TestUndeclaredParameterRefused(base.BaseNamespacedTestCase):
    """PLAN-api-input-validation-phase-04-enforce.md step 6, test 1.

    API_VALIDATION_MODE defaults to 'enforce' (decision D16). D14 settled
    on webargs' unknown=RAISE for a body key no declaration names: the
    request is refused with a 400 rather than reaching a handler that
    would raise a TypeError of its own.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidation'
        super().__init__(*args, **kwargs)

    def test_undeclared_body_key_refused(self):
        # GET /instances declares exactly one body parameter, "all". An
        # additional, undeclared key must be refused before the handler
        # is ever called.
        try:
            self.test_client._request_url(
                'GET', '/instances',
                data={'all': False, 'no_such_parameter': 'zzz'})
            self.fail('Undeclared body parameter was not refused')
        except apiclient.RequestMalformedException as e:
            self.addDetail(
                'response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)

            body = json.loads(e.text)
            self.assertEqual(
                {'error': 'no_such_parameter: not declared by this endpoint',
                 'status': 400},
                body)

            for marker in _INTERPRETER_TEXT_MARKERS:
                self.assertNotIn(
                    marker, e.text,
                    'Validation refusal leaked interpreter text: %s' % e.text)


class TestNamespaceBodyParameterStillWorks(base.BaseNamespacedTestCase):
    """PLAN-api-input-validation-phase-04-enforce.md step 6, test 2.

    Regression test for issue #3739. Before step 1 of this phase declared
    a `namespace` body parameter on every handler behind
    `arg_is_instance_ref` / `arg_is_network_ref`, turning on D14's
    unknown=RAISE would have refused the shipped client's own
    cross-namespace lookups: `get_instance`, `delete_instance`,
    `get_network` and `delete_network` all send `{'namespace': ...}` in
    the request body once the server advertises the
    `get-instance-namespace` / `get-network-namespace` capability tokens
    (both unconditional in app.py's API_CAPABILITIES). This is the more
    important of the two tests in step 6 -- it is what would have caught
    #3739 before the warn window did.

    `get_artifact` is not exercised here: unlike `get_instance` and
    `get_network`, it takes no `namespace=` argument and sends no body at
    all (see `TestArtifactLookupByName.test_artifact_system_creds_namespace_scoped`
    in test_artifacts.py, which drives the artifact endpoint's namespace
    body parameter directly via `_request_url` for that reason), so it
    cannot regress this way.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidationns'
        super().__init__(*args, **kwargs)

    def test_get_instance_with_namespace_body_parameter(self):
        minimal_disk = [{'size': 1, 'type': 'disk'}]
        inst = self.test_client.create_instance(
            'namespace-body-param', 1, 128, None, minimal_disk, None, None,
            namespace=self.namespace)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        # The shipped client's get_instance() sends {'namespace': ...} in
        # the request body -- exactly the call #3739 broke.
        found = self.system_client.get_instance(
            inst['uuid'], namespace=self.namespace)
        self.addDetail(
            'found',
            content.text_content(json.dumps(found, indent=4, sort_keys=True)))
        self.assertEqual(inst['uuid'], found['uuid'])

    def test_get_network_with_namespace_body_parameter(self):
        net = self.test_client.allocate_network(
            '192.168.250.0/24', True, True, 'namespace-body-param-net')
        self.addDetail(
            'net',
            content.text_content(json.dumps(net, indent=4, sort_keys=True)))

        # The shipped client's get_network() sends {'namespace': ...} in
        # the request body -- exactly the call #3739 broke.
        found = self.system_client.get_network(
            net['uuid'], namespace=self.namespace)
        self.addDetail(
            'found',
            content.text_content(json.dumps(found, indent=4, sort_keys=True)))
        self.assertEqual(net['uuid'], found['uuid'])
