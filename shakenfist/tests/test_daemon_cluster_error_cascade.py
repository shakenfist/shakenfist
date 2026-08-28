# Copyright 2026 Michael Still and contributors
#
# Tests for the cluster maintainer's reaction (phase 3 of node resource
# health) to a node that sf-resources marked STATE_ERROR because its storage
# is unhealthy: error the hosted instances (move to <state>-error, not delete)
# when instance storage failed, and drop the node's blob locations +
# re-replicate when the blob store failed -- both gated on the affected object
# type read back from the diagnosis event.

from unittest import mock

from shakenfist.daemons.cluster import main as cluster_main
from shakenfist.node import Node
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base


class _FakeInstance:
    def __init__(self, uuid, state_value='created'):
        self.uuid = uuid
        self._state = state_value
        self.error = None
        self.events = []

    @property
    def state(self):
        return mock.Mock(value=self._state)

    @state.setter
    def state(self, value):
        self._state = value

    def add_event(self, eventtype, message, extra=None, **kwargs):
        self.events.append((eventtype, message, extra))


class _FakeNode:
    def __init__(self, uuid='node-uuid', fqdn='sf-6', blobs=None):
        self.uuid = uuid
        self.fqdn = fqdn
        self.blobs = blobs if blobs is not None else []
        self.events = []

    def add_event(self, eventtype, message, extra=None, **kwargs):
        self.events.append((eventtype, message, extra))


def _make_monitor():
    m = cluster_main.Monitor.__new__(cluster_main.Monitor)
    m.lock = None
    m.pet_watchdog = mock.MagicMock()
    m._cascaded_error_nodes = set()
    return m


class CascadeErroredNodeTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.daemons.cluster.main.eventlog')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.node_health')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    def test_instance_and_blob_affected(
            self, mock_instance, mock_node_health, mock_blob, mock_eventlog):
        n = _FakeNode(blobs=['blob-1', 'blob-2'])
        insts = [_FakeInstance('i-1'), _FakeInstance('i-2', 'creating')]
        mock_instance.healthy_instances_on_node.return_value = insts
        mock_node_health.errored_node_affected_types.return_value = {
            ObjectType.INSTANCE, ObjectType.BLOB}
        b1, b2 = mock.MagicMock(), mock.MagicMock()
        mock_blob.from_db.side_effect = [b1, b2]

        m = _make_monitor()
        m._cascade_errored_node(n)

        # Instances errored (state before error), not deleted.
        self.assertEqual('created-error', insts[0]._state)
        self.assertEqual('creating-error', insts[1]._state)
        for i in insts:
            self.assertIn('unhealthy', i.error)
            self.assertEqual(1, len(i.events))
        # A per-instance audit event was also recorded on the node.
        self.assertEqual(2, len(n.events))

        # Blob locations dropped and re-replicated.
        b1.remove_location.assert_called_once_with('sf-6')
        b1.request_replication.assert_called_once_with()
        b2.remove_location.assert_called_once_with('sf-6')
        b2.request_replication.assert_called_once_with()
        self.assertEqual(2, mock_eventlog.add_event_multi.call_count)

        # Recorded as cascaded so it is not re-processed next pass.
        self.assertIn(str(n.uuid), m._cascaded_error_nodes)

    @mock.patch('shakenfist.daemons.cluster.main.eventlog')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.node_health')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    def test_instance_only_does_not_touch_blobs(
            self, mock_instance, mock_node_health, mock_blob, mock_eventlog):
        n = _FakeNode(blobs=['blob-1'])
        insts = [_FakeInstance('i-1')]
        mock_instance.healthy_instances_on_node.return_value = insts
        mock_node_health.errored_node_affected_types.return_value = {
            ObjectType.INSTANCE}

        m = _make_monitor()
        m._cascade_errored_node(n)

        self.assertEqual('created-error', insts[0]._state)
        mock_blob.from_db.assert_not_called()
        mock_eventlog.add_event_multi.assert_not_called()
        self.assertIn(str(n.uuid), m._cascaded_error_nodes)

    @mock.patch('shakenfist.daemons.cluster.main.eventlog')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.node_health')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    def test_uploads_only_errors_nothing_but_marks_cascaded(
            self, mock_instance, mock_node_health, mock_blob, mock_eventlog):
        n = _FakeNode(blobs=['blob-1'])
        insts = [_FakeInstance('i-1')]
        mock_instance.healthy_instances_on_node.return_value = insts
        mock_node_health.errored_node_affected_types.return_value = {
            ObjectType.UPLOAD}

        m = _make_monitor()
        m._cascade_errored_node(n)

        self.assertEqual('created', insts[0]._state)
        self.assertIsNone(insts[0].error)
        mock_blob.from_db.assert_not_called()
        # Nothing to drain, so the guard set is what stops a re-read.
        self.assertIn(str(n.uuid), m._cascaded_error_nodes)

    @mock.patch('shakenfist.daemons.cluster.main.eventlog')
    @mock.patch('shakenfist.daemons.cluster.main.Blob')
    @mock.patch('shakenfist.daemons.cluster.main.node_health')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    def test_blob_deleted_mid_cascade_is_skipped(
            self, mock_instance, mock_node_health, mock_blob, mock_eventlog):
        # A blob deleted between reading n.blobs and processing it (from_db
        # returns None) must be skipped cleanly, not raise.
        n = _FakeNode(blobs=['blob-gone', 'blob-live'])
        mock_instance.healthy_instances_on_node.return_value = []
        mock_node_health.errored_node_affected_types.return_value = {
            ObjectType.BLOB}
        b_live = mock.MagicMock()
        mock_blob.from_db.side_effect = [None, b_live]

        m = _make_monitor()
        m._cascade_errored_node(n)

        # Only the surviving blob was touched; the missing one did not raise
        # and produced no event.
        b_live.remove_location.assert_called_once_with('sf-6')
        b_live.request_replication.assert_called_once_with()
        self.assertEqual(1, mock_eventlog.add_event_multi.call_count)
        self.assertIn(str(n.uuid), m._cascaded_error_nodes)

    @mock.patch('shakenfist.daemons.cluster.main.node_health')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    def test_already_cascaded_is_a_noop(
            self, mock_instance, mock_node_health):
        n = _FakeNode()
        m = _make_monitor()
        m._cascaded_error_nodes.add(str(n.uuid))

        m._cascade_errored_node(n)

        mock_node_health.errored_node_affected_types.assert_not_called()
        mock_instance.healthy_instances_on_node.assert_not_called()

    @mock.patch('shakenfist.daemons.cluster.main.node_health')
    @mock.patch('shakenfist.daemons.cluster.main.instance')
    def test_unknown_blast_radius_retries(
            self, mock_instance, mock_node_health):
        n = _FakeNode()
        mock_node_health.errored_node_affected_types.return_value = None

        m = _make_monitor()
        m._cascade_errored_node(n)

        # No diagnosis yet -> nothing done and NOT recorded, so a later pass
        # once the event exists still cascades.
        mock_instance.healthy_instances_on_node.assert_not_called()
        self.assertNotIn(str(n.uuid), m._cascaded_error_nodes)


