# Replace `last_cluster_operation` with a `cluster_operation_targets`-driven check

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named
`PLAN-replace-last-cluster-operation-phase-NN-descriptive.md`,
and tracked in the Execution table below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The `last_cluster_operation` field on a database-backed
object historically pointed at the most recent cluster
operation targeting that object. It was an etcd-era design:
a single JSON pointer in `object_metadata` updated by every
caller that enqueued an op against the object. The network
maintainer's `Network.is_okay()` check (in
`shakenfist/network/network.py`) reads this pointer to decide
whether to defer its own recreate path while an op is in
flight.

The `cluster_operation_targets` MariaDB table has since been
introduced (see `CLAUDE.md` *Cluster Operation Targets* and
`shakenfist/schema/cluster_operation_target.py`). It records
**every** cluster operation against **every** target object,
with `AUTO_INCREMENT` sequence numbering for ordering.
`baseobject.DatabaseBackedObjectWithOperations.last_cluster_operation`
is now a property that calls
`mariadb.get_latest_cluster_operation_target(...)` and
synthesises the old shape; `set_last_cluster_operation` is a
thin write to that table. So the storage migration is
already done — the API surface and the gating logic still
behave as if it were a single pointer.

The single-pointer behaviour now produces two distinct bugs:

1. **Latest-only race**: `is_okay()` reads only the latest
   target row. If a later op against the same object has
   already reached a terminal state while an earlier op is
   still queued/executing, the maintainer sees "latest is
   terminal", concludes nothing is in flight, and runs its
   own recreate path. The earlier op is the one actually
   modifying the network on this hypervisor.
2. **Forgotten-call race**: every caller that enqueues an
   op against an object must remember to call
   `set_last_cluster_operation` on that object. We just
   shipped a CI failure caused by exactly this in the hot-plug
   interface flow (commit `8923391c`), and an audit found six
   more sites at risk: `shakenfist/network/network.py:731,
   782, 798, 816, 839, 863`,
   `shakenfist/external_api/instance.py:1554`, and
   `shakenfist/daemons/cluster/scheduled_tasks.py:210`. Three
   are race-prone (`network.py` 782/798/816 in particular)
   because they're reachable from API paths that the
   maintainer can race.

The CI workflow has been intermittently broken in subtly
related ways for ~20 months. We want one clean landing of the
right design rather than another iteration of "patch the
caller you noticed".

## Mission and problem statement

Replace the single-pointer `last_cluster_operation` semantics
with a `cluster_operation_targets`-driven check that:

* Returns "in flight" if **any** non-terminal cluster
  operation targets the object, regardless of whether a
  later op against the same object has since completed.
* Removes the per-caller obligation to remember
  `set_last_cluster_operation` — targeting is recorded
  automatically inside the `*_create_and_enqueue` helpers
  for every target object referenced by the op's schema.
* Preserves the `last_cluster_operation` field in
  `external_view()` output as a synthetic projection of the
  same query, so external API consumers (CLI, tests, anyone
  scraping the JSON) keep working unchanged.

The current `last_cluster_operation` *property* on the base
object already does the synthesis. The work is in switching
gating callers to a new "any-in-flight" query and automating
the writes.

## Open questions

These need decisions before phase planning starts. The
"recommended answer" reflects what I'd default to; the
operator should confirm or override.

1. **Synthetic `external_view` semantics.** Should
   `last_cluster_operation` in `external_view()` continue to
   be the *latest* target row regardless of state, or the
   *latest non-terminal* one (matching the new gating
   query)?
   *Recommended: latest of any state.* Matches existing
   external behaviour; consumers that rely on it (e.g.
   `runs_after=[instance_from_db.last_cluster_operation]`
   in `external_api/instance.py:1013`) keep their meaning.
   The new gating query is a separate, internal-only API.

