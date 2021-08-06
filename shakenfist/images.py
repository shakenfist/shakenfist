import hashlib
import os
import random
import re
import requests
import shutil
import uuid

from shakenfist.artifact import Artifact, BLOB_URL
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import blob
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist import exceptions
from shakenfist import image_resolver
from shakenfist import instance
from shakenfist import logutil
from shakenfist import util


LOG, _ = logutil.setup(__name__)


class Image(dbo):
    object_type = 'image'
    current_version = 2
    state_targets = {
        None: (dbo.STATE_INITIAL, dbo.STATE_CREATING),
        dbo.STATE_INITIAL: (dbo.STATE_CREATING, dbo.STATE_DELETED, dbo.STATE_ERROR),
        # TODO(andy): This is broken but will be accepted until Image class is
        # refactored. (hey, at least the state names will be valid)
        dbo.STATE_CREATING: (dbo.STATE_INITIAL, dbo.STATE_CREATING, dbo.STATE_CREATED,
                             dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_INITIAL, dbo.STATE_CREATING, dbo.STATE_CREATED,
                            dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (dbo.STATE_DELETED, dbo.STATE_ERROR, dbo.STATE_CREATING),
    }

    def __init__(self, static_values):
        # NOTE(mikal): we call the unique_ref the "uuid" for the rest of this
        # class because that's what the base object does. Note that the
        # checksum is not in fact a UUID and is in fact intended to collide
        # when URLs are identical.
        self.__unique_ref = static_values['ref']

        super(Image, self).__init__(self.__unique_ref + '/' +
                                    static_values['node'], static_values['version'])

        self.__url = static_values['url']
        self.__node = static_values['node']

        # Images here have a mirroring artifact of type 'image' for now.
        # This is done so we can tie all the various download versions
        # together. Over time this class should go away and be replaced
        # with just the artifact.
        self.__artifact = Artifact.from_url(Artifact.TYPE_IMAGE, self.__url)

    @classmethod
    def new(cls, url, checksum=None):
        # Handle URL shortcut with built-in resolvers
        url, resolver_checksum = image_resolver.resolve(url)
        if not checksum:
            checksum = resolver_checksum

        unique_ref = Image.calc_unique_ref(url)
        image_uuid = '%s/%s' % (unique_ref, config.NODE_NAME)

        # Check for existing metadata in DB
        i = Image.from_db(image_uuid)
        if i:
            i.update_checksum(checksum)
            return i

        Image._db_create(image_uuid, {
            'uuid': image_uuid,
            'url': url,
            'node': config.NODE_NAME,
            'ref': unique_ref,
            'version': cls.current_version
        })
        i = Image.from_db(image_uuid)
        i.state = Image.STATE_INITIAL
        i.update_checksum(checksum)
        i.add_event('db record creation', None)
        return i

    @staticmethod
    def from_db(image_uuid):
        if not image_uuid:
            return None

        static_values = Image._db_get(image_uuid)
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

    # Implementation
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

    def artifact(self):
        return self.__artifact

    def external_view(self):
        return self.__artifact.external_view()

    def delete(self):
        # NOTE(mikal): it isn't actually safe to remove the image from the cache
        # without verifying that no instance is using it, so we just mark the
        # image as deleted in the database and move on without removing things
        # from the cache. We will probably want to revisit this in the future.
        self.state = self.STATE_DELETED

    def get(self, locks, related_object):
        self.state = self.STATE_CREATING
        url = self.url

        # If this is a request for a URL, do we have the most recent version
        # somewhere in the cluster?
        if not url.startswith(BLOB_URL):
            most_recent = self.__artifact.most_recent_index
            dirty = False

            if most_recent.get('index', 0) == 0:
                self.log.with_fields({'url': url}).info(
                    'Cluster does not have a copy of image')
                dirty = True
            else:
                most_recent_blob = Blob.from_db(most_recent['blob_uuid'])
                resp = self._open_connection(self.url)

                if most_recent_blob.modified != resp.headers.get('Last-Modified'):
                    self.add_event('image requires fetch', None, None,
                                   'Last-Modified: %s -> %s' % (most_recent_blob.modified,
                                                                resp.headers.get('Last-Modified')))
                    dirty = True

                if most_recent_blob.size != resp.headers.get('Content-Length'):
                    self.add_event('image requires fetch', None, None,
                                   'Content-Length: %s -> %s' % (most_recent_blob.size,
                                                                 resp.headers.get('Content-Length')))
                    dirty = True

                self.log.with_fields({'url': url}).info(
                    'Cluster cached image is stale')

            if not dirty:
                url = '%s%s' % (BLOB_URL, most_recent_blob.uuid)
                self.log.with_fields({'url': url}).info(
                    'Using cached image from cluster')

        # Ensure that we have the blob in the local store. This blob is in the
        # "original format" if downloaded from an HTTP source.
        if url.startswith(BLOB_URL):
            self.log.with_fields({'url': url}).info(
                'Fetching image from within the cluster')
            b = self._blob_get(url, locks, related_object)
        else:
            self.log.with_fields({'url': url}).info(
                'Fetching image from the internet')
            b = self._http_get_inner(url, locks, related_object)

        # Transcode if required, placing the transcoded file in a well known location.
        if not os.path.exists(os.path.join(config.STORAGE_PATH, 'image_cache', b.uuid + '.qcow2')):
            blob_path = os.path.join(config.STORAGE_PATH, 'blobs', b.uuid)
            if b.info.get('mime-type', '') == 'application/gzip':
                cache_path = os.path.join(
                    config.STORAGE_PATH, 'image_cache', b.uuid)
                with util.RecordedOperation('decompress image', related_object):
                    util.execute(locks, 'gunzip -k -q -c %s > %s'
                                 % (blob_path, cache_path))
                blob_path = cache_path

            os.makedirs(
                os.path.join(config.STORAGE_PATH, 'image_cache'), exist_ok=True)
            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', b.uuid + '.qcow2')
            if identify(blob_path).get('file format', '') == 'qcow2':
                try:
                    os.link(blob_path, cache_path)
                    self.log.with_fields({'blob': b}).info(
                        'Hard linking %s -> %s' % (blob_path, cache_path))
                except OSError:
                    os.symlink(blob_path, cache_path)
                    self.log.with_fields({'blob': b}).info(
                        'Symbolic linking %s -> %s' % (blob_path, cache_path))

                shutil.chown(cache_path, config.LIBVIRT_USER,
                             config.LIBVIRT_GROUP)
            else:
                with util.RecordedOperation('transcode image', related_object):
                    self.log.with_fields({'blob': b}).info(
                        'Transcoding %s -> %s' % (blob_path, cache_path))
                    create_qcow2(locks, blob_path, cache_path)

            shutil.chown(cache_path, 'libvirt-qemu', 'libvirt-qemu')
            self.log.with_fields(util.stat_log_fields(cache_path)).info(
                'Cache file %s created' % cache_path)

        self.__artifact.state = Artifact.STATE_CREATED
        self.state = self.STATE_CREATED

    def _blob_get(self, url, locks, related_object):
        """Fetch a blob from the cluster."""

        blob_uuid = url[len(BLOB_URL):]
        blob_dir = os.path.join(config.STORAGE_PATH, 'blobs')
        blob_path = os.path.join(blob_dir, blob_uuid)
        os.makedirs(blob_dir, exist_ok=True)

        b = Blob.from_db(blob_uuid)
        locations = b.locations
        random.shuffle(locations)
        blob_source = locations[0]

        if not os.path.exists(blob_path):
            with util.RecordedOperation('fetch blob', related_object):
                url = 'http://%s:%d/blob/%s' % (blob_source, config.API_PORT,
                                                blob_uuid)
                admin_token = util.get_api_token(
                    'http://%s:%d' % (blob_source, config.API_PORT))
                r = requests.request('GET', url,
                                     headers={'Authorization': admin_token,
                                              'User-Agent': util.get_user_agent()})

                with open(blob_path + '.partial', 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

                os.rename(blob_path + '.partial', blob_path)
                b.observe()

        return b

    def _http_get_inner(self, url, locks, related_object):
        """Fetch image if not downloaded and return image path."""

        with util.RecordedOperation('fetch image', related_object):
            resp = self._open_connection(url)
            blob_uuid = str(uuid.uuid4())
            b = blob.http_fetch(resp, blob_uuid, locks, self.log)

            # Ensure checksum is correct
            if not self.correct_checksum(
                    os.path.join(config.STORAGE_PATH, 'blobs', b.uuid)):
                if isinstance(related_object, instance.Instance):
                    related_object.add_event('fetch image', 'bad checksum')
                raise exceptions.BadCheckSum('url=%s' % url)

            # Only persist values after the file has been verified.
            b.observe()
            self.__artifact.add_index(b.uuid)

            return b

    def _open_connection(self, url):
        proxies = {}
        if config.HTTP_PROXY_SERVER:
            proxies['http'] = config.HTTP_PROXY_SERVER

        resp = requests.get(url, allow_redirects=True, stream=True,
                            headers={'User-Agent': util.get_user_agent()},
                            proxies=proxies)
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch HEAD of %s (status code %d)'
                % (url, resp.status_code))
        return resp

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


VALUE_WITH_BRACKETS_RE = re.compile(r'.* \(([0-9]+) bytes\)')


def identify(path):
    """Work out what an image is."""

    if not os.path.exists(path):
        return {}

    out, _ = util.execute(None, 'qemu-img info %s' % path)

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

    if disk_size:
        util.execute(locks,
                     'qemu-img create -b %s -f qcow2 %s %dG'
                     % (cache_file, disk_file, int(disk_size)))
    else:
        util.execute(locks,
                     'qemu-img create -b %s -f qcow2 %s'
                     % (cache_file, disk_file))


def create_qcow2(locks, cache_file, disk_file):
    """Make a qcow2 copy of the disk from the image cache."""

    if os.path.exists(disk_file):
        return

    util.execute(locks,
                 'qemu-img convert -t none -O qcow2 %s %s'
                 % (cache_file, disk_file))


def create_blank(locks, disk_file, disk_size):
    """Make an empty image."""

    if os.path.exists(disk_file):
        return

    util.execute(locks, 'qemu-img create -f qcow2 %s %sG'
                 % (disk_file, disk_size))


def snapshot(locks, source, destination):
    """Convert a possibly COW layered disk file into a snapshot."""

    util.execute(locks,
                 'qemu-img convert --force-share -O qcow2 %s %s'
                 % (source, destination))


def resize(locks, input, output, size):
    if os.path.exists(output):
        return

    current_size = identify(input).get('virtual size')

    if current_size == size * 1024 * 1024 * 1024:
        os.link(input, output)
        return

    create_cow(locks, input, output, size)
