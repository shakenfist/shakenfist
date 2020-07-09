import etcd3
import json
import logging
from logging import handlers as logging_handlers
import setproctitle
import time

from shakenfist import config
from shakenfist import db
from shakenfist import db


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf cleaner')

    def run(self):
        last_compaction = 0

        while True:
            # Cleanup soft deleted instances and networks
            delay = config.parsed.get('CLEANER_DELAY')

            for i in db.get_stale_instances(delay):
                LOG.info('Hard deleting instance %s' % i['uuid'])
                db.hard_delete_instance(i['uuid'])

            for n in db.get_stale_networks(delay):
                LOG.info('Hard deleting network %s' % n['uuid'])
                db.hard_delete_network(n['uuid'])

            for ni in db.get_stale_network_interfaces(delay):
                LOG.info('Hard deleting network interface %s' % ni['uuid'])
                db.hard_delete_network_interface(ni['uuid'])

            # Perform etcd maintenance
            if time.time() - last_compaction > 1800:
                # We need to determine what revision to compact to, so we keep a
                # key which stores when we last compacted and we use it's latest
                # revision number as the revision to compact to.
                c = etcd3.client()
                c.put('/sf/compact',
                      json.dumps({'compacted_at': time.time()}))
                _, kv = c.get('/sf/compact')
                c.compact(kv.mod_revision, physical=True)
                c.defragment()

                last_compaction = time.time()

            time.sleep(60)
