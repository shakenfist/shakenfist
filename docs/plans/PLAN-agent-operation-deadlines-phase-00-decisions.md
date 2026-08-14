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

## Results

### Measurement results (step 0a)

**Runs checked** — the five most recent successful `merge_group` runs
of the "Functional tests" workflow (all after PR #3506's merge,
ff259930d, 2026-07-26):

| Run id | Date (UTC) | Queue branch (PR) |
|---|---|---|
| 31663812079 | 2026-08-13 | merge queue, pr-3726 |
| 31640716414 | 2026-08-12 | merge queue, pr-3727 |
| 31576979115 | 2026-08-12 | merge queue, pr-3714 |
| 31225327822 | 2026-08-07 | merge queue, pr-3614 |
| 31219864140 | 2026-08-07 | merge queue, pr-3653 |

**Transfer stats** — parsed from `sf-sidechannel` JSON journal lines
in the `bundle-shakenfist-full-guests` artifacts
(`bundle/sf*/_commands/journalctl-sf-units`); each request paired
with its completion on the `agent_operation` uuid. Exactly 10
completed transfers per run, 50 total.

| Metric | count | min | p50 | p95 | max |
|---|---|---|---|---|---|
| Duration (s, request-to-completion) | 50 | 0.146 | 0.249 | 0.417 | 2.834 |
| Size (bytes) | 50 | 7 | 813 | 86,177 | 625,094,656 |

- The two slowest/largest transfers are the deliberate big-file
  tests: 625 MB in 2.83 s (~220 MB/s) and 494 MB in 1.99 s
  (~248 MB/s). Every other transfer (48/50) finished in under 0.44 s.
- 5 request lines had no stat or completion — all are
  `path=/tmp/nosuch`, i.e. the deliberate `test_get_missing_file`
  negative case (one per run). No genuinely hung or lost transfer was
  observed.
- **Bounding internal gaps**: per-chunk logs are debug-only and
  absent from these journals, so intra-transfer gaps are not directly
  measurable. The request-to-completion duration is therefore the
  upper bound on any internal stall within a transfer: the worst case
  across all 50 transfers is **2.834 s**, more than 20x under a 60 s
  progress timeout.

**test_agentops wall-clock times** (stestr per-test timings from the
Guests job logs; runs 31663812079 / 31219864140): individual tests
range 117.9-284.0 s (worst: `test_interface_plug_and_exec_reboot` at
~283 s in both runs). Test wall times are dominated by instance
boot/agent-ready waits, not transfer time — all 50 transfers in a run
sum to well under 10 s.

**Conclusion for the 60 s progress-timeout default**: the slowest
observed CI transfer (625 MB) completes in under 3 s end-to-end, so a
60 s progress timeout carries roughly 20x headroom over the worst
complete transfer observed, let alone any internal gap within one.

### State audit (step 0b)

