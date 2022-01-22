import copy
import time

from shakenfist_ci import base


class TestNamespace(base.BaseNamespacedTestCase):
    """Make sure instances boot under various configurations."""

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'namespace_test'
        super(TestNamespace, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestNamespace, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_namespace_clean(self):
        """Check that instances and networks are cleaned from namespace

        The "clean namespace" command line functionality uses
        delete_all_instances() and delete_all_networks() in quick succession to
        delete all components in a namespace. This test replicates this calling
        pattern.
        """

        NUM_INSTANCES = 6
        LONG_WAIT_MINS = 5
        SHORT_WAIT_MINS = 2

        inst_uuids = set()
        for i in range(NUM_INSTANCES):
            new_inst = self.test_client.create_instance(
                'test-%s' % i, 1, 1024,
                [
                    {
                        'network_uuid': self.net['uuid']
                    }
                ],
                [
                    {
                        'size': 8,
                        'base': 'sf://upload/system/debian-11',
                        'type': 'disk'
                    }
                ], None, None, namespace=self.namespace, side_channels=['sf-agent'])
            inst_uuids.add(new_inst['uuid'])

        # Wait for all instances to start
        for uuid in inst_uuids:
            self._await_instance_ready(uuid)

        # Run the test
        self.test_client.delete_all_instances(self.namespace)
        self.test_client.delete_all_networks(self.namespace, clean_wait=True)

        # Wait for instances to be deleted
        start_time = time.time()
        while inst_uuids:
            for uuid in copy.copy(inst_uuids):
                i = self.system_client.get_instance(uuid)
                if i['state'] in ['deleted']:
                    inst_uuids.remove(uuid)
            if time.time() - start_time > LONG_WAIT_MINS * 60:
                break
            time.sleep(5)

        self.assertEqual(0, len(inst_uuids),
                         'Instances not deleted: %s' % inst_uuids)

        start_time = time.time()
        while time.time() - start_time < SHORT_WAIT_MINS * 60:
            test_net = self.test_client.get_network(self.net['uuid'])
            if test_net['state'] in ['deleted', 'error']:
                break
            time.sleep(5)

        self.assertEqual('deleted', test_net['state'],
                         'Network not deleted by delete_all_networks()')
