# Copyright 2020 Michael Still

import datetime
import json
import logging
import randmac
import random
import time
import uuid

from sqlalchemy import create_engine
from sqlalchemy import BLOB, Boolean, Column, Float, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import exc, scoped_session, sessionmaker
from sqlalchemy.sql.expression import func


from shakenfist import config
from shakenfist import etcd
from shakenfist import ipmanager


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


Base = declarative_base()
ENGINE = create_engine(config.parsed.get('SQL_URL'))
SESSIONMAKER = sessionmaker(bind=ENGINE)
SESSION = scoped_session(SESSIONMAKER)


def ensure_valid_session():
    global SESSION
    global SESSIONMAKER

    try:
        see_this_node()
        return
    except:
        SESSION = scoped_session(SESSIONMAKER)
        see_this_node()


def see_this_node():
    etcd.put(
        'node', None,
        config.parsed.get('NODE_NAME'),
        {
            'fqdn': config.parsed.get('NODE_NAME'),
            'ip': config.parsed.get('NODE_IP'),
            'lastseen': time.time(),
        },
        ttl=120)


def get_node_ips():
    see_this_node()
    for value in etcd.get_all('node', None):
        yield value['ip']


def get_node(fqdn):
    see_this_node()
    return etcd.get('node', None, fqdn)


def get_nodes():
    see_this_node()
    return etcd.get_all('node', None)


def get_network(network_uuid):
    see_this_node()
    return etcd.get('network', None, network_uuid)


def get_networks(all=False):
    see_this_node()
    for i in etcd.get_all('network', None):
        if all:
            yield i
        else:
            if i['network_uuid'] == 'floating':
                continue
            if not i['state'] in ['deleted', 'error']:
                yield i


def allocate_network(netblock, provide_dhcp=True, provide_nat=False, name=None):
    see_this_node()

    netid = str(uuid.uuid4())
    ipm = ipmanager.NetBlock(netblock)
    etcd.put('ipmanager', None, netid, ipm.save())

    with etcd.get_lock('vxlan') as _:
        vxid = 1
        while etcd.get('vxlan', None, vxid):
            vxid += 1

        etcd.put('vxlan', None, vxid,
                 {
                     'network_uuid': netid
                 })

    d = {
        'uuid': netid,
        'vxid': vxid,
        'netblock': netblock,
        'provide_dhcp': provide_dhcp,
        'provide_nat': provide_nat,
        'owner': None,
        'floating_gateway': None,
        'name': name,
        'state': 'initial',
        'state_updated': time.time()
    }
    etcd.put('network', None, netid, d)
    return d


def update_network_state(network_uuid, state):
    see_this_node()
    n = get_network(network_uuid)
    n['state'] = state
    n['state_updated'] = time.time()
    etcd.put('network', None, network_uuid, n)


def get_stale_networks(delay):
    see_this_node()
    for n in etcd.get_all('network', None):
        if n['state'] in ['deleted', 'error']:
            if time.time() - n['state_updated'] > delay:
                yield n


def hard_delete_network(network_uuid):
    see_this_node()
    etcd.delete('network', None, network_uuid)


def create_floating_network(netblock):
    see_this_node()
    ipm = ipmanager.NetBlock(netblock)
    etcd.put('ipmanager', None, 'floating', ipm.save())
    etcd.put('network', None, 'floating',
             {
                 'uuid': 'floating',
                 'vxid': 0,
                 'netblock': netblock,
                 'provide_dhcp': False,
                 'provide_nat': False,
                 'owner': None,
                 'floating_gateway': None,
                 'name': 'floating',
                 'state': 'initial',
                 'state_updated': time.time()
             })


def get_ipmanager(network_uuid):
    see_this_node()
    return ipmanager.from_db(etcd.get('ipmanager', None, network_uuid))


def persist_ipmanager(network_uuid, data):
    see_this_node()
    etcd.put('ipmanager', None, network_uuid, data)


def persist_floating_gateway(network_uuid, gateway):
    see_this_node()
    n = get_network(network_uuid)
    n['floating_gateway'] = gateway
    etcd.put('network', None, network_uuid, n)


def get_instance(instance_uuid):
    see_this_node()
    return etcd.get('instance', None, instance_uuid)


def get_instances(only_node=None, all=False):
    see_this_node()
    for i in etcd.get_all('instance', None):
        if not all:
            if not i['state'] in ['deleted', 'error']:
                yield i
        else:
            yield i


