# Copyright 2019 Michael Still and contributors

import os
import shutil
import tempfile
import time
from unittest import mock

from shakenfist import exceptions
from shakenfist.daemons.sidechannel import main as sidechannel
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.tests import base


class _FakeState:
    def __init__(self, value):
        self.value = value


class _FakeAgentOp:
    """Stands in for an AgentOperation in the executor's tests.

    fail() and expire() reimplement the real terminal-state guard so
    that code under test behaves realistically when it calls them. That
    makes them fixture behaviour, not behaviour under test: the real
    guard is asserted against a real AgentOperation in
    test_agent_operation_expiry.py.

    That split has one maintenance obligation, recorded here because
    nothing enforces it: TERMINAL_STATES is borrowed from the real
    class but the shape of the guard is not, so if AgentOperation's
    guard grows a condition these fakes must grow it too. Until they
    do, the executor tests keep passing against behaviour production
    no longer has.
    """

    TERMINAL_STATES = AgentOperation.TERMINAL_STATES
    object_type = AgentOperation.object_type

    def __init__(self, state_value, commands=None, attempts=0,
                 deadline_passed=False):
        self.uuid = 'fake-agentop'
        self.instance_uuid = 'fake-instance'
        self.state = state_value

        # Abandoning an operation is only terminal when it cannot be
        # retried, so the default command list here is one which never
        # is: execute repeats a side effect the agent cannot take
        # back. Tests of the retry path pass a retryable list.
        if commands is None:
            commands = [{'command': 'execute'}]
        self.commands = list(commands)
        self.failure_reason = None
        self.expired_reason = None

        # Retry inputs. attempts is written on dispatch by the real
        # dispatcher, so a fake in the middle of its first attempt
        # carries 1, not 0.
        self.attempts = attempts
        self._deadline_passed = deadline_passed
        self.results = {'0': {'content_blob': 'an-abandoned-blob'}}
        self.results_cleared = False

    # The real state is a State object read as .value and assigned as a
    # bare string, so the fake accepts a string and reads back as an
    # object, exactly like the thing it stands in for.
    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = _FakeState(value)

    def fail(self, message):
        """Stands in for AgentOperation.fail(), guard included."""
        if self.state.value in self.TERMINAL_STATES:
            return
        self.state = AgentOperation.STATE_ERROR
        self.failure_reason = message

    def expire(self, reason):
        """Stands in for AgentOperation.expire(), guard included."""
        if self.state.value in self.TERMINAL_STATES:
            return
        self.state = AgentOperation.STATE_EXPIRED
        self.expired_reason = reason

    def deadline_passed(self, state=None):
        # The real signature takes an already-read State to anchor a
        # NULL deadline against, and the reaper passes one.
        return self._deadline_passed

    def clear_results(self):
        self.results = {}
        self.results_cleared = True

    def record_attempt(self):
        self.attempts += 1


class _FakeInstance:
    def __init__(self):
        self.uuid = 'fake-instance'


class _FakeStatResultReply:
    """Stands in for an AgentToHypervisor command with a stat_result field."""

    class _StatResult:
        size = 0
        mode = 0

    stat_result = _StatResult()


class ExecutorOrphanTestCase(base.ShakenFistTestCase):
    """SideChannelExecutorJob must not leave an operation orphaned in the
    executing state if it exits without completing it (issue #3516)."""

    def _make_executor(self, state_value):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = _FakeAgentOp(state_value)
        job.log = mock.MagicMock()
        return job

    def test_marks_error_when_exit_while_executing(self):
        job = self._make_executor(AgentOperation.STATE_EXECUTING)
        with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                               return_value=None):
            job.execute()
        self.assertEqual(AgentOperation.STATE_ERROR, job.agentop.state.value)
        self.assertEqual(
            'sidechannel executor exited before the operation completed, '
            'and the operation cannot be safely retried',
            job.agentop.failure_reason)

    def test_marks_error_when_exception_while_executing(self):
        job = self._make_executor(AgentOperation.STATE_EXECUTING)
        # An unexpected exception must still leave the op errored (and then
        # propagate for the usual exception tracking).
        with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                               side_effect=RuntimeError('boom')):
            self.assertRaises(RuntimeError, job.execute)
        self.assertEqual(AgentOperation.STATE_ERROR, job.agentop.state.value)

    def test_leaves_expired_op_untouched(self):
        # The invariant the whole executor design rests on:
        # expire_if_out_of_budget() writes the terminal state and
        # returns, and this finally block must not then overwrite it
        # with error. It holds because the guard tests == EXECUTING,
        # which is exactly the line a later change would widen to
        # "anything not complete".
        job = self._make_executor(AgentOperation.STATE_EXPIRED)
        with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                               return_value=None):
            job.execute()
        self.assertEqual(AgentOperation.STATE_EXPIRED, job.agentop.state.value)
        self.assertIsNone(job.agentop.failure_reason)

    def test_leaves_completed_op_untouched(self):
        job = self._make_executor(AgentOperation.STATE_COMPLETE)
        with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                               return_value=None):
            job.execute()
        # A completed op is not reassigned to error.
        self.assertEqual(AgentOperation.STATE_COMPLETE, job.agentop.state.value)
        self.assertIsNone(job.agentop.failure_reason)


