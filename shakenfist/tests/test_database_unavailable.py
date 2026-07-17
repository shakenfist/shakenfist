# Copyright 2019 Michael Still and contributors

"""Tests for DatabaseUnavailable propagation (issue 3373).

An unreachable database service must surface as a distinct exception,
not as the same None/False/[] return values that mean "object not
found", and the hot paths that intentionally shrug off an unreachable
database must catch it explicitly.
"""

from unittest import mock

import grpc

from shakenfist import exceptions
from shakenfist import locks
from shakenfist import mariadb
from shakenfist.daemons import daemon
from shakenfist.daemons.queues import main as queues_main
from shakenfist.tests import base


class FakeRpcError(grpc.RpcError):
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code


class GrpcCallRetryExhaustionTestCase(base.ShakenFistTestCase):
    # 'shakenfist.mariadb.time' (not '...time.sleep') so the real time
    # module is untouched; see the note on ClusterLockAcquireTestCase.
    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_unavailable_exhaustion_raises_database_unavailable(
            self, mock_stub, mock_reset, mock_time):
        method = mock.MagicMock(
            side_effect=FakeRpcError(grpc.StatusCode.UNAVAILABLE))
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._grpc_call, method, mock.MagicMock())
        self.assertEqual(mariadb.GRPC_RETRIES, method.call_count)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_non_retryable_rpc_error_raised_unchanged(
            self, mock_stub, mock_reset, mock_time):
        method = mock.MagicMock(
            side_effect=FakeRpcError(grpc.StatusCode.INTERNAL))
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertRaises(
            FakeRpcError, mariadb._grpc_call, method, mock.MagicMock())
        self.assertEqual(1, method.call_count)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_unavailable_retry_keeps_the_channel(
            self, mock_stub, mock_reset, mock_time):
        # An UNAVAILABLE means the backend is unreachable and round_robin
        # already routes around it, so the retry must reuse the warm channel
        # rather than discard it for a cold one (#3430). A rebuild here would
        # amplify a single gateway's restart into a client-wide outage.
        method = mock.MagicMock(
            side_effect=[FakeRpcError(grpc.StatusCode.UNAVAILABLE), 'ok'])
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertEqual('ok', mariadb._grpc_call(method, mock.MagicMock()))
        self.assertEqual(2, method.call_count)
        mock_reset.assert_not_called()

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_deadline_exceeded_retry_rebuilds_the_channel(
            self, mock_stub, mock_reset, mock_time):
        # A DEADLINE_EXCEEDED is the wedged-subchannel signature: round_robin
        # still thinks the subchannel is READY, so the channel must be rebuilt
        # to shed it before the retry.
        method = mock.MagicMock(
            side_effect=[
                FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED), 'ok'])
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertEqual('ok', mariadb._grpc_call(method, mock.MagicMock()))
        self.assertEqual(2, method.call_count)
        mock_reset.assert_called_once()

    @mock.patch('shakenfist.mariadb._grpc_call',
                side_effect=exceptions.DatabaseUnavailable('down'))
    @mock.patch('shakenfist.mariadb._get_database_stub')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_client_wrapper_does_not_swallow_outage(
            self, mock_use, mock_stub, mock_call):
        # The wrappers catch grpc.RpcError and return "not found";
        # DatabaseUnavailable must propagate through them instead.
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.get_node_by_fqdn, 'sf-1')


class CheckDaemonStateTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.Node.this_node',
                side_effect=exceptions.DatabaseUnavailable('down'))
    def test_unavailable_database_is_skipped(
            self, mock_this_node, mock_set_abort):
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.daemon_name = 'queues'
        d.abort_path = '/run/sf/queues.abort'

        d.check_daemon_state()
        mock_set_abort.assert_not_called()


class ClusterLockAcquireTestCase(base.ShakenFistTestCase):
    # Patch the whole time module *as referenced by shakenfist.locks*
    # rather than time.time itself: locks.py does a plain "import
    # time", so patching 'shakenfist.locks.time.time' would replace
    # time.time process-wide, and on Python <= 3.12 the logging module
    # calls time.time() for every LogRecord and consumes the
    # side_effect sequence.
    @mock.patch('shakenfist.locks.time')
    @mock.patch('shakenfist.locks.mariadb.get_cluster_lock_holder',
                return_value={'holder': None})
    @mock.patch('shakenfist.locks.mariadb.acquire_cluster_lock',
                side_effect=exceptions.DatabaseUnavailable('down'))
    def test_unavailable_database_retries_until_lock_timeout(
            self, mock_acquire, mock_holder, mock_time):
        mock_time.time.side_effect = [0, 0, 0.5, 1, 1.5]
        mock_time.sleep.return_value = None

        lock = locks.ClusterLock('instance', 'uuid', 'test-lock', timeout=1)
        self.assertRaises(exceptions.LockException, lock.__enter__)
        self.assertEqual(2, mock_acquire.call_count)


class BlockUntilHealthyTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.daemons.queues.main.daemon.check_abort_path',
                return_value=False)
    @mock.patch('shakenfist.daemons.queues.main._health_checks',
                side_effect=exceptions.DatabaseUnavailable('down'))
    def test_unavailable_database_is_unhealthy(
            self, mock_checks, mock_abort):
        # The exception must be treated as "not healthy yet" rather
        # than escaping the wait loop; the pending-shutdown abort path
        # then terminates the loop.
        queues_main._block_until_healthy(abort_path='/run/sf/queues.abort')
        mock_checks.assert_called_once()
