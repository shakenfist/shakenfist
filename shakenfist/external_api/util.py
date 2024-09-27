from flask_jwt_extended import get_jwt_identity
from shakenfist_utilities import api as sf_api
from shakenfist_utilities import logs

from shakenfist import ipam
from shakenfist import network
from shakenfist.daemons import daemon
from shakenfist.instance import Instance
from shakenfist.networkinterface import NetworkInterface


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def assign_floating_ip(ni):
    # Address is allocated and added to the record here, so the job has it later.
    fn = network.floating_network()
    ni.floating = fn.ipam.reserve_random_free_address(
        ni.unique_label(), ipam.RESERVATION_TYPE_FLOATING, '')


def assign_routed_ip(n):
    # Address is allocated and then returned, as there is no network interface
    # to associate it with.
    fn = network.floating_network()
    return fn.ipam.reserve_random_free_address(
        n.unique_label(), ipam.RESERVATION_TYPE_ROUTED, '')


def safe_get_network_interface(interface_uuid):
    ni = NetworkInterface.from_db(interface_uuid)
    if not ni:
        return None, None, sf_api.error(404, 'interface not found')

    log = LOG.with_fields({'network': ni.network_uuid,
                           'networkinterface': ni.uuid})

    n = network.Network.from_db(ni.network_uuid)
    if not n:
        log.info('Network not found or deleted')
        return None, None, sf_api.error(404, 'interface network not found')

    if get_jwt_identity()[0] not in [n.namespace, 'system']:
        log.info('Interface not found, failed ownership test')
        return None, None, sf_api.error(404, 'interface not found')

    i = Instance.from_db(ni.instance_uuid)
    if get_jwt_identity()[0] not in [i.namespace, 'system']:
        log.with_fields({'instance': i}).info(
            'Instance not found, failed ownership test')
        return None, None, sf_api.error(404, 'interface not found')

    return ni, n, None
