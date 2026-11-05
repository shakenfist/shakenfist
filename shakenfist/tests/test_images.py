from unittest import mock

from shakenfist import exceptions
from shakenfist import images
from shakenfist.tests import base


class ImageResolverTestCase(base.ShakenFistTestCase):
    def test_resolve_ubuntu_1604(self):
        self.assertEqual(
            'https://images.shakenfist.com/ubuntu:16.04/latest.qcow2',
            images._resolve_image('ubuntu:16.04'))

    def test_resolve_ubuntu_16804(self):
        self.assertEqual(
            'https://images.shakenfist.com/ubuntu:18.04/latest.qcow2',
            images._resolve_image('ubuntu:18.04'))

    def test_resolve_ubuntu_2004(self):
        self.assertEqual(
            'https://images.shakenfist.com/ubuntu:20.04/latest.qcow2',
            images._resolve_image('ubuntu:20.04'))

    def test_resolve_ubuntu_2204(self):
        self.assertEqual(
            'https://images.shakenfist.com/ubuntu:22.04/latest.qcow2',
            images._resolve_image('ubuntu:22.04'))

    def test_resolve_a_url_is_left_alone(self):
        self.assertEqual(
            'https://example.com/an-image.qcow2',
            images._resolve_image('https://example.com/an-image.qcow2'))

    def test_resolve_an_unknown_bare_name_is_left_alone(self):
        """What makes the fetch fail, rather than the shortcut expand.

        _resolve_image() hands a string it does not recognise straight
        back, and the caller then hands it to requests. This is the
        pass-through is_fetchable() exists to refuse before an API
        caller can reach it (issue 4557).
        """
        self.assertEqual('wombat', images._resolve_image('wombat'))


class ImageShortcutTestCase(base.ShakenFistTestCase):
    """Which strings _resolve_image() expands, asked directly.

    The shortcut test used to be inline in _resolve_image() and is now
    a predicate, because whether a scheme-less base is fetchable is the
    same question (issue 4557) and the two must not drift apart.
    """

    def test_the_images_site_prefixes_are_shortcuts(self):
        for url in ['centos', 'centos:7', 'debian', 'debian:12',
                    'debian-docker:12', 'fedora:40', 'rocky', 'ubuntu:24.04',
                    'cirros', 'cirros:0.4.0']:
            self.assertTrue(images.is_image_shortcut(url), url)

    def test_a_subdirectory_is_not_a_shortcut(self):
        """The rule _resolve_image() has always used.

        A path makes it a URL, which is what stops the prefix test
        claiming https://debian.example.com/x.qcow2 -- that string does
        not start with a prefix anyway, but
        'debian/images/x.qcow2' does.
        """
        for url in ['debian/images/x.qcow2',
                    'https://images.shakenfist.com/debian:12/latest.qcow2',
                    'sf://upload/system/debian']:
            self.assertFalse(images.is_image_shortcut(url), url)

    def test_an_unknown_bare_name_is_not_a_shortcut(self):
        for url in ['wombat', 'alpine:3.19', '']:
            self.assertFalse(images.is_image_shortcut(url), url)


