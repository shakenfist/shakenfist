# Copyright 2019 Michael Still and contributors

"""Tests for DatabaseUnavailable propagation (issue 3373).

An unreachable database service must surface as a distinct exception,
not as the same None/False/[] return values that mean "object not
found", and the hot paths that intentionally shrug off an unreachable
database must catch it explicitly.
"""

import json
import uuid
from unittest import mock

import grpc
from sqlalchemy.exc import OperationalError

from shakenfist import exceptions
from shakenfist import locks
from shakenfist import mariadb
from shakenfist.daemons import daemon
from shakenfist.daemons.database import main as daemons_database_main
from shakenfist.daemons.queues import main as queues_main
from shakenfist.daemons.queues import workitem as queues_workitem
from shakenfist.protos import database_pb2
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class FakeRpcError(grpc.RpcError):
    def __init__(self, code, details=None):
        self._code = code
        self._details = details

    def code(self):
        return self._code

    def details(self):
        return self._details


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
        self.assertEqual(mariadb.GRPC_UNAVAILABLE_RETRIES, method.call_count)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_unavailable_patience_outlasts_reconnect_backoff(
            self, mock_stub, mock_reset, mock_time):
        # UNAVAILABLE fails fast, so it gets a larger retry budget than the
        # old three attempts: a brief window with no READY backend (the
        # database-tier rolling restart, #3430) must be ridden out, not
        # amplified into a cluster-wide DatabaseUnavailable storm. Here the
        # call only succeeds on the fifth attempt -- beyond the deadline
        # budget of GRPC_RETRIES, within GRPC_UNAVAILABLE_RETRIES.
        method = mock.MagicMock(
            side_effect=[FakeRpcError(grpc.StatusCode.UNAVAILABLE)] * 4 +
            ['ok'])
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertEqual('ok', mariadb._grpc_call(method, mock.MagicMock()))
        self.assertEqual(5, method.call_count)
        mock_reset.assert_not_called()

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_deadline_exceeded_capped_below_unavailable_budget(
            self, mock_stub, mock_reset, mock_time):
        # Every DEADLINE_EXCEEDED attempt blocks for the full GRPC_TIMEOUT
        # before failing, so those stay capped at GRPC_RETRIES even though
        # the fast-failing UNAVAILABLE budget is larger -- otherwise the
        # extended patience would stretch a wedged-subchannel caller's
        # worst case from ~1.5 to ~3 minutes.
        method = mock.MagicMock(
            side_effect=FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED))
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._grpc_call, method, mock.MagicMock())
        self.assertEqual(mariadb.GRPC_RETRIES, method.call_count)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_exhaustion_reports_the_status_code_and_budget(
            self, mock_stub, mock_reset, mock_time):
        # Issue 3607: 262 of these were logged in 23 minutes with
        # exception_class and stack_trace both null, so nothing recorded
        # whether the calls timed out, were cancelled, or hit a resource
        # limit -- nor that "after 1 attempts" is the bounded budget doing
        # its job rather than a broken retry loop.
        method = mock.MagicMock(side_effect=FakeRpcError(
            grpc.StatusCode.DEADLINE_EXCEEDED, 'Deadline Exceeded'))
        method._method = (
            '/shakenfist.protos.DatabaseService/GetNodeDaemonState')
        mock_stub.return_value.GetNodeDaemonState = method

        try:
            mariadb._grpc_call(
                method, mock.MagicMock(),
                timeout=mariadb.BOUNDED_QUERY_TIMEOUT, max_slow_failures=1)
            self.fail('DatabaseUnavailable not raised')
        except exceptions.DatabaseUnavailable as e:
            message = str(e)
        self.assertIn('GetNodeDaemonState', message)
        self.assertIn('failed after 1 attempts', message)
        self.assertIn(f'deadline {mariadb.BOUNDED_QUERY_TIMEOUT}s', message)
        self.assertIn('slow attempt budget 1', message)
        self.assertIn('DEADLINE_EXCEEDED', message)
        self.assertIn('Deadline Exceeded', message)

    def test_error_description_survives_a_bare_rpc_error(self):
        # grpc.RpcError itself has neither code() nor details(); only the
        # subclass the runtime raises does. The describer must not blow up
        # on the bare one _grpc_call seeds last_error with.
        self.assertIn(
            'RpcError', mariadb._describe_rpc_error(grpc.RpcError()))

    def test_error_description_of_a_closed_channel_value_error(self):
        # The other thing _grpc_call can exhaust on is the concurrent
        # channel-close ValueError, which has no gRPC status at all.
        described = mariadb._describe_rpc_error(
            ValueError('Cannot invoke RPC on closed channel!'))
        self.assertIn('ValueError', described)
        self.assertIn('closed channel', described)

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

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_mixed_retry_sequence_rebuilds_only_for_deadline(
            self, mock_stub, mock_reset, mock_time):
        # The rebuild decision is per attempt, not per call: an UNAVAILABLE
        # followed by a DEADLINE_EXCEEDED must keep the warm channel for the
        # first retry and rebuild exactly once for the deadline attempt.
        method = mock.MagicMock(
            side_effect=[
                FakeRpcError(grpc.StatusCode.UNAVAILABLE),
                FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED),
                'ok'])
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertEqual('ok', mariadb._grpc_call(method, mock.MagicMock()))
        self.assertEqual(3, method.call_count)
        mock_reset.assert_called_once()

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_cancelled_retry_rebuilds_the_channel(
            self, mock_stub, mock_reset, mock_time):
        # A CANCELLED never comes from the server -- a stopping sf-database
        # surfaces as UNAVAILABLE -- it means the channel our in-flight call
        # was riding on was torn down locally (issue 3605). So retry, and
        # rebuild first, because that channel is closed and reusing it only
        # earns a "closed channel" ValueError on every remaining attempt.
        method = mock.MagicMock(
            side_effect=[FakeRpcError(grpc.StatusCode.CANCELLED), 'ok'])
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertEqual('ok', mariadb._grpc_call(method, mock.MagicMock()))
        self.assertEqual(2, method.call_count)
        mock_reset.assert_called_once()

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_cancelled_exhaustion_raises_database_unavailable(
            self, mock_stub, mock_reset, mock_time):
        # A cancellation fails fast (nothing waited out a deadline), so it
        # gets the larger fast-failing budget, and once that is spent the
        # caller sees DatabaseUnavailable rather than an RpcError the
        # wrappers would translate into "object not found".
        method = mock.MagicMock(
            side_effect=FakeRpcError(grpc.StatusCode.CANCELLED))
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        mock_stub.return_value.GetNode = method

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._grpc_call, method, mock.MagicMock())
        self.assertEqual(mariadb.GRPC_UNAVAILABLE_RETRIES, method.call_count)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_cancelled_dequeue_is_not_reported_as_an_empty_queue(
            self, mock_stub, mock_reset, mock_time):
        # The failure that made this matter: a Dequeue cancelled mid-flight
        # during a redeploy was caught by the wrapper and returned as [],
        # which the queues daemon cannot tell apart from "no work waiting".
        method = mock.MagicMock(
            side_effect=FakeRpcError(grpc.StatusCode.CANCELLED))
        method._method = b'/shakenfist.protos.DatabaseService/Dequeue'
        mock_stub.return_value.Dequeue = method

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._grpc_work_queue_dequeue_batch, ['queue-1'], 1)

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_cancelled_write_is_not_reported_as_a_failed_write(
            self, mock_stub, mock_reset, mock_time):
        # Same shape on the write side: a cancelled SetNodeDaemonState used
        # to return False, which reads as "the database refused this", not
        # "we could not ask".
        method = mock.MagicMock(
            side_effect=FakeRpcError(grpc.StatusCode.CANCELLED))
        method._method = (
            b'/shakenfist.protos.DatabaseService/SetNodeDaemonState')
        mock_stub.return_value.SetNodeDaemonState = method

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._grpc_set_node_daemon_state,
            uuid.UUID('11111111-1111-1111-1111-111111111111'), 'api',
            'running', 1234.0, None)

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


