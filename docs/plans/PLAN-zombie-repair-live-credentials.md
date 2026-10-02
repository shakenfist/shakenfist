# Zombie repair must not delete a live credential

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

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`AGENTS.md` for project conventions and the index into
`docs/`, including build commands and database access
patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

On 2026-08-22 at 05:16 AEST the `system` namespace on sfcbr stopped
authenticating: the `system/deploy` row had vanished from
`namespace_keys` about ninety seconds after a token validated against
it. The private-CI conductor was locked out for 66 minutes until the
same secret was reseeded with `sf-ctl bootstrap-system-key`. Issue
[#3836](https://github.com/shakenfist/shakenfist/issues/3836) records
the investigation. Its leading hypothesis -- the orphan reconciler
hard deleting a live key that had no state row -- could not be
confirmed from the logs of the day, but the code today makes it
reachable, and makes it reachable in two more object types.

### How the reconciler treats a zombie

`reconcile_orphaned_objects()`
(`shakenfist/daemons/cluster/scheduled_tasks.py:939`) was added for
issue 3534 (`bf23811c7`, 2026-07-28). A *zombie* is a static-values row
with no `object_states` row. Once one has been seen on two consecutive
hourly sweeps, the reconciler writes a `deleted` state row for it
(`:989-993`), and `per_deleted_object_checks()` then hard deletes it
through the normal path once it has been deleted for `CLEANER_DELAY`.
Only `node` and `namespace` are exempt
(`ZOMBIE_REPAIR_EXCLUDED_TYPES`, `:927`).

The premise, stated in the function's docstring, is that zombies "are
otherwise invisible to every state-driven iterator, forever": an object
nothing can see is garbage, so deleting it changes nothing anyone can
observe. That is true of instances, networks and blobs. Issue 3588
added `NAMESPACE_KEY` to `_STATIC_TABLE_GETTERS` (`shakenfist/mariadb.py:24514`)
to stop a storm of junk audit events, and issue 3788 added
`MAPPING_RULE` and `TRUSTED_ISSUER` for parity. For all three, the
premise is false.

### Three object types whose read paths treat "no state" as live

| Type | Read path | What it checks |
|------|-----------|----------------|
| `namespace_key` | `Namespace.lookup_key()` (`shakenfist/namespace.py:217`) via `mariadb.get_namespace_key_by_name()` (`shakenfist/mariadb.py:13736`); `keys_with_attributes()` (`shakenfist/namespace_key.py:386`) for `POST /auth` | Static row joined to attributes, expiry. No state at all. |
| `trusted_issuer` | `TrustedIssuer.from_db_by_name()` (`shakenfist/trusted_issuer.py:105`); the exchange's issuer listing (`shakenfist/federation.py:268`) | Rejects only `state == deleted`; a missing state passes. |
| `mapping_rule` | `MappingRule.from_db_by_name()` (`shakenfist/mapping_rule.py:312`) | Rejects only `state == deleted`; a missing state passes. |

So a stateless key authenticates, and a stateless issuer or rule is
honoured by the federated exchange. To the reconciler each of these is
indistinguishable from garbage, and it destroys them. The key docstring
at `shakenfist/namespace_key.py:402` still asserts that "the only thing
which soft deletes a key is the expiry sweep", which stopped being true
the day #3588 merged.

### How a live object ends up with no state row

All three types create in separate round trips: the static row, then
the attributes row, then two state writes (`INITIAL`, then `CREATED`).
See `NamespaceKey.new()` (`shakenfist/namespace_key.py:204-240`),
`TrustedIssuer._db_create()` (`shakenfist/trusted_issuer.py:79-85`) and
`MappingRule._db_create()` (`shakenfist/mapping_rule.py:283-290`).
Between the attributes write and the first state write the object is
live and stateless. A `DatabaseUnavailable` on the state write -- a
tier blip, a gateway restart -- leaves it that way.

For keys it is worse than a one-off window, because the obvious
recovery makes it permanent. The caller sees the failure and retries.
`NamespaceKey.new()` now finds the key by name (`from_db_by_name()`
does not look at state either), takes the rotation path, and
`rotate()` (`:243`) writes no state. The retry reports success, and
the key is still stateless. `sf-ctl bootstrap-system-key` is exactly
such a caller, so the issue's note that it "does write a proper
`created` state row" holds only when the key did not already exist.

The population of stateless keys left over from the cutover is now
zero (the reconciler drained it), so the risk today is the creation
window, not the backlog. That is the quiet kind: no symptom until up
to about two hours after the blip, and the symptom is an outage of
whichever credential happened to be created during it.

### What has already changed since the issue was filed

The issue's second suggested fix (log what reconciliation touches) is
mostly done. Each repair now writes an audit event and an info log
naming the object (`scheduled_tasks.py:994-1002`), and
`dbo.hard_delete()` writes a `hard deleted object` audit event
(`shakenfist/baseobject.py:740`). The hard-delete step in
`per_deleted_object_checks()` (`scheduled_tasks.py:914-915`) still logs
nothing itself.

The developer guide is out of date in the other direction:
`docs/developer_guide/subsystem_internals.md:351-352` says the
registry omission "is still live for `TRUSTED_ISSUER` and
`MAPPING_RULE`", which #3788 fixed.

## Mission and problem statement

The orphan reconciler must never change what a read path treats as
live. Concretely, when this plan is done:

1. A confirmed zombie whose type's read path would honour it -- a
   namespace key, trusted issuer or mapping rule with its attributes
   row present -- is repaired *forward*, by writing a `created` state
   row, not a `deleted` one. Then the normal lifecycle (the expiry
   sweep for keys, operator deletion for all three) governs it like
   any other object. A zombie its read path would not honour (no
   attributes row, so there is nothing to authenticate against or
   evaluate) is still marked `deleted` and collected, exactly as
   today.
2. The decision fails closed. If the reconciler cannot tell whether a
   zombie is live because the database read failed, it writes nothing
   and tries again on the next pass. Today none of the three attribute
   readers can make that distinction: each returns `None` both for "no
   such row" and for a MariaDB error, and the database servicer
   forwards the direct path's `None` as `found=False`. So the readers
   get the same treatment issue 3522 gave `get_namespace_key_by_name()`.
3. The decision lives in one place per type, on the object class, so
   the reconciler stays type-agnostic. A pinned test names the types
   that override it, so whoever adds the next object type with a
   by-name read path that ignores state is made to decide.
4. Retrying a key creation heals the key rather than freezing it
   stateless: `NamespaceKey.new()`'s rotation path writes the missing
   state when the existing key has none.
5. The docs say what the reconciler actually does, including the
   stale `subsystem_internals.md` sentence and the docstring at
   `namespace_key.py:402`.

Out of scope: making creation atomic (one transaction for the static,
attributes and state rows), and detecting a namespace with no usable
key. See open questions 2 and 3.

This closes [#3836](https://github.com/shakenfist/shakenfist/issues/3836).

## Open questions

Each question carries a recommendation. They stand as written unless
the operator says otherwise when reviewing this plan.

1. **Repair forward, or exempt the three types from zombie repair?**
   Adding them to `ZOMBIE_REPAIR_EXCLUDED_TYPES` would be a one-line
   fix, but it would leave genuine junk -- a static row whose create
   died before its attributes were written -- in place forever. It
   would also bring back the population #3588 was filed to drain, and
   those rows still hold their `(namespace, name)` unique index slot.
   **Recommendation: repair forward when the attributes row exists,
   and delete otherwise.** This is the issue's own first suggestion,
   and the only option where the reconciler's outcome matches what the
   read path already believes.

2. **Make creation atomic as well?** A single transaction for the
   static, attributes and initial state rows would close the window
   at source. But it needs a new combined RPC for each of the three
   types (a proto change and a servicer per type), and it does nothing
   for an object already in that state. With forward repair the
   window becomes harmless: a live object without state is repaired
   to `created` within two passes. **Recommendation: defer to Future
   work.**

3. **Detect a namespace with no usable key?** The issue suggests
   asserting that `system` always has at least one unexpired key.
   That is a health signal in different code (a gauge or a health
   check, plus an alert), and it would be worth having even with this
   fix in, since an operator can also remove the last key by hand.
   **Recommendation: file it as its own issue at the end of this plan
   and do not build it here.**

4. **Should a key's liveness predicate also require it to be
   unexpired?** No. An expired stateless key repaired to `created` is
   picked up by `reap_expired_namespace_keys()`
   (`scheduled_tasks.py:652`), which reads `ACTIVE_STATES`, and is soft
   deleted after `NAMESPACE_KEY_REAP_GRACE`. That is what would have
   happened had it had a state row all along. Keeping the predicate
   structural ("the attributes row exists") keeps it identical across
   the three types and avoids a second clock. **Recommendation: no
   expiry term.**

5. **Log the reaper's hard delete?** `per_deleted_object_checks()`
   hard deletes silently in the log, but `dbo.hard_delete()` already
   writes a `hard deleted object` audit event, and the repair that
   precedes it now logs and audits. A log line per hard delete across
   every object type is a volume change for little gain.
   **Recommendation: no.**

6. **Heal a stateless trusted issuer or mapping rule on create
   retry, as step 3 does for keys?** Their create paths refuse an
   existing name (`trusted_issuer.py:147`), so a retry gets a conflict
   rather than a silent success. That makes the problem visible
   instead of quiet, and forward repair resolves it within two
   passes. **Recommendation: keys only.**

## Execution

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

!!! note "In this project"

    The same term is written twice: once in the phase table
    below, and once in the row this plan carries in
    `docs/plans/index.md`. Keep them in step -- the index row is
    the whole-plan status, so it only reaches `Complete` once
    every phase has been completed, abandoned or superseded.

This is a single pull request. It has no separate phase files: the
steps below are the whole of phase 1, and phase 2 is the mandatory
push audit.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Repair live zombies forward | This file, "Phase 1" below | Not started | — |
| 2. Push audit | This file, "Phase 2" below | Not started | — |

### Phase 1: repair live zombies forward

Recommended planning effort: high (done). It turns on a fail-closed
correctness question about which database reads can tell "absent"
from "unreadable".

Code comments and docstrings cite issue 3836, not this plan file. The
`check-plan-phase-references` pre-commit hook and the open #4397 audit
both police plan references in source.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Make the three attribute readers fail closed. See brief 1a below. |
| 1b | high | opus | none | Add the per-class zombie repair decision and use it in the reconciler. See brief 1b below. |
| 1c | medium | sonnet | none | Heal a stateless key on `NamespaceKey.new()`'s rotation path. See brief 1c below. |
| 1d | medium | sonnet | none | Update docs and stale docstrings. See brief 1d below. |

Steps run in order: 1b relies on 1a's readers raising, and 1d
describes what 1b and 1c built. Each step is its own commit.

#### Brief 1a: fail-closed attribute reads

`mariadb.get_namespace_key_attributes()`,
`get_trusted_issuer_attributes()` and `get_mapping_rule_attributes()`
currently return `None` both when no row exists and when the read
failed:

- the direct readers `_direct_get_namespace_key_attributes`
  (`shakenfist/mariadb.py:13381`), `_direct_get_trusted_issuer_attributes`
  (`:14100`) and `_direct_get_mapping_rule_attributes` (`:14767`)
  catch `OperationalError` and return `None`;
- the database servicers (`GetNamespaceKeyAttributes`,
  `GetTrustedIssuerAttributes`, `GetMappingRuleAttributes` in
  `shakenfist/daemons/database/main.py` around `:3985`, `:4158` and
  `:4343`) call the direct reader and turn its `None` into
  `found=False`, so a MariaDB error reaches gRPC clients as "not
  found";
- `_grpc_get_namespace_key_attributes` (`:13654`) also returns `None`
  on `grpc.RpcError`. The issuer and rule gRPC readers (`:14328`,
  `:15030`) already raise `DatabaseUnavailable`.

Mirror what issue 3522 did for `get_namespace_key_by_name`. The
direct path is at `shakenfist/mariadb.py:13178-13187` and the gRPC
path at `:13571-13580`. Each direct reader raises
`exceptions.DatabaseUnavailable` on `OperationalError`, with a comment
saying why. `_grpc_get_namespace_key_attributes` raises it on
`grpc.RpcError`. The servicers need no change: their catch-all
already sets `INTERNAL`, which the client wrappers turn into
`DatabaseUnavailable`. Update each public wrapper's docstring to say
it raises. Keep `CorruptMappingRule` behaviour as it is.

Then audit the callers. Within the tree, the only callers are the
`_attributes()` methods at `shakenfist/namespace_key.py:295`,
`shakenfist/trusted_issuer.py:194` and `shakenfist/mapping_rule.py:439`.
Grep for every use of those, and for any `try`/`except` or `if not
attrs` around them that assumed `None` could mean a failure. A caller
that turned `None` into a confident answer should now let
`DatabaseUnavailable` propagate (the REST layer maps it to 503). A
caller for which `None` legitimately means "concurrently hard
deleted" keeps its `None` handling, because that case still returns
`None`. Report each caller and what you decided for it.

Tests: add cases to `NamespaceKeyFetchFailureTestCase` in
`shakenfist/tests/test_database_unavailable.py:341`, or a sibling
class in the same file, following its existing four tests. Cover each
direct reader raising on `OperationalError`, and the key gRPC reader
raising on `RpcError`.

#### Brief 1b: the zombie repair decision

Add a classmethod to `dbo` in `shakenfist/baseobject.py`:

```python
@classmethod
def zombie_repair_state(cls, object_uuid: str) -> str:
    """..."""
    return cls.STATE_DELETED
```

Its docstring states the contract: the orphan reconciler calls it for
a static row that has had no `object_states` row on two consecutive
passes, and writes the returned state. The default, `deleted`, is
right for any type whose read paths cannot see a stateless object. A
type with a read path that honours a stateless object must return a
live state when that object would be honoured, so that reconciliation
never changes what a reader observes (issue 3836). It may raise
`DatabaseUnavailable`; the reconciler then writes nothing for that
object on this pass.

Override it in `NamespaceKey` (`shakenfist/namespace_key.py`),
`TrustedIssuer` (`shakenfist/trusted_issuer.py`) and `MappingRule`
(`shakenfist/mapping_rule.py`). Each returns `cls.STATE_CREATED` if
`mariadb.get_<type>_attributes(_as_uuid(object_uuid))` returns a row,
and `cls.STATE_DELETED` if it returns `None`. Step 1a made those reads
raise on failure, so `None` now really means absent. Each override's
docstring names the read path that ignores state: `lookup_key` and
`keys_with_attributes` for keys, `from_db_by_name` and the federation
issuer listing for the other two.

In `reconcile_orphaned_objects()`
(`shakenfist/daemons/cluster/scheduled_tasks.py:939`), resolve the
class once per object type with `constants.get_object_class(objtype)`.
That is already a dependency of the reconciler's delete path; see
`docs/developer_guide/subsystem_internals.md:347-350`. For each
confirmed zombie, call `cls.zombie_repair_state(obj_uuid)` inside the
existing per-object `try` (`:988-1005`), so a raised
`DatabaseUnavailable` is caught by the existing `ignore_exception`.
The zombie is not removed from `_ZOMBIE_CANDIDATES`, so it is retried
next pass. Pass the returned value to `mariadb.set_state()` instead of
the hard-coded `dbo.STATE_DELETED`. Make the state message, the audit
event text and the log line say which way the object was repaired.
Today they say "marked this object deleted" and "repaired zombie
object". Add a `repaired_state` field to the log line. A live repair
should read as one, for example "the orphan reconciliation sweep
found this object live with no state row and marked it created".

Tests, in `ReconcileOrphanedObjectsTestCase`
(`shakenfist/tests/test_daemon_cluster_scheduled_tasks.py:1083`),
following `test_zombies_repaired_after_two_observations` (`:1123`):

- a confirmed `namespace_key` zombie with attributes is written
  `created`, and one without attributes is written `deleted`;
- the same two cases for `trusted_issuer` and `mapping_rule`;
- a confirmed zombie whose attributes read raises
  `DatabaseUnavailable` gets no `set_state` call and no audit event,
  and is repaired on a later pass once the read succeeds;
- an existing type (`network`) is still written `deleted`, which the
  existing test may already cover.

Add unit tests for each override in `test_namespace_key_object.py`,
`test_trusted_issuer.py` and `test_mapping_rule.py`. Add one pinned
structural test, beside the `_STATIC_TABLE_GETTERS` parity test in
`shakenfist/tests/test_mariadb_orphans.py:241-250`. It walks
`mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES` minus
`ZOMBIE_REPAIR_EXCLUDED_TYPES`, resolves each class, and asserts that
the set of types whose class overrides `zombie_repair_state` is
exactly `{namespace_key, trusted_issuer, mapping_rule}`. The failure
message tells the next author to decide whether their type's read
paths honour a stateless object.

**Mutation check (required, run it and report it):** with the tests
in place, temporarily delete the `NamespaceKey` override and confirm
that the key reconciler test, the key override test and the pinned
structural test all fail. Then temporarily make
`_direct_get_namespace_key_attributes` return `None` on
`OperationalError` again, and confirm the fail-closed reconciler test
fails. Restore both. A test that still passes under either mutation
is not testing the fix.

#### Brief 1c: heal a stateless key on rotation

In `NamespaceKey.new()` (`shakenfist/namespace_key.py:196-202` and
the race fallback at `:228-236`), after `existing.rotate(...)`, check
whether the key has a state row: `existing.state.value is None`, as
the expiry-sweep tests at
`shakenfist/tests/test_daemon_cluster_scheduled_tasks.py:664-710`
model a zombie. If it has none, write `STATE_INITIAL` and then
`STATE_CREATED` through the object's `state` setter, as the create
path does at `:239-240`, and add an audit event saying the rotation
repaired a key with no state row (issue 3836). Do not touch the case
where state exists.

Test in `shakenfist/tests/test_namespace_key_object.py` (or
`test_namespace_keys.py`, whichever already covers `new()`'s rotation
path):

- rotating a stateless existing key writes both states;
- rotating a `created` key writes no state.

Run the mutation check: remove the heal and confirm the first test
fails.

#### Brief 1d: documentation

- `docs/operator_guide/database.md:1027-1035`: the zombie bullet says
  zombies are repaired by writing `deleted`. Say that a namespace key,
  trusted issuer or mapping rule whose attributes are present is
  repaired to `created`, because its authentication read path honours
  it without a state row, and that a repair whose liveness read fails
  is retried on the next pass.
- `docs/operator_guide/authentication.md:20-23`: say that the
  reconciliation repairs a live zombie key to `created`, after which
  the expiry sweep governs it normally. Add one sentence saying that
  re-running `sf-ctl bootstrap-system-key` for an existing key heals a
  missing state row.
- `docs/developer_guide/database_internals.md:357-360`: same
  correction as the operator guide, in that page's terms.
- `docs/developer_guide/subsystem_internals.md:347-352`: delete the
  stale "the same defect is still live for `TRUSTED_ISSUER` and
  `MAPPING_RULE`" (#3788 fixed it). Add that a new object type must
  also decide `zombie_repair_state`, and that the pinned test in
  `test_mariadb_orphans.py` enforces this.
- `shakenfist/namespace_key.py:402-405` (`keys_with_attributes`
  docstring) and `Namespace.lookup_key()` (`shakenfist/namespace.py:217`):
  the claim that only the expiry sweep soft deletes a key is true
  again after this change, but only because `zombie_repair_state`
  makes it so. Say that, citing issue 3836.
- The `reconcile_orphaned_objects()` docstring (`scheduled_tasks.py:940-951`):
  replace "repaired by writing a deleted state row" with the two-way
  rule.

#### Definition of done for phase 1

Run each of these and record the result in the pull request
description. Do not assert that it passes.

- `pre-commit run --all-files` passes.
- `stestr run shakenfist.tests.test_daemon_cluster_scheduled_tasks.ReconcileOrphanedObjectsTestCase`
  passes, with the new live, non-live and fail-closed cases visible
  in the output.
- `stestr run shakenfist.tests.test_mariadb_orphans` passes, including
  the pinned override test.
- `stestr run shakenfist.tests.test_database_unavailable` passes.
- The mutation checks in briefs 1b and 1c were run, and each mutation
  made the named tests fail.
- `grep -n "STATE_DELETED" shakenfist/daemons/cluster/scheduled_tasks.py`
  no longer shows a hard-coded deleted state inside the zombie repair
  loop.
- `grep -rn "still live for" docs/developer_guide/subsystem_internals.md`
  returns nothing.

### Phase 2: push audit

Run `PUSH-AUDIT.md` over phase 1's merge commit (recorded in the
`Merged` column when phase 1 lands). Because this plan has one code
phase, the audit range is that one pull request. Findings land as
their own pull request; an empty audit is recorded here in one
sentence, and this phase then closes itself out per the
`plan-phase-landing` block below.

Before the audit, file the follow-up issue from open question 3
(detect a namespace with no usable key) and link it under Future
work.

<!-- shared-block: plan-push-audit-phase v3 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did.
<!-- shared-block-end -->

!!! note "In this project"

    The runbook is `PUSH-AUDIT.md` at the repository root, and
    it reads its `$RANGE` from the `Merged` column, so a plan
    without one cannot be audited once its phases have merged.

    The column goes in the master plan's Execution table, last:

        | Phase | Plan | Status | Merged |

    The worked example in the `plan-file-conventions` block
    above still shows the three-column form, because it
    predates this column and its canonical copy lives in
    shakenfist/development; add `Merged` after `Status` rather
    than copying that example as-is.

    `tools/check-plan-status.py` reads the `Status` column by
    name rather than by position, so a `Merged` column added
    after it does not disturb the index arithmetic. A phase
    which has not landed reads `—` -- in a table cell or a
    `**Merged**:` line alike -- rather than an empty cell,
    `TBD` or `n/a`.

    A few of this repository's older roadmaps write their
    phases as prose sections rather than as a table:
    `blob-storage-roadmap.md`, `api-query-batching-roadmap.md`
    and `PLAN-attribute-field-masks.md`, which are the three
    named in `check-plan-status.py`'s `HAND_COUNTED`. The
    first two carry a `**Merged**:` line in the phase's own
    section -- beside `**Status**` where the phase has one,
    otherwise directly under the heading. The third carries no
    such line, which is a decision rather than an oversight:
    it is `Complete` and has no push-audit phase, so the
    shared block's do-not-reopen rule leaves it alone. Nothing
    checks either form, so a wrong SHA is caught only in
    review.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

!!! note "In this project"

    This repository does not track human review, so it has no
    `REVIEWS.md` and no `prune-reviews` workflow; the third rule
    has nothing to act on here. The first two apply as written.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases involving schema design, cross-daemon coordination,
    protocol changes, or subtle correctness questions (locking,
    consistency, migration safety) should be planned at high
    effort. Phases that mirror an already-established pattern --
    adding a new MariaDB accessor alongside an existing one, for
    example -- can be planned at medium effort.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    High effort typically means a new object type lifecycle, a
    cross-daemon protocol change, or migration logic; medium
    effort typically means a new gRPC endpoint parallel to an
    existing one, or a new MariaDB accessor; low effort typically
    means a rename, a log line, or regenerating proto stubs.

    A worked brief for this codebase: instead of "add MariaDB
    functions for the new object", write "add direct, gRPC, and
    public wrappers for `get_widget` in `shakenfist/mariadb.py`,
    mirroring the `get_artifact` trio at lines
    11129/11797/11811. The direct path uses
    `_get_widgets_table()`; the gRPC wrapper goes through
    `GetWidget` on the database daemon; register the counter in
    the Monitor operations list in
    `shakenfist/daemons/database/main.py`."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code passes `pre-commit run --all-files` (flake8,
          stestr unit tests, mypy).
    - [ ] If proto files changed, stubs were regenerated with
          `tox -e genprotos` and committed.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* New code follows existing patterns: object lifecycle in
  `baseobject.py`, MariaDB access via the three-layer pattern
  (direct/gRPC/public), Pydantic schemas in
  `shakenfist/schema/`.
