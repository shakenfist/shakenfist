# Copyright 2026 Michael Still and contributors
import itertools
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

API_URL = 'https://api.example.com'
KEY = 'a-key'
IDENTITY = 'a-namespace'

DISCOVERY_KWARGS = ('base_url', 'namespace', 'key',
                    'suppress_configuration_lookup')


class MakeClientConnectionRuleTestCase(base.ShakenFistTestCase):
    """Every collection module's _make_client(), against one rule table.

    These modules are copy-pasted from each other and have already started
    to diverge, so the guard is asserted here once per module rather than in
    the two per-module test files, which cover three of the five between
    them.
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
