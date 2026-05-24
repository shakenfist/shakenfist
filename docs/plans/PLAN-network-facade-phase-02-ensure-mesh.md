# Phase 2: BridgedVXLanNetwork scaffold, ErrorReport, ensure_mesh migration

## Context

Phase 1 shipped the per-node queue family, exponential
back-off, and cancellation check. Phase 2 is the first
per-method migration. It establishes the pattern that
every later migration (floating-IP, dnsmasq, lifecycle)
follows, so the choices made here matter beyond the one
method being migrated.

Phase 2 ships, in order:

1. **`ErrorReport`** at
   `shakenfist/operations/error_report.py`. The
   structured failure record carried across the queue
   boundary. Includes `from_exception()` for
   worker-side construction (with the four initial
   network-exception registry entries) and `to_http()`
   for REST rendering. Persistence is a new
   append-only `cluster_operation_errors` table — see
   step 2a for the rationale.
2. **`BridgedVXLanNetwork`** at
   `shakenfist/network/bridged_vxlan_network.py`. The
   worker-only sibling of `Network`. Holds the
   `_apply_*` methods that actually mutate host
   network state. Phase 2 implements
   `_apply_ensure_mesh` only; subsequent phases add
   the other `_apply_*` methods.
3. **`poll_until_terminal` / `op.error_report` /
   `op.raise_for_error`.** The small generic helper
   that callers needing synchronous semantics use to
   block until an op reaches a terminal state, plus
   the convenience methods on `BaseClusterOperation`
   for accessing the error report and raising on
   failure.
4. **A new `network_ensure_mesh` task** on `NetOp` so
   `Network.ensure_mesh()` has something to enqueue.
   The task handler instantiates
   `BridgedVXLanNetwork(n)._apply_ensure_mesh()`.
5. **Migrate net_op.py in-worker callers.** Replace
   the two existing `n.ensure_mesh()` calls inside
   `_network_deploy` and `_network_update_dnsmasq`
   with `BridgedVXLanNetwork(n)._apply_ensure_mesh()`.
   This is the re-entrancy safety from the master plan
   — in-worker code uses the worker class, not the
   public class.
6. **Flip `Network.ensure_mesh()`.** Its body changes
   from "do the work inline" to "build a NetOp task
   for `network_ensure_mesh`, enqueue on this node's
   per-node `network` queue, return the op handle".
   The existing `with self.get_lock(...)` wrapper
   stays in `_apply_ensure_mesh` for now — Phase 8
   removes it once the queue is the sole serialisation
   point.
7. **Update external callers.** The five non-worker
   call sites of `n.ensure_mesh()` change from
   `n.ensure_mesh()` to
   `op = n.ensure_mesh(); op.raise_for_error()` —
   one extra line each. This preserves today's
   synchronous-with-exception semantics through the
   per-method migration. Later phases can introduce
   chains via `depends_on` once enough methods are
   migrated.
8. **Documentation** updates.

What Phase 2 does **not** do:

* No migration of `add_floating_ip`, `route_address`,
  `unroute_address`, `remove_nat` — Phase 3.
* No migration of `update_dnsmasq`, `remove_dnsmasq`,
  `update_dns_entry`, `remove_dns_entry`,
  `remove_dhcp_lease` — Phase 4.
* No migration of `create_on_*`, `delete_on_*` — Phase 5.
* No rewrite of `maintain.py` — Phase 6.
* No removal of the `NodeLock` wrappers in
  `_apply_ensure_mesh` — Phase 8.
* No `depends_on` chain semantics on external
  callers. They use the `op.raise_for_error()` shim
  to preserve sync behaviour during the per-method
  migration.

## Key references in the existing code

* `shakenfist/network/network.py:885-929` — current
  `Network.ensure_mesh()` body. The actual work (the
  `util_concurrency.ensure_vxlan_mesh` call plus the
  event emission and FDB diff logic) moves to
  `BridgedVXLanNetwork._apply_ensure_mesh()`. The
  `with self.get_lock(...)` block stays inside
  `_apply_ensure_mesh` until Phase 8.

* `shakenfist/operations/net_op.py:100-122` — current
  task handlers (`_network_deploy`,
  `_network_update_dnsmasq`) that call
  `n.ensure_mesh()` directly. These are in-worker
  callers and switch to
  `BridgedVXLanNetwork(n)._apply_ensure_mesh()`.

