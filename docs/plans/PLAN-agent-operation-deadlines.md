# Agent operation deadlines and progress detection

## Prompt

Before responding to questions or discussion points in this document,
explore the shakenfist codebase thoroughly. Read the sidechannel daemon
(`shakenfist/daemons/sidechannel/main.py`, especially
`SideChannelExecutorJob` and `_execute_inner`), the agent operation
object (`shakenfist/operations/agentoperation.py` and
`shakenfist/operations/baseoperation.py`), the instance-side queue
(`Instance.agent_operation_next` and `agent_operation_enqueue` in
`shakenfist/instance.py`), the three REST endpoints that create agent
operations (`InstanceAgentPutBlobEndpoint`, `InstanceAgentGetEndpoint`,
`InstanceAgentExecuteEndpoint` in
`shakenfist/external_api/instance.py`), and the MariaDB accessors for
agent operations in `shakenfist/mariadb.py`. The client-side await
loop lives in the sibling `client-python` repository. Ground your
answers in what the code actually does today rather than guessing.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture overview and
`CLAUDE.md` for build commands, project conventions, the API parameter
declaration rules, the attribute field-mask rule, and the state
machine documentation pointer.

When we get to detailed planning, I prefer a separate plan file per
detailed phase, named for the master plan with `-phase-NN-descriptive`
appended before the `.md` extension.

I prefer one commit per logical change, and at minimum one commit per
phase. Do not batch unrelated changes into a single commit.

## Situation

Issue #3516: `sf-sidechannel` can leave an agent operation wedged in
`executing` until the client gives up. The trigger (issue #2240, a
`get-file` that never delivers) is agent-side and still open, but the
hypervisor-side handling turns a transient hiccup into a test-fatal
hang. This is currently one of the two most frequent merge-queue CI
flakes (`test_agentops.TestAgentOperations.test_instance_put_and_get_blob`,
three occurrences in the week to 2026-08-14).

This is not just a CI problem. The sidechannel dispatch loop runs at
most one executor per instance (`_dispatch_loop` skips any instance
with a live executor), so a wedged operation monopolises the
instance's executor slot: the user loses the ability to run *any*
agent operation against that instance until the 900-second backstop
fires. A single stuck `get-file` takes out the whole agent surface of
the instance for a quarter of an hour.

PR #3506 already landed two mitigations: a try/finally in
`SideChannelExecutorJob.execute()` that marks a still-`EXECUTING`
operation `ERROR` on abnormal executor exit, and a whole-operation
execution deadline (`AGENT_OPERATION_EXECUTION_TIMEOUT`, hardcoded to
900 seconds in `shakenfist/daemons/sidechannel/main.py`). CI
occurrences post-dating those fixes show why they are insufficient:

1. **The 900s backstop is longer than every client await.** The guest
   CI awaits give up after 120-377 seconds, so from the client's
   perspective the operation is still wedged in `executing` when it
   times out. The server then grinds on for the remainder of the 900
   seconds doing work nobody wants, holding the per-instance executor
   slot.
2. **A failed operation is never retried.**
   `Instance.agent_operation_next()` pops the queue entry as soon as
   the operation leaves `QUEUED`, so an `EXECUTING` operation is never
   re-dispatched. A transient agent hiccup is fatal to the operation.
3. **The whole-operation deadline cannot distinguish a stalled
   transfer from a genuinely large one.** A single wall-clock number
   has to be big enough for the biggest legitimate transfer, which
   makes it useless for detecting stalls quickly.
4. **There is no server-side notion of the client's intent.** The
   client knows exactly how long it is willing to wait (its await
   timeout) but has no way to communicate it. Periodic callers (an
   operation every N seconds) would rather skip a cycle than queue
   deeper and deeper behind a backlog.

## Decisions already made

These were settled in discussion before this plan was written and are
not open questions:

1. **Two independent timeout knobs per operation.**
   - A **wall-clock deadline**, expressed by the client as "seconds
     since this REST request was received". Queue time (and preflight
     time, for put-blob) counts against it. The API server converts it
     to an absolute expiry timestamp at request receipt and stores
     that on the operation.
   - A **progress timeout**: "no forward progress for N seconds is
     fatal". Only meaningful for commands that can observe progress.
2. **The server default deadline is 600 seconds** (a new config
   option, replacing the hardcoded 900s
   `AGENT_OPERATION_EXECUTION_TIMEOUT` constant). A client may pass an
   explicit sentinel (0) meaning "no wall-clock deadline".
3. **The no-deadline + progress-timeout combination is a first-class
   use case.** Streaming a 1TB file out of an instance should set
   deadline=none and rely on the progress timeout to detect fatal
   stalls (and permit retry) without ever timing out just for being
   big.
