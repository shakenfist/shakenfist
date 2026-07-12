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
