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
