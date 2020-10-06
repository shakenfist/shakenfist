# Copyright 2020 Michael Still

import randmac
import random
import socket
import time
import uuid


from shakenfist import config
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import ipmanager
from shakenfist import logutil
from shakenfist import util
from shakenfist.tasks import DeleteInstanceTask

# TODO(andy): Change back to 5 once network bugs fixed
ETCD_ATTEMPT_TIMEOUT = 15


LOG, _ = logutil.setup(__name__)


def see_this_node():
    etcd.put(
        'node', None,
        config.parsed.get('NODE_NAME'),
        {
            'fqdn': config.parsed.get('NODE_NAME'),
            'ip': config.parsed.get('NODE_IP'),
            'lastseen': time.time(),
            'version': util.get_version()
        },
        ttl=120)


def get_lock(objecttype, subtype, name, ttl=60, timeout=ETCD_ATTEMPT_TIMEOUT,
             relatedobjects=None, log_ctx=LOG):
    return etcd.get_lock(objecttype, subtype, name, ttl=ttl, timeout=timeout,
                         log_ctx=log_ctx)


def refresh_lock(lock, relatedobjects=None, log_ctx=LOG):
    if lock:
        etcd.refresh_lock(lock, log_ctx=log_ctx)


def refresh_locks(locks, relatedobjects=None, log_ctx=LOG):
    if locks:
        for lock in locks:
            refresh_lock(lock, log_ctx=log_ctx)


def clear_stale_locks():
    etcd.clear_stale_locks()


def get_node_ips():
    for value in etcd.get_all('node', None):
        yield value['ip']


def get_node(fqdn):
    return etcd.get('node', None, fqdn)


def get_nodes():
    return etcd.get_all('node', None)


def get_network_node():
    for n in get_nodes():
        if n['ip'] == config.parsed.get('NETWORK_NODE_IP'):
            return n


def get_network(network_uuid):
    return etcd.get('network', None, network_uuid)


def get_networks(all=False, namespace=None):
    for n in etcd.get_all('network', None):
        if n['uuid'] == 'floating':
            continue
        if not all:
            if n['state'] in ['deleted', 'error']:
                continue
        if namespace:
            if namespace not in [n['namespace'], 'system']:
                continue
        yield n


def allocate_network(netblock, provide_dhcp=True, provide_nat=False, name=None,
                     namespace=None):

    net_id = str(uuid.uuid4())
    ipm = ipmanager.NetBlock(netblock)
    etcd.put('ipmanager', None, net_id, ipm.save())

    with etcd.get_lock('vxlan', None, 'all'):
        vxid = 1
        while etcd.get('vxlan', None, vxid):
            vxid += 1

        etcd.put('vxlan', None, vxid,
                 {
                     'network_uuid': net_id
                 })

    d = {
        'uuid': net_id,
        'vxid': vxid,
        'netblock': netblock,
        'provide_dhcp': provide_dhcp,
        'provide_nat': provide_nat,
        'namespace': namespace,
        'floating_gateway': None,
        'name': name,
        'state': 'initial',
        'state_updated': time.time()
    }
    etcd.put('network', None, net_id, d)
    return d


def update_network_state(network_uuid, state):
    n = get_network(network_uuid)
    n['state'] = state
    n['state_updated'] = time.time()
    etcd.put('network', None, network_uuid, n)

    if state == 'deleted':
        etcd.delete('vxlan', None, n['vxid'])
        etcd.delete('ipmanager', None, n['uuid'])


def get_stale_networks(delay):
    for n in etcd.get_all('network', None):
        if n['state'] in ['deleted', 'error']:
            if time.time() - n['state_updated'] > delay:
                yield n


def hard_delete_network(network_uuid):
    etcd.delete('network', None, network_uuid)
    etcd.delete_all('event/network', network_uuid)
    delete_metadata('network', network_uuid)


