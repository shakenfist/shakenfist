# Phase 5: push audit of the deleted-object sweep

Parent plan:
[PLAN-deleted-object-sweep.md](PLAN-deleted-object-sweep.md).

**Planning effort:** medium. **Review effort:** medium. The master plan
says only "run `PUSH-AUDIT.md` over the merged ranges recorded in the
table above", and the runbook supplies the headings, so the planning
judgement is in three places: which ranges, what to do about the two
33fl landings the Execution table does not name, and which question is
worth a heading of its own beyond the runbook's. All three are decided
below.

Decisions continue the plan's single sequence: phase 4 used D24-D29, so
this phase starts at **D30**.

## Why this phase exists

Four phases landed in nine days, 20 to 28 October, and every one of
them changed `shakenfist/daemons/cluster/scheduled_tasks.py` and
`shakenfist/mariadb.py`. Phase 1 instrumented the sweep, phase 2
replaced its single FIFO with per-type round-robin work and a paged
read, phase 3 moved 25 of the 29 object types' hard deletes onto a
batch RPC and changed the cadence, and phase 4 raised one
configuration value and wrote the operator guide. Four pull request
reviews each saw one of those; nothing has looked at them as one body
of work.

That matters more here than the shared block's general argument does,
because of what phase 3 did. Moving a type's hard delete onto a batch
means restating, in a declaration, the set of tables that type's
`hard_delete()` used to delete from. A type whose table went missing
in that restatement still hard deletes cleanly, still passes its own
tests, still reports success to the sweep -- and leaks the rows it no
longer removes, for every object of that type, on every cluster. That
is exactly the class of defect a per-phase review is worst at and an
audit of the accumulated diff is best at, and it is why this phase
carries a heading of its own (D35).

## Scope

**In scope.**

* The four merges which put phases 1 to 4 on `develop` (D30), audited
  under every `PUSH-AUDIT.md` heading: wave 1, the wave 2 mechanical
  sweep, the style judgment portion, and 2a to 2d.
* One heading of this plan's own: whether the batch declaration
  removes what each type's pre-batch `hard_delete()` removed (D35).
* The two 33fl pull requests that landed phase 4's steps 4b and 4c,
  under the five-item floor from the `plan-push-audit-phase` shared
  block rather than a runbook, because 33fl has none (D31).
* Three seeded findings the planning survey already has in hand
  (D36).

**Out of scope.**

* **Fixing anything.** Findings are graded and dispositioned; blocking
  ones get a fix branch of their own, as D37 says. The audit's own
  pull request changes only this plan and the master plan.
* **The 25 later commits on `develop` which touch the audited files**
  (survey finding 4). They are other plans' work. Where one of them
  changed something this plan wrote, it is a reason to check a finding
  at `$TIP` rather than something to audit.
* **#4759**, the functional-test flake the faster sweep exposed. It is
  a true consequence of this plan and the master plan's Future work now
  records it, but the fix is in a suite this plan never touched and the
  issue is already filed with three fix directions.
* **#3922** (errored objects are never hard deleted). The gate that
  held it -- phase 4's drain and soak -- has now opened, which the
  master plan's Future work says. It is the next piece of work, not
  part of auditing this one.
* **The live cluster.** Phase 4's readings are complete and recorded;
  this phase reads code.

## What the survey found

The master plan's phase 5 section makes one claim, and it holds:
`PUSH-AUDIT.md` exists at the repository root, 664 lines, and the four
`Merged` cells are populated. Everything below is detail the section
could not have carried, plus the one thing the section did not say.

1. **All four `Merged` cells name merge commits with two parents**, so
   every range takes the `<sha>^1..<sha>` form and none needs the
   runbook's direct-landing or unrecoverable-range treatment:

   | Phase | Merge | PR | Landed | Diff |
   |-------|-------|----|--------|------|
   | 1 | `6537966c00` | #4572 | 2026-10-20 | 11 files, +2,054 -110 |
   | 2 | `dd5723c3db` | #4595 | 2026-10-21 | 22 files, +4,002 -1,090 |
   | 3 | `1090771e64` | #4618 | 2026-10-28 | 43 files, +5,088 -923 |
   | 4 | `1bd914aa02` | #4720 | 2026-10-29 | 5 files, +954 -4 |

   Phase 4's range is documentation and plan files only, which is why
   it is cheap to include and why it still matters: 2c has something to
   read there.

2. **Two of the four ranges change `protos/database.proto`** (phases 2
   and 3, both adding RPCs and regenerating the four
   `shakenfist/protos/database_pb2*` files), so the runbook's
   `tox -e genprotos && git diff --exit-code shakenfist/protos` check
   applies and is not optional. **All four ranges touch markdown**, so
   `tools/mermaid-lint.sh` applies -- and its exit status must be read
   directly, never through a pipe.

