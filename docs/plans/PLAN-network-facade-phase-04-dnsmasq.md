# Phase 4: dnsmasq operation migration

## Context

Phase 3 migrated `add_floating_ip`, `remove_floating_ip`,
`route_address`, `unroute_address`, and `remove_nat`.
Phase 4 covers the five remaining `Network` methods that
mutate dnsmasq process state:

* `Network.update_dnsmasq()`
* `Network.remove_dnsmasq()`
* `Network.remove_dhcp_lease(ipv4, macaddr)`
* `Network.update_dns_entry(name, value)`
* `Network.remove_dns_entry(name)`

These methods differ from the Phase 2 and Phase 3
methods in one structural way: they are **already
partially queue-aware**. Each carries an explicit
`if config.NODE_IS_NETWORK_NODE: ... else: enqueue ...`
branch, so non-network-node callers already enqueue
today, while network-node callers do the work inline.
The migration's task is to lift the inline branch into
`BridgedVXLanNetwork._apply_*` methods and have the
public `Network` methods always enqueue (no branching).

A subtle existing-code observation: the current
`net_op._network_update_dnsmasq` task handler does **not
actually call `n.update_dnsmasq()`**. It calls
`n.create_on_network_node()` followed by
`BridgedVXLanNetwork._apply_ensure_mesh()`. The
roundabout chain still ends up restarting dnsmasq
because `create_on_network_node` itself calls
`self.update_dnsmasq()` internally (network.py:688) — but
the task name is misleading and the work is broader than
its name implies. Phase 4 introduces a new
`network_apply_update_dnsmasq` task whose handler does
exactly what its name says (calls
`BridgedVXLanNetwork._apply_update_dnsmasq()`). The
existing `network_update_dnsmasq` task is left in place
for now because the `_network_update_dnsmasq` handler is
still invoked from elsewhere via the existing enqueues
that Phase 4 redirects; Phase 6 (`maintain.py` rewrite)
will revisit the broader reconciliation task.

The `_apply_*` methods Phase 4 introduces are:

* `_apply_update_dnsmasq()` — `_get_dnsmasq_object().restart()`
* `_apply_remove_dnsmasq()` — `_get_dnsmasq_object().terminate()` + state transition
* `_apply_remove_dhcp_lease(ipv4, macaddr)` — `_get_dnsmasq_object().remove_lease(ipv4, macaddr)`

`update_dns_entry` and `remove_dns_entry` do **not** get
dedicated `_apply_*` methods. Their attribute mutation
(`attrs.hosteddns[name] = value` and the matching
`del`) is DB-only state, not host state, and stays
synchronous inside `Network`. They then enqueue a
`network_apply_update_dnsmasq` task to restart dnsmasq.

### Latent Phase 3 bug to fix

While auditing the dnsmasq call sites I found a latent
bug introduced in Phase 3 that Phase 4 should fix as
part of the same delta.

`Network.delete_on_network_node` (network.py:759-760)
calls `self.remove_nat()` and then
`raise_for_error()`. But `delete_on_network_node` is
itself called from `_network_destroy` inside the
single-threaded net-worker dispatcher. So we have:

1. Dispatcher dequeues `network_destroy` NetOp.
2. Handler calls `delete_on_network_node`.
3. `delete_on_network_node` calls `self.remove_nat()`,
   which enqueues a `network_remove_nat` NetOp on the
   same networknode queue.
4. `raise_for_error()` polls until the new op reaches
   terminal state.
5. The dispatcher is blocked inside the current handler
   waiting for `raise_for_error`. The new op never
   dequeues — same queue, single worker.
6. `raise_for_error` times out at `API_ASYNC_WAIT`
   (default 15 s) with `OperationTimeout`.

The `network_destroy` op then ends in `STATE_ERROR`,
the dispatcher moves on, and the deferred
`network_remove_nat` op eventually runs (cleaning up
NAT state on a later dispatcher iteration). The
functional outcome — eventual cleanup, just spread
across two ops with a misleading ERROR state on the
first — is why CI did not catch this directly. But
each network teardown pays a 15-second timeout and
logs an ERROR.

Phase 4's same internal-call pattern applies to the
two dnsmasq cases:

* `network.py:688` — `create_on_network_node` calls
  `self.update_dnsmasq()`. In-worker; can't
  `raise_for_error` without deadlocking.
* `network.py:753` — `delete_on_network_node` calls
  `self.remove_dnsmasq()`. In-worker; same constraint.

The right pattern for in-worker callers inside other
`Network` host-mutating methods is: call
`BridgedVXLanNetwork(self)._apply_X()` directly. No
queue round-trip, semantics preserved, no deadlock.
Apply this pattern to:

* `delete_on_network_node`'s call to `self.remove_nat()`
  (the Phase 3 bug fix).
* `create_on_network_node`'s call to
  `self.update_dnsmasq()`.
* `delete_on_network_node`'s call to
  `self.remove_dnsmasq()`.

Phase 5 will migrate `create_on_network_node` and
`delete_on_network_node` themselves; this Phase 4
fix-in-passing leaves them in a coherent in-worker
state until then.

## What Phase 4 ships, in order

1. Three new `_apply_*` methods on `BridgedVXLanNetwork`:
   `_apply_update_dnsmasq`, `_apply_remove_dnsmasq`,
   `_apply_remove_dhcp_lease`. Bodies lifted from the
   `if NODE_IS_NETWORK_NODE` branches of today's `Network`
   methods, with the existing `get_lock` wrappers
   preserved.

2. Two new `NetOp` tasks: `network_apply_update_dnsmasq`
   (value 9) and `network_apply_remove_dnsmasq` (value 10).
   The existing tasks `network_update_dnsmasq` (3) and
   `network_remove_dnsmasq` (4) are left in place for now
   — they are still enqueueable from anywhere that has
   the old behaviour wired in.

3. Updated in-worker dispatcher handlers:
   * `net_op._network_remove_dnsmasq` (existing handler)
     changes from `n.remove_dnsmasq()` to
     `BridgedVXLanNetwork(n)._apply_remove_dnsmasq()`.
   * `net_op._network_apply_update_dnsmasq` (new handler)
     calls `BridgedVXLanNetwork(n)._apply_update_dnsmasq()`.
   * `net_op._network_apply_remove_dnsmasq` (new handler)
     calls `BridgedVXLanNetwork(n)._apply_remove_dnsmasq()`
     — note this is the same body as
     `_network_remove_dnsmasq` after the update; the
     duplication is intentional and resolves when the
     old task is removed in a later phase.
   * `net_macaddr_ip_op._remove_dhcp_lease` changes from
     `n.remove_dhcp_lease(...)` to
     `BridgedVXLanNetwork(n)._apply_remove_dhcp_lease(...)`.
     ErrorReport persistence is already wired in
     `net_macaddr_ip_op`'s outer except from Phase 3.
   * `node_inst_netdesc_op._instance_start` (line 245) and
     `node_inst_op._reset` (or whichever handler — line
     237) currently call `n.update_dnsmasq()` from
     inside their dispatcher contexts. After step 4d
     flips `Network.update_dnsmasq` to enqueue, these
     calls would enqueue from inside their dispatchers.
     Update to `BridgedVXLanNetwork(n)._apply_update_dnsmasq()`
     directly to avoid the round-trip.

4. Flip the five `Network` methods:
   * `update_dnsmasq()` always enqueues NetOp with task
     `network_apply_update_dnsmasq`. No `NODE_IS_NETWORK_NODE`
     branch. Returns the op handle.
   * `remove_dnsmasq()` always enqueues NetOp with task
     `network_apply_remove_dnsmasq`. Returns the op
     handle.
   * `remove_dhcp_lease(ipv4, macaddr)` always enqueues
     `net_macaddr_ip_op` (existing op type) with task
     `remove_dhcp_lease`. Returns the op handle.
   * `update_dns_entry(name, value)` keeps its
     synchronous attribute update
     (`attrs.hosteddns[name] = value` +
     `_save_attributes()` + audit event), then enqueues
     `network_apply_update_dnsmasq`. Returns the op
     handle.
   * `remove_dns_entry(name)` keeps its synchronous
     attribute update, then enqueues
     `network_apply_update_dnsmasq`. Returns the op
     handle.

