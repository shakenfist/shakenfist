# Copyright 2026 Michael Still and contributors
"""An image checksum which is fetched and then thrown away.

The cirros resolver fetched the mirror's digest file, logged what it
found, and then returned the URL either way -- the branch which chose
between them had the same expression on both sides. `http_fetch()`
meanwhile computed an md5 of the download and never used it. So a
mirror serving the wrong bytes was indistinguishable from one serving
the right ones, and the whole apparatus for noticing cost two HTTP
requests and a hash per fetch and decided nothing (issue 764).

Which digest file to ask for is not a free choice. cirros publishes
SHA256SUMS only from 0.6.3 onwards; 0.4.0, 0.5.2 and 0.6.1 publish
MD5SUMS and nothing else -- checked against the mirror, which is why
md5 survives as a fallback rather than being dropped outright.
"""

import hashlib
import os
from unittest import mock

import requests

from shakenfist import blob
from shakenfist import exceptions
from shakenfist import images
from shakenfist.config import SFConfig
from shakenfist.config import config
from shakenfist.tests import base


SHA256SUMS = (
    'aaaa  cirros-0.6.3-aarch64-disk.img\n'
    '%s  cirros-0.6.3-x86_64-disk.img\n'
) % ('1' * 64)
MD5SUMS = (
    'bbbb  cirros-0.6.3-aarch64-disk.img\n'
    '%s  cirros-0.6.3-x86_64-disk.img\n'
) % ('2' * 32)


def _fixture(name):
    with open(os.path.join(os.path.dirname(__file__), 'files', name)) as f:
        return f.read()


def _response(status_code, text=''):
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


class ResolveCirrosChecksumTestCase(base.ShakenFistTestCase):
    URL = 'http://download.cirros-cloud.net/0.6.3/cirros-0.6.3-x86_64-disk.img'

    def _resolve(self, responses):
        with mock.patch('shakenfist.images.requests.get',
                        side_effect=responses) as mock_get:
            result = images._resolve_cirros_checksum(self.URL, '0.6.3')
        return result, mock_get

    def test_sha256_is_preferred(self):
        (algorithm, checksum), mock_get = self._resolve(
            [_response(200, SHA256SUMS)])

        self.assertEqual('sha256', algorithm)
        self.assertEqual('1' * 64, checksum)
        # And having found it, it does not go on to ask for the weaker
        # one.
        self.assertEqual(1, mock_get.call_count)
        self.assertIn('SHA256SUMS', mock_get.call_args[0][0])

    def test_md5_is_the_fallback(self):
        # The releases before 0.6.3 publish nothing else.
        (algorithm, checksum), mock_get = self._resolve(
            [_response(404), _response(200, MD5SUMS)])

        self.assertEqual('md5', algorithm)
        self.assertEqual('2' * 32, checksum)
        self.assertEqual(2, mock_get.call_count)
        self.assertIn('MD5SUMS', mock_get.call_args[0][0])

    def test_a_mirror_publishing_nothing_yields_no_checksum(self):
        (algorithm, checksum), _ = self._resolve(
            [_response(404), _response(404)])

        self.assertIsNone(algorithm)
        self.assertIsNone(checksum)

    def test_a_digest_file_which_omits_our_image_falls_through(self):
        # SHA256SUMS exists and is readable, but does not list this
        # image. That is not an answer, so keep looking rather than
        # reporting "no checksum" with a weaker file sitting unread.
        (algorithm, checksum), mock_get = self._resolve(
            [_response(200, 'cccc  some-other-image.img\n'),
             _response(200, MD5SUMS)])

        self.assertEqual('md5', algorithm)
        self.assertEqual('2' * 32, checksum)
        self.assertEqual(2, mock_get.call_count)

    def test_a_digest_file_without_our_image_yields_no_checksum(self):
        (algorithm, checksum), _ = self._resolve(
            [_response(200, 'cccc  some-other-image.img\n'),
             _response(200, 'dddd  some-other-image.img\n')])

        self.assertIsNone(algorithm)
        self.assertIsNone(checksum)

    def test_an_unreachable_mirror_yields_no_checksum(self):
        (algorithm, checksum), _ = self._resolve(
            [requests.exceptions.ConnectionError('refused'),
             requests.exceptions.ConnectionError('refused')])

        self.assertIsNone(algorithm)
        self.assertIsNone(checksum)

    def test_an_md5_digest_is_never_labelled_sha256(self):
        # A mirror serving MD5SUMS under the name SHA256SUMS. Labelled
        # by its file name, the md5 digest was compared against a
        # sha256 of the download and every fetch failed as a mismatch.
        (algorithm, checksum), mock_get = self._resolve(
            [_response(200, MD5SUMS), _response(200, MD5SUMS)])

        self.assertEqual('md5', algorithm)
        self.assertEqual('2' * 32, checksum)
        self.assertEqual(2, mock_get.call_count)

    def _resolve_legacy(self, responses):
        # The template as shipped before it took %(digest_file)s, which
        # an operator's cluster_config may still carry.
        with mock.patch.object(
                images.config, 'CHECKSUM_URL_CIRROS',
                'http://mirror.example.com/%(vernum)s/MD5SUMS'):
            return self._resolve(responses)

    def test_a_legacy_template_is_still_checked(self):
        (algorithm, checksum), mock_get = self._resolve_legacy(
            [_response(200, MD5SUMS)])

        self.assertEqual('md5', algorithm)
        self.assertEqual('2' * 32, checksum)
        self.assertEqual(1, mock_get.call_count)
        self.assertEqual(
            'http://mirror.example.com/0.6.3/MD5SUMS',
            mock_get.call_args[0][0])

    def test_a_legacy_template_is_labelled_by_its_digest(self):
        # Whatever the one file it names turns out to hold.
        (algorithm, checksum), _ = self._resolve_legacy(
            [_response(200, SHA256SUMS)])

        self.assertEqual('sha256', algorithm)
        self.assertEqual('1' * 64, checksum)

    def test_a_digest_we_do_not_recognise_is_not_used(self):
        (algorithm, checksum), _ = self._resolve_legacy(
            [_response(200, 'abc  cirros-0.6.3-x86_64-disk.img\n')])

        self.assertIsNone(algorithm)
        self.assertIsNone(checksum)


