# Phase 3: Floating-IP and route migration

## Context

Phase 2 shipped the `Network` / `BridgedVXLanNetwork`
split, the `ErrorReport` model, the poll helper, and
migrated `ensure_mesh` end-to-end. Phase 3 follows the
same pattern for the remaining network-node-only
single-network mutation methods.

The methods covered in this phase:

* `Network.add_floating_ip(floating, inner, affected_objects)`
* `Network.remove_floating_ip(floating, inner, affected_objects)`
* `Network.route_address(ip)`
* `Network.unroute_address(ip)`
* `Network.remove_nat()`

All five mutate state owned by the elected network node
(floating-IP allocation, NAT/iptables rules, routes on
the egress veth bridge). They go to the cluster-wide
`networknode-clusteroperation-*` queue family, not the
per-node `network` family added in Phase 1 (which is for
per-hypervisor mutations like `ensure_mesh`).

Phase 3 is more interesting than Phase 2 because the
fan-out is wider. The existing codebase already has
multiple op types that internally call these `Network`
methods from their dispatchers:

* `net_op` (the same op type Phase 2 extended)
  contains the `_network_remove_nat` task handler
  (calls `n.remove_nat()`).
* `net_ip_op` exists specifically for the
  `route_address` / `unroute_address` tasks; its
  dispatcher calls `n.route_address(ip)` /
  `n.unroute_address(ip)`.
* `net_iface_op` has the `interface_float` task whose
  handler calls `n.add_floating_ip(floating, ni.ipv4,
  [self, n, ni])`.
* `net_iface_ip_op` has the `interface_defloat` task
  whose handler calls `n.remove_floating_ip(...)`.

All four dispatchers run inside the net-worker on the
elected network node — they are in-worker callers and
must route through `BridgedVXLanNetwork` to avoid
re-entrancy when `Network.X()` flips to enqueue.

External (non-dispatcher) callers of these methods, after
Phase 2 landed, are sparse. `Network.delete_on_network_node`
calls `self.remove_nat()` from inside the `Network` class
itself (this becomes a call into `BridgedVXLanNetwork` in
Phase 5 when `delete_on_network_node` migrates).
`maintain.py` lines 138 and 146 call `n.add_floating_ip(...)`
and `n.route_address(...)` directly — these need updating
in this phase.

The REST API does **not** call these `Network` methods
directly today. It enqueues `net_ip_op`, `net_iface_op`,
or `net_iface_ip_op` (depending on the endpoint), which
then dispatches into the Network method. So the API path
is unaffected by changes to `Network.add_floating_ip()`
etc. The existing API → operation-type-queue → dispatcher
path stays in place; only the dispatcher's *implementation*
changes (route through `BridgedVXLanNetwork`).

What Phase 3 ships, in order:

1. `_apply_add_floating_ip`, `_apply_remove_floating_ip`,
   `_apply_route_address`, `_apply_unroute_address`, and
   `_apply_remove_nat` on `BridgedVXLanNetwork`. The
   bodies are the lift-and-shift of today's `Network`
   method bodies; the existing `get_lock` wrappers stay
   in place until Phase 8.

2. Two new tasks on `NetOp`: `network_add_floating_ip`
   and `network_remove_floating_ip`. The existing
   `network_remove_nat` task is reused. Tasks
   `route_address` / `unroute_address` on `net_ip_op`
   are reused.

3. Two new optional fields on the `NetOp` model:
   `floating_address` and `inner_address`. The version
   bumps from 1 to 2; existing version-1 records still
   parse because the new fields are optional and the
   model's version range becomes `[1, 2]`.

4. Updated dispatcher handlers in `net_op.py`,
   `net_ip_op.py`, `net_iface_op.py`, and
   `net_iface_ip_op.py` — each now instantiates a
   `BridgedVXLanNetwork(n)` and calls the appropriate
   `_apply_*` method. Each dispatcher's outer `except`
   persists an `ErrorReport` via
   `mariadb.set_cluster_operation_error(...)` before
   setting `STATE_ERROR`, mirroring the Phase 2 wiring
   on `NetOp`.

