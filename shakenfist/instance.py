# Copyright 2019 Michael Still
# Please note: instances are a "compositional" baseobject type, which means
# part of their role is to combine foundational baseobjects into something more
# useful.
import base64
import copy
import io
import json
import os
import pathlib
import random
import shutil
import socket
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from contextlib import contextmanager
from functools import partial
from uuid import UUID
from uuid import uuid4

import jinja2
import pycdlib
from shakenfist_utilities import logs  # noreorder

from shakenfist import artifact
from shakenfist import baseobject
from shakenfist import blob
from shakenfist import constants
from shakenfist.constants import get_object_class
from shakenfist import locks
from shakenfist import mariadb
from shakenfist.schema.instance_attributes import InstanceAttributesData
from shakenfist.schema.instance_data import InstanceData
from shakenfist.schema.operations.artifact_fetch_op \
    import create_and_enqueue as afo_create_and_enqueue
from shakenfist.schema.operations.artifact_fetch_op \
    import model_tasks as afo_tasks
from shakenfist.schema.operations.baseclusteroperation import dependency
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.node_inst_op \
    import create_and_enqueue as nio_create_and_enqueue
from shakenfist.schema.operations.node_inst_op \
    import model_tasks as nio_tasks
from shakenfist.schema.operations.node_inst_snap_op \
    import create_and_enqueue as niso_create_and_enqueue
from shakenfist.schema.operations.node_inst_snap_op \
    import snapshot as niso_snapshot
from shakenfist.schema.operations.node_inst_snap_op \
    import model_tasks as niso_tasks
from shakenfist.eventlog import add_event_multi
from shakenfist import exceptions
from shakenfist.network import network
from shakenfist.network import interface
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.operations.agentoperation import AgentOperations
from shakenfist.operations.agentoperation \
    import instance_filter as agent_instance_filter
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectWithOperations as dbowo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.object_reference import references_to_grouped_dict
from shakenfist.schema.object_types import ObjectType
from shakenfist.operations.baseoperation import BaseClusterOperation as bco
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logs.setup(__name__)


def _get_defaulted_disk_bus(disk):
    bus = disk.get('bus')
    if bus:
        return bus
    return config.DISK_BUS


LETTERS = 'abcdefghijklmnopqrstuvwxyz'
NUMBERS = '0123456789'


def _get_disk_device(bus, index):
    bases = {
        'sata': ('sd', LETTERS),
        'scsi': ('sd', LETTERS),
        'usb': ('sd', LETTERS),
        'virtio': ('vd', LETTERS),
        'nvme': ('nvme', NUMBERS),
    }
    if bus not in bases:
        raise exceptions.InstanceBadDiskSpecification(f'Unknown bus {bus}')
    prefix, index_scheme = bases.get(bus)
    return f'{prefix}{index_scheme[index]}'


def _get_defaulted_disk_type(disk):
    kind = disk.get('type')
    if kind:
        return kind
    return 'disk'


def _safe_int_cast(i):
    if i:
        return int(i)
    return i


def traverse_cluster_operations_tree(op, only_incomplete=True):
    # Walk the tree of ops from a starting point and yield all ops.
    if not op:
        return

    if only_incomplete and op.state.value not in [
        bco.STATE_QUEUED,
        bco.STATE_EXECUTING
    ]:
        return

    for dep in op.depends_on:
        dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
        for thing in traverse_cluster_operations_tree(dep_op):
            yield thing

    for dep in op.runs_after:
        dep_op = get_object_class(dep['op_type']).from_db(dep['op_uuid'])
        for thing in traverse_cluster_operations_tree(dep_op):
            yield thing

    yield op


class ConnectedVSockChannel():
    def __init__(self, channel, cid, port, log):
        self.channel = channel
        self.cid = cid
        self.port = port
        self.log = log.with_fields({
            'channel': channel,
            'cid': cid,
            'port': port
        })
        self.sock = None

    def __enter__(self):
        self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        self.sock.connect((self.cid, self.port))
        self.log.debug(f'Connected to vsock with socket {self.sock}')
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.log.debug(f'Disconnected from vsock channel {self.channel}')
        self.sock.close()


