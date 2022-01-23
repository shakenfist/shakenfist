from collections import defaultdict
import time

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import logutil
from shakenfist.util import general as util_general


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.WorkerPoolDaemon):
    def _process_queued_events(self):
        while True:
            results = defaultdict(list)

            for k, v in etcd.get_all('event', None, limit=10000):
                _, _, _, objtype, objuuid, _ = k.split('/')
                results[(objtype, objuuid)].append((k, v))

            for objtype, objuuid in results:
                with eventlog.EventLog(objtype, objuuid) as eventdb:
                    for k, v in results[(objtype, objuuid)]:
                        eventdb.write_event(
                            v['timestamp'], v['fqdn'], v['operation'], v['phase'],
                            v['duration'], v['message'])
                        etcd.WrappedEtcdClient().delete(k)

            if not results:
                time.sleep(0.2)

    def run(self):
        LOG.info('Starting')

        eventlog_worker = None

        while True:
            try:
                self.reap_workers()
                worker_pids = []
                for w in self.workers:
                    worker_pids.append(w.pid)

                if config.NODE_IS_EVENTLOG_NODE:
                    if eventlog_worker not in worker_pids:
                        eventlog_worker = self.start_workitem(
                            self._process_queued_events, [], 'eventlog-worker')

            except Exception as e:
                util_general.ignore_exception('eventlog daemon', e)
