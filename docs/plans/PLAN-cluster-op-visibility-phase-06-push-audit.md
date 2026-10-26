# PLAN: Cluster op visibility phase 6 -- push audit

Planning effort: medium. Review effort: medium.

## Why this phase exists

Phases 1 to 5 landed in six pull requests over three months. Each was
reviewed on its own terms, and none was read beside the others. This
phase runs `PUSH-AUDIT.md` over all six, plus the client-python pull
request that carried phase 4's client half.

The plan's idea is a single rule, applied in many places: an operation
whose every task only measures is *observational*. It is recorded, but
it never becomes `last_cluster_operation`, never counts as pending,
never fails a CI wait, and failing it never errors its object. Phases
2 to 5 each wrote some of those places, and #4608 added another after
phase 3. Whether every place agrees is the question no single phase's
review could ask, so this audit adds it as its own heading (decision
6).

## Scope

**In scope.**

* The six merges in decision 1, under every `PUSH-AUDIT.md` heading:
  wave 1, the wave 2 mechanical sweep, the style judgment portion, and
  2a to 2d.
* Two headings of this plan's own: the coherence of the observational
  and pending rules (step 6h), and the client half of the contract,
  `shakenfist/client-python` #436 (step 6i).
* The master plan's Future work entries, none of which has an issue
  (survey finding 8). Disposition decides which become issues.

**Out of scope.**

* #4608 (`issue-fix-4585`) and #4621 (`issue-fix-4604`). Both fix
  defects that predate this plan, in code the plan touched, and the
  `Merged` column does not claim them (survey finding 5). #4608 does
  add a consumer of this plan's `observational_tasks` declarations,
  so step 6h reads that consumer at `$TIP` without auditing the pull
  request.
* The other later merges that touch audited files (survey finding 5).
  They matter only where they change whether a finding still holds,
  which decision 2 covers.
* Fixing what the audit finds. Decision 7 says where each kind of
  finding goes.
* Editing `shakenfist/client-python`. Findings there become
  client-python issues (decision 3).

## What the survey found

The master plan's Phase 6 section is short and accurate, and the
`Merged` column is complete for this repository. Two things are
corrected at source in the planning commit, so a later step does not
need to redo them:

* Phase 4's `Merged` cell now names the client pull request (finding
  3).
* The Phase 6 section, and the Planning effort section, now give this
  phase a planning effort, and the Phase 6 section says what this
  survey found (finding 10).

1. **All six ranges are merge commits on `develop`.** Each has two
   parents, and `git merge-base --is-ancestor <sha> origin/develop`
   holds for every one:

   | Phase | PR | Merge | Files | Insertions / deletions |
   |-------|----|-------|-------|------------------------|
   | 1, 1b | #3465 | `bc7c119bf` | 18 | +1196 / -177 |
   | 2 | #4574 | `9b098c6ac` | 32 | +2542 / -731 |
   | 3 | #4588 | `27ea18202` | 27 | +984 / -57 |
   | 4 | #4606 | `3642e7a75` | 31 | +1961 / -408 |
   | 4 | #4617 | `bdc91db78` | 4 | +44 / -5 |
   | 5 | #4650 | `93c31035a0` | 26 | +1060 / -129 |

   The counts include the plan documents. Generated stubs under
   `shakenfist/protos/` appear in #3465, #4574 and #4606.

2. **`git log` over these ranges is clean.** One branch merged
   `develop` into itself mid-flight. That was #3465, at `ecceeefd30`,
   whose second parent was already in the first parent's history.
   `git rev-list --count bc7c119bf^1..bc7c119bf` is therefore 13: the
   eleven plan commits, that merge, and the pull request's own merge.
   The other five ranges contain no merges. `git diff <sha>^1 <sha>` is still the authority (decision
   1), but an agent that reads commit messages for context reads only
   this plan's commits.

