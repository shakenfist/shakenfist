import mock
import six
import testtools


from shakenfist import exceptions
from shakenfist import images
from shakenfist import image_resolver_cirros
from shakenfist import image_resolver_ubuntu


QEMU_IMG_OUT = """foo
image: /tmp/foo
file format: qcow2
virtual size: 112M (117440512 bytes)
disk size: 16M
cluster_size: 65536
Format specific information:
    compat: 1.1
    lazy refcounts: false
    refcount bits: 16
    corrupt: false"""


CIRROS_DOWNLOAD_HTML = """
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 3.2 Final//EN">
<html>
 <head>
  <title>Index of /</title>
 </head>
 <body>
<h1>Index of /</h1>
<pre>      <a href="?C=N;O=D">Name</a>                                               <a href="?C=M;O=A">Last modified</a>      <a href="?C=S;O=A">Size</a>  <a href="?C=D;O=A">Description</a><hr>      <a href="0.3.0/">0.3.0/</a>                                             2017-11-20 07:20    -
      <a href="0.3.1/">0.3.1/</a>                                             2017-11-20 07:18    -
      <a href="0.3.1~pre1/">0.3.1~pre1/</a>                                        2017-11-20 07:17    -
      <a href="0.3.1~pre3/">0.3.1~pre3/</a>                                        2017-11-20 07:20    -
      <a href="0.3.1~pre4/">0.3.1~pre4/</a>                                        2017-11-20 07:18    -
      <a href="0.3.2/">0.3.2/</a>                                             2017-11-20 07:18    -
      <a href="0.3.2~pre1/">0.3.2~pre1/</a>                                        2017-11-20 07:19    -
      <a href="0.3.2~pre2/">0.3.2~pre2/</a>                                        2017-11-20 07:18    -
      <a href="0.3.2~pre3/">0.3.2~pre3/</a>                                        2017-11-20 07:19    -
      <a href="0.3.3/">0.3.3/</a>                                             2017-11-20 07:18    -
      <a href="0.3.3~pre1/">0.3.3~pre1/</a>                                        2017-11-20 07:20    -
      <a href="0.3.4/">0.3.4/</a>                                             2017-11-20 07:19    -
      <a href="0.3.4~pre1/">0.3.4~pre1/</a>                                        2017-11-20 07:21    -
      <a href="0.3.5/">0.3.5/</a>                                             2017-11-20 07:19    -
      <a href="0.3.6/">0.3.6/</a>                                             2018-12-12 09:51    -
      <a href="0.4.0/">0.4.0/</a>                                             2017-11-19 20:01    -
      <a href="0.4.0~pre1/">0.4.0~pre1/</a>                                        2017-11-20 07:20    -
      <a href="0.5.0/">0.5.0/</a>                                             2020-03-04 07:08    -
      <a href="0.5.1/">0.5.1/</a>                                             2020-03-09 06:55    -
      <a href="contrib/">contrib/</a>                                           2020-02-03 07:25    -
      <a href="daily/">daily/</a>                                             2016-12-01 13:19    -
      <a href="favicon.gif">favicon.gif</a>                                        2012-10-16 12:53    0
      <a href="favicon.ico">favicon.ico</a>                                        2012-10-16 12:53    0
      <a href="old/">old/</a>                                               2017-11-20 09:02    -
      <a href="streams.old/">streams.old/</a>                                       2020-03-09 07:25    -
      <a href="streams/">streams/</a>                                           2020-03-09 07:17    -
      <a href="testing-dl/">testing-dl/</a>                                        2017-11-15 10:07    -
      <a href="version/">version/</a>                                           2020-03-09 06:56    -
<hr></pre>
</body></html>"""

