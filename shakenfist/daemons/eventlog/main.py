
from collections import defaultdict
from concurrent import futures
import copy
import json
import os
import pathlib
import time

import grpc
from prometheus_client import Counter
from prometheus_client import start_http_server
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities.random import random_id  # noreorder

from shakenfist import eventlog
from shakenfist import mariadb
from shakenfist import node
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_HISTORIC
from shakenfist.constants import EVENT_TYPES
from shakenfist.daemons import daemon
from shakenfist.schema.object_types import ObjectType
from shakenfist.daemons.daemon import send_systemd_ready
from shakenfist.daemons.daemon import send_systemd_status
from shakenfist.exceptions import InvalidStateException
from shakenfist.node import Node
from shakenfist.protos import event_pb2
from shakenfist.protos import event_pb2_grpc
from shakenfist.util import exceptions as util_exceptions


LOG, _ = logs.setup(__name__)


class EventService(event_pb2_grpc.EventServiceServicer):
    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor

    def _record_with_dlq(
            self, event_type, object_type, object_uuid, message, duration,
            extra, timestamp, fqdn, correlation_id=None):
        with eventlog.EventLog(object_type, object_uuid) as eventdb:
            if not eventdb.write_event(
                    event_type, timestamp, fqdn, duration, message, extra=extra,
                    correlation_id=correlation_id):
                # Writing the event failed, queue it to the DLQ
                LOG.info('Failed to write event to chunk, adding to dead '
                         'letter queue')
                mariadb.enqueue_event_dlq(
                    object_type=object_type,
                    object_uuid=object_uuid,
                    event_timestamp=timestamp,
                    event_json={
                        'timestamp': timestamp,
                        'event_type': event_type,
                        'object_type': object_type,
                        'object_uuid': object_uuid,
                        'fqdn': fqdn,
                        'message': message,
                        'extra': extra,
                        'correlation_id': correlation_id,
                    },
                )

    def _add_other_objects(self, not_this_type, objects, extra):
        tweaked_extra = copy.deepcopy(extra)
        for eo in objects:
            if eo.object_type != not_this_type:
                tweaked_extra[eo.object_type] = eo.object_uuid
        return tweaked_extra

    # An older, less preferred implementation but still here as a fallback
    # during upgrade.
    def RecordEvent(self, request, context):
        try:
            extra = json.loads(request.extra)

            # Handle the replacement of the timestamp field. Weirdly, HasField()
            # raises an exception if the field is not present in the message,
            # instead of a boolean.
            timestamp = request.obsolete_timestamp
            try:
                timestamp = request.timestamp
            except ValueError:
                ...

            # Generate a correlation id, but only if this event also has a
            # request-id.
            correlation_id = None
            if 'request-id' in extra:
                correlation_id = random_id()

            if not timestamp or timestamp == 0:
                LOG.with_fields({
                    'event_type': request.event_type,
                    'timestamp': timestamp,
                    'protobuf_timestamp': request.timestamp,
                    'protobuf_obsolete_timestamp': request.obsolete_timestamp,
                    'node': request.fqdn,
                    'message': request.message,
                    'extra': request.extra,
                    'correlation_id': correlation_id
                }).error('Event has invalid timestamp')

            self._record_with_dlq(
                request.event_type, request.object_type, request.object_uuid,
                request.message, request.duration, extra, timestamp,
                request.fqdn, correlation_id=correlation_id)
            self.monitor.counters[request.event_type].inc()

            # Piggy back request tracing onto object events
            if 'request-id' in extra:
                # Add object information from the original event to extra
                extra['object_type'] = request.object_type
                extra['object_uuid'] = request.object_uuid
                self._record_with_dlq(
                    request.event_type, ObjectType.API_REQUESTS,
                    extra['request-id'], request.message, request.duration,
                    extra, timestamp, request.fqdn, correlation_id=correlation_id)

        except Exception as e:
            util_exceptions.ignore_exception(
                'failed to write event for %s %s'
                % (request.object_type, request.object_uuid), e)
            return event_pb2.EventReply(ack=False)

        return event_pb2.EventReply(ack=True)

    def RecordMultiEvent(self, request, context):
        try:
            extra = json.loads(request.extra)
            timestamp = request.timestamp

            # Generate a correlation id, but only if this event also has a
            # request-id or is for more than one object.
            correlation_id = None
            if 'request-id' in extra or len(request.objects) > 1:
                correlation_id = random_id()

            logged_types = []
            for eo in request.objects:
                logged_types.append(eo.object_type)
                tweaked_extra = self._add_other_objects(
                    eo.object_type, request.objects, extra)
                self._record_with_dlq(
                    request.event_type, eo.object_type, eo.object_uuid,
                    request.message, request.duration, tweaked_extra, timestamp,
                    request.fqdn, correlation_id=correlation_id)
            self.monitor.counters[request.event_type].inc()

            # Piggy back request tracing onto object events
            if 'request-id' in extra and ObjectType.API_REQUESTS not in logged_types:
                # Add object information from the original event to extra
                tweaked_extra = self._add_other_objects(
                    ObjectType.API_REQUESTS, request.objects, extra)
                self._record_with_dlq(
                    request.event_type, ObjectType.API_REQUESTS,
                    extra['request-id'], request.message, request.duration,
                    tweaked_extra, timestamp, request.fqdn,
                    correlation_id=correlation_id)

        except Exception as e:
            util_exceptions.ignore_exception(
                'failed to write event for %s' % request.objects, e)
            return event_pb2.EventReply(ack=False)

        return event_pb2.EventReply(ack=True)

    def RecordMultiEventBatch(self, request, context):
        """Persist a batch of EventMultiRequest items in one RPC.

        Called only by the local eventlog spool drainer (see
        ``shakenfist.eventlog.spool``). The drainer is the
        single producer for any given client process and sizes
        batches at ~100 events so partial-failure replay is
        cheap.

        Semantics: ack=true means every event in the batch
        was persisted (each via the same code path as a
        single ``RecordMultiEvent`` call would take, including
        the DLQ fallback). ack=false means at least one
        sub-event raised; the drainer treats the whole batch
        as not-delivered and replays. The sub-event-level DLQ
        fallback inside ``_record_with_dlq`` still applies, so
        a transient sub-write failure does not bubble up if
        the DLQ accepts the write.

        This handler runs synchronously on the gRPC server's
        worker thread; the per-batch wall time is roughly
        ``batch_size * per-event sqlite write`` plus one RPC
        round trip rather than ``batch_size * (RPC + sqlite
        write)``, which is the win for the caller.
        """
        try:
            for inner in request.events:
                # Reuse the per-event handler so we get the
                # same correlation_id logic, the same
                # request-id piggyback, and the same DLQ
                # fallback semantics. ``context`` is reused
                # but neither path consumes anything from it
                # today.
                reply = self.RecordMultiEvent(inner, context)
                if not reply.ack:
                    # One sub-event raised in a way the DLQ
                    # couldn't absorb. Treat the whole batch
                    # as not-delivered so the drainer
                    # replays.
                    return event_pb2.EventReply(ack=False)
        except Exception as e:
            util_exceptions.ignore_exception(
                'failed to write event batch of size %d'
                % len(request.events), e)
            return event_pb2.EventReply(ack=False)

        return event_pb2.EventReply(ack=True)


