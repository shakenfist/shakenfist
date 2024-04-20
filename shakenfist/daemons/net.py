from collections import defaultdict
import itertools
import os
from oslo_concurrency import processutils
import setproctitle
from shakenfist_utilities import logs
import signal
import time

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import ipam
from shakenfist import network
from shakenfist import networkinterface
from shakenfist.networkinterface import NetworkInterface
from shakenfist.tasks import (
    DeployNetworkTask,
    DestroyNetworkTask,
    NetworkTask,
    RemoveDHCPNetworkTask,
    RemoveNATNetworkTask,
    UpdateDHCPNetworkTask,
    RemoveDHCPLeaseNetworkTask,
    NetworkInterfaceTask,
    FloatNetworkInterfaceTask,
    DefloatNetworkInterfaceTask,
    RouteAddressTask, UnrouteAddressTask)
from shakenfist.util import general as util_general
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)

EXTRA_VLANS_HISTORY = {}


class Monitor(daemon.WorkerPoolDaemon):
    def _remove_stray_interfaces(self):
        last_loop = 0

        while not self.exit.is_set():
            if time.time() - last_loop < 30:
                time.sleep(1)
                continue

            last_loop = time.time()
            LOG.info('Scanning for stray network interfaces')
            for n in network.Networks([], prefilter='active'):
                try:
                    t = time.time()
                    for ni_uuid in n.networkinterfaces:
                        ni = NetworkInterface.from_db(ni_uuid)
                        if not ni:
                            continue

                        inst = instance.Instance.from_db(ni.instance_uuid)
                        if not inst:
                            ni.delete()
                            LOG.with_fields({
                                'networkinterface': ni,
                                'instance': ni.instance_uuid}).info(
                                'Deleted stray network interface for missing instance')
                        else:
                            s = inst.state
                            if (s.update_time + 30 < t and
                                    s.value in [dbo.STATE_DELETED, dbo.STATE_ERROR, 'unknown']):
                                ni.delete()
                                LOG.with_fields({
                                    'networkinterface': ni,
                                    'instance': ni.instance_uuid}).info(
                                    'Deleted stray network interface')

                except exceptions.LockException:
                    pass

    def _maintain_networks(self):
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
                        ni = networkinterface.NetworkInterface.from_db(iface_uuid)
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
                    n = network.Network.from_db(network_uuid)
                    if not n:
                        continue

                    # If this network is in state delete_wait, then we should remove
                    # it if it has no interfaces left.
                    if n.state.value == dbo.STATE_DELETE_WAIT:
                        if not n.networkinterfaces:
                            LOG.with_fields({'network': n}).info(
                                'Removing stray delete_wait network')
                            etcd.enqueue('networknode', DestroyNetworkTask(n.uuid))

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

                except exceptions.LockException as e:
                    LOG.warning(
                        'Failed to acquire lock while maintaining networks: %s' % e)
                except exceptions.DeadNetwork as e:
                    LOG.with_fields({'exception': e}).info(
                        'maintain_network attempted on dead network')
                except processutils.ProcessExecutionError as e:
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
                    LOG.with_fields({'vxid': vxid}).warning('Extra vxlan present!')

    def _process_network_workitem(self, log_ctx, workitem):
        log_ctx = log_ctx.with_fields({'network': workitem.network_uuid()})
        n = network.Network.from_db(workitem.network_uuid())
        if not n:
            log_ctx.warning('Received work item for non-existent network')
            return

        # NOTE(mikal): there's really nothing stopping us from processing a bunch
        # of these jobs in parallel with a pool of workers, but I am not sure its
        # worth the complexity right now. Are we really going to be changing
        # networks that much?

        #
        # Tasks valid for a network in ANY STATE
        #
        if isinstance(workitem, RemoveDHCPNetworkTask):
            n.remove_dhcp()
            return

        if isinstance(workitem, RemoveNATNetworkTask):
            n.remove_nat()
            return

        if isinstance(workitem, UnrouteAddressTask):
            n.unroute_address(workitem.ipv4())

        #
        # Tasks that should NOT operate on a DEAD network
        #
        if n.is_dead() and n.state.value != network.Network.STATE_DELETE_WAIT:
            log_ctx.with_fields({'state': n.state,
                                 'workitem': workitem}).info(
                'Received work item for a dead network and not delete_wait')
            return

        if isinstance(workitem, DestroyNetworkTask):
            if n.networkinterfaces:
                log_ctx.with_fields(
                    {'networkinterfaces': n.networkinterfaces}).info(
                    'DestroyNetworkTask for network with interfaces, deferring.')
                etcd.enqueue('networknode', workitem, delay=60)
                return

            try:
                n.delete_on_network_node()
            except exceptions.DeadNetwork as e:
                log_ctx.with_fields({'exception': e}).warning(
                    'DestroyNetworkTask on dead network')
            return

        #
        # Tasks that should NOT operate on a DEAD or DELETE_WAIT network
        #
        if n.is_dead():
            log_ctx.with_fields({'state': n.state,
                                 'workitem': workitem}).info(
                'Received work item for a dead network')
            return

        try:
            if isinstance(workitem, DeployNetworkTask):
                n.create_on_network_node()
                n.ensure_mesh()

            elif isinstance(workitem, UpdateDHCPNetworkTask):
                n.create_on_network_node()
                n.ensure_mesh()

            elif isinstance(workitem, RemoveDHCPLeaseNetworkTask):
                n.remove_dhcp_lease(workitem.ipv4(), workitem.macaddr())

            elif isinstance(workitem, RouteAddressTask):
                n.route_address(workitem.ipv4())

        except exceptions.DeadNetwork as e:
            log_ctx.with_fields({'exception': e}).warning(
                'Network task on dead network')

    def _process_networkinterface_workitem(self, log_ctx, workitem):
        log_ctx = log_ctx.with_fields({
            'networkinterface': workitem.interface_uuid()})
        n = network.Network.from_db(workitem.network_uuid())
        if not n:
            log_ctx.warning('Received work item for non-existent network')
            return

        ni = NetworkInterface.from_db(workitem.interface_uuid())
        if not ni:
            log_ctx.warning(
                'Received work item for non-existent network interface')
            return

        # Tasks that should not operate on a dead or delete waiting network
        if n.is_dead() and n.state.value != network.Network.STATE_DELETE_WAIT:
            log_ctx.with_fields({'state': n.state,
                                 'workitem': workitem}).info(
                'Received work item for a completely dead network')
            return

        if isinstance(workitem, DefloatNetworkInterfaceTask):
            floating = ni.floating.get('floating_address')
            if not floating:
                self.log.warning(
                    'Not defloating an interface with no floating address')
            else:
                n.remove_floating_ip(floating, ni.ipv4)
                fn = network.floating_network()
                fn.ipam.release(ni.floating.get('floating_address'))
                ni.floating = None
            return

        # Tasks that should not operate on a dead network
        if n.is_dead():
            log_ctx.with_fields({'state': n.state,
                                 'workitem': workitem}).info(
                'Received work item for a dead network')
            return

        if isinstance(workitem, FloatNetworkInterfaceTask):
            floating = ni.floating.get('floating_address')
            if not floating:
                self.log.warning(
                    'Not floating an interface with no floating address')
            else:
                n.add_floating_ip(floating, ni.ipv4)
            return

    def _process_network_node_workitems(self):
        while not self.exit.is_set():
            jobname_workitem = etcd.dequeue('networknode')
            if not jobname_workitem:
                time.sleep(0.2)

            else:
                jobname, workitem = jobname_workitem
                setproctitle.setproctitle(
                    '%s-%s' % (daemon.process_name('net'), jobname))

                try:
                    log_ctx = LOG.with_fields({'workitem': workitem})
                    log_ctx.info('Starting work item')

                    if NetworkTask.__subclasscheck__(type(workitem)):
                        self._process_network_workitem(log_ctx, workitem)
                    elif NetworkInterfaceTask.__subclasscheck__(type(workitem)):
                        self._process_networkinterface_workitem(
                            log_ctx, workitem)
                    else:
                        raise exceptions.UnknownTaskException(
                            'Network workitem was not decoded: %s' % workitem)

                finally:
                    etcd.resolve('networknode', jobname)

                setproctitle.setproctitle('%s-idle' % daemon.process_name('net'))

    def _reap_leaked_floating_ips(self):
        last_loop = 0

        while not self.exit.is_set():
            if time.time() - last_loop < 30:
                time.sleep(1)
                continue

            last_loop = time.time()

            # Ensure we haven't leaked any floating IPs (because we used to). We
            # have to hold a lock here to avoid races where an IP is freed while
            # we're iterating through the loop. Note that this means we can't call
            # anything which also wants to lock the ipmanager.
            with etcd.get_lock('ipmanager', None, 'floating', ttl=120,
                               op='Cleanup leaks'):
                floating_network = network.floating_network()
                LOG.debug('Floating network registrations: %s'
                          % floating_network.ipam.in_use)

                # Collect floating gateways and floating IPs, while ensuring that
                # they are correctly reserved on the floating network as well.
                floating_gateways = []
                for n in network.Networks([], prefilter='active'):
                    fg = n.floating_gateway
                    if fg:
                        floating_gateways.append(fg)
                        if floating_network.ipam.is_free(fg):
                            floating_network.ipam.reserve(
                                fg, n.unique_label(), ipam.RESERVATION_TYPE_GATEWAY,
                                'Rescued from incorrect registration')
                            LOG.with_fields({
                                'network': n.uuid,
                                'address': fg
                            }).error('Floating gateway not reserved correctly')
                LOG.info('Found floating gateways: %s' % floating_gateways)

                floating_addresses = []
                for ni in networkinterface.NetworkInterfaces([], prefilter='active'):
                    fa = ni.floating.get('floating_address')
                    if fa:
                        floating_addresses.append(fa)
                        if floating_network.ipam.is_free(fa):
                            floating_network.ipam.reserve(
                                fg, n.unique_label(), ipam.RESERVATION_TYPE_FLOATING,
                                'Rescued from incorrect registration')
                            LOG.with_fields({
                                'networkinterface': ni.uuid,
                                'address': fa
                            }).error('Floating address not reserved correctly')
                LOG.info('Found floating addresses: %s' % floating_addresses)

                floating_routed = []
                for addr in floating_network.ipam.in_use:
                    reservation = floating_network.ipam.get_reservation(addr)
                    if not reservation:
                        continue
                    if reservation.get('type') != ipam.RESERVATION_TYPE_ROUTED:
                        continue
                    user_type, user_uuid = reservation['user']
                    if user_type != 'network':
                        LOG.with_fields(reservation).error(
                            'Objects of type %s should not be routing floating IPs!'
                            % user_type)
                        continue

                    n = network.Network.from_db(user_uuid)
                    if not n:
                        LOG.with_fields(reservation).error(
                            'Routed IP reserved by missing network')
                        continue

                    floating_routed.append(addr)
                LOG.info('Found routed addresses: %s' % floating_routed)

                floating_reserved = [
                    floating_network.ipam.get_address_at_index(0),
                    floating_network.ipam.get_address_at_index(1),
                    floating_network.ipam.broadcast_address,
                    floating_network.ipam.network_address
                ]
                LOG.info('Found floating reservations: %s' % floating_reserved)

                floating_halo = list(floating_network.ipam.get_haloed_addresses())
                LOG.info('Found floating deletion halos: %s' % floating_halo)

                # Now the reverse check. Test if there are any reserved IPs which
                # are not actually in use. Free any we find.
                leaks = []
                for ip in floating_network.ipam.in_use:
                    if ip not in itertools.chain(floating_gateways,
                                                 floating_addresses,
                                                 floating_routed,
                                                 floating_reserved,
                                                 floating_halo):
                        # This IP needs to have been allocated more than 300 seconds
                        # ago to ensure that the network setup isn't still queued.
                        if time.time() - floating_network.ipam.get_allocation_age(ip) > 300:
                            LOG.error('Floating IP %s has leaked.' % ip)
                            leaks.append(ip)

                for ip in leaks:
                    LOG.error('Leaked floating IP %s has been released.' % ip)
                    floating_network.ipam.release(ip)

    def _validate_mtus(self):
        last_loop = 0

        while not self.exit.is_set():
            if time.time() - last_loop < 30:
                time.sleep(1)
                continue

            last_loop = time.time()
            LOG.info('Validating network interface MTUs')

            by_mtu = defaultdict(list)
            for iface, mtu in util_network.get_interface_mtus():
                by_mtu[mtu].append(iface)

            for mtu in sorted(by_mtu):
                log = LOG.with_fields({
                    'mtu': mtu,
                    'interfaces': by_mtu[mtu]
                })
                if mtu < 1501:
                    log.warning('Interface MTU is 1500 bytes or less')
                else:
                    log.debug('Interface MTU is normal')

    def run(self):
        LOG.info('Starting')
        running = True
        shutdown_commenced = None

        network_worker = None
        stray_interface_worker = None
        maintain_networks_worker = None
        floating_ip_reap_worker = None
        mtu_validation_worker = None

        while True:
            try:
                self.reap_workers()

                if not self.exit.is_set():
                    worker_pids = []
                    for workername in self.workers:
                        worker_pids.append(self.workers[workername].pid)

                    if config.NODE_IS_NETWORK_NODE and network_worker not in worker_pids:
                        network_worker = self.start_workitem(
                            self._process_network_node_workitems, [], 'net-worker')

                    # Management tasks are treated as extra workers, and run in
                    # parallel with other network work items.
                    if stray_interface_worker not in worker_pids:
                        stray_interface_worker = self.start_workitem(
                            self._remove_stray_interfaces, [], 'stray-nics')

                    if maintain_networks_worker not in worker_pids:
                        maintain_networks_worker = self.start_workitem(
                            self._maintain_networks, [], 'maintain')

                    if mtu_validation_worker not in worker_pids:
                        mtu_validation_worker = self.start_workitem(
                            self._validate_mtus, [], 'mtus')

                    if config.NODE_IS_NETWORK_NODE:
                        if floating_ip_reap_worker not in worker_pids:
                            floating_ip_reap_worker = self.start_workitem(
                                self._reap_leaked_floating_ips, [], 'fip-reaper')

                elif len(self.workers) > 0:
                    if running:
                        shutdown_commenced = time.time()
                        for proc in self.workers:
                            try:
                                os.kill(self.workers[proc].pid, signal.SIGTERM)
                                LOG.info('Sent SIGTERM to %s (pid %s)'
                                         % (proc, self.workers[proc].pid))
                            except OSError as e:
                                LOG.warn('Failed to send SIGTERM to %s: %s'
                                         % (proc, e))

                        running = False

                    if time.time() - shutdown_commenced > 10:
                        LOG.warning('We have taken more than ten seconds to shut down')
                        LOG.warning('Dumping thread traces')
                        for proc in self.workers:
                            LOG.warning('%s daemon still running (pid %d)'
                                        % (proc, self.workers[proc].pid))
                            try:
                                os.kill(self.workers[proc].pid, signal.SIGUSR1)
                            except ProcessLookupError:
                                pass
                            except OSError as e:
                                LOG.warn('Failed to send SIGUSR1 to %s: %s'
                                         % (proc, e))

                else:
                    break

                self.exit.wait(1)

            except Exception as e:
                util_general.ignore_exception('network worker', e)

        LOG.info('Terminated')
