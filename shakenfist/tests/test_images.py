import mock
import os
import six
import testtools


from shakenfist import exceptions
from shakenfist import images
from shakenfist import image_resolver_cirros
from shakenfist import image_resolver_ubuntu


TEST_DIR = os.path.dirname(os.path.abspath(__file__))


with open('%s/files/qemu-img-info' % TEST_DIR) as f:
    QEMU_IMG_OUT = f.read()

with open('%s/files/cirros-download' % TEST_DIR) as f:
    CIRROS_DOWNLOAD_HTML = f.read()

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


class ImageUtilsTestCase(testtools.TestCase):
    @mock.patch('shakenfist.config.parsed.get', return_value='/a/b/c')
    @mock.patch('os.path.exists', return_value=True)
    def test_get_cache_path(self, mock_exists, mock_config):
        p = images._get_cache_path()
        mock_exists.assert_called_with('/a/b/c/image_cache')
        self.assertEqual('/a/b/c/image_cache', p)

    @mock.patch('shakenfist.config.parsed.get', return_value='/a/b/c')
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('os.makedirs')
    def test_get_cache_path_create(self, mock_makedirs, mock_exists, mock_config):
        p = images._get_cache_path()
        mock_exists.assert_called_with('/a/b/c/image_cache')
        mock_makedirs.assert_called_with('/a/b/c/image_cache')
        self.assertEqual('/a/b/c/image_cache', p)


class ImageResolversTestCase(testtools.TestCase):
    @mock.patch('requests.get', return_value=FakeResponse(200, CIRROS_DOWNLOAD_HTML))
    def test_resolve_cirros(self, mock_get):
        u = image_resolver_cirros.resolve('cirros')
        self.assertEqual(
            'http://download.cirros-cloud.net/0.5.1/cirros-0.5.1-x86_64-disk.img', u)

        u = image_resolver_cirros.resolve('cirros:0.3.4')
        self.assertEqual(
            'http://download.cirros-cloud.net/0.3.4/cirros-0.3.4-x86_64-disk.img', u)

        self.assertRaises(exceptions.VersionSpecificationError,
                          image_resolver_cirros.resolve, 'cirros***')

    @mock.patch('requests.get', return_value=FakeResponse(404, None))
    def test_resolve_cirros_error(self, mock_get):
        self.assertRaises(exceptions.HTTPError,
                          image_resolver_cirros.resolve, 'cirros')

    @mock.patch('requests.get', return_value=FakeResponse(200, UBUNTU_DOWNLOAD_HTML))
    @mock.patch('shakenfist.image_resolver_ubuntu.UBUNTU_URL',
                'https://cloud-images.ubuntu.com')
    def test_resolve_ubuntu(self, mock_get):
        u = image_resolver_ubuntu.resolve('ubuntu')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/groovy/current/'
             'groovy-server-cloudimg-amd64.img'),
            u)

        u = image_resolver_ubuntu.resolve('ubuntu:bionic')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/bionic/current/'
             'bionic-server-cloudimg-amd64.img'),
            u)

        u = image_resolver_ubuntu.resolve('ubuntu:18.04')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/bionic/current/'
             'bionic-server-cloudimg-amd64.img'),
            u)

        self.assertRaises(exceptions.VersionSpecificationError,
                          image_resolver_ubuntu.resolve, 'ubuntu***')

    @mock.patch('requests.get', return_value=FakeResponse(404, None))
    def test_resolve_ubuntu_error(self, mock_get):
        self.assertRaises(exceptions.HTTPError,
                          image_resolver_ubuntu.resolve, 'ubuntu')


