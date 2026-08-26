import faulthandler
import logging
import os
import signal
import socket
import threading
import time
import uuid
from logging.handlers import SysLogHandler
from math import inf

import requests
import setproctitle
from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import get_maximum_object_version
from shakenfist.baseobject import get_minimum_object_version
from shakenfist.config import config
from shakenfist.constants import DISK_BUSY_PER_SECOND_METRIC
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist import mariadb
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.exceptions import InvalidStateException
from shakenfist.exceptions import MissingNodeLockSocket
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.exceptions import TruncatedNodeLockResponse
from shakenfist.node import Node
from shakenfist.operations.baseoperation import get_all_user_facing_node_queues
from shakenfist.operations.baseoperation import get_all_background_node_queues
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util.caller_identity import set_caller_identity
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logs.setup(__name__)


# Seconds between systemd WATCHDOG=1 keepalive pets. Pets are rate-limited to
# this cadence so a base-class daemon idling on its 0.2s tick does not emit a
# notification on every tick.
WATCHDOG_PET_INTERVAL = 10


# Seconds between database reads of this daemon's own state in
# check_daemon_state(). The read is rate-limited to this cadence (rather than
# running on every 0.2s idle tick) because it only notices an externally
# written stop request; local SIGTERM shutdown is handled by exit_gracefully()
# and the abort_path check in idle(), which are unaffected. See
# PLAN-database-load-reduction-phase-01-idle-loop.md.
DAEMON_STATE_POLL_INTERVAL = 2


# Ceiling for the backoff applied to that poll while the database cannot
# answer it. Every daemon on every node issues this read, and it is by
# far the most frequent of the bounded-deadline calls we make, so it is
# the first thing to fail when the database tier slows down (issue 3607
# recorded 262 failures of it in 23 minutes while every other RPC, on
# the longer default deadline, kept working) -- and re-issuing it every two
# seconds during a stall piles abandoned reads onto sf-database's worker
# pool, which lengthens the stall it is a symptom of (issue 3607).
# Backing off geometrically to a minute costs nothing (the poll only
# notices a manually written stop request, which has no latency
# expectation) and lets a struggling database recover. The interval
# snaps back to DAEMON_STATE_POLL_INTERVAL on the first successful read.
DAEMON_STATE_POLL_MAX_INTERVAL = 60


# Adaptive backoff for the idle dequeue loops (the queues and net
# dispatchers). Those loops slept a flat IDLE_POLL_FAST_SECONDS whenever a
# dequeue came back empty, so a completely idle cluster still issued ~5
# Dequeue calls per second per node. IdlePollBackoff grows the empty-poll
# sleep geometrically towards IDLE_POLL_MAX_SECONDS and snaps back to the
# fast interval the moment any work is found, so a burst is still drained at
# full speed and only the idle->work transition pays the extra latency. See
# PLAN-database-load-reduction-phase-05-next-tier.md and issue #3499.
IDLE_POLL_FAST_SECONDS = 0.2
IDLE_POLL_MAX_SECONDS = 2.0
IDLE_POLL_BACKOFF_FACTOR = 2.0


DAEMON_NAMES = {
    'api': 'sf-api',
    'checksums': 'sf-checksums',
    'cleaner': 'sf-cleaner',
    'cluster': 'sf-cluster',
    'database': 'sf-database',
    'main': 'sf-main',
    'net': 'sf-net',
    'queues': 'sf-queues',
    'resources': 'sf-resources',
    'sidechannel': 'sf-sidechannel',
    'transfers': 'sf-transfers'
}


def process_name(name):
    if name not in DAEMON_NAMES:
        raise Exception('Code Error: Bad process name: %s' % name)
    return DAEMON_NAMES[name]


def set_syslog_ident(procname):
    # Each module-level `logs.setup(__name__)` call attaches its own
    # SysLogHandler at import time, before setproctitle runs. Without
    # an explicit ident, the syslog program field falls back to whatever
    # rsyslog parses from /proc/PID/comm, which is truncated to 15 chars
    # and unreliable. Stamp every SysLogHandler we can see with the
    # daemon's process name so the centralised syslog has a stable,
    # greppable program= label that we can also reuse as a Loki label
    # when the eventual structured-logging migration lands.
    ident = f'{procname}: '
    for name in list(logging.root.manager.loggerDict.keys()) + ['']:
        for handler in logging.getLogger(name).handlers:
            if isinstance(handler, SysLogHandler):
                handler.ident = ident


