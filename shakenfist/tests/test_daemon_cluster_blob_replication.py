# Copyright 2026 Michael Still and contributors
#
# The cluster maintainer prunes over replicated blobs from nodes which
# are low on disk, and requests replication of under replicated blobs.
# Issue 4226: the pruner used the desired surviving-copy count as its
# removal budget, so a blob with no running instances could lose every
# copy it had in a single pass when an in-flight transfer pushed it over
# the replication factor and that transfer then failed. These tests pin
# the corrected contract: only genuinely surplus completed copies are
# ever removed, in-use copies are never removed, and a blob with no
# copies left anywhere is recorded as errored instead of being listed as
# under replicated forever.
#
# Issue 4626: blob locations are node names but instance placements are
# node UUIDs, and the pruner compared the two directly, so no copy was
# ever seen as in use and copies backing running instances were removed.
# The in-use tests below therefore use those two real shapes: a test
# which places an instance on a node name passes whether or not the
# pruner resolves placements, which is how the bug went unnoticed.

import threading
from unittest import mock

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.tests import base


BLOB_UUID_1 = '11111111-1111-4111-8111-111111111111'

# Node UUID to node name, as Node.from_db() would resolve them.
NODES = {
    'aaaaaaaa-aaaa-4aaa-8aaa-000000000001': 'sf-1',
    'aaaaaaaa-aaaa-4aaa-8aaa-000000000002': 'sf-2',
    'aaaaaaaa-aaaa-4aaa-8aaa-000000000003': 'sf-3',
}
NODE_UUIDS = {name: node_uuid for node_uuid, name in NODES.items()}


def _fake_node_from_db(identifier, suppress_failure_audit=False):
    name = NODES.get(identifier)
    if not name:
        return None
    n = mock.MagicMock()
    n.uuid = identifier
    n.fqdn = name
    return n


def _pass_mocks(func):
    # Patches are applied innermost first, so their mocks arrive as
    # positional arguments in this order after self.
    for patch in [
            mock.patch('shakenfist.daemons.cluster.main.mariadb'),
            mock.patch('shakenfist.daemons.cluster.main.ipam'),
            mock.patch('shakenfist.daemons.cluster.main.network'),
            mock.patch('shakenfist.daemons.cluster.main.'
                       'remove_abandoned_uploads'),
            mock.patch('shakenfist.daemons.cluster.main.artifact'),
            mock.patch('shakenfist.daemons.cluster.main.instance'),
            mock.patch('shakenfist.daemons.cluster.main.Blob'),
            mock.patch('shakenfist.daemons.cluster.main.'
                       'nodes_by_free_disk_descending'),
            mock.patch('shakenfist.daemons.cluster.main.Nodes')]:
        func = patch(func)
    return func