5. `Network.add_floating_ip`, `Network.remove_floating_ip`,
   `Network.route_address`, `Network.unroute_address`, and
   `Network.remove_nat` flip from inline work to
   enqueue+return:
   - `add_floating_ip` and `remove_floating_ip` enqueue
     `NetOp` tasks `network_add_floating_ip` /
     `network_remove_floating_ip` with the
     `floating_address` and `inner_address` fields
     populated, `target='networknode'`,
     `family='clusteroperation'` (the existing default).
   - `route_address` and `unroute_address` enqueue
     `net_ip_op` with task `route_address` /
     `unroute_address`. The existing
     `net_ip_op.create_and_enqueue` helper is used
     directly.
   - `remove_nat` enqueues `NetOp` with task
     `network_remove_nat` (existing).
   All five return the enqueued op so callers can call
   `op.raise_for_error()`.

6. External callers in `maintain.py` lines 138 and 146
   change from
   ```python
   n.add_floating_ip(...)
   n.route_address(addr)
   ```
   to
   ```python
   add_op = n.add_floating_ip(...)
   add_op.raise_for_error()
   route_op = n.route_address(addr)
   route_op.raise_for_error()
   ```

7. `ErrorReport` registry gains entries for the
   floating-IP exceptions:
   - `AddFloatingIPFailed` -> `network.floating.add_failed`
   - `RemoveFloatingIPFailed` -> `network.floating.remove_failed`
   - `EnableNATFailed` -> `network.nat.enable_failed`
     (registered now for Phase 5's use; the exception
     is raised from `_apply_enable_nat` which lives on
     `BridgedVXLanNetwork` as an internal helper)
   - `CongestedNetwork` -> `network.congested`
   - `CreateNetworkNamespaceFailed` ->
     `network.create_namespace.failed` (Phase 5 needs
     it but the registry entry is cheap to add now)
   - `ListingInterfaceAddressesFailed` ->
     `network.list_interface_addresses.failed`

8. Documentation update.

What Phase 3 does **not** do:

* No migration of `create_on_*`, `delete_on_*`,
  `assign_floating_gateway`, `unassign_floating_gateway`,
  `enable_nat` — Phase 5.
* No migration of dnsmasq methods — Phase 4.
* No rewrite of `maintain.py` — Phase 6.
* No removal of `NodeLock` wrappers or
  `redirect_to_network_node` — Phases 7 and 8.
* No change to the REST API path. The existing API →
  net_ip_op / net_iface_op / net_iface_ip_op enqueue
  remains exactly as-is; only the dispatcher
  implementations under those op types change.

## Key references in the existing code

* `shakenfist/network/network.py:817-824` — `enable_nat`
  and `remove_nat` definitions. Phase 3 migrates
  `remove_nat`; `enable_nat` becomes a
  `_apply_enable_nat` on `BridgedVXLanNetwork` in this
  phase only because the registry entry is added, but
  the public `Network.enable_nat` is not flipped (it
  was never publicly used; it was always internal to
  `create_on_network_node`, which is Phase 5).
* `shakenfist/network/network.py:908-958` — the four
  floating-IP / route method bodies. The host-mutating
  logic moves to `BridgedVXLanNetwork`.
* `shakenfist/operations/net_op.py:142` — current
  `_network_remove_nat` handler. Updates to use
  `BridgedVXLanNetwork(n)._apply_remove_nat()`.
* `shakenfist/operations/net_ip_op.py:92-99` — current
  `_route_address` / `_unroute_address` handlers.
  Update to use `BridgedVXLanNetwork`.
* `shakenfist/operations/net_iface_op.py:109-119` —
  current `_interface_float` handler. Update to use
  `BridgedVXLanNetwork`.
* `shakenfist/operations/net_iface_ip_op.py:111-113` —
  current `_interface_defloat` handler. Update to use
  `BridgedVXLanNetwork`.
* `shakenfist/daemons/network/maintain.py:138, 146` —
  external callers requiring update.
* `shakenfist/network/network.py:753` — internal
  `self.remove_nat()` call inside `Network`. This sits
  inside the `Network` class body and will be
  addressed in Phase 5 (when `delete_on_network_node`
  itself migrates). For Phase 3 we leave this internal
  call as-is and update only the public surface.
* `shakenfist/schema/operations/net_op.py:27-33` —
  `model_tasks` enum that gains two new entries.
