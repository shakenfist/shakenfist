import copy
import os
import pathlib
import re
import shutil
import time
import urllib.parse
import uuid
from typing import NamedTuple

import requests
from shakenfist_utilities import logs  # noreorder

from shakenfist import blob
from shakenfist import exceptions
from shakenfist.artifact import Artifact
from shakenfist.artifact import BLOB_URL
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import QCOW2_CLUSTER_SIZE
from shakenfist.constants import TRANSCODE_DESCRIPTION
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.imgcache_op \
    import create_and_enqueue as ic_create_and_enqueue
from shakenfist.schema.operations.imgcache_op \
    import model_tasks as ic_tasks
from shakenfist.eventlog import add_event_multi
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


VALID_SF_IMAGES = ['centos', 'debian', 'fedora', 'rocky', 'ubuntu']

# The schemes _open_connection() can actually fetch, because it hands the
# string to requests.get() and requests has no adapter for anything else.
FETCHABLE_SCHEMES = ['http', 'https']


def is_image_shortcut(url):
    """Is this one of the shortcut forms _expand_image_url() expands?

    A shortcut names an image on config.IMAGE_DOWNLOAD_URL (or a cirros
    release) rather than a URL: 'debian', 'debian:11', 'cirros:0.4.0'.
    Shortcuts have no subdirectory, which is how they are told apart
    from a URL.

    _expand_image_url() is the only consumer of this distinction and
    calls it rather than restating it, because this is also what decides
    whether a scheme-less base is fetchable at all (see is_fetchable).
    """
    if url.find('/') != -1:
        return False

    if url.startswith('cirros'):
        return True

    for valid in VALID_SF_IMAGES:
        if url.startswith(valid):
            return True

    return False


def is_fetchable(url):
    """Could an image fetch of this source URL ever succeed?

    A fetch resolves the URL and hands the result to requests.get(),
    which raises MissingSchema or InvalidSchema for anything which is
    not http or https -- on a hypervisor, long after the API accepted
    it, and the artifact is retried and then errored (issue 4557). A
    shortcut is fetchable because _expand_image_url() turns it into an
    http(s) URL before requests sees it.

    This says nothing about whether the URL resolves, responds, or
    serves an image: those are all answered on the node. It answers
    only the question an API handler can answer for itself.

    It is also not the whole question for an *artifact's* source url,
    which transfer_image() serves from within the cluster when it is a
    BLOB_URL rather than fetching it at all. A caller who means "may
    this artifact's source url be refetched" needs that case too; this
    one is for a disk base, where the sf:// prefixes are dispatched to
    a blob before the plain URL branch is reached.
    """
    if not isinstance(url, str):
        return False

    if is_image_shortcut(url):
        return True

    # urlparse raises on a string it cannot parse at all -- an unbalanced
    # bracket in the authority, for example. A disk base is not format
    # validated (validation._format_url explains why the published `url`
    # format cannot be applied to it), so this is reachable from a
    # request body and must be an answer rather than a server error.
    try:
        scheme = urllib.parse.urlparse(url).scheme
    except ValueError:
        return False

    return scheme in FETCHABLE_SCHEMES


class ResolvedImage(NamedTuple):
    """A fetchable URL, and the digest the mirror published for it.

    ``algorithm`` and ``checksum`` are both None when the mirror
    publishes no digest we can read. They travel together because the
    algorithm is not fixed -- see CHECKSUM_FILES -- and a checksum
    without the algorithm which produced it cannot be checked.
    """

    url: str
    algorithm: 'str | None' = None
    checksum: 'str | None' = None

    @property
    def verifiable(self):
        """Is there a digest a fetch of this URL can be checked against?

        This is the predicate blob.http_fetch() actually verifies on,
        named once rather than restated: the audit event, the
        REQUIRE_IMAGE_CHECKSUM refusal and the fetch itself must not be
        able to disagree about whether a check can happen.
        """
        return bool(self.algorithm and self.checksum)


