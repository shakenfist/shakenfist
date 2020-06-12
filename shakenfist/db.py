# Copyright 2020 Michael Still

import datetime
import json
import logging
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
        })


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


class Network(Base):
    __tablename__ = 'networks'

    uuid = Column(String, primary_key=True)
    vxid = Column(Integer)
    netblock = Column(String)
    provide_dhcp = Column(Boolean)
    provide_nat = Column(Boolean)
    owner = Column(String)
    ipmanager = Column(BLOB)
    floating_gateway = Column(String)
    name = Column(String)
    state = Column(String)
    state_updated = Column(DateTime)

    def __init__(self, network_uuid, vxid, netblock, provide_dhcp, provide_nat,
                 owner, name):
        self.uuid = network_uuid
        self.vxid = vxid
        self.netblock = netblock
        self.provide_dhcp = provide_dhcp
        self.provide_nat = provide_nat
        self.owner = owner
        self.floating_gateway = None
        self.name = name
        self.state = 'initial'
        self.state_updated = datetime.datetime.now()

    def export(self):
        return {
            'uuid': self.uuid,
            'vxid': self.vxid,
            'netblock': self.netblock,
            'provide_dhcp': self.provide_dhcp,
            'provide_nat': self.provide_nat,
            'owner': self.owner,
            'floating_gateway': self.floating_gateway,
            'name': self.name,
            'state': self.state,
            'state_updated': self.state_updated.timestamp()
        }


def get_network(network_uuid):
    ensure_valid_session()

    try:
        query = SESSION.query(Network).filter(
            Network.uuid == network_uuid)

        query = query.filter(Network.state != 'deleted')
        query = query.filter(Network.state != 'error')

        return query.one().export()
    except exc.NoResultFound:
        return None


def get_networks(all=False):
    ensure_valid_session()

    try:
        query = SESSION.query(Network)

        if not all:
            query = query.filter(Network.state != 'deleted')
            query = query.filter(Network.state != 'error')

        for n in query.all():
            if n.uuid != 'floating':
                yield n.export()
    except exc.NoResultFound:
        pass


def allocate_network(netblock, provide_dhcp=True, provide_nat=False, name=None):
    see_this_node()

    netid = str(uuid.uuid4())
    ipm = ipmanager.NetBlock(netblock)
    etcd.put('ipmanager', None, netid, ipm.save())

    ensure_valid_session()

    try:
        for r in SESSION.query(func.max(Network.vxid)).first():
            if r:
                vxid = r + 1
            else:
                vxid = 1

        n = Network(netid, vxid, netblock, provide_dhcp,
                    provide_nat, None, name)
        SESSION.add(n)
        return n.export()
    finally:
        SESSION.commit()


def update_network_state(network_uuid, state):
    ensure_valid_session()

    try:
        i = SESSION.query(Network).filter(
            Network.uuid == network_uuid).one()
        i.state = state
        i.state_updated = datetime.datetime.now()
    finally:
        SESSION.commit()


def get_stale_networks(delay):
    ensure_valid_session()

    horizon = datetime.datetime.now() - datetime.timedelta(seconds=delay)

    for state in ['deleted', 'error']:
        try:
            query = SESSION.query(Network).filter(
                Network.state_updated < horizon).filter(
                    Network.state == state)
            for n in query.all():
                yield n.export()
        except exc.NoResultFound:
            pass


def hard_delete_network(network_uuid):
    ensure_valid_session()

    try:
        for n in SESSION.query(Network).filter(
                Network.uuid == network_uuid).all():
            SESSION.delete(n)
    except exc.NoResultFound:
        return None
    finally:
        SESSION.commit()


def create_floating_network(netblock):
    see_this_node()
    ipm = ipmanager.NetBlock(netblock)
    etcd.put('ipmanager', None, 'floating', ipm.save())

    ensure_valid_session()

    try:
        n = Network('floating', 0, netblock, False, False, None, 'floating')
        SESSION.add(n)
    finally:
        SESSION.commit()


def get_ipmanager(network_uuid):
    see_this_node()
    return ipmanager.from_db(etcd.get('ipmanager', None, network_uuid))


def persist_ipmanager(network_uuid, data):
    see_this_node()
    etcd.put('ipmanager', None, network_uuid, data)


def persist_floating_gateway(network_uuid, gateway):
    ensure_valid_session()

    try:
        n = SESSION.query(Network).filter(Network.uuid == network_uuid).one()
        n.floating_gateway = gateway
    finally:
        SESSION.commit()


