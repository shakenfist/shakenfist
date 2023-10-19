from shakenfist import agentoperation
from shakenfist import artifact
from shakenfist import blob
from shakenfist import instance
from shakenfist import ipam
from shakenfist import namespace
from shakenfist import network
from shakenfist import networkinterface
from shakenfist import node

OBJECT_NAMES_TO_CLASSES = {
    'agentoperation': agentoperation.AgentOperation,
    'artifact': artifact.Artifact,
    'blob': blob.Blob,
    'instance': instance.Instance,
    'ipam': ipam.IPAM,
    'namespace': namespace.Namespace,
    'network': network.Network,
    'networkinterface': networkinterface.NetworkInterface,
    'node': node.Node
}

OBJECT_NAMES_TO_ITERATORS = {
    'agentoperation': agentoperation.AgentOperations,
    'artifact': artifact.Artifacts,
    'blob': blob.Blobs,
    'instance': instance.Instances,
    'ipam': ipam.IPAMs,
    'namespace': namespace.Namespaces,
    'network': network.Networks,
    'networkinterface': networkinterface.NetworkInterfaces,
    'node': node.Nodes
}