* `shakenfist/operations/net_op.py:69-98` — the
  dispatcher's `dispatch_task` method. Its outer
  `except` is where exceptions escaping
  `_apply_*` methods need to be converted to
  `ErrorReport` via `ErrorReport.from_exception()`
  and persisted on the op record. Today it just
  calls `util_exceptions.ignore_exception` and sets
  `STATE_ERROR`.

* `shakenfist/schema/operations/net_op.py:27-32` —
  the `model_tasks` enum that needs a new
  `network_ensure_mesh` entry.

* `shakenfist/operations/baseoperation.py:97-249` —
  `BaseClusterOperation` is the right home for the
  new `error_report` property and
  `raise_for_error(timeout=None)` method. The
  `poll_until_terminal` helper can be a free function
  alongside `is_outstanding` (line 242) or a method
  on the class. Either works; choose by readability.

* `shakenfist/mariadb.py:801-857` — `cluster_operations`
  table is documented as insert-only ("rows are not
  mutated after creation"). Therefore `ErrorReport`
  cannot live as a column on this table. State
  transitions go to the separate `object_states`
  table. We add a new `cluster_operation_errors`
  table for ErrorReport persistence — see step 2a.

* `shakenfist/schema/object_state.py:27-72` — the
  `State` Pydantic model has an optional `message`
  field. We deliberately do not overload this field
  with JSON; structured error data goes to the new
  table.

* `shakenfist/config.py:108-114` — `API_ASYNC_WAIT`
  (default 15 seconds) is the existing async-op
  timeout knob. The `poll_until_terminal` helper
  defaults its `timeout` parameter to this value.

* `shakenfist/deploy/shakenfist_ci/base.py:395` —
  `_await_instance_operations_complete` is the
  existing poll-based wait pattern. The new
  `poll_until_terminal` helper generalises it; the
  CI helper can later move to use the new helper as
  cleanup.

* The five external `n.ensure_mesh()` call sites:
  - `shakenfist/instance.py:2022`
  - `shakenfist/operations/node_inst_netdesc_op.py:243`
  - `shakenfist/daemons/queues/startup_tasks.py:135`
  - `shakenfist/daemons/network/maintain.py:153`
  Each becomes
  `op = n.ensure_mesh(); op.raise_for_error()`.

## Success criteria

Phase 2 is complete when:

* `ErrorReport` exists at
  `shakenfist/operations/error_report.py` with
  `code`, `message`, `details`, `origin_class`, and
  `traceback` fields. `ErrorReport.from_exception()`
  maps the four registered network exceptions
  (`EnsureMeshFailed`, `DeadNetwork`,
  `CreateVXLANInterfaceFailed`,
  `CannotAssignFloatingGateway`) to stable codes;
  unknown exceptions become `internal.unknown` with
  the original class preserved. `to_http()` maps
  each registered code to a sensible HTTP status
  + body dict.
* A `cluster_operation_errors` table exists in
  MariaDB with `op_uuid` (primary key, FK to
  `cluster_operations.uuid`), `error_report` (JSON),
  and `created_at`. The schema is registered via the
  same pattern as the other cluster-operation tables
  (`_ensure_<x>_schema` in `mariadb.py`). A migration
  test confirms the table is created on a clean
  install.
* `BridgedVXLanNetwork` exists at
  `shakenfist/network/bridged_vxlan_network.py`. Its
  constructor takes a `Network` instance.
  `_apply_ensure_mesh()` implements the host-mutating
  body that was previously inside
  `Network.ensure_mesh()`, including the existing
  `get_lock` wrapper, the FDB diff computation, the
  `util_concurrency.ensure_vxlan_mesh` call, and the
  event emission. Raises the same typed exceptions
  (`EnsureMeshFailed`, `DeadNetwork`) it does today.
* `BaseClusterOperation` exposes
  `op.error_report` (returns the `ErrorReport`
  loaded from the new table, or `None`) and
  `op.raise_for_error(timeout=None)` (polls until
  terminal, then if state is `ERROR` raises
  `NetworkOperationFailed(error_report=...)`,
  otherwise returns silently). A
  `poll_until_terminal(op, timeout=None)` helper
  exists (as a standalone function or method) and
  is used by `raise_for_error`. Default timeout
  comes from `config.API_ASYNC_WAIT`.
* `NetworkOperationFailed` exception exists (likely
  in `shakenfist/exceptions.py`), carries the
  `ErrorReport` as an attribute, and renders the
  report's message in `__str__`.
* `NetOp` schema has a new task
  `network_ensure_mesh`. `NetOp.dispatch_task`
  invokes the handler
  `_network_ensure_mesh(self, n)` which instantiates
  `BridgedVXLanNetwork(n)` and calls
  `_apply_ensure_mesh()`. On exception, the
  dispatcher's outer except clause writes an
  `ErrorReport` row via the new table helper before
  setting `STATE_ERROR`.
* `net_op.py` in-worker callers (`_network_deploy`,
  `_network_update_dnsmasq`) call
  `BridgedVXLanNetwork(n)._apply_ensure_mesh()`
  directly, not `n.ensure_mesh()`. Re-entrancy
  through the queue is structurally impossible.
* `Network.ensure_mesh()` body changes from
  `util_concurrency.ensure_vxlan_mesh` to
  building a `NetOp` task with
  `task=network_ensure_mesh`, enqueueing at
  `target=config.NODE_UUID, family='network'`,
  priority `user_facing`, and returning the
  enqueued op handle. No host mutation happens
  inside `Network.ensure_mesh()` anymore.
* The five external `n.ensure_mesh()` call sites
  are updated to `op = n.ensure_mesh();
  op.raise_for_error()`, preserving today's
  synchronous-with-exception semantics.
* `pre-commit run --all-files` passes.
* `tox -e py3` shows no regressions.
* The cluster_ci functional suite passes on the
  phase 2 PR.
* `grep -n "util_concurrency.ensure_vxlan_mesh"
  shakenfist/network/network.py` returns nothing
  — the call site has moved entirely to
  `BridgedVXLanNetwork`.
* `ARCHITECTURE.md`, `AGENTS.md`, and the developer
  guide describe `ErrorReport`,
  `BridgedVXLanNetwork`, the `op.error_report` /
  `op.raise_for_error` API, and the per-method
  migration pattern. The dev guide page
  `network_dispatcher.md` (created in phase 1)
  gains a section on how the dispatcher translates
  in-worker exceptions to `ErrorReport`.

## Step-level guidance

Each step is its own commit on the phase branch. Steps
2a, 2b, 2c are independent prerequisites for the
later steps; 2d depends on 2b and 2c; 2e depends on
2b; 2f depends on 2d; 2g depends on 2f; 2h after all.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a. ErrorReport + persistence | high | opus | none | Create `shakenfist/operations/error_report.py` with the `ErrorReport` dataclass (or Pydantic model, matching existing conventions in `shakenfist/schema/`): fields `code: str`, `message: str`, `details: dict[str, Any]`, `origin_class: str`, `traceback: str`. Add `ErrorReport.from_exception(exc) -> ErrorReport` with a module-level registry `_EXCEPTION_CODE_REGISTRY: dict[type[Exception], str]` mapping the four network exceptions to codes (`network.ensure_mesh.failed`, `network.dead`, `network.create_vxlan.failed`, `network.floating.assign_failed`). Unknown exceptions become `code='internal.unknown'` with `origin_class` set to the fully qualified class path. Add `ErrorReport.to_http() -> tuple[int, dict[str, Any]]` returning `(http_status, body_dict)`; the mapping table inside the method maps known codes to status codes (e.g. `network.dead` → 410 Gone; `network.ensure_mesh.failed` → 500; `internal.unknown` → 500). Add a new MariaDB table `cluster_operation_errors` via `_get_cluster_operation_errors_table()` in `shakenfist/mariadb.py`, with columns: `op_uuid` (sa.Uuid primary key, FK to cluster_operations.uuid), `error_report` (sa.JSON, not null), `created_at` (sa.Double, not null). Register the table in the existing schema-ensure pattern (look at `_ensure_cluster_operations_schema` and add an analogous `_ensure_cluster_operation_errors_schema`; wire it into the schema-ensure entry point alongside the other tables). Add public functions `set_cluster_operation_error(op_uuid, error_report)` (inserts a row, JSON-serialising the report) and `get_cluster_operation_error(op_uuid) -> Optional[ErrorReport]` (selects + deserialises). Both go through the three-layer pattern (`_direct_`, `_grpc_`, public) per `shakenfist/mariadb.py` conventions; the gRPC piece needs corresponding protos and a database-daemon handler — see `daemons/database/main.py` for the pattern. **Note on proto regeneration:** if proto files change, run `tox -e genprotos` and commit the regenerated stubs. Add unit tests for `from_exception` (each registered exception, plus a generic Exception), `to_http` (each registered code), table creation, and round-trip persistence. Commit message subject: `operations: introduce ErrorReport for queue-boundary failures.` |
| 2b. BridgedVXLanNetwork scaffold | high | opus | none | Create `shakenfist/network/bridged_vxlan_network.py`. The class wraps a `Network` instance: `__init__(self, network: Network)` stores the network. Move the body of `Network.ensure_mesh()` (network.py:885-929) into a new method `_apply_ensure_mesh(self) -> None` on `BridgedVXLanNetwork`. Inside `_apply_ensure_mesh`: keep the existing `with self.network.get_lock(op='Network ensure mesh', global_scope=False):` wrapper (Phase 8 removes it once the queue is the sole serialisation point); the FDB diff computation; the `util_concurrency.ensure_vxlan_mesh` call; the `add_event` calls; the typed exceptions raised on failure (`EnsureMeshFailed`, `DeadNetwork`). All references to `self.uuid`, `self.vxid`, etc. become `self.network.uuid`, `self.network.vxid`. Do **not** modify `Network.ensure_mesh()` in this step — that's step 2f. The intent of this step is to create the new class alongside the old code path. Add a module-level docstring explaining the class's role: instantiated only inside the workitem dispatcher, holds `_apply_*` host-mutating methods, paired with `Network` which is the public enqueueing facade. Add unit tests that mock the privexec layer and confirm `_apply_ensure_mesh` calls `util_concurrency.ensure_vxlan_mesh` with the expected arguments computed from the wrapped Network. Commit message subject: `network: add BridgedVXLanNetwork with _apply_ensure_mesh.` |
| 2c. poll helper + op API | high | opus | none | Add `poll_until_terminal(op, timeout=None)` either as a standalone function in `shakenfist/operations/baseoperation.py` or as a method on `BaseClusterOperation`. Default `timeout=config.API_ASYNC_WAIT`. The helper polls `mariadb.get_cluster_operation(op_uuid)` at a short interval (e.g. 0.1 s) until the op's state is in `{STATE_COMPLETE, STATE_ABORT, STATE_DELETED, STATE_ERROR}` or `timeout` elapses. If timeout elapses, raise `OperationTimeout` (add to `shakenfist/exceptions.py` if absent). On terminal state, return the refreshed op. Add `op.error_report` as a property on `BaseClusterOperation` that calls `mariadb.get_cluster_operation_error(self.uuid)` and returns the `ErrorReport` (or `None`). Add `op.raise_for_error(timeout=None)`: calls `poll_until_terminal(self, timeout)`, then if the resulting state value is `dbo.STATE_ERROR`, raises `NetworkOperationFailed(error_report=self.error_report)`. Add `NetworkOperationFailed(Exception)` to `shakenfist/exceptions.py` with `__init__(self, error_report)`, `self.error_report = error_report`, and `__str__` rendering the report's code + message. Existing test pattern at `shakenfist/deploy/shakenfist_ci/base.py:395` is similar to what the helper provides; do not modify that file in this step (its eventual cleanup to use the new helper is out of scope here). Add unit tests covering: terminal-state transition is observed; timeout raises `OperationTimeout`; raise_for_error raises `NetworkOperationFailed` on ERROR state with the right report attached; raise_for_error returns silently on COMPLETE state. Commit message subject: `operations: add poll_until_terminal and op error API.` |
| 2d. network_ensure_mesh task | medium | sonnet | none | Add a new task `network_ensure_mesh` to the `model_tasks` enum in `shakenfist/schema/operations/net_op.py:27`. Position it at the end (after `network_remove_nat=5`) with value `6` to preserve existing enum values. The schema regeneration via `tox -e genprotos` may be needed if model_tasks affects protobuf — check whether `model_tasks` is serialised as part of the protobuf surface (read `tox.ini` and `tools/` for the gen-protos invocation). Add the dispatcher handler `_network_ensure_mesh(self, n)` in `shakenfist/operations/net_op.py` alongside the other task handlers. The handler imports `BridgedVXLanNetwork` from `shakenfist.network.bridged_vxlan_network` and runs `BridgedVXLanNetwork(n)._apply_ensure_mesh()`. The dispatcher's outer try/except in `dispatch_task` (lines 81-98) needs adjustment: on exception, before setting `STATE_ERROR`, call `mariadb.set_cluster_operation_error(self.uuid, ErrorReport.from_exception(exc))` so the report is persisted. Existing typed-exception handling (`EnsureMeshFailed`, `CreateVXLANInterfaceFailed`) remains unchanged behaviourally — those branches still set `STATE_ERROR`, just now they also persist a report first. Add unit tests: a fake `NetOp` with task `network_ensure_mesh` dispatches into the handler; mocked `_apply_ensure_mesh` is called; when it raises, the op transitions to ERROR and a report is persisted. Commit message subject: `net_op: add network_ensure_mesh task and ErrorReport wiring.` |
| 2e. Migrate net_op in-worker callers | medium | sonnet | none | In `shakenfist/operations/net_op.py`, update `_network_deploy` (currently line 100) and `_network_update_dnsmasq` (currently line 120) to use `BridgedVXLanNetwork`. Each method instantiates a single `BridgedVXLanNetwork(n)` at the top and calls `bvn._apply_ensure_mesh()` instead of `n.ensure_mesh()`. Other calls in those handlers (`n.create_on_network_node()`, `n.remove_dnsmasq()`, etc.) are **not yet migrated** — they stay as-is on `Network` because those methods aren't migrated until later phases. The intent here is purely to ensure that the in-worker callers of `ensure_mesh` use the worker class so that step 2f (flipping Network.ensure_mesh() to enqueue) does not cause re-entrancy. Add a unit test confirming that `_network_deploy` calls `BridgedVXLanNetwork._apply_ensure_mesh` (not `Network.ensure_mesh`) by patching both and asserting which one is called. Commit message subject: `net_op: route in-worker ensure_mesh through BridgedVXLanNetwork.` |
| 2f. Flip Network.ensure_mesh() | high | opus | none | Replace the body of `Network.ensure_mesh()` (network.py:885-929) with code that builds a `NetOp` task and enqueues it. The new body looks roughly like: `from shakenfist.schema.operations import net_op as net_op_schema; from shakenfist.schema.operations.baseclusteroperation import PRIORITY; ... op_type, op_uuid = net_op_schema.create_and_enqueue(network_uuid=str(self.uuid), tasks=[net_op_schema.model_tasks.network_ensure_mesh], priority=PRIORITY.user_facing); from shakenfist.constants import get_object_class; return get_object_class(op_type).from_db(op_uuid)`. The function now returns the `NetOp` instance (loaded from DB so callers can `raise_for_error()` on it). **Critical:** the enqueue must use the calling node's per-node queue. Look at `shakenfist/schema/operations/util.py:enqueue_cluster_operation` to confirm how `target` and `family` propagate; for ensure_mesh we need `target=config.NODE_UUID, family='network'`. The `create_and_enqueue` wrapper in `net_op.py` does not currently expose `family` — extend it with a `family='clusteroperation'` parameter (matching the underlying `enqueue_cluster_operation` shape) and pass `family='network'` from the new Network.ensure_mesh body. Remove the now-dead `_not_on_floating_network` decorator usage if it doesn't make sense for the enqueueing version (read the decorator and decide). Keep the public method name and signature; the only externally observable change is the return value. Update the docstring to describe the new contract: "enqueues an ensure_mesh task and returns the operation handle; callers that need to block on completion call `op.raise_for_error()`." Add unit tests: calling `n.ensure_mesh()` enqueues exactly one `NetOp` with task `network_ensure_mesh` and priority `user_facing` on this node's per-node `network` queue; no `util_concurrency.ensure_vxlan_mesh` call is made directly. Commit message subject: `network: Network.ensure_mesh now enqueues a NetOp.` |
| 2g. Update external callers | medium | sonnet | none | Update the five external call sites listed in the Key references section so each `n.ensure_mesh()` becomes `op = n.ensure_mesh(); op.raise_for_error()`. The five sites are: `shakenfist/instance.py:2022`, `shakenfist/operations/node_inst_netdesc_op.py:243`, `shakenfist/daemons/queues/startup_tasks.py:135`, `shakenfist/daemons/network/maintain.py:153`. (The two net_op.py sites are in-worker and already migrated in step 2e.) Each update is a one-line addition; preserve surrounding logic. For `maintain.py:153`, note that Phase 6 will rewrite this function entirely; the goal here is just to keep it working through the per-method migration, so a minimal mechanical change is appropriate. Add a focused regression test for `node_inst_netdesc_op.py` (or whichever site has the cleanest seam) that confirms a `raise_for_error` failure surfaces as `NetworkOperationFailed` rather than the original `EnsureMeshFailed`. Commit message subject: `network: callers of ensure_mesh use op.raise_for_error.` |
| 2h. Documentation | medium | sonnet | none | Update `ARCHITECTURE.md` to describe `ErrorReport` (the structured failure record at the queue boundary, the registry pattern, the `to_http` mapping), `BridgedVXLanNetwork` (the worker-only sibling of `Network`, instantiated only inside the dispatcher), and the per-method migration pattern (callers see synchronous semantics via `op.raise_for_error()` during migration; chains via `depends_on` come later). Update `AGENTS.md` to point at the new files. Extend `docs/developer_guide/network_dispatcher.md` (created in phase 1) with a section on how the dispatcher translates exceptions to `ErrorReport` at the queue boundary, including the principle "errors are data, never rehydrated exceptions" and the convergence with gRPC's status-code model. Update `mkdocs.yml.tmpl` if any new doc file is added (per phase 1's convention, never edit `mkdocs.yml` directly). Commit message subject: `docs: describe ErrorReport and BridgedVXLanNetwork.` |

