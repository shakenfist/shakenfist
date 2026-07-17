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
   `is_hypervisor` are candidates.
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
6. **Disk capacity** -- requested disk must fit while leaving
   `MINIMUM_FREE_DISK` GB free.
7. **Disk bandwidth** -- nodes whose disks are saturated (busy
   more than 120% of wall time across spindles) are excluded.
8. **Affinity** -- surviving nodes are scored against the
   instance's affinity tags and only the highest-scoring group
   continues.
9. **Load ordering and weighted selection** -- the survivors are
   ranked by load and a weighted-random choice spreads work
   across similar nodes. See below.

## System reservations

Some of a machine's capacity is never offered to instances. The
resources daemon computes reservations locally (it knows the
node's roles and CPU topology) and publishes the *schedulable*
remainder in `node_metrics`; the scheduler consumes the published
values rather than recomputing them.

Reservations are denominated in **physical cores, not threads**:

- Every hypervisor reserves `CPU_SYSTEM_RESERVATION` cores
  (default 1) for the operating system.
- A node carrying a cluster-wide infrastructure role -- it is the
  network node or a database node -- reserves
  `CPU_INFRA_ROLE_RESERVATION` further cores (default 1). This is
  one extra core in total, not one per role.
- Reserved cores convert to threads at `ceil(threads / cores)`
  threads per core, which errs conservative on hybrid parts where
  some cores have two threads and some have one.

RAM is treated symmetrically: every node holds back
`RAM_SYSTEM_RESERVATION` GB (default 2) and infra-role nodes hold
back `RAM_INFRA_ROLE_RESERVATION` GB more (default 4, sized for
the network and database daemons' working sets).

The published fields are `cpu_cores`, `cpu_threads`,
`cpu_cores_reserved`, `cpu_schedulable` (threads),
`cpu_cores_schedulable` and `memory_reserved_mb`. On Intel hybrid
CPUs the daemon also publishes `cpu_cores_performance` and
`cpu_cores_efficiency`; these are informational and nothing in
scheduling consumes them yet.

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
behaviour can be restored with `CPU_OVERCOMMIT_RATIO=16` and the
reservation variables set to zero.

Note that on a cluster already packed beyond the new cap, existing
instances are untouched but new schedules to full nodes are
refused until they drain.

## Configuration reference

| Variable | Default | Meaning |
|----------|---------|---------|
| `CPU_SYSTEM_RESERVATION` | 1 | Physical cores reserved for the OS on every hypervisor |
| `CPU_INFRA_ROLE_RESERVATION` | 1 | Additional cores reserved on network / database nodes |
| `RAM_SYSTEM_RESERVATION` | 2.0 | GB of RAM reserved for the OS on every node |
| `RAM_INFRA_ROLE_RESERVATION` | 4.0 | Additional GB reserved on network / database nodes |
| `CPU_OVERCOMMIT_RATIO` | 3.0 | vCPUs admitted per schedulable thread |
| `SCHEDULER_TARGET_LOAD` | 0.75 | Target sustained load per schedulable thread, used for selection weighting |
| `SCHEDULER_CACHE_TIMEOUT` | 5 | Seconds an sf-api worker caches its metrics view |

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
reservation scheme lack the new fields. For exactly those rows
the scheduler synthesises an approximate CPU reservation from the
node's role flags (assuming two threads per reserved core) so
that a not-yet-upgraded node doesn't look artificially large and
absorb bursts during the roll, and falls back to the config RAM
reservation for memory. Audit events mark these nodes with
`cpu_schedulable_from_fallback`. The window closes as each node's
resources daemon restarts and republishes.
