# Agent operation deadlines phase 7: documentation and CI coverage

## Prompt

Plan the next phase of `PLAN-agent-operation-deadlines.md` with the
`next-phase` skill, after phase 6 merged as shakenfist#4005 and
client-python#380. Phases 1 to 5 built and enforced the timing budgets
server-side; phase 6 taught the client to propagate them and to fail
fast on a terminal state. This phase is the one that tells a human
what changed, proves the behaviour in functional CI, and pays off the
audit finding that this repository's own test suite awaits agent
operations with the very bug the plan exists to fix.

## Planning effort

High. The volume is modest but two of the four code changes turn on
correctness questions the master plan gets wrong, and one of them
would be a regression if the master plan's instruction were followed
literally (see decision 1). The functional coverage also has to be
designed against a dispatcher whose one-executor-per-instance rule is
the thing being proven, which is easy to write as a test that passes
for the wrong reason.

## Scope

**In scope.**

- The three event-renewed await loops in
  `shakenfist/deploy/shakenfist_ci/base.py` gain an absolute ceiling
  as well as a progress window, and their renewal is restricted to
  events which represent progress. This is #3770.
- The ten agent-operation poll loops in the two `test_agentops.py`
  files learn to fail fast on a terminal state, and stop sharing one
  clock across sequential operations.
- `base.AGENT_OPERATION_FAILURES` loses its `getattr()` compatibility
  shim now that client-python#380 has merged, and the two
  `test_get_missing_file` assertions narrow to the exception the new
  client actually promises.
- Functional coverage in `shakenfist_ci` for the four scenarios the
  master plan enumerates.
- A new `docs/operator_guide/agent_operations.md`, and an extension of
  the existing `v07-v08.md` release-note section to cover phase 5's
  retry and phase 6's client-visible changes.

**Out of scope.**

- `docs/developer_guide/state_machine.md`. It is a rendering of
  `state_targets`, so phases 4 and 5 updated it as they changed that
  dict rather than leaving the tree self-contradictory. The master
  plan's phase 7 row already says so.
- `docs/developer_guide/api_reference/instances.md` and the existing
  release-note section on deadlines. Both were written by phase 3 and
  corrected since; see survey finding F1. This phase adds what is
  missing rather than rewriting what is there.
- The push audit. That is phase 8, over the whole plan's accumulated
  diff.
- Any change to server-side enforcement. If the functional tests
  written here find an enforcement bug, it is filed and recorded in
  Future work, not fixed inline.

## What the survey found

The master plan's phase 7 row was written before phases 3 to 6
executed and is wrong in four places. All four corrections are applied
at source as part of the planning commit; a later step should not redo
them.

### F1 — the documentation is largely already written

The row says docs were "deferred from phase 3 so it is written once,
after enforcement exists". They were not. Phase 3's commit `cf094551b`
("Document agent operation timing parameters"), corrected by
`83a0d2d0e`, wrote both of the following:

- `docs/developer_guide/api_reference/instances.md:657-710` — both
  parameters, their units, the omitted-versus-zero distinction, the
  defaults table, and why `agent/execute` refuses a progress timeout.
- `docs/release_notes/v07-v08.md:731-782` — a full section, "Agent
  operations are bounded by a deadline, not a fixed backstop",
  covering the removal of the 900-second constant, both budgets, the
  `expired` state, the three enforcement points, and an explicit
  warning that the effective default is *tighter* than the behaviour
  it replaces.

What is genuinely missing:

- **Phase 5's retry and reaper are undocumented outside the plan
  files.** `AGENT_OPERATION_MAX_ATTEMPTS` appears in exactly one
  published page, `docs/developer_guide/state_machine.md:45`. There is
  no release-note text for the `EXECUTING -> QUEUED` edge, for attempt
  counting, or for the node-local executor reaper, and no operator
  guidance on what to do when an operation exhausts its attempts.
