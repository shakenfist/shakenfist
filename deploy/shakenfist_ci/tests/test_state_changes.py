import logging
import time

from shakenfist_ci import base


logging.basicConfig(level=logging.INFO, format='%(message)s')
LOG = logging.getLogger()


class TestStateChanges(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'statechanges'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, self.namespace)
        self._await_networks_ready([self.net['uuid']])

        # We need to start a spare instance on the same node / network so that
        # the network doesn't get torn down during any of the tests.
        inst = self.test_client.create_instance(
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
            ], None, None)
        self._emit_tracing_event({
            'msg': 'Started keep network alive instance'
        })
        self.node = inst['node']

    def _start_target(self, suffix):
        self._emit_tracing_event({
            'msg': 'Starting target instance'
        })
        inst = self.test_client.create_instance(
            'test-statechanges-%s' % suffix, 1, 1024,
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
            ], None, None,
            force_placement=self.node)
        self._emit_tracing_event({
            'msg': 'Started target instance',
            'instance_uuid': inst['uuid']
        })

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        # We need to refetch the instance to get a complete view of its state.
        # It is also now safe to fetch the instance IP.
        inst = self.test_client.get_instance(inst['uuid'])
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']

        # The network can be slow to start and may not be available after the
        # instance "system ready" event. We are willing to forgive a few fails
        # while the network starts.
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)
        return inst

    def test_lifecycle_soft_reboot(self):
        inst = self._start_target('softreboot')
        last_boot = inst['agent_system_boot_time']
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self.assertNotIn(last_boot, [None, 0])

        self.test_client.delete_console_data(inst['uuid'])
        self.test_client.reboot_instance(inst['uuid'])
        self._await_instance_not_ready(inst['uuid'])
        self._await_instance_ready(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        this_boot = inst['agent_system_boot_time']
        self.assertNotIn(
            this_boot, [None, 0, last_boot],
            'Instance %s failed soft reboot' % inst['uuid'])
        last_boot = this_boot
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

    def test_lifecycle_hard_reboot(self):
        inst = self._start_target('hardreboot')
        last_boot = inst['agent_system_boot_time']
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self.assertNotIn(last_boot, [None, 0])

        self.test_client.delete_console_data(inst['uuid'])
        self.test_client.reboot_instance(inst['uuid'], hard=True)
        self._await_instance_not_ready(inst['uuid'])
        self._await_instance_ready(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        this_boot = inst['agent_system_boot_time']
        self.assertNotIn(this_boot, [None, 0, last_boot],
                         'Instance %s failed hard reboot' % inst['uuid'])
        last_boot = this_boot
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

    def test_lifecycle_power_cycle(self):
        inst = self._start_target('powercycle')
        last_boot = inst['agent_system_boot_time']
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self.assertNotIn(last_boot, [None, 0])

        # Power off
        self.test_client.power_off_instance(inst['uuid'])
        # Once the API returns the libvirt has powered off the instance or an
        # error has occurred (which CI will catch).
        time.sleep(5)
        self._test_ping(inst['uuid'], self.net['uuid'], ip, False)

        # Rapidly powering on an instance has been showing to confuse libvirt
        # in some cases. Let's see if being more patient here makes it work
        # better.
        time.sleep(30)

        # Power on
        self.test_client.delete_console_data(inst['uuid'])
        self.test_client.power_on_instance(inst['uuid'])
        self._await_instance_not_ready(inst['uuid'])
        self._await_instance_ready(inst['uuid'])
        inst = self.test_client.get_instance(inst['uuid'])
        this_boot = inst['agent_system_boot_time']
        self.assertNotIn(this_boot, [None, 0, last_boot],
                         'Instance %s failed power cycle' % inst['uuid'])
        last_boot = this_boot
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

    def test_lifecycle_pause_cycle(self):
        inst = self._start_target('pausecycle')
        last_boot = inst['agent_system_boot_time']
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self.assertNotIn(last_boot, [None, 0])

        # Pause
        self.test_client.pause_instance(inst['uuid'])
        self._emit_tracing_event({
            'msg': 'Paused instance',
            'instance_uuid': inst['uuid']
        })
        self._await_instance_not_ready(inst['uuid'])
        self._emit_tracing_event({
            'msg': 'Instance not ready',
            'instance_uuid': inst['uuid']
        })
        self._test_ping(inst['uuid'], self.net['uuid'], ip, False)

        # Unpause
        self.test_client.unpause_instance(inst['uuid'])
        self._emit_tracing_event({
            'msg': 'Unpaused instance',
            'instance_uuid': inst['uuid']
        })
        self._await_instance_ready(inst['uuid'])
        self._emit_tracing_event({
            'msg': 'Instance ready',
            'instance_uuid': inst['uuid']
        })
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)


class TestDetectReboot(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'detectreboot'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, self.namespace)
        self._await_networks_ready([self.net['uuid']])

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
            ], None, None)
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
        self._await_instance_not_ready(inst['uuid'])
        self._await_instance_ready(inst['uuid'])

        inst = self.test_client.get_instance(inst['uuid'])
        if first_boot == inst['agent_system_boot_time']:
            raise Exception(
                'Instance %s has not updated its start time within 60 seconds. '
                'First boot at %s, still reporting %s.'
                % (inst['uuid'], first_boot, inst['agent_system_boot_time']))
