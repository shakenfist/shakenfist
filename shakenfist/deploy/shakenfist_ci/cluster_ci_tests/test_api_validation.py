# Copyright 2019 Michael Still and contributors

import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


# Text that must never appear in a validation refusal body. Before D14's
# unknown=RAISE, an undeclared body key which reached its handler answered
# with something like "AuthEndpoint.post() got an unexpected keyword
# argument 'zzz'" -- issue #3612 in its purest form. The enforced shape is
# {"error": "<name>: not declared by this endpoint", "status": 400} instead.
#
# This is a subset of INTERPRETER_TEXT in
# shakenfist/tests/external_api/test_request_validation.py, which asserts
# the same property against the same responses in the unit suite. The list
# is repeated rather than imported because this suite runs from an
# installed shakenfist_ci package against a remote cluster and cannot
# import the server's test tree; the entries dropped here are the ones
# which describe a Python source layout the client cannot see anyway. Add
# a marker to both, or the two will drift.
_INTERPRETER_TEXT_MARKERS = (
    'got an unexpected keyword argument',
    'Traceback',
    'TypeError',
)


# This suite intentionally pins only the enforced 400 shape above, not the
# opaque {"error": "server error", "status": 500} shape decision D31
# settled on for a genuine handler-internal fault. The 400 is reachable on
# purpose -- an ordinary caller sends an undeclared parameter every day --
# but there is no known way to drive a real endpoint into an internal
# TypeError from this namespaced CI client without either white-box
# mocking (unavailable against a remote cluster, which is the whole point
# of this suite) or actually exploiting a live bug, and deliberately
# breaking a running cluster to exercise a failure path is out of bounds
# for this suite. The 500 shape is pinned instead in the unit suite --
# test_a_handler_internal_type_error_is_a_recorded_500 and the
# warn/off-mode tests in shakenfist/tests/external_api/
# test_request_validation.py -- where a handler can be safely mocked into
# raising. This absence is a decision, not an oversight.
class TestUndeclaredParameterRefused(base.BaseNamespacedTestCase):
    """PLAN-api-input-validation-phase-04-enforce.md step 6, test 1.

    API_VALIDATION_MODE defaults to 'enforce' (decision D16). D14 settled
    on webargs' unknown=RAISE for a body key no declaration names: the
    request is refused with a 400 rather than reaching a handler that
    would raise a TypeError of its own.

    Namespaced rather than admin on purpose: the refusal has to hold for
    an ordinary caller, which is the one that would otherwise have been
    handed a class and method name it has no other way to learn.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidation'
        super().__init__(*args, **kwargs)

    def test_undeclared_body_key_refused(self):
        # GET /instances declares exactly one body parameter, "all". An
        # additional, undeclared key must be refused before the handler
        # is ever called.
        #
        # _request_url is private, and used deliberately: no public
        # client method sends a key the API does not declare, which is
        # the whole point of the property under test. A client refactor
        # which renames it must update this test rather than assume
        # nothing depends on it.
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
        inst = self.create_instance(
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


class TestOmittedRequiredParameterRefused(base.BaseNamespacedTestCase):
    """PLAN-api-input-validation-phase-06-required.md, step 3.

    A parameter declared `required=True` used to be documentation: every
    one of them had a default in its handler's signature, so an omission
    reached the handler and was answered by whatever that handler did
    next. Enforcement makes an omission a 400 naming the parameter,
    before any handler runs.

    This is a contract change for callers, which is why it is tested
    here as well as in the unit suite: what the shipped client and the
    Ansible collection send is not something a unit test can see.
    `confirm` on a delete-all is the case to pick -- it is a parameter
    a caller really does omit by mistake, and the handler's own refusal
    (`parameter confirm is not set true`) is one of the few messages
    enforcement makes less specific, so this pins the replacement.

    In its own namespace because the request under test is a delete-all.
    If enforcement ever stopped working, this test would delete the
    namespace's instances -- and it must not be sharing one.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidationreq'
        super().__init__(*args, **kwargs)

    def test_delete_all_without_confirm_refused(self):
        # delete_all_instances() always sends confirm, which is the
        # point: no public client method omits a required parameter, so
        # the omission has to be built by hand.
        try:
            self.test_client._request_url(
                'DELETE', '/instances', data={'namespace': self.namespace})
            self.fail('A delete-all with no confirm was not refused')
        except apiclient.RequestMalformedException as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)

            body = json.loads(e.text)
            self.assertEqual(
                {'error': 'confirm: declared required but not supplied',
                 'status': 400},
                body)

            for marker in _INTERPRETER_TEXT_MARKERS:
                self.assertNotIn(
                    marker, e.text,
                    'Validation refusal leaked interpreter text: %s' % e.text)


class TestUserDataFormatEnforced(base.BaseNamespacedTestCase):
    """PLAN-api-input-validation-phase-06-required.md, step 4, and #3269.

    `user_data` publishes a `byte` format and, until this phase, was
    compiled to a string field which accepted anything. A caller who
    pasted raw cloud-config instead of the base64 of it got a 200, an
    instance which was scheduled and placed, and then a binascii error
    in a daemon log on a different machine when the config drive was
    built. The API now decodes it.

    Both halves are here because only one of them is the risk. Refusing
    raw cloud-config is the fix; still accepting everything the config
    drive accepts is the regression, and it is the one no unit test can
    honestly settle -- the decode this validator is standing in for
    happens in a different daemon, on a hypervisor, from a config drive
    this process never builds.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidationb64'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_unencoded_user_data_refused(self):
        """Issue 3269's input, at the real API."""
        try:
            # raw-create: the refusal is the assertion. create_instance()
            # in the base class waits out a 507, which would turn a
            # validation failure into a seven minute timeout rather than
            # the 400 this test is here to see.
            self.test_client.create_instance(
                'userdata-raw', 1, 1024,
                [{'network_uuid': self.net['uuid']}],
                [{'size': 8, 'base': base.CLUSTER_CI_IMAGE, 'type': 'disk'}],
                None, '#cloud-config\nruncmd:\n  - echo hello\n')
            self.fail('Unencoded user_data was not refused')
        except apiclient.RequestMalformedException as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)
            self.assertIn('user_data', e.text)

    def test_wrapped_base64_user_data_still_boots(self):
        """And the half which proves nothing was narrowed.

        `base64 user-data.yaml` wraps its output at 76 columns, and a
        caller running `sf-client instance create -U "$(base64
        user-data.yaml)"` sends those newlines to the API. The wrapping
        is rebuilt here rather than taken from load_userdata(), which
        emits a single line, because the wrapped form is the one a
        strict decoder would refuse and therefore the one worth
        driving through a real cluster.

        Reaching `ready` is the assertion. An instance whose config
        drive cannot be built does not get there, so a boot is the
        evidence that the value the API accepted is a value the
        hypervisor could still decode.
        """
        userdata = base.load_userdata('cluster_ci_tests', 'console_scribbler')
        wrapped = '\n'.join(
            userdata[i:i + 76] for i in range(0, len(userdata), 76)) + '\n'
        self.assertIn('\n', wrapped)

        inst = self.create_instance(
            'userdata-wrapped', 1, 1024,
            [{'network_uuid': self.net['uuid']}],
            [{'size': 8, 'base': base.CLUSTER_CI_IMAGE, 'type': 'disk'}],
            None, wrapped)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])
