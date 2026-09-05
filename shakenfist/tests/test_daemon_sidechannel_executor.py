# Copyright 2019 Michael Still and contributors

import functools
import os
import shutil
import tempfile
import time
from unittest import mock

from shakenfist import constants
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
        self.expired_budget = None

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

    def expire(self, reason, budget):
        """Stands in for AgentOperation.expire(), guards included."""
        if budget not in AgentOperation.EXPIRY_REASONS:
            raise ValueError(f'unknown expiry budget: {budget}')
        if self.state.value in self.TERMINAL_STATES:
            return
        self.state = AgentOperation.STATE_EXPIRED
        self.expired_reason = reason
        self.expired_budget = budget

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

        # The finally block tears down any in-flight get-file whatever
        # the operation's state, so these have to be set for the same
        # reason __init__() sets them: without them the teardown raises
        # AttributeError rather than being the no-op it is meant to be.
        job._blob_uuid = None
        job._stat_result = None
        job._agent_path_for_get = None
        job._blob_partial_file = None
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


class SideChannelAbortPathTestCase(base.ShakenFistTestCase):
    """A monitor and an executor for one instance need separate abort files.

    The reaper stops a wedged executor by setting its abort path. When
    the two jobs shared a file that also stopped the instance's agent
    monitor, and the monitor's restart cleared the file again --
    possibly before the wedged executor's one second poll had read it,
    leaving it wedged with the executor slot still held, which is the
    thing steps 5b and 5d exist to prevent.

    These construct real jobs. A hand-built object would pass whatever
    the derivation did, which is exactly how the two came to share a
    file.
    """

    def _jobs(self):
        inst = _FakeInstance()
        with mock.patch.object(sidechannel.daemon, 'clear_abort_path'):
            monitor = sidechannel.SideChannelMonitorJob(inst)
            executor = sidechannel.SideChannelExecutorJob(
                inst, _FakeAgentOp(AgentOperation.STATE_QUEUED))
        return monitor, executor

    def test_the_two_job_types_do_not_share_an_abort_path(self):
        monitor, executor = self._jobs()
        self.assertNotEqual(monitor.abort_path, executor.abort_path)

    def test_the_monitor_keeps_the_historical_path(self):
        # Operators and the daemon's own shutdown path know the monitor
        # by this name, so the executor is the one which moved.
        monitor, _ = self._jobs()
        self.assertEqual(
            '/run/sf/sidechannel-fake-instance.abort', monitor.abort_path)

    def test_the_executor_path_is_per_instance(self):
        # Not per operation: the dispatcher runs one executor per
        # instance, and a per-operation name would leave a file behind
        # in /run/sf for every operation the reaper ever stopped.
        _, executor = self._jobs()
        self.assertEqual(
            '/run/sf/sidechannel-executor-fake-instance.abort',
            executor.abort_path)

    def test_building_an_executor_does_not_clear_the_monitors_abort(self):
        # The base constructor clears whatever path it derives. While
        # the two were shared, constructing an executor during shutdown
        # un-stopped the monitor.
        inst = _FakeInstance()
        with mock.patch.object(sidechannel.daemon,
                               'clear_abort_path') as clear:
            monitor = sidechannel.SideChannelMonitorJob(inst)
            clear.reset_mock()
            sidechannel.SideChannelExecutorJob(
                inst, _FakeAgentOp(AgentOperation.STATE_QUEUED))

        cleared = [c[0][0] for c in clear.call_args_list]
        self.assertNotIn(monitor.abort_path, cleared)


