from shakenfist_ci import base


class TestUbuntu(base.TestDistroBoots):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'ubuntu'
        super(TestUbuntu, self).__init__(*args, **kwargs)

    def test_ubuntu_1804_pings(self):
        self._test_distro_boot('ubuntu:18.04')

    def test_ubuntu_2004_pings(self):
        self._test_distro_boot('ubuntu:20.04')
