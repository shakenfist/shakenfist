# PLAN: Queue performance phase 12 -- push audit of phases 9 to 11

Planning effort: medium. Review effort: medium.

## Why this phase exists

Phase 8 ran `PUSH-AUDIT.md` over phases 1 to 8 and found that
coalescing had never worked. Phases 9, 10 and 11 then reopened the
plan and changed the same code again: phase 9 proved the fold and
instrumented it, phase 10 instrumented where pre-execution time
goes, and phase 11 widened the coalescing key to two columns and
rewrote its guards twice before it merged. Nothing has audited those
three phases as one body of work. Phase 8 cannot have, because none
of them existed when it ran, and it stays where it is and goes on
saying what it covered (see "Phase 12" in the master plan).

This plan's defects have been ones that phases did to each other.
Phase 8 found a join that phases 4 and 5 had each written correctly
on their own terms. Phase 8's own fix would have activated a
cross-node fold, and only review caught it. This audit exists to ask
the same question of phases 9 to 11.

## Scope

**In scope.** The five merges which put phases 9 to 11 on `develop`,
audited under every `PUSH-AUDIT.md` heading: wave 1, the wave 2
mechanical sweep, the style judgment portion, and 2a to 2d. Also one
heading of this plan's own, the coherence of the coalescing rule
(step 12h), which the master plan names as the first area worth
looking at.

**Out of scope.**

* Phases 1 to 8, which phase 8 audited. Where a phase 9 to 11 change
  relies on something earlier, the dependency is noted, not
  re-audited.
* #3916 (`issue-fix-3863`, the `sf-queues` dependency-wait back-off).
  Phase 10's premise rests on it, but the autofixer landed it for
  #3863, it was reviewed in its own pull request, and the plan's
  `Merged` column does not claim it.
* The ten later commits on `develop` which touch the audited files
  (listed in survey finding 4). They are not this plan's work. They
  matter only where they change whether a finding is still true,
  which decision 2 covers.
* Fixing what the audit finds. Decision 7 says what happens to each
  kind of finding and which pull request carries the fix.

## What the survey found

The master plan's Phase 12 section was written on 2026-09-10, after
phase 11 merged, so it is less stale than most phase sections. Its
baseline is correct. Four of its other claims were wrong or have
gone out of date, and the survey found one gap that phase 11 left.
The false claims are corrected at source in this commit, in the
master plan's Status and Phase 12 sections and in the
`docs/plans/index.md` row. A later step does not need to redo that.

