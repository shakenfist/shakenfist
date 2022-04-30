from collections import defaultdict
import etcd3
import json
import threading
import time

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import logutil
from shakenfist.util import general as util_general


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.WorkerPoolDaemon):
    def __init__(self, name):
        super(Monitor, self).__init__(name)
        self.lock = threading.Lock()

    def _watch_events(self, watchresponse):
        for event in watchresponse.events:
            if isinstance(event, etcd3.events.PutEvent):
                with self.lock:
                    k = event.key
                    _, _, _, objtype, objuuid, _ = k.split('/')
                    v = json.loads(event.value)

                    try:
                        with eventlog.EventLog(objtype, objuuid) as eventdb:
                            eventdb.write_event(
                                v['timestamp'], v['fqdn'], v['operation'], v['phase'],
                                v['duration'], v['message'], extra=v.get('extra'))
                            etcd.WrappedEtcdClient().delete(k)
                    except Exception as e:
                        util_general.ignore_exception(
                            'failed to write event for %s %s' % (objtype, objuuid), e)

    def run(self):
        LOG.info('Starting')

        try:
            # Add a watcher for new events
            self.client = etcd3.client(host=config.ETCD_HOST)
            watch_id = self.client.add_watch_prefix_callback(
                '/sf/events/', self._watch_events)

            # Churn through the backlog at startup quickly
            backlog = True
            while backlog:
                results = defaultdict(list)

                with self.lock:
                    for k, v in etcd.get_all('event', None, limit=10000):
                        _, _, _, objtype, objuuid, _ = k.split('/')
                        results[(objtype, objuuid)].append((k, v))

                    for objtype, objuuid in results:
                        try:
                            with eventlog.EventLog(objtype, objuuid) as eventdb:
                                for k, v in results[(objtype, objuuid)]:
                                    eventdb.write_event(
                                        v['timestamp'], v['fqdn'], v['operation'], v['phase'],
                                        v['duration'], v['message'], extra=v.get('extra'))
                                    etcd.WrappedEtcdClient().delete(k)
                        except Exception as e:
                            util_general.ignore_exception(
                                'failed to write event for %s %s' % (objtype, objuuid), e)

                LOG.info('Batch processed %d queued events' % len(results))
                if not results:
                    backlog = False
                else:
                    time.sleep(1)

            # And now just idle processing callbacks as needed
            while not self.exit.is_set():
                time.sleep(1)

            # Cancel our watch
            self.client.cancel_watch(watch_id)

        except Exception as e:
            util_general.ignore_exception('eventlog daemon', e)