class PublishedDigestFilesTestCase(base.ShakenFistTestCase):
    """The parser against the files cirros actually publishes.

    Every other test here uses a synthetic two-field fixture, which
    cannot show the parse missing the real format -- and a miss is not
    an error, it is a fetch which goes ahead unverified. These are the
    files verbatim, as served by download.cirros-cloud.net.
    """

    def _resolve(self, vernum, responses):
        url = (f'http://download.cirros-cloud.net/{vernum}/'
               f'cirros-{vernum}-x86_64-disk.img')
        with mock.patch('shakenfist.images.requests.get',
                        side_effect=responses):
            return images._resolve_cirros_checksum(url, vernum)

    def test_the_real_sha256sums_names_our_image(self):
        algorithm, checksum = self._resolve(
            '0.6.3', [_response(200, _fixture('cirros-SHA256SUMS-0.6.3'))])
        self.assertEqual('sha256', algorithm)
        self.assertEqual(
            '7d6355852aeb6dbcd191bcda7cd74f15'
            '36cfe5cbf8a10495a7283a8396e4b75b', checksum)

    def test_the_real_md5sums_names_our_image(self):
        # 0.4.0 publishes no SHA256SUMS.
        algorithm, checksum = self._resolve(
            '0.4.0',
            [_response(404), _response(200, _fixture('cirros-MD5SUMS-0.4.0'))])
        self.assertEqual('md5', algorithm)
        self.assertEqual('443b7623e27ecf03dc9e01ee93f67afe', checksum)

    def test_a_binary_mode_marker_is_not_part_of_the_name(self):
        text = _fixture('cirros-SHA256SUMS-0.6.3').replace('  ', ' *')
        algorithm, checksum = self._resolve('0.6.3', [_response(200, text)])
        self.assertEqual('sha256', algorithm)
        self.assertEqual(
            '7d6355852aeb6dbcd191bcda7cd74f15'
            '36cfe5cbf8a10495a7283a8396e4b75b', checksum)

    def test_finding_nothing_for_cirros_is_said(self):
        with mock.patch.object(images, 'LOG') as mock_log:
            self._resolve('0.6.3', [_response(404), _response(404)])
        warnings = [c.args[0] for c in
                    mock_log.with_fields.return_value.warning.call_args_list]
        self.assertIn('No published checksum found for a cirros image, it '
                      'will be fetched unverified', warnings)


class AlgorithmForDigestTestCase(base.ShakenFistTestCase):
    def test_lengths(self):
        self.assertEqual('md5', images._algorithm_for_digest('a' * 32))
        self.assertEqual('sha256', images._algorithm_for_digest('a' * 64))
        self.assertEqual('sha512', images._algorithm_for_digest('a' * 128))

    def test_not_a_digest(self):
        for value in [None, '', 'a' * 40, 'z' * 64]:
            self.assertIsNone(images._algorithm_for_digest(value))

    def test_what_int_accepts_is_not_a_digest(self):
        # int(x, 16) takes a 0x prefix, underscores and surrounding
        # whitespace. None of those is a digest, and labelling one
        # sha256 would fail the fetch as a mismatch.
        for value in ['0x' + 'a' * 62, 'a' * 31 + '_' + 'a' * 32,
                      ' ' + 'a' * 63, 'A' * 63 + '\n']:
            self.assertIsNone(images._algorithm_for_digest(value), value)

    def test_upper_case_is_a_digest(self):
        self.assertEqual('sha256', images._algorithm_for_digest('A' * 64))


