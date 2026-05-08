# Phase 5: Documentation and final audit

This is phase 5 of `PLAN-replace-last-cluster-operation.md`.
Phases 1–4 already landed: the new history-aware query
exists, `Network.is_okay()` is rewired, target rows are
written automatically by `enqueue_cluster_operation`, every
explicit `set_last_cluster_operation` caller has been
swept, the setter is now private, and the dead
`object_metadata.last_cluster_operation_json` column has
been dropped. Phase 5 finishes the plan: docs are brought
in line with the new design, the master plan's
*Bugs fixed during this work* section is filled in, the
plan-status index is bumped to Complete, and a final
codebase audit confirms there are no stray references.

## Goal

After phase 5 lands:
- `ARCHITECTURE.md`'s *Cluster Operation Tracking*
  subsection describes the new model (auto-target writes
  inside `enqueue_cluster_operation`, history-aware
  gating via `has_pending_cluster_operation()`, the now-
  private `_set_last_cluster_operation`).
- `CLAUDE.md`'s *Object Metadata* and *Cluster Operation
  Targets* entries no longer claim
  `object_metadata.last_cluster_operation` exists or that
  there is a dual-write fallback.
- `docs/operator_guide/database.md` matches the same
  reality, and gains a short subsection that documents
  the gating model so operators can understand what the
  table is for and what the prune does.
- `docs/plans/index.md` shows all five phases as
  Complete.
- `docs/plans/PLAN-replace-last-cluster-operation.md`'s
  *Bugs fixed during this work* section has the two CI
  failures it actually fixed (the hot-plug
  triple-target case from commit `8923391c`, and the
  `recreating not okay network on hypervisor`
  maintainer race).
- A final repository-wide audit confirms no stray
  references to the old setter name, the old gating
  pattern, or the dead column.

## Audit findings

The grep-based audit identified five files plus the
master plan that need updating. Doc-file edits only — no
code changes in this phase.

**File-by-file:**

- `ARCHITECTURE.md:122-141` — *Cluster Operation Tracking*
  subsection. Currently still describes
  `set_last_cluster_operation()` as a public API that
  callers invoke and that "raises RuntimeError on
  failure so API endpoints return 500" — both claims
  are now wrong. Needs a substantive rewrite to
  describe auto-target writes and history-aware gating.
- `CLAUDE.md:426-437` — entries on *Object Metadata* and
  *Cluster Operation Targets*. Object Metadata still
  says "key-value pairs and last_cluster_operation"; the
  column is gone, drop that wording. Cluster Operation
  Targets still says "Dual-write with object_metadata
  fallback" — same fix.
- `docs/operator_guide/database.md:614, 635, 650` —
  three table-summary lines. Two need "and
  last_cluster_operation" removed (the column is gone).
  The third describes
  `cluster_operation_targets` shape only — fine, but
  the file needs a new short subsection (see below)
  describing the gating model.
- `docs/plans/index.md:25-29` — five rows currently say
  "Planning". Bump every row in the
  *Replace last_cluster_operation* group to "Complete"
  now that all five phases have landed.
- `docs/plans/PLAN-replace-last-cluster-operation.md`
  *Bugs fixed during this work* section — placeholder
  ("To be filled in as phases land."). Fill in.

**Files that do NOT need updates:**

- `README.md` and `AGENTS.md` — neither references the
  old design. No work needed.
- `docs/plans/order.yml` — entry already present and
  correct.
- `docs/operator_guide/locks.md` — no LCO mentions.
- `shakenfist/schema/cluster_operation_target.py:8` —
  inline comment that says "Replaces the single-pointer
  `last_cluster_operation` column in `object_metadata`".
  This is historical tense and remains accurate; leave
  alone.
- All other `last_cluster_operation` references in the
  codebase — they read the *property*, which still
  exists and is consumed by `external_view()` and
  `runs_after=[...]` chains per master plan decision 1.

## Detailed work

### 1. Rewrite `ARCHITECTURE.md` *Cluster Operation Tracking*

Lines 122-141 currently read:

```
#### Cluster Operation Tracking

`set_last_cluster_operation()` records which cluster operation was most recently
enqueued for an object. API clients poll this to wait for operations to complete.
The write now raises `RuntimeError` on failure so API endpoints return 500
instead of silently losing the tracking data. Callers in deletion paths and
daemons catch this exception to ensure cleanup always proceeds.

The history is stored in the dedicated `cluster_operation_targets` MariaDB
table -- one append-only row per (operation, target object) pair, with an
AUTO_INCREMENT `sequence_number` providing total ordering per target.
`last_cluster_operation` reads the highest-sequence row for the target.
Because the table is append-only it is bounded by a periodic prune in the
cluster daemon, alongside the existing `delete_stale_transfers` cleanup.
The prune removes rows older than `CLUSTER_OPERATION_TARGET_RETENTION`
seconds whose operation has already reached a terminal state. In-flight
operations (`queued`/`preflight`/`executing`) are never pruned regardless
of age. Because the cluster daemon already runs cluster-wide cleanup
under `ClusterLock` election, no additional locking or master-node
gating is required.
```

Replace with text that describes the current model:

- Each operation schema's `model` class declares its
  targets via a `target_fields: ClassVar[dict[str,
  ObjectType]]`. `enqueue_cluster_operation` (in
  `schema/operations/util.py`) reads the declaration
  after the cluster_operations row is written and
  writes one `cluster_operation_targets` row per
  non-None target. Callers do not bookkeep target
  rows.
- The setter `_set_last_cluster_operation` is private
  and used only by internal plumbing — it is not a
  public API; callers should not invoke it.
- Two read shapes are exposed on
  `DatabaseBackedObjectWithOperations`:
  - `last_cluster_operation` (property): returns the
    most recent target row regardless of state.
    Consumed by `external_view()` projections and
    `runs_after=[...]` chains.
  - `has_pending_cluster_operation()` (method):
    returns True if any target row's operation is in
    `{queued, preflight, executing}`. Consumed by
    `Network.is_okay()` and any future history-aware
    gate. The query joins `cluster_operation_targets`
    against `object_states` so a later terminal op
    cannot mask an earlier in-flight op.
- The prune description above is unchanged and stays
  accurate.

The rewrite should keep the heading
`#### Cluster Operation Tracking` and stay in the same
file location (between the *DataBaseBackedObject*
discussion above and the *Cluster Operation Storage and
Work Queues* discussion below).

### 2. Update `CLAUDE.md` entries

In `CLAUDE.md`:

- Lines 426-429 (*Object Metadata* entry): change
  "User-defined metadata key-value pairs and
  last_cluster_operation for all object types." to
  "User-defined metadata key-value pairs for all
  object types." Also drop "Dual-write with etcd
  fallback." if it has gone stale (verify against the
  current state of the etcd→MariaDB migration; if the
  fallback was already removed in earlier work, the
  CLAUDE.md text is stale and should be removed).
- Lines 430-437 (*Cluster Operation Targets* entry):
  change "Replaces the single-pointer
  `last_cluster_operation` in `object_metadata` with a
  full append-only history." to historical tense
  ("Replaced …") and drop "Dual-write with
  object_metadata fallback." entirely (the column is
  gone, there is no fallback). Add one sentence noting
  that target rows are written automatically by
  `enqueue_cluster_operation` and that
  `has_pending_cluster_operation()` exposes the
  history-aware "any in-flight op?" query.

### 3. Update `docs/operator_guide/database.md`

- Line 614: change
  `Complete - object_metadata table (metadata + last_cluster_operation)`
  to `Complete - object_metadata table (user metadata)`.
- Line 635: change
  `User-defined metadata and last_cluster_operation for all objects`
  to `User-defined metadata for all objects`.
- Line 650: keep the existing line. Add a new
  subsection after the *High-Churn Dedicated Tables*
  table titled `### Cluster Operation Target Tracking`
  (or similar) that briefly explains:
  - What the table is for (one row per
    operation+target).
  - The two query shapes
    (`get_latest_cluster_operation_target` for the
    property/external_view, and
    `has_pending_cluster_operation_target` for
    history-aware gating).
  - The prune behaviour
    (`_direct_delete_stale_cluster_operation_targets`
    runs from the cluster daemon, prunes rows whose op
    has reached a terminal state and that are older
    than the retention window).

Keep the subsection short — under 200 words. The
operator audience cares about *what the table does*,
not the implementation details that already live in
ARCHITECTURE.md.

### 4. Bump `docs/plans/index.md` rows

For each of the five rows in the
*Replace last_cluster_operation* group (lines 25-29),
change the *Status* column from `Planning` to
`Complete`.

### 5. Fill in *Bugs fixed during this work*

In `docs/plans/PLAN-replace-last-cluster-operation.md`,
replace the placeholder
`(To be filled in as phases land.)` with a short list
of bugs this plan actually addressed. From the
commit history and CI failure notes, the substantive
ones are:

- **Latest-only race in `Network.is_okay()`** — the
  legacy single-pointer read concluded "no op in
  flight" whenever a later terminal op had been
  written, even if an earlier op was still queued or
  executing. The maintainer raced the queue worker and
  produced the recurring
  `recreating not okay network on hypervisor` audit
  event for first-time creations. Fixed in phase 2 by
  switching to history-aware gating.
- **Forgotten-call race in the hot-plug interface flow
  (commit `8923391c`)** — the API path
  `external_api/instance.py:1028-1029` used to call
  `set_last_cluster_operation` twice, once on the
  instance and once on the network. Earlier audits
  found six other sites where the call had been
  forgotten. Fixed in phase 3 by moving the writes
  inside `enqueue_cluster_operation` so callers cannot
  forget.
- Anything else surfaced during phase implementation
  that is bug-shaped (rather than design or refactor)
  should be added here. The implementing agent should
  re-read the eight phase commits and add bullet
  points for any concrete bugs they fixed beyond
  these two.

### 6. Final audit

Run the following greps to confirm the codebase is
clean:

```
grep -rn 'set_last_cluster_operation\b' shakenfist/ --include='*.py' | grep -v '_set_last_cluster_operation'
grep -rn 'last_cluster_operation_json' shakenfist/ protos/ --include='*.py' --include='*.proto' | grep -v 'shakenfist/protos/database_pb2'
grep -rn "get_lock_attr('last_cluster_operation'" shakenfist/
```