3. **Phase 4's client half was never audited, and the `Merged` column
   did not name it.** `shakenfist/client-python` #436 merged as
   `fdcfe6e53d`, a two-parent merge on client-python's `develop`. It
   touched four files, +422 / -2. It added
   `get_{instance,network,artifact}_clusteroperations()` and
   `await_no_pending_operations()`, and phase 5's CI helper now depends
   on the latter. The `plan-push-audit-phase` block says a phase landed
   in another repository is audited in that pull request and cited
   here. client-python has no `PUSH-AUDIT.md`, though, and #436 had
   only two rounds of automated review. There is no audit to cite.
   The cell now reads `` `3642e7a75` (#4606), `bdc91db78` (#4617),
   client-python `fdcfe6e53` (#436) ``. Step 6i reads that range
   (decision 3).

4. **Phase 1's range is three months older than the others, and later
   phases rewrote most of it.** #4650 replaced the CI helper it
   introduced in `deploy/shakenfist_ci/base.py`. #4574 reworked its
   by-target query and target writes. What survives of #3465 as
   written is chiefly:
   * the body-kwargs handling in `ClusterOperationsEndpoint.get()`
     (`external_api/clusteroperation.py:315`);
   * the atomic target write in `schema/operations/util.py`;
   * its tests.

   A finding against #3465 is more likely than any other to be "fixed
   since" (decision 2).

5. **Eleven later merges touch audited files.** Each is listed with
   the audited files it touched:

   | Merge | PR | Audited files touched |
   |-------|----|-----------------------|
   | `48849831cc` | #4591 | `docs/developer_guide/ci.md`, `docs/release_notes/v07-v08.md` |
   | `dd5723c3db` | #4595 | `database_internals.md`, `database.md`, `v07-v08.md`, `protos/database.proto`, `daemons/cluster/main.py`, `daemons/database/main.py`, `mariadb.py` |
   | `6a3f30767c` | #4581 | `ci.md` |
   | `9e005c674f` | #4608 | `database_internals.md`, `operations/node_inst_op.py` |
   | `b50aa3a13f` | #4609 | `daemons/cluster/main.py` |
   | `9efbff1481` | #4621 | `external_api/artifact.py`, `tests/external_api/test_outstanding_operations_all.py` |
   | `ad1662abb1` | #4612 | `ci.md` |
   | `d9a9134ed3` | #4615 | `instance.py`, `operations/node_inst_op.py` |
   | `54fed79bcf` | #4616 | `v07-v08.md` |
   | `a6cb54570e` | #4653 | `ci.md`, `database_internals.md`, `upgrades.md`, `v07-v08.md`, `daemons/cluster/main.py`, `daemons/database/main.py`, `mariadb.py`, `tests/mock_mariadb.py` |
   | `10e8e2d9bc` | #4620 | `external_api/{artifact,clusteroperation,instance,network}.py` |

   Five of them matter to this audit:
   * #4595 changed the deleted-object sweep, which #4650's docs
     describe.
   * #4608 made `NodeInstOp.dispatch_task()` read
     `observational_tasks` (`operations/node_inst_op.py:106`).
   * #4615 changed the instance delete path, which #4606 and #4617
     also changed.
   * #4620 changed the four endpoint modules.
   * #4653 changed `mariadb.py` and the database daemon.

6. **Merge CI runs this plan's two cluster tests on one lane.** #4653
   moved `ubuntu-2404-slim-primary` and `debian-13-slim-tier` to the
   weekly `scheduled-tests.yml`. As a result,
   `test_observational_sweeps` and `TestOutstandingOperationsBodyAll`
   run in the merge queue only on `debian-13-slim-primary`. Phase 5's
   real-cluster run predates that change, and ran them on three lanes.
   Step 6e weighs this.

7. **The observational and pending rules are written down in at least
   ten places.** Step 6h lists them. Two readers deliberately skip the
   observational filter, and each says why in a comment:
   * `_direct_get_cluster_operation_targets_for_object` (`mariadb.py:5299`),
     because the per-blob sweep must see its own checksum ops;
   * `_direct_get_recent_terminal_op_states_for_target` (`:5455`),
     whose comment says its only callers ask about `net_op` on
     networks, "which is never observational".

   The "still in flight" state list is written twice: as
   `_ACTIVE_OPERATION_STATES` (`mariadb.py:5402`), and as a literal
   `active_states` in `_direct_delete_stale_cluster_operation_targets`
   (`:5588`). The sweep test has its own `TERMINAL_STATES` set. Each
   is a place the rule can drift.

8. **None of the master plan's Future work entries has an issue.**
   `gh issue list --state all --search` for each entry's key terms
   (`enqueue_delete fails open`, `node_aop_op preflight instance`,
   `interfaces last_cluster_operation`, `pending_only blob sweep`,
   `user_waiting hot-plug latency`, `runs_after last_cluster_operation`)
   returns nothing. Two entries describe defects:
   * `Instance.enqueue_delete()` fails open on a listing error;
   * an agent operation's preflight does not target its instance.

   Both predate this plan, by their own account. They are seeded as
   S1 and S2 for disposition, together with S3, the four `runs_after`
   sites phase 4 left on the pointer.

9. **Phase 5's success-criteria walk is the starting position.** All
   ten criteria are met. The hot-plug one is met on a single smoke run
   (`40183572268`), which the phase 5 plan says cannot prove a race is
   gone.

10. **The master plan gives this phase no planning effort.** Its
    Planning effort section covers phases 1 to 5 only. This plan uses
    medium. The phase 6 section now says so, and points here.

Phase 5 itself is complete:
* Its Execution row and the `index.md` row agreed before the close-out.
* Its merge is an ancestor of `develop`.
* Its worktree and local branch were removed after the merge.
* Four falsifiable Definition of done items re-run clean on `develop`:
  * `await_no_pending_operations` appears once in `base.py`, and
    `observed_outstanding` or `terminal_states` does not appear;
  * the sweep test exists;
  * the "minute or two" grep finds nothing;
  * the `ci.md` section precedes "Creating instances".
* #4603 is closed, and its other deferrals are Future work entries.

## Decisions

1. **The baseline is the six ranges, read with `git diff`.** Each is
   `<sha>^1..<sha>`:

   | Phase | PR | Range |
   |-------|----|-------|
   | 1, 1b | #3465 | `bc7c119bf^1..bc7c119bf` |
   | 2 | #4574 | `9b098c6ac^1..9b098c6ac` |
   | 3 | #4588 | `27ea18202^1..27ea18202` |
   | 4 | #4606 | `3642e7a75^1..3642e7a75` |
   | 4 | #4617 | `bdc91db78^1..bdc91db78` |
   | 5 | #4650 | `93c31035a0^1..93c31035a0` |

   Wave 1's greps and the wave 2 mechanical sweep run once per range,
   and their output is pooled, as the runbook requires. Commit messages
   may be read for context, since survey finding 2 shows they are this
   plan's own.

2. **The judgment reviews read all six ranges together, and every
   finding is checked at `$TIP`.** `$TIP` is `develop` at
   `93c31035a0`, the commit this branch was cut from. Each candidate
   is recorded as one of:
   * **a finding**, if it is still true at `$TIP`;
   * **fixed since by `<sha>`**, if a later commit removed it, which
     is a result but not a finding;
   * **out of scope, filed**, if the defect is in a later commit but
     breaks something this plan built.

3. **The client half of the contract is read, not changed.** #436 is
   read with `git diff fdcfe6e53d^1 fdcfe6e53d` in
   `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python`,
   against what this repository publishes. client-python has no
   runbook. That range therefore gets the `plan-push-audit-phase`
   block's five-item floor, plus this contract's own questions (step
   6i). Nothing in that repository is edited from this phase, and
   findings become client-python issues. This follows decision 3 of
   [the agent operation deadlines
   audit](PLAN-agent-operation-deadlines-phase-08-push-audit.md), for
   the same reason. The defect class lives at the boundary, and no
   other reviewer sits on both sides of it.

4. **Every step runs as a sub-agent, at the runbook's model and effort,
   with one raised.** 2a goes to opus rather than sonnet. Its
   three-layer and pushdown checks span `mariadb.py`, which is 29,640
   lines, and the database daemon. The master plan's model guidance
   gives that much context to opus. The two added headings go to opus,
   because each is a narrow adversarial question (decision 6).

5. **The judgment agents are read-only and run in parallel.** They do
   not edit files, and do not run `pre-commit`, `tox` or `stestr`.
   pre-commit's stash covers the whole repository, so parallel agents
   in one worktree can silently revert each other, and stestr writes
   into `.stestr/`. An agent whose finding depends on running a test
   names the test, and the management session runs it. Only the
   management session writes to this plan. It records each report
   after checking at least one of that report's claims against the
   file.

6. **Two headings are added: rule coherence (6h) and the client
   contract (6i).** Neither fits a runbook heading. 2a is a broad
   quality sweep, and the coherence question is narrow. Take two
   expressions of the rule, and build an operation that one admits and
   the other refuses. Then ask whether any real enqueue site can
   produce it. The client contract is the cross-repository question in
   decision 3.

7. **The audit's pull request records findings, and a separate pull
   request fixes the blocking ones.** This is what the
   `plan-push-audit-phase` and `plan-phase-landing` blocks require.
   * **Blocking** means a defect on `develop` now, in this plan's
     work. Its issue is filed with the `automated-fix-attempted` label
     at filing time, so the autofixer does not race the fix. The fix
     lands in a follow-up pull request, cut from `develop` in its own
     worktree as soon as the finding is graded. That pull request also
     carries this phase's close-out: its `Merged` cell, and Complete
     for the plan.
   * **Advisory** findings are filed as issues. Each disposition, in
     this plan, gives the reason it is not this plan's to fix. That is
     declined in writing.
   * **Predating defects**, S1 and S2 among them, are filed as issues
     and are not findings. A Future work entry that names no defect,
     such as the pagination or single-RPC ideas, stays a Future work
     entry. Its disposition says so.
   * **No blocking findings** means no follow-up pull request is
     opened for the sake of one cell. Phase 6 sets itself and the plan
     to Complete in its own pull request, and records no `Merged`
     cell.

8. **Known issues are cited, not refiled.** Before filing anything, the
   management session runs `gh issue list --state all --search` for
   the finding's key terms, and writes the queries into the
   disposition. A finding that matches an open issue is recorded
   against it, and gets a comment only if it adds evidence the issue
   lacks. Issues to check first:
   * #3922, errored operations never hard deleted;
   * #3864, terminal operations hard deleted;
   * #4407, the guest `ssh.service` power-cycle flake;
   * #3278, the CI coverage backlog;
   * #3432, lock lifetime noise.

9. **Wave 1 is relaxed the way the earlier audits relaxed it.**
   `pre-commit` and `tox` run against `$TIP` plus this phase's prose,
   so a failure there is a failure on `develop`. It is recorded,
   checked for whether a range touched the failing module, and then
   wave 2 proceeds. The style greps, the genprotos freshness check and
   `tools/mermaid-lint.sh` are about the ranges, so they are not
   relaxed.

10. **A clean heading names what it read.** "Nothing found" is
    accepted only next to a list of the functions, files or tests
    examined. If the whole audit is clean, the master plan says so in
    one sentence.

11. **The record holds verdicts, paths, commands and counts only.**
    This is a public repository. The management session checks the
    text this phase adds for private hostnames, addresses and
    credentials before each commit. CI run links and issue numbers are
    public, and may be cited.

## Step plan

Every step after 6a takes `$RANGES` to mean the six ranges in decision
1, and `$TIP` to mean `93c31035a0`. The worktree is
`/srv/kasm_profiles/mikal/vscode/src/shakenfist/shakenfist-wt-cov-p06`.
Generated files under `shakenfist/protos/` are exempt from judgment
review, because 6b's genprotos check covers them.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | opus | none | **Done in the planning commit.** Write this plan. Correct survey findings 3 and 10 at source in the master plan's Execution table and Phase 6 section. Link this file from the Execution table and from `docs/plans/index.md`, with status In progress. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: "Plan the cluster op visibility push audit." |
| 6b | low | sonnet | none | Wave 1 and the wave 2 mechanical sweep, in the worktree, running alone before any other step. Run `pre-commit run --all-files`, then `tox`, then `tools/mermaid-lint.sh` (every range touches markdown; check its exit status directly, never through a pipe), then `tox -e genprotos && git diff --exit-code shakenfist/protos` (#3465, #4574 and #4606 change `protos/database.proto`). Then, **for each of the six ranges in decision 1 separately**, run the four style greps from `PUSH-AUDIT.md`'s wave 1 and the five commands of its wave 2 mechanical sweep, substituting `<sha>^1 <sha>` for `$RANGE`. Report every command and its literal output, or "no output", grouped by range. If `pre-commit` or `tox` fails, report the failing test names, and say whether any range touched the failing module (`git diff --name-only <sha>^1 <sha>`). Then stop (decision 9). Edit nothing; if genprotos leaves a diff, restore the files from a copy taken before running it, not with `git checkout`. Commit subject, for the management session's record: "Record wave 1 of the cluster op visibility audit." |
| 6c | low | sonnet | none | Style conformance, judgment portion, read-only (decision 5). Apply `PUSH-AUDIT.md`'s "Style conformance — judgment portion" brief to the six ranges, read with `git diff <sha>^1 <sha>`, skipping `shakenfist/protos/*_pb2*.py`. Pay particular attention to these rules. **Three-layer:** every new `_direct_*` function in #4574 and #4606 needs its gRPC and public pair, a proto message, and a Monitor counter in `shakenfist/daemons/database/main.py`. **Field rename / unit change:** `has_pending_operations` is a new view field, `observational` a new column and listing key. Confirm each candidate against the file at `$TIP` and classify it under decision 2. Return findings with file, line, range and grade, or "Style checks passed" plus the files read. Commit subject: "Record the cluster op visibility style review." |
| 6d | medium | opus | none | 2a code quality, read-only (decision 5). Apply `PUSH-AUDIT.md`'s 2a brief and all four of its shared blocks to the six ranges together (decision 2), taking 6b's sweep output as input. Areas worth the time: (1) the target accessors in `shakenfist/mariadb.py` (`_direct_get_cluster_operation_targets_for_object` 5299, `_direct_get_latest_cluster_operation_target` 5354, `_direct_has_pending_cluster_operation_target` 5405, `_direct_get_recent_terminal_op_states_for_target` 5455, `_direct_delete_stale_cluster_operation_targets` 5562, `_direct_list_cluster_operations_for_target` 21762) for duplicated query bodies and the two copies of the in-flight state list (survey finding 7); (2) the three `*OutstandingOperationsEndpoint` classes (`external_api/instance.py:2540`, `network.py:1061`, `artifact.py:1050`), which were rewritten together, for whether they should share one implementation; (3) `baseobject.py`'s `last_cluster_operation`, `has_pending_cluster_operation` and `get_cluster_operations` (784-848), and the three views that call them (`instance.py:723`, `network/network.py:350`, `artifact.py:598`). `mariadb.py` is far past the `source-file-size` threshold; say whether this plan's additions have a seam, without proposing to split the file. Ask what each change switches on beyond being correct on its own terms: #4606 made deleting an instance cancel its outstanding operations (`Instance.enqueue_delete`, `instance.py:2744`), so say what it now cancels that it did not before, against #4615's later change to the same path. Check every candidate at `$TIP` against survey finding 5. Return findings with file, line, range, grade and decision 2 classification. Commit subject: "Record the cluster op visibility code quality review." |
| 6e | medium | sonnet | none | 2b test review, read-only (decision 5). Apply `PUSH-AUDIT.md`'s 2b brief and its `functional-test-coverage` block to the six ranges together. For each behaviour the master plan's success criteria name, say which functional test would have failed before and passes after. That covers the observational op not moving the pointer, the per-object listing returning a queued op behind a later terminal one, `has_pending_operations`, and delete cancelling outstanding operations. Survey finding 6 matters here: since #4653 the two cluster tests run in merge CI on one lane, so say whether that is enough for what they guard. Check whether `shakenfist/tests/test_observational_current_state.py` and `test_object_cluster_operations.py` exercise the SQL through the in-memory database (`shakenfist/tests/dbfixture.py`) or through `mock_mariadb.py`. If the latter, say whether the mock's filtering can disagree with the real SQL. #3465's by-target bug escaped because a fixture used the wrong column type, so look for the same shape. Check whether the per-object listing's `all` flag is tested on both the body and query-string paths for all three object types. Return findings grouped by file, with grades. Commit subject: "Record the cluster op visibility test review." |
| 6f | medium | sonnet | none | 2c documentation review, read-only (decision 5). Apply `PUSH-AUDIT.md`'s 2c brief and all of its shared blocks to the six ranges together. Check these pages against the code at `$TIP`: `docs/operator_guide/database.md` (the `cluster_operation_targets` v3 schema, and "The deleted-object sweep"), `docs/operator_guide/upgrades.md`, `docs/developer_guide/database_internals.md` (mutating and observational), `docs/developer_guide/api_reference/clusteroperations.md` and the `instances.md`, `networks.md` and `artifacts.md` reference pages, `docs/developer_guide/ci.md` ("Waiting for an object's cluster operations"), and `docs/release_notes/v07-v08.md`. Every column, capability token, field, parameter and timing they state must match the code under that name, and nothing user-visible this plan added may be undescribed. #4595 and #4653 touched several of these pages afterwards, so read the current text, not the diff alone. Check that the master plan's Execution table, its Future work and `docs/plans/index.md` agree with the tree. Apply `plan-references-in-code` and `plan-phase-references` to the diff's added lines. Return findings, or "No documentation gaps found" plus the pages read. Commit subject: "Record the cluster op visibility documentation review." |
| 6g | high | opus | none | 2d security review, read-only (decision 5). Apply `PUSH-AUDIT.md`'s 2d brief to the six ranges together. **Authorisation is the area that matters.** Three per-object listings (`external_api/instance.py:2540`, `network.py:1061`, `artifact.py:1050`) and the by-target listing (`external_api/clusteroperation.py:283`) return operations, their state and their targets. Say whether a caller in namespace A can learn anything about objects in namespace B through any of them. That includes an operation that targets objects in two namespaces, the `all=true` path, and the body-kwargs handling #3465 added. Say whether `has_pending_operations` adds a query per view that an unauthenticated or cheap request can multiply (resource exhaustion). For #4606's cancel-on-delete (`Instance.enqueue_delete`, `instance.py:2744`), say whether deleting an instance can cancel an operation that also targets an object the caller does not own, and whether cancellation races an executing operation. Check that no new SQL interpolates a value. Return findings with severity, file, line, vulnerability class and recommended fix. Commit subject: "Record the cluster op visibility security review." |
| 6h | high | opus | none | The coherence of the observational and pending rules, read-only (decisions 5 and 6). The rule is in the "Why this phase exists" section of this plan. Its expressions at `$TIP` are: (1) the `observational_tasks` declarations, `schema/operations/node_inst_op.py:44` and `node_blob_op.py:43`; (2) `is_observational()`, `schema/operations/util.py:20`, applied at enqueue at :130; (3) the stored column, its write in `_direct_create_and_enqueue_cluster_operation` (`mariadb.py:21861`), and the gRPC handler's `HasField('observational')` default (:22320); (4) the filtered readers, `_direct_get_latest_cluster_operation_target` (5354) and `_direct_has_pending_cluster_operation_target` (5405); (5) the deliberately unfiltered readers, at 5299, 5455 and 21762 (survey finding 7); (6) the views and `baseobject.py` 784-848; (7) `NodeInstOp.dispatch_task()`'s observational branch (`operations/node_inst_op.py:106`, from #4608), which tests a *task* where (2) tests the whole *operation*; (8) the CI helper `_await_object_operations_complete()` (`deploy/shakenfist_ci/base.py`) and `cluster_ci_tests/test_observational_sweeps.py`'s `TERMINAL_STATES`; (9) the in-flight state lists, `_ACTIVE_OPERATION_STATES` (`mariadb.py:5402`) and the literal at 5588, against every state an operation can actually reach; (10) the classification test, `shakenfist/tests/schema/operations/test_observational_tasks.py`. For each pair that could disagree, construct the operation one admits and the other refuses. Check in particular: an operation mixing a measurement task with a mutating one, under (2) and (7), where a failing measurement task returns before later tasks run; whether anything enforces (5)'s "net_op is never observational", or only a comment says it; an operation enqueued by an old sf-api during a rolling upgrade, under (3); and a state outside both (9) lists. Then say whether any real enqueue site can produce it; grepping for `enqueue_cluster_operation(` and `create_and_enqueue(` finds them. Reproducing a divergence in the in-memory database (`shakenfist/tests/dbfixture.py`) counts as proof; describe the test and the management session runs it. Report each pair as consistent, divergent but unreachable (say why), or divergent and reachable (a blocking finding). Commit subject: "Record the observational rule coherence audit." |
| 6i | medium | opus | none | The client contract, read-only (decision 3). In `/srv/kasm_profiles/mikal/vscode/src/shakenfist/client-python`, after `git fetch origin`, read `git diff fdcfe6e53d^1 fdcfe6e53d` (#436), and read `await_no_pending_operations()` as it stands on `origin/develop`. Change nothing in that repository. Against this repository at `$TIP`: does each wrapper gate on the capability the server advertises (`external_api/app.py:432-437`), send `all` and `namespace` where the server reads them, and name its parameters as the API reference pages do? Does `await_no_pending_operations()` treat a missing field, a deleted object, a negative or zero timeout and an observational operation as the server's docs say it should? Does `deploy/shakenfist_ci/base.py`'s helper catch every exception it can raise? Then apply the `plan-push-audit-phase` block's five-item floor to that range, giving each item a verdict or a sentence on why it does not apply. For item 4, grep `client-python`, `client-python-k3s`, `sfui` and `client-python-ova` under `/srv/kasm_profiles/mikal/vscode/src/shakenfist/` and state the greps. Return findings, each marked for client-python or this repository. Commit subject: "Record the cluster op visibility client contract audit." |
| 6j | medium | opus | none | Management session, not a sub-agent: grading and disposition are decisions. Pool 6b-6i's findings with seeded S1, S2 and S3 (survey finding 8). Grade each blocking, advisory or predating, and give each a disposition (decision 7). Present the graded table to the operator before filing anything (see Back brief). Then run decision 8's duplicate searches, file the issues, and write a `### Disposition` table into this plan. Add an "Audit of phases 1 to 5" subsection to the master plan's Phase 6 section. Point each Future work entry at its issue, or say why it stays an idea. If no finding is blocking, set phase 6 and the plan to Complete (decision 7, last case). Otherwise leave them In progress and cut the fix branch. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: "Record the cluster op visibility audit disposition." |

6c to 6i are independent of each other. They run in parallel once 6b
has reported and the first back-brief gate has passed. The management
session records each report under `## Findings`, in a sub-heading
named for its step.

## Risks and mitigations

* **The audit rubber-stamps merged code.** Each phase had its own
  review, and a reviewer reading six reviewed diffs is inclined to
  agree with them. *Mitigation:* decision 10 makes every clean heading
  list what it read. 6d and 6h each ask what a change switches on, not
  only whether it is correct. 6j rejects a report that names no
  functions.
* **Findings against code that has since changed.** Eleven later
  merges touch audited files, and phase 1 is three months old.
  *Mitigation:* decision 2's three classifications. 6j spot-checks one
  "still true" claim per report at `$TIP`.
* **The client range is audited as if it were this repository.**
  client-python has its own conventions, and no runbook. *Mitigation:*
  decision 3 limits 6i to the contract and the five-item floor, and
  findings go to client-python as issues, never as edits.
* **Parallel agents interfere.** *Mitigation:* decision 5. Only 6b,
  which runs alone first, and 6j write files or run the suite.
* **Predating defects are graded as this plan's.** S1 and S2 each say
  they predate the plan, and a reviewer reading the Future work list
  could mistake them for findings. *Mitigation:* decision 7's
  predating grade, under which they become issues and not findings, so
  they cannot hold the plan open.
* **Wave 1 is slow or fails for reasons outside these ranges.**
  *Mitigation:* decision 9, and the back-brief gate after 6b, so a
  broken `develop` is reported before wave 2 is spent.

## Definition of done

* `## Findings` in this plan has a sub-heading for each of: wave 1;
  the mechanical sweep; style; 2a; 2b; 2c; 2d; rule coherence; the
  client contract; and disposition. Every heading except disposition
  names the files or functions it examined.
* Wave 1 records literal output for 24 style-grep runs (four greps
  across six ranges) and 30 sweep runs (five commands across six
  ranges). The check is that each range's sub-heading cites its merge:

  ```
  for s in bc7c119bf 9b098c6ac 27ea18202 3642e7a75 bdc91db78 93c31035a0; do
    grep -c "$s" docs/plans/PLAN-cluster-op-visibility-phase-06-push-audit.md
  done
  ```

  This prints a non-zero count for every range. Reading each
  sub-heading confirms the nine commands are there.
* `tox -e genprotos && git diff --exit-code shakenfist/protos` exits 0
  with the result recorded, or the stale stubs are a recorded finding.
* `tools/mermaid-lint.sh`'s exit status is recorded.
* Step 6h gives a verdict for every pair it considered, across all ten
  expressions. Step 6i gives a verdict for each of the five floor
  items.
* Every finding carries a grade, a disposition (fixed in #NNNN, filed
  as #NNNN, recorded on an existing issue, or declined with a reason)
  and the decision 2 classification. The searches behind each "no
  duplicate" claim are written in the disposition table.
* S1, S2 and S3 are dispositioned. Every entry in the master plan's
  Future work names an issue, or says why it stays an idea.
* `python3 tools/check-plan-status.py` exits 0, and
  `pre-commit run --all-files` is clean.
* The plan is Complete only when no blocking finding remains open. If
  it is Complete in this phase's own pull request, phase 6's `Merged`
  cell is left empty, as the `plan-phase-landing` block requires.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns with
it.

There are two gates beyond that, both cheap to stop at and expensive
to get wrong:

* **After 6b**, report whether wave 1 passed before spending on the
  seven parallel agents. If something failed for a reason unrelated to
  these ranges (decision 9), say so explicitly rather than presenting
  it as a finding.
* **In 6j, before filing any issue**, present the graded findings
  table to the operator. Issues are public, in two repositories, and
  the blocking or advisory split decides whether a follow-up pull
  request exists at all. Both are easier to agree before filing than
  to unwind after.

## Execution notes

None yet.