class ImageObjectTestCase(testtools.TestCase):
    @mock.patch('shakenfist.image_resolver_cirros.resolve',
                return_value='!!!cirros!!!')
    @mock.patch('shakenfist.image_resolver_ubuntu.resolve',
                return_value='!!!ubuntu!!!')
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('os.makedirs')
    def test_resolve_image(self, mock_mkdirs, mock_exists, mock_ubuntu, mock_centos):
        img = images.Image('win10')
        self.assertEqual('win10', img.url)

        img = images.Image('http://example.com/image')
        self.assertEqual('http://example.com/image', img.url)

        img = images.Image('cirros')
        self.assertEqual('!!!cirros!!!', img.url)

        img = images.Image('ubuntu')
        self.assertEqual('!!!ubuntu!!!', img.url)

    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('os.makedirs')
    def test_hash_image(self, mock_mkdirs, mock_exists):
        img = images.Image('http://example.com')
        self.assertEqual('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                         '852c2df5c7991cc66241bf7072d1c4', img.hashed_image_url)

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    @mock.patch('shakenfist.images.Image._read_local_info',
                return_value={
                    'url': 'http://example.com',
                    'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                    'Content-Length': 200000,
                    'version': 0
                })
    @mock.patch('os.makedirs')
    def test_does_not_require_fetch(self, mock_mkdirs, mock_read_local_info, mock_request_head):
        img = images.Image('http://example.com')
        dirty_fields, _ = img._requires_fetch()
        self.assertEqual(0, img.info['version'])
        self.assertEqual({}, dirty_fields)

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    @mock.patch('shakenfist.images.Image._read_local_info',
                return_value={
                    'url': 'http://example.com',
                    'Last-Modified': 'Tue, 10 Sep 2017 07:24:40 GMT',
                    'Content-Length': 200000,
                    'version': 0
                })
    @mock.patch('os.makedirs')
    def test_requires_fetch(self, mock_mkdirs, mock_read_local_info, mock_request_head):
        img = images.Image('http://example.com')
        dirty_fields, _ = img._requires_fetch()
        self.assertEqual(0, img.info['version'])
        self.assertEqual({
            'Last-Modified': {'after': 'Tue, 10 Sep 2019 07:24:40 GMT',
                              'before': 'Tue, 10 Sep 2017 07:24:40 GMT'}
        }, dirty_fields)

    @mock.patch('shakenfist.config.parsed.get', return_value='/a/b/c')
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('os.makedirs')
    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    def test_fetch_image_new(self, mock_get, mock_makedirs,
                             mock_exists, mock_config):
        img = images.Image('http://example.com')
        dirty_fields, _ = img._requires_fetch()
        self.assertEqual({
            'Content-Length': {'after': 200000, 'before': None},
            'Last-Modified': {'after': 'Tue, 10 Sep 2019 07:24:40 GMT', 'before': None}
        }, dirty_fields)

    @mock.patch('shakenfist.config.parsed.get', return_value='/a/b/c')
    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('os.makedirs')
    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    @mock.patch('json.loads', return_value={
        'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
        'Content-Length': 200000,
        'version': 0})
    def test_fetch_image_old(self, mock_loads, mock_get, mock_makedirs,
                             mock_exists, mock_config):
        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open',
                               new=mock_open):
            img = images.Image('http://example.com')
            dirty_fields, _ = img._requires_fetch()
        self.assertEqual({}, dirty_fields)

    @mock.patch('shakenfist.config.parsed.get', return_value='/a/b/c')
    @mock.patch('os.path.exists', side_effect=[True, True, False])
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.images.Image._read_local_info',
                return_value={
                    'Last-Modified': 'Tue, 10 Sep 2018 07:24:40 GMT',
                    'Content-Length': 100000,
                    'version': 0})
    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    def test_fetch_image_changed(self, mock_execute, mock_read_local_info, mock_makedirs,
                                 mock_exists, mock_config):
        img = images.Image('http://example.com')
        image_dirty, _ = img._requires_fetch()
        self.assertEqual({
            'Content-Length': {'after': '648', 'before': 100000},
            'Last-Modified': {'after': 'Thu, 17 Oct 2019 07:18:26 GMT',
                              'before': 'Tue, 10 Sep 2018 07:24:40 GMT'}
        }, image_dirty)

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.db.get_lock')
    @mock.patch('shakenfist.db.add_event')
    def test_transcode_image_noop(self, mock_event, mock_lock, mock_exists, mock_execute):
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
    def test_transcode_image_link(self, mock_event, mock_lock, mock_link, mock_identify, mock_exists,
                                  mock_execute):
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
    def test_transcode_image_convert(self, mock_event, mock_lock, mock_link, mock_identify, mock_exists,
                                     mock_execute):
        images._transcode(None, '/a/b/c/hash', FakeImage())
        mock_link.assert_not_called()
        mock_execute.assert_called_with(
            None, 'qemu-img convert -t none -O qcow2 /a/b/c/hash /a/b/c/hash.qcow2')

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 8 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_noop(self, mock_link, mock_identify, mock_exists,
                               mock_execute):
        images.resize(None, '/a/b/c/hash', 8)
        mock_link.assert_not_called()
        mock_execute.assert_not_called()

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 8 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_link(self, mock_link, mock_identify, mock_exists,
                               mock_execute):
        images.resize(None, '/a/b/c/hash', 8)
        mock_link.assert_called_with('/a/b/c/hash', '/a/b/c/hash.qcow2.8G')
        mock_execute.assert_not_called()

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 4 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_resize(self, mock_link, mock_identify, mock_exists,
                                 mock_execute):
        images.resize(None, '/a/b/c/hash', 8)
        mock_link.assert_not_called()
        mock_execute.assert_has_calls(
            [mock.call(None, 'cp /a/b/c/hash.qcow2 /a/b/c/hash.qcow2.8G'),
             mock.call(None, 'qemu-img resize /a/b/c/hash.qcow2.8G 8G')]
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
        images.create_cow(None, '/a/b/c/base', '/a/b/c/cow')
        mock_execute.assert_called_with(
            None, 'qemu-img create -b /a/b/c/base -f qcow2 /a/b/c/cow')

    @mock.patch('shakenfist.util.execute',
                return_value=(None, None))
    def test_snapshot(self, mock_execute):
        images.snapshot(None, '/a/b/c/base', '/a/b/c/snap')
        mock_execute.assert_called_with(
            None, 'qemu-img convert --force-share -O qcow2 /a/b/c/base /a/b/c/snap')
