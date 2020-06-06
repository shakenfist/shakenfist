# Helpers to resolve images when we don't have an image service

import email.utils
import hashlib
import json
import logging
import os
import re
import shutil
import urllib.request

from oslo_concurrency import processutils

from shakenfist import config


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)

VALIDATED_IMAGE_FIELDS = ['Last-Modified', 'Content-Length']


def resolve_image(name):
    if name.startswith('cirros'):
        return _resolve_cirros(name)
    if name.startswith('ubuntu'):
        return _resolve_ubuntu(name)
    return name


def _resolve_cirros(name):
    req = urllib.request.Request(
        'http://download.cirros-cloud.net/', method='GET')
    resp = urllib.request.urlopen(req)

    if name == 'cirros':
        versions = []
        dir_re = re.compile('.*<a href="([0-9]+\.[0-9]+\.[0-9]+)/">.*/</a>.*')
        for line in resp.read().decode('utf-8').split('\n'):
            m = dir_re.match(line)
            if m:
                versions.append(m.group(1))
        LOG.info('Found cirros versions: %s' % versions)
        ver = versions[-1]
    else:
        # Name is assumed to be in the form cirros:0.4.0
        _, ver = name.split(':')

    return ('http://download.cirros-cloud.net/%(ver)s/cirros-%(ver)s-x86_64-disk.img'
            % {'ver': ver})


def _resolve_ubuntu(name):
    req = urllib.request.Request(
        'https://cloud-images.ubuntu.com', method='GET')
    resp = urllib.request.urlopen(req)

    versions = {}
    dir_re = re.compile(
        '.*<a href="(.*)/">.*Ubuntu Server ([0-9]+\.[0-9]+).*')
    for line in resp.read().decode('utf-8').split('\n'):
        m = dir_re.match(line)
        if m:
            versions[m.group(2)] = m.group(1)
    LOG.info('Found ubuntu versions: %s' % versions)

    if name == 'ubuntu':
        verkey = sorted(versions.keys())[-1]
        ver = versions[verkey]
    else:
        # Name is assumed to be in the form ubuntu:xenial or ubuntu:19.04
        _, req = name.split(':')
        ver = versions.get(req, req)

    return ('https://cloud-images.ubuntu.com/%(ver)s/current/%(ver)s-server-cloudimg-amd64.img'
            % {'ver': ver})


def _get_cache_path():
    image_cache_path = os.path.join(
        config.parsed.get('STORAGE_PATH'), 'image_cache')
    if not os.path.exists(image_cache_path):
        LOG.debug('Creating image cache at %s' % image_cache_path)
        os.makedirs(image_cache_path)
    return image_cache_path


def fetch_image(image_url):
    """Download the image if we don't already have the latest version in cache."""

    image_url = resolve_image(image_url)

    # Determine the hash for this image
    h = hashlib.sha256()
    h.update(image_url.encode('utf-8'))
    hashed_image_url = h.hexdigest()
    LOG.debug('Image %s hashes to %s' % (image_url, hashed_image_url))

    # Populate cache if its empty
    hashed_image_path = os.path.join(_get_cache_path(), hashed_image_url)

    if not os.path.exists(hashed_image_path + '.info'):
        info = {
            'url': image_url,
            'hash': hashed_image_url,
            'version': 0
        }
    else:
        with open(hashed_image_path + '.info') as f:
            info = json.loads(f.read())

    # Fetch basic information about the image from the remote server
    # NOTE(mikal): if the head request results in a redirect, we end up
    # with a GET request instead. This is lame, but I am lazy right now.
    req = urllib.request.Request(image_url, method='HEAD')
    resp = urllib.request.urlopen(req)

    image_dirty = False
    for field in VALIDATED_IMAGE_FIELDS:
        if info.get(field) != resp.headers.get(field):
            image_dirty = True

    # If the image is missing, or has changed, fetch
    if image_dirty:
        received = 0

        LOG.info('Fetching image %s' % image_url)
        info['version'] += 1
        info['fetched_at'] = email.utils.formatdate()

        req = urllib.request.Request(image_url, method='GET')
        resp = urllib.request.urlopen(req)
        fetched = 0

        for field in VALIDATED_IMAGE_FIELDS:
            info[field] = resp.headers.get(field)

        with open(hashed_image_path + '.v%03d' % info['version'], 'wb') as f:
            chunk = resp.read(1024 * 1024)
            received += len(chunk)

            while chunk:
                fetched += len(chunk)
                f.write(chunk)

                chunk = resp.read(1024 * 1024)
                received += len(chunk)

        with open(hashed_image_path + '.info', 'w') as f:
            f.write(json.dumps(info, indent=4, sort_keys=True))

        LOG.info('Fetching image %s complete (%d bytes)' %
                 (image_url, fetched))

    # Decompress if required
    if image_url.endswith('.gz'):
        if not os.path.exists(hashed_image_path + '.v%03d.orig' % info['version']):
            processutils.execute(
                'gunzip -k -q -c %(img)s > %(img)s.orig' % {
                    'img': hashed_image_path + '.v%03d' % info['version']},
                shell=True)
        return '%s.v%03d.orig' % (hashed_image_path, info['version'])

    return '%s.v%03d' % (hashed_image_path, info['version'])


