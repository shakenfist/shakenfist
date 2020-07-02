import logging
from logging import handlers as logging_handlers
import os
import select
import setproctitle
import time

from shakenfist import config
from shakenfist import db


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf triggers')

    def run(self):
        files_by_instance = {}
        files_by_fileno = {}
        file_objects = []

        queued_data = {}

        while True:
            for inst in list(db.get_instances(only_node=config.parsed.get('NODE_NAME'))):
                if inst['uuid'] not in files_by_instance:
                    try:
                        f = open(os.path.join(
                            config.parsed.get('STORAGE_PATH'), 'instances', inst['uuid'], 'console.log'))
                        f.seek(0, 2)
                        files_by_instance[inst['uuid']] = f
                        files_by_fileno[f.fileno()] = inst['uuid']

                    except FileNotFoundError:
                        pass

            readable, _, exceptional = select.select(
                file_objects, [], file_objects, 0.5)

            for f in exceptional:
                instance_uuid = files_by_fileno.get(f.fileno())
                if instance_uuid and instance_uuid in files_by_instance:
                    del files_by_instance[instance_uuid]
                if f.fileno() in files_by_fileno:
                    del files_by_fileno[f.fileno()]
                if f in file_objects:
                    file_objects.remove(f)

            for f in readable:
                d = f.read(1)
                instance_uuid = files_by_fileno.get(f.fileno())
                if instance_uuid:
                    queued_data.setdefault(instance_uuid, '')
                    if d != '\n':
                        queued_data[instance_uuid] += d
                    else:
                        LOG.info('Read from %s console: %s' %
                                 (instance_uuid, queued_data[instance_uuid]))
                        queued_data[instance_uuid] = ''
