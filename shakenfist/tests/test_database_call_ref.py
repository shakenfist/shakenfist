# Copyright 2026 Michael Still and contributors

"""Tests for the sf-database call reference (issue 5077).

The cause of a failed database RPC is logged on the database node and
the caller only learns that its call failed. These cover the token which
joins the two: the client mints one per call and names it in its own
error, and the servicer names the same token on everything it logs while
serving that call.
"""

import concurrent.futures
import threading
from unittest import mock

import grpc

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.daemons.database import main
from shakenfist.tests import base
from shakenfist.util import caller_identity
from shakenfist.util import grpc_channel


class RefFromMetadataTestCase(base.ShakenFistTestCase):
    def test_a_minted_reference_is_read(self):
        self.assertEqual(
            '1a2b3c4d',
            main._ref_from_metadata(
                [(caller_identity.CALL_REF_METADATA_KEY, '1a2b3c4d')]))

    def test_a_reference_the_client_actually_mints_is_accepted(self):
        # Pins the reader against the minter, so a change to the shape
        # of one cannot silently stop the other from reading it.
        ref = caller_identity.new_call_ref()
        self.assertEqual(
            ref,
            main._ref_from_metadata(
                [(caller_identity.CALL_REF_METADATA_KEY, ref)]))

    def test_an_older_client_sends_nothing_and_that_is_not_an_error(self):
        self.assertEqual('', main._ref_from_metadata([]))
        self.assertEqual('', main._ref_from_metadata(None))
        self.assertEqual(
            '', main._ref_from_metadata([('caller-daemon', 'net')]))

    def test_a_forged_reference_is_dropped_not_trimmed(self):
        # The port is unauthenticated and this value lands in a log
        # line, so anything but the shape we mint is discarded whole: a
        # newline forges a log record, a long one pads every exception
        # we record for the call, and a plausible-looking one sends an
        # operator chasing a call that never happened.
        for forged in ['1a2b3c4d\nforged', 'deadbeefdeadbeef', 'zzzzzzzz',
                       '1A2B3C4D', '', '1a2b3c4', ' 1a2b3c4d']:
            self.assertEqual(
                '',
                main._ref_from_metadata(
                    [(caller_identity.CALL_REF_METADATA_KEY, forged)]),
                f'{forged!r} was accepted as a call reference')


class CallRefInterceptorTestCase(base.ShakenFistTestCase):
    def _hcd(self, metadata=None):
        return mock.Mock(
            method='/shakenfist.protos.DatabaseService/GetNode',
            invocation_metadata=metadata or [])

    def _handler(self, behaviour):
        return grpc.unary_unary_rpc_method_handler(
            behaviour,
            request_deserializer=lambda b: b,
            response_serializer=lambda b: b)

    def test_the_handler_runs_with_the_reference_bound(self):
        seen = []
        handler = self._handler(
            lambda request, context: seen.append(
                caller_identity.get_call_ref()))

        wrapped = main._CallRefInterceptor().intercept_service(
            lambda hcd: handler,
            self._hcd([(caller_identity.CALL_REF_METADATA_KEY, '1a2b3c4d')]))
        wrapped.unary_unary(None, None)

        self.assertEqual(['1a2b3c4d'], seen)
        # And not after: the pool thread goes on to serve other calls.
        self.assertEqual('', caller_identity.get_call_ref())

    def test_the_reference_is_cleared_when_the_handler_raises(self):
        def explode(request, context):
            raise ValueError('a handler which failed')

        wrapped = main._CallRefInterceptor().intercept_service(
            lambda hcd: self._handler(explode),
            self._hcd([(caller_identity.CALL_REF_METADATA_KEY, '1a2b3c4d')]))

        self.assertRaises(ValueError, wrapped.unary_unary, None, None)
        self.assertEqual('', caller_identity.get_call_ref())

    def test_the_serializers_are_preserved(self):
        # The wrapper replaces the handler, so the server must still be
        # able to decode a request and encode a reply through it.
        handler = self._handler(lambda request, context: request)
        wrapped = main._CallRefInterceptor().intercept_service(
            lambda hcd: handler,
            self._hcd([(caller_identity.CALL_REF_METADATA_KEY, '1a2b3c4d')]))

        self.assertIs(handler.request_deserializer,
                      wrapped.request_deserializer)
        self.assertIs(handler.response_serializer,
                      wrapped.response_serializer)
        self.assertFalse(wrapped.request_streaming)
        self.assertFalse(wrapped.response_streaming)

    def test_a_call_without_a_reference_is_untouched(self):
        handler = self._handler(lambda request, context: None)

        self.assertIs(
            handler,
            main._CallRefInterceptor().intercept_service(
                lambda hcd: handler, self._hcd()))

    def test_a_streaming_handler_is_untouched(self):
        # Of DatabaseService none are, but the health service's Watch is.
        handler = grpc.unary_stream_rpc_method_handler(
            lambda request, context: iter([]))

        self.assertIs(
            handler,
            main._CallRefInterceptor().intercept_service(
                lambda hcd: handler,
                self._hcd(
                    [(caller_identity.CALL_REF_METADATA_KEY, '1a2b3c4d')])))

    def test_an_unwrappable_handler_still_serves_the_call(self):
        # Never raise on the RPC path: diagnosability must not be able
        # to take the database tier down.
        sentinel = object()

        self.assertIs(
            sentinel,
            main._CallRefInterceptor().intercept_service(
                lambda hcd: sentinel,
                self._hcd(
                    [(caller_identity.CALL_REF_METADATA_KEY, '1a2b3c4d')])))

    def test_a_missing_handler_is_passed_through(self):
        self.assertIsNone(
            main._CallRefInterceptor().intercept_service(
                lambda hcd: None,
                self._hcd(
                    [(caller_identity.CALL_REF_METADATA_KEY, '1a2b3c4d')])))