class SideChannelPreConnectionWaitTestCase(base.ShakenFistTestCase):
    """The pre-connection wait in SideChannelJob.execute() must be
    abortable, or the reaper can resolve a wedged operation and still not
    free the instance's executor slot (step 5d)."""

    def _make_job(self, instance_path):
        # Built with __new__ rather than the real __init__ so abort_path
        # can point at a temp file instead of the real /run/sf location.
        job = sidechannel.SideChannelJob.__new__(sidechannel.SideChannelJob)
        job.instance = mock.Mock(instance_path=instance_path)
        job.abort_path = os.path.join(instance_path, 'abort')
        job.thread_name = 'fake-instance'
        job.log = mock.MagicMock()
        return job

    def test_wait_returns_promptly_when_aborted(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._make_job(tmp)

            def _set_abort_path_instead_of_sleeping(seconds):
                # Stands in for the reaper setting the abort path while
                # this thread is blocked in the wait.
                sidechannel.daemon.set_abort_path(job.abort_path, 'test')

            with mock.patch.object(
                    sidechannel.time, 'sleep',
                    side_effect=_set_abort_path_instead_of_sleeping) as m_sleep:
                job.execute()

            # Exactly one sleep: the loop notices the abort path on its very
            # next check, rather than looping again or falling through to
            # the vsock connection attempt.
            self.assertEqual(1, m_sleep.call_count)
            job.instance.socket_on_vsock_channel.assert_not_called()


class ExecutorGetFileGuardTestCase(base.ShakenFistTestCase):
    """The get-file transfer guards in _handle_stat_result() and
    _handle_file_chunk() must raise GetException when no transfer is in
    flight, not AttributeError -- which requires SideChannelExecutorJob.
    __init__() to have initialised the four get-file attributes to None."""

    def _make_executor(self):
        # Deliberately runs the real __init__ rather than building the object
        # with __new__ and assigning the attributes here. A hand-built object
        # would pass this test even if __init__ stopped setting them, which is
        # exactly the regression the test exists to catch. Only the abort path
        # is stubbed, because it writes to /run/sf.
        with mock.patch.object(sidechannel.daemon, 'clear_abort_path'):
            job = sidechannel.SideChannelExecutorJob(
                _FakeInstance(), _FakeAgentOp(AgentOperation.STATE_QUEUED))
        job.log = mock.MagicMock()
        return job

    def test_init_initialises_get_file_transfer_state(self):
        job = self._make_executor()
        self.assertIsNone(job._agent_path_for_get)
        self.assertIsNone(job._blob_uuid)
        self.assertIsNone(job._blob_partial_file)
        self.assertIsNone(job._stat_result)

    def test_handle_stat_result_raises_get_exception_not_attribute_error(self):
        job = self._make_executor()
        self.assertRaises(
            sidechannel.GetException, job._handle_stat_result,
            _FakeStatResultReply())


class AgentCommandHandlerTestCase(base.ShakenFistTestCase):
    """The command handler registry must cover exactly the commands the API
    builds, unambiguously, and every handler must actually dispatch."""

    # The complete set of agent commands constructed in
    # shakenfist/external_api/instance.py.
    API_COMMANDS = {'execute', 'put-blob', 'chmod', 'get-file'}

    def test_handlers_cover_the_api_commands(self):
        names = [cls.name for cls in sidechannel.AGENT_COMMAND_HANDLERS]
        self.assertEqual(self.API_COMMANDS, set(names))

    def test_handler_names_are_unique(self):
        names = [cls.name for cls in sidechannel.AGENT_COMMAND_HANDLERS]
        self.assertEqual(len(names), len(set(names)))

    def test_every_handler_overrides_dispatch(self):
        for cls in sidechannel.AGENT_COMMAND_HANDLERS:
            self.assertNotEqual(
                sidechannel.AgentCommandHandler.dispatch, cls.dispatch,
                f'{cls.__name__} does not override dispatch')

    def test_declared_capabilities(self):
        # Declared in phase 1 and consumed in phases 4 and 5. Nothing reads
        # these yet, so this test is the only thing holding them honest.
        expected = {
            # name: (reports_progress, retryable, register_as_outstanding)
            'execute': (False, False, False),
            'put-blob': (True, True, True),
            'chmod': (False, True, False),
            'get-file': (True, True, False)
        }
        actual = {
            cls.name: (cls.reports_progress, cls.retryable,
                       cls.register_as_outstanding)
            for cls in sidechannel.AGENT_COMMAND_HANDLERS
        }
        self.assertEqual(expected, actual)


class ExecutorTerminalStateGuardTestCase(base.ShakenFistTestCase):
    """The failure paths must tolerate an operation which has already
    reached a terminal state. Deadline enforcement can expire an
    operation underneath a caller, and expired has no edge to error, so
    an unguarded write raises InvalidStateException."""

    def _make_executor(self, state_value, commands):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = _FakeAgentOp(state_value)
        job.commands = list(commands)
        job._blob_partial_file = None
        job.log = mock.MagicMock()
        return job

    def test_commands_abort_when_errored(self):
        job = self._make_executor(
            AgentOperation.STATE_EXECUTING, [{'command': 'chmod'}])
        job.agentop.fail('transfer broke')
        job._abort_commands_if_terminal()
        self.assertEqual([], job.commands)

    def test_commands_abort_when_expired(self):
        # The point of the change: an operation whose caller ran out of
        # budget must drop its remaining commands the same way an
        # errored one does.
        job = self._make_executor(
            AgentOperation.STATE_EXECUTING, [{'command': 'chmod'}])
        job.agentop.expire('deadline passed')
        job._abort_commands_if_terminal()
        self.assertEqual([], job.commands)

    def test_commands_survive_a_healthy_operation(self):
        job = self._make_executor(
            AgentOperation.STATE_EXECUTING, [{'command': 'chmod'}])
        job._abort_commands_if_terminal()
        self.assertEqual([{'command': 'chmod'}], job.commands)


class _BudgetAgentOp(_FakeAgentOp):
    """A fake operation whose two timing budgets are directly settable."""

    def __init__(self, deadline_passed=False, progress_timeout=30.0,
                 commands=None, attempts=0):
        # A stall only reaches a terminal state when a retry is
        # impossible, so these fakes default to a command list which
        # cannot be retried. The retry path itself is exercised in
        # ExecutorRetryTestCase.
        super().__init__(
            AgentOperation.STATE_EXECUTING, commands=commands,
            attempts=attempts, deadline_passed=deadline_passed)
        self._progress_timeout = progress_timeout
        self.deadline_checks = 0

    def deadline_passed(self):
        self.deadline_checks += 1
        return self._deadline_passed

    def effective_progress_timeout(self):
        return self._progress_timeout


class _ProgressHandler:
    name = 'put-blob'
    reports_progress = True


class _SilentHandler:
    name = 'execute'
    reports_progress = False


class ExecutorBudgetTestCase(base.ShakenFistTestCase):
    """expire_if_out_of_budget() is what replaced the fixed 900 second
    backstop. It enforces two caller-set budgets and reports exhausting
    either as expired, distinguished by the message."""

    # A realistic timestamp, because the rate limit compares against
    # _last_budget_check and a "now" near zero is inside the interval
    # of the never-checked sentinel.
    NOW = 1700000000.0

    def _make_executor(self, agentop, handler=None, ready=False,
                       last_progress=0.0):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = agentop
        job.in_flight_handler = handler
        job.ready = ready
        job._last_progress = last_progress
        job._last_budget_check = 0.0
        job.commands = []
        job._blob_partial_file = None
        job.log = mock.MagicMock()
        return job

    def test_the_check_is_rate_limited(self):
        # The caller is the socket loop, which iterates once per packet
        # during a transfer. A NULL-deadline operation resolves its
        # default against self.state.update_time, which is an uncached
        # database read, so an unthrottled check is thousands of reads
        # a second.
        agentop = _BudgetAgentOp(deadline_passed=False)
        job = self._make_executor(agentop, handler=_SilentHandler())

        with mock.patch('time.time', return_value=self.NOW):
            self.assertFalse(job.expire_if_out_of_budget())
        self.assertEqual(1, agentop.deadline_checks)

        # Everything inside the interval is free.
        for offset in (0.1, 0.5, 0.9):
            with mock.patch('time.time', return_value=self.NOW + offset):
                self.assertFalse(job.expire_if_out_of_budget())
        self.assertEqual(1, agentop.deadline_checks)

        # Past it, one more check.
        with mock.patch('time.time', return_value=self.NOW + 1.5):
            self.assertFalse(job.expire_if_out_of_budget())
        self.assertEqual(2, agentop.deadline_checks)

    def test_passed_deadline_expires_and_stops(self):
        agentop = _BudgetAgentOp(deadline_passed=True)
        job = self._make_executor(agentop)
        self.assertTrue(job.expire_if_out_of_budget())
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            'the operation deadline passed while executing',
            agentop.expired_reason)

    def test_stalled_progress_expires_and_stops(self):
        # The fake operation is an execute, which is never retried, so
        # the stall is terminal here and the message names both what
        # happened and why no retry followed. ExecutorRetryTestCase
        # owns the case where it is retried instead.
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        job = self._make_executor(
            agentop, handler=_ProgressHandler(), last_progress=0.0)
        with mock.patch('time.time', return_value=31.0):
            self.assertTrue(job.expire_if_out_of_budget())
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            'no progress from the agent for 30 seconds, and the '
            'operation cannot be safely retried',
            agentop.expired_reason)

    def test_progress_inside_the_window_continues(self):
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        job = self._make_executor(
            agentop, handler=_ProgressHandler(), last_progress=0.0)
        with mock.patch('time.time', return_value=29.0):
            self.assertFalse(job.expire_if_out_of_budget())
        self.assertEqual(AgentOperation.STATE_EXECUTING, agentop.state.value)

    def test_a_command_which_cannot_report_progress_is_never_stalled(self):
        # An execute of a long running command must survive far longer
        # than the progress timeout, because it reports nothing until
        # it is done.
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        job = self._make_executor(
            agentop, handler=_SilentHandler(), last_progress=0.0)
        with mock.patch('time.time', return_value=100000.0):
            self.assertFalse(job.expire_if_out_of_budget())
        self.assertEqual(AgentOperation.STATE_EXECUTING, agentop.state.value)

    def test_nothing_in_flight_is_never_stalled(self):
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        job = self._make_executor(agentop, handler=None, last_progress=0.0)
        with mock.patch('time.time', return_value=100000.0):
            self.assertFalse(job.expire_if_out_of_budget())

    def test_a_ready_executor_is_never_stalled(self):
        # Ready means the command completed and we are between
        # commands; there is nothing to be waiting on.
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        job = self._make_executor(
            agentop, handler=_ProgressHandler(), ready=True,
            last_progress=0.0)
        with mock.patch('time.time', return_value=100000.0):
            self.assertFalse(job.expire_if_out_of_budget())

    def test_a_disabled_progress_timeout_is_never_stalled(self):
        # The caller passed progress_timeout_seconds=0.
        agentop = _BudgetAgentOp(progress_timeout=None)
        job = self._make_executor(
            agentop, handler=_ProgressHandler(), last_progress=0.0)
        with mock.patch('time.time', return_value=100000.0):
            self.assertFalse(job.expire_if_out_of_budget())

    def test_the_deadline_is_checked_before_progress(self):
        # A passed deadline must win, so the recorded reason names the
        # budget which actually ran out first.
        agentop = _BudgetAgentOp(deadline_passed=True, progress_timeout=30.0)
        job = self._make_executor(
            agentop, handler=_ProgressHandler(), last_progress=0.0)
        with mock.patch('time.time', return_value=100000.0):
            self.assertTrue(job.expire_if_out_of_budget())
        self.assertEqual(
            'the operation deadline passed while executing',
            agentop.expired_reason)


