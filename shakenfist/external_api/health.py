# Copyright 2019 Michael Still and contributors
"""Per-worker readiness state for sf-api.

Each gunicorn worker process runs a single background checker thread
that polls the sf-database health endpoint every ``READINESS_POLL``
seconds. The result is cached in module-level state so the ``/readyz``
handler can answer in microseconds without making an RPC on the request
path.

The cached ``ready`` flag is debounced: it only flips to ``False`` after
``READINESS_FAIL_THRESHOLD`` consecutive failed polls, so a single
transient blip does not pull the worker out of the load balancer. A
staleness guard (``READINESS_STALE``) means a wedged checker thread that
stops updating is treated as not-ready even if its last cached result was
positive.

``begin_drain()`` latches a one-way ``draining`` flag used by the SIGTERM
handler: once draining, ``is_ready()`` reports ``False`` so the load
balancer drops this node on its next probe before the worker actually
stops serving.
"""
import threading
import time

from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc
from shakenfist_utilities import logs

from shakenfist.config import config
from shakenfist.util.grpc_channel import make_database_channel


LOG, _ = logs.setup(__name__)


# Seconds between readiness polls.
READINESS_POLL = 5
# Per-poll RPC deadline, in seconds, for the health Check call.
READINESS_RPC_TIMEOUT = 2
# Number (K) of consecutive failed polls before ``ready`` flips False.
READINESS_FAIL_THRESHOLD = 3
# If the checker has not updated ``last_update`` within this many seconds
# the cached result is considered stale and ``is_ready()`` returns False.
READINESS_STALE = 15


# Per-worker readiness state. Mutated only by the checker thread (and the
# test helpers), read by the request-path ``is_ready()``. Plain reads of
# these scalars are atomic enough for our purposes; the lock below only
# guards the checker-thread singleton.
ready: bool = False
last_update: float = 0
consecutive_failures: int = 0
draining: bool = False

_checker_thread = None
_checker_lock = threading.Lock()


def _build_stub():
    """Construct a fresh HealthStub on a new database channel.

    Returns ``None`` when no database gateway hosts are configured, which
    the caller treats as a failed poll.
    """
    if not config.MARIADB_GATEWAY_HOSTS:
        return None
    channel = make_database_channel(
        config.MARIADB_GATEWAY_HOSTS, config.MARIADB_GATEWAY_PORT)
    return health_pb2_grpc.HealthStub(channel)


def _poll_once(stub):
    """Run a single readiness poll against ``stub`` and update state.

    Factored out of the thread loop so tests can drive one iteration with
    a mock stub. ``stub`` may be ``None`` (no configured hosts), which is
    treated as a failure. Always updates ``last_update``.
    """
    global ready, last_update, consecutive_failures

    serving = False
    try:
        if stub is None:
            raise RuntimeError('no database gateway hosts configured')
        resp = stub.Check(
            health_pb2.HealthCheckRequest(service=''),
            timeout=READINESS_RPC_TIMEOUT)
        serving = (resp.status == health_pb2.HealthCheckResponse.SERVING)
    except Exception as e:
        LOG.with_fields({'error': str(e)}).debug('Readiness poll failed')
        serving = False

    if serving:
        if not ready:
            LOG.info('Readiness check now SERVING')
        ready = True
        consecutive_failures = 0
    else:
        consecutive_failures += 1
        if consecutive_failures >= READINESS_FAIL_THRESHOLD and ready:
            LOG.with_fields({
                'consecutive_failures': consecutive_failures,
            }).warning('Readiness check failed threshold, marking not ready')
        if consecutive_failures >= READINESS_FAIL_THRESHOLD:
            ready = False

    last_update = time.time()


def _checker_loop():
    """Background loop: rebuild the stub on error and poll forever."""
    stub = None
    while True:
        try:
            if stub is None:
                stub = _build_stub()
            _poll_once(stub)
        except Exception as e:
            # Defensive: _poll_once swallows its own RPC errors, so an
            # exception here is something unexpected (e.g. channel build).
            # Drop the stub so the next iteration rebuilds it.
            LOG.with_fields({'error': str(e)}).warning(
                'Readiness checker iteration error')
            stub = None

        # If the last poll failed, discard the stub so we reconnect on a
        # fresh channel rather than reusing a potentially dead one.
        if not ready:
            stub = None

        time.sleep(READINESS_POLL)


def start_checker():
    """Launch the per-worker readiness checker thread.

    Idempotent and lock-guarded: calling this more than once per process
    spawns exactly one daemon thread.
    """
    global _checker_thread
    with _checker_lock:
        if _checker_thread is not None:
            return
        _checker_thread = threading.Thread(
            target=_checker_loop, name='readiness-checker', daemon=True)
        _checker_thread.start()


def is_ready() -> bool:
    """Return whether this worker should report ready to the load balancer."""
    return (ready
            and (time.time() - last_update) <= READINESS_STALE
            and not draining)


def begin_drain():
    """Latch the one-way draining flag (logged once)."""
    global draining
    if not draining:
        LOG.info('sf-api worker beginning drain, readiness now reports 503')
    draining = True


def is_draining() -> bool:
    """Return whether this worker has begun draining."""
    return draining


def _reset_for_test():
    """Reset all module state between unit tests."""
    global ready, last_update, consecutive_failures, draining, _checker_thread
    ready = False
    last_update = 0
    consecutive_failures = 0
    draining = False
    _checker_thread = None
