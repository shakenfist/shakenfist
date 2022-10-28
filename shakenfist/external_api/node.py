from flask_jwt_extended import jwt_required
from flask_restful import fields
from flask_restful import marshal_with


from shakenfist import etcd
from shakenfist import eventlog
from shakenfist.external_api import base as api_base
from shakenfist.node import Node, Nodes


class NodeEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    def delete(self, node=None):
        n = Node.from_db(node)
        if not n:
            return api_base.error(404, 'node not found')

        n.delete()
        return n.external_view()


class NodesEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    @marshal_with({
        'name': fields.String(attribute='fqdn'),
        'ip': fields.String,
        'state': fields.String,
        'lastseen': fields.Float,
        'version': fields.String,
        'is_etcd_master': fields.Boolean,
        'is_hypervisor': fields.Boolean,
        'is_network_node': fields.Boolean,
        'is_eventlog_node': fields.Boolean,
        'is_cluster_maintainer': fields.Boolean
    })
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


class NodeEventsEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.caller_is_admin
    @api_base.redirect_to_eventlog_node
    def get(self, node=None):
        with eventlog.EventLog('node', node) as eventdb:
            return list(eventdb.read_events())