class RequestAllThreadsExitTestCase(base.ShakenFistTestCase):
    """Every thread must be told to stop, whatever happens to the joins.

    Until this phase a monitor and its instance's executor shared one
    abort file, so the monitors loop stopped every executor as a side
    effect. Separating the files (which the reaper needs, or its abort
    for a wedged executor also stops the monitor) removed that
    accident, and what it was masking is #3931: _request_thread_exit()
    joins and deletes out of self.monitors whichever dictionary the
    entry came from, so it raises KeyError once the monitors loop has
    already removed an entry, with no try/except at the call site.
    Signalling up front is what makes shutdown independent of that.
    """

    class _ShutdownMonitor:
        _request_all_threads_exit = sidechannel.Monitor.\
            _request_all_threads_exit
        _request_thread_exit = sidechannel.Monitor._request_thread_exit

        def __init__(self, instances):
            self.monitors = {}
            self.executors = {}
            for uuid in instances:
                # The monitor threads stop promptly, so the monitors
                # loop deletes their entries. That is what arms
                # #3931: the executors loop then reaches into
                # self.monitors for an entry which is gone. Without
                # it this fixture would never reproduce the bug,
                # because a monitor which is still alive is not
                # deleted and the KeyError never happens.
                monitor_thread = mock.Mock()
                monitor_thread.is_alive.return_value = False
                self.monitors[uuid] = {
                    'object': mock.Mock(
                        abort_path=f'/run/sf/sidechannel-{uuid}.abort'),
                    'thread': monitor_thread,
                    'instance_uuid': uuid
                }
                self.executors[uuid] = {
                    'object': mock.Mock(
                        abort_path=(
                            f'/run/sf/sidechannel-executor-{uuid}.abort')),
                    'thread': mock.Mock(),
                    'instance_uuid': uuid
                }

    def test_every_thread_is_signalled(self):
        mon = self._ShutdownMonitor(['inst-a', 'inst-b'])

        with mock.patch.object(sidechannel, 'add_event'), \
                mock.patch.object(sidechannel.daemon, 'clear_abort_path'), \
                mock.patch.object(sidechannel.daemon,
                                  'set_abort_path') as abort:
            try:
                mon._request_all_threads_exit()
            except KeyError:
                # #3931, which lives in _request_thread_exit() and is
                # fixed separately. The point of this test is that the
                # signalling above it has already happened.
                pass

        signalled = {c[0][0] for c in abort.call_args_list}
        for uuid in ('inst-a', 'inst-b'):
            self.assertIn(f'/run/sf/sidechannel-{uuid}.abort', signalled)
            self.assertIn(
                f'/run/sf/sidechannel-executor-{uuid}.abort', signalled)

    def test_signalling_happens_before_any_join(self):
        # Signalling inside the per-thread loop makes each thread's
        # notice wait on the previous thread's half second join, so
        # threads told to stop together would not stop together.
        mon = self._ShutdownMonitor(['inst-a', 'inst-b'])
        order = []

        for entry in list(mon.monitors.values()) + list(
                mon.executors.values()):
            entry['thread'].join.side_effect = (
                lambda *a, **kw: order.append('join'))

        with mock.patch.object(sidechannel, 'add_event'), \
                mock.patch.object(sidechannel.daemon, 'clear_abort_path'), \
                mock.patch.object(
                    sidechannel.daemon, 'set_abort_path',
                    side_effect=lambda *a: order.append('signal')):
            try:
                mon._request_all_threads_exit()
            except KeyError:
                pass

        self.assertEqual(4, order[:4].count('signal'))


