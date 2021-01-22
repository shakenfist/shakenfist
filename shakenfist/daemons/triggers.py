from functools import partial
import multiprocessing
import os
import re
import setproctitle
import signal
import time

from shakenfist import baseobject
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import logutil
from shakenfist import virt


LOG, _ = logutil.setup(__name__)


def observe(path, instance_uuid):
    setproctitle.setproctitle(
        '%s-%s' % (daemon.process_name('triggers'), instance_uuid))
    regexps = {
        'login prompt': ['^.* login: .*', re.compile('.* login: .*')]
    }

    while not os.path.exists(path):
        time.sleep(1)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    log_ctx = LOG.with_instance(instance_uuid)
    log_ctx.with_field('path', path).info('Monitoring path for triggers')
    db.add_event('instance', instance_uuid, 'trigger monitor',
                 'detected console log', None, None)

    # Sometimes the trigger process is slow to start, so rewind 4KB to ensure
    # that the last few log lines are not missed. (4KB since Cloud-Init can be
    # noisy after the login prompt.)
    os.lseek(fd, max(0, os.fstat(fd).st_size - 4096), os.SEEK_SET)

    buffer = ''
    while True:
        d = os.read(fd, 1024).decode('utf-8', errors='ignore')
        if d:
            buffer += d
            lines = buffer.split('\n')
            buffer = lines[-1]

            for line in lines:
                if line:
                    for trigger in regexps:
                        m = regexps[trigger][1].match(line)
                        if m:
                            log_ctx.with_field('trigger', trigger,
                                               ).info('Trigger matched')
                            db.add_event('instance', instance_uuid, 'trigger',
                                         None, None, trigger)
        else:
            # Only pause if there was no data to read
            time.sleep(1)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting Monitor Daemon')
        observers = {}

        while True:
            # Cleanup terminated observers
            all_observers = list(observers.keys())
            for instance_uuid in all_observers:
                if not observers[instance_uuid].is_alive():
                    # Reap process
                    observers[instance_uuid].join(1)
                    LOG.with_instance(instance_uuid
                                      ).info('Trigger observer has terminated')
                    db.add_event(
                        'instance', instance_uuid, 'trigger monitor', 'crashed', None, None)
                    del observers[instance_uuid]

            # Start missing observers
            extra_instances = list(observers.keys())

            for inst in virt.Instances([virt.this_node_filter,
                                        partial(baseobject.state_filter, ['created'])]):
                if inst.uuid in extra_instances:
                    extra_instances.remove(inst.uuid)

                if inst.uuid not in observers:
                    console_path = os.path.join(config.get('STORAGE_PATH'),
                                                'instances',
                                                inst.uuid,
                                                'console.log')
                    p = multiprocessing.Process(
                        target=observe, args=(console_path, inst.uuid),
                        name='%s-%s' % (daemon.process_name('triggers'),
                                        inst.uuid))
                    p.start()

                    observers[inst.uuid] = p
                    LOG.with_instance(inst.uuid).info(
                        'Started trigger observer')
                    db.add_event(
                        'instance', inst.uuid, 'trigger monitor', 'started', None, None)

            # Cleanup extra observers
            for instance_uuid in extra_instances:
                p = observers[instance_uuid]
                try:
                    os.kill(p.pid, signal.SIGKILL)
                    observers[instance_uuid].join(1)
                except Exception:
                    pass

                del observers[instance_uuid]
                LOG.with_instance(instance_uuid).info(
                    'Finished trigger observer')
                db.add_event(
                    'instance', instance_uuid, 'trigger monitor', 'finished', None, None)

            time.sleep(1)
