# Copyright 2026 Michael Still and contributors

from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.exceptions import MissingNodeLockSocket
from shakenfist.tests import base


class HealthCheckNodelockTestCase(base.ShakenFistTestCase):
    # health_check_nodelock() must report "unhealthy" rather than raise for
    # the connection failures seen while the nodelock daemon restarts. In
    # particular a stale socket file (nodelock only unlinks its socket at
    # startup) surfaces as ConnectionRefusedError; letting that escape
    # bypasses wait_for_nodelock()'s calm 1Hz retry loop and callers like
    # the sidechannel monitor busy-spin logging tracebacks instead.

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

    def test_missing_socket_is_unhealthy(self):
        self.assertFalse(self._check_with_lock_error(MissingNodeLockSocket()))
