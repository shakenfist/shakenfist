from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder

from shakenfist.network import network
from shakenfist.daemons import daemon
from shakenfist.instance import Instance
from shakenfist.network.interface import NetworkInterface
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist.util.access_tokens import request_namespace


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def assign_floating_ip(ni):
    # Address is allocated and added to the record here, so the job has it later.
    fn = network.floating_network()
    ni.floating = fn.ipam.reserve_random_free_address(
        ni.unique_label(), ReservationType.FLOATING, '')


def release_floating_ip(ni):
    # Inverse of assign_floating_ip. The host side teardown (veth and DNAT) is
    # handled asynchronously by the interface_defloat job; here we release the
    # IPAM reservation and clear the interface record so the address returns to
    # the pool. Mirrors the same two steps in NetworkInterface.delete().
    fn = network.floating_network()
    fn.ipam.release(ni.floating.get('floating_address'))
    ni.floating = None


def assign_routed_ip(n):
    # Address is allocated and then returned, as there is no network interface
    # to associate it with.
    fn = network.floating_network()
    return fn.ipam.reserve_random_free_address(
        n.unique_label(), ReservationType.ROUTED, '')


def safe_get_network_interface(interface_uuid):
    ni = NetworkInterface.from_db(interface_uuid)
    if not ni:
        return None, None, sf_api.error(404, 'interface not found')

    log = LOG.with_fields({
        'network': ni.network_uuid,
        'networkinterface': ni.uuid
    })

    n = network.Network.from_db(ni.network_uuid)
    if not n:
        log.info('Network not found or deleted')
        return None, None, sf_api.error(404, 'interface network not found')

    if request_namespace() not in [n.namespace, 'system']:
        log.info('Interface not found, failed ownership test')
        return None, None, sf_api.error(404, 'interface not found')

    i = Instance.from_db(ni.instance_uuid)
    if request_namespace() not in [i.namespace, 'system']:
        log.with_fields({'instance': i}).info(
            'Instance not found, failed ownership test')
        return None, None, sf_api.error(404, 'interface not found')

    return ni, n, None
