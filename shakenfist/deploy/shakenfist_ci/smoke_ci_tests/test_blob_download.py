import json
import random

from testtools import content

from shakenfist_ci import base


class TestBlobDownload(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'blob-download'
        super().__init__(*args, **kwargs)

    def test_download_size_matches_expected(self):
        """Regression test: blob download must stop at the expected size.

        sf-client get_blob_data() with blob-data-limit support reads in
        512MB chunks. A bug where the offset was not advanced between
        chunk requests caused the same data to be re-read forever,
        producing an infinitely growing download. This test verifies
        that the total bytes received matches the artifact's declared
        blob size.
        """
        img = self.test_client.cache_artifact(
            'https://sfcbr.shakenfist.com/cgi-bin/uuid.cgi?uniq=%06d'
            % random.randint(-999999, 999999))
        self.addDetail(
            'img initial',
            content.text_content(json.dumps(img, indent=4, sort_keys=True)))
        results = self._await_artifacts_ready([img['uuid']])
        img = results[0]
        self.addDetail(
            'img ready',
            content.text_content(json.dumps(img, indent=4, sort_keys=True)))

        self.assertIn('blobs', img)
        self.assertEqual(1, len(img['blobs']))
        self.assertIn(1, img['blobs'])
        blob_uuid = img['blobs'][1]['uuid']
        expected_size = img['blobs'][1]['size']

        self.addDetail('blob_uuid', content.text_content(blob_uuid))
        self.addDetail('expected_size', content.text_content(str(expected_size)))

        # Download the blob data and verify total size matches
        total = 0
        for chunk in self.test_client.get_blob_data(blob_uuid):
            total += len(chunk)

            # Fail fast if we exceed the expected size
            self.assertLessEqual(
                total, expected_size,
                'Downloaded %d bytes but blob size is %d'
                % (total, expected_size))

        self.assertEqual(
            expected_size, total,
            'Downloaded %d bytes but expected %d' % (total, expected_size))

    def test_download_with_offset_and_limit(self):
        """Verify that offset and limit parameters work correctly."""
        img = self.test_client.cache_artifact(
            'https://sfcbr.shakenfist.com/cgi-bin/uuid.cgi?uniq=%06d'
            % random.randint(-999999, 999999))
        results = self._await_artifacts_ready([img['uuid']])
        img = results[0]

        blob_uuid = img['blobs'][1]['uuid']
        expected_size = img['blobs'][1]['size']

        self.addDetail('blob_uuid', content.text_content(blob_uuid))
        self.addDetail('expected_size', content.text_content(str(expected_size)))

        # Download the full blob
        full_data = b''
        for chunk in self.test_client.get_blob_data(blob_uuid):
            full_data += chunk
        self.assertEqual(expected_size, len(full_data))

        # Download with an offset partway through
        offset = expected_size // 2
        partial_data = b''
        for chunk in self.test_client.get_blob_data(blob_uuid, offset=offset):
            partial_data += chunk

        self.assertEqual(
            expected_size - offset, len(partial_data),
            'Partial download from offset %d returned %d bytes, expected %d'
            % (offset, len(partial_data), expected_size - offset))
        self.assertEqual(full_data[offset:], partial_data)
