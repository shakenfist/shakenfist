import json
import logging
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


logging.basicConfig(level=logging.INFO, format='%(message)s')
LOG = logging.getLogger()


# shakenfist.constants.AGENT_INSTANCE_OFF's value, repeated here because this
# suite runs from an installed shakenfist_ci package against a remote cluster
# and cannot import the server's constants module (see
# cluster_ci_tests/test_networking.py's FLOATING_NETWORK_UUID, which does the
# same).
AGENT_INSTANCE_OFF = 'not ready (instance powered off)'


class TestStateChanges(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'statechanges'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

        # We need to start a spare instance on the same node / network so that
        # the network doesn't get torn down during any of the tests.
        inst = self.create_instance(
            'keepalive-statechanges', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail(
            'keepalive inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._emit_tracing_event({
            'msg': 'Started keep network alive instance'
        })
        self.node = inst['node']

    def _start_target(self, suffix):
        self._emit_tracing_event({
            'msg': 'Starting target instance'
        })
        inst = self.create_instance(
            'test-statechanges-%s' % suffix, 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
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
        self._assert_power_state(inst['uuid'], 'on', 'after create')

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
        self._assert_power_state(inst['uuid'], 'on', 'after soft reboot')
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
        self._assert_power_state(inst['uuid'], 'on', 'after hard reboot')
        inst = self.test_client.get_instance(inst['uuid'])
        this_boot = inst['agent_system_boot_time']
        self.assertNotIn(this_boot, [None, 0, last_boot],
                         'Instance %s failed hard reboot' % inst['uuid'])
        last_boot = this_boot
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

    def test_lifecycle_reboot_powered_off(self):
        # A powered off instance normally has a defined but inactive libvirt
        # domain. Rebooting it must return the documented 409, not a 500
        # (issue 3630).
        inst = self._start_target('rebootoff')

        self.test_client.power_off_instance(inst['uuid'])
        # Once the API returns the libvirt has powered off the instance or an
        # error has occurred (which CI will catch).
        self._assert_power_state(inst['uuid'], 'off', 'after power off')

        self.assertRaises(
            apiclient.ResourceStateConflictException,
            self.test_client.reboot_instance, inst['uuid'])
        self.assertRaises(
            apiclient.ResourceStateConflictException,
            self.test_client.reboot_instance, inst['uuid'], hard=True)
        self._assert_power_state(inst['uuid'], 'off', 'after rejected reboots')

    def test_lifecycle_power_cycle(self):
        inst = self._start_target('powercycle')
        last_boot = inst['agent_system_boot_time']
        ip = self.test_client.get_instance_interfaces(inst['uuid'])[0]['ipv4']
        self.assertNotIn(last_boot, [None, 0])

        # Power off
        self.test_client.power_off_instance(inst['uuid'])
        self._assert_power_state(inst['uuid'], 'off', 'after power off')
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
        self._assert_power_state(inst['uuid'], 'on', 'after power on')
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
        self._assert_power_state(inst['uuid'], 'paused', 'after pause')
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
        self._assert_power_state(inst['uuid'], 'on', 'after unpause')
        self._emit_tracing_event({
            'msg': 'Instance ready',
            'instance_uuid': inst['uuid']
        })
        self._test_ping(inst['uuid'], self.net['uuid'], ip, True)

    def _detected_poweroff_reason(self, instance_uuid, after):
        """Return the libvirt shutoff reason on a 'detected poweroff' event.

        _await_power_off() only returns the matching event's timestamp,
        which is all D7's write-order guarantee needs. Reading the reason
        out of extra means re-reading the instance's events for the same
        event. The event's human-readable label -- 'detected poweroff' --
        is carried in the events API response's 'message' field: there is
        no 'operation' field (shakenfist.schema.event.EventReadRow has
        none).

        Called only after _await_power_off() has already found the event,
        so the loop below should always find it too; the fail() at the end
        is a defensive backstop, not the expected path.
        """
        for event in self.system_client.get_instance_events(instance_uuid):
            if event['timestamp'] <= after:
                continue
            if event['message'] == 'detected poweroff':
                return event['extra']['reason']

        self.fail(
            'No "detected poweroff" event with an extra payload found for '
            'instance %s after %s' % (instance_uuid, after))

    def test_lifecycle_guest_poweroff(self):
        # A guest which powers itself off (as opposed to being destroyed by
        # our power_off() API) is only ever noticed by the cleaner's second
        # loop, on its next pass -- there is no synchronous write the API
        # can wait on. See phase 1b's D7 and S11 in
        # docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md.
        inst = self._start_target('guestpoweroff')
        last_boot = inst['agent_system_boot_time']
        self.assertNotIn(last_boot, [None, 0])
        after = time.time()

        # systemd-run merely schedules the power off and returns at once. A
        # bare "systemctl poweroff" would never reply, because it takes the
        # agent's transport down with the guest.
        self._await_command(
            inst['uuid'], 'systemd-run --on-active=3 systemctl poweroff')

        self._await_power_off(inst['uuid'], after=after)
        # A single read is valid here: the cleaner writes power_state before
        # it writes agent_state or the event (D7), and _await_power_off()
        # above already waited for the event.
        self._assert_power_state(inst['uuid'], 'off', 'after guest power off')

        detected = self.test_client.get_instance(inst['uuid'])
        self.assertEqual(
            AGENT_INSTANCE_OFF, detected['agent_state'],
            'Cleaner did not set agent_state on a guest poweroff')
        self.assertEqual(
            'shutdown', self._detected_poweroff_reason(inst['uuid'], after),
            "A guest poweroff should record libvirt's shutoff reason as "
            "'shutdown'")

        # Power back on through the API and confirm the instance recovers.
        self.test_client.delete_console_data(inst['uuid'])
        self.test_client.power_on_instance(inst['uuid'])
        self._await_instance_ready(inst['uuid'])
        self._assert_power_state(inst['uuid'], 'on', 'after power on')
        inst = self.test_client.get_instance(inst['uuid'])
        this_boot = inst['agent_system_boot_time']
        self.assertNotIn(
            this_boot, [None, 0, last_boot],
            'Instance %s failed to reboot after a guest poweroff'
            % inst['uuid'])

    def test_lifecycle_qemu_killed(self):
        # A qemu process killed by a signal is libvirt's other route to an
        # inactive domain, and its shutoff reason is documented (though
        # unverified before this test) as CRASHED. Node exec is unproven in
        # the Guests suite, so this skips loudly rather than silently when
        # it cannot run commands on the instance's hypervisor -- see
        # _require_node_exec()'s docstring.
        inst = self._start_target('qemukilled')
        node = self._node_by_uuid(inst['node'])
        self._require_node_exec(node)
        after = time.time()

        # libvirt's qemu driver names the process "-name guest=<domain
        # name>,...", and libvirt.tmpl sets the domain name to "sf:<uuid>".
        self._node_exec(
            node, ['pkill', '-9', '-f', 'guest=sf:%s' % inst['uuid']],
            sudo=True)

        self._await_power_off(inst['uuid'], after=after)
        self._assert_power_state(inst['uuid'], 'off', 'after qemu killed')

        detected = self.test_client.get_instance(inst['uuid'])
        self.assertEqual(
            'created', detected['state'],
            'A killed qemu should leave the instance in state created, '
            'not change it (D7)')

        # D7 deliberately does not verify this against a real hypervisor
        # before now: mapping CRASHED to 'crashed' was unverified survey
        # text, not a decision anything branches on. Do not weaken this
        # assertion to make a flaky run pass -- if libvirt reports a
        # different reason here, D7 and docs/operator_guide/power_states.md
        # need to be revisited, not this test.
        self.assertEqual(
            'crashed', self._detected_poweroff_reason(inst['uuid'], after),
            "A killed qemu should record libvirt's shutoff reason as "
            "'crashed'")


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
        inst = self.create_instance(
            'test-rebootdetect', 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                },
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        LOG.info('Started test instance %s', inst['uuid'])

        # Wait for our test instance to boot
        self.assertIsNotNone(inst['uuid'])
        self._await_instance_ready(inst['uuid'])
        self._assert_power_state(inst['uuid'], 'on', 'after create')

        inst = self.test_client.get_instance(inst['uuid'])
        first_boot = inst['agent_system_boot_time']
        self.assertIsNotNone(first_boot)

        # Hard reboot
        LOG.info('Instance Hard reboot')
        self.test_client.reboot_instance(inst['uuid'], hard=True)
        self._await_instance_not_ready(inst['uuid'])
        self._await_instance_ready(inst['uuid'])
        self._assert_power_state(inst['uuid'], 'on', 'after hard reboot')

        inst = self.test_client.get_instance(inst['uuid'])
        if first_boot == inst['agent_system_boot_time']:
            raise Exception(
                'Instance %s has not updated its start time within 60 seconds. '
                'First boot at %s, still reporting %s.'
                % (inst['uuid'], first_boot, inst['agent_system_boot_time']))