2. **Auto-targeting target discovery.** Each
   `*_create_and_enqueue` helper has known target UUID
   fields in its Pydantic model (`network_uuid`,
   `instance_uuid`, `interface_uuid`, `agentoperation_uuid`,
   `artifact_uuid`, `node_uuid`, `blob_uuid`). Should the
   automation enumerate fields by convention (`*_uuid` →
   matching object type), or should each schema declare its
   targets explicitly?
   *Recommended: explicit declaration in the schema model.*
   `interface_uuid` belongs to a `NetworkInterface` not an
   `Interface`; `node_uuid` does not have LCO tracking;
   convention is brittle. A `target_object_types: ClassVar`
   on each model is unambiguous.

3. **Should `set_last_cluster_operation` remain a public
   API?** Once auto-targeting lands, every existing caller
   becomes redundant.
   *Recommended: keep as a private helper on the base
   object, used only by `*_create_and_enqueue`. Audit and
   remove the explicit external callers in phase 4.*

4. **Per-target locking.** The existing pattern wraps each
   API enqueue in `obj.get_lock_attr('last_cluster_operation',
   'add new operation')`. This was meaningful when the
   single-pointer write was racy. With auto-targeting, every
   write is a fresh `INSERT` into `cluster_operation_targets`
   with an `AUTO_INCREMENT` `sequence_number` — concurrent
   writers don't conflict.
   *Recommended: drop the attr lock once auto-targeting is
   the only writer.* Confirm by reading
   `_direct_create_cluster_operation_target` for any hidden
   read-modify-write that requires serialisation.

5. **Node objects.** `node_net_op` and other `node_*` ops
   target a node UUID. Does the `Node` class inherit
   `DatabaseBackedObjectWithOperations`?
   *Recommended: confirm in phase 1; if not, exclude
   node-targeted ops from auto-targeting.*

6. **Migration of stale `object_metadata` rows.** The
   `last_cluster_operation` JSON field on `object_metadata`
   is no longer read or written (the property and setter
   route through `cluster_operation_targets`). Do we drop
   the column, leave it dead, or keep-and-clear?
   *Recommended: leave dead for one release, schedule
   removal in a follow-up.* Matches the precedent set by
   the per-daemon-state migration described in `CLAUDE.md`.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Add `has_pending_cluster_operation` query and tests | PLAN-replace-last-cluster-operation-phase-01-query.md | Not started |
| 2. Switch `Network.is_okay()` and other gating callers | PLAN-replace-last-cluster-operation-phase-02-gating.md | Not started |
| 3. Auto-target tracking in `*_create_and_enqueue` helpers | PLAN-replace-last-cluster-operation-phase-03-auto-target.md | Not started |
| 4. Remove explicit `set_last_cluster_operation` callers | PLAN-replace-last-cluster-operation-phase-04-cleanup.md | Not started |
| 5. Documentation and final audit | PLAN-replace-last-cluster-operation-phase-05-docs.md | Not started |

### Phase outlines

**Phase 1.** Add a new `has_pending_cluster_operation()`
method on `DatabaseBackedObjectWithOperations` that returns
True if any row in `cluster_operation_targets` for this
`(object_type, uuid)` references an operation whose state is
not in `{COMPLETE, ABORT, ERROR, DELETED}`. Implement the
query as `_direct_*` / `_grpc_*` / public trio in
`mariadb.py`, mirroring the existing
`get_latest_cluster_operation_target` shape. Add unit tests
covering: no targets, single in-flight target, single
terminal target, multiple targets with mixed states, and
multiple terminal targets followed by an in-flight one (the
race we are fixing). Plan effort: high; phase plan effort:
medium.

**Phase 2.** Switch `Network.is_okay()`, the network
maintainer in `shakenfist/daemons/network/maintain.py`, and
any sibling gating logic to use
`has_pending_cluster_operation()`. The current `is_okay()`
opens with a `last_cluster_operation` lookup and only falls
through to bridge / dnsmasq checks when the latest op is
terminal — that whole prelude is replaced with the new
query. Re-run the audit (phase plan should include rerunning
the Explore agent on `is_okay`-equivalents across object
types) so we don't miss e.g. the cleaner daemon. Plan
effort: medium; phase plan effort: medium.

