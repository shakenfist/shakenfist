# Copyright 2026 Michael Still and contributors
#
# The cluster maintainer reaps blobs with no references which have not
# been used for 300 seconds. A blob under fetch has no references (the
# artifact index reference is only created once the fetch succeeds),
# so the only thing standing between an in-flight fetch and the reaper
# is a fresh last_used: http_fetch() persists one as a heartbeat while
# data is flowing (issue 4000, and see test_blob_http_fetch.py). These
# tests pin the reaper side of that contract -- a fresh last_used
# survives the pass, a stale one is reaped, and a never-used blob gets
# its grace period from fetched_at.

import threading
import time
from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


BLOB_UUID_1 = '11111111-1111-4111-8111-111111111111'


class ClusterBlobReaperTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = mock.MagicMock()
        m.lock.lost_event = threading.Event()
        m.is_elected = True
        m.pet_watchdog = mock.MagicMock()
        m._cascaded_error_nodes = set()
        return m

    def _run_pass(self, mock_mariadb, mock_ipam, mock_network, mock_artifact,
                  mock_instance, mock_blob, mock_low_disk, mock_nodes,
                  the_blob):
        # The orphan artifact sweep lists namespaces once per pass and
        # treats an empty list as an unreadable one, skipping the rest
        # of the cleanup. A bare MagicMock iterates empty, so this has
        # to be set for the blob section below to be reached at all.
        mock_mariadb.get_all_namespace_names.return_value = ['system']
        mock_mariadb.delete_stale_transfers.return_value = 0
        mock_mariadb.delete_stale_cluster_operation_targets.return_value = 0
        mock_ipam.IPAMs.return_value = []
        mock_network.floating_network.return_value = None
        mock_artifact.Artifacts.return_value = []
        mock_instance.instance_blob_usage.return_value = {}
        mock_low_disk.return_value = []
        mock_nodes.return_value = []

        mock_mariadb.get_active_blob_uuids.return_value = [BLOB_UUID_1]
        mock_mariadb.get_expired_blob_uuids.return_value = []
        mock_mariadb.get_stale_transcoded_blob_uuids.return_value = []
        mock_blob.from_db.return_value = the_blob

        m = self._make_monitor()
        m._cluster_wide_cleanup(last_loop_run=0)

    def _make_blob(self, last_used, fetched_at):
        b = mock.MagicMock()
        b.uuid = BLOB_UUID_1
        b.ref_count = 0
        b.last_used = last_used
        b.fetched_at = fetched_at
        return b

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch('shakenfist.daemons.cluster.main.nodes_by_free_disk_descending')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    @mock.patch('shakenfist.daemons.cluster.main.artifact')
    @mock.patch('shakenfist.daemons.cluster.main.remove_abandoned_uploads')
    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_zero_ref_blob_with_fresh_last_used_survives(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # This is what a healthy in-flight fetch looks like: no
        # references yet, a fetched_at outside the grace period, but a
        # heartbeat within the last 30 seconds.
        b = self._make_blob(
            last_used=time.time() - 10, fetched_at=time.time() - 600)
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b)
        b.cascading_delete.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch('shakenfist.daemons.cluster.main.nodes_by_free_disk_descending')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    @mock.patch('shakenfist.daemons.cluster.main.artifact')
    @mock.patch('shakenfist.daemons.cluster.main.remove_abandoned_uploads')
    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_zero_ref_blob_with_stale_last_used_is_reaped(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # This is what a dead or stalled fetch looks like: the
        # heartbeat stopped more than 300 seconds ago.
        b = self._make_blob(
            last_used=time.time() - 400, fetched_at=time.time() - 600)
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b)
        b.cascading_delete.assert_called_once()

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch('shakenfist.daemons.cluster.main.nodes_by_free_disk_descending')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    @mock.patch('shakenfist.daemons.cluster.main.artifact')
    @mock.patch('shakenfist.daemons.cluster.main.remove_abandoned_uploads')
    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_zero_ref_blob_never_used_gets_grace_from_fetched_at(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # A brand new blob whose fetch has not yet reported progress has
        # no last_used at all, and gets its grace period from fetched_at.
        b = self._make_blob(last_used=None, fetched_at=time.time() - 10)
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b)
        b.cascading_delete.assert_not_called()
