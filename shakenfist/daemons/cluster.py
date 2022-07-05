# The cluster daemon is for cluster level maintenance tasks which are not
# urgent. Hard deleting data for example. Its therefore pretty relaxed about
# obtaining the lock to do work et cetera. There is only one active cluster
# maintenance daemon per cluster.

from collections import defaultdict
import setproctitle
import time

from shakenfist import artifact
from shakenfist.baseobject import (DatabaseBackedObject as dbo,
                                   active_states_filter)
from shakenfist.baseobjectmapping import OBJECT_NAMES_TO_CLASSES
from shakenfist import blob
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import logutil
from shakenfist import network
from shakenfist.node import (
    Node, Nodes,
    active_states_filter as node_active_states_filter,
    inactive_states_filter as node_inactive_states_filter,
    nodes_by_free_disk_descending)
from shakenfist.upload import Uploads


LOG, _ = logutil.setup(__name__)


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
            self.lock = etcd.get_lock('cluster', None, None, ttl=900, timeout=10,
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
            with etcd.ThreadLocalReadOnlyCache():
                for b in blob.Blobs([active_states_filter]):
                    for node in b.locations:
                        per_node[node].append(b.uuid)

            for node in Nodes([]):
                node.blobs = per_node.get(node.uuid, [])

        # Cleanup soft deleted objects
        for objtype in OBJECT_NAMES_TO_CLASSES:
            for _, objdata in etcd.get_all(objtype, None):
                try:
                    obj = OBJECT_NAMES_TO_CLASSES[objtype].from_db(
                        objdata['uuid'])
                    if (obj.state.value == dbo.STATE_DELETED and
                            time.time() - obj.state.update_time > config.CLEANER_DELAY):
                        obj.hard_delete()
                except exceptions.BadObjectVersion:
                    LOG.with_fields({
                        objtype: objdata['uuid']
                    }).warning('Could not load object for hard delete, bad version')

        # Cleanup vxids which specify a missing network
        for k, objdata in etcd.get_all('vxlan', None):
            network_uuid = objdata.get('network_uuid')
            if network_uuid:
                n = network.Network.from_db(network_uuid)
                if not n:
                    etcd.WrappedEtcdClient().delete(k)
                    LOG.with_fields({
                        'network': network_uuid,
                        'vxid record': k
                    }).warning('Cleaning up leaked vxlan')

        # Cleanup old uploads which were never completed
        for upload in Uploads([]):
            if time.time() - upload.state.update_time > 7 * 24 * 3600:
                LOG.with_fields({
                    'upload': upload.uuid
                }).warning('Cleaning up stale upload')
                upload.hard_delete()

        # Prune artifacts which might have too many versions
        for a in artifact.Artifacts([]):
            a.delete_old_versions()

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

        in_use_blobs = []
        with etcd.ThreadLocalReadOnlyCache():
            for b in blob.Blobs([active_states_filter]):
                if b.instances:
                    in_use_blobs.append(b)

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
        for b in in_use_blobs:
            b.record_usage()

        # Prune over replicated blobs
        for blob_uuid in overreplicated:
            b = blob.Blob.from_db(blob_uuid)
            for node in overreplicated[blob_uuid]:
                LOG.with_fields({
                    'blob': b,
                    'node': node
                }).info('Blob over replicated, removing from node with no users')
                b.drop_node_location(node)

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

            b = blob.Blob.from_db(blob_uuid)
            LOG.with_fields({
                'blob': b
            }).info('Blob under replicated, attempting to correct')
            b.request_replication(allow_excess=excess)
            current_fetches[blob_uuid].append('unknown')

        # Find transcodes of not recently used blobs and reap them
        old_transcodes = []
        with etcd.ThreadLocalReadOnlyCache():
            for b in blob.Blobs([active_states_filter]):
                if not b.transcoded:
                    continue

                if time.time() - b.last_used > config.BLOB_TRANSCODE_MAXIMUM_IDLE_TIME:
                    old_transcodes.append((b.uuid, b.transcoded))

        for blob_uuid, transcodes in old_transcodes:
            b = blob.Blob.from_db(blob_uuid)
            b.remove_transcodes()

            for transcode in transcodes:
                tb = blob.Blob.from_db(transcodes[transcode])
                tb.ref_count_dec()

        # Node management
        for n in Nodes([node_inactive_states_filter]):
            age = time.time() - n.last_seen

            # Find nodes which have returned from being missing
            if age < config.NODE_CHECKIN_MAXIMUM:
                n.state = Node.STATE_CREATED
                LOG.with_object(n).info('Node returned from being missing')

            # Find nodes which have been offline for a long time, unless
            # this machine has been asleep for a long time (think developer
            # laptop).
            if (time.time() - last_loop_run < config.NODE_CHECKIN_MAXIMUM
                    and age > config.NODE_CHECKIN_MAXIMUM * 10):
                n.state = Node.STATE_ERROR
                for i in instance.healthy_instances_on_node(n):
                    LOG.with_object(i).with_object(n).info(
                        'Node in error state, erroring instance')
                    # Note, this queue job is just in case the node comes
                    # back.
                    i.enqueue_delete_due_error('Node in error state')

        # Find nodes which haven't checked in recently
        for n in Nodes([node_active_states_filter]):
            age = time.time() - n.last_seen
            if age > config.NODE_CHECKIN_MAXIMUM:
                n.state = Node.STATE_MISSING

        # And we're done
        LOG.info('Cluster maintenance loop complete')

    def run(self):
        LOG.info('Starting')

        last_loop_run = 0
        while not self.exit.is_set():
            setproctitle.setproctitle(daemon.process_name('cluster') + ' idle')
            self._await_election()

            if self.is_elected and not self.exit.is_set():
                setproctitle.setproctitle(
                    daemon.process_name('cluster') + ' active')
                self.lock.refresh()
                self._cluster_wide_cleanup(last_loop_run)
                last_loop_run = time.time()
                self.lock.refresh()
                self.exit.wait(60)
