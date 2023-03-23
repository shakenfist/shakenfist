from collections import defaultdict
from shakenfist_utilities import logs
import os
import pathlib
import time

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist.eventlog import EVENT_TYPE_HISTORIC
from shakenfist import node
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Monitor(daemon.WorkerPoolDaemon):
    def run(self):
        LOG.info('Starting')
        last_prune = 0

        eventlog.upgrade_data_store()

        # This dance forces all node databases to be opened and possibly upgraded
        # before we start processing events. We do this because the v3 upgrade
        # wants to delete a large number of old events from these databases and
        # we want that to occur at a predictable time.
        for n in node.Nodes([]):
            with eventlog.EventLog(n.object_type, n.uuid) as eventdb:
                pass

        while not self.exit.is_set():
            try:
                # Fetch queued events from etcd
                results = defaultdict(list)
                for k, v in etcd.get_all('event', None, limit=10000):
                    try:
                        _, _, _, objtype, objuuid, _ = k.split('/')
                    except ValueError as e:
                        util_general.ignore_exception(
                            'failed to parse event key "%s"' % k, e)
                        continue

                    results[(objtype, objuuid)].append((k, v))

                # Write them to local disk, but minimizing the number of times
                # we open each database
                for objtype, objuuid in results:
                    try:
                        with eventlog.EventLog(objtype, objuuid) as eventdb:
                            for k, v in results[(objtype, objuuid)]:
                                eventdb.write_event(
                                    v.get('event_type', EVENT_TYPE_HISTORIC),
                                    v['timestamp'], v['fqdn'], v['duration'],
                                    v['message'], extra=v.get('extra'))
                                etcd.WrappedEtcdClient().delete(k)
                    except Exception as e:
                        util_general.ignore_exception(
                            'failed to write event for %s %s' % (objtype, objuuid), e)

                if not results:
                    start_prune = time.time()

                    if time.time() - last_prune > 3600:
                        # Prune old events
                        event_path = os.path.join(config.STORAGE_PATH, 'events')
                        p = pathlib.Path(event_path)
                        for entpath in p.glob('**/*.lock'):
                            entpath = str(entpath)[len(event_path) + 1:-5]
                            objtype, _, objuuid = entpath.split('/')

                            with eventlog.EventLog(objtype, objuuid) as eventdb:
                                for event_type in ['audit', 'mutate', 'status',
                                                   'usage', 'resources', 'prune',
                                                   'historic']:
                                    max_age = getattr(
                                        config, 'MAX_%s_EVENT_AGE' % event_type.upper())
                                    if max_age == -1:
                                        continue

                                    eventdb.prune_old_events(
                                        time.time() - max_age, event_type)

                        self.log.info('Prune complete')
                        last_prune = time.time()

                    # Have a nap if pruning was quick
                    if time.time() - start_prune < 1:
                        time.sleep(1)

            except Exception as e:
                util_general.ignore_exception('eventlog daemon', e)

        LOG.info('Terminating')