class FetchRemoteChecksumTestCase(base.ShakenFistTestCase):
    def test_a_missing_file_is_none_not_empty(self):
        # None means "could not read it"; {} means "it exists and names
        # no files". Collapsing them would let a mirror which is
        # briefly unwell look like one which publishes nothing.
        with mock.patch('shakenfist.images.requests.get',
                        return_value=_response(404)):
            self.assertIsNone(
                images._fetch_remote_checksum('http://example.com/SHA256SUMS'))

    def test_an_empty_file_is_an_empty_dict(self):
        with mock.patch('shakenfist.images.requests.get',
                        return_value=_response(200, '')):
            self.assertEqual(
                {},
                images._fetch_remote_checksum('http://example.com/SHA256SUMS'))

    def test_the_fetch_is_bounded(self):
        # A mirror which accepts the connection and then says nothing
        # must not park an image fetch forever.
        with mock.patch('shakenfist.images.requests.get',
                        return_value=_response(200, '')) as mock_get:
            images._fetch_remote_checksum('http://example.com/SHA256SUMS')
        self.assertEqual(
            images.CHECKSUM_FETCH_TIMEOUT, mock_get.call_args.kwargs['timeout'])

    def test_a_timeout_is_none(self):
        with mock.patch(
                'shakenfist.images.requests.get',
                side_effect=requests.exceptions.ReadTimeout('slow')):
            self.assertIsNone(
                images._fetch_remote_checksum('http://example.com/SHA256SUMS'))

    def test_a_transport_error_is_none(self):
        with mock.patch(
                'shakenfist.images.requests.get',
                side_effect=requests.exceptions.ConnectionError('refused')):
            self.assertIsNone(
                images._fetch_remote_checksum('http://example.com/SHA256SUMS'))


class ResolveImageTestCase(base.ShakenFistTestCase):
    def test_a_plain_url_has_no_checksum(self):
        # Not a failure: we have nothing to check it against, which is
        # a different thing from having checked it.
        resolved = images._resolve_image('https://example.com/image.qcow2')
        self.assertEqual('https://example.com/image.qcow2', resolved.url)
        self.assertIsNone(resolved.algorithm)
        self.assertIsNone(resolved.checksum)

    def test_cirros_carries_its_checksum(self):
        with mock.patch('shakenfist.images.requests.get',
                        return_value=_response(200, SHA256SUMS)):
            with mock.patch.object(
                    images.config, 'DOWNLOAD_URL_CIRROS',
                    'http://download.cirros-cloud.net/%(vernum)s/'
                    'cirros-%(vernum)s-x86_64-disk.img'):
                resolved = images._resolve_image('cirros:0.6.3')

        self.assertEqual('sha256', resolved.algorithm)
        self.assertEqual('1' * 64, resolved.checksum)


