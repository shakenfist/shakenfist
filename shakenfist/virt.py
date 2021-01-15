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

from shakenfist import baseobject
from shakenfist.config import config
from shakenfist import db
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import images
from shakenfist import logutil
from shakenfist import net
from shakenfist import util
from shakenfist.tasks import DeleteInstanceTask


LOG, _ = logutil.setup(__name__)


def _get_defaulted_disk_bus(disk):
    bus = disk.get('bus')
    if bus:
        return bus
    return config.get('DISK_BUS')


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
    if config.get('DISK_FORMAT') == 'flat':
        disk_type = 'raw'

    block_devices = {
        'devices': [
            {
                'type': disk_type,
                'size': _safe_int_cast(disk_spec[0].get('size')),
                'device': root_device,
                'bus': bus,
                'path': os.path.join(instance_path, root_device),
                'base': disk_spec[0].get('base'),
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
            'present_as': _get_defaulted_disk_type(d),
            'snapshot_ignores': False
        })
        i += 1

    block_devices['finalized'] = False
    return block_devices


def _snapshot_path():
    return os.path.join(config.get('STORAGE_PATH'), 'snapshots')


class Instance(baseobject.DatabaseBackedObject):
    object_type = 'instance'
    current_version = 2

    # docs/development/state_machine.md has a description of these states.
    STATE_INITIAL = 'initial'
    STATE_INITIAL_ERROR = 'initial-error'
    STATE_PREFLIGHT = 'preflight'
    STATE_PREFLIGHT_ERROR = 'preflight-error'
    STATE_CREATING = 'creating'
    STATE_CREATING_ERROR = 'creating-error'
    STATE_CREATED = 'created'
    STATE_CREATED_ERROR = 'created-error'
    STATE_ERROR = 'error'
    STATE_DELETED = 'deleted'

    state_targets = {
        None: (STATE_INITIAL, STATE_ERROR),
        STATE_INITIAL: (STATE_PREFLIGHT, STATE_DELETED, STATE_INITIAL_ERROR),
        STATE_PREFLIGHT: (STATE_CREATING, STATE_DELETED, STATE_PREFLIGHT_ERROR),
        STATE_CREATING: (STATE_CREATED, STATE_DELETED, STATE_CREATING_ERROR),
        STATE_CREATED: (STATE_DELETED, STATE_CREATED_ERROR),
        STATE_INITIAL_ERROR: (STATE_ERROR),
        STATE_PREFLIGHT_ERROR: (STATE_ERROR),
        STATE_CREATING_ERROR: (STATE_ERROR),
        STATE_CREATED_ERROR: (STATE_ERROR),
        STATE_ERROR: (STATE_DELETED, STATE_ERROR),
        STATE_DELETED: None,
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

        if not self.__disk_spec:
            # This should not occur since the API will filter for zero disks.
            self.log.error('Found disk spec empty')
            raise exceptions.InstanceBadDiskSpecification()

    @classmethod
    def new(cls, name, cpus, memory, namespace, ssh_key=None, disk_spec=None,
            user_data=None, video=None, requested_placement=None, uuid=None):

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
    def instance_path(self):
        return os.path.join(config.get('STORAGE_PATH'), 'instances', self.uuid)

    @property
    def xml_file(self):
        return os.path.join(self.instance_path, 'libvirt.xml')

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

        # Create the actual instance
        with util.RecordedOperation('create domain XML', self):
            self._create_domain_xml()

        # Sometimes on Ubuntu 20.04 we need to wait for port binding to work.
        # Revisiting this is tracked by issue 320 on github.
        with util.RecordedOperation('create domain', self):
            if not self.power_on():
                attempts = 0
                while not self.power_on() and attempts < 100:
                    self.log.warning(
                        'Instance required an additional attempt to power on')
                    time.sleep(5)
                    attempts += 1

        if self.is_powered_on():
            self.log.info('Instance now powered on')
        else:
            self.log.info('Instance failed to power on')
        self.state = self.STATE_CREATED

    def delete(self):
        with util.RecordedOperation('delete domain', self):
            try:
                self.power_off()

                instance = self._get_domain()
                if instance:
                    instance.undefine()
            except Exception as e:
                util.ignore_exception('instance delete', e)

        with util.RecordedOperation('delete disks', self):
            try:
                if os.path.exists(self.instance_path):
                    shutil.rmtree(self.instance_path)
            except Exception as e:
                util.ignore_exception('instance delete', e)

        ports = self.ports
        self._free_console_port(ports.get('console_port'))
        self._free_console_port(ports.get('vdi_port'))

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
        consumed = {value['port']
                    for _, value in etcd.get_all('console', node)}
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
            except socket.error as e:
                LOG.with_field('instance', self.uuid).info(
                    'Exception during port allocation: %s' % e)
            finally:
                s.close()

    def _free_console_port(self, port):
        if port:
            etcd.delete('console', config.NODE_NAME, port)

    def allocate_instance_ports(self):
        with self.get_lock_attr('ports', 'Instance port allocation'):
            ports = self.ports
            if not ports:
                self.ports = {
                    'console_port': self._allocate_console_port(),
                    'vdi_port': self._allocate_console_port()
                }

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

            # Prepare disks
            if not block_devices['finalized']:
                modified_disks = []
                for disk in block_devices['devices']:
                    if disk.get('base'):
                        img = images.Image.new(disk['base'])
                        hashed_image_path = img.version_image_path()

                        with util.RecordedOperation('detect cdrom images', self):
                            try:
                                cd = pycdlib.PyCdlib()
                                cd.open(hashed_image_path)
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
                                os.link(hashed_image_path, disk['path'])
                            except OSError:
                                # Different filesystems
                                util.execute(
                                    [lock], 'cp %s %s' % (hashed_image_path, disk['path']))

                            # Due to limitations in some installers, cdroms are always on IDE
                            disk['device'] = 'hd%s' % disk['device'][-1]
                            disk['bus'] = 'ide'
                        else:
                            if config.get('DISK_FORMAT') == 'qcow':
                                with util.RecordedOperation('create copy on write layer', self):
                                    images.create_cow([lock], hashed_image_path,
                                                      disk['path'], disk['size'])

                                # Record the backing store for modern libvirts
                                disk['backing'] = (
                                    '<backingStore type=\'file\'>\n'
                                    '        <format type=\'qcow2\'/>\n'
                                    '        <source file=\'%s\'/>\n'
                                    '      </backingStore>\n'
                                    % (hashed_image_path))

                            elif config.get('DISK_FORMAT') == 'qcow_flat':
                                with util.RecordedOperation('resize image', self):
                                    resized_image_path = img.resize(
                                        [lock], disk['size'])

                                with util.RecordedOperation('create flat layer', self):
                                    images.create_flat(
                                        [lock], resized_image_path, disk['path'])

                            elif config.get('DISK_FORMAT') == 'flat':
                                with util.RecordedOperation('resize image', self):
                                    resized_image_path = img.resize(
                                        [lock], disk['size'])

                                with util.RecordedOperation('create raw disk', self):
                                    images.create_raw(
                                        [lock], resized_image_path, disk['path'])

                            else:
                                raise Exception('Unknown disk format')

                    elif not os.path.exists(disk['path']):
                        util.execute(None, 'qemu-img create -f qcow2 %s %sG'
                                     % (disk['path'], disk['size']))

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

        # meta_data.json
        md = json.dumps({
            'random_seed': base64.b64encode(os.urandom(512)).decode('ascii'),
            'uuid': self.uuid,
            'availability_zone': config.get('ZONE'),
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
                    'address': '8.8.8.8',
                    'type': 'dns'
                }
            ]
        }

        seen_networks = []
        for iface in db.get_instance_interfaces(self.uuid):
            devname = 'eth%d' % iface['order']
            nd['links'].append(
                {
                    'ethernet_mac_address': iface['macaddr'],
                    'id': devname,
                    'name': devname,
                    'mtu': 1450,
                    'type': 'vif',
                    'vif_id': iface['uuid']
                }
            )

            if not iface['network_uuid'] in seen_networks:
                n = net.Network.from_db(iface['network_uuid'])
                nd['networks'].append(
                    {
                        'id': iface['network_uuid'],
                        'link': devname,
                        'type': 'ipv4',
                        'ip_address': iface['ipv4'],
                        'netmask': str(n.netmask),
                        'routes': [
                            {
                                'network': '0.0.0.0',
                                'netmask': '0.0.0.0',
                                'gateway': str(n.router)
                            }
                        ],
                        'network_id': iface['network_uuid']
                    }
                )
                seen_networks.append(iface['network_uuid'])

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

        if os.path.exists(self.xml_file):
            return

        with open(os.path.join(config.get('STORAGE_PATH'), 'libvirt.tmpl')) as f:
            t = jinja2.Template(f.read())

        networks = []
        for iface in list(db.get_instance_interfaces(self.uuid)):
            n = net.Network.from_db(iface['network_uuid'])
            networks.append(
                {
                    'macaddr': iface['macaddr'],
                    'bridge': n.subst_dict()['vx_bridge'],
                    'model': iface['model']
                }
            )

        # NOTE(mikal): the database stores memory allocations in MB, but the
        # domain XML takes them in KB. That wouldn't be worth a comment here if
        # I hadn't spent _ages_ finding a bug related to it.
        block_devices = self.block_devices
        ports = self.ports
        xml = t.render(
            uuid=self.uuid,
            memory=self.memory * 1024,
            vcpus=self.cpus,
            disks=block_devices.get('devices'),
            networks=networks,
            instance_path=self.instance_path,
            console_port=ports.get('console_port'),
            vdi_port=ports.get('vdi_port'),
            video_model=self.video['model'],
            video_memory=self.video['memory']
        )

        with open(self.xml_file, 'w') as f:
            f.write(xml)

    def _get_domain(self):
        libvirt = util.get_libvirt()
        conn = libvirt.open('qemu:///system')
        try:
            return conn.lookupByName('sf:' + self.uuid)

        except libvirt.libvirtError:
            return None

    def is_powered_on(self):
        instance = self._get_domain()
        if not instance:
            return 'off'

        libvirt = util.get_libvirt()
        return util.extract_power_state(libvirt, instance)

    def power_on(self):
        if not os.path.exists(self.xml_file):
            self.enqueue_delete_due_error('missing domain file in power on')

        libvirt = util.get_libvirt()
        with open(self.xml_file) as f:
            xml = f.read()

        instance = self._get_domain()
        if not instance:
            conn = libvirt.open('qemu:///system')
            instance = conn.defineXML(xml)
            if not instance:
                self.enqueue_delete_due_error(
                    'power on failed to create domain')
                raise exceptions.NoDomainException()

        try:
            instance.create()
        except libvirt.libvirtError as e:
            err = 'Requested operation is not valid: domain is already running'
            if not str(e).startswith(err):
                self.log.warning('Instance start error: %s', e)
                return False

        instance.setAutostart(1)
        self.update_power_state(util.extract_power_state(libvirt, instance))
        self.add_event('poweron', 'complete')
        return True

    def power_off(self):
        libvirt = util.get_libvirt()
        instance = self._get_domain()
        if not instance:
            return

        try:
            instance.destroy()
        except libvirt.libvirtError as e:
            self.log.error('Failed to delete domain: %s', e)

        self.update_power_state('off')
        self.add_event('poweroff', 'complete')

    def _snapshot_device(self, source, destination):
        images.snapshot(None, source, destination)

    def snapshot(self, all=False):
        disks = self.block_devices['devices']
        if not all:
            disks = [disks[0]]

        snapshot_uuid = str(uuid4())
        snappath = os.path.join(_snapshot_path(), snapshot_uuid)
        if not os.path.exists(snappath):
            self.log.debug('Creating snapshot storage at %s', snappath)
            os.makedirs(snappath, exist_ok=True)
            with open(os.path.join(_snapshot_path(), 'index.html'), 'w') as f:
                f.write('<html></html>')

        for d in disks:
            if not os.path.exists(d['path']):
                continue

            if d['snapshot_ignores']:
                continue

            if d['type'] != 'qcow2':
                continue

            with util.RecordedOperation('snapshot %s' % d['device'], self):
                self._snapshot_device(
                    d['path'], os.path.join(snappath, d['device']))
                db.create_snapshot(snapshot_uuid, d['device'], self.uuid,
                                   time.time())

        return snapshot_uuid

    def reboot(self, hard=False):
        libvirt = util.get_libvirt()
        instance = self._get_domain()
        if not hard:
            instance.reboot(flags=libvirt.VIR_DOMAIN_REBOOT_ACPI_POWER_BTN)
        else:
            instance.reset()
        self.add_event('reboot', 'complete')

    def pause(self):
        libvirt = util.get_libvirt()
        instance = self._get_domain()
        instance.suspend()
        self.update_power_state(util.extract_power_state(libvirt, instance))
        self.add_event('pause', 'complete')

    def unpause(self):
        libvirt = util.get_libvirt()
        instance = self._get_domain()
        instance.resume()
        self.update_power_state(util.extract_power_state(libvirt, instance))
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
        self.log.info('enqueue_instance_error')

        # Error needs to be set immediately so that API clients get
        # correct information. The VM and network tear down can be delayed.
        self.state = '%s-error' % self.state.value
        self.error = error_msg
        self.enqueue_delete()


# TODO(mikal): can this be refactored into baseobject?
class Instances(object):
    def __init__(self, filters):
        self.filters = filters

    def __iter__(self):
        for _, i in etcd.get_all('instance', None):
            i = Instance.from_db(i['uuid'])
            if not i:
                continue

            skip = False
            for f in self.filters:
                # If a filter returns false, we remove the instance from
                # the result set.
                if not f(i):
                    skip = True
                    break

            if not skip:
                yield i


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
