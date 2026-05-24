# Phase 8: NodeLock removal

## Context

The stability-branch commit `bd9e1869` ("network: serialise
host-mutating ops with NodeLock.") wrapped six host-mutating
methods on `Network` in `with self.get_lock(op='Network <verb>',
global_scope=False):` to serialise concurrent invocations from
the four daemons that historically called those methods directly
(`sf-net`'s `net-worker` and `maintain` thread, `sf-queues`,
`sf-api`, plus the instance lifecycle code in `instance.py`). That
fix was always documented as short-term: the longer-term
queue-only facade refactor (Phases 1–7 of the master plan) would
make the locks redundant by removing every direct caller.

Phases 2–5 of the master plan moved those six methods, plus
seven more, into `BridgedVXLanNetwork._apply_*` methods on the
worker class. The `get_lock` wrappers came along verbatim — each
phase plan deliberately preserved them and pointed at Phase 8 as
the place where they'd be removed. Phase 6 finished the
maintain-thread migration; Phase 7 removed the last
synchronous-call API surface
(`@redirect_to_network_node`). With Phase 7 landed, there is
exactly one caller of every `_apply_*` method: the net-worker
dispatcher loop in `shakenfist/daemons/network/workitem.py`,
which is single-threaded by construction. The locks are now
provably redundant.

Phase 8 removes them.

## What Phase 8 ships

A single behaviour change — the 13 per-network `NodeLock` wrappers
inside `BridgedVXLanNetwork._apply_*` methods are removed and the
method bodies dedented one level. No logic changes, no signature
changes, no public API changes.

Affected methods (line numbers as of phase 7 head
`82d0af2f`, in `shakenfist/network/bridged_vxlan_network.py`):

| Method | Current lock line | Lock op string |
|--------|-------------------|----------------|
| `_apply_ensure_mesh` | 98 | `'Network ensure mesh'` |
| `_apply_add_floating_ip` | 156 | `'Network add floating IP'` |
| `_apply_remove_floating_ip` | 173 | `'Network remove floating IP'` |
| `_apply_route_address` | 190 | `'Network route address'` |
| `_apply_unroute_address` | 208 | `'Network unroute address'` |
| `_apply_remove_nat` | 224 | `'Network remove NAT'` |
| `_apply_update_dnsmasq` | 240 | `'Network update dnsmasq'` |
| `_apply_remove_dnsmasq` | 254 | `'Network remove dnsmasq'` |
| `_apply_remove_dhcp_lease` | 269 | `'Network remove DHCP lease'` |
| `_apply_create_on_hypervisor` | 292 | `'Network create on hypervisor'` |
| `_apply_create_on_network_node` | 331 | `'Network create on network node'` |
| `_apply_delete_on_hypervisor` | 456 | `'Network delete'` |
| `_apply_delete_on_network_node` | 493 | `'Network delete'` |

(`_apply_enable_nat` does not currently hold a lock; nothing to
change there. The exact op-string spellings in the table come
from the current source and should be cross-checked when
implementing — the verb may have drifted slightly.)

The 13 docstrings that mention "the `get_lock` wrapper is
preserved (Phase 8 removes it)" or similar are also updated:
remove the forward-reference language and update the body to
reflect the new "single-threaded dispatcher is the
serialisation point" reality.

Unit tests in `shakenfist/tests/test_bridged_vxlan_network.py`
that assert `network.get_lock.assert_called_once_with(...)` are
updated to `network.get_lock.assert_not_called()`. The mock
setup that wires `get_lock` as a context manager (lines 28–30 of
the test file) stays — no harm in mocking an unused attribute,
and removing the setup would force every test to opt out.

A small doc update accompanies the code change: ARCHITECTURE.md,
AGENTS.md, and `docs/developer_guide/network_dispatcher.md` each
gain a sentence noting that NodeLock has been removed from the
`_apply_*` methods and that serialisation is now provided by the
single-threaded dispatcher loop. This is **not** the full Phase
9 doc sweep — it's a focused "the locks are gone" note.

## What Phase 8 does **not** do

* **No changes to `NodeLock` itself** or `DatabaseBackedObject.get_lock`.
  Both remain in `shakenfist/util/concurrency.py` and
  `shakenfist/baseobject.py` respectively. Other code paths
  (eventlog chunks, blob transfers, the daemon health-check
  decorator) still legitimately use NodeLock.
* **No "migration flag" removal.** The master plan row mentions
  "per-method migration flags" but a grep of the post-Phase-7
  tree shows none exist — Phases 2–5 migrated each method
  cleanly, in-place, without leaving feature flags behind. There
  is nothing here to remove.
* **No removal of `_apply_*` methods, `BridgedVXLanNetwork` class
  structure, or any other architectural surgery.** The facade
  stays exactly as Phase 7 left it.
* **No documentation sweep beyond the "locks gone" note.** That
  sweep is Phase 9's scope.
* **No changes to test fixtures that mock `get_lock` for other
  reasons.** Only the bridged-vxlan-network tests are touched.

## Key references in the existing code

* `shakenfist/network/bridged_vxlan_network.py` — the file
  carrying the 13 lock wrappers. Methods named `_apply_*`.
* `shakenfist/util/concurrency.py:361` — `class NodeLock`. Not
  touched.
* `shakenfist/baseobject.py:498-509` — `DatabaseBackedObject.get_lock`.
  Not touched.
* `shakenfist/daemons/network/workitem.py` — the single-threaded
  dispatcher that now provides serialisation. The "single-worker
  safety" comment placed in this file by Phase 1 is the load-
  bearing invariant for Phase 8's correctness; review checklist
  re-confirms it.
* `shakenfist/tests/test_bridged_vxlan_network.py` — the tests.

## Success criteria

* `grep -n 'with self.network.get_lock' shakenfist/network/bridged_vxlan_network.py`
  returns zero hits.
* `grep -rn 'get_lock' shakenfist/network/ --include='*.py'`
  returns zero hits in the `network/` package.
* `grep -n 'Phase 8' shakenfist/network/bridged_vxlan_network.py`
  returns zero hits (no more forward references).
* Full `tox -e py3` passes (no test fixture breakage from the
  test updates).
* `pre-commit run --all-files` passes.
* `cluster_ci` smoke suite passes on the phase 8 PR. The risk to
  watch for here is some assumption elsewhere in the codebase
  that the per-network NodeLock provides cross-daemon
  serialisation. The dispatcher is single-threaded *within* the
  net-worker, but `sf-queues` / `sf-api` / `instance.py` still
  run in their own processes. The argument for safety is that
  Phases 2–7 already removed every direct host-mutating call
  from those other daemons; they now go through the queue and
  hit the same single-threaded dispatcher. The smoke suite is
  the load-bearing verification of that argument.

## Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a. Remove the lock wrappers and update tests | low | sonnet | none | Edit `shakenfist/network/bridged_vxlan_network.py`. For each of the 13 `with self.network.get_lock(op='...', global_scope=False):` blocks, remove the `with` line and dedent the body by 4 spaces. Where the docstring above the method mentions "Phase 8 removes it" / "the `get_lock` wrapper is preserved" / similar forward-reference language, rewrite the relevant docstring sentence to say something like "the single-threaded net-worker dispatcher is the only caller and provides natural serialisation; no explicit lock is required". Keep the docstrings concise. The 13 methods to touch are listed in the master plan body (and in this phase plan above). Also: confirm via `grep -n 'Phase 8' shakenfist/network/bridged_vxlan_network.py` that no forward references remain. Then edit `shakenfist/tests/test_bridged_vxlan_network.py`: every `network.get_lock.assert_called_once_with(...)` assertion becomes `network.get_lock.assert_not_called()`. Leave the mock setup at the top of the file (lines 28-30) alone. Run `tox -e py3` to confirm all tests pass. Run `pre-commit run --all-files`. Commit message subject: `network: remove redundant NodeLock from _apply_* methods.` The commit body should explain that the single-threaded dispatcher in `shakenfist/daemons/network/workitem.py` is the new serialisation point, reference the original stability-branch commit `bd9e1869` (the locks were known to be short-term), and confirm via grep that no other callers of `network.get_lock` exist. |
| 8b. Documentation | low | sonnet | none | Three short doc updates: (1) `ARCHITECTURE.md` — the "Network Operation Error Handling" section gains a one-paragraph "Phase 8 — NodeLock removal" subsection at the end noting that the per-network `NodeLock` wrappers inside `BridgedVXLanNetwork._apply_*` were removed because the single-threaded net-worker dispatcher provides natural serialisation; cross-daemon serialisation is now via the queue itself (only one daemon, `sf-net`, dequeues and executes work for any given network). (2) `AGENTS.md` — the "Phase 7 — REST contract" subsection's closing sentence currently says "Phase 8 (NodeLock removal) is the only remaining phase." Update it to a "Phase 8 — NodeLock removal" subsection that briefly notes the locks are gone, gives the load-bearing reason (single-threaded dispatcher), and points at Phase 9 (docs sweep) as the only remaining phase. (3) `docs/developer_guide/network_dispatcher.md` — append a short "Phase 8: NodeLock removal" section explaining the rationale and pointing at the dispatcher's single-worker safety comment as the invariant that makes the removal safe. No new doc files; no changes to `mkdocs.yml.tmpl`. Commit message subject: `docs: phase 8 NodeLock removal.` |

## Step ordering and dependencies

* 8a is the entire substantive change. It is self-contained.
* 8b is the doc note. It can land in the same commit as 8a if the
  reviewer prefers, but keeping it separate makes the code change
  reviewable on its own and follows the pattern of earlier
  phases.

Recommended landing order: 8a → 8b.

## Back brief

Before executing 8a, the implementing sub-agent must back brief
the management session. Confirm:

* The single-worker safety invariant. The net-worker dispatcher
  in `workitem.py` has a prominent comment placed by Phase 1
  explaining that the exponential backoff map is only safe
  because the dispatcher is single-threaded. The same invariant
  is what makes Phase 8 safe — there is exactly one caller of
  every `_apply_*` method at runtime, and that caller is
  serialised by being single-threaded. Re-read that comment
  before removing locks.
* No external callers of `network.get_lock`. A `grep -rn
  '\.get_lock(' shakenfist/ --include='*.py' | grep -v 'test_\|
  baseobject.py' | grep -iE 'network|self.network'` immediately
  before the edit should return only the 13 sites in
  `bridged_vxlan_network.py`. If anything else shows up,
  **stop and report** rather than removing — that's a sign Phase
  8's premise is wrong.
* The lock wrappers all use `global_scope=False` (per-node).
  None of them are `ClusterLock`s. The Phase 8 reasoning does
  not apply to `ClusterLock`s — those serialise across the
  cluster and the single-worker argument does not cover them.
  If any `with self.network.get_lock(...)` in `bridged_vxlan_network.py`
  is missing the `global_scope=False` argument (i.e. defaulting
  to `True` and therefore being a ClusterLock), **stop and
  report**.
* The test assertions are mutex-pair: every
  `network.get_lock.assert_called_once_with(...)` becomes
  `assert_not_called()`. Removing the assertion entirely is
  also acceptable, but converting to `assert_not_called` is
  safer because it actively verifies the lock isn't being taken.

## Review checklist for the management session

After 8a's sub-agent reports completion:

- [ ] Named files were modified; no unrelated files changed.
- [ ] `grep -n 'with self.network.get_lock' shakenfist/network/bridged_vxlan_network.py`
      returns zero hits.
- [ ] `grep -rn 'get_lock' shakenfist/network/ --include='*.py'`
      returns zero hits in the `network/` package.
- [ ] `grep -n 'Phase 8' shakenfist/network/bridged_vxlan_network.py`
      returns zero hits.
- [ ] `pre-commit run --files <changed files>` passes.
- [ ] All tests pass (`tox -e py3`).
- [ ] Commit message subject ends in a period, ≤ 50 characters; body wraps at 75.
- [ ] Commit body includes the `Prompt:` paragraph, references the
      original stability-branch commit `bd9e1869`, and the
      `Co-Authored-By` / `Signed-off-by` lines.

After 8b's sub-agent reports completion:

- [ ] The three named doc files (ARCHITECTURE.md, AGENTS.md,
      network_dispatcher.md) are each updated.
- [ ] `mkdocs build` does not warn or error (no broken links).
- [ ] No new doc files; `mkdocs.yml.tmpl` not touched.

After both steps complete:

- [ ] `cluster_ci` functional smoke suite passes on the phase 8 PR.
- [ ] Master plan execution table flipped: Phase 8 → Complete.
      Phase 9 remains as the only outstanding phase.
