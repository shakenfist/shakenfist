# Scheduler reservations phase 5b — the hard ceiling

## Prompt

Phase 5a spent its calibration window, read it on 2026-11-13 against
the decision rule D2 pre-registered, and the rule said **Hold**. The
flip of `CLAIM_ENFORCEMENT_HARD` from advisory to binding, the cluster
CI assertion inversion and the documentation did not run, and are this
phase. The row that held it was the structural one — five namespaces
admitted a single instance larger than the whole claim it was charged
to — and the cause was in another repository: private-ci sized a
combination claim for the largest *pending* job and then charged it to
whichever job GitHub dispatched.

That is fixed. private-ci's phase 3g bound `sf-e2e`, `oVirt 4.5 on
Rocky 8` and `OpenStack via Kolla-Ansible master on Debian 13` to
their own runners (shakenfist/kerbside#727, merged 2026-11-14 19:48
UTC), read the seven days that followed, and found the class gone:
zero over-limit runs fleet-wide against five in the seven days before.
It handed that over on `shakenfist/private-ci#311` on 2026-11-25.
Plan-blocker b21's two conditions — the binding phase landed and
deployed, and a fresh seven-day window available — are therefore both
met, and this phase may be planned and may read.

Before executing any step, read phase 5a's *Observations* and
*Outcome* in full: D2's table is this phase's rule unchanged, phase
4c's figures are the baseline, and 5a's step 4, 5 and 6 briefs are
where this phase's work was specified. Then read the admission
primitive (`shakenfist/mariadb.py`'s
`_direct_admit_instance_placement`), the capacity stage constants
(`shakenfist/constants.py`), the create path's exhaustion branch
(`shakenfist/external_api/instance.py`), the refusal contract's
operator page (`docs/operator_guide/capacity_refusals.md`) and the
cluster CI claim tests
(`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`).
Do not trust 5a's line numbers: every one has moved, most of them by
more than a thousand lines.

## Planning effort

**High.** The flip changes a published refusal contract — a new status
code, a new stage name, two new body fields and a classification three
components read — and it does so in a file another plan rewrote 1,900
lines of since the contract was last specified. The measurement that
gates it has to be read against a rule fixed before the data is seen,
which is a discipline rather than a procedure. Review effort: high,
for the same reasons, and because an error in the 403's body shape
reaches clients.

## Scope

**In scope:**

1. Read a fresh seven-day window against phase 5a's D2 rule, exactly
   as written, and record Flip, Hold or Unreadable.
2. If, and only if, the rule says Flip: set `CLAIM_ENFORCEMENT_HARD =
   True`, introduce `CAPACITY_CLAIM_STAGE`, classify it in neither
   transient nor permanent, thread the claim uuid through
   `CapacityAdmissionDenied` and the placement-denied audit event, and
   publish HTTP 403 with the D16 body when every candidate was refused
   at the claim stage.
3. Declare the 403 in the instance create endpoint's OpenAPI response
   list, with its own example body.
4. Invert the cluster CI test that asserts an over-claim create
   succeeds, and fix every unit test and tool whose assertion or
   output text is conditioned on the constant being `False`.
5. Rewrite the documentation that says the ceiling is advisory,
   specifying the 403 once on the refusal contract's own page.
6. Close out this phase.

**Out of scope, and why:**

- **Re-deriving or re-tuning D2.** The rule was pre-registered in
  phase 5a and amended once, before any data was seen, with Michael's
  agreement. It is read as written. A phase that reaches its own
  measurement and adjusts the threshold has not measured anything.
- **Re-reading the window phase 5a spent** (2026-11-05 to 2026-11-12).
  n24 forbids it and so does the point of the exercise.