* Object or attribute filtering is pushed down to the
  MariaDB SQL layer where indexes can make it faster, rather
  than materialising everything and filtering in Python.
* There are unit tests for core logic and preferably
  functional test coverage as well (see
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests`).
* Lines are wrapped at 120 characters, single quotes for
  strings, double quotes for docstrings.
* gRPC proto changes (if any) have been regenerated with
  `tox -e genprotos`.
* Documentation in `docs/` has been updated to describe any
  new features, commands, or database changes. In particular
  `docs/operator_guide/database.md` has been updated for
  schema changes.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` have been
  updated if the change adds or modifies modules, daemons,
  or object types.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add one row to the *Master plans*
  table: the date the plan was written, a link to it, a
  one-line intent, its status from the vocabulary above,
  and its phase arithmetic (`0 of 7`, or `—` for a plan
  with no phases). One row per master plan, never one per
  phase — the phases are tracked in this plan's own
  Execution table, and duplicating them in the index is
  how the two drift apart. Rows run oldest first.
* **`order.yml`** — add an entry for the new master plan,
  in the intended reading order. This registers the plan
  and is what the plan-status check reads; it does **not**
  by itself put the plan in the published site navigation,
  which `mkdocs.yml.tmpl` currently lists by hand. The file
  is not ours: its format and its meaning as a per-directory
  navigation allowlist come from `tools/sync_component_docs.py`
  in shakenfist/actions, which is how a repository's docs are
  synced into a documentation site. Keeping it complete is
  what would make generating the nav from it a mechanical
  change later. Phase files should *not* be added to
  `order.yml`; they are linked from the master plan's
  Execution table only, which makes that table the only path
  to them.

The index row carries the whole-plan status, so it only
reaches `Complete` once every phase has been completed,
abandoned or superseded. Update it as the arithmetic
changes, not only at the end.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

- **Atomic creation** (open question 2). Write the static, attributes
  and initial state rows of a namespace key, trusted issuer and
  mapping rule in one transaction, through one RPC per type. This
  removes the window instead of making it harmless.
- **A namespace with no usable key** (open question 3). This is a
  health signal for `system` above all. It is filed as its own issue
  in phase 2.
- **The legacy `namespace_attributes.keys` column.** #3836 also points
  out that this column still holds live key material nobody reads, and
  that the auth-federation plan's open question 7 resolved every part
  of "when is it retired" except the retirement itself. It is
  unrelated to the reconciler and is not taken up here. If no issue
  tracks it, phase 2 files one.
- **Fail-closed reads more broadly.** Step 1a fixes three readers
  because this plan depends on them. #3373 tracks the same
  absent-versus-unreadable conflation across the rest of `mariadb.py`.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

- [#3836](https://github.com/shakenfist/shakenfist/issues/3836): a
  live `system/deploy` namespace key was hard deleted, locking the
  system namespace out of auth. Closed by this plan's phase 1 pull
  request.
- Found while planning, and fixed in step 1c: retrying a key creation
  whose state write failed reports success and leaves the key
  stateless, because the rotation path writes no state.
- Found while planning, and fixed in step 1a: the namespace key,
  trusted issuer and mapping rule attribute reads report a MariaDB
  error as "no such row", including through the database servicer.

Related, and not resolved here: #3373 (the general form of step 1a's
defect), #3588 and #3788 (the registry additions that exposed these
types to zombie repair).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