**Phase 3.** Move target-tracking into the
`*_create_and_enqueue` helpers in
`shakenfist/schema/operations/*.py`. Each schema's `model`
class declares its target object types via a class variable
(see open question 2), and `enqueue_cluster_operation` (or
the helper directly) writes a `cluster_operation_targets`
row per target before returning. This is the
behaviour-preserving change: every existing
`set_last_cluster_operation` call becomes redundant after
this lands. Plan effort: high; phase plan effort: high.

**Phase 4.** Audit and remove the now-redundant explicit
`set_last_cluster_operation` calls. The audit list is in the
*Situation* section above. For each call site, verify the
auto-targeting in phase 3 produces the same row, then delete
the explicit call and its surrounding `get_lock_attr`
wrapping. Phase plan should include verification by
inspection of the produced `cluster_operation_targets` rows
in a CI run. Plan effort: medium; phase plan effort: medium.

**Phase 5.** Update `docs/operator_guide/database.md` to
describe the new gating model and the deprecation of the
single-pointer field. Update `CLAUDE.md` *Cluster Operation
Targets* entry. Update `ARCHITECTURE.md` if the locking
section references `last_cluster_operation` semantics. Run
`pre-commit run --all-files` and the merge-queue
functional CI suite to confirm no regression. Plan effort:
low; phase plan effort: low.

## Agent guidance

(See PLAN-TEMPLATE.md *Agent guidance* section for the full
execution-model boilerplate. This plan inherits that
guidance verbatim — sub-agents implement, the management
session reviews, opus is the default for cross-daemon
correctness work, sonnet for well-briefed mechanical
sweeps.)

The phase that most warrants opus is phase 3 (auto-target
tracking). It touches every operation schema, the queue
helpers, and changes a contract that ~20 callers rely on.
Phase 1 and 2 are well-scoped and could be sonnet with a
detailed brief. Phase 4 is a sweep — sonnet is fine, with
the management session verifying the diff against the audit
list. Phase 5 is mechanical (haiku acceptable).

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] If proto files changed, stubs were regenerated with
      `tox -e genprotos` and committed.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* `Network.is_okay()` (and any sibling gating method)
  returns False only when no non-terminal cluster operation
  targets the network — fully history-aware.
* No call site in `shakenfist/` (outside the
  `*_create_and_enqueue` helpers and tests) calls
  `set_last_cluster_operation` directly. Every cluster-op
  target write is automated by the enqueue helpers.
* `external_view()` for every object type that previously
  exposed `last_cluster_operation` continues to expose the
  same shape, sourced from
  `mariadb.get_latest_cluster_operation_target`.
* The CI failure mode "recreating not okay network on
  hypervisor" no longer fires for first-time creations
  during op-pickup races.
* `pre-commit run --all-files` passes (flake8, stestr unit
  tests, mypy).
* Docs in `docs/` are updated to describe the new gating
  model and deprecation of the single-pointer.
* The full functional CI suite goes green for at least one
  merge-queue run.

### Future work

* **Drop the dead `last_cluster_operation` JSON column on
  `object_metadata`** in a follow-up release once we are
  confident no rollback path needs it. Track in the same
  cadence as the per-daemon-state migration.
* **Consider exposing a full history endpoint** — once the
  data is reliably populated, an external API to list all
  cluster ops against a given object would aid debugging.
  Out of scope here.
* **Pushdown `has_pending_cluster_operation` filtering** —
  iterators that walk objects and call `is_okay` would
  benefit from a SQL-side join filter. Defer to the
  pushdown roadmap.

### Bugs fixed during this work

(To be filled in as phases land.)

### Documentation index maintenance

When this master plan lands, update:

* **`docs/plans/index.md`** — add a row to the *Plan
  Status* table.
* **`docs/plans/order.yml`** — add an entry for this master
  plan (phase files are not added to `order.yml`).

When all phases of the plan are complete, update the status
column in `docs/plans/index.md` to `Complete`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
