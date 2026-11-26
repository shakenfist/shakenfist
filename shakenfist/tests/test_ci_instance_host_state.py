# Copyright 2026 Michael Still and contributors
"""Unit tests for the hypervisor host state helpers in the CI suite.

``shakenfist_ci/instance_state.py``'s
``test_delete_removes_the_instance_from_the_hypervisor`` asserts that a
deleted instance leaves no directory, nvram file or libvirt domain on the
hypervisor it was pinned to. It needs a deployed cluster, and the cluster
suite runs for the first time in the merge queue, so what can be checked
without one is checked here: the readings the test is built out of, and
the polling which turns them into a pass or a failure.

The readings are ``BaseTestCase._node_path_present()``,
``_domain_nvram_path()``, ``_domain_is_defined()`` and
``_require_node_sudo()`` in ``shakenfist_ci/base.py``. Each of them turns
a node's output into a boolean or a path, and each has a failure mode
which would read as "the state is gone" if it were wrong -- which is the
answer a teardown assertion most wants to be wrong about, because it
passes.

``shakenfist_ci/base.py`` is loaded by path, with ``shakenfist_client``
and ``prettytable`` stubbed out, because neither is a test dependency of
this repository. This is the same loader ``test_ci_power_state.py``,
``test_ci_capacity_wait.py`` and ``test_ci_assert_refused_at_stage.py``
each carry their own copy of; another copy is written here rather than
imported from one of them, following their precedent of not sharing it
across test modules. ``instance_state.py`` itself imports nothing but the
standard library and testtools, so it loads by path on its own.
"""

import importlib.util
import logging
import os
import sys
import types
from unittest import mock

import fixtures

from shakenfist.tests import base as test_base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')


def _client_stubs():
    """A shakenfist_client stub with just the exception taxonomy.

    ``base.py`` references several of these names at module level, so the
    stub must define them even though this test never raises one.
    """
    client = types.ModuleType('shakenfist_client')
    apiclient = types.ModuleType('shakenfist_client.apiclient')

    class APIException(Exception):
        def __init__(self, message='', method=None, url=None,
                     status_code=None, text=None):
            super().__init__(message)
            self.status_code = status_code
            self.text = text

    apiclient.APIException = APIException
    for name in ('InsufficientResourcesException',
                 'ResourceStateConflictException',
                 'ResourceNotFoundException',
                 'RequestMalformedException',
                 'ServiceUnavailableException',
                 'IncapableException',
                 'AgentCommandError',
                 'AgentOperationFailed',
                 'AgentAwaitTimeout'):
        setattr(apiclient, name, type(name, (APIException,), {}))

    apiclient.ASYNC_PAUSE = 'pause'
    apiclient.ASYNC_CONTINUE = 'continue'
    apiclient.Client = type(
        'Client', (), {'__init__': lambda self, *a, **kw: None})

    client.apiclient = apiclient
    return client, apiclient


def _load_ci_base():
    """Import the functional suite's base.py without its dependencies.

    Everything mutated here is put back, because stestr runs the whole
    unit suite in one process and a stubbed shakenfist_client left in
    sys.modules would be a trap for a later test rather than a
    convenience for this one.
    """
    stub_names = ('prettytable', 'shakenfist_client',
                  'shakenfist_client.apiclient', 'shakenfist_ci',
                  'shakenfist_ci.base', 'shakenfist_ci.process',
                  'shakenfist_ci.retries')
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in stub_names}
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)
    saved_level = root_logger.level

    try:
        sys.path.insert(0, os.path.dirname(CI_SUITE))
        for name in stub_names:
            sys.modules.pop(name, None)

        prettytable = types.ModuleType('prettytable')
        prettytable.PrettyTable = type('PrettyTable', (), {})
        sys.modules['prettytable'] = prettytable

        client, apiclient = _client_stubs()
        sys.modules['shakenfist_client'] = client
        sys.modules['shakenfist_client.apiclient'] = apiclient

        import shakenfist_ci.base as ci_base
        import shakenfist_ci.process as ci_process
        return ci_base, ci_process

    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