class HttpFetchChecksumTestCase(base.ShakenFistTestCase):
    """The comparison itself, which is what the issue is about."""

    CONTENT = b'an image, for the sake of argument'

    def _fetch(self, expected_algorithm=None, expected_checksum=None,
               unlinked=None, events=None):
        resp = mock.MagicMock()
        resp.headers = {}
        resp.iter_content.return_value = iter([self.CONTENT])

        b = mock.MagicMock()
        b.uuid = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'

        written = {}

        class _File:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def write(self, data):
                written.setdefault('data', b'')
                written['data'] += data

        with mock.patch.object(blob.Blob, 'filepath', return_value='/tmp/x'):
            with mock.patch('shakenfist.blob.open',
                            mock.mock_open()) as mock_open:
                mock_open.return_value.__enter__.return_value = _File()
                with mock.patch(
                        'shakenfist.blob.add_event_multi') as mock_events:
                    if events is not None:
                        mock_events.side_effect = (
                            lambda *a, **k: events.append((a, k)))
                    with mock.patch('shakenfist.blob.os.unlink') as unlink:
                        if unlinked is not None:
                            unlink.side_effect = unlinked.append
                        result = blob.http_fetch(
                            'http://example.com/image', resp, b, [],
                            expected_algorithm=expected_algorithm,
                            expected_checksum=expected_checksum)
        return result, mock_events, unlink

    def test_a_matching_checksum_registers_the_blob(self):
        digest = hashlib.sha256(self.CONTENT).hexdigest()
        result, _, unlink = self._fetch('sha256', digest)
        self.assertIsNotNone(result)
        unlink.assert_not_called()

    def test_a_mismatched_checksum_is_refused(self):
        self.assertRaises(
            exceptions.BadCheckSum, self._fetch, 'sha256', '0' * 64)

    def test_a_mismatched_checksum_removes_the_partial_file(self):
        # A corrupt download must not be left on disk where the next
        # thing along could adopt it.
        unlinked = []
        self.assertRaises(
            exceptions.BadCheckSum, self._fetch, 'sha256', '0' * 64,
            unlinked=unlinked)
        self.assertEqual(['/tmp/x.partial'], unlinked)

    def test_a_mismatched_checksum_is_reported_as_an_event(self):
        events = []
        self.assertRaises(
            exceptions.BadCheckSum, self._fetch, 'sha256', '0' * 64,
            events=events)
        self.assertTrue(
            any('does not match its published checksum' in str(call)
                for call in events),
            'no mismatch event was recorded')

    def test_case_does_not_matter(self):
        # Mirrors are not consistent about this, and a case mismatch is
        # not a corrupt image.
        digest = hashlib.sha256(self.CONTENT).hexdigest().upper()
        result, _, _ = self._fetch('sha256', digest)
        self.assertIsNotNone(result)

    def test_md5_is_still_checkable(self):
        # It is not in BLOB_HASH_ALGORITHMS, but the older cirros
        # releases publish nothing else.
        digest = hashlib.md5(self.CONTENT).hexdigest()
        result, _, _ = self._fetch('md5', digest)
        self.assertIsNotNone(result)

    def test_the_digest_is_not_claimed_as_a_security_primitive(self):
        # A FIPS-restricted OpenSSL refuses md5 unless told it is not
        # being used for security, and the fetch failed outright.
        digest = hashlib.md5(self.CONTENT).hexdigest()
        real_new = hashlib.new
        with mock.patch('shakenfist.blob.hashlib.new',
                        side_effect=real_new) as mock_new:
            self._fetch('md5', digest)
        mock_new.assert_called_once_with('md5', usedforsecurity=False)

    def test_a_failed_tidy_up_does_not_hide_the_mismatch(self):
        # The caller handles BadCheckSum, and records the event the
        # whole check exists to produce. An OSError from removing the
        # partial file would skip both.
        def _cannot_unlink(path):
            raise PermissionError('read only filesystem')

        unlinked = mock.MagicMock(side_effect=_cannot_unlink)
        self.assertRaises(
            exceptions.BadCheckSum, self._fetch, 'sha256', '0' * 64,
            unlinked=mock.MagicMock(append=unlinked))
        unlinked.assert_called_once_with('/tmp/x.partial')

    def test_no_checksum_means_no_check(self):
        result, _, unlink = self._fetch(None, None)
        self.assertIsNotNone(result)
        unlink.assert_not_called()

    def test_a_checksum_without_an_algorithm_is_not_checked(self):
        # Both or neither. A digest whose algorithm is unknown cannot
        # be compared against anything.
        result, _, unlink = self._fetch(None, '0' * 64)
        self.assertIsNotNone(result)
        unlink.assert_not_called()


class CachedBlobPathTestCase(base.ShakenFistTestCase):
    """A copy the cluster already holds is not a mirror fetch.

    The branch and the fetch both read `resolved`, so the URL being
    fetched and the digest it may be checked against cannot drift
    apart. Reading one from `url` and the other from `resolved` let
    the cluster path carry the mirror's digest, which claimed a check
    that never happened -- and a mutation check found that the line
    guarding against it was never executed.
    """

    def test_the_cluster_path_does_not_carry_the_mirrors_digest(self):
        helper = images.ImageFetchHelper.__new__(images.ImageFetchHelper)
        helper.objects = []
        helper.instance = None
        helper.artifact = mock.MagicMock()
        helper.artifact.source_url = 'cirros:0.6.3'
        helper.artifact.most_recent_index = {
            'index': 1, 'blob_uuid': 'bbbbbbbb-cccc-4ddd-8eee-ffffffffffff'}

        resolved = images.ResolvedImage(
            'http://example.com/image.qcow2', 'sha256', '1' * 64)

        with mock.patch.object(images, '_resolve_image',
                               return_value=resolved) as mock_resolve:
            with mock.patch.object(images.blob.Blob, 'from_db',
                                   return_value=mock.MagicMock()):
                with mock.patch.object(
                        helper, '_open_connection',
                        side_effect=exceptions.HTTPError('404')):
                    with mock.patch.object(
                            helper, '_blob_get') as mock_blob_get:
                        with mock.patch.object(
                                helper, '_http_get_inner') as mock_http_get:
                            with mock.patch(
                                    'shakenfist.images.add_event_multi'):
                                helper.transfer_image()

        # Served from the cluster, so the mirror fetch never runs and
        # the mirror's digest never reaches it.
        mock_http_get.assert_not_called()
        mock_blob_get.assert_called_once()
        self.assertTrue(
            mock_blob_get.call_args[0][0].startswith(images.BLOB_URL))
        # And the digest is not merely dropped on this path, it is
        # never resolved: the resolver is what costs the mirror round
        # trips (issue 4976).
        mock_resolve.assert_not_called()


