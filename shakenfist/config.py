# Copyright 2019 Michael Still

import copy
import logging
import os
import socket


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


node_name = socket.getfqdn()
try:
    node_ip = socket.gethostbyname(node_name)
except Exception:
    # Only for localhost development environments
    node_ip = '127.0.0.1'
    LOG.warning(
        'Could not determine hostname. This is a failure for production '
        'deploys.')

CONFIG_DEFAULTS = {
    # Deployment options
    # The ports for the internal REST API service
    'API_PORT': 13000,

    # Where the MySQL database is
    'SQL_URL': 'mysql://root:foo@localhost/sf',

    # Which node provides DHCP and NAT
    'NETWORK_NODE_IP': node_ip,

    # What nova called an availability zone
    'ZONE': 'shaken',

    # NODE SPECIFIC
    # -------------

    # The bus to use for disk devices. One of virtio, scsi, usb, ide, etc.
    # See libvirt docs for full list of options.
    'DISK_BUS': 'virtio',

    # The network model to use for network devices. One of virtio, e1000,
    # or another value supported by libvirt.
    'NETWORK_MODEL': 'virtio',

    # The IP of this node
    'NODE_IP': node_ip,
    'NODE_NAME': node_name,
    'NODE_EGRESS_NIC': 'eth0',

    # Where on disk instances are stored
    'STORAGE_PATH': '/srv/shakenfist',
}


class Config(object):
    def __init__(self):
        self.config = copy.copy(CONFIG_DEFAULTS)
        for var in os.environ:
            if var.startswith('SHAKENFIST_'):
                flag = var.replace('SHAKENFIST_', '')
                self.config[flag] = os.environ[var]

    def get(self, var):
        return self.config.get(var)


parsed = Config()
