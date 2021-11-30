# The cluster daemon is for cluster level maintenance tasks which are not
# urgent. Hard deleting data for example. Its therefore pretty relaxed about
# obtaining the lock to do work et cetera. There is only one active cluster
# maintenance daemon per cluster.

import setproctitle
import time

from shakenfist import artifact
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import blob
from shakenfist.config import config
from shakenfist import constants
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import instance
from shakenfist import logutil
from shakenfist import net
from shakenfist import networkinterface
from shakenfist.node import (
    Node, Nodes,
    active_states_filter as node_active_states_filter,
    inactive_states_filter as node_inactive_states_filter)


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
        # Cleanup soft deleted objects
        for objtype in constants.OBJECT_NAMES:
            for _, objdata in etcd.get_all(objtype, None):
                obj = OBJECT_NAMES_TO_CLASSES[objtype].from_db(objdata['uuid'])
                if (obj.state.value == dbo.STATE_DELETED and
                        time.time() - obj.state.update_time > config.CLEANER_DELAY):
                    LOG.with_fields({
                        objtype: obj.uuid
                    }).info('Hard deleting')
                    obj.hard_delete()

        # Prune artifacts which might have too many versions
        for a in artifact.Artifacts([]):
            a.delete_old_versions()

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
