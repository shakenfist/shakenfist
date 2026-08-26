# PLAN: Queue performance and coalescing

## Status

In progress.

Reopened on 2026-08-25 with three further phases. The plan reached 8
of 8 while explicitly recording two things it had not proven and two
follow-ups it had not built; those are now phases 9, 10 and 11 rather
than issues nobody is scheduled to reach. Phases 1-8 are unchanged and
remain complete.

Steps 1-6 merged to `develop` as PR #3194 on 2026-05-26. Step 7
measured the result and decided against explicit fairness; the
measurement, the exclusions it rests on and the decision are in
[PLAN-queue-performance-phase-07-measure-and-decide.md](PLAN-queue-performance-phase-07-measure-and-decide.md)
and summarised under "What step 7 measured" below.

Step 8 ran a `PUSH-AUDIT.md` audit over everything the plan changed,
and found that **coalescing had never worked**. Steps 4 and 5 -- the
enqueue-side dedup and the worker-side fold -- joined
`cluster_operations` to `object_states` on two columns which could
never match: an undashed uuid against a dashed one, and an enum value
against an enum name. Both primitives therefore always returned
"nothing found", and the `coalesced sibling ops` event had fired zero
times in seven days on `sfcbr`. Fixed here as #3878, with thirteen tests
in `shakenfist/tests/test_mariadb_coalescing.py` which execute the
real statements against an in-memory sqlite database built from
`mariadb.py`'s own table definitions. That is enough to catch a join
which can never match, which is what #3878 was, and explicitly not
enough for the fold's `FOR UPDATE` half: the sqlite dialect emits
nothing at all for `FOR UPDATE`, so every one of those tests runs
uncontended. #3879 tracks the missing functional coverage that let
the defect sit unnoticed since 2026-05-26, and phase 9 addresses it.
The full audit, including the three headings which found nothing, is in
[PLAN-queue-performance-phase-08-push-audit.md](PLAN-queue-performance-phase-08-push-audit.md).

Review of that fix found a second defect it would have activated:
`network_ensure_mesh` was declared coalescible, but it does node-local
work and the fold's key is the network alone, so the network node's
survivor would have marked every *other* hypervisor's pending mesh op
complete without doing their work. It is no longer coalescible, and an
enqueue-time guard now enforces the invariant that made the
queue-blind fold SQL safe in the first place. #3884 tracks the
multi-column key that would let per-node tasks coalesce properly.

Two things this plan changed are deliberately still unproven:

* **Step 7's numbers characterise a cluster with coalescing inert.**
  They were gathered on `sfcbr` while steps 4 and 5 were doing
  nothing at all. That does not invalidate the conclusion -- if
  anything "batched dequeue alone was enough" is the stronger reading
  of it -- but the system measured is not the system now running.