class SideChannelPreConnectionWaitTestCase(base.ShakenFistTestCase):
    """The pre-connection wait in SideChannelJob.execute() must be
    abortable, or the reaper can resolve a wedged operation and still not
    free the instance's executor slot (step 5d)."""

    def _make_job(self, instance_path):
        # The real constructor, so the abort path is the one production
        # derives rather than one the test chose; only its directory is
        # redirected away from /run/sf, which a test cannot write to.
        # An earlier version of this built the object with __new__ and
        # assigned abort_path by hand, which is why it could not have
        # caught the monitor and the executor sharing a file.
        inst = mock.MagicMock(
            uuid='fake-instance', instance_path=instance_path)
        with mock.patch.object(sidechannel.daemon, 'clear_abort_path'):
            job = sidechannel.SideChannelExecutorJob(
                inst, _FakeAgentOp(AgentOperation.STATE_QUEUED))

        self.assertEqual(
            '/run/sf/sidechannel-executor-fake-instance.abort',
            job.abort_path)
        job.abort_path = os.path.join(
            instance_path, os.path.basename(job.abort_path))
        job.log = mock.MagicMock()
        return job

    def test_the_wait_exits_when_the_abort_path_is_set(self):
        # Deliberately no patch of time.sleep. sidechannel.time is the
        # global time module, so patching sleep there is process wide
        # for the duration of the test, and this suite does not need
        # it: an abort path which is already set is checked before the
        # first sleep, so the loop exits without sleeping at all.
        with tempfile.TemporaryDirectory() as tmp:
            job = self._make_job(tmp)
            sidechannel.daemon.set_abort_path(job.abort_path, 'test')

            started = time.time()
            job.execute()

            self.assertLess(time.time() - started, 1)
            job.instance.socket_on_vsock_channel.assert_not_called()

    def test_the_wait_ends_when_the_console_log_appears(self):
        # The other exit, so the test above cannot pass merely because
        # the loop never runs: with no abort path set and the console
        # log present, execute() falls through to the connection
        # attempt on its first pass.
        with tempfile.TemporaryDirectory() as tmp:
            job = self._make_job(tmp)
            open(os.path.join(tmp, 'console.log'), 'w').close()

            with mock.patch.object(
                    sidechannel.SideChannelExecutorJob, '_execute_inner'):
                job.execute()

            job.instance.socket_on_vsock_channel.assert_called_once()


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
        job.agentop.expire('deadline passed',
                           AgentOperation.EXPIRY_REASON_DEADLINE)
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
                 commands=None, attempts=0, deadline=1700000000.0):
        # A stall only reaches a terminal state when a retry is
        # impossible, so these fakes default to a command list which
        # cannot be retried. The retry path itself is exercised in
        # ExecutorRetryTestCase.
        super().__init__(
            AgentOperation.STATE_EXECUTING, commands=commands,
            attempts=attempts, deadline_passed=deadline_passed)
        self._progress_timeout = progress_timeout
        self.deadline_checks = 0
        self.anchor_states = []

        # A stored absolute deadline by default, because that is what
        # every operation a current API server creates carries. Tests
        # of the NULL-deadline anchor path pass deadline=None.
        self.deadline = deadline

    def deadline_passed(self, state=None):
        self.deadline_checks += 1
        self.anchor_states.append(state)
        return self._deadline_passed

    def effective_progress_timeout(self):
        return self._progress_timeout

    def deadline_needs_state_anchor(self):
        # Mirrors the real resolution: a NULL deadline anchors its
        # server default on a state read, as does the 0.0 sentinel
        # with no live progress timeout (the issue #4074 backstop).
        if self.deadline is None:
            return True
        return (self.deadline == 0.0
                and self.effective_progress_timeout() is None)


