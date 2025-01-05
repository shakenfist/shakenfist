from shakenfist import artifact
from shakenfist.operations import artifact_fetch_op
from shakenfist import blob
from shakenfist import instance
from shakenfist import ipam
from shakenfist.managed_executables import dnsmasq
from shakenfist import namespace
from shakenfist import network
from shakenfist import networkinterface
from shakenfist import node
from shakenfist.operations import agentoperation
from shakenfist.operations import node_blob_op
from shakenfist.operations import node_inst_op
from shakenfist.operations import node_inst_netdesc_op
from shakenfist import upload

OBJECT_NAMES_TO_CLASSES = {
    'agentoperation': agentoperation.AgentOperation,
    'artifact': artifact.Artifact,
    'artifact_fetch_op': artifact_fetch_op.ArtifactFetchOp,
    'blob': blob.Blob,
    'dhcp': dnsmasq.DnsMasq,
    'instance': instance.Instance,
    'ipam': ipam.IPAM,
    'namespace': namespace.Namespace,
    'network': network.Network,
    'networkinterface': networkinterface.NetworkInterface,
    'node': node.Node,
    'node_blob_op': node_blob_op.NodeBlobOp,
    'node_inst_op': node_inst_op.NodeInstOp,
    'node_inst_netdesc_op': node_inst_netdesc_op.NodeInstNetDescOp,
    'upload': upload.Upload
}
