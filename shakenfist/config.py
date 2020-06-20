# Copyright 2019 Michael Still

import copy
import logging
from logging import handlers as logging_handlers
import os
import socket


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)
LOG.addHandler(logging_handlers.SysLogHandler(address=('127.0.0.1', 514)))


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
    # Deployment wide options
    # -----------------------

    # API options
    #  - api_port: the port for the REST API
    #  - include_tracebacks: if tracebacks should be included where relevant
    #    in API responses. Do not enable for untrusted clients!
    #  - prometheus_metrics_port: where to expose internal metrics. Do not
    #    allow access from untrusted clients!
    'API_PORT': 13000,
    'INCLUDE_TRACEBACKS': '1',
    'PROMETHEUS_METRICS_PORT': 13001,

    # Scheduler options:
    #  - scheduler_cache_timeout: how long the scheduler should cache things for
    #  - cpu_overcommit_ratio: how many vCPUS per real CPU
    #  - ram_overcommit_ratio: how much vRAM per real unit of RAM
    'SCHEDULER_CACHE_TIMEOUT': 30,
    'CPU_OVERCOMMIT_RATIO': 16,
    'RAM_OVERCOMMIT_RATIO': 1.5,

    # Network options:
    #  - floating_network: a network block used for NAT egress from VMs and
    #    floating IPs
    #  - network_node_ip: the IP of a node which will egress all traffic
    'FLOATING_NETWORK': '192.168.20.0/24',
    'NETWORK_NODE_IP': node_ip,

    # Database options:
    #  - cleaner_delay: how long to wait before removing old data from the
    #    database.
    'CLEANER_DELAY': 3600,
    'ETCD_USER': 'sf',
    'ETCD_PASSWORD': 'foo',
    'ETCD_SERVER': 'localhost',

    # What nova called an availability zone
    'ZONE': 'shaken',

    # NODE SPECIFIC
    # -------------

    # The bus to use for disk devices. One of virtio, scsi, usb, ide, etc.
    # See libvirt docs for full list of options.
    'DISK_BUS': 'virtio',

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
