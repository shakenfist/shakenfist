# Copyright 2019 Michael Still

import base64
from functools import partial
import jinja2
import io
import json
import os
import pathlib
import pycdlib
import random
import shutil
import socket
import time
from uuid import uuid4

from shakenfist import artifact
from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist import blob
from shakenfist.config import config
from shakenfist import constants
from shakenfist import db
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist.metrics import get_minimum_object_version as gmov
from shakenfist import network
from shakenfist import networkinterface
from shakenfist.tasks import DeleteInstanceTask, SnapshotTask
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logutil.setup(__name__)


def _get_defaulted_disk_bus(disk):
    bus = disk.get('bus')
    if bus:
        return bus
    return config.DISK_BUS


LETTERS = 'abcdefghijklmnopqrstuvwxyz'
NUMBERS = '0123456789'


def _get_disk_device(bus, index):
    bases = {
        'ide': ('hd', LETTERS),
        'sata': ('sd', LETTERS),
        'scsi': ('sd', LETTERS),
        'usb': ('sd', LETTERS),
        'virtio': ('vd', LETTERS),
        'nvme': ('nvme', NUMBERS),
    }
    prefix, index_scheme = bases.get(bus, 'sd')
    return '%s%s' % (prefix, index_scheme[index])


def _get_defaulted_disk_type(disk):
    kind = disk.get('type')
    if kind:
        return kind
    return 'disk'


def _safe_int_cast(i):
    if i:
        return int(i)
    return i


