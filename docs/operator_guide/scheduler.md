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
   The node is charged whichever is larger of its measured running
   vCPUs and the `used_cpus` its capacity counters already record.
   See [CPU overcommit](#cpu-overcommit).
4. **RAM admission** -- the node must retain its published memory
   reservation after placement, KSM overcommit must stay
   under `RAM_OVERCOMMIT_RATIO`, and -- because both of those are
   measurements which lag placement -- the memory the node's capacity
   counters already record must leave room under the counters' own
   limit. See [Guest memory returned to the
   host](#guest-memory-returned-to-the-host) for how much of a guest's
   memory a node is actually charged.
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
   ordered by CPU load and committed RAM, best first, and a
   weighted-random shuffle spreads work across similar nodes. No
   node is dropped here. See below.

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

## Guest memory returned to the host

Instances are given a virtio balloon device with **free page
reporting** enabled. When a guest frees a page, its kernel tells the
balloon driver, and the host releases the backing memory
(`MADV_DONTNEED`) instead of holding it. Without this a guest's host
footprint is a high-water mark: memory a workload touched once stays
charged to the hypervisor for the life of the instance, and the only
way the host can reclaim it under pressure is to swap it out. On one
production node an idle CI runner was found holding 4.9 GB of its
pages in host swap while reporting 4.7 GB free inside the guest, on a
node that was 109% committed and still being placed on.

Things worth knowing about it:

- **This is not ballooning.** The balloon target is never moved, so
  nothing shrinks a guest against its will. Only pages the guest has
  already decided it does not want are returned.
- **Guest page cache is not returned**, only free pages. A guest that
  has read a lot of data keeps that cache, and keeps the host memory
  backing it.
- **It is negotiated.** A guest kernel older than 5.7 does not
  advertise `VIRTIO_BALLOON_F_REPORTING` and simply declines, behaving
  as it always has. The host needs QEMU 5.1 or newer and libvirt 6.9
  or newer, which every supported platform satisfies.
- **The guest can take the memory back at any time.** A returned page
  is re-faulted on next use. Freed memory is an opportunity, not a
  permanent reduction in the instance's demand.

### What this changes about scheduling

The two RAM admission checks are affected differently, and the
difference matters when you are debugging a memory pressure incident:

- The **measured** check -- the node must retain its published memory
  reservation after placement -- reads the `memory_available` metric,
  which the resources daemon takes from the host's own view of free
  memory. Returned pages show up there, so a node running
  reporting-capable guests will admit work it previously would not.
  That is the point of the feature, but remember the previous bullet:
  those guests can re-fault the pages afterwards.
- The **allocation** checks are unchanged. KSM overcommit is computed
  from `memory_total_instance_actual`, which is the balloon
  allocation, and the [capacity
  counters](#admission-is-a-guarded-capacity-claim) count requested
  memory. Neither moves because a guest behaved well, so nodes are not
  packed harder on that basis.

### Only new domains get it

A libvirt domain definition is persistent, and Shaken Fist renders the
domain template only when the hypervisor has no definition for the
instance. An instance that already exists therefore does **not** gain
free page reporting from a power off and power on, a hypervisor
reboot, or a redeploy that ships the new template -- it has to be
recreated. This is most visible on exactly the long-lived instances
that stand to gain the most. See
[Upgrades](upgrades.md#free-page-reporting-applies-to-new-instances).

Once an instance is recreated, the `rss kb` figure in its usage events
steps down, because the qemu process really is holding less memory.
It is not comparable with the same instance's historical values. The
`actual kb` figure in those events is the balloon allocation and does
not move, so anything billing on allocation is unaffected.

## Load-aware ordering

Candidate nodes that survive the hard filters are bucketed by
**load per schedulable thread** (`cpu_load_1 / cpu_schedulable`)
in coarse 0.25-wide bands, and the list is ordered lowest band
first. Normalising by size is what lets a cluster of differently
sized machines compare fairly: an idle 24-thread node and a
struggling 12-thread node no longer look equivalent just because
both have a load average under 1.0.

RAM commitment participates in the same banding: each node's
committed memory (the larger of its capacity counters'
`used_memory_mb` and its measured instance allocation) as a
fraction of its memory limit is quantised into the same 0.25-wide
bands, and a node ranks by whichever of its two bands is worse.
Without this, a node carrying RAM-heavy but CPU-idle instances
looks like the *best* candidate precisely because of the workload
that makes it dangerous, and attracts every large instance in a
burst until the capacity guard finally refuses it -- observed on a
production cluster as one node at 109% of physical RAM sustaining
swap and OOM kills while its peers sat a third full (issue 3636).
Because the committed fraction is read from the counters that
admission draws down, it moves with every placement rather than
with the metrics refresh, so even a burst against one frozen
metrics snapshot sees each placement land.

This stage **orders** the candidate list; it does not shorten it. A
band says a node looks busier right now, not that it cannot host the
instance -- every node reaching this stage has already passed every
pre-filter. Because admission is a guarded claim that can refuse the
node at the head of the list, a caller that runs out of candidates
fails a create the cluster had room for, so the busier nodes stay in
the list behind the preferred ones for the walk to fall through to.

The bands are deliberately coarse. The metrics snapshot can be up
to a minute stale, so a burst of instance creates is scheduled
against essentially frozen numbers; fine-grained ranking would
send the entire burst to whichever node looked best at the last
refresh. Coarse bands keep genuinely similar nodes interchangeable
so a burst spreads across them.

Within a band, ordering is a weighted shuffle rather than a uniform
one. A node's weight is its load headroom toward
`SCHEDULER_TARGET_LOAD` (default 0.75 per schedulable thread),
scaled by its uncommitted RAM fraction:

    weight = max(0.1, SCHEDULER_TARGET_LOAD x cpu_schedulable - cpu_load_1)
             x max(0.1, 1 - ram_committed_fraction)

A machine with twice the headroom draws roughly twice the share of
a burst. Every band is shuffled this way, not just the first choice
from the best one, because callers fall through to later candidates
when a placement fails.

### RAM overcommit

`RAM_OVERCOMMIT_RATIO` (default 3.0) bounds allocated guest memory
per unit of physical RAM, both in the RAM pre-filter and in the
capacity counters' memory limit. The default is **KSM-optimistic**:
it assumes most guest pages deduplicate, which holds for fleets of
many near-identical, mostly-idle guests and does not hold for
workloads that dirty most of their allocation with unique pages
(CI runs, databases, container hosts). On one production CI cluster
KSM recovered ~11.5 GB on a 64 GB node carrying ~96 GB of nominal
guest RAM -- nowhere near the deficit -- and the node took repeated
OOM kills of instance kvm processes. Low-dedup fleets should set
the ratio much closer to 1.0-1.25 via cluster config.

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

This is a pre-filter, not the admission decision, but it is sized
from both of the figures admission cares about. A node is charged
whichever is larger of `cpu_total_instance_vcpus` -- the resources
daemon's count of *running* libvirt domains, republished roughly once
a minute -- and `used_cpus` from that node's capacity counters. The
measurement alone lags reality, because an instance still fetching
its image has no domain to measure yet, so a node whose capacity is
fully claimed can measure as completely idle for minutes. Reading the
counters here means such a node leaves the candidate list at this
stage instead of surviving to be refused by the guard. The RAM
pre-filter reads the counters the same way -- a just-placed instance
that has not yet faulted its allocation in is invisible to
`memory_available` for even longer than it is to the vCPU count --
so only the disk pre-filter remains sized from published
measurements alone.

An instance being rescheduled is not charged for itself on the node
it is already placed on, and a node with no capacity row -- one
mid-upgrade, or one the reconciler declined to size -- is charged
nothing, because admission will let it through unguarded too.

See [Admission is a guarded capacity
claim](#admission-is-a-guarded-capacity-claim) for the check that
actually admits or refuses a placement, and closes the burst window
a pre-filter cannot.

## Admission is a guarded capacity claim

The pipeline above orders and prunes candidates from a metrics
snapshot (and, for CPU, the counters below); it is not what admits
an instance. Once a candidate is
chosen, `Instance.place_instance()` makes one atomic claim against the
allocation-denominated counters in `scheduler_node_capacity` and, if the
instance's namespace holds a capacity claim, `namespace_claims` (see
[Namespace capacity claims](#namespace-capacity-claims)) -- the same database
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
request fail, with a 507 reporting how many candidates refused it.
The per-candidate detail -- which node was refused, and on which
dimension(s) (`cpus`, `memory_mb`, `disk_gb`, or the `demand`
feedforward term described below) -- is attached to the instance's
`schedule failed, every candidate refused by capacity guard` audit
event rather than to the response body, so diagnosing a 507 means
reading the instance's events. It is the same audit detail the
scheduler has always published, now sourced from the guard that
actually admitted or refused the placement rather than a snapshot of
it.

Ground-truth writers -- the cleaner's placement rewrites and the
queues daemon's startup reconciliation -- do not enforce the guard,
because they record where a libvirt domain already *is*: refusing to
record reality would just leave the counters wrong. A write that
pushes a node over its limit this way still updates every counter and
is recorded loudly in the instance's events, rather than silently
absorbed.

Of those two, only the startup reconciliation repairs a missing
`instance_location` row: the cleaner's per-domain rewrite early-outs
when the placement attribute already names the right node, so an
instance whose attribute is correct but whose reference row (and so
capacity charge) is missing stays under-counted until the node's
queues daemon next restarts. No current write path can produce that
divergence -- the attribute and the row are written by one transaction
-- so if you ever see a persistent under-count of a node's committed
resources, the remedy is a restart of that node's Shaken Fist
services, and the interesting question is what deleted the row.

`/admin/resources` (`summarize_resources()`) publishes `cpu_committed`
sourced from these same counters rather than a separate walk,
alongside `cpu_committed_row_present`: a node the reconciler has not
yet sized reports `cpu_committed` as zero *and*
`cpu_committed_row_present` as false, which distinguishes a genuinely
idle node from one that is admitting unguarded. It also publishes the
capacity row's own `limit_cpus` as `cpu_limit`, so a reader can compare
it against the live-derived `cpu_hard_max` and see the two ledgers
disagree; a node with no capacity row reports `cpu_limit` as `None`
rather than falling back to `cpu_hard_max`.

## Namespace capacity claims

A **capacity claim** is a namespace's reservation of aggregate cluster
capacity: so many vCPUs, so much instance memory and so much instance
disk, held for the namespace against the rest of the cluster for as long
as the claim is active. It is a cluster-wide quantity, not a per-node
one -- a claim says nothing about *where* the namespace's instances land,
only how much of the cluster is set aside for them in total.

Claims are administered through the REST API at
`/auth/namespaces/<namespace>/claims`, admin-only, alongside the
namespace's keys and mapping rules. The request and response bodies are
published in the [OpenAPI specification](https://openapi.shakenfist.com);
`sf-client` has no claim verbs yet, so for now a claim is created with an
HTTP request rather than a command. A namespace holds at most one active
claim.

### Advisory this release: exceedances are recorded, not refused

**Creating a claim does not stop anybody -- including the claiming
namespace -- from exceeding it.** In this release claim ceilings are
*advisory*: a placement that would push a namespace past the limits it
claimed is **admitted**, and the fact that it went over is recorded. The
refusal arrives in a later release, once operators have had a release in
which to see real exceedances and calibrate their claims against them.

Two consequences worth being explicit about, because both surprise
people:

- A claim does **not** cap the claiming namespace. `used_*` on a claim
  can and will exceed `limit_*`.
- What a claim *does* do immediately is reserve capacity **from everybody
  else**. The claim's limits are added to
  `cluster_capacity.claimed_*`, and the guard that admits instances in
  namespaces *without* a claim only lets them use what active claims have
  not spoken for. So an oversized claim starves unclaimed namespaces
  today, even though it does not bind its own.

The record of an exceedance is an audit event on the *instance* whose
placement crossed the line:

```
placement admitted over namespace capacity claim
```

It is emitted at warning level and carries `node`, `namespace` and
`claim_dimensions` -- one entry per dimension that is over, giving the
claim's limit, what the claim held before this placement, and this
placement's own allocation. `sf-client instance events <instance>` is
where to find it, and a search of the cluster's logs for that message is
how to answer "is anything over its claim". It is deliberately distinct
from `placement recorded despite exceeding capacity guard`, which is a
different event about a different thing: that one says a ground-truth
writer was forced past a *node's* guard, this one says a placement was
charged to its *namespace's* claim and the claim is now over.

Because a create that exceeds a claim looks exactly like a create in a
namespace with no claim at all, the event is the only observable
difference between advisory mode working and advisory mode being absent.
If you are testing a claim, assert the event.

### What creating a claim does to existing usage

A namespace usually already has instances when its claim is created, and
that usage is already counted -- on the cluster's *unclaimed* side. So
creation is a migration as well as a reservation: in the same transaction
that adds the claim's limits to `cluster_capacity.claimed_*`, the
namespace's existing drawdown is seeded into the new claim's `used_*` and
subtracted from `cluster_capacity.unclaimed_used_*`. Deleting a claim
migrates it back, returning whatever the claim still held to the
unclaimed side.

Without that migration, a namespace with running instances could place
its whole claim a second time until the next reconcile pass -- five
minutes, starting from the moment an operator does the thing claims exist
for.

The same migration is why a claim is granted or refused against

    claimed + limit + GREATEST(0, unclaimed_used - migrated) <= total

per dimension, where `migrated` is the drawdown being moved onto the
claim. Reading it without that term -- "is there `total - claimed -
unclaimed_used` left?" -- makes claims look harder to get than they are:
a namespace is not counted against its own claim on the unclaimed side,
because the same statement is taking it off there.

### Growing, shrinking, expiring and deleting

Growing any dimension is a fresh admission decision against the same
guard, with the migration term at zero (a grow moves nothing -- the
namespace's usage is already on the claim's side of the ledger).
Shrinking is always allowed down to what the claim is currently using and
no further. One request may grow one dimension and shrink another.
Nothing ever grows a claim automatically.

Expiry is given as a duration in seconds, not as a timestamp, and is
applied against the cluster's clock. That is the only clock the expiry
sweep ever compares against, so accepting an absolute time would mean
evaluating it against a clock the client never saw. The sweep runs as
part of the capacity reconciler's five-minute pass.

Expiry is not cleanup. A claim stops covering placements the moment it
expires -- from then on its namespace's creates are charged to the
cluster's unclaimed side -- and the next reconcile pass drops its limits
out of `claimed_*` and folds its namespace's usage back into
`unclaimed_used_*`. But the row stays, holding no capacity and covering
nothing, until somebody deletes it, and it cannot be grown back to life:
an expired claim must be deleted and replaced. Expired claims are
included in the namespace's claim listing precisely because their
existence is what explains a namespace whose placements stopped being
charged to its claim.

Deletion is immediate and has no soft-delete step: the same transaction
that removes the row returns what it held to the cluster. A claim sitting
in a `deleted` state while its row still held capacity would be an
accounting lie for a whole cleaner delay -- capacity promised to a
namespace that no longer wanted it and refused to everybody else.
Deleting a namespace does eventually clean up after itself: the
namespace's own hard delete, a `CLEANER_DELAY` after it is deleted,
cascades to its claims and returns their capacity, because a claim
outliving its namespace would hold capacity nothing can ever release.
Deleting a namespace's claim first is the way to get that capacity back
promptly.

### The two states a claim carries

A claim publishes two states, and they are two different facts:

| Field | Values | Meaning |
|-------|--------|---------|
| `state` | `created`, `deleted` | Object existence, where every other Shaken Fist object publishes it |
| `coverage_state` | `active`, `expired` | Whether the claim still covers placements |

An expired claim reads as `state: created, coverage_state: expired`. A
deleted claim has no row at all.

### When a claim cannot be made

A claim request that the cluster declines is not a failure, and the
status code says which kind of no it was:

- **507** -- the cluster does not have the capacity to promise this
  claim. The message names each dimension that did not fit, with its
  limit, its current use and what was asked for. Nothing but releasing
  capacity will help.
- **503** -- retry. Either the reconciler has not built the
  `cluster_capacity` singleton yet (which is normal for the first few
  minutes of a cluster's life), or the claim was being changed
  concurrently and the optimistic retry gave up.
- **409** -- change the request. The namespace already holds an active
  claim, or the shrink was below what the claim is already using, or the
  claim has expired and must be replaced rather than updated.

A **503 from a read**, or from a delete, means the database could not be
reached, and is worth separating from the retryable 503 above: claim
reads deliberately fail rather than answering "no such claim", because
an operator (and the namespace delete cascade) would act on that absence
as though the capacity had already been returned. A delete which cannot
be completed answers **500** rather than a 200 saying capacity came
back, and leaves the claim in place for the next attempt. In both cases
the capacity is still held and the request can simply be repeated.

### Verifying claims on a cluster

`sf-client` has no claim verbs, so there is no quick interactive way to
confirm that the claim pathway works on a particular cluster.
`tools/exercise-namespace-claims.py` in the Shaken Fist source tree
does it instead: it walks the whole surface end to end -- request
validation, create, the duplicate and capacity refusals, reads and
cross-namespace non-disclosure, field-masked updates, drawdown against
a real instance, the below-usage shrink refusal, expiry and delete --
and reports a pass or fail count.

```bash
tools/exercise-namespace-claims.py                 # full run
tools/exercise-namespace-claims.py --no-instances  # API paths only
tools/exercise-namespace-claims.py --no-expiry     # skip the expiry wait
```

Three things to know before pointing it at a cluster:

- **It consumes real capacity.** The full run creates a network and an
  instance to draw a claim down against. Do not run it on a cluster
  that is already close to full, and prefer `--no-instances` if you
  only want to check the API.
- **It waits for the reconciler.** `coverage_state` is swept on the
  five-minute reconcile pass rather than computed when you read it, so
  the expiry checks sit for several minutes by design. `--no-expiry`
  skips them.
- **It cleans up after itself**, including after a failure or a
  Ctrl-C. It works only in a throwaway namespace it creates, and never
  touches anything it did not make. It needs cluster admin credentials.

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
| `SCHEDULER_DEMAND_PER_VCPU` | 0.6 | Anticipated load per vCPU of a freshly placed instance |
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
the admission guard refuses a node whose existing load is *already*
above its target:

```
cpu_load_1 + expected_demand <= SCHEDULER_TARGET_LOAD × cpu_schedulable
```

A denial on this clause is reported as the `demand` dimension. Because
its `used` is the sum of two terms that mean different things --
`cpu_load_1` is measured ground truth, while `expected_demand` is an
estimate that decays -- the refusal detail also reports each term under
its own key. When triaging a `schedule candidate refused by capacity
guard` event, a `used` dominated by `cpu_load_1` means the node really
was busy and the refusal was correct; one dominated by
`expected_demand` on an otherwise idle node means the estimator is
wrong (mis-tuned constants, or decay not keeping up) and should be
compared against the `scheduler_capacity_node_expected_demand`
prometheus gauge.

The placement asking is deliberately not part of that comparison. The
question the clause asks is whether the node is already over target,
not whether it would be afterwards, so a node with real allocation room
can never be refused however large the instance or however small the
node. The instance's own contribution is charged to `expected_demand`
by the same statement, so it counts against the *next* placement. That
is what makes this a spreader rather than a bound: what stops a node
accepting work it has no room for is the `cpus`, `memory_mb` and
`disk_gb` dimensions of the same guard, not this one.

It was not always so. Until scheduler-reservations phase 4a the clause
added the incoming placement's charge to the left-hand side while the
budget stayed denominated per schedulable thread, and
`SCHEDULER_DEMAND_PER_VCPU` was seeded at 2.5 -- a figure transcribed
from a measurement of allocated vCPUs per thread rather than of load
per vCPU. Together those meant a node needed at least 3.34 schedulable
threads before it could admit a 1-vCPU instance at zero load, and
fourteen before it could admit a 4-vCPU one, so on small nodes the
clause refused everything and the spreader never operated (issue
#3813). If you are running a version older than that fix and your
hypervisors have fewer than four schedulable threads, expect the
`waiving demand guard` events described below on every single create.

The capacity reconciler still owns the decay: it recomputes each node's
`expected_demand` from placement ages every five minutes, and also
publishes the matching `scheduler_capacity_node_expected_demand`
metric. A refusal on `demand` behaves like any other guard denial: the
caller walks to the next candidate. Setting `SCHEDULER_TARGET_LOAD` to
zero or below disables the demand clause entirely rather than refusing
every placement, which matters for a mid-upgrade caller whose request
carries an unset field.

`SCHEDULER_DEMAND_PER_VCPU`'s default of 0.6 is the burst-peak figure
measured on a CI-dominated cluster, where steady-state demand ran
0.12-0.35 load per allocated vCPU; the burst figure is the relevant one
because bursts are what the term exists to spread. In practice it sets
how many placements a quiet node absorbs before the scheduler starts
preferring its neighbours: at the defaults, a 12-thread node absorbs
about fifteen 1-vCPU instances' worth of anticipated load before it
reaches target. `SCHEDULER_DEMAND_DECAY_SECONDS` is still an unmeasured
provisional value.

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

Note the steady-state cost on a cluster that stays demand-saturated
(sustained churn against a small node count): because each waived
admission still adds demand, every create pays both walks -- twice the
admission RPCs and an extra pair of audit events -- until churn slows
enough for the reconciler's decay to catch up. That is the accepted
trade, since a second walk is cheaper than a failed create.

One more thing to know before reading those events: **`expected_demand`
is not credited back when an instance is deleted.** A placement's
contribution has usually decayed by then, so subtracting the original
figure would over-credit the node; the reconciler owns the decay
instead, and it runs every five minutes. Under rapid create/delete
churn a node therefore carries demand from instances that no longer
exist, and with the clause binding, that residue alone can put it over
target. Compare `scheduler_capacity_node_expected_demand` against the
node's live instance count to tell the two apart: demand well above
what the placed instances justify is residue waiting for the next
reconcile pass, not load.

Read `waiving demand guard` events accordingly.

* **Occasional ones** mean every candidate was over target. That is
  usually real saturation, but on a cluster doing rapid create/delete
  churn it can be the residue above rather than live load.
* **One on every create** means something is wrong with the sizing
  rather than with the cluster -- either the demand constants are
  mis-sized for this hardware, or you are running a version predating
  the #3813 fix, where they could not be satisfied at all.

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
  reservation subtracted, or the committed memory compared against
  the counters' limit.
- `schedule have highest affinity` includes the winning score and
  a per-candidate `affinity_detail` breakdown of which neighbouring
  instances contributed what. `schedule keeping affinity despite
  transient load` follows it when load shedding was ignored to
  honour that group.
- `schedule have lowest cpu load` includes per-node `load_detail`:
  raw `cpu_load_1`, the denominator used, the normalised load, the
  committed-RAM fraction and the bucket.
- `schedule final candidates` records the weighted ordering and
  each node's selection weight.
- Once a candidate is walked for admission, `schedule candidate
  refused by capacity guard` records the failing stage (`cluster`,
  `claim` or `node`) and dimension(s) from the guarded capacity
  claim itself -- distinct from, and later than, the pre-filters'
  `dropped` reasons above. `schedule failed, every candidate refused
  by capacity guard` follows if every candidate is refused.
- `placement admitted over namespace capacity claim` records that the
  admission succeeded but drew the namespace past the claim it holds,
  with the exceeded dimensions in `claim_dimensions`. See [Namespace
  capacity claims](#namespace-capacity-claims); in this release that is
  a warning, not a refusal.
- A schedule with no surviving pre-filter candidates raises an error
  recorded as `schedule has no candidates at stage <name>, aborting`
  -- the stage name plus the previous event's `dropped` map identify
  exactly which constraint eliminated the last node.

The admin resources API (`/admin/resources`, surfaced by
`get_cluster_resources()` in the client) reports per-node
`cpu_schedulable`, `memory_reserved_mb`, `cpu_available` and RAM
headroom using the same pre-filter arithmetic as the pipeline above.
It also breaks the CPU decision out into `cpu_hard_max`,
`cpu_measured`, `cpu_committed` and `cpu_limit` -- see [Admission is a
guarded capacity claim](#admission-is-a-guarded-capacity-claim) for what
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
