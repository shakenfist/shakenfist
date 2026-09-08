# The cluster daemon is for cluster level maintenance tasks which are not
# urgent. Hard deleting data for example. Its therefore pretty relaxed about
# obtaining the lock to do work et cetera. There is only one active cluster
# maintenance daemon per cluster.
from collections import defaultdict
import datetime
import os
import random
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

# Prefix for the cluster_config keys recording when each long-period
# maintenance task last ran, cluster-wide. cluster_config already holds
# non-configuration operational state (the Kerbside signing key).
SCHEDULED_TASK_LAST_RUN_PREFIX = 'SCHEDULED_TASK_LAST_RUN_'

# How long the elected maintenance loop sleeps between passes. Named
# because it is not only a sleep: this loop polls its own daemon state row
# once per iteration rather than once per DAEMON_STATE_POLL_INTERVAL from
# idle(), so the value sets the elected node's GetNodeDaemonState rate and
# is what the cluster_base_qps term of that pair in
# shakenfist/data/database_load_budget.yaml is arithmetic about. Change it
# and the budget entry, the functional suite's positive control, and the
# generated Prometheus rules are all wrong together -- which is why
# test_elected_cluster_daemon_poll_matches_the_daemon_code reads this
# constant rather than restating the number.
ELECTED_LOOP_POLL_SECONDS = 5

# Marks the "no capacity rows at all" half of a forced-reconcile
# condition, so that state can never compare equal to the partly
# populated one. See _force_capacity_reconcile().
CAPACITY_TABLE_EMPTY = 'capacity-table-empty'

# The candidate election poll: how long an unelected cluster daemon waits
# between attempts to acquire the cluster maintenance lock. The wait is
# drawn uniformly from this range rather than being a fixed five seconds
# because a fixed cadence made the election unfair: candidates whose
# daemons start together (the deployer restarts sf-cluster on every node
# in one parallel task) keep an almost stable relative poll order for
# minutes, so a freed lock always went to whichever candidate's standing
# phase came next -- the same one or two nodes every time, never a fair
# draw (issue 3663). The jitter re-randomises the order on every cycle.
# The mean of the range must stay at five seconds: the
# AcquireLock/cluster entry in shakenfist/data/database_load_budget.yaml
# is arithmetic over one attempt per candidate per five seconds.
ELECTION_POLL_MINIMUM_SECONDS = 2.5
ELECTION_POLL_MAXIMUM_SECONDS = 7.5