class ResolutionOrderingTestCase(base.ShakenFistTestCase):
    """What the mirror is asked for, before the cache has been consulted.

    transfer_image() resolved the source URL -- version listing for a
    bare `cirros`, then up to two digest files -- before it decided
    whether the cluster's own copy would do, so the `cached_only`
    fallback which exists *because* the mirror is unreachable paid for
    those round trips and threw the answers away (issue 4976).
    """

    LISTING = ('''<html><body>
    <a href="0.4.0/">0.4.0/</a>
    <a href="0.6.3/">0.6.3/</a>
    </body></html>''')

    def setUp(self):
        super().setUp()
        self.requested = []

    def _fake_get(self, url, *args, **kwargs):
        url = str(url)
        self.requested.append(url)
        if url.endswith('SHA256SUMS'):
            return _response(200, SHA256SUMS)
        if url.endswith('MD5SUMS'):
            return _response(200, MD5SUMS)
        return _response(200, self.LISTING)

    def _helper(self, source_url, index=1):
        helper = images.ImageFetchHelper.__new__(images.ImageFetchHelper)
        helper.objects = []
        helper.instance = None
        helper.artifact = mock.MagicMock()
        helper.artifact.source_url = source_url
        helper.artifact.most_recent_index = {
            'index': index, 'blob_uuid': 'bbbbbbbb-cccc-4ddd-8eee-ffffffffffff'}
        if index == 0:
            helper.artifact.most_recent_index = {'index': 0}
        return helper

    def test_cached_only_asks_the_mirror_for_nothing(self):
        """The whole point of the degraded path.

        A bare `cirros` is the worst case: a version listing plus a
        digest file, three mirror round trips before the cached copy
        the caller asked for is even looked at.
        """
        helper = self._helper('cirros')
        cached = mock.MagicMock()

        with mock.patch('shakenfist.images.requests.get',
                        side_effect=self._fake_get):
            with mock.patch.object(images.blob.Blob, 'from_db',
                                   return_value=cached):
                with mock.patch.object(
                        helper, '_open_connection',
                        side_effect=AssertionError(
                            'the source must not be consulted')):
                    with mock.patch.object(
                            helper, '_blob_get') as mock_blob_get:
                        with mock.patch(
                                'shakenfist.images.add_event_multi'):
                            helper.transfer_image(cached_only=True)

        self.assertEqual([], self.requested)
        mock_blob_get.assert_called_once()

    def test_cached_only_survives_an_unreachable_mirror(self):
        """The case the fallback exists for, which it could not serve.

        A bare `cirros` resolved its version from the mirror's listing,
        with no timeout and no error handling, before the cache was
        consulted. So when the mirror was down -- the one condition
        under which artifact_fetch_op falls back to the cached copy at
        all -- the fallback raised the connection error on its way in,
        and the artifact errored with "the cached version was not
        usable" while a perfectly good cached version sat there.
        """
        helper = self._helper('cirros')
        cached = mock.MagicMock()

        with mock.patch('shakenfist.images.requests.get',
                        side_effect=requests.exceptions.ConnectionError(
                            'Name or service not known')):
            with mock.patch.object(images.blob.Blob, 'from_db',
                                   return_value=cached):
                with mock.patch.object(
                        helper, '_blob_get') as mock_blob_get:
                    with mock.patch('shakenfist.images.add_event_multi'):
                        helper.transfer_image(cached_only=True)

        mock_blob_get.assert_called_once()
        self.assertTrue(
            mock_blob_get.call_args[0][0].startswith(images.BLOB_URL))

    def test_cached_only_with_nothing_cached_asks_for_nothing_either(self):
        # The raise is what the caller handles; it must not cost three
        # fetches to get to it.
        helper = self._helper('cirros', index=0)
        with mock.patch('shakenfist.images.requests.get',
                        side_effect=self._fake_get):
            with mock.patch('shakenfist.images.add_event_multi'):
                self.assertRaises(
                    exceptions.BlobMissing, helper.transfer_image,
                    cached_only=True)
        self.assertEqual([], self.requested)

    def test_a_fresh_cached_copy_does_not_resolve_the_digest(self):
        """The staleness check needs the URL, not the digest.

        A pinned `cirros` expands for free, so what is being asserted
        here is that the digest files -- which the cluster copy can
        never be checked against -- are not fetched when the cached
        copy turns out to be current.
        """
        helper = self._helper('cirros:0.6.3')
        cached = mock.MagicMock()
        cached.modified = 'the-timestamp'
        cached.size = 1234
        resp = mock.MagicMock()
        resp.headers = {'Last-Modified': 'the-timestamp',
                        'Content-Length': '1234'}

        with mock.patch('shakenfist.images.requests.get',
                        side_effect=self._fake_get):
            with mock.patch.object(images.blob.Blob, 'from_db',
                                   return_value=cached):
                with mock.patch.object(
                        images.blob.Blob, 'normalize_timestamp',
                        side_effect=lambda v: v):
                    with mock.patch.object(
                            helper, '_open_connection',
                            return_value=resp) as mock_open:
                        with mock.patch.object(
                                helper, '_blob_get') as mock_blob_get:
                            with mock.patch(
                                    'shakenfist.images.add_event_multi'):
                                helper.transfer_image()

        self.assertEqual([], self.requested)
        mock_open.assert_called_once_with(
            'https://download.cirros-cloud.net/0.6.3/'
            'cirros-0.6.3-x86_64-disk.img')
        mock_blob_get.assert_called_once()

    def test_a_mirror_fetch_still_resolves_the_digest(self):
        """The fix must not turn verification off.

        Nothing is cached, so this is a mirror fetch and the published
        digest is exactly what it is for.
        """
        helper = self._helper('cirros:0.6.3', index=0)

        with mock.patch('shakenfist.images.requests.get',
                        side_effect=self._fake_get):
            with mock.patch.object(
                    helper, '_http_get_inner') as mock_http_get:
                with mock.patch('shakenfist.images.add_event_multi'):
                    helper.transfer_image()

        self.assertEqual(
            ['https://download.cirros-cloud.net/0.6.3/SHA256SUMS'],
            self.requested)
        mock_http_get.assert_called_once_with(images.ResolvedImage(
            'https://download.cirros-cloud.net/0.6.3/'
            'cirros-0.6.3-x86_64-disk.img', 'sha256', '1' * 64))

    def test_a_stale_cached_copy_reads_the_listing_once(self):
        """Both halves need the expansion, and it is paid for once.

        The staleness check expands the URL to ask the mirror about it,
        finds the cached copy stale, and the fetch which follows reuses
        that expansion rather than reading the version listing a second
        time.
        """
        helper = self._helper('cirros')
        cached = mock.MagicMock()
        cached.modified = 'old'
        cached.size = 1234
        resp = mock.MagicMock()
        resp.headers = {'Last-Modified': 'new', 'Content-Length': '2345'}

        with mock.patch('shakenfist.images.requests.get',
                        side_effect=self._fake_get):
            with mock.patch.object(images.blob.Blob, 'from_db',
                                   return_value=cached):
                with mock.patch.object(
                        images.blob.Blob, 'normalize_timestamp',
                        side_effect=lambda v: v):
                    with mock.patch.object(
                            helper, '_open_connection', return_value=resp):
                        with mock.patch.object(
                                helper, '_http_get_inner') as mock_http_get:
                            with mock.patch(
                                    'shakenfist.images.add_event_multi'):
                                helper.transfer_image()

        self.assertEqual(
            ['https://download.cirros-cloud.net/',
             'https://download.cirros-cloud.net/0.6.3/SHA256SUMS'],
            self.requested)
        mock_http_get.assert_called_once_with(images.ResolvedImage(
            'https://download.cirros-cloud.net/0.6.3/'
            'cirros-0.6.3-x86_64-disk.img', 'sha256', '1' * 64))

    def test_a_blob_source_url_resolves_nothing(self):
        helper = self._helper(images.BLOB_URL + 'a-blob-uuid')
        with mock.patch('shakenfist.images.requests.get',
                        side_effect=self._fake_get):
            with mock.patch.object(helper, '_blob_get') as mock_blob_get:
                with mock.patch('shakenfist.images.add_event_multi'):
                    helper.transfer_image()

        self.assertEqual([], self.requested)
        mock_blob_get.assert_called_once_with(
            images.BLOB_URL + 'a-blob-uuid')