class NamespaceAttributesFetchFailureTestCase(base.ShakenFistTestCase):
    """Issue 3522: a failed namespace key set fetch must raise, not read
    as an authoritative "no attributes row" (which the auth path treats
    as an empty key set and answers with a 401)."""

    @mock.patch('shakenfist.mariadb._grpc_call',
                side_effect=FakeRpcError(grpc.StatusCode.RESOURCE_EXHAUSTED))
    @mock.patch('shakenfist.mariadb._get_database_stub')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_grpc_error_raises_database_unavailable(
            self, mock_use, mock_stub, mock_call):
        # The trigger from issue 3521: a keys blob over the 4MiB message
        # cap fails RESOURCE_EXHAUSTED, which is not retryable and so
        # reaches the wrapper as a raw RpcError rather than as a
        # DatabaseUnavailable from _grpc_call's retry exhaustion.
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.get_namespace_attributes, 'banana')

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_direct_operational_error_raises_database_unavailable(
            self, mock_use, mock_engine):
        # The direct path feeds the database daemon's servicer: swallowing
        # an OperationalError there made the daemon reply found=False, so
        # a MariaDB outage read as "namespace has no keys" cluster-wide.
        mock_engine.return_value.connect.side_effect = OperationalError(
            'SELECT', {}, Exception('server has gone away'))
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.get_namespace_attributes, 'banana')


