# Helpers to resolve images when we don't have an image service

import email.utils
import hashlib
import json
import os
import re
import requests
import time

from shakenfist import db
from shakenfist import config
from shakenfist import exceptions
from shakenfist import image_resolver_cirros
from shakenfist import image_resolver_ubuntu
from shakenfist import logutil
from shakenfist import util


LOG, _ = logutil.setup(__name__)


resolvers = {
    'cirros': image_resolver_cirros,
    'ubuntu': image_resolver_ubuntu
}

# The HTTP fields we use to decide if an image is out of date in the cache
VALIDATED_IMAGE_FIELDS = ['Last-Modified', 'Content-Length']


def _get_cache_path():
    image_cache_path = os.path.join(
        config.parsed.get('STORAGE_PATH'), 'image_cache')
    if not os.path.exists(image_cache_path):
        LOG.withField('image_cache_path',
                      image_cache_path).debug('Creating image cache')
        os.makedirs(image_cache_path)
    return image_cache_path


class Image(object):
    def __init__(self, url):
        self.url = self._resolve(url)
        self.orig_url = url

        self._hash()
        self.info = self._read_local_info()

    def unique_label(self):
        return ('image', self.hashed_image_url)

    def _resolve(self, url):
        for resolver in resolvers:
            if url.startswith(resolver):
                return resolvers[resolver].resolve(url)
        return url

    def _hash(self):
        h = hashlib.sha256()
        h.update(self.url.encode('utf-8'))
        self.hashed_image_url = h.hexdigest()
        self.hashed_image_path = os.path.join(
            _get_cache_path(), self.hashed_image_url)

    def _read_local_info(self):
        if not os.path.exists(self.hashed_image_path + '.info'):
            LOG.withImage(self).info('No info in cache for this image')
            return {
                'url': self.url,
                'hash': self.hashed_image_url,
                'version': 0
            }
        else:
            with open(self.hashed_image_path + '.info') as f:
                return json.loads(f.read())

    def _persist_info(self):
        with open(self.hashed_image_path + '.info', 'w') as f:
            f.write(json.dumps(self.info, indent=4, sort_keys=True))

    def get(self, locks, related_object):
        """Wrap some lock retries around the get."""

        # NOTE(mikal): this deliberately retries the lock for a long time
        # because the other option is failing instance start and fetching
        # an image can take an extremely long time. This still means that
        # for very large images you should probably pre-cache before
        # attempting a start.
        exc = None
        for _ in range(30):
            db.refresh_locks(locks)

            try:
                return self._get(locks, related_object)
            except exceptions.LockException as e:
                time.sleep(10)
                exc = e

        raise exceptions.LockException(
            'Failed to acquire image fetch lock after retries: %s' % exc)

    def _get(self, locks, related_object):
        """Fetch image if not downloaded and return image path."""
        actual_image = '%s.v%03d' % (
            self.hashed_image_path, self.info['version'])

        with db.get_lock('image', config.parsed.get('NODE_NAME'),
                         self.hashed_image_url) as image_lock:
            with util.RecordedOperation('fetch image', related_object):
                dirty_fields, resp = self._requires_fetch()

                if dirty_fields:
                    LOG.withImage(self).withField(
                        'dirty_fields', dirty_fields).info(
                            'Starting fetch due to dirty fields')

                    if related_object:
                        t, u = related_object.unique_label()
                        dirty_fields_pretty = []
                        for field in dirty_fields:
                            dirty_fields_pretty.append(
                                '%s: %s -> %s' % (field, dirty_fields[field]['before'],
                                                  dirty_fields[field]['after']))
                        db.add_event(t, u, 'image requires fetch',
                                     None, None, '\n'.join(dirty_fields_pretty))
                    actual_image = self._fetch(
                        resp, locks=locks.append(image_lock))

            _transcode(locks, actual_image, related_object)

        return actual_image

    def _requires_fetch(self):
        resp = requests.get(self.url, allow_redirects=True, stream=True,
                            headers={'User-Agent': util.get_user_agent()})
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch HEAD of %s (status code %d)'
                % (self.url, resp.status_code))

        dirty_fields = {}
        for field in VALIDATED_IMAGE_FIELDS:
            if self.info.get(field) != resp.headers.get(field):
                dirty_fields[field] = {
                    'before': self.info.get(field),
                    'after': resp.headers.get(field)
                }

        return dirty_fields, resp

    def _fetch(self, resp, locks=None):
        """Download the image if we don't already have the latest version in cache."""

        # Do the fetch
        fetched = 0
        self.info['version'] += 1
        self.info['fetched_at'] = email.utils.formatdate()
        for field in VALIDATED_IMAGE_FIELDS:
            self.info[field] = resp.headers.get(field)

        last_refresh = 0
        with open(self.hashed_image_path + '.v%03d' % self.info['version'], 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                fetched += len(chunk)
                f.write(chunk)

                if time.time() - last_refresh > 10:
                    db.refresh_locks(locks)

        if fetched > 0:
            self._persist_info()
            LOG.withImage(self).withField('bytes_fetched',
                                          fetched).info('Fetch complete')

        # Decompress if required
        if self.info['url'].endswith('.gz'):
            if not os.path.exists(self.hashed_image_path + '.v%03d.orig' % self.info['version']):
                util.execute(locks,
                             'gunzip -k -q -c %(img)s > %(img)s.orig' % {
                                 'img': self.hashed_image_path + '.v%03d' % self.info['version']})
            return '%s.v%03d.orig' % (self.hashed_image_path, self.info['version'])

        return '%s.v%03d' % (self.hashed_image_path, self.info['version'])


def _transcode(locks, actual_image, related_object):
    with util.RecordedOperation('transcode image', related_object):
        if os.path.exists(actual_image + '.qcow2'):
            return

        current_format = identify(actual_image).get('file format')
        if current_format == 'qcow2':
            os.link(actual_image, actual_image + '.qcow2')
            return

        util.execute(locks,
                     'qemu-img convert -t none -O qcow2 %s %s.qcow2'
                     % (actual_image, actual_image))


def resize(locks, hashed_image_path, size):
    """Resize the image to the specified size."""

    backing_file = hashed_image_path + '.qcow2' + '.' + str(size) + 'G'

    if os.path.exists(backing_file):
        return backing_file

    current_size = identify(hashed_image_path).get('virtual size')

    if current_size == size * 1024 * 1024 * 1024:
        os.link(hashed_image_path, backing_file)
        return backing_file

    util.execute(locks,
                 'cp %s %s' % (hashed_image_path + '.qcow2', backing_file))
    util.execute(locks,
                 'qemu-img resize %s %sG' % (backing_file, size))

    return backing_file


VALUE_WITH_BRACKETS_RE = re.compile(r'.* \(([0-9]+) bytes\)')


def identify(path):
    """Work out what an image is."""

    if not os.path.exists(path):
        return {}

    out, _ = util.execute(None,
                          'qemu-img info %s' % path)

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
            except Exception:
                data[key] = value

    return data


def create_cow(locks, cache_file, disk_file):
    """Create a COW layer on top of the image cache."""

    if os.path.exists(disk_file):
        return

    util.execute(locks,
                 'qemu-img create -b %s -f qcow2 %s' % (
                     cache_file, disk_file))


def create_flat(locks, cache_file, disk_file):
    """Make a flat copy of the disk from the image cache."""

    if os.path.exists(disk_file):
        return

    util.execute(locks, 'cp %s %s' % (cache_file, disk_file))


def create_raw(locks, cache_file, disk_file):
    """Make a raw copy of the disk from the image cache."""

    if os.path.exists(disk_file):
        return

    util.execute(locks,
                 'qemu-img convert -t none -O raw %s %s'
                 % (cache_file, disk_file))


def snapshot(locks, source, destination):
    """Convert a possibly COW layered disk file into a snapshot."""

    util.execute(locks,
                 'qemu-img convert --force-share -O qcow2 %s %s'
                 % (source, destination))