def _configured_log_level(name):
    # Check for configuration override
    level = getattr(config, 'LOGLEVEL_' + name.upper(), None)
    if not level:
        return logging.INFO
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError('Invalid log level: %s' % level)
    return numeric_level


def set_log_level(log, name):
    # Check that id is a valid name
    process_name(name)

    log.setLevel(_configured_log_level(name))


def apply_log_level(daemon_name):
    """Apply this daemon's configured log level to every shakenfist logger.

    ``logs.setup()`` leaves the root logger at DEBUG and gives each
    module its own child logger with no explicit level, so until a
    level is set on the *package* logger every module ships debug
    records (privexec command dumps, concurrency output captures, ...)
    to syslog and Loki no matter what ``LOGLEVEL_*`` says --
    ``set_log_level()`` only quiets the single module logger it is
    handed. Setting the level once here makes every ``shakenfist.*``
    logger inherit it. Daemons without a ``LOGLEVEL_*`` config field
    default to info.
    """
    logging.getLogger('shakenfist').setLevel(
        _configured_log_level(daemon_name))


def write_pid_file(daemon_name):
    # The one universal per-daemon startup hook (see below), so this
    # is also where the configured log level is enforced.
    apply_log_level(daemon_name)

    with open(f'/run/sf/{daemon_name}.pid', 'w') as f:
        f.write(f'{os.getpid()}')

    # Initialise the local eventlog spool + drainer for this
    # process. The drainer is started once per daemon process,
    # at the same time as the pid file is written, so any
    # downstream startup events land in the spool rather than
    # paying the synchronous gRPC tax on the critical path. The
    # call is intentionally inside ``write_pid_file`` so every
    # daemon picks it up without per-daemon boilerplate -- this
    # function is already the one universal startup hook.
    #
    # Late import (rather than top-of-file) because the
    # ``eventlog_drainer`` -> ``eventlog_spool`` chain pulls in
    # the proto stubs, and not every site that imports
    # ``shakenfist.daemons.daemon`` wants to load those at
    # module-import time. We accept the per-daemon-startup
    # import cost.
    from shakenfist import eventlog_drainer  # noqa: E402
    eventlog_drainer.start(daemon_name)

    # Wire up Loki log shipping for this process. Like the eventlog
    # drainer above this spawns a background thread (when
    # LOKI_BASE_URL is set), so it belongs at the same per-process
    # startup hook. The call is a no-op when LOKI_BASE_URL is empty.
    # Same late-import rationale as eventlog_drainer: the
    # logship -> logship_spool chain pulls in sqlite/prometheus
    # bookkeeping we don't want at module-import time.
    from shakenfist import logship  # noqa: E402
    logship.start(daemon_name)


def clear_abort_path(abort_path):
    if os.path.exists(abort_path):
        LOG.info(f'Clearing abort file: {abort_path}')
        os.unlink(abort_path)


def set_abort_path(abort_path, source):
    LOG.info(f'Setting abort file: {abort_path} ({source})')
    with open(abort_path, 'w') as f:
        f.write('1')


def check_abort_path(abort_path):
    return not os.path.exists(abort_path)


def health_check_privexec():
    try:
        util_concurrency.execute('whoami')
    except ProcessExecutionError as e:
        LOG.with_fields({
            'stdout': e.stdout,
            'stderr': e.stderr,
            'exit_code': e.exit_code
        }).warning('privsep daemon is unhealthy (execution error)!')
        return False
    except ConnectionResetError:
        LOG.warning('privsep daemon is unhealthy (connection reset)!')
        return False

    return True


def health_check_nodelock():
    try:
        with util_concurrency.NodeLock('_health_check'):
            ...
    except (ConnectionError, FileNotFoundError, MissingNodeLockSocket,
            TruncatedNodeLockResponse) as e:
        # nodelock only unlinks its socket at startup, so while it is
        # restarting (for example during a deploy) clients see a range of
        # transient failures rather than one exception type: the stale
        # socket file refuses connections, the startup unlink races
        # connect() (FileNotFoundError), an accepted connection can be
        # reset or aborted or closed without a reply mid-shutdown, and a
        # not-yet-created socket is missing entirely. They all mean the
        # same thing here, and any of them escaping bypasses
        # wait_for_nodelock()'s calm 1Hz retry loop.
        LOG.warning(f'nodelock daemon is unhealthy ({e.__class__.__name__})!')
        return False

    return True


