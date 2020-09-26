# Copyright 2019 Michael Still

import copy
import os
import socket

from shakenfist import exceptions
from shakenfist import util


# NOTE(mikal): Dear future developer. Thanks ftheor dropping by! Remember that the type
# of values in this set of defaults matters, as we use it to decide how to parse
# override values from the user. Your current options are strings (the default), integers,
# and floats. Integers and floats differ in that a float has values after a decimal point.

CONFIG_DEFAULTS = {
    # Deployment wide options
    # -----------------------

    # API options
    #  - api_port: the port for the REST API
    #  - api_timeout: how long gunicorn processes can use for a single request
    #  - api_async_wait: how long we wait for an async operation to complete
    #    before returning to the user
    #  - include_tracebacks: if tracebacks should be included where relevant
    #    in API responses. Do not enable for untrusted clients!
    #  - prometheus_metrics_port: where to expose internal metrics. Do not
    #    allow access from untrusted clients!
    #  - auth_secret_seed: a random string to see auth secrets with
    #  - api_command_line: the gunicorn command line to use
    'API_PORT': 13000,
    'API_TIMEOUT': 900,
    'API_ASYNC_WAIT': 30,
    'INCLUDE_TRACEBACKS': '1',
    'PROMETHEUS_METRICS_PORT': 13001,
    'AUTH_SECRET_SEED': 'foo',
    'API_COMMAND_LINE': ('gunicorn --workers 10 --bind 0.0.0.0:%(port)d '
                         '--log-syslog --log-syslog-prefix sf '
                         '--timeout %(timeout)s --name "%(name)s" '
                         'shakenfist.external_api.app:app'),

    # Scheduler options:
    #  - scheduler_cache_timeout: how long the scheduler should cache things for
    #  - cpu_overcommit_ratio: how many vCPUS per real CPU
    #  - ram_overcommit_ratio: how much vRAM per real unit of RAM, that is if
    #    Kernel Shared Memory (KSM) is enabled, how much to overcommit memory
    #    because of shared pages.
    #  - ram_system_reservation: how much RAM is reserved for the OS
    'SCHEDULER_CACHE_TIMEOUT': 5,
    'CPU_OVERCOMMIT_RATIO': 16,
    'RAM_OVERCOMMIT_RATIO': 1.5,
    'RAM_SYSTEM_RESERVATION': 5.0,

    # Network options:
    #  - floating_network: a network block used for NAT egress from VMs and
    #    floating IPs
    #  - network_node_ip: the IP of a node which will egress all traffic
    'FLOATING_NETWORK': '192.168.20.0/24',
    'NETWORK_NODE_IP': None,

    # Database options:
    #  - cleaner_delay: how long to wait before removing old data from the
    #    database.
    'CLEANER_DELAY': 3600,

    # What nova called an availability zone
    'ZONE': 'shaken',

    # Do we have preferred mirrors for Ubuntu or Cirros?
    'DOWNLOAD_URL_CIRROS': ('http://download.cirros-cloud.net/%(vernum)s/'
                            'cirros-%(vernum)s-x86_64-disk.img'),
    'DOWNLOAD_URL_UBUNTU': ('https://cloud-images.ubuntu.com/%(vername)s/current/'
                            '%(vername)s-server-cloudimg-amd64.img'),

    # NODE SPECIFIC
    # -------------

    # The bus to use for disk devices. One of virtio, scsi, usb, ide, etc.
    # See libvirt docs for full list of options.
    'DISK_BUS': 'virtio',

    # The format for disks. Options are qcow (COW layers onto of image cache),
    # qcow_flat (just qcow2, no COW), and flat (just raw disk).
    'DISK_FORMAT': 'qcow',

    # The IP of this node, populated by parse()
    'NODE_IP': None,
    'NODE_NAME': None,
    'NODE_EGRESS_NIC': 'eth0',

    # Where on disk instances are stored
    'STORAGE_PATH': '/srv/shakenfist',

    # LOGGING
    # -------
    'SLOW_LOCK_THRESHOLD': 5.0,
    'LOGLEVEL_API': 'info',
    'LOGLEVEL_CLEANER': 'info',
    'LOGLEVEL_MAIN': 'info',
    'LOGLEVEL_NET': 'info',
    'LOGLEVEL_RESOURCES': 'info',
    'LOGLEVEL_TRIGGERS': 'info',
}


class Config(object):
    def __init__(self):
        self.config = None

    def parse(self):
        global CONFIG_DEFAULTS

        node_name = socket.getfqdn()
        try:
            node_ip = socket.gethostbyname(node_name)
        except Exception as e:
            # Only for localhost development environments
            node_ip = '127.0.0.1'
            util.ignore_exception('config parser', e)

        CONFIG_DEFAULTS['NODE_NAME'] = node_name
        CONFIG_DEFAULTS['NODE_IP'] = node_ip
        CONFIG_DEFAULTS['NETWORK_NODE_IP'] = node_ip

        self.config = copy.copy(CONFIG_DEFAULTS)
        for var in os.environ:
            if var.startswith('SHAKENFIST_'):
                flag = var.replace('SHAKENFIST_', '')
                value = os.environ[var]

                # We use the type of the default value to determine
                # what type we should force the value provided by an
                # environment variable into.
                if isinstance(CONFIG_DEFAULTS[flag], int):
                    value = int(value)
                elif isinstance(CONFIG_DEFAULTS[flag], float):
                    value = float(value)
                elif not isinstance(CONFIG_DEFAULTS[flag], str):
                    raise exceptions.FlagException(
                        'Flag %s has unknown type.' % flag)

                self.config[flag] = value

    def get(self, var):
        if not self.config:
            self.parse()
        return self.config.get(var)


parsed = Config()
