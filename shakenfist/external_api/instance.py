# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: yes
#   - API reference docs exist:
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:
import os
import uuid
from functools import partial

import flask
import symbolicmode
import validators
from flasgger import swag_from
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder
from webargs import fields
from webargs.flaskparser import use_kwargs

from shakenfist import baseobject
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.net_op \
    import create_and_enqueue as net_create_and_enqueue
from shakenfist.schema.operations.net_op \
    import model_tasks as net_tasks
from shakenfist.schema.operations.node_aop_op \
    import create_and_enqueue as na_create_and_enqueue
from shakenfist.schema.operations.node_aop_op \
    import model_tasks as na_tasks
from shakenfist.schema.operations.node_inst_net_iface_op \
    import create_and_enqueue as niio_create_and_enqueue
from shakenfist.schema.operations.node_inst_net_iface_op \
    import model_tasks as niio_tasks
from shakenfist.schema.operations.node_inst_netdesc_op \
    import create_and_enqueue as nino_create_and_enqueue
from shakenfist.schema.operations.node_inst_netdesc_op \
    import model_tasks as nino_tasks
from shakenfist import exceptions
from shakenfist import instance
from shakenfist.network import network as sfnet
from shakenfist.schema.ipam_reservation import ReservationType
from shakenfist import scheduler
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.artifact import Artifact
from shakenfist.artifact import ARTIFACT_URL
from shakenfist.artifact import BLOB_URL
from shakenfist.artifact import LABEL_URL
from shakenfist.artifact import SNAPSHOT_URL
from shakenfist.artifact import UPLOAD_URL
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import agentoperation as api_agentoperation
from shakenfist.external_api import base as api_base
from shakenfist.external_api import util as api_util
from shakenfist.namespace import namespace_is_trusted
from shakenfist.network.interface import NetworkInterface
from shakenfist.node import Node
from shakenfist.util.access_tokens import request_namespace
from shakenfist.util import general as util_general
from shakenfist.util import vdi_tokens


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


SCHEDULER = None


# Published verbatim in five parameter declarations across the three
# endpoints which create agent operations. They name an operator
# settable default, so a copy left behind would publish a number the
# server no longer uses.
DEADLINE_SECONDS_DESCRIPTION = (
    'How many seconds after this request is received the operation '
    'may continue to be dispatched or execute. Queue time and any '
    'preflight work count against it. 0 means no wall-clock '
    'deadline at all, although an operation whose progress timeout '
    'is also disabled -- which includes every agent/execute -- is '
    'still expired AGENT_OPERATION_MAX_DEADLINE seconds after it '
    'last changed state. The published maximum is that same '
    'operator ceiling, 86400 seconds unless changed. Omitting this '
    'applies the server default, AGENT_OPERATION_DEFAULT_DEADLINE, '
    'which is 600 seconds unless the operator has changed it.')

