import json
import os
import re
import shutil
import subprocess
import sys
import time

from shakenfist_utilities import logs                    # noreorder

from shakenfist.config import config
from shakenfist.exceptions import ListingInterfaceAddressesFailed


LOG, _ = logs.setup(__name__)
CACHED_EXECUTABLES = {}


def locate_command(command):
    if command not in CACHED_EXECUTABLES:
        command_path = shutil.which(command)
        if not command_path:
            LOG.error(f'Cannot find {command} command in path')
            sys.exit(1)
        CACHED_EXECUTABLES[command] = command_path

    return CACHED_EXECUTABLES[command]


def command_helper(*command, failure_is_error=True):
    LOG.with_fields({'command': command}).debug('Executing command')
    obj = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, close_fds=True)

    stdout, stderr = obj.communicate(None, timeout=None)
    log = LOG.with_fields({
            'command': command,
            'stdout': stdout,
            'stderr': stderr,
            'exit_code': obj.returncode
    })
    if failure_is_error and obj.returncode != 0:
        log.error('Command failed')
    else:
        log.debug('Command executed')
    return stdout.decode(), stderr.decode(), obj.returncode


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


def check_for_interface(interface, namespace=None, up=False):
    if namespace:
        if not os.path.exists('/var/run/netns/%s' % namespace):
            return False

        command = [locate_command('ip'), 'netns', 'exec', namespace]
    else:
        command = []

    command.extend([
        locate_command('ip'), '-pretty', '-json', 'link', 'show', interface
    ])

    stdout, stderr, returncode = command_helper(
        *command, failure_is_error=False)

    if stderr.rstrip('\n').endswith(' does not exist.'):
        return False

    if returncode != 0:
        return False

    if up:
        j = _clean_ip_json(stdout)
        if 'UP' not in j[0]['flags']:
            return False

    return True


def _get_safe_interface_name(interface):
    return interface[:15]


def create_interface(interface, interface_type, extra, mtu=None,
                     inner_namespace=None):
    """Create an interface, returning (success, error_detail).

    error_detail is the empty string on success. On failure it names the
    step which failed and carries that command's stderr, which includes
    the kernel's errno text -- for example "No such file or directory"
    when inner_namespace was torn down underneath us. Swallowing that
    detail left CREATE_INTERFACE_FAILED undiagnosable (issue 3608).
    """
    interface = _get_safe_interface_name(interface)
    if check_for_interface(interface):
        return True, ''

    if not mtu:
        mtu = config.MAX_HYPERVISOR_MTU - 50

    command = [
        locate_command('ip'), 'link', 'add', interface, 'mtu', str(mtu),
        'type', interface_type
    ]
    if extra:
        command.extend(extra)

    attempts = 0
    while True:
        last_attempt = attempts == 3

        _, stderr, returncode = command_helper(
            *command, failure_is_error=last_attempt)
        if returncode == 0:
            break
        if last_attempt:
            return False, (
                f'ip link add exited {returncode}: {stderr.strip()}')

        time.sleep(0.2)
        attempts += 1

    if inner_namespace:
        command = [
            locate_command('ip'), 'link', 'set', f'{interface}-i',
            'netns', inner_namespace
        ]
        _, stderr, returncode = command_helper(*command)
        if returncode != 0:
            return False, (
                f'ip link set netns {inner_namespace} exited '
                f'{returncode}: {stderr.strip()}')

    return True, ''


