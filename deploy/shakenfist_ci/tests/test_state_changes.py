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

    def test_lifecycle_events(self):
        # Start our test instance
        inst = self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None)
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        LOG.info('Started test instance %s', inst['uuid'])

        # We need to start a second instance on the same node / network so that
        # the network doesn't get torn down during any of the tests.
        self.test_client.create_instance(
            'cirros', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': 'cirros',
                    'type': 'disk'
                }
            ], None, None, force_placement=inst['node'])
        LOG.info('Started keep network alive instance')

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])
        self._await_login_prompt(inst['uuid'])
        LOG.info('  ping test...')
        # The network can be slow to start and may not be available after the
        # instance "login prompt" event. We are willing to forgive a few fails
        # while the network starts.
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True, 10)

        # Soft reboot
        LOG.info('Instance Soft reboot')
        self.test_client.reboot_instance(inst['uuid'])
        self._await_login_prompt(inst['uuid'], after=time.time())
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True, 10)

        # Hard reboot
        LOG.info('Instance Hard reboot')
        self.test_client.reboot_instance(inst['uuid'], hard=True)
        self._await_login_prompt(inst['uuid'], after=time.time())
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True, 10)

        # Power off
        LOG.info('Power off')
        self.test_client.power_off_instance(inst['uuid'])
        self._await_power_off(inst['uuid'])
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, False)

        # Power on
        LOG.info('Instance Power on')
        self.test_client.power_on_instance(inst['uuid'])
        self._await_login_prompt(inst['uuid'], after=time.time())
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True, 10)

        # Pause
        LOG.info('Instance Pause')
        self.test_client.pause_instance(inst['uuid'])
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, False, 10)

        # Unpause
        LOG.info('Instance Unpause')
        self.test_client.unpause_instance(inst['uuid'])
        # No new login prompt after unpause, so just forgive a few fails while
        # the instance is un-paused.
        LOG.info('  ping test...')
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True, 10)
