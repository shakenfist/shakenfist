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
| 10. Where the pre-execution time goes | [PLAN-queue-performance-phase-10-defer-latency.md](PLAN-queue-performance-phase-10-defer-latency.md) | Complete |
| 11. Multi-column coalescing key | [PLAN-queue-performance-phase-11-multi-column-key.md](PLAN-queue-performance-phase-11-multi-column-key.md) | In progress |

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

    **That fix landed, and phase 9's residual turned out to be the
    old behaviour rather than a new mystery.** #3916
    (`dependency_defer_delay()` in
    `shakenfist/daemons/queues/workitem.py`) gave `sf-queues` the
    same ladder, derived statelessly from the persisted
    `defer_count`, and #3863 was closed. Over a wholly post-fix
    window the ladder fires exactly as designed -- 1,391 defers at
    0.1 s decaying to 11 at 12.8 s, summing to the unfiltered total
    exactly -- and a flat 15 second dependency defer happens once
    in 42 hours. That one event is the ladder reaching its own
    `MAX_DEFER_DELAY` cap at `defer_count >= 8`, not a caller
    taking `defer()`'s `delay=15.0` default; all 32 such events in
    the retained span come from `node_inst_netdesc_op`, and
    `node_blob_op` did not defer once.

    Phase 9's window was not wholly post-fix, which is what its
    unexplained population was. The pre-#3916 code emitted its flat
    wait as `Execution deferred for 15 seconds` -- the integer form,
    because the call site passed no delay and took an `int` default
    -- while the ladder emits floats. Counting the two forms
    separately over phase 9's window gives **411 integer-form
    events**, against 1 float-form; and the integer form falls to
    **zero from 2026-08-28 onwards** and stays there. So the
    "roughly 400 of 823 first deferrals at 15-17 s" phase 9 could
    not explain were #3863 itself, still in the sample. See
    "What step 10 measured" below, which reconstructs the window
    and withdraws the claim.

    That resolves the question this phase inherited, and leaves a
    different one standing. `wait_seconds` is
    `start_time - created_at` and conflates queue-sit time with
    deferral without separating them, and once the pre-fix
    population is excluded the deep tail is not deferral at all --
    over 90% of the operations waiting 15 s or more never deferred
    once. Phase 10 decomposes the wait and characterises that tail.
    See
    [PLAN-queue-performance-phase-10-defer-latency.md](PLAN-queue-performance-phase-10-defer-latency.md).

11. **Multi-column coalescing key.** The fold keys on a single
    target column, which for `net_op` means "the same network". A
    per-node task therefore cannot be coalesced: two hypervisors'
    operations look identical to both dedup paths while doing
    different work on different hosts. That is why
    `network_ensure_mesh` was removed from `COALESCIBLE_TASKS` in
    phase 8. Generalising the key to a list of `(column, value)`
    pairs lets it back in. Filed as #3884. Phase 9 came first
    deliberately: generalising a primitive that was silently broken
    for three months, before anything proved it worked on a running
    cluster, would have repeated the mistake. See
    [PLAN-queue-performance-phase-11-multi-column-key.md](PLAN-queue-performance-phase-11-multi-column-key.md).

    Planning that phase corrected two things this section used to
    say. First, there are **three** guards holding the single-column
    key safe, not the one #3884 names: the enqueue-time
    `InvalidCoalescibleEnqueue` check, the fold's own
    `queue_is_cluster_wide` skip in
    `BaseClusterOperation.execute`, and the task's absence from
    `COALESCIBLE_TASKS`. All three have to move together or the
    change measures as a no-op. Second,
    `network_apply_create_hypervisor` is **not** part of phase 11
    after all. Its model already carries `node_uuid`, so it looks
    like the cheap half, but it is a `NodeNetOp` drained by
    `sf-queues`, whose worker pool has no per-target routing key --
    the partitioned-worker invariant that makes a per-node fold safe
    for `sf-net` simply does not exist there. It is deferred to a
    successor issue on its own merits.

    **The code has landed; the measurement has not.** The key
    generalised to a tuple end to end, `network_ensure_mesh` is back
    in `COALESCIBLE_TASKS` with the key `(network_uuid, node_uuid)`,
    both guards became key-aware and family-aware, and unit and
    functional CI both verify the per-node fold against a real
    database rather than by inspection. Execution surfaced two
    corrections beyond what planning anticipated: a `None` key value
    has to bind `IS NULL` rather than being refused, or widening the
    key would have silently switched off the only coalescing the
    cluster already does; and naming `node_uuid` in the key is
    necessary but not sufficient, because the queue *family* decides
    which dispatcher drains the work, so both guards test the family
    as well. Both are recorded as decisions 8 and the family
    condition on decision 4 in the phase plan. What has not happened
    yet is step 11h, the `sfcbr` re-measurement -- see "What step 11
    measured" below.

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

