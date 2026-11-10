import json
from xml.etree import ElementTree

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

    def _create(self, name, secure_boot, bus=None):
        disk = {
            'size': 8,
            'base': base.CLUSTER_CI_IMAGE,
            'type': 'disk'
        }
        if bus:
            disk['bus'] = bus

        inst = self.create_instance(
            name, 1, 1024, None, [disk], None, None, uefi=True,
            secure_boot=secure_boot)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self.assertIsNotNone(inst['uuid'])
        return inst

    def _assert_booted_under_efi(self, instance_uuid):
        # The guest must actually have come up under EFI firmware, not
        # merely been created with a UEFI flag.
        results = self._await_command(
            instance_uuid, 'test -d /sys/firmware/efi && echo efi')
        self.addDetail(
            'results',
            content.text_content(json.dumps(results, indent=4, sort_keys=True)))
        self.assertEqual(0, results['return-code'])
        self.assertIn('efi', results['stdout'])

    def test_uefi_boot(self):
        inst = self._create('test-uefi', False)
        self.assertTrue(inst['uefi'])
        self._await_instance_ready(inst['uuid'])
        self._assert_booted_under_efi(inst['uuid'])

    def test_uefi_boot_from_scsi(self):
        # Issue 4579: a UEFI instance with a bus=scsi disk got libvirt's
        # default LSI Logic controller, which OVMF has no driver for, so the
        # firmware found no disk, tried the network, and dropped to the EFI
        # shell. Nothing in the suite booted a SCSI disk at all, under either
        # firmware. The agent coming up is the assertion: it cannot answer
        # unless the guest booted off that disk.
        inst = self._create('test-uefi-scsi', False, bus='scsi')
        self._await_instance_ready(inst['uuid'])
        self._assert_booted_under_efi(inst['uuid'])

        # And the reason it booted, so a regression here says which half
        # broke: the controller is in the domain XML, not merely implied by a
        # guest which came up.
        # Parse rather than match the template's text: the newest event is
        # usually libvirt's own rewrite of the domain, which gives the
        # controller an index and a PCI address and so never contains the
        # line libvirt.tmpl wrote.
        xml = self._await_domain_xml(inst['uuid'])
        controllers = ElementTree.fromstring(xml).findall(
            "./devices/controller[@type='scsi'][@model='virtio-scsi']")
        self.assertNotEqual(
            0, len(controllers),
            'the domain XML this hypervisor recorded declares no virtio-scsi '
            'controller, so libvirt chose its LSI Logic default (issue 4579). '
            'The most likely cause is a hypervisor running an older copy of '
            'libvirt.tmpl.')

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