def health_check_api():
    # 5 s rather than 2 s because the API request crosses a process
    # boundary into a gunicorn worker that may be in the middle of
    # another request; under any sustained MariaDB latency the 2 s
    # budget is unreachable even when sf-api is fundamentally
    # healthy. sf-queues' caller (_block_until_healthy) already
    # tolerates a 60 s window of consecutive failures before
    # declaring degraded, so a longer per-probe budget here just
    # trades probe latency for fewer false positives.
    try:
        r = requests.get(
            f'http://{config.NODE_MESH_IP}:13000/', timeout=5)
        return r.status_code == 200
    except requests.exceptions.RequestException as e:
        LOG.warning(f'api daemon is unhealthy ({e})!')
        return False


class IdlePollBackoff:
    """Adaptive sleep for idle dequeue loops.

    Call reset() whenever a poll finds work (returns to fast polling) and
    next_empty_interval() whenever a poll comes back empty (returns the sleep
    to use this time and grows the next one, capped at the maximum). The first
    empty poll still sleeps the fast interval, so a brief lull is cheap; only a
    sustained idle period reaches the cap.
    """

    def __init__(self, fast=IDLE_POLL_FAST_SECONDS,
                 maximum=IDLE_POLL_MAX_SECONDS,
                 factor=IDLE_POLL_BACKOFF_FACTOR):
        self._fast = fast
        self._max = maximum
        self._factor = factor
        self._current = fast

    def reset(self):
        """A poll found work; return to the fast polling interval."""
        self._current = self._fast

    def next_empty_interval(self):
        """A poll was empty; return this sleep and grow the next one."""
        interval = self._current
        self._current = min(self._current * self._factor, self._max)
        return interval


