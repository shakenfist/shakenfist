# Copyright 2026 Michael Still and contributors

from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.exceptions import MissingNodeLockSocket
from shakenfist.exceptions import TruncatedNodeLockResponse
from shakenfist.exceptions import UnknownNodeLockReplyException
from shakenfist.tests import base


class HealthCheckNodelockTestCase(base.ShakenFistTestCase):
    # health_check_nodelock() must report "unhealthy" rather than raise for
    # the connection failures seen while the nodelock daemon restarts: a
    # stale socket file (nodelock only unlinks its socket at startup)
    # refuses connections, the startup unlink races connect()
    # (FileNotFoundError), an accepted connection can be reset or aborted
    # or closed without a reply mid-shutdown, and a not-yet-created socket
    # is missing entirely. Letting any of them escape bypasses
    # wait_for_nodelock()'s calm 1Hz retry loop and callers like the
    # sidechannel monitor busy-spin logging tracebacks instead.

    def _check_with_lock_error(self, exc):
        with mock.patch.object(
                daemon.util_concurrency, 'NodeLock', side_effect=exc):
            return daemon.health_check_nodelock()

    def test_healthy(self):
        with mock.patch.object(daemon.util_concurrency, 'NodeLock'):
            self.assertTrue(daemon.health_check_nodelock())

    def test_connection_reset_is_unhealthy(self):
        self.assertFalse(self._check_with_lock_error(ConnectionResetError()))

    def test_connection_refused_is_unhealthy(self):
        self.assertFalse(self._check_with_lock_error(ConnectionRefusedError()))

    def test_connection_aborted_is_unhealthy(self):
        self.assertFalse(self._check_with_lock_error(ConnectionAbortedError()))

    def test_broken_pipe_is_unhealthy(self):
        self.assertFalse(self._check_with_lock_error(BrokenPipeError()))

    def test_socket_unlinked_before_connect_is_unhealthy(self):
        self.assertFalse(self._check_with_lock_error(FileNotFoundError()))

    def test_missing_socket_is_unhealthy(self):
        self.assertFalse(self._check_with_lock_error(MissingNodeLockSocket()))

    def test_truncated_response_is_unhealthy(self):
        self.assertFalse(self._check_with_lock_error(
            TruncatedNodeLockResponse()))

    def test_unlock_failure_is_unhealthy(self):
        # The unlock in NodeLock.__exit__ is a separate socket request, so
        # nodelock can go away between a successful lock and the unlock.
        lock = mock.MagicMock()
        lock.__exit__.side_effect = ConnectionRefusedError()
        with mock.patch.object(
                daemon.util_concurrency, 'NodeLock', return_value=lock):
            self.assertFalse(daemon.health_check_nodelock())

    def test_protocol_error_still_raises(self):
        # A malformed reply is a bug, not a transient restart, and should
        # not be silently converted into an unhealthy poll.
        self.assertRaises(
            UnknownNodeLockReplyException,
            self._check_with_lock_error, UnknownNodeLockReplyException())
