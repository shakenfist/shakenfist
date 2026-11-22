# Placeable capacity phase 3: shape holds, schema and the node guard

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly and ground
your answers in what the code actually does today. Do not
speculate when you could read instead. Flag uncertainty
explicitly.

This is phase 3 of `PLAN-placeable-capacity.md`. Read that master
plan's "Phase 3: shape holds — schema and the node guard (`A`)"
section first, its `G5`, `G6` and `G8` requirements, its *What a
hold promises, and what it does not* section, and phase 0's `D7`,
`D8`, `D9` and `D15`
(`PLAN-placeable-capacity-phase-00-decisions.md`). `D8` is the
schema decision this phase implements, and its *Left to phase 3*
paragraph is the list of questions this plan answers.

**Three plans number their decisions `D`-something, and the
numbers collide.** In this file, a bare `D`*n* is a decision of
*this* phase plan. A decision of
`PLAN-placeable-capacity-phase-00-decisions.md` is written
"phase 0's `D8`". A decision of shakenfist/private-ci's
`PLAN-placeable-claims` phase 0, which the master plan calls the
brief, is written "the brief's `D3`". The master plan itself uses
the second and third of those conventions inconsistently, which
is noted in `F9` below and is why this paragraph exists.

Citations below are into `develop` at `0b0271ef64` unless stated
otherwise.

## Planning effort

High, as the master plan specifies, and the review effort is high
too. This phase adds a table to the capacity schema, a term to the
admission guard that every instance create passes through, and a
counter the reconciler must recompute rather than adjust. Each of
those three has a failure mode that is silent: a migration that
skips a column on an upgraded database, a guard whose arithmetic
disagrees with the pre-filter in front of it, and a reconciler
pass that overwrites the bookings it was meant to total. None of
them fails a test that does not exist to catch it, which is why
`DoD` items here are mostly mutations and arithmetic identities
rather than feature checks.

## Scope

In:

- A `namespace_claim_holds` table: one row per hold, the ground
  truth phase 0's `D8` requires.
- `held_cpus`, `held_memory_mb` and `held_disk_gb` on
  `scheduler_node_capacity`, with the version bump and the
  existence-guarded `ADD COLUMN` migration that implies.
- The booking path in the core: creating holds with a claim,
  choosing their nodes through phase 1's
  `find_candidates_for_shapes()`, and the `sum(holds) <= limit_*`
  invariant tested in SQL inside the claim's own transaction, at
  creation and at update.
- The release paths that belong to the claim's own lifecycle:
  claim delete, the reconciler's expiry sweep, and a shrink that
  leaves the hold sum above the new limit.
- The node guard term (`G6`): `used + held + request <= limit` in
  `_claim_node()`, and `held` carried in the per-dimension denial
  detail.
- The reconciler's recomputation of the three counters from hold
  rows, in the pass that already recomputes `used_*`.
- The pre-filter's agreement with that guard, and the brief's
  `D2` narrowing of the live memory check.
- Visibility (`G8`) in `_summarize_resources()`.
- The proto fields and the sf-database RPCs the above needs.

Out:

- **Any API surface, and the capability token for `A`.** See
  `D6`. Nothing a REST caller can reach creates a hold in this
  phase; the booking path is reachable from `NamespaceClaim` and
  from sf-database's RPC, and is exercised by tests.
- Consumption: implicit best fit, the `POST /instances` hold
  reference, and moving a hold's room from `held_*` to `used_*`.
  That is phase 4, under `G3` and phase 0's `D7`.
- Displacement and re-placement when a node leaves service. Phase
  4, under phase 0's `D9`. What this phase does instead with a
  hold whose node has gone is `D8` below.
- The refusal contract: `stage`/`transient`/`Retry-After` on claim
  refusals, and the reclassification of a held refusal as
  non-transient. Phase 5. `R3` is the sequencing hazard that
  creates, and it is a hazard for phase 4 rather than for this
  phase.
- `sf_claim`, `sf-client` and the operator-facing documentation of
  how to express a shape. Phase 6, under phase 0's `D11`.
- The `_derive_disk_limit_gb()` defect that is not a defect: see
  `D9`. Held disk stays outside that derivation deliberately.

## What the survey found

Phase 0's `D7` and `D8` are unusually complete for a design this
phase inherits — between them they settle the hold's identity, its
lifetime, its storage, the reconciler's obligation and five
rejected alternatives, and `D8` ends with an explicit list of what
it left here. So the survey's job was mostly to re-find the
citations and to check the three claims the master plan's own
phase 3 section makes in its own words. Two of those three are
right, one is ambiguous in a way that matters, and phase 0's `D8`
contains one instruction this phase declines (`D4`).

**`F1`. Every line number in phase 0's decisions has drifted, and
by hundreds of lines.** That file says so of itself — it was
researched against `32de6cec7f` on 2026-11-07 and warns that the
master plan's citations had already drifted — and phase 2 then
landed. The current locations of everything `D7` and `D8` cite:

| Cited as | Now |
|---|---|
| `_claim_node()` `mariadb.py:29013-29037` | `mariadb.py:29769-29793` |
| `_claim_capacity_guard()` `:29927-29972` | `:30694-30740` |
| `_floored_node_decrement()` `:28284-28310` | `:29039` |
| `_probe_admission_rows()` block comment | `:29270` |
| `_direct_admit_instance_placement()` `:28750-29038` | `:29505-29945` |
| `_reconcile_fetch_usage()` `:27085-27108` | `:27800-27823` |
| `_RECONCILE_USAGE_SQL` `:26872` | `:27587` |
| `_derive_disk_limit_gb()` `:26676-26712` | `:27365-27400` |
| reconciler `removals` `:27360-27368` | `:28075-28083`, deleted at `:28167-28170` |
| expiry sweep `:27223-27228` | `:27942` |
| shrink floor `:30368-30374` | `:31186-31191` |
| `_blocked_by_shrink_floor()` `:30534-30556` | `:31301` |
| `EXPECTED_SCHEMA_VERSIONS` `:575-622` | `:704-740` |
| `SCHEDULER_NODE_CAPACITY_VERSION` `:546` | `:675` |
| `_add_missing_namespace_claims_columns()` `:4117-4144` | `:4381-4407` |
| `_ensure_namespace_claims_schema()` `:4146-4179` | `:4410-4446` |
| `_has_sufficient_ram()` third sub-check `scheduler.py:808-826` | `scheduler.py:1130-1159` |
| `_summarize_resources()` `scheduler.py:457-545` | `scheduler.py:676-888` |
| `Scheduler.summarize_resources()` `scheduler.py:1377` | `scheduler.py:1890` |
| hard affinity `scheduler.py:623-665` | `_satisfies_hard_affinity()` `:953`, `_affinity_neighbour_skip()` `:508` |
| `arg_is_claim_ref()` `auth.py:1504-1541` | `auth.py:2250` |
| `_claim_refusal()` bare `sf_api.error()` | `auth.py:2195` |
| `NamespaceClaim.hard_delete()` `namespace_claim.py:387-397` | unchanged, `:387` |

Every one of those was re-found rather than recomputed from a
diffstat, and the step briefs below cite the current numbers. The
substance of each citation was checked at its new home and none of
it had changed.

**`F2`. "The per-node held column beside `used_*`" is ambiguous,
and only one reading is implementable.** The master plan's phase 3
section and `Q4` both say the schema decision is "a ledger
dimension beside `used_*`" without saying which table's `used_*`.
There are two: `namespace_claims.used_*` (`mariadb.py:4268-4276`)
and `scheduler_node_capacity.used_*` (`:4194-4202`). A hold books
one shape onto one node, so a per-node quantity cannot live on the
claim row — and `_direct_admit_instance_placement()` says in terms
why not: a claim's counters "are namespace-denominated and
node-independent" (`:29688-29693`). Phase 0's `D8` resolves this
correctly and in the only direction that works: the counters go on
`scheduler_node_capacity`, the per-(claim, node, shape) detail
goes in a new table, and there is no held column on
`namespace_claims` or on `cluster_capacity`. This phase follows
`D8`. The master plan's wording is corrected at its source in the
planning commit, because a reader who takes the other reading
designs a ledger that cannot be guarded in SQL.

