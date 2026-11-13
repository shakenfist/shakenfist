from unittest import mock

import testtools

from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.tests import base
from shakenfist.util import concurrency as util_concurrency


class NodeLockTimeoutTestCase(base.ShakenFistTestCase):
    """NodeLock(timeout=...) is a bounded wait. The default, unbounded
    wait, must keep retrying forever, and a timeout must only fire once
    it has genuinely elapsed."""

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
    NodeLock, and must never hand it the cluster-only timeout keyword."""

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
        # (see get_lock() in baseobject.py). It must not reach NodeLock,
        # which would start timing out a call that has never timed out
        # before.
        self.obj.get_lock(global_scope=False, timeout=120)
        mock_nodelock.assert_called_once_with(
            f'{self.obj.object_type}-{self.obj.uuid}', timeout=None)


class NodeLockUnlockFailureTestCase(base.ShakenFistTestCase):
    """A failed unlock must not fail an operation whose work is done.

    The nodelock daemon drops its whole lock table when it restarts, and
    deletes a key before it answers the unlock, so a transport error on
    the way out does not mean the lock is still held. Raising one failed
    completed operations and masked the body's own exception (issue
    4902)."""

    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request')
    def test_connection_reset_on_unlock_is_swallowed(
            self, mock_request, mock_sleep):
        # The lock is granted, then every unlock attempt is reset by a
        # nodelock daemon which is going away.
        mock_request.side_effect = [
            True,
            ConnectionResetError(104, 'Connection reset by peer'),
            ConnectionResetError(104, 'Connection reset by peer'),
            ConnectionResetError(104, 'Connection reset by peer'),
        ]

        executed = []
        with util_concurrency.NodeLock('some-lock'):
            executed.append(True)

        self.assertEqual([True], executed)
        self.assertEqual(
            1 + util_concurrency.NODELOCK_UNLOCK_ATTEMPTS,
            mock_request.call_count)

    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request')
    def test_unlock_is_retried_before_giving_up(
            self, mock_request, mock_sleep):
        # A refused connection is not proof the daemon is gone -- a full
        # listen backlog refuses too -- so the unlock is retried, and a
        # second attempt which succeeds really does release the lock.
        mock_request.side_effect = [
            True,
            ConnectionRefusedError(111, 'Connection refused'),
            True,
        ]

        with util_concurrency.NodeLock('some-lock'):
            pass

        self.assertEqual(3, mock_request.call_count)
        mock_sleep.assert_called_once_with(
            util_concurrency.NODELOCK_UNLOCK_RETRY_INTERVAL)

    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request')
    def test_missing_socket_on_unlock_is_swallowed(
            self, mock_request, mock_sleep):
        mock_request.side_effect = [
            True,
            exceptions.MissingNodeLockSocket(),
            FileNotFoundError(2, 'No such file or directory'),
            exceptions.TruncatedNodeLockResponse(),
        ]

        with util_concurrency.NodeLock('some-lock'):
            pass

        self.assertEqual(
            1 + util_concurrency.NODELOCK_UNLOCK_ATTEMPTS,
            mock_request.call_count)

    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request')
    def test_unknown_reply_on_unlock_still_raises(
            self, mock_request, mock_sleep):
        # A reply which parses but carries no known field is a bug in the
        # nodelock protocol rather than a daemon which is restarting, and
        # health_check_nodelock() pins the same decision.
        mock_request.side_effect = [
            True, exceptions.UnknownNodeLockReplyException()]

        with testtools.ExpectedException(
                exceptions.UnknownNodeLockReplyException):
            with util_concurrency.NodeLock('some-lock'):
                pass

    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request')
    def test_body_exception_is_not_masked_by_unlock_failure(
            self, mock_request, mock_sleep):
        # __exit__ must not replace the body's exception with its own,
        # and must not suppress it either.
        mock_request.side_effect = [
            True,
            ConnectionResetError(104, 'Connection reset by peer'),
            ConnectionResetError(104, 'Connection reset by peer'),
            ConnectionResetError(104, 'Connection reset by peer'),
        ]

        with testtools.ExpectedException(exceptions.DeadNetwork):
            with util_concurrency.NodeLock('some-lock'):
                raise exceptions.DeadNetwork('the body failed')

    @mock.patch('time.sleep')
    @mock.patch('shakenfist.util.concurrency._node_lock_request')
    def test_programming_error_on_unlock_still_raises(
            self, mock_request, mock_sleep):
        # Only transport errors are swallowed. Anything else escaping the
        # unlock is a bug, and a bug is still loud.
        mock_request.side_effect = [True, AttributeError('not a transport')]

        with testtools.ExpectedException(AttributeError):
            with util_concurrency.NodeLock('some-lock'):
                pass
