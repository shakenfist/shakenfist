import flask
import flask_restful
from flask_restful import fields, marshal_with, reqparse
import json
import logging
import randmac
import requests
import setproctitle
import sys
import time
import traceback
import uuid

from shakenfist.client import apiclient
from shakenfist import config
from shakenfist import db
from shakenfist import images
from shakenfist import net
from shakenfist import util
from shakenfist import virt


logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


TESTING = False


def error(status_code, message):
    global TESTING

    body = {
        'error': message,
        'status': status_code
    }

    if TESTING or config.parsed.get('INCLUDE_TRACEBACKS') == '1':
        _, _, tb = sys.exc_info()
        if tb:
            body['traceback'] = traceback.format_exc()

    resp = flask.Response(json.dumps(body),
                          mimetype='application/json')
    resp.status_code = status_code
    LOG.info('Returning API error: %d, %s' % (status_code, message))
    return resp


def generic_catch_exception(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except:
            return error(500, 'server error')
    return wrapper


class Resource(flask_restful.Resource):
    method_decorators = [generic_catch_exception]


def arg_is_instance_uuid(func):
    # Method uses the instance from the db
    def wrapper(*args, **kwargs):
        if 'instance_uuid' in kwargs:
            kwargs['instance_from_db'] = db.get_instance(
                kwargs['instance_uuid'])
        if not kwargs.get('instance_from_db'):
            return error(404, 'instance not found')

        return func(*args, **kwargs)
    return wrapper


def arg_is_instance_uuid_as_virt(func):
    # Method uses the rehydrated instance
    def wrapper(*args, **kwargs):
        if 'instance_uuid' in kwargs:
            kwargs['instance_from_db_virt'] = virt.from_db(
                kwargs['instance_uuid']
            )
        if not kwargs.get('instance_from_db_virt'):
            return error(404, 'instance not found')

        return func(*args, **kwargs)
    return wrapper


def redirect_instance_request(func):
    # Redirect method to the hypervisor hosting the instance
    def wrapper(*args, **kwargs):
        i = kwargs.get('instance_from_db_virt')
        if i and i.db_entry['node'] != config.parsed.get('NODE_NAME'):
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                'http://%s:%d%s'
                % (i.db_entry['node'],
                   config.parsed.get('API_PORT'),
                   flask.request.environ['PATH_INFO']),
                data=flask.request.get_json())

            LOG.info('Returning proxied request: %d, %s'
                     % (r.status_code, r.text))
            resp = flask.Response(r.text,
                                  mimetype='application/json')
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def arg_is_network_uuid(func):
    # Method uses the network from the db
    def wrapper(*args, **kwargs):
        if 'network_uuid' in kwargs:
            kwargs['network_from_db'] = db.get_network(
                kwargs['network_uuid'])
        if not kwargs.get('network_from_db'):
            return error(404, 'network not found')

        return func(*args, **kwargs)
    return wrapper


app = flask.Flask(__name__)
api = flask_restful.Api(app, catch_all_404s=False)


class Root(Resource):
    def get(self):
        resp = flask.Response(
            'Shaken Fist REST API service',
            mimetype='text/plain')
        resp.status_code = 200
        return resp


class Instance(Resource):
    @arg_is_instance_uuid
    def get(self, instance_uuid=None, instance_from_db=None):
        return instance_from_db

    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def delete(self, instance_uuid=None, instance_from_db_virt=None):
        instance_networks = []
        for iface in list(db.get_instance_interfaces(instance_uuid)):
            if not iface['network_uuid'] in instance_networks:
                instance_networks.append(iface['network_uuid'])

        host_networks = []
        for inst in list(db.get_instances(local_only=True)):
            if not inst['uuid'] == instance_uuid:
                for iface in db.get_instance_interfaces(inst['uuid']):
                    if not iface['network_uuid'] in host_networks:
                        host_networks.append(iface['network_uuid'])

        instance_from_db_virt.delete(None)

        for network in instance_networks:
            n = net.from_db(network)
            if n:
                if network in host_networks:
                    with util.RecordedOperation('deallocate ip address', instance_from_db_virt) as _:
                        n.update_dhcp()
                else:
                    with util.RecordedOperation('remove network', n) as _:
                        n.delete()


class Instances(Resource):
    def get(self):
        return list(db.get_instances())

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('name', type=str)
        parser.add_argument('cpus', type=int)
        parser.add_argument('memory', type=int)
        parser.add_argument('network', type=str, action='append')
        parser.add_argument('disk', type=str, action='append')
        parser.add_argument('ssh_key', type=str)
        parser.add_argument('user_data', type=str)
        args = parser.parse_args()

        new_instance_uuid = str(uuid.uuid4())
        instance = virt.from_definition(
            uuid=new_instance_uuid,
            name=args['name'],
            disks=args['disk'],
            memory_mb=args['memory'] * 1024,
            vcpus=args['cpus'],
            ssh_key=args['ssh_key'],
            user_data=args['user_data']
        )

        with util.RecordedOperation('allocate ip addresses', instance) as _:
            order = 0
            for network in args['network']:
                n = net.from_db(network)
                if n:
                    ip = n.ipmanager.get_random_free_address()
                    n.persist_ipmanager()

                    macaddr = str(randmac.RandMac(
                        '00:00:00:00:00:00', False)).lstrip('\'').rstrip('\'')
                    db.create_network_interface(
                        str(uuid.uuid4()), network, new_instance_uuid, macaddr, ip, order)
                    order += 1

                    n.create()
                    n.ensure_mesh()
                    n.update_dhcp()

        with util.RecordedOperation('instance creation', instance) as _:
            instance.create(None)

        return db.get_instance(new_instance_uuid)


