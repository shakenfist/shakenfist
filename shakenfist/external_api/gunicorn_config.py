# Copyright 2019 Michael Still and contributors
"""Gunicorn config for sf-api.

The only thing this module does is start the local eventlog spool
drainer in each worker after fork. Without it, every API request
synchronously gRPC-calls sf-eventlog twice (before_request +
after_request audit events), saturating the eventlog server and
forcing every event onto the event_dlq MariaDB write path. That
positive-feedback loop is what tripped the smoke checks in
GH actions run 26612233454.

``post_fork`` runs in each worker after the master forks it, which
is the only correct moment to start the drainer: it spawns a
thread, and threads don't survive fork. The spool itself is keyed
on pid so workers don't collide on the same sqlite file.
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
