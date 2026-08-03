# Scheduler

The scheduler decides which hypervisor a new instance lands on. It
runs in-process in each `sf-api` worker (there is no scheduler
daemon), consulting the `node_metrics` table that the resources
daemon refreshes roughly every 60 seconds and caching its view for
`SCHEDULER_CACHE_TIMEOUT` (default 5s). Placement is therefore
always made against a slightly stale snapshot; the ordering
behaviour described below is designed around that fact.

## The placement pipeline

A scheduling request walks an ordered set of stages. Hard filters
drop nodes that cannot host the instance; the survivors are then
ranked. Every stage emits an audit event against the instance, so
a placement decision can be reconstructed after the fact (see
[Diagnosing a placement decision](#diagnosing-a-placement-decision)).

1. **Hypervisor check** -- only nodes reporting
   `is_hypervisor` are candidates. Nodes that are not "active"
   (those in `error` or `missing`) are excluded before this stage,
   so a node whose storage has failed stops receiving instances
   (see [Node resource health](node_health.md)).
2. **Queue health** -- nodes with more than 20 waiting queue jobs
   are excluded; they are not keeping up.
3. **Per-instance vCPU limit** -- the request must fit libvirt's
   per-domain vCPU maximum on that node.
4. **CPU admission** -- allocated vCPUs (including this request)
   must stay under `schedulable threads x CPU_OVERCOMMIT_RATIO`.
   See [CPU overcommit](#cpu-overcommit).
5. **RAM admission** -- the node must retain its published memory
   reservation after placement, and KSM overcommit must stay
   under `RAM_OVERCOMMIT_RATIO`.
6. **Disk capacity** -- requested disk must fit while leaving the
   node's `NODE_DISK_RESERVATION_GB` free on the instances/blobs
   filesystems. The candidate node publishes its own reservation as
   the `disk_reservation_gb` metric, so admission honours that
   node's per-host value rather than the evaluator's own config.
7. **Disk bandwidth** -- nodes whose disks are saturated (busy
   more than 120% of wall time across spindles) are excluded.
8. **Affinity** -- surviving nodes are scored against the
   instance's affinity tags and only the highest-scoring group
   continues.
9. **Load ordering and weighted selection** -- the survivors are
   ranked by load and a weighted-random choice spreads work
   across similar nodes. See below.

## System reservations

Some of a machine's capacity is never offered to instances. Each
node carries three **per-node** reservation values -- RAM, CPU and
disk -- set through that node's `/etc/sf/config`, which the deploy
templates per host. These are ordinary node-local config keys, not
cluster config: they are **never** set with `sf-ctl set-config`,
which only reaches cluster-wide values. The resources daemon reads
its own node's values, computes the *schedulable* remainder, and
publishes it in `node_metrics`; the scheduler consumes the
published values rather than recomputing them.

- **CPU** -- `NODE_CPU_RESERVATION_THREADS` (default 2) is a count
  of hardware **threads**, not physical cores, reserved for the
  operating system and host-level services. It is subtracted
  directly from the node's thread count; there is no cores-to-threads
  conversion in the arithmetic that scheduling uses (an informational
  `cpu_cores_reserved` field derives a core-equivalent for display,
  but nothing in admission consumes it).
- **RAM** -- `NODE_RAM_RESERVATION_GB` (default 2.0) is the amount
  of RAM, in GB, held back for the operating system and host-level
  services.
- **Disk** -- `NODE_DISK_RESERVATION_GB` (default 20.0) is the free
  disk, in GB, kept on the instances and blobs filesystems. It is
  published as the `disk_reservation_gb` metric and applied at both
  allocation points.

There is no separate reservation added on nodes carrying a
cluster-wide role (network node, database node). Instead, the
Ansible deploy computes a per-host *default* for each of the three
values that already accounts for a node's roles -- each host's own
10% of RAM floored at 2 GB, plus a 4 GB bump on network/database
nodes, for RAM; `(1 + 1 if network/database else 0) * 2` threads for
CPU; and a flat 20 GB for disk -- and only fills that default in
when the operator hasn't already set the value. An operator can
override any of the three per host in inventory (`host_vars` or
`group_vars`), which is the supported way to give a specific node
(for example one also running an unrelated sensor workload) extra
headroom.

The published fields are `cpu_cores`, `cpu_threads`,
`cpu_cores_reserved`, `cpu_schedulable` (threads),
`cpu_cores_schedulable`, `memory_reserved_mb` and
`disk_reservation_gb`. On Intel hybrid CPUs the daemon also
publishes `cpu_cores_performance` and `cpu_cores_efficiency`; these
are informational and nothing in scheduling consumes them yet.

## Load-aware ordering

Candidate nodes that survive the hard filters are bucketed by
**load per schedulable thread** (`cpu_load_1 / cpu_schedulable`)
in coarse 0.25-wide bands, and only the lowest band continues.
Normalising by size is what lets a cluster of differently sized
machines compare fairly: an idle 24-thread node and a struggling
12-thread node no longer look equivalent just because both have a
load average under 1.0.

The bands are deliberately coarse. The metrics snapshot can be up
to a minute stale, so a burst of instance creates is scheduled
against essentially frozen numbers; fine-grained ranking would
send the entire burst to whichever node looked best at the last
refresh. Coarse bands keep genuinely similar nodes interchangeable
so a burst spreads across them.

Within the winning band, selection is a weighted shuffle rather
than a uniform one. A node's weight is its load headroom toward
`SCHEDULER_TARGET_LOAD` (default 0.75 per schedulable thread):

    weight = max(0.1, SCHEDULER_TARGET_LOAD x cpu_schedulable - cpu_load_1)

A machine with twice the headroom draws roughly twice the share of
a burst. The whole candidate list is weighted-shuffled (not just
the first choice), because callers fall through to later candidates
when a placement fails.

## CPU overcommit

`CPU_OVERCOMMIT_RATIO` is how many vCPUs may be admitted per
schedulable thread (logical CPU). The default is 3.0, measured on
a CI-dominated cluster where busy hypervisors sustained 2.3-3.0
allocated vCPUs per thread with RAM as the binding constraint.

The historic default of 16 dated back to assumptions about large
numbers of mostly-idle instances, and in practice never rejected a
node -- RAM always bound first. If your workload matches that older
assumption (many small, mostly-idle instances), the historic
behaviour can be restored with `CPU_OVERCOMMIT_RATIO=16` and
`NODE_CPU_RESERVATION_THREADS` / `NODE_RAM_RESERVATION_GB` set to
zero per node.

Note that on a cluster already packed beyond the new cap, existing
instances are untouched but new schedules to full nodes are
refused until they drain.

## Configuration reference

Except for `CPU_OVERCOMMIT_RATIO`, `RAM_OVERCOMMIT_RATIO`,
`SCHEDULER_TARGET_LOAD`, `SCHEDULER_CACHE_TIMEOUT` and the two
`SCHEDULER_DEMAND_*` settings (cluster-wide, set with
`sf-ctl set-config`), the reservation variables below are **per-node**
and set through each node's `/etc/sf/config`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `NODE_RAM_RESERVATION_GB` | 2.0 | GB of RAM reserved per node for the OS and host services |
| `NODE_CPU_RESERVATION_THREADS` | 2 | Hardware threads reserved per node |
| `NODE_DISK_RESERVATION_GB` | 20.0 | GB of free disk kept per node on the instances/blobs filesystems |
| `CPU_OVERCOMMIT_RATIO` | 3.0 | vCPUs admitted per schedulable thread |
| `SCHEDULER_TARGET_LOAD` | 0.75 | Target sustained load per schedulable thread, used for selection weighting |
| `SCHEDULER_CACHE_TIMEOUT` | 5 | Seconds an sf-api worker caches its metrics view |
| `SCHEDULER_DEMAND_PER_VCPU` | 2.5 | Anticipated load per vCPU of a freshly placed instance (provisional) |
| `SCHEDULER_DEMAND_DECAY_SECONDS` | 600 | Seconds over which that anticipated load decays to zero (provisional) |

### Expected demand

The two `SCHEDULER_DEMAND_*` settings describe how much load a
*just-placed* instance is assumed to be about to generate, before that
load shows up in the node's measured `cpu_load_*` metrics. A placement
starts at `vcpus × SCHEDULER_DEMAND_PER_VCPU` of anticipated load and
decays linearly to zero over `SCHEDULER_DEMAND_DECAY_SECONDS` of
instance age. The purpose is to stop a burst of placements all choosing
the same node because none of them have started doing any work yet.

In this release they only shape the `expected_demand` column the
capacity reconciler writes to `scheduler_node_capacity`, and the
matching `scheduler_capacity_node_expected_demand` metric — **they do
not affect placement**. The defaults are also provisional, pending an
analysis of accumulated cluster data, so expect them to change. There is
no reason to tune them yet.

## Diagnosing a placement decision

Every stage of the pipeline records an audit event on the
instance (and the candidate nodes), so `sf-client instance events`
tells the whole story:

- `schedule inputs` records what was asked for (vCPUs, memory,
  disk, affinity, namespace) and the age of the metrics snapshot.
- Each filter stage emits `schedule at stage <name>` with the
  surviving candidates and a `dropped` map giving each excluded
  node's reason dict -- for CPU admission that includes the
  schedulable base used and whether it came from the
  `cpu_schedulable` field or the pre-reservation fallback; for RAM
  it includes the reservation subtracted.
- `schedule have lowest cpu load` includes per-node `load_detail`:
  raw `cpu_load_1`, the denominator used, the normalised load and
  the bucket.
- `schedule final candidates` records the weighted ordering and
  each node's selection weight.
- A schedule with no survivors raises an error recorded as
  `schedule has no candidates at stage <name>, aborting` -- the
  stage name plus the previous event's `dropped` map identify
  exactly which constraint eliminated the last node.

The admin resources API (`/admin/resources`, surfaced by
`get_cluster_resources()` in the client) reports per-node
`cpu_schedulable`, `memory_reserved_mb`, `cpu_available` and RAM
headroom using the same arithmetic as admission, so what it
reports as available is what the scheduler would actually admit.

## Mixed-version clusters

Metrics rows written by a resources daemon older than the
reservation scheme lack the new fields. For exactly those rows the
scheduler falls back to subtracting the evaluating node's own
`NODE_CPU_RESERVATION_THREADS` (there is no infra-role bump in this
fallback -- it cannot know a remote node's per-host override) so
that a not-yet-upgraded node doesn't look artificially large and
absorb bursts during the roll. RAM and disk fall back the same way,
to `NODE_RAM_RESERVATION_GB` and `NODE_DISK_RESERVATION_GB`
respectively. Audit events mark these nodes with
`cpu_schedulable_from_fallback`. The window closes as each node's
resources daemon restarts and republishes.
