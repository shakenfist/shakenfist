import faulthandler
import logging
from math import inf
import os
import signal
import threading
import time

import pyprctl
import setproctitle
from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import get_maximum_object_version
from shakenfist.baseobject import get_minimum_object_version
from shakenfist.baseobject import OBJECT_NAMES
from shakenfist.baseobjectmapping import OBJECT_NAMES_TO_CLASSES
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import etcd
from shakenfist.exceptions import InvalidStateException
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.node import Node
from shakenfist.operations.baseoperation import get_all_user_facing_queue_names
from shakenfist.operations.baseoperation import get_all_background_queue_names
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logs.setup(__name__)


DAEMON_NAMES = {
    'api': 'sf-api',
    'checksums': 'sf-checksums',
    'cleaner': 'sf-cleaner',
    'cluster': 'sf-cluster',
    'eventlog': 'sf-eventlog',
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


def set_log_level(log, name):
    # Check that id is a valid name
    process_name(name)

    # Check for configuration override
    level = getattr(config, 'LOGLEVEL_' + name.upper(), None)
    if level:
        numeric_level = getattr(logging, level.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError('Invalid log level: %s' % level)
    else:
        numeric_level = logging.INFO

    log.setLevel(numeric_level)


def write_pid_file(daemon_name):
    with open(f'/run/sf/{daemon_name}.pid', 'w') as f:
        f.write(f'{os.getpid()}')


def health_check_privexec():
    try:
        stdout, stderr = util_concurrency.execute(None, 'whoami')
    except ProcessExecutionError as e:
        LOG.with_fields({
            'stdout': e.stdout,
            'stderr': e.stderr,
            'exit_code': e.exit_code
        }).error('privsep daemon is unhealthy (execution error)!')
        return False
    except ConnectionResetError:
        LOG.error('privsep daemon is unhealthy (connection reset)!')
        return False

    return True


class Daemon:
    def __init__(self, name):
        self.daemon_name = name

        procname = process_name(name)
        setproctitle.setproctitle(procname)
        pyprctl.set_name(procname)
        self.log, _ = logs.setup(name)
        set_log_level(self.log, name)

        self.abort_path = f'/run/sf/{name}.abort'
        if os.path.exists(self.abort_path):
            os.unlink(self.abort_path)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

        faulthandler.register(signal.SIGUSR1)

        self.last_stability = None
        self.last_stability_log = 0

    def run(self):
        try:
            LOG.info('Starting')
            self.record_start()

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
        for objname in OBJECT_NAMES:
            current_version = OBJECT_NAMES_TO_CLASSES[objname].current_version
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
                n = Node.from_db(config.NODE_NAME)
                n.set_daemon_state(
                    self.daemon_name, Node.DAEMON_STATE_STOPPING)
            except ValueError:
                # This might fail if grpc has already started shutting down
                ...

            with open(self.abort_path, 'w') as f:
                f.write('1')

    def check_daemon_state(self):
        n = Node.from_db(config.NODE_NAME)
        daemon_state = n.get_daemon_state(self.daemon_name).value
        if daemon_state in [Node.DAEMON_STATE_STOPPED,
                            Node.DAEMON_STATE_STOPPING]:
            with open(self.abort_path, 'w') as f:
                f.write('1')

    def record_start(self):
        n = Node.from_db(config.NODE_NAME)
        n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_RUNNING)
        n.add_event(EVENT_TYPE_AUDIT, f'{self.daemon_name} daemon starting')

    def record_exit(self):
        n = Node.from_db(config.NODE_NAME)
        try:
            n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_STOPPED)
        except InvalidStateException as e:
            # Sometimes we race between the node going into stopping before the
            # daemons all start to stop.
            if not str(e).startswith('Invalid state change from stopping to degraded'):
                raise e
        n.add_event(EVENT_TYPE_AUDIT, f'{self.daemon_name} daemon stopped')

    def idle(self, seconds):
        for _ in range(int(seconds / 0.2)):
            time.sleep(0.2)
            self.check_daemon_state()
            if os.path.exists(self.abort_path):
                break


class WorkerPoolDaemon(Daemon):
    def __init__(self, name):
        super().__init__(name)
        self.workers = {}
        self.present_cpus = util_libvirt.get_cpu_count()

    def run(self):
        try:
            LOG.info('Starting')
            self.record_start()

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
                    with open(self.workers[thread_name]['object'].abort_path, 'w') as f:
                        f.write('1')
                    LOG.info(f'Sent exit event to {thread_name} thread '
                             f'with ident {thread_ident}')

                if len(self.workers) > 0:
                    time.sleep(5)

                self.reap_workers()

            LOG.info(f'There are {len(self.workers)} remaining workers')
            LOG.info('Stopped')

            LOG.info('Terminated')
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
        max_workers = max(3, self.present_cpus / 2)
        num_workers = len(self.workers)

        if num_workers > max_workers:
            return False

        for queue_name in get_all_user_facing_queue_names(config.NODE_NAME):
            jobname_workitem = etcd.dequeue(queue_name)
            if jobname_workitem:
                args = [queue_name, jobname_workitem[0], jobname_workitem[1]]
                self.start_job(processing_class, args, jobname_workitem[0])
                return True

        # Lower priority jobs reserve a number of workers for user facing things
        if num_workers > max_workers - 2:
            return False

        for queue_name in get_all_background_queue_names(config.NODE_NAME):
            jobname_workitem = etcd.dequeue(queue_name)
            if jobname_workitem:
                args = [queue_name, jobname_workitem[0], jobname_workitem[1]]
                self.start_job(processing_class, args, jobname_workitem[0])
                return True

        return False
