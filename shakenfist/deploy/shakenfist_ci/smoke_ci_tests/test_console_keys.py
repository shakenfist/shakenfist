import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestConsoleKeys(base.BaseNamespacedTestCase):
    """POST /instances/<ref>/console/keys types at the guest's console.

    Issue 4580: without this there was no way to drive an appliance whose
    only configuration surface is an interactive first boot wizard on the
    VGA console, short of connecting to the hypervisor's VNC listener
    directly.

    The keystrokes land on the guest's emulated keyboard, and what the
    guest does with them is its own business -- a stock Debian image's
    login prompt does not echo anywhere this suite can read. So what is
    asserted here is the whole path to libvirt and back: that the request
    is accepted on the node hosting the instance, that the keycodes were
    ones libvirt would take (a bad codeset or chord is a 500 from the
    hypervisor, not a 200), that the audit event records the keystrokes
    without recording what they were, and that each refusal answers the
    status its declaration publishes.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'consolekeys'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def _send_keys(self, instance_uuid, **body):
        # No released client verb sends these yet, so the endpoint is
        # driven directly. _request_url raises the client's typed
        # exceptions for a non-2xx, which is what the refusals below
        # assert on.
        return self.test_client._request_url(
            'POST', '/instances/%s/console/keys' % instance_uuid,
            data=body).json()

    def _refusal_status(self, instance_uuid, **body):
        """The status code a refused request answers.

        The client raises a different exception class per status and this
        suite cares about the status itself, so the raise is unwound back
        into one. A request which is not refused at all returns 200,
        which fails the assertion rather than passing silently.
        """
        try:
            self._send_keys(instance_uuid, **body)
            return 200
        except apiclient.APIException as e:
            return e.status_code

    def test_console_keys(self):
        inst = self.create_instance(
            'test-console-keys', 1, 1024,
            [{'network_uuid': self.net['uuid']}],
            [{'size': 8, 'base': base.CLUSTER_CI_IMAGE, 'type': 'disk'}],
            None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._await_instance_create(inst['uuid'])

        # Text, one keystroke per character. The newline is the enter
        # key, so this is five keystrokes and not four plus a stray.
        self.assertEqual(
            {'keystrokes': 5},
            self._send_keys(inst['uuid'], text='frob\n'))

        # Named keys and a chord, which is the half a wizard needs for
        # its function keys and its arrow keys.
        self.assertEqual(
            {'keystrokes': 4},
            self._send_keys(inst['uuid'],
                            keys=['f2', 'down', 'ctrl+c', 'KEY_ENTER']))

        # hold_ms is passed to libvirt as its holdtime, so a value it
        # would refuse shows up as a 500 rather than as this 200.
        self.assertEqual(
            {'keystrokes': 1},
            self._send_keys(inst['uuid'], keys=['esc'], hold_ms=10))

        # The audit trail says keystrokes were sent and how many, and
        # deliberately does not say what they were: a first boot wizard
        # asks for passwords, and an event is a durable record.
        events = self.test_client.get_instance_events(inst['uuid'])
        sends = [e for e in events
                 if e.get('message') == 'sent keystrokes to instance console']
        self.assertNotEqual([], sends, 'No console keystroke event: %s'
                            % json.dumps(events, indent=4, sort_keys=True))
        rendered = json.dumps(sends)
        self.assertNotIn('frob', rendered)
        self.assertIn('chords', rendered)

        # Exactly one of text and keys, so that "send the string 'keys'"
        # and "send the key named f2" cannot be confused.
        # hold_ms alone, so that the request still carries a body: the
        # property under test is that neither keystroke parameter was
        # sent, not that the body was empty.
        self.assertEqual(400, self._refusal_status(inst['uuid'], hold_ms=0))
        self.assertEqual(
            400, self._refusal_status(inst['uuid'], text='a', keys=['enter']))

        # A key name Shaken Fist cannot send is refused here rather than
        # by libvirt, which would not say which element was wrong.
        self.assertEqual(
            400, self._refusal_status(inst['uuid'], keys=['banana']))
        self.assertEqual(
            400, self._refusal_status(inst['uuid'], text='café'))

        # And the request is bounded, because libvirt's holdtime paces
        # the keystrokes and so an unbounded one is an unbounded wait.
        self.assertEqual(
            400, self._refusal_status(inst['uuid'], text='a' * 1000))

        # A powered off instance has a defined but inactive libvirt
        # domain, which answers the documented 409 rather than a 500.
        self.test_client.power_off_instance(inst['uuid'])
        self._assert_power_state(inst['uuid'], 'off', 'after power off')
        self.assertEqual(
            409, self._refusal_status(inst['uuid'], text='a'))
