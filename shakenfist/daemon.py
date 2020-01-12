# Copyright 2019 Michael Still

import logging
import uuid

from shakenfist import db
from shakenfist import net
from shakenfist import util
from shakenfist import virt


logging.basicConfig(level=logging.DEBUG)


db.see_this_node()

node_ips = list(db.get_node_ips())
network = net.Network(
    uuid=str(uuid.uuid4()),
    vxlan_id=3,
    provide_dhcp=True,
    physical_nic='eth0',
    nodes=node_ips,
    ipblock='192.168.200.0/24')
with util.RecordedOperation('network creation', network) as ro:
    network.create()

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
