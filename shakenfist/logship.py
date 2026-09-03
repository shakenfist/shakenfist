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
  per-module ``SysLogHandler``s stay in place and all logs go to
  ``/dev/log`` / journald locally.
* **Mode A** (``LOKI_BASE_URL`` set): the library's per-module
  ``SysLogHandler``s are left in place (so every level still logs
  locally to journald, cheaply, as before) and an additional Loki
  handler is attached to the **root** logger at **INFO** level.
  Records propagate from per-module loggers up to root, so INFO and
  above are also shipped to Loki.

**Only INFO and above is shipped to Loki; DEBUG stays local.** This
matches the previous rsyslog deployment, whose forwarder shipped
``*.*;*.!=debug`` -- DEBUG was never centrally aggregated, only kept
in each node's local syslog. It is also the performance fix for the
multi-node cluster: DEBUG is by far the highest-volume level (e.g.
privexec logs every command at DEBUG), and keeping it off the
spool/push path -- which costs a JSON format plus a sqlite insert
per line -- restores throughput while leaving DEBUG available on
the node via ``journalctl`` for diagnosis.

Future work (revisit with OpenTelemetry): once SF has OTel-based
tracing/diagnostics, reconsider shipping DEBUG (or trace-level
detail) to a central store so deep diagnosis does not require
on-node access. Tracked in
``docs/plans/PLAN-remove-syslog-forwarding.md`` (Future work) and
the OpenTelemetry thread.
"""
import json
import logging
from typing import Any

from shakenfist_utilities import logs
from shakenfist_utilities.logs import JsonFormatter

from shakenfist.config import config


LOG, _ = logs.setup(__name__)


# The library installs a ``JsonFormatter`` on each per-module logger
# when running with structured logging (v0.8.5+). We reuse that
# instance so the field list is a single source of truth. Under older
# library versions the per-module handlers carry a ``TextFormatter``
# instead, so no ``JsonFormatter`` exists to lift and we build one from
# ``logs.ENABLED_FIELDS``.
#
# That list used to be duplicated here, with a comment asking for the
# library to expose it directly. It now does, so there is no longer a
# copy that can drift out of step with the field-name contract in the
# library's docs/log-record-fields.md.


def _json_safe_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Return fields with any unserialisable values stringified.

    Values that already encode cleanly pass through untouched; anything
    else is round-tripped through ``json.dumps(default=str)`` (or, as a
    last resort, ``str()``) so the record as a whole always encodes.
    """
    safe = {}
    for key, value in fields.items():
        try:
            json.dumps(value)
            safe[key] = value
        except (TypeError, ValueError):
            try:
                safe[key] = json.loads(json.dumps(value, default=str))
            except (TypeError, ValueError):
                safe[key] = str(value)
    return safe


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

            try:
                line = self.format(record)
            except (TypeError, ValueError):
                # Defence in depth: a caller attached a field that is not
                # JSON serialisable (issue 3573: raw uuid.UUID values in
                # event 'extra'). Losing the record to handleError dumps a
                # raw multi-line traceback to stderr/syslog instead, so
                # stringify the offending fields and retry -- a slightly
                # lossy field beats a dropped record.
                extra_fields = getattr(record, 'extra_fields', None)
                if isinstance(extra_fields, dict):
                    setattr(record, 'extra_fields',
                            _json_safe_fields(extra_fields))
                line = self.format(record)
            ts_ns = int(record.created * 1_000_000_000)
            logship_spool.enqueue(ts_ns, line)
        except Exception:
            self.handleError(record)


def _find_library_json_formatter() -> 'JsonFormatter | None':
    """Return a library ``JsonFormatter`` instance, or None.

    Walks every logger's handlers (the ``set_syslog_ident``
    pattern) and returns the first ``JsonFormatter`` found. Under
    v0.8.5+ the library installs one on each per-module handler;
    under older versions the handlers carry a ``TextFormatter`` and
    this returns None so the caller builds one instead.
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
    otherwise build one from the library's exported field list (the
    v0.8.4 case, where per-module handlers carry a ``TextFormatter``).
    """
    existing = _find_library_json_formatter()
    if existing is not None:
        return existing
    return JsonFormatter(datefmt='Z', enabled_fields=logs.ENABLED_FIELDS)


def start(daemon_name: str) -> None:
    """Wire up Loki log shipping for this process.

    No-op when ``LOKI_BASE_URL`` is empty (Mode B). When set
    (Mode A), initialises the spool and attaches a Loki handler to
    the root logger at INFO level (so only INFO+ is shipped; DEBUG
    stays in local journald via the library's per-module handlers,
    which are left untouched), then starts the drainer. Idempotent.
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

    handler = LokiHandler()
    handler.setFormatter(_build_formatter())
    # Ship INFO and above only. DEBUG is the highest-volume level and
    # stays local (journald) -- matching the old rsyslog forwarder's
    # ``*.!=debug`` and keeping the high-volume DEBUG stream off the
    # spool/push path. The library's per-module SysLogHandlers are
    # left in place, so every level still reaches journald locally.
    handler.setLevel(logging.INFO)
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
