import json
import os
import pathlib
import time
from collections import defaultdict
from concurrent import futures

import grpc
from prometheus_client import Counter
from prometheus_client import start_http_server
from shakenfist_utilities import logs

from shakenfist import etcd
from shakenfist import event_pb2
from shakenfist import event_pb2_grpc
from shakenfist import eventlog
from shakenfist import node
from shakenfist.config import config
from shakenfist.constants import API_REQUESTS
from shakenfist.constants import EVENT_TYPE_HISTORIC
from shakenfist.constants import EVENT_TYPES
from shakenfist.daemons import daemon
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class EventService(event_pb2_grpc.EventServiceServicer):
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor

    def RecordEvent(self, request, context):
        try:
            with eventlog.EventLog(request.object_type, request.object_uuid) as eventdb:
                extra = json.loads(request.extra)

                # Handle the replacement of the timestamp field. Weirdly, HasField()
                # raises an exception if the field is not present in the message,
                # instead of a boolean.
                timestamp = request.obsolete_timestamp
                try:
                    timestamp = request.timestamp
                except ValueError:
                    ...

                if not timestamp or timestamp == 0:
                    LOG.with_fields({
                        'event_type': request.event_type,
                        'timestamp': timestamp,
                        'protobuf_timestamp': request.timestamp,
                        'protobuf_obsolete_timestamp': request.obsolete_timestamp,
                        'node': request.fqdn,
                        'message': request.message,
                        'extra': request.extra
                    }).error('Event has invalid timestamp')

                if not eventdb.write_event(
                        request.event_type, timestamp, request.fqdn,
                        request.duration, request.message, extra):
                    # Write the event failed, queue it to etcd instead
                    LOG.info('Failed to write event via gRPC path, adding to dead '
                             'letter queue')
                    etcd.put('event/%s' % request.object_type, request.object_uuid,
                             timestamp,
                             {
                                 'timestamp': timestamp,
                                 'event_type': request.event_type,
                                 'object_type': request.object_type,
                                 'object_uuid': request.object_uuid,
                                 'fqdn': request.fqdn,
                                 'message': request.message,
                                 'extra': request.extra
                             })
                self.monitor.counters[request.event_type].inc()

            # Piggy back request tracing onto object events
            if 'request-id' in extra:
                # Add object information from the original event to extra
                extra['object_type'] = request.object_type
                extra['object_uuid'] = request.object_uuid

                with eventlog.EventLog(API_REQUESTS, extra['request-id']) as eventdb:
                    eventdb.write_event(
                        request.event_type, timestamp, request.fqdn,
                        request.duration, request.message, extra)
        except Exception as e:
            util_general.ignore_exception(
                'failed to write event for %s %s'
                % (request.object_type, request.object_uuid), e)
            return event_pb2.EventReply(ack=False)

        return event_pb2.EventReply(ack=True)


class Monitor(daemon.WorkerPoolDaemon):
    def __init__(self, id):
        super().__init__(id)
        self.counters = {
            'pruned_events': Counter('pruned_events', 'Number of pruned events'),
            'pruned_sweep': Counter('pruned_sweep',
                                    'Number of databases checked for pruning')
        }
        start_http_server(config.EVENTLOG_METRICS_PORT)

    def run(self):
        LOG.info('Starting')
        prune_targets = []
        prune_sweep_started = 0

        for event_type in EVENT_TYPES:
            self.counters[event_type] = Counter(
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

        server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        event_pb2_grpc.add_EventServiceServicer_to_server(
            EventService(self), server)
        server.add_insecure_port(f'{config.EVENTLOG_NODE_IP}:{config.EVENTLOG_API_PORT}')
        server.start()

        while not self.exit.is_set():
            try:
                did_work = False

                # Fetch queued events from etcd. This is how all events worked
                # in versions older than v0.7, but is now here to catch in flight
                # upgrades and act as a dead letter queue for when the event node
                # isn't answering.
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
                                    v['timestamp'], v['fqdn'],
                                    v.get('duration'),
                                    v['message'], extra=v.get('extra'))
                                self.counters[event_type].inc()
                                etcd.get_etcd_client().delete(k)
                    except Exception as e:
                        util_general.ignore_exception(
                            f'Failed to write event for {objtype} {objuuid}, will retry', e)

                if results:
                    did_work = True

                else:
                    # Prune old events
                    if not prune_targets:
                        # Only sweep all databases once a day
                        if time.time() - prune_sweep_started > 24 * 3600:
                            event_path = os.path.join(config.STORAGE_PATH, 'events')
                            p = pathlib.Path(event_path)
                            for entpath in p.glob('**/*.lock'):
                                entpath = str(entpath)[len(event_path) + 1:-5]
                                objtype, _, objuuid = entpath.split('/')
                                prune_targets.append([objtype, objuuid])
                            prune_sweep_started = time.time()

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

                                    # API requests are super verbose, so we can
                                    # also prune them especially vigorously.
                                    if objtype == 'api-request':
                                        max_age = max(
                                            max_age, config.MAX_API_REQUEST_EVENT_AGE)

                                    c = eventdb.prune_old_events(
                                        time.time() - max_age, event_type)
                                    self.counters['pruned_events'].inc(c)
                                    count += c

                                if count > 0:
                                    self.log.with_fields({objtype: objuuid}).info(
                                        'Pruned %d events' % count)

                            self.counters['pruned_sweep'].inc()
                            did_work = True

                if not did_work:
                    self.exit.wait(10)

            except Exception as e:
                util_general.ignore_exception('eventlog daemon', e)

        server.stop(1).wait()
        LOG.info('Terminated')
