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


class _Exited(SystemExit):
    ...


class CheckModeModule(FakeModule):
    """FakeModule with the attributes run_module() reaches for.

    check_mode is set because the check mode paths are the ones a rule
    living in _make_client() never reached.
    """

    check_mode = True

    def __init__(self, **params):
        super().__init__(**params)
        self.exited = None

    def exit_json(self, **kwargs):
        self.exited = kwargs
        raise _Exited()


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

# A check mode task per module that reports changed -- or at least exits
# cleanly -- once the connection is whole. Each was confirmed to reach
# exit_json() with the connection check stubbed out, which is what makes
# test_check_mode_refuses_a_partial_connection an assertion about where the
# rule is checked rather than about whether run_module() happens to raise.
CHECK_MODE_TASK = {
    'sf_claim': {'state': 'absent', 'namespace': 'a-namespace',
                 'renew_within_seconds': None},
    'sf_namespace': {'state': 'present', 'name': 'a-namespace'},
    'sf_snapshot': {'state': 'present', 'instance_uuid': 'an-instance'},
    'sf_instance': {'state': 'absent', 'uuid': 'an-instance'},
    'sf_network': {'state': 'absent', 'uuid': 'a-network'},
}

# A task each module rejects on its own account, and the fragment of the
# message it uses. The connection rule is checked before any of these, so a
# task that is wrong in both ways is told which cluster it would have talked
# to rather than shown the typo. sf_namespace has no row because it has no
# such check: everything it needs is required in the argument spec, so its
# run_module() reaches _make_client() with nothing in between. The call it
# makes before that is there so the next branch added above it cannot
# reopen the hole, which is not a difference any test can see today.
OWN_ARGUMENT_CHECK = {
    'sf_claim': ({'state': 'present', 'expires_in_seconds': 0,
                  'renew_within_seconds': None, 'namespace': 'a-namespace'},
                 'expires_in_seconds must be positive'),
    'sf_snapshot': ({'state': 'present'},
                    'You must specify an instance_uuid'),
    'sf_instance': ({'state': 'absent'},
                    'You must specify one of name or uuid'),
    'sf_network': ({'state': 'absent'},
                   'You must specify one of name or uuid'),
}

# The collection README is published to Ansible Galaxy and is the first
# thing a collection user reads about authentication. It is held to
# delegating the table rather than restating it, because a third copy is a
# third thing to keep in step -- and it went stale once already.
COLLECTION_README = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'README.md'))

API_URL = 'https://api.example.com'
KEY = 'a-key'
IDENTITY = 'a-namespace'

