# Copyright 2019 Michael Still and contributors
"""Shared gRPC channel factory for the sf-database tier.

Provides a single `make_database_channel(...)` helper that every SF
client of the database service uses. Centralising the channel
construction here gives us:

  - one place to set the gRPC keepalive options
  - one place to attach the static `ipv4:` resolver + `round_robin`
    load-balancing policy + empty-string client-side health checking
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
    ('grpc.service_config', json.dumps({
        'loadBalancingConfig': [{'round_robin': {}}],
        'healthCheckConfig': {'serviceName': ''},
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
    ``hosts``. Subchannel health is reported via the gRPC health
    protocol against the empty service name (the convention for
    "overall server health"); the server side is registered in
    ``daemons/database/main.py`` by step 2 of phase 3.

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