5. Update Network-internal sibling calls inside the
   not-yet-migrated lifecycle methods:
   * `create_on_network_node` (network.py:688):
     `self.update_dnsmasq()` -> `BridgedVXLanNetwork(self)._apply_update_dnsmasq()`.
     Also handle the case where dnsmasq isn't required
     (`self.provide_dhcp and not self.provide_dns` -> no
     call at all). Replicate the
     `if not self.provide_dhcp and not self.provide_dns:
     return` guard logic, but lifted to the call site.
   * `delete_on_network_node` (network.py:753):
     `self.remove_dnsmasq()` ->
     `BridgedVXLanNetwork(self)._apply_remove_dnsmasq()`.
     Same provide-guard handling.
   * `delete_on_network_node` (network.py:759-760):
     `remove_nat_op = self.remove_nat(); remove_nat_op.raise_for_error()`
     -> `BridgedVXLanNetwork(self)._apply_remove_nat()`.
     This fixes the latent Phase 3 deadlock-via-timeout
     bug.
   * `remove_networkinterface_lease` (network.py:427):
     `self.remove_dhcp_lease(ni.ipv4, ni.macaddr)`.
     This Network method is called from
     `network/interface.py:296` — read that file to
     determine whether it's in-worker (in which case
     use `BridgedVXLanNetwork`) or external (in which
     case use `op.raise_for_error()`). The phase plan
     does not pre-determine this; the implementing
     agent should investigate.

6. Update external callers:
   * `external_api/network.py:689` —
     `network_from_db.update_dns_entry(name, value)`.
     Capture the returned op and call `raise_for_error()`.
   * `external_api/network.py:716` —
     `network_from_db.remove_dns_entry(name)`. Same.

7. Add `ErrorReport` registry entries if any new typed
   exceptions are raised by the lifted dnsmasq bodies.
   Investigate during step 4b. The
   `managed_executables/dnsmasq.py` module is where the
   dnsmasq lifecycle lives; check its exception classes
   (e.g. anything like `DnsMasqRestartFailed`,
   `DnsMasqAlreadyRunning`). Add registry entries for
   any exception that surfaces across the queue
   boundary.

8. Documentation.

## What Phase 4 does **not** do

* Does not migrate `create_on_*` or `delete_on_*` —
  Phase 5. The Phase 4 fix-in-passing updates the
  internal sibling calls inside these methods to use
  `BridgedVXLanNetwork` directly, but the methods
  themselves remain `Network` instance methods.
* Does not remove the existing
  `network_update_dnsmasq` and
  `network_remove_dnsmasq` tasks. Both are still
  reachable via the existing `_network_update_dnsmasq`
  handler (which does broader reconciliation) and
  `_network_remove_dnsmasq` handler. Phase 6 cleans
  these up as part of the `maintain.py` rewrite.
* Does not remove the `NodeLock` wrappers on the
  `_apply_*` methods — Phase 8.

## Key references in the existing code

* `shakenfist/network/network.py:780-805` — current
  `remove_dhcp_lease`, with its
  `if NODE_IS_NETWORK_NODE` branch.
* `shakenfist/network/network.py:793-806` — current
  `update_dnsmasq`.
* `shakenfist/network/network.py:808-822` — current
  `remove_dnsmasq`.
* `shakenfist/network/network.py:846-867` — current
  `update_dns_entry`.
* `shakenfist/network/network.py:869-891` — current
  `remove_dns_entry`.
* `shakenfist/network/network.py:427` — internal call
  to `self.remove_dhcp_lease` inside
  `remove_networkinterface_lease`.
* `shakenfist/network/network.py:688` — internal call
  to `self.update_dnsmasq` inside
  `create_on_network_node`.
* `shakenfist/network/network.py:753` — internal call
  to `self.remove_dnsmasq` inside
  `delete_on_network_node`.
* `shakenfist/network/network.py:759-760` — the
  Phase 3 latent bug:
  `self.remove_nat(); raise_for_error()` from inside
  `delete_on_network_node`.
* `shakenfist/operations/net_op.py:148-154` — existing
  `_network_update_dnsmasq` and
  `_network_remove_dnsmasq` handlers.
* `shakenfist/operations/net_macaddr_ip_op.py:95-108`
  — existing `_remove_dhcp_lease` handler.