class ClusterBlobReplicationTestCase(base.ShakenFistTestCase):
    def _make_monitor(self):
        m = cluster_main.Monitor.__new__(cluster_main.Monitor)
        m.abort_path = '/nonexistent/sf-test-cluster-abort-path'
        m.lock = mock.MagicMock()
        m.lock.lost_event = threading.Event()
        m.is_elected = True
        m.pet_watchdog = mock.MagicMock()
        m._cascaded_error_nodes = set()
        return m

    def _run_pass(self, mock_mariadb, mock_ipam, mock_network, mock_artifact,
                  mock_instance, mock_blob, mock_low_disk, mock_nodes,
                  the_blob, low_disk_nodes, instance_nodes=None):
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
        # instance_nodes maps the uuid of each instance using the blob to
        # the node its placement records.
        instance_nodes = instance_nodes or {}
        mock_instance.instance_blob_usage.return_value = (
            {BLOB_UUID_1: list(instance_nodes)} if instance_nodes else {})

        def fake_instance_from_db(instance_uuid):
            i = mock.MagicMock()
            i.placement = {'node': instance_nodes[instance_uuid]}
            return i

        mock_instance.Instance.from_db.side_effect = fake_instance_from_db
        mock_low_disk.return_value = low_disk_nodes
        mock_nodes.return_value = []

        mock_mariadb.get_active_blob_uuids.return_value = [BLOB_UUID_1]
        mock_mariadb.get_expired_blob_uuids.return_value = []
        mock_mariadb.get_stale_transcoded_blob_uuids.return_value = []
        mock_blob.from_db.return_value = the_blob

        m = self._make_monitor()
        with mock.patch('shakenfist.daemons.cluster.main.Node.from_db',
                        side_effect=_fake_node_from_db):
            m._cluster_wide_cleanup(last_loop_run=0)

    def _make_blob(self, locations, incomplete_nodes, state=dbo.STATE_CREATED):
        b = mock.MagicMock()
        b.uuid = BLOB_UUID_1
        b.ref_count = 1
        b.locations = locations
        b.incomplete_healthy_locations = [
            {'node': n, 'percentage': 50.0} for n in incomplete_nodes]
        b.state.value = state
        return b

    @_pass_mocks
    def test_inflight_transfer_does_not_fund_a_prune(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # The issue 4226 incident: exactly the replication factor of
        # completed copies, both on low-disk nodes, plus one transfer in
        # flight. The transfer makes the blob look over replicated but
        # there is no surplus completed copy, so nothing may be removed.
        b = self._make_blob(['sf-3', 'sf-4'], ['sf-1'])
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b,
            low_disk_nodes=['sf-3', 'sf-4'])
        b.drop_node_location.assert_not_called()

    @_pass_mocks
    def test_genuine_surplus_prunes_only_the_surplus(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # Three completed copies against a factor of two is a surplus of
        # exactly one, even with every location low on disk.
        b = self._make_blob(['sf-1', 'sf-2', 'sf-3'], [])
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b,
            low_disk_nodes=['sf-1', 'sf-2', 'sf-3'])
        b.drop_node_location.assert_called_once_with('sf-1')

    @_pass_mocks
    def test_in_use_location_is_never_pruned(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # A copy on the node running an instance which uses the blob is
        # not a candidate for removal, and the copies which must survive
        # elsewhere are reduced by it: three copies with one in use
        # leaves one removable.
        b = self._make_blob(['sf-1', 'sf-2', 'sf-3'], [])
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b,
            low_disk_nodes=['sf-2', 'sf-1', 'sf-3'],
            instance_nodes={'fake-instance-uuid': NODE_UUIDS['sf-2']})
        b.drop_node_location.assert_called_once_with('sf-1')

    @_pass_mocks
    def test_copies_in_use_everywhere_are_never_pruned(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # The issue 4626 incident: three copies against a factor of two,
        # every node low on disk, and instances using the blob on all
        # three. Every copy is a running instance's backing file, so
        # none of them may be removed however over replicated the blob
        # looks.
        b = self._make_blob(['sf-1', 'sf-2', 'sf-3'], [])
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b,
            low_disk_nodes=['sf-1', 'sf-2', 'sf-3'],
            instance_nodes={
                'fake-instance-1': NODE_UUIDS['sf-1'],
                'fake-instance-2': NODE_UUIDS['sf-2'],
                'fake-instance-3': NODE_UUIDS['sf-3'],
            })
        b.drop_node_location.assert_not_called()

    @_pass_mocks
    def test_lost_blob_is_errored_and_not_retried(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # A created blob with no completed copies and no transfers in
        # flight is lost, not under replicated: replication has nothing
        # to copy from. It must be recorded as errored, not retried on
        # every pass forever.
        b = self._make_blob([], [])
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b,
            low_disk_nodes=[])
        self.assertEqual(dbo.STATE_ERROR, b.state)
        self.assertTrue(b.add_event.called)
        b.request_replication.assert_not_called()

    @_pass_mocks
    def test_initial_blob_with_no_locations_is_not_errored(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # A blob in the initial state has no locations because its first
        # fetch has not completed yet. That is not data loss.
        b = self._make_blob([], [], state=dbo.STATE_INITIAL)
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b,
            low_disk_nodes=[])
        self.assertNotEqual(dbo.STATE_ERROR, b.state)

    @_pass_mocks
    def test_underreplicated_blob_requests_replication(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # One completed copy against a factor of two is under
        # replicated, and still repairable.
        b = self._make_blob(['sf-1'], [])
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b,
            low_disk_nodes=[])
        b.request_replication.assert_called_once_with(allow_excess=0)
        self.assertNotEqual(dbo.STATE_ERROR, b.state)

    @_pass_mocks
    def test_full_node_rebalance_still_requests_extra_copy(
            self, mock_mariadb, mock_ipam, mock_network,
            mock_remove_uploads, mock_artifact, mock_instance, mock_blob,
            mock_low_disk, mock_nodes):
        # Exactly the replication factor of copies with one on a
        # low-disk node still requests a temporary extra copy so a later
        # pass can prune the copy on the full node.
        b = self._make_blob(['sf-1', 'sf-2'], [])
        self._run_pass(
            mock_mariadb, mock_ipam, mock_network, mock_artifact,
            mock_instance, mock_blob, mock_low_disk, mock_nodes, b,
            low_disk_nodes=['sf-2'])
        b.request_replication.assert_called_once_with(allow_excess=1)
        b.drop_node_location.assert_not_called()
