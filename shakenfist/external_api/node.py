from flask_jwt_extended import jwt_required
from flask_restful import fields
from flask_restful import marshal_with
from shakenfist_utilities import api as sf_api


from shakenfist import eventlog
from shakenfist.external_api import base as api_base
from shakenfist.node import Node, Nodes


class NodeEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    def delete(self, node=None):
        n = Node.from_db(node)
        if not n:
            return sf_api.error(404, 'node not found')

        n.delete()
        return n.external_view()


class NodesEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    @marshal_with({
        'name': fields.String(attribute='fqdn'),
        'ip': fields.String,
        'state': fields.String,
        'lastseen': fields.Float,
        'version': fields.String,
        'is_etcd_master': fields.Boolean,
        'is_hypervisor': fields.Boolean,
        'is_network_node': fields.Boolean,
        'is_eventlog_node': fields.Boolean
    })
    def get(self):
        out = []
        for n in Nodes([]):
            out.append(n.external_view())
        return out


class NodeEventsEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    @api_base.redirect_to_eventlog_node
    def get(self, node=None):
        with eventlog.EventLog('node', node) as eventdb:
            return list(eventdb.read_events())
