# Copyright 2019 Michael Still and contributors
"""Unit tests for the power state helpers in ``shakenfist_ci/base.py``.

``_assert_power_state()``, ``_domain_autostart()``,
``_domain_root_disk_path()``, ``_node_by_identifier()`` and
``_await_instance_event()`` are all read by the power state tests in
``guest_ci_tests/test_state_changes.py``.

``_domain_disks()`` is covered here too, beside the two other helpers
which read a domain with ``virsh``, even though its caller is
``cluster_ci_tests/test_disk_specs.py`` rather than the power state
tests: a module of its own would mean another copy of the ``base.py``
loader below, for no coverage this file cannot give it.

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

import fixtures

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


class _NodeLookupHarness(ci_base.BaseTestCase):
    """A bound ``self`` for the node and event lookups, without setUp().

    Mirrors _PowerStateHarness above.
    """

    def __init__(self, system_client):
        self.system_client = system_client


class NodeByIdentifierTestCase(test_base.ShakenFistTestCase):
    """One lookup resolves an instance's ``node`` field to a node dict.

    The field has carried both a node uuid and a node name, because
    ``Node.from_db()`` accepts either, so the lookup matches on both
    rather than on whichever form the cluster under test happens to use.
    """

    NODES = [
        {'uuid': 'uuid-1', 'name': 'sf-1', 'fqdn': 'sf-1.example.com'},
        {'uuid': 'uuid-2', 'name': 'sf-2', 'fqdn': 'sf-2.example.com'},
    ]

    def setUp(self):
        super().setUp()
        self.system_client = mock.MagicMock()
        self.system_client.get_nodes.return_value = self.NODES
        self.harness = _NodeLookupHarness(self.system_client)

    def _lookup(self, identifier):
        return self.harness._node_by_identifier(
            self.harness._get_cluster_nodes(), identifier)

    def test_a_uuid_resolves(self):
        self.assertEqual(self.NODES[1], self._lookup('uuid-2'))

    def test_a_name_resolves(self):
        self.assertEqual(self.NODES[1], self._lookup('sf-2'))

    def test_an_fqdn_resolves(self):
        self.assertEqual(self.NODES[0], self._lookup('sf-1.example.com'))

    def test_an_unknown_identifier_is_none(self):
        self.assertIsNone(self._lookup('sf-9'))

    def test_no_identifier_is_none(self):
        # An instance which has not been placed yet has no node, which is
        # not an error for a caller which then skips the node exec.
        self.assertIsNone(self._lookup(None))


class AwaitInstanceEventTestCase(test_base.ShakenFistTestCase):
    """The wait matches an event's ``message``, which is the only field it
    could match: ``EventReadRow`` has no ``operation``. The second matcher
    it used to carry could only ever repeat the first, so it is gone.
    """

    def setUp(self):
        super().setUp()
        self.system_client = mock.MagicMock()
        self.harness = _NodeLookupHarness(self.system_client)
        self.harness._await_instance_create = mock.MagicMock()
        self.harness._log_console = mock.MagicMock()
        self.harness._log_instance_events = mock.MagicMock()

    def test_a_matching_message_returns_its_timestamp(self):
        self.system_client.get_instance_events.return_value = [
            {'message': 'poweroff', 'timestamp': 10},
            {'message': 'detected poweroff', 'timestamp': 20},
        ]

        self.assertEqual(
            20,
            self.harness._await_instance_event('uuid1', 'detected poweroff'))

    def test_an_event_from_before_after_is_ignored(self):
        # The same operation can happen more than once in a test, so a wait
        # must not be satisfied by the previous one.
        self.system_client.get_instance_events.return_value = [
            {'message': 'detected poweroff', 'timestamp': 10},
            {'message': 'detected poweroff', 'timestamp': 30},
        ]

        self.assertEqual(
            30,
            self.harness._await_instance_event(
                'uuid1', 'detected poweroff', after=20))

    def test_the_instance_must_be_created_first(self):
        self.system_client.get_instance_events.return_value = [
            {'message': 'poweron', 'timestamp': 10}]

        self.harness._await_instance_event('uuid1', 'poweron')

        self.harness._await_instance_create.assert_called_once_with('uuid1')


class AwaitInstanceEventExtraTestCase(test_base.ShakenFistTestCase):
    """The wait outlasts the eventlog drainer rather than reading once.

    Issue 4919: an event emitted before the instance reached 'created'
    was not yet readable when the CI test looked, because add_event()
    only spools it locally. The helper must keep reading until the event
    arrives, and must not return early on an event which matches the
    message but lacks the field.
    """

    def setUp(self):
        super().setUp()
        self.system_client = mock.MagicMock()
        self.harness = _NodeLookupHarness(self.system_client)
        # base.py's own reference to the time module is replaced, not the
        # module's attributes, so the clock everything else reads is real.
        self.clock = self.useFixture(
            fixtures.MockPatchObject(ci_base, 'time')).mock
        self.clock.time.return_value = 0
        self.sleep = self.clock.sleep

    def test_a_late_event_is_waited_for(self):
        self.system_client.get_instance_events.side_effect = [
            [],
            [{'message': 'poweron', 'extra': None}],
            [{'message': 'poweron', 'extra': None},
             {'message': 'poweron', 'extra': {'libvirt_returned_at': 12.5}}],
        ]

        self.assertEqual(
            [12.5],
            self.harness._await_instance_event_extra(
                'uuid1', 'poweron', 'libvirt_returned_at'))
        self.assertEqual(
            3, self.system_client.get_instance_events.call_count)
        self.assertEqual(2, self.sleep.call_count)

    def test_other_messages_do_not_satisfy_the_wait(self):
        self.system_client.get_instance_events.side_effect = [
            [{'message': 'poweroff', 'extra': {'libvirt_returned_at': 1.0}}],
            [{'message': 'poweron', 'extra': {'libvirt_returned_at': 2.0}}],
        ]

        self.assertEqual(
            [2.0],
            self.harness._await_instance_event_extra(
                'uuid1', 'poweron', 'libvirt_returned_at'))

    def test_every_carrier_is_returned_so_the_caller_can_count(self):
        self.system_client.get_instance_events.return_value = [
            {'message': 'poweroff', 'extra': {'libvirt_returned_at': 1.0}},
            {'message': 'poweroff', 'extra': {}},
            {'message': 'poweroff', 'extra': {'libvirt_returned_at': 3.0}},
        ]

        self.assertEqual(
            [1.0, 3.0],
            self.harness._await_instance_event_extra(
                'uuid1', 'poweroff', 'libvirt_returned_at'))
        self.sleep.assert_not_called()

    def test_gives_up_empty_at_the_deadline(self):
        self.system_client.get_instance_events.return_value = []
        # The deadline is taken at 0, so it is 30; the reads are at 29.5
        # and 30. A read exactly at the deadline still happens, and none
        # follows it.
        self.clock.time.side_effect = [0, 29.5, 30]
        # A wait which ignored the deadline would otherwise spin forever
        # on the mocked sleep rather than fail.
        self.sleep.side_effect = [
            None, AssertionError('slept again after the deadline')]

        self.assertEqual(
            [],
            self.harness._await_instance_event_extra(
                'uuid1', 'poweron', 'libvirt_returned_at'))
        self.assertEqual(
            2, self.system_client.get_instance_events.call_count)


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


# Realistic `virsh dumpxml` output: libvirt's own rewrite of what
# libvirt.tmpl handed it, so each disk has gained an alias and a drive
# address and the implicit controllers are present. The domain is the one
# cluster_ci_tests/test_disk_specs.py builds -- a sata root disk, its
# config drive on the same bus, a blank scsi disk and a defaulted cdrom
# moved to usb -- trimmed to the devices _domain_disks() reads.
DUMPXML_UUID = '1234abcd-0000-0000-0000-000000000000'
DUMPXML_DIR = '/srv/shakenfist/instances/%s' % DUMPXML_UUID


def _dumpxml_disk(device, target, bus, fmt, source, driver=True):
    driver_element = ''
    if driver:
        driver_element = (
            "      <driver name='qemu' type='%s' cache='none'\n"
            "              error_policy='stop' rerror_policy='stop'/>\n"
            % fmt)
    return (
        "    <disk type='file' device='%s'>\n"
        '%s'
        "      <source file='%s' index='1'/>\n"
        "      <target dev='%s' bus='%s'/>\n"
        "      <alias name='%s-0-0'/>\n"
        "      <address type='drive' controller='0' bus='0' target='0' "
        "unit='0'/>\n"
        '    </disk>\n'
        % (device, driver_element, source, target, bus, bus))


def _dumpxml(disks, trailer=''):
    return (
        "<domain type='kvm' id='7'>\n"
        '  <name>sf:%s</name>\n'
        '  <uuid>%s</uuid>\n'
        '  <devices>\n'
        '    <emulator>/usr/bin/kvm</emulator>\n'
        '%s'
        "    <controller type='sata' index='0'>\n"
        "      <alias name='ide'/>\n"
        '    </controller>\n'
        "    <memballoon model='virtio' freePageReporting='on'>\n"
        "      <stats period='10'/>\n"
        '    </memballoon>\n'
        '  </devices>\n'
        '%s'
        '</domain>\n'
        % (DUMPXML_UUID, DUMPXML_UUID, ''.join(disks), trailer))


DUMPXML_OUTPUT = _dumpxml([
    _dumpxml_disk('disk', 'sda', 'sata', 'qcow2', '%s/sda' % DUMPXML_DIR),
    _dumpxml_disk('disk', 'sdb', 'sata', 'raw', '%s/sdb' % DUMPXML_DIR),
    _dumpxml_disk('disk', 'sdc', 'scsi', 'qcow2', '%s/sdc' % DUMPXML_DIR),
    _dumpxml_disk('cdrom', 'sdd', 'usb', 'raw', '%s/sdd.raw' % DUMPXML_DIR),
])

# An nvme disk has no <disk> element at all: it is rendered as
# qemu:commandline arguments, which libvirt echoes back untouched.
DUMPXML_NVME = _dumpxml(
    [_dumpxml_disk('disk', 'vda', 'virtio', 'qcow2',
                   '%s/vda' % DUMPXML_DIR)],
    trailer=(
        "  <qemu:commandline\n"
        "      xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>\n"
        "    <qemu:arg value='-drive'/>\n"
        "    <qemu:arg value='file=%s/nvme1,format=qcow2,if=none,"
        "id=NVME1,werror=stop,rerror=stop,discard=unmap'/>\n"
        "    <qemu:arg value='-device'/>\n"
        "    <qemu:arg value='nvme,drive=NVME1,serial=nvme-1'/>\n"
        '  </qemu:commandline>\n' % DUMPXML_DIR))

DUMPXML_NO_DISKS = _dumpxml([])

DUMPXML_NO_TARGET = _dumpxml([
    "    <disk type='file' device='disk'>\n"
    "      <driver name='qemu' type='qcow2' cache='none'/>\n"
    "      <source file='%s/sda'/>\n"
    '    </disk>\n' % DUMPXML_DIR,
])

DUMPXML_NO_DRIVER = _dumpxml([
    _dumpxml_disk('disk', 'sda', 'sata', 'qcow2', '%s/sda' % DUMPXML_DIR,
                  driver=False),
])

# One target device name claimed by a disk on each of two buses, which is
# what a collision in instance._get_disk_device() would look like from
# here. libvirt would refuse such a domain, so this is a parser test
# rather than a domain a hypervisor could hold.
DUMPXML_DUPLICATE_TARGET = _dumpxml([
    _dumpxml_disk('disk', 'sda', 'sata', 'qcow2', '%s/sda' % DUMPXML_DIR),
    _dumpxml_disk('cdrom', 'sda', 'usb', 'raw', '%s/sda.raw' % DUMPXML_DIR),
])


class DomainDisksTestCase(test_base.ShakenFistTestCase):
    """Unit tests for ``BaseTestCase._domain_disks()``.

    test_requested_bus_and_type_reach_libvirt asserts on what this
    returns, so a parser which quietly dropped a disk, or read an
    attribute off the wrong element, would turn that assertion into a
    weaker one rather than fail. These tests pin the shape of the answer
    and every way the helper is allowed to refuse to give one.
    """

    def setUp(self):
        super().setUp()
        self.node_exec = mock.MagicMock()
        self.harness = _DomainAutostartHarness(self.node_exec)
        self.node = {'name': 'sf2', 'ip': '192.168.1.2'}

    def test_every_disk_is_reported_by_target(self):
        self.node_exec.return_value = (DUMPXML_OUTPUT, '')

        result = self.harness._domain_disks(self.node, DUMPXML_UUID)

        self.assertEqual(
            {
                'sda': {'device': 'disk', 'bus': 'sata',
                        'format': 'qcow2', 'cache': 'none'},
                'sdb': {'device': 'disk', 'bus': 'sata',
                        'format': 'raw', 'cache': 'none'},
                'sdc': {'device': 'disk', 'bus': 'scsi',
                        'format': 'qcow2', 'cache': 'none'},
                'sdd': {'device': 'cdrom', 'bus': 'usb',
                        'format': 'raw', 'cache': 'none'},
            }, result)
        self.node_exec.assert_called_once_with(
            self.node, ['virsh', 'dumpxml', 'sf:%s' % DUMPXML_UUID],
            sudo=True)

    def test_an_nvme_disk_is_invisible(self):
        # Documented, not incidental: a caller asserting on an nvme disk
        # has to read the domain's qemu arguments instead.
        self.node_exec.return_value = (DUMPXML_NVME, '')

        result = self.harness._domain_disks(self.node, DUMPXML_UUID)

        self.assertEqual(['vda'], list(result.keys()))

    def test_a_disk_without_a_driver_reports_none(self):
        # The template always writes one, so this is a stale libvirt.tmpl
        # on the node rather than an impossible domain, and it has to
        # fail the caller's assertion rather than raise here.
        self.node_exec.return_value = (DUMPXML_NO_DRIVER, '')

        result = self.harness._domain_disks(self.node, DUMPXML_UUID)

        self.assertEqual(
            {'sda': {'device': 'disk', 'bus': 'sata',
                     'format': None, 'cache': None}}, result)

    def test_no_disks_fails(self):
        self.node_exec.return_value = (DUMPXML_NO_DISKS, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_disks,
            self.node, DUMPXML_UUID)

        self.assertIn('No disks in virsh dumpxml', str(exc))
        self.assertIn(DUMPXML_UUID, str(exc))
        self.assertIn('memballoon', str(exc))

    def test_a_disk_without_a_target_fails(self):
        self.node_exec.return_value = (DUMPXML_NO_TARGET, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_disks,
            self.node, DUMPXML_UUID)

        self.assertIn('has no target device name', str(exc))
        self.assertIn(DUMPXML_UUID, str(exc))

    def test_a_duplicate_target_fails(self):
        # Rather than one disk silently replacing the other in the
        # answer, which the caller would read as a disk never having
        # been defined at all.
        self.node_exec.return_value = (DUMPXML_DUPLICATE_TARGET, '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_disks,
            self.node, DUMPXML_UUID)

        self.assertIn('both claim target device sda', str(exc))
        self.assertIn(DUMPXML_UUID, str(exc))

    def test_output_which_is_not_xml_fails(self):
        # virsh writes its errors to stderr and exits non zero, which
        # _node_exec() raises on, so this is a truncated or otherwise
        # unexpected stdout rather than an error message.
        self.node_exec.return_value = ('<domain type=\'kvm\'>\n', '')

        exc = self.assertRaises(
            AssertionError, self.harness._domain_disks,
            self.node, DUMPXML_UUID)

        self.assertIn('is not XML', str(exc))
        self.assertIn(DUMPXML_UUID, str(exc))
