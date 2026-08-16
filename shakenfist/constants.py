import importlib
import uuid

# Note the most exciting constants ever
KiB = 1024
MiB = 1024 * 1024
GiB = 1024 * 1024 * 1024
TiB = 1024 * 1024 * 1024 * 1024


# How long we wait to acquire an etcd lock by default.
ETCD_ATTEMPT_TIMEOUT = 60


# Lease length on cluster locks (seconds). Holders refresh every third
# of this while alive; if a holder dies, the row's lease lapses and
# another candidate may steal it. Lives here rather than alongside the
# locks code or the database accessor so both ``shakenfist.locks`` and
# ``shakenfist.mariadb`` can read it without the two modules having to
# import each other.
CLUSTER_LOCK_LEASE_SECONDS = 60


# The resources daemon publishes a per-second rate for each raw psutil
# counter by appending this suffix to the counter name. The full key for
# the disk busy rate is spelled out here because both the scheduler and
# the queue workers consume it, and each historically invented its own
# subtly wrong spelling which silently read the default of zero. The
# published value is a float (often serialised as a string) counting
# milliseconds of disk busy time per wall clock second, so 1000.0 is one
# fully busy disk; parse it with float(), not int().
METRICS_DELTA_PER_SECOND_SUFFIX = '_delta_per_second'
DISK_BUSY_PER_SECOND_METRIC = 'disk_busy_time' + METRICS_DELTA_PER_SECOND_SUFFIX


# The node states a node may be scheduled onto. A node in any other state
# -- errored, missing, stopping, stopped, deleted -- is not a scheduling
# candidate, so nothing may count its resources as available capacity.
# Note that "degraded" is active: a node with one unhealthy store still
# runs instances.
#
# This lives here rather than only as Node.ACTIVE_STATES so that
# ``shakenfist.mariadb`` can filter on it in SQL without importing
# ``shakenfist.node`` (which imports mariadb) -- the same trick as
# CLUSTER_LOCK_LEASE_SECONDS above. ``Node.ACTIVE_STATES`` is defined
# from this set, so there is one definition rather than two that drift.
NODE_ACTIVE_STATES = frozenset({'initial', 'created', 'degraded'})


# Disk caching mode. Refer to docs/development/io_performance_tuning.md for
# more details than you really want.
#
# Options are:
#  - "default", which is the libvirt default of writeback
#  - "none", which is our recommendation
#  - "writethrough"
#  - "writeback"
#  - "directsync"
#  - "unsafe"
DISK_CACHE_MODE = 'none'


# qcow2 cluster size. Refer to docs/development/io_performance_tuning.md for
# more details than you really want. The value must be a power of 2 and less
# than 2MB. qemu defaults to 64K and we recommend 2048K to improve IO performance
# on larger disks. Note for a change in this setting to fully take effect you
# need to re-transcode the images into the image cache. There is no automation
# to support doing at at this time.
QCOW2_CLUSTER_SIZE = '2048K'


# This description is used to uniquely identify transcoded versions of images.
# It is important that it be bumped when the transcode format changes.
TRANSCODE_DESCRIPTION = 'zlib;qcow2;cluster_size'


# Instance agent states
AGENT_NEVER_TALKED = 'not ready (no contact)'
AGENT_STOPPED_TALKING = 'not ready (unresponsive)'
AGENT_STARTED = 'not ready (agent startup)'
AGENT_STOPPED = 'not ready (agent stopped)'
AGENT_TOO_OLD = 'not ready (agent too old)'
AGENT_INSTANCE_PAUSED = 'not ready (instance paused)'
AGENT_INSTANCE_OFF = 'not ready (instance powered off)'
AGENT_DEGRADED = 'not ready (%s)'
AGENT_READY = 'ready'
AGENT_READY_DEGRADED = 'ready (degraded)'


# Event types
# NOTE(mikal): if you add to this list, you must also update the MAX_AGE config
# options.
EVENT_TYPE_AUDIT = 'audit'
EVENT_TYPE_MUTATE = 'mutate'
EVENT_TYPE_STATUS = 'status'
EVENT_TYPE_USAGE = 'usage'
EVENT_TYPE_RESOURCES = 'resources'
EVENT_TYPE_PRUNE = 'prune'

# Node resource-health verdicts. A dedicated channel, separate from the audit
# action log, so that node_health can reliably read back the most recent
# diagnosis regardless of how many audit events (for example the cluster
# cascade's per-instance and per-blob action events) have since been written
# against the same node.
EVENT_TYPE_HEALTH = 'health'

# Use only for events which pre-date the type system
EVENT_TYPE_HISTORIC = 'historic'

