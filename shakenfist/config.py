# Copyright 2019 Michael Still
import json
import os
import socket
import sys
from typing import Annotated
from typing import NoReturn
from typing import Optional

from pydantic import AnyHttpUrl
from pydantic import BeforeValidator
from pydantic import Field
from pydantic_settings import BaseSettings


def get_node_name() -> str:
    return socket.getfqdn()


def _parse_comma_separated_hosts(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return value


def load_cluster_config() -> None:
    """Load cluster-wide config into environment variables.

    If MARIADB_HOST is set, this process has direct MariaDB
    access available and uses it -- this path is used by
    sf-database itself and by `sf-ctl ensure-mariadb-schema`.
    Direct access is preferred when available because it
    avoids a self-loop through the sf-database gRPC tier.

    Otherwise, if MARIADB_GATEWAY_HOSTS is set, the process
    reaches the sf-database tier via gRPC. Phase 3 of
    PLAN-byo-mariadb will reshape this into a client-side
    load-balanced channel; today we connect to the first
    endpoint in the list.

    Falls back silently on any failure so that fresh-install
    nodes with no database daemon yet can still start.

    Built inline to avoid circular imports (database.py and
    mariadb.py both import config.py).
    """
    mariadb_host = os.getenv('SHAKENFIST_MARIADB_HOST')
    if mariadb_host:
        try:
            import sqlalchemy as sa

            port = int(os.getenv('SHAKENFIST_MARIADB_PORT', '3306'))
            user = os.getenv('SHAKENFIST_MARIADB_USER', 'shakenfist')
            password = os.getenv('SHAKENFIST_MARIADB_PASSWORD', '')
            database = os.getenv(
                'SHAKENFIST_MARIADB_DATABASE', 'shakenfist')

            url = (
                f'mariadb+mysqldb://{user}:{password}'
                f'@{mariadb_host}:{port}/{database}'
            )
            engine = sa.create_engine(url)
            with engine.connect() as conn:
                rows = conn.execute(sa.text(
                    'SELECT key_name, value_json FROM cluster_config'
                )).fetchall()

            for key_name, value_raw in rows:
                # Raw SQL gets the stored string; JSON-decode so we
                # match the gRPC path's behavior.
                try:
                    value = json.loads(value_raw)
                except (TypeError, ValueError):
                    value = value_raw
                if isinstance(value, (dict, list)):
                    value = json.dumps(value)
                os.environ['SHAKENFIST_%s' % key_name] = str(value)
            return

        except Exception:
            # Table may not exist yet on a fresh install; or
            # MariaDB may not be reachable. Fall through silently
            # so bootstrap keeps working.
            return

    hosts_raw = os.getenv('SHAKENFIST_MARIADB_GATEWAY_HOSTS', '')
    if not hosts_raw:
        return
    hosts = [h.strip() for h in hosts_raw.split(',') if h.strip()]
    if not hosts:
        return

    db_port = os.getenv('SHAKENFIST_MARIADB_GATEWAY_PORT', '13005')

    try:
        from shakenfist.protos import database_pb2
        from shakenfist.protos import database_pb2_grpc
        from shakenfist.util.grpc_channel import make_database_channel

        channel = make_database_channel(hosts, int(db_port))
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
        60,
        description=(
            'Default deadline for ``BaseClusterOperation.raise_for_error()``: '
            'how long an internal caller (or REST handler) will block waiting '
            'for an enqueued cluster operation to reach a terminal state. The '
            '15 s value used through phases 1-9 of the network-facade refactor '
            'was too aggressive for the cascading wait-on-op patterns -- under '
            'cluster load a ``net_op`` that took 16 s to dispatch tripped '
            'a 15 s waiter even though the op itself was healthy. Long-running '
            'startup paths still override this explicitly.'
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
    MAINTAIN_QUEUE_DEPTH_THRESHOLD: int = Field(
        50,
        description=(
            'Maintain pass is skipped if the combined depth of the network '
            'queue family exceeds this threshold. Prevents piling '
            'reconciliation requests on top of an already backed-up queue.'
        )
    )
    MAINTAIN_RECONCILE_COOLDOWN_SECONDS: int = Field(
        60,
        description=(
            'If the most recent terminal reconciliation op for a network '
            'ended in ERROR within this many seconds, maintain skips '
            'enqueueing another reconciliation for that network. Lets a '
            'previous failure breathe before retrying.'
        )
    )
    MAINTAIN_RECONCILE_CIRCUIT_K: int = Field(
        5,
        description=(
            'If the last K terminal reconciliations for a network all '
            'ended in ERROR, maintain quiesces that network with an '
            'operator-visible event. The next maintain pass naturally '
            're-checks once a fresh reconciliation succeeds, which closes '
            'the circuit.'
        )
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

    # Database Service Options
    MARIADB_GATEWAY_HOSTS: Annotated[
        list[str], BeforeValidator(_parse_comma_separated_hosts)
    ] = Field(
        default_factory=list,
        description=(
            'List of sf-database tier endpoints clients connect to. A '
            'single-instance deployment sets this to a one-element list. '
            'When supplied via environment variable, comma-separated '
            'values are parsed into the list.'
        )
    )
    MARIADB_GATEWAY_PORT: int = Field(
        13005,
        description='Port the sf-database gRPC service listens on.'
    )
    MARIADB_GATEWAY_METRICS_PORT: int = Field(
        13006,
        description='Prometheus metrics port for the sf-database daemon.'
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
