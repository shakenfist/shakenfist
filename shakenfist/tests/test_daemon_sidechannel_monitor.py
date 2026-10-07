# Copyright 2026 Michael Still and contributors

import socket
from unittest import mock

from shakenfist import constants
from shakenfist.daemons.sidechannel import main as sidechannel
from shakenfist.protos import agent_pb2
from shakenfist.tests import base


class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class _FakeInstance:
    """Records every agent_state write rather than modelling the database.

    The real setter skips a write which would not change the stored
    value; these tests care about what the monitor asks for, so every
    assignment is kept.
    """

    def __init__(self):
        self.uuid = 'fake-instance'
        self.agent_state_writes = []
        self.add_event = mock.MagicMock()

    @property
    def agent_state(self):
        return self.agent_state_writes[-1] if self.agent_state_writes else None

    @agent_state.setter
    def agent_state(self, value):
        self.agent_state_writes.append(value)


class _FakeAgentSocket:
    """A vsock which replays a script of agent behaviour against a clock.

    Each step is (seconds, payload): the clock moves on by seconds, then
    recv() either times out (payload None) or returns payload. When the
    script runs out recv() returns nothing, which ends the monitor's loop
    the way a closed connection does.
    """

    def __init__(self, clock, script):
        self.clock = clock
        self.script = list(script)
        self.sent = []
        self.sent_at = []

    def recv(self, size):
        if not self.script:
            return b''
        seconds, payload = self.script.pop(0)
        self.clock.now += seconds
        if payload is None:
            raise socket.timeout()
        return payload

    def sendall(self, data):
        envelope = agent_pb2.HypervisorToAgent()
        envelope.ParseFromString(data)
        for cmd in envelope.commands:
            self.sent.append(cmd.WhichOneof('request'))
            self.sent_at.append(self.clock.now)


def _silence(seconds):
    return [(1, None)] * seconds


def _reply(**kwargs):
    envelope = agent_pb2.AgentToHypervisor()
    envelope.commands.append(
        agent_pb2.AgentToHypervisorCommand(command_id='reply', **kwargs))
    return (0.1, envelope.SerializeToString())


def _ping_reply():
    return _reply(ping_reply=agent_pb2.PingReply())


def _running_reply(result=True, message=''):
    return _reply(is_system_running_reply=agent_pb2.IsSystemRunningReply(
        result=result, message=message))


class SideChannelMonitorReplyGapTestCase(base.ShakenFistTestCase):
    """A gap in agent replies makes the monitor re-check the agent's state.

    The monitor writes agent_state only when its cached view changes, but
    pause() and unpause() write it too. A pause short enough that the
    management loop never tears the monitor down left the cache at ready
    and the database at "no contact" for good, because ping replies never
    touch agent_state. The gap is the only sign the monitor gets that the
    guest was frozen.
    """

    NOW = 1700000000.0

    def _run(self, cached_state, script):
        inst = _FakeInstance()
        with mock.patch.object(sidechannel.daemon, 'clear_abort_path'):
            monitor = sidechannel.SideChannelMonitorJob(inst)
        monitor.log = mock.MagicMock()

        # The monitor has already talked to this agent and settled on a
        # state; the constructor's own write is not under test.
        monitor.instance_ready = cached_state
        inst.agent_state_writes = []

        clock = _Clock(self.NOW)
        sock = _FakeAgentSocket(clock, script)
        with mock.patch('time.time', clock), \
                mock.patch.object(sidechannel.daemon, 'check_abort_path',
                                  return_value=True):
            monitor._execute_inner(mock.MagicMock(sock=sock))

        # The first thing sent is always the hypervisor welcome.
        self.assertEqual('hypervisor_welcome', sock.sent[0])
        self.sent_at = sock.sent_at[1:]
        return monitor, inst, sock.sent[1:]

    def test_a_gap_makes_the_next_ping_ask_whether_the_system_is_running(self):
        # The guest freezes, answers a ping once it is thawed, and is then
        # asked whether it is running. Its answer is written, although the
        # cache said ready before the gap -- which is what replaces the
        # "no contact" unpause() left behind.
        _, inst, sent = self._run(
            constants.AGENT_READY,
            _silence(10) + [_ping_reply()] + _silence(4) + [_running_reply()])

        # While frozen the guest is only pinged: the gap is not acted on
        # until something arrives, so the first is_system_running goes
        # out after the reply which ended the gap.
        self.assertIn('ping_request', sent)
        first = sent.index('is_system_running_request')
        self.assertGreater(self.sent_at[first], self.NOW + 10)

        self.assertEqual([constants.AGENT_READY], inst.agent_state_writes)

        # Moving back into ready gathers facts again, which is wanted
        # after a pause: the guest may have changed while frozen.
        self.assertEqual('gather_facts_request', sent[-1])

    def test_without_a_gap_plain_pings_continue_and_nothing_is_written(self):
        # A healthy agent answers every ping inside the threshold, so the
        # cache is trusted and nothing goes near the database.
        script = []
        for _ in range(5):
            script += _silence(3) + [_ping_reply()]
        monitor, inst, sent = self._run(constants.AGENT_READY, script)

        self.assertTrue(sent)
        self.assertEqual(['ping_request'] * len(sent), sent)
        self.assertEqual([], inst.agent_state_writes)
        self.assertEqual(constants.AGENT_READY, monitor.instance_ready)

    def test_the_reset_happens_once_per_gap(self):
        # After the gap the agent answers promptly again, so the state is
        # re-checked exactly once: one is_system_running, one write, one
        # gather facts, and plain pings from then on.
        script = (_silence(10) + [_ping_reply()] + _silence(3)
                  + [_running_reply()])
        for _ in range(4):
            script += _silence(3) + [_ping_reply()]
        monitor, inst, sent = self._run(constants.AGENT_READY, script)

        self.assertEqual(1, sent.count('is_system_running_request'))
        self.assertEqual(1, sent.count('gather_facts_request'))
        self.assertEqual([constants.AGENT_READY], inst.agent_state_writes)
        self.assertEqual('ping_request', sent[-1])
        self.assertEqual(constants.AGENT_READY, monitor.instance_ready)

    def test_a_gap_resets_a_cached_state_which_is_not_ready(self):
        # A degraded guest is asked is_system_running on every ping
        # already, but its reply is only written when it differs from the
        # cache, so the same pause would strand it too.
        degraded = constants.AGENT_DEGRADED % 'starting'
        _, inst, sent = self._run(
            degraded,
            _silence(10) + [_running_reply(False, 'starting')])

        self.assertEqual([degraded], inst.agent_state_writes)
        self.assertEqual('gather_facts_request', sent[-1])
