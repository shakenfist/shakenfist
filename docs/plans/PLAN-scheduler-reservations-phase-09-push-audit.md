# PLAN: Atomic scheduling via reservations phase 9 -- push audit

## Why this phase exists

[PLAN-scheduler-reservations.md](PLAN-scheduler-reservations.md) ends,
as every master plan must, with a phase that runs the repository's
`PUSH-AUDIT.md` over the whole plan's work. This
plan is the largest the repository has run it on: eighteen phases
across four months, thirty-one merge commits here and two more in
`client-python`, and roughly 35,000 non-plan insertions.

The master plan says why the per-phase audits are not enough, and the
reason is concrete rather than theoretical. Phase 4 shipped the
namespace claims API without adding `auth: namespace-claims` to the
API's capability list, so no client could feature-detect claims. That
was found by a passing question at phase 4's close-out, not by phase
4's own review, and nothing in a per-phase audit would have caught it:
the gap was only visible from the whole, by asking what the plan had
promised a client and whether the server had advertised it. That
defect is the reason this phase exists, and finding its siblings is
what the phase is for.

Recommended planning effort: medium (done). The judgment lenses run at
the efforts given in the step table. Two are at high: the security
lens, because the range adds an admission path that refuses creates
and an admin CRUD surface over it, and the cross-phase lens, because it
is the lens this phase exists to run and the one no earlier audit
could have.

## Scope

In scope:

- `PUSH-AUDIT.md` wave 1 and its wave 2 mechanical sweep over the
  twenty-one code-bearing ranges of D1.
- The style-conformance judgment portion, and the four wave 2 judgment
  lenses -- 2a code quality, 2b test review, 2c documentation, 2d
  security -- over the groups D6 fixes.
- The cross-phase lens: what the phases did to each other, and what is
  missing that is only visible from the whole. This is the phase's
  reason for existing and it is its own step.
- The master plan's specific instruction to re-check every code
  identifier cited by the developer guide's *Guarded counters for
  finite resources* section against `shakenfist/mariadb.py` (D10).
