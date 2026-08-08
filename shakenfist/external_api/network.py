# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:
import ipaddress
from functools import partial

import flask
import validators
from flasgger import swag_from
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder
from webargs import fields
from webargs.flaskparser import use_kwargs

from shakenfist import baseobject
from shakenfist import exceptions
from shakenfist.network import network
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import FLOATING_NETWORK_UUID
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.external_api import util as api_util
from shakenfist.schema.operations.baseclusteroperation \
    import PRIORITY
from shakenfist.schema.operations.net_op \
    import create_and_enqueue as net_create_and_enqueue
from shakenfist.schema.operations.net_op \
    import model_tasks as net_tasks
from shakenfist.schema.operations.net_ip_op \
    import create_and_enqueue as nip_create_and_enqueue
from shakenfist.schema.operations.net_ip_op \
    import model_tasks as nip_tasks
from shakenfist.util.access_tokens import request_namespace
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def _delete_network(network_from_db, wait_interfaces=None):
    """Initiate deletion of a network.

    Returns a ``(result, op_type, op_uuid)`` tuple:

    * On success, the network deletion has been enqueued and
      ``op_type`` / ``op_uuid`` identify the cluster operation that
      will perform the work.
    * If ``wait_interfaces`` is truthy the network is also moved to
      ``STATE_DELETE_WAIT`` to stop new interfaces being attached;
      the enqueued op defers itself in the worker until the existing
      interfaces drain, then performs the delete. The Phase 7 REST
      contract guarantees the caller always receives an op handle to
      poll, even on this slow path.
    * On failure (network missing or already deleted), ``result`` is the
      Flask error response to return; ``op_type`` / ``op_uuid`` are
      ``None``.
    """
    # Load network from DB to ensure obtaining correct lock.
    n = network.Network.from_db(network_from_db.uuid)
    if not n:
        LOG.with_fields({'network_uuid': n.uuid}).warning(
            'delete_network: network does not exist')
        return sf_api.error(404, 'network does not exist'), None, None

    if n.is_dead() and n.state.value != network.Network.STATE_DELETE_WAIT:
        # The network has been deleted. No need to attempt further effort.
        # We do allow attempts to delete networks in DELETE_WAIT.
        LOG.with_fields({'network_uuid': n.uuid,
                         'state': n.state.value
                         }).warning('delete_network: network is dead')
        return sf_api.error(404, 'network is deleted'), None, None

    network_from_db.add_event(EVENT_TYPE_AUDIT, 'delete request from REST API')
    if wait_interfaces:
        # The interfaces still attached belong to instances that are
        # being deleted concurrently. Block new interfaces from being
        # attached by entering DELETE_WAIT; the enqueued op below
        # checks for interfaces at execution time and defers itself
        # until they drain.
        n.state = network.Network.STATE_DELETE_WAIT

    # Phase 6 of `PLAN-network-facade.md` retired the
    # `network_destroy` composite; `network_apply_delete_network_node`
    # is the direct behavioural equivalent.
    op_type, op_uuid = net_create_and_enqueue(
        n.uuid,
        [net_tasks.network_apply_delete_network_node],
        PRIORITY.user_facing,
        request_id=util_general.get_request_id())
    return None, str(op_type), str(op_uuid)


network_get_example = """{
    "floating_gateway": "192.168.10.16",
    "last_cluster_operation": {
        "op_type": "net_iface_op",
        "op_uuid": "48a40459-a813-491f-81d6-a68536122e07"
    },
    "metadata": {},
    "name": "example",
    "namespace": "system",
    "netblock": "10.0.0.0/24",
    "provide_dhcp": true,
    "provide_nat": true,
    "provide_dns": false,
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
    "provide_dns": false,
    "provide_nat": true,
    "state": "deleted",
    "uuid": "d56ae6e4-2592-43cd-b614-2dc7ca04970a",
    "version": 4,
    "vxid": 15408371
}
"""


class NetworkEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Get network information.',
        [('network_ref', 'path', 'uuidorname',
          'The UUID or name of the network.', True),
         ('namespace', 'body', 'namespace',
          'Scope the name lookup to this namespace.', False)],
        [(200, 'Information about a single network.', network_get_example),
         (404, 'Network not found.', None)]))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None, namespace=None):
        return network_from_db.external_view()

    @swag_from(api_base.swagger_helper(
        'networks', 'Delete a network.',
        [('network_ref', 'path', 'uuidorname',
          'The UUID or name of the network.', True),
         ('namespace', 'body', 'namespace',
          'Scope the name lookup to this namespace.', False)],
        [(202,
          'Deletion has been queued. The response body identifies the cluster '
          'operation that will perform the work; clients should poll the '
          'cluster-operations endpoints to observe completion.', None),
         (404, 'Network not found.', None)]))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def delete(self, network_ref=None, network_from_db=None, namespace=None):
        if network_ref == str(FLOATING_NETWORK_UUID):
            return sf_api.error(403, 'you cannot delete the floating network')

        # An already-deleted network has nothing left to enqueue, but the
        # Phase 7 client contract is "DELETE returns 202+op-handle and
        # the client polls"; returning a bare ``return`` here produced a
        # 200 ``null`` body that crashed the client on
        # ``handle['op_type']``. ``_delete_network`` itself emits a clean
        # 404 for dead networks (other than DELETE_WAIT, which it lets
        # through), so just delegate.
        err, op_type, op_uuid = _delete_network(
            network_from_db, wait_interfaces=network_from_db.networkinterfaces)
        if err is not None:
            return err

        # Phase 7 of `PLAN-network-facade.md` flipped this endpoint to the
        # 202+poll contract: the delete work runs asynchronously via the
        # cluster operation queue, so the response acknowledges receipt
        # and returns the op handle the client can poll.
        resp = flask.jsonify({'op_type': op_type, 'op_uuid': op_uuid})
        resp.status_code = 202
        return resp


networks_get_example = """[
    {
        "name": "sfcbr-7YWeQo4BoqLjASDd",
        "namespace": "sfcbr-7YWeQo4BoqLjASDd",
        "netblock": "10.0.0.0/24",
        "provide_dhcp": true,
        "provide_nat": true,
        "provide_dns": false,
        "state": "created",
        "uuid": "759b742d-6140-475e-9553-ac120b56c1ef",
        "vxlan_id": 0
    },
    ...
]"""


class NetworksEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Get a list of all networks visible to the authenticated namespace.',
        [('all', 'body', 'boolean', 'Include deleted networks.', False)],
        [(200, 'A list of information about visible networks.', networks_get_example)]))
    @api_base.log_token_use
    def get(self, all=False):
        filters = [partial(baseobject.namespace_filter,
                           request_namespace())]
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
            ('netblock', 'body', 'netblock',
             'A CIDR netblock to use for address allocation on the network.', True),
            ('provide_dhcp', 'body', 'boolean',
             'Whether or not to provide DHCP services on the network. Defaults '
             'to enabled.', False),
            ('provide_nat', 'body', 'boolean',
             'Whether or not to NAT for egress traffic on the network. Defaults '
             'to enabled.', False),
            ('provide_dns', 'body', 'boolean',
             'Whether or not to provide a DNS server with hosts from this '
             'virtual network configured as a domain. Defaults to disabled.',
             False),
            ('name', 'body', 'string', 'The name of the network.', True),
            ('namespace', 'body', 'namespace', 'The namespace to contain the network.', False)
        ],
        [(200, 'Information about a single network.', network_get_example),
         (400, 'The netblock is invalid.', None)]))
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def post(self, netblock=None, provide_dhcp=None, provide_nat=None, name=None,
             namespace=None, provide_dns=None):
        # NOTE(mikal): these defaults must distinguish "the caller omitted
        # the field" (None) from "the caller explicitly asked for False".
        # Using `if not provide_nat` collapses both cases and silently
        # re-enables NAT (and DHCP) when --no-nat / --no-dhcp was requested.
        if provide_dhcp is None:
            provide_dhcp = True
        if provide_nat is None:
            provide_nat = True
        if provide_dns is None:
            provide_dns = False

        try:
            n = ipaddress.ip_network(netblock)
            if n.num_addresses < 8:
                return sf_api.error(400, 'network is below minimum size of /29')
        except ValueError as e:
            return sf_api.error(
                400, 'cannot parse netblock: %s' % e, suppress_traceback=True)

        if not namespace:
            namespace = request_namespace()

        # If accessing a foreign name namespace, we need to be an admin
        if request_namespace() not in [namespace, 'system']:
            return sf_api.error(
                401, 'only admins can create resources in a different namespace')

        n = network.Network.new(name, namespace, netblock, provide_dhcp,
                                provide_nat, provide_dns=provide_dns)
        n.add_event(EVENT_TYPE_AUDIT, 'create request from REST API')
        return n.external_view()

    @swag_from(api_base.swagger_helper(
        'networks', 'Delete all networks in a namespace.',
        [('confirm', 'body', 'boolean', 'I really mean it.', True),
         ('namespace', 'body', 'namespace',
          'The namespace to delete networks from.', False),
         ('clean_wait', 'body', 'boolean',  'Block until complete.', False)],
        [(202, 'A list of {network_uuid, op_type, op_uuid} entries identifying '
               'the cluster operations that will perform the per-network deletes. '
               'Clients should poll the cluster-operations endpoints to observe '
               'completion.', None),
         (400, 'The confirm parameter is not True or a administrative user has '
               'not specified a namespace.', None)]))
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def delete(self, confirm=False, namespace=None, clean_wait=False):
        """Delete all networks in the namespace.

        Set clean_wait to True to have the system wait until all interfaces are
        deleted from the network. New instances will not be permitted to be
        added to the network.
        """

        if confirm is not True:
            return sf_api.error(400, 'parameter confirm is not set true')

        if request_namespace() == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all networks in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return sf_api.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != request_namespace():
                return sf_api.error(401, 'you cannot delete other namespaces')
            namespace = request_namespace()

        networks_del = []
        networks_unable = []
        for n in network.Networks(namespace=namespace, prefilter='active'):
            if not n.networkinterfaces:
                _, op_type, op_uuid = _delete_network(n)
            else:
                if clean_wait:
                    _, op_type, op_uuid = _delete_network(n, n.networkinterfaces)
                else:
                    LOG.with_fields({'network': n}).warning(
                        'Network in use, cannot be deleted by delete-all')
                    networks_unable.append(str(n.uuid))
                    continue

            networks_del.append({
                'network_uuid': str(n.uuid),
                'op_type': op_type,
                'op_uuid': op_uuid,
            })

        if networks_unable:
            return sf_api.error(403, {'deleted': networks_del,
                                      'unable': networks_unable})

        # Phase 7 of `PLAN-network-facade.md` flipped this endpoint to the
        # 202+poll contract: each network's delete work runs asynchronously
        # via the cluster operation queue, so the response acknowledges
        # receipt and returns one op handle per network for the client to
        # poll.
        resp = flask.jsonify(networks_del)
        resp.status_code = 202
        return resp


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


class NetworkEventsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Get network event information.',
        [
            ('network_ref', 'path', 'uuidorname',
             'The UUID or name of the network.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100 and is '
             'capped at 1000.', False, {'minimum': 1, 'maximum': 1000})
        ],
        [(200, 'Event information about a single network.', network_events_example),
         (404, 'Network not found.', None)]))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, event_type=None, limit=100, network_from_db=None):
        return api_base.object_events_response(
            'network', network_from_db.uuid, limit, event_type)


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


class NetworkInterfacesEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Get network interface information.',
        [('network_ref', 'path', 'uuidorname',
          'The UUID or name of the network.', True)],
        [(200, 'The network interfaces on a single network.',
          network_interfaces_example),
         (404, 'Network not found.', None)]))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        out = []
        for ni in network_from_db.networkinterfaces:
            if not ni:
                continue
            out.append(ni.external_view())
        return out


class NetworkMetadatasEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Fetch metadata for a network.',
        [('network_ref', 'path', 'uuidorname',
          'The network fetch metadata for.', True)],
        [(200, 'Artifact metadata, if any.', None),
         (404, 'Artifact not found.', None)],
        requires_admin=True))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        return network_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'networks', 'Add metadata for a network.',
        [
            ('network_ref', 'path', 'uuidorname', 'The network to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Network not found.', None)],
        requires_admin=True))
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


class NetworkMetadataEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Update a metadata key for a network.',
        [
            ('network_ref', 'path', 'uuidorname', 'The network to add a key to.', True),
            ('key', 'path', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Network not found.', None)],
        requires_admin=True))
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
            ('network_ref', 'path', 'uuidorname', 'The network to remove a key from.', True),
            ('key', 'path', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Network not found.', None)],
        requires_admin=True))
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


class NetworkPingEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Send ICMP ping traffic to an address on a network.',
        [
            ('network_ref', 'path', 'uuidorname',
             'The network to send traffic on.', True),
            ('address', 'path', 'string', 'The IPv4 address to ping.', True)
        ],
        [(200, 'The stdout and stderr of the ping request.', None),
         (400, 'The IPv4 address is not in the network\'s netblock or is invalid.',
          None),
         (404, 'Network not found.', None)],
        requires_admin=True))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    # NOTE(phase-7): this is the sole remaining `redirect_to_network_node`
    # site. The ping handler shells out to `ip netns exec <network_uuid>
    # ping -c 10 <address>`, so it genuinely needs to execute on the
    # elected network node where the network namespace exists. Migrating
    # to a queue-based ping requires op-output infrastructure that does
    # not yet exist -- today the operation queue carries error reports
    # only, not arbitrary command stdout/stderr. See
    # `docs/plans/PLAN-network-facade.md` future work for the migration
    # plan; Phase 7 intentionally kept the redirect here as a tactical
    # exception while removing it from the other three sites.
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
        out, err = util_concurrency.execute(
            f'ip netns exec {network_from_db.uuid} ping -c 10 {address}',
            check_exit_code=[0, 1])
        return {
            'stdout': out.split('\n'),
            'stderr': err.split('\n')
        }


network_allocations_example = ''


class NetworkAddressesEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Return information about the address reservations in a network.',
        [
            ('network_ref', 'path', 'uuidorname',
             'The network to return address allocation information about.', True)
        ],
        [(200, 'Address allocations', network_allocations_example),
         (404, 'Network not found.', None)]))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, network_from_db=None):
        out = []
        for addr in network_from_db.ipam.in_use:
            reservation = network_from_db.ipam.get_reservation(addr)
            if reservation:
                out.append(reservation.model_dump(mode='json'))
        return out


class NetworkRouteAddressEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Route a floating address to this network, with no DNAT.',
        [
            ('network_ref', 'path', 'uuidorname',
             'The network route the address to.', True)
        ],
        [(200, 'The address that was routed', None),
         (507, 'No floating addresses are available', None),
         (404, 'Network not found.', None)]))
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
        nip_create_and_enqueue(
            network_from_db.uuid,
            address,
            [nip_tasks.route_address],
            priority=PRIORITY.user_facing,
            request_id=util_general.get_request_id()
        )
        return address


class NetworkUnrouteAddressEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Remove routing for a floating address to this network.',
        [
            ('network_ref', 'path', 'uuidorname',
             'The network route the address to.', True),
            ('address', 'path', 'string', 'The address to remove routing for', True)
        ],
        [(200, 'The address that was routed', None),
         (403, 'That address is not routed by this network.', None),
         (404, 'That address is not routed.', None)]))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.requires_network_active
    @api_base.log_token_use
    def delete(self, network_ref=None, network_from_db=None, address=None):
        fn = network.floating_network()
        reservation = fn.ipam.get_reservation(address)
        if not reservation:
            return sf_api.error(404, 'address not routed')
        # Compare with str(user_uuid) since unique_label() returns a string UUID
        res_label = (reservation.user_type, str(reservation.user_uuid) if reservation.user_uuid else None)
        if res_label != network_from_db.unique_label():
            return sf_api.error(403, 'address not routed by this network')

        network_from_db.add_event(EVENT_TYPE_AUDIT, 'unroute request from REST API')
        nip_create_and_enqueue(
            network_from_db.uuid,
            address,
            [nip_tasks.unroute_address],
            priority=PRIORITY.user_facing,
            request_id=util_general.get_request_id()
        )


class NetworkDNSAddressEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'networks', 'Add a custom DNS entry for this network.',
        [
            ('network_ref', 'path', 'uuidorname',
             ('The network to add a DNS record for, which must have provide_dns '
              'enabled.'),
             True),
            ('name', 'body', 'string', 'The DNS entry', True),
            ('value', 'body', 'ipv4',
             'The IP address the DNS entry resolves to', True)
        ],
        [(200, 'DNS entry created', None),
         (400, 'Network does not have provide_dns enabled', None),
         (404, 'Network not found.', None),
         (406, 'The provided DNS entry is invalid', None)]))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.requires_network_active
    @api_base.log_token_use
    def post(self, network_ref=None, network_from_db=None, name=None, value=None):
        if not network_from_db.provide_dns:
            return sf_api.error(406, 'network does not provide DNS')

        valid_hostname = validators.hostname(
            name, skip_ipv4_addr=True, skip_ipv6_addr=True, may_have_port=False)
        if not valid_hostname:
            return sf_api.error(406, 'invalid DNS name')

        op = network_from_db.update_dns_entry(name, value)
        if op is not None:
            op.raise_for_error()

    @swag_from(api_base.swagger_helper(
        'networks', 'Remove a custom DNS entry for this network.',
        [
            ('network_ref', 'path', 'uuidorname',
             'The network route the address to.', True),
            ('name', 'body', 'string', 'The DNS entry', True)
        ],
        [(200, 'DNS entry removed', None),
         (400, 'Network does not have provide_dns enabled or name not found', None),
         (404, 'Network not found.', None),
         (406, 'The provided DNS entry is invalid', None)]))
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.requires_network_active
    @api_base.log_token_use
    def delete(self, network_ref=None, network_from_db=None, name=None):
        if not network_from_db.provide_dns:
            return sf_api.error(406, 'network does not provide DNS')

        valid_hostname = validators.hostname(
            name, skip_ipv4_addr=True, skip_ipv6_addr=True, may_have_port=False)
        if not valid_hostname:
            return sf_api.error(406, 'invalid DNS name')

        op = network_from_db.remove_dns_entry(name)
        if op is not None:
            op.raise_for_error()


network_outstanding_operations_example = """[
    [
        {
            "network_uuid": "7c822e61-a5cc-45ed-9ffa-086568aa7ade",
            "operation_type": "net_op",
            "state": "complete",
            "tasks": [
                "network_deploy"
            ],
            "uuid": "22b757ae-41d1-456b-84a4-edca694ceb6d"
        }
    ]
]"""


class NetworkOutstandingOperationsEndpoint(api_base.Resource):
    # NOTE(mikal): note that arguments from URL routes (object uuid for example),
    # are not included in the webargs schema because webargs doesn't appear to
    # know how to find them.
    get_args = {
        'all': fields.Boolean(load_default=False)
    }

    @swag_from(api_base.swagger_helper(
        'networks', 'Get the outstanding cluster operations for a network.',
        [('network_ref', 'path', 'uuidorname',
          'The UUID or name of the network.', True),
         ('all', 'query', 'boolean',
          'Include operations which have already completed, rather than '
          'only those still in flight.', False)],
        [(
            200,
            'A list of the cluster operations not yet executed for this network.',
            network_outstanding_operations_example),
         (404, 'Network not found.', None)]))
    @use_kwargs(get_args, location='json_or_query')
    @api_base.arg_is_network_ref
    @api_base.requires_network_ownership
    @api_base.log_token_use
    def get(self, network_ref=None, all=False, network_from_db=None):
        retval = []
        for op in network_from_db.get_cluster_operations(
            outstanding_only=(not all)
        ):
            retval.append(op.external_view())
        return retval