PROGRESS_TIMEOUT_SECONDS_DESCRIPTION = (
    'How many seconds without forward progress are fatal to this '
    'operation. 0 disables the progress timeout. The published '
    'maximum is the operator ceiling AGENT_OPERATION_MAX_DEADLINE, '
    '86400 seconds unless changed. Omitting this applies the server '
    'default, AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT, which is 30 '
    'seconds unless the operator has changed it.')


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
    "last_cluster_operation": {
        "op_type": "node_inst_snap_op",
        "op_uuid": "78d4c3dc-1c1d-4870-bac4-b397cfe79884"
    },
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
        "sf-agent2"
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
    },
    "references_to": {},
    "references_from": {
        "disk": [
            {
                "source_object_type": "instance",
                "source_uuid": "d51aa352-368c-484c-9e4c-4542927b4277",
                "relationship": "disk",
                "relationship_value": "0",
                "target_object_type": "blob",
                "target_uuid": "5117f778-b214-4184-8358-f2c7376b76db",
                "created": 1683995934.357137,
                "last_active": 1684054381.217045
            }
        ]
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
        "sf-agent2"
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
    },
    "references_to": {},
    "references_from": {
        "disk": [
            {
                "source_object_type": "instance",
                "source_uuid": "d51aa352-368c-484c-9e4c-4542927b4277",
                "relationship": "disk",
                "relationship_value": "0",
                "target_object_type": "blob",
                "target_uuid": "5117f778-b214-4184-8358-f2c7376b76db",
                "created": 1683995934.357137,
                "last_active": 1684054381.217045
            }
        ]
    }
}"""


class InstanceEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Get instance information.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True),
         ('namespace', 'body', 'namespace',
          'Scope the name lookup to this namespace.', False)],
        [(200, 'Information about a single instance.', instance_get_example),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None, namespace=None):
        return instance_from_db.external_view()

    @swag_from(api_base.swagger_helper(
        'instances', 'Delete an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True),
         ('namespace', 'body', 'namespace',
          'The namespace containing the instance', False)],
        [(200, 'Information about the instance post delete.',
          instance_get_example_deleted),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def delete(self, instance_ref=None, instance_from_db=None, namespace=None):
        # Check if instance has already been deleted
        if instance_from_db.state.value == dbo.STATE_DELETED:
            return sf_api.error(404, 'instance not found')

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete request from REST API')
        instance_from_db.enqueue_delete()

        # Return UUID in case API call was made using object name
        return instance_from_db.external_view()


def _artifact_unusable_reason(a):
    """Why this request may not use this artifact, or None if it may.

    Split out of _artifact_safety_checks so there is a way to ask the
    question without answering it. Most callers here are refusing the
    request when the answer is no, and want the error response and the
    log line that go with a refusal. The disk.base fall-through is not:
    a foreign artifact it cannot use is a reason to fetch its own copy,
    so building a 404 nobody will ever be sent -- and emitting
    'Returning API error' for a request about to succeed -- would put a
    refusal in the log of a request that created an instance.

    Returns a (log message, API message) pair rather than one string,
    because those deliberately differ: an artifact somebody may not see
    is logged as not visible and reported as not found, so the refusal
    is diagnosable by an operator without being an oracle to a caller.
    """
    if not a:
        return ('Artifact not found', 'artifact not found')
    if a.state.value != Artifact.STATE_CREATED:
        return ('Artifact not in ready state',
                'artifact not ready (state=%s)' % a.state.value)

    if namespace_is_trusted(a.namespace, request_namespace()):
        return None
    if a.shared:
        return None

    return ('Artifact not owned or trusted by requestor and not shared',
            'artifact not found')


def _artifact_safety_checks(a, instance_uuid=None):
    reason = _artifact_unusable_reason(a)
    if not reason:
        return

    log = LOG
    if a:
        log = log.with_fields({'artifact': a})
    if instance_uuid:
        log = log.with_fields({'instance': instance_uuid})

    log.info(reason[0])
    return sf_api.error(404, reason[1])


def _netdesc_safety_checks(netdesc, namespace):
    if not isinstance(netdesc, dict):
        return sf_api.error(
            400, 'network specification should contain JSON objects')

    if 'network_uuid' not in netdesc:
        return sf_api.error(
            400, 'network specification is missing network_uuid')

    # Allow network to be specified by name or UUID (and error early
    # if not found). Scope to the *instance's* namespace, not the
    # caller's — a system admin creating an instance in tenant 'ns1'
    # must resolve 'ns1's network, not whatever same-named network
    # happens to be visible cluster-wide.
    try:
        n = sfnet.Network.from_db_by_ref(netdesc['network_uuid'], namespace)
    except exceptions.MultipleObjects as e:
        return sf_api.error(400, str(e), suppress_traceback=True)

    if not n:
        return sf_api.error(
            404, 'network %s not found' % netdesc['network_uuid'])
    # Stringified because the netdesc is later logged as event 'extra',
    # which must be JSON serialisable (issue 3573).
    netdesc['network_uuid'] = str(n.uuid)

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
            'missing network  during IP allocation phase')
        return (
            None,
            sf_api.error(404, f'network {netdesc["network_uuid"]} not found')
        )

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
                    inst.unique_label(), ReservationType.INSTANCE, '')
                inst.add_event(
                    EVENT_TYPE_AUDIT, 'allocated ip address', extra=netdesc)
            else:
                # An explicit address may take over a deletion-halo
                # reservation: the halo protects against surprising random
                # reallocation, which is not what a caller asking for this
                # exact address is exposed to. Without this, deleting and
                # immediately recreating an instance at a static address
                # 409s for the whole halo period (issue 4059).
                if not n.ipam.reserve(netdesc['address'], inst.unique_label(),
                                      ReservationType.INSTANCE, '',
                                      evict_halo=True):
                    inst.enqueue_delete_due_error(
                        'failed to reserve an IP on network %s'
                        % netdesc['network_uuid'])
                    return None, sf_api.error(
                        409, 'address %s in use' % netdesc['address'])

    except exceptions.CongestedNetwork as e:
        inst.enqueue_delete_due_error('cannot allocate address: %s' % e)
        return None, sf_api.error(507, str(e), suppress_traceback=True)

    if 'model' not in netdesc or not netdesc['model']:
        netdesc['model'] = 'virtio'

    iface_uuid = str(uuid.uuid4())
    LOG.with_fields({
        'interface': iface_uuid,
        'instance': inst,
        'network': n
    }).with_fields(netdesc).info('Interface allocated')
    ni = NetworkInterface.new(iface_uuid, netdesc, inst.uuid, order)

    try:
        if 'float' in netdesc and netdesc['float']:
            err = api_util.assign_floating_ip(ni)
            if err:
                inst.enqueue_delete_due_error(
                    'interface float failed: %s' % err)
                return None, err

    except exceptions.CongestedNetwork as e:
        inst.enqueue_delete_due_error('cannot allocate address: %s' % e)
        return None, sf_api.error(507, str(e), suppress_traceback=True)

    # Include the interface uuid in the network description we
    # pass through to the instance start task.
    netdesc['iface_uuid'] = iface_uuid

    return netdesc, None


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


VALID_SIDE_CHANNELS = ['sf-agent', 'sf-agent2']


class InstancesEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Get all instances visible to the currently '
                     'authenticated namespace.',
        [('all', 'body', 'boolean',
          'If unset or False, only active instances are shown.', False)],
        [(200, 'Information about a single instance.', instances_get_example),
         (404, 'Instance not found.', None)]))
    @api_base.log_token_use
    def get(self, all=False):
        prefilter = None
        filters = [partial(baseobject.namespace_filter,
                           request_namespace())]
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
            ('cpus', 'body', 'unsignedinteger', 'The number of vCPUs', True),
            ('memory', 'body', 'unsignedinteger',
             'The amount of RAM in MB.', True),
            ('network', 'body', 'arrayofdict',
             'A list of networkspecs defining the networking for this instance. '
             'See https://shakenfist.com/developer_guide/api_reference/instances/#networkspec '
             'for more details on networkspecs.', False),
            ('disk', 'body', 'arrayofdict',
             'A list of diskspecs defining the disk devices for this instance. '
             'See https://shakenfist.com/developer_guide/api_reference/instances/#diskspec '
             'for more details on diskspecs.', True),
            ('ssh_key', 'body', 'string',
             'A ssh public key to add to the default users authorized_keys file '
             'via cloud-init. Requires that both configdrive be enabled, and that '
             'cloud-init be installed on the instance before boot.', False),
            ('user_data', 'body', 'base64',
             'Other user-data to be provided to cloud-init, base64 encoded. '
             'Requires that both configdrive be enabled, and that cloud-init '
             'be installed on the instance before boot.', False),
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
            ('metadata', 'body', 'dict',
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
             'to function. None will result in you receiving the default set of '
             'side channels (currently just sf-agent, whereas an empty list will'
             'result in no side channels at all).', False)
          ],
        [
            (200, 'Information about a single instance.', instance_get_example),
            (400, 'Instance configuration error such as invalid name of boot '
                'configuration.', None),
            (404, 'Namespace, network, node, blob, snapshot, or label not found.', None),
            (406, 'Network not ready.', None),
            (409, 'Network address in use, or no node satisfies a hard '
                'affinity constraint.', None),
            (507, 'Unable to allocate resources for the instance.', None)
         ]))
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def post(self, name=None, cpus=None, memory=None, network=None, disk=None,
             ssh_key=None, user_data=None, placed_on=None, namespace=None,
             video=None, uefi=False, configdrive=None, metadata=None,
             nvram_template=None, secure_boot=False, side_channels=None):
        # NOTE(mikal): if we cleaned this up to have less business logic in it,
        # then that would also mean that we could reduce the amount of duplicated
        # logic in mock_mariadb.create_instance().
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
            namespace = request_namespace()

        # If accessing a foreign namespace, we need to be an admin
        if not namespace_is_trusted(namespace, request_namespace()):
            return sf_api.error(404, 'namespace not found')

        # Check that the instance name is safe for use as a DNS host name
        valid_hostname = validators.hostname(
            name, skip_ipv4_addr=True, skip_ipv6_addr=True, may_have_port=False)
        contains_domain = '.' in name
        if not valid_hostname or contains_domain or len(name) > 63:
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
                return sf_api.error(
                    404, f'Specified node {placed_on} does not exist')
            # Normalize to UUID so the scheduler can match against its
            # UUID-keyed metrics dict.
            placed_on = str(n.uuid)
            node_state = n.state.value
            if node_state != Node.STATE_CREATED:
                n.add_event(
                    EVENT_TYPE_AUDIT, 'API query for node told node not ready',
                    extra={
                        'node_state': node_state,
                        'degraded_daemons': n.get_degraded_daemons()
                    })
                return sf_api.error(404, f'Specified node {placed_on} not ready')

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
                    f'{LABEL_URL}{request_namespace()}/{label}',
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

            elif (disk_base.startswith(UPLOAD_URL) or
                  disk_base.startswith(LABEL_URL) or
                  disk_base.startswith(ARTIFACT_URL)):
                if disk_base.startswith(UPLOAD_URL):
                    a = Artifact.from_url(Artifact.TYPE_IMAGE, disk_base,
                                          namespace=namespace)
                elif disk_base.startswith(LABEL_URL):
                    a = Artifact.from_url(Artifact.TYPE_LABEL, disk_base,
                                          namespace=namespace)
                else:
                    a_uuid = disk_base[len(ARTIFACT_URL):]
                    a = Artifact.from_db(a_uuid, suppress_failure_audit=True)

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
                # A plain URL, to be fetched from the internet. We ensure that
                # the image exists in the database in an initial state here so
                # that it will show up in image list requests. The image is
                # fetched by the queued job later.
                #
                # Resolution is by ownership, because that queued job ends in
                # add_index and add_index ends in delete_old_versions.
                # Resolving by visibility is how booting from the URL of a
                # shared image rolled the system namespace's artifact forward
                # and dropped the versions underneath it -- the operator guide
                # says outright that a non-system namespace should not be able
                # to update a shared artifact.
                #
                # Reuse is the whole point of sharing one, though, so a
                # visible artifact somebody else already fetched is still
                # worth having: we boot from its blob, which costs no download
                # and writes nothing. Reading theirs and writing only our own
                # is the distinction, not ours-or-nothing.
                if not Artifact.owned_from_url(Artifact.TYPE_IMAGE, disk_base,
                                               namespace=namespace):
                    theirs = Artifact.from_url(Artifact.TYPE_IMAGE, disk_base,
                                               namespace=namespace)

                    # The safety check is a usability test here rather than an
                    # authorisation one, so a failure falls through to our own
                    # fetch instead of refusing the request. Somebody else's
                    # half-built artifact is a reason to fetch our own copy,
                    # not a reason the instance cannot boot. Hence the
                    # predicate rather than _artifact_safety_checks: there is
                    # no refusal here to log or to return.
                    blob_uuid = None
                    if theirs and not _artifact_unusable_reason(theirs):
                        blob_uuid = theirs.resolve_to_blob()

                    if blob_uuid:
                        d['blob_uuid'] = blob_uuid
                    else:
                        Artifact.new(Artifact.TYPE_IMAGE, disk_base,
                                     namespace=namespace)

            transformed_disk.append(d)

        disk = transformed_disk

        # Perform a similar translation for NVRAM templates, turning them into
        # blob UUIDs.
        if nvram_template:
            original_template = nvram_template
            if nvram_template.startswith('label:'):
                label = nvram_template[len('label:'):]
                url = f'{LABEL_URL}{request_namespace()}/{label}'
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
            side_channels = ['sf-agent', 'sf-agent2']

        for sc in side_channels:
            if sc not in VALID_SIDE_CHANNELS:
                return sf_api.error(400, f'{sc} is not a known side channel type')

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
        inst.add_event(
            EVENT_TYPE_AUDIT, 'requested networking configuration',
            extra={
                'networks': network
            })

        # Initialise metadata
        if metadata:
            for k, v in metadata.items():
                inst.add_metadata_key(k, v)
                # Emitted here rather than in the validator: that runs
                # before Instance.new(), so there is no object to hang
                # an event on, and the create path is the one a caller
                # of the weighted form is most likely to be using.
                _warn_if_weighted_affinity(inst, k, v)

        # Allocate IP addresses
        order = 0
        updated_networks = []
        if network:
            for netdesc in network:
                netdesc, err = _netdesc_allocate_address(inst, netdesc, order)
                if err:
                    return err
                updated_networks.append(netdesc)
                order += 1

        inst.add_event(
            EVENT_TYPE_AUDIT,
            'post address allocation networking configuration',
            extra={
                'networks': updated_networks
            })

        # The NetworkInterface rows allocated above carry instance_uuid
        # already; the query-backed ``interfaces`` property finds them.

        if not SCHEDULER:
            SCHEDULER = scheduler.Scheduler()

        try:
            # Have we been placed?
            if not placed_on:
                candidates = SCHEDULER.find_candidates(inst)
            else:
                candidates = SCHEDULER.find_candidates(
                    inst, candidates=[placed_on])

        # This clause must stay *above* the LowResourceException one:
        # AffinityConstraintUnsatisfiable is a subclass of it, and
        # except clauses match in order, so reversing these two silently
        # turns every 409 back into a 507.
        except exceptions.AffinityConstraintUnsatisfiable as e:
            inst.add_event(
                EVENT_TYPE_AUDIT, 'schedule failed, affinity unsatisfiable',
                extra={'message': str(e)})
            inst.enqueue_delete_due_error('scheduling failed')
            return sf_api.error(409, str(e), suppress_traceback=True)

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

        # Record placement, by claiming the capacity to do so. The
        # scheduler's filters run against a metrics snapshot up to a
        # minute stale, so its ordered candidate list is a preference,
        # not a decision: the guarded capacity claim inside
        # place_instance() is what actually admits the instance, and a
        # refusal means some other create took the slot between the two.
        # So walk the list (D7). A WriteException is deliberately not
        # caught -- an unreachable database is not a full cluster, and
        # trying the next node would only ask it the same question.
        #
        # This walk (including the P9 demand-only re-walk below) also
        # exists in node_inst_netdesc_op.py's _instance_preflight();
        # until phase 5 extracts a shared helper, a semantic change here
        # must be made there too.
        denials = {}

        def place_walk(enforce_demand):
            for candidate in candidates:
                try:
                    inst.place_instance(
                        candidate, enforce_demand=enforce_demand)
                    return candidate
                except exceptions.CapacityAdmissionDenied as e:
                    denials[candidate] = {
                        'failing_stage': e.failing_stage,
                        'dimensions': e.dimensions,
                        'demand_only': e.demand_only,
                    }
                    inst.add_event(
                        EVENT_TYPE_AUDIT,
                        'schedule candidate refused by capacity guard',
                        extra={
                            'node': candidate,
                            'failing_stage': e.failing_stage,
                            'dimensions': e.dimensions,
                            'enforce_demand': enforce_demand,
                        })
            return None

        placement = place_walk(True)

        # The D13 demand term spreads correlated bursts across nodes; it
        # is not a capacity bound. The first pass already gave
        # demand-quiet nodes their preference, so if nothing admitted
        # and at least one candidate was refused on demand alone, the
        # only alternative to a second pass with the clause waived is
        # failing a create the cluster has real capacity for -- which
        # would turn a spreading heuristic into a user-visible rate
        # limit (the smoke CI single-node lockout of 2026-08-14).
        if placement is None and any(
                d['demand_only'] for d in denials.values()):
            inst.add_event(
                EVENT_TYPE_AUDIT,
                'no candidate admitted and some refused on demand alone, '
                'waiving demand guard',
                extra={'candidates': candidates, 'denials': denials})
            placement = place_walk(False)

        if placement is None:
            inst.add_event(
                EVENT_TYPE_AUDIT,
                'schedule failed, every candidate refused by capacity guard',
                extra={'candidates': candidates, 'denials': denials})
            inst.enqueue_delete_due_error('scheduling failed')
            return sf_api.error(
                507,
                'no node had capacity for this instance, %d candidates '
                'refused it' % len(denials),
                suppress_traceback=True)

        # Request the artifact fetches immediately, then the instance start
        instance_start_dependencies = inst.enqueue_disk_fetches(
            placement, PRIORITY.user_waiting,
            request_id=util_general.get_request_id(),
            artifact_event='creation request from REST API') or None

        nino_create_and_enqueue(
            placement,
            inst.uuid,
            updated_networks,
            [nino_tasks.instance_preflight,
             nino_tasks.instance_start],
            PRIORITY.user_waiting,
            request_id=util_general.get_request_id(),
            depends_on=instance_start_dependencies,
            runs_after=[inst.last_cluster_operation])

        return inst.external_view()

    @swag_from(api_base.swagger_helper(
        'instances', 'Delete all instances in a namespace.',
        [('confirm', 'body', 'boolean', 'I really mean it.', True),
         ('namespace', 'body', 'namespace',
          'The namespace to delete instances from', False)],
        [(200, 'A list of the UUIDs of instances awaiting deletion.', None),
         (400, 'The confirm parameter is not True or a administrative user has '
               'not specified a namespace.', None)]))
    @api_base.requires_namespace_exist_if_specified
    @api_base.log_token_use
    def delete(self, confirm=False, namespace=None):
        """Delete all instances in the namespace."""

        if confirm is not True:
            return sf_api.error(400, 'parameter confirm is not set true')

        if request_namespace() == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all instances in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return sf_api.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != request_namespace():
                return sf_api.error(401, 'you cannot delete other namespaces')
            namespace = request_namespace()

        waiting_for = []
        for inst in instance.Instances(namespace=namespace):
            inst.add_event(
                EVENT_TYPE_AUDIT, 'delete request via delete all from REST API')
            inst.enqueue_delete()
            waiting_for.append(str(inst.uuid))

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


class InstanceInterfacesEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'List network interfaces for an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(200, 'A list of network interfaces for an instance.',
          instance_interfaces_example),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        return [ni.external_view() for ni in instance_from_db.interfaces]

    @swag_from(api_base.swagger_helper(
        'instances', 'Create a new network interface on an instance',
        [
            ('instance_ref', 'path', 'uuidorname',
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
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def post(self, instance_ref=None, network=None, instance_from_db=None):
        if instance_from_db.state.value in instance.Instance.TERMINAL_STATES:
            return sf_api.error(406, 'instance in invalid state for hot plug')

        err = _netdesc_safety_checks(network, instance_from_db.namespace)
        if err:
            return err

        ifaces = instance_from_db.interfaces
        if not ifaces:
            order = 0
        else:
            order = max(ni.order for ni in ifaces) + 1

        netdesc, err = _netdesc_allocate_address(
            instance_from_db, network, order)
        if err:
            return err

        # We ensure the new interface is in the DHCP service for the network
        # before we plug the interface into the instance.
        #
        # The hot-plug op runs ``n.create_on_hypervisor()`` on the target
        # node as a side effect, so for the duration of the chain the
        # network is being modified on that node. The cluster_operation_targets
        # rows written automatically by enqueue_cluster_operation() mark both
        # the network and the instance as "operation in flight" so the network
        # maintainer's Network.is_okay() check defers its own recreate path.
        #
        # Phase 6 of `PLAN-network-facade.md` retired the misleadingly-
        # named `network_update_dnsmasq` composite task; the explicit task
        # list preserves the broader reconciliation (create on network
        # node, then ensure mesh) that this caller has always wanted.
        reconcile_op_type, reconcile_op_uuid = net_create_and_enqueue(
            netdesc['network_uuid'],
            [net_tasks.network_apply_create_network_node,
             net_tasks.network_ensure_mesh],
            priority=PRIORITY.user_waiting
        )

        niio_create_and_enqueue(
            instance_from_db.placement['node'],
            instance_from_db.uuid,
            netdesc['network_uuid'],
            netdesc['iface_uuid'],
            [niio_tasks.hot_plug_instance_interface],
            PRIORITY.user_waiting,
            request_id=util_general.get_request_id(),
            depends_on=[
                dependency(op_type=reconcile_op_type,
                           op_uuid=reconcile_op_uuid)
            ],
            runs_after=[instance_from_db.last_cluster_operation])
        # The NetworkInterface row created above is the source of truth
        # for the instance->NI association; no further bookkeeping needed.

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


class InstanceEventsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Get instance event information.',
        [
            ('instance_ref', 'path', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100 and is '
             'capped at 1000.', False, {'minimum': 1, 'maximum': 1000})
        ],
        [(200, 'Event information about a single instance.', instance_events_example),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, event_type=None, limit=100, instance_from_db=None):
        return api_base.object_events_response(
            'instance', instance_from_db.uuid, limit, event_type)


class InstanceRebootSoftEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Soft (ACPI) reboot an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be rebooted.', None)]))
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
            return sf_api.error(409, f'Invalid lifecycle state: {e}')


class InstanceRebootHardEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Hard (reset switch) reboot an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be rebooted.', None)]))
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
            return sf_api.error(409, f'Invalid lifecycle state: {e}')


class InstancePowerOffEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Power off an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be powered off.', None)]))
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
            return sf_api.error(409, f'Invalid lifecycle state: {e}')


class InstancePowerOnEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Power on an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be powered on.', None)]))
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
            return sf_api.error(409, f'Invalid lifecycle state: {e}')


class InstancePauseEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Pause an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be paused.', None)]))
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
            return sf_api.error(409, f'Invalid lifecycle state: {e}')


class InstanceUnpauseEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Unpause an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True)],
        [(404, 'Instance not found.', None),
         (409, 'The instance cannot be unpaused.', None)]))
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
            return sf_api.error(409, f'Invalid lifecycle state: {e}')


class InstanceMetadatasEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Fetch metadata for an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The instance to fetch metadata for.', True)],
        [(200, 'Instance metadata, if any.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        return instance_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'instances', 'Add metadata for an instance.',
        [
            ('instance_ref', 'path', 'uuidorname', 'The instance to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
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
        _warn_if_weighted_affinity(instance_from_db, key, value)
        instance_from_db.add_metadata_key(key, value)


AFFINITY_DEPRECATION_MESSAGE = (
    'deprecated weighted affinity specification accepted')


def _affinity_spec_is_weighted(value):
    """Is this affinity value the deprecated weighted form?

    The shape test lives here so that the validator and the deprecation
    warning cannot disagree about which form a caller sent.
    """
    if not isinstance(value, dict) or not value:
        return False
    return not (set(value) & set(instance.Instance.AFFINITY_BINARY_KEYS))


def _warn_if_weighted_affinity(inst, key, value):
    """Warn, once per acceptance, that the weighted form is deprecated.

    Emitted where the specification is *accepted* rather than where it
    is consumed. Per-schedule would fire on every create and every
    reschedule, and would need either an attribute write or a read of
    the instance's own event history on the scheduling hot path; both
    are the kind of addition the database load budget exists to catch.
    Per-process would reset on every daemon restart.

    Accept time also puts the warning where the caller can act on it,
    at the moment they submit the deprecated form.

    The limit this accepts, deliberately: it covers new acceptances
    only. Every instance already carrying a weighted specification when
    this shipped warns nobody, ever. The discovery recipe in the
    operator guide is what covers those, and it is what the eventual
    removal release will need.
    """
    if key != instance.Instance.METADATA_KEY_AFFINITY:
        return
    if not _affinity_spec_is_weighted(value):
        return

    inst.add_event(
        EVENT_TYPE_AUDIT, AFFINITY_DEPRECATION_MESSAGE,
        extra={
            'affinity': value,
            'mapped_to': instance.map_weighted_affinity(value),
            'note': ('weighted affinity values are deprecated and will be '
                     'removed in a future release; the magnitude is already '
                     'ignored by the scheduler'),
        })


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

        # The binary model is a second value shape under the same key,
        # not a second key, so the two are told apart here by type. A
        # dict whose keys are the four reserved names is the binary
        # form; anything else is read as the weighted form and has to
        # coerce to integers below. A spec mixing the two is refused
        # rather than guessed at, because either guess silently
        # discards half of what the caller asked for.
        binary_keys = set(instance.Instance.AFFINITY_BINARY_KEYS)
        present = set(value.keys())
        if present & binary_keys:
            unknown = present - binary_keys
            if unknown:
                return sf_api.error(
                    400,
                    'affinity keys %s are not valid alongside the binary '
                    'affinity constraints; the weighted and binary forms '
                    'cannot be mixed' % sorted(unknown))

            for constraint, tags in value.items():
                if not isinstance(tags, list):
                    return sf_api.error(
                        400,
                        'value for affinity constraint "%s" should be a JSON '
                        'list of tags' % constraint)
                for tag in tags:
                    # isinstance(True, str) is false, so booleans are
                    # already excluded here, unlike in the weighted form.
                    if not isinstance(tag, str) or not tag:
                        return sf_api.error(
                            400,
                            'affinity constraint "%s" should contain only '
                            'non-empty tag names' % constraint)
            return

        for key_type, dv in value.items():
            # isinstance(True, int) is true in Python, so int(True) is 1 and a
            # JSON true would be accepted as a weight of one. Nobody writing
            # true means "weight 1", and the binary affinity model gives it a
            # meaning nobody asked for, so refuse it explicitly.
            if isinstance(dv, bool):
                return sf_api.error(
                    400, 'affinity dictionary values should be integers, not booleans')

            # int() raises TypeError -- not ValueError -- for a list, a dict or
            # None, and OverflowError for infinity. json.loads() accepts the
            # bare Infinity and NaN literals by default, so flask hands them
            # straight through and a bare ValueError handler returns a 500 to
            # the caller. NaN is already covered: int(float('nan')) is a
            # ValueError.
            try:
                int(dv)
            except (TypeError, ValueError, OverflowError):
                return sf_api.error(400, 'affinity dictionary values should be integers')


class InstanceMetadataEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Update a metadata key for an instance.',
        [
            ('instance_ref', 'path', 'uuidorname', 'The instance to add a key to.', True),
            ('key', 'path', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
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
        _warn_if_weighted_affinity(instance_from_db, key, value)
        instance_from_db.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'instances', 'Delete a metadata key for an instance.',
        [
            ('instance_ref', 'path', 'uuidorname', 'The instance to remove a key from.', True),
            ('key', 'path', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
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


class InstanceConsoleDataEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Fetch console data from an instance.',
        [
            ('instance_ref', 'path', 'uuidorname',
             'The instance fetch console data for.', True),
            # Not unsignedinteger: -1 is a supported sentinel meaning
            # "the whole log", which get_console_data() special-cases
            # and the functional suite relies on. Publishing minimum 0
            # would describe the API as narrower than it is, and phase
            # 4 would compile that into rejecting a value which works.
            ('length', 'body', 'integer',
             'The amount of data to fetch, defaults to 10240 bytes. Use -1 '
             'to fetch the entire console log.', False)
        ],
        [(200, 'The console data as an application/octet-stream.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
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
            mimetype='application/octet-stream')
        resp.status_code = 200
        return resp

    @swag_from(api_base.swagger_helper(
        'instances', 'Delete console data for an instance.',
        [
            ('instance_ref', 'path', 'uuidorname',
             'The instance fetch console data for.', True)
        ],
        [(200, 'Nothing.', None),
         (404, 'Instance not found.', None)],
        requires_admin=True))
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
# Note that virt-viewer accepts only 'spice' and 'vnc' as the type.
VIRTVIEWER_TEMPLATE = """[virt-viewer]
type=%(vdi_type)s
host=%(node)s
port=%(vdi_port)s%(vdi_tls_port)s%(host_subject)s
delete-this-file=1%(ca_cert)s
"""


instance_vv_file_example = """[virt-viewer]
type=spice
host=192.168.1.53
port=42281
tls-port=43197
host-subject=CN=sf-3
delete-this-file=1
ca=-----BEGIN CERTIFICATE-----\nMIIEF...16br/Fw==\n-----END CERTIFICATE-----\n"""


class InstanceVDIConsoleHelperEndpoint(api_base.Resource):
    # A .vv file carries the SPICE connection credentials for the
    # guest, so this hands out interactive keyboard and mouse control
    # rather than an observation. Deriving instance.read would let a
    # credential granted only to watch instances take one over.
    @api_base.scope(verb='console')
    @swag_from(api_base.swagger_helper(
        'instances',
        ('Fetch a virt-viewer .vv file describing how to connect to the VDI console '
         'for this instance.'),
        [
            ('instance_ref', 'path', 'uuidorname',
             'The instance fetch console data for.', True)
        ],
        [(200, 'A .vv file to open in virt-viewer as a application/x-virt-viewer stream.',
          instance_vv_file_example),
         (404, 'Instance not found.', None)],
        requires_admin=True))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        p = instance_from_db.ports

        # placement['node'] holds a node UUID, which is not a connectable
        # address. Emit the node's IP: a node name is not resolvable from
        # outside the cluster either, and the TLS identity check travels in
        # host-subject below rather than in the hostname.
        n = Node.from_db(instance_from_db.placement.get('node'))
        if not n:
            return sf_api.error(404, 'placement node not found')

        cacert = ''
        if os.path.exists('/etc/pki/libvirt-spice/ca-cert.pem'):
            with open('/etc/pki/libvirt-spice/ca-cert.pem') as f:
                cacert = f.read()
            cacert = '\nca=%s' % cacert.replace('\n', '\\n')

        tls_port = ''
        host_subject = ''
        if p.get('vdi_tls_port'):
            tls_port = '\ntls-port=%s' % p['vdi_tls_port']
            # Every hypervisor's SPICE certificate is signed by the same
            # cluster CA, so without a pinned subject a viewer would accept
            # any node in the cluster as this endpoint.
            subject = n.spice_server_cert_subject
            if subject:
                host_subject = '\nhost-subject=%s' % subject

        # video['vdi'] is Shaken Fist's internal enum. The SPICE variants
        # (spiceconcurrent, spicedebug) are server-side policy invisible to
        # the viewer, which accepts only 'spice' and 'vnc'.
        vdi_type = instance_from_db.video['vdi']
        if vdi_type.startswith('spice'):
            vdi_type = 'spice'

        vvconfig = VIRTVIEWER_TEMPLATE % {
            'vdi_type': vdi_type,
            'node': n.ip,
            'vdi_port': p.get('vdi_port'),
            'vdi_tls_port': tls_port,
            'host_subject': host_subject,
            'ca_cert': cacert
        }

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'vdiconsole request from REST API')
        resp = flask.Response(
            vvconfig, mimetype='application/x-virt-viewer')
        resp.status_code = 200
        return resp


instance_vdiconsoleproxy_get_example = """{
    "url": "https://kerbside.example.com/sf-console.vv?token=eyJhbGciOiJFZERTQSIs...",
    "expires_at": 1789000300
}"""


class InstanceVDIProxyConsoleHelperEndpoint(api_base.Resource):
    # Mints an Ed25519 console token, which is a credential for full
    # interactive control of the guest. Same reasoning as the .vv file
    # helper above: this is not a read.
    @api_base.scope(verb='console')
    @swag_from(api_base.swagger_helper(
        'instances',
        ('Mint a short lived Kerbside VDI console token and return a '
         'proxy URL for the SPICE console of this instance.'),
        [
            ('instance_ref', 'path', 'uuidorname',
             'The instance to mint a VDI console proxy token for.', True)
        ],
        [(200, 'A Kerbside proxy URL and the token expiry time.',
          instance_vdiconsoleproxy_get_example),
         (404, 'Instance not found, or kerbside integration is not '
          'configured.', None),
         (406, 'Instance is not ready.', None),
         (409, 'Instance does not have a SPICE console.', None),
         (500, 'Kerbside signing key is not configured.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        if not config.KERBSIDE_URL:
            return sf_api.error(
                404, 'kerbside integration is not configured')

        if instance_from_db.state.value != dbo.STATE_CREATED:
            return sf_api.error(
                406,
                f'instance {instance_from_db.uuid} is not ready '
                f'({instance_from_db.state.value})')

        video = instance_from_db.video
        if not video or not video.get('vdi', '').startswith('spice'):
            return sf_api.error(
                409, 'instance does not have a SPICE console')

        namespace = instance_from_db.namespace

        # The proxy URL base and the token audience must be byte-identical:
        # Kerbside compares the audience by exact string match, so normalise a
        # possibly trailing-slashed KERBSIDE_URL once and use it for both.
        base = config.KERBSIDE_URL.rstrip('/')

        try:
            minted = vdi_tokens.mint_console_token(
                str(instance_from_db.uuid), namespace,
                audience=base, issuer=config.ZONE,
                duration=config.KERBSIDE_TOKEN_DURATION)
        except vdi_tokens.SigningKeyError:
            return sf_api.error(
                500,
                'kerbside signing key is not configured, run sf-ctl '
                'ensure-kerbside-signing-key')

        url = f'{base}/sf-console.vv?token={minted["token"]}'

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'vdi console proxy token minted',
            extra={
                'jti': minted['jti'],
                'kid': minted['kid'],
                'namespace': namespace,
                'expires_at': minted['expires_at']
            })

        return {'url': url, 'expires_at': minted['expires_at']}


class InstanceAgentPutEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Upload a file to an instance via the Shaken Fist agent.',
        [
            ('instance_ref', 'path', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('blob_uuid', 'body', 'uuid',
             'The UUID of the blob to put onto the instance.', True),
            ('path', 'body', 'string',
             'The path to write the file at inside the instance.', True),
            ('mode', 'body', 'string',
             'The mode of the file once written, in symbolic or numeric form.', True),
            ('deadline_seconds', 'body', 'number',
             DEADLINE_SECONDS_DESCRIPTION, False,
             {'minimum': 0, 'maximum': config.AGENT_OPERATION_MAX_DEADLINE}),
            ('progress_timeout_seconds', 'body', 'number',
             PROGRESS_TIMEOUT_SECONDS_DESCRIPTION, False,
             {'minimum': 0, 'maximum': config.AGENT_OPERATION_MAX_DEADLINE})
        ],
        [(200, 'An agent operation.', api_agentoperation.agentoperation_get_example),
         (400, 'No agent connection to instance, or an invalid timing parameter.', None),
         (404, 'Instance or blob not found.', None),
         (406, 'Invalid mode specified', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, blob_uuid=None, path=None, mode=None,
             deadline_seconds=None, progress_timeout_seconds=None,
             instance_from_db=None):
        if not instance_from_db.agent_state.value.startswith('ready'):
            return sf_api.error(400, 'instance agent not ready')

        # Before any event or object is created: a refused request must
        # leave nothing behind but the request log. put-blob reports
        # progress, so this operation is progress capable.
        timing, error = api_base.agent_operation_timing(
            deadline_seconds, progress_timeout_seconds, True)
        if error:
            return error
        deadline, progress_timeout = timing

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
                               str(instance_from_db.uuid), commands,
                               deadline=deadline,
                               progress_timeout=progress_timeout)
        instance_from_db.agent_operation_enqueue(o.uuid)
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'queued agent command requiring preflight',
            extra={'agentoperation': o.uuid, 'commands': commands})
        o.state = AgentOperation.STATE_PREFLIGHT
        na_create_and_enqueue(
            instance_from_db.placement['node'], o.uuid,
            [na_tasks.preflight],
            PRIORITY.user_facing,
            request_id=util_general.get_request_id())
        return o.external_view()


class InstanceAgentGetEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Download a file from an instance via the Shaken Fist agent.',
        [
            ('instance_ref', 'path', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('path', 'body', 'string',
             'The path to fetch the file from inside the instance.', True),
            ('deadline_seconds', 'body', 'number',
             DEADLINE_SECONDS_DESCRIPTION, False,
             {'minimum': 0, 'maximum': config.AGENT_OPERATION_MAX_DEADLINE}),
            ('progress_timeout_seconds', 'body', 'number',
             PROGRESS_TIMEOUT_SECONDS_DESCRIPTION, False,
             {'minimum': 0, 'maximum': config.AGENT_OPERATION_MAX_DEADLINE})
        ],
        [(200, 'An agent operation.', api_agentoperation.agentoperation_get_example),
         (400, 'No agent connection to instance, or an invalid timing parameter.', None),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, path=None, deadline_seconds=None,
             progress_timeout_seconds=None, instance_from_db=None):
        if not instance_from_db.agent_state.value.startswith('ready'):
            return sf_api.error(400, 'instance agent not ready')

        # get-file reports progress, so this operation is progress
        # capable.
        timing, error = api_base.agent_operation_timing(
            deadline_seconds, progress_timeout_seconds, True)
        if error:
            return error
        deadline, progress_timeout = timing

        commands = [
            {
                'command': 'get-file',
                'path': path
            }
        ]

        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'agent operation get-file request from REST API')
        o = AgentOperation.new(str(uuid.uuid4()), instance_from_db.namespace,
                               str(instance_from_db.uuid), commands,
                               deadline=deadline,
                               progress_timeout=progress_timeout)
        instance_from_db.agent_operation_enqueue(o.uuid)
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'queued agent command not requiring preflight',
            extra={'agentoperation': o.uuid, 'commands': commands})
        o.state = AgentOperation.STATE_QUEUED
        return o.external_view()


class InstanceAgentExecuteEndpoint(api_base.Resource):
    # Arbitrary command execution inside the guest is a different kind
    # of privilege from creating an instance, and an operator writing a
    # mapping rule would sensibly grant one without the other, so it
    # gets its own verb rather than riding on instance.write.
    @api_base.scope(verb='execute')
    @swag_from(api_base.swagger_helper(
        'instances', 'Execute a command within an instance via the Shaken Fist agent.',
        [
            ('instance_ref', 'path', 'uuidorname',
             'The UUID or name of the instance.', True),
            ('command_line', 'body', 'string', 'The command to execute.', True),
            ('deadline_seconds', 'body', 'number',
             DEADLINE_SECONDS_DESCRIPTION, False,
             {'minimum': 0, 'maximum': config.AGENT_OPERATION_MAX_DEADLINE})
        ],
        [(200, 'An agent operation.', api_agentoperation.agentoperation_get_example),
         (400, 'No agent connection to instance, an invalid timing parameter, or a '
          'progress_timeout_seconds, which this call does not accept.', None),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.requires_instance_active
    @api_base.log_token_use
    def post(self, instance_ref=None, command_line=None,
             deadline_seconds=None, instance_from_db=None):
        if not instance_from_db.agent_state.value.startswith('ready'):
            return sf_api.error(400, 'instance agent not ready')

        # No command this endpoint builds reports progress, so it
        # publishes no progress_timeout_seconds and records an explicit
        # 0.0 -- which is true of the operation, and keeps NULL meaning
        # only "written by an API node which predates deadlines".
        timing, error = api_base.agent_operation_timing(
            deadline_seconds, None, False)
        if error:
            return error
        deadline, progress_timeout = timing

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
                               str(instance_from_db.uuid), commands,
                               deadline=deadline,
                               progress_timeout=progress_timeout)
        instance_from_db.agent_operation_enqueue(o.uuid)
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'queued agent command not requiring preflight',
            extra={'agentoperation': o.uuid, 'commands': commands})
        o.state = AgentOperation.STATE_QUEUED
        return o.external_view()


class InstanceScreenshotEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'instances', 'Collect a screenshot of an instance.',
        [
            ('instance_ref', 'path', 'uuidorname',
             'The UUID or name of the instance.', True)
        ],
        [(200, 'The UUID of a blob containing the screenshot.', None),
         (404, 'Instance not found.', None)]))
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.redirect_instance_request
    @api_base.requires_instance_active
    @api_base.log_token_use
    def get(self, instance_ref=None, instance_from_db=None):
        instance_from_db.add_event(
            EVENT_TYPE_AUDIT, 'screenshot request from REST API')
        return instance_from_db.get_screenshot()


instance_outstanding_operations_example = """[
    [
        {
            "instance_uuid": "5d6810b9-cef5-4c88-9406-b4c11e830de1",
            "net_desc": [
                {
                    "address": "10.0.13.109",
                    "float": true,
                    "iface_uuid": "2e5fa696-2cee-40b1-9f6b-61c45e5a9027",
                    "macaddress": "02:00:00:9a:4d:5e",
                    "model": "virtio",
                    "network_uuid": "7c822e61-a5cc-45ed-9ffa-086568aa7ade"
                }
            ],
            "node_uuid": "localhost",
            "operation_type": "node_inst_netdesc_op",
            "state": "complete",
            "tasks": [
                "instance_preflight",
                "instance_start"
            ],
            "uuid": "5dd7a116-37e3-474e-aeaa-fb003e5f5c60"
        },
        {
            "instance_uuid": "5d6810b9-cef5-4c88-9406-b4c11e830de1",
            "namespace": "system",
            "operation_type": "artifact_fetch_op",
            "state": "complete",
            "tasks": [
                "image_fetch"
            ],
            "url": "debian:12",
            "uuid": "c3615ff7-228b-44e5-ab32-0fe668389528"
        }
    ]
]"""


class InstanceOutstandingOperationsEndpoint(api_base.Resource):
    # NOTE(mikal): note that arguments from URL routes (object uuid for example),
    # are not included in the webargs schema because webargs doesn't appear to
    # know how to find them.
    get_args = {
        'all': fields.Boolean(load_default=False)
    }

    @swag_from(api_base.swagger_helper(
        'instances', 'Get the outstanding cluster operations for an instance.',
        [('instance_ref', 'path', 'uuidorname',
          'The UUID or name of the instance.', True),
         ('all', 'query', 'boolean',
          'Include operations which have already completed, rather than '
          'only those still in flight.', False)],
        [(
            200,
            'A list of the cluster operations not yet executed for this instance.',
            instance_outstanding_operations_example),
         (404, 'Instance not found.', None)]))
    @use_kwargs(get_args, location='json_or_query')
    @api_base.arg_is_instance_ref
    @api_base.requires_instance_ownership
    @api_base.log_token_use
    def get(self, instance_ref=None, all=False, instance_from_db=None):
        retval = []
        for op in instance_from_db.get_cluster_operations(
            outstanding_only=(not all)
        ):
            retval.append(op.external_view())
        return retval
