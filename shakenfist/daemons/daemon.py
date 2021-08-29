import copy
import logging
import multiprocessing
import setproctitle

from shakenfist.config import config
from shakenfist import db
from shakenfist import logutil
from shakenfist.util import libvirt as util_libvirt


DAEMON_NAMES = {
    'api': 'sf-api',
    'cleaner': 'sf-cleaner',
    'main': 'sf-main',
    'net': 'sf-net',
    'queues': 'sf-queues',
    'resources': 'sf-resources',
    'triggers': 'sf-triggers',
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
        log, _ = logutil.setup(name)
        set_log_level(log, name)


class WorkerPoolDaemon(Daemon):
    def __init__(self, name):
        super(WorkerPoolDaemon, self).__init__(name)
        self.workers = []

        libvirt = util_libvirt.get_libvirt()
        conn = libvirt.open('qemu:///system')
        self.present_cpus, _, _ = conn.getCPUMap()

    def reap_workers(self):
        for w in copy.copy(self.workers):
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

        jobname, workitem = db.dequeue(queue_name)
        if not workitem:
            return False
        self.start_workitem(processing_callback, (jobname, workitem), 'worker')