class ExpandedImage(NamedTuple):
    """A fetchable URL, before any digest has been fetched for it.

    Reading the published digest costs up to one fetch per entry in
    CHECKSUM_FILES, each bounded by CHECKSUM_FETCH_TIMEOUT, so it is a
    separate step taken only by a caller which is about to fetch the
    image from the mirror. A caller which can serve the image from the
    cluster, or which is forbidden to consult the mirror at all, pays
    for none of it (issue 4976).

    ``vernum`` is the cirros release the URL names, which is how the
    digest files are addressed. It is None for everything else, which
    is also how "there is no digest to go looking for" is said.
    """

    url: str
    vernum: 'str | None' = None


# The digest files a mirror may publish beside its images, strongest
# first. A resolver takes the first one which exists.
#
# cirros publishes SHA256SUMS only from 0.6.3 onwards: 0.4.0, 0.5.2 and
# 0.6.1 have MD5SUMS and nothing else (checked against the mirror). So
# md5 cannot simply be dropped, even though it is not a hash anyone
# should be relying on in 2026 and is deliberately absent from
# BLOB_HASH_ALGORITHMS. It is kept as a fallback, and which algorithm
# actually verified an image is recorded on the event, so an operator
# can tell the two apart.
CHECKSUM_FILES = [
    ('sha256', 'SHA256SUMS'),
    ('md5', 'MD5SUMS'),
]

# (connect, read) seconds for fetching a digest file. It is a few
# hundred bytes; a mirror which takes longer than this is treated as
# one whose digest could not be read, and says so.
CHECKSUM_FETCH_TIMEOUT = (10, 30)


def _expand_image_url(url):
    """Expand a source URL into something requests could fetch.

    No digest is read here -- see ExpandedImage for why that is a
    separate step.
    """
    # Short cut URLs do not have subdirectories
    if not is_image_shortcut(url):
        return ExpandedImage(url)

    if url.startswith('cirros'):
        # cirros is a special case as we can't rebuild the images yet
        return _expand_cirros_url(url)

    return ExpandedImage(f'{config.IMAGE_DOWNLOAD_URL}{url}/latest.qcow2')


def _resolve_image(url, expanded=None):
    """Expand a source URL, with any digest the mirror publishes.

    Always a ResolvedImage. A plain URL resolves to one with no
    checksum, which is the honest answer: we have nothing to check it
    against, which is a different thing from having checked it.

    `expanded` is an ExpandedImage the caller has already paid for, so
    that a caller which expanded the URL to ask the mirror about its
    cached copy does not expand it a second time.
    """
    if expanded is None:
        expanded = _expand_image_url(url)

    if expanded.vernum is None:
        return ResolvedImage(expanded.url)

    algorithm, checksum = _resolve_cirros_checksum(
        expanded.url, expanded.vernum)
    LOG.with_fields({
        'name': url,
        'url': expanded.url,
        'algorithm': algorithm,
        'checksum': checksum
    }).info('Image resolved')

    return ResolvedImage(expanded.url, algorithm, checksum)


def _expand_cirros_url(name):
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

    return ExpandedImage(
        str(config.DOWNLOAD_URL_CIRROS) % {'vernum': vernum}, vernum)