class Instance(dbo):
    object_type = 'instance'
    current_version = 7
    upgrade_supported = True

    # docs/development/state_machine.md has a description of these states.
    STATE_INITIAL_ERROR = 'initial-error'
    STATE_PREFLIGHT = 'preflight'
    STATE_PREFLIGHT_ERROR = 'preflight-error'
    STATE_CREATING_ERROR = 'creating-error'
    STATE_CREATED_ERROR = 'created-error'
    STATE_DELETE_WAIT_ERROR = 'delete-wait-error'

    ACTIVE_STATES = set([dbo.STATE_INITIAL,
                         STATE_INITIAL_ERROR,
                         STATE_PREFLIGHT,
                         STATE_PREFLIGHT_ERROR,
                         dbo.STATE_CREATING,
                         STATE_CREATING_ERROR,
                         dbo.STATE_CREATED,
                         STATE_CREATED_ERROR,
                         dbo.STATE_ERROR
                         ])

    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (STATE_PREFLIGHT, dbo.STATE_DELETE_WAIT,
                            dbo.STATE_DELETED, STATE_INITIAL_ERROR),
        STATE_PREFLIGHT: (dbo.STATE_CREATING, dbo.STATE_DELETE_WAIT,
                          dbo.STATE_DELETED, STATE_PREFLIGHT_ERROR),
        dbo.STATE_CREATING: (dbo.STATE_CREATED, dbo.STATE_DELETE_WAIT,
                             dbo.STATE_DELETED, STATE_CREATING_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED,
                            STATE_CREATED_ERROR),
        STATE_INITIAL_ERROR: (dbo.STATE_ERROR),
        STATE_PREFLIGHT_ERROR: (dbo.STATE_ERROR),
        STATE_CREATING_ERROR: (dbo.STATE_ERROR),
        STATE_CREATED_ERROR: (dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED,
                          dbo.STATE_ERROR),
        dbo.STATE_DELETE_WAIT: (dbo.STATE_DELETED, STATE_DELETE_WAIT_ERROR),
        STATE_DELETE_WAIT_ERROR: (dbo.STATE_ERROR),
        dbo.STATE_DELETED: None,
    }

    # Metadata - Reserved Keys
    METADATA_KEY_TAGS = 'tags'
    METADATA_KEY_AFFINITY = 'affinity'

    def __init__(self, static_values):
        if static_values['version'] != self.current_version:
            upgraded, static_values = self.upgrade(static_values)

            if upgraded and gmov('instance') == self.current_version:
                etcd.put(self.object_type, None,
                         static_values.get('uuid'), static_values)
                LOG.with_field(
                    self.object_type, static_values['uuid']).info('Online upgrade committed')

        super(Instance, self).__init__(static_values.get('uuid'),
                                       static_values.get('version'))

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
    def upgrade(cls, static_values):
        changed = False
        starting_version = static_values.get('version')

        if static_values.get('version') == 3:
            static_values['configdrive'] = 'openstack-disk'
            static_values['version'] = 4
            changed = True

        if static_values.get('version') == 4:
            static_values['nvram_template'] = None
            static_values['secure_boot'] = False
            static_values['version'] = 5
            changed = True

        if static_values.get('version') == 5:
            static_values['machine_type'] = 'pc'
            static_values['version'] = 6
            changed = True

        if static_values.get('version') == 6:
            static_values['side_channels'] = []
            static_values['version'] = 7
            changed = True

        if changed:
            LOG.with_fields({
                cls.object_type: static_values['uuid'],
                'start_version': starting_version,
                'final_version': static_values.get('version')
            }).info('Object online upgraded')
        return changed, static_values

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
        i._db_set_attribute(
            'power_state', {'power_state': cls.STATE_INITIAL})
        return i

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        i = {
            'uuid': self.uuid,
            'cpus': self.cpus,
            'disk_spec': self.disk_spec,
            'memory': self.memory,
            'name': self.name,
            'namespace': self.namespace,
            'ssh_key': self.ssh_key,
            'state': self.state.value,
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

            'version': self.version,
            'error_message': self.error,
        }

        if self.requested_placement:
            i['requested_placement'] = self.requested_placement

        external_attribute_key_whitelist = [
            'console_port',
            'node',
            'power_state',
            'vdi_port'
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
        i['interfaces'] = []
        for iface_uuid in self.interfaces:
            ni = networkinterface.NetworkInterface.from_db(iface_uuid)
            if not ni:
                self.log.with_object(ni).error('Network interface missing')
            else:
                i['interfaces'].append(ni.external_view())

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
        return os.path.join(config.STORAGE_PATH, 'instances', self.uuid)

    # Values routed to attributes, writes are via helper methods.
    @property
    def affinity(self):
        # TODO(andy) Move metadata to a new DBO subclass "DBO with metadata"
        meta = db.get_metadata('instance', self.uuid)
        return meta.get(self.METADATA_KEY_AFFINITY, {})

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

    # NOTE(mikal): this really should use the newer _add_item / _remove_item
    # stuff, but I can't immediately think of an online upgrade path for this
    # so skipping for now.
    @property
    def interfaces(self):
        return self._db_get_attribute('interfaces')

    # NOTE(mikal): this really should use the newer _add_item / _remove_item
    # stuff, but I can't immediately think of an online upgrade path for this
    # so skipping for now.
    @interfaces.setter
    def interfaces(self, interfaces):
        self._db_set_attribute('interfaces', interfaces)

    @property
    def tags(self):
        # TODO(andy): Move metadata to a new DBO subclass "DBO with metadata"
        meta = db.get_metadata('instance', self.uuid)
        if not meta:
            # Gracefully handle malformed instances
            return None
        return meta.get(self.METADATA_KEY_TAGS, None)

    @property
    def agent_state(self):
        db_data = self._db_get_attribute('agent_state')
        if not db_data:
            return baseobject.State(None, 0)
        return baseobject.State(**db_data)

    @agent_state.setter
    def agent_state(self, new_value):
        orig = self.agent_state
        if orig.value == new_value:
            return

        new_state = baseobject.State(new_value, time.time())
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

    # Implementation
    def _initialize_block_devices(self):
        bus = _get_defaulted_disk_bus(self.disk_spec[0])
        root_device = _get_disk_device(bus, 0)
        config_device = _get_disk_device(bus, 1)

        disk_type = 'qcow2'

        block_devices = {
            'devices': [
                {
                    'type': disk_type,
                    'size': _safe_int_cast(self.disk_spec[0].get('size')),
                    'device': root_device,
                    'bus': bus,
                    'path': os.path.join(self.instance_path, root_device),
                    'base': self.disk_spec[0].get('base'),
                    'blob_uuid': self.disk_spec[0].get('blob_uuid'),
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
                    'cache_mode': constants.DISK_CACHE_MODE
                }
            )
            i += 1

        for d in self.disk_spec[1:]:
            bus = _get_defaulted_disk_bus(d)
            device = _get_disk_device(bus, i)
            disk_path = os.path.join(self.instance_path, device)

            block_devices['devices'].append({
                'type': disk_type,
                'size': _safe_int_cast(d.get('size')),
                'device': device,
                'bus': bus,
                'path': disk_path,
                'base': d.get('base'),
                'blob_uuid': d.get('blob_uuid'),
                'present_as': _get_defaulted_disk_type(d),
                'snapshot_ignores': False,
                'cache_mode': constants.DISK_CACHE_MODE
            })
            i += 1

        # NVME disks require a different treatment because libvirt doesn't natively
        # support them yet.
        nvme_counter = 0
        for d in block_devices['devices']:
            if d['bus'] == 'nvme':
                nvme_counter += 1
                block_devices['extracommands'].extend([
                    '-drive', ('file=%s,format=%s,if=none,id=NVME%d'
                               % (d['path'], d['type'], nvme_counter)),
                    '-device', ('nvme,drive=NVME%d,serial=nvme-%d'
                                % (nvme_counter, nvme_counter))
                ])

        block_devices['finalized'] = False
        return block_devices

    def place_instance(self, location):
        with self.get_lock_attr('placement', 'Instance placement'):
            # We don't write unchanged things to the database
            placement = self.placement
            if placement.get('node') == location:
                return

            placement['node'] = location
            placement['placement_attempts'] = placement.get(
                'placement_attempts', 0) + 1
            self._db_set_attribute('placement', placement)

    def enforced_deletes_increment(self):
        with self.get_lock_attr('enforced_deletes',
                                'Instance enforced deletes increment'):
            enforced_deletes = self.enforced_deletes
            enforced_deletes['count'] = enforced_deletes.get('count', 0) + 1
            self._db_set_attribute('enforced_deletes', enforced_deletes)

    def update_power_state(self, state):
        with self.get_lock_attr('power_state', 'Instance power state update'):
            # We don't write unchanged things to the database
            dbstate = self.power_state
            if dbstate.get('power_state') == state:
                return

            # TODO(andy): Find out what problem this is avoiding

            # If we are in transition, and its new, then we might
            # not want to update just yet
            state_age = time.time() - dbstate.get('power_state_updated', 0)
            if (dbstate.get('power_state', '').startswith('transition-to-') and
                    dbstate['power_state_previous'] == state and
                    state_age < 70):
                return

            dbstate['power_state_previous'] = dbstate.get('power_state')
            dbstate['power_state'] = state
            dbstate['power_state_updated'] = time.time()
            self._db_set_attribute('power_state', dbstate)

    # NOTE(mikal): this method is now strictly the instance specific steps for
    # creation. It is assumed that the image sits in local cache already, and
    # has been transcoded to the right format. This has been done to facilitate
    # moving to a queue and task based creation mechanism.
    def create(self, iface_uuids, lock=None):
        self.state = self.STATE_CREATING
        self.interfaces = iface_uuids

        # Ensure we have state on disk
        os.makedirs(self.instance_path, exist_ok=True)

        # Configure block devices, include config drive creation
        self._configure_block_devices(lock)

        self.power_on()
        if self.is_powered_on():
            self.state = self.STATE_CREATED
        else:
            self.add_event2('instance failed to power on')
            self.enqueue_delete_due_error('Instance failed to power on')

    def _delete_on_hypervisor(self):
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
            if self.nvram_template:
                b = blob.Blob.from_db(self.nvram_template)
                b.ref_count_dec()

            with util_libvirt.LibvirtConnection() as lc:
                inst = lc.get_domain_from_sf_uuid(self.uuid)
                if inst:
                    inst.undefine()
        except Exception as e:
            util_general.ignore_exception(
                'instance delete domain %s' % self, e)

        with util_general.RecordedOperation('delete disks', self):
            try:
                if os.path.exists(self.instance_path):
                    shutil.rmtree(self.instance_path)
            except Exception as e:
                util_general.ignore_exception(
                    'instance delete disks %s' % self, e)

    def _delete_globally(self):
        for disk in self.block_devices.get('devices', []):
            if 'blob_uuid' in disk and disk['blob_uuid']:
                b = blob.Blob.from_db(disk['blob_uuid'])
                if b:
                    b.ref_count_dec()

        self.deallocate_instance_ports()

        if self.state.value.endswith('-%s' % self.STATE_ERROR):
            self.state = self.STATE_ERROR
        else:
            self.state = self.STATE_DELETED

    def delete(self, global_only=False):
        self._delete_on_hypervisor()
        self._delete_globally()

    def _allocate_console_port(self):
        node = config.NODE_NAME
        consumed = [value['port']
                    for _, value in etcd.get_all('console', node)]
        while True:
            port = random.randint(30000, 50000)
            # avoid hitting etcd if it's probably in use
            if port in consumed:
                continue
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # We hold this port open until it's in etcd to prevent
                # anyone else needing to hit etcd to find out they can't
                # use it as well as to verify we can use it
                s.bind(('0.0.0.0', port))
                allocatedPort = etcd.create(
                    'console', node, port,
                    {
                        'instance_uuid': self.uuid,
                        'port': port,
                    })
                if allocatedPort:
                    return port
            except socket.error:
                LOG.with_field('instance', self.uuid).info(
                    'Collided with in use port %d, selecting another' % port)
                consumed.append(port)
            finally:
                s.close()

    def _free_console_port(self, port):
        if port:
            etcd.delete('console', config.NODE_NAME, port)

    def allocate_instance_ports(self):
        with self.get_lock_attr('ports', 'Instance port allocation'):
            p = self.ports
            if not p:
                p = {
                    'console_port': self._allocate_console_port(),
                    'vdi_port': self._allocate_console_port()
                }
                self.ports = p

    def deallocate_instance_ports(self):
        ports = self.ports
        self._free_console_port(ports.get('console_port'))
        self._free_console_port(ports.get('vdi_port'))
        self._db_delete_attribute('ports')

    def _configure_block_devices(self, lock):
        with self.get_lock_attr('block_devices', 'Initialize block devices'):
            # Create block devices if required
            block_devices = self.block_devices
            if not block_devices:
                block_devices = self._initialize_block_devices()

            # Generate a config drive
            if self.configdrive == 'openstack-disk':
                self._make_config_drive_openstack_disk(
                    os.path.join(self.instance_path, block_devices['devices'][1]['path']))

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
                        disk_base = '%s%s' % (
                            artifact.BLOB_URL, disk['blob_uuid'])
                    elif disk.get('base') and not util_general.noneish(disk.get('base')):
                        a = artifact.Artifact.from_url(
                            artifact.Artifact.TYPE_IMAGE, disk['base'],
                            namespace=self.namespace, create_if_new=True)
                        mri = a.most_recent_index

                        if 'blob_uuid' not in mri:
                            raise exceptions.ArtifactHasNoBlobs(
                                'Artifact %s of type %s has no versions'
                                % (a.uuid, a.artifact_type))

                        disk['blob_uuid'] = mri['blob_uuid']
                        disk_base = '%s%s' % (
                            artifact.BLOB_URL, disk['blob_uuid'])

                    if disk_base:
                        cached_image_path = util_general.file_permutation_exists(
                            os.path.join(config.STORAGE_PATH,
                                         'image_cache', disk['blob_uuid']),
                            ['iso', 'qcow2'])
                        if not cached_image_path:
                            raise exceptions.ImageMissingFromCache(
                                'Image %s is missing' % disk['blob_uuid'])

                        b = blob.Blob.from_db(disk['blob_uuid'])
                        b.ref_count_inc()

                        with util_general.RecordedOperation('detect cdrom images', self):
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
                            util_image.create_qcow2([lock], cached_image_path,
                                                    disk['path'], disk_size=disk['size'])

                        else:
                            with util_general.RecordedOperation('create copy on write layer', self):
                                util_image.create_cow([lock], cached_image_path,
                                                      disk['path'], disk['size'])
                            self.log.with_fields(util_general.stat_log_fields(disk['path'])).info(
                                'COW layer %s created' % disk['path'])

                            # Record the backing store for modern libvirt. This requires
                            # walking the chain of dependencies. Backing chains only work
                            # for qcow2 images. The backing image should already have been
                            # transcoded as part of the image fetch process.
                            backing_chain = []
                            backing_uuid = disk['blob_uuid']
                            while backing_uuid:
                                backing_path = os.path.join(
                                    config.STORAGE_PATH, 'image_cache', backing_uuid + '.qcow2')
                                backing_chain.append(backing_path)
                                backing_blob = blob.Blob.from_db(backing_uuid)
                                backing_uuid = backing_blob.depends_on

                            indent = '      '
                            disk['backing'] = ''
                            backing_chain.reverse()

                            for backing_path in backing_chain:
                                disk['backing'] = (
                                    '%(indent)s<backingStore type=\'file\'>'
                                    '%(indent)s  <format type=\'qcow2\'/>'
                                    '%(indent)s  <source file=\'%(path)s\'/>\n'
                                    '%(indent)s  %(chain)s\n'
                                    '%(indent)s</backingStore>\n'
                                    % {
                                        'chain': disk['backing'],
                                        'path': backing_path,
                                        'indent': indent
                                    })
                                indent += '  '

                            disk['backing'] = disk['backing'].lstrip()

                    elif not os.path.exists(disk['path']):
                        util_image.create_blank(
                            [lock], disk['path'], disk['size'])

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

        # meta_data.json -- note that limits on hostname are imposted at the API layer
        md = json.dumps({
            'random_seed': base64.b64encode(os.urandom(512)).decode('ascii'),
            'uuid': self.uuid,
            'availability_zone': config.ZONE,
            'hostname': '%s.local' % self.name,
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

        # user_data
        if self.user_data:
            user_data = base64.b64decode(self.user_data)
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
            'services': [
                {
                    'address': config.DNS_SERVER,
                    'type': 'dns'
                }
            ]
        }

        have_default_route = False
        for iface_uuid in self.interfaces:
            iface = networkinterface.NetworkInterface.from_db(iface_uuid)
            if iface.ipv4:
                devname = 'eth%d' % iface.order
                nd['links'].append(
                    {
                        'ethernet_mac_address': iface.macaddr,
                        'id': devname,
                        'name': devname,
                        'mtu': config.MAX_HYPERVISOR_MTU - 50,
                        'type': 'vif',
                        'vif_id': iface.uuid
                    }
                )

                n = network.Network.from_db(iface.network_uuid)
                nd['networks'].append(
                    {
                        'id': '%s-%s' % (iface.network_uuid, iface.order),
                        'link': devname,
                        'type': 'ipv4',
                        'network_id': iface.network_uuid
                    }
                )

                nd['networks'][-1].update({
                    'ip_address': iface.ipv4,
                    'netmask': str(n.netmask),
                })

            # NOTE(mikal): it is assumed that the default route should be on
            # the first interface specified.
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
        vd = '{}'.encode('ascii')
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
        iso.write(disk_path)
        iso.close()

    def _create_domain_xml(self):
        """Create the domain XML for the instance."""

        os.makedirs(self.instance_path, exist_ok=True)
        with open(os.path.join(config.STORAGE_PATH, 'libvirt.tmpl')) as f:
            t = jinja2.Template(f.read())

        networks = []
        for iface_uuid in self.interfaces:
            ni = networkinterface.NetworkInterface.from_db(iface_uuid)
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
                        'Blob %s does not exist' % self.nvram_template)
                b.ensure_local([], instance_object=self)
                b.ref_count_inc()
                shutil.copyfile(
                    blob.Blob.filepath(b.uuid), os.path.join(self.instance_path, 'nvram'))
                nvram_template_attribute = ''

        # Convert side channels into extra devices. Side channels are implemented
        # as virtio-serial domain sockets on the hypervisor, and serial posts on
        # the guest.
        extradevices = []
        side_channels = self.side_channels
        if side_channels:
            for channel in side_channels:
                extradevices.append("<channel type='unix'>")
                extradevices.append(
                    "  <source mode='bind' path='%s/sc-%s'/>" % (self.instance_path, channel))
                extradevices.append(
                    "  <target type='virtio' name='%s' state='connected'/>" % channel)
                extradevices.append("</channel>")

        # NOTE(mikal): the database stores memory allocations in MB, but the
        # domain XML takes them in KB. That wouldn't be worth a comment here if
        # I hadn't spent _ages_ finding a bug related to it.
        block_devices = self.block_devices
        ports = self.ports
        x = t.render(
            uuid=self.uuid,
            memory=self.memory * 1024,
            vcpus=self.cpus,
            disks=block_devices.get('devices'),
            networks=networks,
            instance_path=self.instance_path,
            console_port=ports.get('console_port'),
            vdi_port=ports.get('vdi_port'),
            video_model=self.video['model'],
            video_memory=self.video['memory'],
            uefi=self.uefi,
            secure_boot=self.secure_boot,
            nvram_template_attribute=nvram_template_attribute,
            extracommands=block_devices.get('extracommands', []),
            machine_type=self.machine_type,
            extradevices=extradevices
        )

        # Libvirt re-writes the domain XML once loaded, so we store the XML
        # as generated as well so that we can debug. Note that this is _not_
        # the XML actually used by libvirt.
        with open(os.path.join(self.instance_path, 'original_domain.xml'), 'w') as f:
            f.write(x)

        return x

    def is_powered_on(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                return 'off'

            return lc.extract_power_state(inst)

    def power_on(self):
        # Create the actual instance. Sometimes on Ubuntu 20.04 we need to wait
        # for port binding to work. Revisiting this is tracked by issue 320 on
        # github. Additionally, sometimes ports are not released correctly by a
        # domain destroy, which means we need to reassign on domain start.
        if not self._power_on_inner():
            attempts = 0
            while not self._power_on_inner() and attempts < 5:
                self.log.warning(
                    'Instance required an additional attempt to power on')
                time.sleep(5)
                attempts += 1

    def _power_on_inner(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                inst = lc.define_xml(self._create_domain_xml())
                if not inst:
                    self.enqueue_delete_due_error(
                        'power on failed to create domain')
                    raise exceptions.NoDomainException()

            try:
                inst.create()
            except lc.libvirt.libvirtError as e:
                if str(e).startswith('Requested operation is not valid: '
                                     'domain is already running'):
                    pass
                elif str(e).find('Failed to find an available port: '
                                 'Address already in use') != -1:
                    self.add_event2(
                        'instance ports clash during boot attempt: %s' % e)

                    # Free those ports and pick some new ones
                    ports = self.ports
                    self._free_console_port(ports['console_port'])
                    self._free_console_port(ports['vdi_port'])

                    # We need to delete the nvram file before we can undefine
                    # the domain. This will be recreated by libvirt on the next
                    # attempt.
                    nvram_path = os.path.join(self.instance_path, 'nvram')
                    if os.path.exists(nvram_path):
                        os.unlink(nvram_path)

                    inst.undefine()

                    self.ports = None
                    self.allocate_instance_ports()
                    return False
                else:
                    self.add_event2('instance start error',
                                    extra={'message': str(e)})
                    return False

            inst.setAutostart(1)
            self.update_power_state(lc.extract_power_state(inst))
            self.add_event2('poweron')
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

            self.agent_state = 'not ready (instance powered off)'
            self.update_power_state('off')
            self.add_event2('poweroff')

    def reboot(self, hard=False):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                # Not returning a libvirt domain here indicates that the instance
                # is "powered off" (destroyed in libvirt speak). It doesn't make
                # sense to reboot a powered off machine
                raise exceptions.InvalidLifecycleState(
                    'you cannot reboot a powered off instance')

            if not hard:
                inst.reboot(flags=lc.libvirt.VIR_DOMAIN_REBOOT_ACPI_POWER_BTN)
                self.add_event2('soft reboot')
            else:
                inst.reset()
                self.add_event2('hard reboot')

    def pause(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                # Not returning a libvirt domain here indicates that the instance
                # is "powered off" (destroyed in libvirt speak). It doesn't make
                # sense to reboot a powered off machine
                raise exceptions.InvalidLifecycleState(
                    'you cannot pause a powered off instance')

            inst.suspend()
            self.agent_state = 'not ready (instance paused)'
            self.update_power_state(lc.extract_power_state(inst))
            self.add_event2('pause')

    def unpause(self):
        with util_libvirt.LibvirtConnection() as lc:
            inst = lc.get_domain_from_sf_uuid(self.uuid)
            if not inst:
                # Not returning a libvirt domain here indicates that the instance
                # is "powered off" (destroyed in libvirt speak). It doesn't make
                # sense to reboot a powered off machine
                raise exceptions.InvalidLifecycleState(
                    'you cannot unpause a powered off instance')

            inst.resume()
            self.update_power_state(lc.extract_power_state(inst))
            self.add_event2('unpause')

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
        self.add_event2('console log cleared')

    def enqueue_delete_remote(self, node):
        etcd.enqueue(node, {
            'tasks': [DeleteInstanceTask(self.uuid)]
        })

    def enqueue_delete_due_error(self, error_msg):
        # Error needs to be set immediately so that API clients get
        # correct information. The VM and network tear down can be delayed.
        if self.state.value == self.STATE_DELETED:
            return

        try:
            self.state = '%s-error' % self.state.value
        except Exception:
            # We can land here if there is a serious database error.
            self.state = self.STATE_ERROR

        self.error = error_msg
        self.enqueue_delete_remote(config.NODE_NAME)

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
        self.log.with_field('devices', disks).info('Devices for snapshot')

        out = {}
        for disk in disks:
            if disk['snapshot_ignores']:
                continue

            if disk['type'] not in ['qcow2', 'nvram']:
                continue

            if not os.path.exists(disk['path']):
                continue

            a = artifact.Artifact.from_url(
                artifact.Artifact.TYPE_SNAPSHOT,
                '%s%s/%s' % (artifact.INSTANCE_URL, self.uuid, disk['device']),
                max_versions, namespace=self.namespace, create_if_new=True)

            blob_uuid = str(uuid4())
            entry = a.add_index(blob_uuid)

            out[disk['device']] = {
                'source_url': a.source_url,
                'artifact_uuid': a.uuid,
                'artifact_index': entry['index'],
                'blob_uuid': blob_uuid
            }

            if disk['type'] == 'nvram':
                # These are small and don't use qemu-img to capture, so just
                # do them now.
                blob.ensure_blob_path()
                dest_path = blob.Blob.filepath(blob_uuid)
                shutil.copyfile(disk['path'], dest_path)

                st = os.stat(dest_path)
                b = blob.Blob.new(blob_uuid, st.st_size,
                                  time.time(), time.time())
                b.observe()
                a.state = artifact.Artifact.STATE_CREATED

            else:
                etcd.enqueue(config.NODE_NAME, {
                    'tasks': [SnapshotTask(self.uuid, disk, a.uuid, blob_uuid,
                                           thin=thin)],
                })

        self.add_event2('snapshot', extra=out)
        return out


class Instances(dbo_iter):
    def __iter__(self):
        for _, i in etcd.get_all('instance', None):
            out = self.apply_filters(Instance(i))
            if out:
                yield out


def placement_filter(node, inst):
    p = inst.placement
    return p.get('node') == node


this_node_filter = partial(placement_filter, config.NODE_NAME)


active_states_filter = partial(baseobject.state_filter, Instance.ACTIVE_STATES)

healthy_states_filter = partial(
    baseobject.state_filter, [Instance.STATE_INITIAL, Instance.STATE_PREFLIGHT,
                              Instance.STATE_CREATING, Instance.STATE_CREATED])


# Convenience helpers
def healthy_instances_on_node(n):
    return Instances([healthy_states_filter, partial(placement_filter, n.uuid)])


def instances_in_namespace(namespace):
    return Instances([partial(baseobject.namespace_filter, namespace)])
