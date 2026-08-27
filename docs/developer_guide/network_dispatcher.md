# Network operation dispatcher

The `sf-net` daemon dispatches network cluster operations through `net-worker`
(`shakenfist/daemons/network/workitem.py`). This page explains the dequeue loop,
the dependency-wait / defer mechanism, and the exponential back-off map that
controls defer timing.

## Queue families

Network operations are split across two queue families (see
[sf-net daemon topology](#sf-net-daemon-topology) below for which node
drains which):

- **Per-node** (`{node_uuid}-network-{priority}`) — hypervisor-local operations
  such as `create_on_hypervisor` and `ensure_mesh`. The net-worker on that node
  always drains these.
- **Network-node** (`networknode-clusteroperation-{priority}`) — elected
  network-node singleton operations such as `create_on_network_node`,
  `add_floating_ip`, and `route_address`. Only the elected network node's
  net-worker drains these.

A caller chooses the family with the `family` keyword argument of
`enqueue_cluster_operation()` (`shakenfist/schema/operations/util.py`),
which builds the queue name as `{target}-{family}-{priority}`. Passing
`family='network'` with a node uuid as the target produces the per-node
`{node_uuid}-network-{priority}` queues; the default
`family='clusteroperation'` with `target='networknode'` produces the
network-node queues.

## Dequeue and terminal-state check

On each loop iteration the worker makes a single
`mariadb.dequeue_work_items()` call passing all of its queue names in
priority order, which claims up to `BATCH_SIZE` items in one round trip —
see
[Batched, priority-aware dequeue](database_internals.md#batched-priority-aware-dequeue)
for the server side. Before executing each
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

`sf-queues` applies the same schedule to its dependency waits, but its jobs
are one-shot threads with no per-worker map, so the depth is derived
statelessly from the `defer_count` the work item persists across defers
(`dependency_defer_delay()` in `shakenfist/daemons/queues/workitem.py`,
issue 3863). Because the count survives worker restarts, `sf-queues` back-off
does not reset the way the operator note below describes for `sf-net`; the
trade-off is that a chained wait (the same op deferring on a second
dependency) carries its depth over rather than starting again at 100 ms.

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

## Floating-IP and route operations

The following `Network` methods enqueue cluster operations and
return op handles rather than performing host mutations inline:

| Method | Op type | Queue family |
|--------|---------|--------------|
| `ensure_mesh` | `net_op` | per-node network |
| `add_floating_ip` | `net_ip_op` | network-node |
| `remove_floating_ip` | `net_ip_op` | network-node |
| `route_address` | `net_ip_op` | network-node |
| `unroute_address` | `net_ip_op` | network-node |
| `remove_nat` | `net_op` | network-node |

All four op-type dispatchers (`net_op`, `net_ip_op`, `net_iface_op`,
`net_iface_ip_op`) now route through `BridgedVXLanNetwork` and persist
`ErrorReport` on their outer exception branch.

### Event-correlation split

Each migrated `Network` method produces two audit events:

1. **Requesting event** (synchronous, emitted on the caller's thread inside
   `Network.X()`). Recorded against all objects relevant to the call via
   `affected_objects=`. For floating-IP and route methods this includes both
   the network being acted on and the floating network
   (`('network', FLOATING_NETWORK_UUID)`).

2. **Dispatch-time event** (emitted by the dispatcher when the op actually
   executes). The dispatcher has access only to the objects it has in scope:
   - `net_op` / `net_ip_op`: the `Network` itself, plus
     `('network', FLOATING_NETWORK_UUID)` for floating-IP ops.
   - `net_iface_op` / `net_iface_ip_op`: the `NetworkInterface`.

The requesting event gives operators an immediate audit trail that the call
was received; the dispatch-time event records when the work actually ran and
on which worker node. The two events are correlated by the shared op UUID
present in both.

## dnsmasq operations

The dnsmasq-related `Network` methods are queue-dispatched too. The full set
of migrated methods spans:

| Method | Op type | Queue family |
|--------|---------|--------------|
| `ensure_mesh` | `net_op` | per-node network |
| `add_floating_ip` | `net_ip_op` | network-node |
| `remove_floating_ip` | `net_ip_op` | network-node |
| `route_address` | `net_ip_op` | network-node |
| `unroute_address` | `net_ip_op` | network-node |
| `remove_nat` | `net_op` | network-node |
| `update_dnsmasq` | `net_op` (task 9) | network-node |
| `remove_dnsmasq` | `net_op` (task 10) | network-node |
| `remove_dhcp_lease` | `net_macaddr_ip_op` | network-node |
| `update_dns_entry` | `net_op` (task 9) | network-node |
| `remove_dns_entry` | `net_op` (task 10) | network-node |

### New NetOp task types

Two task constants serve the dnsmasq path:

- **`network_apply_update_dnsmasq` (9)** — applies a dnsmasq configuration
  refresh on the network node, used by both `update_dnsmasq` and
  `update_dns_entry`.
- **`network_apply_remove_dnsmasq` (10)** — tears down the dnsmasq instance on
  the network node, used by both `remove_dnsmasq` and `remove_dns_entry`.

The historical `network_update_dnsmasq` (3) and `network_remove_dnsmasq` (4)
task constants remain in place for the broader reconciliation path used by
`maintain.py`.

### In-worker sibling call pattern

Some `Network` lifecycle methods need to invoke dnsmasq operations as part of
a larger compound operation. For example, `create_on_network_node` calls
`update_dnsmasq` at the end of `_network_deploy`, and `delete_on_network_node`
calls `remove_dnsmasq` during teardown.

Re-enqueueing through the normal `Network.update_dnsmasq()` facade from inside
these callers would deadlock: the network-node queue has a single worker, and
that worker is already executing the parent op. The enqueued child op would
never be dequeued until the parent completes — but the parent is waiting for
the child. The cluster operation reaper would eventually kill one of them, but
only after `CLUSTER_OP_STUCK_THRESHOLD` seconds.

The correct pattern is to construct `BridgedVXLanNetwork` directly and call the
`_apply_*` method inline:

```python
# Inside create_on_network_node / _network_deploy
BridgedVXLanNetwork(self)._apply_update_dnsmasq(context)

# Inside delete_on_network_node
BridgedVXLanNetwork(self)._apply_remove_dnsmasq(context)
```

This keeps all host mutation inside `BridgedVXLanNetwork` (the worker-only
mutation surface), avoids a queue round-trip, and eliminates the
deadlock-by-timeout. Earlier incarnations of these callers used the old
inline mutation path, which carried a latent deadlock.

The general rule: **never call `Network.X()` from inside a dispatcher handler
if `X()` enqueues to the same queue family**. Always use
`BridgedVXLanNetwork(self)._apply_X()` instead.

## Lifecycle operations

The four `Network` lifecycle methods are queue-dispatched, completing the
migration of all 15 host-mutating methods.

### Method-to-queue-family mapping

| Method | Op type | Queue family |
|--------|---------|--------------|
| `create_on_hypervisor` | `node_net_op` (task 2) | per-node network |
| `delete_on_hypervisor` | `node_net_op` (task 1 — reused `network_destroy`) | per-node network |
| `create_on_network_node` | `net_op` (task 11) | network-node |
| `delete_on_network_node` | `net_op` (task 12) | network-node |

`create_on_hypervisor` and `delete_on_hypervisor` route to the per-node
`{node_uuid}-network-{priority}` queues because they mutate per-hypervisor
state (local VXLAN interface, bridge membership, FDB entries).
`create_on_network_node` and `delete_on_network_node` route to the cluster-wide
`networknode-clusteroperation-{priority}` queues because they configure state
that only the elected network node owns (dnsmasq, NAT rules, floating-IP
routing).

### New task constants

Two new `NetOp` task constants were added:

- **`network_apply_create_network_node` (11)** — provisions the network on the
  network node (dnsmasq start, NAT/floating-IP plumbing, DNS zone). Calls
  `BridgedVXLanNetwork._apply_create_on_network_node`, which internally calls
  `self._apply_enable_nat` (formerly the public `Network.enable_nat`) as part
  of the same in-worker pass.
- **`network_apply_delete_network_node` (12)** — tears down the network on the
  network node (dnsmasq stop, NAT/routing cleanup). Calls
  `BridgedVXLanNetwork._apply_delete_on_network_node`.

One new `node_net_op` task constant was added:

- **`network_apply_create_hypervisor` (2)** — creates the local VXLAN interface
  and bridge on a hypervisor node. Calls
  `BridgedVXLanNetwork._apply_create_on_hypervisor`.

The existing **`network_destroy` (1)** on `node_net_op` is reused for
`delete_on_hypervisor`; no new constant was needed.

### `enable_nat` removal from public surface

`Network.enable_nat` no longer exists as a public method. The logic lives in
`BridgedVXLanNetwork._apply_enable_nat`, called only from within
`_apply_create_on_network_node`. External callers that previously called
`enable_nat` directly should use `create_on_network_node` instead; NAT
enablement is an implementation detail of network creation, not a separately
callable operation.

### Broader reconciliation path

The existing `network_deploy` (5), `network_destroy` (6 — network-node variant),
and `network_update_dnsmasq` (3) task constants on `NetOp` continue to do
broader reconciliation: `network_deploy` calls `create_on_network_node` +
`ensure_mesh` for all cluster nodes; `network_update_dnsmasq` refreshes dnsmasq
across the cluster. These reconciliation paths are used by `maintain.py`.

### In-class `_apply_X` cleanup

`_apply_create_on_network_node` and `_apply_delete_on_network_node` call other
`_apply_*` helpers directly on `self` (e.g. `self._apply_enable_nat`,
`self._apply_update_dnsmasq`, `self._apply_remove_dnsmasq`) rather than going
through `Network.X()`. This replaces two earlier patterns — late imports, and
constructing a fresh `BridgedVXLanNetwork(self)` inside the handler. The
in-class call is cleaner, avoids the redundant wrapper construction, and makes
the call graph explicit.

## maintain.py and the discovery-only model

`shakenfist/daemons/network/maintain.py` is **discovery-only**: the maintain
thread detects drift and enqueues reconciliation ops,
but never waits for them to complete. All `raise_for_error()` calls have been
removed from the maintain loop. The net-worker dispatcher handles async
reconciliation; the maintain thread's only job is to notice drift and express
intent via the queue.

### The five-guard pipeline

For every network with detected drift, maintain applies five guards in order
before enqueuing:

#### Guard 1 — Queue-depth safety

Before the per-network loop, maintain queries `mariadb.get_work_queue_length`
across all network queue families this node services:

- Always: `get_node_network_queues(config.NODE_UUID)` — per-node queues for
  hypervisor-local ops.
- When `config.NODE_IS_NETWORK_NODE`: `get_all_network_queues()` — cluster-wide
  `networknode-clusteroperation-*` queues.

The `processing + queued + deferred` counts are summed across all queues. If
the total exceeds `MAINTAIN_QUEUE_DEPTH_THRESHOLD` (default 50), the entire
maintain pass is skipped with an audit event against the node. Rationale: piling
reconciliation requests on top of an already backed-up queue would worsen
head-of-line blocking without improving convergence speed.

#### Guard 2 — Per-network gating

For each network with detected drift, `mariadb.has_pending_cluster_operation(
target_object_type='network', target_uuid=n.uuid)` is called. This queries the
`cluster_operation_targets` table (history-aware, not a single-pointer) and
returns `True` if any in-flight op (`queued`, `preflight`, or `executing`) is
already targeting this network. If `True`, the network is skipped for this pass:
the in-flight op will fix the drift when it executes.

#### Guard 3 — Cooldown

`mariadb.get_recent_terminal_op_states_for_target('network', n.uuid, limit=1)`
returns the most recent terminal op for the network as a
`(op_uuid, state_value, update_time)` tuple. If the most recent terminal op
ended in `STATE_ERROR` within the last `MAINTAIN_RECONCILE_COOLDOWN_SECONDS`
(default 60 s), maintain skips enqueueing for this network on this pass. This
prevents tight retry loops against a consistently misbehaving network — the
previous failure is given time to breathe before another attempt is enqueued.

#### Guard 4 — Circuit breaker

`mariadb.get_recent_terminal_op_states_for_target('network', n.uuid,
limit=config.MAINTAIN_RECONCILE_CIRCUIT_K)` returns the most recent K terminal
ops. If **all K** terminal ops ended in `STATE_ERROR`, maintain skips this
network and emits a prominent audit event:

> "network has failed reconciliation K times in a row; quiesced pending operator attention"

The circuit closes naturally: on the next maintain pass, if an operator has
intervened and a fresh reconciliation has succeeded, the most recent terminal op
is `STATE_COMPLETE` and the pipeline proceeds. There is no manual circuit-reset
command — the history naturally re-evaluates.

#### Guard 5 — Enqueue at background priority

If all four guards pass, maintain enqueues the reconciliation via the schema
helpers using `PRIORITY.background` (not `user_facing`). The maintain thread
does not wait. Per-hypervisor drift uses `nn_create_and_enqueue`; network-node
drift uses `net_create_and_enqueue` plus per-floating-IP and per-route ops.

### Stray vxlan reaping — the one host mutation maintain performs

Discovery-only has exactly one exception. At the end of each pass maintain
compares the vxlan devices present on the host against the networks it should
be carrying. A device which matches nothing, and has done so for
`MAINTAIN_STRAY_VXLAN_GRACE_SECONDS`, is handled by
`Job._handle_stray_vxlans()`, which sorts it into one of three outcomes.

**Reaped directly, on the maintain thread.** A device whose vxid no `networks`
row claims. This is the exception to worker-only mutation, and it exists
because the queue path is genuinely unavailable: a cluster operation has to
target an object, and this is precisely the device whose object is gone. The
row is inserted before the device is ever created, so an on-host device whose
vxid has no row can never be a network under construction — it can only be
residue. Note the test is for the *static row*, not the object state: a
soft-deleted network still protects its device, only a hard-deleted one is
reapable.

**Enqueued as a `node_net_op`.** On a hypervisor, a device whose network *does*
still exist but which no instance on this node is attached to. Per-instance
teardown deletes the device when the last instance on a host leaves a network,
so one still present means that cleanup was missed. The object exists here, so
there is no reason to step outside the dispatcher:
`nn_create_and_enqueue(node_uuid, network_uuid, [network_destroy],
PRIORITY.background)` targets exactly this (node, network) pair, and running
inside the worker serialises the teardown against any concurrent create for
the same network. `find_network_vxids()` returns the claiming network's uuid
alongside the vxid so this is possible. The enqueue is skipped when a cluster
operation already targets that network, and the vxid's grace period is re-armed
afterwards so the operation has time to run before the vxid is reconsidered.
The network node is excluded from this branch entirely, since it carries a
device for every active network whether or not it hosts instances.

**Left alone, warned about once.** Anything else. Before this existed the
warning fired on every pass forever, which on one production cluster was
~5,700 identical log lines per day per stray (issue #3597). Every non-actioned
outcome — a failed delete, a vxid which maps to no Shaken Fist-named device —
also re-arms the grace period rather than retrying every thirty seconds.

Suppression is keyed on `(vxid, reason)`, in `EXTRA_VLANS_WARNED`, and the
per-device delete failure warning goes through the same mechanism keyed on
`(vxid, device)`. Both are dropped for a vxid when the stray leaves the host or
is reaped, so a vxid randomly reissued to a later network is reported on its
own merits. Keying on the reason rather than on the vxid alone matters in both
directions: a stray whose disposition changes is still reported, and a stray
whose disposition is stable — including one whose devices persistently refuse
to be deleted — is reported exactly once per episode. No path here logs on a
per-pass cadence.

Deciding whether an instance protects its network is a single indexed query,
`mariadb.get_node_instance_vxids()`: this node's `INSTANCE_LOCATION` reference
rows joined to `network_interfaces` joined to `networks`, with the instance
state filter applied in SQL. A protected stray is deliberately never reaped, so
this runs on every thirty second pass for as long as the stray survives —
hydrating every instance placed here plus one network per distinct network
uuid, which is what this used to do, made that permanent cost proportional to
the node's instance count. The join spans both uuid storage conventions
(references hold the dashed form, the static tables the undashed one), so it
transforms the reference side with `REPLACE`. The states which protect are
`Instance.ACTIVE_STATES` plus `delete-wait-error`: the latter is not in
`ACTIVE_STATES` but by definition means teardown did not complete, so a domain
may well still be attached to the bridge. Interfaces are filtered on `active IS
NOT NULL` (NULLed only on delete), which is a superset of the interface active
states, because erring towards protecting a device is the right direction here.
If this node's row cannot be read at all, every claimed stray is protected, and
a database failure propagates rather than presenting as an empty result — an
empty result is permission to tear devices down, so aborting the pass is the
safe outcome.

**The host gets a veto.** Everything above this point is the database's view:
that a networks row is gone, or that no instance record places a user of this
network here. Both are the correct sources, and both are *records* rather than
observations — a lost update or a missing placement row looks exactly like a
leaked device once the grace period has passed, and the consequence on the
teardown path is a live domain losing its network. So immediately before either
mutating branch commits, `_foreign_bridge_members()` asks the host directly:
`ip link show master br-vxlan-<vxid>`, minus the devices Shaken Fist itself
enslaves (`vxlan-`, `veth-...-o`, `egr-...-o`). Anything left is a guest tap,
which is proof that a domain is attached right now, and the stray is protected
and warned about instead. If the question cannot be answered at all, the stray
is protected — "could not ask the host" is not "the host says nobody is using
it". This is one `ip` invocation per candidate vxid, only on the paths which
are about to mutate.

The line between those two answers is load-bearing, and iproute2 makes it easy
to get wrong. `_apply_delete_on_hypervisor()` deletes `br-vxlan-<vxid>` before
`vxlan-<vxid>`, and `discover_interfaces()` keys stray detection on the latter,
so *the most common stray shape is a vxlan interface whose bridge is already
gone*. That has to read as "nothing is enslaved to it", not as "I could not
ask" — otherwise the reaper protects precisely the residue it exists to remove,
and a partial reap (which deletes the bridge first) can never retry its
survivors. `util_network.get_bridge_members()` therefore returns `[]` for a
missing bridge. It cannot decide that on exit status alone: `ip link show
master <missing>` exits 255 — iproute2's catch-all failure code — with
`Error: argument "<name>" is wrong: Device does not exist`, which is neither
the exit code nor the wording `ip link show <missing>` produces for the same
condition. The message is therefore matched explicitly and every other failure
still raises, because an empty member list is what authorises deleting devices.

Racing the net-worker is harmless. Deletion is guarded by
`check_for_interface()` and each device is deleted inside its own
`try`/`except`, so a `network_destroy` running concurrently on the same node
cannot take the maintain thread down with it, and one failing device does not
abandon the others. `vxlan-%06x` is deleted last and only if every sibling
succeeded: `discover_interfaces()` only reports a vxid when an interface named
`vxlan-` exists, so removing it while a leftover survives would hide that
leftover from every future pass — no rediscovery, therefore no retry and no
event.

Reaping removes every device Shaken Fist names from the vxid — `br-vxlan-`,
`veth-...-o`, `egr-...-o` and then `vxlan-`. The network namespace and NAT
rules a network node also owns are keyed by network uuid rather than vxid, so
they are unreachable once the row is gone; that residue is a known limitation,
and is called out for operators in
[the networking overview](../operator_guide/networking/overview.md#stray-vxlan-reaping).
Each reap records an audit event on the node naming the devices actually
removed, which is the operator-visible record of what happened and why. A reap
in which some devices went and others could not be deleted records
`partially reaped stray vxlan` instead, with both the `devices` which were
removed and the `failed` ones in `extra` — devices left the host either way,
and the warning names only the failure. An audit event is *not* recorded when
nothing at all was removed.

The database check uses `mariadb.find_network_vxids()`, an indexed
`WHERE vxid IN (...)` against the UNIQUE index on `networks.vxid`. Unlike most
getters it deliberately does not swallow database errors: an empty result means
"nothing claims these vxids" and the caller deletes host devices on the
strength of it, so a failed query must raise rather than present as an answer.
`mariadb.get_node_instance_vxids()` carries the same contract for the same
reason. `shakenfist/tests/test_mariadb_find.py` pins both on the direct and
gRPC paths, and pins the servicer's `INTERNAL` status on failure — the sibling
finders in that module all assert the *opposite* contract, so without those
tests a refactor which made them consistent would turn a database outage into
cluster-wide device deletion.

Because the whole stray check depends on RPCs newer than the rest of the
daemon, `execute()` wraps `_handle_stray_vxlans()` in a `try`/`except`. An
`sf-net` talking to an `sf-database` which predates `FindNetworkVxids` or
`GetNodeInstanceVxids` gets `UNIMPLEMENTED`, which `_grpc_call()` does not
retry; without the guard the
maintain thread would die and be restarted by the monitor every thirty seconds
for the length of the mixed-version window, losing the rest of the pass with
it. Stray vxlans are the least urgent thing maintain does, so they fail alone.

### New config knobs

| Knob | Default | Description |
|------|---------|-------------|
| `MAINTAIN_QUEUE_DEPTH_THRESHOLD` | `50` | Skip the entire pass if the combined network-queue depth exceeds this value |
| `MAINTAIN_RECONCILE_COOLDOWN_SECONDS` | `60` | Skip a network if its most recent terminal op was `STATE_ERROR` within this window |
| `MAINTAIN_RECONCILE_CIRCUIT_K` | `5` | Quiesce a network if the last K terminal ops are all `STATE_ERROR` |
| `MAINTAIN_STRAY_VXLAN_GRACE_SECONDS` | `300` | How long a vxlan device must be stray before maintain reaps or warns about it |

### The `get_recent_terminal_op_states_for_target` MariaDB helper

A three-layer helper supports this:

```python
mariadb.get_recent_terminal_op_states_for_target(
    target_object_type: str,
    target_uuid: str,
    limit: int,
    op_type: str | None = None,
) -> list[tuple[str, str, float]]
```

Returns up to `limit` most recent terminal op state records targeting the given
object, as `(op_uuid, state_value, update_time)` tuples ordered newest first.
The query joins `cluster_operation_targets` against `object_states` filtered to
terminal states (`STATE_COMPLETE`, `STATE_ABORT`, `STATE_DELETED`, `STATE_ERROR`),
ordered by `update_time DESC`. If `op_type` is provided, results are further
filtered by `cluster_operation_targets.operation_type`.

The same helper powers both the cooldown and circuit-breaker queries — they
differ only in `limit`: cooldown calls it with `limit=1`, circuit-breaker with
`limit=config.MAINTAIN_RECONCILE_CIRCUIT_K`. This avoids code duplication and
ensures both checks see the same ordered history.

The helper is generic: it works for any `target_object_type`, not just networks.
The maintain caller passes `target_object_type='network'`.

### Operator note: clearing the circuit-breaker quiescence

When a network enters the circuit-breaker quiesced state, the maintain thread
stops enqueuing reconciliation ops for it. The quiescence resolves automatically:

1. The operator investigates the network (e.g. checks event log, inspects host
   state, corrects a misconfiguration).
2. The operator manually triggers a reconciliation via the REST API or CLI, or
   the underlying host condition resolves on its own.
3. When that reconciliation succeeds, the most recent terminal op for the network
   is no longer `STATE_ERROR`, and the next maintain pass re-evaluates all guards
   cleanly.

There is no separate "reset" command. The circuit-breaker is a read-only
assessment of recent history — it never mutates state.

## REST contract

The user-facing REST contract reflects the async
queue-based dispatch visible at the API boundary.

### 202+poll response shape for the two delete endpoints

`DELETE /networks/<uuid>` and `DELETE /networks` now return HTTP 202 (Accepted)
instead of 200. The delete work is queue-based, but
the previous response shape falsely implied synchronous completion. The new shapes
are:

**Single-network delete** (`DELETE /networks/<uuid>`):

```json
{"op_type": "net_op", "op_uuid": "<cluster-operation-uuid>"}
```

**Bulk delete** (`DELETE /networks` — all networks in a namespace):

```json
[
  {"network_uuid": "<n1>", "op_type": "net_op", "op_uuid": "<op1>"},
  {"network_uuid": "<n2>", "op_type": "net_op", "op_uuid": "<op2>"}
]
```

Clients that need synchronous-completion semantics should poll
`GET /clusteroperations/<op_type>/<op_uuid>` until the `state` field is in a
terminal set (`complete`, `abort`, `deleted`, or `error`). On `error`, the
op's `external_view` carries an `error_report` field with the structured
failure information.

### Two new cluster-operation discovery endpoints

#### GET /clusteroperations/\<op_uuid\>/chain

Returns the transitive `depends_on` ancestor closure starting at `<op_uuid>`,
as a list of op-summary dicts. The walk follows each op's `depends_on` field
until no new ancestors are found. The result is unordered with respect to
execution order; clients must reconstruct the DAG from the `depends_on` fields
in the response if ordering matters.

**Namespace scoping**: non-admin callers receive HTTP 403 if any chain member
targets an object in a namespace they do not own. Admin callers see the full
closure. HTTP 404 is returned if the starting op UUID does not exist.

Example:

```
GET /clusteroperations/abc123.../chain
→ 200 [
    {"uuid": "abc123...", "op_type": "net_op", "state": "complete", ...},
    {"uuid": "def456...", "op_type": "net_op", "state": "complete", ...}
  ]
```

#### GET /clusteroperations?target_object_type=\<type\>&target_uuid=\<uuid\>

Returns all cluster operations that targeted the given object, ordered newest
first. The `target_object_type` parameter must be a valid `ObjectType` string
(e.g. `'network'`, `'instance'`).

**Namespace scoping**: the filter is applied at the SQL layer by joining
`cluster_operation_targets` against the namespace-carrying static-values table
for the given object type. Large result sets are never materialised in Python
before filtering — the query is always indexed.

Example:

```
GET /clusteroperations?target_object_type=network&target_uuid=abc123...
→ 200 [
    {"uuid": "ghi789...", "op_type": "net_op", "state": "complete", ...},
    {"uuid": "abc123...", "op_type": "net_op", "state": "complete", ...}
  ]
```

The MariaDB helper `list_cluster_operations_for_target`
follows the same three-layer pattern (Python helper → gRPC → MariaDB) as the
existing `has_pending_cluster_operation` and `get_recent_terminal_op_states_for_target`
helpers described above.

### redirect_to_network_node — three sites removed, one retained

The `@api_base.redirect_to_network_node` decorator proxied HTTP requests from
the receiving API server to the network node's gunicorn on port 13000. After
all host-mutating work now goes through the queue, the decorator is no
longer needed on most endpoints. It has been removed from three sites:

| Endpoint | Reason for removal |
|----------|--------------------|
| `InterfaceEndpoint.get` (`interface.py`) | Synchronous DB read; can run on any node. |
| `NetworkEndpoint.delete` (`network.py`) | Now 202+poll; enqueue works from any node. |
| `NetworksEndpoint.delete` (`network.py`) | Same as single-network delete. |

The decorator **remains** on `NetworkPingEndpoint.get` (`network.py`). The ping
handler executes `ip netns exec <network_uuid> ping -c 10 <addr>` directly and
returns its stdout/stderr synchronously. The network namespace exists only on
the elected network node, so this handler genuinely needs to run there.

Migrating the ping endpoint to be queue-based requires new op-output
infrastructure: today the queue carries only error reports, not arbitrary command
output. Until that infrastructure exists, the redirect is a tactical necessity.
The decorator definition in `shakenfist/external_api/base.py` is retained for
this one remaining use. Future work can either:

- Introduce an op-output storage layer (e.g. a `cluster_operation_outputs` table)
  and migrate ping to enqueue a `NetOp` task that captures the ping result, or
- Retain the redirect indefinitely if ping latency requirements make async
  delivery unacceptable.

### client-python transparent polling (feature branch network-facade-phase-07)

The sibling `client-python` repo carries matching changes on the
`network-facade-phase-07` feature branch:

- `delete_network(wait=True)` (default) detects the 202 response, extracts the
  op UUID, and polls `GET /clusteroperations/<op_type>/<op_uuid>` at 1-second intervals
  until a terminal state is reached. On `STATE_ERROR` it raises
  `ClusterOperationFailed` carrying the `ErrorReport`. This preserves the
  synchronous-with-exception behaviour that existing callers expect.
- `delete_network(wait=False)` returns the `(op_type, op_uuid)` handle
  immediately without polling. Advanced callers use this for fire-and-forget
  patterns or when building their own polling loops.
- `delete_all_networks` follows the same pattern; the bulk response list is
  polled sequentially (one poll loop per op UUID).
- New methods `get_cluster_operation_chain(op_uuid)` and
  `list_cluster_operations_for_target(target_object_type, target_uuid)` call
  the two new discovery endpoints.
- New exceptions `ClusterOperationFailed` and `ClusterOperationTimeout` carry
  structured error information for callers that need to branch on failure codes.

### Retired NetOp handlers

Three handler bodies that pre-Phase-6 `maintain.py` enqueued have been removed
from `shakenfist/operations/net_op.py`:

| Task constant | Enum value | Former purpose |
|---------------|------------|----------------|
| `network_deploy` | `1` | Broader network-node deploy: `create_on_network_node` + `ensure_mesh` for all nodes |
| `network_destroy` | `2` | Broader network-node destroy |
| `network_update_dnsmasq` | `3` | Cluster-wide dnsmasq refresh |

The **enum values are preserved** in `shakenfist/schema/operations/net_op.py` so
that any `cluster_operations` rows still on disk from a prior deploy continue to
parse correctly. The handler bodies now consist of a single line:

```python
raise InvalidStateForTask(self, task)
```

The dispatcher's outer `except Exception` branch converts this to `STATE_ERROR`
via `ErrorReport`, so in-flight ops at deploy time fail gracefully rather than
hanging or producing unhandled exceptions. Operators who see `STATE_ERROR` on one
of these task types after a rolling upgrade can safely re-deploy the affected
network via the standard `Network.create_on_network_node()` / `ensure_mesh()` API.

## NodeLock removal

The 13 `NodeLock(global_scope=False)` wrappers are gone from all
`BridgedVXLanNetwork._apply_*` methods (commit `277b0572`). Those wrappers were
added by stability-branch commit `bd9e1869` as a short-term guard against
concurrent callers from four daemons (`sf-net`, `sf-queues`, `sf-api`, and
`instance.py`). With the queue migration complete, the dispatcher loop is the
**only** caller of every `_apply_*` method, and it is single-threaded by
construction. The load-bearing invariant is the single-worker safety property
documented in the "Single-worker safety invariant" section above (and in the
comment block at `self._defer_delays` in this file): each queue is drained by
exactly one worker, so no two `_apply_*` invocations can race. Cross-daemon
serialisation is now provided by the queue itself — only `sf-net` dequeues and
executes network work, so concurrent host-mutating calls from other daemons
(`sf-queues`, `sf-api`, `instance.py`) cannot bypass the dispatcher by
construction.

An important scope note: all 13 removed locks used `global_scope=False`, making
them per-node `NodeLock`s, not `ClusterLock`s. The single-threaded-dispatcher
argument covers per-node serialisation only. `ClusterLock`s serialise across the
whole cluster via a different mechanism and remain in use for operations that
require cluster-wide exclusion; the reasoning above does not apply to them.

## sf-net daemon topology

`sf-net` runs a `net-worker` job on **every** cluster node (not only the
elected network node). Each node's worker drains its own per-node
`{node_uuid}-network-*` queues for hypervisor-local operations
(`create_on_hypervisor`, `ensure_mesh`). Additionally, the elected network
node's worker also drains the cluster-wide `networknode-clusteroperation-*`
queues for network-node-only operations (`create_on_network_node`,
`add_floating_ip`, etc.). This two-family design means per-hypervisor network
mutations are parallelised across nodes while network-node-singleton operations
remain serialised.

## Network facade architecture

**Worker-only mutation surface.** `BridgedVXLanNetwork`
(`shakenfist/network/bridged_vxlan_network.py`) is the only place that
mutates host network state for a network which exists (the one exception,
reaping devices belonging to networks which no longer exist, is described
under "maintain is discovery-only" below). Its constructor is called
exclusively from the
single-threaded net-worker dispatcher
(`shakenfist/daemons/network/workitem.py`) — making re-entrancy through
the queue structurally impossible. External callers always hold `Network`;
the dispatcher constructs `BridgedVXLanNetwork` and calls `_apply_*`
methods on it. The single-worker-per-queue invariant (see the comment
block at `self._defer_delays` in workitem.py) is a load-bearing property:
it is why the dispatcher's in-memory exponential back-off map is correct,
and why cross-daemon serialisation can be queue-based rather than
lock-based. All `NodeLock(global_scope=False)` wrappers that formerly
existed inside `_apply_*` methods have been removed — only `sf-net`
dequeues and executes network work, so concurrent invocation across
daemons cannot happen by construction. The cancellation check on dequeue
runs before the `_apply_*` call; if the op is already cancelled, the
worker skips execution and transitions the op to `STATE_ABORT`.

**Network methods enqueue; maintain is discovery-only.** All 15
host-mutating `Network` methods enqueue a cluster operation and return an
op handle rather than mutating state directly. `shakenfist/daemons/network/maintain.py`
is discovery-only: it never blocks on `raise_for_error()`. Each maintain
pass applies a five-guard pipeline before enqueueing any reconciliation op
at `PRIORITY.background` — (1) queue-depth safety, (2) per-network gating
via `has_pending_cluster_operation`, (3) cooldown on recent errors,
(4) circuit-breaker on repeated errors, (5) enqueue. The config knobs
controlling maintain are `MAINTAIN_QUEUE_DEPTH_THRESHOLD` (default 50),
`MAINTAIN_RECONCILE_COOLDOWN_SECONDS` (default 60),
`MAINTAIN_RECONCILE_CIRCUIT_K` (default 5) and
`MAINTAIN_STRAY_VXLAN_GRACE_SECONDS` (default 300, see below).

**The one exception: reaping stray vxlans.** Maintain deletes orphaned
vxlan devices (`_handle_stray_vxlans()`) directly, on the maintain thread,
rather than through the queue. This is deliberate and is the only
host-mutating code outside the net-worker. The exception is kept exactly
as wide as the argument for it: it covers *only* devices whose network
object no longer exists, because for those the queue path is unavailable
by construction — an operation has to target an object, and there is no
object left to target. The neighbouring case, where the network still
exists but no instance on this node uses it, *is* enqueued: it becomes a
`node_net_op` `network_destroy` targeting (this node, that network), so
it stays inside the dispatcher and serialises against any concurrent
create for the same network.

Three properties make the direct case safe. First, the networks row is
written before any device is created, so a device whose vxid has no row
can never be a network under construction — it can only be residue.
Second, neither mutating branch commits until the host agrees: if a
device Shaken Fist did not create is still enslaved to `br-vxlan-<vxid>`
then a domain is attached to that bridge right now, whatever the
database records say, and the stray is protected instead. A bridge
which does not exist answers that question with "nothing" rather than
failing to answer it — teardown deletes the bridge before the vxlan
interface and rediscovery keys on the interface, so a vxlan device with
no bridge is the commonest stray shape and must stay reapable. Third,
deletion is idempotent and guarded by `check_for_interface()`, so
racing the net-worker's own `network_destroy` teardown of the same device
is harmless; each device is deleted inside its own `try`/`except` which
logs and re-arms the grace period rather than killing the maintain
thread. Devices are only touched after they have been stray for
`MAINTAIN_STRAY_VXLAN_GRACE_SECONDS` (default 300). A stray which is
*not* actionable is warned about once per episode rather than on every
pass — see issue #3597 for the log storm that motivated this.

**REST API surface.** The two network delete endpoints
(`DELETE /networks/<uuid>` and `DELETE /networks`) return HTTP 202
(Accepted) with an op-handle body; callers poll
`GET /clusteroperations/<op_uuid>` for completion. Two discovery endpoints
are available: `GET /clusteroperations/<op_uuid>/chain` (transitive
`depends_on` ancestor closure, namespace-scoped) and
`GET /clusteroperations?target_object_type=<type>&target_uuid=<uuid>`
(ops targeting an object, SQL-layer namespace filtering). The only
surviving `@redirect_to_network_node` is on `NetworkPingEndpoint.get`
because the ping handler runs `ip netns exec` directly on the network
node; migrating it to queue-based requires op-output infrastructure not
yet built (deferred future work).

**Error handling.** `ErrorReport` (`shakenfist/operations/error_report.py`)
is the on-the-wire shape for failed cluster operations: fields `code`,
`message`, `details`, `origin_class`, `traceback`. Errors are data, never
rehydrated Python exception types. The `_EXCEPTION_CODE_REGISTRY` dict
maps typed exceptions to stable string codes (e.g.
`'network.ensure_mesh.failed'`). The op carries `error_report` in its
`external_view`; `op.raise_for_error(timeout=None)` polls until terminal
and raises `NetworkOperationFailed` if the op ended in `STATE_ERROR`,
letting callers that want exception-flow control use a familiar `try/raise`
pattern without the error type being load-bearing across process
boundaries.
