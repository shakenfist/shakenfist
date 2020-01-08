# Copyright 2019 Michael Still

import logging
import uuid

import net
import util
import virt


logging.basicConfig(level=logging.DEBUG)


network = net.Network(
    uuid=str(uuid.uuid4()),
    vxlan_id=1,
    provide_dhcp=False,
    physical_nic='eth0',
    nodes=['35.223.115.132', '34.70.161.180'])
with util.RecordedOperation('network creation', network) as ro:
    network.create()

instance = virt.Instance(
    uuid=str(uuid.uuid4()),
    name='foo',
    tenant=None,
    # image_url='https://cloud-images.ubuntu.com/disco/current/disco-server-cloudimg-amd64.img',
    image_url='http://download.cirros-cloud.net/0.4.0/cirros-0.4.0-x86_64-disk.img',
    root_size_gb=20,
    memory_kb=1 * 1024 * 1024,
    vcpus=1)
with util.RecordedOperation('instance creation', instance) as ro:
    instance.create()
