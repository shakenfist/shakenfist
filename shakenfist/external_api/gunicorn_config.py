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

``post_worker_init`` installs a SIGTERM drain handler. On stop,
systemd sends SIGTERM; rather than shutting down immediately we want
the load balancer to drop this node first. The handler latches the
draining flag (so ``/readyz`` reports 503 on its next probe), keeps
the worker serving in-flight and new requests for
``API_DRAIN_GRACE`` seconds, then chains to gunicorn's own SIGTERM
handler (``handle_exit``) to perform the normal graceful shutdown.

``post_worker_init`` is the correct seam -- not ``post_fork`` --
because gunicorn's sync worker installs its own SIGTERM handler in
``init_signals``, which runs *during* ``init_process`` *after*
``post_fork``. A handler installed in ``post_fork`` would be
clobbered. ``post_worker_init`` is called at the very end of
``init_process``, after ``init_signals``, so the handler installed
there survives. ``worker_int`` is not usable here: it only fires on
SIGQUIT/SIGINT, which systemd does not send on stop.
"""
import signal
import threading

from shakenfist_utilities import logs

# NOTE: gunicorn loads this file as its --config module and scans the module's
# globals for any name matching one of its own settings, applying them. A
# module-level name `config` collides with gunicorn's own `config` setting and
# makes gunicorn abort at startup with "Error: Not a string". So import the SF
# config under an alias (`sf_config`) -- do NOT expose a bare `config` global
# here, and avoid module-level globals named after gunicorn settings generally.
from shakenfist.config import config as sf_config
from shakenfist.external_api import health


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
        health.start_checker()
    except Exception as e:
        LOG.with_fields({'error': str(e), 'pid': worker.pid}).warning(
            'Failed to start readiness checker in gunicorn worker; '
            '/readyz will report not-ready until the checker runs')


def post_worker_init(worker):
    """Install a SIGTERM handler that drains before shutting down.

    Called at the end of gunicorn's ``init_process`` -- after
    ``init_signals`` has installed the worker's own SIGTERM handler --
    so the handler we install here is not clobbered. See the module
    docstring for why this is the only correct seam.
    """
    # Capture gunicorn's currently-installed SIGTERM handler. In the
    # normal case this is the worker's bound ``handle_exit`` method,
    # which is callable. It could in principle be SIG_DFL / SIG_IGN
    # (not callable) -- we guard against that below.
    orig = signal.getsignal(signal.SIGTERM)

    def _drain_then_exit(signum, frame):
        # Idempotence: a second SIGTERM while we are already draining
        # must not stack another timer. The drain flag is one-way, so
        # this is safe -- the first timer is already counting down.
        if health.is_draining():
            return

        health.begin_drain()
        LOG.with_fields({
            'grace_seconds': sf_config.API_DRAIN_GRACE,
            'pid': worker.pid,
        }).info(
            'SIGTERM received, draining sf-api worker: /readyz now reports '
            '503; will keep serving then shut down after grace period')

        def _invoke_orig():
            # Chain to gunicorn's original handler to perform the normal
            # graceful shutdown. The expected case is a callable bound
            # method (handle_exit); SIG_DFL / SIG_IGN are not callable.
            if callable(orig):
                orig(signum, frame)
            else:
                # No usable original handler (SIG_DFL / SIG_IGN). This path is
                # unreachable under normal gunicorn -- init_signals always
                # installs a callable handle_exit before post_worker_init runs.
                # We cannot cleanly terminate from this timer thread anyway
                # (signal.signal is main-thread-only, and re-raising SIGTERM
                # would just re-enter this guarded handler), so we raise
                # SystemExit to unwind the timer thread and rely on systemd's
                # TimeoutStopSec as the backstop that actually stops the worker.
                LOG.with_fields({'pid': worker.pid}).warning(
                    'No callable original SIGTERM handler captured; relying on '
                    'systemd TimeoutStopSec to stop the worker')
                raise SystemExit(0)

        # Defer the actual shutdown so this handler returns immediately
        # and the worker keeps serving during the grace period. A daemon
        # timer thread does not block process exit if shutdown happens
        # another way first.
        timer = threading.Timer(sf_config.API_DRAIN_GRACE, _invoke_orig)
        timer.daemon = True
        timer.start()

    signal.signal(signal.SIGTERM, _drain_then_exit)
