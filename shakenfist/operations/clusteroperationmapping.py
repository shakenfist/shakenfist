from shakenfist.operations.artifact_fetch_op import ArtifactFetchOp
from shakenfist.operations.node_blob_op import NodeBlobOp
from shakenfist.operations.node_inst_op import NodeInstOp
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetDescOp


OPERATION_NAMES_TO_CLASSES = {
    'artifact_fetch_op': ArtifactFetchOp,
    'node_blob_op': NodeBlobOp,
    'node_inst_op': NodeInstOp,
    'node_inst_netdesc_op': NodeInstNetDescOp
}
