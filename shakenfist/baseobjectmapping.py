from shakenfist import artifact
from shakenfist import blob
from shakenfist import instance
from shakenfist import namespace
from shakenfist import network
from shakenfist import networkinterface
from shakenfist import node

OBJECT_NAMES_TO_CLASSES = {
    'artifact': artifact.Artifact,
    'blob': blob.Blob,
    'instance': instance.Instance,
    'namespace': namespace.Namespace,
    'network': network.Network,
    'networkinterface': networkinterface.NetworkInterface,
    'node': node.Node
}
