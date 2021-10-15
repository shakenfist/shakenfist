import base64
import os
import time

from shakenfist_ci import base


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
with open('%s/files/diskdiagnostics_userdata' % TEST_DIR) as f:
    DISK_DIAGNOSTICS = base64.b64encode(
        f.read().encode('utf-8')).decode('utf-8')


class TestDiskSpecifications(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'diskspecs'
        super(TestDiskSpecifications, self).__init__(*args, **kwargs)

    def _test_disk_bus(self, disk_bus, expected_device, fail_message):
        inst = self.test_client.create_instance(
            'virtio', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/cirros',
                    'type': 'disk',
                    'bus': disk_bus
                }
            ], None, DISK_DIAGNOSTICS)

        self.assertIsNotNone(inst['uuid'])
        self._await_instances_ready([inst['uuid']])
        time.sleep(60)

        console = self.test_client.get_console_data(inst['uuid'], 1000000)
        disk_diag = []
        for line in console.split('\n'):
            print(line)
            if line.startswith('[DISKDIAG '):
                disk_diag.append(line)
        disk_diag = '\n'.join(disk_diag)

        if not disk_diag:
            self.fail('Disk diagnostics failed to run')

        if disk_diag.find(expected_device) == -1:
            self.fail(fail_message + '\n\n' + disk_diag)

    def test_default(self):
        self._test_disk_bus(None, 'vda1', 'Disk is not virtio')

    def test_ide(self):
        self._test_disk_bus('ide', 'hda1', 'Disk is not virtio')

    def test_sata(self):
        self._test_disk_bus('sata', 'sda1', 'Disk is not SATA')

    def test_usb(self):
        self.skip('None of the cloud distros support usb boot disks')

    def test_old_style_disk_size(self):
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
            ], None, DISK_DIAGNOSTICS)

        self.assertIsNotNone(inst['uuid'])
        self._await_instances_ready([inst['uuid']])
        time.sleep(60)

        console = self.test_client.get_console_data(inst['uuid'], 1000000)
        disk_diag = []
        for line in console.split('\n'):
            if line.startswith('[DISKDIAG '):
                disk_diag.append(line)
        disk_diag = '    \n'.join(disk_diag)

        if not disk_diag:
            self.fail('Disk diagnostics failed to run')

        # Boot disk
        if console.find('[sda] 16777216 512-byte logical blocks: (8.59 GB/8.00 GiB)') == -1:
            self.fail('sda config is incorrect\n\n%s' % disk_diag)

        # config drive
        if console.find('[sdb] Attached SCSI disk') == -1:
            self.fail('sdb config is incorrect\n\n%s' % disk_diag)

        # 16gb empty data disk
        if console.find('[vda] 33554432 512-byte logical blocks (17.2 GB/16.0 GiB)') == -1:
            self.fail('vda config is incorrect\n\n%s' % disk_diag)

        # ISO as CDROM
        if console.find('Attached scsi CD-ROM sr0') == -1:
            self.fail('sr0 config is incorrect\n\n%s' % disk_diag)

    def test_complex_no_configdrive(self):
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
            ], None, None, configdrive='none')

        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])

        console = base.LoggingSocket(self.test_client, inst)

        # Boot disk
        out = console.execute('dmesg | grep sda')
        if not out.find('[sda] 16777216 512-byte logical blocks: (8.59 GB/8.00 GiB)'):
            self.fail('sda config is incorrect\n\n%s' % out)

        # config drive
        out = console.execute('dmesg | grep -c sdb')
        if int(out) != 0:
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
