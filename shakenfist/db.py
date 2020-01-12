# Copyright 2020 Michael Still

import datetime
import logging

from sqlalchemy import create_engine
from sqlalchemy import Column, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import exc, sessionmaker


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


def see_this_node():
    session = Session()

    try:
        node = session.query(Node).filter(
            Node.fqdn == config.parsed.get('NODE_NAME')).one()
    except exc.NoResultFound:
        node = Node(config.parsed.get('NODE_NAME'),
                    config.parsed.get('NODE_IP'))
        session.add(node)

    node.lastseen = datetime.datetime.now()

    session.commit()
    session.close()


def get_node_ips():
    see_this_node()
    session = Session()

    try:
        nodes = session.query(Node).all()
        for node in nodes:
            yield node.ip
    except exc.NoResultFound:
        pass
    finally:
        session.close()