class ExecutorProgressPersistenceTestCase(base.ShakenFistTestCase):
    """observe_progress() moves an in-memory timestamp on every call and
    persists it at most once per PROGRESS_PERSIST_INTERVAL.

    The write itself lives on AgentOperation.record_progress(); the
    field mask it carries is asserted in test_agent_operation_expiry.py.
    """

    def _make_executor(self):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = mock.MagicMock()
        job._last_progress = 0.0
        job._last_progress_persisted = 0.0
        job.log = mock.MagicMock()
        return job

    # A real timestamp, because the throttle compares against
    # _last_progress_persisted and a "now" near zero is inside the
    # interval of the never-persisted sentinel.
    NOW = 1700000000.0

    def test_in_memory_timestamp_moves_on_every_call(self):
        job = self._make_executor()
        for offset in (0.0, 1.0, 2.0):
            with mock.patch('time.time', return_value=self.NOW + offset):
                job.observe_progress()
            self.assertEqual(self.NOW + offset, job._last_progress)

    def test_persistence_is_throttled(self):
        job = self._make_executor()

        # The first call persists (this row has never been written),
        # then everything inside the interval is in-memory only. A
        # 100KiB-chunk transfer calls this hundreds of times a second,
        # which is the whole point.
        for offset in (0.0, 1.0, 4.0, 9.0):
            with mock.patch('time.time', return_value=self.NOW + offset):
                job.observe_progress()
        self.assertEqual(1, job.agentop.record_progress.call_count)

        # Past the interval, one more write.
        with mock.patch('time.time', return_value=self.NOW + 11.0):
            job.observe_progress()
        self.assertEqual(2, job.agentop.record_progress.call_count)

    def test_the_persisted_value_is_the_observed_time(self):
        job = self._make_executor()
        with mock.patch('time.time', return_value=self.NOW):
            job.observe_progress()
        job.agentop.record_progress.assert_called_once_with(self.NOW)

    def test_a_failed_write_does_not_reach_the_caller(self):
        # The persist is bookkeeping for a reaper which does not exist
        # yet. Without the guard a DatabaseUnavailable here propagates
        # out of the reply handler and out of _execute_inner(), and
        # execute()'s finally block turns a healthy transfer into an
        # errored operation.
        job = self._make_executor()
        job.agentop.record_progress.side_effect = exceptions.DatabaseUnavailable(
            'no database for you')

        with mock.patch('time.time', return_value=self.NOW):
            job.observe_progress()

        # The in-memory value, which is what enforcement actually reads,
        # still moved.
        self.assertEqual(self.NOW, job._last_progress)

    def test_a_failed_write_is_retried_on_the_next_call(self):
        # The throttle stamp moves only on success, so a blip costs one
        # write rather than a whole interval of them.
        job = self._make_executor()
        job.agentop.record_progress.side_effect = exceptions.DatabaseUnavailable(
            'no database for you')
        with mock.patch('time.time', return_value=self.NOW):
            job.observe_progress()

        job.agentop.record_progress.side_effect = None
        with mock.patch('time.time', return_value=self.NOW + 0.1):
            job.observe_progress()
        self.assertEqual(2, job.agentop.record_progress.call_count)


