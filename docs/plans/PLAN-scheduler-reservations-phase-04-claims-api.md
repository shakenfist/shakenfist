# Scheduler reservations phase 4: namespace claims object and API

## Prompt

This is a phase plan under
[PLAN-scheduler-reservations.md](PLAN-scheduler-reservations.md).
The master plan's Prompt section applies unchanged; the
decisions it refers to as D-numbers live in
[PLAN-scheduler-reservations-phase-00-decisions.md](PLAN-scheduler-reservations-phase-00-decisions.md).

Planning effort: **high**. The phase designs a new
first-class object, a new guarded-UPDATE transaction against
tables three other writers already touch, and a change to the
admission hot path that phase 3 only just stabilised against
`innodb_snapshot_isolation`. Review effort: **high**.

## Situation

Phase 2 created `namespace_claims` empty and gave the
reconciler the machinery to maintain it. Phase 3 taught the
admission transaction to draw a claim down when one exists,
and left the branch unreachable in production because
nothing can create a claim row.

This phase makes the row creatable: the claim becomes a
first-class Shaken Fist object with REST CRUD, its own
lifecycle and its own events, and the admission path starts
reporting when a namespace exceeds what it claimed.

## Mission and problem statement

Ship the claim as an operator-facing resource, in advisory
mode: creating a claim guarantees a namespace's aggregate
capacity against the cluster (D14), and exceeding it is
recorded rather than refused (D16). Phase 5 flips the
refusal on.

The phase is done when the conductor could, in principle,
size a runner namespace with one POST and see the drawdown
in the response to a GET -- and when nothing else in the
cluster's accounting has moved as a result.

## What the survey found (2026-08-16)

The master plan's phase 4 section survived verification: its
scope statement, its D-number references and its stated
prerequisite (the P7 fail-open dropping the claim guard) are
all accurate against the tree at `0144e36ed`. Nothing in it
had to be corrected.

What it *omits* is the substance of this plan. Five findings,
three of them written into the code by phase 3 as explicit
phase 4 obligations:

1. **The per-claim usage recompute double-counts a
   duplicated placement.** `mariadb.py:23798-23807` records
   that a lost node's `INSTANCE_LOCATION` row can survive
   `place_instance()`'s best-effort removal, so one instance
   appears on two nodes; the cluster fold is protected by
   its restriction to nodes holding a capacity row, but the
   per-claim recompute is deliberately namespace-wide and so
   counts the instance twice. Inert at zero claims. The
   comment names the fix as this phase's: de-duplicate by
   instance uuid in the aggregation.

2. **The per-claim recompute is a loop of one UPDATE per
   active claim inside the reconcile transaction**
   (`mariadb.py:24252-24261`, the `for claim_row in
   active_claims` loop). The comment says so and names the
   fix as this phase's: fold it into a single set-based
   `UPDATE ... JOIN` over the namespace usage aggregation.

3. **P7's fail-open is one flag for three guards.**
   `mariadb.py:25228` computes `guarded = enforce and
   node_present` and applies it to the claim branch, the
   cluster branch and the node branch alike. The fail-open
   reasoning -- a node whose limits are not in the cluster
   totals must not be denied against those totals -- is
   sound for the node and cluster branches and false for the
   claim branch, whose limits are namespace-denominated and
   node-independent.

4. **A functional test of claims cannot use the client
   library.** The CI suite reaches the API only through
   `shakenfist_client.apiclient`
   (`shakenfist/deploy/shakenfist_ci/base.py:17`), and the
   collection installs `shakenfist-client` from PyPI by
   default (`collection/roles/node/defaults/main.yml:74`).
   New client verbs would therefore not exist in CI until a
   client release, which no server PR can produce.

5. **Nothing migrates existing usage when a claim is
   created.** A namespace's instances are counted in
   `cluster_capacity.unclaimed_used_*` until the reconciler
   next runs (`mariadb.py:24297-24304` skips claimed
   namespaces from the unclaimed fold). Creating a claim for
   a namespace that already has instances therefore leaves
   that usage counted on the unclaimed side and *not* on the
   claim's `used_*`, for up to one reconcile period -- during
   which the namespace can draw its whole claim down a
   second time. The master plan does not mention this and
   phase 3 had no reason to.

Two smaller notes:

* Adding an `ObjectType` member needs **no hand-written
  migration**: `ensure_schema()` gained an enum-widening
  reconciliation pass in commit `40c91013f`, after the
  `NAMESPACE_KEY` member broke upgraded clusters on
  2026-07-28. Any plan text or habit that assumes otherwise
  is out of date.
* `_direct_reconcile_scheduler_capacity()`'s docstring still
  says "Nothing else writes these tables until phase 3, so
  the per-statement writes need no enclosing transaction"
  (`mariadb.py:23998`). Phase 3 shipped; the sentence is now
  false as written. Step 1 touches that function and fixes it
  in passing.

