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

## Errors at the queue boundary

### How the dispatcher converts exceptions to ErrorReport

When `dispatch_task` calls an `_apply_*` handler and the handler raises,
the outer `except` clauses in `NetOp.dispatch_task` (`net_op.py`) catch the
exception and do three things in order:

1. Call `ErrorReport.from_exception(e)` to build a structured record. For
   known typed exceptions (`EnsureMeshFailed`, `DeadNetwork`,
   `CreateVXLANInterfaceFailed`, `CannotAssignFloatingGateway`) this maps to a
   stable code via the `_EXCEPTION_CODE_REGISTRY` in
   `shakenfist/operations/error_report.py`. Any other exception maps to
   `internal.unknown` with the original class name preserved in
   `origin_class`.
2. Persist the report via `mariadb.set_cluster_operation_error(op_uuid, report)`,
   which writes a row to the `cluster_operation_errors` table.
3. Transition the op to `STATE_ERROR` by setting `self.state = NetOp.STATE_ERROR`.

The report is **always written before** the state transitions to `STATE_ERROR`.
This guarantees that any caller polling on the op state will see the report
immediately upon observing `STATE_ERROR`, with no race between the state
transition and the persistence of the report.

The core architectural principle is that **errors are data, never rehydrated
Python exception classes**. This converges with gRPC's status-code model and
is an explicit rejection of the `oslo.messaging` rehydration pattern, which
made exception types load-bearing across process boundaries.

### How external callers consume the report

External callers receive an op handle from enqueueing methods such as
`Network.ensure_mesh()`. The typical usage pattern is:

```python
op = n.ensure_mesh()
op.raise_for_error()   # blocks; raises NetworkOperationFailed on error
```

`op.raise_for_error(timeout=None)` delegates to `poll_until_terminal(op,
timeout)`, which polls `cls.from_db(op.uuid)` at a 0.1 s cadence until the
op's state is in `{STATE_COMPLETE, STATE_ABORT, STATE_DELETED, STATE_ERROR}`.
The default timeout is `config.API_ASYNC_WAIT` (15 seconds); callers can
override it for long-running ops. If the deadline elapses,
`OperationTimeout` is raised.

On `STATE_ERROR`, `raise_for_error` reads the report via `op.error_report`
(which calls `mariadb.get_cluster_operation_error(op.uuid)`) and raises
`NetworkOperationFailed(error_report=report)`. The `NetworkOperationFailed`
exception carries the full `ErrorReport` so the caller can branch on
`exc.error_report.code`, render the report for a REST response via
`error_report.to_http()`, or log the traceback field for debugging.

If the op ends in `STATE_COMPLETE` or `STATE_ABORT`, `raise_for_error` returns
silently. Callers that need to distinguish `ABORT` from `COMPLETE` read
`op.state.value` explicitly after the call.

### Extending the error registry

`_EXCEPTION_CODE_REGISTRY` in `shakenfist/operations/error_report.py` is the
single canonical place for the exception-to-code mapping. To support a new
typed exception:

1. Add one entry to `_EXCEPTION_CODE_REGISTRY`:
   `YourException: 'your.subsystem.code'`.
2. Add a corresponding entry to `_CODE_HTTP_STATUS` if the code should map to
   a non-500 HTTP status.
3. No changes are required in the dispatcher's `except` clauses — the generic
   `except Exception` branch catches it and delegates to `from_exception`.

Typed `except` branches in `dispatch_task` exist only where additional
behaviour beyond the report is needed (e.g. logging at a different severity).
