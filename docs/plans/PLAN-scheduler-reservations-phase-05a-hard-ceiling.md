# Scheduler reservations phase 5a — the calibration re-run and the hard ceiling

## Prompt

Phase 5 landed three of its four steps as PR #4800 (merge
`660d4f65e7`, 2026-11-05) and deliberately held the fourth: the
advisory→hard flip of `CLAIM_ENFORCEMENT_HARD`, gated on
plan-blocker b13, the re-run of phase 4c's observation
measurement. Phase 5's branch merged and was deleted, so the
"step 3 lands as a later commit on this branch" the Execution
notes describe is no longer available. This phase carries the
flip instead.

Before executing any step, read phase 4c's *Observations*
section and its "Does phase 5 flip `CLAIM_ENFORCEMENT_HARD`?"
answer — that record is the baseline this phase's measurement
is compared against, and its three "what is still missing"
items are this phase's entry conditions. Read the admission
primitive (`shakenfist/mariadb.py`'s
`_direct_admit_instance_placement`), the capacity stage
constants (`shakenfist/constants.py`), the create path's
exhaustion branch (`shakenfist/external_api/instance.py`) and
the cluster CI claim tests
(`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`).
Ground every claim in what the code does today; this plan's own
survey found seven of phase 5's step 3 premises wrong.

## Planning effort

**High.** The flip changes a refusal contract that three
components already publish and one consumes, it inverts a
cluster CI test's assertion, and its gate is a measurement whose
decision rule has to be fixed before the data is read rather
than after. Phase 5's own step 3 brief, written at high effort
three days ago, has seven stale or wrong premises — recorded in
*What the survey found* — which is the measure of how much this
area has moved.

## Scope

**In scope:**

1. Pin the measurement window's start from the conductor's own
   data, and open it.
2. Pre-register the numeric decision rule for the flip, and read
   the window against it exactly as written.
3. Populate `claim_uuid` on the admission-denial path, which
   D16's refusal body needs and which today is set only on the
   admitted path.
4. If, and only if, the rule says flip: set
   `CLAIM_ENFORCEMENT_HARD = True`, publish a distinct claim
   refusal stage, classify it as not retry-worthy, and map a
   claim refusal to HTTP 403 with the D16 body.
5. Invert the cluster CI test that today asserts an over-claim
   create succeeds.
6. Update the documentation that says the ceiling is advisory.
7. Close out phase 5 and this phase.

**Out of scope, and why:**

- **Changing how the conductor sizes claims.** If the rule says
  hold, the defect is in private-ci's sizing and the fix belongs
  to `PLAN-claim-coverage-and-sizing.md`, which owns that code.
  This phase stops and says so rather than widening.
- **Reconsidering `CLAIM_EXACT_MIN_RUNS`.** Exact sizing is
  private-ci's decision (its phase 3d), shipped on at 5 runs.
  This phase measures its cost under enforcement and reports it;
  it does not change it.