`shakenfist/tests/mock_mariadb.py:2742` models the node stage
only -- no cluster singleton, no claim row -- exactly as the
phase 3 plan's Future work said, so no caller-side unit test
can currently produce a `claim`-stage reply.

**Corrections made at source in this planning commit:** the
phase 3 rows in the master plan's Execution table and in
`docs/plans/index.md` said "Implemented -- awaiting operator
review and sfcbr soak"; PR #3754 merged as `0144e36ed` on
2026-08-16 and they now say Complete, with the outstanding
sfcbr soak named rather than implied. The phase 3 plan's own
Execution table still showed steps 1 and 2 as "Not started"
although both landed (verified: `git grep
_dual_write_legacy_instances` and `git grep
SCHEDULER_DISK_OVERCOMMIT` in `config.py`); those are
corrected too, and step 9 now records that the PR merged and
the soak has not started. `index.md`'s phase 4 description
said "client verbs", which decision 7 below moves out of
scope; it now says so.

## Scope

**In scope.**

* The `NamespaceClaim` object: a `DatabaseBackedObject`
  subclass with uuid, namespace, the three limits, expiry,
  events and `hard_delete()`; its `ObjectType` member; its
  pydantic schema model; its iterator.
* Three-layer MariaDB CRUD for claims (direct, gRPC, public)
  plus proto messages, servicer methods and Monitor
  registration.
