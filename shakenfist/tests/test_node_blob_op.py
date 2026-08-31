# Tests for NodeBlobOp cluster operation.
#
# In particular, regression coverage for the _ensure_local free-disk floor,
# which historically compared a byte quantity against a GB reservation and so
# reserved only ~20 bytes. The floor is now converted to bytes and reserves a
# real NODE_DISK_RESERVATION_GB * GiB.
#
# Also covers the BlobAlreadyBeingTransferred retry path, which used to be a
# bare self.defer() relying on defer()'s 15.0 s default and retried forever.
# It now goes through defer_with_backoff(), which has a bounded budget.
from unittest import mock

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import GiB
from shakenfist.exceptions import BlobAlreadyBeingTransferred
from shakenfist.operations.node_blob_op import NodeBlobOp
from shakenfist.tests import base


class NodeBlobOpEnsureLocalTestCase(base.ShakenFistTestCase):
    """Regression tests for the _ensure_local disk floor (byte/GB fix)."""

    def _op(self):
        # _ensure_local reads only its arguments, config and mariadb; construct
        # the operation without running __init__ so the test needs no DB rows.
        return NodeBlobOp.__new__(NodeBlobOp)

    def _blob(self, size_bytes):
        b = mock.MagicMock()
        # An empty locations list keeps this node out of the "already present"
        # early return, so the disk-floor branch is exercised.
        b.locations = []
        b.size = size_bytes
        return b

    @mock.patch('shakenfist.operations.node_blob_op.mariadb.get_node_metrics')
    @mock.patch('shakenfist.operations.node_blob_op.config')
    def test_insufficient_headroom_skips_replication(
            self, mock_config, mock_get_metrics):
        # 25 GiB free minus a 10 GiB blob leaves 15 GiB of headroom, which is
        # less than the real 20 GiB reservation, so the replica is refused.
        # Under the historical bug the reservation was compared in GB against a
        # byte quantity (~20 bytes), so this pull would wrongly have proceeded
        # -- this is the regression the byte conversion closes.
        mock_config.NODE_NAME = 'node2'
        mock_config.NODE_UUID = 'uuid-node2'
        mock_config.NODE_DISK_RESERVATION_GB = 20.0
        mock_get_metrics.return_value = {
            'metrics': {'disk_free_blobs': 25 * GiB}}

        b = self._blob(10 * GiB)
        self._op()._ensure_local(b)

        b.add_event.assert_called_once_with(
            EVENT_TYPE_AUDIT, 'cannot replicate blob, insufficient space')
        b.ensure_local.assert_not_called()

    @mock.patch('shakenfist.operations.node_blob_op.mariadb.get_node_metrics')
    @mock.patch('shakenfist.operations.node_blob_op.config')
    def test_sufficient_headroom_replicates(
            self, mock_config, mock_get_metrics):
        # 35 GiB free minus a 10 GiB blob leaves 25 GiB of headroom, which
        # clears the 20 GiB reservation, so the pull runs.
        mock_config.NODE_NAME = 'node2'
        mock_config.NODE_UUID = 'uuid-node2'
        mock_config.NODE_DISK_RESERVATION_GB = 20.0
        mock_get_metrics.return_value = {
            'metrics': {'disk_free_blobs': 35 * GiB}}

        b = self._blob(10 * GiB)
        self._op()._ensure_local(b)

        b.ensure_local.assert_called_once_with(wait_for_other_transfers=False)
        b.add_event.assert_not_called()


