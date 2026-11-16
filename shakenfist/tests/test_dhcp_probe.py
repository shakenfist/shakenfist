# Copyright 2026 Michael Still and contributors
"""dnsmasq readiness, as opposed to dnsmasq liveness.

`is_running()` reads the pid file and asks whether that process
exists, so a dnsmasq which is alive but wedged passes it and the
network looks healthy while its guests cannot get a lease (issue 730).

The packet-level expectations below were measured against a real
dnsmasq configured the way Shaken Fist configures one
(`dhcp-range=...,static` with a `dhcp-hostsfile`), not assumed:

* a DHCPINFORM from a MAC the server has never heard of is answered
  with a DHCPACK, while a DHCPDISCOVER from the same MAC is ignored,
  because `static` mode serves only hosts in its hosts file -- so a
  DISCOVER probe would have called every healthy network broken;
* the reply carries `yiaddr` 0.0.0.0 and the lease file is untouched;
* with the server stopped but still present (state `T`, which
  `is_running()` reports as healthy) the probe times out.
"""

import socket
import struct
from functools import partial
from unittest import mock

from shakenfist.exceptions import ProcessExecutionError
from shakenfist.managed_executables.dnsmasq import DnsMasq
from shakenfist.network import network as network_module
from shakenfist.network.network import Network
from shakenfist.tests import base
from shakenfist.util import dhcp_probe


def _reply(xid, message_type, include_cookie=True):
    packet = struct.pack(
        '!BBBBIHH4s4s4s4s16s64s128s',
        2, 1, 6, 0, xid, 0, 0,
        socket.inet_aton('0.0.0.0'),
        socket.inet_aton('0.0.0.0'),
        b'\x00' * 4, b'\x00' * 4,
        b'\x00' * 16, b'\x00' * 64, b'\x00' * 128)
    if include_cookie:
        packet += dhcp_probe.MAGIC_COOKIE
    if message_type is not None:
        packet += bytes([dhcp_probe.OPTION_MESSAGE_TYPE, 1, message_type])
    packet += bytes([dhcp_probe.OPTION_END])
    return packet