- `client-python` `135ab53042` (client-python#375), phase 4b's client
  half, audited from here (D5).
- Closing out phase 5b's `Merged` cell and its outstanding definition-of
  -done item 17 (F2), as this phase's first commit.
- Recording the findings, their grades and their disposition in this
  file, and closing out this phase and the plan.

Out of scope:

- **Fixing anything the audit finds.** The master plan says findings
  land as their own pull request, and that is what a blocking finding
  gets. This pull request carries the phase 5b close-out, this plan,
  and the audit record. D7 says how the two relate.
- Re-running `client-python` `d484fdc` (client-python#442), phase 7b.
  It applied the floor checklist in its own pull request and that
  result is cited, per the `plan-push-audit-phase` shared block
  (F8).
- Re-reviewing this plan's own phase plan files as documentation. The
  plan files are the audit's input, not its subject; the ten
  plan-file-only ranges of D2 get floor item 1 and nothing more.
- Any work belonging to `PLAN-placeable-capacity.md`, which has landed
  1,900 lines in `shakenfist/mariadb.py` across this plan's code since
  phase 3. Its ranges are not in D1 and a finding against them is
  filed, not fixed.
- The deferred designs this plan declined: batch create (D8 in the
  master plan), and a server-side placement queue, which
  `PLAN-transient-capacity-refusals.md` owns.

## What the survey found

Twelve findings. Four change what the audit does, and one is a
premise of the master plan's phase 9 section that is now wrong.

**F1 -- the master plan's audit range is superseded, and by a rule
written after it.** The phase 9 section says to rewrite every
`develop...HEAD` in `PUSH-AUDIT.md` to `ea3c9bf63..develop`,
restricted to a pinned path list. That text was written on 2026-08-25
(`9ddab70a05`). On 2026-09-13 `PUSH-AUDIT.md` gained a *What `$RANGE`
means* section (`f445a9d31e`, "Teach the audit runbook to read the
Merged column") which rules the union approach out in terms: "Run the
whole audit once per range and pool the findings; a plan with four
merges gets four passes, not one pass over a union that git cannot
express." The `plan-push-audit-phase` shared block agrees. D1 follows
the runbook and this phase corrects the master plan at source. The
path list the master plan asks for becomes unnecessary with it: each
range is a pull request from this plan's own branches, so nothing in
it is unrelated work and there is nothing to filter out. One
confirmation that the two anchors at least agree: `ea3c9bf63` and
`87a58a81e^1` both resolve to `ea3c9bf632bd18e6820ade80b3eef5eb1dd1327e`,
so the master plan's arithmetic was right even though its method is
superseded.

**F2 -- phase 5b's `Merged` cell is blank, and `PUSH-AUDIT.md` says a
blank cell stops the audit.** The runbook's range rule ends: "If a
plan's `Merged` column is missing or a cell is blank, stop and say so
-- that is an unauditable plan." Phase 5b reads `Complete` with `—`,
because a phase cannot record the merge commit of its own pull
request; its own Future work says so and owes the cell to this phase.
It is `31f3350c1c` (#5087), merged 2026-11-26 19:53 UTC, and this
phase's first commit fills it. The same commit can close something
else 5b left open: its definition-of-done item 17 was recorded as *not
run* because the cluster CI test runs only in the merge queue, and the
merge queue has since run it. Run `45877310889`, job `140107936676`,
`Functional lanes / Debian 13 cluster (collection)`, 2026-11-26
19:16:33 UTC:
`test_namespace_claims.TestNamespaceClaimAccounting.test_placements_draw_down_the_claim_and_the_ceiling_refuses [21.093576s] ... ok`.
The merge queue selected only the Debian 13 cluster lane -- `Ubuntu
24.04 cluster` was skipped -- so item 17's "on every platform" is
answered for the one cluster platform that ran, which is stated rather
than rounded up.

**F3 -- thirty-one ranges, and ten of them contain nothing but plan
files.** Every sha in the master plan's `Merged` column resolves, is a
merge commit, and is an ancestor of `develop`. D1's table has the
sizes. Ten ranges -- #3566, #3731, #3783, #3952, #4008, #4139, #4154,
#3956, #3957, #4687 -- touch nothing outside `docs/plans/`, because
phase 0 was a decisions document, phase 4c's work lives in
`shakenfist/private-ci`, and three of them are close-out commits. That
is a third of the ranges carrying none of the audit's subject matter,
and D2 scopes them out of everything but floor item 1.

**F4 -- three pull requests appear in more than one phase row, so the
ranges are a set and not a list.** `921573c475` (#3888) closed out
phases 3, 4 and 4a together; `c27ad9bf27` (#3930) closed out 00a while
landing 4b's server half. Running the audit once per *row* would audit
those twice. D1's table is deduplicated to thirty-one distinct shas,
with the phases each one served named beside it.

**F5 -- `docs/operator_guide/scheduler.md` is touched by fourteen of
the twenty-one code-bearing ranges, and is where cross-phase
documentation drift is most likely.** The next most-touched files are
`shakenfist/mariadb.py` (9), `shakenfist/scheduler.py` (8),
`ARCHITECTURE.md` (8), and then `shakenfist/tests/test_scheduler.py`,
the two capacity-admission test modules, `shakenfist/tests/mock_mariadb.py`,
`protos/database.proto` and `docs/developer_guide/subsystem_internals.md`
at 7 each. The whole non-plan union is about 100 files. The 2c brief
is pointed at the fourteen-range page first.

**F6 -- four ranges edit `CLAUDE.md`, which no longer exists as a
separate file.** It was folded into `AGENTS.md` and `docs/` on
2026-09-28 (`2f443906ff`), after phases 00a to 6 landed. So a
convention one of those ranges recorded in `CLAUDE.md` may be in
`AGENTS.md` today, may have been dropped in the fold, or may be in
neither. Any finding about a convention this plan recorded has to be
confirmed against `AGENTS.md` at `$TIP`, not against the file the diff
edited. This is a worked example of D3's rule rather than an exception
to it.

**F7 -- the developer guide's guarded-counter section exists and its
identifiers are all still present, which is the weaker of the two
questions.** `docs/developer_guide/database_internals.md:238`, *Guarded
counters for finite resources*, cites 25 code identifiers in its first
half alone -- `_floored_node_decrement()`, `_retry_transaction()`,
`_TRANSIENT_TRANSACTION_ERRNOS`, `_retry_on_deadlock()`,
`node_not_sized`, `never_reconciled`, `uq_networks_vxid`,
`ipam_reservations`, `instance_location`, `CLIENT_FOUND_ROWS` and the
rest. A `grep -w` for each over `shakenfist/` finds all 25, so none
has been deleted outright. That is not what the master plan asks for:
it asks whether the prose still describes the admission path, which
phase 5b changed under it. D10 makes that its own step, and the
"present somewhere in the tree" check is explicitly not the answer.

**F8 -- phase 7b's floor checklist exists and is citable, with one
caveat worth stating.** `client-python`'s
`docs/plans/PLAN-scheduler-reservations-phase-07b-client-views.md:467`
carries a *Push audit* section run on 2026-10-28, with a verdict for
each of the five floor items. The caveat: it audited
`origin/develop...HEAD`, which also covered client-python#433 to #438,
and its own item 1 reads "Not yet applicable" because the phase had
not merged yet. Nothing of 7b's work went unaudited, so the citation
is sound; it is wider than 7b rather than narrower, and item 1 is the
one this phase can now close for it (`d484fdc63c`, client-python#442,
merged 2026-10-28).

**F9 -- phase 4b's client range is not in the `Merged` column at all.**
The master plan's phase 9 section says "4b's client range predates the
`plan-push-audit-phase` rule and is audited from here", but 4b's row
names only the two shakenfist merges, so the range itself is
unrecorded. It is `client-python` `135ab53042` (client-python#375),
merged 2026-08-28 07:24 UTC, 11 files, +945/-0, adding
`apiclient` claim verbs, `sf-client namespace claim`, and four
documentation files. The close-out commit records it in 4b's cell in
the form the shared block prescribes, and a step audits it.

**F10 -- `client-python` still has no `PUSH-AUDIT.md`.** Confirmed by
`ls`. `PLAN-transient-capacity-refusals` phase 7 found the same thing,
audited its client range from the server repository's runbook, and
recorded "client-python should carry its own `PUSH-AUDIT.md`" as Future
work rather than adding one. This phase follows that precedent exactly
and does not add one either; D5 says why.

**F11 -- the capability string this phase exists for was fixed, which
narrows the cross-phase lens rather than closing it.** The API
advertises `auth: namespace-claims` as of the phase 4a close-out, and
phase 7a added three more tokens. So the specific defect is gone. The
question the lens asks is the general one: what else did a phase
promise that a later phase was supposed to publish, and did it? The
capability tokens, the `coverage_state` filter, the closed
`EXCLUSION_REASONS` set and `CAPACITY_CLAIM_STAGE`'s membership of
neither classified set are four places where one phase's contract is
another phase's publication.

**F12 -- one range touches `.github/workflows/functional-tests.yml`,
which is the only unattended thing the plan added.** That is floor
item 2's subject, and it matters for a reason F2 names: the
`functional_lanes` job is excluded on `pull_request` and runs only on
`merge_group` or `workflow_dispatch`, which is what made phase 5b's
item 17 unsatisfiable from its own pull request. Whether a cluster test
this plan added can fail without anyone seeing it is a real audit
question, not a formality.

Nothing else the master plan's phase 9 section claims was found wrong.
The three orphan defect fixes it names (`49e98a67c8` #3722,
`45332ff810` #3918, `a5c6216339` #3937) all resolve, are merge
commits, and carry code; they are rows in D1.

## Decisions

**D1 -- the range set is thirty-one in-repository ranges plus one
client range, run per range and pooled.** This follows
`PUSH-AUDIT.md`'s range rule and declines the master plan's
`ea3c9bf63..develop` union (F1). `$RANGE` for a row below is
`<sha>^1..<sha>`, and `git diff <sha>^1 <sha>` is the command; never
`git log`.

| Phase(s) | PR | Range sha | Whole diff | Outside `docs/plans/` |
|----------|----|-----------|------------|-----------------------|
| 00a | #3436 | `113e4efb57` | 15 files, +1434/-25 | 12 files, +1039/-24 |
| 00a, 4b | #3930 | `c27ad9bf27` | 9 files, +1576/-133 | 3 files, +241/-82 |
| 00a | #3956 | `2fcfe8afcb` | 2 files, +91/-12 | plan files only |
| 0 | #3566 | `87a58a81ef` | 3 files, +1641/-26 | plan files only |
| 0 | #3731 | `a16d32aecd` | 2 files, +110/-5 | plan files only |
| 0 | #3783 | `b93539322c` | 2 files, +63/-7 | plan files only |
| 1 | #3578 | `80bffad56e` | 6 files, +709/-9 | 4 files, +461/-8 |
| 2 | #3614 | `d3a91cdef5` | 35 files, +5577/-96 | 32 files, +4588/-95 |
| 3 | #3754 | `0144e36ede` | 47 files, +9755/-1160 | 42 files, +8275/-1133 |
| 3, 4, 4a | #3888 | `921573c475` | 10 files, +1420/-60 | 6 files, +1023/-35 |
| 4 | #3790 | `7bd7bbd03c` | 43 files, +9958/-158 | 39 files, +9133/-136 |
| 4a | #3843 | `b02ec6e5e5` | 13 files, +1208/-125 | 8 files, +451/-47 |
| 4b | #3949 | `a5014b6ba8` | 5 files, +366/-74 | 1 file, +23/-8 |
| 4c | #3952 | `c273627a28` | 2 files, +11/-5 | plan files only |
| 4c | #4008 | `9480550630` | 2 files, +123/-5 | plan files only |
| 4c | #4139 | `8fc8ba961a` | 2 files, +330/-55 | plan files only |
| 4c | #4154 | `2b767a9b1f` | 3 files, +416/-190 | plan files only |
| 5 | #4800 | `660d4f65e7` | 15 files, +1016/-460 | 13 files, +644/-459 |
| 5a | #4917 | `3797716023` | 9 files, +1472/-36 | 5 files, +316/-28 |
| 5b | #5087 | `31f3350c1c` | 30 files, +2275/-393 | 27 files, +1194/-387 |
| 6 | #3957 | `597312d2a7` | 3 files, +2017/-3 | plan files only |
| 6 | #3971 | `b54f126e53` | 14 files, +1749/-73 | 14 files, +1749/-73 |
| 6 | #3972 | `c37012c891` | 3 files, +134/-1 | 3 files, +134/-1 |
| 6 | #4006 | `96c78fd63b` | 6 files, +245/-56 | 1 file, +1/-1 |
| 7 | #4052 | `30a02a918a` | 27 files, +3391/-189 | 24 files, +2237/-183 |
| 7a | #4654 | `e8585a8745` | 31 files, +2766/-88 | 28 files, +2113/-87 |
| 7a | #4687 | `3695f168aa` | 3 files, +49/-35 | plan files only |
| 8 | #4737 | `a005fba498` | 9 files, +1167/-44 | 6 files, +437/-30 |
| none (#3565) | #3722 | `49e98a67c8` | 4 files, +232/-36 | 4 files, +232/-36 |
| none (#3907) | #3918 | `45332ff810` | 3 files, +310/-14 | 3 files, +310/-14 |
| none (#3636) | #3937 | `a5c6216339` | 4 files, +346/-25 | 4 files, +346/-25 |
| 4b (client) | client-python#375 | `135ab53042` | 11 files, +945/-0 | audited here, D5 |
| 7b (client) | client-python#442 | `d484fdc63c` | — | cited, not re-run (F8) |

**D2 -- the ten plan-file-only ranges get floor item 1 and nothing
else.** They carry no code, no published documentation and no
workflow, so there is nothing for a code, test, documentation or
security lens to read in them, and re-reviewing this plan's own phase
plans as documentation is out of scope. Each gets a line confirming it
is a merge commit whose diff is read against its first parent, and
that its content is entirely under `docs/plans/`. If a step finds
anything outside `docs/plans/` in one of them, the range moves into
the code-bearing set and the step says so -- that would mean the
survey's arithmetic was wrong.

**D3 -- `$TIP` is the sha this branch was cut from, and every finding
is confirmed there.** `$TIP` is `7cb1f24ec4` (`develop`, 2026-11-28).
A finding is a defect only if it is still a defect at `$TIP`: the
ranges are four months old, `PLAN-placeable-capacity.md` has landed
1,900 lines in `shakenfist/mariadb.py` among them, and a line a range
added may have been rewritten since. Every finding carries a quoted
line at `$TIP` with a file and line number, and a report that cannot
produce one is classified **already fixed** and recorded as such
rather than filed. F6 is the worked example: a convention a range
wrote into `CLAUDE.md` is confirmed against `AGENTS.md` at `$TIP` or
not at all.

**D4 -- wave 1's tree-level commands run once at `$TIP`, and the
thirty-one merge commits are not checked out.** `pre-commit run
--all-files` and `tox` test a *tree*, not a range.
`PLAN-transient-capacity-refusals` phase 7 ran them against each of its
ten merge commits in scratch worktrees, to ask whether each range
passed when it landed. That is the most likely decision here to be
argued with, and it is declined for thirty-one: the answer for the
older ranges is dominated by hooks that did not exist in August
(`check-plan-phase-references`, `check-doc-anchors`,
`check-signal-handlers`, `skillsaw` and the whole-suite `py3` hook have
all arrived or changed since), so the output is mostly archaeology
about the hook configuration rather than about this plan's code, at
thirty-one full `pre-commit` runs of cost. What matters is that the
tree passes now, and the per-range work that *is* range-shaped -- the
four wave 1 style greps and the six wave 2 mechanical sweep commands
-- is run per range, which is where range-specific defects actually
show up. If the management session wants the historical question
answered, three ranges sample it: `d3a91cdef5` (phase 2, the schema),
`7bd7bbd03c` (phase 4, the largest), and `31f3350c1c` (phase 5b, the
most recent). That is an option the back brief offers, not a step.

**D5 -- `client-python` `135ab53042` is audited from here; `d484fdc63c`
is cited.** The shared block says a phase that landed in another
repository is audited there as part of the pull request that lands it,
and the plan's push-audit phase cites that audit rather than re-running
it. 7b did exactly that, so it is cited (F8). 4b's client half predates
the rule and no such audit exists (F9), so there is nothing to cite and
it is audited here against this repository's runbook, as
`PLAN-transient-capacity-refusals` phase 7 did for its own client
range. `client-python` still gets no `PUSH-AUDIT.md` from this phase
(F10): writing one is a change to another repository's conventions and
belongs in that repository's own plan, and it is recorded as Future
work there rather than done here on the way past.

**D6 -- the judgment lenses read groups of ranges, not single ranges
and not all twenty-one at once.** A lens that reads one range at a
time cannot see what the phases did to each other, which is the whole
point; a lens handed 35,000 insertions reads none of it properly. The
groups are fixed here so no step has to choose:

| Group | Ranges | Outside `docs/plans/` |
|-------|--------|-----------------------|
| G1 schema and ledger | #3578, #3614 | 36 files, +5049 |
| G2 admission primitive and claims | #3754, #3790, #3843, #3888 | 95 files, +18882 |
| G3 enforcement | #4800, #4917, #5087 | 45 files, +2154 |
| G4 affinity | #3971, #3972, #4006 | 18 files, +1884 |
| G5 diagnostics and operator surface | #4052, #4654 | 52 files, +4350 |
| G6 static quick wins and client support | #3436, #3930, #3949 | 16 files, +1303 |
| G7 documentation | #4737 | 6 files, +437 |
| G8 orphan defect fixes | #3722, #3918, #3937 | 11 files, +888 |
| G9 client | client-python#375 | 11 files, +945 |

G2 is 54% of the insertions and gets a code-quality step to itself
with G1. The file counts are sums over ranges, so a file touched by
two ranges is counted twice.

**D7 -- findings are graded blocking or advisory, and nothing is fixed
in this pull request.** The master plan says findings land as their own
pull request and that anything security-critical or high is fixed in
this phase. Those two are in tension, and the resolution is: *this*
pull request carries the phase 5b close-out, this plan and the audit
record; a **blocking** finding -- security-critical, or a correctness
defect reachable in production -- is fixed in a second pull request on
its own branch, before this phase closes, and this plan records which
pull request fixed it. An **advisory** finding is filed as an issue or
recorded under Future work with a sentence saying why it was not
fixed. A finding declined says why, here, where the next reader finds
it. The phase is not complete until every finding has one of those
three dispositions in writing.

**D8 -- two management-checklist items are struck, not ticked.** "Commit
history is clean" and "branch is up to date" do not apply to thirty-one
merge commits that landed months ago; nothing here is rebaseable. The
master plan already says this and the audit record says it again rather
than reporting a pass it did not earn.

**D9 -- this phase records no `Merged` cell and closes itself out in
its own pull request.** It is the last row of the Execution table, so
there is no next phase to fill its cell, which is the standing
exception in the `plan-phase-landing` block. Its own close-out step
sets its status, sets the master plan to `Complete`, and sets
`index.md`'s row to `Complete` with `18 of 18` -- but only once every
finding has a disposition under D7, because the plan is not complete
until they do. If a blocking finding is still open when the audit
record is written, the phase stays `In progress` and says what it is
waiting on.

**D10 -- the guarded-counter identifier re-check asks whether the prose
is true, not whether the names exist.** The master plan singles this out
because phase 5b changed the admission path that
`docs/developer_guide/database_internals.md`'s *Guarded counters for
finite resources* section describes, and nothing else keeps the prose
in step. F7 establishes that all 25 identifiers in its first half are
still present somewhere in `shakenfist/`, which is the cheap half of
the question and is not the answer. The step reads the section against
`shakenfist/mariadb.py` at `$TIP` and reports, per claim, whether the
code still behaves as described -- with the enforcement sentences
first, since those are the ones phase 5b moved under.

## Step plan

Every step works in
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/shakenfist-wt-sr-p09`,
takes `$TIP` as `7cb1f24ec4` and the ranges and groups as D1 and D6
define them, and classifies every finding under D3. Read-only steps
change no files; the management session records each one's report into
this plan under a `### ` heading named for the step.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | medium | management session | none | **Done in the planning commit.** Close out phase 5b -- set its `Merged` cell to `31f3350c1c` (#5087), record F2's merge-queue evidence against its definition-of-done item 17, and add client-python#375's `135ab53042` to phase 4b's cell per F9 -- then write this plan and register it in the master plan's Execution table and `docs/plans/index.md`, correcting the phase 9 section's superseded range instruction per F1. Two commits: `Close out the hard ceiling phase.` and `Plan the scheduler reservations push audit.` |
| 9b | low | sonnet | none | **Wave 1 and the wave 2 mechanical sweep.** First, once, at `$TIP`, each under `timeout 2400`: `pre-commit run --all-files`; `tox`; `tools/mermaid-lint.sh` (read its exit status directly -- piping it through `tail` or `grep` reports the filter's status and turns every failure green); `tox -e genprotos && git diff --exit-code shakenfist/protos` (seven ranges change `protos/database.proto`). Do **not** check out any of D1's merge commits; D4 says why. Then, for each of the **twenty-one code-bearing ranges** of D1 separately, run `PUSH-AUDIT.md`'s four wave 1 style greps and the six commands of its wave 2 mechanical sweep, substituting `git diff <sha>^1 <sha>` for `$RANGE`. Never use `git log` on a range. For each of the **ten plan-file-only ranges**, instead confirm D2: that the sha is a merge commit with two parents (`git rev-list --parents -n1 <sha>`), and that `git diff --name-only <sha>^1 <sha>` lists nothing outside `docs/plans/`; report any range where that is false as a survey error. Record every command and its literal output, or "no output", in a new `## Findings` -> `### Wave 1` section of this plan, one `#### <sha>` sub-heading per range whose second word is the bare sha. Fix nothing. If `pre-commit` or `tox` fails, record the failing test names, check with `git diff --name-only <sha>^1 <sha>` whether any range touched the failing module, and carry on. Commit subject: `Record wave 1 of the reservations push audit.` |
| 9c | medium | sonnet | none | **Style conformance, judgment portion.** Read-only. Apply `PUSH-AUDIT.md`'s *Style conformance — judgment portion* brief to the twenty-one code-bearing ranges together. Skip `shakenfist/protos/*_pb2*`, which is generated. The rules most at risk across this plan: the SQL-pushdown rule (the plan's whole thesis is that the ledger is read in SQL, so a Python-side filter added by a later phase is a regression); the three-layer `_direct_*` / `_grpc_*` / public pattern in `shakenfist/mariadb.py`, which phases 2, 3, 4, 7a and 5b each added RPCs under; the unit-change rule, since phase 00a moved CPU reservations from cores to threads and phases 1 and 7a publish `*_gb`, `*_mb` and thread-denominated metrics side by side; and single-quote, 120-column and trailing-whitespace conformance in the newest code. Confirm each candidate at `$TIP` and classify it under D3. Return findings with file, line, range and grade, or "Style checks passed" plus the files read. |
| 9d | high | opus | none | **2a code quality over G1 and G2** -- the schema, the ledger, the reconciler, the claim primitive and the claims API. Read-only. 131 files and +23,931 outside `docs/plans/`, of which G2 alone is 95 files and +18,882, so spend the budget where the risk is: `shakenfist/mariadb.py`'s admission transaction, the reconciler in `shakenfist/daemons/cluster/scheduled_tasks.py`, `shakenfist/scheduler.py`'s guard stages, `shakenfist/namespace_claim.py`, and the `GetClusterCapacity` / claim RPCs in `protos/database.proto` and `shakenfist/daemons/database/main.py`. Apply `PUSH-AUDIT.md`'s 2a brief and all its shared blocks, and `source-file-size` to `shakenfist/mariadb.py` honestly -- it is over 30,000 lines; say whether these ranges made it worse and whether there is a seam, not that it is out of scope. The specific questions: is the guarded `UPDATE` genuinely the first statement of every transaction these ranges added (the `snapshot-isolation` rule, and MariaDB >= 11.6.2 returns ER_CHECKREAD if it is not, which CI's 10.11 cannot see); does every drawdown have a release, and does every release path survive the operation dying between them; is the reconciler's "absent row" choice the one the developer guide says it is, per pool; and does any guard re-derive usage at decision time instead of reading the ledger. Confirm every candidate at `$TIP` and classify under D3. Return findings with file, line, range, grade and classification. |
| 9e | medium | opus | none | **2a code quality over G3, G4 and G6** -- enforcement, affinity, and the static quick wins. Read-only. 79 files and +5,341 outside `docs/plans/`. Apply `PUSH-AUDIT.md`'s 2a brief and its shared blocks. Where to spend it: `CLAIM_ENFORCEMENT_HARD`'s consumers in `shakenfist/mariadb.py` and the refusal classification in `shakenfist/constants.py`, where `CAPACITY_CLAIM_STAGE` is deliberately in neither `TRANSIENT_CAPACITY_STAGES` nor `PERMANENT_CAPACITY_STAGES` -- establish that every reader of those two frozensets handles a stage in neither, because a membership test that assumes the sets partition the stages is the defect shape here; the affinity scorer in `shakenfist/scheduler.py` and its binary model from phase 6; and phase 00a's load-aware ordering, whose `floor(load)` quantisation phase 6 replaced with load-per-core -- check nothing still reads the old form. G6 includes `shakenfist/deploy/shakenfist_ci/sizing.py`, which sizes CI claims; it is test infrastructure but a wrong size there now refuses a job rather than warning. Confirm at `$TIP`, classify under D3, return findings with file, line, range, grade and classification. |
| 9f | medium | opus | none | **2a code quality over G5, G7 and G8** -- diagnostics, the operator surface, the documentation phase's code, and the three orphan defect fixes. Read-only. 69 files and +5,675 outside `docs/plans/`. Apply `PUSH-AUDIT.md`'s 2a brief and its shared blocks. Where to spend it: the `/admin/resources` and `GET /admin/claims` handlers in `shakenfist/external_api/admin.py` and their examples, the `coverage_state` SQL-side filter (a pushdown that a later phase could have undone), the closed `EXCLUSION_REASONS` set in `shakenfist/scheduler.py` and whether anything can emit a reason outside it, and `tools/ci_headroom_report.py`. Then the three orphan fixes, which are the ranges nobody has looked at as part of this plan: #3722 ranked affinity above transient load shedding, #3918 removed the claims tests' unreserved-headroom assumption, and #3937 added RAM-aware ranking and gating -- for each, ask whether a later phase re-introduced what it fixed, naming the lines you read. Confirm at `$TIP`, classify under D3, return findings with file, line, range, grade and classification. |
| 9g | medium | opus | none | **2b test review over all twenty-one code-bearing ranges.** Read-only. Apply `PUSH-AUDIT.md`'s 2b brief and its `functional-test-coverage` block. Four questions beyond the brief. (1) `shakenfist/tests/mock_mariadb.py` is in seven ranges: a mock taught the shape of the admission transaction can make a test pass against a mock that is wrong in the same way as the code. Say which capacity assertions rest on the mock and which on real SQL, and which of `test_mariadb_capacity_admission.py` / `test_mariadb_capacity_admission_live.py` / `..._claims{,_live}.py` / `..._reconcile{,_live}.py` are reached by `pre-commit` and `tox` at all, versus only by a live MariaDB. (2) The guarded-`UPDATE`-first invariant cannot fail on CI's MariaDB 10.11 -- it needs >= 11.6.2 for ER_CHECKREAD -- so say what, if anything, would catch a violation before production. (3) Phase 6 closed issue #3565 on a *test* change: `test_affinity` now skips rather than passes when the candidate set collapsed before affinity was scored. Say whether that skip can now swallow a real affinity regression, and whether anything reports how often it skips. (4) Phase 5b's definition-of-done item 17 was satisfiable only in the merge queue (F2, F12); say which other tests these ranges added have that property, and whether any of them can fail unnoticed. Do not duplicate 9k's cross-phase question. Return findings grouped by file, with grades and classifications. |
| 9h | medium | opus | none | **2c documentation review over all twenty-one code-bearing ranges.** Read-only. Apply `PUSH-AUDIT.md`'s 2c brief and all its shared blocks. Start with `docs/operator_guide/scheduler.md`, touched by fourteen of the twenty-one ranges (F5) -- that is where a fact true when its phase landed is most likely to have been falsified by a later one. Then `docs/developer_guide/subsystem_internals.md` (7 ranges), `ARCHITECTURE.md` (8), `docs/operator_guide/database.md` (5), `docs/release_notes/v07-v08.md` (5), `docs/operator_guide/capacity_refusals.md`, `docs/glossary.md`, `docs/user_guide/affinity.md`, `docs/user_guide/placement.md` and `docs/developer_guide/api_reference/{admin,instances}.md`. Check every metric name, configuration option, capability token, refusal stage, status code and PromQL expression against the code at `$TIP`. The question that matters: **is any fact about this plan stated differently on two pages** -- what the ledger counts, whether a claim ceiling is advisory or binding, which status code a capacity refusal returns, whether a refusal is transient, what the reconciler does with an absent row. Phase 5b swept "advisory" and left four known traps which are about something else (`developer_guide/ci.md:15-16`, `developer_guide/standards.md:76-89`, `user_guide/placement.md:73,81`, `operator_guide/scheduler.md:114`); a fifth is a finding. Also apply `plan-references-in-code` and `plan-phase-references` to every range's added lines, and handle F6: four ranges edit `CLAUDE.md`, which no longer exists, so confirm each convention they recorded against `AGENTS.md` at `$TIP` and say which were lost in the 2026-09-28 fold. Do **not** review this plan's own phase plan files (D2). Return findings, or "No documentation gaps found" plus the pages read. |
| 9i | high | opus | none | **2d security review over all twenty-one code-bearing ranges, plus G9.** Read-only; `client-python` ranges are at `../client-python`. Apply `PUSH-AUDIT.md`'s 2d brief. The area that matters is **who may move the ledger**. Establish: that `GET /admin/claims`, `POST`/`DELETE` on `/auth/namespaces/<namespace>/claims` and the `GetClusterCapacity` read are each reachable only by the caller they are meant for, checked against `shakenfist/external_api/auth.py` and the endpoint-authentication decorators rather than by reading the docstrings; that a namespace cannot create, grow or draw down a claim belonging to another namespace, including by supplying a namespace name in a body while authenticated for a different one; that no table, column or namespace name reaches SQL by interpolation in the admission transaction or the `coverage_state` filter, and that uuid and quantity arguments are bound; and that a quantity cannot be negative, non-integer or large enough to overflow its column in a way that turns a drawdown into a credit. Then disclosure: now that refusal bodies publish `stage`, `transient`, a claim uuid and claim dimensions, say whether a refusal, an audit event or a log line tells a caller about another namespace's footprint, cluster-wide totals, or node names it should not see -- the 403 body phase 5b added names a claim and recommends growth, so check what it reveals when the claim is not the caller's. Finally floor item 3 over every range's added lines: no secret, token, private hostname or non-placeholder IP address. Return findings with severity, file, line, vulnerability class and recommended fix, each confirmed at `$TIP`. |
| 9j | medium | sonnet | none | **The guarded-counter prose re-check (D10).** Read-only, and the master plan asks for this one specifically. `docs/developer_guide/database_internals.md:238`, *Guarded counters for finite resources*, is written as the pattern for a developer about to add a second finite-resource claim. Read the whole section and, **claim by claim**, say whether `shakenfist/mariadb.py` at `$TIP` still behaves as described. Start with the sentences phase 5b moved under: what a zero-row result means now that `CLAIM_ENFORCEMENT_HARD` is `True`, what the claim guard does on refusal, and whether the described refusal is still the refusal the API returns. Then every named identifier -- `_floored_node_decrement()`, `_retry_transaction()`, `_TRANSIENT_TRANSACTION_ERRNOS`, `_retry_on_deadlock()`, `node_not_sized`, `never_reconciled`, `instance_location`, `CLIENT_FOUND_ROWS`, `uq_networks_vxid`, `ipam_reservations` and the rest -- checked at its definition, not by `grep -w`: F7 already established all 25 exist somewhere, which is not the question. Report per claim: still true (with the file and lines read), stale (with the current behaviour), or unverifiable. A verdict with no citation is rejected. Also say whether the section's three MariaDB error numbers (1213, 1205, 1020) still match `_TRANSIENT_TRANSACTION_ERRNOS`. |
| 9k | high | opus | none | **The cross-phase lens.** Read-only, and this is the step the phase exists for -- no earlier audit could have run it. Answer three questions in order and do not generalise beyond them. (1) **What did a phase promise that a later phase was supposed to publish, and did it?** The known instance is the one this phase exists for: phase 4 shipped the claims API without `auth: namespace-claims`, found by a question at close-out (F11). It is fixed. Find its siblings by working the other direction: enumerate every capability token, refusal stage, metric, configuration option, audit event key, API field and `EXCLUSION_REASONS` member these ranges added, and for each say what advertises it, what consumes it, and whether a client that does not know it degrades or breaks. Name the enumeration method, not just the result. (2) **What did one phase assume that another changed?** Candidates the survey can already name: phase 00a's cores-to-threads unit change against phases 1 and 7a's published metrics; phase 3's D13 demand guard, which shipped the #3813 defect that phase 4a fixed -- is the fix still in place; phase 6's binary affinity model against phase 00a's load ordering, which #3722 then re-ranked; and phase 5's consolidation of the duplicated placement walk onto `Instance.place_on_candidates()`, which `PLAN-placeable-capacity.md` phase 1 then generalised. (3) **What is only missing when you look at the whole?** A helper that exists twice because two phases each added it; a code path that no phase's tests reach because each phase tested its own half; a documented workflow that needs a command from one phase and a token from another and was never run end to end. State plainly where the answer is "they agree" -- that is a result. Confirm every claim at `$TIP` per D3. Return findings with file, line, the phases involved, grade and classification. |
| 9l | medium | sonnet | worktree (client-python) | **G9: phase 4b's client half, audited from here (D5).** Read-only, in a worktree of `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python` at `develop`. Range `135ab53042^1..135ab53042` (client-python#375), 11 files, +945/-0. That repository has no `PUSH-AUDIT.md` (F10), so apply the five numbered items of the `plan-push-audit-phase` floor checklist -- quoted from `PLAN-TEMPLATE.md:126` -- giving each a verdict or a sentence saying why it does not apply, plus this plan's own questions: does any new verb or rendering path print a credential; does `apiclient`'s claim support feature-detect `auth: namespace-claims` and degrade rather than traceback against a server that lacks it; and does any claim verb crash on a payload with every optional key removed. Confirm each finding against `client-python`'s `develop` tip, not against the 2026-08 tree. Item 1 for client-python#442 is answered by citation, not by re-running it (F8): record that `d484fdc63c` is its merge commit and that its own *Push audit* section at `docs/plans/PLAN-scheduler-reservations-phase-07b-client-views.md:467` carries the five verdicts, with F8's caveat that it audited a wider range than its own. Record "client-python should carry its own `PUSH-AUDIT.md`" as a Future work item for that repository; do **not** add one. Return a per-item report naming the files and functions read. |
| 9m | medium | management session | none | **Triage, disposition and close-out.** Not a sub-agent: grading and disposition are decisions. Pool 9b to 9l's findings. Grade each blocking or advisory and give each a disposition under D7, and spot-check one "still true at `$TIP`" claim per report against the file itself before recording it -- a report that names no files or functions is rejected and re-run. Present the graded table at the back brief's second gate before filing anything. Then file the advisory findings as issues or Future work entries, record the blocking ones and the pull request that fixes them, strike D8's two checklist items, and write `### Disposition` into this plan's Findings section. Write this plan's *Outcome*: what each step read, what was found, and "nothing" where that is the answer -- `PUSH-AUDIT.md` says a clean result is recorded in one sentence and is a real result. Add an *Audit of phases 00a to 8* subsection to the master plan's phase 9 section. Set this phase and the plan per D9, update `docs/plans/index.md` to `18 of 18`, run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: `Record the reservations push audit disposition.` |

9c to 9l are independent of each other and run in parallel once 9b has
reported; 9b's wave 1 output is an input to 9d, 9e and 9f, so those
three take it from this plan's Findings section rather than re-running
the sweep.

## Risks and mitigations

**R1 -- the audit rubber-stamps merged code.** It is the standing
failure of this phase across every plan that has run it, and it is
worse here than anywhere, because thirty-one ranges and four months
make "it has been fine in production" an unusually comfortable
answer. *Mitigation:* D3's classification, which forces a quoted line
at `$TIP` and rejects a finding that cannot produce one; D6's rule that
each lens reads a group rather than a range; 9m's spot-check of one
claim per report; and 9k carrying the "what is only visible from the
whole" question as a step of its own rather than a clause in another
brief. 9m rejects a report that names no functions.

**R2 -- 9k is the step that matters and the step most likely to come
back "no cross-phase issues found".** An agent asked whether eighteen
phases are consistent will be tempted to confirm it. *Mitigation:* its
brief asks for the enumeration *method* for question 1, not just the
result, and names four concrete candidates for question 2 from the
survey, each of which is a real historical event in this plan rather
than a hypothetical. The management session re-reads the capability-token
enumeration itself before accepting the report, because that is the
defect class the phase was created for.

**R3 -- a finding is really another plan's.** `PLAN-placeable-capacity.md`
has landed about 1,900 lines in `shakenfist/mariadb.py` among this
plan's code since phase 3, and `PLAN-transient-capacity-refusals.md`
owns the refusal contract this plan's phase 5b extended. An agent
reading `mariadb.py` at `$TIP` cannot tell whose line it is.
*Mitigation:* D3 requires the range as well as the file and line on
every finding, so a finding whose line is not in any of D1's ranges is
identifiable as out of scope; 9m files it against the owning plan
rather than absorbing it. Scope says this explicitly.

**R4 -- the scale defeats the lenses.** 35,000 insertions across
twenty-one ranges is more than any of this repository's previous push
audits, and a lens that skims it all returns nothing useful.
*Mitigation:* D6's groups, with G2 -- 54% of the insertions -- given a
step with only G1 beside it; every judgment brief naming the files to
spend the budget on rather than describing them; and D2 removing ten
ranges from the judgment work entirely. If a lens reports that it could
not read its group, 9m splits it rather than accepting the report.

**R5 -- wave 1 says nothing, because it runs at `$TIP`.** D4 declines
to check out the thirty-one merge commits, so wave 1's tree-level half
reports only that today's tree passes, which the last merge-queue run
already said. *Mitigation:* the range-shaped half -- ten grep-level
commands per code-bearing range, 210 in all -- is where range-specific
defects actually show, and it does run per range. The back brief offers
the three-range historical sample as an explicit option, so declining
it is a decision taken rather than a step forgotten.

**R6 -- a blocking finding reopens a phase that is already `Complete`.**
A correctness defect in, say, phase 3's admission transaction is a
defect in code that has been enforcing claim ceilings in production
since 2026-11-26. *Mitigation:* D7 keeps the fix out of this pull
request and on its own branch, so the audit record lands whatever
happens to the fix, and D9 holds the phase and the plan open until
every finding has a disposition. A phase row is not reopened: the
finding is recorded against this phase, which is where a reader of the
plan will look for it.

## Definition of done

1. Every one of D1's thirty-three rows has a line in this plan's
   Findings section saying what was read against it, including the ten
   plan-file-only ranges and the two cited or separately-audited client
   ranges. Checked by
   `grep -cE '^#### [0-9a-f]{10}' docs/plans/PLAN-scheduler-reservations-phase-09-push-audit.md`,
   which reads 31 -- the twenty-one code-bearing and ten plan-only
   in-repository ranges, each with its own sub-heading -- and by the
   two client ranges being named in prose.
2. The master plan's Execution table has no blank `Merged` cell for any
   row but phase 9's, and phase 5b's reads `31f3350c1c` (#5087). Phase
   4b's cell also names `client-python 135ab53042 (#375)`.
3. Phase 5b's definition-of-done item 17 is no longer *not run*: its
   row records the merge-queue run id, the job id and the test's result,
   and says which cluster platforms ran and which were skipped.
4. The master plan's phase 9 section no longer instructs the audit to
   use `ea3c9bf63..develop` with a pinned path list, and says instead
   what D1 does and why `PUSH-AUDIT.md`'s range rule supersedes it.
5. `pre-commit run --all-files` passes every configured hook at `$TIP`,
   `tox` exits 0, `tools/mermaid-lint.sh` exits 0 read directly rather
   than through a pipe, and `tox -e genprotos && git diff --exit-code
   shakenfist/protos` is clean. Each is recorded with its result; a
   `py3` hook failure is retried and reported as a flake only after the
   suite has been run directly and passed.
6. The ten wave 1 and wave 2 mechanical commands are recorded with
   their literal output, or "no output", for each of the twenty-one
   code-bearing ranges -- 210 command lines.
7. Each of the ten plan-file-only ranges has a recorded confirmation
   that it is a merge commit with two parents and that its diff names
   no path outside `docs/plans/`.
8. Every one of 9c to 9l has a `### ` section in this plan carrying its
   report, including the ones whose answer is that they found nothing,
   and each names the files it read.
9. 9j reports a verdict for every claim in
   `docs/developer_guide/database_internals.md`'s *Guarded counters for
   finite resources* section, each with the file and lines read, and
   states whether the three MariaDB error numbers still match
   `_TRANSIENT_TRANSACTION_ERRNOS`.
10. 9k answers its three questions separately, names the method it used
    to enumerate the capability tokens, stages, metrics, options, event
    keys and API fields of question 1, and states "they agree" where
    that is the answer rather than omitting the question.
11. Every finding in the `### Disposition` table carries a file, a
    line, the range it belongs to, a grade, a disposition, and a quoted
    line at `$TIP` or the classification *already fixed*. No finding is
    recorded without one of those two.
12. Every blocking finding names the pull request that fixed it, and
    every advisory finding names the issue or the Future work entry
    that carries it. A declined finding says why, in this plan.
13. D8's two management-checklist items appear struck, with the reason,
    rather than ticked.
14. `python3 tools/check-plan-status.py` exits 0, the master plan's
    Execution table and `docs/plans/index.md` agree, and `index.md`
    reads `18 of 18` and `Complete` -- or `In progress` with a named
    open finding, per D9.
15. `grep -c 'ea3c9bf63' docs/plans/PLAN-scheduler-reservations.md`
    reads 1, and that one hit sits inside the master plan's
    *Corrected 2026-11-28* paragraph -- so the superseded range is
    described there rather than instructed. Checked by reading the
    paragraph, not by the count alone. (This plan's own six mentions
    of the sha are in F1, D1 and this list, all of them describing it;
    the grep is deliberately scoped to the master plan, because that
    is the file an implementer would otherwise follow.)

## Back brief

Before executing any step, back-brief the operator on the plan and how
the intended work aligns with it. Two gates:

- **Before 9b runs**, confirm D4. Declining to check out the
  thirty-one merge commits is the decision in this plan most likely to
  be argued with, because `PLAN-transient-capacity-refusals` phase 7
  did exactly that for its ten and found the anachronistic-hook
  distinction informative. If the historical question is wanted, the
  three-range sample named in D4 -- `d3a91cdef5`, `7bd7bbd03c`,
  `31f3350c1c` -- is the cheap version of it, and that is a decision to
  take before 9b starts rather than a step to add afterwards.
- **Before 9m files anything**, present the graded findings table. The
  split between blocking and advisory decides what gets a second pull
  request and what gets an issue, and under D7 it is the difference
  between fixing production code now and recording it for later. Present
  the grades for agreement before any issue is filed or any fix branch
  is cut.

D1, D2, D3 and D6 need no gate: they follow `PUSH-AUDIT.md`'s own range
rule and the shared block, and F1 and F3 are the evidence. D9 needs
none either -- it is the `plan-phase-landing` block's standing
exception for a push-audit phase.

## Findings

### Wave 1

#### Tree-level checks at the tip

Run once, at the tip, in this worktree. No merge commit was checked
out.

`timeout 2400 pre-commit run --all-files`
Passed (exit 0); every hook passed.

`timeout 2400 tox`
Passed (exit 0): py3 OK, flake8 OK, cover OK, 70% total coverage.

`timeout 600 tools/mermaid-lint.sh`
Passed (exit status read directly, 0); every diagram rendered ok.

`timeout 1200 tox -e genprotos`
Passed (exit 0).

`git diff --exit-code shakenfist/protos`
Clean (exit 0): the generated stubs are up to date, so no restore
was needed.

Note on the sweep output below: the over-120-character and print()
greps hit generated protobuf descriptors, two operator scripts
whose output is print(), and one historical comment mentioning
etcd. None is a defect in itself, and the management session
confirmed each class by file rather than by inference. Every
over-120 line is a `DESCRIPTOR` line in a generated
`shakenfist/protos/*_pb2.py`. Every added `print(` is in
`tools/exercise-namespace-claims.py` (33 in `921573c475`) or
`tools/ci_headroom_report.py` (84 in `30a02a918a`, 9 in
`31f3350c1c`), both of which are operator-run report scripts for
which stdout is the interface. The `etcd` hit is
`# v1: schema creation. v2: etcd data-import marker (historical).`

#### 113e4efb57

`git diff 113e4efb57^1 113e4efb57 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 113e4efb57^1 113e4efb57 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 113e4efb57^1 113e4efb57 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 113e4efb57^1 113e4efb57 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 113e4efb57^1 113e4efb57 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 113e4efb57^1 113e4efb57 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 113e4efb57^1 113e4efb57 --stat | tail -1`
```
 15 files changed, 1434 insertions(+), 25 deletions(-)
```

`git diff 113e4efb57^1 113e4efb57 -- '*.py' | grep -cE '^\+\s*def test_'`
```
28
```

`git diff 113e4efb57^1 113e4efb57 --name-only -- 'docs/*' '*.md'`
```
AGENTS.md
ARCHITECTURE.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-00a-load-aware-ordering.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
docs/release_notes/v07-v08.md
```

`git diff 113e4efb57^1 113e4efb57 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### c27ad9bf27

`git diff c27ad9bf27^1 c27ad9bf27 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff c27ad9bf27^1 c27ad9bf27 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff c27ad9bf27^1 c27ad9bf27 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff c27ad9bf27^1 c27ad9bf27 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff c27ad9bf27^1 c27ad9bf27 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff c27ad9bf27^1 c27ad9bf27 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff c27ad9bf27^1 c27ad9bf27 --stat | tail -1`
```
 9 files changed, 1576 insertions(+), 133 deletions(-)
```

`git diff c27ad9bf27^1 c27ad9bf27 -- '*.py' | grep -cE '^\+\s*def test_'`
```
2
```

`git diff c27ad9bf27^1 c27ad9bf27 --name-only -- 'docs/*' '*.md'`
```
docs/developer_guide/ci.md
docs/plans/PLAN-scheduler-reservations-phase-00-decisions.md
docs/plans/PLAN-scheduler-reservations-phase-00a-load-aware-ordering.md
docs/plans/PLAN-scheduler-reservations-phase-04b-client.md
docs/plans/PLAN-scheduler-reservations-phase-04c-conductor-claims.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff c27ad9bf27^1 c27ad9bf27 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 80bffad56e

`git diff 80bffad56e^1 80bffad56e -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 80bffad56e^1 80bffad56e -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 80bffad56e^1 80bffad56e -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
```
18:+# v1: schema creation. v2: etcd data-import marker (historical).
```

`git diff 80bffad56e^1 80bffad56e -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 80bffad56e^1 80bffad56e -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 80bffad56e^1 80bffad56e -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 80bffad56e^1 80bffad56e --stat | tail -1`
```
 6 files changed, 709 insertions(+), 9 deletions(-)
```

`git diff 80bffad56e^1 80bffad56e -- '*.py' | grep -cE '^\+\s*def test_'`
```
14
```

`git diff 80bffad56e^1 80bffad56e --name-only -- 'docs/*' '*.md'`
```
CLAUDE.md
docs/operator_guide/database.md
docs/plans/PLAN-scheduler-reservations-phase-01-node-metrics-columns.md
docs/plans/PLAN-scheduler-reservations.md
```

`git diff 80bffad56e^1 80bffad56e -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### d3a91cdef5

`git diff d3a91cdef5^1 d3a91cdef5 -- '*.py' | grep -nE '^\+[^+].{120,}'`
```
1886:+DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0e\x64\x61tabase.proto\x12\x11shakenfist.protos\x1a\x16shakenfist_enums.proto\"-\n\x0bStatusReply\x12\x0f\n\x07success\x18\x01 \x0
```

`git diff d3a91cdef5^1 d3a91cdef5 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff d3a91cdef5^1 d3a91cdef5 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff d3a91cdef5^1 d3a91cdef5 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff d3a91cdef5^1 d3a91cdef5 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff d3a91cdef5^1 d3a91cdef5 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff d3a91cdef5^1 d3a91cdef5 --stat | tail -1`
```
 35 files changed, 5577 insertions(+), 96 deletions(-)
```

`git diff d3a91cdef5^1 d3a91cdef5 -- '*.py' | grep -cE '^\+\s*def test_'`
```
102
```

`git diff d3a91cdef5^1 d3a91cdef5 --name-only -- 'docs/*' '*.md'`
```
AGENTS.md
ARCHITECTURE.md
CLAUDE.md
README.md
docs/operator_guide/database.md
docs/operator_guide/installation.md
docs/operator_guide/scheduler.md
docs/operator_guide/upgrades.md
docs/plans/PLAN-byo-mariadb-phase-01-statelessness.md
docs/plans/PLAN-scheduler-reservations-phase-02-capacity-tables.md
docs/plans/PLAN-scheduler-reservations.md
docs/release_notes/v07-v08.md
```

`git diff d3a91cdef5^1 d3a91cdef5 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 0144e36ede

`git diff 0144e36ede^1 0144e36ede -- '*.py' | grep -nE '^\+[^+].{120,}'`
```
2733:+DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0e\x64\x61tabase.proto\x12\x11shakenfist.protos\x1a\x16shakenfist_enums.proto\"-\n\x0bStatusReply\x12\x0f\n\x07success\x18\x01 \x0
```

`git diff 0144e36ede^1 0144e36ede -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 0144e36ede^1 0144e36ede -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 0144e36ede^1 0144e36ede -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 0144e36ede^1 0144e36ede -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 0144e36ede^1 0144e36ede -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 0144e36ede^1 0144e36ede --stat | tail -1`
```
 47 files changed, 9755 insertions(+), 1160 deletions(-)
```

`git diff 0144e36ede^1 0144e36ede -- '*.py' | grep -cE '^\+\s*def test_'`
```
186
```

`git diff 0144e36ede^1 0144e36ede --name-only -- 'docs/*' '*.md'`
```
AGENTS.md
ARCHITECTURE.md
CLAUDE.md
docs/developer_guide/database_internals.md
docs/developer_guide/standards.md
docs/developer_guide/subsystem_internals.md
docs/operator_guide/database.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-00-decisions.md
docs/plans/PLAN-scheduler-reservations-phase-02-capacity-tables.md
docs/plans/PLAN-scheduler-reservations-phase-03-primitive.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff 0144e36ede^1 0144e36ede -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 921573c475

`git diff 921573c475^1 921573c475 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 921573c475^1 921573c475 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
```
504:+        print('\n=== %s ===' % title)
511:+            print('  FAIL  %s\n          %s' % (description, e))
516:+            print('  ERROR %s\n          %s' % (description, trace))
519:+        print('  ok    %s%s' % (
530:+        print('  skip  %s  (%s)' % (description, why))
```
33 lines; truncated to the first 5.

`git diff 921573c475^1 921573c475 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 921573c475^1 921573c475 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 921573c475^1 921573c475 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 921573c475^1 921573c475 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 921573c475^1 921573c475 --stat | tail -1`
```
 10 files changed, 1420 insertions(+), 60 deletions(-)
```

`git diff 921573c475^1 921573c475 -- '*.py' | grep -cE '^\+\s*def test_'`
```
6
```

`git diff 921573c475^1 921573c475 --name-only -- 'docs/*' '*.md'`
```
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-04-claims-api.md
docs/plans/PLAN-scheduler-reservations-phase-04a-demand-guard.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff 921573c475^1 921573c475 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 7bd7bbd03c

`git diff 7bd7bbd03c^1 7bd7bbd03c -- '*.py' | grep -nE '^\+[^+].{120,}'`
```
3889:+DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0e\x64\x61tabase.proto\x12\x11shakenfist.protos\x1a\x16shakenfist_enums.proto\"-\n\x0bStatusReply\x12\x0f\n\x07success\x18\x01 \x0
4208:+DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x16shakenfist_enums.proto\x12\x11shakenfist.protos*\xd9\x07\n\nObjectType\x12\x1b\n\x17OBJECT_TYPE_UNSPECIFIED\x10\x00\x12\x1e\n\x1
```

`git diff 7bd7bbd03c^1 7bd7bbd03c -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 7bd7bbd03c^1 7bd7bbd03c -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 7bd7bbd03c^1 7bd7bbd03c -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 7bd7bbd03c^1 7bd7bbd03c -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 7bd7bbd03c^1 7bd7bbd03c -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 7bd7bbd03c^1 7bd7bbd03c --stat | tail -1`
```
 43 files changed, 9958 insertions(+), 158 deletions(-)
```

`git diff 7bd7bbd03c^1 7bd7bbd03c -- '*.py' | grep -cE '^\+\s*def test_'`
```
217
```

`git diff 7bd7bbd03c^1 7bd7bbd03c --name-only -- 'docs/*' '*.md'`
```
ARCHITECTURE.md
CLAUDE.md
docs/developer_guide/authentication.md
docs/developer_guide/database_internals.md
docs/developer_guide/state_machine.md
docs/developer_guide/subsystem_internals.md
docs/operator_guide/database.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-03-primitive.md
docs/plans/PLAN-scheduler-reservations-phase-04-claims-api.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff 7bd7bbd03c^1 7bd7bbd03c -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### b02ec6e5e5

`git diff b02ec6e5e5^1 b02ec6e5e5 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff b02ec6e5e5^1 b02ec6e5e5 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff b02ec6e5e5^1 b02ec6e5e5 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff b02ec6e5e5^1 b02ec6e5e5 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff b02ec6e5e5^1 b02ec6e5e5 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff b02ec6e5e5^1 b02ec6e5e5 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff b02ec6e5e5^1 b02ec6e5e5 --stat | tail -1`
```
 13 files changed, 1208 insertions(+), 125 deletions(-)
```

`git diff b02ec6e5e5^1 b02ec6e5e5 -- '*.py' | grep -cE '^\+\s*def test_'`
```
8
```

`git diff b02ec6e5e5^1 b02ec6e5e5 --name-only -- 'docs/*' '*.md'`
```
docs/developer_guide/subsystem_internals.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-00-decisions.md
docs/plans/PLAN-scheduler-reservations-phase-02-capacity-tables.md
docs/plans/PLAN-scheduler-reservations-phase-04a-demand-guard.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff b02ec6e5e5^1 b02ec6e5e5 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### a5014b6ba8

`git diff a5014b6ba8^1 a5014b6ba8 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff a5014b6ba8^1 a5014b6ba8 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff a5014b6ba8^1 a5014b6ba8 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff a5014b6ba8^1 a5014b6ba8 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff a5014b6ba8^1 a5014b6ba8 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff a5014b6ba8^1 a5014b6ba8 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff a5014b6ba8^1 a5014b6ba8 --stat | tail -1`
```
 5 files changed, 366 insertions(+), 74 deletions(-)
```

`git diff a5014b6ba8^1 a5014b6ba8 -- '*.py' | grep -cE '^\+\s*def test_'`
```
0
```

`git diff a5014b6ba8^1 a5014b6ba8 --name-only -- 'docs/*' '*.md'`
```
docs/developer_guide/ci.md
docs/plans/PLAN-scheduler-reservations-phase-04b-client.md
docs/plans/PLAN-scheduler-reservations-phase-04c-conductor-claims.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff a5014b6ba8^1 a5014b6ba8 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 660d4f65e7

`git diff 660d4f65e7^1 660d4f65e7 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 660d4f65e7^1 660d4f65e7 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 660d4f65e7^1 660d4f65e7 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 660d4f65e7^1 660d4f65e7 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 660d4f65e7^1 660d4f65e7 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 660d4f65e7^1 660d4f65e7 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 660d4f65e7^1 660d4f65e7 --stat | tail -1`
```
 15 files changed, 1016 insertions(+), 460 deletions(-)
```

`git diff 660d4f65e7^1 660d4f65e7 -- '*.py' | grep -cE '^\+\s*def test_'`
```
5
```

`git diff 660d4f65e7^1 660d4f65e7 --name-only -- 'docs/*' '*.md'`
```
AGENTS.md
ARCHITECTURE.md
docs/developer_guide/subsystem_internals.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-05-callers.md
docs/plans/PLAN-scheduler-reservations.md
```

`git diff 660d4f65e7^1 660d4f65e7 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 3797716023

`git diff 3797716023^1 3797716023 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 3797716023^1 3797716023 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 3797716023^1 3797716023 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 3797716023^1 3797716023 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 3797716023^1 3797716023 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 3797716023^1 3797716023 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 3797716023^1 3797716023 --stat | tail -1`
```
 9 files changed, 1472 insertions(+), 36 deletions(-)
```

`git diff 3797716023^1 3797716023 -- '*.py' | grep -cE '^\+\s*def test_'`
```
8
```

`git diff 3797716023^1 3797716023 --name-only -- 'docs/*' '*.md'`
```
docs/plans/PLAN-scheduler-reservations-phase-05-callers.md
docs/plans/PLAN-scheduler-reservations-phase-05a-hard-ceiling.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff 3797716023^1 3797716023 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 31f3350c1c

`git diff 31f3350c1c^1 31f3350c1c -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 31f3350c1c^1 31f3350c1c -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
```
2025:+    print('    These placements SUCCEEDED. A claim ceiling refuses a')
2026:+    print('    scheduled create, so what landed here is an unguarded')
2027:+    print('    ground-truth write -- a guard cannot refuse a domain which')
2028:+    print('    already exists. It is the signal a declared footprint needs')
2029:+    print('    revising, and it is never added to the refusal count above.')
2038:+        print('  %d %s admitted OVER a namespace capacity claim, which only'
2043:+        print('  an unguarded ground-truth write can now be; this is data')
2044:+        print('  about the claim\'s size, not a failure, and it is no part')
2045:+        print('  of the refusal counts above.')
```

`git diff 31f3350c1c^1 31f3350c1c -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 31f3350c1c^1 31f3350c1c -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 31f3350c1c^1 31f3350c1c -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 31f3350c1c^1 31f3350c1c -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 31f3350c1c^1 31f3350c1c --stat | tail -1`
```
 30 files changed, 2275 insertions(+), 393 deletions(-)
```

`git diff 31f3350c1c^1 31f3350c1c -- '*.py' | grep -cE '^\+\s*def test_'`
```
19
```

`git diff 31f3350c1c^1 31f3350c1c --name-only -- 'docs/*' '*.md'`
```
ARCHITECTURE.md
docs/developer_guide/api_reference/instances.md
docs/developer_guide/subsystem_internals.md
docs/glossary.md
docs/operator_guide/capacity_refusals.md
docs/operator_guide/database.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-05b-hard-ceiling.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
docs/release_notes/v07-v08.md
```

`git diff 31f3350c1c^1 31f3350c1c -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### b54f126e53

`git diff b54f126e53^1 b54f126e53 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff b54f126e53^1 b54f126e53 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff b54f126e53^1 b54f126e53 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff b54f126e53^1 b54f126e53 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff b54f126e53^1 b54f126e53 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff b54f126e53^1 b54f126e53 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff b54f126e53^1 b54f126e53 --stat | tail -1`
```
 14 files changed, 1749 insertions(+), 73 deletions(-)
```

`git diff b54f126e53^1 b54f126e53 -- '*.py' | grep -cE '^\+\s*def test_'`
```
50
```

`git diff b54f126e53^1 b54f126e53 --name-only -- 'docs/*' '*.md'`
```
ARCHITECTURE.md
docs/developer_guide/ci.md
docs/operator_guide/scheduler.md
docs/user_guide/affinity.md
```

`git diff b54f126e53^1 b54f126e53 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### c37012c891

`git diff c37012c891^1 c37012c891 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff c37012c891^1 c37012c891 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff c37012c891^1 c37012c891 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff c37012c891^1 c37012c891 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff c37012c891^1 c37012c891 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff c37012c891^1 c37012c891 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff c37012c891^1 c37012c891 --stat | tail -1`
```
 3 files changed, 134 insertions(+), 1 deletion(-)
```

`git diff c37012c891^1 c37012c891 -- '*.py' | grep -cE '^\+\s*def test_'`
```
12
```

`git diff c37012c891^1 c37012c891 --name-only -- 'docs/*' '*.md'`
```
docs/release_notes/v07-v08.md
```

`git diff c37012c891^1 c37012c891 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 96c78fd63b

`git diff 96c78fd63b^1 96c78fd63b -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 96c78fd63b^1 96c78fd63b -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 96c78fd63b^1 96c78fd63b -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 96c78fd63b^1 96c78fd63b -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 96c78fd63b^1 96c78fd63b -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 96c78fd63b^1 96c78fd63b -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 96c78fd63b^1 96c78fd63b --stat | tail -1`
```
 6 files changed, 245 insertions(+), 56 deletions(-)
```

`git diff 96c78fd63b^1 96c78fd63b -- '*.py' | grep -cE '^\+\s*def test_'`
```
0
```

`git diff 96c78fd63b^1 96c78fd63b --name-only -- 'docs/*' '*.md'`
```
GOALS.md
docs/plans/PLAN-ci-cloud-sizing-phase-00-decisions.md
docs/plans/PLAN-ci-cloud-sizing.md
docs/plans/PLAN-scheduler-reservations-phase-06-affinity.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff 96c78fd63b^1 96c78fd63b -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 30a02a918a

`git diff 30a02a918a^1 30a02a918a -- '*.py' | grep -nE '^\+[^+].{120,}'`
```
844:+DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0e\x64\x61tabase.proto\x12\x11shakenfist.protos\x1a\x16shakenfist_enums.proto\"-\n\x0bStatusReply\x12\x0f\n\x07success\x18\x01 \x01
```

`git diff 30a02a918a^1 30a02a918a -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
```
2844:+        print('  NO CENSUS WAS SUPPLIED (--census was not given), so nothing is')
2845:+        print('  known about what the capacity guard did. Not zero refusals.')
2848:+        print('  NO CENSUS IS AVAILABLE: %s (%s)' % (census.status, census.detail))
2849:+        print('  Read as "unknown", never as zero guard refusals.')
2853:+        print('  NO CAPACITY GUARD EVENTS IN THIS CENSUS.')
```
84 lines; truncated to the first 5.

`git diff 30a02a918a^1 30a02a918a -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 30a02a918a^1 30a02a918a -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 30a02a918a^1 30a02a918a -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 30a02a918a^1 30a02a918a -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 30a02a918a^1 30a02a918a --stat | tail -1`
```
 27 files changed, 3391 insertions(+), 189 deletions(-)
```

`git diff 30a02a918a^1 30a02a918a -- '*.py' | grep -cE '^\+\s*def test_'`
```
37
```

`git diff 30a02a918a^1 30a02a918a --name-only -- 'docs/*' '*.md'`
```
docs/developer_guide/subsystem_internals.md
docs/operator_guide/events.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-07-diagnostics.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
```

`git diff 30a02a918a^1 30a02a918a -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### e8585a8745

`git diff e8585a8745^1 e8585a8745 -- '*.py' | grep -nE '^\+[^+].{120,}'`
```
983:+DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0e\x64\x61tabase.proto\x12\x11shakenfist.protos\x1a\x16shakenfist_enums.proto\"-\n\x0bStatusReply\x12\x0f\n\x07success\x18\x01 \x01
```

`git diff e8585a8745^1 e8585a8745 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff e8585a8745^1 e8585a8745 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff e8585a8745^1 e8585a8745 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff e8585a8745^1 e8585a8745 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff e8585a8745^1 e8585a8745 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff e8585a8745^1 e8585a8745 --stat | tail -1`
```
 31 files changed, 2766 insertions(+), 88 deletions(-)
```

`git diff e8585a8745^1 e8585a8745 -- '*.py' | grep -cE '^\+\s*def test_'`
```
55
```

`git diff e8585a8745^1 e8585a8745 --name-only -- 'docs/*' '*.md'`
```
docs/developer_guide/api_reference/admin.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-07-diagnostics.md
docs/plans/PLAN-scheduler-reservations-phase-07a-capacity-surface.md
docs/plans/PLAN-scheduler-reservations.md
```

`git diff e8585a8745^1 e8585a8745 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### a005fba498

`git diff a005fba498^1 a005fba498 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff a005fba498^1 a005fba498 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff a005fba498^1 a005fba498 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff a005fba498^1 a005fba498 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff a005fba498^1 a005fba498 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff a005fba498^1 a005fba498 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff a005fba498^1 a005fba498 --stat | tail -1`
```
 9 files changed, 1167 insertions(+), 44 deletions(-)
```

`git diff a005fba498^1 a005fba498 -- '*.py' | grep -cE '^\+\s*def test_'`
```
0
```

`git diff a005fba498^1 a005fba498 --name-only -- 'docs/*' '*.md'`
```
ARCHITECTURE.md
docs/developer_guide/database_internals.md
docs/developer_guide/subsystem_internals.md
docs/glossary.md
docs/operator_guide/scheduler.md
docs/plans/PLAN-scheduler-reservations-phase-08-docs.md
docs/plans/PLAN-scheduler-reservations.md
docs/plans/index.md
docs/release_notes/v07-v08.md
```

`git diff a005fba498^1 a005fba498 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 49e98a67c8

`git diff 49e98a67c8^1 49e98a67c8 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 49e98a67c8^1 49e98a67c8 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 49e98a67c8^1 49e98a67c8 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 49e98a67c8^1 49e98a67c8 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 49e98a67c8^1 49e98a67c8 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 49e98a67c8^1 49e98a67c8 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 49e98a67c8^1 49e98a67c8 --stat | tail -1`
```
 4 files changed, 232 insertions(+), 36 deletions(-)
```

`git diff 49e98a67c8^1 49e98a67c8 -- '*.py' | grep -cE '^\+\s*def test_'`
```
7
```

`git diff 49e98a67c8^1 49e98a67c8 --name-only -- 'docs/*' '*.md'`
```
docs/operator_guide/scheduler.md
```

`git diff 49e98a67c8^1 49e98a67c8 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 45332ff810

`git diff 45332ff810^1 45332ff810 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 45332ff810^1 45332ff810 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 45332ff810^1 45332ff810 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 45332ff810^1 45332ff810 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 45332ff810^1 45332ff810 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 45332ff810^1 45332ff810 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 45332ff810^1 45332ff810 --stat | tail -1`
```
 3 files changed, 310 insertions(+), 14 deletions(-)
```

`git diff 45332ff810^1 45332ff810 -- '*.py' | grep -cE '^\+\s*def test_'`
```
5
```

`git diff 45332ff810^1 45332ff810 --name-only -- 'docs/*' '*.md'`
no output

`git diff 45332ff810^1 45332ff810 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### a5c6216339

`git diff a5c6216339^1 a5c6216339 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff a5c6216339^1 a5c6216339 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff a5c6216339^1 a5c6216339 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff a5c6216339^1 a5c6216339 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff a5c6216339^1 a5c6216339 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff a5c6216339^1 a5c6216339 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff a5c6216339^1 a5c6216339 --stat | tail -1`
```
 4 files changed, 346 insertions(+), 25 deletions(-)
```

`git diff a5c6216339^1 a5c6216339 -- '*.py' | grep -cE '^\+\s*def test_'`
```
7
```

`git diff a5c6216339^1 a5c6216339 --name-only -- 'docs/*' '*.md'`
```
docs/operator_guide/scheduler.md
```

`git diff a5c6216339^1 a5c6216339 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 2fcfe8afcb

`git rev-list --parents -n1 2fcfe8afcb`: 3 hashes (merge commit, two parents)
`git diff --name-only 2fcfe8afcb^1 2fcfe8afcb`: 2 files, all under docs/plans/

#### 87a58a81ef

`git rev-list --parents -n1 87a58a81ef`: 3 hashes (merge commit, two parents)
`git diff --name-only 87a58a81ef^1 87a58a81ef`: 3 files, all under docs/plans/

#### a16d32aecd

`git rev-list --parents -n1 a16d32aecd`: 3 hashes (merge commit, two parents)
`git diff --name-only a16d32aecd^1 a16d32aecd`: 2 files, all under docs/plans/

#### b93539322c

`git rev-list --parents -n1 b93539322c`: 3 hashes (merge commit, two parents)
`git diff --name-only b93539322c^1 b93539322c`: 2 files, all under docs/plans/

#### c273627a28

`git rev-list --parents -n1 c273627a28`: 3 hashes (merge commit, two parents)
`git diff --name-only c273627a28^1 c273627a28`: 2 files, all under docs/plans/

#### 9480550630

`git rev-list --parents -n1 9480550630`: 3 hashes (merge commit, two parents)
`git diff --name-only 9480550630^1 9480550630`: 2 files, all under docs/plans/

#### 8fc8ba961a

`git rev-list --parents -n1 8fc8ba961a`: 3 hashes (merge commit, two parents)
`git diff --name-only 8fc8ba961a^1 8fc8ba961a`: 2 files, all under docs/plans/

#### 2b767a9b1f

`git rev-list --parents -n1 2b767a9b1f`: 3 hashes (merge commit, two parents)
`git diff --name-only 2b767a9b1f^1 2b767a9b1f`: 3 files, all under docs/plans/

#### 597312d2a7

`git rev-list --parents -n1 597312d2a7`: 3 hashes (merge commit, two parents)
`git diff --name-only 597312d2a7^1 597312d2a7`: 3 files, all under docs/plans/

#### 3695f168aa

`git rev-list --parents -n1 3695f168aa`: 3 hashes (merge commit, two parents)
`git diff --name-only 3695f168aa^1 3695f168aa`: 3 files, all under docs/plans/


### Step 9c -- style conformance

Verdict: clean on every mechanical rule; two candidates raised, neither a
finding of this plan. Read `shakenfist/mariadb.py` (the cited ranges),
`shakenfist/daemons/database/main.py`, `shakenfist/daemons/resources/main.py:100-145`,
`shakenfist/namespace_claim.py:630-700`, and the per-range diff of all 21
ranges.

Confirmed clean: no new `mariadb.get_all_*(` call site in any range; 0 added
lines over 120 characters outside generated protobuf modules; 0 added trailing
whitespace; no wrong-unit `*_mb` / `*_gb` / `*_cpus` names in the new SQL or
capacity dicts. The three-layer `_direct_*` / `_grpc_*` / public pattern holds
for all ten RPCs these ranges add, each with a servicer handler and a
registered counter; the step tabulated all thirty line numbers.

Two candidates, dispositioned below as **pre-existing** and **declined**: the
two triple-single-quoted SQL literals, and `cpu_schedulable`'s unit-free name.

### Step 9d -- code quality, G1 and G2

Verdict: **no blocking finding.** The admission transaction, the release
mirror, the reconciler, the claim CRUD transactions and the scheduler's guard
stages are correct on all four questions asked, and unusually well documented
about why. Four advisory findings.

1. **The guarded `UPDATE` is the first statement on every path.** Each
   transaction these ranges added was named with its first statement:
   `_direct_reconcile_scheduler_capacity` opens on the claim-expiry `UPDATE`
   (position 0 pinned by a test), `_direct_admit_instance_placement` on the
   guarded claim or cluster `UPDATE` -- or, on a move, on `_claim_node()`'s
   guarded node `UPDATE` or `_floored_node_decrement()`, whose own first
   statement is its guarded `UPDATE` and not its following `SELECT` -- and the
   three claim CRUD transactions on the guarded `cluster_capacity` `UPDATE`.
   Every probe runs on its own connection. Phase 1's and phase 2's
   `engine.begin()` blocks are `ALTER TABLE` migrations, so the rule does not
   apply to them.
2. **Nothing leaks between drawdown and placement, because there is no
   between.** The guarded drawdown, the `placement` attribute write and the
   `instance_location` insert are one transaction, and the reference rows are
   the sole authority for release. Death after commit leaves a charged,
   referenced instance, which is a correct ledger; the reconciler drops it the
   pass after its state becomes `deleted`. Branch switching is symmetric in
   both directions, so release always finds the charge on the branch it
   decrements.
3. **Both absent-row choices match the guide**, per pool and per the two
   different answers it documents.
4. **No guard re-derives usage.** The SQL guards compare only materialised
   columns, and the scheduler's pre-filters read the ledger rather than
   recomputing it. One honest exception is named: claim create and grow bind a
   re-derived `migrated` migration term into the guard, which the design
   requires, is floored, and is reconciled within a pass. The quantity
   compared against the limit is still the ledger.

### Step 9e -- code quality, G3, G4 and G6

Verdict: one finding raised as blocking and **withdrawn on adjudication** (see
*The disagreement* below); one advisory. Four of its five questions came back
correct.

- `CLAIM_ENFORCEMENT_HARD` has exactly **one** consumer,
  `shakenfist/mariadb.py:30611`, read once and used once. There is no second
  reader to disagree with it.
- **Nothing assumes the two classified stage sets partition the stage space**,
  which was the defect shape this step was sent to hunt. No reader tests
  `PERMANENT_CAPACITY_STAGES` membership at all, `constants.py:406` is
  one-sided and defaults False, and
  `shakenfist/tests/external_api/test_capacity_error.py:301-342` AST-walks
  `scheduler.py` and asserts every stage reaching `capacity_error()` is
  classified, with the claim stage excluded from both sides deliberately. The
  contract is actively guarded, not merely correct.
- The affinity scorer matches phase 6's binary model, and mixed weighted and
  binary specs are refused at validation, so no spec is half-dropped.
- `floor(load)` is gone, and a correction to this plan's premise: range
  `113e4efb57` itself replaced it; phase 6 added the RAM band later.
- The CI claim sizing cannot produce a claim smaller than the instance it
  charges, because an explicit equality assertion against the live footprint
  fires by name if it ever would.

### Step 9f -- code quality, G5, G7 and G8

Verdict: no blocking finding. **The three orphan defect fixes -- the ranges
belonging to no phase row, and the reason G8 exists -- are all still in place
at `$TIP`**, each with the lines read cited: #3722's affinity-above-load-shedding
ordering and its "may narrow but never move out of the tier" fallback survive
verbatim, and phase 6's binary model did not break it because with no scoring
tags the preferred tier is the whole candidate set; #3918's headroom-aware
retry seam is intact and a later plan generalised it rather than undoing it;
and #3937's RAM-aware gate and ranking are intact and extended, reindented by
another plan but semantically unchanged.

Also verified correct: `/admin/resources` and `GET /admin/claims` both match
their published examples key for key, and the resources one is machine-pinned;
`coverage_state` is still filtered in SQL with no Python-side filter
reintroduced; and `EXCLUSION_REASONS` is genuinely closed -- established by
grepping every *write* into the `excluded` mapping rather than every `'reason'`
key, finding two sources and four constants, pinned twice including a
deliberate independent copy in the cluster tests.

**One result worth recording as a positive.** `tools/ci_headroom_report.py`
shows phase 5b's sweep done properly: its nine lines there corrected six
separate sites -- the docstring, the constant comment, `GuardCensus`,
`guard_record()`, `print_guard_census()` and `print_verdict()` -- and a grep
for `advisory` or `CLAIM_ENFORCEMENT_HARD` over the file at `$TIP` returns
nothing. That is the same sweep which missed three sites elsewhere, so the
record holds both halves.

### Step 9g -- test review

Verdict: coverage is sound on the questions that matter, with three advisory
findings and **one premise of this plan overturned**.

The substrate map came back as three layers, not the two the brief assumed:
six `*_live` modules asserting real SQL, each skipped without
`SF_MARIADB_TEST_DSN` and run in the `schema_enum_widening` job; compiled
statement text against a mocked DBAPI, which is what the non-live capacity
modules assert; and `MockMariaDB`, a Python reimplementation of admission on
which everything caller-side rests.

The claims API surface has both unit and real cluster coverage, including the
adversarial set -- admin-only, unauthenticated, a claim not readable through
another namespace, an unknown namespace or claim. There is no merge-queue
lane-selection hole: the merge caller selects the lane the cluster
configuration runs on, and a seam test asserts every lane runs in exactly one
place and that weekly failures file an issue.

### Step 9h -- documentation review

Verdict: **two blocking findings, two advisory**, and the audit's central
question produced hits on exactly the axis predicted -- whether a claim ceiling
is advisory or binding. Phase 5b swept the *word* "advisory" and updated six
pages; it missed two paraphrases of the same fact which do not contain the
word.

No fifth "advisory" about claim ceilings exists: every hit at `$TIP` is one of
the four known traps plus one about blob accounting.

**The `CLAUDE.md` fold question (F6) is answered: nothing this plan recorded
was lost.** Four ranges edited `CLAUDE.md`, which was folded into `AGENTS.md`
and `docs/` on 2026-09-28. `AGENTS.md` carries none of them, which is correct
-- all four were reference material the documentation policy sends to `docs/`,
and each survived there: the typed `node_metrics` projection and the
`is_hypervisor` gotcha, the three capacity tables and the
reconciler/guarded-UPDATE split, the placement invariant that
`Instance.place_instance()` is the sole caller and `Node` has no
placement-writing helpers -- the one genuine agent-facing invariant, intact --
and the `state` versus `coverage_state` distinction. The single convention
that is gone, phase 4's "ceilings are advisory this release, do not add an
enforcement knob or a 403 path before it does", was correctly retired by phase
5b, which did exactly what it reserved.

Checked mechanically and clean: 621 backticked identifiers extracted from
eleven pages and `grep -w`'d against `shakenfist/` and `protos/` at `$TIP`,
with the only non-matches belonging to `client-python` or other plans. The
403/507/503/409 matrix agrees across five pages; the absent-counter-row rule
agrees between two; the glossary's capacity-ledger definition matches the
`cluster_capacity` columns with no held or peak drift.

### Step 9i -- security review

Verdict: **the ledger's authorisation is correct.** One low disclosure
finding, three informational notes. No critical, high or medium finding.

- **Reachability holds.** `caller_has_admin_rights()` is the single predicate
  and every claim verb carries it -- the two claims endpoints' five methods,
  the events endpoint, `GET /admin/claims`, and `GetClusterCapacity`'s only
  REST exposure through `/admin/resources`. The scope axis is real rather than
  decorative: a token needs the admin namespace *and* the scope derived for
  the resource. The endpoint-authentication pre-commit hook enforces
  subclassing and decorator order rather than authorisation, and these
  endpoints satisfy it substantively -- no `@public`, and the public set is
  pinned at four by a test.
- **Cross-namespace claim mutation holds, including the body-override shape.**
  A non-admin cannot reach any claim verb, which a cluster test asserts live by
  expecting 401 from a namespace's own credential. The body does merge over
  path arguments, but the overlap is recorded and answered 400 by default; and
  even with the operator's rollback to warn or off there is no escalation,
  because an override only changes which namespace all three decorators test,
  and a claim whose own namespace differs 404s with the same response a
  missing claim gives, so the URL is not an oracle. On the drawdown side the
  instance's own namespace is passed, never a request value.
- **SQL holds.** Every claim and admission statement is expression language;
  uuids are parsed and quantities bound. The f-strings are `Table.c` lookups
  keyed by a fixed module constant, so no table, column or namespace name
  reaches SQL text. The two `sa.text()` statements in the claim path splice a
  module constant and bind the namespace. Phase 7a's `coverage_state` filter is
  bound and allowlisted twice, with a test pinning the rendered placeholder.
- **Quantities hold** against negative, non-integer and credit-producing
  values, guarded in three independent places -- the declared schema, the
  handler, and the database layer -- with `BigInteger` columns and every
  decrement floored.
- **The disclosure question is answered:** no refusal body and no
  claim-endpoint audit event tells a caller about another namespace's
  footprint, cluster-wide totals or node names. Phase 5b's 403 names a claim
  and recommends growing it; the only ways that claim is not the caller's own
  are that the caller is the system namespace, or that the target namespace
  had already trusted the caller to create instances there. The one gap is the
  finding below, which is a scheduler event rather than a claim refusal.
- Floor item 3 is clean. The greps are recorded: IPv4 found three hits, all
  placeholders (a disposable local DSN and two RFC1918 CI netblocks);
  credential patterns found only generated gRPC stub keyword names and prose;
  private hostname suffixes found none; and `subprocess` / `os.system` /
  `shell=True` found zero.

### Step 9j -- the guarded-counter prose re-check

Verdict: **no claim is stale.** Nineteen claims in
`docs/developer_guide/database_internals.md:238-407` were checked at their
definitions, eighteen "still true" with the lines read cited and one recorded
unverifiable -- the phase 0 benchmark's historical measurements, deliberately
not re-run, whose structural claims do check out. One loose wording, filed
below. The section's three MariaDB error numbers match
`_TRANSIENT_TRANSACTION_ERRNOS` exactly, no more and no fewer, and
`_retry_on_deadlock()` still retries only the deadlock errno, which is what
makes the section's warning not to widen it correct.

### Step 9k -- the cross-phase lens

Verdict: one blocking finding (the client capability gap, which 9l reached
independently), three advisory, and the four named candidates for "what did one
phase assume that another changed" all came back **they agree**, with
confirming lines cited.

**The enumeration method for question 1**, which this plan required to be
stated rather than summarised: for each range, `git diff <sha>^1 <sha> -- .
':(exclude)docs/plans'` filtered to added lines, then one grep per category --
added quoted lowercase literals in the single advertising module for capability
tokens; added `*_STAGE` / `*_STAGES` lines in `constants.py` plus a literal
sweep for the two stage names; added metric constructor lines, with each name
then grepped against `docs/`; added screaming-case assignments in `config.py`
for options; per-range counts for each named closed set; added quoted event
messages outside tests; and the per-range file list under `external_api/`
plus a tree-wide sweep for `coverage_state`. Each result was then resolved
three ways -- what advertises it, what consumes it, and whether a test pins it.

The inventory it produced: six capability tokens, all advertised at `$TIP` and
all six pinned in one test; four exclusion-reason strings and their set, pinned
twice; the claim stage, unclassified by design and pinned; eleven Prometheus
metrics; seven configuration options; and the `coverage_state` filter,
advertised, consumed and documented in four places.

On the four assumption candidates: the cores-to-threads change agrees, and the
knob that states its unit replaced the three that did not in a commit outside
these ranges; the #3813 demand-guard fix is in place and not reintroduced, with
both sides of the comparison still node state; the load ordering, the binary
affinity model and #3722's re-ranking compose in one contiguous block whose
comments name each other; and phase 5's consolidation survived another plan's
generalisation, because that generalisation landed *upstream* of the walk and
its multi-shape callers do not use it.

### Step 9l -- the client-python floor checklist

Verdict: the five floor items pass or do not apply, and one advisory finding
which 9k reached independently. Range `135ab53042` (client-python#375) only
adds, with zero deletions, so item 4 has no orphaned consumer to find; it
touches nothing under `.github/`, so item 2 does not apply; it changes no
workflow, packaging or version metadata, so item 5 does not apply; and item 3
is clean, with the only credential-shaped hits a fixture bearer token and an
example URL.

No verb or rendering path prints a credential. The renderers index the
server's always-present keys directly, which is a documented design choice
rather than a crash risk, and phase 7b's own audit ran 78 payload-stripping
cases over the claim views.

**Phase 7b is cited, not re-run**, per the shared block. Its merge commit is
`d484fdc63c` (client-python#442, merged 2026-10-28) and its five verdicts are
in a *Push audit* section of its own plan file in that repository. Two caveats
this audit records: it audited `origin/develop...HEAD`, a range *wider* than
its own work which also covered client-python#433 to #438, so nothing of 7b
went unaudited but other pull requests were swept in; and its item 1 read "Not
yet applicable" because the phase had not merged when it was written. That item
is now answerable, and the answer is `d484fdc63c`.

## The disagreement, and how it was resolved

Two lenses reached opposite verdicts on the same site, which is worth recording
in full because the site still looks wrong to a careful reader.

**9e reported, as blocking:** `shakenfist/operations/node_inst_netdesc_op.py:302`
raises `stage=CAPACITY_GUARD_STAGE` unconditionally when every candidate
refused, with no test of whether the refusals were all claim-stage. Since
`capacity_guard` is transient and `capacity_claim` is not, a pure claim refusal
on the reschedule path would be published to the instance as retryable --
exactly what `shakenfist/constants.py:363-373` says must not happen ("a claim
refusal which inherited it would promise a retry that can never succeed"). The
create path honours the distinction through `_claim_only_denial`; this sibling
caller does not, and both cite the same issue number, so it is demonstrably the
same contract. The management session confirmed all of that, and confirmed that
`place_on_candidates()` surfaces a `claim` key in its denials.

**9k reached the opposite verdict** and cited the branch that settles it.

**9k is right, and the finding is withdrawn.** `shakenfist/mariadb.py:30643`
reads `if old_node_key is not None: pass`, under a comment saying a move "skips
[the cluster or claim guard] entirely rather than washing an increment against
a decrement" and "is likewise not refusable at this stage: there is nothing new
to refuse". A reschedule is always a move: `instance.py:1464` derives
`old_location` from the instance's existing placement, and
`node_inst_netdesc_op.py:267-269` builds its candidate list by *excluding* the
node running the operation, which is the node the instance is currently placed
on. So `old_node_key` is non-`None` on every reschedule, the claim guard never
runs, no claim-stage denial can arise, and publishing `CAPACITY_GUARD_STAGE`
there is correct. It is designed behaviour pinned in both substrates:
`test_a_move_does_not_draw_down_a_claim_either`
(`shakenfist/tests/test_mariadb_capacity_admission.py:1156`) and
`test_a_move_in_a_claimed_namespace_reports_nothing`
(`..._live.py:999`).

What the audit takes from it. The reachability step is the one that decides a
finding of this shape, and it was the step the management session had not taken
when it accepted 9e's report -- confirming that the mechanism exists is
necessary and not sufficient. Two independent lenses went for this site, so a
third reader will too; the advisory below adds the one comment that stops them.

### Disposition

Every finding has one of five dispositions. Each carries the file, the line,
the range and a line confirmed at `$TIP`, or the classification *already
fixed*; D3 rejects a finding without one.

**Blocking -- fixed in #5119, the follow-up pull request.**
Graded blocking on Michael's decision of 2026-11-28: an operator guide which
states the feature backwards is a defect in shipped output, even though no
production code is wrong. D7 keeps the fix out of this pull request.

| # | Site | Range | Why |
|---|------|-------|-----|
| B1 | `docs/operator_guide/scheduler.md:1087-1088` -- "in this release that is a warning, not a refusal" | `7bd7bbd03c`, falsified by `31f3350c1c` | Contradicted on its own page at `:602-604`, "The ceiling is enforced ... and it binds". It sits in the audit-events list an operator reads while diagnosing a refusal, so it is read by the person deciding whether a claim can turn creates away, and it would tell them to size a claim small without consequence |
| B2 | `docs/developer_guide/subsystem_internals.md:12` -- "hard claim ceilings, caller migration and further SQL pushdown are still to come" | `d3a91cdef5` / `0144e36ede` / `7bd7bbd03c`; `31f3350c1c` edited the page without touching its opening | Ceilings landed in `31f3350c1c` and caller migration in `660d4f65e7`. Contradicted on its own page at `:321` and by `ARCHITECTURE.md:212`. "Still to come" is the strongest available statement that the ceiling does not bind |
| B3 | `docs/operator_guide/capacity_refusals.md:212` -- attributes `capacity_refusal_is_transient()` to `external_api/base.py` | `31f3350c1c` | Defined at `shakenfist/constants.py:391`; `base.py` re-exports the frozensets and calls the function without rebinding the name. One page disagreeing with itself. One line |
| B4 | `docs/developer_guide/database_internals.md:271-274` -- release described as "a decrement floored with `GREATEST(0, used - :x)`" | `d3a91cdef5` | The code tries a guarded `counter >= x` form first and falls back to the clamp only on a miss, logging and reporting the shortfall. Not false, but it presents the fallback as the only path. One sentence |
| B5 | `shakenfist/operations/node_inst_netdesc_op.py:300` -- no comment saying why the stage is unconditional | `31f3350c1c` | Not a defect (see *The disagreement*), but two independent lenses read it as one. One comment saying a reschedule is always a move, and so carries no claim-stage denial, stops the next reader re-deriving three files away |

B3 to B5 are one-line changes grouped with B1 and B2 because they land in the
same pull request and none is worth its own.

**Advisory -- filed as issues.** Grouped by theme on Michael's decision, each
issue carrying the full evidence for its members.

| Issue | Covers |
|-------|--------|
| shakenfist#5110 | The three unobserved cluster-CI skips: `test_scheduler.py:308` and `:319`, and `test_nodes.py`'s reconciler-row skip. The skips are right; being unobserved is the defect. `PLAN-ci-cloud-sizing.md:391` measured one firing in 114 job-runs, so it is not theoretical |
| shakenfist#5111 | The tenant disclosure asymmetry: `external_api/instance.py:1511` and `instance.py:1679` put node identities and cluster totals in the instance's own event log, where `external_api/placement.py:382` gates identical data on admin rights and says why |
| shakenfist#5112 | `source-file-size`: `mariadb.py` at 34,127 lines with +4,326 from this plan, a self-labelled ~6,200-line seam and `mariadb_hard_delete.py` as the precedent; plus `scheduler.py` crossing 1,500 at phase 5a unexplained |
| shakenfist#5113 | Published contracts kept correct by hand: `CLUSTER_CAPACITY_FIGURES` unpinned, `admin_claims_get_example` and `claim_example` untested where their sibling is pinned, `mock_mariadb.py`'s admission double with no parity test, and the opt-in nature of the ordering assertions |
| shakenfist#5114 | Reconciler behaviour: the pass has no transient-error retry where every sibling transaction does, and an expired claim's `used_*` freezes and is still published |
| shakenfist#5115 | Duplicated helpers: the dimensions tuple in two modules, and `_admission_denial_dimensions` against `_cluster_capacity_dimensions` |
| shakenfist#5116 | Scheduler ranking arithmetic: the RAM band ignores `held_memory_mb` where the gate charges it, and two pressures share one 0.25 scale with different full points. The held term is another plan's, so the fix likely belongs there |
| client-python#464 | The claim verbs check no capability, where two later claim methods in the same file do. The server publishes `namespace-claims` expressly so a client can degrade |

**Declined, with the reason recorded.**

`shakenfist/daemons/resources/main.py:136`, `cpu_schedulable`, range
`113e4efb57`: a published metric name in threads sitting beside
`cpu_cores_schedulable`, which states its unit. Declined because the unit is
not silent -- it is stated in the `_compute_reservations` docstring, in
`ARCHITECTURE.md:198` and in `docs/developer_guide/subsystem_internals.md:22`
and `:88` -- and because it is a published key with real consumers, including
the CI sizing formula stated in terms of it at
`docs/developer_guide/ci_cloud_sizing.md:26`, `:40` and `:52`. Renaming it
would break those to fix a name whose unit is documented three times.

**Pre-existing, so issues rather than findings -- and in both cases already
tracked, so neither was filed.** The push-audit rule says a problem which
predates the plan is not a finding of it.

* Triple-single-quoted strings, forbidden by the convention, at
  `shakenfist/mariadb.py:28246` and `:28330`. The sweep is what changes the
  verdict: `grep -c "'''"` on that file reads 44 today and **18 on
  `ea3c9bf632`, before phase 0 merged**, and seven files outside tests carry
  the form. The convention was already broken in this very file before the
  plan began. Converting two and leaving forty-two would be theatre.
* Plan and decision citations in production code -- eight plan-file pointers
  and roughly thirty bare decision labels from these ranges.
  `python3 tools/check-plan-phase-references.py` reports **370** such
  citations repository-wide, across at least five unrelated plans, and its
  own configuration makes this class advisory "while the backlog of those is
  worked off", prescribing "fix the ones in the files you touch". This plan's
  share is a fraction of a tooling-tracked backlog with a stated remedy.

**Withdrawn.** The reschedule-path claim stage, raised blocking by 9e. See
*The disagreement* for why it is correct, and B5 for the comment that records
it in the code.

**Out of scope -- reported, not filed against this plan.**

* The RAM ranking's missing held term is in this plan's range but the `held_*`
  counters are `PLAN-placeable-capacity`'s, so shakenfist#5116 says the fix
  likely belongs there.
* `sf-database`'s gRPC listener is unauthenticated, so the claim CRUD RPCs
  carry no caller identity and mesh access bypasses the admin check. These
  ranges added RPCs to an existing tier with that property; the exposure is
  `PLAN-sf-as-infra`'s, behind mTLS.
* `client-python` builds claim URLs by concatenation without quoting. That is
  the client-wide pattern -- 56 such concatenations and no `quote()` calls --
  and not something this range introduced.
* A claim limit has no upper bound. The worst case is a 500 for an
  already-admin caller, with no credit and no wraparound, because the column
  raises out-of-range rather than wrapping. Informational.
* `shakenfist/tests/test_mariadb_capacity_holds.py` carries the strongest
  ordering assertions in the tree but belongs to `PLAN-placeable-capacity`;
  read, nothing filed.

**Future work, recorded rather than filed.**

* The `namespace-claim-events` capability token is advertised but named on no
  documentation page -- the inverted form of the defect this phase exists for.
  Low confidence, because `docs/` does not catalogue tokens exhaustively.
* The documented claim-verification workflow at
  `docs/operator_guide/scheduler.md:843` pairs phase 4b's command with phase
  7a's ledger and is exercised end to end by nothing. The underlying behaviour
  is sound -- claim create increments `claimed_*` synchronously in the guarded
  `UPDATE` -- so this is a coverage gap in a documented workflow, not a wrong
  document.
* `client-python` should carry its own `PUSH-AUDIT.md`. A plan phase landing
  there has only the shared block's five-item floor to apply, which is a floor
  and not a runbook. Recorded for that repository, as
  `PLAN-transient-capacity-refusals` phase 7 did before it; not added here,
  because writing one is a change to another repository's conventions.

## Execution notes

**2026-11-28 (AEDT).** The phase ran in one sitting. Step 9a was the planning
commit; 9b ran alone, because it writes into this file and runs the tree-level
checks; 9c to 9l then ran in parallel as ten read-only lenses; 9m is this
record.

**The D4 decision was confirmed before 9b started**, as the back brief
required: wave 1's tree-level commands ran once at `$TIP` and none of the
thirty-one merge commits was checked out. The three-range historical sample the
plan offered as the cheap alternative was not taken.

**Michael took two gradings at the second gate.** The documentation findings
are blocking rather than advisory, so they are fixed in a follow-up pull
request before the phase closes; and the advisory findings are grouped by
theme into eight issues rather than filed one apiece.

Five corrections to this plan and the master plan, made at their source as the
steps proved them wrong.

1. **D10's premise was wrong, for an instructive reason.** It told 9j to start
   with "the sentences phase 5b moved under", and the master plan's phase 9
   section says the re-check is needed "because phase 5 changes the admission
   path those names describe". The guarded-counter section is **silent** on
   enforcement: a grep of `:238-407` for `CLAIM_ENFORCEMENT_HARD`, `403`,
   `507`, `advisory` or `binding` returns nothing. The section documents the
   *primitive* -- a zero-row result from a guarded `UPDATE` is a refusal -- not
   the *policy* of what the API does with one. The flip changed the policy and
   left the primitive alone, so there was nothing there for it to falsify. The
   re-check was still worth running; it came back clean because the prose was
   written at the right altitude.
2. **The ER_CHECKREAD invariant is not invisible to CI.** The 9g row, and this
   plan's own framing, said it cannot fail there because CI runs MariaDB 10.11.
   Both halves are wrong at `$TIP`. It is asserted directly on mocks, needing
   no database, in `test_mariadb_capacity_admission.py:1586` over five shapes
   and in `test_mariadb_capacity_claims.py` over create, update and delete. And
   a server which does raise it runs in CI: the `schema_enum_widening` job runs
   the live suite on Debian 13, and `tools/ci-enum-widening-test.sh` *refuses
   to run* unless `@@GLOBAL.innodb_snapshot_isolation` is 1, so a runner-image
   rebuild cannot silently drop the regime, while a later check rejects a
   zero-test green tick.
3. **D6 put `a005fba498` in G7 as a code-bearing range, and it carries no
   code.** All six of its files outside `docs/plans/` are markdown, with no
   added test functions. It was 9h's subject entirely and should not have been
   given to a code-quality lens. The range count is unaffected; the group
   assignment was wrong.
4. **`113e4efb57` replaced `floor(load)` itself.** D-level framing and the 9e
   row implied phase 6 did it and phase 00a was the old form to hunt for.
   Phase 6 added the RAM band later. Classified already fixed.
5. **The master plan's phase 9 range instruction was superseded**, which this
   phase's planning commit corrected at source. F1 has the detail.

**One management-session error, recorded because the audit's own credibility
rests on saying so.** 9e's blocking finding was confirmed and reported to
Michael as the audit's headline before 9k landed and contradicted it. The
confirmation checked that claim denials reach the caller, which is necessary
and not sufficient; it did not check whether the claim guard runs on a move,
which is the step that decides it. *The disagreement* records the adjudication.

**2026-11-28 (AEDT), the same day.** The audit record merged as `4aac34728b`
(#5117). The five blocking corrections then landed as #5119, one commit per
finding, and that pull request carries this close-out: the phase and the plan
reach `Complete` there rather than here, because D9 holds them open while a
finding has no disposition and the corrections did not exist when the record
was written. Each correction was re-swept after it was made, since the defect
being fixed was a keyword sweep missing paraphrases: every surviving use of
"advisory" in `docs/` is about the placement query, which is advisory by
construction, and the one remaining "still to come" outside the plans is
`ARCHITECTURE.md`'s, on SQL pre-filter pushdown, which is accurate.

## Outcome

**The audit found no blocking defect in the plan's production code, and it
found three places where one phase falsified another and nobody noticed.** On
the question the phase was created to ask -- what did eighteen phases do to
each other -- the answer is mostly "they agree", with the agreements cited
rather than asserted. On the narrower question of whether anything was
published that nothing consumes, the answer is yes, once, on the client side.

**What was read.** Thirty-one in-repository merge ranges and one in
`client-python`, run per range and pooled per `PUSH-AUDIT.md`'s range rule
rather than as the cumulative union the master plan originally asked for.
Twenty-one of the thirty-one carry code; ten contain nothing but plan files and
got the two checks that scope them out. Two hundred and ten grep-level
commands are recorded with their literal output, the tree-level checks ran once
at `$TIP` and all passed, and ten judgement lenses read the code in the nine
groups D6 fixed.

**The pattern worth carrying forward.** Phase 5b landed the hard-ceiling flip
with a sweep for the word "advisory", and that sweep is the audit's single
richest seam -- in both directions. It was done thoroughly in
`tools/ci_headroom_report.py`, where nine lines corrected six separate sites
and nothing survives. It missed two pages where the same fact is paraphrased
without the word: one page now says a claim exceedance is "a warning, not a
refusal" four hundred lines from its own heading "The ceiling is enforced", and
another says hard claim ceilings are "still to come" three hundred lines from
"Claim ceilings are enforced". Neither is reachable by grepping for a keyword,
and neither per-phase review could have seen it, because each page was correct
when its own phase edited it. That is the whole argument for this phase
existing, demonstrated twice.

**The defect this phase was created by is fixed, and its mirror image is not.**
Phase 4 shipped the claims API without advertising `auth: namespace-claims`,
found by a passing question at close-out. The string is now advertised and
pinned by a test. What nobody checked is whether anything *reads* it: the
client's five base claim verbs gate on no capability at all, while two later
claim methods in the same file gate on their own tokens. The asymmetry is
visible inside a single function. So the plan learned to publish its contracts
and did not learn to consume them, which is the same lesson from the other
side.

**Where the code is strong, said plainly, because an audit that only lists
faults misreports what it read.** The admission transaction, its release
mirror, the reconciler and the three claim CRUD transactions are correct on
every question put to them, and are unusually well documented about why -- the
guarded-`UPDATE`-first invariant holds on every path, including the two where
the first statement is not the obvious one; nothing leaks between drawdown and
placement because they are one transaction; both of the reconciler's
deliberately different absent-row choices match the guide; and no guard
re-derives usage, with the one re-derivation that exists named and justified.
The authorisation is correct, including against a body-override attack that is
real in mechanism and closed in three independent places. The stage
classification that phase 5b deliberately left as neither transient nor
permanent is not merely unbroken but actively guarded by a test that AST-walks
the scheduler to prove every stage is classified. And all nineteen claims in
the developer guide's guarded-counter pattern section are still true.

**The three orphan defect fixes -- the ranges belonging to no phase row, which
no phase review ever covered -- are all still in place**, each verified by
reading the lines rather than by assuming, including the one whose mechanism
phase 6's binary affinity model could plausibly have broken and did not.

**One lens contradicted another and the deeper one was right.** The record
keeps both, because the site in question still reads as a defect and the reason
it is not lives three files away in a `pass` branch. The management session had
confirmed the wrong verdict before the contradiction arrived; that is recorded
in the execution notes rather than quietly corrected.

**What was open, and how it closed.** Five documentation and comment
corrections, graded blocking, landed in #5119: one commit each, four pages
corrected and one comment added, with no change in behaviour. That pull
request also carries this close-out, because the phase and the plan could not
reach `Complete` until the corrections existed. Eight issues carry the
advisory findings. Two
findings dissolved under a sweep and became nothing: both turned out to be
repository-wide conditions older than the plan, one of them already tracked by
the repository's own tooling with a stated remedy.

### Definition of done

Every item passes. Items 12 and 14 were met by #5119 rather than by the audit
record: item 12's follow-up pull request did not exist when the record was
written, and item 14 could not read `Complete` until it did, per D9. Both
rows below say how they were closed.

| # | Result | How it was checked |
|---|--------|--------------------|
| 1 | **pass** | `grep -cE '^#### [0-9a-f]{10}'` on this file reads **31** -- the 21 code-bearing and 10 plan-only in-repository ranges. The two `client-python` ranges are named in prose in step 9l's section, one audited and one cited |
| 2 | **pass** | Set by the 9a close-out commit: phase 5b reads `31f3350c1c` (#5087) and phase 4b also names `client-python 135ab53042 (#375)`. Phase 9's is the only `—` left |
| 3 | **pass** | Phase 5b's item 17 now reads "pass, after the fact", with merge-queue run `45877310889`, job `140107936676`, the Debian 13 cluster lane, and an explicit note that `Ubuntu 24.04 cluster` was skipped so the answer covers one cluster platform and not both |
| 4 | **pass** | The master plan's phase 9 section carries a *Corrected 2026-11-28* paragraph describing the superseded instruction and stating the range rule instead |
| 5 | **pass** | 9b ran all four at `$TIP`: `pre-commit run --all-files` exit 0, `tox` exit 0, `tools/mermaid-lint.sh` exit 0 and `git diff --exit-code shakenfist/protos` clean after `tox -e genprotos`. The management session independently re-ran `pre-commit run --all-files` (all 17 hooks) and `tools/mermaid-lint.sh`, reading the latter's status from the command itself rather than through a pipe. No hook was retried and no flake accepted |
| 6 | **pass** | `grep -cE '^.git diff [0-9a-f]{10}\^1 '` reads **210** -- ten commands for each of the 21 code-bearing ranges |
| 7 | **pass** | `grep -cE '^.git rev-list --parents -n1 '` reads **10**. The management session re-derived all ten independently: every one prints three hashes, and every one's diff lists zero paths outside `docs/plans/` |
| 8 | **pass** | `grep -cE '^### Step 9[c-l] '` reads **10**. Each names the files it read, including the four whose answer was that they found nothing |
| 9 | **pass** | 9j reports 18 "still true" with the lines read and 1 "unverifiable" (the phase 0 benchmark's historical measurements, deliberately not re-run), and the error-number comparison reads `[1213, 1205, 1020]` against `frozenset((1213, 1205, 1020))`, which the management session confirmed at `mariadb.py:29282` |
| 10 | **pass** | 9k's three questions are answered under separate headings, the question 1 enumeration method is stated as seven per-category greps resolved three ways, and the four question 2 candidates are each answered "they agree" with confirming lines |
| 11 | **pass** | Every row of the Disposition section carries file, line, range, grade and disposition, with a `$TIP` line or the *already fixed* classification. Nothing is recorded without one |
| 12 | **pass** | Every advisory finding names its issue: shakenfist#5110 to #5116 and client-python#464. The five blocking findings are fixed in #5119, one commit per finding, which is where D7 puts them; `In progress` here until that existed |
| 13 | **pass** | Recorded below under *The struck management-checklist items* |
| 14 | **pass** | `python3 tools/check-plan-status.py` exits 0 and the table and `index.md` agree. With B1 to B5 landed, `index.md` reads `Complete` and `18 of 18` and the master plan's phase 9 row reads `Complete` with no `Merged` cell, which is what D9 asks of the last row |
| 15 | **pass** | `grep -c 'ea3c9bf63' docs/plans/PLAN-scheduler-reservations.md` reads **1**, and that line sits inside the *Corrected 2026-11-28* paragraph, so the superseded range is described there rather than instructed |

### The struck management-checklist items

`PUSH-AUDIT.md`'s management checklist ends with two items which do not apply
to an audit of merged history, and which D8 strikes rather than ticks:

* ~~Commit history is clean.~~ Thirty-one merge commits landed over four
  months. There is no history to tidy.
* ~~Branch is up to date.~~ Nothing here is rebaseable.

Reporting either as a pass would be claiming a check that was not performed.
