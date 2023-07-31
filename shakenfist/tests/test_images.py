from shakenfist import images
from shakenfist.tests import base


class ImageResolverTestCase(base.ShakenFistTestCase):
    def test_resolve_ubuntu_1604(self):
        self.assertEqual(
            'https://images.shakenfist.com/ubuntu:16.04/latest.qcow2',
            images._resolve_image('ubuntu:16.04'))

    def test_resolve_ubuntu_16804(self):
        self.assertEqual(
            'https://images.shakenfist.com/ubuntu:18.04/latest.qcow2',
            images._resolve_image('ubuntu:18.04'))

    def test_resolve_ubuntu_2004(self):
        self.assertEqual(
            'https://images.shakenfist.com/ubuntu:20.04/latest.qcow2',
            images._resolve_image('ubuntu:20.04'))

    def test_resolve_ubuntu_2204(self):
        self.assertEqual(
            'https://images.shakenfist.com/ubuntu:22.04/latest.qcow2',
            images._resolve_image('ubuntu:22.04'))
