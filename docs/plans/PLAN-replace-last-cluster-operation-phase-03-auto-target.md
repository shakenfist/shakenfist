# Phase 3: Auto-target tracking, audit sweep, and privatise the setter

This is phase 3 of `PLAN-replace-last-cluster-operation.md`.
Read the master plan first (especially the *Decisions*
section). Phases 1 and 2 already landed:
`has_pending_cluster_operation()` exists,
`Network.is_okay()` is history-aware. Phase 3 makes target
writes automatic, sweeps every explicit
`set_last_cluster_operation` caller, and privatises the
setter so callers cannot use it wrong.

This is the largest phase. It splits into two sub-phases
that each build and pass tests independently.

## Decisions

These were resolved by the operator before sub-agent
dispatch. Each is recorded here so the implementing
agents can reference the chosen path without
re-litigating.

1. **Target writes are centralised in
   `enqueue_cluster_operation()`** in
   `shakenfist/schema/operations/util.py`. Each `model`
   class declares its targets via a `ClassVar`, and the
   central function iterates the declaration and writes
   one `cluster_operation_targets` row per target. This
   is the single point of truth for "target rows are
   always written".

2. **Declaration shape on each schema model is an
   explicit field-to-type mapping**:
   ```python
   target_fields: ClassVar[dict[str, ObjectType]] = {
       'instance_uuid': ObjectType.instance,
       'network_uuid': ObjectType.network,
   }
   ```
   Convention-based discovery was ruled out by master
   plan decision 2.

3. **Phase 3 ships as two commits inside this phase**:
   - **3a (purely additive)**: declare targets on every
     schema; have `enqueue_cluster_operation` write the
     rows automatically. After 3a the codebase has
     duplicate writes (auto + the existing manual ones)
     but the duplicate is harmless because
     `_direct_create_cluster_operation_target` already
     handles `IntegrityError` from the UNIQUE-on-
     `operation_uuid` constraint (`mariadb.py:5320-5325`).
   - **3b (the sweep)**: remove every explicit
     `set_last_cluster_operation` call, remove the eight
     `get_lock_attr('last_cluster_operation', ...)`
     wrappers, remove the manual target write in
     `node_blob_op.py:90-96`, and rename
     `set_last_cluster_operation` to
     `_set_last_cluster_operation`.

   Each commit must build and pass `pre-commit run --all-files`
   and `tox` independently.

4. **`node_blob_op.py:90-96` is removed in 3b** along
   with the other call sites. Be clean and consistent —
   once auto-targeting is in place, the manual call is a
   duplicate and there is no reason to keep it.

5. **Operation-execution-path callers are removed in 3b.**
   `operations/artifact_fetch_op.py:114`,
   `operations/node_inst_snap_op.py:157`,
   `operations/node_inst_netdesc_op.py:253`, and
   `network/interface.py:290` all call
   `set_last_cluster_operation(...)` from inside a
   running operation. After 3a, every operation
   auto-records its targets at *its own* enqueue time, so
   these execution-path calls are redundant. The 3b sweep
   removes them too.

6. **Resolve the artifact UUID earlier so auto-targeting
   covers it.** Sub-phase 3a left `artifact_fetch_op`
   declaring only `instance_uuid` as a target, because
   the schema carries no `artifact_uuid` field — the
   artifact has historically been resolved by URL +
   namespace at op-execution time. Sub-phase 3b adds
   `artifact_uuid: Optional[UUID4]` to the schema,
   updates the two enqueue sites to resolve eagerly, and
   adds `'artifact_uuid': ObjectType.ARTIFACT` to the
   `target_fields` declaration. Concretely:
   - `external_api/artifact.py:329-349` already calls
     `Artifact.from_url(... create_if_new=True)` before
     enqueueing — pass the resolved `a.uuid` into
     `afo_create_and_enqueue`.
   - `external_api/instance.py:820-849` is the only
     enqueue site that does not pre-resolve. Move the
     `Artifact.from_url(... create_if_new=True)` call
     into the disk loop *before* the enqueue, then pass
     `a.uuid`. This mirrors what the artifact API
     already does.
   - The execution-path `Artifact.from_url(... create_if_new=True)`
     inside `_image_fetch` (`operations/artifact_fetch_op.py:99-101`)
     stays in place: it is idempotent for the same
     URL+namespace key, and is still the correct fallback
     for any future caller that does not pre-resolve.

   **Side effect.** The instance create path now creates
   artifacts at API time instead of at op-execution time.
   If the instance create is aborted before the op runs,
   an orphan INITIAL-state artifact is left. This is not
   a regression: the same artifact gets created at op-
   execution time today, and the same abandonment
   scenario already exists if the op fails mid-execution.
   Eager creation just shifts *when* the artifact
   appears.

   With this decision, no documented exceptions remain
   and the rename of `set_last_cluster_operation` to
   `_set_last_cluster_operation` covers every call site.

