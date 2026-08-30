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
| 9. Prove coalescing works | [PLAN-queue-performance-phase-09-prove-coalescing.md](PLAN-queue-performance-phase-09-prove-coalescing.md) | Complete |
| 10. Where the pre-execution time goes | [PLAN-queue-performance-phase-10-defer-latency.md](PLAN-queue-performance-phase-10-defer-latency.md) | Not started |
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
   reads, and re-measures on `sfcbr`. All of that has landed and the
   measurement is written up under "What step 9 measured" below: the
   fold is cheap (3.7 ms median, not the ~200 ms the code asserted)
   and it fires very rarely (7 matches in 1,335 folds over 42
   hours). #3879 is closed; the deterministic concurrency coverage
   the phase declined to build is #3948. The functional test has
   also been observed to fail with `COALESCIBLE_TASKS` emptied, on a
   real cluster (run 33219587241), so the assertion is known to be
   load bearing rather than assumed to be. See
   [PLAN-queue-performance-phase-09-prove-coalescing.md](PLAN-queue-performance-phase-09-prove-coalescing.md).

10. **Where the pre-execution time goes.** This phase began as
    "the 15 second dependency wait" (#3863): a dependency wait
    re-enqueued a flat 15 seconds into the future on the queues
    `sf-queues` drains, where `sf-net` instead backed off from
    0.1 s to a 15 s cap. Step 7 measured that as a 15.78 s p50 on
    the `user_waiting` lane against 0.77 s restricted to
    operations which never deferred.

    **That fix landed and the subject moved.** #3916
    (`dependency_defer_delay()` in
    `shakenfist/daemons/queues/workitem.py`) gave `sf-queues` the
    same ladder, derived statelessly from the persisted
    `defer_count`, and #3863 was closed. Counting the defer events
    over phase 9's window confirms the ladder fires exactly as
    designed -- 1,391 defers at 0.1 s decaying to 11 at 12.8 s --
    and that **a flat 15 second dependency defer now happens once
    in 42 hours**, from the one caller still taking `defer()`'s
    `delay=15.0` default.

    So the flat wait is measurably gone, and the open question is
    a different one. Phase 9 recorded that the lane's p99 is still
    17.18 s and that roughly 400 of 823 first deferrals sit at
    15-17 s -- which, now that a first deferral costs 0.1 s,
    cannot be the defer delay. `wait_seconds` is
    `start_time - created_at` and conflates queue-sit time with
    deferral without separating them. Phase 10 decomposes it. See
    [PLAN-queue-performance-phase-10-defer-latency.md](PLAN-queue-performance-phase-10-defer-latency.md).

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

## What step 9 measured

Step 7's numbers were captured while coalescing was inert. Step 9
instrumented the fold itself and re-measured on `sfcbr` once the
instrumented build was deployed, so for the first time these numbers
describe a cluster on which the fold actually matches rows.

One window, through the same `tools/queue-wait-report.py`:

* **`sfcbr`**, 41h56m, 26,229 operations
  (2026-08-27T13:03Z to 2026-08-29T07:00Z). Production steady state.
  147 of those operations predate the instrumented build and are
  excluded from the coalescing tables by the tool, which is the
  deploy boundary showing up in the data.

### The fold is cheap -- the ~200 ms estimate was wrong by 50x

`claim_coalescible_siblings`, over all 1,335 executions which reached
it:

| p50 | p90 | p99 | max |
|-----|-----|-----|-----|
| 3.7 ms | 5.2 ms | 88.6 ms | 149.5 ms |

The comment in `baseoperation.py` asserted "~200 ms under load" from a
CI-bundle profile, and used that figure to justify the
`dispatcher_batch_size == 1` guard. The measured median is 3.7 ms and
the single most expensive fold observed in nearly 42 hours did not
reach 200 ms. Both comments are corrected in place.

The estimate was not merely imprecise, it was measuring something
else: it was taken while #3878 was live, so every call it timed was a
query that could never match, and it inferred "under load" from a
CI bundle rather than from a cluster carrying real traffic. The guard it justifies is still
worth keeping -- skipping a query that cannot help is free -- but it
is a tidiness optimisation, not a latency defence, and nothing else
should be justified by that number.

### Coalescing works, and on this workload it almost never fires

Of 8,661 `net_op` executions, the outcome breakdown was:

| Outcome | n | Share |
|---------|---|-------|
| `not_cluster_wide` | 3,661 | 42% |
| `batch_size_one` | 3,343 | 39% |
| `ran` | 1,335 | 15% |
| `no_coalescible_tasks` | 322 | 4% |

Of the 1,335 folds which ran, **7 folded anything**, each folding
exactly one sibling: seven operations avoided in 41h56m. The seven are
spread across six separate hours on both days rather than clustered in
one burst, and all of them landed on the
`networknode / user_facing_high_io` class.

No other operation type coalesces at all: the remaining 17,421
instrumented operations recorded `type_not_coalescible`, which is the
expected shape today -- `NetOp` is the only type that declares a
coalescing key.

So the phase 8 fix was necessary and is confirmed working, but its
practical yield on this cluster is small. Three readings are
consistent with the data and this plan does not pretend to choose
between them without more evidence:

1. `sfcbr` genuinely does not generate concurrent duplicate network
   work very often, and the fold is correctly rare.
2. The two guards are too aggressive. 81% of `net_op` executions
   never reach the fold, and `batch_size_one` alone accounts for 39%
   -- a dispatcher that dequeued in slightly larger batches would
   expose more foldable pairs.
3. The single-column key is the limit, which is what #3884 already
   describes: the per-node tasks most likely to be duplicated are the
   ones the key cannot express.

Reading 3 is the one already funded as a phase. Reading 2 is new and
worth a measurement before anyone acts on it.

### Cross-check against the counters

The Prometheus counters agree with the log-derived numbers, which is
the point of taking both:

| Counter | 42h increase | Log-derived |
|---------|--------------|-------------|
| `database_claim_coalescible_siblings_total` | 1,336.5 | 1,335 |
| `database_find_existing_coalescible_op_total` | 2,364.9 | not derivable |

The 1.5 difference is `increase()` extrapolating across scrape
boundaries, not a discrepancy. The enqueue-side dedup runs about 1.8
times as often as the worker-side fold, which is expected -- it is
consulted on every enqueue, not only on dequeued batches.

**There is no counter for enqueue-side dedup _hits_.** We can see how
often `find_existing_coalescible_op` was asked and not how often it
found something, so the enqueue-side half of coalescing has no
equivalent of the `coalesce_folded` number above. That asymmetry is
recorded here rather than fixed, because the fix belongs with #3884's
work on the key.

### What this window says about phase 10, which is less than it looks

#3916 merged on 2026-08-27 at 10:11 and `sfcbr` was redeployed at
13:13, so this is the first window carrying the dependency-wait
back-off. The `user_waiting` lane reads:

| | p50 | p90 | p99 | max |
|---|-----|-----|-----|-----|
| all | 1.20 s | 7.28 s | 17.18 s | 42.83 s |
| never deferred | 0.86 s | 1.82 s | 2.30 s | 9.08 s |

against step 7's 15.78 s p50 for the same lane. **Do not read that as
a controlled before-and-after.** Two things get in the way. The
windows are different workloads a week apart, with no attempt to hold
load constant. And `wait_seconds` is `start_time - created_at`
(`baseoperation.py:162`) -- time since the operation was *created*,
not since its last deferral -- so it is not a direct reading of the
defer delay at all.

What the window does establish is narrower and still worth having:

* Sub-second waits now appear on operations with `defer_count == 1`,
  which a flat fifteen second delay could not produce. The back-off
  is live.
* 1,568 operations in the window still deferred at least once, and
  the lane's p99 is 17.18 s. Deferral has not stopped mattering.
* Roughly 400 of the 823 first deferrals sit at 15-17 s. That is
  **not** the transient-failure retry path -- `defer_with_backoff`
  uses a `(15, 30, 60)` schedule and emits `scheduling retry after
  transient failure`, which occurs **zero** times in this window. What
  those operations were waiting for has not been established.

So phase 10 is not "already fixed by #3916". Its subject moved, and
the remaining question -- what the 15-17 s population is waiting on --
is a different question from the one #3863 asked.

That re-scope has since been done. Counting the defer events directly
shows the ladder firing as designed and only one flat 15 s defer in
the whole window, so the 15-17 s population cannot be waiting on the
defer delay at all. Phase 10 is now scoped to decompose `wait_seconds`
into the intervals it conflates; see
[PLAN-queue-performance-phase-10-defer-latency.md](PLAN-queue-performance-phase-10-defer-latency.md).

### Method note

The invocation in `tools/queue-wait-report.py`'s docstring did not
work as written. Loki caps a query at 5000 lines: asking for more
fails outright, and asking for exactly 5000 silently returns the most
recent 5000 lines. A 24 hour window of this cluster is past that
ceiling, so the documented invocation returned a truncated stream
which looked complete -- the first pass at this measurement
undercounted the majority outcome threefold before that was caught.
The
docstring now shows a `query_range` loop that pages the window in
half-hour chunks, and points at `count_over_time` for totals. Anyone
repeating this measurement should check that no chunk comes back at
the ceiling.

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