class NamespaceKeyFetchFailureTestCase(base.ShakenFistTestCase):
    """Issue 3522, post auth federation phase 2: the auth path reads
    keys via find_namespace_keys (/auth) and get_namespace_key_by_name
    (token validation), so those reads must also raise on failure
    rather than conflating an outage with "no such key"."""

    @mock.patch('shakenfist.mariadb._grpc_call',
                side_effect=FakeRpcError(grpc.StatusCode.RESOURCE_EXHAUSTED))
    @mock.patch('shakenfist.mariadb._get_database_stub')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_grpc_find_error_raises_database_unavailable(
            self, mock_use, mock_stub, mock_call):
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.find_namespace_keys, 'banana', False, 0.0)

    @mock.patch('shakenfist.mariadb._grpc_call',
                side_effect=FakeRpcError(grpc.StatusCode.RESOURCE_EXHAUSTED))
    @mock.patch('shakenfist.mariadb._get_database_stub')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_grpc_point_read_error_raises_database_unavailable(
            self, mock_use, mock_stub, mock_call):
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.get_namespace_key_by_name, 'banana', 'key1')

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_direct_find_operational_error_raises_database_unavailable(
            self, mock_use, mock_engine):
        mock_engine.return_value.connect.side_effect = OperationalError(
            'SELECT', {}, Exception('server has gone away'))
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.find_namespace_keys, 'banana', False, 0.0)

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_direct_point_read_operational_error_raises_database_unavailable(
            self, mock_use, mock_engine):
        mock_engine.return_value.connect.side_effect = OperationalError(
            'SELECT', {}, Exception('server has gone away'))
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.get_namespace_key_by_name, 'banana', 'key1')


class GrpcCallMethodNameTestCase(base.ShakenFistTestCase):
    # The wire method name is read from the private ``_method`` attribute of
    # grpcio's multicallable so the retry path can re-resolve a fresh bound
    # method by name. That attribute's type differs across grpcio releases and
    # stub flavours -- older unregistered multicallables expose ``bytes``,
    # current registered multicallables (our generated stubs use
    # ``_registered_method=True``) expose ``str``. Both must work; assuming
    # ``bytes`` and calling ``.decode()`` unconditionally crashed every gRPC
    # database call with ``AttributeError`` against current grpcio.
    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def _assert_retry_reresolves(
            self, wire_method, mock_stub, mock_reset, mock_time):
        # First attempt fails DEADLINE_EXCEEDED (which rebuilds the channel),
        # so the retry must re-resolve the method by name off a fresh stub --
        # the code path that depends on parsing ``_method``.
        failing = mock.MagicMock(
            side_effect=FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED))
        failing._method = wire_method
        succeeding = mock.MagicMock(return_value='ok')
        mock_stub.return_value.GetNode = succeeding

        self.assertEqual(
            'ok', mariadb._grpc_call(failing, mock.MagicMock()))
        # The retry resolved GetNode by name from the fresh stub and called it.
        succeeding.assert_called_once()

    def test_method_name_from_bytes(self):
        self._assert_retry_reresolves(
            b'/shakenfist.protos.DatabaseService/GetNode')

    def test_method_name_from_str(self):
        # The regression: current grpcio hands us a ``str`` here.
        self._assert_retry_reresolves(
            '/shakenfist.protos.DatabaseService/GetNode')


