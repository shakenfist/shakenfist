import ipaddress
import json
import os
import random
import re
import time
from typing import Any, Iterator

from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.exceptions import InvalidAddress
from shakenfist.exceptions import NoInterfaceStatistics
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.util import concurrency
# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


LOG, _ = logs.setup(__name__)


def _clean_ip_json(data: str | None) -> list[dict[str, Any]]:
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


# Matches the all-zeroes flood entries a VXLAN interface uses for
# broadcast / unknown-unicast forwarding in a unicast mesh. This mirrors
# MESH_RE in the privexec daemon, which owns the mutating side of the
# mesh (``bridge fdb append`` / ``bridge fdb del``).
MESH_FLOOD_RE = re.compile(r'00:00:00:00:00:00 dst (.*) self permanent')


def discover_mesh_flood_ips(vx_interface: str) -> set[str] | None:
    """Return the flood destination IPs in a VXLAN interface's FDB.

    Returns None if the interface does not exist on this node (``bridge
    fdb show`` exits non-zero with "Cannot find device"), which callers
    should treat as "nothing to audit" -- interface existence is
    ``Network.is_created``'s problem, not the mesh's.
    """
    try:
        stdout, _ = concurrency.execute(
            f'bridge fdb show brport {vx_interface}',
            suppress_command_logging=True)
    except ProcessExecutionError:
        return None

    ips = set()
    for line in stdout.split('\n'):
        m = MESH_FLOOD_RE.match(line)
        if m:
            ips.add(m.group(1))
    return ips


def check_for_interface(
    name: str, netns: str | None = None, up: bool = False
) -> bool:
    log = LOG.with_fields({
        'name': name,
        'netns': netns
    }
    )
    if netns:
        if not os.path.exists('/var/run/netns/%s' % str(netns)):
            log.info('Interface is down, namespace missing')
            return False

    stdout, stderr = concurrency.execute(
        'ip -pretty -json link show %s' % name,
        check_exit_code=[0, 1], netns=netns,
        suppress_command_logging=True)

    if stderr.rstrip('\n').endswith(' does not exist.'):
        log.info('Interface is down, interface missing')
        return False

    if up:
        j = _clean_ip_json(stdout)
        if 'UP' not in j[0]['flags']:
            log.info('Interface is down, UP flag is missing')
            return False

    return True


# iproute2 reports a missing device differently depending on whether it
# is the subject of the command or a filter argument to it:
#
#   $ ip -pretty -json link show banana0
#   Device "banana0" does not exist.
#   EXIT=1
#   $ ip -pretty -json link show master banana0
#   Error: argument "banana0" is wrong: Device does not exist
#   EXIT=255
#
# check_for_interface() only ever asks the first question so it can match
# the first form inline, but get_bridge_members() asks the second, which
# both fails the check_exit_code allowlist and words the message
# differently. Match either form.
_DEVICE_MISSING_RE = re.compile(
    r'(Device "[^"]*" does not exist\.|Device does not exist)\s*$')


def get_bridge_members(name: str, netns: str | None = None) -> list[str]:
    """The names of the interfaces currently enslaved to a bridge.

    An empty list is returned both when the bridge has no members and
    when the bridge does not exist, because "nothing is attached to it"
    is the honest answer in both cases. Errors other than a missing
    bridge are raised, so a caller which is about to delete something
    can tell "no members" from "could not ask".
    """
    try:
        stdout, stderr = concurrency.execute(
            'ip -pretty -json link show master %s' % name,
            check_exit_code=[0, 1], netns=netns,
            suppress_command_logging=True)
    except ProcessExecutionError as e:
        # A missing bridge exits 255, which is outside the allowlist
        # above, so it arrives here rather than as a return value. 255 is
        # iproute2's catch-all failure code, so match on the message
        # rather than widening check_exit_code -- otherwise every other
        # way ip can fail would produce an empty member list, and an
        # empty member list is what authorises deleting devices.
        if _DEVICE_MISSING_RE.search(e.stderr or ''):
            return []
        raise

    if stderr and _DEVICE_MISSING_RE.search(stderr):
        return []

    return [elem['ifname'] for elem in _clean_ip_json(stdout)
            if elem.get('ifname')]


