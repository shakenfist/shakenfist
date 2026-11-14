# Copyright 2026 Michael Still and contributors
#
# End to end tests for what POST /instances/<ref>/console/keys answers
# (InstanceConsoleKeysEndpoint), and for what it hands libvirt.

import json
from unittest import mock
from uuid import uuid4

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.external_api import app as external_api
from shakenfist.external_api import instance as api_instance
from shakenfist.instance import Instance
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.util import keymap
from shakenfist.util import libvirt as util_libvirt


class FakeLibvirtError(Exception):
    """Enough of libvirt.libvirtError for the error code checks."""

    def __init__(self, code):
        super().__init__('libvirt said no')
        self.code = code

    def get_error_code(self):
        return self.code


class InstanceConsoleKeysTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        # Placed on this node so redirect_instance_request() runs the
        # handler here rather than proxying it to a hypervisor.
        saved_node_uuid = config.NODE_UUID
        self.addCleanup(setattr, config, 'NODE_UUID', saved_node_uuid)
        config.NODE_UUID = self.mock_mariadb.node_uuids['node1_net']

        self.instance_uuid = str(uuid4())
        self.mock_mariadb.create_instance(
            'typeme', uuid=self.instance_uuid, namespace='foo',
            set_state=dbo.STATE_CREATED, place_on_node=config.NODE_UUID)

        self.send = mock.patch.object(Instance, 'send_console_keys').start()
        self.addCleanup(mock.patch.stopall)

        self.client = external_api.app.test_client()
        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': 'foo', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth = {
            'Authorization': 'Bearer %s' % resp.get_json()['access_token']}

    def _post(self, **body):
        return self.client.post(
            '/instances/%s/console/keys' % self.instance_uuid,
            headers=self.auth, data=json.dumps(body))

    def test_text_is_typed_one_chord_per_character(self):
        resp = self._post(text='hi\n')
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual({'keystrokes': 3}, resp.get_json())
        self.send.assert_called_once_with([[35], [23], [28]], 0)

    def test_keys_are_sent_as_named_chords(self):
        resp = self._post(keys=['f2', 'ctrl+alt+delete'])
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertEqual({'keystrokes': 2}, resp.get_json())
        self.send.assert_called_once_with([[60], [29, 56, 111]], 0)

    def test_hold_ms_reaches_libvirt(self):
        resp = self._post(text='a', hold_ms=20)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.send.assert_called_once_with([[30]], 20)

    def test_a_hold_ms_sent_as_a_string_is_read_as_a_number(self):
        # The validation layer is check only, so a JSON string which the
        # published schema reads as an integer arrives as a string.
        resp = self._post(text='a', hold_ms='20')
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.send.assert_called_once_with([[30]], 20)

    def test_neither_text_nor_keys_is_refused(self):
        resp = self._post()
        self.assertEqual(400, resp.status_code)
        self.assertIn('exactly one of', resp.get_json()['error'])
        self.send.assert_not_called()

    def test_both_text_and_keys_is_refused(self):
        resp = self._post(text='a', keys=['enter'])
        self.assertEqual(400, resp.status_code)
        self.assertIn('exactly one of', resp.get_json()['error'])
        self.send.assert_not_called()

    def test_an_unknown_key_name_is_refused_naming_it(self):
        resp = self._post(keys=['enter', 'banana'])
        self.assertEqual(400, resp.status_code)
        self.assertIn('banana', resp.get_json()['error'])
        self.send.assert_not_called()

    def test_an_untypeable_character_is_refused(self):
        resp = self._post(text='café')
        self.assertEqual(400, resp.status_code)
        self.assertIn('key name', resp.get_json()['error'])
        self.send.assert_not_called()

    def test_too_much_text_is_refused(self):
        resp = self._post(text='a' * (api_instance.MAX_CONSOLE_KEYSTROKES + 1))
        self.assertEqual(400, resp.status_code)
        self.assertIn('at most', resp.get_json()['error'])
        self.send.assert_not_called()

    def test_too_many_keys_are_refused(self):
        resp = self._post(
            keys=['enter'] * (api_instance.MAX_CONSOLE_KEYSTROKES + 1))
        self.assertEqual(400, resp.status_code)
        self.assertIn('at most', resp.get_json()['error'])
        self.send.assert_not_called()

    def test_the_largest_accepted_request_is_still_accepted(self):
        # The control for the two refusals above: an off by one in the
        # bound would otherwise read as a working guard.
        resp = self._post(text='a' * api_instance.MAX_CONSOLE_KEYSTROKES)
        self.assertEqual(200, resp.status_code, resp.get_json())

    def test_hold_ms_above_the_published_maximum_is_refused(self):
        resp = self._post(text='a', hold_ms=10000)
        self.assertEqual(400, resp.status_code)
        self.assertIn('hold_ms', resp.get_json()['error'])
        self.send.assert_not_called()

    def test_hold_ms_is_clamped_when_validation_is_rolled_back(self):
        # The declared maximum is enforced by the validation layer, which
        # an operator can switch off. The clamp in the handler is what
        # keeps the time one request can block an API worker bounded even
        # then, which is why the bound is tested in both modes.
        saved = config.API_VALIDATION_MODE
        self.addCleanup(setattr, config, 'API_VALIDATION_MODE', saved)
        config.API_VALIDATION_MODE = 'off'

        resp = self._post(text='a', hold_ms=10000)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.send.assert_called_once_with(
            [[30]], api_instance.MAX_CONSOLE_HOLD_MS)

    def test_a_negative_hold_ms_is_clamped_when_validation_is_rolled_back(self):
        saved = config.API_VALIDATION_MODE
        self.addCleanup(setattr, config, 'API_VALIDATION_MODE', saved)
        config.API_VALIDATION_MODE = 'off'

        resp = self._post(text='a', hold_ms=-5)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.send.assert_called_once_with([[30]], 0)

    def test_a_non_string_text_is_refused_when_validation_is_rolled_back(self):
        # The type guard is unreachable at enforce, and is what stops the
        # handler faulting on len() of an integer with the layer off.
        saved = config.API_VALIDATION_MODE
        self.addCleanup(setattr, config, 'API_VALIDATION_MODE', saved)
        config.API_VALIDATION_MODE = 'off'

        resp = self._post(text=7)
        self.assertEqual(400, resp.status_code)
        self.assertIn('text must be a string', resp.get_json()['error'])

        resp = self._post(keys='enter')
        self.assertEqual(400, resp.status_code)
        self.assertIn('keys must be a list', resp.get_json()['error'])
        self.send.assert_not_called()

    def test_a_stopped_instance_answers_409(self):
        self.send.side_effect = exceptions.InvalidLifecycleState(
            'instance is not running, cannot send console keystrokes')
        resp = self._post(text='a')
        self.assertEqual(409, resp.status_code)
        self.assertIn('not running', resp.get_json()['error'])


