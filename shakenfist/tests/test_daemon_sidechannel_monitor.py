# Copyright 2026 Michael Still and contributors

import socket
from unittest import mock

from shakenfist import constants
from shakenfist.daemons.sidechannel import main as sidechannel
from shakenfist import instance
from shakenfist.protos import agent_pb2
from shakenfist.tests import base
from shakenfist.util import libvirt as util_libvirt


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

    record_observed_agent_state() is the real implementation, bound to
    this fake: it is what decides whether a reading from the agent is
    recorded at all, so a fake of its own would test nothing.
    """

    record_observed_agent_state = \
        instance.Instance.record_observed_agent_state
    _agent_state_when_not_running = \
        instance.Instance._agent_state_when_not_running

    def __init__(self, power_state='on'):
        self.uuid = 'fake-instance'
        self.agent_state_writes = []
        self.add_event = mock.MagicMock()
        self.power_state = {'power_state': power_state}

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


class _MonitorScriptTestCase(base.ShakenFistTestCase):
    """Runs one monitor against a script of agent behaviour."""

    NOW = 1700000000.0

    def _run(self, cached_state, script, power_state='on'):
        inst = _FakeInstance(power_state=power_state)
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


class SideChannelMonitorReplyGapTestCase(_MonitorScriptTestCase):
    """A gap in agent replies makes the monitor re-check the agent's state.

    The monitor writes agent_state only when its cached view changes, but
    pause() and unpause() write it too. A pause short enough that the
    management loop never tears the monitor down left the cache at ready
    and the database at "no contact" for good, because ping replies never
    touch agent_state. The gap is the only sign the monitor gets that the
    guest was frozen.
    """

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


def _welcome_reply(boot_time=1699999000):
    return _reply(agent_welcome=agent_pb2.AgentWelcome(
        version='1.0', boot_time=boot_time))


class SideChannelMonitorNotRunningTestCase(_MonitorScriptTestCase):
    """The monitor does not write a reading of a guest which is not running.

    The monitor and the power methods write agent_state with no lock
    between them, so an is_system_running reply in flight as the vCPUs
    froze could land after pause() or power_off() had written "instance
    paused" or "instance powered off". The management loop then tears the
    monitor down and nothing rewrites the value, leaving power_state
    reading paused or off while agent_state reads ready -- which passes
    the readiness gates in external_api/instance.py and leaves agent
    operations queued until their deadline (issue 4485).
    """

    def test_a_reply_which_lands_after_a_pause_is_not_written(self):
        monitor, inst, sent = self._run(
            constants.AGENT_NEVER_TALKED,
            [_running_reply()] + _silence(3), power_state='paused')

        # The agent said it was ready, but the instance is recorded as
        # paused, so that is what the database keeps.
        self.assertEqual(
            [constants.AGENT_INSTANCE_PAUSED], inst.agent_state_writes)

        # No facts are gathered from a guest which is not running, and the
        # cache is not advanced, so the next round trip asks whether the
        # system is running rather than merely pinging. power_state is a
        # cache too, and a guest resumed outside Shaken Fist is running
        # while power_state still says paused.
        self.assertEqual(['is_system_running_request'], sent)
        self.assertEqual(
            constants.AGENT_NEVER_TALKED, monitor.instance_ready)

    def test_a_reply_which_lands_after_a_power_off_is_not_written(self):
        _, inst, _ = self._run(
            constants.AGENT_NEVER_TALKED, [_running_reply()],
            power_state='off')

        self.assertEqual(
            [constants.AGENT_INSTANCE_OFF], inst.agent_state_writes)

    def test_the_monitor_asks_again_on_the_next_reply(self):
        # Two replies while the instance reads paused means two attempts,
        # because not caching the refused state is what lets the monitor
        # recover once the cleaner has noticed the guest is running again.
        _, inst, sent = self._run(
            constants.AGENT_NEVER_TALKED,
            [_running_reply()] + _silence(3) + [_running_reply()],
            power_state='paused')

        self.assertEqual(
            [constants.AGENT_INSTANCE_PAUSED] * 2, inst.agent_state_writes)
        self.assertEqual(['is_system_running_request'], sent)

    def test_a_welcome_from_a_paused_guest_is_not_written(self):
        monitor, inst, _ = self._run(
            constants.AGENT_NEVER_TALKED, [_welcome_reply()],
            power_state='paused')

        self.assertEqual(
            [constants.AGENT_INSTANCE_PAUSED], inst.agent_state_writes)
        self.assertEqual(
            constants.AGENT_NEVER_TALKED, monitor.instance_ready)

    def test_a_running_guests_reply_is_written_as_observed(self):
        monitor, inst, sent = self._run(
            constants.AGENT_NEVER_TALKED, [_running_reply()])

        self.assertEqual([constants.AGENT_READY], inst.agent_state_writes)
        self.assertEqual(constants.AGENT_READY, monitor.instance_ready)
        self.assertIn('gather_facts_request', sent)

    def test_the_constructor_does_not_overwrite_a_paused_instance(self):
        # The management loop can list a domain as running just before a
        # pause() or power_off() finishes. An unconditional "no contact"
        # write then replaces the truer value those wrote.
        inst = _FakeInstance(power_state='paused')
        with mock.patch.object(sidechannel.daemon, 'clear_abort_path'):
            sidechannel.SideChannelMonitorJob(inst)

        self.assertEqual(
            [constants.AGENT_INSTANCE_PAUSED], inst.agent_state_writes)

    def test_the_constructor_writes_no_contact_for_a_running_instance(self):
        inst = _FakeInstance()
        with mock.patch.object(sidechannel.daemon, 'clear_abort_path'):
            sidechannel.SideChannelMonitorJob(inst)

        self.assertEqual(
            [constants.AGENT_NEVER_TALKED], inst.agent_state_writes)


class _FakeLibvirtError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code

    def get_error_code(self):
        return self.code


class _FakeLibvirtModule:
    libvirtError = _FakeLibvirtError
    VIR_ERR_INTERNAL_ERROR = 1
    VIR_ERR_NO_DOMAIN = 42
    VIR_DOMAIN_RUNNING = 1
    VIR_DOMAIN_PAUSED = 3
    VIR_DOMAIN_PMSUSPENDED = 7
    VIR_DOMAIN_SHUTOFF = 5
    VIR_DOMAIN_CRASHED = 6


class _FakeDomain:
    def __init__(self, instance_uuid, state=_FakeLibvirtModule.VIR_DOMAIN_RUNNING,
                 error=None):
        self._name = f'sf:{instance_uuid}'
        self._state = state
        self._error = error

    def name(self):
        return self._name

    def state(self):
        if self._error:
            raise self._error
        return self._state, 0


class SideChannelMonitorAuditTestCase(base.ShakenFistTestCase):
    """The audit pass's walk over this node's running domains."""

    def _running(self, domains):
        lc = util_libvirt.LibvirtConnection()
        lc.libvirt = _FakeLibvirtModule()
        lc.get_active_sf_domains = mock.Mock(return_value=domains)
        m = sidechannel.Monitor.__new__(sidechannel.Monitor)
        return m._running_instance_uuids(lc)

    def test_running_domains_are_listed_and_stopped_ones_are_not(self):
        self.assertEqual(['a'], self._running([
            _FakeDomain('a'),
            _FakeDomain('b', state=_FakeLibvirtModule.VIR_DOMAIN_PAUSED)]))

    def test_a_domain_deleted_mid_audit_is_skipped_not_raised(self):
        """A delete can undefine a domain between the listing and the state
        read. The domains after it must still be audited."""
        gone = _FakeLibvirtError(
            'Domain not found', _FakeLibvirtModule.VIR_ERR_NO_DOMAIN)
        self.assertEqual(['b'], self._running([
            _FakeDomain('a', error=gone), _FakeDomain('b')]))

    def test_other_libvirt_errors_still_end_the_pass(self):
        broken = _FakeLibvirtError(
            'internal error', _FakeLibvirtModule.VIR_ERR_INTERNAL_ERROR)
        self.assertRaises(_FakeLibvirtError, self._running, [
            _FakeDomain('a', error=broken), _FakeDomain('b')])
