# Copyright 2019 Michael Still

import base64
import jinja2
import logging
import io
import json
import libvirt
import os
import pycdlib
import randmac
import shutil

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist import images
from shakenfist import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


class Instance(object):
    def __init__(self, uuid=None, name=None, tenant=None, disks=None, memory_kb=None, vcpus=None):
        self.uuid = uuid
        self.name = name
        self.tenant = tenant
        self.memory = memory_kb
        self.vcpus = vcpus

        self.instance_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'instances', self.uuid)

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

        # TODO(mikal): sanity check instance specification

        self.sshkey = 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC2Pas6zLLgzXsUSZzt8E8fX7tzpwmNlrsbAeH9YoI2snfo+cKfO1BZVQgJnJVz+hGhnC1mzsMZMtdW2NRonRgeeQIPTUFXJI+3dyGzmiNrmtH8QQz++7zsmdwngeXKDrYhD6JGnPTkKcjShYcbvB/L3IDDJvepLxVOGRJBVHXJzqHgA62AtVsoiECKxFSn8MOuRfPHj5KInLxOEX9i/TfYKawSiId5xEkWWtcrp4QhjuoLv4UHL2aKs85ppVZFTmDHHcx3Au7pZ7/T9NOcUrvnwmQDVIBeU0LEELzuQZWLkFYvStAeCF7mYra+EJVXjiCQ9ZBw0vXGqJR1SU+W6dh9 mikal@kolla-m1'
        self.eth0_mac = str(randmac.RandMac(
            '52:54:00:00:00:00', True)).lstrip('\'').rstrip('\'')
        self.eth0_ip = None

    def __str__(self):
        return 'instance(%s)' % self.uuid

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

    def create(self):
        # Ensure we have state on disk
        if not os.path.exists(self.instance_path):
            LOG.debug('%s: Creating instance storage at %s' %
                      (self, self.instance_path))
            os.makedirs(self.instance_path)

        # Generate a config drive
        with util.RecordedOperation('make config drive', self) as ro:
            self._make_config_drive(os.path.join(
                self.instance_path, self._get_disk_device_base() + 'b' + '.raw'))

        # Prepare disks
        for disk in self.disks:
            if disk.get('base'):
                with util.RecordedOperation('fetch image', self) as ro:
                    hashed_image_path = images.fetch_image(disk['base'])
                with util.RecordedOperation('transcode image', self) as ro:
                    images.transcode_image(hashed_image_path)
                with util.RecordedOperation('resize image', self) as ro:
                    resized_image_path = images.resize_image(
                        hashed_image_path, str(disk['size']) + 'G')
                with util.RecordedOperation('create copy on write layer', self) as ro:
                    images.create_cow(resized_image_path, disk['path'])
            elif not os.path.exists(disk['path']):
                processutils.execute('qemu-img create -f qcow2 %s %sG'
                                     % (disk['path'], disk['size']),
                                     shell=True)

        # Create the actual instance
        with util.RecordedOperation('create domain XML', self) as ro:
            self._create_domain_xml()
        with util.RecordedOperation('create domain', self) as ro:
            self._create_domain()

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
            'availability_zone': config.parsed.get('ZONE'),
            'hostname': '%s.local' % self.name,
            'launch_index': 0,
            'devices': [],
            'project_id': self.tenant,
            'name': self.name,
            'public_keys': {
                'mykey': self.sshkey
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

        self.xml_file = os.path.join(self.instance_path, 'libvirt.xml')
        if os.path.exists(self.xml_file):
            return

        with open(os.path.join(config.parsed.get('STORAGE_PATH'), 'libvirt.tmpl')) as f:
            t = jinja2.Template(f.read())

        xml = t.render(
            uuid=self.uuid,
            memory=self.memory,
            vcpus=self.vcpus,
            disk_bus=config.parsed.get('DISK_BUS'),
            disks=self.disks,
            eth0_mac=self.eth0_mac,
            eth0_bridge=self.network_subst['vx_bridge'],
            network_model=config.parsed.get('NETWORK_MODEL'),
        )

        with open(self.xml_file, 'w') as f:
            f.write(xml)

    def _create_domain(self):
        with open(self.xml_file) as f:
            xml = f.read()

        conn = libvirt.open(None)
        try:
            instance = conn.lookupByName('sf:' + self.uuid)
            return

        except libvirt.libvirtError:
            instance = conn.defineXML(xml)
            if not instance:
                LOG.error('%s: Failed to create libvirt domain' % self)
                return

        instance.create()
        instance.setAutostart(1)
