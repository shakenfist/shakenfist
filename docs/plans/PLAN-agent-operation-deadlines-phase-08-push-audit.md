# PLAN: Agent operation deadlines phase 8 -- push audit

Planning effort: medium. Review effort: high.

## Why this phase exists

Every phase of
[PLAN-agent-operation-deadlines.md](PLAN-agent-operation-deadlines.md)
has merged. Each was reviewed on its own branch, against its own
diff, by a reviewer who had the phase plan in front of them and not
the seven other phases. Nobody has read this plan's work as one body
of code.

That matters more here than it does for a plan whose phases are
independent. This plan built a single mechanism across seven merges:
an intent recorded by the API server (phase 3) is stored on a static
column (phase 2), read by an executor (phase 4), consumed by a retry
loop and a reaper (phase 5), populated by a client (phase 6) and
documented for operators (phase 7). A defect in that chain lives in
the joins between phases, which is precisely what a per-phase review
cannot see. Three post-merge defect fixes (decision 2) are evidence
that the joins have already leaked twice.

`PUSH-AUDIT.md` is the repository's audit template. It is normally a
pre-push gate run against `develop...HEAD`. Here it runs
retrospectively, which changes the baseline but not the questions.
The precedent is
[PLAN-queue-performance-phase-08-push-audit.md](PLAN-queue-performance-phase-08-push-audit.md).

## Scope

**In scope.** Every change this plan made to this repository, audited
under the `PUSH-AUDIT.md` headings: wave 1 mechanical checks, and
wave 2's code quality, test coverage, documentation and security
reviews. The baseline is pinned in decision 1.

**In scope.** The three defect fixes which landed against this plan's
code outside its phase branches (decision 2). They are part of what
is on `develop` today; auditing the phase branches alone would audit
a version of the code that no longer exists.

**In scope, read-only.** The client half of the deadline contract in
`shakenfist/client-python` (decision 3). Findings there are filed as
client-python issues; no code in that repository is changed by this
phase.

**In scope.** Recording the disposition of the two open defects this
plan caused and never named -- #3995 and #4039 (decision 6). The plan
is not complete while defects it introduced are absent from it.

**Out of scope.** Fixing anything the audit finds, unless it is
trivial or blocking. A review phase records and files; it does not
expand into the work it discovers. Blocking findings are fixed here,
because a blocking finding is by definition not something to leave on
`develop`.

**Out of scope.** Retuning `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT`
(decision 7). #3995 says the next step is a measurement, not a
constant change, and the audit agrees with it.

**Out of scope.** The two merges which touched
`shakenfist/operations/agentoperation.py` incidentally as part of
other work: #3902 `117a155a3` (error messages on the state row) and
#3925 `130cb2936` (`from_db()` miss logging). Both are baseobject
changes that happened to reach one line of this plan's file. They
belong to whoever plans their audit.

## What the survey found

The master plan's phase 8 row was written on 2026-08-14, before any
phase executed. Six findings; the first is a completion problem, the
rest are planning inputs. Corrections are applied at source -- see
*Corrections applied at source* below.

### F1. Phase 7 is complete in substance but was never closed out

Step 7f of
[PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md](PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md)
never ran. The phase plan's *What remains* section says the branch
"has not been pushed yet" and that the sole outstanding Definition of
done item is a green CI run including the smoke suite. Both statements
are now false:

* PR #4053 merged as `4a122bcd3` on 2026-09-04.
* Run
  [33802668130](https://github.com/shakenfist/shakenfist/actions/runs/33802668130)
  passed `Smoke tests (collection)`, and all four new tests appear in
  its results rather than being skipped:
  `test_default_deadline_is_published` (166.69s),
  `test_expiry_frees_the_executor_slot` (176.77s),
  `test_silent_execute_survives_the_progress_timeout` (227.14s) and
  `test_queued_operation_expires_on_its_deadline` (163.33s), each
  `... ok`.

Every other Definition of done item was independently re-checked
against `develop` during this survey and holds:
`grep -rn "getattr(apiclient" shakenfist/deploy/shakenfist_ci/`
returns nothing; `AGENT_OPERATION_FAILURES` in
`shakenfist/deploy/shakenfist_ci/base.py` names exactly
`AgentCommandError`, `AgentOperationFailed` and `AgentAwaitTimeout`;
`docs/operator_guide/agent_operations.md` exists and is in
`mkdocs.yml` at line 502;
`shakenfist/deploy/shakenfist_ci/smoke_ci_tests/test_agentop_deadlines.py`
contains four `def test_`.

So the work is done and the bookkeeping is not: the master plan's
Execution table still reads `In progress` for phase 7, and
`docs/plans/index.md` line 110 still reads `7 of 9`. Step 8a folds
that closeout in (decision 8), because leaving it would put two
phases In Progress in one table.

### F2. The phase 8 row describes a diff that cannot be taken

The row says the audit "runs `PUSH-AUDIT.md` over the accumulated diff
of every phase in this plan against `develop`". Every phase is merged,
so `git diff develop...HEAD` -- which is what every command in
`PUSH-AUDIT.md` is written against -- is empty, and each check would
report success against nothing. Decision 1 replaces it with an
explicit merge list. Corrected at source.

### F3. The plan's footprint is nine merges, not seven

Phase 6 landed in *two* pull requests in this repository, neither of
which is the phase-6 plan file (that lives in `client-python`):

* #4005 `4afa29476` -- the capability token in
  `shakenfist/external_api/app.py`, which phase 3 did not add and
  without which a client cannot safely send the new parameters.
* #4015 `864608276` -- the CI suite's use of the new client.

The master plan's phase list has one row for phase 6 and names
neither. Decision 1's table names all nine. Corrected at source.

### F4. Three defect fixes landed outside the phase branches

None is in any phase's diff, and each changes code this plan wrote:

| Merge | PR | Issue | What it fixed |
|-------|----|-------|---------------|
| `2341ae0c4` | #3933 | #3931 | Sidechannel executors were shut down through the monitors path; phase 5 separated the abort files and exposed a `KeyError` on executor teardown |
| `2e19bb1ea` | #3970 | -- | `SideChannelExecutorJob` took its working queue by reference from `agentop.commands`, so `pop(0)` drained the operation and `operation_is_retryable()`'s empty-list guard fired -- turning phase 5's retry into a permanent expiry. Also added a coding rule to `docs/developer_guide/coding_rules.md` |
| `91d565a05` | #4025 | #4014 | The executor re-resolved a NULL deadline's anchor on every budget check, and the CI harness's own pacing was being read as cluster polling |

Two of the three are defects in the retry and enforcement machinery
phases 4 and 5 built, found by CI within days of merge. That is the
strongest available argument for where wave 2 should spend its
attention: the executor's command-list ownership, its shutdown path,
and its per-check database reads.

### F5. Two open defects caused by this plan are named nowhere in it

* **#3995** -- `test_instance_put_and_get_blob` expired a legitimate
  471 MiB `get-file`: the agent answered the stat in 20 ms and then
  sent nothing for 30 s, exhausting
  `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT`. The issue's own analysis
  is that this is the *only* large `get-file` in CI, so the 30 s
  window has one data point and that data point is a failure. Labelled
  `automated-fix-attempted`; open.
* **#4039** -- `test_no_unbudgeted_fixed_rate_database_polling` saw
  `(GetInstanceAttributes, sidechannel)` at 1.716/s against a 1.486/s
  ceiling. Same load surface as #4014, which `91d565a05` fixed for
  three other pairs; open.

Neither appears in the master plan or in any phase plan. `grep -n
"3995\|4039" docs/plans/PLAN-agent-operation-deadlines*.md` returns
nothing. Decision 6 makes recording them a deliverable of this phase.

### F6. What is not a finding

Worth stating, because each of these is a heading `PUSH-AUDIT.md`
would otherwise send an agent to check from scratch:

* **No SQL pushdown violation exists to find.** Across all twelve
  merges in decision 1's tables, `git diff <M>^1 <M> -- '*.py' |
  grep -E '^\+[^+].*mariadb\.get_all_'` returns nothing. The blocking
  pushdown rule has no candidate in this diff.
* **No cached FK list was added.**
  `shakenfist/schema/agentoperation_attributes.py` gained
  `last_progress: Optional[float]` and `attempts: int` -- two scalars.
  The blocking cached-FK-list rule has no candidate either.
* **`docs/developer_guide/state_machine.md` matches the code.** Its
  *Agent Operations* section (line 19) documents `expired` at line 34
  and carries all five `--> expired` edges plus the
  `executing --> queued` retry edge, which is exactly
  `AgentOperation.state_targets` at
  `shakenfist/operations/agentoperation.py:56-75`.
* **The plan is already compliant with consistency issue #4063.**
  That audit requires a master plan to end with a push audit phase;
  this one does, and the issue's findings name three other plans, not
  this one.

Wave 2's briefs below say so explicitly, so those headings are
confirmed rather than re-derived.

## Decisions

1. **The audit baseline is this plan's merge list, not
   `develop...HEAD`.** For each merge `M` below the diff is
   `git diff M^1 M`. Unlike the queue-performance audit, no
   file-level restriction is needed: with two exceptions named in
   *Scope*, every one of these merges is wholly this plan's work.

   | Phase | PR | Merge | Content |
   |-------|----|-------|---------|
   | master plan + 0 | #3761 | `87bbffcf4` | Documentation only |
   | 1 | #3773 | `f21d5da3a` | Field mask, handler classes |
   | 2 | #3858 | `cb9e10bba` | Schema |
   | 3 | #3883 | `08807c83f` | API parameters, config defaults |
   | 4 | #3898 | `291054e98` | Enforcement, the `expired` state |
   | 5 | #3941 | `185de6b32` | Retry, reaper |
   | 6 | #4005 | `4afa29476` | Capability token |
   | 6 | #4015 | `864608276` | CI suite adoption |
   | 7 | #4053 | `4a122bcd3` | Documentation and CI coverage |

   The Python and proto footprint is roughly 7,300 insertions across
   42 non-generated files;
   `shakenfist/protos/database_pb2.py` and `.pyi` are generated and
   are checked for freshness (wave 1) rather than read.

2. **The three out-of-branch defect fixes are in the baseline.**
   `2341ae0c4` (#3933), `2e19bb1ea` (#3970) and `91d565a05` (#4025),
   as F4 describes. The argument for including them is that the audit
   asks "is the code on `develop` correct?", and the code on
   `develop` includes them. The argument against -- that they are
   somebody else's pull requests -- fails because each exists only to
   repair this plan's work, and each changes a file no other plan
   owns.

3. **The client half of the contract is read, not changed.** Phase 6
   landed in `shakenfist/client-python` as PR #380 (`4557100`), and
   the deadline contract spans both repositories: the server publishes
   a bound and a default, the client populates the parameter from its
   own await timeout. A client that sends a value the server rejects
   is a defect in neither half read alone. Step 8g therefore reads
   `git diff 4557100^1 4557100` against the server's published
   contract. Nothing in that repository is edited from this phase;
   findings become client-python issues.

   This is the decision most likely to be argued with, and the
   counter-argument is real: a repository's audit should stop at its
   own boundary, and phase 6 already had its own review. The reason
   to override it is that the boundary is exactly where the defect
   class lives, and no other reviewer sits on both sides of it. PR
   #369 (`5358f25`, "await-deadline") in that repository is *not* in
   scope: it bounds instance creation, not agent operations, and
   predates phase 6 by a fortnight.

4. **Wave 2's judgment work runs as four parallel sub-agents, as
   `PUSH-AUDIT.md` specifies.** This departs from the
   queue-performance audit, which ran inline because the operator had
   asked for no sub-agents in that session. There is no such
   constraint here, `CLAUDE.md`'s *Following plan files* section
   authorises a plan step to fan work out, and the accumulated diff
   is larger than one session should read serially without losing
   resolution. Steps 8c to 8f are independent and are launched
   together.

5. **Wave 1's exit condition is relaxed in one specific way.** The
   template says to stop if `pre-commit` or `tox` fails. Those run
   against the working tree, which is `develop` plus this phase's
   documentation, so a failure would be a pre-existing failure on
   `develop` rather than something this plan introduced. If wave 1
   fails, record it, check whether this plan's diff is implicated,
   and continue to wave 2 rather than stopping.

6. **#3995 and #4039 get a written disposition in the master plan,
   whatever the audit concludes about them.** They were caused by
   this plan and are recorded nowhere in it. Recording them is a
   deliverable, not a finding: a reader who arrives at a Complete
   master plan should not have to search the issue tracker to learn
   that its mechanism has two known open defects.

7. **The audit does not retune the progress timeout.** #3995 asks for
   a measurement of what a guest actually does between receiving a
   `GetFileRequest` for a 471 MiB file and emitting its first chunk,
   and explicitly warns that a blanket larger timeout would slacken
   the #3516 wedge detection the window exists for. The audit's job
   is to grade the defect, not to tune against a baseline that #3970
   has just moved. Expected grade is advisory: the failure mode is a
   safe expiry with an accurate state message, not the wedge #3516
   described, and the operation's executor slot is released.

8. **Phase 7's closeout is folded into step 8a.** F1 established that
   the work and its gating CI run are both done. Step 8a therefore
   sets phase 7 to Complete, ticks the last Definition of done box in
   its plan with the run link, corrects its now-false *What remains*
   section, and moves `docs/plans/index.md` to `8 of 9` -- then sets
   phase 8 In Progress in the same commit. The alternative, a
   separate closeout pull request, buys nothing: the two commits
   would touch the same three files an hour apart.

9. **A clean heading is a result.** If a wave 2 heading finds nothing,
   it says so in one sentence alongside the list of what it actually
   examined. An audit reporting nothing under every heading is
   recorded as such rather than padded.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | low | opus | none | **Close phase 7 and open phase 8.** In `docs/plans/PLAN-agent-operation-deadlines.md`: set phase 7's status to `Complete`, set phase 8's to `In progress` and link this file from its Plan cell, and correct the phase 8 row's content per F2 and F3 (name the merge list rather than "the accumulated diff against `develop`"; note phase 6 landed in two server-repo pull requests). In `docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md`: tick the final Definition of done box, citing run 33802668130 and the four test names and durations from F1, and rewrite *What remains* -- it currently says the branch has not been pushed, which is false. In `docs/plans/index.md` line 110: `7 of 9` becomes `8 of 9`, status stays `In progress`. Do not touch `docs/plans/order.yml`. Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: "Plan agent operation deadlines phase 8." |
| 8b | medium | sonnet | none | **Wave 1, mechanical.** Run `pre-commit run --all-files` and `tox`, recording output rather than asserting a result; per decision 5, a failure here is presumed pre-existing on `develop` and does not stop the phase, but must be checked against this plan's file list before it is dismissed. `protos/database.proto` is in the baseline (phases 1 and 2), so also run `tox -e genprotos` and `git diff --exit-code shakenfist/protos` to confirm the checked-in stubs are fresh. Then run `PUSH-AUDIT.md`'s four style greps against each merge in decisions 1 and 2 -- `git diff M^1 M -- '*.py' \| grep -nE ...` for over-120-character lines, stray `print(`, new `etcd` references and untagged `mariadb.get_all_*` -- and record the output. F6 predicts the pushdown grep is empty across all twelve; confirm that rather than assuming it. Finally run the style-conformance judgment brief from `PUSH-AUDIT.md` over the same ranges. Write the results into this plan under a *Wave 1* heading. Commit subject: "Run the agent operation deadlines wave 1 audit." |
| 8c | medium | sonnet | none | **Wave 2 mechanical sweep plus 2a, code quality.** Run `PUSH-AUDIT.md`'s wave 2 mechanical sweep (TODO/FIXME/HACK/XXX, new `# noqa` / `# type: ignore` / `pragma: no cover`, new-test ratio, documentation files touched, new `subprocess.` / `os.system` / `shell=True`) against every merge in decisions 1 and 2, then work the 2a brief. Front-loaded research, so do not re-derive it: F6 establishes that the blocking SQL-pushdown rule and the blocking cached-FK-list rule both have **zero** candidates in this diff -- confirm each in one line and move on. Spend the effort instead on the areas F4 says have already leaked: `SideChannelExecutorJob` in `shakenfist/daemons/sidechannel/main.py`, which phases 1, 4 and 5 rewrote and which #3933 and #3970 then repaired. Specifically: is the command list still owned rather than aliased after `2e19bb1ea` (see the rule it added to `docs/developer_guide/coding_rules.md`); does the executor's teardown path hold together across the separated abort files; and are the deadline, progress-timeout, attempt-bound and reaper checks four expressions of one policy or four independently drifting copies. The `mariadb.py` additions from phases 1 and 2 (`update_agent_operation_attributes`'s field mask, the deadline columns) need the three-layer direct/gRPC/public check with a registered Monitor counter in `shakenfist/daemons/database/main.py`. Apply the comment-proportion shared block. Grade each finding blocking or advisory. Commit subject: "Audit agent operation deadline code quality." |
| 8d | medium | sonnet | none | **Wave 2b, test review.** Work the `PUSH-AUDIT.md` 2b brief over the decision 1 and 2 merges. The plan added six test modules -- `test_agent_operation_expiry.py`, `test_agent_operation_retry.py`, `test_daemon_sidechannel_shutdown.py`, `test_mariadb_agent_operations.py`, `test_mariadb_agent_operations_live.py`, `test_instance_static_value_ownership.py` -- and grew `test_daemon_sidechannel_executor.py` by about 1,800 lines, so the question is not "is there coverage" but "does the coverage assert behaviour or implementation". Two specific questions the phases could not ask themselves. First: #3970's aliasing defect and #3931's shutdown `KeyError` both reached `develop` through phases that had extensive unit tests -- read the tests that existed at the time and say what shape of assertion would have caught each, then check whether the tests added since actually have that shape or merely pin the fixed behaviour. Second: functional coverage. `shakenfist/deploy/shakenfist_ci/smoke_ci_tests/test_agentop_deadlines.py` has four tests; CLAUDE.md prefers functional to unit coverage, so name what the four do not reach -- retry, the reaper and the attempt bound are the obvious candidates -- and say whether that is a gap worth filing. Commit subject: "Audit agent operation deadline test coverage." |
| 8e | medium | sonnet | none | **Wave 2c, documentation review.** Work the `PUSH-AUDIT.md` 2c brief, including its readme-discipline, llm-doc-discipline and plan-phase-references shared blocks, over the decision 1 and 2 merges. F6 has already confirmed `docs/developer_guide/state_machine.md` matches `AgentOperation.state_targets`; confirm in one line rather than re-deriving. The real question is single-sourcing across the five places this plan now documents the same three constants: `docs/operator_guide/agent_operations.md`, `docs/developer_guide/api_reference/instances.md`, `docs/developer_guide/api_reference/agentoperations.md`, `docs/operator_guide/database.md` and `docs/release_notes/v07-v08.md`. Phase 7 checked three of those agreed; check all five, and check them against `shakenfist/config.py:240,259,303` rather than against each other. Also: the schema changed (phase 2), so confirm the upgrade path via `sf-ctl ensure-mariadb-schema` is documented; and `docs/developer_guide/coding_rules.md` gained a rule in `2e19bb1ea` that no phase plan mentions -- check it is discoverable from where a reader would look for it. Commit subject: "Audit agent operation deadline documentation." |
| 8f | high | opus | none | **Wave 2d, security review.** Work the `PUSH-AUDIT.md` 2d brief over the decision 1 and 2 merges. Three areas carry the weight. **Authorisation:** phases 3 and 6 added `deadline_seconds` and `progress_timeout_seconds` to three endpoints in `shakenfist/external_api/instance.py` and a capability token in `shakenfist/external_api/app.py` (`4afa29476`). Check the parameter declarations against `CLAUDE.md`'s rules, that the 400 guard actually backs the published bound rather than only documenting it, and that a caller cannot use a deadline to affect an operation in another namespace. **Denial of service:** a deadline is a client-supplied number that governs how long a hypervisor holds a per-instance executor slot. `deadline_seconds=0` means no wall-clock deadline at all. Establish what stops an unprivileged caller from parking an instance's only executor slot indefinitely with a no-deadline, progress-disabled operation, and whether the reaper actually recovers it. This is the highest-value question in the phase. **Concurrency:** phase 5's retry moves an operation `EXECUTING -> QUEUED` while a reaper may be examining it on the same node; phase 1's field mask exists because unmasked attribute writes lose cross-attribute updates. Check that `last_progress` and `attempts` writes are masked at every site, and that the throttled `last_progress` write cannot race the reaper into a false stall. Also check for interpolated SQL in the new accessors and whether an expiry state message can leak a guest path or filename into the broadly-readable event log. Commit subject: "Audit agent operation deadline security." |
| 8g | medium | opus | none | **The cross-repository contract.** Per decision 3, read `git diff 4557100^1 4557100` in `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python` (PR #380) against this repository's published contract. Change nothing in that repository. The questions: does the client's default deadline, derived from its own await timeout, always land inside the server's published bound and its 400 guard; does the client handle every terminal state the server can now produce, `expired` included, rather than polling to its full timeout; do the CLI flags name the same units the server documents; and does `AgentAwaitTimeout` versus `AgentOperationFailed` versus `AgentCommandError` mean the same thing on both sides -- phase 7's Future work records that a client exception rename silently narrowed an `except` tuple in the CI suite, which is the general hazard here. File findings as client-python issues and list them in this plan. Commit subject: "Audit the agent operation deadline client contract." |
| 8h | high | opus | none | **Grade, dispose, close out.** Collect every finding from 8b to 8g, grade each blocking or advisory, and give each a disposition: fixed here, filed as #NNNN, or declined with a reason. Fix the blocking ones in this branch. Then discharge decision 6: add a *Known defects* subsection to `docs/plans/PLAN-agent-operation-deadlines.md` naming #3995 and #4039, what each is, and why neither is fixed here (decision 7 for #3995; #4039 is a load-budget question on the same surface as #4014 and wants the same measurement discipline). Set phase 8 Complete in the Execution table, move `docs/plans/index.md` to `9 of 9` and its status to `Complete`, and write the audit's overall result into this plan. Run `python3 tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: "Close out agent operation deadlines phase 8." |

Steps 8c, 8d, 8e and 8f are independent and are launched together per
decision 4. Step 8g is independent of all four and can go with them.
Step 8b runs first because decision 5 relaxes but does not remove
wave 1's gate, and 8h needs everything.

## Corrections applied at source

Made in the step 8a commit, so a later step does not repeat them:

* `docs/plans/PLAN-agent-operation-deadlines.md`, phase 8 row: "the
  accumulated diff of every phase in this plan against `develop`" is
  replaced by a pointer to decision 1's merge list, because the diff
  as described is empty (F2).
* `docs/plans/PLAN-agent-operation-deadlines.md`, phase 6 row: notes
  that phase 6 landed in two pull requests in this repository (#4005,
  #4015) in addition to client-python #380 (F3).
* `docs/plans/PLAN-agent-operation-deadlines.md`, phase 7 row and
  Execution table status: set to `Complete` (F1, decision 8).
* `docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md`:
  the final Definition of done box is ticked with its evidence, and
  *What remains* is rewritten -- it currently asserts the branch is
  unpushed, which stopped being true when `4a122bcd3` merged (F1).
* `docs/plans/index.md`: `7 of 9` becomes `8 of 9`; the Intent cell is
  left alone, since it describes the plan rather than this phase and
  is still accurate.

The two out-of-plan corrections this survey found -- that #3995 and
#4039 are unrecorded (F5), and that three defect fixes landed outside
the phase branches (F4) -- are *not* corrected in step 8a. They are
findings the audit must dispose of, and writing them into the plan
before the audit has graded them would prejudge it. Step 8h writes
them.

## Risks and mitigations

* **The audit rubber-stamps merged code.** Reviewing something that
  already shipped invites confirming it, and every phase here was
  already reviewed once. Mitigation: each heading must name what it
  actually examined -- a function, a file, a test -- and "nothing
  found" is only acceptable alongside that list. Step 8h checks this
  before writing the results, and F4 gives every heading a concrete
  place where the code has already been shown to be wrong.
* **F6 short-circuits a check that should have been run.** Telling
  four sub-agents that the pushdown and cached-FK rules have no
  candidates saves real effort and also propagates any error in this
  survey into every heading. Mitigation: each brief asks for a
  one-line confirmation of the F6 claim it relies on, run against the
  diff, rather than an assumption. A confirmation that fails is a
  finding about this plan.
* **Step 8f's denial-of-service question has no obviously correct
  answer.** `deadline_seconds=0` is a deliberate, documented feature
  (design decision 3: streaming a 1TB file). If the reaper does not in
  fact recover a parked slot, the finding is blocking and its fix is
  larger than an audit phase. Mitigation: the phase says so and files
  it at high priority rather than downgrading it to advisory to keep
  the phase small. Decision 9's "a clean heading is a result" cuts
  both ways.
* **The client-repository step produces findings nobody owns.**
  Filing issues on another repository from an audit phase here risks
  them sitting unread. Mitigation: step 8g lists every filed issue in
  this plan, and step 8h's disposition table treats an unfixed
  cross-repository finding the same as any other advisory -- named,
  numbered, and visible from the master plan.
* **Four parallel sub-agents produce four overlapping reports.**
  Mitigation: the briefs partition by question, not by file, and step
  8h de-duplicates before grading. A finding reported by two headings
  is one finding.

## Definition of done

* Phase 7 reads `Complete` in both
  `docs/plans/PLAN-agent-operation-deadlines.md` and
  `docs/plans/index.md`, and its plan file has no unticked Definition
  of done box:
  `grep -c '^- \[ \]' docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md`
  returns 0.
* `docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md`
  contains no claim that its branch is unpushed:
  `grep -n "has not been pushed" docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md`
  returns nothing.
* Wave 1's commands and the four style greps have been run against
  every merge in decisions 1 and 2, with output recorded in this file
  -- not asserted.
* `tox -e genprotos` followed by
  `git diff --exit-code shakenfist/protos` has been run and its result
  recorded, because `protos/database.proto` is in the baseline.
* Each of the four wave 2 headings and the cross-repository step has a
  written result in this file naming what it examined.
* Every finding carries a grade (blocking or advisory) and a
  disposition (fixed here, filed as #NNNN, or declined with a stated
  reason). No blocking finding is left unresolved.
* `docs/plans/PLAN-agent-operation-deadlines.md` names #3995 and #4039
  and says why each is open:
  `grep -c "3995" docs/plans/PLAN-agent-operation-deadlines.md` and
  the same for `4039` both return at least 1.
* Every one of the three out-of-branch fixes in F4 is named in the
  master plan, so a reader of the completed plan knows the mechanism
  needed three repairs after its phases merged.
* `python3 tools/check-plan-status.py` exits zero.
* `pre-commit run --all-files` exits zero.
* `docs/plans/index.md` reads `9 of 9` and `Complete` for this plan,
  and no phase in the Execution table reads `In progress`.

## Findings

### Wave 1

Run in worktree `/srv/kasm_profiles/mikal/vscode/src/shakenfist/shakenfist-wt-aod-08`,
branch `agent-operation-deadlines-phase-08-push-audit`, working tree clean at
`1d4c9972a` (the step 8a commit) throughout.

#### pre-commit

Command: `pre-commit run --all-files`

```
Lint GitHub Actions workflows............................................Passed
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

All ten hooks passed, including the unit-test and mypy hooks. No decision-5
fallback was needed: there is nothing to check against this plan's file list
because nothing failed.

#### tox

Command: `tox` (bare invocation; `tox.ini`'s `envlist = py3,flake8,cover`, so
this ran exactly those three environments — `genprotos` is not in the default
list and was run separately below).

```
py3: OK (138.36=setup[7.35]+cmd[0.34,130.12,0.55] seconds)
flake8: OK (21.32=setup[21.32]+cmd[0.00] seconds)
cover: OK (87.19=setup[20.47]+cmd[0.06,59.52,0.30,4.99,1.85] seconds)
congratulations :) (246.93 seconds)
```

`cover`'s tail:
```
-----------------------------------------------------------------------------------------------
TOTAL                                                         37492  15148   8864    919    59%
```

Total wall time 246.93s, comfortably inside a 10-minute budget. `py3` output
contained a large volume of expected `WARNING:shakenfist.external_api.app:
Failed to resolve node UUID in API worker` and `TypeError: Object of type UUID
is not JSON serializable` traceback noise from tests that deliberately drive
API error paths (visible mid-run in the streamed output); none of it
correlated with a test outcome other than `ok`. All three environments
reported `OK`; no failure to check against decision 5.

#### Proto freshness

`protos/database.proto` is in the baseline (phases 1 and 2 add the
`deadline`/`progress_timeout` fields and the `fields` mask on
`UpdateAgentOperationAttributesRequest`), so per the plan this was checked
explicitly.

Command: `tox -e genprotos` (run only after `tox` above finished, to avoid a
concurrent regeneration of checked-in files racing the `py3` test run against
the same tree).

```
  genprotos: OK (23.31=setup[22.98]+cmd[0.33] seconds)
  congratulations :) (23.36 seconds)
```

Then: `git diff --exit-code shakenfist/protos` → exit code `0` (no output).
`git status --short` immediately after was also empty.

**Result: the checked-in stubs are fresh.** `shakenfist/protos/database_pb2.py`
and `.pyi` (and every other generated file) exactly match what
`tox -e genprotos` produces from the current `.proto` sources and Python enum
definitions. No revert was needed since nothing changed.

#### Style greps

Run against each of the twelve merges as `git diff M^1 M -- '*.py' | grep ...`
(the pushdown grep additionally piped through `grep -v '# nopushdown:'`).
`shakenfist/protos/database_pb2.py` and `shakenfist/protos/database_pb2.pyi`
were excluded from the long-line grep only (via `':!shakenfist/protos/database_pb2.py' ':!shakenfist/protos/database_pb2.pyi'`
pathspecs), since they are generated and their long lines are not findings;
the other three greps only ever look for specific tokens (`print(`, `etcd`,
`mariadb.get_all_*(`) that generated protobuf code does not contain, so no
exclusion was needed for those.

Merges checked: `87bbffcf4 f21d5da3a cb9e10bba 08807c83f 291054e98 185de6b32
4afa29476 864608276 4a122bcd3 2341ae0c4 2e19bb1ea 91d565a05`.

**Lines over 120 characters (excluding generated protos).** Without the
exclusion, exactly one hit appeared in each of `f21d5da3a` and `cb9e10bba`
(1 each); verified with a small Python check that both are the single
`DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(...)` line in
`shakenfist/protos/database_pb2.py`:

```
f21d5da3a 1 {'b/shakenfist/protos/database_pb2.py'}
cb9e10bba 1 {'b/shakenfist/protos/database_pb2.py'}
```

With the generated-file exclusion applied, all twelve merges returned empty.
**Result: no over-120-character lines in non-generated Python across the
baseline.**

**Stray `print(`.** All twelve merges returned empty. **Nothing found.**

**New `etcd` references.** All twelve merges returned empty. **Nothing
found** — expected, since this codebase's MariaDB migration long predates
this plan.

**Pushdown violations (`mariadb.get_all_[a-z_]+(` untagged with
`# nopushdown:`).** All twelve merges returned empty:

```
=== 87bbffcf4 === (empty)
=== f21d5da3a === (empty)
=== cb9e10bba === (empty)
=== 08807c83f === (empty)
=== 291054e98 === (empty)
=== 185de6b32 === (empty)
=== 4afa29476 === (empty)
=== 864608276 === (empty)
=== 4a122bcd3 === (empty)
=== 2341ae0c4 === (empty)
=== 2e19bb1ea === (empty)
=== 91d565a05 === (empty)
```

**F6's pushdown claim is CONFIRMED**, not assumed: the grep was actually run
against every one of the twelve merges (not just spot-checked), and every
one came back empty. No new-bulk-scan pushdown violation exists anywhere in
this plan's footprint.

#### Style conformance (judgment)

Examined the non-generated Python touched by all twelve merges (file lists
obtained via `git diff --name-only M^1 M -- '*.py' ':!shakenfist/protos/database_pb2.py' ':!shakenfist/protos/database_pb2.pyi'`
per merge; `87bbffcf4` touches no Python).

**Import ordering / all-imports-at-top.** `git diff M^1 M -- '*.py' | grep -nE
'^\+\s+(import |from .* import )'` (indented import lines = late imports)
returned empty across all eleven code-bearing merges. Spot-checked the added
top-level imports in `shakenfist/external_api/base.py` (`08807c83f`):
`math` and `time` were inserted alphabetically into the existing stdlib
block (`copy, functools, json, math, re, sys, time, traceback`), correctly
ahead of the `typing` imports and the `shakenfist.*` block. Test-file imports
(e.g. `shakenfist.tests.test_mariadb_agent_operations.py` in `cb9e10bba`)
consistently group stdlib (`os`, `unittest`, `uuid`), then third-party
(`sqlalchemy`), then `shakenfist.*`. **Nothing found** — no late imports, no
misordered groups.

**Logging pattern.** `git diff M^1 M -- '*.py' | grep -nE
'^\+.*(logging\.getLogger|import logging)'` returned empty across all
eleven merges — no new module introduces a raw `logging` logger bypassing
`shakenfist_utilities.logs`. `shakenfist/deploy/shakenfist_ci/base.py` does
call `logging.getLogger()` at line 22, but `git diff M^1 M -- .../base.py |
grep -nE '^\+.*(LOG|logging)'` is empty for the three merges that touch that
file (`291054e98`, `864608276`, `4a122bcd3`) — the line predates this plan
and none of this plan's diffs added or touched it. `record_attempt()`,
`record_progress()`, `add_result()` and `clear_results()` in
`shakenfist/operations/agentoperation.py` (added/extended by `f21d5da3a`,
`291054e98`, `185de6b32`) use the existing `self._attributes()` /
`mariadb.update_agent_operation_attributes()` calls and `add_event()`, not
ad-hoc prints or logging — consistent with the module's pre-existing
`LOG, _ = logs.setup(__name__)` / `.with_fields()` pattern. **Nothing
found.**

**Quote style (single for strings, double for docstrings, no triple-single).**
`git diff M^1 M -- '*.py' | grep -nE "^\+.*'''"` returned empty across all
eleven merges — **no triple-single-quoted strings added anywhere.** A
heuristic double-quote grep (`^\+[^+]*"[a-zA-Z0-9_]+"`, excluding `"""`)
turned up two categories, both benign: (1) `08807c83f` matches are inside a
`"""{...}"""`-delimited JSON example literal (`agentoperation_get_example`
style block) where the double quotes are JSON syntax, not Python string
delimiters; (2) `cb9e10bba`'s one hit is a comment containing the quoted
word `"unset"`, not a string literal. **No quote-style violations found.**

**120-char wrapping.** Covered by the style-grep section above — confirmed
zero non-generated over-120-char lines across all twelve merges.

**Three-layer MariaDB pattern + Monitor counter registration.** No new gRPC
method was added anywhere in the baseline:
`git diff M^1 M -- shakenfist/daemons/database/main.py | grep -nE
'^\+\s+def [A-Za-z]'` is empty for all eleven merges, and no diff touches
anything matching `monitor|counter|prometheus` in that file. Both phases'
`mariadb.py` changes are *extensions* of existing three-layer functions,
not new ones: `f21d5da3a` adds the `fields` mask parameter and a new
`_agent_operation_attributes_column_values()` helper, threading it correctly
through `_direct_update_agent_operation_attributes`,
`_grpc_update_agent_operation_attributes` and the public
`update_agent_operation_attributes` wrapper (verified by reading the diff:
all three layers were touched together in `f21d5da3a`). `cb9e10bba` adds the
`deadline`/`progress_timeout`/`last_progress`/`attempts` columns to
`_direct_create_agent_operation`, `_direct_get_agent_operation`,
`_direct_create_agent_operation_attributes`,
`_direct_get_agent_operation_attributes`,
`_agent_operation_attributes_column_values`,
`_grpc_create_agent_operation`, `_grpc_get_agent_operation`,
`_grpc_create_agent_operation_attributes`,
`_grpc_get_agent_operation_attributes` and
`_grpc_update_agent_operation_attributes` — every direct-layer function that
gained a column also gained the matching gRPC-layer change in the same
merge. Since no new counter-bearing RPC was introduced, there is nothing to
register in the database daemon's Monitor operations list, and none was
added. **Nothing found — pattern held on both merges that touched
`mariadb.py`.**

**Field-mask discipline (unmasked writes).** All four production call sites
of `mariadb.update_agent_operation_attributes()` outside tests
(`shakenfist/operations/agentoperation.py:422,438,468,487`) pass an explicit
non-empty `fields=` list (`['attempts']`, `['results']` x2,
`['last_progress']`); none passes `fields=None`. `record_attempt()` and
`record_progress()`'s docstrings both explicitly justify the mask by name
(collision with `add_result()`'s read-merge-write on the same `results`
column). **Nothing found** — matches the coding-rules invariant in
`CLAUDE.md`'s Common Pitfalls #3.

**Field rename / unit-change discipline (`deadline` vs `progress_timeout`).**
Read `shakenfist/schema/agentoperation_data.py` and
`agentoperation_attributes.py` (added in `cb9e10bba`) in full. `deadline` is
explicitly documented, in both the class docstring and an inline comment, as
"the absolute unix timestamp after which this operation must not be
dispatched" with an explicit note that it is "of order 1.7e9" so `0.0` reads
unambiguously as a sentinel rather than a real value. `progress_timeout` is
documented as "seconds without forward progress which are fatal to the
operation" — a duration, never conflated with the absolute timestamp. At the
API layer (`08807c83f`, `shakenfist/external_api/instance.py`), both
parameters are named with an explicit `_seconds` suffix
(`deadline_seconds`, `progress_timeout_seconds`) precisely because the
stored `deadline` is *not* itself a seconds value — `agent_operation_timing()`
in `shakenfist/external_api/base.py` converts the request's
`deadline_seconds` (a duration relative to receipt) into the stored
`deadline` (`time.time() + deadline_seconds`, an absolute timestamp) inside
one documented conversion point, and `progress_timeout_seconds` passes
through unchanged into the duration-typed `progress_timeout` column. No
site was found where the two are used interchangeably or where a duration
is written into `deadline` or vice versa. **No unit-blurring found** — this
is the one area the audit brief called out by name, and it holds.

#### Wave 1 result

pre-commit: **passed**, all 10 hooks. tox: **passed**, `py3`/`flake8`/`cover`
all `OK`, 246.93s. Proto freshness: **fresh** — `tox -e genprotos` followed
by `git diff --exit-code shakenfist/protos` exited 0, tree remained clean
throughout (no revert needed). Style greps: **clean** across all twelve
merges for all four checks; F6's "no pushdown violation exists" claim is
**confirmed by direct execution**, not assumed. Style-conformance judgment:
**no violations found** across import ordering, logging pattern, quote
style, line length, the three-layer MariaDB pattern (with no new
counter-registration gap, since no new RPC was added), the field-mask
discipline, and the `deadline`/`progress_timeout` unit distinction.

**Wave 1 passes outright — no decision-5 fallback was invoked, because
nothing failed.** No blocking findings. No advisory findings. Wave 2 (steps
8c-8g) can proceed on this baseline without qualification.

### 2a. Code quality

Baseline: `git diff M^1 M` for each of the eleven non-doc-only merges listed
in decision 1 of `docs/plans/PLAN-agent-operation-deadlines-phase-08-push-audit.md`
(`87bbffcf4` skipped as docs-only): `f21d5da3a cb9e10bba 08807c83f 291054e98
185de6b32 4afa29476 864608276 4a122bcd3 2341ae0c4 2e19bb1ea 91d565a05`.

#### Wave 2 mechanical sweep

- **TODO/FIXME/HACK/XXX in added lines:** none in any of the eleven merges.
- **New `# noqa` / `# type: ignore` / `pragma: no cover`:** none.
- **New `subprocess.` / `os.system` / `shell=True`:** none.
- **Added test-function counts** (`^\+\s*def test_`): f21d5da3a=9,
  cb9e10bba=35, 08807c83f=34, 291054e98=68, 185de6b32=53, 4afa29476=0,
  864608276=0, 4a122bcd3=4, 2341ae0c4=7, 2e19bb1ea=8, 91d565a05=4. The two
  zero-test merges (4afa29476, 864608276) are the phase-6 capability-token
  and CI-suite-adoption merges (4 and 4 files changed, 20 and 46 insertions);
  not a coverage gap for this brief.
- **Docs touched:** every phase merge (1-7) and both repair merges (2e19bb1ea,
  91d565a05 minus the latter, which touched none) touch `docs/plans/*` and/or
  `docs/developer_guide/*`; 2341ae0c4 and 91d565a05 touch no docs at all
  (pure code fixes plus tests).

No mechanical finding to triage (step 7): the sweep is clean across every
merge, so there is nothing here to grade blocking/advisory.

#### F6 confirmations (both hold)

- **SQL pushdown:** `git diff <M>^1 <M> -- '*.py' | grep -E '^\+[^+].*mariadb\.get_all_'`
  returns nothing for any of the eleven merges. Confirmed clean.
- **Cached FK list:** `git diff cb9e10bba^1 cb9e10bba -- shakenfist/schema/agentoperation_attributes.py`
  shows exactly two new scalar fields added, `last_progress: Optional[float] = None`
  and `attempts: int = 0` — no `list[...]` field. Confirmed clean.

#### 3a. Command-list ownership rule (`docs/developer_guide/coding_rules.md`, "A frozen model is not a deep frozen model")

Read `2e19bb1ea`'s addition to `coding_rules.md`: the rule names the four
container fields on cached models that must be copied at the `_db_get()`
boundary (`AgentOperationData.commands`, `InstanceData.disk_spec`,
`.video`, `.side_channels`) and forbids a caller mutating an alias of one.

Checked every boundary:
- `shakenfist/operations/agentoperation.py:141` — `'commands': list(data.commands)`.
- `shakenfist/instance.py:403,410,416` — `list(data.disk_spec)`, `dict(data.video)`,
  `list(data.side_channels)`.
- `shakenfist/daemons/sidechannel/main.py:622` — `SideChannelExecutorJob.__init__`
  takes its own copy (`self.commands = list(agentop.commands)`), and only that
  copy is popped (`self.commands.pop(0)` at line 1172).

Grepped the whole tree for mutation of these fields
(`grep -rn '\.commands\.\(pop\|append\|remove\|clear\|extend\|insert\)\|\.disk_spec\....\|\.side_channels\....'`).
The only hits are the executor's own copy (line 1172, correct) and
`out.commands.append(cmd)` in `_send_commands_single_envelope`/
`_send_replies_single_envelope`, which append to a freshly built
`agent_pb2.HypervisorToAgent()` protobuf message, not a cached model.

**No violation found.** The rule is honoured everywhere it applies today,
including at the two sites (`instance.py`, `agentoperation.py`) singled out
for a second look.

#### 3b. Executor teardown — finding

Read `_request_all_threads_exit()`, `_request_thread_exit()`,
`start_instance_executor()`, `reap_instance_executors()`,
`_wait_for_all_threads_exit()` in full (`shakenfist/daemons/sidechannel/main.py`).

Signal-before-join ordering is correct: `_request_all_threads_exit()`
(lines 1750-1785) sets every thread's abort path in one loop over
`list(self.monitors.values()) + list(self.executors.values())` *before*
either of the two join loops runs, exactly as `2341ae0c4`'s fix and its
comment describe. That part holds.

**But a sibling race the same fix left unguarded:**
`start_instance_executor()` (line 1504) registers the executor into
`self.executors[instance_uuid]` at lines 1533-1537 — *before* calling
`sc_thread.start()` at line 1547 — deliberately, so the reaper can see an
in-flight dispatch before the thread exists (the comment at 1522-1532
explains why). `reap_instance_executors()` (line 1559) knows about this
window and guards it explicitly: `if t['thread'].ident is None: continue`
(line 1586), with a comment spelling out that `join()` on an unstarted
thread raises `RuntimeError: cannot join thread before it is started`.

`_request_thread_exit()` (line 1787), the method `2341ae0c4` generalised
from the old monitor-only version to serve both dictionaries, has **no
such guard** and calls `t['thread'].join(0.5)` unconditionally (line 1800).
For monitors this is safe by construction — `start_instance_monitor()`
calls `sc_thread.start()` *before* registering into `self.monitors`, so a
monitor entry is never seen mid-registration. For executors it is not:
if `_request_all_threads_exit()` runs (from `_wait_for_all_threads_exit()`
during daemon shutdown) while the dispatcher thread is between line 1537
and line 1547 of `start_instance_executor()` for the same instance, the
executor's `join(0.5)` call raises `RuntimeError`. Nothing at the call
site (`_request_all_threads_exit()`'s `for instance_uuid in
list(self.executors.keys()): self._request_thread_exit(...)`, line
1783-1785) or in `_run_inner()`'s shutdown block (lines 2008-2011, no
try/except around `self._wait_for_all_threads_exit()`) catches it, so the
daemon's graceful-shutdown path crashes with an uncaught exception instead
of reaching `LOG.info('Stopped')` / `daemon.force_clean_exit()`.

**File:** `shakenfist/daemons/sidechannel/main.py:1787-1808` (`_request_thread_exit`),
contrasted with the guard at `shakenfist/daemons/sidechannel/main.py:1586`
(`reap_instance_executors`).
**Grade: advisory.** The race window is a handful of bytecode instructions
in `start_instance_executor()` between dict registration and
`Thread.start()`, so it needs a shutdown signal to land in that exact
window while the dispatcher is actively starting a new executor — rare in
practice, and systemd will restart the daemon on the resulting crash
(no data corruption: the operation is left mid-dispatch the same way an
ordinary daemon kill -9 would leave it, which `agent_operation_next()`'s
durability design and the reaper already tolerate). It is not blocking,
but it is a second instance of exactly the defect class `2341ae0c4` was
written to close (a `KeyError`/`RuntimeError` on shutdown teardown caused
by the executor's "register before start" ordering), and the fix that
generalised `_request_thread_exit()` for both dictionaries did not carry
over the one guard that ordering requires. Concrete failure scenario: a
rolling deploy sends SIGTERM to `sf-sidechannel` at the instant its
dispatcher thread is between lines 1533 and 1547 dispatching a new
operation; `_wait_for_all_threads_exit()` calls `_request_thread_exit()`
on that same instance, which raises `RuntimeError` out of the shutdown
path.

#### 3c. Policy duplication — no finding (clean)

Read `AgentOperation.effective_deadline()` / `deadline_passed()` /
`effective_progress_timeout()` (`shakenfist/operations/agentoperation.py:251-317`),
`resolve_abandoned_operation()` and `SideChannelExecutorJob.expire_if_out_of_budget()`
(`shakenfist/daemons/sidechannel/main.py:528-592, 960-1061`),
`Monitor._resolve_stuck_queue_head()` (same file, 1625-1748), and
`Instance.agent_operation_next()` (`shakenfist/instance.py:2606-2684`), plus
`NodeAgentopOp._preflight()` (`shakenfist/operations/node_aop_op.py:90-131`).

The four "checks" the brief asks about collapse to two shared primitives and
one single-site check, not four independent re-derivations:

- **Wall-clock deadline** is decided in exactly one place,
  `AgentOperation.deadline_passed()` (which itself delegates the NULL-anchor
  resolution to `effective_deadline()`). Every enforcement site —
  `agent_operation_next()` (dequeue), `NodeAgentopOp._preflight()` (twice,
  before and mid blob copy), `SideChannelExecutorJob.expire_if_out_of_budget()`,
  `Monitor._resolve_stuck_queue_head()`, and `resolve_abandoned_operation()`
  — calls `agentop.deadline_passed(state=...)` rather than reading
  `.deadline` and comparing against `time.time()` itself. Grepping the tree
  for `deadline_passed\|effective_deadline` confirms there is no second
  comparison anywhere.
- **Progress timeout** is decided in exactly one place,
  `AgentOperation.effective_progress_timeout()`, called from exactly one
  site, `expire_if_out_of_budget()`. The reaper deliberately does not
  duplicate this: its own docstring says "the progress timeout is that
  executor's own job and it holds state this method does not," so a live,
  in-budget executor is left alone rather than re-checked.
- **Attempt bound** (`config.AGENT_OPERATION_MAX_ATTEMPTS`) is compared
  against `agentop.attempts` in exactly one function,
  `resolve_abandoned_operation()`. Both of the reaper's two resolution paths
  (case one: no executor; case two: wedged executor past deadline) and the
  executor's own two exit paths (progress-timeout stall, and the `finally`
  block in `SideChannelExecutorJob.execute()`) all route through this one
  function rather than re-implementing the attempt-cap comparison.
- **The reaper's view of a stalled operation** (`_resolve_stuck_queue_head()`)
  is not a fourth independent policy: it is `deadline_passed()` reused, plus
  liveness evidence unique to the reaper (whether `self.executors` has a
  thread for the instance) that no other site could derive since it depends
  on this node's process state.

**No finding.** A shared constant/method used four times, not four copies
that can drift, exactly as the brief's threshold describes.

#### 4. MariaDB three-layer pattern and RPC registration

Checked the affected accessors in `shakenfist/mariadb.py`:
`_direct_update_agent_operation_attributes` /
`_grpc_update_agent_operation_attributes` / `update_agent_operation_attributes`
(the field-mask trio), and the corresponding create/get/delete trios for
`agent_operation` and `agent_operation_attributes`. All follow the
`_direct_*` / `_grpc_*` / public-wrapper pattern already in place; the field
mask (`fields: Optional[List[str]] = None`) is threaded through
`_agent_operation_attributes_column_values()` identically in the direct
path and as a `repeated string fields` proto field in the gRPC path, and
every caller (`record_attempt()` → `fields=['attempts']`,
`record_progress()` → `fields=['last_progress']`, `add_result()` /
`clear_results()` → `fields=['results']`) passes an explicit mask, never
`fields=None`.

Verified wave 1's "no new RPC" claim directly rather than trusting it:
`git diff <M>^1 <M> -- protos/database.proto` across all eleven merges
shows only new *fields* added to three pre-existing messages
(`AgentOperationStaticData` gained `deadline`/`progress_timeout`,
`AgentOperationAttributesProto` gained `last_progress`/`attempts`,
`UpdateAgentOperationAttributesRequest` gained `fields`) — no new `rpc`
method declaration anywhere in the diff. `grep -n "AgentOperation"
shakenfist/daemons/database/main.py` shows the five RPC handlers
(`CreateAgentOperation`, `GetAgentOperation`, `DeleteAgentOperation`,
`CreateAgentOperationAttributes`, `GetAgentOperationAttributes`,
`UpdateAgentOperationAttributes`, `DeleteAgentOperationAttributes`) all
pre-date this plan and are unchanged in count, so nothing new needed a
Monitor-operations counter. Confirmed, not assumed.

Also checked the migration for the two new columns
(`_ensure_agent_operation_attributes_schema`, around
`shakenfist/mariadb.py:18889-18915`): `last_progress DOUBLE NULL`,
`attempts BIGINT NOT NULL DEFAULT 0`. `pydantic_to_sqlalchemy_table` maps
Python `int` to `sa.BigInteger()` (`shakenfist/schema/sqlalchemy.py:272`),
so a fresh install (via `create_all()`) and an upgraded cluster (via this
`ALTER TABLE`) land on the same column type. No drift.

**No finding.**

#### 5. Duplicated code / missed abstractions

Looked specifically at whether deadline/progress-timeout logic is
duplicated between the executor, the REST layer, and
`operations/agentoperation.py`, per the brief.

`shakenfist/external_api/base.py:227-281` (`agent_operation_timing()`) and
its helper `_timing_seconds()` are the single conversion point for the
three request-body parameters (`deadline_seconds`, `progress_timeout_seconds`)
into the stored `(deadline, progress_timeout)` pair. All three endpoints
that create agent operations —
`InstanceAgentPutEndpoint.post()`, `InstanceAgentGetEndpoint.post()`,
`InstanceAgentExecuteEndpoint.post()` in `shakenfist/external_api/instance.py`
(diffed directly: lines ~1676-1880 across the eleven merges) — call this one
function rather than validating/defaulting inline, and share the two
parameter-description constants (`DEADLINE_SECONDS_DESCRIPTION`,
`PROGRESS_TIMEOUT_SECONDS_DESCRIPTION`, `instance.py:76-91`) instead of
re-typing the Swagger description three times. No copy-paste found here.

**No finding** — this is the one area the brief specifically worried about,
and it is well-factored, not duplicated.

#### 6. Comment proportion

Scanned the touched files (`sidechannel/main.py`, `agentoperation.py`,
`node_aop_op.py`, `external_api/base.py`, `external_api/instance.py`) for
comment runs of 12+ lines, then checked which of those are actually new
in this plan's diff (several long-standing blocks in `external_api/base.py`
around credential redaction and decorator ordering pre-date this plan and
are out of scope).

Candidates actually introduced by this plan, all in
`shakenfist/daemons/sidechannel/main.py`:
- Lines 98-113 (`SideChannelJob.__init__`, abort-path ownership rationale).
- Lines 607-621 (`SideChannelExecutorJob.__init__`, the #3970 command-list
  copy rationale).
- Lines 667-680 and 691-702 (`execute()`'s `finally` block).
- Lines 991-1011 (`expire_if_out_of_budget`'s NULL-deadline anchor cache,
  #4014).
- Lines 1197-1214 and 1231-1257 (`_dispatch_next_command`'s terminal-state
  guard and attempt-counting rationale).
- Lines 1695-1714 (`_resolve_stuck_queue_head`, case-one fail-vs-expire
  distinction).
- Lines 1753-1774 (`_request_all_threads_exit`, the #3931 KeyError history).

Also `shakenfist/external_api/base.py:227-256` (`agent_operation_timing`'s
30-line docstring over its ~15-line body).

Applying the shared block's test: every one of these documents either (a)
a three-valued semantic that is genuinely non-obvious from the code alone
(the `None`/`0`/value tri-state for deadline and progress timeout — code
this short cannot say why `0` is special without prose), (b) a correctness
invariant whose violation was a real, merged bug (#3516, #3970/#3931,
#4014 are all named directly, with PR numbers), or (c) an ordering
guarantee (attempt counted once per dispatch, not per command) that a
future editor could plausibly get wrong without the explanation. None of
them restate what the adjacent code already says in different words.

**No advisory findings** — every candidate by line-count is justified by
the shared block's own carve-out (a hard-won bug explanation or a
non-obvious contract).

#### 7. Triage of mechanical findings

The wave 2 mechanical sweep (TODO/FIXME/HACK/XXX, new `# noqa` / `# type:
ignore` / `pragma: no cover`, new `subprocess.`/`os.system`/`shell=True`)
found nothing across all eleven merges. There is nothing to triage.

---

**Summary of what was examined:** `shakenfist/daemons/sidechannel/main.py`
in full (2037 lines); `shakenfist/operations/agentoperation.py` in full;
`shakenfist/operations/node_aop_op.py` (preflight deadline enforcement);
`shakenfist/instance.py` (`_static_values_to_dict`, `agent_operation_next`,
static-value properties for `disk_spec`/`video`/`side_channels`);
`shakenfist/external_api/base.py` (`agent_operation_timing`,
`_timing_seconds`) and `shakenfist/external_api/instance.py` (the three
agent-operation endpoints) as diffed across the eleven merges;
`shakenfist/mariadb.py` (agent-operation and agent-operation-attributes
direct/gRPC/public trios, field-mask helper, schema migration);
`shakenfist/daemons/database/main.py` (AgentOperation RPC handlers);
`protos/database.proto` diffed across all eleven merges;
`docs/developer_guide/coding_rules.md`'s "A frozen model is not a deep
frozen model" section; `shakenfist/schema/agentoperation_attributes.py`
and `shakenfist/schema/sqlalchemy.py` (`pydantic_to_sqlalchemy_table`'s
`int` → `BigInteger` mapping).

**Totals: 0 blocking, 1 advisory** finding (§3b, the unguarded
`Thread.join()` in `_request_thread_exit()` for an executor thread that
has been registered but not yet started). Everything else examined — the
command-list ownership rule, the four timing-budget checks, the MariaDB
three-layer pattern and RPC registration, REST-to-executor duplication,
and the comment-proportion candidates — came back clean.

### 2b. Test review

Baseline: `git diff M^1 M` for each of `f21d5da3a`, `cb9e10bba`,
`08807c83f`, `291054e98`, `185de6b32`, `4afa29476`, `864608276`,
`4a122bcd3`, `2341ae0c4`, `2e19bb1ea`, `91d565a05`. Wave 1 result
(pre-commit + `tox`: py3, flake8, cover) already confirmed passing by
step 8b; not re-run here.

Files read in full or by targeted section: `shakenfist/tests/
test_daemon_sidechannel_executor.py` (1946 lines, all classes),
`test_daemon_sidechannel_shutdown.py`, `test_agent_operation_expiry.py`,
`test_agent_operation_retry.py`, `test_instance_static_value_ownership.py`,
`test_mariadb_agent_operations.py`, `test_mariadb_agent_operations_live.py`,
`test_instance.py` (agent-operation sections, lines ~759-1110),
`shakenfist/tests/operations/test_node_aop_op.py`,
`shakenfist/tests/external_api/test_agent_operation_timing.py`,
`test_agent_operation_parameters.py`,
`shakenfist/deploy/shakenfist_ci/smoke_ci_tests/test_agentop_deadlines.py`,
plus production `shakenfist/operations/node_aop_op.py`,
`shakenfist/external_api/base.py`, `shakenfist/external_api/instance.py`,
`shakenfist/daemons/sidechannel/main.py` (diff-relevant sections) and
`shakenfist/operations/agentoperation.py`. Git history was walked with
`git show <rev>:<path>` to read test files as they stood immediately
before each fix.

#### 1. The counterfactual question

**#3970 (`2e19bb1ea`) — command-list aliasing.**

Read `shakenfist/tests/test_daemon_sidechannel_executor.py` at
`2e19bb1ea^1` (before the fix). Every test that touches
`SideChannelExecutorJob.commands` builds the job with
`SideChannelExecutorJob.__new__(...)` and then hand-assigns
`job.commands = list(commands)` directly (e.g. the pre-fix
`ExecutorTerminalStateGuardTestCase._make_executor`,
`ExecutorBudgetTestCase._make_executor`,
`ExecutorDispatchTerminalGuardTestCase._make_executor`). None of them
ever went through the real `__init__`, which is exactly where
`self.commands = agentop.commands` created the alias. The shape of
assertion that would have caught it is an identity check taken through
the real constructor: build a job with `SideChannelExecutorJob(inst,
agentop)` and assert `job.commands is not agentop.commands`, then
dispatch through it and assert `agentop.commands` is unchanged. No such
test existed pre-fix.

The fix itself adds exactly that shape, in three places, and it is the
right shape, not just a pin: `AgentOperationCommandListTestCase` in
`test_agent_operation_retry.py:146-215` (added by `2e19bb1ea`) asserts
`assertIsNot(data.commands, first['commands'])` and that mutating one
reader's list leaves a sibling and the cached model untouched;
`ExecutorCommandListOwnershipTestCase` in
`test_daemon_sidechannel_executor.py:1208-1262` builds a
`SideChannelExecutorJob` through its real constructor and asserts
`assertIsNot(job.commands, job.agentop.commands)`, then dispatches and
asserts the operation's own list is untouched
(`test_a_dispatched_get_file_is_still_retryable` even re-checks
`operation_is_retryable()` at the exact point the regression fired);
`test_instance_static_value_ownership.py` mirrors the same identity
check for `Instance._static_values_to_dict()`'s three container fields.
These are invariant assertions (identity/independence), not
value-pinning, so a regression of the alias would fail them again.
**Grade: informational / confirmed adequate.**

**#3931 (`2341ae0c4`) — executor teardown `KeyError`.**

Read `shakenfist/tests/test_daemon_sidechannel_executor.py` at
`2341ae0c4^1`: `grep -n "^class "` over that revision returns no class
for `_request_thread_exit` or `_request_all_threads_exit` at all — the
shutdown path had **zero** test coverage before the fix, not merely the
wrong shape. `test_daemon_sidechannel_shutdown.py` is new in this same
merge and is the first test of that method. Its shape is right:
`test_executor_without_monitor_does_not_keyerror` builds a reaper-style
fixture with an executor entry and no matching monitor entry and calls
the un-mocked real method, which is precisely the condition that raised
`KeyError`; `test_executor_shutdown_leaves_the_monitor_alone` and
`test_shutdown_waits_for_executors` extend it. **Grade: informational /
confirmed adequate for the new file.**

However, a related test added *after* the fix landed still tolerates
the exact defect class it is nominally about, and is worth flagging on
its own:

- `shakenfist/tests/test_daemon_sidechannel_executor.py:287-306`
  (`test_every_thread_is_signalled`) and `:308-330`
  (`test_signalling_happens_before_any_join`), in class
  `RequestAllThreadsExitTestCase`. Both wrap the call to
  `mon._request_all_threads_exit()` in `try: ... except KeyError: pass`
  with the comment "#3931, which lives in `_request_thread_exit()` and
  is fixed separately." `git log -S"class RequestAllThreadsExitTestCase"`
  shows this class was added in commit `93ada39c2`, dated 2026-08-29 —
  one day *after* `2341ae0c4` (2026-08-28) had already fixed #3931 on
  `develop`, and the branch's merge-base with `develop` (`52b4a9447`,
  2026-08-30) postdates the fix as well. So this is not a test written
  under the bug and later stranded — it was written with the fix
  already on the branch, and still defensively swallows the exact
  exception the fix removed. **Grade: blocking.** What goes undetected:
  if `_request_thread_exit()` ever regresses to reading the wrong dict
  again (the precise shape of #3931), these two tests will not fail —
  they will silently take the `except KeyError: pass` branch and keep
  asserting only on the *signalling*, which happens before the point
  where the KeyError was raised. `test_daemon_sidechannel_shutdown.py`
  is a smaller, more targeted regression test for the same defect and
  does not have this problem, but its existence does not make the
  `except KeyError` in this file harmless — it means a second
  regression-catching site currently masks the very failure it names in
  its own docstring. Fix: call the un-swallowed method and let a
  `KeyError` fail the test, or drop the `try/except` now that the fix
  is a permanent part of the tree.

#### 2. Behaviour versus implementation

- `shakenfist/tests/test_daemon_sidechannel_executor.py:1116-1117`
  (`test_a_failure_to_unlink_is_not_fatal`): asserts
  `job.log.with_fields.return_value.warning.assert_called_once()` in
  addition to the behavioural assertion
  (`self.assertIsNone(job._blob_partial_file)`). **Advisory.** Coupled
  to the exact logging call chain (`log.with_fields(...).warning(...)`);
  a refactor to a different logging call shape (e.g. `log.warning(...,
  extra=...)`) breaks this test with no behavioural regression. The
  state assertion on the same test already proves the failure was
  tolerated, so the log assertion adds fragility without adding
  detection power.
- `shakenfist/tests/test_daemon_sidechannel_executor.py:296,327`
  (the `except KeyError: pass` pattern discussed above) is also an
  implementation-adjacent smell in the opposite direction: rather than
  asserting on an implementation detail, it structurally cannot detect
  a regression in one. Already graded blocking above; listed here too
  because it is the clearest instance of "assertion shape hides
  behaviour" in the diff.
- Exact state-message string assertions such as
  `test_daemon_sidechannel_executor.py:673-679`
  (`'no progress from the agent for 30 seconds, and the operation
  cannot be safely retried'`) and the parallel strings in
  `ExecutorRetryTestCase` (`test_daemon_sidechannel_executor.py:1312-
  1391`) pin exact wording. **Advisory, not blocking** — these strings
  are the operator-visible `object_states.message` value CLAUDE.md
  asks the project to keep auditable, not a private log line, so
  pinning them is closer to a documented contract than an
  implementation detail. Still, a future wording tweak (e.g. adding
  the attempt count in a different position) will break several tests
  at once for a change with no behavioural content; grouping the
  literal strings into shared constants (the way `ExecutorRetryTestCase`
  already does with `STALL`/`EXIT`) would reduce the blast radius.
- No instances found of assertions on private-attribute *names* purely
  for their own sake (as opposed to the state they carry), on internal
  helper call counts that aren't the behaviour under test, or on
  unrelated-operation ordering. The `_last_progress`, `_blob_partial_file`,
  `_deadline_anchor` attribute reads throughout `ExecutorBudgetTestCase`
  and `ExecutorProgressPersistenceTestCase` are read via
  `__new__`-constructed fixtures rather than the real constructor, which
  is a common and accepted pattern in this file (documented inline,
  e.g. `test_daemon_sidechannel_executor.py:502-503`), and the values
  asserted are the actual budget/anchor state the production code
  depends on, not incidental internals.

#### 3. Adversarial cases

| Case | Covered? | Where |
|---|---|---|
| Deadline already in the past at enqueue/dequeue | **Yes** | `test_instance.py:867` `test_next_expires_a_queued_head_past_its_deadline` uses `deadline=1.0` (1970), and the equivalent NULL-deadline-anchor case at `test_instance.py:916`. Functionally in `test_agentop_deadlines.py:191` `test_queued_operation_expires_on_its_deadline`. |
| `deadline_seconds=0` (no-deadline sentinel) alone | **Yes** | `test_agent_operation_expiry.py:71` `test_explicit_zero_deadline_means_none`; `test_instance.py:905` `test_next_never_expires_an_explicit_zero_deadline`; `test_agent_operation_timing.py:75-99`. |
| `deadline_seconds=0` **combined with** a disabled progress timeout, end-to-end through `expire_if_out_of_budget()` | **No** | `test_instance.py:1062` `test_zero_is_not_none` only checks the pair round-trips through the database; no test drives `SideChannelExecutorJob.expire_if_out_of_budget()` with both budgets simultaneously disabled to confirm it returns `False` forever (i.e. that nothing ever reclaims such a slot short of the reaper's thread-liveness check). This is the exact scenario the phase's own risk section (decision list, "Step 8f's denial-of-service question") flags as unresolved; a test review can only confirm the gap, not answer whether it is safe. |
| Attempt bound reached exactly | **Yes** | `test_daemon_sidechannel_executor.py:1406-1450` (`test_an_executor_exit_retries_to_the_cap_and_then_errors`) loops exactly `config.AGENT_OPERATION_MAX_ATTEMPTS` times and asserts the state sequence `[QUEUED]*(cap-1) + [ERROR]`. |
| Transition attempted from a terminal state | **Partial** | `complete` and `expired` are covered as *sources* refused into `queued`/`error` (`test_agent_operation_retry.py:68-76`, `test_agent_operation_expiry.py:175-183`; also `AgentOperationStateMachineTestCase.test_expired_is_terminal`, `test_agent_operation_expiry.py:287-294`). `error` and `deleted` as sources are asserted only at the `state_targets` dict-shape level (`state_targets[dbo.STATE_ERROR] == (dbo.STATE_DELETED,)`), never via a runtime `assertRaises(InvalidStateException, ...)` the way `complete`/`expired` are. **Advisory gap.** |
| Reaper and executor racing on the same operation | **Yes (simulated ordering)** | `test_daemon_sidechannel_executor.py:1817-1848` (`test_a_wedged_live_executor_is_resolved_then_aborted`) explicitly asserts resolve-before-abort ordering via an instrumented `order` list, which is exactly the invariant the docstring says protects against the executor's own `finally` overwriting the reaper's verdict. Not a true concurrent-thread test (acceptable — none of the surrounding suite uses real threads for this either). |
| Empty command list | **Yes** | `OperationRetryabilityTestCase.test_an_empty_list_is_not_retryable` (`test_daemon_sidechannel_executor.py:1288-1291`), with the comment explaining why `all([])` being vacuously true would otherwise wrongly mark it retryable. |
| Concurrent deletion of the instance mid-operation | **No** | No test in any of the reviewed files sets `Instance.state` to `deleted` (or calls `hard_delete()` on the instance) while an `AgentOperation` is `executing`/`queued` against it. `test_agent_operation_expiry.py:244` `test_hard_delete_clears_object_references` covers the *operation's own* hard-delete cleanup, not the instance-disappears-underneath-it case. Every executor/dispatcher test that models "gone" models the *operation* reaching `deleted` (`test_a_deleted_operation_lets_the_loop_exit`, `test_daemon_sidechannel_executor.py:1166-1178`), never the instance. **Advisory gap** — worth a targeted unit test (fake instance `from_db()` returning `None` or raising mid-dispatch) even without a functional equivalent. |

#### 4. Functional coverage gaps

`shakenfist/deploy/shakenfist_ci/smoke_ci_tests/test_agentop_deadlines.py`'s
four tests cover: the default deadline being published on an operation
(`test_default_deadline_is_published`), a wall-clock expiry freeing the
executor slot for a queued follower
(`test_expiry_frees_the_executor_slot`), a silent (non-progress-capable)
command surviving the progress timeout
(`test_silent_execute_survives_the_progress_timeout`), and a queued
head expiring on its own deadline before ever being dispatched
(`test_queued_operation_expires_on_its_deadline`). None of the four
dispatches a command more than once, so none reaches:

- **Retry-to-completion** (a progress-capable command that stalls once,
  requeues, and succeeds on a second attempt). Not present in
  `smoke_ci_tests` or `cluster_ci_tests` (`grep` for `sidechannel` /
  `agentop` across `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/`
  returns nothing). **Worth filing.** It does not require killing a
  daemon or a wedged guest — only a guest-side command that is silent
  for longer than the progress timeout on its first attempt and
  responds normally after, which the CI guest images already support
  the primitives for (the existing silent-execute test proves the
  harness can already produce a controlled stall).
- **The attempt bound reached functionally** (a command that stalls on
  every attempt until `AGENT_OPERATION_MAX_ATTEMPTS` is exhausted and
  the operation errors). **Worth filing**, same primitive as above
  repeated `AGENT_OPERATION_MAX_ATTEMPTS` times, at a real but bounded
  cost (roughly `progress_timeout * attempts` wall time per run) — not
  free, but not impractical either.
- **The node-local reaper's daemon-restart-drains-the-queue path**
  (an operation left `executing` when the sidechannel daemon dies and
  restarts, requiring the *next* daemon's reaper to notice the missing
  executor thread and resolve it). **Genuinely impractical in this
  suite as it stands**, not merely inconvenient: `test_health.py:13-16`
  in this same `cluster_ci_tests` directory states the project's own
  precedent explicitly — "restart sf-api and observe the 503 window
  ... would require stopping a daemon in the shared CI cluster, which
  is too invasive for a single test module," and defers that class of
  assertion to unit coverage plus a documentation note. The reaper's
  daemon-restart case has exactly the same shape and the same
  objection applies; it is well covered at the unit level instead
  (`ExecutorReaperTestCase.test_a_daemon_restart_drains_the_queue`,
  `test_daemon_sidechannel_executor.py:1727-1751`).

#### 5. Zero-coverage check

- `shakenfist/daemons/sidechannel/main.py`: every function this plan's
  merges added or materially changed has a corresponding test class
  (`SideChannelExecutorJob.__init__` command copy, `expire_if_out_of_budget`,
  `observe_progress`/persistence, `_dispatch_next_command`,
  `_abort_commands_if_terminal`, `resolve_abandoned_operation`,
  `operation_is_retryable`, `start_instance_executor`,
  `reap_instance_executors`/`reap_instance_monitors`,
  `_request_thread_exit`/`_request_all_threads_exit`). Nothing found
  uncovered.
- `shakenfist/operations/agentoperation.py`: `effective_deadline`,
  `deadline_passed`, `effective_progress_timeout`, `expire`, `fail`,
  `record_progress`, `record_attempt`, `clear_results` are all
  exercised in `test_agent_operation_expiry.py` /
  `test_agent_operation_retry.py`. Nothing found uncovered.
- `shakenfist/instance.py`: the diffs in this plan's merges did not add
  new top-level functions (confirmed via `git diff <M>^1 <M> --
  shakenfist/instance.py | grep '^+.*def '` returning nothing for
  `291054e98`/`185de6b32`); they changed `agent_operation_next()` and
  `_static_values_to_dict()` in place, both covered
  (`test_instance.py:867` onward; `test_instance_static_value_ownership.py`).
- `shakenfist/external_api/`: `agent_operation_timing()` and its
  `_timing_seconds()` helper in `external_api/base.py` are covered
  exhaustively by `test_agent_operation_timing.py` (bool, NaN, inf,
  negative, list, non-numeric all present). The three call sites in
  `external_api/instance.py` (`InstanceAgentPutEndpoint`,
  `InstanceAgentGetEndpoint`, and the execute endpoint) are covered by
  `test_agent_operation_parameters.py`, including the "refused request
  creates nothing" property
  (`test_put_refuses_a_negative_deadline_before_looking_up_the_blob`).
  The capability-token addition in `external_api/app.py` (`4afa29476`)
  is covered only by `test_root.py`'s one-line update
  (`git diff 4afa29476^1 4afa29476 -- shakenfist/tests/`); this repo's
  own PUSH-AUDIT.md F1 confirms the capability is exercised functionally
  by every `test_agentop_deadlines.py` test via
  `check_capability('agentoperation-deadlines')`, so this is not a real
  gap, just thin in isolation.

Nothing else in the footprint (per the merge list in decision 1) was
found with zero coverage.

---

**Wave 1**: confirmed already green by step 8b (`tox`: py3, flake8,
cover all OK); not re-run here per the task instructions.

### 2c. Documentation review

Baseline used: `git diff M^1 M` for each of the twelve merges named in
decisions 1 and 2 of `docs/plans/PLAN-agent-operation-deadlines-phase-08-push-audit.md`
(`87bbffcf4`, `f21d5da3a`, `cb9e10bba`, `08807c83f`, `291054e98`, `185de6b32`,
`4afa29476`, `864608276`, `4a122bcd3`, `2341ae0c4`, `2e19bb1ea`, `91d565a05`).

#### F6 confirmation (state machine)

Confirmed: `docs/developer_guide/state_machine.md`'s *Agent Operations* section
matches `AgentOperation.state_targets` (`shakenfist/operations/agentoperation.py:56-75`)
exactly -- `expired` is documented, and the mermaid diagram's four
`--> expired` edges (`initial`, `preflight`, `queued`, `executing`) plus the
`executing --> queued` retry edge reproduce the dict verbatim.

One caveat worth surfacing: F6 itself says the doc "carries all **five**
`--> expired` edges." Both the code and the doc have **four**, not five
(`None` does not target `expired`; only `initial`, `preflight`, `queued`,
`executing` do). This is a miscount in the phase-8 plan's own survey text,
not a doc/code mismatch -- the doc and code agree with each other, just not
with F6's count. **Advisory** — worth a one-word fix in the plan
(`docs/plans/PLAN-agent-operation-deadlines-phase-08-push-audit.md:~F6`)
so a future reader doesn't recount and find a ghost discrepancy.

#### 1. Single-sourcing of the three constants

Read and checked against `shakenfist/config.py:240` (`AGENT_OPERATION_DEFAULT_DEADLINE`,
600), `:259` (`AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT`, 30) and `:303`
(`AGENT_OPERATION_MAX_ATTEMPTS`, 3, `ge=1`):

- `docs/operator_guide/agent_operations.md:41,47,126` — 600 / 30 / 3, all correct.
- `docs/developer_guide/api_reference/instances.md:680` — 600 / 30, correct (this
  page never states MAX_ATTEMPTS, which is fine — it documents request
  parameters, not retry).
- `docs/developer_guide/api_reference/agentoperations.md` — states neither
  numeric default directly (only describes the fields); no contradiction, but
  see the stale-prose finding below.
- `docs/operator_guide/database.md:1141-1142` — 600 / 30, correct; MAX_ATTEMPTS
  mentioned by name at line 1163 with no restated number (safe).
- `docs/release_notes/v07-v08.md:745,749,789` — 600 / 30 / 3, all correct.

**All five agree with each other and with `config.py`.** No numeric
contradiction found.

**NULL semantics.** Checked every place that states what NULL means:
`docs/operator_guide/database.md:1128-1131` gives the corrected meaning
("NULL — no client intent was recorded, so the server default applies ...
It does **not** mean 'no deadline'") and matches
`AgentOperation.effective_deadline()`'s docstring
(`shakenfist/operations/agentoperation.py:251-269`) almost verbatim, including
the per-transition-anchor consequence. `docs/developer_guide/api_reference/agentoperations.md:14-17`
states the same corrected meaning for the caller-intent fields. No document
anywhere restates the old, wrong "NULL means no deadline."

**Finding (advisory): stale enforcement-timing prose in `docs/developer_guide/api_reference/agentoperations.md:12-14`.**
The sentence "`last_progress` and `attempts` stay `null` and `0` until a
following release begins enforcing these values" was written in phase 2
(`cb9e10bba`) when only the schema existed, and was never revisited when
phases 4/5 (`291054e98`, `185de6b32`) actually shipped enforcement and the
retry counter (`AgentOperation.attempts` setter increments it at
`shakenfist/operations/agentoperation.py:421`, read and enforced by
`shakenfist/daemons/sidechannel/main.py:564-566`). A reader of this page
today would wrongly conclude `attempts` is inert and `last_progress` never
populates outside a "following release" that, in this same tree, has
already landed. **Advisory** (not blocking: nothing about it is wrong in
outcome, only the framing is dated) — the fix is to drop the "until a
following release" clause and state plainly that both fields are the
server's live bookkeeping.

#### 2. Shared blocks

**readme-discipline.** `git diff M^1 M -- README.md` is empty for every one
of the twelve merges. No feature bullets were added to `README.md`. Clean.

**llm-doc-discipline.** `git diff M^1 M -- AGENTS.md ARCHITECTURE.md` is
empty for every merge. Neither file grew, and neither restates a fact
`docs/` owns (there is nothing to restate). Clean.

**plan-phase-references.** Grepped `README.md` and `docs/` excluding
`docs/plans/` for `phase [0-9]`, restricted to files this plan could plausibly
have touched (`docs/operator_guide/`, `docs/developer_guide/`,
`docs/release_notes/`, `AGENTS.md`, `ARCHITECTURE.md`, `README.md`,
`docs/index.md`). Zero hits attributable to this plan. The only `phase N`
hits under `docs/` outside `docs/plans/` belong entirely to
`PLAN-scheduler-reservations.md` and `PLAN-ci-cloud-sizing.md` prose in
`docs/operator_guide/scheduler.md`, `docs/operator_guide/database.md`,
`docs/developer_guide/database_internals.md`, `docs/developer_guide/subsystem_internals.md`,
`docs/developer_guide/ci.md` and `ARCHITECTURE.md:198` — none introduced or
touched by any of the twelve merges in scope, and already tracked (per the
shared block's own text) under the out-of-band consistency audit, issue
#3732. Not this plan's finding. (`docs/components/development/**` also
matches the grep heavily, but that subtree is an aggregated mirror of other
repositories' own plan docs, unrelated to this plan and already covered by
the same out-of-band audit — excluded as noise.) Clean for this plan.

#### 3. Schema and upgrade path

`docs/operator_guide/database.md:578-591` documents `sf-ctl
ensure-mariadb-schema` generically (creates missing tables, applies pending
migrations, must run before starting/rolling `sf-database`) and separately
states (lines 92-98, elsewhere in the same doc) that `sf-database` performs
compatibility and schema-version checks and refuses to start against a
schema behind its build. The `agent_operations` (line 1084) and
`agent_operation_attributes` (line 1107) table rows in the schema table list
`deadline`/`progress_timeout` and `last_progress`/`attempts` respectively,
both marked nullable where appropriate, matching the phase 2 migration
(`ALTER TABLE ... ADD COLUMN IF NOT EXISTS ... DOUBLE NULL` /
`BIGINT NOT NULL DEFAULT 0`). No new table was added by phase 2, so there is
no gap in `EXPECTED_TABLE_NAMES` documentation to check. The upgrade path is
discoverable from the same document an operator would already consult for
any schema bump, and the refuse-to-start behaviour is generic rather than
per-field, which is appropriate — it isn't specific to this plan's columns.
No gap found.

#### 4. Undocumented-by-any-plan coding rule (`2e19bb1ea`)

`docs/developer_guide/coding_rules.md:481-529`, "A frozen model is not a
deep frozen model." Checked for accuracy against the code it describes:

- Claims `AgentOperationData.commands` is copied at its boundary — confirmed,
  `list(data.commands)` at `shakenfist/operations/agentoperation.py:141`.
- Claims `InstanceData`'s `disk_spec`, `video` and `side_channels` are
  copied — confirmed at `shakenfist/instance.py:403` (`list(data.disk_spec)`),
  `:410` (`dict(data.video)`), `:416` (`list(data.side_channels)`).
- Claims `SideChannelExecutorJob.__init__` now copies rather than aliases —
  confirmed, `self.commands = list(agentop.commands)` at
  `shakenfist/daemons/sidechannel/main.py:622`, with an inline comment at
  the site cross-referencing the same defect.

All three claims check out. **Discoverability:** the rule is
cross-referenced from `docs/developer_guide/database_internals.md:104-107`
("... see 'A frozen model is not a deep frozen model' in ..."), which is
where a reader investigating the object cache would already be looking, and
it sits in `coding_rules.md` alongside every other real-defect rule (the
file's own convention is a flat list of headings with no index, so this
is consistent with how every other rule in the file is surfaced). No gap
found.

#### 5. Plan hygiene

- `docs/plans/index.md:110` reads "In progress | 8 of 9" — correct, matching
  phases 0-7 Complete / phase 8 In progress in
  `PLAN-agent-operation-deadlines.md`'s Execution table (confirmed at
  lines 610-618). This is step 8a's already-applied correction (decision 8);
  confirming it, not re-deriving it.
- `docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md`: zero
  unticked `- [ ]` boxes (`grep -c '^- \[ \]'` → 0); `grep -n "has not been
  pushed"` → no hits. Both Definition-of-done checks from the phase-8 plan
  hold.
- Every phase plan 00 through 05 (06 lives in client-python, 07 checked
  above) has zero unticked Definition-of-done boxes.
- Master plan's phase 6 row (line 616) correctly names both server-repo PRs
  (#4005, #4015) alongside the client-python plan, per F3's correction.
- No deferred items were found unlisted; the two genuinely undocumented
  open defects (#3995, #4039) are explicitly out of this step's scope —
  step 8h's job per decision 6.

No plan hygiene findings.

#### 6. Accuracy sweep of `docs/operator_guide/agent_operations.md`

Read in full against the code paths it describes:

- One-executor-per-instance claim — matches `Instance.agent_operation_next()`'s
  docstring and the dispatcher's skip-if-live-executor behaviour
  (`shakenfist/instance.py:2606-2626`).
- The three enforcement points (dequeue, preflight, executor) — matches
  `agent_operation_next()`, the preflight promotion task, and
  `SideChannelExecutorJob.expire_if_out_of_budget()`.
- `AGENT_OPERATION_MAX_ATTEMPTS` default 3, "counting the first attempt plus
  retries" — matches `config.py:303` and its own extensive inline comment.
- Retry conditions (retryable command list, deadline not passed, attempt cap
  not reached) — matches `resolve_abandoned_operation()`
  (`shakenfist/daemons/sidechannel/main.py:528-`) and its docstring.
- Reaper's three recovered situations and its 30-second per-instance rate
  limit — matches `Monitor.reap_instance_executors()` and
  `EXECUTOR_REAP_INTERVAL = 30` (`shakenfist/daemons/sidechannel/main.py:76`),
  including the two acknowledged blind spots (no live monitor;
  `deadline_seconds=0` wedge before connecting).
- The NULL-deadline anchor-restarts-per-transition claim is not on this page
  directly, but the page correctly defers detail to
  `docs/operator_guide/database.md`, which does state it and matches
  `effective_deadline()`'s docstring verbatim (see task 1 above, and the
  `91d565a05` fix which added the anchor cache without changing this
  semantic).
- "What to tune" section's framing (effective default now tighter than the
  former 900-second backstop) matches the release notes and the removed
  `AGENT_OPERATION_EXECUTION_TIMEOUT` constant.

No inaccurate statement found on this page. It reads as current against the
code including the three out-of-branch defect fixes.

#### Summary of what was read

`docs/operator_guide/agent_operations.md` (full), `docs/developer_guide/api_reference/instances.md`
(lines 640-730), `docs/developer_guide/api_reference/agentoperations.md`
(full), `docs/operator_guide/database.md` (schema table + lines 1084-1166,
570-745), `docs/release_notes/v07-v08.md` (lines 700-840),
`docs/developer_guide/state_machine.md` (Agent Operations section + diagram),
`docs/developer_guide/coding_rules.md` (the new rule, lines 481-529),
`docs/developer_guide/database_internals.md` (cross-reference at 104-107),
`docs/plans/PLAN-agent-operation-deadlines.md` (Execution table, phase 6/7/8
rows), `docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md`
(Definition of done, What remains), `docs/plans/index.md:110`, `README.md`,
`AGENTS.md`, `ARCHITECTURE.md` (diffed against all twelve merges), plus the
relevant code: `shakenfist/config.py:230-320`,
`shakenfist/operations/agentoperation.py` (state_targets, `effective_deadline`,
`deadline_passed`, `attempts`, `_db_get`), `shakenfist/instance.py`
(`agent_operation_next`, static-value copies), `shakenfist/daemons/sidechannel/main.py`
(`SideChannelExecutorJob`, `resolve_abandoned_operation`,
`reap_instance_executors`, `expire_if_out_of_budget`), and the diff of
`91d565a05` and `2e19bb1ea`.

### Cross-repository contract

Step 8g of `PLAN-agent-operation-deadlines-phase-08-push-audit.md`, per
decision 3. Read-only in both repositories; nothing was edited or committed
in either. Server tree is the worktree
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/shakenfist-wt-aod-08`
(branch `agent-operation-deadlines-phase-08-push-audit`); client tree is
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python` at `develop`.

Result: **1 blocking, 7 advisory.**

#### What was actually read

**Client repository (`client-python`)**

* `git diff 4557100^1 4557100 --stat`, then the full diff of
  `shakenfist_client/apiclient.py`, `shakenfist_client/commandline/instance.py`
  and `shakenfist_client/main.py`.
* Resulting tree: `shakenfist_client/apiclient.py` lines 121-135
  (`AgentAwaitTimeout`, `AgentCommandError`, `AgentOperationFailed`), 195-203
  (`_is_error_state`, `TERMINAL_AGENT_OPERATION_STATES`), 303-313
  (`_collect_capabilities`, `check_capability`), 1276-1330 (`_await_agentop`),
  1327-1372 (`_add_agentop_timing`, `_remaining_agentop_budget`), 1380-1408
  (`_agent_failure_context`, `_enriched_agent_failure`), 1409-1454 (the three
  creating helpers), 1704-1734 (`await_agent_ready`), 1735-1828
  (`await_agent_command`), 1830-1876 (`await_agent_fetch`).
* `shakenfist_client/commandline/instance.py` lines 28-50
  (`_warn_if_timing_unsupported`) and 826-930 (the three verbs and their
  options).
* `shakenfist_client/main.py` lines 113-178 (`GroupCatchExceptions`).
* `docs/plans/PLAN-agent-operation-deadlines-phase-06-client.md` in full,
  including both *Corrections applied in review* sections and *Future work*.
* `AGENTS.md` (lines 95-125), `ARCHITECTURE.md:86`, `CLAUDE.md:90-120`,
  `README.md`, `docs/` listing.
* Greps: `AgentCommandError|AgentOperationFailed|AgentAwaitTimeout` across
  `*.py` and `*.md`; `except.*Agent` across `*.py`; `expired` across
  `shakenfist_client/`; `/agent/` across `apiclient.py`;
  `get_agent_operation|_await_agentop|'state'\]` across `apiclient.py`.
* Test inventory only (not line-by-line):
  `shakenfist_client/tests/test_client_apiclient.py` (the terminal-state
  parameterisation at `:1193-1198`, `TERMINAL_FAILURE_STATES = ('error',
  'expired', 'deleted')`), `test_client_main.py:255-270`,
  `test_client_commandline_instance.py`.
* Executed `click.INT` negative-value probe in a scratch script (not in the
  repository) to confirm CLI parsing behaviour.

**Server repository (`shakenfist-wt-aod-08`)**

* `docs/plans/PLAN-agent-operation-deadlines-phase-08-push-audit.md` in full;
  the *Future work* section of
  `docs/plans/PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md`.
* `shakenfist/external_api/base.py:227-327` (`agent_operation_timing`,
  `_timing_seconds`), `:1205-1245` (`log_request`'s body merge).
* `shakenfist/external_api/instance.py:80-94` (the two shared parameter
  descriptions), `:1810-1860` (put), `:1895-1935` (get), `:1955-1995`
  (execute).
* `shakenfist/external_api/app.py:296-397` (`ConditionalCapability`,
  `API_CAPABILITIES`, `advertised_capabilities`, `render_capabilities`,
  `Root`).
* `shakenfist/config.py:240-315` (the three constants).
* `shakenfist/operations/agentoperation.py:25-90` (`STATE_EXPIRED`,
  `TERMINAL_STATES`, `state_targets`), `:187-214` (`external_view`),
  `:320-370` (`expire`, `fail`).
* `shakenfist/baseobject.py:629-657` (`error` property and setter),
  `:690-707` (`_external_view`).
* `shakenfist/deploy/shakenfist_ci/base.py:28-52`
  (`AGENT_OPERATION_FAILURES`), `:475-525` (both `except` sites).
* `docs/operator_guide/agent_operations.md:85-125`;
  `docs/release_notes/v07-v08.md:730-840`.
* Greps: the three exception names across `*.py` and `*.md`;
  `await_agent_command(|await_agent_fetch(` across `shakenfist/`;
  `deadline_seconds|progress_timeout_seconds` across
  `external_api/instance.py`.
* Also grepped `client-python-k3s/`, `sfui/` and `client-python-ova/` for the
  three exception names — **no hits**, so those two named downstream consumers
  carry no `except` clause that phase 6 could have narrowed.

---

#### Question 1 — can the client produce a `deadline_seconds` the server 400s?

**The two numbers.** The server publishes `{'minimum': 0}` on
`deadline_seconds` and `progress_timeout_seconds` at
`shakenfist/external_api/instance.py:1823, 1826, 1904, 1906, 1963`. There is
**no published maximum**, so no upper bound can be violated. The 400 guard is
`_timing_seconds()` at `shakenfist/external_api/base.py:284-326` and rejects
exactly four things: a JSON boolean (`:299`), a non-numeric (`:303`), a
non-finite float — `inf`/`NaN` (`:317`), and a negative (`:322`).

**The derived path cannot reach any of those.** Every derived value comes from
`Client._remaining_agentop_budget()`
(`client-python/shakenfist_client/apiclient.py:1358-1372`):

```python
return max(1, round(timeout - (time.time() - start_time)))
```

`round()` on a float returns an `int`, and `max(1, ...)` floors it at 1. So:

* Very large `timeout` — an int of any magnitude; `float()` of it is finite
  and positive. Accepted.
* `timeout=0` — `max(1, round(-elapsed))` is `1`. Accepted (and the operation
  gets a 1-second server deadline, which is the documented intent of the
  floor).
* `timeout` negative — same, `1`. Accepted.
* Rounding vs truncation — `round()` (banker's rounding), never `int()`
  truncation, so no value can be pushed below the floor. Both `await_agent_command`
  (`:1749`) and `await_agent_fetch` (`:1837`) are the only two call sites.
* `_await_agentop` derives nothing at all. Decision 3 of the phase-6 plan was
  reversed in review (`Corrections applied in review`, first bullet): the
  async-strategy-derived deadline was removed and `_add_agentop_timing()`
  (`:1327-1356`) now sends only what a caller passed. So the CLI's default
  (`pause`, 60 s) does **not** become a `deadline_seconds` — this is the
  hazard the phase-6 review already caught, and it is genuinely gone.

The only non-derived hazard is the CLI, below (**CR-2**).

**Finding CR-2 — `click.INT` accepts a negative `--deadline`, and for
`instance upload` the 400 arrives after the transfer.** *Advisory. Fix in
`client-python`.*

`client-python/shakenfist_client/commandline/instance.py:826, 832, 872, 912,
917` all declare `type=click.INT, default=None` with no range. Verified by
running click: both `--deadline=-5` and `--deadline -5` parse to `-5`
(exit code 0). The value is passed straight through
`_add_agentop_timing()`'s `is not None` test onto the wire, and the server
returns `400 deadline_seconds must not be negative`, surfaced by
`main.py:118` as `Malformed Request: ...` with exit 1.

Concrete scenario:
`sf-client instance upload --deadline=-1 myinst ./4GB.qcow2 /tmp/x`.
`instance_upload()` (`:837`) calls `_warn_if_timing_unsupported()` first —
which the second phase-6 review round deliberately moved *above* the transfer
so a user is not told too late — then uploads the whole artifact
(`:845-861`), and only then calls `instance_put_blob()` (`:864`), which
400s. The user has paid for a 4 GB transfer to learn about a typo the client
could have caught before argument parsing finished. The published
specification says `minimum: 0`; `click.IntRange(min=0)` is the one-line fix
and it makes the client honour the bound it can already see.

Note also that the CLI help text (`:827-831` etc.) says "0 disables this
budget" but never says the value must be non-negative, so nothing warns the
user off.

**Not findings, but recorded.** `_remaining_agentop_budget(start, float('inf'))`
raises `OverflowError` from `round()` rather than sending anything, and
`timeout=None` raises `TypeError`. Neither is reachable: both call sites take
`timeout` from a signature defaulting to `120` and no caller in either tree
passes `None` or a non-finite value. Worth knowing only because PR #369's
`create_instance(timeout=None)` convention makes `None` a plausible thing for
a future caller to try here.

---

#### Question 2 — does the client handle every terminal state?

**Yes, exactly, including `expired`.**

Authority: `shakenfist/operations/agentoperation.py:41-43` —
`TERMINAL_STATES = (STATE_COMPLETE, STATE_ERROR, STATE_EXPIRED, STATE_DELETED)`.
The `state_targets` map at `:56-75` confirms `expired` is reachable from
`initial`, `preflight`, `queued` and `executing`, and that only `deleted`
follows any of the four.

Client: `TERMINAL_AGENT_OPERATION_STATES = frozenset({'complete', 'error',
'expired', 'deleted'})` at `apiclient.py:202`, with a comment naming the
server module as the authority and saying the duplication is deliberate. Set
equality holds.

**Fail-fast is real and single-sourced.** `_await_agentop()`
(`apiclient.py:1300-1305`) tests membership on the *first* poll, before any
`time.sleep(1)`, and raises `AgentOperationFailed` for the three non-`complete`
members. Critically, the second review round deleted the outer
"wait for the operation to be complete" loops from both `await_agent_command`
and `await_agent_fetch` (see the diff hunks at old `:1583-1590` and
`:1653-1658`), so `grep -n "get_agent_operation\|_await_agentop"` shows
`_await_agentop()` at `:1301-1325` is now the **only** place in the client
that polls an agent operation's state. There is no second loop that could
still poll to a full timeout. That is client-python#363 genuinely fixed, and
`expired` is covered by the same code path as `error`, not bolted on beside it.

Test coverage confirms it rather than assuming: `test_client_apiclient.py:1198`
parameterises `TERMINAL_FAILURE_STATES = ('error', 'expired', 'deleted')` and
`:1193` names #363 in the docstring. `expired` is not a special case in either
the code or the tests.

**Finding CR-4 — the results and blob loops can still be entered already
expired, and then report the wrong thing.** *Advisory. Fix in `client-python`.*

`apiclient.py:1782` (`await_agent_command`), `:1856` and `:1869`
(`await_agent_fetch`) all bound themselves by `while time.time() - start_time
< timeout` against the *same* `start_time` that `_remaining_agentop_budget()`
already spent. `_await_agentop()`'s own deadline is `time.time() + remaining`
computed **after** the POST returns, and `round()` can round the remaining
budget up by half a second, so `_await_agentop()` may legitimately still be
polling at `start_time + timeout + POST-latency + 0.5`.

Concrete scenario: `await_agent_command(uuid, cmd, timeout=120)` where
`await_agent_ready()` returns at 118 s (its own loops break after a 5-second
sleep, so it can succeed at up to `timeout + 5`). `remaining` is 2. The
operation completes at 119.8 s. `_await_agentop()` returns it with `state ==
'complete'` but `results` not yet written. The results loop at `:1782`
evaluates `119.9 - 0 < 120` — true once, sleeps 5, then false — or, if
`await_agent_ready` overshot, zero iterations. The caller then hits
`raise AgentCommandError('operation returned no results')` at `:1791`, which
says the operation produced nothing when what actually happened is that the
caller's budget ran out. This is the residue of survey finding 5 / step 6d:
the hardcoded `120`/`60`/`60` literals are gone, but the shared clock that
made them wrong is still shared. `AgentAwaitTimeout` is the honest exception
here, and the CI suite's `AGENT_OPERATION_FAILURES` catches both so nothing is
currently mis-retried — this is a diagnosis problem, not a control-flow one.

**Not a finding, recorded.** `_await_agentop()` calls `get_agent_operation()`
at `:1325` with no handling for a `ResourceNotFoundException`. Phase 4 put
`expired` in `FINAL_OBJECT_STATES`, so an expired operation is swept for hard
deletion — but only after `CLEANER_DELAY`, by which time the client has long
since seen the `expired` state and raised. Not reachable in practice.

---

#### Question 3 — do the CLI flags name the units the server documents?

**Yes. No mismatch found.**

Server: `DEADLINE_SECONDS_DESCRIPTION`
(`shakenfist/external_api/instance.py:80-86`) and
`PROGRESS_TIMEOUT_SECONDS_DESCRIPTION` (`:88-93`) both say seconds, both say
what `0` means, both name the config default by constant name and value
(600 / 30).

Client: `--deadline` (`commandline/instance.py:826, 872, 912`) — "The wall
clock budget **in seconds** ... 0 disables this budget. Omit to use the server
default." `--progress-timeout` (`:832, 917`) — "The number of **seconds** of no
progress ... 0 disables this budget." Both go on the wire as
`deadline_seconds` / `progress_timeout_seconds` (`:864-865`, `:880`,
`:926-927`), which is the same unit and the same sentinel semantics.

The `--deadline` / `deadline_seconds` name difference is cosmetic: the flag's
first help clause names the unit. `instance upload`'s help correctly scopes
the budget to the agent operation ("once the upload of the artifact to the
cluster is complete") — which matches the server, since the deadline is
anchored at the `/agent/put` request, after the artifact upload. The
asymmetry holds: `execute` has `--deadline` only
(`:872-878`, no `--progress-timeout`), `instance_execute()` has no
`progress_timeout_seconds` kwarg and hard-codes `None` into
`_add_agentop_timing()` (`apiclient.py:1433`), matching the server's refusal
at `instance.py:1982-1983`.

**Finding CR-8 — the CLI help omits that queue and preflight time count
against the deadline.** *Advisory. Fix in `client-python`.*

Server (`instance.py:81-83`): "How many seconds after this request is
received the operation may continue to be dispatched or execute. **Queue time
and any preflight work count against it.**" Client help (`commandline/instance.py:827,
873, 913`): "The wall clock budget in seconds for the in-guest agent operation
which executes the command." A user reading only `--help` will size the flag
against execution time. On a busy hypervisor with a queued instance
executor, `--deadline 30` on a command that takes 5 seconds can still expire
before it is ever dispatched. One clause added to the help text closes it.

---

#### Question 4 — do the exception names mean the same thing on both sides?

**The mapping.** Server condition → client exception:

| Server outcome | Client exception | Raised at |
|---|---|---|
| operation reaches `error` (agent reported failure, `fail()`) | `AgentOperationFailed`, `op_view['state'] == 'error'` | `apiclient.py:1303` |
| operation reaches `expired` (`expire()`, either budget) | `AgentOperationFailed`, `op_view['state'] == 'expired'` | `apiclient.py:1303` |
| operation reaches `deleted` | `AgentOperationFailed`, `op_view['state'] == 'deleted'` | `apiclient.py:1303` |
| operation still in flight when the *client's* budget runs out | `AgentAwaitTimeout` | `apiclient.py:1773`, `:1851` |
| operation completed but the result is unusable (no results, unexpected stderr, no stdout/content blob) | `AgentCommandError` | `apiclient.py:1791, 1797, 1805, 1821, 1863, 1865` |

The mapping is documented in three places, all of them in the **server**
repository: `shakenfist/deploy/shakenfist_ci/base.py:29-48` (the best
statement of it, and correct), `docs/operator_guide/agent_operations.md:99-104`,
and `docs/release_notes/v07-v08.md:825-831`. It is documented nowhere in
`client-python` outside `AGENTS.md`/`CLAUDE.md`, which are agent instructions,
not caller documentation — see **CR-6**.

**`except` clauses in both trees.** Greps for the three names across `*.py`:

* Server: three `except` sites total. `shakenfist_ci/base.py:487` and `:516`
  both catch `AGENT_OPERATION_FAILURES`, which at `:49-52` is the full triple
  `(AgentCommandError, AgentOperationFailed, AgentAwaitTimeout)` with a
  comment (`:41-48`) explaining that it is for catching, not asserting, and
  why it is deliberately not narrowed. Phase 7's regression is repaired and
  the comment prevents its recurrence. The two assertion sites
  (`smoke_ci_tests/test_agentops.py:265`,
  `guest_ci_tests/test_agentops.py:276`) correctly use
  `AgentOperationFailed` alone. **Nothing narrower than intended found.**
* Client: three `except` sites. `main.py:163` catches
  `AgentOperationFailed` in `GroupCatchExceptions` (so a terminal state is an
  error message and exit 1, not a traceback);
  `apiclient.py:1754` and `:1842` catch it internally to re-raise through
  `_enriched_agent_failure()`. **Nothing narrower than intended found.**
* `client-python-k3s`, `sfui`, `client-python-ova`: zero hits for any of the
  three names, so the phase-6 plan's named third-party consumer carries no
  affected `except`.

**Can a caller distinguish `expired` from a plain failure?** Yes, at the
granularity question 4 asks for, but only through a dict subscript.

**Finding CR-6 — `AgentOperationFailed` exposes the state only as
`e.op_view['state']`, which one path degrades to the string `'unknown'`, and
the client repository documents none of it.** *Advisory. Fix in
`client-python`.*

`AgentOperationFailed.__init__` (`apiclient.py:132-135`) stores `op_uuid` and
`op_view` but no `state`. A caller wanting "retry `expired`, do not retry
`error`" must write `e.op_view.get('state')`. Both enriching call sites
(`:1755-1761`, `:1843-1845`) substitute `{'uuid': e.op_uuid, 'state':
'unknown'}` when `op_view` is falsy, so a defensive caller must also handle
`'unknown'` — a value no server state machine can produce. A `state` property
on the exception would make the discriminator part of the API instead of part
of a payload.

Compounding it: `client-python`'s `AGENTS.md:121-125` still says "There is
deliberately no `docs/` page for the timing model yet. Phase 7 of the master
plan writes it once, for the server and client halves together." Phase 7 has
merged (`4a122bcd3`) — and wrote that page in the **server** repository. A
library caller of `shakenfist_client` therefore has `--help` text and nothing
else; `client-python/docs/` contains only `namespace-claims.md`,
`vdi-console.md` and `plans/`. The forward reference is now stale in a way
that reads as "still to come" when it is in fact "done, elsewhere".

**Finding CR-5 — *which* budget expired an operation is unreachable through
the API, so no client can tell a wall-clock expiry from a progress-timeout
expiry.** *Advisory. Fix in `shakenfist`.*

`expire()` (`shakenfist/operations/agentoperation.py:320-349`) records the
reason as the state row's message. `AgentOperation.external_view()` (`:187-214`)
returns `error_message = self.error`, and `baseobject.error`
(`shakenfist/baseobject.py:629-638`) returns `None` unless the state value
ends in `error` — `expired` does not. `_external_view()`
(`baseobject.py:690-707`) renders `State` to its value string via
`BaseExternalView`, dropping the message. So for an expired operation the
external view carries `state='expired'` and `error_message=None`, and the
reason exists only in the instance's event log.

`docs/operator_guide/agent_operations.md:92-99` states this honestly. But
`docs/release_notes/v07-v08.md:753` tells a reader "`expired` means a budget
ran out, **and the state's message says which**" without saying that message
is not on the wire — which is exactly the sentence an integrator would read
before writing the discriminator they cannot write. A retry policy that wants
to widen `progress_timeout_seconds` on a stall but not on a genuine
deadline overrun cannot be written against today's API.

---

#### Question 5 — version skew and the capability token

**Does the client consult the token? Yes, on every path that can send the new
parameters.**

`_add_agentop_timing()` (`apiclient.py:1348-1356`) returns early — sending
neither key — unless `self.check_capability('agentoperation-deadlines')`. All
three creating helpers route through it and there is no other POST to an
`/agent/` endpoint in the client: `grep -n "/agent/" apiclient.py` returns
exactly `:1420` (put), `:1437` (execute), `:1452` (get), and each builds its
`data` dict through `_add_agentop_timing()` immediately before the POST. The
token is advertised unconditionally (a plain string, not a
`ConditionalCapability`) in the `instances` family at
`shakenfist/external_api/app.py:372`, with a comment naming precisely which
parameter goes on which endpoint. The token is **not** advertisement nobody
reads.

**New client, old server.** `check_capability()` is a substring test against
`self.root_html` (`apiclient.py:310-312`), so an old server's root page
contains no such token and nothing is sent — behaviour identical to
pre-phase-6. This matters because `log_request()`
(`shakenfist/external_api/base.py:1222-1231`) merges the JSON body into
handler kwargs unconditionally, so an undeclared `deadline_seconds` reaching
an old handler is a hard failure, not an ignored field. The gate is load-bearing
and it is correctly placed. The CLI additionally warns on stderr when the
*user typed a flag* the server cannot accept
(`commandline/instance.py:28-50`), while the library stays silent — which is
right, because `await_agent_command()` passes a deadline on every call and
must keep working against an old cluster.

**Old client, new server.** The old client sends nothing and gets the server
defaults. This is where the release note is wrong — see **CR-1**.

**Finding CR-1 — the release note's compatibility guarantee is false in both
directions, and contradicts a bolded paragraph 74 lines above it in the same
section.** *Blocking. Fix in `shakenfist`.*

`docs/release_notes/v07-v08.md:832-836`:

> Both changes are gated on the server advertising the
> `agentoperation-deadlines` capability, so an old client against a new
> cluster and a new client against an old cluster both keep behaving as they
> did before this release.

Both halves are untrue.

*An old client against a new cluster does not keep behaving as before.* An
old client sends no parameters, so `agent_operation_timing()`
(`shakenfist/external_api/base.py:266-279`) applies
`AGENT_OPERATION_DEFAULT_DEADLINE` (600 s, `config.py:240`) and, for `put`
and `get`, `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` (30 s, `config.py:259`)
— replacing a hardcoded 900-second backstop and *no* progress timeout at all.
The same document says so at `:758-765`: "**The effective default is tighter
than the behaviour it replaces.** Where a welcomed operation previously had
900 seconds of execution, it now has 600 seconds counted from request
receipt". Issue #3995 is the demonstrated consequence: a legitimate 471 MiB
`get-file` expired because the guest answered the stat in 20 ms and then sent
nothing for 30 s. A reader who reaches `:832` first concludes there is
nothing to do and does not raise the constant.

*The fail-fast change is not gated at all.* Decision 7 of
`client-python/docs/plans/PLAN-agent-operation-deadlines-phase-06-client.md`
says so explicitly ("The fail-fast change (decision 5) is *not* gated"), and
the code agrees: `_await_agentop()`'s terminal-state test
(`apiclient.py:1301-1305`) has no `check_capability()` anywhere near it.
A new client against a pre-phase-3 server that produces `error` or `deleted`
raises `AgentOperationFailed` today where it previously polled to its own
timeout and raised `AgentCommandError` (fetch) or `AgentAwaitTimeout`
(execute).

Concrete scenario: an operator upgrades `sf-client` ahead of the cluster,
reads `:832`, and leaves an in-house wrapper's
`except apiclient.AgentCommandError:` retry unchanged. The first `agent/get`
whose file does not exist now escapes the retry as an uncaught
`AgentOperationFailed`. That is the *identical* failure mode phase 7's own
Future work records against `base.AGENT_OPERATION_FAILURES` — "changing which
exception a client raises silently narrows every `except` tuple downstream
that was written against the old one" — and this sentence tells downstream
consumers not to look.

Graded blocking rather than advisory because it is a false compatibility
guarantee currently on `develop`, in the one document a downstream integrator
reads to decide whether they must change code, about a hazard that has
already caused one silent regression inside this repository. The fix is a
paragraph rewrite in `docs/release_notes/v07-v08.md` — trivial, and therefore
within step 8h's "fix blocking findings here" mandate. Recommended
replacement content: the parameter-*sending* half is gated; the fail-fast
half is not and is a deliberate behaviour change (already stated correctly at
`:825-831`); and old clients do inherit the tighter defaults, cross-referencing
`:758-765`.

**Finding CR-3 — `await_agent_fetch()` propagates a deadline but never a
progress timeout, so every fetch is pinned to the server's 30-second default
regardless of the caller's budget.** *Advisory. Fix in `client-python`.*

`await_agent_fetch()` (`apiclient.py:1830-1845`) calls
`instance_get(instance_uuid, path, deadline_seconds=remaining,
await_seconds=remaining)`. `instance_get()` accepts
`progress_timeout_seconds` (`:1441-1443`) but the helper never passes one and
exposes no way for a caller to. `await_agent_command()` is the same shape,
though harmless there — `agent/execute` is not progress-capable and the server
stores `0.0` for it (`instance.py:1982-1983`).

Concrete scenario, which is #3995: `await_agent_fetch(uuid,
'/tmp/471MiB.img', timeout=600)`. The client asks for a 600-second wall-clock
budget and gets one. The guest stats the file in 20 ms, then spends 30+
seconds producing the first chunk; the executor's progress check fires on the
server's 30-second default and the operation goes to `expired`. The caller
asked for ten minutes and was killed at thirty seconds by a budget it was
given no way to name. Adding `progress_timeout_seconds=None` to the signature
and passing it through — the pattern the three creating helpers already
follow — restores the caller's control without touching the default.

This is *not* the same as retuning `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT`,
which decision 7 of this phase puts out of scope. It is the missing lever that
would let CI (or any caller with a known-large transfer) opt out per call while
the measurement #3995 asks for is still pending.

**Finding CR-7 — the capability is cached once per `Client` and can be stale
across a rolling API upgrade.** *Advisory. Fix in `shakenfist` (or accepted).*

`_collect_capabilities()` (`apiclient.py:305-308`) fetches the root page once,
in the constructor, and `check_capability()` substring-matches the cached
text. Behind a load balancer during a rolling upgrade, a client can read the
root page from an upgraded worker, see `agentoperation-deadlines`, and then
POST `deadline_seconds` to a worker that has not restarted yet — where
`log_request()`'s `kwargs.update(j)` (`base.py:1231`) puts an undeclared kwarg
in front of a handler that has no such parameter. This is a property of the
capability mechanism generally rather than of phase 6, and the exposure window
is one deploy, but phase 6 is the first feature to depend on the token for
request *validity* rather than for a graceful degradation, which is what
raises it above noise. Listing it so it is a decision rather than an oversight.

---

#### Summary of findings

| Id | Grade | Repository | File | Question |
|----|-------|-----------|------|----------|
| CR-1 | **blocking** | `shakenfist` | `docs/release_notes/v07-v08.md:832-836` | 5 |
| CR-2 | advisory | `client-python` | `shakenfist_client/commandline/instance.py:826, 832, 872, 912, 917` | 1 |
| CR-3 | advisory | `client-python` | `shakenfist_client/apiclient.py:1830-1845` | 1 / 5 |
| CR-4 | advisory | `client-python` | `shakenfist_client/apiclient.py:1782, 1856, 1869` | 2 |
| CR-5 | advisory | `shakenfist` | `shakenfist/operations/agentoperation.py:187-214`, `shakenfist/baseobject.py:629-638` | 4 |
| CR-6 | advisory | `client-python` | `shakenfist_client/apiclient.py:129-135`, `AGENTS.md:121-125` | 4 |
| CR-7 | advisory | `shakenfist` | `shakenfist/external_api/base.py:1222-1231` (with `client-python/shakenfist_client/apiclient.py:305-312`) | 5 |
| CR-8 | advisory | `client-python` | `shakenfist_client/commandline/instance.py:827, 873, 913` | 3 |

No GitHub issues were filed; per the step brief the operator files them.
Five findings would be filed against `client-python` (CR-2, CR-3, CR-4, CR-6,
CR-8) and three against `shakenfist` (CR-1, CR-5, CR-7).

#### Clean headings

Stated explicitly per decision 9, each alongside what was examined:

* **Terminal-state coverage is exact.** `TERMINAL_AGENT_OPERATION_STATES`
  (`apiclient.py:202`) equals `AgentOperation.TERMINAL_STATES`
  (`agentoperation.py:41-43`); `state_targets` (`:56-75`) reaches no fifth
  terminal state. `expired` is handled by the same branch as `error`, on the
  first poll, and is parameterised in the client's tests
  (`test_client_apiclient.py:1198`).
* **No `except` clause in either tree is narrower than intended.** All six
  sites read (server `shakenfist_ci/base.py:487, 516`; client `main.py:163`,
  `apiclient.py:1754, 1842`), plus the two assertion sites and the
  `AGENT_OPERATION_FAILURES` tuple at `base.py:49-52`. Phase 7's regression is
  repaired and commented against recurrence. `client-python-k3s`, `sfui` and
  `client-python-ova` contain none of the three names.
* **Units agree.** `deadline_seconds`/`progress_timeout_seconds` (server
  `instance.py:80-93`) and `--deadline`/`--progress-timeout` (client
  `commandline/instance.py:826-834, 872-877, 912-921`) are both seconds, with
  the same `0` sentinel and the same "omit for the server default" rule; the
  `execute`-has-no-progress-timeout asymmetry holds on both sides.
* **The capability gate is real, correctly placed, and consulted on every
  path.** Three `/agent/` POSTs, three `_add_agentop_timing()` calls, one
  `check_capability()` inside it.

## Future work

Recorded here at planning time; step 8h adds to it.

* The two `test_agentops.py` files in `guest_ci_tests` and
  `smoke_ci_tests` remain near-exact copies with one number
  different, as phase 7's Future work records. This audit does not
  collapse them.
* `docs/operator_guide/` has two pages absent from `mkdocs.yml`'s nav
  (`credential_rotation.md`, `vdi_console_tokens.md`), noticed by
  phase 7 and unrelated to this plan.

## Back brief

Before step 8b runs, the implementing session confirms in its own
words:

1. What the audit baseline is, and why `git diff develop...HEAD`
   would have produced nothing.
2. Which two merges touching `shakenfist/operations/agentoperation.py`
   are deliberately excluded, and why.
3. That findings are recorded and filed rather than fixed, with the
   single exception of blocking findings.

**A gate before step 8h.** Step 8h both fixes blocking findings and
closes the plan out. If any finding is graded blocking, the
management session reviews the grade and the proposed fix *before*
the fix is written -- a blocking finding on merged code means
something is wrong on `develop` right now, and the choice between
fixing it here and filing it at high priority is the operator's, not
the audit's. If nothing is blocking, 8h proceeds without the gate.
