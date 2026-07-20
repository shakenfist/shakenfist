# Node resource health

Shaken Fist watches whether the storage a node depends on is actually
working, and takes an unhealthy node out of service automatically. This
is separate from the two older notions of node health:

- **`missing`** — the node has not checked in within
  `NODE_CHECKIN_MAXIMUM`. This is about the node being *reachable*.
- **`degraded`** — one of the node's daemons is self-reporting as not
  running. A degraded node is still a scheduling candidate.
- **`error`** — a resource the node depends on has failed (or the node
  has been missing for a long time). An errored node is **not** a
  scheduling candidate, and its blob replicas stop counting toward
  replication targets.

Node resource health drives that `error` state from the health of the
node's storage. It exists because of a real incident: a hypervisor lost
the NVMe drive backing its blob store, every access to
`/srv/shakenfist/blobs` returned `EIO`, and yet the scheduler kept
placing new instances there for hours because nothing it consulted
looked at the blob store.

## What is checked

Each object type declares the local storage paths it depends on to be
healthy. sf-resources works out which object types this node hosts from
its role flags and probes the union of their paths (de-duplicated, so a
shared path is probed once):

| Object type | Paths (under `STORAGE_PATH`) | Hosted on |
|-------------|------------------------------|-----------|
| Instance | `instances`, `image_cache`, `blobs` | hypervisor nodes |
| Blob | `blobs` | every node (any node can hold a replica) |
| Upload | `uploads` | every node (any node can receive an upload) |

So a hypervisor probes `instances`, `image_cache`, `blobs` and
`uploads`; a database-only node probes only `blobs` and `uploads`. Paths
are `STORAGE_PATH`-relative (default `/srv/shakenfist`).

Each path is checked two ways:

- **Cheap check, every cycle** (`NODE_HEALTH_CHECK_INTERVAL`, default
  60s): a `statvfs` of the path. A raised error (the store is gone) or a
  read-only mount (`ST_RDONLY`) is unhealthy. This bounds how quickly a
  fully-dead path is noticed.
- **Write heartbeat, less often** (`NODE_HEALTH_WRITE_INTERVAL`, default
  300s): a write-plus-`fsync` of a `_heartbeat` file in the path. This
  catches write-only failures a read cannot see (a directory can
  `statvfs` fine while writes fail), and doubles as a forensic
  "last seen live" timestamp on disk. The write interval is deliberately
  longer than the check interval so the heartbeat does not consume write
  endurance on flash unnecessarily.

## The NFS subtlety

sfcbr and similar deployments mount storage over NFS with the `hard`
option. **A `hard` NFS mount hangs indefinitely when the server dies**,
rather than returning an error. A `statvfs` or write on such a mount
blocks forever.

Because of that, every probe runs under a deadline
(`NODE_HEALTH_PROBE_TIMEOUT`, default 30s) on a separate thread, and **a
probe that times out is itself the unhealthy signal**. A path whose
previous probe is still outstanding is not probed again, so a hung mount
leaks at most one blocked thread per path.

This also means that for NFS-backed storage, the node-level probe — not
the instance-level qemu pause — is the primary detector. A qemu disk
error policy pauses a domain on `EIO`, but a hung `hard` mount produces
no `EIO`: the I/O just blocks. The node probe's timeout is what catches
it.

## What happens when a path fails

sf-resources moves the node to `error`. From that alone, by existing
mechanics:

- the scheduler stops placing new instances on the node (an errored node
  is not an "active" candidate); and
- the node's blob copies stop counting as replicas, so the replicator
  rebuilds them to `BLOB_REPLICATION_FACTOR` on other nodes.

The diagnosis is recorded as an audit event on the node, naming the
failed path, the object types that depend on it, and the failure detail
(for example `timeout` for a hung mount, or `readonly` for a read-only
remount). If metrics are enabled, the `node_resource_health` gauge on
the resources metrics port reads `0` for an unhealthy node and `1`
otherwise — a scrapeable signal you can alert on.

Then the cluster daemon, running on a surviving node, cascades the
failure, **gated on which object type was affected**:

- if instance storage failed, the node's instances are moved to
  `<state>-error` (terminal — not deleted; you can still snapshot them);
- if the blob store failed, the node's blob location records are dropped
  and re-replication is requested.

An `uploads`-only failure errors nothing and drops no replicas, but the
node still goes `error` and out of scheduling. This is why the blast
radius follows the dependency lists rather than a blanket "node is bad"
flag.

## Recovery

**A node's resource-health error never clears automatically.** A marginal
disk that flaps in and out of readiness would otherwise flap the node in
and out of scheduling, which is worse than staying out until a human
looks. Recovery is deliberately an operator action:

1. Fix the underlying storage (replace the disk, remount, restore the
   NFS server) and confirm the path is healthy.
2. Return the node to service:

   ```
   sf-ctl clear-node-error --node-name <node>
   ```

   (Run without `--node-name` to clear the local node.) This moves the
   node from `error` back to `created`. If the storage is still bad,
   sf-resources will re-error the node within one check interval, so a
   premature clear is safe — it simply will not stick.

Clearing the node error returns only the **node** to scheduling. Any
instances that were errored when the node failed stay in `<state>-error`
— snapshot or delete them as you see fit; they are not automatically
revived.

## Configuration

| Setting | Default | Purpose |
|---------|---------|---------|
| `NODE_HEALTH_CHECK_INTERVAL` | `60` | Seconds between health evaluations. Bounds detection latency for a fully-dead path. |
| `NODE_HEALTH_WRITE_INTERVAL` | `300` | Seconds between authoritative write heartbeats. Catches write-only failures; kept long to limit writes to flash. |
| `NODE_HEALTH_PROBE_TIMEOUT` | `30` | Deadline for a single probe. A probe that overruns is treated as unhealthy — this is how a hung `hard`-NFS mount is caught. |

The checked paths are `STORAGE_PATH`-relative subdirectories and are not
individually configurable; they follow the object types a node hosts.

## See also

- [Scheduler](scheduler.md) — why an errored node is not a placement
  candidate.
- The node state machine in the developer guide documents the full set
  of node states and transitions.
