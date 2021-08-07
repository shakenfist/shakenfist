# Copyright 2019 Michael Still

import socket

from pydantic import (
    BaseSettings,
    Field,
    SecretStr,
    AnyHttpUrl,
)


def get_node_name():
    return socket.getfqdn()


class SFConfig(BaseSettings):
    ###################
    # Deployment Wide #
    ###################

    # API Options
    API_PORT: int = Field(13000, description='Port for the REST API')
    API_TIMEOUT: int = Field(
        900,
        description='How long gunicorn processes can use for a single request'
    )
    API_ASYNC_WAIT: int = Field(
        30,
        description='How long we wait for an async operation to complete '
                    'before returning to the user'
    )
    INCLUDE_TRACEBACKS: bool = Field(
        False,
        description='If tracebacks should be included where relevant in API '
                    'responses. Do not enable for untrusted clients!'
    )
    PROMETHEUS_METRICS_PORT: int = Field(
        13001,
        description='Where to expose internal metrics. Do not allow '
                    'access from untrusted clients!'
    )
    AUTH_SECRET_SEED: SecretStr = Field(
        'foo', description='A random string to seed auth secrets with'
    )
    API_COMMAND_LINE: str = Field(
        (
            '/srv/shakenfist/venv/bin/gunicorn --workers 10 --bind 0.0.0.0:%(port)d --log-syslog '
            '--log-syslog-prefix sf --timeout %(timeout)s --name "%(name)s" '
            'shakenfist.external_api.app:app'
        ),
        description='The gunicorn command line to use'
    )

    # Scheduler Options
    SCHEDULER_CACHE_TIMEOUT: int = Field(
        5, description='How long the scheduler should cache things for'
    )
    CPU_OVERCOMMIT_RATIO: float = Field(
        16, description='How many vCPUS per real CPU'
    )
    RAM_OVERCOMMIT_RATIO: float = Field(
        1.5,
        description='How much vRAM per real unit of RAM, that is if Kernel '
                    'Shared Memory (KSM) is enabled, how much to overcommit '
                    'memory because of shared pages.'
    )
    RAM_SYSTEM_RESERVATION: float = Field(
        5.0, description='How much RAM is reserved for the OS'
    )

    # Network Options
    FLOATING_NETWORK: str = Field(
        '192.168.20.0/24',
        description='Network block used for NAT egress from VMs and floating '
                    'IPs'
    )
    NETWORK_NODE_IP: str = Field(
        '', description='Mesh IP of the node which will egress all traffic',
    )
    DNS_SERVER: str = Field(
        '8.8.8.8',
        description='The DNS server to pass to instances via DHCP.'
    )
    HTTP_PROXY_SERVER: str = Field(
        '',
        description='The URL to an option HTTP proxy used for image downloads.'
    )
    MAX_HYPERVISOR_MTU: int = Field(
        8000,
        description='Maximum network MTU our hypervisors can safely set.'
    )

    # Database Options
    CLEANER_DELAY: int = Field(
        3600,
        description='How long to wait before removing old data from the '
                    'database'
    )
    NODE_CHECKIN_MAXIMUM: int = Field(
        300,
        description='How long to wait before declaring a node to be offline '
                    'due to failure to check in'
    )

    # Image resolver options
    DOWNLOAD_URL_CIRROS: AnyHttpUrl = Field(
        (
            'http://download.cirros-cloud.net/%(vernum)s/'
            'cirros-%(vernum)s-x86_64-disk.img'
        ),
        description='Default download mirror for Cirros images'
    )
    CHECKSUM_URL_CIRROS: AnyHttpUrl = Field(
        (
            'http://download.cirros-cloud.net/%(vernum)s/MD5SUMS'
        ),
        description='Default checksum URL for Cirros images'
    )
    LISTING_URL_CIRROS: AnyHttpUrl = Field(
        (
            'http://download.cirros-cloud.net/'
        ),
        description='A URL listing all Cirros releases'
    )

    DOWNLOAD_URL_DEBIAN: AnyHttpUrl = Field(
        (
            'https://cloud.debian.org/images/cloud/OpenStack/current-%(vernum)s/'
            'debian-%(vernum)s-openstack-amd64.qcow2'
        ),
        description='Default download mirror for Debian images'
    )
    CHECKSUM_URL_DEBIAN: AnyHttpUrl = Field(
        (
            'https://cloud.debian.org/images/cloud/OpenStack/'
            'current-%(vernum)s/MD5SUMS'
        ),
        description='Default checksum URL for Debian images'
    )

    DOWNLOAD_URL_UBUNTU: AnyHttpUrl = Field(
        (
            'https://cloud-images.ubuntu.com/%(vername)s/current/'
            '%(vername)s-server-cloudimg-amd64.img'
        ),
        description='Default download mirror for Ubuntu image'
    )
    CHECKSUM_URL_UBUNTU: AnyHttpUrl = Field(
        (
            'https://cloud-images.ubuntu.com/%(vername)s/current/MD5SUMS'
        ),
        description='Default checksum URL for Debian images'
    )
    LISTING_URL_UBUNTU: AnyHttpUrl = Field(
        (
            'https://cloud-images.ubuntu.com'
        ),
        description='A URL listing all Ubuntu releases'
    )

    # Other options
    BLOB_REPLICATION_FACTOR: int = Field(
        2, description='How many copies of each blob we like to have.'
    )
    ZONE: str = Field(
        'shakenfist', description='What nova called an availability zone'
    )

    #################
    # Node Specific #
    #################

    DISK_BUS: str = Field(
        'virtio',
        description='The bus to use for disk devices. One of virtio, scsi, '
                    'usb, ide, etc. See libvirt docs for full list of options.'
    )
    NODE_NAME: str = Field(
        default_factory=get_node_name, description='FQDN of this node'
    )
    NODE_EGRESS_IP: str = Field(
        '', description='Egress IP of this node'
    )
    NODE_EGRESS_NIC: str = Field(
        'eth0', description='NIC for outbound traffic'
    )
    NODE_MESH_IP: str = Field(
        '', description='Mesh network IP of this node'
    )
    NODE_MESH_NIC: str = Field(
        'eth0', description='NIC for virtual network mesh traffic'
    )
    STORAGE_PATH: str = Field(
        '/srv/shakenfist', description='Where on disk instances are stored'
    )

    LIBVIRT_USER: str = Field(
        'libvirt-qemu', description='Name of the libvirt user'
    )
    LIBVIRT_GROUP: str = Field(
        'libvirt-qemu', description='Name of the libvirt group'
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
    LOG_METHOD_TRACE: bool = Field(
        False, description='Add method name and module line number to log messages'
    )

    class Config:
        env_prefix = 'SHAKENFIST_'


config = SFConfig()
