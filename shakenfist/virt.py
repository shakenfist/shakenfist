# Copyright 2019 Michael Still

import base64
import email.utils
import hashlib
import jinja2
import logging
import io
import json
import libvirt
import os
import pycdlib
import randmac
import shutil
import urllib.request

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist import images
from shakenfist import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


VALIDATED_IMAGE_FIELDS = ['Last-Modified', 'Content-Length']


class Instance(object):
    def __init__(self, uuid=None, name=None, tenant=None, image_url=None, disks=None, memory_kb=None, vcpus=None):
        self.uuid = uuid
        self.name = name
        self.tenant = tenant
        self.image_url = images.resolve_image(image_url)
        self.root_size = str(disks[0]) + 'G'
        self.memory = memory_kb
        self.vcpus = vcpus

        self.instance_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'instances', self.uuid)

        # Allocate devices to root and config disks depending on the bus in use
        self.root_disk_device = self._get_disk_device_base() + 'a'
        self.config_disk_device = self._get_disk_device_base() + 'b'

        # Convert extra disks into something we can use in a template
        self.extra_disks = []
        i = 0
        for d in disks[1:]:
            device = self._get_disk_device_base() + chr(ord('c') + i)
            self.extra_disks.append({
                'size': d,
                'device': device,
                'slot': hex(8 + i),
                'path': os.path.join(self.instance_path, device + '.qcow2')
            })
            i += 1

        # TODO(mikal): sanity check instance specification

        self.sshkey = 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC2Pas6zLLgzXsUSZzt8E8fX7tzpwmNlrsbAeH9YoI2snfo+cKfO1BZVQgJnJVz+hGhnC1mzsMZMtdW2NRonRgeeQIPTUFXJI+3dyGzmiNrmtH8QQz++7zsmdwngeXKDrYhD6JGnPTkKcjShYcbvB/L3IDDJvepLxVOGRJBVHXJzqHgA62AtVsoiECKxFSn8MOuRfPHj5KInLxOEX9i/TfYKawSiId5xEkWWtcrp4QhjuoLv4UHL2aKs85ppVZFTmDHHcx3Au7pZ7/T9NOcUrvnwmQDVIBeU0LEELzuQZWLkFYvStAeCF7mYra+EJVXjiCQ9ZBw0vXGqJR1SU+W6dh9 mikal@kolla-m1'
        self.eth0_mac = str(randmac.RandMac(
            '52:54:00:00:00:00', True)).lstrip('\'').rstrip('\'')
        self.eth0_ip = None

    def __str__(self):
        return 'instance(%s)' % self.uuid

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

        self.image_cache_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'image_cache')
        if not os.path.exists(self.image_cache_path):
            LOG.debug('%s: Creating image cache at %s' %
                      (self, self.image_cache_path))
            os.makedirs(self.image_cache_path)

        # Generate a config drive
        with util.RecordedOperation('make config drive', self) as ro:
            self._make_config_drive()

        # Prepare the root disk image
        with util.RecordedOperation('fetch image', self) as ro:
            self._fetch_image()
        with util.RecordedOperation('transcode image', self) as ro:
            self._transcode_image()
        with util.RecordedOperation('resize image', self) as ro:
            self._resize_image()
        with util.RecordedOperation('create root disk', self) as ro:
            self._create_root_disk()

        # Prepare extra disks
        with util.RecordedOperation('create extra disks', self) as ro:
            for disk in self.extra_disks:
                print('Creating %s' % disk['path'])
                processutils.execute('qemu-img create -f qcow2 %s %sG'
                                     % (disk['path'], disk['size']),
                                     shell=True)

        # Create the actual instance
        with util.RecordedOperation('create domain XML', self) as ro:
            self._create_domain_xml()
        with util.RecordedOperation('create domain', self) as ro:
            self._create_domain()

    def _make_config_drive(self):
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
        self.config_disk_file = os.path.join(self.instance_path, 'config.disk')
        iso.write(self.config_disk_file)
        iso.close()

    def _fetch_image(self):
        """Download the image if we don't already have the latest version in cache."""

        # Determine the hash for this image
        h = hashlib.sha256()
        h.update(self.image_url.encode('utf-8'))
        self.hashed_image_url = h.hexdigest()
        LOG.debug('%s: Image %s hashes to %s' %
                  (self, self.image_url, self.hashed_image_url))

        # Populate cache if its empty
        self.hashed_image_path = os.path.join(
            self.image_cache_path, self.hashed_image_url)

        if not os.path.exists(self.hashed_image_path + '.info'):
            info = {
                'url': self.image_url,
                'hash': self.hashed_image_url,
                'version': 0
            }
        else:
            with open(self.hashed_image_path + '.info') as f:
                info = json.loads(f.read())

        # Fetch basic information about the image from the remote server
        # NOTE(mikal): if the head request results in a redirect, we end up
        # with a GET request instead. This is lame, but I am lazy right now.
        print(self.image_url)
        req = urllib.request.Request(self.image_url, method='HEAD')
        resp = urllib.request.urlopen(req)

        image_dirty = False
        for field in VALIDATED_IMAGE_FIELDS:
            if info.get(field) != resp.headers.get(field):
                image_dirty = True

        # If the image is missing, or has changed, fetch
        if image_dirty:
            LOG.info('%s: Fetching image' % self)
            info['version'] += 1
            info['fetched_at'] = email.utils.formatdate()

            req = urllib.request.Request(self.image_url, method='GET')
            resp = urllib.request.urlopen(req)
            fetched = 0

            for field in VALIDATED_IMAGE_FIELDS:
                info[field] = resp.headers.get(field)

            with open(self.hashed_image_path, 'wb') as f:
                chunk = resp.read(1024 * 1024)
                while chunk:
                    fetched += len(chunk)
                    f.write(chunk)
                    chunk = resp.read(1024 * 1024)

            with open(self.hashed_image_path + '.info', 'w') as f:
                f.write(json.dumps(info, indent=4, sort_keys=True))

            LOG.info('%s: Fetching image complete (%d bytes)' %
                     (self, fetched))

    def _transcode_image(self):
        """Convert the image to qcow2."""

        if os.path.exists(self.hashed_image_path + '.qcow2'):
            return

        if self.image_url.endswith('.gz'):
            if not os.path.exists(self.hashed_image_path + '.orig'):
                processutils.execute(
                    'gunzip -k -q -c %(img)s > %(img)s.orig' % {
                        'img': self.hashed_image_path},
                    shell=True)
            self.hashed_image_path += '.orig'

        processutils.execute(
            'qemu-img convert -t none -O qcow2 %s %s.qcow2'
            % (self.hashed_image_path, self.hashed_image_path),
            shell=True)

    def _resize_image(self):
        """Resize the image to the specified size."""

        self.root_backing_file = self.hashed_image_path + '.qcow2' + '.' + self.root_size
        if os.path.exists(self.root_backing_file):
            return

        shutil.copyfile(self.hashed_image_path +
                        '.qcow2', self.root_backing_file)
        processutils.execute(
            'qemu-img resize %s %s' % (self.root_backing_file, self.root_size),
            shell=True)

    def _create_root_disk(self):
        """Create the root disk as a COW layer on top of the image cache."""

        self.root_disk_file = os.path.join(self.instance_path, 'root.qcow2')
        if os.path.exists(self.root_disk_file):
            return

        processutils.execute(
            'qemu-img create -b %s -f qcow2 %s'
            % (self.root_backing_file, self.root_disk_file),
            shell=True)

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
            disk_root=self.root_disk_file,
            device_root=self.root_disk_device,
            disk_config=self.config_disk_file,
            device_config=self.config_disk_device,
            eth0_mac=self.eth0_mac,
            eth0_bridge=self.network_subst['vx_bridge'],
            extra_disks=self.extra_disks,
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
