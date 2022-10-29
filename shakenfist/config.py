# Copyright 2019 Michael Still

from etcd3gw.client import Etcd3Client
from etcd3gw.exceptions import ConnectionFailedError
import json
from pydantic import (
    BaseSettings,
    Field,
    SecretStr,
    AnyHttpUrl,
)
import socket

from shakenfist import exceptions


def get_node_name():
    return socket.getfqdn()


def etcd_settings(_settings):
    try:
        value = Etcd3Client(
            host='localhost', port=2379, protocol='http', api_path='/v3beta/').get(
                '/sf/config', metadata=True)
        if value is None or len(value) == 0:
            return {}
        return json.loads(value[0][0])

    except ConnectionFailedError:
        # NOTE(mikal): I'm not sure this is the right approach, as it might cause
        # us to silently ignore config errors. However, I can't just mock this away
        # in tests because this code runs before the mocking occurs.
        return {}


class SFConfig(BaseSettings):
    ###################
    # Deployment Wide #
    ###################

    # API Options
    API_PORT: int = Field(13000, description='Port for the REST API')
    API_TIMEOUT: int = Field(
        30,
        description='How long gunicorn processes can use for a single request'
    )
    API_ASYNC_WAIT: int = Field(
        15,
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
            '/srv/shakenfist/venv/bin/gunicorn --workers %(workers)d --bind 0.0.0.0:%(port)d '
            '--log-syslog --log-syslog-prefix sf --timeout %(timeout)s --name "%(name)s" '
            '--pid /var/run/sf/gunicorn.pid shakenfist.external_api.app:app'
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
        3.0,
        description='How much vRAM per real unit of RAM, that is if Kernel '
                    'Shared Memory (KSM) is enabled, how much to overcommit '
                    'memory because of shared pages.'
    )
    RAM_SYSTEM_RESERVATION: float = Field(
        5.0, description='How much RAM is reserved for the OS'
    )
    MINIMUM_FREE_DISK: int = Field(
        20, description='Shaken Fist attempts to leave this amount of disk free'
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
        120,
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

    IMAGE_DOWNLOAD_URL: AnyHttpUrl = Field(
        'https://images.shakenfist.com',
        description='Default download server for Shaken Fist images'
    )

    MAX_IMAGE_TRANSFER_SECONDS: int = Field(
        1800, description='How long to wait for an image transfer to occur before giving up'
    )

    COMPRESS_SNAPSHOTS: bool = Field(
        True, description='Compress snapshots taken of instances'
    )

    SNAPSHOTS_DEFAULT_TO_THIN: bool = Field(
        False, description='Whether snapshots are thin (just changes from base image) or thick'
    )

    # Artifact options
    ARTIFACT_MAX_VERSIONS_DEFAULT: int = Field(
        3, description='Default number of versions to keep within an artifact'
    )

    # Event options
    EVENTLOG_NODE_IP: str = Field(
        '', description='Mesh IP of the node which stores event logs',
    )
    USAGE_EVENT_FREQUENCY: int = Field(
        60, description='How frequently to collect usage events.'
    )

    # Blob options
    BLOB_REPLICATION_FACTOR: int = Field(
        2, description='How many copies of each blob we like to have.'
    )
    MAX_CONCURRENT_BLOB_TRANSFERS: int = Field(
        20, description='How many concurrent blob transfers we can have queued.'
    )
    BLOB_TRANSCODE_MAXIMUM_IDLE_TIME: int = Field(
        24 * 3600,
        description=('How long we keep a unused cached transcode of a blob '
                     'before reaping.')
    )

    # Other options
    ZONE: str = Field(
        'shakenfist', description='What nova called an availability zone'
    )

    #################
    # Node Specific #
    #################

    NODE_IS_ETCD_MASTER: bool = Field(
        False, description='True if this node is an etcd master. This controls '
                           'attempts to compact the master database.'
    )
    NODE_IS_HYPERVISOR: bool = Field(
        False, description='True if this node is a hypervisor. This controls if '
                           'VMs are started on this node or not.'
    )
    NODE_IS_NETWORK_NODE: bool = Field(
        False, description='True if this node is the network node.'
    )
    NODE_IS_EVENTLOG_NODE: bool = Field(
        False, description='True if this node is a the eventlog node.'
    )

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
    LIBVIRT_LOG_PATH: str = Field(
        '/var/log/libvirt/qemu', description='Path to libvirt logs'
    )

    ENABLE_EVENTS: bool = Field(
        True, description='Record events about objects in the database. '
                          'Consider disabling if you have etcd performance issues.'
    )

    # Logging
    SLOW_LOCK_THRESHOLD: float = 5.0
    LOGLEVEL_API: str = 'info'
    LOGLEVEL_CLEANER: str = 'info'
    LOGLEVEL_MAIN: str = 'info'
    LOGLEVEL_NET: str = 'info'
    LOGLEVEL_RESOURCES: str = 'info'
    LOGLEVEL_SIDECHANNEL: str = 'info'
    LOGLEVEL_QUEUES: str = 'info'
    LOG_METHOD_TRACE: bool = Field(
        False, description='Add method name and module line number to log messages'
    )

    # etcd
    ETCD_HOST: str = Field(
        '', description='Hostname or IP of the etcd host to query'
    )
    EXCESSIVE_ETCD_CACHE_LOGGING: bool = Field(
        False, description='Record detailed information about etcd cache performance.'
    )

    class Config:
        env_prefix = 'SHAKENFIST_'

        @classmethod
        def customise_sources(cls, init_settings, env_settings, file_secret_settings):
            return init_settings, etcd_settings, env_settings, file_secret_settings


config = SFConfig()
if config.ETCD_HOST == '':
    raise exceptions.NoEtcd(
        'Shaken Fist is configured incorrectly, you _must_ configure '
        'at least ETCD_HOST!')