**`F3`. Nothing of this phase's scope has shipped.** `grep -rln
'shape_hold\|claim_hold\|held_cpus\|held_memory\|namespace_claim_holds'`
over `*.py`, `*.proto` and the repository's SQL returns nothing.
Unlike phases 1 and 2, whose premises went stale because other
work arrived first, this phase's premise is intact: no table, no
column, no proto field, no helper.

**`F4`. "Holds sit inside `claimed`" is true, and the reconciler
is where it is true.** The master plan's phase 3 section rests the
claim-branch argument on it, so it was checked rather than taken:
`cluster_capacity.claimed_*` is the sum of **limits** of claims in
coverage state `active`, accumulated at `mariadb.py:28177-28183`
and written at `:28228-28230`. A hold lives inside its claim's
`limit_*`, which is therefore already inside `claimed_*`. So no
cluster-side counter is needed, the claim branch of the admission
transaction is genuinely unchanged, and `_claim_capacity_guard()`
must not gain a hold term — which is also what its own docstring
warns twice about, for the adjacent migrated term (`:30711-30725`).

**`F5`. The node guard exists twice, and the second copy does not
cover disk.** The guard phase 3 must change is the `WHERE` clause
in `_claim_node()` (`mariadb.py:29769-29793`), which tests all
three dimensions. In front of it sits the scheduler's pre-filter,
which charges the same ledger so that a burst does not get
attracted to a node that will then refuse it (issue 3636):
`_has_sufficient_cpu()` reads `used_cpus` against `limit_cpus`
(`scheduler.py:1052-1065`) and `_has_sufficient_ram()` reads
`used_memory_mb` against `limit_memory_mb` (`:1130-1159`).
`_has_sufficient_disk()` reads **no capacity row at all**
(`:1162-1184`): it tests filesystem free space minus the node's
reservation, and the disk ledger appears nowhere in the
pre-filter. So after this phase a node's held disk is invisible
until admission. That is not a new asymmetry — `used_disk_gb` is
equally invisible there today — and `place_on_candidates()` walks
to the next candidate on a guard denial rather than failing the
create (`instance.py:1628-1645`), so the cost is a wasted
admission attempt per candidate, not a spurious refusal. `D11`
keeps the asymmetry rather than closing it, and says why that is
not laziness.

**`F6`. `disk_available` has no ledger term, which is why phase
0's `D8`'s visibility instruction cannot be followed literally.**
`D8` says `_summarize_resources()` should net held room out of
`cpu_available`, `ram_available` and `disk_available`. The first
two already carry a ledger term: `cpu_available` is
`hard_max_cpus - max(measured, committed)` (`scheduler.py:817`)
and `ram_available` is explicitly `min()`-ed against
`limit_memory_mb - committed_mb` (`:846-852`). `disk_available` is
`disk_free_instances / GiB - disk_reservation_gb`
(`:857-864`) — a filesystem measurement with no ledger in it at
all, which is why the capacity row's disk counters are published
*beside* it rather than folded into it, for the reason the comment
at `:865-874` gives (headroom moves under load, the ledger ceiling
does not; issue 4208). Netting held disk out of it would be the
first ledger term in a measured figure, and it would disagree with
the guard, which tests against `limit_disk_gb`. `D4` amends `D8`
on this one point and leaves the rest of it intact.

**`F7`. The `_derive_disk_limit_gb()` trap `D8` warns about is
real and concrete.** The limit is `used_disk_gb + headroom`
(`mariadb.py:27365-27400`), and the reconciler passes the freshly
recomputed `used[2]` into it (`:28104-28108`). If held disk ever
entered that `used` term, the guard `used + held + request <=
limit` would expand to `request <= headroom` and admit every hold
a second time. The call site is one line and the mistake is one
word, so `D9` pins it with a mutation rather than a comment.

**`F8`. The schema has no foreign keys anywhere.** `grep -c
'ForeignKey' shakenfist/mariadb.py` is 0. A hold's claim uuid,
node uuid and instance uuid are therefore plain columns, like
`object_references`' are, and referential integrity is the
reconciler's job rather than the engine's. `D1` follows that.

**`F9`. The master plan mixes two `D` namespaces without saying
so, and one citation inside phase 0 says so explicitly.** The
master plan's phase 4 bullet cites "(`D3`, `D8`)" meaning the
brief's, while its phase 3 bullet cites phase 0's `D15` by the
same unqualified form; phase 0's `D7` is careful about it in one
place, noting that a docstring's "`D15`" is
scheduler-reservations' rather than this plan's
(the claim-update docstring at `mariadb.py:31122-31124`). This is a
documentation hazard rather than a code one, and the Prompt
section above states the convention this file uses. The master
plan is not rewritten for it; its phase 3 bullet is the only one
this phase's commit touches.

**`F10`. The commit that opened this phase's gate is not in the
Execution table, and does not need to be.** `d05845338c` (#5013)
recorded the `Hold` verdict and filled phase 2's deferred `Merged`
cell. It is docs-only — `PLAN-placeable-capacity.md` alone — so
phase 7's audit loses nothing by not finding it in a `Merged`
cell. It is named here so it is findable.

## Decisions

**`D1`. The table is `namespace_claim_holds`, with no foreign
keys, server-side timestamps, and three indexes.**

```
namespace_claim_holds
  uuid          CHAR(32) PRIMARY KEY     -- uuid_column_type()
  claim_uuid    CHAR(32) NOT NULL
  namespace     VARCHAR(255) NOT NULL
  cpus          BIGINT NOT NULL
  memory_mb     BIGINT NOT NULL
  disk_gb       BIGINT NOT NULL
  node_uuid     CHAR(32) NULL            -- NULL means displaced
  instance_uuid CHAR(32) NULL            -- the consuming instance
  created_at    DATETIME(6) NOT NULL     -- see the execution notes
  updated_at    DATETIME(6) NOT NULL
  INDEX idx_namespace_claim_holds_claim (claim_uuid)
  INDEX idx_namespace_claim_holds_node  (node_uuid)
  INDEX idx_namespace_claim_holds_instance (instance_uuid)
```

The dimension columns are named `cpus`/`memory_mb`/`disk_gb` and
not `held_*`: on a hold row the whole row *is* the holding, so a
`held_` prefix would be redundant where on the node row it is the
distinction that matters. `BIGINT` throughout, for the reason the
other three capacity tables give (`mariadb.py:4186-4190`): the
proto fields are `int64` and the singleton sums them.

`namespace` is carried even though it is derivable from the claim,
because the per-node fold and the release paths both filter on it
and a join to `namespace_claims` on the admission hot path is what
phase 0's `D8` built the counters to avoid. It is a denormalisation
and the reconciler is its authority, like every other counter in
this family.

Server-side `DATETIME` rather than `object_references`' float
epochs, because this table is a member of the capacity family and
follows that family's `cluster_locks` TIMESTAMP idiom: `created_at`
orders a shrink's releases (`D5`) and `updated_at` is compared
against the database's `NOW()`, never a per-node clock. **Amended
during step 3d:** both are `DATETIME(6)`, because at whole-second
precision the ordering `created_at` exists to provide was decided by
the uuid tie-break whenever two bookings landed in the same second.
The execution notes carry the three ways that change could have been
made without taking effect. Three
indexes because there are exactly three access paths: the sum
invariant filters by claim, the per-node fold groups by node, and
phase 4's consumption and release look up by instance.

No foreign keys, per `F8`.

**`D2`. Consumption state is the nullable `instance_uuid` alone.**
Phase 0's `D8` left open "whether the consumption state is its own
column or is read from the nullable instance uuid alone". It is
read from the uuid. A separate boolean would be a second ledger
that agrees with the first only until the next write — the reason
`_summarize_resources()` publishes the singleton rather than
recomputing it (`scheduler.py:676-690`) — and it would buy nothing,
because `D8`'s residue formula needs the consuming instance's
request and so needs the uuid regardless. The cost is that
"consumed" and "consumed by an instance that no longer exists" are
the same state until the reconciler notices; that is phase 4's
release path, and until phase 4 ships nothing sets the column.

**`D3`. The holds table is last in the canonical table order, and
the sum invariant is a scalar subquery inside the guarded
`UPDATE`, never a separate `SELECT`.**

Phase 0's `D7` left this phase "where the hold-taking statement
sits in the transaction's canonical table order". The order today
is `cluster_capacity`, then `namespace_claims`, then
`scheduler_node_capacity` (`mariadb.py:29513-29520`), and it is
what keeps concurrent admissions from deadlocking rather than
merely recovering. `namespace_claim_holds` goes **after** all
three. That position changes no existing writer's order, so the
deadlock-freedom argument carries over unchanged, and it is the
order both transactions that touch holds want anyway: booking
tests the claim and increments the node before writing the hold
row, and phase 4's consumption excludes the hold from the node
guard and then marks the row.

The sum invariant is the subtle half. `sum(holds) <= limit_*` has
to be tested inside the claim's transaction, and that transaction's
first statement must be a guarded `UPDATE` or every writer that
loses a race dies of `ER_CHECKREAD` (1020) under
`innodb_snapshot_isolation`, which is on by default from MariaDB
11.6.2 — the invariant stated at `mariadb.py:29662-29675` and
measured at 46 of 50 concurrent admissions exhausting their retry
budget. A plain `SELECT SUM(...) FROM namespace_claim_holds` added
to the claim transaction is exactly the statement that invariant
forbids, and it reads a table this transaction holds no locks on,
so it would be the one read that cannot be excused as "after our
own writes". The invariant therefore rides as a correlated scalar
subquery in the `WHERE` of the claim's own guarded `UPDATE`:

```sql
UPDATE namespace_claims SET ... WHERE uuid = :claim
  AND :new_limit_cpus >= COALESCE(
      (SELECT SUM(cpus) FROM namespace_claim_holds
        WHERE claim_uuid = :claim), 0)
  AND ...
