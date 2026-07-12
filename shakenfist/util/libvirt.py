import importlib
from types import ModuleType
from typing import Any, Iterator, Self
from xml.etree import ElementTree

from shakenfist_utilities import logs  # noreorder


LOG, _ = logs.setup(__name__)
LIBVIRT: ModuleType | None = None


def get_libvirt() -> ModuleType:
    global LIBVIRT

    if not LIBVIRT:
        LIBVIRT = importlib.import_module('libvirt')

    return LIBVIRT


def get_cpu_count() -> int:
    with LibvirtConnection() as lc:
        present_cpus, _, _ = lc.get_cpu_map()

    return present_cpus


class LibvirtConnection():
    def __init__(self) -> None:
        self.libvirt: ModuleType | None = None
        self.conn: Any = None

    def __enter__(self) -> Self:
        self.libvirt = get_libvirt()
        self.conn = self.libvirt.open('qemu:///system')
        return self

    def __exit__(self, *args: Any) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_domain_from_sf_uuid(self, u: str) -> Any:
        try:
            return self.conn.lookupByName(f'sf:{u}')
        except self.libvirt.libvirtError as e:
            LOG.debug(f'SF libvirt domain {u} not found: {e}')
            return None

    def extract_power_state(self, domain: Any) -> str:
        state, _ = domain.state()
        if state == self.libvirt.VIR_DOMAIN_SHUTOFF:
            return 'off'

        if state == self.libvirt.VIR_DOMAIN_CRASHED:
            return 'crashed'

        if state in [self.libvirt.VIR_DOMAIN_PAUSED,
                     self.libvirt.VIR_DOMAIN_PMSUSPENDED]:
            return 'paused'

        # Covers all "running states": BLOCKED, NOSTATE,
        # RUNNING, SHUTDOWN
        return 'on'

    def extract_power_state_pretty(self, domain: Any) -> str:
        # We skip the reason code because they don't look super useful. They're
        # in a series of enums with names like VirtDomainCrashedReason as
        # documented at https://libvirt.org/html/libvirt-libvirt-domain.html
        # if we ever change our mind about that.
        state, _ = domain.state()

        # https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainState
        libvirt_states_to_strings = {
            self.libvirt.VIR_DOMAIN_NOSTATE: 'no state',
            self.libvirt.VIR_DOMAIN_RUNNING: 'running',
            self.libvirt.VIR_DOMAIN_BLOCKED: 'blocked',
            self.libvirt.VIR_DOMAIN_PAUSED: 'paused',
            self.libvirt.VIR_DOMAIN_SHUTDOWN: 'shutdown',
            self.libvirt.VIR_DOMAIN_SHUTOFF: 'shutoff',
            self.libvirt.VIR_DOMAIN_CRASHED: 'crashed',
            self.libvirt.VIR_DOMAIN_PMSUSPENDED: 'power management suspended'
        }
        return libvirt_states_to_strings[state]

    def define_xml(self, xml: str) -> Any:
        return self.conn.defineXML(xml)

    def get_sf_domains(self) -> Iterator[Any]:
        for domain in self.get_all_domains():
            try:
                if not domain.name().startswith('sf:'):
                    continue
                yield domain

            except self.libvirt.libvirtError:
                pass

    def get_all_domains(self) -> Iterator[Any]:
        # Active VMs have an ID. Active means running in libvirt
        # land.
        for domain_id in self.conn.listDomainsID():
            try:
                domain = self.conn.lookupByID(domain_id)
                if not domain.name().startswith('sf:'):
                    continue

                yield domain

            except self.libvirt.libvirtError:
                pass

    def get_cpu_map(self) -> tuple[int, int, list[bool]]:
        return self.conn.getCPUMap()

    def get_max_vcpus(self) -> int:
        return self.conn.getMaxVcpus(None)

    def get_memory_stats(self) -> dict[str, int]:
        return self.conn.getMemoryStats(
            self.libvirt.VIR_NODE_MEMORY_STATS_ALL_CELLS)

    def get_screenshot(self, instance_uuid: str, dest_path: str) -> None:
        domain = self.get_domain_from_sf_uuid(instance_uuid)
        stream = self.conn.newStream()

        # The numeric argument here is the display number. We just assume there
        # is only one for now.
        domain.screenshot(stream, 0)

        with open(dest_path, 'wb') as f:
            while d := stream.recv(262120):
                f.write(d)

        stream.finish()


