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
from shakenfist import blob
from shakenfist.config import config
from shakenfist.constants import OBJECT_NAMES
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import logutil
from shakenfist import net
from shakenfist import networkinterface
from shakenfist.node import (
    Node, Nodes,
    active_states_filter as node_active_states_filter,
    inactive_states_filter as node_inactive_states_filter,
    nodes_by_free_disk_descending)
from shakenfist.tasks import FetchBlobTask


LOG, _ = logutil.setup(__name__)
OBJECT_NAMES_TO_CLASSES = {
    'artifact': artifact.Artifact,
    'blob': blob.Blob,
    'instance': instance.Instance,
    'network': net.Network,
    'networkinterface': networkinterface.NetworkInterface
}


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super(Monitor, self).__init__(name)
        self.lock = None
        self.is_elected = False

    def _await_election(self):
        # Attempt to acquire the cluster maintenance lock forever. We never
        # release the lock, it gets cleared on a crash. This is so that only
        # one node at a time is performing cluster maintenance.
        while self.running:
            self.lock = etcd.get_lock('cluster', None, None, ttl=900, timeout=10,
                                      op='Cluster maintenance')
            result = self.lock.acquire()
            if result:
                self.is_elected = True
                return
            time.sleep(10)

    def _cluster_wide_cleanup(self, last_loop_run):
        LOG.info('Running cluster maintenance')

        # Cleanup soft deleted objects
        for objtype in OBJECT_NAMES:
            for _, objdata in etcd.get_all(objtype, None):
                try:
                    obj = OBJECT_NAMES_TO_CLASSES[objtype].from_db(
                        objdata['uuid'])
                    if (obj.state.value == dbo.STATE_DELETED and
                            time.time() - obj.state.update_time > config.CLEANER_DELAY):
                        LOG.with_object(obj).info('Hard deleting')
                        obj.hard_delete()
                except exceptions.BadObjectVersion:
                    LOG.with_fields({
                        objtype: obj.uuid
                    }).warning('Could not load object for hard delete, bad version')

        # Prune artifacts which might have too many versions
        for a in artifact.Artifacts([]):
            a.delete_old_versions()

        # Inspect current state of blobs, the actual changes are done below outside
        # the read only cache.
        overreplicated = {}
        underreplicated = []
        low_disk_nodes = nodes_by_free_disk_descending(
            minimum=0, maximum=config.MINIMUM_FREE_DISK,
            intention='blobs')

        absent_nodes = []
        for n in Nodes([node_inactive_states_filter]):
            LOG.with_fields({
                'node': n.fqdn}).info('Node is absent for blob replication')
            absent_nodes.append(n.fqdn)
        LOG.info('Found %d inactive nodes' % len(absent_nodes))

        current_fetches = defaultdict(list)
        for workname, workitem in etcd.get_outstanding_jobs():
            # A workname looks like: /sf/queue/sf-3/jobname
            _, _, phase, node, _ = workname.split('/')
            if node == 'networknode':
                continue

            for task in workitem:
                if isinstance(task, FetchBlobTask):
                    if node in absent_nodes:
                        LOG.with_fields({
                            'blob': task.blob_uuid,
                            'node': node,
                            'phase': phase
                        }).warning('Node is absent, ignoring fetch')
                    else:
                        LOG.with_fields({
                            'blob': task.blob_uuid,
                            'node': node,
                            'phase': phase
                        }).info('Node is fetching blob')
                        current_fetches[task.blob_uuid].append(node)

        with etcd.ThreadLocalReadOnlyCache():
            for b in blob.Blobs([active_states_filter]):
                # If there is current work for a blob, we ignore it until that
                # work completes
                if b.uuid in current_fetches:
                    LOG.with_fields({
                        'blob': task.blob_uuid
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

        # Prune over replicated blobs
        for blob_uuid in overreplicated:
            b = blob.Blob.from_db(blob_uuid)
            for node in overreplicated[blob_uuid]:
                LOG.with_fields({
                    'blob': b,
                    'node': node
                }).info('Blob over replicated, removing from node with no users')
                b.drop_node_location(node)

        # Replicate under replicated blobs
        for blob_uuid, excess in underreplicated:
            b = blob.Blob.from_db(blob_uuid)
            LOG.with_fields({
                'blob': b
            }).info('Blob under replicated, attempting to correct')
            b.request_replication(allow_excess=excess)

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
        while self.running:
            setproctitle.setproctitle(daemon.process_name('cluster') + ' idle')
            self._await_election()

            if self.is_elected and self.running:
                setproctitle.setproctitle(
                    daemon.process_name('cluster') + ' active')
                self.lock.refresh()
                self._cluster_wide_cleanup(last_loop_run)
                last_loop_run = time.time()
                self.lock.refresh()
                time.sleep(10)
