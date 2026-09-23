# Copyright 2026 Michael Still and contributors
import itertools
import os
from unittest import mock

from shakenfist.tests import ansible_module_loader
from shakenfist.tests import base


class _Failed(SystemExit):
    ...


class FakeModule(object):
    """The part of AnsibleModule that _make_client() touches.

    fail_json() raises rather than returning, because the real one calls
    sys.exit() and the code after each fail_json() call in the modules is
    written on that basis.
    """

    def __init__(self, **params):
        self.params = params
        self.failure = None

    def fail_json(self, **kwargs):
        self.failure = kwargs
        raise _Failed()


class ForgivingModule(FakeModule):
    """A module whose fail_json() records and returns, as a stub's might.

    Used to assert that the code after a guard is safe on its own rather
    than relying on the caller never coming back.
    """

    def fail_json(self, **kwargs):
        self.failure = kwargs


# The connection rule differs between modules, and the difference is
# deliberate. Where the identity parameter is also the object to operate on
# it is legitimate on its own -- sf_instance and sf_network pass namespace
# to get_instance() and get_network(), and the deployment playbooks call
# them that way -- so only api_url and key drag in the full set. Where it is
# an identity and nothing else, supplying it alone says only "authenticate
# as this", which is exactly the instruction discovery would discard, so all
# three are held together.
#
# identity_optional is therefore the whole of the difference, and this table
# is the enforced statement of which module is which. A new module added to
# the collection belongs here, and in the table in
# docs/user_guide/ansible.md, which says the same thing for operators.
MODULES = [
    ('sf_claim', 'auth_namespace', False),
    ('sf_namespace', 'namespace', False),
    ('sf_snapshot', 'namespace', False),
    ('sf_instance', 'namespace', True),
    ('sf_network', 'namespace', True),
]

# The operator-facing statement of the same table, which the tests below
# hold to this one.
ANSIBLE_DOC = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'docs', 'user_guide',
    'ansible.md'))

API_URL = 'https://api.example.com'
KEY = 'a-key'
IDENTITY = 'a-namespace'

DISCOVERY_KWARGS = ('base_url', 'namespace', 'key',
                    'suppress_configuration_lookup')


def _spec_rejects(declared, supplied):
    """Would Ansible refuse this parameter set, given the declaration?

    required_together refuses a group that is partly present;
    required_by refuses a parameter present without everything it names.
    Both are reimplemented here rather than called, because ansible is not
    a test dependency of this repository -- ansible_module_loader stubs it
    out to load the modules at all. The two behaviours were checked
    against ansible 2.19.11's ArgumentSpecValidator before being written
    down.
    """
    supplied = set(supplied)
    for group in declared.get('required_together') or []:
        present = supplied & set(group)
        if present and present != set(group):
            return True
    for param, needs in (declared.get('required_by') or {}).items():
        if param in supplied and not set(needs) <= supplied:
            return True
    return False


