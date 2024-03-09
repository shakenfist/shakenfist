# The cluster daemon is for cluster level maintenance tasks which are not
# urgent. Hard deleting data for example. Its therefore pretty relaxed about
# obtaining the lock to do work et cetera. There is only one active cluster
# maintenance daemon per cluster.

from collections import defaultdict
from functools import partial
import json
import setproctitle
from shakenfist_utilities import logs
import time

from shakenfist import artifact
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobjectmapping import (
    OBJECT_NAMES_TO_CLASSES, OBJECT_NAMES_TO_ITERATORS)
from shakenfist.blob import Blob, Blobs, placement_filter
from shakenfist import cache
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import instance
from shakenfist import ipam
from shakenfist import namespace
from shakenfist import network
from shakenfist import networkinterface
from shakenfist.node import Node, Nodes, nodes_by_free_disk_descending
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
            self.lock = etcd.get_lock('cluster', None, None, ttl=300, timeout=10,
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
            for b in Blobs([], prefilter='active'):
                if not b.locations:
                    b.add_event(EVENT_TYPE_AUDIT, 'no locations for this blob, hard deleting.')
                    b.hard_delete()

                for node in b.locations:
                    per_node[node].append(b.uuid)

            for node in Nodes([]):
                node.blobs = per_node.get(node.uuid, [])
            self.lock.refresh()

        # Cleanup soft deleted objects
        for objtype in OBJECT_NAMES_TO_ITERATORS:
            for obj in OBJECT_NAMES_TO_ITERATORS[objtype]([], prefilter='deleted'):
                if time.time() - obj.state.update_time > config.CLEANER_DELAY:
                    obj.hard_delete()
        self.lock.refresh()

        # Cleanup vxids which specify a missing network. We ignore allocations
        # less than five minutes old to let the network setup complete.
        for k, objdata in etcd.get_all('vxlan', None):
            when = objdata.get('when')
            if not when:
                objdata['when'] = time.time()
                etcd.get_etcd_client().put(
                    k, json.dumps(objdata, indent=4, sort_keys=True))
                continue

            if time.time() - when < 300:
                continue

            network_uuid = objdata.get('network_uuid')
            if network_uuid:
                n = network.Network.from_db(network_uuid, suppress_failure_audit=True)
                if not n:
                    etcd.get_etcd_client().delete(k)
                    LOG.with_fields({
                        'network': network_uuid,
                        'vxid record': k
                    }).warning('Cleaning up leaked vxlan')
        self.lock.refresh()

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
        self.lock.refresh()

        # Cleanup IPAMs whose network is absent
        for ipm in ipam.IPAMs([], prefilter='active'):
            if time.time() - ipm.state.update_time < 300:
                continue

            n = network.Network.from_db(ipm.network_uuid, suppress_failure_audit=True)
            if not n and ipm.state.value != dbo.STATE_DELETED:
                ipm.state = dbo.STATE_DELETED
                ipm.log.warning('Cleaning up leaked IPAM')
        self.lock.refresh()

        # Cleanup floating IP reservations which refer to deleted objects
        fn = network.floating_network()
        releaseable = []
        with fn.ipam.get_lock('reservations', op='Delete stray reservations'):
            for addr in fn.ipam.in_use:
                reservation = fn.ipam.get_reservation(addr)
                if reservation['type'] not in [ipam.RESERVATION_TYPE_GATEWAY,
                                               ipam.RESERVATION_TYPE_FLOATING,
                                               ipam.RESERVATION_TYPE_ROUTED]:
                    continue

                object_type, object_uuid = reservation['user']
                obj = OBJECT_NAMES_TO_CLASSES[object_type].from_db(object_uuid)
                if not obj or obj.state.value == dbo.STATE_DELETED:
                    releaseable.append(addr)

        for addr in releaseable:
            fn.ipam.release(addr)
            fn.ipam.log.with_fields({object_type: object_uuid}).warning(
                'Cleaned up an address which refers to a deleted object')

        # Cleanup old uploads which were never completed
        for upload in Uploads([]):
            if time.time() - upload.created_at > 7 * 24 * 3600:
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
        for n in Nodes([], prefilter='inactive'):
            LOG.with_fields({
                'node': n.fqdn}).info('Node is absent for blob replication')
            absent_nodes.append(n.fqdn)
        LOG.info('Found %d inactive nodes' % len(absent_nodes))

        # We count fetches currently requested (or under way) as having completed
        # in order to stop over-replication for large blobs.
        current_fetches = etcd.get_current_blob_transfers(
            absent_nodes=absent_nodes)

        for b in Blobs([], prefilter='active'):
            instances = instance.instance_usage_for_blob_uuid(b.uuid)
            if instances:
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

                for instance_uuid in instances:
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
            b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
            if b:
                b.record_usage()
        self.lock.refresh()

        # Find expired blobs
        for b in Blobs([], prefilter='active'):
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
        for b in Blobs([], prefilter='active'):
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
                    n.add_event(
                        EVENT_TYPE_AUDIT, 'deleting instance as hosting node as been deleted',
                        extra={'instance_uuid': i.uuid})
                    i.add_event(
                        EVENT_TYPE_AUDIT, 'deleting instance as hosting node as been deleted')
                    i.delete(global_only=True)

                    # Cleanup the instance's interfaces
                    for ni in networkinterface.interfaces_for_instance(i):
                        ni.delete()

                # Cleanup any blob locations
                for b in Blobs([partial(placement_filter, n.fqdn)], prefilter='active'):
                    n.add_event(
                        EVENT_TYPE_AUDIT,
                        'deleting blob location as hosting node has been deleted',
                        extra={'blob_uuid': b.uuid})
                    b.add_event(
                        EVENT_TYPE_AUDIT,
                        'deleting blob location as hosting node has been deleted',
                        extra={'node_uuid': n.uuid})
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

        # Remove old entries from the hard-deleted state caches
        for object_type in OBJECT_NAMES_TO_ITERATORS:
            with etcd.get_lock('cache', None, object_type, op='Hard deleted prune'):
                hd = etcd.get('cache', object_type, 'hard-deleted')
                if hd:
                    for obj in list(hd.keys()):
                        if time.time() - hd[obj] > 7 * 3600 * 24:
                            del hd[obj]
                    etcd.put('cache', object_type, 'hard-deleted', hd)

        # And we're done
        LOG.info('Cluster maintenance loop complete')

    def refresh_object_state_caches(self):
        for object_type in OBJECT_NAMES_TO_ITERATORS:
            with etcd.get_lock('cache', None, object_type, op='Cache refresh'):
                by_state = {
                    '_all_': {},
                    'deleted': {}
                }

                for state in OBJECT_NAMES_TO_CLASSES[object_type].state_targets:
                    if state:
                        by_state[state] = {}

                for obj in OBJECT_NAMES_TO_ITERATORS[object_type]([]):
                    if obj.state.value:
                        by_state[obj.state.value][obj.uuid] = time.time()
                        by_state['_all_'][obj.uuid] = time.time()

                for state in by_state:
                    cache.clobber_object_state_cache(
                        object_type, state, by_state[state])

    def run(self):
        LOG.info('Starting')

        self.refresh_object_state_caches()

        last_loop_run = 0
        while not self.exit.is_set():
            setproctitle.setproctitle(daemon.process_name('cluster') + ' idle')
            self._await_election()

            # Infrequently ensure we have no blobs with a reference count of zero
            orphan_blobs = []
            for b in Blobs([], prefilter='active'):
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
        LOG.info('Terminated')