def persist_block_devices(instance_uuid, block_devices):
    see_this_node()
    i = get_instance(instance_uuid)
    i['block_devices'] = block_devices
    etcd.put('instance', None, instance_uuid, i)


def create_instance(instance_uuid, name, cpus, memory_mb, disk_spec, ssh_key, user_data):
    see_this_node()
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
                 'state_updated': time.time()
    }
    etcd.put('instance', None, instance_uuid, d)
    return d


def place_instance(instance_uuid, node):
    see_this_node()
    i = get_instance(instance_uuid)
    i['node'] = node
    etcd.put('instance', None, instance_uuid, i)


def update_instance_state(instance_uuid, state):
    see_this_node()
    i = get_instance(instance_uuid)
    i['state'] = state
    i['state_updated'] = time.time()
    etcd.put('instance', None, instance_uuid, i)


def hard_delete_instance(instance_uuid):
    see_this_node()
    i = get_instance(instance_uuid)
    free_console_port(i['console_port'])
    free_console_port(i['vdi_port'])
    etcd.delete('instance', None, instance_uuid)


def get_stale_instances(delay):
    see_this_node()
    for i in etcd.get_all('instance', None):
        if i['state'] in ['deleted', 'error']:
            if time.time() - i['state_updated'] > delay:
                yield i


def create_network_interface(interface_uuid, netdesc, instance_uuid, order):
    see_this_node()
    if not 'macaddress' in netdesc or not netdesc['macaddress']:
        with etcd.get_lock('macaddress', ttl=120) as _:
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


def hard_delete_network_interface(interface_uuid):
    see_this_node()
    d = etcd.get('networkinterface', None, interface_uuid)
    etcd.delete('macaddress', None, d['macaddr'])
    etcd.delete('networkinterface', None, interface_uuid)


def get_instance_interfaces(instance_uuid):
    see_this_node()
    for ni in etcd.get_all('networkinterface', None):
        if ni['state'] == 'deleted':
            continue
        if ni['instance_uuid'] == instance_uuid:
            yield ni


def get_network_interfaces(network_uuid):
    see_this_node()
    for ni in etcd.get_all('networkinterface', None):
        if ni['state'] == 'deleted':
            continue
        if ni['network_uuid'] == network_uuid:
            yield ni


def get_interface(interface_uuid):
    see_this_node()
    return etcd.get('networkinterface', None, interface_uuid)


def update_network_interface_state(interface_uuid, state):
    see_this_node
    ni = get_interface(interface_uuid)
    ni['state'] = state
    ni['state_updated'] = time.time()
    etcd.put('networkinterface', None, interface_uuid, ni)


def add_floating_to_interface(interface_uuid, addr):
    see_this_node
    ni = get_interface(interface_uuid)
    ni['floating'] = addr
    etcd.put('networkinterface', None, interface_uuid, ni)


def remove_floating_from_interface(interface_uuid):
    see_this_node
    ni = get_interface(interface_uuid)
    ni['floating'] = None
    etcd.put('networkinterface', None, interface_uuid, ni)


def create_snapshot(snapshot_uuid, device, instance_uuid, created):
    see_this_node()
    etcd.put(
        'snapshot', instance_uuid, created,
        {
            'uuid': snapshot_uuid,
            'device': device,
            'instance_uuid': instance_uuid,
            'created': created
        })


def get_instance_snapshots(instance_uuid):
    see_this_node()
    for m in etcd.get_all('snapshot', instance_uuid,
                          sort_order='ascend'):
        yield m


def add_event(object_type, object_uuid, operation, phase, duration, message):
    see_this_node()
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
    see_this_node()
    for m in etcd.get_all('event/%s' % object_type, object_uuid,
                          sort_order='ascend'):
        yield m


def update_metrics_bulk(metrics):
    see_this_node()
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
    see_this_node()
    d = etcd.get('metrics', fqdn, None)
    return d['metrics']


def allocate_console_port(instance_uuid):
    see_this_node()
    node = config.parsed.get('NODE_NAME')
    with etcd.get_lock('console/%s' % node) as _:
        consumed = []
        for value in etcd.get_all('console', node):
            consumed.append(value['port'])

        port = random.randint(30000, 50000)
        while port in consumed:
            port = random.randint(30000, 50000)

        etcd.put(
            'console', node, port,
            {
                'instance_uuid': instance_uuid,
                'port': port,
            })
        return port


def free_console_port(port):
    see_this_node()
    node = config.parsed.get('NODE_NAME')
    with etcd.get_lock('console/%s' % node) as _:
        etcd.delete('console', node, str(port))
