# Copyright 2019 Michael Still

import base64
import jinja2
import io
import json
import os
import pycdlib
import shutil
import time
from uuid import uuid4

from shakenfist.config import config
from shakenfist import db
from shakenfist import exceptions
from shakenfist import images
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import net
from shakenfist import util


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


def instance_path(instance_uuid):
    return os.path.join(config.get('STORAGE_PATH'), 'instances', instance_uuid)


def _snapshot_path(instance_uuid):
    return os.path.join(config.get('STORAGE_PATH'), 'snapshots')


def _xml_file(instance_uuid):
    return os.path.join(instance_path(instance_uuid), 'libvirt.xml')


class Instance(object):
    def __init__(self, static_values):
        # This dictionary contains values which will never change for this
        # instance. It is therefore safe to cache forever. I know this moves
        # us away from member variables again, but I wanted to make it very
        # clear that these values are not meant to be assigned to.
        self.static_values = static_values

        if not self.static_values.get('disk_spec'):
            # This should not occur since the API will filter for zero disks.
            LOG.withObj(self).error('Found disk spec empty')
            raise exceptions.InstanceBadDiskSpecification()

    @staticmethod
    def new(name, cpus, memory, namespace, ssh_key=None, disk_spec=None,
            user_data=None, video=None, requested_placement=None, uuid=None):

        if not uuid:
            # uuid should only be specified in testing
            uuid = str(uuid4())

        db.create_instance(
            uuid,
            {
                'uuid': uuid,

                'cpus': cpus,
                'disk_spec': disk_spec,
                'memory': memory,
                'name': name,
                'namespace': namespace,
                'requested_placement': requested_placement,
                'ssh_key': ssh_key,
                'user_data': user_data,
                'video': video,

                'version': 2
            })
        i = Instance.from_db(uuid)
        i.add_event('db record creation', None)
        return i

    @staticmethod
    def from_db(uuid):
        if not uuid:
            return None

        static_values = db.get_instance(uuid)
        if not static_values:
            return None

        return Instance(static_values)

    def __str__(self):
        return 'instance(%s)' % self.static_values['uuid']

    def unique_label(self):
        return ('instance', self.static_values['uuid'])

    def add_event(self, operation, phase, duration=None, msg=None):
        db.add_event(
            'instance', self.static_values['uuid'], operation, phase, duration, msg)

    def place_instance(self, location):
        with db.get_lock('attribute/instance', self.static_values['uuid'], 'placement',
                         op='Instance placement'):
            # We don't write unchanged things to the database
            placement = db.get_instance_attribute(
                self.static_values['uuid'], 'placement')
            if placement.get('node') == location:
                return

            placement['node'] = location
            placement['placement_attempts'] = placement.get(
                'placement_attempts', 0) + 1
            db.set_instance_attribute(
                self.static_values['uuid'], 'placement', placement)
            self.add_event('placement', None, None, location)

    def enforced_deletes_increment(self):
        with db.get_lock('attribute/instance', self.static_values['uuid'], 'enforced_deletes',
                         op='Instance enforced deletes increment'):
            enforced_deletes = db.get_instance_attribute(
                self.static_values['uuid'], 'enforced_deletes')
            enforced_deletes['count'] = enforced_deletes.get('count', 0) + 1
            db.set_instance_attribute(
                self.static_values['uuid'], 'enforced_deletes', enforced_deletes)

    def update_instance_state(self, state, error_message=None):
        with db.get_lock('attribute/instance', self.static_values['uuid'], 'state',
                         op='Instance state update'):
            # We don't write unchanged things to the database
            dbstate = db.get_instance_attribute(
                self.static_values['uuid'], 'state')
            if dbstate.get('state') == state:
                return

            orig_state = dbstate.get('state')
            dbstate['state'] = state
            dbstate['state_updated'] = time.time()

            if error_message:
                dbstate['error_message'] = error_message

            db.set_instance_attribute(
                self.static_values['uuid'], 'state', dbstate)
            self.add_event('state changed', '%s -> %s' % (orig_state, state))

    def update_power_state(self, state):
        with db.get_lock('attribute/instance', self.static_values['uuid'], 'power_state',
                         op='Instance power state update'):
            # We don't write unchanged things to the database
            dbstate = db.get_instance_attribute(
                self.static_values['uuid'], 'power_state')
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
            db.set_instance_attribute(
                self.static_values['uuid'], 'power_state', dbstate)
            self.add_event('power state changed', '%s -> %s' %
                           (dbstate['power_state_previous'], state))

    # NOTE(mikal): this method is now strictly the instance specific steps for
    # creation. It is assumed that the image sits in local cache already, and
    # has been transcoded to the right format. This has been done to facilitate
    # moving to a queue and task based creation mechanism.
    def create(self, lock=None):
        self.update_instance_state('creating')

        # Ensure we have state on disk
        os.makedirs(instance_path(self.static_values['uuid']), exist_ok=True)

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
                    LOG.withObj(self).warning(
                        'Instance required an additional attempt to power on')
                    time.sleep(5)
                    attempts += 1

        if self.is_powered_on():
            LOG.withObj(self).info('Instance now powered on')
        else:
            LOG.withObj(self).info('Instance failed to power on')
        self.update_instance_state('created')

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
                if os.path.exists(instance_path(self.static_values['uuid'])):
                    shutil.rmtree(instance_path(self.static_values['uuid']))
            except Exception as e:
                util.ignore_exception('instance delete', e)

        with util.RecordedOperation('release network addresses', self):
            for ni in db.get_instance_interfaces(self.static_values['uuid']):
                db.update_network_interface_state(ni['uuid'], 'deleted')
                with db.get_lock('ipmanager', None, ni['network_uuid'],
                                 ttl=120, op='Instance delete'):
                    ipm = IPManager.from_db(ni['network_uuid'])
                    ipm.release(ni['ipv4'])
                    ipm.persist()

        ports = db.get_instance_attribute(self.static_values['uuid'], 'ports')
        db.free_console_port(ports.get('console_port'))
        db.free_console_port(ports.get('vdi_port'))

        self.update_instance_state('deleted')

    def allocate_instance_ports(self):
        with db.get_lock('attribute/instance', self.static_values['uuid'], 'ports',
                         op='Instance port allocation'):
            ports = db.get_instance_attribute(
                self.static_values['uuid'], 'ports')
            if not ports:
                db.set_instance_attribute(
                    self.static_values['uuid'], 'ports',
                    {
                        'console_port': db.allocate_console_port(self.static_values['uuid']),
                        'vdi_port': db.allocate_console_port(self.static_values['uuid'])
                    })

    def _configure_block_devices(self, lock):
        with db.get_lock('attribute/instance', self.static_values['uuid'], 'block_devices',
                         op='Instance initialize block devices'):
            # Create block devices if required
            block_devices = db.get_instance_attribute(
                self.static_values['uuid'], 'block_devices')
            if not block_devices:
                block_devices = _initialize_block_devices(
                    instance_path(self.static_values['uuid']), self.static_values['disk_spec'])

            # Generate a config drive
            with util.RecordedOperation('make config drive', self):
                self._make_config_drive(
                    os.path.join(instance_path(self.static_values['uuid']),
                                 block_devices['devices'][1]['path']))

            # Prepare disks
            if not block_devices['finalized']:
                modified_disks = []
                for disk in block_devices['devices']:
                    if disk.get('base'):
                        img = images.Image.from_url(disk['base'])
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
                db.set_instance_attribute(
                    self.static_values['uuid'], 'block_devices', block_devices)

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
            'uuid': self.static_values['uuid'],
            'availability_zone': config.get('ZONE'),
            'hostname': '%s.local' % self.static_values['name'],
            'launch_index': 0,
            'devices': [],
            'project_id': None,
            'name': self.static_values['name'],
            'public_keys': {
                'mykey': self.static_values['ssh_key']
            }
        }).encode('ascii')
        iso.add_fp(io.BytesIO(md), len(md), '/openstack/latest/meta_data.json;1',
                   rr_name='meta_data.json',
                   joliet_path='/openstack/latest/meta_data.json')
        iso.add_fp(io.BytesIO(md), len(md), '/openstack/2017-02-22/meta_data.json;2',
                   rr_name='meta_data.json',
                   joliet_path='/openstack/2017-02-22/meta_data.json')

        # user_data
        if self.static_values['user_data']:
            user_data = base64.b64decode(self.static_values['user_data'])
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
        for iface in db.get_instance_interfaces(self.static_values['uuid']):
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

        if os.path.exists(_xml_file(self.static_values['uuid'])):
            return

        with open(os.path.join(config.get('STORAGE_PATH'), 'libvirt.tmpl')) as f:
            t = jinja2.Template(f.read())

        networks = []
        for iface in list(db.get_instance_interfaces(self.static_values['uuid'])):
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
        block_devices = db.get_instance_attribute(
            self.static_values['uuid'], 'block_devices')
        ports = db.get_instance_attribute(self.static_values['uuid'], 'ports')
        xml = t.render(
            uuid=self.static_values['uuid'],
            memory=self.static_values['memory'] * 1024,
            vcpus=self.static_values['cpus'],
            disks=block_devices.get('devices'),
            networks=networks,
            instance_path=instance_path(self.static_values['uuid']),
            console_port=ports.get('console_port'),
            vdi_port=ports.get('vdi_port'),
            video_model=self.static_values['video']['model'],
            video_memory=self.static_values['video']['memory']
        )

        with open(_xml_file(self.static_values['uuid']), 'w') as f:
            f.write(xml)

    def _get_domain(self):
        libvirt = util.get_libvirt()
        conn = libvirt.open('qemu:///system')
        try:
            return conn.lookupByName('sf:' + self.static_values['uuid'])

        except libvirt.libvirtError:
            return None

    def is_powered_on(self):
        instance = self._get_domain()
        if not instance:
            return 'off'

        libvirt = util.get_libvirt()
        return util.extract_power_state(libvirt, instance)

    def power_on(self):
        if not os.path.exists(_xml_file(self.static_values['uuid'])):
            db.enqueue_instance_error(self.static_values['uuid'],
                                      'missing domain file in power on')

        libvirt = util.get_libvirt()
        with open(_xml_file(self.static_values['uuid'])) as f:
            xml = f.read()

        instance = self._get_domain()
        if not instance:
            conn = libvirt.open('qemu:///system')
            instance = conn.defineXML(xml)
            if not instance:
                db.enqueue_instance_error(self.static_values['uuid'],
                                          'power on failed to create domain')
                raise exceptions.NoDomainException()

        try:
            instance.create()
        except libvirt.libvirtError as e:
            err = 'Requested operation is not valid: domain is already running'
            if not str(e).startswith(err):
                LOG.withObj(self).warning('Instance start error: %s' % e)
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
            LOG.withObj(self).error('Failed to delete domain: %s' % e)

        self.update_power_state('off')
        self.add_event('poweroff', 'complete')

    def _snapshot_device(self, source, destination):
        images.snapshot(None, source, destination)

    def snapshot(self, all=False):
        disks = db.get_instance_attribute(
            self.static_values['uuid'], 'block_devices')['devices']
        if not all:
            disks = [disks[0]]

        snapshot_uuid = str(uuid4())
        snappath = os.path.join(_snapshot_path(
            self.static_values['uuid']), snapshot_uuid)
        if not os.path.exists(snappath):
            LOG.withObj(self).debug(
                'Creating snapshot storage at %s' % snappath)
            os.makedirs(snappath, exist_ok=True)
            with open(os.path.join(_snapshot_path(self.static_values['uuid']), 'index.html'), 'w') as f:
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
                db.create_snapshot(snapshot_uuid, d['device'], self.static_values['uuid'],
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
        console_path = os.path.join(instance_path(
            self.static_values['uuid']), 'console.log')
        if not os.path.exists(console_path):
            return ''

        d = None
        file_length = os.stat(console_path).st_size
        with open(console_path, 'rb') as f:
            if length != -1:
                offset = max(0, file_length - length)
                f.seek(offset)
            d = f.read()

        LOG.withObj(self).info(
            'Client requested %d bytes of console log, returning %d bytes'
            % (length, len(d)))
        return d
