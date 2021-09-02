from functools import partial
import flask
from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import jwt_required
import re
import uuid

from shakenfist.artifact import (
    Artifact, BLOB_URL, LABEL_URL, SNAPSHOT_URL)
from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import exceptions
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist import instance
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import net
from shakenfist.networkinterface import NetworkInterface
from shakenfist.node import Node
from shakenfist import scheduler
from shakenfist.tasks import (
    DeleteInstanceTask,
    FetchImageTask,
    PreflightInstanceTask,
    StartInstanceTask,
    FloatNetworkInterfaceTask
)
from shakenfist.util import general as util_general


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


SCHEDULER = None


class InstanceEndpoint(api_base.Resource):
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


class InstancesEndpoint(api_base.Resource):
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
                blob_uuid = a.most_recent_index.get('blob_uuid')
                if not blob_uuid:
                    return api_base.error(404, 'label %s not found (no versions)' % label)
                d['blob_uuid'] = blob_uuid

            elif disk_base.startswith(SNAPSHOT_URL):
                a = Artifact.from_db(disk_base[len(SNAPSHOT_URL):])
                blob_uuid = a.most_recent_index.get('blob_uuid')
                if not blob_uuid:
                    return api_base.error(404, 'snapshot not found (no versions)')
                d['blob_uuid'] = blob_uuid

            elif disk_base.startswith(BLOB_URL):
                d['blob_uuid'] = disk_base[len(BLOB_URL):]

            else:
                # We ensure that the image exists in the database in an initial state
                # here so that it will show up in image list requests. The image is
                # fetched by the queued job later.
                Artifact.from_url(Artifact.TYPE_IMAGE, disk_base)

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
                if netdesc.get('address') and not util_general.noneish(netdesc.get('address')):
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
        else:
            if 'model' not in video:
                return api_base.error(400, 'video specification requires "model"')
            if 'memory' not in video:
                return api_base.error(400, 'video specification requires "memory"')

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
        updated_networks = []
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
                if 'address' in netdesc and util_general.noneish(netdesc['address']):
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
                    err = api_util.assign_floating_ip(ni)
                    if err:
                        inst.enqueue_delete_due_error(
                            'interface float failed: %s' % err)
                        return err

                    float_tasks.append(FloatNetworkInterfaceTask(
                        netdesc['network_uuid'], iface_uuid))

                # Include the interface uuid in the network description we
                # pass through to the instance start task.
                netdesc['iface_uuid'] = iface_uuid
                updated_networks.append(netdesc)

        # Store interfaces soon as they are allocated to the instance
        inst.interfaces = [i['iface_uuid'] for i in updated_networks]

        if not SCHEDULER:
            SCHEDULER = scheduler.Scheduler()

        try:
            # Have we been placed?
            if not placed_on:
                candidates = SCHEDULER.place_instance(inst, updated_networks)
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


class InstanceInterfacesEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        out = []
        for iface_uuid in instance_from_db.interfaces:
            ni, _, err = api_util.safe_get_network_interface(iface_uuid)
            if err:
                return err
            out.append(ni.external_view())
        return out


class InstanceEventsEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def get(self, instance_uuid=None, instance_from_db=None):
        return list(db.get_events('instance', instance_uuid))


class InstanceRebootSoftEndpoint(api_base.Resource):
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


class InstanceRebootHardEndpoint(api_base.Resource):
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


class InstancePowerOffEndpoint(api_base.Resource):
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


class InstancePowerOnEndpoint(api_base.Resource):
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


class InstancePauseEndpoint(api_base.Resource):
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


class InstanceUnpauseEndpoint(api_base.Resource):
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


class InstanceMetadatasEndpoint(api_base.Resource):
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
        return api_util.metadata_putpost('instance', instance_uuid, key, value)


class InstanceMetadataEndpoint(api_base.Resource):
    @jwt_required
    @api_base.arg_is_instance_uuid
    @api_base.requires_instance_ownership
    def put(self, instance_uuid=None, key=None, value=None, instance_from_db=None):
        return api_util.metadata_putpost('instance', instance_uuid, key, value)

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


class InstanceConsoleDataEndpoint(api_base.Resource):
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