UBUNTU_DOWNLOAD_HTML = """
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"
 "http://www.w3.org/TR/html4/strict.dtd">
<html>
 <head>
  <title>Ubuntu Cloud Images</title>
  <!-- Main style sheets for CSS2 capable browsers -->
  <style type="text/css" media="screen">
  @import url(https://cloud-images.ubuntu.com/include/style.css);
  pre { background: none; }
  body { margin: 2em; }
  table {
     margin: 0.5em 0;
     border-collapse: collapse;
  }
  td {
     padding: 0.25em;
     border: 1pt solid #C1B496; /* ubuntu dark tan */
  }
  td p {
     margin: 0;
     padding: 0;
  }
  </style>
 </head>
 <body><div id="pageWrapper">
<div id="header"><a href="http://www.ubuntu.com/"></a></div>

<div id="main">
<h1>Ubuntu Cloud Images</h1>

<p>Ubuntu Cloud Images are the official Ubuntu images and are pre-installed
disk images that have been customized by Ubuntu engineering to run on
<a href="http://www.ubuntu.com/cloud/public-cloud">public clouds that provide Ubuntu Certified Images</a>, Openstack,  LXD, and more.  </p>

<p>For more information, please see the following:</p>
<ul>
<li><a href="http://cloud.ubuntu.com">Ubuntu Cloud Portal</a></li>
<li><a href="http://www.ubuntu.com/business/services/cloud">Commercial Support Options</a></li>
<li><a href="https://help.ubuntu.com/community/UEC/Images">Community Help Page</a></li>
</ul>
Cloud image specific bugs should be filed in the <a class="http" href="https://bugs.launchpad.net/cloud-images/+filebug">cloud-images</a> project on Launchpad.net.

<pre><img src="/icons/blank.gif" alt="Icon " width="22" height="22"> <a href="?C=N;O=D">Name</a>                    <a href="?C=M;O=A">Last modified</a>      <a href="?C=S;O=A">Size</a>  <a href="?C=D;O=A">Description</a><hr><img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="bionic/">bionic/</a>                 06-Jun-2020 02:58    -   Ubuntu Server 18.04 LTS (Bionic Beaver) daily builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="daily/">daily/</a>                  24-Feb-2016 21:07    -   Daily image builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="docs/">docs/</a>                   14-Jun-2018 15:01    -
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="eoan/">eoan/</a>                   06-Jun-2020 02:58    -   Ubuntu Server 19.10 (Eoan Ermine) daily builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="focal/">focal/</a>                  06-Jun-2020 02:58    -   Ubuntu Server 20.04 LTS (Focal Fossa) daily builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="groovy/">groovy/</a>                 06-Jun-2020 02:58    -   Ubuntu Server 20.10 (Groovy Gorilla) daily builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="locator/">locator/</a>                06-Jun-2020 03:36    -   Image Locator
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="minimal/">minimal/</a>                09-Jul-2018 09:32    -   Ubuntu Server minimized image builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="precise/">precise/</a>                03-May-2017 02:58    -   Ubuntu Server 12.04 LTS (Precise Pangolin) daily builds [END OF LIFE - for reference only]
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="releases/">releases/</a>               16-Apr-2020 13:52    -   Release image builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="server/">server/</a>                 06-Jun-2020 03:36    -   Ubuntu Server Cloud Image Builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="trusty/">trusty/</a>                 11-Nov-2019 13:16    -   Ubuntu Server 14.04 LTS (Trusty Tahr) daily builds
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="vagrant/">vagrant/</a>                25-Jan-2017 14:48    -   Vagrant images
<img src="../../../../cdicons/folder.png" alt="[DIR]" width="22" height="22"> <a href="xenial/">xenial/</a>                 06-Jun-2020 02:58    -   Ubuntu Server 16.04 LTS (Xenial Xerus) daily builds
<hr></pre>
</div></div></body></html>"""


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