DISCOVERY_KWARGS = ('base_url', 'namespace', 'key',
                    'suppress_configuration_lookup')


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

    def _run(self, name, task=None, **params):
        """Run run_module() in check mode, returning (module, client_mock)."""
        mod = self.modules[name]
        if task is None:
            task = CHECK_MODE_TASK[name]
        fake = CheckModeModule(**dict(task, **params))
        with mock.patch.object(mod, 'AnsibleModule', return_value=fake), \
                mock.patch.object(mod.apiclient, 'Client') as client:
            try:
                mod.run_module()
            except (_Failed, _Exited):
                pass
        return fake, client

    def test_check_mode_refuses_a_partial_connection(self):
        # The rule used to live only in _make_client(), which runs on a
        # path that wants a client -- and sf_snapshot returns for check
        # mode before wanting one. A play naming a cluster half way was
        # therefore told --check would have succeeded, and then failed on
        # the real run against whatever credentials the control node held.
        #
        # Every module is held to this, not just the one with the early
        # return today, because the hole is in where the rule is checked
        # rather than in any one module: the next early return added above
        # a _make_client() call would open it again. CHECK_MODE_TASK is
        # what makes the assertion bite -- each is a task that reports
        # changed when the connection check is removed.
        for name, identity, _optional in MODULES:
            for supplied in (('api_url', ), ('key', ),
                             ('api_url', 'key'), ('api_url', identity)):
                params = {n: {'api_url': API_URL, 'key': KEY}.get(
                    n, IDENTITY) for n in supplied}
                case = (name, supplied)

                fake, client = self._run(name, **params)
                self.assertIsNotNone(fake.failure, case)
                self.assertIsNone(fake.exited, case)
                client.assert_not_called()

    def test_check_mode_still_runs_when_the_connection_is_whole(self):
        # Otherwise the test above would pass just as well if the modules
        # refused every check mode task, which is not the fix.
        for name, identity, _optional in MODULES:
            for params in ({}, {'api_url': API_URL, 'key': KEY,
                                identity: IDENTITY}):
                case = (name, sorted(params))
                fake, _client = self._run(name, **params)
                self.assertIsNone(fake.failure, case)
                self.assertIsNotNone(fake.exited, case)

    def test_the_connection_is_checked_before_the_task_itself(self):
        # Checking it first is the whole of the fix: a rule enforced only
        # where a client is built is not enforced on the paths that return
        # before wanting one. Asserting the order, rather than that a check
        # exists, is what distinguishes the two.
        for name, (task, message) in OWN_ARGUMENT_CHECK.items():
            fake, _client = self._run(name, task=task)
            self.assertIsNotNone(fake.failure, name)
            self.assertIn(message, fake.failure['msg'], name)

            fake, client = self._run(name, task=task, api_url=API_URL)
            self.assertIsNotNone(fake.failure, name)
            self.assertNotIn(message, fake.failure['msg'], name)
            self.assertIn('discarded in favour of', fake.failure['msg'], name)
            client.assert_not_called()

    def test_an_empty_string_counts_as_not_supplied(self):
        # docs/user_guide/ansible.md tells operators that a templated
        # api_url: "{{ sf_url | default('') }}" against unset inventory
        # auto-discovers. That is only true while "supplied" means a
        # truthy value. Declaring the rule to Ansible as required_together
        # or required_by instead would break exactly this, because those
        # count key presence and never look at the value: the empty string
        # is present, so the whole templated pattern would be refused,
        # while a full set with one member empty would be accepted and
        # caught only later, or -- in check mode -- not at all.
        for name, identity, _optional in MODULES:
            fake, client = self._call(
                name, api_url='', key='', **{identity: ''})
            self.assertIsNone(fake.failure, name)
            for unwanted in DISCOVERY_KWARGS:
                self.assertNotIn(unwanted, client.call_args[1], name)

            for empty in ('api_url', identity, 'key'):
                params = {'api_url': API_URL, 'key': KEY, identity: IDENTITY}
                params[empty] = ''
                case = (name, empty)

                fake, client = self._call(name, **params)
                self.assertIsNotNone(fake.failure, case)
                client.assert_not_called()

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

    def test_the_collection_readme_does_not_restate_the_table(self):
        # It said 'sf_claim is the exception' until this rule reached the
        # other four modules, at which point it was simply wrong. Rather
        # than add a third copy of the table to keep in step, the README
        # states the general rule and points at the user guide -- and this
        # holds it to that.
        with open(COLLECTION_README) as f:
            readme = f.read()

        self.assertIn('user_guide/ansible', readme,
                      'the collection README should point at the user '
                      'guide for the per-module table rather than '
                      'restating it')

        with open(ANSIBLE_DOC) as f:
            header = [line for line in f
                      if line.startswith('| Module | Identity parameter')]
        self.assertEqual(1, len(header),
                         'the identity table in docs/user_guide/ansible.md '
                         'has moved or changed shape; this test and '
                         'test_the_documented_table_says_the_same_thing '
                         'both parse it')
        for cell in header[0].strip('|\n').split('|'):
            cell = cell.strip()
            if not cell.startswith('Identity'):
                # 'Module' is too common a word to look for.
                continue
            self.assertNotIn(
                cell, readme,
                'the collection README appears to be growing its own copy '
                'of the identity table from docs/user_guide/ansible.md; '
                'keep one copy and link to it')

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
