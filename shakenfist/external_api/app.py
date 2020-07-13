#################################################################################
# DEAR FUTURE ME... The order of decorators on these API methods deeply deeply  #
# matters. We need to verify auth before anything, and we need to fetch things  #
# from the database before we make decisions based on those things. So remember #
# the outer decorator is executed first!                                        #
#################################################################################

import base64
import bcrypt
import flask
from flask_jwt_extended import create_access_token
from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import JWTManager
from flask_jwt_extended import jwt_required
import flask_restful
from flask_restful import fields
from flask_restful import marshal_with
import ipaddress
import json
import logging
from logging import handlers as logging_handlers
import os
import re
import requests
import setproctitle
import sys
import traceback
import uuid

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist import db
from shakenfist import db
from shakenfist import images
from shakenfist import net
from shakenfist import scheduler
from shakenfist import util
from shakenfist import virt


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


TESTING = False
SCHEDULER = None


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
    LOG.error('Returning API error: %d, %s\n    %s'
              % (status_code, message, '\n    '.join(body.get('traceback', '').split('\n'))))
    return resp


def flask_get_post_body():
    j = {}
    try:
        j = flask.request.get_json(force=True)
    except Exception:
        if flask.request.data:
            try:
                j = json.loads(flask.request.data)
            except Exception:
                pass
    return j


def generic_wrapper(func):
    def wrapper(*args, **kwargs):
        try:
            j = flask_get_post_body()

            if j:
                for key in j:
                    if key == 'uuid':
                        destkey = 'passed_uuid'
                    else:
                        destkey = key
                    kwargs[destkey] = j[key]

            formatted_headers = []
            for header in flask.request.headers:
                formatted_headers.append(str(header))

            LOG.info('API request: %s %s\n    Headers:\n        %s\n    Args: %s\n    KWargs: %s'
                     % (flask.request.method, flask.request.url, '\n        '.join(formatted_headers), args, kwargs))
            return func(*args, **kwargs)
        except Exception:
            return error(500, 'server error')
    return wrapper


class Resource(flask_restful.Resource):
    method_decorators = [generic_wrapper]


def caller_is_admin(func):
    # Ensure only users in the "system" namespace can call this method
    def wrapper(*args, **kwargs):
        if get_jwt_identity() != 'system':
            return error(401, 'unauthorized')

        return func(*args, **kwargs)
    return wrapper


def arg_is_instance_uuid(func):
    # Method uses the instance from the db
    def wrapper(*args, **kwargs):
        if 'instance_uuid' in kwargs:
            kwargs['instance_from_db'] = db.get_instance(
                kwargs['instance_uuid'])
        if not kwargs.get('instance_from_db'):
            LOG.info(
                'instance(%s): instance not found, genuinely missing' % kwargs.get('instance_uuid'))
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
            LOG.info(
                'instance(%s): instance not found, genuinely missing' % kwargs.get('instance_uuid'))
            return error(404, 'instance not found')

        return func(*args, **kwargs)
    return wrapper


def redirect_instance_request(func):
    # Redirect method to the hypervisor hosting the instance
    def wrapper(*args, **kwargs):
        i = kwargs.get('instance_from_db_virt')
        if i and i.db_entry['node'] != config.parsed.get('NODE_NAME'):
            url = 'http://%s:%d%s' % (i.db_entry['node'],
                                      config.parsed.get('API_PORT'),
                                      flask.request.environ['PATH_INFO'])
            api_token = util.get_api_token(
                'http://%s:%d' % (i.db_entry['node'],
                                  config.parsed.get('API_PORT')),
                namespace=get_jwt_identity())
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=json.dumps(flask_get_post_body()),
                headers={'Authorization': api_token,
                         'User-Agent': util.get_user_agent()})

            LOG.info('Proxied %s %s returns: %d, %s'
                     % (flask.request.environ['REQUEST_METHOD'], url,
                        r.status_code, r.text))
            resp = flask.Response(r.text,
                                  mimetype='application/json')
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def requires_instance_ownership(func):
    # Requires that @arg_is_instance_uuid has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('instance_from_db'):
            LOG.info('instance(%s): instance not found, kwarg missing'
                     % kwargs['instance_uuid'])
            return error(404, 'instance not found')

        if get_jwt_identity() not in [kwargs['instance_from_db']['namespace'], 'system']:
            LOG.info('instance(%s): instance not found, ownership test in decorator'
                     % kwargs['instance_uuid'])
            return error(404, 'instance not found')

        return func(*args, **kwargs)
    return wrapper


def arg_is_network_uuid(func):
    # Method uses the network from the db
    def wrapper(*args, **kwargs):
        if 'network_uuid' in kwargs:
            kwargs['network_from_db'] = db.get_network(
                kwargs['network_uuid'])
        if not kwargs.get('network_from_db'):
            LOG.info('network(%s): network not found, genuinely missing' %
                     kwargs['network_uuid'])
            return error(404, 'network not found')

        return func(*args, **kwargs)
    return wrapper


