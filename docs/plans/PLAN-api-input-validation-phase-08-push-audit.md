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
  (`03ea26514`, `d6b84b365`) resolve to nothing -- they are pre-rebase
  objects which exist in no clone but the one that wrote them, and the
  commits they were meant to name landed as `ec406a78a` and
  `c7a432886` -- but see below: that is a separate, real finding.
* **`index.md` arithmetic is right.** Line 111 reads `8 of 9`,
  `In progress`. Phases 0-8 is nine, eight are complete. No change until
  step 8g.
* **The master plan's corrective paragraph is correct.** Lines 398-412
  explain that an earlier revision named `03ea26514` and `d6b84b365`,
  and give the real SHAs (`ec406a78a` and `c7a432886`). Citing the dead
  ones there is deliberate.

One thing that *is* a finding, found by the same check: the phase 6 plan
at `PLAN-api-input-validation-phase-06-required.md:977` still asserts the
dead SHAs as live fact -- *"#3269 and #323 both carry `Fixes #NNNN` in
their commits (`03ea26514` and `d6b84b365`) and will auto-close on
merge"*. Those two objects are dead and the commits landed as
`ec406a78a` and `c7a432886`. #4222 fixed the master plan and left the
phase plan behind. Corrected in the planning commit.

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

`PUSH-AUDIT.md` wave 1 and wave 2 were run against all twelve ranges of
decision 1. Wave 1 (8b) is clean: `pre-commit run --all-files` passes all
eleven hooks and `tox` passes `py3`, `flake8` and `cover`, so decision 2's
presumed-pre-existing fallback was never needed. 8a read the fifty decisions
as a set before any code was read, per decision 4; wave 2's four judgment
steps (2a, 2b, 2c, 2d) then ran in parallel per decision 6. All six sections
are collected below in the order 8a, 8b, 2a, 2b, 2c, 2d, each keeping its own
evidence and citations.

**Result: four blocking findings, and none of them is a vulnerability.**
Three are false statements about what the validation layer refuses — a
decision changed in one phase and its documentation left behind in another,
which is the exact class decision 4 predicted. The fourth is a real
behavioural defect at the shipped default mode: request parameters whose
published schema says the opposite of what the server does. (This paragraph
read "or a wrong answer at the shipped default" when 8f collected it. 8g
corrected it: that is true of the three documentation findings and false of
B-4, which is measured at `enforce`.) Every advisory finding is listed in its
step's section. Disposition is 8g's.

### Reconciliation across the four parallel steps

The six steps reported eight blocking findings between them, and five of
those eight are two findings counted more than once. **Three steps found B-1
independently, by three different methods**, and two steps found B-2. They
are merged below and appear once each. The three directions are recorded
rather than collapsed silently, because the fact that three independent
methods converged on one paragraph — and that each method found a site the
other two could not reach — is the audit's strongest single piece of evidence
that cross-phase decision drift is this plan's characteristic defect:

* 8a found B-1 by reading the fifty decisions as a set (its pair P1, findings
  1 and 2) — D17's "recorded and never enforced" residue.
* 8d found B-1 by reading the four places the error contract is documented
  and checking each against `validation.py` rather than against each other.
* 8e found B-1 by measuring the real authenticated stack, and found two sites
  the other two missed: `shakenfist/config.py:291-293` and
  `shakenfist/external_api/instance.py:654-656`.
* 8a's findings 3 and 4 and 8e's S4 are one finding about `warn` restoring
  pre-0.8 behaviour; merged as B-2.

Three corrections were applied during collection, each verified against the
tree by the management session before the section was written:

1. **8a's finding 14 is half right.** The stale-sentence half holds and is
   kept. The half claiming D44's mechanism is disproven is dropped:
   `validation.py:534` builds every field with
   `{'required': required, 'allow_none': not required}`, so a *required*
   field gets `allow_none=False` and does refuse an explicit null, and
   `base.py:516` marks `network_uuid` required. 8a had read the comment at
   `validation.py:1110-1111` ("every compiled field is `allow_none=True`",
   which explains why `validate_request` pre-treats an explicit null as
   missing) out of its context. The mechanism is not disproven; whether that
   comment misleads the next reader is recorded as a minor advisory in 2a's
   territory instead.
2. **2b's B1 loses its cross-namespace claim.** `artifact.py:484-488` puts
   the `request_namespace() != 'system'` check *inside* the `if shared:`
   branch, so an unprivileged caller sending `"false"` gets a 403. Regraded
   as an admin foot-gun. B-4 stays blocking on the `uefi` and `secure_boot`
   members, which need no privilege.
3. **2c's `sf-client` finding is verified and is not fixable here.**
   `client-python/shakenfist_client/commandline/instance.py:327` is
   `value = s[1] in ['true', 'True']`. It is a `client-python` defect; 8g
   files it.

One finding was actioned during collection and must not be filed twice: 8e
found that #4242's stated fix misses a second sink, the hotplug f-string at
`shakenfist/instance.py:2837-2843`, which `_create_domain_xml()` does not
cover. That has already been posted to #4242 as a comment. See 2d's S3.

### The four blocking findings

**B-1. "A parameter declared required but not supplied is recorded and never
enforced" is false, in four places, one of which is the rendered operator
configuration reference.**

Phase 4's D17 kept `missing-required` out of the enforcement decision; phase
6's D32/D37 deleted the filter, and the shipped state is
`shakenfist/external_api/base.py:2236-2246` — *"Every reason code is
enforceable, missing-required included"*. Phase 6 corrected the release note
and one half of one guide, and missed everything else. Measured through the
full authenticated stack at both modes:

```
MODE=enforce  POST /instances (no name)      400 {'error': 'name: declared required but not supplied'}
MODE=enforce  POST /networks (no netblock)   400 {'error': 'netblock: declared required but not supplied'}
MODE=off      POST /instances (no name)      400 {'error': 'instance name must be specified'}
MODE=off      POST /networks (no netblock)   400 {'error': 'cannot parse netblock: None ...'}
```

The union of sites, from all three steps:

1. **`shakenfist/config.py:291-293`** — *"answers 400 ... (except for
   missing-required findings, which are recorded and never enforced)"*. This
   is the `API_VALIDATION_MODE` description rendered into the configuration
   reference: the operator's own account of what the control does.
   Introduced by range 5 (`3790aa487`, phase 3), reworded but not corrected
   by range 9 (`f1040a23b`, phase 4), untouched by range 11 (`81aa9a7d0`,
   phase 6) which made it false.
2. **`docs/operator_guide/logging.md:175-176` and `:181-182`** — says it
   twice, and nothing elsewhere in that file corrects it. An operator
   reading only this page would conclude an omitted required field is merely
   logged under the default mode.
3. **`docs/developer_guide/writing_an_endpoint.md:351-352`** — *"a finding
   other than `missing-required` answers `400`"*, which the **same file**
   contradicts sixty lines later at `:409-411`: *"`required` is enforced. An
   omitted parameter and an explicit JSON `null` both answer `400
   <parameter>: declared required but not supplied`"*. Whoever added the
   correct section in phase 6 read past the stale paragraph.
4. **`shakenfist/external_api/instance.py:654-656`** — *"`name` is declared
   required, but required-ness is deliberately not enforced (decision D17)"*.
   The guard it sits on is still needed, because `warn` and `off` still reach
   it; the reason given for its existence is wrong.

`docs/release_notes/v07-v08.md:210` carries the same words and is **correct**,
because it is explicitly historical — *"At this point in the rollout"*, with
a forward pointer to the required-ness entry. **Do not change it.** That
asymmetry is itself worth recording: an append-only changelog survived seven
phases of decision churn and two edited-in-place reference documents did not.

Blocking under decision 5's *"undocumented in a way that misleads"*: sites 1
and 2 are the operator-facing description of a security control's behaviour.
Four edits, no behaviour change.

**B-2. `warn` is documented as restoring pre-0.8 behaviour exactly, and fails
to in two independent directions.**

`docs/operator_guide/logging.md:185-188`: *"`warn` restores the pre-v0.8.0
behaviour: findings are recorded and change no response, so the request is
answered exactly as it always was."* False twice over:

* **D25 (phase 5):** an undeclared body key under `warn` now answers a
  recorded 500, not the pre-0.8 400. `writing_an_endpoint.md:363-373` and
  `v07-v08.md:240-255` both say so; `logging.md` never mentions it, and also
  omits the cost `writing_an_endpoint.md:375-384` warns about — each such
  request writes an exception record and an ERROR line.
* **D42 (phase 7):** the three new handler guards refuse, at `warn` and
  `off`, requests that previously answered 200 or 507 — a netdesc with
  `{"network_uuid": null}`, a videospec with a null `model` or `memory`, and
  the six diskspec shapes asking for neither size nor base. Ten rows of
  `test_nested_sweep.py` are marked `moves at warn` for exactly this reason.

`docs/developer_guide/writing_an_endpoint.md:369-371` carries the same false
claim in the developer's words — *"An operator choosing the rollback gets
requests that were working kept working"* — and is the statement D42 leans on
while breaking. The release note is the only document that gets both halves
right (`v07-v08.md:240-255` and `:419-431`).

Blocking: an operator plans a rollback from this page. Note the divergence is
in the *safe* direction in the D42 case, which is why this is a documentation
finding and not a behavioural one.

**B-3. The master plan's front-page account of a live security defect
contains a sentence phase 7 made false.**

`docs/plans/PLAN-api-input-validation.md:377` says `_netdesc_safety_checks`
*"tests for presence rather than for a value"*. That was true when written
and is false now: phase 7 changed it to
`if netdesc.get('network_uuid') is None:` precisely because presence was not
enough (`shakenfist/external_api/instance.py:366`).

This is the plan's own record of #4223, which F4 says was wrongly closed on
merge and which 8g reopens. The reopen comment reasons from this paragraph,
so it has to be right first: what closes the reachable path is the phase 7
handler guard at `warn`/`off` **plus** `required`-means-not-null in the
schema at `enforce` (`validation.py:534`, `base.py:516`) — a two-part
closure, in the past tense. Per correction 1 above, do **not** argue that the
schema fails to close it.

Blocking on the narrow ground that it is the plan's record of a live defect
and the input to 8g's issue comment, not on 8a's original wider ground.

**B-4. `validation.declared_boolean()` closed one site in a class of about
ten, and two members that need no privilege are still shipping.**

The phase 7 review found that `{"float": "false"}` was published as a valid
boolean meaning False and floated the interface anyway, and wrote
`validation.declared_boolean()` (`shakenfist/external_api/validation.py:166-198`)
to fix it. Its docstring states the general rule: the compiled path is
check-only (D14), so `validate_request` discards the deserialised result
(`base.py:2219-2224`) and the handler is handed the raw body — and every
falsy string spelling marshmallow accepts is a non-empty string, which Python
reads as true. The docstring's own words: *"That is worse than the
unvalidated state it replaced: before this phase the specification said
nothing about the value, and now it says something the server contradicts."*

The API declares 19 `boolean` parameters. `declared_boolean` is called at
exactly one site, `shakenfist/external_api/instance.py:482`. Probed the
others against the shipped fixtures at `enforce`:

```
STATUS uefi=false -> 507    uefi reached Instance.new as 'false' (str)   machine_type='pc'
STATUS secure_boot=off -> 507  secure_boot reached Instance.new as 'off' (str)  machine_type='q35'
shared=False   -> 200 shared_in_reply=False
shared='false' -> 200 shared_in_reply=True
```

* **`uefi`** (`instance.py:579` declares it `boolean`, read at `:967`) —
  `{"uefi": "false"}` boots the instance with UEFI. Any authenticated caller.
* **`secure_boot`** (`instance.py:603`, read at `:689` and `:692`) —
  `{"secure_boot": "off"}` turns secure boot on and switches the machine type
  to `q35`. With *both* sent as falsy strings the `secure_boot and not uefi`
  refusal at `:689` also fails to fire, because both operands are truthy
  strings: the guard that exists to catch an inconsistent pair is defeated by
  the same bug.
* **`shared`** (`artifact.py:437` and `:559`, read at `:484` and `:649`) — per
  correction 2 this is an **admin foot-gun, not a tenant escalation**. The
  `request_namespace() != 'system'` check is inside the `if shared:` branch,
  so an unprivileged caller sending `"false"` gets a 403. A `system`
  operator who sends `"false"` intending not to share gets an artifact shared
  with every namespace, silently.

Same shape by inspection, not probed: `thin` (`snapshot.py:46`, read at
`:76`) and `all` on the instance, network, artifact and agent-operation list
routes, where a falsy string widens the listing to include deleted objects.

Two sites in the class are **not** affected, and the difference is the
argument for fixing this at the generator rather than instance by instance:
`provide_dhcp`/`provide_nat`/`provide_dns` are coerced downstream (probed:
`provide_dhcp='false'` stores `False`), and `confirm` on the three delete-all
routes uses `if confirm is not True:` (`instance.py:1150`, `network.py:351`,
`artifact.py:518`), which fails safe. The class is not uniform, which is
exactly why a reviewer's eye keeps missing members and why 2b's recommended
derived differential test is the right answer.

Blocking on `uefi` and `secure_boot`: the published specification says
something the server contradicts, for any authenticated caller. 2b's section
explains why the test surface did not catch it.

### 8a. Decision set review

Step 8a, per decision 4. Read as one set: D1-D9 plus the three
"Carried into phase N" sections of `docs/plans/PLAN-api-input-validation.md`,
and D10-D50 across the seven phase plans. Then grepped the tree for
statements reasoning from the superseded half of each pair, searching
for the *claim* rather than for the decision number.

Nineteen findings. Five were graded blocking here; after collection
findings 1 and 2 are merged into **B-1**, findings 3 and 4 into
**B-2**, and finding 14 is corrected and carried as **B-3**, so nothing
in this section stands as a blocking finding of its own. Four of the
five were in shipped documentation
(`docs/operator_guide/logging.md`,
`docs/developer_guide/writing_an_endpoint.md`) rather than in plan
files, which is the part no phase's own review would have covered —
and 2d then found two more sites in code and configuration that this
step's grep did not reach.

---

#### The decision pairs

**P1. D17 → D32/D37: `missing-required` went from exempt to enforced.**
Phase 4's D17 (`...phase-04-enforce.md:200`) kept `missing-required`
out of the enforcement decision; phase 6's D32/D37
(`...phase-06-required.md:742,804`) deleted the filter. Shipped state
is `base.py:2236-2246`: *"Every reason code is enforceable,
missing-required included"*. Two shipped documents still describe the
D17 world (findings 1, 2). Phase 6 corrected the release note — its
own Progress notes doing so at `...phase-06-required.md:996-997` — and
one half of `writing_an_endpoint.md`, and missed the other half of the
same file and the whole of the operator guide.

**P2. D25 → D34 → D42: three statements of the `warn`/`off` contract,
written three phases apart, that do not agree.**
D25 (phase 5, `...phase-05-narrow.md:453`) decides in as many words
that the rollback *does not* restore prior behaviour: an undeclared
body key becomes a 500. D34 (phase 6, `...phase-06-required.md:762`)
then reasserts the unqualified form — *"`warn` and `off` are the
operator's rollback and hand the handler whatever the caller sent"* —
and D42 (phase 7, `...phase-07-structured.md:741-746`) goes further:
*"an operator who turns validation off ... must get the behaviour they
had before it"*. But phase 7's three handler guards refuse input that
previously answered 200, in every mode. So by the end of the plan the
rollback fails to restore prior behaviour in **two** directions: it
makes some refused requests worse (D25) and it keeps refusing some
requests that used to work (D42). No single document states both. The
release note is the only place that gets both halves right
(`v07-v08.md:240-255` and `:419-431`). Findings 3, 4, 5, 6.

**P3. D42's handler-guard count: two → three, mid-review, 2026-09-17.**
The phase 7 amendment (`...phase-07-structured.md:36-47`) records the
videospec `model`/`memory` presence tests becoming value tests. The
review corrected six places. Findings 7 through 11 are five more,
including the one the brief predicted ("check for a seventh"):
`...phase-07-structured.md:1360`, *"Two handler guards were added"*.

**P4. D45's "asks for nothing" spellings: four → six.**
The same 2026-09-17 amendment adds a sixth spelling
(`base: "none"`, `...phase-07-structured.md:55-59`). Two counts inside
the phase 7 plan were left at the old number (findings 10, 11), and
`test_nested_sweep.py`'s docstring is internally inconsistent about
whose guards produce which rows (finding 9).

**P5. D46 rewritten mid-phase-7: `strict=True` → "refuses a fractional
number".** Phase 7 believed it had found all dependents (D49, D50,
DoD 5/7/8, the `base.py` `video.memory` comment, a
`test_validation_compiler.py` docstring). It had found all the *prose*
dependents. It missed two of its own step briefs (findings 12, 13) and
nothing outside phase 7. **The good news: the search outside phase 7
came back clean** — see finding 19.

**P6. D49's "ordering obligation" retraction.** Clean everywhere except
the step 6 brief (finding 13). Confirmed: `base.py:615-640`,
`test_validation_compiler.py:1306-1323` and `v07-v08.md:409-420` all
carry the corrected form, and no document names a client version as a
release requirement.

**P7. D50's narrowings: two → three.** Corrected in D50 itself and in
the master plan's phase 7 row; the step 6 brief still says two
(finding 13). D50's own third item then contradicts the release note
about rollback (finding 8).

**P8. D44's mechanism was restated: "required string in the schema" →
"`required` in an object fragment means present and not null".**
D44's body was corrected in place mid-phase-7 to say precisely what
`required` buys. The master plan's account of how #4223's reachable
path is closed was left in the future tense, alongside a present-tense
claim about `_netdesc_safety_checks` that phase 7 made false
(finding 14, carried as **B-3**). Note the correction recorded in the
collection preamble: the restated mechanism *works* —
`validation.py:534` gives a required field `allow_none=False` and
`base.py:516` marks `network_uuid` required — so the pair is a
precision change, not a disproof.

**P9. D6's mechanism: `location=('json','query')` → the `json_or_query`
custom loader.** The tuple location was tried and rejected before
phase 3 started (a tuple key is not JSON-serialisable, so the 422
becomes a 500); phase 3's inherited-constraints section says *"Phase 3
must not re-derive this"* (`...phase-03-compile-and-warn.md:47-55`).
The master plan's D6 is mechanism-free and therefore fine; phase 0's
D6 — which the master plan points at for "the reasoning" — still states
the rejected mechanism (finding 15). The derivation axis table carried
into phase 3 in the master plan also predates the loader (finding 17).

**P10. D2's "fold the four `get_args` schemas" → D19's "read literally
that is a bug, deferred as #4098".** The master plan's phase 4 row was
corrected; phase 0's hand-off list was not (finding 16).

**P11. D8's supporting evidence (`passed_uuid`) → D11 killed it.**
The master plan carries an explicit *"Amended by phase 3"* note on D8
(`PLAN-api-input-validation.md:287-294`) — this is the one pair that
was handled properly, and it is the model the others should have
followed. Phase 0's copy of the reasoning was not amended
(finding 15).

**P12. D5's warn-window exit criterion vs D35's "no warn window".**
Checked and clean — finding 18.

---

#### The nineteen findings

