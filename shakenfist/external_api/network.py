from functools import partial
from flask_jwt_extended import jwt_required, get_jwt_identity
from flask_restful import fields, marshal_with
import ipaddress

from shakenfist.config import config
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import etcd
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import net
from shakenfist import networkinterface
from shakenfist.util import process as util_process
from shakenfist.tasks import DestroyNetworkTask, DeleteNetworkWhenClean


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


def _delete_network(network_from_db, wait_interfaces=None):
    # Load network from DB to ensure obtaining correct lock.
    n = net.Network.from_db(network_from_db.uuid)
    if not n:
        LOG.with_fields({'network_uuid': n.uuid}).warning(
            'delete_network: network does not exist')
        return api_base.error(404, 'network does not exist')

    if n.is_dead() and n.state.value != net.Network.STATE_DELETE_WAIT:
        # The network has been deleted. No need to attempt further effort.
        # We do allow attempts to delete networks in DELETE_WAIT.
        LOG.with_fields({'network_uuid': n.uuid,
                         'state': n.state.value
                         }).warning('delete_network: network is dead')
        return api_base.error(404, 'network is deleted')

    if wait_interfaces:
        n.state = net.Network.STATE_DELETE_WAIT
        n.add_event('api', 'delete-wait')
        etcd.enqueue(config.NODE_NAME,
                     {'tasks': [DeleteNetworkWhenClean(n.uuid, wait_interfaces)]})
    else:
        n.add_event('api', 'delete')
        etcd.enqueue('networknode', DestroyNetworkTask(n.uuid))


class NetworkEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        return network_from_db.external_view()

    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    @api_base.requires_namespace_exist
    @api_base.redirect_to_network_node
    def delete(self, network_uuid=None, network_from_db=None, namespace=None):
        if network_uuid == 'floating':
            return api_base.error(403, 'you cannot delete the floating network')

        n = net.Network.from_db(network_from_db.uuid)
        if not n:
            LOG.with_fields({'network_uuid': n.uuid}).warning(
                'delete_network: network does not exist')
            return api_base.error(404, 'network does not exist')

        # If a namespace is specified, ensure the network is in it
        if namespace:
            if network_from_db.namespace != namespace:
                return api_base.error(404, 'network not in namespace')

        # We only delete unused networks
        ifaces = list(networkinterface.interfaces_for_network(n))
        if len(ifaces) > 0:
            for iface in ifaces:
                LOG.withFields({'network_interface': iface.uuid,
                                'state': iface.state}).info('Blocks network delete')
            return api_base.error(403, 'you cannot delete an in use network')

        # Check if network has already been deleted
        if network_from_db.state.value in dbo.STATE_DELETED:
            return

        _delete_network(network_from_db)


class NetworksEndpoint(api_base.Resource):
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
    @jwt_required
    def get(self, all=False):
        filters = [partial(baseobject.namespace_filter,
                           get_jwt_identity()[0])]
        if not all:
            filters.append(baseobject.active_states_filter)

        retval = []
        for n in net.Networks(filters):
            # This forces the network through the external view rehydration
            retval.append(n.external_view())
        return retval

    @jwt_required
    @api_base.requires_namespace_exist
    def post(self, netblock=None, provide_dhcp=None, provide_nat=None, name=None,
             namespace=None):
        try:
            n = ipaddress.ip_network(netblock)
            if n.num_addresses < 8:
                return api_base.error(400, 'network is below minimum size of /29')
        except ValueError as e:
            return api_base.error(400, 'cannot parse netblock: %s' % e,
                                  suppress_traceback=True)

        if not namespace:
            namespace = get_jwt_identity()[0]

        # If accessing a foreign name namespace, we need to be an admin
        if get_jwt_identity()[0] not in [namespace, 'system']:
            return api_base.error(
                401, 'only admins can create resources in a different namespace')

        network = net.Network.new(name, namespace, netblock, provide_dhcp,
                                  provide_nat)
        return network.external_view()

    @jwt_required
    @api_base.requires_namespace_exist
    @api_base.redirect_to_network_node
    def delete(self, confirm=False, namespace=None, clean_wait=False):
        """Delete all networks in the namespace.

        Set clean_wait to True to have the system wait until all interfaces are
        deleted from the network. New instances will not be permitted to be
        added to the network.
        """

        if confirm is not True:
            return api_base.error(400, 'parameter confirm is not set true')

        if get_jwt_identity()[0] == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all networks in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return api_base.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity()[0]:
                return api_base.error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()[0]

        networks_del = []
        networks_unable = []
        for n in net.Networks([partial(baseobject.namespace_filter, namespace),
                               baseobject.active_states_filter]):
            iface_on_net = list(networkinterface.interfaces_for_network(n))
            if not iface_on_net:
                _delete_network(n)
            else:
                if clean_wait:
                    _delete_network(n, [n.uuid for n in iface_on_net])
                else:
                    LOG.with_object(n).warning(
                        'Network in use, cannot be deleted by delete-all')
                    networks_unable.append(n.uuid)
                    continue

            networks_del.append(n.uuid)

        if networks_unable:
            return api_base.error(403, {'deleted': networks_del,
                                        'unable': networks_unable})

        return networks_del


class NetworkEventsEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        return list(db.get_events('network', network_uuid))


class NetworkInterfacesEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        out = []
        for ni in networkinterface.interfaces_for_network(self.network):
            out.append(ni.external_view())
        return out


class NetworkMetadatasEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        md = db.get_metadata('network', network_uuid)
        if not md:
            return {}
        return md

    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def post(self, network_uuid=None, key=None, value=None, network_from_db=None):
        return api_util.metadata_putpost('network', network_uuid, key, value)


class NetworkMetadataEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def put(self, network_uuid=None, key=None, value=None, network_from_db=None):
        return api_util.metadata_putpost('network', network_uuid, key, value)

    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def delete(self, network_uuid=None, key=None, network_from_db=None):
        if not key:
            return api_base.error(400, 'no key specified')

        with db.get_lock('metadata', 'network', network_uuid, op='Network metadata delete'):
            md = db.get_metadata('network', network_uuid)
            if md is None or key not in md:
                return api_base.error(404, 'key not found')
            del md[key]
            db.persist_metadata('network', network_uuid, md)


class NetworkPingEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    @api_base.redirect_to_network_node
    @api_base.requires_network_active
    def get(self, network_uuid=None, address=None, network_from_db=None):
        ipm = IPManager.from_db(network_uuid)
        if not ipm.is_in_range(address):
            return api_base.error(400, 'ping request for address outside network block')

        n = net.Network.from_db(network_uuid)
        if not n:
            return api_base.error(404, 'network %s not found' % network_uuid)

        out, err = util_process.execute(
            None, 'ip netns exec %s ping -c 10 %s' % (
                network_uuid, address),
            check_exit_code=[0, 1])
        return {
            'stdout': out,
            'stderr': err
        }
