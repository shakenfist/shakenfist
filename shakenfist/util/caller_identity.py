# Copyright 2026 Michael Still and contributors

"""Process-global caller identity for sf-database attribution.

The gRPC client interceptor stamps a ``caller-daemon`` label on every
sf-database call so the server can attribute load to the daemon that
issued it. A library like ``mariadb.py`` has no natural way to know
which daemon it is running inside, so each process records its own
name here once at startup via ``set_caller_identity``; the interceptor
reads it on every call.

A daemon's passes name themselves on top of that with the
``caller_task`` context manager. That one is per thread, so a daemon
which also calls the database from another thread does not have those
calls labelled with whatever its main loop is doing.

Kept deliberately import-light -- it imports nothing from
``shakenfist`` -- so the config bootstrap path, which builds a gRPC
channel at import time before ``shakenfist.config`` is initialised,
can use it without an import cycle. A process that never calls
``set_caller_identity`` (a one-shot, or the import-time bootstrap)
reports ``'unknown'``, which is a valid, bounded label rather than an
error.
"""

import contextlib
import secrets
import threading
from typing import Iterator


# Every identity a process may legitimately claim. The server side uses
# this to decide whether an arriving ``caller-daemon`` becomes a metrics
# label, because that label is client-asserted: sf-database's gRPC port
# is unauthenticated, so without an allowlist anyone who can reach it
# can mint an unbounded number of distinct label values, each of which
# is a permanently retained prometheus child. That would degrade the
# very monitoring this attribution exists to provide.
#
# Deliberately duplicated from daemon.DAEMON_NAMES rather than imported:
# this module is import-light by design (see above) and importing
# daemon.py would reintroduce the cycle it exists to avoid.
# test_caller_identity pins the two together, so drift fails the suite.
KNOWN_CALLERS = frozenset({
    'api', 'checksums', 'cleaner', 'cluster', 'ctl', 'database', 'main',
    'net', 'nodelock', 'privexec', 'queues', 'resources', 'sentinel-first',
    'sentinel-last', 'sidechannel', 'transfers', 'unknown'
})

# A plain module global. The value is a single string reference, so
# reads (on the hot call path) and the once-at-startup write are atomic
# under CPython's GIL; no lock is needed.
_caller_daemon = 'unknown'


def set_caller_identity(name: str) -> None:
    """Record this process's daemon name. Call once, at startup."""
    global _caller_daemon
    _caller_daemon = name


def get_caller_daemon() -> str:
    """Return this process's daemon name, or 'unknown' if unset."""
    return _caller_daemon


# Every scheduled task or maintenance pass which may name itself on a
# call. ``caller_daemon`` localises load to a daemon but not to the loop
# inside it, which is why a rise in the cleaner's GetObjectState rate
# could not be attributed to one of its several passes without reading
# source (issue 4399). Allowlisted on the server for exactly the reason
# KNOWN_CALLERS is: the label is client-asserted on an unauthenticated
# port. test_caller_identity reads the names the tree actually uses out
# of the source and pins them against this set.
KNOWN_TASKS = frozenset({
    'find_missing_blobs', 'libvirt_log_cleanup', 'maintain_blobs',
    'observe_local_blobs', 'remove_stale_uploads', 'update_power_states',
})

# Thread local rather than a module global like _caller_daemon: a task is
# a property of the thread running the pass, so a daemon which also calls
# the database from another thread (an event log drainer, a worker pool)
# must not have its calls labelled with whatever the main loop happens to
# be doing. A thread which never enters a task reports '', and an empty
# label value is indistinguishable from an absent label to Prometheus, so
# every existing query over database_requests_total is unaffected.
_caller_task = threading.local()


def get_caller_task() -> str:
    """Return the task this thread is in, or '' if it is not in one."""
    return getattr(_caller_task, 'name', '')


@contextlib.contextmanager
def caller_task(name: str) -> Iterator[None]:
    """Label this thread's sf-database calls with a task name.

    Restores the previous value on exit, so a nested pass does not
    silently keep the label of the one it was called from.
    """
    previous = get_caller_task()
    _caller_task.name = name
    try:
        yield
    finally:
        _caller_task.name = previous


# The metadata key the client stamps a per-call reference under, and the
# thread local the server parks it in while it serves that call.
#
# The cause of a failed database RPC -- the statement, its parameters,
# the exception -- is logged on the database node, and the caller sees
# only that its call failed. Nothing joined the two, so an operator
# holding a client-side failure had to find the matching exception by
# timestamp alone, which on a cluster serving hundreds of calls a second
# is not a search that converges (issue 5077). A reference minted per
# call, sent on the wire and named by both sides joins the two halves of
# one failure back together, and is also the identifier a later
# span-propagation pass would carry.
#
# Thread local for the same reason _caller_task is: on the server it is
# a property of the pool thread running one RPC. Lower case because gRPC
# requires that of metadata keys.
CALL_REF_METADATA_KEY = 'call-ref'

_call_ref = threading.local()


def new_call_ref() -> str:
    """Mint a short opaque reference for a single sf-database call.

    Random rather than derived from the request: a reference names the
    call, not its content, so it carries no namespace, object or schema
    information and is safe to repeat in an event or an API error. Four
    bytes is eight characters -- short enough for a human to carry
    across two logs by eye, and unique enough that a grep for one lands
    on the call that failed.
    """
    return secrets.token_hex(4)


def get_call_ref() -> str:
    """Return the call reference this thread is serving, or '' if none."""
    return getattr(_call_ref, 'ref', '')


@contextlib.contextmanager
def call_ref(ref: str) -> Iterator[None]:
    """Name ``ref`` on everything this thread logs inside the block.

    Restores the previous value on exit, so a nested call does not
    silently keep the reference of the one it was called from.
    """
    previous = get_call_ref()
    _call_ref.ref = ref
    try:
        yield
    finally:
        _call_ref.ref = previous