class _SlowProgressHandler:
    """A handler whose dispatch() takes longer than the progress window."""

    name = 'put-blob'
    reports_progress = True
    register_as_outstanding = True

    def __init__(self, clock, duration):
        self.clock = clock
        self.duration = duration

    def dispatch(self, command_id, cmd):
        # Stands in for PutBlobCommand.dispatch() calling
        # Blob.ensure_local(), which fetches the blob from another node
        # if preflight did not already.
        self.clock.now += self.duration
        return ['a-request']


class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class ExecutorDispatchWindowTestCase(base.ShakenFistTestCase):
    """The progress window starts when the command reaches the wire.

    Seeding it before handler.dispatch() means a put-blob whose blob
    has to be fetched from another node is expired before the agent has
    been sent anything, with a message blaming the agent for a delay
    which was entirely hypervisor side.
    """

    NOW = 1700000000.0

    def _make_executor(self, agentop, handler, cmd):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = agentop
        job.instance = _FakeInstance()
        job.affected_objects = [job.instance, agentop]
        job.commands = [cmd]
        job.command_handlers = {handler.name: handler}
        job.in_flight_handler = None
        job.outstanding_message_count = 0
        job.ready = True
        job._last_progress = 0.0
        job._last_budget_check = 0.0
        job._blob_partial_file = None
        job.log = mock.MagicMock()
        job._send_commands_single_envelope = mock.MagicMock()
        return job

    def test_a_slow_dispatch_does_not_start_the_window(self):
        clock = _Clock(self.NOW)
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        handler = _SlowProgressHandler(clock, 300.0)
        job = self._make_executor(
            agentop, handler, {'command': 'put-blob', 'blob_uuid': 'b1'})

        with mock.patch('time.time', clock):
            with mock.patch.object(sidechannel, 'add_event_multi'):
                job._dispatch_next_command(mock.MagicMock())

            # Five minutes of blob copying happened inside dispatch, but
            # the window starts from the send.
            self.assertEqual(self.NOW + 300.0, job._last_progress)

            # So the very next loop iteration must not expire it.
            self.assertFalse(job.expire_if_out_of_budget())

        self.assertEqual(AgentOperation.STATE_EXECUTING, agentop.state.value)

    def test_the_window_still_expires_a_genuinely_stalled_agent(self):
        # The other half: once the command is on the wire, silence from
        # the agent for longer than the window is still fatal.
        clock = _Clock(self.NOW)
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        handler = _SlowProgressHandler(clock, 300.0)
        job = self._make_executor(
            agentop, handler, {'command': 'put-blob', 'blob_uuid': 'b1'})

        with mock.patch('time.time', clock):
            with mock.patch.object(sidechannel, 'add_event_multi'):
                job._dispatch_next_command(mock.MagicMock())

            clock.now += 31.0
            self.assertTrue(job.expire_if_out_of_budget())

        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            'no progress from the agent for 30 seconds, and the '
            'operation cannot be safely retried',
            agentop.expired_reason)


class _FakeCommandError:
    def __init__(self, error):
        self.error = error
        self.last_envelope = None


class _FakeCommandErrorReply:
    def __init__(self, error):
        self.command_error = _FakeCommandError(error)


class ExecutorCommandErrorTestCase(base.ShakenFistTestCase):
    """An agent-reported command error is an error, not an expiry.

    The base handler only emits an event, which leaves the operation
    executing with a command in flight. Before deadlines were enforced
    the 900 second backstop eventually tidied that up as an error;
    without the override the next thing to notice would be a timing
    budget, and the operation would land in expired with a message
    blaming the agent for silence it did not commit.
    """

    def _make_executor(self, state_value, commands):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = _FakeAgentOp(state_value)
        job.instance = _FakeInstance()
        job.instance.add_event = mock.MagicMock()
        job.commands = list(commands)
        job.outstanding_message_count = 1
        job.ready = False
        job._blob_partial_file = None
        job.log = mock.MagicMock()
        return job

    def _handle(self, job, error='no such file'):
        with mock.patch.object(sidechannel, 'MessageToDict', return_value={}):
            job._handle_command_error(_FakeCommandErrorReply(error))

    def test_a_command_error_fails_the_operation(self):
        job = self._make_executor(
            AgentOperation.STATE_EXECUTING, [{'command': 'chmod'}])
        self._handle(job)
        self.assertEqual(AgentOperation.STATE_ERROR, job.agentop.state.value)
        self.assertEqual(
            'agent reported a command error: no such file',
            job.agentop.failure_reason)

    def test_the_remaining_commands_are_dropped(self):
        job = self._make_executor(
            AgentOperation.STATE_EXECUTING, [{'command': 'chmod'}])
        self._handle(job)
        self.assertEqual([], job.commands)

    def test_the_executor_is_released_rather_than_left_to_a_budget(self):
        # ready plus an empty command list is the socket loop's
        # ordinary exit. Without it the loop spins until a deadline,
        # which is the path that produced the wrong terminal state.
        job = self._make_executor(
            AgentOperation.STATE_EXECUTING, [{'command': 'chmod'}])
        self._handle(job)
        self.assertTrue(job.ready)

    def test_the_event_is_still_emitted(self):
        job = self._make_executor(
            AgentOperation.STATE_EXECUTING, [{'command': 'chmod'}])
        self._handle(job)
        job.instance.add_event.assert_called_once()

    def test_an_already_expired_operation_is_not_rewritten(self):
        # fail()'s guard. A budget can expire the operation between the
        # agent sending its error and us reading it.
        job = self._make_executor(AgentOperation.STATE_EXPIRED, [])
        self._handle(job)
        self.assertEqual(AgentOperation.STATE_EXPIRED, job.agentop.state.value)
        self.assertIsNone(job.agentop.failure_reason)