def redirect_to_network_node(func):
    # Redirect method to the network node
    def wrapper(*args, **kwargs):
        if config.parsed.get('NODE_IP') != config.parsed.get('NETWORK_NODE_IP'):
            admin_token = util.get_api_token(
                'http://%s:%d' % (config.parsed.get('NETWORK_NODE_IP'),
                                  config.parsed.get('API_PORT')),
                namespace='system')
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                'http://%s:%d%s'
                % (config.parsed.get('NETWORK_NODE_IP'),
                   config.parsed.get('API_PORT'),
                   flask.request.environ['PATH_INFO']),
                data=json.dumps(flask.request.get_json()),
                headers={'Authorization': admin_token,
                         'User-Agent': util.get_user_agent()})

            LOG.info('Returning proxied request: %d, %s'
                     % (r.status_code, r.text))
            resp = flask.Response(r.text,
                                  mimetype='application/json')
            resp.status_code = r.status_code
            return resp

        return func(*args, **kwargs)
    return wrapper


def requires_network_ownership(func):
    # Requires that @arg_is_network_uuid has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('network_from_db'):
            LOG.info('network(%s): network not found, kwarg missing'
                     % kwargs['network_uuid'])
            return error(404, 'network not found')

        if get_jwt_identity() not in [kwargs['network_from_db']['namespace'], 'system']:
            LOG.info('network(%s): network not found, ownership test in decorator'
                     % kwargs['network_uuid'])
            return error(404, 'network not found')

        return func(*args, **kwargs)
    return wrapper


def _metadata_putpost(meta_type, owner, key, value):
    if meta_type not in ['namespace', 'instance', 'network']:
        return error(500, 'invalid meta_type %s' % meta_type)
    if not key:
        return error(400, 'no key specified')
    if not value:
        return error(400, 'no value specified')

    with db.get_lock('sf/metadata/%s/%s' % (meta_type, owner)) as _:
        md = db.get_metadata(meta_type, owner)
        if md is None:
            md = {}
        md[key] = value
        db.persist_metadata(meta_type, owner, md)


app = flask.Flask(__name__)
api = flask_restful.Api(app, catch_all_404s=False)
app.config['JWT_SECRET_KEY'] = config.parsed.get('AUTH_SECRET_SEED')
jwt = JWTManager(app)


@app.before_request
def log_request_info():
    output = 'API request headers:\n'
    for header, value in flask.request.headers:
        output += '    %s: %s\n' % (header, value)
    output += 'API request body: %s' % flask.request.get_data()

    app.logger.info(output)


class Root(Resource):
    def get(self):
        resp = flask.Response(
            'Shaken Fist REST API service',
            mimetype='text/plain')
        resp.status_code = 200
        return resp


class Auth(Resource):
    def _get_keys(self, namespace):
        rec = db.get_namespace(namespace)
        if not rec:
            return (None, [])

        keys = []
        for key_name in rec.get('keys', {}):
            keys.append(base64.b64decode(rec['keys'][key_name]))
        return (rec.get('service_key'), keys)

    def post(self, namespace=None, key=None):
        if not namespace:
            return error(400, 'missing namespace in request')
        if not key:
            return error(400, 'missing key in request')

        service_key, keys = self._get_keys(namespace)
        if service_key and key == service_key:
            return {'access_token': create_access_token(identity=namespace)}
        for possible_key in keys:
            if bcrypt.checkpw(key.encode('utf-8'), possible_key):
                return {'access_token': create_access_token(identity=namespace)}

        return error(401, 'unauthorized')


