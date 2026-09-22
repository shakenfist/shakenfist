# Copyright 2019 Michael Still and contributors

import json

import requests
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


class TestNestedSpecKeysRefused(base.BaseNamespacedTestCase):
    """PLAN-api-input-validation-phase-07-structured.md, step 7.

    Phase 7 taught the validation vocabulary to describe what is
    *inside* a diskspec, a networkspec and a videospec. Until it did, a
    `dict` compiled to a marshmallow field which checked only that the
    value was a mapping, so every key inside one of those specs was
    unexamined: an unknown key was silently discarded and a wrong-typed
    value reached a handler which could not cope with it.

    The unit sweep -- shakenfist/tests/external_api/test_nested_sweep.py
    -- measures ninety nine of those values against a mocked MariaDB and
    a fixture with no hypervisor. The rows repeated here are the
    ones whose *old* behaviour a caller could see in production, and
    this is the only suite which can show that a real sf-api behind
    gunicorn now answers them the way the sweep says it does.

    None of these tests allocates a network or creates an instance, and that is
    a finding rather than a saving: a create which is refused by the
    validation layer never reaches the handler, so it never resolves a
    network, never reaches the scheduler and never places anything. If
    either of these tests ever needs a fixture to pass, the refusal has
    moved out of the validation layer and into the handler, which is a
    different contract than the one this file pins.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidnested'
        super().__init__(*args, **kwargs)

    def test_unknown_diskspec_key_refused(self):
        """Issue #936's original complaint, at the real API.

        A caller who typed `siz` instead of `size` used to get a 200 and
        a default sized disk, with nothing anywhere saying that the key
        they sent had been thrown away. D41 publishes
        `additionalProperties: false` for the diskspec, so the typo is
        now named in a 400.

        The body is asserted whole rather than just the status. With the
        typo the diskspec asks for neither a size nor a base, which is
        D45's new handler guard -- so a 400 alone cannot tell "the
        schema refused the unknown key" from "the guard refused the
        empty spec". The message can, and the message is what the
        caller has to act on.
        """
        try:
            # raw-create: the refusal is the assertion, so this must not
            # go through the capacity-waiting wrapper -- a validation
            # failure would become a seven minute timeout rather than
            # the 400 under test.
            self.test_client.create_instance(
                'diskspec-typo', 1, 1024, None,
                [
                    {
                        'siz': 20
                    }
                ], None, None)
            self.fail('An unknown diskspec key was not refused')
        except apiclient.RequestMalformedException as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)

            body = json.loads(e.text)
            self.assertEqual(
                {'error': 'disk[0].siz: Unknown field.', 'status': 400},
                body)

            for marker in _INTERPRETER_TEXT_MARKERS:
                self.assertNotIn(
                    marker, e.text,
                    'Validation refusal leaked interpreter text: %s' % e.text)

    def test_attribute_closing_model_refused(self):
        """Issue #4242, at the real API.

        A `network[].model` is rendered into a quoted attribute of the
        instance's libvirt domain XML, and the API publishes no enum
        for it on purpose (D43: the vocabulary is the hypervisor's qemu
        build). What it publishes instead is a character class, and a
        value which could close the attribute and write device elements
        into the domain definition must be refused with a 400 naming
        the key. The render site also escapes, but that half is not
        observable from a request which never creates an instance; the
        unit suite pins it (test_instance.py's
        InstanceDomainXMLEscapingTestCase).

        The network is deliberately one which does not exist: the
        refusal has to come from the validation layer, before the
        handler ever resolves a network, exactly as the class docstring
        demands of these tests.
        """
        try:
            # raw-create: as above, the refusal is the assertion.
            self.test_client.create_instance(
                'netspec-model-injection', 1, 1024,
                [
                    {
                        'network_uuid': 'nosuchnetwork',
                        'model': "virtio'/><x"
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': base.CLUSTER_CI_IMAGE,
                        'type': 'disk'
                    }
                ], None, None)
            self.fail('An attribute-closing model was not refused')
        except apiclient.RequestMalformedException as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)

            body = json.loads(e.text)
            self.assertEqual(
                {'error': ('network[0].model: String does not match '
                           'expected pattern.'),
                 'status': 400},
                body)

            for marker in _INTERPRETER_TEXT_MARKERS:
                self.assertNotIn(
                    marker, e.text,
                    'Validation refusal leaked interpreter text: %s' % e.text)

    def test_non_string_network_uuid_refused(self):
        """Item 3 of issue #4167, at the real API.

        An integer `network_uuid` reached `util_general.valid_uuid4()`,
        which does `value.replace(...)`, so the server answered a bare
        `server error` and wrote an exception record on a hypervisor for
        a request that was simply malformed. The networkspec now types
        the key, so the caller is told which element of which parameter
        was wrong.

        `string` and not a uuid format: a networkspec may name a network
        rather than identify it (D44), which is why the assertion here
        is about the type and not about the shape of a uuid.
        """
        try:
            # raw-create: as above, the refusal is the assertion.
            self.test_client.create_instance(
                'netspec-int-uuid', 1, 1024,
                [
                    {
                        'network_uuid': 5
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': base.CLUSTER_CI_IMAGE,
                        'type': 'disk'
                    }
                ], None, None)
            self.fail('A non-string network_uuid was not refused')
        except apiclient.InternalServerError as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.fail('A non-string network_uuid was answered with a 500, '
                      'which is issue #4167 unfixed: %s' % e.text)
        except apiclient.RequestMalformedException as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)

            body = json.loads(e.text)
            self.assertEqual(
                {'error': 'network[0].network_uuid: Not a valid string.',
                 'status': 400},
                body)

            for marker in _INTERPRETER_TEXT_MARKERS:
                self.assertNotIn(
                    marker, e.text,
                    'Validation refusal leaked interpreter text: %s' % e.text)

    def test_negative_disk_size_refused(self):
        """Issue 4248, at the real API.

        A negative `disk[].size` is summed straight into the requested
        capacity, and `admit_instance_placement`'s guarded UPDATE
        (`used + requested <= limit`) always admits a negative request
        and *deflates* the node's `used_disk_gb` -- inflating the
        capacity every other namespace's claims are admitted against.
        That was the only cross-namespace effect in the warn/off
        inventory of the phase 8 push audit.

        This suite runs at the `enforce` default, so the refusal pinned
        here is the schema's `minimum: 0` (D45). The handler guard which
        holds the same refusal at the `warn`/`off` rollback answers a
        different 400 and is pinned by the unit sweep's
        `disk.size.negative` rows, because a deployed cluster's
        validation mode is not this suite's to change.
        """
        try:
            # raw-create: as above, the refusal is the assertion.
            self.test_client.create_instance(
                'diskspec-negative-size', 1, 1024, None,
                [
                    {
                        'size': -5
                    }
                ], None, None)
            self.fail('A negative disk size was not refused')
        except apiclient.RequestMalformedException as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)

            body = json.loads(e.text)
            self.assertEqual(
                {'error': 'disk[0].size: Must be greater than or equal to 0.',
                 'status': 400},
                body)

            for marker in _INTERPRETER_TEXT_MARKERS:
                self.assertNotIn(
                    marker, e.text,
                    'Validation refusal leaked interpreter text: %s' % e.text)


class TestDNSValueRefused(base.BaseNamespacedTestCase):
    """Issue 4248, at the real API.

    The DNS `value` on `POST /networks/{ref}/dns` is stored on the
    network's hosteddns attribute and rendered raw as
    `{{value}} {{name}}` into the addn-hosts file that network's
    dnsmasq serves, through a jinja2.Template with autoescape off. A
    value carrying a newline therefore injects extra host entries into
    the caller's own network's DNS.

    This suite runs at the `enforce` default, so the refusal pinned
    here is the `ipv4` declaration's format validator. The handler
    guard which holds the same refusal at the `warn`/`off` rollback
    answers 406 and is pinned in the unit suite
    (test_network.py's NetworkDNSAddressValueGuardTestCase), because a
    deployed cluster's validation mode is not this suite's to change.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivaliddns'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.244.0/24', True, True, '%s-net' % self.namespace,
            provide_dns=True)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_injection_value_refused(self):
        try:
            self.test_client.update_network_dns_entry(
                self.net['uuid'], 'probe',
                '10.0.0.1 innocent\n10.0.0.2 victim.example.com')
            self.fail('A DNS value carrying a newline was not refused')
        except apiclient.RequestMalformedException as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)

            body = json.loads(e.text)
            self.assertEqual(
                {'error': 'value: Not a valid IP address.', 'status': 400},
                body)

            for marker in _INTERPRETER_TEXT_MARKERS:
                self.assertNotIn(
                    marker, e.text,
                    'Validation refusal leaked interpreter text: %s' % e.text)

        # The control, which is also the width assertion: a well formed
        # address on the same network still round trips, so neither the
        # declaration nor the handler guard is narrower than the
        # handler was.
        self.test_client.update_network_dns_entry(
            self.net['uuid'], 'probe', '192.168.244.5')
        self.test_client.delete_network_dns_entry(self.net['uuid'], 'probe')