class SendConsoleKeysTestCase(base.ShakenFistTestCase):
    """What Instance.send_console_keys() and LibvirtConnection.send_keys()
    hand libvirt.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.instance_uuid = str(uuid4())
        self.mock_mariadb.create_instance(
            'typeme', uuid=self.instance_uuid, set_state=dbo.STATE_CREATED)
        self.inst = Instance.from_db(self.instance_uuid)

    def _connection(self, domain):
        lc = util_libvirt.LibvirtConnection()
        lc.libvirt = mock.MagicMock()
        lc.libvirt.VIR_KEYCODE_SET_LINUX = 'linuxcodeset'
        # A real class, because send_keys() catches it: `except
        # <MagicMock>` is a TypeError rather than a handler.
        lc.libvirt.libvirtError = FakeLibvirtError
        lc.libvirt.VIR_ERR_NO_DOMAIN = 42
        lc.libvirt.VIR_ERR_OPERATION_INVALID = 55
        lc.conn = mock.MagicMock()
        lc.conn.lookupByName.return_value = domain
        return lc

    def test_each_chord_is_one_libvirt_send_key_call(self):
        domain = mock.MagicMock()
        lc = self._connection(domain)
        lc.send_keys(self.instance_uuid, keymap.chords_for_text('Ab'), 5)

        # The codeset matters as much as the codes: the same numbers in
        # the XT or QNUM set type something else entirely.
        self.assertEqual(
            [mock.call('linuxcodeset', 5, [keymap.KEY_LEFTSHIFT, 30], 2, 0),
             mock.call('linuxcodeset', 5, [48], 1, 0)],
            domain.sendKey.call_args_list)

    def test_no_domain_raises_rather_than_faulting(self):
        lc = self._connection(None)
        self.assertRaises(exceptions.NoDomainException, lc.send_keys,
                          self.instance_uuid, [[28]], 0)

    def test_a_powered_off_domain_reads_as_no_domain(self):
        # lookupByName() finds an inactive domain, so "is there anything
        # to type at" is only answered by what sendKey() says. Without
        # this branch a powered off instance answered 500.
        domain = mock.MagicMock()
        lc = self._connection(domain)
        domain.sendKey.side_effect = FakeLibvirtError(
            lc.libvirt.VIR_ERR_OPERATION_INVALID)
        self.assertRaises(exceptions.NoDomainException, lc.send_keys,
                          self.instance_uuid, [[28]], 0)

    def test_any_other_libvirt_error_is_not_swallowed(self):
        domain = mock.MagicMock()
        lc = self._connection(domain)
        domain.sendKey.side_effect = FakeLibvirtError(999)
        self.assertRaises(FakeLibvirtError, lc.send_keys,
                          self.instance_uuid, [[28]], 0)

    def test_a_missing_domain_is_an_invalid_lifecycle_state(self):
        conn = mock.MagicMock()
        conn.__enter__.return_value.send_keys.side_effect = \
            exceptions.NoDomainException(self.instance_uuid)

        with mock.patch('shakenfist.util.libvirt.LibvirtConnection',
                        return_value=conn):
            self.assertRaises(
                exceptions.InvalidLifecycleState,
                self.inst.send_console_keys, [[28]], 0)

    def test_the_keystrokes_are_not_recorded_in_the_event(self):
        # A first boot wizard asks for passwords, and an event is a
        # durable, queryable record on the instance.
        conn = mock.MagicMock()
        with mock.patch('shakenfist.util.libvirt.LibvirtConnection',
                        return_value=conn):
            with mock.patch.object(Instance, 'add_event') as add_event:
                self.inst.send_console_keys(
                    keymap.chords_for_text('sekrit'), 0)

        add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT, 'sent keystrokes to instance console',
            extra={'chords': 6, 'hold_ms': 0})