class ExecutorPartialBlobTeardownTestCase(base.ShakenFistTestCase):
    """Abandoning an operation must not abandon its .partial file.

    GetFileCommand.dispatch() opens one and hands the job ownership.
    The cleaner sweeps orphans eventually, so this was survivable when
    only a 900 second backstop reached it; the progress timeout reaches
    it in tens of seconds.
    """

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir)

        self.filepath = mock.patch.object(
            sidechannel.blob.Blob, 'filepath',
            side_effect=lambda u: os.path.join(self.tempdir, str(u)))
        self.filepath.start()
        self.addCleanup(self.filepath.stop)

    def _make_executor(self, agentop, in_flight=True):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = agentop
        job.commands = []
        job.in_flight_handler = _ProgressHandler()
        job.ready = False
        job._last_progress = 0.0
        job._last_budget_check = 0.0
        job._blob_uuid = None
        job._stat_result = None
        job._agent_path_for_get = None
        job._blob_partial_file = None
        job.log = mock.MagicMock()

        if in_flight:
            job._blob_uuid = 'a-blob'
            job._stat_result = {'size': 10}
            job._agent_path_for_get = '/etc/hosts'
            job._blob_partial_file = open(self.partial_path, 'wb')

        return job

    @property
    def partial_path(self):
        return os.path.join(self.tempdir, 'a-blob') + '.partial'

    def test_expiry_closes_and_removes_the_partial_file(self):
        agentop = _BudgetAgentOp(deadline_passed=True)
        job = self._make_executor(agentop)
        self.assertTrue(os.path.exists(self.partial_path))

        self.assertTrue(job.expire_if_out_of_budget())

        self.assertFalse(os.path.exists(self.partial_path))
        self.assertIsNone(job._blob_partial_file)
        self.assertIsNone(job._blob_uuid)
        self.assertIsNone(job._stat_result)
        self.assertIsNone(job._agent_path_for_get)

    def test_a_progress_timeout_removes_it_too(self):
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        job = self._make_executor(agentop)
        with mock.patch('time.time', return_value=31.0):
            self.assertTrue(job.expire_if_out_of_budget())
        self.assertFalse(os.path.exists(self.partial_path))

    def test_aborting_a_failed_operation_removes_it(self):
        agentop = _BudgetAgentOp()
        job = self._make_executor(agentop)
        agentop.fail('transfer broke')
        job._abort_commands_if_terminal()
        self.assertFalse(os.path.exists(self.partial_path))

    def test_no_transfer_in_flight_is_a_noop(self):
        job = self._make_executor(_BudgetAgentOp(), in_flight=False)
        job._abandon_get_file_transfer()
        self.assertIsNone(job._blob_partial_file)

    def test_a_failure_to_unlink_is_not_fatal(self):
        # This runs while an operation is already being abandoned, so
        # it must never be the thing that raises.
        job = self._make_executor(_BudgetAgentOp())
        with mock.patch.object(sidechannel.os, 'unlink',
                               side_effect=OSError('nope')):
            job._abandon_get_file_transfer()
        self.assertIsNone(job._blob_partial_file)
        job.log.with_fields.return_value.warning.assert_called_once()


class _DispatchHandler:
    """A handler whose dispatch() always produces one request."""

    name = 'execute'
    reports_progress = False
    register_as_outstanding = False

    def dispatch(self, command_id, cmd):
        return ['a-request']


class ExecutorDispatchTerminalGuardTestCase(base.ShakenFistTestCase):
    """A dispatch must not walk an edge the state machine forbids.

    An old dispatcher generation can call agent_operation_next() after
    a replacement has taken over an instance, and since this phase that
    call expires an over-deadline queued head rather than reading it
    again. The executor's next transition to executing would then raise
    InvalidStateException out of the thread.
    """

    def _make_executor(self, state_value):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = _FakeAgentOp(state_value)
        job.instance = _FakeInstance()
        job.affected_objects = [job.instance, job.agentop]
        job.commands = [{'command': 'execute', 'commandline': 'true'}]
        job.command_handlers = {'execute': _DispatchHandler()}
        job.in_flight_handler = None
        job.outstanding_message_count = 0
        job.ready = True
        job._last_progress = 0.0
        job._last_budget_check = 0.0
        job._blob_partial_file = None
        job.log = mock.MagicMock()
        job._send_commands_single_envelope = mock.MagicMock()
        return job

    def test_an_expired_operation_is_not_dispatched(self):
        job = self._make_executor(AgentOperation.STATE_EXPIRED)
        with mock.patch.object(sidechannel, 'add_event_multi'):
            job._dispatch_next_command(mock.MagicMock())

        job._send_commands_single_envelope.assert_not_called()
        self.assertEqual(AgentOperation.STATE_EXPIRED, job.agentop.state.value)
        self.assertEqual([], job.commands)

    def test_a_deleted_operation_lets_the_loop_exit(self):
        # deleted and complete are terminal but are not what
        # _abort_commands_if_terminal() tests, so the guard has to drop
        # the commands itself. ready plus an empty command list is the
        # socket loop's exit; leaving commands behind would re-dispatch
        # on every pass forever.
        job = self._make_executor('deleted')
        with mock.patch.object(sidechannel, 'add_event_multi'):
            job._dispatch_next_command(mock.MagicMock())

        job._send_commands_single_envelope.assert_not_called()
        self.assertEqual([], job.commands)
        self.assertTrue(job.ready)

    def test_a_healthy_operation_is_dispatched(self):
        job = self._make_executor(AgentOperation.STATE_QUEUED)
        with mock.patch.object(sidechannel, 'add_event_multi'):
            job._dispatch_next_command(mock.MagicMock())

        job._send_commands_single_envelope.assert_called_once()
        self.assertEqual(
            AgentOperation.STATE_EXECUTING, job.agentop.state.value)