class TestNullNetworkUuidRefusedOnHotplug(base.BaseNamespacedTestCase):
    """PLAN-api-input-validation-phase-07-structured.md, finding F7.

    The sharpest of the phase's contract changes, and issue #4223's
    symptom. `_netdesc_safety_checks` checked that `network_uuid` was
    *present*, not that it had a value, and
    `Network.from_db_by_ref(None, namespace)` reads a `None` name as
    "no name filter" rather than as a name of `None` -- so the query
    returned every active network in the namespace and one of them was
    returned as though the caller had named it. End to end, a hotplug
    of `{"network_uuid": null}` answered **200 and created an interface
    on an arbitrary network**.

    D44 makes `required` inside an object fragment mean present *and
    not null*, the same rule phase 6 gave it at the top level, which
    closes the only path a caller can reach that lookup by. #4223 stays
    open for the lookup itself.

    Two things make this test what it is:

    * The namespace holds exactly one network. With two, the old code
      raised MultipleObjects and answered a confusing 400, which would
      make this test pass without the fix. With one, the pre-fix answer
      is a 200 and an interface, which is what has to be shown not to
      happen.
    * The instance's interfaces are asserted as well as the status.
      "Refuses and creates it anyway" is the exact shape the original
      bug had, and a status code cannot see it.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidnull'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.246.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_null_network_uuid_creates_no_interface(self):
        # A minimal disk: an empty 1GB disk with no base image to fetch.
        # The instance never has to boot for this test -- the hotplug
        # endpoint refuses in the validation layer, and all the instance
        # has to be is a real, non-terminal instance whose interfaces
        # can be counted.
        minimal_disk = [{'size': 1, 'type': 'disk'}]
        inst = self.create_instance(
            'hotplug-null-uuid', 1, 128,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ], minimal_disk, None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._await_instance_create(inst['uuid'])

        before = self.test_client.get_instance_interfaces(inst['uuid'])
        self.addDetail(
            'before',
            content.text_content(json.dumps(before, indent=4, sort_keys=True)))
        self.assertEqual(1, len(before))

        try:
            self.test_client.add_instance_interface(
                inst['uuid'], {'network_uuid': None})
            self.fail('A null network_uuid was not refused')
        except apiclient.RequestMalformedException as e:
            self.addDetail('response', content.text_content(str(e.text)))
            self.assertEqual(400, e.status_code)

            body = json.loads(e.text)
            self.assertEqual(
                {'error': ('network.network_uuid: Missing data for required '
                           'field.'),
                 'status': 400},
                body)

            for marker in _INTERPRETER_TEXT_MARKERS:
                self.assertNotIn(
                    marker, e.text,
                    'Validation refusal leaked interpreter text: %s' % e.text)

        # The half a status code cannot prove. The interfaces are
        # compared by uuid rather than whole: an interface's version and
        # metadata are free to move underneath this test, and what F7 is
        # about is whether a new object exists.
        after = self.test_client.get_instance_interfaces(inst['uuid'])
        self.addDetail(
            'after',
            content.text_content(json.dumps(after, indent=4, sort_keys=True)))
        self.assertEqual(
            sorted(i['uuid'] for i in before),
            sorted(i['uuid'] for i in after),
            'The refused hotplug changed the instance\'s interfaces, which '
            'is finding F7 exactly: the request was refused and the object '
            'was created anyway')


class TestEveryDocumentedSpecKeyStillBoots(base.BaseNamespacedTestCase):
    """PLAN-api-input-validation-phase-07-structured.md, step 7.

    The other half of the phase, and the more important one. Every
    narrowing phase 7 added -- five new type tokens, three enums, two
    deliberate narrowings and `additionalProperties: false` on all three
    specs -- is a chance to have refused a request some caller legitimately
    sends, and a suite which only tested refusals would pass just as well
    against a server which refused everything.

    So this sends one instance carrying *every* documented key of all
    three specs, with the values a real caller sends, and requires it to
    create, boot and answer an agent command. The API reference at
    docs/developer_guide/api_reference/instances.md is the list: four
    diskspec keys, five networkspec keys and three videospec keys.

    Two values are chosen rather than obvious:

    * `float` is False. It is the value the Ansible collection sends on
      every network it builds, so it is a real caller's value, and a
      truthy float would make this test depend on the deployment's
      floating network having a spare address -- a property of the
      cluster rather than of the schema under test.
    * `macaddress` is pinned. The UNIQUE constraint on interface MAC
      addresses is scoped to (macaddr, active, network_uuid), and the
      network here is created by this test in its own namespace, so a
      fixed address cannot collide with a sibling test.

    Pinning a MAC and an address does carry one known hazard, which is
    the same one test_networking.py's overlapping-network tests already
    accept for a pinned address. Interfaces are allocated before the
    scheduler runs (external_api/instance.py), so a create refused with
    a 507 leaves its interface holding both values until the error
    delete completes -- and the capacity-waiting wrapper retries under a
    new name with the same netdesc. A retry which overtakes that delete
    answers 409 for the address or, because a caller-supplied MAC is
    attempted exactly once by design (network/interface.py), 500 for the
    MAC. Neither is this contract failing. If it is ever seen, the fix
    is to derive both values per attempt, not to stop asserting them.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidwide'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.247.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_every_documented_key_still_creates_and_boots(self):
        address = '192.168.247.10'
        macaddress = '02:00:00:7c:0f:e7'

        inst = self.create_instance(
            'every-documented-key', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid'],
                    'address': address,
                    'macaddress': macaddress,
                    'model': 'virtio',
                    'float': False
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'bus': 'virtio',
                    'type': 'disk'
                }
            ], None, None,
            video={
                'model': 'cirrus',
                'memory': 16384,
                'vdi': 'spice'
            })
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        # The networkspec keys, as the server understood them. Asserting
        # the values rather than only that the instance booted: a schema
        # which quietly dropped a key it now knows how to describe would
        # still boot an instance, just not the one that was asked for.
        nics = self.test_client.get_instance_interfaces(inst['uuid'])
        self.addDetail(
            'nics',
            content.text_content(json.dumps(nics, indent=4, sort_keys=True)))
        self.assertEqual(1, len(nics))
        self.assertEqual(address, nics[0]['ipv4'])
        self.assertEqual(macaddress, nics[0]['macaddr'])
        self.assertEqual('virtio', nics[0]['model'])
        self.assertIsNone(nics[0]['floating'])

        # The videospec, round-tripped. `vdi` is the one key in this
        # vocabulary where publishing the enum is the enforcement, so a
        # value inside it has to survive.
        fetched = self.test_client.get_instance(inst['uuid'])
        self.addDetail(
            'fetched',
            content.text_content(json.dumps(fetched, indent=4, sort_keys=True)))
        self.assertEqual('cirrus', fetched['video']['model'])
        self.assertEqual(16384, fetched['video']['memory'])
        self.assertEqual('spice', fetched['video']['vdi'])

        # And the diskspec, as the guest sees it: a virtio disk is vda.
        results = self._await_command(inst['uuid'], 'df -h')
        self.addDetail(
            'results',
            content.text_content(json.dumps(results, indent=4, sort_keys=True)))
        self.assertEqual(0, results['return-code'])
        self.assertIn('vda', results['stdout'])