class NodeBlobOpAlreadyBeingTransferredTestCase(base.ShakenFistTestCase):
    """Tests for the BlobAlreadyBeingTransferred retry path in _ensure_local.

    This used to be a bare self.defer(), which relied on defer()'s
    delay=15.0 default and retried indefinitely. It now goes through
    defer_with_backoff(), which has a bounded retry budget; on exhaustion
    the operation must record an audit event and return normally rather
    than raising or erroring itself out (blob replication contention is
    benign, matching the insufficient-space branch above).
    """

    def _op(self):
        # _ensure_local reads only its arguments, config and mariadb; construct
        # the operation without running __init__ so the test needs no DB rows.
        return NodeBlobOp.__new__(NodeBlobOp)

    def _blob(self, size_bytes):
        b = mock.MagicMock()
        # An empty locations list keeps this node out of the "already present"
        # early return, so the transfer attempt is exercised.
        b.locations = []
        b.size = size_bytes
        b.ensure_local.side_effect = BlobAlreadyBeingTransferred()
        return b

    @mock.patch('shakenfist.operations.node_blob_op.mariadb.get_node_metrics')
    @mock.patch('shakenfist.operations.node_blob_op.config')
    def test_retry_scheduled_defers_and_does_not_audit(
            self, mock_config, mock_get_metrics):
        mock_config.NODE_NAME = 'node2'
        mock_config.NODE_UUID = 'uuid-node2'
        mock_config.NODE_DISK_RESERVATION_GB = 20.0
        mock_get_metrics.return_value = {
            'metrics': {'disk_free_blobs': 35 * GiB}}

        b = self._blob(10 * GiB)
        op = self._op()
        op.defer_with_backoff = mock.MagicMock(return_value=True)

        op._ensure_local(b)

        op.defer_with_backoff.assert_called_once_with(
            delays=NodeBlobOp._TRANSFER_CONTENTION_DELAYS,
            reason='blob already being transferred')
        b.add_event.assert_not_called()

    @mock.patch('shakenfist.operations.node_blob_op.mariadb.get_node_metrics')
    @mock.patch('shakenfist.operations.node_blob_op.config')
    def test_budget_exhausted_audits_without_raising_or_erroring(
            self, mock_config, mock_get_metrics):
        mock_config.NODE_NAME = 'node2'
        mock_config.NODE_UUID = 'uuid-node2'
        mock_config.NODE_DISK_RESERVATION_GB = 20.0
        mock_get_metrics.return_value = {
            'metrics': {'disk_free_blobs': 35 * GiB}}

        b = self._blob(10 * GiB)
        op = self._op()
        op.defer_with_backoff = mock.MagicMock(return_value=False)

        state_patcher = mock.patch.object(NodeBlobOp, '_state_update')
        mock_state_update = state_patcher.start()
        self.addCleanup(state_patcher.stop)

        # Must not raise: dispatch_task's except Exception around this call
        # sets STATE_ERROR on anything that escapes, so this path has to
        # handle exhaustion itself rather than propagate.
        op._ensure_local(b)

        op.defer_with_backoff.assert_called_once_with(
            delays=NodeBlobOp._TRANSFER_CONTENTION_DELAYS,
            reason='blob already being transferred')
        b.add_event.assert_called_once()
        args, _ = b.add_event.call_args
        self.assertEqual(EVENT_TYPE_AUDIT, args[0])
        mock_state_update.assert_not_called()

    @mock.patch('shakenfist.operations.baseoperation.mariadb.enqueue_work_item')
    @mock.patch('shakenfist.operations.node_blob_op.mariadb.get_node_metrics')
    @mock.patch('shakenfist.operations.node_blob_op.config')
    def test_real_backoff_requeues_and_leaves_the_op_queued(
            self, mock_config, mock_get_metrics, mock_enqueue):
        """The two tests above stub defer_with_backoff, so they cannot see this.

        What the change actually depends on is not the branch but its
        effect: a scheduled retry must leave the operation in
        STATE_QUEUED, because that is what makes
        BaseClusterOperation.execute() stop dispatching further tasks
        instead of falling through to STATE_COMPLETE. Let the real
        defer_with_backoff run so a change to that contract fails here.
        """
        mock_config.NODE_NAME = 'node2'
        mock_config.NODE_UUID = 'uuid-node2'
        mock_config.NODE_DISK_RESERVATION_GB = 20.0
        mock_get_metrics.return_value = {
            'metrics': {'disk_free_blobs': 35 * GiB}}

        b = self._blob(10 * GiB)
        op = self._op()
        op.queue_name = 'node2-background_high_io'
        op.current_defer_count = 0
        op.add_event = mock.MagicMock()

        uuid_patcher = mock.patch.object(
            NodeBlobOp, 'uuid', new_callable=mock.PropertyMock,
            return_value='uuid-op')
        uuid_patcher.start()
        self.addCleanup(uuid_patcher.stop)

        recorded = []
        state_patcher = mock.patch.object(
            NodeBlobOp, 'state',
            new_callable=mock.PropertyMock,
            side_effect=lambda *a: recorded.append(a))
        state_patcher.start()
        self.addCleanup(state_patcher.stop)

        op._ensure_local(b)

        # Re-enqueued on the first rung of this path's own ladder, which
        # is longer than defer_with_backoff's default so a healthy peer
        # transfer is outlasted.
        mock_enqueue.assert_called_once()
        enqueue_args, enqueue_kwargs = mock_enqueue.call_args
        self.assertEqual(
            NodeBlobOp._TRANSFER_CONTENTION_DELAYS[0], enqueue_kwargs['delay'])
        self.assertEqual(1, enqueue_args[1]['defer_count'])

        # And left queued, so execute() stops rather than completing.
        self.assertEqual(
            [(NodeBlobOp.STATE_QUEUED,)], recorded)

        # Contention is not an audit-worthy failure while retries remain.
        b.add_event.assert_not_called()
