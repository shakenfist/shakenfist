# Copyright 2019 Michael Still

import logging
import uuid

import virt


logging.basicConfig(level=logging.DEBUG)


instance = virt.Instance(
    uuid=str(uuid.uuid4()),
    name='foo',
    tenant=None,
    image_url='https://cloud-images.ubuntu.com/disco/current/disco-server-cloudimg-amd64.img',
    root_size_gb=20,
    memory_kb=4 * 1024 * 1024,
    vcpus=1)