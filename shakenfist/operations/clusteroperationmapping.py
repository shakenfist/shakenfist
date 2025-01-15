from shakenfist.operations.artifact_fetch_op import ArtifactFetchOp
from shakenfist.operations.imgcache_op import ImageCacheOp
from shakenfist.operations.node_blob_op import NodeBlobOp
from shakenfist.operations.node_inst_iface_op import NodeInstIfaceOp
from shakenfist.operations.node_inst_op import NodeInstOp
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetDescOp
from shakenfist.operations.node_inst_snap_op import NodeInstSnapOp


OPERATION_NAMES_TO_CLASSES = {
    'artifact_fetch_op': ArtifactFetchOp,
    'imgcache_op': ImageCacheOp,
    'node_blob_op': NodeBlobOp,
    'node_inst_iface_op': NodeInstIfaceOp,
    'node_inst_op': NodeInstOp,
    'node_inst_netdesc_op': NodeInstNetDescOp,
    'node_inst_snap_op': NodeInstSnapOp
}