## What step 10 measured

Step 9 reported a `user_waiting` p99 of 17.18 s and roughly 400 first
deferrals sitting at 15-17 s which it could not explain. Step 10 built
`tools/operation-timeline.py`, which joins the `Execution deferred`
events to the `execution duration` events on the operation uuid and
splits `wait_seconds` into the three intervals it conflates:

```
(created -> first dequeue) + (summed defer delay) + (residual)
```

Two windows on `sfcbr`, both through that tool:

* **Window A**, 2026-08-27T13:15Z to 2026-08-29T07:15Z, 42h00m,
  23,362 operations and 3,585 defer events (the defer fetch runs
  from 60 minutes before the window, so that an operation created
  before it still has its early legs). This is step 9's own window,
  so it is the one that can confirm or refute step 9.
* **Window B**, 2026-08-28T13:15Z to 2026-08-30T07:15Z, 42h00m,
  23,177 operations and 3,588 defer events. A trailing window ending
  at the measurement, retained independently in case A ages out.

The two **overlap by 18 hours** -- 42 hours of new traffic did not
exist yet -- so they are a retention hedge and a consistency check,
not two independent samples. Read them that way.

No chunk of either fetch came back at Loki's 5000 line ceiling, and
every stream was cross-checked against `count_over_time`: A matched
exactly on both selectors, B matched on defers and differed by 2 of
23,179 on executions, which is the window edge.

### Step 9's 15-17 s population was pre-#3916 traffic

**The claim is withdrawn, and it is corrected at source above.** After
#3916 there is no 15-17 s population on the `user_waiting` lane at all:

| | window A | window B |
|---|---|---|
| `user_waiting` operations | 3,821 | 3,815 |
| of those, deferred at least once | 1,360 | 1,347 |
| of those, `defer_count == 1` | 473 | 438 |
| `defer_count == 1` in the 15-17 s band | **0** | **0** |
| longest `defer_count == 1` wait | 3.79 s | 2.32 s |

Step 9's dataset was reconstructed rather than guessed at. Sweeping
the window boundaries over the retained stream and re-running
`tools/queue-wait-report.py` reproduces every number it published, at
one and only one window -- 2026-08-27T03:03Z to 2026-08-28T21:00Z,
exactly ten hours earlier than its label:

| | published by step 9 | reconstruction |
|---|---|---|
| operations in window | 26,229 | 26,225 |
| deferred at least once | 1,568 | 1,568 |
| `defer_count == 1` | 823 | 823 |
| `defer_count == 1` at 15-17 s | "roughly 400" | 359 (419 at >= 15 s) |
| `user_waiting` p50 / p90 / p99 / max | 1.20 / 7.28 / 17.18 / 42.83 | 1.20 / 7.30 / 17.19 / 42.83 |
| never deferred p50 / p90 / p99 / max | 0.86 / 1.82 / 2.30 / 9.08 | 0.86 / 1.82 / 2.30 / 9.08 |

Ten hours is this cluster's UTC offset. The log records carry a `ts`
which is local time with a `Z` suffix, so a window read off the
records reads ten hours later than the window Loki was actually asked
for. 10,108 of those 26,225 samples fall before the 13:13Z redeploy
that brought #3916 in, and 493 of the 823 first deferrals are pre-fix.
Under the flat fifteen second defer a first deferral lands in the
15-17 s band by construction, and 754 of the 956 pre-fix first
deferrals do. So the population step 9 could not explain was #3863
itself, sampled after its fix had shipped.

