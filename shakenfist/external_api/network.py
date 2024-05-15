# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:

from functools import partial
from flask_jwt_extended import get_jwt_identity
from flasgger import swag_from
from flask_restful import fields, marshal_with
import ipaddress
from shakenfist_utilities import api as sf_api, logs

from shakenfist.config import config
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import eventlog
from shakenfist import network
from shakenfist import networkinterface
from shakenfist.util import process as util_process
from shakenfist.tasks import (
    DestroyNetworkTask, DeleteNetworkWhenClean, RouteAddressTask,
    UnrouteAddressTask)


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

    network_from_db.add_event(EVENT_TYPE_AUDIT, 'delete request from REST API')
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
          'The UUID or name of the network.', True),
         ('namespace', 'body', 'namespace',
          'The namespace to contain the network.', False)],
        [(200, 'Information about a single network.', network_get_example),
         (404, 'Network not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None, namespace=None):
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


networks_get_example = """[
    {
        "name": "sfcbr-7YWeQo4BoqLjASDd",
        "namespace": "sfcbr-7YWeQo4BoqLjASDd",
        "netblock": "10.0.0.0/24",
        "provide_dhcp": true,
        "provide_nat": true,
        "state": "created",
        "uuid": "759b742d-6140-475e-9553-ac120b56c1ef",
        "vxlan_id": 0
    },
    ...
]"""


class NetworksEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Get a list of all networks visible to the authenticated namespace.',
        [('all', 'body', 'boolean', 'Include deleted networks.', False)],
        [(200, 'A list of information about visible networks.', networks_get_example)]))
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
        prefilter = None
        if not all:
            prefilter = 'active'

        retval = []
        for n in network.Networks(filters, prefilter=prefilter):
            # This forces the network through the external view rehydration
            retval.append(n.external_view())
        return retval

    @swag_from(api_base.swagger_helper(
        'networks', 'Create a network.',
        [
            ('netblock', 'body', 'string',
             'A CIDR netblock to use for address allocation on the network.', True),
            ('provide_dhcp', 'body', 'boolean',
             'Whether or not to provide DHCP services on the network.', True),
            ('provide_nat', 'body', 'boolean',
             'Whether or not to NAT for egress traffic on the network.', True),
            ('name', 'body', 'string', 'The name of the network.', True),
            ('namespace', 'body', 'namespace', 'The namespace to contain the network.', False)
        ],
        [(200, 'Information about a single network.', network_get_example),
         (400, 'The netblock is invalid.', None)]))
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
        n.add_event(EVENT_TYPE_AUDIT, 'create request from REST API')
        return n.external_view()

    @swag_from(api_base.swagger_helper(
        'networks', 'Delete all networks in a namespace.',
        [('confirm', 'body', 'boolean', 'I really mean it.', True),
         ('namespace', 'body', 'namespace',
          'The namespace to delete networks from.', False),
         ('clean_wait', 'body', 'boolean',  'Block until complete.', False)],
        [(200, 'A list of the UUIDs of networks awaiting deletion.', None),
         (400, 'The confirm parameter is not True or a administrative user has '
               'not specified a namespace.', None)]))
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
        for n in network.Networks([partial(baseobject.namespace_filter, namespace)],
                                  prefilter='active'):
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


network_events_example = """    [
    ...
    {
        "duration": null,
        "extra": {
            "rx": {
                "bytes": 2146364,
                "dropped": 0,
                "errors": 0,
                "multicast": 0,
                "over_errors": 0,
                "packets": 13127
            },
            "tx": {
                "bytes": 152367092,
                "carrier_errors": 0,
                "collisions": 0,
                "dropped": 0,
                "errors": 0,
                "packets": 96644
            }
        },
        "fqdn": "sf-1",
        "message": "usage",
        "timestamp": 1685229103.9690208,
        "type": "usage"
    },
    ...
]"""


class NetworkEventsEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Get network event information.',
        [
            ('network_ref', 'query', 'uuidorname',
             'The UUID or name of the network.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100.', False)
        ],
        [(200, 'Event information about a single network.', network_events_example),
         (404, 'Network not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.redirect_to_eventlog_node
    @api_base.log_token_use
    def get(self, network_ref=None, event_type=None, limit=100, network_from_db=None):
        with eventlog.EventLog('network', network_from_db.uuid) as eventdb:
            return list(eventdb.read_events(limit=limit, event_type=event_type))


network_interfaces_example = """{
    "floating": "192.168.10.84",
    "instance_uuid": "fffaa23b-c38b-484b-b58e-22eedc6ba94f",
    "ipv4": "10.0.0.20",
    "macaddr": "02:00:00:19:e4:b4",
    "metadata": {},
    "model": "virtio",
    "network_uuid": "91b88200-ab4c-4ac4-9709-459504d1da0a",
    "order": 0,
    "state": "created",
    "uuid": "24e636b4-b60c-4fcc-89d3-e717667a8c83",
    "version": 3
},
{
    "floating": null,
    "instance_uuid": "1762820a-1e44-41b3-9174-44412481d873",
    "ipv4": "10.0.0.57",
    "macaddr": "02:00:00:4b:dc:5f",
    "metadata": {},
    "model": "virtio",
    "network_uuid": "91b88200-ab4c-4ac4-9709-459504d1da0a",
    "order": 0,
    "state": "created",
    "uuid": "0c790a6e-a4de-4518-84e7-11d1421cd4df",
    "version": 3
}"""


class NetworkInterfacesEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Get network interface information.',
        [('network_ref', 'query', 'uuidorname',
          'The UUID or name of the network.', True)],
        [(200, 'The network interfaces on a single network.',
          network_interfaces_example),
         (404, 'Network not found.', None)]))
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
    @swag_from(api_base.swagger_helper(
        'networks', 'Fetch metadata for a network.',
        [('network_ref', 'query', 'uuidorname',
          'The network fetch metadata for.', True)],
        [(200, 'Artifact metadata, if any.', None),
         (404, 'Artifact not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        return network_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'networks', 'Add metadata for a network.',
        [
            ('network_ref', 'query', 'uuidorname', 'The network to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Network not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def post(self, network_ref=None, key=None, value=None, network_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        network_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'post'})
        network_from_db.add_metadata_key(key, value)


class NetworkMetadataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Update a metadata key for a network.',
        [
            ('network_ref', 'query', 'uuidorname', 'The network to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Network not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def put(self, network_ref=None, key=None, value=None, network_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        network_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'put'})
        network_from_db.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'networks', 'Delete a metadata key for a network.',
        [
            ('network_ref', 'query', 'uuidorname', 'The network to remove a key from.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Network not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def delete(self, network_ref=None, key=None, network_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        network_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete metadata key request from REST API',
            extra={'key': key})
        network_from_db.remove_metadata_key(key)


network_ping_example = """{
    "stderr": [
        ""
    ],
    "stdout": [
        "PING 10.0.0.187 (10.0.0.187) 56(84) bytes of data.",
        "64 bytes from 10.0.0.187: icmp_seq=1 ttl=64 time=0.393 ms",
        "64 bytes from 10.0.0.187: icmp_seq=2 ttl=64 time=0.273 ms",
        "64 bytes from 10.0.0.187: icmp_seq=3 ttl=64 time=0.227 ms",
        "64 bytes from 10.0.0.187: icmp_seq=4 ttl=64 time=0.252 ms",
        "64 bytes from 10.0.0.187: icmp_seq=5 ttl=64 time=0.269 ms",
        "64 bytes from 10.0.0.187: icmp_seq=6 ttl=64 time=0.252 ms",
        "64 bytes from 10.0.0.187: icmp_seq=7 ttl=64 time=0.228 ms",
        "64 bytes from 10.0.0.187: icmp_seq=8 ttl=64 time=0.265 ms",
        "64 bytes from 10.0.0.187: icmp_seq=9 ttl=64 time=0.246 ms",
        "64 bytes from 10.0.0.187: icmp_seq=10 ttl=64 time=0.257 ms",
        "",
        "--- 10.0.0.187 ping statistics ---",
        "10 packets transmitted, 10 received, 0% packet loss, time 9213ms",
        "rtt min/avg/max/mdev = 0.227/0.266/0.393/0.044 ms",
        ""
    ]
}"""


class NetworkPingEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Send ICMP ping traffic to an address on a network.',
        [
            ('network_ref', 'query', 'uuidorname',
             'The network to send traffic on.', True),
            ('address', 'query', 'string', 'The IPv4 address to ping.', True)
        ],
        [(200, 'The stdout and stderr of the ping request.', None),
         (400, 'The IPv4 address is not in the network\'s netblock or is invalid.',
          None),
         (404, 'Network not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.redirect_to_network_node
    @api_base.requires_network_active
    @api_base.log_token_use
    def get(self, network_ref=None, address=None, network_from_db=None):
        try:
            ipaddress.ip_address(address)
        except ValueError:
            return sf_api.error(400, 'invalid address')

        if not network_from_db.ipam.is_in_range(address):
            return sf_api.error(400, 'ping request for address outside network block')

        network_from_db.add_event(
            EVENT_TYPE_AUDIT, 'ping request from REST API')
        out, err = util_process.execute(
            None, f'ip netns exec {network_from_db.uuid} ping -c 10 {address}',
            check_exit_code=[0, 1])
        return {
            'stdout': out.split('\n'),
            'stderr': err.split('\n')
        }


network_allocations_example = ''


class NetworkAddressesEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Return information about the address reservations in a network.',
        [
            ('network_ref', 'query', 'uuidorname',
             'The network to return address allocation information about.', True)
        ],
        [(200, 'Address allocations', network_allocations_example),
         (404, 'Network not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        out = []
        for addr in network_from_db.ipam.in_use:
            out.append(network_from_db.ipam.get_reservation(addr))
        return out


class NetworkRouteAddressEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Route a floating address to this network, with no DNAT.',
        [
            ('network_ref', 'query', 'uuidorname',
             'The network route the address to.', True)
        ],
        [(200, 'The address that was routed', None),
         (507, 'No floating addresses are available', None),
         (404, 'Network not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.requires_network_active
    @api_base.log_token_use
    def post(self, network_ref=None, network_from_db=None):
        try:
            address = api_util.assign_routed_ip(network_from_db)
        except exceptions.CongestedNetwork as e:
            return sf_api.error(507, str(e), suppress_traceback=True)

        network_from_db.add_event(EVENT_TYPE_AUDIT, 'route request from REST API')
        etcd.enqueue('networknode', RouteAddressTask(network_from_db.uuid, address))
        return address


class NetworkUnrouteAddressEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Remove routing for a floating address to this network.',
        [
            ('network_ref', 'query', 'uuidorname',
             'The network route the address to.', True),
            ('address', 'query', 'string', 'The address to remove routing for', True)
        ],
        [(200, 'The address that was routed', None),
         (403, 'That address is not routed by this network.', None),
         (404, 'That address is not routed.', None)]))
    @api_base.verify_token
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.requires_network_active
    @api_base.log_token_use
    def delete(self, network_ref=None, network_from_db=None, address=None):
        fn = network.floating_network()
        reservation = fn.ipam.get_reservation(address)
        if not reservation:
            return sf_api.error(404, 'address not routed')
        if reservation['user'] != network_from_db.unique_label():
            return sf_api.error(403, 'address not routed by this network')

        network_from_db.add_event(EVENT_TYPE_AUDIT, 'unroute request from REST API')
        etcd.enqueue('networknode', UnrouteAddressTask(network_from_db.uuid, address))