class Daemon:
    def __init__(self, name):
        self.daemon_name = name
        # Record this daemon's name process-globally so the sf-database gRPC
        # client interceptor can attribute each call to it.
        set_caller_identity(name)

        procname = process_name(name)
        setproctitle.setproctitle(procname)
        util_concurrency.set_thread_name(procname)
        self.log, _ = logs.setup(name)
        set_log_level(self.log, name)
        set_syslog_ident(procname)

        self.abort_path = f'/run/sf/{name}.abort'
        clear_abort_path(self.abort_path)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

        faulthandler.register(signal.SIGUSR1)

        self.last_stability = None
        self.last_stability_log = 0
        self._last_watchdog = 0.0
        self._last_daemon_state_check = 0.0
        self._daemon_state_poll_interval = DAEMON_STATE_POLL_INTERVAL

        # Let the database gRPC retry loop pet the watchdog between
        # attempts. A main-loop database call on the default budget blocks
        # for up to GRPC_RETRIES * GRPC_TIMEOUT plus backoff -- past
        # WatchdogSec -- while correctly waiting out a slow database, which
        # is how the 2026-08-16 stall SIGABRT-killed every non-database
        # daemon cluster-wide (issue 3789).
        mariadb.set_watchdog_petter(self._pet_watchdog_from_database_wait)

    def _resolve_node_uuid(self):
        """Populate config.NODE_UUID if not already set.

        config.NODE_UUID may be None when SHAKENFIST_NODE_UUID was not
        set in the environment. We resolve it from the persisted UUID
        file (written by sentinel_first) or by looking up this node's
        FQDN in the database.
        """
        if config.NODE_UUID:
            return

        node_uuid = Node._load_persisted_uuid()
        if not node_uuid:
            n = Node.from_db(config.NODE_NAME)
            if n:
                node_uuid = str(n.uuid)

        if node_uuid:
            config.NODE_UUID = node_uuid
            LOG.with_fields({'node_uuid': node_uuid}).info('Resolved node UUID')

    def run(self):
        try:
            LOG.info('Starting')
            self.record_start()
            self._resolve_node_uuid()

            self._run_inner()
        except ValueError as e:
            # This value error is caused by grpc getting confused by channels
            # shutting down in other threads as we terminate our processes
            # at graceful shutdown. Given we recreate the channel if we need it,
            # its safe to ignore.
            if str(e) != 'Cannot monitor channel state: Channel closed!':
                LOG.warning('Unhandled top level value error: {e}')
                raise e
        finally:
            LOG.info('Terminated')
            self.record_exit()

    def _log_stability(self, log, msg):
        if (self.last_stability == msg and
                time.time() - self.last_stability_log < 10):
            return

        self.last_stability = msg
        log.debug(msg)
        self.last_stability_log = time.time()

    def cluster_stable(self):
        # Does the cluster have a stable set of object versions across nodes?
        # We should generally avoid cleanup operations if there is still an
        # upgrade in flight.
        for objname in OBJECT_NAMES_TO_CLASSES:
            current_version = get_object_class(objname).current_version
            minimum = get_minimum_object_version(objname, max_cache_age=5)
            maximum = get_maximum_object_version(objname, max_cache_age=5)
            log = LOG.with_fields({
                'object': objname,
                'minimum_version': minimum,
                'maximum_version': maximum,
                'current_version': current_version
            })

            if maximum == -1:
                self._log_stability(
                    log, 'Cluster not yet stable (no maximum recorded)')
                return False
            if maximum != current_version:
                self._log_stability(
                    log, 'Cluster not yet stable (maximum is not the current version)')
                return False

            if minimum == inf:
                self._log_stability(
                    log, 'Cluster not yet stable (no minimum recorded)')
                return False
            if minimum != current_version:
                self._log_stability(
                    log, 'Cluster not yet stable (minimum is not the current version)')
                return False

        # We don't use the helper here because we don't want to emit this every
        # ten seconds.
        msg = 'Cluster is stable'
        if self.last_stability != msg:
            log.debug(msg)
            self.last_stability = msg
        return True

    def exit_gracefully(self, sig, _frame):
        if sig == signal.SIGTERM:
            self.log.info('Caught SIGTERM, terminating')
            try:
                n = Node.this_node()
                n.set_daemon_state(
                    self.daemon_name, Node.DAEMON_STATE_STOPPING)
            except ValueError:
                # This might fail if grpc has already started shutting down
                ...

            set_abort_path(self.abort_path, 'from exit_gracefully')

    def check_daemon_state(self):
        # Callers hit this on every 0.2s idle tick, but the database read only
        # needs to notice an externally written stop request (local SIGTERM is
        # handled by exit_gracefully() + the abort_path check in idle()), so we
        # rate-limit it to DAEMON_STATE_POLL_INTERVAL. The timestamp is advanced
        # before the read so a database outage is polled at that cadence rather
        # than hammered every tick.
        now = time.time()
        if now - self._last_daemon_state_check < self._daemon_state_poll_interval:
            return
        self._last_daemon_state_check = now

        # Read this daemon's own state row directly by node UUID. The UUID is
        # resolved into config.NODE_UUID at startup (_resolve_node_uuid), so we
        # avoid a get_node round trip just to reach a daemon-state accessor. An
        # unreachable database must not propagate from here -- we just can't
        # check right now, and will again shortly.
        #
        # bounded=True because this poll runs inside idle(), upstream of the
        # systemd watchdog pet: a slow database must make the poll fail fast,
        # not block past WatchdogSec and get the daemon SIGABRT-killed. That
        # was the coredump mechanism in issue 3586, on both the database
        # daemon's direct path and other daemons' gRPC path.
        node_uuid = config.NODE_UUID
        if not node_uuid:
            return
        try:
            row = mariadb.get_node_daemon_state(
                uuid.UUID(node_uuid), self.daemon_name, bounded=True)
        except DatabaseUnavailable:
            # Back off rather than re-asking a database which just told us it
            # could not answer within the bounded deadline. See the comment on
            # DAEMON_STATE_POLL_MAX_INTERVAL.
            self._daemon_state_poll_interval = min(
                self._daemon_state_poll_interval * 2,
                DAEMON_STATE_POLL_MAX_INTERVAL)
            return
        self._daemon_state_poll_interval = DAEMON_STATE_POLL_INTERVAL
        daemon_state = row.value if row is not None else None
        if daemon_state in [Node.DAEMON_STATE_STOPPED,
                            Node.DAEMON_STATE_STOPPING]:
            set_abort_path(self.abort_path, 'from check_daemon_state')

    def record_start(self):
        n = Node.this_node()
        n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_RUNNING)
        n.add_event(EVENT_TYPE_AUDIT, f'{self.daemon_name} daemon starting')
        send_systemd_ready()

    def record_exit(self):
        n = Node.this_node()
        try:
            n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_STOPPED)
        except InvalidStateException as e:
            # Sometimes we race between the node going into stopping before the
            # daemons all start to stop.
            if not str(e).startswith('Invalid state change from stopping to degraded'):
                raise e
        n.add_event(EVENT_TYPE_AUDIT, f'{self.daemon_name} daemon stopped')
        send_systemd_status('Terminated')

    def pet_watchdog(self):
        # Emit a systemd WATCHDOG=1 liveness signal, rate-limited to
        # WATCHDOG_PET_INTERVAL so the 0.2s idle() tick does not sendto() on
        # every tick. The underlying helper is gated on NOTIFY_SOCKET, so this
        # is a no-op outside systemd.
        now = time.time()
        if now - self._last_watchdog >= WATCHDOG_PET_INTERVAL:
            send_systemd_watchdog()
            self._last_watchdog = now

    def _pet_watchdog_from_database_wait(self):
        # Installed into mariadb.set_watchdog_petter() and called between
        # gRPC retry attempts. Only the main thread pets: the watchdog
        # exists to catch a wedged main loop, and a worker thread retrying
        # against a slow database must not keep a genuinely wedged main
        # loop alive.
        if threading.current_thread() is threading.main_thread():
            self.pet_watchdog()

    def idle(self, seconds):
        # round(), not int(): the loop sleeps in 0.2s chunks, and truncating
        # dropped fractional chunks (idle(0.8) slept 0.6s) -- harmless for the
        # integer callers but wrong for the adaptive backoff intervals, which
        # are fractional multiples of 0.2.
        for _ in range(max(1, round(seconds / 0.2))):
            time.sleep(0.2)
            self.check_daemon_state()
            self.pet_watchdog()
            if os.path.exists(self.abort_path):
                break

    def wait_for_nodelock(self):
        # Block until the node-local nodelock daemon is healthy, petting the
        # systemd watchdog (via idle) while we wait. nodelock can be briefly
        # unhealthy during startup or node churn; without petting, a daemon
        # whose unit has WatchdogSec armed and which is correctly waiting would
        # trip the watchdog and be SIGABRT-killed. The abort check in the loop
        # condition lets shutdown break out promptly.
        while not health_check_nodelock() and check_abort_path(self.abort_path):
            LOG.info('Waiting for nodelock daemon to be healthy')
            self.idle(1)