1. **The baseline is right, and every merge is on `develop`.**
   `git merge-base --is-ancestor <sha> origin/develop` holds for all
   five: `30168f0dd` (#3905), `9e41f07c6` (#3951), `fea4af7bd`
   (#3973), `9aab98d94` (#4007), `ff65c622a` (#4033). Each one's
   first-parent diff is confined to coalescing, queue and
   instrumentation files, plus plan documents and generated protos.

2. **`git log` over the #4033 range is wrong, and `git diff` is
   right.** The `queue-performance-phase-11-review-followup` branch
   merged `develop` into itself mid-flight. As a result,
   `git log ff65c622a^1..ff65c622a` lists about forty unrelated
   commits (renovate bumps, scheduler-reservations phase 7,
   agent-operation-deadlines phase 7). Only three commits are this
   plan's: `e27601c222`, `082d85c56c` and `61d262d4ca`. The
   first-parent *diff* is clean, at 20 files, all of them
   coalescing work. An auditor who reads commits rather than the diff
   would audit other plans' work under this plan's name.

3. **#4033 is not a "measurement write-up".** The master plan's
   Status called it one. In fact it answered the automated review of
   #4007, which merged at its original head before that review was
   addressed, and it carried code. It added `_reusable_priorities()`
   (`shakenfist/schema/operations/net_op.py:156`), which makes the
   enqueue-side dedup priority-aware through a new proto field and an
   `IN` filter. It added the `missing_key_columns` guard and its
   `key_column_missing` outcome
   (`shakenfist/operations/baseoperation.py:546-577`). It also added
   the upgrade-step tests, and recorded step 11h's measurement. That
   is 1,778 insertions. Corrected at source.

4. **The coalescing rule has more than three expressions now.** The
   master section names three: the enqueue-time check, the
   `queue_is_cluster_wide` skip and `COALESCIBLE_TASKS`. All three
   still exist. Since then, #4033 added two more, and the original
   list had already left several out. Step 12h's brief lists all
   eleven.

5. **The measurement tooling does have tests and documentation.** The
   master section said `tools/queue-wait-report.py` was "the kind of
   code that gets no test coverage and no documentation unless an
   audit asks". In fact `shakenfist/tests/test_queue_wait_report.py`
   has 34 tests and `shakenfist/tests/test_operation_timeline.py` has
   29. Both tools are documented in
   `docs/operator_guide/networking/overview.md` and
   `docs/developer_guide/database_internals.md`. The useful question
   is therefore not whether coverage exists but whether it is
   correct. Specifically: do the fixtures the tests feed the tools
   match what the code emits today? The fields in question are
   `wait_seconds`, `defer_count`, `queue_name`, `coalesce_outcome`,
   `coalesce_seconds` and `coalesce_folded`. Corrected at source.

6. **The audited code has moved since it merged.** Ten later commits
   touch audited files. Two of them matter here. `3e18fe40b2` ("Bound
   the cluster op dependency defer wait") added
   `DEPENDENCY_DEFER_BUDGET` to the defer path that phase 10
   instrumented. `70e9f2eeeb` ("Say why, not which plan step, in
   source comments") was a consistency fix, so plan-reference
   findings may already be resolved. The other eight are `9155b37641`,
   `2c08f1d2c4`, `b6f1f912f5`, `2b1c22ff4c`, `8c28e70651`,
   `fa642a1e0e`, `8eff2ce3b3` and `b7936e00bf`.

7. **One piece of deferred work was dropped rather than recorded.**
   `protos/database.proto:20-25` says the V1 coalescing RPCs
   (`ClaimCoalescibleSiblings`, `FindExistingCoalescibleOp`) are
   "retained unmodified for one release" for rolling upgrades. No
   issue tracks removing them. No plan section, Future work entry or
   follow-up mentions it, and no release has been tagged since
   (`git tag --contains 9aab98d94` is empty). This is phase 11's gap,
   and it was found during phase 11's completion check. It is seeded
   here as finding S1 rather than being treated as a reason to hold
   this phase.

8. **Coalescing's open issues are numerous, and the audit must not
   refile them.** They are #3948 (no deterministic concurrency
   coverage of the `FOR UPDATE` path), #3928, #3929 and #4235
   (functional coalescing tests flake in merge CI), #3864 (a
   completed operation's events are unreachable after 30 seconds),
   #3974 (queue sit and daemon time cannot be separated) and #4017
   (the `sf-queues` half of the multi-column key).

9. **The master section credited the cross-node fold to the wrong
   review.** It said "phase 11's own review found a fold that phase
   8's fix would have activated". In fact the automated review of
   phase 8's own pull request, #3880, found it; see "What review
   found" in the phase 8 plan. Corrected at source.

Phase 11 itself is genuinely complete. Its Execution row and the
index agree. Its merges are ancestors of `develop`. No phase 11
branch or worktree is left over. Three falsifiable Definition of
done items re-run clean against the tree:

* `grep -rn coalescible_target_column shakenfist/` returns nothing.
* `NetOp.coalescible_key_columns` reads
  `('network_uuid', 'node_uuid')`, and `network_ensure_mesh` is in
  `COALESCIBLE_TASKS`.
* The schema upgrade-step script prints `[]` and exits 0.

Its close-out was already done, so this phase's first commit has
nothing to close: the row reads Complete and the `Merged` cell was
reconstructed by #4160.

## Decisions

1. **The baseline is the five merge ranges, read with `git diff` and
   never with `git log`.** Each range is `<sha>^1..<sha>`:

   | Phase | PR | Range |
   |-------|----|-------|
   | 9 | #3905 | `30168f0dd^1..30168f0dd` |
   | 9 | #3951 | `9e41f07c6^1..9e41f07c6` |
   | 10 | #3973 | `fea4af7bd^1..fea4af7bd` |
   | 11 | #4007 | `9aab98d94^1..9aab98d94` |
   | 11 | #4033 | `ff65c622a^1..ff65c622a` |

   Wave 1's greps and the wave 2 mechanical sweep run once per range,
   and their output is pooled. That is the runbook's rule: git cannot
   express the union of these five as one range. `git log` is
   excluded because of survey finding 2. Any agent that needs commit
   messages for context is given the three #4033 commits by SHA.

2. **The judgment reviews read all five ranges together, and every
   finding is checked against current `develop`.** The shared block
   asks for the accumulated diff, because the interesting defects
   are between phases. A judgment agent therefore reads all five
   diffs in one pass and is asked about interactions across them.
   Each candidate finding is then confirmed against the file as it
   stands at `9efbff1481`, and recorded as one of three things:
   * **a finding**, if it is still true;
   * **fixed since by `<sha>`**, if it has gone, which is a result
     but not a finding;
   * **out of scope, filed**, if the defect is in one of the later
     commits but breaks something phases 9 to 11 built.

3. **Every step runs as a sub-agent, as `PLAN-TEMPLATE.md` and
   `PUSH-AUDIT.md` expect, but two of the runbook's model choices
   are raised.** The runbook gives 2a and 2b to sonnet. Here they go
   to opus, because the phase 8 lesson is about exactly these two
   headings: the audit asked whether the join was wrong and did not
   ask what the corrected join would then match. 2c stays on sonnet,
   and 2d stays on opus at high effort. Phase 8 ran inline because
   the operator asked for that in its session. Nobody has asked that
   here, so it is not a precedent.

4. **The judgment agents are read-only and run in parallel.** They
   do not edit files and do not run `pre-commit`, `tox` or `stestr`.
   pre-commit's stash covers the whole repository, so parallel agents
   in one worktree can silently revert each other's work, and stestr
   writes into `.stestr/`. An agent whose finding depends on running
   a test says which test and why, and the management session runs
   it. Each agent returns its findings as its final report, and only
   the management session writes to this plan.

5. **One heading is added: the coherence of the coalescing rule
   (step 12h).** This is the master plan's first named area. It is
   also the heading most likely to find a real defect, since it is
   where phase 8 found two. It is not folded into 2a because 2a's
   brief is a broad code-quality sweep. This question is narrow and
   adversarial: build an operation that one expression of the rule
   admits and another refuses, then say whether a real enqueue site
   can produce it.

6. **The tooling question goes to 2b and 2c as a producer/consumer
   contract, not as a coverage count.** Survey finding 5 shows that
   coverage exists. The live risk is that the emitter and the reader
   drift apart, and that `3e18fe40b2`'s defer budget created a
   terminal path the tools do not recognise. 2b checks whether the
   tests' fixtures derive from what the code emits, or are
   hand-written shapes that can rot.
   `test_every_outcome_the_code_records_is_reported` is the model of
   a fixture derived from the emitter. 2c checks the two docs pages
   against the tools' current output.

7. **The audit's pull request records findings, and a separate pull
   request fixes the blocking ones.** This is what both the
   `plan-push-audit-phase` shared block and the master plan's Phase
   12 section require. It is not what phase 8 did, because phase 8
   fixed its findings in its own pull request. Grades work like this:
   * **Blocking** means a defect on `develop` now. Its issue is
     filed with the `automated-fix-attempted` label at filing time,
     so the autofixer does not race the fix. The fix lands in a
     follow-up pull request, which also carries phase 12's
     close-out: its `Merged` cell, and Complete for the plan.
   * **Advisory** findings are filed as issues. Each issue's
     disposition, written in this plan, gives the reason it is not
     this plan's to fix. That counts as declined in writing.
   * **No blocking findings** means no follow-up pull request is
     opened for the sake of one cell. Phase 12 sets itself and the
     plan to Complete in its own pull request, and records no
     `Merged` cell.

   The fix branch does not wait for the audit to merge. It is cut
   from `develop` in its own worktree as soon as a blocking finding
   is graded. Only its close-out commit has to follow the audit's
   merge.

8. **Known issues are cited, not refiled.** Before filing anything,
   the management session runs `gh issue list --state all --search`
   for the finding's key terms, and states the queries it ran in
   the disposition. A finding that matches an open issue is recorded
   against that issue. It gets a comment only if it adds evidence
   the issue lacks. Survey finding 8's list is the floor, not the
   whole search.

   2b has one specific question to answer about the flake family:
   do the functional coalescing tests assert that an opportunistic
   optimisation fired? Three flake issues suggest so. If they do,
   that is a test-design finding, and it is advisory. It is recorded
   once, as a comment on #3929, which carries the thesis, rather
   than as a new issue.

9. **Wave 1 is relaxed exactly as phase 8 relaxed it (its decision
   4).** `pre-commit` and `tox` run against `develop` plus this
   phase's prose. A failure is therefore a pre-existing failure on
   `develop`. It is recorded, checked for whether the plan's ranges
   are implicated, and then wave 2 proceeds. The style greps and
   genprotos freshness check are about the ranges, so they are not
   relaxed.

10. **A clean heading is a result, and it names what it examined.**
    "Nothing found" is accepted only next to a list of the functions,
    files or tests that were read. If the whole audit is clean, the
    master plan records that in one sentence.

## Step plan

Every step after 12a takes `$RANGES` to mean the five ranges in
decision 1, and `$TIP` to mean `develop` at `9efbff1481`, the
commit this branch was cut from. Generated files under
`shakenfist/protos/` are exempt from judgment review. The genprotos
check in 12b covers them, and #4007's churn to
`agent_pb2_grpc.py`, `common_pb2_grpc.py` and similar files is
regeneration, not authorship.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 12a | medium | opus | none | **Done in the planning commit.** Write this plan, correct survey findings 3, 4, 5 and 9 at source, and add finding 2's `git diff` rule, in the master plan's Status and Phase 12 sections, link this file from the master plan's Execution table and from `docs/plans/index.md` with status In progress, run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: "Plan the queue performance push audit." |
| 12b | low | sonnet | none | Wave 1 and the wave 2 mechanical sweep, in `/srv/kasm_profiles/mikal/vscode/src/shakenfist/shakenfist-wt-qp-phase-12`. Run `pre-commit run --all-files`, then `tox`, then `tools/mermaid-lint.sh` (all five ranges touch markdown; check its exit status directly, never through a pipe), then `tox -e genprotos && git diff --exit-code shakenfist/protos` (#4007 and #4033 change `protos/database.proto`). Then, **for each of the five ranges in decision 1 separately**, run the four style greps from `PUSH-AUDIT.md`'s wave 1 and the five commands of its wave 2 mechanical sweep, substituting the range for `$RANGE` and using `git diff <sha>^1 <sha>`. Never use `git log` on these ranges (survey finding 2). Record every command and its literal output, or "no output", in a new `## Findings` → `### Wave 1` section of this plan, one sub-heading per range for the greps and sweep. If `pre-commit` or `tox` fails, record the failing test names, check with `git diff --name-only <sha>^1 <sha>` whether any range touched the failing module, and carry on (decision 9). Do not fix anything. Commit subject: "Record wave 1 of the phase 12 audit." |
| 12c | low | sonnet | none | Style conformance, judgment portion, read-only (decision 4). Apply `PUSH-AUDIT.md`'s "Style conformance — judgment portion" brief to all five ranges in decision 1, read with `git diff <sha>^1 <sha>`. Skip `shakenfist/protos/*_pb2*.py`. For each candidate, confirm it against the file at `$TIP` and classify it under decision 2. Pay particular attention to the field rename and unit-change rule: `coalesce_seconds` and `wait_seconds` are new event fields, and the outcome `not_cluster_wide` was renamed to `key_cannot_distinguish_queue` (with a mapping in `tools/queue-wait-report.py`'s `RETIRED_COALESCE_OUTCOMES`). Return a findings list with file, line, range and grade, or "Style checks passed" plus the files read. The management session records it. Commit subject: "Record the phase 12 style review." |
| 12d | medium | opus | none | 2a code quality, read-only (decision 4). Apply `PUSH-AUDIT.md`'s 2a brief, including all four of its shared blocks, to the five ranges together (decision 2), taking 12b's mechanical sweep output from this plan's Findings section as input. Areas this plan knows are worth the time: `shakenfist/mariadb.py`'s coalescing helpers (`_coalescible_key_pairs` 22682, `_coalescible_preflight` 23480, `_coalescible_key_clause` 23595, `_direct_find_existing_coalescible_op` 23643, `_direct_claim_coalescible_siblings` 23767) check the three-layer pattern; V1 and V2 RPCs coexist in `shakenfist/daemons/database/main.py` at 276, 307, 340 and 381, so check for duplicated bodies and registered Monitor counters. `tools/operation-timeline.py` is 1,425 lines and over the `source-file-size` threshold, so say whether it has a seam. `baseoperation.py:418-640` has comment blocks far longer than the code they explain, so apply `comment-proportion` rather than assume they earn it. **The phase 8 question** is whether the change, beyond being correct on its own terms, switches anything on. #4033's `_reusable_priorities` (`schema/operations/net_op.py:156`) changes which pending ops enqueue dedup may adopt, so say what it now matches that it did not before. Check every candidate at `$TIP`, against the ten later commits in survey finding 6. Return findings with file, line, range, grade and decision 2 classification. Commit subject: "Record the phase 12 code quality review." |
| 12e | medium | opus | none | 2b test review, read-only (decision 4). Apply `PUSH-AUDIT.md`'s 2b brief and its `functional-test-coverage` block to the five ranges together. Three questions are specific to this plan. (1) **Producer/consumer contract** (decision 6): for every event field `tools/queue-wait-report.py` and `tools/operation-timeline.py` read, establish whether the tests' fixtures are derived from what `shakenfist/operations/baseoperation.py` emits at `$TIP` (as `test_every_outcome_the_code_records_is_reported` is) or are hand-written dicts that can drift, and name each hand-written one. Check whether `3e18fe40b2`'s `DEPENDENCY_DEFER_BUDGET` gives an operation a terminal path the timeline tool misreads. (2) **The flake family** (decision 8): do `test_duplicate_network_work_is_coalesced` and `test_per_node_mesh_work_is_coalesced` in `shakenfist/deploy/shakenfist_ci/cluster_ci_tests/test_coalescing.py` assert that an opportunistic optimisation fired? Read #3928, #3929 and #4235 with `gh issue view`, say whether the tests' design guarantees the contention they assert, and suggest what a deterministic version would assert. (3) Is every new outcome branch in `baseoperation.py:562-630` (`key_column_missing`, `coalescing_unavailable`) covered by a unit test that reaches it through `execute()` rather than by setting the attribute? Do not refile #3948. Return findings grouped by file, with grades. Commit subject: "Record the phase 12 test review." |
| 12f | medium | sonnet | none | 2c documentation review, read-only (decision 4). Apply `PUSH-AUDIT.md`'s 2c brief and all of its shared blocks to the five ranges together. Check `docs/operator_guide/networking/overview.md` and `docs/developer_guide/database_internals.md` against the code at `$TIP`: every coalescing outcome name, every event field and every tool flag they mention must exist under that name, with nothing the code records left undescribed. Check that the safety argument is not stated differently in `shakenfist/daemons/network/workitem.py:51-160`, `shakenfist/schema/operations/net_op.py:60-130` and `_direct_claim_coalescible_siblings`'s docstring (`shakenfist/mariadb.py:23767`); this repeats a phase 11 done-criterion, now against a tree three later commits have touched. Apply `plan-references-in-code` and `plan-phase-references` to the diff's added lines, noting where `70e9f2eeeb` already fixed one. Also say whether this plan's row in `docs/plans/index.md` is still a one-line summary as `plan-status-vocabulary` intends; it is currently a paragraph of several hundred words. Return findings, or "No documentation gaps found" plus the pages read. Commit subject: "Record the phase 12 documentation review." |
| 12g | high | opus | none | 2d security review, read-only (decision 4). Apply `PUSH-AUDIT.md`'s 2d brief to the five ranges together. **Concurrency is the area that matters.** The fold flips sibling operations to `complete` from one worker while another worker may hold them, and its soundness rests on the four-link argument in `shakenfist/daemons/network/workitem.py:51-160`. Re-derive each link at `$TIP`, not from the comment, including whether any of the ten later commits in survey finding 6 changed routing or dequeue. Check that the V1 RPCs (`daemons/database/main.py:276,307`) cannot be reached by a current client, so that the fold cannot run on the first column alone, as the proto comment at `protos/database.proto:20-25` promises. Check that `_coalescible_preflight` (`mariadb.py:23480`) refuses a key column outside `_COALESCIBLE_TARGET_COLUMNS` (23463) before any SQL is built, and that no column name reaches SQL by interpolation. Check that `execution duration` events add nothing broadly readable that should not be there (`queue_name` embeds node uuids). Return findings with severity, file, line, vulnerability class and recommended fix. Commit subject: "Record the phase 12 security review." |
| 12h | high | opus | none | The coherence of the coalescing rule, read-only (decisions 4 and 5). The rule is "two operations may be folded together exactly when they do the same work and the same single-threaded drainer would have run both". It is expressed in eleven places at `$TIP`: (1) `COALESCIBLE_TASKS`, `schema/operations/net_op.py:107`; (2) `COALESCIBLE_KEY_COLUMNS`, :129; (3) `_coalescible_keys` and its `KeyError`, :132; (4) `_reusable_priorities`, :156; (5) the enqueue-time `InvalidCoalescibleEnqueue` guard, :287-310; (6) the single-task dedup condition, :318-327; (7) the fold's guards in `operations/baseoperation.py:474-577` (`skip_due_to_empty_queue`, `queue_is_cluster_wide`, `key_distinguishes_node`, `missing_key_columns`); (8) `NetOp.coalescible_key_columns`, `operations/net_op.py:67`, and its other reader at `baseoperation.py:223`; (9) the `sf-net` routing key, which the docstring at `daemons/network/workitem.py:140-160` says uses the first key column; (10) the SQL in `_coalescible_preflight`, `_coalescible_key_clause` (`None` binds `IS NULL`) and the two `_direct_*` functions, `mariadb.py:23463-23900`; (11) the static call-site walk in `shakenfist/tests/schema/test_net_op_coalescing.py`. For each pair that could disagree, construct the operation that one admits and another refuses: by queue, by priority, by `node_uuid` present-but-`None` versus absent, by multi-task list, by queue family (`network` versus `clusteroperation`). Then say whether any real enqueue site can produce it; the call sites are in `network/network.py`, `external_api/instance.py`, `daemons/network/maintain.py` and `daemons/queues/startup_tasks.py`. Ask in particular whether enqueue-side dedup (priority-filtered, set 4) and the fold (priority-blind by design, per #4033's review item 1) can disagree in a way that loses work rather than merely delaying it. Reproducing a divergence against the in-memory database in `shakenfist/tests/test_mariadb_coalescing.py` counts as proof; describe the test and the management session runs it. Report each pair as consistent, divergent but unreachable (say why), or divergent and reachable (a blocking finding). Commit subject: "Record the coalescing rule coherence audit." |
| 12i | medium | opus | none | Management session, not a sub-agent: grading and disposition are decisions. Pool 12b-12h's findings, together with seeded finding S1 (survey finding 7: the V1 RPC removal is untracked). Grade each finding blocking or advisory, and give each a disposition (decision 7). Present the graded table to the operator before filing anything (see Back brief). Then run decision 8's duplicate searches, file the issues, and write a `### Disposition` table into this plan. Add an "Audit of phases 9 to 11" subsection to the master plan's Phase 12 section, and add the V1-removal issue to its "Follow-ups not landed here". If no finding is blocking, set phase 12 and the plan to Complete (decision 7, last case). Otherwise leave them In progress and cut the fix branch. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit subject: "Record the phase 12 audit disposition." |

12c to 12h are independent of each other and run in parallel once 12b
has reported. Each read-only step's report is recorded into this plan
by the management session, under the heading named in its commit
subject, after that session has checked at least one of the step's
claims against the file itself.

## Risks and mitigations

* **The audit rubber-stamps merged code.** That happened to phase 8,
  which asked the right question of the join and the wrong one of
  what the join then matched. *Mitigation:* decision 10 makes every
  clean heading list what it read. 12d and 12h each carry the phase 8
  question explicitly. Before recording a report, 12i rejects it if
  it names no functions.
* **An agent audits other plans' work.** `git log` over the #4033
  range lists about forty unrelated commits (survey finding 2).
  *Mitigation:* every brief specifies `git diff <sha>^1 <sha>`, and
  decision 1 forbids `git log`. 12i rejects any finding whose file is
  absent from `git diff --name-only` of its stated range.
* **Findings against code that has since changed.** Ten later commits
  touch the audited files. *Mitigation:* decision 2's three
  classifications. 12i spot-checks one "still true" claim per report
  at `$TIP`.
* **Parallel agents interfere.** *Mitigation:* decision 4. Only 12b,
  which runs alone first, and 12i write files or run the suite.
* **Duplicate issues.** Coalescing already has seven open issues.
  *Mitigation:* decision 8, with the search queries stated in the
  disposition so a reviewer can re-run them.
* **A blocking finding sits on `develop` for a pull request cycle.**
  This is the cost of decision 7. *Mitigation:* the fix branch is cut
  as soon as the finding is graded, not after the audit merges.
* **Wave 1 is slow or fails for reasons outside these ranges.** `tox`
  is the full matrix. *Mitigation:* decision 9, plus the back-brief
  gate after 12b, so a broken `develop` is reported before wave 2's
  cost is spent.

## Definition of done

* `## Findings` in this plan has a sub-heading for each of: wave 1;
  the mechanical sweep; style; 2a; 2b; 2c; 2d; coalescing rule
  coherence; and disposition. Every heading except disposition
  names the files or functions it examined.
* Wave 1 records literal output for 20 style-grep runs (four greps
  across five ranges) and 25 sweep runs (five commands across five
  ranges). The check is that each range's sub-heading lists all nine
  commands:

  ```
  for s in 30168f0dd 9e41f07c6 fea4af7bd 9aab98d94 ff65c622a; do
    grep -c "$s" docs/plans/PLAN-queue-performance-phase-12-push-audit.md
  done
  ```

  This gives a non-zero count for every range. Reading the
  sub-heading confirms the nine commands are there.
* `tox -e genprotos && git diff --exit-code shakenfist/protos` exits 0,
  with the result recorded, or the stale stubs are a recorded
  finding.
* Step 12h reports a verdict for every pair it considered, against
  all eleven expressions.
* Every finding carries a grade, a disposition (fixed in #NNNN,
  filed as #NNNN, recorded on an existing issue, or declined with a
  reason) and the decision 2 classification. No issue filed by 12i
  duplicates one from survey finding 8, and the search queries
  behind that claim are written in the disposition table.
* Seeded finding S1 is dispositioned, and an issue for removing the
  V1 coalescing RPCs is named in the master plan's "Follow-ups not
  landed here".
* `python3 tools/check-plan-status.py` exits 0, and
  `pre-commit run --all-files` is clean.
* The plan is Complete only when no blocking finding remains open.
  If it is Complete in this phase's own pull request, phase 12's
  `Merged` cell is left empty, as the `plan-phase-landing` block
  requires.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns
with it.

Two gates beyond that, both cheap to stop at and expensive to get
wrong:

* **After 12b**, report whether wave 1 passed before spending on
  wave 2's five parallel agents. If something failed for a reason
  unrelated to these ranges (decision 9), say so explicitly rather
  than presenting it as a finding.
* **In 12i, before filing any issue**, present the graded findings
  table to the operator. Issues are public, and the blocking or
  advisory split decides whether a follow-up pull request exists at
  all. Both are easier to agree before filing than to unwind after.