3. **The master plan's phase 5 section did not say what to do about
   33fl.** Phase 4's steps 4b and 4c landed in Mach33Labs/33fl as
   #1095 (`8740490f`, 4 files, +258: the `shakenfist_sweep_backlog`
   fact, its tests, the report prompt and the facts documentation) and
   #1094 (`789d3b45`, 1 file, +6 -1: sfcbr's cap). Neither has been
   push-audited, 33fl has no `PUSH-AUDIT.md`, and neither is named in
   a `Merged` cell, because they are steps rather than phases. D31
   decides this.

4. **25 commits have landed on `develop` since phase 4** that touch
   the audited files, two of them the sweep's own code:
   `d6bfe8e51e` ("Mute the hard-delete audit event's log echo",
   2026-10-31) changed `baseobject.py`,
   `daemons/cluster/scheduled_tasks.py` and **this plan's own
   `test_hard_delete_batch_parity.py`**; `d04d5583f5` ("Name the sweep
   queue each log line reports", 2026-11-09) changed
   `scheduled_tasks.py` and `test_daemon_cluster_scheduled_tasks.py`
   and added fields to the pass log line the operator guide describes.
   So a finding read from the diff can be already fixed, and a
   documentation claim phase 4 wrote can have been made stale by
   `d04d5583f5` nine days later. D32 handles both.

5. **The batch parity test is better than its heading needs to
   assume, and still does not answer the question.**
   `shakenfist/tests/test_hard_delete_batch_parity.py` (492 lines)
   declares in its own docstring that its per-type literals are
   "deliberately a second copy" of what each type's hard delete
   removes, so a delete added to or dropped from
   `mariadb.HARD_DELETE_BATCH_FUNCTIONS` (`mariadb.py:26378`) fails
   there. That guards the declaration against *future* drift. It does
   not establish that the literals matched the pre-batch code when
   they were written, because both sides of the comparison were
   authored in the same change. The pre-batch tree is still readable
   at `1090771e64^1`, so this is checkable rather than merely
   arguable. D35.

6. **Open question 1's claim that the namespace cascade "is still
   unmeasured" was false** and is corrected at source in the close-out
   commit, with phase 4's number (0.247 s an object). Open question 5
   had no answer recorded although phase 4 answered it; it now has
   one. Both corrections are in the master plan, not repeated here.

7. **Nothing else in the master plan's phase 5 section was wrong.**
   It is three lines long, which is most of why.

## Decisions

### D30 -- Four ranges, `git diff` only, greps per range and judgement pooled

`$RANGES` means the four ranges in survey finding 1, each written
`git diff <sha>^1 <sha>`. `$TIP` means `develop` at `08c26e343b`, the
commit this branch was cut from.

`git log` is forbidden on these ranges. Each merge brought a phase
branch with its own history, and `git log <sha>^1..<sha>` over phase
3's range lists commits that arrived under it alongside, with no way
for the reader to tell which. The runbook's own range rule exists for
this.

The mechanical greps run **once per range**, with each range's literal
output recorded under its own sub-heading, because that is the only
form in which a reader can check that the audit read all four. The
judgement headings (style, 2a to 2d, and D35's) read the four ranges
**together**, because every defect worth finding here is one the
phases did to each other and a per-range pass cannot see it.

Generated files under `shakenfist/protos/` are exempt from judgement
review; the genprotos check in step 5b covers them.

### D31 -- The two 33fl landings are audited here, under the five-item floor

33fl has no `PUSH-AUDIT.md`, so the shared block's floor applies: the
five numbered items, plus the questions 33fl's blast radius raises.
They are audited in this plan rather than in 33fl, for three reasons.
They are steps of phase 4 and no `Merged` cell names them, so the
block's "audited there, as part of the pull request that lands it"
route is already closed. Their diffs are 258 and 6 lines. And the
record belongs with the plan that caused the change, where the next
reader of this plan will find it.

The questions worth asking of those two beyond the floor, because 33fl
runs unattended against production and this repository does not:

* `#1095` adds a nightly computation over four Prometheus queries. If
  a query returns nothing -- a leadership change inside the hour
  leaves the gauge absent -- does the fact report `unavailable` rather
  than zero, as D26 required and its tests pin? A guard that reads
  absence as zero turns "we cannot see the sweep" into "the sweep is
  perfect", which is the failure the whole regression guard exists to
  prevent.
* `#1094` changes `extra_config`, which is applied by the nightly
  deploy. Is the change inert for every other cluster in the
  inventory, and does it leave `RAM_OVERCOMMIT_RATIO` intact? The
  value is a single JSON string and an edit to it can silently drop a
  sibling setting.
* Does either introduce something that runs unattended and reports no
  failure anywhere a human sees (floor item 2)?

This is step 5i, and it is the only step that reads another
repository. Its report is recorded in this plan.

### D32 -- Every finding is classified against `$TIP`, not against the diff

25 later commits touch the audited files (survey finding 4). Each
finding carries one of:

* **still true** -- confirmed by reading the file at `$TIP`. The
  finding must quote the current line, not the diff's.
* **fixed since** -- naming the commit that fixed it. Still recorded:
  it is evidence about what the phase reviews missed.
* **superseded** -- the code the finding is about no longer exists in
  that shape at `$TIP`.

A report that classifies nothing is rejected and re-run. The
management session spot-checks one "still true" per report against the
file itself before recording it; this is the check that stops an audit
becoming a summary of its own briefs.

### D33 -- Every audit step is read-only except 5b and 5j

Only step 5b (wave 1) runs the suite, and only 5b and the management
session write files. The judgement steps return reports and the
management session records them. Three reasons: the judgement steps
run in parallel in one worktree, and pre-commit's stash is repository
wide, so a parallel writer can revert another's work; a sub-agent that
can edit will fix what it finds and the audit then reports on a tree
nobody reviewed; and the findings have to be graded before anything
changes, which is a decision, not an implementation.

### D34 -- Grading, and what a grade commits to

**Blocking** -- a correctness or security defect reachable on a
supported configuration, data left behind or removed wrongly, or a
documented fact that is false in a way that would mislead an operator
acting on it. Blocking findings are fixed before the plan is Complete.

**Advisory** -- everything else: structure, naming, a test that could
be stronger, a documentation gap that misleads nobody.

Every finding gets a disposition: fixed in #NNNN, filed as #NNNN,
recorded on an existing issue, or declined in writing with a reason.
"Noted" is not a disposition.

### D35 -- The heading of this plan's own: did the batch keep the contract?

Phase 3 moved 25 of 29 object types' hard deletes onto
`mariadb.hard_delete_objects()`, whose per-type table list lives in
`HARD_DELETE_BATCH_FUNCTIONS` (`mariadb.py:26378`). Four types --
instance, namespace, namespace claim, node -- keep per-object
cascades. There is also a fallback, `hard_delete_objects_per_call()`
(`mariadb.py:26692`), reached from `baseobject.py:807` when
sf-database answers UNIMPLEMENTED.

The question is not whether this is well written. It is whether, for
each of the 25 types, the batch removes exactly what that type's
`hard_delete()` removed before phase 3, and whether the three paths
(batch, per-call fallback, per-object cascade) agree on every type they
share. A missing table is a silent row leak of precisely the kind this
plan exists to stop, and the plan's own defence against it
(`test_hard_delete_batch_parity.py`) compares the declaration with
literals written in the same change, so it cannot see an error made at
that moment (survey finding 5).

The pre-batch tree is at `1090771e64^1`, so this is decidable. It is
step 5h, at high effort on opus, and it is the step most likely to
find something.

### D36 -- Three seeded findings

The planning survey already holds three; they enter the pool at 5j
rather than waiting to be rediscovered.

* **S1. Nothing distinguishes a pass that hit the cap from one that
  ran out of time.** Phase 4 measured the median pass at 15.93 s of a
  20 s budget with 26% of passes ending on the budget, and the
  operator guide's new "Draining a backlog" arithmetic -- cap times
  passes a day -- silently over-promises past about 400 a pass. The
  pass log reports `elapsed_seconds` and `remaining` and neither
  answers which limit stopped the pass. Advisory, to be filed.
* **S2. sfcbr's cap is left at 400.** Phase 4 raised it and did not
  put it back, although the guide that phase wrote says a cap above
  arrivals changes nothing at steady state. Advisory; both are in the
  master plan's Future work.
* **S3. #4759 is this plan's doing.** A functional test asserts that a
  completed cluster operation is still listable, which only holds
  while the sweep is slow. Advisory, already filed, and its analysis
  needs correcting: it says the sweep runs every fifteen minutes,
  which phase 3 made untrue.

### D37 -- The plan reaches Complete in this phase's pull request if nothing blocks

If no finding is blocking, step 5j sets phase 5 and the plan to
`Complete` in the master plan's Execution table and in
`docs/plans/index.md`, within this phase's own pull request. Phase 5's
`Merged` cell stays empty, as `plan-push-audit-phase` requires of a
phase that closes itself out.

If something blocks, phase 5 and the plan stay `In progress`, the fix
branch is cut as soon as the finding is graded rather than after this
pull request merges, and the plan is Complete when the fix lands.

### D38 -- Search before filing

Two open issues already live in this area: #3922 (errored objects are
never hard deleted) and #4759. Before filing anything, 5j runs these
and writes them into the disposition so a reviewer can re-run them:

```
gh issue list --state all --search 'hard delete in:title' --limit 50
gh issue list --state all --search 'sweep in:title' --limit 50
gh issue list --state all --search 'deleted object in:title' --limit 50
gh issue list --state all --search 'object_states in:title,body' --limit 50
```

A finding that matches an open issue is recorded there, not refiled.

## Step plan

Every step after 5a takes `$RANGES` and `$TIP` as D30 defines them,
and works in
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/shakenfist-wt-dos-p5`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | management session | none | **Done in the planning commit.** Close out phase 4 (its Execution row, "What phase 4 measured", open questions 1 and 5, the Future work entries, the phase 4 plan's execution notes, the two issue comments, the `plan-blockers` clear), then write this plan and register it in the master plan's Execution table and `docs/plans/index.md`. Two commits: "Close out the deleted-object sweep drain." and "Plan the deleted-object sweep push audit." |
| 5b | low | sonnet | none | Wave 1 and the wave 2 mechanical sweep. Run, in order and each under `timeout 2400`: `pre-commit run --all-files`; `tox`; `tools/mermaid-lint.sh` (all four ranges touch markdown; read its exit status directly, never through a pipe or `tail`); `tox -e genprotos && git diff --exit-code shakenfist/protos` (phases 2 and 3 change `protos/database.proto`). Then, **for each of the four ranges in D30 separately**, run the four style greps from `PUSH-AUDIT.md`'s wave 1 and the six commands of its wave 2 mechanical sweep, substituting `git diff <sha>^1 <sha>` for `$RANGE`. Never use `git log` on a range. Record every command and its literal output, or "no output", in a new `## Findings` → `### Wave 1` section of this plan, one sub-heading per range. The recording format is fixed, because the definition of done counts it: each range gets a `#### <sha>` heading whose second word is the bare merge sha, and under it each command is one line of its own in single backticks beginning `` `git diff <sha>^1 ``, followed by its output. The arithmetic is 10 commands per range (four wave 1 greps, six sweep commands) and 40 in all; the counting `awk` is in the definition of done and was checked against this format at planning time. If `pre-commit` or `tox` fails, record the failing test names, check with `git diff --name-only <sha>^1 <sha>` whether any range touched the failing module, and carry on without fixing anything. Commit subject: `Record wave 1 of the sweep push audit.` |
| 5c | low | sonnet | none | Style conformance, judgment portion, read-only. Apply `PUSH-AUDIT.md`'s "Style conformance — judgment portion" brief to the four ranges together. Skip `shakenfist/protos/*_pb2*`. The rules most at risk here: the three-layer `_direct_*` / `_grpc_*` / public pattern, which phases 2 and 3 each added RPCs under (`mariadb.py:26487`, `:26572`, `:26619` for the batch; `count_objects_by_state` at `:5159`); the SQL-pushdown rule, since the whole plan is about not materialising lists; and the unit-change rule, since phase 1 added timing metrics and phase 4's documentation quotes their units. Confirm each candidate at `$TIP` and classify it under D32. Return findings with file, line, range and grade, or "Style checks passed" plus the files read. |
| 5d | medium | opus | none | 2a code quality, read-only. Apply `PUSH-AUDIT.md`'s 2a brief and all its shared blocks to the four ranges together, taking 5b's mechanical sweep output from this plan's Findings section as input. Where to spend the time: `shakenfist/daemons/cluster/scheduled_tasks.py` (1,685 lines at `$TIP`) holds the sweep's module-level mutable state -- `_DELETED_OBJECT_WORK`, `_DELETED_OBJECT_CURSOR`, `_DELETED_OBJECT_RECHECK_AT`, `_DELETED_OBJECT_NEXT_TYPE` (`:107-110`), `_SWEEP_FAILURE_STREAK` (`:166`), `_SWEEP_RESUME_AFTER` (`:186`) -- which phases 1, 2 and 3 each added to; say whether a lost election or a re-election leaves any of it stale, and whether the cursor can skip an object. `mariadb.py` is 31,241 lines; apply `source-file-size` to it honestly, which means saying whether phases 2 and 3 made it worse and whether there is a seam, not declaring it out of scope. **The phase 8 question** -- what does the change switch on, beyond being correct? Phase 2's refill-per-type and phase 3's cadence fix each made the sweep reach objects it had never reached; #4759 is one consequence already filed, so ask what else now runs that did not. Check every candidate at `$TIP` against survey finding 4's two sweep commits. Return findings with file, line, range, grade and D32 classification. |
| 5e | medium | opus | none | 2b test review, read-only. Apply `PUSH-AUDIT.md`'s 2b brief and its `functional-test-coverage` block to the four ranges together. Three questions beyond the brief. (1) The ranges add `test_mariadb_objects_by_state_page_live.py` and `test_mariadb_hard_delete_objects_live.py`; establish what "live" means here, whether they run in `pre-commit`/`tox` or only somewhere else, and whether the non-live sibling of each proves the same property against a mock that cannot diverge. (2) `shakenfist/tests/mock_mariadb.py` is in phase 3's range: a mock that was taught the batch's shape can make a batch test pass against a mock that is wrong in the same way as the code. Say which assertions rest on the mock and which on real SQL. (3) Phase 2's definition of done claimed a test that fails against the old single FIFO; find it, and say whether it would still fail if the round-robin were reverted, or whether later edits have made it pass either way. Do not duplicate 5h's parity question. Return findings grouped by file, with grades and D32 classifications. |
| 5f | medium | sonnet | none | 2c documentation review, read-only. Apply `PUSH-AUDIT.md`'s 2c brief and all its shared blocks to the four ranges together. The sweep's story is now told in four places in `docs/operator_guide/database.md` -- the three option bullets under "The deleted-object sweep", "Draining a backlog", the metrics under "Watching for sweeps that silently do nothing", and the days-behind check -- plus `docs/developer_guide/database_internals.md`, `docs/developer_guide/subsystem_internals.md` and `docs/release_notes/v07-v08.md`. Check every metric name, label, configuration option, pass-log field and PromQL expression in them against the code at `$TIP`, and in particular against `d04d5583f5`, which added fields to the pass log line nine days after phase 4 described it. Say whether any fact about the sweep -- what the backlog gauge counts, how often a pass runs, what ends a pass, what the default cap is -- is stated differently on two pages. Apply `plan-references-in-code` and `plan-phase-references` to every range's added lines; phase 4's own definition of done ran that grep over `docs` and found nothing, so a hit now is a regression or a gap in that grep. Return findings, or "No documentation gaps found" plus the pages read. |
| 5g | medium | opus | none | 2d security review, read-only. Apply `PUSH-AUDIT.md`'s 2d brief to the four ranges together. The area that matters is **what the batch is allowed to delete**. `_direct_hard_delete_objects()` (`mariadb.py:26487`) resolves a caller-supplied `object_type` into a list of function names via `HARD_DELETE_BATCH_FUNCTIONS` and calls them; establish that an `object_type` outside that mapping is refused before any SQL is built (`hard_delete_objects_request_error` at `:26454` is the candidate), that no table or column name reaches SQL by interpolation, and that the uuid list is bound rather than formatted. Then the authorisation question: the sweep runs as the elected cluster maintainer, so check that the new RPCs (`GetObjectsByState`, `CountObjectsByState`, `HardDeleteObjects`, and phase 2's paged variant) cannot be reached by a caller that should not hard delete, and that a malformed or hostile page cursor cannot be made to walk outside its own object type. Phase 2's paged read takes a keyset cursor from the client. Finally, whether the audit event written after a hard delete (open question 6, and `d6bfe8e51e`, which muted its log echo) leaks anything into a broadly readable place. Return findings with severity, file, line, vulnerability class and recommended fix. |
| 5h | high | opus | none | **The heading of this plan's own** (D35), read-only. For each of the 25 object types in `mariadb.HARD_DELETE_BATCH_FUNCTIONS` (`mariadb.py:26378` at `$TIP`), read that type's `hard_delete()` in the pre-batch tree -- `git show 1090771e64^1:shakenfist/<module>.py`, and `baseobject.py` for the base class's share -- and establish whether the declaration names every table that method deleted from, nothing it did not, and nothing a sibling type's. Report per type: match, missing (a row leak: blocking), extra (a wrong delete: blocking), or reordered-but-equivalent. Then the three paths: `hard_delete_objects()` (`:26619`), the `hard_delete_objects_per_call()` fallback (`:26692`, reached from `baseobject.py:807`), and the four per-object cascades (instance, namespace, namespace claim, node) -- do they agree on every type they share, and is the fallback reachable and covered by a test that exercises it rather than asserting it exists? Say explicitly whether `test_hard_delete_batch_parity.py`'s literals match the pre-batch code or only the declaration (survey finding 5); its docstring claims they are a second copy, and this step is the only thing that can check the claim. Where a type's answer is "match", name the pre-batch method and the lines you read; a verdict with no citation is rejected. A reproduction against the in-memory database in `shakenfist/tests/test_mariadb_hard_delete_objects.py` counts as proof of a mismatch; describe it and the management session runs it. |
| 5i | low | sonnet | worktree (33fl) | The two 33fl landings (D31), read-only. In a worktree of `/srv/kasm_profiles/mikal/vscode/src/mach33labs/33fl` at `master`, read `git diff 789d3b45^1 789d3b45` (sfcbr's cap) and `git diff 8740490f^1 8740490f` (the `shakenfist_sweep_backlog` fact). Apply the five numbered items of the `plan-push-audit-phase` shared block's floor -- quoted in the master plan -- giving each a verdict or a sentence saying why it does not apply, plus D31's three extra questions. Also run 33fl's own checks over the diff's files: `pre-commit run --all-files` and `python3 tools/test_infra_report_precompute.py`. Report per item, naming the files and functions read. |
| 5j | medium | management session | none | Not a sub-agent: grading and disposition are decisions. Pool 5b-5i's findings with D36's S1, S2 and S3. Grade each blocking or advisory (D34), give each a disposition and a D32 classification, and spot-check one "still true" claim per report against the file itself before recording it. Present the graded table before filing anything (see Back brief). Then run D38's four searches, file what is new, correct #4759's fifteen-minute claim, and write a `### Disposition` table into this plan. Add an "Audit of phases 1 to 4" subsection to the master plan's phase 5 section saying what the audit read and what it found, including "nothing" where that is the answer. Set phase 5 and the plan per D37. Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: `Record the sweep push audit disposition.` |

5c to 5i are independent of each other and run in parallel once 5b has
reported. Each read-only step's report is recorded into this plan by the
management session under a `### ` heading named for the step.

## Risks and mitigations

* **The audit rubber-stamps merged code.** It is the standing failure
  of this phase across every plan that has run it, and the database
  load reduction plan's phase 8 is the local proof that it can go the
  other way. *Mitigation:* D32's classification, which forces a quoted
  line at `$TIP`; D30's rule that judgement reads all four ranges
  together; the management session's spot-check; and 5h and 5d each
  carrying the "what does it switch on" question explicitly. 5j
  rejects a report that names no functions.
* **5h is the step that matters and it is the step most likely to come
  back "all 25 match".** A sub-agent asked to compare 25 pairs will be
  tempted to compare the declaration with itself. *Mitigation:* the
  brief requires the pre-batch method name and the lines read for every
  "match" verdict, from a tree it has to check out explicitly, and the
  management session re-reads two of the 25 itself before accepting the
  report.
* **An agent audits another plan's work.** 25 later commits touch these
  files and `git log` over a range lists unrelated commits. *Mitigation:*
  D30 forbids `git log` and every brief names `git diff <sha>^1 <sha>`.
  5j rejects a finding whose file is absent from
  `git diff --name-only` of its stated range.
* **Parallel agents interfere.** pre-commit's stash is repository wide,
  so two writers in one worktree can revert each other. *Mitigation:*
  D33. Only 5b and 5j write or run the suite, and 5i works in a
  separate repository's worktree.
* **`tox` fails for reasons outside these ranges.** It is the full
  matrix on a 31,000 line `mariadb.py`. *Mitigation:* 5b records the
  failure, attributes it or does not, and carries on; the back brief
  gate after 5b means a broken `develop` is reported before wave 2's
  cost is spent.
* **Duplicate issues.** *Mitigation:* D38, with the queries written
  into the disposition.
* **A blocking finding delays the plan by a pull request cycle.** That
  is the cost of D37. *Mitigation:* the fix branch is cut when the
  finding is graded, not when this pull request merges.

## Definition of done

1. `## Findings` in this plan has a sub-heading for each of: wave 1;
   the mechanical sweep; style; 2a; 2b; 2c; 2d; the hard-delete
   contract; the 33fl floor; and disposition. Every heading except
   disposition names the files or functions it examined.
2. Wave 1 records literal output for 16 style-grep runs (four greps
   across four ranges) and 24 sweep runs (six commands across four
   ranges), so 40 recorded commands. The check is that each range's
   sub-heading lists all ten:

   ```
   awk '/^#### /{h=$2} /^`git diff [0-9a-f]/{c[h]++}
        END{for(k in c) print k, c[k]}' \
     docs/plans/PLAN-deleted-object-sweep-phase-05-push-audit.md
   ```

   This prints 10 for every range. It was run against a mock of 5b's
   recording format at planning time, so a zero means 5b departed from
   that format, not that the check is wrong.
3. `tox -e genprotos && git diff --exit-code shakenfist/protos` exits
   0 with the result recorded, or the stale stubs are a recorded
   finding. `tools/mermaid-lint.sh` exit status is recorded as a
   number, read directly rather than through a pipe.
4. Step 5h reports a verdict for all 25 batchable types, each with the
   pre-batch method and lines it read, plus a verdict on each of the
   three delete paths and an explicit answer on whether
   `test_hard_delete_batch_parity.py`'s literals were derived from the
   pre-batch code.
5. Step 5i gives a verdict or a non-applicability sentence for each of
   the floor's five items and each of D31's three questions, and
   records the exit status of 33fl's `pre-commit run --all-files` and
   `tools/test_infra_report_precompute.py`.
6. Every finding, S1 to S3 included, carries a grade, a disposition
   and a D32 classification. No issue filed duplicates one the D38
   searches returned, and those queries are written in the
   disposition.
7. The master plan's phase 5 section carries an "Audit of phases 1 to
   4" subsection recording what was read and what was found, in one
   sentence where the answer is nothing.
8. `python3 tools/check-plan-status.py` exits 0 and
   `pre-commit run --all-files` is clean.
9. Phase 5 and the plan are `Complete` only if no blocking finding is
   open, and phase 5's `Merged` cell is empty either way (D37).

## Findings

### Wave 1

Suite results, run from the worktree root, each under `timeout 2400`.

* `timeout 2400 pre-commit run --all-files` exited 0.
* `timeout 2400 tox` exited 0 (py3, flake8 and cover all OK; no
  failing tests).
* `timeout 2400 tools/mermaid-lint.sh` exited 0 (docker reachable;
  status read directly from a file, not through a pipe).
* `timeout 2400 bash -c 'tox -e genprotos && git diff --exit-code shakenfist/protos'`
  exited 1. The diff is five files only, each a one-line change of
  `GRPC_GENERATED_VERSION` from `'1.84.0'` to `'1.84.1'`:
  `agent_pb2_grpc.py`, `common_pb2_grpc.py`, `nodelock_pb2_grpc.py`,
  `privexec_pb2_grpc.py` and `shakenfist_enums_pb2_grpc.py`. The
  regenerated `database_pb2*.py` and `database_pb2_grpc.py` files, the
  only ones the ranges touch (`git diff --name-only` shows phases 2 and 3
  change them; phases 1 and 4 touch nothing under `protos/`), matched
  the committed copies exactly.

  So the failure is not a stale generated file from any range, and the
  management session traced its actual cause rather than leaving it at
  tooling drift. `database_pb2_grpc.py` read `'1.84.0'` at both
  `dd5723c3db` and `1090771e64`; `d0107d1eaa` ("Remove the
  never-released V1 coalescing RPCs", 2026-10-29) regenerated the
  `database_pb2*` family with grpcio-tools 1.84.1 and left the other
  five families at 1.84.0. That commit is outside all four ranges and
  belongs to another plan, so the split stamp is not a finding of this
  audit; it is recorded here because it is the reason this check cannot
  exit 0 on any machine running 1.84.1, and because the next audit in
  any plan will hit the same wall until the five laggards are
  regenerated. An issue for that is filed at the disposition.

  The regenerated files were put back to their committed content
  afterwards; `git status` and `git diff --cached` confirm the tree
  carries nothing but this plan file.

#### 6537966c00

`git diff 6537966c00^1 6537966c00 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 6537966c00^1 6537966c00 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 6537966c00^1 6537966c00 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 6537966c00^1 6537966c00 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 6537966c00^1 6537966c00 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 6537966c00^1 6537966c00 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 6537966c00^1 6537966c00 --stat | tail -1`
```
 11 files changed, 2054 insertions(+), 110 deletions(-)
```

`git diff 6537966c00^1 6537966c00 -- '*.py' | grep -cE '^\+\s*def test_'`
```
15
```

`git diff 6537966c00^1 6537966c00 --name-only -- 'docs/*' '*.md'`
```
docs/operator_guide/database.md
docs/plans/PLAN-deleted-object-sweep-phase-01-measure.md
docs/plans/PLAN-deleted-object-sweep.md
docs/plans/index.md
docs/plans/order.yml
docs/release_notes/v07-v08.md
```

`git diff 6537966c00^1 6537966c00 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### dd5723c3db

`git diff dd5723c3db^1 dd5723c3db -- '*.py' | grep -nE '^\+[^+].{120,}'`
```
976:+DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0e\x64\x61tabase.proto\x12\x11s
[line truncated here by the recorder: 91048 characters in all, one serialized protobuf descriptor in shakenfist/protos/database_pb2.py]
```

`git diff dd5723c3db^1 dd5723c3db -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff dd5723c3db^1 dd5723c3db -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff dd5723c3db^1 dd5723c3db -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff dd5723c3db^1 dd5723c3db -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff dd5723c3db^1 dd5723c3db -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff dd5723c3db^1 dd5723c3db --stat | tail -1`
```
 22 files changed, 4002 insertions(+), 1090 deletions(-)
```

`git diff dd5723c3db^1 dd5723c3db -- '*.py' | grep -cE '^\+\s*def test_'`
```
68
```

`git diff dd5723c3db^1 dd5723c3db --name-only -- 'docs/*' '*.md'`
```
docs/developer_guide/database_internals.md
docs/operator_guide/database.md
docs/plans/PLAN-deleted-object-sweep-phase-02-fair-drain.md
docs/plans/PLAN-deleted-object-sweep.md
docs/plans/PLAN-grpc-bounded-replies.md
docs/plans/index.md
docs/release_notes/v07-v08.md
```

`git diff dd5723c3db^1 dd5723c3db -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 1090771e64

`git diff 1090771e64^1 1090771e64 -- '*.py' | grep -nE '^\+[^+].{120,}'`
```
1328:+DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(b'\n\x0e\x64\x61tabase.proto\x12\x11
[line truncated here by the recorder: 92220 characters in all, one serialized protobuf descriptor in shakenfist/protos/database_pb2.py]
```

`git diff 1090771e64^1 1090771e64 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 1090771e64^1 1090771e64 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 1090771e64^1 1090771e64 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 1090771e64^1 1090771e64 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 1090771e64^1 1090771e64 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 1090771e64^1 1090771e64 --stat | tail -1`
```
 43 files changed, 5088 insertions(+), 923 deletions(-)
```

`git diff 1090771e64^1 1090771e64 -- '*.py' | grep -cE '^\+\s*def test_'`
```
92
```

`git diff 1090771e64^1 1090771e64 --name-only -- 'docs/*' '*.md'`
```
docs/developer_guide/database_internals.md
docs/developer_guide/subsystem_internals.md
docs/operator_guide/database.md
docs/plans/PLAN-deleted-object-sweep-phase-01-measure.md
docs/plans/PLAN-deleted-object-sweep-phase-02-fair-drain.md
docs/plans/PLAN-deleted-object-sweep-phase-03-batched-deletes.md
docs/plans/PLAN-deleted-object-sweep.md
docs/plans/index.md
docs/release_notes/v07-v08.md
```

`git diff 1090771e64^1 1090771e64 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output

#### 1bd914aa02

`git diff 1bd914aa02^1 1bd914aa02 -- '*.py' | grep -nE '^\+[^+].{120,}'`
no output

`git diff 1bd914aa02^1 1bd914aa02 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
no output

`git diff 1bd914aa02^1 1bd914aa02 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
no output

`git diff 1bd914aa02^1 1bd914aa02 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
no output

`git diff 1bd914aa02^1 1bd914aa02 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
no output

`git diff 1bd914aa02^1 1bd914aa02 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
no output

`git diff 1bd914aa02^1 1bd914aa02 --stat | tail -1`
```
 5 files changed, 954 insertions(+), 4 deletions(-)
```

`git diff 1bd914aa02^1 1bd914aa02 -- '*.py' | grep -cE '^\+\s*def test_'`
```
0
```

`git diff 1bd914aa02^1 1bd914aa02 --name-only -- 'docs/*' '*.md'`
```
docs/operator_guide/database.md
docs/plans/PLAN-deleted-object-sweep-phase-04-drain.md
docs/plans/PLAN-deleted-object-sweep.md
docs/plans/index.md
docs/release_notes/v07-v08.md
```

`git diff 1bd914aa02^1 1bd914aa02 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
no output


## Execution notes

- **Back brief, 2026-11-11.** Michael kept all three points as planned:
  D31 (the two 33fl pull requests are audited here under the five-item
  floor, not as pull requests against 33fl), D35 at its full weight
  (step 5h compares all 25 batchable types at opus and high effort,
  rather than a five-type spot-check) and D37 (the plan may reach
  `Complete` inside this phase's own pull request when nothing blocks).
  He also gave this session permission to push and open the pull
  request once the definition of done passes, so step 5j does not stop
  to ask.
- **5b, 2026-11-11.** Wave 1 and the mechanical sweep, recorded above.
  Three of the four suite commands exited 0; the genprotos check exited
  1 for a cause outside every range, traced in the Wave 1 record.
  Thirty-three of the forty greps produced no output.
  - *Departure from the brief, kept deliberately.* The management
    session's brief told 5b to keep every line of every grep's output.
    Two lines are serialized protobuf descriptors from
    `shakenfist/protos/database_pb2.py`, 91,048 and 92,220 characters,
    and keeping them whole would have added about 183KB of generated
    bytes to this plan. They are truncated to their first 100
    characters with a marker giving the full length and the file. The
    plan's own definition of done counts commands rather than output
    lines, so nothing in the plan is weakened; the brief's rule was the
    management session's and it is the management session that relaxed
    it.
  - The four diffstats 5b recorded (11/+2,054-110, 22/+4,002-1,090,
    43/+5,088-923 and 5/+954-4) match the figures the planning survey
    measured independently, which is a cheap check that the ranges it
    read are the ranges the plan names.
  - Three verdicts were reproduced in the management session before
    this was committed: the pushdown grep and the `print(` grep over
    phase 3's range (both empty) and the over-120-character grep over
    the same range (one hit, the generated descriptor, 92,220
    characters). The one hit is in a generated file, which D30 exempts
    from judgement review.

## Back brief

Before starting, confirm with Michael:

* **D31**, that the two 33fl pull requests are audited here under the
  five-item floor rather than in 33fl. The alternative is two small
  pull requests against a repository with no runbook, and it is his
  call whether the record belongs with the plan or with the code.
* **D35 and the weight given to 5h.** It is high effort on opus for a
  question four pull request reviews already looked at in pieces, and
  it is the single most expensive step here. If he thinks the parity
  test's "deliberately a second copy" design is defence enough, 5h
  becomes a cheaper spot-check of five types rather than all 25.
* **D37**, that the plan may reach `Complete` inside this phase's own
  pull request when nothing blocks.

And a gate during execution: after 5b reports, before 5c to 5i are
spawned. Wave 2 is seven agents, two of them opus at medium or high
effort, and a wave 1 failure that is not this plan's doing changes
whether it is worth spending that now.
