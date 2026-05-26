# Phase 6: maintain.py rewrite as discovery-only

## Context

Phase 5 finished the per-method migration: every
host-mutating `Network` method now enqueues a cluster
operation rather than running work inline. The remaining
phases of the master plan are cleanups, and Phase 6 is
the first of those.

Today's `shakenfist/daemons/network/maintain.py` is a
hybrid: it does discovery (walk networks, identify
those whose host state has drifted) **and** drives
reconciliation synchronously via
`op.raise_for_error()` blocks. After every fix-the-drift
call, the maintain thread blocks until that op reaches
a terminal state before considering the next network.
Concretely:

* `create_nn_op = n.create_on_network_node(); ...raise_for_error()`
  at line 129
* `add_op = n.add_floating_ip(...); add_op.raise_for_error()`
  at line 140
* `route_op = n.route_address(addr); route_op.raise_for_error()`
  at line 149
* `create_hyp_op = n.create_on_hypervisor(); ...raise_for_error()`
  at line 156
* `mesh_op = n.ensure_mesh(); mesh_op.raise_for_error()`
  at line 159

Each `raise_for_error()` blocks the maintain thread,
serialising the work and concentrating reconciliation
through a single thread of execution. Under the
queue-only model that Phases 2–5 established, this is
unnecessary: the net-worker dispatcher handles the
async reconciliation, and the maintain thread's only
remaining job is to *notice* drift and enqueue the
intent.

Phase 6 rewrites maintain.py to be **discovery-only**
following the design in master plan open question 6.
Each maintain pass:

1. **Queue-depth safety guard.** If
   `mariadb.get_work_queue_length` on the network queue
   family exceeds a configurable threshold, the entire
   pass is skipped (with an audit event). Prevents
   piling reconciliation requests on top of an already
   backed-up queue.

2. **Discovery.** Walk all networks observed on this
   host (and on the network node's set when running on
   the elected network node), compare observed vs
   desired state. Same drift-detection logic as today.

3. **Per-network gating.** For each network with
   detected drift, query
   `mariadb.has_pending_cluster_operation(target=network)`
   (the history-aware query on
   `cluster_operation_targets`). If a reconciliation
   op is already in flight for this network, skip it
   for this pass — the in-flight op will fix the drift
   when it runs.

4. **Cooldown.** If no in-flight op exists, look up
   the most recent terminal reconciliation op for this
   network. If it ended in ERROR within the last
   `MAINTAIN_RECONCILE_COOLDOWN_SECONDS` (default 60s),
   skip enqueueing — let the previous failure breathe
   before retrying. This avoids tight retry loops
   against a misbehaving network.

