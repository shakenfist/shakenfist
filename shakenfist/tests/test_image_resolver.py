import mock
import os
from pydantic import AnyHttpUrl


from shakenfist import exceptions
from shakenfist import image_resolver
from shakenfist.image_resolver import cirros
from shakenfist.image_resolver import ubuntu
from shakenfist import logutil
from shakenfist.tests import test_shakenfist
from shakenfist.config import BaseSettings


LOG, _ = logutil.setup(__name__)
TEST_DIR = os.path.dirname(os.path.abspath(__file__))


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


class FakeConfig(BaseSettings):
    STORAGE_PATH: str = '/a/b/c'
    NODE_NAME: str = 'sf-245'
    DOWNLOAD_URL_CIRROS: AnyHttpUrl = (
        'http://download.cirros-cloud.net/%(vernum)s/'
        'cirros-%(vernum)s-x86_64-disk.img')

    DOWNLOAD_URL_UBUNTU: AnyHttpUrl = (
        'https://cloud-images.ubuntu.com/%(vername)s/current/'
        '%(vername)s-server-cloudimg-amd64.img')
    LISTING_URL_UBUNTU: AnyHttpUrl = (
        'https://cloud-images.ubuntu.com/')


fake_config = FakeConfig()


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


class ImageResolversTestCase(test_shakenfist.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        fake_config = FakeConfig()

        self.config = mock.patch('shakenfist.config.config', fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    @mock.patch('requests.get', side_effect=[
        FakeResponse(200, CIRROS_DOWNLOAD_HTML),
        FakeResponse(404, ''),  # Handle no file available
        FakeResponse(200, CIRROS_MD5SUM_0_3_4),
    ])
    def test_resolve_cirros(self, mock_get):
        u = cirros.resolve('cirros')
        self.assertEqual(
            ('http://download.cirros-cloud.net/0.5.1/cirros-0.5.1-x86_64-disk.img',
             None),
            u)

        u = cirros.resolve('cirros:0.3.4')
        self.assertEqual(
            ('http://download.cirros-cloud.net/0.3.4/cirros-0.3.4-x86_64-disk.img',
             'ee1eca47dc88f4879d8a229cc70a07c6'),
            u)

        self.assertRaises(exceptions.VersionSpecificationError,
                          cirros.resolve, 'cirros***')

    @mock.patch('requests.get', return_value=FakeResponse(404, None))
    def test_resolve_cirros_error(self, mock_get):
        self.assertRaises(exceptions.HTTPError,
                          cirros.resolve, 'cirros')

    @mock.patch('requests.get', side_effect=[
        FakeResponse(200, UBUNTU_DOWNLOAD_HTML),
        FakeResponse(200, UBUNTU_MD5SUM_GROOVY),
        FakeResponse(200, UBUNTU_DOWNLOAD_HTML),
        FakeResponse(200, UBUNTU_MD5SUM_BIONIC),
        FakeResponse(200, UBUNTU_DOWNLOAD_HTML),
        FakeResponse(200, UBUNTU_MD5SUM_BIONIC),
        FakeResponse(200, UBUNTU_DOWNLOAD_HTML),
    ])
    def test_resolve_ubuntu(self, mock_get):
        u = ubuntu.resolve('ubuntu')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/groovy/current/'
             'groovy-server-cloudimg-amd64.img',
             '1c19b08060b9feb1cd0e7ee28fd463fb'),
            u)

        u = ubuntu.resolve('ubuntu:bionic')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/bionic/current/'
             'bionic-server-cloudimg-amd64.img',
             'ed44b9745b8d62bcbbc180b5f36c24bb'),
            u)

        u = ubuntu.resolve('ubuntu:18.04')
        self.assertEqual(
            ('https://cloud-images.ubuntu.com/bionic/current/'
             'bionic-server-cloudimg-amd64.img',
             'ed44b9745b8d62bcbbc180b5f36c24bb'),
            u)

        self.assertRaises(exceptions.VersionSpecificationError,
                          ubuntu.resolve, 'ubuntu***')

    @mock.patch('requests.get', return_value=FakeResponse(404, None))
    def test_resolve_ubuntu_error(self, mock_get):
        self.assertRaises(exceptions.HTTPError,
                          ubuntu.resolve, 'ubuntu')

    @mock.patch('shakenfist.image_resolver.cirros.resolve',
                return_value=('!!!cirros!!!', '123abc'))
    @mock.patch('shakenfist.image_resolver.ubuntu.resolve',
                return_value=('!!!ubuntu!!!', '123abc'))
    def test_resolve_image(self, mock_ubuntu, mock_centos):
        self.assertEqual(('win10', None),
                         image_resolver.resolve('win10'))
        self.assertEqual(('http://example.com/image', None),
                         image_resolver.resolve('http://example.com/image'))
        self.assertEqual(('!!!cirros!!!', '123abc'),
                         image_resolver.resolve('cirros'))
        self.assertEqual(('!!!ubuntu!!!', '123abc'),
                         image_resolver.resolve('ubuntu'))
