Instance power states
=====================

Shaken Fist version 0.2.1 introduced power states for instances. Before this, you could power on or off an instance, or pause it, but you couldn't tell what power state the instance was actually in. That was pretty confusing and was therefore treated as a bug.

The following power states are implemented:

* **on**: the instance is running
* **off**: the instance is not running
* **paused**: the instance is paused, either by an operator request or by the hypervisor because a disk I/O error occurred (see below)
* **crashed**: the hypervisor reports the domain as crashed while it is still active. Shaken Fist's domain configuration prevents this in practice (see below), so it is not seen.

There are additionally a set of "transition states" which are used to indicate that you have requested a change of state that might not yet have completed. These are:

* transition-to-on
* transition-to-off
* transition-to-paused

We're hoping to not have to implement a transition-to-crashed state, but you never know.

Detecting power changes made outside the API
---------------------------------------------

Not every power off goes through the API: a guest can power itself off from inside, or the qemu process backing it can die on its own. Shaken Fist's cleaner notices either of these within one or two of its passes, each about a minute apart (passes are deferred while the cluster is not stable). When it does, it records the instance's power state as `off`, sets the agent state to "not ready (instance powered off)", and adds a "detected poweroff" audit event, whose `extra` carries `reason` (libvirt's shutoff reason for the domain) and `previous_power_state` (the power state recorded just before).

The instance's own state does not change -- it is still `created`. A power on through the API recovers it normally.

The reason strings, and what each means:

* **unknown**: libvirt could not determine why the domain stopped
* **shutdown**: the guest shut itself down, for example a guest-initiated poweroff
* **destroyed**: the domain was destroyed, for example by the power off API. The cleaner does not normally emit a "detected poweroff" event for this case, because the API already recorded the instance as `off`
* **crashed**: the qemu process died unexpectedly, for example killed by the OOM killer or by a signal. See below: this is reported as power state `off`, not as the `crashed` power state
* **migrated**: the domain stopped here because it migrated elsewhere
* **saved**: the domain was saved (suspended to disk)
* **failed**: the domain failed to start
* **from snapshot**: the domain was started from a snapshot that left it shut off
* **daemon**: libvirtd shut the domain down

Crashed is not seen in practice
--------------------------------

The `crashed` power state above is written only for a domain libvirt reports as crashed while it is still running, and Shaken Fist's domain configuration prevents that: `on_crash` is `restart` in the domain template, and there is no panic device configured. So in practice this power state is not seen.

A qemu process which dies is instead recorded as power state `off`, with reason `crashed` (see above). It is not terminal: a power on through the API recovers it.

Instance files missing
-----------------------

If an instance's files are missing from its hypervisor while its domain is powered off, the cleaner moves it to an error state once, with error message "instance files missing".

Disk I/O errors
---------------

Instance disks are configured with a "stop" error policy: when the storage backing a disk fails (for example a dying NVMe device or an unreachable NFS mount), qemu pauses the instance instead of passing I/O errors through to the guest. Shaken Fist notices the pause reason and marks the instance as errored, recording per-disk error detail in the instance's error message and event log.

Note that this is deliberately permanent -- even a transient storage error pauses the guest and errors the instance, because an instance which has taken disk errors is not trustworthy. The errored instance is terminal (it cannot return to the created state), but it can still be snapshotted to salvage data. The paused domain is left in place as forensic state until the operator deletes the instance.