class CascadeDispatchTestCase(base.ShakenFistTestCase):
    """The node-management loop dispatches to the cascade and manages the
    guard set based on node state."""

    def _drive_node_loop(self, nodes):
        # Mock every collection the cleanup walks before the node-management
        # loop so it runs cleanly through to `for n in Nodes([])`.
        m = _make_monitor()
        m._cascade_errored_node = mock.MagicMock()
        patches = {
            'mariadb': mock.DEFAULT,
            'instance': mock.DEFAULT,
            'ipam': mock.DEFAULT,
            'network': mock.DEFAULT,
            'artifact': mock.DEFAULT,
            'remove_abandoned_uploads': mock.DEFAULT,
            'nodes_by_free_disk_descending': mock.DEFAULT,
            'Nodes': mock.DEFAULT,
        }
        with mock.patch.multiple(
                'shakenfist.daemons.cluster.main', **patches) as mocks:
            # The orphan artifact sweep lists namespaces once per pass and
            # treats an empty list as an unreadable one, skipping the rest
            # of the cleanup. A bare MagicMock iterates empty, so this has
            # to be set for the sections below to be reached at all.
            mocks['mariadb'].get_all_namespace_names.return_value = [
                'system']
            mocks['mariadb'].delete_stale_transfers.return_value = 0
            mocks['mariadb'].delete_stale_cluster_operation_targets \
                .return_value = 0
            mocks['mariadb'].get_active_blob_uuids.return_value = []
            mocks['mariadb'].get_expired_blob_uuids.return_value = []
            mocks['mariadb'].get_stale_transcoded_blob_uuids.return_value = []
            mocks['ipam'].IPAMs.return_value = []
            mocks['network'].floating_network.return_value = None
            mocks['artifact'].Artifacts.return_value = []
            mocks['nodes_by_free_disk_descending'].return_value = []
            mocks['Nodes'].return_value = nodes
            m._cluster_wide_cleanup(last_loop_run=0)
        return m

    def _node(self, state_value, uuid):
        import time
        n = mock.Mock()
        n.uuid = uuid
        n.fqdn = uuid
        n.last_seen = time.time()
        n.state.value = state_value
        return n

    def test_error_node_is_dispatched_to_cascade(self):
        n = self._node(Node.STATE_ERROR, 'sf-err')
        m = self._drive_node_loop([n])
        m._cascade_errored_node.assert_called_once_with(n)

    def test_non_error_node_discards_guard_entry(self):
        n = self._node(Node.STATE_CREATED, 'sf-ok')
        m = _make_monitor()
        m._cascade_errored_node = mock.MagicMock()
        m._cascaded_error_nodes.add('sf-ok')
        # Re-drive with the pre-seeded guard set.
        with mock.patch.multiple(
                'shakenfist.daemons.cluster.main',
                mariadb=mock.DEFAULT, instance=mock.DEFAULT,
                ipam=mock.DEFAULT, network=mock.DEFAULT,
                artifact=mock.DEFAULT, remove_abandoned_uploads=mock.DEFAULT,
                nodes_by_free_disk_descending=mock.DEFAULT,
                Nodes=mock.DEFAULT) as mocks:
            # The orphan artifact sweep lists namespaces once per pass and
            # treats an empty list as an unreadable one, skipping the rest
            # of the cleanup. A bare MagicMock iterates empty, so this has
            # to be set for the sections below to be reached at all.
            mocks['mariadb'].get_all_namespace_names.return_value = [
                'system']
            mocks['mariadb'].delete_stale_transfers.return_value = 0
            mocks['mariadb'].delete_stale_cluster_operation_targets \
                .return_value = 0
            mocks['mariadb'].get_active_blob_uuids.return_value = []
            mocks['mariadb'].get_expired_blob_uuids.return_value = []
            mocks['mariadb'].get_stale_transcoded_blob_uuids.return_value = []
            mocks['ipam'].IPAMs.return_value = []
            mocks['network'].floating_network.return_value = None
            mocks['artifact'].Artifacts.return_value = []
            mocks['nodes_by_free_disk_descending'].return_value = []
            mocks['Nodes'].return_value = [n]
            m._cluster_wide_cleanup(last_loop_run=0)
        self.assertNotIn('sf-ok', m._cascaded_error_nodes)
        m._cascade_errored_node.assert_not_called()
