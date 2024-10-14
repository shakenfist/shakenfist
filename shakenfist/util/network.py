import json
import os
import random
import re
import time

from oslo_concurrency import processutils
from shakenfist_utilities import logs

from shakenfist import exceptions
from shakenfist.config import config
from shakenfist.util import process
# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


LOG, _ = logs.setup(__name__)


def _clean_ip_json(data):
    # For reasons I can't explain, the ip command sometimes returns
    # slightly bogus JSON like this:
    #
    # $ ip -pretty -json addr show enp5s0
    # [ {},{},{},{
    #         "ifindex": 2,
    #         "ifname": "enp5s0",
    #         "flags": [ "BROADCAST","MULTICAST","UP","LOWER_UP" ],
    #         "mtu": 9000,
    #         "qdisc": "pfifo_fast",
    #         "operstate": "UP",
    #         "group": "default",
    #         "txqlen": 1000,
    #         "link_type": "ether",
    #         "address": "18:c0:4d:75:50:b9",
    #         "broadcast": "ff:ff:ff:ff:ff:ff",
    #         "addr_info": [ {
    #                 "family": "inet",
    #                 "local": "192.168.1.52",
    #                 "prefixlen": 24,
    #                 "broadcast": "192.168.1.255",
    #                 "scope": "global",
    #                 "dynamic": true,
    #                 "label": "enp5s0",
    #                 "valid_life_time": 3449,
    #                 "preferred_life_time": 3449
    #             } ]
    #     },{},{},{},{},...,{} ]
    #
    # This method strips out all those empty entries in the list

    if not data:
        return []

    j = json.loads(data)
    return [x for x in j if x]


def check_for_interface(name, namespace=None, up=False):
    if namespace:
        if not os.path.exists('/var/run/netns/%s' % namespace):
            return False

    stdout, stderr = process.execute(
        None, 'ip -pretty -json link show %s' % name,
        check_exit_code=[0, 1], namespace=namespace,
        suppress_command_logging=True)

    if stderr.rstrip('\n').endswith(' does not exist.'):
        return False

    if up:
        j = _clean_ip_json(stdout)
        return 'UP' in j[0]['flags']

    return True


def get_interface_addresses(name, namespace=None):
    stdout, _ = process.execute(
        None, 'ip -pretty -json addr show %s' % name,
        check_exit_code=[0, 1], namespace=namespace)

    addresses = []
    for elem in _clean_ip_json(stdout):
        for addr_info in elem.get('addr_info', []):
            addresses.append(addr_info['local'])

    LOG.with_fields({
        'namespace': namespace,
        'device': name,
        'addresses': addresses
    }).debug('Found addresses')
    return addresses


def get_interface_statistics(name, namespace=None):
    stdout, stderr = process.execute(
        None, 'ip -s -pretty -json link show %s' % name,
        check_exit_code=[0, 1], namespace=namespace,
        suppress_command_logging=True)

    if not stdout:
        raise exceptions.NoInterfaceStatistics(
            'No statistics for interface %s in namespace %s (%s)'
            % (name, namespace, stderr))

    try:
        stats = _clean_ip_json(stdout)
        return stats[0].get('stats64')
    except IndexError:
        raise exceptions.NoInterfaceStatistics(
            'No statistics for interface %s in namespace %s (%s)'
            % (name, namespace, stderr))


def get_interface_mtus(namespace=None):
    stdout, _ = process.execute(
        None, 'ip -pretty -json link show',
        check_exit_code=[0, 1], namespace=namespace,
        suppress_command_logging=True)

    for elem in _clean_ip_json(stdout):
        yield elem['ifname'], elem['mtu']


def get_interface_mtu(interface, namespace=None):
    stdout, _ = process.execute(
        None, 'ip -pretty -json link show %s' % interface,
        check_exit_code=[0, 1], namespace=namespace,
        suppress_command_logging=True)

    for elem in _clean_ip_json(stdout):
        return elem['mtu']


