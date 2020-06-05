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


class Node(Base):
    __tablename__ = 'nodes'

    fqdn = Column(String, primary_key=True)
    lastseen = Column(DateTime)
    ip = Column(String)

    def __init__(self, fqdn, ip):
        self.fqdn = fqdn
        self.ip = ip
        self.lastseen = datetime.datetime.now()

    def export(self):
        return {
            'fqdn': self.fqdn,
            'ip': self.ip,
            'lastseen': self.lastseen
        }


def see_this_node():
    try:
        node = SESSION.query(Node).filter(
            Node.fqdn == config.parsed.get('NODE_NAME')).one()
    except exc.NoResultFound:
        node = Node(config.parsed.get('NODE_NAME'),
                    config.parsed.get('NODE_IP'))
        SESSION.add(node)

    node.lastseen = datetime.datetime.now()
    SESSION.commit()


def get_node_ips():
    ensure_valid_session()

    try:
        nodes = SESSION.query(Node).all()
        for node in nodes:
            yield node.ip
    except exc.NoResultFound:
        pass


def get_node(fqdn):
    ensure_valid_session()

    try:
        node = SESSION.query(Node).filter(
            Node.fqdn == fqdn).one()
        return node.export()
    except exc.NoResultFound:
        pass


def get_nodes():
    ensure_valid_session()

    try:
        nodes = SESSION.query(Node).all()
        for n in nodes:
            yield n.export()
    except exc.NoResultFound:
        pass


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
        self.ipmanager = None
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
            'ipmanager': self.ipmanager,
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
    ensure_valid_session()

    try:
        netid = str(uuid.uuid4())

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
    ensure_valid_session()

    try:
        n = Network('floating', 0, netblock, False, False, None, 'floating')
        SESSION.add(n)
    finally:
        SESSION.commit()


def persist_ipmanager(network_uuid, data):
    ensure_valid_session()

    try:
        n = SESSION.query(Network).filter(Network.uuid == network_uuid).one()
        n.ipmanager = data.encode('utf-8')
    finally:
        SESSION.commit()


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
        # TODO(mikal): this is naive and we should at least check
        # we haven't double allocated the port number on this node.
        console_port = random.randrange(30000, 31000)
        vdi_port = random.randrange(30000, 31000)
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


def is_address_free(network_uuid, address):
    ensure_valid_session()

    try:
        query = SESSION.query(NetworkInterface).filter(
            NetworkInterface.network_uuid == network_uuid).filter(
                NetworkInterface.ipv4 == address)

        query = query.filter(NetworkInterface.state != 'deleted')
        query = query.filter(NetworkInterface.state != 'error')

        if query.count() > 0:
            return False
        return True
    except exc.NoResultFound:
        return True


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


class Snapshot(Base):
    __tablename__ = 'snapshots'

    uuid = Column(String, primary_key=True)
    device = Column(String, primary_key=True)
    instance_uuid = Column(String)
    created = Column(DateTime)

    def __init__(self, snapshot_uuid, device, instance_uuid, created):
        self.uuid = snapshot_uuid
        self.device = device
        self.instance_uuid = instance_uuid
        self.created = created

    def export(self):
        return {
            'uuid': self.uuid,
            'device': self.device,
            'instance_uuid': self.instance_uuid,
            'created': self.created
        }


def create_snapshot(snapshot_uuid, device, instance_uuid, created):
    ensure_valid_session()

    try:
        SESSION.add(Snapshot(snapshot_uuid, device, instance_uuid, created))
    finally:
        SESSION.commit()


def get_instance_snapshots(instance_uuid):
    ensure_valid_session()

    try:
        snapshots = SESSION.query(Snapshot).filter(
            Snapshot.instance_uuid == instance_uuid).all()
        for s in snapshots:
            yield s.export()
    except exc.NoResultFound:
        pass


class Event(Base):
    __tablename__ = 'events'

    timestamp = Column(DateTime)
    object_type = Column(String, primary_key=True)
    object_uuid = Column(String, primary_key=True)
    fqdn = Column(String, primary_key=True)
    operation = Column(String, primary_key=True)
    phase = Column(String, primary_key=True)
    duration = Column(Float)
    message = Column(String)

    def __init__(self, object_type, object_uuid, operation, phase, duration, message):
        self.timestamp = datetime.datetime.now()
        self.object_type = object_type
        self.object_uuid = object_uuid
        self.fqdn = config.parsed.get('NODE_NAME')
        self.operation = operation
        self.phase = phase
        self.duration = duration
        self.message = message

    def export(self):
        return {
            'timestamp': self.timestamp,
            'object_type': self.object_type,
            'object_uuid': self.object_uuid,
            'fqdn': self.fqdn,
            'operation': self.operation,
            'phase': self.phase,
            'duration': self.duration,
            'message': self.message
        }


def add_event(object_type, object_uuid, operation, phase, duration, message):
    ensure_valid_session()

    try:
        SESSION.add(Event(object_type, object_uuid, operation, phase,
                          duration, message))
    finally:
        SESSION.commit()


def get_events(object_type, object_uuid):
    ensure_valid_session()

    try:
        events = SESSION.query(Event).filter(
            Event.object_type == object_type).filter(
                Event.object_uuid == object_uuid).all()
        for e in events:
            yield e.export()
    except exc.NoResultFound:
        pass


class NodeMetric(Base):
    __tablename__ = 'node_metrics'

    fqdn = Column(String, primary_key=True)
    metric = Column(String, primary_key=True)
    value = Column(String)
    timestamp = Column(DateTime)

    def __init__(self, metric, value):
        self.fqdn = config.parsed.get('NODE_NAME')
        self.metric = metric
        self.value = value
        self.timestamp = datetime.datetime.now()

    def export(self):
        return {
            'fqdn': self.fqdn,
            'metric': self.metric,
            'value': self.value,
            'timestamp': self.timestamp
        }


def update_metric(metric, value):
    ensure_valid_session()

    try:
        try:
            metric = SESSION.query(NodeMetric).filter(
                NodeMetric.fqdn == config.parsed.get('NODE_NAME')).filter(
                    NodeMetric.metric == metric).one()
            metric.value = value
        except exc.NoResultFound:
            metric = NodeMetric(metric, value)

        SESSION.add(metric)
    finally:
        SESSION.commit()


def get_metric(fqdn, metric):
    ensure_valid_session()

    try:
        m = SESSION.query(NodeMetric).filter(
            NodeMetric.fqdn == fqdn).filter(
                NodeMetric.metric == metric).one()
        return m.export()
    except exc.NoResultFound:
        pass


def get_metrics(fqdn):
    ensure_valid_session()

    try:
        metrics = SESSION.query(NodeMetric).filter(
            NodeMetric.fqdn == fqdn).all()
        for m in metrics:
            yield m.export()
    except exc.NoResultFound:
        pass