4. **Progress observation is a declared capability of each agent
   command.** The command dispatch in `SideChannelExecutorJob` (today
   an if/elif chain at the bottom of `_execute_inner`) is restructured
   so that each command declares whether it reports progress, making
   progress support an obvious thing to implement for future
   commands. `get-file` and `put-blob` report progress via file
   chunks; `execute` cannot (a shell command that produces no output
   until completion is indistinguishable from a stalled one) and is
   covered by the wall-clock deadline only.
5. **Aggressive defaults live client-side, not server-side.** The
   client library populates the deadline from its own await timeout by
   default; the server default stays conservative so existing callers
   with slow-but-legitimate operations do not break.
6. **Retry is in scope.** An operation that fails in `EXECUTING`
   before its deadline (executor death, progress stall) is
   re-dispatched rather than failed, within an attempt bound.

## Design sketch

### Object model and schema

`AgentOperation` gains two new static values, stored as nullable
columns on the `agent_operations` table:

- `deadline` — absolute unix timestamp (float) after which the
  operation must not be dispatched and must not continue executing.
  Computed by the API server at request receipt as
  `time.time() + deadline_seconds`. NULL means no client intent was
  recorded, so the server default applies; an explicit `0.0` means
  the client asked for no wall-clock deadline at all (it passed the
  0 sentinel).
- `progress_timeout` — float seconds. NULL means the same thing it
  means for `deadline`: no client intent recorded, so the server
  default for progress-capable commands applies. An explicit `0.0`
  disables the progress timeout.

The NULL semantics were corrected during phase 2 planning. This
section previously said a NULL `deadline` meant "no deadline", which
made the same absence mean opposite things in two adjacent columns
and — because NULL is what every legacy row and every row written by
a not-yet-upgraded API node contains — would have left exactly those
operations unbounded at the moment phase 4 deletes the 900-second
constant. `0.0` is an unambiguous sentinel because a real deadline is
an absolute timestamp of order 1.7e9.

There is **no object version bump**: `AgentOperation.current_version`
stays at 3. `baseobject.upgrade()` cannot read a row whose version is
higher than the reader's — it looks up `_upgrade_step_4_to_5` with a
`getattr` that has no default and raises `AttributeError` — and an
agent operation is created on an API node and read on the
hypervisor's `sf-sidechannel`, so a bump would break agent operations
on every not-yet-rolled node for the length of a rolling upgrade.
There is also nothing to migrate, since NULL is a meaningful value
rather than a gap. "Does this database have the columns?" is answered
by the *table* schema version, which `sf-database` refuses to start
against if it is behind. See
`PLAN-agent-operation-deadlines-phase-02-schema.md` decision 2.

Static values are immutable, which fits: the client's intent is fixed
at submission time.

`AgentOperationAttributesData` gains two new mutable fields:

- `last_progress` — float unix timestamp of the most recent observed
  progress, persisted with a throttle (at most one write every ~10
  seconds) so a fast chunk stream does not hammer the attributes
  table. Needed so that a reaper on the hypervisor node can reason
  about a no-deadline operation whose executor died without running
  its finally block.
- `attempts` — integer dispatch counter for the retry bound.

Per the field-mask rule in CLAUDE.md,
`update_agent_operation_attributes` must take a `fields` mask before
these fields are added — today it only carries `results`, so
`add_result()`'s unmasked write would clobber concurrent
`last_progress`/`attempts` updates (exactly the cross-attribute lost
update the rule exists to prevent). Schema migration runs via
`sf-ctl ensure-mariadb-schema` as usual; migrations must be
idempotent.

All four values appear in `external_view()` so clients (and CI
diagnostics) can see them. The view already reads the attributes row
once for `results`, so `last_progress` and `attempts` come along for
no extra database round trip.

### API surface

The three creating endpoints (`.../agent/execute`, `.../agent/get`,
`.../agent/put`) gain optional body parameters:

- `deadline_seconds` — number, minimum 0, on all three. 0 means "no
  deadline". Omitted means the server default (600).
- `progress_timeout_seconds` — number, minimum 0, on `.../agent/get`
  and `.../agent/put` only. 0 means "disabled". Omitted means the
  server default.

`.../agent/execute` deliberately does **not** publish
`progress_timeout_seconds`, and stores an explicit `0.0`. No command
that endpoint builds can report progress — `ExecuteCommand.reports_progress`
is `False`, against `True` for `PutBlobCommand` and `GetFileCommand` —
so the parameter would accept input the enforcement phase can never
consult. Corrected during phase 3 planning; this section previously
gave all three endpoints both parameters. See
`PLAN-agent-operation-deadlines-phase-03-api.md` decision 4.

The API server writes a value on every create: an omitted
`deadline_seconds` is stored as `time.time() + the default`, not as
NULL. NULL remains the signature of a row written by an API node
which predates this work, which is the only case the dispatch-time
fallback below exists for.

All of them are declared per the parameter declaration rules (body
location, `number` type token, constraints dict with `minimum`), the
published `minimum` is backed by a handler guard answering 400 rather
than by coercion, and each gets a `STRUCTURED_PARAMETERS` entry
describing what the handler actually accepts. New config options:

- `AGENT_OPERATION_DEFAULT_DEADLINE` = 600
- `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` = 30 (see decisions)

### Enforcement points

The deadline and progress timeout are enforced at four places, in
increasing order of reach. Phase 4 owns the first three; the reaper is
phase 5's, because a reaper for a dead executor is only useful once
the queue entry survives execution, which is phase 5's terminal-only
pop. This section originally listed three points and did not mention
preflight at all; both were corrected during phase 4 planning.

1. **At dequeue** (phase 4) — `Instance.agent_operation_next()` checks the
   deadline before returning an operation. An expired queued operation
   is transitioned to `expired`, popped, and the next
   entry considered. This is the "skip a cycle" behaviour for periodic
   callers: work the caller has already abandoned never occupies the
   executor.
2. **During preflight** (phase 4) — `NodeAgentopOp._preflight()`
   checks the deadline on entry and again after each
   `Blob.ensure_local()`, which is the longest pre-queue delay in the
   system and precisely the wait a receipt-anchored deadline exists to
   count. See decision 4 below, which this section previously did not
   reflect.
3. **In the executor** (phase 4) — `_execute_inner()` replaces the fixed
   `AGENT_OPERATION_EXECUTION_TIMEOUT` check with two checks: the
   operation's absolute deadline (if any), and
   `time.time() - last_progress > progress_timeout` while a
   progress-capable command is in flight. Note that `last_data` in the
   executor loop is refreshed by *any* socket traffic including ping
   replies, so it is not a progress signal; progress must be observed
   in the command reply handlers (`file_chunk`, `file_chunk_reply`,
   `stat_result`, `execute_reply`) via an explicit
   `observe_progress()` hook.
4. **By a node-local reaper** (phase 5) — the sidechannel daemon's main loop
   (which already iterates this node's instances and knows which
   executors are live) sweeps for operations in `EXECUTING` with no
   live executor thread. If the operation's deadline has passed, or
   its persisted `last_progress` is older than its progress timeout
   plus the persistence throttle slack, the reaper resolves it
   (retry or expire, below). This covers the case PR #3506's finally
   block cannot: the sidechannel process dying outright.

   Phase 4's review added a second case the reaper must cover, and it
   is a live executor rather than a dead one. `SideChannelJob.execute()`
   blocks in `while not os.path.exists(console_path): time.sleep(1)`
   before `_execute_inner()` is entered, so neither budget is
   evaluated during it. An instance whose `console.log` never appears
   holds its executor slot indefinitely, and enforcement point 1
   cannot help because the dispatcher skips instances with a live
   executor. This is not a regression -- the 900 second timer phase 4
   deleted also started at `connected_at` -- but it is the one wedge
   shape none of phase 4's three enforcement points can observe, and
   only something looking at executors from outside can.

Because only the sidechannel daemon on the instance's placement node
dispatches executors, the reaper has no cross-node race: reaping and
dispatching are serialised in one process.

All three read a NULL `deadline` as "apply
`AGENT_OPERATION_DEFAULT_DEADLINE`" rather than as "no deadline" (see
the object model section). Since such a row carries no receipt
timestamp to anchor the default against — it was written by an API
node that predates this work — the fallback anchor is dispatch time,
which is a node-local number the executor and the reaper both already
have. This path exists only for legacy rows and for the window of a
rolling upgrade; a deadline-aware API server always writes an
absolute timestamp or the explicit `0.0` sentinel.

Clock skew note: the absolute deadline is computed on the API node and
enforced on the hypervisor node. Shaken Fist already assumes
NTP-synchronised cluster nodes; a skew of a few seconds is immaterial
against a 600-second default, and the progress timeout is computed
entirely node-locally.

### Retry

A retried operation must not lose its place in line: appending it
back onto the tail of the instance queue would reorder it behind
operations submitted after it, violating the linear model (op1 "put a
file" retrying behind op2 "execute the script that reads it" runs
them in the wrong order). Equally, having the executor silently
re-execute in place would bypass dispatch (losing the 5-second
attempt throttle as natural backoff, and the per-dispatch audit
events) and would still need a second, queue-based mechanism for the
case where the executor died with the daemon.

Instead, retry exploits the pop being lazy. Today the queue entry is
still at the head *while* the operation executes;
`agent_operation_next()` only pops it on a later call, once the state
has provably left `QUEUED`. So: the pop rule changes to "pop only
operations in a terminal state" (an `EXECUTING` head with a live
executor returns nothing, as dispatch skips instances with executors
anyway), and retry is nothing more than the state transition
`EXECUTING -> QUEUED` (a new edge in `state_targets`) applied to an
operation whose entry never left the head. The dispatcher then
re-dispatches it exactly like a first attempt: same position, fresh
connection, throttled, evented. There is no re-enqueue and therefore
no reordering; the retrying operation deliberately blocks later
operations until it completes, errors, or expires — which is the
linear contract working as intended, now bounded by the deadline and
attempt cap rather than open-ended.

