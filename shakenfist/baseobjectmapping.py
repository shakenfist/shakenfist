from shakenfist import artifact
from shakenfist import blob
from shakenfist import instance
from shakenfist import ipam
from shakenfist.managed_executables import dnsmasq
from shakenfist import namespace
from shakenfist import network
from shakenfist import networkinterface
from shakenfist import node
from shakenfist.operations import agentoperation
from shakenfist.operations import node_inst_op
from shakenfist import upload

OBJECT_NAMES_TO_CLASSES = {
    'agentoperation': agentoperation.AgentOperation,
    'artifact': artifact.Artifact,
    'blob': blob.Blob,
    'dhcp': dnsmasq.DnsMasq,
    'instance': instance.Instance,
    'ipam': ipam.IPAM,
    'namespace': namespace.Namespace,
    'network': network.Network,
    'networkinterface': networkinterface.NetworkInterface,
    'node': node.Node,
    'node_inst_op': node_inst_op.NodeInstOp,
    'upload': upload.Upload
}
