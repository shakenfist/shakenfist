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
   and relabelled the per-blob and per-instance pass log lines. (Survey
   finding corrected after step 5f: that commit does **not** touch the
   deleted-object sweep's own pass line or its fields, which this plan first
   claimed it did.)
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


### Style

Step 5c, read across all four ranges. Files read: `mariadb.py` (:4563-4640,
:5112-5190, :26454-26760, :6428-6436), `daemons/database/main.py` (:770-800,
:6625-6634), `daemons/cluster/scheduled_tasks.py` (:1030-1050, :1215-1260),
`baseobject.py` (:790-830), plus the non-test code of all four diffs;
`PUSH-AUDIT.md`'s style brief, `AGENTS.md` and all 359 lines of
`docs/developer_guide/standards.md`.

One finding, advisory, still true:

- `shakenfist/tests/test_daemon_cluster_scheduled_tasks.py:1239` reads
  `# calls, in the same order, with the same arguments (D6).` A decision
  citation in committed code. Introduced by `a4f5fa1b3f`, inside phase 1's
  range.

Conventions checked and honoured, named so the pass says what it covers:
imports at the top and in order with `# noreorder` on `shakenfist_utilities`;
logging through `logs.setup(__name__)` and `LOG.with_fields`; single quotes
outside docstrings and no triple-single quotes; 120 character lines; no
trailing whitespace; the three-layer `_direct_*`/`_grpc_*`/public pattern on
all three new accessors, with `_direct_*` reached only from its public wrapper;
proto edits in `protos/database.proto` with both new operations registered in
the Monitor list at `daemons/database/main.py:6630-6631`; SQL pushdown, every
new predicate in SQL; `hard_delete()` cleaning up what the object owns; mypy
annotations on new functions; the guarded-UPDATE-first invariant, which the
batch satisfies by opening its transaction with the guarded DELETE; event
coverage preserved. Not applicable: field masks, `SecretStr`, native ENUM
columns, glossary. Not assessable from a diff, and recorded as such rather
than passed: whether an index serves the count query, `in_memory_only`
behaviour, and whether the sweep loops reach `idle()` for the watchdog.

**Management session note.** 5c's first report returned "Style checks passed"
having read none of `PUSH-AUDIT.md`, `AGENTS.md` or `standards.md` -- it had
worked from the brief's summary of the conventions. That is a pass against the
summary, not against the conventions, so it was sent back; the record above is
the second report. Its specific claims were reproduced before acceptance (the
three-layer line numbers, the Monitor registration, the event mute). Its count
of the advisory checker's hits was wrong -- it said 11, the checker reports 406
repository-wide -- so the management session blamed every one of the 406
against the four ranges: exactly one is in range, the finding above. The other
405 predate the ranges and are a question about that check's default, not about
this plan.

### 2a. Code quality

Step 5d, read across all four ranges, with wave 1's mechanical sweep as input.
Files read at `$TIP`: `scheduled_tasks.py`, `mariadb.py`'s hard-delete region,
`daemons/cluster/main.py`, `daemons/database/main.py`, `baseobject.py`,
`node.py`, `namespace.py`, `namespace_claim.py`, `instance.py`, `constants.py`,
`config.py`, `protos/database.proto`, `docs/operator_guide/database.md`.

