from shakenfist.operations.artifact_fetch_op import ArtifactFetchOp
from shakenfist.operations.imgcache_op import ImageCacheOp
from shakenfist.operations.net_iface_ip_op import NetIfaceIPOp
from shakenfist.operations.net_iface_op import NetIfaceOp
from shakenfist.operations.net_ip_op import NetIPOp
from shakenfist.operations.net_macaddr_ip_op import NetMacaddrIPOp
from shakenfist.operations.net_op import NetOp
from shakenfist.operations.node_aop_op import NodeAgentopOp
from shakenfist.operations.node_blob_op import NodeBlobOp
from shakenfist.operations.node_inst_net_iface_op import NodeInstNetIfaceOp
from shakenfist.operations.node_inst_op import NodeInstOp
from shakenfist.operations.node_inst_netdesc_op import NodeInstNetdescOp
from shakenfist.operations.node_inst_snap_op import NodeInstSnapOp
from shakenfist.operations.node_net_op import NodeNetOp


OPERATION_NAMES_TO_CLASSES = {
    'artifact_fetch_op': ArtifactFetchOp,
    'imgcache_op': ImageCacheOp,
    'net_iface_ip_op': NetIfaceIPOp,
    'net_iface_op': NetIfaceOp,
    'net_ip_op': NetIPOp,
    'net_macaddr_ip_op': NetMacaddrIPOp,
    'net_op': NetOp,
    'node_aop_op': NodeAgentopOp,
    'node_blob_op': NodeBlobOp,
    'node_inst_net_iface_op': NodeInstNetIfaceOp,
    'node_inst_op': NodeInstOp,
    'node_inst_netdesc_op': NodeInstNetdescOp,
    'node_inst_snap_op': NodeInstSnapOp,
    'node_net_op': NodeNetOp
}
