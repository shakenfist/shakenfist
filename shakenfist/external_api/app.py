#################################################################################
# DEAR FUTURE ME... The order of decorators on these API methods deeply deeply  #
# matters. We need to verify auth before anything, and we need to fetch things  #
# from the database before we make decisions based on those things. So remember #
# the outer decorator is executed first!                                        #
#################################################################################

import base64
import bcrypt
import copy
from functools import partial
import flask
from flask_jwt_extended import create_access_token
from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import JWTManager
from flask_jwt_extended import jwt_required
from flask_jwt_extended.exceptions import (
    JWTDecodeError, NoAuthorizationError, InvalidHeaderError, WrongTokenError,
    RevokedTokenError, FreshTokenRequired, CSRFError
)
import flask_restful
from flask_restful import fields
from flask_restful import marshal_with
import ipaddress
import json
from jwt.exceptions import DecodeError, PyJWTError
import re
import requests
import sys
import time
import traceback
import uuid

from shakenfist import baseobject
from shakenfist.config import config
from shakenfist import db
from shakenfist import exceptions
from shakenfist import images
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import net
from shakenfist import scheduler
from shakenfist import util
from shakenfist import virt
from shakenfist.daemons import daemon
from shakenfist.tasks import (DeleteInstanceTask,
                              FetchImageTask,
                              PreflightInstanceTask,
                              StartInstanceTask,
                              )


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


TESTING = False
SCHEDULER = None


def error(status_code, message):
    global TESTING

    body = {
        'error': message,
        'status': status_code
    }

    if TESTING or config.get('INCLUDE_TRACEBACKS'):
        _, _, tb = sys.exc_info()
        if tb:
            body['traceback'] = traceback.format_exc()

    resp = flask.Response(json.dumps(body),
                          mimetype='application/json')
    resp.status_code = status_code
    LOG.info('Returning API error: %d, %s' % (status_code, message))
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

            # Ensure key does not appear in logs
            kwargs_log = copy.copy(kwargs)
            if 'key' in kwargs_log:
                kwargs_log['key'] = '*****'

            msg = 'API request: %s %s' % (
                flask.request.method, flask.request.url)
            msg += '\n    Args: %s\n    KWargs: %s' % (args, kwargs_log)

            if re.match(r'http(|s)://0.0.0.0:\d+/$', flask.request.url):
                LOG.debug(msg)
            else:
                LOG.info(msg)

            return func(*args, **kwargs)

        except TypeError as e:
            return error(400, str(e))

        except DecodeError:
            # Send a more informative message than 'Not enough segments'
            return error(401, 'invalid JWT in Authorization header')

        except (JWTDecodeError,
                NoAuthorizationError,
                InvalidHeaderError,
                WrongTokenError,
                RevokedTokenError,
                FreshTokenRequired,
                CSRFError,
                PyJWTError,
                ) as e:
            return error(401, str(e))

        except Exception as e:
            LOG.exception('Server error')
            return error(500, 'server error: %s' % repr(e))

    return wrapper


class Resource(flask_restful.Resource):
    method_decorators = [generic_wrapper]


def caller_is_admin(func):
    # Ensure only users in the 'system' namespace can call this method
    def wrapper(*args, **kwargs):
        if get_jwt_identity() != 'system':
            return error(401, 'unauthorized')

        return func(*args, **kwargs)
    return wrapper


def arg_is_instance_uuid(func):
    # Method uses the instance from the db
    def wrapper(*args, **kwargs):
        if 'instance_uuid' in kwargs:
            kwargs['instance_from_db'] = virt.Instance.from_db(
                kwargs['instance_uuid'])
        if not kwargs.get('instance_from_db'):
            LOG.with_instance(kwargs['instance_uuid']).info(
                'Instance not found, genuinely missing')
            return error(404, 'instance not found')

        return func(*args, **kwargs)
    return wrapper


