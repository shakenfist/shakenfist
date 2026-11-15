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
