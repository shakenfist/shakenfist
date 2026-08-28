# Copyright 2026 Michael Still and contributors
#
# The cluster maintainer's orphan artifact sweep decides whether each
# artifact's namespace still exists. It used to ask the database once per
# artifact via Namespace.from_db(), which overrides the cached base
# implementation; the underlying mariadb.get_namespace() caches on
# OBJECT_CACHE_TTL_MUTABLE (30s), which is half this loop's 60s period, so
# every one of those lookups was a guaranteed cache miss.
#
# These tests pin the replacement: one namespace listing per pass, the same
# keep/delete decision as before, and -- because the listing's direct
# MariaDB path returns [] on OperationalError and this loop is the one
# caller whose reaction to "no namespaces" is destructive -- an empty
# answer must skip the sweep rather than delete every artifact.

import threading
from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


class ClusterArtifactNamespaceSweepTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.lock = mock.MagicMock()
        m.lock.lost_event = threading.Event()
        m.is_elected = True
        m.pet_watchdog = mock.MagicMock()
        m._cascaded_error_nodes = set()
        return m

    def _artifact(self, namespace):
        a = mock.MagicMock()
        a.namespace = namespace
        a.get_all_indexes.return_value = []
        return a

    def _quiesce(self, mock_mariadb, mock_ipam, mock_network, mock_instance,
                 mock_low_disk, mock_nodes):
        """Stub everything either side of the artifact sweep."""
        mock_mariadb.delete_stale_transfers.return_value = 0
        mock_mariadb.delete_stale_cluster_operation_targets.return_value = 0
        mock_mariadb.get_active_blob_uuids.return_value = []
        mock_mariadb.get_expired_blob_uuids.return_value = []
        mock_mariadb.get_stale_transcoded_blob_uuids.return_value = []
        mock_ipam.IPAMs.return_value = []
        mock_network.floating_network.return_value = None
        mock_instance.instance_blob_usage.return_value = {}
        mock_low_disk.return_value = []
        mock_nodes.return_value = []

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch('shakenfist.daemons.cluster.main.nodes_by_free_disk_descending')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    @mock.patch('shakenfist.daemons.cluster.main.artifact')
    @mock.patch('shakenfist.daemons.cluster.main.remove_abandoned_uploads')
    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_namespaces_are_listed_once_however_many_artifacts(
            self, mock_mariadb, mock_ipam, mock_network, mock_remove_uploads,
            mock_artifact, mock_instance, mock_low_disk, mock_nodes):
        self._quiesce(mock_mariadb, mock_ipam, mock_network, mock_instance,
                      mock_low_disk, mock_nodes)
        mock_mariadb.get_all_namespace_names.return_value = ['system', 'ns-a']
        # Six artifacts over two namespaces. The old code made one
        # GetNamespace call per artifact because the object cache expires
        # before this loop comes round again.
        mock_artifact.Artifacts.return_value = [
            self._artifact('ns-a') for _ in range(3)
        ] + [self._artifact('system') for _ in range(3)]

        m = self._make_monitor()
        m._cluster_wide_cleanup(last_loop_run=0)

        mock_mariadb.get_all_namespace_names.assert_called_once_with()
        # The per-artifact accessor must be gone entirely, not merely
        # called less often.
        mock_mariadb.get_namespace.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch('shakenfist.daemons.cluster.main.nodes_by_free_disk_descending')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    @mock.patch('shakenfist.daemons.cluster.main.artifact')
    @mock.patch('shakenfist.daemons.cluster.main.remove_abandoned_uploads')
    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_only_artifacts_whose_namespace_is_gone_are_deleted(
            self, mock_mariadb, mock_ipam, mock_network, mock_remove_uploads,
            mock_artifact, mock_instance, mock_low_disk, mock_nodes):
        self._quiesce(mock_mariadb, mock_ipam, mock_network, mock_instance,
                      mock_low_disk, mock_nodes)
        mock_mariadb.get_all_namespace_names.return_value = ['system', 'ns-a']
        kept = self._artifact('ns-a')
        orphan = self._artifact('ns-deleted')
        mock_artifact.Artifacts.return_value = [kept, orphan]

        m = self._make_monitor()
        m._cluster_wide_cleanup(last_loop_run=0)

        orphan.delete.assert_called_once_with()
        kept.delete.assert_not_called()
        # A kept artifact carries on through the rest of the loop body.
        kept.delete_old_versions.assert_called_once_with()
        orphan.delete_old_versions.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.main.Nodes')
    @mock.patch('shakenfist.daemons.cluster.main.nodes_by_free_disk_descending')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    @mock.patch('shakenfist.daemons.cluster.main.artifact')
    @mock.patch('shakenfist.daemons.cluster.main.remove_abandoned_uploads')
    @mock.patch('shakenfist.daemons.cluster.main.network')
    @mock.patch('shakenfist.daemons.cluster.main.ipam')
    @mock.patch('shakenfist.daemons.cluster.main.mariadb')
    def test_an_unreadable_namespace_list_deletes_nothing(
            self, mock_mariadb, mock_ipam, mock_network, mock_remove_uploads,
            mock_artifact, mock_instance, mock_low_disk, mock_nodes):
        self._quiesce(mock_mariadb, mock_ipam, mock_network, mock_instance,
                      mock_low_disk, mock_nodes)
        # What _direct_get_all_namespace_names() returns on OperationalError.
        mock_mariadb.get_all_namespace_names.return_value = []
        doomed = self._artifact('ns-a')
        mock_artifact.Artifacts.return_value = [doomed]

        m = self._make_monitor()
        m._cluster_wide_cleanup(last_loop_run=0)

        # Every artifact in the cluster would otherwise look orphaned.
        doomed.delete.assert_not_called()
        mock_artifact.Artifacts.assert_not_called()