## Goal

After phase 3 lands:
- Every operation enqueued via
  `*_create_and_enqueue` writes its target rows
  automatically. Callers do not call
  `set_last_cluster_operation` and cannot — the method is
  renamed `_set_last_cluster_operation` and used only by
  internal mariadb plumbing and tests.
- The eight `get_lock_attr('last_cluster_operation', ...)`
  wrappers are gone. They were vestigial — phase 1's
  exploration confirmed
  `_direct_create_cluster_operation_target` is a pure
  INSERT and AUTO_INCREMENT means concurrent writers
  cannot conflict (master plan decision 4).
- The bug class "caller forgot to call
  `set_last_cluster_operation` after enqueuing an op" is
  structurally impossible.

## Scope mapping (from the audit)

The phase 3 surface area maps as follows. Cite locations
are the `set_last_cluster_operation` call sites; the
preceding `*_create_and_enqueue` is in parentheses.

**External API enqueue (8 sites):**
- `external_api/network.py:74` (after `net_create_and_enqueue` at :69)
- `external_api/network.py:623` (after `nip_create_and_enqueue` at :616)
- `external_api/network.py:662` (after `nip_create_and_enqueue` at :655)
- `external_api/instance.py:864` (after `afo_create_and_enqueue` at :839)
- `external_api/instance.py:1028` *(network target for hot-plug)*
- `external_api/instance.py:1029` *(instance target for hot-plug)*
- `external_api/interface.py:99` (after `ni_create_and_enqueue` at :93)
- `external_api/interface.py:127` (after `nii_create_and_enqueue` at :120)
- `external_api/artifact.py:349` (after `afo_create_and_enqueue` at :341)

**Internal helper enqueue (3 sites):**
- `network/network.py:316` (inside `Network.create_floating_network` or similar)
- `instance.py:1802` (inside `Instance.enqueue_delete`)
- `instance.py:1901` (inside `Instance.snapshot_enqueue` or similar)

**Operation execution path (4 sites):**
- `operations/node_inst_snap_op.py:157`
- `operations/artifact_fetch_op.py:114`
- `operations/node_inst_netdesc_op.py:253`
- `network/interface.py:290`

**Daemon path (1 site):**
- `daemons/network/maintain.py:113` (inside the stray
  delete_wait cleanup branch)

**Get-lock-attr wrappers paired with the above (8 sites):**
- `external_api/network.py:68, 615, 654`
- `external_api/instance.py:823, 1009`
- `external_api/interface.py:92, 119`
- `external_api/artifact.py:340`

**Pre-existing manual target write (1 site, unique):**
- `operations/node_blob_op.py:90-96` — calls
  `mariadb.create_cluster_operation_target` directly. Remove
  in 3b alongside the others.

**Total**: 16 explicit `set_last_cluster_operation` calls +
1 manual target write + 8 lock-attr wrappers = **25 call
sites** swept by 3b.

## Detailed work

### Sub-phase 3a — auto-target writes

#### Step 1. Define the declaration shape

Each operation schema's `model` class gets a `ClassVar`
mapping target UUID fields to their object types. Use the
`ObjectType` enum from `shakenfist.schema.object_types`.

Recommended target maps per schema (operator may correct):