- **Anything about how private-ci sizes or binds claims.** 3g owns
  that, it has handed over, and its own remaining work (the `Merged`
  cell for private-ci#322) is private-ci's.
- **The anti-starvation policy** (D18's 15-minute rule) and
  **delegated claim creation** (D15). Phase 4c deferred both; neither
  is touched by making a ceiling binding.
- **Splitting a *hold* stage out of `TRANSIENT_CAPACITY_STAGES`.**
  `PLAN-placeable-capacity.md` phase 4 owns that, for the same
  frozenset and for the same reason. See R2.
- **Phase 9's push audit.** It runs last, and it audits the
  documentation this phase rewrites.

## What the survey found

Phase 5a said this phase "needs no fresh survey of the enforcement
contract", and in one sense that was right: D5's discriminator and
D6's classification are intact, and the three step briefs still
describe the right work. In another sense it was wrong. Every file
reference in them has moved, and the briefs' *scope* is short by two
documentation surfaces and one OpenAPI declaration that nobody had
looked for. The contract decisions survived; the map to the tree did
not.

**F1 — every line reference in 5a's steps 4, 5 and 6 has drifted, and
`mariadb.py`'s by 1,342 lines.** The cause is
`PLAN-placeable-capacity.md`'s phase 3, which merged as #5026 and
added about 1,900 lines to `shakenfist/mariadb.py` alone. The map:

| 5a's brief says | today | what is there |
|---|---|---|
| `mariadb.py:27715` | `:29057` | `CLAIM_ENFORCEMENT_HARD = False` |
| `mariadb.py:28889` | `:30351` | `claim_guarded = enforce and CLAIM_ENFORCEMENT_HARD` |
| `mariadb.py:28971` | `:30433` | `raise _AdmissionDenied('claim', str(claim_uuid))` |
| `constants.py:220` | `:317` | `CAPACITY_GUARD_STAGE = 'capacity_guard'` |
| `constants.py:249-256` | `:344-355` | the comment instructing this change |
| `exceptions.py:158-188` | `:200-231` | `CapacityAdmissionDenied` |
| `external_api/instance.py:1261-1277` | `:1420-1437` | the exhaustion branch and its 507 |
| `instance.py:1501-1509` | `:1498-1505` | the `instance placement denied` event |
| `instance.py:1522` | `:1518` | where `place_instance()` drops the claim uuid |
| `instance.py:1410`, `:1420` | `:1402`, `:1411` | the two advisory events |
| `external_api/base.py` `capacity_error()` | `:234` | unchanged in substance |
| `test_capacity_error.py:229-241` | `:229-243` | the `classified - stages` assertion |

Only two references in the whole of 5a's steps 4 to 6 still land
where they say: `ARCHITECTURE.md:212` and
`tests/external_api/test_capacity_error.py:108`.

**F2 — nothing of this phase has shipped early.**
`CLAIM_ENFORCEMENT_HARD` is `False` at `mariadb.py:29057`, and
`CAPACITY_CLAIM_STAGE` and the string `capacity_claim` appear nowhere
in the tree outside plan files. There is no 403 path, no claim stage
and no partial flip to reconcile.

**F3 — D5's invariant is now asserted in a second, independent
place.** F4 of phase 5a argued that a claim refusal refuses on every
candidate or on none, because the claim guard is node-independent.
`exceptions.py:241-242` now says the same thing from the other
direction, in the `demand_only` property's docstring: "A denial with
any real dimension exceeded (or from the cluster or claim stage, which
have no demand term) must never be waived." Two code sites agreeing
makes D5 a property of the design rather than a reading of one
function.

**F4 — `place_on_candidates()` walks the candidate list twice, and
that cannot manufacture a mixed all-claim dict.** The second pass
(`instance.py:1663`) runs only when every refusal in the first pass
was `demand_only`, and by F3 a claim denial never is. So an all-claim
`denials` dict can only come from a single pass, and D5's
discriminator reads a dict no re-walk has disturbed. The implementer
does not need to reason about the two passes; it needs to not break
this.

**F5 — the denials dict carries three keys and no claim uuid.**
`instance.py:1646-1650` builds `{failing_stage, dimensions,
demand_only}` per candidate, and
`CapacityAdmissionDenied.__init__` (`exceptions.py:217`) still takes
two arguments. `demand_only` is a derived property, not a constructor
field, so threading the uuid means a third argument and one more key.
5a's step 4 addendum is correct as written.

**F6 — the advisory events already name the claim; the denial event
does not.** Both `placement admitted over namespace capacity claim`
events (`instance.py:1402` on the instance, `:1411` on the namespace)
carry `'claim': result['claim_uuid']`. The `instance placement denied`
event at `:1498-1505` carries `failing_stage`, `dimensions` and
`enforce` and no claim, and `place_instance()` drops the uuid at
`:1518`. This is exactly the asymmetry 5a's review round (iii) asked
this phase to close.

**F7 — the refusal contract has a dedicated operator page, and 5a's
F6 never listed it.** `docs/operator_guide/capacity_refusals.md` is
290 lines whose entire job is this contract: which refusals are
transient, whether the refusal is queued, the response body, the
`stage` field, the `transient` field, the refusal as recorded on the
instance, and `Retry-After`. It currently states that a scheduling
refusal "returns a JSON body with four fields", enumerates the stage
values exhaustively (filter names, `capacity_guard`, `unknown`), and
**mentions claims nowhere at all** — `grep -i claim` over it returns
nothing. Every one of those statements becomes incomplete at the flip:
a fifth stage, a second status code, and two body fields the page does
not know about. This is the largest single gap between 5a's step 6
brief and the work.

**F8 — the 507 is a declared OpenAPI response, so the 403 must be
declared too.** The create endpoint's `swag_from(...)` block at
`external_api/instance.py:770-808` lists each status code with its
description, an example body and, for the 507,
`{'headers': [api_base.TRANSIENT_RETRY_AFTER_HEADER]}`. The example
lives at `:273-278` as `instance_post_507_example`, with a comment
explaining that `Retry-After` cannot be expressed in the tuple format
and is named in the description instead. A 403 that is not added here
is absent from the published specification at openapi.shakenfist.com,
and `shakenfist/tests/external_api/test_openapi_spec.py` validates
that specification without being able to notice something missing.
`docs/developer_guide/api_reference/instances.md:93` also sends the
reader to the capacity-refusals page for "that response's format, its
`stage` and `transient` fields, and the `Retry-After` header", which
after the flip describes only one of the two responses.

**F9 — v0.8 is not released, so the release notes are not history.**
5a's step 6 brief said of `docs/release_notes/v07-v08.md:1459`: "check
whether it describes the release or the present, and if the release,
leave it and say so in the back brief rather than falsifying history."
The check resolves the other way. The newest tag in the repository is
`v0.8.0-rc4`; there is no `v0.8.0`. The file is the release notes for
the release *being prepared*, so "**Claim ceilings are advisory this
release.** A placement that takes a namespace past its claim is
admitted, not refused" (now `:1665-1666`) is a statement about what
v0.8 will ship, and flipping the constant makes it false. It is
rewritten, not preserved. The passage continues at `:1677` with a
status-code list for claim *creation* refusals (507 and others) which
is a different subject and stays as it is.

**F10 — F6's eight advisory lines, mapped to today's tree.** Eight
sites, nine lines, every one found:

| 5a's F6 | today | note |
|---|---|---|
| `docs/glossary.md:63` | `:64` | second clause ("still reserved against namespaces without a claim") stays true |
| `docs/operator_guide/scheduler.md:456` | `:602` | the section heading |
| `scheduler.md:460` | `:606` | |
| `scheduler.md:522` | `:668` | "the only observable difference between advisory mode working and advisory mode being absent" |
| `docs/developer_guide/subsystem_internals.md:312` | `:321` | |
| `subsystem_internals.md:337` | `:346` | **an instruction, not a description**: "do not add an enforcement knob or a 403 path ahead of it." This phase is what it was waiting for, so it records that rather than repeating it |
| `docs/operator_guide/database.md:932` | `:1075` | |
| `docs/release_notes/v07-v08.md:1459` | `:1665-1666` | two lines; see F9 |
| `ARCHITECTURE.md:212` | `:212` | unmoved |

One more in the same family that F6 missed:
`subsystem_internals.md:325` states the constant's value ("a module
constant set `False`") inside a passage describing the guard split.

**F11 — a grep for "advisory" now returns four traps, where 5a named
one.** `docs/developer_guide/ci.md:15-16` (CI lint lanes) is the one
5a's brief warned about. Three more have arrived or were never
listed: `docs/operator_guide/scheduler.md:114` and
`docs/user_guide/placement.md:73` and `:81`, all of which call the
*placement dry run's* answer advisory — a different and still-true
sense, owned by `PLAN-placeable-capacity.md`; and
`docs/developer_guide/standards.md:76-89`, about advisory plan-label
lint. None of the four changes.

**F12 — a tool tells operators the constant is `False`, and 5a's step
5 listed only its test.** `tools/ci_headroom_report.py:51` says it in
a module docstring and `:2589` prints it in the report's own output:
"These placements SUCCEEDED. CLAIM_ENFORCEMENT_HARD is False, so".
5a's step 5 named `tests/test_ci_headroom_report.py:1845`, which pins
that text, but not the text itself. The test and the tool move
together or the test fails. (*Corrected 2026-11-26 during step 3:*
this finding undercounts the tool. There are **eight** sites, not two
— lines 50, 51, 348, 738, 1717, 2589, 2590 and 2806 — and every one
was stale.)

**F13 — a code comment predicts this phase will do something it must
not.** `shakenfist/instance.py:1349-1356` explains why the advisory
over-limit event is not `log_as_error`, and ends: "Once that constant
is flipped the same condition refuses the create instead, and will be
an error because then the create does not happen." That prediction is
false under D7, which 5a confirmed with Michael: the event path stays
reachable after the flip for a *move*, which charges nothing on the
namespace side, and for an unguarded admission by a ground-truth
writer. Neither is a refused create. The event stays a warning and the
comment is corrected — see D8.

**F14 — the unit-test sites step 5 named have moved, and there are
more of them.** 5a listed six; there are ten `CLAIM_ENFORCEMENT_HARD`
sites across five test modules, plus the tool of F12:

| 5a's brief | today |
|---|---|
| `test_ci_headroom_report.py:1845` | `:1845` (unmoved) |
| `external_api/test_capacity_error.py:108` | `:108` (unmoved) |
| `test_instance.py:3091` | `:3795` comment, `:4050` patch |
| `test_mariadb_capacity_admission.py:333` | `:340` comment, `:348` and `:367` patches |
| `test_mariadb_capacity_admission.py:726` | `:760` comment, `:784`, `:840`, `:958`, `:1061`, `:1076` patches |
| `test_mariadb_capacity_admission_live.py:779` | `:1032` |
| — | `tools/ci_headroom_report.py:51`, `:2589` (F12) |
| — | `tests/mock_mariadb.py` reads the constant at call time, by design, and needs checking rather than changing |

The patch count grew because placeable-capacity phase 3 added 474
lines to `test_mariadb_capacity_admission.py`, several of which wrap
themselves in the same `mock.patch.object`.

(*Corrected 2026-11-26 during steps 2 and 3:* this enumeration is a
floor rather than a ceiling, and three classes of site are missing
from it. **One unit test it does not list went red on the flip**:
`AdmitBranchSelectionTestCase.test_the_claim_increment_is_advisory_this_phase`,
which asserts advisory behaviour by name instead of by patching the
constant. **Three live-suite tests would have gone red in merge CI**
— `test_a_claim_that_would_overflow_is_admitted_and_reported`,
`test_every_over_claim_dimension_is_reported`, and the setup of
`test_a_move_in_a_claimed_namespace_reports_nothing` — which matters
because `test_mariadb_capacity_admission_live.py` runs in CI through
`tools/ci-enum-widening-test.sh` and the `schema_enum_widening` job,
not only in a local live suite. And **one test case was green while
asserting an impossible state**: `AdmitAdvisoryClaimTestCase` expected
an `enforce=True` admission over a claim, which the primitive can no
longer produce. Grepping for the constant finds the sites that patch
it; it does not find the sites that assert what it used to mean.)

**F15 — the three facts this phase inherits from private-ci, one of
which contradicts 3g's own earlier record.** From private-ci#322 and
the handover comment on #311: the binding merged 2026-11-14 19:48 UTC;
the window 2026-11-14T19:48:13Z to 2026-11-21T19:48:13Z reads **zero**
over-limit runs across 3,738 joined runs, against five in the seven
days before, and all 118 claims for the three bound jobs record "max
over 1 pending job"; and the live claims are **exact in all three
dimensions with no `CLAIM_HEADROOM` applied** — sf-e2e 18/28672/520,
oVirt 18/28672/760, Kolla 16/24576/660. That last contradicts 3g's own
step 2 note, which recorded sf-e2e at 22/34407/624 (the headroom
multiple). A reading that goes looking for the multiplied figures will
not find them. Two consequences for this phase: the exact-dimension
arm of D2 (row 4) covers all three of these jobs, and the window
contains a fleet-wide contention episode on 2026-11-21 — see D2.

**F16 — phase 5a owes nothing.** Its row reads `Complete` against
`3797716023` (#4917) in the Execution table, `index.md` agrees at 16
of 18, the close-out landed separately as #4973, and no `05a` branch
or worktree remains. The usual close-out of the previous phase is
already done, so this phase's planning commit only registers 5b.

## Decisions

**D1 — phase 5a's D2 is this phase's rule, carried over unchanged,
including its one amendment.** Six measures against phase 4c's
seven-day baseline:

| Measure | Phase 4c baseline | Flip requires |
|---|---|---|
| Over-limit admissions per day | 33.4/day (234 in 7 days) | ≤ 3.3/day |
| Namespaces with any over-limit admission | 101 | ≤ 10 |
| Namespaces where one instance exceeded the whole claim | 24 | 0 |
| Over-limit admissions on an *exact* dimension | not measured | ≤ 1/day |
| `conductor_claims_failed_total`, claim-logic failures only | 0 | 0 |
| Claims created in the window (readability floor) | 2038 | ≥ 500 |

Flip if every row is satisfied; Hold if any is not, naming the failing
rows and stopping; Unreadable if the floor fails, in which case the
window extends once and the rule is read again unchanged. The
2026-11-07 amendment stands: a `failed` row whose `detail` is an
sf-api reachability error is recorded with its detail text and
excluded from the failure row. The rule is reproduced here so that
step 1 reads it without a second file open, not so that it can be
edited; 5a's D2 is the authority and the two must match.

**D2 — the gating window is the binding-anchored one, 2026-11-15
06:48 to 2026-11-22 06:48 AEDT, and the 2026-11-21 contention episode
is not grounds for choosing another.** This is the window b21 named,
that n24 pinned to the hour, and that the master plan records; it is
closed, readable today, and exactly seven days, which is the only
reason it can be compared with phase 4c's. A later window excluding
the episode (2026-11-22 to 2026-11-29) is available from 2026-11-29,
and plan-blocker n26 suggested this phase might prefer it. **It is
declined**, for a reason worth stating before any of the data is read
rather than after: the episode cannot flatter a single D2 row. The
1,890 claim refusals of 2026-11-21 are conductor-side
`refused_capacity` outcomes — claims the cluster would not grant —
and D2 counts none of those. What contention does to D2's rows is
raise the claims-created floor (row 6, which helps readability and not
the verdict) and, by putting more instances through more claims, raise
rows 1 to 4 if anything. The episode therefore biases *against* the
flip. Choosing a window to exclude an episode that works against you
is the choice that needs justifying; keeping it is the conservative
one. If a row fails, this phase Holds and does not then go looking for
a cleaner week — that is D1's whole discipline, and the fact that a
cleaner week exists is precisely why the temptation has to be refused
in writing now.

**D3 — the flip, the stage, the claim uuid and the 403 land in one
commit.** Carried from phase 5a's first review round, which asked for
the placement-denied event and the refusal body to "start naming the
claim together rather than one of them doing it a phase early", and
required by correctness in the other direction too: a flip without the
403 publishes a claim refusal as `capacity_guard` with `transient:
true`, telling a client to retry a request no wait can clear. The
tempting split — land the uuid threading ungated, since it changes no
client-visible behaviour — is declined on that prior decision rather
than re-argued.

**D4 — the 403 body's `dimensions` is optional, and an empty list is
meaningful.** Also carried from 5a's review round. The claim uuid
comes from the row the guarded `UPDATE` named; the per-dimension
numbers come from a read taken after the rollback. A delete-and-create
between the two gives one claim's uuid beside another's figures, and a
claim deleted or expired in between gives a uuid with no dimensions at
all. So the body must not require `limit` and `shortfall` to be
present, and where they are absent it says which claim refused and
nothing it cannot support — in particular it does not tell an operator
to grow a claim that no longer exists. An empty `dimensions` is how
the two cases are told apart.

**D5 — 5a's D5 and D6 are carried unchanged and need no new back
brief.** The 403 is published when *every* denial in the walk is at
the claim stage and the 507 is kept for any other mixture (D5); the
claim stage goes in neither `TRANSIENT_CAPACITY_STAGES` nor
`PERMANENT_CAPACITY_STAGES`, because `capacity_refusal_is_transient()`
already answers False for an unclassified stage and a claim refusal is
genuinely a third kind — not transient, since no wait clears namespace
quota, and not permanent in that set's documented sense, since
deleting the namespace's instances does clear it (D6). Michael
confirmed both on 2026-11-07 and F3 has since strengthened D5. The
back brief does not re-ask.

**D6 — the 403 is specified once, on `capacity_refusals.md`, and
every other page points at it.** F7's page is the refusal contract's
home: it already owns the body shape, the stage vocabulary and the
`transient` field, and it is where `api_reference/instances.md`
already sends readers. So the claim refusal is written there in full —
a new stage value in the `stage` section, the two extra body fields,
the 403 beside the 507, and why no `Retry-After` — and the eight
advisory lines of F10 each become a short statement that the ceiling
is enforced plus a link. This is how the done-criterion "no two pages
state the refusal's shape differently" is made achievable rather than
merely asserted: there is one page to get right and eight pointers.
`shortfall` is already defined once, at
`subsystem_internals.md:124-135`, and is pointed at, not restated.

**D7 — the release notes are rewritten.** F9 settles the question 5a's
step 6 left open. v0.8 is unreleased, the file describes what v0.8
ships, and after this phase v0.8 ships enforcement, so leaving the
line would not be preserving history — it would be shipping a release
note that is false on the day of the release. The surrounding
paragraph's other claims stay: a claim still does not cap its own
namespace by any mechanism other than the ceiling, and it still
reserves capacity from namespaces without a claim, which is what
"starves them today" means. The status-code list for claim *creation*
at `:1677` is a different subject and is untouched.

**D8 — the advisory over-limit event stays a warning, and F13's
comment is corrected rather than obeyed.** After the flip the event
fires only where the guard did not refuse, which is not a refused
create, so "will be an error because then the create does not happen"
is wrong, and `log_as_error` stays off. The comment is rewritten to
say which path still reaches it — this is the kind of stale
instruction that gets obeyed by a later reader who trusts it more than
the code.

(*Corrected 2026-11-26 during step 2:* this decision said **two**
paths still reach the event, a move and an unguarded ground-truth
write. A move reaches **neither** it nor the guard.
`shakenfist/mariadb.py`'s admission opens its cluster-or-claim section
with `if old_node_key is not None: pass`, whose comment reads "A move
is likewise not refusable at this stage: there is nothing new to
refuse" — a move changes no namespace's usage, so it charges no claim
and leaves no read-back to report. The unguarded ground-truth write is
the only path left, and D8's conclusion is unchanged.)

**D9 — the 403 is declared in the OpenAPI response list, with its own
example, and carries no `Retry-After` header entry.** F8's
declaration block is the only place the published specification learns
about a response. The 403 gets a sibling to
`instance_post_507_example` showing `error`, `status`, `stage`,
`transient: false`, `claim` and a one-entry `dimensions`, and a
description naming what an operator does about it. It does **not**
take `api_base.TRANSIENT_RETRY_AFTER_HEADER`: the whole point of the
classification is that this refusal has no wait to recommend. This
lands in the same commit as the 403 itself, because a response and its
declaration are one change.

**D10 — `tools/ci_headroom_report.py` moves with its test, in the test
step.** F12's two sites are operator-facing text, which argues for the
documentation step, but `tests/test_ci_headroom_report.py:1845` pins
the string and 5a already put that test in step 5. A tool and the test
that asserts its output belong in one commit; the documentation step
would otherwise break a test it never ran.

## Step plan

All implementation is done by sub-agents per the execution model, each
at the row's effort and model. Steps 2, 3 and 4 are **gated on step 1
recording Flip** and do not start otherwise.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|---|---|---|---|---|
| 1 | high | management session | none | **Read the window against D1, exactly as written.** The window is D2's: 2026-11-15 06:48:13 to 2026-11-22 06:48:13 AEDT (`2026-11-14T19:48:13Z` to `2026-11-21T19:48:13Z`), seven days, anchored on shakenfist/kerbside#727's merge. Collect, for that window: claims created and deleted; every `placement admitted over namespace capacity claim` audit event on sfcbr with its namespace and its exceeded dimensions; the same split by whether the exceeded dimension was claimed exactly (`claim_events.exact_dimensions`); the count of namespaces in which a *single instance* exceeded the whole claim it was charged to; every `claim_events` row with outcome `failed`, with its `detail` text, and whether D1's amendment excludes it; capacity refusals per day; and whether the reconciler reported drift in `cluster_capacity.claimed_*`. Prefer the existing tooling to fresh SQL and say which tool produced each number: private-ci's `tools/measure-claim-accuracy.py --since` and `tools/compare-claim-sizing.py`, and `tools/measure-oversized-instances.py` for the single-instance row — **note that the `--start`/`--end` arguments that let that script read a closed window are on private-ci PR #322 and not yet on `master`, so run it from that branch's worktree or wait for the merge; `--days` alone measures back from now and cannot read this window**. Data access per phase 5a's cautions, which are current: `conductor.db` is `/srv/shakenfist/private-ci/conductor.db` on `maui`, world-readable and in WAL mode, so copy it and query the copy; `ansible@maui` authenticates with `~/.ssh/id_ansible_ed25519`, not the RSA key; the `ansible` user is in neither `adm` nor `systemd-journal`, so read the conductor's log from Loki's `{job="conductor"}` and exclude `caller=metrics.go` and `caller=engine.go`. Cross-check the single-instance row against private-ci 3g's independent reading of the same window (F15: zero over 3,738 joined runs) and report any disagreement as a finding rather than reconciling it silently. Write the reading into *Observations* beside 5a's and 4c's figures in the same units, then record **Flip**, **Hold** or **Unreadable** with the arithmetic shown row by row. If Hold: name the failing rows, file the defect against the plan that owns the cause, and stop — do not renegotiate a threshold and do not go looking for a cleaner window (D2). |
| 2 | high | opus | worktree | **GATED ON STEP 1 RECORDING FLIP.** **The enforcement contract.** One commit, five parts; read D3, D4, D5 and D9 first, and F1's table instead of 5a's line numbers. (a) `CLAIM_ENFORCEMENT_HARD = True` at `shakenfist/mariadb.py:29057`, which makes `claim_guarded` (`:30351`) true and lets `_AdmissionDenied('claim', str(claim_uuid))` (`:30433`) fire for the first time. (b) Add `CAPACITY_CLAIM_STAGE = 'capacity_claim'` to `shakenfist/constants.py` beside `CAPACITY_GUARD_STAGE` (`:317`), in neither classified set per D5, and rewrite the comment inside `TRANSIENT_CAPACITY_STAGES` at `:344-355` — which instructs this very change — to record that it happened rather than to ask for it. Read `shakenfist/tests/external_api/test_capacity_error.py:229-243` before editing: it pins `classified - stages` to exactly `{CAPACITY_GUARD_STAGE}`; the new name must be accounted for there, and if the only way to satisfy it is to force the stage into a set, amend the test with a comment naming the third kind instead and say so in the back brief. Also correct `shakenfist/external_api/base.py:223-228`, whose comment describes this split in the future tense. (c) Thread the claim uuid through the denial path: `CapacityAdmissionDenied` (`shakenfist/exceptions.py:200-231`) takes a third constructor argument, `place_instance()` stops dropping `result['claim_uuid']` at `shakenfist/instance.py:1518`, `place_on_candidates()`'s denials dict (`:1646-1650`) gains the key beside `failing_stage`, `dimensions` and `demand_only`, and the `instance placement denied` audit event at `:1498-1505` gains `'claim': result['claim_uuid']` to match the two advisory events at `:1402` and `:1411`. Do not disturb the `demand_only` property (`exceptions.py:232-247`) or the second walk it feeds (F3, F4). (d) In `shakenfist/external_api/instance.py:1420-1437`, where the walk has exhausted its candidates and holds `denials`, publish 403 when *every* denial's failing stage is `'claim'` and keep today's 507 otherwise (D5). The 403 needs a sibling to `api_base.capacity_error()` in `external_api/base.py:234` — read that function's docstring first, because it explains why the body is built by mutating `sf_api.error()`'s response rather than by passing fields in, and the same constraint applies — carrying `{error, status, stage, transient: false, claim, dimensions}` with whatever per-dimension `limit`, `used` and `shortfall` the denial provides, **optional per D4**, and no `Retry-After`. The stage recorded on the instance by `enqueue_delete_due_error()` becomes the new stage on this path (issue 4568's classification). (e) Per D9, declare the 403 in the create endpoint's `swag_from` response list at `:770-808` with its own example beside `instance_post_507_example` (`:273-278`), and no `TRANSIENT_RETRY_AFTER_HEADER` entry. Tests: an all-claim refusal returns 403 with the body; a mixed claim-and-node refusal still returns 507; the 403 carries no `Retry-After`; `capacity_refusal_is_transient(CAPACITY_CLAIM_STAGE)` is False; a claim-stage denial with an empty `dimensions` still names its claim and recommends nothing; and the placement-denied event names the claim. Verify `shakenfist/tests/mock_mariadb.py` still produces a claim denial carrying a uuid now that the constant it reads at call time is True — check it rather than changing it. Commit subject: `Make namespace claim ceilings binding.` |
| 3 | high | opus | worktree | **GATED.** **Invert the cluster CI assertion, and sweep every site conditioned on advisory.** `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_namespace_claims.py`, `test_placements_draw_down_the_claim_and_report_exceedance` (`:732`), asserts at `:775-779` that an over-claim create reaches state `created` and at `:781-789` that the advisory audit event follows. Both are false after step 2, and this is a cluster CI test, so leaving it breaks merge CI on every platform. Invert it per 5a's D7: the create must be refused, with 403 and a body naming the claim and the exceeded dimension. Keep the test's rigour and shape — it builds a real claim, places within it, then exceeds it, and its failure messages explain what was being proven — and keep its `_claim_limits()` and footprint helpers. Check the other three tests in the file for advisory assumptions (`test_claim_lifecycle_and_refusals:446`, `test_claim_appears_in_cluster_listing:672`, `test_claim_creation_migrates_existing_drawdown:835`) and the `:820-833` assertion that an exactly-fitting instance carries no event, which stays true. Then fix every unit site in F14's right-hand column: `tests/test_ci_headroom_report.py:1845`, `tests/external_api/test_capacity_error.py:108`, `tests/test_instance.py:3795` and `:4050`, `tests/test_mariadb_capacity_admission.py:340`, `:348`, `:367`, `:760`, `:784`, `:840`, `:958`, `:1061`, `:1076`, and `tests/test_mariadb_capacity_admission_live.py:1032`. Several no longer need their `mock.patch.object(mariadb, 'CLAIM_ENFORCEMENT_HARD', True)` wrapper, which is the tidy half of the same change; removing a wrapper that is now the default is in scope, changing what a test asserts is not. Per D10, `tools/ci_headroom_report.py:51` and `:2589` move in this commit too, because `test_ci_headroom_report.py:1845` pins the printed text. Do not touch `shakenfist/tests/mock_mariadb.py`'s call-time read. Commit subject: `Assert a claim ceiling now refuses.` |
| 4 | medium | sonnet | worktree | **GATED.** **Documentation.** Per D6, start with `docs/operator_guide/capacity_refusals.md` (F7) and write the claim refusal there in full: `capacity_claim` in the `stage` section's list beside `capacity_guard` and `unknown`; the body section's "four fields" corrected, with `claim` and `dimensions` described and `dimensions` noted as sometimes absent and why (D4); the 403 beside the 507, with what an operator does about one — grow the claim by the shortfall the body reports — and why it carries no `Retry-After`. Point at `docs/developer_guide/subsystem_internals.md:124-135` for `shortfall`'s definition rather than restating it. Then make each of F10's nine lines say the ceiling is enforced and link to that page: `docs/glossary.md:64` (keep its second clause, which stays true), `docs/operator_guide/scheduler.md:602` (a section heading), `:606` and `:668`, `docs/developer_guide/subsystem_internals.md:321`, `:325` and `:346`, `docs/operator_guide/database.md:1075`, `docs/release_notes/v07-v08.md:1665-1666`, and `ARCHITECTURE.md:212`. `subsystem_internals.md:346` is an instruction this phase carried out ("do not add an enforcement knob or a 403 path ahead of it") and becomes a record that it did; `:325` states the constant's value. Per D7 the release notes line is rewritten rather than preserved, because v0.8 is unreleased — leave the claim-creation status-code list at `:1677` alone. `ARCHITECTURE.md` changes because how placement refuses is the shape of the system; keep it to the same one or two sentences rather than expanding it, per the documentation policy. Add the 403 to `docs/developer_guide/api_reference/instances.md:93`'s pointer so it names both responses. **Do not touch** the four traps of F11: `docs/developer_guide/ci.md:15-16`, `docs/operator_guide/scheduler.md:114`, `docs/user_guide/placement.md:73` and `:81`, `docs/developer_guide/standards.md:76-89`. `AGENTS.md` needs no change — this changes no convention — so if you find yourself editing it, stop and say why. Commit subject: `Document the binding claim ceiling.` |
| 5 | medium | sonnet | worktree | **Close-out.** Write this plan's *Outcome*: what each step did, the window's figures and its verdict, which D1 row was closest to failing, what the flip cost in the first CI runs after it, and anything left as future work. Fill the Definition of done table with a result and a how-checked for every item, reporting anything not run as not run rather than ticking it. Set this phase's Execution row to Complete; phase 5a's row and `index.md`'s count were already correct before this phase began (F16) and this phase's planning commit set 5b's own, so check them rather than editing them. Clear plan-blocker b21 and record the reading in a note. Per the documentation policy `AGENTS.md` needs no change here. Commit subject: `Record what the hard ceiling cost.` |

## Risks and mitigations

**R1 — a 403 that names a claim which no longer exists.** The uuid and
the dimensions have different sources either side of a rollback (D4),
so a delete-and-create in between produces a refusal naming a claim
nobody can grow. *Mitigation:* D4 makes `dimensions` optional and uses
its emptiness as the discriminator, and step 2's test list includes
the empty-dimensions case explicitly. The management session checks
that the 403's text in that case recommends nothing — a body that says
"grow claim X by 4 vCPU" about a deleted claim is worse than one that
says only which claim refused.

**R2 — collision with `PLAN-placeable-capacity.md` phases 4 and 5.**
That plan's phase 4 must split a *hold* stage out of
`TRANSIENT_CAPACITY_STAGES` ("a refusal caused by another namespace's
hold is not transient"), and its phase 5 is called *The refusal
contract*. Both touch the frozensets this phase edits, the
`CAPACITY_GUARD_STAGE` comment this phase rewrites, and the page this
phase extends. Its phase 3 has already merged (as #5026) and is what
moved every line number in F1. *Mitigation:* this phase lands first
and leaves the shape those phases need — a third classification that
is neither set, demonstrated once, and a `capacity_refusals.md` with a
stage list and a non-507 status code already in it, so a hold stage is
an entry rather than a redesign. The management session states this in
the pull request so placeable-capacity's planner sees it; nothing here
reserves that plan's decisions.

**R3 — merge CI breaks on every platform if step 3 misses a site.**
The cluster CI test runs in merge CI and is not locally runnable, so a
missed assertion is found by the merge queue and not by `tox`.
*Mitigation:* F14 enumerates the sites rather than describing them,
and the Definition of done includes a grep that must come back empty.
The management session runs the unit suite before the push and reads
the first merge run; the known-flaky `tox py3` pre-commit hook is not
accepted as a pass.

**R4 — the flip makes a CI refusal permanent if the binding
regresses.** Under enforcement each over-limit admission becomes a
refused job with no operator remedy short of changing the declaration.
The five that held phase 5a were all one job landing on a peer's
claim, and what fixed it was a per-job binding token in another
repository, which nothing in this repository can keep working.
*Mitigation:* the conductor publishes
`conductor_bound_runner_mismatch_total` for exactly this, and it reads
0 today; the close-out records it as the canary and names it on
private-ci#311 so the people who own the binding know that a
regression now refuses jobs rather than merely mis-sizing claims.

**R5 — a thin window.** D1's readability floor is 500 claims and the
two previous windows read 2,038 and 3,770, so this is unlikely, but a
quiet week would make a residue unmeasurable. *Mitigation:* D1's
Unreadable branch extends the window once and reads the same rule
again. The floor is checked first, before any other row, so the phase
cannot read a verdict off a denominator it has not validated.

**R6 — the measurement needs an unmerged tool.** Step 1's
single-instance row wants
`tools/measure-oversized-instances.py --start/--end`, which is on
private-ci PR #322 and not on `master`. *Mitigation:* the brief says
so and names both ways round it. The row is also independently
readable from `workflow_costs` and the conductor's claim rows, which
is how 3g read it, so the tool is a convenience rather than a
dependency.

## Definition of done

1. *Observations* records the gating window's bounds to the second, in
   both AEDT and UTC, and they are exactly seven days apart and equal
   to the window D2 names.
2. All six of D1's measures are reported in *Observations* in phase
   4c's units, each with the tool or query that produced it, and the
   verdict is recorded as Flip, Hold or Unreadable with the arithmetic
   shown row by row.
3. The single-instance row is reported alongside private-ci 3g's
   independent reading of the same window, and any disagreement
   between them is stated.
4. If the verdict is Hold or Unreadable, items 5 to 14 are reported as
   not run, the failing rows are named, and the defect is filed
   against the plan that owns the cause.
5. `grep -n 'CLAIM_ENFORCEMENT_HARD' shakenfist/mariadb.py` shows
   `= True`.
6. `python3 -c "from shakenfist import constants;
   print(constants.capacity_refusal_is_transient('capacity_claim'))"`
   prints `False`, and `capacity_claim` appears in neither
   `TRANSIENT_CAPACITY_STAGES` nor `PERMANENT_CAPACITY_STAGES`.
7. A create refused at the claim stage on every candidate returns 403
   with `transient: false`, no `Retry-After`, and a body naming the
   claim; a create refused at a mixture of stages still returns 507.
   Both are asserted by tests that fail if the discriminator is
   inverted.
8. A claim-stage denial carrying an empty `dimensions` list still
   names its claim and recommends no growth, asserted by a test.
9. The `instance placement denied` audit event carries a `claim` key,
   and a test fails if it is dropped.
10. The instance create endpoint's published OpenAPI responses include
    the 403 with an example body, and
    `shakenfist/tests/external_api/test_openapi_spec.py` passes.
11. `grep -rn "CLAIM_ENFORCEMENT_HARD is False" shakenfist/ tools/`
    returns nothing, and no test wraps itself in
    `mock.patch.object(mariadb, 'CLAIM_ENFORCEMENT_HARD', True)` to
    get the default behaviour.
12. `grep -rn -i advisory docs/ ARCHITECTURE.md | grep -v
    '^docs/components/\|^docs/plans/'` returns only the four traps of
    F11 — `docs/developer_guide/ci.md:15-16`,
    `docs/operator_guide/scheduler.md:114`,
    `docs/user_guide/placement.md:73` and `:81`, and
    `docs/developer_guide/standards.md:76-89` — and nothing about
    claim ceilings.
13. `docs/operator_guide/capacity_refusals.md` names `capacity_claim`
    in its stage list, describes the 403 and the two extra body
    fields, and says `dimensions` may be absent; and no other page
    states the refusal's shape, each linking to it instead. Checked by
    grepping for `capacity_claim` and for `403` across `docs/` and
    reading every hit.
14. `docs/release_notes/v07-v08.md` does not say claim ceilings are
    advisory, and its claim-creation status-code list at `:1677` is
    unchanged by this phase.
15. `tox -e py3 -- 'shakenfist\.tests\.(test_mariadb_capacity_admission\.|external_api\.test_capacity_error\.|test_instance\.|test_ci_headroom_report\.)'`
    exits 0, run on the branch rebased onto `develop`.
16. `pre-commit run --all-files` passes every configured hook. A
    failure of the `tox py3` hook is retried and reported as a flake
    only after the suite has been run directly and passed.
17. The first merge-queue run after the push is read, and
    `test_placements_draw_down_the_claim_and_the_ceiling_refuses` is
    reported as passing on every platform, with the run id recorded.
    (Renamed from `..._and_report_exceedance` by step 3, because the
    test now proves that the ceiling refuses rather than that it
    reports.)
18. The master plan's Execution table reads `Complete` for this phase,
    `index.md`'s count agrees with the table, and plan-blocker b21 is
    cleared with a note recording the reading.

## Back brief

Before executing any step, back-brief the operator on the plan and how
the intended work aligns with it. Two gates:

- **Before step 1 reads anything**, confirm D2's choice of window.
  This is the one decision that cannot be revisited after the data is
  on screen without destroying the point of D1, and it is a decision
  that declines a suggestion recorded in plan-blocker n26. If the
  episode-free window of 2026-11-22 to 2026-11-29 is wanted instead,
  that is a decision to take now, at the cost of waiting until
  2026-11-29.
- **Before step 4 edits the release notes**, confirm D7. Rewriting a
  release-notes line is normally falsifying history; it is only
  correct here because v0.8 has not shipped, and that is a fact worth
  one sentence of agreement rather than an assumption.

D5 needs no gate: 5a's D5 and D6 were confirmed on 2026-11-07 and the
survey strengthened the first of them. Step 1's Hold branch needs no
back brief either — it stops, and stopping needs no permission.

## Observations

Phase 5a's reading of 2026-11-13 and phase 4c's baseline are the
comparison figures, and live in each of those plans' own *Observations*
sections. Step 1 writes its reading here beside them, in the same
units.

### Step 1 -- the reading, 2026-11-26

**The window reads Flip. All six of D1's rows pass, and the residue
the advisory release was watching for is not merely smaller than the
threshold -- it is absent.** Zero over-limit admissions, in a window
holding 3,913 claims and 3,738 collected runs.

**The window, to the second.** 2026-11-15 06:48:13 AEDT to
2026-11-22 06:48:13 AEDT, which is `2026-11-14T19:48:13Z` to
`2026-11-21T19:48:13Z`: **exactly 7.0 days**, computed rather than
asserted, and the window D2 names, anchored on
shakenfist/kerbside#727's merge at 2026-11-14 19:48 UTC. The
epoch bounds used in every query below are 1794685693 and 1795290493,
and the upper bound is exclusive throughout.

**Sources, and which produced each number.** The conductor's
`claim_events` and `workflow_costs`, read from a 2026-11-26 17:52
AEDT copy of `/srv/shakenfist/private-ci/conductor.db` taken with
`~/.ssh/id_ansible_ed25519`; the live file was never opened, and the
copy passed `PRAGMA quick_check`. No `-wal` or `-shm` sidecar existed
at the moment of the copy, so the snapshot is not missing checkpointed
transactions. private-ci's `tools/measure-oversized-instances.py
--start 2026-11-14T19:48:13Z --end 2026-11-21T19:48:13Z`, run from the
`claim-coverage-and-sizing-phase-03g-reading` worktree because R6's
`--start`/`--end` support is on private-ci#322 and not yet on
`master`. `tools/measure-claim-accuracy.py` for the ledger. Loki, for
the sfcbr nodes' own warnings and the conductor's stream, every query
excluding `caller=metrics.go` and `caller=engine.go`. `sf-client
namespace claim list --all` for the live census.

#### D1, row by row

| Measure | 4c baseline | 5a window | Flip requires | This window | |
|---|---|---|---|---|---|
| Over-limit admissions per day | 33.4/day | 0.71/day | <= 3.3/day | **0.00/day** (0 in 7 days) | pass |
| Namespaces with any over-limit admission | 101 | 5 | <= 10 | **0** | pass |
| Namespaces where one instance exceeded the whole claim | 24 | 5 | 0 | **0** | pass |
| Over-limit admissions on an *exact* dimension | not measured | 0.71/day | <= 1/day | **0.00/day** (0 in 7 days) | pass |
| `conductor_claims_failed_total`, claim logic only | 0 | 0 | 0 | **0** | pass |
| Claims created (readability floor) | 2038 | 3770 | >= 500 | **3913** | pass |

The floor was read first, as R5 requires, so no other row was read off
an unvalidated denominator: 3,913 created against a floor of 500 is
7.8x the minimum and the largest of the three windows. Rows 1, 2 and 4
come from the aggregate comparison -- each namespace's
`peak_allocated_*` against the `limit_*` of the one `created`
`claim_events` row it was charged to -- and read 0 run and 0 namespace
over, in any dimension. Row 3 comes from
`measure-oversized-instances.py`, which reports **"No run went
over"**. Row 5 is 0 because `claim_events` holds no `failed` row
inside the window; the only three in the table's entire 163,000-row
history are the sf-api reachability errors 5a found
(2026-09-21 21:09, 2026-10-21 19:11 and 2026-10-24 20:43 UTC: an
nginx 502, a refused connection and a second 502), and all three
predate the window, so D1's amendment excluded nothing and changed no
number -- as 5a expected it would not.

**Row 4's zero is a consequence rather than a measurement.** It counts
a subset of row 1, and row 1 is empty, so it is 0 whatever
`exact_dimensions` says. Worth recording because the first pass at it
was wrong: `exact_dimensions` stores a `+`-joined string
(`cpus+memory_mb+disk_gb`), not JSON, so a `json.loads` parse failed
to an empty list and would have reported 0 for the wrong reason. The
script was corrected to split on `+` and re-run to the same 0. On the
window's created claims the column reads `cpus+memory_mb+disk_gb` on
3,444 of 3,913 (88.0%), `cpus+memory_mb` on 386, and empty on 83.

#### Why the zero is believed

A zero is also what a broken measurement looks like, so each way it
could be false was closed.

**The columns are populated.** Across the window's 3,863 cost rows
there are **no** NULLs in `peak_allocated_cpus`, `peak_allocated_ram_mb`,
`peak_allocated_disk_gb` or `peak_shapes`; 2,546 rows record a nonzero
vCPU peak, the largest being 36 vCPU and 9 concurrent instances. The
zero is a comparison that found nothing, not a column that held
nothing.

**The join is sound.** `measure-oversized-instances.py`'s own checks
report 0 runs whose claim was attempted but not created, 0 runs with
no `claim_events` row at all, and 0 namespaces with more than one
`created` row, over 3,738 joined runs. F8 of 3g's plan holds on this
window as it did on fourteen days of earlier data.

**Two independent readings agree.** private-ci 3g read the same seven
days from the same tables and reported zero over-limit runs across
3,738 joined runs (F15). This phase's reading, taken from a database
copy four days later and run with explicit `--start`/`--end` bounds,
reports zero across 3,738 joined runs. The run counts are identical
and **there is no disagreement to report** -- which is what D1 item 3
asks for either way.

**The SF side agrees, as far as it can be made to.** Loki returns
**no** `Placement admitted over the namespace capacity claim` line in
the last twelve days, and no line matching `namespace capacity` at
all, with Loki's own query log excluded. All six sfcbr nodes (`sf-1`
to `sf-6`) have live `{job="syslog"}` streams. The stronger check is
the conductor stream against the database it writes: on every day the
helper's 5,000-line cap covers completely, the two agree **exactly** --
417 `Claimed` lines against 417 `created` rows on 2026-11-19, 439
against 439 on 2026-11-20, and 893 against 893 on 2026-11-21, the
contention day and the window's busiest. This is a weaker completeness
statement than 5a's, and the limit is worth naming rather than
glossing: `loki-query` takes `--since` but no absolute end, and an
unfiltered host stream saturates 2,000 lines in about twenty minutes,
so 2026-11-15 to 2026-11-18 could not be counted this way. The
authoritative record for rows 1 to 4 is the conductor database, which
is complete over the whole window; Loki corroborates it on the three
days it can reach, including the one most likely to have produced an
exceedance.

#### Claims, refusals and the ledger

| | This window | 5a window | 4c window |
|---|---|---|---|
| created (`claim_events`) | 3913 | 3770 | 1934 |
| refused on capacity | 3707 | 8032 | 303 |
| failed | 0 | 0 | 0 |

Per AEDT day, created then refused: 488/640 (2026-11-15, from
06:48), 292/139, 698/55, 666/619, 417/132, 439/232, 893/1890, and
20/0 on 2026-11-22 to 06:48.

**The contention episode is in the window and reads exactly as D2
predicted it would.** 2026-11-21 carries 1,890 of the window's 3,707
capacity refusals -- and also its highest claim creation count, 893.
Both halves of D2's argument are visible in that one row: the episode
raises the readability floor, and it put more claims and more
placements through the guard than any other day in the window, so if
contention could manufacture an over-limit admission this is the day
it would have. It produced none. Keeping the window cost nothing and
proved more than excluding it would have.

**No claim leaked, and the ledger does not drift.**
`measure-claim-accuracy.py`'s item 7 finds the ledger's mark and the
conductor's reconstruction agreeing to a median of **1.000** on all
three dimensions over **4,267** runs, with **zero** runs outside a 5%
tolerance and none to explain. The live census agrees from the other
end: `sf-client namespace claim list --all` returns 15 claims, every
one `created` and `active`, one per namespace, with usage at or below
limit in every row and no holder whose namespace has gone. As in 5a
and 4c this is the answer to the definition of done's
`cluster_capacity.claimed_*` drift question, which cannot be read as
worded -- `claimed_*` goes to a Prometheus gauge and no Prometheus is
reachable from the development host. One caveat on the accuracy
figures specifically: that tool takes `--since` and no `--end`, so its
4,267-run population is the window **plus** the four days after it,
not the window. It is quoted here for the ledger agreement, which is
what it is for; none of D1's six rows depends on it.

#### What the flip costs, and the one thing to watch

**On this window's traffic, the flip refuses nothing.** Zero of 3,738
runs exceeded the claim they were charged to in any dimension, so
enforcement applied to the measured week would have changed no
outcome. That is the direct answer to the question 5a could not
answer, where the cost was a 13.5% chance per `sf-e2e` run.

**The headroom, however, is exactly zero.** The maximum
peak-over-limit ratio in the window is **1.0000** in all three
dimensions, and 2,315 of 3,738 runs (61.9%) sit at a vCPU ratio of
exactly 1.000 -- the claim is the peak, not the peak plus margin,
which is F15's "exact in all three dimensions with no `CLAIM_HEADROOM`"
showing up fleet-wide rather than only on the three bound jobs. The
sizer has earned that: 4,132 of 4,268 runs are sized exactly and an
exact claim lands within 2% of what the run uses. But it means the
cost of this flip is borne entirely by *future* footprint growth
rather than by present traffic. A job whose footprint grows -- one
more VM in a test, a bigger image -- has no slack to grow into, and
under enforcement its first run after the change is refused where
today it is admitted with a warning. That is R4's territory and it is
why R4's canary matters; the close-out records it, and it is named on
private-ci#311 so the people who own the sizing know that a
regression now refuses jobs rather than mis-sizing claims.

**The three flagged pools are worst-case pairings that did not
occur.** `measure-oversized-instances.py`'s forward-looking report
flags `debian-13/l`, `debian-13/m` and `debian-13/s` as RESIDUE: in
each, the smallest claim the pool produced is smaller than the largest
single instance some job in it holds. Two are the false positive the
tool's own docstring predicts -- the largest instance belongs to
`kerbside/oVirt 4.5 on Rocky 8` and to `kerbside/OpenStack via
Kolla-Ansible master on Debian 13`, both **bound** by #727, and a bind
token lives in the combination key which `claim_events` does not
record, so binding is invisible in this schema. The third was checked
rather than assumed, because its holder is not one of the three bound
jobs: in `debian-13/s` the `kerbside-patches` jobs holding a
12/16384/400 instance were charged claims of **14/20480/560** (38
runs) and **17/24576/672** (5 runs), while the pool's smallest claim
of 2/4096/160 went to 49 runs whose peak was 2 vCPU. The small claim
and the large instance never met. Recorded because the exposure is
structural even where it is unrealised: the mis-assignment class was
removed for the three jobs that were bound, not for the pool
mechanism, and an unbound job sharing a combination pool can still in
principle be charged a claim sized for a smaller lane.

**Already-refused creates, for context.** The window's cost rows
record 464 refused creates across 53 runs and 1,781 unplaced
instances across 1,370 runs. None of these is a claim refusal --
the ceiling is advisory throughout the window -- they are the
cluster-capacity surface 5a flagged and `PLAN-placeable-capacity.md`
owns. They are noted so a later reader does not mistake them for the
flip's cost, and so the first merge run after the flip is read against
a baseline that already contains them.

#### Verdict

**Flip.** Every row of D1 is satisfied, the floor was validated before
any other row was read, the two readings of row 3 agree, and the
window is the one D2 pinned with the contention episode kept in.
Steps 2, 3 and 4 are therefore ungated and run.

## Execution notes

**Back brief answered 2026-11-26 (AEDT), before step 1 read
anything.** Both of the plan's gates were put to Michael together
with the two permissions the skill requires, and all four were
answered in one pass:

- **D2's window: confirmed as written.** The binding-anchored
  2026-11-15 06:48:13 to 2026-11-22 06:48:13 AEDT, declining the
  episode-free week that opens 2026-11-29 and declining plan-blocker
  n26's own suggestion of it. The reasoning is D2's and was agreed
  before the data was on screen, which is the only time it could be
  agreed. The reading has since vindicated it: see the 2026-11-21 row
  of the per-day table, which is both the episode and the window's
  busiest claim day, and produced no exceedance.
- **D7's release-notes rewrite: confirmed.** v0.8 is unreleased
  (newest tag `v0.8.0-rc4`), so the file describes the release being
  prepared and the line would be false on release day. Step 4 rewrites
  it and leaves the claim-creation status-code list alone.
- **Live read: approved**, read-only. The conductor database was
  copied and the copy queried; the live file was not opened. Nothing
  on `maui` or `sfcbr` was written, deployed or restarted.
- **Push and pull request: approved for this phase**, so the step 5
  question the skill would otherwise ask is not asked again.

**Step 1 ran in the management session**, as its row specifies, rather
than in a sub-agent; steps 2 to 4 are sub-agents per the execution
model. Two departures from the step 1 brief, both recorded at the
point they arose in *Observations*: `measure-oversized-instances.py`
was run from the private-ci 3g worktree because its `--start`/`--end`
arguments are still only on private-ci#322 (R6, as the brief
anticipated), and `measure-claim-accuracy.py` was run without
`python3 -I` because `-I` suppresses the user site-packages that hold
the editable `shakenfist_client` the tool imports -- the data rule's
`-I` is for untrusted downloaded data, and this is the repository's own
tool reading a database passed to it by path from outside its
directory.

**Steps 2 and 3 land as one commit, 2026-11-26 (AEDT), which the step
plan did not anticipate.** The plan gives them a commit subject each,
and there is no order in which both are green. The repository's `py3`
pre-commit hook runs the *whole* unit suite (`pass_filenames: false`)
on any change under `shakenfist/` or `tools/`, and the two steps are
the two halves of one behavioural change: step 2 makes three existing
tests false, and step 3 is what makes them true again. Committing step
2 alone would mean a commit the repository's own gate refuses, and
reordering does not help, because inverted assertions fail against
advisory behaviour exactly as advisory assertions fail against
enforcement. So they are one commit naming both halves, under step 2's
subject, and step 3's subject is not used. The split was a planning
error rather than a change of mind.

**Three facts the steps proved wrong have been corrected at their
source** rather than only noted here: D8 claimed two paths still reach
the over-limit event when only one does, F12 undercounted
`tools/ci_headroom_report.py` by six sites, and F14's test enumeration
missed a unit test, three live-suite tests which run in merge CI, and
a test case that was green while asserting a state the primitive can
no longer produce. Each correction is marked and dated where the
original claim is.

**One gap found by step 2 was ruled out of scope with evidence rather
than deferred.** The preflight redirect in
`shakenfist/operations/node_inst_netdesc_op.py` records
`CAPACITY_GUARD_STAGE` when every candidate refuses, which would be
wrong for an all-claim refusal because that stage is classified
transient. It is unreachable: the redirect always passes the
instance's existing placement as the old node, and the admission skips
the cluster and claim guards entirely for a move, so the only stages
reachable on that path are `node` and `cluster`. Checked rather than
assumed, and left alone.

**The wire contract was corrected too, which no step owned.**
`protos/database.proto` published "claim ceilings are advisory for one
release" in the comment on `claim_over_limit`, so the interface
definition between sf-api and sf-database stated the opposite of the
behaviour being shipped. The comment was corrected and the stubs
regenerated through `tox -e genprotos`; the regeneration touched only
`protos/database.proto` and `shakenfist/protos/database_pb2.pyi`, and
only that comment, so no unrelated generated churn rode along with it.

**Step 2 took a second round.** A sweep for the class of comment the
flip falsifies found five more in `shakenfist/mariadb.py` and
`shakenfist/instance.py` after the step reported, one of them plainly
wrong ("With claim_guarded False the increment above cannot refuse").
Its own report had named three; the sweep is what found the rest,
which is the argument for sweeping a class rather than accepting the
list that comes back.