class OperationRetryabilityTestCase(base.ShakenFistTestCase):
    """Retryability is a property of the whole command list.

    A retry restarts the list at index 0, so a list containing any
    command which cannot be repeated must not be retried at all --
    even when the command which stalled could have been. No API
    endpoint builds a mixed list today, which is exactly why this is
    tested here rather than left to be discovered when one does.
    """

    def _op(self, *names):
        return _FakeAgentOp(
            AgentOperation.STATE_EXECUTING,
            commands=[{'command': n} for n in names])

    def test_a_retryable_list_is_retryable(self):
        self.assertTrue(
            sidechannel.operation_is_retryable(self._op('put-blob', 'chmod')))
        self.assertTrue(
            sidechannel.operation_is_retryable(self._op('get-file')))

    def test_execute_is_not_retryable(self):
        self.assertFalse(
            sidechannel.operation_is_retryable(self._op('execute')))

    def test_an_empty_list_is_not_retryable(self):
        # all() over an empty list is True, which would make an
        # operation with nothing to run retryable. A second attempt at
        # nothing cannot make progress, so it would burn dispatches to
        # the cap and then report a timing budget as the reason.
        self.assertFalse(sidechannel.operation_is_retryable(self._op()))

    def test_a_mixed_list_is_not_retryable_in_either_order(self):
        # The decision this test exists for. Retrying the second list
        # would re-run the execute, which is the side effect the agent
        # cannot take back.
        self.assertFalse(
            sidechannel.operation_is_retryable(
                self._op('execute', 'get-file')))
        self.assertFalse(
            sidechannel.operation_is_retryable(
                self._op('get-file', 'execute')))

    def test_an_unknown_command_is_not_retryable(self):
        # We cannot know what running it a second time would do.
        self.assertFalse(
            sidechannel.operation_is_retryable(self._op('no-such-command')))


class ExecutorRetryTestCase(base.ShakenFistTestCase):
    """resolve_abandoned_operation() is the only place the retry decision
    is made, for the executor and for the reaper alike.

    Retry is for a stalled attempt and never for a failed one, so the
    three refusals -- not retryable, deadline passed, attempts
    exhausted -- each say so in the message the operator reads back
    from object_states.
    """

    STALL = 'no progress from the agent for 30 seconds'
    EXIT = 'sidechannel executor exited before the operation completed'

    def _op(self, commands=None, attempts=1, deadline_passed=False):
        return _FakeAgentOp(
            AgentOperation.STATE_EXECUTING, commands=commands,
            attempts=attempts, deadline_passed=deadline_passed)

    def _resolve(self, agentop, reason, terminal):
        with mock.patch.object(sidechannel, 'add_event_multi') as event:
            retried = sidechannel.resolve_abandoned_operation(
                agentop, reason, terminal=terminal)
        return retried, event

    def test_a_stall_under_the_cap_requeues_and_clears_results(self):
        agentop = self._op(commands=[{'command': 'get-file'}], attempts=1)
        retried, event = self._resolve(
            agentop, self.STALL, agentop.expire)

        self.assertTrue(retried)
        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        self.assertIsNone(agentop.expired_reason)

        # The abandoned attempt registered a blob nothing else
        # references. Leaving its result would hand the caller a
        # content_blob from an attempt which never finished.
        self.assertTrue(agentop.results_cleared)
        self.assertEqual({}, agentop.results)

        # One audit event, carrying why and which attempt.
        event.assert_called_once()
        extra = event.call_args[1]['extra']
        self.assertEqual(self.STALL, extra['reason'])
        self.assertEqual(1, extra['attempts'])

    def test_the_cap_expires_with_a_message_naming_it(self):
        # A stall which runs out of attempts expires rather than
        # errors, because the progress timeout is a budget the caller
        # set. The count is in the message so an operator can tell
        # "stalled once and gave up" from "stalled three times".
        agentop = self._op(commands=[{'command': 'get-file'}], attempts=3)
        retried, _ = self._resolve(agentop, self.STALL, agentop.expire)

        self.assertFalse(retried)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(f'{self.STALL}, after 3 attempts',
                         agentop.expired_reason)
        self.assertFalse(agentop.results_cleared)

    def test_a_passed_deadline_expires_even_with_attempts_left(self):
        # Retrying would spend time nobody is waiting for: the
        # caller's budget is the thing which just ran out.
        agentop = self._op(commands=[{'command': 'get-file'}], attempts=1,
                           deadline_passed=True)
        retried, _ = self._resolve(agentop, self.STALL, agentop.expire)

        self.assertFalse(retried)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            f'{self.STALL}, and the operation deadline has passed',
            agentop.expired_reason)

    def test_an_execute_operation_never_retries(self):
        agentop = self._op(commands=[{'command': 'execute'}], attempts=1)
        retried, _ = self._resolve(agentop, self.STALL, agentop.expire)

        self.assertFalse(retried)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            f'{self.STALL}, and the operation cannot be safely retried',
            agentop.expired_reason)

    def test_a_stalled_transfer_is_requeued_by_the_budget_check(self):
        # The same decision reached through the executor's progress
        # stall branch, which must still stop the executor: a requeued
        # operation is dispatched afresh by a new one.
        agentop = _BudgetAgentOp(
            progress_timeout=30.0, commands=[{'command': 'get-file'}],
            attempts=1)
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = agentop
        job.in_flight_handler = _ProgressHandler()
        job.ready = False
        job._last_progress = 0.0
        job._last_budget_check = 0.0
        job.commands = []
        job._blob_partial_file = None
        job.log = mock.MagicMock()

        with mock.patch.object(sidechannel, 'add_event_multi'):
            with mock.patch('time.time', return_value=31.0):
                self.assertTrue(job.expire_if_out_of_budget())

        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        self.assertIsNone(agentop.expired_reason)

    def test_an_executor_exit_retries_to_the_cap_and_then_errors(self):
        # The whole loop, one dispatch at a time. attempts is written
        # on dispatch, so the first executor to exit sees 1.
        agentop = self._op(commands=[{'command': 'get-file'}], attempts=0)
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = agentop
        job.log = mock.MagicMock()

        cap = sidechannel.config.AGENT_OPERATION_MAX_ATTEMPTS
        states = []
        for _ in range(cap):
            agentop.attempts += 1
            agentop.state = AgentOperation.STATE_EXECUTING
            with mock.patch.object(sidechannel, 'add_event_multi'):
                with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                                       return_value=None):
                    job.execute()
            states.append(agentop.state.value)

        self.assertEqual(
            [AgentOperation.STATE_QUEUED] * (cap - 1)
            + [AgentOperation.STATE_ERROR], states)
        self.assertEqual(
            f'{self.EXIT}, after {cap} attempts', agentop.failure_reason)


