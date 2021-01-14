# Copyright 2020 Michael Still

import time
import uuid

from shakenfist.config import config
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import util


ETCD_ATTEMPT_TIMEOUT = 60


LOG, _ = logutil.setup(__name__)


#####################################################################
# Nodes
#####################################################################
def see_this_node():
    etcd.put(
        'node', None, config.NODE_NAME,
        {
            'fqdn': config.NODE_NAME,
            'ip': config.NODE_IP,
            'lastseen': time.time(),
            'version': util.get_version()
        },
        ttl=120)


def get_node_ips():
    for _, value in etcd.get_all('node', None):
        yield value['ip']


def get_node(fqdn, seen_recently=False):
    node = etcd.get('node', None, fqdn)
    if not node:
        return None
    if seen_recently and (time.time() - node.get('lastseen', 0) > 300):
        return None
    return node


def get_nodes(seen_recently=False):
    for _, value in etcd.get_all('node', None):
        if seen_recently and (time.time() - value.get('lastseen', 0) > 300):
            continue
        yield value


def get_network_node():
    for n in get_nodes():
        if n['ip'] == config.NETWORK_NODE_IP:
            return n

#####################################################################
# Locks
#####################################################################


def get_lock(objecttype, subtype, name, ttl=60, timeout=ETCD_ATTEMPT_TIMEOUT,
             relatedobjects=None, log_ctx=LOG, op=None):
    return etcd.get_lock(objecttype, subtype, name, ttl=ttl, timeout=timeout,
                         log_ctx=log_ctx, op=op)


def refresh_lock(lock, relatedobjects=None, log_ctx=LOG):
    if lock:
        etcd.refresh_lock(lock, log_ctx=log_ctx)


def refresh_locks(locks, relatedobjects=None, log_ctx=LOG):
    if locks:
        for lock in locks:
            refresh_lock(lock, log_ctx=log_ctx)


def clear_stale_locks():
    etcd.clear_stale_locks()


def get_existing_locks():
    return etcd.get_existing_locks()


#####################################################################
# IPManagers
#####################################################################


def get_ipmanager(network_uuid):
    ipm = etcd.get('ipmanager', None, network_uuid)
    if not ipm:
        raise Exception('IP Manager not found for network %s' % network_uuid)
    return ipm


def persist_ipmanager(network_uuid, data):
    etcd.put('ipmanager', None, network_uuid, data)


def delete_ipmanager(network_uuid):
    etcd.delete('ipmanager', None, uuid)


#####################################################################
# NetworkInterfaces
#####################################################################


def create_network_interface(interface_uuid, netdesc, instance_uuid, order):
    if 'macaddress' not in netdesc or not netdesc['macaddress']:
        possible_mac = util.random_macaddr()
        mac_iface = {'interface_uuid': interface_uuid}
        while not etcd.create('macaddress', None, possible_mac, mac_iface):
            possible_mac = util.random_macaddr()
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
    for _, n in etcd.get_all('networkinterface', None):
        if n['state'] in ['deleted', 'error']:
            if time.time() - n['state_updated'] > delay:
                yield n


def hard_delete_network_interface(interface_uuid):
    etcd.delete('networkinterface', None, interface_uuid)
    etcd.delete_all('event/networkinterface', interface_uuid)


def get_instance_interfaces(instance_uuid):
    for _, ni in etcd.get_all('networkinterface', None):
        if ni['state'] == 'deleted':
            continue
        if ni['instance_uuid'] == instance_uuid:
            yield ni


def get_network_interfaces(network_uuid):
    for _, ni in etcd.get_all('networkinterface', None):
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

#####################################################################
# Snapshots
#####################################################################


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
    for _, m in etcd.get_all('snapshot', instance_uuid,
                             sort_order='ascend'):
        yield m

#####################################################################
# Events
#####################################################################


def add_event(object_type, object_uuid, operation, phase, duration, message):
    t = time.time()
    LOG.with_fields(
        {
            object_type: object_uuid,
            'fqdn': config.NODE_NAME,
            'operation': operation,
            'phase': phase,
            'duration': duration,
            'message': message
        }).info('Added event')
    etcd.put(
        'event/%s' % object_type, object_uuid, t,
        {
            'timestamp': t,
            'object_type': object_type,
            'object_uuid': object_uuid,
            'fqdn': config.NODE_NAME,
            'operation': operation,
            'phase': phase,
            'duration': duration,
            'message': message
        })


def get_events(object_type, object_uuid):
    for _, m in etcd.get_all('event/%s' % object_type, object_uuid,
                             sort_order='ascend'):
        yield m

#####################################################################
# Metrics
#####################################################################


def update_metrics_bulk(metrics):
    etcd.put(
        'metrics', config.NODE_NAME, None,
        {
            'fqdn': config.NODE_NAME,
            'timestamp': time.time(),
            'metrics': metrics
        },
        ttl=120)


def get_metrics(fqdn):
    d = etcd.get('metrics', fqdn, None)
    if not d:
        return {}
    return d.get('metrics', {})


#####################################################################
# Namespaces
#####################################################################


def list_namespaces():
    for _, value in etcd.get_all('namespace', None):
        yield value


def get_namespace(namespace):
    return etcd.get('namespace', None, namespace)


def persist_namespace(namespace, data):
    etcd.put('namespace', None, namespace, data)


def delete_namespace(namespace):
    etcd.delete('namespace', None, namespace)

#####################################################################
# Metadata
#####################################################################


def get_metadata(object_type, name):
    return etcd.get('metadata', object_type, name)


def persist_metadata(object_type, name, metadata):
    etcd.put('metadata', object_type, name, metadata)


def delete_metadata(object_type, name):
    etcd.delete('metadata', object_type, name)

#####################################################################
# vxid mappings
#####################################################################


def persist_node_vxid_mapping(node, vxid_to_mac):
    etcd.put('vxid_mapping', None, node, vxid_to_mac)


def get_node_vxid_mapping(node):
    etcd.get('vxid_mapping', None, node)

#####################################################################
# Queues
#####################################################################


def enqueue(queuename, workitem):
    etcd.enqueue(queuename, workitem)


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