class CheckDaemonStateTestCase(base.ShakenFistTestCase):
    NODE_UUID = '11111111-1111-1111-1111-111111111111'

    def _daemon(self):
        # Build a bare Daemon without running __init__ (which sets a
        # process title, installs signal handlers, etc). check_daemon_state
        # only needs these attributes.
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.daemon_name = 'queues'
        d.abort_path = '/run/sf/queues.abort'
        d._last_daemon_state_check = 0.0
        d._daemon_state_poll_interval = daemon.DAEMON_STATE_POLL_INTERVAL
        return d

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state',
                side_effect=exceptions.DatabaseUnavailable('down'))
    def test_unavailable_database_is_skipped(
            self, mock_get_state, mock_set_abort):
        with mock.patch.object(daemon.config, 'NODE_UUID', self.NODE_UUID):
            self._daemon().check_daemon_state()
        mock_set_abort.assert_not_called()

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.Node.this_node')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state',
                return_value=mock.Mock(value=daemon.Node.DAEMON_STATE_RUNNING))
    def test_does_not_fetch_the_node_object(
            self, mock_get_state, mock_this_node, mock_set_abort):
        # The whole point of phase 1: reach the daemon-state row directly by
        # UUID, never via Node.this_node() (which costs a get_node round trip).
        with mock.patch.object(daemon.config, 'NODE_UUID', self.NODE_UUID):
            self._daemon().check_daemon_state()
        mock_this_node.assert_not_called()
        # bounded=True: the poll runs upstream of the systemd watchdog pet
        # and must not block past WatchdogSec on a stalled database (3586).
        mock_get_state.assert_called_once_with(
            uuid.UUID(self.NODE_UUID), 'queues', bounded=True)

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state')
    def test_abort_set_on_stopping_and_stopped(
            self, mock_get_state, mock_set_abort):
        for state in (daemon.Node.DAEMON_STATE_STOPPING,
                      daemon.Node.DAEMON_STATE_STOPPED):
            mock_set_abort.reset_mock()
            mock_get_state.return_value = mock.Mock(value=state)
            with mock.patch.object(daemon.config, 'NODE_UUID', self.NODE_UUID):
                self._daemon().check_daemon_state()
            mock_set_abort.assert_called_once()

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state')
    def test_no_abort_on_running_or_missing(
            self, mock_get_state, mock_set_abort):
        for row in (mock.Mock(value=daemon.Node.DAEMON_STATE_RUNNING), None):
            mock_get_state.return_value = row
            with mock.patch.object(daemon.config, 'NODE_UUID', self.NODE_UUID):
                self._daemon().check_daemon_state()
            mock_set_abort.assert_not_called()

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state',
                return_value=None)
    def test_missing_node_uuid_is_skipped(
            self, mock_get_state, mock_set_abort):
        with mock.patch.object(daemon.config, 'NODE_UUID', None):
            self._daemon().check_daemon_state()
        mock_get_state.assert_not_called()
        mock_set_abort.assert_not_called()

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state',
                return_value=mock.Mock(value=daemon.Node.DAEMON_STATE_RUNNING))
    def test_read_is_rate_limited(self, mock_get_state, mock_set_abort):
        d = self._daemon()
        with mock.patch.object(daemon.config, 'NODE_UUID', self.NODE_UUID):
            # First call reads (last-check timestamp starts at 0.0).
            d.check_daemon_state()
            self.assertEqual(1, mock_get_state.call_count)

            # A second call within the interval must not touch the database.
            d.check_daemon_state()
            self.assertEqual(1, mock_get_state.call_count)

            # Once the interval has elapsed, the read happens again.
            d._last_daemon_state_check -= (daemon.DAEMON_STATE_POLL_INTERVAL + 1)
            d.check_daemon_state()
            self.assertEqual(2, mock_get_state.call_count)

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state',
                side_effect=exceptions.DatabaseUnavailable('down'))
    def test_poll_backs_off_while_the_database_cannot_answer(
            self, mock_get_state, mock_set_abort):
        # Re-asking every two seconds during a database stall is what piled
        # abandoned reads onto sf-database and sustained the failure burst
        # in issue 3607.
        d = self._daemon()
        with mock.patch.object(daemon.config, 'NODE_UUID', self.NODE_UUID):
            expected = daemon.DAEMON_STATE_POLL_INTERVAL
            for _ in range(10):
                d._last_daemon_state_check = 0.0
                d.check_daemon_state()
                expected = min(expected * 2,
                               daemon.DAEMON_STATE_POLL_MAX_INTERVAL)
                self.assertEqual(expected, d._daemon_state_poll_interval)

        # Capped, not unbounded.
        self.assertEqual(daemon.DAEMON_STATE_POLL_MAX_INTERVAL,
                         d._daemon_state_poll_interval)

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state')
    def test_poll_backoff_resets_on_success(
            self, mock_get_state, mock_set_abort):
        d = self._daemon()
        d._daemon_state_poll_interval = daemon.DAEMON_STATE_POLL_MAX_INTERVAL
        mock_get_state.return_value = mock.Mock(
            value=daemon.Node.DAEMON_STATE_RUNNING)

        with mock.patch.object(daemon.config, 'NODE_UUID', self.NODE_UUID):
            d.check_daemon_state()

        self.assertEqual(daemon.DAEMON_STATE_POLL_INTERVAL,
                         d._daemon_state_poll_interval)

    @mock.patch('shakenfist.daemons.daemon.set_abort_path')
    @mock.patch('shakenfist.daemons.daemon.mariadb.get_node_daemon_state',
                side_effect=exceptions.DatabaseUnavailable('down'))
    def test_backoff_actually_suppresses_the_next_read(
            self, mock_get_state, mock_set_abort):
        d = self._daemon()
        with mock.patch.object(daemon.config, 'NODE_UUID', self.NODE_UUID):
            d.check_daemon_state()
            self.assertEqual(1, mock_get_state.call_count)

            # The base interval has elapsed, but the backed off one has not.
            d._last_daemon_state_check -= (
                daemon.DAEMON_STATE_POLL_INTERVAL + 1)
            d.check_daemon_state()
            self.assertEqual(1, mock_get_state.call_count)


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