* The guarded claim-admission transaction: creating or
  growing a claim is itself an admission decision against
  `cluster_capacity` (D14's mirror guard).
* Migration of a namespace's existing drawdown into the
  claim at creation, and back out at deletion.
* REST endpoints, admin-only, with parameter declarations.
* Namespace `hard_delete()` cascading to its claims.
* The P7 guard-flag split, and advisory (record-don't-refuse)
  claim accounting with the D16/D9 structured event.
* The two reconciler fixes phase 3 deferred here.
* `mock_mariadb` gaining a claim stage, so caller-side unit
  tests can exercise claim-stage replies.
* Functional CI coverage.
* Documentation.

**Out of scope.**

* **Hard enforcement and the 403 body** (D16's second half) --
  phase 5. This phase must not add a config knob for it
  either; see decision 4.
* **Client verbs** (`sf-client`) -- decision 7.
* **Conductor integration** (D18) -- lands in `private-ci`
  after this phase ships.
* **Delegated (non-admin) claim creation**, operator-imposed
  claims on tenants, and node affinity for claims -- all
  named as future work by D14/D15.
* **Batch create** (D8/D19), affinity (phase 6), diagnostic
  rejection logging (phase 7), removal of the in-Python
  capacity pre-filters (phase 5).
* A cluster-wide claim listing endpoint. `/admin/resources`
  already publishes the cluster totals and a claim is
  addressed through its namespace; recorded as future work
  rather than guessed at now.

## Decisions

**D1. One word for the concept: "claim".** The table, the
object, the `ObjectType` member, the REST path and any future
CLI verb all say *claim*. D15 wrote the CLI verb as `sf-client
reservation create/show/...`, which predates D14's rename of
the concept away from per-decision reservations; that naming
is superseded here. The master plan keeps its title -- it
names the technique, not the object.

**D2. The claim is a full `dbo`, and it carries two states,
which are two different facts.** The object's
`baseobject`-managed state in `object_states` is *existence*
(`created`, `deleted`, `error`), like every other object. The
`namespace_claims.state` column is *coverage* (`active`,
`expired`) and stays exactly where it is.

The alternative -- one state, in `object_states` -- was
rejected on the hot path. `_active_claim_for_namespace()`
(`mariadb.py:24790`) runs on every instance admission and
filters `state = 'active' AND expires_at > NOW()` against the
claims table's own index. Moving that predicate to
`object_states` would make the probe a join across the two
uuid storage conventions (CLAUDE.md pitfall 6: dashed
`String(36)` there, undashed `sa.Uuid` here), which is both
slower and the exact shape the codebase has been burned by.

To stop the two lying to each other they must never encode
the same fact: an expired claim is still a `created` object,
a deleted claim has no row at all, and `external_view()`
publishes both fields under distinct names: `state` carries
existence, where `_external_view()` already puts it for every
other object, and coverage is published beside it as
`coverage_state`.

*(Corrected during step 5. This decision originally read
"`state` for coverage, and the standard object state where
every other object publishes it", which is self-contradictory
-- the standard place **is** `state`. Existence keeps it,
because taking it away would make this one object's
`external_view()` disagree with every other object's for a
reason no caller could guess.)*

**D3. Creating a claim migrates the namespace's existing
drawdown into it; deleting one migrates it back.** Survey
finding 5 is a real double-spend window, so claim creation
computes the namespace's current usage before it opens its
transaction (the `_probe_admission_rows()` idiom -- a plain
read on its own connection, never inside the transaction, per
the ER_CHECKREAD invariant), seeds the new row's `used_*`
with it, and subtracts the same amounts from
`cluster_capacity.unclaimed_used_*` in the same transaction
that increments `claimed_*`. Deletion does the reverse:
whatever the claim still holds returns to the unclaimed side,
floored.

The probe is time-of-check-to-time-of-use racy against a
concurrent instance create in that namespace, by at most one
instance's allocation, in one direction, corrected by the
next reconcile pass. That is the same bargain phase 3 struck
for the branch probe and it is documented as such.

The rejected alternative -- seed zero and let the reconciler
sort it out -- is materially worse than it sounds: it is not
"the counters are briefly stale", it is "the namespace may
place its entire claim twice", for up to five minutes,
starting from the moment an operator does the thing the
feature exists for.

**D4. Advisory is unconditional this phase. No knob.** D16
asks for advisory for one release, then hard. A config option
whose `hard` setting has no 403 path -- and the 403 path is
phase 5's stated scope -- is a trap for the operator who sets
it. So the claim guard is simply not applied here, and phase
5 introduces enforcement, the knob if it still wants one, and
the structured refusal together.

**D5. Advisory over-limit is detected by read-back inside the
admission transaction, not by a second RPC.** The claim
branch issues its unguarded `UPDATE`, then re-reads the row
it just wrote and compares `used_*` against `limit_*`,
returning `claim_over_limit` and the offending dimensions in
the existing reply. The read is after our own write, on rows
we hold locks on, which the phase 3 invariant comment at
`mariadb.py:25225-25236` explicitly permits.

This is the decision most likely to be argued with, because
there is an established alternative sitting right next to it:
`Instance._admit_placement()` (`instance.py:986`) already
derives P5's over-limit event by probe-then-force -- a
guarded call used as a probe, then an unguarded call to do
the write. Reusing it would be more obviously consistent.

It is rejected because the two cases are not alike. P5's
probe-then-force pays a second RPC only on the rare path
where a ground-truth writer is recording an over-limit
placement. Advisory claims are the opposite shape: every
create in a claimed namespace would pay the probe, and the
namespaces that most want claims are the ones creating
instances hardest. Read-back costs one indexed primary-key
SELECT inside a transaction that is already open, on every
create, and nothing on the paths without a claim.

The structural benefit matters more than the RPC: with
read-back, phase 5's flip is moving a predicate that already
exists from the reply into the `WHERE` clause. With
probe-then-force it is deleting a second call site and
rewriting the caller.

**D6. Three independent guard flags, replacing P7's single
one.** `node_guarded` and `cluster_guarded` keep `enforce and
node_present`, because their fail-open reasoning is about the
node's limits being absent from the totals. `claim_guarded`
becomes `enforce and CLAIM_ENFORCEMENT_HARD`, a module
constant which is False this phase. This is where the master
plan's stated prerequisite is discharged, and it is also
what makes D4 and D5 one-line changes in phase 5 rather than
a re-litigation.

**D7. Client verbs are out of scope and functional coverage
uses the raw request path.** Survey finding 4 is a hard
dependency: a CI test written against new `apiclient` methods
cannot pass until a client release exists, and no server PR
can produce one. So the functional test drives the endpoints
through `apiclient._request_url()`, with a comment saying why
it is reaching past the public surface, and a companion issue
is filed against `shakenfist/client-python` for the verbs. The
alternative -- ship the API with no functional coverage and
wait for the client -- trades the project's stated preference
for functional tests against a cross-repo release cycle, and
loses.

**D8. Grow is guarded, shrink is floored, expiry may be
extended, nothing auto-grows.** Growing any dimension is an
admission decision using the same mirror guard as creation,
with its migrated-drawdown term at zero
(`claimed + delta + unclaimed_used <= total`), and increments
`claimed_*`. The term is zero and must stay zero: a grow moves
nothing off the unclaimed side, because the namespace's usage
is already counted in the claim's own `used_*`, so crediting a
drawdown here would count the same capacity twice. Shrinking
is always permitted down to the
claim's current `used_*` and no further, guarded by `used_*
<= new limit` so a concurrent create cannot slip under it,
and decrements `claimed_*`. A single update may grow one
dimension and shrink another; each dimension is evaluated on
its own terms in one transaction. Per D15 there is no
auto-grow, ever.

**D9. The reconciler fixes land first, as their own commit.**
Both are corrections to code that is provably inert while
`namespace_claims` is empty, which is precisely the property
that makes them safe to land ahead of the API and impossible
to validate after it. Landing them first also means the
step that makes claims creatable does not arrive on top of a
recompute known to double-count.

## Design

### The claim admission transaction

Canonical write order is unchanged and non-negotiable:
`cluster_capacity`, then `namespace_claims`, then
`scheduler_node_capacity`. A claim create/grow/shrink/delete
touches the first two, in that order, so it composes with
instance admission without a new deadlock class.

Create, in order:

1. Outside the transaction: read the namespace's current
   drawdown (D3) and the cluster singleton's presence.
2. Guarded `UPDATE cluster_capacity` -- the transaction's
   first statement, per the ER_CHECKREAD invariant --
   incrementing `claimed_*` by the new limits and
   decrementing `unclaimed_used_*` by the migrated drawdown,
   guarded by
   `claimed + limit + GREATEST(0, unclaimed_used - migrated) <= total`.
   Rowcount zero is the refusal.
3. `INSERT` the claim row with `used_*` seeded to the
   migrated drawdown, `state='active'`.

**Corrected during step 4.** This plan originally wrote the
guard as `claimed + limit <= total - unclaimed_used`, which is
D14's formula from before D3 existed and which the planning
pass failed to propagate the drawdown migration into. It tests
the state the statement *started* from rather than the one its
own `SET` produces, so it counts the namespace's usage on the
unclaimed side that the very same statement is taking off it,
and refuses an operator claiming capacity their namespace is
already holding -- which is the feature's primary use case
(the conductor sizing a runner namespace that already has
runners). Concretely: 100 cpus total, 80 used by unclaimed
namespaces of which 40 belong to `ci-1`; a 40 cpu claim for
`ci-1` lands at claimed 40 plus unclaimed_used 40, a consistent
80 of 100, and the old form computed `0 + 40 <= 100 - 80` and
refused it. The `GREATEST(0, ...)` mirrors the flooring already
in the `SET`; a guard which floored differently from the write
it guards would test a state that write cannot reach.

Delete is the mirror, floored on the way back (the
`_floored_namespace_decrement()` idiom at `mariadb.py:24746`
is the reference for what floored means here), and then the
row is deleted.

Grow/shrink is a per-dimension delta against the same guard
with its migrated term at zero, and the shrink floor as D8
describes. Zero is not an oversight there: a grow migrates
nothing, because the namespace's usage is already counted in
the claim's `used_*` rather than in the cluster's unclaimed
sums.

### Advisory accounting on the instance path

`_direct_admit_instance_placement()`'s claim branch drops its
guard clauses (D6), performs the increment, then re-reads the
row and populates two new reply fields: `claim_over_limit`
(bool) and the per-dimension detail in the shape
`dimensions` already uses. `Instance._admit_placement()`
emits an audit event from them, alongside the existing P5
event and distinct from it -- the P5 event says a
*ground-truth* write exceeded a *node* guard; this one says
a *scheduled* create exceeded its *namespace's claim*.

Release is unchanged in shape but inherits a known drift
worth stating in the code: `_active_claim_for_namespace()`
returns None once a claim has expired, so an instance charged
to a claim and released after expiry decrements the cluster's
unclaimed sums instead, which were never charged. Both sides
are floored and the reconciler corrects both within a pass.
This is not new in this phase -- phase 3 shipped it -- but it
becomes reachable in production for the first time here, so
it gets a comment and a named test rather than a discovery.

## Execution

| Step | Description | Effort | Model | Isolation | Status |
|------|-------------|--------|-------|-----------|--------|
| 1 | Reconciler correctness and cost for non-empty claims. In `_direct_reconcile_scheduler_capacity()` (`mariadb.py:23981`): de-duplicate the per-claim usage aggregation by instance uuid so a duplicated `INSTANCE_LOCATION` row counts an instance once (the obligation recorded at `mariadb.py:23798-23807`), and replace the `for claim_row in active_claims` loop (`mariadb.py:24252-24278`) with a single set-based `UPDATE ... JOIN` over the namespace usage aggregation (the obligation recorded at `mariadb.py:24256-24261`). Keep `claimed_limits`/`claimed_namespaces` correct -- they are consumed by the cluster rebuild immediately below. Fix the now-false docstring sentence at `mariadb.py:23998`. Unit tests plus a live test that a claim with a duplicated placement row reconciles to the single-count figure; the duplicate-count test must be mutation-tested against the pre-fix aggregation | medium | opus | worktree | Complete |
| 2 | The guard-flag split and advisory claim accounting in `_direct_admit_instance_placement()` (`mariadb.py:25115`). Replace `guarded = enforce and node_present` (`:25228`) with `node_guarded`/`cluster_guarded` (unchanged semantics) and `claim_guarded = enforce and CLAIM_ENFORCEMENT_HARD`, a new module constant set False. The claim branch performs its unguarded increment and then re-reads the row to populate new reply fields `claim_over_limit` and the per-dimension detail (D5); this read is after our own write and must stay after it -- read the invariant comment at `:25225-25236` before touching this function. Proto reply fields, tri-layer wrappers and the servicer reply build (`daemons/database/main.py:2516`) all move together. Unit and live tests: an over-claim create is admitted, reports over-limit with the right dimensions, and leaves `used_*` above `limit_*`; a within-limits create reports nothing; a claim guard is *not* dropped by a missing node capacity row (assert against `claim_guarded` directly, since the constant makes it unobservable until phase 5) | high | opus | worktree | Complete — the Definition of done's `guarded = enforce` grep was unsatisfiable as written and was corrected here |
| 3 | Caller side of advisory. `Instance._admit_placement()` (`instance.py:986`) emits a new audit event from the step 2 reply fields, distinct from `_event_admission_over_limit()`'s P5 event in both message and `extra` keys -- P5 is a ground-truth write exceeding a node guard, this is a scheduled create exceeding its namespace's claim. Teach `mock_mariadb._mariadb_admit_instance_placement()` (`tests/mock_mariadb.py:2742`) a claim stage: a claim row per namespace with limits and used counters, seeded by a new `set_namespace_claim()` helper beside `set_node_capacity()`, producing `failing_stage='claim'` and the over-limit fields. This discharges the phase 3 Future work entry that says phase 4 makes it necessary. Unit tests in `test_instance.py` for the event firing, not firing within limits, and not firing for a node-stage denial | medium | sonnet | worktree | Complete |
| 4 | The claim CRUD primitive: proto messages and RPCs (`protos/database.proto`, then `tox -e genprotos` -- never `grpc_tools.protoc` directly), direct implementations, gRPC and public wrappers in `mariadb.py`, servicer methods and Monitor operation registration in `daemons/database/main.py`. Follow the Design section exactly for transaction shape: canonical order, guarded UPDATE first, the D3 drawdown probe outside the transaction, floored returns on delete. Mirror the phase 3 admission RPCs for retry-on-1213/1205/1020, reply shape and the bounded-budget question (read `_grpc_get_scheduler_node_capacity()` and decide deliberately whether each new read is on a watchdog-adjacent path -- issue 3586). Unit tests for each guard dimension refusing, the shrink floor, the concurrent create-vs-create race, and double delete | high | opus | worktree | Complete — the mirror guard as planned refused legitimate claims; corrected to its migration-aware form (see the Design section's correction note) |
| 5 | The `NamespaceClaim` object: new module `shakenfist/namespace_claim.py` modelled on `shakenfist/namespace_key.py` (a namespace-owned `dbo` with its own table, `new()`, `external_view()`, `hard_delete()` and a `dbo_iter`), its pydantic schema model in `shakenfist/schema/`, its `ObjectType` member in `schema/object_types.py` with the next free `proto_id` (32) -- no hand-written ENUM migration is needed, `ensure_schema()` widens enum columns automatically since `40c91013f`. Two states, per D2: do not route coverage through `object_states`. `Namespace.hard_delete()` (`namespace.py:339`) cascades to the namespace's claims, alongside the existing key and mapping-rule cascades. Unit tests including the cascade and a `hard_delete()` that returns capacity | high | opus | worktree | Complete — D2's wording was self-contradictory and was corrected: existence keeps `state`, coverage publishes as `coverage_state`. `constants.OBJECT_NAMES_TO_CLASSES` turned out to be load bearing alongside `_STATIC_TABLE_GETTERS` |
| 6 | REST endpoints in `shakenfist/external_api/auth.py` beside the namespace key and rule endpoints (`AuthNamespaceKeysEndpoint`, `:466`), routed in `app.py` at `/auth/namespaces/<namespace>/claims` and `/auth/namespaces/<namespace>/claims/<claim_ref>`, admin-only. Every handler carries a `swag_from(swagger_helper(...))` -- the declarations are validated at import time and sf-api will not start if one is malformed; read `docs/developer_guide/writing_an_endpoint.md` first and run `tools/fix-api-parameter-locations.py --apply` before committing. Decorator order matters; the comments in `external_api/app.py` are the authority. Register any structured or bounded parameter in `STRUCTURED_PARAMETERS` (`shakenfist/tests/external_api/test_openapi_spec.py:104`), whose completeness CI derives from the published specification and fails without | medium | sonnet | worktree | Complete — five endpoints; refusals map to 507/503/409 split on whether the condition is durable or transient, and an unrecognised reason is a 500 rather than the caller's fault |
| 7 | Functional CI coverage in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`: create a claim for a namespace, create an instance in it, assert the drawdown appears on the claim and not on the cluster's unclaimed sums, exceed the claim and assert the create still succeeds and the advisory event is present, shrink below usage and assert refusal, delete the claim and assert the capacity returns. Drive the endpoints through `apiclient._request_url()` with a comment explaining D7 -- the client has no claim verbs and CI installs the released client from PyPI. Note in the test what it cannot assert without them | medium | sonnet | worktree | Complete — three tests. Two Definition of done items could not be met as written (no REST surface publishes the `cluster_capacity` singleton; the namespace cascade only runs a `CLEANER_DELAY` after the soft delete) and were corrected rather than faked; both are now Future work |
| 8 | Documentation: `docs/operator_guide/database.md` for the schema and the two new RPC families, `docs/operator_guide/scheduler.md` for what a claim is and what advisory mode does and does not do, `docs/developer_guide/subsystem_internals.md` for the claim admission transaction beside the placement one, the CLAUDE.md capacity paragraph, `ARCHITECTURE.md`/`AGENTS.md` only if the object inventory or a convention actually changed. Update the master plan Execution table and `docs/plans/index.md`. Every fact about claims must read identically wherever it appears | low | sonnet | worktree | Complete — `ARCHITECTURE.md` needed a one-clause correction (its roadmap sentence still promised the claims API as future work) and `docs/developer_guide/state_machine.md` a new section, since the claim object's comment already pointed at one. `AGENTS.md` and `README.md` untouched: no convention and no pitch changed. Also fixed the pre-existing stale scope family list in `docs/developer_guide/authentication.md` |
| 9 | Management-session code review against the checklist below | medium | management session | none | Complete — every item checked by running it rather than reading it; see the review notes below |
| 10 | Operator review and PR; deploy to sfcbr and soak with a real claim on a real namespace | — | operator | — | Not started |

## Risks and mitigations

* **The claim create transaction deadlocks against instance
  admission.** Both touch `cluster_capacity` then
  `namespace_claims`; a create that took them in the other
  order would deadlock under load rather than merely
  contend. Checked by: step 4's concurrent create-vs-create
  test, and the management review reading the statement
  order in every new transaction against the Design section.
* **ER_CHECKREAD regression.** Phase 3 spent a whole step
  (6a) discovering that a plain `SELECT` as a transaction's
  first statement turns contention into aborts on MariaDB
  11.6.2+, and CI's 10.11 cannot see it. Every transaction
  this phase adds must open with a guarded `UPDATE`. Checked
  by: the step 4 brief states it, the review checklist tests
  it, and the live suite reports the server regime it ran
  under -- but note issue #3759, which is the standing gap
  that CI does not run against a server where this bites.
* **The D3 drawdown probe is wrong under load**, seeding a
  claim with a figure that a concurrent create has already
  invalidated. Bounded to one instance's allocation and
  corrected within one reconcile pass. Checked by: a live
  test that creates a claim while creates are in flight and
  asserts the reconciler computes zero drift afterwards.
* **Advisory mode silently does nothing.** The failure mode
  of a record-don't-refuse feature is that the recording is
  missing and nobody notices, because the observable
  behaviour of "working advisory mode" and "no advisory
  mode" are identical for the create. Checked by: the step 7
  functional test asserts the *event*, not the create.
* **The new object type breaks upgraded clusters.** This
  exact thing happened on 2026-07-28 with `NAMESPACE_KEY`
  and was fixed generically in `40c91013f`. Checked by: the
  step 5 brief points at the mechanism, and the
  `schema_enum_widening` CI job covers it.
* **Scope creep into phase 5.** The temptation is one
  `if` and a 403. Checked by: the review checklist item, and
  a `git grep` for 403 in the diff.

## Definition of done

Falsifiable, and mostly runnable:

* `POST /auth/namespaces/<ns>/claims` creates a claim and a
  subsequent instance create in that namespace increments
  `namespace_claims.used_cpus` (step 7, functional). That the
  same create leaves `cluster_capacity.unclaimed_used_cpus`
  unchanged is asserted by step 4's live suite against the
  database directly.

  *(Corrected during step 7. This bullet asked step 7 to assert
  both halves functionally, which is not possible: no REST
  surface publishes the `cluster_capacity` singleton, so a
  functional test cannot see the unclaimed sums at all. The
  claim charge and the unclaimed charge are mutually exclusive
  branches of one `elif` in `_direct_admit_instance_placement()`,
  so observing the first is indirect evidence of the second --
  but the direct assertion belongs where the test can read the
  table, which is the live suite.)*
* Creating a claim for a namespace that already holds
  instances seeds `used_*` with that drawdown and reduces
  `unclaimed_used_*` by the same amounts, in one transaction
  (D3; live test).
* A claim which does not fit once its namespace's own drawdown
  has been migrated -- `claimed + limit + GREATEST(0,
  unclaimed_used - drawdown) > total` -- is refused and leaves
  `claimed_*` unchanged. Conversely, a claim which fits *only*
  because of that migration is granted. (Corrected during step
  4: this bullet said `A claim larger than total - claimed -
  unclaimed_used is refused`, which restates the pre-D3 guard
  and is not satisfiable alongside D3 -- it would refuse a
  namespace the capacity it is already using. See the Design
  section's correction note.)
* Shrinking a claim below its `used_*` is refused; shrinking
  to exactly `used_*` succeeds.
* An instance create exceeding an active claim **succeeds**
  and emits the advisory audit event carrying the exceeded
  dimensions.
* `Namespace.hard_delete()` leaves no `namespace_claims` row
  for that namespace, and the capacity returns to the
  cluster's unclaimed side.
* A claim whose namespace holds an instance with two
  `INSTANCE_LOCATION` rows reconciles to the single-count
  figure. The test fails against the pre-step-1 aggregation
  (mutation-tested, not asserted by inspection).
* `git grep -n "for claim_row in active_claims"` returns
  nothing.
* `git grep -nE "\bguarded = enforce"` returns nothing. (The word
  boundary is load bearing: `node_guarded = enforce and node_present`
  contains the old text as a substring, so the unanchored form this
  bullet originally used can never pass. Corrected during step 2.)
* `git grep -n 403 -- ':!docs' ':!*.md'` shows no new hit in
  the diff (scope guard for phase 5).
* The reconciler computes zero drift after a randomised soak
  that includes claim create, grow, shrink, expire and
  delete interleaved with instance creates and deletes.
* `pre-commit run --all-files` passes; mypy has no untyped
  defs in the new modules and RPC surface.
* No fact about claims is stated differently in `CLAUDE.md`,
  `docs/operator_guide/database.md`,
  `docs/operator_guide/scheduler.md`,
  `docs/developer_guide/subsystem_internals.md` and the
  master plan.
* A companion issue exists against
  `shakenfist/client-python` for the claim verbs (D7), and
  the functional test names it.

## Review checklist (management session, step 9)

- [x] Every new transaction opens with a guarded `UPDATE`
      and follows the canonical write order.
- [x] The D3 probe runs outside its transaction, on its own
      connection.
- [x] The advisory read-back is after the write it reads,
      and the reason is in a comment.
- [x] No 403, no enforcement knob, no hard-mode branch.
- [x] Two states stay two facts: nothing writes coverage
      into `object_states` or existence into
      `namespace_claims.state`.
- [x] Parameter declarations validate at import time and the
      openapi spec table is updated.
- [x] `hard_delete()` accounts for capacity return, and
      double delete is harmless.
- [x] Tests that claim to prove a fix have been mutated to
      confirm they fail without it.
- [x] Diff contains no phase 5/6/7 material.
- [x] mypy clean; single quotes; 120-char lines.

### Review notes (step 9, 2026-08-16)

Every box above was checked by running something, not by
reading code and believing it.

* **Transaction invariant.** All three new transactions were
  inspected for their first statement. Two open with the
  guarded `cluster_capacity` UPDATE. The third --
  `_direct_update_namespace_claim()` -- opens with it
  *conditionally*, since a pure expiry change or a shrink
  touches no cluster counter; when the condition is false the
  first statement is the guarded claim UPDATE, which is
  equally DML and equally takes its row lock first. The
  invariant holds either way and the code says so.
* **The grow guard passes `migrated=0` explicitly**, which is
  what stops a later "simplification" from crediting a
  drawdown twice. Confirmed at the call site.
* **Scope guards.** `git grep` for the old single guard flag
  and the per-claim UPDATE loop both return nothing outside
  this document. The two `403` matches in the diff are a
  comment naming phase 5's future refusal path and a
  generated protobuf offset that contains the digits; neither
  is a code path. `shakenfist/scheduler.py` is untouched, so
  no pre-filter removal leaked in from phase 5, and the diff
  contains no occurrence of "affinity" outside documentation,
  so none of phase 6 did either. There is no config knob.
* **Two states stay two facts.** `namespace_claim.py`'s state
  targets carry only existence (`initial`, `created`,
  `deleted`); coverage is read from the row and published
  separately as `coverage_state`. Nothing writes either into
  the other.
* **Mutation testing** was carried out and reported for every
  step that changed behaviour (1 through 6). Step 7 is
  functional and cannot run outside a deployed cluster; step
  8 is documentation. The step 4 guard fix was mutation
  tested after the correction, not before it.
* **Suite**: 3,241 unit tests, 3,138 passed, 103 skipped (the
  live modules, which need a MariaDB DSN), 0 failed.
  `pre-commit run --all-files` passes all nine hooks.

Two process notes worth keeping, because both nearly put a
defect in the tree:

* A sub-agent's report described the migration-aware guard as
  an open question *after* it had been told to fix it. The
  fix had not been applied; reading the `WHERE` clause is
  what established that. Reports describe intent, the tree
  describes fact.
* The same step's file mtimes were then misread as evidence
  that verification had been skipped, when the timestamp in
  question was the *restore* at the end of a mutation cycle.
  Checking is right; concluding from a single indirect signal
  is not.

## Future work

* A cluster-wide claim listing endpoint for operator
  dashboards (deliberately not built here; `/admin/resources`
  covers the totals).
* Delegated per-namespace claim creation, and
  operator-imposed claims on tenants (D15).
* Node affinity for claims, if measured claim shapes ever
  approach node size (D14's revisit condition).
* The expired-claim release asymmetry: an instance charged
  to a claim and released after the claim expired decrements
  the cluster unclaimed sums instead. Self-healing within a
  reconcile pass and documented in code by step 2, but a
  claim state of `expiring` that keeps accepting releases
  would remove the drift entirely.
* Issue #3759 (a MariaDB 11 CI job for the ER_CHECKREAD
  invariant) grows in importance with every transaction this
  phase adds.
* The `SCHEDULER_DISK_OVERCOMMIT = 5.0` variability pass
  (~2026-08-26) inherited from phase 0.
* **The two sides of the D3 migration are denominated
  differently** (found in step 4). A claim's `used_*` must be
  the namespace-wide figure, because that is what the
  reconciler's per-claim recompute writes and the two have to
  agree or a new claim's counters flap on every pass. But the
  `unclaimed_used_*` those same amounts come off is *not*
  namespace-wide: the reconciler's unclaimed fold is restricted
  to nodes which hold a capacity row, so an instance stranded
  on an unsized node is subtracted at creation having never
  been added. The `GREATEST(0, ...)` in the guard and in the
  `SET` keeps the result non-negative and the next reconcile
  pass recomputes both sides from ground truth, so the error is
  bounded by one namespace's stranded instances and lasts at
  most one period -- but while it lasts it under-counts
  unclaimed usage, which is the permissive direction. Fixing it
  properly needs a second, capacity-node-restricted aggregation
  for the cluster side, which reintroduces exactly the
  two-queries-that-can-disagree risk that sharing one query was
  chosen to eliminate. Worth doing only with both queries
  derived from one fragment and both pinned by a
  create-then-reconcile test.
* **One active claim per namespace is enforced by a probe, not
  a constraint** (found in step 4). `_probe_claim_create()`
  refuses a create for a namespace that already holds an active
  claim, but the probe runs outside the transaction, so two
  concurrent creates for one namespace can both pass it and
  both commit. The accounting stays consistent -- the
  reconciler rebuilds `claimed_*` from the sum of every active
  claim's limits, and a live test asserts the singleton agrees
  with the rows after a concurrent burst -- but the namespace
  then has two claims and admission draws down the lowest-uuid
  one. A database constraint cannot express it: uniqueness
  would have to cover *active* rows only, so that an expired
  claim can coexist with its replacement, and MariaDB has no
  partial or filtered index. The options are a uniqueness
  constraint plus deleting expired rows outright instead of
  marking them (which loses the audit trail D2 wanted), or a
  generated column that is the namespace when active and NULL
  otherwise, uniquely indexed -- the latter is probably right,
  and is a schema change rather than step 4 work.
* **The claim endpoints derive the `auth` scope family** (found
  in step 6), because they live under `/auth/namespaces/...`
  like the keys and rules beside them. A claim is a capacity
  concept rather than an auth one, so a `claim` family is
  arguably more correct -- but the families are a vocabulary
  operators write into mapping rules, pinned by
  `EXPECTED_FAMILIES` in `test_scopes.py`, and adding a word to
  it is a compatibility decision this phase was not authorised
  to make. `cluster-admin` is the gate that actually matters
  either way. Worth deciding deliberately rather than
  inheriting.
* ~~**`docs/developer_guide/authentication.md`'s scope family
  list is already stale** (noticed in step 6): it is missing
  `issuer` and `rule`, both of which shipped before this phase.
  Unrelated to claims, so step 8 should fix the list rather
  than only appending to it.~~ Fixed in step 8, checked against
  `EXPECTED_FAMILIES` in `test_scopes.py`.
* **Nothing publishes the `cluster_capacity` singleton** (found
  in step 7). `/admin/resources` reports per-node capacity, but
  the cluster totals, `claimed_*` and `unclaimed_used_*` are
  visible only to something that can read the table. That makes
  the whole cluster side of this phase's accounting
  unobservable to an operator, unassertable by a functional
  test, and invisible to D18's dashboard, which wants claimed
  versus unclaimed at a glance. Adding the singleton to
  `/admin/resources` is a small, self-contained change and is
  probably the right home for it.
* **The namespace `hard_delete()` cascade cannot be covered
  functionally** (found in step 7). `DELETE
  /auth/namespaces/<ns>` is a soft delete; the cascade runs
  when the cluster daemon collects the namespace a
  `CLEANER_DELAY` later, which is an hour. So the claim
  cascade's real cluster-side decrement is asserted only at the
  mock boundary (step 5) and by inspection. A live test that
  drives `Namespace.hard_delete()` directly would close it
  without waiting an hour.
* **`TRUSTED_ISSUER` and `MAPPING_RULE` are missing from
  `_STATIC_TABLE_GETTERS`** (found in step 5), although both
  own static tables. This is issue 3588's defect -- the orphan
  reconciler cannot repair their zombie rows, and the expiry
  sweep re-events them every pass -- live in two more object
  types. Out of scope here and deliberately not fixed in this
  phase's commits; it needs its own issue and its own commit,
  and the same pairing this phase used, since
  `_STATIC_TABLE_GETTERS` without
  `constants.OBJECT_NAMES_TO_CLASSES` trades one leak for
  another.

## Back brief

Before executing any step, back brief the operator on your
understanding of the plan and how the work aligns with it.

Two gates where agreement is cheap now and expensive later:

* **Before step 4**, confirm D3 -- that claim creation
  migrates existing drawdown rather than seeding zero. It
  sets the shape of the create transaction and is painful to
  retrofit.
* **Before step 6**, confirm the REST path shape
  (`/auth/namespaces/<namespace>/claims`). The OpenAPI
  specification is published at openapi.shakenfist.com and a
  path is expensive to move once it is.