class _AnchorAgentOp(_BudgetAgentOp):
    """A budget fake which counts how often its state row is read.

    On the real object every state read is an uncached GetObjectState
    round trip, which is what the executor's anchor cache exists to
    bound (issue #4014).
    """

    def __init__(self, **kwargs):
        self.state_reads = 0
        super().__init__(**kwargs)

    @property
    def state(self):
        self.state_reads += 1
        return self._state

    @state.setter
    def state(self, value):
        self._state = _FakeState(value)


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
        job._deadline_anchor = None
        job.commands = []
        job._blob_partial_file = None
        job.log = mock.MagicMock()
        return job

    def test_the_check_is_rate_limited(self):
        # The caller is the socket loop, which iterates once per packet
        # during a transfer, so an unthrottled check runs thousands of
        # times a second for no gain in resolution.
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

    def test_a_null_deadline_reads_its_anchor_once(self):
        # A NULL deadline (a row written by an API server which
        # predates deadlines) resolves the server default against the
        # operation's state row, which on the real object is an
        # uncached database read. The anchor cannot move for the life
        # of this executor, so however many budget checks run, it is
        # read once and the same State handed to every check --
        # otherwise this method is a fixed-rate database poll of 1/s
        # per live executor (issue #4014).
        agentop = _AnchorAgentOp(deadline=None, deadline_passed=False)
        job = self._make_executor(agentop, handler=_SilentHandler())

        for offset in (0.0, 1.5, 3.0):
            with mock.patch('time.time', return_value=self.NOW + offset):
                self.assertFalse(job.expire_if_out_of_budget())

        self.assertEqual(3, agentop.deadline_checks)
        self.assertEqual(1, agentop.state_reads)
        # Every check was anchored, and on the one State that was read.
        self.assertEqual(3, len(agentop.anchor_states))
        for anchor in agentop.anchor_states:
            self.assertIs(agentop.anchor_states[0], anchor)
        self.assertIsNotNone(agentop.anchor_states[0])

    def test_a_stored_deadline_never_reads_an_anchor(self):
        # Every operation a current API server creates carries an
        # absolute deadline, and resolving one costs no database read
        # at all -- so the executor must not read the state row just to
        # have an anchor it will never use.
        agentop = _AnchorAgentOp(deadline=1700000000.0,
                                 deadline_passed=False)
        job = self._make_executor(agentop, handler=_SilentHandler())

        for offset in (0.0, 1.5, 3.0):
            with mock.patch('time.time', return_value=self.NOW + offset):
                self.assertFalse(job.expire_if_out_of_budget())

        self.assertEqual(3, agentop.deadline_checks)
        self.assertEqual(0, agentop.state_reads)
        self.assertEqual([None, None, None], agentop.anchor_states)

    def test_the_no_budget_backstop_reads_its_anchor_once(self):
        # The issue #4074 backstop: a 0.0 deadline sentinel with no
        # live progress timeout resolves AGENT_OPERATION_MAX_DEADLINE
        # against the state row, exactly like a NULL deadline, and gets
        # the same one-read anchor treatment for the same #4014 reason.
        agentop = _AnchorAgentOp(deadline=0.0, progress_timeout=None,
                                 deadline_passed=False)
        job = self._make_executor(agentop, handler=_SilentHandler())

        for offset in (0.0, 1.5, 3.0):
            with mock.patch('time.time', return_value=self.NOW + offset):
                self.assertFalse(job.expire_if_out_of_budget())

        self.assertEqual(3, agentop.deadline_checks)
        self.assertEqual(1, agentop.state_reads)
        self.assertIsNotNone(agentop.anchor_states[0])

    def test_the_licensed_sentinel_never_reads_an_anchor(self):
        # A 0.0 deadline alongside a live progress timeout really has
        # no wall-clock deadline, so there is nothing to anchor and the
        # state row must not be read just in case.
        agentop = _AnchorAgentOp(deadline=0.0, progress_timeout=30.0,
                                 deadline_passed=False)
        job = self._make_executor(agentop, handler=_SilentHandler())

        with mock.patch('time.time', return_value=self.NOW):
            self.assertFalse(job.expire_if_out_of_budget())

        self.assertEqual(0, agentop.state_reads)
        self.assertEqual([None], agentop.anchor_states)

    def test_passed_deadline_expires_and_stops(self):
        agentop = _BudgetAgentOp(deadline_passed=True)
        job = self._make_executor(agentop)
        self.assertTrue(job.expire_if_out_of_budget())
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            'the operation deadline passed while executing',
            agentop.expired_reason)
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_DEADLINE, agentop.expired_budget)

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
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_PROGRESS, agentop.expired_budget)

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
        job._deadline_anchor = None
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
        job._deadline_anchor = None
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

    def test_an_executor_exit_removes_it_too(self):
        # execute()'s finally is the third way out, and until the review
        # of this phase it was the one which did not clean up. It
        # matters more now than it did: a retried operation runs
        # GetFileCommand.dispatch() again and mints a fresh blob uuid,
        # so a dropped connection mid-transfer would otherwise leave one
        # orphaned .partial file per attempt.
        agentop = _BudgetAgentOp()
        job = self._make_executor(agentop)
        self.assertTrue(os.path.exists(self.partial_path))

        with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                               return_value=None), \
                mock.patch.object(sidechannel, 'add_event_multi'):
            job.execute()

        self.assertFalse(os.path.exists(self.partial_path))
        self.assertIsNone(job._blob_partial_file)

    def test_an_executor_exit_with_no_transfer_is_a_noop(self):
        # The teardown is outside the state guard in the finally, so it
        # runs for a completed operation too. It must be a no-op there.
        agentop = _BudgetAgentOp()
        agentop.state = AgentOperation.STATE_COMPLETE
        job = self._make_executor(agentop, in_flight=False)

        with mock.patch.object(sidechannel.SideChannelJob, 'execute',
                               return_value=None), \
                mock.patch.object(sidechannel, 'add_event_multi'):
            job.execute()

        self.assertEqual(AgentOperation.STATE_COMPLETE, agentop.state.value)
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
        job._deadline_anchor = None
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


class _GetFileDispatchHandler(sidechannel.AgentCommandHandler):
    """A retryable handler whose dispatch() always produces one request.

    Subclassed rather than duck typed so it inherits retryable,
    register_as_outstanding and anything the base class grows later.
    Only the two things this test needs to control are overridden.
    """

    name = 'get-file'
    reports_progress = True

    def dispatch(self, command_id, cmd):
        return ['a-request']