That reconstruction is confirmed independently by the message text
itself, which does not depend on getting the window boundaries right.
The pre-#3916 code passed no delay and took an `int` default, so it
emitted `Execution deferred for 15 seconds`; the ladder computes a
float and emits `15.0`. The two forms are separable in the log:

| | integer form (pre-fix) | float form (ladder cap) |
|---|---|---|
| in step 9's reconstructed window | **411** | 1 |
| 2026-08-26 | 772 | -- |
| 2026-08-27 | 632 | -- |
| 2026-08-28 onwards | **0** | -- |

411 integer-form events against step 9's "roughly 400" unexplained
first deferrals, falling to zero the day after the redeploy and
staying there. A search for the float form alone -- which is what the
phase 10 survey ran -- sees one event and concludes the flat wait is
gone, which is true of a post-fix window and false of step 9's.

### The two accounts of the deep tail were about different populations

Step 10b's own preview found 97 of 105 operations waiting >= 15 s had
never deferred at all, which reads as a flat contradiction of step 9.
It is not one. Both are real and they are disjoint populations, and
the split holds in both windows:

| operations waiting >= 15 s | window A | window B |
|---|---|---|
| total | 153 of 23,362 (0.7%) | 191 of 23,177 (0.8%) |
| never deferred | 141 (92%) | 173 (91%) |
| deferred | 12 | 18 |
| in the 15-17 s band | 11, **all** never deferred | 8, **all** never deferred |

Step 9 was looking only at `user_waiting`, which is the only lane on
which anything defers at all -- 1,360 of 1,360 deferred operations in
window A and 1,347 of 1,347 in window B are on it, because a
dependency wait is what defers and dependency-bearing work is enqueued
`user_waiting`. Step 10b was looking at the whole cluster, where the
deep tail is on the background lanes and never deferred once. Step 9's
error was the ten hour label, not the lane.

### Where the time goes when an operation does defer

Deferred operations only, since an operation which never deferred has
no intermediate event and therefore no decomposition:

| window A (n=1,360) | p50 | p90 | p99 | max | share of summed wait |
|---|-----|-----|-----|-----|---|
| total wait | 1.60 s | 3.17 s | 13.66 s | 42.83 s | 100.0% |
| created -> first dequeue | 0.43 s | 1.61 s | 2.03 s | 3.52 s | 30.2% |
| summed defer delay | 0.30 s | 1.50 s | 12.70 s | 40.50 s | 49.1% |
| residual | 0.19 s | 1.12 s | 3.79 s | 6.78 s | 20.7% |

| window B (n=1,347) | p50 | p90 | p99 | max | share of summed wait |
|---|-----|-----|-----|-----|---|
| total wait | 1.66 s | 3.23 s | 26.12 s | 293.80 s | 100.0% |
| created -> first dequeue | 0.44 s | 1.61 s | 1.99 s | 3.81 s | 24.7% |
| summed defer delay | 0.30 s | 1.50 s | 25.50 s | 280.50 s | 55.3% |
| residual | 0.24 s | 1.14 s | 5.16 s | 16.55 s | 20.0% |

The interval which holds the time depends entirely on how far up the
ladder the operation got, and the crossover is sharp (window A):

| `defer_count` | n | wait p50 | dequeue share | delay share | residual share |
|---|---|---|---|---|---|
| 1 | 473 | 1.06 s | 76.4% | 8.8% | 14.8% |
| 2 | 297 | 1.34 s | 59.1% | 21.1% | 19.8% |
| 3 | 205 | 1.51 s | 28.2% | 41.5% | 30.3% |
| 4 | 259 | 2.80 s | 11.9% | 57.7% | 30.5% |
| 5 | 82 | 3.42 s | 12.6% | 67.4% | 20.1% |
| 6 | 23 | 6.96 s | 8.9% | 75.3% | 15.7% |
| 7 | 10 | 13.45 s | 3.6% | 90.4% | 6.0% |
| 8 | 10 | 30.45 s | 2.9% | 84.1% | 12.9% |
| 9 | 1 | 42.83 s | 3.6% | 94.6% | 1.8% |

