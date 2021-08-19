from flask_jwt_extended import jwt_required
from flask_restful import fields
from flask_restful import marshal_with

from shakenfist.external_api import base as api_base
from shakenfist.node import Nodes


class NodesEndpoint(api_base.Resource):
    @jwt_required
    @api_base.caller_is_admin
    @marshal_with({
        'name': fields.String(attribute='fqdn'),
        'ip': fields.String,
        'lastseen': fields.Float,
        'version': fields.String,
    })
    def get(self):
        out = []
        for n in Nodes([]):
            out.append(n.external_view())
        return out