class ImagesTestCase(testtools.TestCase):
    @mock.patch('shakenfist.image_resolver_cirros.resolve',
                return_value='!!!cirros!!!')
    @mock.patch('shakenfist.image_resolver_ubuntu.resolve',
                return_value='!!!ubuntu!!!')
    def test_resolve_image(self, mock_ubuntu, mock_centos):
        self.assertEqual('win10', images.resolve('win10'))
        self.assertEqual('http://example.com/image',
                         images.resolve('http://example.com/image'))
        self.assertEqual('!!!cirros!!!',
                         images.resolve('cirros'))
        self.assertEqual('!!!ubuntu!!!',
                         images.resolve('ubuntu'))

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

    def test_hash_image_url(self):
        h = images._hash_image_url('http://example.com')
        self.assertEqual('f0e6a6a97042a4f1f1c87f5f7d44315b2d'
                         '852c2df5c7991cc66241bf7072d1c4', h)

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    @mock.patch('shakenfist.images._read_info',
                return_value={
                    'url': 'http://example.com',
                    'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                    'Content-Length': 200000,
                    'version': 0
                })
    @mock.patch('os.makedirs')
    def test_does_not_require_fetch(self, mock_mkdirs, mock_read_info, mock_request_head):
        _, info, image_dirty, _ = images.requires_fetch('http://example.com')
        self.assertEqual(0, info['version'])
        self.assertEqual(False, image_dirty)

    @mock.patch('requests.get',
                return_value=FakeResponse(
                    200, '',
                    headers={'Last-Modified': 'Tue, 10 Sep 2019 07:24:40 GMT',
                             'Content-Length': 200000}))
    @mock.patch('shakenfist.images._read_info',
                return_value={
                    'url': 'http://example.com',
                    'Last-Modified': 'Tue, 10 Sep 2017 07:24:40 GMT',
                    'Content-Length': 200000,
                    'version': 0
                })
    @mock.patch('os.makedirs')
    def test_requires_fetch(self, mock_mkdirs, mock_read_info, mock_request_head):
        _, info, image_dirty, _ = images.requires_fetch('http://example.com')
        self.assertEqual(0, info['version'])
        self.assertEqual(True, image_dirty)

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
        _, _, image_dirty, _ = images.requires_fetch('http://example.com')
        self.assertEqual(True, image_dirty)

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
            _, _, image_dirty, _ = images.requires_fetch('http://example.com')
        self.assertEqual(False, image_dirty)

    @mock.patch('shakenfist.config.parsed.get', return_value='/a/b/c')
    @mock.patch('os.path.exists', side_effect=[True, True, False])
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.images._read_info',
                return_value={
                    'Last-Modified': 'Tue, 10 Sep 2018 07:24:40 GMT',
                    'Content-Length': 100000,
                    'version': 0})
    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    def test_fetch_image_changed(self, mock_execute, mock_read_info, mock_makedirs,
                                 mock_exists, mock_config):
        _, _, image_dirty, _ = images.requires_fetch('http://example.com')
        self.assertEqual(True, image_dirty)

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=True)
    def test_transcode_image_noop(self, mock_exists, mock_execute):
        images.transcode('/a/b/c/hash')
        mock_execute.assert_not_called()

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'file format': 'qcow2'})
    @mock.patch('os.link')
    def test_transcode_image_link(self, mock_link, mock_identify, mock_exists,
                                  mock_execute):
        images.transcode('/a/b/c/hash')
        mock_link.assert_called_with('/a/b/c/hash', '/a/b/c/hash.qcow2')
        mock_execute.assert_not_called()

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'file format': 'raw'})
    @mock.patch('os.link')
    def test_transcode_image_convert(self, mock_link, mock_identify, mock_exists,
                                     mock_execute):
        images.transcode('/a/b/c/hash')
        mock_link.assert_not_called()
        mock_execute.assert_called_with(
            'qemu-img convert -t none -O qcow2 /a/b/c/hash /a/b/c/hash.qcow2',
            shell=True
        )

    @mock.patch('shutil.copyfile')
    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 8 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_noop(self, mock_link, mock_identify, mock_exists,
                               mock_execute, mock_copyfile):
        images.resize('/a/b/c/hash', 8)
        mock_link.assert_not_called()
        mock_execute.assert_not_called()
        mock_copyfile.assert_not_called()

    @mock.patch('shutil.copyfile')
    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 8 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_link(self, mock_link, mock_identify, mock_exists,
                               mock_execute, mock_copyfile):
        images.resize('/a/b/c/hash', 8)
        mock_link.assert_called_with('/a/b/c/hash', '/a/b/c/hash.qcow2.8G')
        mock_execute.assert_not_called()
        mock_copyfile.assert_not_called()

    @mock.patch('shutil.copyfile')
    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    @mock.patch('shakenfist.images.identify',
                return_value={'virtual size': 4 * 1024 * 1024 * 1024})
    @mock.patch('os.link')
    def test_resize_image_resize(self, mock_link, mock_identify, mock_exists,
                                 mock_execute, mock_copyfile):
        images.resize('/a/b/c/hash', 8)
        mock_link.assert_not_called()
        mock_execute.assert_called_with(
            'qemu-img resize /a/b/c/hash.qcow2.8G 8G', shell=True)
        mock_copyfile.assert_called_with(
            '/a/b/c/hash.qcow2', '/a/b/c/hash.qcow2.8G'
        )

    @mock.patch('oslo_concurrency.processutils.execute',
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

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    @mock.patch('os.path.exists', return_value=False)
    def test_create_cow(self, mock_exists, mock_execute):
        images.create_cow('/a/b/c/base', '/a/b/c/cow')
        mock_execute.assert_called_with(
            'qemu-img create -b /a/b/c/base -f qcow2 /a/b/c/cow', shell=True)

    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(None, None))
    def test_snapshot(self, mock_execute):
        images.snapshot('/a/b/c/base', '/a/b/c/snap')
        mock_execute.assert_called_with(
            'qemu-img convert --force-share -O qcow2 /a/b/c/base /a/b/c/snap',
            shell=True)
