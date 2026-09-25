import json

from testtools import content

from shakenfist_ci import base


class TestUEFI(base.BaseNamespacedTestCase):
    """UEFI instances boot on every hypervisor distribution we deploy to.

    Nothing in the suite booted a UEFI instance until issue 4329, which is
    how hard-coded 2 MB OVMF paths broke every UEFI instance on Debian 13
    and Ubuntu 24.04 hypervisors without CI noticing: it was found by
    kerbside's end to end lane instead. The CI image has an EFI system
    partition, so the same image serves both boot modes.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'uefi'
        super().__init__(*args, **kwargs)

    def _create(self, name, secure_boot):
        inst = self.create_instance(
            name, 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None, uefi=True, secure_boot=secure_boot)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self.assertIsNotNone(inst['uuid'])
        return inst

    def test_uefi_boot(self):
        inst = self._create('test-uefi', False)
        self.assertTrue(inst['uefi'])
        self._await_instance_ready(inst['uuid'])

        # The guest must actually have come up under EFI firmware, not
        # merely been created with a UEFI flag.
        results = self._await_command(
            inst['uuid'], 'test -d /sys/firmware/efi && echo efi')
        self.addDetail(
            'results',
            content.text_content(json.dumps(results, indent=4, sort_keys=True)))
        self.assertEqual(0, results['return-code'])
        self.assertIn('efi', results['stdout'])

    def test_uefi_secure_boot_firmware_starts(self):
        # This exercises the secure boot code and VARS pair, which is a
        # separate branch of both the firmware selection and the domain
        # template. Whether the guest then boots depends on its bootloader
        # being signed, which is a property of the image rather than of
        # Shaken Fist, so this only asserts the domain starts.
        inst = self._create('test-uefi-secureboot', True)
        self._await_instance_create(inst['uuid'])

        i = self.system_client.get_instance(inst['uuid'])
        self.assertEqual('created', i['state'])
        self.assertTrue(i['secure_boot'])
        self.assertEqual('q35', i['machine_type'])