def force_clean_exit():
    # Skip the interpreter's normal teardown and exit immediately.
    #
    # Some gRPC client channels (notably the thread-local eventlog channel
    # in shakenfist.eventlog) leave C-level background threads alive that
    # block ordinary interpreter shutdown when the server-side has already
    # gone away. record_exit() has already flushed the audit state, so
    # anything still running here is not productive work -- waiting for it
    # only buys a systemd SIGKILL after TimeoutStopSec.
    #
    # Before exiting, log any non-daemon Python threads still alive. Those
    # are leakers we control (the C++ gRPC event_engine threads we cannot)
    # and naming them in syslog makes future regressions visible.
    main_thread = threading.main_thread()
    leakers = [
        (t.name, t.ident) for t in threading.enumerate()
        if t is not main_thread and not t.daemon
    ]
    if leakers:
        LOG.warning(
            f'Non-daemon Python threads still alive at exit: {leakers}')
    os._exit(0)


def _send_systemd_notification(message):
    # If running systemd and we are Type=notify...
    addr = os.environ.get('NOTIFY_SOCKET')
    if addr:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        sock.connect(addr)
        sock.sendall(message)
        sock.close()


def send_systemd_ready():
    _send_systemd_notification(b'READY=1')


def send_systemd_stopping():
    _send_systemd_notification(b'STOPPING=1')


def send_systemd_status(message):
    _send_systemd_notification(f'STATUS={message}'.encode('utf-8'))


def send_systemd_watchdog():
    _send_systemd_notification(b'WATCHDOG=1')


