from collections import defaultdict
import time

from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import logutil
from shakenfist.util import general as util_general


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.WorkerPoolDaemon):
    def run(self):
        LOG.info('Starting')

        while not self.exit.is_set():
            try:
                results = defaultdict(list)

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

                if not results:
                    time.sleep(1)

            except Exception as e:
                util_general.ignore_exception('eventlog daemon', e)

        LOG.info('Terminating')
