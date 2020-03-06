# Copyright 2020 Michael Still

import logging
import time

from oslo_concurrency import processutils


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class RecordedOperation():
    def __init__(self, operation, object, callback=None):
        self.operation = operation
        self.object = object
        self.callback = callback

    def __enter__(self):
        self.start_time = time.time()
        LOG.info('%s: Start %s' % (self.object, self.operation))
        if self.callback:
            self.callback({
                'event': 'start',
                'operation': self.operation,
                'object': str(self.object)
            })
        return self

    def __exit__(self, *args):
        LOG.info('%s: Finish %s, duration %.02f seconds'
                 % (self.object, self.operation,
                    time.time() - self.start_time))
        if self.callback:
            self.callback({
                'event': 'finish',
                'operation': self.operation,
                'object': str(self.object)
            })

    def heartbeat(self, status=None):
        LOG.info('%s: Heartbeat %s, status %s'
                 % (self.object, self.operation, status))
        if self.callback:
            self.callback({
                'event': 'heartbeat',
                'operation': self.operation,
                'object': str(self.object),
                'status': status
            })


def check_for_interface(name):
    stdout, stderr = processutils.execute(
        'ip link show %s' % name, check_exit_code=[0, 1], shell=True)
    return not stderr.rstrip('\n').endswith(' does not exist.')