* `shakenfist/schema/operations/net_op.py:35-48` —
  `model` BaseModel that gains two optional fields
  and bumps `current_version`.
* `shakenfist/operations/error_report.py` —
  `_EXCEPTION_CODE_REGISTRY` to extend.
* `shakenfist/exceptions.py:364-402` — the relevant
  exception classes (already defined).

## Success criteria

Phase 3 is complete when:

* The five `_apply_*` methods exist on
  `BridgedVXLanNetwork` with bodies lifted from today's
  `Network` method bodies (existing `get_lock`
  wrappers preserved for now).
* `Network.add_floating_ip()`,
  `Network.remove_floating_ip()`,
  `Network.route_address()`,
  `Network.unroute_address()`, and
  `Network.remove_nat()` all enqueue a cluster
  operation and return the op handle. None of them
  invoke `util_concurrency.*` host-mutating helpers
  directly anymore.
* `grep -n "util_concurrency.add_floating_ip\|
  util_concurrency.remove_floating_ip"
  shakenfist/network/network.py` returns zero hits.
  (`util_concurrency.execute` for route-add is also
  gone from `Network`.)
* The four in-worker dispatchers
  (`net_op._network_remove_nat`,
  `net_ip_op._route_address`,
  `net_ip_op._unroute_address`,
  `net_iface_op._interface_float`,
  `net_iface_ip_op._interface_defloat`) all use
  `BridgedVXLanNetwork(n)._apply_*` rather than the
  `Network` methods. `grep -rn "n\.add_floating_ip\|
  n\.remove_floating_ip\|n\.route_address\|
  n\.unroute_address\|n\.remove_nat"
  shakenfist/operations/` returns zero hits.
* Each dispatcher's outer `except` persists an
  `ErrorReport` via
  `mariadb.set_cluster_operation_error` before setting
  `STATE_ERROR`, mirroring `net_op.dispatch_task` from
  Phase 2.
* The `ErrorReport` registry includes the new entries
  named above.
* `maintain.py` lines 138 and 146 use
  `op.raise_for_error()` after the enqueueing call.
* `pre-commit run --all-files` passes.
* `tox -e py3` shows no regressions.
* cluster_ci functional suite passes on the phase 3 PR.
* `ARCHITECTURE.md`, `AGENTS.md`, and the developer
  guide note that floating-IP and route methods are
  now part of the migrated set.

## Step-level guidance

