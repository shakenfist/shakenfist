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


def get_network(uuid):
    session = Session()
    see_this_node(session=session)

    try:
        return session.query(Network).filter(
            Network.uuid == uuid).one()
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
            yield n
    except exc.NoResultFound:
        pass
    finally:
        session.close()


def allocate_network(netblock):
    session = Session()
    see_this_node(session=session)

    try:
        netid = str(uuid.uuid4())

        for r in session.query(func.max(Network.vxid)).first():
            if r:
                vxid = r + 1
            else:
                vxid = 1

        n = Network(netid, vxid, netblock, True, False, None)
        session.add(n)
        return n
    finally:
        session.commit()
        session.close()
