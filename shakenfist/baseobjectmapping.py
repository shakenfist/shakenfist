from shakenfist import artifact
from shakenfist import blob
from shakenfist import instance
from shakenfist import network
from shakenfist import networkinterface
from shakenfist import node

# Remember to update the separate list in metrics.py as well!
OBJECT_NAMES_TO_CLASSES = {
    'artifact': artifact.Artifact,
    'blob': blob.Blob,
    'instance': instance.Instance,
    'network': network.Network,
    'networkinterface': networkinterface.NetworkInterface,
    'node': node.Node
}

OBJECT_NAMES_TO_ITERATORS = {
    'artifact': artifact.Artifacts,
    'blob': blob.Blobs,
    'instance': instance.Instances,
    'network': network.Networks,
    'networkinterface': networkinterface.NetworkInterfaces,
    'node': node.Nodes
}