## Step ordering and dependencies

* 2a, 2b, 2c are independent of each other and can land
  in any order (or in parallel sub-agents if isolation
  allows — but the implementations touch different files,
  so direct-tree parallel is safe).
* 2d depends on 2a (uses `ErrorReport`) and 2b (calls
  `BridgedVXLanNetwork`).
* 2e depends on 2b (uses `BridgedVXLanNetwork`).
* 2f depends on 2d (uses the new task) and on 2e being
  in place (otherwise the net_op in-worker handlers
  would now enqueue when `Network.ensure_mesh()` flips,
  causing re-entrancy through the queue).
* 2g depends on 2c (`op.raise_for_error()`) and 2f
  (the new return value).
* 2h after all.

Recommended landing order on the phase branch:
2a → 2b → 2c → 2d → 2e → 2f → 2g → 2h.

## Back brief

Before executing any step, the implementing sub-agent
must back brief the management session as to its
understanding of the step and how its planned changes
align with this phase plan and the master plan. The
back brief should explicitly confirm:

* The single safety property step 2f relies on: at the
  moment `Network.ensure_mesh()` flips to enqueue, every
  in-worker caller must already be using
  `BridgedVXLanNetwork` (step 2e). Otherwise the
  net-worker enqueues to itself and deadlocks.