def get_interface_addresses(name: str, netns: str | None = None) -> list[str]:
    stdout, _ = concurrency.execute(
        'ip -pretty -json addr show %s' % name,
        check_exit_code=[0, 1], netns=netns)

    addresses = []
    for elem in _clean_ip_json(stdout):
        for addr_info in elem.get('addr_info', []):
            addresses.append(addr_info['local'])
    return addresses


def get_interface_statistics(
    name: str, netns: str | None = None
) -> dict[str, Any] | None:
    stdout, stderr = concurrency.execute(
        'ip -s -pretty -json link show %s' % name,
        check_exit_code=[0, 1], netns=netns,
        suppress_command_logging=True)

    if not stdout:
        raise NoInterfaceStatistics(
            'No statistics for interface %s in netns %s (%s)'
            % (name, netns, stderr))

    try:
        stats = _clean_ip_json(stdout)
        return stats[0].get('stats64')
    except IndexError:
        raise NoInterfaceStatistics(
            'No statistics for interface %s in netns %s (%s)'
            % (name, netns, stderr))


def get_interface_mtus(
    netns: str | None = None
) -> Iterator[tuple[str, int]]:
    stdout, _ = concurrency.execute(
        'ip -pretty -json link show',
        check_exit_code=[0, 1], netns=netns,
        suppress_command_logging=True)

    for elem in _clean_ip_json(stdout):
        yield elem['ifname'], elem['mtu']


def get_interface_mtu(interface: str, netns: str | None = None) -> int | None:
    stdout, _ = concurrency.execute(
        'ip -pretty -json link show %s' % interface,
        check_exit_code=[0, 1], netns=netns,
        suppress_command_logging=True)

    for elem in _clean_ip_json(stdout):
        return elem['mtu']
    return None


# What ``ip netns exec`` exits with when the namespace it was asked for
# does not exist ("Cannot open network namespace ...: No such file or
# directory"). It is distinct from the exit codes ip itself uses for the
# command it was asked to run -- 2 for "no such route", for example --
# so a caller which wants to tolerate a vanished namespace can tell that
# apart from the command having failed inside a namespace which is
# there. Verified against iproute2 rather than assumed.
NETNS_MISSING_EXIT_CODE = 255


def get_default_routes(netns: str | None) -> list[str]:
    stdout, _ = concurrency.execute(
        'ip route list default', netns=netns)

    if not stdout:
        return []

    routes = []
    for line in stdout.split('\n'):
        elems = line.split(' ')
        if len(elems) > 3 and elems[2] not in routes:
            routes.append(elems[2])
    return routes


def get_host_routes(netns: str | None, device: str) -> set[str]:
    """Return the host route destinations pointing at a device.

    Host routes list without a prefix length ("192.168.15.29 dev ..."),
    while a connected network route lists with one, so the presence of a
    "/" is what separates the routes somebody added from the ones the
    kernel derived from an address. Used to tell whether a routed
    address's route is actually installed inside a network namespace.
    """
    stdout, _ = concurrency.execute(
        f'ip route list dev {device}', netns=netns)

    destinations = set()
    for line in stdout.split('\n'):
        candidate = line.split(' ')[0]
        if '/' in candidate:
            # A connected route the kernel derived from an address on
            # the device, not one somebody added.
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            # A blank line, "default", or a route type keyword such as
            # "unreachable" or "blackhole". None of them is a host
            # route destination, and the caller asked for destinations.
            continue
        destinations.add(candidate)
    return destinations


