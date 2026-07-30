from unittest import mock

from shakenfist.tests import base
from shakenfist.util import libvirt as util_libvirt


DOMAIN_XML = """<domain type='kvm'>
  <devices>
    <disk type='file' device='disk'>
      <target dev='vda' bus='virtio'/>
    </disk>
    <interface type='bridge'>
      <mac address='02:00:00:12:34:56'/>
      <target dev='vnet0'/>
    </interface>
  </devices>
</domain>"""


class FakeLibvirtError(Exception):
    ...


class FakeLibvirtModule:
    libvirtError = FakeLibvirtError

    VIR_DOMAIN_NOSTATE = 0
    VIR_DOMAIN_RUNNING = 1
    VIR_DOMAIN_BLOCKED = 2
    VIR_DOMAIN_PAUSED = 3
    VIR_DOMAIN_SHUTDOWN = 4
    VIR_DOMAIN_SHUTOFF = 5
    VIR_DOMAIN_CRASHED = 6
    VIR_DOMAIN_PMSUSPENDED = 7

    VIR_DOMAIN_PAUSED_UNKNOWN = 0
    VIR_DOMAIN_PAUSED_USER = 1
    VIR_DOMAIN_PAUSED_IOERROR = 5

    VIR_DOMAIN_DISK_ERROR_NONE = 0
    VIR_DOMAIN_DISK_ERROR_UNSPEC = 1
    VIR_DOMAIN_DISK_ERROR_NO_SPACE = 2


class FakeDomain:
    def __init__(self, memory_stats=None, block_info=None):
        self._memory_stats = memory_stats
        self._block_info = block_info

    def XMLDesc(self):
        return DOMAIN_XML

    def getCPUStats(self, total):
        return [{'cpu_time': 1000, 'system_time': 300, 'user_time': 700}]

    def blockStats(self, device):
        return (10, 4096, 20, 8192, 0)

    def interfaceStats(self, device):
        return (100, 1, 0, 0, 200, 2, 0, 0)

    def memoryStats(self):
        if self._memory_stats is None:
            raise FakeLibvirtError('balloon not available')
        return self._memory_stats

    def blockInfo(self, device):
        if self._block_info is None:
            raise FakeLibvirtError('no media')
        return self._block_info


class FakeStatefulDomain:
    def __init__(self, state, reason=0, disk_errors=None,
                 disk_errors_raise=False):
        self._state = state
        self._reason = reason
        self._disk_errors = disk_errors or {}
        self._disk_errors_raise = disk_errors_raise

    def state(self):
        return [self._state, self._reason]

    def diskErrors(self):
        if self._disk_errors_raise:
            raise FakeLibvirtError('domain went away')
        return self._disk_errors


def _connection():
    lc = util_libvirt.LibvirtConnection()
    lc.libvirt = FakeLibvirtModule()
    return lc


class UtilLibvirtPowerState(base.ShakenFistTestCase):
    def test_pause_reason_not_paused(self):
        domain = FakeStatefulDomain(FakeLibvirtModule.VIR_DOMAIN_RUNNING)
        self.assertIsNone(_connection().extract_pause_reason(domain))

    def test_pause_reason_pmsuspended_is_not_paused(self):
        # PMSUSPENDED reports as 'paused' via extract_power_state, but it
        # is not VIR_DOMAIN_PAUSED so there is no pause reason.
        domain = FakeStatefulDomain(FakeLibvirtModule.VIR_DOMAIN_PMSUSPENDED)
        self.assertIsNone(_connection().extract_pause_reason(domain))

    def test_pause_reason_user(self):
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_PAUSED,
            reason=FakeLibvirtModule.VIR_DOMAIN_PAUSED_USER)
        self.assertEqual(
            'user request', _connection().extract_pause_reason(domain))

    def test_pause_reason_ioerror(self):
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_PAUSED,
            reason=FakeLibvirtModule.VIR_DOMAIN_PAUSED_IOERROR)
        self.assertEqual(
            'i/o error', _connection().extract_pause_reason(domain))

    def test_is_paused_ioerror(self):
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_PAUSED,
            reason=FakeLibvirtModule.VIR_DOMAIN_PAUSED_IOERROR)
        self.assertTrue(_connection().is_paused_ioerror(domain))

    def test_is_paused_ioerror_user_pause(self):
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_PAUSED,
            reason=FakeLibvirtModule.VIR_DOMAIN_PAUSED_USER)
        self.assertFalse(_connection().is_paused_ioerror(domain))

    def test_is_paused_ioerror_not_paused(self):
        # A stale I/O error reason code on a domain which is no longer
        # paused must not count: both the state and the reason matter.
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_RUNNING,
            reason=FakeLibvirtModule.VIR_DOMAIN_PAUSED_IOERROR)
        self.assertFalse(_connection().is_paused_ioerror(domain))

    def test_pause_reason_unrecognised(self):
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_PAUSED, reason=999)
        self.assertEqual(
            'unrecognised reason 999',
            _connection().extract_pause_reason(domain))

    def test_power_state_pretty_includes_pause_reason(self):
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_PAUSED,
            reason=FakeLibvirtModule.VIR_DOMAIN_PAUSED_IOERROR)
        self.assertEqual(
            'paused (i/o error)',
            _connection().extract_power_state_pretty(domain))

    def test_power_state_pretty_other_states_unchanged(self):
        domain = FakeStatefulDomain(FakeLibvirtModule.VIR_DOMAIN_RUNNING)
        self.assertEqual(
            'running', _connection().extract_power_state_pretty(domain))

    def test_disk_errors_filters_healthy_disks(self):
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_PAUSED,
            reason=FakeLibvirtModule.VIR_DOMAIN_PAUSED_IOERROR,
            disk_errors={
                'vda': FakeLibvirtModule.VIR_DOMAIN_DISK_ERROR_UNSPEC,
                'vdb': FakeLibvirtModule.VIR_DOMAIN_DISK_ERROR_NONE,
                'vdc': FakeLibvirtModule.VIR_DOMAIN_DISK_ERROR_NO_SPACE,
            })
        self.assertEqual(
            {'vda': 'unspecified error', 'vdc': 'no space'},
            _connection().extract_disk_errors(domain))

    def test_disk_errors_best_effort_on_libvirt_error(self):
        domain = FakeStatefulDomain(
            FakeLibvirtModule.VIR_DOMAIN_PAUSED, disk_errors_raise=True)
        self.assertEqual({}, _connection().extract_disk_errors(domain))