class Instance(Base):
    __tablename__ = 'instances'

    uuid = Column(String, primary_key=True)
    name = Column(String)
    cpus = Column(Integer)
    memory = Column(Integer)
    disk_spec = Column(String)
    ssh_key = Column(String)
    node = Column(String)
    console_port = Column(Integer)
    vdi_port = Column(Integer)
    user_data = Column(String)
    block_devices = Column(BLOB)
    state = Column(String)
    state_updated = Column(DateTime)

    def __init__(self, instance_uuid, name, cpus, memory_mb, disk_spec,
                 ssh_key, node, console_port, vdi_port, user_data):
        self.uuid = instance_uuid
        self.name = name
        self.cpus = cpus
        self.memory = memory_mb
        self.disk_spec = disk_spec
        self.ssh_key = ssh_key
        self.node = node
        self.console_port = console_port
        self.vdi_port = vdi_port
        self.user_data = user_data
        self.block_devices = None
        self.state = 'initial'
        self.state_updated = datetime.datetime.now()

    def export(self):
        if self.block_devices:
            block_devices = json.loads(self.block_devices)
        else:
            block_devices = None

        if self.disk_spec:
            disk_spec = json.loads(self.disk_spec)
        else:
            disk_spec = None

        return {
            'uuid': self.uuid,
            'name': self.name,
            'cpus': self.cpus,
            'memory': self.memory,
            'disk_spec': disk_spec,
            'ssh_key': self.ssh_key,
            'node': self.node,
            'console_port': self.console_port,
            'vdi_port': self.vdi_port,
            'user_data': self.user_data,
            'block_devices': block_devices,
            'state': self.state,
            'state_updated': self.state_updated.timestamp()
        }


def get_instance(instance_uuid):
    ensure_valid_session()

    try:
        return SESSION.query(Instance).filter(
            Instance.uuid == instance_uuid).one().export()
    except exc.NoResultFound:
        return None
    finally:
        SESSION.commit()


def get_instances(only_node=None, all=False):
    ensure_valid_session()

    try:
        if only_node:
            query = SESSION.query(Instance).filter(
                Instance.node == only_node)
        else:
            query = SESSION.query(Instance)

        if not all:
            query = query.filter(Instance.state != 'deleted')
            query = query.filter(Instance.state != 'error')

        for i in query.all():
            yield i.export()
    except exc.NoResultFound:
        pass


def persist_block_devices(instance_uuid, block_devices):
    ensure_valid_session()

    try:
        i = SESSION.query(Instance).filter(
            Instance.uuid == instance_uuid).one()
        i.block_devices = json.dumps(block_devices).encode('utf-8')
    finally:
        SESSION.commit()


def create_instance(instance_uuid, name, cpus, memory_mb, disk_spec, ssh_key, user_data):
    ensure_valid_session()

    try:
        console_port = allocate_console_port(instance_uuid)
        vdi_port = allocate_console_port(instance_uuid)
        i = Instance(instance_uuid, name, cpus, memory_mb, disk_spec, ssh_key,
                     config.parsed.get('NODE_NAME'), console_port,
                     vdi_port, user_data)
        SESSION.add(i)
        return i.export()
    finally:
        SESSION.commit()


def place_instance(instance_uuid, node):
    ensure_valid_session()

    try:
        i = SESSION.query(Instance).filter(
            Instance.uuid == instance_uuid).one()
        i.node = node
    finally:
        SESSION.commit()


def update_instance_state(instance_uuid, state):
    ensure_valid_session()

    try:
        i = SESSION.query(Instance).filter(
            Instance.uuid == instance_uuid).one()
        i.state = state
        i.state_updated = datetime.datetime.now()
    finally:
        SESSION.commit()


def hard_delete_instance(instance_uuid):
    ensure_valid_session()

    # TODO(mikal): once instances are in etcd
    # free_console_port(self.console_port)
    # free_console_port(self.vdi_port)

    try:
        for s in SESSION.query(Snapshot).filter(
                Snapshot.instance_uuid == instance_uuid):
            SESSION.delete(s)
        for ni in SESSION.query(NetworkInterface).filter(
                NetworkInterface.instance_uuid == instance_uuid).all():
            SESSION.delete(ni)
        i = SESSION.query(Instance).filter(
            Instance.uuid == instance_uuid).one()
        SESSION.delete(i)
    except exc.NoResultFound:
        return None
    finally:
        SESSION.commit()


