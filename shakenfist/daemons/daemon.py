import faulthandler
import logging
import setproctitle
from shakenfist_utilities import logs
import signal
from threading import Event

from shakenfist.config import config
from shakenfist import etcd
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import process as util_process


DAEMON_NAMES = {
    'api': 'sf-api',
    'checksum': 'sf-checksum',
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
        setproctitle.setproctitle(process_name(name))
        self.log, _ = logs.setup(name)
        set_log_level(self.log, name)

        self.exit = Event()
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

    def reap_workers(self):
        for workname in list(self.workers.keys()):
            p = self.workers[workname]
            if not p.is_alive():
                p.join(1)
                del self.workers[workname]

    def start_workitem(self, processing_callback, args, name):
        p = util_process.fork(processing_callback, args,
                              '{}-{}'.format(process_name('queues'), name))
        self.workers[name] = p
        return p.pid

    def dequeue_work_item(self, queue_name, processing_callback):
        if len(self.workers) > self.present_cpus / 2:
            return False

        jobname_workitem = etcd.dequeue(queue_name)
        if not jobname_workitem:
            return False
        self.start_workitem(processing_callback, jobname_workitem, 'worker')
