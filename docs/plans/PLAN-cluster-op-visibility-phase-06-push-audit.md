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
     networks, "which is never observational". *(Corrected by 6h:
     `daemons/network/maintain.py:779` also asks about `net_ip_op`, so
     the comment was wrong when it was written. The classification
     table is what keeps both types mutating.)*

   The "still in flight" state list is written twice: as
   `_ACTIVE_OPERATION_STATES` (`mariadb.py:5402`), and as a literal
   `active_states` in `_direct_delete_stale_cluster_operation_targets`
   (`:5588`). *(6h found a third, in the orphan reaper at `:23986`.)*
   The sweep test has its own `TERMINAL_STATES` set. Each
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

## Findings

Every candidate below was checked at `$TIP` by the management session.
At least one claim from each report was reproduced against the file,
and 6h's six proof tests were run. Where a report names a later merge,
its classification under decision 2 is given. A finding with no
classification is still true at `$TIP`.

### Wave 1

Run by 6b on 2026-10-26 against `$TIP` plus this phase's two planning
commits. It passed, so decision 9's relaxation was not needed.

| Check | Result |
|-------|--------|
| `pre-commit run --all-files` | Pass, exit 0 |
| `tox` (py3, flake8, cover) | Pass, exit 0. stestr ran 6,216 tests: 6,065 passed, 151 skipped, 0 failed |
| `tools/mermaid-lint.sh` | Exit 0, read directly rather than through a pipe |
| `tox -e genprotos && git diff --exit-code shakenfist/protos` | Exit 0, no diff. The stubs are fresh |
| `git status --short` afterwards | Empty |

Across the 24 style-grep runs there are three kinds of hit:

* **Lines over 120 characters:** only the generated `database_pb2.py`
  descriptor, in #3465, #4574 and #4606, which is exempt.
* **`print(`:** one hit, in #4574, `test_mariadb_cluster_operation_targets_live.py:300`.
  It is graded under Style.
* **`etcd`:** one hit, in #3465, and it is only the word in a mock's
  docstring.

`get_all_` produces no output in any range.

### Mechanical sweep

The 30 sweep runs found no TODO, FIXME, `# noqa`, `# type: ignore` or
subprocess hits in any range. #4617 is the one range with no new test
functions; it is the smallest, at four files. Below are the literal
outputs for every range, all nine commands each. The three descriptor
lines are described rather than quoted, because each one is 65,000 to
91,000 characters of serialised protobuf.

##### `bc7c119bf` (#3465)

* `git diff bc7c119bf^1 bc7c119bf -- '*.py' | grep -nE '^\+[^+].{120,}'`

  ```
  392:+DESCRIPTOR = ... (generated shakenfist/protos/database_pb2.py, 65,581 characters; exempt)
  ```
* `git diff bc7c119bf^1 bc7c119bf -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
  no output
* `git diff bc7c119bf^1 bc7c119bf -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`

  ```
  566:+        mock also writes the legacy etcd path at
  ```
* `git diff bc7c119bf^1 bc7c119bf -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
  no output
* `git diff bc7c119bf^1 bc7c119bf -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
  no output
* `git diff bc7c119bf^1 bc7c119bf -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
  no output
* `git diff bc7c119bf^1 bc7c119bf --stat | tail -1; git diff bc7c119bf^1 bc7c119bf -- '*.py' | grep -cE '^\+\s*def test_'`

  ```
   18 files changed, 1196 insertions(+), 177 deletions(-)
  2
  ```
* `git diff bc7c119bf^1 bc7c119bf --name-only -- 'docs/*' '*.md'`

  ```
  docs/plans/PLAN-cluster-op-visibility-phase-01-ci-deflake.md
  docs/plans/PLAN-cluster-op-visibility.md
  docs/plans/index.md
  docs/plans/order.yml
  ```