| Schema | `target_fields` |
|--------|-----------------|
| `artifact_fetch_op.py` | `{'artifact_uuid': artifact}` (and `{'instance_uuid': instance}` if non-None) |
| `imgcache_op.py` | `{'blob_uuid': blob}` |
| `net_iface_ip_op.py` | `{'network_uuid': network, 'interface_uuid': networkinterface}` |
| `net_iface_op.py` | `{'network_uuid': network, 'interface_uuid': networkinterface}` |
| `net_ip_op.py` | `{'network_uuid': network}` |
| `net_macaddr_ip_op.py` | `{'network_uuid': network}` |
| `net_op.py` | `{'network_uuid': network}` |
| `node_aop_op.py` | `{'agentoperation_uuid': agentoperation}` |
| `node_blob_op.py` | `{'blob_uuid': blob}` |
| `node_inst_net_iface_op.py` | `{'instance_uuid': instance, 'network_uuid': network, 'interface_uuid': networkinterface}` |
| `node_inst_netdesc_op.py` | `{'instance_uuid': instance}` |
| `node_inst_op.py` | `{'instance_uuid': instance}` |
| `node_inst_snap_op.py` | `{'instance_uuid': instance}` |
| `node_net_op.py` | `{'network_uuid': network}` |

Notes:
- `node_uuid` never appears (master plan decision 5).
- `artifact_fetch_op.py` may not currently have a top-level
  `artifact_uuid`; verify before declaring. If the op is
  fetching for an artifact, the artifact UUID lives somewhere
  in the schema — find it.