class FetchableTestCase(base.ShakenFistTestCase):
    """What the API can tell a caller before anything is queued.

    is_fetchable() is the whole of the issue 4557 fix: a base which
    requests cannot fetch used to be accepted, retried on a hypervisor
    and then errored the instance with no error message at all.
    """

    def test_http_and_https_are_fetchable(self):
        for url in ['http://example.com/an-image.qcow2',
                    'https://example.com/an-image.qcow2',
                    'HTTPS://example.com/an-image.qcow2',
                    'http://10.0.0.10/debian-12']:
            self.assertTrue(images.is_fetchable(url), url)

    def test_a_shortcut_is_fetchable(self):
        """The one scheme-less form which is, and why the guard is not
        simply a scheme test."""
        for url in ['debian:12', 'cirros', 'ubuntu:24.04']:
            self.assertTrue(images.is_fetchable(url), url)

    def test_a_scheme_requests_cannot_use_is_not_fetchable(self):
        """Every scheme but http and https, sf:// included.

        sf://blob/<uuid> is in this list deliberately, even though
        transfer_image() *can* serve an artifact whose source url is
        one, from within the cluster rather than by fetching it. The
        predicate answers the requests question only -- a disk base
        naming a blob is dispatched to that blob several branches
        before the one this guards, so the instance create caller never
        reaches here with one.
        """
        for url in ['ova:///srv/x.ova/disk.vmdk',
                    'sf://instance/88317f29-6d6b-4b4e-9a7f-2c1b5f0a3d41/vda',
                    'sf://blob/88317f29-6d6b-4b4e-9a7f-2c1b5f0a3d41',
                    'ftp://example.com/an-image.qcow2',
                    'file:///srv/an-image.qcow2']:
            self.assertFalse(images.is_fetchable(url), url)

    def test_a_bare_uuid_is_not_fetchable(self):
        self.assertFalse(
            images.is_fetchable('88317f29-6d6b-4b4e-9a7f-2c1b5f0a3d41'))

    def test_an_unparseable_url_is_not_fetchable(self):
        """urlparse raises rather than answering for this one.

        A disk base is not format validated, so this string reaches the
        predicate from a request body. An exception here would be a
        recorded 500 for a caller's typo.
        """
        self.assertFalse(images.is_fetchable('http://[::1'))

    def test_a_non_string_is_not_fetchable(self):
        """Not reachable through a disk base, and answered anyway.

        The diskspec schema refuses a non-string base at `enforce`, but
        API_VALIDATION_MODE=warn and off are the operator's rollback,
        and 'the guard faulted instead of refusing' is the failure mode
        this whole class of guard exists to avoid.
        """
        for value in [None, 5, ['https://example.com/x.qcow2'], {}]:
            self.assertFalse(images.is_fetchable(value), repr(value))


@mock.patch('shakenfist.images.add_event_multi')
class TransferImageTestCase(base.ShakenFistTestCase):
    """The source-unreachable paths of ImageFetchHelper.transfer_image()
    (issue 3603)."""

    URL = 'https://example.com/an-image.qcow2'

    def _helper(self, index=1):
        artifact = mock.MagicMock()
        artifact.source_url = self.URL
        artifact.most_recent_index = {'index': index, 'blob_uuid': 'blob-1'}
        if index == 0:
            artifact.most_recent_index = {'index': 0}
        return images.ImageFetchHelper(None, artifact)

    def test_cached_only_skips_the_source(self, _mock_events):
        cached_blob = mock.MagicMock(uuid='blob-1')
        fetched_blob = mock.MagicMock()
        helper = self._helper()

        with mock.patch('shakenfist.images.blob.Blob.from_db',
                        return_value=cached_blob), \
                mock.patch.object(
                    helper, '_open_connection',
                    side_effect=AssertionError(
                        'the source must not be consulted')), \
                mock.patch.object(
                    helper, '_blob_get',
                    return_value=fetched_blob) as mock_blob_get:
            b = helper.transfer_image(cached_only=True)

        mock_blob_get.assert_called_once_with('sf://blob/blob-1')
        self.assertEqual(fetched_blob, b)

    def test_cached_only_with_no_versions_raises(self, _mock_events):
        helper = self._helper(index=0)
        self.assertRaises(
            exceptions.BlobMissing, helper.transfer_image, cached_only=True)

    def test_cached_only_with_a_missing_blob_raises(self, _mock_events):
        helper = self._helper()
        with mock.patch('shakenfist.images.blob.Blob.from_db',
                        return_value=None):
            self.assertRaises(
                exceptions.BlobMissing, helper.transfer_image,
                cached_only=True)

    def test_missing_blob_and_dead_source_refetches(self, _mock_events):
        # Regression: when the index named a blob which no longer existed
        # AND the source check failed, dirty stayed False and building the
        # blob URL crashed with an AttributeError on None.
        helper = self._helper()
        with mock.patch('shakenfist.images.blob.Blob.from_db',
                        return_value=None), \
                mock.patch.object(
                    helper, '_open_connection',
                    side_effect=exceptions.HTTPError('status code 404')), \
                mock.patch.object(
                    helper, '_http_get_inner') as mock_http_get:
            helper.transfer_image()

        mock_http_get.assert_called_once_with(self.URL)
