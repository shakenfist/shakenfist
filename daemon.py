# Copyright 2019 Michael Still

import logging
import uuid

import virt


logging.basicConfig(level=logging.DEBUG)


instance = virt.Instance(
    uuid=str(uuid.uuid4()),
    name='foo',
    tenant=None,
    #image_url='https://cloud-images.ubuntu.com/disco/current/disco-server-cloudimg-amd64.img',
    image_url='http://download.cirros-cloud.net/0.4.0/cirros-0.4.0-x86_64-disk.img',
    root_size_gb=20,
    memory_kb=1 * 1024 * 1024,
    vcpus=1)
