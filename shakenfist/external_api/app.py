#################################################################################
# DEAR FUTURE ME... The order of decorators on these API methods deeply deeply  #
# matters. We need to verify auth before anything, and we need to fetch things  #
# from the database before we make decisions based on those things. So remember #
# the outer decorator is executed first!                                        #
#                                                                               #
# Additionally, you should use suppress_traceback=True in calls to error()      #
# which exist inside an expected exception block, otherwise we'll log a stray   #
# traceback.                                                                    #
#################################################################################

import base64
import bcrypt
from functools import partial
import flask
from flask_jwt_extended import create_access_token
from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import JWTManager
from flask_jwt_extended import jwt_required
import flask_restful
from flask_restful import fields
from flask_restful import marshal_with
import ipaddress
import re
import uuid

from shakenfist.artifact import (
    Artifact, Artifacts, BLOB_URL, LABEL_URL, SNAPSHOT_URL,
    type_filter as artifact_type_filter)
from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.external_api import blob as api_blob
from shakenfist.external_api import label as api_label
from shakenfist.external_api import snapshot as api_snapshot
from shakenfist.config import config
from shakenfist import db
from shakenfist import exceptions
from shakenfist import images
from shakenfist import instance
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import net
from shakenfist import networkinterface
from shakenfist.networkinterface import NetworkInterface
from shakenfist.node import Node, Nodes
from shakenfist import scheduler
from shakenfist.tasks import (
    DeleteInstanceTask,
    FetchImageTask,
    PreflightInstanceTask,
    StartInstanceTask,
    DestroyNetworkTask,
    FloatNetworkInterfaceTask,
    DefloatNetworkInterfaceTask
)
from shakenfist import util


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


SCHEDULER = None


def _metadata_putpost(meta_type, owner, key, value):
    if meta_type not in ['namespace', 'instance', 'network']:
        return api_base.error(500, 'invalid meta_type %s' % meta_type)
    if not key:
        return api_base.error(400, 'no key specified')
    if not value:
        return api_base.error(400, 'no value specified')

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


class Root(api_base.Resource):
    def get(self):
        resp = flask.Response(
            'Shaken Fist REST API service',
            mimetype='text/plain')
        resp.status_code = 200
        return resp


class AdminLocks(api_base.Resource):
    @jwt_required
    @api_base.caller_is_admin
    def get(self):
        return db.get_existing_locks()


class Auth(api_base.Resource):
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
            return api_base.error(400, 'missing namespace in request')
        if not key:
            return api_base.error(400, 'missing key in request')
        if not isinstance(key, str):
            # Must be a string to encode()
            return api_base.error(400, 'key is not a string')

        service_key, keys = self._get_keys(namespace)
        if service_key and key == service_key:
            return {'access_token': create_access_token(identity=namespace)}
        for possible_key in keys:
            if bcrypt.checkpw(key.encode('utf-8'), possible_key):
                return {'access_token': create_access_token(identity=namespace)}

        return api_base.error(401, 'unauthorized')


