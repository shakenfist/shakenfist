# Copyright 2019 Michael Still and contributors
"""The Loki log shipper handler and its lifecycle wiring.

This module provides a ``logging.Handler`` that formats each log
record to a JSON line and enqueues it into the local logship spool
(``shakenfist.logship_spool``); a background drainer
(``shakenfist.logship_drainer``) ships the spool to Loki.

``start(daemon_name)`` is the single entry point, called once per
process from the same lifecycle seams as the eventlog drainer
(``daemon.write_pid_file`` and the gunicorn ``post_fork``). It has
two modes:

* **Mode B** (``LOKI_BASE_URL`` empty): a no-op. The library's
  per-module ``SysLogHandler``s stay in place and logs go to
  ``/dev/log`` / journald locally.
* **Mode A** (``LOKI_BASE_URL`` set, Loki-only): the library's
  per-module ``SysLogHandler``s are removed from every logger and
  a single Loki handler is attached to the **root** logger.
  Records propagate from per-module loggers up to root, so they
  reach Loki only -- there is no second local shipping pipeline
  (the on-disk spool is the local-durability buffer; journald
  still gets the systemd stdout/stderr residual for free).

Known residual (accepted for v1): a module that calls
``logs.setup(__name__)`` *after* ``start()`` re-adds a per-module
``SysLogHandler`` whose lines also reach ``/dev/log`` until
re-pointed. This is the same limitation ``set_syslog_ident``
already lives with, and such lines still reach Loki via root
propagation. The clean fix -- having the library configure the
root logger once -- is future work.
"""
import logging
from logging.handlers import SysLogHandler

from pylogrus import JsonFormatter
from shakenfist_utilities import logs

from shakenfist.config import config


LOG, _ = logs.setup(__name__)


# The library installs a ``JsonFormatter`` on each per-module
# logger when running with structured logging (v0.9.0+). We reuse
# that instance so the field list is a single source of truth.
# Under v0.8.4 the per-module handlers carry a ``TextFormatter``
# instead, so no ``JsonFormatter`` exists to lift; this fallback
# list reconstructs the library's field list.
#
# IMPORTANT: keep this list in sync with
# ``shakenfist_utilities/logs.py`` ``setup()`` ``enabled_fields``
# until the library exposes the list directly (future work). It is
# only used when no library ``JsonFormatter`` is found.
_FALLBACK_ENABLED_FIELDS = [
    ('name', 'logger_name'),
    ('asctime', 'ts'),
    ('levelname', 'level'),
    ('process', 'pid'),
    ('threadName', 'thread_name'),
    'message',
    ('exception', 'exception_class'),
    ('stacktrace', 'stack_trace'),
    'module',
    ('funcName', 'function'),
]


# Module-level singletons. ``start()`` is idempotent and guards
# against a double start.
_handler: 'LokiHandler' = None  # type: ignore[assignment]
_started = False


class LokiHandler(logging.Handler):
    """A ``logging.Handler`` that spools formatted lines for Loki.

    ``emit`` formats the record to a JSON line, computes the
    nanosecond timestamp, and enqueues it into the singleton
    logship spool. The enqueue is one cheap sqlite insert and
    returns immediately; over the spool's high-water mark the line
    is dropped with a counter (graceful degradation, never blocks
    the caller). Any error is routed through ``handleError`` so it
    never propagates into the logging call site -- a handler must
    not raise.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Late import: the spool pulls in sqlite/prometheus
            # bookkeeping we don't want at module-import time for
            # callers that merely import ``logship``.
            from shakenfist import logship_spool

            line = self.format(record)
            ts_ns = int(record.created * 1_000_000_000)
            logship_spool.enqueue(ts_ns, line)
        except Exception:
            self.handleError(record)


def _find_library_json_formatter() -> 'JsonFormatter | None':
    """Return a library ``JsonFormatter`` instance, or None.

    Walks every logger's handlers (the ``set_syslog_ident``
    pattern) and returns the first ``JsonFormatter`` found. Under
    v0.9.0+ the library installs one on each per-module handler;
    under v0.8.4 the handlers carry a ``TextFormatter`` and this
    returns None so the caller uses the fallback field list.
    """
    for name in list(logging.root.manager.loggerDict.keys()) + ['']:
        for handler in logging.getLogger(name).handlers:
            if isinstance(handler.formatter, JsonFormatter):
                return handler.formatter
    return None


def _build_formatter() -> JsonFormatter:
    """Build the JSON formatter for the Loki handler.

    Reuse the library's ``JsonFormatter`` instance if one is
    already installed (single source of truth, no field drift);
    otherwise fall back to reconstructing the documented field
    list (the v0.8.4 case, where per-module handlers carry a
    ``TextFormatter``).
    """
    existing = _find_library_json_formatter()
    if existing is not None:
        return existing
    return JsonFormatter(
        datefmt='Z', enabled_fields=_FALLBACK_ENABLED_FIELDS)


def _remove_library_syslog_handlers() -> None:
    """Remove every library-installed ``SysLogHandler``.

    Walks every logger (the ``set_syslog_ident`` ``loggerDict`` +
    root walk) so that, with the Loki handler on root, records
    reach Loki only and no longer go to ``/dev/log``.
    """
    for name in list(logging.root.manager.loggerDict.keys()) + ['']:
        target = logging.getLogger(name)
        for handler in list(target.handlers):
            if isinstance(handler, SysLogHandler):
                target.removeHandler(handler)


def start(daemon_name: str) -> None:
    """Wire up Loki log shipping for this process.

    No-op when ``LOKI_BASE_URL`` is empty (Mode B). When set
    (Mode A), initialises the spool, removes the library's
    per-module ``SysLogHandler``s, attaches a single Loki handler
    to the root logger, starts the drainer, and registers a
    bounded ``atexit`` drain. Idempotent.
    """
    global _handler, _started

    if not config.LOKI_BASE_URL:
        # Mode B: leave the library's per-module handlers in place.
        return

    if _started:
        return
    _started = True

    # Late import: the spool/drainer chain pulls in sqlite and the
    # prometheus registry; keep that off the module-import path for
    # callers that merely import ``logship``.
    from shakenfist import logship_drainer
    from shakenfist import logship_spool

    logship_spool.initialise(daemon_name)

    # Build the Loki handler. The formatter must be lifted (or
    # reconstructed) before we remove the library handlers, since
    # removal drops the formatters we want to reuse.
    handler = LokiHandler()
    handler.setFormatter(_build_formatter())

    # Re-point logging to Loki-only: drop the per-module syslog
    # handlers and attach the single Loki handler to root. Records
    # propagate from per-module loggers up to root -> Loki only.
    _remove_library_syslog_handlers()
    logging.getLogger('').addHandler(handler)
    _handler = handler

    # The drainer registers its own bounded atexit drain in its
    # start(), so the shutdown flush is handled there -- we don't
    # register a second one here.
    logship_drainer.start(daemon_name)


def reset_for_tests() -> None:
    """Tear down module state for unit-test isolation."""
    global _handler, _started
    if _handler is not None:
        logging.getLogger('').removeHandler(_handler)
    _handler = None  # type: ignore[assignment]
    _started = False
