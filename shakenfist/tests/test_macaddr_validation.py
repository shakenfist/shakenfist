# Copyright 2019 Michael Still and contributors

import json
from unittest import mock

from shakenfist.external_api import base as api_base
from shakenfist.external_api.instance import _netdesc_safety_checks
from shakenfist.tests import base
from shakenfist.util import network as util_network


class ValidMacaddrTestCase(base.ShakenFistTestCase):
    def test_accepts_well_formed(self):
        for macaddr in ['02:00:00:19:e4:b4', '1a:91:64:d2:15:39',
                        'AA:BB:CC:DD:EE:FF', '00:00:00:00:00:00',
                        'aA:bB:cC:dD:eE:fF']:
            self.assertTrue(util_network.valid_macaddr(macaddr),
                            f'{macaddr} should be valid')

    def test_rejects_malformed(self):
        for macaddr in [
                '',
                'banana',
                '02:00:00:19:e4',              # too short
                '02:00:00:19:e4:b4:c7',        # too long
                '02-00-00-19-e4-b4',           # dash separated
                '020000019e4b4',               # no separators
                '02:00:00:19:e4:g0',           # not hex
                '2:0:0:19:e4:b4',              # not zero padded
                ' 02:00:00:19:e4:b4',          # leading whitespace
                '02:00:00:19:e4:b4 ',          # trailing whitespace
                '02:00:00:19:e4:b4\n',         # trailing newline
        ]:
            self.assertFalse(util_network.valid_macaddr(macaddr),
                             f'{macaddr!r} should be invalid')

    def test_rejects_non_strings(self):
        for macaddr in [None, 42, ['02:00:00:19:e4:b4'],
                        {'macaddress': '02:00:00:19:e4:b4'}]:
            self.assertFalse(util_network.valid_macaddr(macaddr),
                             f'{macaddr!r} should be invalid')

    def test_generated_macaddrs_are_valid(self):
        # A drift guard. If random_macaddr() ever changes shape, the
        # validator has to keep accepting what we ourselves generate,
        # or every interface we allocate a MAC for stops being creatable.
        for _ in range(100):
            macaddr = util_network.random_macaddr()
            self.assertTrue(util_network.valid_macaddr(macaddr),
                            f'generated {macaddr} should be valid')

    def test_published_pattern_matches_validator(self):
        # The REST API publishes MACADDR_PATTERN as the pattern for the
        # 'macaddr' parameter type. If these two ever come apart the
        # specification describes an API we do not implement.
        self.assertEqual(util_network.MACADDR_PATTERN,
                         api_base.ARGTYPES['macaddr']['pattern'])


class NetdescMacaddrTestCase(base.ShakenFistTestCase):
    """The MAC format check in _netdesc_safety_checks.

    Both callers of _netdesc_safety_checks -- instance create and
    interface hotplug -- go through this, so covering the helper covers
    both entry points.
    """

    @mock.patch('shakenfist.network.network.Network.from_db_by_ref')
    def test_malformed_macaddr_rejected(self, mock_from_db_by_ref):
        resp = _netdesc_safety_checks(
            {'network_uuid': 'notanetwork', 'macaddress': 'banana'}, 'ns')

        self.assertIsNotNone(resp)
        self.assertEqual(400, resp.status_code)
        self.assertIn('malformed MAC address',
                      json.loads(resp.get_data(as_text=True))['error'])

        # The check happens before the database is consulted. A bad MAC
        # is a bad request whether or not the network exists, and it
        # costs nothing to say so without a round trip.
        mock_from_db_by_ref.assert_not_called()

    @mock.patch('shakenfist.network.network.Network.from_db_by_ref',
                return_value=None)
    def test_well_formed_macaddr_passes_the_check(self, mock_from_db_by_ref):
        # Reaching the network lookup (and so the 404 for a network that
        # does not exist) proves the MAC was accepted.
        resp = _netdesc_safety_checks(
            {'network_uuid': 'notanetwork',
             'macaddress': '02:00:00:19:e4:b4'}, 'ns')

        self.assertIsNotNone(resp)
        self.assertEqual(404, resp.status_code)
        mock_from_db_by_ref.assert_called()

    @mock.patch('shakenfist.network.network.Network.from_db_by_ref',
                return_value=None)
    def test_absent_macaddr_is_not_rejected(self, mock_from_db_by_ref):
        # Not supplying a MAC is the common case: NetworkInterface.new()
        # generates one.
        for netdesc in [{'network_uuid': 'notanetwork'},
                        {'network_uuid': 'notanetwork', 'macaddress': None},
                        {'network_uuid': 'notanetwork', 'macaddress': ''}]:
            resp = _netdesc_safety_checks(netdesc, 'ns')
            self.assertEqual(404, resp.status_code)
