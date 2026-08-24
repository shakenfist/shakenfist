# PLAN: Queue performance phase 8 -- push audit

Planning effort: medium. Review effort: medium.

## Why this phase exists

Every step of [PLAN-queue-performance.md](PLAN-queue-performance.md)
has merged, and step 7 closed the question the plan was written to
answer. What has not happened is a single review of the plan's work
*as a whole*. Steps 1-6 were reviewed as part of PR #3194, a 105 file
`network-facade` refactor in which the queue changes were a minority
of the diff; step 7 was reviewed on its own. Nobody has looked at the
queue-performance changes as one body of work.

`PUSH-AUDIT.md` is the repository's audit template. It is normally a
pre-push gate, run against `develop...HEAD`. Here it runs
retrospectively, which changes the baseline but not the questions.

## Scope

**In scope.** The code this plan added or changed, across both
merges, audited under the `PUSH-AUDIT.md` headings: wave 1
mechanical checks, and wave 2's code quality, test coverage,
documentation and security reviews.

**Out of scope.** The rest of PR #3194. The `network-facade` refactor
is far larger than this plan and auditing it here would be auditing
somebody else's change under this plan's name. Where a queue-performance
change depends on a network-facade one, the dependency is noted rather
than followed.

**Out of scope.** Fixing anything the audit finds, unless it is
trivial or blocking. The plan's own convention -- established by
step 7 -- is that a review phase records and files, rather than
expanding into the work it discovers. Blocking findings are fixed
here because a blocking finding is by definition not something to
leave on `develop`.

## Decisions

1. **The audit baseline is the plan's commit range, not
   `develop...HEAD`.** `PUSH-AUDIT.md` assumes unmerged work. This
   work is merged, so `git diff develop...HEAD` is empty and every
   command in the template would report success against nothing. The
   baseline is instead the union of the two merges which carried this
   plan:

   | Steps | Merge | Range |
   |-------|-------|-------|
   | 1-6 | PR #3194 `57867532c` | `57867532c^1..57867532c` |
   | 7 | PR #3865 `2daebabc1` | `2daebabc1^1..2daebabc1` |

2. **Within PR #3194, the audit is restricted to the files this plan
   changed.** Auditing all 105 files would not be this plan's audit.
   The queue-performance footprint in that merge is:

   * `shakenfist/daemons/daemon.py` (batched dequeue, disk-busy gate)
   * `shakenfist/daemons/network/workitem.py` (dispatcher, defer backoff)
   * `shakenfist/daemons/queues/workitem.py` (dispatcher, wait event)
   * `shakenfist/daemons/queues/startup_tasks.py`
   * `shakenfist/operations/baseoperation.py` (queue lists, coalescing)
   * `shakenfist/schema/operations/net_op.py`, `node_net_op.py`, `util.py`
   * `shakenfist/mariadb.py` and `protos/database.proto`, restricted to
     `dequeue_work_items`, `find_existing_coalescible_op` and
     `claim_coalescible_siblings`

   `mariadb.py` changed by 1,057 insertions in that merge, the large
   majority of which is network-facade work. Only the three functions
   above belong to this plan.

3. **The audit is run inline, not by sub-agents.** `PUSH-AUDIT.md`
   describes four judgment agents. The findings are what the phase is
   for; the mechanism is not load-bearing, and the operator has asked
   in this session that sub-agents not be spawned. Each of the four
   briefs is worked through in turn against the same diff, and the
   findings are reported under the same four headings so the output
   is comparable to an agent-run audit.

4. **Wave 1's exit condition is relaxed in one specific way.**
   The template says to stop if `pre-commit` or `tox` fails. Those
   run against the working tree, which is `develop` plus this phase's
   documentation -- so a failure would be a pre-existing failure on
   `develop`, not something this plan introduced. If wave 1 fails,
   record it, check whether the plan's own diff is implicated, and
   continue to wave 2 rather than stopping. Stopping would only be
   correct if this branch were about to be pushed as code.

5. **Findings are graded blocking or advisory, and blocking findings
   are fixed in this phase.** Advisory findings are filed as issues
   and listed here. A finding which is real but out of this plan's
   scope -- network-facade code reached by a queue-performance change
   -- is filed and named as such.

