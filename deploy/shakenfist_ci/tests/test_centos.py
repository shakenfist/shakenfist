from shakenfist_ci import base


class TestCentos(base.TestDistroBoots):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'centos'
        super(TestCentos, self).__init__(*args, **kwargs)

    def test_centos_6_pings(self):
        self._test_distro_boot('centos:6')

    def test_centos_7_pings(self):
        self._test_distro_boot('centos:7')

    def test_centos_8_pings(self):
        self._test_distro_boot('centos:8')