def create_floating_network(netblock):
    ipm = ipmanager.NetBlock(netblock)
    etcd.put('ipmanager', None, 'floating', ipm.save())
    etcd.put('network', None, 'floating',
             {
                 'uuid': 'floating',
                 'vxid': 0,
                 'netblock': netblock,
                 'provide_dhcp': False,
                 'provide_nat': False,
                 'namespace': None,
                 'floating_gateway': None,
                 'name': 'floating',
                 'state': 'initial',
                 'state_updated': time.time()
             })


def get_ipmanager(network_uuid):
    ipm = etcd.get('ipmanager', None, network_uuid)
    if not ipm:
        raise Exception('IP Manager not found for network %s' % network_uuid)
    return ipmanager.from_db(ipm)


def persist_ipmanager(network_uuid, data):
    etcd.put('ipmanager', None, network_uuid, data)


def persist_floating_gateway(network_uuid, gateway):
    n = get_network(network_uuid)
    n['floating_gateway'] = gateway
    etcd.put('network', None, network_uuid, n)


def get_instance(instance_uuid):
    i = etcd.get('instance', None, instance_uuid)
    if not i:
        return None

    if 'video' not in i:
        i['video'] = {'model': 'cirrus', 'memory': 16384}
    if 'error_message' not in i:
        i['error_message'] = None
    return i


def get_instances(only_node=None, all=False, namespace=None):
    for i in etcd.get_all('instance', None):
        if only_node and i['node'] != only_node:
            continue
        if not all:
            if i['state'] in ['deleted', 'error']:
                continue
        if namespace:
            if namespace not in [i['namespace'], 'system']:
                continue

        if 'video' not in i:
            i['video'] = {'model': 'cirrus', 'memory': 16384}
        if 'error_message' not in i:
            i['error_message'] = None

        yield i


def persist_block_devices(instance_uuid, block_devices):
    i = get_instance(instance_uuid)
    i['block_devices'] = block_devices
    etcd.put('instance', None, instance_uuid, i)


def create_instance(instance_uuid, name, cpus, memory_mb, disk_spec, ssh_key,
                    user_data, namespace, video, requested_placement):
    d = {
        'uuid': instance_uuid,
        'name': name,
        'cpus': cpus,
        'memory': memory_mb,
        'disk_spec': disk_spec,
        'ssh_key': ssh_key,
        'node': config.parsed.get('NODE_NAME'),
        'console_port': allocate_console_port(instance_uuid),
        'vdi_port': allocate_console_port(instance_uuid),
        'user_data': user_data,
        'block_devices': None,
        'state': 'initial',
        'state_updated': time.time(),
        'namespace': namespace,
        'power_state': 'initial',
        'video': video,
        'node_history': [],
        'error_message': None,
        'requested_placement': None,
        'placement_attempts': 0,
    }
    etcd.put('instance', None, instance_uuid, d)
    return d


def place_instance(instance_uuid, node):
    i = get_instance(instance_uuid)

    # We don't write unchanged things to the database
    if i.get('node') == node:
        return

    i['node'] = node
    i['placement_attempts'] = i.get('placement_attempts', 0) + 1
    etcd.put('instance', None, instance_uuid, i)


def instance_enforced_deletes_increment(instance_uuid):
    i = get_instance(instance_uuid)
    i['enforced_deletes'] = i.get('enforced_deletes', 0) + 1
    etcd.put('instance', None, instance_uuid, i)


def update_instance_state(instance_uuid, state):
    i = get_instance(instance_uuid)

    # We don't write unchanged things to the database
    if i.get('state') == state:
        return

    orig_state = i.get('state', 'unknown')
    i['state'] = state
    i['state_updated'] = time.time()
    etcd.put('instance', None, instance_uuid, i)

    add_event('instance', instance_uuid, 'state changed',
              '%s -> %s' % (orig_state, state), None, None)


