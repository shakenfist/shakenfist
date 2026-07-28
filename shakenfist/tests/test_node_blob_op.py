# Tests for NodeBlobOp cluster operation.
#
# In particular, regression coverage for the _ensure_local free-disk floor,
# which historically compared a byte quantity against a GB reservation and so
# reserved only ~20 bytes. The floor is now converted to bytes and reserves a
# real NODE_DISK_RESERVATION_GB * GiB.
from unittest import mock

from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import GiB
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
