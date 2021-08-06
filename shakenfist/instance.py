# Copyright 2019 Michael Still

import base64
from functools import partial
import jinja2
import io
import json
import os
import pycdlib
import random
import shutil
import socket
import time
from uuid import uuid4

from shakenfist.artifact import BLOB_URL
from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist import db
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import images
from shakenfist import logutil
from shakenfist import net
from shakenfist import networkinterface
from shakenfist import util
from shakenfist.tasks import DeleteInstanceTask


LOG, _ = logutil.setup(__name__)


def _get_defaulted_disk_bus(disk):
    bus = disk.get('bus')
    if bus:
        return bus
    return config.DISK_BUS


def _get_disk_device_base(bus):
    bases = {
        'ide': 'hd',
        'scsi': 'sd',
        'usb': 'sd',
        'virtio': 'vd'
    }
    return bases.get(bus, 'sd')


def _get_defaulted_disk_type(disk):
    kind = disk.get('type')
    if kind:
        return kind
    return 'disk'


def _safe_int_cast(i):
    if i:
        return int(i)
    return i


def _initialize_block_devices(instance_path, disk_spec):
    bus = _get_defaulted_disk_bus(disk_spec[0])
    root_device = _get_disk_device_base(bus) + 'a'
    config_device = _get_disk_device_base(bus) + 'b'

    disk_type = 'qcow2'

    block_devices = {
        'devices': [
            {
                'type': disk_type,
                'size': _safe_int_cast(disk_spec[0].get('size')),
                'device': root_device,
                'bus': bus,
                'path': os.path.join(instance_path, root_device),
                'base': disk_spec[0].get('base'),
                'blob_uuid': disk_spec[0].get('blob_uuid'),
                'present_as': _get_defaulted_disk_type(disk_spec[0]),
                'snapshot_ignores': False
            },
            {
                'type': 'raw',
                'device': config_device,
                'bus': bus,
                'path': os.path.join(instance_path, config_device),
                'present_as': 'disk',
                'snapshot_ignores': True
            }
        ]
    }

    i = 0
    for d in disk_spec[1:]:
        bus = _get_defaulted_disk_bus(d)
        device = _get_disk_device_base(bus) + chr(ord('c') + i)
        block_devices['devices'].append({
            'type': disk_type,
            'size': _safe_int_cast(d.get('size')),
            'device': device,
            'bus': bus,
            'path': os.path.join(instance_path, device),
            'base': d.get('base'),
            'blob_uuid': d.get('blob_uuid'),
            'present_as': _get_defaulted_disk_type(d),
            'snapshot_ignores': False
        })
        i += 1

    block_devices['finalized'] = False
    return block_devices


def _blob_path():
    blob_path = os.path.join(config.STORAGE_PATH, 'blobs')
    os.makedirs(blob_path, exist_ok=True)
    return blob_path


