# Copyright 2026 Michael Still and contributors
"""Unit tests for the logship drainer thread.

The interesting bits are the push-body envelope, the conditional
tenant/auth headers, and the failure-handling branches (push
failure, backoff, row retention). Mirrors
``test_eventlog_drainer.py``.
"""
import shutil
import tempfile
from unittest import mock

import requests

from shakenfist import logship_drainer
from shakenfist import logship_spool
from shakenfist.tests import base


class _SpoolRootMixin:
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix='sf-logship-drainer-test-')
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._original_root = logship_spool.SPOOL_ROOT
        logship_spool.SPOOL_ROOT = self.tmp
        logship_spool.reset_for_tests()
        self.addCleanup(logship_spool.reset_for_tests)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        logship_spool.SPOOL_ROOT = self._original_root


class PushBodyTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """The push body envelope and its labels/values."""

    def test_build_push_body_envelope(self):
        thread = logship_drainer._DrainerThread('sf-cleaner')
        with mock.patch.object(
                logship_drainer.config, 'NODE_NAME', 'node-1'):
            body = thread._build_push_body(
                [(1, 100, 'first'), (2, 200, 'second')])

        self.assertEqual(1, len(body['streams']))
        stream = body['streams'][0]
        self.assertEqual(
            {'job': 'shakenfist', 'daemon': 'sf-cleaner', 'host': 'node-1'},
            stream['stream'])
        # Values are [str_ns, line] pairs, time-ascending.
        self.assertEqual(
            [['100', 'first'], ['200', 'second']],
            stream['values'])
        # The timestamps must be strings, not ints.
        for ts, _line in stream['values']:
            self.assertIsInstance(ts, str)


class PushToLokiTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """``_push_to_loki`` HTTP behaviour, headers, and bool contract."""

    def _config(self, base_url='http://loki:3100', tenant='', auth=''):
        return mock.patch.multiple(
            logship_drainer.config,
            LOKI_BASE_URL=base_url,
            LOKI_TENANT=tenant,
            LOKI_AUTH_HEADER=auth)

    def test_2xx_returns_true(self):
        resp = mock.Mock(status_code=204)
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp) as post:
            self.assertTrue(
                logship_drainer._push_to_loki('sf-api', {'streams': []}))
        post.assert_called_once()
        # URL is the base + the push path.
        self.assertEqual(
            'http://loki:3100/loki/api/v1/push', post.call_args[0][0])

    def test_5xx_returns_false(self):
        resp = mock.Mock(status_code=503)
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp):
            self.assertFalse(
                logship_drainer._push_to_loki('sf-api', {'streams': []}))

    def test_timeout_returns_false_and_never_raises(self):
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           side_effect=requests.Timeout('slow')):
            self.assertFalse(
                logship_drainer._push_to_loki('sf-api', {'streams': []}))

    def test_connection_error_returns_false(self):
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           side_effect=requests.ConnectionError('down')):
            self.assertFalse(
                logship_drainer._push_to_loki('sf-api', {'streams': []}))

    def test_headers_absent_when_not_configured(self):
        resp = mock.Mock(status_code=200)
        with self._config(tenant='', auth=''), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp) as post:
            logship_drainer._push_to_loki('sf-api', {'streams': []})
        headers = post.call_args.kwargs['headers']
        self.assertEqual('application/json', headers['Content-Type'])
        self.assertNotIn('X-Scope-OrgID', headers)
        self.assertNotIn('Authorization', headers)

    def test_headers_present_when_configured(self):
        resp = mock.Mock(status_code=200)
        with self._config(tenant='tenant-7', auth='Bearer secret'), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp) as post:
            logship_drainer._push_to_loki('sf-api', {'streams': []})
        headers = post.call_args.kwargs['headers']
        self.assertEqual('tenant-7', headers['X-Scope-OrgID'])
        self.assertEqual('Bearer secret', headers['Authorization'])


class DrainOneBatchTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """Happy path, push failure, empty spool."""

    def setUp(self):
        super().setUp()
        logship_spool.initialise('test-daemon')
        self.thread = logship_drainer._DrainerThread('test-daemon')

    def _enqueue(self, n):
        spool = logship_spool.get_spool()
        for i in range(n):
            spool.enqueue(i, f'line-{i}')

    def test_empty_spool_returns_zero(self):
        with mock.patch(
                'shakenfist.logship_drainer._push_to_loki') as push:
            result = self.thread._drain_one_batch()
        self.assertEqual(0, result)
        push.assert_not_called()

    def test_happy_path_deletes_rows_and_resets_backoff(self):
        self._enqueue(3)
        self.thread._backoff = 16.0  # simulate prior failures

        with mock.patch(
                'shakenfist.logship_drainer._push_to_loki',
                return_value=True) as push:
            drained = self.thread._drain_one_batch()

        self.assertEqual(3, drained)
        push.assert_called_once()
        # The body passed to the sink carries our three lines.
        body = push.call_args[0][1]
        self.assertEqual(3, len(body['streams'][0]['values']))
        self.assertEqual(0, logship_spool.get_spool().count())
        self.assertEqual(
            logship_drainer.BACKOFF_INITIAL, self.thread._backoff)

    def test_failed_push_leaves_rows_and_backs_off(self):
        self._enqueue(2)
        initial_backoff = self.thread._backoff

        with mock.patch(
                'shakenfist.logship_drainer._push_to_loki',
                return_value=False), \
                mock.patch.object(self.thread._stop_event, 'wait'):
            drained = self.thread._drain_one_batch()

        self.assertEqual(0, drained)
        self.assertEqual(2, logship_spool.get_spool().count())
        self.assertGreater(self.thread._backoff, initial_backoff)

    def test_backoff_grows_on_repeated_failures(self):
        self._enqueue(1)
        with mock.patch(
                'shakenfist.logship_drainer._push_to_loki',
                return_value=False), \
                mock.patch.object(self.thread._stop_event, 'wait'):
            self.thread._drain_one_batch()
            after_one = self.thread._backoff
            self.thread._drain_one_batch()
            after_two = self.thread._backoff

        self.assertGreater(after_one, logship_drainer.BACKOFF_INITIAL)
        self.assertGreater(after_two, after_one)
        self.assertLessEqual(after_two, logship_drainer.BACKOFF_MAX)


class BatchSizeCapTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """A drain reads no more than DRAIN_BATCH_SIZE rows per call."""

    def setUp(self):
        super().setUp()
        logship_spool.initialise('test-daemon')
        self.thread = logship_drainer._DrainerThread('test-daemon')

    def test_single_batch_is_capped(self):
        spool = logship_spool.get_spool()
        n = logship_drainer.DRAIN_BATCH_SIZE + 50
        for i in range(n):
            spool.enqueue(i, f'line-{i}')

        with mock.patch(
                'shakenfist.logship_drainer._push_to_loki',
                return_value=True) as push:
            drained = self.thread._drain_one_batch()

        self.assertEqual(logship_drainer.DRAIN_BATCH_SIZE, drained)
        body = push.call_args[0][1]
        self.assertEqual(
            logship_drainer.DRAIN_BATCH_SIZE,
            len(body['streams'][0]['values']))
        self.assertEqual(50, spool.count())