Each step is its own commit. Steps 3a-3c are
independent; 3d depends on 3a, 3b, and 3c; 3e depends
on 3d; 3f after 3e.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a. NetOp schema extensions | medium | sonnet | none | In `shakenfist/schema/operations/net_op.py`: (1) Add `network_add_floating_ip = 7` and `network_remove_floating_ip = 8` to the `model_tasks` enum, after the existing `network_ensure_mesh = 6`. (2) Add two new optional fields to the `model` BaseModel: `floating_address: Optional[str] = None` and `inner_address: Optional[str] = None`. (3) Bump `current_version` from 1 to 2; leave `initial_version` at 1. The `version` field's `Field(ge=initial_version, le=current_version)` constraint expands from `[1,1]` to `[1,2]`. (4) Add `floating_address` and `inner_address` as parameters to `create_and_enqueue(...)`, defaulting to `None`, and pass them through to the model construction. Confirm no existing caller passes these kwargs (`grep -rn "net_op.create_and_enqueue\|net_op_schema\.create_and_enqueue" shakenfist/`). (5) Check whether the protobuf surface uses `model_tasks` enum values — grep `shakenfist/protos/` for any existing task names. If found, run `tox -e genprotos` and commit the regenerated stubs alongside. Add unit tests covering: enum value of `network_add_floating_ip` is 7 and `network_remove_floating_ip` is 8; model accepts version=2 with floating fields populated; model rejects version=3; model accepts version=1 (no floating fields). Commit message subject: `schema: extend NetOp for floating-IP tasks.` |
| 3b. BridgedVXLanNetwork _apply methods | high | opus | none | Extend `shakenfist/network/bridged_vxlan_network.py` with five new methods: `_apply_add_floating_ip(self, floating_address, inner_address) -> None`, `_apply_remove_floating_ip(self, floating_address, inner_address) -> None`, `_apply_route_address(self, ip) -> None`, `_apply_unroute_address(self, ip) -> None`, `_apply_remove_nat(self) -> None`. Each body is lifted from the corresponding `Network` method (`shakenfist/network/network.py:908-985` for the floating/route methods, `network.py:823-839` for `remove_nat`). Important: rewrite `self.foo` references to `self.network.foo`. Keep the existing `with self.network.get_lock(...)` wrappers; Phase 8 removes them. The `affected_objects` parameter in today's `add_floating_ip` / `remove_floating_ip` is **not** part of the `_apply_*` signatures — event correlation is the dispatcher's responsibility, not the apply layer's. (The dispatchers that today pass `affected_objects` will emit their own correlated events at dispatch time, with whatever objects they have in scope.) Add unit tests mirroring Phase 2's test structure: mock the `util_concurrency.*` calls and confirm each `_apply_*` calls into them with the expected arguments. Also test that `_apply_route_address` raises `DeadNetwork` when the network is dead (read the existing body to confirm this is how it works today). Commit message subject: `network: add floating-IP and route apply methods.` |
| 3c. ErrorReport registry extensions | low | sonnet | none | In `shakenfist/operations/error_report.py`, extend the `_EXCEPTION_CODE_REGISTRY` with: `AddFloatingIPFailed -> 'network.floating.add_failed'`, `RemoveFloatingIPFailed -> 'network.floating.remove_failed'`, `EnableNATFailed -> 'network.nat.enable_failed'`, `CongestedNetwork -> 'network.congested'`, `CreateNetworkNamespaceFailed -> 'network.create_namespace.failed'`, `ListingInterfaceAddressesFailed -> 'network.list_interface_addresses.failed'`. Import the exceptions from `shakenfist.exceptions` — grep first to confirm class names. Extend `to_http()` so each new code maps to a sensible HTTP status: `network.floating.add_failed` -> 500; `network.floating.remove_failed` -> 500; `network.nat.enable_failed` -> 500; `network.congested` -> 503 (Service Unavailable — the system can't currently fulfil the request but might be able to later); `network.create_namespace.failed` -> 500; `network.list_interface_addresses.failed` -> 500. Extend `shakenfist/tests/test_error_report.py` with parameterised tests covering: each new exception maps to its expected code in `from_exception`; each new code renders the expected HTTP status in `to_http`. Commit message subject: `operations: register floating-IP exception codes.` |
| 3d. In-worker dispatcher migration | high | opus | none | Update four op-type dispatchers to use `BridgedVXLanNetwork(n)._apply_*` instead of `n.X(...)`. The files and methods: (1) `shakenfist/operations/net_op.py` — `_network_remove_nat`: change `n.remove_nat()` to `BridgedVXLanNetwork(n)._apply_remove_nat()`. Add a new `_network_add_floating_ip(self, n)` handler that reads `self.floating_address` and `self.inner_address` (note: NetOp needs to expose these as properties — read static_values in `__init__` the same way `network_uuid` is exposed). Add a new `_network_remove_floating_ip(self, n)` handler similarly. (2) `shakenfist/operations/net_ip_op.py` — `_route_address(self, n)` and `_unroute_address(self, n)`: change to `BridgedVXLanNetwork(n)._apply_route_address(self.ip)` and `_apply_unroute_address(self.ip)`. (3) `shakenfist/operations/net_iface_op.py` — `_interface_float(self, n, ni)`: change `n.add_floating_ip(floating, ni.ipv4, [self, n, ni])` to `BridgedVXLanNetwork(n)._apply_add_floating_ip(floating, ni.ipv4)`. The handler then emits the multi-target audit event explicitly (call `add_event_multi(EVENT_TYPE_AUDIT, [self, n, ni], 'floating-IP attach', extra={...})`) to preserve today's event-correlation behaviour. Inspect what `Network.add_floating_ip` emits today (network.py:908-921) to match. (4) `shakenfist/operations/net_iface_ip_op.py` — `_interface_defloat(self, n, ni)`: similar pattern; emit the multi-target event explicitly using `[ni, ('instance', ni.instance_uuid)]`, then call `BridgedVXLanNetwork(n)._apply_remove_floating_ip(self.ip, ni.ipv4)`. **Important:** also wire `ErrorReport` persistence into each dispatcher's outer `except` clause, mirroring the Phase 2 net_op pattern: before `self.state = ...STATE_ERROR`, call `mariadb.set_cluster_operation_error(str(self.uuid), ErrorReport.from_exception(e))`. The three op-type dispatchers (`net_ip_op`, `net_iface_op`, `net_iface_ip_op`) currently have a single generic `except Exception` block (read each file) — extend that block with the persistence call. Imports of `BridgedVXLanNetwork`, `ErrorReport`, and `mariadb` need adding at the top of each file. Add focused unit tests for each handler that asserts the dispatcher routes to `BridgedVXLanNetwork` rather than `Network`, and that ErrorReport persistence fires on exception. Commit message subject: `operations: route floating-IP dispatchers through BridgedVXLanNetwork.` |
| 3e. Flip Network methods | high | opus | none | Replace the bodies of five `Network` methods to enqueue rather than perform work inline. Each returns the enqueued op handle. (1) `Network.add_floating_ip(floating_address, inner_address, affected_objects)` body becomes: emit a synchronous "requesting add floating IP" audit event on the caller's `affected_objects` (preserves today's correlation for callers), build a NetOp with `tasks=[model_tasks.network_add_floating_ip]`, `floating_address=floating_address`, `inner_address=inner_address`, `priority=PRIORITY.user_facing`, `target='networknode'`, `family='clusteroperation'`, then return the loaded NetOp instance via `get_object_class(op_type).from_db(op_uuid)`. Note: `target='networknode'` and `family='clusteroperation'` are the existing defaults of `net_op.create_and_enqueue`, so they can be omitted from the call if the helper preserves them. (2) `Network.remove_floating_ip` analogous, with task `network_remove_floating_ip`. (3) `Network.route_address(floating_address)` body enqueues via `net_ip_op.create_and_enqueue(network_uuid=str(self.uuid), ip=floating_address, tasks=[net_ip_op_schema.model_tasks.route_address], priority=PRIORITY.user_facing)`. Returns the loaded `NetIPOp`. Import `net_ip_op` schema appropriately. (4) `Network.unroute_address` analogous, task `unroute_address`. (5) `Network.remove_nat()` enqueues a `NetOp` with `tasks=[model_tasks.network_remove_nat]` (existing task value 5). Returns the loaded `NetOp`. The `@_not_on_floating_network` decorator stays where it exists today. **Important:** these methods previously had `with self.get_lock(...)` wrappers around the host work. The lock work is now inside `BridgedVXLanNetwork._apply_*`, so the wrappers move with the body and are gone from `Network`. After this commit, `grep -n "util_concurrency" shakenfist/network/network.py` should not show host-mutating calls (`add_floating_ip`, `remove_floating_ip`, the `ip route add/del` `execute` call). Test that calling each method enqueues exactly one cluster operation with the right task and parameters; test that `util_concurrency.add_floating_ip` is not called during a `Network.add_floating_ip` call. Commit message subject: `network: floating-IP and route methods now enqueue.` |
| 3f. External callers | medium | sonnet | none | Update `shakenfist/daemons/network/maintain.py` line 138 (currently `n.add_floating_ip(floating_addr, ni.ipv4, [ni, ('instance', ni.instance_uuid)])`) and line 146 (currently `n.route_address(addr)`) to capture the returned op and call `raise_for_error()` on it. Use distinct local variable names (`add_op`, `route_op`) since the loops may iterate multiple times in one pass. Reuse the `NetworkOperationFailed` handler that Phase 2 already added to `maintain.py` — the new calls will surface failures the same way. Add a regression test that confirms the maintain loop calls `op.raise_for_error()` after each enqueue. Commit message subject: `network: maintain uses raise_for_error for floating IPs and routes.` |
| 3g. Documentation | medium | sonnet | none | Extend the existing `ARCHITECTURE.md` "Network Operation Error Handling" section (added in Phase 2) with a note listing the migrated methods after Phase 3 (`ensure_mesh`, `add_floating_ip`, `remove_floating_ip`, `route_address`, `unroute_address`, `remove_nat`). Update `AGENTS.md` if anything in the orienting section is now inaccurate. Add to `docs/developer_guide/network_dispatcher.md` a paragraph noting that floating-IP and route ops are now part of the migrated set, and that the affected op types (`net_op`, `net_ip_op`, `net_iface_op`, `net_iface_ip_op`) all route through `BridgedVXLanNetwork` in their dispatchers and persist `ErrorReport` on failure. Do not modify `mkdocs.yml` directly. Commit message subject: `docs: phase 3 floating-IP migration notes.` |

