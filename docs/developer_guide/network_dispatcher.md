# Network operation dispatcher

The `sf-net` daemon dispatches network cluster operations through `net-worker`
(`shakenfist/daemons/network/workitem.py`). This page explains the dequeue loop,
the dependency-wait / defer mechanism, and the exponential back-off map that
controls defer timing.

## Queue families

Network operations are split across two queue families (see `ARCHITECTURE.md`
for the full topology):

- **Per-node** (`{node_uuid}-network-{priority}`) — hypervisor-local operations
  such as `create_on_hypervisor` and `ensure_mesh`. The net-worker on that node
  always drains these.
- **Network-node** (`networknode-clusteroperation-{priority}`) — elected
  network-node singleton operations such as `create_on_network_node`,
  `add_floating_ip`, and `route_address`. Only the elected network node's
  net-worker drains these.

## Dequeue and terminal-state check

On each loop iteration the worker calls `mariadb.dequeue_work_item()` for each
queue name in priority order, stopping at the first hit. Before executing the
dequeued op it checks whether the op is already in a terminal state
(`abort`, `complete`, `deleted`, or `error`). If so, the op is skipped
(with an audit event) rather than executed — this prevents a stale
`InvalidStateException` that would fire if a pre-aborted op were handed to the
executor.

## Dependency waiting and deferred re-queue

If an op's declared dependencies are not yet in a suitable state, the dispatcher
calls `op.defer(waiting_on=..., delay=N)`, which re-enqueues the op after `N`
seconds. The defer cycle is:

1. The op is dequeued.
2. The dispatcher checks each entry in `op.depends_on`.
3. If any dependency is still in `initial`, `queued`, or `preflight`, the op is
   deferred with a delay drawn from the back-off map and the loop moves on.
4. If a dependency is in `error`, `deleted`, or `abort`, the op itself is
   transitioned to `abort` or `error` and removed from the map.
5. When all dependencies are satisfied the op executes normally and its map
   entry is dropped.

## Exponential back-off map

The back-off map (`self._defer_delays`, a plain `dict[str, float]`) records the
next defer delay for each op UUID. Constants:

| Constant | Value |
|----------|-------|
| `INITIAL_DEFER_DELAY` | 100 ms |
| `MAX_DEFER_DELAY` | 15 s |
| `DEFER_DELAY_MULTIPLIER` | 2× |
| `BACKOFF_MAP_CAP` | 1 000 entries |

Each successive defer doubles the delay up to the 15 s cap. On successful
execution or cancellation the entry is removed. When the map exceeds 1 000
entries, the oldest entry (insertion order) is evicted (FIFO).

### Single-worker safety invariant

**The back-off schedule is only correct because each queue is drained by
exactly one worker.** Per-node queues are drained by that node's net-worker
only; `networknode-*` queues are drained by the elected network node's
net-worker only. Two workers racing over the same queue can independently
defer the same op, resulting in double-enqueueing and broken timing.

If you ever need to move to a multi-worker topology, valid mitigations are:

- **In-process worker pool** — share one map behind a lock.
- **Cross-node workers** — return to database-backed back-off state.

The authoritative statement of this invariant is the comment block at the
`self._defer_delays` declaration in
`shakenfist/daemons/network/workitem.py`.

### Operator note

The back-off schedule is in-memory and per-worker. If a net-worker restarts,
all delay state is lost and defers resume from 100 ms. This means the
effective retry window after a restart is always bounded by
`INITIAL_DEFER_DELAY`, not the accumulated cap. Operators who observe
unexpected defer timing should check whether the net-worker has recently
restarted.
