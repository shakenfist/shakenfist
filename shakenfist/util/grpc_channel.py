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

import json
from typing import Any
from typing import Optional

import grpc


_DEFAULT_OPTIONS: list[tuple[str, Any]] = [
    ('grpc.keepalive_time_ms', 10000),
    ('grpc.keepalive_timeout_ms', 5000),
    ('grpc.http2.max_pings_without_data', 0),
    ('grpc.keepalive_permit_without_calls', 1),
    # Disable gRPC's default behaviour of honouring the HTTP_PROXY /
    # HTTPS_PROXY environment variables. SF nodes that sit behind an
    # outbound HTTP proxy (CI runners, corporate networks) would
    # otherwise see localhost / mesh-IP database calls routed through
    # the proxy and rejected with 503.
    ('grpc.enable_http_proxy', 0),
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
    example, the blob path in ``shakenfist/database.py`` shortens
    the keepalive timeout to 200 ms).

    Raises ``ValueError`` if ``hosts`` is empty.
    """
    if not hosts:
        raise ValueError('make_database_channel requires at least one host')

    target = 'ipv4:' + ','.join(f'{h}:{port}' for h in hosts)

    options = list(_DEFAULT_OPTIONS)
    if extra_options:
        options.extend(extra_options)

    return grpc.insecure_channel(target, options=options)