class FederationRepliesFailClosedTestCase(base.ShakenFistTestCase):
    """A reply nobody could produce must not read as a permissive one.

    Both federation replies used to signal failure by carrying a
    non-empty `error`, which left the fail closed property resting on
    string formatting. An exception raised with no args -- `KeyError()`,
    a bare `AttributeError()` -- has an empty `str()`, so the reply
    arrived as `attempts=0, error=''`. Read as a count, that says
    "nobody has tried this minute", which is an allow on the one
    unauthenticated endpoint in the API.

    `ok` has to be set deliberately on the success path, so no
    formatting accident can produce one. Each refusal below is paired
    with the corresponding success, because a client which raised
    unconditionally would satisfy the refusals on its own.
    """

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_counter_failure_with_no_message_still_refuses(self, mock_stub):
        mock_stub.return_value.CountFederatedAttempt.return_value = \
            database_pb2.CountFederatedAttemptReply(
                attempts=0, error='', ok=False)

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._grpc_count_federated_attempt, '10.0.0.1', 1234)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_counted_attempt_is_returned(self, mock_stub):
        mock_stub.return_value.CountFederatedAttempt.return_value = \
            database_pb2.CountFederatedAttemptReply(
                attempts=7, error='', ok=True)

        self.assertEqual(
            7, mariadb._grpc_count_federated_attempt('10.0.0.1', 1234))

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_claim_failure_with_no_message_still_refuses(self, mock_stub):
        mock_stub.return_value.RecordFederatedExchange.return_value = \
            database_pb2.RecordFederatedExchangeReply(
                recorded=False, error='', ok=False)

        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb._grpc_record_federated_exchange,
            'token-1', uuid.uuid4(), 1.0)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_first_claim_is_recorded(self, mock_stub):
        mock_stub.return_value.RecordFederatedExchange.return_value = \
            database_pb2.RecordFederatedExchangeReply(
                recorded=True, error='', ok=True)

        self.assertTrue(mariadb._grpc_record_federated_exchange(
            'token-1', uuid.uuid4(), 1.0))

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_an_already_claimed_pair_is_a_replay_not_a_failure(self, mock_stub):
        # recorded False with ok True is the replay answer. It has to stay
        # distinguishable from "we could not find out": both refuse the
        # exchange, but only one of them should wake anybody up.
        mock_stub.return_value.RecordFederatedExchange.return_value = \
            database_pb2.RecordFederatedExchangeReply(
                recorded=False, error='', ok=True)

        self.assertFalse(mariadb._grpc_record_federated_exchange(
            'token-1', uuid.uuid4(), 1.0))


