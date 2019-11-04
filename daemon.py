# Copyright 2019 Michael Still

import uuid

import virt


uuid = str(uuid.uuid4())
instance = virt.Instance(uuid, 'instance', 'tenant')
