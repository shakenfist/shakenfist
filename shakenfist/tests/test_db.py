import mock

from shakenfist import db
from shakenfist.tests import test_shakenfist


class DBTestCase(test_shakenfist.ShakenFistTestCase):
    maxDiff = None

    def setUp(self):
        super(DBTestCase, self).setUp()

    @mock.patch('etcd3gw.Etcd3Client.get_prefix',
                return_value=[
                    ('''{"checksum": "ed44b9745b8d62bcbbc180b5f36c24bb",
                        "file_version": 1,
                        "size": "359464960",
                        "version": 1
                        }''',
                        {'key': b'/sf/image/095fdd2b66625412aa/sf-2',
                         'create_revision': '198335947',
                         'mod_revision': '198335947',
                         'version': '1'}),
                    ('''{"checksum": null,
                        "file_version": 1,
                        "size": "16338944",
                        "version": 1
                        }''',
                        {'key': b'/sf/image/aca41cefa18b052074e092/sf-3',
                         'create_revision': '200780292',
                         'mod_revision': '200780292',
                         'version': '1'
                         })])
    def test_get_image_metadata_all(self, mock_get):
        val = db.get_image_metadata_all()
        self.assertDictEqual({
            '/sf/image/095fdd2b66625412aa/sf-2': {
                'checksum': 'ed44b9745b8d62bcbbc180b5f36c24bb',
                'file_version': 1,
                'size': '359464960',
                'version': 1
                },
            '/sf/image/aca41cefa18b052074e092/sf-3': {
                'checksum': None,
                'file_version': 1,
                'size': '16338944',
                'version': 1
                },
            },
            val)

        val = db.get_image_metadata_all('sf-2')
        self.assertDictEqual({
            '/sf/image/095fdd2b66625412aa/sf-2': {
                'checksum': 'ed44b9745b8d62bcbbc180b5f36c24bb',
                'file_version': 1,
                'size': '359464960',
                'version': 1
                },
            },
            val)