class CorruptMappingRuleOverGrpcTestCase(base.ShakenFistTestCase):
    """A damaged rule must stay a damaged rule across the RPC.

    The exchange refuses a rule it cannot decode with a generic 401,
    and external_view() marks it unusable so one bad row does not take
    a namespace's listing down. Both are driven by catching
    CorruptMappingRule in the API process, and the decode happens in
    sf-database, so neither works unless the fault survives the trip.

    These drive the real servicer method and the real client wrapper.
    Patching MappingRule._attributes -- which is how the behavioural
    tests in test_federated_exchange.py and test_rules.py raise this --
    exercises only the single-process path and would pass just as
    happily with the RPC flattening the fault into INTERNAL.
    """

    def setUp(self):
        super().setUp()
        self.rule_uuid = uuid.uuid4()
        self.servicer = daemons_database_main.DatabaseService(
            mock.MagicMock())

    @mock.patch('shakenfist.mariadb._direct_get_mapping_rule_attributes')
    def test_the_servicer_reports_corruption_as_a_field(self, mock_get):
        mock_get.side_effect = exceptions.CorruptMappingRule(
            f'mapping rule {self.rule_uuid} has undecodable scopes')
        context = mock.MagicMock()

        reply = self.servicer.GetMappingRuleAttributes(
            database_pb2.GetMappingRuleAttributesRequest(
                uuid=str(self.rule_uuid)),
            context)

        self.assertTrue(reply.corrupt)
        # found stays True: the row is there and cannot be read, which
        # is not the same answer as no such row.
        self.assertTrue(reply.found)
        # Not an error status. INTERNAL is what made this invisible.
        context.set_code.assert_not_called()

    @mock.patch('shakenfist.mariadb._direct_get_mapping_rule_attributes')
    def test_the_servicer_does_not_return_the_rule_uuid(self, mock_get):
        # The exception text names the rule, and the exchange is
        # unauthenticated, so the uuid must not ride back in the reply.
        mock_get.side_effect = exceptions.CorruptMappingRule(
            f'mapping rule {self.rule_uuid} has undecodable scopes')
        context = mock.MagicMock()

        reply = self.servicer.GetMappingRuleAttributes(
            database_pb2.GetMappingRuleAttributesRequest(
                uuid=str(self.rule_uuid)),
            context)

        context.set_details.assert_not_called()
        self.assertNotIn(str(self.rule_uuid), str(reply))

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_the_client_re_raises_corruption(self, mock_stub):
        mock_stub.return_value.GetMappingRuleAttributes.return_value = \
            database_pb2.GetMappingRuleAttributesReply(
                found=True, corrupt=True)

        # CorruptMappingRule, not DatabaseUnavailable. The API tells
        # them apart: one refuses the exchange, the other is a 503.
        self.assertRaises(
            exceptions.CorruptMappingRule,
            mariadb._grpc_get_mapping_rule_attributes, self.rule_uuid)

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_missing_row_is_still_not_corruption(self, mock_stub):
        mock_stub.return_value.GetMappingRuleAttributes.return_value = \
            database_pb2.GetMappingRuleAttributesReply(found=False)

        self.assertIsNone(
            mariadb._grpc_get_mapping_rule_attributes(self.rule_uuid))

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_healthy_row_is_still_returned(self, mock_stub):
        # Without this an implementation which raised unconditionally
        # would pass everything above.
        mock_stub.return_value.GetMappingRuleAttributes.return_value = \
            database_pb2.GetMappingRuleAttributesReply(
                found=True,
                data=database_pb2.MappingRuleAttributesProto(
                    uuid=str(self.rule_uuid),
                    issuer='an-issuer',
                    bound_claims=json.dumps({'repo': 'shakenfist/shakenfist'}),
                    scopes=json.dumps(['namespace']),
                    key_ttl=3600,
                    key_name_prefix='ci'))

        attrs = mariadb._grpc_get_mapping_rule_attributes(self.rule_uuid)
        self.assertEqual('an-issuer', attrs.issuer)
        self.assertEqual(['namespace'], attrs.scopes)


