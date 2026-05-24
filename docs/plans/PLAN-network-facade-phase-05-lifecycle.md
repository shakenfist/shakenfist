# Phase 5: lifecycle method migration

## Context

Phase 5 is the last per-method migration. It covers the
four `Network` lifecycle methods plus the private
`enable_nat` helper they depend on:

* `Network.create_on_hypervisor()` — runs on every node
  that places interfaces on the network. Sets up the
  VXLAN interface locally.
* `Network.create_on_network_node()` — runs only on the
  elected network node. Sets up the network namespace,
  veth pairs, NAT routing, and (transitively) dnsmasq.
  ~110 lines of host setup.
* `Network.delete_on_hypervisor()` — runs on every
  node. Tears down the local VXLAN.
* `Network.delete_on_network_node()` — runs only on
  the elected network node. Tears down the namespace,
  veth pairs, hypervisor cleanup fan-out, and
  (transitively) dnsmasq and NAT.
* `Network.enable_nat()` — private helper. Sets up
  the masquerade rules. Only called from inside
  `create_on_network_node`.

This phase is wider than Phases 2-4 because:

1. The lifecycle methods are the broadest fan-out of
   the per-method migrations — six different code
   sites enqueue or call them today.
2. `create_on_network_node` is the largest method in
   `Network` (~110 lines) and carries the most
   complex host setup.
3. Two of the four methods (`create_on_hypervisor`
   and `delete_on_hypervisor`) are per-hypervisor —
   they enqueue to per-node `node_net_op` queues, not
   the cluster-wide `networknode` queue.
4. Phase 4's in-worker sibling-call pattern inside
   `create_on_network_node` and `delete_on_network_node`
   (the late-import workarounds for `_apply_remove_dnsmasq`,
   `_apply_remove_nat`, `_apply_update_dnsmasq`)
   becomes natural in-class `self._apply_X()` calls
   once the bodies move into `BridgedVXLanNetwork`.

After Phase 5, every host-mutating `Network` method
will have flipped to enqueue. Phase 6 rewrites
`maintain.py` as discovery-only. Phase 7 removes the
`redirect_to_network_node` decorator and the four REST
sites that still use it. Phase 8 removes the residual
`get_lock` wrappers in `BridgedVXLanNetwork._apply_*`.

## What Phase 5 ships

1. **Five new `_apply_*` methods on `BridgedVXLanNetwork`**:
   * `_apply_create_on_hypervisor()`
   * `_apply_create_on_network_node()` (large; ~110 lines lifted)
   * `_apply_delete_on_hypervisor()`
   * `_apply_delete_on_network_node()`
   * `_apply_enable_nat()` — private internal helper, called
     only from `_apply_create_on_network_node`.

   The body lifts include rewriting the late-import / nested
   `BridgedVXLanNetwork(self)._apply_X()` patterns from
   Phase 4 step 4e into clean `self._apply_X()` calls now
   that the bodies are inside the worker class. The
   `_apply_remove_dnsmasq`, `_apply_remove_nat`, and
   `_apply_update_dnsmasq` calls that today live inside
   `Network.create_on_network_node` / `delete_on_network_node`
   become local method calls on `self` inside the new
   `_apply_create_on_network_node` / `_apply_delete_on_network_node`.

