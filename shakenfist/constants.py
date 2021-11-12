# Note the most exciting constants ever
KiB = 1024
MiB = 1024 * 1024
GiB = 1024 * 1024 * 1024

# Sometimes we hold a lock for a long time and need to refresh it. This
# is how often we do that refresh.
LOCK_REFRESH_SECONDS = 5


# How long we wait to acquire an etcd lock by default.
ETCD_ATTEMPT_TIMEOUT = 60


# A list of object names which deserve hard delete and event tracking
OBJECT_NAMES = ['artifact', 'blob', 'instance', 'network', 'networkinterface']


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