def check_for_iptables_rule(netns: str | None, table: str,
                            rule: list[str]) -> bool:
    """Is an iptables rule present, optionally inside a namespace?

    ``rule`` is the chain name followed by the match and target
    arguments, as ``shakenfist.util.iptables`` builds them.

    This asks iptables with ``-C`` rather than parsing ``-S`` output,
    because iptables normalises what it prints: a rule written with a
    dotted quad netmask -- which is how the network object holds one --
    lists back as a prefix length, so a textual comparison would report
    a rule which is right there as missing. Anything which stops the
    check running at all, a namespace which is not there included,
    counts as absent: the repair for both is the same rebuild.

    The elements of ``rule`` are joined with spaces and interpolated
    into a command string which sf-privexec runs as root through a
    shell, so they must never carry user supplied data. Today's callers
    build rules from ``shakenfist.util.iptables``, whose only inputs are
    an IPAM derived address and netmask and a hex formatted vxid; a
    caller which wants to check something a user chose needs to quote it
    first.
    """
    try:
        concurrency.execute(
            'iptables -w 10 -t {} -C {}'.format(table, ' '.join(rule)),
            netns=netns)
        return True
    except ProcessExecutionError:
        return False


def add_default_route(netns: str, router: str) -> None:
    try:
        concurrency.execute(
            f'route add default gw {router}', netns=netns)
    except ProcessExecutionError as e:
        if e.stderr != 'SIOCADDRT: File exists\n':
            raise e


def delete_default_route(netns: str, router: str) -> None:
    concurrency.execute(
        f'route del default gw {router}', netns=netns)


def get_safe_interface_name(interface: str) -> str:
    if len(interface) > 15:
        interface = interface[:15]
    return interface


def _create_interface_inner(
    interface: str, interface_type: str, extra: str, mtu: int
) -> bool:
    try:
        concurrency.execute(
            'ip link add %(interface)s mtu %(mtu)s '
            'type %(interface_type)s %(extra)s' % {
                'interface': interface,
                'interface_type': interface_type,
                'mtu': mtu,
                'extra': extra
            })
        return True

    except ProcessExecutionError as e:
        if e.stderr != 'RTNETLINK answers: File exists\n':
            raise e

        # If the interface exists we don't return true here because it likely
        # means we're racing another thread.

    return False


def create_interface(
    interface: str, interface_type: str, extra: str, mtu: int | None = None
) -> None:
    if not mtu:
        mtu = config.MAX_HYPERVISOR_MTU - 50

    interface = get_safe_interface_name(interface)
    attempts = 0
    while attempts < 3:
        if _create_interface_inner(interface, interface_type, extra, mtu):
            return
        time.sleep(0.2)
        attempts += 1


def discover_interfaces() -> tuple[
    dict[str, str | None], dict[str | None, str], dict[int, str]
]:
    mac_to_iface = {
        '00:00:00:00:00:00': 'broadcast'
    }
    iface_to_mac = {}
    vxid_to_mac = {}

    iface_name = None
    iface_name_re = re.compile('^[0-9]+: ([^:]+): <')

    link_ether = None
    link_ether_re = re.compile('^    link/ether (.*) brd .*')

    stdout, _ = concurrency.execute('ip addr list')
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


def random_macaddr() -> str:
    b1 = random.randint(0, 255)
    b2 = random.randint(0, 255)
    b3 = random.randint(0, 255)
    return f'02:00:00:{b1:02x}:{b2:02x}:{b3:02x}'


def add_address_to_interface(
    netns: str, address: str | None, netmask: int, device: str
) -> None:
    # Adding an address to an interface can sometimes require waiting briefly
    # to ensure the address appears. This is a wrapper which does all that
    # for you. This used to error if repeated attempts fail, but that's so
    # common its not useful. This needs revisiting.
    log = LOG.with_fields({
        'netns': netns,
        'address': address,
        'netmask': netmask,
        'device': device
    })

    def _add_address(
        netns: str, address: str | None, netmask: int, device: str
    ) -> None:
        if not address:
            raise InvalidAddress(address)

        try:
            concurrency.execute(
                f'ip addr add {address}/{netmask} dev {device}',
                netns=netns)
            concurrency.execute(
                f'ip link set {device} up', netns=netns)

        except ProcessExecutionError as e:
            if e.stderr.rstrip() != 'RTNETLINK answers: File exists':
                raise e

    attempts = 0
    _add_address(netns, address, netmask, device)
    while address not in list(get_interface_addresses(device, netns=netns)):
        time.sleep(0.5)
        attempts += 1
        if attempts == 5:
            log.with_fields({'attempt': attempts}).warning(
                'Repeated failures to add address to device')
            return

        _add_address(netns, address, netmask, device)
