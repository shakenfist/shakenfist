# Copyright 2019 Michael Still and contributors
"""Shared gRPC channel factory for the sf-database tier.

Provides a single `make_database_channel(...)` helper that every SF
client of the database service uses. Centralising the channel
construction here gives us:

  - one place to set the gRPC keepalive options
  - one place to attach the static `ipv4:` resolver + `round_robin`
    load-balancing policy
  - per-caller option overrides via the ``extra_options`` argument
    (gRPC's "last value wins" behaviour for duplicated option keys)

The factory takes ``hosts`` and ``port`` as arguments rather than reading
them from ``shakenfist.config`` directly. This is deliberate: the
bootstrap path in ``shakenfist.config.load_cluster_config()`` runs at
module-import time, before ``shakenfist.config`` itself is fully
initialised. Importing config from here would create a cycle. Callers
pass the values they already have.
"""

import collections
import json
from typing import Any
from typing import Optional

import grpc

from shakenfist.util.caller_identity import get_caller_daemon


_DEFAULT_OPTIONS: list[tuple[str, Any]] = [
    ('grpc.keepalive_time_ms', 10000),
    ('grpc.keepalive_timeout_ms', 5000),
    ('grpc.http2.max_pings_without_data', 0),
    ('grpc.keepalive_permit_without_calls', 1),
    # Several DatabaseService replies are unbounded by design --
    # GetObjectsByState returns every matching uuid and GetObjectEvents
    # an object's whole (limit-capped, but limits reach 1000) event
    # history -- and on sfcbr they crossed gRPC's default 4MiB receive
    # cap (#3638, observed up to ~7.2MB), turning routine reads into
    # opaque RESOURCE_EXHAUSTED failures.
    #
    # 32MiB is deliberately not "large enough that this can never
    # happen again". Raising the cap only moves the cliff, and moving
    # it too far changes where the failure lands: sf-database has to
    # serialise whatever it sends, so an arbitrarily generous client
    # cap converts a fast, loud, client-side RESOURCE_EXHAUSTED into
    # memory pressure and latency on the database tier -- which this
    # cluster has already been hurt by (the gateway watchdog SIGABRTs
    # of issue 3586). 32MiB is ~4.5x the largest payload observed in
    # the wild, which clears today's traffic with room to spare while
    # keeping a cliff close enough that we still hear about growth.
    #
    # The durable fix is to stop sending unbounded replies at all; see
    # docs/plans/PLAN-grpc-bounded-replies.md.
    ('grpc.max_receive_message_length', 32 * 1024 * 1024),
    # Disable gRPC's default behaviour of honouring the HTTP_PROXY /
    # HTTPS_PROXY environment variables. SF nodes that sit behind an
    # outbound HTTP proxy (CI runners, corporate networks) would
    # otherwise see localhost / mesh-IP database calls routed through
    # the proxy and rejected with 503.
    ('grpc.enable_http_proxy', 0),
    # Cap the subchannel reconnect backoff. gRPC's default backoff grows
    # from 1s by 1.6x per failed dial to a 120s ceiling, so a subchannel
    # that failed while its gateway restarted can sit in TRANSIENT_FAILURE
    # for up to two minutes after that gateway has recovered -- round_robin
    # does not redial it early while another backend is READY. That left a
    # window where the serial database-tier roll took the *other* gateway
    # down while clients had still not redialled the first, producing
    # "connections to all backends failing" DatabaseUnavailable storms for
    # 1-2 minutes per deploy (#3430). With the cap at 5s a recovered
    # gateway is redialled promptly, and the deploy roll's settle
    # (sf_database_roll_settle_seconds, default 10s -- see the node role's
    # register.yml) fully covers the reconnect window. That settle must
    # always stay longer than this cap.
    ('grpc.initial_reconnect_backoff_ms', 1000),
    ('grpc.max_reconnect_backoff_ms', 5000),
    # No healthCheckConfig here, deliberately. Client-side health
    # checking opens a grpc.health.v1.Health/Watch stream on every
    # subchannel, and the synchronous HealthServicer on sf-database
    # sends the initial Watch response while holding its internal
    # lock. The server's single event-dispatch thread runs Watch
    # close callbacks inline, which acquire that same lock. A Watch
    # opening while another closes deadlocks the event thread and
    # wedges the entire gRPC server -- permanently. (Reproduced
    # across grpcio 1.70-1.81; the lock pattern is still present
    # upstream at master as of June 2026.) round_robin handles dead
    # backends via subchannel connectivity state, and the aggressive
    # keepalives above detect dead transports within ~15 s, so the
    # health protocol is not needed for failover.
    ('grpc.service_config', json.dumps({
        'loadBalancingConfig': [{'round_robin': {}}],
    })),
]