class ClusterOperationFetchFailureTestCase(base.ShakenFistTestCase):
    """Issue 3716: a failed cluster operation fetch must raise, not read
    as an authoritative "no such operation". The queue workers discard a
    work item whose operation lookup returns None, so conflating a
    database failure with not-found silently loses queued operations (a
    "Too many connections" storm dropped 596 of them, two of which were
    network_destroys whose vxlans leaked permanently)."""

    @mock.patch('shakenfist.mariadb._grpc_call',
                side_effect=FakeRpcError(grpc.StatusCode.INTERNAL))
    @mock.patch('shakenfist.mariadb._get_database_stub')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=True)
    def test_grpc_error_raises_database_unavailable(
            self, mock_use, mock_stub, mock_call):
        # INTERNAL is what the database daemon's servicer returns when
        # the direct path raises; it is not retryable and so reaches the
        # wrapper as a raw RpcError rather than as a DatabaseUnavailable
        # from _grpc_call's retry exhaustion.
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.get_cluster_operation, str(uuid.uuid4()))

    @mock.patch('shakenfist.mariadb._get_engine')
    @mock.patch('shakenfist.mariadb._use_database_service',
                return_value=False)
    def test_direct_operational_error_raises_database_unavailable(
            self, mock_use, mock_engine):
        # The direct path feeds the database daemon's servicer:
        # swallowing an OperationalError there made the daemon reply
        # found=False, so a MariaDB outage read as "operation does not
        # exist" cluster-wide.
        mock_engine.return_value.connect.side_effect = OperationalError(
            'SELECT', {}, Exception('Too many connections'))
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.get_cluster_operation, str(uuid.uuid4()))