class AuthNamespaces(api_base.Resource):
    @jwt_required
    @api_base.caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        if not namespace:
            return api_base.error(400, 'no namespace specified')

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
                    return api_base.error(400, 'no key specified')
                if not isinstance(key, str):
                    # Must be a string to encode()
                    return api_base.error(400, 'key is not a string')
                if key_name == 'service_key':
                    return api_base.error(403, 'illegal key name')

                encoded = str(base64.b64encode(bcrypt.hashpw(
                    key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
                rec['keys'][key_name] = encoded

            # Initialise metadata
            db.persist_metadata('namespace', namespace, {})
            db.persist_namespace(namespace, rec)

        return namespace

    @jwt_required
    @api_base.caller_is_admin
    def get(self):
        out = []
        for rec in db.list_namespaces():
            out.append(rec['name'])
        return out


class AuthNamespace(api_base.Resource):
    @jwt_required
    @api_base.caller_is_admin
    def delete(self, namespace):
        if not namespace:
            return api_base.error(400, 'no namespace specified')
        if namespace == 'system':
            return api_base.error(403, 'you cannot delete the system namespace')

        # The namespace must be empty
        instances = []
        deleted_instances = []
        for i in instance.instances_in_namespace(namespace):
            if i.state.value in [dbo.STATE_DELETED, dbo.STATE_ERROR]:
                deleted_instances.append(i.uuid)
            else:
                LOG.withFields({'instance': i.uuid,
                                'state': i.state}).info('Blocks namespace delete')
                instances.append(i.uuid)
        if len(instances) > 0:
            return api_base.error(400, 'you cannot delete a namespace with instances')

        networks = []
        for n in net.networks_in_namespace(namespace):
            if not n.is_dead():
                LOG.withFields({'network': n.uuid,
                                'state': n.state}).info('Blocks namespace delete')
                networks.append(n.uuid)
        if len(networks) > 0:
            return api_base.error(400, 'you cannot delete a namespace with networks')

        db.delete_namespace(namespace)
        db.delete_metadata('namespace', namespace)


def _namespace_keys_putpost(namespace=None, key_name=None, key=None):
    if not namespace:
        return api_base.error(400, 'no namespace specified')
    if not key_name:
        return api_base.error(400, 'no key name specified')
    if not key:
        return api_base.error(400, 'no key specified')
    if key_name == 'service_key':
        return api_base.error(403, 'illegal key name')

    with db.get_lock('namespace', None, 'all', op='Namespace key update'):
        rec = db.get_namespace(namespace)
        if not rec:
            return api_base.error(404, 'namespace does not exist')

        encoded = str(base64.b64encode(bcrypt.hashpw(
            key.encode('utf-8'), bcrypt.gensalt())), 'utf-8')
        rec['keys'][key_name] = encoded

        db.persist_namespace(namespace, rec)

    return key_name


class AuthNamespaceKeys(api_base.Resource):
    @jwt_required
    @api_base.caller_is_admin
    def get(self, namespace=None):
        rec = db.get_namespace(namespace)
        if not rec:
            return api_base.error(404, 'namespace does not exist')

        out = []
        for keyname in rec['keys']:
            out.append(keyname)
        return out

    @jwt_required
    @api_base.caller_is_admin
    def post(self, namespace=None, key_name=None, key=None):
        return _namespace_keys_putpost(namespace, key_name, key)


class AuthNamespaceKey(api_base.Resource):
    @jwt_required
    @api_base.caller_is_admin
    def put(self, namespace=None, key_name=None, key=None):
        rec = db.get_namespace(namespace)
        if not rec:
            return api_base.error(404, 'namespace does not exist')
        if key_name not in rec['keys']:
            return api_base.error(404, 'key does not exist')

        return _namespace_keys_putpost(namespace, key_name, key)

    @jwt_required
    @api_base.caller_is_admin
    def delete(self, namespace, key_name):
        if not namespace:
            return api_base.error(400, 'no namespace specified')
        if not key_name:
            return api_base.error(400, 'no key name specified')

        with db.get_lock('namespace', None, namespace, op='Namespace key delete'):
            ns = db.get_namespace(namespace)
            if ns.get('keys') and key_name in ns['keys']:
                del ns['keys'][key_name]
            else:
                return api_base.error(404, 'key name not found in namespace')
            db.persist_namespace(namespace, ns)


class AuthMetadatas(api_base.Resource):
    @jwt_required
    @api_base.caller_is_admin
    def get(self, namespace):
        md = db.get_metadata('namespace', namespace)
        if not md:
            return {}
        return md

    @jwt_required
    @api_base.caller_is_admin
    def post(self, namespace, key=None, value=None):
        return _metadata_putpost('namespace', namespace, key, value)


class AuthMetadata(api_base.Resource):
    @jwt_required
    @api_base.caller_is_admin
    def put(self, namespace, key=None, value=None):
        return _metadata_putpost('namespace', namespace, key, value)

    @jwt_required
    @api_base.caller_is_admin
    def delete(self, namespace, key=None, value=None):
        if not key:
            return api_base.error(400, 'no key specified')

        with db.get_lock('metadata', 'namespace', namespace, op='Metadata delete'):
            md = db.get_metadata('namespace', namespace)
            if md is None or key not in md:
                return api_base.error(404, 'key not found')
            del md[key]
            db.persist_metadata('namespace', namespace, md)


class Instance(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        return instance_from_db.external_view()

    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def delete(self, instance_uuid=None, instance_from_db=None):
        # Check if instance has already been deleted
        if instance_from_db.state.value == dbo.STATE_DELETED:
            return api_base.error(404, 'instance not found')

        # If this instance is not on a node, just do the DB cleanup locally
        placement = instance_from_db.placement
        if not placement.get('node'):
            node = config.NODE_NAME
        else:
            node = placement['node']

        instance_from_db.enqueue_delete_remote(node)


def _assign_floating_ip(ni):
    float_net = net.Network.from_db('floating')
    if not float_net:
        return api_base.error(404, 'floating network not found')

    # Address is allocated and added to the record here, so the job has it later.
    db.add_event('interface', ni.uuid, 'api', 'float', None, None)
    with db.get_lock('ipmanager', None, 'floating', ttl=120, op='Interface float'):
        ipm = IPManager.from_db('floating')
        addr = ipm.get_random_free_address(ni.unique_label())
        ipm.persist()

    ni.floating = addr


class Instances(api_base.Resource):
    @jwt_required
    def get(self, all=False):
        filters = [partial(baseobject.namespace_filter, get_jwt_identity())]
        if not all:
            filters.append(instance.active_states_filter)

        retval = []
        for i in instance.Instances(filters):
            # This forces the instance through the external view rehydration
            retval.append(i.external_view())
        return retval

    @jwt_required
    def post(self, name=None, cpus=None, memory=None, network=None, disk=None,
             ssh_key=None, user_data=None, placed_on=None, namespace=None,
             video=None, uefi=False):
        global SCHEDULER

        # Check that the instance name is safe for use as a DNS host name
        if name != re.sub(r'([^a-zA-Z0-9\-])', '', name) or len(name) > 63:
            return api_base.error(400, ('instance name %s is not useable as a DNS and Linux host name. '
                                        'That is, less than 63 characters and in the character set: '
                                        'a-z, A-Z, 0-9, or hyphen (-).' % name))

        # If we are placed, make sure that node exists
        if placed_on:
            n = Node.from_db(placed_on)
            if not n:
                return api_base.error(404, 'Specified node does not exist')
            if n.state.value != Node.STATE_CREATED:
                return api_base.error(404, 'Specified node not ready')

        # Sanity check and lookup blobs for disks where relevant
        if not disk:
            return api_base.error(400, 'instance must specify at least one disk')

        transformed_disk = []
        for d in disk:
            if not isinstance(d, dict):
                return api_base.error(400, 'disk specification should contain JSON objects')

            # Convert internal shorthand forms into specific blobs
            disk_base = d.get('base')
            if not disk_base:
                disk_base = ''

            if disk_base.startswith('label:'):
                label = disk_base[len('label:'):]
                a = Artifact.from_url(
                    Artifact.TYPE_LABEL, '%s%s/%s' % (LABEL_URL, get_jwt_identity(), label))
                if not a:
                    return api_base.error(404, 'label %s not found' % label)
                d['blob_uuid'] = a.most_recent_index['blob_uuid']
            elif disk_base.startswith(SNAPSHOT_URL):
                a = Artifact.from_db(disk_base[len(SNAPSHOT_URL):])
                d['blob_uuid'] = a.most_recent_index['blob_uuid']
            elif disk_base.startswith(BLOB_URL):
                d['blob_uuid'] = disk_base[len(BLOB_URL):]

            transformed_disk.append(d)
        disk = transformed_disk

        if network:
            for netdesc in network:
                if not isinstance(netdesc, dict):
                    return api_base.error(400,
                                          'network specification should contain JSON objects')

                if 'network_uuid' not in netdesc:
                    return api_base.error(400, 'network specification is missing network_uuid')

                net_uuid = netdesc['network_uuid']
                if netdesc.get('address') and not util.noneish(netdesc.get('address')):
                    # The requested address must be within the ip range specified
                    # for that virtual network, unless it is equivalent to "none".
                    ipm = IPManager.from_db(net_uuid)
                    if not ipm.is_in_range(netdesc['address']):
                        return api_base.error(400,
                                              'network specification requests an address outside the '
                                              'range of the network')

                n = net.Network.from_db(net_uuid)
                if not n:
                    return api_base.error(404, 'network %s does not exist' % net_uuid)
                if n.state.value != net.Network.STATE_CREATED:
                    return api_base.error(406, 'network %s is not ready (%s)' % (n.uuid, n.state.value))

        if not video:
            video = {'model': 'cirrus', 'memory': 16384}

        if not namespace:
            namespace = get_jwt_identity()

        # If accessing a foreign namespace, we need to be an admin
        if get_jwt_identity() not in [namespace, 'system']:
            return api_base.error(401,
                                  'only admins can create resources in a different namespace')

        # Create instance object
        inst = instance.Instance.new(
            name=name,
            disk_spec=disk,
            memory=memory,
            cpus=cpus,
            ssh_key=ssh_key,
            user_data=user_data,
            namespace=namespace,
            video=video,
            uefi=uefi,
            requested_placement=placed_on
        )

        # Initialise metadata
        db.persist_metadata('instance', inst.uuid, {})

        # Allocate IP addresses
        order = 0
        float_tasks = []
        if network:
            for netdesc in network:
                n = net.Network.from_db(netdesc['network_uuid'])
                if not n:
                    m = 'missing network %s during IP allocation phase' % (
                        netdesc['network_uuid'])
                    inst.enqueue_delete_due_error(m)
                    return api_base.error(
                        404, 'network %s not found' % netdesc['network_uuid'])

                # NOTE(mikal): we now support interfaces with no address on them
                # (thanks OpenStack Kolla), which are special cased here. To not
                # have an address, you use a detailed netdesc and specify
                # address=none.
                if 'address' in netdesc and util.noneish(netdesc['address']):
                    netdesc['address'] = None
                else:
                    with db.get_lock('ipmanager', None,  netdesc['network_uuid'],
                                     ttl=120, op='Network allocate IP'):
                        db.add_event('network', netdesc['network_uuid'], 'allocate address',
                                     None, None, inst.uuid)
                        ipm = IPManager.from_db(netdesc['network_uuid'])
                        if 'address' not in netdesc or not netdesc['address']:
                            netdesc['address'] = ipm.get_random_free_address(
                                inst.unique_label())
                        else:
                            if not ipm.reserve(netdesc['address'], inst.unique_label()):
                                m = 'failed to reserve an IP on network %s' % (
                                    netdesc['network_uuid'])
                                inst.enqueue_delete_due_error(m)
                                return api_base.error(409, 'address %s in use' %
                                                      netdesc['address'])

                        ipm.persist()

                if 'model' not in netdesc or not netdesc['model']:
                    netdesc['model'] = 'virtio'

                iface_uuid = str(uuid.uuid4())
                LOG.with_object(inst).with_object(n).withFields({
                    'networkinterface': iface_uuid
                }).info('Interface allocated')
                ni = NetworkInterface.new(
                    iface_uuid, netdesc, inst.uuid, order)
                order += 1

                if 'float' in netdesc and netdesc['float']:
                    err = _assign_floating_ip(ni)
                    if err:
                        inst.enqueue_delete_due_error(
                            'interface float failed: %s' % err)
                        return err

                    float_tasks.append(FloatNetworkInterfaceTask(
                        netdesc['network_uuid'], iface_uuid))

        if not SCHEDULER:
            SCHEDULER = scheduler.Scheduler()

        try:
            # Have we been placed?
            if not placed_on:
                candidates = SCHEDULER.place_instance(inst, network)
                placement = candidates[0]

            else:
                SCHEDULER.place_instance(inst, network,
                                         candidates=[placed_on])
                placement = placed_on

        except exceptions.LowResourceException as e:
            inst.add_event('schedule', 'failed', None,
                           'Insufficient resources: ' + str(e))
            inst.enqueue_delete_due_error('scheduling failed')
            return api_base.error(507, str(e), suppress_traceback=True)

        except exceptions.CandidateNodeNotFoundException as e:
            inst.add_event('schedule', 'failed', None,
                           'Candidate node not found: ' + str(e))
            inst.enqueue_delete_due_error('scheduling failed')
            return api_base.error(404, 'node not found: %s' % e, suppress_traceback=True)

        # Record placement
        inst.place_instance(placement)

        # Create a queue entry for the instance start
        tasks = [PreflightInstanceTask(inst.uuid, network)]
        for disk in inst.disk_spec:
            if disk.get('blob_uuid'):
                tasks.append(FetchImageTask(
                    '%s%s' % (BLOB_URL, disk['blob_uuid']), inst.uuid))
            elif disk.get('base'):
                tasks.append(FetchImageTask(disk['base'], inst.uuid))
        tasks.append(StartInstanceTask(inst.uuid, network))
        tasks.extend(float_tasks)

        # Enqueue creation tasks on desired node task queue
        db.enqueue(placement, {'tasks': tasks})
        inst.add_event('create', 'enqueued', None, None)
        return inst.external_view()

    @jwt_required
    def delete(self, confirm=False, namespace=None):
        """Delete all instances in the namespace."""

        if confirm is not True:
            return api_base.error(400, 'parameter confirm is not set true')

        if get_jwt_identity() == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all instances in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return api_base.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity():
                return api_base.error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()

        waiting_for = []
        tasks_by_node = {}
        for inst in instance.Instances([partial(baseobject.namespace_filter, namespace),
                                        instance.active_states_filter]):
            # If this instance is not on a node, just do the DB cleanup locally
            dbplacement = inst.placement
            if not dbplacement.get('node'):
                node = config.NODE_NAME
            else:
                node = dbplacement['node']

            tasks_by_node.setdefault(node, [])
            tasks_by_node[node].append(DeleteInstanceTask(inst.uuid))
            waiting_for.append(inst.uuid)

        for node in tasks_by_node:
            db.enqueue(node, {'tasks': tasks_by_node[node]})

        return waiting_for


class InstanceInterfaces(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        out = []
        for ni in networkinterface.interfaces_for_instance(instance_from_db):
            out.append(ni.external_view())
        return out


class InstanceEvents(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        return list(db.get_events('instance', instance_uuid))


class InstanceRebootSoft(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance reboot soft'):
            instance_from_db.add_event('api', 'soft reboot')
            return instance_from_db.reboot(hard=False)


class InstanceRebootHard(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance reboot hard'):
            instance_from_db.add_event('api', 'hard reboot')
            return instance_from_db.reboot(hard=True)


class InstancePowerOff(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance power off'):
            instance_from_db.add_event('api', 'poweroff')
            return instance_from_db.power_off()


class InstancePowerOn(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance power on'):
            instance_from_db.add_event('api', 'poweron')
            return instance_from_db.power_on()


class InstancePause(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance pause'):
            instance_from_db.add_event('api', 'pause')
            return instance_from_db.pause()


class InstanceUnpause(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_uuid=None, instance_from_db=None):
        with db.get_lock(
                'instance', None, instance_uuid, ttl=120, timeout=120,
                op='Instance unpause'):
            instance_from_db.add_event('api', 'unpause')
            return instance_from_db.unpause()


def _safe_get_network_interface(interface_uuid):
    ni = NetworkInterface.from_db(interface_uuid)
    if not ni:
        return None, None, api_base.error(404, 'interface not found')

    log = LOG.with_fields({'network': ni.network_uuid,
                           'networkinterface': ni.uuid})

    n = net.Network.from_db(ni.network_uuid)
    if not n:
        log.info('Network not found or deleted')
        return None, None, api_base.error(404, 'interface network not found')

    if get_jwt_identity() not in [n.namespace, 'system']:
        log.info('Interface not found, failed ownership test')
        return None, None, api_base.error(404, 'interface not found')

    i = instance.Instance.from_db(ni.instance_uuid)
    if get_jwt_identity() not in [i.namespace, 'system']:
        log.with_object(i).info('Instance not found, failed ownership test')
        return None, None, api_base.error(404, 'interface not found')

    return ni, n, None


class Interface(api_base.Resource):
    @jwt_required
    @api_base.redirect_to_network_node
    def get(self, interface_uuid=None):
        ni, _, err = _safe_get_network_interface(interface_uuid)
        if err:
            return err
        return ni.external_view()


class InterfaceFloat(api_base.Resource):
    @jwt_required
    def post(self, interface_uuid=None):
        ni, n, err = _safe_get_network_interface(interface_uuid)
        if err:
            return err

        err = _assign_floating_ip(ni)
        if err:
            return err

        db.enqueue('networknode',
                   FloatNetworkInterfaceTask(n.uuid, interface_uuid))


class InterfaceDefloat(api_base.Resource):
    @jwt_required
    def post(self, interface_uuid=None):
        ni, n, err = _safe_get_network_interface(interface_uuid)
        if err:
            return err

        float_net = net.Network.from_db('floating')
        if not float_net:
            return api_base.error(404, 'floating network not found')

        # Address is freed as part of the job, so code is "unbalanced" compared
        # to above for reasons.
        db.enqueue('networknode',
                   DefloatNetworkInterfaceTask(n.uuid, interface_uuid))


class InstanceMetadatas(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        md = db.get_metadata('instance', instance_uuid)
        if not md:
            return {}
        return md

    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def post(self, instance_uuid=None, key=None, value=None, instance_from_db=None):
        return _metadata_putpost('instance', instance_uuid, key, value)


class InstanceMetadata(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def put(self, instance_uuid=None, key=None, value=None, instance_from_db=None):
        return _metadata_putpost('instance', instance_uuid, key, value)

    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def delete(self, instance_uuid=None, key=None, instance_from_db=None):
        if not key:
            return api_base.error(400, 'no key specified')

        with db.get_lock('metadata', 'instance', instance_uuid, op='Instance metadata delete'):
            md = db.get_metadata('instance', instance_uuid)
            if md is None or key not in md:
                return api_base.error(404, 'key not found')
            del md[key]
            db.persist_metadata('instance', instance_uuid, md)


class InstanceConsoleData(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    def get(self, instance_uuid=None, length=None, instance_from_db=None):
        parsed_length = None

        if not length:
            parsed_length = -1
        else:
            try:
                parsed_length = int(length)
            except ValueError:
                pass

            # This is done this way so that there is no active traceback for
            # the api_base.error call, otherwise it would be logged.
            if parsed_length is None:
                return api_base.error(400, 'length is not an integer')

        resp = flask.Response(
            instance_from_db.get_console_data(parsed_length),
            mimetype='text/plain')
        resp.status_code = 200
        return resp


class Images(api_base.Resource):
    @jwt_required
    def get(self, node=None):
        retval = []
        for i in Artifacts(filters=[
                partial(artifact_type_filter,
                        Artifact.TYPE_IMAGE),
                baseobject.active_states_filter]):
            b = i.most_recent_index
            if b:
                if not node:
                    retval.append(i.external_view())
                elif node in b.locations:
                    retval.append(i.external_view())
        return retval

    @jwt_required
    def post(self, url=None):
        db.add_event('image', url, 'api', 'cache', None, None)

        # We ensure that the image exists in the database in an initial state
        # here so that it will show up in image list requests. The image is
        # fetched by the queued job later.
        img = images.Image.new(url)
        db.enqueue(config.NODE_NAME, {
            'tasks': [FetchImageTask(url)],
        })
        return img.external_view()


class ImageEvents(api_base.Resource):
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
        return api_base.error(404, 'network does not exist')

    if n.is_dead():
        # The network has been deleted. No need to attempt further effort.
        LOG.with_fields({'network_uuid': n.uuid,
                         'state': n.state.value
                         }).warning('delete_network: network is dead')
        return api_base.error(404, 'network is deleted')

    n.add_event('api', 'delete')
    db.enqueue('networknode', DestroyNetworkTask(n.uuid))


class Network(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        return network_from_db.external_view()

    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    @api_base.redirect_to_network_node
    def delete(self, network_uuid=None, network_from_db=None):
        if network_uuid == 'floating':
            return api_base.error(403, 'you cannot delete the floating network')

        n = net.Network.from_db(network_from_db.uuid)
        if not n:
            LOG.with_fields({'network_uuid': n.uuid}).warning(
                'delete_network: network does not exist')
            return api_base.error(404, 'network does not exist')

        # We only delete unused networks
        ifaces = list(networkinterface.interfaces_for_network(n))
        if len(ifaces) > 0:
            for iface in ifaces:
                LOG.withFields({'network_interface': iface.uuid,
                                'state': iface.state}).info('Blocks network delete')
            return api_base.error(403, 'you cannot delete an in use network')

        # Check if network has already been deleted
        if network_from_db.state.value in dbo.STATE_DELETED:
            return

        _delete_network(network_from_db)


class Networks(api_base.Resource):
    @marshal_with({
        'uuid': fields.String,
        'vxlan_id': fields.Integer,
        'netblock': fields.String,
        'provide_dhcp': fields.Boolean,
        'provide_nat': fields.Boolean,
        'namespace': fields.String,
        'name': fields.String,
        'state': fields.String
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
                return api_base.error(400, 'network is below minimum size of /29')
        except ValueError as e:
            return api_base.error(400, 'cannot parse netblock: %s' % e,
                                  suppress_traceback=True)

        if not namespace:
            namespace = get_jwt_identity()

        # If accessing a foreign name namespace, we need to be an admin
        if get_jwt_identity() not in [namespace, 'system']:
            return api_base.error(
                401,
                'only admins can create resources in a different namespace')

        network = net.Network.new(name, namespace, netblock, provide_dhcp,
                                  provide_nat)
        return network.external_view()

    @jwt_required
    @api_base.redirect_to_network_node
    def delete(self, confirm=False, namespace=None):
        """Delete all networks in the namespace."""

        if confirm is not True:
            return api_base.error(400, 'parameter confirm is not set true')

        if get_jwt_identity() == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all networks in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return api_base.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity():
                return api_base.error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()

        networks_del = []
        networks_unable = []
        for n in net.Networks([partial(baseobject.namespace_filter, namespace),
                               baseobject.active_states_filter]):
            if len(list(networkinterface.interfaces_for_network(n))) > 0:
                LOG.with_object(n).warning(
                    'Network in use, cannot be deleted by delete-all')
                networks_unable.append(n.uuid)
                continue

            _delete_network(n)
            networks_del.append(n.uuid)

        if networks_unable:
            return api_base.error(403, {'deleted': networks_del,
                                        'unable': networks_unable})

        return networks_del


class NetworkEvents(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        return list(db.get_events('network', network_uuid))


class NetworkInterfacesEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    @api_base.requires_network_active
    def get(self, network_uuid=None, network_from_db=None):
        out = []
        for ni in networkinterface.interfaces_for_network(self.network):
            out.append(ni.external_view())
        return out


class NetworkMetadatas(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def get(self, network_uuid=None, network_from_db=None):
        md = db.get_metadata('network', network_uuid)
        if not md:
            return {}
        return md

    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def post(self, network_uuid=None, key=None, value=None, network_from_db=None):
        return _metadata_putpost('network', network_uuid, key, value)


class NetworkMetadata(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def put(self, network_uuid=None, key=None, value=None, network_from_db=None):
        return _metadata_putpost('network', network_uuid, key, value)

    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    def delete(self, network_uuid=None, key=None, network_from_db=None):
        if not key:
            return api_base.error(400, 'no key specified')

        with db.get_lock('metadata', 'network', network_uuid, op='Network metadata delete'):
            md = db.get_metadata('network', network_uuid)
            if md is None or key not in md:
                return api_base.error(404, 'key not found')
            del md[key]
            db.persist_metadata('network', network_uuid, md)


class NetworkPing(api_base.Resource):
    @jwt_required
    @api_base.arg_is_network_uuid
    @api_base.requires_network_ownership
    @api_base.redirect_to_network_node
    @api_base.requires_network_active
    def get(self, network_uuid=None, address=None, network_from_db=None):
        ipm = IPManager.from_db(network_uuid)
        if not ipm.is_in_range(address):
            return api_base.error(400, 'ping request for address outside network block')

        n = net.Network.from_db(network_uuid)
        if not n:
            return api_base.error(404, 'network %s not found' % network_uuid)

        out, err = util.execute(
            None, 'ip netns exec %s ping -c 10 %s' % (
                network_uuid, address),
            check_exit_code=[0, 1])
        return {
            'stdout': out,
            'stderr': err
        }


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

api.add_resource(api_blob.BlobEndpoint, '/blob/<blob_uuid>')

api.add_resource(Instances, '/instances')
api.add_resource(Instance, '/instances/<instance_uuid>')
api.add_resource(InstanceEvents, '/instances/<instance_uuid>/events')
api.add_resource(InstanceInterfaces, '/instances/<instance_uuid>/interfaces')
api.add_resource(api_snapshot.InstanceSnapshotEndpoint,
                 '/instances/<instance_uuid>/snapshot')
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

api.add_resource(api_label.LabelEndpoint, '/label/<label_name>')

api.add_resource(Networks, '/networks')
api.add_resource(Network, '/networks/<network_uuid>')
api.add_resource(NetworkEvents, '/networks/<network_uuid>/events')
api.add_resource(NetworkInterfacesEndpoint,
                 '/networks/<network_uuid>/interfaces')
api.add_resource(NetworkMetadatas, '/networks/<network_uuid>/metadata')
api.add_resource(NetworkMetadata,
                 '/networks/<network_uuid>/metadata/<key>')
api.add_resource(NetworkPing,
                 '/networks/<network_uuid>/ping/<address>')

api.add_resource(NodesEndpoint, '/nodes')
