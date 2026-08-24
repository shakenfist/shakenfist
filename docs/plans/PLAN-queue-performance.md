# PLAN: Queue performance and coalescing

## Status

In progress.

Steps 1-6 merged to `develop` as PR #3194 on 2026-05-26. Step 7
measured the result and decided against explicit fairness; the
measurement, the exclusions it rests on and the decision are in
[PLAN-queue-performance-phase-07-measure-and-decide.md](PLAN-queue-performance-phase-07-measure-and-decide.md)
and summarised under "What step 7 measured" below.

Step 8 runs a `PUSH-AUDIT.md` audit over everything the plan
changed. The work is already on `develop`, so this is a
retrospective audit rather than a pre-push gate, and the plan is
not complete until each finding is resolved or declined in
writing.

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
| 8. Push audit | [PLAN-queue-performance-phase-08-push-audit.md](PLAN-queue-performance-phase-08-push-audit.md) | In progress |

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

## What step 7 measured

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
  measurable baseline numbers first.

* **Explicit fairness for low-priority queues**. The dequeue
  query honours strict priority order via `FIELD()`; lower
  priorities only spill in when higher ones yield fewer rows
  than `limit`. Sustained heavy load on `user_facing` could in
  principle starve `background`. The CI signal will tell us
  whether to add bounded-staleness ordering
  (`ORDER BY CASE WHEN NOW() - created_at > N THEN top
  ELSE priority END, ...`) or a reserved-slot mechanism.