class Instance(dbo):
    object_type = 'instance'
    current_version = 3

    # docs/development/state_machine.md has a description of these states.
    STATE_INITIAL_ERROR = 'initial-error'
    STATE_PREFLIGHT = 'preflight'
    STATE_PREFLIGHT_ERROR = 'preflight-error'
    STATE_CREATING_ERROR = 'creating-error'
    STATE_CREATED_ERROR = 'created-error'

    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_ERROR),
        dbo.STATE_INITIAL: (STATE_PREFLIGHT, dbo.STATE_DELETED, STATE_INITIAL_ERROR),
        STATE_PREFLIGHT: (dbo.STATE_CREATING, dbo.STATE_DELETED, STATE_PREFLIGHT_ERROR),
        dbo.STATE_CREATING: (dbo.STATE_CREATED, dbo.STATE_DELETED, STATE_CREATING_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, STATE_CREATED_ERROR),
        STATE_INITIAL_ERROR: (dbo.STATE_ERROR),
        STATE_PREFLIGHT_ERROR: (dbo.STATE_ERROR),
        STATE_CREATING_ERROR: (dbo.STATE_ERROR),
        STATE_CREATED_ERROR: (dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: None,
    }

    def __init__(self, static_values):
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

        if not self.__disk_spec:
            # This should not occur since the API will filter for zero disks.
            self.log.error('Found disk spec empty')
            raise exceptions.InstanceBadDiskSpecification()

    @classmethod
    def new(cls, name, cpus, memory, namespace, ssh_key=None, disk_spec=None,
            user_data=None, video=None, requested_placement=None, uuid=None,
            uefi=False):

        if not uuid:
            # uuid should only be specified in testing
            uuid = str(uuid4())

        Instance._db_create(
            uuid,
            {
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

                'version': cls.current_version
            })
        i = Instance.from_db(uuid)
        i.state = cls.STATE_INITIAL
        i._db_set_attribute(
            'power_state', {'power_state': cls.STATE_INITIAL})
        i.add_event('db record creation', None)
        return i

    @staticmethod
    def from_db(uuid):
        if not uuid:
            return None

        static_values = Instance._db_get(uuid)
        if not static_values:
            return None

        return Instance(static_values)

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

        for attrname in ['placement', 'state', 'power_state', 'ports']:
            d = self._db_get_attribute(attrname)
            for key in d:
                if key not in external_attribute_key_whitelist:
                    continue

                # We skip keys with no value
                if d[key] is None:
                    continue

                i[key] = d[key]

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
    def instance_path(self):
        return os.path.join(config.STORAGE_PATH, 'instances', self.uuid)

    # Values routed to attributes, writes are via helper methods.
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

    # Implementation
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
            self.add_event('placement', None, None, location)

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
            self.add_event('power state changed', '%s -> %s' %
                           (dbstate['power_state_previous'], state))

    # NOTE(mikal): this method is now strictly the instance specific steps for
    # creation. It is assumed that the image sits in local cache already, and
    # has been transcoded to the right format. This has been done to facilitate
    # moving to a queue and task based creation mechanism.
    def create(self, lock=None):
        self.state = self.STATE_CREATING

        # Ensure we have state on disk
        os.makedirs(self.instance_path, exist_ok=True)

        # Configure block devices, include config drive creation
        self._configure_block_devices(lock)

        # Create the actual instance. Sometimes on Ubuntu 20.04 we need to wait
        # for port binding to work. Revisiting this is tracked by issue 320 on
        # github.
        with util.RecordedOperation('create domain', self):
            if not self.power_on():
                attempts = 0
                while not self.power_on() and attempts < 5:
                    self.log.warning(
                        'Instance required an additional attempt to power on')
                    time.sleep(5)
                    attempts += 1

        if self.is_powered_on():
            self.log.info('Instance now powered on')
            self.state = self.STATE_CREATED
        else:
            self.log.info('Instance failed to power on')
            self.enqueue_delete_due_error('Instance failed to power on')

    def delete(self):
        with util.RecordedOperation('delete domain', self):
            try:
                self.power_off()

                nvram_path = os.path.join(
                    config.STORAGE_PATH, 'instances', self.uuid,
                    'nvram')
                if os.path.exists(nvram_path):
                    os.unlink(nvram_path)

                inst = self._get_domain()
                if inst:
                    inst.undefine()
            except Exception as e:
                util.ignore_exception('instance delete', e)

        with util.RecordedOperation('delete disks', self):
            try:
                if os.path.exists(self.instance_path):
                    shutil.rmtree(self.instance_path)
            except Exception as e:
                util.ignore_exception('instance delete', e)

        self.deallocate_instance_ports()

        if self.state.value.endswith('-%s' % self.STATE_ERROR):
            self.state = self.STATE_ERROR
        else:
            self.state = self.STATE_DELETED

    def hard_delete(self):
        etcd.delete('instance', None, self.uuid)
        db.delete_metadata('instance', self.uuid)
        etcd.delete_all('attribute/instance', self.uuid)
        etcd.delete_all('event/instance', self.uuid)

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
                self.log.with_fields(p).info('Console ports allocated')

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
                block_devices = _initialize_block_devices(self.instance_path,
                                                          self.disk_spec)

            # Generate a config drive
            with util.RecordedOperation('make config drive', self):
                self._make_config_drive(
                    os.path.join(self.instance_path,
                                 block_devices['devices'][1]['path']))

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
                        disk_base = '%s%s' % (BLOB_URL, disk['blob_uuid'])
                    elif disk.get('base'):
                        img = images.Image.new(disk['base'])
                        disk['blob_uuid'] = \
                            img.artifact().most_recent_index['blob_uuid']
                        disk_base = '%s%s' % (BLOB_URL, disk['blob_uuid'])

                    if disk_base:
                        img = images.Image.new(disk_base)
                        cached_image_path = os.path.join(
                            config.STORAGE_PATH, 'image_cache', disk['blob_uuid'] + '.qcow2')

                        with util.RecordedOperation('detect cdrom images', self):
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

                            try:
                                os.link(cached_image_path, disk['path'])
                            except OSError:
                                os.symlink(cached_image_path, disk['path'])

                            # Due to limitations in some installers, cdroms are always on IDE
                            disk['device'] = 'hd%s' % disk['device'][-1]
                            disk['bus'] = 'ide'
                        else:
                            with util.RecordedOperation('create copy on write layer', self):
                                images.create_cow([lock], cached_image_path,
                                                  disk['path'], disk['size'])
                            shutil.chown(
                                disk['path'], 'libvirt-qemu', 'libvirt-qemu')
                            self.log.with_fields(util.stat_log_fields(disk['path'])).info(
                                'COW layer %s created' % disk['path'])

                            # Record the backing store for modern libvirts
                            disk['backing'] = (
                                '<backingStore type=\'file\'>\n'
                                '        <format type=\'qcow2\'/>\n'
                                '        <source file=\'%s\'/>\n'
                                '      </backingStore>\n'
                                % (cached_image_path))

                    elif not os.path.exists(disk['path']):
                        images.create_blank([lock], disk['path'], disk['size'])

                    modified_disks.append(disk)

                block_devices['devices'] = modified_disks
                block_devices['finalized'] = True
                self._db_set_attribute('block_devices', block_devices)

    def _make_config_drive(self, disk_path):
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
        for iface in networkinterface.interfaces_for_instance(self):
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

            n = net.Network.from_db(iface.network_uuid)
            nd['networks'].append(
                {
                    'id': '%s-%s' % (iface.network_uuid, iface.order),
                    'link': devname,
                    'type': 'ipv4',
                    'network_id': iface.network_uuid
                }
            )

            if iface.ipv4:
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

        with open(os.path.join(config.STORAGE_PATH, 'libvirt.tmpl')) as f:
            t = jinja2.Template(f.read())

        networks = []
        for ni in networkinterface.interfaces_for_instance(self):
            n = net.Network.from_db(ni.network_uuid)
            networks.append(
                {
                    'macaddr': ni.macaddr,
                    'bridge': n.subst_dict()['vx_bridge'],
                    'model': ni.model
                }
            )

        # NOTE(mikal): the database stores memory allocations in MB, but the
        # domain XML takes them in KB. That wouldn't be worth a comment here if
        # I hadn't spent _ages_ finding a bug related to it.
        block_devices = self.block_devices
        ports = self.ports
        return t.render(
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
            uefi=self.uefi
        )

    def _get_domain(self):
        libvirt = util.get_libvirt()
        conn = libvirt.open('qemu:///system')
        try:
            return conn.lookupByName('sf:' + self.uuid)

        except libvirt.libvirtError:
            return None

    def is_powered_on(self):
        inst = self._get_domain()
        if not inst:
            return 'off'

        libvirt = util.get_libvirt()
        return util.extract_power_state(libvirt, inst)

    def power_on(self):
        libvirt = util.get_libvirt()
        inst = self._get_domain()
        if not inst:
            conn = libvirt.open('qemu:///system')
            inst = conn.defineXML(self._create_domain_xml())
            if not inst:
                self.enqueue_delete_due_error(
                    'power on failed to create domain')
                raise exceptions.NoDomainException()

        try:
            inst.create()
        except libvirt.libvirtError as e:
            if str(e).startswith('Requested operation is not valid: '
                                 'domain is already running'):
                pass
            elif str(e).find('Failed to find an available port: '
                             'Address already in use') != -1:
                self.log.warning('Instance ports clash: %s', e)

                # Free those ports and pick some new ones
                ports = self.ports
                self._free_console_port(ports['console_port'])
                self._free_console_port(ports['vdi_port'])
                inst.undefine()

                self.ports = None
                self.allocate_instance_ports()
                return False
            else:
                self.log.warning('Instance start error: %s', e)
                return False

        inst.setAutostart(1)
        self.update_power_state(util.extract_power_state(libvirt, inst))
        self.add_event('poweron', 'complete')
        return True

    def power_off(self):
        libvirt = util.get_libvirt()
        inst = self._get_domain()
        if not inst:
            return

        try:
            inst.destroy()
        except libvirt.libvirtError as e:
            self.log.error('Failed to delete domain: %s', e)

        self.update_power_state('off')
        self.add_event('poweroff', 'complete')

    def reboot(self, hard=False):
        libvirt = util.get_libvirt()
        inst = self._get_domain()
        if not hard:
            inst.reboot(flags=libvirt.VIR_DOMAIN_REBOOT_ACPI_POWER_BTN)
        else:
            inst.reset()
        self.add_event('reboot', 'complete')

    def pause(self):
        libvirt = util.get_libvirt()
        inst = self._get_domain()
        inst.suspend()
        self.update_power_state(util.extract_power_state(libvirt, inst))
        self.add_event('pause', 'complete')

    def unpause(self):
        libvirt = util.get_libvirt()
        inst = self._get_domain()
        inst.resume()
        self.update_power_state(util.extract_power_state(libvirt, inst))
        self.add_event('unpause', 'complete')

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

        self.log.info(
            'Client requested %d bytes of console log, returning %d bytes',
            length, len(d))
        return d

    def enqueue_delete(self):
        self.enqueue_delete_remote(config.NODE_NAME)

    def enqueue_delete_remote(self, node):
        db.enqueue(node, {
            'tasks': [DeleteInstanceTask(self.uuid)]
        })

    def enqueue_delete_due_error(self, error_msg):
        self.log.with_field('error', error_msg).info('enqueue_instance_error')

        # Error needs to be set immediately so that API clients get
        # correct information. The VM and network tear down can be delayed.
        try:
            self.state = '%s-error' % self.state.value
        except Exception:
            # We can land here if there is a serious database error.
            self.state = self.STATE_ERROR

        self.error = error_msg
        self.enqueue_delete()


