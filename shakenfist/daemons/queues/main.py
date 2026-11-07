import os
import time

import psutil
import setproctitle
from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import locks as sf_locks
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.daemons.queues import startup_tasks
from shakenfist.daemons.queues import workitem
from shakenfist.node import Node
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions


LOG, _ = logs.setup(__name__)


# Seconds between stray-lock housekeeping scans. Locks left by a dead process
# are a slow-changing condition, so this scan (a GetExistingLocks read) is
# rate-limited rather than run on every poll of the queue loop. See issue #3499.
STRAY_LOCK_CHECK_INTERVAL = 30


def _check_other_daemon(n, daemon_name, override_daemon_name=None):
    health_checks = {
        'gunicorn': daemon.health_check_api,
        'nodelock': daemon.health_check_nodelock,
        'privexec': daemon.health_check_privexec
    }

    recorded_daemon_name = daemon_name
    if override_daemon_name:
        recorded_daemon_name = override_daemon_name

    if not os.path.exists(f'/run/sf/{daemon_name}.pid'):
        n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                           message='pid file missing')
        return False

    try:
        with open(f'/run/sf/{daemon_name}.pid') as f:
            pid = int(f.read())
            psutil.Process(pid)
            if not health_checks[daemon_name]():
                n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                                   message='health check failed')
                return False

            n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_RUNNING)
            return True
    except FileNotFoundError:
        n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                           message='pid file missing on read')
        return False
    except ValueError:
        n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                           message='pid file not parsable')
        return False
    except psutil.NoSuchProcess:
        n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                           message='process absent')
        return False

    n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                       message='unknown issue')
    return False


def _health_checks():
    healthy = True
    n = Node.from_db(config.NODE_NAME, suppress_failure_audit=True)
    if not n:
        # The node record has not been created yet (an unreachable
        # database instead raises DatabaseUnavailable, which our
        # caller treats as unhealthy). Not healthy yet, keep waiting.
        LOG.info(f'Node record for {config.NODE_NAME} is not readable, waiting')
        return False
    if not _check_other_daemon(n, 'privexec'):
        healthy = False
    if not _check_other_daemon(n, 'nodelock'):
        healthy = False
    if not _check_other_daemon(n, 'gunicorn', override_daemon_name='api'):
        healthy = False
    return healthy


def _block_until_healthy(abort_path=None):
    """Loop until every dependency reports healthy.

    Pass ``abort_path`` and the loop returns immediately when the
    abort file appears, so a SIGTERM during sf.target shutdown is
    not held up here while the api/nodelock daemons we are
    health-checking are themselves shutting down. Without this
    check the queue daemon ignores its own stop signal until
    systemd's TimeoutStopSec elapses and it gets SIGKILLed.
    """
    start_time = time.time()
    while True:
        try:
            if _health_checks():
                return
        except exceptions.DatabaseUnavailable:
            LOG.info('Database service unavailable during health checks, '
                     'waiting')
        if abort_path and not daemon.check_abort_path(abort_path):
            LOG.info(
                'Aborting health check loop because of pending shutdown')
            return
        if time.time() - start_time > 60:
            LOG.warning('Not processing queues as dependencies are unhealthy')
            start_time = time.time()
        time.sleep(5)


class Monitor(daemon.WorkerPoolDaemon):
    def _reap_stray_locks(self):
        """Clear locks on this node whose holder process is gone.

        Returns the lock keys reaped, for the caller's logging.

        This used to warn once per holder pid and then report the same
        row at ERROR on every later pass, because the escalation stored
        the time of its first warning and never updated it -- so the
        elif was permanently true. Nothing cleared the row either: one
        lock left by an sf-queues restart taken mid-replication logged
        1,567 ERROR lines in 13 hours, and only the next restart ended
        it (#3432).

        The startup tasks run the same reap, but a reap there cannot be
        relied on: the gRPC client returns silently when there is no
        database stub yet, which is exactly the state after the
        watchdog kills a daemon during a database stall. Reaping from
        the sweep, as the stray vxlan detector was taught to do
        (#3597), means the next pass clears what the last one could
        not.

        The lease on a lock row (expires_at, server-side) already stops
        a dead holder stranding a lock for anyone else, so this is
        about the row and the log volume, not about availability.
        """
        reaped = []
        for lock, holder in sf_locks.get_existing_locks().items():
            if holder.get('node') != config.NODE_NAME:
                continue
            if psutil.pid_exists(holder.get('pid')):
                continue

            reaped.append(lock)
            # The holder goes under a key of its own: with_fields(holder)
            # overwrote this record's pid with the dead holder's, so
            # these lines meant something different by pid than every
            # other line the daemon logs.
            LOG.with_fields({
                'lock': lock,
                'holder': holder,
            }).warning('Clearing lock held by missing process')

        if reaped:
            # One call clears every stale lock this node holds, and it
            # re-reads the live pids itself, so a pid recycled since the
            # check above cannot have its lock deleted.
            sf_locks.clear_stale_locks()

        return reaped

    def _run_inner(self):
        _block_until_healthy(abort_path=self.abort_path)

        last_third_party_health_check = 0
        last_stray_lock_check = 0.0
        poll_backoff = daemon.IdlePollBackoff()

        while daemon.check_abort_path(self.abort_path):
            try:
                self.reap_workers()

                if time.time() - last_third_party_health_check > 30:
                    # We also check in on the privexec and api daemon heres because
                    # they cannot do this for themselves...
                    _block_until_healthy(abort_path=self.abort_path)
                    last_third_party_health_check = time.time()

                # Check if we hold any locks for processes which don't exist any
                # more. That is, a process has ended but left a stray lock.
                #
                # NOTE(mikal): this doesn't really make sense with threads, but
                # given we want to get rid of locks anyways...
                #
                # Rate-limited: stray locks are slow-changing housekeeping, so
                # there is no need to re-read every lock on every poll (that was
                # ~19 GetExistingLocks/s cluster-wide at idle). See issue #3499.
                if time.time() - last_stray_lock_check > STRAY_LOCK_CHECK_INTERVAL:
                    self._reap_stray_locks()
                    last_stray_lock_check = time.time()

                # Adaptive backoff: poll fast while there is work, back off
                # towards IDLE_POLL_MAX_SECONDS while idle so an empty cluster
                # stops issuing ~5 Dequeue/s per node. See issue #3499.
                if self.dequeue_job(workitem.Job):
                    poll_backoff.reset()
                else:
                    self.idle(poll_backoff.next_empty_interval())

            except exceptions.DatabaseUnavailable:
                # Not an error to be ignored noisily: the database will
                # come back, and _block_until_healthy() knows how to wait
                # for it.
                LOG.warning(
                    'Database service unavailable, pausing queue processing')
                _block_until_healthy(abort_path=self.abort_path)
            except Exception as e:
                util_exceptions.ignore_exception('queue worker', e)

            self.check_daemon_state()


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('queues')

    # Because we do work before starting the queue thread, we need to name
    # ourselves here too.
    name = f'{daemon.process_name("queues")} startup'
    setproctitle.setproctitle(name)
    util_concurrency.set_thread_name(name)

    start_time = time.time()
    startup_tasks.startup_tasks()
    duration = time.time() - start_time
    LOG.info(f'Startup tasks took {duration:.2f} seconds')

    m = Monitor('queues')
    m.run()

    daemon.force_clean_exit()