## Step ordering and dependencies

* 3a, 3b, 3c are independent and can land in any order
  or even in parallel (different files).
* 3d depends on 3a (uses the new model fields and task
  enums), 3b (calls into `_apply_*`), and 3c (uses
  `ErrorReport.from_exception` with the new registry
  entries).
* 3e depends on 3d. The safety property mirrors Phase 2:
  by the time `Network.add_floating_ip()` flips to
  enqueue, every in-worker caller must already be using
  `BridgedVXLanNetwork`, otherwise the net-worker
  enqueues to itself.
* 3f depends on 3e (the new return value).
* 3g after all.

Recommended landing order on the phase branch:
3a -> 3b -> 3c -> 3d -> 3e -> 3f -> 3g.

## Back brief

Before executing any step, the implementing sub-agent
must back brief the management session confirming
understanding. Each implementing agent should
explicitly confirm:

* The safety property: 3d must land before 3e. The four
  in-worker dispatchers (`net_op._network_remove_nat`,
  `net_ip_op._route_address`/`_unroute_address`,
  `net_iface_op._interface_float`,
  `net_iface_ip_op._interface_defloat`) must use
  `BridgedVXLanNetwork` before `Network.X()` flips to
  enqueue, otherwise the dispatcher would enqueue to
  itself.