class TestOversizedBodyRefused(base.BaseTestCase):
    """Issue 4249: the general request body cap.

    The validation layer walks everything the body parser produces, so
    a large malformed body used to buy seconds of single-threaded work
    per gunicorn worker. The limit_request_body_size hook refuses an
    oversized body before anything reads it, on every route.

    These requests use ``requests`` directly rather than the client:
    the cap runs before authentication, so no credentials are carried,
    and no public client method sends a body this large. The size is
    the server-side default of API_MAX_REQUEST_BODY_BYTES, hardcoded
    because the CI cluster deploys the default configuration and this
    suite cannot read the server's config.
    """

    def test_an_oversized_body_is_refused(self):
        r = requests.post(
            f'{self.system_client.base_url}/instances',
            data='x' * (1048576 + 1))
        self.addDetail('response', content.text_content(r.text))
        self.assertEqual(413, r.status_code)

    def test_a_body_under_the_cap_reaches_authentication(self):
        # The control: the same unauthenticated request with a small
        # body is answered by the JWT layer, which proves the 413 above
        # is the size hook and not any later refusal.
        r = requests.post(
            f'{self.system_client.base_url}/instances', json={})
        self.addDetail('response', content.text_content(r.text))
        self.assertEqual(401, r.status_code)


class TestStringSpelledBooleanBootsBIOS(base.BaseNamespacedTestCase):
    """Issue 4253's companion case, at the real API.

    `uefi` is published as a boolean, and the published vocabulary
    includes string spellings: marshmallow's Boolean reads 'false' as
    False, so `{"uefi": "false"}` is a request the specification calls
    a valid ask for BIOS boot. A shell-script caller genuinely sends
    it -- a flag variable interpolated into a curl body is exactly
    this shape. The handler now decodes it through
    validation.declared_boolean(), the same single reading the
    networkspec's `float` already had; before that fix the spelling
    survived to storage only because the persistence model's lax
    pydantic field happened to coerce the same set, while the
    handler's own secure-boot guard read it truthily and got it
    wrong.

    The unit half (test_instance_create_validation.py's
    InstanceCreateBooleanSpellingTestCase) asserts on the exact value
    handed to Instance.new(); this half pins the contract against a
    deployed cluster, storing and reporting the decoded value. The
    stored value is what the domain XML's firmware choice is keyed on
    (`if self.uefi:` in instance.py), so asserting it *is* asserting
    which firmware a boot would use, one hop earlier and without
    spending a boot on it.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'apivalidbool'
        super().__init__(*args, **kwargs)

    def test_uefi_string_false_means_bios(self):
        # The public client sends real JSON booleans, which is why the
        # string spelling is passed through create_instance()'s kwargs
        # verbatim: the client body-builds `'uefi': uefi` untouched, so
        # this is the raw `{"uefi": "false"}` request a hand-rolled
        # caller sends, still with the wrapper's capacity waiting.
        #
        # A minimal empty disk, no network: the instance never has to
        # boot for this test, it just has to exist so its stored uefi
        # value can be read back.
        minimal_disk = [{'size': 1, 'type': 'disk'}]
        inst = self.create_instance(
            'uefi-string-false', 1, 128, None, minimal_disk, None, None,
            uefi='false')
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._await_instance_create(inst['uuid'])

        fetched = self.test_client.get_instance(inst['uuid'])
        self.addDetail(
            'fetched',
            content.text_content(json.dumps(fetched, indent=4, sort_keys=True)))
        # assertIs rather than assertFalse: the pre-fix failure mode is
        # the string 'false' stored verbatim, and a truthiness-blind
        # assertion is exactly the mistake under test.
        self.assertIs(False, fetched['uefi'])
