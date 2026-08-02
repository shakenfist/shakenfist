from collections import defaultdict
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.daemons import daemon
from shakenfist import instance
from shakenfist import mariadb
from shakenfist.network import network
from shakenfist.node import Node
from shakenfist.operations.baseoperation import get_all_network_queues
from shakenfist.operations.baseoperation import get_node_network_queues
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.operations.baseclusteroperation \
    import PRIORITY
from shakenfist.schema.operations.net_ip_op \
    import create_and_enqueue as net_ip_create_and_enqueue
from shakenfist.schema.operations.net_ip_op \
    import model_tasks as net_ip_tasks
from shakenfist.schema.operations.net_op \
    import create_and_enqueue as net_create_and_enqueue
from shakenfist.schema.operations.net_op \
    import model_tasks as net_tasks
from shakenfist.schema.operations.node_net_op \
    import create_and_enqueue as nn_create_and_enqueue
from shakenfist.schema.operations.node_net_op \
    import model_tasks as nn_tasks
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


EXTRA_VLANS_HISTORY = {}
EXTRA_VLANS_WARNED = set()


# Terminal cluster-operation states. Mirrors the set used by the
# net-worker dispatcher in shakenfist.daemons.network.workitem.
_TERMINAL_OP_STATES = {
    'complete',
    'abort',
    dbo.STATE_DELETED,
    dbo.STATE_ERROR,
}


