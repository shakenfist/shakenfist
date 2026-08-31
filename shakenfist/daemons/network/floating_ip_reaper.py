import time
import itertools

import grpc
from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import get_object_class
from shakenfist.daemons import daemon
from shakenfist import mariadb
from shakenfist.network import network
from shakenfist.network import interface
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


# How long an address must look leaked, continuously and across more than
# one sweep, before we act on it. The lifecycle operations which own
# floating IPs are not atomic, so a single sweep can easily observe an
# object mid-teardown and mistake it for a leak. Requiring the same
# address to look leaked for this long means an in flight delete (seconds)
# never gets reaped, while a genuine leak (forever) still does.
LEAK_CONFIRMATION_SECONDS = 300

# Addresses which looked leaked on previous sweeps, mapped to the time we
# first saw them look that way. Rebuilt on every sweep, so an address
# which stops looking leaked forgets its history.
_leak_candidates: dict[str, float] = {}

# Whether the bulk floating gateway read has been failing, so the
# fallback is logged on the transition into failure rather than on
# every 30 second sweep for the length of a mixed version window.
_bulk_gateway_read_failing = False


def _network_floating_gateways():
    """Every assigned floating gateway, keyed by network uuid -- or None.

    One read for the whole sweep, instead of one GetNetworkAttributes
    per active network below. The attributes row is deliberately
    uncacheable and this sweep runs on every sf-net node every 30
    seconds, so the per-network read made this pair's database rate
    scale with node count times network count (issue 3976) -- the same
    per-object access shape issue 3655 removed for reservations.

    Returns None when the RPC failed: an sf-database from before this
    RPC answers UNIMPLEMENTED for the length of a mixed version window,
    which is not retryable. The caller then falls back to per-network
    attribute reads -- the old shape at the old cost, not a wrong
    answer. An unreachable database tier is different and propagates
    as DatabaseUnavailable, aborting the sweep, because every read
    after this one would fail the same way.
    """
    global _bulk_gateway_read_failing
    try:
        gateways = mariadb.get_network_floating_gateways()
    except grpc.RpcError as e:
        if not _bulk_gateway_read_failing:
            _bulk_gateway_read_failing = True
            LOG.warning(
                'Bulk floating gateway read failed, falling back to '
                'per-network attribute reads: %s' % e)
        return None
    _bulk_gateway_read_failing = False
    return gateways


def reap_floating_ips():
    """Run one reconciliation sweep of the floating network's IPAM.

    Rescues active gateways and floating addresses whose reservation
    has gone missing, then releases reservations with no matching
    user. Split out of ``Job.execute`` so the sweep is unit
    testable. Returns True if a sweep ran, False if there was no
    floating network to sweep.
    """
    floating_network = network.floating_network()
    if not floating_network:
        return False

    # One read of the reservation table for the whole pass. Every loop below
    # walks the in-use addresses, and IPAM.in_use is a property that issues a
    # fresh GetAddressesInUse round trip on each access -- so reading it per
    # address, or even once per loop, is the per-address access shape issue
    # 3655 exists to remove.
    #
    # in_use is snapshotted rather than derived from the reservations dict.
    # The two are the same SELECT differing only in projected columns, so
    # their keys agree -- but the loops below deliberately handle an address
    # which is in use with no reservation row, that being the leak this sweep
    # exists to find, and deriving one from the other would quietly delete
    # that path along with its tests.
    #
    # Snapshot skew is safe. On the rescue paths a stale entry means at worst
    # a reserve() which returns False, and read-then-reserve was never atomic
    # anyway. On the leak path an address must look leaked across
    # LEAK_CONFIRMATION_SECONDS -- ten consecutive sweeps -- before anything
    # is released, so an address reserved after this snapshot drops out on
    # the next pass long before it could be reaped.
    reservations = floating_network.ipam.get_all_reservations()
    in_use = floating_network.ipam.in_use

    LOG.debug('Floating network registrations: %s' % in_use)

    # Collect floating gateways and floating IPs, while ensuring that
    # they are correctly reserved on the floating network as well. The
    # gateways come from one bulk read (see _network_floating_gateways
    # for why); reading n.floating_gateway per network is the fallback
    # for the mixed version window only.
    gateways = _network_floating_gateways()

    floating_gateways = []
    for n in network.Networks([], prefilter='active'):
        if gateways is None:
            fg = n.floating_gateway
        else:
            fg = gateways.get(str(n.uuid))
        if fg:
            floating_gateways.append(fg)
            if fg not in in_use:
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
            if fa not in in_use:
                floating_network.ipam.reserve(
                    fa, ni.unique_label(), ReservationType.FLOATING,
                    'Rescued from incorrect registration')
                LOG.with_fields({
                    'interface': ni.uuid,
                    'address': fa
                }).error('Floating address not reserved correctly')
    LOG.info('Found floating addresses: %s' % floating_addresses)

    floating_routed = []
    for addr in in_use:
        reservation = reservations.get(addr)
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
    now = time.time()
    leaks = []
    candidates = {}
    for ip in in_use:
        if ip not in itertools.chain(floating_gateways,
                                     floating_addresses,
                                     floating_routed,
                                     floating_reserved,
                                     floating_halo):
            # This IP needs to have been allocated more than 300 seconds
            # ago to ensure that the network setup isn't still queued.
            # An address which is in use but has no reservation row at
            # all is precisely the leak this sweep exists to find, so it
            # must fall through rather than be skipped. It also has no
            # age to test -- which is why this used to subtract None from
            # a float and raise.
            res = reservations.get(ip)
            if res and now - res.reserved_at < 300:
                continue

            # However, the inverse is also true -- the deletion of whatever
            # was using this address might still be in process.
            if res and res.user_type and res.user_uuid:
                o = get_object_class(res.user_type).from_db(str(res.user_uuid))
                if o:
                    obj_state = o.state
                    if (
                        obj_state.value == dbo.STATE_DELETED and
                        now - obj_state.update_time < 300
                    ):
                        continue

            # This address _looks_ leaked, but the observations which got
            # us here are not a consistent snapshot -- the object holding
            # the address might have been torn down between the scan above
            # and the reservation lookup here. Only act once the address
            # has looked leaked for LEAK_CONFIRMATION_SECONDS of
            # continuous observation, which no in flight teardown lasts
            # for (issue 3645).
            first_seen = _leak_candidates.get(ip, now)
            candidates[ip] = first_seen
            if now - first_seen < LEAK_CONFIRMATION_SECONDS:
                LOG.with_fields({
                    'address': ip,
                    'first_seen': first_seen
                }).info('Floating IP might have leaked, awaiting confirmation')
                continue

            # A leak!
            LOG.warning(f'Floating IP {ip} has leaked.')
            leaks.append(ip)

    _leak_candidates.clear()
    _leak_candidates.update(candidates)

    for ip in leaks:
        LOG.warning('Leaked floating IP %s has been released.' % ip)
        floating_network.ipam.release(ip)

    return True


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
            if not reap_floating_ips():
                return