At one or two deferrals the wait is dominated by the initial queue sit
before anybody looked, which at a p50 of 0.43 s and a p90 of 1.61 s is
the dispatcher's idle poll interval (`IDLE_POLL_MAX_SECONDS = 2.0`)
and not contention. From three deferrals up the ladder itself is the
wait, which is the ladder working: an operation that has waited eight
times is waiting on something slow, and backing further off is the
intended response.

Every deferred operation over 15 s in either window is the same thing:
12 in A and 18 in B, all `node_inst_netdesc_op`, all `user_waiting`,
all `waiting_on` an `artifact_fetch_op`, all at `defer_count` 7 or
more. In window A they account for 308.2 s of ladder delay against
11.7 s of initial queue sit and 46.0 s of residual. An instance
waiting on an image fetch is the operation the ladder exists for, and
nothing here needs fixing.

### The ladder's rungs are served a little early, and it does not matter

The residual is redelivery slack: for each leg, how much longer it
actually took than the delay that was asked for.

| requested delay | legs | served p50 | slack p50 | served early |
|---|---|---|---|---|
| 0.1 s | 1,360 | 0.24 s | +0.14 s | 2 |
| 0.2 s | 887 | 0.24 s | +0.04 s | 3 |
| 0.4 s | 590 | 0.63 s | +0.23 s | 175 |
| 0.8 s | 385 | 1.43 s | +0.63 s | 142 |
| 1.6 s | 126 | 1.50 s | -0.10 s | 71 |
| 3.2 s | 44 | 3.17 s | -0.03 s | 25 |
| 6.4 s | 21 | 7.06 s | +0.66 s | 0 |
| 12.8 s | 11 | 13.87 s | +1.07 s | 3 |
| 15.0 s | 1 | 15.22 s | +0.22 s | 0 |

Redelivery is not exact in either direction. 421 legs in window A came
back **before** their delay had elapsed, by up to 0.19 s, and the
1.6 s and 3.2 s rungs are early at the median. That is a real fidelity
finding and it is the reason a residual can be negative -- 122
operations in window A and 81 in window B have one, which is
arithmetically correct rather than a join error.

It is not material to the question. The residual is 20% of the
deferred population's summed wait in both windows, its p50 is 0.19 s
and 0.24 s, and its p99 is 3.79 s and 5.16 s. Every rung is served
within a poll interval of what was asked for. Nobody's latency is
explained by redelivery drift.

### The deep tail is queue sit on the background lanes

That is the answer to the question phase 10 was re-scoped around: the
time is queue sit, not deferral, and it is not on `user_waiting`.
Ninety-two per cent of the >= 15 s population never deferred, and
splits into two families which are not the same phenomenon:

| never-deferred, wait >= 15 s | window A | window B |
|---|---|---|
| `node_blob_op`, `per-node`/`background_high_io` | 97 | 115 |
| `net_op`, `networknode`/`background` | 25 | 38 |
| `net_op`, `per-node (network)`/`background` | 6 | 13 |
| `net_macaddr_ip_op` / `net_op` / `net_iface_ip_op`, user-facing | 13 | 7 |
| summed wait | 45,697 s | 68,577 s |

**`node_blob_op` is a saturated pool**, which is what step 7 already
concluded and this window confirms directly. For the six worst waits
in window A, the same queue executed as much of its own work during
the wait as the wait itself lasted -- ratios of 1.03, 1.18, 1.33,
2.36, 2.68 and 2.69 of queue-seconds to wait-seconds, several workers
running concurrently. The queue was full of blob transfers, and
`background_high_io` is where blob transfers are meant to go.

**`net_op` on `networknode`/`background` is not.** The whole 1,800+ s
end of both windows is a single incident, visible in both because it
falls in the 18 hour overlap: 12 operations created within one second
at 2026-08-28T23:30:52Z, executed 31 minutes later between
2026-08-29T00:01:42Z and 00:02:01Z, serially, at intervals matching
their own 2.3-2.5 s execution times.

