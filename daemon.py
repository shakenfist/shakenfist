# Copyright 2019 Michael Still

import logging
import uuid

import virt


logging.basicConfig(level=logging.DEBUG)


uuid = str(uuid.uuid4())
instance = virt.Instance(uuid, 'instance', 'tenant', 'https://cloud-images.ubuntu.com/disco/current/disco-server-cloudimg-amd64.img', '20G')
