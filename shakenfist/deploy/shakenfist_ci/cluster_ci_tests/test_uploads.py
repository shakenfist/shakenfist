import json
import string

from shakenfist_client import apiclient
from testtools import content

from shakenfist_ci import base


class TestUploads(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'uploads'
        super().__init__(*args, **kwargs)

    def test_upload(self):
        upl = self.test_client.create_upload()
        self.addDetail(
            'upl',
            content.text_content(json.dumps(upl, indent=4, sort_keys=True)))
        for _ in range(100):
            self.test_client.send_upload(upl['uuid'], string.ascii_letters)

        self.test_client.truncate_upload(upl['uuid'], 100)

        for _ in range(50):
            self.test_client.send_upload(upl['uuid'], string.ascii_letters)

        a = self.test_client.upload_artifact('test', upl['uuid'])
        self.addDetail(
            'artifact',
            content.text_content(json.dumps(a, indent=4, sort_keys=True)))

        self.assertEqual(
            len(string.ascii_letters) * 50 + 100, a['blobs']['1']['size'])

    def test_truncate_rejects_a_bad_offset(self):
        # The truncate offset used to reach os.truncate through an
        # unguarded int(): a non-numeric offset raised ValueError and
        # became a 500 with a ValueError repr in the body, and a
        # negative one raised OSError and did the same. Both are client
        # input, so both must be a 400. This is the same defect class
        # as issue 3609.
        upl = self.test_client.create_upload()
        self.test_client.send_upload(upl['uuid'], string.ascii_letters)

        # Assert the specific message per case: 'offset' alone matches
        # either, so it could not tell the two rejection paths apart
        # and would keep passing if one regressed into the other.
        cases = (
            ('banana', 'offset is not an integer'),
            ('-1', 'offset must not be negative'),
        )
        for offset, expected in cases:
            exc = self.assertRaises(
                apiclient.RequestMalformedException,
                self.test_client._request_url,
                'POST', '/upload/' + upl['uuid'] + '/truncate/' + offset)
            self.assertEqual(400, exc.status_code, 'offset %s' % offset)
            self.assertIn(expected, exc.text, 'offset %s' % offset)

        # A valid truncate still works after the rejections, so the
        # upload was not damaged by them.
        self.test_client.truncate_upload(upl['uuid'], 10)
        a = self.test_client.upload_artifact('test-bad-offset', upl['uuid'])
        self.assertEqual(10, a['blobs']['1']['size'])
