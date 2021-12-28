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
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute('df -h')
        if not out.find('vda'):
            self.fail('Disk is not virtio!\n\n%s' % out)

    def test_ide(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk',
                    'bus': 'ide'
                }
            ], None, None)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute('df -h')
        if not out.find('sda'):
            self.fail('Disk is not IDE!\n\n%s' % out)

    def test_old_style_disk_size(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024, None,
            [
                {
                    'size': '8',
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk'
                }
            ], None, None)
        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

    def test_complex(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
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

        console = base.LoggingSocket(self.test_client, inst)

        # Boot disk
        out = console.execute('dmesg | grep sda')
        if not out.find('[sda] 16777216 512-byte logical blocks: (8.59 GB/8.00 GiB)'):
            self.fail('sda config is incorrect\n\n%s' % out)

        # config drive
        out = console.execute('dmesg | grep sdb')
        if not out.find('[sdb] Attached SCSI disk'):
            self.fail('sdb config is incorrect\n\n%s' % out)

        # 16gb empty data disk
        out = console.execute('dmesg | grep vda')
        if not out.find('[vda] 33554432 512-byte logical blocks (17.2 GB/16.0 GiB)'):
            self.fail('vda config is incorrect\n\n%s' % out)

        # ISO as CDROM
        out = console.execute('dmesg | grep sr0')
        if not out.find('Attached scsi CD-ROM sr0'):
            self.fail('sr0 config is incorrect\n\n%s' % out)

        out = console.execute('sudo mount /dev/sr0 /mnt; ls /mnt')
        if not out.find('isolinux.bin'):
            self.fail('sr0 did not mount correctly\n\n%s' % out)
