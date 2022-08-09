from collections import defaultdict
from functools import partial
import flask
from flask_jwt_extended import jwt_required, get_jwt_identity
import re
import uuid

from shakenfist.artifact import (
    Artifact, BLOB_URL, LABEL_URL, SNAPSHOT_URL, UPLOAD_URL)
from shakenfist import baseobject
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist import instance
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import network as sfnet  # Unfortunate, but we have an API arg
# called network too.
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
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    def get(self, instance_ref=None, instance_from_db=None):
        return instance_from_db.external_view()

    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_namespace_exist
    def delete(self, instance_ref=None, instance_from_db=None, namespace=None):
        # Check if instance has already been deleted
        if instance_from_db.state.value == dbo.STATE_DELETED:
            return api_base.error(404, 'instance not found')

        # If a namespace is specified, ensure the instance is in it
        if namespace:
            if instance_from_db.namespace != namespace:
                return api_base.error(404, 'instance not in namespace')

        # If this instance is not on a node, just do the DB cleanup locally
        placement = instance_from_db.placement
        if not placement.get('node'):
            node = config.NODE_NAME
        else:
            node = placement['node']

        instance_from_db.enqueue_delete_remote(node)

        # Return UUID in case API call was made using object name
        return instance_from_db.external_view()


def _artifact_safety_checks(a):
    if not a:
        return api_base.error(404, 'artifact not found')
    if a.state.value != Artifact.STATE_CREATED:
        return api_base.error(
            404, 'artifact not ready (state=%s)' % a.state.value)
    if get_jwt_identity()[0] != 'system':
        if not a.shared and a.namespace != get_jwt_identity()[0]:
            return api_base.error(404, 'artifact not found')
    return


