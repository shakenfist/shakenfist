from collections import defaultdict
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist import etcd
from shakenfist.exceptions import LockException
from shakenfist.exceptions import DeadNetwork
from shakenfist.exceptions import ProcessExecutionError
from shakenfist import instance
from shakenfist import ipam
from shakenfist import network
from shakenfist import networkinterface
from shakenfist.tasks import DestroyNetworkTask
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


EXTRA_VLANS_HISTORY = {}


class Job(util_concurrency.Job):
    def execute(self):
        LOG.info('Starting network maintenance')
        last_loop = 0

        while not self.exit.is_set():
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
                for inst in instance.Instances([instance.this_node_filter], prefilter='healthy'):
                    ifaces = inst.interfaces
                    if not ifaces:
                        ifaces = list(
                            networkinterface.interfaces_for_instance(inst))

                    for iface_uuid in ifaces:
                        ni = networkinterface.NetworkInterface.from_db(
                            iface_uuid)
                        if not ni:
                            LOG.with_fields({
                                'instance': inst,
                                'networkinterface': iface_uuid}).error(
                                    'Network interface does not exist')
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
            for addr in fn.ipam.in_use:
                resv = fn.ipam.get_reservation(addr)
                if resv and resv['type'] == ipam.RESERVATION_TYPE_ROUTED:
                    network_uuid = resv['user'][1]
                    routed_by_network[network_uuid].append(addr)

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
                            etcd.enqueue(
                                'networknode', DestroyNetworkTask(n.uuid))

                        # We skip maintenance on all delete_wait networks
                        continue

                    # Track what vxlan ids we've seen
                    seen_vxids.append(n.vxid)

                    if time.time() - n.state.update_time < 60:
                        # Network state changed in the last minute, punt for now
                        continue

                    if not n.is_okay():
                        if config.NODE_IS_NETWORK_NODE:
                            LOG.with_fields({'network': n}).info(
                                'Recreating not okay network on network node')
                            n.create_on_network_node()

                            # If the network node was missing a network, then that implies
                            # that we also need to re-create all of the floating IPs for
                            # that network.
                            for ni_uuid in n.networkinterfaces:
                                ni = networkinterface.NetworkInterface.from_db(
                                    ni_uuid)
                                if not ni:
                                    continue

                                if ni.floating.get('floating_address'):
                                    LOG.with_fields(
                                        {
                                            'instance': ni.instance_uuid,
                                            'networkinterface': ni.uuid,
                                            'floating': ni.floating.get('floating_address')
                                        }).info('Refloating interface')
                                    n.add_floating_ip(ni.floating.get(
                                        'floating_address'), ni.ipv4)

                            # It also implies we should create all the routed IPs
                            # for that network too.
                            if n.uuid in routed_by_network:
                                for addr in routed_by_network[n.uuid]:
                                    n.route_address(addr)

                        else:
                            LOG.with_fields({'network': n}).info(
                                'Recreating not okay network on hypervisor')
                            n.create_on_hypervisor()

                    n.ensure_mesh()

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
            global EXTRA_VLANS_HISTORY
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
