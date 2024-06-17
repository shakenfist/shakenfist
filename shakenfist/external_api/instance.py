# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist:
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:

from collections import defaultdict
from functools import partial
import flask
from flask_jwt_extended import get_jwt_identity
from flasgger import swag_from
import os
import re
from shakenfist_utilities import api as sf_api, logs
import symbolicmode
import uuid

from shakenfist.agentoperation import AgentOperation
from shakenfist.artifact import (
    Artifact, BLOB_URL, LABEL_URL, SNAPSHOT_URL, UPLOAD_URL)
from shakenfist import baseobject
from shakenfist.blob import Blob
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist.external_api import (
    agentoperation as api_agentoperation,
    base as api_base,
    util as api_util)
from shakenfist import instance
from shakenfist import ipam
from shakenfist.namespace import namespace_is_trusted
from shakenfist import network as sfnet
from shakenfist.networkinterface import NetworkInterface
from shakenfist.node import Node
from shakenfist import scheduler
from shakenfist.tasks import (
    DeleteInstanceTask,
    FetchImageTask,
    PreflightInstanceTask,
    StartInstanceTask,
    FloatNetworkInterfaceTask,
    PreflightAgentOperationTask,
    HotPlugInstanceInterfaceTask
)
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


SCHEDULER = None


instance_get_example = """{
    "agent_start_time": null,
    "agent_state": null,
    "agent_system_boot_time": null,
    "configdrive": "openstack-disk",
    "console_port": null,
    "cpus": 1,
    "disk_spec": [
        {
            "base": "debian:11",
            "bus": null,
            "size": 20,
            "type": "disk"
        }
    ],
    "disks": [],
    "error_message": null,
    "interfaces": [],
    "machine_type": "pc",
    "memory": 1024,
    "metadata": {},
    "name": "example",
    "namespace": "system",
    "node": "sf-3",
    "nvram_template": null,
    "power_state": "initial",
    "secure_boot": false,
    "side_channels": [
        "sf-agent"
    ],
    "ssh_key": null,
    "state": "preflight",
    "uefi": false,
    "user_data": null,
    "uuid": "d51aa352-368c-484c-9e4c-4542927b4277",
    "vdi_port": null,
    "vdi_tls_port": null,
    "version": 12,
    "video": {
        "memory": 16384,
        "model": "cirrus",
        "vdi": "spice"
    }
}"""


instance_get_example_deleted = """{
    "agent_start_time": null,
    "agent_state": "not ready (instance powered off)",
    "agent_system_boot_time": null,
    "configdrive": "openstack-disk",
    "console_port": null,
    "cpus": 1,
    "disk_spec": [
        {
            "base": "debian:11",
            "bus": null,
            "size": 20,
            "type": "disk"
        }
    ],
    "disks": [
        {
            "blob_uuid": "5117f778-b214-4184-8358-f2c7376b76db",
            "bus": "virtio",
            "device": "vda",
            "size": 20,
            "snapshot_ignores": false
        },
        {
            "blob_uuid": null,
            "bus": "virtio",
            "device": "vdb",
            "size": null,
            "snapshot_ignores": true
        }
    ],
    "error_message": null,
    "interfaces": [],
    "machine_type": "pc",
    "memory": 1024,
    "metadata": {},
    "name": "example",
    "namespace": "system",
    "node": "sf-3",
    "nvram_template": null,
    "power_state": "off",
    "secure_boot": false,
    "side_channels": [
        "sf-agent"
    ],
    "ssh_key": null,
    "state": "deleted",
    "uefi": false,
    "user_data": null,
    "uuid": "d51aa352-368c-484c-9e4c-4542927b4277",
    "vdi_port": null,
    "vdi_tls_port": null,
    "version": 12,
    "video": {
        "memory": 16384,
        "model": "cirrus",
        "vdi": "spice"
    }
}"""


class InstanceEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Get instance information.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True),
         ('namespace', 'body', 'namespace',
          'The namespace to contain the network.', False)],
        [(200, 'Information about a single instance.', instance_get_example),
         (404, 'Instance not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None, namespace=None):
        return instance_from_db.external_view()

    @swag_from(api_base.swagger_helper(
        'instances', 'Delete an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True),
         ('namespace', 'body', 'namespace',
          'The namespace containing the instance', False)],
        [(200, 'Information about the instance post delete.',
          instance_get_example_deleted),
         (404, 'Instance not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def delete(self, instance_ref=None, instance_from_db=None, namespace=None):
        # Check if instance has already been deleted
        if instance_from_db.state.value == dbo.STATE_DELETED:
            return sf_api.error(404, 'instance not found')

        # If a namespace is specified, ensure the instance is in it
        if namespace:
            if instance_from_db.namespace != namespace:
                return sf_api.error(404, 'instance not in namespace')

        # If this instance is not on a node, just do the DB cleanup locally
        placement = instance_from_db.placement
        if not placement.get('node'):
            node = config.NODE_NAME
        else:
            node = placement['node']

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete request from REST API')
        instance_from_db.enqueue_delete_remote(node)

        # Return UUID in case API call was made using object name
        return instance_from_db.external_view()


def _artifact_safety_checks(a, instance_uuid=None):
    log = LOG
    if a:
        log = log.with_fields({'artifact': a})
    if instance_uuid:
        log = log.with_fields({'instance': instance_uuid})

    if not a:
        log.info('Artifact not found')
        return sf_api.error(404, 'artifact not found')
    if a.state.value != Artifact.STATE_CREATED:
        log.info('Artifact not in ready state')
        return sf_api.error(
            404, 'artifact not ready (state=%s)' % a.state.value)

    if namespace_is_trusted(a.namespace, get_jwt_identity()[0]):
        return
    if a.shared:
        return

    log.info('Artifact not owned or trusted by requestor and not shared')
    return sf_api.error(404, 'artifact not found')


def _netdesc_safety_checks(netdesc, namespace):
    if not isinstance(netdesc, dict):
        return sf_api.error(
            400, 'network specification should contain JSON objects')

    if 'network_uuid' not in netdesc:
        return sf_api.error(
            400, 'network specification is missing network_uuid')

    # Allow network to be specified by name or UUID (and error early
    # if not found)
    try:
        n = sfnet.Network.from_db_by_ref(netdesc['network_uuid'],
                                         get_jwt_identity()[0])
    except exceptions.MultipleObjects as e:
        return sf_api.error(400, str(e), suppress_traceback=True)

    if not n:
        return sf_api.error(
            404, 'network %s not found' % netdesc['network_uuid'])
    netdesc['network_uuid'] = n.uuid

    if netdesc.get('address') and not util_general.noneish(netdesc.get('address')):
        # The requested address must be within the ip range specified
        # for that virtual network, unless it is equivalent to "none".
        if not n.ipam.is_in_range(netdesc['address']):
            return sf_api.error(
                400,
                'network specification requests an address outside the '
                'range of the network')

    if n.state.value != sfnet.Network.STATE_CREATED:
        return sf_api.error(
            406, f'network {n.uuid} is not ready ({n.state.value})')
    if n.namespace != namespace:
        return sf_api.error(404, 'network %s does not exist' % n.uuid)

    return


def _netdesc_allocate_address(inst, netdesc, order):
    n = sfnet.Network.from_db(netdesc['network_uuid'])
    if not n:
        inst.enqueue_delete_due_error(
            'missing network %s during IP allocation phase'
            % netdesc['network_uuid'])
        return None, None, sf_api.error(
            404, 'network %s not found' % netdesc['network_uuid'])

    # NOTE(mikal): we now support interfaces with no address on them
    # (thanks OpenStack Kolla), which are special cased here. To not
    # have an address, you use a detailed netdesc and specify
    # address=none.
    try:
        if 'address' in netdesc and util_general.noneish(netdesc['address']):
            netdesc['address'] = None
        else:
            if 'address' not in netdesc or not netdesc['address']:
                netdesc['address'] = n.ipam.reserve_random_free_address(
                    inst.unique_label(), ipam.RESERVATION_TYPE_INSTANCE, '')
                inst.add_event(
                    EVENT_TYPE_AUDIT, 'allocated ip address', extra=netdesc)
            else:
                if not n.ipam.reserve(netdesc['address'], inst.unique_label(),
                                      ipam.RESERVATION_TYPE_INSTANCE, ''):
                    inst.enqueue_delete_due_error(
                        'failed to reserve an IP on network %s'
                        % netdesc['network_uuid'])
                    return None, None, sf_api.error(
                        409, 'address %s in use' % netdesc['address'])
    except exceptions.CongestedNetwork as e:
        inst.enqueue_delete_due_error(
            'cannot allocate address: %s' % e)
        return None, None, sf_api.error(507, str(e), suppress_traceback=True)

    if 'model' not in netdesc or not netdesc['model']:
        netdesc['model'] = 'virtio'

    iface_uuid = str(uuid.uuid4())
    LOG.with_fields({
        'networkinterface': iface_uuid,
        'instance': inst,
        'network': n
    }).with_fields(netdesc).info('Interface allocated')
    ni = NetworkInterface.new(iface_uuid, netdesc, inst.uuid, order)

    float_task = None
    try:
        if 'float' in netdesc and netdesc['float']:
            err = api_util.assign_floating_ip(ni)
            if err:
                inst.enqueue_delete_due_error(
                    'interface float failed: %s' % err)
                return None, None, err

            float_task = FloatNetworkInterfaceTask(
                netdesc['network_uuid'], iface_uuid)
    except exceptions.CongestedNetwork as e:
        inst.enqueue_delete_due_error(
            'cannot allocate address: %s' % e)
        return None, None, sf_api.error(507, str(e), suppress_traceback=True)

    # Include the interface uuid in the network description we
    # pass through to the instance start task.
    netdesc['iface_uuid'] = iface_uuid

    return float_task, netdesc, None


instances_get_example = """[
    {
        ...
        "name": "sfcbr-33WgX7tS4nqGtBTO",
        "namespace": "sfcbr-33WgX7tS4nqGtBTO",
        "node": "sf-1",
        ...
        "uuid": "3de4e98a-c234-48eb-8105-cc501ff6f22c",
        ...
    },
    {
        ...
        "name": "foo",
        "namespace": "system",
        "node": "sf-2",
        ...
        "uuid": "5c346d09-1562-4cbf-9800-c1c43192d93c",
        ...
    }
]"""


class InstancesEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Get all instances visible to the currently '
                     'authenticated namespace.',
        [('all', 'body', 'boolean',
          'If unset or False, only active instances are shown.', False)],
        [(200, 'Information about a single instance.', instances_get_example),
         (404, 'Instance not found.', None)]))
    @api_base.verify_token
    @api_base.log_token_use
    def get(self, all=False):
        prefilter = None
        filters = [partial(baseobject.namespace_filter, get_jwt_identity()[0])]
        if not all:
            prefilter = 'active'

        retval = []
        for i in instance.Instances(filters, prefilter=prefilter):
            retval.append(i.external_view())
        return retval

    @swag_from(api_base.swagger_helper(
        'instances', 'Create an instance.',
        [
            ('name', 'body', 'string',
             'The name of the instance, must meet the requirements of DNS RFCs.', True),
            ('cpus', 'body', 'integer', 'The number of vCPUs', True),
            ('memory', 'body', 'integer', 'The amount of RAM in MB.', True),
            ('network', 'body', 'arrayofdict',
             'A list of networkspecs defining the networking for this instance. '
             'See https://shakenfist.com/developer_guide/api_reference/instances/#networkspec '
             'for more details on networkspecs.', False),
            ('disk', 'body', 'arrayofdict',
             'A list of diskspecs defining the disk devices for this instance. '
             'See https://shakenfist.com/developer_guide/api_reference/instances/#diskspec '
             'for more details on diskspecs.', True),
            ('sshkey', 'body', 'string',
             'A ssh public key to add to the default users authorized_keys file '
             'via cloud-init. Requires that both configdrive be enabled, and that '
             'cloud-init be installed on the instance before boot.', False),
            ('userdata', 'body', 'string',
             'Other user-data to be provided to cloud-init. Requires that both '
             'configdrive be enabled, and that cloud-init be installed on the '
             'instance before boot.', False),
            ('placed_on', 'body', 'node',
             'The name of a Node to place this instance on.', False),
            ('namespace', 'body', 'namespace',
             'The namespace this instance should be created in, if other than '
             'the currently authenticated namespace.', False),
            ('video', 'body', 'dict',
             'A single videospec describing the video configuration of this instance. '
             'See https://shakenfist.com/developer_guide/api_reference/instances/#videospec '
             'for more details on videospecs.', False),
            ('uefi', 'body', 'boolean',
             'True if you want to boot an instance with UEFI instead of BIOS boot.',
             False),
            ('configdrive', 'body', 'string',
             'A config drive type. Currently "none" and "openstack-disk" are '
             'supported.', False),
            ('metadata', 'body', 'arrayofdict',
             'Any metadata to be set for the instance at creation time. See '
             'https://shakenfist.com/developer_guide/api_reference/instances/ for '
             'a discussion of instance metadata.', False),
            ('nvram_template', 'body', 'url',
             'A pointer to a template for the NVRAM image to be used for UEFI boot '
             'configuration. This can either be of the form "label:...label...", '
             'or "sf://blob/...blob.uuid...". URLs from the Internet are not '
             'currently supported unless fetched separately with an artifact cache '
             'operation.', False),
            ('secure_boot', 'body', 'boolean',
             'True if you would like to boot this instance with secure boot. '
             'Note that secure boot requires that UEFI also be True.', False),
            ('side_channels', 'body', 'arrayofstring',
             'Either None, or an array of strings listing side channels to '
             'connect to the instance. The only currently supported side channel '
             'is sf-agent, which is required for the Shaken Fist in-guest agent '
             'to function.', False)
          ],
        [
            (200, 'Information about a single instance.', instance_get_example),
            (400, 'Instance configuration error such as invalid name of boot '
                'configuration.', None),
            (404, 'Namespace, network, node, blob, snapshot, or label not found.', None),
            (406, 'Network not ready.', None),
            (409, 'Network address in use.', None),
            (507, 'Unable to allocate resources for the instance.', None)
         ]))
    @api_base.verify_token
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def post(self, name=None, cpus=None, memory=None, network=None, disk=None,
             ssh_key=None, user_data=None, placed_on=None, namespace=None,
             video=None, uefi=False, configdrive=None, metadata=None,
             nvram_template=None, secure_boot=False, side_channels=None):
        # NOTE(mikal): if we cleaned this up to have less business logic in it,
        # then that would also mean that we could reduce the amount of duplicated
        # logic in mock_etcd.create_instance().
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
        if not namespace_is_trusted(namespace, get_jwt_identity()[0]):
            return sf_api.error(404, 'namespace not found')

        # Check that the instance name is safe for use as a DNS host name
        if name != re.sub(r'([^a-zA-Z0-9\-])', '', name) or len(name) > 63:
            return sf_api.error(
                400, ('instance name %s is not useable as a DNS and Linux host name. '
                      'That is, less than 63 characters and in the character set: '
                      'a-z, A-Z, 0-9, or hyphen (-).' % name))

        # Secure boot requires UEFI
        if secure_boot and not uefi:
            return sf_api.error(400, 'secure boot requires UEFI be enabled')

        if secure_boot:
            machine_type = 'q35'

        # If we are placed, make sure that node exists
        if placed_on:
            n = Node.from_db(placed_on, suppress_failure_audit=True)
            if not n:
                return sf_api.error(404, 'Specified node does not exist')
            if n.state.value != Node.STATE_CREATED:
                return sf_api.error(404, 'Specified node not ready')

        # Make sure we've been given a valid configdrive option
        if not configdrive:
            configdrive = 'openstack-disk'
        elif configdrive not in ['openstack-disk', 'none']:
            return sf_api.error(400, 'invalid config drive type: "%s"' % configdrive)

        # Sanity check and lookup blobs for disks where relevant
        if not disk:
            return sf_api.error(400, 'instance must specify at least one disk')

        transformed_disk = []
        for d in disk:
            if not isinstance(d, dict):
                return sf_api.error(400, 'disk specification should contain JSON objects')

            # Ensure we're using a known disk bus
            disk_bus = instance._get_defaulted_disk_bus(d)
            try:
                instance._get_disk_device(disk_bus, 0)
            except exceptions.InstanceBadDiskSpecification:
                return sf_api.error(400, 'invalid disk bus %s' % disk_bus,
                                    suppress_traceback=True)

            # Convert internal shorthand forms into specific blobs
            disk_base = d.get('base')
            if util_general.noneish(disk_base):
                d['disk_base'] = None

            elif disk_base.startswith('label:'):
                label = disk_base[len('label:'):]
                a = Artifact.from_url(
                    Artifact.TYPE_LABEL,
                    f'{LABEL_URL}{get_jwt_identity()[0]}/{label}',
                    name=label, namespace=namespace)
                err = _artifact_safety_checks(a, instance_uuid=instance_uuid)
                if err:
                    return err

                blob_uuid = a.resolve_to_blob()
                if not blob_uuid:
                    return sf_api.error(404, 'Could not resolve label %s to a blob' % label)
                d['blob_uuid'] = blob_uuid

            elif disk_base.startswith(SNAPSHOT_URL):
                a = Artifact.from_db(disk_base[len(SNAPSHOT_URL):])
                err = _artifact_safety_checks(a, instance_uuid=instance_uuid)
                if err:
                    return err

                blob_uuid = a.resolve_to_blob()
                if not blob_uuid:
                    return sf_api.error(404, 'Could not resolve snapshot to a blob')
                d['blob_uuid'] = blob_uuid

            elif disk_base.startswith(UPLOAD_URL) or disk_base.startswith(LABEL_URL):
                if disk_base.startswith(UPLOAD_URL):
                    a = Artifact.from_url(Artifact.TYPE_IMAGE, disk_base,
                                          namespace=namespace)
                else:
                    a = Artifact.from_url(Artifact.TYPE_LABEL, disk_base,
                                          namespace=namespace)
                err = _artifact_safety_checks(a, instance_uuid=instance_uuid)
                if err:
                    return err

                blob_uuid = a.resolve_to_blob()
                if not blob_uuid:
                    return sf_api.error(404, 'Could not resolve artifact to a blob')
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
                url = f'{LABEL_URL}{get_jwt_identity()[0]}/{label}'
                a = Artifact.from_url(Artifact.TYPE_LABEL, url, name=label,
                                      namespace=namespace)
                err = _artifact_safety_checks(a, instance_uuid=instance_uuid)
                if err:
                    return err

                blob_uuid = a.resolve_to_blob()
                if not blob_uuid:
                    return sf_api.error(404, 'Could not resolve label %s to a blob' % label)
                LOG.with_fields({'instance': instance_uuid}).with_fields({
                    'original_template': original_template,
                    'label': label,
                    'source_url': url,
                    'artifact': a.uuid,
                    'blob': blob_uuid
                }).info('NVRAM template label resolved')
                nvram_template = blob_uuid

            elif nvram_template.startswith(BLOB_URL):
                nvram_template = nvram_template[len(BLOB_URL):]
                LOG.with_fields({'instance': instance_uuid}).with_fields({
                    'original_template': original_template,
                    'blob': nvram_template
                }).info('NVRAM template URL converted')
                nvram_template = blob_uuid

        # We no longer support IDE.
        for d in disk:
            if d.get('bus') == 'ide':
                return sf_api.error(400, 'IDE disks are no longer supported')

        if network:
            for netdesc in network:
                err = _netdesc_safety_checks(netdesc, namespace)
                if err:
                    return err

        if not video:
            video = {'model': 'cirrus', 'memory': 16384, 'vdi': 'spice'}
        else:
            if 'model' not in video:
                return sf_api.error(400, 'video specification requires "model"')
            if 'memory' not in video:
                return sf_api.error(400, 'video specification requires "memory"')
            if 'vdi' not in video:
                video['vdi'] = 'spice'

        # Validate metadata before instance creation
        if metadata:
            if not isinstance(metadata, dict):
                return sf_api.error(400, 'metadata must be a dictionary')
            for k, v in metadata.items():
                err = _validate_instance_metadata(k, v)
                if err:
                    return err

        # If no preference for side channels is expressed, then use the default
        if side_channels is None:
            side_channels = ['sf-agent']

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
        inst.add_event(EVENT_TYPE_AUDIT, 'create request from REST API')

        # Initialise metadata
        if metadata:
            inst._db_set_attribute('metadata', metadata)

        # Allocate IP addresses
        order = 0
        float_tasks = []
        updated_networks = []
        if network:
            for netdesc in network:
                float_task, netdesc, err = _netdesc_allocate_address(
                    inst, netdesc, order)
                if err:
                    return err
                if float_task:
                    float_tasks.append(float_task)
                updated_networks.append(netdesc)
                order += 1

        # Store interfaces soon as they are allocated to the instance
        inst.interfaces = [i['iface_uuid'] for i in updated_networks]

        if not SCHEDULER:
            SCHEDULER = scheduler.Scheduler()

        try:
            # Have we been placed?
            if not placed_on:
                candidates = SCHEDULER.find_candidates(inst, updated_networks)
                placement = candidates[0]

            else:
                SCHEDULER.find_candidates(inst, network, candidates=[placed_on])
                placement = placed_on

        except exceptions.LowResourceException as e:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'schedule failed, insufficient resources',
                extra={'message': str(e)})
            inst.enqueue_delete_due_error('scheduling failed')
            return sf_api.error(507, str(e), suppress_traceback=True)

        except exceptions.CandidateNodeNotFoundException as e:
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule failed, node not found',
                           extra={'message': str(e)})
            inst.enqueue_delete_due_error('scheduling failed')
            return sf_api.error(404, 'node not found: %s' % e, suppress_traceback=True)

        # Record placement
        inst.place_instance(placement)

        # Create a queue entry for the instance start
        tasks = [PreflightInstanceTask(inst.uuid, network)]
        for disk in inst.disk_spec:
            disk_base = disk.get('base')
            if disk.get('blob_uuid'):
                tasks.append(FetchImageTask(
                    '{}{}'.format(BLOB_URL, disk['blob_uuid']),
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

    @swag_from(api_base.swagger_helper(
        'instances', 'Delete all instances in a namespace.',
        [('confirm', 'body', 'boolean', 'I really mean it.', True),
         ('namespace', 'body', 'namespace',
          'The namespace to delete instances from', False)],
        [(200, 'A list of the UUIDs of instances awaiting deletion.', None),
         (400, 'The confirm parameter is not True or a administrative user has '
               'not specified a namespace.', None)]))
    @api_base.verify_token
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def delete(self, confirm=False, namespace=None):
        """Delete all instances in the namespace."""

        if confirm is not True:
            return sf_api.error(400, 'parameter confirm is not set true')

        if get_jwt_identity()[0] == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all instances in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return sf_api.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity()[0]:
                return sf_api.error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()[0]

        waiting_for = []
        tasks_by_node = defaultdict(list)
        for inst in instance.Instances([partial(baseobject.namespace_filter, namespace)]):
            inst.add_event(
                EVENT_TYPE_AUDIT, 'delete request via delete all from REST API')

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


instance_interfaces_example = """[
    {
        "floating": "192.168.10.73",
        "instance_uuid": "c0d52a77-0f8a-4f19-bec7-0c05efb03cb4",
        "ipv4": "10.0.0.47",
        "macaddr": "02:00:00:6d:e5:e0",
        "metadata": {},
        "model": "virtio",
        "network_uuid": "1bed1aa5-10f0-45cc-ae58-4a94761bef59",
        "order": 0,
        "state": "created",
        "uuid": "8e7b2f39-c652-4ec2-88ff-2791b503fc65",
        "version": 3
    }
]"""


instance_interface_create_example = """{
    "floating": "192.168.10.73",
    "instance_uuid": "c0d52a77-0f8a-4f19-bec7-0c05efb03cb4",
    "ipv4": "10.0.0.47",
    "macaddr": "02:00:00:6d:e5:e0",
    "metadata": {},
    "model": "virtio",
    "network_uuid": "1bed1aa5-10f0-45cc-ae58-4a94761bef59",
    "order": 1,
    "state": "created",
    "uuid": "8e7b2f39-c652-4ec2-88ff-2791b503fc65",
    "version": 3
}"""


class InstanceInterfacesEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'List network interfaces for an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(200, 'A list of network interfaces for an instance.',
          instance_interfaces_example),
         (404, 'Instance not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        out = []
        for iface_uuid in instance_from_db.interfaces:
            ni, _, err = api_util.safe_get_network_interface(iface_uuid)
            if err:
                return err
            out.append(ni.external_view())
        return out

    @swag_from(api_base.swagger_helper(
        'instances', 'Create a new network interface on an instance',
        [
            ('instance_ref', 'query', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('network', 'body', 'dict',
             'A networkspec defining the new interface. '
             'See https://shakenfist.com/developer_guide/api_reference/instances/#networkspec '
             'for more details on networkspecs.', True)
        ],
        [
            (200, 'The new interface details.', instance_interface_create_example),
            (400, 'Network description invalid.', None),
            (404, 'Instance or network not found.', None),
            (406, 'Instance or network not ready.', None),
            (409, 'Address in use.', None)
        ]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def post(self, instance_ref=None, network=None, instance_from_db=None):
        s = instance_from_db.state.value
        if s == dbo.STATE_DELETED or s.endswith('-error'):
            return sf_api.error(406, 'instance in invalid state for hot plug')

        err = _netdesc_safety_checks(network, instance_from_db.namespace)
        if err:
            return err

        ifaces = instance_from_db.interfaces
        if not ifaces or len(ifaces) == 0:
            order = 0
        else:
            last_iface_uuid = ifaces[-1]
            last_iface = NetworkInterface.from_db(last_iface_uuid)
            if not last_iface:
                return sf_api.error(406, 'instance interfae list invalid')
            order = last_iface.order + 1

        float_task, netdesc, err = _netdesc_allocate_address(
            instance_from_db, network, order)
        if err:
            return err

        tasks = [HotPlugInstanceInterfaceTask(
            instance_from_db.uuid, netdesc['network_uuid'], netdesc['iface_uuid'])]

        if float_task:
            tasks.append(float_task)
        instance_from_db.interfaces_append(netdesc['iface_uuid'])

        etcd.enqueue(instance_from_db.placement['node'], {'tasks': tasks})
        return NetworkInterface.from_db(netdesc['iface_uuid']).external_view()


instance_events_example = """[
    ...
    {
        "duration": null,
        "extra": {
            "cpu usage": {
                "cpu time ns": 357485828000,
                "system time ns": 66297716000,
                "user time ns": 291188112000
            },
            "disk usage": {
                "vda": {
                    "actual bytes on disk": 956301312,
                    "errors": -1,
                    "read bytes": 406776320,
                    "read requests": 12225,
                    "write bytes": 2105954304,
                    "write requests": 3657
                },
                "vdb": {
                    "actual bytes on disk": 102400,
                    "errors": -1,
                    "read bytes": 279552,
                    "read requests": 74,
                    "write bytes": 0,
                    "write requests": 0
                }
            },
            "network usage": {
                "02:00:00:1d:24:ae": {
                    "read bytes": 147084732,
                    "read drops": 0,
                    "read errors": 0,
                    "read packets": 16484,
                    "write bytes": 2166754,
                    "write drops": 0,
                    "write errors": 0,
                    "write packets": 13144
                }
            }
        },
        "fqdn": "sf-2",
        "message": "usage",
        "timestamp": 1685229509.9592097,
        "type": "usage"
    },
    ...
]"""


class InstanceEventsEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Get instance event information.',
        [
            ('instance_ref', 'query', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100.', False)
        ],
        [(200, 'Event information about a single instance.', instance_events_example),
         (404, 'Instance not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_to_eventlog_node
    @api_base.log_token_use
    def get(self, instance_ref=None, event_type=None, limit=100, instance_from_db=None):
        with eventlog.EventLog('instance', instance_from_db.uuid) as eventdb:
            return list(eventdb.read_events(limit=limit, event_type=event_type))


class InstanceRebootSoftEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Soft (ACPI) reboot an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be rebooted.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            instance_from_db.add_event(
                EVENT_TYPE_AUDIT, 'soft reboot request from REST API')
            with instance_from_db.get_lock(op='Instance reboot soft',
                                           global_scope=False):
                return instance_from_db.reboot(hard=False)
        except exceptions.InvalidLifecycleState as e:
            return sf_api.error(409, e)


class InstanceRebootHardEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Hard (reset switch) reboot an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be rebooted.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            instance_from_db.add_event(
                EVENT_TYPE_AUDIT, 'hard reboot request from REST API')
            with instance_from_db.get_lock(op='Instance reboot hard',
                                           global_scope=False):
                return instance_from_db.reboot(hard=True)
        except exceptions.InvalidLifecycleState as e:
            return sf_api.error(409, e)


class InstancePowerOffEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Power off an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be powered off.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            instance_from_db.add_event(
                EVENT_TYPE_AUDIT, 'power off request from REST API')
            with instance_from_db.get_lock(op='Instance power off',
                                           global_scope=False):
                return instance_from_db.power_off()
        except exceptions.InvalidLifecycleState as e:
            return sf_api.error(409, e)


class InstancePowerOnEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Power on an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be powered on.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            instance_from_db.add_event(
                EVENT_TYPE_AUDIT, 'power on request from REST API')
            with instance_from_db.get_lock(op='Instance power on',
                                           global_scope=False):
                return instance_from_db.power_on()
        except exceptions.InvalidLifecycleState as e:
            return sf_api.error(409, e)


class InstancePauseEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Pause an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be paused.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            instance_from_db.add_event(
                EVENT_TYPE_AUDIT, 'pause request from REST API')
            with instance_from_db.get_lock(op='Instance pause',
                                           global_scope=False):
                return instance_from_db.pause()
        except exceptions.InvalidLifecycleState as e:
            return sf_api.error(409, e)


class InstanceUnpauseEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Unpause an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be unpaused.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, instance_from_db=None):
        try:
            instance_from_db.add_event(
                EVENT_TYPE_AUDIT, 'unpause request from REST API')
            with instance_from_db.get_lock(op='Instance unpause',
                                           global_scope=False):
                return instance_from_db.unpause()
        except exceptions.InvalidLifecycleState as e:
            return sf_api.error(409, e)


class InstanceMetadatasEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Fetch metadata for an instance.',
        [('instance_ref', 'query', 'uuidorname',
          'The instance to fetch metadata for.', True)],
        [(200, 'Instance metadata, if any.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        return instance_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'instances', 'Add metadata for an instance.',
        [
            ('instance_ref', 'query', 'uuidorname', 'The instance to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def post(self, instance_ref=None, key=None, value=None, instance_from_db=None):
        err = _validate_instance_metadata(key, value)
        if err:
            return err
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'post'})
        instance_from_db.add_metadata_key(key, value)


def _validate_instance_metadata(key, value):
    if not key:
        return sf_api.error(400, 'no key specified')
    if not value:
        return sf_api.error(400, 'no value specified')

    # Reserved key "tags" should be validated to avoid unexpected failures
    if key == instance.Instance.METADATA_KEY_TAGS:
        if not isinstance(value, list):
            return sf_api.error(400, 'value for "tags" key should be a JSON list')

    # Reserved key "affinity" should be validated to avoid unexpected
    # failures during instance creation.
    elif key == instance.Instance.METADATA_KEY_AFFINITY:
        if not isinstance(value, dict):
            return sf_api.error(
                400, 'value for "affinity" key should be a valid JSON dictionary')

        for key_type, dv in value.items():
            try:
                int(dv)
            except ValueError:
                return sf_api.error(400, 'affinity dictionary values should be integers')


class InstanceMetadataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Update a metadata key for an instance.',
        [
            ('instance_ref', 'query', 'uuidorname', 'The instance to add a key to.', True),
            ('key', 'query', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def put(self, instance_ref=None, key=None, value=None, instance_from_db=None):
        err = _validate_instance_metadata(key, value)
        if err:
            return err
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'put'})
        instance_from_db.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'instances', 'Delete a metadata key for an instance.',
        [
            ('instance_ref', 'query', 'uuidorname', 'The instance to remove a key from.', True),
            ('key', 'query', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def delete(self, instance_ref=None, key=None, instance_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete metadata key request from REST API',
            extra={'key': key})
        instance_from_db.remove_metadata_key(key)


class InstanceConsoleDataEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Fetch console data from an instance.',
        [
            ('instance_ref', 'query', 'uuidorname',
             'The instance fetch console data for.', True),
            ('length', 'body', 'integer',
             'The amount of data to fetch, defaults to 10240 bytes.', False)
        ],
        [(200, 'The console data as an application/octet-stream.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.log_token_use
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
            # the sf_api.error call, otherwise it would be logged.
            if parsed_length is None:
                return sf_api.error(400, 'length is not an integer')

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'get console data request from REST API')
        resp = flask.Response(
            instance_from_db.get_console_data(parsed_length),
            mimetype='applicaton/octet-stream')
        resp.status_code = 200
        return resp

    @swag_from(api_base.swagger_helper(
        'instances', 'Delete console data for an instance.',
        [
            ('instance_ref', 'query', 'uuidorname',
             'The instance fetch console data for.', True)
        ],
        [(200, 'Nothing.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.log_token_use
    def delete(self, instance_ref=None, instance_from_db=None):
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete console data request from REST API')
        instance_from_db.delete_console_data()


# The best documentation I can find for the format of this file and the various
# fields is this source code:
# https://gitlab.com/virt-viewer/virt-viewer/-/blob/master/src/virt-viewer-file.c
VIRTVIEWER_TEMPLATE = """[virt-viewer]
type=%(vdi_type)s
host=%(node)s
port=%(vdi_port)s%(vdi_tls_port)s
delete-this-file=1%(ca_cert)s
"""


instance_vv_file_example = """[virt-viewer]
type=spice
host=sf-3
port=42281
tls-port=43197
delete-this-file=1
ca=-----BEGIN CERTIFICATE-----\nMIIEF...16br/Fw==\n-----END CERTIFICATE-----\n"""


class InstanceVDIConsoleHelperEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances',
        ('Fetch a virt-viewer .vv file describing how to connect to the VDI console '
         'for this instance.'),
        [
            ('instance_ref', 'query', 'uuidorname',
             'The instance fetch console data for.', True)
        ],
        [(200, 'A .vv file to open in virt-viewer as a application/x-virt-viewer stream.',
          instance_vv_file_example),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        p = instance_from_db.ports

        cacert = ''
        if os.path.exists('/etc/pki/libvirt-spice/ca-cert.pem'):
            with open('/etc/pki/libvirt-spice/ca-cert.pem') as f:
                cacert = f.read()
            cacert = '\nca=%s' % cacert.replace('\n', '\\n')

        tls_port = ''
        if p.get('vdi_tls_port'):
            tls_port = '\ntls-port=%s' % p['vdi_tls_port']

        config = VIRTVIEWER_TEMPLATE % {
            'vdi_type': instance_from_db.video['vdi'],
            'node': instance_from_db.placement.get('node'),
            'vdi_port': p.get('vdi_port'),
            'vdi_tls_port': tls_port,
            'ca_cert': cacert
        }

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'vdiconsole request from REST API')
        resp = flask.Response(
            config, mimetype='application/x-virt-viewer')
        resp.status_code = 200
        return resp


class InstanceAgentPutEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Upload a file to an instance via the Shaken Fist agent.',
        [
            ('instance_ref', 'query', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('blob_uuid', 'body', 'uuid',
             'The UUID of the blob to put onto the instance.', True),
            ('path', 'body', 'string',
             'The path to write the file at inside the instance.', True),
            ('mode', 'body', 'string',
             'The mode of the file once written, in symbolic or numeric form.', True)
        ],
        [(200, 'An agent operation.', api_agentoperation.agentoperation_get_example),
         (400, 'No agent connection to instance.', None),
         (404, 'Instance or blob not found.', None),
         (406, 'Invalid mode specified', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, blob_uuid=None, path=None, mode=None,
             instance_from_db=None):
        if not instance_from_db.agent_state.value.startswith('ready'):
            return sf_api.error(400, 'instance agent not ready')

        try:
            int(mode)
        except ValueError:
            try:
                symbolicmode.symbolic_to_numeric_permissions(mode)
            except ValueError as e:
                return sf_api.error(406, 'invalid mode: %s' % e)

        b = Blob.from_db(blob_uuid)
        if not b:
            return self.api_error(404, 'blob not found')

        commands = [
            {
                'command': 'put-blob',
                'blob_uuid': blob_uuid,
                'path': path
            },
            {
                'command': 'chmod',
                'path': path,
                'mode': mode
            }
        ]

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'agent operation put-blob request from REST API')
        o = AgentOperation.new(str(uuid.uuid4()), instance_from_db.namespace,
                               instance_from_db.uuid, commands)
        instance_from_db.agent_operation_enqueue(o.uuid)
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'queued agent command requiring preflight',
            extra={'agentoperation': o.uuid, 'commands': commands})
        o.state = AgentOperation.STATE_PREFLIGHT
        etcd.enqueue(instance_from_db.placement['node'],
                     {'tasks': [PreflightAgentOperationTask(o.uuid)]})
        return o.external_view()


class InstanceAgentGetEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Download a file from an instance via the Shaken Fist agent.',
        [
            ('instance_ref', 'query', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('path', 'body', 'string',
             'The path to fetch the file from inside the instance.', True)
        ],
        [(200, 'An agent operation.', api_agentoperation.agentoperation_get_example),
         (400, 'No agent connection to instance.', None),
         (404, 'Instance not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, path=None, instance_from_db=None):
        if not instance_from_db.agent_state.value.startswith('ready'):
            return sf_api.error(400, 'instance agent not ready')

        commands = [
            {
                'command': 'get-file',
                'path': path
            }
        ]

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'agent operation get-file request from REST API')
        o = AgentOperation.new(str(uuid.uuid4()), instance_from_db.namespace,
                               instance_from_db.uuid, commands)
        instance_from_db.agent_operation_enqueue(o.uuid)
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'queued agent command not requiring preflight',
            extra={'agentoperation': o.uuid, 'commands': commands})
        o.state = AgentOperation.STATE_QUEUED
        return o.external_view()


class InstanceAgentExecuteEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Execute a command within an instance via the Shaken Fist agent.',
        [
            ('instance_ref', 'query', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('command_line', 'body', 'string', 'The command to execute.', True)
        ],
        [(200, 'An agent operation.', api_agentoperation.agentoperation_get_example),
         (400, 'No agent connection to instance.', None),
         (404, 'Instance not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, command_line=None, instance_from_db=None):
        if not instance_from_db.agent_state.value.startswith('ready'):
            return sf_api.error(400, 'instance agent not ready')

        commands = [
            {
                'command': 'execute',
                'commandline': command_line,
                'block-for-result': True
            }
        ]

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'agent operation execute request from REST API')
        o = AgentOperation.new(str(uuid.uuid4()), instance_from_db.namespace,
                               instance_from_db.uuid, commands)
        instance_from_db.agent_operation_enqueue(o.uuid)
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'queued agent command not requiring preflight',
            extra={'agentoperation': o.uuid, 'commands': commands})
        o.state = AgentOperation.STATE_QUEUED
        return o.external_view()


class InstanceScreenshotEndpoint(sf_api.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Collect a screenshot of an instance.',
        [
            ('instance_ref', 'query', 'uuidorname',
             'The UUID or name of the instance.', True)
        ],
        [(200, 'The UUID of a blob containing the screenshot.', None),
         (404, 'Instance not found.', None)]))
    @api_base.verify_token
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'screenshot request from REST API')
        return instance_from_db.get_screenshot()
