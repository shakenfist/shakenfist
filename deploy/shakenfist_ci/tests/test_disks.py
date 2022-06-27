from shakenfist_ci import base


class TestDiskBusBase(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = '%s-disks' % self.bus
        super(TestDiskBusBase, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestDiskBusBase, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_disk(self):
        inst = self.test_client.create_instance(
            'test-boot-%s' % self.bus, 2, 2048,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk',
                    'bus': self.bus
                }
            ], None, None, side_channels=['sf-agent'])

        self._await_instance_ready(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        self.assertNotIn(inst['agent_system_boot_time'], [None, 0])

        self.test_client.delete_instance(inst['uuid'])
        inst_uuids = []
        for i in self.test_client.get_instances():
            inst_uuids.append(i['uuid'])
        self.assertNotIn(inst['uuid'], inst_uuids)


class TestNVMeDisks(TestDiskBusBase):
    def __init__(self, *args, **kwargs):
        self.bus = 'nvme'
        super(TestNVMeDisks, self).__init__(*args, **kwargs)


class TestSATADisks(TestDiskBusBase):
    def __init__(self, *args, **kwargs):
        self.bus = 'sata'
        super(TestSATADisks, self).__init__(*args, **kwargs)


class TestIDEDisks(TestDiskBusBase):
    def __init__(self, *args, **kwargs):
        self.bus = 'ide'
        super(TestIDEDisks, self).__init__(*args, **kwargs)