# All event types
EVENT_TYPES = [EVENT_TYPE_AUDIT, EVENT_TYPE_MUTATE, EVENT_TYPE_STATUS,
               EVENT_TYPE_USAGE, EVENT_TYPE_RESOURCES, EVENT_TYPE_PRUNE,
               EVENT_TYPE_HEALTH, EVENT_TYPE_HISTORIC]

# Blob hashing algorithms
BLOB_HASH_ALGORITHMS = ['sha1', 'sha256', 'sha512', 'xxh128']

# Well-known UUID for the floating network. This is a valid UUID4 that contains
# "F10A7" (FLOAT) repeated throughout for easy identification. The floating
# network previously used the string "floating" as its UUID, but this was not
# a valid UUID4.
FLOATING_NETWORK_UUID = uuid.UUID('f10a7f10-a7f1-4a7f-a10a-7f10a7f10a7f')

OBJECT_NAMES_TO_CLASSES = {
    'agentoperation': 'operations.agentoperation.AgentOperation',
    'artifact': 'artifact.Artifact',
    'artifact_fetch_op': 'operations.artifact_fetch_op.ArtifactFetchOp',
    'blob': 'blob.Blob',
    'dhcp': 'managed_executables.dnsmasq.DnsMasq',
    'imgcache_op': 'operations.imgcache_op.ImageCacheOp',
    'instance': 'instance.Instance',
    'ipam': 'ipam.IPAM',
    'namespace': 'namespace.Namespace',
    'namespace_claim': 'namespace_claim.NamespaceClaim',
    'namespace_key': 'namespace_key.NamespaceKey',
    'trusted_issuer': 'trusted_issuer.TrustedIssuer',
    'mapping_rule': 'mapping_rule.MappingRule',
    'net_iface_ip_op': 'operations.net_iface_ip_op.NetIfaceIPOp',
    'net_iface_op': 'operations.net_iface_op.NetIfaceOp',
    'net_ip_op': 'operations.net_ip_op.NetIPOp',
    'net_macaddr_ip_op': 'operations.net_macaddr_ip_op.NetMacaddrIPOp',
    'net_op': 'operations.net_op.NetOp',
    'network': 'network.network.Network',
    'interface': 'network.interface.NetworkInterface',
    'node': 'node.Node',
    'node_aop_op': 'operations.node_aop_op.NodeAgentopOp',
    'node_blob_op': 'operations.node_blob_op.NodeBlobOp',
    'node_inst_net_iface_op': 'operations.node_inst_net_iface_op.NodeInstNetIfaceOp',
    'node_inst_netdesc_op': 'operations.node_inst_netdesc_op.NodeInstNetdescOp',
    'node_inst_op': 'operations.node_inst_op.NodeInstOp',
    'node_inst_snap_op': 'operations.node_inst_snap_op.NodeInstSnapOp',
    'node_net_op': 'operations.node_net_op.NodeNetOp',
    'upload': 'upload.Upload'
}


OPERATION_NAMES_TO_CLASSES = {
    'artifact_fetch_op': 'operations.artifact_fetch_op.ArtifactFetchOp',
    'imgcache_op': 'operations.imgcache_op.ImageCacheOp',
    'net_iface_ip_op': 'operations.net_iface_ip_op.NetIfaceIPOp',
    'net_iface_op': 'operations.net_iface_op.NetIfaceOp',
    'net_ip_op': 'operations.net_ip_op.NetIPOp',
    'net_macaddr_ip_op': 'operations.net_macaddr_ip_op.NetMacaddrIPOp',
    'net_op': 'operations.net_op.NetOp',
    'node_aop_op': 'operations.node_aop_op.NodeAgentopOp',
    'node_blob_op': 'operations.node_blob_op.NodeBlobOp',
    'node_inst_net_iface_op': 'operations.node_inst_net_iface_op.NodeInstNetIfaceOp',
    'node_inst_netdesc_op': 'operations.node_inst_netdesc_op.NodeInstNetdescOp',
    'node_inst_op': 'operations.node_inst_op.NodeInstOp',
    'node_inst_snap_op': 'operations.node_inst_snap_op.NodeInstSnapOp',
    'node_net_op': 'operations.node_net_op.NodeNetOp'
}


class NoSuchObject(Exception):
    ...


def get_object_class(object_type):
    cls = OBJECT_NAMES_TO_CLASSES.get(object_type)
    if not cls:
        raise NoSuchObject(object_type)

    lib_name = '.'.join(cls.split('.')[:-1])
    cls_name = cls.split('.')[-1]
    lib = importlib.import_module(f'shakenfist.{lib_name}')
    return getattr(lib, cls_name)


# A list of object states subject to "hard deletion"
FINAL_OBJECT_STATES = [
    'deleted',
    'complete',
    'abort'
]