- **There is no operator-guide page for agent operations at all.**
  `docs/operator_guide/` has eighteen pages and none of them is about
  agent operations; the only operator-facing mention of the two
  defaults is a passing one in `docs/operator_guide/database.md:1141`,
  in the context of what the schema columns mean.
- **Phase 6's client-visible changes have no release note.** The new
  `--deadline` and `--progress-timeout` flags on `instance execute`,
  `upload` and `download`, and the behaviour change from
  `AgentCommandError` to `AgentOperationFailed` on a terminal state,
  are user-visible and unannounced.

### F2 — `AGENT_OPERATION_FAILURES` must not become a plain reference

The row instructs this phase to "narrow `base.AGENT_OPERATION_FAILURES`
to a plain `AgentOperationFailed` reference now that both client
generations no longer need tolerating". Half of that is right and half
of it is a regression.

The `getattr()` shim at
`shakenfist/deploy/shakenfist_ci/base.py:46` genuinely can go:
client-python#380 merged on 2026-09-03, so `AgentOperationFailed`
always exists now. But the tuple has two kinds of consumer and they
want different things.

The two catch sites, `base.py:469` and `base.py:498`, are
`_await_instance_ready`'s cloud-init retry loop and its debug-gathering
loop. They mean "this attempt did not give us a usable answer, try
again". On client-python's current `develop`, `await_agent_command()`
raises **three** unrelated exception types, none of which shares a base
class beyond `Exception`:

- `AgentOperationFailed` — the operation reached a terminal failure
  state (`apiclient.py:1301`, raised from `_await_agentop`).
- `AgentAwaitTimeout` — the operation never completed within the
  caller's own budget (`apiclient.py:1773`).
- `AgentCommandError` — the operation completed but the result is
  unusable: no results, unexpected stderr, no stdout blob
  (`apiclient.py:1791`, `:1797`, `:1805`, `:1821`).

Narrowing the tuple to `AgentOperationFailed` alone would stop the
retry loop catching the other two. A cloud-init health check whose
command writes to stderr would then escape the retry as an
`AgentCommandError`, and the whole `_await_instance_ready` would fail
on the first attempt instead of the third — a silent loss of retry
coverage across every test in the suite that boots an instance.

The assertion sites are the opposite case. `test_get_missing_file` in
both suites (`smoke_ci_tests/test_agentops.py:306`,
`guest_ci_tests/test_agentops.py:315`) asserts that fetching a
non-existent file raises *something*. A three-member tuple there
asserts almost nothing: it would pass if the operation timed out, or
if it succeeded and returned garbage. Those two want the narrow
reference.

Decision 1 resolves this.

### F3 — there is a third event-renewed loop, and the addresses drifted

The master plan names two loops and gives addresses from before phase
4 and 5 landed. Current addresses, and the one it misses:

| Loop | Master plan says | Actually at | Window |
|------|------------------|-------------|--------|
| `_await_agent_state` | `base.py:436` | `base.py:522`, loop at `:531`, renewal at `:545` | 500s |
| `_await_instance_create` | `base.py:471` | `base.py:557`, loop at `:561`, renewal at `:587` | 180s |
| `_await_objects_ready` | not mentioned | `base.py:706`, renewal at `:724`, check at `:736` | 300s |

`_await_objects_ready` is the same construction — `time.time() -
time_since_last_progress > 300`, with `time_since_last_progress`
reassigned from `last_event['timestamp']` for any event at all — and
it backs `_await_networks_ready`, `_await_artifacts_ready` and
`_await_blobs_ready`, so it is on the path of nearly every test in the
suite. It has to be fixed with the other two or the class of bug
survives.

The mechanism is confirmed rather than assumed. `node_inst_op`
(`shakenfist/operations/node_inst_op.py:169`) writes an
`EVENT_TYPE_USAGE` event against every instance on a timer, with
`suppress_event_logging=True`, so an instance which is going nowhere
still produces events forever. `get_instance_events(..., limit=1)`
returns the most recent event of any type, so that timer alone renews
the window indefinitely. The client already supports the fix:
`get_instance_events()` takes an `event_type` filter
(`apiclient.py:570`), gated on the `events-by-type` capability, and
the vocabulary is in `shakenfist/constants.py:93-113`.