def update_instance_power_state(instance_uuid, state):
    i = get_instance(instance_uuid)

    # We don't write unchanged things to the database
    if i.get('power_state') == state:
        return

    # If we are in transition, and its new, then we might
    # not want to update just yet
    state_age = time.time() - i.get('power_state_updated', 0)
    if (i.get('power_state', '').startswith('transition-to-') and
            i.get('power_state_previous') == state and state_age < 70):
        return

    i['power_state_previous'] = i.get('power_state', 'unknown')
    i['power_state'] = state
    i['power_state_updated'] = time.time()
    etcd.put('instance', None, instance_uuid, i)


def update_instance_error_message(instance_uuid, error_message):
    i = get_instance(instance_uuid)
    i['error_message'] = error_message
    etcd.put('instance', None, instance_uuid, i)

    add_event('instance', instance_uuid, 'error message',
              error_message, None, None)


def hard_delete_instance(instance_uuid):
    etcd.delete('instance', None, instance_uuid)
    etcd.delete_all('event/instance', instance_uuid)
    delete_metadata('instance', instance_uuid)


def get_stale_instances(delay):
    for i in etcd.get_all('instance', None):
        if i['state'] in ['deleted', 'error']:
            if time.time() - i['state_updated'] > delay:
                yield i


def create_network_interface(interface_uuid, netdesc, instance_uuid, order):
    if 'macaddress' not in netdesc or not netdesc['macaddress']:
        with etcd.get_lock('macaddress', None, 'all', ttl=120):
            possible_mac = str(randmac.RandMac(
                '00:00:00:00:00:00', False)).lstrip('\'').rstrip('\'')
            while etcd.get('macaddress', None, possible_mac):
                possible_mac = str(randmac.RandMac(
                    '00:00:00:00:00:00', False)).lstrip('\'').rstrip('\'')

            etcd.put('macaddress', None, possible_mac,
                     {
                         'interface_uuid': interface_uuid
                     })
            netdesc['macaddress'] = possible_mac

    etcd.put('networkinterface', None, interface_uuid,
             {
                 'uuid': interface_uuid,
                 'network_uuid': netdesc['network_uuid'],
                 'instance_uuid': instance_uuid,
                 'macaddr': netdesc['macaddress'],
                 'ipv4': netdesc['address'],
                 'order': order,
                 'floating': None,
                 'state': 'initial',
                 'state_updated': time.time(),
                 'model': netdesc['model']
             })


def get_stale_network_interfaces(delay):
    for n in etcd.get_all('networkinterface', None):
        if n['state'] in ['deleted', 'error']:
            if time.time() - n['state_updated'] > delay:
                yield n


def hard_delete_network_interface(interface_uuid):
    etcd.delete('networkinterface', None, interface_uuid)
    etcd.delete_all('event/networkinterface', interface_uuid)


def get_instance_interfaces(instance_uuid):
    for ni in etcd.get_all('networkinterface', None):
        if ni['state'] == 'deleted':
            continue
        if ni['instance_uuid'] == instance_uuid:
            yield ni


def get_network_interfaces(network_uuid):
    for ni in etcd.get_all('networkinterface', None):
        if ni['state'] == 'deleted':
            continue
        if ni['network_uuid'] == network_uuid:
            yield ni


def get_interface(interface_uuid):
    return etcd.get('networkinterface', None, interface_uuid)


def update_network_interface_state(interface_uuid, state):
    ni = get_interface(interface_uuid)
    ni['state'] = state
    ni['state_updated'] = time.time()
    etcd.put('networkinterface', None, interface_uuid, ni)

    if state == 'deleted':
        etcd.delete('macaddress', None, ni['macaddr'])


def add_floating_to_interface(interface_uuid, addr):
    ni = get_interface(interface_uuid)
    ni['floating'] = addr
    etcd.put('networkinterface', None, interface_uuid, ni)


