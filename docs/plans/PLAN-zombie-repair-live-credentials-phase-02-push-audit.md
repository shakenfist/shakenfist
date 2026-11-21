# PLAN: Zombie repair live credentials phase 2 -- push audit

## Why this phase exists

The master plan
[PLAN-zombie-repair-live-credentials.md](PLAN-zombie-repair-live-credentials.md)
ends, as every master plan must, with a phase that runs
`PUSH-AUDIT.md` over the whole plan's work. This plan has one code
phase, so the audit range is that one pull request.

Phase 1 landed on 2026-10-03 as
[#4419](https://github.com/shakenfist/shakenfist/pull/4419) and
closed
[#3836](https://github.com/shakenfist/shakenfist/issues/3836). Its
status was never closed out: both the master plan's Execution table
and the `docs/plans/index.md` row still read `Not started` for work
which has been on `develop` for seven weeks. The first commit of
this phase records it, because the `Merged` cell is what makes the
range auditable at all.

Recommended planning effort: medium (done). The audit's own
judgment lenses run at the efforts given in the step table; the
security lens is the one at high, because the range is credential
and authentication code.

## Scope

In scope:

- `PUSH-AUDIT.md` wave 1 and its wave 2 mechanical sweep over the
  range, run in this worktree.
- The four wave 2 judgment lenses: 2a code quality, 2b test review,
  2c documentation, 2d security.
- Mutation testing, extended past what phase 1's definition of done
  required -- see survey finding 7 and decision 5.
- Filing the two issues the master plan's Future work defers to
  this phase, and linking them from Future work (survey finding 9).
- Recording the audit's findings and disposition in this file, and
  closing the phase and the plan out.

Out of scope:

- Fixing anything the audit finds. The master plan says findings
  land as their own pull request, and that is what a blocking
  finding gets. This pull request carries the phase 1 close-out,
  this plan, and the audit record.
- Re-reviewing the master plan file itself as documentation
  (decision 7).
- The two deferred designs the plan already declined: atomic
  creation of the three object types, and a namespace-has-no-usable-key
  health signal. This phase files the issue for the second; it does
  not build either.

## What the survey found

Ten findings. Five of them change what the audit should do, and one
is a real coverage gap in phase 1.

1. **The range is a single merge.** `f79a23cd5c^1..f79a23cd5c`
   (#4419), recorded in the master plan's `Merged` cell by this
   phase's first commit. It is a two-parent merge, so reading it
   against its first parent is the whole of what landed. One range
   means one pass, not a pooled union.

2. **The master plan said this phase gets no plan file, and that is
   now false.** Its Execution preamble read "This is a single pull
   request. It has no separate phase files". That was written about
   phase 1, whose steps are inline, but as written it covers phase
   2 as well, and this phase does get a file -- as every other
   push-audit phase in this repository does
   (`PLAN-kerbside-deployer-phase-07-push-audit.md`,
   `PLAN-transient-capacity-refusals-phase-07-push-audit.md`).
   Corrected at source in the planning commit, so the sentence now
   scopes itself to phase 1.

3. **`tox -e genprotos` does not apply.** No `.proto` file is in
   range (`git diff --name-only <range> | grep -c '\.proto$'` is
   `0`). Step 2b says so rather than running it.

4. **`tools/mermaid-lint.sh` has nothing in range.** The runbook
   says to render diagrams when the diff touches markdown, and this
   diff does touch six markdown files -- but none of the four guide
   pages it changed carries a ```mermaid fence (verified: all four
   grep to `0`). The render is a docker build for no diagram, so
   step 2b states the check and skips it.

5. **Wave 1's four style greps are already clean on this range.**
   Run at planning time per this repository's habit of executing a
   definition-of-done item rather than asserting it: no added line
   over 120 characters, no stray `print(`, no new `etcd` reference,
   no new `mariadb.get_all_*` pushdown violation. All four returned
   nothing.

6. **Wave 2's mechanical sweep is already clean too.** No added
   `TODO`/`FIXME`/`HACK`/`XXX`, no new `# noqa`, `# type: ignore` or
   `pragma: no cover`, no new `subprocess`/`os.system`/`shell=True`,
   and no change to any dependency manifest. 25 new test functions.

   The range is 20 files and 1,879 insertions, but that number
   flatters it: 1,034 insertions are the master plan file itself and
   about 553 are tests, leaving roughly 294 lines of production code
   across seven modules (`baseobject.py`,
   `daemons/cluster/scheduled_tasks.py`, `mariadb.py`,
   `namespace.py`, `namespace_key.py`, `trusted_issuer.py`,
   `mapping_rule.py`). The audit should be scaled to 294 lines, not
   to 1,879 (decision 2).

7. **Phase 1's mutation checks were genuinely run, and they leave
   two of the three overrides untested.** #4419's body records
   counts, not assertions: deleting the `NamespaceKey` override
   fails 10 tests, restoring `None`-on-error in the direct key
   reader fails 4, removing the rotation heal fails 2. That is the
   definition of done met. But the briefs only ever asked for the
   key path, so **nothing has ever mutated the `TrustedIssuer` or
   `MappingRule` overrides**, and the pinned structural test in
   `test_mariadb_orphans.py` would keep passing if either returned
   the wrong state, because it only checks that an override exists.
   This is the survey's one substantive gap, and decision 5 puts it
   in scope.

8. **The implementation deliberately diverges from brief 1b, and
   the divergence is undocumented outside the code.** Brief 1b said
   a raised `DatabaseUnavailable` is "caught by the existing
   `ignore_exception`" and the zombie "is not removed from
   `_ZOMBIE_CANDIDATES`, so it is retried next pass".
   `_repair_zombies()`
   (`shakenfist/daemons/cluster/scheduled_tasks.py:1643-1703`)
   instead returns `False` on `DatabaseUnavailable` and ends the
   pass for that object type, catching only *other* exceptions per
   object. Its docstring gives the reason -- a tier-wide failure
   must not spend another `_grpc_call` retry budget per remaining
   object inside a job whose watchdog is petted only between jobs --
   and three tests pin the distinction
   (`test_unreadable_liveness_writes_nothing_and_retries`,
   `test_other_liveness_failures_cost_only_that_object`,
   `test_unavailable_database_stops_zombie_repair_after_one_budget`).
   This reads as the better design rather than a slip, but no phase
   outcome records that the plan was overridden, so the 2a lens is
   told to reach its own verdict rather than inherit mine.

9. **Neither issue this phase must file exists.** Searched `gh issue
   list --state all` for the open-question-3 health signal (a
   namespace with no usable key) and for the legacy
   `namespace_attributes.keys` column, by several wordings each;
   both come back empty. Both are real outstanding work, so the
   index row now names them.

10. **#3836 is closed**, on 2026-10-03T08:33:17Z, by #4419. Nothing
    to do; recorded so the audit does not re-check it.

Nothing else in the master plan's Phase 2 section is wrong. Its
range rule, its "findings land as their own pull request", and its
instruction to file the open-question-3 issue first all hold as
written.

## Decisions

1. **One range, one pass.** `f79a23cd5c^1..f79a23cd5c`. Every diff
   command substitutes `f79a23cd5c^1 f79a23cd5c` for `$RANGE`.
   `develop...HEAD` is forbidden here: the phase has merged, that
   diff is empty, and a clean report over an empty diff is the
   exact failure `PUSH-AUDIT.md`'s range rule exists to prevent.

2. **Scale the audit to the range.** Four judgment lenses and a
   mechanical step, not the ten-step shape the kerbside-deployer
   audit needed. That audit read eleven ranges across three
   repositories; this one reads 294 lines of production code in one
   repository, with no ansible, no shell, no protos and no
   cross-repository seam. Inflating it would buy findings about the
   audit rather than about the code.

3. **Skip `genprotos` and `mermaid-lint.sh`, and say so in the
   report.** Justified by survey findings 3 and 4. A skipped check
   that is not reported reads later as a check that passed.

4. **Re-run wave 1 and the mechanical sweep even though planning
   ran the greps clean.** Survey findings 5 and 6 are evidence for
   *this plan* -- they say the audit will not trip over trivia, so
   it is worth spending on the lenses. They are not the audit
   record. `pre-commit run --all-files` and `tox` have not been run
   in this worktree at all, and the greps need to appear in the
   audit's own output with their commands beside them.

5. **Mutation testing is in scope, and extends to the two overrides
   phase 1 never mutated.** Survey finding 7. The step re-runs the
   three mutations #4419 recorded, to confirm the counts
   independently, and then adds four: make `TrustedIssuer` and
   `MappingRule`'s overrides return `STATE_DELETED` unconditionally,
   and make each return `STATE_CREATED` unconditionally. A test must
   fail for each. If making an override always return `created`
   fails nothing, then no test proves that a genuinely dead issuer
   or rule is still collected, and the plan's own mission -- that a
   zombie its read path would not honour is "still marked `deleted`
   and collected, exactly as today" -- is unevidenced for two of its
   three types.

   This is more than phase 1's definition of done asked for, and
   that is the point: the audit's job is to find what the phase's
   own checks did not cover.

6. **The security lens runs at high effort.** The range decides
   whether a credential authenticates. A fail-open in the liveness
   predicate deletes live keys; a fail-closed one in the attribute
   readers turns a tier blip into a 503. Both are in the diff, and
   the second is new behaviour reaching the REST layer.

7. **The documentation lens reads the four guide pages, not the plan
   file.** `docs/operator_guide/database.md`,
   `docs/operator_guide/authentication.md`,
   `docs/developer_guide/database_internals.md` and
   `docs/developer_guide/subsystem_internals.md` are the
   user-visible surface and carry the facts that can disagree
   between pages. The 1,034-line plan file in the same diff is the
   instrument being executed, is reviewed by the planning and review
   cycle, and would otherwise dominate the lens by sheer line count.
   It is context for the lens, not a target.

8. **Findings land as their own pull request.** The master plan says
   so. This pull request carries three things and no fixes: the
   phase 1 close-out, this plan, and the audit record with its
   disposition table.

9. **Correct the master plan at source in the planning commit**
   (survey finding 2), rather than leaving a sentence which tells
   the next reader this file should not exist.

## Step plan

Steps 2c, 2e and 2f are read-only and independent, so they can be
spawned in parallel. Step 2b runs alone and first: it invokes
`pre-commit`, whose stash is repository-wide, and a concurrent agent
in the same worktree can silently revert it. Step 2d gets its own
worktree for the same reason -- it edits source to mutate it -- and
must not overlap step 2b.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | opus | none | **Done in the planning commit.** Write this plan. Close out phase 1 in its own commit first: master plan Execution table `Status` to `Complete` and `Merged` to `f79a23cd5c (#4419)`, and the `docs/plans/index.md` row to `In progress` / `1 of 2` with the phase 1 outcome and the two unfiled issues in its Intent cell. Correct survey finding 2 at source in the master plan's Execution preamble. Link this file from the Execution table and the index row. Run `pre-commit run --all-files`. Commit subjects: "Close out zombie repair phase 1." and "Plan the zombie repair push audit." |
| 2b | low | sonnet | none | Wave 1 and the wave 2 mechanical sweep, **running alone in this worktree before any other step**. Run `pre-commit run --all-files`, then `tox`. Do **not** run `tox -e genprotos` (no proto in range, survey finding 3) or `tools/mermaid-lint.sh` (no mermaid fence in the four touched guide pages, survey finding 4); say in the report that each was skipped and why. Then run `PUSH-AUDIT.md`'s four wave 1 style greps and five wave 2 mechanical commands, substituting `f79a23cd5c^1 f79a23cd5c` for `$RANGE`. Report every command with its literal output, or "no output". Planning already ran the greps clean (survey findings 5 and 6); report what you get regardless, and flag any disagreement with those findings as a finding in itself. If `pre-commit` or `tox` fails, report the failing test names and say whether the range touched the failing module (`git diff --name-only f79a23cd5c^1 f79a23cd5c`). Edit nothing; if a hook rewrites a file, restore it from a copy taken beforehand, never with `git checkout` (it would discard uncommitted work in the worktree). Commit subject: "Record wave 1 of the zombie repair audit." |
| 2c | high | opus | none | 2a code quality lens, read-only. Apply `PUSH-AUDIT.md`'s 2a brief and its shared blocks to the range. The production surface is about 294 lines across seven modules (survey finding 6); read all of it. Give a verdict on each of: (1) **survey finding 8** -- `_repair_zombies()` ends the pass on `DatabaseUnavailable` where brief 1b said to skip the object and continue. Read the function (`shakenfist/daemons/cluster/scheduled_tasks.py:1643-1703`), its docstring's reasoning about the watchdog and the `_grpc_call` retry budget, and the three tests that pin it. Say whether the divergence is right, and whether ending the pass for *one* type's unreadable object can starve the types after it (`_sweep_object_types` and `_record_sweep_stopped` are the relevant machinery). Reach your own verdict; do not inherit the plan's. (2) The three `zombie_repair_state()` overrides against the base-class contract in `shakenfist/baseobject.py:530-551`: are they three copies of one predicate that wants a helper, or three deliberately separate ones? (3) `if repaired_state in cls.ACTIVE_STATES` as the discriminator for the audit text -- is `STATE_CREATED` in `ACTIVE_STATES` for all three classes, and what does the branch print for a type whose override returns something else? (4) The fail-closed change in `mariadb.py`: are the three direct readers and the one gRPC reader consistent with each other and with the `get_namespace_key_by_name` precedent from #3522 they were modelled on? (5) The `CorruptMappingRule` path, which #4419's body says treats an undecodable rule as present-but-damaged -- confirm that is what the code does and that it is the right answer for a liveness predicate. Return findings with file, line and grade, or "no findings" plus the files read. Commit subject: "Record the zombie repair code quality review." |
| 2d | medium | sonnet | worktree | 2b test review lens **plus the mutation work of decision 5**. Read-only for the review; the mutation runs edit source, which is why this step gets its own worktree and must not overlap step 2b. First, the review: apply `PUSH-AUDIT.md`'s 2b brief to the range's 25 new test functions, and for each of the master plan's five mission statements say which test would fail if that statement were violated. Then the mutations. Run each one, one at a time, under `timeout` (a hung suite must not outlive the step), with `python -B` or `PYTHONDONTWRITEBYTECODE=1` so a same-size mutant restored within a second cannot leave live bytecode, and **never** concurrently with a `pre-commit` run. Restore each by the inverse edit or from a copy taken beforehand, never with `git checkout`. Re-run the three #4419 recorded, confirming the counts (delete the `NamespaceKey` override: 10 tests; restore `None`-on-`OperationalError` in `_direct_get_namespace_key_attributes`: 4; remove the rotation heal in `NamespaceKey.new()`: 2). Then add four: `TrustedIssuer.zombie_repair_state` and `MappingRule.zombie_repair_state` each made to return `cls.STATE_DELETED` unconditionally, and each made to return `cls.STATE_CREATED` unconditionally. For every mutation report which tests failed by name, or that none did. A mutation nothing catches is a finding, and the always-`created` pair is the one most likely to find a gap. Confirm `git status --short` is empty before you finish. Commit subject: "Record the zombie repair test review." |
| 2e | medium | sonnet | none | 2c documentation lens, read-only. Apply `PUSH-AUDIT.md`'s 2c brief to the four guide pages the range changed: `docs/operator_guide/database.md`, `docs/operator_guide/authentication.md`, `docs/developer_guide/database_internals.md`, `docs/developer_guide/subsystem_internals.md`. The master plan file is context, not a target (decision 7). The falsifiable question is whether any fact is stated differently on two pages: which object types are repaired forward and which are deleted, which types are exempt from zombie repair entirely (`node` and `namespace`), what happens when the liveness read fails, how many consecutive sweeps confirm a zombie, what heals a stateless key on a creation retry, and what a new object type's author must now decide. Also confirm the two claims phase 1's definition of done made about these pages still hold: `grep -rn "still live for" docs/developer_guide/subsystem_internals.md` returns nothing, and no page still says a zombie is repaired by writing `deleted` without qualification. Check the docstrings the range touched say the same as the pages (`shakenfist/namespace_key.py` `keys_with_attributes`, `shakenfist/namespace.py` `lookup_key`, and `reconcile_orphaned_objects()`). Apply the `plan-phase-references` rule: code and docs cite issue 3836, never this plan or its phases. Return findings, or "no documentation gaps found" plus the pages read. Commit subject: "Record the zombie repair documentation review." |
| 2f | high | opus | none | 2d security lens, read-only, at high effort because this range decides whether a credential authenticates (decision 6). Apply `PUSH-AUDIT.md`'s 2d brief to the range. Work through, in order: (1) **fail-open in the liveness predicate.** Construct the inputs under which each of the three overrides returns `deleted` for an object a read path would honour. For keys the read paths are `Namespace.lookup_key()` and `keys_with_attributes()`; for the other two, `from_db_by_name()` and the federation issuer listing in `shakenfist/federation.py`. Read those paths as they are today and say whether the predicate's "attributes row exists" is genuinely the same question each read path asks. An expired key is deliberately repaired to `created` and left to the expiry sweep -- confirm the sweep's `ACTIVE_STATES` filter actually picks it up, because if it does not, this change resurrects expired credentials. (2) **fail-closed turning into a denial of service.** The three attribute readers now raise `DatabaseUnavailable` where they returned `None`. Trace every caller to its API boundary and say which HTTP status each now returns during a tier blip, and whether any caller that previously degraded gracefully now fails a request. `POST /auth` is the one that matters most. (3) **the reconciler's own authority.** It writes state for arbitrary objects on a timer with no namespace scoping; confirm a non-`system` caller cannot influence which objects are confirmed as zombies, and that `_ZOMBIE_CANDIDATES` cannot be grown without bound by a hostile or broken client. (4) Whether any new log line, audit event or exception message can carry key material or a secret: read every `eventlog.add_event` and `LOG` call the range added, and the `State` message text. (5) The usual sweep of the range for the remaining classes in the 2d brief. Return findings with severity, file, line, vulnerability class and recommended fix. Critical and high must be resolved before this phase closes. Commit subject: "Record the zombie repair security review." |
| 2g | high | opus | none | Management session, not a sub-agent: grading and disposition are decisions. Before anything else, file the two issues from survey finding 9 -- the open-question-3 health signal (a namespace, `system` above all, with no usable key) and the legacy `namespace_attributes.keys` column retirement -- searching for a duplicate under at least three wordings each first, then point the master plan's Future work entries at them. Then pool 2b-2f's findings, grade each blocking, advisory or predating, and give each a disposition. Write a `### Findings` section and a `### Disposition` table into this file. Add an "Audit of phase 1" subsection to the master plan's Phase 2 section, recording the outcome in one sentence if the audit is empty. If nothing is blocking, set phase 2 to `Complete` in the master plan and the plan's index row to `Complete` / `2 of 2`, with no `Merged` cell for this phase (it closes itself out in its own pull request). Otherwise leave both `In progress`, cut the fix branch, and say what is outstanding. Run `pre-commit run --all-files`. Commit subject: "Record the zombie repair audit disposition." |

## Risks and mitigations

- **The audit reports clean because it read nothing.** The single
  most likely failure, and the reason `PUSH-AUDIT.md` has a range
  rule at all: `develop...HEAD` is empty on this branch. Mitigation:
  decision 1 fixes the range literally in every brief, and step 2b
  reports each command with its output, so a reviewer can see a
  non-empty diff was read. The management session checks that
  `git diff --stat f79a23cd5c^1 f79a23cd5c` shows 20 files in 2b's
  report before accepting any lens's "no findings".

- **A mutation run leaves the worktree dirty, or is restored with
  `git checkout` and discards work.** Mitigation: step 2d runs in
  its own worktree, restores by inverse edit or from a pre-taken
  copy, and must show `git status --short` empty before finishing.
  The management session verifies that in 2d's report.

- **Step 2b and step 2d collide over pre-commit's repository-wide
  stash.** Mitigation: 2b runs alone and first; 2d is in a separate
  worktree. Stated in the step plan preamble and in both briefs.

- **The 2a lens rubber-stamps survey finding 8 because this plan
  calls the divergence reasonable.** Mitigation: the brief states
  the finding and then explicitly tells the lens to reach its own
  verdict, and asks the specific follow-on question the plan did not
  answer (whether one type's unreadable object can starve the types
  behind it).

- **An empty audit is taken as proof the code is right.** 294 lines
  of production code can genuinely audit clean. Mitigation: an empty
  result is recorded as one sentence naming what was read, per the
  master plan, and decision 5's mutation extension gives the audit
  at least one place where a negative result is itself informative.

## Definition of done

Each item is falsifiable, and each was checked for runnability at
planning time.

- `pre-commit run --all-files` passes in this worktree.
- `tox` passes, or its failures are reported with the range's
  overlap with each failing module stated.
- Step 2b's report contains all nine `PUSH-AUDIT.md` commands with
  `f79a23cd5c^1 f79a23cd5c` substituted, each with its literal
  output, and states that `genprotos` and `mermaid-lint.sh` were
  skipped and why.
- `git diff --stat f79a23cd5c^1 f79a23cd5c` appears in the audit
  record showing 20 files changed, so the range demonstrably was not
  empty.
- All four judgment lenses have reported, each either with findings
  carrying file and line, or with "no findings" plus the list of
  files read.
- Seven mutations were run and reported by name with the tests each
  one broke: the three #4419 recorded, and the four from decision 5.
  Any mutation that broke no test is recorded as a finding.
- `git status --short` is empty in step 2d's worktree at the end of
  that step.
- Two issues exist and are linked from the master plan's Future
  work: the no-usable-key health signal, and the
  `namespace_attributes.keys` retirement. Neither existed before
  this phase (survey finding 9).
- Every critical and high security finding is resolved, or the phase
  is not closed.
- The master plan's Phase 2 section records the audit outcome, the
  Execution table shows phase 2 `Complete` with no `Merged` cell,
  and `docs/plans/index.md` shows the plan `Complete` / `2 of 2` --
  or both read `In progress` and the outstanding work is named.
- Survey finding 2 is corrected at source, checked as a pair against
  the master plan file alone: `grep -n "phase-02-push-audit"
  docs/plans/PLAN-zombie-repair-live-credentials.md` matches (the
  Execution table links this file), and `grep -n "no separate phase
  files" docs/plans/PLAN-zombie-repair-live-credentials.md` returns
  nothing (the old claim is gone). Both were run at planning time: the
  first matches at line 286, the second is empty. It needs both halves
  and that file scope -- a single grep for the *corrected* wording
  would pass by matching nothing at all, and a repository-wide grep
  for the old wording fails on this plan file, which quotes it twice
  on purpose.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

One gate, at step 2g: **present the graded findings table to the
operator before filing any issue or cutting any fix branch.** Grading
a finding blocking rather than advisory decides whether this plan
closes now or grows a fix pull request, and filing an issue is
outward-facing and awkward to retract. The audit may proceed through
2b to 2f without a gate; they are read-only, apart from 2d's mutations
in their own worktree.
