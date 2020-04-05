# Copyright 2019 Michael Still

import logging
import setproctitle
import time
import os

from shakenfist.daemons import api as api_daemon
from shakenfist.daemons import net as net_daemon
from shakenfist.db import impl as db
from shakenfist.net import impl as net
from shakenfist import util
from shakenfist import virt


logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


def main():
    # Ensure all instances for this node are defined
    networks = []
    instances = []
    for inst in list(db.get_instances(local_only=True)):
        for iface in db.get_instance_interfaces(inst['uuid']):
            if not iface['network_uuid'] in networks:
                networks.append(iface['network_uuid'])
        instances.append(inst['uuid'])

    with util.RecordedOperation('restore networks', None) as _:
        for network in networks:
            LOG.info('Restoring network %s' % network)
            n = net.from_db(network)
            n.create()
            n.update_dhcp()

    with util.RecordedOperation('restore instances', None) as _:
        for instance in instances:
            LOG.info('Restoring instance %s' % instance)
            i = virt.from_db(instance)
            i.create(None)

    # Network mesh maintenance
    net_pid = os.fork()
    if net_pid == 0:
        net_daemon.monitor().run()

    # REST API
    api_pid = os.fork()
    if api_pid == 0:
        api_daemon.monitor().run()

    setproctitle.setproctitle('sf main')
    LOG.info('api pid is %s' % api_pid)
    LOG.info('net pid is %d' % net_pid)

    while True:
        time.sleep(10)
        wpid, status = os.waitpid(-1, os.WNOHANG)
        if wpid != 0:
            LOG.warning('Subprocess %d died' % wpid)
