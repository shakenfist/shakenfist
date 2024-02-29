# Note the most exciting constants ever
KiB = 1024
MiB = 1024 * 1024
GiB = 1024 * 1024 * 1024
TiB = 1024 * 1024 * 1024 * 1024

# Sometimes we hold a lock for a long time and need to refresh it. This
# is how often we do that refresh.
LOCK_REFRESH_SECONDS = 5


# How long we wait to acquire an etcd lock by default.
ETCD_ATTEMPT_TIMEOUT = 60


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

# Use only for events which pre-date the type system
EVENT_TYPE_HISTORIC = 'historic'

# All event types
EVENT_TYPES = [EVENT_TYPE_AUDIT, EVENT_TYPE_MUTATE, EVENT_TYPE_STATUS,
               EVENT_TYPE_USAGE, EVENT_TYPE_RESOURCES, EVENT_TYPE_PRUNE,
               EVENT_TYPE_HISTORIC]

# Fake object type for API request tracing
API_REQUESTS = 'api-requests'