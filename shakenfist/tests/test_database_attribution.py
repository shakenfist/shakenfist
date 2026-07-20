# Copyright 2026 Michael Still and contributors

"""Tests for the sf-database server-side caller-attribution interceptor."""

from unittest import mock

from shakenfist.daemons.database import main
from shakenfist.tests import base


class ServerMetricsInterceptorTestCase(base.ShakenFistTestCase):
    def _hcd(self, method, metadata=None):
        return mock.Mock(method=method, invocation_metadata=metadata or [])

    def test_method_to_operation(self):
        self.assertEqual(
            'GetNode',
            main._method_to_operation(
                '/shakenfist.protos.DatabaseService/GetNode'))
        self.assertEqual('unknown', main._method_to_operation(''))

    def test_caller_from_metadata(self):
        self.assertEqual(
            'net', main._caller_from_metadata([('caller-daemon', 'net')]))
        self.assertEqual('unknown', main._caller_from_metadata([]))
        self.assertEqual('unknown', main._caller_from_metadata(None))

    def test_counts_and_calls_through(self):
        interceptor = main._CallerMetricsInterceptor()
        child = main.DATABASE_REQUESTS.labels(
            operation='GetNode', caller_daemon='net')
        before = child._value.get()
        sentinel = object()

        result = interceptor.intercept_service(
            lambda hcd: sentinel,
            self._hcd('/shakenfist.protos.DatabaseService/GetNode',
                      [('caller-daemon', 'net')]))

        self.assertIs(sentinel, result)
        self.assertEqual(before + 1, child._value.get())

    def test_missing_metadata_counts_as_unknown(self):
        interceptor = main._CallerMetricsInterceptor()
        child = main.DATABASE_REQUESTS.labels(
            operation='GetBlob', caller_daemon='unknown')
        before = child._value.get()

        interceptor.intercept_service(
            lambda hcd: None,
            self._hcd('/shakenfist.protos.DatabaseService/GetBlob'))

        self.assertEqual(before + 1, child._value.get())

    def test_health_methods_are_skipped(self):
        interceptor = main._CallerMetricsInterceptor()
        sentinel = object()
        # Passes through without raising and without counting an operation.
        result = interceptor.intercept_service(
            lambda hcd: sentinel,
            self._hcd('/grpc.health.v1.Health/Check'))
        self.assertIs(sentinel, result)
