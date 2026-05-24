# Copyright 2019 Michael Still and contributors
"""Tests for poll_until_terminal, op.error_report, and op.raise_for_error.

The helper and the matching convenience methods on
``BaseClusterOperation`` provide the synchronous shim that callers of
the ``Network`` facade use during the per-method migration. The tests
mock the MariaDB layer so the helper is exercised in isolation.
"""

from unittest import mock

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.operations import baseoperation
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.baseoperation import poll_until_terminal
from shakenfist.operations.error_report import ErrorReport
from shakenfist.tests import base


OP_UUID = 'aabbccdd-1234-5678-abcd-000000000001'


class _StateStub:
    def __init__(self, value):
        self.value = value


class _FakeOp:
    """Minimal stand-in for a BaseClusterOperation.

    ``poll_until_terminal`` only needs ``.uuid`` and ``.state.value``,
    plus a ``from_db`` classmethod that returns a refreshed instance.
    ``raise_for_error`` also reads ``.error_report``. We implement just
    enough surface to drive the helper without instantiating the real
    object (which would require a full static-values dict).
    """

    # Class-level slot that tests mutate to control what subsequent
    # ``from_db`` calls return. A list lets tests model a sequence of
    # observations (QUEUED, QUEUED, COMPLETE, ...).
    _next_states: list = []
    _persisted_report = None

    def __init__(self, state_value):
        self.uuid = OP_UUID
        self.state = _StateStub(state_value)

    @classmethod
    def from_db(cls, object_uuid, suppress_failure_audit=False):
        # Pop the next scripted state. If the script is exhausted, hold
        # on the last observed state so the timeout path can be tested
        # deterministically.
        if cls._next_states:
            value = cls._next_states.pop(0)
        else:
            value = 'queued'
        return cls(value)

    @property
    def error_report(self):
        return type(self)._persisted_report

    # Bind the method straight off the real class so we exercise the
    # production code path verbatim.
    raise_for_error = BaseClusterOperation.raise_for_error


class PollUntilTerminalTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        _FakeOp._next_states = []
        _FakeOp._persisted_report = None

    def test_returns_immediately_on_terminal_state(self):
        _FakeOp._next_states = [BaseClusterOperation.STATE_COMPLETE]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        result = poll_until_terminal(op, timeout=1.0)

        self.assertEqual(
            BaseClusterOperation.STATE_COMPLETE, result.state.value)

    def test_observes_transition_from_queued_to_complete(self):
        # Script: two non-terminal observations, then COMPLETE. The
        # helper must keep polling until it sees the terminal state.
        _FakeOp._next_states = [
            BaseClusterOperation.STATE_QUEUED,
            BaseClusterOperation.STATE_EXECUTING,
            BaseClusterOperation.STATE_COMPLETE,
        ]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        result = poll_until_terminal(op, timeout=2.0)

        self.assertEqual(
            BaseClusterOperation.STATE_COMPLETE, result.state.value)
        self.assertEqual([], _FakeOp._next_states)

    def test_timeout_raises_operation_timeout(self):
        # Empty script -> from_db always returns 'queued'. With a tiny
        # timeout we must observe OperationTimeout.
        _FakeOp._next_states = []
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        self.assertRaises(
            exceptions.OperationTimeout,
            poll_until_terminal, op, 0.05)

    def test_error_state_is_terminal(self):
        _FakeOp._next_states = [dbo.STATE_ERROR]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        result = poll_until_terminal(op, timeout=1.0)

        self.assertEqual(dbo.STATE_ERROR, result.state.value)

    def test_deleted_state_is_terminal(self):
        _FakeOp._next_states = [dbo.STATE_DELETED]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        result = poll_until_terminal(op, timeout=1.0)

        self.assertEqual(dbo.STATE_DELETED, result.state.value)

    def test_abort_state_is_terminal(self):
        _FakeOp._next_states = [BaseClusterOperation.STATE_ABORT]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        result = poll_until_terminal(op, timeout=1.0)

        self.assertEqual(
            BaseClusterOperation.STATE_ABORT, result.state.value)

    def test_default_timeout_pulls_from_config(self):
        # Confirm the helper consults config.API_ASYNC_WAIT when timeout
        # is not supplied. We patch the symbol on the module the helper
        # imports rather than the config object itself.
        _FakeOp._next_states = [BaseClusterOperation.STATE_COMPLETE]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        with mock.patch.object(
                baseoperation.config, 'API_ASYNC_WAIT', 0.5):
            result = poll_until_terminal(op)

        self.assertEqual(
            BaseClusterOperation.STATE_COMPLETE, result.state.value)


class ErrorReportPropertyTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        _FakeOp._next_states = []
        _FakeOp._persisted_report = None

    def test_error_report_returns_none_when_no_report_persisted(self):
        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_error',
                return_value=None) as m:
            # Drive the real property by binding it onto our fake.
            op = _FakeOp(BaseClusterOperation.STATE_COMPLETE)
            # Use the real property descriptor to exercise the actual
            # mariadb call -- not the test stub on ``_FakeOp``.
            result = BaseClusterOperation.error_report.fget(op)

        self.assertIsNone(result)
        m.assert_called_once_with(OP_UUID)

    def test_error_report_returns_persisted_report(self):
        report = ErrorReport(
            code='network.dead',
            message='network is dead',
            details={},
            origin_class='shakenfist.exceptions.DeadNetwork',
            traceback='',
        )
        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_error',
                return_value=report) as m:
            op = _FakeOp(BaseClusterOperation.STATE_ERROR)
            result = BaseClusterOperation.error_report.fget(op)

        self.assertIs(report, result)
        m.assert_called_once_with(OP_UUID)

    def test_error_report_is_read_fresh_each_access(self):
        # Two accesses must produce two MariaDB reads -- no caching.
        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_error',
                return_value=None) as m:
            op = _FakeOp(BaseClusterOperation.STATE_COMPLETE)
            BaseClusterOperation.error_report.fget(op)
            BaseClusterOperation.error_report.fget(op)

        self.assertEqual(2, m.call_count)


class RaiseForErrorTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        _FakeOp._next_states = []
        _FakeOp._persisted_report = None

    def test_returns_silently_on_complete(self):
        _FakeOp._next_states = [BaseClusterOperation.STATE_COMPLETE]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        # No exception expected.
        op.raise_for_error(timeout=1.0)

    def test_returns_silently_on_abort(self):
        # ABORT is a terminal non-error state. Per the docstring,
        # raise_for_error does not distinguish ABORT from COMPLETE --
        # callers that care inspect op.state.value themselves.
        _FakeOp._next_states = [BaseClusterOperation.STATE_ABORT]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        op.raise_for_error(timeout=1.0)

    def test_returns_silently_on_deleted(self):
        _FakeOp._next_states = [dbo.STATE_DELETED]
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        op.raise_for_error(timeout=1.0)

    def test_raises_network_operation_failed_on_error(self):
        report = ErrorReport(
            code='network.ensure_mesh.failed',
            message='kaboom',
            details={'node': 'sf1'},
            origin_class='shakenfist.exceptions.EnsureMeshFailed',
            traceback='Traceback...',
        )
        _FakeOp._next_states = [dbo.STATE_ERROR]
        _FakeOp._persisted_report = report
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_error',
                return_value=report):
            exc = self.assertRaises(
                exceptions.NetworkOperationFailed,
                op.raise_for_error, 1.0)

        self.assertIs(report, exc.error_report)
        self.assertIn('network.ensure_mesh.failed', str(exc))
        self.assertIn('kaboom', str(exc))

    def test_raises_with_fallback_report_when_none_persisted(self):
        _FakeOp._next_states = [dbo.STATE_ERROR]
        _FakeOp._persisted_report = None
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        with mock.patch(
                'shakenfist.mariadb.get_cluster_operation_error',
                return_value=None):
            exc = self.assertRaises(
                exceptions.NetworkOperationFailed,
                op.raise_for_error, 1.0)

        # The fallback report carries the synthetic internal.unknown
        # code so callers still get a structured failure to branch on.
        self.assertEqual('internal.unknown', exc.error_report.code)
        self.assertIn(OP_UUID, exc.error_report.message)

    def test_timeout_propagates_from_helper(self):
        _FakeOp._next_states = []
        op = _FakeOp(BaseClusterOperation.STATE_QUEUED)

        self.assertRaises(
            exceptions.OperationTimeout,
            op.raise_for_error, 0.05)