5. **Circuit breaker.** If the last K terminal
   reconciliations on this network all ended in ERROR
   (default `MAINTAIN_RECONCILE_CIRCUIT_K = 5`), skip
   enqueueing and emit a prominent audit event
   ("network has failed reconciliation K times in a
   row; quiesced pending operator attention"). The
   next maintain pass naturally re-checks; once the
   operator does something that lets a fresh
   reconciliation succeed, the circuit closes (the
   most recent terminal op is no longer ERROR).

6. **Enqueue.** Otherwise enqueue the reconciliation
   directly via the schema helpers (`net_create_and_enqueue`,
   `nn_create_and_enqueue`, `net_ip_op.create_and_enqueue`)
   with `PRIORITY.background` (not `user_facing`).
   No `raise_for_error()` call. The maintain pass moves
   on immediately.

Plus a cleanup: retire the dead net_op task handlers
that earlier phases kept around. After phase 6's
maintain rewrite, the existing `_network_deploy`,
`_network_destroy`, and `_network_update_dnsmasq`
handlers on `NetOp` are no longer enqueued by anything.
Phase 4 explicitly deferred this cleanup ("Phase 6
cleans these up as part of the maintain.py rewrite").
The enum values stay (for any in-flight records still
on disk during deploy); the handlers go.

## What Phase 6 ships

1. **Three new config knobs** in `shakenfist/config.py`:
   * `MAINTAIN_QUEUE_DEPTH_THRESHOLD` (int, default 50)
   * `MAINTAIN_RECONCILE_COOLDOWN_SECONDS` (int, default 60)
   * `MAINTAIN_RECONCILE_CIRCUIT_K` (int, default 5)
   
   The existing hard-coded 30-second pass interval at
   maintain.py line 47 (`if time.time() - last_loop < 30`)
   stays the same — keep the existing behaviour unless
   we have evidence to change it.

2. **One new MariaDB helper**, three-layer pattern
   per `shakenfist/mariadb.py` conventions:
   `get_recent_terminal_op_states_for_target(
       target_object_type, target_uuid, limit,
       op_type=None) -> list[tuple[str, str, float]]`
   Returns up to `limit` most recent terminal op state
   records targeting the given object, as `(op_uuid,
   state_value, update_time)` tuples ordered newest
   first. The implementation joins
   `cluster_operation_targets` against `object_states`
   filtered to terminal states (`STATE_COMPLETE`,
   `STATE_ABORT`, `STATE_DELETED`, `STATE_ERROR`).
   This single helper services both the cooldown and
   circuit-breaker queries — cooldown asks for limit=1,
   circuit-breaker for limit=K.

3. **Rewritten `maintain.py` main loop.** The discovery
   logic (lines 54-93: querying `discover_interfaces`,
   building `host_networks`, building
   `routed_by_network`) is preserved verbatim — Phase 6
   is not weakening drift detection. The per-network
   action logic (lines 95-176) is replaced with the
   five-guard pipeline. The extra-vxlan warning logic
   at the end of the loop (lines 178-194) is preserved —
   it's discovery-only already.

4. **Retired net_op handlers**. Remove the bodies of
   `_network_deploy`, `_network_destroy`, and
   `_network_update_dnsmasq` on `NetOp`. The enum values
   (1, 2, 3) stay in the schema so on-disk in-flight
   records still parse. A small dispatcher change in
   `dispatch_task` either skips these tasks or routes
   them to the new equivalents — see step 6c for the
   approach.

5. **Documentation update** noting the maintain
   rewrite, the new config knobs, the circuit-breaker
   behaviour, and the retired handlers.

## What Phase 6 does **not** do

* No change to the `_network_apply_*` handlers or
  any of the `_apply_*` methods on `BridgedVXLanNetwork`.
  Phase 6 only changes how maintain triggers
  reconciliation, not how reconciliation runs.
* No removal of the existing `network_remove_nat`,
  `network_ensure_mesh`, or any other migrated NetOp
  task. Those are still in active use from the public
  `Network` methods.
* No `redirect_to_network_node` removal — Phase 7.
* No `get_lock` wrapper removal — Phase 8.
* No introduction of the recurring-operations
  framework. Maintain stays a thread for now. The
  master plan's future-work note about
  `PLAN-recurring-operations.md` absorbing maintain.py
  remains a separate plan.

## Key references in the existing code

* `shakenfist/daemons/network/maintain.py:42-194` —
  the entire body that's being rewritten.
* `shakenfist/daemons/network/maintain.py:54-94` —
  the discovery block to preserve verbatim.
* `shakenfist/daemons/network/maintain.py:178-194` —
  the extra-vxlan warning block to preserve verbatim.
* `shakenfist/mariadb.py` —
  `has_pending_cluster_operation` (existing, used for
  per-network gating);
  `get_work_queue_length` (existing, used for the
  queue-depth guard); the
  `cluster_operation_targets` table and the
  `object_states` table schemas; the three-layer
  accessor pattern.
* `shakenfist/operations/net_op.py:125-160` — the
  three handlers being retired
  (`_network_deploy`, `_network_destroy`,
  `_network_update_dnsmasq`). Their tasks stay in the
  enum.
* `shakenfist/schema/operations/net_op.py:27-39` —
  the `model_tasks` enum. No changes here.
* `shakenfist/config.py` — config knobs added.
* `shakenfist/operations/baseoperation.py` —
  `BaseOperation.STATE_COMPLETE`,
  `BaseOperation.STATE_ABORT`, and the terminal-state
  set used by the new query helper.

## Success criteria

Phase 6 is complete when:

* `shakenfist/daemons/network/maintain.py` contains no
  `raise_for_error()` calls. `grep -n "raise_for_error"
  shakenfist/daemons/network/maintain.py` returns
  zero hits. The maintain thread no longer blocks on
  enqueued operations.

* The four guards (queue-depth, gating, cooldown,
  circuit-breaker) are observable: a deliberately
  failing network reconciliation produces no more
  than `MAINTAIN_RECONCILE_CIRCUIT_K` ops within an
  extended observation window, and the circuit-breaker
  event surfaces in the event log.

* `shakenfist/mariadb.py` has
  `get_recent_terminal_op_states_for_target` with the
  three-layer pattern (direct + gRPC + public). Proto
  stubs regenerated via `tox -e genprotos` if any
  `.proto` files changed.

* `get_work_queue_length` is called once per maintain
  pass; if its sum across the network queue family
  exceeds `MAINTAIN_QUEUE_DEPTH_THRESHOLD`, the pass
  is skipped with an audit event.

* `_network_deploy`, `_network_destroy`, and
  `_network_update_dnsmasq` handler bodies are gone
  from `shakenfist/operations/net_op.py`. The
  task-enum values stay; the dispatcher handles the
  obsolete-task case without an unhandled
  `AttributeError`.

* `pre-commit run --all-files` passes.

* `tox -e py3` shows no regressions. The test suite
  exercises the new helpers' correctness via mocked
  MariaDB.

* cluster_ci functional suite passes on the phase 6 PR.

* `ARCHITECTURE.md`, `AGENTS.md`, and
  `docs/developer_guide/network_dispatcher.md`
  describe the discovery-only maintain pattern, the
  circuit-breaker behaviour, and the new config
  knobs.

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a. Config and helper | high | opus | none | (1) Add three new config knobs in `shakenfist/config.py` after the existing maintain-adjacent ones — look at where other operator-tuning knobs live in that file and place these alongside. The knobs: `MAINTAIN_QUEUE_DEPTH_THRESHOLD: int = Field(50, description='Maintain pass is skipped if the combined depth of the network queue family exceeds this threshold.')`, `MAINTAIN_RECONCILE_COOLDOWN_SECONDS: int = Field(60, description='If the most recent terminal reconciliation op for a network ended in ERROR within this many seconds, maintain skips enqueueing another reconciliation for that network.')`, `MAINTAIN_RECONCILE_CIRCUIT_K: int = Field(5, description='If the last K terminal reconciliations for a network all ended in ERROR, maintain quiesces that network with an operator-visible event.')`. (2) Add one new MariaDB helper `get_recent_terminal_op_states_for_target(target_object_type, target_uuid, limit, op_type=None)` in `shakenfist/mariadb.py`. Follow the three-layer pattern: `_direct_*`, `_grpc_*`, public wrapper. The query: join `cluster_operation_targets` against `object_states` (object_type='cluster_operation', object_uuid=op_uuid) filtered to terminal states (`STATE_COMPLETE`, `STATE_ABORT`, `STATE_DELETED`, `STATE_ERROR`), ordered by `update_time DESC`, limited to `limit`. Returns `list[tuple[op_uuid, state_value, update_time]]`. If `op_type` is provided, additionally filter `cluster_operation_targets.operation_type=op_type` (useful to scope to `net_op` reconciliations only and exclude unrelated cluster ops). gRPC layer: add `GetRecentTerminalOpStatesForTarget` RPC and the request/response messages to `protos/database.proto`, run `tox -e genprotos`. The handler in `shakenfist/daemons/database/main.py` mirrors the existing `has_pending_cluster_operation` handler pattern. Register a Monitor counter for the new RPC. Add unit tests in `shakenfist/tests/test_mariadb.py` (or wherever the MariaDB helper tests live — check first) covering: limit honoured; op_type filter narrows results; terminal-state filter excludes non-terminal ops; results ordered newest-first; empty result when no targets exist. Commit message subject: `mariadb: add get_recent_terminal_op_states_for_target.` |
| 6b. Rewrite maintain.py | high | opus | none | Rewrite the main loop body of `shakenfist/daemons/network/maintain.py:42-194`. Preserve the discovery block (lines 54-94) and the extra-vxlan warning block (lines 178-194) verbatim — these are not changing. Replace lines 95-176 (the per-network action block) with the five-guard pipeline. **The new shape:** Before the per-network loop, query the queue depth across the network queue families this node services. Use `get_work_queue_length` on each per-node and (where applicable) cluster-wide network queue (mirror the queue-list construction in `shakenfist/daemons/network/workitem.py` — call `get_node_network_queues(config.NODE_UUID)` always, plus `get_all_network_queues()` if `config.NODE_IS_NETWORK_NODE`). Sum the per-queue `processing + queued + deferred` counts. If the total exceeds `config.MAINTAIN_QUEUE_DEPTH_THRESHOLD`, emit an audit event ("maintain pass skipped: network queue depth N exceeds threshold M") and `continue` to the next pass. **Per-network actions become:** for each network with detected drift, in this order: (a) `has_pending_cluster_operation(target_object_type='network', target_uuid=network_uuid, op_type='net_op')` — if true, skip. (b) `get_recent_terminal_op_states_for_target('network', n.uuid, limit=1, op_type='net_op')` — if the most recent result is `STATE_ERROR` within `MAINTAIN_RECONCILE_COOLDOWN_SECONDS`, skip with an audit event. (c) `get_recent_terminal_op_states_for_target('network', n.uuid, limit=config.MAINTAIN_RECONCILE_CIRCUIT_K, op_type='net_op')` — if all K results are `STATE_ERROR`, skip and emit a prominent circuit-breaker event ("network has failed reconciliation %d times in a row; quiesced pending operator attention"). (d) Otherwise, enqueue the reconciliation directly via the schema helpers using `PRIORITY.background`: for hypervisor-side drift, `nn_create_and_enqueue(str(config.NODE_UUID), n.uuid, [nn_tasks.network_apply_create_hypervisor], PRIORITY.background)`; for network-node-side drift (when `NODE_IS_NETWORK_NODE`), `net_create_and_enqueue(network_uuid=str(n.uuid), tasks=[net_tasks.network_apply_create_network_node], priority=PRIORITY.background)` plus per-floating-IP and per-route ops via the same pattern. **Remove** all `raise_for_error()` calls — the maintain thread does not wait. **Remove** the `try/except CreateVXLANInterfaceFailed / LockException / DeadNetwork / NetworkOperationFailed / ProcessExecutionError` block — those exceptions can only fire from the synchronous code paths that have been removed. The remaining per-network code should be straight-line discovery-then-enqueue with the four guards. **Update the delete_wait cleanup** at lines 105-112 to use `network_apply_delete_network_node` (task 12) instead of `network_destroy` (task 2). The discovery block (`discover_interfaces`, `host_networks`, `routed_by_network`) and the extra-vxlan warning block stay verbatim. Add unit tests in `shakenfist/tests/test_daemon_network_maintain.py` (exists from Phase 3) covering: queue-depth guard skips the pass; pending-op gate skips a network; cooldown gate skips a network within the window; circuit-breaker fires after K consecutive failures and emits the prominent event; happy path enqueues `network_apply_create_hypervisor` (or `network_apply_create_network_node`) at background priority. Imports likely need updating — drop `CreateVXLANInterfaceFailed`, `DeadNetwork`, `LockException`, `NetworkOperationFailed`, `ProcessExecutionError` if they're no longer referenced; add `get_node_network_queues`, `get_all_network_queues`, `nn_create_and_enqueue`, `nn_tasks`, `EVENT_TYPE_AUDIT`, `mariadb`. Commit message subject: `sf-net: maintain is discovery-only.` |
| 6c. Retire dead NetOp handlers | medium | sonnet | none | Remove the bodies of three handlers in `shakenfist/operations/net_op.py`: `_network_deploy` (around line 125), `_network_destroy` (around line 135), and `_network_update_dnsmasq` (around line 148). The task-enum values `network_deploy=1`, `network_destroy=2`, and `network_update_dnsmasq=3` **stay** in `shakenfist/schema/operations/net_op.py` because cluster_operations rows persisted on disk may still reference them during a deploy. The dispatcher needs to handle the case where an in-flight op references one of these obsolete tasks. Option A (cleaner): each handler becomes a one-line `raise InvalidStateForTask(self, task)` (the existing exception type defined in this file). The outer `except Exception` in `dispatch_task` already persists an `ErrorReport` and transitions to `STATE_ERROR`. Option B (most-conservative): each handler delegates to the equivalent `_network_apply_*` handler (e.g. `_network_deploy` calls `_network_apply_create_network_node` then `_network_ensure_mesh`). **Choose Option A** — the master plan said "Phase 6 cleans these up" which is consistent with retiring them; any in-flight op at deploy time pays an ERROR transition (graceful) and the cluster operator can re-deploy networks if needed. Add a brief comment at each retired handler's location explaining the situation: "this task is no longer enqueued by any production code path as of phase 6; existing in-flight ops at deploy time gracefully ERROR via the dispatcher's outer exception handler. Phase 6 of `PLAN-network-facade.md`". Confirm `grep -rn "network_deploy\|network_destroy\|network_update_dnsmasq" shakenfist/ --include='*.py' | grep -v "test\|schema/operations" | grep -v "^.*:.*# .*"` shows no remaining enqueue sites. Update tests to remove any that exercised the retired handlers' happy paths; replace with tests that confirm the retired handlers `InvalidStateForTask`. Commit message subject: `net_op: retire deprecated reconciliation handlers.` |
| 6d. Documentation | medium | sonnet | none | Update three docs: (1) `ARCHITECTURE.md` "Network Operation Error Handling" section: append a paragraph describing the maintain.py discovery-only model and the four guards (queue-depth, gating, cooldown, circuit-breaker). Note that maintain ops use `PRIORITY.background`. Mention the retired handlers. (2) `AGENTS.md`: add a one-paragraph note to the "Network facade (Phases 2-5)" subsection (rename to "Network facade (Phases 2-6)") that maintain is now discovery-only and lists the three new config knobs. (3) `docs/developer_guide/network_dispatcher.md`: add a "Phase 6: maintain.py and the discovery-only model" section. Cover: the five-guard pipeline; the new config knobs; the new MariaDB helper `get_recent_terminal_op_states_for_target`; the circuit-breaker behaviour and how to clear it (operator does something that lets a fresh reconciliation succeed). `mkdocs.yml.tmpl` not touched (no new doc files). Commit message subject: `docs: phase 6 maintain discovery-only.` |

## Step ordering and dependencies

* 6a (config + helper) is independent and lands first. It introduces the new helper but does not yet use it.
* 6b (maintain rewrite) depends on 6a — the new helper is what powers the cooldown and circuit-breaker queries.
* 6c (retire handlers) depends on 6b — only after maintain no longer enqueues the old tasks is it safe to retire the handlers. Independent confirmation grep: `grep -rn "network_deploy\|network_destroy\|network_update_dnsmasq" shakenfist/ --include='*.py'` should show no enqueue sites in production code outside `schema/operations/net_op.py` and the now-retired handlers themselves after 6b.
* 6d (docs) lands last.

Recommended landing order: 6a → 6b → 6c → 6d.

## Back brief

Before executing any step, the implementing sub-agent must back brief the management session. Each agent should explicitly confirm:

* The "no `raise_for_error()` in maintain" invariant. The point of phase 6 is to make maintain non-blocking; any leftover `raise_for_error()` defeats the purpose.

* `get_recent_terminal_op_states_for_target` is a generic helper. It targets *any* object type, not just networks. Use `target_object_type='network'` for the maintain caller, but the helper itself is reusable.

* The queue-depth threshold is a sum across the network queue family this node services, not a per-queue limit. The agent must reproduce the queue-list construction from `shakenfist/daemons/network/workitem.py`'s `execute()` method (which already has the `NODE_IS_NETWORK_NODE` branching).

* The cooldown and circuit-breaker query the **same** helper — cooldown uses `limit=1` and checks "is the most recent terminal op an ERROR within the cooldown window?"; circuit-breaker uses `limit=K` and checks "are all K terminal ops ERROR?". Avoid duplicating the query.

* The retired handlers in step 6c become one-liners that raise `InvalidStateForTask`. Do not silently no-op or delegate to the new handlers — that would mask deploy-time leftover state and complicate later cleanup of the enum values.

* The audit event for the queue-depth guard ("maintain pass skipped: queue depth too high") fires on the *Network* object? Or on a generic system object? Pick a reasonable target — probably the Node, since the depth is a node-level observation. Phase 6 documentation should specify whichever you choose.

## Review checklist for the management session

After each step's sub-agent reports completion:

- [ ] Named files were modified; no unrelated files changed.
- [ ] `pre-commit run --files <changed files>` passes.
- [ ] New unit tests pass.
- [ ] Commit message subject ends in a period, ≤ 50 characters; body wraps at 75.
- [ ] Commit body includes the `Prompt:` paragraph and the `Co-Authored-By` / `Signed-off-by` lines.
- [ ] For step 6a: if proto files changed, the stubs are regenerated via `tox -e genprotos` and committed alongside.
- [ ] For step 6b: `grep -n "raise_for_error" shakenfist/daemons/network/maintain.py` returns zero hits.
- [ ] For step 6c: `grep -rn "network_deploy\|network_destroy\|network_update_dnsmasq" shakenfist/ --include='*.py'` outside `schema/operations/net_op.py` shows only the now-retired handlers themselves.

After all steps complete:

- [ ] cluster_ci functional smoke suite passes on the phase 6 PR.
- [ ] No new `ERROR` / `Traceback` lines in the cluster_ci stable-log gate.
- [ ] The maintain thread observably enqueues at `PRIORITY.background` (verifiable via the event log or the cluster_operations table).
- [ ] Master plan execution table for Phase 6 is updated from `Planning` to `Complete`.
