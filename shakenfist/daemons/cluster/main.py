# The cluster daemon is for cluster level maintenance tasks which are not
# urgent. Hard deleting data for example. Its therefore pretty relaxed about
# obtaining the lock to do work et cetera. There is only one active cluster
# maintenance daemon per cluster.
from collections import defaultdict
import os
import time

import schedule
from shakenfist_utilities import logs  # noreorder

from shakenfist import artifact
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import mariadb
from shakenfist import instance
from shakenfist import ipam
from shakenfist import namespace
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
from shakenfist.exceptions import InvalidStateException
from shakenfist.node import Node
from shakenfist.node import Nodes
from shakenfist.node import nodes_by_free_disk_descending
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import get_all_node_queues
from shakenfist.upload import remove_abandoned_uploads
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general
from shakenfist.util import json as util_json


LOG, _ = logs.setup(__name__)


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super().__init__(name)
        self.lock = None
        self.is_elected = False

    def _await_election(self):
        # Attempt to acquire the cluster maintenance lock forever. We never
        # release the lock, it gets cleared on a crash. This is so that only
        # one node at a time is performing cluster maintenance.
        while daemon.check_abort_path(self.abort_path):
            self.lock = etcd.ClusterLock(
                'cluster', None, None, timeout=10, op='Cluster maintenance')
            result = self.lock.acquire()
            if result:
                self.is_elected = True
                return

            self.idle(5)
            self.check_daemon_state()

    def _cluster_wide_cleanup(self, last_loop_run):
        LOG.info('Running cluster maintenance')

        # NOTE: The per-node blob cache is now maintained by each node's
        # cleaner daemon calling observe() on local blobs. The cleaner also
        # handles hard-deleting blobs with no locations. This is more accurate
        # since each node knows definitively what files exist on its local disk.

        # Cleanup stale blob transfers (transfers that haven't been updated in
        # more than 10 minutes are likely abandoned)
        deleted = mariadb.delete_stale_transfers(max_age=600)
        if deleted > 0:
            LOG.info(f'Deleted {deleted} stale blob transfers')

        # Cleanup vxids which specify a missing network. We ignore allocations
        # less than five minutes old to let the network setup complete.
        for k, objdata in etcd.get_all('vxlan', None):
            when = objdata.get('when')
            if not when:
                objdata['when'] = time.time()
                etcd.get_etcd_client().put(k, util_json.json_dump(objdata))
                continue

            if time.time() - when < 300:
                continue

            network_uuid = objdata.get('network_uuid')
            if network_uuid:
                n = network.Network.from_db(
                    network_uuid, suppress_failure_audit=True)
                if not n:
                    etcd.get_etcd_client().delete(k)
                    LOG.with_fields({
                        'network': network_uuid,
                        'vxid record': k
                    }).warning('Cleaning up leaked vxlan')

        # Cleanup ipmanagers whose network is absent
        # TODO(mikal): remove in v0.9
        for k, objdata in etcd.get_all('ipmanager', None):
            when = time.time()
            if 'ipmanager.v3' in objdata:
                for reservation in objdata['ipmanager.v3']['in_use']:
                    when = objdata['ipmanager.v3']['in_use'][reservation]['when']
                    break
            if time.time() - when < 300:
                continue

            network_uuid = objdata.get('uuid')
            if network_uuid:
                n = network.Network.from_db(network_uuid)
                if not n:
                    etcd.get_etcd_client().delete(k)
                    LOG.with_fields({
                        'ipmanager': network_uuid
                    }).warning('Cleaning up leaked ipmanager')

        # Cleanup IPAMs whose network is absent
        for ipm in ipam.IPAMs([], prefilter='active'):
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
        # the read only cache. We define being low on disk has having less than three
        # times the minimum amount of disk. This is so we start to rearrange blobs
        # before scheduling starts to fail.
        overreplicated = {}
        underreplicated = []
        low_disk_nodes = nodes_by_free_disk_descending(
            minimum=0, maximum=(config.MINIMUM_FREE_DISK * 3),
            intention='blobs')

        # We count fetches currently requested (or under way) as having completed
        # in order to stop over-replication for large blobs.
        for blob_uuid in mariadb.get_active_blob_uuids():
            b = Blob.from_db(blob_uuid)
            if not b:
                continue
            instances = instance.instance_usage_for_blob_uuid(b.uuid)
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

            incomplete_nodes = [loc['node'] for loc in b.incomplete_healthy_locations]
            locations = b.locations + incomplete_nodes
            delta = len(locations) - config.BLOB_REPLICATION_FACTOR
            if delta > 0:
                # So... The blob replication factor is a target not a limit.
                # Specifically, if there are more locations than the target
                # but we aren't low on disk, we don't clean them up. That's
                # because its hard for us to predict which machine will run
                # out of disk first, and copying a blob back to a machine if
                # its needed there is slow and annoying.

                # Work out where the blob is in active use.
                excess_locations = b.locations
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
                    if n in b.locations:
                        # We have at least one space constrained node with
                        # this blob. Request an extra temporary copy of the
                        # blob elsewhere so we can hopefully clean up one of
                        # these next pass. The tuple is blob UUID, and how
                        # much to over replicate by.
                        underreplicated.append((b.uuid, 1))
                        break

        # Record blobs in use
        for blob_uuid in in_use_blobs:
            b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
            if b:
                b.record_usage()

        # Find expired blobs (database-level filtering)
        for blob_uuid in mariadb.get_expired_blob_uuids():
            b = Blob.from_db(blob_uuid)
            if b:
                b.add_event(EVENT_TYPE_AUDIT, 'blob has expired')
                b.state = dbo.STATE_DELETED

        # Prune over replicated blobs
        for blob_uuid in overreplicated:
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
            b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
            if b:
                LOG.with_fields({
                    'blob': b
                }).info('Blob under replicated, attempting to correct')
                b.request_replication(allow_excess=excess)

        # Find transcodes of not recently used blobs and reap them
        # (database-level filtering)
        for blob_uuid in mariadb.get_stale_transcoded_blob_uuids(
                config.BLOB_TRANSCODE_MAXIMUM_IDLE_TIME):
            b = Blob.from_db(blob_uuid)
            if b:
                b.remove_transcodes()

        # Node management
        for n in Nodes([]):
            age = round(time.time() - n.last_seen, 2)

            LOG.with_fields(
                {
                    'node': n.uuid,
                    'status_age': age,
                    'last_seen': n.last_seen,
                    'state': n.state.value
                }).debug('Considering node status')

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
                for blob_uuid in n.blobs:
                    b = Blob.from_db(blob_uuid)
                    if not b:
                        continue
                    eventlog.add_event_multi(
                        EVENT_TYPE_AUDIT, [n, b],
                        'deleting blob location as hosting node has been deleted')
                    b.remove_location(n.fqdn)
                    b.request_replication()

                # Clean up any lingering queue tasks
                for queue_name in get_all_node_queues(n.uuid):
                    while jobname_workitem := etcd.dequeue(queue_name):
                        jobname, workitem = jobname_workitem
                        n.add_event(
                            EVENT_TYPE_AUDIT,
                            'deleting work item for deleted node',
                            extra={
                                'jobname': jobname,
                                'queue': queue_name
                            })

                        # Cluster operations might have dependencies
                        if queue_name.find('-clusteroperation-') != -1:
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

                        etcd.resolve(queue_name, jobname)

        # And we're done
        LOG.info('Cluster maintenance loop complete')

    def _run_inner(self):
        last_defer_message = 0
        last_loop_run = 0

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
                continue

            # Setup a schedule of things to do
            schedule.every(1).minutes.do(
                scheduled_tasks.log_cluster_queue_lengths)
            schedule.every(5).minutes.do(
                scheduled_tasks.per_blob_checks)
            schedule.every(5).minutes.do(
                scheduled_tasks.per_instance_checks_and_usage)
            schedule.every(15).minutes.do(
                scheduled_tasks.per_deleted_object_checks)

            # And then do regular cluster maintenance things
            while self.is_elected and not os.path.exists(self.abort_path):
                try:
                    with util_general.RecordedOperation(
                            'scheduled cluster operations', None, threshold=10):
                        schedule.run_pending()
                except Exception as e:
                    util_exceptions.ignore_exception('cluster', e)

                try:
                    with util_general.RecordedOperation(
                            'cluster wide cleanup', None, threshold=10):
                        self._cluster_wide_cleanup(last_loop_run)
                except Exception as e:
                    util_exceptions.ignore_exception('cluster', e)

                last_loop_run = time.time()

                self.idle(60)

        # Stop being the cluster maintenance node if we were
        if self.lock.is_acquired():
            self.lock.release()


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('cluster')
    m = Monitor('cluster')
    m.run()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    raise SystemExit(0)