**1 and 2. The D17 residue in `logging.md` and
`writing_an_endpoint.md` — merged into B-1.**
`docs/operator_guide/logging.md:181-182` (*"A parameter declared
required but not supplied is recorded and never enforced"*),
`logging.md:175-176` and
`docs/developer_guide/writing_an_endpoint.md:351-352` (both *"a finding
other than `missing-required`"*) are all false since phase 6 (#4199,
2026-09-15); `base.py:2236` enforces every reason code.
`writing_an_endpoint.md` contradicts itself, since `:410` of the same
file says required *is* enforced. Both documents were written in phase
4 and never revisited. Graded blocking here, and carried as **B-1**
with the two further sites 2d measured (`config.py:291-293` and
`instance.py:654-656`).

**3 and 4. `warn` is documented as restoring pre-0.8 behaviour —
merged into B-2.**
`docs/operator_guide/logging.md:186-188` (*"so the request is answered
exactly as it always was"*) and
`docs/developer_guide/writing_an_endpoint.md:369-371` (*"requests that
were working kept working"*) are each false in two independent ways: D25
made an undeclared body key a recorded 500 under `warn`, and D42's three
phase 7 handler guards refuse at `warn` and `off` requests that
previously answered 200 or 507. Graded blocking here, and carried as
**B-2**, where 2d's S4 arrived at the same place from the sweep's ten
`moves at warn` rows.

**5. `shakenfist/tests/external_api/test_request_validation.py:107-111`
— advisory.**
> "The five metadata delete handlers are the one thing the rollback
> does not undo at all: they no longer accept the `value` kwarg they
> used to ignore, in any mode."

Written 2026-09-10 (phase 6 era); "the one thing" was true then and is
not now — phase 7's three handler guards are three more. Should say
"one of the things", with a pointer to `test_nested_sweep.py`'s
`NestedSweepWarnTestCase`. Advisory: a docstring, no behaviour at
stake, but it is the same sentence-level claim as finding 4.

**6. `docs/developer_guide/writing_an_endpoint.md`, the "What
validation does with them" section — advisory.**
The section (lines 347-413) documents the rollback contract, the
`TypeError`-becomes-500 consequence and the 404→400 consequence, and
never mentions the phase 7 handler guards at all (`grep -n "guard"`
over the file returns nothing in this section). Not a stale sentence
so much as a missing paragraph, recorded here because findings 3 and 4
both fall out of the same gap and a fix that only edits the two
sentences leaves the section still incomplete.

**7. `docs/plans/PLAN-api-input-validation-phase-07-structured.md:736-739`
— advisory.**
> "D42. Every existing handler guard stays. Not one of the checks in
> `_netdesc_safety_checks`, the disk-bus check, the IDE refusal or the
> videospec presence checks is deleted"

The videospec checks are not presence checks any more — the review that
moved D42's count to three replaced them with value tests
(`external_api/instance.py:902,922-923`). D42's own text is the one
place the review did not update. Should read "the videospec `model` and
`memory` value tests". Advisory (the decision's substance is
unaffected), but it is D42's canonical text.

**8. `...phase-07-structured.md:943-945` and `:158-160` — advisory.**
> ":944" — "Because it is a handler guard rather than a schema check it
> holds at `warn` and `off` too, which makes it **the only narrowing in
> this phase an operator cannot roll back**."
> ":159" — "it is the one narrowing in this phase an operator cannot
> roll back."

Both were written 2026-09-16, the day before the review added the third
handler guard. The release note gets the final state right:
*"Three new handler guards are deliberately not rolled back"*
(`v07-v08.md:419-421`), and so does the master plan's phase 7 row. The
null `network_uuid` and null videospec refusals are narrowings by this
plan's own working definition (they refuse input previously accepted),
and they are equally un-rollbackable. Defensible only if "narrowing" is
read as "one of D50's three", which no reader will do unprompted.
Should say "the only one of these three narrowings that is a handler
guard, and so one of three refusals in this phase an operator cannot
roll back". Advisory: the operator-facing text is correct, so nobody
acts wrongly on it; graded up from trivia because it is the literal
shape of defect this step exists to find — a claim invalidated one day
later by a decision change, in a paragraph nobody re-read.

**9. `shakenfist/tests/external_api/test_nested_sweep.py:49-53` —
advisory.**
> "Ten rows deliberately answer 400 at `warn` where they used to
> answer 507 or 200, and they are the phase's **two** new *handler*
> guards rather than schema checks -- D42 keeps every existing guard,
> and step 4 added **two** more"

Internally contradictory as it stands: the ten rows include
`video.model.null` and `video.memory.null`, which come from the third
guard described in the *next* paragraph (`:61-65`). The count was
updated to ten in review and the "two guards" clause was not. Should
read "three new handler guards" and fold the following paragraph in.
Advisory.

**10. `...phase-07-structured.md:1211-1213` (definition-of-done audit
item 9) — advisory.**
> "`disk.empty` answers `400 disk specification must specify at least
> one of size or base` at all three modes, as do **the other four
> spellings** listed under item 7."

Item 7 (`:1186-1191`) lists six diskspec spellings, so "the other four"
should be "the other five". Off by one since the sixth spelling
(`base: "none"`) was added in review. Advisory, but it is a
verdict line in the definition-of-done audit, where a wrong count reads
as a wrong verdict.

**11. `...phase-07-structured.md:1465-1471` (Progress, "what the steps
found" item 13) — advisory.**
> "A third class of `warn`-mover. The plan expected two rows not to
> roll back and there are **seven, in three classes**: the null
> `network_uuid` on both routes, **the four spellings** of a diskspec
> which asks for nothing, and one nobody had seen..."

Two stale counts and a stale class list. The final state is ten rows in
three classes (2 + 6 + 2), which definition-of-done item 7 states
correctly at `:1182-1193`. This item was written after step 5 and not
revisited after the review that added the videospec guard and the sixth
spelling. Advisory.

**12. `...phase-07-structured.md:957` (step 2 brief) — advisory.**
> "Apply D46 in the same step: `fields.Integer(strict=True)` wherever
> the rendered type is `integer`."

This is the instruction D46's rewrite exists to retract — applying it
literally broke `test_blob_data_bounds` within a minute
(`:97-105`, `:1411-1413`). The phase convention, stated at `:33-34`, is
that such changes are *"made in place below rather than only recorded
here"*; the step briefs were not included in that sweep. Should say
"a field subclass bound into `_SCALARS` which refuses a fractional
number". Advisory — the step has run — but a future phase copying this
brief as a pattern would reintroduce the defect.

**13. `...phase-07-structured.md:961` (step 6 brief) — advisory, and
this is the predicted seventh place for both D49 and D50.**
> "A release note ... names **the two narrowings of D50**, names **the
> client version D49 requires**, and names `API_VALIDATION_MODE=warn`
> as the rollback."

Three wrong things in one clause. D50 has three narrowings, not two
(`:922-945`). D49 requires no client version — that is the exact
retraction at `:894-896` and `:906-915`, and `client-python#398` is
"a tidy-up rather than a release gate". And the rollback is `warn`
*and* `off`. What shipped is right (`v07-v08.md:409-421` names three
narrowings, names #398 as "not required for this release", and names
both modes), so this is the brief and not the deliverable. Advisory.

**14. `docs/plans/PLAN-api-input-validation.md:376-383` — blocking,
and half of what this step originally claimed here is withdrawn.
Carried as B-3.**
> "The only path a caller can reach is the netdesc's `network_uuid`,
> which `_netdesc_safety_checks` **tests for presence rather than for a
> value** ... Phase 7 leaves it alone on purpose — its networkspec
> schema **will make `network_uuid` a required string, which closes the
> reachable path**"

**The half that holds.** `_netdesc_safety_checks` tests the *value*
now, not presence — `external_api/instance.py:366` is
`if netdesc.get('network_uuid') is None:`, added by phase 7 precisely
because presence was not enough. The sentence was true when written and
is false now, and the second clause is still in the future tense about
work that has shipped.

**The half that is withdrawn.** This step also argued that "a required
string closes the reachable path" credits D44's *disproven* mechanism,
on the grounds that every compiled field is `allow_none=True`. That is
not what the code does, and the reading was a misattributed comment:
the `allow_none=True` sentence is a comment at `validation.py:1110-1111`
explaining why `validate_request` pre-treats an explicit null as
missing. The field builder itself is `validation.py:534`,
`{'required': required, 'allow_none': not required}`, so a required
field gets `allow_none=False` and does refuse an explicit null — with
`error_messages['null']` remapped to the `required` message at
`:536-538` so the two spellings report identically — and `base.py:516`
marks `network_uuid` required in `NETWORKSPEC_SCHEMA`. At `enforce` the
schema does close the reachable path.

So the paragraph should say the guard tests the value, name the
**two-part** closure (the handler guard covers `warn` and `off`, the
schema covers `enforce`), and move to the past tense. Still blocking,
but on the narrower ground that this is the plan's own record of a live
defect and the input to 8g's #4223 reopen comment — 8g must reopen on
the true ground that the *lookup function* is still wrong for any
caller reaching it by another path, and must not argue that the schema
fails to close the reachable path.

**15. `docs/plans/PLAN-api-input-validation-phase-00-decisions.md:251-257`
(D6) and `:270-275` (D8) — advisory.**
> D6: "**Accept `location=('json', 'query')` for parameters declared
> `query`**, keeping the JSON body authoritative."
> D8: "`log_request` already special-cases a body `uuid` to
> `passed_uuid` (`base.py:593-594`) specifically to dodge one instance
> of this collision — evidence that it is a known hazard rather than a
> feature."

Phase 0's file opens by saying *"The resolved form of each is on the
master plan under 'Decisions'; the reasoning is here"* (`:177-179`), so
this is where a reader goes for the *why*. D6's tuple mechanism was
tried and rejected before phase 3 began and turns a 422 into a 500
(`...phase-03-compile-and-warn.md:51-55`). D8's evidence was killed by
D11, the remap was deleted, and `base.py:593-594` no longer contains it
(the surviving note is at `base.py:1740-1741`). The master plan amended
D8 in place and left phase 0 alone; D6 got no amendment anywhere.
Should carry the same *"Amended by phase 3"* note the master plan's D8
carries. Advisory — the decisions' substance survives in both cases,
only their mechanism and evidence died.

**16. `...phase-00-decisions.md:332-333` — advisory.**
> "Four hand-authored `get_args` schemas to fold into the compiled path
> so they stop being a second source of truth."

Superseded by D19 (`...phase-04-enforce.md:217-248`): read literally
this is a bug, not a refactor — deleting the `@use_kwargs` decorators
would silently revert `offset`/`limit` on `GET /blobs/<uuid>/data` to
their signature defaults. Deferred as #4098. The master plan's phase 4
row was corrected; this hand-off list was not. Advisory.

**17. `docs/plans/PLAN-api-input-validation.md:615` — advisory.**
The derivation-generator axis table's webargs row reads
`none, get_args on the class, on the module, inline dict,
location='json'`. The phase 3 copy of the same table
(`...phase-03-compile-and-warn.md:237`) adds `location='json_or_query'`,
and the shipped generator has it — `test_derivation_generator.py:101-103`
carries a `schema bound at json_or_query` case and asserts 225 cases at
`:222`. Consequence of P9: the master plan's copy is the
pre-`json_or_query` version. `writing_an_endpoint.md:336-346` tells a
developer *"If you add a way for a parameter to arrive, add an axis
value here"*, so an incomplete published axis list is a small trap.
Advisory.

**18. Confirmed clean: D5 vs D35, the warn-window exit criterion.**
D5 (`...phase-00-decisions.md:234-247`) sets warn-only's exit criterion;
D35 (`...phase-06-required.md:772-794`) declines a warn window for
required-ness. D35 names itself the departure, states the case for and
against, and gives the evidence (26 days, 20 findings, 73 of 75
declarations untouched by traffic). Nothing anywhere claims every
tightening in this plan was traffic-measured before being turned on:
`grep -rn "warn window\|every tightening"` over `docs/` returns only
D35's own framing of the argument and phase 4's uses of it. One line,
as asked: clean.

**19. Confirmed clean: D46's dependents outside phase 7.**
This was the brief's main open question, and the answer is that phase 7
caught everything outside itself. Searched
`strict=True`, `numeric string`, `fractional`, `Not a valid integer`,
`client-python#398` and `ordering obligation` across `docs/`,
`shakenfist/` and the test tree. `base.py:615-640` carries the
corrected `video.memory` comment; `validation.py:113-142` and the
`_ExactInteger` docstring state the rewritten rule;
`test_validation_compiler.py:906-954,1306-1323` and
`test_request_validation.py:1406-1412` all pin the width the rewrite
chose; `v07-v08.md:409-420` names the numeric-string acceptances as
"things which look like narrowings and are not";
`api_reference/instances.md:179` says only that a fractional `memory`
is refused, which is exactly right. The two live D46 residues
(findings 12, 13) are both *inside* the phase 7 plan's step briefs — a
section phase 7's own D46 sweep did not treat as prose. So the phase's
recorded lesson holds with one amendment: it greps its decisions and
its deliverables, and not its own step table.

**Also confirmed clean, one line each.** D14's shipped message matches
its text (`not declared by this endpoint`, `validation.py:1103`). D18's
`body-path-collision` is documented as enforced (`v07-v08.md:219-223`).
D31's bare `server error` has no re-introduced interpreter text in the
docs. D15's `any` token is still applied to the fourteen metadata
`value` declarations and nothing else. D41's conditionality was
discharged by the census rather than left hanging
(`...phase-07-structured.md:125-126`). D7 (response validation out of
scope) is restated correctly in phase 7's Scope. D11/D12's account of
`log_request` running before index 0 is still accurate. And
`AGENTS.md`, `ARCHITECTURE.md` and `CLAUDE.md` carry no
validation-mode or required-ness claims at all, so none of P1 or P2
reaches them.

---

#### One cross-cutting observation

Five of the nineteen findings (1, 2, 3, 4, 6) are in the two shipped
prose documents that describe the validation contract, and all five
came from the same two pairs (P1, P2) — decisions changed in phase 6
and phase 7 whose shipped documentation was written in phase 4 and
phase 5. The release note is right in every case because each phase
appended to it chronologically; the two reference documents are wrong
because each phase edited the paragraph it was thinking about. That is
a structural difference worth naming in the audit's result: an
append-only document survived seven phases of decision churn and two
rewritten-in-place documents did not.

2d then extended the observation past prose: `config.py:291-293` is
the same stale claim in a *rendered configuration reference*, which no
documentation grep would have found, and `instance.py:654-656` is the
same claim in a code comment. So the pattern is not "two reference
documents rotted" but "every in-place statement of the contract rotted
except the one that was append-only".

Note on overlap with the survey: the release note's `float` spelling
list and the `usage.md` ansible-conversion claim (F6.2, F6.3) were
assigned to 2c and are fixed there, not repeated here.

### 8b. Wave 1

Worktree: `/srv/kasm_profiles/mikal/vscode/src/shakenfist/shakenfist-wt-aiv-08`, branch
`api-input-validation-phase-08-push-audit`, clean throughout this step
(`git status --short` empty before and after).

**Note on worktree sharing, resolved at collection.** This step saw a
scoped `tox -e py3 -- shakenfist.tests.external_api` running in the same
worktree that it did not start, and flagged it rather than assuming it was
benign. It was a read-only test run started by another step;
`git status --short` stayed clean throughout and nothing collided. Recorded
because decision 6's hazard is real and the instinct to flag it was right,
not because anything went wrong.

#### 1. `pre-commit run --all-files` and `tox`

Both were run to completion (not asserted) against the tree as it stands, per
decision 2. Full output saved at
`scratchpad/audit/precommit.log` and `scratchpad/audit/tox.log`.

**`pre-commit run --all-files`** — all eleven hooks passed, exit 0:

```
Lint GitHub Actions workflows............................................Passed
skillsaw.................................................................Passed
Lint Ansible playbooks...................................................Passed
Style check with flake8..................................................Passed
Run unit tests...........................................................Passed
Check from_db_by_ref namespace scoping...................................Passed
Check endpoints authenticate by default..................................Passed
Check API parameter locations are derivable..............................Passed
Check documentation links and anchors resolve............................Passed
Check plan statuses and index arithmetic agree...........................Passed
Type check with mypy.....................................................Passed
```

**`tox`** — all three environments passed, exit 0:

```
  py3: OK (165.05=setup[9.71]+cmd[0.40,152.97,1.97] seconds)
  flake8: OK (13.87=setup[13.84]+cmd[0.03] seconds)
  cover: OK (159.32=setup[36.74]+cmd[0.07,114.29,0.33,5.75,2.13] seconds)
  congratulations :) (338.32 seconds)
```

Coverage from the `cover` env (repo-wide, not scoped to this plan's files):
39211 statements, 15094 missed, 9480 branches, 946 partial, 61% overall.

No failures to check against the 77-file list — decision 2's fallback path
was not needed.

#### 2. The 77-file list

Reproduced exactly as specified:

```
for s in 25e03b764 ad759f25e 1e78fd1be e9b28a65a 3790aa487 0c7eacf48 6274cd924 \
         1c203b111 f1040a23b b3de0a44f 81aa9a7d0 91312b9a3; do
    git diff --name-only "$s^1..$s"
done | sort -u
```

77 files, matching decision 1's count exactly. `git diff <range> --stat` for
each of the twelve ranges also reproduced decision 1's file counts and
+/- exactly (checked all twelve; e.g. `25e03b764`: 31 files, 4374/149;
`91312b9a3`: 22 files, 6001/106) — the range table is correct as recorded.

#### 3. Wave 1 style greps, per range

Ran all four greps (`over-120-char`, `print(`, `etcd`, untagged
`mariadb.get_all_*`) against each of the twelve `<sha>^1..<sha>` ranges,
restricted to `*.py`, exactly as PUSH-AUDIT.md specifies.

| Range | Phase | over-120 | `print(` | `etcd` | `get_all_*` (untagged) |
|---|---|---|---|---|---|
| `25e03b764` | 0+1 #3620 | 0 | 4 (see below) | 0 | 0 |
| `ad759f25e` | 2 #3666 | 0 | 0 | 0 | 0 |
| `1e78fd1be` | 2 #3682 | 0 | 0 | 0 | 0 |
| `e9b28a65a` | 2 #3685 | 0 | 0 | 0 | 0 |
| `3790aa487` | 3 #3726 | 0 | 0 | 0 | 0 |
| `0c7eacf48` | 3 #3742 | 0 | 0 | 0 | 0 |
| `6274cd924` | 3 #3835 | 0 | 0 | 0 | 0 |
| `1c203b111` | 4 #4101 | 0 | 0 | 0 | 0 |
| `f1040a23b` | 4 #4141 | 0 | 0 | 0 | 0 |
| `b3de0a44f` | 5 #4162 | 0 | 0 | 0 | 0 |
| `81aa9a7d0` | 6 #4199 | 0 | 0 | 0 | 0 |
| `91312b9a3` | 7 #4232 | 0 | 0 | 0 | 0 |

The four `print(` hits are all in `tools/fix-api-parameter-locations.py`
(new in `25e03b764`), a CLI script reporting problems to a human on stdout —
legitimate use, not a stray debug print in library/handler code:

```
+            print('  %s' % problem)
+            print('  %-38s %-18s %-6s -> %s'
+        print('  %-38s %-18s %-6s -> not derivable, left alone'
+    print('\n%d location(s) %s'
```

**Both predictions confirmed rather than assumed, one line each:**

* `etcd`: zero hits across all twelve ranges — confirmed by running the grep
  per range above, not assumed.
* `mariadb.get_all_*` (SQL pushdown): zero hits across all twelve ranges,
  and independently confirmed at the file level — `git diff <range> | grep
  mariadb.py` matched nothing for any of the twelve ranges; this plan's
  diffs never touch `shakenfist/mariadb.py`, `shakenfist/protos/database.proto`,
  or `shakenfist/daemons/database/main.py` at all, so there is no database
  layer surface here for the pushdown rule to apply to.

**Mermaid / markdown.** The diff touches 17 markdown files (`AGENTS.md`,
`CLAUDE.md`, 3 `docs/developer_guide|operator_guide` pages, 9 phase plans,
`docs/plans/index.md`, the release note, and `usage.md`). Ran
`tools/mermaid-lint.sh` against exactly those 17 files (docker available,
`docker info` succeeded):

```
No markdown files contain mermaid diagrams; nothing to lint.
```

Clean, and empirically confirmed rather than skipped on the assumption that
a validation/documentation plan wouldn't touch diagrams.

**Proto freshness.** Skipped per the brief. `grep -c '^protos/'` over the
77-file list is 0 — no `.proto` files are touched by any of the twelve
ranges, so `tox -e genprotos` + `git diff --exit-code shakenfist/protos`
has nothing to check.

#### 4. Style conformance — judgment portion

Worked the brief's six bullets against all twelve ranges (`git diff
<range>`, `*.py` only unless noted):

* **Import ordering / logging / quotes.** Sampled every newly-added
  production module (`shakenfist/external_api/declarations.py`,
  `shakenfist/external_api/validation.py`) and the new functional-CI test
  (`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_api_validation.py`):
  stdlib imports alphabetised, then third-party, then `shakenfist`, matching
  CLAUDE.md's example exactly. No `logging.getLogger` direct use anywhere in
  the twelve ranges (grep, 0 hits) — the plan doesn't add any new logger
  setup at all, existing `LOG, _ = logs.setup(__name__)` call sites are
  untouched. A heuristic grep for new double-quoted plain-string literals
  (assignment or `return`, excluding docstrings) found zero hits across all
  twelve ranges.

  One real finding: **triple single-quoted strings (`'''...'''`)**, which
  CLAUDE.md bans outright ("Never use triple single quotes, use triple
  double quotes instead"), appear 90 times across three test files in four
  ranges:
  - `shakenfist/tests/external_api/test_parameter_declarations.py`: 68
    instances in `25e03b764`, 4 more in `e9b28a65a`, 16 more in `1c203b111`.
  - `shakenfist/tests/external_api/test_derivation_generator.py`: 2
    instances in `3790aa487`.

  All of them are `source = '''...'''` (or `self._write('app.py', '''...''')`)
  blocks holding a literal fixture of *Python source code as a string* for
  AST-parsing tests — the fixture's own content uses single-quoted string
  literals (e.g. `'alpha'`), but there's no escaping conflict either way, so
  triple-double would have worked identically. Not caught by `pre-commit` or
  `tox`: flake8 has no quote-style plugin configured in this repo (checked
  `tox.ini` — no `flake8-quotes`), so nothing mechanical enforces the CLAUDE.md
  single/double-quote rule beyond a human or an agent reading the diff. This is
  a style rule violation, not a behavioural defect. **Advisory** — 90
  mechanical find/replace instances across 2 files, no functional risk, but
  worth fixing given how directly it contradicts a written convention and how
  cheap the fix is (a `sed` pass on the two files).

* **Object lifecycle conventions** (`state_targets`, `hard_delete()`, event
  logging). Grepped all twelve ranges for `state_targets|hard_delete|
  add_event\(|EVENT_TYPE_`: one hit, in `f1040a23b`
  (`shakenfist/external_api/base.py`), which is `ns.add_event(EVENT_TYPE_AUDIT,
  message, extra=fields)` in `log_token_use` — correct use of the existing
  event-logging pattern, not a new event type. No `state_targets` or
  `hard_delete()` touches anywhere in the plan's 77 files: this plan doesn't
  touch object lifecycle at all, so this convention doesn't apply here.
  **No finding.**

* **Database access conventions (three-layer pattern).** No range touches
  `shakenfist/mariadb.py`. **N/A**, confirmed by the same file-level check as
  the pushdown grep above.

* **SQL-pushdown discipline.** Covered above under the mechanical greps —
  zero hits, and no database-layer surface exists in this plan's diff at all
  to hide a judgment-level violation the grep might miss. **No finding.**

* **gRPC conventions** (`protos/database.proto`, the Monitor operations list
  in `daemons/database/main.py`, `tox -e genprotos`). **N/A** — 0 proto files
  and 0 `daemons/database/` files in the 77-file list.

* **Field rename / unit-change discipline.** Grepped for new
  `seconds|_ms|milliseconds|_kib|_bytes|_gb|_mb` tokens across all twelve
  ranges, filtering out timeouts/sleeps/tests. All hits are consistently-named
  schema fields already carrying their unit in the name
  (`deadline_seconds`, `expires_in_seconds`, `limit_memory_mb`,
  `limit_disk_gb`) from the namespace-claims validation schema work — no
  evidence of a silent unit change. This plan validates request shapes, it
  doesn't recompute or rename existing fields. **No finding.**

#### Wave 1 result

Wave 1 is clean: `pre-commit run --all-files` (all eleven hooks) and `tox`
(py3, flake8, cover) both pass against the tree as it stands, the mechanical
style greps are empty across all twelve ranges except four legitimate
`print()` calls in a CLI tool script, `tools/mermaid-lint.sh` confirms none
of the 17 touched markdown files carry a mermaid diagram to break, and proto
freshness does not apply because no `.proto` file is in scope. The judgment
portion found one real but advisory style-convention violation (90 triple-
single-quoted string literals across two test fixture files, banned by
CLAUDE.md but not mechanically enforced) and confirmed the database-layer
rules (three-layer pattern, SQL pushdown, gRPC conventions) are all N/A
because this plan never touches `shakenfist/mariadb.py`, `shakenfist/protos/`,
or `shakenfist/daemons/database/`. Nothing here is blocking. **Wave 2 is
worth spending on** — wave 1 passing clean is exactly the gate PUSH-AUDIT.md
sets for it, and this audit's stated purpose (cross-phase decision drift,
which no single phase's own review could see) lives entirely in wave 2's
judgment steps, not in anything wave 1 can check.

### 2a. Code quality

Step 8c: wave 2 mechanical sweep plus 2a code-quality judgment, run over
the twelve ranges of decision 1. Wave 1 already passed (per the brief) and
is not re-run here.

#### Wave 2 mechanical sweep (per range)

Ran, for each `<sha>^1..<sha>`: TODO/FIXME/HACK/XXX grep, new
`# noqa`/`# type: ignore`/`pragma: no cover` grep, `git diff --stat` tail,
new `def test_` count, `docs/*`/`*.md` files touched, and
`subprocess.`/`os.system`/`shell=True` grep.

| # | Range | +/- (files) | TODO/FIXME/HACK/XXX | noqa/type:ignore/pragma | new tests | docs touched | subprocess/shell |
|---|---|---|---|---|---|---|---|
| 1 | `25e03b764` (#3620) | 4374/149 (31) | none | 1: `tools/fix-api-parameter-locations.py:1+3262` `# noqa: E402` | 50 | 9 files | none |
| 2 | `ad759f25e` (#3666) | 545/38 (8) | none | none | 2 | 4 files | none |
| 3 | `1e78fd1be` (#3682) | 234/90 (6) | none | none | 9 | 3 files | none |
| 4 | `e9b28a65a` (#3685) | 1597/111 (28) | none | none | 27 | 7 files | none |
| 5 | `3790aa487` (#3726) | 2947/63 (20) | none | 1: `shakenfist/external_api/base.py:474` `# type: ignore[attr-defined]  # noqa: E501` | 46 | 5 files | none |
| 6 | `0c7eacf48` (#3742) | 63/4 (3) | none | none | 0 (docs-only range) | 3 files | none |
| 7 | `6274cd924` (#3835) | 63/0 (1) | none | none | 0 (docs-only range) | 1 file | none |
| 8 | `1c203b111` (#4101) | 2200/150 (23) | none | none | 18 | 5 files | none |
| 9 | `f1040a23b` (#4141) | 1604/167 (23) | none | none | 32 | 4 files | none |
| 10 | `b3de0a44f` (#4162) | 1762/113 (20) | none | none | 14 | 5 files | none |
| 11 | `81aa9a7d0` (#4199) | 3312/175 (22) | none | none | 45 | 5 files | none |
| 12 | `91312b9a3` (#4232) | 6001/106 (22) | none | none | 72 | 7 files | none |

**Clean, confirmed rather than assumed:**

- No `TODO`/`FIXME`/`HACK`/`XXX` added in any of the twelve ranges.
- No new `pragma: no cover` in any range.
- No new `subprocess.`, `os.system`, or `shell=True` in any range.
- Ranges 6 and 7 (the 63-line ranges the risk section flags as easy to
  under-scrutinise) are pure `docs/plans/*.md` edits with no `.py` files at
  all — zero new tests is the correct reading, not a coverage gap.
- Every range touches at least one documentation file; none trips the "no
  doc files touched" warning.
- Two `# noqa`/`# type: ignore` hits total, both triaged below as
  advisory-clean (justified, not blocking).

**Triage of the two noqa/type:ignore hits:**

- `tools/fix-api-parameter-locations.py:3262` (range 1) — `# noqa: E402`
  on `from shakenfist.external_api import declarations` after a
  `sys.path.insert(0, ...)` shim. Standard pattern for a script that must
  mutate `sys.path` before importing the package it lives outside of; not
  production API code. **Advisory-clean, no action.**
- `shakenfist/external_api/base.py:474` (range 5) —
  `# type: ignore[attr-defined]  # noqa: E501` on
  `wrapper.__self__ = getattr(func, '__self__', None)`. Carries an
  eight-line comment (`base.py:466-473`) explaining exactly why the
  attribute copy is required (`functools.wraps` does not carry a bound
  method's `__self__`, and both `_sf_public`/`_sf_scope` markers and
  flasgger's specs depend on it surviving). This is the "hard-won bug
  explanation" the comment-proportion shared block exempts.
  **Advisory-clean, no action.**

No new `mariadb.get_all_*(` call sites, no `*_attributes.py` schema files,
and no `mariadb.py` touched in any of the twelve ranges (checked directly
against each range's changed-file list, not only by the wave-1 `etcd`/
pushdown greps) — **SQL-pushdown and cached-FK-list checks are confirmed
not applicable to this plan's diff**, consistent with decision 1's note
that the plan does not touch the database layer.

Three-layer MariaDB pattern: not applicable, no new `mariadb.py` functions
in any range. **Confirmed clean.**

---

#### (a) `validation.py`'s `_field()` — one function or four sharing a name?

`_field()` (`shakenfist/external_api/validation.py:476-726`, 251 lines)
was built incrementally: the scalar/bounds/pattern/enum/format logic in
phase 3 (range 5, `3790aa487`), the `array` branch tightened alongside it,
the semantic-format lookup in phase 6 (range 11, `81aa9a7d0`), and the
`properties`-keyed object/nested branch and the `any`-sentinel branch in
phase 7 (range 12, `91312b9a3`; confirmed against each range's diff).

**Verdict: one function with a shared preamble, dispatching on shape —
but the accretion shows.** The preamble (`required`/`allow_none`/
`error_messages` at `validation.py:533-537`, decision D44) is genuinely
shared: every shape honours nullability the same way, including the two
recursive calls (`array`'s `_field(spec.get('items', {}))` at line 612,
and the nested-schema dict comprehension at lines 639-641 for `object`).
That is real reuse, not a coincidence of a shared name.

The `validators` list construction (`minimum`/`maximum`/`pattern`/`enum`/
semantic-`format`, lines 539-607) is where the seams show. It is built
unconditionally before the shape dispatch, but it is only ever *consumed*
by the scalar leaf branch (line 726's `field_class(**kwargs)`) and by the
`array` branch's outer `fields.List(...)` call. Three of the four
branches — `object` (line 670: `kwargs.pop('validate', None)`), the `any`
sentinel (line 708: same), and the unrecognised-type fallback (line 724:
same) — each explicitly discard it, each with its own comment explaining
why (lines 660-669, 701-707, 716-720 respectively). The `array` branch at
line 609-612 is the odd one out: it does **not** pop `'validate'` before
constructing `fields.List(_field(...), **kwargs)`, so if `validators` were
ever non-empty when `declared == 'array'`, that validator would apply to
the *whole list value*, not to each item — a different semantic from
every other branch's careful "this cannot apply here" stance.

In practice this is unreachable today: `_validated_constraints()`
(`shakenfist/external_api/base.py:761-820`) refuses `minimum`/`maximum`/
`pattern` on anything but a numeric or string rendered type, and `enum`
is never set on an array-typed fragment by any of the three hand-authored
structured schemas (`DISKSPEC_SCHEMA`, `NETWORKSPEC_SCHEMA`,
`VIDEOSPEC_SCHEMA` — checked directly, all three `enum` values sit on
`type: string` leaf properties). So this is **advisory**, not blocking:
the shipped behaviour is correct because nothing can currently populate
`validators` on an array-typed spec, but the array branch is the one
place in `_field()` that relies on that invariant holding *implicitly*
rather than defending it the way its three siblings do — exactly the
foot-gun the surrounding comments elsewhere are careful to guard against
("this keeps that true if a future token renders one itself",
`validation.py:667-669` and `705-707`). A future array-of-scalar token
that wanted a whole-array constraint would silently get one applied in a
way nothing else in this module does, with no comment marking the
decision as deliberate.
**Advisory** — `shakenfist/external_api/validation.py:609-612`: add the
same defensive `kwargs.pop('validate', None)` (or a one-line comment
saying why not) for symmetry with the other three shape branches.

Comment-proportion check on `_field()`: several individual blocks exceed
the shared block's fifteen-line-comment/ten-line-body candidate
threshold (e.g. lines 500-537, ~33 comment lines over a 5-line body;
lines 616-658, ~38 comment lines over ~7 lines of object-branch code).
Each of these is a "hard-won bug explanation" or cites a specific
decision number and a specific defect (F7, N1, D33/D43/D44/D47) rather
than restating the code, which the shared block treats as justifying the
length. **Advisory-clean by inspection** — no restatement-only comment
block found in `_field()`; not a finding in its own right, recorded so a
later pass does not re-flag it purely on line count.

#### (b) `base.py`'s three additions to the type vocabulary

`ARGTYPES` (`shakenfist/external_api/base.py:669-758`) and the three
structured-schema constants (`DISKSPEC_SCHEMA:423`, `NETWORKSPEC_SCHEMA:505`,
`VIDEOSPEC_SCHEMA:594`) were extended in phase 2 (base vocabulary, range 2
`ad759f25e`), phase 6 (bounds/format-as-validator wiring, range 11
`81aa9a7d0`), and phase 7 (the three `properties`-bearing structured
tokens, range 12 `91312b9a3`) — confirmed against each range's diff.

**Verdict: one coherent vocabulary, not three conventions sharing a
dict.** Concretely:

- **Naming** is uniform across all three additions: single lower-case
  words, `arrayof<name>` for the array form of a structured or scalar
  token (`arrayofdict`, `arrayofdiskspec`, `arrayofnetworkspec`,
  `arrayofstring`), no phase introduces a second naming scheme.
- **How a constraint is expressed** is consistently one of two paths, and
  the code enforces they cannot collide: a token can bake an intrinsic
  constraint into its own rendering (`unsignedinteger`'s `'minimum': 0` at
  `base.py:749-750`, `macaddr`'s `'pattern'` at `base.py:726-728`), or a
  declaration can narrow further via the per-call `constraints` dict
  (checked by `_validated_constraints`, `base.py:761-820`), and
  `_validated_constraints` explicitly refuses a `constraints` key that
  duplicates one the type already renders (`base.py:783-787`) — so the
  two mechanisms are structurally prevented from disagreeing rather than
  merely conventionally kept apart.
- **How a semantic `format` relates to a validator** is one rule with one
  documented, deliberate exception, not three ad hoc arrangements: five
  format strings are keyed in `validation._FORMATS`
  (`validation.py:427-433`) and get a real validator; every other format
  string is prose only. `macaddr` is the sole case that uses `pattern`
  instead of a `_FORMATS` entry, and this is explained in two places that
  agree with each other: `validation.py:423-426` ("a second check would
  be a second definition of the same format") and
  `base.py:538-543` (reusing `util_network.MACADDR_PATTERN` "so the
  published contract and the check cannot drift (PR #4183)"). This is
  exactly the question the brief asks after — whether the vocabulary
  additions were reconciled or left to drift — and the answer is that
  phase 6 and the out-of-band #4183 merge were reconciled deliberately,
  in-place, with cross-references. **Confirmed clean**, not a finding.
- `DISKSPEC_SCHEMA`, `NETWORKSPEC_SCHEMA`, and `VIDEOSPEC_SCHEMA` share
  the same shape: `type: object`, `additionalProperties: False`, and a
  per-property `description`; `NETWORKSPEC_SCHEMA` is the only one with a
  top-level `required` (`['network_uuid']`, `base.py:516`), which matches
  its own guard's behaviour (`_netdesc_safety_checks` refuses a missing
  `network_uuid` too) rather than being an unexplained inconsistency.
  **Confirmed clean.**

No advisory or blocking findings in this area.

#### (c) `shakenfist/external_api/instance.py` request-guard chain

Read as it stands today on `develop` (decision 3), i.e. including
`f8c801ebe`/#4183's MAC-format check inside `_netdesc_safety_checks`,
which is now folded into `NETWORKSPEC_SCHEMA['properties']['macaddress']`
(`base.py:535-548`) and independently re-checked in the handler at
`instance.py:377-381`.

**The chain, in the order it runs inside `InstancesEndpoint.post()`:**
name checks (673-686) → secure-boot/UEFI (688-693) → `placed_on` node
lookup (696-712) → configdrive (714-718) → per-disk loop: dict-shape,
size-or-base (D45, 736-740), bus (743-748), base-URL resolution (750-847)
→ NVRAM template resolution (853-881) → IDE-disallow loop (884-886) →
per-netdesc `_netdesc_safety_checks` loop (888-892) → videospec
defaults/guards (894-937) → metadata (939-946) → side_channels (948-954)
→ `Instance.new()`.

**Ordered by accretion, not by a redesigned single pass.** The order
does not track the parameter declaration order in the `swag_from` list
just above it (`network` is declared before `disk` at
`instance.py:554/558`, but disk is processed first in the body), and each
block's comment cites a different phase's decision number in isolation
(D42 at `instance.py:364`, D44/D45 at `instance.py:734-735` and
`897-901`, D50 referenced from `base.py:487-491`) rather than one comment
describing the guard chain as a whole. This is consistent with four
phases each inserting its block at the point in the function its own
plan needed, which is exactly what the brief's framing predicts. This is
**advisory, not blocking**: nothing here is wrong, and the guard-to-guard
cross references are actually unusually good for code assembled this
way — but a fifth phase adding a new structured parameter has no single
place to look to see "where do guards for a new body key go", and would
likely add a sixth insertion point rather than recognising an existing
pattern to extend.

**The known unreachable guard, confirmed:** the "IDE disks are no longer
supported" loop at `instance.py:884-886` (`if d.get('bus') == 'ide':
return sf_api.error(400, ...)`) can never fire. Every `d` that reaches
line 884 has already passed the per-disk loop's bus check at lines
743-748 (`instance._get_disk_device(disk_bus, 0)`), and
`_get_disk_device`'s own `bases` dict
(`shakenfist/instance.py:92-98`: `sata`, `scsi`, `usb`, `virtio`, `nvme`)
does not include `ide` — so any diskspec naming `ide` already raised
`InstanceBadDiskSpecification` and returned 400 at line 746-748, in every
`API_VALIDATION_MODE` (this check is a handler guard, not schema-gated,
so it is not merely shadowed under `enforce`). This matches
`docs/plans/PLAN-api-input-validation-phase-07-structured.md`'s own
record of the defect. **Advisory** (dead code, not wrong behaviour — the
handler still answers exactly the right 400, just from the earlier
check) — `shakenfist/external_api/instance.py:884-886` is safe to delete.

**Looking for a second unreachable guard:** none found. Checked
specifically:
- `_netdesc_safety_checks`'s `isinstance(netdesc, dict)` check
  (`instance.py:351-353`) is reachable and load-bearing under `warn`/
  `off` — `NETWORKSPEC_SCHEMA`'s `type: object` only refuses a non-dict
  element under `enforce`, and decision D42's rollback contract requires
  the handler guard to hold at every mode, so this is deliberate
  redundancy, not accidental dead code (same shape as the IDE case only
  on the surface; the difference is that this check is reachable in two
  of the three modes, where the IDE check is reachable in none).
- The videospec `model`/`memory`/`vdi` guards (`instance.py:929-937`) are
  not shadowed by the schema at any mode: `VIDEOSPEC_SCHEMA` declares no
  `required` properties (`base.py:594-668` has no top-level `required`
  key), so an `enforce`-mode caller who omits `model` or `memory` is not
  refused by the compiled schema at all — these handler checks are the
  only enforcement at every mode, confirmed load-bearing.
- The hot-plug endpoint (`InstanceInterfacesEndpoint.post`,
  `instance.py:1245-1301`) calls `_netdesc_safety_checks` once and does
  not duplicate any of its checks locally.
- No handler-level guard duplicates `DISKSPEC_SCHEMA`'s `bus`/`type`
  enums the way the IDE loop duplicates the bus check — `type` (`disk`/
  `cdrom`) has no handler-level re-check at all in `external_api/
  instance.py`.

No second instance of the specific "guard whose own sibling handler
check already makes it permanently dead in every mode" pattern was
found.

#### Duplicated code / missed abstractions found outside the three named areas

- **`shakenfist/external_api/validation.py:274-283, 301-309, 328-336,
  370-378, 406-414`** — the five `_format_byte`/`_format_netblock`/
  `_format_ip_address`/`_format_url`/`_format_uuid` functions, all added
  together in phase 6 (range 11, `81aa9a7d0` — confirmed by diff), share
  an identical eight-line skeleton: `if value is None: return value`,
  `if not isinstance(value, str): raise _invalid(...)`, `try: <parse>
  except Exception as e: raise _invalid(...) from e`, `return value`.
  Each function's docstring is individually justified (each documents a
  specific historical defect and a specific set of consumers, which the
  comment-proportion shared block exempts), but the mechanical wrapper
  around each parse call is copy-pasted five times with nothing but the
  parser call and the error string changed. **Advisory**: a small
  `_string_format(parser, what)` helper (or a decorator) would remove
  the duplication and give the next format validator — there will be a
  sixth — a single place to extend rather than an example to copy.
  Not blocking: correctness is unaffected, and
  `shakenfist/tests/external_api/test_format_validation.py` already
  covers all five uniformly.
- **`shakenfist/external_api/validation.py:1110-1111` — advisory, added
  at collection.** The comment explaining why `validate_request`
  pre-treats an explicit null as missing reads *"every compiled field is
  `allow_none=True`"*. That is true of the *optional* fields the comment
  is about and false as a general statement: `validation.py:534` builds
  fields with `{'required': required, 'allow_none': not required}`, so a
  required field gets `allow_none=False` and refuses a null. 8a read
  this sentence as a general claim and drew a wrong conclusion from it
  about D44 (see the collection preamble's correction 1 and finding 14),
  which is direct evidence that the wording misleads a careful reader.
  Should be scoped — "every *optional* compiled field" — or should point
  at `:534`. Not blocking: the code is correct and only the comment is
  loose.
- No other duplicated-logic or missed-abstraction candidates found
  within this plan's diff across the twelve ranges. (Note: the
  artifact-resolution branches in `InstancesEndpoint.post()`
  (`instance.py:755-804`, three near-identical
  `resolve_to_blob()`/404/`d['blob_uuid']` tails) look similar at first
  glance, but that code is untouched by all twelve ranges — checked
  directly, no range's diff touches those lines — so per decision 3 and
  the plan's Scope it is pre-existing code outside this audit's
  footprint, not a finding here.)

#### Summary

No blocking findings in 2a. Three advisory findings worth carrying into
8g's disposition: the `_field()` array-branch `validate`-kwarg asymmetry
(`validation.py:609-612`), the five duplicated `_format_*` wrappers
(`validation.py:274-414`), and the loose `allow_none=True` comment at
`validation.py:1110-1111` that demonstrably misled one of this audit's
own steps. The confirmed-unreachable IDE guard
(`instance.py:884-886`) is already known and recorded in the phase 7
plan; no second unreachable guard was found despite a specific search for
one. `base.py`'s three vocabulary additions are one coherent design, not
three conventions sharing a dict.

### 2b. Test review

Worked across the twelve ranges in decision 1. Everything below is a
result that was run in the worktree rather than read, except where it
says otherwise.

**What was run.** `tools/mutate-nested-sweep.sh` (16 mutations, full
sweep run per mutation); three mutations of my own against guards the
script does not cover, applied and restored by file copy exactly as the
script does and never with `git checkout`;
`.tox/py3/bin/python -m stestr run --no-subunit-trace external_api`
(970 tests, PASSED, id=26); five throwaway probes run from the
scratchpad against the shipped fixtures with `PYTHONPATH` rather than by
adding files to the tree; and an intersection of the existing `cover/`
report against the set of lines `git blame` attributes to the 111
commits in the twelve ranges. `git status` at the end of this step shows
only step 8d's three documentation files modified — no production or
test file in the repository was left changed.

**Test surface per range.** Insertions/deletions under
`shakenfist/tests/`, production Python under `shakenfist/` excluding
tests and deploy, and insertions under
`shakenfist/deploy/shakenfist_ci/`:

| Range | Production | Unit tests | Cluster CI |
|---|---|---|---|
| `25e03b764` ph0+1 | 868/148 | 1352/0 (1 file) | 0 |
| `ad759f25e` ph2 | 21/1 | 148/11 (2) | 0 |
| `1e78fd1be` ph2 | 68/4 | 127/57 (2) | 0 |
| `e9b28a65a` ph2 | 400/79 | 755/9 (7) | 0 |
| `3790aa487` ph3 | 806/18 | 1230/5 (6) | 0 |
| `0c7eacf48` ph3 | 0/0 | 0/0 | 0 |
| `6274cd924` ph3 | 0/0 | 0/0 | 0 |
| `1c203b111` ph4 | 553/87 | 764/32 (4) | 0 |
| `f1040a23b` ph4 | 260/50 | 812/68 (9) | +140 |
| `b3de0a44f` ph5 | 158/40 | 556/51 (7) | +14 |
| `81aa9a7d0` ph6 | 425/47 | 1898/106 (9) | +181 |
| `91312b9a3` ph7 | 742/37 | 2732/35 (8) | +351 |

Ranges `0c7eacf48` (#3742) and `6274cd924` (#3835) are documentation
only — plan bookkeeping in
`docs/plans/PLAN-api-input-validation-phase-03-compile-and-warn.md` and
two index lines. There is no code in them and nothing for this step to
review; they are dispatched here in one line, per the risk note about
uniform effort.

Overall the ratio is 11,374 test insertions to 4,301 production
insertions, 2.6:1. Coverage quantity is not the question, as the brief
says. What follows is about shape.

---

#### The counterfactual: what shape would have caught the two phase 7 defects

Read `git show 975a979d6:shakenfist/tests/external_api/test_nested_sweep.py`
(927 lines, 99 rows) against the merged `91312b9a3` version (1065 lines,
104 rows).

**Both defects had a row over them, and both rows asserted that the
defect was correct.** That is worse than the brief's framing of "merely
pinning the fixed answer", because these rows pinned the *broken*
answer, and each one derived its expectation from the handler's
implementation rather than from the contract:

* `video.model.null`, pre-review (`sweep_pre.py:684-686`): `ACCEPTED,
  ACCEPTED`, note *"width: the handler tests presence, not truth, so a
  null model passes its guard and must pass the schema too"*. The row
  reasons from the guard to the expectation. Every word of it is true
  and the conclusion is the bug.
* `net.float.yes`, pre-review (`sweep_pre.py:481-487`): note *"'yes' is
  in marshmallow's truthy set, and the handler's own test is a bare
  truthiness check, so the schema is exactly as wide as the handler"*.
  The bare truthiness check is written down as the justification for the
  row. There was no row at all for a *falsy* string spelling — the
  asymmetry that the schema and the handler agree on `'yes'` and
  disagree on `'false'` is invisible if you only sweep the spellings
  where they agree.

**The shape that would have caught each is the same shape: an
assertion on the value the handler passes downstream, not on the status
code the request returns.** The sweep's observable is a three-tuple —
`Answer = (status, exception_recorded, message)`, `test_nested_sweep.py:119`
— and that tuple cannot distinguish "the null was defaulted" from "the
null was stored", nor "the interface floated" from "it did not". It is
structurally blind to the entire class *accepted, with the wrong
meaning*. Quantified against the merged table: of 104 rows, 43 answer
`ACCEPTED` in both `enforce` and `warn`; 35 of those are on the create
route, where `ACCEPTED_ON_CREATE = Answer(507, False, 'No nodes
remaining at scheduling stage is_hypervisor')`
(`test_nested_sweep.py:103`) — the request dies at the scheduler, so
those 35 rows prove only that the value got past the schema and the
guards and say nothing whatever about what it meant. The 8 accepted
hotplug rows do reach a real 200 and a real interface, and still observe
only the 200.

**Do the rows added since have the right shape?** Partly, and the part
that has it is hand-written per defect rather than derived.

* The two *table* rows added for the videospec (`video.model.null` and
  `video.memory.null`, `test_nested_sweep.py:729-744`) are status rows.
  They pin the now-fixed 400. They would catch a regression in those two
  values and nothing else.
* The two *tests* added alongside them do have the right shape, and are
  well built. `test_a_null_video_key_is_never_stored`
  (`test_nested_sweep.py:906-938`) wraps `Instance.new` and asserts the
  `video` kwarg the handler passed — the downstream value, not the
  status — and its docstring says why it cannot read the object back.
  `test_a_falsy_float_spelling_does_not_float`
  (`test_nested_sweep.py:940-981`) asserts the *effect* (whether the
  created interface got a floating address) and deliberately asserts
  both spellings, with the reason stated in the docstring: *"a test
  which only showed the falsy one not floating would pass just as well
  against a handler which had stopped floating anything."* That is the
  adversarial-control instinct the rest of this plan's test surface
  shows throughout, and it is right.
* But each is a one-off over one key. Nothing derives the obligation.
  There is no mechanism that says *for every nullable property, an
  explicit null must produce the same downstream value as an omission*,
  or *for every declared boolean, a falsy string spelling must produce
  the same effect as JSON `false`*. So the next member of either class
  is not caught — and the next members exist today. See B1.

Graded: **advisory** on its own terms (a test-shape gap is not shipped
behaviour), but it is the direct cause of B1, which is blocking, and the
two should be fixed together.

---

#### B1 — blocking, carried as B-4. `declared_boolean` closed one site in a class of about ten

`validation.declared_boolean()` (`shakenfist/external_api/validation.py:166-198`)
is the phase 7 review's fix for the `float` defect, and its docstring
states the general rule in as many words:

> This layer is check-only (decision D14): validate() is run for its
> findings and the deserialised result is thrown away, so a handler is
> handed the raw body and has to do its own reading. A bare truthiness
> test is not that reading. […] So the published specification said
> `{"float": "false"}` was a valid boolean meaning False while
> external_api/instance.py floated the interface. **That is worse than
> the unvalidated state it replaced: before this phase the
> specification said nothing about the value, and now it says something
> the server contradicts.**

`validate_request()` confirms the premise for *every* parameter, not
just nested ones — it calls `validation.check()` and discards the
result (`shakenfist/external_api/base.py:2219-2224`), so a handler kwarg
is the raw body value. The API declares 19 `boolean` parameters. The
function is called at exactly one site:
`shakenfist/external_api/instance.py:482`.

Ran the other sites. `probe_bool.py` / `probe_bool2.py`, both against
the shipped `SweepFixtureTestCase` fixture at `enforce`:

```
STATUS uefi=false -> 507
  uefi reached Instance.new as 'false' (str)
  machine_type='pc'
STATUS secure_boot=off -> 507
  secure_boot reached Instance.new as 'off' (str)
  machine_type='q35'

shared=False    -> 200 shared_in_reply=False
shared='false'  -> 200 shared_in_reply=True
shared='off'    -> 200 shared_in_reply=True
```

Confirmed defects, identical in kind to the one phase 7 fixed:

* **`uefi`** (`shakenfist/external_api/instance.py:579` declares it
  `boolean`; read at `:967`). `{"uefi": "false"}` is published as a
  valid boolean meaning False and boots the instance with UEFI.
* **`secure_boot`** (`instance.py:603`, read at `:689` and `:692`).
  `{"secure_boot": "off"}` turns secure boot on and switches the machine
  type to `q35`. Worth noting the second-order effect: the guard at
  `instance.py:689` is `if secure_boot and not uefi:` — with both sent
  as falsy strings, both are truthy, so `secure_boot and not uefi` is
  False and the "secure boot requires UEFI" refusal does not fire
  either, for a caller who asked for neither.
* **`shared`** on `ArtifactsEndpoint.post` (`artifact.py:437`, read at
  `:484`) and on the artifact-upload route (`artifact.py:559`, read at
  `:649`). `{"shared": "false"}` shares the artifact with every
  namespace. **Correction applied at collection:** this step reported it
  as a cross-namespace escalation and that is wrong — the
  `request_namespace() != 'system'` check sits *inside* the `if shared:`
  branch at `artifact.py:484-488`, so an unprivileged caller sending
  `"false"` gets a 403. It is an admin foot-gun: a `system` operator who
  sends `"false"` intending not to share gets the opposite, silently.
  Not a tenant escalation, and must not be filed as one.

Same shape by inspection, not probed: `thin` on the snapshot route
(`snapshot.py:46`, read at `:76`), and `all` on the instance, network,
artifact and agent-operation list routes (`instance.py:529`,
`network.py:211`, `artifact.py:437`, `agentoperation.py:200`), where a
falsy string widens the listing to include deleted objects.

Two sites in the class are **not** affected, and the difference is why
this needs a test rather than a reviewer's eye:
`provide_dhcp`/`provide_nat`/`provide_dns` on network create are coerced
somewhere downstream (probed: `provide_dhcp='false'` stores `False`),
and `confirm` on the three delete-all routes uses `if confirm is not
True:` (`instance.py:1150`, `network.py:351`, `artifact.py:518`), an
identity test that fails safe.

Graded blocking under decision 5 on the `uefi` and `secure_boot`
members, which need no privilege: the published specification says
something the server contradicts. It is recorded here rather than in
2a/2d because the question that found it is the brief's counterfactual
question — *does the added row have a shape that catches the next member
of the class* — and the answer is no, demonstrated by three next
members found in twenty minutes. Collected as **B-4**; 8g should note
the overlap with #4167, whose surviving scope is the same two parameters
reached by a different route (an explicit null rather than a falsy
string), and check whether one issue carries both.

**Recommended fix, and it is a test as much as a code change.** Add a
*derived differential* test: enumerate every parameter declared
`boolean` from `declarations.handlers()` (the same enumeration
`test_required_sweep.required_declarations()` already walks), send each
one the string `'false'` and then JSON `false` using the complete valid
request `test_required_sweep.RECIPES` already carries for every handler,
and require the two to produce the same downstream call or the same
observable effect. It is differential, so it has no hand-written
expected value and therefore cannot pin a wrong answer the way
`net.float.yes` did. Then route every failing site through
`declared_boolean`.

---

#### B2 — advisory. Four handler guards this plan added have a test written to pin them, and in all four the validation layer answers first

This is the same blindness as the counterfactual, found by the
zero-coverage check, and it is the failure mode
`tools/mutate-nested-sweep.sh`'s own header warns about: *"A sweep row
which passes for the wrong reason is worse than a missing row, because
it reads as evidence."*

The `cover/` report says these guard bodies never execute:
`shakenfist/external_api/artifact.py:828-829`,
`shakenfist/external_api/label.py:130-131`,
`shakenfist/external_api/snapshot.py:86-87` (the three
`except InvalidMaxVersions: return sf_api.error(400, ...)` arms), and
`shakenfist/external_api/blob.py:211` and `:213` (the `offset < 0` and
`limit < 0` refusals). All five lines are attributed by `git blame` to
commits inside the twelve ranges.

Each has a test. `test_snapshot_max_versions.py` opens by saying it
exists because *"the value travels through Instance.snapshot() into
Artifact.owned_from_url_or_new() and lands in the max_versions setter,
where a negative persists"*, and
`test_a_negative_max_versions_is_refused` asserts a 400.
`test_blob_data_bounds.py`'s `test_a_negative_offset_is_refused` asserts
a 400 and `assertIn('offset', error)`. Probed which layer answers
(`probe_mv2.py`, `probe_blob.py`):

```
mode=enforce  max_versions=-1    -> 400 'max_versions: Must be greater than or equal to 0.'
mode=warn     max_versions=-1    -> 400 'max version cannot be negative'
mode=off      max_versions=-1    -> 400 'max version cannot be negative'

mode=enforce  offset=-1  -> 400 'offset: Must be greater than or equal to 0.'
mode=warn     offset=-1  -> 400 'offset cannot be negative'
mode=off      offset=-1  -> 400 'offset cannot be negative'
```

The guards are correct — good news, and worth recording as such. But at
`enforce`, which is what these test classes run at, the declared
`minimum: 0` / `unsignedinteger` refuses the value before the handler is
reached, so every assertion in both files is satisfied by the
validation layer. `assertIn('offset', error)` matches
`'offset: Must be greater than or equal to 0.'` just as happily as
`'offset cannot be negative'`. The handler guard — the half that is the
*only* defence under `API_VALIDATION_MODE=warn` or `off`, which is
precisely why phase 7 wrote the videospec checks as guards rather than
schema — has zero executed coverage on four routes.

The fix is the pattern this plan already invented and should simply
apply more widely: give these classes the
`NestedSweepWarnTestCase`/`NestedSweepOffTestCase` treatment
(`test_nested_sweep.py:1029,1054`) so the same assertions run at `warn`
and `off`, and assert the guard's own message so the answering layer is
identified rather than inferred. Two lines per file.

**Cross-reference, reconciled at collection.** 2d's S2 cites
`artifact.validated_max_versions()` as the plan's *worked precedent* for
a destructive-negative guard that holds at every mode, and uses it to
argue that `disk[].size` and the DNS `value` should get the same
treatment. These two findings agree and are worth reading together: the
probe above independently confirms 2d's claim that the guard holds at
`warn` and `off` (this is what the `mode=warn`/`mode=off` rows show), so
the precedent is sound. What 2b adds is that the precedent is **unproven
by test** — the guard works, and nothing in the suite would notice if it
stopped. A guard 2d wants copied to two more parameters should be one
whose own coverage is real first.

---

#### Mutation testing

**Ran `bash tools/mutate-nested-sweep.sh`. Confirmed: `16 mutations
applied, 0 survived.`** Every mutation was caught by the row named in
the script, and the named row appears in the failure output in each
case. The baseline gate fired green first, so the run means something.
The four files it touches were restored; `git status` afterwards showed
no change to them.

The script is good work and its design notes are right about why it
exists. Two observations the script cannot make about itself:

* **It does cover the review round, but not all of it.** Of the guards
  added during the phase 7 review, mutation 11 covers the videospec
  `model` null guard (`instance.py:932`), mutation 12 covers
  `validation.declared_boolean` at its call site
  (`instance.py:482`), and mutations 6 and 6b cover *both halves* of the
  `_schema` literal-key discrimination — the key test and the
  element-is-a-Mapping test — which is exactly right, since each half is
  caught by a different row (`disk.element_not_a_mapping` and
  `disk.schema_key`). Three review-added guards have no mutation:
  `instance.py:929` (`if not isinstance(video, dict)`, the shape guard
  the review had to add in front of the value tests), `instance.py:934`
  (the `memory` null guard) and `instance.py:936` (the `vdi` defaulting,
  which is what `test_a_null_video_key_is_never_stored` exists for).

  I wrote and ran the three missing mutations (`extra_mutants.sh`,
  restored by `cp` from a copy taken first). **All three are caught**:

  ```
  videospec memory null guard -> presence test   caught: video.memory.null [enforce]+[warn]
  videospec vdi defaulting -> presence test      caught: test_a_null_video_key_is_never_stored (all 3 modes)
  videospec non-mapping shape guard removed      caught: video.not_a_mapping [warn]+[off]
  ```

  So the rows are sound; it is the script that is behind the code. The
  middle result is the valuable one: it proves
  `test_a_null_video_key_is_never_stored` is not vacuous, which matters
  because it is a mock-`call_args` assertion behind a 507 precondition
  and is otherwise the most plausibly-vacuous test in the file.
  Advisory: add those three mutations to the script. An unmutated guard
  is an unproven one, and the script's stated purpose is that the set
  grows as the schemas do.

* **The mutation set is bounded by the sweep, and the sweep is bounded
  to three specs.** Every mutation targets `base.py`'s three structured
  schemas, `validation.py`'s object branch, or the two-then-three
  `instance.py` guards. Nothing mutates the declaration-time guards in
  `swagger_helper()` (see B3) or the four guards in B2, because the
  sweep does not reach them. Advisory, and the honest framing is that
  the tool is correctly scoped to what it names — but the audit should
  record that "16 mutations, 0 survivors" is a statement about the
  nested sweep, not about the plan.

One hazard worth a line for whoever runs it next: `restore()` copies all
four files back from a snapshot taken before the first mutation, so a
*concurrent* edit to `base.py`, `validation.py`, `instance.py` or
`test_nested_sweep.py` by another process is silently reverted. The
header's "Uncommitted work is safe" is true of the script's own
mutations and not of a sibling agent's edits. Advisory; a `git status`
check on those four paths at startup would close it.

---

#### B3 — advisory. Three import-time declaration guards have no test, and testing them found a false refusal

Intersecting `cover/` with the plan-authored line set leaves 37
uncovered lines across 8 files. Most are unreachable defensive arms
(`except RuntimeError: pass` around `flask.g` writes at
`base.py:1704-1705` and `:1752-1755` and `:2233-2234`, the audit-event
`except Exception` at `base.py:1309-1313`, `if resource is None` at
`base.py:2196`) and are not findings. Two groups are.

B2 covers the first group. The second is in `swagger_helper()`'s pattern
validation:

* **`base.py:846`** — the refusal of Python-only regex constructs
  (`(?P`, `(?#`, `\A`, `\Z`) in a declared `pattern`. Never executed by
  any test.
* **`base.py:875,877,879,881`** — the escape and paren-depth branches of
  the top-level-alternation scanner. Never executed, because the only
  pattern the test table feeds it (`'^a|b$'`,
  `test_parameter_declarations.py:2098`) contains no `\`, `(` or `)`.

`test_parameter_declarations.py:2094-2095` carries the claim *"A grouped
alternation like `^(a|b)$` is fine"* as a comment with no test behind
it. Probed the whole guard directly through `api_base.swagger_helper`:

```
ACCEPTED '^(a|b)$'
ACCEPTED '^((a|b)|c)$'
ACCEPTED '^a\|b$'
REFUSED  '^(?P<x>a)$' -> Python only construct(s) (?P
REFUSED  '^(?#c)a$'   -> Python only construct(s) (?#
REFUSED  '\Aa\Z'      -> Python only construct(s) \A, \Z
REFUSED  '^[a|b]$'    -> declares a pattern with a top level alternation
```

The last line is a defect. A `|` inside a character class is a literal
pipe, not an alternation; the scanner tracks `(`/`)` depth but not
`[`/`]`, so any declaration whose pattern contains a pipe in a character
class is refused at import time and **sf-api does not start**. Nothing
in tree declares one today (`util_network.MACADDR_PATTERN` is the only
declared pattern), so nothing shipped is broken and this is advisory
rather than blocking — but it is the next member of the class, it was
found in the first five minutes of exercising branches the coverage
report says are never exercised, and that is the argument for the
finding. Fix: track `[`/`]` alongside `(`/`)`, and add the four positive
cases above plus the four refusals to the table at
`test_parameter_declarations.py:2075-2114`.

Otherwise the zero-coverage answer to the brief's question 4 is
reassuring: `shakenfist/external_api/validation.py`, the plan's
centrepiece, is 294 statements with **1** uncovered
(`validation.py:1000`, the scalar-message tail of `_flatten_messages`)
and 3 uncovered branches; `declarations.py` is 283 with 5. There is no
added production *module* or *function* with no test reaching it.

---

#### Functional coverage

`shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_api_validation.py`
is 638 lines and 8 test methods, added by phases 4, 5, 6 and 7 only.
Ranges 1-7 (phases 0-3, 9,600+ insertions) added no functional coverage
at all. That is defensible and should be recorded so 8g does not treat
it as a gap: until D16 flipped the default in phase 4 the layer changed
no caller-visible behaviour, so there was no functional test that could
have failed before and passed after.

The existing CI set is the right set. `TestEveryDocumentedSpecKeyStillBoots`
(`test_api_validation.py:518`) is the one that matters most — it sends
every documented key of all three specs, with the values a real caller
sends, and requires the instance to create, boot and answer an agent
command, on the stated reasoning that *"a suite which only tested
refusals would pass just as well against a server which refused
everything."* `TestNullNetworkUuidRefusedOnHotplug` covers the F7 guard
for real. Nothing there is testing a mock of itself.

**What the unit sweep proves that CI does not.** Three things, in
descending order of how much it matters that CI is silent about them:

1. **`warn` and `off`.** `NestedSweepWarnTestCase` and
   `NestedSweepOffTestCase` run all 104 rows at both modes; CI runs only
   the deployed default. `grep -rn API_VALIDATION_MODE shakenfist/deploy/`
   returns exactly one hit, and it is a docstring
   (`test_api_validation.py:49`). The operator's documented escape hatch
   has never been exercised against a real cluster.
2. **Error message text.** 61 of the 104 rows assert a specific message
   (`disk[0].siz: Unknown field.` and its kin). CI asserts a handful.
3. **Derived completeness** — `test_every_key_has_an_accepted_value` and
   the create/hotplug parity check. These are computed from the table
   and cannot exist in CI at all. Mutation 14 proves they are not
   vacuous.

**Recommendation — one, not a list.** Add a single cluster CI case that
sets `API_VALIDATION_MODE=warn` on the API node, restarts `sf-api`, and
asserts two things: that a request `enforce` refuses (an undeclared body
key) now succeeds, and that the three handler guards still refuse (a
null `network_uuid` on hotplug, a diskspec asking for neither size nor
base, a null `video.model`). That is the whole of D34 and D42 in one
test. It earns CI over unit because it is the only part of this plan
whose contract is with an *operator* rather than a client, it will be
exercised under pressure during an incident, and it depends on process
restart and config rendering that a unit test cannot see. Everything
else the sweep proves — coercion semantics, message strings, per-key
width — is a property of one process's parsing and is correctly
unit-level; promoting it would slow CI without adding a fact.

That recommendation has a precondition which belongs to 2c/2d rather
than here, so I am flagging it rather than writing it up:
`API_VALIDATION_MODE` does not appear anywhere in
`shakenfist/deploy/collection/`, and
`roles/node/templates/config` is an explicit list of 40 `SHAKENFIST_*`
lines with no generic passthrough. The rollback that
`docs/release_notes/v07-v08.md:213` and `:421` tell operators to use
cannot be set through the supported deployment path. If 2d has not
raised it, it is theirs; if it has, this is the same finding and should
be merged rather than filed twice.

The second CI case worth having, once B1 is fixed, is `{"uefi": "false"}`
booting BIOS — a one-request assertion that would have caught B1 and is
a contract a `curl` or shell-script client genuinely depends on. I am
naming it as a companion, not as a second recommendation.

---

#### Recorded as clean, so nothing re-derives it

* `STRUCTURED_PARAMETERS` (`test_openapi_spec.py:105-...`) pins the
  published document, which *is* the contract, so pinning it is correct
  rather than fragile. It checks both directions — declared keys must
  match and any constraint key not listed must be absent
  (`test_openapi_spec.py:470-476`) — and its completeness is derived
  from the specification, so a new structure fails CI until it has an
  entry. Not a finding.
* `test_api_reference_specs.py` compares the reference's key sets,
  types, required-ness and enum values against `ARGTYPES` in both
  directions and deliberately does not compare prose. The reasoning in
  its header for what is *not* compared (the `bus` bullet naming `ide`
  in order to say it is refused) is correct. Not a finding.
* `test_validation_compiler.py` is 1,514 lines with only 8 assertions on
  compiler internals (which `fields.*` class was built, at lines
  152, 308, 355-376). For a compiler test, building the right field *is*
  the behaviour. Not a finding.
* `RequiredSweepTestCase` runs only at `warn`
  (`test_required_sweep.py:639`), and its docstring gives the right
  reason: at `enforce` the validation layer answers before any handler
  is reached, which is the opposite of what the file measures. The
  shipped `enforce` answer for the 76 required declarations is covered
  at four individual parameters rather than swept, but it comes from one
  `if` in `validate_request` and `test_the_census_still_finds_seventy_six`
  plus the single-element `RELAXED_BY_STEP_2` hold the declaration set
  closed. Low enough not to be worth a finding.
* All 970 `external_api` tests pass on the tree as it stands.
* One trivial drift, recorded rather than graded:
  `test_openapi_spec.py:113` cites `external_api/instance.py:833` for
  the videospec defaulting, which moved to `:894-895` when the review
  inserted the guards at `:929-937`. The four other citations in that
  comment block (`instance.py:1715`, `:2153`, `:2161`,
  `libvirt.tmpl:156`) were checked and are accurate.

---

#### Findings summary

| # | Finding | Grade |
|---|---|---|
| B1 → **B-4** | `declared_boolean` applied at 1 of 19 declared-boolean sites; `uefi` and `secure_boot` still read falsy string spellings truthily, so the published schema says something the server contradicts, for any authenticated caller. Verified by probe. Artifact `shared` is in the same class but is an admin foot-gun, not a cross-namespace escalation (corrected at collection). | **blocking** |
| B2 | Four handler guards added by this plan (`artifact.py:828`, `label.py:130`, `snapshot.py:86`, `blob.py:211,213`) have tests written to pin them, and at `enforce` the validation layer answers every assertion first; the guards have zero executed coverage and are the only defence under `warn`/`off`. Guards verified correct; tests verified to be passing for the wrong reason. | advisory |
| B3 | `base.py:846` and `:875-881` (the pattern dialect refusal and the alternation scanner's escape/depth branches) have no test; exercising them found `^[a|b]$` falsely refused at import time because the scanner tracks `()` but not `[]`. Latent — nothing in tree declares such a pattern. | advisory |
| B4 | The nested sweep's observable is `(status, exception, message)`, and 35 of its 43 accepted rows resolve to a scheduler 507, so the table is structurally blind to *accepted with the wrong meaning* — the class of both phase 7 review defects. The two tests that do observe effects are hand-written one-offs; nothing derives the obligation. Recommend two derived differential tests (null-equals-absent; string-spelling-equals-boolean). | advisory |
| B5 | `tools/mutate-nested-sweep.sh` confirmed at 16/0. Three review-added videospec guards (`instance.py:929`, `:934`, `:936`) have no mutation; I wrote and ran them and all three are caught, so the gap is in the script, not the coverage. Also: the script's `restore()` silently reverts concurrent edits to its four files. | advisory |
| B6 | No functional coverage for `warn` or `off` anywhere in `shakenfist/deploy/`. One CI case recommended. Precondition — `API_VALIDATION_MODE` cannot be rendered by the ansible collection at all — flagged to 2c/2d rather than filed here. | advisory |

One blocking finding, B1, collected as **B-4**. Nothing else in the test
surface says the shipped behaviour is wrong.

### 2c. Documentation

Scope: the sixteen `docs/` files touched across the twelve audit ranges
(`for s in <the twelve>; do git diff --name-only "$s^1..$s"; done | sort
-u | grep '^docs/'`):

```
docs/developer_guide/api_reference/instances.md
docs/developer_guide/writing_an_endpoint.md
docs/operator_guide/logging.md
docs/plans/PLAN-api-input-validation-phase-00-decisions.md
docs/plans/PLAN-api-input-validation-phase-01-declaration-audit.md
docs/plans/PLAN-api-input-validation-phase-02-type-vocabulary.md
docs/plans/PLAN-api-input-validation-phase-03-compile-and-warn.md
docs/plans/PLAN-api-input-validation-phase-04-enforce.md
docs/plans/PLAN-api-input-validation-phase-05-narrow.md
docs/plans/PLAN-api-input-validation-phase-06-required.md
docs/plans/PLAN-api-input-validation-phase-07-structured.md
docs/plans/PLAN-api-input-validation.md
docs/plans/index.md
docs/plans/order.yml
docs/release_notes/v07-v08.md
docs/user_guide/usage.md
```

#### Fixes applied

F6's three confirmed defects, fixed directly per this step's brief
(diagnosis was not repeated, only verified against the pinned
marshmallow 4.3.1 — `sorted(fields.Boolean.truthy)` /
`.falsy` were run in a scratch venv to check the exact sets before
writing new prose):

1. **`docs/release_notes/v07-v08.md:427`** — "That second one
   previously resolved to an arbitrary network" named the third item
   of a three-item list by ordinal, a leftover from a two-item
   version. Changed to name the case directly ("The null
   `network_uuid` case previously resolved...") and rewrapped the
   107-character line to match the paragraph's ~72-character wrap
   (new lines are 63-70 characters).
2. **`docs/developer_guide/api_reference/instances.md:153-159`** and
   **`docs/user_guide/usage.md:307-310`** — both enumerated `true`,
   `yes`, `on`, `1` (and negatives) "and their cases" as the accepted
   `float` spellings. Verified against marshmallow 4.3.1:
   `fields.Boolean.truthy` = `{1, 't', 'ON', '1', 'Y', 'Yes', 'TRUE',
   'True', 'true', 'y', 'yes', 'on', 'YES', 'T', 'On'}`, `.falsy` =
   `{0, '0', 'f', 'n', 'Off', 'OFF', 'false', 'No', 'False', 'F',
   'no', 'off', 'N', 'NO', 'FALSE'}` — so the single letters
   `t`/`T`/`y`/`Y`/`f`/`F`/`n`/`N` were missing from the docs, and
   "their cases" overstated it (`tRue` is not in either set, so
   `declared_boolean()`'s schema-layer caller refuses it with a 400
   rather than falling through to the `bool()` branch in
   `shakenfist/external_api/validation.py:166-194`). Both files were
   rewritten to say a JSON boolean is the expected form, that a range
   of string spellings is also read, and that this acceptance is
   narrower than it looks and should not be relied on — rather than
   enumerating sixteen tokens.
3. **`docs/user_guide/usage.md:307-310`** — claimed the `sf-client`
   and ansible interfaces "convert what you type before it reaches
   the API," implying a faithful, complete conversion. Checked both,
   read-only, against the sibling `client-python` repo and this
   repo's ansible module:
   - `sf-client`'s own `-N`/`--networkspec` parsing
     (`client-python/shakenfist_client/commandline/instance.py`,
     `_parse_detailed_netspec()`, `value = s[1] in ['true', 'True']`,
     used by both `instance create -N` and `instance add-interface
     -N`) reads **only** the literal strings `true` or `True` as
     true, case-sensitively. `yes`, `on`, `1`, `TRUE`, `t`, `y` are
     all silently read as false. This section of `usage.md` is
     specifically documenting what you type at that `-N` command
     line, so this is the most directly relevant fact to it.
   - `shakenfist/deploy/collection/plugins/modules/sf_instance.py:333`
     converts only `s[1].strip().lower() in ('true', '1', 'yes')` —
     case-insensitive but a different, also-narrow set (no
     `on`/`t`/`y`/single letters).
   - So the two interfaces do not even agree with **each other**:
     `-N ...,float=on` means false via `sf-client` and false via
     ansible (neither's set contains `on`); `-N ...,float=1` means
     false via `sf-client` (not in `['true','True']`) but true via
     ansible (`'1'` is in its tuple). **Verified independently at
     collection** against the sibling repository:
     `client-python/shakenfist_client/commandline/instance.py:327` is
     `value = s[1] in ['true', 'True']`, so `float=yes`, `float=1`,
     `float=on` and `float=TRUE` are all *silently* read as false with
     no error and no warning. That is the same class of silent wrong
     answer this whole plan exists to eliminate at the API boundary, so
     the audit finding is that the plan closed it at the server and left
     the shipped client doing it to its own users. It is a
     `client-python` defect and cannot be fixed on this branch; 8g files
     it, and should check whether it belongs with the already-open
     `client-python#398`. Rewrote the `usage.md` bullet to
     state `sf-client`'s actual literal-`true`/`True`-only behaviour
     (since that's the syntax the paragraph documents), point to the
     API's wider acceptance, and name the ansible divergence rather
     than claiming either interface "converts what you type."

All three fixes are confined to the three files/paragraphs named in
this step's brief. Diffs:

```
docs/developer_guide/api_reference/instances.md | 10 ++++++----
docs/release_notes/v07-v08.md                   |  8 ++++----
docs/user_guide/usage.md                        | 12 +++++++++---
```

#### The error-contract cross-reference (the real question)

The plan documents the same request-validation error contract — the
`enforce`/`warn`/`off` modes, the `{"error": "<parameter>: <reason>",
"status": ...}` shape, "names the first finding only", and whether a
missing-required parameter is refused — in (at least) four places
written across four phases:

| Location | Phase written | Says missing-required is enforced? |
|---|---|---|
| `docs/developer_guide/writing_an_endpoint.md:349-352` (general summary) | 3/4 (compile-and-warn / enforce) | **No** — stale |
| `docs/developer_guide/writing_an_endpoint.md:409-411` ("`required` is enforced" section, same file) | 6 (required) | Yes — correct |
| `docs/operator_guide/logging.md:174-182` | 4 (enforce) | **No** — stale |
| `docs/release_notes/v07-v08.md:187-215` and `:286-303` | 4 then 6, written as a changelog | Correctly scoped: says exemption held "at this point in the rollout" and separately documents the phase-6 closure |
| `docs/developer_guide/api_reference/instances.md:66-73` | 7 (structured) | Silent on missing-required specifically; the rest of the contract it states is accurate |

Checked each against `shakenfist/external_api/base.py`'s
`validate_request()` (the actual enforcement point), not against each
other, per the brief:

```python
if config.API_VALIDATION_MODE == 'enforce':
    # Every reason code is enforceable, missing-required
    # included (decision D37 of phase 6 keeps the reason code
    # itself, in the log and in findings, so an operator on
    # 'warn' can still tell a required failure from a type
    # one -- only the exemption from rejection is gone).
    if findings:
        first = findings[0]
        ...
        return sf_api.error(400, '%s: %s' % (first.parameter, first.detail))
```
(`shakenfist/external_api/base.py:2236-2244`; the docstring above it,
`base.py:2144-2148`, states the same thing: "Phase 6 closed the last
exemption... missing-required is refused like any other reason now.")

**Finding: two of the four documents are stale and contradict current
code (and, in one case, contradict themselves).**

- `docs/developer_guide/writing_an_endpoint.md:349-352` says: *"a
  finding other than `missing-required` answers `400`... naming the
  offending parameter"* — i.e. missing-required is exempt. This was
  true when phase 4 wrote it and false since phase 6. The same file's
  own later section, added in phase 6, is correct: *"`required` is
  enforced. An omitted parameter and an explicit JSON `null` both
  answer `400 <parameter>: declared required but not supplied`, in
  every `API_VALIDATION_MODE` but `warn` and `off`"*
  (`writing_an_endpoint.md:409-411`). Nothing links the two sections
  or updates the first, so the file contradicts itself depending on
  which section a reader lands on.
- `docs/operator_guide/logging.md:174-182` says the identical stale
  thing — *"a finding other than `missing-required` refuses the
  request... A parameter declared required but not supplied is
  recorded and never enforced"* — with no correction anywhere else in
  that file. An operator reading only this doc (its stated audience)
  would conclude a caller can omit a required field under the default
  `enforce` mode and merely get logged, which is wrong: it gets a 400.
- `docs/release_notes/v07-v08.md` gets this right by being careful
  about **when**: the enforce-flip entry explicitly says "*At this
  point in the rollout `required` was still recorded and never
  enforced... see the required-ness entry below for when that
  changed, later in this cycle*" (`v07-v08.md:208-210`), and a later
  bullet documents the phase-6 closure in full
  (`v07-v08.md:286-303`). This is the one document of the four that
  is unambiguously correct, because a changelog is allowed to describe
  a past state as long as it is dated.
- `docs/developer_guide/api_reference/instances.md:66-73` doesn't
  make a missing-required claim either way, so it isn't wrong, just
  silent on this one nuance — not a defect.

Confirmed this is not a git-blame accident of "the file predates
phase 6 and nobody looked at it since": `writing_an_endpoint.md`'s
correct section (`:409-428`) and its stale section (`:349-352`) are in
the same file, so whoever wrote phase 6's addition read past the
stale paragraph without updating it.

**Grade: blocking — collected as B-1.** Per decision 5,
"undocumented in a way that misleads": this is worse than
undocumented, it is documentation that affirmatively asserts the
opposite of current behaviour, in the one guide
(`writing_an_endpoint.md`) new-endpoint authors are told to read and in
the one guide (`logging.md`) an operator reads to interpret
`API request validation finding` log lines. 8a reached the same
paragraphs by reading the decisions as a set, and 2d reached them by
measuring the stack and added two sites this step's document-level
read could not see — `shakenfist/config.py:291-293` and
`shakenfist/external_api/instance.py:654-656`. One finding, four sites;
see B-1. Fixing is 8g's call per decision 6. Recommend: reword
`writing_an_endpoint.md:349-352` to drop the missing-required
exemption clause (it can simply say "any finding answers 400", since
that's now uniformly true and the nuance belongs only in the
`required`-specific section below it), and drop
`logging.md:181-182`'s final sentence and the "other than
`missing-required`" qualifier at `logging.md:176`.

**A related, smaller finding found by the same cross-check:**
`docs/developer_guide/writing_an_endpoint.md:415` and `:431` each
name "phase 6" in prose ("`[phase 6](../plans/PLAN-api-input-validation-phase-06-required.md)`
audited every `body`/`query` declaration..." and "...the `SWEEP` table
published in the phase 6 plan") — a phase reference in a
`developer_guide` file, which the plan-phase-references shared block
calls a plan smell by definition, outside a plans directory and
without an `<!-- audit-ok: phase-reference -->` annotation. `git
blame` confirms both lines were added by this plan itself (ancestor
check: `e31e4d9b1a` is reachable from `81aa9a7d0`, the phase-6 merge,
range 11 of the twelve), so this is in scope rather than inherited.
It doesn't mislead about current behaviour (the described behaviour
*is* current), so **grade: advisory**, and it overlaps with the
repo-wide consistency audit already tracked as issue #3732 (per the
shared block's own text, `PUSH-AUDIT.md:479-483`) — recommend folding
it into that issue rather than filing a new one.

#### Should the error-contract half get `test_api_reference_specs.py`-style mechanical enforcement?

`shakenfist/tests/external_api/test_api_reference_specs.py` derives
the *structured-parameter* half (key sets, types, required-ness, enum
values) straight from `ARGTYPES` and fails CI if
`instances.md`'s diskspec/networkspec/videospec prose drifts from it
— deliberately excluding prose descriptions, because comparing full
sentences would fail on a wording fix rather than a fact change (its
own docstring, `test_api_reference_specs.py:41-45`, makes this
argument explicitly).

**Recommendation: partial, narrow mechanization — not the full
narrative comparison, which would be over-fitting.**

- Full-text parity across the four documents would be wrong for the
  same reason `test_api_reference_specs.py` excludes prose: a
  developer guide, an operator guide, and a dated changelog entry
  correctly say the same fact in different words for different
  audiences (see `v07-v08.md`'s deliberately time-scoped phrasing
  above, which a strict-parity test would have flagged as
  "different" even though it's the one document that's right).
  Building that test would force it to either accept prose
  divergence (defeating the point) or forbid legitimate
  audience-specific phrasing (as `test_api_reference_specs.py`'s
  docstring warns against).
- But a handful of facts in this contract *are* single booleans or
  fixed strings with no legitimate reason to phrase two ways, and
  those are exactly what drifted here:
  1. The literal error shape string
     `{"error": "<parameter>: <reason>", "status": ...}` (or the
     `400`-pinned variant) appears verbatim in three of the four
     files. A test asserting all live occurrences (excluding
     `docs/plans/`, which are historical by convention) are
     byte-identical up to the `.../400` variance would catch a typo
     or format change with zero false positives — this is exactly
     `test_api_reference_specs.py`'s "compare against a script, not a
     reviewer's eye" argument, applied to a shorter string.
  2. Whether a missing-required finding is refused under `enforce`
     is a single fact `test_required_sweep.py` already proves at
     runtime (76 declarations, per the release note). Nothing ties
     that proof to the prose. A one-line addition — grep
     `writing_an_endpoint.md` and `logging.md` (outside
     `docs/plans/`) for text matching `missing-required.*never
     enforced` or `other than.*missing-required` and fail if found,
     analogous to the phase-reference grep `PUSH-AUDIT.md` already
     describes for issue #3732 — would have caught this specific
     regression without asserting anything about wording elsewhere.
- Building the general case (diff every sentence of the four
  documents against each other) is over-fitting: it would encode the
  current wording as the only acceptable wording, which is the
  opposite of what a living operator guide and a frozen changelog
  should do. Building the narrow case (pin the wire-format string,
  and add a boolean check tied to what `test_required_sweep.py`
  already proves) is proportionate and would have caught exactly the
  defect found here.

#### Other checks performed against `PUSH-AUDIT.md`'s 2c brief (clean)

- **README discipline**: none of the twelve ranges touch `README.md`.
  Clean.
- **AGENTS.md / ARCHITECTURE.md discipline**: `ARCHITECTURE.md` is
  untouched by any of the twelve ranges. `AGENTS.md` gained two small
  sections (range 1, phase 0+1: "API parameter declarations are
  enforced at import time" plus three new pre-commit hook bullets;
  range 4, phase 2: an expansion of the same section covering the
  constraints dict and body-parameter rendering). `AGENTS.md` is
  outside this step's assigned sixteen `docs/` files, so this is a
  spot-check rather than a full review, but both additions are
  short, state invariants an agent can't infer by reading one file
  (import-time validation, the shared pre-commit/CI derivation), and
  each ends with a link into
  `docs/developer_guide/writing_an_endpoint.md` for the full
  reference rather than restating it — in line with the shared
  block. No growth-that-belongs-in-docs/ finding.
- **Diagram discipline**: grepped every range's `docs/` diff for
  fenced `mermaid` blocks and ASCII box-drawing characters. Found
  none of either — only markdown tables (`|---|---|`) were added.
  Nothing to convert, nothing new to flag.
- **State machine docs**: none of the twelve ranges touch any
  object's `state_targets` map (this plan is about request
  validation, not state machines), and none of the sixteen files is
  `docs/developer_guide/state_machine.md`. Per the brief this is
  worth a pass regardless — spot-checked and it does not mention
  input validation at all, so there is nothing for this plan to have
  drifted. Clean.
- **Database schema / migration guidance**: this plan adds no schema
  change (it lives entirely in the request-handling layer), so no
  migration doc is owed. Clean.
- **Plan-file freshness** (`docs/plans/index.md` and the phase
  plans): this checklist item is already covered in depth by this
  audit's own F1-F7 (SHA correctness, phase-completion status,
  `index.md` arithmetic) and by step 8a's decision-set review; adding
  a second pass here would re-derive rather than find anything new.
  Per F7, `index.md` line 111's `8 of 9`/`In progress` is correct as
  of this audit and is 8g's to flip to `9 of 9`/`Complete`, not this
  step's.
- **Phase-reference smell in the other fifteen files**: grepped all
  five non-plan files in scope (`instances.md`, `writing_an_endpoint.md`,
  `logging.md`, `v07-v08.md`, `usage.md`) for `phase <number>`.
  Found only the two `writing_an_endpoint.md` occurrences reported
  above; the other four are clean. (`docs/plans/*` are exempt by the
  shared block's own rule.)

#### Findings summary

| # | Location | Grade | Status |
|---|---|---|---|
| F6.1 | `docs/release_notes/v07-v08.md:427` | — | **Fixed** (this step) |
| F6.2 | `docs/user_guide/usage.md:308`, `docs/developer_guide/api_reference/instances.md:157` | — | **Fixed** (this step) |
| F6.3 | `docs/user_guide/usage.md:309-310` | — | **Fixed** (this step) |
| New-1 | `docs/developer_guide/writing_an_endpoint.md:349-352` and `docs/operator_guide/logging.md:174-182` describe missing-required as unenforced; both are stale since phase 6 and contradict `base.py:2236-2244` (and, for the first file, contradict its own `:409-411`) | **Blocking** | Merged into **B-1** with 8a's findings 1-2 and 2d's S1 |
| New-4 | `sf-client`'s `-N ...,float=` parser reads only the literal `true`/`True`, silently, so four documented spellings mean False at the CLI (`client-python/.../commandline/instance.py:327`) | Advisory | Verified at collection; a `client-python` defect, so 8g files rather than fixes |
| New-2 | `docs/developer_guide/writing_an_endpoint.md:415,431` name "phase 6" outside a plans directory, added by this plan (phase 6, range `81aa9a7d0`) | Advisory | Reported for 8g disposition; overlaps issue #3732 |
| New-3 (process) | Recommend a narrow mechanical check (wire-format string identity + a grep tied to `test_required_sweep.py`'s existing proof) for the error contract's fixed facts; explicitly recommend against full narrative-parity testing as over-fitting | Advisory (process recommendation, not a code defect) | For 8g/future work |

### 2d. Security

`PUSH-AUDIT.md`'s 2d brief, worked across the twelve ranges of decision 1.
Findings are pooled, not per range, because every one of them arises from a
statement or a guard that crosses phase boundaries -- which is what decision 4
predicted and what this step actually found.

**Result: one blocking finding (collected as B-1), six advisory.** The
blocking one is not a vulnerability; it is a false statement about what the
enforcement refuses, written in phase 3, carried by phase 4, invalidated by
phase 6, and surviving in four places including the operator guide and the
rendered configuration reference. **No shipped behaviour is insecure at the
default mode**, which is the sentence the master plan's phase 8 row asks this
step for. The advisory findings are about `warn` and `off`, where the schema
is not running and a handler guard is the only thing left.

Measurements below were run rather than read. Probe scripts are in this step's
scratchpad (`probe_dns.py`, `probe_ping.py`, `probe_required.py`,
`probe_derivation.py`, `probe_cost.py`, `tab.py`); none touched the worktree.
`test_nested_sweep`, `test_required_sweep`, `test_server_error_logging`,
`test_auth_universal`, `test_format_validation`, `test_nested_required_null`,
`test_parameter_declarations`, `test_validation_compiler`,
`test_request_validation`, `test_openapi_spec`, `test_api_reference_specs` and
`test_derivation_generator` were each run and all pass on this tree.

---

#### S1 (blocking, collected as B-1). "missing-required is recorded and never enforced" is false in four places

Merged into **B-1**, which carries the full site list, the measurement and
the note that `docs/release_notes/v07-v08.md:210` is correct and must not be
changed. Recorded here only for what this step contributed that the other two
could not: it arrived at the finding by *measuring the real authenticated
stack* rather than by reading documents or decisions, and that is how it found
the two sites neither of the others reached —
**`shakenfist/config.py:291-293`**, the `API_VALIDATION_MODE` description
rendered into the configuration reference, and
**`shakenfist/external_api/instance.py:654-656`**, a code comment citing D17
as the reason for a guard that is still needed for a different reason. Three
methods, one paragraph, and each method found a site the others missed; the
documentation grep would never have reached a pydantic `Field` description.

#### S2 (advisory). Two request values are guarded only by the schema, and their sink is not a Python exception

The rollback question the sweep cannot answer -- whether `off` is *safe* or
merely *documented* -- comes down to this. The 104 rows of
`shakenfist/tests/external_api/test_nested_sweep.py` were tabulated
mechanically: **52 of 104 rows answer differently at `enforce` than at
`warn`/`off`**, of which 18 are a recorded 500 (`Answer(500, True, 'server
error')`) and 34 are silently accepted. Every row of the second group was walked
to its sink. All but two are caller-local -- a wrongly typed value on the
caller's own instance, a typo'd key discarded, a `vdi` string that
`AttributeError`s in the console endpoint. Two are not:

**(a) `value` on `POST /networks/{ref}/dns`.** Declared `ipv4`
(`shakenfist/external_api/network.py:792-794`), which since phase 6 step 4 means
`validation._format_ip_address` (`shakenfist/external_api/validation.py:312-337`).
The handler checks `name` with `validators.hostname()`
(`shakenfist/external_api/network.py:807-810`) and checks `value` **not at all**.
It is stored on the network's `hosteddns` attribute
(`shakenfist/network/network.py:908-909`) and rendered raw as `{{value}} {{name}}`
into dnsmasq's `addn-hosts` file
(`shakenfist/deploy/collection/roles/network/files/dnshosts.tmpl:6`, rendered at
`shakenfist/managed_executables/managedexecutable.py:102-104` through a
`jinja2.Template` with autoescape off at `:85`). Measured:

```
MODE=enforce  value='10.0.0.1 innocent\n10.0.0.2 victim.example.com'  400 'value: Not a valid IP address.'  hosteddns={}
MODE=warn     same                                                    200  hosteddns={'probe': '10.0.0.1 innocent\n10.0.0.2 victim.example.com'}
MODE=off      same                                                    200  hosteddns={'probe': '10.0.0.1 innocent\n10.0.0.2 victim.example.com'}
MODE=warn/off value='not-an-ip-at-all'                                200  hosteddns={'probe': 'not-an-ip-at-all'}
```

A newline in `value` injects extra host entries into the hosts file the
network's dnsmasq serves. Blast radius is the caller's own network (one dnsmasq
per network, config dir keyed on the network uuid at
`shakenfist/managed_executables/managedexecutable.py:54-55`), so no tenancy
boundary is crossed -- which is why this is advisory and not blocking. Note it
bites under `warn` as well as `off`: `warn` is the *documented* rollback, and
this is the one parameter in the API whose only validation is a schema check and
whose sink is a configuration file consumed by a process rather than a Python
expression.

**(b) `disk[].size`'s `minimum: 0`.** `shakenfist/external_api/base.py:447`. No
handler guard: `shakenfist/external_api/instance.py:722-747` checks the
size/base pair and the bus, never the sign. A negative size is summed straight
into the capacity claim (`shakenfist/mariadb.py:24697-24745` via
`shakenfist/instance.py:1055-1056`) and into the scheduler's own arithmetic
(`shakenfist/scheduler.py:586-589`). The guarded `UPDATE` in
`admit_instance_placement` compares `used + requested <= limit`, so a negative
request always admits and *deflates* `used_disk_gb` on the node and
`unclaimed_used_disk_gb` on the cluster singleton -- which inflates the capacity
other namespaces' claims are admitted against. That is the one cross-namespace
effect in the whole `off` inventory. The sweep row records it
(`disk.size.negative`, note: *"a negative size corrupts the capacity ledger at
scheduler.py:473"*) and measures it as `ACCEPTED` at `warn`.

**Why this is a finding and not just the rollback doing its job.** D34 and D42
keep handler guards in place at every mode precisely so a rollback cannot hand
back the bug a phase just closed, and phase 7 wrote *three* new handler guards
for exactly that reason (null `network_uuid`, a diskspec asking for neither size
nor base, a null `video.model`/`video.memory`). The plan also has a worked
precedent for the destructive-negative class: `max_versions` carries
`unsignedinteger`'s `minimum: 0` **and** `artifact.validated_max_versions()`
(`shakenfist/artifact.py:43-67`), called from all three writing routes
(`external_api/artifact.py:827`, `label.py:129`, `snapshot.py:85`) and from the
setter (`artifact.py:536`), so it holds at every mode. The same reasoning was not
applied to `disk[].size` or to the DNS `value`. Recommended disposition: file for
two handler guards -- `ipaddress.ip_address()` in
`NetworkDNSAddressEndpoint.post` and a non-negative check on each diskspec size
-- so the rollback stops being the only thing standing between these and their
sinks. No behaviour change at the shipped default.

`disk[].type` belongs to the same class: `_get_defaulted_disk_type()`
(`shakenfist/instance.py:105-109`) returns any truthy value unchanged into
`device='{{disk.present_as}}'`
(`.../hypervisor/files/libvirt.tmpl:50`). Phase 7's enum closes it at `enforce`;
at `warn`/`off` it is a raw XML sink.

---

#### S3 (advisory). #4242 is the only *enforce*-mode raw-XML injection site, but it is scoped one sink too narrowly

Every value this plan declares as an unconstrained string was enumerated from the
rendered specification (46 distinct names, listing in the scratchpad) and walked
to its sink. The complete set of raw interpolations into the libvirt domain XML
reachable from a request at `enforce` is exactly `network[].model` and
`video.model` -- the two #4242 names. Verified against
`shakenfist/instance.py:2072-2073` (`jinja2.Template(f.read())`, autoescape off)
and `:2143-2163` (the `render()` argument list), item by item:

| Template variable | Source | Reachable? |
|---|---|---|
| `uuid`, `instance_path`, `console_port`, `vdi_port`, `vdi_tls_port`, `nvram_template_attribute` | server-generated | no |
| `memory`, `vcpus`, `video_memory` | integer-typed | no (non-numeric refused by `fields.Integer._validated`) |
| `machine_type` | set unconditionally to `'pc'`/`'q35'` at `external_api/instance.py:644,693`; **not a declared parameter at all** | no |
| `disk.*` | `bus`/`present_as` enum-constrained; `device`/`path`/`source`/`backing`/`type` server-built from `_get_disk_device(bus, i)` | no at `enforce` |
| `net.macaddr` | anchored pattern (`util_network.MACADDR_PATTERN`, PR #4183) | no |
| `net.bridge`, `net.mtu` | `subst_dict()` / config | no |
| `extradevices` | literals plus an allowlisted `channel` and an integer `cid` (`instance.py:2119-2132`) | no |
| `extracommands` | NVMe loop only, from server-built paths (`instance.py:998-1007`) | no |
| `vdi_type`, `spice_concurrent`, `spice_debug` | enum-constrained | no |
| **`net.model`** | netdesc `model`, bare string, no enum or pattern | **yes** (`libvirt.tmpl:139`) |
| **`video_model`** | videospec `model`, bare string | **yes** (`libvirt.tmpl:206`) |

So the answer to the brief's question is: **yes, #4242 is the only instance at
the default mode.** Two refinements:

* **The issue's scope misses a sink.** The plan records #4242 as wanting *"a
  schema `pattern` on both `model` properties **and** XML escaping at render time
  in `Instance._create_domain_xml()`"*. `network[].model` reaches libvirt by a
  second route that `_create_domain_xml()` does not cover: the interface hotplug
  path builds `device_xml` as a plain f-string at
  `shakenfist/instance.py:2837-2843` and hands it to `attachDeviceFlags()` at
  `:2848`. There is no template and no jinja there, so a fix confined to
  `libvirt.tmpl` and `_create_domain_xml()` would leave `POST
  /instances/{ref}/interfaces` open. #4242's remediation note should name
  `instance.py:2837` as well. **Already actioned:** this was posted to
  #4242 as a comment during collection, so 8g must not file it again — the
  issue's recorded scope now covers both sinks.
* **`ET.fromstring()` is not a mitigation.** `shakenfist/instance.py:2177`
  validates the rendered XML and enqueues a delete on `ParseError`. That catches
  *malformed* XML; a `model` value of the form `vga'/><somedevice/><model
  type='vga` produces *well-formed* XML with an extra element inside `<video>`,
  which is the interesting half of the injection. Worth saying on the issue so
  nobody reads the parse check as a defence.

Nothing else in the tree turns a declared string into markup, a shell word, SQL
or a path -- see the confirmed-clean list below.

---

#### S4 (collected as B-2). `logging.md` promises `warn` restores pre-0.8 behaviour exactly; ten sweep rows say otherwise

Merged into **B-2**, which carries both halves of the divergence. This step
reached it from the sweep rather than from the decisions:
`docs/operator_guide/logging.md:185-188` says the request is *"answered
exactly as it always was"*, and `test_nested_sweep.py:49-65` names ten rows
marked `moves at warn` which do not roll back because they are handler guards
rather than schema checks. 8a reached the same sentence from the D25/D34/D42
chain. The divergence is in the safe direction — a 400 where there used to be
a 200 and an interface on an arbitrary network — which is why it is a
documentation finding and not a behavioural one, and it is graded blocking
because it is the sentence an operator reads when deciding whether a rollback
is safe.

#### S5 (advisory). The validation pass is bounded in output and unbounded in input

The plan bounded everything it emits: `MAX_PARAMETER_NAME = 64`
(`validation.py:861`), `MAX_UNKNOWN_PARAMETER_FINDINGS = 20` (`:869`),
`MAX_TYPE_MISMATCH_FINDINGS = 20` (`:881`), and names are stripped of
non-printables before truncation (`:903-904`). It bounded nothing it consumes,
and `validation.py:874-881` says so (*"Nothing bounds a request body's size"*).
Measured on `InstancesEndpoint.post`:

```
disk elements   findings   check() seconds   maxrss
1               1          0.000             110 MB
100             21         0.002             111 MB
10 000          21         0.189             119 MB
100 000         21         2.076             194 MB
undeclared body keys: 200 000 -> 25 findings, 0.103s
```

A 2 MB body of malformed nested elements costs 22 ms to `json.loads` and
**2.08 s** inside `check()` -- roughly 95x amplification, single-threaded, per
gunicorn worker. Authenticated-only (`validate_request` is first in
`method_decorators` and therefore runs *after* `_authenticate_unless_public`,
`base.py:2296-2308`), and the undeclared-key path is cheap, so this is a
misbehaving-client hazard rather than an anonymous one. The model for a fix
already exists in this plan's own diff: `limit_federated_body_size`
(`shakenfist/external_api/app.py:218-229`) refuses an unmeasured or oversized
body before any reader, and it is the only body-size limit in the tree. Worth an
issue for a general cap. Separately and pre-existing: `log_request` parses the
body *before* authentication (`base.py:1689`), so the JSON-parse cost alone is
reachable unauthenticated on every route -- not this plan's, noted so the general
cap is scoped to the right decorator.

---

#### S6 (advisory). The kwarg derivation prevents recurrence inside the package, and is silent outside it

Phase 4's answer to #3739 is `declarations.decorator_kwargs()` /
`_consumed_kwargs()` (`shakenfist/external_api/declarations.py:498-620`), which
AST-walks each handler's decorators for `kwargs.pop('name', ...)` and `del
kwargs['name']`, follows a top-level `return some_function(...)` delegation, and
reports anything undeclared through `audit()`
(`declarations.py:828-840`). `test_parameter_declarations.py` runs it in CI (78
tests, all passing). I mutation-tested the *mechanism* rather than reading it, by
copying `shakenfist/external_api/` to a tempdir and running
`declarations.audit(<copy>)` against mutations:

| Mutation | Result |
|---|---|
| unmutated control | `drifted=0 problems=0` |
| `kwargs.pop('sneaky', None)` added to `arg_is_instance_ref` | **27 problems**, one per affected handler, each naming the parameter and the location it must be declared in |
| `kwargs.pop('sne' + 'aky', None)` added to `arg_is_network_ref` | **1 problem**: *"arg_is_network_ref removes a kwarg named by something this cannot read ('sne' + 'aky'), so a parameter it consumes is missing from the derivation"* |
| a decorator defined **outside** `shakenfist/external_api/` applied to a real handler | **0 problems -- not caught** |

So the class really is closed, not just the 55 instances: the check is keyed on
the pop, not on the name `namespace`, and a non-literal key is reported rather
than silently skipped. The residual is the fourth row, which
`declarations.py:597-607` documents as a deliberate gap (*"The gap this leaves is
a decorator defined outside this package which pops a kwarg. There is none
today"*). I confirmed there is none today -- every ref decorator is in
`base.py` or `artifact.py`. Advisory only: worth a `problems` entry the day a
decorator is imported from outside the package, since the failure is silent and
the consequence is a functional-but-undeclared parameter, which is a 400 for
every working caller the moment enforcement sees it.

---

#### S7 (advisory, out of range). Namespace names are unvalidated and are rendered into a dnsmasq configuration file

Found while walking the string inventory; the declaration is in range (`auth.py`
is one of the 77 files) and the sink is not. `AuthNamespacesEndpoint.post`
declares `namespace` as a bare `'string'`
(`shakenfist/external_api/auth.py:252`) and the handler checks only non-empty and
not-already-existing (`auth.py:263-268`) -- no character set, no length. The name
reaches dnsmasq's conf-file as `domain={{namespace}}.{{zone}}` and
`local=/{{namespace}}.{{zone}}/`
(`shakenfist/deploy/collection/roles/network/files/dhcp.tmpl:29-30`, also `:44`),
via `subst_dict()` at
`shakenfist/managed_executables/managedexecutable.py:144-148`, written to disk at
`:102-104` and loaded by `dnsmasq --conf-file=<that>` at
`shakenfist/managed_executables/dnsmasq.py:312-314`. A newline in a namespace
name therefore injects arbitrary dnsmasq directives on the network node, and
dnsmasq's vocabulary includes `dhcp-script=`.

**Not blocking, and not a privilege escalation:** the route is
`@api_base.caller_is_admin`, so only the `system` namespace can reach it, and
`system` already has full cluster control. But it is worth recording because the
plan's own rationale for leaving namespace strings unconstrained is written down
and is wrong for this one route: `ARGTYPES`'s `namespace` token is documented as
carrying prose format only because *"a ref decorator resolves [it] against the
database and answers 404 -- a stronger check than a format one"*
(`shakenfist/external_api/base.py:307` and `validation.py:33-38`). That is true
of every route that *resolves* a namespace and false of the one that *creates*
one, where nothing resolves anything. Same shape as S1: a decision about one case
applied to a case it does not cover. File separately; it is not this plan's
surface to fix.

The sibling `{{vm.name}}` in `dhcphosts.tmpl:2` and `dnshosts.tmpl:2` is **not** a
finding: instance names go through `validators.hostname()` plus a no-dots and
63-character check at `shakenfist/external_api/instance.py:679-686`, an
unconditional handler guard, so no newline or comma can reach the template.

---

#### Confirmed clean

Recorded one line each so no later step re-derives them.

* **No new execution, SQL or deserialisation sink in any of the twelve ranges.**
  `git diff` over all twelve for added lines matching
  `subprocess.|os.system|shell=True|os.popen|eval\(|exec\(|pickle|sa\.text\(|\.execute\(`
  returns exactly two hits, both `ast.literal_eval` in
  `shakenfist/external_api/declarations.py` operating on the repository's own
  source AST. `ast.literal_eval` executes nothing.
* **The only shell command built inside an API handler refuses a metacharacter at
  every mode, including via the body/path smuggle route.**
  `shakenfist/external_api/network.py:668-670` interpolates `address` into `ip
  netns exec ... ping`. Measured with `util_concurrency.execute` spied and
  `NODE_IS_NETWORK_NODE` forced true: a clean address executes; `10.9.8.5%3B%20id`
  in the path answers 400 `invalid address` at both `enforce` and `off` with no
  call; and a body `{"address": "10.9.8.5; id"}` overwriting the path parameter
  answers 400 `address: a body key of this name overwrote the URL path parameter`
  at `enforce` and 400 `invalid address` at `off`. `ipaddress.ip_address()`
  (`network.py:657-660`) runs first and unconditionally, so the guard holds at
  every mode. (Declaring it `'ipv4'` rather than `'string'` would make the
  published spec say what the server enforces -- a one-token nit for 8c/8d, not a
  security issue.)
* **`D31` holds: one 500 site, bare body, no interpreter text added since.**
  `sf_api.error(500, ...)` occurs exactly once in `shakenfist/external_api/`
  (`base.py:2059`, `'server error'`), the `except TypeError` arm is gone from
  `handle_authorization_exceptions` (`base.py:1866-1888` documents the deletion),
  and `log_request` answers a non-object body directly at `base.py:1718-1731`
  rather than leaning on it. `test_server_error_logging.py` asserts the response
  carries neither `ValueError` nor `PermissionError` while the log line carries
  the class, the traceback and the exception hash. The two `str(e)` responses the
  ranges added are `artifact.validated_max_versions`'s `InvalidMaxVersions`
  (fixed strings, `artifact.py:63,66`) and `MultipleObjects` from
  `_resolve_artifact_ref` (`external_api/artifact.py:83`); neither is interpreter
  text. Flask runs with no debug mode and no `PROPAGATE_EXCEPTIONS`, so a
  serialisation-time failure gets flask_restful's generic body, not a traceback.
* **`MultipleObjects` on the widened artifact lookup leaks no foreign namespace
  names.** `shakenfist/artifact.py:343-345` names the requestor and the ref only,
  and `:271-273` names the caller's own namespace. The widening path
  (`_resolve_artifact_ref(..., widen=True)`) is gated by
  `resolve_lookup_namespace` (`base.py:215-233`), which 404s a non-system caller
  naming another namespace, matching the `requires_*_ownership` posture so
  namespace existence is not disclosed. `test_arg_is_ref_namespace_scoping.py`
  covers it.
* **No credential, token or nonce reaches a response, a log line or an event.**
  `handles_credentials()` (`base.py:97-99`) drops the whole body on every `/auth`
  route in three independent places -- `log_request` (`base.py:1765-1767`),
  `log_request_info` and `log_response_info` (`app.py:237-238, 262-263`) -- and
  the pairing with `log_validation_findings`' parameter-name redaction
  (`app.py:713-718`) is documented as mutually load-bearing at `app.py:196-202`.
  The enforce 400 *does* carry the caller's own parameter name
  (`base.py:2245-2247`); it goes only to the caller who sent it and is dropped
  from the log by the same predicate. `_token_use_event` and
  `_token_request_fields` (`base.py:1225-1242, 1118-1135`) both document that the
  presented token and the key nonce are excluded, and they are.
* **Findings carry types, never values, and names are sanitised.**
  `validation.Finding.__init__` (`validation.py:903-906`) filters
  non-printables and truncates to 64 before the name can reach a log line or a
  response; `value_type` is `type(value).__name__`
  (`validation.py:907`). Marshmallow's own messages never echo the input
  (`_ExactInteger` uses `make_error('invalid')`, whose message is `"Not a valid
  integer."`), so the flattened `detail` cannot carry caller text either.
* **The event log gains almost nothing new, and nothing guest-controlled.** The
  only event writes added across the twelve ranges are
  `_record_refused_token_use` (`base.py:1257-1315`) and `_reject_token`'s audit
  event; both write to the caller's own namespace, and the first redacts the
  parameter name on credential routes. 134 of 142 handlers already carried
  `log_token_use`, so for all but two read routes
  (`ArtifactVersionsEndpoint.get`, `ArtifactOutstandingOperationsEndpoint.get`)
  the refusal event *replaces* an event that would have been written anyway --
  no amplification. (Out of range and worth knowing about separately:
  `managedexecutable._make_config` writes the full `original` and `regenerated`
  configuration text into an AUDIT event at
  `shakenfist/managed_executables/managedexecutable.py:110-116`, which for
  dnsmasq includes guest hostnames and hosted DNS entries. Not in this plan's
  diff.)
* **No filesystem path is built from a request-supplied name, and the one
  unchecked uuid cannot traverse.** Every `os.path.join` in the blob, instance,
  upload and artifact paths uses a server-generated uuid, a `STORAGE_PATH`
  constant, or a code-chosen filename; `external_api/artifact.py:659` uses
  `str(uuid.uuid4())`, and `external_api/upload.py:58-62` uses the resolved
  object's uuid. `Blob.filepath(blob_uuid)` in `external_api/blob.py:216` is
  reached only behind `arg_is_blob_uuid` (`blob.py:81-91`), which requires
  `Blob.from_db()` to hit, so a traversal string 404s before any path is built
  -- at every mode. `LabelEndpoint.post`'s `blob_uuid`, the one declaration
  `validation.py:381-411` records as checked by nothing in the handler, can
  therefore only produce a dangling label index; every filesystem use of a blob
  uuid goes through a `Blob` loaded from the database.
* **No new endpoint, and authentication is structurally the default.** No
  `flask_restful.Resource` subclass and no `add_resource()` call was added in
  any of the twelve ranges (`git diff` grep for `^+class ` / `add_resource`
  across all twelve returns only `Declaration`, `CompiledEndpoint`, `Finding`
  and `_ExactInteger`, none of them endpoints). So nothing added authenticates
  weaker than its siblings, trivially. The standing mechanism is
  `Resource.method_decorators` (`base.py:2296-2308`) with a closed
  `EXPECTED_PUBLIC` set of five enumerated in
  `shakenfist/tests/external_api/test_auth_universal.py:26-39`.
* **`validate_request` fails closed in both directions.** Making it innermost
  means `_authenticate_unless_public` reads `_sf_public` and `_sf_scope` off its
  wrapper; `functools.wraps` carries the function `__dict__` and `__self__` is
  copied by hand (`base.py:2256-2260`). Losing `_sf_public` would make a public
  endpoint *demand* a token; losing `_sf_scope` would fall back to the derived
  scope, and `scope()` exists to *narrow* a derivation
  (`base.py:2100-2118`). Both failure modes are more restrictive, not less.
  Every `AuthenticatedStackTestCase.setUp` proves `_sf_public` survives by
  obtaining a token from `POST /auth` without one.
* **Enum, pattern and allowlist guards that hold at every mode.** Checked
  individually because each is a value the schema also constrains, and a
  handler guard is what makes the constraint survive a rollback:
  `operation_type` against `OPERATION_NAMES_TO_CLASSES`
  (`external_api/clusteroperation.py:123-124`), `target_object_type` through
  `ObjectType()` (`clusteroperation.py:335-339`), `max_versions` through
  `validated_max_versions` (`artifact.py:43-67`), `configdrive` against a
  two-element list (`external_api/instance.py:716-719`), disk `bus` through
  `_get_disk_device` (`instance.py:742-747`), instance `name` through
  `validators.hostname` (`instance.py:679-686`), DNS `name` likewise
  (`network.py:807-810`), agent `mode` through an explicit `isinstance` plus
  `symbolic_to_numeric_permissions` (`instance.py:2051-2062`), `float` through
  `validation.declared_boolean` (`instance.py:474-482`), and `jwks_uri`'s https
  requirement in `_validate_issuer_arguments` (`auth.py:820`). None of these is
  mode-dependent.
* **`API_VALIDATION_MODE` cannot be mistyped into silence.** `Literal['off',
  'warn', 'enforce']` with a default of `'enforce'`
  (`shakenfist/config.py:284-285`), so `Enforce` or `enforced` is a config-load
  failure rather than a silent downgrade.
* **Concurrency and locking:** the ranges add no lock acquisition and no shared
  mutable state. Everything request-scoped rides on `flask.g`
  (`validation.VALIDATION_FINDINGS`, `BODY_PATH_COLLISIONS`, `PARSED_BODY`,
  `base._RECORDED_EXCEPTION_FIELDS`), every setter is wrapped against the
  no-application-context `RuntimeError`, and `validation.REGISTRY` is written
  once by `install()` at import time and read-only thereafter. No deadlock
  surface.

---

#### One observation for whoever reads the sinks next (not a finding here)

`shakenfist/util/image.py:181-201` puts `identify(source).get('backing file')`
-- a string read out of a qcow2 header -- into a `shell=True` command string.
The header of an instance's disk is written by Shaken Fist and is not visible to
the guest, and nothing in the twelve ranges touches this file, so it is neither
this plan's nor reachable from a declared parameter. Recorded only so the next
person walking `util_concurrency.execute()` call sites does not have to rediscover
that it was considered.

## Dispositions

Step 8g, per decision 5 and definition-of-done items 5 and 6. Every
finding recorded above appears here exactly once, with one of three
dispositions: **fixed** on this branch, **filed** as a numbered issue,
or **declined** with a reason. Nothing is left without one.

Issues filed by this step:
[#4248](https://github.com/shakenfist/shakenfist/issues/4248),
[#4249](https://github.com/shakenfist/shakenfist/issues/4249),
[#4250](https://github.com/shakenfist/shakenfist/issues/4250),
[#4251](https://github.com/shakenfist/shakenfist/issues/4251),
[#4252](https://github.com/shakenfist/shakenfist/issues/4252),
[#4253](https://github.com/shakenfist/shakenfist/issues/4253),
[#4254](https://github.com/shakenfist/shakenfist/issues/4254) and
[client-python#401](https://github.com/shakenfist/client-python/issues/401).
Every open issue on this surface was searched first --
`gh issue list --state open` plus four keyword searches -- and the two
findings which already had a home were recorded there rather than
filed again (#4242 and #4167, below).

### The survey findings

| # | Disposition |
|---|---|
| F1 | **Fixed** in the planning commit. The phase 8 row names the twelve ranges instead of `develop`. |
| F2 | **Fixed** in the planning commit. Phase 7's `Merged` cell reads `91312b9a3` (#4232); phase 7's definition-of-done item 14 corrected. |
| F3 | **Fixed** in the planning commit. The phase 2 cell names #3666, #3682 and #3685 and the note under the table says phase 2 landed across three. The five out-of-band merges are context per decision 3, which needs no disposition beyond that decision. |
| F4 | **Fixed** by this step. #4223 is reopened, carries a comment distinguishing the two API routes phase 7 guarded from the lookup function which is still wrong, and no longer carries `automated-fix-attempted`. The conductor behaviour behind it is **declined** here and recorded in Future work: it is a `private-ci` defect, not this repository's. |
| F5 | **Fixed** by this step. `docs/plans/PLAN-api-input-validation.md` gained a *Known defects* subsection naming #4242, #4236 and #4223, each with what it is and why it is not fixed, plus the eight issues filed above. It also records that [#4227](https://github.com/shakenfist/shakenfist/issues/4227) is a second filing of #4236 and that the two should be merged. |
| F6 | **Fixed** by step 8d, all three: the release note's three-item list names its own third item and wraps at the paragraph's width; neither `usage.md` nor `instances.md` gives an incomplete `float` spelling set; and no page claims the ansible module converts spellings it does not. |
| F7 | Nothing to dispose of except the one real finding it contained, the phase 6 plan's two dead SHAs, **fixed** in the planning commit. Every mention of `03ea26514` and `d6b84b365` in every file of this plan now sits in a passage which says they are dead and names `ec406a78a` and `c7a432886` as what replaced them -- corrected in this step, which is what made definition-of-done item 4 pass rather than nearly pass. |

### The four blocking findings

All four are **fixed on this branch**. No blocking finding was
downgraded, and none was declined.

**B-1**, four sites, no behaviour change:

* `shakenfist/config.py` -- the `API_VALIDATION_MODE` description now
  says any finding answers 400 including a missing required parameter,
  and names what the rollback does *not* restore.
* `docs/operator_guide/logging.md` -- both sentences corrected.
* `docs/developer_guide/writing_an_endpoint.md` -- the stale summary
  paragraph rewritten and pointed at the `required`-specific section
  sixty lines below it, so the file no longer contradicts itself.
* `shakenfist/external_api/instance.py` -- the comment on the `name`
  guard now gives the real reason the guard is still needed (`warn` and
  `off` reach it) rather than citing a retired decision.
* `docs/release_notes/v07-v08.md:210` is **deliberately unchanged**, as
  the finding says: it is explicitly historical, dated with "At this
  point in the rollout", and carries a forward pointer to the entry
  which records the change.

**B-2**, fixed in the two documents which were wrong. `logging.md`'s
`warn` bullet now names both classes of request which do not roll back
-- the undeclared body key which becomes a 500 and writes an exception
record per request, and the three handler guards which refuse in every
mode -- and its `off` bullet says the same two exceptions apply.
`writing_an_endpoint.md` gained the paragraph 8a's finding 6 said was
missing rather than only a corrected sentence: what a handler guard is,
which three exist, why they are not rolled back, and how to choose
between writing a check as a guard and writing it as a declaration.

**B-3**, fixed in the master plan. The paragraph now says the guard
tests the value, names the two-part closure in the past tense (the
handler guard covers `warn` and `off`, `network_uuid` being required in
`NETWORKSPEC_SCHEMA` covers `enforce`, because a required compiled
field is built `allow_none=False`), and says what is still wrong. It
does **not** argue that the schema fails to close the reachable path;
that half of 8a's finding 14 was withdrawn at collection and is not
reinstated here or in the #4223 comment.

**B-4**, fixed at the generator rather than at the three confirmed
instances, per this repository's preference for fixing the caller over
papering over at the receiver. Two halves:

* **The reading.** Ten of the nineteen declared booleans were routed
  through `validation.declared_boolean()`: `uefi` and `secure_boot` on
  instance create, `shared` on both artifact routes, `all` and `thin`
  on snapshot, `all` on the instance, network and agent-operation
  listings, and `clean_wait` on delete-all-networks. `confirm` on the
  three delete-all routes was deliberately left reading only a JSON
  `true` -- an identity test on a destructive route refuses a string
  spelling rather than acting on it, which is the safe direction --
  and `provide_dhcp`/`provide_nat`/`provide_dns` were left alone
  because they are coerced downstream and because `declared_boolean`
  would turn their "absent means True" default into False.
* **The test, which is the half that keeps the class closed.**
  `shakenfist/tests/external_api/test_boolean_sweep.py` enumerates
  every `boolean` declaration from `declarations.handlers()` -- the
  same source the published specification is built from -- and fails
  if one has no entry, so a twentieth boolean cannot join the class
  silently. For each it sends four requests (JSON `true`, JSON
  `false`, `'true'`, `'off'`) and requires the string spelling to
  produce the same observable as the JSON boolean marshmallow says it
  means. There is no hand-written expected value in the table, which
  is the point: the pinned-expectation shape is how `net.float.yes`
  passed for years while being wrong. Each row also carries an
  anti-vacuity check -- the two JSON booleans must produce *different*
  observables before any spelling is compared -- so a row whose
  observable cannot see the property fails instead of decorating.
  Mutation evidence is free here: the test was written before the
  fixes and named all ten defective sites on its first run.

  The behaviour change is documented in `v07-v08.md` (it is a
  *reading* rather than a schema check, so it holds in every mode) and
  the rule is now written down for the next endpoint author in
  `writing_an_endpoint.md`. A comment on
  [#4167](https://github.com/shakenfist/shakenfist/issues/4167)
  records the overlap 2b asked about: its item 1 named `uefi` and
  `secure_boot` as null-or-omitted reaching a pydantic
  `ValidationError`, and that half is now closed (measured: both
  answer the scheduler's 507 with no exception recorded), while
  `cpus` and `memory` remain. The same comment records that #4167's
  opening premise -- "`required` is deliberately not enforced" -- is
  itself B-1, and is no longer true.

### 8a, the decision set review

Findings 1-4 are B-1 and B-2; finding 14 is B-3. Findings 18 and 19
are recorded as clean and need no disposition. Every remaining
advisory is **fixed** here, because each is a single stale sentence in
a file this branch already touches and the plan's convention is to
correct a false claim where it lives:

| # | Where | Fix |
|---|---|---|
| 5 | `test_request_validation.py` docstring | "the one thing the rollback does not undo" became "one of the things", with a pointer at `NestedSweepWarnTestCase`. |
| 6 | `writing_an_endpoint.md`, the *What validation does with them* section | The missing handler-guard paragraph, written as part of B-2. |
| 7 | D42's own canonical text | "videospec presence checks" became the `model` and `memory` value tests, with a note that the review changed them. |
| 8 | Phase 7 plan, two places | "the only narrowing an operator cannot roll back" became one of three, naming the other two. |
| 9 | `test_nested_sweep.py` docstring | "two new handler guards" became three, and the following paragraph was folded in as the third class, so the ten rows are 2 + 6 + 2 rather than internally contradictory. |
| 10 | Phase 7 definition-of-done item 9 | "the other four spellings" became five. |
| 11 | Phase 7 Progress item 13 | "seven, in three classes" became ten, with the per-class counts and a pointer to item 7 which had it right. |
| 12 | Phase 7 step 2 brief | The literal `fields.Integer(strict=True)` instruction replaced by what D46's rewrite says, plus a note that applying it literally broke `test_blob_data_bounds` in a minute. A future phase copying this brief as a pattern would have reintroduced the defect. |
| 13 | Phase 7 step 6 brief | Three wrong things in one clause corrected: three narrowings not two, no client version is a release requirement, and the rollback is `warn` *and* `off`. |
| 15 | Phase 0 plan, D6 and D8 | Both gained the *Amended by phase 3* note the master plan's D8 already carried. D6's tuple-location mechanism was tried and rejected before phase 3 began; D8's `passed_uuid` evidence was deleted by D11. |
| 16 | Phase 0 plan, the hand-off list | The `get_args` fold item now records D19's supersession and #4098. |
| 17 | Master plan, the derivation axis table | Gained `location='json_or_query'`, which the shipped generator has and the published copy of the table did not. |

### 8b, wave 1

| Finding | Disposition |
|---|---|
| Ninety triple-single-quoted string literals in two test files, banned by CLAUDE.md and not mechanically enforced | **Fixed.** Mechanically converted after checking that no block contains a `"""` (45 blocks, one containing a single `"`). All 81 tests in the two files pass. |
| Four `print()` calls in a CLI tool script | **Declined**, no defect: a command-line tool's output is what `print()` is for. |
| The database-layer rules (three-layer pattern, SQL pushdown, gRPC conventions) | **Declined**, not applicable: this plan never touches `mariadb.py`, `protos/` or `daemons/database/`. |

### 2a, code quality

| Finding | Disposition |
|---|---|
| `validation.py`'s array branch keeps the `validate` kwarg its three sibling shape branches pop, so a whole-array validator would be applied in a way nothing else in the module does | **Fixed**, as a comment rather than a behaviour change. Popping it would silently discard a constraint somebody meant; the invariant which makes it unreachable (`base._validated_constraints()` refuses bounds and patterns on non-scalar rendered types, and no array-typed fragment carries an enum or format) is now written where the next reader is, with the decision left to whoever renders the first such token. |
| Five copy-pasted `_format_*` wrappers | **Filed** as #4251. A refactor with a test behind it, not a defect. |
| `validation.py:1110-1111`'s "every compiled field is `allow_none=True`" | **Fixed.** Scoped to "an *optional* compiled field" and pointed at the builder. This one is worth noting: the loose wording demonstrably misled one of this audit's own steps into a wrong conclusion about D44, which is as direct a piece of evidence as a comment defect ever gets. |
| The unreachable IDE disk guard at `instance.py:884-886` | **Filed** as #4251. Dead in every mode, not wrong -- the handler still answers the right 400 from the earlier bus check. Deleting it is a small change which wants its own review rather than riding in an audit commit. |
| The guard chain is ordered by accretion, and a fifth phase has no single place to look | **Filed** as #4251. A design observation with a documentation fix, not a defect. |

### 2b, tests

| Finding | Disposition |
|---|---|
| B1 | B-4, **fixed**. |
| B2 -- four handler guards whose tests are all satisfied by the validation layer at `enforce`, leaving the guards with zero executed coverage | **Fixed**, all four. `test_snapshot_max_versions.py`, `test_blob_data_bounds.py`, `test_label_access.py` and a new `ArtifactMaxVersionsTestCase` in `test_artifact_access.py` now run their refusals at `warn` and `off` as well, and assert the guard's *own message* so the answering layer is identified rather than inferred from a status code both layers produce. The artifact versions route had no negative test at all and now has three. |
| B3 -- the pattern dialect refusal and the alternation scanner have no test, and exercising them found `^[a|b]$` falsely refused at import time | **Fixed.** The scanner now tracks `[`/`]` alongside `(`/`)`, because a `|` inside a character class is a literal pipe; a declaration carrying one would have stopped `sf-api` from starting. Ten new cases in `test_parameter_declarations.py`: six accepted patterns in a new `test_an_accepted_pattern_dialect` (which exists because the old comment claimed grouped alternations were fine with nothing behind it) and four Python-only constructs in the refusal table. Mutation-checked: reverting the `in_class` tracking fails the new test. |
| B4 -- the nested sweep is structurally blind to *accepted with the wrong meaning*; two derived differential tests recommended | **Half fixed, half filed.** The string-spelling-equals-boolean differential is `test_boolean_sweep.py`, written here. The null-equals-absent differential is **filed** as #4252, because it is a new test over three schemas rather than a fix to what shipped. |
| B5 -- three review-added videospec guards have no mutation, and `restore()` silently reverts a concurrent edit | **Fixed.** `tools/mutate-nested-sweep.sh` now runs 19 mutations with 0 survivors, the three new ones covering the `memory` null guard, the `vdi` defaulting (which proves `test_a_null_video_key_is_never_stored` is not vacuous) and the non-mapping shape guard. The header now warns that the restore is unsafe against a *sibling's* edit rather than claiming uncommitted work is safe, and the "two handler guards" reference became three. |
| B6 -- no functional coverage for `warn` or `off` | **Filed** as #4253. It needs a cluster CI case which restarts `sf-api`, which an audit branch should not be writing. Its precondition is disposed of below. |
| The `test_openapi_spec.py:113` citation of `instance.py:833` | **Fixed**, now `:916-917`. |

### 2c, documentation

| Finding | Disposition |
|---|---|
| F6.1, F6.2, F6.3 | **Fixed** by step 8d. |
| New-1 | B-1, **fixed**. |
| New-2 -- two "phase 6" references in a `developer_guide` file, which the shared block calls a plan smell | **Fixed** rather than folded into #3732, which is closed. Both sentences were reworded to state the fact without naming the phase; the file now contains no phase reference at all. |
| New-3 -- a narrow mechanical check for the error contract's fixed facts | **Filed** as #4254, with the recommendation intact: pin the wire-format string and add a grep tied to what `test_required_sweep.py` already proves, and explicitly do *not* build narrative parity across the four documents. |
| New-4 -- `sf-client` reads only the literal `true`/`True` in `-N ...,float=` | **Filed** as client-python#401, and cross-referenced to client-python#398, which is the same family (the CLI's ad-hoc `key=value` parsing producing a value the server reads differently) on a different key. It cannot be fixed in this repository. |

### 2d, security

| Finding | Disposition |
|---|---|
| S1 | B-1, **fixed**. |
| S2 -- the DNS `value` and `disk[].size`'s `minimum: 0` are guarded only by the schema | **Filed** as #4248, and the reason is worth stating because the alternative was tempting. Both fixes are three lines and the plan has a worked precedent (`artifact.validated_max_versions()`). But a new handler guard is new validation behaviour and a narrowing an operator cannot roll back, which this phase's Scope puts out of bounds -- the same reason #4242 is out of bounds for it -- and each wants functional coverage. Taking them here would have been the audit quietly extending the plan it was auditing. The issue names the precedent to copy. |
| S3 -- #4242 is the only `enforce`-mode raw-XML sink, and its recorded scope missed the hotplug f-string | **No action, already recorded.** The second sink and the note that `ET.fromstring()` is not a mitigation were posted to #4242 during collection; #4242 itself is out of scope per Scope, and the *Known defects* subsection now carries it with both halves. |
| S4 | B-2, **fixed**. |
| S5 -- the validation pass is bounded in output and unbounded in input (95x amplification, 2.08 s for a 2 MB body) | **Filed** as #4249. Authenticated-only, and a general body cap belongs where the body is first read rather than where it is validated -- the issue says why, and names `limit_federated_body_size` as the model. |
| S6 -- the kwarg derivation cannot see a decorator defined outside the package | **Declined**, with the reason: `declarations.py:597-607` already documents it as a deliberate gap, the audit confirmed there is no such decorator today (every ref decorator is in `base.py` or `artifact.py`), and the derivation already reports a non-literal `pop` key rather than skipping it silently -- which 2d proved by mutation. The residual is a `problems` entry owed the day a decorator is imported from outside the package, and there is nothing to fix until then. |
| S7 -- namespace names are unvalidated on create and reach a dnsmasq configuration file | **Filed** as #4250. Admin-only, so not an escalation; filed because the recorded reason for leaving namespace strings unconstrained ("a ref decorator resolves it and answers 404") is true of every route which resolves a namespace and false of the one which creates one. |
| The confirmed-clean list | No disposition owed; recorded so nothing re-derives it. |

### The flagged precondition, verified

2b flagged rather than filed the claim that `API_VALIDATION_MODE`
"cannot be set through the supported deployment path". Verified
independently by this step, and it is half right:

* The facts hold. `grep -rn API_VALIDATION_MODE
  shakenfist/deploy/collection/` returns nothing, and
  `roles/node/templates/config` is 89 lines of explicitly enumerated
  `SHAKENFIST_*` assignments with no generic passthrough.
* The conclusion does not. `API_VALIDATION_MODE` is a declared
  `SFConfig` field, so `sf-ctl set-config API_VALIDATION_MODE warn`
  writes a `cluster_config` row, `_exportable_cluster_config_key()`
  returns True for it, and `load_cluster_config()` exports it as
  `SHAKENFIST_API_VALIDATION_MODE` into every daemon's environment at
  process start. That is the same mechanism `KERBSIDE_URL` and
  `AUTH_SECRET_SEED` use and it is documented elsewhere in the
  operator guide.

So it is a **documentation defect, fixed here**: neither
`v07-v08.md` nor `logging.md` said *how* to set the mode, and an
operator reaching for the rollback during an incident would have looked
for an ansible variable which does not exist. Both now say
`sf-ctl set-config API_VALIDATION_MODE warn`, that `sf-api` must be
restarted because the value is read at process start, and that
`sf-ctl unset-config` puts it back. No collection change is owed, and
that is recorded here so a later reader does not add a redundant
template line.

### Two things noted rather than disposed of

* [#4100](https://github.com/shakenfist/shakenfist/issues/4100) ("an
  explicit `thin: false` on snapshot is indistinguishable from omitting
  it") is unaffected by B-4's fix and remains open and correct.
  `declared_boolean('false')` is `False`, which still falls through to
  `SNAPSHOTS_DEFAULT_TO_THIN` exactly as an omission does; the blocker
  there is the shipped client, as that issue says.
* The three Future work items already recorded below are **declined**
  here by scope rather than left undisposed: two are changes to
  `PLAN-TEMPLATE.md` and one is a `private-ci` investigation.

## The audit's result

The audit did not find nothing, so the one sentence the master plan's
phase 8 row asks for is not available. What it found, and what that
means about the plan as shipped:

**Four blocking findings. None is a vulnerability, and three of the
four change no behaviour at all.** Those three are the same defect
wearing different clothes: a decision was changed in one phase
and the sentences reasoning from it were left standing in another.
`missing-required` went from exempt to enforced when phase 6 deleted
the filter, and four places still said it was recorded and never
enforced -- including `shakenfist/config.py`'s own description of
`API_VALIDATION_MODE`, which is the operator's rendered account of what
a security control does, and including a file which contradicted itself
sixty lines later. `warn` was documented as answering a request
"exactly as it always was" while two separate later decisions made that
false in two directions. And the master plan's front-page record of a
live, open defect described a guard phase 7 had rewritten. The fourth
blocking finding is behavioural, and it is wrong at the shipped default
mode rather than only under the rollback: the API declares nineteen boolean
parameters, `validation.declared_boolean()` was applied to one of them,
and ten of the rest read a string spelling with the opposite of its
published meaning -- `{"uefi": "false"}` booted with UEFI,
`{"secure_boot": "off"}` enabled secure boot *and* defeated the
`secure_boot and not uefi` refusal because both operands were truthy
strings, and a `system` operator sending `{"shared": "false"}` got an
artifact shared with every namespace.

**What that says about the plan as shipped.** The mechanism is sound
and the enforcement is right: twelve pull requests, 24,702 insertions,
and the audit found no request the server answers incorrectly at
`enforce` except through the declared-boolean reading, which is a
handler defect rather than a defect in the layer. Every one of 2d's
confirmed-clean checks held -- no new execution, SQL or deserialisation
sink; no credential, token or nonce in a response, a log line or an
event; no filesystem path built from a caller-supplied name; findings
which carry types and never values; a validation decorator which fails
closed in both directions. The compiler is 294 statements with one
uncovered. What the plan did *not* get right is the part no phase could
review for itself. Five of 8a's nineteen findings, three of the four
blocking ones, and the whole reason this phase exists are the same
shape: a statement written in phase 4 or 5 about a decision phase 6 or
7 changed. The release note is correct in every single case, because
each phase appended to it chronologically and a dated claim about a
past state stays true. Every in-place statement of the contract rotted
-- two reference documents, one pydantic `Field` description and one
code comment -- because each phase edited the paragraph it was thinking
about. That is the durable lesson, and it is worth more than the four
fixes: **a long plan should write its contract once, chronologically,
and point every reference document at it.**

**And the test surface was strong but the wrong shape in one specific
way.** The nested sweep is 104 rows over three modes with a mutation
script behind it, which is unusually good; its observable is
`(status, exception, message)` and 35 of its 43 accepted rows resolve
to a scheduler 507, so it is structurally blind to *accepted with the
wrong meaning* -- which is the class of both defects the phase 7 review
found and of the ten this audit found. The fix, applied here, is a
derived differential: enumerate the property from the source and
compare two requests to each other rather than to a hand-written
expected value. `test_boolean_sweep.py` is that, it found all ten
defects on its first run, and the remaining half of the same idea is
filed as #4252. Four handler guards also had tests which the
validation layer was answering first, so the guards -- the only defence
under the rollback -- had no executed coverage at all; all four now run
at `warn` and `off` and assert the guard's own message.

## Definition of done, audited

Item by item, the way phase 7's step 8 did it. Every verdict below was
checked against the tree rather than recalled.

1. **Met.** Wave 1 and wave 2 were each run against all twelve ranges
   of decision 1, and the *Wave 1* and *2a* sections record what was
   run per range rather than pooling it. Wave 1's style greps are
   reported per range, and 2a's mechanical sweep likewise. The four
   judgment sections pool deliberately -- 2d says why in its opening
   paragraph, and it is the right choice there: every one of its
   findings arises from a statement or a guard which crosses phase
   boundaries, so a per-range presentation would scatter one finding
   across four ranges. Item 1's requirement is that a finding can be
   traced back to a range, and each one names the merge it came from
   where that is a fact about the finding (B-1's site 1 names range 5
   for its introduction and range 9 for its rewording).
2. **Met.** The phase 2 cell names #3666, #3682 and #3685; the phase 7
   cell names `91312b9a3` (#4232); the note under the table says phase
   2 landed across three and says what #3682 was.
   `grep -c '#3682' docs/plans/PLAN-api-input-validation*.md` is 1 in
   the master plan and 6 in this file, not zero.
3. **Met.** The phase 8 row says "over the twelve merges in the
   `Merged` column above" and states why `develop...HEAD` is the wrong
   range, citing `PUSH-AUDIT.md:23-51`.
4. **Met, and it took a fix in this step to get there.** The script in
   the item was run from the repository root:

   ```
   docs/plans/PLAN-api-input-validation-phase-06-required.md 03ea26514
   docs/plans/PLAN-api-input-validation-phase-06-required.md d6b84b365
   docs/plans/PLAN-api-input-validation-phase-08-push-audit.md 03ea26514  (x5)
   docs/plans/PLAN-api-input-validation-phase-08-push-audit.md d6b84b365  (x5)
   docs/plans/PLAN-api-input-validation.md 03ea26514
   docs/plans/PLAN-api-input-validation.md d6b84b365
   ```

   Every line names one of the two permitted SHAs and no other, which
   is the item's test. One of this file's five mentions of each is the
   definition-of-done item itself, so the count moves with the prose;
   the test is the absence of a third SHA, not the multiplicity.

   The second half of the item -- that every mention sits inside a
   passage which says they are dead and names `ec406a78a` and
   `c7a432886` as the replacements -- was **not** true when this step
   started: this file's own three prose mentions said
   the objects resolve to nothing without naming what replaced them.
   Fixed here. The item is the reason that was noticed, which is a
   point in favour of writing a definition-of-done item as a runnable
   script and then actually running it.
5. **Met.** See *Dispositions* above: every finding in this file, from
   F1 through 2d's S7, carries fixed, filed or declined, and the eight
   issues filed are listed by number.
6. **Met.** All four blocking findings are fixed on this branch. None
   was declined and none was downgraded to advisory, which the phase's
   own risk section asked to be checked.
7. **Met.** #4223 is open, carries the comment distinguishing the two
   guarded API routes from the still-wrong lookup function and quoting
   phase 7's own commit, and carries no labels at all -- so
   `automated-fix-attempted` is gone and the issue-fix workflow can
   take it.
8. **Met.** `docs/plans/PLAN-api-input-validation.md` has a *Known
   defects* subsection naming #4242, #4236 and #4223 with what each is
   and why it is not fixed, plus the eight filed here and the note
   that #4227 duplicates #4236.
9. **Met** by step 8d, verified here: `grep -n "float" docs/user_guide/usage.md`
   and `docs/developer_guide/api_reference/instances.md` show both now
   say a JSON boolean is the expected form and that the string
   acceptance is narrower than it looks; the release note names the
   null `network_uuid` case directly instead of by ordinal and wraps at
   63-70 characters; and `usage.md` states `sf-client`'s actual
   literal-`true`/`True` behaviour and names the ansible divergence.
10. **Met.** The Execution table reads `Complete` for phase 8 and
    `docs/plans/index.md` line 111 reads `Complete`, `9 of 9`.
    `python3 tools/check-plan-status.py` agrees, which it did not
    before the master plan's row was flipped -- it caught the
    half-applied edit that left the index ahead of the plan.
11. **Met.** `python3 tools/check-plan-status.py` prints "Plan
    statuses, index arithmetic and phase links agree", and
    `pre-commit run --all-files` passes every hook. `tox` (`py3`,
    `flake8`, `cover`) passes with no failures, including the new
    `test_boolean_sweep.py` and the four new mode-crossing guard
    classes. `bash tools/mutate-nested-sweep.sh` reports 19 mutations,
    0 survivors.
12. **Not applicable, and deliberately so.** The item is conditional on
    the audit finding nothing blocking. It found four. *The audit's
    result* above says what they were and what they mean about the plan
    as shipped, which is what the master plan's phase 8 row asks for in
    the case that actually obtained.

## Future work

* **A `Merged` cell that can only be filled after merge will be blank at
  merge.** It has now been filled in retrospectively twice, by #4222 for
  phase 6 and by this phase for phase 7, and in both cases the phase's
  own definition of done recorded that it could not do it.

  **This has since been settled, and not the way this section first
  proposed it.** The original text here said the task belongs to
  whoever merges the pull request. It does not, and that would not
  have worked: the merger has no reason to be holding the plan open.
  The `plan-phase-landing` shared block -- canonical in
  `shakenfist/development` at
  `templates/shared-blocks/plan-phase-landing.md`, landed by
  [development#145](https://github.com/shakenfist/development/pull/145)
  -- puts the close-out in **the first commit of the next phase**.
  That is the only ordering which both knows the merge commit and
  records it without spending a pull request and a CI run on prose:
  by the time the next phase branches, the previous one has merged.
  The `next-phase` skill carries it as its step 5.
  `PLAN-transient-capacity-refusals` phase 4 was closed out the old
  way, in its own pull request (#4265), some six hours before
  development#145 merged.

  **This phase's own cell is deliberately left as `—`, and stays
  that way.** Phase 8 is `Complete` and its `Merged` cell is empty,
  because the rule under the Execution table is that every SHA there is
  a merge commit read off the first-parent history, which does not
  exist until this pull request merges. The shared block's second
  rule now ratifies exactly this: the push-audit phase is the last
  row of every plan, no next phase will carry its close-out, and it
  is *the only row permitted to omit a `Merged` cell* -- the column
  exists so the push-audit phase can reconstruct what to audit, and
  nothing ever reads its own row. A follow-up pull request is opened
  only where the audit's findings need a carrier; this audit's were
  fixed inside this pull request, so there is none, and nobody fills
  this cell in later.

* **The block that settles the point has not reached this repository
  yet.** `plan-phase-landing` is now in `PLAN_TEMPLATE_BLOCKS` in
  `shakenfist/development`'s `scripts/audit/checks/plans.py`, so the
  consistency audit requires it, but this repository's
  `PLAN-TEMPLATE.md` carries nine shared blocks and not that one.
  Tracked as
  [#4299](https://github.com/shakenfist/shakenfist/issues/4299)
  (`Consistency: Plan template`). Until it lands, a phase plan
  written here is working from a template which still does not say
  how a phase is closed out -- which is how the three retrospective
  fills above happened.
* **The phase 8 row's range was wrong here and in the agent operation
  deadlines plan.** Two independent discoveries of the same defect in two
  plans suggests the row is copied from a template written before
  `PUSH-AUDIT.md` gained its range rule. Worth fixing in the template so
  there is not a third.
* **A false claim about how a plan's own control is deployed survived
  seven phases.** The release note told an operator to set
  `API_VALIDATION_MODE=warn` as the rollback and never said how, and
  the setting appears nowhere in the ansible collection -- so the
  natural search for it fails. It *is* settable, through
  `sf-ctl set-config`, and both documents now say so. The general
  lesson is worth a line somewhere: a plan which introduces an
  operator-facing control owes the *mechanism* for setting it, not
  just its name and its values, and the two are written in different
  files by different phases.
* **`sfconductor` closed #4223 on merge without being asked** (F4). The
  pull request body named only `Fixes #3612`, and no commit in the range
  carried a closing keyword. Whatever heuristic closed it can close any
  issue a branch merely discusses, which is a way to lose a defect
  silently. Worth investigating in `private-ci`.
