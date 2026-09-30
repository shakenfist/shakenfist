from unittest import mock

import testtools

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.tests import base
from shakenfist.util import concurrency as util_concurrency


class NodeLockTimeoutTestCase(base.ShakenFistTestCase):
    """NodeLock(timeout=...) is a bounded wait (phase 1b's D1). The
    default, unbounded wait, must keep retrying forever, and a timeout
    must only fire once it has genuinely elapsed."""

    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request')
    def test_no_timeout_retries_until_granted(
            self, mock_request, mock_sleep):
        # Fails four times, then granted on the fifth request. The sixth
        # value answers __exit__'s unlock request.
        mock_request.side_effect = [False, False, False, False, True, True]

        with util_concurrency.NodeLock('some-lock'):
            pass

        # Four failed lock requests, then a fifth which is granted, then
        # the unlock request on the way out.
        self.assertEqual(6, mock_request.call_count)

    @mock.patch('time.time')
    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request',
                return_value=False)
    def test_timeout_raises_when_never_granted(
            self, mock_request, mock_sleep, mock_time):
        # start_time reads 0.0, and every subsequent check advances the
        # clock well past a 1 second timeout without a real sleep.
        mock_time.side_effect = [0.0] + [i * 0.5 for i in range(1, 20)]

        with testtools.ExpectedException(exceptions.NodeLockTimeout):
            with util_concurrency.NodeLock('some-lock', timeout=1):
                pass

        # No unlock request is sent: __enter__ raised, so the context
        # body and __exit__ never run (Python does not call __exit__
        # when __enter__ itself raises).
        self.assertTrue(mock_request.called)

    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request',
                return_value=True)
    def test_timeout_not_raised_when_granted_first_time(
            self, mock_request, mock_sleep):
        with util_concurrency.NodeLock('some-lock', timeout=5):
            pass

        # One lock request, one unlock request. Never timed out.
        self.assertEqual(2, mock_request.call_count)
        mock_sleep.assert_not_called()


class GetLockNodeTimeoutTestCase(base.ShakenFistTestCase):
    """get_lock(global_scope=False, ...) must hand node_timeout to
    NodeLock, and must never hand it the cluster-only timeout keyword
    (phase 1b's D1)."""

    def setUp(self):
        super().setUp()
        self.obj = DatabaseBackedObject(
            '12345678-1234-4321-8234-123456789012')

    @mock.patch('shakenfist.baseobject.util_concurrency.NodeLock')
    def test_node_timeout_passed_through(self, mock_nodelock):
        self.obj.get_lock(global_scope=False, node_timeout=2)
        mock_nodelock.assert_called_once_with(
            f'{self.obj.object_type}-{self.obj.uuid}', timeout=2)

    @mock.patch('shakenfist.baseobject.util_concurrency.NodeLock')
    def test_cluster_timeout_not_passed_to_node_lock(self, mock_nodelock):
        # timeout=120 is sf-queues restore's cluster-lock timeout
        # (baseobject.py D1). It must not reach NodeLock, which would
        # start timing out a call that has never timed out before.
        self.obj.get_lock(global_scope=False, timeout=120)
        mock_nodelock.assert_called_once_with(
            f'{self.obj.object_type}-{self.obj.uuid}', timeout=None)