class ExecutorCommandListOwnershipTestCase(base.ShakenFistTestCase):
    """The executor's work queue must be its own list, not the operation's.

    AgentOperation.commands returns the operation's list by reference,
    and that list is the one the process wide object cache holds, so an
    executor which aliased it drained the operation as it dispatched.
    The visible consequence was in the retry decision:
    operation_is_retryable() refuses an empty command list, so a single
    command get-file called itself unretryable the moment its only
    command went out -- the exact operation shape issue #3516 is about,
    and the opposite of what the retry path was built to do.

    These build a real executor through its real constructor. The
    dispatch tests above hand-set job.commands, which is precisely why
    they could not see this: the alias was made in __init__.
    """

    def _executor(self):
        agentop = _FakeAgentOp(
            AgentOperation.STATE_QUEUED,
            commands=[{'command': 'get-file', 'path': '/tmp/x'}])
        with mock.patch.object(sidechannel.daemon, 'clear_abort_path'):
            job = sidechannel.SideChannelExecutorJob(_FakeInstance(), agentop)
        job.command_handlers = {'get-file': _GetFileDispatchHandler(job)}
        job._send_commands_single_envelope = mock.MagicMock()
        return job

    def test_the_executor_does_not_share_the_operations_list(self):
        job = self._executor()
        self.assertEqual(job.commands, job.agentop.commands)
        self.assertIsNot(job.commands, job.agentop.commands)

    def test_dispatch_does_not_drain_the_operation(self):
        job = self._executor()
        with mock.patch.object(sidechannel, 'add_event_multi'):
            job._dispatch_next_command(mock.MagicMock())

        # The executor consumed its own queue, which is what makes the
        # socket loop exit once the replies are in...
        self.assertEqual([], job.commands)
        # ...while the operation still knows what it was asked to do.
        self.assertEqual(
            [{'command': 'get-file', 'path': '/tmp/x'}], job.agentop.commands)

    def test_a_dispatched_get_file_is_still_retryable(self):
        # The consequence, asserted where it was actually observed. A
        # stall is only reachable after dispatch, so this is the state
        # every caller of operation_is_retryable() sees.
        job = self._executor()
        with mock.patch.object(sidechannel, 'add_event_multi'):
            job._dispatch_next_command(mock.MagicMock())

        self.assertTrue(sidechannel.operation_is_retryable(job.agentop))


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

    @staticmethod
    def _expire_on_stall(agentop):
        """The terminal the executor's stall branch passes: expire, with
        the budget it detected running out bound in."""
        return functools.partial(
            agentop.expire, budget=AgentOperation.EXPIRY_REASON_PROGRESS)

    def test_a_stall_under_the_cap_requeues_and_clears_results(self):
        agentop = self._op(commands=[{'command': 'get-file'}], attempts=1)
        retried, event = self._resolve(
            agentop, self.STALL, self._expire_on_stall(agentop))

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
        retried, _ = self._resolve(
            agentop, self.STALL, self._expire_on_stall(agentop))

        self.assertFalse(retried)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(f'{self.STALL}, after 3 attempts',
                         agentop.expired_reason)
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_PROGRESS, agentop.expired_budget)
        self.assertFalse(agentop.results_cleared)

    def test_a_passed_deadline_expires_even_with_attempts_left(self):
        # Retrying would spend time nobody is waiting for: the
        # caller's budget is the thing which just ran out.
        agentop = self._op(commands=[{'command': 'get-file'}], attempts=1,
                           deadline_passed=True)
        retried, _ = self._resolve(
            agentop, self.STALL, self._expire_on_stall(agentop))

        self.assertFalse(retried)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            f'{self.STALL}, and the operation deadline has passed',
            agentop.expired_reason)
        # The budget is the one the caller detected running out -- the
        # stall is what abandoned the attempt, the passed deadline only
        # foreclosed the retry. The message carries both facts.
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_PROGRESS, agentop.expired_budget)

    def test_an_execute_operation_never_retries(self):
        agentop = self._op(commands=[{'command': 'execute'}], attempts=1)
        retried, _ = self._resolve(
            agentop, self.STALL, self._expire_on_stall(agentop))

        self.assertFalse(retried)
        self.assertEqual(AgentOperation.STATE_EXPIRED, agentop.state.value)
        self.assertEqual(
            f'{self.STALL}, and the operation cannot be safely retried',
            agentop.expired_reason)
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_PROGRESS, agentop.expired_budget)

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
        job._deadline_anchor = None
        job.commands = []
        job._blob_partial_file = None
        job.log = mock.MagicMock()

        with mock.patch.object(sidechannel, 'add_event_multi'):
            with mock.patch('time.time', return_value=31.0):
                self.assertTrue(job.expire_if_out_of_budget())

        self.assertEqual(AgentOperation.STATE_QUEUED, agentop.state.value)
        self.assertIsNone(agentop.expired_reason)

    def test_an_executor_exit_retries_to_the_cap_and_then_errors(self):
        # The whole loop, one attempt at a time. attempts is written
        # when the operation transitions into executing, which is what
        # the assignment inside the loop below stands in for, so the
        # first executor to exit sees 1.
        agentop = self._op(commands=[{'command': 'get-file'}], attempts=0)
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = agentop
        job.log = mock.MagicMock()
        job._blob_uuid = None
        job._stat_result = None
        job._agent_path_for_get = None
        job._blob_partial_file = None

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