The transition is applied either by the executor's exit path
(deadline, progress stall, exception, with attempts and deadline
checked) or by the reaper for a dead executor; both run in the one
sidechannel process per instance, serialised with dispatch, so the
transition cannot race a concurrent pop. The invariant becomes "the
queue entry lives at the head until the operation reaches a terminal
state", a strict strengthening of today's model. The
single-executor-per-instance guarantee is unchanged. Otherwise —
deadline passed or attempts exhausted — the operation goes to its
terminal failure state and the lazy pop removes it as today. The
retry policy is therefore "retry until the deadline expires or
attempts are exhausted, whichever is first"; the attempt cap exists
so no-deadline operations cannot retry forever.

Retryability is a property of the whole command list, not of the
command that happened to be in flight. Because a retry restarts at
index 0, an operation containing any non-retryable command must not
be retried at all -- an `[execute, get-file]` operation stalling in
the `get-file` would otherwise re-run the `execute`, which is exactly
what decision 6 forbids. No endpoint builds such a list today, so the
two readings agree in practice; only the whole-list one stays correct
when one does. Corrected during phase 5 planning, where this section
implied the per-command reading.

Partial results on retry need care: a retried `get-file` restarts the
transfer from offset 0 and must not append to or duplicate the
previous attempt's blob. The first attempt's incomplete blob (if any)
must be cleaned up when the retry is scheduled -- phase 4's
`_abandon_get_file_transfer()` already does this.

There is a second case this section originally missed, found during
phase 5 planning. A `get-file` which *completed* registers a blob and
records its uuid as the result for that command index; if a later
command in the same operation then stalls, the retry mints a fresh
blob uuid and overwrites that result, leaving the first blob recorded
nowhere. The fix is to clear the abandoned attempt's results when the
retry is scheduled, so no result from a superseded attempt is ever
served to a caller. The orphaned blob itself needs no special
handling: it carries no `object_references` row, so the cluster
daemon's existing unreferenced-blob sweep collects it.

### Failure semantics between operations

Linear execution raises the question: when an operation fails
terminally (error or expiry), should the operations queued behind it
be cancelled, since they may depend on the failed operation having
prepared something for them?

No — and deliberately so. The instance queue is shared by every
caller authorised for the instance (two API clients' operations, and
system-generated operations, interleave in the one queue), so a
queue-wide cascade would cancel unrelated callers' work because
someone else's operation failed. The promise of the linear model is
**ordering, not dependency**: operations execute one at a time in
submission order, and each has an independent outcome — shell `;`,
not `&&`. This is today's contract (a queue simply continues past an
`ERROR` operation) and this plan preserves it; it must be documented
explicitly in the user guide as part of phase 7.

Dependency semantics do exist, but scoped where they can be correct:

- **Within one operation**, the commands list is already a fail-fast
  transaction: on `ERROR` the executor clears the remaining commands
  (`self.commands = []`), so a put-blob whose transfer fails never
  runs its chmod. This is the transactional primitive — it is just
  not currently composable from the public API, which builds only
  fixed command lists per endpoint.
- **Across operations**, the pattern already exists in the codebase:
  cluster operations carry `depends_on` (fate-sharing — at dequeue a
  dependency in `ERROR`/`DELETED`/`ABORT` aborts the dependent
  operation, a missing dependency errors it, an in-flight one defers
  it; see `daemons/queues/workitem.py`) and, separately, `runs_after`
  (ordering only — wait for the operation to finish, outcome
  irrelevant). That is precisely the `&&`-versus-`;` distinction,
  with the cascade following declared edges rather than queue
  adjacency, which is what makes it correct in a shared queue. The
  future direction for agent operations is to extend this existing
  vocabulary rather than invent a lane identifier — see non-goals.

In practice the hazard window is narrow: the dominant client pattern
(including the CI suite) is submit-and-await, where a dependent
operation is only submitted after its predecessor succeeded. Only
callers who pipeline submissions without awaiting can observe a
dependent operation running after its predecessor failed, and those
callers can already cancel their own queued operations via the
existing agent operation DELETE endpoint. Deadline propagation also
softens the expiry case specifically: a pipelined chain submitted
with one client timeout carries the same deadline on every
operation, so when the first expires the rest typically expire with
it rather than executing against missing preparation.

### Connection teardown and agent-side recovery

A question raised during planning: if the hypervisor closes its side
of the virtio-vsock connection, does the agent abort and can we
reconnect? Inspection of both sides shows recovery-by-reconnect
already works at the transport level, which is what makes the abort +
retry design above sufficient without any agent protocol change:

- The executor's vsock connection is scoped to the executor job
  (`with self.instance.socket_on_vsock_channel('sf-agent2')` in
  `SideChannelJob.execute()`), so any executor exit — deadline,
  progress stall, exception — already closes the hypervisor side.
