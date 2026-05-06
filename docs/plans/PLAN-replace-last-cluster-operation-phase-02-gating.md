# Phase 2: Switch `Network.is_okay()` and other gating callers

This is phase 2 of `PLAN-replace-last-cluster-operation.md`.
Phase 1 added `has_pending_cluster_operation()` and the
underlying `mariadb.has_pending_cluster_operation_target`
query — read both that phase plan and the master plan
(especially the *Decisions* section) before starting work
here.

## Goal

Stop using the legacy single-pointer `last_cluster_operation`
read for gating decisions. Switch every true gating call
site to the history-aware
`has_pending_cluster_operation()` method introduced in
phase 1.

After this phase lands:
- `Network.is_okay()` returns True while *any* non-terminal
  cluster operation targets the network, regardless of how
  many later terminal operations have piled on top — fixing
  the latest-only race that caused the
  `recreating not okay network on hypervisor` CI failures.
- The network maintainer
  (`shakenfist/daemons/network/maintain.py`) defers its
  recreate path correctly because it calls `is_okay()`.
- The legacy `last_cluster_operation` *property* still
  exists and is still consumed by `external_view()`,
  `runs_after=[...]` chains, and the Instance delete
  cancellation logic. Those consumers are not "gating"
  and are deliberately left alone in this phase (see
  *Out of scope* below).

## Audit findings

A repository-wide audit grounding this phase:

```
grep -rn 'is_okay\|last_cluster_operation' shakenfist/ \
    --include='*.py' \
    | grep -v ^shakenfist/tests/ \
    | grep -v ^shakenfist/protos/
```

The returned matches fall into four buckets:

**Bucket A — true gating (must change in this phase):**

- `shakenfist/network/network.py:503-516` — `Network.is_okay()`.
  The 11-line prelude that reads `self.last_cluster_operation`,
  looks up the op object via `get_object_class(...).from_db(...)`,
  and checks whether its state is terminal. This is the only
  true gating call in the codebase.

**Bucket B — `runs_after=[obj.last_cluster_operation]` chain
dependencies (deliberately left alone):**

- `shakenfist/network/network.py:314`
- `shakenfist/instance.py:1799, 1899`
- `shakenfist/external_api/instance.py:863, 1027`
- `shakenfist/external_api/artifact.py:348`

These pass the latest op (terminal or not) into a new op's
`runs_after` list to chain dependencies. Master plan
decision 1 settled this: keep the latest-of-any-state
semantics here. Phase 2 does not touch them.

**Bucket C — `external_view()` projection (deliberately
left alone):**

- `shakenfist/instance.py:519`
- `shakenfist/network/network.py:333`
- `shakenfist/artifact.py:378`

External API consumers see the same shape as before.
Master plan decision 1.

**Bucket D — list-style consumers of the operation tree
(out of scope, see below):**

- `shakenfist/instance.py:1772-1777` — `Instance.enqueue_delete()`
  walks the cluster operations tree starting from the latest
  op so it can abort each one before deleting the instance.
- `shakenfist/baseobject.py:722-741` — `get_cluster_operations()`,
  consumed by `external_api/network.py:764`,
  `external_api/artifact.py:856`,
  `external_api/instance.py:1725`. Returns a list of
  outstanding ops by traversing
  `last_cluster_operation.depends_on/runs_after`.

