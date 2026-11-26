import json
import uuid

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestDiskSpecifications(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'diskspecs'
        super().__init__(*args, **kwargs)

    def test_default(self):
        inst = self.create_instance(
            'test-default-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        results = self._await_command(inst['uuid'], 'df -h')
        self.addDetail(
            'results',
            content.text_content(json.dumps(results, indent=4, sort_keys=True)))
        self.assertEqual(0, results['return-code'])
        self.assertEqual('', results['stderr'])
        self.assertTrue('vda' in results['stdout'])

    def test_unfetchable_base(self):
        """A base no fetch could resolve is refused at create time.

        Issue 4557: each of these used to be accepted, scheduled, and
        then failed on the hypervisor with a requests exception,
        leaving the instance in error with no error message at all.
        The bare uuid is the mistake which prompted the issue -- it is
        the one string every artifact listing shows -- so its refusal
        is also read, to check it names the form which would have
        worked. Whether the uuid names a real artifact does not matter
        to the guard, and a literal one keeps this test independent of
        what the cloud happens to hold.

        A unit test proves the handler answers 400. Only a cluster
        proves that the shipped client and the whole decorator stack
        in front of that handler do not turn it into something else
        first.
        """
        bare_uuid = str(uuid.uuid4())
        bad_bases = [
            'ova:///srv/x.ova/disk.vmdk',
            'sf://instance/%s/vda' % bare_uuid,
            'not-an-image',
            bare_uuid,
        ]

        for bad_base in bad_bases:
            try:
                # raw-create: asserts the 400 an unfetchable disk base
                # gets, which is raised validating the request long
                # before the scheduler runs.
                self.test_client.create_instance(
                    'test-unfetchable-base', 1, 1024, None,
                    [
                        {
                            'size': 8,
                            'base': bad_base,
                            'type': 'disk'
                        }
                    ], None, None)
                self.fail('Unfetchable disk base %s was accepted' % bad_base)
            except apiclient.RequestMalformedException as e:
                self.addDetail(
                    'response for %s' % bad_base,
                    content.text_content(str(e.text)))
                self.assertEqual(400, e.status_code)
                body = json.loads(e.text)
                self.assertIn('cannot be fetched', body['error'])

                if bad_base == bare_uuid:
                    self.assertIn('sf://artifact/%s' % bare_uuid,
                                  body['error'])

    def test_bad_bus(self):
        self.assertRaises(
            apiclient.RequestMalformedException,
            # raw-create: asserts the 400 a malformed disk bus gets, which is
            # raised validating the request long before the scheduler runs.
            self.test_client.create_instance,
            'test-bad-bus-disk', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk',
                    'bus': 'banana'
                }
            ], None, None)

    def test_requested_bus_and_type_reach_libvirt(self):
        """A disk spec's bus and type arrive at libvirt, not just at the API.

        Issue 5071: nothing in the suite looked at the domain at all. The
        API reports a disk spec back as it was asked for, and test_default
        above only reads the guest's ``df``, so a spec which asked for one
        bus and quietly got another -- or whose cache mode was dropped --
        booted and passed.

        Three requests are made in one domain: a root disk on a bus which
        is not the cluster default (sata rather than DISK_BUS's virtio), an
        extra disk on a third bus (scsi), and an extra device of the other
        device type (a cdrom). The cdrom asks for no bus on purpose: qemu
        has no removable media on virtio, so a defaulted cdrom is moved to
        usb when its block devices are configured, and that substitution
        is deliberate behaviour which should be visible here rather than
        look like the bug this test is for.

        Reaching 'created' is enough, and the agent is deliberately not
        waited for. The domain exists by then -- Instance.create() powers
        on before recording the state -- and waiting for the guest instead
        would make this test depend on the guest shipping a driver for
        every bus it is asked to boot from, which is a different claim and
        one the UEFI suite already makes for scsi.

        The target device names are asserted rather than discovered,
        because the naming scheme is part of the contract
        instance._get_disk_device() implements: the root disk is the first
        letter on the root disk's bus, the config drive takes the next
        letter on that same bus, and each extra disk takes the next letter
        again whatever its own bus is.
        """
        inst = self.create_instance(
            'test-disk-spec-libvirt', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk',
                    'bus': 'sata'
                },
                {
                    'size': 1,
                    'type': 'disk',
                    'bus': 'scsi'
                },
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'cdrom'
                }
            ], None, None, configdrive='openstack-disk')
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        self.assertIsNotNone(inst['uuid'])
        self.assertIsNotNone(inst['node'])
        self._await_instance_create(inst['uuid'])

        # Skip loudly, not silently, when the instance's hypervisor cannot
        # be reached -- see _require_node_exec()'s docstring.
        node = self._node_by_identifier(
            self._get_cluster_nodes(), inst['node'])
        self._require_node_exec(node)

        disks = self._domain_disks(node, inst['uuid'])
        self.addDetail(
            'disks',
            content.text_content(json.dumps(disks, indent=4, sort_keys=True)))

        # cache is constants.DISK_CACHE_MODE, which is not importable from
        # the suite. It is the same for every disk and is not settable per
        # request, so a dropped cache mode shows up on all four.
        self.assertEqual(
            {
                # The requested root disk, on the requested bus.
                'sda': {'device': 'disk', 'bus': 'sata',
                        'format': 'qcow2', 'cache': 'none'},
                # The config drive, which follows the root disk's bus.
                'sdb': {'device': 'disk', 'bus': 'sata',
                        'format': 'raw', 'cache': 'none'},
                # A blank extra disk on a bus of its own.
                'sdc': {'device': 'disk', 'bus': 'scsi',
                        'format': 'qcow2', 'cache': 'none'},
                # The cdrom: the other device type, moved off virtio to
                # usb, and a raw hard link into the image cache rather
                # than a copy on write layer.
                'sdd': {'device': 'cdrom', 'bus': 'usb',
                        'format': 'raw', 'cache': 'none'},
            }, disks)

        self.test_client.delete_instance(inst['uuid'])
        self._await_instance_deleted(inst['uuid'])
