# Copyright 2019 Michael Still

import base64
import email.utils
import hashlib
import logging
import io
import json
import os
import pycdlib
import randmac
import shutil
import urllib.request

from oslo_concurrency import processutils

import config


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


VALIDATED_IMAGE_FIELDS = ['Last-Modified', 'Content-Length']


class Instance(object):
    def __init__(self, uuid, name, tenant, image_url, root_size):
        self.uuid = uuid
        self.name = name
        self.tenant = tenant
        self.image_url = image_url
        self.root_size = root_size

        self.sshkey = 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC2Pas6zLLgzXsUSZzt8E8fX7tzpwmNlrsbAeH9YoI2snfo+cKfO1BZVQgJnJVz+hGhnC1mzsMZMtdW2NRonRgeeQIPTUFXJI+3dyGzmiNrmtH8QQz++7zsmdwngeXKDrYhD6JGnPTkKcjShYcbvB/L3IDDJvepLxVOGRJBVHXJzqHgA62AtVsoiECKxFSn8MOuRfPHj5KInLxOEX9i/TfYKawSiId5xEkWWtcrp4QhjuoLv4UHL2aKs85ppVZFTmDHHcx3Au7pZ7/T9NOcUrvnwmQDVIBeU0LEELzuQZWLkFYvStAeCF7mYra+EJVXjiCQ9ZBw0vXGqJR1SU+W6dh9 mikal@kolla-m1'
        self.mac_address = str(randmac.RandMac('00:00:00:00:00:00', True))

        # Ensure we have state on disk
        self.instance_path = os.path.join(
            config.parsed.get('STORAGE_PATH'), 'instances', uuid)
        if not os.path.exists(self.instance_path):
            LOG.debug('%s: Creating instance storage at %s' %(self.uuid, self.instance_path))
            os.makedirs(self.instance_path)

        self.image_cache_path = os.path.join(
           config.parsed.get('STORAGE_PATH'), 'image_cache')
        if not os.path.exists(self.image_cache_path):
            LOG.debug('%s: Creating image cache at %s' %(self.uuid, self.image_cache_path))
            os.makedirs(self.image_cache_path)

        # Generate a config drive
        self._make_config_drive()

        # Prepare the root disk image
        self._fetch_image()
        self._transcode_image()
        self._resize_image()
        self._create_root_disk()

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
                    'ethernet_mac_address': self.mac_address,
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
        LOG.debug('%s: Writing config drive to %s' %(self.uuid, self.instance_path))
        iso.write(os.path.join(self.instance_path, 'config.disk'))
        iso.close()

    def _fetch_image(self):
        """Download the image if we don't already have the latest version in cache."""

        # Determine the hash for this image
        h = hashlib.sha256()
        h.update(self.image_url.encode('utf-8'))
        self.hashed_image_url = h.hexdigest()
        LOG.debug('%s: Image %s hashes to %s' %(self.uuid, self.image_url, self.hashed_image_url))

        # Populate cache if its empty
        self.hashed_image_path = os.path.join(self.image_cache_path, self.hashed_image_url)

        if not os.path.exists(self.hashed_image_path +  '.info'):
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
        req = urllib.request.Request(self.image_url, method='HEAD')
        resp = urllib.request.urlopen(req)

        image_dirty = False
        for field in VALIDATED_IMAGE_FIELDS:
            if info.get(field) != resp.headers.get(field):
                image_dirty = True

        # If the image is missing, or has changed, fetch
        if image_dirty:
            LOG.info('%s: Fetching image' % self.uuid)
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

            LOG.info('%s: Fetching image complete (%d bytes)' %(self.uuid, fetched))

    def _transcode_image(self):
        """Convert the image to qcow2."""

        if not os.path.exists(self.hashed_image_path + '.qcow2'):
            LOG.info('%s: Transcoding image to qcow2' % self.uuid)
            processutils.execute(
                'qemu-img', 'convert', '-t', 'none', '-O', 'qcow2',
                self.hashed_image_path, self.hashed_image_path + '.qcow2')
            LOG.info('%s: Transcoding image to qcow2 complete' % self.uuid)

    def _resize_image(self):
        """Resize the image to the specified size."""

        self.root_backing_file = self.hashed_image_path + '.qcow2'  + '.' + self.root_size
        if not os.path.exists(self.root_backing_file):
            LOG.info('%s: Resizing image to %s' %(self.uuid, self.root_size))
            shutil.copyfile(self.hashed_image_path + '.qcow2', self.root_backing_file)
            processutils.execute(
                'qemu-img', 'resize', self.root_backing_file, self.root_size)
            LOG.info('%s: Resizing image to %s complete' %(self.uuid, self.root_size))

    def _create_root_disk(self):
        """Create the root disk as a COW layer on top of the image cache."""

        processutils.execute(
            'qemu-img', 'create', '-b', self.root_backing_file,
            '-f', 'qcow2', os.path.join(self.instance_path, 'root.qcow2'))