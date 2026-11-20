import importlib
from typing import Any
from typing import Union
import uuid

from shakenfist.schema.object_types import ObjectType

# Note the most exciting constants ever
KiB = 1024
MiB = 1024 * 1024
GiB = 1024 * 1024 * 1024
TiB = 1024 * 1024 * 1024 * 1024


# How long we wait to acquire an etcd lock by default.
ETCD_ATTEMPT_TIMEOUT = 60


# The longest an object's key may be, and so the narrowest a database
# column which stores one may be. Almost every object is keyed by its
# uuid, 36 characters -- but a namespace is keyed by its *name*, and
# names are longer than that. The two were allowed to drift: the create
# handler published and enforced a 63 character ceiling while
# object_states.object_uuid was still VARCHAR(36), so every name between
# 37 and 63 characters was accepted by the API and then refused by the
# database with error 1406, answering an opaque 500 and leaving a
# namespace row behind with no state (issue 4856).
#
# Both sides now read this. It lives here, rather than beside either of
# them, so that shakenfist.mariadb and shakenfist.external_api.auth can
# share it without importing each other.
NAMESPACE_NAME_MAX_LENGTH = 63
OBJECT_KEY_MAX_LENGTH = NAMESPACE_NAME_MAX_LENGTH


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


def get_object_class(object_type: str) -> Any:
    cls = OBJECT_NAMES_TO_CLASSES.get(object_type)
    if not cls:
        raise NoSuchObject(object_type)

    lib_name = '.'.join(cls.split('.')[:-1])
    cls_name = cls.split('.')[-1]
    lib = importlib.import_module(f'shakenfist.{lib_name}')
    return getattr(lib, cls_name)


# The object states subject to "hard deletion" for every object type.
# This is keyed by state value across every type, so a state only one
# object type can reach is inert for the rest. A tuple, because what is
# in it decides what the sweep deletes: nothing should be able to widen
# it for the rest of the process by appending to it.
#
# 'error' is deliberately not in it. Read final_object_states() below
# rather than this: 'error' is final for some types and not for others,
# and which is which is a decision per type rather than one value shared
# by all of them (issue 3922).
FINAL_OBJECT_STATES: tuple[str, ...] = (
    'deleted',
    'complete',
    'abort',
    'expired',
)


# The object types other than cluster operations for which reaching
# 'error' means the object is finished with, so the hard-delete sweep
# takes it like any other finished object. It is empty: no such type
# exists today, and cluster operations do not need to be listed because
# error_is_final() takes every one of them by its '_op' suffix. This set
# is where a non-operation type would be added.
#
# Every non-operation type is deliberately out, and the reason is
# specific to the type rather than caution:
#
#   instance, network, networkinterface, node -- 'error' is a state an
#   operator is expected to look at and may recover from, and for a
#   node and a network the object is still live: it holds its vxlan,
#   its dnsmasq, its NAT rules and its IPAM reservations, and
#   sf-ctl clear-node-error returns an errored node to service.
#   Sweeping these would destroy a running cluster's objects, not just
#   the evidence.
#
#   blob -- an errored blob has partial files on disk (the snapshot
#   path is what errors one), and nothing moves it to 'deleted', which
#   is what removes files. Hard deleting the row would strand the
#   files with no record that they exist, turning a row leak into a
#   disk leak.
#
#   artifact -- Artifact.ACTIVE_STATES includes 'error', so an errored
#   artifact still resolves by name and a user can still see why their
#   fetch failed. Changing that is the artifact UX work's to make.
#
#   agentoperation -- the sidechannel daemon depends on 'error' not
#   being swept: it resolves an operation whose executor died as
#   errored rather than expired specifically so the daemon-restart
#   case is still there for an operator to read (see
#   daemons/sidechannel/main.py). Issue 3922 argued the other way; the
#   code's reason is more specific and is load-bearing, so it wins
#   until someone changes it deliberately.
#
# Adding a type here changes what the sweep deletes, so each addition
# wants its own reason written down beside the ones above.
ERROR_IS_FINAL_OBJECT_TYPES: frozenset[str] = frozenset()

_FINAL_OBJECT_STATES_WITH_ERROR: tuple[str, ...] = (
    FINAL_OBJECT_STATES + ('error',))