class CallRefOverARealServerTestCase(base.ShakenFistTestCase):
    """The interceptor against a real gRPC server, end to end.

    The reference has to be bound in the thread which runs the handler,
    and intercept_service does not run there -- which a mocked
    continuation cannot show. A one-thread pool also proves the reset: a
    second call on the same pool thread must not inherit the first
    call's reference.
    """

    def setUp(self):
        super().setUp()
        self.observed = []
        self.server = grpc.server(
            concurrent.futures.ThreadPoolExecutor(max_workers=1),
            interceptors=[main._CallRefInterceptor()])
        self.server.add_generic_rpc_handlers((
            grpc.method_handlers_generic_handler(
                'shakenfist.protos.DatabaseService', {
                    'GetNode': grpc.unary_unary_rpc_method_handler(
                        self._behaviour,
                        request_deserializer=lambda b: b,
                        response_serializer=lambda b: b),
                }),))
        port = self.server.add_insecure_port('127.0.0.1:0')
        self.server.start()
        self.addCleanup(lambda: self.server.stop(grace=0).wait())

        # Disable http_proxy so the loopback connection is not routed
        # through any ambient proxy (CI runners typically set one).
        self.channel = grpc.insecure_channel(
            f'127.0.0.1:{port}', options=[('grpc.enable_http_proxy', 0)])
        self.addCleanup(self.channel.close)
        self.call = self.channel.unary_unary(
            '/shakenfist.protos.DatabaseService/GetNode',
            request_serializer=lambda b: b,
            response_deserializer=lambda b: b)

    def _behaviour(self, request, context):
        self.observed.append(
            (threading.current_thread().name, caller_identity.get_call_ref()))
        return b''

    def test_the_reference_reaches_the_handler_and_does_not_linger(self):
        self.call(
            b'', timeout=30,
            metadata=((caller_identity.CALL_REF_METADATA_KEY, '1a2b3c4d'),))
        self.call(b'', timeout=30)

        self.assertEqual(2, len(self.observed))
        self.assertEqual('1a2b3c4d', self.observed[0][1])
        # Same pool thread, no reference left behind on it.
        self.assertEqual(self.observed[0][0], self.observed[1][0])
        self.assertEqual('', self.observed[1][1])