* `shakenfist/operations/node_inst_netdesc_op.py:245`
  — `n.update_dnsmasq()` in-worker caller.
* `shakenfist/operations/node_inst_op.py:237`
  — `n.update_dnsmasq()` in-worker caller.
* `shakenfist/external_api/network.py:689,716` —
  REST handler callers of `update_dns_entry` and
  `remove_dns_entry`.
* `shakenfist/network/interface.py:296` — caller of
  `Network.remove_networkinterface_lease`, the
  context determines whether step 5 treats the
  internal `remove_dhcp_lease` call as in-worker or
  external.
* `shakenfist/managed_executables/dnsmasq.py` —
  module where the dnsmasq lifecycle lives; check for
  exception classes to register in `ErrorReport`.

## Success criteria

Phase 4 is complete when:

* `BridgedVXLanNetwork` has `_apply_update_dnsmasq`,
  `_apply_remove_dnsmasq`, and `_apply_remove_dhcp_lease`
  methods with bodies lifted from the
  `NODE_IS_NETWORK_NODE` branches of the corresponding
  `Network` methods.
* `Network.update_dnsmasq`, `remove_dnsmasq`,
  `remove_dhcp_lease`, `update_dns_entry`, and
  `remove_dns_entry` all enqueue (no
  `NODE_IS_NETWORK_NODE` branch remains in any of
  them). Each returns the op handle.
* `update_dns_entry` and `remove_dns_entry` keep their
  synchronous attribute updates (the
  `attrs.hosteddns` mutation) — these are DB-only state
  and stay synchronous.
* `grep -n "_get_dnsmasq_object\(\)\.\(restart\|terminate
  \|remove_lease\)" shakenfist/network/network.py`
  returns zero hits. The dnsmasq lifecycle calls have
  moved entirely to `BridgedVXLanNetwork`.
* `grep -rn "n\.update_dnsmasq\|n\.remove_dnsmasq\|
  n\.remove_dhcp_lease\|n\.update_dns_entry\|
  n\.remove_dns_entry" shakenfist/operations/` returns
  zero hits.
* `Network.delete_on_network_node`'s internal call
  to `self.remove_nat()` no longer uses
  `raise_for_error()`. It uses
  `BridgedVXLanNetwork(self)._apply_remove_nat()`
  directly. The Phase 3 deadlock-via-timeout bug is
  fixed.
* `Network.create_on_network_node` and
  `delete_on_network_node` use
  `BridgedVXLanNetwork(self)._apply_update_dnsmasq()`
  and `_apply_remove_dnsmasq()` for their internal
  sibling calls. (`Network.remove_networkinterface_lease`
  also fixed per the implementing agent's
  investigation.)
* External REST handlers in `external_api/network.py`
  use `op.raise_for_error()` after calling
  `update_dns_entry` / `remove_dns_entry`.
* `ErrorReport` registry has entries for any new
  exception types surfaced by the lifted dnsmasq
  bodies (whatever `managed_executables/dnsmasq.py`
  raises).