- **The anti-starvation policy** (D18's 15-minute rule). Phase
  4c established the population and not the outcome, and said
  the policy keeps its own phase. Unchanged here.
- **Delegated claim creation** (D15) and **publishing
  private-ci**. Phase 4c future work, untouched.
- **Phase 9's push audit.** It runs after this phase and reads
  this phase's merges among the rest.
- **Claims for the remaining unclaimed namespaces.** The survey
  found this already discharged; see F8.

## What the survey found

Seven of phase 5 step 3's premises are wrong or stale, two of
them load-bearing. Corrections were made at source in the master
plan's phase 5 section, its Execution table and the `index.md`
row as part of this phase's planning commit, and phase 5's own
step 3 row is marked superseded by this plan rather than having
its line numbers repaired — maintaining two copies of the same
brief is how they diverge.

**F1 — every line number in step 3's brief has drifted, because
step 1 of the same phase moved the code.** `CLAIM_ENFORCEMENT_HARD`
is at `mariadb.py:27715`, not 27461. The
`result['admitted'] and result['claim_over_limit']` branch is at
`instance.py:1232` with `_event_claim_over_limit()` at
`instance.py:1253`, not ~1182. The claim guard inside
`admit_instance_placement` is at `mariadb.py:28889`
(`claim_guarded = enforce and CLAIM_ENFORCEMENT_HARD`), not
28541-28640. Step 1's extraction of `place_on_candidates()` is
what moved them, in the same pull request.

**F2 — the capacity stage sets have moved out of the API layer,
so step 3(d) names the wrong file.** `TRANSIENT_CAPACITY_STAGES`
and `PERMANENT_CAPACITY_STAGES` now live in
`shakenfist/constants.py:241` and `:268`;
`external_api/base.py:141-142` only re-exports them. The comment
in `constants.py` explains why: a preflight abort records the
same classification on the instance (issue 4568) and
`instance.py` cannot import the API layer. Editing
`external_api/base.py:135-148` as the brief says would change
nothing.

**F3 — there is no claim stage to split out of the transient set;
one has to be created.** This is the first load-bearing finding.
`CAPACITY_GUARD_STAGE = 'capacity_guard'` (`constants.py:220`) is
the *single* stage every guard denial publishes — node, cluster
and claim alike — and it is that one constant which sits in
`TRANSIENT_CAPACITY_STAGES` (`constants.py:257`) with a comment
saying claim denials "must be split out of this set at that
point". So step 3(d) is not a frozenset edit: a distinct stage
has to be introduced, published where the claim guard is the
refusing one, and classified. The guard that makes this
enforceable already exists:
`tests/external_api/test_capacity_error.py:229-241` walks
`scheduler.py` for stage literals and asserts every published
stage is in one of the two sets, so a new stage classified into
neither fails a test — but note its second assertion, which
pins `classified - stages` to exactly `{CAPACITY_GUARD_STAGE}`
and will need the new name added.

**F4 — by the time the create path refuses, there is no single
exception to map, and the claim guard's node-independence is what
makes a 403 well defined.** This is the second load-bearing
finding, and it is the one step 3(c) is quietest about.
`external_api/instance.py:1261-1277` refuses *after* the walk has
tried every candidate, holding a `denials` dict rather than one
`CapacityAdmissionDenied`, and its comment says so: "There is no
single stage here — denials may span 'cluster', 'claim' and
'node' across different candidates (D30)". What rescues the
mapping is an invariant of the primitive rather than of the
caller: the claim guard is statement (2) in
`_direct_admit_instance_placement`, before either node guard, and
`mariadb.py:28880-28886` records why it is node-independent — "a
claim's limits are namespace-denominated and node-independent, so
a missing node capacity row says nothing about whether the
namespace has exceeded what it claimed". A claim refusal
therefore aborts the transaction on *every* candidate before any
node guard runs, in both passes of the demand re-walk, so
"every denial is at the claim stage" is the only shape a claim
refusal can take. That is the discriminator, and it is provable
rather than heuristic. See D5.

**F5 — `claim_uuid` is not populated on the denial path, and
D16's body names the claim.** `AdmitPlacementResult.claim_uuid`
exists and is documented as "the claim this admission was charged
against … so an event recorded on the namespace can name which
claim it was", but it is assigned only at `mariadb.py:29120`,
inside the advisory read-back on the *admitted* path. The
`_AdmissionDenied` handler at `mariadb.py:29170-29174` sets
`failing_stage` and `dimensions` and returns, leaving
`claim_uuid` at the `''` default from `mariadb.py:28205`. A
refusal body that must name the claim cannot, today. See D4.

**F6 — the advisory doc line list is 9 lines, not 10, and two of
them are unrelated.** Running phase 8's own item-12 grep
(`grep -rn -i 'advisory' docs ARCHITECTURE.md --include='*.md'`,
less `docs/plans/` and `docs/components/`) returns nine lines.
Seven are claim-ceiling statements: `docs/glossary.md:63`,
`docs/operator_guide/scheduler.md:456`, `:460` and `:522`,
`docs/developer_guide/subsystem_internals.md:312`,
`docs/operator_guide/database.md:932`,
`docs/release_notes/v07-v08.md:1459`, plus
`ARCHITECTURE.md:212`. That is eight. The remaining two,
`docs/developer_guide/ci.md:15-16`, use the word about CI lint
lanes and must not be touched. `subsystem_internals.md:337` also
describes the flip without using the word and belongs on the
list.

**F7 — phase 4c's three entry conditions are all met, and two
were met by a different mechanism than it named.** Phase 4c said
the flip needed a sizing input tracking real use, a `CI_SIZES`
floor covering a cluster job's first run, and a re-run.

- *Sizing input:* done, and done well.
  `private-ci conductor/db.py:4911`'s `get_claim_sizes()` now
  takes, per row, the larger of `claim_peak_*` — the cluster's
  own high-water mark on the claim, which is `peak_used_*` from
  phases 4 and 7a — and the conductor's reconstruction from
  instance lifetimes. That directly addresses the 2.12x–5x
  under-statement phase 4c measured, because the ledger mark
  cannot sit below the instances it marked.
- *The floor:* addressed by a different mechanism. Rather than
  raising `CI_SIZES`, private-ci's phase 3a added `s-cloud` and
  `s-cluster` size tokens (`conductor/provisioner.py:103-108`) so
  a cloud or cluster job no longer shares a combination key — and
  therefore a claim size — with every small job, and
  `db.py:4998` added an all-history MAX so a new job does not
  fall to the bare floor. The problem phase 4c named is solved;
  the solution is not the one it proposed.
- *The re-run:* this phase. The last sizing change is
  `2ee760d` ("conductor: seed new-job claims from repo max"),
  merged as private-ci#287 on 2026-11-04.

The fourth condition, which phase 4c added in passing — that
`PLAN-transient-capacity-refusals.md` land first, or the re-run
cannot tell a warm-up artefact from genuine contention — is also
met: that plan is Complete, 7 of 7.

**F8 — the claim population is real now, and phase 4c's
"claims for `ci-images` and the static runners" future-work item
has been discharged.** Phase 4c closed with four of nine
namespaces holding no claim, and recorded that every claim which
had ever existed was created and deleted by the same test. Read
on sfcbr on 2026-11-07, every namespace but `system` holds a
live, active claim: nine conductor-provisioned `sfcbr-*` runner
namespaces, plus `ci-images` (8 cpus / 8192 MB / 200 GB),
`static-ci` (8 / 12288 / 100) and `static-runners`
(32 / 65536 / 1200). The measurement finally has a population to
measure.

**F9 — exact sizing is on, and it puts three claims at exactly
100% of their ceiling.** `CLAIM_EXACT_MIN_RUNS = 5`
(`private-ci conductor/provisioner.py:426`), so a dimension whose
windowed peak is a plateau over at least five runs is claimed at
`max(floor, peak)` **with no `CLAIM_HEADROOM`** — private-ci's
phase 3d D3, whose commit message says in terms that it left "the
enforcement question deferred to the flip". That question arrives
here. The same sfcbr reading shows the consequence: the
`sfcbr-0Hpf3Mq7KE5jIVPy` runner claim stood at 8/8 cpus and
16384/16384 MB, and `static-ci` and `static-runners` stood at
100% of all three dimensions. Under hard enforcement a dimension
at exactly its claim has no tolerance by construction, so the
plateau test's false-positive rate becomes a CI outage rate. See
D3, which gives the exact arm its own threshold, and R2.

Nothing else in phase 5's step 3 was found wrong: the flip is
still one constant, the admission primitive does return
per-dimension `limit`, `used`, `requested` and `shortfall` on a
denial (`mariadb.py:28107-28119`), `CapacityAdmissionDenied`
does carry `failing_stage` of `'claim'` and those dimensions
(`exceptions.py:158-188`), and `place_instance()` does raise it.

## Decisions

**D1 — this is a new lettered phase, and phase 5 closes as
Complete on what it landed.** Phase 5's branch is merged and
deleted, so its own plan's "step 3 lands as a separate commit on
this branch" cannot happen. The alternative to a new phase is a
row that reads `In progress` forever with no branch behind it.
Phase 5 therefore closes as Complete against `660d4f65e7`
(#4800) — which is what the Execution table's `Merged` column
means, "what put this phase on `develop`" — and a status note
records that its step 3 moved here rather than being dropped.
The lettered-suffix shape matches 00a, 04a, 04b, 04c, 07a and
07b, every one of which was inserted for the same reason: work
the original cut put inside a phase turned out to need its own.

**D2 — the decision rule is pre-registered here, before the
window is read, and it is read exactly as written.** Phase 4c
named the number to watch — "over-limit admissions per day, which
must fall to a residue" — but never said what a residue is, which
leaves the threshold to be chosen by whoever reads the data, after
seeing it. `PLAN-transient-capacity-refusals`'s phase 5 is the
cautionary tale and the precedent: it pre-registered a rule, found
the data unreadable, and wrote its D43 amendment *before* reading
the second window specifically because "amending a pre-registered
rule after seeing the data is what pre-registration exists to
prevent". The rule, against phase 4c's seven-day baseline of 234
over-limit admissions across 101 namespaces:

| Measure | Phase 4c baseline | Flip requires |
|---|---|---|
| Over-limit admissions per day | 33.4/day (234 in 7 days) | ≤ 3.3/day |
| Namespaces with any over-limit admission | 101 | ≤ 10 |
| Namespaces where one instance exceeded the whole claim | 24 | 0 |
| Over-limit admissions on an *exact* dimension | not measured | ≤ 1/day |
| `conductor_claims_failed_total`, claim-logic failures only (amended, see below) | 0 | 0 |
| Claims created in the window (readability floor) | 2038 | ≥ 500 |

Three outcomes, and no others. **Flip** if every row is
satisfied. **Hold** if any is not: the phase records which, files
the defect against `PLAN-claim-coverage-and-sizing.md` in
private-ci, and stops — it does not renegotiate a threshold.
**Unreadable** if the readability floor fails, in which case the
window extends once and the rule is read again unchanged; a
residue cannot be measured on a thin denominator.

**Amended on 2026-11-07, two days into the window and before any
of it was read, with Michael's agreement.** The failure row now
counts only `failed` rows attributable to claim logic. Step 1 found
that `claim_events` holds three `failed` rows in its entire history
-- 2026-09-22, 2026-10-22 and 2026-10-25 -- and that all three are
sf-api reachability errors: two nginx 502s and a refused
connection, none of them a statement about whether a claim was
sized accurately. Left literal, the row would hold the flip on one
unrelated 502, and the under-cloud redeploy which restarts sf-api
is a known source of those. A `failed` row whose `detail` is a
reachability error is therefore recorded in *Observations* with its
detail text and excluded from this row, with the exclusion stated;
anything else still fails it. The amendment is written here rather
than applied at reading time because that is the whole discipline
D2 inherited from `PLAN-transient-capacity-refusals`' D43: a rule
may be amended before the data is seen and not after. None of the
three rows falls inside this window, so the amendment changes no
number today -- which is exactly why it was safe to make now.

*The 10x reduction is the number a reviewer is most likely to
argue with*, so here is the reasoning rather than the figure
alone. Zero is the wrong target: an exceedance is admitted
whenever a namespace's real footprint moves above what it was
observed to need, and claims are sized from observation, so some
exceedance is a property of the method and not a defect. The
right target is the rate at which an operator can treat each
exceedance as an individual question — read the namespace's
events, see what grew, grow the claim. Three a day is readable
that way and thirty-three is not; at phase 4c's rate the
exceedances were a population to be explained statistically,
which is exactly how the measurement defect hid inside them. The
single-instance-exceeds-claim row is 0 rather than a rate
because it is structural: a namespace whose claim cannot hold one
of its own instances is not over-consuming, it is mis-declared,
and hard enforcement would refuse it forever rather than
transiently.

**D3 — the exact-dimension arm is measured and gated separately,
at a tighter threshold.** A dimension claimed exactly has zero
tolerance by construction (F9), so an over-limit admission on one
measures the plateau test's false-positive rate, and under
enforcement each becomes a refused CI job with no operator remedy
short of changing the declaration. One a day is the threshold
because that is roughly the rate at which a human notices and
acts; it is deliberately stricter than the aggregate rate even
though exact dimensions are a subset of it. The split is readable
because private-ci's phase 3d added a
`claim_events.exact_dimensions` column and a
`conductor_claim_exact_groups{dimension}` gauge for exactly this
purpose. If this row alone fails, the fix is private-ci's —
raise `CLAIM_EXACT_MIN_RUNS`, or give an exact dimension a small
headroom — and this phase holds.

**D4 — `claim_uuid` is populated on the denial path, from the
lookup the denial already performs, and it lands ungated.** F5's
gap has to be closed for D16's body to name the claim. The
`_AdmissionDenied` handler already calls
`_admission_denial_dimensions(denial.stage, namespace, …)`, which
resolves the namespace's active claim row to build the
per-dimension triples, so the uuid is in hand and no second query
is needed. Two properties follow and are worth testing: a result
carrying `failing_stage == 'claim'` and an empty `claim_uuid` is
a contradiction and must be impossible, and the field must stay
empty for a `'node'` or `'cluster'` denial, because its docstring
says to read the name for absence rather than a zero. This step
is ungated and lands before the window closes: the claim guard
cannot refuse while `CLAIM_ENFORCEMENT_HARD` is `False` — with
it unset the claim `UPDATE` carries no guard clause and matches
its row — so the code is unreachable until the flip, which costs
nothing to land early and shortens the gated critical path. Its
test must force the stage through `mock_mariadb` rather than
waiting for the flip.

*Corrected on 2026-11-07 by step 3, which proved this decision's own
sourcing instruction wrong.* D4 said to take the uuid from the claim
row `_admission_denial_dimensions()` resolves. That read happens
**after** the rollback and reports what is true now, which is right
for a measurement and wrong for an identity: after a
delete-and-create it names a different claim from the one that
refused, and if the claim expired in between it names none at all
— which would make "`failing_stage == 'claim'` with an empty
`claim_uuid`" reachable, the very contradiction D4 asked to be proved
impossible. The uuid therefore rides `_AdmissionDenied` instead, from
the local the raising branch already holds. That branch is
`elif claim_uuid is not None`, so the invariant holds by construction
rather than by assertion, and no second query was added either way.
D4's two testable properties are unchanged, and both are tested.

**D5 — the 403 is published when every denial in the walk is at
the claim stage, and that is the only shape a claim refusal can
take.** F4's invariant, not a heuristic: the claim guard runs
before either node guard and is node-independent, so it refuses on
every candidate or on none. The create path's exhaustion branch
therefore inspects the `denials` dict it already holds and, if
every entry's failing stage is `'claim'`, publishes a 403 naming
the claim; any other mixture keeps today's 507. A test asserts
both directions, including the mixed case, because a mixed
refusal published as a claim refusal would tell an operator to
grow a claim that is not the constraint. The alternative — having
`place_on_candidates()` re-raise the first claim denial — was
rejected because it would put a second placement-outcome channel
beside the return value that phase 5 step 1 just finished
consolidating.

**D6 — the claim stage goes in neither classified set, and the
default carries it.** `capacity_refusal_is_transient()` returns
False for a stage in neither set, and documents that as the safe
direction. That is the correct answer for a claim refusal, which
is genuinely a third kind: not transient, because no wait clears
namespace quota exhaustion, but not permanent in
`PERMANENT_CAPACITY_STAGES`'s documented sense either — that set
is for a request the cluster "would refuse … after every instance
on it had been deleted", and deleting the namespace's instances
is precisely what *does* clear a claim refusal. Putting it there
would make the set's own docstring false. The implementer must
read `test_capacity_error.py:229-241` first and report back if
its `classified - stages` assertion forces a set: if it does, the
test is the thing to amend, with a comment naming the third kind,
not the classification.

**D7 — the cluster CI test's advisory assertion is inverted in
place, not deleted.**
`test_placements_draw_down_the_claim_and_report_exceedance`
(`cluster_ci_tests/test_namespace_claims.py:732`) asserts today
that an over-claim create reaches state `created` and that the
advisory audit event follows. After the flip both halves are
false and the test fails in merge CI. It is inverted rather than
removed because what it proves is still worth proving — that the
ceiling is read at all, which is the thing "advisory mode which
records nothing is indistinguishable from no claim at all" was
written to catch — so it becomes an assertion that the create is
refused with 403 and a body naming the claim. The
`CLAIM_OVER_LIMIT_MESSAGE` event path stays reachable and tested:
a *move* still charges nothing on the namespace side, and an
unguarded admission still admits over the limit.

## Step plan

All implementation is done by sub-agents per the execution model;
the management session reviews each against the files, not the
summary. Steps 1 to 3 are ungated. Steps 4 to 6 do not start
until step 2 records **Flip**.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | low | management session | none | **Pin the window and open it.** Establish when the last claim-sizing change reached sfcbr, from the conductor's own data rather than from the merge — phase 4c pinned its window start from the first row of `claim_events` and not from its PR, and said why. The change to look for is `2ee760d` ("conductor: seed new-job claims from repo max", private-ci#287, merged 2026-11-04), running under `CLAIM_EXACT_MIN_RUNS = 5`. The conductor is on `maui`; read `conductor.db` or `/api/state` per the access note in this repository's plans, and pin the start at the first `claim_events` row written by the deployed sizer (its `exact_dimensions` column is populated, which a pre-3d sizer cannot do). Record the pinned start, the close at start + 7 days, and how it was pinned, in this plan's *Observations*. Then record the soak with `plan-blockers` so the wait is visible, and clear b13's phrasing to name this window rather than "re-run the measurement". Seven days matches phase 4c exactly, which is the point: the two readings are only comparable if the windows are. **Corrected on 2026-11-07 while running: `exact_dimensions` pins phase 3d's deploy (2026-10-24), not #287's, so it is the wrong discriminator and was not used. The start was pinned instead from the deployed `provisioner.py`'s mtime and the md5 of the two sizing functions on the host, which hash identically to `2ee760d`. See *Observations*.** **Done: the window runs 2026-11-05 07:47:02 to 2026-11-12 07:47:02 AEDT.** |
| 2 | high | management session | none | **Read the window against D2's rule.** Collect, for the pinned window: claims created and deleted; every `placement admitted over namespace capacity claim` audit event on sfcbr with its dimension and its namespace; the same split by whether the exceeded dimension was exact (`claim_events.exact_dimensions`, D3); the count of namespaces in which a single instance exceeded the whole claim; `conductor_claims_failed_total`; capacity refusals per day; the distribution of claim size against measured peak per `(repo, job_name)`; and whether the reconciler reported drift in `cluster_capacity.claimed_*`. The tooling exists — private-ci's `tools/measure-claim-accuracy.py` and `tools/compare-claim-sizing.py`, and `peak_used_*` on each claim through `sf-client namespace claim show` — so prefer it to fresh SQL, and say which tool produced each number. Write the record into *Observations* beside phase 4c's figures in the same units, then read it against D2's table **as written** and record Flip, Hold or Unreadable with the arithmetic shown. If Hold, name the failing rows, file the defect against `PLAN-claim-coverage-and-sizing.md` in private-ci, and stop. Note that the installed `sf-client` on the development host predates phase 7b's `claim list --all`, so an `--all` sweep needs the editable client-python checkout. |
| 3 | medium | opus | worktree | **Name the claim on a denial.** In `shakenfist/mariadb.py`, the `_AdmissionDenied` handler (`:29170-29174`) sets `failing_stage` and `dimensions` and returns, leaving `claim_uuid` at the `''` default from `:28205`; populate it for a `'claim'` denial from the claim row `_admission_denial_dimensions()` already resolves, without adding a second query — read that function first (`:28658`) and reuse what it looks up rather than calling `_namespace_claim_row()` again. Carry the value over the wire too: `claim_uuid` is already on the RPC (`:29455` reads it back, `daemons/database/main.py:2901` writes the admitted path's), so the denial reply must set it on the same field. Tests: a forced `'claim'` denial through `tests/mock_mariadb.py` reports the charged claim's uuid (the stage is unreachable until step 4, so it must be forced — see D4); a `'node'` and a `'cluster'` denial both leave it empty; and `failing_stage == 'claim'` with an empty `claim_uuid` is asserted impossible. This lands ungated. Commit subject: `Name the claim a denial was charged against.` **Corrected on 2026-11-07 while running: the wire path already carried the field for every outcome — `daemons/database/main.py:2901` sets `claim_uuid` on the reply it builds whatever the result, and `mariadb.py:29455` reads it back unconditionally — so “the denial reply must set it on the same field” asked for a change to code that already did it. Nothing was edited there; a round-trip test pins it instead, and gating the reply field on `admitted` kills that test. The commit subject landed as `Name the claim a denial was weighed against.`, because a denial charges nothing — which is the whole distinction the change rests on.** |
| 4 | high | opus | worktree | **GATED ON STEP 2 RECORDING FLIP.** **The enforcement contract.** Three changes, one commit. (a) `CLAIM_ENFORCEMENT_HARD = True` at `shakenfist/mariadb.py:27715`, which makes `claim_guarded` (`:28889`) true and lets `_AdmissionDenied('claim')` (`:28971`) fire for the first time. (b) Add `CAPACITY_CLAIM_STAGE = 'capacity_claim'` to `shakenfist/constants.py` beside `CAPACITY_GUARD_STAGE` (`:220`), in neither classified set per D6, and rewrite the `CAPACITY_GUARD_STAGE` comment at `:249-256` — which instructs this very change — to record that it happened. Read `tests/external_api/test_capacity_error.py:229-241` before editing: it pins `classified - stages` to exactly `{CAPACITY_GUARD_STAGE}` and will need the new name accounted for; if it forces the new stage into a set, amend the test with a comment naming the third kind rather than mis-classifying the stage, and say so in the back brief. (c) In `shakenfist/external_api/instance.py:1261-1277`, where the walk has exhausted its candidates and holds a `denials` dict, publish 403 when *every* denial's failing stage is `'claim'` and keep today's 507 otherwise (D5, and read F4's invariant before deciding you need something cleverer). The 403 needs a sibling to `api_base.capacity_error()` in `external_api/base.py` — read that function's docstring first, because it explains why the body is built by mutating `sf_api.error()`'s response rather than by passing fields in, and the same constraint applies here — carrying `{error, status, stage, transient: false, claim, dimensions}` with the per-dimension `limit`, `used` and `shortfall` the denial already provides, and **no** `Retry-After`. The `enqueue_delete_due_error()` stage recorded on the instance becomes the new stage on this path (issue 4568's classification). Tests: an all-claim refusal returns 403 with the body; a mixed claim-and-node refusal still returns 507; the 403 carries no `Retry-After`; and `capacity_refusal_is_transient(CAPACITY_CLAIM_STAGE)` is False. Commit subject: `Make namespace claim ceilings binding.` **Added on 2026-11-07 by step 3, which found this gap while closing its own: `CapacityAdmissionDenied` (`exceptions.py:158-188`) carries `failing_stage` and `dimensions` and no claim uuid, so the `denials` dict the create path holds cannot name a claim even now that the primitive reports one. Threading it through that exception belongs to this step, not to step 3.** |
| 5 | high | opus | worktree | **GATED.** **Invert the cluster CI assertion.** `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py:732`, `test_placements_draw_down_the_claim_and_report_exceedance`, asserts at `:775-779` that an over-claim create reaches state `created` and at `:781-789` that the advisory audit event follows. Both are false after step 4 and this is a cluster CI test, so leaving it would break merge CI on every platform. Invert it per D7: the create must be refused, with 403 and a body naming the claim and the exceeded dimension. Keep the test's existing rigour — it builds a real claim, places within it, then exceeds it, and its failure messages explain what was being proven; preserve that shape and its `_claim_limits()`/footprint helpers. Check the other three tests in the file for advisory assumptions too (`test_claim_lifecycle_and_refusals:446`, `:672`, `:835`) and fix any that the flip falsifies. Then grep the unit suite for the same assumption and fix each: `tests/test_ci_headroom_report.py:1845`, `tests/external_api/test_capacity_error.py:108`, `tests/test_instance.py:3091`, `tests/test_mariadb_capacity_admission.py:333` and `:726`, `tests/test_mariadb_capacity_admission_live.py:779`, each of which carries a comment conditioning its assertion on the constant being `False`; several then no longer need their `mock.patch.object(mariadb, 'CLAIM_ENFORCEMENT_HARD', True)` wrapper, which is the tidy half of the same change. Commit subject: `Assert a claim ceiling now refuses.` |
| 6 | medium | sonnet | none | **GATED.** **Documentation.** Eight lines say the ceiling is advisory, and F6 lists them: `docs/glossary.md:63`, `docs/operator_guide/scheduler.md:456` (a section heading, "Advisory this release: exceedances are recorded, not refused"), `:460` and `:522`, `docs/developer_guide/subsystem_internals.md:312` and `:337`, `docs/operator_guide/database.md:932`, `docs/release_notes/v07-v08.md:1459`, and `ARCHITECTURE.md:212`. Do **not** touch `docs/developer_guide/ci.md:15-16`, which uses the word about CI lint lanes. Each must now say the ceiling is enforced, name the 403 and what an operator does about one (grow the claim by the shortfall the body reports), and no two pages may state the refusal's shape differently — `shortfall`'s definition is already recorded once, by phase 7, so point at it rather than restating it. `ARCHITECTURE.md`'s line changes because enforcement becoming binding changes how placement refuses, which is the shape of the system; keep it to the same one or two sentences rather than expanding it, per the documentation policy. The release notes line is a record of what v0.8 shipped — check whether it describes the release or the present, and if the release, leave it and say so in the back brief rather than falsifying history. Commit subject: `Document the binding claim ceiling.` |
| 7 | medium | sonnet | worktree | **Close-out.** Write this plan's *Outcome*: what each step did, the window's figures, which D2 row was closest to failing, and anything left as future work. Set this phase's Execution row to Complete. Phase 5's own row and the `index.md` count were already corrected by this phase's planning commit, so check them rather than editing them. Clear the plan-blocker soak and b13. Per the documentation policy `AGENTS.md` needs no change here — this changes no convention — so if you find yourself editing it, stop and say why. Commit subject: `Record what the hard ceiling cost.` |

## Risks and mitigations

- **The window is read, says Hold, and the phase has spent six
  days to land one commit.** That is the correct outcome in that
  case rather than a failure — the advisory window exists to find
  exactly this — but it is worth planning for. *Mitigation:*
  step 3 is ungated and lands regardless, and step 2's Hold
  branch is required to file the defect against the plan that
  owns the sizing code, so the information is not lost. The
  management session does not renegotiate D2's thresholds.
- **Hard enforcement refuses CI jobs on exact dimensions, where
  the operator has no remedy.** Three claims were observed at
  100% of their ceiling (F9), and an exact dimension has no
  tolerance by design. *Mitigation:* D3 gives the exact arm its
  own tighter threshold, read before the flip rather than after,
  and names private-ci as the owner if it fails. The management
  session reads that row specifically, because the aggregate rate
  can pass while this one fails.
- **The hand-sized `static-ci` and `static-runners` claims are at
  100% and are not conductor-managed.** `conductor/staticrunners.py`
  contains no claim handling and their expiries (2026-11-30,
  2026-12-01) are weeks out rather than hours, so they were
  declared by hand. Under enforcement neither fleet can gain one
  instance. *Mitigation:* step 2 reports their drawdown against
  their ceilings as part of the record, and the remedy is a
  declaration change outside this repository — which is cheap,
  but only if it is known about before the flip rather than
  discovered by a refused create.
- **A mixed refusal is published as a claim refusal, and tells the
  operator to grow a claim that is not the constraint.**
  *Mitigation:* D5's discriminator is "every denial is at the
  claim stage", which F4 shows is the only shape a claim refusal
  takes; step 4 requires a test for the mixed case explicitly, in
  both directions.
- **The new stage lands in neither classified set and a later
  reader mis-classifies it.** *Mitigation:* D6 requires the
  reasoning in a comment at the constant, and
  `test_capacity_error.py`'s existing AST walk fails on a
  published stage it cannot account for, so the question cannot
  be silently skipped.
- **The flip lands and merge CI breaks on the cluster claim
  test.** *Mitigation:* step 5 inverts it in the same pull
  request as step 4, and the definition of done requires the
  cluster tests to have run in CI on push rather than only
  locally — phase 5's own Execution notes record that these
  tests are not locally runnable, which is how the gap would
  otherwise be missed.

## Definition of done

Falsifiable; the management session runs each.

1. *Observations* records the pinned window start, how it was
   pinned (a `claim_events` row, not a merge date), and the
   close, and the window is at least 7 days.
2. *Observations* reports every measure in D2's table, in the
   same units as phase 4c's figures, with the tool that produced
   each named; and records Flip, Hold or Unreadable with the
   arithmetic shown.
3. `grep -n "claim_uuid" shakenfist/mariadb.py` shows the field
   assigned on the `_AdmissionDenied` path as well as at
   `:29120`, and a unit test fails if a forced `'claim'` denial
   returns an empty `claim_uuid`.
4. **Steps 4-6 only, after a Flip:**
   `grep -c 'CLAIM_ENFORCEMENT_HARD = True' shakenfist/mariadb.py`
   is 1.
5. **Steps 4-6 only:**
   `python -c "from shakenfist import constants;
   print(constants.capacity_refusal_is_transient('capacity_claim'))"`
   prints `False`, and `capacity_claim` appears in neither
   `TRANSIENT_CAPACITY_STAGES` nor `PERMANENT_CAPACITY_STAGES`
   (or, if `test_capacity_error.py` forced a set, the back brief
   records which and why).
6. **Steps 4-6 only:** a unit test asserts an all-claim refusal
   returns 403 with a body carrying `claim`, a per-dimension
   `limit` and a `shortfall`, and carrying no `Retry-After`; and a
   separate test asserts a mixed claim-and-node refusal still
   returns 507.
7. **Steps 4-6 only:**
   `grep -rn -i 'advisory' docs ARCHITECTURE.md --include='*.md'
   | grep -v '^docs/plans/\|^docs/components/'` returns only the
   two `docs/developer_guide/ci.md` CI-lane lines; no page says
   the claim ceiling is advisory.
8. **Steps 4-6 only:** no test still conditions an assertion on
   `CLAIM_ENFORCEMENT_HARD` being `False` —
   `grep -rn "CLAIM_ENFORCEMENT_HARD" shakenfist/tests/
   shakenfist/deploy/` shows no comment or branch asserting the
   advisory behaviour.
9. **Steps 4-6 only:** the cluster CI claim tests have **passed
   in CI on a push**, on all three platforms, not merely locally.
   Phase 5 recorded that `cluster_ci_tests/` is not locally
   runnable; a local pass is not evidence here.
10. `tox -e py3 -- shakenfist.tests.test_mariadb_capacity_admission`
    and `shakenfist.tests.external_api.test_capacity_error` pass.
11. `pre-commit run --all-files` passes.
12. The master plan's Execution table reads Complete for phase 5
    against `660d4f65e7` (#4800) with a note that step 3 moved
    here, and `index.md`'s count agrees with the table.

## Back brief

Before executing any step, back-brief the operator on the plan
and how the intended work aligns with it. Two gates:

- **Before step 2 reads anything**, confirm D2's table is the
  rule the operator will be held to, thresholds included. It is
  cheap to agree now and worthless to agree after the data is on
  screen, which is the whole of D2's reasoning.
- **Before step 4's edit begins**, confirm D5's discriminator and
  D6's "neither set" classification. Both are contracts three
  components read, and both are expensive to change once a test
  and a documentation page assert them.

Step 2's Hold branch needs no back brief: it stops, and stopping
needs no permission.

## Observations

Phase 4c's figures are the comparison baseline and live in
`PLAN-scheduler-reservations-phase-04c-conductor-claims.md`'s own
Observations section. Step 2 writes its reading here beside them in
the same units.

### Step 1 -- the window, pinned 2026-11-07

**The window is open. It runs from 2026-11-05 07:47:02 AEDT to
2026-11-12 07:47:02 AEDT**: seven days exactly, which is phase 4c's
window length and the only reason the two readings can be compared.

The start is `claim_events` row **id 134672** -- the first claim the
conductor recorded after the deploy that put the sizer under
measurement on `maui`. Its neighbours are row 134671 at
2026-11-05 06:53:56 and the deploy's write of
`conductor/provisioner.py` at 2026-11-05 07:19:38 AEDT, so 134672 is
the first claim sized by the new code and not the last sized by the
old.

**Step 1's brief named the wrong discriminator, and this is the
correction.** It said to pin the start at the first row whose
`exact_dimensions` column is populated, on the reasoning that a
pre-phase-3d sizer cannot write that column. That is true and it
pins the wrong deploy: the first populated row is id 99440 at
**2026-10-24 18:20:32**, phase 3d's own deploy, twelve days before
the change this phase measures. Taking it would have opened the
window on a sizer that predates `2ee760d` and closed it before the
code under test had run a full day. The column marks 3d, not #287.

What was used instead is the deployed file itself, which is checkable
and which no later deploy has disturbed:

| Evidence | Reading |
|---|---|
| `conductor/provisioner.py` mtime on `maui` | 2026-11-05 07:19:38 AEDT |
| `get_claim_sizes()` in the deployed `db.py`, md5 of the function | `7eeefc28...`, byte-identical to `2ee760d` |
| `claim_size_with_exact()` in the deployed `provisioner.py`, md5 | `5208c55d...`, byte-identical to `2ee760d` |
| `CLAIM_HEADROOM` / `CLAIM_EXACT_MIN_RUNS` on the host | 1.2 / 5 |
| `repo_seeds` occurrences in the deployed `db.py` / `provisioner.py` | 7 / 2 |
| Deployed revision | `8eae682` (private-ci#297, 2026-11-07 09:49) |

Both sizing functions on the host hash identically to their
`2ee760d` versions, so the sizer measured over this window is
exactly the one #287 shipped, and the three conductor deploys since
(2026-11-06, and 2026-11-07 at 10:01 with a restart at 10:03) did
not touch it. Of the six commits that have touched
`conductor/db.py` or `conductor/provisioner.py` since `2ee760d`,
only `0859d74` ("conductor: offer periodic jobs below merge and
PR") touches `provisioner.py`, and it landed in private-ci#289 at
2026-11-05 05:56 -- before the window opened, so it is a constant
across the window rather than a change inside it. It is worth
naming anyway: it excludes periodic jobs from the starvation wait,
so it changes which jobs hold claims without changing how any
claim is sized.

The 53-minute gap in `claim_events` either side of the deploy
(06:53:56 to 07:47:02) is **not** evidence of the restart, and
should not be read as such: gaps of 20 to 250 minutes are ordinary
in this table -- 2026-11-06 alone has one of 248 minutes and one of
171. The deploy is evidenced by the file mtime, not by the silence.

**Interim readability, at 55.9 of the window's 168 hours:** 1,172
claims created and 2,457 refused on capacity. At that rate the
window will finish near 3,500 creations, against D2's readability
floor of 500 and phase 4c's 2,038. The floor is very unlikely to
bind; step 2 confirms rather than assumes it.

### Two cautions for step 2, found while pinning

**The access path in this repository's plans has rotated and the
key named in it no longer works.** `conductor.db` is still
`/srv/shakenfist/private-ci/conductor.db` on `maui`, world-readable
and in WAL mode, so it must be copied and the copy queried -- phase
4c's method, unchanged. What has changed is the key: the RSA
`id_ansible` is refused, and `ansible@maui` authenticates with
`~/.ssh/id_ansible_ed25519`. The copy for this step went to a scratch
directory and the live file was not opened. Note also that the
`ansible` user is in neither `adm` nor `systemd-journal`, so
`journalctl -u conductor.service` returns nothing from the
development host; Loki's `{job="conductor"}` is the way to read the
conductor's own log, as phase 4c did.

**`claim_events` holds three `failed` rows in its whole history, and
all three are sf-api reachability errors rather than claim defects:**
2026-09-22 (502 from nginx), 2026-10-22 (connection refused) and
2026-10-25 (502). None falls in this window, so D2's failure row
reads 0 today. The hazard is that it was one unrelated nginx 502
away from reading 1 and holding the flip for a reason that has
nothing to do with sizing -- the under-cloud redeploy that restarts
sf-api is a known source of exactly that. Raised with Michael on
the day it was found, two days into the window and with none of the
window read; **D2's failure row was narrowed accordingly**, and the
amendment and its reasoning are recorded at D2 itself. Step 2 must
still report any `failed` row it finds, with its `detail`, and say
why it was excluded if it was.

## Execution notes

**2026-11-07 (AEDT).** Back brief answered by Michael before any
step ran: D2's table binds as written; the read-only `conductor.db`
copy from `maui` was authorised; step 3 was authorised to land today
while the window soaks; and D5's discriminator and D6's "neither
set" classification were both confirmed, so step 4 needs no second
gate when it starts.

**Step 1 ran in the management session, as planned.** It corrected
its own brief's pinning discriminator -- see *Observations* -- and
the correction is recorded there rather than only here because the
pinned start is the figure step 2 depends on. The soak is recorded
with `plan-blockers` as **b17**, a time wait until the pinned close
naming the window and row 134672; **b13** was removed rather than
re-worded, because its text asked for "re-run the measurement" and
the measurement is now running against a fixed close. The plan's
standing note was replaced for the same reason (n15 removed, n18
added): it still read "phase 5 is next" and cited b13.

**One decision changed: D2's failure row was narrowed**, on
2026-11-07, two days into the window and before any of it was read.
Step 1 found the only three `failed` rows in `claim_events`' history
are sf-api reachability errors rather than claim defects, so the row
as written could have held the flip on an unrelated nginx 502. Put
to Michael the day it was found and agreed; the amendment and its
reasoning are at D2, not only here, because that is where a reader
checking the rule will look. No number changes today -- none of the
three rows is inside the window -- which is what made amending it
now honest rather than convenient.

**Step 3 ran as one opus sub-agent at medium effort in the worktree,
as the row specifies, and departed from the plan in two places** --
both corrected at source above rather than only here. It sources the
claim uuid from the exception rather than from the post-rollback read
D4 named, which is the better answer and is argued at D4; and it made
no wire-path change, because none was needed.

The management session reviewed the diff rather than the report. It
confirmed the raising branch really is `elif claim_uuid is not None`,
so the invariant is structural and not merely asserted; confirmed
that proto comments do not reach the generated stubs, so the
contract edit in `protos/database.proto` needs no regeneration;
reproduced the headline mutation (dropping the uuid from the raise
kills two tests, and the tree restored byte-identical); and replaced
one assertion. An exact-source-line match on the raise site became an
`ast` walk asserting that every claim-stage `_AdmissionDenied` call
passes two arguments, which survives re-wrapping the line and still
catches what it was written for: a second claim raise site that
forgets the uuid kills exactly one test and no behavioural test,
which was checked rather than assumed.
