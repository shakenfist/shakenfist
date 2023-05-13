# Documentation state:
#   - Has metadata calls:
#   - OpenAPI complete:
#   - Covered in user or operator docs:
#   - API reference docs exist:
#        - and link to OpenAPI docs:
#        - and include examples:
#   - Has complete CI coverage:

from flask_restful import fields
from flask_restful import marshal_with
from shakenfist_utilities import api as sf_api


from shakenfist import etcd
from shakenfist import eventlog
from shakenfist.external_api import base as api_base
from shakenfist.node import Node, Nodes


class NodeEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.log_token_use
    def delete(self, node=None):
        n = Node.from_db(node)
        if not n:
            return sf_api.error(404, 'node not found')

        n.delete()
        return n.external_view()


class NodesEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @marshal_with({
        'name': fields.String(attribute='fqdn'),
        'ip': fields.String,
        'state': fields.String,
        'lastseen': fields.Float,
        'version': fields.String,
        'release': fields.String,
        'is_etcd_master': fields.Boolean,
        'is_hypervisor': fields.Boolean,
        'is_network_node': fields.Boolean,
        'is_eventlog_node': fields.Boolean,
        'is_cluster_maintainer': fields.Boolean
    })
    @api_base.log_token_use
    def get(self):
        # This is a little terrible. The way to work out which node is currently
        # doing cluster maintenance is to lookup the lock.
        locks = etcd.get_existing_locks()
        maintainer = locks.get('/sflocks/sf/cluster/', {}).get('node')

        out = []
        for n in Nodes([]):
            node_out = n.external_view()
            node_out['is_cluster_maintainer'] = node_out['fqdn'] == maintainer
            out.append(node_out)
        return out


class NodeEventsEndpoint(sf_api.Resource):
    @api_base.verify_token
    @sf_api.caller_is_admin
    @api_base.redirect_to_eventlog_node
    @api_base.log_token_use
    def get(self, node=None):
        with eventlog.EventLog('node', node) as eventdb:
            return list(eventdb.read_events())
