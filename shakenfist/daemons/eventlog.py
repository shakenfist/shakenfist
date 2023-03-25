from collections import defaultdict
from shakenfist_utilities import logs
import os
import pathlib
import time

from prometheus_client import Counter
from prometheus_client import start_http_server

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist.eventlog import EVENT_TYPES, EVENT_TYPE_HISTORIC
from shakenfist import node
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Monitor(daemon.WorkerPoolDaemon):
    def __init__(self, id):
        super(Monitor, self).__init__(id)
        start_http_server(config.EVENTLOG_METRICS_PORT)

    def run(self):
        LOG.info('Starting')
        prune_targets = []

        counters = {
            'pruned_events': Counter('pruned_events', 'Number of pruned events')
        }
        for event_type in EVENT_TYPES:
            counters[event_type] = Counter(
                '%s_events' % event_type,
                'Number of %s events seen' % event_type)

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
                                event_type = v.get('event_type', EVENT_TYPE_HISTORIC)
                                eventdb.write_event(
                                    event_type,
                                    v['timestamp'], v['fqdn'], v['duration'],
                                    v['message'], extra=v.get('extra'))
                                counters[event_type].inc()
                                etcd.WrappedEtcdClient().delete(k)
                    except Exception as e:
                        util_general.ignore_exception(
                            'failed to write event for %s %s' % (objtype, objuuid), e)

                if not results:
                    # Prune old events
                    if not prune_targets:
                        event_path = os.path.join(config.STORAGE_PATH, 'events')
                        p = pathlib.Path(event_path)
                        for entpath in p.glob('**/*.lock'):
                            entpath = str(entpath)[len(event_path) + 1:-5]
                            objtype, _, objuuid = entpath.split('/')
                            prune_targets.append([objtype, objuuid])

                    else:
                        start_prune = time.time()
                        while time.time() - start_prune < 10 and prune_targets:
                            objtype, objuuid = prune_targets.pop()

                            with eventlog.EventLog(objtype, objuuid) as eventdb:
                                count = 0
                                for event_type in EVENT_TYPES:
                                    max_age = getattr(
                                        config, 'MAX_%s_EVENT_AGE' % event_type.upper())
                                    if max_age == -1:
                                        continue

                                    c = eventdb.prune_old_events(
                                        time.time() - max_age, event_type)
                                    counters['pruned_events'].inc(c)
                                    count += c

                                if count > 0:
                                    self.log.with_fields({objtype: objuuid}).info(
                                        'Pruned %d events' % count)

                        # Have a nap if pruning was quick
                        if time.time() - start_prune < 1:
                            time.sleep(1)

            except Exception as e:
                util_general.ignore_exception('eventlog daemon', e)

        LOG.info('Terminating')
