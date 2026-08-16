# Copyright 2026 Michael Still and contributors
#
# The cluster maintainer and the cleaner call the same accessor,
# mariadb.get_active_blob_uuids(), and a failed read means opposite
# things to them. The cleaner uses the list as a complement set, so an
# unreadable list would delete a node's whole blob store and the pass is
# abandoned (see test_daemon_cleaner.py). The cluster maintainer only
# ever iterates it, so an unreadable list costs it one pass of reaping
# and rebalancing -- everything else in the cleanup is independent of it
# and must still run (#3638).

import threading
from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.exceptions import DatabaseUnavailable
from shakenfist.tests import base


BLOB_UUID_1 = '11111111-1111-4111-8111-111111111111'


class ClusterCleanupBlobReadFailureTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = mock.MagicMock()
        m.lock.lost_event = threading.Event()
        m.is_elected = True
        m.pet_watchdog = mock.MagicMock()
        m._cascaded_error_nodes = set()
        return m

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch('shakenfist.daemons.cluster.main.nodes_by_free_disk_descending')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    @mock.patch('shakenfist.daemons.cluster.main.artifact')
    @mock.patch('shakenfist.daemons.cluster.main.remove_abandoned_uploads')
    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_unreadable_active_list_degrades_one_section_only(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        mock_mariadb.delete_stale_transfers.return_value = 0
        mock_mariadb.delete_stale_cluster_operation_targets.return_value = 0
        mock_ipam.IPAMs.return_value = []
        mock_network.floating_network.return_value = None
        mock_artifact.Artifacts.return_value = []
        mock_instance.instance_blob_usage.return_value = {}
        mock_low_disk.return_value = []
        mock_nodes.return_value = []

        mock_mariadb.get_active_blob_uuids.side_effect = DatabaseUnavailable(
            'could not read the list of active blobs')
        # The sections after the blob loop have work waiting for them, so
        # "they ran" is observable rather than vacuous.
        mock_mariadb.get_expired_blob_uuids.return_value = [BLOB_UUID_1]
        mock_mariadb.get_stale_transcoded_blob_uuids.return_value = [
            BLOB_UUID_1]
        expired = mock.MagicMock()
        mock_blob.from_db.return_value = expired

        m = self._make_monitor()
        m._cluster_wide_cleanup(last_loop_run=0)

        # The blob reaping and replication section degraded to a no-op:
        # nothing was reaped, dropped or replicated.
        expired.cascading_delete.assert_not_called()
        expired.drop_node_location.assert_not_called()
        expired.request_replication.assert_not_called()

        # But the rest of the pass still ran. Expiry and transcode reaping
        # both did their work, and the pass reached the node loop.
        mock_mariadb.get_expired_blob_uuids.assert_called_once()
        expired.add_event.assert_called_once()
        expired.remove_transcodes.assert_called_once()
        mock_nodes.assert_called_once()
