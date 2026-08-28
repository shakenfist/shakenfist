# Copyright 2019 Michael Still
import json
import os
import re
import socket
import sys
from collections.abc import Iterator
from typing import Annotated
from typing import Any
from typing import Literal
from typing import NoReturn
from typing import Optional

from pydantic import AnyHttpUrl
from pydantic import BeforeValidator
from pydantic import Field
from pydantic import SecretStr
from pydantic_settings import BaseSettings
from pydantic_settings import NoDecode


# Configuration keys whose values are secrets, matched by name.
#
# Three callers need the same answer and must not disagree about it,
# which is why this lives here rather than in any one of them: sf-ctl's
# show-config redacts matches by default so its output is safe to log
# (and set-config avoids echoing their values), and both sites which
# dump every configuration item -- the daemon startup banner in
# daemons/queues/startup_tasks.py and _config_failure() below -- render
# through redacted_config_items(). The banner writes its lines at INFO,
# and INFO and above is shipped to Loki, so a secret which reaches it
# leaves the cluster. The same reasoning puts handles_credentials() in
# external_api/base.py rather than in app.py.
#
# Match generously -- under-matching leaks a credential, while
# over-matching merely hides a value behind --show-secrets. It does in
# fact over-match today, catching the integer API_TOKEN_DURATION,
# FEDERATION_MAX_TOKEN_BYTES and KERBSIDE_TOKEN_DURATION, which is the
# harmless direction.
#
# This is deliberately belt-and-braces with the SecretStr types on the
# secret-carrying fields themselves. The dumping sites iterate every
# configuration item rather than named ones, so a name check is what
# covers a secret option which does not exist yet; the types are what
# cover every other way a field might be stringified. Removing either
# one re-opens a hole the other does not close. See
# docs/plans/PLAN-auth-federation-phase-06-secret-types.md.
SECRET_CONFIG_KEY_RE = re.compile(
    r'(SECRET|PASSWORD|PASSPHRASE|TOKEN|AUTH_HEADER|_SEED$|_KEY$)')


# The sentinel AUTH_SECRET_SEED carries until an operator sets one, which
# verify_config() refuses to start on.
#
# Named rather than written twice because the field is a SecretStr, and
# SecretStr('x') == 'x' is False. Comparing the field against a bare
# string literal is therefore always false, silently, which would turn
# that refusal into a cluster happily signing tokens with the shipped
# default. The comparison must unwrap, and there are exactly two places
# which legitimately read this value: verify_config() below, and
# external_api/app.py where it becomes JWT_SECRET_KEY.
UNCONFIGURED_AUTH_SECRET_SEED = '~~unconfigured~~'


def get_node_name() -> str:
    return socket.getfqdn()


