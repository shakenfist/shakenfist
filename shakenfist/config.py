# Copyright 2019 Michael Still
import json
import os
import socket
import sys
from typing import NoReturn
from typing import Optional

from pydantic import AnyHttpUrl
from pydantic import Field
from pydantic_settings import BaseSettings


def get_node_name() -> str:
    return socket.getfqdn()


def load_cluster_config() -> None:
    """Load cluster-wide config from the database service.

    Builds a gRPC channel directly to avoid circular imports
    (database.py imports config.py). Falls back silently if the
    database service is unreachable, matching the old etcd
    tolerance.
    """
    db_ip = os.getenv('SHAKENFIST_DATABASE_NODE_IP')
    if not db_ip:
        return

    db_port = os.getenv('SHAKENFIST_DATABASE_API_PORT', '13005')

    try:
        import grpc
        from shakenfist.protos import database_pb2
        from shakenfist.protos import database_pb2_grpc

        channel = grpc.insecure_channel(f'{db_ip}:{db_port}')
        stub = database_pb2_grpc.DatabaseServiceStub(channel)
        request = database_pb2.ClusterConfigRequest()

        response = stub.GetClusterConfig(request, timeout=5)

        for entry in response.entries:
            value = json.loads(entry.value_json)
            os.environ['SHAKENFIST_%s' % entry.key_name] = str(value)

        channel.close()

    except Exception:
        # Match current behavior: silently ignore unavailable
        # database service. On fresh installs the database
        # service may not be running yet.
        return