class MakeClientConnectionRuleTestCase(base.ShakenFistTestCase):
    """Every collection module's _make_client(), against one rule table.

    These modules are copy-pasted from each other and have already started
    to diverge -- issue 4314 tracks folding _make_client() into the
    collection's module_utils -- so the rule is asserted here once per
    module, against one table, rather than in the per-module test files.
    """

    def setUp(self):
        super().setUp()
        self.modules = {
            name: ansible_module_loader.load_collection_module(name)
            for name, _identity, _optional in MODULES}

    def _call(self, name, **params):
        """Run _make_client(), returning (module, client_mock)."""
        mod = self.modules[name]
        fake = FakeModule(**params)
        with mock.patch.object(mod.apiclient, 'Client') as client:
            try:
                mod._make_client(fake)
            except _Failed:
                pass
        return fake, client

    def _declared(self, name):
        """The connection rule as run_module() declares it to Ansible."""
        mod = self.modules[name]
        captured = {}

        def _capture(**kwargs):
            captured.update(kwargs)
            raise _Failed()

        with mock.patch.object(mod, 'AnsibleModule', side_effect=_capture):
            self.assertRaises(_Failed, mod.run_module)

        return captured

    def test_the_spec_and_the_guard_refuse_the_same_sets(self):
        # _make_client()'s guard only runs when a client is wanted, and
        # sf_snapshot returns for check mode before wanting one -- so a
        # partial set there used to pass --check and fail the real run.
        # Declaring the rule on the argument spec closes that, because
        # Ansible checks it while AnsibleModule is built. Asserting the two
        # agree, rather than that a declaration merely exists, is what
        # keeps them from drifting apart into two different rules.
        for name, identity, _optional in MODULES:
            declared = self._declared(name)
            names = ('api_url', identity, 'key')
            for count in range(len(names) + 1):
                for subset in itertools.combinations(names, count):
                    params = {n: {'api_url': API_URL, 'key': KEY}.get(
                        n, IDENTITY) for n in subset}
                    fake, _client = self._call(name, **params)
                    case = (name, subset)

                    self.assertEqual(_spec_rejects(declared, subset),
                                     fake.failure is not None, case)

    def test_a_refused_set_never_reaches_the_client(self):
        # The kwargs branch has to stand on its own rather than on
        # fail_json() exiting. The real AnsibleModule.fail_json() calls
        # sys.exit(), so in production nothing runs after the guard -- but
        # that is a property of the caller, not of this code. A stub, a
        # shim, or a future refactor whose fail_json() returns would
        # otherwise fall through and build a client from half a connection,
        # which is worse than the discovery it replaced: it fails deep
        # inside the API client instead of doing something.
        for name, identity, _optional in MODULES:
            mod = self.modules[name]
            for supplied in (('api_url', ), ('key', ),
                             ('api_url', 'key'), ('api_url', identity)):
                params = {n: {'api_url': API_URL, 'key': KEY}.get(
                    n, IDENTITY) for n in supplied}
                fake = ForgivingModule(**params)
                case = (name, supplied)

                with mock.patch.object(mod.apiclient, 'Client') as client:
                    mod._make_client(fake)

                self.assertIsNotNone(fake.failure, case)
                for unwanted in DISCOVERY_KWARGS:
                    self.assertNotIn(unwanted, client.call_args[1], case)

    def test_the_table_covers_every_collection_module(self):
        # The comment above MODULES says a new module belongs here. Until
        # this test, nothing checked it: a sixth module could land with no
        # coverage of its connection rule and every check still green,
        # because the collection's own ansible module CI is merge tier and
        # does not run on a pull request.
        on_disk = {f[:-len('.py')]
                   for f in os.listdir(ansible_module_loader.MODULE_DIR)
                   if f.endswith('.py') and not f.startswith('_')}

        self.assertEqual(
            on_disk, {name for name, _identity, _optional in MODULES},
            'plugins/modules/ and the MODULES table above disagree. A new '
            'collection module needs a row in that table, and a row in the '
            'table in docs/user_guide/ansible.md.')

    def test_the_documented_table_says_the_same_thing(self):
        # Operators read docs/user_guide/ansible.md, not this file. The two
        # tables are the same statement written twice, so hold them
        # together rather than trusting that both get updated.
        documented = {}
        with open(ANSIBLE_DOC) as f:
            for line in f:
                cells = [c.strip().strip('`')
                         for c in line.strip().split('|')[1:-1]]
                if len(cells) == 3 and cells[0].startswith('sf_'):
                    documented[cells[0]] = (cells[1], cells[2])

        expected = {name: (identity, 'Allowed' if optional else 'An error')
                    for name, identity, optional in MODULES}

        self.assertEqual(expected, documented,
                         'the table in docs/user_guide/ansible.md and the '
                         'MODULES table above disagree')

    def test_nothing_supplied_auto_discovers(self):
        for name, _identity, _optional in MODULES:
            fake, client = self._call(name)
            self.assertIsNone(fake.failure, name)
            kwargs = client.call_args[1]
            for unwanted in DISCOVERY_KWARGS:
                self.assertNotIn(unwanted, kwargs, name)

    def test_a_full_set_is_used_verbatim(self):
        for name, identity, _optional in MODULES:
            fake, client = self._call(
                name, api_url=API_URL, key=KEY, **{identity: IDENTITY})
            self.assertIsNone(fake.failure, name)
            kwargs = client.call_args[1]
            self.assertEqual(API_URL, kwargs['base_url'], name)
            self.assertEqual(IDENTITY, kwargs['namespace'], name)
            self.assertEqual(KEY, kwargs['key'], name)
            self.assertTrue(kwargs['suppress_configuration_lookup'], name)

    def test_the_identity_alone_follows_the_table(self):
        for name, identity, optional in MODULES:
            fake, client = self._call(name, **{identity: IDENTITY})
            if optional:
                # It names the object to operate on as well, so it stands
                # alone and the credentials are discovered. This is what the
                # deployment playbooks do, and holding it to the all or
                # nothing rule broke the collection smoke test outright.
                self.assertIsNone(fake.failure, name)
                kwargs = client.call_args[1]
                for unwanted in DISCOVERY_KWARGS:
                    self.assertNotIn(unwanted, kwargs, name)
            else:
                self.assertIsNotNone(fake.failure, name)
                client.assert_not_called()

    def test_every_other_partial_set_fails(self):
        for name, identity, _optional in MODULES:
            names = ('api_url', identity, 'key')
            for count in (1, 2):
                for subset in itertools.combinations(names, count):
                    if subset == (identity, ):
                        # Covered by the table above, which is where the
                        # two rules differ.
                        continue
                    params = {n: {'api_url': API_URL, 'key': KEY}.get(
                        n, IDENTITY) for n in subset}
                    case = (name, subset)

                    fake, client = self._call(name, **params)
                    self.assertIsNotNone(fake.failure, case)
                    client.assert_not_called()

                    # Assert on the variable part of the message only.
                    # Every parameter name appears in the fixed prefix, so
                    # an assertion over the whole message passes even if
                    # the list were built from the missing parameters
                    # rather than the supplied ones.
                    detail = fake.failure['msg'].split('Got only ', 1)[1]
                    for n in names:
                        if n in subset:
                            self.assertIn(n, detail, case)
                        else:
                            self.assertNotIn(n, detail, case)
