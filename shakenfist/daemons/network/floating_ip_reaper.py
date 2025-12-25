import time
import itertools

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist.network import network
from shakenfist.network import interface
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


class Job(util_concurrency.Job):
    def __init__(self, name):
        super().__init__()
        self.name = name

        self.abort_path = f'/run/sf/net-{name}.abort'
        daemon.clear_abort_path(self.abort_path)

    def execute(self):
        LOG.info('Starting floating IP reaper')
        last_loop = 0

        while daemon.check_abort_path(self.abort_path):
            if time.time() - last_loop < 30:
                time.sleep(1)
                continue

            last_loop = time.time()

            # Ensure we haven't leaked any floating IPs (because we used to).
            floating_network = network.floating_network()
            if not floating_network:
                return
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
                            fg, n.unique_label(), ReservationType.GATEWAY,
                            'Rescued from incorrect registration')
                        LOG.with_fields({
                            'network': n.uuid,
                            'address': fg
                        }).error('Floating gateway not reserved correctly')
            LOG.info('Found floating gateways: %s' % floating_gateways)

            floating_addresses = []
            for ni in interface.NetworkInterfaces([], prefilter='active'):
                fa = ni.floating.get('floating_address')
                if fa:
                    floating_addresses.append(fa)
                    if floating_network.ipam.is_free(fa):
                        floating_network.ipam.reserve(
                            fg, n.unique_label(), ReservationType.FLOATING,
                            'Rescued from incorrect registration')
                        LOG.with_fields({
                            'interface': ni.uuid,
                            'address': fa
                        }).error('Floating address not reserved correctly')
            LOG.info('Found floating addresses: %s' % floating_addresses)

            floating_routed = []
            for addr in floating_network.ipam.in_use:
                reservation = floating_network.ipam.get_reservation(addr)
                if not reservation:
                    continue
                if reservation.reservation_type != ReservationType.ROUTED:
                    continue
                if reservation.user_type != ObjectType.NETWORK:
                    LOG.with_fields({
                        'address': addr,
                        'user_type': reservation.user_type,
                        'user_uuid': reservation.user_uuid
                    }).error(
                        'Objects of type %s should not be routing floating IPs!'
                        % reservation.user_type)
                    continue

                n = network.Network.from_db(str(reservation.user_uuid))
                if not n:
                    LOG.with_fields({
                        'address': addr,
                        'user_uuid': reservation.user_uuid
                    }).error('Routed IP reserved by missing network')
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
                    if time.time() - floating_network.ipam.get_allocation_age(ip) < 300:
                        continue

                    # However, the inverse is also true -- the deletion of whatever
                    # was using this address might still be in process.
                    res = floating_network.ipam.get_reservation(ip)
                    if res and res.user_type and res.user_uuid:
                        o = get_object_class(res.user_type).from_db(str(res.user_uuid))
                        if o:
                            obj_state = o.state
                            if (
                                obj_state.value == dbo.STATE_DELETED and
                                time.time() - obj_state.update_time < 300
                            ):
                                continue

                    # A leak!
                    LOG.error(f'Floating IP {ip} has leaked.')
                    leaks.append(ip)

            for ip in leaks:
                LOG.error('Leaked floating IP %s has been released.' % ip)
                floating_network.ipam.release(ip)