* `pre-commit run --all-files` passes.
* `tox -e py3` shows no regressions.
* cluster_ci functional suite passes on the phase 4 PR.
* `ARCHITECTURE.md`, `AGENTS.md`, and
  `docs/developer_guide/network_dispatcher.md` note
  that dnsmasq methods are now part of the migrated
  set.

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a. NetOp schema additions | low | sonnet | none | In `shakenfist/schema/operations/net_op.py`, add two new entries to the `model_tasks` enum after the Phase 3 additions: `network_apply_update_dnsmasq = 9` and `network_apply_remove_dnsmasq = 10`. No new model fields are needed (the dnsmasq operations only need `network_uuid`, which is already there). Existing values must not be renumbered. Update the unit tests in `shakenfist/tests/schema/operations/test_net_op.py` to assert the new values. Confirm no proto regeneration is needed (model_tasks is not in the proto surface — confirmed in Phase 3). Commit message subject: `schema: add dnsmasq apply tasks to NetOp.` |
| 4b. BridgedVXLanNetwork _apply methods | high | opus | none | Add three new methods to `shakenfist/network/bridged_vxlan_network.py`: `_apply_update_dnsmasq(self) -> None`, `_apply_remove_dnsmasq(self) -> None`, `_apply_remove_dhcp_lease(self, ipv4: str, macaddr: str) -> None`. Each lifts the body from inside the `if config.NODE_IS_NETWORK_NODE:` branch of the corresponding `Network` method (network.py:780-822). Preserve the `with self.network.get_lock(...)` wrapper exactly. The `if not self.provide_dhcp and not self.provide_dns: return` guard at the top of each Network method stays at the `Network` method level (it's a "should we even bother" check that the caller side handles); the `_apply_*` method assumes it's been called because work is needed. **Investigate** `shakenfist/managed_executables/dnsmasq.py` to find exception classes the lifted bodies may raise (e.g. anything around restart failure, terminate failure, lease removal). If exceptions are defined, register them in `ErrorReport._EXCEPTION_CODE_REGISTRY` in the same commit with appropriate codes (e.g. `network.dnsmasq.restart_failed`, `network.dnsmasq.terminate_failed`, `network.dnsmasq.remove_lease_failed`). Add `to_http()` mappings (all 500 likely). Extend tests in `shakenfist/tests/test_bridged_vxlan_network.py` and `shakenfist/tests/test_error_report.py`. Commit message subject: `network: add dnsmasq apply methods.` |
| 4c. In-worker dispatcher and lifecycle updates | high | opus | none | Three changes in one commit because they're tightly coupled: (1) Add new task handlers `_network_apply_update_dnsmasq(self, n)` and `_network_apply_remove_dnsmasq(self, n)` in `shakenfist/operations/net_op.py`. Each instantiates `BridgedVXLanNetwork(n)` and calls the corresponding `_apply_*`. (2) Update the existing `_network_remove_dnsmasq` handler in `net_op.py:153` to use `BridgedVXLanNetwork(n)._apply_remove_dnsmasq()` instead of `n.remove_dnsmasq()`. Leave `_network_update_dnsmasq` (which does broader reconciliation) untouched — Phase 6 cleans that up. (3) Update `net_macaddr_ip_op._remove_dhcp_lease` (`shakenfist/operations/net_macaddr_ip_op.py:108`) to use `BridgedVXLanNetwork(n)._apply_remove_dhcp_lease(self.ip, self.mac_address)` instead of `n.remove_dhcp_lease(...)`. (4) Update `node_inst_netdesc_op._instance_start` (line 245) and `node_inst_op._reset` (line 237 — confirm name during implementation) to use `BridgedVXLanNetwork(n)._apply_update_dnsmasq()` instead of `n.update_dnsmasq()`. Wire `ErrorReport` persistence in any dispatcher's outer `except` that doesn't already have it (check existing state per file). After this step, `grep -rn "n\.update_dnsmasq\|n\.remove_dnsmasq\|n\.remove_dhcp_lease" shakenfist/operations/` should return zero hits. Add unit tests covering each updated handler. Commit message subject: `operations: route dnsmasq dispatchers through BridgedVXLanNetwork.` |
| 4d. Flip Network methods | high | opus | none | Replace the bodies of five `Network` methods in `shakenfist/network/network.py`. The `if config.NODE_IS_NETWORK_NODE: ... else: enqueue ...` branching disappears in favour of always enqueueing. Each method returns the op handle. (1) `update_dnsmasq()` body: keep the `if not self.provide_dhcp and not self.provide_dns: return None` guard; then enqueue NetOp with `tasks=[net_tasks.network_apply_update_dnsmasq]`, `priority=PRIORITY.user_facing_high_io` (preserving today's priority), return loaded NetOp. (2) `remove_dnsmasq()` body: same guard, enqueue NetOp with `tasks=[net_tasks.network_apply_remove_dnsmasq]`, `priority=PRIORITY.user_facing`, return loaded NetOp. (3) `remove_dhcp_lease(ipv4, macaddr)` body: keep guard; enqueue `net_macaddr_ip_op` with `tasks=[nmi_tasks.remove_dhcp_lease]` (existing task), return loaded NetMacaddrIPOp. (4) `update_dns_entry(name, value)` body: keep the `if not self.provide_dns: return None` guard; keep the synchronous attribute update (`attrs = self._ensure_attributes(); attrs.hosteddns[name] = value; self._save_attributes(); self.add_event(...)`); then enqueue NetOp with `tasks=[net_tasks.network_apply_update_dnsmasq]`, return loaded NetOp. (5) `remove_dns_entry(name)` body: same shape as `update_dns_entry` but with the dict delete instead of assignment. Drop the now-redundant `enable_nat` import if it's no longer used. After this commit, `grep -n "_get_dnsmasq_object" shakenfist/network/network.py` should return only `def _get_dnsmasq_object` (no callers). Add tests in `shakenfist/tests/test_net.py` for each flipped method asserting the right task is enqueued. Commit message subject: `network: dnsmasq methods now enqueue.` |
| 4e. Internal sibling calls + Phase 3 fix | high | opus | none | Update three internal call sites inside `Network` methods that are themselves called from in-worker dispatchers, so they call `BridgedVXLanNetwork(self)._apply_X()` directly rather than going through the queue. (1) `network.py:688` — inside `create_on_network_node`, change `self.update_dnsmasq()` to wrap the guard plus `BridgedVXLanNetwork(self)._apply_update_dnsmasq()`. Specifically: `if self.provide_dhcp or self.provide_dns: BridgedVXLanNetwork(self)._apply_update_dnsmasq()`. (2) `network.py:753` — inside `delete_on_network_node`, change `self.remove_dnsmasq()` similarly to `if self.provide_dhcp or self.provide_dns: BridgedVXLanNetwork(self)._apply_remove_dnsmasq()`. (3) `network.py:759-760` — inside `delete_on_network_node`, **fix the latent Phase 3 deadlock bug**: replace `remove_nat_op = self.remove_nat(); remove_nat_op.raise_for_error()` with `BridgedVXLanNetwork(self)._apply_remove_nat()`. Update the surrounding comment to note that calling the worker class directly avoids the in-worker enqueue-to-self that the Phase 3 helper-loop fix would otherwise trigger. (4) Investigate `Network.remove_networkinterface_lease` (network.py:427) — read `shakenfist/network/interface.py:296` to determine the caller context. If the caller is in-worker, use `BridgedVXLanNetwork(self)._apply_remove_dhcp_lease(ni.ipv4, ni.macaddr)`. If external, use `op = self.remove_dhcp_lease(...); op.raise_for_error()`. Pick the right one and document the choice in the commit message. Add unit tests for the fix-in-passing. Commit message subject: `network: route internal sibling calls through BridgedVXLanNetwork.` |
| 4f. External REST handlers | medium | sonnet | none | Update `shakenfist/external_api/network.py:689` and `:716` to use `op = network_from_db.update_dns_entry(...); op.raise_for_error()` and `op = network_from_db.remove_dns_entry(...); op.raise_for_error()` respectively. Preserve today's synchronous-with-exception semantics. The `NetworkOperationFailed` will surface as a 500 (or whatever HTTP status the existing handler maps), or via the REST API's standard error path. Add focused regression tests if there's a simple seam; otherwise rely on the cluster_ci smoke suite. Commit message subject: `network: REST handlers use raise_for_error for DNS entries.` |
| 4g. Documentation | medium | sonnet | none | Extend `ARCHITECTURE.md`'s "Network Operation Error Handling" section with the Phase 4 additions: the migrated method list grows to all of `ensure_mesh`, `add_floating_ip`, `remove_floating_ip`, `route_address`, `unroute_address`, `remove_nat`, `update_dnsmasq`, `remove_dnsmasq`, `remove_dhcp_lease`, `update_dns_entry`, `remove_dns_entry`. The migrated op-type dispatchers extend to include `net_macaddr_ip_op`. Note the new pattern for in-worker Network methods calling sibling host-mutating methods: use `BridgedVXLanNetwork(self)._apply_X()` directly rather than the public method. Update `AGENTS.md` if anything in the orienting subsection is stale. Add a paragraph to `docs/developer_guide/network_dispatcher.md` noting the new `network_apply_update_dnsmasq` / `network_apply_remove_dnsmasq` tasks and the fix-in-passing for the Phase 3 deadlock. Commit message subject: `docs: phase 4 dnsmasq migration notes.` |

## Step ordering and dependencies

* 4a (schema) is independent and can land first.
* 4b (apply methods + ErrorReport registry) is independent of 4a (the registry extension is the only schema-ish thing it touches).
* 4c depends on 4a (new tasks must exist for the new dispatcher handlers to reference) and 4b (calls `_apply_*`).
* 4d depends on 4c (in-worker callers must already be routed through `BridgedVXLanNetwork` before the public methods flip, otherwise the net-worker enqueues to itself).
* 4e is the fix-in-passing and the internal-sibling-call updates; it depends on 4b (uses `_apply_*` methods). It can land any time after 4b — its safety property is independent of 4c/4d ordering because it never goes through the queue.
* 4f depends on 4d (the new return value).
* 4g after all.

Recommended landing order: 4a -> 4b -> 4c -> 4d -> 4e -> 4f -> 4g. 4e can land alongside 4d if convenient; the implementing agents should not parallelise 4d and 4c because they're in a dependency chain.

## Back brief

Before executing any step, the implementing sub-agent must back brief the management session confirming understanding. Each agent should explicitly confirm:

* The safety property: 4c (in-worker dispatcher updates) must land before 4d (public method flips). Otherwise the net-worker would enqueue to itself when `_network_remove_dnsmasq` runs (which today calls `n.remove_dnsmasq()` — that becomes an enqueue under step 4d's flip).
* The Phase 3 deadlock bug is a real existing bug. The Phase 3 step 3e change had `delete_on_network_node` call `self.remove_nat(); raise_for_error()` from inside `_network_destroy`. Step 4e fixes it via `BridgedVXLanNetwork(self)._apply_remove_nat()`. The implementing agent of step 4e must understand the in-worker-vs-external-caller distinction and apply the right pattern.
* The `update_dnsmasq` and `remove_dnsmasq` task chain: `Network.update_dnsmasq()` enqueues `network_apply_update_dnsmasq` (new); the existing `network_update_dnsmasq` task remains for backward compatibility and for the broader-reconciliation path. The two tasks are named similarly but do different things; do not conflate them.
* The DNS entry methods keep their synchronous attribute updates. `_apply_update_dns_entry` does **not** exist; `update_dns_entry` mutates DB state synchronously and then enqueues the dnsmasq restart.
* The provide-guard pattern. Today's `Network` methods have `if not self.provide_dhcp and not self.provide_dns: return` at the top. Under the flip, this guard stays at the `Network` method level (so an enqueue isn't made for networks that don't use dnsmasq). The `_apply_*` methods on `BridgedVXLanNetwork` do not include the guard — they assume the work is needed.

## Review checklist for the management session

After each step's sub-agent reports completion:

- [ ] Named files were modified; no unrelated files changed.
- [ ] `pre-commit run --files <changed files>` passes.
- [ ] New unit tests pass via `tox -e py3 -- <module>`.
- [ ] Commit message subject ends in a period, ≤ 50 characters; body wraps at 75.
- [ ] Commit body includes the `Prompt:` paragraph and the `Co-Authored-By` / `Signed-off-by` lines.
- [ ] For step 4b: any new typed exceptions surfaced by the lifted bodies were registered in `ErrorReport._EXCEPTION_CODE_REGISTRY` and `to_http()` in the same commit.
- [ ] For step 4c: `grep -rn "n\.update_dnsmasq\|n\.remove_dnsmasq\|n\.remove_dhcp_lease" shakenfist/operations/` returns zero hits.
- [ ] For step 4d: `grep -n "_get_dnsmasq_object" shakenfist/network/network.py` shows only the `def _get_dnsmasq_object`; no callers.
- [ ] For step 4e: the Phase 3 `raise_for_error` bug fix is in place. `grep -n "remove_nat_op\|self\.remove_nat()" shakenfist/network/network.py` shows no `raise_for_error` pattern remaining.

After all steps complete:

- [ ] cluster_ci functional smoke suite passes on the phase 4 PR.
- [ ] No new `ERROR` / `Traceback` lines in the cluster_ci stable-log gate.
- [ ] Network teardown latency in cluster_ci does not exhibit a 15-second `ASYNC_OP_TIMEOUT` per delete (evidence the Phase 3 deadlock-via-timeout bug is gone — though confirming this directly may require comparing log timings between runs).
- [ ] Master plan execution table for Phase 4 is updated from `Planning` to `Complete`.