class _DispatchMonitor:
    """Stands in for self inside Monitor.start_instance_executor().

    The method only touches these three dictionaries, and constructing
    a real Monitor would start a daemon. Calling the unbound method
    with this keeps the test to the one method under test, the same
    way PreflightDeadlineTestCase._FakeClusterOp does in
    test_node_aop_op.py.
    """

    def __init__(self):
        self.executor_attempts = {}
        self.monitors = {}
        self.executors = {}


class DispatchRecordsAnAttemptTestCase(base.ShakenFistTestCase):
    """Dispatch is what counts an attempt.

    Nothing else writes the attempts counter, so if this call is lost
    the counter stays at zero, every comparison against
    AGENT_OPERATION_MAX_ATTEMPTS is false, and a stalling operation
    retries until its deadline -- or forever, for the deadline_seconds
    of 0 case the cap exists to be the only bound on.
    """

    def _dispatch(self, agentop):
        mon = _DispatchMonitor()
        mon.monitors['inst'] = {'object': mock.Mock()}

        with mock.patch.object(sidechannel.instance.Instance, 'from_db',
                               return_value=mock.Mock()), \
                mock.patch.object(sidechannel, 'SideChannelExecutorJob'), \
                mock.patch.object(sidechannel.threading, 'Thread'), \
                mock.patch.object(sidechannel, 'add_event'):
            sidechannel.Monitor.start_instance_executor(mon, 'inst', agentop)
        return mon

    def test_dispatch_records_an_attempt(self):
        agentop = _FakeAgentOp(AgentOperation.STATE_QUEUED)
        self._dispatch(agentop)
        self.assertEqual(1, agentop.attempts)

    def test_each_dispatch_records_another(self):
        agentop = _FakeAgentOp(AgentOperation.STATE_QUEUED)
        self._dispatch(agentop)
        self._dispatch(agentop)
        self._dispatch(agentop)
        self.assertEqual(3, agentop.attempts)

    def test_an_instance_with_no_monitor_records_nothing(self):
        # The early return above the counter. Dispatch did not happen,
        # so it must not be counted against the operation's cap.
        agentop = _FakeAgentOp(AgentOperation.STATE_QUEUED)
        mon = _DispatchMonitor()

        with mock.patch.object(sidechannel.instance.Instance, 'from_db',
                               return_value=mock.Mock()), \
                mock.patch.object(sidechannel, 'SideChannelExecutorJob'), \
                mock.patch.object(sidechannel.threading, 'Thread'), \
                mock.patch.object(sidechannel, 'add_event'):
            sidechannel.Monitor.start_instance_executor(mon, 'inst', agentop)

        self.assertEqual(0, agentop.attempts)


class _QueuedInstance:
    """An instance whose agent operation queue can be peeked at.

    The peek count is asserted on, because the peek being the *only*
    database read the reaper performs for an idle instance is the
    property which keeps it affordable at the top of every dispatcher
    pass, for every instance on the node.
    """

    def __init__(self, queue=None):
        self.uuid = 'fake-instance'
        self._queue = list(queue or [])
        self.peeks = 0

    @property
    def agent_operations(self):
        self.peeks += 1
        return {'queue': list(self._queue)}


class _ReaperMonitor:
    """Stands in for self inside Monitor.reap_instance_executors().

    The method only touches these two dictionaries, and constructing a
    real Monitor would start a daemon, so the unbound method is called
    with this instead -- the same approach _DispatchMonitor takes above.
    """

    # The reaper calls exactly one method on itself, so the fake borrows
    # the real one rather than standing in for it: the per-instance
    # decisions are the behaviour under test.
    _resolve_stuck_queue_head = sidechannel.Monitor._resolve_stuck_queue_head

    def __init__(self):
        self.monitors = {}
        self.executors = {}
        self.reaper_attempts = {}