class UtilLibvirtStatistics(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        fake_libvirt = mock.patch.object(
            util_libvirt, 'LIBVIRT', FakeLibvirtModule())
        fake_libvirt.start()
        self.addCleanup(fake_libvirt.stop)

    def test_extract_statistics_full(self):
        domain = FakeDomain(
            memory_stats={
                'actual': 2097152,
                'rss': 1048576,
                'unused': 524288,
                'available': 2000000,
                'swap_in': 0,
                'swap_out': 12,
                'major_fault': 3,
                'minor_fault': 400,
            },
            block_info=(107374182400, 2147483648, 1073741824))

        stats = util_libvirt.extract_statistics(domain)

        self.assertEqual(
            {
                'cpu time ns': 1000,
                'system time ns': 300,
                'user time ns': 700,
            },
            stats['cpu usage'])
        self.assertEqual(
            {
                'actual kb': 2097152,
                'rss kb': 1048576,
                'unused kb': 524288,
                'available kb': 2000000,
                'swap in kb': 0,
                'swap out kb': 12,
                'major fault': 3,
                'minor fault': 400,
            },
            stats['memory usage'])
        self.assertEqual(
            {
                'read requests': 10,
                'read bytes': 4096,
                'write requests': 20,
                'write bytes': 8192,
                'errors': 0,
                'capacity bytes': 107374182400,
                'allocation bytes': 2147483648,
                'physical bytes': 1073741824,
            },
            stats['disk usage']['vda'])
        self.assertEqual(
            {
                'read bytes': 100,
                'read packets': 1,
                'read errors': 0,
                'read drops': 0,
                'write bytes': 200,
                'write packets': 2,
                'write errors': 0,
                'write drops': 0,
            },
            stats['network usage']['02:00:00:12:34:56'])

    def test_extract_statistics_partial_memory_stats(self):
        # Without a balloon stats period (or a guest driver reporting
        # them), memoryStats() only returns host side values. The guest
        # internal values must default to zero, not KeyError.
        domain = FakeDomain(
            memory_stats={'actual': 2097152, 'rss': 1048576},
            block_info=(1, 1, 1))

        stats = util_libvirt.extract_statistics(domain)

        self.assertEqual(2097152, stats['memory usage']['actual kb'])
        self.assertEqual(1048576, stats['memory usage']['rss kb'])
        self.assertEqual(0, stats['memory usage']['unused kb'])
        self.assertEqual(0, stats['memory usage']['swap out kb'])

    def test_extract_statistics_collection_failures(self):
        # A failing memoryStats() or blockInfo() call must not prevent
        # collection of the other statistics.
        domain = FakeDomain(memory_stats=None, block_info=None)

        stats = util_libvirt.extract_statistics(domain)

        self.assertEqual({}, stats['memory usage'])
        self.assertEqual(
            {
                'read requests': 10,
                'read bytes': 4096,
                'write requests': 20,
                'write bytes': 8192,
                'errors': 0,
            },
            stats['disk usage']['vda'])
        self.assertIn('02:00:00:12:34:56', stats['network usage'])
