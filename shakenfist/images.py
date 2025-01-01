import copy
import os
import pathlib
import re
import shutil
import time
import uuid

import requests
from shakenfist_utilities import logs  # noreorder

from shakenfist import blob
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist.artifact import Artifact
from shakenfist.artifact import BLOB_URL
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.constants import LOCK_REFRESH_SECONDS
from shakenfist.constants import QCOW2_CLUSTER_SIZE
from shakenfist.constants import TRANSCODE_DESCRIPTION
from shakenfist.eventlog import add_event_multi
from shakenfist.tasks import ArchiveTranscodeTask
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


VALID_SF_IMAGES = ['centos', 'debian', 'fedora', 'rocky', 'ubuntu']


def _resolve_image(url):
    # Short cut URLs do not have subdirectories
    if url.find('/') != -1:
        return url

    if url.startswith('cirros'):
        # cirros is a special case as we can't rebuild the images yet
        return _resolve_cirros(url)

    for valid in VALID_SF_IMAGES:
        if url.startswith(valid):
            return f'{config.IMAGE_DOWNLOAD_URL}{url}/latest.qcow2'

    return url


def _resolve_cirros(name):
    # Name is assumed to be in the form cirros or cirros:0.4.0
    if name == 'cirros':
        resp = requests.get(config.LISTING_URL_CIRROS,
                            allow_redirects=True,
                            headers={'User-Agent': util_general.get_user_agent()})
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch %s, status code %d'
                % (config.LISTING_URL_CIRROS, resp.status_code))

        versions = []
        dir_re = re.compile(r'.*<a href="([0-9]+\.[0-9]+\.[0-9]+)/">.*/</a>.*')
        for line in resp.text.split('\n'):
            m = dir_re.match(line)
            if m:
                versions.append(m.group(1))
        LOG.with_fields({'versions': versions}).info('Found cirros versions')
        vernum = versions[-1]
    else:
        try:
            _, vernum = name.split(':')
        except Exception:
            raise exceptions.VersionSpecificationError(
                'Cannot parse version: %s' % name)

    url = str(config.DOWNLOAD_URL_CIRROS) % {'vernum': vernum}

    checksum_url = str(config.CHECKSUM_URL_CIRROS) % {'vernum': vernum}
    checksums = _fetch_remote_checksum(checksum_url)
    checksum = checksums.get(os.path.basename(url))
    LOG.with_fields({
        'name': name,
        'url': url,
        'checksum': checksum
    }).info('Image resolved')

    if checksum:
        return url
    else:
        return url


def _fetch_remote_checksum(checksum_url):
    resp = requests.get(checksum_url,
                        headers={'User-Agent': util_general.get_user_agent()})
    if resp.status_code != 200:
        return {}

    checksums = {}
    for line in resp.text.split('\n'):
        elems = line.split()
        if len(elems) == 2:
            checksums[elems[1]] = elems[0]
    return checksums


