from shakenfist_ci import base


class TestDebian(base.TestDistroBoots):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'debian'
        super(TestDebian, self).__init__(*args, **kwargs)

    def test_debian_9_pings(self):
        self._test_distro_boot('debian:9')

    def test_debian_10_pings(self):
        self._test_distro_boot('debian:10')
