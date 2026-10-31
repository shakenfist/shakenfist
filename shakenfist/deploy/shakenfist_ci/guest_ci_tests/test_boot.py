import json

from testtools import content

from shakenfist_ci import base


class TestBoot(base.BaseNamespacedTestCase):
    """Make sure instances boot under various configurations.

    Three plain tests on one image, deliberately. This class became a
    testscenarios list in August 2025, and the same change renamed its
    three test methods to _boot_*, which stestr does not collect: it has
    run nothing since. The list itself held two entries, debian-11 and
    debian-12, and the Debian 11 to 12 migration rewrote the first to
    debian-12 and left both in place, so even collected it would have
    booted one image twice under two test ids (issue 4400). The cluster
    image artifact is now release-neutral -- one upload, named debian --
    so there is no second image for a scenario to vary, and three test
    methods on CLUSTER_CI_IMAGE say what the class covers.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'boot'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_boot_no_network(self):
        """Check that instances without a network still boot.

        Once we had a bug that only stopped instance creation when no network
        was specified.
        """
        inst = self.create_instance(
            'test-boot-no-network', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        self._await_instance_ready(inst['uuid'])

    def test_boot_network(self):
        inst = self.create_instance(
            'test-boot-network', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        self._await_instance_ready(inst['uuid'])

    def test_boot_large_disk(self):
        inst = self.create_instance(
            'test-boot-large-disk', 1, 1024, None,
            [
                {
                    'size': 30,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

        self._await_instance_ready(inst['uuid'])
