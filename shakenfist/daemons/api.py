import flask
import flask_restful
from flask_restful import fields, marshal_with, reqparse
import logging
import setproctitle
import time
import uuid

from shakenfist import config
from shakenfist.db import impl as db
from shakenfist import images
from shakenfist.net import impl as net
from shakenfist import util
from shakenfist import virt


logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


app = flask.Flask(__name__)
api = flask_restful.Api(app)


class Root(flask_restful.Resource):
    def get(self):
        resp = flask.Response(
            'Shaken Fist REST API service',
            mimetype='text/plain')
        resp.status_code = 200
        return resp


class Instance(flask_restful.Resource):
    def get(self, uuid):
        return db.get_instance(uuid)

    def delete(self, uuid):
        i = virt.from_db(uuid)
        n = net.from_db(i.get_network_uuid())
        i.delete(None)
        if n:
            with util.RecordedOperation('deallocate ip address', i) as _:
                n.update_dhcp()
        return True


class Instances(flask_restful.Resource):
    def get(self):
        return list(db.get_instances())

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('network', type=str)
        parser.add_argument('name', type=str)
        parser.add_argument('cpus', type=int)
        parser.add_argument('memory', type=int)
        parser.add_argument('disk', type=str)
        parser.add_argument('ssh_key', type=str)
        args = parser.parse_args()

        n = net.from_db(args['network'])
        if not n:
            return False
        n.create()

        newid = str(uuid.uuid4())
        instance = virt.from_definition(
            uuid=newid,
            name=args['name'],
            disks=args['disk'],
            memory_mb=args['memory'] * 1024,
            vcpus=args['cpus'],
            ssh_key=args['ssh_key']
        )

        with util.RecordedOperation('allocate ip address', instance) as _:
            n.allocate_ip_to_instance(instance)
            (mac, ip) = instance.get_network_details()
            db.create_network_interface(str(uuid.uuid4()), n.uuid, newid,
                                        mac, ip)
            n.update_dhcp()

        with util.RecordedOperation('instance creation', instance) as _:
            instance.create(None)

        return db.get_instance(newid)


class InstanceInterfaces(flask_restful.Resource):
    def get(self, uuid):
        return list(db.get_instance_interfaces(uuid))


class InstanceSnapshot(flask_restful.Resource):
    def post(self, uuid):
        parser = reqparse.RequestParser()
        parser.add_argument('all', type=bool)
        args = parser.parse_args()

        i = virt.from_db(uuid)
        return i.snapshot(all=args['all'])


class Image(flask_restful.Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('url', type=str)
        args = parser.parse_args()

        with util.RecordedOperation('cache image', args['url']) as ro:
            images.fetch_image(args['url'], recorded=ro)

        return True


class Network(flask_restful.Resource):
    def get(self, uuid):
        return db.get_network(uuid)

    def delete(self, uuid):
        n = net.from_db(uuid)
        if not n:
            return False

        n.remove_dhcp()
        db.delete_network(uuid)
        return True


class Networks(flask_restful.Resource):
    @marshal_with({
        'uuid': fields.String,
        'vxlan_id': fields.Integer,
        'netblock': fields.String,
        'provide_dhcp': fields.Boolean,
        'provide_nat': fields.Boolean,
        'owner': fields.String,
    })
    def get(self):
        return list(db.get_networks())

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('netblock', type=str)
        parser.add_argument('provide_dhcp', type=bool)
        parser.add_argument('provide_nat', type=bool)
        args = parser.parse_args()

        return(db.allocate_network(args['netblock'],
                                   args['provide_dhcp'],
                                   args['provide_nat']))


class Nodes(flask_restful.Resource):
    @marshal_with({
        'name': fields.String(attribute='fqdn'),
        'ip': fields.String,
        'lastseen': fields.DateTime(dt_format='rfc822'),
    })
    def get(self):
        return list(db.get_nodes())


api.add_resource(Root, '/')
api.add_resource(Instance, '/instances/<uuid>')
api.add_resource(Instances, '/instances')
api.add_resource(InstanceInterfaces, '/instances/<uuid>/interfaces')
api.add_resource(InstanceSnapshot, '/instances/<uuid>/snapshot')
api.add_resource(Image, '/images')
api.add_resource(Network, '/networks/<uuid>')
api.add_resource(Networks, '/networks')
api.add_resource(Nodes, '/nodes')


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf api')

    def run(self):
        app.run(host='0.0.0.0', port=config.parsed.get('API_PORT'), debug=True)