class Instances(dbo_iter):
    def __iter__(self):
        for _, i in etcd.get_all('instance', None):
            i = Instance.from_db(i['uuid'])
            if not i:
                continue

            out = self.apply_filters(i)
            if out:
                yield out


def placement_filter(node, inst):
    p = inst.placement
    return p.get('node') == node


this_node_filter = partial(placement_filter, config.NODE_NAME)


active_states_filter = partial(
    baseobject.state_filter, [Instance.STATE_INITIAL, Instance.STATE_INITIAL_ERROR,
                              Instance.STATE_PREFLIGHT, Instance.STATE_PREFLIGHT_ERROR,
                              Instance.STATE_CREATING, Instance.STATE_CREATING_ERROR,
                              Instance.STATE_CREATED, Instance.STATE_CREATED_ERROR,
                              Instance.STATE_ERROR])

healthy_states_filter = partial(
    baseobject.state_filter, [Instance.STATE_INITIAL, Instance.STATE_PREFLIGHT,
                              Instance.STATE_CREATING, Instance.STATE_CREATED])

inactive_states_filter = partial(
    baseobject.state_filter, [Instance.STATE_DELETED])


# Convenience helpers

def inactive_instances():
    return Instances([
        inactive_states_filter,
        partial(baseobject.state_age_filter, config.CLEANER_DELAY)])


def healthy_instances_on_node(n):
    return Instances([healthy_states_filter, partial(placement_filter, n.uuid)])


def created_instances_on_node():
    return Instances([this_node_filter, partial(baseobject.state_filter, [dbo.STATE_CREATED])])


def instances_in_namespace(namespace):
    return Instances([partial(baseobject.namespace_filter, namespace)])
