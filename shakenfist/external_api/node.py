from functools import partial
from flask_jwt_extended import jwt_required
from flask_restful import fields
from flask_restful import marshal_with
from shakenfist_utilities import api as sf_api


from shakenfist.baseobject import (
    active_states_filter as dbo_active_states_filter)
from shakenfist.blob import Blobs, placement_filter
from shakenfist import etcd
from shakenfist.instance import healthy_instances_on_node
from shakenfist.node import Node, Nodes


class NodeEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
    def delete(self, node=None):
        n = Node.from_db(node)
        if not n:
            return sf_api.error(404, 'node not found')

        # This really shouldn't happen in the API layer, but it can't happen
        # in blobs.py because of circular imports. I need to think about that
        # more, and its a problem bigger than just this method.
        for i in healthy_instances_on_node(n):
            n.add_event(
                'Deleting instance %s as hosting node has been deleted' % i.uuid)
            i.add_event(
                'Deleting instance as hosting node %s has been deleted' % n.uuid)
            i.delete()

        blobs_to_remove = []
        with etcd.ThreadLocalReadOnlyCache():
            for b in Blobs([dbo_active_states_filter,
                            partial(placement_filter, n.fqdn)]):
                blobs_to_remove.append(b)
        for b in blobs_to_remove:
            n.add_event(
                'Deleting blob %s location as hosting node has been deleted' % b.uuid)
            b.add_event(
                'Deleting blob location as hosting node %s has been deleted' % n.uuid)
            b.remove_location(n.fqdn)

        n.delete()
        return n.external_view()


class NodesEndpoint(sf_api.Resource):
    @jwt_required()
    @sf_api.caller_is_admin
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
