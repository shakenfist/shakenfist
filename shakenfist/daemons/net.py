import copy
import logging
import setproctitle
import time

from shakenfist.db import impl as db
from shakenfist.net import impl as net
from shakenfist import util


logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf net')

    def run(self):
        node_ips = list(db.get_node_ips())
        net_uuids = []
        for n in db.get_networks():
            net_uuids.append(n['uuid'])

        for n in net_uuids:
            network = net.from_db(n)
            with util.RecordedOperation('network creation', network) as _:
                network.create()

        while True:
            time.sleep(30)

            node_ips = list(db.get_node_ips())
            net_uuids = []
            for n in db.get_networks():
                net_uuids.append(n['uuid'])

            for n in net_uuids:
                network = net.from_db(n)
                network.ensure_mesh(copy.deepcopy(node_ips))