* `git diff bc7c119bf^1 bc7c119bf -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
  no output

##### `9b098c6ac` (#4574)

* `git diff 9b098c6ac^1 9b098c6ac -- '*.py' | grep -nE '^\+[^+].{120,}'`

  ```
  792:+DESCRIPTOR = ... (generated shakenfist/protos/database_pb2.py, 90,549 characters; exempt)
  ```
* `git diff 9b098c6ac^1 9b098c6ac -- '*.py' | grep -nE '^\+[^+].*\bprint\('`

  ```
  2824:+        print(message)
  ```
* `git diff 9b098c6ac^1 9b098c6ac -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
  no output
* `git diff 9b098c6ac^1 9b098c6ac -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
  no output
* `git diff 9b098c6ac^1 9b098c6ac -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
  no output
* `git diff 9b098c6ac^1 9b098c6ac -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
  no output
* `git diff 9b098c6ac^1 9b098c6ac --stat | tail -1; git diff 9b098c6ac^1 9b098c6ac -- '*.py' | grep -cE '^\+\s*def test_'`

  ```
   32 files changed, 2542 insertions(+), 731 deletions(-)
  43
  ```
* `git diff 9b098c6ac^1 9b098c6ac --name-only -- 'docs/*' '*.md'`

  ```
  docs/developer_guide/api_reference/clusteroperations.md
  docs/developer_guide/database_internals.md
  docs/developer_guide/network_dispatcher.md
  docs/operator_guide/database.md
  docs/operator_guide/upgrades.md
  docs/plans/PLAN-cluster-op-visibility-phase-02-observational-schema.md
  docs/plans/PLAN-cluster-op-visibility.md
  ```
* `git diff 9b098c6ac^1 9b098c6ac -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
  no output

##### `27ea18202` (#4588)

* `git diff 27ea18202^1 27ea18202 -- '*.py' | grep -nE '^\+[^+].{120,}'`
  no output
* `git diff 27ea18202^1 27ea18202 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
  no output
* `git diff 27ea18202^1 27ea18202 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
  no output
* `git diff 27ea18202^1 27ea18202 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
  no output
* `git diff 27ea18202^1 27ea18202 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
  no output
* `git diff 27ea18202^1 27ea18202 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
  no output
* `git diff 27ea18202^1 27ea18202 --stat | tail -1; git diff 27ea18202^1 27ea18202 -- '*.py' | grep -cE '^\+\s*def test_'`

  ```
   27 files changed, 984 insertions(+), 57 deletions(-)
  15
  ```
* `git diff 27ea18202^1 27ea18202 --name-only -- 'docs/*' '*.md'`

  ```
  docs/developer_guide/database_internals.md
  docs/plans/PLAN-cluster-op-visibility-phase-03-mark-observational.md
  docs/plans/PLAN-cluster-op-visibility.md
  docs/plans/index.md
  docs/release_notes/v07-v08.md
  ```
* `git diff 27ea18202^1 27ea18202 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
  no output

##### `3642e7a75` (#4606)

* `git diff 3642e7a75^1 3642e7a75 -- '*.py' | grep -nE '^\+[^+].{120,}'`

  ```
  715:+DESCRIPTOR = ... (generated shakenfist/protos/database_pb2.py, 91,099 characters; exempt)
  ```
* `git diff 3642e7a75^1 3642e7a75 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
  no output
* `git diff 3642e7a75^1 3642e7a75 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
  no output
* `git diff 3642e7a75^1 3642e7a75 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
  no output
* `git diff 3642e7a75^1 3642e7a75 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
  no output
* `git diff 3642e7a75^1 3642e7a75 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
  no output
* `git diff 3642e7a75^1 3642e7a75 --stat | tail -1; git diff 3642e7a75^1 3642e7a75 -- '*.py' | grep -cE '^\+\s*def test_'`

  ```
   31 files changed, 1961 insertions(+), 408 deletions(-)
  26
  ```
* `git diff 3642e7a75^1 3642e7a75 --name-only -- 'docs/*' '*.md'`

  ```
  docs/developer_guide/api_reference/artifacts.md
  docs/developer_guide/api_reference/clusteroperations.md
  docs/developer_guide/api_reference/instances.md
  docs/developer_guide/api_reference/networks.md
  docs/developer_guide/database_internals.md
  docs/plans/PLAN-cluster-op-visibility-phase-04-api-surface.md
  docs/plans/PLAN-cluster-op-visibility.md
  docs/plans/index.md
  docs/release_notes/v07-v08.md
  ```
* `git diff 3642e7a75^1 3642e7a75 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
  no output