def get_stale_instances(delay):
    ensure_valid_session()

    horizon = datetime.datetime.now() - datetime.timedelta(seconds=delay)

    for state in ['deleted', 'error']:
        try:
            query = SESSION.query(Instance).filter(
                Instance.state_updated < horizon).filter(
                    Instance.state == state)
            for i in query.all():
                yield i.export()
        except exc.NoResultFound:
            pass


class NetworkInterface(Base):
    __tablename__ = 'network_interfaces'

    uuid = Column(String, primary_key=True)
    network_uuid = Column(String)
    instance_uuid = Column(String)
    macaddr = Column(String)
    ipv4 = Column(String)
    order = Column(Integer)
    floating = Column(String)
    state = Column(String)
    state_updated = Column(DateTime)
    model = Column(String)

    def __init__(self, interface_uuid, network_uuid, instance_uuid, macaddr, ipv4, order,
                 floating, model):
        self.uuid = interface_uuid
        self.network_uuid = network_uuid
        self.instance_uuid = instance_uuid
        self.macaddr = macaddr
        self.ipv4 = ipv4
        self.order = order
        self.floating = floating
        self.state = 'initial'
        self.state_updated = datetime.datetime.now()
        self.model = model

    def export(self):
        return {
            'uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'instance_uuid': self.instance_uuid,
            'macaddr': self.macaddr,
            'ipv4': self.ipv4,
            'order': self.order,
            'floating': self.floating,
            'state': self.state,
            'state_updated': self.state_updated.timestamp(),
            'model': self.model
        }


def create_network_interface(interface_uuid, netdesc, instance_uuid, order):
    ensure_valid_session()

    try:
        SESSION.add(NetworkInterface(
            interface_uuid, netdesc['network_uuid'], instance_uuid, netdesc['macaddress'], netdesc['address'], order, None, netdesc['model']))
    finally:
        SESSION.commit()


def hard_delete_network_interface(interface_uuid):
    ensure_valid_session()

    try:
        for i in SESSION.query(NetworkInterface).filter(
                NetworkInterface.instance_uuid == interface_uuid).all():
            SESSION.delete(i)
    except exc.NoResultFound:
        return None
    finally:
        SESSION.commit()


def get_instance_interfaces(instance_uuid):
    ensure_valid_session()

    try:
        query = SESSION.query(NetworkInterface).filter(
            NetworkInterface.instance_uuid == instance_uuid)

        query = query.filter(NetworkInterface.state != 'deleted')
        query = query.filter(NetworkInterface.state != 'error')

        for i in query.all():
            yield i.export()
    except exc.NoResultFound:
        pass


def get_network_interfaces(network_uuid):
    ensure_valid_session()

    try:
        query = SESSION.query(NetworkInterface).filter(
            NetworkInterface.network_uuid == network_uuid)

        query = query.filter(NetworkInterface.state != 'deleted')
        query = query.filter(NetworkInterface.state != 'error')

        for i in query.all():
            yield i.export()
    except exc.NoResultFound:
        pass


def get_interface(interface_uuid):
    ensure_valid_session()

    try:
        query = SESSION.query(NetworkInterface).filter(
            NetworkInterface.uuid == interface_uuid)

        query = query.filter(NetworkInterface.state != 'deleted')
        query = query.filter(NetworkInterface.state != 'error')

        return query.one().export()
    except exc.NoResultFound:
        pass


def update_network_interface_state(interface_uuid, state):
    ensure_valid_session()

    try:
        i = SESSION.query(NetworkInterface).filter(
            NetworkInterface.uuid == interface_uuid).one()
        i.state = state
        i.state_updated = datetime.datetime.now()
    finally:
        SESSION.commit()


def add_floating_to_interface(interface_uuid, addr):
    ensure_valid_session()

    try:
        ni = SESSION.query(NetworkInterface).filter(
            NetworkInterface.uuid == interface_uuid).one()
        ni.floating = addr
    finally:
        SESSION.commit()


def remove_floating_from_interface(interface_uuid):
    ensure_valid_session()

    try:
        ni = SESSION.query(NetworkInterface).filter(
            NetworkInterface.uuid == interface_uuid).one()
        ni.floating = None
    finally:
        SESSION.commit()


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
        })


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