class DispatchRegistrationOrderTestCase(base.ShakenFistTestCase):
    """The executor must be registered before its thread starts.

    self.executors is what the reaper reads to tell "nothing is working
    on this operation" from "something is". Between start() and the
    registration the operation can already be executing with no entry
    there, and a reaper running in that window requeues an operation
    which is actively running -- clearing its results underneath it.
    Within one dispatcher thread the window is unobservable, but
    supervise_dispatcher() deliberately leaves a replaced generation
    alive, so an old thread can be here while the new generation reaps.
    """

    def test_the_entry_exists_before_the_thread_is_started(self):
        mon = _DispatchMonitor()
        mon.monitors['inst'] = {'object': mock.Mock()}
        agentop = _FakeAgentOp(AgentOperation.STATE_QUEUED)
        registered_at_start = []

        thread = mock.Mock()
        thread.start.side_effect = lambda: registered_at_start.append(
            'inst' in mon.executors)

        with mock.patch.object(sidechannel.instance.Instance, 'from_db',
                               return_value=mock.Mock()), \
                mock.patch.object(sidechannel, 'SideChannelExecutorJob'), \
                mock.patch.object(sidechannel.threading, 'Thread',
                                  return_value=thread), \
                mock.patch.object(sidechannel, 'add_event'):
            sidechannel.Monitor.start_instance_executor(mon, 'inst', agentop)

        self.assertEqual([True], registered_at_start)

    def test_a_failed_start_does_not_leave_the_entry_behind(self):
        # start() raises RuntimeError when the process cannot make
        # another thread. The sweep skips an unstarted thread rather
        # than joining it, so an entry left here would never be
        # collected -- and while it sat there the instance would look
        # to the reaper like it had a live executor, so an operation
        # left executing on it would never be resolved again.
        mon = _DispatchMonitor()
        mon.monitors['inst'] = {'object': mock.Mock()}
        agentop = _FakeAgentOp(AgentOperation.STATE_QUEUED)

        thread = mock.Mock()
        thread.start.side_effect = RuntimeError('cannot start new thread')

        with mock.patch.object(sidechannel.instance.Instance, 'from_db',
                               return_value=mock.Mock()), \
                mock.patch.object(sidechannel, 'SideChannelExecutorJob'), \
                mock.patch.object(sidechannel.threading, 'Thread',
                                  return_value=thread), \
                mock.patch.object(sidechannel, 'add_event'):
            self.assertRaises(
                RuntimeError, sidechannel.Monitor.start_instance_executor,
                mon, 'inst', agentop)

        self.assertEqual({}, mon.executors)

    def test_dispatch_alone_records_no_attempt(self):
        # Attempts are counted on the transition into executing, not
        # here. An executor which never reaches the agent leaves the
        # operation queued for a later dispatch, and the dispatcher
        # retries every five seconds, so counting here would burn the
        # whole cap on an instance with a flaky agent channel without a
        # single command having been sent.
        mon = _DispatchMonitor()
        mon.monitors['inst'] = {'object': mock.Mock()}
        agentop = _FakeAgentOp(AgentOperation.STATE_QUEUED)

        with mock.patch.object(sidechannel.instance.Instance, 'from_db',
                               return_value=mock.Mock()), \
                mock.patch.object(sidechannel, 'SideChannelExecutorJob'), \
                mock.patch.object(sidechannel.threading, 'Thread'), \
                mock.patch.object(sidechannel, 'add_event'):
            sidechannel.Monitor.start_instance_executor(mon, 'inst', agentop)

        self.assertEqual(0, agentop.attempts)


