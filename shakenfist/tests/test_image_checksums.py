# Copyright 2026 Michael Still and contributors
"""An image checksum which is fetched and then thrown away.

`_resolve_cirros()` fetched the mirror's digest file, logged what it
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
        try:
            self._fetch('sha256', '0' * 64, unlinked=unlinked)
        except exceptions.BadCheckSum:
            pass
        self.assertEqual(['/tmp/x.partial'], unlinked)

    def test_a_mismatched_checksum_is_reported_as_an_event(self):
        events = []
        try:
            self._fetch('sha256', '0' * 64, events=events)
        except exceptions.BadCheckSum:
            pass
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
                               return_value=resolved):
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