class ImageFetchHelper:
    def __init__(self, inst, artifact):
        self.instance = inst
        self.artifact = artifact

        self.objects = [self.artifact]
        if self.instance:
            self.objects.append(self.instance)

    def get_image(self):
        fetched_blobs = []
        with self.artifact.get_lock(ttl=(12 * LOCK_REFRESH_SECONDS),
                                    timeout=config.MAX_IMAGE_TRANSFER_SECONDS,
                                    op='get image') as lock:
            # Transfer the requested image, in its original format, from either
            # within the cluster (if we have it cached), or from the source. This
            # means that even if we have a cached post transcode version of the image
            # we insist on having the original locally. This was mostly done because
            # I am lazy, but it also serves as a partial access check.
            fetched_blobs.append(self.transfer_image(lock))

            # If the image depends on another image, we must fetch that too.
            while depends_on := fetched_blobs[-1].depends_on:
                add_event_multi(
                    EVENT_TYPE_STATUS, self.objects, 'fetching dependency',
                    extra={
                        'parent_blob_uuid': fetched_blobs[-1].uuid,
                        'child_blob_uuid': depends_on
                        })
                fetched_blobs.append(
                    self._blob_get(lock, 'sf://blob/%s' % depends_on))

            # We might already have a transcoded version of the image cached. If so
            # we use that. Otherwise, we might have a transcoded version within the
            # cluster, in which case we fetch it. The final option is we do an
            # actual transcode ourselves. We need to do it this way because thin
            # snapshots mean we need to try quite hard to have a static post
            # transcode version of the image, and we don't completely trust the
            # transcode process to be deterministic.
            for b in fetched_blobs:
                self.transcode_image(lock, b)

    def transfer_image(self, lock):
        # NOTE(mikal): it is assumed the caller holds a lock on the artifact, and passes
        # it in.

        url = _resolve_image(self.artifact.source_url)

        # If this is a request for a URL, do we have the most recent version
        # somewhere in the cluster?
        if not url.startswith(BLOB_URL):
            most_recent = self.artifact.most_recent_index
            dirty = False

            if most_recent.get('index', 0) == 0:
                add_event_multi(
                    EVENT_TYPE_STATUS, self.objects,
                    'cluster does not have a copy of image')
                dirty = True
            else:
                most_recent_blob = blob.Blob.from_db(most_recent['blob_uuid'])

                try:
                    resp = self._open_connection(url)
                except exceptions.HTTPError as e:
                    self.artifact.add_event(
                        EVENT_TYPE_AUDIT,
                        'image fetch had HTTP error, not fetching image',
                        extra={'error': str(e)})
                else:
                    normalized_new_timestamp = blob.Blob.normalize_timestamp(
                        resp.headers.get('Last-Modified'))

                    if not most_recent_blob:
                        dirty = True
                    else:
                        if not most_recent_blob.modified:
                            self.artifact.add_event(
                                EVENT_TYPE_AUDIT,
                                'image requires fetch, no Last-Modified recorded')
                            dirty = True
                        elif most_recent_blob.modified != normalized_new_timestamp:
                            self.artifact.add_event(
                                EVENT_TYPE_AUDIT,
                                'image requires fetch, Last-Modified changed',
                                extra={
                                    'old': most_recent_blob.modified,
                                    'new': normalized_new_timestamp
                                })
                            dirty = True

                        response_size = resp.headers.get('Content-Length')
                        if response_size:
                            response_size = int(response_size)

                        if not most_recent_blob.size:
                            self.artifact.add_event(
                                EVENT_TYPE_AUDIT,
                                'image requires fetch, no Content-Length recorded')
                            dirty = True
                        elif most_recent_blob.size != response_size:
                            self.artifact.add_event(
                                EVENT_TYPE_AUDIT,
                                'image requires fetch, Content-Length changed',
                                extra={
                                    'old': most_recent_blob.size,
                                    'new': response_size
                                })
                            dirty = True

            if not dirty:
                url = f'{BLOB_URL}{most_recent_blob.uuid}'

        # Ensure that we have the blob in the local store. This blob is in the
        # "original format" if downloaded from an HTTP source.
        if url.startswith(BLOB_URL):
            add_event_multi(
                EVENT_TYPE_STATUS, self.objects,
                'fetching image from within the cluster')
            b = self._blob_get(lock, url)
        else:
            add_event_multi(
                EVENT_TYPE_STATUS, self.objects,
                'fetching image from the internet')
            b = self._http_get_inner(lock, url)

        return b

    def transcode_image(self, lock, b):
        # NOTE(mikal): it is assumed the caller holds a lock on the artifact, and passes
        # it in lock.

        # If this blob uuid is not the most recent index for the artifact, set that
        if self.artifact.most_recent_index.get('blob_uuid') != b.uuid:
            self.artifact.add_index(b.uuid)

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

        # See if we have a remotely cached transcode _which_actually_exists_.
        cached_remotely = b.transcoded.get(TRANSCODE_DESCRIPTION)
        cached_remotely_blob = blob.Blob.from_db(cached_remotely)
        if not cached_remotely_blob:
            cached_remotely = None

        cache_path = None

        if cached_locally:
            # We touch the file here, because we want to know when it was last used.
            pathlib.Path(cached_locally).touch(exist_ok=True)
            return

        elif mimetype in ['application/x-cd-image', 'application/x-iso9660-image']:
            blob_path = blob.Blob.filepath(b.uuid)
            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', b.uuid + '.iso')
            if not os.path.exists(cache_path):
                util_general.link(blob_path, cache_path)

        elif cached_remotely:
            remote_blob = blob.Blob.from_db(cached_remotely)
            if not remote_blob:
                raise exceptions.BlobMissing(cached_remotely)
            remote_blob.ensure_local([lock], instance_object=self.instance)

            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', b.uuid + '.qcow2')
            remote_blob_path = blob.Blob.filepath(remote_blob.uuid)
            if not os.path.exists(cache_path):
                util_general.link(remote_blob_path, cache_path)

        else:
            blob_path = blob.Blob.filepath(b.uuid)

            if mimetype == 'application/gzip':
                cache_path = os.path.join(
                    config.STORAGE_PATH, 'image_cache', b.uuid)
                with util_general.RecordedOperation('decompress image', self.instance):
                    util_concurrency.execute(
                        [lock], f'gunzip -k -q -c {blob_path} > {cache_path}')
                blob_path = cache_path

            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', b.uuid + '.qcow2')
            cache_info = util_image.identify(blob_path)

            cluster_size_as_int = int(util_image.convert_numeric_qemu_value(
                QCOW2_CLUSTER_SIZE))

            if (cache_info.get('file format', '') == 'qcow2' and
                    cache_info.get('cluster_size', 0) == cluster_size_as_int):
                try:
                    util_general.link(blob_path, cache_path)
                except FileExistsError:
                    ...
            else:
                objects_with_blob = copy.deepcopy(self.objects)
                objects_with_blob.append(b)
                with util_general.RecordedOperation('transcode image', self.instance):
                    add_event_multi(
                        EVENT_TYPE_STATUS, objects_with_blob, 'transcoding blob')
                    util_image.create_qcow2([lock], blob_path, cache_path)

                # We will cache this transcode, but we do it later as part of a
                # task so the instance isn't waiting for it.
                etcd.enqueue(
                    f'{config.NODE_NAME}-background',
                    {
                        'tasks': [
                            ArchiveTranscodeTask(
                                b.uuid, cache_path, TRANSCODE_DESCRIPTION)]
                    })

        shutil.chown(cache_path, config.LIBVIRT_USER, config.LIBVIRT_GROUP)
        add_event_multi(
            EVENT_TYPE_STATUS, self.objects, f'cache file {cache_path} created',
            extra=util_general.stat_log_fields(cache_path))

        if self.artifact.state.value == Artifact.STATE_INITIAL:
            self.artifact.state = Artifact.STATE_CREATED

    def _blob_get(self, lock, url):
        """Fetch a blob from the cluster."""

        blob_uuid = url[len(BLOB_URL):]

        b = blob.Blob.from_db(blob_uuid)
        if not b:
            raise exceptions.BlobMissing(blob_uuid)

        b.ensure_local([lock], instance_object=self.instance)
        return b

    def _http_get_inner(self, lock, url):
        """Fetch image if not downloaded and return image path."""

        with util_general.RecordedOperation('fetch image', self.instance):
            resp = self._open_connection(url)
            blob_uuid = str(uuid.uuid4())
            b = blob.Blob.new(
                blob_uuid, resp.headers.get('Last-Modified'), time.time())

            objects_including_blob = copy.deepcopy(self.objects)
            objects_including_blob.append(b)
            add_event_multi(
                EVENT_TYPE_STATUS, objects_including_blob,
                'commencing HTTP fetch to blob', extra={'url': url})

            try:
                blob.http_fetch(url, resp, b, [lock], objects_including_blob)
            except exceptions.BadCheckSum as e:
                add_event_multi(
                    EVENT_TYPE_AUDIT, objects_including_blob,
                    'fetched image had bad checksum', extra={'url': url})
                raise e

            return b

    def _open_connection(self, url):
        proxies = {}
        if config.HTTP_PROXY_SERVER:
            proxies['http'] = config.HTTP_PROXY_SERVER

        resp = requests.get(url, allow_redirects=True, stream=True,
                            headers={'User-Agent': util_general.get_user_agent()},
                            proxies=proxies)
        if resp.status_code != 200:
            raise exceptions.HTTPError(
                'Failed to fetch HEAD of %s (status code %d)'
                % (url, resp.status_code))
        return resp
