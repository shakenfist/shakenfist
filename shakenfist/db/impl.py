# Copyright 2020 Michael Still

import datetime
import logging
import random
import uuid

from sqlalchemy import create_engine
from sqlalchemy import Boolean, Column, Integer, String, DateTime
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

    def __init__(self, uuid, vxid, netblock, provide_dhcp, provide_nat, owner):
        self.uuid = uuid
        self.vxid = vxid
        self.netblock = netblock
        self.provide_dhcp = provide_dhcp
        self.provide_nat = provide_nat
        self.owner = owner

    def export(self):
        return {
            'uuid': self.uuid,
            'vxid': self.vxid,
            'netblock': self.netblock,
            'provide_dhcp': self.provide_dhcp,
            'provide_nat': self.provide_nat,
            'owner': self.owner
        }


def get_network(uuid):
    ensure_valid_session()

    try:
        return SESSION.query(Network).filter(
            Network.uuid == uuid).one().export()
    except exc.NoResultFound:
        return None


def get_networks():
    ensure_valid_session()

    try:
        networks = SESSION.query(Network).all()
        for n in networks:
            yield n.export()
    except exc.NoResultFound:
        pass


def allocate_network(netblock, provide_dhcp=True, provide_nat=False):
    ensure_valid_session()

    try:
        netid = str(uuid.uuid4())

        for r in SESSION.query(func.max(Network.vxid)).first():
            if r:
                vxid = r + 1
            else:
                vxid = 1

        n = Network(netid, vxid, netblock, provide_dhcp, provide_nat, None)
        SESSION.add(n)
        return n.export()
    finally:
        SESSION.commit()


def delete_network(uuid):
    ensure_valid_session()

    try:
        for n in SESSION.query(Network).filter(
                Network.uuid == uuid).all():
            SESSION.delete(n)
    except exc.NoResultFound:
        return None
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

    def __init__(self, uuid, name, cpus, memory_mb, disk_spec,
                 ssh_key, node, console_port, vdi_port, user_data):
        self.uuid = uuid
        self.name = name
        self.cpus = cpus
        self.memory = memory_mb
        self.disk_spec = disk_spec
        self.ssh_key = ssh_key
        self.node = node
        self.console_port = console_port
        self.vdi_port = vdi_port
        self.user_data = user_data

    def export(self):
        return {
            'uuid': self.uuid,
            'name': self.name,
            'cpus': self.cpus,
            'memory': self.memory,
            'disk_spec': self.disk_spec,
            'ssh_key': self.ssh_key,
            'node': self.node,
            'console_port': self.console_port,
            'vdi_port': self.vdi_port,
            'user_data': self.user_data
        }


def get_instance(uuid):
    ensure_valid_session()

    try:
        return SESSION.query(Instance).filter(
            Instance.uuid == uuid).one().export()
    except exc.NoResultFound:
        return None
    finally:
        SESSION.commit()


def get_instances(local_only=False):
    ensure_valid_session()

    try:
        if local_only:
            query = SESSION.query(Instance).filter(
                Instance.node == config.parsed.get('NODE_NAME'))
        else:
            query = SESSION.query(Instance)

        for i in query.all():
            yield i.export()
    except exc.NoResultFound:
        pass


def create_instance(uuid, name, cpus, memory_mb, disk_spec, ssh_key, user_data):
    ensure_valid_session()

    try:
        # TODO(mikal): this is naive and we should at least check
        # we haven't double allocated the port number on this node.
        console_port = random.randrange(30000, 31000)
        vdi_port = random.randrange(30000, 31000)
        i = Instance(uuid, name, cpus, memory_mb, disk_spec, ssh_key,
                     config.parsed.get('NODE_NAME'), console_port,
                     vdi_port, user_data)
        SESSION.add(i)
        return i.export()
    finally:
        SESSION.commit()


def delete_instance(uuid):
    ensure_valid_session()

    try:
        for s in SESSION.query(Snapshot).filter(
                Snapshot.instance_uuid == uuid):
            SESSION.delete(s)
        for ni in SESSION.query(NetworkInterface).filter(
                NetworkInterface.instance_uuid == uuid).all():
            SESSION.delete(ni)
        i = SESSION.query(Instance).filter(
            Instance.uuid == uuid).one()
        SESSION.delete(i)
    except exc.NoResultFound:
        return None
    finally:
        SESSION.commit()


class NetworkInterface(Base):
    __tablename__ = 'network_interfaces'

    uuid = Column(String, primary_key=True)
    network_uuid = Column(String)
    instance_uuid = Column(String)
    macaddr = Column(String)
    ipv4 = Column(String)
    order = Column(Integer)

    def __init__(self, uuid, network_uuid, instance_uuid, macaddr, ipv4, order):
        self.uuid = uuid
        self.network_uuid = network_uuid
        self.instance_uuid = instance_uuid
        self.macaddr = macaddr
        self.ipv4 = ipv4
        self.order = order

    def export(self):
        return {
            'uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'instance_uuid': self.instance_uuid,
            'macaddr': self.macaddr,
            'ipv4': self.ipv4,
            'order': self.order
        }


def create_network_interface(uuid, network_uuid, instance_uuid, macaddr, ipv4, order):
    ensure_valid_session()

    try:
        SESSION.add(NetworkInterface(
            uuid, network_uuid, instance_uuid, macaddr, ipv4, order))
    finally:
        SESSION.commit()


def delete_network_interface(uuid):
    ensure_valid_session()

    try:
        for i in SESSION.query(NetworkInterface).filter(
                NetworkInterface.instance_uuid == uuid).all():
            SESSION.delete(i)
    except exc.NoResultFound:
        return None
    finally:
        SESSION.commit()


def get_instance_interfaces(uuid):
    ensure_valid_session()

    try:
        interfaces = SESSION.query(NetworkInterface).filter(
            NetworkInterface.instance_uuid == uuid).all()
        for i in interfaces:
            yield i.export()
    except exc.NoResultFound:
        pass


def get_network_interfaces(uuid):
    ensure_valid_session()

    try:
        interfaces = SESSION.query(NetworkInterface).filter(
            NetworkInterface.network_uuid == uuid).all()
        for i in interfaces:
            yield i.export()
    except exc.NoResultFound:
        pass


class Snapshot(Base):
    __tablename__ = 'snapshots'

    uuid = Column(String, primary_key=True)
    device = Column(String, primary_key=True)
    instance_uuid = Column(String)
    created = Column(DateTime)

    def __init__(self, uuid, device, instance_uuid, created):
        self.uuid = uuid
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


def create_snapshot(uuid, device, instance_uuid, created):
    ensure_valid_session()

    try:
        SESSION.add(Snapshot(uuid, device, instance_uuid, created))
    finally:
        SESSION.commit()