* The fan-out: changes touch four op-type files
  (`net_op.py`, `net_ip_op.py`, `net_iface_op.py`,
  `net_iface_ip_op.py`) plus `network.py`. Touching
  all four is required for the safety property; no op
  type can keep using the old `Network` methods after
  Phase 3 commits.

* Event correlation: today's
  `Network.add_floating_ip(... affected_objects)`
  emits a multi-target audit event using
  `affected_objects`. Under the migration, that
  responsibility splits: `Network.add_floating_ip`
  emits a "requesting" event synchronously on the
  caller-provided objects, and the dispatcher that
  picks up the NetOp emits its own dispatch-time event
  on whatever objects it has access to (the network and
  the interface, when called via `net_iface_op`). The
  `_apply_*` methods on `BridgedVXLanNetwork` do not
  take `affected_objects` as a parameter.

* `route_address`/`unroute_address` use the existing
  `net_ip_op` op type (rather than a new `NetOp` task)
  to match how the REST API already enqueues these
  operations. `add_floating_ip`/`remove_floating_ip`
  use new NetOp tasks (not existing op types) because
  the existing `net_iface_op`/`net_iface_ip_op` op
  types have a different surface (they look up the
  floating address from the interface state rather than
  taking it as a parameter).

## Review checklist for the management session

After each step's sub-agent reports completion:

- [ ] Named files were modified; no unrelated files
      changed.
- [ ] `pre-commit run --files <changed files>` passes.
- [ ] New unit tests pass via
      `tox -e py3 -- <module>`.
- [ ] Commit message subject ends in a period, no
      longer than 50 characters, body wraps at 75
      characters per `CLAUDE.md`.
- [ ] Commit body includes the `Prompt:` paragraph and
      the `Co-Authored-By` / `Signed-off-by` lines.
- [ ] For step 3a: if `tox -e genprotos` was needed,
      the regenerated stubs are committed in the same
      commit.
- [ ] For step 3d: a follow-up grep confirms `grep -rn
      "n\.add_floating_ip\|n\.remove_floating_ip\|
      n\.route_address\|n\.unroute_address\|
      n\.remove_nat" shakenfist/operations/` returns
      zero hits.
- [ ] For step 3e: `grep -n "util_concurrency" shakenfist/network/network.py`
      shows no host-mutating calls in `Network`.

After all steps complete:

- [ ] cluster_ci functional smoke suite passes on the
      phase 3 PR.
- [ ] No new `ERROR` / `Traceback` lines in the
      cluster_ci stable-log gate.
- [ ] Master plan execution table for Phase 3 is
      updated from `Planning` to `Complete`.