def _resolve_cirros_checksum(url, vernum):
    """The published digest for a cirros image, strongest first.

    Returns (algorithm, checksum), or (None, None) when the mirror
    publishes no digest we can read. Returning no checksum is not the
    same as returning one which does not match: the caller must not be
    able to confuse them, which is why this is a pair and not a string.

    A digest is only ever labelled with the algorithm its length says
    produced it. The file name it came from is a hint, and a wrong one
    -- an MD5SUMS served where SHA256SUMS was asked for -- would
    otherwise fail every fetch with a checksum mismatch which was
    nothing of the sort.
    """
    filename = os.path.basename(url)
    template = str(config.CHECKSUM_URL_CIRROS)

    if '%(digest_file)s' not in template:
        # A template from before the digest file was a substitution,
        # which names one file -- MD5SUMS, when it was the shipped
        # default. Still a published digest worth checking against, so
        # read it and let the digest say what it is.
        checksum_url = template % {'vernum': vernum}
        checksums = _fetch_remote_checksum(checksum_url)
        checksum = (checksums or {}).get(filename)
        algorithm = _algorithm_for_digest(checksum)
        if checksum and not algorithm:
            LOG.with_fields({
                'url': checksum_url,
                'checksum': checksum
            }).warning('Published checksum is not a digest we recognise')
        if algorithm:
            return algorithm, checksum
        return _no_cirros_checksum(url, vernum)

    for algorithm, digest_file in CHECKSUM_FILES:
        checksum_url = template % {
            'vernum': vernum, 'digest_file': digest_file}
        checksums = _fetch_remote_checksum(checksum_url)
        if checksums is None:
            # The digest file could not be read at all. A mirror which
            # is briefly unwell must not silently downgrade us to a
            # weaker algorithm, and must not look like a mirror which
            # publishes nothing -- try the next file, but say so.
            LOG.with_fields({
                'url': checksum_url,
                'algorithm': algorithm
            }).warning('Could not read published checksums')
            continue

        checksum = checksums.get(filename)
        if not checksum:
            # A file we could read which does not name our image. The
            # weaker file may, so carry on, but a parse which never
            # matches would otherwise turn verification off without a
            # word.
            LOG.with_fields({
                'url': checksum_url,
                'algorithm': algorithm,
                'filename': filename,
                'names': len(checksums)
            }).warning('Published checksums do not name this image')
            continue
        if _algorithm_for_digest(checksum) != algorithm:
            LOG.with_fields({
                'url': checksum_url,
                'algorithm': algorithm,
                'checksum': checksum
            }).warning('Published checksum is not the digest its file '
                       'name says it is, ignoring it')
            continue
        return algorithm, checksum

    return _no_cirros_checksum(url, vernum)


def _no_cirros_checksum(url, vernum):
    """Say so, and return the (None, None) which means it.

    Every cirros release we know of publishes a digest for its disk
    images, so arriving here means the mirror, the template or the
    parse is wrong, not that there is nothing to check against.
    """
    LOG.with_fields({
        'url': url,
        'vernum': vernum
    }).warning('No published checksum found for a cirros image, it will '
               'be fetched unverified')
    return None, None


# The length of each algorithm's hex digest, which is how a digest read
# from a file says which algorithm produced it.
DIGEST_LENGTHS = {
    32: 'md5',
    64: 'sha256',
    128: 'sha512',
}
HEX_DIGEST_RE = re.compile('[0-9a-fA-F]+')


def _algorithm_for_digest(checksum):
    """The algorithm which produced a hex digest, or None if unknown."""
    # Not int(checksum, 16), which also accepts a 0x prefix and
    # underscores: a string like that is not a digest, and labelling it
    # one fails the fetch as a mismatch rather than costing a warning.
    if not checksum or not HEX_DIGEST_RE.fullmatch(checksum):
        return None
    return DIGEST_LENGTHS.get(len(checksum))


def _fetch_remote_checksum(checksum_url):
    """Parse a `<digest>  <filename>` digest file.

    Returns a dict, or None when the file could not be fetched. An
    empty dict means the file exists and names no files, which is a
    different fact.

    The format is coreutils' sha256sum / md5sum output, which marks a
    file hashed in binary mode with a `*` before its name. cirros does
    not, but a mirror regenerating the files might, and a name which
    does not match is a check which silently does not happen.
    """
    try:
        resp = requests.get(
            checksum_url,
            headers={'User-Agent': util_general.get_user_agent()},
            timeout=CHECKSUM_FETCH_TIMEOUT)
    except requests.exceptions.RequestException:
        return None

    if resp.status_code != 200:
        return None

    checksums = {}
    for line in resp.text.split('\n'):
        elems = line.split()
        if len(elems) == 2:
            checksums[elems[1].removeprefix('*')] = elems[0]
    return checksums