def transcode_image(hashed_image_path):
    """Convert the image to qcow2."""

    if os.path.exists(hashed_image_path + '.qcow2'):
        return

    current_format = identify_image(hashed_image_path).get('file format')
    if current_format == 'qcow2':
        os.link(hashed_image_path, hashed_image_path + '.qcow2')
        return

    processutils.execute(
        'qemu-img convert -t none -O qcow2 %s %s.qcow2'
        % (hashed_image_path, hashed_image_path),
        shell=True)


def resize_image(hashed_image_path, size):
    """Resize the image to the specified size."""

    backing_file = hashed_image_path + '.qcow2' + '.' + str(size) + 'G'
    current_size = identify_image(hashed_image_path).get('virtual size')
    if current_size == size * 1024 * 1024 * 1024:
        os.link(hashed_image_path, backing_file)
        return backing_file

    if os.path.exists(backing_file):
        return backing_file

    shutil.copyfile(hashed_image_path + '.qcow2', backing_file)
    processutils.execute(
        'qemu-img resize %s %sG' % (backing_file, size),
        shell=True)

    return backing_file


VALUE_WITH_BRACKETS_RE = re.compile('.* \(([0-9]+) bytes\)')


def identify_image(path):
    """Work out what an image is."""

    if not os.path.exists(path):
        return {}

    out, _ = processutils.execute(
        'qemu-img info %s' % path, shell=True)

    data = {}
    for line in out.split('\n'):
        line = line.lstrip().rstrip()
        elems = line.split(': ')
        if len(elems) > 1:
            key = elems[0]
            value = ': '.join(elems[1:])

            m = VALUE_WITH_BRACKETS_RE.match(value)
            if m:
                value = float(m.group(1))

            elif value.endswith('K'):
                value = float(value[:-1]) * 1024
            elif value.endswith('M'):
                value = float(value[:-1]) * 1024 * 1024
            elif value.endswith('G'):
                value = float(value[:-1]) * 1024 * 1024 * 1024
            elif value.endswith('T'):
                value = float(value[:-1]) * 1024 * 1024 * 1024 * 1024

            try:
                data[key] = float(value)
            except:
                data[key] = value

    return data


def create_cow(cache_file, disk_file):
    """Create a COW layer on top of the image cache."""

    if os.path.exists(disk_file):
        return

    processutils.execute(
        'qemu-img create -b %s -f qcow2 %s' % (cache_file, disk_file),
        shell=True)


def snapshot(source, destination):
    """Convert a possibly COW layered disk file into a snapshot."""

    processutils.execute(
        'qemu-img convert --force-share -O qcow2 %s %s'
        % (source, destination),
        shell=True)
