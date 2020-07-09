# Copyright 2019 Michael Still

import base64
import errno
import jinja2
import logging
from logging import handlers as logging_handlers
import io
import json
import os
import pycdlib
import shutil
import time
import uuid

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist import db
from shakenfist import db
from shakenfist import images
from shakenfist import net
from shakenfist import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


def from_definition(uuid=None, name=None, disks=None, memory_mb=None,
                    vcpus=None, ssh_key=None, user_data=None, owner=None):
    db_entry = db.create_instance(uuid, name, vcpus, memory_mb, disks,
                                  ssh_key, user_data, owner)
    return Instance(db_entry)


def from_db(uuid):
    db_entry = db.get_instance(uuid)
    if not db_entry:
        return None
    return Instance(db_entry)


def _get_defaulted_disk_bus(disk):
    bus = disk.get('bus')
    if bus:
        return bus
    return config.parsed.get('DISK_BUS')


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


class Instance(object):
    def __init__(self, db_entry):
        self.db_entry = db_entry
        self.tenant = None

        self.instance_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'instances', self.db_entry['uuid'])
        self.snapshot_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'snapshots')
        self.xml_file = os.path.join(self.instance_path, 'libvirt.xml')

        if not self.db_entry['block_devices']:
            self._populate_block_devices()

    def __str__(self):
        return 'instance(%s)' % self.db_entry['uuid']

    def get_describing_tuple(self):
        return ('instance', self.db_entry['uuid'])

    def _populate_block_devices(self):
        bus = _get_defaulted_disk_bus(self.db_entry['disk_spec'][0])
        root_device = _get_disk_device_base(bus) + 'a'
        config_device = _get_disk_device_base(bus) + 'b'

        self.db_entry['block_devices'] = {
            'devices': [
                {
                    'type': 'qcow2',
                    'size': self.db_entry['disk_spec'][0].get('size'),
                    'device': root_device,
                    'bus': bus,
                    'path': os.path.join(self.instance_path, root_device + '.qcow2'),
                    'base': self.db_entry['disk_spec'][0].get('base'),
                    'present_as': _get_defaulted_disk_type(self.db_entry['disk_spec'][0]),
                    'snapshot_ignores': False
                },
                {
                    'type': 'raw',
                    'device': config_device,
                    'bus': bus,
                    'path': os.path.join(self.instance_path, config_device + '.raw'),
                    'present_as': 'disk',
                    'snapshot_ignores': True
                }
            ]
        }

        i = 0
        for d in self.db_entry['disk_spec'][1:]:
            bus = _get_defaulted_disk_bus(d)
            device = _get_disk_device_base(bus) + chr(ord('c') + i)
            self.db_entry['block_devices']['devices'].append({
                'type': 'qcow2',
                'size': d.get('size'),
                'device': device,
                'bus': bus,
                'path': os.path.join(self.instance_path, device + '.qcow2'),
                'base': d.get('base'),
                'present_as': _get_defaulted_disk_type(d),
                'snapshot_ignores': False
            })
            i += 1

        self.db_entry['block_devices']['finalized'] = False

    def create(self, lock=None):
        # Ensure we have state on disk
        if not os.path.exists(self.instance_path):
            LOG.debug('%s: Creating instance storage at %s' %
                      (self, self.instance_path))
            os.makedirs(self.instance_path)

        # Generate a config drive
        with util.RecordedOperation('make config drive', self) as _:
            self._make_config_drive(os.path.join(
                self.instance_path, self.db_entry['block_devices']['devices'][1]['path']))

        # Prepare disks
        if not self.db_entry['block_devices']['finalized']:
            modified_disks = []
            for disk in self.db_entry['block_devices']['devices']:
                if disk.get('base'):
                    with util.RecordedOperation('fetch image', self) as _:
                        image_url = images.resolve(disk['base'])
                        hashed_image_path, info, image_dirty, resp = \
                            images.requires_fetch(image_url)

                        if image_dirty:
                            hashed_image_path = images.fetch(hashed_image_path,
                                                             info, resp, lock=lock)
                        else:
                            hashed_image_path = '%s.v%03d' % (
                                hashed_image_path, info['version'])

                    try:
                        cd = pycdlib.PyCdlib()
                        cd.open(hashed_image_path)
                        disk['present_as'] = 'cdrom'
                    except Exception:
                        pass

                    if disk.get('present_as', 'cdrom') == 'cdrom':
                        # There is no point in resizing or COW'ing a cdrom
                        disk['path'] = disk['path'].replace('.qcow2', '.raw')
                        disk['type'] = 'raw'
                        disk['snapshot_ignores'] = True

                        try:
                            os.link(hashed_image_path, disk['path'])
                        except OSError:
                            # Different filesystems
                            if lock:
                                lock.refresh()

                            shutil.copyfile(hashed_image_path, disk['path'])

                            if lock:
                                lock.refresh()

                        # Due to limitations in some installers, cdroms are always on IDE
                        disk['device'] = 'hd%s' % disk['device'][-1]
                        disk['bus'] = 'ide'
                    else:
                        with util.RecordedOperation('transcode image', self) as _:
                            if lock:
                                lock.refresh()

                            images.transcode(hashed_image_path)

                        with util.RecordedOperation('resize image', self) as _:
                            if lock:
                                lock.refresh()

                            resized_image_path = images.resize(
                                hashed_image_path, disk['size'])

                        with util.RecordedOperation('create copy on write layer', self) as _:
                            if lock:
                                lock.refresh

                            images.create_cow(resized_image_path, disk['path'])

                elif not os.path.exists(disk['path']):
                    processutils.execute('qemu-img create -f qcow2 %s %sG'
                                         % (disk['path'], disk['size']),
                                         shell=True)

                modified_disks.append(disk)

            self.db_entry['block_devices']['devices'] = modified_disks
            self.db_entry['block_devices']['finalized'] = True

        db.persist_block_devices(
            self.db_entry['uuid'], self.db_entry['block_devices'])

        # Create the actual instance
        with util.RecordedOperation('create domain XML', self) as _:
            self._create_domain_xml()
        with util.RecordedOperation('create domain', self) as _:
            self.power_on()

        db.update_instance_state(self.db_entry['uuid'], 'created')

    def delete(self):
        with util.RecordedOperation('delete domain', self) as _:
            try:
                self.power_off()

                instance = self._get_domain()
                instance.undefine()
            except Exception:
                pass

        with util.RecordedOperation('delete disks', self) as _:
            try:
                shutil.rmtree(self.instance_path)
            except Exception:
                pass

        with util.RecordedOperation('release network addreses', self) as _:
            for ni in db.get_instance_interfaces(self.db_entry['uuid']):
                with db.get_lock('sf/ipmanager/%s' % ni['network_uuid'],
                                 ttl=120) as _:
                    ipm = db.get_ipmanager(ni['network_uuid'])
                    ipm.release(ni['ipv4'])
                    db.persist_ipmanager(ni['network_uuid'], ipm.save())

        db.update_instance_state(self.db_entry['uuid'], 'deleted')
        db.free_console_port(self.db_entry['console_port'])
        db.free_console_port(self.db_entry['vdi_port'])

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
            'uuid': self.db_entry['uuid'],
            'availability_zone': config.parsed.get('ZONE'),
            'hostname': '%s.local' % self.db_entry['name'],
            'launch_index': 0,
            'devices': [],
            'project_id': self.tenant,
            'name': self.db_entry['name'],
            'public_keys': {
                'mykey': self.db_entry['ssh_key']
            }
        }).encode('ascii')
        iso.add_fp(io.BytesIO(md), len(md), '/openstack/latest/meta_data.json;1',
                   rr_name='meta_data.json',
                   joliet_path='/openstack/latest/meta_data.json')
        iso.add_fp(io.BytesIO(md), len(md), '/openstack/2017-02-22/meta_data.json;2',
                   rr_name='meta_data.json',
                   joliet_path='/openstack/2017-02-22/meta_data.json')

        # user_data
        if self.db_entry['user_data']:
            user_data = base64.b64decode(self.db_entry['user_data'])
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
        for iface in db.get_instance_interfaces(self.db_entry['uuid']):
            devname = 'eth%d' % iface['order']
            nd['links'].append(
                {
                    'ethernet_mac_address': iface['macaddr'],
                    'id': devname,
                    'name': devname,
                    'mtu': 1450,
                    'type': 'physical',
                }
            )

            if not iface['network_uuid'] in seen_networks:
                n = net.from_db(iface['network_uuid'])
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
        iso.add_fp(io.BytesIO(nd_encoded), len(nd_encoded), '/openstack/latest/network_data.json;3',
                   rr_name='network_data.json',
                   joliet_path='/openstack/latest/vendor_data.json')
        iso.add_fp(io.BytesIO(nd_encoded), len(nd_encoded), '/openstack/2017-02-22/network_data.json;4',
                   rr_name='network_data.json',
                   joliet_path='/openstack/2017-02-22/vendor_data.json')

        # empty vendor_data.json and vendor_data2.json
        vd = '{}'.encode('ascii')
        iso.add_fp(io.BytesIO(vd), len(vd), '/openstack/latest/vendor_data.json;5',
                   rr_name='vendor_data.json',
                   joliet_path='/openstack/latest/vendor_data.json')
        iso.add_fp(io.BytesIO(vd), len(vd), '/openstack/2017-02-22/vendor_data.json;6',
                   rr_name='vendor_data.json',
                   joliet_path='/openstack/2017-02-22/vendor_data.json')
        iso.add_fp(io.BytesIO(vd), len(vd), '/openstack/latest/vendor_data2.json;7',
                   rr_name='vendor_data2.json',
                   joliet_path='/openstack/latest/vendor_data2.json')
        iso.add_fp(io.BytesIO(vd), len(vd), '/openstack/2017-02-22/vendor_data2.json;8',
                   rr_name='vendor_data2.json',
                   joliet_path='/openstack/2017-02-22/vendor_data2.json')

        # Dump to disk
        iso.write(disk_path)
        iso.close()

    def _create_domain_xml(self):
        """Create the domain XML for the instance."""

        if os.path.exists(self.xml_file):
            return

        with open(os.path.join(config.parsed.get('STORAGE_PATH'), 'libvirt.tmpl')) as f:
            t = jinja2.Template(f.read())

        networks = []
        for iface in list(db.get_instance_interfaces(self.db_entry['uuid'])):
            n = net.from_db(iface['network_uuid'])
            networks.append(
                {
                    'macaddr': iface['macaddr'],
                    'bridge': n.subst_dict()['vx_bridge'],
                    'model': iface['model']
                }
            )

        # NOTE(mikal): the database stores memory allocations in MB, but the domain
        # XML takes them in KB. That wouldn't be worth a comment here if I hadn't
        # spent _ages_ finding a bug related to it.
        xml = t.render(
            uuid=self.db_entry['uuid'],
            memory=self.db_entry['memory'] * 1024,
            vcpus=self.db_entry['cpus'],
            disks=self.db_entry['block_devices']['devices'],
            networks=networks,
            instance_path=self.instance_path,
            console_port=self.db_entry['console_port'],
            vdi_port=self.db_entry['vdi_port']
        )

        with open(self.xml_file, 'w') as f:
            f.write(xml)

    def _get_domain(self):
        libvirt = util.get_libvirt()
        conn = libvirt.open(None)
        try:
            return conn.lookupByName('sf:' + self.db_entry['uuid'])

        except libvirt.libvirtError:
            LOG.error('%s: Failed to lookup domain' % self)
            return None

    def power_on(self):
        libvirt = util.get_libvirt()
        with open(self.xml_file) as f:
            xml = f.read()

        instance = self._get_domain()
        if not instance:
            conn = libvirt.open(None)
            instance = conn.defineXML(xml)
            if not instance:
                LOG.error('%s: Failed to create libvirt domain' % self)
                return

        try:
            instance.create()
        except libvirt.libvirtError:
            pass

        instance.setAutostart(1)

    def power_off(self):
        libvirt = util.get_libvirt()
        with open(self.xml_file) as f:
            xml = f.read()

        instance = self._get_domain()
        if not instance:
            conn = libvirt.open(None)
            instance = conn.defineXML(xml)
            if not instance:
                LOG.error('%s: Failed to create libvirt domain' % self)
                return

        try:
            instance.destroy()
        except libvirt.libvirtError as e:
            LOG.error('%s: Failed to delete domain: %s' % (self, e))

    def _snapshot_device(self, source, destination):
        images.snapshot(source, destination)

    def snapshot(self, all=False):
        disks = self.db_entry['block_devices']['devices']
        if not all:
            disks = [disks[0]]

        snapshot_uuid = str(uuid.uuid4())
        snappath = os.path.join(self.snapshot_path, snapshot_uuid)
        if not os.path.exists(snappath):
            LOG.debug('%s: Creating snapshot storage at %s' %
                      (self, snappath))
            os.makedirs(snappath)
            with open(os.path.join(self.snapshot_path, 'index.html'), 'w') as f:
                f.write('<html></html>')

        for d in disks:
            if not os.path.exists(d['path']):
                continue

            if d['snapshot_ignores']:
                continue

            if d['type'] != 'qcow2':
                continue

            with util.RecordedOperation('snapshot %s' % d['device'], self) as _:
                self._snapshot_device(
                    d['path'], os.path.join(snappath, d['device']))
                db.create_snapshot(snapshot_uuid, d['device'], self.db_entry['uuid'],
                                   time.time())

        return snapshot_uuid

    def reboot(self, hard=False):
        libvirt = util.get_libvirt()
        instance = self._get_domain()
        if not hard:
            instance.reboot(flags=libvirt.VIR_DOMAIN_REBOOT_ACPI_POWER_BTN)
        else:
            instance.reset()

    def pause(self):
        instance = self._get_domain()
        instance.suspend()

    def unpause(self):
        instance = self._get_domain()
        instance.resume()

    def get_console_data(self, length):
        console_path = os.path.join(self.instance_path, 'console.log')
        if not os.path.exists(console_path):
            return ''

        offset = max(0, os.stat(console_path).st_size - length)

        with open(console_path) as f:
            f.seek(offset)
            return f.read()
