from shakenfist_ci import base


class TestDiskSpecifications(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'diskspecs'
        super(TestDiskSpecifications, self).__init__(*args, **kwargs)

    def test_default(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(inst['node'], inst['console_port'])
        out = console.execute('df -h')
        if not out.find('vda'):
            self.fail('Disk is not virtio!\n\n%s' % out)

    def test_ide(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk',
                    'bus': 'ide'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(inst['node'], inst['console_port'])
        out = console.execute('df -h')
        if not out.find('hda'):
            self.fail('Disk is not IDE!\n\n%s' % out)

    def test_complex(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk',
                    'bus': 'ide'
                },
                {
                    'size': 16,
                    'type': 'disk'
                },
                {
                    'base': ('http://archive.ubuntu.com/ubuntu/dists/focal/main/'
                             'installer-amd64/current/legacy-images/netboot/mini.iso')
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(inst['node'], inst['console_port'])
        out = console.execute('df -h')
        if not out.find('hda'):
            self.fail('Disk is not IDE!\n\n%s' % out)
