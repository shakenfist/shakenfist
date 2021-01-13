import mock
import os
import testtools
from pydantic import AnyHttpUrl
import shutil


from shakenfist import exceptions
from shakenfist import images
from shakenfist import image_resolver_cirros
from shakenfist import image_resolver_ubuntu
from shakenfist import logutil
from shakenfist.baseobject import State
from shakenfist.tests import test_shakenfist
from shakenfist.config import SFConfigBase


LOG, _ = logutil.setup(__name__)
TEST_DIR = os.path.dirname(os.path.abspath(__file__))


with open('%s/files/qemu-img-info' % TEST_DIR) as f:
    QEMU_IMG_OUT = f.read()

with open('%s/files/cirros-download' % TEST_DIR) as f:
    CIRROS_DOWNLOAD_HTML = f.read()

with open('%s/files/cirros-MD5SUMS-0.3.4' % TEST_DIR) as f:
    CIRROS_MD5SUM_0_3_4 = f.read()

with open('%s/files/ubuntu-MD5SUMS-bionic' % TEST_DIR) as f:
    UBUNTU_MD5SUM_BIONIC = f.read()

with open('%s/files/ubuntu-MD5SUMS-groovy' % TEST_DIR) as f:
    UBUNTU_MD5SUM_GROOVY = f.read()

with open('%s/files/ubuntu-download' % TEST_DIR) as f:
    UBUNTU_DOWNLOAD_HTML = f.read()


class FakeResponse(object):
    def __init__(self, status, text, headers={}):
        self.status_code = status
        self.text = text
        self.headers = headers

    def iter_content(self, chunk_size=None):
        for i in range(10):
            yield 'AAAAAAAAAA'

    def close(self):
        pass


class FakeImage(object):
    def __init__(self, url='http://somewhere'):
        self.url = url

    def unique_label(self):
        return ('image', self.url)


class FakeConfig(SFConfigBase):
    STORAGE_PATH: str = '/a/b/c'
    NODE_NAME: str = 'sf-245'
    DOWNLOAD_URL_CIRROS: AnyHttpUrl = (
        'http://download.cirros-cloud.net/%(vernum)s/'
        'cirros-%(vernum)s-x86_64-disk.img')

    DOWNLOAD_URL_UBUNTU: AnyHttpUrl = (
        'https://cloud-images.ubuntu.com/%(vername)s/current/'
        '%(vername)s-server-cloudimg-amd64.img')


fake_config = FakeConfig()


class ImageUtilsTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        fake_config = FakeConfig()

        self.config = mock.patch('shakenfist.images.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.etcd.get_all',
                return_value=[('download_1', {'sequence': 1})])
    def test_get_cache_path(self, mock_get_all, mock_exists):
        i = images.Image({
            'url': 'https://www.shakenfist.com',
            'checksum': None,
            'ref': 'hdjkhghjsdfjkhfdghk',
            'node': 'sf-245',
            'version': 2
        })
        p = i.version_image_path()
        mock_exists.assert_called_with('/a/b/c/image_cache')
        self.assertTrue(p.startswith('/a/b/c/image_cache'))

    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.etcd.get_all',
                return_value=[('download_1', {'sequence': 1})])
    def test_get_cache_path_create(self, mock_get_all, mock_makedirs, mock_exists):
        i = images.Image({
            'url': 'https://www.shakenfist.com',
            'checksum': None,
            'ref': 'hdjkhghjsdfjkhfdghk',
            'node': 'sf-245',
            'version': 2
        })
        p = i.version_image_path()
        mock_exists.assert_called_with('/a/b/c/image_cache')
        mock_makedirs.assert_called_with('/a/b/c/image_cache', exist_ok=True)
        self.assertTrue(p.startswith('/a/b/c/image_cache'))


class ImageResolversTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        fake_config = FakeConfig()

        self.config = mock.patch('shakenfist.config.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('requests.get', side_effect=[
        FakeResponse(200, CIRROS_DOWNLOAD_HTML),
        FakeResponse(200, ''),  # Handle no file available
        FakeResponse(200, CIRROS_DOWNLOAD_HTML),
        FakeResponse(200, CIRROS_MD5SUM_0_3_4),
        FakeResponse(200, CIRROS_DOWNLOAD_HTML),
    ])
    def test_resolve_cirros(self, mock_get):
        u = image_resolver_cirros.resolve('cirros')
        self.assertEqual(
            ('http://download.cirros-cloud.net/0.5.1/cirros-0.5.1-x86_64-disk.img',
             None),
            u)

        u = image_resolver_cirros.resolve('cirros:0.3.4')
        self.assertEqual(
            ('http://download.cirros-cloud.net/0.3.4/cirros-0.3.4-x86_64-disk.img',
             'ee1eca47dc88f4879d8a229cc70a07c6'),
            u)

        self.assertRaises(exceptions.VersionSpecificationError,
                          image_resolver_cirros.resolve, 'cirros***')

    @mock.patch('requests.get', return_value=FakeResponse(404, None))
    def test_resolve_cirros_error(self, mock_get):
        self.assertRaises(exceptions.HTTPError,
                          image_resolver_cirros.resolve, 'cirros')

    @mock.patch('requests.get', side_effect=[
        FakeResponse(200, UBUNTU_DOWNLOAD_HTML),
        FakeResponse(200, UBUNTU_MD5SUM_GROOVY),
        FakeResponse(200, UBUNTU_DOWNLOAD_HTML),
        FakeResponse(200, UBUNTU_MD5SUM_BIONIC),
        FakeResponse(200, UBUNTU_DOWNLOAD_HTML),
        FakeResponse(200, UBUNTU_MD5SUM_BIONIC),
        FakeResponse(200, UBUNTU_DOWNLOAD_HTML),
    ])
    @mock.patch('shakenfist.image_resolver_ubuntu.UBUNTU_URL',
                'https://cloud-images.ubuntu.com')
    def test_resolve_ubuntu(self, mock_get):
        u = image_resolver_ubuntu.resolve('ubuntu')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/groovy/current/'
             'groovy-server-cloudimg-amd64.img',
             '1c19b08060b9feb1cd0e7ee28fd463fb'),
            u)

        u = image_resolver_ubuntu.resolve('ubuntu:bionic')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/bionic/current/'
             'bionic-server-cloudimg-amd64.img',
             'ed44b9745b8d62bcbbc180b5f36c24bb'),
            u)

        u = image_resolver_ubuntu.resolve('ubuntu:18.04')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/bionic/current/'
             'bionic-server-cloudimg-amd64.img',
             'ed44b9745b8d62bcbbc180b5f36c24bb'),
            u)

        self.assertRaises(exceptions.VersionSpecificationError,
                          image_resolver_ubuntu.resolve, 'ubuntu***')

    @mock.patch('requests.get', return_value=FakeResponse(404, None))
    def test_resolve_ubuntu_error(self, mock_get):
        self.assertRaises(exceptions.HTTPError,
                          image_resolver_ubuntu.resolve, 'ubuntu')


class ImageObjectTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        fake_config = FakeConfig()

        self.config = mock.patch('shakenfist.images.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.create = mock.patch('shakenfist.etcd.create')
        self.mock_create = self.create.start()
        self.addCleanup(self.create.stop)

        self.put = mock.patch('shakenfist.etcd.put')
        self.mock_put = self.put.start()
        self.addCleanup(self.put.stop)

    @mock.patch('shakenfist.image_resolver_cirros.resolve',
                return_value=('!!!cirros!!!', '123abc'))
    @mock.patch('shakenfist.image_resolver_ubuntu.resolve',
                return_value=('!!!ubuntu!!!', '123abc'))
    def test_resolve_image(self, mock_ubuntu, mock_centos):
        self.assertEqual(('win10', None),
                         images.Image._resolve('win10'))
        self.assertEqual(('http://example.com/image', None),
                         images.Image._resolve('http://example.com/image'))
        self.assertEqual(('!!!cirros!!!', '123abc'),
                         images.Image._resolve('cirros'))
        self.assertEqual(('!!!ubuntu!!!', '123abc'),
                         images.Image._resolve('ubuntu'))

    @mock.patch('shakenfist.etcd.get',
                return_value={
                    'url': 'http://example.com',
                    'version': 1
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.images.Image.update_checksum')
    def test_image_rejects_bad_version(
            self, mock_checksum, mock_mkdirs, mock_get_meta):
        with testtools.ExpectedException(exceptions.BadObjectVersion):
            images.Image.new('http://example.com')

    def test_hash_image(self):
        self.assertEqual('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                         '852c2df5c7991cc66241bf7072d1c4',
                         images.Image.calc_unique_ref('http://example.com'))

    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.images.Image._db_get_attribute',
                side_effect=[
                    {'value': None, 'update_time': 0},
                    {'value': 'initial', 'update_time': 0},
                    {'value': 'initial', 'update_time': 0},
                    {'value': 'creating', 'update_time': 0},
                    {'value': 'created', 'update_time': 0},
                    {'value': 'error', 'update_time': 0},
                    {'value': 'deleted', 'update_time': 0},
                ])
    @mock.patch('shakenfist.images.Image._db_set_attribute')
    @mock.patch('shakenfist.etcd.put')
    def test_state_property_valid(
            self, mock_put, mock_attribute_set, mock_state_get, mock_lock):

        i = images.Image({
            'ref': 'ref',
            'node': 'bod',
            'version': 1,
            'url': 'a'
            })
        i.state = 'initial'
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = 'created'
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = 'created'
        i.state = 'deleted'
        i.state = 'error'
        i.state = 'deleted'
        with testtools.ExpectedException(exceptions.InvalidStateException):
            i.state = 'created'

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                return_value={
                    'url': 'http://example.com',
                    'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                            '852c2df5c7991cc66241bf7072d1c4'),
                    'node': 'sf-245',
                    'version': 2
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    def test_image_stores_checksum(
            self, mock_checksum, mock_set_attr, mock_mkdirs, mock_get):
        images.Image.new('http://example.com', '1abab')
        self.assertEqual([mock.call('latest_checksum', {'checksum': '1abab'})],
                         mock_set_attr.mock_calls)

    @mock.patch('shakenfist.images.Image.state')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                side_effect=[None,
                             {
                                 'url': 'http://example.com',
                                 'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                                         '852c2df5c7991cc66241bf7072d1c4'),
                                 'node': 'sf-245',
                                 'version': 2
                             }])
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_create')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    def test_image_persist(
            self, mock_checksum, mock_create, mock_set_attr, mock_mkdirs, mock_get,
            mock_state):
        mock_state.setter.return_value = State(None, 1)
        images.Image.new('http://example.com', '1abab')
        self.assertEqual([
            mock.call('f0e6a6a97042a4f1f1c87f5f7d44315b2d852c2df5c7991cc66241'
                      'bf7072d1c4/sf-245',
                      {
                          'uuid': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d852c2df'
                                   '5c7991cc66241bf7072d1c4/sf-245'),
                          'url': 'http://example.com',
                          'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                                  '852c2df5c7991cc66241bf7072d1c4'),
                          'node': 'sf-245',
                          'version': 2
                      })
        ], mock_create.mock_calls)
        self.assertEqual([
            mock.call('latest_checksum', {'checksum': '1abab'})
        ], mock_set_attr.mock_calls)

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                return_value={
                    'url': 'http://some.com',
                    'ref': ('bbf155338660b47643506f35a6f92eba'
                            'ef11e630b17d33da88b8638d267763f4'),
                    'node': 'sf-245',
                    'version': 2
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    def test_version_image_path(
            self, mock_version, mock_checksum, mock_mkdirs, mock_get_meta):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        img = images.Image.new('http://some.com')
        img.file_version = 1
        self.assertEqual('/a/b/c/image_cache/bbf155338660b476435'
                         '06f35a6f92ebaef11e630b17d33da88b8638d267763f4.v001',
                         img.version_image_path())

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                return_value={
                    'url': 'http://example.com',
                    'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                            '852c2df5c7991cc66241bf7072d1c4'),
                    'node': 'sf-245',
                    'version': 2
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    def test_does_not_require_fetch(
            self, mock_version, mock_checksum, mock_mkdirs, mock_get,
            mock_request_head):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        img = images.Image.new('http://example.com')
        dirty_fields = img._new_image_available(img._open_connection())
        self.assertEqual(False, dirty_fields)

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Fri, 06 Mar 2020 19:19:05 GMT',
                             'Content-Length': 200000}))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                return_value={
                    'url': 'http://example.com',
                    'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                            '852c2df5c7991cc66241bf7072d1c4'),
                    'node': 'sf-245',
                    'version': 2
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    def test_requires_fetch_due_age(
            self, mock_version, mock_checksum, mock_mkdirs, mock_get,
            mock_request_head):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        img = images.Image.new('http://example.com')
        dirty_fields = img._new_image_available(img._open_connection())
        self.assertEqual(('modified',
                          'Tue, 10 Sep 2019 07:24:40 GMT',
                          'Fri, 06 Mar 2020 19:19:05 GMT'),
                         dirty_fields)

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 16338944}))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                return_value={
                    'url': 'http://example.com',
                    'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                            '852c2df5c7991cc66241bf7072d1c4'),
                    'node': 'sf-245',
                    'version': 2
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    def test_requires_fetch_due_size(
            self, mock_version, mock_checksum, mock_mkdirs, mock_get,
            mock_request_head):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        img = images.Image.new('http://example.com')
        dirty_fields = img._new_image_available(img._open_connection())
        self.assertEqual(('size', 200000, 16338944), dirty_fields)

    @mock.patch('shakenfist.images.Image.state')
    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 16338944}))
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                side_effect=[None,
                             {
                                 'url': 'http://example.com',
                                 'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                                         '852c2df5c7991cc66241bf7072d1c4'),
                                 'node': 'sf-245',
                                 'version': 2
                             }])
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    def test_fetch_image_new(
            self, mock_version, mock_checksum, mock_mkdirs, mock_get,
            mock_request_head, mock_state):
        mock_version.return_value = {}
        mock_state.setter.return_value = State(None, 1)

        img = images.Image.new('http://example.com')
        dirty_fields = img._new_image_available(img._open_connection())
        self.assertEqual(('modified', None, 'Tue, 10 Sep 2019 07:24:40 GMT'),
                         dirty_fields)

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.add_event')
    def test_transcode_image_noop(self, mock_event, mock_lock, mock_exists,
                                  mock_execute):
        images._transcode(None, '/a/b/c/hash', FakeImage())
        mock_execute.assert_not_called()

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'file format': 'qcow2'})
    @mock.patch('os.link')
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.add_event')
    def test_transcode_image_link(self, mock_event, mock_lock, mock_link,
                                  mock_identify, mock_exists, mock_execute):
        images._transcode(None, '/a/b/c/hash', FakeImage())
        mock_link.assert_called_with('/a/b/c/hash', '/a/b/c/hash.qcow2')
        mock_execute.assert_not_called()

    @mock.patch('shakenfist.util.execute', return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'file format': 'raw'})
    @mock.patch('os.link')
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.add_event')
    def test_transcode_image_convert(self, mock_event, mock_lock, mock_link,
                                     mock_identify, mock_exists, mock_execute):
        images._transcode(None, '/a/b/c/hash', FakeImage())
        mock_link.assert_not_called()
        mock_execute.assert_called_with(
            None,
            'qemu-img convert -t none -O qcow2 /a/b/c/hash /a/b/c/hash.qcow2')

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                return_value={
                    'url': 'http://example.com',
                    'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                            '852c2df5c7991cc66241bf7072d1c4'),
                    'node': 'sf-245',
                    'version': 2
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.util.execute', return_value=(None, None))
    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 8 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_create')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    def test_resize_image_noop(
            self, mock_version, mock_checksum, mock_new, mock_link,
            mock_identify, mock_exists, mock_execute, mock_makedirs,
            mock_get_meta):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        img = images.Image.new('http://example.com')
        img.resize(None, 8)
        mock_link.assert_not_called()
        mock_execute.assert_not_called()

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                return_value={
                    'url': 'http://example.com',
                    'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                            '852c2df5c7991cc66241bf7072d1c4'),
                    'node': 'sf-245',
                    'version': 2
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.util.execute', return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 8 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_create')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    def test_resize_image_link(
            self, mock_version, mock_checksum, mock_new, mock_link,
            mock_identify, mock_exists, mock_execute, mock_makedirs,
            mock_get_meta):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        img = images.Image.new('http://example.com')
        img.resize(None, 8)
        mock_link.assert_called_with(
            '/a/b/c/image_cache/f0e6a6a97042a4f1f1c87f5f7d4'
            '4315b2d852c2df5c7991cc66241bf7072d1c4.v001',
            '/a/b/c/image_cache/f0e6a6a97042a4f1f1c87f5f7d4'
            '4315b2d852c2df5c7991cc66241bf7072d1c4.v001.qcow2.8G')
        mock_execute.assert_not_called()

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get',
                return_value={
                    'url': 'http://example.com',
                    'ref': ('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                            '852c2df5c7991cc66241bf7072d1c4'),
                    'node': 'sf-245',
                    'version': 2
                })
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.util.execute', return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 4 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_create')
    @mock.patch('shakenfist.images.Image.checksum',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    def test_resize_image_resize(
            self, mock_version, mock_checksum, mock_new, mock_link,
            mock_identify, mock_exists, mock_execute, mock_makedirs,
            mock_get_meta):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        img = images.Image.new('http://example.com')
        img.resize(None, 8)
        mock_link.assert_not_called()
        mock_execute.assert_has_calls(
            [mock.call(
                None,
                ('qemu-img create -b '
                 '/a/b/c/image_cache/f0e6a6a97042a4f1f1c87f5f7d4431'
                 '5b2d852c2df5c7991cc66241bf7072d1c4.v001.qcow2 '
                 '-f qcow2 /a/b/c/image_cache/f0e6a6a97042a4f1f1c87f'
                 '5f7d44315b2d852c2df5c7991cc66241bf7072d1c4.v001.qcow2.8G 8G')
            )
            ]
        )

    @mock.patch('shakenfist.util.execute',
                return_value=(QEMU_IMG_OUT, None))
    @mock.patch('os.path.exists', return_value=True)
    def test_identify_image(self, mock_exists, mock_execute):
        d = images.identify('/tmp/foo')
        self.assertEqual({
            'cluster_size': 65536.0,
            'compat': 1.1,
            'corrupt': 'false',
            'disk size': 16777216.0,
            'file format': 'qcow2',
            'image': '/tmp/foo',
            'lazy refcounts': 'false',
            'refcount bits': 16.0,
            'virtual size': 117440512.0
        }, d)

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    def test_create_cow(self, mock_exists, mock_execute):
        images.create_cow(None, '/a/b/c/base', '/a/b/c/cow', 10)
        mock_execute.assert_called_with(
            None, 'qemu-img create -b /a/b/c/base -f qcow2 /a/b/c/cow 10G')

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    def test_snapshot(self, mock_execute):
        images.snapshot(None, '/a/b/c/base', '/a/b/c/snap')
        mock_execute.assert_called_with(
            None,
            'qemu-img convert --force-share -O qcow2 /a/b/c/base /a/b/c/snap')


class FakeConfigTmpFile(SFConfigBase):
    STORAGE_PATH: str = '/tmp/'
    NODE_NAME: str = 'sf-245'
    DOWNLOAD_URL_CIRROS: AnyHttpUrl = (
        'http://download.cirros-cloud.net/%(vernum)s/'
        'cirros-%(vernum)s-x86_64-disk.img')

    DOWNLOAD_URL_UBUNTU: AnyHttpUrl = (
        'https://cloud-images.ubuntu.com/%(vername)s/current/'
        '%(vername)s-server-cloudimg-amd64.img')


class FakeHeaders(object):
    def __init__(self, headers):
        self.headers = headers

    def get(self, header):
        return self.headers.get(header)


class FakeResp(object):
    def __init__(self, headers=None, chunks=None):
        self.headers = FakeHeaders(headers)
        self.chunks = chunks

    def iter_content(self, chunk_size=None):
        for c in self.chunks.pop():
            yield c


class FakeImageChecksum(images.Image):
    def __init__(self, static_values):
        super(FakeImageChecksum, self).__init__(static_values)
        self.__checksum = None

    @property
    def checksum(self):
        return self.__checksum

    def update_checksum(self, new_checksum):
        self.__checksum = new_checksum


class ImageChecksumTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        fake_config = FakeConfigTmpFile()
        if not os.path.exists('/tmp/image_cache'):
            os.mkdir('/tmp/image_cache')
            self.addCleanup(shutil.rmtree, '/tmp/image_cache')

        self.config = mock.patch('shakenfist.images.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    def test_correct_checksum(self):
        test_file = '/tmp/' + self.id()
        test_checksum = 'a5890ace30a3e84d9118196c161aeec2'

        image = FakeImageChecksum({
            'url': 'testurl',
            'ref': 'hdjkhghjsdfjkhfdghk',
            'node': 'sf-245',
            'version': 2
        })
        image.update_checksum(test_checksum)

        with open(test_file, 'w') as f:
            f.write('this is a test file')
        self.addCleanup(os.remove, test_file)

        # Correct checksum
        self.assertTrue(image.correct_checksum(test_file))

        # Bad checksum
        image.update_checksum('wrong')
        self.assertFalse(image.correct_checksum(test_file))

    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.db.refresh_locks')
    def test__fetch(self, mock_refresh_locks, mock_version):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        test_checksum = '097c42989a9e5d9dcced7b35ec4b0486'
        image = FakeImageChecksum({
            'url': 'testurl',
            'ref': '67066e685b94da02c9b7bb3840a9624b306755f7e1e5453dee8f3e665f34ff8f',
            'node': 'sf-245',
            'version': 2
        })
        image.update_checksum(test_checksum)

        # Data matching checksum
        resp = FakeResp(chunks=[(b'chunk1', b'chunk2')])
        ret = image._fetch(resp)
        self.addCleanup(os.remove, ret)
        self.assertEqual(
            '/tmp/image_cache/'
            '67066e685b94da02c9b7bb3840a9624b306755f7e1e5453dee8f3e665f34ff8f.v002',
            ret)
        self.assertTrue(image.correct_checksum(ret))

        # Data does not match checksum
        resp = FakeResp(chunks=[(b'chunk1', b'badchunk2')])
        ret = image._fetch(resp)
        self.assertEqual(
            '/tmp/image_cache/'
            '67066e685b94da02c9b7bb3840a9624b306755f7e1e5453dee8f3e665f34ff8f.v002',
            ret)
        self.assertFalse(image.correct_checksum(ret))

    # Data matches checksum
    @mock.patch('shakenfist.images.Image.state')
    @mock.patch('shakenfist.images.Image._add_download_version')
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images._transcode')
    @mock.patch('shakenfist.images.Image._open_connection',
                return_value=FakeResp(headers={'Last-Modified': 'yesterday',
                                               'Content-Length': 123,
                                               },
                                      chunks=[(b'chunk1', b'chunk2')])
                )
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.db.refresh_locks')
    def test_get(
            self, mock_refresh_locks, mock_put, mock_open, mock_transcode,
            mock_version, mock_add_version, mock_state):
        mock_state.setter.return_value = State(None, 1)
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        test_checksum = '097c42989a9e5d9dcced7b35ec4b0486'
        image = FakeImageChecksum({
            'url': 'testurl',
            'ref': '67066e685b94da02c9b7bb3840a9624b306755f7e1e5453dee8f3e665f34ff8f',
            'node': 'sf-245',
            'version': 2
        })
        image.update_checksum(test_checksum)

        ret = image.get(None, None)
        self.addCleanup(os.remove, ret)

        self.assertEqual(
            '/tmp/image_cache/'
            '67066e685b94da02c9b7bb3840a9624b306755f7e1e5453dee8f3e665f34ff8f.v002',
            ret)
        self.assertTrue(image.correct_checksum(ret))
        self.assertEqual(
            [mock.call(123, 'yesterday', mock.ANY)],
            mock_add_version.mock_calls)

    # First download attempt corrupted, second download matches checksum
    @mock.patch('shakenfist.images.Image.state')
    @mock.patch('shakenfist.images.Image._add_download_version')
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images._transcode')
    @mock.patch('shakenfist.images.Image._open_connection',
                return_value=FakeResp(headers={'Last-Modified': 'yesterday',
                                               'Content-Length': 123,
                                               },
                                      chunks=[
                                          (b'chunk1', b'badchunk2'),
                                          (b'chunk1', b'chunk2'),
                ]))
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.db.refresh_locks')
    def test_get_one_corrupt(
            self, mock_refresh_locks, mock_put, mock_open, mock_transcode,
            mock_version, mock_add_version, mock_state):
        mock_state.setter.return_value = State(None, 1)
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }

        test_checksum = '097c42989a9e5d9dcced7b35ec4b0486'
        image = FakeImageChecksum({
            'url': 'testurl',
            'ref': '67066e685b94da02c9b7bb3840a9624b306755f7e1e5453dee8f3e665f34ff8f',
            'node': 'sf-245',
            'version': 2
        })
        image.update_checksum(test_checksum)

        ret = image.get(None, None)
        self.addCleanup(os.remove, ret)
        self.assertEqual(
            '/tmp/image_cache/'
            '67066e685b94da02c9b7bb3840a9624b306755f7e1e5453dee8f3e665f34ff8f.v002',
            ret)
        self.assertTrue(image.correct_checksum(ret))

    # All download attempts not matching checksum
    @mock.patch('shakenfist.images.Image._db_get_attribute',
                side_effect=[
                    {'value': 'creating', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    {'value': 'error', 'update_time': 123},
                    ])
    @mock.patch('shakenfist.images.Image.get_lock_attr')
    @mock.patch('shakenfist.images.Image._add_download_version')
    @mock.patch('shakenfist.images.Image.latest_download_version',
                new_callable=mock.PropertyMock)
    @mock.patch('shakenfist.images.Image._open_connection',
                return_value=FakeResp(headers={'Last-Modified': 'yesterday',
                                               'Content-Length': 123,
                                               },
                                      chunks=[
                                          (b'chunk1', b'badchunk2'),
                                          (b'chunk1', b'badchunk2'),
                                          (b'chunk1', b'differentbadchunk'),
                ]))
    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.db.refresh_locks')
    def test_get_always_corrupt(
            self, mock_refresh_locks, mock_put, mock_open,
            mock_version, mock_add_version, get_lock_attr,
            mock_db_get_attribute):
        mock_version.return_value = {
            'size': 200000,
            'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
            'fetched_at': 'Tue, 20 Oct 2020 23:02:29 -0000',
            'sequence': 1
        }
        test_checksum = '097c42989a9e5d9dcced7b35ec4b0486'
        image = FakeImageChecksum({
            'url': 'testurl',
            'ref': 'hdjkhghjsdfjkhfdghk',
            'node': 'sf-245',
            'version': 2
        })
        image.update_checksum(test_checksum)

        self.assertRaises(exceptions.BadCheckSum, image.get, None, None)
