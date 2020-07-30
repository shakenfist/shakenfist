import logging
from logging import handlers as logging_handlers
import multiprocessing
import os
import re
import setproctitle
import signal
import time

from shakenfist import config
from shakenfist import db


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


def observe(path, instance_uuid):
    regexps = {
        'login prompt': ['^.* login: .*', re.compile('.* login: .*')]
    }

    while not os.path.exists(path):
        time.sleep(1)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    LOG.info('Monitoring %s for triggers' % path)
    db.add_event('instance', instance_uuid, 'trigger monitor',
                 'detected console log', None, None)
    os.lseek(fd, 0, os.SEEK_END)

    buffer = ''
    while True:
        d = os.read(fd, 1024).decode('utf-8')
        if d:
            LOG.debug('Trigger observer read %d bytes for instance %s'
                      % (len(d), instance_uuid))
            buffer += d
            lines = buffer.split('\n')
            buffer = lines[-1]

            for line in lines:
                if line:
                    LOG.debug('Trigger observer checks "%s" for instance %s'
                              % (line, instance_uuid))
                    for trigger in regexps:
                        m = regexps[trigger][1].match(line)
                        if m:
                            LOG.info('Trigger %s matched for instance %s'
                                     % (trigger, instance_uuid))
                            db.add_event('instance', instance_uuid, 'trigger',
                                         None, None, trigger)

        time.sleep(1)


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf triggers')

    def run(self):
        observers = {}

        while True:
            # Cleanup terminated observers
            all_observers = list(observers.keys())
            for instance_uuid in all_observers:
                if not observers[instance_uuid].is_alive():
                    LOG.info('Trigger observer for instance %s has terminated'
                             % instance_uuid)
                    db.add_event(
                        'instance', instance_uuid, 'trigger monitor', 'crashed', None, None)
                    del observers[instance_uuid]

            # Start missing observers
            extra_instances = list(observers.keys())
            for inst in list(db.get_instances(only_node=config.parsed.get('NODE_NAME'))):
                if inst['uuid'] in extra_instances:
                    extra_instances.remove(inst['uuid'])

                if inst['state'] != 'created':
                    continue

                if inst['uuid'] not in observers:
                    console_path = os.path.join(
                        config.parsed.get('STORAGE_PATH'), 'instances', inst['uuid'], 'console.log')
                    p = multiprocessing.Process(
                        target=observe, args=(console_path, inst['uuid']),
                        name='sf trigger %s' % inst['uuid'])
                    p.start()

                    observers[inst['uuid']] = p
                    LOG.info('Started trigger observer for instance %s'
                             % inst['uuid'])
                    db.add_event(
                        'instance', inst['uuid'], 'trigger monitor', 'started', None, None)

            # Cleanup extra observers
            for instance_uuid in extra_instances:
                p = observers[instance_uuid]
                try:
                    os.kill(p.pid, signal.SIGKILL)
                except Exception:
                    pass

                del observers[instance_uuid]
                LOG.info('Finished trigger observer for instance %s'
                         % instance_uuid)
                db.add_event(
                    'instance', instance_uuid, 'trigger monitor', 'finished', None, None)

            time.sleep(1)