def create_vx_interface(vx_interface, vx_id, vx_bridge, mesh_interface):
    log = LOG.with_fields({
        'vx_id': vx_id,
        'vx_interface': vx_interface,
        'vx_bridge': vx_bridge,
        'mesh_interface': mesh_interface,
    })
    log.debug('Ensuring vxlan interface and bridge exist')

    if not check_for_interface(vx_interface):
        log.debug('vxlan interface absent, creating')
        rc, error = create_interface(
            vx_interface, 'vxlan',
            ['id', str(vx_id), 'dev', str(mesh_interface), 'dstport', '0']
        )
        log.with_fields({'create_interface_rc': rc,
                         'create_interface_error': error}).debug(
            'create_interface returned for vxlan interface')

        command = ['sysctl', '-w',
                   f'net.ipv4.conf.{vx_interface}.arp_notify=1']
        _, _, returncode = command_helper(*command)
        if returncode != 0:
            return False
    else:
        log.debug('vxlan interface already present')

    if not check_for_interface(vx_bridge):
        log.debug('vxlan bridge absent, creating')
        rc, error = create_interface(vx_bridge, 'bridge', [])
        log.with_fields({'create_interface_rc': rc,
                         'create_interface_error': error}).debug(
            'create_interface returned for vxlan bridge')

        command = ['sysctl', '-w', f'net.ipv4.conf.{vx_bridge}.arp_notify=1']
        _, _, returncode = command_helper(*command)
        if returncode != 0:
            return False

        command = ['brctl', 'setfd', str(vx_bridge), '0']
        _, _, returncode = command_helper(*command)
        if returncode != 0:
            return False

        command = ['brctl', 'stp', str(vx_bridge), 'off']
        _, _, returncode = command_helper(*command)
        if returncode != 0:
            return False

        command = ['brctl', 'setageing', str(vx_bridge), '0']
        _, _, returncode = command_helper(*command)
        if returncode != 0:
            return False
    else:
        log.debug('vxlan bridge already present')

    # Enslavement and admin state are converged unconditionally rather
    # than only when the bridge was just created: this function can be
    # entered with any combination of the pair already existing (for
    # example the vxlan interface deleted by a racing teardown while
    # the bridge survived). In that state the recreated interface was
    # previously left unenslaved and down -- mesh FDB entries would
    # render onto it and the drift auditor would report the mesh
    # healthy, while the bridge silently had no vxlan member and the
    # overlay was dark. All three commands are idempotent.
    command = [
        'ip', 'link', 'set', str(vx_interface), 'master', str(vx_bridge)
    ]
    _, _, returncode = command_helper(*command)
    if returncode != 0:
        return False

    command = ['ip', 'link', 'set', str(vx_interface), 'up']
    _, _, returncode = command_helper(*command)
    if returncode != 0:
        return False

    command = ['ip', 'link', 'set', str(vx_bridge), 'up']
    _, _, returncode = command_helper(*command)
    if returncode != 0:
        return False

    log.debug('vxlan interface and bridge ready')
    return True


def get_interface_addresses(interface, namespace=None):
    if namespace:
        command = [locate_command('ip'), 'netns', 'exec', namespace]
    else:
        command = []

    command.extend([
        locate_command('ip'), '-pretty', '-json', 'addr', 'show', interface
    ])
    stdout, stderr, returncode = command_helper(*command)
    if returncode not in [0, 1]:
        raise ListingInterfaceAddressesFailed(stderr)

    addresses = []
    for elem in _clean_ip_json(stdout):
        for addr_info in elem.get('addr_info', []):
            addresses.append(addr_info['local'])
    return addresses


def add_address_to_interface(interface, namespace, address, netmask):
    if namespace:
        command = [locate_command('ip'), 'netns', 'exec', namespace]
    else:
        command = []

    command.extend([
        locate_command('ip'), 'addr', 'add', f'{address}/{netmask}',
        'dev', interface
    ])

    attempts = 0
    while True:
        _, stderr, returncode = command_helper(*command)
        if returncode == 0:
            return True
        if stderr.find('RTNETLINK answers: File exists') != -1:
            return True

        if attempts > 5:
            return False

        time.sleep(0.5)
        attempts += 1


def create_network_namespace(namespace):
    if not os.path.exists('/var/run/netns/%s' % namespace):
        command = ['ip', 'netns', 'add', namespace]
        _, stderr, returncode = command_helper(*command)
        if returncode != 0:
            r = re.compile(
                r'Cannot create namespace file ".*": File exists\n')
            m = r.match(stderr)
            if not m:
                return False

    return True