class _FakeSocket:
    """A socket which replies with a scripted list of packets."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.sent = []
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendto(self, data, address):
        self.sent.append((data, address))

    def recvfrom(self, _size):
        if not self._replies:
            raise socket.timeout()
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, ('10.0.0.1', 67)

    def close(self):
        pass


class BuildInformTestCase(base.ShakenFistTestCase):
    def test_it_is_an_inform_not_a_discover(self):
        # A DISCOVER is ignored by a dnsmasq in static mode for a MAC
        # it does not know, so probing with one would report every
        # healthy network as broken.
        _, packet = dhcp_probe.build_inform('10.0.0.1')

        options = packet[dhcp_probe.BOOTP_HEADER_BYTES
                         + len(dhcp_probe.MAGIC_COOKIE):]
        self.assertEqual(dhcp_probe.OPTION_MESSAGE_TYPE, options[0])
        self.assertEqual(dhcp_probe.DHCPINFORM, options[2])

    def test_the_ciaddr_is_set(self):
        # An INFORM with no ciaddr is not a valid INFORM: it is the
        # field which tells the server who is asking.
        _, packet = dhcp_probe.build_inform('10.0.0.7')
        self.assertEqual(
            socket.inet_aton('10.0.0.7'), packet[12:16])

    def test_it_carries_the_magic_cookie(self):
        _, packet = dhcp_probe.build_inform('10.0.0.1')
        self.assertEqual(
            dhcp_probe.MAGIC_COOKIE,
            packet[dhcp_probe.BOOTP_HEADER_BYTES:
                   dhcp_probe.BOOTP_HEADER_BYTES
                   + len(dhcp_probe.MAGIC_COOKIE)])

    def test_the_probe_mac_is_locally_administered(self):
        # So it cannot collide with a real card in the hosts file.
        self.assertTrue(dhcp_probe.PROBE_MAC[0] & 0x02)


class ProbeTestCase(base.ShakenFistTestCase):
    XID = 0x11111111

    def _probe(self, replies):
        """Probe with a known transaction id and scripted replies."""
        sock = _FakeSocket(replies)
        with mock.patch.object(
                dhcp_probe, 'build_inform',
                lambda *a, **k: (self.XID, b'packet')):
            result = dhcp_probe.probe(
                '10.0.0.1', '10.0.0.1', timeout=0.01, sock=sock)
        return result, sock

    def test_an_ack_means_it_is_serving(self):
        result, _ = self._probe([_reply(self.XID, dhcp_probe.DHCPACK)])
        self.assertTrue(result)

    def test_silence_means_it_is_not_serving(self):
        # The wedged case: the process is alive, so is_running() is
        # happy, and nothing answers.
        result, _ = self._probe([])
        self.assertFalse(result)

    def test_a_reply_which_is_not_an_ack_is_not_serving(self):
        result, _ = self._probe([_reply(self.XID, 6)])      # DHCPNAK
        self.assertFalse(result)

    def test_another_clients_reply_is_not_mistaken_for_ours(self):
        # dnsmasq broadcasts, so a guest mid-handshake can land in our
        # socket. Reading it as our answer would report a wedged server
        # as healthy.
        result, _ = self._probe([_reply(0x22222222, dhcp_probe.DHCPACK)])
        self.assertFalse(result)

    def test_our_reply_is_still_found_behind_another_clients(self):
        # ...and the converse: somebody else's packet arriving first
        # must not make us give up on ours.
        result, _ = self._probe([
            _reply(0x22222222, dhcp_probe.DHCPACK),
            _reply(self.XID, dhcp_probe.DHCPACK)])
        self.assertTrue(result)

    def test_a_socket_error_is_not_serving(self):
        result, _ = self._probe([OSError('network is unreachable')])
        self.assertFalse(result)

    def test_it_is_sent_to_the_dhcp_server_port(self):
        _, sock = self._probe([])
        self.assertEqual([(b'packet', ('10.0.0.1', 67))], sock.sent)

    def test_the_timeout_is_applied_to_the_socket(self):
        sock = _FakeSocket([])
        with mock.patch.object(
                dhcp_probe, 'build_inform',
                lambda *a, **k: (self.XID, b'packet')):
            dhcp_probe.probe(
                '10.0.0.1', '10.0.0.1', timeout=3.5, sock=sock)
        self.assertEqual(3.5, sock.timeout)


class ParseTestCase(base.ShakenFistTestCase):
    def test_a_packet_with_no_cookie_has_no_message_type(self):
        self.assertIsNone(
            dhcp_probe.parse_message_type(
                _reply(1, dhcp_probe.DHCPACK, include_cookie=False)))

    def test_a_short_packet_has_no_message_type(self):
        self.assertIsNone(dhcp_probe.parse_message_type(b'short'))

    def test_a_packet_with_no_message_type_option(self):
        self.assertIsNone(dhcp_probe.parse_message_type(_reply(1, None)))

    def test_options_before_the_message_type_are_skipped(self):
        packet = _reply(1, None)[:-1]
        packet += bytes([dhcp_probe.OPTION_PAD])
        packet += bytes([1, 4, 255, 255, 255, 0])        # subnet mask
        packet += bytes([dhcp_probe.OPTION_MESSAGE_TYPE, 1,
                         dhcp_probe.DHCPACK])
        packet += bytes([dhcp_probe.OPTION_END])
        self.assertEqual(
            dhcp_probe.DHCPACK, dhcp_probe.parse_message_type(packet))


class MainTestCase(base.ShakenFistTestCase):
    def test_serving_exits_zero(self):
        with mock.patch.object(dhcp_probe, 'probe', return_value=True):
            self.assertEqual(0, dhcp_probe.main(['10.0.0.1', '10.0.0.1']))

    def test_not_serving_exits_one(self):
        with mock.patch.object(dhcp_probe, 'probe', return_value=False):
            self.assertEqual(1, dhcp_probe.main(['10.0.0.1', '10.0.0.1']))

    def test_missing_arguments_exit_two(self):
        self.assertEqual(2, dhcp_probe.main([]))

    def test_the_timeout_is_passed_through(self):
        with mock.patch.object(
                dhcp_probe, 'probe', return_value=True) as mock_probe:
            dhcp_probe.main(['10.0.0.1', '10.0.0.2', '7.5'])
        self.assertEqual(7.5, mock_probe.call_args.kwargs['timeout'])


class IsServingDhcpTestCase(base.ShakenFistTestCase):
    """DnsMasq.is_serving_dhcp() and the network audit which calls it."""

    def _dnsmasq(self, provide_dhcp=True):
        d = mock.MagicMock()
        d.provide_dhcp = provide_dhcp
        d.subst_dict.return_value = {
            'router': '10.0.0.1', 'netns': 'a-netns'}
        d.is_serving_dhcp = partial(DnsMasq.is_serving_dhcp, d)
        return d

    def test_an_answering_dnsmasq_is_serving(self):
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', return_value=('', '')):
            self.assertTrue(d.is_serving_dhcp())

    def test_a_wedged_dnsmasq_is_not_serving(self):
        # The probe exits non-zero when nothing answered, which
        # execute() raises for. That is the condition being reported,
        # not a failure to run the probe.
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute',
                side_effect=ProcessExecutionError('exit 1', '', '', 1)):
            self.assertFalse(d.is_serving_dhcp())

    def test_the_probe_runs_inside_the_network_namespace(self):
        # The server listens on an address which only exists in there,
        # so a probe run outside it would report every network broken.
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', return_value=('', '')) as mock_execute:
            d.is_serving_dhcp()

        self.assertEqual('a-netns', mock_execute.call_args.kwargs['netns'])
        self.assertIn('dhcp_probe', mock_execute.call_args[0][0])
        self.assertIn('10.0.0.1', mock_execute.call_args[0][0])

    def test_a_dns_only_network_is_not_probed(self):
        # There is no DHCP readiness to check, and reporting such a
        # network unhealthy would rebuild it on every pass.
        d = self._dnsmasq(provide_dhcp=False)
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute') as mock_execute:
            self.assertTrue(d.is_serving_dhcp())
        mock_execute.assert_not_called()


class NetworkIsOkayTestCase(base.ShakenFistTestCase):
    """is_okay() drives the maintain pass's recreate, so a wedged
    dnsmasq must make it False -- that is what gets the network
    repaired rather than left serving nothing."""

    def _network(self, running=True, serving=True):
        n = mock.MagicMock()
        n.has_pending_cluster_operation.return_value = False
        n.is_created.return_value = True
        n.provide_dhcp = True
        n.provide_dns = False
        n.provide_nat = False
        n.is_dnsmasq_running.return_value = running
        n.is_dnsmasq_serving_dhcp.return_value = serving
        n.is_okay = partial(Network.is_okay, n)
        return n

    def test_a_healthy_network_is_okay(self):
        with mock.patch.object(network_module.config,
                               'NODE_IS_NETWORK_NODE', True):
            self.assertTrue(self._network().is_okay())

    def test_a_dead_dnsmasq_is_not_okay(self):
        with mock.patch.object(network_module.config,
                               'NODE_IS_NETWORK_NODE', True):
            self.assertFalse(self._network(running=False).is_okay())

    def test_a_wedged_dnsmasq_is_not_okay(self):
        # The regression this issue is about: running but not serving
        # used to read as healthy.
        with mock.patch.object(network_module.config,
                               'NODE_IS_NETWORK_NODE', True):
            self.assertFalse(self._network(serving=False).is_okay())

    def test_liveness_is_checked_before_readiness(self):
        # No point sending a DHCPINFORM to a process which is not
        # there, and the liveness check is far cheaper.
        n = self._network(running=False)
        with mock.patch.object(network_module.config,
                               'NODE_IS_NETWORK_NODE', True):
            n.is_okay()
        n.is_dnsmasq_serving_dhcp.assert_not_called()
