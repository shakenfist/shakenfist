import string

from shakenfist_ci import base


class TestUploads(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'uploads'
        super().__init__(*args, **kwargs)

    def test_upload(self):
        upl = self.test_client.create_upload()
        for _ in range(100):
            self.test_client.send_upload(upl['uuid'], string.ascii_letters)

        self.test_client.truncate_upload(upl['uuid'], 100)

        for _ in range(50):
            self.test_client.send_upload(upl['uuid'], string.ascii_letters)

        a = self.test_client.upload_artifact('test', upl['uuid'])

        self.assertEqual(
            len(string.ascii_letters) * 50 + 100, a['blobs']['1']['size'])