class ExpandImageUrlTestCase(base.ShakenFistTestCase):
    """Expansion is the cheap half, and stays that way."""

    def test_a_plain_url_expands_to_itself(self):
        expanded = images._expand_image_url('https://example.com/i.qcow2')
        self.assertEqual(
            images.ExpandedImage('https://example.com/i.qcow2'), expanded)

    def test_a_shakenfist_shortcut_fetches_nothing(self):
        with mock.patch('shakenfist.images.requests.get',
                        side_effect=AssertionError('no fetch')):
            expanded = images._expand_image_url('debian:12')
        self.assertEqual(
            'https://images.shakenfist.com/debian:12/latest.qcow2',
            expanded.url)
        # No vernum, so _resolve_image() has no digest to go looking
        # for either.
        self.assertIsNone(expanded.vernum)

    def test_a_pinned_cirros_fetches_nothing(self):
        with mock.patch('shakenfist.images.requests.get',
                        side_effect=AssertionError('no fetch')):
            expanded = images._expand_image_url('cirros:0.6.3')
        self.assertEqual(
            'https://download.cirros-cloud.net/0.6.3/'
            'cirros-0.6.3-x86_64-disk.img', expanded.url)
        self.assertEqual('0.6.3', expanded.vernum)

    def test_a_bare_cirros_reads_only_the_listing(self):
        requested = []

        def _get(url, *args, **kwargs):
            requested.append(str(url))
            return _response(
                200, '<a href="0.6.3/">0.6.3/</a>')

        with mock.patch('shakenfist.images.requests.get', side_effect=_get):
            expanded = images._expand_image_url('cirros')

        self.assertEqual(
            ['https://download.cirros-cloud.net/'], requested)
        self.assertEqual('0.6.3', expanded.vernum)


