# The cluster daemon is for cluster level maintenance tasks which are not
# urgent. Hard deleting data for example. Its therefore pretty relaxed about
# obtaining the lock to do work et cetera. There is only one active cluster
# maintenance daemon per cluster.
from collections import defaultdict
import os
import time

from prometheus_client import start_http_server
import schedule
from shakenfist_utilities import logs  # noreorder

from shakenfist import artifact
from shakenfist import eventlog
from shakenfist import locks
from shakenfist import mariadb
from shakenfist import instance
from shakenfist import ipam
from shakenfist import namespace
from shakenfist import node_health
from shakenfist.network import network
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.network import interface
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist.daemons.cluster import scheduled_tasks
from shakenfist import exceptions
from shakenfist.exceptions import InvalidStateException
from shakenfist.node import Node
from shakenfist.node import Nodes
from shakenfist.node import nodes_by_free_disk_descending
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_all_node_queues
from shakenfist.schema.object_types import ObjectType
from shakenfist.upload import remove_abandoned_uploads
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super().__init__(name)
        self.lock = None
        self.is_elected = False
        # Nodes whose STATE_ERROR cascade (phase 3) has already run, so a
        # persistently-errored node is not re-processed every maintenance
        # pass. Entries are dropped when the node is next seen out of error.
        self._cascaded_error_nodes = set()
        start_http_server(config.CLUSTER_METRICS_PORT)

    def _await_election(self):
        # Attempt to acquire the cluster maintenance lock forever. The
        # lock is leased -- if we hold it, the refresher thread keeps
        # extending the lease; if we crash or get partitioned, the
        # lease expires and a candidate steals the row here. So only
        # one node at a time is performing cluster maintenance.
        while daemon.check_abort_path(self.abort_path):
            self.lock = locks.ClusterLock(
                'cluster', None, None, timeout=10, op='Cluster maintenance')
            result = self.lock.acquire()
            if result:
                self.is_elected = True
                return

            self.idle(5)
            self.check_daemon_state()

    def _cluster_wide_cleanup(self, last_loop_run):
        # Bail out before doing anything destructive if our lease has
        # already lapsed -- another node may be running this same loop
        # in parallel and we should not double up on the cleanup work.
        # The outer wait() will see lost_event and re-elect.
        if self.lock and self.lock.lost_event.is_set():
            LOG.warning(
                'Skipping cluster maintenance pass; lease lost')
            return
        LOG.info('Running cluster maintenance')

        # Pet before the preamble below (stale-transfer cleanup + history
        # prune) which runs before the first per-item loop's pet.
        self.pet_watchdog()

        # NOTE: The per-node blob cache is now maintained by each node's
        # cleaner daemon calling observe() on local blobs. The cleaner also
        # handles hard-deleting blobs with no locations. This is more accurate
        # since each node knows definitively what files exist on its local disk.

        # Cleanup stale blob transfers (transfers that haven't been updated in
        # more than 10 minutes are likely abandoned)
        deleted = mariadb.delete_stale_transfers(max_age=600)
        if deleted > 0:
            LOG.info(f'Deleted {deleted} stale blob transfers')

        # Prune the cluster_operation_targets history table. The table is
        # append-only -- bounded only by CLUSTER_OPERATION_TARGET_RETENTION.
        # Operations still in flight (queued/preflight/executing) are never
        # pruned regardless of age. Set the config to 0 to disable.
        if config.CLUSTER_OPERATION_TARGET_RETENTION > 0:
            deleted = mariadb.delete_stale_cluster_operation_targets(
                max_age=config.CLUSTER_OPERATION_TARGET_RETENTION)
            if deleted > 0:
                LOG.info(
                    f'Deleted {deleted} stale cluster_operation_targets rows')

        # Cleanup IPAMs whose network is absent
        for ipm in ipam.IPAMs([], prefilter='active'):
            self.pet_watchdog()
            if time.time() - ipm.state.update_time < 300:
                continue

            n = network.Network.from_db(
                ipm.network_uuid, suppress_failure_audit=True)
            if not n and ipm.state.value != dbo.STATE_DELETED:
                ipm.add_event(
                    EVENT_TYPE_AUDIT,
                    ('the cluster wide cleanup daemon is deleting this '
                     'IPAM as leaked because the associated network is '
                     'missing'))
                ipm.state = dbo.STATE_DELETED

        # Cleanup floating IP reservations which refer to deleted objects
        fn = network.floating_network()
        if fn:
            for addr in fn.ipam.in_use:
                self.pet_watchdog()
                reservation = fn.ipam.get_reservation(addr)
                if not reservation:
                    continue
                if reservation.reservation_type not in [ReservationType.GATEWAY,
                                                        ReservationType.FLOATING,
                                                        ReservationType.ROUTED]:
                    continue

                leaked = False
                if not reservation.user_type or not reservation.user_uuid:
                    leaked = True
                else:
                    obj = get_object_class(reservation.user_type).from_db(
                        str(reservation.user_uuid))
                    if not obj:
                        leaked = True
                    else:
                        s = obj.state
                        if (s.value == dbo.STATE_DELETED and
                                time.time() - s.update_time > 300):
                            leaked = True

                if leaked:
                    fn.ipam.release(addr)
                    eventlog.add_event_multi(
                        EVENT_TYPE_AUDIT,
                        [fn.ipam, (reservation.user_type, reservation.user_uuid)],
                        'cleaned up an address which refers to a deleted object')

        remove_abandoned_uploads()

        # Cleanup orphan artifacts, delete old versions, and record blobs used
        # by artifacts
        in_use_blobs = defaultdict(int)
        for a in artifact.Artifacts([]):
            self.pet_watchdog()

            # If the artifact's namespace is deleted then we should remove the
            # artifact
            ns = namespace.Namespace.from_db(
                a.namespace, suppress_failure_audit=True)
            if not ns:
                a.delete()
                continue

            # Prune artifacts which might have too many versions
            a.delete_old_versions()

            # Record usage for blobs used by artifacts
            for blob_index in a.get_all_indexes():
                blob_uuid = blob_index['blob_uuid']
                b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
                if b:
                    in_use_blobs[b.uuid] += 1

        # Inspect current state of blobs, the actual changes are done below outside
        # the read only cache. We define being low on disk as having less than two
        # reservations of headroom left (the helper already subtracts each node's
        # own reservation, so a headroom band of 2x the reservation is equivalent
        # to raw free disk below 3x the reservation at the default reservation --
        # preserving the historical "three times the minimum" trigger). This is a
        # rebalancing heuristic, so keying the band off this daemon's own default
        # reservation is acceptable. We omit a lower bound (minimum=None) so a
        # node that has already dropped below its own reservation -- negative
        # headroom, the most urgent to relieve -- is still caught. We rearrange
        # blobs before scheduling fails.
        overreplicated = {}
        underreplicated = []
        low_disk_nodes = nodes_by_free_disk_descending(
            maximum=(2 * config.NODE_DISK_RESERVATION_GB),
            intention='blobs')

        # Compute which instances use which blobs once for the whole pass.
        # Doing this per blob repeats the instance walk (and its per-disk
        # attribute and dependency chain reads) for every blob, which was
        # the bulk of the cluster daemon's idle database load (issue 3502).
        blob_usage = instance.instance_blob_usage()

        # We count fetches currently requested (or under way) as having completed
        # in order to stop over-replication for large blobs.
        #
        # This pass only ever iterates the list, so an unreadable one
        # degrades to "no reaping or rebalancing this time" -- unlike
        # the cleaner's complement-set use of the same call, where the
        # equivalent failure would delete blobs off disk (#3638). Skip
        # just this section so the rest of the cleanup still runs.
        #
        # "Just this section" is not quite true, which is why the flag
        # exists: the loop below is what records instance-backed blobs
        # in in_use_blobs, and record_usage() further down is the only
        # thing that refreshes last_used for a blob whose instance is
        # long running and never reopens it. The transcode reaper
        # selects on exactly that column, so a degraded pass which
        # still reaped would drop transcodes of blobs that are in use.
        # Artifact-backed blobs are unaffected -- they are recorded
        # before this read -- but the reaper cannot tell the two apart,
        # so it sits out the whole pass.
        blobs_readable = True
        try:
            active_blob_uuids = mariadb.get_active_blob_uuids()
        except exceptions.DatabaseUnavailable as e:
            LOG.with_fields({'error': str(e)}).warning(
                'Could not read the active blob list, skipping blob reaping '
                'and replication this pass')
            active_blob_uuids = []
            blobs_readable = False

        for blob_uuid in active_blob_uuids:
            self.pet_watchdog()

            b = Blob.from_db(blob_uuid)
            if not b:
                continue
            instances = blob_usage.get(str(b.uuid), [])
            if instances:
                in_use_blobs[b.uuid] += 1

            # If the blob's reference count is zero and it hasn't been used
            # recently, we can reap it. The grace period prevents race
            # conditions where a relationship is being created while the
            # cleanup runs. With the MariaDB-based object_references table,
            # ref_count is computed dynamically from actual relationships.
            if b.ref_count < 1:
                # Use fetched_at as fallback for last_used. This handles new
                # blobs that haven't been registered yet - they get a 300s
                # grace period from creation time instead of appearing unused
                # since epoch (which caused race conditions during downloads).
                last_used = b.last_used or b.fetched_at
                age = time.time() - last_used
                if age > 300:
                    b.add_event(
                        EVENT_TYPE_AUDIT,
                        'reference count is zero and unused for >300s, '
                        'cascading delete initiated',
                        extra={'last_used': last_used, 'age': age})
                    b.cascading_delete()
                continue

            # Each locations read is a database round trip, so read once
            # and reuse below.
            blob_locations = b.locations
            incomplete_nodes = [loc['node'] for loc in b.incomplete_healthy_locations]
            locations = blob_locations + incomplete_nodes
            delta = len(locations) - config.BLOB_REPLICATION_FACTOR
            if delta > 0:
                # So... The blob replication factor is a target not a limit.
                # Specifically, if there are more locations than the target
                # but we aren't low on disk, we don't clean them up. That's
                # because its hard for us to predict which machine will run
                # out of disk first, and copying a blob back to a machine if
                # its needed there is slow and annoying.

                # Work out where the blob is in active use.
                excess_locations = list(blob_locations)
                in_use_locations = []

                for instance_uuid in instances:
                    i = instance.Instance.from_db(instance_uuid)
                    node = i.placement.get('node')
                    if node in excess_locations:
                        excess_locations.remove(node)
                        in_use_locations.append(node)

                # Only remove excess copies from nodes which are running
                # low on disk. Do not end up with too few replicas.
                overreplicated[b.uuid] = []
                target = (config.BLOB_REPLICATION_FACTOR -
                          len(in_use_locations))
                for n in low_disk_nodes:
                    if n in excess_locations:
                        overreplicated[b.uuid].append(n)
                    if len(overreplicated[b.uuid]) == target:
                        break

            elif delta < 0:
                # The tuple is blob UUID, and how much to over replicate by.
                underreplicated.append((b.uuid, 0))

            else:
                # We have exactly the right number of copies, but what if
                # the blob is on a really full node?
                for n in low_disk_nodes:
                    if n in blob_locations:
                        # We have at least one space constrained node with
                        # this blob. Request an extra temporary copy of the
                        # blob elsewhere so we can hopefully clean up one of
                        # these next pass. The tuple is blob UUID, and how
                        # much to over replicate by.
                        underreplicated.append((b.uuid, 1))
                        break

        # Record blobs in use
        for blob_uuid in in_use_blobs:
            self.pet_watchdog()
            b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
            if b:
                b.record_usage()

        # Find expired blobs (database-level filtering)
        for blob_uuid in mariadb.get_expired_blob_uuids():
            self.pet_watchdog()
            b = Blob.from_db(blob_uuid)
            if b:
                b.add_event(EVENT_TYPE_AUDIT, 'blob has expired')
                b.state = dbo.STATE_DELETED

        # Prune over replicated blobs
        for blob_uuid in overreplicated:
            self.pet_watchdog()
            b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
            if b:
                for node in overreplicated[blob_uuid]:
                    LOG.with_fields({
                        'blob': b,
                        'node': node
                    }).info('Blob over replicated, removing from node with no users')
                    b.drop_node_location(node)

        # Replicate under replicated blobs, but only if we don't have heaps of
        # queued replications already
        for blob_uuid, excess in underreplicated:
            self.pet_watchdog()
            b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
            if b:
                LOG.with_fields({
                    'blob': b
                }).info('Blob under replicated, attempting to correct')
                b.request_replication(allow_excess=excess)

        # Find transcodes of not recently used blobs and reap them
        # (database-level filtering). Only when this pass was able to
        # refresh last_used -- see the comment on blobs_readable above.
        if blobs_readable:
            for blob_uuid in mariadb.get_stale_transcoded_blob_uuids(
                    config.BLOB_TRANSCODE_MAXIMUM_IDLE_TIME):
                self.pet_watchdog()
                b = Blob.from_db(blob_uuid)
                if b:
                    b.remove_transcodes()
        else:
            LOG.info('Skipping stale transcode reaping, last_used was not '
                     'refreshed this pass')

        # Node management
        for n in Nodes([]):
            self.pet_watchdog()

            age = round(time.time() - n.last_seen, 2)

            LOG.with_fields(
                {
                    'node': n.fqdn,
                    'status_age': age,
                    'last_seen': n.last_seen,
                    'state': n.state.value
                }).debug('Considering node status')

            # Drop the cascade guard for any node not currently errored, so a
            # node that recovers and later re-fails is cascaded afresh.
            if n.state.value != Node.STATE_ERROR:
                self._cascaded_error_nodes.discard(str(n.uuid))

            # Find nodes which are now missing or have returned from being missing
            if n.state.value in [Node.STATE_INITIAL, Node.STATE_CREATING,
                                 Node.STATE_CREATED, Node.STATE_DEGRADED]:
                if age > config.NODE_CHECKIN_MAXIMUM:
                    n.state = Node.STATE_MISSING
                    n.add_event(EVENT_TYPE_AUDIT, 'node has gone missing',
                                extra={
                                    'checkin_at': n.last_seen,
                                    'checkin_age': age
                                })
            elif n.state.value == Node.STATE_MISSING:
                if age < config.NODE_CHECKIN_MAXIMUM:
                    n.state = Node.STATE_CREATED
                    n.add_event(EVENT_TYPE_AUDIT,
                                'node returned from being missing')
            elif n.state.value == Node.STATE_DELETED:
                # Find instances on deleted nodes
                for i in instance.healthy_instances_on_node(n):
                    n.add_event(
                        EVENT_TYPE_AUDIT, 'deleting instance as hosting node as been deleted',
                        extra={'instance_uuid': i.uuid})
                    i.add_event(
                        EVENT_TYPE_AUDIT, 'deleting instance as hosting node as been deleted')
                    i.delete(global_only=True)

                    # Cleanup the instance's interfaces
                    for ni in interface.interfaces_for_instance(i):
                        ni.delete()

                # Cleanup any blob locations (use Node.blobs property)
                self._drop_blob_locations(
                    n, 'deleting blob location as hosting node has been deleted')

                # Clean up any lingering queue tasks. Drain in batches of
                # 100 -- this only runs once per deleted node, so a
                # larger batch amortises the dequeue cost without
                # growing the orphan window noticeably (this code path
                # is the only consumer of the dead node's queues, so a
                # crash mid-batch just leaves the rows for the next
                # cluster pass).
                node_queues = list(get_all_node_queues(n.fqdn))
                while items := mariadb.dequeue_work_items(
                        node_queues, limit=100):
                    self.pet_watchdog()
                    for queue_name, jobname, workitem in items:
                        n.add_event(
                            EVENT_TYPE_AUDIT,
                            'deleting work item for deleted node',
                            extra={
                                'jobname': jobname,
                                'queue': queue_name
                            })

                        # Cluster operations might have dependencies
                        if '-clusteroperation-' in queue_name:
                            op_type = workitem.get('operation_type')
                            op_uuid = workitem.get('operation_uuid')
                            op = get_object_class(op_type).from_db(op_uuid)

                            try:
                                op.state = BaseClusterOperation.STATE_ABORT
                                eventlog.add_event_multi(
                                    EVENT_TYPE_AUDIT,
                                    [n, op],
                                    'aborted operation for deleted node',
                                    extra={
                                        'jobname': jobname,
                                        'queue': queue_name
                                    })

                            except InvalidStateException:
                                eventlog.add_event_multi(
                                    EVENT_TYPE_AUDIT,
                                    [n, op],
                                    'failed to abort operation'
                                )

                        mariadb.resolve_work_item(queue_name, jobname)

            elif n.state.value == Node.STATE_ERROR:
                self._cascade_errored_node(n)

        # And we're done
        LOG.info('Cluster maintenance loop complete')

    def _cascade_errored_node(self, n):
        # React to a node that phase 2 (sf-resources) marked STATE_ERROR
        # because its storage is unhealthy. Mirrors the deleted-node cleanup
        # above, but *errors* the hosted instances rather than deleting them
        # (an errored instance is terminal-but-snapshottable, left for the
        # operator), and both cascades are gated on which object type the
        # failure actually affected (an uploads-only failure marks the node
        # error but must not kill instances or drop blob replicas).
        #
        # Runs here, on the surviving cluster maintenance node, not on the
        # (possibly dying) affected node, which does only the one fast
        # self-mark in phase 2.
        if str(n.uuid) in self._cascaded_error_nodes:
            return

        affected = node_health.errored_node_affected_types(n)
        if affected is None:
            # Blast radius unknown (no diagnosis event yet); do nothing and
            # retry next pass without recording the node as cascaded.
            return

        if ObjectType.INSTANCE in affected:
            for i in instance.healthy_instances_on_node(n):
                self.pet_watchdog()
                reason = (
                    'erroring instance as hosting node storage is unhealthy')
                n.add_event(
                    EVENT_TYPE_AUDIT, reason, extra={'instance_uuid': i.uuid})
                i.add_event(EVENT_TYPE_AUDIT, reason)
                # The error setter requires the object already be in an error
                # state, so move state before setting error.
                i.state = i.state.value + '-error'
                i.error = reason

        if ObjectType.BLOB in affected:
            self._drop_blob_locations(
                n, 'dropping blob location as hosting node storage is '
                'unhealthy')

        self._cascaded_error_nodes.add(str(n.uuid))

    def _drop_blob_locations(self, n, reason):
        # Drop every blob replica hosted on node n and ask the replicator to
        # re-establish the copies elsewhere. Shared by the deleted-node cleanup
        # and the errored-node cascade -- the only difference between the two
        # callers is the audit reason string. A blob deleted between reading
        # n.blobs and processing it is skipped.
        for blob_uuid in n.blobs:
            self.pet_watchdog()
            b = Blob.from_db(blob_uuid)
            if not b:
                continue
            eventlog.add_event_multi(EVENT_TYPE_AUDIT, [n, b], reason)
            b.remove_location(n.fqdn)
            b.request_replication()

    def _run_due_scheduled_jobs(self):
        """Run every due maintenance job, petting between each one.

        This is schedule.run_pending() with a watchdog pet before each
        job. The batch matters because job timers run continuously while
        a node is idle but only fire while it is elected, so a node that
        has been up for hours and is then elected finds every job
        overdue at once -- including the three heaviest (prune_events,
        reconcile_orphaned_objects, per_blob_checks). Petting only
        around the batch, with WatchdogSec at 60s, makes that first
        elected pass the most likely place to be killed for
        unresponsiveness. A pet between jobs bounds the exposure to one
        job rather than nine.

        Sorting, the should_run check and the CancelJob handling mirror
        run_pending() and _run_job() themselves, so job ordering and
        semantics are unchanged. A job that raises propagates to the
        caller's ignore_exception, skipping the rest of this batch; the
        raising job never reached _schedule_next_run() so it stays due
        and retries on the next 60 second cycle, exactly as it would
        under run_pending().
        """
        for job in sorted(schedule.jobs):
            if not job.should_run:
                continue
            self.pet_watchdog()
            ret = job.run()
            if isinstance(ret, schedule.CancelJob) or ret is schedule.CancelJob:
                schedule.cancel_job(job)
        self.pet_watchdog()

    def _run_inner(self):
        last_defer_message = 0
        last_loop_run = 0

        # Set up the maintenance schedule once, for the life of the
        # daemon, rather than on each election. schedule.every() appends
        # to a module-global job list and computes each job's next run
        # from the moment it is registered, so registering inside the
        # election loop had two problems: a node elected twice ran every
        # task twice per cadence (three times after a third election, and
        # so on), and re-registering restarted every period from zero, so
        # on a cluster where the maintenance lock changes hands often the
        # long-period tasks -- prune_events daily, reconcile_orphaned
        # _objects hourly -- could go indefinitely without ever running.
        # Registering once fixes both: run_pending() below is only called
        # while elected, so an idle node still does no maintenance, but
        # the timers are continuous and a newly elected node promptly
        # runs whatever fell due while it was idle.
        schedule.every(1).minutes.do(
            scheduled_tasks.log_cluster_queue_lengths)
        schedule.every(1).minutes.do(
            scheduled_tasks.reap_stuck_cluster_operation_jobs)
        schedule.every(5).minutes.do(
            scheduled_tasks.per_blob_checks)
        schedule.every(5).minutes.do(
            scheduled_tasks.per_instance_checks_and_usage)
        schedule.every(5).minutes.do(
            scheduled_tasks.reconcile_scheduler_capacity)
        schedule.every(15).minutes.do(
            scheduled_tasks.per_deleted_object_checks)
        schedule.every(15).minutes.do(
            scheduled_tasks.reap_expired_namespace_keys)
        schedule.every(15).minutes.do(
            scheduled_tasks.reap_federation_records)
        schedule.every(60).minutes.do(
            scheduled_tasks.reconcile_orphaned_objects)
        schedule.every(1).days.do(scheduled_tasks.prune_events)

        while daemon.check_abort_path(self.abort_path):
            util_concurrency.set_thread_name('idle')
            LOG.debug('This cluster thread is now idle and awaiting election')
            self._await_election()

            util_concurrency.set_thread_name('active')
            LOG.debug('This cluster thread is now active')

            if not self.cluster_stable():
                if time.time() - last_defer_message > 10:
                    LOG.info('Cluster not yet stable, deferring maintenance')
                    last_defer_message = time.time()
                self.idle(60)
                continue

            # And then do regular cluster maintenance things
            while self.is_elected and not os.path.exists(self.abort_path):
                # This elected loop sleeps via lock.lost_event.wait() rather
                # than idle(), so it must pet the systemd watchdog itself
                # (rate-limited internally) to stay alive between maintenance
                # passes and during the cleanup below.
                self.pet_watchdog()

                now = time.time()
                if now - last_loop_run >= 60:
                    try:
                        with util_general.RecordedOperation(
                                'scheduled cluster operations',
                                None, threshold=10):
                            self._run_due_scheduled_jobs()
                    except Exception as e:
                        util_exceptions.ignore_exception('cluster', e)

                    # _run_due_scheduled_jobs() above and the cleanup below are unbounded
                    # maintenance phases; pet between them so a slow scheduled
                    # task does not eat the whole watchdog budget before the
                    # cleanup's own per-loop pets start.
                    self.pet_watchdog()

                    try:
                        with util_general.RecordedOperation(
                                'cluster wide cleanup', None, threshold=10):
                            self._cluster_wide_cleanup(last_loop_run)
                    except Exception as e:
                        util_exceptions.ignore_exception('cluster', e)

                    last_loop_run = now

                # Sleep up to 5s. Wakes early if the background refresher
                # reports our lease was stolen, OR when the timeout fires
                # so the outer ``not os.path.exists(self.abort_path)``
                # check runs again. The maintenance work above is gated
                # at 60 s; this short poll is just so SIGTERM during a
                # quiet period gets observed inside the systemd
                # ``TimeoutStopSec=30s`` budget (the old wait(60) parked
                # past it and got SIGKILLed).
                if self.lock.lost_event.wait(5):
                    LOG.warning(
                        'Cluster maintenance lock lost; re-entering election')
                    self.is_elected = False

            # No longer the leader (lock lost, or shutting down). The
            # capacity gauges describe cluster-wide singleton state, so
            # stop publishing them rather than leaving this node
            # contradicting whichever node takes over. The sweep failure
            # streaks go the same way: only the leader sweeps, so a
            # demoted node holding a non-zero streak would keep an alert
            # firing against work it is no longer doing.
            scheduled_tasks.clear_scheduler_capacity_metrics()
            scheduled_tasks.clear_sweep_failure_metrics()

        # Stop being the cluster maintenance node if we were. Release
        # may raise LockNotHeld if our lease has lapsed -- swallow it,
        # the row is gone either way.
        if self.lock and self.lock.is_acquired():
            try:
                self.lock.release()
            except exceptions.LockNotHeld:
                ...


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('cluster')
    m = Monitor('cluster')
    m.run()

    daemon.force_clean_exit()
