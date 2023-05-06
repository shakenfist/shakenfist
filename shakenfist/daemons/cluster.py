# The cluster daemon is for cluster level maintenance tasks which are not
# urgent. Hard deleting data for example. Its therefore pretty relaxed about
# obtaining the lock to do work et cetera. There is only one active cluster
# maintenance daemon per cluster.

from collections import defaultdict
from functools import partial
import setproctitle
from shakenfist_utilities import logs
import time

from shakenfist import artifact
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo, active_states_filter)
from shakenfist.baseobjectmapping import (
    OBJECT_NAMES_TO_CLASSES, OBJECT_NAMES_TO_ITERATORS)
from shakenfist.blob import Blob, Blobs, placement_filter, all_active_blobs
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist.eventlog import EVENT_TYPE_AUDIT
from shakenfist import instance
from shakenfist import namespace
from shakenfist import network
from shakenfist import networkinterface
from shakenfist.node import (
    Node, Nodes,
    inactive_states_filter as node_inactive_states_filter,
    nodes_by_free_disk_descending)
from shakenfist.upload import Uploads


LOG, _ = logs.setup(__name__)


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super(Monitor, self).__init__(name)
        self.lock = None
        self.is_elected = False

    def _await_election(self):
        # Attempt to acquire the cluster maintenance lock forever. We never
        # release the lock, it gets cleared on a crash. This is so that only
        # one node at a time is performing cluster maintenance.
        while not self.exit.is_set():
            self.lock = etcd.get_lock('cluster', None, None, ttl=120, timeout=10,
                                      op='Cluster maintenance')
            result = self.lock.acquire()
            if result:
                self.is_elected = True
                return
            self.exit.wait(10)

    def _cluster_wide_cleanup(self, last_loop_run):
        LOG.info('Running cluster maintenance')

        # Recompute our cache of what blobs are on what nodes every 30 minutes
        if time.time() - last_loop_run > 1800:
            per_node = defaultdict(list)
            for b in all_active_blobs():
                if not b.locations:
                    b.add_event(EVENT_TYPE_AUDIT, 'No locations for this blob, hard deleting.')
                    b.hard_delete()

                for node in b.locations:
                    per_node[node].append(b.uuid)

            for node in Nodes([]):
                node.blobs = per_node.get(node.uuid, [])
            self.lock.refresh()

        # Cleanup soft deleted objects
        for objtype in OBJECT_NAMES_TO_ITERATORS:
            for obj in OBJECT_NAMES_TO_ITERATORS[objtype]([]):
                if (obj.state.value == dbo.STATE_DELETED and
                        time.time() - obj.state.update_time > config.CLEANER_DELAY):
                    obj.hard_delete()
        self.lock.refresh()

        # Cleanup vxids which specify a missing network
        for k, objdata in etcd.get_all('vxlan', None):
            network_uuid = objdata.get('network_uuid')
            if network_uuid:
                n = network.Network.from_db(network_uuid)
                if not n:
                    etcd.get_etcd_client().delete(k)
                    LOG.with_fields({
                        'network': network_uuid,
                        'vxid record': k
                    }).warning('Cleaning up leaked vxlan')
        self.lock.refresh()

        # Cleanup ipmanagers whose network is absent
        for k, objdata in etcd.get_all('ipmanager', None):
            network_uuid = objdata.get('uuid')
            if network_uuid:
                n = network.Network.from_db(network_uuid)
                if not n:
                    etcd.get_etcd_client().delete(k)
                    LOG.with_fields({
                        'ipmanager': network_uuid
                    }).warning('Cleaning up leaked ipmanager')
        self.lock.refresh()

        # Cleanup old uploads which were never completed
        for upload in Uploads([]):
            if time.time() - upload.state.update_time > 7 * 24 * 3600:
                LOG.with_fields({
                    'upload': upload.uuid
                }).warning('Cleaning up stale upload')
                upload.hard_delete()
        self.lock.refresh()

        # Cleanup orphan artifacts, delete old versions, and record blobs used
        # by artifacts
        in_use_blobs = defaultdict(int)
        for a in artifact.Artifacts([]):
            # If the artifact's namespace is deleted then we should remove the
            # artifact
            ns = namespace.Namespace.from_db(a.namespace)
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
        self.lock.refresh()

        # Inspect current state of blobs, the actual changes are done below outside
        # the read only cache. We define being low on disk has having less than three
        # times the minimum amount of disk. This is so we start to rearrange blobs
        # before scheduling starts to fail.
        overreplicated = {}
        underreplicated = []
        low_disk_nodes = nodes_by_free_disk_descending(
            minimum=0, maximum=(config.MINIMUM_FREE_DISK * 3),
            intention='blobs')

        absent_nodes = []
        for n in Nodes([node_inactive_states_filter]):
            LOG.with_fields({
                'node': n.fqdn}).info('Node is absent for blob replication')
            absent_nodes.append(n.fqdn)
        LOG.info('Found %d inactive nodes' % len(absent_nodes))

        # We count fetches currently requested (or under way) as having completed
        # in order to stop over-replication for large blobs.
        current_fetches = etcd.get_current_blob_transfers(
            absent_nodes=absent_nodes)

        for b in all_active_blobs():
            if b.instances:
                in_use_blobs[b.uuid] += 1

            # If there is current work for a blob, we ignore it until that
            # work completes
            if b.uuid in current_fetches:
                LOG.with_fields({
                    'blob': b.blob_uuid
                }).info('Blob has current fetches, ignoring')
                continue

            locations = b.locations
            ignored_locations = []
            for n in absent_nodes:
                if n in locations:
                    locations.remove(n)
                    ignored_locations.append(n)

            if ignored_locations:
                LOG.with_fields({
                    'blob': b,
                    'ignored_locations': ignored_locations
                }).info('Ignored some blob locations as nodes are absent')

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

                for instance_uuid in b.instances:
                    i = instance.Instance.from_db(instance_uuid)
                    node = i.placement.get('node')
                    if node in excess_locations:
                        excess_locations.remove(node)
                        in_use_locations.append(node)

                # Only remove excess copies from nodes which are running
                # low on disk. Do not end up with too few replicas.
                overreplicated[b.uuid] = []
                target = (config.BLOB_REPLICATION_FACTOR - len(in_use_locations))
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
            b = Blob.from_db(blob_uuid)
            if b:
                b.record_usage()
        self.lock.refresh()

        # Find expired blobs
        for b in all_active_blobs():
            if b.expires_at > 0 and b.expires_at < time.time():
                b.add_event(EVENT_TYPE_AUDIT, 'blob has expired')
                b.state = dbo.STATE_DELETED
        self.lock.refresh()

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
        self.lock.refresh()

        # Replicate under replicated blobs, but only if we don't have heaps of
        # queued replications already
        for blob_uuid, excess in underreplicated:
            LOG.with_fields({
                'current': len(current_fetches),
                'maximum': config.MAX_CONCURRENT_BLOB_TRANSFERS
            }).info('Concurrent blob transfers')
            if len(current_fetches) > config.MAX_CONCURRENT_BLOB_TRANSFERS:
                LOG.info(
                    'Too many concurrent blob transfers queued, not queueing more')
                break

            b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
            if b:
                LOG.with_fields({
                    'blob': b
                }).info('Blob under replicated, attempting to correct')
                b.request_replication(allow_excess=excess)
                current_fetches[blob_uuid].append('unknown')
        self.lock.refresh()

        # Find transcodes of not recently used blobs and reap them
        for b in all_active_blobs():
            if not b.transcoded:
                continue

            if time.time() - b.last_used > config.BLOB_TRANSCODE_MAXIMUM_IDLE_TIME:
                transcoded = b.transcoded
                b.remove_transcodes()
                for transcode in transcoded:
                    tb = Blob.from_db(transcoded[transcode])
                    tb.ref_count_dec(b)
        self.lock.refresh()

        # Node management
        for n in Nodes([]):
            age = time.time() - n.last_seen

            LOG.with_fields(
                {
                    'node': n.uuid,
                    'status_age': age,
                    'last_seen': n.last_seen,
                    'state': n.state.value
                }).debug('Considering node status')

            # Find nodes which have returned from being missing
            if n.state.value == Node.STATE_CREATED:
                if age > config.NODE_CHECKIN_MAXIMUM:
                    n.state = Node.STATE_MISSING
                    n.add_event(EVENT_TYPE_AUDIT, 'node has gone missing')
            elif n.state.value == Node.STATE_MISSING:
                if age < config.NODE_CHECKIN_MAXIMUM:
                    n.state = Node.STATE_CREATED
                    n.add_event(EVENT_TYPE_AUDIT, 'node returned from being missing')
            elif n.state.value == Node.STATE_DELETED:
                # Find instances on deleted nodes
                for i in instance.healthy_instances_on_node(n):
                    i.add_event(EVENT_TYPE_AUDIT, 'instance is on deleted node, deleting.')
                    n.add_event(
                        EVENT_TYPE_AUDIT,
                        'deleting instance %s as node as been deleted' % i.uuid)
                    i.delete(global_only=True)

                    # Cleanup the instance's interfaces
                    for ni in networkinterface.interfaces_for_instance(i):
                        ni.delete()

                # Cleanup any blob locations
                for b in Blobs([active_states_filter, partial(placement_filter, n.fqdn)]):
                    n.add_event(
                        EVENT_TYPE_AUDIT,
                        'deleting blob %s location as hosting node has been deleted' % b.uuid)
                    b.add_event(
                        EVENT_TYPE_AUDIT,
                        'deleting blob location as hosting node %s has been deleted' % n.uuid)
                    b.remove_location(n.fqdn)
                    b.request_replication()

                # Clean up any lingering queue tasks
                jobname, workitem = etcd.dequeue(n.uuid)
                while workitem:
                    LOG.with_fields({
                        'jobname': jobname,
                        'node': n.uuid
                    }).info('Deleting work item for deleted node')
                    etcd.resolve(n.uuid, jobname)
                    jobname, workitem = etcd.dequeue(n.uuid)

        # And we're done
        LOG.info('Cluster maintenance loop complete')

    def refresh_object_state_caches(self):
        for object_type in OBJECT_NAMES_TO_ITERATORS:
            with etcd.get_lock('cache', None, object_type, op='Cache update'):
                by_state = {'deleted': {}}

                for state in OBJECT_NAMES_TO_CLASSES[object_type].state_targets:
                    if state:
                        by_state[state] = {}

                for obj in OBJECT_NAMES_TO_ITERATORS[object_type]([]):
                    if obj.state.value:
                        by_state[obj.state.value][obj.uuid] = time.time()

                for state in by_state:
                    etcd.put('cache', object_type, state, by_state[state])

    def run(self):
        LOG.info('Starting')

        self.refresh_object_state_caches()

        last_loop_run = 0
        while not self.exit.is_set():
            setproctitle.setproctitle(daemon.process_name('cluster') + ' idle')
            self._await_election()

            # Infrequently audit blob references and correct errors
            discovered_refs = defaultdict(list)
            for b in all_active_blobs():
                discovered_refs[b.uuid] = []

            for i in instance.Instances([instance.active_states_filter]):
                for d in i.block_devices.get('devices', []):
                    blob_uuid = d.get('blob_uuid')
                    if blob_uuid:
                        discovered_refs[blob_uuid].append(str(i))

            for a in artifact.Artifacts(filters=[active_states_filter]):
                for blob_index in a.get_all_indexes():
                    blob_uuid = blob_index['blob_uuid']
                    discovered_refs[blob_uuid].append(str(a))

            for b in all_active_blobs():
                dep_blob_uuid = b.depends_on
                if dep_blob_uuid:
                    discovered_refs[dep_blob_uuid].append(str(b))

                transcodes = b.transcoded
                for t in transcodes:
                    discovered_refs[transcodes[t]].append(str(t))

            for blob_uuid in discovered_refs:
                # If the blob still exists, and is more than five minutes old,
                # we should correct the reference count.
                b = Blob.from_db(blob_uuid)
                if b and (time.time() - b.fetched_at > 300):
                    b.ref_count_set(len(discovered_refs[blob_uuid]),
                                    discovered_refs[blob_uuid])

            # Infrequently ensure we have no blobs with a reference count of zero
            orphan_blobs = []
            for b in all_active_blobs():
                if b.ref_count == 0:
                    orphan_blobs.append(b)

            for b in orphan_blobs:
                self.log.with_fields({'blob': b}).error(
                    'Blob has zero references, deleting')
                b.state = Blob.STATE_DELETED

            # And then do regular cluster maintenance things
            while self.is_elected and not self.exit.is_set():
                self.lock.refresh()

                setproctitle.setproctitle(
                    daemon.process_name('cluster') + ' active')
                self.lock.refresh()

                self._cluster_wide_cleanup(last_loop_run)
                last_loop_run = time.time()
                self.lock.refresh()

                self.exit.wait(60)

        # Stop being the cluster maintenance node if we were
        if self.lock.is_acquired():
            self.lock.release()
        LOG.info('Terminating')
