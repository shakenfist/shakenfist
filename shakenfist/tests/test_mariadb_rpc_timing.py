# Copyright 2026 Michael Still and contributors

"""Tests for the client-side RPC timing scope in mariadb._grpc_call()."""

from unittest import mock

import grpc
from prometheus_client import REGISTRY

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.tests import base
from shakenfist.tests.test_database_unavailable import FakeRpcError


def _count(sweep, object_type, operation):
    # The registry is process-global, so tests compare before and after
    # rather than asserting absolute values.
    return REGISTRY.get_sample_value(
        'database_client_rpc_seconds_count',
        {'sweep': sweep, 'object_type': object_type,
         'operation': operation}) or 0


def _method(side_effect):
    method = mock.MagicMock(side_effect=side_effect)
    method._method = b'/shakenfist.protos.DatabaseService/GetNode'
    return method


@mock.patch('shakenfist.mariadb._reset_database_stub')
@mock.patch('shakenfist.mariadb._get_database_stub')
class RpcTimingScopeTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # 'shakenfist.mariadb.time' (not '...time.sleep') so retry sleeps are
        # instant without touching the real time module process-wide. The
        # monotonic clock is a mock too, so give it numbers to subtract.
        patcher = mock.patch('shakenfist.mariadb.time')
        self.mock_time = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_time.monotonic.return_value = 0.0
        # Unique labels per test so a leak between tests cannot mask a bug.
        self.sweep = 'sweep-' + self.id().rsplit('.', 1)[-1]
        self.before = _count(self.sweep, 'blob', 'GetNode')

    def test_success_in_scope_observes_one_sample(
            self, mock_stub, mock_reset):
        self.mock_time.monotonic.side_effect = [10.0, 10.5]
        method = _method(['ok'])

        with mariadb.rpc_timing_scope(self.sweep, 'blob'):
            self.assertEqual(
                'ok', mariadb._grpc_call(method, mock.MagicMock()))

        self.assertEqual(
            self.before + 1, _count(self.sweep, 'blob', 'GetNode'))
        self.assertEqual(
            0.5, REGISTRY.get_sample_value(
                'database_client_rpc_seconds_sum',
                {'sweep': self.sweep, 'object_type': 'blob',
                 'operation': 'GetNode'}))

    def test_outside_scope_observes_nothing(
            self, mock_stub, mock_reset):
        method = _method(['ok'])

        self.assertEqual('ok', mariadb._grpc_call(method, mock.MagicMock()))

        self.assertEqual(self.before, _count(self.sweep, 'blob', 'GetNode'))
        self.mock_time.monotonic.assert_not_called()

    def test_exhausted_retries_still_observe(
            self, mock_stub, mock_reset):
        method = _method(FakeRpcError(grpc.StatusCode.UNAVAILABLE))
        mock_stub.return_value.GetNode = method

        with mariadb.rpc_timing_scope(self.sweep, 'blob'):
            self.assertRaises(
                exceptions.DatabaseUnavailable,
                mariadb._grpc_call, method, mock.MagicMock())

        self.assertEqual(
            self.before + 1, _count(self.sweep, 'blob', 'GetNode'))

    def test_non_retryable_error_still_observes(
            self, mock_stub, mock_reset):
        method = _method(FakeRpcError(grpc.StatusCode.INTERNAL))

        with mariadb.rpc_timing_scope(self.sweep, 'blob'):
            self.assertRaises(
                FakeRpcError,
                mariadb._grpc_call, method, mock.MagicMock())

        self.assertEqual(
            self.before + 1, _count(self.sweep, 'blob', 'GetNode'))

    def test_retried_call_is_one_sample(
            self, mock_stub, mock_reset):
        # The histogram times the whole call including retries, not each
        # attempt.
        method = _method([FakeRpcError(grpc.StatusCode.UNAVAILABLE), 'ok'])
        mock_stub.return_value.GetNode = method

        with mariadb.rpc_timing_scope(self.sweep, 'blob'):
            self.assertEqual(
                'ok', mariadb._grpc_call(method, mock.MagicMock()))

        self.assertEqual(
            self.before + 1, _count(self.sweep, 'blob', 'GetNode'))

    def test_scope_is_reset_after_the_block(
            self, mock_stub, mock_reset):
        self.assertIsNone(mariadb._RPC_TIMING_SCOPE.get())
        with mariadb.rpc_timing_scope(self.sweep, 'blob'):
            self.assertEqual(
                (self.sweep, 'blob'), mariadb._RPC_TIMING_SCOPE.get())
        self.assertIsNone(mariadb._RPC_TIMING_SCOPE.get())

    def test_scope_is_reset_when_the_body_raises(
            self, mock_stub, mock_reset):
        with self.assertRaises(KeyError):
            with mariadb.rpc_timing_scope(self.sweep, 'blob'):
                raise KeyError('boom')
        self.assertIsNone(mariadb._RPC_TIMING_SCOPE.get())

    def test_nested_scope_replaces_then_restores(
            self, mock_stub, mock_reset):
        with mariadb.rpc_timing_scope(self.sweep, 'blob'):
            with mariadb.rpc_timing_scope(self.sweep, 'instance'):
                self.assertEqual(
                    (self.sweep, 'instance'), mariadb._RPC_TIMING_SCOPE.get())
            self.assertEqual(
                (self.sweep, 'blob'), mariadb._RPC_TIMING_SCOPE.get())
        self.assertIsNone(mariadb._RPC_TIMING_SCOPE.get())


@mock.patch('shakenfist.mariadb._get_database_stub')
class DeleteObjectEventsTimingTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch('shakenfist.mariadb.time')
        self.mock_time = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_time.monotonic.return_value = 0.0
        self.sweep = 'sweep-' + self.id().rsplit('.', 1)[-1]
        self.before = _count(self.sweep, 'blob', 'DeleteObjectEvents')

    def test_success_in_scope_observes_one_sample(self, mock_stub):
        self.mock_time.monotonic.side_effect = [10.0, 10.5]
        mock_stub.return_value.DeleteObjectEvents.return_value = (
            mock.MagicMock(success=True))

        with mariadb.rpc_timing_scope(self.sweep, 'blob'):
            mariadb._grpc_delete_object_events('blob', 'uuid')

        self.assertEqual(
            self.before + 1, _count(self.sweep, 'blob', 'DeleteObjectEvents'))
        self.assertEqual(
            30.0, mock_stub.return_value.DeleteObjectEvents.call_args.kwargs['timeout'])

    def test_rpc_error_in_scope_observes_one_sample(self, mock_stub):
        mock_stub.return_value.DeleteObjectEvents.side_effect = FakeRpcError(
            grpc.StatusCode.UNAVAILABLE)

        with mariadb.rpc_timing_scope(self.sweep, 'blob'):
            mariadb._grpc_delete_object_events('blob', 'uuid')

        self.assertEqual(
            self.before + 1, _count(self.sweep, 'blob', 'DeleteObjectEvents'))

    def test_outside_scope_observes_nothing(self, mock_stub):
        mock_stub.return_value.DeleteObjectEvents.return_value = (
            mock.MagicMock(success=True))

        mariadb._grpc_delete_object_events('blob', 'uuid')

        self.assertEqual(
            self.before, _count(self.sweep, 'blob', 'DeleteObjectEvents'))
        self.mock_time.monotonic.assert_not_called()