class QueuesWorkItemDatabaseUnavailableTestCase(base.ShakenFistTestCase):
    """Issue 3716: a database outage during the queue worker's operation
    lookup must leave the work item claimed for the stuck-row reaper to
    re-queue, not resolve (and so permanently discard) it."""

    OP_UUID = 'f34e9c4c-3b17-41ca-9bc1-2f5c23111412'

    def _make_job(self):
        with mock.patch('shakenfist.daemons.queues.workitem.daemon'):
            return queues_workitem.Job(
                'banana-user-facing', f'job-{self.OP_UUID}',
                {
                    'operation_type': 'node_net_op',
                    'operation_uuid': self.OP_UUID
                })

    def test_lookup_outage_abandons_work_item(self):
        job = self._make_job()
        with mock.patch(
            'shakenfist.daemons.queues.workitem.mariadb'
        ) as mock_mariadb, mock.patch(
            'shakenfist.daemons.queues.workitem.util_concurrency'
        ), mock.patch(
            'shakenfist.daemons.queues.workitem.get_object_class'
        ) as mock_goc:
            mock_goc.return_value.from_db.side_effect = \
                exceptions.DatabaseUnavailable('down')

            # Must not raise (the worker thread lives on), and must not
            # resolve the work item (the stuck-row reaper re-queues it).
            job.execute()
            mock_mariadb.resolve_work_item.assert_not_called()

    def test_missing_operation_still_resolves(self):
        # A lookup which authoritatively finds nothing is still a
        # discard: the operation was hard-deleted, not lost.
        job = self._make_job()
        with mock.patch(
            'shakenfist.daemons.queues.workitem.mariadb'
        ) as mock_mariadb, mock.patch(
            'shakenfist.daemons.queues.workitem.util_concurrency'
        ), mock.patch(
            'shakenfist.daemons.queues.workitem.get_object_class'
        ) as mock_goc:
            mock_goc.return_value.from_db.return_value = None

            job.execute()
            mock_mariadb.resolve_work_item.assert_called_once_with(
                'banana-user-facing', f'job-{self.OP_UUID}')

    def test_successful_execution_resolves(self):
        job = self._make_job()
        with mock.patch(
            'shakenfist.daemons.queues.workitem.mariadb'
        ) as mock_mariadb, mock.patch(
            'shakenfist.daemons.queues.workitem.util_concurrency'
        ), mock.patch.object(
            job, '_cluster_operation_execute'
        ):
            job.execute()
            mock_mariadb.resolve_work_item.assert_called_once_with(
                'banana-user-facing', f'job-{self.OP_UUID}')

    def test_resolve_outage_does_not_kill_the_worker(self):
        job = self._make_job()
        with mock.patch(
            'shakenfist.daemons.queues.workitem.mariadb'
        ) as mock_mariadb, mock.patch(
            'shakenfist.daemons.queues.workitem.util_concurrency'
        ), mock.patch.object(
            job, '_cluster_operation_execute'
        ):
            mock_mariadb.resolve_work_item.side_effect = \
                exceptions.DatabaseUnavailable('down')

            # The row stays claimed and the stuck-row reaper re-queues
            # it, exactly as for a worker crash.
            job.execute()


class ReferenceReadFailureTestCase(base.ShakenFistTestCase):
    """A failed reference read must survive as None all the way up.

    The cleaner's deletion test is an OR over two lists, and the node's
    own blob locations are the second one. Every layer of that read used
    to flatten failure to [], so the complement-set hazard of #3638 was
    reachable through it even after get_active_blob_uuids() was fixed.
    """

    @mock.patch('shakenfist.mariadb._grpc_call',
                side_effect=FakeRpcError(grpc.StatusCode.RESOURCE_EXHAUSTED))
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_oversized_reply_is_a_failed_read(self, mock_stub, mock_call):
        # RESOURCE_EXHAUSTED is not retryable, so it arrives here as a
        # raw RpcError rather than as DatabaseUnavailable from the retry
        # budget. Both must end up as None, not [].
        self.assertIsNone(
            mariadb._grpc_get_references_from(ObjectType.NODE, 'sf-1'))

    @mock.patch('shakenfist.mariadb._get_engine')
    def test_operational_error_is_a_failed_read(self, mock_engine):
        # The likelier failure: MariaDB itself errors while sf-database
        # is healthy. This is the path the servicer runs.
        mock_engine.return_value.connect.side_effect = OperationalError(
            'select', {}, Exception('lock wait timeout'))

        self.assertIsNone(
            mariadb._direct_get_references_from(ObjectType.NODE, 'sf-1'))

    @mock.patch('shakenfist.mariadb._get_references_from', return_value=None)
    def test_node_blob_uuids_raises(self, mock_refs):
        self.assertRaises(
            exceptions.DatabaseUnavailable,
            mariadb.get_node_blob_uuids, 'sf-1')

    @mock.patch('shakenfist.mariadb._get_references_from', return_value=[])
    def test_node_with_no_blobs_does_not_raise(self, mock_refs):
        # The negative control: an empty answer is a real answer.
        self.assertEqual([], mariadb.get_node_blob_uuids('sf-1'))

    @mock.patch('shakenfist.mariadb._get_references_from', return_value=None)
    def test_tolerant_accessor_still_collapses(self, mock_refs):
        # get_references_from() keeps collapsing deliberately, because
        # every one of its callers iterates the result. Pinning that
        # keeps the decision a decision rather than an oversight.
        self.assertEqual(
            [], mariadb.get_references_from(ObjectType.NODE, 'sf-1'))
