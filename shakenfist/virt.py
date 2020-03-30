# Copyright 2019 Michael Still

import base64
import datetime
import jinja2
import logging
import io
import json
import libvirt
import os
import pycdlib
import randmac
import shutil
import uuid

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist.db import impl as db
from shakenfist import images
from shakenfist import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


def from_definition(uuid=None, name=None, disks=None, memory_mb=None,
                    vcpus=None, ssh_key=None):
    db_entry = db.create_instance(uuid, None, name, vcpus, memory_mb, disks,
                                  ssh_key)
    return Instance(db_entry)


def from_db(uuid):
    db_entry = db.get_instance(uuid)
    if not db_entry:
        return None
    return Instance(db_entry)


class Instance(object):
    def __init__(self, db_entry):
        self.db_entry = db_entry
        self.tenant = None

        self.instance_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'instances', self.db_entry['uuid'])
        self.snapshot_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'snapshots')
        self.xml_file = os.path.join(self.instance_path, 'libvirt.xml')

        disks = self.db_entry['disk_spec'].split(' ')
        size, base = self._parse_disk_spec(disks[0])
        root_device = self._get_disk_device_base() + 'a'
        config_device = self._get_disk_device_base() + 'b'
        self.disks = [
            {
                'type': 'qcow2',
                'size': size,
                'device': root_device,
                'path': os.path.join(self.instance_path, root_device + '.qcow2'),
                'base': base
            },
            {
                'type': 'raw',
                'device': config_device,
                'path': os.path.join(self.instance_path, config_device + '.raw')
            }
        ]

        i = 0
        for d in disks[1:]:
            size, base = self._parse_disk_spec(d)
            device = self._get_disk_device_base() + chr(ord('c') + i)
            self.disks.append({
                'type': 'qcow2',
                'size': size,
                'device': device,
                'path': os.path.join(self.instance_path, device + '.qcow2'),
                'base': base
            })
            i += 1

        self.eth0_mac = str(randmac.RandMac(
            '00:00:00:00:00:00', False)).lstrip('\'').rstrip('\'')
        self.eth0_ip = None

    def __str__(self):
        return 'instance(%s)' % self.db_entry['uuid']

    def _parse_disk_spec(self, spec):
        if not '@' in spec:
            return int(spec), None
        size, base = spec.split('@')
        return int(size), base

    def _get_disk_device_base(self):
        bases = {
            'ide': 'hd',
            'scsi': 'sd',
            'usb': 'sd',
            'virtio': 'vd',
        }
        return bases.get(config.parsed.get('DISK_BUS'), 'sd')

    def set_network_details(self, ip, network_subst):
        LOG.info('%s: Setting IP to %s' % (self, ip))
        self.eth0_ip = ip
        self.network_subst = network_subst

    def get_network_details(self):
        return (self.eth0_mac, self.eth0_ip)

    def get_network_uuid(self):
        return self.db_entry['network_uuid']

    def create(self, status_callback):
        # Ensure we have state on disk
        if not os.path.exists(self.instance_path):
            LOG.debug('%s: Creating instance storage at %s' %
                      (self, self.instance_path))
            os.makedirs(self.instance_path)

        # Generate a config drive
        with util.RecordedOperation('make config drive', self, status_callback) as ro:
            self._make_config_drive(os.path.join(
                self.instance_path, self._get_disk_device_base() + 'b' + '.raw'))

        # Prepare disks
        for disk in self.disks:
            if disk.get('base'):
                with util.RecordedOperation('fetch image', self, status_callback) as ro:
                    hashed_image_path = images.fetch_image(
                        disk['base'], recorded=ro)
                with util.RecordedOperation('transcode image', self, status_callback) as ro:
                    images.transcode_image(hashed_image_path)
                with util.RecordedOperation('resize image', self, status_callback) as ro:
                    resized_image_path = images.resize_image(
                        hashed_image_path, str(disk['size']) + 'G')
                with util.RecordedOperation('create copy on write layer', self,
                                            status_callback) as ro:
                    images.create_cow(resized_image_path, disk['path'])
            elif not os.path.exists(disk['path']):
                processutils.execute('qemu-img create -f qcow2 %s %sG'
                                     % (disk['path'], disk['size']),
                                     shell=True)

        # Create the actual instance
        with util.RecordedOperation('create domain XML', self, status_callback) as ro:
            self._create_domain_xml()
        with util.RecordedOperation('create domain', self, status_callback) as ro:
            self._create_domain()

    def delete(self, status_callback):
        with util.RecordedOperation('delete domain', self, status_callback) as ro:
            try:
                self._delete_domain()
            except:
                pass

        with util.RecordedOperation('delete disks', self, status_callback) as ro:
            try:
                shutil.rmtree(self.instance_path)
            except:
                pass

        db.delete_instance(self.db_entry['uuid'])

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

        # network_data.json
        nd = json.dumps({
            'links': [
                {
                    'ethernet_mac_address': self.eth0_mac,
                    'id': 'eth0',
                    'name': 'eth0',
                    'mtu': 1450,
                    'type': 'physical',
                }
            ],
            'networks': [
                {
                    'id': 'network0',
                    'link': 'eth0',
                    'type': 'ipv4_dhcp'
                }
            ],
            'services': [
                {
                    'address': '8.8.8.8',
                    'type': 'dns'
                }
            ]
        }).encode('ascii')
        iso.add_fp(io.BytesIO(nd), len(nd), '/openstack/latest/network_data.json;3',
                   rr_name='network_data.json',
                   joliet_path='/openstack/latest/vendor_data.json')
        iso.add_fp(io.BytesIO(nd), len(nd), '/openstack/2017-02-22/network_data.json;4',
                   rr_name='network_data.json',
                   joliet_path='/openstack/2017-02-22/vendor_data.json')

        # emtpy vendor_data.json and vendor_data2.json
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

        xml = t.render(
            uuid=self.db_entry['uuid'],
            memory=self.db_entry['memory'] * 1024,
            vcpus=self.db_entry['cpus'],
            disk_bus=config.parsed.get('DISK_BUS'),
            disks=self.disks,
            eth0_mac=self.eth0_mac,
            eth0_bridge=self.network_subst['vx_bridge'],
            network_model=config.parsed.get('NETWORK_MODEL'),
        )

        with open(self.xml_file, 'w') as f:
            f.write(xml)

    def _get_domain(self):
        conn = libvirt.open(None)
        try:
            return conn.lookupByName('sf:' + self.db_entry['uuid'])

        except libvirt.libvirtError:
            LOG.error('%s: Failed to lookup domain' % self)
            return None

    def _create_domain(self):
        with open(self.xml_file) as f:
            xml = f.read()

        instance = self._get_domain()
        if not instance:
            conn = libvirt.open(None)
            instance = conn.defineXML(xml)
            if not instance:
                LOG.error('%s: Failed to create libvirt domain' % self)
                return

        instance.create()
        instance.setAutostart(1)

    def _delete_domain(self):
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
        disks = self.disks
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
            print(d)
            if d['type'] != 'qcow2':
                continue

            with util.RecordedOperation('snapshot %s' % d['device'], self) as ro:
                self._snapshot_device(
                    d['path'], os.path.join(snappath, d['device']))
                db.create_snapshot(snapshot_uuid, d['device'], self.db_entry['uuid'],
                                   datetime.datetime.now())

        return snapshot_uuid