These have the same latest-only race as `is_okay()` but
need a *list of in-flight ops*, not a boolean. The right
fix is a separate `get_pending_cluster_operation_targets()`
query, plus rewiring `get_cluster_operations()` and
`enqueue_delete()` to use it. That change is larger than
phase 2 should swallow and is genuinely a separate
concern (the failure mode for an unaborted op during
delete is different from the maintainer's recreate race).
**Out of scope for this phase.** Track in the master plan's
*Future work* section.

The cleaner daemon (`shakenfist/daemons/cleaner/`) and the
queues daemon do not read `last_cluster_operation` and have
no `is_okay`-style gating.

## Detailed work

### 1. `Network.is_okay()` — replace the LCO prelude

Current implementation (`network/network.py:503-530`):

```python
def is_okay(self):
    """Check if network is created and running."""
    last_op = self.last_cluster_operation
    if last_op and last_op.get('op_type'):
        op = get_object_class(last_op.get('op_type')).from_db(
            last_op.get('op_uuid'), suppress_failure_audit=True)
        if op and op.state.value not in [op.STATE_COMPLETE,
                                         op.STATE_ABORT,
                                         op.STATE_ERROR,
                                         op.STATE_DELETED]:
            # There is an incomplete operation so we assume this network
            # is ok for now.
            return True

    if not self.is_created():
        ...
```

Replace lines 505-515 with a single call to the new
method:

```python
def is_okay(self):
    """Check if network is created and running."""
    if self.has_pending_cluster_operation():
        # An operation is in flight against this network. Defer
        # the maintainer's recreate path so it does not race with
        # the queue worker.
        return True

    if not self.is_created():
        ...
```

The semantic change:
- **Before:** read the latest target row, look up the op,
  return True only if *that single op* is non-terminal.
  A later terminal op masks an earlier in-flight op.
- **After:** ask the database "is any target row's op
  non-terminal?". History-aware; the bug the plan exists
  to fix.

The `get_object_class` import on this file may become
unused after this change. Check and remove if so. (It is
likely still used elsewhere — search before removing.)

### 2. Verify the network maintainer is fixed by the change above

`shakenfist/daemons/network/maintain.py:127` calls
`n.is_okay()`. No code change needed here — the maintainer
inherits the fix automatically. The phase plan for phase 3
will revisit `maintain.py:113` to remove the
`set_last_cluster_operation` call there, but that is not
this phase's concern.

### 3. Re-run the audit

Before declaring phase 2 done, the implementing agent must
re-run the repository-wide grep above and confirm no new
`is_okay` methods or `last_cluster_operation`-reading
gating sites have appeared since this plan was written. If
any are found, escalate to the management session rather
than fixing them silently — phase 2's brief is "switch
known gating callers", not "find and fix all gating".

### 4. Tests

The existing test file is
`shakenfist/tests/test_network_is_okay.py` if one exists,
otherwise `tests/test_network.py` or similar. Read first
to find where `is_okay` is currently tested.

Required new test cases:

1. **`test_is_okay_true_when_pending_operation`** —
   `has_pending_cluster_operation` returns True, mocked to
   skip the rest of the body. Method returns True without
   calling `is_created` / `is_dnsmasq_running`.
2. **`test_is_okay_falls_through_when_no_pending_operation`** —
   `has_pending_cluster_operation` returns False, mocked
   `is_created` returns True (and `is_dnsmasq_running` if
   the network is on a network node and provides DHCP/DNS).
   Method returns True via the fall-through path.
3. **`test_is_okay_false_when_not_created_and_no_pending`** —
   `has_pending_cluster_operation` returns False,
   `is_created` returns False. Method returns False (the
   pre-existing "network not ok, is not created" path).
4. **`test_is_okay_history_aware_race_fix`** — *the
   regression test for the bug this plan fixes*. Mock
   `has_pending_cluster_operation` to return True and
   verify `is_okay` returns True without inspecting any
   particular op state. The point of the test is to lock
   in that the gating decision rests on the
   `has_pending_cluster_operation` return value alone, so
   a future regression to "look up the latest op and check
   its state" gets caught by CI.

Existing tests of `is_okay` (if any) probably mock
`last_cluster_operation` directly. Update those mocks to
target `has_pending_cluster_operation` instead.

### 5. Lint and test

```bash
pre-commit run --all-files
tox
```

Functional CI is the strongest verification we have for
this fix — the merge-queue Guests run is what caught the
original race. The phase plan does not require running
functional CI before commit (master plan handles that at
the end), but the implementing agent should note that
`pre-commit run --all-files` and `tox` are necessary but
not sufficient.

## Files expected to change

- `shakenfist/network/network.py` — `is_okay()` body, and
  potentially the `get_object_class` import if it becomes
  unused (check first).
- `shakenfist/tests/test_network*.py` (or wherever
  `is_okay` is tested) — replace LCO mocks with
  `has_pending_cluster_operation` mocks; add the four new
  test cases above.

No other files should change. In particular,
`Network.set_last_cluster_operation` calls,
`maintain.py:113`, the audit list in
`network/network.py` (731, 782, 798, 816, 839, 863), and
the entire `Instance.enqueue_delete` flow are *not*
modified in this phase.

## Commit shape

One commit, message along the lines of:

```
Switch Network.is_okay() to has_pending_cluster_operation.

Replaces the legacy single-pointer last_cluster_operation
prelude with the history-aware query introduced in phase 1.
A later terminal cluster operation against the same network
no longer masks an earlier in-flight operation, so the
network maintainer correctly defers its recreate path while
any op is in flight -- the fix for the recurring
"recreating not okay network on hypervisor" CI failure.

No other gating call sites exist; the audit confirmed
Network.is_okay() is the only true gating consumer.
runs_after chains, external_view projections, and the
Instance.enqueue_delete tree-walk are deliberately left
on the legacy property -- master plan decision 1 (keep
latest-of-any-state for those consumers) and a separate
follow-up for list-style consumers.
```

(Plus standard `Prompt:`, `Signed-off-by`,
`Co-Authored-By`.)

## Acceptance criteria

- `pre-commit run --all-files` passes.
- `tox` passes, including the new tests on `is_okay`.
- The `is_okay()` body no longer references
  `last_cluster_operation`, `get_object_class`,
  `STATE_COMPLETE`, `STATE_ABORT`, `STATE_ERROR`, or
  `STATE_DELETED`.
- The audit grep above produces no surprises (no new
  gating callers since this plan was written).
- The unit test that exercises the latest-only race fix
  is genuinely testing the new behaviour, not just
  asserting `has_pending_cluster_operation` is called.

## Out of scope

The following all share the same latest-only race as
`is_okay()` but are deliberately not addressed in this
phase. They need a list-based query, not a boolean, and
the failure mode is different enough that bundling them
would obscure the gating fix:

- `shakenfist/baseobject.py:722` `get_cluster_operations()`
  and its three external API consumers
  (`external_api/network.py:764`,
  `external_api/artifact.py:856`,
  `external_api/instance.py:1725`).
- `shakenfist/instance.py:1772-1777`
  `Instance.enqueue_delete()` operation-tree walk.

Track these in the master plan's *Future work* section as
a follow-up: "expose a full pending-operations query and
rewire `get_cluster_operations` and `enqueue_delete` to
use it". Note in the phase 2 commit message that this is
intentional.

Phase 2 also does not:

- Rename or privatise `set_last_cluster_operation`
  (phase 3).
- Remove any `set_last_cluster_operation` call sites
  (phase 3).
- Drop the dead `last_cluster_operation_json` column
  (phase 4).
- Update operator documentation (phase 5).