class InstanceInterfaces(Resource):
    @arg_is_instance_uuid
    def get(self, instance_uuid=None, instance_from_db=None):
        return list(db.get_instance_interfaces(instance_uuid))


class InstanceSnapshot(Resource):
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db_virt=None):
        parser = reqparse.RequestParser()
        parser.add_argument('all', type=bool)
        args = parser.parse_args()

        return instance_from_db_virt.snapshot(all=args['all'])


class InstanceRebootSoft(Resource):
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db_virt=None):
        return instance_from_db_virt.reboot(hard=False)


class InstanceRebootHard(Resource):
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db_virt=None):
        return instance_from_db_virt.reboot(hard=True)


class InstancePowerOff(Resource):
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db_virt=None):
        return instance_from_db_virt.power_off()


class InstancePowerOn(Resource):
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db_virt=None):
        return instance_from_db_virt.power_on()


class InstancePause(Resource):
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db_virt=None):
        return instance_from_db_virt.pause()


class InstanceUnpause(Resource):
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db_virt=None):
        return instance_from_db_virt.unpause()


class Image(Resource):
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('url', type=str)
        args = parser.parse_args()

        with util.RecordedOperation('cache image', args['url']) as ro:
            images.fetch_image(args['url'], recorded=ro)


class Network(Resource):
    @arg_is_network_uuid
    def get(self, network_uuid=None, network_from_db=None):
        return network_from_db

    @arg_is_network_uuid
    def delete(self, network_uuid=None, network_from_db=None):
        if network_uuid == 'floating':
            return error(403, 'you cannot delete the floating network')

        n = net.from_db(network_uuid)

        # We only delete unused networks
        if len(list(db.get_network_interfaces(network_uuid))) > 0:
            return error(403, 'you cannot delete an in use network')

        n.remove_dhcp()
        n.delete()

        if n.floating_gateway:
            floating_network = net.from_db('floating')
            floating_network.ipmanager.release(n.floating_gateway)
            floating_network.persist_ipmanager()

        db.delete_network(network_uuid)


class Networks(Resource):
    @marshal_with({
        'uuid': fields.String,
        'vxlan_id': fields.Integer,
        'netblock': fields.String,
        'provide_dhcp': fields.Boolean,
        'provide_nat': fields.Boolean,
        'owner': fields.String,
        'name': fields.String,
    })
    def get(self):
        return list(db.get_networks())

    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('netblock', type=str)
        parser.add_argument('provide_dhcp', type=bool)
        parser.add_argument('provide_nat', type=bool)
        parser.add_argument('name', type=str)
        args = parser.parse_args()

        network = db.allocate_network(args['netblock'],
                                      args['provide_dhcp'],
                                      args['provide_nat'],
                                      args['name'])

        # Networks should immediately appear on the network node
        if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
            n = net.from_db(network['uuid'])
            if not n:
                return error(404, 'network not found')

            n.create()
            n.ensure_mesh()
        else:
            requests.request(
                'put',
                ('http://%s:%d/deploy_network_node'
                 % (config.parsed.get('NETWORK_NODE_IP'),
                    config.parsed.get('API_PORT'))),
                data={
                    'uuid': network['uuid']
                })

        return network


class Nodes(Resource):
    @marshal_with({
        'name': fields.String(attribute='fqdn'),
        'ip': fields.String,
        'lastseen': fields.DateTime(dt_format='rfc822'),
    })
    def get(self):
        return list(db.get_nodes())


# Internal APIs


class DeployNetworkNode(Resource):
    def put(self):
        parser = reqparse.RequestParser()
        parser.add_argument('uuid', type=str)
        args = parser.parse_args()

        n = net.from_db(args['uuid'])
        if not n:
            return error(404, 'network not found')

        n.create()
        n.ensure_mesh()


class UpdateDHCP(Resource):
    def put(self):
        parser = reqparse.RequestParser()
        parser.add_argument('uuid', type=str)
        args = parser.parse_args()

        n = net.from_db(args['uuid'])
        if not n:
            return error(404, 'network not found')

        n.update_dhcp()


class RemoveDHCP(Resource):
    def put(self):
        parser = reqparse.RequestParser()
        parser.add_argument('uuid', type=str)
        args = parser.parse_args()

        n = net.from_db(args['uuid'])
        if not n:
            return error(404, 'network not found')

        n.remove_dhcp()


api.add_resource(Root, '/')
api.add_resource(Instances, '/instances')
api.add_resource(Instance, '/instances/<instance_uuid>')
api.add_resource(InstanceInterfaces, '/instances/<instance_uuid>/interfaces')
api.add_resource(InstanceSnapshot, '/instances/<instance_uuid>/snapshot')
api.add_resource(InstanceRebootSoft, '/instances/<instance_uuid>/rebootsoft')
api.add_resource(InstanceRebootHard, '/instances/<instance_uuid>/reboothard')
api.add_resource(InstancePowerOff, '/instances/<instance_uuid>/poweroff')
api.add_resource(InstancePowerOn, '/instances/<instance_uuid>/poweron')
api.add_resource(InstancePause, '/instances/<instance_uuid>/pause')
api.add_resource(InstanceUnpause, '/instances/<instance_uuid>/unpause')
api.add_resource(Image, '/images')
api.add_resource(Network, '/networks/<network_uuid>')
api.add_resource(Networks, '/networks')
api.add_resource(Nodes, '/nodes')

api.add_resource(DeployNetworkNode, '/deploy_network_node')
api.add_resource(UpdateDHCP, '/update_dhcp')
api.add_resource(RemoveDHCP, '/remove_dhcp')


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf api')

    def run(self):
        app.run(
            host='0.0.0.0',
            port=config.parsed.get('API_PORT'))
