import os
import random
import re

# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, logutils, and specific
# other util modules.
from shakenfist.config import config
from shakenfist import logutil
from shakenfist.util import process


LOG, _ = logutil.setup(__name__)


def is_network_node():
    return config.NODE_MESH_IP == config.NETWORK_NODE_IP


def check_for_interface(name, namespace=None, up=False):
    in_netns = ''
    if namespace:
        if not os.path.exists('/var/run/netns/%s' % namespace):
            return False

        in_netns = 'ip netns exec %s ' % namespace

    stdout, stderr = process.execute(
        None, '%sip link show %s' % (in_netns, name),
        check_exit_code=[0, 1])

    if stderr.rstrip('\n').endswith(' does not exist.'):
        return False

    if up:
        return bool(re.match(r'.*[<,]UP[,>].*', stdout))

    return True


def get_interface_addresses(namespace, name):
    in_namespace = ''
    if namespace:
        in_namespace = 'ip netns exec %s ' % namespace

    stdout, _ = process.execute(None,
                                '%(in_namespace)sip addr show %(name)s'
                                % {
                                    'in_namespace': in_namespace,
                                    'name': name
                                },
                                check_exit_code=[0, 1])
    if not stdout:
        return

    inet_re = re.compile(r' +inet (.*)/[0-9]+.*')
    for line in stdout.split('\n'):
        m = inet_re.match(line)
        if m:
            yield m.group(1)

    return


def get_default_routes(namespace):
    in_namespace = ''
    if namespace:
        in_namespace = 'ip netns exec %s ' % namespace

    stdout, _ = process.execute(None,
                                '%(in_namespace)sip route list default'
                                % {
                                    'in_namespace': in_namespace
                                })
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
        orig_interface = interface
        interface = interface[:15]
        LOG.info('Interface name truncated from %s to %s',
                 orig_interface, interface)
    return interface


def create_interface(interface, interface_type, extra):
    interface = get_safe_interface_name(interface)
    process.execute(None,
                    'ip link add %(interface)s type %(interface_type)s %(extra)s'
                    % {'interface': interface,
                       'interface_type': interface_type,
                       'extra': extra})


def nat_rules_for_ipblock(ipblock):
    out, _ = process.execute(None, 'iptables -t nat -L POSTROUTING -n -v')
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
    return '02:00:00:%02x:%02x:%02x' % (random.randint(0, 255),
                                        random.randint(0, 255),
                                        random.randint(0, 255))