class Monitor(daemon.WorkerPoolDaemon):
    """Background monitor for the eventlog daemon.

    The eventlog daemon is special because it provides event logging to other
    daemons. This means we must force events to the dead letter queue during
    our own startup and shutdown, otherwise we'd have a self-deadlock trying
    to send events to ourselves via gRPC.
    """

    def __init__(self, id):
        super().__init__(id)
        self.counters = {
            'pruned_events': Counter('pruned_events', 'Number of pruned events'),
            'pruned_sweep': Counter('pruned_sweep',
                                    'Number of databases checked for pruning')
        }

        for event_type in EVENT_TYPES:
            self.counters[event_type] = Counter(
                '%s_events' % event_type,
                'Number of %s events seen' % event_type)

        start_http_server(config.EVENTLOG_METRICS_PORT)

    def record_start(self):
        # Force events to the DLQ because we can't use ourselves for
        # event logging during startup (WE ARE the eventlog service).
        eventlog.set_force_event_dlq(True)
        try:
            n = Node.from_db(config.NODE_NAME)
            n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_RUNNING)
            n.add_event(EVENT_TYPE_AUDIT, f'{self.daemon_name} daemon starting')
        finally:
            eventlog.set_force_event_dlq(False)
        send_systemd_ready()

    def record_exit(self):
        # Force events to the DLQ because we can't use ourselves for
        # event logging during shutdown.
        eventlog.set_force_event_dlq(True)
        try:
            n = Node.from_db(config.NODE_NAME)
            try:
                n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_STOPPED)
            except InvalidStateException as e:
                if not str(e).startswith(
                        'Invalid state change from stopping to degraded'):
                    raise e
            n.add_event(EVENT_TYPE_AUDIT, f'{self.daemon_name} daemon stopped')
        finally:
            eventlog.set_force_event_dlq(False)
        send_systemd_status('Terminated')

    def _run_inner(self):
        prune_targets = []
        prune_sweep_started = 0

        eventlog.upgrade_data_store()

        # This dance forces all node databases to be opened and possibly upgraded
        # before we start processing events. We do this because the v3 upgrade
        # wants to delete a large number of old events from these databases and
        # we want that to occur at a predictable time.
        for n in node.Nodes([]):
            with eventlog.EventLog(n.object_type, str(n.uuid)) as eventdb:
                pass

        while daemon.check_abort_path(self.abort_path):
            while not daemon.health_check_nodelock():
                LOG.info('Waiting for nodelock daemon to be healthy')
                time.sleep(1)
                continue

            try:
                did_work = False

                # Drain events from the dead-letter queue in MariaDB.
                # Events land here when the eventlog gRPC service is
                # unavailable or during daemon startup.
                results = defaultdict(list)
                dlq_rows = mariadb.drain_event_dlq(limit=10000)
                for row in dlq_rows:
                    key = (row['object_type'], row['object_uuid'])
                    results[key].append(row)

                # Write them to local disk, minimizing the number of
                # times we open each database
                ids_to_delete = []
                for (objtype, objuuid), rows in results.items():
                    try:
                        with eventlog.EventLog(objtype, objuuid) as eventdb:
                            for row in rows:
                                v = row['event_json']
                                event_type = v.get(
                                    'event_type', EVENT_TYPE_HISTORIC)
                                eventdb.write_event(
                                    event_type, v['timestamp'], v['fqdn'],
                                    v.get('duration'), v['message'],
                                    extra=v.get('extra'),
                                    correlation_id=v.get('correlation_id'))
                                self.counters[event_type].inc()
                                ids_to_delete.append(row['id'])
                    except Exception as e:
                        util_exceptions.ignore_exception(
                            f'Failed to write event for {objtype} '
                            f'{objuuid}, will retry', e)

                if ids_to_delete:
                    mariadb.delete_event_dlq(ids_to_delete)

                if results:
                    did_work = True

                else:
                    # Prune old events
                    if not prune_targets:
                        # Only sweep all databases once a day
                        if time.time() - prune_sweep_started > 24 * 3600:
                            event_path = os.path.join(
                                config.STORAGE_PATH, 'events')
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
                    self.idle(10)

            except Exception as e:
                util_exceptions.ignore_exception('eventlog daemon', e)

            self.check_daemon_state()


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('eventlog')
    m = Monitor('eventlog')

    # Start the grpc server very early, but not before nodelock. We cannot just
    # lookup the state of the daemon from etcd, because eventlog starts before
    # the queue daemon which actually writes that value
    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    # Allow clients to send keepalive pings as often as every 5 seconds.
    # Without this the default minimum (5 minutes) triggers GOAWAY with
    # ENHANCE_YOUR_CALM when the client pings more frequently.
    #
    # keepalive_permit_without_calls=1 is also required: when there are no
    # active RPCs the server otherwise treats the transport as idle and
    # forces a 2-hour minimum ping interval regardless of
    # min_recv_ping_interval_without_data_ms.
    #
    # max_ping_strikes=0 disables the strike counter entirely. Even with the
    # two options above, multi-node CI clusters still occasionally tripped
    # the default 2-strike limit (e.g., during reconnect bursts), producing
    # GOAWAY too_many_pings. Our clients are trusted internal daemons, so
    # disabling the kill switch is the recommended pattern.
    # 32 server threads, not 10. With every daemon process now
    # running its own eventlog drainer (the spool fan-out from
    # 2bb62455 + the gunicorn-worker fan-out from c27ae482),
    # the steady-state set of concurrent ``RecordMultiEventBatch``
    # callers is ~30 across a 6-node cluster (1 per daemon per
    # node + 5 gunicorn workers per node). At max_workers=10
    # the surplus batches queue past the drainer's 10 s RPC
    # timeout, every drainer backs off exponentially to
    # BACKOFF_MAX=30 s, events sit in their spools that long,
    # and read-after-write tests time out. 32 gives enough
    # headroom that batches do not queue under steady-state
    # cluster load.
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=32),
        options=[
            ('grpc.http2.min_recv_ping_interval_without_data_ms', 5000),
            ('grpc.keepalive_permit_without_calls', 1),
            ('grpc.http2.max_ping_strikes', 0),
        ]
    )
    event_pb2_grpc.add_EventServiceServicer_to_server(
        EventService(m), server)
    server.add_insecure_port(
        f'{config.EVENTLOG_NODE_IP}:{config.EVENTLOG_API_PORT}')

    server.start()
    m.run()
    server.stop(1).wait()

    daemon.force_clean_exit()