* **The fold's cost was never measured.**
  `claim_coalescible_siblings` is recorded in `baseoperation.py` as
  costing ~200 ms under load, and it now also takes `FOR UPDATE`
  locks on `object_states` rows and issues an UPDATE against a hot
  table. Both are re-measured in phase 9, alongside the functional
  coverage.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Visibility | (in PR #3194) | Complete |
| 2. Unified batched dequeue | (in PR #3194) | Complete |
| 3. Coalescible-task metadata | (in PR #3194) | Complete |
| 4. Worker-side dedup | (in PR #3194) | Complete |
| 5. Enqueue-side dedup | (in PR #3194) | Complete |
| 6. Caller-site audit | (in PR #3194) | Complete |
| 7. Re-measure and decide on fairness | [PLAN-queue-performance-phase-07-measure-and-decide.md](PLAN-queue-performance-phase-07-measure-and-decide.md) | Complete |
| 8. Push audit | [PLAN-queue-performance-phase-08-push-audit.md](PLAN-queue-performance-phase-08-push-audit.md) | Complete |
| 9. Prove coalescing works | [PLAN-queue-performance-phase-09-prove-coalescing.md](PLAN-queue-performance-phase-09-prove-coalescing.md) | In progress |
| 10. The 15 second dependency wait | (not yet planned) | Not started |
| 11. Multi-column coalescing key | (not yet planned) | Not started |

## Problem

Functional CI on the `network-facade` branch surfaced a
cluster-wide latency tail: cluster operations -- especially
`network_apply_update_dnsmasq` on the elected network node --
spent >60 s queued before a worker picked them up. Six instance
starts on one network each enqueued one `update_dnsmasq` op; the
single-threaded `sf-net` worker serviced them strictly serially,
and each one paid the full state-machine round-trip
(`STATE_EXECUTING` write -> work -> `STATE_COMPLETE` write -> 100 ms
poll lag on the waiter side) even though the actual `dnsmasq`
restart is sub-second.

The pre-existing topology was already serialised; what changed
is that the network-facade refactor moved work that used to run
inline on the network node into the queue. Every change now pays
the queue+state-machine overhead, and the work backed up.

## Approach

Six discrete changes, one measurement step, and a closing
audit:

1. **Visibility**: carry `wait_seconds`, `defer_count` and
   `queue_name` on the per-op event the dispatcher emits. The
   dispatcher is the only place in the pipeline that observes both
   `op.created_at` (insert time) and `start_time` (when the worker
   is about to call `op.execute()`), so the per-op queue-wait
   latency lands directly in eventlog. As implemented, these fields
   ride on the existing end-of-op `'execution duration'` event
   rather than a separate `'started executing'` event at the pickup
   boundary: a second event doubles the eventlog cost on the
   dispatcher's critical path, which profiling identified as the
   largest per-op overhead this plan added. See
   `docs/operator_guide/networking/overview.md`.

2. **Unified batched dequeue**: replace `dequeue_work_item(qn)`
   and its direct/gRPC pair with `dequeue_work_items(queue_names,
   limit)`, served by a single MariaDB SELECT using `ORDER BY
   FIELD(queue_name, ...), scheduled_at`. Both `sf-net` and
   `sf-queues` use the new API; the singular method is removed
   (one way of doing the thing). The previous 10 sequential
   `Dequeue` gRPCs per idle poll become one.

3. **Coalescible-task metadata**: declare which (op_type, task)
   combinations are safe to fold. Subclasses set
   `coalescible_tasks` (frozenset) and `coalescible_target_column`
   on `BaseClusterOperation`; the schema module declares the same
   set under `COALESCIBLE_TASKS`. Metadata-only commit -- no
   behaviour changes.

4. **Worker-side dedup**: inside `BaseClusterOperation.execute`,
   two passes. (a) Within-job: drop duplicate coalescible tasks
   from `self.tasks`. (b) Cross-op: ask MariaDB (one transactional
   SQL statement) to fold every other pending op on the same
   target whose entire task list is one of our coalescible tasks
   -- their state transitions to `complete`, and when the
   dispatcher eventually surfaces their `work_queue` row the
   terminal-state branch drops it cleanly.

5. **Enqueue-side dedup**: at the top of
   `net_op.create_and_enqueue`, look up an existing pending
   coalescible op on the same target. If found, return that op's
   uuid instead of inserting a duplicate row. Dedup is skipped
   when the new enqueue carries `depends_on` or `runs_after`
   (those encode an ordering constraint reusing a sibling would
   erase). The lookup race is bounded -- two concurrent callers
   that both miss the lookup produce at most one duplicate row,
   which the worker-side fold (step 4) catches on dispatch.

6. **Caller-site audit**: sweep for fan-out patterns we can
   collapse before they hit `create_and_enqueue`. See the
   findings section below.

7. **Re-measure**: once steps 1-6 are deployed, the per-op
   wait distribution tells us whether the tail is gone or whether
   explicit fairness (bounded staleness, reserved-slot lottery) is
   still needed for lower-priority queues. Done -- see "What step 7
   measured" below,
   [PLAN-queue-performance-phase-07-measure-and-decide.md](PLAN-queue-performance-phase-07-measure-and-decide.md)
   for the method, and `tools/queue-wait-report.py` for the tool
   which produced the numbers.

8. **Push audit**: run `PUSH-AUDIT.md` over the accumulated diff
   of every step in this plan, rather than over the last step's
   diff alone. Because the work is already merged, the audit's
   baseline is the plan's own commit range and not
   `develop...HEAD`; the phase plan pins the exact range. Each
   finding is resolved, or declined in writing, before this plan
   is marked complete. If the audit finds nothing, that is
   recorded in one sentence.

## Phases 9 to 11

Added on 2026-08-25. Steps 1-8 answered the question the plan was
written to ask -- the wait tail is gone -- but left three things
behind, each of which is now a phase rather than an issue waiting
for someone to notice it.

9. **Prove coalescing works.** Step 7's numbers were gathered while
   coalescing was inert, and the fold's cost has never been
   measured. There is also still no functional coverage anywhere
   that coalescing matches a row on a running cluster (#3879),
   which is what let #3878 sit on `develop` for three months. This
   phase makes the fold's evidence durable enough to assert on,
   adds that assertion to the functional suite, instruments the
   fold's cost onto the event `tools/queue-wait-report.py` already
   reads, and re-measures on `sfcbr`. See
   [PLAN-queue-performance-phase-09-prove-coalescing.md](PLAN-queue-performance-phase-09-prove-coalescing.md).

10. **The 15 second dependency wait.** Step 7 measured a p50 of
    15.78 s on the `user_waiting` queue and traced all of it to
    deferral: a dependency wait re-enqueues a flat 15 seconds into
    the future on the queues `sf-queues` drains, where `sf-net`
    instead backs off from 0.1 s to a 15 s cap. Restricted to
    operations which never deferred, the same p50 is 0.77 s. This
    is the largest user-visible latency in the whole sample and it
    has nothing to do with queue order, which is why step 7 excluded
    it from the fairness question and filed it as #3863 instead.
    Not yet planned.

11. **Multi-column coalescing key.** The fold keys on a single
    target column, which for `net_op` means "the same network". A
    per-node task therefore cannot be coalesced: two hypervisors'
    operations look identical to both dedup paths while doing
    different work on different hosts. That is why
    `network_ensure_mesh` was removed from `COALESCIBLE_TASKS` in
    phase 8, and why `network_apply_create_hypervisor` -- fanned out
    per instance during node startup, and idempotent -- has never
    been coalescible despite the step 6 audit identifying it.
    Generalising the key to a list of `(column, value)` pairs lets
    both back in. Filed as #3884. Not yet planned. Phase 9 comes
    first deliberately: generalising a primitive that was silently
    broken for three months, before anything proves it works on a
    running cluster, repeats the mistake.

## What step 7 measured

Read these numbers knowing what step 8 later found: **both windows
were captured with coalescing inert**, because the fold and the
enqueue-side dedup never matched a row until #3878 was fixed. Whatever
improvement is visible below was delivered by the batched dequeue
alone, and the fold's own cost does not appear in it at all.

Two windows, both through `tools/queue-wait-report.py`:

* **`sfcbr`**, 25h58m, 17,936 operations
  (2026-08-22T16:33Z to 2026-08-23T18:30Z). Production steady state.
* **Cluster CI**, 33 minutes, 1,248 operations (merge-queue run
  32597511463, all five cluster nodes' journals). The site where the
  original >60 s waits were seen.

### The tail this plan set out to remove is gone

`net_op` -- the family containing `network_apply_update_dnsmasq` --
against a starting point of over 60 seconds:

| Window | n | p50 | p90 | p99 | max |
|--------|---|-----|-----|-----|-----|
| `sfcbr`, 26h | 6310 | 0.78 | 1.81 | 7.77 | 27.60 |
| CI, 33m | 313 | 0.56 | 1.72 | 2.19 | 23.96 |

A p90 of 1.8 s is the dispatcher's idle poll cap
(`IDLE_POLL_MAX_SECONDS = 2.0`), so on both clusters the median
operation now waits roughly one poll interval and nothing else.

### Explicit fairness is not needed

Three tails in the `sfcbr` window sit above that floor. None of them
is a lower-priority queue being starved by a higher-priority one, and
each was excluded on evidence rather than on argument:

* **`user_waiting`, p50 15.78 s** (`node_inst_netdesc_op`). Entirely
  deferral: restricted to operations which never deferred, the same
  p50 is 0.77 s, and 962 of 1013 samples had deferred at least once.
  On the queues `sf-queues` drains, a dependency wait re-enqueues an
  operation a flat 15 seconds into the future (`sf-net` instead backs
  off from 0.1 s to a 15 s cap, so this cost is specific to the
  dispatcher rather than general). This is the largest user-visible
  latency in the whole sample and it has nothing to do with queue
  order. Filed as #3863.
* **`background_high_io`, p90 403 s** (`node_blob_op`), with a defer
  count of zero, which is the shape starvation would have. It is not:
  during each of the eight worst waits, the *same queue* executed
  between 1,281 and 1,334 seconds of its own work, which is more than
  the wait itself (several workers run concurrently). The queue was
  saturated with its own blob transfers throughout, not held off by
  user-facing work, and not gated off by the disk-busy check.
* **`networknode`/`background`, p50 4.45 s**. All 103 samples are
  bursts of 20-40 operations arriving within seconds of each other.
  In the largest, the background lane ran 34.5 s of its own work in a
  29 s span while `networknode`/`user_facing` ran 2.9 s in total. The
  rising wait across a burst is position in that burst, not
  higher-priority work crowding in ahead of it.

So the `FIELD()` ordering's theoretical starvation risk
(`shakenfist/mariadb.py`) did not materialise in 26 hours of
production traffic which included exactly the bursty contention it
would show up in. Bounded-staleness ordering and reserved slots are
**not** being added. If the question is reopened, reopen it with a
measurement: the tool is committed and the exclusions above are what
any future claim of starvation has to survive.

### What the measurement cost us to build

The events could not be read back out of the database at all: an
operation is hard deleted 30 seconds after it completes and takes its
events' object references with it, so the numbers had to come from the
log echo instead. That gap is filed as #3864 and the operator
documentation, which claimed 30 day retention, is corrected.

## Audit findings (step 6)

| Site | Pattern | Resolution |
|------|---------|------------|
| `node_inst_netdesc_op._instance_start` | Loop over `net_desc`; per-interface call to `n.ensure_mesh()` and `n.update_dnsmasq()` | Track `reconciled_network_uuids` in a set; first interface on a network triggers reconciliation, subsequent interfaces on the same network skip. Per-interface work (state flip, floating-IP fan-out) stays inside the loop. **Fixed in this commit.** |
| `node_inst_op._instance_delete` | Loop over `instance_networks`; one `n.update_dnsmasq()` per network | Already deduplicates network_uuid before entering the loop. No change. |
| External API hot-plug (`external_api/instance.py`) | Single multi-task enqueue `[create_network_node, ensure_mesh]` | Not a fan-out -- one op per hot-plug call. No change. |
| `daemons/network/maintain.py` | Per-network reconciliation enqueues during a 30 s pass | Bounded by per-network in-flight gate (`has_pending_cluster_operation_target`). After step 5, parallel maintainer passes on the same node coalesce; cross-node dedup deliberately disabled because mesh is per-hypervisor. No change. |
| `daemons/queues/startup_tasks.py` | Per-network sequential `n.create_on_hypervisor()` + `n.ensure_mesh()` waits during node startup | `create_on_hypervisor` enqueues `node_net_op.network_apply_create_hypervisor`, which is **not** currently coalescible. Multiple instances on the same network on the same node fan out at startup. See the follow-up below. |

## Follow-ups not landed here

* **`NodeNetOp.network_apply_create_hypervisor` coalescing**.
  This task is idempotent (`util_concurrency.create_vxlan_interface`
  is "create if missing") and is enqueued per-instance during
  startup -- so a node restoring N instances on the same network
  enqueues N node_net_ops where one would do. Marking it
  coalescible needs the find/claim primitives to filter on both
  `node_uuid` **and** `network_uuid` (same network on the same
  node), which the current `target_column` parameter does not
  support. Generalising to a list of `(column, value)` pairs is
  straightforward; deferred so step 5's CI run gives us
  measurable baseline numbers first. Now scheduled as phase 11,
  behind phase 9, and tracked as #3884.

* **Explicit fairness for low-priority queues**. The dequeue
  query honours strict priority order via `FIELD()`; lower
  priorities only spill in when higher ones yield fewer rows
  than `limit`. Sustained heavy load on `user_facing` could in
  principle starve `background`. The CI signal will tell us
  whether to add bounded-staleness ordering
  (`ORDER BY CASE WHEN NOW() - created_at > N THEN top
  ELSE priority END, ...`) or a reserved-slot mechanism.