```

Phase 0's `D8` already said this is affordable — claim create and
update are not a hot path, unlike admission — and the subquery
form is what makes it safe as well as affordable. `COALESCE` is
load bearing: `SUM` over no rows is `NULL`, and `x >= NULL` is
`NULL`, which is not true, so a claim with no holds would fail its
own guard.

**`D4`. Held room is netted out of `cpu_available` and
`ram_available` and is *not* netted out of `disk_available`. This
amends phase 0's `D8`.** `F6` has the reasoning: the first two
figures already carry a ledger term and are already `min()`-ed
against the ledger ceiling, so netting held out of them keeps them
saying what they say today. `disk_available` is a filesystem
measurement, and netting a booking out of it would be the first
ledger term in it, would disagree with the guard, and would
contradict the comment that explains why the disk ledger is
published beside it rather than folded into it. All three `*_held`
figures are published either way, so nothing is hidden; what
changes is which published figure already accounts for them. A
client-side ledger model that reads `disk_available` and wants the
ledger reads `disk_limit_gb`, `disk_committed_gb` and
`disk_held_gb`, exactly as it must today to see committed disk.

This is the decision in this plan most likely to be argued with,
because it declines an instruction from a landed design phase. The
narrow case for following `D8` literally is that `G8`'s stated
purpose is that `summarize_resources()` "must net holds out, or
every client-side ledger model counts booked room as free" — and
on disk, it will. The answer is that it already counts *committed*
room as free there, that the figure is not a ledger and cannot be
made into one by subtraction, and that the private-ci consumer
phase 2 was built for reads the per-node figures rather than the
aggregate (`PLAN-placeable-claims-phase-05a-fit-by-query.md`). The
amendment is recorded at `D8`'s own site in the planning commit so
the next reader meets it there.

**`D5`. Field names follow their neighbours on both sides, which
means the column and the published figure differ.** The columns are
`held_cpus`, `held_memory_mb`, `held_disk_gb`, beside
`used_cpus`/`used_memory_mb`/`used_disk_gb`. The published figures
are `cpu_held`, `ram_held_mb`, `disk_held_gb`, beside
`cpu_committed`, `ram_committed`, `disk_committed_gb`. Phase 0's
`D8` wrote the middle one as `memory_held_mb`; that would match the
column and clash with every figure around it, and this codebase
already carries exactly this split — the column is
`used_memory_mb` and the figure it feeds is `ram_committed`
(`scheduler.py:845`). Consistency with the immediate neighbours
wins on each side of the boundary.

**`D6`. This phase ships no API surface and advertises no
capability.** `G5` requires one capability string for `A`, and a
capability token is a promise that the thing works. `A` does not
work until a create can consume a hold, which is phase 4. So
advertising the token here would advertise a half-built `A`, and
shipping a booking endpoint without a token is the other thing
`G5` forbids ("additive and advertised"). Phase 4 adds the
endpoint and the token together. This phase's booking path is the
sf-database RPC and a `NamespaceClaim` method, exercised by unit
and live tests.

The consequence is deliberate and is the mitigation for `R3`:
`held_*` is zero in every production cluster for this phase's
release, because nothing a caller can reach writes a hold row.
Every arithmetic change this phase makes is therefore a no-op in
production until phase 4, which is the right shape for a change to
the guard every instance create passes through.

The argument against is that a phase which ships a schema, a guard
term and a reconciler fold with no reachable producer ships dead
code for a release. That is true, and it is the better of the two
failure modes: the alternative ships a reachable producer of rows
that nothing can consume, release on delete, or displace, in a
release whose refusal contract still tells callers that a hold
refusal is transient.

**`D7`. The pre-filter gains the held term in its ledger
sub-checks for cpu and memory, and the live memory sub-check gains
the brief's `D2` narrowing.** Three changes in `scheduler.py`:

- `_has_sufficient_cpu()`: the ledger side becomes `committed +
  held`, so `current_cpu = max(measured_cpus, committed_cpus +
  held_cpus)`. The self-exclusion for a reschedule onto the same
  node applies to `committed` only, because a hold is not the
  instance being replaced.
- `_has_sufficient_ram()`, third sub-check: the same, against
  `limit_memory_mb`.
- `_has_sufficient_ram()`, first sub-check: `available` is reduced
  by the node's **unconsumed** held memory. This is the brief's
  `D2` first narrowing — "every other create's live check counts a
  node's unconsumed holds as if resident" — and it is the one place
  in this phase where a ledger quantity deliberately enters a
  measured figure. It is justified where `D4`'s disk netting is
  not: a booked instance's memory *will* become resident, and the
  live check exists to predict the OOM kills that set
  `RAM_OVERCOMMIT_RATIO` to 1.0, so a booking is exactly the kind
  of future residency it should fear. Filesystem disk has no
  equivalent — a booked disk consumes no blocks until something
  writes to it.

Why the pre-filter at all, when the guard is the decision: because
a pre-filter that disagrees with the guard attracts a burst to a
node that then refuses all of it, which is issue 3636 and is the
reason the ledger terms are in these filters today
(`scheduler.py:1130-1140`).

**`D8`. A hold whose node has no capacity row contributes to no
node's `held_*`, and this phase does not displace it.** The
reconciler deletes capacity rows for nodes that have left service
(`mariadb.py:28167-28170`), and the `held_*` fold is restricted to
nodes that still hold a row after the pass — the same restriction
the unclaimed fold already uses, for the reason given at
`:28201-28214` (an instance stranded on a drained hypervisor must
inflate neither side of a total its node no longer contributes
to). The hold row keeps pointing at the departed node until phase
4 displaces it. With no foreign keys (`F8`) that dangles nothing,
and phase 0's `D9` owns the policy.

**`D9`. `held_disk_gb` never enters `_derive_disk_limit_gb()`, and
a mutation proves a test says so.** `F7` is the trap. A comment is
not enough for a one-word mistake on a line that already takes a
`used_*` value as its first argument, so step 3i mutates
`used[2]` to `used[2] + held[2]` at `mariadb.py:28104` and the
phase is not done until a named test fails on it.

**`D10`. The reconciler recomputes `held_*` as `SUM(size -
consumed_request)` over the node's hold rows whose claim is
active, in the pass and the read that already recompute `used_*`.**
Phase 0's `D8` fixes this; what is left to decide is where the
consuming instance's request comes from. It comes from the
`instances` row the existing usage SQL already joins
(`_RECONCILE_USAGE_SQL`, `mariadb.py:27587`), not from a column on
the hold row: a copy of the request on the hold row is a third
ledger, and the instance row is the same ground truth `used_*` is
computed from, so the residue and the usage cannot disagree about
one instance. An unconsumed hold's residue is its full size, which
falls out of the same expression with a `LEFT JOIN`.

**`D11`. The disk pre-filter asymmetry stays.** `F5`: adding a
ledger disk term to `_has_sufficient_disk()` would be a behaviour
change for every create in the cluster, held or not, on a stage
whose refusals are the largest single family in the private-ci
reading phase 5a just took. It is out of this phase's scope, it is
not required by `G6` (which is about the guard), and it should be
decided with the measurement rather than on the way past. It is
recorded in the master plan's Future work in the planning commit.

## Step plan

Every step runs in this phase's worktree,
`../shakenfist-wt-pc-p03`, and they run **strictly in sequence**.
Two reasons, both learned here: `pre-commit`'s stash is
repository-wide, so two agents in one worktree silently revert each
other, and a mutation run that overlaps a check leaves a killed
mutant in place with its own test gone quiet. One step at a time,
one `tox`/`pre-commit` at a time.

Steps 3a and 3b are the only ones that may not be reordered: every
later step reads the table and the columns 3a creates, and 3c
onwards call the accessors 3b generates.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | **The schema: one table, three columns, two migrations.** Read this plan's `D1`, `F1` and `F8`, and phase 0's `D8` in full. Add `_get_namespace_claim_holds_table()` to `shakenfist/mariadb.py` beside `_get_namespace_claims_table()` (`:4219`), following that function's exact idiom: module-level `Optional[sa.Table]` cache declared beside `_namespace_claims_table` (`:4152`), `TABLE_CREATION_LOCK`, the double-checked `metadata.tables` lookup, and a docstring that says what the table is ground truth for and why its timestamps are server-side. The DDL is `D1`'s, verbatim, including the three index names. Add `NAMESPACE_CLAIM_HOLDS_VERSION = 1` beside `NAMESPACE_CLAIMS_VERSION` (`:676`), an entry in `EXPECTED_SCHEMA_VERSIONS` (`:704-740`), an `_ensure_namespace_claim_holds_schema()` modelled on `_ensure_namespace_claims_schema()` (`:4410-4446`) — the `current_ver <= 0` create-at-current-version branch and nothing else, since version 1 has no prior shape — and its `results.append(...)` line in `ensure_schema()` (`:4547-4549`), placed after the three existing capacity tables. Then add `held_cpus`, `held_memory_mb`, `held_disk_gb` to `_get_scheduler_node_capacity_table()` (`:4181-4203`) as `sa.BigInteger(), nullable=False, server_default='0'`, bump `SCHEDULER_NODE_CAPACITY_VERSION` from 1 to 2 (`:675`), and add the v1-to-2 migration: a `SCHEDULER_NODE_CAPACITY_HELD_COLUMNS` tuple and an `_add_missing_scheduler_node_capacity_columns()`, both mirroring `NAMESPACE_CLAIMS_PEAK_COLUMNS` (`:4215`) and `_add_missing_namespace_claims_columns()` (`:4381-4407`) — the column type taken from the table definition so the DDL cannot drift, each `ADD COLUMN` existence-guarded so a re-run is a no-op — wired into `_ensure_scheduler_node_capacity_schema()` (`:4354`) under a `current_ver < 2` branch. Do **not** backfill `held_*` from anything: there are no hold rows, so zero is correct, and the next reconcile pass is the authority regardless. Tests go in `shakenfist/tests/test_mariadb_capacity_schema.py`: mirror `test_namespace_claims_types` (the guard that every name in the columns tuple is a `BigInteger NOT NULL` with a server default, and that the table defines no `held_*` column the tuple omits — read that test and copy its shape, because it is what stops the migration silently skipping a column), the holds table's own column types and index set, both tables' presence in `EXPECTED_SCHEMA_VERSIONS`, and that `ensure_schema()` names the new ensure function. Also update `shakenfist/tests/mock_mariadb.py` if it enumerates capacity columns. Checks: `tox -epy3`, `tox -eflake8`, `tox -emypy`, then `pre-commit run --all-files` from the repository root. Commit subject: `Add the shape holds table and held counters.` |
| 3b | medium | sonnet | none | **The proto fields, the read path, and the booking and release RPCs.** Read `D1`, `D2` and `D5`, and the `add-grpc-service` skill, which is the house procedure for this and is more specific than this brief. In `protos/database.proto`, add `int64 held_cpus = 9; int64 held_memory_mb = 10; int64 held_disk_gb = 11;` to `SchedulerNodeCapacityRow` (`:3161-3170`) — new field numbers, never reusing one — and a `NamespaceClaimHold` message carrying `D1`'s columns with the two nullable uuids as empty-string-means-unset `string`, matching how the rest of this proto spells an absent uuid. Add the request/reply pairs for booking holds onto a claim, listing a claim's holds, and releasing holds, modelled on the namespace claim CRUD block that starts at `:3219`. Regenerate with `tox -e genprotos` and commit the generated files. Then carry the three counters through the read path: `_direct_get_scheduler_node_capacity()` (`shakenfist/mariadb.py:30089`), `_grpc_get_scheduler_node_capacity()` (`:30279`), the `SchedulerNodeCapacityRead` typed dict and `CAPACITY_*` figure tuples near `:28618`, and the servicer in `shakenfist/daemons/database/main.py` — including a counter per new RPC in the Monitor operations list at `:6957-6959`, which is the registration that is easy to miss. Leave the *implementations* of booking and release raising `NotImplementedError` or returning an empty result: steps 3c and 3d write them, and this step's job is the wire surface. Tests: that a row read back carries the three counters, that an older reply without them reads as zero rather than raising, and the servicer counter names. Checks as 3a, plus confirm `git status` shows the regenerated `*_pb2*` files as changed and name them (every step leaves its work uncommitted for the management session to stage, so do not stage anything yourself). Commit subject: `Carry held counters and hold rows over gRPC.` |
| 3c | xhigh | opus | none | **The booking path, and the sum invariant that makes it safe.** Read `D1`, `D2`, `D3`, `D6` and `D10`, phase 0's `D7` and `D8` in full, and the block comment above `_probe_admission_rows()` (`shakenfist/mariadb.py:29270`) and the `INVARIANT` comment inside `_direct_admit_instance_placement()` (`:29662-29675`) before writing a line — `D3` turns on them. Add `_direct_book_claim_holds()` and its gRPC and public wrappers, mirroring the trio layout of the claim CRUD functions (`_direct_create_namespace_claim()` `:30934`, `_direct_update_namespace_claim()` `:31114`). It takes a claim uuid and a list of shapes with their chosen nodes, and in one transaction, in `D3`'s order: the claim's guarded `UPDATE` carrying `D3`'s correlated-subquery sum invariant in its `WHERE` (and note `COALESCE` — `SUM` over no rows is `NULL`); then one guarded `UPDATE` per node row incrementing `held_*`, taken in node-uuid order for the reason `_claim_node()`'s sibling comment gives (`:29760-29768`); then the `INSERT` of the hold rows. A guard matching no row aborts the whole transaction and reports which dimension and which stage, in the shape `_AdmissionDenied` (`:28726`) and `_admission_denial_dimensions()` (`:29413`) already use. Wrap it in `_retry_transaction()` like its neighbours. Node choice is **not** this function's job: the caller passes nodes, and the caller is a new `NamespaceClaim` method that calls `Scheduler.find_candidates_for_shapes()` (`shakenfist/scheduler.py:1276`) and takes the head of each shape's list — read that method's docstring, especially the all-or-nothing contract and the refused shape's index, and let its exception propagate rather than translating it. Add the claim-side `sum(holds) <= limit_*` test to the **creation** path too (`:30934`), in the same subquery form, even though a new claim can have no holds: `G6`'s invariant is stated at both sites in phase 0's `D7` and a later phase that books at creation must not have to add the guard then. No API, no capability, no endpoint (`D6`). Tests in a new `shakenfist/tests/test_mariadb_capacity_holds.py` plus a live counterpart beside `test_mariadb_capacity_claims_live.py`: booking increments exactly the named nodes' counters and writes exactly one row per shape; a booking that would push the claim's hold sum over a limit is refused whole, with no counter moved and no row written; two concurrent bookings of the same claim do not both pass the invariant (the live test, which is the only place that can show it); and the compiled `WHERE` of the claim `UPDATE` contains the subquery, asserted through `_compiled()` and the dispatching fake connection at `shakenfist/tests/test_mariadb_capacity_admission.py:49` and `:103`, which is how that suite already asserts the claim `UPDATE`'s `ordered_values()` ordering. Read `docs/developer_guide/database_internals.md` for how to run the live suite against a local MariaDB — it needs `--serial`. Checks as 3a. Commit subject: `Book shape holds onto nodes.` |
| 3d | high | opus | none | **The release paths that belong to the claim's lifecycle.** Read `D1`, `D5`, `D8`, and phase 0's `D7`'s lifetime paragraphs, which are the specification: a hold ends when its claim is deleted, when the expiry sweep lapses it, or when a shrink leaves the hold sum above the new limit — and *not* when its instance is deleted, which is phase 4's. Three sites. (1) Claim delete: `NamespaceClaim.hard_delete()` (`shakenfist/namespace_claim.py:387`) "gives the capacity back inside the same transaction that removes the row"; the holds and their `held_*` decrements go in that same transaction, decrementing with the floored form `_floored_node_decrement()` uses (`shakenfist/mariadb.py:29039`) so a counter the reconciler has not yet corrected cannot go negative. (2) The expiry sweep (`:27942`): a claim moving to `expired` releases its holds rather than displacing them, for the reason phase 0's `D7` gives — an expired claim's limits are not in `claimed_*`, so there is no aggregate for a displaced hold to sit inside. (3) Shrink: the floor stays `used_* <= new limit` exactly where it is (`:31186-31191`, diagnosed by `_blocked_by_shrink_floor()` at `:31301`) and is **not** raised to `used + held` — phase 0's `D7` rejects that explicitly, twice. Instead a shrink that leaves `sum(holds)` above the new limit releases holds, most recently booked first by `created_at` (`D1`), until the claim fits, and names each release in the update's reply and in an event on the claim. Note the ordering hazard: the releases must be computed and applied inside the same transaction as the limit change, or a concurrent booking lands between them. Tests: each of the three paths releases rows and decrements the named nodes; a shrink releases the newest hold first and stops as soon as the claim fits; a shrink that needs no release changes no hold; and a decrement cannot drive a counter negative. Checks as 3a. Commit subject: `Release shape holds with their claim.` |
| 3e | high | opus | none | **The node guard term, and the denial detail that explains it.** Read `D6`, `D7` and `F5`. In `_claim_node()` (`shakenfist/mariadb.py:29769-29793`) the three guard clauses become `used_* + held_* + request <= limit_*`. Do not touch the `SET` clause: an admission charges `used_*` and nothing here moves `held_*`, because consumption is phase 4. Do not touch the claim branch (`:29714-29726`) or the cluster branch (`:29728-29753`) at all — `F4` is why, and `_claim_capacity_guard()` (`:30694`) must not gain a hold term whatever it looks like it is missing. Then make the denial explain itself: `_admission_denial_dimensions()`'s `node` branch (`:29461-29472`) passes `used_* + held_*` as the `used` argument to `_capacity_dimension()` so `exceeded` recomputes the test the guard actually made, and `_capacity_dimension()` (`:28822`) gains a keyword-only `held` key reported beside the others -- which also requires an `optional double held` field on `CapacityDimensionDetail` in `protos/database.proto` and a `tox -e genprotos`, because the servicer splats the dimension dict into `reply.dimensions.add()` and a key with no field behind it raises — absent, not zero, on the dimensions that have none, exactly as `cpu_load_1` and `expected_demand` are absent on the three allocation dimensions today, and for the same stated reason: which term bound is what tells a correct refusal from a defect. Read that docstring in full first; it explains why `charged` is keyword-only, and the same care applies here. Then `D7`'s pre-filter changes in `shakenfist/scheduler.py`: `_has_sufficient_cpu()` (`:1052-1065`), and both sub-checks of `_has_sufficient_ram()` (`:1085-1159`). The capacity row reaches them through `PlacementSnapshot.capacity_row()` (`:244`), so `held_*` arrives with the row once 3b lands; the refusal `reason` dicts gain a `held_*` key beside the `committed_*` they already carry, because those dicts are what the audit event publishes. Mint no new stage name and change no existing one (`G5`): `grep` the diff for stage strings before you finish. Tests in `test_scheduler.py` and `test_mariadb_capacity_admission.py`: a node whose `used + held` exceeds its limit is refused where `used` alone would not; the refusal names the held term; a reschedule onto the same node excludes its own `used` charge but not the node's holds; a node with no capacity row is still unguarded and unfiltered; and the pre-filter and the guard agree on the same row, which is worth asserting directly rather than implying. Checks as 3a. Commit subject: `Guard node capacity against held room.` |
| 3f | high | opus | none | **The reconciler recomputes the held counters.** Read `D8`, `D9`, `D10`, `F7` and phase 0's `D8`'s reconciler paragraph. In `shakenfist/mariadb.py`, extend the usage recompute so one pass feeds both counters: add a holds fold beside `_reconcile_fetch_usage()` (`:27800-27823`), reading `D10`'s `SUM(size - consumed_request)` over hold rows joined `LEFT` to `instances` for the consuming instance's allocation, grouped by node, restricted to claims in coverage `active`. Read the comment block above `_RECONCILE_USAGE_SQL` (`:27500-27530`) first: it records the two uuid storage conventions a bound parameter here has to satisfy — `object_references` and `object_states` store the dashed 36-character form while `instances.uuid` is undashed `CHAR(32)` — and your hold rows store whichever form `D1`'s `uuid_column_type()` gives, so check which that is before writing the join -- a form mismatch here matches nothing and reports zero held rather than failing. Write the three counters into the capacity-row upsert at `:28126-28138`, in the `values` dict beside `used_*`. **The trap, which is `D9`:** `_derive_disk_limit_gb()` is called with `used[2]` at `:28104-28108` and must keep being called with `used[2]` alone — adding held to that term raises the limit by the holds and the guard then admits them twice. Add `delta_held_*` to the reply nodes at `:28147-28163` only if the existing `delta_used_*` pattern is cheap to extend; say so either way. Hold rows whose node has no capacity row after the pass contribute to nothing and are left alone (`D8`); do not delete them and do not null their node. Tests in `test_mariadb_capacity_reconcile.py` and its live counterpart: a pass totals unconsumed holds onto the right nodes; a consumed hold contributes its residue and not its full size; a hold on a node with no row contributes nowhere and survives the pass; a pass does not overwrite counters with zero when the fold returns no rows for a node that has holds (the failure phase 0's `D8` quotes the brief's `D3` warning about); and `limit_disk_gb` is unchanged by the presence of held disk. Checks as 3a. Commit subject: `Recompute held counters from hold rows.` |
| 3g | medium | sonnet | none | **Publish held room where committed room is published.** Read `D4` and `D5` and `F6`, and the comment at `shakenfist/scheduler.py:865-874` which is the reason `D4` exists. In `_summarize_resources()` (`:676-888`) add `cpu_held`, `ram_held_mb` and `disk_held_gb` per node from the capacity row, beside `cpu_committed` (`:817`), `ram_committed` (`:845`) and `disk_committed_gb` (`:876`), zero when there is no row for the same reason the committed figures are. Net held out of `cpu_available` (`:817-819`) and `ram_available` (`:846-852`), and **do not** touch `disk_available` (`:857-864`) — `D4`. The cluster totals at `:823-824` and `:853-854` keep their `max(0, ...)` floor. The fields go in this shared payload builder, not in the bound `Scheduler.summarize_resources()` at `:1890`, whose docstring records it as retained for the test suite and called by no production code. Then check the readers: `shakenfist/external_api/admin.py` serves this payload, and `shakenfist/tests/test_external_api.py` and `test_scheduler.py` assert its keys — find every assertion over the per-node dict and extend rather than replace it. Tests: the three figures appear per node; a node with no capacity row reports zero and the row-present flag already says why; `cpu_available` and `ram_available` fall by the held amount while `disk_available` does not; and the cluster totals do not go negative. Checks as 3a. Commit subject: `Publish held room in the resource summary.` |
| 3h | medium | sonnet | none | **Document the holds in the places that describe the ledger.** Read `D1`, `D2`, `D4`, `D6`, `D8` and `D10`. In `docs/developer_guide/subsystem_internals.md`, beside the "The peak high-water mark" passage (`:377`) that the claim table's docstring points at, add the holds section: what a hold row is, that it is ground truth and the three node counters are derived, how a consumed hold's residue works, and that `held_*` is recomputed and never adjusted. In `docs/developer_guide/database_internals.md`, **widen the "Never guard with an aggregate" rule at `:311-320`**, which step 3c's review found does not cover the guard this phase adds: it names a conditional `INSERT ... SELECT` and a `SUM()` over rows locked `FOR UPDATE`, and carves out a subquery reading *one* row, so an aggregate subquery inside the guarded single-row `UPDATE` falls in a gap. Add it as a third form that is permitted, with the reason -- the guard and the write are inseparable, so serialisation comes from the single row's own lock -- and with what was measured: probe-then-compare let 8 of 8 concurrent bookings take one claim's whole allowance, the subquery form let exactly 1 land, on 11.8 with snapshot isolation on and on 10.11 with it off. Say that the evidence is 8-way concurrency and not the original benchmark's scale. Also, the passage at `:351-356` says "Three tables" and names the canonical lock order as `cluster_capacity`, then `namespace_claims`, then `scheduler_node_capacity`: make it four tables, with the holds table's schema version, and record `D3`'s position for it -- last, after all three -- because that order is the deadlock-freedom contract and a reader who cannot find the new table in it will guess. In `docs/operator_guide/scheduler.md`, extend the admission-guard passage to say the node guard is `used + held + request <= limit` and that the published per-node figures now include held room, and say plainly that no operator-facing way to create a hold exists yet (`D6`) — an operator who reads about held room and cannot find the verb needs that sentence. Do not name a plan or a phase number anywhere outside `docs/plans/`; the `Check documentation cites no plan phase numbers` hook enforces it. Leave `AGENTS.md` and `ARCHITECTURE.md` alone: a new table in an existing subsystem is not a new component and no convention changed. Checks: `pre-commit run --all-files`, with `Check documentation links and anchors resolve` passing. Commit subject: `Document shape holds and the held counters.` |
| 3i | medium | sonnet | none | **Prove the new assertions bite.** Use the `mutation-check` skill's method over steps 3a-3g only. Each mutation is applied by replacement from a pristine copy held **outside** the worktree and restored in a `finally` — never with `git checkout` on a directory, which has discarded uncommitted work here before — and each runs under `timeout 900`, one at a time, never beside a `pre-commit` run. Run the tests with `python -B` so a same-size mutant restored within a second cannot leave live bytecode behind. At least these: drop `held_*` from `_claim_node()`'s `WHERE` so the guard ignores holds; drop the `COALESCE` from `D3`'s sum subquery so a claim with no holds fails its own guard; change `_derive_disk_limit_gb()`'s first argument at `mariadb.py:28104` from `used[2]` to `used[2] + held[2]` (`D9` — this one matters most); make the reconciler's fold use the hold's full size instead of its residue; make the shrink release the *oldest* hold first instead of the newest; remove the floor from the release decrement so a counter can go negative; net held out of `disk_available` in `_summarize_resources()` (`D4`, which should fail a test that asserts it is not netted); and remove one column from `SCHEDULER_NODE_CAPACITY_HELD_COLUMNS` so the migration skips it. Record which test failed and on which assertion for each, and compare the tree byte-for-byte against the pristine copy afterwards. A mutation no test catches is a finding: report it with the test that should have caught it and do not write the test yourself. Change no file under `shakenfist/`. Commit subject: none — the management session records this in this plan's Results. |

## Risks and mitigations

**`R1`. The migration skips a column on an upgraded database, and
nothing notices until the guard reads a column that is not
there.** This is the `namespace_claims` v2 failure mode the
existing `test_namespace_claims_types` guard was written for: the
migration walks a named tuple, so a column added to the table
definition and not to the tuple is created on a fresh database and
absent on an upgraded one. *Mitigation:* step 3a copies that
guard for `SCHEDULER_NODE_CAPACITY_HELD_COLUMNS`, step 3i mutates
a column out of the tuple and the phase is not done until a test
fails on it, and the live suite runs against a real server at
10.11. The management session checks the guard test exists and
fails for the right reason, not that it passes.

**`R2`. The pre-filter and the guard disagree, and a burst is
attracted to a node that refuses all of it.** Exactly issue 3636,
one dimension further on. *Mitigation:* `D7` puts the held term in
both, step 3e's brief asks for a test that asserts the two agree
on the same row rather than testing each separately, and `D11`
records the one dimension where they still differ along with why.

**`R3`. Phase 4 ships a reachable producer of holds before phase 5
fixes the refusal contract, so a held refusal tells callers to
retry something no retry can clear.** `capacity_guard` is in
`TRANSIENT_CAPACITY_STAGES` (`shakenfist/constants.py:338-356`),
and the comment there already anticipates exactly this for claim
denials: once a denial means "quota exhausted" rather than
"momentary shortage", it "must be split out of this set at that
point rather than silently inheriting it". A hold is the same
shape of fact — it survives every retry until its claim releases
it. This phase is safe because of `D6`: nothing a caller can reach
writes a hold row, so no production refusal can be caused by a
hold before phase 5 lands. Phase 4 is not safe on that reasoning,
because advertising `A`'s capability is what makes holds
reachable. *Mitigation:* the planning commit adds a prerequisite
sentence to the master plan's phase 4 bullet — the split lands no
later than the capability, in phase 4 if phase 5 has not landed —
so the next planner meets it before writing phase 4 rather than
after. The decision between "phase 4 carries the split" and "swap
phases 4 and 5" is the master plan's and is left to it.

**`R4`. The sum invariant's subquery reintroduces the
`ER_CHECKREAD` failure the admission path was redesigned to
avoid.** A correlated subquery inside the guarded `UPDATE` is
safe; a separate `SELECT SUM(...)` before it is not, and the
difference is invisible in a unit test against SQLite-shaped
mocks. *Mitigation:* `D3` states the form and the reason, step
3c's brief sends the agent to the two comment blocks that record
the original diagnosis, the step asserts the compiled `WHERE`
contains the subquery through `_compiled()` the way the existing suite asserts
`ordered_values()`'s compiled order, and the concurrency case is a
live test against a real MariaDB, which is the only place it can
appear at all; see the execution notes for what CI actually runs.

**`R5`. The reconciler's hold fold joins on the wrong uuid form
and silently totals zero.** Pitfall 6 in the existing comment
block, which cost a reconciler pass once already: dashed versus
undashed uuid storage across `object_references`, `object_states`
and `instances`. A join that matches nothing returns no rows,
which the fold reads as "this node holds nothing", which
overwrites the counters with zero. *Mitigation:* step 3f's brief
names the pitfall and the comment block; the step's test list
includes "a pass does not overwrite counters with zero when the
fold returns no rows for a node that has holds", which is the
assertion that catches a silently empty join rather than a
syntactically broken one.

**`R6`. This phase's code is unreachable in production for a
release, so a defect in it is found by phase 4 rather than by
operation.** The deliberate consequence of `D6`. *Mitigation:*
accepted rather than mitigated, and stated here so phase 4's
planner treats this phase's arithmetic as unproven in the field
even though it merged. The live MariaDB suite is what stands in
for production exposure, which is why 3c, 3d and 3f each carry a
live test rather than unit tests alone.

## Definition of done

1. `namespace_claim_holds` exists with `D1`'s columns and all
   three indexes, is in `EXPECTED_SCHEMA_VERSIONS` at version 1,
   and `ensure_schema()` names its ensure function. `grep -c
   "_ensure_namespace_claim_holds_schema" shakenfist/mariadb.py`
   is at least 2 — the definition and the `ensure_schema()` call.
2. `scheduler_node_capacity` is at version 2, the three `held_*`
   columns are in the table definition and in
   `SCHEDULER_NODE_CAPACITY_HELD_COLUMNS`, and a test asserts the
   tuple and the definition name the same set — so a column added
   to one and not the other fails rather than diverging silently.
3. An upgrade path is exercised, not only a creation path: a test
   creates the table at version 1, runs the ensure function, and
   asserts the three columns exist with `NOT NULL DEFAULT 0`.
4. The node guard tests `used + held + request <= limit` on all
   three dimensions. `_claim_capacity_guard()` is byte-identical
   to its state at `0b0271ef64`, and so is the claim branch of
   `_direct_admit_instance_placement()` — `git diff` over
   `mariadb.py` shows no line inside either.
5. No stage name was minted or changed. `git diff` over
   `shakenfist/constants.py` and `shakenfist/scheduler.py` touches
   no stage string, and `TRANSIENT_CAPACITY_STAGES` and
   `PERMANENT_CAPACITY_STAGES` are unchanged.
6. A node denial names the held term: a test asserts the `node`
   stage's dimension detail carries `held` and that `exceeded`
   recomputes the guard's own test against `used + held`.
7. The pre-filter and the guard agree. A test builds one capacity
   row with non-zero `held_*` and asserts that
   `_has_sufficient_cpu()`, `_has_sufficient_ram()` and the
   admission guard reach the same verdict for the same shape --
   the guard's side read from its compiled `WHERE` through
   `_compiled()` and the dispatching fake connection
   `shakenfist/tests/test_mariadb_capacity_admission.py:49` and
   `:103` already provide. Disk is exempt and `D11` says so.
8. `limit_disk_gb` does not move when held disk exists. A test
   reconciles a cluster with held disk and asserts
   `limit_disk_gb` equals what the same cluster produces with the
   holds removed. Step 3i's `used[2] + held[2]` mutation fails
   this test.
9. The reconciler recomputes rather than adjusts: a test writes
   deliberately wrong `held_*` values, runs a pass, and asserts
   they are corrected from the hold rows; and a second test
   asserts a consumed hold contributes its residue.
10. Every release path is covered by a test that checks both the
    hold rows and the node counters: claim delete, expiry sweep,
    and shrink. The shrink test asserts newest-first order and
    that the floor stayed at `used_*`.
11. `_summarize_resources()` publishes `cpu_held`, `ram_held_mb`
    and `disk_held_gb`; `cpu_available` and `ram_available` are
    net of held room and `disk_available` is not. A test asserts
    the negative case for disk explicitly, so `D4` cannot be
    undone by a later tidy-up without a test failing.
12. No REST route, no capability token, no client method. `git
    diff` touches no file under `shakenfist/external_api/` except
    `admin.py`'s serialisation of the summary payload if that
    proves necessary, and `API_CAPABILITIES` is unchanged.
13. Step 3i reports at least eight mutations, every one caught,
    with the failing test and assertion named for each, and the
    worktree byte-identical to its pristine copy afterwards.
14. Proto stubs were regenerated with `tox -e genprotos` and
    committed; no proto field number was reused.
15. `tox` and `pre-commit run --all-files` pass from the
    repository root, and `tools/check-plan-status.py` agrees with
    the index after the close-out commit.
16. The live MariaDB suite passes against a real server for the
    three steps that carry one (3c, 3d, 3f), run with `--serial`.
    A step whose live test could not be run says so rather than
    being reported as passing.
17. The master plan corrections this phase's planning commit made
    are in place and are not re-litigated by a later step: the
    phase 3 section says which table carries the held counters
    (`F2`), the phase 4 bullet carries `R3`'s prerequisite, Future
    work carries `D11`, and phase 0's `D8` carries a pointer to
    `D4`'s amendment.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

Two gates in particular, because both are cheap to propose and
expensive to redo:

- **Before step 3a writes any DDL**, state the final column list,
  types and index names you intend to create, and wait. A schema
  is the one thing in this phase that a later phase cannot change
  without a migration of its own.
- **Before step 3c writes the claim transaction**, state the
  statement order and the exact form of the sum invariant, and
  wait. `D3` decides both, and getting the invariant's form wrong
  is a concurrency defect that only a live test against a modern
  MariaDB can see.

## Results

To be completed as the phase executes.

## Execution notes

**2026-11-21 (AEDT).** The back brief was answered before step 3a
ran, and all four answers are recorded here because later steps
depend on them.

1. **The DDL is approved as `D1` specifies it**, including the
   denormalised `namespace` column and `D2`'s nullable
   `instance_uuid` as the whole of the consumption state. Two
   alternatives were put and declined: dropping `namespace`, and
   carrying an explicit `state` column.
2. **The sum invariant takes `D3`'s form** -- a correlated scalar
   subquery in the claim `UPDATE`'s own `WHERE`, with `COALESCE`.
   The two alternatives put and declined were a
   `SELECT ... FOR UPDATE` over the hold rows first, which is the
   shape the `ER_CHECKREAD` invariant forbids, and materialising
   `held_*` on the claim row, which phase 0's `D8` rejects.
3. **The live suite runs against both 10.11 and 11.8**, each in a
   disposable container, never against a real deployment.
4. **This session may push the branch and open the pull request**
   once the Definition of done is met.

**`R4` was wrong about CI, and has been corrected.** It said the
concurrency case could only appear in a local live run "since CI
runs 10.11 where `innodb_snapshot_isolation` is off". The 10.11
figure is the *floor* `ensure_schema()` is defended against, and
the version the cluster functional tests exercise through their
Debian 12 guest images; the live MariaDB tests are a separate job
which runs on a `debian-13` runner
(`.github/workflows/functional-tests.yml:586`, installing the
distribution's `mariadb-server` through
`tools/ci-install-mariadb.sh`), so they exercise 11.8 and therefore
run with `innodb_snapshot_isolation` ON. The comment above
`EXPECTED_SCHEMA_VERSIONS` (`shakenfist/mariadb.py:695`) says so in
terms, and the live claims suite's own docstring says the
`ER_CHECKREAD` invariant "is only observably necessary against a
server with `innodb_snapshot_isolation` ON (the default from
MariaDB 11.6.2)" and reports which regime each run used. So `D3`'s
concurrency behaviour is covered by CI as well as locally. The
decision to run both versions stands unchanged -- 10.11 is the
floor the migration has to work on and 11.8 is what CI's live lane
actually uses -- but it is belt and braces rather than the only way
to see the failure.

**Step 3a found two registration sites its row did not name**, both
mandatory, and both named by the comment above
`EXPECTED_SCHEMA_VERSIONS`: `register_all_tables()` must call the
new getter or `RegisterAllTablesTestCase` fails, and
`EXPECTED_TABLE_NAMES` in
`shakenfist/tests/test_mariadb_schema_concurrency.py` must gain the
table or two drift guards fail. A schema step's brief in a later
phase should name all four sites -- the table definition,
`EXPECTED_SCHEMA_VERSIONS`, `ensure_schema()` and
`register_all_tables()` -- plus that test constant.

**Step 3a declined `mock_mariadb.py`**, which its row offered
conditionally, and the reason is right: the only place that mock
enumerates capacity columns is `set_node_capacity()`, whose dict is
returned verbatim as the *read reply*, so adding `held_*` there
before the real accessor returns it would make the mock advertise
fields production does not have. Step 3b owns that read path and
adds it in both places at once.

**Step 3b, and a defect in its own row.** The row told the agent to
confirm the regenerated stubs were *staged*, which contradicts the
standing instruction that a step leaves its work uncommitted for the
management session to stage. The agent did the right thing -- staged
nothing, and reported the contradiction instead of guessing -- and the
row has been corrected to ask that the files be named as changed.

**The release request's shape was the step's own design, and was
changed in review.** The `3b` row did not specify the request and
reply messages, so the agent chose them. For releasing it chose
"an empty `hold_uuids` releases every hold the claim has", and that
sentinel is now an explicit `bool all_holds` with an empty-and-unflagged
request refused. proto3 cannot tell a repeated field left unset from
one explicitly empty, and release is destructive -- it gives back room
a holder is relying on -- so under the sentinel a caller whose list
went missing asks for every hold on the claim, in a request identical
to the one it meant to send. It is also the ambiguity
`UpdateNamespaceClaimRequest`'s explicit `fields` mask exists to avoid.
The guard is in `_direct_release_claim_holds()` rather than the
servicer, because the release paths step 3d writes run inside the
database tier and call the direct function; a servicer-only guard
would be bypassed by exactly the callers that release in bulk.
**Step 3d must keep that guard** and give `all_holds` its two real
callers, claim delete and the expiry sweep, while the shrink path
names the holds it releases one by one.

**A `degraded` flag was considered for the holds read and declined.**
`GetSchedulerNodeCapacityReply` and `GetClusterCapacityReply` both
carry one, so an empty holds list that really meant "could not be
read" looked like `R5`'s failure mode in another guise. It is not:
the list convention in this tier signals failure by raising, which
`_grpc_get_namespace_claims()` states in terms, and the new
`GetNamespaceClaimHolds` handler sets `grpc.StatusCode.INTERNAL`
accordingly. `degraded` exists only on the two reads the scheduler
makes on the placement hot path, where failing open rather than
refusing is a live decision. **Step 3c's gRPC and public wrappers must
follow the raising convention**, not return an empty list on failure.

**Step 3c, and the documented rule it collided with.**
`docs/developer_guide/database_internals.md:311` says "Never guard
with an aggregate", which on its face forbids `D3`. It does not, and
the rule's own reasoning is why. It names two designs -- a conditional
`INSERT ... SELECT`, which overshot and committed twice the limit
under READ COMMITTED, and a `SUM()` over rows locked `FOR UPDATE`,
which livelocked into thousands of deadlocks under REPEATABLE READ --
and both share one cause: the guard and the write are *separable*, so
two transactions can each satisfy the guard before either writes.
`D3`'s form is neither. The aggregate is a sub-expression of a
single-row `UPDATE`'s `WHERE`, so serialisation comes from the claim
row's own lock, and InnoDB re-evaluates that `WHERE`, subquery
included, against the row as committed by whoever held the lock
first. The rule's closing carve-out covers a subquery reading *one*
row, which is `_demand_guard_clause()`, so this third form sat in a
gap rather than on either side of the line. Step 3h now owns widening
that sentence.

What settles it is a measurement rather than the argument, taken with
the benchmark's own method: replacing the subquery with
probe-then-compare let **8 of 8** concurrent bookings take one claim's
entire allowance, while the subquery form let **exactly 1** land. That
is `R4`'s failure reproduced and then closed. Two residual limits are
recorded rather than glossed. The evidence is 8-way concurrency, which
is representative of the known callers -- the conductor books about ten
shapes a pass -- but is well short of the benchmark's scale, so the
rule has not been re-derived at scale. And the theoretical deadlock
path is not closed by argument: the subquery scans the holds index
while the same transaction later inserts into it. It is retried (1213
is in `_TRANSIENT_TRANSACTION_ERRNOS`) and nothing livelocked across
four concurrency tests, which is the distinction the benchmark drew
between deadlock-free and livelock.

**Three judgement calls step 3c made for itself, all kept.**
1. **A node with no capacity row refuses a booking**, where admission
   fails open. The inversion is right: an admission records something
   that has already happened, so refusing it would deny reality,
   while a booking is a promise and an unguarded promise is worse
   than a refused one -- which is `_direct_create_namespace_claim()`'s
   own reasoning for a missing singleton.
2. **The create path's copy of the guard raises rather than
   refusing.** A fresh uuid4 has no holds, so a miss there is a broken
   invariant and not a capacity answer, and reporting it as a refusal
   would need a reason token that means nothing to a caller.
3. **Four refusal reasons were minted** -- `claim_capacity`,
   `node_capacity`, `not_found` and `not_active` -- of which the last
   two are already mapped in `auth.py`'s `CLAIM_REFUSAL_STATUS` (404
   and 409) and in `CLAIM_REFUSAL_MESSAGE`. This note previously said
   none of the four was mapped, which was wrong; step 3h checked the
   constant and corrected it. The two genuinely new tokens are
   `claim_capacity` and `node_capacity`. Harmless while `D6` keeps
   every route out of this phase, and **phase 4 must map those two
   when it adds the API**, or a booking refusal reaches a caller as an
   unmapped token.

**An earlier execution note was wrong.** It said each of steps 3c, 3d
and 3f must fix *its own* live fixture. Adding the invariant to the
claim *create* path broke 36 tests in the existing claims live suite,
whose fixture also lacked the table; step 3c fixed both. A step that
touches a shared path has to fix every fixture that path runs under,
not only the one it is adding.

**Live results for step 3c.** On 11.8 at snapshot isolation ON, 218
tests across every `test_mariadb_*_live` module passed with no
`ER_CHECKREAD` exhaustion. On 10.11 at snapshot isolation OFF, the
holds module's 23 tests passed. Both runs report their regime in the
suite's own output, as the live claims suite does.

**Step 3d, and the one schema change this phase made after its
gate.** Step 3d found that `D1`'s "most recently booked first" was
only true to the second: `created_at` was a plain `DATETIME` stamped
with `NOW()`, so holds booked in the same second tied and the order
fell back to uuid, which is arbitrary. The agent's first live test
asserted *which* holds a shrink released and failed on exactly that.
The operator was asked, because the DDL had been approved at a gate
and the table had not shipped, and chose microsecond precision: the
two timestamp columns are now `DATETIME(6)`. `D1`'s DDL above is
amended accordingly, and `NAMESPACE_CLAIM_HOLDS_VERSION` stays at 1,
because version 1 has never existed in any database and there is no
prior shape to migrate from.

The change had three ways to fail silently and all three were checked
against a real server before it was made:
1. **The column type alone is a no-op.** `NOW()` inserted into a
   `DATETIME(6)` column stores `.000000`, verified on 11.8, so the
   column would advertise precision it never received and the
   ordering would stay coarse with nothing visible to notice it by.
   Every statement that stamps a hold uses `NOW(6)` through one
   helper; the booking INSERT is the only such statement, and the
   other three capacity tables keep plain `DATETIME` and `NOW()`
   because nothing orders their rows against each other. A test pins
   that separation.
2. **`sa.func.now(6)` does not work.** It renders `now(%s)`, and
   MariaDB will not take the fractional-seconds precision as a bind
   parameter, so the fsp has to be a literal.
3. **`NOW(6)` had to exist on the floor, not only on 11.8.** It does,
   on 10.11.19, which is what the supported-version comment in
   `mariadb.py` exists to protect.
`UNIX_TIMESTAMP()` carries the fraction through, so the published
`created_at` orders the same way the column does; without that the
API would have exposed whole seconds for microsecond-ordered rows and
two readers would have disagreed about which hold is newest.
`test_the_hold_timestamps_really_carry_microseconds` fails if either
half of the change is undone.

**How step 3d made the shrink safe, which is a pattern this phase had
not used before.** The release set must be computed from a probe
*outside* the transaction, because the hold rows cannot be read inside
it before the first DML without the `ER_CHECKREAD` exposure `D3`
avoids. A stale probe sounds unsafe and would be, except that the
claim `UPDATE`'s own `WHERE` re-validates the probe's *conclusion*
against committed state: `new_limit >= sum(holds excluding the
released uuids)`. A booking that lands in the window is inside that
sum, so the statement matches nothing and the whole attempt
re-probes. It validates the answer rather than the inputs, which is
the same property `D3` rests on and the same pattern
`_direct_update_namespace_claim()` already uses for its limit deltas.
`_claim_hold_sum_guard()` gained a literal `limit=` for this, which is
necessary rather than tidy: the statement is changing `limit_*`, and a
column's new value is not visible in its own `WHERE`.

**Two test counts for the live suite, and both are right.** Step 3d's
report put the live suite at 249 tests where the management session
counted 244, and the difference is the selector, not an error. The
regular expression CI uses, at `tools/ci-enum-widening-test.sh:89`, is
`shakenfist\.tests\.test_mariadb_.*_live\.` -- the trailing dot
anchors the match on a module name, so it selects the live modules and
nothing else. Dropping the dots selects by test name as well, which
picks up five more tests in other modules that merely have `live`
somewhere in their names, among them
`test_mariadb_error_on_key_liveness_read_writes_nothing` and
`test_live_call_is_served`. Every figure in these notes reconciles on
that offset of five. Both of the management session's runs executed
244 with zero skips: 244 on 11.8.9 with `innodb_snapshot_isolation` 1,
and 244 on 10.11.19 with it 0. **Quote the selector with the count**,
because the number alone does not say what was counted.

**Step 3e nearly shipped the bug its own test was written to catch,
and that is the finding of this step.** `R2` asks that the pre-filter
and the guard agree, because a pre-filter which disagrees attracts a
burst to a node that then refuses all of it. The step's first
agreement test compared **whole-statement verdicts**, and it passed
with `held_cpus` deleted from the guard: the memory clause refused in
all the same cases, so the cpus defect was invisible behind it. That
is not an exotic coincidence -- memory is usually the binding
dimension on these nodes, so the masking is the expected case rather
than bad luck. The test now resolves and evaluates **one dimension at
a time**, and its new form was proved to bite by three mutations, each
failing with a named case: held removed from the guard's cpus clause,
from `_has_sufficient_cpu()`, and from the ram ledger sub-check.

The lesson generalises and belongs with `R2`: **an agreement test that
compares verdicts can be satisfied by a coincidence, and only a
per-dimension comparison means anything.** The management session's
brief for this step asked for the agreement to be asserted "directly"
and that was not specific enough; a later phase asking for the same
property should ask for it per dimension.

**A row defect: the `held` key could not be optional work.** The `3e`
row said `_capacity_dimension()` "gains an optional `held` key"
without mentioning the wire, but the servicer builds replies with
`reply.dimensions.add(**dimension)`, so a dict key with no proto field
behind it raises. The field and a regeneration were mandatory, not a
judgement call, and the row has been corrected.

**The live test count is settled, and the management session's
reading of the disagreement was wrong.** Step 3e reported the live set
at 252 where the module-anchored selector finds **247** with its three
new live tests in the tree, and a full run executed 247 with zero
skips. The session took the run of disagreements -- 248 from step 3c,
249 from 3d, 252 from 3e -- for three consecutive miscounts and said
so in the commit messages for steps 3d and 3e. It was not that. Each
pair differs by exactly the five tests the loose selector adds, so the
two sets of figures agree on every step; the cause was findable in one
`stestr list` and the session reached for agent error instead. **A
disagreement wants an explanation that accounts for both figures**
before it is called a mistake. The numbers: 244 before step 3e and 247
after, module-anchored as CI counts them, or 249 and 252 counting by
test name.

**Two extras from step 3e, both kept.** `_hold_denial_dimensions()`,
which step 3c wrote, also reports `held` now, so a booking refusal and
an admission refusal describe a node in the same shape rather than two
-- worth having, since phase 4 will put both in front of the same
caller. And six existing scheduler predicate tests that hand-build
partial capacity rows were fixed to carry `held_*` rather than being
accommodated with `.get()`, which keeps the filters loud about a read
path that forgot to carry it.

**Operational note.** Both disposable MariaDB containers stopped
between steps -- they were started without a restart policy and did
not survive whatever restarted the daemon. A step found them down and
started the one it needed. Anything later that needs them should check
rather than assume, and the final Definition of done pass should
confirm both are up before reading anything into a live result.

**A flake, recorded because an unrecorded flake is
indistinguishable from a new one.** Step 3e's first full
`pre-commit run --all-files` failed one hook,
`Test the Kerbside cluster config`, with
`FAIL [case 5: rows equal to both variables]: ansible-playbook
failed` and an **empty** stub `sf-ctl` log -- the stub produced no
output at all, so the playbook appears to have died before doing
anything rather than computing a wrong answer. It is not this
phase's: the same hook passes on an unmodified `develop`, and it
passed on a re-run in this worktree with this diff in place, twice.
Nothing in this phase touches what that hook reads. The most likely
cause is resource contention -- both disposable MariaDB containers
were up and the 247-test live suite had just finished when that run
started, so `ansible-playbook` forked into a busy machine -- but that
is a hypothesis consistent with the evidence and not a diagnosis. It
is written down because this will eventually appear in CI, where an
`ansible-playbook failed` with an empty log and no diff-related cause
is exactly the shape of failure that gets misattributed to whichever
pull request is in the queue at the time.

**Out of scope, found by step 3e and left alone:**
`cluster_ci_tests/test_saturation.py:1016`'s docstring describes the
node ledger as `max(measured_cpus, committed_cpus)`, which stays true
while nothing holds and is wrong once something does. It is a cluster
CI test's docstring rather than production code or operator
documentation, so step 3h should decide whether it is in its remit and
say so either way.

**Carried to step 3f:** the live fixtures do not know
about the new table. Each of those steps must add
`_ensure_namespace_claim_holds_schema` to its fixture's ensure list
and `namespace_claim_holds` to `TEST_TABLES`. That is also where
the real `CREATE TABLE` first executes against a server, rather
than being asserted as rendered DDL as step 3a asserts it.

### Step 3f, the reconciler fold

**The `R5` warning named the wrong mechanism, and the step was right
to ignore it.** The `3f` row told the step that the hold-side join
"needs the same `REPLACE` treatment" the `object_references` and
`object_states` joins carry. It does not.
`uuid_column_type()` is `sa.Uuid(native_uuid=False)`, which is the
undashed `CHAR(32)` form -- the same form `instances.uuid` stores --
so `h.instance_uuid = i.uuid` compares like with like and a `REPLACE`
there would have been a no-op on the data and misleading as
documentation, pointing a later reader at a conversion that is not
happening. Only the two reference tables store the dashed
36-character form. `R5`'s *failure mode* is untouched by this: a join
that matches nothing still reports zero held and overwrites the
counters, and the step proved its test bites by mutating the claim
join to match nothing, which failed nine tests, seven of them live.
The row has been corrected to ask that the form be checked rather than
asserting what it is.

**The fold is clamped per hold, not per node.** A hold whose
consuming instance was resized larger than the hold would otherwise
contribute a negative residue and quietly offset another hold's
booking on the same node, so each hold's contribution is
`GREATEST(0, size - consumed)` before the `SUM`. Per-node clamping
would have let the offset through.

**`D9`'s trap has a test that bites.** Mutating the
`_derive_disk_limit_gb()` call site from `used[2]` to
`used[2] + held[2]` failed exactly the two
`limit_disk_gb_is_unchanged_by_booked_disk` tests and nothing else,
which is the narrow signal that the assertion is about that term
rather than incidentally sensitive to it.

### Step 3g, publishing held room

**The step netted held in before the maximum, not after, and that is
the right reading of `D4`.** The row asked that held be netted out of
`cpu_available`. The step instead charged the node
`max(measured, committed + held)`, which is verbatim what
`_has_sufficient_cpu()` does, and that function's docstring says it
charges "whichever of the two is larger, which is the same arithmetic
summarize_resources() publishes". Step 3e taught the pre-filter about
holds and left that sentence false; this form makes it true again.
Subtracting after the maximum would have left the published figure
and the pre-filter disagreeing on every node where the measurement
binds, which is `R2`'s concern one layer out, in the figure an
operator reads rather than the one admission applies.

**Three mutations, re-run by the management session.** Netting held
out of `disk_available` fails exactly
`test_summarize_resources_does_not_net_held_out_of_disk`; dropping
held from the cpu term and from the ram term each fail the netting
test and the cluster-totals test. The disk test books 40 GB of held
disk on the node it asserts over, which is what makes it bite: with
held disk at zero it would pass whether or not the netting existed.

### Step 3h, the documentation

**The step read the code rather than this plan, and caught two false
claims in it.** Both were this session's.

`D10` and the `3h` row describe the held counters as "recomputed and
never adjusted". That is not what was built. Booking increments
`held_*` in its guarded `UPDATE` and release decrements it with a
floor; the reconciler recomputes from the hold rows and overwrites
whatever it finds. The thing that never touches `held_*` is
admission, which charges `used_*`. The documentation says the latter.
The recompute-not-adjust principle still holds where it matters -- the
reconciler never reads a counter to derive it -- but the blanket form
of the sentence was wrong and would have been copied forward.

The execution note on the refusal reasons said none of the four was
in `CLAIM_REFUSAL_STATUS`. `not_found` (404) and `not_active` (409)
are in it, and in `CLAIM_REFUSAL_MESSAGE` as well. Only
`claim_capacity` and `node_capacity` are new. The note has been
corrected; as written it would have had phase 4 map twice the tokens
it owes.

**What the review corrected in the step's own work.** The holds
section said a hold ends in "exactly three ways" and then described a
fourth two paragraphs later: there are four callers of
`_release_hold_rows()` -- an explicit release, claim delete, the
expiry sweep, and a shrink. The count is now four, with a sentence
saying all four share the helper, which is why the counters come back
the same way however a hold ended. A rewrap had also left a CI
docstring line ragged, and a new paragraph had run into the sentence
after it.

**Checked against the tree, not taken on report:** holds last in the
canonical order (`_release_hold_rows()` decrements node capacity in
uuid order and deletes the hold rows after it), 1213 in
`_TRANSIENT_TRANSACTION_ERRNOS`, the pre-filter's `committed + held`
and its live-memory term, and `NamespaceClaim.book_holds()` as the
only reachable booking path.