class ImageFetchHelper:
    def __init__(self, inst, artifact):
        self.instance = inst
        self.artifact = artifact

        self.objects = [self.artifact]
        if self.instance:
            self.objects.append(self.instance)

    def get_image(self, cached_only=False):
        fetched_blobs = []
        with self.artifact.get_lock(timeout=config.MAX_IMAGE_TRANSFER_SECONDS,
                                    op='get image'):
            # Transfer the requested image, in its original format, from either
            # within the cluster (if we have it cached), or from the source. This
            # means that even if we have a cached post transcode version of the image
            # we insist on having the original locally. This was mostly done because
            # I am lazy, but it also serves as a partial access check.
            fetched_blobs.append(self.transfer_image(cached_only=cached_only))

            # If the image depends on another image, we must fetch that too.
            while depends_on := fetched_blobs[-1].depends_on:
                add_event_multi(
                    EVENT_TYPE_STATUS, self.objects, 'fetching dependency',
                    extra={
                        'parent_blob_uuid': fetched_blobs[-1].uuid,
                        'child_blob_uuid': depends_on
                        })
                fetched_blobs.append(
                    self._blob_get('sf://blob/%s' % depends_on)[0])

            # We might already have a transcoded version of the image cached. If so
            # we use that. Otherwise, we might have a transcoded version within the
            # cluster, in which case we fetch it. The final option is we do an
            # actual transcode ourselves. We need to do it this way because thin
            # snapshots mean we need to try quite hard to have a static post
            # transcode version of the image, and we don't completely trust the
            # transcode process to be deterministic.
            for b in fetched_blobs:
                self.transcode_image(b)

    def transfer_image(self, cached_only=False):
        """Fetch the artifact's image into the local blob store.

        With cached_only set the source URL is not consulted at all: the
        most recent version the cluster already holds is used, and
        BlobMissing is raised if there is no usable cached version. This
        is the fallback for a source which cannot be reached but whose
        image has previously been fetched (issue 3603).

        The source URL is expanded, and its digest resolved, only on
        the branch about to go to the mirror: doing either up front
        made this degraded path pay for round trips it threw away
        (issue 4976). A shortcut is never a BLOB_URL, so deciding
        whether the cluster can serve the image needs neither.
        """
        source_url = self.artifact.source_url
        expanded = None
        resolved = None

        # If this is a request for a URL, do we have the most recent version
        # somewhere in the cluster?
        if not source_url.startswith(BLOB_URL):
            most_recent = self.artifact.most_recent_index
            most_recent_blob = None
            dirty = False

            if most_recent.get('index', 0) == 0:
                add_event_multi(
                    EVENT_TYPE_STATUS, self.objects,
                    'cluster does not have a copy of image')
                dirty = True
            else:
                # The index must name a blob which still exists before we
                # can serve from cache, whether or not the source is
                # reachable. This used to be checked only when the source
                # responded, which crashed the "source is gone but we have
                # a cached copy" path with an AttributeError below.
                most_recent_blob = blob.Blob.from_db(most_recent['blob_uuid'])
                if not most_recent_blob:
                    dirty = True

            if not dirty and not cached_only:
                expanded = _expand_image_url(source_url)
                try:
                    resp = self._open_connection(expanded.url)
                except exceptions.HTTPError as e:
                    self.artifact.add_event(
                        EVENT_TYPE_AUDIT,
                        'image fetch had HTTP error, not fetching image',
                        extra={'error': str(e)})
                else:
                    normalized_new_timestamp = blob.Blob.normalize_timestamp(
                        resp.headers.get('Last-Modified'))

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
                # Serving from a copy the cluster already holds. That
                # blob was verified when it was fetched; the mirror's
                # digest is for the mirror's copy and says nothing about
                # this one, so it does not come along -- carrying it
                # would claim a check which did not happen.
                resolved = ResolvedImage(f'{BLOB_URL}{most_recent_blob.uuid}')
            elif cached_only:
                raise exceptions.BlobMissing(
                    f'Artifact {self.artifact.uuid} has no cached version '
                    'to fall back to')

        if resolved is None:
            # A mirror fetch is what happens next, so the digest is now
            # worth its round trips. Any expansion the staleness check
            # already paid for is handed back, so a bare `cirros` reads
            # the version listing at most once.
            resolved = _resolve_image(source_url, expanded=expanded)

        # Ensure that we have the blob in the local store. This blob is in the
        # "original format" if downloaded from an HTTP source.
        #
        # Both the branch and the fetch read `resolved`, so there is one
        # answer to "what are we fetching, and what may we check it
        # against" rather than two which can drift apart.
        # Whether the cluster already held this image is the one thing
        # about an image fetch nothing counted. The two branches below
        # have always said which way it went, but only as a message, so
        # a cache hit rate -- or the bytes a cluster pulls from the
        # internet -- could not be had without reading every fetch by
        # hand (issue 852).
        #
        # A copy the cluster holds is either already on this node or
        # has to be replicated here, and those are different costs: the
        # first moves nothing, the second moves the whole blob across
        # the cluster network. get_image() calls this on every instance
        # create, so without the distinction a node creating many
        # instances from one image would report each create as a cache
        # hit with the blob's size against it. ensure_local() says
        # which, from the check it acted on.
        fetch_started = time.time()

        if resolved.url.startswith(BLOB_URL):
            add_event_multi(
                EVENT_TYPE_STATUS, self.objects,
                'fetching image from within the cluster')
            b, replicated = self._blob_get(resolved.url)
            source = 'cluster' if replicated else 'node'
        else:
            source = 'internet'
            add_event_multi(
                EVENT_TYPE_STATUS, self.objects,
                'fetching image from the internet')
            b = self._http_get_inner(resolved)

        # One event per call, carrying where the image came from and
        # what it cost, so the hit rates and the bytes are a count over
        # the event stream rather than a reading exercise. That is once
        # per instance create from this artifact on this node, which
        # already writes a handful of status events per create, so one
        # more is in proportion. ``bytes`` is the blob's size whatever
        # the source; it is traffic only where ``source`` says something
        # moved. It is this blob only: a thin snapshot's parent chain is
        # replicated by ensure_local() and get_image()'s dependency walk
        # without being counted, so the sums are a lower bound there.
        add_event_multi(
            EVENT_TYPE_USAGE, self.objects, 'image fetch complete',
            extra={
                'source': source,
                'seconds': time.time() - fetch_started,
                'bytes': b.size,
                'blob_uuid': str(b.uuid),
            })

        return b

    def transcode_image(self, b):
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
            os.path.join(config.STORAGE_PATH, 'image_cache', str(b.uuid)),
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
                config.STORAGE_PATH, 'image_cache', f'{b.uuid}.iso')
            if not os.path.exists(cache_path):
                util_general.link(blob_path, cache_path)

        elif cached_remotely:
            remote_blob = blob.Blob.from_db(cached_remotely)
            if not remote_blob:
                raise exceptions.BlobMissing(
                    f'Failed to lookup blob {cached_remotely}')
            remote_blob.ensure_local(instance_object=self.instance)

            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', f'{b.uuid}.qcow2')
            remote_blob_path = blob.Blob.filepath(remote_blob.uuid)
            if not os.path.exists(cache_path):
                util_general.link(remote_blob_path, cache_path)

        else:
            blob_path = blob.Blob.filepath(b.uuid)

            if mimetype == 'application/gzip':
                cache_path = os.path.join(
                    config.STORAGE_PATH, 'image_cache', str(b.uuid))
                with util_general.RecordedOperation('decompress image', self.instance):
                    util_concurrency.execute(
                        f'gunzip -k -q -c {blob_path} > {cache_path}')
                blob_path = cache_path

            cache_path = os.path.join(
                config.STORAGE_PATH, 'image_cache', f'{b.uuid}.qcow2')
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
                    util_image.create_qcow2(blob_path, cache_path)

                # We will cache this transcode, but we do it later as part of a
                # task so the instance isn't waiting for it.
                node_uuid = blob._local_node_uuid()
                if node_uuid:
                    ic_create_and_enqueue(
                        node_uuid,
                        b.uuid,
                        cache_path,
                        TRANSCODE_DESCRIPTION,
                        [ic_tasks.archive_transcode],
                        PRIORITY.background_high_io,
                        request_id=util_general.get_request_id())
                else:
                    LOG.warning(
                        'Cannot enqueue transcode cache task, '
                        'node UUID is not available')

        shutil.chown(cache_path, config.LIBVIRT_USER, config.LIBVIRT_GROUP)
        add_event_multi(
            EVENT_TYPE_STATUS, self.objects, f'cache file {cache_path} created',
            extra=util_general.stat_log_fields(cache_path))

        if self.artifact.state.value == Artifact.STATE_INITIAL:
            self.artifact.state = Artifact.STATE_CREATED

    def _blob_get(self, url):
        """Fetch a blob from the cluster.

        Returns the blob and whether this call replicated it to this
        node, which is False when it was already here.
        """

        blob_uuid = url[len(BLOB_URL):]
        b = blob.Blob.from_db(blob_uuid)
        if not b:
            raise exceptions.BlobMissing(
                f'Failed to lookup blob {blob_uuid}')

        replicated = b.ensure_local(instance_object=self.instance)
        return b, replicated

    def _http_get_inner(self, resolved):
        """Fetch image if not downloaded and return image path."""
        url = resolved.url

        # An operator who has pointed us at mirrors which publish
        # digests can ask for the stronger property: no digest means no
        # fetch, rather than a fetch nobody checked. The guard sits
        # here because this is the only place a mirror fetch happens --
        # a copy served from within the cluster goes to _blob_get() and
        # is unaffected -- and above _open_connection(), because a
        # refusal after the download has started has already spent the
        # image.
        if config.REQUIRE_IMAGE_CHECKSUM and not resolved.verifiable:
            add_event_multi(
                EVENT_TYPE_AUDIT, self.objects,
                'refusing image fetch with no published checksum',
                extra={'url': url, 'algorithm': resolved.algorithm})
            raise exceptions.NoPublishedCheckSum(
                f'No published checksum could be read for {url}, and '
                'REQUIRE_IMAGE_CHECKSUM requires one')

        with util_general.RecordedOperation('fetch image', self.instance):
            resp = self._open_connection(url)
            blob_uuid = str(uuid.uuid4())
            b = blob.Blob.new(
                blob_uuid, resp.headers.get('Last-Modified'), time.time())

            objects_including_blob = copy.deepcopy(self.objects)
            objects_including_blob.append(b)
            add_event_multi(
                EVENT_TYPE_STATUS, objects_including_blob,
                'commencing HTTP fetch to blob',
                extra={'url': url, 'algorithm': resolved.algorithm})

            try:
                blob.http_fetch(
                    url, resp, b, objects_including_blob,
                    expected_algorithm=resolved.algorithm,
                    expected_checksum=resolved.checksum)
            except exceptions.BadCheckSum as e:
                add_event_multi(
                    EVENT_TYPE_AUDIT, objects_including_blob,
                    'fetched image had bad checksum',
                    extra={'url': url, 'algorithm': resolved.algorithm})
                raise e

            # The same predicate http_fetch() verifies on, so this
            # event can never claim a check which did not run.
            if resolved.verifiable:
                add_event_multi(
                    EVENT_TYPE_AUDIT, objects_including_blob,
                    'fetched image matched its published checksum',
                    extra={'url': url, 'algorithm': resolved.algorithm})
            else:
                # Say so. A fetch nobody could check is not the same as
                # a fetch which checked out, and an operator reading
                # these events back should not have to guess which
                # happened (issue 764).
                add_event_multi(
                    EVENT_TYPE_AUDIT, objects_including_blob,
                    'fetched image had no published checksum to verify '
                    'against', extra={'url': url})

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