def remove_floating_from_interface(interface_uuid):
    ni = get_interface(interface_uuid)
    ni['floating'] = None
    etcd.put('networkinterface', None, interface_uuid, ni)


def create_snapshot(snapshot_uuid, device, instance_uuid, created):
    etcd.put(
        'snapshot', instance_uuid, created,
        {
            'uuid': snapshot_uuid,
            'device': device,
            'instance_uuid': instance_uuid,
            'created': created
        })


def get_instance_snapshots(instance_uuid):
    for m in etcd.get_all('snapshot', instance_uuid,
                          sort_order='ascend'):
        yield m


def add_event(object_type, object_uuid, operation, phase, duration, message):
    t = time.time()
    etcd.put(
        'event/%s' % object_type, object_uuid, t,
        {
            'timestamp': t,
            'object_type': object_type,
            'object_uuid': object_uuid,
            'fqdn': config.parsed.get('NODE_NAME'),
            'operation': operation,
            'phase': phase,
            'duration': duration,
            'message': message
        })


def get_events(object_type, object_uuid):
    for m in etcd.get_all('event/%s' % object_type, object_uuid,
                          sort_order='ascend'):
        yield m


def update_metrics_bulk(metrics):
    node = config.parsed.get('NODE_NAME')
    etcd.put(
        'metrics', node, None,
        {
            'fqdn': node,
            'timestamp': time.time(),
            'metrics': metrics
        },
        ttl=120)


def get_metrics(fqdn):
    d = etcd.get('metrics', fqdn, None)
    if not d:
        return {}
    return d.get('metrics', {})


def _port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('0.0.0.0', port))  # lgtm
        return True
    except socket.error:
        return False
    finally:
        s.close()


def allocate_console_port(instance_uuid):
    node = config.parsed.get('NODE_NAME')
    with etcd.get_lock('console', None, node):
        consumed = []
        for value in etcd.get_all('console', node):
            consumed.append(value['port'])

        port = random.randint(30000, 50000)
        while port in consumed or not _port_free(port):
            port = random.randint(30000, 50000)

        etcd.put(
            'console', node, port,
            {
                'instance_uuid': instance_uuid,
                'port': port,
            })
        return port


def free_console_port(port):
    etcd.delete('console', config.parsed.get('NODE_NAME'), port)


def list_namespaces():
    return etcd.get_all('namespace', None)


def get_namespace(namespace):
    return etcd.get('namespace', None, namespace)


def persist_namespace(namespace, data):
    etcd.put('namespace', None, namespace, data)


def delete_namespace(namespace):
    etcd.delete('namespace', None, namespace)


def get_metadata(object_type, name):
    return etcd.get('metadata', object_type, name)


def persist_metadata(object_type, name, metadata):
    etcd.put('metadata', object_type, name, metadata)


def delete_metadata(object_type, name):
    etcd.delete('metadata', object_type, name)


def persist_node_vxid_mapping(node, vxid_to_mac):
    etcd.put('vxid_mapping', None, node, vxid_to_mac)


def get_node_vxid_mapping(node):
    etcd.get('vxid_mapping', None, node)


def enqueue(queuename, workitem):
    etcd.enqueue(queuename, workitem)


# TODO(andy): make this a general enqueue now that the task has enforced formatting
# can also move the NODE_NAME to this function
def enqueue_instance_delete(node, instance_uuid, next_state, next_state_message):
    enqueue(node, {
        'tasks': [
            DeleteInstanceTask(instance_uuid, next_state, next_state_message)
            ],
    })


def dequeue(queuename):
    try:
        return etcd.dequeue(queuename)
    except exceptions.LockException:
        # We didn't acquire the lock, we should just try again later. This probably
        # indicates congestion.
        return None, None


def resolve(queuename, jobname):
    etcd.resolve(queuename, jobname)


def get_queue_length(queuename):
    return etcd.get_queue_length(queuename)


def restart_queues():
    etcd.restart_queues()
