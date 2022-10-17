from flask_jwt_extended import jwt_required
from flask_restful import fields
from flask_restful import marshal_with


from shakenfist import etcd
from shakenfist.external_api import base as api_base
from shakenfist.instance import healthy_instances_on_node
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
    })
    def get(self):
        out = []
        for n in Nodes([]):
            out.append(n.external_view())
        return out
