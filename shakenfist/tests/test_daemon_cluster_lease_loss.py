# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster maintainer's lease-loss handling: the early
# return inside _cluster_wide_cleanup when lost_event is set, and the
# re-election when lost_event.wait() trips the inner loop.

import threading
from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


class ClusterCleanupLeaseLossTestCase(base.ShakenFistTestCase):
    """``_cluster_wide_cleanup`` must not run if the lease was lost
    between iterations -- otherwise two maintainers can run cleanup
    concurrently while the inner loop's wait() catches up."""

    def _make_monitor(self, lost: bool):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = mock.MagicMock()
        m.lock.lost_event = threading.Event()
        if lost:
            m.lock.lost_event.set()
        m.is_elected = True
        # _cluster_wide_cleanup now pets the watchdog in its preamble; the
        # __init__ that would set this is bypassed by __new__.
        m._last_watchdog = 0.0
        return m

    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_cleanup_skipped_when_lease_lost(self, mock_mariadb):
        m = self._make_monitor(lost=True)

        m._cluster_wide_cleanup(last_loop_run=0)

        # Skipping means none of the cleanup mariadb calls fire. The
        # very first thing the body would otherwise hit is
        # delete_stale_transfers; it must not be reached.
        mock_mariadb.delete_stale_transfers.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_cleanup_proceeds_when_lease_held(self, mock_mariadb):
        # Sanity check the inverse: if lost_event is not set, the
        # cleanup body is entered (it will subsequently fail because
        # we have not stubbed out the rest of the loop, but that is
        # fine -- we only need to confirm the early return is
        # gated on ``lost_event``).
        mock_mariadb.delete_stale_transfers.return_value = 0
        m = self._make_monitor(lost=False)

        try:
            m._cluster_wide_cleanup(last_loop_run=0)
        except Exception:
            pass

        mock_mariadb.delete_stale_transfers.assert_called_once()
