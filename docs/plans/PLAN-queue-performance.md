# PLAN: Queue performance and coalescing

## Status

In progress.

Steps 1-6 are implemented, on the `network-facade` branch. Step 7
(measure, decide on fairness) is left until the per-op
`'started executing'` event from step 1 produces real CI data.

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

Six discrete changes plus one measurement step:

1. **Visibility**: emit a `'started executing'` event at the
   dispatcher-pickup boundary carrying `wait_seconds`,
   `defer_count` and `queue_name`. This is the only place in the
   pipeline that observes both `op.created_at` (insert time) and
   `start_time` (when the worker is about to call `op.execute()`),
   so the per-op queue-wait latency lands directly in eventlog.

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

7. **Re-measure**: once steps 1-6 are deployed in CI, the
   `'started executing'` event distribution tells us whether the
   wait tail is gone or whether explicit fairness (bounded
   staleness, reserved-slot lottery) is still needed for
   lower-priority queues.

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
