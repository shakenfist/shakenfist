# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.daemons.sidechannel import main as sidechannel
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.tests import base


class _FakeState:
    def __init__(self, value):
        self.value = value


class _FakeAgentOp:
    TERMINAL_STATES = AgentOperation.TERMINAL_STATES

    def __init__(self, state_value):
        self.uuid = 'fake-agentop'
        self.state = _FakeState(state_value)
        self.error = None
        self.commands = []
        self.expired_reason = None

    def fail(self, message):
        """Stands in for AgentOperation.fail(), guard included."""
        if self.state.value in self.TERMINAL_STATES:
            return
        self.state = _FakeState(AgentOperation.STATE_ERROR)
        self.error = message

    def expire(self, reason):
        """Stands in for AgentOperation.expire(), guard included."""
        if self.state.value in self.TERMINAL_STATES:
            return
        self.state = _FakeState(AgentOperation.STATE_EXPIRED)
        self.expired_reason = reason


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
        self.assertEqual(AgentOperation.STATE_ERROR, job.agentop.state)
        self.assertIsNotNone(job.agentop.error)

    def test_marks_error_when_exception_while_executing(self):
        job = self._make_executor(AgentOperation.STATE_EXECUTING)
        # An unexpected exception must still leave the op errored (and then
        # propagate for the usual exception tracking).
        with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                               side_effect=RuntimeError('boom')):
            self.assertRaises(RuntimeError, job.execute)
        self.assertEqual(AgentOperation.STATE_ERROR, job.agentop.state)

    def test_leaves_completed_op_untouched(self):
        job = self._make_executor(AgentOperation.STATE_COMPLETE)
        with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                               return_value=None):
            job.execute()
        # A completed op is not reassigned to error.
        self.assertEqual(AgentOperation.STATE_COMPLETE, job.agentop.state.value)
        self.assertIsNone(job.agentop.error)


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
        job.log = mock.MagicMock()
        return job

    def test_fail_from_expired_leaves_the_state_alone(self):
        agentop = _FakeAgentOp(AgentOperation.STATE_EXECUTING)
        agentop.expire('deadline passed')
        agentop.fail('and then the transfer broke')
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertIsNone(agentop.error)

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

    def __init__(self, deadline_passed=False, progress_timeout=30.0):
        super().__init__(AgentOperation.STATE_EXECUTING)
        self._deadline_passed = deadline_passed
        self._progress_timeout = progress_timeout

    def deadline_passed(self):
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

    def _make_executor(self, agentop, handler=None, ready=False,
                       last_progress=0.0):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = agentop
        job.in_flight_handler = handler
        job.ready = ready
        job._last_progress = last_progress
        job.log = mock.MagicMock()
        return job

    def test_passed_deadline_expires_and_stops(self):
        agentop = _BudgetAgentOp(deadline_passed=True)
        job = self._make_executor(agentop)
        self.assertTrue(job.expire_if_out_of_budget())
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            'the operation deadline passed while executing',
            agentop.expired_reason)

    def test_stalled_progress_expires_and_stops(self):
        agentop = _BudgetAgentOp(progress_timeout=30.0)
        job = self._make_executor(
            agentop, handler=_ProgressHandler(), last_progress=0.0)
        with mock.patch('time.time', return_value=31.0):
            self.assertTrue(job.expire_if_out_of_budget())
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            'no progress from the agent for 30.0 seconds',
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
    persists it at most once per PROGRESS_PERSIST_INTERVAL."""

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
        with mock.patch.object(sidechannel.mariadb,
                               'update_agent_operation_attributes'):
            for offset in (0.0, 1.0, 2.0):
                with mock.patch('time.time', return_value=self.NOW + offset):
                    job.observe_progress()
                self.assertEqual(self.NOW + offset, job._last_progress)

    def test_persistence_is_throttled(self):
        job = self._make_executor()
        with mock.patch.object(
                sidechannel.mariadb,
                'update_agent_operation_attributes') as mock_update:
            # The first call persists (this row has never been
            # written), then everything inside the interval is
            # in-memory only. A 100KiB-chunk transfer calls this
            # hundreds of times a second, which is the whole point.
            for offset in (0.0, 1.0, 4.0, 9.0):
                with mock.patch('time.time', return_value=self.NOW + offset):
                    job.observe_progress()
            self.assertEqual(1, mock_update.call_count)

            # Past the interval, one more write.
            with mock.patch('time.time', return_value=self.NOW + 11.0):
                job.observe_progress()
            self.assertEqual(2, mock_update.call_count)

    def test_the_write_carries_a_field_mask(self):
        # An unmasked write would push a stale snapshot of the results
        # column over a concurrent add_result().
        job = self._make_executor()
        with mock.patch.object(
                sidechannel.mariadb,
                'update_agent_operation_attributes') as mock_update:
            with mock.patch('time.time', return_value=self.NOW):
                job.observe_progress()

        self.assertEqual(1, mock_update.call_count)
        self.assertEqual(
            ['last_progress'], mock_update.call_args.kwargs['fields'])
        self.assertEqual(
            self.NOW, mock_update.call_args.args[0].last_progress)
