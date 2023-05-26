# Documentation state:
#   - Has metadata calls:
#   - OpenAPI complete:
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:

from functools import partial
from flask_jwt_extended import get_jwt_identity
from flasgger import swag_from
from flask_restful import fields, marshal_with
import ipaddress
from shakenfist_utilities import api as sf_api, logs

from shakenfist.config import config
from shakenfist.external_api import base as api_base
from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import network
from shakenfist import networkinterface
from shakenfist.util import process as util_process
from shakenfist.tasks import DestroyNetworkTask, DeleteNetworkWhenClean


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def _delete_network(network_from_db, wait_interfaces=None):
    # Load network from DB to ensure obtaining correct lock.
    n = network.Network.from_db(network_from_db.uuid)
    if not n:
        LOG.with_fields({'network_uuid': n.uuid}).warning(
            'delete_network: network does not exist')
        return sf_api.error(404, 'network does not exist')

    if n.is_dead() and n.state.value != network.Network.STATE_DELETE_WAIT:
        # The network has been deleted. No need to attempt further effort.
        # We do allow attempts to delete networks in DELETE_WAIT.
        LOG.with_fields({'network_uuid': n.uuid,
                         'state': n.state.value
                         }).warning('delete_network: network is dead')
        return sf_api.error(404, 'network is deleted')

    if wait_interfaces:
        n.state = network.Network.STATE_DELETE_WAIT
        etcd.enqueue(config.NODE_NAME,
                     {'tasks': [DeleteNetworkWhenClean(n.uuid, wait_interfaces)]})
    else:
        etcd.enqueue('networknode', DestroyNetworkTask(n.uuid))


network_get_example = """{
    "floating_gateway": "192.168.10.16",
    "metadata": {},
    "name": "example",
    "namespace": "system",
    "netblock": "10.0.0.0/24",
    "provide_dhcp": true,
    "provide_nat": true,
    "state": "created",
    "uuid": "1e9222c5-2d11-4ada-b258-ed1838bd774b",
    "version": 4,
    "vxid": 4882442
}"""

network_delete_example = """
{
    "floating_gateway": null,
    "metadata": {},
    "name": "example",
    "namespace": "system",
    "netblock": "10.0.0.0/24",
    "provide_dhcp": true,
    "provide_nat": true,
    "state": "deleted",
    "uuid": "d56ae6e4-2592-43cd-b614-2dc7ca04970a",
    "version": 4,
    "vxid": 15408371
}
"""


class NetworkEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Get network information.',
        [('artifact_ref', 'query', 'uuidorname',
          'The UUID or name of the network.', True)],
        [(200, 'Information about a single network.', network_get_example),
         (404, 'Network not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        return network_from_db.external_view()

    @swag_from(api_base.swagger_helper(
        'networks', 'Delete a network.',
        [('artifact_ref', 'query', 'uuidorname',
          'The UUID or name of the network.', True)],
        [(200,
          'Information about a single network, this may not immediately indicate '
          'the network is deleted.', network_delete_example),
         (404, 'Network not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.requires_namespace_exist_if_specified
    @api_base.redirect_to_network_node
    @api_base.log_token_use
    def delete(self, network_ref=None, network_from_db=None, namespace=None):
        if network_ref == 'floating':
            return sf_api.error(403, 'you cannot delete the floating network')

        # If a namespace is specified, ensure the network is in it
        if namespace:
            if network_from_db.namespace != namespace:
                return sf_api.error(404, 'network not in namespace')

        # Check if network has already been deleted
        if network_from_db.state.value in dbo.STATE_DELETED:
            return

        _delete_network(
            network_from_db, wait_interfaces=network_from_db.networkinterfaces)

        # Return UUID in case API call was made using object name
        return network_from_db.external_view()


class NetworksEndpoint(sf_api.Resource):
    @marshal_with({
        'uuid': fields.String,
        'vxlan_id': fields.Integer,
        'netblock': fields.String,
        'provide_dhcp': fields.Boolean,
        'provide_nat': fields.Boolean,
        'namespace': fields.String,
        'name': fields.String,
        'state': fields.String
    })
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, all=False):
        filters = [partial(baseobject.namespace_filter, get_jwt_identity()[0])]
        if not all:
            filters.append(baseobject.active_states_filter)

        retval = []
        for n in network.Networks(filters):
            # This forces the network through the external view rehydration
            retval.append(n.external_view())
        return retval

    @api_base.verify_token
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def post(self, netblock=None, provide_dhcp=None, provide_nat=None, name=None,
             namespace=None):
        try:
            n = ipaddress.ip_network(netblock)
            if n.num_addresses < 8:
                return sf_api.error(400, 'network is below minimum size of /29')
        except ValueError as e:
            return sf_api.error(
                400, 'cannot parse netblock: %s' % e, suppress_traceback=True)

        if not namespace:
            namespace = get_jwt_identity()[0]

        # If accessing a foreign name namespace, we need to be an admin
        if get_jwt_identity()[0] not in [namespace, 'system']:
            return sf_api.error(
                401, 'only admins can create resources in a different namespace')

        n = network.Network.new(name, namespace, netblock, provide_dhcp,
                                provide_nat)
        return n.external_view()

    @api_base.verify_token
    @api_base.requires_namespace_exist_if_specified
    @api_base.redirect_to_network_node
    @api_base.log_token_use
    def delete(self, confirm=False, namespace=None, clean_wait=False):
        """Delete all networks in the namespace.

        Set clean_wait to True to have the system wait until all interfaces are
        deleted from the network. New instances will not be permitted to be
        added to the network.
        """

        if confirm is not True:
            return sf_api.error(400, 'parameter confirm is not set true')

        if get_jwt_identity()[0] == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all networks in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return sf_api.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity()[0]:
                return sf_api.error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()[0]

        networks_del = []
        networks_unable = []
        for n in network.Networks([partial(baseobject.namespace_filter, namespace),
                                   baseobject.active_states_filter]):
            if not n.networkinterfaces:
                _delete_network(n)
            else:
                if clean_wait:
                    _delete_network(n, n.networkinterfaces)
                else:
                    LOG.with_fields({'network': n}).warning(
                        'Network in use, cannot be deleted by delete-all')
                    networks_unable.append(n.uuid)
                    continue

            networks_del.append(n.uuid)

        if networks_unable:
            return sf_api.error(403, {'deleted': networks_del,
                                      'unable': networks_unable})

        return networks_del


class NetworkEventsEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.redirect_to_eventlog_node
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        with eventlog.EventLog('network', network_from_db.uuid) as eventdb:
            return list(eventdb.read_events())


class NetworkInterfacesEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        out = []
        for ni_uuid in network_from_db.networkinterfaces:
            ni = networkinterface.NetworkInterface.from_db(ni_uuid)
            if not ni:
                continue
            out.append(ni.external_view())
        return out


class NetworkMetadatasEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        return network_from_db.metadata

    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def post(self, network_ref=None, key=None, value=None, network_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        network_from_db.add_metadata_key(key, value)


class NetworkMetadataEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def put(self, network_ref=None, key=None, value=None, network_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        network_from_db.add_metadata_key(key, value)

    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def delete(self, network_ref=None, key=None, network_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        network_from_db.remove_metadata_key(key)


class NetworkPingEndpoint(sf_api.Resource):
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.redirect_to_network_node
    @api_base.requires_network_active
    @api_base.log_token_use
    def get(self, network_ref=None, address=None, network_from_db=None):
        if not network_from_db.is_in_range(address):
            return sf_api.error(400, 'ping request for address outside network block')

        out, err = util_process.execute(
            None, 'ip netns exec %s ping -c 10 %s' % (
                network_from_db.uuid, address),
            check_exit_code=[0, 1])
        return {
            'stdout': out,
            'stderr': err
        }
