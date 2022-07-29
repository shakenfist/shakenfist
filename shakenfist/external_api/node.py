from functools import partial
from flask_jwt_extended import jwt_required
from flask_restful import fields
from flask_restful import marshal_with
import time


from shakenfist.baseobject import (
    active_states_filter as dbo_active_states_filter)
from shakenfist.blob import Blobs, placement_filter
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

        # This really shouldn't happen in the API layer, but it can't happen
        # in blobs.py because of circular imports. I need to think about that
        # more, and its a problem bigger than just this method.
        if time.time() - self.last_seen < 60:
            # If we have seen the node recently, we should try and be nice
            # about deleting resources on it
            for i in healthy_instances_on_node(self.fqdn):
                i.add_event2(
                    'Deleting instance as hosting node has been deleted')
                i.delete()

        blobs_to_remove = []
        with etcd.ThreadLocalReadOnlyCache():
            for b in Blobs([dbo_active_states_filter,
                            partial(placement_filter, self.fqdn)]):
                blobs_to_remove.append(b)
        for b in blobs_to_remove:
            b.add_event2(
                'Removing %s as a location, as node has been deleted' % self.fqdn)
            b.remove_location(self.fqdn)

        n.delete()
        return n.external_view()


class NodesEndpoint(api_base.Resource):
    @jwt_required()
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
