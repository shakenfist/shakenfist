# The cluster daemon is for cluster level maintenance tasks which are not
# urgent. Hard deleting data for example. Its therefore pretty relaxed about
# obtaining the lock to do work et cetera. There is only one active cluster
# maintenance daemon per cluster.

import setproctitle
import time

from shakenfist import artifact
from shakenfist.config import config
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


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super(Monitor, self).__init__(name)
        self.lock = None
        self.is_elected = False

    def _await_election(self):
        # Attempt to acquire the cluster maintenance lock forever. We never
        # release the lock, it gets cleared on a crash. This is so that only
        # one node at a time is performing cluster maintenance.
        while True:
            self.lock = etcd.get_lock('cluster', None, None, ttl=900, timeout=10,
                                      op='Cluster maintenance')
            result = self.lock.acquire()
            if result:
                self.is_elected = True
                return
            time.sleep(300)

    def _cluster_wide_cleanup(self, last_loop_run):
        # Cleanup soft deleted instances and networks
        for i in instance.inactive_instances():
            LOG.with_object(i).info('Hard deleting instance')
            i.hard_delete()

        for n in net.inactive_networks():
            LOG.with_network(n).info('Hard deleting network')
            n.hard_delete()

        for ni in networkinterface.inactive_network_interfaces():
            LOG.with_networkinterface(
                ni).info('Hard deleting network interface')
            ni.hard_delete()

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
        setproctitle.setproctitle(daemon.process_name('cluster') + ' idle')
        self._await_election()
        setproctitle.setproctitle(daemon.process_name('cluster') + ' active')

        last_loop_run = 0
        while True:
            self.lock.refresh()
            self._cluster_wide_cleanup(last_loop_run)
            last_loop_run = time.time()
            self.lock.refresh()
            time.sleep(300)