class FetchedImageEventTestCase(base.ShakenFistTestCase):
    """What the audit event says was checked, against what was."""

    def _events_for(self, resolved):
        helper = images.ImageFetchHelper.__new__(images.ImageFetchHelper)
        helper.objects = []
        helper.instance = None
        with mock.patch.object(helper, '_open_connection'):
            with mock.patch.object(images.blob.Blob, 'new'):
                with mock.patch.object(images.blob, 'http_fetch'):
                    with mock.patch(
                            'shakenfist.images.add_event_multi') as events:
                        with mock.patch(
                                'shakenfist.images.util_general.'
                                'RecordedOperation'):
                            helper._http_get_inner(resolved)
        return [c[0][2] for c in events.call_args_list]

    def test_a_verified_fetch_says_it_matched(self):
        messages = self._events_for(images.ResolvedImage(
            'http://example.com/i.qcow2', 'sha256', '1' * 64))
        self.assertIn(
            'fetched image matched its published checksum', messages)

    def test_a_checksum_without_an_algorithm_is_not_claimed(self):
        # http_fetch() only checks when it has both, so the event must
        # not say the image matched when only one was known.
        messages = self._events_for(images.ResolvedImage(
            'http://example.com/i.qcow2', None, '1' * 64))
        self.assertNotIn(
            'fetched image matched its published checksum', messages)
        self.assertIn(
            'fetched image had no published checksum to verify against',
            messages)


class VerifiableTestCase(base.ShakenFistTestCase):
    """The one predicate which says whether a check can happen.

    http_fetch() only verifies when it has both halves, so the audit
    event, the REQUIRE_IMAGE_CHECKSUM refusal and the fetch itself all
    read this rather than restating it.
    """

    def test_both_halves_are_verifiable(self):
        self.assertTrue(images.ResolvedImage(
            'http://example.com/i.qcow2', 'sha256', '1' * 64).verifiable)

    def test_neither_half_is_not_verifiable(self):
        self.assertFalse(images.ResolvedImage(
            'http://example.com/i.qcow2').verifiable)

    def test_a_checksum_without_an_algorithm_is_not_verifiable(self):
        self.assertFalse(images.ResolvedImage(
            'http://example.com/i.qcow2', None, '1' * 64).verifiable)

    def test_an_algorithm_without_a_checksum_is_not_verifiable(self):
        self.assertFalse(images.ResolvedImage(
            'http://example.com/i.qcow2', 'sha256', None).verifiable)


