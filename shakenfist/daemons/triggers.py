import logging
from logging import handlers as logging_handlers
import multiprocessing
import os
import re
import select
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

    f = None
    while not f:
        try:
            f = open(path)
        except Exception:
            pass
        time.sleep(1)

    LOG.info('Monitoring %s for triggers' % path)
    f.seek(0, os.SEEK_END)
    buffer = ''

    while True:
        d = f.read()
        if d:
            buffer += d
            lines = buffer.split('\n')
            buffer = lines[-1]

            for line in lines[:-1]:
                if line:
                    for trigger in regexps:
                        m = regexps[trigger][1].match(line)
                        if m:
                            LOG.info('Trigger %s matched for %s'
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

            time.sleep(1)
