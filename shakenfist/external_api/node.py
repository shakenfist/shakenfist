# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist:
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:

from flasgger import swag_from
from shakenfist_utilities import api as sf_api

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist.external_api import base as api_base
from shakenfist.node import Node, Nodes


node_get_example = """{
    "ip": "192.168.21.51",
    "is_cluster_maintainer": true,
    "is_etcd_master": false,
    "is_eventlog_node": true,
    "is_hypervisor": true,
    "is_network_node": true,
    "lastseen": 1685351741.350039,
    "name": "sf-1",
    "release": "0.7.0",
    "state": "created",
    "version": "3"
}"""

node_delete_example = """{
    "ip": "192.168.21.51",
    "is_cluster_maintainer": true,
    "is_etcd_master": false,
    "is_eventlog_node": true,
    "is_hypervisor": true,
    "is_network_node": true,
    "lastseen": 1685351741.350039,
    "name": "sf-1",
    "release": "0.7.0",
    "state": "deleted",
    "version": "3"
}"""


class NodeEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'nodes', 'Get information for a node.',
        [('node_name', 'query', 'node', 'The name of a node.', True)],
        [(200, 'Information about a single node.', node_get_example),
         (404, 'Node not found.', None)]))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self, node=None):
        n = Node.from_db(node, suppress_failure_audit=True)
        if not n:
            return sf_api.error(404, 'node not found')
        return n.external_view()

    @swag_from(api_base.swagger_helper(
        'nodes', 'Delete a node.',
        [('node_name', 'query', 'node', 'The name of a node.', True)],
        [(200, 'Information about a single node.', node_delete_example),
         (404, 'Node not found.', None)]))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def delete(self, node=None):
        n = Node.from_db(node, suppress_failure_audit=True)
        if not n:
            return sf_api.error(404, 'node not found')

        n.add_event(EVENT_TYPE_AUDIT, 'delete request from REST API')
        n.delete()
        return n.external_view()


node_list_example = """[
    {
        "ip": "192.168.21.51",
        "is_cluster_maintainer": true,
        "is_etcd_master": false,
        "is_eventlog_node": true,
        "is_hypervisor": true,
        "is_network_node": true,
        "lastseen": 1685351741.350039,
        "name": "sf-1",
        "release": "0.7.0",
        "state": "created",
        "version": "3"
    },
    ...
]"""


class NodesEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'nodes', 'List all nodes.',
        [],
        [(200, 'Information about all nodes.', node_list_example),
         (404, 'Node not found.', None)]))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self):
        # This is a little terrible. The way to work out which node is currently
        # doing cluster maintenance is to lookup the lock.
        locks = etcd.get_existing_locks()
        maintainer = locks.get('/sflocks/sf/cluster/', {}).get('node')

        out = []
        for n in Nodes([]):
            node_out = n.external_view()
            node_out['is_cluster_maintainer'] = node_out['name'] == maintainer
            out.append(node_out)
        return out


node_events_example = """[
    ...,
    {
        "duration": null,
        "extra": {
            "cpu_available": 12,
            "cpu_load_1": 5.98,
            "cpu_load_15": 7.86,
            "cpu_load_5": 7.1,
            ...
        },
        "name": "sf-1",
        "message": "updated node resources and package versions",
        "timestamp": 1685330702.492032,
        "type": "resources"
    },
    ...
]"""


class NodeEventsEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'nodes', 'Get nodes event information.',
        [
            ('node', 'query', 'node', 'The name of a node.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100.', False)
        ],
        [(200, 'Event information about a single node.', node_events_example),
         (404, 'Node not found.', None)]))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.redirect_to_eventlog_node
    @api_base.log_token_use
    def get(self, node=None, event_type=None, limit=100):
        with eventlog.EventLog('node', node) as eventdb:
            return list(eventdb.read_events(limit=limit, event_type=event_type))


node_process_metrics_example = """[
    ...,
    ...
]"""


class NodeProcessMetricsEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'nodes', 'Get process metrics for a given node.',
        [
            ('node', 'query', 'node', 'The name of a node.', True)
        ],
        [(200, 'Process metrics for a single node.', node_process_metrics_example),
         (404, 'Node not found.', None)]))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self, node=None):
        n = Node.from_db(node, suppress_failure_audit=True)
        if not n:
            return sf_api.error(404, 'node not found')
        return n.process_metrics


class NodeMetadatasEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'nodes', 'Fetch metadata for a node.',
        [('node', 'query', 'node', 'The node to fetch metadata for.', True)],
        [(200, 'Node metadata, if any.', None),
         (404, 'Node not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def get(self, node=None):
        n = Node.from_db(node, suppress_failure_audit=True)
        if not n:
            return sf_api.error(404, 'node not found')
        return n.metadata

    @swag_from(api_base.swagger_helper(
        'nodes', 'Add metadata for a node.',
        [
            ('node', 'query', 'node', 'The node to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Node not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def post(self, node=None, key=None, value=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        n = Node.from_db(node, suppress_failure_audit=True)
        if not n:
            return sf_api.error(404, 'node not found')
        n.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'post'})
        n.add_metadata_key(key, value)


class NodeMetadataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'nodes', 'Update a metadata key for a node.',
        [
            ('node', 'query', 'node', 'The node to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Node not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def put(self, node=None, key=None, value=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        n = Node.from_db(node, suppress_failure_audit=True)
        if not n:
            return sf_api.error(404, 'node not found')
        n.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'put'})
        n.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'nodes', 'Delete a metadata key for a node.',
        [
            ('node', 'query', 'node', 'The node to remove a key from.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Node not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.caller_is_admin
    @api_base.log_token_use
    def delete(self, node=None, key=None, value=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        n = Node.from_db(node, suppress_failure_audit=True)
        if not n:
            return sf_api.error(404, 'node not found')
        n.add_event(
            EVENT_TYPE_AUDIT, 'delete metadata key request from REST API',
            extra={'key': key})
        n.remove_metadata_key(key)
