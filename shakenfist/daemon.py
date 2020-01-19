# Copyright 2019 Michael Still

import copy
import logging
import setproctitle
import time
import os
import uuid

from shakenfist import db
from shakenfist import net
from shakenfist import util
from shakenfist import virt


logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


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
            physical_nic='eth0',
            nodes=node_ips,
            ipblock=n.netblock)
        with util.RecordedOperation('network creation', network) as ro:
            network.create()

    while True:
        time.sleep(30)

        node_ips = list(db.get_node_ips())
        for n in db.get_networks():
            network = net.Network(
                uuid=n.uuid,
                vxlan_id=n.vxid,
                provide_dhcp=n.provide_dhcp,
                physical_nic='eth0',
                nodes=node_ips,
                ipblock=n.netblock)
            network.ensure_mesh(copy.deepcopy(node_ips))

setproctitle.setproctitle('sf main')
LOG.info('netmod pid is %d' % netmon_pid)

while True:
    time.sleep(10)
    wpid, status = os.waitpid(-1, os.WNOHANG)
    LOG.warning('Subprocess %d died' % wpid)


# instance = virt.Instance(
#     uuid=str(uuid.uuid4()),
#     name='foo',
#     tenant=None,
#     # image_url='https://cloud-images.ubuntu.com/disco/current/disco-server-cloudimg-amd64.img',
#     image_url='http://download.cirros-cloud.net/0.4.0/cirros-0.4.0-x86_64-disk.img',
#     root_size_gb=20,
#     memory_kb=1 * 1024 * 1024,
#     vcpus=1)

# with util.RecordedOperation('allocate ip address', instance) as ro:
#     network.allocate_ip_to_instance(instance)
#     network.update_dhcp()

# with util.RecordedOperation('instance creation', instance) as ro:
#     instance.create()
