# Copyright 2019 Michael Still and contributors
"""Gunicorn config for sf-api.

``post_fork`` starts two per-worker threads after the master forks
each worker -- the only correct moment, because threads don't
survive the ``--preload`` fork:

- the local eventlog spool drainer. Without it, every API request
  synchronously gRPC-calls sf-eventlog twice (before_request +
  after_request audit events), saturating the eventlog server and
  forcing every event onto the event_dlq MariaDB write path. That
  positive-feedback loop is what tripped the smoke checks in
  GH actions run 26612233454. The spool itself is keyed on pid so
  workers don't collide on the same sqlite file.
- the readiness checker (``shakenfist.external_api.health``), which
  polls sf-database so ``/readyz`` can answer from cached state.
"""
from shakenfist_utilities import logs


LOG, _ = logs.setup(__name__)


def post_fork(server, worker):
    try:
        from shakenfist import eventlog_drainer
        eventlog_drainer.start('sf-api')
    except Exception as e:
        LOG.with_fields({'error': str(e), 'pid': worker.pid}).warning(
            'Failed to start eventlog drainer in gunicorn worker; '
            'events will fall through to the direct gRPC + DLQ path')

    # Start the per-worker readiness checker. Like the drainer above it
    # spawns a thread, so post_fork is the only correct moment to start it
    # (threads don't survive the --preload fork).
    try:
        from shakenfist.external_api import health
        health.start_checker()
    except Exception as e:
        LOG.with_fields({'error': str(e), 'pid': worker.pid}).warning(
            'Failed to start readiness checker in gunicorn worker; '
            '/readyz will report not-ready until the checker runs')
