from shakenfist_ci import base


class TestDiskSpecifications(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'diskspecs'
        super(TestDiskSpecifications, self).__init__(*args, **kwargs)

    def test_default(self):
        inst = self.test_client.create_instance(
            'test-default-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute('df -h')
        if not out.find('vda'):
            self.fail('Disk is not virtio!\n\n%s' % out)

    def test_ide(self):
        # Booting an IDE based VM is incredibly slow, even on real hardware.
        # In my tests on a physical cluster it was taking ten minutes to finish
        # the initrd stage. We need to be patient with this test.
        inst = self.test_client.create_instance(
            'test-ide-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk',
                    'bus': 'ide'
                }
            ], None, None, side_channels=['sf-agent'])

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'], timeout=15)

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute('df -h')
        if not out.find('sda'):
            self.fail('Disk is not IDE!\n\n%s' % out)

    def test_old_style_disk_size(self):
        inst = self.test_client.create_instance(
            'test-old-style-disk', 1, 1024, None,
            [
                {
                    'size': '8',
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])
        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

    def test_complex(self):
        # Booting an IDE based VM is incredibly slow, even on real hardware.
        # In my tests on a physical cluster it was taking ten minutes to finish
        # the initrd stage. We need to be patient with this test.
        inst = self.test_client.create_instance(
            'test-complex-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk',
                    'bus': 'ide'
                },
                {
                    'size': 16,
                    'type': 'disk'
                },
                {
                    'base': ('https://sfcbr.shakenfist.com/focal-mini.iso')
                }
            ], None, None, side_channels=['sf-agent'])

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'], timeout=15)

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