class RequireImageChecksumTestCase(base.ShakenFistTestCase):
    """Fail closed when the operator asks for it (issue 4999).

    The default is to fetch unverified when no digest could be read,
    which is right for a fleet whose mirrors publish nothing but means
    anyone able to stop the digest request arriving turns the check
    off. REQUIRE_IMAGE_CHECKSUM makes that a refusal instead.
    """

    def _fetch(self, resolved, require):
        helper = images.ImageFetchHelper.__new__(images.ImageFetchHelper)
        helper.objects = []
        helper.instance = None
        with mock.patch.object(config, 'REQUIRE_IMAGE_CHECKSUM', require):
            with mock.patch.object(helper, '_open_connection') as connection:
                with mock.patch.object(images.blob.Blob, 'new'):
                    with mock.patch.object(images.blob, 'http_fetch') as fetch:
                        with mock.patch(
                                'shakenfist.images.add_event_multi') as events:
                            with mock.patch(
                                    'shakenfist.images.util_general.'
                                    'RecordedOperation'):
                                try:
                                    helper._http_get_inner(resolved)
                                    raised = None
                                except Exception as e:
                                    raised = e
        messages = [c[0][2] for c in events.call_args_list]
        return raised, connection, fetch, messages

    def test_no_digest_is_refused_when_required(self):
        raised, _, _, _ = self._fetch(
            images.ResolvedImage('http://example.com/i.qcow2'), True)
        self.assertIsInstance(raised, exceptions.NoPublishedCheckSum)

    def test_the_refusal_happens_before_the_download(self):
        # A refusal after the download has started has already spent
        # the image, so the guard sits above _open_connection().
        _, connection, fetch, _ = self._fetch(
            images.ResolvedImage('http://example.com/i.qcow2'), True)
        connection.assert_not_called()
        fetch.assert_not_called()

    def test_the_refusal_is_an_audit_event(self):
        _, _, _, messages = self._fetch(
            images.ResolvedImage('http://example.com/i.qcow2'), True)
        self.assertIn(
            'refusing image fetch with no published checksum', messages)
        # And does not also claim an unverified fetch happened.
        self.assertNotIn(
            'fetched image had no published checksum to verify against',
            messages)

    def test_a_partial_digest_is_refused_too(self):
        # A checksum with no algorithm is not checkable, so it must not
        # satisfy a requirement for a published digest.
        raised, connection, _, _ = self._fetch(
            images.ResolvedImage(
                'http://example.com/i.qcow2', None, '1' * 64), True)
        self.assertIsInstance(raised, exceptions.NoPublishedCheckSum)
        connection.assert_not_called()

    def test_a_published_digest_still_fetches_when_required(self):
        raised, connection, fetch, messages = self._fetch(
            images.ResolvedImage(
                'http://example.com/i.qcow2', 'sha256', '1' * 64), True)
        self.assertIsNone(raised)
        connection.assert_called_once()
        fetch.assert_called_once()
        self.assertIn(
            'fetched image matched its published checksum', messages)

    def test_the_default_still_fails_open(self):
        # The floor this must not quietly move: an existing fleet points
        # much of itself at mirrors which publish nothing.
        raised, connection, fetch, messages = self._fetch(
            images.ResolvedImage('http://example.com/i.qcow2'), False)
        self.assertIsNone(raised)
        connection.assert_called_once()
        fetch.assert_called_once()
        self.assertIn(
            'fetched image had no published checksum to verify against',
            messages)

    def test_the_shipped_default_is_off(self):
        # The declared default rather than SFConfig(), so a developer
        # running the suite with the option exported is not testing
        # their shell.
        self.assertFalse(
            SFConfig.model_fields['REQUIRE_IMAGE_CHECKSUM'].default)

    def test_a_copy_from_the_cluster_is_not_a_mirror_fetch(self):
        # _http_get_inner() is deliberately not mocked here, so the real
        # guard is on the path and would refuse if the fetch branch were
        # taken. Serving a blob the cluster already holds must not be
        # affected by a setting about mirrors.
        helper = images.ImageFetchHelper.__new__(images.ImageFetchHelper)
        helper.objects = []
        helper.instance = None
        helper.artifact = mock.MagicMock()
        helper.artifact.source_url = 'http://example.com/i.qcow2'
        helper.artifact.most_recent_index = {
            'index': 1, 'blob_uuid': 'bbbbbbbb-cccc-4ddd-8eee-ffffffffffff'}

        with mock.patch.object(config, 'REQUIRE_IMAGE_CHECKSUM', True):
            with mock.patch.object(images.blob.Blob, 'from_db',
                                   return_value=mock.MagicMock()):
                with mock.patch.object(
                        helper, '_open_connection',
                        side_effect=exceptions.HTTPError('404')):
                    with mock.patch.object(
                            helper, '_blob_get') as mock_blob_get:
                        with mock.patch(
                                'shakenfist.images.add_event_multi'):
                            helper.transfer_image()

        mock_blob_get.assert_called_once()
        self.assertTrue(
            mock_blob_get.call_args[0][0].startswith(images.BLOB_URL))