- The agent (`shakenfist_agent/commandline/daemon.py` in the sibling
  `agent-python` repository) accepts **concurrent connections**: each
  `accept()` spawns a fresh daemon worker thread. A retried dispatch
  therefore connects and is welcomed even if a previous worker is
  still wedged; a stuck worker does not block the listener.
- A worker blocked in `recv()` sees EOF on close and exits cleanly. A
  worker mid-transfer hits `BrokenPipeError` on its next send, which
  is caught, abandons the command, and the worker then exits via EOF.
- The one non-recovering case is a worker wedged in something that
  neither sends nor receives — an `execute` child (the agent runs
  `obj.communicate(None, timeout=None)` with no cancellation on
  connection close) or a file read hung in the guest kernel. That
  thread stays wedged and any in-guest side effects continue, but
  because workers are per-connection daemon threads this costs the
  guest a thread, not the operation path.

Consequently a retried attempt may run concurrently with a zombie
prior attempt inside the guest. For `get-file` (concurrent reads of
the same file) this is harmless; for retried `execute` it would mean
running the command twice, which is why `execute` is not retried
(decision 6).

### Command dispatch restructure

The if/elif chain in `_execute_inner()` (`execute` / `put-blob` /
`chmod` / `get-file`) becomes one handler class per command,
instantiated once per `SideChannelExecutorJob` and looked up by
command name, where each handler declares:

- `reports_progress` (bool) — whether the progress timeout applies
  while this command is in flight;
- `retryable` (bool) — whether phase 5 may retry it;
- its dispatch method.

The registry does not own reply handling. Replies are dispatched on
the protobuf field they carry rather than on a command name, and the
two do not map one-to-one — `file_chunk_reply` acks put-blob chunks
while `file_chunk` carries get-file payloads, and the same handler
serves whichever command is in flight. Phase 4's `observe_progress()`
calls therefore go inside the existing reply handlers, and the
registry answers only "may this command's progress be timed out, and
may it be retried".

The registry makes "does this command support progress, and where
would I observe it?" a question with an obvious answer for the next
command someone adds. This is a mechanical refactor with no behaviour
change, done as its own commit before the enforcement logic lands.

### Client (sibling `client-python` repository)

- The SDK's agent-operation await helpers pass their await timeout as
  `deadline_seconds` by default, so the server never keeps working
  after the client has given up. An explicit kwarg overrides.
- New CLI flags on the relevant `sf-client instance` verbs:
  `--deadline` and `--progress-timeout` (0 meaning none/disabled).
- The await loop treats the expiry outcome as terminal.

Old clients keep working: they simply never send the new parameters
and get the 600-second server default, which is tighter than the 900s
constant it replaces but far above observed legitimate operation
times.

New clients against old servers need a guard, which this section
originally did not cover and phase 6 planning added. `log_request`
merges the request body into handler kwargs unconditionally, so a
`deadline_seconds` reaching a server that predates phase 3 is an
undeclared kwarg rather than an ignored field. The client gates every
send on `check_capability('agentoperation-deadlines')`, a token phase
3 should have added to `API_CAPABILITIES` and phase 6 adds.

## Decisions from phase 0

Resolved 2026-08-15 with the operator, on the evidence in
`PLAN-agent-operation-deadlines-phase-00-decisions.md` (a 50-transfer
measurement across five recent merge-queue CI runs, and a
three-repository audit of every consumer of agent operation state).