- `interface_uuid` mapped to `networkinterface` because that
  is the registered `ObjectType` (master plan caveat:
  "interface_uuid belongs to a NetworkInterface not an
  Interface"). Confirm against
  `shakenfist/schema/object_types.py`.
- Nullable fields: e.g. `artifact_fetch_op.py`'s
  `instance_uuid` is optional. The central writer must skip
  fields whose value on the model instance is None.

#### Step 2. Update `enqueue_cluster_operation` to write target rows

In `shakenfist/schema/operations/util.py:16-85`, after the
`mariadb.create_and_enqueue_cluster_operation(...)` call
succeeds, iterate the model's `target_fields` and for each
non-None UUID write a `cluster_operation_targets` row.

Outline:

```python
# After the existing enqueue succeeds:
target_fields = getattr(model_class, 'target_fields', {})
for field_name, object_type in target_fields.items():
    target_uuid = metadata.get(field_name)
    if target_uuid is None:
        continue
    mariadb.create_cluster_operation_target(
        operation_uuid=operation_uuid,
        operation_type=object_type_str,
        target_object_type=object_type,
        target_uuid=target_uuid,
        created_at=time.time(),
    )
```

Adjust naming and exact call shape to match the
surrounding code. The function already has access to the
metadata dict (since it dumps the model via
`m.model_dump(mode='json')`).

The model class needs to be passed in or recoverable. The
two paths:
- Pass `model_class` as an additional argument to
  `enqueue_cluster_operation` (cleanest — every caller
  already imports its own `model`).
- Look up via `object_type` if `enqueue_cluster_operation`
  knows it (more brittle).

*Recommended: pass `model_class` as an explicit argument*
so each caller declares the schema it is enqueuing. All 14
helpers will need a one-line update to pass `model` along.

#### Step 3. Verify duplicate writes are harmless

`_direct_create_cluster_operation_target`
(`mariadb.py:5320-5325`) already swallows `IntegrityError`
from the UNIQUE constraint on `operation_uuid`. After 3a,
every existing manual `set_last_cluster_operation` call
is a duplicate write that hits this swallow. Re-read the
swallow to confirm it returns success rather than raising,
so the existing callers continue to work unchanged.

#### Step 4. Tests for 3a

Add tests to `shakenfist/tests/test_enqueue_cluster_operation.py`
(or wherever `enqueue_cluster_operation` is currently
tested) that exercise:

1. **Single-target op**: enqueueing a `net_op`-shaped model
   writes exactly one target row.
2. **Multi-target op**: enqueueing a
   `node_inst_net_iface_op`-shaped model writes three target
   rows (instance, network, networkinterface).
3. **Nullable target field**: enqueueing an
   `artifact_fetch_op` with `instance_uuid=None` writes the
   artifact target but not the instance target.
4. **Schema with no `target_fields`** (defensive): if a
   schema does not declare `target_fields`, the central
   writer is a no-op (skip all writes, do not crash).

#### Step 5. Lint and test 3a

```bash
pre-commit run --all-files
tox
```

After 3a, the codebase still has every explicit
`set_last_cluster_operation` call. The IntegrityError
swallow means duplicates are silently absorbed. Tests
should still pass.

### Sub-phase 3b — sweep callers and privatise

#### Step 6. Remove explicit `set_last_cluster_operation` calls

For each of the 16 call sites in the *Scope mapping*
section, remove the explicit call. Where the call is
inside a `try/except RuntimeError` (e.g.
`instance.py:1803-1804`,
`daemons/network/maintain.py:114-115`), remove the entire
try/except.

Verify by inspection that each removal does not lose other
behaviour — the only effect should be a duplicate row write
becoming a single row write.

#### Step 7. Remove `get_lock_attr('last_cluster_operation', ...)` wrappers

For each of the 8 wrappers, replace the `with` block with
its body. Since the body is the helper call plus the now-
removed `set_last_cluster_operation`, this means just
unwrapping the `with` line and dedenting one level. No new
imports or refactoring.

The lock was vestigial: master plan decision 4 confirmed
that `_direct_create_cluster_operation_target` is a pure
INSERT with AUTO_INCREMENT, so concurrent writers cannot
conflict.

#### Step 8. Remove `node_blob_op.py:90-96` manual write

Specific to that file: the manual
`mariadb.create_cluster_operation_target(...)` call
becomes redundant once 3a writes the same row at
enqueue time. Delete it.

#### Step 9. Privatise the setter

In `shakenfist/baseobject.py`, rename
`set_last_cluster_operation` (line 698) to
`_set_last_cluster_operation`. After the sweep above, no
caller outside `baseobject.py` itself should reference
the old name. Re-run the grep to confirm:

```
grep -rn 'set_last_cluster_operation\b' shakenfist/ \
    --include='*.py' \
    | grep -v '_set_last_cluster_operation'
```

Expected matches after rename: only inside `baseobject.py`
and tests in `shakenfist/tests/test_cluster_operation_targets.py`.
Test files should be updated to call the renamed method.

If `enqueue_cluster_operation` ends up calling
`_set_last_cluster_operation` internally as part of the
3a writes, that is the intended use of the now-private
method.

#### Step 10. Tests for 3b

For each call site removed, find the test that was
exercising the surrounding helper. Confirm:

- The test still passes — the helper still produces a
  target row, just via the auto-targeting path now.
- If the test mocked `set_last_cluster_operation` directly
  (e.g. `tests/test_external_api.py:79`), update to mock
  the new path: either
  `mariadb.create_cluster_operation_target` or
  `enqueue_cluster_operation` itself.

Add a new test that asserts the regression: spawn a
`node_inst_net_iface_op` and verify three target rows were
written (instance, network, networkinterface), proving the
hot-plug case from commit `8923391c` cannot regress.

#### Step 11. Lint and test 3b

```bash
pre-commit run --all-files
tox
```

## Files expected to change

### 3a (additive)

- `shakenfist/schema/operations/util.py` — extend
  `enqueue_cluster_operation` to write target rows.
- `shakenfist/schema/operations/artifact_fetch_op.py`
- `shakenfist/schema/operations/imgcache_op.py`
- `shakenfist/schema/operations/net_iface_ip_op.py`
- `shakenfist/schema/operations/net_iface_op.py`
- `shakenfist/schema/operations/net_ip_op.py`
- `shakenfist/schema/operations/net_macaddr_ip_op.py`
- `shakenfist/schema/operations/net_op.py`
- `shakenfist/schema/operations/node_aop_op.py`
- `shakenfist/schema/operations/node_blob_op.py`
- `shakenfist/schema/operations/node_inst_net_iface_op.py`
- `shakenfist/schema/operations/node_inst_netdesc_op.py`
- `shakenfist/schema/operations/node_inst_op.py`
- `shakenfist/schema/operations/node_inst_snap_op.py`
- `shakenfist/schema/operations/node_net_op.py`
- `shakenfist/tests/test_enqueue_cluster_operation.py`
  (existing or new) — coverage for the four cases above.

### 3b (sweep + privatise + artifact_uuid)

- `shakenfist/schema/operations/artifact_fetch_op.py` —
  add `artifact_uuid: Optional[UUID4]` field to the
  model; add `'artifact_uuid': ObjectType.ARTIFACT` to
  the `target_fields` declaration.
- `shakenfist/baseobject.py` — rename method.
- `shakenfist/external_api/network.py` — drop 3 lock
  wrappers and 3 set calls.
- `shakenfist/external_api/instance.py` — drop 2 lock
  wrappers and 3 set calls; add the
  `Artifact.from_url(... create_if_new=True)` resolve-
  early step inside the disk loop, pass `a.uuid` as
  `artifact_uuid` to `afo_create_and_enqueue`.
- `shakenfist/external_api/interface.py` — drop 2 lock
  wrappers and 2 set calls.
- `shakenfist/external_api/artifact.py` — drop 1 lock
  wrapper and 1 set call; pass the already-resolved
  `a.uuid` as `artifact_uuid` to
  `afo_create_and_enqueue`.
- `shakenfist/network/network.py` — drop 1 set call.
- `shakenfist/network/interface.py` — drop 1 set call.
- `shakenfist/instance.py` — drop 2 set calls and the
  surrounding `try/except RuntimeError` blocks.
- `shakenfist/operations/node_inst_snap_op.py` — drop 1
  set call.
- `shakenfist/operations/artifact_fetch_op.py` — drop 1
  set call.
- `shakenfist/operations/node_inst_netdesc_op.py` — drop
  1 set call.
- `shakenfist/operations/node_blob_op.py` — drop 1 manual
  target write.
- `shakenfist/daemons/network/maintain.py` — drop 1 set
  call and the surrounding try/except.
- `shakenfist/tests/test_cluster_operation_targets.py` —
  rename method references.
- `shakenfist/tests/test_external_api.py` — update mock
  to new path.
- Possibly `shakenfist/tests/test_object_metadata.py` and
  `shakenfist/tests/test_net.py` if they touched the
  setter — verify against the test-file audit (E above)
  before changes.

## Commit shape

**Two commits**, both shipped in this phase:

1. `Auto-write cluster_operation_targets at enqueue time.`
   — sub-phase 3a: schema declarations and central writer.
2. `Remove explicit set_last_cluster_operation callers; privatise the setter.`
   — sub-phase 3b: 25-site sweep and rename.

Each commit must build and pass `pre-commit run --all-files`
and `tox` independently.

## Acceptance criteria

After 3a:
- `pre-commit run --all-files` passes.
- `tox` passes.
- Every operation schema has a `target_fields` declaration
  (or is explicitly documented as having no targets, which
  none currently do).
- Test suite proves the central writer fires and writes
  the right rows for single-target, multi-target,
  nullable, and undeclared cases.

After 3b:
- `pre-commit run --all-files` passes.
- `tox` passes.
- The grep
  `grep -rn 'set_last_cluster_operation\b' shakenfist/ --include='*.py' | grep -v '_set_last_cluster_operation'`
  returns matches only inside `baseobject.py` and test
  files.
- The grep
  `grep -rn "get_lock_attr('last_cluster_operation'" shakenfist/`
  returns no matches.
- The hot-plug interface flow that originally produced
  CI failure `8923391c` writes three target rows
  (instance, network, networkinterface) without any
  caller-side bookkeeping.
- The renamed `_set_last_cluster_operation` is referenced
  only from inside `baseobject.py` itself and tests that
  exercise it directly.

## Out of scope

- Bucket D follow-up (list-based pending-ops query for
  `Instance.enqueue_delete` and
  `baseobject.get_cluster_operations`). Tracked in master
  plan *Future work*.
- Dropping the dead `last_cluster_operation_json`
  column on `object_metadata` (phase 4).
- Documentation updates (phase 5).

## Agent guidance

The master plan flags phase 3 as the most worthy of opus —
"it touches every operation schema, the queue helpers, and
changes a contract that ~20 callers rely on". Sub-phase 3a
is the central design change (declaration shape + writer
plumbing); 3b is a mechanical sweep. The recommended
allocation:

- Spawn an opus sub-agent for 3a.
- Once 3a is committed and the management session has
  reviewed it, spawn a sonnet sub-agent for 3b with a
  brief that includes the exact list of 25 sites to sweep
  and the rename to perform.

Both sub-agents must be told the same constraint: do not
touch the Bucket D consumers
(`Instance.enqueue_delete` tree-walk,
`baseobject.get_cluster_operations`) — those are tracked
as future work.