class InstancesEndpoint(api_base.Resource):
    @jwt_required()
    def get(self, all=False):
        with etcd.ThreadLocalReadOnlyCache():
            filters = [partial(baseobject.namespace_filter,
                               get_jwt_identity()[0])]
            if not all:
                filters.append(instance.active_states_filter)

            retval = []
            for i in instance.Instances(filters):
                # This forces the instance through the external view rehydration
                retval.append(i.external_view())
            return retval

    @jwt_required()
    @api_base.requires_namespace_exist
    def post(self, name=None, cpus=None, memory=None, network=None, disk=None,
             ssh_key=None, user_data=None, placed_on=None, namespace=None,
             video=None, uefi=False, configdrive=None, metadata=None,
             nvram_template=None, secure_boot=False, side_channels=None):
        global SCHEDULER

        instance_uuid = str(uuid.uuid4())

        # There is a wart in the qemu machine type naming. 'pc' is shorthand for
        # "the most recent version of pc-i440fx", whereas 'q35' is shorthand for
        # "the most recent version of pc-q35" you have. We default to i440fx
        # unless you specify secure boot. We could infer the machine type from
        # the use of secure boot in the libvirt template later, but I want to be
        # more explicit in case we want to add other machine types later (microvm
        # for example).
        machine_type = 'pc'

        if not namespace:
            namespace = get_jwt_identity()[0]

        # If accessing a foreign namespace, we need to be an admin
        if get_jwt_identity()[0] not in [namespace, 'system']:
            return api_base.error(404, 'namespace not found')

        # Check that the instance name is safe for use as a DNS host name
        if name != re.sub(r'([^a-zA-Z0-9\-])', '', name) or len(name) > 63:
            return api_base.error(
                400, ('instance name %s is not useable as a DNS and Linux host name. '
                      'That is, less than 63 characters and in the character set: '
                      'a-z, A-Z, 0-9, or hyphen (-).' % name))

        # Secure boot requires UEFI
        if secure_boot and not uefi:
            return api_base.error(400, 'secure boot requires UEFI be enabled')

        if secure_boot:
            machine_type = 'q35'

        # If we are placed, make sure that node exists
        if placed_on:
            n = Node.from_db(placed_on)
            if not n:
                return api_base.error(404, 'Specified node does not exist')
            if n.state.value != Node.STATE_CREATED:
                return api_base.error(404, 'Specified node not ready')

        # Make sure we've been given a valid configdrive option
        if not configdrive:
            configdrive = 'openstack-disk'
        elif configdrive not in ['openstack-disk', 'none']:
            return api_base.error(400, 'invalid config drive type: "%s"' % configdrive)

        # Sanity check and lookup blobs for disks where relevant
        if not disk:
            return api_base.error(400, 'instance must specify at least one disk')

        transformed_disk = []
        for d in disk:
            if not isinstance(d, dict):
                return api_base.error(400, 'disk specification should contain JSON objects')

            # Convert internal shorthand forms into specific blobs
            disk_base = d.get('base')
            if util_general.noneish(disk_base):
                d['disk_base'] = None

            elif disk_base.startswith('label:'):
                label = disk_base[len('label:'):]
                a = Artifact.from_url(
                    Artifact.TYPE_LABEL,
                    '%s%s/%s' % (LABEL_URL, get_jwt_identity()[0], label),
                    namespace=namespace)
                err = _artifact_safety_checks(a)
                if err:
                    return err

                blob_uuid = a.resolve_to_blob()
                if not blob_uuid:
                    return api_base.error(404, 'Could not resolve label %s to a blob' % label)
                d['blob_uuid'] = blob_uuid

            elif disk_base.startswith(SNAPSHOT_URL):
                a = Artifact.from_db(disk_base[len(SNAPSHOT_URL):])
                err = _artifact_safety_checks(a)
                if err:
                    return err

                blob_uuid = a.resolve_to_blob()
                if not blob_uuid:
                    return api_base.error(404, 'Could not resolve snapshot to a blob')
                d['blob_uuid'] = blob_uuid

            elif disk_base.startswith(UPLOAD_URL) or disk_base.startswith(LABEL_URL):
                if disk_base.startswith(UPLOAD_URL):
                    a = Artifact.from_url(Artifact.TYPE_IMAGE, disk_base,
                                          namespace=namespace)
                else:
                    a = Artifact.from_url(Artifact.TYPE_LABEL, disk_base,
                                          namespace=namespace)
                err = _artifact_safety_checks(a)
                if err:
                    return err

                blob_uuid = a.resolve_to_blob()
                if not blob_uuid:
                    return api_base.error(404, 'Could not resolve artifact to a blob')
                d['blob_uuid'] = blob_uuid

            elif disk_base.startswith(BLOB_URL):
                d['blob_uuid'] = disk_base[len(BLOB_URL):]

            else:
                # We ensure that the image exists in the database in an initial state
                # here so that it will show up in image list requests. The image is
                # fetched by the queued job later.
                Artifact.from_url(Artifact.TYPE_IMAGE, disk_base,
                                  namespace=namespace, create_if_new=True)

            transformed_disk.append(d)

        disk = transformed_disk

        # Perform a similar translation for NVRAM templates, turning them into
        # blob UUIDs.
        if nvram_template:
            original_template = nvram_template
            if nvram_template.startswith('label:'):
                label = nvram_template[len('label:'):]
                url = '%s%s/%s' % (LABEL_URL, get_jwt_identity()[0], label)
                a = Artifact.from_url(
                    Artifact.TYPE_LABEL, url, namespace=namespace)
                err = _artifact_safety_checks(a)
                if err:
                    return err

                blob_uuid = a.resolve_to_blob()
                if not blob_uuid:
                    return api_base.error(404, 'Could not resolve label %s to a blob' % label)
                LOG.with_field('instance', instance_uuid).with_fields({
                    'original_template': original_template,
                    'label': label,
                    'source_url': url,
                    'artifact': a.uuid,
                    'blob': blob_uuid
                }).info('NVRAM template label resolved')
                nvram_template = blob_uuid

            elif nvram_template.startswith(BLOB_URL):
                nvram_template = nvram_template[len(BLOB_URL):]
                LOG.with_field('instance', instance_uuid).with_fields({
                    'original_template': original_template,
                    'blob': nvram_template
                }).info('NVRAM template URL converted')
                nvram_template = blob_uuid

        # Make sure that we are on a compatible machine type if we specify any
        # IDE attachments.
        if machine_type == 'q35':
            for d in disk:
                if d.get('bus') == 'ide':
                    return api_base.error(
                        400, 'secure boot machine type does not support IDE')

        if network:
            for netdesc in network:
                if not isinstance(netdesc, dict):
                    return api_base.error(
                        400, 'network specification should contain JSON objects')

                if 'network_uuid' not in netdesc:
                    return api_base.error(
                        400, 'network specification is missing network_uuid')

                # Allow network to be specified by name or UUID (and error early
                # if not found)
                try:
                    n = sfnet.Network.from_db_by_ref(netdesc['network_uuid'])
                except exceptions.MultipleObjects as e:
                    return api_base.error(400, str(e))

                if not n:
                    return api_base.error(
                        404, 'network %s not found' % netdesc['network_uuid'])
                netdesc['network_uuid'] = n.uuid

                if netdesc.get('address') and not util_general.noneish(netdesc.get('address')):
                    # The requested address must be within the ip range specified
                    # for that virtual network, unless it is equivalent to "none".
                    ipm = IPManager.from_db(n.uuid)
                    if not ipm.is_in_range(netdesc['address']):
                        return api_base.error(400,
                                              'network specification requests an address outside the '
                                              'range of the network')

                if n.state.value != sfnet.Network.STATE_CREATED:
                    return api_base.error(406, 'network %s is not ready (%s)' % (n.uuid, n.state.value))
                if n.namespace != namespace:
                    return api_base.error(404, 'network %s does not exist' % n.uuid)

        if not video:
            video = {'model': 'cirrus', 'memory': 16384}
        else:
            if 'model' not in video:
                return api_base.error(400, 'video specification requires "model"')
            if 'memory' not in video:
                return api_base.error(400, 'video specification requires "memory"')

        # Validate metadata before instance creation
        if metadata:
            if not isinstance(metadata, dict):
                return api_base.error(400, 'metadata must be a dictionary')
            for k, v in metadata.items():
                err = _validate_instance_metadata(k, v)
                if err:
                    return err

        # Create instance object
        inst = instance.Instance.new(
            instance_uuid=instance_uuid,
            name=name,
            disk_spec=disk,
            memory=memory,
            cpus=cpus,
            ssh_key=ssh_key,
            user_data=user_data,
            namespace=namespace,
            video=video,
            uefi=uefi,
            configdrive=configdrive,
            requested_placement=placed_on,
            nvram_template=nvram_template,
            secure_boot=secure_boot,
            machine_type=machine_type,
            side_channels=side_channels
        )

        # Initialise metadata
        if metadata:
            db.persist_metadata('instance', inst.uuid, metadata)
        else:
            db.persist_metadata('instance', inst.uuid, {})

        # Allocate IP addresses
        order = 0
        float_tasks = []
        updated_networks = []
        if network:
            for netdesc in network:
                n = sfnet.Network.from_db(netdesc['network_uuid'])
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
                try:
                    if 'address' in netdesc and util_general.noneish(netdesc['address']):
                        netdesc['address'] = None
                    else:
                        with db.get_lock('ipmanager', None,  netdesc['network_uuid'],
                                         ttl=120, op='Network allocate IP'):
                            ipm = IPManager.from_db(netdesc['network_uuid'])
                            if 'address' not in netdesc or not netdesc['address']:
                                netdesc['address'] = ipm.get_random_free_address(
                                    inst.unique_label())
                                inst.add_event2(
                                    'allocated ip address', extra=netdesc)
                            else:
                                if not ipm.reserve(netdesc['address'], inst.unique_label()):
                                    m = 'failed to reserve an IP on network %s' % (
                                        netdesc['network_uuid'])
                                    inst.enqueue_delete_due_error(m)
                                    return api_base.error(409, 'address %s in use' %
                                                          netdesc['address'])

                            ipm.persist()
                except exceptions.CongestedNetwork as e:
                    inst.enqueue_delete_due_error(
                        'cannot allocate address: %s' % e)
                    return api_base.error(507, str(e))

                if 'model' not in netdesc or not netdesc['model']:
                    netdesc['model'] = 'virtio'

                iface_uuid = str(uuid.uuid4())
                LOG.with_object(inst).with_object(n).withFields({
                    'networkinterface': iface_uuid
                }).info('Interface allocated')
                ni = NetworkInterface.new(
                    iface_uuid, netdesc, inst.uuid, order)
                order += 1

                try:
                    if 'float' in netdesc and netdesc['float']:
                        err = api_util.assign_floating_ip(ni)
                        if err:
                            inst.enqueue_delete_due_error(
                                'interface float failed: %s' % err)
                            return err

                        float_tasks.append(FloatNetworkInterfaceTask(
                            netdesc['network_uuid'], iface_uuid))
                except exceptions.CongestedNetwork as e:
                    inst.enqueue_delete_due_error(
                        'cannot allocate address: %s' % e)
                    return api_base.error(507, str(e))

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
            inst.add_event2(
                'schedule failed, insufficient resources: %s' % str(e))
            inst.enqueue_delete_due_error('scheduling failed')
            return api_base.error(507, str(e), suppress_traceback=True)

        except exceptions.CandidateNodeNotFoundException as e:
            inst.add_event2('schedule failed, node not found: %s' % str(e))
            inst.enqueue_delete_due_error('scheduling failed')
            return api_base.error(404, 'node not found: %s' % e, suppress_traceback=True)

        # Record placement
        inst.place_instance(placement)

        # Create a queue entry for the instance start
        tasks = [PreflightInstanceTask(inst.uuid, network)]
        for disk in inst.disk_spec:
            disk_base = disk.get('base')
            if disk.get('blob_uuid'):
                tasks.append(FetchImageTask(
                    '%s%s' % (BLOB_URL, disk['blob_uuid']),
                    namespace=namespace, instance_uuid=inst.uuid))
            elif not util_general.noneish(disk_base):
                tasks.append(FetchImageTask(
                    disk['base'],
                    namespace=namespace, instance_uuid=inst.uuid))
        tasks.append(StartInstanceTask(inst.uuid, network))
        tasks.extend(float_tasks)

        # Enqueue creation tasks on desired node task queue
        etcd.enqueue(placement, {'tasks': tasks})
        return inst.external_view()

    @jwt_required()
    @api_base.requires_namespace_exist
    def delete(self, confirm=False, namespace=None):
        """Delete all instances in the namespace."""

        if confirm is not True:
            return api_base.error(400, 'parameter confirm is not set true')

        if get_jwt_identity()[0] == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all instances in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return api_base.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity()[0]:
                return api_base.error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()[0]

        waiting_for = []
        tasks_by_node = defaultdict(list)
        for inst in instance.Instances([partial(baseobject.namespace_filter, namespace)]):
            # If this instance is not on a node, just do the DB cleanup locally
            db_placement = inst.placement
            if not db_placement.get('node'):
                node = config.NODE_NAME
            else:
                node = db_placement['node']

            tasks_by_node[node].append(DeleteInstanceTask(inst.uuid))
            waiting_for.append(inst.uuid)

        for node in tasks_by_node:
            etcd.enqueue(node, {'tasks': tasks_by_node[node]})

        return waiting_for


class InstanceInterfacesEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    def get(self, instance_ref=None, instance_from_db=None):
        out = []
        for iface_uuid in instance_from_db.interfaces:
            ni, _, err = api_util.safe_get_network_interface(iface_uuid)
            if err:
                return err
            out.append(ni.external_view())
        return out


class InstanceEventsEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_to_eventlog_node
    def get(self, instance_ref=None, instance_from_db=None):
        with eventlog.EventLog('instance', instance_from_db.uuid) as eventdb:
            return list(eventdb.read_events())


class InstanceRebootSoftEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            with instance_from_db.get_lock(op='Instance reboot soft'):
                return instance_from_db.reboot(hard=False)
        except exceptions.InvalidLifecycleState as e:
            return api_base.error(409, e)


class InstanceRebootHardEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            with instance_from_db.get_lock(op='Instance reboot hard'):
                return instance_from_db.reboot(hard=True)
        except exceptions.InvalidLifecycleState as e:
            return api_base.error(409, e)


class InstancePowerOffEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            with instance_from_db.get_lock(op='Instance power off'):
                return instance_from_db.power_off()
        except exceptions.InvalidLifecycleState as e:
            return api_base.error(409, e)


class InstancePowerOnEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            with instance_from_db.get_lock(op='Instance power on'):
                return instance_from_db.power_on()
        except exceptions.InvalidLifecycleState as e:
            return api_base.error(409, e)


class InstancePauseEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            with instance_from_db.get_lock(op='Instance pause'):
                return instance_from_db.pause()
        except exceptions.InvalidLifecycleState as e:
            return api_base.error(409, e)


class InstanceUnpauseEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            with instance_from_db.get_lock(op='Instance unpause'):
                return instance_from_db.unpause()
        except exceptions.InvalidLifecycleState as e:
            return api_base.error(409, e)


class InstanceMetadatasEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    def get(self, instance_ref=None, instance_from_db=None):
        md = db.get_metadata('instance', instance_from_db.uuid)
        if not md:
            return {}
        return md

    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    def post(self, instance_ref=None, key=None, value=None, instance_from_db=None):
        err = _validate_instance_metadata(key, value)
        if err:
            return err
        return api_util.metadata_putpost('instance', instance_from_db.uuid, key, value)


def _validate_instance_metadata(key, value):
    # Reserved key "tags" should be validated to avoid unexpected failures
    if key == instance.Instance.METADATA_KEY_TAGS:
        if not isinstance(value, list):
            return api_base.error(400, 'value for "tags" key should be a JSON list')

    # Reserved key "affinity" should be validated to avoid unexpected
    # failures during instance creation.
    elif key == instance.Instance.METADATA_KEY_AFFINITY:
        if not isinstance(value, dict):
            return api_base.error(
                400, 'value for "affinity" key should be a valid JSON dictionary')

        for key_type, dv in value.items():
            if key_type not in ('cpu', 'disk', 'instance'):
                return api_base.error(
                    400, 'can only set affinity for cpu, disk or instance')

            if not isinstance(dv, dict):
                return api_base.error(
                    400, 'value for affinity key should be a dictionary')
            for v in dv.values():
                try:
                    int(v)
                except ValueError:
                    return api_base.error(
                        400, 'affinity dictionary values should be integers')


class InstanceMetadataEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    def put(self, instance_ref=None, key=None, value=None, instance_from_db=None):
        err = _validate_instance_metadata(key, value)
        if err:
            return err
        return api_util.metadata_putpost(
            'instance', instance_from_db.uuid, key, value)

    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    def delete(self, instance_ref=None, key=None, instance_from_db=None):
        if not key:
            return api_base.error(400, 'no key specified')

        with instance_from_db.get_lock(op='Instance metadata delete'):
            md = db.get_metadata('instance', instance_from_db.uuid)
            if md is None or key not in md:
                return api_base.error(404, 'key not found')
            del md[key]
            db.persist_metadata('instance', instance_from_db.uuid, md)


class InstanceConsoleDataEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    def get(self, instance_ref=None, length=None, instance_from_db=None):
        parsed_length = None

        if not length:
            parsed_length = 10240
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
            mimetype='applicaton/octet-stream')
        resp.status_code = 200
        return resp

    @jwt_required()
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    def delete(self, instance_ref=None, instance_from_db=None):
        instance_from_db.delete_console_data()
