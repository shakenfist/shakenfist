import hashlib
import os
import pathlib
import requests
import shutil
import time
import uuid

from shakenfist.artifact import Artifact, BLOB_URL
from shakenfist import blob
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import QCOW2_CLUSTER_SIZE, LOCK_REFRESH_SECONDS
from shakenfist import exceptions
from shakenfist import image_resolver
from shakenfist import logutil
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image
from shakenfist.util import process as util_process


LOG, _ = logutil.setup(__name__)
TRANSCODE_DESCRIPTION = 'gunzip;qcow2;cluster_size'


class ImageFetchHelper(object):
    def __init__(self, inst, url):
        self.instance = inst
        self.url = url

        self.__artifact = Artifact.from_url(Artifact.TYPE_IMAGE, self.url)
        self.log = LOG.with_fields(
            {'url': self.url, 'artifact': self.__artifact.uuid})

    def get_image(self):
        with self.__artifact.get_lock(ttl=(12 * LOCK_REFRESH_SECONDS),
                                      timeout=config.MAX_IMAGE_TRANSFER_SECONDS) as lock:
            # Transfer the requested image, in its original format, from either
            # within the cluster (if we have it cached), or from the source. This
            # means that even if we have a cached post transcode version of the image
            # we insist on having the original locally. This was mostly done because
            # I am lazy, but it also serves as a partial access check.
            b = self.transfer_image(lock)

            # We might already have a trancoded version of the image cached. If so
            # we use that. Otherwise, we might have a transcoded version within the
            # cluster, in which case we fetch it. The final option is we do an
            # actual transcode ourselves. We need to do it this way because thin
            # snapshots mean we need to try quite hard to have a static post
            # transcode version of the image, and we don't completely trust the
            # transcode process to be deterministic.
            self.transcode_image(lock, b)

    def transfer_image(self, lock):
        # NOTE(mikal): it is assumed the caller holds a lock on the artifact, and passes
        # it in.

        url, checksum, checksum_type = image_resolver.resolve(self.url)

        # If this is a request for a URL, do we have the most recent version
        # somewhere in the cluster?
        if not url.startswith(BLOB_URL):
            most_recent = self.__artifact.most_recent_index
            dirty = False

            if most_recent.get('index', 0) == 0:
                self.log.info('Cluster does not have a copy of image')
                dirty = True
            else:
                most_recent_blob = Blob.from_db(most_recent['blob_uuid'])
                resp = self._open_connection(url)

                if not most_recent_blob.modified:
                    dirty = True
                elif most_recent_blob.modified != resp.headers.get('Last-Modified'):
                    self.__artifact.add_event(
                        'image requires fetch', None, None,
                        'Last-Modified: %s -> %s' % (most_recent_blob.modified,
                                                     resp.headers.get('Last-Modified')))
                    dirty = True

                if not most_recent_blob.size:
                    dirty = True
                elif most_recent_blob.size != resp.headers.get('Content-Length'):
                    self.__artifact.add_event(
                        'image requires fetch', None, None,
                        'Content-Length: %s -> %s' % (most_recent_blob.size,
                                                      resp.headers.get('Content-Length')))
                    dirty = True

            if dirty:
                self.log.info('Cluster cached image is stale')
            else:
                url = '%s%s' % (BLOB_URL, most_recent_blob.uuid)
                self.log.info('Using cached image from cluster')

        # Ensure that we have the blob in the local store. This blob is in the
        # "original format" if downloaded from an HTTP source.
        if url.startswith(BLOB_URL):
            self.log.info('Fetching image from within the cluster')
            b = self._blob_get(lock, url)
        else:
            self.log.info('Fetching image from the internet')
            b = self._http_get_inner(lock, url, checksum, checksum_type)
            # Ref count increased here since it is known here whether the blob
            # will be used from within the cluster or newly created.
            b.ref_count_inc()

        return b

    def transcode_image(self, lock, b):
        # NOTE(mikal): it is assumed the caller holds a lock on the artifact, and passes
        # it in lock.

        # If this blob uuid is not the most recent index for the artifact, set that
        if self.__artifact.most_recent_index.get('blob_uuid') != b.uuid:
            self.__artifact.add_index(b.uuid)

        # Transcode if required, placing the transcoded file in a well known location.
        # Note that even if we cache the transcoded version as another blob, the
        # transcoded version is stored in the image cache under the original blob's
        # UUID.
        os.makedirs(
            os.path.join(config.STORAGE_PATH, 'image_cache'), exist_ok=True)
        cached_locally = util_general.file_permutation_exists(
            os.path.join(config.STORAGE_PATH, 'image_cache', b.uuid),
            ['iso', 'qcow2'])
        mimetype = b.info.get('mime-type', '')
        cached_remotely = b.transcoded.get(TRANSCODE_DESCRIPTION)
        cache_path = None

        if cached_locally:
            # We touch the file here, because we want to know when it was last used.
            pathlib.Path(cached_locally).touch(exist_ok=True)
            return

        elif mimetype in ['application/x-cd-image', 'application/x-iso9660-image']:
            blob_path = os.path.join(config.STORAGE_PATH, 'blobs', b.uuid)
            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', b.uuid + '.iso')
            util_general.link(blob_path, cache_path)

        elif cached_remotely:
            remote_blob = Blob.from_db(cached_remotely)
            if not remote_blob:
                raise exceptions.BlobMissing(cached_remotely)
            remote_blob.ensure_local([lock])

            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', b.uuid + '.qcow2')
            remote_blob_path = os.path.join(
                config.STORAGE_PATH, 'blobs', remote_blob.uuid)
            util_general.link(remote_blob_path, cache_path)

        else:
            blob_path = os.path.join(config.STORAGE_PATH, 'blobs', b.uuid)

            if mimetype == 'application/gzip':
                cache_path = os.path.join(
                    config.STORAGE_PATH, 'image_cache', b.uuid)
                with util_general.RecordedOperation('decompress image', self.instance):
                    util_process.execute(
                        [lock], 'gunzip -k -q -c %s > %s' % (blob_path, cache_path))
                blob_path = cache_path

            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', b.uuid + '.qcow2')
            cache_info = util_image.identify(blob_path)

            cluster_size_as_int = int(util_image.convert_numeric_qemu_value(
                QCOW2_CLUSTER_SIZE))

            if (cache_info.get('file format', '') == 'qcow2' and
                    cache_info.get('cluster_size', 0) == cluster_size_as_int):
                util_general.link(blob_path, cache_path)
            else:
                with util_general.RecordedOperation('transcode image', self.instance):
                    self.log.with_object(b).info(
                        'Transcoding %s -> %s' % (blob_path, cache_path))
                    util_image.create_qcow2(
                        [lock], blob_path, cache_path)

            # Now create the cache of the transcode output
            remote_blob_uuid = str(uuid.uuid4())
            remote_blob_path = os.path.join(
                config.STORAGE_PATH, 'blobs', remote_blob_uuid)
            shutil.copyfile(cache_path, remote_blob_path)
            st = os.stat(remote_blob_path)

            remote_blob = Blob.new(
                remote_blob_uuid, st.st_size, time.time(), time.time())
            remote_blob.state = Blob.STATE_CREATED
            remote_blob.observe()
            remote_blob.request_replication()

            b.add_transcode(TRANSCODE_DESCRIPTION, remote_blob_uuid)
            remote_blob.ref_count_inc()

        shutil.chown(cache_path, config.LIBVIRT_USER,
                     config.LIBVIRT_GROUP)
        self.log.with_fields(util_general.stat_log_fields(cache_path)).info(
            'Cache file %s created' % cache_path)
        self.__artifact.state = Artifact.STATE_CREATED

    def _blob_get(self, lock, url):
        """Fetch a blob from the cluster."""

        blob_uuid = url[len(BLOB_URL):]

        b = Blob.from_db(blob_uuid)
        if not b:
            raise exceptions.BlobMissing(blob_uuid)

        b.ensure_local([lock])
        return b

    def _http_get_inner(self, lock, url, checksum, checksum_type):
        """Fetch image if not downloaded and return image path."""

        with util_general.RecordedOperation('fetch image', self.instance):
            resp = self._open_connection(url)
            blob_uuid = str(uuid.uuid4())
            self.log.with_object(self.__artifact).with_fields({
                'blob': blob_uuid,
                'url': url}).info('Commencing HTTP fetch to blob')
            b = blob.http_fetch(resp, blob_uuid, [lock], self.log)

            # Ensure checksum is correct
            if not verify_checksum(
                    os.path.join(config.STORAGE_PATH, 'blobs', b.uuid),
                    checksum, checksum_type):
                self.instance.add_event('fetch image', 'bad checksum')
                raise exceptions.BadCheckSum('url=%s' % url)

            # Only persist values after the file has been verified.
            b.observe()
            b.request_replication()
            return b

    def _open_connection(self, url):
        proxies = {}
        if config.HTTP_PROXY_SERVER:
            proxies['http'] = config.HTTP_PROXY_SERVER

        resp = requests.get(url, allow_redirects=True, stream=True,
                            headers={
                                'User-Agent': util_general.get_user_agent()},
                            proxies=proxies)
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch HEAD of %s (status code %d)'
                % (url, resp.status_code))
        return resp


def verify_checksum(image_name, checksum, checksum_type):
    log = LOG.with_field('image', image_name)

    if not checksum:
        log.info('No checksum comparison available')
        return True

    if not os.path.exists(image_name):
        return False

    if checksum_type == 'md5':
        # MD5 chosen because cirros 90% of the time has MD5SUMS available...
        md5_hash = hashlib.md5()
        with open(image_name, 'rb') as f:
            for byte_block in iter(lambda: f.read(4096), b''):
                md5_hash.update(byte_block)
        calc = md5_hash.hexdigest()
        log.with_field('calc', calc).debug('Calc from image download')

        correct = calc == checksum
        log.with_field('correct', correct).info('Image checksum verification')
        return correct

    else:
        raise exceptions.UnknownChecksumType(checksum_type)
