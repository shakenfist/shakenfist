<<<<<<< HEAD
=======
import json
>>>>>>> 3b896f2c (Push removing floating IPs down to privexec.)
import os
import shutil
import subprocess
import sys
import time

from shakenfist_utilities import logs        # noreorder

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
    return stdout, stderr, obj.returncode


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
    log = LOG.with_fields({
        'name': name,
        'namespace': namespace
    })

    if namespace:
        if not os.path.exists('/var/run/netns/%s' % namespace):
            log.info('Interface is down, namespace missing')
            return False

        command = [locate_command('ip'), 'netns', 'exec', namespace]
    else:
        command = []

    command.extend([
        locate_command('ip'), '-pretty', '-json', 'link', 'show', name
    ])

    stdout, stderr, returncode = command_helper(
        *command, failure_is_error=False)
    if returncode != 0:
        return False

    if stderr.rstrip('\n').endswith(' does not exist.'):
        log.info('Interface is down, interface missing')
        return False

    if up:
        j = _clean_ip_json(stdout)
        if 'UP' not in j[0]['flags']:
            log.info('Interface is down, UP flag is missing')
            return False

    return True


def _get_safe_interface_name(interface):
    return interface[:15]


def create_interface(interface, interface_type, extra, mtu=None,
                     inner_namespace=None):
    interface = _get_safe_interface_name(interface)
    if check_for_interface(interface):
        return True

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
        _, _, returncode = command_helper(
            *command, failure_is_error=last_attempt)
        if returncode == 0:
            break
        if last_attempt:
            return False

        time.sleep(0.2)
        attempts += 1

    if inner_namespace:
        _, _, returncode = command_helper(
            locate_command('ip'), 'link', 'set', interface,
            'netns', inner_namespace)
        if returncode != 0:
            return False

    return True


def get_interface_addresses(name, namespace=None):
    if namespace:
        command = [locate_command('ip'), 'netns', 'exec', namespace]
    else:
        command = []

    command.extend([
        locate_command('ip'), '-pretty', '-json', 'addr', 'show', name
    ])
    stdout, stderr, returncode = command_helper(*command)
    if returncode not in [0, 1]:
        raise ListingInterfaceAddressesFailed(stderr)

    addresses = []
    for elem in _clean_ip_json(stdout):
        for addr_info in elem.get('addr_info', []):
            addresses.append(addr_info['local'])
    return addresses


def add_address_to_interface(namespace, address, netmask, device):
    if namespace:
        command = [locate_command('ip'), 'netns', 'exec', namespace]
    else:
        command = []

    command.extend([
        locate_command('ip'), 'addr', 'add', f'{address}/{netmask}',
        'dev', device
    ])

    attempts = 0
    while True:
        _, stderr, returncode = command_helper(*command)
        if returncode == 0:
            return True
        if stderr.decode().find('RTNETLINK answers: File exists') != -1:
            return True
        if attempts > 5:
            return False

        time.sleep(0.5)
        attempts += 1
