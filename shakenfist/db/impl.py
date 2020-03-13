# Copyright 2020 Michael Still

import datetime
import logging
import uuid

from sqlalchemy import create_engine
from sqlalchemy import Boolean, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import exc, sessionmaker
from sqlalchemy.sql.expression import func


from shakenfist import config


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)

sql_url = config.parsed.get('SQL_URL')
LOG.info('Connecting to database at %s' % sql_url)
engine = create_engine(sql_url)
Base = declarative_base()
Session = sessionmaker(bind=engine)


class Node(Base):
    __tablename__ = 'nodes'

    fqdn = Column(String, primary_key=True)
    lastseen = Column(DateTime)
    ip = Column(String)

    def __init__(self, fqdn, ip):
        self.fqdn = fqdn
        self.ip = ip
        self.lastseen = datetime.datetime.now()


def see_this_node(session=None):
    if not session:
        session = Session()

    try:
        node = session.query(Node).filter(
            Node.fqdn == config.parsed.get('NODE_NAME')).one()
    except exc.NoResultFound:
        node = Node(config.parsed.get('NODE_NAME'),
                    config.parsed.get('NODE_IP'))
        session.add(node)

    node.lastseen = datetime.datetime.now()


def get_node_ips():
    session = Session()
    see_this_node(session=session)

    try:
        nodes = session.query(Node).all()
        for node in nodes:
            yield node.ip
    except exc.NoResultFound:
        pass
    finally:
        session.commit()
        session.close()


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
    session = Session()
    see_this_node(session=session)

    try:
        return session.query(Network).filter(
            Network.uuid == uuid).one().export()
    except exc.NoResultFound:
        return None
    finally:
        session.close()


def get_networks():
    session = Session()
    see_this_node(session=session)

    try:
        networks = session.query(Network).all()
        for n in networks:
            yield n.export()
    except exc.NoResultFound:
        pass
    finally:
        session.close()


def allocate_network(netblock, provide_dhcp=True, provide_nat=False):
    session = Session()
    see_this_node(session=session)

    try:
        netid = str(uuid.uuid4())

        for r in session.query(func.max(Network.vxid)).first():
            if r:
                vxid = r + 1
            else:
                vxid = 1

        n = Network(netid, vxid, netblock, provide_dhcp, provide_nat, None)
        session.add(n)
        return n.export()
    finally:
        session.commit()
        session.close()


def delete_network(uuid):
    session = Session()
    see_this_node(session=session)

    try:
        for n in session.query(Network).filter(
                Network.uuid == uuid):
            session.delete(n)
    except exc.NoResultFound:
        return None
    finally:
        session.commit()
        session.close()


class Instance(Base):
    __tablename__ = 'instances'

    uuid = Column(String, primary_key=True)
    network_uuid = Column(String)
    name = Column(String)
    cpus = Column(Integer)
    memory = Column(Integer)
    disk_spec = Column(String)
    ssh_key = Column(String)

    def __init__(self, uuid, network_uuid, name, cpus, memory_mb, disk_spec, ssh_key):
        self.uuid = uuid
        self.network_uuid = network_uuid
        self.name = name
        self.cpus = cpus
        self.memory = memory_mb
        self.disk_spec = disk_spec
        self.ssh_key = ssh_key

    def export(self):
        return {
            'uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'name': self.name,
            'cpus': self.cpus,
            'memory': self.memory,
            'disk_spec': self.disk_spec,
            'ssh_key': self.ssh_key
        }


def get_instance(uuid):
    session = Session()
    see_this_node(session=session)

    try:
        return session.query(Instance).filter(
            Instance.uuid == uuid).one().export()
    except exc.NoResultFound:
        return None
    finally:
        session.close()


def get_instances():
    session = Session()
    see_this_node(session=session)

    try:
        instances = session.query(Instance).all()
        for i in instances:
            yield i.export()
    except exc.NoResultFound:
        pass
    finally:
        session.close()


def create_instance(uuid, network_uuid, name, cpus, memory_mb, disk_spec, ssh_key):
    session = Session()
    see_this_node(session=session)

    try:
        i = Instance(uuid, network_uuid, name, cpus,
                     memory_mb, disk_spec, ssh_key)
        session.add(i)
        return i.export()
    finally:
        session.commit()
        session.close()


def delete_instance(uuid):
    session = Session()
    see_this_node(session=session)

    try:
        for ni in session.query(NetworkInterface).filter(
                NetworkInterface.instance_uuid == uuid):
            session.delete(ni)
        i = session.query(Instance).filter(
            Instance.uuid == uuid).one()
        session.delete(i)
    except exc.NoResultFound:
        return None
    finally:
        session.commit()
        session.close()


class NetworkInterface(Base):
    __tablename__ = 'network_interfaces'

    uuid = Column(String, primary_key=True)
    network_uuid = Column(String)
    instance_uuid = Column(String)
    macaddr = Column(String)
    ipv4 = Column(String)

    def __init__(self, uuid, network_uuid, instance_uuid, macaddr, ipv4):
        self.uuid = uuid
        self.network_uuid = network_uuid
        self.instance_uuid = instance_uuid
        self.macaddr = macaddr
        self.ipv4 = ipv4

    def export(self):
        return {
            'uuid': self.uuid,
            'network_uuid': self.network_uuid,
            'instance_uuid': self.instance_uuid,
            'macaddr': self.macaddr,
            'ipv4': self.ipv4
        }


def create_network_interface(uuid, network_uuid, instance_uuid, macaddr, ipv4):
    session = Session()
    see_this_node(session=session)

    try:
        session.add(NetworkInterface(
            uuid, network_uuid, instance_uuid, macaddr, ipv4))
    finally:
        session.commit()
        session.close()


def get_instance_interfaces(uuid):
    session = Session()
    see_this_node(session=session)

    try:
        interfaces = session.query(NetworkInterface).filter(
            NetworkInterface.instance_uuid == uuid).all()
        for i in interfaces:
            yield i.export()
    except exc.NoResultFound:
        pass
    finally:
        session.close()


def get_network_interfaces(uuid):
    session = Session()
    see_this_node(session=session)

    try:
        interfaces = session.query(NetworkInterface).filter(
            NetworkInterface.network_uuid == uuid).all()
        for i in interfaces:
            yield i.export()
    except exc.NoResultFound:
        pass
    finally:
        session.close()


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
    session = Session()
    see_this_node(session=session)

    try:
        session.add(Snapshot(uuid, device, instance_uuid, created))
    finally:
        session.commit()
        session.close()
