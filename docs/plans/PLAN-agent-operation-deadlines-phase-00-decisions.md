# Agent operation deadlines phase 0: research and decisions

## Prompt

This phase resolves the six open questions in
`PLAN-agent-operation-deadlines.md` into recorded decisions, so that
phases 1-7 implement against settled semantics. It produces no
production code. Ground every answer in the tree: the sidechannel
daemon (`shakenfist/daemons/sidechannel/main.py`), the agent operation
object (`shakenfist/operations/agentoperation.py`,
`shakenfist/operations/baseoperation.py`), the preflight cluster
operation (`shakenfist/operations/node_aop_op.py`), and the client
await paths in the sibling `client-python` repository and the
functional CI suite (`shakenfist/deploy/shakenfist_ci`).

**Planning effort:** high (this is the decisions phase for a plan
whose subtle parts are locking, state machines, and failure
semantics). Review effort for the recorded decisions: high.

**Process note:** this phase plan lives on the same branch as the
master plan (`worktree-agentop-deadlines`) by explicit operator
decision, because the master plan has not yet merged and phase 0's
research does not depend on merged code. The standard
worktree-per-phase flow resumes from phase 1.

## Scope

In: answering the master plan's six open questions, recording each as
a numbered decision with rationale in the master plan (replacing the
"Open questions" section), and updating any design-sketch text those
decisions change.

Out: all implementation, including the phase 1 field-mask work; any
client-python changes; filing issues for unrelated bugs found during
research (they are recorded here and filed, but not fixed).

## What the survey found

The survey checked the master plan's factual claims against the tree
and found none false. It did, however, substantially answer three of
the six questions outright and reframe a fourth:

1. **The reaper seam already exists.** `reap_instance_executors()`
   (`daemons/sidechannel/main.py:972`) runs at the top of every
   dispatcher pass (`main.py:1047`), already serialised with dispatch.
   It currently joins dead executor threads and frees the slot but
   does nothing to the operation. Extending it is the natural home
   for the reaper (open question 5). One gap it must cover: the
   `self.executors` dict is in-memory, so after a daemon restart an
   operation stuck in `EXECUTING` has no executor entry at all — the
   sweep must treat "no entry for a monitored instance with an
   `EXECUTING` operation" as reapable, not just "entry whose thread
   is dead".
2. **Preflight can be slow, and has an obvious check site.**
   `NodeAgentopOp._preflight()` (`operations/node_aop_op.py:91`) calls
   `Blob.ensure_local()`, which copies the blob to the hypervisor —
   potentially minutes for a large put-blob. Since the deadline counts
   from REST receipt, it must be evaluated inside `_preflight()`
   (before and after `ensure_local()`), answering open question 4
   concretely.
3. **Old clients do not distinguish terminal states anyway.**
   `await_agent_command()` in `client-python`'s `apiclient.py:1418`
   polls until `state == 'complete'` and otherwise runs out its own
   timeout — it does not break on `error` today. A new `expired`
   state therefore cannot regress old clients relative to `error`:
   both are equally unrecognised. This weakens the compatibility
   argument against a distinct state (open question 1). It is also a
   client bug worth an issue: any terminal failure today costs the
   caller its full await timeout.
4. **Historical chunk-gap data does not exist.** Get-file diagnostics
   log at INFO only at request (`main.py:543`), stat-with-size
   (`main.py:565`) and completion-with-bytes (`main.py:650`);
   per-chunk logging is debug. So open question 2 can be informed by
   whole-transfer durations and sizes from CI journals, but not by
   intra-transfer gap distributions — those would need a freshly
   instrumented run, which is not worth blocking phase 0 on.
5. Verified unchanged premises: `AGENT_OPERATION_EXECUTION_TIMEOUT`
   is 900 at `main.py:56`; `update_agent_operation_attributes`
   (`mariadb.py:18807`) takes no field mask, so the phase 1 premise
   holds; `BaseOperation.ACTIVE_STATES` (`baseoperation.py:38`)
   excludes `error`, so an `expired` state would be filtered from
   default iterators exactly the way `error` already is.

No corrections to the master plan were required at source.

## Decisions this plan already takes

The survey answers make these judgement calls now rather than
deferring them to the research steps:

1. **The reaper is an extension of `reap_instance_executors()`**,
   running every dispatcher pass, covering both dead-thread and
   no-entry cases. Rationale: it is the only place already serialised
   with dispatch, which is what makes reap-then-dispatch race-free.
   (Resolves open question 5; the cadence question reduces to "every
   pass, with the database read gated on the instance actually having
   a non-terminal operation", which step 3 records.)
2. **The deadline applies during `PREFLIGHT`**, checked in
   `_preflight()` before and after `ensure_local()`. Rationale: the
   blob copy is the single longest pre-queue delay in the system and
   the whole point of receipt-anchored deadlines is that such time
   counts. (Resolves open question 4.)
3. **Tentative, pending step 1 data: the progress timeout default is
   60 seconds** and the expiry-state and execute-retry questions get
   proposed answers (`expired` as a distinct terminal state; `execute`
   not retried, via a per-command `retryable` capability flag
   defaulting true for transfers and false for `execute`). These are
   recorded as proposals for the operator to confirm at the back
   brief, informed by steps 1 and 2.

The decision most likely to be argued with is the distinct `expired`
state (versus `ERROR` with a machine-readable message). The case for
it: queryability for periodic callers, honest semantics ("your budget
ran out" is not "the operation failed"), and the survey finding that
old-client compatibility is a non-issue because old clients treat all
non-complete states identically. The cost is one more state in
`state_targets`, the state machine documentation, and the phase 6
client work it was already doing.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 0a | high | sonnet | none | Measure real agent transfer behaviour. From the most recent five successful cluster CI runs (use the `ci-status` helper to find them; artifacts contain node journals — note Loki dumps in CI artifacts are empty, use the journals), extract sidechannel INFO lines 'Requesting file from agent', 'Received file stat from agent' (has size) and 'Completed file transfer from agent' (has bytes), pair them per operation, and report count, p50/p95/max durations and sizes, plus the same for test_agentops wall times. If journals lack these lines (the diagnostics only landed recently), say so explicitly and report how many runs were checked — an honest 'no data' is a valid result. Output: a short data table appended to this phase plan under 'Measurement results'. |
| 0b | medium | sonnet | none | Audit every consumer of agent operation state for compatibility with a new terminal state 'expired'. Enumerate with file:line: `state_targets` and `ACTIVE_STATES` in shakenfist/operations/; `shakenfist/external_api/agentoperation.py` and instance.py agent endpoints; `docs/developer_guide/state_machine.md`; the await/poll loops in ../client-python/shakenfist_client/ (note `await_agent_command` at apiclient.py:1418 already ignores 'error' — record any similar loops); agent-operation awaits in shakenfist/deploy/shakenfist_ci/; and any state handling in the sfui repository if it lists agent operations. For each site state: breaks / ignores / needs update in a later phase. Output: a table appended to this phase plan under 'State audit'. |
| 0c | high | opus | none | Management session with the operator: back-brief the proposed decisions (see gate below), incorporating 0a/0b outputs; then edit the master plan — replace the 'Open questions (resolve in phase 0)' section with numbered decisions and rationale, update the design sketch and phases table where decisions change them, and update the index.md phase 0 row description to reflect what was actually decided. |
| 0d | low | haiku | none | File the client-python issue found by the survey: await_agent_command polls to full timeout on any terminal failure state instead of failing fast (apiclient.py:1418). Reference it from the master plan's phase 6 row so the fix lands with the client deadline work. |

Each step is its own commit; 0c and 0d may combine if the edits are
small.

## Risks and mitigations

- **CI journals predate the INFO diagnostics** (they landed with PR
  #3506), so step 0a may find nothing. Mitigation: 0a's brief makes
  "no data" a reportable result; the fallback is adopting 60 seconds
  as a deliberately conservative default and recording in the master
  plan that the new INFO logging makes post-deploy measurement (and a
  config change) cheap. The operator judges this at the back brief.
- **The state audit misses a consumer** (some tool greps states by
  string). Mitigation: 0b's brief requires enumeration by grep across
  all three repositories, and phase 7's functional test asserting an
  expired operation's external view will catch a missed server-side
  site before release.
- **Decisions recorded here drift from the master plan PR review**
  (both are on the same unmerged branch). Mitigation: this is the
  accepted trade-off of the operator's sequencing decision; the back
  brief happens in-review, and any review reshaping reopens 0c before
  merge.

## Definition of done

- The master plan contains no section titled "Open questions" and no
  text saying "resolve in phase 0"; each of the six questions appears
  as a numbered decision with a stated rationale.
- This file contains a "Measurement results" section with either the
  transfer statistics table or an explicit statement of how many runs
  were checked and that no data was found.
- This file contains a "State audit" section listing every
  state-consuming site found, each with a file:line reference and a
  breaks/ignores/needs-update verdict, and no site marked "breaks"
  is left without a phase assignment.
- The client-python fail-fast issue exists on GitHub and is
  referenced from the master plan's phase 6 row.
- `pre-commit run --all-files` passes.

## Back brief

Before step 0c edits the master plan, back-brief the operator on: the
proposed answer to each of the six questions, the measurement (or
absence of measurement) behind the progress-timeout default, and the
state-audit verdicts. The `expired`-versus-`ERROR` decision and the
progress-timeout default are the two the operator most needs to
confirm; do not record either without explicit agreement.
