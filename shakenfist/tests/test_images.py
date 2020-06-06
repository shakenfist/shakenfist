import mock
import testtools


from shakenfist import images


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


class ImagesTestCase(testtools.TestCase):
    @mock.patch('oslo_concurrency.processutils.execute',
                return_value=(QEMU_IMG_OUT, None))
    @mock.patch('os.path.exists', return_value=True)
    def test_identify_image(self, mock_exists, mock_execute):
        d = images.identify_image('/tmp/foo')
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
