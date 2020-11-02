import mock
import os
import testtools
from pydantic import AnyHttpUrl


from shakenfist import exceptions
from shakenfist import images
from shakenfist import image_resolver_cirros
from shakenfist import image_resolver_ubuntu
from shakenfist.tests import test_shakenfist
from shakenfist.config import SFConfigBase

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
    DOWNLOAD_URL_CIRROS: AnyHttpUrl = ('http://download.cirros-cloud.net/%(vernum)s/'
                                       'cirros-%(vernum)s-x86_64-disk.img')

    DOWNLOAD_URL_UBUNTU: AnyHttpUrl = ('https://cloud-images.ubuntu.com/%(vername)s/current/'
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
    def test_get_cache_path(self, mock_exists):
        p = images._get_cache_path()
        mock_exists.assert_called_with('/a/b/c/image_cache')
        self.assertEqual('/a/b/c/image_cache', p)

    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('os.makedirs')
    def test_get_cache_path_create(self, mock_makedirs, mock_exists):
        p = images._get_cache_path()
        mock_exists.assert_called_with('/a/b/c/image_cache')
        mock_makedirs.assert_called_with('/a/b/c/image_cache')
        self.assertEqual('/a/b/c/image_cache', p)


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

    @mock.patch('shakenfist.image_resolver_cirros.resolve',
                return_value=('!!!cirros!!!', '123abc'))
    @mock.patch('shakenfist.image_resolver_ubuntu.resolve',
                return_value=('!!!ubuntu!!!', '123abc'))
    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    def test_resolve_image(self, mock_mkdirs, mock_exists, mock_ubuntu,
                           mock_centos):
        img = images.Image.from_url('win10')
        self.assertEqual('win10', img.url)

        img = images.Image.from_url('http://example.com/image')
        self.assertEqual('http://example.com/image', img.url)

        img = images.Image.from_url('cirros')
        self.assertEqual('!!!cirros!!!', img.url)

        img = images.Image.from_url('ubuntu')
        self.assertEqual('!!!ubuntu!!!', img.url)

    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    def test_hash_image(self, mock_mkdirs, mock_get_meta):
        img = images.Image.from_url('http://example.com')
        self.assertEqual('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                         '852c2df5c7991cc66241bf7072d1c4', img.unique_ref)

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    @mock.patch('shakenfist.db.get_image_metadata',
                return_value={
                    'checksum': None,
                    'fetched': 'Tue, 20 Oct 2020 23:02:29 -0000',
                    'file_version': 1,
                    'modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                    'size': 200000,
                    'url': 'http://example.com',
                    'version': 1
                })
    @mock.patch('os.makedirs')
    def test_does_not_require_fetch(self, mock_mkdirs,
                                    mock_get_meta, mock_request_head):
        img = images.Image.from_url('http://example.com')
        dirty_fields = img._new_image_available(img._open_connection())
        self.assertEqual(1, img.file_version)
        self.assertEqual(False, dirty_fields)

    @mock.patch('shakenfist.db.get_image_metadata',
                return_value={
                    'checksum': None,
                    'fetched': 'Tue, 20 Oct 2020 23:02:29 -0000',
                    'file_version': 1,
                    'modified': 'Fri, 06 Mar 2020 19:19:05 GMT',
                    'size': 16338944,
                    'url': 'http://example.com',
                    'version': 999
                })
    @mock.patch('os.makedirs')
    def test_image_rejects_bad_packet(self, mock_mkdirs, mock_get_meta):
        with testtools.ExpectedException(exceptions.BadMetadataPacket):
            images.Image.from_url('http://example.com')

    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    def test_image_stores_checksum(self, mock_mkdirs, mock_get_meta):
        img = images.Image.from_url('http://example.com', '1abab')
        self.assertEqual('1abab', img.checksum)

    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.db.persist_image_metadata')
    def test_image_persist(self, mock_db_persist, mock_mkdirs, mock_get_meta):
        img = images.Image.from_url('http://example.com', '1abab')
        img.size = 1234
        img.file_version = 4
        img.persist()
        mock_db_persist.called_with(
            'f0e6a6a97042a4f1f1c87f5f7d44315b2d852c2df5c7991cc66241bf7072d1c4',
            'sf-245',
            {
                'url': 'http://example.com',
                'checksum': '1abab',
                'size': 1234,
                'modified': None,
                'fetched': None,
                'file_version': 4,
                'version': 1,
            })

    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    def test_version_image_path(self, mock_mkdirs, mock_get_meta):
        img = images.Image.from_url('http://some.com')
        img.file_version = 1
        self.assertEqual('/a/b/c/image_cache/bbf155338660b476435'
                         '06f35a6f92ebaef11e630b17d33da88b8638d267763f4.v001',
                         img.version_image_path())

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    @mock.patch('shakenfist.db.get_image_metadata',
                return_value={
                    'checksum': None,
                    'fetched': 'Tue, 20 Oct 2020 23:02:29 -0000',
                    'file_version': 1,
                    'modified': 'Fri, 06 Mar 2020 19:19:05 GMT',
                    'size': 16338944,
                    'url': 'http://example.com',
                    'version': 1
                })
    @mock.patch('os.makedirs')
    def test_requires_fetch_due_age(self, mock_mkdirs,
                                    mock_get_meta, mock_request_head):
        img = images.Image.from_url('http://example.com')
        dirty_fields = img._new_image_available(img._open_connection())
        self.assertEqual(1, img.file_version)
        self.assertEqual(('modified', 'Fri, 06 Mar 2020 19:19:05 GMT',
                          'Tue, 10 Sep 2019 07:24:40 GMT'),
                         dirty_fields)

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Fri, 06 Mar 2020 19:19:05 GMT',
                             'Content-Length': 200001}))
    @mock.patch('shakenfist.db.get_image_metadata',
                return_value={
                    'checksum': None,
                    'fetched': 'Tue, 20 Oct 2020 23:02:29 -0000',
                    'file_version': 1,
                    'modified': 'Fri, 06 Mar 2020 19:19:05 GMT',
                    'size': 16338944,
                    'url': 'http://example.com',
                    'version': 1
                })
    @mock.patch('os.makedirs')
    def test_requires_fetch_due_size(self, mock_mkdirs,
                                     mock_get_meta, mock_request_head):
        img = images.Image.from_url('http://example.com')
        dirty_fields = img._new_image_available(img._open_connection())
        self.assertEqual(1, img.file_version)
        self.assertEqual(('size', 16338944, 200001), dirty_fields)

    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    def test_fetch_image_new(self, mock_get,
                             mock_makedirs, mock_get_meta):
        img = images.Image.from_url('http://example.com')
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

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
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

    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 8 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_noop(self, mock_link, mock_identify, mock_exists,
                               mock_execute, mock_makedirs, mock_get_meta):
        img = images.Image.from_url('http://example.com')
        img.resize(None, 8)
        mock_link.assert_not_called()
        mock_execute.assert_not_called()

    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 8 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_link(self, mock_link, mock_identify, mock_exists,
                               mock_execute, mock_makedirs, mock_get_meta):
        img = images.Image.from_url('http://example.com')
        img.resize(None, 8)
        mock_link.assert_called_with(
            '/a/b/c/image_cache/f0e6a6a97042a4f1f1c87f5f7d4'
            '4315b2d852c2df5c7991cc66241bf7072d1c4.v000',
            '/a/b/c/image_cache/f0e6a6a97042a4f1f1c87f5f7d4'
            '4315b2d852c2df5c7991cc66241bf7072d1c4.v000.qcow2.8G')
        mock_execute.assert_not_called()

    @mock.patch('shakenfist.db.get_image_metadata', return_value=None)
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 4 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_resize(self, mock_link, mock_identify, mock_exists,
                                 mock_execute, mock_makedirs, mock_get_meta):
        img = images.Image.from_url('http://example.com')
        img.resize(None, 8)
        mock_link.assert_not_called()
        mock_execute.assert_has_calls(
            [mock.call(
                None,
                ('qemu-img create -b '
                 '/a/b/c/image_cache/f0e6a6a97042a4f1f1c87f5f7d4431'
                 '5b2d852c2df5c7991cc66241bf7072d1c4.v000.qcow2 '
                 '-f qcow2 /a/b/c/image_cache/f0e6a6a97042a4f1f1c87f'
                 '5f7d44315b2d852c2df5c7991cc66241bf7072d1c4.v000.qcow2.8G 8G')
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
