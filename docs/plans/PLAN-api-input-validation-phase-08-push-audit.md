# PLAN: API input validation phase 8 -- push audit

## Prompt

Plan phase 8 of `PLAN-api-input-validation.md`, the push audit, after
phase 7 merged as `91312b9a3` (#4232) on 2026-09-17.

## Why this phase exists

Seven phases rewrote how every request body reaches every handler in
this API. Each phase reviewed its own diff, and each review was bounded
by what that phase changed. Nothing has yet read the twelve merges as
one body of work, which is the only way to see the class of defect this
plan is most likely to have produced: a decision taken in one phase and
silently invalidated by a later one.

That is not a hypothetical risk here. Phase 7 recorded it as its own
lesson -- D46 was rewritten mid-phase and the rewrite orphaned three
statements reasoning from it, which four separate steps rediscovered
independently. Phase 7 could only look for that inside phase 7. The
same shape of staleness across phase boundaries is exactly what this
audit is for, and the survey below already found four instances of it
without looking hard.

Planning effort: high. Review effort: high, per the master plan's
treatment of the audit as the gate on declaring the plan complete.

## Scope

**In.** `PUSH-AUDIT.md` run over the twelve merges listed in decision 1,
wave 1 then wave 2, findings pooled across ranges and graded blocking or
advisory. Correcting the master plan's record of its own footprint
(F1, F2, F3). Disposing of every finding: fixed here, filed, or declined
in writing. Closing the plan out.

**Out.** New validation behaviour. The audit fixes what it finds wrong
with what shipped; it does not extend the plan. Two specific exclusions:

* **#4242, the unescaped `model` interpolation.** Filed 2026-09-17 from
  the phase 7 review. It wants a schema `pattern` on both `model`
  properties *and* XML escaping at render time in
  `Instance._create_domain_xml()`, plus sweep rows. That is a change to
  `shakenfist/instance.py` and to the domain template, which is outside
  what an audit of this plan's diff should be writing, and it needs its
  own functional coverage. The audit's job is to confirm the finding is
  recorded and correctly scoped, not to take it.
* **The five out-of-band issue-fix merges** in F3. See decision 3.

## What the survey found

The master plan's phase 8 row was written at phase 0, before any phase
executed. Every claim in it was checked against the tree. One is wrong
in a way that would have stopped the audit dead, and the plan's record
of where its own work landed is incomplete in two places.

### F1. The phase 8 row describes a range the audit is forbidden to use

The row reads: *"Runs `PUSH-AUDIT.md` over the accumulated diff of every
phase in this plan against `develop`"*. `PUSH-AUDIT.md:23-51` says in as
many words that this is not the range for a plan's push-audit phase:

> Running as a plan's push-audit phase, it is **not**. By then the
> plan's phases have merged, `develop...HEAD` is empty, and every
> command here would come back clean while reading nothing.

`develop...HEAD` on this branch is empty, as predicted. There is also no
single git range expressing the union: the twelve merges are interleaved
with 30-odd unrelated merges on the same files. The rule is one pass per
merge, findings pooled -- *"a plan with four merges gets four passes, not
one pass over a union that git cannot express"*.

Corrected at source in this phase's planning commit. The same defect
was found
and fixed in the agent operation deadlines audit (its F2), which suggests
the phase 8 row is copied from a template that predates the range rule;
worth a note in that template rather than a third independent discovery.

### F2. Phase 7's `Merged` cell was blank, which makes the plan unauditable

`PUSH-AUDIT.md:48-51`: *"If a plan's `Merged` column is missing or a cell
is blank, stop and say so -- that is an unauditable plan, and reporting a
clean run against an empty diff is the failure this range rule exists to
prevent."*

Phase 7's cell read `—` because the phase could not fill it from its own
branch; its definition-of-done item 14 says so explicitly and says to
take the SHA from the first-parent range after the merge. Done:
`git log --first-parent --oneline develop` gives
`91312b9a3 Merge pull request #4232`. Recorded in the planning commit.

This is the second time in two phases. #4222 existed solely to fill in
phase 6's blank cell after the fact. A cell that can only be filled after
merge will be blank at merge every time, so the closing task belongs to
whoever merges rather than to the phase -- noted in Future work.

### F3. The plan's footprint is twelve merges, not eleven

The `Merged` column names eleven. A twelfth is missing, and it is not a
small one.

**#3682 (`1e78fd1be`), "Render one schema-carrying body parameter per
operation", is phase 2 work and appears in no plan file at all.** Its own
body opens *"Part 2 of 3 for phase 2 of `PLAN-api-input-validation`,
following #3666"*. The phase 2 cell names only #3666 and #3685 -- parts 1
and 3. `grep -c '#3682' docs/plans/PLAN-api-input-validation*.md` is zero
across every file in the plan. The range is 6 files, 234 insertions, 90
deletions, and it is the change that collapsed each operation's body
parameters into one schema-carrying parameter -- which is to say it is
the change the entire compiled path in phase 3 was built on top of.
Auditing phase 2 from the recorded cell alone would read parts 1 and 3
and skip the part that did the structural work.

The master plan's note under the table lists the multi-pull-request
phases -- *"Phases 0 and 1 shared a pull request... Phase 4 landed across
two pull requests"* -- and does not mention that phase 2 landed across
three. Corrected in the planning commit.

**Five further merges touch this plan's surface and close issues the plan
tracks, but are not this plan's phases:**

| Merge | PR | Closes | Lines |
|---|---|---|---|
| `2001c7960` | #3677 | #3629 body-supplied `all` | 250 / 10 |
| `7bb80cc24` | #3699 | #3642 variadic handlers | 40 / 0 |
| `1fa3a2a32` | #3701 | #3616 `base.py` under mypy | 83 / 7 |
| `d8350644a` | #3714 | #3606 JWT nonce logging | 270 / 17 |
| `187a28d4f` | #4196 | #4194 agent put 500 | 27 / 1 |

All five are named in the master plan's *Where the tracked issues stand*
section as closed, so none is lost. They are not phases and each was
reviewed and merged on its own; decision 3 says what the audit does with
them.

A sixth, `f8c801ebe` (#4183, MAC format validation), is a special case
worth naming because it lands *inside a function phase 7 rewrote*. It
added format checking to `_netdesc_safety_checks()` -- the same function
phase 7 taught to refuse a null `network_uuid` -- and it closed #534,
which this plan tracked. Phase 6 records it as already done
(*"`macaddr` already validated via PR #4183 and is unchanged"*). Decision
3 covers it.

### F4. A tracked defect was closed by the merge, against the plan's own text

**#4223 is CLOSED as completed, and the plan says it is not fixed.**

`sfconductor` closed it at 2026-09-17T08:06:40Z, the same second it
merged #4232, with no comment. Nothing in the pull request asked for
that: the body carries only `Fixes #3612`, and
`git log 91312b9a3^1..91312b9a3` contains no `Fixes #` line at all. The
phase 7 commits say the opposite of fixed, in as many words -- *"filed as
#4223 and deliberately not fixed here"*, and *"#4223, whose lookup is
still wrong for anyone reaching it another [way]"*.

What phase 7 actually did was guard the two API routes: a netdesc whose
`network_uuid` is an explicit null is refused by `_netdesc_safety_checks`
on both instance create and interface hotplug. The defect #4223 was filed
against is the *lookup function*, which still resolves a null reference
to an arbitrary object for any caller reaching it by another path. The
guard closes the two doors this plan could see; the room is still open.

So the issue is closed, carries `automated-fix-attempted`, and will not
be picked up by the issue-fix workflow. Step 8g reopens it with a comment
restating the surviving scope. This is a finding about the *conductor's*
close-on-merge heuristic as much as about the issue, and Future work says
so.

### F5. Two defects this plan caused are recorded in no plan file

* **#4236** (ansible `sf_instance` sends `video` as a string) appears in
  the phase 7 plan three times and in the master plan zero times. It is a
  consequence of phase 7 typing the videospec.
* **#4242** (unescaped `model` into the domain XML) is new, filed
  2026-09-17 from the phase 7 review, and appears nowhere. It is the
  review's only `fix` item and the pull request merged without it.

By contrast #4098, #4161 and #4167 are all carried in the master plan.
Step 8g adds a *Known defects* subsection naming #4236, #4242 and the
reopened #4223, which is the disposition the agent operation deadlines
audit settled on for the same situation.

### F6. Three documentation defects survive on `develop`

All three were raised by the phase 7 re-review, which arrived after
approval; the auto-filer did not run for it, so nothing carries them.
Each is confirmed against the tree as it stands:

1. `docs/release_notes/v07-v08.md:427` -- *"That second one previously
   resolved to an arbitrary network"* describes the third item in its own
   three-item list, a leftover from when the list had two entries. The
   line is 107 characters where the paragraph wraps at ~72.
2. `docs/user_guide/usage.md:308` and
   `docs/developer_guide/api_reference/instances.md:157` both give the
   accepted `float` spellings as `true`/`yes`/`on`/`1` and their cases.
   marshmallow 4.3.1 also accepts `t`, `T`, `y`, `Y`, `f`, `F`, `n`, `N`;
   and "their cases" overstates it, since only the lower, Title and UPPER
   forms are in the sets, so `tRue` is a 400.
3. `usage.md` says the client and ansible interfaces convert what you
   type before it reaches the API. The ansible module converts only
   `('true', '1', 'yes')`
   (`shakenfist/deploy/collection/plugins/modules/sf_instance.py:333`),
   so `-N float=on` through ansible means False while the same string
   sent to the API means True. That divergence is real and undocumented.

These are in scope: they are documentation defects in this plan's own
diff, which is what 2c audits. Step 8d takes them.

### F7. What is not a finding

Checked and clean, recorded so no step re-derives them:

* **Every SHA the plan cites exists on `develop`.** All 12 merge SHAs
  verified with `git merge-base --is-ancestor`; all are merge commits, so
  `<sha>^1..<sha>` is well-formed for each. Two SHAs in the phase 6 plan
  (`03ea26514`, `d6b84b365`) resolve to nothing, but see below -- that is
  a separate, real finding.
* **`index.md` arithmetic is right.** Line 111 reads `8 of 9`,
  `In progress`. Phases 0-8 is nine, eight are complete. No change until
  step 8g.
* **The master plan's corrective paragraph is correct.** Lines 398-412
  explain that an earlier revision named `03ea26514` and `d6b84b365`, and
  give the real SHAs. Citing the dead ones there is deliberate.

One thing that *is* a finding, found by the same check: the phase 6 plan
at `PLAN-api-input-validation-phase-06-required.md:977` still asserts the
dead SHAs as live fact -- *"#3269 and #323 both carry `Fixes #NNNN` in
their commits (`03ea26514` and `d6b84b365`) and will auto-close on
merge"*. #4222 fixed the master plan and left the phase plan behind.
Corrected in the planning commit.

## Decisions

**1. The audit ranges are these twelve, one pass each.**

| # | Range | Phase | Files | +/- |
|---|---|---|---|---|
| 1 | `25e03b764^1..25e03b764` | 0+1 (#3620) | 31 | 4374 / 149 |
| 2 | `ad759f25e^1..ad759f25e` | 2 (#3666) | 8 | 545 / 38 |
| 3 | `1e78fd1be^1..1e78fd1be` | 2 (#3682) | 6 | 234 / 90 |
| 4 | `e9b28a65a^1..e9b28a65a` | 2 (#3685) | 28 | 1597 / 111 |
| 5 | `3790aa487^1..3790aa487` | 3 (#3726) | 20 | 2947 / 63 |
| 6 | `0c7eacf48^1..0c7eacf48` | 3 (#3742) | 3 | 63 / 4 |
| 7 | `6274cd924^1..6274cd924` | 3 (#3835) | 1 | 63 / 0 |
| 8 | `1c203b111^1..1c203b111` | 4 (#4101) | 23 | 2200 / 150 |
| 9 | `f1040a23b^1..f1040a23b` | 4 (#4141) | 23 | 1604 / 167 |
| 10 | `b3de0a44f^1..b3de0a44f` | 5 (#4162) | 20 | 1762 / 113 |
| 11 | `81aa9a7d0^1..81aa9a7d0` | 6 (#4199) | 22 | 3312 / 175 |
| 12 | `91312b9a3^1..91312b9a3` | 7 (#4232) | 22 | 6001 / 106 |

77 distinct files, 24,702 insertions, 1,166 deletions. 21 of the files
are production Python outside `tests/`, all but three of them under
`shakenfist/external_api/`.

**2. Wave 1 failures are presumed pre-existing unless they touch this
plan's files.** `pre-commit` and `tox` run against the tree as it stands,
not against each historical range, so a failure is a statement about
`develop` today. Check it against the 77-file list before treating it as
this plan's; if it is not in the list, record it and carry on rather than
stopping the phase.

**3. The six out-of-band merges are read as context, not audited as
ranges.** Each was reviewed and merged on its own, and `PUSH-AUDIT.md`'s
range rule is explicit that the ranges come from the `Merged` column.
Re-auditing them would be scope creep and would file duplicate findings.
But two of them changed code this plan owns, so the audit *reads* them
where they bear on a range it is auditing: `f8c801ebe` (#4183) added MAC
format checking to `_netdesc_safety_checks()`, which range 12 then
rewrote, so step 8e reads the netdesc guard chain as it stands today
rather than as range 12 left it; and `2001c7960` (#3677) changed how
`all` reaches a handler, which bears on range 5's compiled path. Neither
gets its own pass.

This is the decision a reviewer is most likely to argue with, so the
reasoning is worth stating plainly. The opposite choice -- audit
everything that touched the surface -- sounds more thorough and is
actually worse: it would pull in 30-odd merges from scheduler
reservations, auth federation and the VDI work, none of which this plan
is accountable for, and it would bury the findings that are this plan's
in findings that are not. The `Merged` column is the accountability
boundary. Where that boundary is wrong, the fix is to correct the column
(F3), not to abandon it.

**4. The audit reads the decisions as a set before it reads code.**
Phase 7's recorded lesson is that a changed decision silently orphans
every statement reasoning from it, and that it took four independent
rediscoveries to notice. This plan carries decisions D1 through D50
across eight files. Step 8a's whole job is to read them as one set and
list every pair where a later decision narrows, contradicts or supersedes
an earlier one, then check the earlier one's dependents. This is
cheaper before the code review than after, and it is the single highest
-value thing this audit can do that no phase could do for itself.

**5. Blocking versus advisory is graded against what shipped, not
against what would be nicer.** A finding is blocking if it means the
shipped behaviour is wrong, undocumented in a way that misleads, or
insecure. Everything else is advisory and goes to an issue. The plan is
not complete until each blocking finding is fixed or declined in writing
here, per the master plan's phase 8 row.

**6. The four judgment steps run in parallel, and only one of them
commits.** `PUSH-AUDIT.md:17-19` says the four judgment agents are
independent, and they are: 8c (2a, code quality), 8d (2c,
documentation), 8e (2d, security) and the review half of 8f (2b, tests)
have no ordering between them. They all follow 8b, because wave 2 is
only worth spending on if wave 1 passes.

What they cannot do is commit. They share a worktree, and pre-commit's
stash is repo-wide, so two agents committing at once can silently revert
each other's uncommitted work. So the four write only to their own
section of this file and do not commit; 8f collects all four and makes
the single commit once the others have finished. Each brief says so.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | high | opus | none | **Read the fifty decisions as a set.** Per decision 4, and before any code is read. The decisions live in the master plan (D1-D9, plus the carried-forward sections at lines 513, 579, 665) and in the seven phase plans (D10-D50). Build one list, then find every pair where a later decision narrows, contradicts or supersedes an earlier one. For each such pair, grep the whole repository -- plans, code comments, docstrings, `docs/`, release notes -- for statements reasoning from the superseded half, exactly as phase 7's step 8 had to do for D46. Known starting points, given as evidence rather than as the answer: D46 was rewritten mid-phase-7 and phase 7 believed it had found all five dependents; D49's "ordering obligation" language was retracted; D42's handler-guard count went from two to three during the phase 7 review, and the review round had to correct six places. Check whether phase 7 actually caught all of D46's dependents or only the ones inside phase 7. Also check the four decisions that *enable rollback* -- D34, D42, D50 and the `warn`/`off` contract -- still say the same thing as each other, since they were written three phases apart. Write the result into this file under *Decision set review*. Do not commit; 8f commits. |
| 8b | medium | sonnet | none | **Wave 1, mechanical.** Run `pre-commit run --all-files` and `tox`, recording output rather than asserting a result; per decision 2 a failure is presumed pre-existing unless the file is in the 77-file list, which is reproducible as `for s in <the twelve>; do git diff --name-only "$s^1..$s"; done \| sort -u`. Then run `PUSH-AUDIT.md`'s wave 1 style greps against each of the twelve ranges in decision 1 -- over-120-character lines, stray `print(`, new `etcd` references, untagged `mariadb.get_all_*` -- and its style-conformance judgment brief. The pushdown and `etcd` greps are predicted empty across all twelve because this plan does not touch the database layer; confirm that in one line each rather than assuming. `protos/` is not in the file list, so skip the proto-freshness check and say why. Write results under *Wave 1*. Do not commit; 8f commits. |
| 8c | high | sonnet | none | **Wave 2 mechanical sweep plus 2a, code quality.** Run `PUSH-AUDIT.md`'s wave 2 mechanical sweep (TODO/FIXME/HACK/XXX, new `# noqa` / `# type: ignore` / `pragma: no cover`, new-test ratio, documentation files touched, new `subprocess.` / `os.system` / `shell=True`) across the twelve ranges, then work the 2a brief. The weight is in three places. First, `shakenfist/external_api/validation.py` is the plan's centrepiece and was built across phases 3, 4, 6 and 7 -- `_field()` in particular gained a branch per phase, so ask whether it is one function with four cases or four functions sharing a name. Second, `shakenfist/external_api/base.py` carries `ARGTYPES`, `swagger_helper()` and the three structured schema constants, and phases 2, 6 and 7 each added a token vocabulary; check the three vocabularies are one vocabulary. Third, `_netdesc_safety_checks()` and the diskspec helpers in `shakenfist/external_api/instance.py` are now a guard chain assembled by four different phases plus #4183 (decision 3) -- read it as it stands today and say whether the guards are ordered deliberately or by accretion, and whether any is unreachable the way the IDE refusal at `instance.py:876` is known to be. Apply the comment-proportion shared block. Grade each finding blocking or advisory per decision 5. Write under *2a. Code quality*. Do not commit. |
| 8d | high | sonnet | none | **Wave 2c, documentation review.** Work the `PUSH-AUDIT.md` 2c brief, including its readme-discipline, llm-doc-discipline and plan-phase-references shared blocks, across the sixteen `docs/` files in the twelve ranges. Start by taking F6's three confirmed defects, which are already diagnosed and need only fixing: the release note's "That second one" at `docs/release_notes/v07-v08.md:427` (make it name the null `network_uuid` case, and rewrap the 107-character line to the paragraph's ~72); the incomplete `float` spellings at `docs/user_guide/usage.md:308` and `docs/developer_guide/api_reference/instances.md:157` (marshmallow also takes `t`/`T`/`y`/`Y`/`f`/`F`/`n`/`N`, and `tRue` is a 400 -- prefer saying a JSON boolean is the expected form and the string spellings are read but should not be relied on, over enumerating sixteen tokens); and the false claim that the ansible interface converts what you type, when `sf_instance.py:333` converts only `('true', '1', 'yes')` so `-N float=on` means False there and True at the API. Then the real question, which F6 does not answer: this plan documents the same error contract in the API reference, `writing_an_endpoint.md`, the operator guide and the release note, written across four phases. Check they say the same thing, and check each against `validation.py` rather than against each other. `shakenfist/tests/external_api/test_api_reference_specs.py` already enforces the instances-page half mechanically; say whether the same treatment is owed to the error-contract half or whether that would be over-fitting. Write under *2c. Documentation*. Do not commit. |
| 8e | high | opus | none | **Wave 2d, security review.** Work the `PUSH-AUDIT.md` 2d brief across the twelve ranges. This plan's whole subject is untrusted input, so this is the step that matters most. Four areas. **The rollback path:** `API_VALIDATION_MODE=warn` and `off` are the operator's escape hatch (D34, D42), and they turn off every schema check while leaving the handler guards. Enumerate what an authenticated caller can send under `off` that `enforce` refuses, and say which of those the handlers still catch -- the sweep's 104 rows are the inventory, and `NestedSweepOffTestCase` already measures `off` on all of them, so use that rather than re-deriving. The question the sweep cannot answer is whether `off` is *safe* or merely *documented*. **Injection:** #4242 is already filed for `network[].model` and `video.model` reaching `libvirt.tmpl` unescaped through a `jinja2.Template` with autoescape off (`instance.py:2072`, template lines 139 and 206); do not fix it, but do check whether it is the only instance -- walk every value this plan declared as a free string and ask where it is rendered. `disk[].type` was closed by phase 7's enum and `macaddr` by #4183; the question is what else. **Error content:** D31 says a 500 answers a bare `server error` with no exception detail, and phase 5 deleted the `except TypeError` arm to get there. Verify no path added since re-introduces interpreter text, including under `warn`, where phase 5's own D25 notes an undeclared body key now takes the generic 500. **Authorisation:** the ref decorators' `namespace` kwarg was undeclared on 55 handlers until phase 4 (#3739); confirm the derivation phase 4 added actually prevents recurrence rather than fixing the 55 instances. Grade per decision 5. Write under *2d. Security*. Do not commit. |
| 8f | high | opus | none | **Wave 2b, test review, and collect.** First work the `PUSH-AUDIT.md` 2b brief across the twelve ranges. This plan added a lot of test surface -- the required sweep, the nested sweep (104 rows, three modes), the compiler tests, `test_api_reference_specs.py`, `test_openapi_spec.py`'s `STRUCTURED_PARAMETERS` -- so the question is not whether there is coverage but whether it asserts behaviour or pins implementation. Two specific questions no phase could ask itself. First, the counterfactual: phase 7's review found a null `video.model` was stored and rendered as `type='None'`, and found `"false"` floated an interface, both in code that had a 99-row sweep over it. Read the sweep as it was before that review and say what shape of row would have caught each, then check whether the rows added since have that shape or merely pin the fixed answer. Second, functional coverage: CLAUDE.md prefers functional to unit tests, and this plan's cluster CI cases are in `shakenfist/deploy/shakenfist_ci/`; name what the sweep proves that CI does not, and whether any of it *should* be in CI because it is a contract a client depends on. `tools/mutate-nested-sweep.sh` exists and runs 16 mutations with 0 survivors -- run it to confirm, and say whether the mutation set covers the guards added in the review round or only the original ones. Then collect: gather every finding from 8a to 8e, resolve any two that contradict, and commit the whole audit. Per decision 6 no other step commits, so this is the phase's first commit and it carries 8a through 8f; expect to be reconciling four sections written in parallel. Commit subject: "Audit the API input validation plan." |
| 8g | high | opus | none | **Grade, dispose, close out.** Take every finding from 8a to 8f and give each a disposition: fixed here, filed as #NNNN, or declined with a reason in writing. Fix the blocking ones on this branch; F6's three documentation defects are already assigned to 8d and should be fixed rather than filed. Then discharge F4 and F5. **F4:** reopen #4223 with a comment saying it was closed by `sfconductor` on the merge of #4232 without being fixed, that phase 7 guarded the two API routes (`_netdesc_safety_checks` on instance create and interface hotplug) but the lookup function still resolves a null reference to an arbitrary object for any other caller, and quote the phase 7 commit saying so; remove `automated-fix-attempted` so the issue-fix workflow can pick it up. **F5:** add a *Known defects* subsection to `docs/plans/PLAN-api-input-validation.md` naming #4242 (the unescaped `model`, out of scope per Scope, with the two-part fix it needs), #4236 (the ansible videospec string) and the reopened #4223, each with what it is and why it is not fixed here. Then close the plan: phase 8 to `Complete` in the Execution table, `docs/plans/index.md` line 111 to `9 of 9` and `Complete`, and the audit's overall result written into this file in a paragraph a reader can act on. If the audit found nothing blocking, say that in one sentence -- the master plan's phase 8 row asks for exactly that. Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: "Close out API input validation phase 8." |

## Corrections applied at source

Per the planning skill, the false claims the survey found are corrected
where they live rather than only recorded here, and they are corrected
**in this planning commit** rather than in a step, because F2 is a
precondition: `PUSH-AUDIT.md` refuses to run against a plan with a blank
`Merged` cell, so the audit cannot start until it is filled. The
corrections are:

* The phase 8 row's range (F1) -- master plan.
* Phase 7's blank `Merged` cell (F2) -- master plan.
* Phase 2's missing third pull request (F3) -- master plan, table and
  note.
* The phase 6 plan's two dead SHAs (F7) -- phase 6 plan.
* Phase 7's definition-of-done item 14 (F2) -- phase 7 plan.

A later step should not rediscover these; the phase 8 row in the master
plan already names the twelve ranges, so step 8a can start reading.
`docs/plans/index.md` needs no change yet: it carries one row per plan,
not per phase, and `8 of 9` / `In progress` stays true until 8g.

## Risks and mitigations

**Twelve passes is a lot of audit, and the middle ranges are small.**
Ranges 6 and 7 are 63 lines each. The risk is that uniform effort across
twelve ranges spends the budget on trivia and arrives tired at range 12,
which is the largest and newest. Mitigation: each judgment step's brief
names where the weight is, and the briefs are written so a small range
can be dispatched in a line. The step author checks the resulting
sections are not uniformly long.

**Four agents in one worktree can revert each other.** pre-commit's stash
is repo-wide. Mitigation: decision 6 -- 8c, 8d, 8e and 8f write only
their own section and do not commit; 8f alone commits, after the other
three have finished. The management session checks
`git status` before dispatching and after collecting.

**The audit finds something blocking in phase 3 or 4 that is now load-
bearing.** Six phases have been built on the compiled path. A blocking
finding there is expensive. Mitigation: it is still the right thing to
find, and decision 5's "declined in writing" is an honest outcome. What
the phase must not do is downgrade a finding to advisory because fixing
it is inconvenient; the management session names anything so downgraded.

**A finding duplicates an auto-filed review issue.** Seven phases of
reviews have filed issues under `fix` and `document`. Mitigation: 8g
searches open issues before filing, and records the existing number
rather than opening a second.

## Definition of done

1. `PUSH-AUDIT.md` wave 1 and wave 2 have each been run against all
   twelve ranges in decision 1, and this file records, per range, what
   was run and what it said -- not a pooled summary that cannot be traced
   back to a range.
2. The master plan's phase 2 cell names three pull requests, its phase 7
   cell names `91312b9a3` (#4232), and the note under the table says
   phase 2 landed across three. `grep -c '#3682'` over the plan files is
   no longer zero.
3. The phase 8 row no longer says "against `develop`", and names the
   twelve ranges instead.
4. No plan file in this plan cites a SHA that is not an ancestor of
   `develop`, except `03ea26514` and `d6b84b365`, and every mention of
   those two sits inside a passage which says they are dead and names
   what replaced them (`ec406a78a` and `c7a432886`). Falsifiable by
   running this from the repository root: every line it prints must name
   one of those two SHAs, and no other:

   ```
   for f in docs/plans/PLAN-api-input-validation*.md; do
       grep -oE '`[0-9a-f]{7,40}`' "$f" | tr -d '`' | while read s; do
           git merge-base --is-ancestor "$s" develop 2>/dev/null \
               || echo "$f $s"
       done
   done
   ```

5. Every finding has a disposition: fixed in this branch, filed as a
   numbered issue, or declined with a reason. No finding is recorded
   without one.
6. Every blocking finding is fixed or declined in writing here.
7. #4223 is open again, carries a comment distinguishing the two API
   routes phase 7 guarded from the lookup function that is still wrong,
   and no longer carries `automated-fix-attempted`.
8. `docs/plans/PLAN-api-input-validation.md` has a *Known defects*
   subsection naming #4242, #4236 and #4223.
9. The three documentation defects of F6 are fixed: no page gives an
   incomplete `float` spelling set, the release note's three-item list
   refers to its own third item correctly and wraps at the paragraph's
   width, and no page claims the ansible module converts spellings it
   does not.
10. The Execution table reads `Complete` for phase 8 and
    `docs/plans/index.md` line 111 reads `9 of 9`, `Complete`.
11. `python3 tools/check-plan-status.py` passes and
    `pre-commit run --all-files` is clean.
12. If the audit found nothing blocking, this file says so in one
    sentence, per the master plan's phase 8 row.

## Back brief

Before starting, confirm:

1. **The twelve ranges of decision 1 are the right twelve.** This is the
   decision everything else rests on, and F3 shows the recorded column
   was wrong once already. If #3682 is in, is anything else? The check
   that found it is in F3 and is cheap to re-run.
2. **Decision 3 -- the six out-of-band merges are context, not ranges.**
   Named as the most arguable decision. A reviewer who disagrees should
   say so now, because it changes the shape of 8c and 8e.
3. **Decision 6's no-commit rule for the parallel steps.** It trades some
   throughput for not losing work. Worth confirming before four agents
   are dispatched.

A gate before 8f commits: the four parallel sections should be read
together once before they are reconciled into one document, because that
reading is where a contradiction between two agents is cheapest to
notice.

## Findings

*Filled in by steps 8a through 8f.*

## Future work

* **A `Merged` cell that can only be filled after merge will be blank at
  merge.** It has now been filled in retrospectively twice, by #4222 for
  phase 6 and by this phase for phase 7, and in both cases the phase's
  own definition of done recorded that it could not do it. The task
  belongs to whoever merges the pull request, not to the phase. Worth a
  line in `PLAN-TEMPLATE.md`.
* **The phase 8 row's range was wrong here and in the agent operation
  deadlines plan.** Two independent discoveries of the same defect in two
  plans suggests the row is copied from a template written before
  `PUSH-AUDIT.md` gained its range rule. Worth fixing in the template so
  there is not a third.
* **`sfconductor` closed #4223 on merge without being asked** (F4). The
  pull request body named only `Fixes #3612`, and no commit in the range
  carried a closing keyword. Whatever heuristic closed it can close any
  issue a branch merely discusses, which is a way to lose a defect
  silently. Worth investigating in `private-ci`.