Nothing was stalled while they waited. The same `sf-net` dispatcher
executed 85 other `networknode` operations during the span -- 75 on
the higher priority lanes and **10 on the `background` lane itself**
-- for 52.3 s of work. So this is neither a saturated pool nor a
starved lane: the lane was served, and these 12 items were not.

Which leaves two mechanisms, and the retained events cannot tell them
apart. Either the rows were never claimed (`networknode`/`background`
is ninth of the ten queues in that dispatcher's `FIELD()` priority
order, so a batch which fills from higher queues never reaches it),
or they were claimed promptly and then held in `sf-net`'s in-memory
worker pool, which partitions by a stable hash of the target so one
slow operation holds every later operation for that target behind it.
The serial drain at execution-time intervals is what the second looks
like; it is not proof. See the next subsection for the timestamp that
would decide it.

This is 12 operations in 42 hours on a background lane, so the
user-visible cost is nil. But step 7 excluded `networknode`/
`background` on the grounds that its waits were burst position rather
than anything structural, and a 31 minute wait on a queue which was
concurrently being served is not that. The exclusion should not be
cited as if it still held without re-deriving it.

### What the join cannot say, and what would let it

**It cannot decompose a never-deferred wait at all** -- which is 92%
of the deep tail, so the paragraph above is a classification of the
tail rather than a measurement of it. The join's resolution comes
entirely from defer events, and an operation which never deferred
emits none. Between `created_at` and `start_time` there is no event,
so the whole wait is one bracket and both readings of it fit:

* the work item sat in `work_queue` because no dispatcher asked for
  its queue, or asked and had no free slot; or
* a dispatcher claimed it promptly and it then sat in an in-memory
  worker queue -- `sf-net` routes claimed items to a partitioned pool
  by a stable hash of the target, so a slow operation on one partition
  holds every later operation for that target behind it, and
  `wait_seconds` counts that as queue wait.

The 31 minute incident is exactly the case which needs telling apart,
and nothing in the retained events can. Two timestamps on the
`execution duration` event would settle it, and neither exists today:

* **`dequeued_at`** -- when the dispatcher's `dequeue_work_items` call
  returned the row. `dequeued_at - created_at` is time in the
  database queue; `start_time - dequeued_at` is time inside the
  daemon after it was claimed. This is the one that matters, and it
  separates "nobody asked for this queue" from "we had it and sat on
  it".
* **`deliveries`** -- how many times the work item has been handed out.
  `defer_count` counts deliberate deferrals only, so a redelivery
  after a crashed or lost worker is currently indistinguishable from
  a first delivery.

Both are cheap: the dispatcher has the first at claim time and the
work item row can carry the second. Neither should be added before
somebody wants to answer this question again, which is the same
sequencing argument decision 1 of the phase plan makes.

### What this does not establish

* **One cluster, and two windows which overlap by 18 hours.** The
  `net_op` incident above appears in both windows because it is the
  same incident. Nothing here is a second independent sample.
* **No before-and-after.** There is no post-#3916 measurement of a
  workload matched to a pre-#3916 one; what step 10 has is a mislabel
  corrected, not an A/B.
* **The tail's causes are classified, not measured.** Saturation for
  `node_blob_op` is inferred from queue-seconds against wait-seconds,
  which is the same argument step 7 made and has the same limits. The
  `networknode`/`background` reading rests on one incident.
* **No claim about dispatch, concurrency or pool sizing is made here,
  and none should be read into the numbers.** Decision 5 of the phase
  plan puts that out of scope deliberately; what the data says about
  it belongs in a successor issue with this evidence attached, and
  that issue is #3974.
* **Nothing about coalescing.** Step 9 measured it and those numbers
  stand untouched; the ten hour mislabel affects the `user_waiting`
  latency table only in as much as its window was wrong, and the
  coalescing counts were cross-checked against Prometheus.

### Method note