def make_database_channel(
        hosts: list[str],
        port: int,
        extra_options: Optional[list[tuple[str, Any]]] = None,
) -> grpc.Channel:
    """Construct a gRPC channel to the sf-database tier.

    Uses the gRPC-native ``ipv4:`` static resolver and the
    ``round_robin`` load-balancing policy across every host in
    ``hosts``. Failed instances are avoided via subchannel
    connectivity state and the keepalive options; the gRPC health
    protocol's Watch-based client-side checking is deliberately NOT
    enabled (see the comment on ``_DEFAULT_OPTIONS``).

    Per-caller overrides go in ``extra_options``. gRPC accepts
    duplicate option keys; the last entry wins. Callers therefore
    pass override values for any default they need to change (for
    example, a caller wanting faster failover can shorten the
    keepalive timeout).

    Raises ``ValueError`` if ``hosts`` is empty.
    """
    if not hosts:
        raise ValueError('make_database_channel requires at least one host')

    target = 'ipv4:' + ','.join(f'{h}:{port}' for h in hosts)

    options = list(_DEFAULT_OPTIONS)
    if extra_options:
        options.extend(extra_options)

    channel = grpc.insecure_channel(target, options=options)
    # Layer caller attribution on top of routing: every call gets
    # caller-daemon/caller-node metadata so sf-database can attribute load.
    # intercept_channel returns a grpc.Channel, so stubs built on it are
    # unaffected and the round_robin/keepalive options above are preserved.
    return grpc.intercept_channel(channel, _CallerMetadataInterceptor())


def _caller_node() -> str:
    """Best-effort node name for the caller-node metadata.

    ``config`` is imported lazily so this module keeps its no-config-import
    contract for the import-time bootstrap path; the interceptor only runs at
    call time, when config is available.
    """
    try:
        from shakenfist.config import config
        return config.NODE_NAME or 'unknown'
    except Exception:
        return 'unknown'


class _ClientCallDetails(
        collections.namedtuple(
            '_ClientCallDetails',
            ('method', 'timeout', 'metadata', 'credentials',
             'wait_for_ready')),
        grpc.ClientCallDetails):
    """A ClientCallDetails copy that carries our augmented metadata."""


class _CallerMetadataInterceptor(
        grpc.UnaryUnaryClientInterceptor):  # type: ignore[type-arg]
    """Append caller-daemon/caller-node metadata to every sf-database call.

    All DatabaseService RPCs are unary-unary, so this single interceptor
    covers 100% of traffic. Attribution must never break a call, so the
    augmentation is best-effort; on any failure the original call proceeds.
    """

    def intercept_unary_unary(self, continuation: Any,
                              client_call_details: Any, request: Any) -> Any:
        try:
            metadata = list(client_call_details.metadata or [])
            metadata.append(('caller-daemon', get_caller_daemon()))
            metadata.append(('caller-node', _caller_node()))
            client_call_details = _ClientCallDetails(
                client_call_details.method,
                client_call_details.timeout,
                metadata,
                client_call_details.credentials,
                client_call_details.wait_for_ready,
            )
        except Exception:
            pass
        return continuation(client_call_details, request)
