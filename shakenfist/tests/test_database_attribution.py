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

    def test_task_from_metadata(self):
        self.assertEqual(
            'maintain_blobs',
            main._task_from_metadata([('caller-task', 'maintain_blobs')]))
        # No task is the common case, and is not an error.
        self.assertEqual('', main._task_from_metadata([]))
        self.assertEqual('', main._task_from_metadata(None))

    def test_an_unknown_task_is_not_a_label_of_its_own(self):
        # The port is unauthenticated, so an unrecognised value must not
        # mint a prometheus child: that is an unbounded cardinality leak
        # in the exposition every load check parses.
        self.assertEqual(
            '', main._task_from_metadata([('caller-task', 'forged')]))

    def test_counts_and_calls_through(self):
        interceptor = main._CallerMetricsInterceptor()
        child = main.DATABASE_REQUESTS.labels(
            operation='GetNode', caller_daemon='net', caller_task='')
        before = child._value.get()
        sentinel = object()

        result = interceptor.intercept_service(
            lambda hcd: sentinel,
            self._hcd('/shakenfist.protos.DatabaseService/GetNode',
                      [('caller-daemon', 'net')]))

        self.assertIs(sentinel, result)
        self.assertEqual(before + 1, child._value.get())

    def test_counts_a_named_task_separately(self):
        # The point of the label: two passes of the same daemon making
        # the same call are separable (issue 4399).
        interceptor = main._CallerMetricsInterceptor()
        tasked = main.DATABASE_REQUESTS.labels(
            operation='GetObjectState', caller_daemon='cleaner',
            caller_task='observe_local_blobs')
        untasked = main.DATABASE_REQUESTS.labels(
            operation='GetObjectState', caller_daemon='cleaner',
            caller_task='')
        before_tasked = tasked._value.get()
        before_untasked = untasked._value.get()

        interceptor.intercept_service(
            lambda hcd: None,
            self._hcd('/shakenfist.protos.DatabaseService/GetObjectState',
                      [('caller-daemon', 'cleaner'),
                       ('caller-task', 'observe_local_blobs')]))

        self.assertEqual(before_tasked + 1, tasked._value.get())
        self.assertEqual(before_untasked, untasked._value.get())

    def test_missing_metadata_counts_as_unknown(self):
        interceptor = main._CallerMetricsInterceptor()
        child = main.DATABASE_REQUESTS.labels(
            operation='GetBlob', caller_daemon='unknown', caller_task='')
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
