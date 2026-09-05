# Copyright 2026 Michael Still and contributors
"""Unit tests for the logship drainer thread.

The interesting bits are the push-body envelope, the conditional
tenant/auth headers, and the failure-handling branches (transient
failure with backoff and row retention, permanent 4xx rejection
with row deletion, drain-time expiry of rows Loki would refuse).
Mirrors ``test_eventlog_drainer.py``.
"""
import time
from unittest import mock

from pydantic import SecretStr
import requests

from shakenfist import logship_drainer
from shakenfist import logship_spool
from shakenfist.tests import base


class _SpoolRootMixin(base.SpoolRootMixin):
    spool_module = logship_spool
    spool_prefix = 'sf-logship-drainer-test-'


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
    """``_push_to_loki`` HTTP behaviour, headers, and result contract."""

    def _config(self, base_url='http://loki:3100', tenant='', auth=''):
        # LOKI_AUTH_HEADER is a SecretStr on the real config, so it is
        # patched as one here. Substituting a plain str would leave the
        # production get_secret_value() call untested, and this is the
        # only coverage asserting the credential reaches the wire
        # unmasked -- a missed unwrap would send Loki the literal
        # '**********' and every push would fail authentication.
        return mock.patch.multiple(
            logship_drainer.config,
            LOKI_BASE_URL=base_url,
            LOKI_TENANT=tenant,
            LOKI_AUTH_HEADER=SecretStr(auth))

    def test_2xx_returns_success(self):
        resp = mock.Mock(status_code=204)
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp) as post:
            result, _detail = logship_drainer._push_to_loki(
                'sf-api', {'streams': []})
        self.assertEqual(logship_drainer.PUSH_SUCCESS, result)
        post.assert_called_once()
        # URL is the base + the push path.
        self.assertEqual(
            'http://loki:3100/loki/api/v1/push', post.call_args[0][0])

    def test_5xx_returns_failure_with_status_detail(self):
        resp = mock.Mock(status_code=503, text='overloaded\nsecond line')
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp):
            result, detail = logship_drainer._push_to_loki(
                'sf-api', {'streams': []})
        self.assertEqual(logship_drainer.PUSH_FAILURE, result)
        # The status code and the first line of the response body are
        # in the detail so the backoff WARNING carries the cause.
        self.assertIn('503', detail)
        self.assertIn('overloaded', detail)
        self.assertNotIn('second line', detail)

    def test_429_returns_failure(self):
        # 429 is the one 4xx that is transient -- it must be retried,
        # not dropped.
        resp = mock.Mock(status_code=429, text='rate limited')
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp):
            result, _detail = logship_drainer._push_to_loki(
                'sf-api', {'streams': []})
        self.assertEqual(logship_drainer.PUSH_FAILURE, result)

    def test_400_returns_rejected_with_body_detail(self):
        # A 400 (for example Loki's reject_old_samples) is permanent:
        # the caller must drop the batch rather than retry it forever
        # (issue 4054).
        resp = mock.Mock(
            status_code=400,
            text="entry for stream '{...}' has timestamp too old")
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp):
            result, detail = logship_drainer._push_to_loki(
                'sf-api', {'streams': []})
        self.assertEqual(logship_drainer.PUSH_REJECTED, result)
        self.assertIn('400', detail)
        self.assertIn('timestamp too old', detail)

    def test_404_returns_rejected(self):
        resp = mock.Mock(status_code=404, text='not found')
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           return_value=resp):
            result, _detail = logship_drainer._push_to_loki(
                'sf-api', {'streams': []})
        self.assertEqual(logship_drainer.PUSH_REJECTED, result)

    def test_timeout_returns_failure_and_never_raises(self):
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           side_effect=requests.Timeout('slow')):
            result, detail = logship_drainer._push_to_loki(
                'sf-api', {'streams': []})
        self.assertEqual(logship_drainer.PUSH_FAILURE, result)
        self.assertIn('Timeout', detail)

    def test_connection_error_returns_failure(self):
        with self._config(), \
                mock.patch('shakenfist.logship_drainer.requests.post',
                           side_effect=requests.ConnectionError('down')):
            result, _detail = logship_drainer._push_to_loki(
                'sf-api', {'streams': []})
        self.assertEqual(logship_drainer.PUSH_FAILURE, result)

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
    """Happy path, transient failure, permanent rejection, expiry."""

    def setUp(self):
        super().setUp()
        logship_spool.initialise('test-daemon')
        self.thread = logship_drainer._DrainerThread('test-daemon')

    def _enqueue(self, n, age_seconds=0):
        # Timestamps must be recent (unlike the sequence numbers the
        # push-body tests use) or the drain-time expiry filter would
        # discard the rows before the push.
        spool = logship_spool.get_spool()
        base_ns = time.time_ns() - int(age_seconds * 1_000_000_000)
        for i in range(n):
            spool.enqueue(base_ns + i, f'line-{i}')

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
                return_value=(logship_drainer.PUSH_SUCCESS, '')) as push:
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
                return_value=(logship_drainer.PUSH_FAILURE, 'HTTP 503')), \
                mock.patch.object(self.thread._stop_event, 'wait'):
            drained = self.thread._drain_one_batch()

        self.assertEqual(0, drained)
        self.assertEqual(2, logship_spool.get_spool().count())
        self.assertGreater(self.thread._backoff, initial_backoff)

    def test_backoff_grows_on_repeated_failures(self):
        self._enqueue(1)
        with mock.patch(
                'shakenfist.logship_drainer._push_to_loki',
                return_value=(logship_drainer.PUSH_FAILURE, 'down')), \
                mock.patch.object(self.thread._stop_event, 'wait'):
            self.thread._drain_one_batch()
            after_one = self.thread._backoff
            self.thread._drain_one_batch()
            after_two = self.thread._backoff

        self.assertGreater(after_one, logship_drainer.BACKOFF_INITIAL)
        self.assertGreater(after_two, after_one)
        self.assertLessEqual(after_two, logship_drainer.BACKOFF_MAX)

    def test_rejected_push_drops_rows_and_advances(self):
        # A permanently-rejected batch must be dropped, not retained,
        # or it wedges the FIFO spool forever (issue 4054). The rows
        # behind it become reachable on the next drain.
        self._enqueue(2)
        self.thread._backoff = 16.0  # simulate prior failures

        with mock.patch(
                'shakenfist.logship_drainer._push_to_loki',
                return_value=(logship_drainer.PUSH_REJECTED,
                              'HTTP 400: timestamp too old')):
            drained = self.thread._drain_one_batch()

        self.assertEqual(2, drained)
        self.assertEqual(0, logship_spool.get_spool().count())
        # Rejection is progress, so the backoff resets too.
        self.assertEqual(
            logship_drainer.BACKOFF_INITIAL, self.thread._backoff)

    def test_expired_rows_are_discarded_without_pushing(self):
        # Rows older than LOKI_MAX_LINE_AGE are deleted at drain time
        # rather than pushed, so a long outage cannot convert itself
        # into a permanently-poisoned spool head.
        self._enqueue(3, age_seconds=10 * 86400)

        with mock.patch.object(
                logship_drainer.config, 'LOKI_MAX_LINE_AGE', 518400), \
                mock.patch(
                    'shakenfist.logship_drainer._push_to_loki') as push:
            drained = self.thread._drain_one_batch()

        self.assertEqual(3, drained)
        push.assert_not_called()
        self.assertEqual(0, logship_spool.get_spool().count())

    def test_mixed_age_batch_ships_only_fresh_rows(self):
        self._enqueue(2, age_seconds=10 * 86400)
        self._enqueue(3)

        with mock.patch.object(
                logship_drainer.config, 'LOKI_MAX_LINE_AGE', 518400), \
                mock.patch(
                    'shakenfist.logship_drainer._push_to_loki',
                    return_value=(
                        logship_drainer.PUSH_SUCCESS, '')) as push:
            drained = self.thread._drain_one_batch()

        # All five rows left the spool, but only the three fresh ones
        # were pushed.
        self.assertEqual(5, drained)
        push.assert_called_once()
        body = push.call_args[0][1]
        self.assertEqual(3, len(body['streams'][0]['values']))
        self.assertEqual(0, logship_spool.get_spool().count())


class BatchSizeCapTestCase(_SpoolRootMixin, base.ShakenFistTestCase):
    """A drain reads no more than DRAIN_BATCH_SIZE rows per call."""

    def setUp(self):
        super().setUp()
        logship_spool.initialise('test-daemon')
        self.thread = logship_drainer._DrainerThread('test-daemon')

    def test_single_batch_is_capped(self):
        spool = logship_spool.get_spool()
        n = logship_drainer.DRAIN_BATCH_SIZE + 50
        base_ns = time.time_ns()
        for i in range(n):
            spool.enqueue(base_ns + i, f'line-{i}')

        with mock.patch(
                'shakenfist.logship_drainer._push_to_loki',
                return_value=(logship_drainer.PUSH_SUCCESS, '')) as push:
            drained = self.thread._drain_one_batch()

        self.assertEqual(logship_drainer.DRAIN_BATCH_SIZE, drained)
        body = push.call_args[0][1]
        self.assertEqual(
            logship_drainer.DRAIN_BATCH_SIZE,
            len(body['streams'][0]['values']))
        self.assertEqual(50, spool.count())
