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

Autostart and a hypervisor reboot
----------------------------------

Shaken Fist uses libvirt's own autostart flag on each domain, not anything of its own, to decide which instances come back when a hypervisor reboots. Power on sets the flag. Power off clears it, whether the power off came through the API or was found by the cleaner (see "Detecting power changes made outside the API" above): an instance the database records as `off` has its autostart flag cleared either way. If a power off fails to stop the domain, its autostart flag is left set. The cleaner also clears the flag on an inactive domain it finds already recorded as `off` with the flag still set, which is how a domain that was powered off before this behaviour existed, or whose own clear on power off failed, gets fixed. That clear is logged as an "autostart cleared" audit event, with `extra={'reason': 'instance is powered off'}`, once per domain.

If the libvirt call to clear autostart on power off itself fails, the power off still completes and the instance is still recorded as `off`; the failure is instead recorded as an "instance autostart configuration error" audit event, and the cleaner's own check retries the clear on a later pass.

This reconciliation only runs one way: the cleaner clears a stale flag on a powered off domain, but it never sets the flag on a running domain which lacks it. The result is that after a hypervisor reboots, none of the instances the database last recorded as off come back, and every instance which was powered on through Shaken Fist and not powered off since does.

This depends on the distribution's own `libvirt-guests` service, which Shaken Fist does not configure. On Debian 13 and Ubuntu 24.04 it is enabled by default, with `ON_BOOT=ignore` and `ON_SHUTDOWN=shutdown`; Ubuntu additionally sets `PARALLEL_SHUTDOWN=10` and `SHUTDOWN_TIMEOUT=120`. With these defaults `libvirt-guests` asks every running guest to shut down cleanly when the host shuts down, and starts nothing itself at boot -- the autostart flag alone decides what comes back. An operator who changes `ON_BOOT` to `start` would have `libvirt-guests` restart the guests that were running at shutdown itself, which the autostart flag already does. An operator who changes `ON_SHUTDOWN` to `suspend` would have guests saved to a managed save image at shutdown instead of shut down, which Shaken Fist does not currently handle.

Every Shaken Fist daemon's systemd unit also orders itself after `libvirt-guests.service`. systemd stops units in the reverse of their start order, so this guarantees that every Shaken Fist daemon, including the cleaner, has stopped before `libvirt-guests` shuts guests down at host shutdown. Without that ordering, the cleaner could see guests going down for the reboot, record each as a detected power off, and clear its autostart flag in response -- turning a reboot into every instance on that hypervisor coming back powered off.

`sf-queues` does not restart or power on any instance when it starts. A restart of `sf-queues`, or of `libvirtd`, leaves running domains running regardless. It is only a hypervisor reboot, which stops every domain, where the autostart flag decides what starts again.

Failed power on and power off
------------------------------

A power on retries starting the domain several times, and a power off's own `destroy()` call can itself fail. When every power on attempt fails, or a power off's `destroy()` fails while the domain is still running, the API answers an error rather than its usual success response -- see [what each power operation answers](/developer_guide/api_reference/instances/#what-each-power-operation-answers) for the status codes. Either way, the instance's recorded power state is updated to match what the domain actually is at that point, not the state that was requested: a failed power on does not leave an earlier `on` behind, and a failed power off does not record `off` while the domain is still running.

These failures add audit events: `power on failed` and `power off failed`, each carrying the error; and, for a power off specifically, a `poweroff failed` event on the instance with the error as `message`. If even reading the domain's own state back fails, no power state is written at all, and the instance instead records `failed to determine instance power state after failed power on` or `... after failed power off` (in place of `poweroff failed`) -- the cleaner settles the instance's recorded state on a later pass.

Pausing or unpausing a powered off instance is refused (409), the same as rebooting one. Pause and unpause are otherwise idempotent: pausing an already paused instance, or unpausing an already running one, succeeds without calling libvirt again.
