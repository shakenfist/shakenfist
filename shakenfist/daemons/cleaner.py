import logging
from logging import handlers as logging_handlers
import setproctitle
import time

from shakenfist import config
from shakenfist import db


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf cleaner')

    def run(self):
        while True:
            delay = config.parsed.get('CLEANER_DELAY')

            for i in db.get_stale_instances(delay):
                LOG.info('Hard deleting instance %s' % i['uuid'])
                db.hard_delete_instance(i['uuid'])

            for n in db.get_stale_networks(delay):
                LOG.info('Hard deleting network %s' % n['uuid'])
                db.hard_delete_network(n['uuid'])

            time.sleep(60)
