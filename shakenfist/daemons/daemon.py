import faulthandler
import logging
import multiprocessing
import setproctitle
import signal
from threading import Event

from shakenfist.config import config
from shakenfist import etcd
from shakenfist import logutil
from shakenfist.util import libvirt as util_libvirt


DAEMON_NAMES = {
    'api': 'sf-api',
    'cleaner': 'sf-cleaner',
    'cluster': 'sf-cluster',
    'eventlog': 'sf-eventlog',
    'main': 'sf-main',
    'net': 'sf-net',
    'queues': 'sf-queues',
    'resources': 'sf-resources',
    'sidechannel': 'sf-sidechannel',
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


class Daemon(object):
    def __init__(self, name):
        setproctitle.setproctitle(process_name(name))
        self.log, _ = logutil.setup(name)
        set_log_level(self.log, name)

        self.exit = Event()
        signal.signal(signal.SIGTERM, self.exit_gracefully)

        faulthandler.register(signal.SIGUSR1)

    def exit_gracefully(self, sig, _frame):
        if sig == signal.SIGTERM:
            self.log.info('Caught SIGTERM, commencing shutdown')
            self.exit.set()


class WorkerPoolDaemon(Daemon):
    def __init__(self, name):
        super(WorkerPoolDaemon, self).__init__(name)
        self.workers = []
        self.present_cpus = util_libvirt.get_cpu_count()

    def reap_workers(self):
        for w in list(self.workers):
            if not w.is_alive():
                w.join(1)
                self.workers.remove(w)

    def start_workitem(self, processing_callback, args, name):
        p = multiprocessing.Process(
            target=processing_callback, args=args,
            name='%s-%s' % (process_name('queues'), name))
        p.start()
        self.workers.append(p)
        return p.pid

    def dequeue_work_item(self, queue_name, processing_callback):
        if len(self.workers) > self.present_cpus / 2:
            return False

        jobname, workitem = etcd.dequeue(queue_name)
        if not workitem:
            return False
        self.start_workitem(processing_callback, (jobname, workitem), 'worker')
