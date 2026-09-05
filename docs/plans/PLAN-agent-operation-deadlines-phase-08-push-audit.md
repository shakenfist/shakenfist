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