class WorkerPoolDaemon(Daemon):
    def __init__(self, name):
        super().__init__(name)
        self.workers = {}
        self.present_cpus = util_libvirt.get_cpu_count()

        self.metrics = {}
        self.metrics_acquired_at = 0

    def run(self):
        try:
            LOG.info('Starting')
            self.record_start()
            self._resolve_node_uuid()

            self._run_inner()
        except ValueError as e:
            # This value error is caused by grpc getting confused by channels
            # shutting down in other threads as we terminate our processes
            # at graceful shutdown. Given we recreate the channel if we need it,
            # its safe to ignore.
            if str(e) != 'Cannot monitor channel state: Channel closed!':
                LOG.warning('Unhandled top level value error: {e}')
                raise e
        finally:
            LOG.info('Stopping')

            while len(self.workers) > 0:
                for thread_name in self.workers:
                    thread_ident = self.workers[thread_name]['thread'].ident
                    set_abort_path(
                        self.workers[thread_name]['object'].abort_path,
                        'worker thread cleanup')
                    LOG.info(f'Sent exit event to {thread_name} thread '
                             f'with ident {thread_ident}')

                if len(self.workers) > 0:
                    time.sleep(5)

                self.reap_workers()

            LOG.info(f'There are {len(self.workers)} remaining workers')
            LOG.info('Stopped')
            self.record_exit()

    def reap_workers(self):
        remaining_workers = {}
        for thread_name in self.workers:
            if self.workers[thread_name]['thread'].is_alive():
                remaining_workers[thread_name] = self.workers[thread_name]
            else:
                thread_ident = self.workers[thread_name]['thread'].ident
                LOG.info(f'Reaping thread {thread_name} with ident '
                         f'{thread_ident}')
                self.workers[thread_name]['thread'].join(0.2)

        self.workers = remaining_workers
        send_systemd_status(f'{len(self.workers)} active workers')

    def start_job(self, processing_class, args, name):
        worker_object = processing_class(*args)
        worker_thread = threading.Thread(
            target=worker_object.run, daemon=True, name=name)
        self.workers[name] = {
            'object': worker_object,
            'thread': worker_thread
        }
        worker_thread.start()

    def dequeue_job(self, processing_class):
        """Fill spare worker slots from the highest-priority eligible work.

        Composes the list of eligible queues based on current
        capacity and local disk pressure, then issues a single
        ``dequeue_work_items`` call with ``limit`` set to the number
        of free slots. The MariaDB-side ``ORDER BY FIELD()`` means
        the highest-priority work is returned first; lower-priority
        rows only fill remaining limit slots if the higher ones are
        exhausted (so a steady stream of user-facing work still
        crowds background out, exactly as before).

        Returns True if at least one job was started, False if the
        pool is full or there was nothing eligible to claim.
        """
        max_workers = max(3, self.present_cpus / 2)
        num_workers = len(self.workers)

        if num_workers > max_workers:
            return False

        # Compose the queue list in caller-supplied priority order.
        # ``get_all_user_facing_node_queues`` always participates;
        # background queues are added only when we have headroom past
        # the user-facing reservation. This preserves the previous
        # behaviour where 2 worker slots are reserved for user-facing
        # work, but does it by gating *what we ask for* rather than
        # making a second round trip.
        queue_names = list(
            get_all_user_facing_node_queues(config.NODE_UUID))

        if num_workers <= max_workers - 2:
            # Refresh disk metrics on the same 30 s cadence as before.
            # ``DISK_BUSY_PER_SECOND_METRIC`` reports milliseconds per
            # second; a value over 800 (~80%) gates ``high_io``
            # background work so a saturated disk doesn't get more
            # piled onto it.
            if time.time() - self.metrics_acquired_at > 30:
                new_metrics = mariadb.get_node_metrics(config.NODE_UUID)
                if new_metrics:
                    self.metrics = new_metrics
                    self.metrics_acquired_at = time.time()
                else:
                    self.metrics = {}
                    self.metrics_acquired_at = 0

            metrics_values = self.metrics.get('metrics', {})
            disk_busy = float(metrics_values.get(
                DISK_BUSY_PER_SECOND_METRIC, 0))
            for queue_name in get_all_background_node_queues(
                    config.NODE_UUID):
                if 'high_io' in queue_name and disk_busy > 800:
                    LOG.debug(
                        f'Skipping {queue_name} queue as local disk '
                        f'is busy')
                    continue
                queue_names.append(queue_name)

        # Ask for one job per free slot. ``int()`` because
        # ``max_workers`` is a float (``cpus / 2``).
        free_slots = max(1, int(max_workers) - num_workers)
        items = mariadb.dequeue_work_items(queue_names, limit=free_slots)
        if not items:
            return False

        # Pass the batch size to each spawned worker so its op's
        # ``execute()`` can skip the coalescing fold when the dispatcher
        # just saw an empty queue (see ``BaseClusterOperation.execute``).
        batch_size = len(items)
        for queue_name, jobname, workitem in items:
            args = [queue_name, jobname, workitem, batch_size]
            self.start_job(processing_class, args, jobname)
        return True