class ExecutorReaperTestCase(base.ShakenFistTestCase):
    """The reaper is what turns terminal-only pop back into a drain.

    An operation's queue entry now survives until the operation is
    terminal, so an operation left in executing with nothing working on
    it blocks its instance's queue for as long as it stays there. This
    node can see what the queue cannot: the instance is placed here, its
    executor is a thread in this process, and the absence of that thread
    is direct evidence rather than an inference.

    Three cases and no more. Two are the absence of a thread; the third
    is a wall-clock deadline, which is an absolute timestamp and so
    cannot be wrong about a thread which is still making progress. A
    live executor inside its budgets is left entirely alone.
    """

    INSTANCE = 'fake-instance'

    def _monitor(self, queue=(), executor_alive=None):
        """A reaper self with one monitored instance, and maybe an executor.

        executor_alive is None for no executor entry at all (the daemon
        restart case), True for a live one, and False for a thread which
        the sweep at the top of the reaper will remove first.
        """
        mon = _ReaperMonitor()
        inst = _QueuedInstance(queue)
        mon.monitors[self.INSTANCE] = {
            'object': mock.Mock(instance=inst),
            'thread': mock.Mock(),
            'instance_uuid': self.INSTANCE
        }

        if executor_alive is not None:
            thread = mock.Mock()
            thread.is_alive.return_value = executor_alive
            mon.executors[self.INSTANCE] = {
                'object': mock.Mock(abort_path='/run/sf/an.abort'),
                'thread': thread,
                'instance_uuid': self.INSTANCE
            }

        return mon, inst

    def _reap(self, mon, agentop=None):
        with mock.patch.object(sidechannel.AgentOperation, 'from_db',
                               return_value=agentop) as from_db, \
                mock.patch.object(sidechannel, 'add_event'), \
                mock.patch.object(sidechannel, 'add_event_multi'), \
                mock.patch.object(sidechannel.daemon,
                                  'set_abort_path') as abort:
            sidechannel.Monitor.reap_instance_executors(mon)
        return from_db, abort

    def test_a_daemon_restart_drains_the_queue(self):
        # The case decision 1 names: the daemon died with an operation
        # executing, so no finally block ever ran and there is no
        # executor entry to find. One pass must leave the operation in a
        # state Instance.agent_operation_next() will pop, or the
        # instance's queue is wedged forever.
        agentop = _FakeAgentOp(AgentOperation.STATE_EXECUTING)
        mon, _ = self._monitor(queue=['an-operation'])

        self._reap(mon, agentop)

        self.assertIn(agentop.state.value, AgentOperation.TERMINAL_STATES)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            'no sidechannel executor was running for this operation, and '
            'the operation cannot be safely retried',
            agentop.expired_reason)

    def test_a_retryable_operation_is_requeued_instead(self):
        # The same case for an operation which can be run again: it
        # returns to the queue rather than to a terminal state, and the
        # dispatcher picks it up on a later pass. The queue drains
        # either way, which is the point.
        agentop = _FakeAgentOp(
            AgentOperation.STATE_EXECUTING, commands=[{'command': 'get-file'}],
            attempts=1)
        mon, _ = self._monitor(queue=['an-operation'])

        self._reap(mon, agentop)

        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        self.assertTrue(agentop.results_cleared)

    def test_a_dead_thread_is_swept_and_resolved_in_one_pass(self):
        # The sweep runs first deliberately: an executor whose thread
        # ended without resolving its operation is handled in the same
        # pass rather than the next one.
        agentop = _FakeAgentOp(AgentOperation.STATE_EXECUTING)
        mon, _ = self._monitor(queue=['an-operation'], executor_alive=False)

        self._reap(mon, agentop)

        self.assertEqual({}, mon.executors)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)

    def test_an_idle_instance_reads_no_operation(self):
        # An instance with nothing queued costs one attributes peek and
        # nothing else. Reading an operation here would put a database
        # read on the idle path for every instance on the node, on every
        # dispatcher pass.
        mon, inst = self._monitor(queue=[])

        from_db, abort = self._reap(mon)

        from_db.assert_not_called()
        abort.assert_not_called()
        self.assertEqual(1, inst.peeks)

    def test_a_wedged_live_executor_is_resolved_then_aborted(self):
        # A live executor whose operation is out of wall-clock budget is
        # wedged somewhere no budget is evaluated -- the pre-connection
        # wait, which blocks before _execute_inner() is entered. The
        # order matters: the executor's finally block only rewrites an
        # operation which is still executing, so resolving first means
        # the thread being stopped cannot overwrite this verdict.
        agentop = _FakeAgentOp(
            AgentOperation.STATE_EXECUTING, commands=[{'command': 'get-file'}],
            attempts=1, deadline_passed=True)
        mon, _ = self._monitor(queue=['an-operation'], executor_alive=True)

        order = []
        resolve = agentop.expire

        def _expire(reason):
            order.append('resolve')
            resolve(reason)

        agentop.expire = _expire

        with mock.patch.object(sidechannel.AgentOperation, 'from_db',
                               return_value=agentop), \
                mock.patch.object(sidechannel, 'add_event'), \
                mock.patch.object(sidechannel, 'add_event_multi'), \
                mock.patch.object(
                    sidechannel.daemon, 'set_abort_path',
                    side_effect=lambda *a: order.append('abort')) as abort:
            sidechannel.Monitor.reap_instance_executors(mon)

        self.assertEqual(['resolve', 'abort'], order)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            'the sidechannel executor was wedged and made no progress, and '
            'the operation deadline has passed',
            agentop.expired_reason)
        self.assertEqual('/run/sf/an.abort', abort.call_args[0][0])

    def test_a_live_executor_inside_its_budget_is_left_alone(self):
        # The reaper acts on evidence, never on suspicion. A progress
        # stall is the executor's own job: it holds state the reaper
        # does not, and second-guessing it would race a thread which is
        # making progress.
        agentop = _FakeAgentOp(
            AgentOperation.STATE_EXECUTING, commands=[{'command': 'get-file'}],
            attempts=1, deadline_passed=False)
        mon, _ = self._monitor(queue=['an-operation'], executor_alive=True)

        _, abort = self._reap(mon, agentop)

        self.assertEqual(AgentOperation.STATE_EXECUTING, agentop.state.value)
        self.assertFalse(agentop.results_cleared)
        abort.assert_not_called()
        self.assertIn(self.INSTANCE, mon.executors)

    def test_a_head_which_is_not_executing_is_left_alone(self):
        # A queued head belongs to the dispatcher, and a preflight or
        # initial one to the operation which is still creating it.
        for state in (AgentOperation.STATE_QUEUED,
                      AgentOperation.STATE_PREFLIGHT):
            agentop = _FakeAgentOp(state)
            mon, _ = self._monitor(queue=['an-operation'])

            _, abort = self._reap(mon, agentop)

            self.assertEqual(state, agentop.state.value)
            abort.assert_not_called()

    def test_the_peek_is_rate_limited(self):
        # The reaper runs at the top of every dispatcher pass, so an
        # unthrottled peek would be one uncached attributes read per
        # instance per second -- the cost the dispatch check's own rate
        # limit exists to avoid. A restarted daemon starts with an empty
        # dictionary, so the first pass is never delayed.
        mon, inst = self._monitor(queue=[])

        self._reap(mon)
        self._reap(mon)
        self._reap(mon)
        self.assertEqual(1, inst.peeks)

        mon.reaper_attempts[self.INSTANCE] = (
            time.time() - sidechannel.EXECUTOR_REAP_INTERVAL - 1)
        self._reap(mon)
        self.assertEqual(2, inst.peeks)

    def test_an_invalid_queue_entry_is_left_for_the_dispatcher(self):
        # Retiring it takes the instance's attribute lock, which the
        # reaper deliberately does not; agent_operation_next() does it
        # on the dispatch path instead.
        mon, _ = self._monitor(queue=['no-such-operation'])

        _, abort = self._reap(mon, None)

        abort.assert_not_called()
