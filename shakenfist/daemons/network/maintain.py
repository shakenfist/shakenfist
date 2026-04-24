from collections import defaultdict
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.daemons import daemon
from shakenfist.exceptions import CreateVXLANInterfaceFailed
from shakenfist.exceptions import DeadNetwork
from shakenfist.exceptions import LockException
from shakenfist.exceptions import ProcessExecutionError
from shakenfist import instance
from shakenfist.network import network
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.network import interface
from shakenfist.schema.operations.baseclusteroperation \
    import PRIORITY
from shakenfist.schema.operations.net_op \
    import create_and_enqueue as net_create_and_enqueue
from shakenfist.schema.operations.net_op \
    import model_tasks as net_tasks
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


EXTRA_VLANS_HISTORY = {}


class Job(util_concurrency.Job):
    def __init__(self, name):
        super().__init__()
        self.name = name

        self.abort_path = f'/run/sf/net-{name}.abort'
        daemon.clear_abort_path(self.abort_path)

    def execute(self):
        LOG.info('Starting network maintenance')
        last_loop = 0

        while daemon.check_abort_path(self.abort_path):
            if time.time() - last_loop < 30:
                time.sleep(1)
                continue

            last_loop = time.time()
            LOG.info('Maintaining existing networks')

            # Discover what networks are present
            _, _, vxid_to_mac = util_network.discover_interfaces()

            # Determine what networks we should be on
            host_networks = []
            seen_vxids = []

            if not config.NODE_IS_NETWORK_NODE:
                # For normal nodes, just the ones we have instances for. We need
                # to use the more expensive interfaces_for_instance() method of
                # looking up instance interfaces here if the instance cache hasn't
                # been populated yet (i.e. the instance is still being created)
                for inst in instance.Instances([instance.this_node_filter],
                                               prefilter='healthy'):
                    # Is the instance built yet?
                    if inst.state.value in [dbo.STATE_INITIAL,
                                            instance.Instance.STATE_PREFLIGHT,
                                            dbo.STATE_CREATING]:
                        continue

                    ifaces = inst.interfaces
                    if not ifaces:
                        ifaces = list(
                            interface.interfaces_for_instance(inst))

                    for iface_uuid in ifaces:
                        ni = interface.NetworkInterface.from_db(
                            iface_uuid, suppress_failure_audit=True)
                        if not ni:
                            LOG.with_fields({
                                'instance': inst,
                                'interface': iface_uuid
                            }).error('Network interface does not exist')
                        elif ni.network_uuid not in host_networks:
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
                try:
                    n = network.Network.from_db(
                        network_uuid, suppress_failure_audit=True)
                    if not n:
                        continue

                    # If this network is in state delete_wait, then we should remove
                    # it if it has no interfaces left.
                    if n.state.value == dbo.STATE_DELETE_WAIT:
                        if not n.networkinterfaces:
                            LOG.with_fields({'network': n}).info(
                                'Removing stray delete_wait network')
                            op_type, op_uuid = net_create_and_enqueue(
                                n.uuid,
                                [net_tasks.network_destroy],
                                PRIORITY.user_facing)
                            try:
                                n.set_last_cluster_operation(op_type, op_uuid)
                            except RuntimeError:
                                pass  # Cleanup must proceed

                        # We skip maintenance on all delete_wait networks
                        continue

                    # Track what vxlan ids we've seen
                    seen_vxids.append(n.vxid)

                    if time.time() - n.state.update_time < 60:
                        # Network state changed in the last minute, punt for now
                        continue

                    if not n.is_okay():
                        if config.NODE_IS_NETWORK_NODE:
                            n.add_event(
                                EVENT_TYPE_STATUS,
                                'Recreating not okay network on network node')
                            n.create_on_network_node()

                            # If the network node was missing a network, then that implies
                            # that we also need to re-create all of the floating IPs for
                            # that network.
                            for ni in n.networkinterfaces:
                                floating_addr = ni.floating.get(
                                    'floating_address')
                                if floating_addr:
                                    n.add_floating_ip(
                                        floating_addr, ni.ipv4,
                                        [ni, ('instance', ni.instance_uuid)])

                            # It also implies we should create all the routed IPs
                            # for that network too.
                            if n.uuid in routed_by_network:
                                for addr in routed_by_network[n.uuid]:
                                    n.route_address(addr)

                        else:
                            n.add_event(
                                EVENT_TYPE_STATUS,
                                'recreating not okay network on hypervisor')
                            n.create_on_hypervisor()

                    n.ensure_mesh()

                except CreateVXLANInterfaceFailed:
                    LOG.with_fields({'network': n}).warning(
                        'Failed to create VXLAN interface during '
                        'network maintenance, will retry')
                except LockException as e:
                    LOG.warning(
                        'Failed to acquire lock while maintaining networks: %s' % e)
                except DeadNetwork as e:
                    LOG.with_fields({'exception': e}).info(
                        'maintain_network attempted on dead network')
                except ProcessExecutionError as e:
                    LOG.error('Network maintenance failure: %s', e)

            # Determine if there are any extra vxids
            extra_vxids = set(vxid_to_mac.keys()) - set(seen_vxids)

            # We keep a global cache of extra vxlans we've seen before, so that
            # we only warn about them when they've been stray for five minutes.
            for vxid in EXTRA_VLANS_HISTORY.copy():
                if vxid not in extra_vxids:
                    del EXTRA_VLANS_HISTORY[vxid]
            for vxid in extra_vxids:
                if vxid not in EXTRA_VLANS_HISTORY:
                    EXTRA_VLANS_HISTORY[vxid] = time.time()

            # Warn of extra vxlans which have been present for more than five minutes
            for vxid in EXTRA_VLANS_HISTORY:
                if time.time() - EXTRA_VLANS_HISTORY[vxid] > 5 * 60:
                    LOG.with_fields({'vxid': vxid}).warning(
                        'Extra vxlan present!')
