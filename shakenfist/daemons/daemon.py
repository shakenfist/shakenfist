import faulthandler
import logging
import signal
<<<<<<< HEAD
from threading import Event
import time

import psutil
=======
import threading

import pyprctl
>>>>>>> 9ad7e383 (Convert to using threads to resolve grpc segfault issues.)
import setproctitle
from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist.config import config
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


class Daemon:
    def __init__(self, name):
        procname = process_name(name)
        setproctitle.setproctitle(procname)
        pyprctl.set_name(procname)
        self.log, _ = logs.setup(name)
        set_log_level(self.log, name)

        self.exit = threading.Event()
        signal.signal(signal.SIGTERM, self.exit_gracefully)

        faulthandler.register(signal.SIGUSR1)

    def exit_gracefully(self, sig, _frame):
        if sig == signal.SIGTERM:
            self.log.info('Caught SIGTERM, terminating')
            self.exit.set()


class WorkerPoolDaemon(Daemon):
    def __init__(self, name):
        super().__init__(name)
        self.workers = {}
        self.present_cpus = util_libvirt.get_cpu_count()

        self.age_warnings = {}

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

    def dequeue_job(self, queue_name, processing_class):
        max_workers = self.present_cpus / 2
        num_workers = len(self.workers)

        if num_workers > max_workers:
            return False

        # High priority jobs
        jobname_workitem = etcd.dequeue(queue_name)
        if jobname_workitem:
            args = [queue_name, jobname_workitem[0], jobname_workitem[1]]
            self.start_job(processing_class, args, jobname_workitem[0])
            return True

        # Low priority jobs
        if num_workers > max_workers - 2:
            return False

        jobname_workitem = etcd.dequeue(f'{queue_name}-background')
        if jobname_workitem:
            args = [f'{queue_name}-background', jobname_workitem[0],
                    jobname_workitem[1]]
            self.start_job(processing_class, args, jobname_workitem[0])
            return True

        return False