def _load_instance_state():
    """The mixin module, loaded by path like sizing.py is elsewhere."""
    path = os.path.join(CI_SUITE, 'instance_state.py')
    spec = importlib.util.spec_from_file_location(
        'shakenfist_ci_instance_state_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_base, ci_process = _load_ci_base()
instance_state = _load_instance_state()


NODE = {'name': 'sf2', 'ip': '192.168.1.2'}
UUID = '1234abcd-0000-0000-0000-000000000000'
INSTANCE_DIRECTORY = '/srv/shakenfist/instances/%s' % UUID
NVRAM_PATH = '%s/nvram' % INSTANCE_DIRECTORY

# A UEFI domain as libvirt.tmpl renders it, trimmed to the elements these
# helpers read. The nvram carries a template attribute because a
# non-secure-boot UEFI instance which brings no nvram_template blob of
# its own gets the installed OVMF VARS image as one.
UEFI_DOMAIN_XML = """<domain type='kvm'>
  <name>sf:%s</name>
  <uuid>%s</uuid>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <loader readonly='yes' type='pflash'>/usr/share/OVMF/OVMF_CODE_4M.fd</loader>
    <nvram template='/usr/share/OVMF/OVMF_VARS_4M.fd'>%s</nvram>
  </os>
</domain>
""" % (UUID, UUID, NVRAM_PATH)

# The same domain without UEFI, which is what a BIOS instance looks like.
BIOS_DOMAIN_XML = """<domain type='kvm'>
  <name>sf:%s</name>
  <uuid>%s</uuid>
  <os>
    <type arch='x86_64' machine='pc'>hvm</type>
  </os>
</domain>
""" % (UUID, UUID)


class _NodeExecHarness(ci_base.BaseTestCase):
    """A bound ``self`` for the node reading helpers, without setUp().

    setUp() builds a real apiclient and writes a tracing event to
    /srv/ci/traces, neither of which a unit test should do, so this
    supplies only what the helpers read -- ``_node_exec``, replaced as an
    instance attribute shadowing the inherited method.
    """

    def __init__(self, node_exec):
        # Deliberately not calling TestCase.__init__: this is not being
        # run as a test, only used as a bound self for the helpers.
        self._node_exec = node_exec


class NodePathPresentTestCase(test_base.ShakenFistTestCase):
    """``_node_path_present()`` turns a shell file test into a boolean.

    Both the dnsmasq directory assertion and the instance teardown
    assertion read absence as success, so an unreadable answer must fail
    rather than be believed.
    """

    def setUp(self):
        super().setUp()
        self.node_exec = mock.MagicMock()
        self.harness = _NodeExecHarness(self.node_exec)

    def test_present_is_true(self):
        self.node_exec.return_value = ('present\n', '')

        self.assertTrue(
            self.harness._node_path_present(NODE, INSTANCE_DIRECTORY))
        self.node_exec.assert_called_once_with(
            NODE,
            ['sh', '-c',
             'if [ -e %s ]; then echo present; else echo absent; fi'
             % INSTANCE_DIRECTORY],
            sudo=True)

    def test_absent_is_false(self):
        self.node_exec.return_value = ('absent\n', '')

        self.assertFalse(
            self.harness._node_path_present(NODE, INSTANCE_DIRECTORY))

    def test_the_file_test_is_the_callers(self):
        # dhcp_state.py asks about a directory, so it passes -d.
        self.node_exec.return_value = ('present\n', '')

        self.harness._node_path_present(NODE, '/srv/shakenfist/dhcp/net',
                                        test='-d')

        self.node_exec.assert_called_once_with(
            NODE,
            ['sh', '-c',
             'if [ -d /srv/shakenfist/dhcp/net ]; then echo present; '
             'else echo absent; fi'],
            sudo=True)

    def test_the_path_is_quoted_into_the_shell(self):
        self.node_exec.return_value = ('absent\n', '')

        self.harness._node_path_present(NODE, '/srv/odd path/nvram')

        args = self.node_exec.call_args[0][1]
        self.assertIn("'/srv/odd path/nvram'", args[2])

    def test_an_unreadable_answer_fails(self):
        # The failure mode which matters: a command which could not run
        # at all must not read as an absent path, because absence is what
        # a teardown assertion is looking for.
        self.node_exec.return_value = (
            'sudo: a password is required\n', '')

        exc = self.assertRaises(
            AssertionError, self.harness._node_path_present,
            NODE, INSTANCE_DIRECTORY)

        self.assertIn('Could not tell whether', str(exc))
        self.assertIn(INSTANCE_DIRECTORY, str(exc))
        self.assertIn('password', str(exc))


class DomainNvramPathTestCase(test_base.ShakenFistTestCase):
    """``_domain_nvram_path()`` reads the path libvirt actually holds.

    The template puts the nvram inside the instance's directory, so the
    directory check would normally cover it. This asks the domain instead,
    so the assertion keeps meaning what it says if that ever stops being
    where the file goes.
    """

    def setUp(self):
        super().setUp()
        self.node_exec = mock.MagicMock()
        self.harness = _NodeExecHarness(self.node_exec)

    def test_returns_the_declared_path(self):
        self.node_exec.return_value = (UEFI_DOMAIN_XML, '')

        self.assertEqual(
            NVRAM_PATH, self.harness._domain_nvram_path(NODE, UUID))
        self.node_exec.assert_called_once_with(
            NODE, ['virsh', 'dumpxml', 'sf:%s' % UUID], sudo=True)

    def test_a_relocated_nvram_is_returned_verbatim(self):
        # The whole point of reading the domain: a path outside the
        # instance's directory is residue the directory check cannot see.
        elsewhere = '/var/lib/libvirt/qemu/nvram/sf:%s_VARS.fd' % UUID
        self.node_exec.return_value = (
            UEFI_DOMAIN_XML.replace(NVRAM_PATH, elsewhere), '')

        self.assertEqual(
            elsewhere, self.harness._domain_nvram_path(NODE, UUID))

    def test_a_domain_with_no_nvram_fails(self):
        self.node_exec.return_value = (BIOS_DOMAIN_XML, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_nvram_path, NODE, UUID)

        self.assertIn('declares no', str(exc))
        self.assertIn(UUID, str(exc))

    def test_an_empty_nvram_element_fails(self):
        self.node_exec.return_value = (
            UEFI_DOMAIN_XML.replace(NVRAM_PATH, '  '), '')

        self.assertRaises(
            AssertionError, self.harness._domain_nvram_path, NODE, UUID)

    def test_output_which_is_not_xml_fails(self):
        self.node_exec.return_value = (
            "error: failed to get domain 'sf:%s'\n" % UUID, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_nvram_path, NODE, UUID)

        self.assertIn('is not XML', str(exc))
        self.assertIn('failed to get domain', str(exc))


class DomainIsDefinedTestCase(test_base.ShakenFistTestCase):
    """``_domain_is_defined()`` reads virsh's exit code as a word.

    ``_node_exec()`` returns output rather than a status, so the shell
    turns the exit code into something to parse. An unparseable answer
    fails for the same reason as above.
    """

    def setUp(self):
        super().setUp()
        self.node_exec = mock.MagicMock()
        self.harness = _NodeExecHarness(self.node_exec)

    def test_defined_is_true(self):
        self.node_exec.return_value = ('defined\n', '')

        self.assertTrue(self.harness._domain_is_defined(NODE, UUID))
        # A domain name needs no shell quoting -- a uuid and a colon are
        # not special -- so shlex.quote() leaves it bare here. The
        # quoting is still what makes the command safe for a name which
        # would need it.
        self.node_exec.assert_called_once_with(
            NODE,
            ['sh', '-c',
             'if virsh domstate sf:%s >/dev/null 2>&1; then echo defined; '
             'else echo undefined; fi' % UUID],
            sudo=True)

    def test_undefined_is_false(self):
        self.node_exec.return_value = ('undefined\n', '')

        self.assertFalse(self.harness._domain_is_defined(NODE, UUID))

    def test_an_unreadable_answer_fails(self):
        self.node_exec.return_value = ('sh: virsh: not found\n', '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_is_defined, NODE, UUID)

        self.assertIn('Could not tell whether', str(exc))
        self.assertIn(UUID, str(exc))


class _SkipHarness(ci_base.BaseTestCase):
    """A bound ``self`` for ``_require_node_sudo()``, without setUp()."""

    def __init__(self, node_exec):
        self._node_exec = node_exec
        self.skipped = None

    def skipTest(self, reason):
        self.skipped = reason


class RequireNodeSudoTestCase(test_base.ShakenFistTestCase):
    """The loud skip guard, now shared rather than owned by dhcp_state.

    A silent no-op is what let the floating lifecycle test look healthy
    while asserting nothing, which is why this skips with the node named
    instead of letting a root-only read answer "absent".
    """

    def setUp(self):
        super().setUp()
        self.node_exec = mock.MagicMock()
        self.harness = _SkipHarness(self.node_exec)

    def test_a_working_sudo_does_not_skip(self):
        self.node_exec.return_value = ('', '')

        self.harness._require_node_sudo(NODE)

        self.assertIsNone(self.harness.skipped)
        self.node_exec.assert_called_once_with(NODE, ['true'], sudo=True)

    def test_a_refused_sudo_skips_with_the_node_named(self):
        self.node_exec.side_effect = ci_process.ProcessExecutionError(
            ['sudo', 'true'], 1, '', 'sudo: a password is required')

        self.harness._require_node_sudo(NODE)

        self.assertIsNotNone(self.harness.skipped)
        self.assertIn('Cannot exec as root', self.harness.skipped)
        self.assertIn('sf2', self.harness.skipped)
        self.assertIn('192.168.1.2', self.harness.skipped)


# The mixin under test. Its methods are copied onto the harnesses below
# rather than inherited, because the mixin also defines the functional
# test itself: a harness which inherited it would be collected by the
# unittest loader as a test case and run that test with no cluster.
_MIXIN = instance_state.InstanceDiskStateTestsMixin


class _ResidueHarness(ci_base.BaseTestCase):
    """A bound ``self`` for the residue readings, without setUp().

    The mixin's setUp() creates an instance on a cluster, so this
    supplies the hypervisor it would have chosen and scripts the two
    readings the residue count is built from.
    """

    _hypervisor_residue = _MIXIN._hypervisor_residue
    _await_hypervisor_residue = _MIXIN._await_hypervisor_residue

    def __init__(self, path_present, domain_defined):
        self.hypervisor = NODE
        self._node_path_present = path_present
        self._domain_is_defined = domain_defined


class HypervisorResidueTestCase(test_base.ShakenFistTestCase):
    """What the hypervisor still holds, counted and named.

    One poll covers all three kinds of state so that a failure says which
    of them survived -- a domain which could not be undefined and the
    disks it holds open are the same defect seen twice, and a message
    naming both is what tells them from a directory which was simply not
    removed.
    """

    def setUp(self):
        super().setUp()
        self.path_present = mock.MagicMock()
        self.domain_defined = mock.MagicMock()
        self.harness = _ResidueHarness(self.path_present, self.domain_defined)

    def test_a_live_instance_holds_every_kind(self):
        self.path_present.return_value = True
        self.domain_defined.return_value = True

        residue = self.harness._hypervisor_residue(
            UUID, INSTANCE_DIRECTORY, NVRAM_PATH)

        self.assertEqual(instance_state.RESIDUE_KINDS, len(residue))
        self.assertIn(INSTANCE_DIRECTORY, residue[0])
        self.assertIn(NVRAM_PATH, residue[1])
        self.assertIn('sf:%s' % UUID, residue[2])

    def test_a_torn_down_instance_holds_nothing(self):
        self.path_present.return_value = False
        self.domain_defined.return_value = False

        self.assertEqual(
            [], self.harness._hypervisor_residue(
                UUID, INSTANCE_DIRECTORY, NVRAM_PATH))

    def test_a_relocated_nvram_is_read_on_its_own_path(self):
        # The nvram is normally inside the directory, but the reading is
        # of whatever path the domain declared, so the two are separate
        # questions.
        self.path_present.side_effect = lambda node, path: path == NVRAM_PATH
        self.domain_defined.return_value = False

        residue = self.harness._hypervisor_residue(
            UUID, INSTANCE_DIRECTORY, NVRAM_PATH)

        self.assertEqual(1, len(residue))
        self.assertIn(NVRAM_PATH, residue[0])


class AwaitHypervisorResidueTestCase(test_base.ShakenFistTestCase):
    """The poll, in both directions, and what it says when it gives up.

    Teardown is asynchronous, so absence is polled for rather than read
    once; and libvirt writes the nvram out as the domain starts, so
    presence is too. A read which happened to be taken before the thing
    it measures is the mistake both directions exist to avoid.
    """

    def setUp(self):
        super().setUp()
        self.path_present = mock.MagicMock()
        self.domain_defined = mock.MagicMock()
        self.harness = _ResidueHarness(self.path_present, self.domain_defined)
        # The module's own reference to the time module is replaced, not
        # the module's attributes, so the clock everything else reads is
        # real.
        self.clock = self.useFixture(
            fixtures.MockPatchObject(instance_state, 'time')).mock
        self.clock.time.return_value = 0
        self.sleep = self.clock.sleep

    def test_a_satisfied_count_returns_without_sleeping(self):
        self.path_present.return_value = False
        self.domain_defined.return_value = False

        self.harness._await_hypervisor_residue(
            0, UUID, INSTANCE_DIRECTORY, NVRAM_PATH, 300, 'gone')

        self.sleep.assert_not_called()

    def test_late_state_is_waited_for(self):
        # The nvram file appears as the domain starts, so the presence
        # assertion must outlast that rather than read once.
        self.path_present.side_effect = [
            True, False,          # directory yes, nvram not yet
            True, True,           # and now both
        ]
        self.domain_defined.return_value = True

        self.harness._await_hypervisor_residue(
            instance_state.RESIDUE_KINDS, UUID, INSTANCE_DIRECTORY,
            NVRAM_PATH, 60, 'created')

        self.assertEqual(1, self.sleep.call_count)

    def test_late_teardown_is_waited_for(self):
        self.path_present.side_effect = [
            True, True,
            False, False,
        ]
        self.domain_defined.side_effect = [True, False]

        self.harness._await_hypervisor_residue(
            0, UUID, INSTANCE_DIRECTORY, NVRAM_PATH, 300, 'deleted')

        self.assertEqual(1, self.sleep.call_count)

    def test_the_deadline_fails_and_names_what_is_left(self):
        self.path_present.side_effect = lambda node, path: (
            path == INSTANCE_DIRECTORY)
        self.domain_defined.return_value = True
        # The deadline is taken at 0, so it is 300. The first clock
        # read after a reading is inside it and the second is past it, so
        # two readings happen and the loop then gives up.
        self.clock.time.side_effect = [0, 299.5, 400]
        self.sleep.side_effect = [
            None, AssertionError('slept again after the deadline')]

        exc = self.assertRaises(
            AssertionError, self.harness._await_hypervisor_residue,
            0, UUID, INSTANCE_DIRECTORY, NVRAM_PATH, 300,
            'Instance %s was deleted' % UUID)

        message = str(exc)
        self.assertIn('Instance %s was deleted' % UUID, message)
        self.assertIn('holds 2 of the 3 kinds', message)
        self.assertIn(INSTANCE_DIRECTORY, message)
        self.assertIn('sf:%s' % UUID, message)
        # The nvram was gone, so it is not reported as left behind.
        self.assertNotIn('its nvram file', message)

    def test_a_create_which_never_got_there_fails_saying_so(self):
        self.path_present.return_value = False
        self.domain_defined.return_value = False
        self.clock.time.side_effect = [0, 100]

        exc = self.assertRaises(
            AssertionError, self.harness._await_hypervisor_residue,
            instance_state.RESIDUE_KINDS, UUID, INSTANCE_DIRECTORY,
            NVRAM_PATH, 60, 'A created UEFI instance has all three')

        message = str(exc)
        self.assertIn('A created UEFI instance has all three', message)
        self.assertIn('holds 0 of the 3 kinds', message)
        self.assertIn('rather than 3: nothing', message)


class ChosenHypervisorTestCase(test_base.ShakenFistTestCase):
    """Which hypervisor the test pins to, and the skip when there is none.

    Chosen before the create, so an unreachable node skips before the
    test has built anything, and ordered by name so a failure on a given
    cluster names the same node every run.
    """

    class _Harness(ci_base.BaseTestCase):
        _state_hypervisor = _MIXIN._state_hypervisor

        def __init__(self, hypervisors):
            self._hypervisors = hypervisors
            self.skipped = None

        def _hypervisor_nodes(self):
            return self._hypervisors

        def skipTest(self, reason):
            self.skipped = reason
            raise RuntimeError(reason)

    def test_the_first_hypervisor_by_name_is_chosen(self):
        harness = self._Harness([
            {'name': 'sf3', 'uuid': 'u3'},
            {'name': 'sf1', 'uuid': 'u1'},
            {'name': 'sf2', 'uuid': 'u2'},
        ])

        self.assertEqual('sf1', harness._state_hypervisor()['name'])

    def test_a_cluster_with_no_hypervisor_skips(self):
        harness = self._Harness([])

        self.assertRaises(RuntimeError, harness._state_hypervisor)
        self.assertIn('no named hypervisor', harness.skipped)

    def test_a_nameless_node_is_not_a_candidate(self):
        # The pin is by name, so a node the API reported without one
        # cannot be pinned to -- and force_placement=None is no pin at
        # all, which would place the instance anywhere and leave the
        # assertions reading a node it was never on.
        harness = self._Harness([{'uuid': 'u1'}, {'name': 'sf1',
                                                  'uuid': 'u2'}])

        self.assertEqual('u2', harness._state_hypervisor()['uuid'])

    def test_only_nameless_nodes_skips(self):
        harness = self._Harness([{'uuid': 'u1'}])

        self.assertRaises(RuntimeError, harness._state_hypervisor)
        self.assertIn('no named hypervisor', harness.skipped)