def error_is_final(object_type: Union[str, ObjectType]) -> bool:
    """Is 'error' a final state for this object type?

    It is for every cluster operation, which is every type whose name ends
    in '_op'. An errored operation carries its failure reason on its state
    row, the caller has either read it or gone away, and the operation is a
    pure database record with nothing on disk behind it. Operations are
    also where the leak bit: they arrive tens of thousands a day on a busy
    cluster against a few hundred of everything else, and an errored one
    used to be kept forever while a complete, aborted or expired one was
    swept. CLUSTER_OPERATION_RETENTION is the window in which an operator
    can read an errored operation, the same window they get for every other
    finished one. The suffix is the same test _deleted_object_delay() uses
    to give operations that retention, and a test over ObjectType pins that
    every '_op' type is a cluster operation.

    It is not for any other type, unless ERROR_IS_FINAL_OBJECT_TYPES names
    it; that set's comment records why each type is out.
    """
    name = str(object_type)
    return name.endswith('_op') or name in ERROR_IS_FINAL_OBJECT_TYPES


def final_object_states(
        object_type: Union[str, ObjectType]) -> list[str]:
    """The states the hard-delete sweep may take this type in.

    FINAL_OBJECT_STATES for most types, plus 'error' for the types
    which treat an error as finished with. A new list on every call, so
    a caller which changes the one it was given changes nothing else.
    """
    if error_is_final(object_type):
        return list(_FINAL_OBJECT_STATES_WITH_ERROR)
    return list(FINAL_OBJECT_STATES)


# The refusal stage published when the capacity admission guard, rather
# than one of scheduler.py's named pre-filter stages, refused every
# candidate. It is a published API token (it appears in the "stage"
# field of a 507 body) with two independent producers -- the create
# path in external_api/instance.py and the preflight redirect in
# operations/node_inst_netdesc_op.py -- so it is named once here rather
# than written out at each. Whether it is retry-worthy is decided in
# capacity_refusal_is_transient() below.
CAPACITY_GUARD_STAGE = 'capacity_guard'


# Which refusal stages a retry can actually clear, and which it cannot.
#
# This line was originally drawn at the exception class: every
# LowResourceException was transient. That was too coarse. The class
# covers filter stages which are momentary shortages *and* filter
# stages which are structural facts about the request or the cluster,
# and telling a client to retry the second kind makes it replay an
# impossible request until its deadline for nothing.
#
# These live here rather than in external_api/base.py (where they were
# written) because the API is no longer their only consumer: a
# preflight abort records the same classification on the instance
# (issue 4568), and instance.py cannot import the API layer. base.py
# re-exports both names.
#
# Transient -- the resource is measured, contended and returned by an
# ordinary instance teardown within seconds (5.3-5.6 s was measured for
# CPU):
TRANSIENT_CAPACITY_STAGES = frozenset([
    'sufficient_idle_cpu',
    'sufficient_idle_memory',
    'sufficient_free_disk',
    'sufficient_idle_disk',
    'queue_state',

    # The guard is an accounting refusal against the capacity counters,
    # which a concurrent delete or a reconciler pass moves on the same
    # horizon. This holds only while mariadb.CLAIM_ENFORCEMENT_HARD is
    # False: today a namespace claim cannot refuse a placement at all,
    # so every guard denial is cluster or node capacity. Once that
    # constant is flipped (see PLAN-scheduler-reservations.md) a claim
    # denial becomes namespace quota exhaustion, which no wait clears,
    # and claim denials must be split out of this set at that point
    # rather than silently inheriting it.
    CAPACITY_GUARD_STAGE,
])

# Permanent -- the cluster would refuse the identical request again in
# fifteen seconds, in an hour, and after every instance on it had been
# deleted. cpu_max_per_instance means the request asks for more vCPUs
# than any single node will ever host; is_hypervisor and pre_schedule
# mean the candidate set was empty before any resource was measured.
# These still publish a stage and a 507 -- the refusal is real and the
# stage is worth reading -- but they publish transient: false and no
# Retry-After.
PERMANENT_CAPACITY_STAGES = frozenset([
    'pre_schedule',
    'is_hypervisor',
    'cpu_max_per_instance',
])


def capacity_refusal_is_transient(stage: str) -> bool:
    """Whether waiting can clear a refusal published at this stage.

    The stage is the sole discriminator, for the reason
    capacity_error() gives: the alternative -- a transient= argument at
    each call site -- puts the classification somewhere it can be got
    wrong once per caller. A stage in neither set, including an empty
    one and the 'unknown' fallback for a refusal which arrived carrying
    no stage at all, is not transient: refusing to promise
    retry-worthiness is the safe direction to be wrong in.
    """
    return stage in TRANSIENT_CAPACITY_STAGES