def get_default_routes(namespace):
    stdout, _ = process.execute(
        None,  'ip route list default', namespace=namespace)

    if not stdout:
        return []

    routes = []
    for line in stdout.split('\n'):
        elems = line.split(' ')
        if len(elems) > 3 and elems[2] not in routes:
            routes.append(elems[2])
    return routes


def get_safe_interface_name(interface):
    if len(interface) > 15:
        interface = interface[:15]
    return interface


def create_interface(interface, interface_type, extra, mtu=None):
    if not mtu:
        mtu = config.MAX_HYPERVISOR_MTU - 50

    interface = get_safe_interface_name(interface)
    process.execute(None,
                    'ip link add %(interface)s mtu %(mtu)s '
                    'type %(interface_type)s %(extra)s'
                    % {'interface': interface,
                       'interface_type': interface_type,
                       'mtu': mtu,
                       'extra': extra})


def nat_rules_for_ipblock(ipblock):
    out, _ = process.execute(None, 'iptables -w 10 -t nat -L POSTROUTING -n -v')
    # Output looks like this:
    # Chain POSTROUTING (policy ACCEPT 199 packets, 18189 bytes)
    # pkts bytes target     prot opt in     out     source               destination
    #   23  1736 MASQUERADE  all  --  *      ens4    192.168.242.0/24     0.0.0.0/0

    for line in out.split('\n'):
        if line.find(str(ipblock)) != -1:
            return True

    return False


def discover_interfaces():
    mac_to_iface = {
        '00:00:00:00:00:00': 'broadcast'
    }
    iface_to_mac = {}
    vxid_to_mac = {}

    iface_name = None
    iface_name_re = re.compile('^[0-9]+: ([^:]+): <')

    link_ether = None
    link_ether_re = re.compile('^    link/ether (.*) brd .*')

    stdout, _ = process.execute(None, 'ip addr list')
    for line in stdout.split('\n'):
        line = line.rstrip()

        m = iface_name_re.match(line)
        if m:
            iface_name = m.group(1)
            continue

        m = link_ether_re.match(line)
        if m:
            link_ether = m.group(1)
            mac_to_iface[link_ether] = iface_name
            iface_to_mac[iface_name] = link_ether

            if iface_name.startswith('vxlan-'):
                vxid = int(iface_name.split('-')[1], 16)
                vxid_to_mac[vxid] = link_ether

    return mac_to_iface, iface_to_mac, vxid_to_mac


def random_macaddr():
    return '02:00:00:{:02x}:{:02x}:{:02x}'.format(random.randint(0, 255),
                                                  random.randint(0, 255),
                                                  random.randint(0, 255))


def add_address_to_interface(namespace, address, netmask, device):
    # Adding an address to an interface can sometimes require waiting briefly
    # to ensure the address appears. This is a wrapper which does all that
    # for you. This used to error if repeated attempts fail, but that's so
    # common its not useful. This needs revisiting.
    log = LOG.with_fields({
        'namespace': namespace,
        'address': address,
        'netmask': netmask,
        'device': device
    })

    def _add_address(namespace, address, netmask, device):
        if not address:
            raise exceptions.InvalidAddress(address)

        try:
            process.execute(
                None,
                'ip addr add {address}/{netmask} dev {device}'.format(
                    address=address,
                    netmask=netmask,
                    device=device
                ),
                namespace=namespace)
            process.execute(None, 'ip link set %s up' % device, namespace=namespace)

        except processutils.ProcessExecutionError as e:
            if e.stderr.rstrip() != 'RTNETLINK answers: File exists':
                raise e

    attempts = 0
    _add_address(namespace, address, netmask, device)
    while address not in list(get_interface_addresses(device, namespace=namespace)):
        time.sleep(0.5)
        attempts += 1
        if attempts == 5:
            log.with_fields({'attempt': attempts}).warning(
                'Repeated failures to add address to device')
            return

        _add_address(namespace, address, netmask, device)