def _parse_comma_separated_hosts(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return value


def load_cluster_config() -> None:
    """Load cluster-wide config into environment variables.

    If MARIADB_HOST is set, this process has direct MariaDB
    access available and uses it. Direct access is preferred
    when available because it avoids a self-loop through the
    sf-database gRPC tier.

    NOTE: this is the one MariaDB dispatch in the codebase which
    is not per-caller. Everywhere else only the daemons named in
    ``mariadb.DIRECT_MARIADB_CALLERS`` may act on MARIADB_HOST;
    here any process which can see it uses it, so on a
    database-tier node every daemon makes one direct MariaDB
    connection at startup. That is a deliberate exception rather
    than an oversight: this runs at ``shakenfist.config`` import
    time, before any entry point has been able to call
    ``set_caller_identity()``, so there is no identity to
    consult. The cost is one connection per daemon start, which
    is invisible to the tier's metrics and connection
    accounting. Anything which needs to be visible there must go
    through mariadb.py rather than being added here.

    Otherwise, if MARIADB_GATEWAY_HOSTS is set, the process
    reaches the sf-database tier via gRPC. Phase 3 of
    PLAN-byo-mariadb will reshape this into a client-side
    load-balanced channel; today we connect to the first
    endpoint in the list.

    Falls back silently on any failure so that fresh-install
    nodes with no database daemon yet can still start.

    Built inline to avoid a circular import (mariadb.py imports
    config.py, so the bootstrap channel cannot route through it).
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
    API_DRAIN_GRACE: int = Field(
        25,
        description=(
            'On SIGTERM, how long sf-api keeps serving while /readyz reports '
            '503 so the load balancer drops this node on its next probe before '
            'workers stop. systemd TimeoutStopSec must exceed this plus '
            'gunicorn graceful_timeout.'
        )
    )
    # SecretStr so that dumping the configuration -- which the sf-queues
    # startup banner does for every field -- renders asterisks rather
    # than the seed every JWT in the cluster is signed with. Read the
    # real value with .get_secret_value(); there are two such reads,
    # both listed on UNCONFIGURED_AUTH_SECRET_SEED below.
    AUTH_SECRET_SEED: SecretStr = Field(
        SecretStr(UNCONFIGURED_AUTH_SECRET_SEED),
        description='A random string to seed auth secrets with.'
    )
    API_TOKEN_DURATION: int = Field(
        15,
        description='How long in minutes an API token is valid for.'
    )
    # Literal rather than str so a typo fails at config load. The
    # setting exists to be flipped, and 'Enforce' or 'enforced'
    # silently meaning warn is exactly the operator error that would
    # otherwise go unnoticed until an incident.
    API_VALIDATION_MODE: Literal['off', 'warn', 'enforce'] = Field(
        'warn',
        description=(
            'What the request validation layer does with input which does '
            'not match an endpoint\'s published parameter declarations. '
            '"warn" logs what it would have rejected and changes nothing, '
            'which is phase 3 of PLAN-api-input-validation; "enforce" '
            'answers 400 (except for missing-required findings, which are '
            'recorded and never enforced); "off" disables the layer '
            'entirely, as a safety valve against unexpected log volume. '
            'Leave this at "warn" until the warn log is understood -- see '
            'docs/developer_guide/writing_an_endpoint.md.'
        )
    )
    AGENT_OPERATION_DEFAULT_DEADLINE: int = Field(
        600,
        description=(
            'The wall-clock budget in seconds applied to an agent operation '
            'whose creator did not ask for one. The API server converts it '
            'to an absolute timestamp at request receipt, so queue time and '
            'preflight time both count against it. A client may pass an '
            'explicit deadline_seconds of 0 to ask for no wall-clock '
            'deadline at all, in which case nothing bounds the '
            'operation. Enforced in three places: an expired operation '
            'is retired when the sidechannel daemon looks for work, '
            'when a preflight task promotes it to queued, and by the '
            'executor while it runs. An operation which runs out of '
            'budget moves to the expired state, which is distinct from '
            'error. This replaced a hardcoded 900 second backstop in '
            'the sidechannel daemon, so the effective default is now '
            'tighter than it used to be.'
        )
    )
    AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT: int = Field(
        30,
        description=(
            'Seconds without forward progress which are fatal to an agent '
            'operation, applied to operations containing a progress-capable '
            'command when the creator did not ask for one. A client may '
            'pass an explicit progress_timeout_seconds of 0 to disable it. '
            'The value comes from the phase 0 measurement in '
            'PLAN-agent-operation-deadlines-phase-00-decisions.md: across 50 '
            'transfers in five merge-queue CI runs the worst complete '
            'transfer was 625 MB in 2.83 s and 48 of 50 finished in under '
            '0.44 s, so 30 seconds is roughly ten times the worst total '
            'duration -- let alone any gap within one -- while detecting the '
            '#3516 sidechannel wedge thirty times faster than the 900 '
            'second constant it replaced. Enforced by the sidechannel '
            'executor, and only while a command which can actually '
            'report progress is in flight -- an execute of a long '
            'running command reports nothing until it finishes and is '
            'never subject to it. An operation which stalls moves to '
            'the expired state, which is distinct from error.'
        )
    )
    FEDERATION_MAX_TOKEN_BYTES: int = Field(
        16384,
        description=(
            'The largest federated exchange request body accepted, in '
            'bytes. Refused before any parsing, because /auth/federated is '
            'unauthenticated and parsing an attacker-sized JWT is work '
            'done on their behalf. Comfortably above a real identity '
            'token, which is typically one to two kilobytes.'
        )
    )
    FEDERATION_JWKS_CACHE_SECONDS: int = Field(
        300,
        description=(
            'How long in seconds a trusted issuer\'s JWKS is cached before '
            'being refetched. Lower values shorten the window in which a '
            'key the issuer has revoked is still accepted; higher values '
            'reduce load on the issuer. An unknown key id always triggers '
            'an immediate refetch regardless of this setting, so raising '
            'it does not delay recognising a newly rotated key.'
        )
    )
    FEDERATION_JWKS_FETCH_TIMEOUT_SECONDS: int = Field(
        5,
        description=(
            'How long in seconds to wait for a trusted issuer\'s JWKS '
            'endpoint before giving up. The fetch happens while holding '
            'that issuer\'s refetch lock, so this is also the longest one '
            'unreachable identity provider can pin an API worker. PyJWT '
            'defaults to 30 seconds, which is long enough that a provider '
            'blackholing traffic exhausts the worker pool.'
        )
    )
    FEDERATION_JWKS_CA_BUNDLE: str = Field(
        '',
        description=(
            'Path to a PEM file of extra certificate authorities to trust '
            'when fetching a trusted issuer\'s JWKS. Empty, the default, '
            'means the system trust store alone. Set this when the identity '
            'provider is behind a private CA -- a self hosted Authentik or '
            'Keycloak usually is. The anchors are added to the system ones '
            'rather than replacing them, so configuring this does not stop '
            'a public issuer like GitHub from verifying. It also does not '
            'relax anything else: jwks_uri must still be https, and a '
            'certificate which chains to neither set is still refused.'
        )
    )
    FEDERATION_RATE_LIMIT_PER_MINUTE: int = Field(
        60,
        description=(
            'How many federated exchange attempts one source address may '
            'make per minute before being refused with a 429. Counted in '
            'MariaDB rather than in the worker, so the limit is cluster '
            'wide rather than per gunicorn worker. Set to 0 to disable '
            'rate limiting entirely. Note that behind a reverse proxy '
            'which does not rewrite the source address every request '
            'appears to come from the proxy, which makes this a single '
            'global limit -- size it accordingly.'
        )
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
    OBJECT_CACHE_TTL_IMMUTABLE: int = Field(
        300,
        description=(
            'Seconds to cache the static values of objects that have no '
            'post-creation writer (instance, network, networkinterface, '
            'agentoperation). Only cross-process deletion can make these '
            'stale, so the TTL can be long. 0 disables this cache tier.'
        )
    )
    OBJECT_CACHE_TTL_MUTABLE: int = Field(
        30,
        description=(
            'Seconds to cache the static values of objects whose row can be '
            'rewritten by an online version upgrade (node, blob, artifact, '
            'upload, dnsmasq, namespace). Kept short to bound cross-process '
            'upgrade staleness. 0 disables this cache tier.'
        )
    )
    SCHEDULER_TARGET_LOAD: float = Field(
        0.75,
        description=(
            'The target sustained load per schedulable thread (logical '
            'CPU) used to weight candidate selection during scheduling. '
            'Nodes with more headroom below this target draw a larger '
            'share of new instances.'
        )
    )
    SCHEDULER_DEMAND_PER_VCPU: float = Field(
        0.6,
        description=(
            'The expected-demand feedforward term for scheduler '
            'reservations: each placement adds this much anticipated '
            'load per requested vCPU to the target node\'s '
            'expected_demand, closing the actuation-to-observation gap '
            'for correlated placement bursts before their load shows in '
            'measured metrics. The capacity reconciler decays each '
            'placement\'s contribution linearly to zero over '
            'SCHEDULER_DEMAND_DECAY_SECONDS of instance age. The '
            'default is the conservative burst figure measured on a '
            'CI-dominated cluster, where steady-state demand ran '
            '0.12-0.35 load per allocated vCPU and a burst peak was '
            'estimated at 0.6; the burst figure is the relevant one '
            'because bursts are what the term exists to spread. A node '
            'is refused only once its measured load plus expected '
            'demand exceeds SCHEDULER_TARGET_LOAD per schedulable '
            'thread, so this value sets how many '
            'placements a quiet node absorbs before the scheduler '
            'prefers its neighbours.'
        )
    )
    SCHEDULER_DEMAND_DECAY_SECONDS: int = Field(
        600,
        description=(
            'How long, in seconds, a placement contributes to a node\'s '
            'expected_demand. Each placement starts at vcpus x '
            'SCHEDULER_DEMAND_PER_VCPU and the capacity reconciler '
            'decays that contribution linearly to zero over this window '
            'of instance age, by which time the real load is assumed to '
            'be visible in measured metrics. The default is a '
            'provisional seed pending the scheduler reservations '
            'phase 0 step 3 data analysis.'
        )
    )
    CPU_OVERCOMMIT_RATIO: float = Field(
        3.0,
        description=(
            'How many vCPUs to admit per schedulable logical CPU (thread). '
            'The schedulable thread count is published by the resources '
            'daemon and excludes the threads reserved by '
            'NODE_CPU_RESERVATION_THREADS. The '
            'default was measured on a CI-dominated cluster; the historic '
            'default of 16 assumed many mostly-idle instances and in '
            'practice never rejected a node.'
        )
    )
    RAM_OVERCOMMIT_RATIO: float = Field(
        3.0,
        description=(
            'How much vRAM per real unit of RAM, that is if Kernel Shared '
            'Memory (KSM) is enabled, how much to overcommit memory because '
            'of shared pages. The default of 3.0 assumes KSM deduplicates '
            'most guest memory, which holds only for fleets of many '
            'near-identical, mostly-idle guests. Workloads which dirty '
            'most of their allocation with unique pages (CI, databases, '
            'container hosts) get little KSM profit and should run much '
            'closer to 1.0-1.25, or the hosts will swap and eventually '
            'OOM-kill instances.'
        )
    )
    SCHEDULER_DISK_OVERCOMMIT: float = Field(
        5.0,
        description=(
            'Disk capacity claims are virtual disk sizes, which heavily '
            'over-claim actual usage for sparse images. This ratio '
            'multiplies the free-space headroom term of the node disk '
            'limit derivation, so admission can overcommit empty space '
            'while a genuinely full disk still admits nothing.'
        )
    )
    NODE_RAM_RESERVATION_GB: float = Field(
        2.0,
        description=(
            'How much RAM (in GB) is reserved for the operating system and '
            'host-level system services on this node. This is a single '
            'absolute per-node value; Ansible templates it per host '
            '(folding in any historical infra-role bump) and an operator '
            'may override it from the inventory.'
        )
    )
    NODE_CPU_RESERVATION_THREADS: int = Field(
        2,
        description=(
            'How many hardware threads (not physical cores) are reserved '
            'for the operating system and host-level system services on '
            'this node. This is a single absolute per-node value; Ansible '
            'templates it per host and an operator may override it from '
            'the inventory.'
        )
    )
    NODE_DISK_RESERVATION_GB: float = Field(
        20.0,
        description=(
            'How much free disk (in GB) to keep on every filesystem the '
            'resources daemon tracks on this node. A single absolute '
            'per-node value that Ansible templates per host and an '
            'operator may override from the inventory.'
        )
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
    NETWORK_OPERATION_WORKERS: int = Field(
        4,
        description=(
            'Number of concurrent network cluster-operation worker threads '
            'in the net daemon. Operations are partitioned across workers '
            'by target network, so ops for the same network always execute '
            'in order on the same worker while different networks proceed '
            'in parallel. Set to 1 to restore fully serial execution.'
        )
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
    MAINTAIN_STRAY_VXLAN_GRACE_SECONDS: int = Field(
        300,
        description=(
            'How long an on-host vxlan device which matches no network '
            'this node should be on must persist before maintain acts on '
            'it. The grace period exists so devices seen mid-create or '
            'mid-delete are not mistaken for strays. Lowering it makes '
            'the functional test for stray reaping faster and is not '
            'otherwise useful.'
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
    NAMESPACE_KEY_REAP_GRACE: int = Field(
        3600,
        description=(
            'How long (in seconds) after a namespace key expires before '
            'the cluster daemon soft deletes it. An expired key stops '
            'authenticating immediately regardless of this setting; the '
            'grace period only delays the tidy up, so that a key which '
            'has just lapsed is still visible to an operator wondering '
            'why their automation broke. Set to 0 to disable reaping, '
            'in which case expired keys are retained forever.'
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
    # NoDecode prevents pydantic-settings from attempting to JSON-decode
    # the env value before the BeforeValidator runs. Without it, a bare
    # comma-separated string like "10.0.0.10" raises JSONDecodeError
    # ("Extra data") because the source treats list[str] as a complex
    # type and tries json.loads() first.
    MARIADB_GATEWAY_HOSTS: Annotated[
        list[str], NoDecode, BeforeValidator(_parse_comma_separated_hosts)
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
    DATABASE_DRAIN_GRACE: int = Field(
        10,
        description=(
            'On shutdown, how many seconds sf-database lets in-flight gRPC '
            'calls finish (having already flipped its health status to '
            'NOT_SERVING and stopped accepting new calls) before it forces '
            'the server down. A cap, not a fixed delay: the server stops as '
            'soon as the last in-flight call ends. Must stay below the '
            'generic sf.service TimeoutStopSec (30s) with margin, or systemd '
            'SIGKILLs the daemon mid-drain.'
        )
    )

    # Loki log shipping options
    LOKI_BASE_URL: str = Field(
        '',
        description=(
            'Base URL of the Loki instance log lines are shipped to (for '
            'example "http://loki.example.com:3100"). When empty, log '
            'shipping is dormant and the library\'s per-module syslog '
            'handlers remain in place (logs go to /dev/log locally).'
        )
    )
    LOKI_TENANT: str = Field(
        '',
        description=(
            'Loki tenant id sent as the X-Scope-OrgID header. Only sent '
            'when non-empty (single-tenant Loki deployments leave this '
            'blank).'
        )
    )
    # A SecretStr, so "treat as a secret" is enforced by the type rather
    # than left as an instruction to the next reader. Unwrapped once,
    # where the push request's headers are assembled.
    LOKI_AUTH_HEADER: SecretStr = Field(
        SecretStr(''),
        description=(
            'Opaque value sent verbatim as the Authorization header on '
            'every Loki push (for example "Bearer <token>" or a Basic '
            'credential). Only sent when non-empty. Treat as a secret: '
            'this value is never logged.'
        )
    )
    LOG_EVENTS_TO_LOKI: bool = Field(
        True,
        description=(
            'When True, the "Added event" diagnostic log line is emitted '
            'so events also flow to the log stream. The authoritative '
            'event record is always written to MariaDB regardless of this '
            'setting.'
        )
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
    MAX_HEALTH_EVENT_AGE: int = Field(
        3600 * 24 * 90,
        description=(
            'How long to retain node resource-health events. This must exceed '
            'the longest time a node is expected to sit in the error state: '
            'the diagnosis the cluster cascade reads back lives in this event, '
            'so pruning it while the node is still errored would strand the '
            'cascade after a cluster-daemon restart.')
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
    KERBSIDE_URL: str = Field(
        '',
        description=(
            "The kerbside deployment's public base URL, for example "
            'https://kerbside.example.com. An empty value disables the VDI '
            'console proxy integration. This value is also the token '
            'audience.'
        )
    )
    KERBSIDE_TOKEN_DURATION: int = Field(
        300,
        description=(
            'Lifetime in seconds of a minted Kerbside VDI console token.'
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
    NODE_IS_DATABASE_NODE: bool = Field(
        False,
        description=(
            'True if this node is part of the database tier, that is it '
            'runs sf-database and has direct access to MariaDB.'
        )
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

    NODE_HEALTH_CHECK_INTERVAL: int = Field(
        60,
        description=(
            'How often, in seconds, sf-resources evaluates the health of the '
            'storage paths this node depends on. Bounds detection latency for '
            'a fully-dead path (the cheap statvfs check runs every '
            'evaluation).'
        )
    )
    NODE_HEALTH_WRITE_INTERVAL: int = Field(
        300,
        description=(
            'How often, in seconds, the node health check writes an '
            'authoritative heartbeat (write plus fsync) to each monitored '
            'path. This catches write-only failures a read cannot, and leaves '
            'a forensic last-seen-live timestamp.'
        )
    )
    NODE_HEALTH_PROBE_TIMEOUT: int = Field(
        30,
        description=(
            'Deadline, in seconds, for a single node health probe. A probe '
            'that does not return in time is treated as unhealthy -- this is '
            'how a hung hard-NFS mount (which blocks rather than erroring) is '
            'detected.'
        )
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

    # Logging. One entry per daemon; daemon.apply_log_level() applies
    # the value to the whole shakenfist logger namespace at daemon
    # startup, so a single daemon can be turned up to debug without
    # drowning the cluster's syslog and Loki streams in every
    # daemon's debug records.
    LOGLEVEL_API: str = 'info'
    LOGLEVEL_CLEANER: str = 'info'
    LOGLEVEL_CLUSTER: str = 'info'
    LOGLEVEL_DATABASE: str = 'info'
    LOGLEVEL_MAIN: str = 'info'
    LOGLEVEL_NET: str = 'info'
    LOGLEVEL_NODELOCK: str = 'info'
    LOGLEVEL_PRIVEXEC: str = 'info'
    LOGLEVEL_QUEUES: str = 'info'
    LOGLEVEL_RESOURCES: str = 'info'
    LOGLEVEL_SIDECHANNEL: str = 'info'
    LOGLEVEL_TRANSFERS: str = 'info'

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
    # SecretStr for the same reason as AUTH_SECRET_SEED. Unwrapped once,
    # where the SQLAlchemy connection URL is built in mariadb.py.
    MARIADB_PASSWORD: SecretStr = Field(
        SecretStr(''),
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


def redacted_config_items() -> Iterator[tuple[str, Any]]:
    """The configuration as (key, value) pairs, with secrets masked.

    Both callers dump every configuration item. The sf-queues startup
    banner writes them at INFO, and INFO and above is shipped off the
    node to Loki (see shakenfist/logship.py), so a secret which reaches
    that loop leaves the cluster and lands in log aggregation.
    AUTH_SECRET_SEED and MARIADB_PASSWORD did exactly that until this
    was added. _config_failure() below prints them to the operator's
    terminal, and to the journal when a daemon is what failed.

    The test is on the key name rather than the value's type because
    this iterates *every* configuration item, including options which
    do not exist yet. The secret-carrying fields are separately typed
    SecretStr, which covers every other path one might be stringified
    on; the two mechanisms are complementary rather than redundant, and
    removing either re-opens a hole the other does not close. See
    docs/plans/PLAN-auth-federation-phase-06-secret-types.md.

    The predicate over-matches by design -- it is shared with
    sf-ctl show-config, where hiding a value behind --show-secrets costs
    nothing. Neither caller here has such an escape hatch, so numbers
    are exempted: API_TOKEN_DURATION, FEDERATION_MAX_TOKEN_BYTES and
    KERBSIDE_TOKEN_DURATION all match the name pattern, none of them can
    be a credential, and all three are tunables an operator reads this
    output to confirm. A credential is always a string (or a SecretStr,
    which is not an int either, so it is still masked here as well as
    rendering as asterisks on its own).

    Field order is deliberately left as the model reports it, because
    operators grep this output.
    """
    for key, value in config.model_dump().items():
        numeric = isinstance(value, (bool, int, float))
        if SECRET_CONFIG_KEY_RE.search(key) and not numeric:
            yield key, '<redacted>'
        else:
            yield key, value


def _config_failure(failures: list[str]) -> NoReturn:
    print('Configuration failed validation!')
    print()
    print('Configuration as read:')
    for key, value in redacted_config_items():
        print(f'    {key} = {value}')
    print()
    print('Errors:')
    for failure in failures:
        print('    * %s' % failure)
    sys.exit(1)


def verify_config(skip_auth_seed: bool = False) -> None:
    failures: list[str] = []

    if not skip_auth_seed:
        # get_secret_value() is required, not stylistic. AUTH_SECRET_SEED
        # is a SecretStr, and SecretStr('x') == 'x' is False, so
        # comparing the field directly against the sentinel would be
        # false for every possible configuration -- including an
        # unconfigured one -- and this refusal would never fire again.
        # The failure mode is a cluster signing every token in its zone
        # with the value shipped in this file.
        if (config.AUTH_SECRET_SEED.get_secret_value()
                == UNCONFIGURED_AUTH_SECRET_SEED):
            failures.append('You must configure AUTH_SECRET_SEED!')

    if failures:
        _config_failure(failures)
