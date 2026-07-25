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