class _FakeRpcError(grpc.RpcError):
    def __init__(self, code):
        self._code = code

    def code(self):
        return self._code

    def details(self):
        return 'the server said no'


def _metadata_refs(method):
    """The call references a mocked stub method was called with."""
    refs = []
    for call in method.call_args_list:
        refs.append(dict(call.kwargs['metadata']).get(
            caller_identity.CALL_REF_METADATA_KEY))
    return refs


class ClientCallRefTestCase(base.ShakenFistTestCase):
    """What the client mints, sends and says when a call fails."""

    def _method(self, side_effect=None):
        method = mock.MagicMock(side_effect=side_effect)
        method._method = b'/shakenfist.protos.DatabaseService/GetNode'
        return method

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_every_call_carries_a_reference(self, mock_stub):
        method = self._method()
        mock_stub.return_value.GetNode = method

        mariadb._grpc_call(method, mock.MagicMock())

        ref = _metadata_refs(method)[0]
        self.assertRegex(ref, '^[0-9a-f]{8}$')
        # Which is the shape the servicer accepts; a reference it drops
        # would be sent, never logged, and never missed.
        self.assertEqual(
            ref,
            main._ref_from_metadata(
                [(caller_identity.CALL_REF_METADATA_KEY, ref)]))

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_two_calls_do_not_share_a_reference(self, mock_stub):
        method = self._method()
        mock_stub.return_value.GetNode = method

        mariadb._grpc_call(method, mock.MagicMock())
        mariadb._grpc_call(method, mock.MagicMock())

        refs = _metadata_refs(method)
        self.assertEqual(2, len(set(refs)))

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_a_failed_call_names_its_reference(
            self, mock_stub, _mock_reset, _mock_time):
        # The half of the join the operator holds: the exception the
        # caller sees -- and which may be copied into an event -- names
        # the token sf-database logged the statement under (issue 5077).
        method = self._method(_FakeRpcError(grpc.StatusCode.INTERNAL))
        mock_stub.return_value.GetNode = method

        try:
            mariadb._grpc_call(method, mock.MagicMock())
            self.fail('expected DatabaseCallFailed')
        except exceptions.DatabaseCallFailed as e:
            self.assertIn(
                f'(ref {_metadata_refs(method)[0]})', str(e))

    @mock.patch('shakenfist.mariadb.time')
    @mock.patch('shakenfist.mariadb._reset_database_stub')
    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_an_exhausted_call_names_one_reference_for_every_attempt(
            self, mock_stub, _mock_reset, _mock_time):
        # One reference for the logical call, not one per attempt: a
        # client which gave up after several tries wants every
        # server-side line those tries produced, under one token.
        method = self._method(
            _FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED))
        mock_stub.return_value.GetNode = method

        try:
            mariadb._grpc_call(method, mock.MagicMock())
            self.fail('expected DatabaseUnavailable')
        except exceptions.DatabaseUnavailable as e:
            refs = _metadata_refs(method)
            self.assertGreater(len(refs), 1)
            self.assertEqual(1, len(set(refs)))
            self.assertIn(f'(ref {refs[0]}', str(e))

    @mock.patch('shakenfist.mariadb._get_database_stub')
    def test_the_reference_does_not_displace_the_caller_metadata(
            self, mock_stub):
        # The client interceptor appends caller-daemon/caller-node to
        # whatever metadata the call already has, so sending ours here
        # must not cost sf-database the per-caller load attribution it
        # labels database_requests_total with.
        method = self._method()
        mock_stub.return_value.GetNode = method
        interceptor = grpc_channel._CallerMetadataInterceptor()

        mariadb._grpc_call(method, mock.MagicMock())
        details = mock.Mock(
            method='/shakenfist.protos.DatabaseService/GetNode',
            timeout=30, metadata=method.call_args.kwargs['metadata'],
            credentials=None, wait_for_ready=None)
        seen = {}
        interceptor.intercept_unary_unary(
            lambda d, r: seen.update(dict(d.metadata)), details, None)

        self.assertIn(caller_identity.CALL_REF_METADATA_KEY, seen)
        self.assertIn('caller-daemon', seen)
        self.assertIn('caller-node', seen)