1. **Expiry is a distinct terminal state, `expired`.** It cleanly
   distinguishes "the caller's budget ran out" from "the operation
   failed" and makes skipped cycles queryable. The audit found no
   consumer that breaks on the new state — old clients already treat
   `error` and `expired` identically (they recognise neither, a
   fail-fast gap tracked as client-python#363) — and enumerated the
   phase 4 obligations: `state_targets` edges (into `expired` from
   every non-terminal state, plus `expired -> deleted`), adding
   `expired` to `FINAL_OBJECT_STATES` (`constants.py:191`) so the
   hard-delete sweep reaps it rather than leaking state rows, guarding
   the five unguarded `state = STATE_ERROR` writes (sidechannel
   `main.py:344,350,844,886`; `node_aop_op.py:89`) that would raise
   `InvalidStateException` from `expired`, and including `expired` in
   the executor's command-abort check (`main.py:910`). The sixth such
   write, `main.py:496`, is already guarded on `STATE_EXECUTING` and
   needs no change. Every address in this decision was refreshed
   during phase 4 planning: phase 1's per-command handler refactor
   moved all of them, and the audit's original numbers
   (`main.py:470,476,794,848`, `:869`, `constants.py:190`) now point
   at unrelated lines.
2. **The progress timeout default is 30 seconds**
   (`AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT`). Measurement: the
   worst complete transfer observed in CI was 625 MB in 2.83 s
   (~220 MB/s), and 48 of 50 transfers finished in under 0.44 s, so
   30 seconds is ~10x headroom over the worst *total* duration, let
   alone any internal gap — while detecting the #3516 wedge 30x
   faster than the 900 s constant it replaces.
3. **The retry attempt cap is 3** (initial dispatch plus two
   retries), as a config option.
4. **The deadline applies during `PREFLIGHT`**, checked inside
   `NodeAgentopOp._preflight()` before and after the potentially long
   `Blob.ensure_local()` copy — the longest pre-queue delay in the
   system, and precisely the time a receipt-anchored deadline exists
   to count.
5. **The reaper is an extension of `reap_instance_executors()`**
   (`daemons/sidechannel/main.py:1270`), which already runs at the top
   of every dispatcher pass serialised with dispatch. It must cover
   both a dead executor thread and the no-entry case after a daemon
   restart, with the database read gated on the instance actually
   having a non-terminal operation so the idle fast path stays cheap.
6. **`execute` is not retried.** Retryability is a per-command
   capability flag alongside `reports_progress` in the dispatch
   registry — true for transfers, false for `execute` — because a
   retried `execute` re-runs a possibly non-idempotent command while
   the agent cannot cancel the prior attempt's child process, so both
   attempts' side effects could land.

## Non-goals

- Fixing the agent-side get-file delivery bug itself (#2240). This
  plan converts it from test-fatal to a logged, retried blip; the
  agent bug remains open and separately tracked.
- Progress reporting for `execute`. If the agent ever streams command
  output, `execute` can declare `reports_progress` then; nothing in
  this design precludes it.
- Agent-side cancellation of in-flight work. When the hypervisor
  abandons a connection, an `execute` child keeps running in the
  guest and a wedged worker thread is leaked until the guest agent
  restarts. A cancellation protocol (kill the child on
  `hypervisor_departure` or connection close) is an `agent-python`
  change worth its own issue, but nothing here depends on it.
- Concurrent agent operations on one instance. The
  one-executor-per-instance rule is load-bearing across the
  orchestration model, and relaxing it is a re-engineering of
  dispatch, durability and ordering semantics together — see
  "Why concurrency stays out of scope" below. The dependency-based
  future is captured separately in
  `PLAN-agent-operation-dependencies.md`.
- Deadlines for cluster operations (`ClusterOperation`). The
  mechanism is deliberately agent-operation-scoped for now; if it
  proves useful the schema pattern can be lifted to `BaseOperation`
  later.

### Why concurrency stays out of scope

The one-executor-per-instance rule is not a transport limitation —
the agent already runs a worker thread per connection. But it is
load-bearing in three places:

1. The strict FIFO queue is a user-visible semantic that callers rely
   on to chain operations ("put a file, then execute it"), so
   parallelism would need explicit dependency declaration in the API,
   the way cluster operations chain.
2. `agent_operation_next()`'s crash-safety argument is literally "the
   head of the queue cannot double dispatch because there is one
   executor"; concurrency would replace that head pointer with
   per-operation claims or leases.
3. This plan's own reaper and retry designs are race-free *because*
   dispatch and reaping for an instance serialise in one process.

Relaxing it is therefore its own master plan if ever wanted. The
nearer future — explicit ordering and fate-sharing without
concurrency — is already planned:
`PLAN-agent-operation-dependencies.md` extends the cluster operation
`depends_on`/`runs_after` vocabulary to agent operations (including
cross-instance edges and per-edge settle delays), gated on this plan
landing because a dependency-blocked operation accrues queue time
against its deadline, so user-created dependency cycles resolve by
expiry rather than deadlocking.

Meanwhile this plan shrinks the cost of serialisation itself:
head-of-line blocking drops from 900 seconds to roughly the progress
timeout, and queue-time expiry stops abandoned work from occupying
the slot at all.

## Phases

| Phase | Plan | Status | Content |
|-------|------|--------|---------|
| 0 | [PLAN-agent-operation-deadlines-phase-00-decisions.md](PLAN-agent-operation-deadlines-phase-00-decisions.md) | Complete | Open questions resolved into the decisions section above; measurement and state-audit results recorded in the phase plan |
| 1 | [PLAN-agent-operation-deadlines-phase-01-groundwork.md](PLAN-agent-operation-deadlines-phase-01-groundwork.md) | Complete | Field mask for `update_agent_operation_attributes`; per-command handler classes replacing the dispatch if/elif chain, declaring `reports_progress` and `retryable` for phases 4 and 5 to read (no behaviour change); initialising the get-file transfer state so its existing guard raises `GetException` rather than `AttributeError` |
| 2 | [PLAN-agent-operation-deadlines-phase-02-schema.md](PLAN-agent-operation-deadlines-phase-02-schema.md) | Complete | Schema: `deadline`/`progress_timeout` columns, `last_progress`/`attempts` attributes, both table versions 2 -> 3, additive migrations, and a live-MariaDB test that they migrate. Survey corrections applied at source: NULL means "server default" rather than "no deadline", and there is deliberately no object version bump |
| 3 | [PLAN-agent-operation-deadlines-phase-03-api.md](PLAN-agent-operation-deadlines-phase-03-api.md) | Complete | API: `deadline_seconds` on all three creating endpoints and `progress_timeout_seconds` on get/put only, their declarations and `STRUCTURED_PARAMETERS` entries, a 400 guard backing the published bound, and the two config defaults. Survey correction applied at source: `execute` does not publish a progress timeout it can never honour, and the API writes a computed deadline rather than NULL |
| 4 | [PLAN-agent-operation-deadlines-phase-04-enforcement.md](PLAN-agent-operation-deadlines-phase-04-enforcement.md) | Complete | Enforcement: dequeue expiry, preflight expiry, executor deadline + progress timeout, `observe_progress()` hooks; remove `AGENT_OPERATION_EXECUTION_TIMEOUT`; the `expired` state with its audit-enumerated obligations (`state_targets`, `FINAL_OBJECT_STATES`, guarded error writes, command-abort check). Survey corrections applied at source: every address in decision 1 was refreshed after phase 1's refactor, the reaper is phase 5's rather than this phase's, and preflight is a fourth enforcement point the design sketch never listed. The phase plan also decides that an expiry records its reason as a state message and an audit event rather than as `.error`, which the `error` setter refuses from a non-`error` state |
| 5 | [PLAN-agent-operation-deadlines-phase-05-retry.md](PLAN-agent-operation-deadlines-phase-05-retry.md) | Complete | Retry: `EXECUTING -> QUEUED` edge, terminal-only lazy pop, attempt bound, partial-result cleanup; the node-local reaper, covering a dead executor thread, no executor at all after a daemon restart, and a live executor wedged in the pre-connection wait. Survey corrections applied at source: decision 5's address for `reap_instance_executors()`, that retryability is evaluated over the whole command list rather than the command in flight, and that partial-result cleanup has a registered-blob case the design sketch did not cover. The phase plan also decides that the terminal-only pop and the reaper must land in one commit, because the pop rule alone turns a daemon restart from a leak into a wedge |
| 6 | `PLAN-agent-operation-deadlines-phase-06-client.md` (in shakenfist/client-python, branch `agent-operation-deadlines-phase-06-client`) | Complete | client-python: deadline from await timeout, CLI flags, terminal-state handling (includes fixing client-python#363: await loops poll to their full timeout on terminal failure states instead of failing fast). The plan file lives in the client repository because the code does, following the `PLAN-vdi-console-tokens.md` precedent; a relative link cannot cross repositories, so it is named here rather than linked. Survey corrections applied at source: the new parameters need a capability token before a client can send them safely, which phase 3 did not add and phase 6 does. The phase plan also decides to repair `await_agent_fetch()`'s three hardcoded timeout windows, which make its `timeout` argument a lie, because the phase rewrites those same loops. The phase also landed two pull requests in this repository, which the row above does not name because the phase plan lives elsewhere: #4005 added the capability token in `shakenfist/external_api/app.py` that a client needs before it can safely send the new parameters, and #4015 moved the CI suite onto the new client |
| 7 | [PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md](PLAN-agent-operation-deadlines-phase-07-docs-and-ci.md) | Complete | Documentation and CI coverage. Survey corrections applied at source, because this row was written before phases 3 to 6 executed and was wrong in four places. (a) The documentation was **not** deferred from phase 3: `cf094551b` wrote both the API reference at `docs/developer_guide/api_reference/instances.md:657-710` and the release note section at `docs/release_notes/v07-v08.md:731-782`. What is missing is phase 5 (retry, attempts and the reaper), phase 6 (the CLI flags and the `AgentCommandError` to `AgentOperationFailed` change), and an operator-guide page for agent operations, of which there is none. (b) `base.AGENT_OPERATION_FAILURES` must **not** narrow to a plain `AgentOperationFailed` reference: the `getattr()` shim goes, but the tuple stays and gains `AgentAwaitTimeout`, because `await_agent_command()` raises three unrelated exceptions and the two catch sites are `_await_instance_ready`'s retry loop, which wants all of them. Only the two `test_get_missing_file` assertions narrow. (c) There is a **third** event-renewed await, `_await_objects_ready`, which backs the network, artifact and blob readiness helpers and so sits on nearly every test in the suite; #3770 is not fixed without it. (d) Commit `a0cc243ad` fixed the shared-clock flake in `guest_ci_tests/test_agentops.py` only, and the identical `smoke_ci_tests` copy -- the suite that runs on every pull request -- still has it. Note `docs/developer_guide/state_machine.md` is **not** this phase's: it is a rendering of `state_targets`, so phase 4 updated it when it added `expired` rather than leaving the tree self-contradictory for three phases, and phase 5 added its one further edge |
| 8 | [PLAN-agent-operation-deadlines-phase-08-push-audit.md](PLAN-agent-operation-deadlines-phase-08-push-audit.md) | In progress | Push audit: runs `PUSH-AUDIT.md` over this plan's work as one body, not the last phase's diff alone. Survey correction applied at source: the "accumulated diff against `develop`" this row used to describe cannot be taken, because every phase is merged and `git diff develop...HEAD` is empty; the baseline is instead the explicit merge list in decision 1 of the phase plan, which also pulls in the three defect fixes that landed against this plan's code outside its phase branches (#3933, #3970, #4025). Findings land as their own pull request, and the plan is not complete until each is resolved or declined in writing here; if the audit finds nothing, that is recorded in one sentence |

Each phase gets its own detailed plan file before implementation.
Unit tests land with each phase; the functional test in phase 7
exercises at minimum: an explicit short deadline expiring a queued
operation, the default deadline appearing in `external_view()`, an
`execute` of a long-running command surviving longer than the
progress timeout (proving the progress timeout does not apply to
non-progress commands), and a follow-up operation dispatching
promptly after a first operation is expired (proving the executor
slot is actually freed).

### The CI suite's own awaits have the bug this plan fixes (#3770)

Found during merge CI triage of PR #3764 and verified against the
tree. `_await_agent_state`
(`shakenfist/deploy/shakenfist_ci/base.py:522`, `:436` when this was
written) looks like a 500-second deadline and is not one:

```python
time_since_last_progress = time.time()
while time.time() - time_since_last_progress < 500:
    ...
    events = self.system_client.get_instance_events(instance_uuid, limit=1)
    if events:
        last_event = events[0]
        time_since_last_progress = last_event['timestamp']
```

`get_instance_events(..., limit=1)` returns the most recent event of
*any* type, so an instance going nowhere while still emitting events
on any sub-500s cadence renews the window forever. The variable is
named for progress; nothing in the query restricts it to progress.
`_await_instance_create` (`base.py:557`, `:471` when this was written)
is the identical construction with a 180-second window. In run
31856630647 this cost the Guests job its entire 60-minute budget: two
agent-awaiting tests never completed, and because the step was killed
rather than failed, stestr wrote no results and the job named no
failing test at all.

Phase 7's survey found a third loop the audit missed:
`_await_objects_ready` (`base.py:706`) renews a 300-second window the
same way, and backs `_await_networks_ready`, `_await_artifacts_ready`
and `_await_blobs_ready`, so it sits on nearly every test in the
suite. Fixing only the two named above would leave the bug class in
place. The mechanism is also now confirmed rather than inferred:
`node_inst_op` (`shakenfist/operations/node_inst_op.py:169`) writes an
`EVENT_TYPE_USAGE` event against every instance on a timer, so a
stalled instance renews its own deadline forever.

This is the same two-knob confusion the plan resolves server-side --
a progress window is not a deadline -- so phase 7 fixes it the same
way: keep the event-renewed progress window, add an absolute ceiling
on the whole await, and prefer restricting renewal to events that
actually represent progress. The ceiling is the part that converts a
future occurrence from a silent budget fire into a diagnosable test
failure with a console dump. While there, correct the timeout
message, which says "no progress in 5 minutes" for a 500-second (8m
20s) window.

Note the causal link to #3516 is plausible but unproven: both stalled
tests await an agent, but nothing in that run confirms an orphaned
agent operation, and #3696 is a distinct symptom (there the runner
lost communication and uploaded nothing).

**Partly pulled forward into phase 4.** `_await_command()`
(`base.py:760`) was the worst of this family -- no window at all, just
`while aop['state'] != 'complete'` -- and phase 4 made it materially
more likely to fire, since an operation can now reach `expired` where
it previously ran to completion. That one loop gained an absolute
bound, a terminal-state check on `error`/`expired`/`deleted`, and a
failure which dumps the operation and the instance's recent events;
see the second review response in
`PLAN-agent-operation-deadlines-phase-04-enforcement.md`. The
event-renewed loops above are untouched and remain phase 7's, as does
restricting renewal to events that represent progress.

**A second slice pulled forward, by CI ordering rather than by
choice.** Phase 6's client change (client-python#380) makes a
terminal agent operation state raise the new `AgentOperationFailed`
on the first poll, where every await loop previously spun out its
budget and raised `AgentCommandError`. Three places in this suite
were written against the old behaviour: `test_get_missing_file` in
both `smoke_ci_tests/test_agentops.py` and
`guest_ci_tests/test_agentops.py` asserts `AgentCommandError`, and
`_await_instance_ready`'s cloud-init retry loop (`base.py`) catches
it twice -- so under the new client a failed health check would
escape the retry rather than being retried. Neither repository could
switch first, because this repository's CI builds the client from
client-python's `develop` and client-python's CI builds the server
from this repository's `develop`. The deadlock is broken by
`base.AGENT_OPERATION_FAILURES`, a tuple which accepts either
exception and resolves the new one through `getattr()` so it still
imports against an old client. Phase 7 removes the `getattr()` -- but
not the tuple, which its survey found must instead grow to name all
three exceptions `await_agent_command()` can raise, since the two
catch sites want breadth and only the two `test_get_missing_file`
assertions want the narrow reference.