**One blocking finding: the sweep reports `node` objects as hard deleted
forever, and never deletes one.** Filed as
[#4876](https://github.com/shakenfist/shakenfist/issues/4876). Every link was
re-verified in the management session:

- `Node.hard_delete()` is `pass` with no `super()` call (`node.py:896-898`), so
  nothing removes the node's `object_states` row.
- `Node.delete()` sets `STATE_DELETED` (`node.py:893`), which is in
  `FINAL_OBJECT_STATES` (`constants.py:204`).
- `node` is in `OBJECT_NAMES_TO_CLASSES` (`constants.py:145`) and the sweep's
  type list is the unfiltered `list(OBJECT_NAMES_TO_CLASSES)`
  (`scheduled_tasks.py:1077`).
- `node` is not in `HARD_DELETE_BATCH_FUNCTIONS`, so it takes the per-object
  path, which sets the outcome without checking that anything was removed
  (`scheduled_tasks.py:1192-1193`).
- The same file already excludes `node` from its *other* sweep,
  `ZOMBIE_REPAIR_EXCLUDED_TYPES = {'node', 'namespace'}` at :1378, so the fix
  pattern sits beside the defect.

Two things the management session added to the report. First, the bug defeats
this plan's own regression guard: `shakenfist_sweep_backlog` computes
`days_behind` as backlog over arrivals, and arrivals are derived from the
hard-delete counter, so the false `hard_deleted` count inflates arrivals and
drives `days_behind` to approximately zero. A cluster whose node backlog is
permanently stuck would never be flagged `behind`. Second, it is **latent, not
live**: on sfcbr `cluster_deleted_object_backlog{object_type="node"}` reads 0
and there is no `object_type="node"` series on the hard-delete counter in 30
days, so no node has been deleted there in that window. No audit event is
written either, because `Node.hard_delete()` never reaches the base class, so
there is no event-row flood.

Attribution is phase 2's reachability change rather than the no-op itself,
which long predates the plan: before the per-type round robin, `node` sat
behind the single FIFO's large backlogs and was rarely reached.

Advisory findings, all still true, filed together as
[#4882](https://github.com/shakenfist/shakenfist/issues/4882): `mariadb.py`
gained about 825 net lines across these phases and has a clean contiguous seam
at its hard-delete region (roughly :26169-:26692, four public entry points);
three near-identical sweep work-list wrappers (:262, :313, :343) and a fourth
copy of `FakeClock`; and a comment in `_fill_deleted_object_work` that
hard-codes `GRPC_UNAVAILABLE_RETRIES`, `GRPC_RETRIES` and `GRPC_TIMEOUT` values
which will rot in place.

**The phase 8 question -- what the change switched on.** The four types *not*
batched are exactly the four whose hard delete has side effects beyond row
deletes, and they are the ones that previously starved. So the cross-object
effects now firing at sweep rate are instance placement releases returning
ledger capacity (`instance.py:1970`), namespace cascades that revoke inbound
trusts on *other* namespaces (`namespace.py:344-380`), and namespace claim
releases returning `cluster_capacity.claimed_*` (`namespace_claim.py:387`).
Nothing there is incorrect, but anything that treats `cluster_capacity` or
federation trust as slowly-changing now sees a sustained stream of releases it
did not before. One consequence already landed as #4723 (the audit event's log
echo, muted three days after phase 4) and another as #4759.

**Can the keyset cursor skip an object? No.** The walk orders by `object_uuid`
with `limit(page_size + 1)`, so `more_available` is exact; the cursor advances
only on `more_available and uuids` and resets to `''` with a 300 s recheck at
the end of a walk; and every drop path returns the entry to the walk
(`work.extendleft(reversed(batch))` at :1340, :1346 and :1362). An object that
becomes eligible behind the cursor is delayed by at most one walk plus 300 s,
never skipped. On a lost election the new leader starts at `''`; with two brief
leaders the guarded batch and the single path's `suppress_failure_audit` read
make a double attempt a `skipped` or `gone`, not a wrong delete.

### 2b. Test review

Step 5e, read across all four ranges. Files read: the paging, batch, per-call
and cache regions of `mariadb.py`, `baseobject.py`, `scheduled_tasks.py`,
`.github/workflows/functional-tests.yml`, `tools/ci-enum-widening-test.sh`,
`tox.ini`, and the tests `test_mariadb_objects_by_state_page{,_live}.py`,
`test_mariadb_hard_delete_objects{,_live}.py`,
`test_hard_delete_batch_parity.py`, `test_daemon_cluster_scheduled_tasks.py`,
`mock_mariadb.py`, `test_blob.py`, `test_error_report.py`,
`test_mapping_rule.py`, `test_agent_operation_expiry.py`.

No blocking finding. The substantive result, filed as
[#4880](https://github.com/shakenfist/shakenfist/issues/4880), is that the
batch's real-SQL behaviour had no pull-request-time test during any of the four
phases' reviews, and the mock-backed tests route around the batch entirely.
Both halves were verified in the management session:

- The `schema_enum_widening` job's condition is
  `(github.event_name == 'merge_group' || github.event_name == 'workflow_dispatch')`
  (`functional-tests.yml:570`) -- never `pull_request`. It does gate a merge, so
  the signal exists; it arrives after review.
- `MockMariaDB`'s double for `mariadb.hard_delete_objects`
  (`mock_mariadb.py:3505`) raises `NotImplementedError` for the guarded batch
  and, for unconditional calls, loops over
  `mariadb.hard_delete_objects_per_call()`. So no MockMariaDB-backed test
  exercises the batch path at all, and the post-conditions those tests assert
  are produced by the fallback through per-table doubles.
- The non-live siblings do not close the gap: the paging test mocks `_direct_*`
  out of every case, and the batch test asserts statement order and bound
  parameters against a `MagicMock` connection, with the table list asserted for
  2 of the 25 types.

**Phase 2's fairness guard is real, and the management session reproduced it.**
`DeletedObjectSweepFairnessTestCase.test_a_small_type_is_served_behind_a_large_one`
(`test_daemon_cluster_scheduled_tasks.py:1433`) was run in the worktree with a
saved copy of the one file for the restore: baseline OK; with the rotation
removed (`index = (_DELETED_OBJECT_NEXT_TYPE + offset) % len(object_types)`
replaced by `index = offset`) FAILED. 5e separately killed a second mutant that
made the fill return after one type, as the old FIFO did. So the guard holds on
both axes, and phase 3's batching did not hollow it out -- both its types are
non-batchable. The tree was restored from the copy, not with `git checkout`, and
`git status` confirmed clean.

Also advisory, recorded rather than filed separately: no functional test
exercises the sweep, the backlog gauge or hard deletion at all, so there is no
functional test that would have failed before phase 2 and passes after. That is
awkward to fix honestly -- the sweep runs only on the elected maintainer and
waits `CLEANER_DELAY` -- and is noted in #4880.

### 2c. Documentation review

Step 5f, read across all four ranges. Pages read at `$TIP`:
`docs/operator_guide/database.md` (:611, :968-1300),
`docs/developer_guide/database_internals.md`,
`docs/developer_guide/subsystem_internals.md`,
`docs/release_notes/v07-v08.md`. Code read: `scheduled_tasks.py`,
`config.py`, `mariadb.py`, `daemons/cluster/main.py`.

No blocking finding. Four advisory, all still true; the first two are filed as
[#4878](https://github.com/shakenfist/shakenfist/issues/4878) (the budget) and
the rest as [#4879](https://github.com/shakenfist/shakenfist/issues/4879):

- The sweep "takes one object from each type in turn" is wrong in three places
  (`database.md:1028`, `database_internals.md:649`, `v07-v08.md:956`); a turn is
  up to `DELETED_OBJECT_BATCH_SIZE` entries of one type, and `database.md`
  contradicts itself by saying "after each batch" at :1000-1002.
- "Draining a backlog"'s sizing arithmetic assumes every pass reaches the cap,
  and never says to raise the budget alongside the cap. The only hint sits in
  the monitoring section *after* the instruction to raise it.
- The page tells the reader to compare `elapsed_seconds` with the budget, but
  `elapsed_seconds` excludes the fill, which is charged against the budget. A
  pass ended by the budget therefore logs a value below it.
- The `Skipping per-deleted-object drain, database unavailable during the fill`
  warning is undocumented, so a missing `Processed` line is unexplained.

Everything else checked out, which is a real result: every metric name and
label, every configuration option and default (20 / 100 / 25, max 1000), every
pass-log field, the 500-uuid page, the 300 s recheck, the hourly backlog count,
all six outcomes against `DELETED_OBJECT_OUTCOMES`, and every PromQL expression
parsing as written with its metrics and labels present. The `mysql_global_status_*`
and `process_start_time_seconds` queries read external exporters and cannot be
checked from the repository. Nothing documented has been removed from the code,
and the plan-reference rules found no hits in any range's added lines, so phase
4's own grep result was correct rather than a gap.

**A premise in this plan was wrong, and 5f refuted it.** Survey finding 4 and
5f's brief both said `d04d5583f5` added fields to the sweep's pass log line. It
did not: it added a `NamedQueue` and relabelled the per-blob and per-instance
`Processed` lines only. The management session confirmed this by reading the
commit, and the survey finding is corrected at source in this commit. The
sweep's own pass line and its fields are unchanged, and the documentation of
them is complete.

### 2d. Security review

Step 5g, read across all four ranges. Files read at `$TIP`: `mariadb.py`
(:26141-26712, :4553-4660, :5112-5180, :783-823, :648-668),
`daemons/database/main.py` (:751-923, :5764-5784, :6570-6591, :7040-7080),
`scheduled_tasks.py` (:1040-1372), `baseobject.py` (:783-818), `config.py`
(:832-874), the `protos/database.proto` diffs, and
`docs/operator_guide/database.md`.

**What the batch may delete: no issue found.** `object_type` is refused before
any SQL is built -- `hard_delete_objects_request_error()` tests membership of
`HARD_DELETE_BATCH_FUNCTIONS` first (`mariadb.py:26466`), and every path
consults it: the public wrapper at :26664, and the servicer independently at
`daemons/database/main.py:903-908` before calling that wrapper, which checks
again. `_direct_hard_delete_objects()` has exactly one caller, downstream of
both checks. No table, column or function name reaches SQL by interpolation;
every statement is built from SQLAlchemy `Table` objects and the function name
is a dict key. uuids are bound through `column.in_()`, an empty list returns
early at :26680, a non-uuid is refused by the `UUID()` loop at :26478, and an
over-long list is refused against `HARD_DELETE_OBJECTS_MAX = 1000`.

**One in-range finding, advisory, still true**, filed as
[#4877](https://github.com/shakenfist/shakenfist/issues/4877): the
cluster-operation statement builders key on the operation's own uuid with no
operation-type predicate, in tables shared by all fourteen cluster-operation
types. The batch's opening state delete *is* type-scoped, but in unconditional
mode the table deletes do not use what it returned -- `mariadb.py:26529` says so
outright: `deleted = list(object_uuids) if unconditional else returned`. So an
unconditional batch declaring one operation type and naming another's uuid
removes that operation's rows and leaves its state row: a phantom. Not reachable
from any in-tree caller. The management session swept the class: **three**
builders share the gap, not the one reported (`delete_cluster_operation` on
`uuid`, `delete_cluster_operation_error` on `op_uuid`,
`delete_cluster_operation_target` on `operation_uuid`), and every other builder
in `HARD_DELETE_STATEMENTS` uses a type-specific table, where a uuid-only
predicate is safe.

**The authorisation answer is "it does not distinguish callers", and it predates
the ranges.** sf-database binds `add_insecure_port`
(`daemons/database/main.py:7069`) and its only interceptor counts requests
(:7042); the `caller_daemon` label is client-supplied and unverified. So
`GetObjectsByState`, `CountObjectsByState`, `HardDeleteObjects` and
`DeleteObjectEvents` are reachable by anything that can open the port, and a
caller that should not hard delete objects can. This is a recorded design
posture, not a new defect -- `docs/plans/PLAN-embrace-tls.md:68` states it as
"the mesh is private; trust it" -- so it is **declined as a finding against this
plan** and not refiled. What the ranges did do is widen that surface from
per-object deletes to a 1,000-object unconditional batch, and the operator
guide's database page never states the trust assumption; that sentence is folded
into #4879.

Two further hardening notes, both advisory and recorded here rather than filed:
`page_size` is a `uint32` passed through without a server-side clamp
(`daemons/database/main.py:789`), which is not additive because `page_size=0` is
already an unbounded read by design; and during a rolling upgrade an
sf-database predating the paging fields returns the whole reply with
`more_available=False` (`mariadb.py:4794`), which is the documented pre-paging
behaviour.

**No issue found** on three further questions. A hostile keyset cursor cannot
escape its type: the walk always ANDs `object_type` with a bound
`object_uuid > after_uuid`, so it is strictly monotonic, finite, and
type-scoped. The audit event carries a constant message plus the object's own
type and uuid -- no namespace name, no key material, nothing beyond what
`object_states` already holds. And the sweep cannot hold a transaction
unboundedly: the batch is capped at 25, the pass at 20 s and 100 objects, the
deadline is checked before every turn and the fill before every read, and
`_retry_transaction` is capped at four attempts.

### The hard-delete contract

Step 5h, this phase's own heading, against the pre-batch tree at
`1090771e64^1`. **All 25 batchable types MATCH: no missing delete, no extra
one.** The restatement phase 3 made was faithful, which is the answer D35 was
written to get.

The base class's share was read at pre-batch `baseobject.py:765-769`
(`delete_state`, `delete_object_metadata`, `delete_object_events`, then the
audit event) and :858-861 (`delete_cluster_operation_targets_for_object` for a
`DatabaseBackedObjectWithOperations`). The fourteen cluster-operation types
share `_CLUSTER_OPERATION_HARD_DELETES`, checked once against pre-batch
`operations/baseoperation.py:268-287` and then confirmed type by type: none of
the fourteen modules defines or extends `hard_delete`, so none added anything of
its own. The other eleven were read individually -- `agentoperation.py:553-563`,
`artifact.py:748-752`, `network/network.py:814-817`, `blob.py:1050-1058` with
`_remove_references_held` at :681-686, `managed_executables/dnsmasq.py:125-127`,
`network/interface.py:302-305`, `ipam.py:428-434`, `namespace_key.py:424-428`,
`trusted_issuer.py:292-296`, `mapping_rule.py:605-609`, `upload.py:149-151`.

The management session re-read two of the 25 itself before accepting the report,
as this plan's risk section requires. BLOB: pre-batch calls
`_remove_references_held()` (which is `remove_all_references_from(BLOB, uuid)`),
then hashes, transfers, attributes, blob; the declaration lists exactly those
five in that order. ARTIFACT: pre-batch calls three, plus
`delete_cluster_operation_targets_for_object` through its `dbowo` base; the
declaration lists exactly those four. Both match.

**The three paths agree.** The servicer re-enters the same public function, so
there is one declaration and one implementation, and the server re-runs the
request check. `hard_delete_objects_per_call` iterates the identical tuple and
then the base three, so the set is identical by construction; it differs only in
atomicity, the absence of a guard (it is reached only from the unconditional
path) and the state row going last. It is reachable at `$TIP` --
`HardDeleteBatchUnavailable` is raised only by the gRPC wrapper on `UNIMPLEMENTED`
or inside its 300 s remembered-answer window, which means a rolling upgrade --
and three tests exercise it rather than assert it exists, two of them in `tox`.
For the four cascade types nothing is done twice or dropped: the targets delete
is gated on the same `not in HARD_DELETE_BATCH_FUNCTIONS` test in both places,
and the sweep chooses batch or singly, never both.

**Part 3, which is why this heading existed.** `EXPECTED_DELETES` in
`test_hard_delete_batch_parity.py:79-151` is **not** independent of the
declaration and could not have caught an error made when the declaration was
written. The only two tests that read the literals compare them with the
declaration (`test_literal_matches_the_mapping` asserts equality outright) or
with `hard_delete_objects_per_call`, which iterates that same declaration. The
live module's fixtures seed themselves from the declaration too
(`test_mariadb_hard_delete_objects_live.py:264`). So all three copies trace to
one source, and the docstring's "as read from each class before it moved onto
the batch" describes an intent the code cannot evidence. What the parity test
*does* prove independently is the other direction, and it is valuable:
`test_class_makes_one_batch_call` runs each of the 25 classes' real
`hard_delete()` and requires exactly one unconditional batch plus one audit
event, so a row delete left behind in a class would fail. This step's table is,
as far as can be established, the first comparison of the declaration against
the pre-batch code. Advisory, and the discipline point is folded into #4880.

### The 33fl floor

Step 5i, over `789d3b45` (Mach33Labs/33fl#1094, sfcbr's cap) and `8740490f`
(#1095, the `shakenfist_sweep_backlog` fact), under the five-item floor. Nothing
blocking, nothing advisory.

The floor: (1) form satisfied -- both are two-parent merge commits read against
their first parents, recorded here because no `Merged` cell names them, which is
what D31 decided; (2) no new unattended job -- the fact runs inside the existing
nightly pre-compute and the cap rides the existing deploy; (3) no secrets or
hostnames in either diff; (4) no removals or renames; (5) nothing published.

D31's three questions, each answered from the code and its tests:

- **Absence reads as `unavailable`, not zero.** `summarize_sweep_backlog`
  (`tools/infra-report-precompute.py:8250`) returns
  `{'unavailable': 'cluster_deleted_object_backlog absent (leadership changed
  within the hour?)'}` when the current rows are empty, which the management
  session confirmed by reading it. Pinned by
  `test_gauge_absent_is_unavailable_not_clean`. Both gauge queries use `max by`,
  pinned by `test_two_leaders_take_the_larger_not_the_sum`, and a missing prior
  sample gives nulls and no flag, pinned by
  `test_missing_prior_sample_gives_null_and_no_flag`. All three tests exist.
- **The cap change is inert elsewhere and leaves `RAM_OVERCOMMIT_RATIO`
  intact.** The management session parsed `group_vars/allsf.yml`'s
  `extra_config`: exactly two entries, `RAM_OVERCOMMIT_RATIO` 1.0 and
  `DELETED_OBJECT_SWEEP_MAX_OBJECTS` 400. Only `sfcbr.yml` consumes
  `extra_config`, and it asserts every pair back from `cluster_config` after
  deploy, so a dropped setting fails the deploy loudly.
- **No silent unattended failure.** `unavailable` reaches the report as "not
  checked tonight", and the landing adds `behind` and `growing` to
  `FLAG_CHANGE_SPECS`.

In its own worktree, cut detached at `master` because the primary clone has it
checked out: `pre-commit run --all-files` exit 0, and
`python3 tools/test_infra_report_precompute.py` exit 0 with 935 tests OK. The
worktree was removed afterwards, which the management session confirmed. The
primary clone's unrelated uncommitted work was never touched -- the brief
required a separate worktree for exactly that reason.

### Disposition

Seven issues filed, one finding declined in writing, two seeded findings
recorded without filing. The D38 searches run before filing were
`hard delete in:title`, `sweep in:title`, `deleted object in:title` and
`object_states in:title,body`, each `--state all --limit 50`, plus
`sf-database authentication`, `mTLS`, `insecure_port` and
`authorise OR authorize in:title` for the security finding. Nothing returned
covers any filed finding; #3922 is the adjacent case to #4876 and is
cross-referenced on it, and #3534 (closed) is cross-referenced on #4877.

| Finding | Step | Grade | D32 | Disposition |
|---------|------|-------|-----|-------------|
| `node` is swept but never hard deleted; outcome and backlog both lie | 5d | **blocking** | still true | filed [#4876](https://github.com/shakenfist/shakenfist/issues/4876) |
| Cluster-operation batch statements have no type predicate (3 builders) | 5g | advisory | still true | filed [#4877](https://github.com/shakenfist/shakenfist/issues/4877) |
| The time budget is the next limit; docs over-promise, pass log is silent | 5f, S1 | advisory | still true | filed [#4878](https://github.com/shakenfist/shakenfist/issues/4878) |
| Four doc and comment corrections, incl. the `(D6)` citation | 5c, 5f, 5g | advisory | still true | filed [#4879](https://github.com/shakenfist/shakenfist/issues/4879) |
| No PR-time test of the batch's SQL; the mock bypasses the batch | 5e, 5h | advisory | still true | filed [#4880](https://github.com/shakenfist/shakenfist/issues/4880) |
| gRPC stubs split across two grpcio-tools versions | 5b | advisory | still true | filed [#4881](https://github.com/shakenfist/shakenfist/issues/4881) |
| `mariadb.py` seam, duplicated wrappers, config-restating comment | 5d | advisory | still true | filed [#4882](https://github.com/shakenfist/shakenfist/issues/4882) |
| sf-database authenticates and authorises nobody | 5g | advisory here | still true | **declined**: a recorded posture (`PLAN-embrace-tls.md:68`), not a defect this plan introduced. The surface widening is recorded above and the missing operator-guide sentence folded into #4879 |
| sfcbr's cap left at 400 (S2) | seeded | advisory | still true | recorded in the master plan's Future work; no issue, it is a one-line 33fl change |
| #4759 is this plan's doing (S3) | seeded | advisory | still true | already filed; its "every fifteen minutes" claim corrected by a comment on the issue |

All 25 batchable types matched the pre-batch code, so the defect class this
phase existed to look for -- a silent row leak from the batch restatement -- is
absent. The blocking finding is in the adjacent place: a type the batch does not
cover at all, which the sweep nonetheless reports as succeeding.

**Consequence for this plan's status.** D37's second branch applies: one finding
is blocking, so phase 5 and the plan stay `In progress`, and the plan is
`Complete` when #4876 is resolved. Per the `plan-phase-landing` convention the
fix is a follow-up pull request after this audit merges, and that pull request
records this phase's merge commit.

### How the findings resolved

Recorded 2026-11-13, in the pull request which set this phase's `Status` and
`Merged` cells and the plan's index row.

| Issue | Resolved by |
|-------|-------------|
| #4876 | The repository's issue-fix automation, PR #4884 (`9914efa207`), while this phase was still open. It took the suggested fix, `DELETED_OBJECT_SWEEP_EXCLUDED_TYPES = {'node'}` |
| #4877 | PR #4897 (`4ef08c4b10`). Two of the three builders take the predicate directly; only `cluster_operation_errors` needs a subquery |
| #4878 | PR #4897. The drain returns a `DrainOutcome` naming the limit which ended it, logged as `stopped_by` with `fill_seconds` and `budget_seconds`, and the sizing arithmetic corrected in the operator guide and both config descriptions |
| #4879 | PR #4897, all four items |
| #4880 | PR #4897. The eight live cases move to `shakenfist/tests/hard_delete_cases.py` and now also run against an in-memory sqlite engine built from `mariadb.py`'s own table definitions, so they run on a pull request; the live module still gates a merge |
| #4881 | The issue-fix automation, PR #4896 (`ccd8f6b701`) |
| #4882 | PR #4897, all four items: the hard delete moves to `shakenfist/mariadb_hard_delete.py`, the three sweep wrappers share `_sweep_read()`, the settable-seconds clock moves to `shakenfist/tests/fakeclock.py`, and the comment restating the gRPC retry budget names those constants instead of copying their values |

**Five of the seven were fixed twice.** A second session took #4877 to #4882
as a themed batch while this phase's own follow-up was in review, and PR #4897
merged first, so the follow-up's five fix commits were dropped rather than
rebased and it carries only this close-out. The cost was a day's work and a
reviewer's attention, and nothing reserved the issues against it: the
`automated-fix-attempted` label guards against the issue-fix automation only,
and `plan-blockers` records what a plan is waiting on rather than which issues
an in-flight branch has claimed.

**#4877 was filed larger than it was.** It said the three cluster-operation
statements key on the uuid "in tables shared by all fourteen
cluster-operation types", implying none could carry a type predicate.
`cluster_operation_targets` already has an `operation_type` column, so two of
the three take the predicate directly and only `cluster_operation_errors`
needs to reach through `cluster_operations`. The invariant restored is the one
the issue describes; the work was smaller. Both independent fixes reached that
reading of it.

**#4882's `FakeClock` item was read as false, and it is not.** Checking it by
comparing the four classes *named* `FakeClock` found four unrelated
interfaces -- one `datetime`-based with an `advance()`, one a bare float
returned from `__call__`, one implementing `time()` and `sleep()` with a
`max_reads` valve, one replaying a canned list -- and the conclusion drawn was
that the issue had grepped a name and reported it as duplication. PR #4897
traced the idea rather than the name, and found four modules which had each
written the same settable-seconds callable, under three names between them; it
moved that one idea to `shakenfist/tests/fakeclock.py` and renamed the
genuinely different stand-ins for what they are. So the lesson is the reverse
of the one first recorded here: a name is the wrong unit for either judgement,
because it over-reports duplication and hides it just as easily, and the only
check is to read the bodies across names.

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

- **Wave 2, 2026-11-11.** 5c to 5i ran in parallel as D33 allows, all
  read-only; only 5b and this session wrote files or ran the suite. Each report
  was reviewed against the tree before being recorded, and the checks the
  management session re-ran are named in the findings above. Three reports
  needed management-session work beyond recording:
  - **5c was sent back.** Its first pass returned "Style checks passed" having
    read none of `PUSH-AUDIT.md`, `AGENTS.md` or `standards.md`. The brief was
    adequate; the instruction was skipped. Its second pass withdrew the bare
    pass and produced the `(D6)` finding.
  - **5f refuted a premise of this plan**, which is recorded as a correction at
    source rather than as a finding: `d04d5583f5` did not add fields to the
    sweep's pass log line. Survey finding 4 is corrected in this commit.
  - **5g's finding F2 was one instance of a class of three.** The management
    session read every builder in `HARD_DELETE_STATEMENTS` and found the same
    missing type predicate in `delete_cluster_operation`,
    `delete_cluster_operation_error` and `delete_cluster_operation_target`;
    #4877 is filed against all three.
  Two reports carried a wrong count that did not change their conclusion, noted
  so the next reader does not inherit either: 5c said the advisory
  plan-citation checker reports 11 hits (it reports 406, of which exactly one is
  in range), and 5h said the pre-batch tree has 18 `hard_delete` definitions (it
  has 22; its substantive claim, that none of the fourteen cluster-operation
  modules defines one, holds).
- **The blocking finding, 2026-11-11.** #4876 was verified link by link in the
  management session before being filed, and two facts were added to it: that
  the false `hard_deleted` count defeats this plan's own
  `shakenfist_sweep_backlog` guard by inflating the arrivals term, and that the
  defect is latent on sfcbr, which has deleted no node in 30 days. Per D37 the
  plan therefore stays `In progress` and the fix is a follow-up pull request.
- **Mutation evidence, 2026-11-11.** Phase 2's fairness guard was re-killed in
  the management session rather than taken from 5e's report: the one file was
  copied to the scratchpad, the rotation was removed by an inverse edit, the
  test was run with `-B` under `timeout`, and the file was restored from the
  copy (not with `git checkout`, which would have discarded the plan edits in
  the same tree). Baseline OK, mutant FAILED, `git status` clean afterwards.

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