6. **A clean audit is a result.** If a heading finds nothing, it says
   so in one sentence. An audit which reports nothing under every
   heading is recorded as such rather than padded.

## Step plan

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 8a | low | opus | none | Add phase 8 to the master plan, write this phase plan, register it in `docs/plans/index.md`, and set the master plan's status back to In progress. Run `tools/check-plan-status.py` and `pre-commit run --all-files`. Commit. |
| 8b | low | opus | none | Wave 1. Run `pre-commit run --all-files` and `tox`. Run the template's style greps against the two ranges in decision 1 rather than `develop...HEAD`. Confirm whether proto stubs are fresh, given `protos/database.proto` is in scope. Record results. |
| 8c | medium | opus | none | Wave 2 mechanical sweep plus the 2a code-quality brief, over the decision-2 file list. The SQL-pushdown and cached-FK-list rules are blocking; check `dequeue_work_items`, `find_existing_coalescible_op` and `claim_coalescible_siblings` for the three-layer direct/gRPC/public pattern and registered Monitor counters. |
| 8d | medium | opus | none | The 2b test-coverage brief. `find_existing_coalescible_op` and `claim_coalescible_siblings` are concurrency primitives; check for adversarial coverage (the enqueue race the plan acknowledges in step 5, terminal-state siblings, an empty task list) and for functional coverage under `shakenfist/deploy/shakenfist_ci`. |
| 8e | medium | opus | none | The 2c documentation brief. The plan changed operator-visible queue behaviour and event payloads; check `docs/operator_guide/networking/overview.md`, `docs/developer_guide/`, `ARCHITECTURE.md` and `AGENTS.md` against the code, and apply the README, LLM-doc and plan-phase-reference shared blocks. |
| 8f | high | opus | none | The 2d security brief. Concurrency is the live area: the coalescing fold marks sibling operations complete from one worker while another may hold them, and the plan documents a routing invariant in `network/workitem.py` which the fold depends on. Check that invariant holds, check for SQL built by interpolation in the three new functions, and check whether the wait event leaks anything into the broadly-readable event log. |
| 8g | medium | opus | none | Grade every finding, fix the blocking ones, file the advisory ones, and write the results into this plan and the master plan. Set both statuses. |

## Risks and mitigations

* **The audit rubber-stamps merged code.** Reviewing something that
  already shipped invites confirming it. Mitigation: each heading must
  name what it actually examined -- a function, a file, a test -- and
  "nothing found" is only acceptable alongside that list. Step 8g
  checks this before writing the results.
* **Scope creep into network-facade.** The queue changes sit inside a
  much larger refactor and the boundary is a judgement call.
  Mitigation: decision 2 pins the file list; anything outside it is
  filed rather than fixed, and named as out of scope.
* **Blocking findings on merged code have no cheap fix.** A blocking
  finding here means something is wrong on `develop` right now.
  Mitigation: that is the point of running the audit; the fix lands in
  this phase's PR, and if it is too large for that, the phase says so
  and files it at high priority rather than silently downgrading it to
  advisory.

## Definition of done

* Every one of the four wave 2 headings has a written result naming
  what was examined.
* Wave 1's four commands and four style greps have been run against
  the decision-1 ranges, with output recorded -- not asserted.
* `git diff 57867532c^1 57867532c -- <decision 2 file list>` and
  `git diff 2daebabc1^1 2daebabc1` have both actually been read, not
  just summarised from the plan.
* Every finding carries a grade (blocking or advisory) and a
  disposition (fixed here, filed as #NNNN, or declined with a reason).
* No blocking finding is left unresolved.
* The master plan's Execution table and `docs/plans/index.md` agree
  with each other and with `tools/check-plan-status.py`.
* The master plan's status is Complete only if no blocking finding
  remains open.

## Findings

*(written by step 8g)*

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns
with it.

Gate: after step 8b, report whether wave 1 passed before spending on
wave 2. If wave 1 fails for a reason unrelated to this plan --
decision 4 -- say so explicitly rather than presenting it as an audit
finding.
