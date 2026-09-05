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