class SFConfig(BaseSettings):
    ###################
    # Deployment Wide #
    ###################

    # API Options
    API_ASYNC_WAIT: int = Field(
        15,
        description=(
            'How long we wait for an async operation to complete  before '
            'returning to the user.'
        )
    )
    AUTH_SECRET_SEED: str = Field(
        '~~unconfigured~~',
        description='A random string to seed auth secrets with.'
    )
    API_TOKEN_DURATION: int = Field(
        15,
        description='How long in minutes an API token is valid for.'
    )
    API_ADVERTISED_HOST: str = Field(
        'localhost',
        description='The DNS name of the REST API host as advertised to users.'
    )
    API_ADVERTISED_BASE_PATH: str = Field(
        '/',
        description='The URL at API_ADVERTISED_HOST that the API is rooted at.'
    )
    API_ADVERTISED_HTTP_SCHEMES: str = Field(
        'http',
        description='Space separated list of schemes (http, https) for the API.'
    )

    # Monitoring Options
    EVENTLOG_METRICS_PORT: int = Field(
        13002,
        description='Where to expose internal metrics from the eventlog daemon.'
    )
    RESOURCES_METRICS_PORT: int = Field(
        13001,
        description='Where to expose internal metrics from the resources daemon.'
    )
    CLUSTER_METRICS_PORT: int = Field(
        13007,
        description='Where to expose internal metrics from the cluster daemon.'
    )

    # Scheduler Options
    SCHEDULER_CACHE_TIMEOUT: int = Field(
        5, description='How long the scheduler should cache things for.'
    )
    CPU_OVERCOMMIT_RATIO: float = Field(
        16, description='How many vCPUS per real CPU.'
    )
    RAM_OVERCOMMIT_RATIO: float = Field(
        3.0,
        description=(
            'How much vRAM per real unit of RAM, that is if Kernel Shared '
            'Memory (KSM) is enabled, how much to overcommit memory because '
            'of shared pages.'
        )
    )
    RAM_SYSTEM_RESERVATION: float = Field(
        2.0,
        description='How much RAM is reserved for the OS.'
    )
    MINIMUM_FREE_DISK: int = Field(
        20,
        description='Shaken Fist attempts to leave this amount of disk free.'
    )

    # Network Options
    FLOATING_NETWORK: str = Field(
        '192.168.20.0/24',
        description=(
            'Network block used for NAT egress from VMs and floating IPs.'
        )
    )
    NETWORK_NODE_IP: str = Field(
        '',
        description='Mesh IP of the node which will egress all traffic.',
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
    IP_DELETION_HALO_DURATION: int = Field(
        300,
        description='How long an IP is unusable for after being released.'
    )

    # Database Options
    CLEANER_DELAY: int = Field(
        3600,
        description=(
            'How long to wait before removing old data from the database.'
        )
    )
    CLUSTER_OPERATION_TARGET_RETENTION: int = Field(
        7 * 24 * 3600,
        description=(
            'How long (in seconds) to retain rows in the '
            'cluster_operation_targets history table for completed or '
            'aborted operations. Active (queued/preflight/executing) '
            'operations are never pruned regardless of age. Set to 0 '
            'to disable pruning.'
        )
    )
    CLUSTER_OP_STUCK_THRESHOLD: int = Field(
        30 * 60,
        description=(
            'Seconds a cluster operation work queue row may remain '
            'claimed before the cluster daemon reaper considers it '
            'stuck. Stuck rows are re-queued, or rejected if they '
            'have exceeded CLUSTER_OP_MAX_ATTEMPTS claims.'
        )
    )
    CLUSTER_OP_MAX_ATTEMPTS: int = Field(
        5,
        description=(
            'Maximum number of times a cluster operation work queue '
            'row may be claimed before the reaper rejects it and '
            'marks the underlying operation as errored.'
        )
    )
    NODE_CHECKIN_MAXIMUM: int = Field(
        120,
        description=(
            'How long to wait before declaring a node to be offline due to '
            'failure to check in.'
        )
    )

    # Image resolver options
    DOWNLOAD_URL_CIRROS: AnyHttpUrl = Field(
        (
            'http://download.cirros-cloud.net/%(vernum)s/'
            'cirros-%(vernum)s-x86_64-disk.img'
        ),
        description='Default download mirror for Cirros images.'
    )
    CHECKSUM_URL_CIRROS: AnyHttpUrl = Field(
        'http://download.cirros-cloud.net/%(vernum)s/MD5SUMS',
        description='Default checksum URL for Cirros images.'
    )
    LISTING_URL_CIRROS: AnyHttpUrl = Field(
        'http://download.cirros-cloud.net/',
        description='A URL listing all Cirros releases.'
    )

    IMAGE_DOWNLOAD_URL: AnyHttpUrl = Field(
        'https://images.shakenfist.com',
        description='Default download server for Shaken Fist images.'
    )

    MAX_IMAGE_TRANSFER_SECONDS: int = Field(
        1800,
        description=(
            'How long to wait for an image transfer to occur before giving up.'
        )
    )

    COMPRESS_SNAPSHOTS: bool = Field(
        True,
        description='Compress snapshots taken of instances.'
    )

    SNAPSHOTS_DEFAULT_TO_THIN: bool = Field(
        False,
        description=(
            'Whether snapshots are thin (just changes from base image) or '
            'thick.'
        )
    )

    # Artifact options
    ARTIFACT_MAX_VERSIONS_DEFAULT: int = Field(
        3,
        description='Default number of versions to keep within an artifact.'
    )
    ARCHIVE_INSTANCE_CONSOLE_DURATION: int = Field(
        0,
        description=(
            'The number of days to archive instance consoles for after '
            'deletion.'
        )
    )

    # Event options
    EVENTLOG_SUPPRESS_GRPC: bool = Field(
        False, description=(
            'Only intended for testing. Always use etcd for event delivery.'
        )
    )
    EVENTLOG_NODE_IP: str = Field(
        '',
        description='Mesh IP of the node which stores event logs.',
    )
    EVENTLOG_API_PORT: int = Field(
        13003,
        description='Port for the internal eventlog gRPC API.'
    )

    # Database Service Options
    DATABASE_NODE_IP: str = Field(
        '',
        description='Mesh IP of the node running the database service.'
    )
    DATABASE_API_PORT: int = Field(
        13005,
        description='Port for the internal database gRPC API.'
    )
    DATABASE_METRICS_PORT: int = Field(
        13006,
        description='Prometheus metrics port for the database daemon.'
    )
    USAGE_EVENT_FREQUENCY: int = Field(
        60,
        description='How frequently to collect usage events.'
    )

    MAX_AUDIT_EVENT_AGE: int = Field(
        3600 * 24 * 90,
        description='How long to retain audit events.'
    )
    MAX_MUTATE_EVENT_AGE: int = Field(
        3600 * 24 * 90,
        description='How long to retain mutate events.'
    )
    MAX_STATUS_EVENT_AGE: int = Field(
        3600 * 24 * 7,
        description='How long to retain status events.'
    )
    MAX_USAGE_EVENT_AGE: int = Field(
        3600 * 24 * 30,
        description='How long to retain usage events.'
    )
    MAX_RESOURCES_EVENT_AGE: int = Field(
        3600 * 24 * 7,
        description='How long to retain resources events.'
    )
    MAX_PRUNE_EVENT_AGE: int = Field(
        3600 * 24 * 30,
        description='How long to retain prune events.'
    )
    MAX_HISTORIC_EVENT_AGE: int = Field(
        3600 * 24 * 90,
        description='How long to retain historic events.'
    )

    MAX_API_REQUEST_EVENT_AGE: int = Field(
        3600 * 24, description=(
            'How long to retain events for the api-request object type. These '
            'events are very verbose, so are special cased for cleanup.'
        )
    )

    # Blob options
    BLOB_REPLICATION_FACTOR: int = Field(
        2,
        description='How many copies of each blob we like to have.'
    )
    CHECKSUM_VERIFICATION_FREQUENCY: int = Field(
        24 * 3600,
        description='How often we verify blob checksums, in seconds.'
    )
    BLOB_TRANSCODE_MAXIMUM_IDLE_TIME: int = Field(
        24 * 3600,
        description=(
            'How long we keep a unused cached transcode of a blob before '
            'reaping.'
        )
    )

    # Other options
    ZONE: str = Field(
        'shakenfist',
        description=(
            'What nova called an availability zone. Note that this '
            'value is also used as the issuer name for JWT authentication '
            'tokens and should be unique to each Shaken Fist cluster. '
            'This value is also used as the DNS domain for DNS within '
            'virtual networks.'
        )
    )

    #################
    # Node Specific #
    #################

    NODE_IS_HYPERVISOR: bool = Field(
        False,
        description=(
            'True if this node is a hypervisor. This controls if VMs are '
            'started on this node or not.'
        )
    )
    NODE_IS_NETWORK_NODE: bool = Field(
        False,
        description='True if this node is the network node.'
    )
    NODE_IS_EVENTLOG_NODE: bool = Field(
        False,
        description='True if this node is a the eventlog node.'
    )

    DISK_BUS: str = Field(
        'virtio',
        description=(
            'The bus to use for disk devices. One of virtio, scsi, '
            'usb, ide, etc. See libvirt docs for full list of options.'
        )
    )
    NODE_NAME: str = Field(
        default_factory=get_node_name, description='FQDN of this node.'
    )
    NODE_UUID: Optional[str] = Field(
        None,
        description=(
            'UUID of this node. Auto-assigned on first run and '
            'persisted to {STORAGE_PATH}/node_uuid. Can also be '
            'set via SHAKENFIST_NODE_UUID environment variable.'
        )
    )
    NODE_EGRESS_IP: str = Field(
        '',
        description='Egress IP of this node.'
    )
    NODE_EGRESS_NIC: str = Field(
        'eth0',
        description='NIC for outbound traffic.'
    )
    NODE_MESH_IP: str = Field(
        '',
        description='Mesh network IP of this node.'
    )
    NODE_MESH_NIC: str = Field(
        'eth0',
        description='NIC for virtual network mesh traffic.'
    )
    STORAGE_PATH: str = Field(
        '/srv/shakenfist',
        description='Where on disk instances are stored.'
    )

    LIBVIRT_USER: str = Field(
        'libvirt-qemu',
        description='Name of the libvirt user.'
    )
    LIBVIRT_GROUP: str = Field(
        'libvirt-qemu',
        description='Name of the libvirt group.'
    )
    LIBVIRT_LOG_PATH: str = Field(
        '/var/log/libvirt/qemu',
        description='Path to libvirt logs.'
    )

    # Logging
    LOGLEVEL_API: str = 'info'
    LOGLEVEL_CLEANER: str = 'info'
    LOGLEVEL_MAIN: str = 'info'
    LOGLEVEL_NET: str = 'info'
    LOGLEVEL_RESOURCES: str = 'info'
    LOGLEVEL_SIDECHANNEL: str = 'info'
    LOGLEVEL_QUEUES: str = 'info'

    # etcd (retained only for DATA_MIGRATIONS drain — remove in next release)
    ETCD_HOST: str = Field(
        '',
        description=(
            'Hostname or IP of the etcd host to query for drain migrations. '
            'Retained only for one-time migration of legacy clusters.'
        )
    )

    # MariaDB
    MARIADB_HOST: str = Field(
        '',
        description='Hostname or IP of the MariaDB server.'
    )
    MARIADB_PORT: int = Field(
        3306,
        description='Port for the MariaDB server.'
    )
    MARIADB_USER: str = Field(
        'shakenfist',
        description='Username for MariaDB connections.'
    )
    MARIADB_PASSWORD: str = Field(
        '',
        description='Password for MariaDB connections.'
    )
    MARIADB_DATABASE: str = Field(
        'shakenfist',
        description='Database name for Shaken Fist data.'
    )

    class Config:
        env_prefix = 'SHAKENFIST_'


load_cluster_config()
config = SFConfig()


def _config_failure(failures: list[str]) -> NoReturn:
    print('Configuration failed validation!')
    print()
    print('Configuration as read:')
    for key, value in config.dict().items():
        print(f'    {key} = {value}')
    print()
    print('Errors:')
    for failure in failures:
        print('    * %s' % failure)
    sys.exit(1)


def verify_config(skip_auth_seed: bool = False) -> None:
    failures: list[str] = []

    if not skip_auth_seed:
        if config.AUTH_SECRET_SEED == '~~unconfigured~~':
            failures.append('You must configure AUTH_SECRET_SEED!')

    if failures:
        _config_failure(failures)