class Instance(dbowo):
    object_type = ObjectType.INSTANCE
    initial_version = 19
    current_version = 19

    # STORAGE_PATH-relative subdirectories this object type depends on to be
    # healthy on a node that hosts it (PLAN-node-resource-health). An
    # instance needs its own COW disk dir, the image cache it boots from, and
    # the blob store its backing image lives in.
    health_dependencies = ['instances', 'image_cache', 'blobs']

    # Attributes stored in MariaDB (everything else stays in etcd).
    # ``interfaces`` was here pre-phase-7; the column on
    # ``instance_attributes`` is dropped in phase 7e and the property
    # now queries ``network_interfaces`` directly.
    MARIADB_ATTRIBUTES = {
        'placement', 'power_state', 'ports', 'enforced_deletes',
        'block_devices', 'agent_state',
        'agent_attributes', 'agent_operations', 'kvm_pid', 'error',
        'vsock_cids',
    }

    # docs/developer_guide/state_machine.md has a description of these states.
    STATE_INITIAL_ERROR = 'initial-error'
    STATE_PREFLIGHT = 'preflight'
    STATE_PREFLIGHT_ERROR = 'preflight-error'
    STATE_CREATING_ERROR = 'creating-error'
    STATE_CREATED_ERROR = 'created-error'
    STATE_DELETE_WAIT_ERROR = 'delete-wait-error'

    ACTIVE_STATES = {
        dbo.STATE_INITIAL, STATE_INITIAL_ERROR, STATE_PREFLIGHT,
        STATE_PREFLIGHT_ERROR, dbo.STATE_CREATING, STATE_CREATING_ERROR,
        dbo.STATE_CREATED, dbo.STATE_DELETE_WAIT, STATE_CREATED_ERROR,
        dbo.STATE_ERROR
    }
    HEALTHY_STATES = {
        dbo.STATE_INITIAL, STATE_PREFLIGHT, dbo.STATE_CREATING,
        dbo.STATE_CREATED
    }
    TERMINAL_STATES = {
        dbo.STATE_DELETED, dbo.STATE_DELETE_WAIT, dbo.STATE_HARD_DELETED,
        dbo.STATE_ERROR, STATE_INITIAL_ERROR, STATE_PREFLIGHT_ERROR,
        STATE_CREATING_ERROR, STATE_CREATED_ERROR, STATE_DELETE_WAIT_ERROR
    }
    ERROR_STATES = {
        dbo.STATE_ERROR, STATE_INITIAL_ERROR, STATE_PREFLIGHT_ERROR,
        STATE_CREATING_ERROR, STATE_CREATED_ERROR, STATE_DELETE_WAIT_ERROR
    }

    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (STATE_PREFLIGHT, dbo.STATE_DELETE_WAIT,
                            dbo.STATE_DELETED, STATE_INITIAL_ERROR),
        STATE_PREFLIGHT: (dbo.STATE_CREATING, dbo.STATE_DELETE_WAIT,
                          dbo.STATE_DELETED, STATE_PREFLIGHT_ERROR),
        dbo.STATE_CREATING: (dbo.STATE_CREATED, dbo.STATE_DELETE_WAIT,
                             dbo.STATE_DELETED, STATE_CREATING_ERROR,
                             dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED,
                            STATE_CREATED_ERROR, dbo.STATE_ERROR),
        STATE_INITIAL_ERROR: (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED,
                              dbo.STATE_ERROR),
        STATE_PREFLIGHT_ERROR: (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED,
                                dbo.STATE_ERROR),
        STATE_CREATING_ERROR: (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED,
                               dbo.STATE_ERROR),
        STATE_CREATED_ERROR: (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED,
                              dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED,
                          dbo.STATE_ERROR),
        dbo.STATE_DELETE_WAIT: (dbo.STATE_DELETED, STATE_DELETE_WAIT_ERROR),
        STATE_DELETE_WAIT_ERROR: (dbo.STATE_ERROR),
        dbo.STATE_DELETED: None,
    }

    # Metadata - Reserved Keys
    METADATA_KEY_TAGS = 'tags'
    METADATA_KEY_AFFINITY = 'affinity'

    # Per-call memo of the instance_attributes row, only populated inside
    # an attribute_memo() block. Class level defaults so that an attribute
    # read during object construction doesn't need __init__ to have run.
    __attribute_memo = None
    __attribute_memo_depth = 0

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values.get('uuid'), static_values.get('version'))

        self.__cpus = static_values.get('cpus')
        self.__disk_spec = static_values.get('disk_spec')
        self.__memory = static_values.get('memory')
        self.__name = static_values.get('name')
        self.__namespace = static_values.get('namespace')
        self.__requested_placement = static_values.get('requested_placement')
        self.__ssh_key = static_values.get('ssh_key')
        self.__user_data = static_values.get('user_data')
        self.__video = static_values.get('video')
        self.__uefi = static_values.get('uefi', False)
        self.__configdrive = static_values.get(
            'configdrive', 'openstack-disk')
        self.__nvram_template = static_values.get('nvram_template')
        self.__secure_boot = static_values.get('secure_boot', False)
        self.__machine_type = static_values.get('machine_type', 'pc')
        self.__side_channels = static_values.get('side_channels', [])

        if not self.__disk_spec:
            # This should not occur since the API will filter for zero disks.
            raise exceptions.InstanceBadDiskSpecification()

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        """Create an Instance record in both etcd and MariaDB."""
        # Write to etcd (base class behavior)
        super()._db_create(object_uuid, metadata)

        # Also write static values to MariaDB
        _uuid = object_uuid if isinstance(object_uuid, UUID) else UUID(object_uuid)

        # Normalize values for Pydantic validation
        requested_placement = metadata.get('requested_placement')
        if not isinstance(requested_placement, dict):
            requested_placement = None

        video = metadata.get('video', {})
        if not isinstance(video, dict):
            video = {'model': str(video)} if video else {}

        side_channels = metadata.get('side_channels')
        if not isinstance(side_channels, list):
            side_channels = []

        data = InstanceData(
            uuid=_uuid,
            cpus=metadata.get('cpus', 0),
            disk_spec=metadata.get('disk_spec', []),
            memory=metadata.get('memory', 0),
            name=metadata.get('name', ''),
            namespace=metadata.get('namespace', ''),
            requested_placement=requested_placement,
            ssh_key=metadata.get('ssh_key'),
            user_data=metadata.get('user_data'),
            video=video,
            uefi=metadata.get('uefi', False),
            configdrive=metadata.get('configdrive', 'openstack-disk'),
            nvram_template=metadata.get('nvram_template'),
            secure_boot=metadata.get('secure_boot', False),
            machine_type=metadata.get('machine_type', 'pc'),
            side_channels=side_channels,
            version=metadata.get('version', cls.current_version)
        )
        if not mariadb.create_instance(data):
            raise RuntimeError(f'Failed to create instance {object_uuid} in MariaDB')

        # Create initial attributes record
        attrs = InstanceAttributesData(uuid=_uuid)
        if not mariadb.create_instance_attributes(attrs):
            raise RuntimeError(f'Failed to create instance attributes {object_uuid} in MariaDB')

    @staticmethod
    def _static_values_to_dict(data):
        """Convert InstanceData to the dict format used internally."""
        return {
            'uuid': str(data.uuid),
            'cpus': data.cpus,
            'disk_spec': data.disk_spec,
            'memory': data.memory,
            'name': data.name,
            'namespace': data.namespace,
            'requested_placement': data.requested_placement,
            'ssh_key': data.ssh_key,
            'user_data': data.user_data,
            'video': data.video,
            'uefi': data.uefi,
            'configdrive': data.configdrive,
            'nvram_template': data.nvram_template,
            'secure_boot': data.secure_boot,
            'machine_type': data.machine_type,
            'side_channels': data.side_channels,
            'version': data.version,
        }

    @classmethod
    def _db_get(cls, object_uuid):
        """Get Instance static values from MariaDB."""
        _uuid = object_uuid if isinstance(object_uuid, UUID) else UUID(object_uuid)
        data = mariadb.get_instance(_uuid)
        if not data:
            return None

        result = cls._static_values_to_dict(data)
        if result.get('version', 0) != cls.current_version:
            if not cls.upgrade_supported:
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {result}')
        return result

    @classmethod
    def filter(cls, filters):
        """Override base class to use MariaDB instead of etcd.

        Documented fallback: ``Instance.from_db_by_ref`` is the
        live name-lookup path and pushes its predicates to SQL
        via ``find_instances``. ``filter()`` exists so the
        predicate API on ``DatabaseBackedObject.from_db_by_ref``
        keeps a usable implementation, even though no in-tree
        caller currently reaches it.
        """
        for data in mariadb.get_all_instances():  # nopushdown: fallback (see docstring)
            obj = cls(cls._static_values_to_dict(data))
            if all(f(obj) for f in filters):
                yield obj

    @classmethod
    def from_db_by_ref(cls, object_ref, namespace=None):
        """Look up an instance by UUID or by name within a namespace.

        UUID lookups short-circuit to from_db. Name lookups push
        state + namespace + name down to a single indexed SQL
        query via mariadb.find_instances.
        """
        if object_ref and util_general.valid_uuid4(object_ref):
            return cls.from_db(object_ref)

        # namespace='system' or namespace=None means 'look across
        # all namespaces' - preserve that by omitting the namespace
        # filter. Matches baseobject.namespace_filter semantics.
        criteria_namespace = (
            namespace if namespace and namespace != 'system' else None)

        criteria = ObjectFilterCriteria(
            states=list(cls.ACTIVE_STATES),
            namespace=criteria_namespace,
            name=object_ref,
        )
        matches = mariadb.find_instances(criteria)

        if not matches:
            return None
        if len(matches) > 1:
            raise exceptions.MultipleObjects(
                f'multiple instances have the name "{object_ref}"'
                f' in namespace "{namespace}"')
        return cls(cls._static_values_to_dict(matches[0]))

    @contextmanager
    def attribute_memo(self):
        """Serve MariaDB attribute reads in this block from one row fetch.

        Every MariaDB-backed attribute lives in the same
        ``instance_attributes`` row, but ``_db_get_attribute`` fetches
        that row per read, so a caller reading several attributes (an
        external view reads nine) issues that many identical
        ``GetInstanceAttributes`` RPCs. Inside this block the row is
        fetched at most once and reused.

        This is a per-call memo, not a cache: it is discarded when the
        block exits and invalidated by any attribute write inside it, so
        nothing outside the block observes different staleness. Reads
        inside the block share one row object, so a caller must not
        mutate a returned value in place and then re-read it.
        """
        self.__attribute_memo_depth += 1
        try:
            yield
        finally:
            self.__attribute_memo_depth = max(
                0, self.__attribute_memo_depth - 1)
            if not self.__attribute_memo_depth:
                self.__attribute_memo = None

    def _db_get_attribute(self, attribute, default=None):
        """Get an attribute, routing MariaDB-stored attributes appropriately."""
        if attribute in self.MARIADB_ATTRIBUTES:
            _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
            attrs = self.__attribute_memo
            if not attrs:
                attrs = mariadb.get_instance_attributes(_uuid)
                if not attrs:
                    attrs = InstanceAttributesData(uuid=_uuid)
                    mariadb.create_instance_attributes(attrs)
                if self.__attribute_memo_depth:
                    self.__attribute_memo = attrs

            # Map the attribute name to the model field
            field_name = attribute
            if attribute == 'error':
                field_name = 'error_message'

            val = getattr(attrs, field_name, None)

            # Handle special cases for compatibility with etcd format
            if attribute == 'kvm_pid':
                if val is not None:
                    return {'pid': val}
                return default if default is not None else {}
            if attribute == 'error':
                if val:
                    return {'message': val}
                return default if default is not None else {}
            if attribute == 'interfaces':
                return val if val else (default if default is not None else [])

            if val is not None:
                return val
            return default if default is not None else {}

        # Fall through to etcd for non-MariaDB attributes
        # (metadata, last_cluster_operation, vsock_cid:*, etc.)
        return super()._db_get_attribute(attribute, default)

    def _db_set_attribute(self, attribute, value):
        """Set an attribute, routing MariaDB-stored attributes appropriately."""
        if attribute in self.MARIADB_ATTRIBUTES:
            # Writing an attribute invalidates any memo of the row, both so
            # this read-modify-write starts from fresh data and so later
            # reads in an enclosing attribute_memo() block see the new value.
            self.__attribute_memo = None

            _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)
            attrs = mariadb.get_instance_attributes(_uuid)
            if not attrs:
                attrs = InstanceAttributesData(uuid=_uuid)
                mariadb.create_instance_attributes(attrs)

            # Map the attribute name to the model field
            if attribute == 'kvm_pid':
                field = 'kvm_pid'
                attrs.kvm_pid = value.get('pid') if isinstance(value, dict) else value
            elif attribute == 'error':
                field = 'error_message'
                attrs.error_message = value.get('message', '') if isinstance(value, dict) else str(value)
            elif attribute == 'agent_state':
                field = 'agent_state'
                if hasattr(value, 'model_dump'):
                    attrs.agent_state = value.model_dump()
                else:
                    attrs.agent_state = value
            else:
                field = attribute
                setattr(attrs, attribute, value)

            # Only write the column we are setting. Writing the whole
            # row here is a cross-attribute lost update: this method is
            # get-row, set-one-field, write-row, and two writers of
            # different attributes on different nodes (the sidechannel
            # monitor's agent state cache and the API's agent operation
            # enqueue) can interleave so the second write reverts the
            # first writer's committed column to the stale value it
            # read. Observed as an enqueued agent operation vanishing
            # from the queue while its state stayed 'queued'.
            mariadb.update_instance_attributes(attrs, fields=[field])
            self._log_attribute_mutation(attribute, value)
            return

        # Fall through to etcd for non-MariaDB attributes
        super()._db_set_attribute(attribute, value)

    @classmethod
    def new(cls, name=None, cpus=None, memory=None, namespace=None, ssh_key=None,
            disk_spec=None, user_data=None, video=None, requested_placement=None,
            instance_uuid=None, uefi=False, configdrive=None, nvram_template=None,
            secure_boot=False, machine_type='pc', side_channels=None):
        if not configdrive:
            configdrive = 'openstack-disk'

        # NOTE(mikal): we don't support creating older versions of objects here.
        # I am not sure if that makes sense (what do you do if you need to make
        # an older version but the caller requested a feature which requires the
        # newer version?), but will call it out for now as a gap.
        static_values = {
            'cpus': cpus,
            'disk_spec': disk_spec,
            'memory': memory,
            'name': name,
            'namespace': namespace,
            'requested_placement': requested_placement,
            'ssh_key': ssh_key,
            'user_data': user_data,
            'video': video,
            'uefi': uefi,
            'configdrive': configdrive,
            'nvram_template': nvram_template,
            'secure_boot': secure_boot,
            'machine_type': machine_type,
            'side_channels': side_channels,

            'version': cls.current_version
        }

        Instance._db_create(instance_uuid, static_values)
        static_values['uuid'] = instance_uuid
        i = Instance(static_values)
        i.state = cls.STATE_INITIAL
        i._db_set_attribute('power_state', {'power_state': cls.STATE_INITIAL})
        return i

    def external_view(self):
        # Building the view reads nine MariaDB-backed attributes, all of
        # which live in a single instance_attributes row. Memo the row for
        # the duration of the call so we make one database round trip
        # instead of nine.
        with self.attribute_memo():
            return self._build_external_view()

    def _build_external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        i = self._external_view()
        i.update({
            'cpus': self.cpus,
            'disk_spec': self.disk_spec,
            'memory': self.memory,
            'name': self.name,
            'namespace': self.namespace,
            'ssh_key': self.ssh_key,
            'user_data': self.user_data,
            'video': self.video,
            'uefi': self.uefi,
            'configdrive': self.configdrive,
            'nvram_template': self.nvram_template,
            'secure_boot': self.secure_boot,
            'machine_type': self.machine_type,
            'side_channels': self.side_channels,
            'agent_state': self.agent_state.value,
            'agent_start_time': self.agent_start_time,
            'agent_system_boot_time': self.agent_system_boot_time,
            'error_message': self.error,
            'last_cluster_operation': self.last_cluster_operation
        })

        if self.requested_placement:
            i['requested_placement'] = self.requested_placement

        external_attribute_key_whitelist = [
            'console_port',
            'node',
            'power_state',
            'vdi_port',
            'vdi_tls_port'
        ]

        # Ensure that missing attributes still get reported
        for attr in external_attribute_key_whitelist:
            i[attr] = None

        for attrname in ['placement', 'power_state', 'ports']:
            d = self._db_get_attribute(attrname)
            for key in d:
                if key not in external_attribute_key_whitelist:
                    continue

                # We skip keys with no value
                if d[key] is None:
                    continue

                i[key] = d[key]

        # Mix in details of the instance's interfaces to reduce API round trips
        # for clients.
        i['interfaces'] = [ni.external_view() for ni in self.interfaces]

        # Mix in details of the configured disks. We don't have all the details
        # in the block devices structure until _initialize_block_devices() is
        # called. If not yet configured, we just return None.
        i['disks'] = []
        for disk in self.block_devices.get('devices', []):
            i['disks'].append({
                'device': disk['device'],
                'bus': disk['bus'],
                'size': disk.get('size'),
                'blob_uuid': disk.get('blob_uuid'),
                'snapshot_ignores': disk.get('snapshot_ignores')
            })

        # Mix in not yet executed agent operations. If you want to see _all_
        # agent operations, then use the agentoperation REST API instead.
        i['agent_operations_queue'] = []
        ops = self.agent_operations
        for agentop_uuid in ops.get('queued', []):
            aop = AgentOperation.from_db(agentop_uuid)
            if aop:
                i['agent_operations_queue'].append(aop.external_view())

        # Add object references (what references this instance and what this
        # instance references)
        refs_to = mariadb.get_references_to(ObjectType.INSTANCE, self.uuid)
        refs_from = mariadb.get_references_from(ObjectType.INSTANCE, self.uuid)
        i['references_to'] = references_to_grouped_dict(refs_to)
        i['references_from'] = references_to_grouped_dict(refs_from)

        return i

    # Static values
    @property
    def cpus(self):
        return self.__cpus

    @property
    def disk_spec(self):
        return self.__disk_spec

    @property
    def memory(self):
        return self.__memory

    @property
    def name(self):
        return self.__name

    @property
    def namespace(self):
        return self.__namespace

    @property
    def requested_placement(self):
        return self.__requested_placement

    @property
    def ssh_key(self):
        return self.__ssh_key

    @property
    def user_data(self):
        return self.__user_data

    @property
    def video(self):
        return self.__video

    @property
    def uefi(self):
        return self.__uefi

    @property
    def configdrive(self):
        return self.__configdrive

    @property
    def nvram_template(self):
        return self.__nvram_template

    @property
    def secure_boot(self):
        return self.__secure_boot

    @property
    def machine_type(self):
        return self.__machine_type

    @property
    def side_channels(self):
        return self.__side_channels

    @property
    def instance_path(self):
        return os.path.join(config.STORAGE_PATH, 'instances', str(self.uuid))

    # Calculated properties
    @property
    def domain_xml_path(self):
        return os.path.join(self.instance_path, 'original_domain.xml')

    # Values routed to attributes, writes are via helper methods.
    @property
    def affinity(self):
        return self.metadata.get(self.METADATA_KEY_AFFINITY, {})

    @property
    def placement(self):
        return self._db_get_attribute('placement')

    @property
    def power_state(self):
        return self._db_get_attribute('power_state')

    @property
    def ports(self):
        return self._db_get_attribute('ports')

    @ports.setter
    def ports(self, ports):
        self._db_set_attribute('ports', ports)

    @property
    def enforced_deletes(self):
        return self._db_get_attribute('enforced_deletes')

    @property
    def block_devices(self):
        return self._db_get_attribute('block_devices')

    @property
    def interfaces(self):
        """Currently-attached NetworkInterface objects.

        Queried live from the network_interfaces table
        (instance_uuid is an indexed column). Previously
        cached as a list of UUID strings on
        instance_attributes; that column is dropped in phase 7e.
        """
        criteria = ObjectFilterCriteria(
            states=list(interface.NetworkInterface.ACTIVE_STATES),
            instance_uuid=str(self.uuid),
        )
        return [
            interface.NetworkInterface(
                interface.NetworkInterface._static_values_to_dict(d))
            for d in mariadb.find_network_interfaces(criteria)
        ]

    @property
    def tags(self):
        return self.metadata.get(self.METADATA_KEY_TAGS, None)

    @property
    def agent_state(self):
        db_data = self._db_get_attribute('agent_state')
        if not db_data:
            return baseobject.State(value=None, update_time=0)
        return baseobject.State(**db_data)

    @agent_state.setter
    def agent_state(self, new_value):
        orig = self.agent_state
        if orig.value == new_value:
            return

        new_state = baseobject.State(value=new_value, update_time=time.time())
        self._db_set_attribute('agent_state', new_state)

    @property
    def agent_start_time(self):
        db_data = self._db_get_attribute('agent_attributes')
        return db_data.get('start_time')

    @agent_start_time.setter
    def agent_start_time(self, new_value):
        with self.get_lock_attr('agent_attributes', 'Update agent attributes'):
            db_data = self._db_get_attribute('agent_attributes')
            db_data['start_time'] = new_value
            self._db_set_attribute('agent_attributes', db_data)

    @property
    def agent_system_boot_time(self):
        db_data = self._db_get_attribute('agent_attributes')
        return db_data.get('system_boot_time')

    @agent_system_boot_time.setter
    def agent_system_boot_time(self, new_value):
        with self.get_lock_attr('agent_attributes', 'Update agent attributes'):
            db_data = self._db_get_attribute('agent_attributes')
            db_data['system_boot_time'] = new_value
            self._db_set_attribute('agent_attributes', db_data)

    @property
    def agent_facts(self):
        db_data = self._db_get_attribute('agent_attributes')
        return db_data.get('facts')

    @agent_facts.setter
    def agent_facts(self, new_value):
        with self.get_lock_attr('agent_attributes', 'Update agent facts'):
            db_data = self._db_get_attribute('agent_attributes')
            db_data['facts'] = new_value
            self._db_set_attribute('agent_attributes', db_data)

    @property
    def kvm_pid(self):
        return self._db_get_attribute('kvm_pid').get('pid')

    @kvm_pid.setter
    def kvm_pid(self, pid):
        if self.kvm_pid == pid:
            return
        self._db_set_attribute('kvm_pid', {'pid': pid})

    def vsock_cid(self, channel):
        cids = self._db_get_attribute('vsock_cids')
        if not cids:
            return None
        return cids.get(channel)

    def set_vsock_cid(self, channel, cid):
        with self.get_lock_attr('vsock_cids', 'Set vsock CID'):
            cids = self._db_get_attribute('vsock_cids') or {}
            cids[channel] = cid
            self._db_set_attribute('vsock_cids', cids)

    # Implementation
    def _initialize_block_devices(self):
        bus = _get_defaulted_disk_bus(self.disk_spec[0])
        root_device = _get_disk_device(bus, 0)
        config_device = _get_disk_device(bus, 1)

        disk_type = 'qcow2'

        blob_uuid = self.disk_spec[0].get('blob_uuid')
        block_devices = {
            'devices': [
                {
                    'type': disk_type,
                    'size': _safe_int_cast(self.disk_spec[0].get('size')),
                    'device': root_device,
                    'bus': bus,
                    'path': os.path.join(self.instance_path, root_device),
                    'base': self.disk_spec[0].get('base'),
                    'blob_uuid': blob_uuid,
                    'present_as': _get_defaulted_disk_type(self.disk_spec[0]),
                    'snapshot_ignores': False,
                    'cache_mode': constants.DISK_CACHE_MODE
                }
            ],
            'extracommands': []
        }

        i = 1
        if self.configdrive == 'openstack-disk':
            block_devices['devices'].append(
                {
                    'type': 'raw',
                    'device': config_device,
                    'bus': bus,
                    'path': os.path.join(self.instance_path, config_device),
                    'present_as': 'disk',
                    'snapshot_ignores': True,
                    'cache_mode': constants.DISK_CACHE_MODE,
                    'is_configdrive': True
                }
            )
            i += 1

        for d in self.disk_spec[1:]:
            bus = _get_defaulted_disk_bus(d)
            device = _get_disk_device(bus, i)
            disk_path = os.path.join(self.instance_path, device)
            blob_uuid = d.get('blob_uuid')

            block_devices['devices'].append({
                'type': disk_type,
                'size': _safe_int_cast(d.get('size')),
                'device': device,
                'bus': bus,
                'path': disk_path,
                'base': d.get('base'),
                'blob_uuid': blob_uuid,
                'present_as': _get_defaulted_disk_type(d),
                'snapshot_ignores': False,
                'cache_mode': constants.DISK_CACHE_MODE
            })

            i += 1

        # NVME disks require a different treatment because libvirt doesn't natively
        # support them yet. werror/rerror=stop are the qemu-commandline equivalent
        # of the error_policy/rerror_policy='stop' we set on libvirt-managed disks
        # in libvirt.tmpl: without them an NVME-bus disk would fall back to qemu's
        # default 'report' policy and pass a backing-store I/O error to the guest
        # while the domain kept running, which is exactly the failure the disk
        # error policy exists to make visible (the domain pauses instead).
        nvme_counter = 0
        for d in block_devices['devices']:
            if d['bus'] == 'nvme':
                nvme_counter += 1
                block_devices['extracommands'].extend([
                    '-drive',
                    f'file={d["path"]},format={d["type"]},if=none,'
                    f'id=NVME{nvme_counter},werror=stop,rerror=stop',
                    '-device', f'nvme,drive=NVME{nvme_counter},serial=nvme-{nvme_counter}'
                ])

        nvram_template = self.nvram_template

        block_devices['finalized'] = False

        # Record blob references in the object_references table
        # Each disk gets its own reference with the disk index as the value
        for disk_idx, disk in enumerate(self.disk_spec):
            disk_blob_uuid = disk.get('blob_uuid')
            if disk_blob_uuid:
                disk_blob = blob.Blob.from_db(disk_blob_uuid)
                if disk_blob:
                    disk_blob.add_disk_reference(self.uuid, disk_idx)

        # Record nvram_template reference if present
        if nvram_template:
            nvram_blob = blob.Blob.from_db(nvram_template)
            if nvram_blob:
                nvram_blob.add_nvram_template_reference(self.uuid)

        return block_devices

    def _record_domain_xml(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if inst:
                xml_desc = inst.XMLDesc(0)
                self.add_event(
                    EVENT_TYPE_MUTATE, 'libvirt domain XML',
                    extra={
                        'xml': xml_desc
                    })
            else:
                self.add_event(
                    EVENT_TYPE_STATUS, 'libvirt reports domain undefined')

    @property
    def _capacity_claim(self):
        """This instance's resource claim, as the capacity ledger counts it.

        The three dimensions come from static values, so they are stable
        for the life of the instance and readable right up until
        hard_delete() removes the row. Disk is the summed *virtual* size
        of the disk spec, computed by the reconciler's own reference
        implementation so acquire and release cannot drift from the
        counters the reconciler recomputes from ground truth.
        """
        return (self.cpus, self.memory,
                mariadb.disk_spec_virtual_gb(self.disk_spec))

    def _admit_placement(self, location, old_location, placement, enforce,
                         enforce_demand):
        """Draw down capacity for a placement and write it, atomically.

        One RPC performs the guarded counter updates, the placement
        attribute write and the placement reference rewrite in a single
        database transaction (D1). Returns the reply so the caller can
        emit events from it.

        When ``enforce`` is False the guard must not refuse -- these are
        the ground-truth writers recording where a libvirt domain
        already is (P5) -- but P5 also wants a loud event when such a
        write pushes a node past its limits, and the reply of a
        non-enforced admission cannot say whether it did: it carries the
        post-admit counters but not the limits they were compared
        against. So the guarded call is made first and used as the
        probe. If it admits, the placement is recorded and within
        limits, at the cost of no extra RPC. If it is denied, nothing
        was written (the denial rolls its transaction back), the denial
        detail names the dimensions which would have been exceeded, and
        the placement is then recorded unguarded. The over-limit event is
        emitted only once that unguarded write has actually succeeded --
        an event claiming a placement was recorded, followed by a failed
        recording, would be an audit trail which lies.

        The probe waives the D13 demand clause: demand is a spreader,
        never a capacity bound (P9), so a demand-only refusal is not an
        over-limit condition and must not emit the event -- on the small
        or busy clusters where demand runs hot, it would fire on routine
        ground-truth writes and cost each one a second RPC. (When
        ``enforce`` is True this same call is the admission rather than
        a probe, and ``enforce_demand`` is honoured as passed.)
        """
        cpus, memory_mb, disk_gb = self._capacity_claim
        # NOTE(mikal): the RPC writes the placement column itself, so the
        # caller has to hand it the same bytes the generic attribute
        # write path would have stored -- hence mariadb's own serializer
        # rather than util_json's (which indents and sorts).
        placement_json = mariadb.json_dumps(placement)

        result = mariadb.admit_instance_placement(
            str(self.uuid), self.namespace, location, cpus, memory_mb,
            disk_gb, placement_json, old_node_uuid=old_location,
            enforce=True,
            enforce_demand=(enforce_demand and enforce))

        if not enforce and result['success'] and not result['admitted']:
            denial = result
            result = mariadb.admit_instance_placement(
                str(self.uuid), self.namespace, location, cpus, memory_mb,
                disk_gb, placement_json, old_node_uuid=old_location,
                enforce=False, enforce_demand=enforce_demand)
            if result['success']:
                self._event_admission_over_limit(location, denial)

        # D16's advisory claim accounting, deliberately read from
        # ``result`` *after* the rebinding above so it comes from
        # whichever reply actually recorded the placement -- which is the
        # same reasoning the P5 event's placement documents. On the
        # probe-then-force path the probe's denial rolled its transaction
        # back, so it charged no claim and can carry no exceedance;
        # eventing from it would announce a claim drawdown that was
        # undone. And ``admitted`` gates both replies, because a
        # placement which was not recorded consumed nothing. This is not
        # conditional on ``enforce``: a ground-truth write into an
        # over-claim namespace is exactly as interesting as a scheduled
        # one, and both reach here.
        if result['admitted'] and result['claim_over_limit']:
            self._event_claim_over_limit(location, result)

        return result

    def _event_admission_over_limit(self, location, result):
        """Record that a ground-truth placement write exceeded a guard (P5)."""
        self.log.with_fields({
            'node': location,
            'failing_stage': result['failing_stage'],
            'dimensions': result['dimensions']}).warning(
                'Recording a placement which exceeds the capacity guard')
        self.add_event(
            EVENT_TYPE_AUDIT,
            'placement recorded despite exceeding capacity guard',
            extra={
                'node': location,
                'failing_stage': result['failing_stage'],
                'dimensions': result['dimensions']
            }, log_as_error=True)

    def _event_claim_over_limit(self, location, result):
        """Record that a placement drew a namespace past its claim (D16).

        Distinct from _event_admission_over_limit() in every respect that
        matters to a reader: that one says a *ground-truth* write was
        forced past a *node's* capacity guard, this one says a placement
        was accounted against its *namespace's* claim and that claim is
        now over the limits the namespace declared. The messages and the
        ``extra`` keys differ so a log consumer never has to guess which
        happened.

        Deliberately not ``log_as_error``: advisory mode did exactly what
        the operator asked for when this fires. CLAIM_ENFORCEMENT_HARD is
        False for one release precisely so exceedances are *observed*
        before they are refused, so this is a warning -- loud enough to
        find, and to calibrate a claim against, but not a failure. Phase
        5 turns the same condition into a refusal, which will be an error
        because then the create does not happen.
        """
        self.log.with_fields({
            'node': location,
            'namespace': self.namespace,
            'claim_dimensions': result['claim_dimensions']}).warning(
                'Placement admitted over the namespace capacity claim')
        self.add_event(
            EVENT_TYPE_AUDIT,
            'placement admitted over namespace capacity claim',
            extra={
                'node': location,
                'namespace': self.namespace,
                'claim_dimensions': result['claim_dimensions']
            })

    def place_instance(self, location, enforce=True, enforce_demand=True):
        """Place this instance on a node, claiming its capacity to do so.

        The placement attribute, the capacity counters and the
        INSTANCE_LOCATION reference rows are written by a single
        database transaction (D1), so a placement can no longer be
        recorded without the capacity it consumes, and two concurrent
        placements cannot both take the last slot on a node.

        ``enforce`` (P5) is True for the scheduler-driven callers, where
        a denial is a genuine reschedule and raises
        CapacityAdmissionDenied so the caller can walk to its next
        candidate. It is False for the ground-truth writers -- the
        cleaner and the startup reconciliation -- which record where a
        libvirt domain already is: a guard cannot refuse reality, and
        refusing to record it would leave the ledger wrong, which is
        strictly worse. Those writers get a loud event instead.

        ``enforce_demand=False`` waives only the D13 demand feedforward
        clause, keeping every real capacity dimension guarded. It is for
        a walker's second pass after a first walk admitted nowhere and
        was refused somewhere on demand alone: demand exists to spread
        bursts across nodes, and when there is no quieter node to
        spread to it must not refuse a cluster whose real capacity is
        free (the smoke CI single-node lockout of 2026-08-14).
        """
        with self.get_lock_attr('placement', 'Instance placement'):
            # We don't write unchanged things to the database
            #
            # _db_get_attribute() can return the dict an enclosing
            # attribute_memo() block is caching, so the proposed
            # placement is built on a copy. Mutating in place would let a
            # denial -- which writes nothing -- still leave that memo (and
            # anything else holding the same dict) reading the refused
            # node with a bumped attempt count.
            placement = copy.deepcopy(self.placement)
            old_location = placement.get('node')
            if old_location == location:
                # This early-out means place_instance() is not a repair
                # path: an instance whose attribute already names this
                # node but which is missing its INSTANCE_LOCATION row
                # (and so its capacity charge) is left as-is here. The
                # queues daemon's restore_instances() owns that repair,
                # via _reconcile_placement() precisely because it cannot
                # go through this method.
                return

            placement['node'] = location
            placement['placement_attempts'] = placement.get(
                'placement_attempts', 0) + 1

            result = self._admit_placement(
                location, old_location or '', placement, enforce,
                enforce_demand)

            if not result['success']:
                # The database could not be reached, or refused the
                # write for a reason which is not a capacity denial.
                # This is not "the cluster is full", so it must not read
                # as one to a caller walking candidate nodes.
                self.log.with_fields({
                    'node': location,
                    'error': result['error']}).error(
                        'Instance placement write failed')
                self.add_event(
                    EVENT_TYPE_AUDIT, 'instance placement write failed',
                    extra={'node': location, 'error': result['error']},
                    log_as_error=True)
                if enforce:
                    raise exceptions.WriteException(
                        f'could not place instance {self.uuid} on '
                        f'{location}: {result["error"]}')
                return

            if not result['admitted']:
                self.add_event(
                    EVENT_TYPE_AUDIT, 'instance placement denied',
                    extra={
                        'node': location,
                        'failing_stage': result['failing_stage'],
                        'dimensions': result['dimensions'],
                        'enforce': enforce
                    })
                if not enforce:
                    # _admit_placement() retries a denial unguarded, so
                    # the only way here is the retry's own key-only
                    # UPDATE matching nothing: the capacity row vanished
                    # between the probe and the write (the reconciler
                    # dropped a node which stopped being a schedulable
                    # hypervisor mid-pass). That is a benign abort, not
                    # a capacity denial, and a ground-truth writer has
                    # no candidate to walk to -- raising would abort the
                    # rest of the caller's pass. Nothing was written;
                    # the next cleaner pass retries.
                    return
                raise exceptions.CapacityAdmissionDenied(
                    result['failing_stage'], result['dimensions'])

            # The RPC wrote the placement column behind this object's
            # back, so do what _db_set_attribute() would have done
            # around that write: drop any memo of the attributes row so
            # an enclosing attribute_memo() block cannot keep serving
            # the pre-placement value, and log the mutation event.
            self.__attribute_memo = None
            self._log_attribute_mutation('placement', placement)

            self.add_event(
                EVENT_TYPE_AUDIT, 'instance placed',
                extra={
                    'node': location,
                    'previous_node': old_location,
                    'placement_attempts': placement['placement_attempts'],
                    'enforce': enforce,
                    'enforce_demand': enforce_demand,
                    'cpus': self.cpus,
                    'memory_mb': self.memory,
                    'node_used_cpus': result['node_used_cpus'],
                    'node_used_memory_mb': result['node_used_memory_mb'],
                    'node_used_disk_gb': result['node_used_disk_gb'],
                    'node_expected_demand': result['node_expected_demand']
                })

            if result['unguarded']:
                # P7: a node whose capacity row the reconciler has not
                # created yet admits without a guard rather than
                # refusing every create mid-upgrade.
                self.log.with_fields({'node': location}).warning(
                    'Instance placed without a capacity guard')
                self.add_event(
                    EVENT_TYPE_AUDIT, 'instance placed without capacity guard',
                    extra={'node': location}, log_as_error=True)

            if result['clamped']:
                # A counter would have gone negative releasing the old
                # node's share, which means the ledger and ground truth
                # had already diverged. The reconciler repairs it.
                self.log.with_fields({
                    'node': location,
                    'previous_node': old_location}).warning(
                        'Capacity counter clamped at zero during placement')
                self.add_event(
                    EVENT_TYPE_AUDIT, 'capacity counter clamped at zero',
                    extra={'node': location, 'previous_node': old_location},
                    log_as_error=True)

    def enqueue_disk_fetches(self, target_node, priority, request_id=None,
                             artifact_event=None):
        """Enqueue an artifact fetch onto target_node for each disk image.

        Instance create assumes the image for every disk is already in the
        node-local image cache, so each placement decision must be paired
        with fetches targeting the chosen node. Returns the enqueued
        operations as a dependency list for the subsequent start operation.
        """
        # TODO(mikal): I would really like the target_node not to be set
        # here so that any node in the cluster could start downloading
        # this image ASAP. Unfortunately, image download is also comingled
        # with populating the local image cache for instance start at the
        # moment and I need to tease that apart first.
        fetch_dependencies = []
        for disk in self.disk_spec:
            disk_base = disk.get('base')
            if disk.get('blob_uuid'):
                url = f'{artifact.BLOB_URL}{disk["blob_uuid"]}'
            elif not util_general.noneish(disk_base):
                url = disk_base
            else:
                # Empty disk with no base image, no artifact fetch needed
                continue

            # By ownership, because the fetch this enqueues ends in
            # add_index: url is either a blob we already resolved, or a URL
            # this instance's namespace was entitled to fetch.
            a = artifact.Artifact.owned_from_url_or_new(
                artifact.Artifact.TYPE_IMAGE, url, namespace=self.namespace)
            if artifact_event:
                a.add_event(EVENT_TYPE_AUDIT, artifact_event)

            op_type, op_uuid = afo_create_and_enqueue(
                self.namespace, url, self.uuid, [afo_tasks.image_fetch],
                priority, artifact_uuid=a.uuid, request_id=request_id,
                target_node=target_node)
            fetch_dependencies.append(
                dependency(op_type=op_type, op_uuid=op_uuid))

        return fetch_dependencies

    def enforced_deletes_increment(self):
        with self.get_lock_attr('enforced_deletes',
                                'Instance enforced deletes increment'):
            enforced_deletes = self.enforced_deletes
            enforced_deletes['count'] = enforced_deletes.get('count', 0) + 1
            self._db_set_attribute('enforced_deletes', enforced_deletes)
            return enforced_deletes['count']

    def update_power_state(self, state):
        with self.get_lock_attr('power_state', 'Instance power state update'):
            # We don't write unchanged things to the database
            dbstate = self.power_state
            if dbstate.get('power_state') == state:
                return False

            dbstate['power_state_previous'] = dbstate.get('power_state')
            dbstate['power_state'] = state
            dbstate['power_state_updated'] = time.time()
            self._db_set_attribute('power_state', dbstate)
            return True

    # NOTE(mikal): this method is now strictly the instance specific steps for
    # creation. It is assumed that the image sits in local cache already, and
    # has been transcoded to the right format. This has been done to facilitate
    # moving to a queue and task based creation mechanism.
    def create(self):
        self.state = self.STATE_CREATING

        # Ensure we have state on disk
        os.makedirs(self.instance_path, exist_ok=True)

        # Configure block devices, not including config drive creation which is
        # done in power_on().
        self._configure_block_devices()

        self.power_on()
        self._record_domain_xml()

        if self.is_powered_on():
            self.state = self.STATE_CREATED
        else:
            self.add_event(EVENT_TYPE_AUDIT, 'instance failed to power on')
            self.enqueue_delete_due_error('instance failed to power on')

    def _delete_on_hypervisor(self):
        if config.ARCHIVE_INSTANCE_CONSOLE_DURATION > 0:
            self.archive_console_log()

        for disk in self.block_devices.get('devices', []):
            if 'blob_uuid' in disk and disk['blob_uuid']:
                # Mark files we used in the image cache as recently used so that
                # they linger a little for possible future users.
                cached_image_path = util_general.file_permutation_exists(
                    os.path.join(config.STORAGE_PATH,
                                 'image_cache', disk['blob_uuid']),
                    ['iso', 'qcow2'])
                if cached_image_path:
                    pathlib.Path(cached_image_path).touch(exist_ok=True)

        try:
            self.power_off()

            nvram_path = os.path.join(self.instance_path, 'nvram')
            if os.path.exists(nvram_path):
                os.unlink(nvram_path)

            with util_libvirt.LibvirtConnection() as lc:
                inst = lc.get_domain_from_sf_uuid(self.uuid)
                if inst:
                    inst.undefine()
        except Exception as e:
            util_exceptions.ignore_exception(
                f'instance delete domain {self}', e)

        with util_general.RecordedOperation('delete disks', self):
            try:
                if os.path.exists(self.instance_path):
                    shutil.rmtree(self.instance_path)
            except Exception as e:
                util_exceptions.ignore_exception(
                    f'instance delete disks {self}', e)

    def _delete_globally(self):
        # Remove all blob references from this instance
        mariadb.remove_all_references_from(ObjectType.INSTANCE, self.uuid)

        self.deallocate_instance_ports()

        # Give this instance's capacity back and drop its placement
        # references (P6). This is where the reconciler's ground truth
        # stops counting the instance -- it excludes instances in state
        # deleted, which is set at the end of this method -- so the
        # explicit decrement belongs here rather than at hard delete.
        # An instance which never placed has nothing to release.
        placement_node = self.placement.get('node')
        if placement_node:
            self._release_placement(placement_node)

        # Find any agent operations for this instance and remove them
        for agentop in AgentOperations(
                [partial(agent_instance_filter, self)],
                suppress_failure_audit=True):
            agentop.delete()

        if self.state.value.endswith(f'-{self.STATE_ERROR}'):
            self.state = self.STATE_ERROR
        else:
            self.state = self.STATE_DELETED

    def delete(self, global_only=False):
        self._delete_on_hypervisor()
        self._delete_globally()

    def _release_placement(self, node_uuid=''):
        """Return this instance's capacity and drop its placement rows (P6).

        An empty ``node_uuid`` releases wherever the instance's
        placement references point, which is what the sweep in
        hard_delete() wants: it knows the instance rather than its node.
        A named node *filters* those references rather than replacing
        them, so a repeat call is a no-op in either form -- which matters
        here, because the node name comes from the ``placement``
        attribute, which is never cleared (P8), and an instance which
        ends in state ``error`` passes delete()'s re-entrancy guard on
        every subsequent delete attempt.
        """
        cpus, memory_mb, disk_gb = self._capacity_claim
        result = mariadb.release_instance_placement(
            str(self.uuid), self.namespace, cpus, memory_mb, disk_gb,
            node_uuid=node_uuid)

        if not result['success']:
            self.log.with_fields({
                'node': node_uuid,
                'error': result['error']}).error(
                    'Instance placement release failed')
            self.add_event(
                EVENT_TYPE_AUDIT, 'instance placement release failed',
                extra={'node': node_uuid, 'error': result['error']},
                log_as_error=True)
            return result

        if result['clamped']:
            # A counter would have gone negative, so the ledger and
            # ground truth had already diverged. The reconciler repairs
            # it within a pass; record that it happened.
            self.log.with_fields({'node': node_uuid}).warning(
                'Capacity counter clamped at zero during placement release')
            self.add_event(
                EVENT_TYPE_AUDIT, 'capacity counter clamped at zero',
                extra={'node': node_uuid, 'released': result['released']},
                log_as_error=True)

        if result['released']:
            self.add_event(
                EVENT_TYPE_AUDIT, 'instance placement released',
                extra={'node': node_uuid})

        return result

    def hard_delete(self):
        _uuid = self.uuid if isinstance(self.uuid, UUID) else UUID(self.uuid)

        # Release any capacity this instance still holds and remove any
        # INSTANCE_LOCATION references targeting it. _delete_globally()
        # releases on the normal path, but this backstop covers the
        # cases it can miss (node row gone, placement lost, or a partial
        # earlier delete) so a hard-deleted instance can never linger in
        # a node's instance list. It runs before the attribute and
        # static rows are deleted because the release needs this
        # instance's cpus, memory and disk spec to know how much to give
        # back. A double release is a no-op: with no reference rows left
        # there is nothing to release.
        self._release_placement()

        mariadb.delete_instance_attributes(_uuid)
        mariadb.delete_instance(_uuid)
        super().hard_delete()

    def _allocate_console_port(self):
        consumed = mariadb.get_consumed_ports_for_node(
            config.NODE_UUID)
        while True:
            port = random.randint(30000, 50000)
            if port in consumed:
                continue
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # Bind to verify the port is available locally.
                # This prevents races between concurrent allocations
                # on the same node.
                s.bind(('0.0.0.0', port))
                return port
            except OSError:
                LOG.with_fields({'instance': self.uuid}).info(
                    f'Collided with in use port {port}, selecting another')
                consumed.append(port)
            finally:
                s.close()

    def allocate_instance_ports(self):
        with self.get_lock_attr('ports', 'Instance port allocation'):
            p = self.ports
            if not p:
                p = {
                    'console_port': self._allocate_console_port(),
                    'vdi_port': self._allocate_console_port()
                }
                if self.video['vdi'].startswith('spice'):
                    p['vdi_tls_port'] = self._allocate_console_port()

                self.ports = p

    def deallocate_instance_ports(self):
        self._db_set_attribute('ports', None)

    def _configure_block_devices(self):
        with self.get_lock_attr(
                'block_devices', 'Initialize block devices'):
            # Create block devices if required
            block_devices = self.block_devices
            if not block_devices:
                block_devices = self._initialize_block_devices()

            # Create an empty config drive file as a place holder here until we
            # get to power on
            if self.configdrive == 'openstack-disk':
                disk_path = os.path.join(self.instance_path,
                                         block_devices['devices'][1]['path'])
                pathlib.Path(disk_path).touch(exist_ok=True)

            # Prepare disks. A this point we have a file for each blob in the image
            # cache at a well known location (the blob uuid with .qcow2 appended).
            if not block_devices['finalized']:
                modified_disks = []
                for disk in block_devices['devices']:
                    disk['source'] = "<source file='%s'/>" % disk['path']
                    disk['source_type'] = 'file'

                    # All disk bases must have an associated blob, force that
                    # if an image had to be fetched from outside the cluster.
                    disk_base = None
                    if disk.get('blob_uuid'):
                        disk_base = f'{artifact.BLOB_URL}{disk["blob_uuid"]}'
                    elif disk.get('base') and not util_general.noneish(disk.get('base')):
                        a = artifact.Artifact.from_url(
                            artifact.Artifact.TYPE_IMAGE, disk['base'],
                            namespace=self.namespace, create_if_new=True)
                        mri = a.most_recent_index

                        if 'blob_uuid' not in mri:
                            raise exceptions.ArtifactHasNoBlobs(
                                f'Artifact {a.uuid} of type {a.artifact_type} '
                                'has no versions')

                        disk['blob_uuid'] = mri['blob_uuid']
                        disk_base = f'{artifact.BLOB_URL}{disk["blob_uuid"]}'

                    if disk_base:
                        cached_image_path = util_general.file_permutation_exists(
                            os.path.join(config.STORAGE_PATH, 'image_cache',
                                         disk['blob_uuid']),
                            ['iso', 'qcow2'])
                        if not cached_image_path:
                            raise exceptions.ImageMissingFromCache(
                                f'Image {disk["blob_uuid"]} is missing')

                        try:
                            cd = pycdlib.PyCdlib()
                            cd.open(cached_image_path)
                            disk['present_as'] = 'cdrom'
                        except Exception:
                            pass

                        if disk.get('present_as', 'cdrom') == 'cdrom':
                            # There is no point in resizing or COW'ing a cdrom
                            disk['path'] = disk['path'].replace(
                                '.qcow2', '.raw')
                            disk['type'] = 'raw'
                            disk['snapshot_ignores'] = True
                            util_general.link(cached_image_path, disk['path'])

                            # qemu does not support removable media on virtio buses. It also
                            # only supports one IDE bus. This is quite limiting. Instead, we
                            # use USB for cdrom drives, unless you've specified a bus other
                            # than virtio in the creation request.
                            if disk['bus'] == 'virtio':
                                disk['bus'] = 'usb'
                                disk['device'] = _get_disk_device(
                                    disk['bus'], LETTERS.find(disk['device'][-1]))

                        elif disk['bus'] == 'nvme':
                            # NVMe disks do not currently support a COW layer for the instance
                            # disk. This is because we don't have a libvirt <disk/> element for
                            # them and therefore can't specify their backing store. Instead we
                            # produce a flat layer here.
                            util_image.create_qcow2(
                                cached_image_path, disk['path'], disk_size=disk['size'])

                        else:
                            with util_general.RecordedOperation('create copy on write layer', self):
                                util_image.create_cow(
                                    cached_image_path, disk['path'], disk['size'])
                            self.log.with_fields(util_general.stat_log_fields(disk['path'])).info(
                                f'COW layer {disk["path"]} created')

                            # Record the backing store for modern libvirt. This requires
                            # walking the chain of dependencies. Backing chains only work
                            # for qcow2 images. The backing image should already have been
                            # transcoded as part of the image fetch process.
                            backing_chain = []
                            backing_uuid = disk['blob_uuid']
                            while backing_uuid:
                                self.log.with_fields(disk).with_fields({
                                    'backing_uuid': backing_uuid
                                }).info('traversing backing blob')
                                backing_path = os.path.join(
                                    config.STORAGE_PATH, 'image_cache',
                                    f'{backing_uuid}.qcow2')
                                backing_chain.append(backing_path)
                                backing_blob = blob.Blob.from_db(backing_uuid)
                                if not backing_blob:
                                    raise exceptions.BlobMissing(
                                        f'Backing blob {backing_uuid} is missing')
                                backing_uuid = backing_blob.depends_on

                            indent = '      '
                            disk['backing'] = ''
                            backing_chain.reverse()
                            self.log.with_fields(disk).with_fields({
                                    'backing_chain': backing_chain
                                }).info('resolved backing chain')

                            for backing_path in backing_chain:
                                chain = disk['backing']
                                disk['backing'] = (
                                    f'{indent}<backingStore type="file">\n'
                                    f'{indent}  <format type="qcow2"/>\n'
                                    f'{indent}  <source file="{backing_path}"/>\n'
                                    f'{indent}  {chain}\n'
                                    f'{indent}</backingStore>\n'
                                )
                                indent += '  '

                            disk['backing'] = disk['backing'].lstrip()

                    elif not os.path.exists(disk['path']):
                        util_image.create_blank(disk['path'], disk['size'])

                    # qemu does not support removable media on virtio buses.
                    # This check handles empty CDROMs (no base image). CDROMs
                    # with base images are handled earlier in the disk_base
                    # block.
                    if (disk.get('present_as') == 'cdrom'
                            and disk['bus'] == 'virtio'):
                        disk['bus'] = 'usb'
                        disk['device'] = _get_disk_device(
                            disk['bus'], LETTERS.find(disk['device'][-1]))

                    shutil.chown(disk['path'], 'libvirt-qemu', 'libvirt-qemu')
                    modified_disks.append(disk)

                block_devices['devices'] = modified_disks
                block_devices['finalized'] = True
                self._db_set_attribute('block_devices', block_devices)

    def _make_config_drive_openstack_disk(self, disk_path):
        """Create a config drive"""

        # NOTE(mikal): with a big nod at https://gist.github.com/pshchelo/378f3c4e7d18441878b9652e9478233f
        iso = pycdlib.PyCdlib()
        iso.new(interchange_level=4,
                joliet=True,
                rock_ridge='1.09',
                vol_ident='config-2')

        # We're only going to pretend to do the most recent OpenStack version
        iso.add_directory('/openstack',
                          rr_name='openstack',
                          joliet_path='/openstack')
        iso.add_directory('/openstack/2017-02-22',
                          rr_name='2017-02-22',
                          joliet_path='/openstack/2017-02-22')
        iso.add_directory('/openstack/latest',
                          rr_name='latest',
                          joliet_path='/openstack/latest')

        # meta_data.json -- note that limits on hostname are imposed at the API layer
        md = json.dumps({
            'random_seed': base64.b64encode(os.urandom(512)).decode('ascii'),
            'uuid': str(self.uuid),
            'availability_zone': config.ZONE,
            'hostname': f'{self.name}.local',
            'launch_index': 0,
            'devices': [],
            'project_id': None,
            'name': self.name,
            'public_keys': {
                'mykey': self.ssh_key
            }
        }).encode('ascii')
        iso.add_fp(io.BytesIO(md), len(md), '/openstack/latest/meta_data.json;1',
                   rr_name='meta_data.json',
                   joliet_path='/openstack/latest/meta_data.json')
        iso.add_fp(io.BytesIO(md), len(md), '/openstack/2017-02-22/meta_data.json;2',
                   rr_name='meta_data.json',
                   joliet_path='/openstack/2017-02-22/meta_data.json')

        # user_data: we used to only write this if there was some user data
        # specified, but that reports a schema error with cloud-init like this:
        #
        # Cloud config schema errors: format-l1.c1: File None needs to begin
        # with "#cloud-config"
        if self.user_data:
            user_data = base64.b64decode(self.user_data)
        else:
            user_data = b'#cloud-config\n'

        iso.add_fp(io.BytesIO(user_data), len(user_data), '/openstack/latest/user_data',
                   rr_name='user_data',
                   joliet_path='/openstack/latest/user_data.json')
        iso.add_fp(io.BytesIO(user_data), len(user_data), '/openstack/2017-02-22/user_data',
                   rr_name='user_data',
                   joliet_path='/openstack/2017-02-22/user_data.json')

        # network_data.json
        nd = {
            'links': [],
            'networks': [],
            'services': []
        }

        detected_dns_servers = []
        have_default_route = False
        for iface in self.interfaces:
            # Interfaces without an IPv4 address contribute nothing to
            # network_data.json: they have no link metadata to publish, no
            # route to install, and no DNS resolver to advertise. Skip them
            # so the default-route and DNS branches below can safely assume
            # ``n`` is bound and ``nd['networks']`` is non-empty.
            if not iface.ipv4:
                continue

            devname = f'eth{iface.order}'
            nd['links'].append(
                {
                    'ethernet_mac_address': iface.macaddr,
                    'id': devname,
                    'name': devname,
                    'mtu': config.MAX_HYPERVISOR_MTU - 50,
                    'type': 'vif',
                    'vif_id': str(iface.uuid)
                }
            )

            n = network.Network.from_db(iface.network_uuid)
            nd['networks'].append(
                {
                    'id': f'{iface.network_uuid}-{iface.order}',
                    'link': devname,
                    'type': 'ipv4',
                    'network_id': str(iface.network_uuid)
                }
            )

            nd['networks'][-1].update({
                'ip_address': iface.ipv4,
                'netmask': str(n.netmask),
            })

            # NOTE(mikal): it is assumed that the default route should be on
            # the first interface specified that has an IPv4 address.
            if not have_default_route:
                nd['networks'][-1].update({
                    'routes': [
                        {
                            'network': '0.0.0.0',
                            'netmask': '0.0.0.0',
                            'gateway': str(n.router)
                        }
                    ]
                })
                have_default_route = True

            # Do we have a DNS server?
            router_as_string = str(n.router)
            if n.provide_dns and router_as_string not in detected_dns_servers:
                nd['services'].append({
                    'address': router_as_string,
                    'type': 'dns',
                    'search': [f'{self.namespace}.{config.ZONE}']
                })
                detected_dns_servers.append(router_as_string)

        if not detected_dns_servers:
            nd['services'].append({
                'address': config.DNS_SERVER,
                'type': 'dns'
            })

        nd_encoded = json.dumps(nd).encode('ascii')
        iso.add_fp(io.BytesIO(nd_encoded), len(nd_encoded),
                   '/openstack/latest/network_data.json;3',
                   rr_name='network_data.json',
                   joliet_path='/openstack/latest/vendor_data.json')
        iso.add_fp(io.BytesIO(nd_encoded), len(nd_encoded),
                   '/openstack/2017-02-22/network_data.json;4',
                   rr_name='network_data.json',
                   joliet_path='/openstack/2017-02-22/vendor_data.json')

        # empty vendor_data.json and vendor_data2.json
        vd = b'{}'
        iso.add_fp(io.BytesIO(vd), len(vd),
                   '/openstack/latest/vendor_data.json;5',
                   rr_name='vendor_data.json',
                   joliet_path='/openstack/latest/vendor_data.json')
        iso.add_fp(io.BytesIO(vd), len(vd),
                   '/openstack/2017-02-22/vendor_data.json;6',
                   rr_name='vendor_data.json',
                   joliet_path='/openstack/2017-02-22/vendor_data.json')
        iso.add_fp(io.BytesIO(vd), len(vd),
                   '/openstack/latest/vendor_data2.json;7',
                   rr_name='vendor_data2.json',
                   joliet_path='/openstack/latest/vendor_data2.json')
        iso.add_fp(io.BytesIO(vd), len(vd),
                   '/openstack/2017-02-22/vendor_data2.json;8',
                   rr_name='vendor_data2.json',
                   joliet_path='/openstack/2017-02-22/vendor_data2.json')

        # Dump to disk
        if os.path.exists(disk_path):
            os.unlink(disk_path)
        iso.write(disk_path)
        iso.close()

    def _allocate_vsock_cid(self, channel_name):
        # Hold a global cluster lock for the duration of the
        # check-then-act sequence. Without this, two concurrent
        # allocations (potentially on different nodes) could both
        # observe the same CID as unused via is_vsock_cid_in_use()
        # and then both write it via set_vsock_cid(), since the
        # set_vsock_cid lock is per-instance and so does not
        # serialise allocations across instances.
        with locks.ClusterLock(
                'vsock_cids', None, 'global',
                op='Allocate vsock CID', timeout=30):
            cid = random.randint(3, 4294967295)
            while mariadb.is_vsock_cid_in_use(cid):
                cid = random.randint(3, 4294967295)
            self.set_vsock_cid(channel_name, cid)
            return cid

    def _create_domain_xml(self):
        """Create the domain XML for the instance."""

        os.makedirs(self.instance_path, exist_ok=True)
        with open(os.path.join(config.STORAGE_PATH, 'libvirt.tmpl')) as f:
            t = jinja2.Template(f.read())

        networks = []
        for ni in self.interfaces:
            n = network.Network.from_db(ni.network_uuid)
            networks.append(
                {
                    'macaddr': ni.macaddr,
                    'bridge': n.subst_dict()['vx_bridge'],
                    'model': ni.model,
                    'mtu': config.MAX_HYPERVISOR_MTU - 50
                }
            )

        # The nvram_template variable is either None (use the default path), or
        # a UUID of a blob to fetch. The nvram template is only used for UEFI boots.
        nvram_template_attribute = ''
        if self.uefi:
            if not self.nvram_template:
                if self.secure_boot:
                    nvram_template_attribute = "template='/usr/share/OVMF/OVMF_VARS.ms.fd'"
                else:
                    nvram_template_attribute = "template='/usr/share/OVMF/OVMF_VARS.fd'"
            else:
                # Fetch the nvram template
                b = blob.Blob.from_db(self.nvram_template)
                if not b:
                    raise exceptions.NVRAMTemplateMissing(
                        f'Blob {self.nvram_template} does not exist')
                b.ensure_local(instance_object=self)
                b.add_event(EVENT_TYPE_AUDIT, 'instance is using blob',
                            extra={'instance_uuid': self.uuid})
                shutil.copyfile(
                    blob.Blob.filepath(b.uuid), os.path.join(self.instance_path, 'nvram'))
                nvram_template_attribute = ''

        # Convert side channels into extra devices. There are now several types
        # of side channel:
        #     * sf-agent side channels are implemented as virtio-serial domain
        #       sockets on the hypervisor, and serial posts on the guest.
        #     * sf-agent2 side channels are implemented as virtio-vsocks, with
        #       the guest being the server and the hypervisor being various
        #       client connections.
        #
        # The API layer has already checked that only valid side channels are
        # requested before we get here.
        extradevices = []
        side_channels = self.side_channels
        if side_channels:
            for channel in side_channels:
                if channel == 'sf-agent':
                    extradevices.append("<channel type='unix'>")
                    extradevices.append(
                        f"  <source mode='bind' path='{self.instance_path}/sc-{channel}'/>")
                    extradevices.append(
                        f'  <target type="virtio" name="{channel}" state="connected"/>')
                    extradevices.append("</channel>")
                elif channel == 'sf-agent2':
                    cid = self._allocate_vsock_cid(channel)
                    extradevices.append("<vsock model='virtio'>")
                    extradevices.append(f"    <cid auto='no' address='{cid}'/>")
                    extradevices.append('</vsock>')

        # NOTE(mikal): the database stores memory allocations in MB, but the
        # domain XML takes them in KB. That wouldn't be worth a comment here if
        # I hadn't spent _ages_ finding a bug related to it.
        block_devices = self.block_devices
        ports = self.ports
        vdi_type = self.video['vdi']

        x = t.render(
            uuid=self.uuid,
            memory=self.memory * 1024,
            vcpus=self.cpus,
            disks=block_devices.get('devices'),
            networks=networks,
            instance_path=self.instance_path,
            console_port=ports.get('console_port'),
            vdi_port=ports.get('vdi_port'),
            vdi_tls_port=ports.get('vdi_tls_port'),
            video_model=self.video['model'],
            video_memory=self.video['memory'],
            uefi=self.uefi,
            secure_boot=self.secure_boot,
            nvram_template_attribute=nvram_template_attribute,
            extracommands=block_devices.get('extracommands', []),
            machine_type=self.machine_type,
            vdi_type=vdi_type,
            spice_concurrent=(vdi_type == 'spiceconcurrent'),
            spice_debug=(vdi_type == 'spicedebug'),
            extradevices=extradevices
        )

        # Libvirt re-writes the domain XML once loaded, so we store the XML
        # as generated as well so that we can debug. Note that this is _not_
        # the XML actually used by libvirt.
        self.add_event(
            EVENT_TYPE_MUTATE, 'libvirt domain XML',
            extra={
                'xml': x
            })

        # Validate domain XML syntax before passing to libvirt
        try:
            ET.fromstring(x)
        except ET.ParseError as e:
            self.enqueue_delete_due_error('invalid domain XML generated')
            raise exceptions.InvalidDomainXML(
                f'Generated domain XML is malformed: {e}')

        return x

    def is_powered_on(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                return 'off'

            return lc.extract_power_state(inst) == 'on'

    def power_on(self):
        # Generate a config drive. It is deliberate that this is in power_on now,
        # as a hard power off / on cycle should imply the re-creation of the
        # config drive. This is useful for hotplugged devices, because subsequent
        # cloud-init runs will now know about the new devices.
        if self.configdrive == 'openstack-disk':
            self._make_config_drive_openstack_disk(
                os.path.join(self.instance_path,
                             self.block_devices['devices'][1]['path']))

        # Create the actual instance. Sometimes on Ubuntu 20.04 we need to wait
        # for port binding to work. Revisiting this is tracked by issue 320 on
        # github. Additionally, sometimes ports are not released correctly by a
        # domain destroy, which means we need to reassign on domain start.
        if not self._power_on_inner():
            attempts = 1
            while not self._power_on_inner() and attempts < 5:
                self.add_event(
                    EVENT_TYPE_STATUS,
                    'instance required an additional attempt to power on',
                    extra={'attempt': attempts})
                time.sleep(1)
                attempts += 1

            self.agent_state = constants.AGENT_NEVER_TALKED

    def _power_on_retry_prep(self, domain, message,
                             needs_port_reallocation=False):
        if needs_port_reallocation:
            message += ' (TCP ports reallocated)'
        self.add_event(
            EVENT_TYPE_STATUS, 'instance power on requires new attempt',
            extra={'message': message})

        if needs_port_reallocation:
            self.deallocate_instance_ports()
            self.allocate_instance_ports()

        # We need to delete the nvram file before we can undefine
        # the domain. This will be recreated by libvirt on the next
        # attempt.
        nvram_path = os.path.join(self.instance_path, 'nvram')
        if os.path.exists(nvram_path):
            os.unlink(nvram_path)

        if domain:
            domain.undefine()

    def _power_on_inner(self):
        with util_libvirt.LibvirtConnection() as lc:
            try:
                domain = lc.get_domain_from_sf_uuid(self.uuid)
                if not domain:
                    domain_xml = self._create_domain_xml()
                    domain = lc.define_xml(domain_xml)
                    if not domain:
                        self.enqueue_delete_due_error(
                            'power on failed to create domain')
                        raise exceptions.NoDomainException()
            except lc.libvirt.libvirtError as e:
                if str(e).find("Invalid value for attribute 'port' in element "
                               "'graphics'") != -1:
                    self._power_on_retry_prep(
                        None, str(e), needs_port_reallocation=True)
                    return False
                else:
                    self._power_on_retry_prep(
                        None, f'unhandled instance definition error: {str(e)}',
                        needs_port_reallocation=True)
                    return False

            try:
                domain.create()
            except lc.libvirt.libvirtError as e:
                if str(e).startswith('Requested operation is not valid: '
                                     'domain is already running'):
                    return True
                elif (str(e).find('Failed to find an available port: '
                                  'Address already in use') != -1):
                    self._power_on_retry_prep(
                        domain, str(e), needs_port_reallocation=True)
                    return False
                elif str(e).find('reds_init_socket: binding socket') != -1:
                    self._power_on_retry_prep(
                        domain, str(e), needs_port_reallocation=True)
                    return False
                elif (str(e).find('internal error: process exited while '
                                  'connecting to monitor') != -1):
                    self._power_on_retry_prep(
                        domain, str(e), needs_port_reallocation=False)
                    return False
                else:
                    self._power_on_retry_prep(
                        None, f'unhandled instance start error: {str(e)}',
                        needs_port_reallocation=True)
                    return False

            try:
                domain.setAutostart(1)
            except lc.libvirt.libvirtError as e:
                self.add_event(
                    EVENT_TYPE_AUDIT, 'instance autostart configuration error',
                    extra={'message': str(e)})
                raise e

            try:
                self.update_power_state(lc.extract_power_state(domain))
            except lc.libvirt.libvirtError as e:
                self.add_event(
                    EVENT_TYPE_AUDIT,
                    'failed to determine instance power state during initial power on',
                    extra={'message': str(e)})
                raise e

            self.add_event(EVENT_TYPE_AUDIT, 'poweron')
            return True

    def power_off(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                return

            try:
                inst.destroy()
            except lc.libvirt.libvirtError as e:
                if not str(e).startswith('Requested operation is not valid: '
                                         'domain is not running'):
                    self.log.error('Failed to delete domain: %s', e)

            self.agent_state = constants.AGENT_INSTANCE_OFF
            self.update_power_state('off')
            self.add_event(EVENT_TYPE_AUDIT, 'poweroff')

    def reboot(self, hard=False):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst or not inst.isActive():
                # Our domains are persistent, so a powered off instance is
                # normally a defined but inactive domain. No domain at all is
                # also possible if the instance has never been started on this
                # node. Either way it doesn't make sense to reboot a powered
                # off machine.
                raise exceptions.InvalidLifecycleState(
                    'you cannot reboot a powered off instance')

            try:
                if not hard:
                    inst.reboot(flags=lc.libvirt.VIR_DOMAIN_REBOOT_ACPI_POWER_BTN)
                    self.add_event(EVENT_TYPE_AUDIT, 'soft reboot')
                else:
                    inst.reset()
                    self.add_event(EVENT_TYPE_AUDIT, 'hard reboot')
            except lc.libvirt.libvirtError as e:
                # The domain can shut off between the isActive() check above
                # and the reboot attempt.
                if 'domain is not running' in str(e):
                    raise exceptions.InvalidLifecycleState(
                        'you cannot reboot a powered off instance') from e
                raise

    def pause(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                # Not returning a libvirt domain here indicates that the instance
                # is "powered off" (destroyed in libvirt speak). It doesn't make
                # sense to reboot a powered off machine
                raise exceptions.InvalidLifecycleState(
                    'you cannot pause a powered off instance')

            attempts = 1
            inst.suspend()
            self.add_event(EVENT_TYPE_AUDIT, 'pause', extra={'attempt': attempts})

            while not self.update_power_state(lc.extract_power_state(inst)):
                if attempts > 2:
                    self.add_event(EVENT_TYPE_AUDIT, 'pause failed')
                    raise exceptions.InvalidLifecycleState(
                        f'pause failed after {attempts} attempts')

                time.sleep(1)
                attempts += 1
                inst.suspend()
                self.add_event(EVENT_TYPE_AUDIT, 'pause', extra={'attempt': attempts})

            self.agent_state = constants.AGENT_INSTANCE_PAUSED

    def unpause(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                # Not returning a libvirt domain here indicates that the instance
                # is "powered off" (destroyed in libvirt speak). It doesn't make
                # sense to reboot a powered off machine
                raise exceptions.InvalidLifecycleState(
                    'you cannot unpause a powered off instance')

            attempts = 1
            inst.resume()
            self.add_event(EVENT_TYPE_AUDIT, 'unpause', extra={'attempt': attempts})

            while not self.update_power_state(lc.extract_power_state(inst)):
                if attempts > 2:
                    self.add_event(EVENT_TYPE_AUDIT, 'unpause failed')
                    raise exceptions.InvalidLifecycleState(
                        f'unpause failed after {attempts} attempts')

                time.sleep(1)
                attempts += 1
                inst.resume()
                self.add_event(EVENT_TYPE_AUDIT, 'unpause', extra={'attempt': attempts})

            self.agent_state = constants.AGENT_NEVER_TALKED

    def get_console_data(self, length):
        console_path = os.path.join(self.instance_path, 'console.log')
        if not os.path.exists(console_path):
            return ''

        d = None
        file_length = os.stat(console_path).st_size
        with open(console_path, 'rb') as f:
            if length != -1:
                offset = max(0, file_length - length)
                f.seek(offset)
            d = f.read()
        return d

    def delete_console_data(self):
        console_path = os.path.join(self.instance_path, 'console.log')
        if not os.path.exists(console_path):
            return
        os.truncate(console_path, 0)
        self.add_event(EVENT_TYPE_AUDIT, 'console log cleared')

    def enqueue_delete(self):
        # If this instance is not on a node, just enqueue on this node
        placement = self.placement
        if not placement.get('node'):
            node = config.NODE_UUID
        else:
            node = placement['node']

        if not node:
            self.add_event(
                EVENT_TYPE_AUDIT,
                'cannot enqueue delete, instance has no placement '
                'and local node UUID is not configured')
            return

        # Determine which outstanding cluster operations should be cancelled
        # for this instance. I don't love this approach, but I cannot think
        # of something which isn't terrible in some other way right now either.
        ops = []

        lco = self.last_cluster_operation
        if lco:
            lco_op = get_object_class(lco['op_type']).from_db(
                lco['op_uuid'], suppress_failure_audit=True)
            if lco_op:
                ops = traverse_cluster_operations_tree(lco_op)

        for op in ops:
            has_protected_task = False
            for task in op.tasks:
                if task in [niso_tasks.instance_snapshot]:
                    has_protected_task = True

            if not has_protected_task:
                add_event_multi(
                    EVENT_TYPE_AUDIT, [self, op],
                    'task aborted due to enqueued delete request')
                try:
                    op.state = bco.STATE_ABORT
                except exceptions.InvalidStateException:
                    op.add_event(EVENT_TYPE_AUDIT, 'failed to abort operation')

        nio_create_and_enqueue(
            node,
            self.uuid,
            [nio_tasks.instance_delete],
            PRIORITY.user_facing,
            runs_after=[self.last_cluster_operation],
            request_id=util_general.get_request_id())

    def enqueue_delete_due_error(self, error_msg):
        # Error needs to be set immediately so that API clients get
        # correct information. The VM and network tear down can be delayed.
        if self.state.value == self.STATE_DELETED:
            return

        try:
            self.state = f'{self.state.value}-error'
        except Exception:
            # We can land here if there is a serious database error.
            self.state = self.STATE_ERROR

        self.error = error_msg
        self.enqueue_delete()

    def snapshot(self, all=False, device=None, max_versions=None, thin=False):
        disks = self.block_devices['devices']

        # Include NVRAM as a snapshot option if we are UEFI booted
        if self.uefi:
            disks.append({
                'type': 'nvram',
                'device': 'nvram',
                'path': os.path.join(self.instance_path, 'nvram'),
                'snapshot_ignores': False
            })

        # Filter if requested
        if device:
            new_disks = []
            for d in disks:
                if d['device'] == device:
                    new_disks.append(d)
            disks = new_disks
        elif not all:
            disks = [disks[0]]
        self.log.with_fields({'devices': disks}).info('Devices for snapshot')

        out = {}
        snapshots = []
        for disk in disks:
            if disk['snapshot_ignores']:
                continue

            if disk['type'] not in ['qcow2', 'nvram']:
                continue

            if not os.path.exists(disk['path']):
                continue

            # By ownership, because this ends in add_index -- here for
            # nvram, and in node_inst_snap_op for everything else --
            # and add_index ends in delete_old_versions. The target
            # namespace is fixed as the instance's own, so there are no
            # two cases to authorise apart and the create comes free.
            #
            # Not currently reachable as a cross namespace write: the
            # URL contains the instance UUID and type_filter pins it to
            # TYPE_SNAPSHOT, so nothing else resolves here. That is an
            # argument for the guard being cheap rather than for going
            # without it -- the next artifact type minted against an
            # instance URL should not have to rediscover the rule.
            a = artifact.Artifact.owned_from_url_or_new(
                artifact.Artifact.TYPE_SNAPSHOT,
                f'{artifact.INSTANCE_URL}{self.uuid}/{disk["device"]}',
                name=f'{self.uuid}/{disk["device"]}',
                max_versions=max_versions, namespace=self.namespace)

            blob_uuid = str(uuid4())
            out[disk['device']] = {
                'source_url': a.source_url,
                'artifact_uuid': str(a.uuid),
                'blob_uuid': blob_uuid
            }

            if disk['type'] == 'nvram':
                # These are small and don't use qemu-img to capture, so just
                # do them now.
                dest_path = blob.Blob.filepath(blob_uuid)
                shutil.copyfile(disk['path'], dest_path)

                st = os.stat(dest_path)
                b = blob.Blob.new(blob_uuid, time.time(), time.time())
                b.size = st.st_size
                b.observe()
                b.verify_checksum()

                a.add_index(blob_uuid, force=True)
                a.state = artifact.Artifact.STATE_CREATED

            else:
                snapshots.append(niso_snapshot(
                    disk=disk,
                    artifact_uuid=a.uuid,
                    blob_uuid=blob_uuid,
                    thin=thin
                ))

        niso_create_and_enqueue(
            config.NODE_UUID,
            self.uuid,
            snapshots,
            [niso_tasks.instance_snapshot],
            PRIORITY.user_facing_high_io,
            runs_after=[self.last_cluster_operation],
            request_id=util_general.get_request_id())

        self.add_event(EVENT_TYPE_AUDIT, 'snapshot requested', extra=out)
        return out

    def archive_console_log(self):
        console_path = os.path.join(self.instance_path, 'console.log')
        if not os.path.exists(console_path):
            return

        st = os.stat(console_path)
        if st.st_size > 0:
            # These two artifacts need to appear in this order, or the system
            # artifact wont be created because system can "see" the other
            # namespace.
            artifacts = []
            if self.namespace != 'system':
                artifacts.append(artifact.Artifact.new(
                    artifact.Artifact.TYPE_OTHER,
                    f'{artifact.INSTANCE_URL}{self.uuid}/console',
                    name=f'{self.uuid}/console', max_versions=1,
                    namespace='system'))
            artifacts.append(artifact.Artifact.new(
                    artifact.Artifact.TYPE_OTHER,
                    f'{artifact.INSTANCE_URL}{self.uuid}/console',
                    name=f'{self.uuid}/console', max_versions=1,
                    namespace=self.namespace))

            blob_uuid = str(uuid4())
            dest_path = blob.Blob.filepath(blob_uuid)
            shutil.copyfile(console_path, dest_path)

            b = blob.Blob.new(blob_uuid, time.time(), time.time())
            b.size = st.st_size
            b.set_lifetime(config.ARCHIVE_INSTANCE_CONSOLE_DURATION * 3600 * 24)
            b.observe()
            b.verify_checksum()

            for a in artifacts:
                a.add_index(blob_uuid)
                a.state = artifact.Artifact.STATE_CREATED
                self.add_event(
                    EVENT_TYPE_AUDIT, 'the console log for this instance was archived',
                    extra={
                        'namespace': a.namespace,
                        'artifact': a.uuid,
                        'blob': b.uuid
                        })
        else:
            self.add_event(
                EVENT_TYPE_AUDIT,
                'the console log for this instance was not archived as it was empty')

    @property
    def agent_operations(self):
        return self._db_get_attribute('agent_operations')

    def agent_operation_next(self):
        """Return the next dispatchable agent operation, leaving it queued.

        The queue entry is the unit of durability: an operation stays at
        the head of the queue until it has provably left the QUEUED state
        (the executor moved it to EXECUTING or beyond), at which point a
        later call lazily pops it. This makes dispatch crash safe -- if
        the sidechannel daemon or its executor thread dies between
        selecting an operation and delivering it to the agent, the
        operation is simply returned again by a later call, instead of
        being orphaned in QUEUED with no queue entry (the failure mode
        this replaces, which presented as an agent operation stuck in the
        queued state while the instance's agent was ready).

        The caller must ensure only one executor runs per instance at a
        time -- instances are placed on exactly one hypervisor and the
        sidechannel daemon there skips instances with a live executor, so
        the operation being visible at the head during execution cannot
        double dispatch.
        """
        # First check cheaply if there are any agent operations queued. This is
        # likely to be the case 99% of the time.
        if not self._db_get_attribute('agent_operations', {}).get('queue', []):
            return None

        # Now do it safely with the lock held
        with self.get_lock_attr('agent_operations', 'Next agent operation'):
            db_data = self._db_get_attribute('agent_operations')
            queue = db_data.get('queue', [])

            changed = False
            result = None
            while queue:
                agentop = AgentOperation.from_db(queue[0])
                if not agentop:
                    # AgentOp is invalid, remove from queue and consider the
                    # next entry.
                    queue.pop(0)
                    changed = True
                    continue

                state = agentop.state.value
                if state == AgentOperation.STATE_QUEUED:
                    # Dispatchable. Leave it on the queue -- it is retired
                    # by a later call once it has left the QUEUED state.
                    result = agentop
                    break

                if state in (dbo.STATE_INITIAL, AgentOperation.STATE_PREFLIGHT):
                    # Not yet dispatchable (the API is mid-enqueue, or a
                    # preflight task has yet to promote it). We like
                    # maintaining order, so claim we have no work to do
                    # right now.
                    break

                # EXECUTING, COMPLETE, ERROR or DELETED: this operation is
                # finished with the queue. Retire the entry and consider
                # the next one. This also unwedges a queue whose head
                # errored or was deleted, which previously blocked the
                # instance's queue forever.
                queue.pop(0)
                changed = True

            if changed:
                db_data['queue'] = queue
                self._db_set_attribute('agent_operations', db_data)
            return result

    def agent_operation_enqueue(self, agentop_uuid):
        with self.get_lock_attr('agent_operations', 'Enqueue agent operation'):
            # NOTE(mikal): the "queue" entry is agent operations not yet executed,
            # the "all" entry is a log of all agent operations ever. We need "all",
            # otherwise its quite hard to lookup an executed agent operation if
            # you've lost its UUID.
            db_data = self._db_get_attribute('agent_operations')
            if 'queue' not in db_data:
                db_data['queue'] = []
            if 'all' not in db_data:
                db_data['all'] = []

            # Ensure UUID is stored as a string for JSON serialization
            agentop_uuid_str = str(agentop_uuid)
            db_data['queue'].append(agentop_uuid_str)
            db_data['all'].append(agentop_uuid_str)
            self._db_set_attribute('agent_operations', db_data)

    def get_screenshot(self):
        blob_uuid = str(uuid4())
        dest_path = blob.Blob.filepath(blob_uuid)

        with util_libvirt.LibvirtConnection() as lc:
            lc.get_screenshot(self.uuid, dest_path + '.partial')

        b = blob.Blob.new(blob_uuid, time.time(), time.time())
        b.register()

        self.add_event(EVENT_TYPE_AUDIT, 'acquired screenshot of instance console',
                       extra={'blob': blob_uuid})

        return blob_uuid

    def hot_plug_interface(self, n, ni):
        # Attach the interface device via libvirt. The network reconcile
        # (create-on-hypervisor + ensure_mesh) and the wait for it are the
        # caller's responsibility -- node_inst_net_iface_op does the
        # reconcile and defers this attach on the mesh op rather than
        # blocking a worker on raise_for_error().
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst or not inst.isActive():
                raise exceptions.InvalidLifecycleState(
                    'instance is not running, cannot hot plug interface')

            bridge = n.subst_dict()['vx_bridge']
            mtu = config.MAX_HYPERVISOR_MTU - 50
            device_xml = f'''    <interface type="bridge">
      <mac address="{ni.macaddr}"/>
      <source bridge="{bridge}"/>
      <model type="{ni.model}"/>
      <mtu size="{mtu}"/>
      </interface>
      '''

            flags = (lc.libvirt.VIR_DOMAIN_AFFECT_CONFIG |
                     lc.libvirt.VIR_DOMAIN_AFFECT_LIVE)
            try:
                inst.attachDeviceFlags(device_xml, flags=flags)
            except lc.libvirt.libvirtError as e:
                add_event_multi(
                    EVENT_TYPE_AUDIT, [self, n, ni],
                    'hot plug interface failed',
                    extra={'error': str(e)})
                raise exceptions.InvalidLifecycleState(
                    f'hot plug interface failed: {e}')

            add_event_multi(
                EVENT_TYPE_AUDIT, [self, n, ni], 'hot plugged interface')
            self._record_domain_xml()

    def socket_on_vsock_channel(self, channel, port=1025):
        cid = self.vsock_cid(channel)
        if not cid:
            raise exceptions.NoSuchChannel(f'No such channel {channel}')

        return ConnectedVSockChannel(channel, cid, port, self.log)


class Instances(dbo_iter):
    base_object = Instance

    def _resolve_prefilter_to_states(self):
        # Preserve the pre-phase-4 Instances override behaviour: when
        # no prefilter is set, do not filter on state (return every
        # instance and let predicate filters scope). The base class
        # default of ACTIVE_STATES is kept for other inheritors.
        if self.prefilter is None:
            return set()
        return super()._resolve_prefilter_to_states()

    def _find(self, criteria):
        return mariadb.find_instances(criteria)

    def _to_static_values(self, data):
        return Instance._static_values_to_dict(data)

    def __iter__(self):
        for _, static_values in self.get_iterator():
            inst = Instance(static_values)
            if not inst:
                continue
            filtered = self.apply_filters(inst)
            if filtered:
                yield filtered


def placement_filter(node, inst):
    p = inst.placement
    return p.get('node') == node


def this_node_filter(inst):
    return placement_filter(config.NODE_UUID, inst)


# Convenience helpers
def healthy_instances_on_node(n):
    return Instances(
        [partial(placement_filter, str(n.uuid))],
        prefilter='healthy')


def instances_in_namespace(namespace):
    return Instances(namespace=namespace)


def all_instances():
    for object_uuid in mariadb.get_all_instance_uuids():
        i = Instance.from_db(object_uuid, suppress_failure_audit=True)
        if i:
            yield i


def instance_blob_usage(node=None):
    """Map blob uuid to the uuids of healthy instances using that blob.

    Walks every healthy instance (optionally filtered to one node) exactly
    once, recording the blobs each disk references directly and via its
    dependency chain. Callers that need usage for many blobs -- the
    cluster wide cleanup loop -- must use this rather than calling
    instance_usage_for_blob_uuid() per blob: the per-blob form repeats
    the instance walk, and its per-disk block_devices and dependency
    chain reads, for every single blob (issue 3502).
    """
    filters = []
    if node:
        filters.append(partial(placement_filter, node))

    usage: dict[str, list[str]] = defaultdict(list)
    for inst in Instances(filters, prefilter='healthy'):
        # inst.block_devices isn't populated until the instance is created,
        # so it may not be ready yet. This means we will miss instances
        # which have been requested but not yet started.
        inst_uuid = str(inst.uuid)
        in_use: set[str] = set()
        for d in inst.block_devices.get('devices', []):
            if 'blob_uuid' not in d:
                continue

            # This blob is in direct use...
            in_use.add(d['blob_uuid'])

            # ...and so is everything in its dependency chain.
            disk_blob = blob.Blob.from_db(
                d['blob_uuid'], suppress_failure_audit=True)
            while disk_blob:
                depends_on = disk_blob.depends_on
                if not depends_on:
                    break
                in_use.add(depends_on)
                disk_blob = blob.Blob.from_db(
                    depends_on, suppress_failure_audit=True)

        for blob_uuid in in_use:
            usage[blob_uuid].append(inst_uuid)

    return dict(usage)


def instance_usage_for_blob_uuid(blob_uuid, node=None):
    return instance_blob_usage(node=node).get(str(blob_uuid), [])