def redirect_instance_request(func):
    # Redirect method to the hypervisor hosting the instance
    def wrapper(*args, **kwargs):
        i = kwargs.get('instance_from_db')
        if not i:
            return

        placement = i.placement
        if not placement:
            return
        if not placement.get('node'):
            return

        if placement.get('node') != config.NODE_NAME:
            url = 'http://%s:%d%s' % (placement['node'], config.get('API_PORT'),
                                      flask.request.environ['PATH_INFO'])
            api_token = util.get_api_token(
                'http://%s:%d' % (placement['node'], config.get('API_PORT')),
                namespace=get_jwt_identity())
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=json.dumps(flask_get_post_body()),
                headers={'Authorization': api_token,
                         'User-Agent': util.get_user_agent()})

            LOG.info('Proxied %s %s returns: %d, %s' % (
                     flask.request.environ['REQUEST_METHOD'], url,
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
            LOG.with_field('instance', kwargs['instance_uuid']).info(
                'Instance not found, kwarg missing')
            return error(404, 'instance not found')

        i = kwargs['instance_from_db']
        if get_jwt_identity() not in [i.namespace, 'system']:
            LOG.with_instance(i).info(
                'Instance not found, ownership test in decorator')
            return error(404, 'instance not found')

        return func(*args, **kwargs)
    return wrapper


def requires_instance_active(func):
    # Requires that @arg_is_instance_uuid has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('instance_from_db'):
            LOG.with_field('instance', kwargs['instance_uuid']).info(
                'Instance not found, kwarg missing')
            return error(404, 'instance not found')

        i = kwargs['instance_from_db']
        if i.state.value in ['initial', 'preflight', 'creating']:
            LOG.with_instance(i).info('Instance not active')
            return error(406, 'instance not active')

        return func(*args, **kwargs)
    return wrapper


def arg_is_network_uuid(func):
    # Method uses the network from the db
    def wrapper(*args, **kwargs):
        if 'network_uuid' in kwargs:
            kwargs['network_from_db'] = net.Network.from_db(
                kwargs['network_uuid'])
        if not kwargs.get('network_from_db'):
            LOG.with_field('network', kwargs['network_uuid']).info(
                'Network not found, missing or deleted')
            return error(404, 'network not found')

        return func(*args, **kwargs)
    return wrapper


def redirect_to_network_node(func):
    # Redirect method to the network node
    def wrapper(*args, **kwargs):
        if not util.is_network_node():
            admin_token = util.get_api_token(
                'http://%s:%d' % (config.NETWORK_NODE_IP,
                                  config.get('API_PORT')),
                namespace='system')
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'],
                'http://%s:%d%s'
                % (config.NETWORK_NODE_IP,
                   config.get('API_PORT'),
                   flask.request.environ['PATH_INFO']),
                data=flask.request.data,
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
        log = LOG.with_field('network', kwargs['network_uuid'])

        if not kwargs.get('network_from_db'):
            log.info('Network not found, kwarg missing')
            return error(404, 'network not found')

        if get_jwt_identity() not in [kwargs['network_from_db'].namespace, 'system']:
            log.info('Network not found, ownership test in decorator')
            return error(404, 'network not found')

        return func(*args, **kwargs)
    return wrapper


def requires_network_active(func):
    # Requires that @arg_is_network_uuid has already run
    def wrapper(*args, **kwargs):
        log = LOG.with_field('network', kwargs['network_uuid'])

        if not kwargs.get('network_from_db'):
            log.info('Network not found, kwarg missing')
            return error(404, 'network not found')

        state = kwargs['network_from_db'].state
        if state.value in ['initial', 'preflight', 'creating']:
            log.info('Network not active')
            return error(406, 'network not active')

        return func(*args, **kwargs)
    return wrapper


def _metadata_putpost(meta_type, owner, key, value):
    if meta_type not in ['namespace', 'instance', 'network']:
        return error(500, 'invalid meta_type %s' % meta_type)
    if not key:
        return error(400, 'no key specified')
    if not value:
        return error(400, 'no value specified')

    with db.get_lock('metadata', meta_type, owner,
                     op='Metadata update'):
        md = db.get_metadata(meta_type, owner)
        if md is None:
            md = {}
        md[key] = value
        db.persist_metadata(meta_type, owner, md)


app = flask.Flask(__name__)
api = flask_restful.Api(app, catch_all_404s=False)
app.config['JWT_SECRET_KEY'] = config.AUTH_SECRET_SEED.get_secret_value()
jwt = JWTManager(app)

# Use our handler to get SF log format (instead of gunicorn's handlers)
app.logger.handlers = [HANDLER]


@app.before_request
def log_request_info():
    LOG.debug(
        'API request headers:\n' +
        ''.join(['    %s: %s\n' % (h, v) for h, v in flask.request.headers]) +
        'API request body: %s' % flask.request.get_data())


class Root(Resource):
    def get(self):
        resp = flask.Response(
            'Shaken Fist REST API service',
            mimetype='text/plain')
        resp.status_code = 200
        return resp


class AdminLocks(Resource):
    @jwt_required
    @caller_is_admin
    def get(self):
        return db.get_existing_locks()


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
        if not isinstance(key, str):
            # Must be a string to encode()
            return error(400, 'key is not a string')

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

        with db.get_lock('namespace', None, 'all', op='Namespace update'):
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
                if not isinstance(key, str):
                    # Must be a string to encode()
                    return error(400, 'key is not a string')
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
        for i in virt.Instances([partial(baseobject.namespace_filter, namespace)]):
            if i.state.value in ['deleted', 'error']:
                deleted_instances.append(i.uuid)
            else:
                instances.append(i.uuid)
        if len(instances) > 0:
            return error(400, 'you cannot delete a namespace with instances')

        networks = []
        for n in net.Networks([partial(baseobject.namespace_filter, namespace)]):
            if not n.is_dead():
                networks.append(n.uuid)
        if len(networks) > 0:
            return error(400, 'you cannot delete a namespace with networks')

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

    with db.get_lock('namespace', None, 'all', op='Namespace key update'):
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

        with db.get_lock('namespace', None, namespace, op='Namespace key delete'):
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

        with db.get_lock('metadata', 'namespace', namespace, op='Metadata delete'):
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
        return instance_from_db.external_view()

    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def delete(self, instance_uuid=None, instance_from_db=None):
        # Check if instance has already been deleted
        if instance_from_db.state.value == 'deleted':
            return error(404, 'instance not found')

        # If this instance is not on a node, just do the DB cleanup locally
        placement = instance_from_db.placement
        if not placement.get('node'):
            node = config.NODE_NAME
        else:
            node = placement['node']

        instance_from_db.enqueue_delete_remote(node)

        start_time = time.time()
        while time.time() - start_time < config.get('API_ASYNC_WAIT'):
            if instance_from_db.state.value in ['deleted', 'error']:
                return

            time.sleep(0.5)

        return


class Instances(Resource):
    @jwt_required
    def get(self, all=False):
        filters = [partial(baseobject.namespace_filter, get_jwt_identity())]
        if not all:
            filters.append(virt.active_states_filter)

        retval = []
        for i in virt.Instances(filters):
            # This forces the instance through the external view rehydration
            retval.append(i.external_view())
        return retval

    @jwt_required
    def post(self, name=None, cpus=None, memory=None, network=None, disk=None,
             ssh_key=None, user_data=None, placed_on=None, namespace=None,
             video=None):
        global SCHEDULER

        # Check that the instance name is safe for use as a DNS host name
        if name != re.sub(r'([^a-zA-Z0-9_\-])', '', name) or len(name) > 63:
            return error(400, 'instance name must be useable as a DNS host name')

        # If we are placed, make sure that node exists
        if placed_on and not db.get_node(placed_on, seen_recently=True):
            return error(404, 'Specified node does not exist')

        # Sanity check
        if not disk:
            return error(400, 'instance must specify at least one disk')
        for d in disk:
            if not isinstance(d, dict):
                return error(400, 'disk specification should contain JSON objects')

        if network:
            for netdesc in network:
                if not isinstance(netdesc, dict):
                    return error(400,
                                 'network specification should contain JSON objects')

                if 'network_uuid' not in netdesc:
                    return error(400, 'network specification is missing network_uuid')

                net_uuid = netdesc['network_uuid']
                if netdesc.get('address'):
                    # The requested address must be within the ip range specified
                    # for that virtual network
                    ipm = IPManager.from_db(net_uuid)
                    if not ipm.is_in_range(netdesc['address']):
                        return error(400,
                                     'network specification requests an address outside the '
                                     'range of the network')

                n = net.Network.from_db(net_uuid)
                if not n:
                    return error(404, 'network %s does not exist' % net_uuid)
                if n.state.value in ['initial', 'preflight', 'creating']:
                    return error(406, 'network %s is not active' % net_uuid)

        if not video:
            video = {'model': 'cirrus', 'memory': 16384}

        if not namespace:
            namespace = get_jwt_identity()

        # If accessing a foreign namespace, we need to be an admin
        if get_jwt_identity() not in [namespace, 'system']:
            return error(401,
                         'only admins can create resources in a different namespace')

        # Create instance object
        instance = virt.Instance.new(
            name=name,
            disk_spec=disk,
            memory=memory,
            cpus=cpus,
            ssh_key=ssh_key,
            user_data=user_data,
            namespace=namespace,
            video=video,
            requested_placement=placed_on
        )

        # Initialise metadata
        db.persist_metadata('instance', instance.uuid, {})

        # Allocate IP addresses
        order = 0
        if network:
            for netdesc in network:
                n = net.Network.from_db(netdesc['network_uuid'])
                if not n:
                    m = 'missing network %s during IP allocation phase' % (
                        netdesc['network_uuid'])
                    instance.enqueue_delete_due_error(m)
                    return error(
                        404, 'network %s not found' % netdesc['network_uuid'])

                with db.get_lock('ipmanager', None,  netdesc['network_uuid'],
                                 ttl=120, op='Network allocate IP'):
                    db.add_event('network', netdesc['network_uuid'], 'allocate address',
                                 None, None, instance.uuid)
                    ipm = IPManager.from_db(netdesc['network_uuid'])
                    if 'address' not in netdesc or not netdesc['address']:
                        netdesc['address'] = ipm.get_random_free_address()
                    else:
                        if not ipm.reserve(netdesc['address']):
                            m = 'failed to reserve an IP on network %s' % (
                                netdesc['network_uuid'])
                            instance.enqueue_delete_due_error(m)
                            return error(409, 'address %s in use' %
                                         netdesc['address'])

                    ipm.persist()

                if 'model' not in netdesc or not netdesc['model']:
                    netdesc['model'] = 'virtio'

                iface_uuid = str(uuid.uuid4())
                LOG.with_object(instance).with_object(n).withFields({
                    'networkinterface': iface_uuid
                }).info('Interface allocated')
                db.create_network_interface(
                    iface_uuid, netdesc, instance.uuid, order)

        if not SCHEDULER:
            SCHEDULER = scheduler.Scheduler()

        try:
            # Have we been placed?
            if not placed_on:
                candidates = SCHEDULER.place_instance(instance, network)
                placement = candidates[0]

            else:
                SCHEDULER.place_instance(instance, network,
                                         candidates=[placed_on])
                placement = placed_on

        except exceptions.LowResourceException as e:
            instance.add_event('schedule', 'failed', None,
                               'Insufficient resources: ' + str(e))
            instance.enqueue_delete_due_error('scheduling failed')
            return error(507, str(e))

        except exceptions.CandidateNodeNotFoundException as e:
            instance.add_event('schedule', 'failed', None,
                               'Candidate node not found: ' + str(e))
            instance.enqueue_delete_due_error('scheduling failed')
            return error(404, 'node not found: %s' % e)

        # Record placement
        instance.place_instance(placement)

        # Create a queue entry for the instance start
        tasks = [PreflightInstanceTask(instance.uuid, network)]
        for disk in instance.disk_spec:
            if disk.get('base'):
                tasks.append(FetchImageTask(disk['base'], instance.uuid))
        tasks.append(StartInstanceTask(instance.uuid, network))

        # Enqueue creation tasks on desired node task queue
        db.enqueue(placement, {'tasks': tasks})
        instance.add_event('create', 'enqueued', None, None)

        # Watch for a while and return results if things are fast, give up
        # after a while and just return the current state
        start_time = time.time()
        while time.time() - start_time < config.get('API_ASYNC_WAIT'):
            if instance.state.value in ['created', 'deleted', 'error']:
                return instance.external_view()
            time.sleep(0.5)
        return instance.external_view()

    @jwt_required
    def delete(self, confirm=False, namespace=None):
        """Delete all instances in the namespace."""

        if confirm is not True:
            return error(400, 'parameter confirm is not set true')

        if get_jwt_identity() == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all instances in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity():
                return error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()

        waiting_for = []
        tasks_by_node = {}
        for instance in virt.Instances([partial(baseobject.namespace_filter, namespace),
                                        virt.active_states_filter]):
            # If this instance is not on a node, just do the DB cleanup locally
            dbplacement = instance.placement
            if not dbplacement.get('node'):
                node = config.NODE_NAME
            else:
                node = dbplacement['node']

            tasks_by_node.setdefault(node, [])
            tasks_by_node[node].append(DeleteInstanceTask(instance.uuid))
            waiting_for.append(instance)

        for node in tasks_by_node:
            db.enqueue(node, {'tasks': tasks_by_node[node]})

        start_time = time.time()
        while (waiting_for and
               (time.time() - start_time < config.get('API_ASYNC_WAIT'))):
            for instance in copy.copy(waiting_for):
                s = instance.state.value
                if s in ['deleted', 'error']:
                    waiting_for.remove(instance)
                else:
                    LOG.with_instance(instance).info(
                        'Still waiting for deletion (state is %s)' % s)

            if waiting_for:
                time.sleep(0.2)

        retval = []
        for instance in waiting_for:
            retval.append(instance.uuid)
        return retval


class InstanceInterfaces(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        return list(db.get_instance_interfaces(instance_uuid))


class InstanceEvents(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        return list(db.get_events('instance', instance_uuid))


class InstanceSnapshot(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @redirect_instance_request
    @requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None, all=None):
        snap_uuid = instance_from_db.snapshot(all=all)
        instance_from_db.add_event('api', 'snapshot (all=%s)' % all,
                                   None, snap_uuid)
        db.add_event('snapshot', snap_uuid, 'api', 'create', None, None)
        return snap_uuid

    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        out = []
        for snap in db.get_instance_snapshots(instance_uuid):
            snap['created'] = snap['created']
            out.append(snap)
        return out


class InstanceRebootSoft(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @redirect_instance_request
    @requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance reboot soft'):
            instance_from_db.add_event('api', 'soft reboot')
            return instance_from_db.reboot(hard=False)


class InstanceRebootHard(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @redirect_instance_request
    @requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance reboot hard'):
            instance_from_db.add_event('api', 'hard reboot')
            return instance_from_db.reboot(hard=True)


class InstancePowerOff(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @redirect_instance_request
    @requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance power off'):
            instance_from_db.add_event('api', 'poweroff')
            return instance_from_db.power_off()


class InstancePowerOn(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @redirect_instance_request
    @requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance power on'):
            instance_from_db.add_event('api', 'poweron')
            return instance_from_db.power_on()


class InstancePause(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @redirect_instance_request
    @requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance pause'):
            instance_from_db.add_event('api', 'pause')
            return instance_from_db.pause()


class InstanceUnpause(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @redirect_instance_request
    @requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance unpause'):
            instance_from_db.add_event('api', 'unpause')
            return instance_from_db.unpause()


def _safe_get_network_interface(interface_uuid):
    ni = db.get_interface(interface_uuid)
    if not ni:
        return None, None, error(404, 'interface not found')

    log = LOG.with_fields({'network': ni['network_uuid'],
                           'networkinterface': ni['uuid']})

    n = net.Network.from_db(ni['network_uuid'])
    if not n:
        log.info('Network not found or deleted')
        return None, None, error(404, 'interface network not found')

    if get_jwt_identity() not in [n.namespace, 'system']:
        log.info('Interface not found, failed ownership test')
        return None, None, error(404, 'interface not found')

    i = virt.Instance.from_db(ni['instance_uuid'])
    if get_jwt_identity() not in [i.namespace, 'system']:
        log.with_object(i).info('Instance not found, failed ownership test')
        return None, None, error(404, 'interface not found')

    return ni, n, None


class Interface(Resource):
    @jwt_required
    @redirect_to_network_node
    def get(self, interface_uuid=None):
        ni, _, err = _safe_get_network_interface(interface_uuid)
        if err:
            return err
        return ni


class InterfaceFloat(Resource):
    @jwt_required
    @redirect_to_network_node
    def post(self, interface_uuid=None):
        ni, n, err = _safe_get_network_interface(interface_uuid)
        if err:
            return err

        float_net = net.Network.from_db('floating')
        if not float_net:
            return error(404, 'floating network not found')

        db.add_event('interface', interface_uuid,
                     'api', 'float', None, None)
        with db.get_lock('ipmanager', None, 'floating', ttl=120, op='Interface float'):
            ipm = IPManager.from_db('floating')
            addr = ipm.get_random_free_address()
            ipm.persist()

        db.add_floating_to_interface(ni['uuid'], addr)
        n.add_floating_ip(addr, ni['ipv4'])


class InterfaceDefloat(Resource):
    @jwt_required
    @redirect_to_network_node
    def post(self, interface_uuid=None):
        ni, n, err = _safe_get_network_interface(interface_uuid)
        if err:
            return err

        float_net = net.Network.from_db('floating')
        if not float_net:
            return error(404, 'floating network not found')

        db.add_event('interface', interface_uuid,
                     'api', 'defloat', None, None)
        with db.get_lock('ipmanager', None, 'floating', ttl=120, op='Instance defloat'):
            ipm = IPManager.from_db('floating')
            ipm.release(ni['floating'])
            ipm.persist()

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

        with db.get_lock('metadata', 'instance', instance_uuid, op='Instance metadata delete'):
            md = db.get_metadata('instance', instance_uuid)
            if md is None or key not in md:
                return error(404, 'key not found')
            del md[key]
            db.persist_metadata('instance', instance_uuid, md)


class InstanceConsoleData(Resource):
    @jwt_required
    @arg_is_instance_uuid
    @requires_instance_ownership
    @redirect_instance_request
    def get(self, instance_uuid=None, length=None, instance_from_db=None):
        if not length:
            length = -1
        else:
            try:
                length = int(length)
            except ValueError:
                return error(400, 'length is not an integer')

        resp = flask.Response(
            instance_from_db.get_console_data(length),
            mimetype='text/plain')
        resp.status_code = 200
        return resp


class Images(Resource):
    @jwt_required
    def get(self, node=None):
        f = []
        if node:
            f.append(partial(images.placement_filter, node))

        retval = []
        for i in images.Images(filters=f):
            retval.append(i.external_view())
        return retval

    @jwt_required
    def post(self, url=None):
        db.add_event('image', url, 'api', 'cache', None, None)

        # We ensure that the image exists in the database in an initial state
        # here so that it will show up in image list requests. The image is
        # fetched by the queued job later.
        images.Image.new(url)
        db.enqueue(config.NODE_NAME, {
            'tasks': [FetchImageTask(url)],
        })


class ImageEvents(Resource):
    @jwt_required
    # TODO(andy): Should images be owned? Personalised images should be owned.
    def get(self, url):
        return list(db.get_events('image', url))


def _delete_network(network_from_db):
    # Load network from DB to ensure obtaining correct lock.
    n = net.Network.from_db(network_from_db.uuid)
    if not n:
        LOG.with_fields({'network_uuid': n.uuid}).warning(
            'delete_network: network does not exist')
        return error(404, 'network does not exist')

    if n.is_dead():
        # The network has been deleted. No need to attempt further effort.
        LOG.with_fields({'network_uuid': n.uuid,
                         'state': n.state.value
                         }).warning('delete_network: network is dead')
        return error(404, 'network is deleted')

    n.add_event('api', 'delete')
    n.delete()


class Network(Resource):
    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        return network_from_db.external_view()

    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    @redirect_to_network_node
    def delete(self, network_uuid=None, network_from_db=None):
        if network_uuid == 'floating':
            return error(403, 'you cannot delete the floating network')

        # We only delete unused networks
        ifaces = list(db.get_network_interfaces(network_uuid))
        if len(ifaces) > 0:
            for iface in ifaces:
                LOG.withFields({'network_interface': iface['uuid'],
                                'state': iface['state']}).info('Blocks network delete')
            return error(403, 'you cannot delete an in use network')

        # Check if network has already been deleted
        if network_from_db.state.value in 'deleted':
            return error(404, 'network not found')

        return _delete_network(network_from_db)


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
        filters = [partial(baseobject.namespace_filter, get_jwt_identity())]
        if not all:
            filters.append(baseobject.active_states_filter)

        retval = []
        for n in net.Networks(filters):
            # This forces the network through the external view rehydration
            retval.append(n.external_view())
        return retval

    @jwt_required
    def post(self, netblock=None, provide_dhcp=None, provide_nat=None, name=None,
             namespace=None):
        try:
            n = ipaddress.ip_network(netblock)
            if n.num_addresses < 8:
                return error(400, 'network is below minimum size of /29')
        except ValueError as e:
            return error(400, 'cannot parse netblock: %s' % e)

        if not namespace:
            namespace = get_jwt_identity()

        # If accessing a foreign name namespace, we need to be an admin
        if get_jwt_identity() not in [namespace, 'system']:
            return error(
                401,
                'only admins can create resources in a different namespace')

        network = net.Network.new(name, namespace, netblock, provide_dhcp,
                                  provide_nat)
        return network.external_view()

    @jwt_required
    @redirect_to_network_node
    def delete(self, confirm=False, namespace=None):
        """Delete all networks in the namespace."""

        if confirm is not True:
            return error(400, 'parameter confirm is not set true')

        if get_jwt_identity() == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all networks in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity():
                return error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()

        networks_del = []
        networks_unable = []
        for n in net.Networks([partial(baseobject.namespace_filter, namespace),
                               baseobject.active_states_filter]):
            if len(list(db.get_network_interfaces(n.uuid))) > 0:
                LOG.with_object(n).warning(
                    'Network in use, cannot be deleted by delete-all')
                networks_unable.append(n.uuid)
                continue

            _delete_network(n)
            networks_del.append(n.uuid)

        if networks_unable:
            return error(403, {'deleted': networks_del,
                               'unable': networks_unable})

        return networks_del


class NetworkEvents(Resource):
    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        return list(db.get_events('network', network_uuid))


class NetworkInterfaces(Resource):
    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    @requires_network_active
    def get(self, network_uuid=None, network_from_db=None):
        return list(db.get_network_interfaces(network_uuid))


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

        with db.get_lock('metadata', 'network', network_uuid, op='Network metadata delete'):
            md = db.get_metadata('network', network_uuid)
            if md is None or key not in md:
                return error(404, 'key not found')
            del md[key]
            db.persist_metadata('network', network_uuid, md)


class NetworkPing(Resource):
    @jwt_required
    @arg_is_network_uuid
    @requires_network_ownership
    @redirect_to_network_node
    @requires_network_active
    def get(self, network_uuid=None, address=None, network_from_db=None):
        ipm = IPManager.from_db(network_uuid)
        if not ipm.is_in_range(address):
            return error(400, 'ping request for address outside network block')

        n = net.Network.from_db(network_uuid)
        if not n:
            return error(404, 'network %s not found' % network_uuid)

        out, err = util.execute(
            None, 'ip netns exec %s ping -c 10 %s' % (
                network_uuid, address),
            check_exit_code=[0, 1])
        return {
            'stdout': out,
            'stderr': err
        }


class Nodes(Resource):
    @jwt_required
    @caller_is_admin
    @marshal_with({
        'name': fields.String(attribute='fqdn'),
        'ip': fields.String,
        'lastseen': fields.Float,
        'version': fields.String,
    })
    def get(self):
        return list(db.get_nodes())


api.add_resource(Root, '/')

api.add_resource(AdminLocks, '/admin/locks')

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

api.add_resource(Images, '/images')
api.add_resource(ImageEvents, '/images/events')

api.add_resource(Networks, '/networks')
api.add_resource(Network, '/networks/<network_uuid>')
api.add_resource(NetworkEvents, '/networks/<network_uuid>/events')
api.add_resource(NetworkInterfaces, '/networks/<network_uuid>/interfaces')
api.add_resource(NetworkMetadatas, '/networks/<network_uuid>/metadata')
api.add_resource(NetworkMetadata,
                 '/networks/<network_uuid>/metadata/<key>')
api.add_resource(NetworkPing,
                 '/networks/<network_uuid>/ping/<address>')

api.add_resource(Nodes, '/nodes')
