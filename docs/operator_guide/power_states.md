Instance power states
=====================

Shaken Fist version 0.2.1 introduced power states for instances. Before this, you could power on or off an instance, or pause it, but you couldn't tell what power state the instance was actually in. That was pretty confusing and was therefore treated as a bug.

The following power states are implemented:

* **on**: the instance is running
* **off**: the instance is not running
* **paused**: the instance is paused, either by an operator request or by the hypervisor because a disk I/O error occurred (see below)
* **crashed**: the instance is crashed according to the hypervisor. Instances in this power state will also be in an instance state of "error".

There are additionally a set of "transition states" which are used to indicate that you have requested a change of state that might not yet have completed. These are:

* transition-to-on
* transition-to-off
* transition-to-paused

We're hoping to not have to implement a transition-to-crashed state, but you never know.

Disk I/O errors
---------------

Instance disks are configured with a "stop" error policy: when the storage backing a disk fails (for example a dying NVMe device or an unreachable NFS mount), qemu pauses the instance instead of passing I/O errors through to the guest. Shaken Fist notices the pause reason and marks the instance as errored, recording per-disk error detail in the instance's error message and event log.

Note that this is deliberately permanent -- even a transient storage error pauses the guest and errors the instance, because an instance which has taken disk errors is not trustworthy. The errored instance is terminal (it cannot return to the created state), but it can still be snapshotted to salvage data. The paused domain is left in place as forensic state until the operator deletes the instance.
