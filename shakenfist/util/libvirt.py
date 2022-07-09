import importlib
from xml.etree import ElementTree


LIBVIRT = None


def get_libvirt():
    global LIBVIRT

    if not LIBVIRT:
        LIBVIRT = importlib.import_module('libvirt')

    return LIBVIRT


def get_cpu_count():
    with LibvirtConnection() as lc:
        present_cpus, _, _ = lc.get_cpu_map()

    return present_cpus


class LibvirtConnection():
    def __init__(self):
        self.libvirt = None
        self.conn = None

    def __enter__(self):
        self.libvirt = get_libvirt()
        self.conn = self.libvirt.open('qemu:///system')
        return self

    def __exit__(self, *args):
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_domain_from_sf_uuid(self, u):
        try:
            return self.conn.lookupByName('sf:' + u)
        except self.libvirt.libvirtError:
            return None

    def extract_power_state(self, domain):
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

    def define_xml(self, xml):
        return self.conn.defineXML(xml)

    def get_sf_domains(self):
        for domain in self.get_all_domains():
            try:
                if not domain.name().startswith('sf:'):
                    continue
                yield domain

            except self.libvirt.libvirtError:
                pass

    def get_all_domains(self):
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

    def get_cpu_map(self):
        return self.conn.getCPUMap()

    def get_max_vcpus(self):
        return self.conn.getMaxVcpus(None)

    def get_memory_stats(self):
        return self.conn.getMemoryStats(
            self.libvirt.VIR_NODE_MEMORY_STATS_ALL_CELLS)


def extract_hypervisor_devices(domain):
    out = {
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


def extract_statistics(domain):
    devices = extract_hypervisor_devices(domain)
    raw_stats = domain.getCPUStats(True)

    out = {
        'cpu usage': {
            'cpu time ns': raw_stats[0]['cpu_time'],
            'system time ns': raw_stats[0]['system_time'],
            'user time ns': raw_stats[0]['user_time']
        },
        'disk usage': {},
        'network usage': {}
    }

    for disk_device in devices['disk']:
        raw_stats = domain.blockStats(disk_device)
        out['disk usage'][disk_device] = {
            'read requests': raw_stats[0],
            'read bytes': raw_stats[1],
            'write requests': raw_stats[2],
            'write bytes': raw_stats[3],
            'errors': raw_stats[4],
        }

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