2. **Three new tasks** spread across the two relevant op
   types:
   * NetOp: `network_apply_create_network_node = 11` and
     `network_apply_delete_network_node = 12`. Both
     target the cluster-wide `networknode` queue (the
     elected network node's net-worker dispatches them).
   * node_net_op: `network_apply_create_hypervisor = 2`.
     Targets the calling node's per-node `node_net_op`
     queue. The existing `network_destroy = 1` task on
     `node_net_op` is reused for `delete_on_hypervisor`
     — it already does what the name suggests.

3. **Updated in-worker dispatchers**:
   * `net_op._network_deploy` (the composite) calls
     `BridgedVXLanNetwork(n)._apply_create_on_network_node()`
     followed by `_apply_ensure_mesh()` on the same
     `BridgedVXLanNetwork` instance.
   * `net_op._network_update_dnsmasq` (the
     misleadingly-named broader-reconciliation task; see
     Phase 4 notes) follows the same pattern.
   * `net_op._network_destroy` calls
     `BridgedVXLanNetwork(n)._apply_delete_on_network_node()`.
   * `net_op._network_apply_create_network_node` (new) is
     a single-step variant calling only
     `_apply_create_on_network_node`.
   * `net_op._network_apply_delete_network_node` (new) is
     a single-step variant calling only
     `_apply_delete_on_network_node`.
   * `node_net_op._network_destroy` switches from
     `n.delete_on_hypervisor()` to
     `BridgedVXLanNetwork(n)._apply_delete_on_hypervisor()`.
   * `node_net_op._network_apply_create_hypervisor` (new)
     calls `BridgedVXLanNetwork(n)._apply_create_on_hypervisor()`.
   * `node_inst_op._instance_delete` (line 249) and
     `node_inst_netdesc_op._instance_start` (line 243)
     route through `BridgedVXLanNetwork` directly for
     the same in-worker reason as Phase 4.

4. **Flipped `Network` methods**:
   * `Network.create_on_hypervisor()` enqueues
     `node_net_op` with task
     `network_apply_create_hypervisor`,
     `target=config.NODE_UUID` (local node's per-node
     queue), `priority=PRIORITY.user_facing`. Returns
     the loaded op.
   * `Network.create_on_network_node()` enqueues
     `NetOp` with task
     `network_apply_create_network_node`,
     `target='networknode'`, `priority=PRIORITY.user_facing`.
     Returns the loaded op.
   * `Network.delete_on_hypervisor()` enqueues
     `node_net_op` with the existing
     `network_destroy = 1` task,
     `target=config.NODE_UUID`, `priority=PRIORITY.user_facing`.
     Returns the loaded op.
   * `Network.delete_on_network_node()` enqueues
     `NetOp` with task
     `network_apply_delete_network_node`,
     `target='networknode'`, `priority=PRIORITY.user_facing`.
     Returns the loaded op.

5. **`Network.enable_nat()` is removed entirely** from the
   public surface. The only existing caller is the
   `enable_nat` call inside `create_on_network_node`,
   which is lifted into `_apply_create_on_network_node`
   and becomes `self._apply_enable_nat()` — a method on
   the same `BridgedVXLanNetwork` instance.

6. **External (non-dispatcher) callers** of the four
   lifecycle methods switch to the
   `op = n.X(); op.raise_for_error()` pattern. The
   identified external sites are:
   * `shakenfist/instance.py:2021` — calls
     `n.create_on_hypervisor()`. Investigate context;
     likely in a Network-related method.
   * `shakenfist/daemons/queues/startup_tasks.py:134`
     and `:151` — calls `n.create_on_hypervisor()` and
     `inst.create_on_hypervisor()` (the second is on
     an Instance, not a Network; verify with a read).
   * `shakenfist/daemons/network/maintain.py:129` and
     `:154` — maintain thread calls. Phase 6 will
     rewrite maintain entirely; for now apply the
     minimal mechanical update so the loop keeps
     working through the migration.

7. **`Network` internal callers** of the lifecycle
   methods. The bodies of `create_on_network_node`
   and `delete_on_network_node` contain calls to
   `self.assign_floating_gateway()` and to
   `BridgedVXLanNetwork(self)._apply_X()` (the Phase 4
   late-import workarounds). When the bodies move,
   these internal references become clean
   `self.network.assign_floating_gateway()` and
   `self._apply_X()` respectively. `assign_floating_gateway`
   and `unassign_floating_gateway` stay on `Network`
   as private helpers — they manipulate IPAM and
   attribute storage, not host state, and are
   appropriately on `Network`.

8. **ErrorReport registry**: confirm no additional
   exception types need registering. Most lifecycle
   methods already raise registered exceptions
   (`DeadNetwork`, `CreateVXLANInterfaceFailed`,
   `CannotAssignFloatingGateway`, `CongestedNetwork`,
   `CreateNetworkNamespaceFailed`, `EnableNATFailed`).
   The Phase 3 step 3c already covered these
   speculatively. Audit during implementation; add
   any newly surfaced exception types.

## What Phase 5 does **not** do

* No rewrite of `maintain.py` — Phase 6.
* No removal of the existing `network_deploy`,
  `network_destroy`, or `network_update_dnsmasq`
  task handlers from `net_op`. Phase 6 cleans these
  up when `maintain.py` migrates.
* No removal of `redirect_to_network_node` — Phase 7.
* No removal of `NodeLock` wrappers from
  `BridgedVXLanNetwork._apply_*` methods — Phase 8.

## Key references in the existing code

* `shakenfist/network/network.py:575-589` — current
  `create_on_hypervisor`.
* `shakenfist/network/network.py:591-719` — current
  `create_on_network_node` (large, ~110 lines).
* `shakenfist/network/network.py:721-745` — current
  `delete_on_hypervisor`.
* `shakenfist/network/network.py:747-797` — current
  `delete_on_network_node`, including the Phase 4
  late-import workarounds for `_apply_remove_dnsmasq`
  and `_apply_remove_nat`.
* `shakenfist/network/network.py:873-877` — current
  `enable_nat` (trivial: just the
  `util_concurrency.enable_nat` call).
* `shakenfist/network/network.py:455-472` —
  `assign_floating_gateway` and
  `unassign_floating_gateway`. Stay on Network as
  private helpers.
* `shakenfist/operations/net_op.py:125-160` — the
  current dispatcher handlers
  (`_network_deploy`, `_network_destroy`,
  `_network_update_dnsmasq`).
* `shakenfist/operations/node_net_op.py:87-88` —
  current `_network_destroy` on node_net_op.
* `shakenfist/operations/node_inst_op.py:249` and
  `node_inst_netdesc_op.py:243` — in-worker callers
  of lifecycle methods.
* `shakenfist/schema/operations/net_op.py` and
  `shakenfist/schema/operations/node_net_op.py` —
  schemas to extend.
* `shakenfist/network/bridged_vxlan_network.py:215`
  — `BridgedVXLanNetwork._apply_remove_nat`'s call to
  `self.network.unassign_floating_gateway()`. Stays
  on Network.

## Success criteria

Phase 5 is complete when:

* `BridgedVXLanNetwork` has all five new `_apply_*`
  methods with bodies lifted from the corresponding
  `Network` methods (no `Network` bodies left over).
* `Network.create_on_hypervisor`,
  `Network.create_on_network_node`,
  `Network.delete_on_hypervisor`, and
  `Network.delete_on_network_node` all enqueue and
  return the op handle.
* `Network.enable_nat` is gone from the public
  surface. `grep -n "def enable_nat" shakenfist/network/network.py`
  returns no hits.
* `grep -n "util_concurrency.create_vxlan_interface\|
  util_concurrency.create_network_namespace\|
  util_concurrency.enable_nat\|util_concurrency.execute"
  shakenfist/network/network.py` returns zero hits.
  All host mutation has moved to
  `BridgedVXLanNetwork`. (`is_dnsmasq_running` may
  still reference `util_concurrency`-adjacent code
  but no host-mutating execute calls remain.)
* `grep -rn "n\.create_on_hypervisor\|
  n\.create_on_network_node\|n\.delete_on_hypervisor\|
  n\.delete_on_network_node\|n\.enable_nat"
  shakenfist/operations/` returns zero hits. Every
  in-worker dispatcher routes through
  `BridgedVXLanNetwork`.
* The Phase 4 late-import workarounds inside
  `Network.create_on_network_node` and
  `Network.delete_on_network_node` are gone. They
  become natural `self._apply_X()` calls inside
  `BridgedVXLanNetwork._apply_create_on_network_node`
  and `_apply_delete_on_network_node`.
* External callers in `instance.py`,
  `startup_tasks.py`, and `maintain.py` use
  `op = n.X(); op.raise_for_error()`.
* `pre-commit run --all-files` passes.
* `tox -e py3` shows no regressions.
* cluster_ci functional suite passes on the phase 5
  PR. Network create/delete latency should not show
  regressions; the dispatcher now executes the
  lifted bodies but the wall-clock work is the same.
* `ARCHITECTURE.md`, `AGENTS.md`, and the developer
  guide describe lifecycle methods as part of the
  migrated set, and note that every host-mutating
  `Network` method is now flipped to enqueue.

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a. Schema additions | low | sonnet | none | (1) In `shakenfist/schema/operations/net_op.py`, add two tasks to `model_tasks`: `network_apply_create_network_node = 11` and `network_apply_delete_network_node = 12`. (2) In `shakenfist/schema/operations/node_net_op.py`, add `network_apply_create_hypervisor = 2`. The existing `network_destroy = 1` on `node_net_op` is preserved. No new fields are needed on either model. No `current_version` bump is needed (additive enum entries, no new fields). Confirm no proto regeneration is needed (`grep -rn "network_destroy\|network_apply" shakenfist/protos/` returns zero hits, as in earlier phases). Update unit tests in `shakenfist/tests/schema/operations/test_net_op.py` and `test_node_net_op.py` (create the latter if absent) asserting the new enum values. Commit message subject: `schema: add lifecycle apply tasks.` |
| 5b. BridgedVXLanNetwork lifecycle apply methods | high | opus | none | Lift five method bodies from `shakenfist/network/network.py` into `shakenfist/network/bridged_vxlan_network.py`. (1) `_apply_create_on_hypervisor()` — lift network.py:575-589. Preserve the `with self.network.get_lock(op='create_on_hypervisor', ...)` wrapper. (2) `_apply_create_on_network_node()` — lift network.py:591-719 (large, ~110 lines). Preserve the `with self.network.get_lock(op='create_on_network_node', ...)` wrapper and the floating-gateway/NAT logic. Rewrite the Phase 4 late-import workaround at network.py:708-714 (`from ... import BridgedVXLanNetwork; BridgedVXLanNetwork(self)._apply_update_dnsmasq()`) to a clean `self._apply_update_dnsmasq()` since `self` is now the worker class. Rewrite the `self.enable_nat()` call (line 700) to `self._apply_enable_nat()`. Rewrite `self.assign_floating_gateway()` (line 660) to `self.network.assign_floating_gateway()` — that helper stays on Network. (3) `_apply_delete_on_hypervisor()` — lift network.py:721-745. Preserve the lock wrapper. (4) `_apply_delete_on_network_node()` — lift network.py:747-797. Preserve the lock wrapper. Rewrite the Phase 4 late-import workarounds at network.py:793-797 to clean `self._apply_remove_dnsmasq()` and `self._apply_remove_nat()`. Keep the per-node fan-out (the `for n in Nodes([], prefilter='active'): nn_create_and_enqueue(...)` loop). (5) `_apply_enable_nat()` — lift network.py:873-877. Trivial; just the `util_concurrency.enable_nat(self.network.uuid, ...)` call. **Do not modify `Network` methods yet** — step 5d flips them. Extend tests in `shakenfist/tests/test_bridged_vxlan_network.py` for each new method, mocking the privexec / shell layer and confirming the lifted body runs. Commit message subject: `network: add lifecycle apply methods.` |
| 5c. In-worker dispatcher updates | high | opus | none | Update five dispatcher files. (1) `shakenfist/operations/net_op.py`: `_network_deploy` (line 125) calls `BridgedVXLanNetwork(n)._apply_create_on_network_node()` then `._apply_ensure_mesh()`. `_network_update_dnsmasq` (line 148) same pattern. `_network_destroy` (line 135) calls `BridgedVXLanNetwork(n)._apply_delete_on_network_node()`. Add two new handlers: `_network_apply_create_network_node(self, n)` and `_network_apply_delete_network_node(self, n)`, each calling the single corresponding `_apply_*`. (2) `shakenfist/operations/node_net_op.py`: `_network_destroy` (line 87) calls `BridgedVXLanNetwork(n)._apply_delete_on_hypervisor()`. Add new handler `_network_apply_create_hypervisor(self, n)` calling `_apply_create_on_hypervisor()`. (3) `shakenfist/operations/node_inst_op.py`: line 249 `n.delete_on_hypervisor()` → `BridgedVXLanNetwork(n)._apply_delete_on_hypervisor()`. (4) `shakenfist/operations/node_inst_netdesc_op.py`: line 243 `n.create_on_hypervisor()` → `BridgedVXLanNetwork(n)._apply_create_on_hypervisor()`. Add `BridgedVXLanNetwork` and `ErrorReport` imports where missing. Wire `ErrorReport` persistence into each dispatcher's outer `except` block if not already present (mirror earlier-phase pattern). After this step, `grep -rn "n\.create_on_hypervisor\|n\.create_on_network_node\|n\.delete_on_hypervisor\|n\.delete_on_network_node\|n\.enable_nat" shakenfist/operations/` returns zero hits. Add unit tests covering each updated/new handler. Commit message subject: `operations: route lifecycle dispatchers through BridgedVXLanNetwork.` |
| 5d. Flip Network methods | high | opus | none | Replace the bodies of four `Network` methods in `shakenfist/network/network.py`. Each enqueues a cluster operation and returns the op handle. (1) `create_on_hypervisor(self)`: enqueue `node_net_op` with `tasks=[nn_tasks.network_apply_create_hypervisor]`, `node_uuid=str(config.NODE_UUID)`, `priority=PRIORITY.user_facing`, return the loaded NodeNetOp. (2) `create_on_network_node(self)`: keep the `@_not_on_floating_network` decorator. Preserve the `if self.state.value == dbo.STATE_DELETED: ... return` short-circuit. Then enqueue `NetOp` with `tasks=[net_tasks.network_apply_create_network_node]`, `target='networknode'` (default), `priority=PRIORITY.user_facing`, return loaded NetOp. (3) `delete_on_hypervisor(self)`: enqueue `node_net_op` with `tasks=[nn_tasks.network_destroy]` (existing task value 1, semantics preserved), `node_uuid=str(config.NODE_UUID)`, `priority=PRIORITY.user_facing`, return loaded NodeNetOp. (4) `delete_on_network_node(self)`: enqueue `NetOp` with `tasks=[net_tasks.network_apply_delete_network_node]`, `target='networknode'`, `priority=PRIORITY.user_facing`, return loaded NetOp. **Remove `Network.enable_nat`** entirely — no external caller exists (Phase 5 step 5b lifted the only call site into `_apply_create_on_network_node`). After this step, `grep -n "def create_on_hypervisor\|def create_on_network_node\|def delete_on_hypervisor\|def delete_on_network_node\|def enable_nat" shakenfist/network/network.py` shows only the four new short enqueue methods. The `_get_dnsmasq_object` and `assign_floating_gateway` / `unassign_floating_gateway` helpers stay on Network. Add tests in `shakenfist/tests/test_net.py` for each flipped method asserting the right task / queue / priority is enqueued. Commit message subject: `network: lifecycle methods now enqueue.` |
| 5e. External callers | medium | sonnet | none | Update the external (non-dispatcher) call sites to use `op = n.X(); op.raise_for_error()`. (1) `shakenfist/instance.py:2021` — locate and update. The call is `n.create_on_hypervisor()` inside some Network-aware method on Instance; the variable name is `n` so it should be the local Network. Read the surrounding code to confirm. (2) `shakenfist/daemons/queues/startup_tasks.py:134` — `n.create_on_hypervisor()`. (3) `shakenfist/daemons/queues/startup_tasks.py:151` — `inst.create_on_hypervisor()`. Important: this looks like a call on an Instance, not a Network. Read the file and verify before updating. If it's on Instance (not Network), do NOT touch it in this step — Instance.create_on_hypervisor is a different method outside this phase's scope. (4) `shakenfist/daemons/network/maintain.py:129` — `n.create_on_network_node()`. (5) `shakenfist/daemons/network/maintain.py:154` — `n.create_on_hypervisor()`. Each gets the standard `op = n.X(); op.raise_for_error()` treatment. Phase 6 will rewrite maintain entirely; for now apply the minimal mechanical update. Reuse the existing `NetworkOperationFailed` handler that Phase 2 added to `maintain.py`. After this step, `grep -rn "n\.create_on_hypervisor\|n\.create_on_network_node\|n\.delete_on_hypervisor\|n\.delete_on_network_node" shakenfist/ --include='*.py' | grep -v test` returns zero hits in production code. Commit message subject: `network: external callers of lifecycle methods use raise_for_error.` |
| 5f. Documentation | medium | sonnet | none | Update three docs: (1) `ARCHITECTURE.md` "Network Operation Error Handling" section: bump the migrated method list from eleven (after phase 4) to **all fifteen** host-mutating methods (the four lifecycle plus the previously-migrated set). Add a closing note that every host-mutating `Network` method is now flipped; remaining phases (6, 7, 8) are cleanups, not migrations. (2) `AGENTS.md`: rename the subsection from "Network facade (Phases 2-4)" to "Network facade (Phases 2-5)"; append the lifecycle methods to the migrated list; note that `Network.enable_nat` is no longer a public method. (3) `docs/developer_guide/network_dispatcher.md`: add a "Phase 5 additions" section with the lifecycle method-to-op-type mapping table and a paragraph noting the dispatcher pattern is now consistent across every host-mutating Network method. `mkdocs.yml.tmpl` not touched (no new doc files). Commit message subject: `docs: phase 5 lifecycle migration notes.` |

## Step ordering and dependencies

* 5a (schema) is independent and lands first.
* 5b (BridgedVXLanNetwork lifecycle apply methods)
  depends on Phase 4's existing
  `_apply_update_dnsmasq` / `_apply_remove_dnsmasq` /
  `_apply_remove_nat` (which are already committed)
  but is otherwise independent of 5a.
* 5c (dispatcher updates) depends on 5a (new tasks)
  and 5b (calls `_apply_*`).
* 5d (Network method flips) depends on 5c. Same
  safety property as Phases 2-4: by the time
  `Network.X()` flips to enqueue, every in-worker
  caller must already be using `BridgedVXLanNetwork`.
* 5e (external callers) depends on 5d (the new
  return value).
* 5f (docs) after all.

Recommended landing order: 5a → 5b → 5c → 5d → 5e → 5f.

## Back brief

Before executing any step, the implementing sub-agent
must back brief the management session. Each agent
should explicitly confirm:

* The safety property: 5c (in-worker dispatchers
  route through `BridgedVXLanNetwork`) must land
  before 5d (`Network.X()` flips to enqueue),
  otherwise the net-worker enqueues to itself.

* The Phase 4 late-import workaround pattern inside
  `Network.create_on_network_node` and
  `Network.delete_on_network_node` (the
  `from ... import BridgedVXLanNetwork;
  BridgedVXLanNetwork(self)._apply_X()` blocks)
  becomes a clean `self._apply_X()` call once the
  body is inside the worker class. The implementing
  agent of step 5b must understand this rewrite and
  apply it correctly.

* `Network.enable_nat` is removed from the public
  surface entirely. The master plan's open question 8
  resolved this — `enable_nat` was always internal-only,
  used only inside `create_on_network_node`. The
  only call site moves with the body into
  `_apply_create_on_network_node` and becomes
  `self._apply_enable_nat()`.

* `assign_floating_gateway` and
  `unassign_floating_gateway` **stay on `Network`**
  as private helpers. They manipulate IPAM and
  attribute storage, not host state. The
  `_apply_*` methods on `BridgedVXLanNetwork` call
  them via `self.network.assign_floating_gateway()`
  / `self.network.unassign_floating_gateway()`.

* The `node_net_op` task family. `node_net_op` is
  per-node (its queue name includes the node uuid),
  so it is the right vehicle for the per-hypervisor
  `create_on_hypervisor` / `delete_on_hypervisor`
  flips. `Network.create_on_hypervisor()` enqueues
  with `node_uuid=str(config.NODE_UUID)` — i.e., the
  local node only. Callers that need fan-out (like
  `delete_on_network_node`'s "remove from every
  hypervisor" loop) continue to enqueue
  `node_net_op` per-node explicitly; that loop is
  not affected by the Network method flip.

* `instance.py:2021` calls `n.create_on_hypervisor()`
  — the implementing agent of step 5e should verify
  this is on a `Network`, not on an `Instance` (the
  variable name `n` is suggestive but not
  conclusive). Read the surrounding context.

## Review checklist for the management session

After each step's sub-agent reports completion:

- [ ] Named files were modified; no unrelated files
      changed.
- [ ] `pre-commit run --files <changed files>` passes.
- [ ] New unit tests pass.
- [ ] Commit message subject ends in a period, ≤ 50
      characters; body wraps at 75.
- [ ] Commit body includes the `Prompt:` paragraph
      and the standard `Co-Authored-By` /
      `Signed-off-by` lines.
- [ ] For step 5b: the Phase 4 late-import
      workarounds inside the lifted
      `_apply_create_on_network_node` and
      `_apply_delete_on_network_node` bodies are
      replaced with clean `self._apply_X()` calls.
- [ ] For step 5c: `grep -rn
      "n\.create_on_hypervisor\|n\.create_on_network_node\|
      n\.delete_on_hypervisor\|n\.delete_on_network_node\|
      n\.enable_nat" shakenfist/operations/` returns
      zero hits.
- [ ] For step 5d: `grep -n "def enable_nat" shakenfist/network/network.py`
      returns zero hits. `grep -n
      "util_concurrency.create_vxlan_interface\|
      util_concurrency.create_network_namespace\|
      util_concurrency.enable_nat\|util_concurrency.execute"
      shakenfist/network/network.py` returns zero
      hits.

After all steps complete:

- [ ] cluster_ci functional smoke suite passes on the
      phase 5 PR.
- [ ] No new `ERROR` / `Traceback` lines in the
      cluster_ci stable-log gate.
- [ ] Network create/delete latency does not regress
      noticeably (compare timings between phase 4 and
      phase 5 CI runs if observable).
- [ ] Master plan execution table for Phase 5 is
      updated from `Planning` to `Complete`.
