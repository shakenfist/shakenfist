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
2. **Per-instance vCPU limit** -- the request must fit libvirt's
   per-domain vCPU maximum on that node.
3. **CPU admission** -- allocated vCPUs (including this request)
   must stay under `schedulable threads x CPU_OVERCOMMIT_RATIO`.
   See [CPU overcommit](#cpu-overcommit).
4. **RAM admission** -- the node must retain its published memory
   reservation after placement, and KSM overcommit must stay
   under `RAM_OVERCOMMIT_RATIO`.
5. **Disk capacity** -- requested disk must fit while leaving the
   node's `NODE_DISK_RESERVATION_GB` free on the instances/blobs
   filesystems. The candidate node publishes its own reservation as
   the `disk_reservation_gb` metric, so admission honours that
   node's per-host value rather than the evaluator's own config.
6. **Affinity** -- surviving nodes are scored against the
   instance's affinity tags and only the highest-scoring group
   continues.
7. **Queue health** -- nodes with more than 20 waiting queue jobs
   are excluded; they are not keeping up.
8. **Disk bandwidth** -- nodes whose disks are saturated (busy
   more than 120% of wall time across spindles) are excluded.
9. **Load ordering and weighted selection** -- the survivors are
   ranked by load and a weighted-random choice spreads work
   across similar nodes. See below.

Stages 1 to 5 are **pre-filters**: they answer, from a metrics
snapshot up to a minute stale, whether a node probably can host the
instance. They are not the admission decision -- that is a separate,
atomic capacity claim made once a candidate is chosen (see [Admission
is a guarded capacity claim](#admission-is-a-guarded-capacity-claim)
below), so a node that survives every pre-filter here can still be
refused there if another concurrent create took the last slot first.
Stages 7 and 8 are **load shedding**: they answer whether a node is a
good idea right now. Affinity sits between the two deliberately. A
busy node is still a node the user asked for, so load shedding may
narrow the winning affinity group but never moves placement out of it
-- if queue health and disk bandwidth would eliminate every member of
that group, they are ignored and an audit event `schedule keeping
affinity despite transient load` is recorded. The pre-filters are
never overridden this way: a node that cannot fit the instance is not
scored for affinity in the first place. If load shedding eliminates
*all* candidates, the schedule still fails with a 507 as before.

Before this ordering, a momentary IO burst on the node an instance
was affine to silently placed it anywhere with headroom, and the
anti-affinity case could leave an instance on the one node it was
asked to avoid.

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

This is a pre-filter, and it is deliberately measurement-only: it
sizes a node from `cpu_total_instance_vcpus`, the resources daemon's
count of *running* libvirt domains, republished roughly once a
minute. That lags reality -- an instance still fetching its image has
no domain to measure yet -- which is exactly why it is not the
admission decision: its job is to order and prune the candidate list
cheaply, not to make the final call. RAM and disk pre-filters are
sized the same way, from published measurements alone. See [Admission
is a guarded capacity claim](#admission-is-a-guarded-capacity-claim)
for the allocation-denominated check that actually admits or refuses
a placement, and closes the burst window a measurement alone cannot.

## Admission is a guarded capacity claim

The pipeline above orders and prunes candidates from a metrics
snapshot; it is not what admits an instance. Once a candidate is
chosen, `Instance.place_instance()` makes one atomic claim against the
allocation-denominated counters in `scheduler_node_capacity` (and,
once the claims API exists, `namespace_claims`) -- the same database
transaction that writes the `placement` attribute and the node's
`instance_location` reference row. Two concurrent creates racing the
last slot on a node cannot therefore both be admitted, and RAM and
disk are protected the same way CPU is: all three dimensions are
checked against the allocation ledger, not just the pre-filters'
measurements above. See
[`docs/operator_guide/database.md`](database.md) for the RPCs and the
tables they draw down.

A refused candidate is not a failed create: the scheduler-driven
callers (the create path and the preflight redirect) walk to the next
candidate on a denial, so one node being momentarily full only costs
an extra round trip. Only once every candidate has refused does the
request fail, with a 507 whose detail names each refused candidate
and the dimension(s) it was refused on (`cpus`, `memory_mb`,
`disk_gb`, or the `demand` feedforward term described below) -- the
same audit detail the scheduler has always published, now sourced
from the guard that actually admitted or refused the placement rather
than a snapshot of it.

Ground-truth writers -- the cleaner's placement rewrites and the
queues daemon's startup reconciliation -- do not enforce the guard,
because they record where a libvirt domain already *is*: refusing to
record reality would just leave the counters wrong. A write that
pushes a node over its limit this way still updates every counter and
is recorded loudly in the instance's events, rather than silently
absorbed.

`/admin/resources` (`summarize_resources()`) publishes `cpu_committed`
sourced from these same counters rather than a separate walk,
alongside `cpu_committed_row_present`: a node the reconciler has not
yet sized reports `cpu_committed` as zero *and*
`cpu_committed_row_present` as false, which distinguishes a genuinely
idle node from one that is admitting unguarded.

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

Since scheduler-reservations phase 3 they do affect placement: each
successful admission adds `vcpus × SCHEDULER_DEMAND_PER_VCPU` to the
target node's `expected_demand` counter in the same transaction, and
the admission guard refuses a node whose `cpu_load_1 + expected_demand`
would exceed `SCHEDULER_TARGET_LOAD × cpu_schedulable` -- a denial on
this clause is reported as the `demand` dimension. The capacity
reconciler still owns the decay: it recomputes each node's
`expected_demand` from placement ages every five minutes, and also
publishes the matching `scheduler_capacity_node_expected_demand`
metric. A refusal on `demand` behaves like any other guard denial: the
caller walks to the next candidate. Setting `SCHEDULER_TARGET_LOAD` to
zero or below disables the demand clause entirely rather than refusing
every placement, which matters for a mid-upgrade caller whose request
carries an unset field. The defaults are provisional, pending an
analysis of accumulated cluster data, so expect them to change. There
is no reason to tune them yet.

Unlike the real dimensions, demand alone can never fail a create. The
term exists to spread correlated bursts across nodes, not to bound
capacity, so when a walk admits nowhere but at least one candidate was
refused *only* on `demand` (every real dimension had room), the caller
walks the candidates a second time with the demand clause waived and
the placement proceeds on real capacity. Both walks are visible in the
instance's audit events (`waiving demand guard`), and the waived
admission still accumulates its demand contribution so later enforced
admissions see it. Without this, a small or single-node cluster under
rapid create churn -- CI being the canonical case -- would refuse
creates indefinitely while sitting essentially idle.

## Diagnosing a placement decision

Every stage of the pipeline records an audit event on the
instance (and the candidate nodes), so `sf-client instance events`
tells the whole story:

- `schedule inputs` records what was asked for (vCPUs, memory,
  disk, affinity, namespace) and the age of the metrics snapshot.
- Each pre-filter stage emits `schedule at stage <name>` with the
  surviving candidates and a `dropped` map giving each excluded
  node's reason dict -- for the CPU pre-filter that includes the
  schedulable base used, whether it came from the `cpu_schedulable`
  field or the pre-reservation fallback, and the measured vCPU count
  compared against the hard maximum; for RAM it includes the
  reservation subtracted.
- `schedule have highest affinity` includes the winning score and
  a per-candidate `affinity_detail` breakdown of which neighbouring
  instances contributed what. `schedule keeping affinity despite
  transient load` follows it when load shedding was ignored to
  honour that group.
- `schedule have lowest cpu load` includes per-node `load_detail`:
  raw `cpu_load_1`, the denominator used, the normalised load and
  the bucket.
- `schedule final candidates` records the weighted ordering and
  each node's selection weight.
- Once a candidate is walked for admission, `schedule candidate
  refused by capacity guard` records the failing stage (`cluster`,
  `claim` or `node`) and dimension(s) from the guarded capacity
  claim itself -- distinct from, and later than, the pre-filters'
  `dropped` reasons above. `schedule failed, every candidate refused
  by capacity guard` follows if every candidate is refused.
- A schedule with no surviving pre-filter candidates raises an error
  recorded as `schedule has no candidates at stage <name>, aborting`
  -- the stage name plus the previous event's `dropped` map identify
  exactly which constraint eliminated the last node.

The admin resources API (`/admin/resources`, surfaced by
`get_cluster_resources()` in the client) reports per-node
`cpu_schedulable`, `memory_reserved_mb`, `cpu_available` and RAM
headroom using the same pre-filter arithmetic as the pipeline above.
It also breaks the CPU decision out into `cpu_hard_max`,
`cpu_measured` and `cpu_committed` -- see [Admission is a guarded
capacity claim](#admission-is-a-guarded-capacity-claim) for what
`cpu_committed` and its `cpu_committed_row_present` companion actually
mean.

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
