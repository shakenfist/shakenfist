# Copyright 2026 Michael Still and contributors
#
# Tests that the cluster maintainer pets the systemd watchdog during
# its long work phases. The elected loop sleeps via lock.lost_event.wait()
# rather than idle(), and _cluster_wide_cleanup iterates over potentially
# large blob/artifact/node collections, so both must pet explicitly to
# survive WatchdogSec once it is armed.

import threading
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


class FakeIPAM:
    def __init__(self):
        # update_time well in the past so the per-IPAM body is reached
        # (the pet fires before the recency check regardless). Mark the
        # IPAM already deleted so the body takes no destructive action
        # and the loop proceeds cleanly to the next item.
        self.state = mock.MagicMock()
        self.state.update_time = 0
        self.network_uuid = 'no-such-network'
        self.state.value = dbo.STATE_DELETED


class ClusterWatchdogTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = mock.MagicMock()
        # A held lease so the early lost_event gate does not short-circuit.
        m.lock.lost_event = threading.Event()
        m.is_elected = True
        m.pet_watchdog = mock.MagicMock()
        return m

    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_cleanup_pets_per_ipam(self, mock_mariadb, mock_ipam, mock_network):
        # Drive far enough to reach the per-IPAM loop and confirm a pet
        # fires for each item. Make the floating_network() absent so the
        # cleanup short-circuits the floating-IP loop and the rest of the
        # heavy body, keeping the test focused.
        mock_mariadb.delete_stale_transfers.return_value = 0
        mock_mariadb.delete_stale_cluster_operation_targets.return_value = 0
        mock_ipam.IPAMs.return_value = [FakeIPAM(), FakeIPAM()]
        # No associated network -> the IPAM body runs (but state is not
        # STATE_DELETED so no destructive action). floating_network None
        # so the remainder of the function raises/returns early when it
        # hits the next collection; wrap in try to keep the test focused
        # on the pet behaviour.
        mock_network.Network.from_db.return_value = None
        mock_network.floating_network.return_value = None

        m = self._make_monitor()
        try:
            m._cluster_wide_cleanup(last_loop_run=0)
        except Exception:
            # The rest of the body is not fully stubbed; we only care
            # that the IPAM loop pet fired.
            pass

        # One pet per IPAM iterated.
        self.assertGreaterEqual(m.pet_watchdog.call_count, 2)