##### `bdc91db78` (#4617)

* `git diff bdc91db78^1 bdc91db78 -- '*.py' | grep -nE '^\+[^+].{120,}'`
  no output
* `git diff bdc91db78^1 bdc91db78 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
  no output
* `git diff bdc91db78^1 bdc91db78 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
  no output
* `git diff bdc91db78^1 bdc91db78 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
  no output
* `git diff bdc91db78^1 bdc91db78 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
  no output
* `git diff bdc91db78^1 bdc91db78 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
  no output
* `git diff bdc91db78^1 bdc91db78 --stat | tail -1; git diff bdc91db78^1 bdc91db78 -- '*.py' | grep -cE '^\+\s*def test_'`

  ```
   4 files changed, 44 insertions(+), 5 deletions(-)
  0
  ```
* `git diff bdc91db78^1 bdc91db78 --name-only -- 'docs/*' '*.md'`

  ```
  docs/plans/PLAN-cluster-op-visibility-phase-04-api-surface.md
  docs/plans/PLAN-cluster-op-visibility.md
  ```
* `git diff bdc91db78^1 bdc91db78 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
  no output

##### `93c31035a0` (#4650)

* `git diff 93c31035a0^1 93c31035a0 -- '*.py' | grep -nE '^\+[^+].{120,}'`
  no output
* `git diff 93c31035a0^1 93c31035a0 -- '*.py' | grep -nE '^\+[^+].*\bprint\('`
  no output
* `git diff 93c31035a0^1 93c31035a0 -- '*.py' | grep -nE '^\+[^+].*\betcd\b'`
  no output
* `git diff 93c31035a0^1 93c31035a0 -- '*.py' | grep -nE '^\+[^+].*\bmariadb\.get_all_[a-z_]+\(' | grep -v '# nopushdown:'`
  no output
* `git diff 93c31035a0^1 93c31035a0 -- '*.py' | grep -nE '^\+.*\b(TODO|FIXME|HACK|XXX)\b'`
  no output
* `git diff 93c31035a0^1 93c31035a0 -- '*.py' | grep -nE '^\+.*(# noqa|# type: ignore|pragma: no cover)'`
  no output
* `git diff 93c31035a0^1 93c31035a0 --stat | tail -1; git diff 93c31035a0^1 93c31035a0 -- '*.py' | grep -cE '^\+\s*def test_'`

  ```
   26 files changed, 1060 insertions(+), 129 deletions(-)
  11
  ```
* `git diff 93c31035a0^1 93c31035a0 --name-only -- 'docs/*' '*.md'`

  ```
  docs/developer_guide/api_reference/clusteroperations.md
  docs/developer_guide/ci.md
  docs/developer_guide/database_internals.md
  docs/operator_guide/database.md
  docs/operator_guide/networking/overview.md
  docs/operator_guide/upgrades.md
  docs/plans/PLAN-cluster-op-visibility-phase-05-coverage-docs.md
  docs/plans/PLAN-cluster-op-visibility.md
  docs/plans/index.md
  docs/release_notes/v07-v08.md
  ```
* `git diff 93c31035a0^1 93c31035a0 -- '*.py' | grep -nE '^\+.*\b(subprocess\.|os\.system|shell=True)\b'`
  no output


### Style (6c)

