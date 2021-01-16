# Helpers to resolve images when we don't have an image service

import email.utils
import hashlib
import os
import re
import requests
import time

from shakenfist import baseobject
from shakenfist.config import config
from shakenfist import db
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import image_resolver_cirros
from shakenfist import image_resolver_ubuntu
from shakenfist import logutil
from shakenfist import util
from shakenfist import virt


LOG, _ = logutil.setup(__name__)


resolvers = {
    'cirros': image_resolver_cirros,
    'ubuntu': image_resolver_ubuntu
}


class Image(baseobject.DatabaseBackedObject):
    object_type = 'image'
    current_version = 2
    state_targets = {
        None: ('initial', 'creating'),
        'initial': ('initial', 'creating', 'error'),
        # TODO(andy): This is broken but will be accepted until Image class is
        # refactored. (hey, at least the state names will be valid)
        'creating': ('initial', 'creating', 'created', 'deleted', 'error'),
        'created': ('initial', 'creating', 'created', 'deleted', 'error'),
        'error': ('deleted', 'error'),
        'deleted': ('deleted', 'error', 'creating'),
    }

    def __init__(self, static_values):
        # NOTE(mikal): we call the unique_ref the "uuid" for the rest of this
        # class because that's what the base object does. Note that the
        # checksum is not in fact a UUID and is in fact intended to collide
        # when URLs are identical.
        self.__unique_ref = static_values['ref']
        uuid = self.__unique_ref + '/' + static_values['node']
        super(Image, self).__init__(uuid, static_values['version'])

        self.__url = static_values['url']
        self.__node = static_values['node']

    @classmethod
    def new(cls, url, checksum=None):
        # Handle URL shortcut with built-in resolvers
        url, resolver_checksum = Image._resolve(url)
        if not checksum:
            checksum = resolver_checksum

        unique_ref = Image.calc_unique_ref(url)
        uuid = '%s/%s' % (unique_ref, config.NODE_NAME)

        # Check for existing metadata in DB
        i = Image.from_db(uuid)
        if i:
            i.update_checksum(checksum)
            return i

        Image._db_create(uuid, {
            'uuid': uuid,
            'url': url,
            'node': config.NODE_NAME,
            'ref': unique_ref,
            'version': cls.current_version
        })
        i = Image.from_db(uuid)
        i.state = 'initial'
        i.update_checksum(checksum)
        i.add_event('db record creation', None)
        return i

    @staticmethod
    def from_db(uuid):
        if not uuid:
            return None

        static_values = Image._db_get(uuid)
        if not static_values:
            return None

        return Image(static_values)

    # Static values
    @property
    def url(self):
        return self.__url

    @property
    def unique_ref(self):
        return self.__unique_ref

    @property
    def node(self):
        return self.__node

    # Values routed to attributes
    @property
    def checksum(self):
        checksum = self._db_get_attribute('latest_checksum')
        if not checksum:
            return None
        return checksum.get('checksum')

    def update_checksum(self, checksum):
        old_checksum = self.checksum
        if checksum and checksum != old_checksum:
            self._db_set_attribute('latest_checksum', {'checksum': checksum})
            self.add_event('checksum has changed',
                           '%s -> %s' % (old_checksum, checksum))

    @property
    def latest_download_version(self):
        versions = {}
        for key, data in self._db_get_attributes('download_'):
            if data:
                versions[int(key.split('_')[1])] = data
        if not versions:
            return {'sequence': 0}
        return versions[sorted(versions)[-1]]

    def _add_download_version(self, size, modified, fetched_at):
        with self.get_lock_attr('download', 'Image version creation'):
            new_version = self.latest_download_version['sequence'] + 1
            self._db_set_attribute('download_%d' % new_version,
                                   {
                                       'size': size,
                                       'modified': modified,
                                       'fetched_at': fetched_at,
                                       'sequence': new_version
                                   })

    def external_view(self):
        # If this is an external view, then mix back in attributes that users expect
        i = {
            'uuid': self.uuid,
            'url': self.url,
            'node': self.node,
            'ref': self.unique_ref,
            'state': self.state.value,
            'version': self.version
        }

        for attrname in ['latest_checksum']:
            d = self._db_get_attribute(attrname)
            for key in d:
                # We skip keys with no value
                if d[key] is None:
                    continue

                i[key] = d[key]

        d = self.latest_download_version
        for key in d:
            # We skip keys with no value
            if d[key] is None:
                continue

            i[key] = d[key]

        return i

    # Implementation
    @staticmethod
    def _resolve(url):
        for resolver in resolvers:
            if url.startswith(resolver):
                return resolvers[resolver].resolve(url)
        return url, None

    @staticmethod
    def calc_unique_ref(url):
        """Calc unique reference for this image.

        The calculated reference is used as the unique DB reference and as the
        on-disk filename.
        """

        # TODO(andy): If we namespace downloads then this can combine namespace
        # with the URL. The DB stores the URL allowing searches to re-use
        # already downloaded images.
        h = hashlib.sha256()
        h.update(url.encode('utf-8'))
        return h.hexdigest()

    def delete(self):
        # NOTE(mikal): it isn't actually safe to remove the image from the cache
        # without verifying that no instance is using it, so we just mark the
        # image as deleted in the database and move on without removing things
        # from the cache. We will probably want to revisit this in the future.
        self.state = 'deleted'

    def get(self, locks, related_object):
        """Wrap three retries around the image get.

        The Image must be locked before calling this function. During the
        download, the locks will be refreshed. Any lock error should abort the
        get, since the lock will have been lost.
        """
        self.state = 'creating'
        for _ in range(3):
            try:
                image_path = self._get(locks, related_object)
                self.state = 'created'
                return image_path
            except exceptions.BadCheckSum as e:
                self.log.warning('Bad checksum while downloading image: %s', e)
                self.state = 'error'
                self.error = 'Bad checksum while downloading image: %s' % e
                exc = e
        raise exc

    def version_image_path(self, inc=0):
        image_cache_path = os.path.join(
            config.get('STORAGE_PATH'), 'image_cache')
        if not os.path.exists(image_cache_path):
            self.log.with_field('image_cache_path',
                                image_cache_path).debug('Creating image cache')
            os.makedirs(image_cache_path, exist_ok=True)

        return '%s/%s.v%03d' % (image_cache_path, self.unique_ref,
                                self.latest_download_version['sequence'] + inc)

    def _get(self, locks, related_object):
        """Fetch image if not downloaded and return image path."""
        actual_image = self.version_image_path()

        with util.RecordedOperation('fetch image', related_object):
            resp = self._open_connection()

            diff_field = self._new_image_available(resp)
            if diff_field:
                self.log.with_field('diff_field', diff_field).info(
                    'Fetch required due HTTP field change')
                if related_object:
                    t, u = related_object.unique_label()
                    msg = '%s: %s -> %s' % diff_field
                    db.add_event(t, u, 'image requires fetch', None, None, msg)

                actual_image = self._fetch(resp, locks)

                # Ensure checksum is correct
                if not self.correct_checksum(actual_image):
                    if isinstance(related_object, virt.Instance):
                        related_object.add_event('fetch image', 'bad checksum')
                    raise exceptions.BadCheckSum('url=%s' % self.url)

                # Only persist values after the file has been verified.
                # Otherwise diff_field will not trigger a new download in the
                # case of a checksum verification failure.
                self._add_download_version(resp.headers.get('Content-Length'),
                                           resp.headers.get('Last-Modified'),
                                           email.utils.formatdate())

        _transcode(locks, actual_image, related_object)
        return actual_image

    def _open_connection(self):
        resp = requests.get(self.url, allow_redirects=True, stream=True,
                            headers={'User-Agent': util.get_user_agent()})
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch HEAD of %s (status code %d)'
                % (self.url, resp.status_code))
        return resp

    def _new_image_available(self, resp):
        """Check if HTTP headers indicate the image file has changed."""
        latest = self.latest_download_version

        modified = resp.headers.get('Last-Modified')
        if latest.get('modified') != modified:
            return ('modified', latest.get('modified'), modified)

        size = resp.headers.get('Content-Length')
        if latest.get('size') != size:
            return ('size', latest.get('size'), size)

        return False

    def _fetch(self, resp, locks=None):
        """Download the image if the latest version is not in the cache."""
        fetched = 0

        last_refresh = 0
        with open(self.version_image_path(inc=1), 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                fetched += len(chunk)
                f.write(chunk)

                if time.time() - last_refresh > 5:
                    db.refresh_locks(locks)
                    last_refresh = time.time()

        self.log.with_field('bytes_fetched', fetched).info('Fetch complete')

        # Check if decompression not required
        fn = self.version_image_path(inc=1)
        if not self.url.endswith('.gz'):
            return fn

        # Check if already decompressed
        if not os.path.exists(f + '.orig'):
            util.execute(locks, 'gunzip -k -q -c %s > %s.orig' % (fn, fn))
        return fn + '.orig'

    def correct_checksum(self, image_name):
        log = self.log.with_field('image', image_name)

        if not self.checksum:
            log.info('No checksum comparison available')
            return True

        if not os.path.exists(image_name):
            return False

        # MD5 chosen because cirros 90% of the time has MD5SUMS available...
        md5_hash = hashlib.md5()
        with open(image_name, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                md5_hash.update(byte_block)
        calc = md5_hash.hexdigest()
        log.with_field('calc', calc).debug('Calc from image download')

        correct = calc == self.checksum
        log.with_field('correct', correct).info('Image checksum verification')
        return correct

    def resize(self, locks, size):
        """Resize the image to the specified size."""
        image_path = self.version_image_path()
        backing_file = image_path + '.qcow2' + '.' + str(size) + 'G'

        if os.path.exists(backing_file):
            return backing_file

        current_size = identify(image_path).get('virtual size')

        if current_size == size * 1024 * 1024 * 1024:
            os.link(image_path, backing_file)
            return backing_file

        util.execute(locks,
                     'qemu-img create -b %s.qcow2 -f qcow2 %s %dG'
                     % (image_path, backing_file, size))

        return backing_file


# TODO(mikal): can this be refactored into baseobject?
class Images(object):
    def __init__(self, filters):
        self.filters = filters

    def __iter__(self):
        for key, i in etcd.get_all('image', None):
            image_node = '/'.join(key.split('/')[-2:])
            i = Image.from_db(image_node)
            if not i:
                continue

            skip = False
            for f in self.filters:
                # If a filter returns false, we remove the image from
                # the result set.
                if not f(i):
                    skip = True
                    break

            if not skip:
                yield i


def url_filter(url, i):
    return i.url == url


def placement_filter(node, i):
    return i.node == node


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


def create_cow(locks, cache_file, disk_file, disk_size):
    """Create a COW layer on top of the image cache.

    disk_size is specified in Gigabytes.
    """

    if os.path.exists(disk_file):
        return

    util.execute(locks,
                 'qemu-img create -b %s -f qcow2 %s %dG'
                 % (cache_file, disk_file, int(disk_size)))


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
