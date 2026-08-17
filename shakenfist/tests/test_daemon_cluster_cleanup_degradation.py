# Copyright 2026 Michael Still and contributors
#
# The cluster maintainer and the cleaner call the same accessor,
# mariadb.get_active_blob_uuids(), and a failed read means opposite
# things to them. The cleaner uses the list as a complement set, so an
# unreadable list would delete a node's whole blob store and the pass is
# abandoned (see test_daemon_cleaner.py). The cluster maintainer only
# ever iterates it, so an unreadable list costs it one pass of reaping
# and rebalancing -- the rest of the cleanup must still run (#3638).
#
# With one exception, which these tests also pin: stale transcode
# reaping reads last_used, and the skipped section is what refreshes it,
# so the reaper sits out a degraded pass rather than reaping transcodes
# of blobs that are in use.

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

        # But the rest of the pass still ran. Expiry did its work and the
        # pass reached the node loop.
        mock_mariadb.get_expired_blob_uuids.assert_called_once()
        expired.add_event.assert_called_once()
        mock_nodes.assert_called_once()

        # Transcode reaping is the one exception, and it is not
        # independent: the skipped section is what populates
        # in_use_blobs for instance-backed blobs, so record_usage() did
        # not refresh last_used this pass and the reaper selects on
        # exactly that column. Reaping here would drop transcodes of
        # blobs in active use.
        mock_mariadb.get_stale_transcoded_blob_uuids.assert_not_called()
        expired.remove_transcodes.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch('shakenfist.daemons.cluster.main.nodes_by_free_disk_descending')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    @mock.patch('shakenfist.daemons.cluster.main.artifact')
    @mock.patch('shakenfist.daemons.cluster.main.remove_abandoned_uploads')
    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_a_readable_list_still_reaps_transcodes(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # The negative control for the assertion above: the skip is
        # conditional on the failed read, not on the reaper having been
        # quietly disabled.
        mock_mariadb.delete_stale_transfers.return_value = 0
        mock_mariadb.delete_stale_cluster_operation_targets.return_value = 0
        mock_ipam.IPAMs.return_value = []
        mock_network.floating_network.return_value = None
        mock_artifact.Artifacts.return_value = []
        mock_instance.instance_blob_usage.return_value = {}
        mock_low_disk.return_value = []
        mock_nodes.return_value = []

        # A genuinely empty active list is a real answer, not a failure.
        mock_mariadb.get_active_blob_uuids.return_value = []
        mock_mariadb.get_expired_blob_uuids.return_value = []
        mock_mariadb.get_stale_transcoded_blob_uuids.return_value = [
            BLOB_UUID_1]
        stale = mock.MagicMock()
        mock_blob.from_db.return_value = stale

        m = self._make_monitor()
        m._cluster_wide_cleanup(last_loop_run=0)

        mock_mariadb.get_stale_transcoded_blob_uuids.assert_called_once()
        stale.remove_transcodes.assert_called_once()