`_await_agent_state`'s timeout message also says "no progress in 5
minutes" for a 500-second (8m20s) window. `_await_instance_create`'s
says three minutes for 180s and is correct.

### F4 — the fix for the merge-queue flake was never applied to smoke

This one is not in the master plan at all and is the most valuable
thing the survey turned up.

Commit `a0cc243ad` ("Give each agent operation its own timeout in the
put/get blob test", 2026-07-15) diagnosed an intermittent flake worth
~9% of merge-queue runs: `test_instance_put_and_get_blob` set one
`start_time` and then ran three sequential agent operations whose wait
loops all measured against it, so `get-file` — the last and heaviest —
was left only what the earlier two had not spent. The fix added
`_await_agentop_complete()`, which polls one operation against its own
fresh clock.

That commit touched `guest_ci_tests/test_agentops.py` and nothing
else. The identical test in `smoke_ci_tests/test_agentops.py:162` still
has the bug: `start_time` is set once at `:201`, and the three loops at
`:206` (30s), `:217` (60s) and `:235` (60s) all measure from it. The
smoke suite is the one that runs on every pull request.

The two files have identical test inventories — eight tests, same
names, same order — and are near-exact copies of each other. Any fix
applied to one and not the other is a fix that half-landed, which is
precisely what happened here. That is the argument for hoisting the
helper into `base.py` rather than copying it across (decision 2).

### F5 — no functional coverage of the timing budgets exists

Grepping `shakenfist/deploy/shakenfist_ci/` for `deadline`,
`progress_timeout` or `expired` finds only unrelated local variables,
the phase-4 hardening of `_await_command` (`base.py:772-828`), and the
`AGENT_OPERATION_FAILURES` comment. Nothing exercises an explicit
`deadline_seconds`, nothing asserts a `deadline` on `external_view()`,
and nothing observes an operation reaching `expired`. Phase 4's
`_await_command` work is the only CI-side change any phase of this
plan has made.

### F6 — everything else the row claims is accurate

The audit finding that the suite's own loops "spin on `!= 'complete'`,
one with no timeout at all" holds, with one correction: the one with no
timeout at all was `_await_command`, and phase 4 already fixed it
(`base.py:781`, now with `AGENT_OPERATION_TIMEOUT = 900` and
`AGENT_OPERATION_FAILED_STATES`). The ten that remain all have a
timeout and none has a terminal-state check.

## Decisions

1. **`AGENT_OPERATION_FAILURES` stays a tuple, and gains a member.**
   It becomes `(AgentCommandError, AgentOperationFailed,
   AgentAwaitTimeout)` — the honest set of "an agent operation did not
   give us a usable answer" — with the `getattr()` shim and its
   compatibility comment removed and replaced by a comment naming each
   member and the call in `apiclient.py` that raises it. The two
   `test_get_missing_file` assertions narrow to
   `apiclient.AgentOperationFailed` on its own.

   This is the decision a reviewer is most likely to argue with,
   because it declines an explicit instruction in the master plan. The
   reasoning is F2: the instruction conflates a *catch* site, which
   wants breadth, with an *assert* site, which wants precision, and
   following it literally silently disables the cloud-init retry for
   the most common failure shape. `AgentAwaitTimeout` is added rather
   than merely retained because it is new since the tuple was written
   and belongs to the same category as the other two; the retry loop
   should retry a timed-out health check.

2. **The poll helper is hoisted to `base.BaseTestCase`, not copied.**
   `_await_agentop_complete()` moves from
   `guest_ci_tests/test_agentops.py:26` to `base.py`, gains a
   terminal-state check that routes through the existing
   `_raise_agent_operation_failure()` (`base.py:805`), and both
   `test_agentops.py` files call it. F4 is what a copied fix looks like
   a month later.

   The helper keeps its per-operation clock and its explanatory
   comment; the comment is the record of why the shared clock was
   wrong, and deleting it while fixing the same bug in the other file
   would lose that.

3. **The three event-renewed loops keep the progress window and gain a
   ceiling.** Both numbers are named constants on `BaseTestCase`, so
   the ceiling is one place to change and the timeout message can
   quote the real number. Renewal is restricted by passing
   `event_type` to the events call rather than by filtering the
   returned list, so the narrowing happens in SQL.

   Which types count as progress is the implementing step's call
   against `shakenfist/constants.py:93-113`, but the plan's position
   is `status` and `mutate` renew, and `usage`, `resources`, `health`
   and `prune` do not — the periodic channels are exactly the ones
   that renew a dead wait forever. A test which needs an event type
   outside that set to make progress is a test whose await is looking
   at the wrong signal.

   The `events-by-type` capability gate means an old server would make
   the filtered call raise `IncapableException`. The suite always runs
   against a cluster built from this repository, so this is not a
   compatibility risk; the step should confirm the capability is
   declared rather than add a fallback path for a case that cannot
   arise.

4. **The functional tests go in the smoke suite, in a new file.** The
   four scenarios are server-behaviour tests, not guest-behaviour
   tests: they need a working in-guest agent, which the smoke suite
   already has (`smoke_ci_tests/test_agentops.py` executes commands
   today), and they need to run on every pull request to be worth
   having. A new `smoke_ci_tests/test_agentop_deadlines.py` keeps them
   out of the file that is duplicated into the guest suite, so this
   phase does not create a fifth F4.

5. **The expiry tests assert the state, not the timing.** Each of the
   four scenarios asserts on `state == 'expired'`, on the presence and
   rough magnitude of `deadline`, or on a follow-up operation
   *reaching* `executing` — never on how many seconds something took.
   CI hardware is contended and a test that asserts an operation
   expired "within 10 seconds" is a flake being written on purpose.
   Waiting is bounded by the new `base.py` helpers, which fail with a
   diagnosable dump rather than a bare timeout.

6. **The operator guide gets one new page, not additions to five.**
   `docs/operator_guide/agent_operations.md`, registered in
   `mkdocs.yml` between "Scheduler" and "Threads", covering: what an
   agent operation is and the one-executor-per-instance rule; the two
   budgets and their three config defaults; the `expired` state and
   how to tell it from `error`; retry, `AGENT_OPERATION_MAX_ATTEMPTS`
   and the node-local reaper; and what to tune when long agent
   commands are being killed. The existing passing mention in
   `docs/operator_guide/database.md:1141` gains a link rather than
   being duplicated.

7. **The release note is extended, not restructured.** The existing
   section at `docs/release_notes/v07-v08.md:731` stands as written.
   Two subsections are appended to it — one on retry and the reaper,
   one on the client — because they are the same story from a reader's
   point of view and a second top-level section about agent operation
   timing would invite the two to drift.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | **Hoist and harden the agent operation poll helper.** Move `_await_agentop_complete(self, instance_uuid, aop, timeout)` from `shakenfist/deploy/shakenfist_ci/guest_ci_tests/test_agentops.py:26` onto `base.BaseTestCase` in `shakenfist/deploy/shakenfist_ci/base.py`, next to `_await_command` at `:781`. Keep its existing docstring-comment verbatim — it records why a shared clock was wrong. Add a terminal-state check at the top of each iteration, using the existing `AGENT_OPERATION_FAILED_STATES` at `:779` and calling the existing `_raise_agent_operation_failure()` at `:805` (its `command` argument is used only for the message; pass a short description of the operation). Then route every remaining poll loop in both test files through it: `smoke_ci_tests/test_agentops.py` lines 51, 95, 146, 206, 217 and 235, and `guest_ci_tests/test_agentops.py` lines 36 (the old helper body), 67, 111 and 162. Note that smoke's `test_instance_put_and_get_blob` (`:162`) sets one `start_time` at `:201` and shares it across the three loops at 206/217/235 — this is the bug commit `a0cc243ad` fixed in the guest copy only, so give those three the same independent budgets the guest copy uses (30/60/120, with the get-file operation getting 120). The two `while time.time() - start_time < 120` loops (smoke `:146`, guest `:162`) are the same pattern written inside out and also become helper calls. Do not change any assertion. Commit subject: "Poll agent operations against one clock each." |
| 7b | low | sonnet | none | **Retire the client compatibility shim.** In `shakenfist/deploy/shakenfist_ci/base.py`, replace the `AGENT_OPERATION_FAILURES` definition at `:44-46` and the comment block above it at `:28-43`. The tuple becomes `(apiclient.AgentCommandError, apiclient.AgentOperationFailed, apiclient.AgentAwaitTimeout)` with no `getattr()`. Write a new comment which says these are the three unrelated exceptions `await_agent_command()` and `await_agent_fetch()` can raise — a terminal failure state, a caller-budget timeout, and an unusable result — that they share no base class, and that this tuple is for *catching* ("the attempt gave us no usable answer, retry"), which is why it is not narrowed to the one the two catch sites at `:469` and `:498` most often see. Separately, narrow the two assertion sites: `smoke_ci_tests/test_agentops.py:306` and `guest_ci_tests/test_agentops.py:315` become `apiclient.AgentOperationFailed` (import `apiclient` the way those files reach `base` today, or reference it via `base`). Do not otherwise touch those tests. Commit subject: "Name the agent failures the client can raise." |
| 7c | high | opus | none | **Give the event-renewed awaits an absolute ceiling (#3770).** Three loops in `shakenfist/deploy/shakenfist_ci/base.py` renew their window from any event at all: `_await_agent_state` (`:522`, loop `:531`, renewal `:545`, 500s), `_await_instance_create` (`:557`, loop `:561`, renewal `:587`, 180s), and `_await_objects_ready` (`:706`, renewal `:724`, check `:736`, 300s). The renewal is unconditional because `get_instance_events(..., limit=1)` returns the most recent event of any type, and `node_inst_op` (`shakenfist/operations/node_inst_op.py:169`) writes a timer-driven `EVENT_TYPE_USAGE` event against every instance forever, so a stalled instance renews its own deadline indefinitely. Give each loop (a) a named absolute ceiling constant on `BaseTestCase` alongside the progress window, checked from a `start_time` taken before the loop; (b) renewal restricted to progress-representing events by passing `event_type` to the events call rather than filtering the returned list — `get_instance_events`/`get_network_events`/`get_artifact_events`/`get_blob_events` all take `event_type` (see `apiclient.py:570`), and the vocabulary is `shakenfist/constants.py:93-113`. Decision 3 in this plan says `status` and `mutate` renew and the periodic channels (`usage`, `resources`, `health`, `prune`) do not; confirm that against what actually gets written during instance create and object readiness before committing to it, and say in the commit message if you disagree. Note the client's `_get_events()` takes one `event_type`, not a list — check whether the server's `object_events_response()` accepts more than one before assuming two calls are needed. Ceilings should be generous relative to the existing windows (the point is to convert a silent budget fire into a diagnosable failure, not to tighten CI); the phase-4 precedent is `AGENT_OPERATION_TIMEOUT = 900` at `:772`. Correct `_await_agent_state`'s message, which says "no progress in 5 minutes" for a 500-second window, and make every message quote its constant rather than a hardcoded prose duration. There is a stray double space in `_await_objects_ready`'s message at `:744` (a trailing double space after "The last"); fix it while there. Commit subject: "Bound the CI awaits, not just their progress." |
| 7d | high | opus | none | **Functional coverage for the timing budgets.** New file `shakenfist/deploy/shakenfist_ci/smoke_ci_tests/test_agentop_deadlines.py`, modelled on `smoke_ci_tests/test_agentops.py`'s class shape (`base.BaseNamespacedTestCase`, `namespace_prefix`, a network in `setUp`, `base.CLUSTER_CI_IMAGE`). Four tests, matching the four scenarios the master plan enumerates: (1) an explicit short `deadline_seconds` expiring an operation while it is still *queued* — enqueue a long `agent/execute` first so the instance's single executor is busy, then a second operation with a small deadline, and assert it reaches `expired` without ever executing; (2) the default deadline appears in `external_view()` — create an operation with no `deadline_seconds` and assert `deadline` is present and is roughly `now + AGENT_OPERATION_DEFAULT_DEADLINE` (600); (3) an `agent/execute` of a command that produces no output for well over the 30-second default progress timeout still completes, proving the progress timeout does not apply to commands that cannot report progress; (4) after an operation is expired, a follow-up operation on the same instance reaches `executing` promptly, proving the executor slot is freed. Assert on states and on the presence and rough magnitude of `deadline`, never on elapsed seconds (decision 5) — CI hardware is contended. Use the `base` helpers for all waiting, including `_await_agentop_complete` as hoisted by step 7a, so a failure prints the operation and the instance's recent events. The client parameters are `deadline_seconds` and `progress_timeout_seconds` on `instance_execute`/`instance_put_blob`/`instance_get` (`client-python:shakenfist_client/apiclient.py:1409-1455`); they are only sent when the server advertises the `agentoperation-deadlines` capability, which this repository's `shakenfist/external_api/app.py:364` does. Read `shakenfist/operations/agentoperation.py` and `shakenfist/daemons/sidechannel/main.py` for what actually sets `expired` before writing the assertions. If a test cannot be made non-flaky, say so and leave it out rather than landing a known flake — this plan's whole subject is CI reliability. Commit subject: "Test agent operation deadlines in CI." |
| 7e | medium | sonnet | none | **Documentation.** Two files. First, new `docs/operator_guide/agent_operations.md` per decision 6, registered in `mkdocs.yml` in the operator guide nav (the list runs alphabetically at lines 494-509; insert "Agent Operations" after "Installation" to match the existing ordering, which puts Installation first and the rest alphabetically). Content: what an agent operation is and the one-executor-per-instance dispatch rule (`shakenfist/daemons/sidechannel/main.py`); the two budgets, `AGENT_OPERATION_DEFAULT_DEADLINE` (600) and `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` (30), from `shakenfist/config.py:240` and `:259`; the `expired` state and how an operator tells it from `error` (the state row's message says which budget ran out, and `expire()` audits it against the instance); retry, `AGENT_OPERATION_MAX_ATTEMPTS` (`config.py:303`) and the per-command `retryable` flag; the node-local executor reaper from phase 5; and what to tune when long agent commands are being killed. Link to `docs/developer_guide/api_reference/instances.md` for the request parameters rather than restating them, and add a link from the passing mention at `docs/operator_guide/database.md:1141`. Second, append two subsections to the existing release-note section at `docs/release_notes/v07-v08.md:731` ("Agent operations are bounded by a deadline, not a fixed backstop") — do not restructure what is there. One subsection on phase 5: an operation which fails in `executing` for a retryable reason is now returned to the head of its instance's queue rather than abandoned, bounded by `AGENT_OPERATION_MAX_ATTEMPTS` (3), and a node-local reaper recovers operations whose executor died or never started. One on the client: `sf-client instance execute`, `upload` and `download` gained `--deadline`, and `upload` and `download` also `--progress-timeout`; and a terminal operation state now raises `AgentOperationFailed` immediately where the client previously polled to its full timeout and raised `AgentCommandError`, which is a behaviour change for anything catching that exception. Read the surrounding release-note prose and match its register — it explains consequences, not APIs. Commit subject: "Document agent operation timing for operators." |
| 7f | low | sonnet | none | **Close the phase out.** Set phase 7 to `Complete` in the Execution table of `docs/plans/PLAN-agent-operation-deadlines.md` and link this file from the row (the row currently has an empty Plan cell). Update this plan's Definition of done checkboxes to reflect what actually happened, and record anything deferred in Future work. In `docs/plans/index.md`, move the plan's row from `7 of 9` to `8 of 9`, leaving the status at `In progress` — phase 8, the push audit, remains. Do not touch `docs/plans/order.yml`. Commit subject: "Close out agent operation deadlines phase 7." |

## Corrections applied at source

Made in the planning commit, so a later step does not repeat them:

- `docs/plans/PLAN-agent-operation-deadlines.md`, phase 7 row: the
  claim that documentation was deferred from phase 3 is corrected to
  name what phase 3 actually wrote and what is left (F1); the
  instruction to narrow `AGENT_OPERATION_FAILURES` to a plain
  reference is corrected to say the shim goes and the tuple stays,
  with a pointer to decision 1 (F2); `_await_objects_ready` is added
  to the list of event-renewed loops and the two stale addresses are
  refreshed (F3); the smoke-suite half of `a0cc243ad` is named as work
  this phase picks up (F4).
- `docs/plans/PLAN-agent-operation-deadlines.md`, the "#3770" section:
  the two `base.py` line numbers are refreshed and
  `_await_objects_ready` added; the note that `_await_command` was
  pulled forward into phase 4 already stands and is left alone.
- `docs/plans/index.md`: the plan's row keeps `7 of 9` and `In
  progress` until step 7f, but its Intent cell is left as it is — it
  describes the plan, not this phase, and is still accurate.

## Risks and mitigations

- **Step 7c tightens CI and turns a slow-but-passing test into a
  failure.** The ceilings are new upper bounds on waits that
  previously had none. Mitigation: the ceilings are set generously
  relative to the existing progress windows, and the management
  session reads the diff for any ceiling that is not comfortably above
  the window it guards. The failure mode this converts *from* is a
  killed 60-minute job with no named test, so a too-tight ceiling is
  strictly easier to diagnose than the status quo.
- **Step 7c's event-type filter starves a legitimate wait.** If an
  object genuinely makes progress only through an event type the
  filter excludes, that wait now fails at its progress window where it
  previously renewed. Mitigation: the brief asks the implementer to
  confirm the type set against what is actually written during
  instance create and object readiness, rather than taking decision
  3's list on faith, and to say so if they disagree. The ceiling is
  the safety net either way.
- **Step 7d writes a flaky test.** Expiry tests are timing tests
  wearing a disguise. Mitigation: decision 5 forbids elapsed-time
  assertions, and the brief explicitly authorises leaving a scenario
  out rather than landing a known flake. The management session runs
  the new file's tests against a real cluster before the phase closes,
  and a scenario that cannot be made reliable is recorded in Future
  work with its reason.
- **Steps 7a and 7b both edit the two `test_agentops.py` files.** Run
  them in order and re-read the second diff; they touch different
  lines but the same files, and 7a renumbers everything below its
  edits.
- **The functional tests cannot run in the management session.** They
  need a cluster. Mitigation: the phase is not marked complete on unit
  tests alone — the Definition of done requires a CI run on the
  branch, and a failure there is a phase-7 defect, not a flake to
  re-queue.

## Definition of done

Every item is checkable by running the command next to it.

- [ ] No `getattr(apiclient` remains in the CI suite:
      `! grep -rn "getattr(apiclient" shakenfist/deploy/shakenfist_ci/`
- [ ] `AGENT_OPERATION_FAILURES` names three exceptions and no shim:
      `grep -A3 "^AGENT_OPERATION_FAILURES = (" shakenfist/deploy/shakenfist_ci/base.py`
      shows `AgentCommandError`, `AgentOperationFailed` and
      `AgentAwaitTimeout`.
- [ ] Both `test_get_missing_file` assertions name the narrow
      exception:
      `grep -n "AgentOperationFailed" shakenfist/deploy/shakenfist_ci/*/test_agentops.py`
      returns two lines, and
      `grep -c "AGENT_OPERATION_FAILURES" shakenfist/deploy/shakenfist_ci/*_ci_tests/test_agentops.py`
      returns zero for both.
- [ ] No unguarded agent-operation poll loop remains in the suite:
      `grep -rn "while aop\['state'\] != 'complete'" shakenfist/deploy/shakenfist_ci/`
      returns nothing.
- [ ] The helper is shared, not duplicated:
      `grep -rn "def _await_agentop_complete" shakenfist/deploy/shakenfist_ci/`
      returns exactly one line, in `base.py`.
- [ ] The smoke put/get test no longer shares a clock: in
      `smoke_ci_tests/test_agentops.py`, `test_instance_put_and_get_blob`
      contains no `start_time` reused across two waits.
- [ ] All three event-renewed loops have a ceiling: each of
      `_await_agent_state`, `_await_instance_create` and
      `_await_objects_ready` in `base.py` references a ceiling
      constant distinct from its progress window.
- [ ] No timeout message states a duration that disagrees with its
      constant: read the three messages and check each against the
      constant it quotes. `_await_agent_state`'s "5 minutes" for 500
      seconds is the known instance.
- [ ] Renewal is filtered server-side:
      `grep -n "event_type=" shakenfist/deploy/shakenfist_ci/base.py`
      shows the filter on every renewal call, and no renewal call
      passes only `limit=1`.
- [ ] `smoke_ci_tests/test_agentop_deadlines.py` exists and contains
      four tests, or fewer with each omission recorded in Future work
      and its reason given.
- [ ] `docs/operator_guide/agent_operations.md` exists, is listed in
      `mkdocs.yml`, and names all three of
      `AGENT_OPERATION_DEFAULT_DEADLINE`,
      `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` and
      `AGENT_OPERATION_MAX_ATTEMPTS`:
      `grep -c "AGENT_OPERATION_" docs/operator_guide/agent_operations.md`
      is at least 3.
- [ ] The release note covers retry and the client:
      `grep -n "AGENT_OPERATION_MAX_ATTEMPTS\|--progress-timeout\|AgentOperationFailed" docs/release_notes/v07-v08.md`
      returns at least one line for each.
- [ ] No fact about the timing budgets is stated differently on two
      pages: the defaults quoted in
      `docs/operator_guide/agent_operations.md`,
      `docs/developer_guide/api_reference/instances.md:680` and
      `docs/release_notes/v07-v08.md:744` agree.
- [ ] `pre-commit run --all-files` exits zero.
- [ ] CI has run green on the branch, including the smoke suite, and
      the new deadline tests appear in its results rather than being
      skipped.

## Future work

- The master plan's phase 7 row observes that `_await_instance_ready`
  retries a cloud-init health check three times with a 30-second sleep
  between attempts. If step 7c's event-type filter turns out to change
  how often that path is entered, the retry count deserves a second
  look — but not in this phase.
- `docs/operator_guide/` has two pages that are not in `mkdocs.yml`'s
  nav (`credential_rotation.md`, `vdi_console_tokens.md`). Noticed
  while adding the new page; unrelated to this plan and not fixed
  here.
- The two `test_agentops.py` files are near-exact copies with
  identical test inventories. F4 shows what that costs. Collapsing
  them into one parameterised module is a larger change than this
  phase should carry, and belongs to whoever next has a reason to
  touch both.

## Back brief

Before executing any step of this plan, back brief the operator on
your understanding of it and how the work you intend to do aligns
with it.

There is one gate. **Decision 1 declines an explicit instruction in
the master plan**, on the grounds that following it literally would
disable the cloud-init retry loop for the most common failure shape.
Step 7b implements that decision. Confirm decision 1 before starting
7b. Steps 7a, 7c, 7d and 7e do not depend on it and can proceed
meanwhile.
