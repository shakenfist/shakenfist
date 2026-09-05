# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.operations import node_aop_op
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.tests import base


class _FakeState:
    def __init__(self, value):
        self.value = value


class _FakeAgentOp:
    """Enough of an AgentOperation for _preflight() to work on.

    deadline_passed() is driven from a list so a test can say "not
    expired on entry, expired after the blob copy", which is the case
    the second check exists for.
    """

    TERMINAL_STATES = AgentOperation.TERMINAL_STATES

    def __init__(self, commands, deadline_passed=False):
        self.uuid = 'fake-agentop'
        self.state = AgentOperation.STATE_PREFLIGHT
        self.commands = commands
        self.failure_reason = None
        self.expired_reason = None
        self.expired_budget = None
        if isinstance(deadline_passed, bool):
            self._deadline_passed = [deadline_passed] * 10
        else:
            self._deadline_passed = list(deadline_passed)

    # The real state is a State object read as .value and assigned as
    # a bare string, so the fake accepts a string and reads back as an
    # object, exactly like the thing it stands in for.
    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = _FakeState(value)

    def deadline_passed(self):
        return self._deadline_passed.pop(0)

    def expire(self, reason, budget):
        if budget not in AgentOperation.EXPIRY_REASONS:
            raise ValueError(f'unknown expiry budget: {budget}')
        if self.state.value in self.TERMINAL_STATES:
            return
        self.state = AgentOperation.STATE_EXPIRED
        self.expired_reason = reason
        self.expired_budget = budget

    def fail(self, message):
        if self.state.value in self.TERMINAL_STATES:
            return
        self.state = AgentOperation.STATE_ERROR
        self.failure_reason = message


class PreflightDeadlineTestCase(base.ShakenFistTestCase):
    """The deadline applies during preflight.

    Preflight's Blob.ensure_local() copy is the longest pre-queue
    delay in the system, and a deadline runs from when the REST
    request was received, so this is precisely the wait it exists to
    count.
    """

    class _FakeClusterOp:
        """Stands in for self inside _preflight().

        The method only ever touches self.state, and assigning that on
        a real NodeAgentopOp would go through the enforced state
        machine and reach the database. Calling the unbound method
        with this keeps the test to the one method under test.
        """

        def __init__(self):
            self.state = None

    def _preflight(self, aop):
        op = self._FakeClusterOp()
        node_aop_op.NodeAgentopOp._preflight(op, aop)
        return op

    def test_expired_on_entry_never_copies_a_blob(self):
        aop = _FakeAgentOp(
            [{'command': 'put-blob', 'blob_uuid': 'b1'}],
            deadline_passed=True)

        with mock.patch.object(node_aop_op, 'Blob') as mock_blob:
            self._preflight(aop)

        mock_blob.from_db.assert_not_called()
        self.assertEqual(AgentOperation.STATE_EXPIRED, aop.state.value)
        self.assertEqual(
            'the operation deadline passed before preflight',
            aop.expired_reason)
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_DEADLINE, aop.expired_budget)

    def test_expired_during_the_blob_copy(self):
        # Not expired on entry, expired by the time ensure_local()
        # returns. Without the second check this operation would be
        # promoted to queued and only caught at dequeue, having spent
        # the executor's turn getting there.
        aop = _FakeAgentOp(
            [{'command': 'put-blob', 'blob_uuid': 'b1'}],
            deadline_passed=[False, True])

        with mock.patch.object(node_aop_op, 'Blob') as mock_blob:
            self._preflight(aop)

        mock_blob.from_db.return_value.ensure_local.assert_called_once_with()
        self.assertEqual(AgentOperation.STATE_EXPIRED, aop.state.value)
        self.assertEqual(
            'the operation deadline passed during preflight',
            aop.expired_reason)
        self.assertEqual(
            AgentOperation.EXPIRY_REASON_DEADLINE, aop.expired_budget)

    def test_within_the_deadline_reaches_queued(self):
        aop = _FakeAgentOp(
            [{'command': 'put-blob', 'blob_uuid': 'b1'}],
            deadline_passed=False)

        with mock.patch.object(node_aop_op, 'Blob'):
            self._preflight(aop)

        self.assertEqual(AgentOperation.STATE_QUEUED, aop.state.value)

    def test_an_operation_with_no_blobs_reaches_queued(self):
        aop = _FakeAgentOp(
            [{'command': 'execute', 'commandline': 'true'}],
            deadline_passed=False)

        with mock.patch.object(node_aop_op, 'Blob') as mock_blob:
            self._preflight(aop)

        mock_blob.from_db.assert_not_called()
        self.assertEqual(AgentOperation.STATE_QUEUED, aop.state.value)

    def test_a_missing_blob_fails_with_its_message(self):
        # This path used to assign aop.error directly from the
        # preflight state, which the error setter refuses, so the
        # message was lost to the enclosing exception handler. It goes
        # through fail() now.
        aop = _FakeAgentOp(
            [{'command': 'put-blob', 'blob_uuid': 'b1'}],
            deadline_passed=False)

        with mock.patch.object(node_aop_op, 'Blob') as mock_blob:
            mock_blob.from_db.return_value = None
            op = self._preflight(aop)

        self.assertEqual(node_aop_op.NodeAgentopOp.STATE_ERROR, op.state)
        self.assertEqual(
            'preflight failure, blob missing: b1', aop.failure_reason)
