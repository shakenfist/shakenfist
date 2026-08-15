# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.daemons.sidechannel import main as sidechannel
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.tests import base


class _FakeState:
    def __init__(self, value):
        self.value = value


class _FakeAgentOp:
    def __init__(self, state_value):
        self.uuid = 'fake-agentop'
        self.state = _FakeState(state_value)
        self.error = None


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
