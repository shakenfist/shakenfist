from shakenfist import artifact
from shakenfist import blob
from shakenfist import instance
from shakenfist import network
from shakenfist import networkinterface

OBJECT_NAMES_TO_CLASSES = {
    'artifact': artifact.Artifact,
    'blob': blob.Blob,
    'instance': instance.Instance,
    'network': network.Network,
    'networkinterface': networkinterface.NetworkInterface
}
