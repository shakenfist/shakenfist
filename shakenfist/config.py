# Copyright 2019 Michael Still

import socket

from pydantic import (
    BaseSettings,
    Field,
    SecretStr,
    AnyHttpUrl,
)


def get_node_ip():
    node_name = socket.getfqdn()
    try:
        return socket.gethostbyname(node_name)
    except Exception:
        # Only for localhost development environments
        return '127.0.0.1'


def get_node_name():
    return socket.getfqdn()


class SFConfigBase(BaseSettings):
    """
    Separated from SFConfig for ease of testing
    """
    def get(self, key):
        return self.dict()[key]


class SFConfig(SFConfigBase):
    ###################
    # Deployment Wide #
    ###################

    # API Options
    API_PORT: int = Field(13000, description='Port for the REST API')
    API_TIMEOUT: int = Field(
        900,
        description='How long gunicorn processes can use for a single request',
    )
    API_ASYNC_WAIT: int = Field(
        30,
        description='How long we wait for an async operation to complete '
                    'before returning to the user',
    )
    INCLUDE_TRACEBACKS: bool = Field(
        False,
        description='If tracebacks should be included where relevant in API '
                    'responses. Do not enable for untrusted clients!',
    )
    PROMETHEUS_METRICS_PORT: int = Field(
        13001,
        description='where to expose internal '
                    'metrics. Do not allow '
                    'access from untrusted '
                    'clients!',
    )
    AUTH_SECRET_SEED: SecretStr = Field(
        'foo', description='A random string to seed auth secrets with'
    )
    API_COMMAND_LINE: str = Field(
        (
            "gunicorn --workers 10 --bind 0.0.0.0:%(port)d --log-syslog "
            "--log-syslog-prefix sf --timeout %(timeout)s --name '%(name)s' "
            "shakenfist.external_api.app:app"
        ),
        description='The gunicorn command line to use',
    )

    # Scheduler Options
    SCHEDULER_CACHE_TIMEOUT: int = Field(
        5, description='how long the scheduler should cache things for'
    )
    CPU_OVERCOMMIT_RATIO: float = Field(
        16, description='how many vCPUS per real CPU'
    )
    RAM_OVERCOMMIT_RATIO: float = Field(
        1.5,
        description='how much vRAM per real unit of RAM, that is if Kernel '
                    'Shared Memory (KSM) is enabled, how much to overcommit '
                    'memory because of shared pages.',
    )
    RAM_SYSTEM_RESERVATION: float = Field(
        5.0, description='how much RAM is reserved for the OS'
    )

    # Network Options
    FLOATING_NETWORK: str = Field(
        '192.168.20.0/24',
        description='Network block used for NAT egress from VMs and floating '
                    'IPs',
    )
    NETWORK_NODE_IP: str = Field(
        default_factory=get_node_ip,
        description='IP of the node which will egress all traffic',
    )

    # Database Options
    CLEANER_DELAY: int = Field(
        3600,
        description='How long to wait before removing old data from the '
                    'database',
    )

    # Other options
    ZONE: str = Field(
        'shaken', description='What nova called an availability zone'
    )
    DOWNLOAD_URL_CIRROS: AnyHttpUrl = Field(
        (
            'http://download.cirros-cloud.net/%(vernum)s/'
            'cirros-%(vernum)s-x86_64-disk.img'
        ),
        description='Default download mirror for Cirros image',
    )
    DOWNLOAD_URL_UBUNTU: AnyHttpUrl = Field(
        (
            'https://cloud-images.ubuntu.com/%(vername)s/current/'
            '%(vername)s-server-cloudimg-amd64.img'
        ),
        description='Default download mirror for Ubuntu image',
    )

    #################
    # Node Specific #
    #################

    DISK_BUS: str = Field(
        'virtio',
        description='The bus to use for disk devices. One of virtio, scsi, '
                    'usb, ide, etc. See libvirt docs for full list of options.',
    )
    DISK_FORMAT: str = Field(
        'qcow',
        description='The format for disks. Options are qcow (COW layers onto '
                    'of image cache), qcow_flat (just qcow2, no COW), and flat '
                    '(just raw disk).',
    )
    NODE_IP: str = Field(
        default_factory=get_node_ip, description='IP of this node'
    )
    NODE_NAME: str = Field(
        default_factory=get_node_name, description='FQDN of this node'
    )
    NODE_EGRESS_NIC: str = Field('eth0', description='NIC for outbound traffic')
    STORAGE_PATH: str = Field(
        '/srv/shakenfist', description='Where on disk instances are stored'
    )

    # Logging
    SLOW_LOCK_THRESHOLD: float = 5.0
    LOGLEVEL_API: str = 'info'
    LOGLEVEL_CLEANER: str = 'info'
    LOGLEVEL_MAIN: str = 'info'
    LOGLEVEL_NET: str = 'info'
    LOGLEVEL_RESOURCES: str = 'info'
    LOGLEVEL_TRIGGERS: str = 'info'
    LOGLEVEL_QUEUES: str = 'info'
    LOG_METHOD_TRACE: int = Field(
        0, description='Add method name and module line number to log messages'
    )

    class Config:
        env_prefix = 'SHAKENFIST_'


config = SFConfig()