class Job(util_concurrency.Job):
    def __init__(self, name):
        super().__init__()
        self.name = name

        self.abort_path = f'/run/sf/net-{name}.abort'
        daemon.clear_abort_path(self.abort_path)

    def _combined_network_queue_depth(self):
        """Return the summed processing+queued+deferred depth for the
        network queue family this node services. The queue list mirrors
        the worker's execute() in shakenfist.daemons.network.workitem so
        the guard observes exactly the queues that are actually draining
        on this node."""
        queue_names = list(get_node_network_queues(config.NODE_UUID))
        if config.NODE_IS_NETWORK_NODE:
            queue_names += get_all_network_queues()

        total = 0
        for queue_name in queue_names:
            processing, queued, deferred = mariadb.get_work_queue_length(
                queue_name)
            total += processing + queued + deferred
        return total

    def _local_instance_vxids(self):
        """The vxids of every network an instance on this node is attached to.

        ``host_networks`` in the main pass deliberately covers only
        instances which are built and healthy, because those are the
        ones whose networks we are obliged to maintain. Deciding whether
        a device may be *deleted* needs the wider set: an instance which
        is still building, or which has errored but whose domain may
        still be running, is entitled to its vxlan even though maintain
        does not reconcile its network.
        """
        network_uuids = set()
        for inst in instance.Instances([instance.this_node_filter],
                                       prefilter='active'):
            for ni in inst.interfaces:
                network_uuids.add(ni.network_uuid)

        vxids = set()
        for network_uuid in network_uuids:
            n = network.Network.from_db(
                network_uuid, suppress_failure_audit=True)
            if n:
                vxids.add(n.vxid)
        return vxids

    def _handle_stray_vxlans(self, overdue):
        """Reap or warn about vxlan devices which have been stray for
        longer than the grace period.

        A device is reapable when nothing on this node can legitimately
        be using it:

        * No networks row claims the vxid. The row is written before the
          device is ever created, so a device whose vxid has no row can
          never be a network under construction -- it is the residue of
          a network which has since been hard deleted. This holds on
          every node role.
        * Or, on a hypervisor only, a row exists but no instance on this
          node is attached to that network. Per-instance teardown
          deletes the device when the last instance on a host leaves a
          network (see node_inst_op), so a leftover device here means
          that cleanup was missed or failed. The network node is
          excluded because it carries a device for every active network
          whether or not it hosts instances.

        Anything else is left alone and warned about once per stray
        episode rather than on every pass.
        """
        # Re-check the networks table immediately before deleting
        # anything. We deliberately test for the presence of the static
        # row rather than the object state, so a network which is merely
        # soft deleted still protects its device; only a hard deleted
        # (row gone) network is reapable on this test.
        claimed_vxids = mariadb.find_network_vxids(overdue)

        # Only pay for the instance lookup if a claimed stray might be
        # reapable on this node.
        protected_vxids = set()
        if (not config.NODE_IS_NETWORK_NODE
                and claimed_vxids.intersection(overdue)):
            protected_vxids = self._local_instance_vxids()

        reapable = []
        for vxid in overdue:
            if vxid in claimed_vxids:
                if config.NODE_IS_NETWORK_NODE:
                    reason = 'network node hosts every active network'
                elif vxid in protected_vxids:
                    reason = 'an instance on this node is attached to it'
                else:
                    reapable.append(
                        (vxid, 'no instance on this node uses this network'))
                    continue

                if vxid not in EXTRA_VLANS_WARNED:
                    EXTRA_VLANS_WARNED.add(vxid)
                    LOG.with_fields({'vxid': vxid, 'reason': reason}).warning(
                        'Extra vxlan present!')
                continue

            reapable.append((vxid, 'no network claims this vxid'))

        if not reapable:
            return

        this_node = Node.from_db(config.NODE_NAME)
        for vxid, reason in reapable:
            # Every device Shaken Fist names from a vxid. The netns and
            # the NAT rules a network node also owns are keyed by network
            # uuid, not vxid, and are therefore unreachable once the row
            # is gone -- they are a known limitation of this reap.
            devices = ['br-vxlan-%06x' % vxid, 'vxlan-%06x' % vxid,
                       'veth-%06x-o' % vxid, 'egr-%06x-o' % vxid]

            try:
                for device in devices:
                    if util_network.check_for_interface(device):
                        util_concurrency.execute('ip link delete %s' % device)
            except Exception as e:
                # The net-worker on this node can be tearing down the
                # same devices, so a delete racing to "no such device"
                # is expected. Leave the vxid in the history so the next
                # pass retries, and do not let one bad device abort the
                # maintenance pass -- this code runs outside the
                # dispatcher, so nothing else would catch it.
                LOG.with_fields({'vxid': vxid}).warning(
                    'Failed to reap stray vxlan: %s' % e)
                continue

            if this_node:
                this_node.add_event(
                    EVENT_TYPE_AUDIT,
                    'reaped stray vxlan: %s' % reason,
                    extra={'vxid': vxid})
            else:
                # add_event() echoes into the log stream, so this only
                # fires when the event could not be recorded at all.
                LOG.with_fields({'vxid': vxid}).info(
                    'Reaped stray vxlan: %s' % reason)

            # Forget the stray so a reappearance gets a fresh grace
            # period, and a later network which is randomly allocated
            # this vxid can be warned about on its own merits.
            del EXTRA_VLANS_HISTORY[vxid]
            EXTRA_VLANS_WARNED.discard(vxid)

    def execute(self):
        LOG.info('Starting network maintenance')
        last_loop = 0

        while daemon.check_abort_path(self.abort_path):
            if time.time() - last_loop < 30:
                time.sleep(1)
                continue

            last_loop = time.time()
            LOG.info('Maintaining existing networks')

            # Queue-depth safety guard. If the network queue family is
            # already backed up, piling more reconciliation requests on
            # top of it is counterproductive. Skip the entire pass --
            # including the extra-vxlan check at the end, which is not
            # actionable when the queue is overloaded anyway.
            total_depth = self._combined_network_queue_depth()
            if total_depth > config.MAINTAIN_QUEUE_DEPTH_THRESHOLD:
                n = Node.from_db(config.NODE_NAME)
                if n:
                    n.add_event(
                        EVENT_TYPE_AUDIT,
                        'maintain pass skipped: combined network queue '
                        'depth %d exceeds threshold %d' % (
                            total_depth,
                            config.MAINTAIN_QUEUE_DEPTH_THRESHOLD))
                continue

            # Discover what networks are present
            _, _, vxid_to_mac = util_network.discover_interfaces()

            # Determine what networks we should be on
            host_networks = []
            seen_vxids = []

            if not config.NODE_IS_NETWORK_NODE:
                # For normal nodes, just the ones we have instances for.
                # ``inst.interfaces`` queries the network_interfaces table
                # live, so the instance-cache fallback that used to live
                # here is no longer needed.
                for inst in instance.Instances([instance.this_node_filter],
                                               prefilter='healthy'):
                    # Is the instance built yet?
                    if inst.state.value in [dbo.STATE_INITIAL,
                                            instance.Instance.STATE_PREFLIGHT,
                                            dbo.STATE_CREATING]:
                        continue

                    for ni in inst.interfaces:
                        if ni.network_uuid not in host_networks:
                            host_networks.append(ni.network_uuid)
            else:
                # For network nodes, its all networks
                for n in network.Networks([], prefilter='active'):
                    host_networks.append(n.uuid)

            # Determine what routed ips should exist for a given network. We do
            # this once to avoid doing it over and over below.
            routed_by_network = defaultdict(list)
            fn = network.floating_network()
            if fn:
                # fn should never be None, but we do in fact see it during
                # installation at the moment and I am not sure why. I suspect
                # a startup race.
                for addr in fn.ipam.in_use:
                    resv = fn.ipam.get_reservation(addr)
                    if resv and resv.reservation_type == ReservationType.ROUTED:
                        routed_by_network[resv.user_uuid].append(addr)

            # Ensure we are on every network we have a host for
            for network_uuid in host_networks:
                n = network.Network.from_db(
                    network_uuid, suppress_failure_audit=True)
                if not n:
                    continue

                # If this network is in state delete_wait, then we should
                # remove it if it has no interfaces left. The REST DELETE
                # handler already enqueues a delete op when transitioning a
                # network into DELETE_WAIT (see external_api/network.py),
                # so the per-target gate below avoids racing a duplicate op
                # against the in-flight one. This path therefore only fires
                # for genuinely orphaned DELETE_WAIT networks -- e.g. ones
                # left over from a crashed API request, or upgrades from
                # before the API began self-enqueuing.
                if n.state.value == dbo.STATE_DELETE_WAIT:
                    if (not n.networkinterfaces
                            and not mariadb.has_pending_cluster_operation_target(
                                target_object_type=ObjectType.NETWORK,
                                target_uuid=str(n.uuid))):
                        LOG.with_fields({'network': n}).info(
                            'Removing stray delete_wait network')
                        net_create_and_enqueue(
                            network_uuid=str(n.uuid),
                            tasks=[net_tasks.network_apply_delete_network_node],
                            priority=PRIORITY.background)

                    # We skip maintenance on all delete_wait networks
                    continue

                # Track what vxlan ids we've seen
                seen_vxids.append(n.vxid)

                if time.time() - n.state.update_time < 60:
                    # Network state changed in the last minute, punt for now
                    continue

                network_okay = n.is_okay()
                mesh_okay = n.is_mesh_okay() if network_okay else False
                if network_okay and mesh_okay:
                    # No drift detected for this network on this pass.
                    continue

                # Per-network gating. If a cluster operation targeting
                # this network is already in flight, skip this pass --
                # the in-flight op will fix the drift when it runs.
                if mariadb.has_pending_cluster_operation_target(
                        target_object_type=ObjectType.NETWORK,
                        target_uuid=str(n.uuid)):
                    continue

                # Cooldown. If the most recent terminal reconciliation
                # for this network ended in ERROR within the cooldown
                # window, let the previous failure breathe before
                # retrying.
                recent = mariadb.get_recent_terminal_op_states_for_target(
                    target_object_type=ObjectType.NETWORK,
                    target_uuid=str(n.uuid),
                    limit=1,
                    op_type='net_op')
                if recent:
                    _, state_value, update_time = recent[0]
                    if (state_value == dbo.STATE_ERROR
                            and update_time > time.time()
                            - config.MAINTAIN_RECONCILE_COOLDOWN_SECONDS):
                        n.add_event(
                            EVENT_TYPE_AUDIT,
                            'maintain pass skipped for network: recent '
                            'reconciliation error within cooldown window')
                        continue

                # Circuit breaker. If the last K terminal reconciliations
                # all ended in ERROR, quiesce this network until a fresh
                # reconciliation succeeds (operator intervention).
                circuit_k = config.MAINTAIN_RECONCILE_CIRCUIT_K
                history = mariadb.get_recent_terminal_op_states_for_target(
                    target_object_type=ObjectType.NETWORK,
                    target_uuid=str(n.uuid),
                    limit=circuit_k,
                    op_type='net_op')
                if (len(history) == circuit_k
                        and all(h[1] == dbo.STATE_ERROR for h in history)):
                    n.add_event(
                        EVENT_TYPE_AUDIT,
                        'network has failed reconciliation %d times in a '
                        'row; quiesced pending operator attention' % (
                            circuit_k))
                    continue

                # Drift remains and no guard fired.
                if network_okay:
                    # The network itself is fine; only the vxlan mesh
                    # has drifted. A full recreate is not needed --
                    # enqueue the targeted repair on this node's queue
                    # and move on.
                    n.add_event(
                        EVENT_TYPE_STATUS,
                        'Repairing drifted vxlan mesh on this node')
                    net_create_and_enqueue(
                        network_uuid=str(n.uuid),
                        tasks=[net_tasks.network_ensure_mesh],
                        priority=PRIORITY.background,
                        target=str(config.NODE_UUID),
                        family='network')
                    continue

                # Enqueue the appropriate reconciliation at background
                # priority and move on -- maintain does not wait for
                # completion.
                if config.NODE_IS_NETWORK_NODE:
                    n.add_event(
                        EVENT_TYPE_STATUS,
                        'Recreating not okay network on network node')
                    net_create_and_enqueue(
                        network_uuid=str(n.uuid),
                        tasks=[net_tasks.network_apply_create_network_node],
                        priority=PRIORITY.background)

                    # If the network node was missing a network, then that
                    # implies that we also need to re-create all of the
                    # floating IPs for that network.
                    for ni in n.networkinterfaces:
                        floating_addr = ni.floating.get('floating_address')
                        if floating_addr:
                            net_create_and_enqueue(
                                network_uuid=str(n.uuid),
                                tasks=[net_tasks.network_add_floating_ip],
                                priority=PRIORITY.background,
                                floating_address=floating_addr,
                                inner_address=ni.ipv4)

                    # It also implies we should create all the routed IPs
                    # for that network too.
                    if n.uuid in routed_by_network:
                        for addr in routed_by_network[n.uuid]:
                            net_ip_create_and_enqueue(
                                network_uuid=str(n.uuid),
                                ip=addr,
                                tasks=[net_ip_tasks.route_address],
                                priority=PRIORITY.background)
                else:
                    n.add_event(
                        EVENT_TYPE_STATUS,
                        'recreating not okay network on hypervisor')
                    nn_create_and_enqueue(
                        str(config.NODE_UUID), n.uuid,
                        [nn_tasks.network_apply_create_hypervisor],
                        PRIORITY.background)

                # Ensure the VXLAN mesh is up to date on this hypervisor.
                # Mesh updates are per-hypervisor, so this routes to the
                # per-node ``network`` family rather than the cluster-wide
                # networknode family.
                net_create_and_enqueue(
                    network_uuid=str(n.uuid),
                    tasks=[net_tasks.network_ensure_mesh],
                    priority=PRIORITY.background,
                    target=str(config.NODE_UUID),
                    family='network')

            # Determine if there are any extra vxids
            extra_vxids = set(vxid_to_mac.keys()) - set(seen_vxids)

            # We keep a global cache of extra vxlans we've seen before, so that
            # we only act on them once they've been stray for the whole grace
            # period.
            for vxid in EXTRA_VLANS_HISTORY.copy():
                if vxid not in extra_vxids:
                    del EXTRA_VLANS_HISTORY[vxid]
                    EXTRA_VLANS_WARNED.discard(vxid)
            for vxid in extra_vxids:
                if vxid not in EXTRA_VLANS_HISTORY:
                    EXTRA_VLANS_HISTORY[vxid] = time.time()

            # Act on extra vxlans which have been stray for longer than the
            # grace period.
            overdue = [
                vxid for vxid in EXTRA_VLANS_HISTORY
                if (time.time() - EXTRA_VLANS_HISTORY[vxid]
                    > config.MAINTAIN_STRAY_VXLAN_GRACE_SECONDS)]
            if overdue:
                self._handle_stray_vxlans(overdue)
