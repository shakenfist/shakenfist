# Helpers to resolve images when we don't have an image service

import email.utils
import hashlib
import os
import re
import requests
import time

from shakenfist import db
from shakenfist.config import config
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


def _get_cache_path():
    image_cache_path = os.path.join(config.get('STORAGE_PATH'), 'image_cache')
    if not os.path.exists(image_cache_path):
        LOG.withField('image_cache_path',
                      image_cache_path).debug('Creating image cache')
        os.makedirs(image_cache_path)
    return image_cache_path


class Image(object):
    def __init__(self, url, checksum, size, modified, fetched, file_version):
        self.url = url
        self.checksum = checksum
        self.size = size
        self.modified = modified
        self.fetched = fetched
        self.file_version = file_version

        # Derive extra parameters
        self.unique_ref = self.calc_unique_ref(self.url)
        self.image_path = os.path.join(_get_cache_path(), self.unique_ref)

        self.log = LOG.withImage(self)

    def unique_label(self):
        return ('image', self.unique_ref)

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

    @staticmethod
    def from_url(url, checksum=None):
        # Handle URL shortcut with built-in resolvers
        url, resolver_checksum = Image._resolve(url)

        # Check for existing metadata in DB
        db_data = db.get_image_metadata(Image.calc_unique_ref(url),
                                        config.NODE_NAME)

        # Load DB data into new Image object
        if db_data:
            return Image.from_db_data(db_data)

        # Create new object since not found in database
        if not checksum:
            checksum = resolver_checksum
        return Image(url, checksum, None, None, None, 0)

    @staticmethod
    def from_db_data(db_data):
        ver = db_data['version']
        del db_data['version']

        # Check version of DB metadata packet
        if ver == 1:
            return Image(**db_data)
        else:
            raise exceptions.BadMetadataPacket('Image: %s', db_data)

    def persist(self):
        metadata = {
            'url': self.url,
            'checksum': self.checksum,
            'size': self.size,
            'modified': self.modified,
            'fetched': self.fetched,
            'file_version': self.file_version,
            'version': 1,
        }
        db.persist_image_metadata(self.unique_ref, config.NODE_NAME,
                                  metadata)

    def get(self, locks, related_object):
        """Wrap three retries around the image get.

        The Image must be locked before calling this function. During the
        download, the locks will be refreshed. Any lock error should abort the
        get, since the lock will have been lost.
        """
        for _ in range(3):
            try:
                return self._get(locks, related_object)
            except exceptions.BadCheckSum as e:
                LOG.warning('Bad checksum while downloading image: %s' % e)
                exc = e
        raise exc

    def version_image_path(self):
        return '%s.v%03d' % (self.image_path, self.file_version)

    def _get(self, locks, related_object):
        """Fetch image if not downloaded and return image path."""
        actual_image = self.version_image_path()

        with util.RecordedOperation('fetch image', related_object):
            resp = self._open_connection()

            diff_field = self._new_image_available(resp)
            if diff_field:
                self.log.withField('diff_field', diff_field).info(
                    'Fetch required due HTTP field change')
                if related_object:
                    t, u = related_object.unique_label()
                    msg = '%s: %s -> %s' % diff_field
                    db.add_event(t, u, 'image requires fetch', None, None, msg)

                actual_image = self._fetch(resp, locks)

                # Ensure checksum is correct
                if not self.correct_checksum(actual_image):
                    raise exceptions.BadCheckSum('url=%s' % self.url)

                # Only persist values after the file has been verified.
                # Otherwise diff_field will not trigger a new download in the
                # case of a checksum verification failure.
                self.persist()

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
        modified = resp.headers.get('Last-Modified')
        if self.modified != modified:
            return ('modified', self.modified, modified)

        size = resp.headers.get('Content-Length')
        if self.size != size:
            return ('size', self.size, size)

        return False

    def _fetch(self, resp, locks=None):
        """Download the image if the latest version is not in the cache."""
        fetched = 0
        self.file_version += 1
        self.fetched = email.utils.formatdate()
        self.modified = resp.headers.get('Last-Modified')
        self.size = resp.headers.get('Content-Length')

        last_refresh = 0
        with open(self.version_image_path(), 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                fetched += len(chunk)
                f.write(chunk)

                if time.time() - last_refresh > 5:
                    db.refresh_locks(locks)
                    last_refresh = time.time()

        LOG.withImage(self).withField('bytes_fetched',
                                      fetched).info('Fetch complete')

        # Check if decompression not required
        fn = self.version_image_path()
        if not self.url.endswith('.gz'):
            return fn

        # Check if already decompressed
        if not os.path.exists(f + '.orig'):
            util.execute(locks, 'gunzip -k -q -c %s > %s.orig' % (fn, fn))
        return fn + '.orig'

    def correct_checksum(self, image_name):
        log = LOG.withField('image', image_name)

        if not self.checksum:
            log.info('No checksum comparison available')
            return True

        # MD5 chosen because cirros 90% of the time has MD5SUMS available...
        md5_hash = hashlib.md5()
        with open(image_name, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                md5_hash.update(byte_block)
        calc = md5_hash.hexdigest()
        log.withField('calc', calc).debug('Calc from image download')

        correct = calc == self.checksum
        log.withField('correct', correct).info('Image checksum verification')
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