class Monitor(daemon.Daemon):
    # Set by _run_inner() when the maintenance schedule is registered.
    # Declared here as well because the maintenance hook which forces a
    # capacity pass reads it, and a Monitor built by a test which never
    # registers a schedule would otherwise raise rather than no-op.
    _capacity_reconcile_job = None

    # The unguarded condition the last forced capacity reconcile was
    # issued for, so the same one is not forced again on every
    # maintenance pass. Process-local and reset on election; a daemon
    # restart re-forcing once is correct rather than a bug, which is why
    # this is never persisted.
    _forced_capacity_reconcile_for = None

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
        #
        # Never overwrite a lock object we still hold: the orphaned
        # object's refresher thread would keep renewing the lease
        # forever, and this process would then wait indefinitely to
        # steal a lock from itself (issue 3802).
        if self.lock is not None and self.lock.is_acquired():
            try:
                self.lock.release()
            except exceptions.LockNotHeld:
                ...
        self.lock = None

        while daemon.check_abort_path(self.abort_path):
            # Idle before the first attempt as well as between attempts.
            # A daemon entering the election -- freshly restarted, or the
            # just-unseated holder re-entering after a lease loss -- must
            # not race ahead of the established candidates, which are on
            # average half a poll interval away from their next attempt.
            # Without this a restarted holder's immediate first acquire
            # won the lock straight back often enough that restarting it
            # was nearly a no-op as a way of moving the role (issue
            # 3663).
            self.idle(random.uniform(
                ELECTION_POLL_MINIMUM_SECONDS, ELECTION_POLL_MAXIMUM_SECONDS))
            self.check_daemon_state()
            if not daemon.check_abort_path(self.abort_path):
                return

            self.lock = locks.ClusterLock(
                'cluster', None, None, timeout=10, op='Cluster maintenance')
            if self.lock.acquire():
                self.is_elected = True
                return

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
            # One read for the whole table rather than one per address
            # (issue 3655); this sweep runs every 60 seconds. Iterate the
            # snapshot for the reservation bodies, but still iterate
            # fn.ipam.in_use: an address in use with no reservation row is a
            # state this sweep must still see, so the second read buys
            # robustness for one round trip per pass.
            reservations = fn.ipam.get_all_reservations()
            for addr in fn.ipam.in_use:
                self.pet_watchdog()
                reservation = reservations.get(addr)
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
        # by artifacts.
        #
        # The namespace names are read once for the whole sweep rather than
        # once per artifact. Namespace.from_db() overrides the cached base
        # implementation and its mariadb.get_namespace() caches on
        # OBJECT_CACHE_TTL_MUTABLE, which is 30s -- half this loop's 60s
        # period -- so every lookup here was a guaranteed cache miss and the
        # pair cost one GetNamespace per distinct namespace per pass forever.
        # On a deployed cluster that is small because namespaces are few and
        # long lived, which is why it sits under the load budget's 0.10/s
        # inclusion cut and is absent from that file. In the functional suite
        # it is not small: every test class creates its own uniquified
        # namespace, so the sweep sees as many namespaces as there are
        # concurrent tests holding artifacts and the pair read 0.33/s against
        # the unbudgeted ceiling of 0.25/s -- which is what
        # test_no_unbudgeted_fixed_rate_database_polling ejected this branch
        # from the merge queue for. One call per pass regardless of artifact
        # count is both the cheaper thing and the honest one.
        namespace_names = set(mariadb.get_all_namespace_names())
        if not namespace_names:
            # Never possible on a healthy cluster -- the system namespace
            # always exists -- so an empty answer means the read failed
            # rather than that every namespace is gone. The direct MariaDB
            # path returns [] on OperationalError, and this loop is the one
            # caller whose reaction to "no namespaces" is destructive: it
            # would delete every artifact in the cluster.
            #
            # The whole pass is abandoned rather than just this section,
            # which is the opposite of the choice made for an unreadable
            # active blob list below. The reason is the blob accounting the
            # sweep feeds: the loop below records artifact-backed blobs in
            # in_use_blobs, and record_usage() further down is the only
            # thing that refreshes last_used for a blob nothing reopens.
            # Running on with an empty in_use_blobs would let the reaper
            # treat every artifact-backed blob as unused, which is the
            # failure the blobs_readable flag exists to avoid one section
            # further down.
            LOG.warning(
                'No namespaces returned; abandoning this maintenance pass')
            return

        in_use_blobs = defaultdict(int)
        for a in artifact.Artifacts([]):
            self.pet_watchdog()

            # If the artifact's namespace is deleted then we should remove the
            # artifact
            if a.namespace not in namespace_names:
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
                # For fetches which outlive that grace period, http_fetch()
                # refreshes last_used as a heartbeat while data is flowing
                # (issue 4000), so reaching here with a stale last_used means
                # the fetcher is dead or stalled, not merely slow.
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

    def _register_anchored_job(self, interval, task_name, func):
        """Register a maintenance job whose cadence is anchored in the database.

        schedule computes a job's next run from the moment it is
        registered, which is process start. There are two ways that
        hurts, and a task with either one belongs here.

        A task whose period is longer than the typical daemon lifetime
        never comes due at all: on a cluster which redeploys daily, the
        daily prune_events effectively never ran and the events tables
        grew without bound (issue 3869).

        A task which is the sole creator of state something else
        depends on leaves that dependency unmet for one whole period
        after every process start, however short the period is. The
        capacity reconciler is the only thing which creates
        scheduler_node_capacity rows, and admission fails open on a
        node which has none (P7), so a five minute process-local timer
        meant every placement in a new cluster's first minutes was
        admitted against nothing -- and the reconciler's first pass
        then wrote the resulting over-limit usage onto the row it
        created, leaving a node refusing every create while measuring
        completely idle (issue 4087).

        Each run of an anchored task therefore records a cluster-wide
        last-run stamp in cluster_config, and _anchor_scheduled_jobs()
        re-reads the stamps on election. A persisted RecurringOperation
        would fix this by construction; until PLAN-recurring-operations
        lands, the stamp is the anchor.
        """
        key = SCHEDULED_TASK_LAST_RUN_PREFIX + task_name.upper()

        def stamped_task():
            ret = func()
            # A task which raises propagates before the stamp is
            # written, matching schedule's own behaviour of leaving a
            # raising job due. The stamp write itself is best-effort:
            # the task has already run, so failing to record that must
            # not re-run the whole task on the next 60 second cycle.
            try:
                mariadb.set_cluster_config(key, time.time())
            except Exception as e:
                LOG.with_fields({
                    'task': task_name,
                    'error': str(e)
                }).warning('Could not record scheduled task last-run stamp')
            return ret

        job = interval.do(stamped_task)
        self._anchored_jobs.append((job, key))
        return job

    def _anchor_scheduled_jobs(self):
        """Re-anchor long-period jobs to their persisted last-run stamps.

        Called on each election, before the elected loop's first
        scheduled pass. A job whose stamp is missing (first election
        since this code landed) or older than its period becomes due
        immediately; a job another node ran recently is pushed out to
        one period after that run, so a lock handover does not re-fire
        it either.
        """
        try:
            stamps = mariadb.get_cluster_config()
        except Exception as e:
            # Better a process-local timer -- the status quo ante --
            # than no maintenance node at all.
            LOG.with_fields({'error': str(e)}).warning(
                'Could not read scheduled task last-run stamps, keeping '
                'process-local timers')
            return

        now = datetime.datetime.now()
        for job, key in self._anchored_jobs:
            stamp = stamps.get(key)
            try:
                if stamp is None:
                    job.next_run = now
                else:
                    # schedule 1.2.2 computes a job's period inside
                    # _schedule_next_run() without retaining it, so
                    # rebuild it. Exact because our jobs never use the
                    # randomised `latest` bound.
                    period = datetime.timedelta(**{job.unit: job.interval})
                    job.next_run = (
                        datetime.datetime.fromtimestamp(stamp) + period)
            except (TypeError, ValueError, OSError, OverflowError) as e:
                LOG.with_fields({
                    'key': key,
                    'stamp': stamp,
                    'error': str(e)
                }).warning('Ignoring unusable scheduled task last-run stamp')

    def _force_capacity_reconcile_if_unguarded(self):
        """Make the capacity reconcile due now if placement is not guarded.

        A node with no scheduler_node_capacity row is admitted against
        nothing at all (P7), so every placement onto it fails open.
        Anchoring the reconcile covers the case which produced issue
        4087 -- a fresh cluster, whose stamp is missing, waiting out a
        five minute process-local timer -- but a stamp is a proxy for
        the condition rather than the condition itself. A cluster whose
        MariaDB outlived its nodes has a recent stamp and no row for any
        node uuid which now exists. Read the table and act on what it
        says.

        Both an empty table and a partly populated one matter, which is
        why this is no longer only an election-time check. Election
        happens before any hypervisor on a cold cluster has published
        metrics, so the pass it forced had nothing to size and the table
        stayed empty until the next cadence tick anyway. Asking the
        sharper question -- is there an active hypervisor with fresh
        metrics and still no row? -- from the maintenance pass closes
        the window about a minute after the first metrics appear.

        The unguarded set deliberately mirrors the predicate
        _direct_reconcile_scheduler_capacity() applies when it decides
        which nodes get a row, because a check which asks a *broader*
        question than the pass can answer would demand a pass on every
        cycle for the life of the cluster. The active-node intersection
        is the clause which cannot be dropped: a node_metrics row which
        outlived its node reads as a fresh hypervisor forever (the
        phantom the reconciler's own comment describes), and without
        that clause it would silently turn a five minute job into a
        sixty second one. The reconciler's fourth clause -- a row in the
        nodes table -- is not restated here because
        Nodes([], prefilter='active') hydrates each node from that table
        and drops anything it cannot find, so a stateless zombie is
        already excluded.

        A failed read is not an empty table, and this must not confuse
        the two: get_scheduler_node_capacity() reports which it was, and
        only an authoritative empty result forces a pass. A degraded
        read leaves the anchored cadence alone rather than scheduling
        work on a guess -- and if the database service is unreachable
        the reconcile RPC would fail anyway.

        Finally, a forced pass which does not clear the condition must
        not be re-forced every minute forever, so the condition last
        forced for is remembered and the same one is never forced twice.
        If some node qualifies here but the reconciler declines to size
        it anyway, that costs one pass rather than a permanent load
        regression; the ordinary five minute cadence still runs.
        """
        if self._capacity_reconcile_job is None:
            return

        try:
            rows, degraded = mariadb.get_scheduler_node_capacity()
        except Exception as e:
            LOG.with_fields({'error': str(e)}).warning(
                'Could not read scheduler capacity counters, leaving the '
                'capacity reconcile on its anchored cadence')
            return

        if degraded:
            return

        unguarded = self._unguarded_hypervisors(rows)

        if not rows:
            # Nothing at all is guarded, which is worth saying in the
            # words it has always been said in. The condition still
            # carries the unguarded set, because a cold cluster forces a
            # pass here before any hypervisor has published metrics: the
            # pass finds nothing to size, the table stays empty, and
            # without the set in the condition the arrival of the first
            # hypervisor would look like the same condition and force
            # nothing. A failed metrics read reads as the empty set,
            # which is the right answer for this branch -- the empty
            # table alone justifies the pass.
            self._force_capacity_reconcile(
                (CAPACITY_TABLE_EMPTY, frozenset(unguarded or set())),
                'No scheduler capacity rows exist, so every placement is '
                'being admitted unguarded; reconciling capacity now',
                fields={'nodes': sorted(unguarded)} if unguarded else None)
            return

        if unguarded is None:
            return

        if not unguarded:
            self._forced_capacity_reconcile_for = None
            return

        self._force_capacity_reconcile(
            frozenset(unguarded),
            'Active hypervisors have no scheduler capacity row, so their '
            'placements are being admitted unguarded; reconciling capacity '
            'now',
            fields={'nodes': sorted(unguarded)})

    def _unguarded_hypervisors(self, rows):
        """Active hypervisors with fresh metrics and no capacity row.

        Returns None if a read failed, which is a different answer from
        the empty set for the same reason ``degraded`` is different from
        an empty table: it means this process does not know.
        """
        try:
            metrics = mariadb.get_all_node_metrics()  # nopushdown: every node wanted
        except Exception as e:
            LOG.with_fields({'error': str(e)}).warning(
                'Could not read node metrics, leaving the capacity '
                'reconcile on its anchored cadence')
            return None

        # is_hypervisor lives inside the metrics blob rather than being a
        # top level field of the record, and is compared against True
        # rather than tested for truth: the reconciler leaves a node
        # whose value is missing or NULL (mid-upgrade, before the
        # resources daemon repopulates the column) out of both its
        # hypervisor set and its non-hypervisor set, and so do we.
        # RECONCILE_METRICS_MAX_AGE_SECONDS is imported rather than
        # restated because a check carrying its own copy of that number
        # would drift from the pass it is trying to trigger.
        fresh_after = time.time() - mariadb.RECONCILE_METRICS_MAX_AGE_SECONDS
        candidates = set()
        for record in metrics:
            if (record.get('metrics') or {}).get('is_hypervisor') is not True:
                continue
            if (record.get('timestamp') or 0) <= fresh_after:
                continue
            candidates.add(str(record.get('node_uuid')))

        candidates -= {str(row.get('node_uuid')) for row in rows}
        if not candidates:
            return set()

        # Only now is the active node set worth reading. In the steady
        # state every fresh hypervisor already has a row, so this costs
        # nothing on the overwhelming majority of maintenance passes.
        try:
            active = {str(n.uuid) for n in Nodes([], prefilter='active')}
        except Exception as e:
            LOG.with_fields({'error': str(e)}).warning(
                'Could not read the active node set, leaving the capacity '
                'reconcile on its anchored cadence')
            return None

        return candidates & active

    def _force_capacity_reconcile(self, condition, message, fields=None):
        """Mark the capacity reconcile due, once per distinct condition.

        ``condition`` is the thing observed -- the empty table paired
        with whatever was unguarded at the time, or the frozenset of
        unguarded node uuids on their own -- and forcing is skipped when
        it has not changed since the last force. That bounds a
        disagreement between this check and the reconciler to one pass
        and one log line rather than one of each per minute.
        """
        if self._forced_capacity_reconcile_for == condition:
            return

        self._forced_capacity_reconcile_for = condition
        log = LOG.with_fields(fields) if fields else LOG
        log.warning(message)
        scheduled_tasks.SCHEDULER_CAPACITY_FORCED.inc()
        self._capacity_reconcile_job.next_run = datetime.datetime.now()

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

        # Initialised here, outside the election loop below, and the
        # unguarded-capacity check now depends on that: the first
        # maintenance pass after any election finds
        # now - last_loop_run >= 60 true and therefore runs immediately,
        # which is the only reason it is safe for
        # _force_capacity_reconcile_if_unguarded() to live in that pass
        # rather than on the election path. Do not move it inside the
        # loop.
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
        #
        # Continuous timers are still process-local, though, so three
        # tasks are additionally anchored to a persisted cluster-wide
        # last-run stamp (see _register_anchored_job) so that a process
        # restart does not restart their period from zero either. Two
        # are anchored because their period outlives the daemon (issue
        # 3869); the capacity reconciler is anchored because it is the
        # only thing which creates the rows admission guards against,
        # and a process-local timer left a new cluster with no guard at
        # all for five minutes (issue 4087).
        self._anchored_jobs = []
        schedule.every(1).minutes.do(
            scheduled_tasks.log_cluster_queue_lengths)
        schedule.every(1).minutes.do(
            scheduled_tasks.reap_stuck_cluster_operation_jobs)
        schedule.every(5).minutes.do(
            scheduled_tasks.per_blob_checks)
        schedule.every(5).minutes.do(
            scheduled_tasks.per_instance_checks_and_usage)
        schedule.every(15).minutes.do(
            scheduled_tasks.per_deleted_object_checks)
        schedule.every(15).minutes.do(
            scheduled_tasks.reap_expired_namespace_keys)
        schedule.every(15).minutes.do(
            scheduled_tasks.reap_federation_records)
        self._capacity_reconcile_job = self._register_anchored_job(
            schedule.every(5).minutes, 'reconcile_scheduler_capacity',
            scheduled_tasks.reconcile_scheduler_capacity)
        self._register_anchored_job(
            schedule.every(60).minutes, 'reconcile_orphaned_objects',
            scheduled_tasks.reconcile_orphaned_objects)
        self._register_anchored_job(
            schedule.every(1).days, 'prune_events',
            scheduled_tasks.prune_events)

        while daemon.check_abort_path(self.abort_path):
            util_concurrency.set_thread_name('idle')
            LOG.debug('This cluster thread is now idle and awaiting election')
            self._await_election()

            util_concurrency.set_thread_name('active')
            LOG.debug('This cluster thread is now active')

            # Anchor the long-period jobs to the cluster-wide record of
            # when they last ran, wherever they ran. Guarded on
            # is_elected because _await_election also returns on the
            # abort path, where a database read would only delay
            # shutdown.
            if self.is_elected:
                self._anchor_scheduled_jobs()

                # Forget which unguarded condition we last forced a
                # capacity reconcile for. A node taking over maintenance
                # has no reason to trust the previous holder's
                # observations, and the check itself runs from the
                # maintenance pass below rather than from here.
                self._forced_capacity_reconcile_for = None

            # And then do regular cluster maintenance things
            while self.is_elected and not os.path.exists(self.abort_path):
                # This elected loop sleeps via lock.lost_event.wait() rather
                # than idle(), so it must pet the systemd watchdog itself
                # (rate-limited internally) to stay alive between maintenance
                # passes and during the cleanup below. For the same reason it
                # must poll its own daemon state row (also rate-limited
                # internally): an externally written stop request -- sf-ctl
                # stop cluster -- is only noticed by check_daemon_state(),
                # and without this call the elected node ignored it until it
                # lost election (issue 3874). The wait below bounds the
                # added stop latency at ELECTED_LOOP_POLL_SECONDS, inside
                # TimeoutStopSec=30s.
                self.pet_watchdog()
                self.check_daemon_state()

                # An unstable cluster (mixed object versions during an
                # upgrade, or no fresh node metrics at all just after a
                # whole-cluster restart) defers maintenance, but we stay
                # elected and keep the lock while we re-check on every
                # pass. Deferring by re-entering the election used to
                # orphan the held lock object -- its refresher thread
                # kept renewing the lease forever, so the daemon
                # deadlocked against itself and all cluster maintenance
                # silently stopped (issue 3802).
                if not self.cluster_stable():
                    if time.time() - last_defer_message > 10:
                        LOG.info('Cluster not yet stable, deferring maintenance')
                        last_defer_message = time.time()

                else:
                    now = time.time()
                    if now - last_loop_run >= 60:
                        # Placement onto a node with no capacity row is
                        # admitted against nothing, so re-ask that
                        # question every maintenance pass rather than
                        # once per election -- on a cold cluster no
                        # hypervisor has published metrics by the time
                        # election happens. This sits ahead of
                        # _run_due_scheduled_jobs() so a pass forced
                        # here runs in this same maintenance pass. Note
                        # it is now behind cluster_stable(), which
                        # compares object versions across nodes and
                        # reads no metric freshness at all, so it defers
                        # this check during a mixed-version upgrade.
                        try:
                            self._force_capacity_reconcile_if_unguarded()
                        except Exception as e:
                            util_exceptions.ignore_exception('cluster', e)

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

                # Sleep up to ELECTED_LOOP_POLL_SECONDS. Wakes early if
                # the background refresher reports our lease was stolen,
                # OR when the timeout fires so the outer ``not
                # os.path.exists(self.abort_path)`` check runs again. The
                # maintenance work above is gated at 60 s; this short
                # poll is just so SIGTERM during a quiet period gets
                # observed inside the systemd ``TimeoutStopSec=30s``
                # budget (the old wait(60) parked past it and got
                # SIGKILLed).
                if self.lock.lost_event.wait(ELECTED_LOOP_POLL_SECONDS):
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