* **A10, advisory.** `shakenfist/tests/test_mariadb_cluster_operation_targets_live.py:300`
  (#4574). A `print(message)` duplicates the `addDetail()` on the line
  above it.

The three-layer rule holds. Neither #4574 nor #4606 adds a `_direct_*`
function, so the 6c row's premise about new ones was wrong. #4574
removes `_direct_create_cluster_operation_target` cleanly from all
three layers, along with its RPC, Monitor counter and proto message.
New parameters (`targets`, `observational`, `pending_only`) appear in
the direct, gRPC, public and daemon layers alike.

`has_pending_operations` and `observational` are additive, and no
units changed. #4617 can append `None` to `runs_after`, which is
harmless because `_convert_deps()`
(`schema/operations/baseclusteroperation.py:114`) drops falsy entries.

Read: the six ranges' diffs, excluding the pb2 stubs.

### Code quality, 2a (6d)

* **A6, advisory.** `operations/node_inst_op.py:125-127` (#4588, made
  false by #4606). The comment says "a queued sweep is not cancelled by
  a later delete". Since #4606, `enqueue_delete` aborts every queued op
  that targets the instance. The guard is still needed, so only the
  reason should change.
* **I2, advisory.** "Operations targeting X" has two implementations.
  One is `baseobject.py:809-847` (#4606); the other is
  `external_api/clusteroperation.py:371-391` (#3465, #4574). They
  differ in ordering, and in the `observational` key against an older
  sf-database.
* **Views, advisory, recorded on the Future work idea.** Each view now
  makes a has-pending RPC beside the latest-op RPC (`instance.py:723`,
  `network/network.py:350`, `artifact.py:598`). The list endpoints
  multiply that by N.
* **A4, advisory.** `mariadb.py:502, 1818, 1916, 5374, 5431, 5853,
  5879, 21899`, `schema/operations/util.py:31`,
  `schema/cluster_operation_target.py:57` and two test modules (#4574)
  all end a stated reason with "See
  docs/plans/PLAN-cluster-op-visibility.md".
* **A8, advisory.** `schema/operations/util.py:80-82` (#3465). The
  docstring omits the target rows written in the same transaction.
* **A9, advisory.** The four-line swagger `all` description is copied
  in `external_api/instance.py:2555`, `network.py:1076` and
  `artifact.py:1065` (#4606, #4650). The condition at
  `instance.py:2791` (#4617) reads inverted.

Predating, not findings:

* the in-flight state list in several copies (graded as I3 below);
* row-to-dataclass construction copied three times (`mariadb.py:5283,
  5336, 5382`);
* `_hydrate_op` re-reading a header the listing already returned;
* the listing failing open (S1).

**What cancel-on-delete switched on (#4606).** Before #4606, a delete
aborted the pointer op and its queued or executing
`depends_on`/`runs_after` chain, and only when the pointer was in
flight. It now also aborts:

* ops that target the instance but sit off the pointer's chain, which
  is the race this plan fixes;
* observational sweeps;
* multi-target ops that include the instance: `node_inst_net_iface_op`
  and `artifact_fetch_op`, both of them per instance.

It waits for every outstanding snapshot, not just the pointer. It no
longer aborts shared network ops reached through dependencies, which
the old walk did, failing other instances' work. Against #4615's later
change to the same path, the cleaner's re-enqueue can abort only a
repeat delete or a late-enqueued op. No harmful interaction was found.

Read:

* every target accessor in `mariadb.py`, with its gRPC and public pair;
* the v3 migration;
* `list_cluster_operations_for_target` and `create_and_enqueue`;
* the database daemon handlers;
* `baseobject.py:771-860` and the three views;
* `enqueue_delete`;
* the three Outstanding endpoints and `clusteroperation.py`;
* `schema/operations/*`'s `target_fields` and `observational_tasks`;
* `node_inst_op.py`, `artifact_fetch_op.py`, `baseoperation.py` and
  `queues/workitem.py`;
* the #4615, #4620 and #4650 changes to these files.

### Tests, 2b (6e)

* **I4, advisory.** The coverage gaps:
  * `test_instance_enqueue_delete.py` never aborts an `executing` op;
  * no functional test deletes an instance that has an op outstanding;
  * only the network per-object listing is exercised functionally;
  * no functional test checks `has_pending_operations` on network or
    artifact views;
  * nothing tests that a foreign namespace is refused on the instance
    and network listings;
  * in `test_observational_sweeps.py:79-84`, the in-loop pending check
    probably never fires.
* **I3, advisory, with 6h.** `mock_mariadb.py:3480` and `:3560` carry
  further literal copies of the in-flight state list.

The real-SQL tests use sqlite built from `mariadb.py`'s own table
definitions, through `dbfixture.py`, not `mock_mariadb.py`. So the
fixture-type shape behind #3465's by-target bug cannot recur in them.

The `all` flag is tested on both the body and the query-string paths
for all three object types, through the real Flask app
(`test_outstanding_operations_all.py`).

One merge lane, `debian-13-slim-primary`, is enough for what the two
cluster tests guard (survey finding 6). They check server semantics,
not anything specific to a guest OS or a topology.

"net_op is never observational" is enforced by
`test_observational_tasks.py`'s `CLASSIFICATION` table, not only by a
comment.

Each success criterion that names a behaviour maps to a test that
would have failed before and passes after:

* the pointer and the pending field ignoring an observational op:
  `test_observational_current_state.py`;
* the queued op behind a later terminal one:
  `test_object_cluster_operations.py::test_success_criterion`;
* `has_pending_operations`: `test_has_pending_operations_view.py`;
* delete cancelling outstanding work:
  `test_instance_enqueue_delete.py`;
* the hot-plug race: the three `instance_hotplug.py` smoke tests.

Predating:

* the fail-closed branch of the direct has-pending query is untested;
* the live-MariaDB suite does not cover the by-target uuid lookup.

Read:

* `test_observational_current_state.py`;
* `test_object_cluster_operations.py`;
* `test_instance_enqueue_delete.py`;
* `test_has_pending_operations_view.py`;
* `test_outstanding_operations_all.py`;
* `test_observational_tasks.py`;
* `test_ci_await_operations.py`;
* `test_mariadb_cluster_operation_targets_live.py`;
* `dbfixture.py` and `mock_mariadb.py`;
* `cluster_ci_tests/test_api.py` and `test_observational_sweeps.py`.

### Documentation, 2c (6f)

* **B1, blocking.** `docs/release_notes/v07-v08.md:1485-1486` (#4588;
  out of scope, made false by #4608). It says a failing billing or
  health task still errors its instance. `node_inst_op.py:100-108`
  now returns first for an observational task.
* **A1, advisory.** `docs/operator_guide/upgrades.md:333-336` and
  `:360` (#4574, made false by #4588). "Until a later release
  classifies any task as observational" is stale: three tasks are
  classified.
* **A2, advisory.** `v07-v08.md:1482` says "No migration is needed".
  `upgrades.md:327-344` requires `ensure-mariadb-schema` before
  sf-database starts.
* **A3, advisory, with 6h D4.** `docs/developer_guide/database_internals.md:363-366`
  (#4606) says the pending-only listing and `has_pending_operations`
  "agree". The listing includes observational ops
  (`mariadb.py:5299-5352`) and the field does not (`:5405-5437`).
  The reader list at `:381` is also incomplete.
* **A4, advisory, with 6d.** `database_internals.md:291` and
  `upgrades.md:336` link the master plan for built behaviour. The code
  citations are listed under 6d.
* **A5, advisory.** `clusteroperations.md:78` and `v07-v08.md:1497`
  say `object-pending-operations` advertises only the field.
  `external_api/app.py:433-437` says it also covers the per-object
  listings.

Fixed since: three plan citations, in `node_inst_op.py`,
`mock_mariadb.py` and `test_observational_tasks.py`.

Predating: `config.py:791` still calls the target table a "history
table".

Clean:

* the sweep timings, prune states and retention;
* the v3 column and index;
* the three observational tasks;
* cancel-on-delete;
* the `ci.md` helper description;
* the client wrapper names;
* the Execution table, `index.md:106` and `order.yml:42`.

No range changes `state_targets` or adds an ASCII diagram, and none
touches README, AGENTS or ARCHITECTURE.

Read:

* `database.md`, `upgrades.md` and `database_internals.md`;
* `clusteroperations.md`, `instances.md`, `networks.md` and
  `artifacts.md`;
* `ci.md` and `v07-v08.md`;
* the master plan and `index.md`.

### Security, 2d (6g)

There are no critical, high or medium findings.

* **I1, low.** `external_api/artifact.py:1073-1078` (#4606 widened a
  predating class). The listing admits anyone who can see the
  artifact. It returns every `artifact_fetch_op`, each with its
  `instance_uuid` and `namespace`, so a reader of a shared or trusting
  artifact learns other namespaces' instance uuids. The by-target
  listing refuses the same caller with a 403
  (`clusteroperation.py:360-372`).
* **I2, low.** Both listings are unbounded and read once per row
  (`baseobject.py:807-847`, `clusteroperation.py:375-391`).
* **Informational.** `has_pending_operations` adds one indexed
  `EXISTS` per view, and every endpoint that returns a view is
  authenticated.
* **Informational.** Cancel-on-delete marks executing ops `abort`. The
  running task finishes and later tasks are skipped. The instance lock
  serialises create, start and delete.
* **P1, low, predating.** The single-op endpoint
  (`clusteroperation.py:139-153`) admits a target that has no
  namespace, and checks only one target row.
* **Informational, predating.** The by-target endpoint answers 404 for
  a missing object and 403 for a foreign one.

Verdicts:

* **Cross-namespace reads:**
  * the instance and network listings leak nothing;
  * the artifact listing leaks in a limited way (I1);
  * the by-target listing fails closed;
  * no enqueue site creates an op that targets two namespaces;
  * #3465's body kwargs are type-checked and authorised on the uuid
    that is queried.
* **Resource exhaustion through `has_pending_operations`:** no.
* **Cancel-on-delete:** it cannot reach an object the caller does not
  own, because every target of such an op shares the instance's
  namespace.
* **Interpolated SQL:** none. The one f-string DDL, `mariadb.py:1944`,
  uses a constant table name.

Read:

* `clusteroperation.py`;
* the auth, ownership, `log_request` and `validate_request` decorators
  in `external_api/base.py`;
* the artifact access decorators;
* the three listing endpoints;
* `baseobject.py:770-860`;
* `enqueue_disk_fetches`, `enqueue_delete` and `hot_plug_interface`;
* the target accessors;
* `enqueue_cluster_operation`;
* every `target_fields` and `external_view`;
* the op state machine and `workitem.py`'s dependency gating.

### Rule coherence (6h)

6h examined 21 pairs across the ten expressions in the 6h row. 15 are
consistent, five are divergent but unreachable, and one is divergent
and reachable only during a rolling upgrade, by design. None is
blocking.

| Pair | Verdict |
|------|---------|
| Declarations and the classification table; the declarations and `is_observational()` | Consistent |
| `is_observational()` and `dispatch_task()` for a mixed op (D1) | Divergent, unreachable. No enqueue site mixes tasks: `scheduled_tasks.py:595` and `instance.py:2795` are the only `node_inst_op` sites. Filed as I5 |
| `is_observational()` and `dispatch_task()` for an all-observational op | Consistent |
| `is_observational()` and the stored flag, at this revision | Consistent |
| The stored flag during a rolling upgrade (D2) | Divergent and reachable during the upgrade only. It is documented at `mariadb.py:1810-1817` and `:1916-1922`, and in `database.proto`, and matches pre-plan behaviour |
| The stored flag and the filtered readers; latest and has-pending | Consistent |
| The filtered readers and `:5299` (the blob guard, the listing, cancel-on-delete) | Consistent by design |
| The filtered readers and `:5455` (D3) | Divergent, unreachable. The comment's "only callers ask about net_op" is wrong, because `daemons/network/maintain.py:779` also asks about `net_ip_op`. The classification table enforces the invariant. Graded A7 |
| The filtered readers and `:21762`; both listings against an older sf-database | Consistent in meaning |
| The views and the readers; `enqueue_delete` and the pointer | Consistent. On a database error has-pending fails closed and the listing fails open, which is S1 |
| `dispatch_task()` and other dispatchers or the reaper (D5) | Divergent, unreachable. Only `NodeInstOp` spares its object |
| The CI helper and the server | Consistent, with a latent dependency on #3922 (D6, recorded on #3922) |
| Every in-flight and terminal list against the reachable states (D7) | Consistent on every reachable state. `preflight` cannot be reached. There is a third copy at `mariadb.py:23986`. Filed as I3 |
| The blob declaration and the readers | Consistent and inert, because `Blob` is a plain `dbo` |

Proof tests: `MixedOperationTestCase`, `RollingUpgradeFlagTestCase`,
`StateListCoherenceTestCase` (three tests) and
`NetworkTargetsNeverObservationalTestCase`. The management session ran
all six in a copy of the tree, and all passed. 6h checked
`MixedOperationTestCase` by mutating the condition at
`node_inst_op.py:106`. The file is posted on #4668 for whoever takes
I3 and I5.

Predating: `NodeBlobOp.execute()` deletes its target row on every exit,
including a requeue (P2).

### Client contract (6i)

client-python #436 (`fdcfe6e53d^1..fdcfe6e53d`) was read against
`$TIP`. Its functions are unchanged on client `origin/develop`, apart
from #438's unrelated addition. Nothing in that repository was edited.

* **C1, advisory.** `await_no_pending_operations()` returns a deleted
  object as settled, though its docstring and
  `docs/object-operations.md` say it raises. A uuid lookup does not
  filter on state (`baseobject.py:417-419`, `instance.py:515-516`),
  and the view comes back until the hard delete, which waits for
  `CLEANER_DELAY`. This repository repeats the claim:
  * `docs/developer_guide/ci.md:228-229`;
  * the helper's `ResourceNotFoundException` branch in
    `deploy/shakenfist_ci/base.py`, added in #4650, which cannot fire
    for a uuid;
  * a unit test that pins a 404 the server does not send.

  Fixing the client makes all three true. That fix is client-python#439.
* **C2, advisory.** The listing wrappers check the 2025
  `*-clusteroperations` tokens, but document the contract that
  `object-pending-operations` advertises.
* **C3, advisory.** `get_artifact(namespace=)` has no capability to
  check.

The contract otherwise holds:

* `all` and `namespace` travel in the body and are read there;
* the names match the API reference pages;
* `check_capability`'s substring test has no false positive;
* a missing field raises `IncapableException`;
* timeouts and observational operations match the client docs.

Floor items:

1. **Pass.** `fdcfe6e53d` is a two-parent merge on client
   `origin/develop`. client-python has no `PUSH-AUDIT.md`, so this is
   the audit of record.
2. **Not applicable.** Nothing in the range runs unattended.
3. **Pass.** No secret, private hostname or address. Only a GitHub
   docs link and the existing `localhost` test fixture.
4. **Pass.** Only `get_artifact`'s signature changed, additively.
   * The greps were `git grep -nE "get_artifact\(|_clusteroperations\(|await_no_pending_operations|has_pending_operations"`
     in client-python, and the same family in client-python-k3s, sfui
     and client-python-ova.
   * The positional callers in client-python's command line and
     client-python-ova are unaffected.
5. **Pass.** client-python publishes from `v*` tags. No tag contains
   `fdcfe6e53d`, so C1 to C3 are unreleased.

Read:

* `get_{instance,network,artifact}_clusteroperations`, `get_artifact`
  and `await_no_pending_operations`;
* `check_capability`, `_calculate_async_deadline` and
  `delete_all_instances`;
* the #436 tests and `docs/object-operations.md`;
* on the server side, the capability list, the endpoints, the lookup
  decorators and `from_db_by_ref`.

### Disposition

Graded with the operator on 2026-10-26, before anything was filed.
Duplicate searches were run with `gh issue list --state all --search`,
for each issue's key terms:

* "release note billing health error state";
* "artifact clusteroperations instance uuid shared";
* "clusteroperations listing unbounded";
* "by-target listing per-object listing";
* "in-flight states duplicated queued preflight executing";
* "enqueue_delete executing abort test";
* "dispatch_task observational task";
* "enqueue_delete fails open";
* "agent operation preflight target instance";
* "runs_after last_cluster_operation pointer";
* "cluster operation single op endpoint namespace";
* "NodeBlobOp target row requeue ensure_local";
* in client-python, "await_no_pending_operations", "clusteroperations
  capability" and "get_artifact namespace".

None returned a duplicate. The nearest results were #4585, which is
closed and fixed, and unrelated flake umbrellas.

| Id | Grade | Disposition |
|----|-------|-------------|
| B1 | Blocking | Filed as #4665 with `automated-fix-attempted`. Fixed in the follow-up pull request |
| A1-A10 | Advisory | Fixed in the same follow-up pull request. A one-line correction each, in this plan's own text |
| I1 | Advisory (low security) | Filed as #4666 |
| I2 | Advisory | Filed as #4667 |
| I3 | Advisory | Filed as #4668, with 6h's tests posted on it |
| I4 | Advisory | Filed as #4669 |
| I5 | Advisory | Filed as #4670 |
| D6 | Advisory | Recorded on #3922 |
| C1 | Advisory | Filed as client-python#439 |
| C2 | Advisory | Filed as client-python#440 |
| C3 | Advisory | Filed as client-python#441 |
| S1 | Predating | Filed as #4671 |
| S2 | Predating | Filed as #4672 |
| S3 | Predating | Filed as #4673 |
| P1 | Predating (low security) | Filed as #4674 |
| P2 | Predating | Filed as #4675 |
| Views RPC count | Advisory | Recorded on the Future work idea. Phase 4 measured it at about 2.5 requests a second on sfcbr |
| Other predating notes (test gaps, duplicated row construction, `config.py`'s "history table", the 404/403 existence oracle) | Predating | Not filed. Each is small, and recorded here |

B1 is blocking, so the plan stays In progress. The fix is a follow-up
pull request cut from `develop`. It also carries this phase's
close-out: its `Merged` cell, and Complete for the plan.

## Execution notes

* **2026-10-26 (AEDT), back brief.** Mikal confirmed the plan as
  written, including both gates. He also gave this session permission
  to push the branch and open the pull request.
* **6b.** As planned, and wave 1 passed (gate 1). 6b's literal record
  ran to about 167,000 tokens, because three of its grep hits are
  generated protobuf descriptor lines of 65,000 to 91,000 characters.
  The management session therefore re-ran the same nine commands per
  range. It records their output with those three lines described
  rather than quoted, and every other line verbatim.
* **6c to 6i.** All seven ran in parallel after gate 1, read-only. Four
  of them (6d, 6g, 6h and 6i) could not write `report.md`: the harness
  refuses file writes from sub-agents. They returned their reports as
  text, and the management session recorded them. 6h wrote its proof
  tests to the scratchpad and ran them in a `git archive` copy. The
  management session ran them again; all six pass. The worktree
  stayed clean throughout.
* **Corrections at source.** The 6c row assumed #4574 and #4606 add new
  `_direct_*` functions, but they add none. Survey finding 7 repeated a
  wrong comment and missed a third copy of the in-flight list. It is
  corrected above.
* **Commits.** The plan names a commit subject for each step. The
  record is committed as one commit instead, "Record the cluster op
  visibility push audit.". Every step only adds a section of this
  file, and the disposition depends on all of them.
* **Gate 2, 2026-10-26.** Mikal approved the grading as proposed. B1 is
  blocking. A1-A10 are fixed in the follow-up pull request. I1-I5,
  S1-S3, P1, P2 and C1-C3 are filed. D6 is recorded on #3922.