Expected results:
1. **First grep** — only docstring matches (e.g. the
   regression test class docstring in
   `test_cluster_operation_targets.py`). No code call
   sites.
2. **Second grep** — only the `reserved` line in
   `protos/database.proto` and the `ALTER TABLE` text
   inside `_ensure_object_metadata_schema`.
3. **Third grep** — empty.

If any unexpected match appears, escalate to the
management session rather than silently fixing it —
phase 5 is doc updates, not a follow-up code sweep.

### 7. Lint and tests

```bash
pre-commit run --all-files
tox
```

Both must pass clean. Phase 5 is doc-only, so no
behaviour change is expected. mypy and flake8 do not
inspect `.md` files, so the only thing that could break
is if a stale code reference somewhere has bit-rotted —
unlikely, but the gate is cheap.

### 8. Functional CI

The master plan asks for "the merge-queue functional CI
suite to confirm no regression". This runs on push,
not locally. The phase plan does **not** instruct the
implementing agent to push — pushing is the operator's
call. The phase 5 commit message should note that the
operator should push the branch and watch the
merge-queue Guests run for the
`recreating not okay network on hypervisor` audit event.
If the run goes green, the plan's success criterion
"the CI failure mode no longer fires for first-time
creations during op-pickup races" is met.

## Files expected to change

- `ARCHITECTURE.md` — *Cluster Operation Tracking*
  subsection rewrite.
- `CLAUDE.md` — two MariaDB-storage entries updated.
- `docs/operator_guide/database.md` — two table-summary
  lines updated, one short subsection added.
- `docs/plans/index.md` — five status cells flipped to
  Complete.
- `docs/plans/PLAN-replace-last-cluster-operation.md` —
  *Bugs fixed during this work* section filled in.

No code files should change in this phase. If the
implementing agent finds code that needs changing, that
is a phase-5 scope creep — flag it and stop.

## Commit shape

One commit, message along the lines of:

```
Bring docs in line with last_cluster_operation rewrite.

Phase 5 of the LCO replacement plan completes the work.
Updates ARCHITECTURE.md's Cluster Operation Tracking
subsection to describe auto-target writes and the
history-aware gating model, drops the now-incorrect
"and last_cluster_operation" wording from CLAUDE.md and
docs/operator_guide/database.md, adds a short
operator-facing subsection on the cluster_operation_targets
table, fills in the master plan's Bugs fixed section
with the two race bugs the plan actually addressed, and
flips the plan-status index to Complete for all five
phases.

Final audit confirms no stray references: the legacy
public set_last_cluster_operation name appears only in
historical-context docstrings, last_cluster_operation_json
appears only in the proto reserved declaration and the
schema migration text, and the
get_lock_attr('last_cluster_operation', ...) wrappers
are gone.
```

Plus standard `Prompt:`, `Signed-off-by`, and
`Co-Authored-By` lines.

## Acceptance criteria

- `pre-commit run --all-files` passes.
- `tox` passes.
- The three audit greps return only the expected
  matches (no stray code references).
- All five rows in
  *Replace last_cluster_operation* in
  `docs/plans/index.md` show `Complete`.
- Master plan's *Bugs fixed during this work* section
  contains at least the two bugs above.
- The merge-queue functional CI run, when triggered by
  the operator pushing the branch, succeeds without
  the
  `recreating not okay network on hypervisor` audit
  event.

## Out of scope

- Bucket D follow-up (list-based pending-ops query
  for `Instance.enqueue_delete` and
  `baseobject.get_cluster_operations`) — tracked in
  master plan *Future work*. Phase 5 does not implement
  this; if the operator wants to fold a brief mention
  into ARCHITECTURE.md, that is a separate decision.
- Removing the `OBJECT_METADATA_VERSION` plumbing or
  any of the per-table version mechanism.
- Any change to the property
  `DatabaseBackedObjectWithOperations.last_cluster_operation`
  or its `external_view()` consumers.

## Agent guidance

Phase 5 is haiku-friendly, but the ARCHITECTURE.md
rewrite needs a paragraph that flows. Sonnet is a safer
choice. The brief should:
- Provide the existing ARCHITECTURE.md text and ask the
  agent to rewrite, not just patch.
- Provide the exact line numbers / current text for
  CLAUDE.md and `docs/operator_guide/database.md` so
  the agent can do precise edits.
- Tell the agent it is *forbidden* to change any code
  file. If a grep result during the final audit
  surfaces something unexpected, the agent reports it
  and stops rather than fixing.

The management session reviews:
- The doc text reads well in isolation, not just
  matches the bullet points.
- The audit greps actually return clean.
- No code file was modified.

## Documentation index maintenance

When this phase commits:
- `docs/plans/index.md` — five rows flipped to Complete
  (already covered in the work above).
- `docs/plans/order.yml` — entry already present and
  correct, no change needed.

The master plan's *Documentation index maintenance*
section also asks for index updates "when this master
plan lands" — that landed earlier in the plan; the
status flip is the final step.