* `ErrorReport.from_exception` covers all four
  registered exceptions and falls back cleanly for the
  unknown case.
* `op.raise_for_error()` is bounded by
  `config.API_ASYNC_WAIT` — it does not block forever.
* The `_apply_ensure_mesh` body keeps the existing
  `get_lock` wrapper; Phase 8 removes it.

## Review checklist for the management session

After each step's sub-agent reports completion:

- [ ] Named files were modified; no unrelated files
      changed.
- [ ] `pre-commit run --files <changed files>` passes.
- [ ] New unit tests pass via `tox -e py3 -- <module>`.
- [ ] The commit message subject ends in a period, is
      no longer than 50 characters, and the body wraps
      at 75 characters per `CLAUDE.md` conventions.
- [ ] The commit body includes the `Prompt:` paragraph
      and the `Co-Authored-By` and `Signed-off-by` lines.
- [ ] For step 2a: the protos for the new
      cluster-operation-errors gRPC endpoints were
      regenerated via `tox -e genprotos` and committed.
- [ ] For step 2f: a follow-up grep
      (`grep -rn "util_concurrency.ensure_vxlan_mesh"
      shakenfist/`) shows the call site has moved
      entirely to `BridgedVXLanNetwork`; the old call
      site in `Network` is gone.

After all steps complete:

- [ ] cluster_ci functional smoke suite passes on the
      phase 2 PR.
- [ ] No new `ERROR` / `Traceback` lines in the
      cluster_ci stable-log gate.
- [ ] Master plan execution table for Phase 2 is
      updated from `Planning` to `Complete`.
