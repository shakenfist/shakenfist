# Copyright 2026 Michael Still and contributors

"""Process-global caller identity for sf-database attribution.

The gRPC client interceptor stamps a ``caller-daemon`` label on every
sf-database call so the server can attribute load to the daemon that
issued it. A library like ``mariadb.py`` has no natural way to know
which daemon it is running inside, so each process records its own
name here once at startup via ``set_caller_identity``; the interceptor
reads it on every call.

Kept deliberately import-light -- it imports nothing from
``shakenfist`` -- so the config bootstrap path, which builds a gRPC
channel at import time before ``shakenfist.config`` is initialised,
can use it without an import cycle. A process that never calls
``set_caller_identity`` (a one-shot, or the import-time bootstrap)
reports ``'unknown'``, which is a valid, bounded label rather than an
error.
"""

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
