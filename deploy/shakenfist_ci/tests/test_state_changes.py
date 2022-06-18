import logging
import time

from shakenfist_ci import base


logging.basicConfig(level=logging.INFO, format='%(message)s')
LOG = logging.getLogger()


class TestStateChanges(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'statechanges'
        super(TestStateChanges, self).__init__(*args, **kwargs)

    def setUp(self):
        super(TestStateChanges, self).setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def test_lifecycle_events(self):
        # Start our test instance
        inst = self.test_client.create_instance(
            'test-statechanges', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])
        LOG.info('Started test instance %s', inst['uuid'])

        # We need to start a second instance on the same node / network so that
        # the network doesn't get torn down during any of the tests.
        self.test_client.create_instance(
            'keepalive-statechanges', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, force_placement=inst['node'], side_channels=['sf-agent'])
        LOG.info('Started keep network alive instance')

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])

        self._await_instance_ready(inst['uuid'])

        # We need to refetch the instance to get a complete view of its state.
        # It is also now safe to fetch the instance IP.
        inst = self.test_client.get_instance(inst['uuid'])
        last_boot = inst['agent_system_boot_time']
        self.assertNotIn(last_boot, [None, 0])
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']

        # The network can be slow to start and may not be available after the
        # instance "system ready" event. We are willing to forgive a few fails
        # while the network starts.
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, 0, 10)

        # Soft reboot
        LOG.info('Instance Soft reboot')
        self.test_client.delete_console_data(inst['uuid'])
        self.test_client.reboot_instance(inst['uuid'])
        time.sleep(60)

        self._await_instance_ready(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        this_boot = inst['agent_system_boot_time']
        self.assertNotIn(
            this_boot, [None, 0, last_boot],
            'Instance %s failed soft reboot' % inst['uuid'])
        last_boot = this_boot

        self._test_ping(inst['uuid'], self.net['uuid'], ip, 0, 10)

        # Hard reboot
        LOG.info('Instance Hard reboot')
        self.test_client.delete_console_data(inst['uuid'])
        self.test_client.reboot_instance(inst['uuid'], hard=True)
        time.sleep(60)

        self._await_instance_ready(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        this_boot = inst['agent_system_boot_time']
        self.assertNotIn(this_boot, [None, 0, last_boot],
                         'Instance %s failed hard reboot' % inst['uuid'])
        last_boot = this_boot

        self._test_ping(inst['uuid'], self.net['uuid'], ip, 0, 10)

        # Power off
        LOG.info('Power off')
        self.test_client.power_off_instance(inst['uuid'])
        # Once the API returns the libvirt has powered off the instance or an
        # error has occurred (which CI will catch).
        time.sleep(60)
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, 100)

        # Power on
        LOG.info('Instance Power on')
        self.test_client.delete_console_data(inst['uuid'])
        self.test_client.power_on_instance(inst['uuid'])
        time.sleep(60)

        self._await_instance_ready(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        this_boot = inst['agent_system_boot_time']
        self.assertNotIn(this_boot, [None, 0, this_boot],
                         'Instance %s failed power cycle' % inst['uuid'])
        last_boot = this_boot

        self._test_ping(inst['uuid'], self.net['uuid'], ip, 0, 10)

        # Pause
        LOG.info('Instance Pause')
        self.test_client.pause_instance(inst['uuid'])
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, 100, 10)

        # Unpause
        LOG.info('Instance Unpause')
        self.test_client.unpause_instance(inst['uuid'])
        # No new login prompt after unpause, so just forgive a few fails while
        # the instance is un-paused.
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, 0, 10)

    def test_agent_detects_reboot(self):
        # Start our test instance
        inst = self.test_client.create_instance(
            'test-rebootdetect', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-11',
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent'])
        LOG.info('Started test instance %s', inst['uuid'])

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        inst = self.test_client.get_instance(inst['uuid'])
        first_boot = inst['agent_system_boot_time']
        self.assertIsNotNone(first_boot)

        # Hard reboot
        LOG.info('Instance Hard reboot')
        self.test_client.reboot_instance(inst['uuid'], hard=True)

        time.sleep(60)
        inst = self.test_client.get_instance(inst['uuid'])
        if first_boot == inst['agent_system_boot_time']:
            raise Exception(
                'Instance %s has not updated its start time within 60 seconds. '
                'First boot at %s, still reporting %s.'
                % (inst['uuid'], first_boot, inst['agent_system_boot_time']))
