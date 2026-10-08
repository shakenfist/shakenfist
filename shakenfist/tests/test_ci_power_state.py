# Copyright 2019 Michael Still and contributors
"""Unit tests for ``BaseTestCase._assert_power_state()``.

The helper reads ``system_client.get_instance()['power_state']`` exactly
once and fails without waiting or retrying: a wait would let a later
writer such as the cleaner correct a wrong value first, and pass a call
that returned without recording what it did. These tests exercise that
contract directly, unbound from the rest of the functional suite.

``shakenfist_ci/base.py`` is loaded by path, with ``shakenfist_client``
and ``prettytable`` stubbed out, because neither is a test dependency of
this repository. This is the same loader ``test_ci_capacity_wait.py`` and
``test_ci_assert_refused_at_stage.py`` each carry their own copy of; a
third copy is written here rather than imported from either, following
their precedent of not sharing it across test modules.
"""

import logging
import os
import sys
import types
from unittest import mock

from shakenfist.tests import base as test_base


CI_SUITE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'deploy', 'shakenfist_ci')


def _client_stubs():
    """A shakenfist_client stub with just the exception taxonomy.

    ``base.py`` references several of these names at module level (the
    tuple of agent exceptions near the top of the file), so the stub
    must define them even though this test never raises one.
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
    apiclient.Client = type('Client', (), {'__init__': lambda self, *a, **kw: None})

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
        return ci_base

    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)


ci_base = _load_ci_base()


class _PowerStateHarness(ci_base.BaseTestCase):
    """A bound ``self`` for ``_assert_power_state()`` without setUp().

    setUp() builds a real apiclient and writes a tracing event to
    /srv/ci/traces, neither of which a unit test should do, so the
    harness supplies only the one attribute the helper reads.
    """

    def __init__(self, system_client):
        # Deliberately not calling TestCase.__init__: this is not being
        # run as a test, only used as a bound self for the helper.
        self.system_client = system_client


class AssertPowerStateTestCase(test_base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.system_client = mock.MagicMock()
        self.harness = _PowerStateHarness(self.system_client)
        self.harness._emit_tracing_event = mock.MagicMock()
        self.harness._log_instance_events = mock.MagicMock()

    def test_matching_value_returns(self):
        self.system_client.get_instance.return_value = {'power_state': 'on'}

        self.harness._assert_power_state('uuid1', 'on', 'after create')

        self.system_client.get_instance.assert_called_once_with('uuid1')
        self.harness._log_instance_events.assert_not_called()
        self.harness._emit_tracing_event.assert_called_once()

    def test_wrong_value_fails_with_a_useful_message(self):
        self.system_client.get_instance.return_value = {'power_state': 'paused'}

        exc = self.assertRaises(
            AssertionError, self.harness._assert_power_state,
            'uuid1', 'on', 'after unpause')

        message = str(exc)
        self.assertIn("expected 'on'", message)
        self.assertIn("power_state 'paused'", message)
        self.assertIn('after unpause', message)
        self.harness._log_instance_events.assert_called_once_with('uuid1')

    def test_never_sleeps(self):
        self.system_client.get_instance.return_value = {'power_state': 'paused'}

        with mock.patch.object(ci_base.time, 'sleep') as mock_sleep:
            self.assertRaises(
                AssertionError, self.harness._assert_power_state,
                'uuid1', 'on', 'after unpause')

        mock_sleep.assert_not_called()

    def test_missing_power_state_key_fails(self):
        self.system_client.get_instance.return_value = {}

        self.assertRaises(
            AssertionError, self.harness._assert_power_state,
            'uuid1', 'on', 'after create')


# Realistic `virsh dominfo` output, trimmed to the lines _domain_autostart()
# cares about plus enough neighbours that a parser keyed on the wrong line
# would be caught.
DOMINFO_AUTOSTART_ENABLED = """\
Id:             3
Name:           sf:1234abcd-0000-0000-0000-000000000000
UUID:           1234abcd-0000-0000-0000-000000000000
State:          running
CPU(s):         1
Max memory:     1048576 KiB
Used memory:    1048576 KiB
Persistent:     yes
Autostart:      enable
Managed save:   no
Security model: none
Security DOI:   0
"""

DOMINFO_AUTOSTART_DISABLED = DOMINFO_AUTOSTART_ENABLED.replace(
    'Autostart:      enable', 'Autostart:      disable')

DOMINFO_NO_AUTOSTART_LINE = (
    'Id:             3\n'
    'Name:           sf:1234abcd-0000-0000-0000-000000000000\n'
    'State:          running\n'
    'Persistent:     yes\n'
    'Managed save:   no\n')

DOMINFO_UNEXPECTED_AUTOSTART_VALUE = DOMINFO_AUTOSTART_ENABLED.replace(
    'Autostart:      enable', 'Autostart:      maybe')


class _DomainAutostartHarness(ci_base.BaseTestCase):
    """A bound ``self`` for ``_domain_autostart()`` without setUp().

    Mirrors _PowerStateHarness above: setUp() does things a unit test
    should not, so the harness supplies only what the helper reads, and
    the test patches ``_node_exec`` directly (as an instance attribute
    overriding the method inherited from ci_base.BaseTestCase).
    """

    def __init__(self, node_exec):
        # Deliberately not calling TestCase.__init__: this is not being
        # run as a test, only used as a bound self for the helper.
        self._node_exec = node_exec


class DomainAutostartTestCase(test_base.ShakenFistTestCase):
    """Unit tests for ``BaseTestCase._domain_autostart()``.

    The flag is read with ``virsh dominfo`` on the instance's hypervisor.
    These tests exercise the parsing without a real cluster, the same way
    AssertPowerStateTestCase above and _detected_poweroff_reason's tests
    exercise their helpers.
    """

    def setUp(self):
        super().setUp()
        self.node_exec = mock.MagicMock()
        self.harness = _DomainAutostartHarness(self.node_exec)
        self.node = {'name': 'sf2', 'ip': '192.168.1.2'}

    def test_enabled_returns_true(self):
        self.node_exec.return_value = (DOMINFO_AUTOSTART_ENABLED, '')

        result = self.harness._domain_autostart(self.node, 'uuid1')

        self.assertTrue(result)
        self.node_exec.assert_called_once_with(
            self.node, ['virsh', 'dominfo', 'sf:uuid1'], sudo=True)

    def test_disabled_returns_false(self):
        self.node_exec.return_value = (DOMINFO_AUTOSTART_DISABLED, '')

        result = self.harness._domain_autostart(self.node, 'uuid1')

        self.assertFalse(result)
        self.node_exec.assert_called_once_with(
            self.node, ['virsh', 'dominfo', 'sf:uuid1'], sudo=True)

    def test_missing_autostart_line_fails(self):
        self.node_exec.return_value = (DOMINFO_NO_AUTOSTART_LINE, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_autostart, self.node, 'uuid1')

        self.assertIn('No Autostart line', str(exc))
        self.assertIn('uuid1', str(exc))
        self.assertIn(DOMINFO_NO_AUTOSTART_LINE, str(exc))

    def test_unexpected_autostart_value_fails(self):
        self.node_exec.return_value = (DOMINFO_UNEXPECTED_AUTOSTART_VALUE, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_autostart, self.node, 'uuid1')

        self.assertIn('Unexpected Autostart value', str(exc))
        self.assertIn("'maybe'", str(exc))


# Realistic `virsh domblklist --details` output for an instance with a root
# disk and a config drive, as libvirt.tmpl defines them.
DOMBLKLIST_UUID = '1234abcd-0000-0000-0000-000000000000'
DOMBLKLIST_ROOT = '/srv/shakenfist/instances/%s/vda' % DOMBLKLIST_UUID
DOMBLKLIST_OUTPUT = (
    ' Type   Device   Target   Source\n'
    '-----------------------------------------------------------------------\n'
    ' file   disk     vda      %s\n'
    ' file   disk     vdb      /srv/shakenfist/instances/%s/vdb\n'
    '\n' % (DOMBLKLIST_ROOT, DOMBLKLIST_UUID))

# A cdrom ahead of the root disk, which must be skipped.
DOMBLKLIST_CDROM_FIRST = (
    ' Type   Device   Target   Source\n'
    '-----------------------------------------------------------------------\n'
    ' file   cdrom    sda      /srv/shakenfist/instances/%s/sda.raw\n'
    ' file   disk     vda      %s\n' % (DOMBLKLIST_UUID, DOMBLKLIST_ROOT))

DOMBLKLIST_NO_DISK = (
    ' Type   Device   Target   Source\n'
    '-----------------------------------------------------------------------\n'
    '\n')

DOMBLKLIST_SHARED_PATH = (
    ' Type   Device   Target   Source\n'
    '-----------------------------------------------------------------------\n'
    ' file   disk     vda      /srv/shakenfist/image_cache/abc.qcow2\n')


class DomainRootDiskPathTestCase(test_base.ShakenFistTestCase):
    """Unit tests for ``BaseTestCase._domain_root_disk_path()``.

    test_lifecycle_power_on_failure moves the path this returns aside as
    root, so these tests pin both the parsing and the refusal to return a
    path outside the instance's own directory.
    """

    def setUp(self):
        super().setUp()
        self.node_exec = mock.MagicMock()
        self.harness = _DomainAutostartHarness(self.node_exec)
        self.node = {'name': 'sf2', 'ip': '192.168.1.2'}

    def test_returns_first_disk(self):
        self.node_exec.return_value = (DOMBLKLIST_OUTPUT, '')

        result = self.harness._domain_root_disk_path(self.node, DOMBLKLIST_UUID)

        self.assertEqual(DOMBLKLIST_ROOT, result)
        self.node_exec.assert_called_once_with(
            self.node,
            ['virsh', 'domblklist', '--details', 'sf:%s' % DOMBLKLIST_UUID],
            sudo=True)

    def test_skips_cdrom(self):
        self.node_exec.return_value = (DOMBLKLIST_CDROM_FIRST, '')

        result = self.harness._domain_root_disk_path(self.node, DOMBLKLIST_UUID)

        self.assertEqual(DOMBLKLIST_ROOT, result)

    def test_no_disk_fails(self):
        self.node_exec.return_value = (DOMBLKLIST_NO_DISK, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_root_disk_path,
            self.node, DOMBLKLIST_UUID)

        self.assertIn('No disk in virsh domblklist', str(exc))
        self.assertIn(DOMBLKLIST_UUID, str(exc))

    def test_path_outside_instance_directory_fails(self):
        self.node_exec.return_value = (DOMBLKLIST_SHARED_PATH, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_root_disk_path,
            self.node, DOMBLKLIST_UUID)

        self.assertIn("not in the instance's directory", str(exc))
        self.assertIn('image_cache/abc.qcow2', str(exc))

    def test_another_instances_disk_fails(self):
        self.node_exec.return_value = (DOMBLKLIST_OUTPUT, '')

        self.assertRaises(
            AssertionError, self.harness._domain_root_disk_path,
            self.node, 'ffffffff-0000-0000-0000-000000000000')