class DispatchRecordsAnAttemptTestCase(base.ShakenFistTestCase):
    """Reaching the agent is what counts an attempt.

    Nothing else writes the attempts counter, so if this call is lost
    the counter stays at zero, every comparison against
    AGENT_OPERATION_MAX_ATTEMPTS is false, and a stalling operation
    retries until its deadline -- or forever, for an operation with no
    deadline at all.
    """

    def _make_executor(self, state_value, commands=2):
        job = sidechannel.SideChannelExecutorJob.__new__(
            sidechannel.SideChannelExecutorJob)
        job.agentop = _FakeAgentOp(state_value)
        job.instance = _FakeInstance()
        job.affected_objects = [job.instance, job.agentop]
        job.commands = [{'command': 'execute', 'commandline': 'true'}
                        for _ in range(commands)]
        job.command_handlers = {'execute': _DispatchHandler()}
        job.in_flight_handler = None
        job.outstanding_message_count = 0
        job.ready = True
        job._last_progress = 0.0
        job._last_budget_check = 0.0
        job._deadline_anchor = None
        job._blob_partial_file = None
        job.log = mock.MagicMock()
        job._send_commands_single_envelope = mock.MagicMock()
        return job

    def _dispatch(self, job):
        with mock.patch.object(sidechannel, 'add_event_multi'):
            job._dispatch_next_command(mock.MagicMock())

    def test_reaching_the_agent_records_an_attempt(self):
        job = self._make_executor(AgentOperation.STATE_QUEUED)
        self._dispatch(job)
        self.assertEqual(
            AgentOperation.STATE_EXECUTING, job.agentop.state.value)
        self.assertEqual(1, job.agentop.attempts)

    def test_a_second_command_is_the_same_attempt(self):
        # The state assignment is a no-op the second time round, and
        # those commands are the same attempt at the same operation.
        # Counting per command would divide the cap by the length of
        # the command list.
        job = self._make_executor(AgentOperation.STATE_QUEUED)
        self._dispatch(job)
        self._dispatch(job)
        self.assertEqual(1, job.agentop.attempts)

    def test_a_retry_records_another_attempt(self):
        # The requeue and re-dispatch that resolve_abandoned_operation()
        # produces: back to queued, dispatched again, counted again.
        job = self._make_executor(AgentOperation.STATE_QUEUED)
        self._dispatch(job)
        job.agentop.state = AgentOperation.STATE_QUEUED

        job.commands = [{'command': 'execute', 'commandline': 'true'}]
        self._dispatch(job)
        self.assertEqual(2, job.agentop.attempts)

    def test_a_terminal_operation_records_nothing(self):
        # The guard above the counter. Nothing was sent, so nothing is
        # counted against the operation's cap.
        job = self._make_executor(AgentOperation.STATE_EXPIRED)
        self._dispatch(job)
        self.assertEqual(0, job.agentop.attempts)


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
        self.monitor_attempts = {}


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

    # The path a real SideChannelExecutorJob for this instance derives,
    # asserted against below rather than an arbitrary one, so the abort
    # cannot silently be sent to the instance's monitor instead. That is
    # not hypothetical: the two shared a file until the review of this
    # phase. SideChannelAbortPathTestCase is what ties this constant to
    # the real derivation.
    EXECUTOR_ABORT = '/run/sf/sidechannel-executor-fake-instance.abort'

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
            # ident is set because the reaper skips a thread which has
            # never been started; see
            # test_an_unstarted_executor_is_not_joined.
            thread = mock.Mock(ident=12345)
            thread.is_alive.return_value = executor_alive
            mon.executors[self.INSTANCE] = {
                'object': mock.Mock(abort_path=self.EXECUTOR_ABORT),
                'thread': thread,
                'instance_uuid': self.INSTANCE
            }

        return mon, inst

    def _reap(self, mon, agentop=None):
        with mock.patch.object(sidechannel.AgentOperation, 'from_db',
                               return_value=agentop) as from_db, \
                mock.patch.object(sidechannel, 'add_event'), \
                mock.patch.object(sidechannel, 'add_event_multi'), \
                mock.patch.object(sidechannel.daemon, 'clear_abort_path'), \
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

        # error, not expired. Decision 3 splits the two on what went
        # wrong rather than on who noticed, and this is an executor
        # which went away -- the same failure the finally block
        # handles, differing only in that the process died first. The
        # difference is load bearing: expired is in
        # FINAL_OBJECT_STATES and error is not, so expiring here would
        # sweep the daemon-restart case for hard deletion while the
        # finally case persisted.
        self.assertEqual(AgentOperation.STATE_ERROR, agentop.state.value)
        self.assertEqual(
            'no sidechannel executor was running for this operation, and '
            'the operation cannot be safely retried',
            agentop.failure_reason)
        self.assertIn(
            AgentOperation.STATE_EXPIRED, constants.FINAL_OBJECT_STATES)
        self.assertNotIn(
            AgentOperation.STATE_ERROR, constants.FINAL_OBJECT_STATES)

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
        self.assertEqual(AgentOperation.STATE_ERROR, agentop.state.value)

    def test_an_unstarted_executor_is_not_joined(self):
        # Registering before start() leaves a window in which the
        # thread exists but has never run, and is_alive() is False for
        # such a thread exactly as it is for a finished one. join()
        # raises RuntimeError there, which without the ident guard
        # would abort the whole reap pass -- and a replaced dispatcher
        # generation reaping while the old one is mid-dispatch is
        # precisely the case the registration order exists for.
        agentop = _FakeAgentOp(AgentOperation.STATE_EXECUTING)
        mon, _ = self._monitor(queue=['an-operation'], executor_alive=False)
        thread = mon.executors[self.INSTANCE]['thread']
        thread.ident = None
        thread.join.side_effect = RuntimeError(
            'cannot join thread before it is started')

        self._reap(mon, agentop)

        # Not swept, not joined, and not mistaken for an absent
        # executor: the operation is left alone for the thread to run.
        self.assertIn(self.INSTANCE, mon.executors)
        thread.join.assert_not_called()
        self.assertEqual(
            AgentOperation.STATE_EXECUTING, agentop.state.value)

    def test_sweeping_a_dead_executor_clears_its_abort_path(self):
        # The reaper is the only thing which sets this file and nothing
        # else clears it, so without this /run/sf accumulates one file
        # per reaped executor for the daemon's lifetime.
        agentop = _FakeAgentOp(AgentOperation.STATE_EXECUTING)
        mon, _ = self._monitor(queue=['an-operation'], executor_alive=False)

        with mock.patch.object(sidechannel.AgentOperation, 'from_db',
                               return_value=agentop), \
                mock.patch.object(sidechannel, 'add_event'), \
                mock.patch.object(sidechannel, 'add_event_multi'), \
                mock.patch.object(sidechannel.daemon, 'set_abort_path'), \
                mock.patch.object(sidechannel.daemon,
                                  'clear_abort_path') as clear:
            sidechannel.Monitor.reap_instance_executors(mon)

        clear.assert_called_once_with(self.EXECUTOR_ABORT)

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

        def _expire(reason, budget):
            order.append('resolve')
            resolve(reason, budget)

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
        # The reaper only reaches a live executor because its deadline
        # passed, so that is the budget it binds.
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_DEADLINE, agentop.expired_budget)
        self.assertEqual(self.EXECUTOR_ABORT, abort.call_args[0][0])

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

    def test_reaping_a_monitor_drops_its_rate_limiter_entry(self):
        # The reaper only ever examines instances which have a monitor,
        # so its rate limiter has nothing to say about one which does
        # not. Without this the dictionary grows for the daemon's
        # lifetime on a node with instance churn.
        mon, _ = self._monitor(queue=[])
        mon.monitors[self.INSTANCE]['thread'].is_alive.return_value = False
        mon.reaper_attempts[self.INSTANCE] = time.time()

        sidechannel.Monitor.reap_instance_monitors(mon)

        self.assertEqual({}, mon.monitors)
        self.assertEqual({}, mon.reaper_attempts)

    def test_an_invalid_queue_entry_is_left_for_the_dispatcher(self):
        # Retiring it takes the instance's attribute lock, which the
        # reaper deliberately does not; agent_operation_next() does it
        # on the dispatch path instead.
        mon, _ = self._monitor(queue=['no-such-operation'])

        _, abort = self._reap(mon, None)

        abort.assert_not_called()
