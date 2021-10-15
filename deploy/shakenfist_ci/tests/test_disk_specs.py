import base64
import os

from shakenfist_ci import base


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
with open('%s/files/localaccount_userdata' % TEST_DIR) as f:
    DEFAULT_USER = str(base64.b64encode(f.read().encode('utf-8'), 'utf-8'))


class TestDiskSpecifications(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'diskspecs'
        super(TestDiskSpecifications, self).__init__(*args, **kwargs)

    def test_default(self):
        inst = self.test_client.create_instance(
            'virtio', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'ubuntu:20.04',
                    'type': 'disk'
                }
            ], None, DEFAULT_USER)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute(
            'ls -l /dev/disk/by-path/virtio-pci-0000:00:07.0-part1')
        if out.find('vda1') == -1:
            self.fail('Disk is not virtio!\n\n%s' % out)

    def test_ide(self):
        inst = self.test_client.create_instance(
            'ide', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'ubuntu:20.04',
                    'type': 'disk',
                    'bus': 'ide'
                }
            ], None, DEFAULT_USER)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute(
            'ls -l /dev/disk/by-path/virtio-pci-0000:00:07.0-part1')
        if out.find('sda1') == -1:
            self.fail('Disk is not IDE!\n\n%s' % out)

    def test_sata(self):
        inst = self.test_client.create_instance(
            'sata', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'ubuntu:20.04',
                    'type': 'disk',
                    'bus': 'sata'
                }
            ], None, DEFAULT_USER)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute('ls -l /dev/disk/by-path/')
        if out.find('banana') == -1:
            self.fail('Disk is not SATA!\n\n%s' % out)

    def test_usb(self):
        self.skip('None of the cloud distros support usb boot disks')
        inst = self.test_client.create_instance(
            'usb', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'ubuntu:20.04',
                    'type': 'disk',
                    'bus': 'usb'
                }
            ], None, DEFAULT_USER)

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)
        out = console.execute('lsusb')
        if out.find('sda') == -1:
            self.fail('Disk is not SATA!\n\n%s' % out)

    def test_old_style_disk_size(self):
        inst = self.test_client.create_instance(
            'cirros', 1, 1024, None,
            [
                {
                    'size': '8',
                    'base': 'cirros',
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

        console = base.LoggingSocket(self.test_client, inst)

        # Boot disk
        out = console.execute('dmesg | grep sda')
        if out.find('[sda] 16777216 512-byte logical blocks: (8.59 GB/8.00 GiB)') == -1:
            self.fail('sda config is incorrect\n\n%s' % out)

        # config drive
        out = console.execute('dmesg | grep sdb')
        if out.find('[sdb] Attached SCSI disk'):
            self.fail('sdb config is incorrect\n\n%s' % out)

        # 16gb empty data disk
        out = console.execute('dmesg | grep vda')
        if out.find('[vda] 33554432 512-byte logical blocks (17.2 GB/16.0 GiB)') == -1:
            self.fail('vda config is incorrect\n\n%s' % out)

        # ISO as CDROM
        out = console.execute('dmesg | grep sr0')
        if out.find('Attached scsi CD-ROM sr0') == -1:
            self.fail('sr0 config is incorrect\n\n%s' % out)

        out = console.execute('sudo mount /dev/sr0 /mnt; ls /mnt')
        if out.find('isolinux.bin') == -1:
            self.fail('sr0 did not mount correctly\n\n%s' % out)