| Site | file:line | Verdict | Notes |
|---|---|---|---|
| `state_targets` map | `shakenfist/operations/agentoperation.py:25` | NEEDS UPDATE (4) | Phase 4 adds `expired` plus transitions into it (from initial/preflight/queued/executing) and `expired -> deleted` so API delete keeps working. |
| `BaseOperation.ACTIVE_STATES` | `shakenfist/operations/baseoperation.py:38` | NEEDS UPDATE (4) | `{created, queued, executing, complete}` — `error` already excluded. Feeds `dbo_iter` no-prefilter default (`baseobject.py:809-811`) and `from_db_by_ref` (`baseobject.py:393`), so expired ops vanish from default iteration exactly like error ops do today. Decide in phase 4 whether that is desired. |
| Instance delete sweep of agent ops | `shakenfist/instance.py:1084-1088` | NEEDS UPDATE (4) | `AgentOperations([instance_filter])` uses the ACTIVE_STATES prefilter, so expired ops (like error ops today) are not soft-deleted with their instance; they must be reaped some other way. |
| Hard-delete sweep | `shakenfist/constants.py:190` (`FINAL_OBJECT_STATES`) + `shakenfist/daemons/cluster/scheduled_tasks.py:559-602` | NEEDS UPDATE (4) | `{deleted, complete, abort}` only. Without adding `expired`, expired ops are never hard-deleted and leak state rows forever (cf. issue 3532 class of bug). Must be in the phase 4 change set. |
| `Instance.agent_operation_next` | `shakenfist/instance.py:2108-2128` | IGNORED safely | Explicitly matches QUEUED (dispatch) and INITIAL/PREFLIGHT (wait); every other state — which would include `expired` — falls to the retire branch and is popped. Correct behaviour for free. |
| Instance external view queue | `shakenfist/instance.py:639-644` | IGNORED safely | Renders `external_view()` of whatever is on the queue; state is an opaque string. |
| API enqueue endpoints | `shakenfist/external_api/instance.py:1682,1726,1770` | IGNORED safely | Only set PREFLIGHT/QUEUED on freshly created ops; never read state. |
| API get/delete/list endpoints | `shakenfist/external_api/agentoperation.py:107-122,195-207` | IGNORED safely | State passed through opaquely; `delete()` works provided `expired -> deleted` is in `state_targets` (phase 4). |
| Sidechannel executor exit guard | `shakenfist/daemons/sidechannel/main.py:339-346` | IGNORED safely | `== STATE_EXECUTING` guard means an op concurrently flipped to `expired` is not clobbered to ERROR. |
| Sidechannel completion write | `shakenfist/daemons/sidechannel/main.py:817-821` | IGNORED safely | Guarded `== STATE_EXECUTING` before `-> COMPLETE`; expired op is left alone. |
| Sidechannel unguarded error writes | `shakenfist/daemons/sidechannel/main.py:470,476,794,848` | NEEDS UPDATE (4) | Unconditional `state = STATE_ERROR`; if phase 4 expiry can fire mid-execution, `expired -> error` violates `state_targets` and raises `InvalidStateException` (`baseobject.py:587-591`). Phase 4 must guard these or allow the transition. |
| Sidechannel error-abort check | `shakenfist/daemons/sidechannel/main.py:869` | NEEDS UPDATE (4) | `== STATE_ERROR` empties the remaining command list; an op expired mid-flight would keep executing commands unless phase 4 adds `expired` here (this is the enforcement seam). |
| `node_aop_op._preflight` | `shakenfist/operations/node_aop_op.py:92,107,110` | IGNORED safely | `!= STATE_PREFLIGHT` early-returns, so an expired op is silently not promoted to QUEUED. |
| `node_aop_op.dispatch_task` error path | `shakenfist/operations/node_aop_op.py:89` | NEEDS UPDATE (4) | Unguarded `aop.state = Instance.STATE_ERROR` in the except block; from `expired` this raises. Same guard as sidechannel needed. |
| `_ACTIVE_OPERATION_STATES` | `shakenfist/mariadb.py:4785` | IGNORED safely | Cluster-operation gating only (not agent ops); `expired` being absent correctly reads as terminal anyway. |
| State machine docs | `docs/developer_guide/state_machine.md:21-59` | NEEDS UPDATE (7) | Documents the seven current states and the mermaid graph; needs `expired` node, edges and prose. Also `docs/developer_guide/api_reference/agentoperations.md:54` example (opaque, optional). |
| CI await loops | `shakenfist/deploy/shakenfist_ci/base.py:671`; `smoke_ci_tests/test_agentops.py:52,96,148-153,207,218,236`; `guest_ci_tests/test_agentops.py:37,68,112,164-169` | NEEDS UPDATE (7) | All spin `while state != 'complete'` (base.py loop has no timeout at all); an expired op hangs the test until the suite timeout, same as `error` today. Should fail fast on terminal states. `cluster_ci_tests/test_api.py:48,59` is cluster ops, unaffected. |
| `sf-ctl` object-type list | `shakenfist/client/ctl.py:485` | IGNORED safely | Type name list only; state values opaque. |
| Unit tests | `shakenfist/tests/test_daemon_sidechannel_executor.py:51,56`; `test_instance.py:737` | IGNORED safely | Use existing states; phase 4 adds new cases rather than fixing breaks. |
| Metrics / eventlog / cleaner | (none found) | IGNORED safely | No Prometheus metric, eventlog path, or cleaner-daemon code enumerates agent-op states; events record transitions generically and the cleaner (`daemons/cleaner/`) never touches agent ops. |
| Client `_await_agentop` | client-python `shakenfist_client/apiclient.py:1169-1181` | NEEDS UPDATE (6) | Polls `== 'complete'` until async deadline then returns the op as-is; `expired` (like `error` today) burns the whole deadline instead of returning early. |
| Client `await_agent_command` | client-python `shakenfist_client/apiclient.py:1418-1441` | NEEDS UPDATE (6) | Confirmed: polls only for `'complete'`, ignores `error`; expired op waits out the full timeout then raises `AgentAwaitTimeout` (state does appear in the message). Should short-circuit on terminal states — client-python#363. |
| Client `await_agent_fetch` | client-python `shakenfist_client/apiclient.py:1488-1503` | NEEDS UPDATE (6) | Same pattern with a hardcoded 120 s loop; raises `AgentCommandError` including the state. |
| Client CLI rendering | client-python `shakenfist_client/commandline/instance.py:264,274` | IGNORED safely | Prints `agentop['state']` as an opaque string in table/CSV/JSON. |
| sfui | (repository) | IGNORED safely | The sfui repo contains no references to agent operations at all (recursive grep for agent_operation/agentoperation/agentop is empty), so it is unaffected. |

**BREAKS verdicts: none.** The two near-misses are the unguarded
`state = STATE_ERROR` writes (sidechannel `main.py:470,476,794,848`;
`node_aop_op.py:89`) and the `== STATE_ERROR` abort check
(`main.py:869`): they cannot break today because nothing produces
`expired` yet, and they only become reachable-from-expired if phase 4
allows expiry of an EXECUTING op — so they are phase 4 design
obligations, not current breakage. Everything else either
string-matches specific states (falling through safely on unknowns)
or treats state as opaque.

Grep patterns used: `ACTIVE_STATES|state_targets`,
`STATE_(QUEUED|PREFLIGHT|EXECUTING|COMPLETE|ERROR|DELETED)`,
`'complete'|'executing'|'queued'|'preflight'` (and double-quoted
forms), `agentop|agent_operation|AgentOperation|AGENTOPERATION`,
`FINAL_OBJECT_STATES`, `hard_delete|STATE_DELETED`, and
`op['state']`-style dict access, across the server repo (including
`shakenfist/deploy/shakenfist_ci/`, `daemons/`, `docs/`),
client-python's `shakenfist_client/`, and the sfui tree.

### Back brief outcome (step 0c)

The operator confirmed the distinct `expired` state and all four
remaining proposals as put, and chose a **30 second** progress
timeout default over the proposed 60 — the measurement's ~20x
headroom supported the tighter value (still ~10x over the worst
observed complete transfer). Decisions are recorded in the master
plan's "Decisions from phase 0" section; the client fail-fast gap is
filed as client-python#363 and referenced from the master plan's
phase 6 row.