class AuthNamespaces(Resource):
    @jwt_required
    @caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        if not namespace:
            return error(400, 'no namespace specified')

        with db.get_lock('sf/namespace') as _:
            rec = db.get_namespace(namespace)
            if not rec:
                rec = {
                    'name': namespace,
                    'keys': {}
                }

            # Allow shortcut of creating key at same time as the namespace
            if key_name:
                if not key:
                    return error(400, 'no key specified')
                if key_name == 'service_key':
                    return error(403, 'illegal key name')

                encoded = str(base64.b64encode(bcrypt.hashpw(
                    key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
                rec['keys'][key_name] = encoded

            # Initialise metadata
            db.persist_metadata('namespace', namespace, {})
            db.persist_namespace(namespace, rec)

        return namespace

    @jwt_required
    @caller_is_admin
    def get(self):
        out = []
        for rec in db.list_namespaces():
            out.append(rec['name'])
        return out


class AuthNamespace(Resource):
    @jwt_required
    @caller_is_admin
    def delete(self, namespace):
        if not namespace:
            return error(400, 'no namespace specified')
        if namespace == 'system':
            return error(403, 'you cannot delete the system namespace')

        # The namespace must be empty
        instances = []
        deleted_instances = []
        for i in db.get_instances(all=True, namespace=namespace):
            if i['state'] == 'deleted':
                deleted_instances.append(i['uuid'])
            else:
                instances.append(i['uuid'])
        if len(instances) > 0:
            return error(400, 'you cannot delete a namespace with instances')

        networks = []
        deleted_networks = []
        for n in db.get_networks(all=True, namespace=namespace):
            if n['state'] == 'deleted':
                deleted_networks.append(n['uuid'])
            else:
                networks.append(n['uuid'])
        if len(networks) > 0:
            return error(400, 'you cannot delete a namespace with networks')

        for instance_uuid in deleted_instances:
            db.hard_delete_instance(instance_uuid)
        for network_uuid in deleted_networks:
            db.hard_delete_network(network_uuid)

        db.delete_namespace(namespace)
        db.delete_metadata('namespace', namespace)


def _namespace_keys_putpost(namespace=None, key_name=None, key=None):
    if not namespace:
        return error(400, 'no namespace specified')
    if not key_name:
        return error(400, 'no key name specified')
    if not key:
        return error(400, 'no key specified')
    if key_name == 'service_key':
        return error(403, 'illegal key name')

    with db.get_lock('sf/namespace') as _:
        rec = db.get_namespace(namespace)
        if not rec:
            return error(404, 'namespace does not exist')

        encoded = str(base64.b64encode(bcrypt.hashpw(
            key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
        rec['keys'][key_name] = encoded

        db.persist_namespace(namespace, rec)

    return key_name


class AuthNamespaceKeys(Resource):
    @jwt_required
    @caller_is_admin
    def get(self, namespace=None):
        rec = db.get_namespace(namespace)
        if not rec:
            return error(404, 'namespace does not exist')

        out = []
        for keyname in rec['keys']:
            out.append(keyname)
        return out

    @jwt_required
    @caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        return _namespace_keys_putpost(namespace, key_name, key)


class AuthNamespaceKey(Resource):
    @jwt_required
    @caller_is_admin
    def put(self, namespace=None, key_name=None, key=None):
        rec = db.get_namespace(namespace)
        if not rec:
            return error(404, 'namespace does not exist')
        if key_name not in rec['keys']:
            return error(404, 'key does not exist')

        return _namespace_keys_putpost(namespace, key_name, key)

    @jwt_required
    @caller_is_admin
    def delete(self, namespace, key_name):
        if not namespace:
            return error(400, 'no namespace specified')
        if not key_name:
            return error(400, 'no key name specified')

        with db.get_lock('sf/namespace/%s' % namespace) as _:
            ns = db.get_namespace(namespace)
            if ns.get('keys') and key_name in ns['keys']:
                del ns['keys'][key_name]
            else:
                return error(404, 'key name not found in namespace')
            db.persist_namespace(namespace, ns)


class AuthMetadatas(Resource):
    @jwt_required
    @caller_is_admin
    def get(self, namespace):
        md = db.get_metadata('namespace', namespace)
        if not md:
            return {}
        return md

    @jwt_required
    @caller_is_admin
    def post(self, namespace, key=None, value=None):
        return _metadata_putpost('namespace', namespace, key, value)


class AuthMetadata(Resource):
    @jwt_required
    @caller_is_admin
    def put(self, namespace, key=None, value=None):
        return _metadata_putpost('namespace', namespace, key, value)

    @jwt_required
    @caller_is_admin
    def delete(self, namespace, key=None, value=None):
        if not key:
            return error(400, 'no key specified')

        with db.get_lock('sf/metadata/namespace/%s' % namespace) as _:
            md = db.get_metadata('namespace', namespace)
            if md is None or key not in md:
                return error(404, 'key not found')
            del md[key]
            db.persist_metadata('namespace', namespace, md)


class Instance(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        db.add_event('instance', instance_uuid, 'api', 'get', None, None)
        return instance_from_db

    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def delete(self, instance_uuid=None, instance_from_db=None, instance_from_db_virt=None):
        # Check if instance has already been deleted
        if instance_from_db['state'] == 'deleted':
            return error(404, 'instance not found')

        with db.get_lock('/sf/instance/%s' % instance_uuid) as _:
            db.add_event('instance', instance_uuid,
                         'api', 'delete', None, None)

            instance_networks = []
            for iface in list(db.get_instance_interfaces(instance_uuid)):
                if not iface['network_uuid'] in instance_networks:
                    instance_networks.append(iface['network_uuid'])
                    db.update_network_interface_state(iface['uuid'], 'deleted')

            host_networks = []
            for inst in list(db.get_instances(only_node=config.parsed.get('NODE_NAME'))):
                if not inst['uuid'] == instance_uuid:
                    for iface in db.get_instance_interfaces(inst['uuid']):
                        if not iface['network_uuid'] in host_networks:
                            host_networks.append(iface['network_uuid'])

            instance_from_db_virt.delete()

            for network in instance_networks:
                n = net.from_db(network)
                if n:
                    if network in host_networks:
                        with util.RecordedOperation('deallocate ip address',
                                                    instance_from_db_virt) as _:
                            n.update_dhcp()
                    else:
                        with util.RecordedOperation('remove network', n) as _:
                            n.delete()


class Instances(Resource):
    @jwt_required
    def get(self, all=False):
        return list(db.get_instances(all=all, namespace=get_jwt_identity()))

    @jwt_required
    def post(self, name=None, cpus=None, memory=None, network=None,
             disk=None, ssh_key=None, user_data=None, placed_on=None, namespace=None,
             instance_uuid=None):
        global SCHEDULER

        # We need to sanitise the name so its safe for DNS
        name = re.sub(r'([^a-zA-Z0-9_\-])', '', name)

        if not namespace:
            namespace = get_jwt_identity()

        # If accessing a foreign namespace, we need to be an admin
        if get_jwt_identity() not in [namespace, 'system']:
            return error(401,
                         'only admins can create resources in a different namespace')

        # The instance needs to exist in the DB before network interfaces are created
        if not instance_uuid:
            instance_uuid = str(uuid.uuid4())
            db.add_event('instance', instance_uuid,
                         'uuid allocated', None, None, None)

        # Create instance object
        instance = virt.from_db(instance_uuid)
        if instance:
            if get_jwt_identity() not in [instance.db_entry['namespace'], 'system']:
                LOG.info(
                    'instance(%s): instance not found, ownership test' % instance_uuid)
                return error(404, 'instance not found')

        if not instance:
            instance = virt.from_definition(
                uuid=instance_uuid,
                name=name,
                disks=disk,
                memory_mb=memory,
                vcpus=cpus,
                ssh_key=ssh_key,
                user_data=user_data,
                owner=namespace
            )

        if not SCHEDULER:
            SCHEDULER = scheduler.Scheduler()

        # Have we been placed?
        if not placed_on:
            candidates = SCHEDULER.place_instance(instance, network)
            if len(candidates) == 0:
                db.add_event('instance', instance_uuid,
                             'schedule', 'failed', None, 'insufficient resources')
                db.update_instance_state(instance_uuid, 'error')
                return error(507, 'insufficient capacity')

            placed_on = candidates[0]
            db.place_instance(instance_uuid, placed_on)
            db.add_event('instance', instance_uuid,
                         'placement', None, None, placed_on)

        else:
            try:
                candidates = SCHEDULER.place_instance(
                    instance, network, candidates=[placed_on])
                if len(candidates) == 0:
                    db.add_event('instance', instance_uuid,
                                 'schedule', 'failed', None, 'insufficient resources')
                    db.update_instance_state(instance_uuid, 'error')
                    return error(507, 'insufficient capacity')
            except scheduler.CandidateNodeNotFoundException as e:
                return error(404, 'node not found: %s' % e)

        # Have we been placed on a different node?
        if not placed_on == config.parsed.get('NODE_NAME'):
            body = flask_get_post_body()
            body['placed_on'] = placed_on
            body['instance_uuid'] = instance_uuid
            body['namespace'] = namespace

            token = util.get_api_token(
                'http://%s:%d' % (placed_on, config.parsed.get('API_PORT')),
                namespace=namespace)
            r = requests.request('POST',
                                 'http://%s:%d/instances'
                                 % (placed_on,
                                    config.parsed.get('API_PORT')),
                                 data=json.dumps(body),
                                 headers={'Authorization': token,
                                          'User-Agent': util.get_user_agent()})

            LOG.info('Returning proxied request: %d, %s'
                     % (r.status_code, r.text))
            resp = flask.Response(r.text,
                                  mimetype='application/json')
            resp.status_code = r.status_code
            return resp

        # Check we can get the required IPs
        nets = {}
        allocations = {}

        def error_with_cleanup(status_code, message):
            for network_uuid in allocations:
                n = net.from_db(network_uuid)
                for addr, _ in allocations[network_uuid]:
                    with db.get_lock('sf/ipmanager/%s' % n.uuid, ttl=120) as _:
                        ipm = db.get_ipmanager(n.uuid)
                        ipm.release(addr)
                        db.persist_ipmanager(n.uuid, ipm.save())
            return error(status_code, message)

        order = 0
        if network:
            for netdesc in network:
                if 'network_uuid' not in netdesc or not netdesc['network_uuid']:
                    return error_with_cleanup(404, 'network not specified')

                if netdesc['network_uuid'] not in nets:
                    n = net.from_db(netdesc['network_uuid'])
                    if not n:
                        return error_with_cleanup(
                            404, 'network %s not found' % netdesc['network_uuid'])
                    nets[netdesc['network_uuid']] = n
                    n.create()

                with db.get_lock('sf/ipmanager/%s' % netdesc['network_uuid'],
                                 ttl=120) as _:
                    db.add_event('network', netdesc['network_uuid'], 'allocate address',
                                 None, None, instance_uuid)
                    allocations.setdefault(netdesc['network_uuid'], [])
                    ipm = db.get_ipmanager(netdesc['network_uuid'])
                    if 'address' not in netdesc or not netdesc['address']:
                        netdesc['address'] = ipm.get_random_free_address()
                    else:
                        if not ipm.reserve(netdesc['address']):
                            return error_with_cleanup(409, 'address %s in use' %
                                               netdesc['address'])
                    db.persist_ipmanager(netdesc['network_uuid'], ipm.save())
                    allocations[netdesc['network_uuid']].append(
                        (netdesc['address'], order))

                if 'model' not in netdesc or not netdesc['model']:
                    netdesc['model'] = 'virtio'

                db.create_network_interface(
                    str(uuid.uuid4()), netdesc, instance_uuid, order)

                order += 1

        # Initialise metadata
        db.persist_metadata('instance', instance_uuid, {})

        # Now we can start the instance
        with db.get_lock('sf/instance/%s' % instance.db_entry['uuid'], ttl=900) as lock:
            with util.RecordedOperation('ensure networks exist', instance) as _:
                for network_uuid in nets:
                    n = nets[network_uuid]
                    n.ensure_mesh()
                    n.update_dhcp()

            with util.RecordedOperation('instance creation', instance) as _:
                instance.create(lock=lock)

            for iface in db.get_instance_interfaces(instance.db_entry['uuid']):
                db.update_network_interface_state(iface['uuid'], 'created')

            return db.get_instance(instance_uuid)


class InstanceInterfaces(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        db.add_event('instance', instance_uuid,
                     'api', 'get interfaces', None, None)
        return list(db.get_instance_interfaces(instance_uuid))


class InstanceEvents(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        db.add_event('instance', instance_uuid,
                     'api', 'get events', None, None)
        return list(db.get_events('instance', instance_uuid))


class InstanceSnapshot(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db=None, instance_from_db_virt=None, all=None):
        snap_uuid = instance_from_db_virt.snapshot(all=all)
        db.add_event('instance', instance_uuid,
                     'api', 'snapshot (all=%s)' % all,
                     None, snap_uuid)
        db.add_event('snapshot', snap_uuid,
                     'api', 'create', None, None)
        return snap_uuid

    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        db.add_event('instance', instance_uuid,
                     'api', 'get', None, None)
        out = []
        for snap in db.get_instance_snapshots(instance_uuid):
            snap['created'] = snap['created']
            out.append(snap)
        return out


class InstanceRebootSoft(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db=None, instance_from_db_virt=None):
        db.add_event('instance', instance_uuid,
                     'api', 'soft reboot', None, None)
        return instance_from_db_virt.reboot(hard=False)


class InstanceRebootHard(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db=None, instance_from_db_virt=None):
        db.add_event('instance', instance_uuid,
                     'api', 'hard reboot', None, None)
        return instance_from_db_virt.reboot(hard=True)


class InstancePowerOff(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db=None, instance_from_db_virt=None):
        db.add_event('instance', instance_uuid,
                     'api', 'poweroff', None, None)
        return instance_from_db_virt.power_off()


class InstancePowerOn(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db=None, instance_from_db_virt=None):
        db.add_event('instance', instance_uuid,
                     'api', 'poweron', None, None)
        return instance_from_db_virt.power_on()


class InstancePause(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db=None, instance_from_db_virt=None):
        db.add_event('instance', instance_uuid, 'api', 'pause', None, None)
        return instance_from_db_virt.pause()


class InstanceUnpause(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @arg_is_instance_uuid_as_virt
    @redirect_instance_request
    def post(self, instance_uuid=None, instance_from_db=None, instance_from_db_virt=None):
        db.add_event('instance', instance_uuid,
                     'api', 'unpause', None, None)
        return instance_from_db_virt.unpause()


class Interface(Resource):
    @jwt_required
    @redirect_to_network_node
    def get(self, interface_uuid=None):
        ni = db.get_interface(interface_uuid)
        if not ni:
            return error(404, 'interface not found')

        n = net.from_db(ni['network_uuid'])
        if not n:
            LOG.info('network(%s): network not found, genuinely missing'
                     % ni['network_uuid'])
            return error(404, 'interface network not found')

        if get_jwt_identity() not in [n.namespace, 'system']:
            LOG.info('%s: interface not found, ownership test' % n)
            return error(404, 'interface not found')

        i = virt.from_db(ni['instance_uuid'])
        if get_jwt_identity() not in [i.db_entry['namespace'], 'system']:
            LOG.info('%s: instance not found, ownership test' % i)
            return error(404, 'interface not found')

        return ni


class InterfaceFloat(Resource):
    @jwt_required
    @redirect_to_network_node
    def post(self, interface_uuid=None):
        ni = db.get_interface(interface_uuid)
        if not ni:
            return error(404, 'network interface not found')

        if ni['floating']:
            return error(409, 'this interface already has a floating ip')

        n = net.from_db(ni['network_uuid'])
        if not n:
            LOG.info('network(%s): network not found, genuinely missing'
                     % ni['network_uuid'])
            return error(404, 'network not found')

        if get_jwt_identity() not in [n.namespace, 'system']:
            LOG.info('%s: network not found, ownership test' % n)
            return error(404, 'network not found')

        i = virt.from_db(ni['instance_uuid'])
        if get_jwt_identity() not in [i.db_entry['namespace'], 'system']:
            LOG.info('%s: instance not found, ownership test' % i)
            return error(404, 'instance not found')

        float_net = net.from_db('floating')
        if not float_net:
            return error(404, 'floating network not found')

        db.add_event('interface', interface_uuid,
                     'api', 'float', None, None)
        with db.get_lock('sf/ipmanager/floating', ttl=120) as _:
            ipm = db.get_ipmanager('floating')
            addr = ipm.get_random_free_address()
            db.persist_ipmanager('floating', ipm.save())

        db.add_floating_to_interface(ni['uuid'], addr)
        n.add_floating_ip(addr, ni['ipv4'])


class InterfaceDefloat(Resource):
    @jwt_required
    @redirect_to_network_node
    def post(self, interface_uuid=None):
        ni = db.get_interface(interface_uuid)
        if not ni:
            return error(404, 'network interface not found')

        if not ni['floating']:
            return error(409, 'this interface does not have a floating ip')

        n = net.from_db(ni['network_uuid'])
        if not n:
            LOG.info('network(%s): network not found, genuinely missing'
                     % ni['network_uuid'])
            return error(404, 'network not found')

        if get_jwt_identity() not in [n.namespace, 'system']:
            LOG.info('%s: network not found, ownership test' % n)
            return error(404, 'network not found')

        i = virt.from_db(ni['instance_uuid'])
        if get_jwt_identity() not in [i.db_entry['namespace'], 'system']:
            LOG.info('%s: instance not found, ownership test' % i)
            return error(404, 'instance not found')

        float_net = net.from_db('floating')
        if not float_net:
            return error(404, 'floating network not found')

        db.add_event('interface', interface_uuid,
                     'api', 'defloat', None, None)
        with db.get_lock('sf/ipmanager/floating', ttl=120) as _:
            ipm = db.get_ipmanager('floating')
            ipm.release(ni['floating'])
            db.persist_ipmanager('floating', ipm.save())

        db.remove_floating_from_interface(ni['uuid'])
        n.remove_floating_ip(ni['floating'], ni['ipv4'])


class InstanceMetadatas(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        md = db.get_metadata('instance', instance_uuid)
        if not md:
            return {}
        return md

    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def post(self, instance_uuid=None, key=None, value=None, instance_from_db=None):
        return _metadata_putpost('instance', instance_uuid, key, value)


class InstanceMetadata(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def put(self, instance_uuid=None, key=None, value=None, instance_from_db=None):
        return _metadata_putpost('instance', instance_uuid, key, value)

    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def delete(self, instance_uuid=None, key=None, instance_from_db=None):
        if not key:
            return error(400, 'no key specified')

        with db.get_lock('sf/metadata/instance/%s' % instance_uuid) as _:
            md = db.get_metadata('instance', instance_uuid)
            if md is None or key not in md:
                return error(404, 'key not found')
            del md[key]
            db.persist_metadata('instance', instance_uuid, md)


class InstanceConsoleData(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @arg_is_instance_uuid_as_virt
    @requires_instance_ownership
    @redirect_instance_request
    def get(self, instance_uuid=None, length=None, instance_from_db=None, instance_from_db_virt=None):
        try:
            length = int(length)
        except:
            return error(400, 'length is not an integer')

        resp = flask.Response(
            instance_from_db_virt.get_console_data(length),
            mimetype='text/plain')
        resp.status_code = 200
        return resp


class Image(Resource):
    @jwt_required
    @caller_is_admin
    def post(self, url=None):
        db.add_event('image', url, 'api', 'cache', None, None)

        with util.RecordedOperation('cache image', url) as _:
            image_url = images.resolve(url)
            hashed_image_path, info, image_dirty, resp = \
                images.requires_fetch(image_url)

            if image_dirty:
                images.fetch(hashed_image_path, info, resp)


class Network(Resource):
    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        db.add_event('network', network_uuid, 'api', 'get', None, None)
        if network_from_db is not None and 'ipmanager' in network_from_db:
            del network_from_db['ipmanager']
        return network_from_db

    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    @redirect_to_network_node
    def delete(self, network_uuid=None, network_from_db=None):
        db.add_event('network', network_uuid, 'api', 'delete', None, None)
        if network_uuid == 'floating':
            return error(403, 'you cannot delete the floating network')

        # We only delete unused networks
        if len(list(db.get_network_interfaces(network_uuid))) > 0:
            return error(403, 'you cannot delete an in use network')

        # Check if network has already been deleted
        if network_from_db['state'] == 'deleted':
            return error(404, 'network not found')

        with db.get_lock('sf/network/%s' % network_uuid, ttl=900) as _:
            n = net.from_db(network_uuid)
            n.remove_dhcp()
            n.delete()

            if n.floating_gateway:
                with db.get_lock('sf/ipmanager/floating', ttl=120) as _:
                    ipm = db.get_ipmanager('floating')
                    ipm.release(n.floating_gateway)
                    db.persist_ipmanager('floating', ipm.save())

            db.update_network_state(network_uuid, 'deleted')


class Networks(Resource):
    @marshal_with({
        'uuid': fields.String,
        'vxlan_id': fields.Integer,
        'netblock': fields.String,
        'provide_dhcp': fields.Boolean,
        'provide_nat': fields.Boolean,
        'namespace': fields.String,
        'name': fields.String,
    })
    @jwt_required
    def get(self, all=False):
        return list(db.get_networks(all=all, namespace=get_jwt_identity()))

    @jwt_required
    def post(self, netblock=None, provide_dhcp=None, provide_nat=None, name=None,
             namespace=None):
        try:
            ipaddress.ip_network(netblock)
        except ValueError as e:
            return error(400, 'cannot parse netblock: %s' % e)

        if not namespace:
            namespace = get_jwt_identity()

        # If accessing a foreign name namespace, we need to be an admin
        if get_jwt_identity() not in [namespace, 'system']:
            return error(401,
                         'only admins can create resources in a different namespace')

        network = db.allocate_network(netblock, provide_dhcp,
                                      provide_nat, name, namespace)
        db.add_event('network', network['uuid'],
                     'api', 'create', None, None)

        # Networks should immediately appear on the network node
        with db.get_lock('sf/network/%s' % network['uuid'], ttl=900) as _:
            if config.parsed.get('NODE_IP') == config.parsed.get('NETWORK_NODE_IP'):
                n = net.from_db(network['uuid'])
                if not n:
                    LOG.info('network(%s): network not found, genuinely missing'
                             % network['uuid'])
                    return error(404, 'network not found')

                n.create()
                n.ensure_mesh()
            else:
                admin_token = util.get_api_token(
                    'http://%s:%d' % (config.parsed.get('NETWORK_NODE_IP'),
                                      config.parsed.get('API_PORT')),
                    namespace=namespace)
                requests.request(
                    'put',
                    ('http://%s:%d/deploy_network_node'
                     % (config.parsed.get('NETWORK_NODE_IP'),
                        config.parsed.get('API_PORT'))),
                    data=json.dumps({'uuid': network['uuid']}),
                    headers={'Authorization': admin_token,
                             'User-Agent': util.get_user_agent()})

            db.add_event('network', network['uuid'],
                         'api', 'created', None, None)
            db.update_network_state(network['uuid'], 'created')

            # Initialise metadata
            db.persist_metadata('network', network['uuid'], {})

        return network


class NetworkEvents(Resource):
    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        db.add_event('network', network_uuid,
                     'api', 'get events', None, None)
        return list(db.get_events('network', network_uuid))


class NetworkMetadatas(Resource):
    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        md = db.get_metadata('network', network_uuid)
        if not md:
            return {}
        return md

    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    def post(self, network_uuid=None, key=None, value=None, network_from_db=None):
        return _metadata_putpost('network', network_uuid, key, value)


class NetworkMetadata(Resource):
    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    def put(self, network_uuid=None, key=None, value=None, network_from_db=None):
        return _metadata_putpost('network', network_uuid, key, value)

    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    def delete(self, network_uuid=None, key=None, network_from_db=None):
        if not key:
            return error(400, 'no key specified')

        with db.get_lock('sf/metadata/network/%s' % network_uuid) as _:
            md = db.get_metadata('network', network_uuid)
            if md is None or key not in md:
                return error(404, 'key not found')
            del md[key]
            db.persist_metadata('network', network_uuid, md)


class Nodes(Resource):
    @jwt_required
    @caller_is_admin
    @marshal_with({
        'name': fields.String(attribute='fqdn'),
        'ip': fields.String,
        'lastseen': fields.Float,
    })
    def get(self):
        return list(db.get_nodes())


# Internal APIs


class DeployNetworkNode(Resource):
    @jwt_required
    @caller_is_admin
    @redirect_to_network_node
    def put(self, passed_uuid=None):
        db.add_event('network', passed_uuid,
                     'network node', 'deploy', None, None)
        n = net.from_db(passed_uuid)
        if not n:
            LOG.info('network(%s): network not found, genuinely missing'
                     % passed_uuid)
            return error(404, 'network not found')

        n.create()
        n.ensure_mesh()


class UpdateDHCP(Resource):
    @jwt_required
    @caller_is_admin
    @redirect_to_network_node
    def put(self, passed_uuid=None):
        db.add_event('network', passed_uuid,
                     'network node', 'update dhcp', None, None)
        n = net.from_db(passed_uuid)
        if not n:
            LOG.info('network(%s): network not found, genuinely missing'
                     % passed_uuid)
            return error(404, 'network not found')

        n.update_dhcp()


class RemoveDHCP(Resource):
    @jwt_required
    @caller_is_admin
    @redirect_to_network_node
    def put(self, passed_uuid=None):
        db.add_event('network', passed_uuid,
                     'network node', 'remove dhcp', None, None)
        n = net.from_db(passed_uuid)
        if not n:
            LOG.info('network(%s): network not found, genuinely missing'
                     % passed_uuid)
            return error(404, 'network not found')

        n.remove_dhcp()


api.add_resource(Root, '/')

api.add_resource(Auth, '/auth')
api.add_resource(AuthNamespaces, '/auth/namespaces')
api.add_resource(AuthNamespace, '/auth/namespaces/<namespace>')
api.add_resource(AuthNamespaceKeys,
                 '/auth/namespaces/<namespace>/keys')
api.add_resource(AuthNamespaceKey,
                 '/auth/namespaces/<namespace>/keys/<key_name>')
api.add_resource(AuthMetadatas, '/auth/namespaces/<namespace>/metadata')
api.add_resource(AuthMetadata,
                 '/auth/namespaces/<namespace>/metadata/<key>')

api.add_resource(Instances, '/instances')
api.add_resource(Instance, '/instances/<instance_uuid>')
api.add_resource(InstanceEvents, '/instances/<instance_uuid>/events')
api.add_resource(InstanceInterfaces, '/instances/<instance_uuid>/interfaces')
api.add_resource(InstanceSnapshot, '/instances/<instance_uuid>/snapshot')
api.add_resource(InstanceRebootSoft, '/instances/<instance_uuid>/rebootsoft')
api.add_resource(InstanceRebootHard, '/instances/<instance_uuid>/reboothard')
api.add_resource(InstancePowerOff, '/instances/<instance_uuid>/poweroff')
api.add_resource(InstancePowerOn, '/instances/<instance_uuid>/poweron')
api.add_resource(InstancePause, '/instances/<instance_uuid>/pause')
api.add_resource(InstanceUnpause, '/instances/<instance_uuid>/unpause')
api.add_resource(Interface, '/interfaces/<interface_uuid>')
api.add_resource(InterfaceFloat, '/interfaces/<interface_uuid>/float')
api.add_resource(InterfaceDefloat, '/interfaces/<interface_uuid>/defloat')
api.add_resource(InstanceMetadatas, '/instances/<instance_uuid>/metadata')
api.add_resource(InstanceMetadata,
                 '/instances/<instance_uuid>/metadata/<key>')
api.add_resource(InstanceConsoleData, '/instances/<instance_uuid>/consoledata',
                 defaults={'length': 10240})

api.add_resource(Image, '/images')

api.add_resource(Networks, '/networks')
api.add_resource(Network, '/networks/<network_uuid>')
api.add_resource(NetworkEvents, '/networks/<network_uuid>/events')
api.add_resource(NetworkMetadatas, '/networks/<network_uuid>/metadata')
api.add_resource(NetworkMetadata,
                 '/networks/<network_uuid>/metadata/<key>')

api.add_resource(Nodes, '/nodes')

api.add_resource(DeployNetworkNode, '/deploy_network_node')
api.add_resource(UpdateDHCP, '/update_dhcp')
api.add_resource(RemoveDHCP, '/remove_dhcp')


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf api')

    def run(self):
        processutils.execute(
            ('gunicorn3 --workers 10 --bind 0.0.0.0:%d '
             '--log-syslog --log-syslog-prefix sf '
             '--timeout 300 --name "sf api" '
             'shakenfist.external_api.app:app'
             % config.parsed.get('API_PORT')),
            shell=True, env_variables=os.environ)
