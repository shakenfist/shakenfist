import logging
import time


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class RecordedOperation():
    def __init__(self, operation, instance):
        self.operation = operation
        self.instance = instance

    def __enter__(self):
        self.start_time = time.time()
        LOG.info('%s: Start %s' % (self.instance, self.operation))
        return self

    def __exit__(self, *args):
        LOG.info('%s: Finish %s, duration %.02f seconds'
                 % (self.instance, self.operation,
                    time.time() - self.start_time))