* **Take the window from Loki, never from the log records.** The
  records' `ts` is local time with a `Z` suffix, so on `sfcbr` it runs
  ten hours ahead of ingestion. That is the whole of step 9's error and
  it is invisible in the output unless you look for it.
  `tools/operation-timeline.py` measures the offset and prints it
  (35999.999 s to 36000.000 s across 26,947 events in window A); a
  *constant* offset cancels out of every interval, a varying one would
  not, which is why it is printed rather than silently corrected.
* `created_at` is `event_ts - seconds - wait_seconds`. Dropping the
  `seconds` term places creation after the operation's own defer
  events and yields negative intervals on long-running operations.
* Cross-check every fetch against `count_over_time`, which is a metric
  query and is not subject to the 5000 line ceiling. Both windows
  above were checked chunk by chunk as well.
* The phase plan's survey attributed window A's single 15.0 s defer
  event to `node_blob_op.py`'s bare `self.defer()`. That was wrong:
  all 33 `Execution deferred for 15.0 seconds` events between
  2026-08-27 and 2026-08-30 come from `node_inst_netdesc_op`, and are
  the back-off ladder reaching its own `MAX_DEFER_DELAY` cap after
  eight or more deferrals. `node_blob_op` did not defer once in either
  window. Step 10a's change to that call site is still right -- a bare
  `defer()` is a latent flat fifteen seconds -- but no observed event
  came from it.

## What step 9 measured

Step 7's numbers were captured while coalescing was inert. Step 9
instrumented the fold itself and re-measured on `sfcbr` once the
instrumented build was deployed, so for the first time these numbers
describe a cluster on which the fold actually matches rows.

One window, through the same `tools/queue-wait-report.py`:

* **`sfcbr`**, 41h57m, 26,229 operations, published as
  2026-08-27T13:03Z to 2026-08-29T07:00Z. **That label is ten hours
  out**: step 10 reconstructed the dataset and it actually spans
  2026-08-27T03:03Z to 2026-08-28T21:00Z, because the log records'
  `ts` is local time carrying a `Z` suffix. Production steady state
  either way, and the coalescing counts below are unaffected -- the
  coalescing instrumentation was already deployed across the whole of
  the real span, and 147 of these operations predate it and are
  excluded from the coalescing tables by the tool. The latency
  numbers *are* affected, because #3916 deployed inside the real span
  but not inside the published one; see the phase 10 subsection below.

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
13:13, so this was meant to be the first window carrying the
dependency-wait back-off. **The data it was read from was not that
window.** Step 10 reconstructed the dataset exactly and found it runs
from 2026-08-27T03:03Z to 2026-08-28T21:00Z -- ten hours earlier than
the label above says, because the log records carry a local-time `ts`
stamped with a `Z` suffix and ten hours is this cluster's UTC offset.
10,108 of its 26,225 samples therefore predate the 13:13Z redeploy and
were captured under the flat fifteen second defer #3916 removed. What
follows is corrected in place; the reconstruction is in
[What step 10 measured](#what-step-10-measured).

The `user_waiting` lane as published, with the same tool's row over
the window the label actually names beneath it:

| | p50 | p90 | p99 | max |
|---|-----|-----|-----|-----|
| as published (straddles the redeploy) | 1.20 s | 7.28 s | 17.18 s | 42.83 s |
| 13:03Z to 07:00Z, all | 1.14 s | 2.35 s | 7.36 s | 42.83 s |
| 13:03Z to 07:00Z, never deferred | 0.86 s | 1.84 s | 2.29 s | 4.59 s |

against step 7's 15.78 s p50 for the same lane. **Do not read either
row as a controlled before-and-after.** Three things get in the way.
Two fifths of the published row is a pre-fix workload. The step 7 window is a
different workload a week earlier, with no attempt to hold load
constant. And `wait_seconds` is `start_time - created_at`
(`baseoperation.py:162`) -- time since the operation was *created*,
not since its last deferral -- so it is not a direct reading of the
defer delay at all.

What survives the correction:

* Sub-second waits appear on operations with `defer_count == 1`,
  which a flat fifteen second delay could not produce. The back-off
  is live. Step 10 sharpens this: after the redeploy the entire
  `defer_count == 1` population tops out at 3.79 s.
* Operations still defer. 1,568 did so in the published dataset and
  1,360 in the window the label names.

What does not survive:

* ~~Roughly 400 of the 823 first deferrals sit at 15-17 s ... what
  those operations were waiting for has not been established.~~
  **Withdrawn.** Those 823 first deferrals include 493 from before
  the redeploy, and under the flat fifteen second defer a first
  deferral lands in the 15-17 s band by construction -- 754 of the
  956 pre-fix first deferrals do. After the redeploy there is not one
  `user_waiting` operation in that band in either of step 10's 42
  hour windows. The population was #3863, measured after the fix had
  shipped but with pre-fix samples still in the dataset. Nothing was
  waiting on anything unexplained.

The rest of the paragraph stands: `defer_with_backoff`'s `(15, 30,
60)` transient-failure retry emits `scheduling retry after transient
failure` **zero** times in either dataset, so it was never the
explanation either.

So phase 10's subject did move, but not for the reason given here.
The flat 15 s defer is gone and the `user_waiting` lane's residual is
the back-off ladder doing what it was designed to do. The question
step 10 inherited turned out to be a different one -- where the deep
tail lives, which is not this lane at all. See
[PLAN-queue-performance-phase-10-defer-latency.md](PLAN-queue-performance-phase-10-defer-latency.md)
and the step 10 section above.

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

## What step 11 measured

**Nothing yet.** This section is a placeholder, not a gap in
transcription -- step 11h, the `sfcbr` re-measurement, is deferred
until the merged build has run there for at least 24 hours (it had
not, as of this close-out), and no other window has been captured
with the wider key deployed. Do not read the absence of numbers here
as "no effect measured"; it is "not measured".

The only real numbers available are the pre-change baseline from the
phase 11 plan's survey finding 1, gathered *before* any of this
phase's code existed and included here only as the yardstick step 11h
measures against:

* **`sfcbr`**, six hours, 4,653 `execution duration` events, 1,510 of
  them `net_op`. The fold `ran` 263 times and folded 4 siblings. 581
  were refused outright by the (then single-column) per-node-queue
  guard -- read as an upper bound on what a per-node fold could
  reach, not a count of foldable work, since an operation with no
  coalescible task at all lands in the same bucket. On the per-node
  `network` family lane, 573 of 919 operations (62%) dequeued
  alongside at least one sibling, which is the ceiling the wider key
  is trying to close on.

When step 11h runs, this section should report, against that
baseline: the `net_op` fold outcome counts before and after with the
wider key deployed, how many siblings the per-node fold actually
collapses, and whether `claim_coalescible_siblings`'s duration
distribution moved -- which is what decision 6's no-new-index call
turns on. See the phase 11 plan's Results section for the build
itself and the two mid-phase corrections found while landing it.

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
  node), which the current `target_column` parameter did not
  support. Generalising to a list of `(column, value)` pairs is
  what phase 11 built, and it is straightforward on the model side
  -- `NodeNetOp` already carries both columns. It turned out **not**
  to be enough on its own: `network_apply_create_hypervisor` is
  drained by `sf-queues`, whose worker pool has no per-target routing
  key, so the partitioned-worker safety argument that makes a
  per-node fold sound for `sf-net` does not hold there. Phase 11
  gave the wider key to `network_ensure_mesh` (drained by `sf-net`)
  instead and deferred this task to a successor issue on its own
  merits -- see decision 5 and the Future work section of
  [PLAN-queue-performance-phase-11-multi-column-key.md](PLAN-queue-performance-phase-11-multi-column-key.md).
  Tracked as #3884.

* **Explicit fairness for low-priority queues**. The dequeue
  query honours strict priority order via `FIELD()`; lower
  priorities only spill in when higher ones yield fewer rows
  than `limit`. Sustained heavy load on `user_facing` could in
  principle starve `background`. The CI signal will tell us
  whether to add bounded-staleness ordering
  (`ORDER BY CASE WHEN NOW() - created_at > N THEN top
  ELSE priority END, ...`) or a reserved-slot mechanism.
