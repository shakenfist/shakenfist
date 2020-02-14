# Copyright 2019 Michael Still

import copy
import logging
import setproctitle
import time
import os

from shakenfist import config
from shakenfist.db import impl as db
from shakenfist.net import impl as net
from shakenfist import util


logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


def main():
    # Network mesh maintenance
    netmon_pid = os.fork()
    if netmon_pid == 0:
        setproctitle.setproctitle('sf netmon')
        node_ips = list(db.get_node_ips())
        for n in db.get_networks():
            network = net.Network(
                uuid=n.uuid,
                vxlan_id=n.vxid,
                provide_dhcp=n.provide_dhcp,
                physical_nic=config.parsed.get('NODE_EGRESS_NIC'),
                nodes=node_ips,
                ipblock=n.netblock)
            with util.RecordedOperation('network creation', network) as _:
                network.create()

        while True:
            time.sleep(30)

            node_ips = list(db.get_node_ips())
            for n in db.get_networks():
                network = net.Network(
                    uuid=n.uuid,
                    vxlan_id=n.vxid,
                    provide_dhcp=n.provide_dhcp,
                    physical_nic=config.parsed.get('NODE_EGRESS_NIC'),
                    nodes=node_ips,
                    ipblock=n.netblock)
                network.ensure_mesh(copy.deepcopy(node_ips))

    setproctitle.setproctitle('sf main')
    LOG.info('netmod pid is %d' % netmon_pid)

    while True:
        time.sleep(10)
        wpid, status = os.waitpid(-1, os.WNOHANG)
        if wpid != 0:
            LOG.warning('Subprocess %d died' % wpid)