def extract_hypervisor_devices(domain: Any) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {
        'disk': [],
        'network': [],
    }

    tree = ElementTree.fromstring(domain.XMLDesc())
    devices = tree.find('devices')
    for child in devices:
        if child.tag == 'disk':
            disk_xml = child.find('target')
            if disk_xml is not None:
                disk_device = disk_xml.attrib.get('dev')
                out['disk'].append(disk_device)

        if child.tag == 'interface':
            mac_xml = child.find('mac')
            mac_address = None
            if mac_xml is not None:
                mac_address = mac_xml.attrib.get('address')

            iface_xml = child.find('target')
            hypervisor_interface = None
            if iface_xml is not None:
                hypervisor_interface = iface_xml.attrib.get('dev')

            if mac_address and hypervisor_interface:
                out['network'].append((mac_address, hypervisor_interface))

    return out


def extract_statistics(domain: Any) -> dict[str, Any]:
    libvirt = get_libvirt()
    devices = extract_hypervisor_devices(domain)
    raw_stats = domain.getCPUStats(True)

    out: dict[str, Any] = {
        'cpu usage': {
            'cpu time ns': raw_stats[0]['cpu_time'],
            'system time ns': raw_stats[0]['system_time'],
            'user time ns': raw_stats[0]['user_time']
        },
        'memory usage': {},
        'disk usage': {},
        'network usage': {}
    }

    # Memory statistics come from the balloon driver. The host side
    # values (actual, rss) are always available, but the guest internal
    # values (unused, available, swap in/out, faults) require a virtio
    # memballoon with a stats collection period in the domain XML and a
    # guest driver which reports them, so default anything missing.
    try:
        mem_stats = domain.memoryStats()
        out['memory usage'] = {
            'actual kb': mem_stats.get('actual', 0),
            'rss kb': mem_stats.get('rss', 0),
            'unused kb': mem_stats.get('unused', 0),
            'available kb': mem_stats.get('available', 0),
            'swap in kb': mem_stats.get('swap_in', 0),
            'swap out kb': mem_stats.get('swap_out', 0),
            'major fault': mem_stats.get('major_fault', 0),
            'minor fault': mem_stats.get('minor_fault', 0),
        }
    except libvirt.libvirtError as e:
        LOG.debug(f'Failed to collect memory statistics: {e}')

    for disk_device in devices['disk']:
        raw_stats = domain.blockStats(disk_device)
        out['disk usage'][disk_device] = {
            'read requests': raw_stats[0],
            'read bytes': raw_stats[1],
            'write requests': raw_stats[2],
            'write bytes': raw_stats[3],
            'errors': raw_stats[4],
        }

        # Capacity is the logical disk size the guest sees, allocation
        # is the space used within the image, and physical is the host
        # disk space consumed (sparse files and qcow2 images can make
        # these differ). Media-less devices (an empty cdrom) have no
        # block info.
        try:
            block_info = domain.blockInfo(disk_device)
            out['disk usage'][disk_device].update({
                'capacity bytes': block_info[0],
                'allocation bytes': block_info[1],
                'physical bytes': block_info[2],
            })
        except libvirt.libvirtError as e:
            LOG.debug(
                f'Failed to collect block info for {disk_device}: {e}')

    for mac_address, hypervisor_interface in devices['network']:
        raw_stats = domain.interfaceStats(hypervisor_interface)
        out['network usage'][mac_address] = {
            'read bytes': raw_stats[0],
            'read packets': raw_stats[1],
            'read errors': raw_stats[2],
            'read drops': raw_stats[3],
            'write bytes': raw_stats[4],
            'write packets': raw_stats[5],
            'write errors': raw_stats[6],
            'write drops':  raw_stats[7]
        }

    return out
