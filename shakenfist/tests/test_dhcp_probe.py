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
  `is_running()` reports as healthy) the probe times out;
* the DHCPACK is sent to the request's source port, not to port 68.

None of that can be established here, because nothing here is a real
dnsmasq. tools/dhcp-probe-against-dnsmasq.sh re-measures it against
each supported distribution's dnsmasq, and the smoke CI suite probes
the dnsmasq on its network node. What is tested here with real sockets
is the probe's own side of the exchange.
"""

import os
import shlex
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from unittest import mock

from shakenfist.daemons.network import maintain
from shakenfist.exceptions import CreateNetworkNamespaceFailed
from shakenfist.exceptions import DHCPProbeFailed
from shakenfist.exceptions import DHCPProbeLoopbackDown
from shakenfist.exceptions import MissingPrivExecSocket
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.exceptions import UnknownPrivExecReplyException
from shakenfist.managed_executables.dnsmasq import DnsMasq
from shakenfist.network import network as network_module
from shakenfist.network.network import Network
from shakenfist.tests import base
from shakenfist.util import concurrency as util_concurrency
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
        self.source = ('10.0.0.1', 67)

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
        return reply, self.source

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
        result, sock = self._probe([_reply(self.XID, 6)])   # DHCPNAK
        self.assertFalse(result)
        # It is an answer, so it is not asked for again.
        self.assertEqual(1, len(sock.sent))

    def test_a_reply_from_anywhere_else_is_not_believed(self):
        # The namespace is bridged to the guests, so stray DHCP traffic
        # on the segment reaches the probe's socket and must not be
        # taken for dnsmasq answering. This is not a defence against a
        # hostile guest, which can spoof the router's address; that
        # guest also needs the random transaction id, and could only
        # hide a failure of its own network.
        sock = _FakeSocket([_reply(self.XID, dhcp_probe.DHCPACK)])
        sock.source = ('10.0.0.99', 67)
        with mock.patch.object(
                dhcp_probe, 'build_inform',
                lambda *a, **k: (self.XID, b'packet')):
            self.assertFalse(dhcp_probe.probe(
                '10.0.0.1', '10.0.0.1', timeout=0.01, sock=sock))

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

    def test_a_socket_error_is_not_reported_as_silence(self):
        # An error which says nothing about the server is raised, so
        # that main() reports "could not probe" rather than "no
        # answer" -- only the second gets a network rebuilt.
        self.assertRaises(
            OSError, self._probe, [OSError('network is unreachable')])

    def test_nothing_listening_is_not_serving(self):
        # A refusal is an answer about the server: nothing on port 67.
        result, _ = self._probe([ConnectionRefusedError()])
        self.assertFalse(result)

    def test_it_is_sent_to_the_dhcp_server_port(self):
        _, sock = self._probe([])
        self.assertEqual(
            [(b'packet', ('10.0.0.1', 67))] * dhcp_probe.DEFAULT_ATTEMPTS,
            sock.sent)

    def test_a_lost_datagram_is_sent_again(self):
        # One dropped packet must not get a healthy network rebuilt.
        result, sock = self._probe([
            socket.timeout(), _reply(self.XID, dhcp_probe.DHCPACK)])
        self.assertTrue(result)
        self.assertEqual(2, len(sock.sent))

    def test_an_answer_is_not_asked_for_again(self):
        _, sock = self._probe([_reply(self.XID, dhcp_probe.DHCPACK)])
        self.assertEqual(1, len(sock.sent))

    def test_silence_is_given_up_on_after_every_attempt(self):
        result, sock = self._probe([socket.timeout()] * 10)
        self.assertFalse(result)
        self.assertEqual(dhcp_probe.DEFAULT_ATTEMPTS, len(sock.sent))

    def test_the_timeout_is_applied_to_the_socket(self):
        sock = _FakeSocket([])
        with mock.patch.object(
                dhcp_probe, 'build_inform',
                lambda *a, **k: (self.XID, b'packet')):
            dhcp_probe.probe(
                '10.0.0.1', '10.0.0.1', timeout=3.5, sock=sock)
        self.assertLessEqual(sock.timeout, 3.5)
        self.assertGreater(sock.timeout, 3.0)


class _Responder:
    """A UDP server on loopback which answers the way dnsmasq does.

    dnsmasq answers a DHCPINFORM at the address and port it came from
    (see dhcp_packet() in its src/dhcp.c, and the module docstring for
    where that was measured). ``replies`` turns the request's
    transaction id into the packets to send back, in order, with
    ``interval`` seconds between them.
    """

    def __init__(self, replies, interval=0.0, ignore=0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._ignore = ignore
        self.requests = 0
        self.sock.bind(('127.0.0.1', 0))
        self.port = self.sock.getsockname()[1]
        self._replies = replies
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        self.sock.settimeout(5)
        while True:
            try:
                request, source = self.sock.recvfrom(2048)
            except OSError:
                return
            self.requests += 1
            if self.requests > self._ignore:
                break
        xid = dhcp_probe.parse_transaction_id(request)
        for packet in self._replies(xid):
            if self._stop.is_set():
                return
            try:
                self.sock.sendto(packet, source)
            except OSError:
                return
            if self._interval:
                time.sleep(self._interval)

    def close(self):
        self._stop.set()
        self._thread.join(timeout=10)
        self.sock.close()


class RealSocketProbeTestCase(base.ShakenFistTestCase):
    """probe() over a real loopback socket, not a scripted one."""

    def _probe_against(self, replies, timeout, interval=0.0, ignore=0,
                       attempts=dhcp_probe.DEFAULT_ATTEMPTS):
        responder = _Responder(replies, interval=interval, ignore=ignore)
        self.addCleanup(responder.close)
        start = time.monotonic()
        result = dhcp_probe.probe(
            '127.0.0.1', '127.0.0.1', timeout=timeout,
            server_port=responder.port, attempts=attempts)
        return result, time.monotonic() - start

    def test_an_ack_to_the_source_port_is_heard(self):
        # The probe does not bind port 68, so this is the reply path
        # it depends on.
        result, _ = self._probe_against(
            lambda xid: [_reply(xid, dhcp_probe.DHCPACK)], timeout=2)
        self.assertTrue(result)

    def test_a_dropped_request_is_answered_on_the_retry(self):
        # The responder drops the first INFORM, as a full receive buffer
        # would, and answers the next.
        result, elapsed = self._probe_against(
            lambda xid: [_reply(xid, dhcp_probe.DHCPACK)], timeout=1.5,
            ignore=1)
        self.assertTrue(result)
        self.assertLess(elapsed, 1.5)

    def test_silence_times_out(self):
        result, elapsed = self._probe_against(
            lambda xid: [], timeout=0.6, attempts=6)
        self.assertFalse(result)
        self.assertGreaterEqual(elapsed, 0.6)
        # The retransmissions share the timeout rather than each
        # getting one of their own, which would take 3.6 seconds. The
        # bound sits midway, so a loaded worker's scheduling delay is
        # not mistaken for that.
        self.assertLess(elapsed, 2.1)

    def test_the_deadline_is_overall_not_per_packet(self):
        # A busy network delivers other clients' packets to the probe.
        # A per-read timeout would restart with each one, and this
        # probe would wait for the whole six seconds of chatter
        # rather than give up at its own deadline.
        result, elapsed = self._probe_against(
            lambda xid: [_reply(xid + 1, dhcp_probe.DHCPACK)] * 120,
            timeout=0.5, interval=0.05)
        self.assertFalse(result)
        # Six seconds of chatter; the bound only needs to be well clear
        # of that, not tight against the deadline.
        self.assertLess(elapsed, 3.0)


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

    def test_a_message_type_truncated_mid_option_is_none(self):
        # [53, 1] and then the end of the packet: the length byte
        # promises a value which is not there.
        packet = _reply(1, None)[:-1]
        packet += bytes([dhcp_probe.OPTION_MESSAGE_TYPE, 1])
        self.assertIsNone(dhcp_probe.parse_message_type(packet))

    def test_an_option_longer_than_the_packet_is_none(self):
        # A message type claiming three bytes with one left is a
        # truncated packet, not an ACK.
        packet = _reply(1, None)[:-1]
        packet += bytes([dhcp_probe.OPTION_MESSAGE_TYPE, 3,
                         dhcp_probe.DHCPACK])
        self.assertIsNone(dhcp_probe.parse_message_type(packet))


class MainTestCase(base.ShakenFistTestCase):
    def test_serving_exits_zero(self):
        with mock.patch.object(dhcp_probe, 'probe', return_value=True):
            self.assertEqual(0, dhcp_probe.main(['10.0.0.1', '10.0.0.1']))

    def test_not_serving_exits_with_the_no_answer_status(self):
        with mock.patch.object(dhcp_probe, 'probe', return_value=False):
            self.assertEqual(
                dhcp_probe.EXIT_NO_ANSWER,
                dhcp_probe.main(['10.0.0.1', '10.0.0.1']))

    def test_no_answer_is_not_what_python_exits_with_on_error(self):
        # Python exits 1 for an import failure or an uncaught
        # exception. If "no answer" were 1 too, a probe which could not
        # run would get every network rebuilt.
        self.assertNotIn(dhcp_probe.EXIT_NO_ANSWER, (0, 1))
        self.assertNotEqual(
            dhcp_probe.EXIT_NO_ANSWER, dhcp_probe.EXIT_COULD_NOT_PROBE)

    def test_missing_arguments_could_not_probe(self):
        self.assertEqual(dhcp_probe.EXIT_COULD_NOT_PROBE, dhcp_probe.main([]))

    def test_a_bad_timeout_could_not_probe(self):
        self.assertEqual(
            dhcp_probe.EXIT_COULD_NOT_PROBE,
            dhcp_probe.main(['10.0.0.1', '10.0.0.1', 'soon']))

    def test_a_probe_which_raises_could_not_probe(self):
        with mock.patch.object(
                dhcp_probe, 'probe', side_effect=OSError('no route')):
            self.assertEqual(
                dhcp_probe.EXIT_COULD_NOT_PROBE,
                dhcp_probe.main(['10.0.0.1', '10.0.0.1']))

    def test_the_timeout_is_passed_through(self):
        with mock.patch.object(
                dhcp_probe, 'probe', return_value=True) as mock_probe:
            dhcp_probe.main(['10.0.0.1', '10.0.0.2', '7.5'])
        self.assertEqual(7.5, mock_probe.call_args.kwargs['timeout'])


class LoopbackTestCase(base.ShakenFistTestCase):
    """The probe asks an address which is local to its own namespace.

    So the request is delivered over loopback, and a namespace from
    "ip netns add" has its loopback down. A down loopback drops the
    packet instead of failing the send, so the probe cannot tell that
    from a wedged dnsmasq -- which is how every DHCP network in CI
    reported itself wedged. It must not report that as "no answer",
    because "no answer" is what rebuilds a network.
    """

    def _flags_file(self, contents):
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.flags')
        self.addCleanup(f.close)
        f.write(contents)
        f.flush()
        return f.name

    def test_the_up_bit_is_read_from_the_flags(self):
        # 0x9 is IFF_UP|IFF_LOOPBACK, which is what a namespace's lo
        # reads once it has been brought up; 0x8 is the same interface
        # down. Measured on debian:trixie.
        self.assertTrue(
            dhcp_probe.loopback_is_up(self._flags_file('0x9\n')))
        self.assertFalse(
            dhcp_probe.loopback_is_up(self._flags_file('0x8\n')))

    def test_unreadable_flags_are_not_a_refusal(self):
        # A host with no such sysfs file should still be able to probe,
        # so "do not know" is permission to go ahead rather than a stop.
        self.assertIsNone(
            dhcp_probe.loopback_is_up('/nonexistent/class/net/lo/flags'))
        self.assertIsNone(
            dhcp_probe.loopback_is_up(self._flags_file('banana\n')))

    def test_a_down_loopback_stops_the_probe(self):
        sock = _FakeSocket([])
        with mock.patch.object(dhcp_probe, 'loopback_is_up',
                               return_value=False):
            self.assertRaises(
                dhcp_probe.ProbeImpossible, dhcp_probe.probe,
                '10.0.0.1', '10.0.0.1', timeout=0.01, sock=sock)
        self.assertEqual(
            [], sock.sent,
            'A request which cannot be delivered should not be sent')

    def test_a_down_loopback_is_not_reported_as_no_answer(self):
        # The status which matters: EXIT_NO_ANSWER is read as a wedged
        # dnsmasq and rebuilds the network. A down loopback has its own
        # status, because the caller repairs it.
        with mock.patch.object(dhcp_probe, 'loopback_is_up',
                               return_value=False):
            self.assertEqual(
                dhcp_probe.EXIT_LOOPBACK_DOWN,
                dhcp_probe.main(['10.0.0.1', '10.0.0.1']))

    def test_the_exit_statuses_are_distinct_and_not_python_s_own(self):
        statuses = [dhcp_probe.EXIT_ANSWERED, dhcp_probe.EXIT_COULD_NOT_PROBE,
                    dhcp_probe.EXIT_NO_ANSWER, dhcp_probe.EXIT_LOOPBACK_DOWN]
        self.assertEqual(len(statuses), len(set(statuses)))
        self.assertNotIn(1, statuses)

    def test_an_unknown_loopback_does_not_stop_the_probe(self):
        xid = 0x22222222
        sock = _FakeSocket([_reply(xid, dhcp_probe.DHCPACK)])
        with mock.patch.object(dhcp_probe, 'loopback_is_up',
                               return_value=None), \
                mock.patch.object(dhcp_probe, 'build_inform',
                                  lambda *a, **k: (xid, b'packet')):
            self.assertTrue(dhcp_probe.probe(
                '10.0.0.1', '10.0.0.1', timeout=0.01, sock=sock))


class IsServingDhcpTestCase(base.ShakenFistTestCase):
    """DnsMasq.is_serving_dhcp() and the network audit which calls it."""

    def _dnsmasq(self, provide_dhcp=True):
        d = mock.MagicMock()
        d.provide_dhcp = provide_dhcp
        d.network.router = '10.0.0.1'
        d.network.uuid = 'a-netns'
        # This runs every pass for every DHCP network, and subst_dict()
        # reads every instance on the network to build a lease list the
        # probe has no use for.
        d.subst_dict.side_effect = AssertionError(
            'the probe built the whole subst_dict()')
        d.is_serving_dhcp = partial(DnsMasq.is_serving_dhcp, d)
        return d

    def test_an_answering_dnsmasq_is_serving(self):
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', return_value=('', '')):
            self.assertTrue(d.is_serving_dhcp())

    def test_a_wedged_dnsmasq_is_not_serving(self):
        # The probe exits with its no-answer status when nothing
        # answered, which execute() raises for. That is the condition
        # being reported, not a failure to run the probe.
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute',
                side_effect=ProcessExecutionError(
                    '', '', dhcp_probe.EXIT_NO_ANSWER, 'probe')):
            self.assertFalse(d.is_serving_dhcp())

    def test_a_probe_which_could_not_import_is_not_a_wedge(self):
        # Exit 1 is Python failing, not dnsmasq: the first version of
        # this ran a python3 with no shakenfist and read the resulting
        # ModuleNotFoundError as every dnsmasq being wedged.
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute',
                side_effect=ProcessExecutionError(
                    '', 'No module named shakenfist', 1, 'probe')):
            e = self.assertRaises(DHCPProbeFailed, d.is_serving_dhcp)
        self.assertIn('No module named shakenfist', str(e))

    def test_privexec_being_unreachable_is_not_a_wedge(self):
        # ...and must not escape into the maintain pass, which would
        # abandon every network after this one.
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', side_effect=MissingPrivExecSocket()):
            self.assertRaises(DHCPProbeFailed, d.is_serving_dhcp)

    def test_a_privexec_which_refuses_is_not_a_wedge(self):
        # A restarting sf-privexec leaves a socket which refuses.
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', side_effect=ConnectionRefusedError()):
            self.assertRaises(DHCPProbeFailed, d.is_serving_dhcp)

    def test_a_programming_error_is_not_could_not_probe(self):
        # Read as "could not probe", a defect on this path would report
        # every network as serving DHCP forever, with nothing louder
        # than a warning. It propagates instead.
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', side_effect=TypeError('unexpected keyword')):
            self.assertRaises(TypeError, d.is_serving_dhcp)

    def test_a_down_loopback_is_reported_as_repairable(self):
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute',
                side_effect=ProcessExecutionError(
                    '', 'the loopback interface is down',
                    dhcp_probe.EXIT_LOOPBACK_DOWN, 'probe')):
            e = self.assertRaises(DHCPProbeLoopbackDown, d.is_serving_dhcp)
        self.assertIn('loopback', str(e))

    def test_a_protocol_bug_is_not_could_not_probe(self):
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', side_effect=UnknownPrivExecReplyException()):
            self.assertRaises(
                UnknownPrivExecReplyException, d.is_serving_dhcp)

    def test_the_probe_is_run_by_this_interpreter(self):
        d = self._dnsmasq()
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', return_value=('', '')) as mock_execute:
            d.is_serving_dhcp()
        self.assertEqual(
            sys.executable, shlex.split(mock_execute.call_args[0][0])[0])

    def test_the_command_runs_where_privexec_runs_it(self):
        # The command as built, run for real: with privexec's PATH, and
        # from a directory with no shakenfist checkout in it, as
        # sf-privexec runs it. Against a loopback address nothing
        # answers DHCP on, so a probe which ran exits with the no-answer
        # status -- and one which could not import exits 1.
        d = self._dnsmasq()
        d.network.router = '127.0.0.1'
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', return_value=('', '')) as mock_execute:
            with mock.patch(
                    'shakenfist.managed_executables.dnsmasq.config.'
                    'DHCP_PROBE_TIMEOUT', 0.2):
                d.is_serving_dhcp()
        command = mock_execute.call_args[0][0]

        with tempfile.TemporaryDirectory() as cwd:
            env = {'PATH': util_concurrency.PRIVEXEC_DEFAULT_PATH}
            result = subprocess.run(
                command, shell=True, cwd=cwd, env=env, timeout=60,
                capture_output=True, text=True)
        self.assertEqual(
            dhcp_probe.EXIT_NO_ANSWER, result.returncode,
            f'{command!r} exited {result.returncode}: {result.stderr}')

    def test_the_command_ignores_modules_in_its_working_directory(self):
        # It runs as root, in whatever directory privexec inherited. With
        # -m and nothing else, that directory is first on sys.path, so a
        # socket.py planted there would run in place of the real one.
        d = self._dnsmasq()
        d.network.router = '127.0.0.1'
        with mock.patch(
                'shakenfist.managed_executables.dnsmasq.util_concurrency.'
                'execute', return_value=('', '')) as mock_execute:
            with mock.patch(
                    'shakenfist.managed_executables.dnsmasq.config.'
                    'DHCP_PROBE_TIMEOUT', 0.2):
                d.is_serving_dhcp()
        command = mock_execute.call_args[0][0]

        with tempfile.TemporaryDirectory() as cwd:
            with open(os.path.join(cwd, 'socket.py'), 'w') as f:
                f.write('raise SystemExit(42)\n')
            env = {'PATH': util_concurrency.PRIVEXEC_DEFAULT_PATH}
            result = subprocess.run(
                command, shell=True, cwd=cwd, env=env, timeout=60,
                capture_output=True, text=True)
        self.assertNotEqual(
            42, result.returncode,
            f'{command!r} imported socket.py from its working directory')
        self.assertEqual(
            dhcp_probe.EXIT_NO_ANSWER, result.returncode,
            f'{command!r} exited {result.returncode}: {result.stderr}')

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


class IsDnsmasqServingDhcpTestCase(base.ShakenFistTestCase):
    """Network.is_dnsmasq_serving_dhcp(), which turns the probe into events."""

    NETWORK_UUID = 'aaaaaaaa-1111-4222-8333-444444444444'

    def setUp(self):
        super().setUp()
        network_module.DHCP_PROBE_FAILURE_REPORTED.clear()
        self.addCleanup(network_module.DHCP_PROBE_FAILURE_REPORTED.clear)

    def _network(self, serving=True, side_effect=None):
        n = mock.MagicMock()
        n.uuid = self.NETWORK_UUID
        d = n._get_dnsmasq_object.return_value
        d.is_serving_dhcp.return_value = serving
        d.is_serving_dhcp.side_effect = side_effect
        n.is_dnsmasq_serving_dhcp = partial(
            Network.is_dnsmasq_serving_dhcp, n)
        n._bring_namespace_loopback_up = partial(
            Network._bring_namespace_loopback_up, n)
        return n

    def test_a_down_loopback_is_repaired_without_a_rebuild(self):
        # A namespace built before networks were created with lo up
        # keeps it down, and a probe which cannot run leaves the network
        # looking healthy, so no rebuild ever comes to repair it. The
        # readiness check repairs it itself, and then asks again.
        n = self._network(side_effect=[
            DHCPProbeLoopbackDown('lo is down'), False])
        with mock.patch.object(
                network_module.util_concurrency,
                'create_network_namespace') as mock_create:
            # The answer after the repair is the answer: here, wedged.
            self.assertFalse(n.is_dnsmasq_serving_dhcp())

        mock_create.assert_called_once_with(self.NETWORK_UUID)
        self.assertEqual(
            2, n._get_dnsmasq_object.return_value.is_serving_dhcp.call_count)
        messages = self._messages(n)
        self.assertIn(
            'brought the network namespace loopback interface up', messages)
        self.assertNotIn(
            'could not check whether dnsmasq is answering DHCP', messages)

    def test_a_loopback_which_cannot_be_repaired_could_not_be_probed(self):
        # Not a reason to rebuild either: it is reported like any other
        # probe which could not run.
        n = self._network(side_effect=DHCPProbeLoopbackDown('lo is down'))
        for error in (CreateNetworkNamespaceFailed(), MissingPrivExecSocket()):
            network_module.DHCP_PROBE_FAILURE_REPORTED.clear()
            n.add_event.reset_mock()
            with mock.patch.object(
                    network_module.util_concurrency,
                    'create_network_namespace', side_effect=error):
                self.assertTrue(n.is_dnsmasq_serving_dhcp())
            self.assertEqual(
                ['could not check whether dnsmasq is answering DHCP'],
                self._messages(n))
            self.assertIn(
                'could not be brought up',
                n.add_event.call_args.kwargs['extra']['error'])

    def test_a_repair_which_does_not_take_is_not_retried_in_a_loop(self):
        # One repair and one more probe per pass: if lo is still down
        # the second probe's failure is the answer for this pass.
        n = self._network(side_effect=DHCPProbeLoopbackDown('lo is down'))
        with mock.patch.object(
                network_module.util_concurrency,
                'create_network_namespace') as mock_create:
            self.assertTrue(n.is_dnsmasq_serving_dhcp())
        mock_create.assert_called_once_with(self.NETWORK_UUID)
        self.assertEqual(
            2, n._get_dnsmasq_object.return_value.is_serving_dhcp.call_count)

    def test_a_probe_which_could_not_run_does_not_rebuild(self):
        # Not knowing is not the same as dnsmasq being wedged.
        n = self._network(side_effect=DHCPProbeFailed('no privexec'))
        self.assertTrue(n.is_dnsmasq_serving_dhcp())

    def test_a_probe_which_could_not_run_says_so(self):
        # ...but it is said, on the network: a readiness check which
        # can never run is broken and should be visible.
        n = self._network(side_effect=DHCPProbeFailed('no privexec'))
        n.is_dnsmasq_serving_dhcp()
        messages = [c.args[1] for c in n.add_event.call_args_list]
        self.assertIn(
            'could not check whether dnsmasq is answering DHCP', messages)
        self.assertEqual(
            'no privexec', n.add_event.call_args.kwargs['extra']['error'])

    def _messages(self, n):
        return [c.args[1] for c in n.add_event.call_args_list]

    def test_it_is_said_once_per_episode_not_once_per_pass(self):
        # The event is a durable row per network. One every thirty
        # seconds for as long as privexec is unreachable is load on the
        # event log which tells nobody anything the first did not.
        n = self._network(side_effect=DHCPProbeFailed('no privexec'))
        for _ in range(3):
            n.is_dnsmasq_serving_dhcp()
        self.assertEqual(
            ['could not check whether dnsmasq is answering DHCP'],
            self._messages(n))

    def test_an_event_which_could_not_be_written_is_tried_again(self):
        # Marked as reported only once the event is written, or a
        # database blip loses it for the whole episode.
        n = self._network(side_effect=DHCPProbeFailed('no privexec'))
        n.add_event.side_effect = RuntimeError('database went away')
        self.assertRaises(RuntimeError, n.is_dnsmasq_serving_dhcp)
        self.assertNotIn(
            self.NETWORK_UUID, network_module.DHCP_PROBE_FAILURE_REPORTED)

        n.add_event.side_effect = None
        n.is_dnsmasq_serving_dhcp()
        self.assertIn(
            'could not check whether dnsmasq is answering DHCP',
            self._messages(n))
        self.assertIn(
            self.NETWORK_UUID, network_module.DHCP_PROBE_FAILURE_REPORTED)

    def test_a_recovery_which_could_not_be_written_is_tried_again(self):
        n = self._network(side_effect=DHCPProbeFailed('no privexec'))
        n.is_dnsmasq_serving_dhcp()

        d = n._get_dnsmasq_object.return_value
        d.is_serving_dhcp.side_effect = None
        n.add_event.side_effect = RuntimeError('database went away')
        self.assertRaises(RuntimeError, n.is_dnsmasq_serving_dhcp)
        self.assertIn(
            self.NETWORK_UUID, network_module.DHCP_PROBE_FAILURE_REPORTED)

        n.add_event.side_effect = None
        n.is_dnsmasq_serving_dhcp()
        self.assertNotIn(
            self.NETWORK_UUID, network_module.DHCP_PROBE_FAILURE_REPORTED)

    def test_the_log_still_says_it_every_pass(self):
        n = self._network(side_effect=DHCPProbeFailed('no privexec'))
        with mock.patch.object(network_module, 'LOG') as mock_log:
            for _ in range(3):
                n.is_dnsmasq_serving_dhcp()
        self.assertEqual(
            3, mock_log.with_fields.return_value.warning.call_count)

    def test_recovery_is_said_and_a_new_episode_is_reported(self):
        n = self._network(side_effect=DHCPProbeFailed('no privexec'))
        n.is_dnsmasq_serving_dhcp()

        d = n._get_dnsmasq_object.return_value
        d.is_serving_dhcp.side_effect = None
        n.is_dnsmasq_serving_dhcp()
        n.is_dnsmasq_serving_dhcp()

        d.is_serving_dhcp.side_effect = DHCPProbeFailed('no privexec')
        n.is_dnsmasq_serving_dhcp()

        self.assertEqual(
            ['could not check whether dnsmasq is answering DHCP',
             'can check whether dnsmasq is answering DHCP again',
             'could not check whether dnsmasq is answering DHCP'],
            self._messages(n))

    def test_a_network_no_pass_visits_is_forgotten(self):
        n = self._network(side_effect=DHCPProbeFailed('no privexec'))
        n.is_dnsmasq_serving_dhcp()
        maintain.forget_unvisited_quiesces([self.NETWORK_UUID])
        self.assertIn(
            self.NETWORK_UUID, network_module.DHCP_PROBE_FAILURE_REPORTED)

        maintain.forget_unvisited_quiesces(['some-other-network'])
        self.assertNotIn(
            self.NETWORK_UUID, network_module.DHCP_PROBE_FAILURE_REPORTED)

    def test_a_wedged_dnsmasq_is_reported(self):
        n = self._network(serving=False)
        self.assertFalse(n.is_dnsmasq_serving_dhcp())
        messages = [c.args[1] for c in n.add_event.call_args_list]
        self.assertIn('dnsmasq is running but not answering DHCP', messages)
