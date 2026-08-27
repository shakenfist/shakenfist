# Agent operation deadlines phase 5: retry and the reaper

## Prompt

Plan the next phase of `PLAN-agent-operation-deadlines.md` with the
`next-phase` skill, after phase 4 merged as PR #3898. Phase 4 made the
timing budgets bite: an operation that outlives its deadline or stalls
mid-transfer now reaches a terminal state instead of hanging. This
phase is the one that makes a stall survivable rather than fatal, and
that closes the wedge shapes phase 4's three enforcement points cannot
see.

## Planning effort

High. Two of the three changes here are to invariants rather than to
code volume. Terminal-only pop rewrites the durability rule that
`agent_operation_next()`'s docstring, the executor's `finally` block
and the dispatcher's comments all describe today, and it converts a
current leak (an operation orphaned in `executing` while its queue
drains) into a wedge (the queue stops) unless the reaper lands in the
same commit. The retry edge then has to decide which failures deserve
another attempt without re-running side effects the agent cannot take
back. The reaper itself is the mechanical part.

## Scope

**In scope.**

- The `EXECUTING -> QUEUED` edge in `AgentOperation.state_targets`,
  and `docs/developer_guide/state_machine.md`, which is a rendering of
  that dict (the master plan's phase 7 row already says this one page
  is phase 5's, not phase 7's).
- Terminal-only lazy pop in `Instance.agent_operation_next()`.
- Attempt counting: writing `attempts` (the attribute exists and is
  read-only today) and a new `AGENT_OPERATION_MAX_ATTEMPTS` config
  option defaulting to 3, per phase 0 decision 3.
- Reading the per-command `retryable` flag, which phase 1 declared for
  exactly this phase and nothing has read since.
- The retry decision at the executor's two exit paths:
  `expire_if_out_of_budget()`'s progress-stall branch and
  `SideChannelExecutorJob.execute()`'s `finally`.
- Clearing the abandoned attempt's `results` when a retry is
  scheduled.
- The node-local reaper, extending `reap_instance_executors()` per
  phase 0 decision 5, covering three cases: a dead executor thread, no
  executor entry at all after a daemon restart, and a live executor
  blocked in the pre-connection wait (phase 4 review item 12).
- Making that pre-connection wait abortable, without which the reaper
  can resolve the operation but cannot stop the thread holding the
  instance's executor slot.
- Unit tests for each of the above.

**Out of scope.**

- **`client-python`.** Phase 6, including making await loops treat
  `expired` as terminal (client-python#363). A retried operation is
  indistinguishable from a slow one to a client, so nothing here
  forces a client change.
- **Release notes, operator and user guide.** Phase 7, which writes
  the whole timing story once. In particular the "ordering, not
  dependency" contract that the master plan's *Failure semantics
  between operations* section says must be written down explicitly is
  phase 7's, not this phase's, even though this phase is what makes a
  failed head stop blocking its queue in a new way.
- **Functional CI coverage.** Phase 7.
- **Deleting the blob an abandoned attempt registered.** See decision
  6: the cluster daemon already reaps blobs with no references, and
  duplicating that policy in the sidechannel daemon would put blob
  lifetime in two places.
- **The `_request_thread_exit()` defect** found in passing, filed as
  #3931. See *What the survey found*, finding 8, and *Future work*.

## What the survey found

Verified against `develop` at `c819cbb04`, which includes phase 4.

1. **Decision 5's address is stale.** It cites
   `reap_instance_executors()` at `daemons/sidechannel/main.py:972`;
   it is at `:1270`. Phase 4's additions moved it. Corrected at
   source.

2. **`attempts` exists but has never been written.** The schema field
   is in `shakenfist/schema/agentoperation_attributes.py:58` (`int`,
   defaulting to `0`, deliberately non-optional "so a reader never has
   to write `attempts or 0`"), the read-only property is at
   `shakenfist/operations/agentoperation.py:377-379`, and it is
   published in `external_view()` at `:195`. Nothing increments it.
   Phase 5 is the first writer.

3. **The attempt cap has no config option yet.** `shakenfist/config.py`
   carries `AGENT_OPERATION_DEFAULT_DEADLINE` (`:240`) and
   `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` (`:259`) and nothing
   else in that family. Phase 0 decision 3 fixed the cap at 3 "as a
   config option"; phases 2 and 3 did not add it, so this phase does.

4. **`retryable` is declared and unread.** `AgentCommandHandler`
   defaults it to `True` (`main.py:322`, with the comment "read in
   phase 5") and `ExecuteCommand` sets it `False` (`:334`). The
   sibling flags `reports_progress` and `register_as_outstanding` are
   both read; `retryable` is not read anywhere. The registry is a
   module-level list, `AGENT_COMMAND_HANDLERS` (`:453`), so the
   retryability of a command list can be computed without an executor
   instance -- which the reaper needs.

5. **`last_progress` is persisted and read by nothing.**
   `AgentOperation.record_progress()`
   (`operations/agentoperation.py:399-414`) writes it with a field
   mask, driven from the executor's `observe_progress()`. It exists
   solely so this phase's reaper has something to read, exactly as
   phase 4 said.

6. **No `EXECUTING -> QUEUED` edge.**
   `AgentOperation.state_targets[STATE_EXECUTING]`
   (`operations/agentoperation.py:67-69`) is
   `(COMPLETE, DELETED, ERROR, EXPIRED)`.

7. **Two comments become false the moment this phase lands**, and both
   are load-bearing explanations rather than incidental:

   - `Instance.agent_operation_next()`'s docstring
     (`instance.py:2457-2482`) states the current rule, and the branch
     it describes -- "EXECUTING, COMPLETE, ERROR, EXPIRED or DELETED:
     this operation is finished with the queue" (`:2552-2558`) -- is
     the exact line this phase inverts.
   - `SideChannelExecutorJob.execute()`'s `finally`
     (`main.py:503-511`) opens "An operation is popped from the
     instance's queue as soon as it reaches EXECUTING (see
     Instance.agent_operation_next), so it is never re-dispatched",
     which is precisely the premise this phase removes. That block is
     also the natural home for the retry decision, so the same step
     rewrites both.

8. **A defect found in passing, out of scope.**
   `_request_thread_exit()` (`main.py:1297-1310`) is called for
   monitors (`:1288-1290`) and then for executors (`:1292-1295`), but
   its body operates on `self.monitors` throughout: it joins
   `self.monitors[instance_uuid]['thread']` and deletes that entry,
   never the executor's. For an executor call this joins the wrong
   thread, leaves the executor entry in place, and raises `KeyError`
   if the monitor loop immediately above already removed the monitor.
   It also emits "side channel monitor instructed to exit" for an
   executor. This is daemon-shutdown-path only and predates this plan
   entirely, introduced in `69f27b7ad` (2024-12-15). Filed as #3931
   and recorded in *Future work* rather than fixed here, because a
   shutdown-ordering change wants its own testing and would be
   invisible inside a commit about retry.

9. **A latent result-versus-blob hazard the design sketch does not
   mention.** Retry restarts the command list from index 0.
   `add_result()` (`agentoperation.py:381-394`) keys results by index,
   so a re-run overwrites in place -- but `GetFileCommand` mints a
   fresh blob uuid per attempt (`Blob.new` at `main.py:727`,
   `register()` at `:738`, `add_result()` at `:745`). An operation
   whose *earlier* `get-file` succeeded and whose *later* command
   stalled would therefore register a second blob on retry and
   overwrite the only record of the first.

   It is latent rather than live: the three command lists the API
   builds are `[put-blob, chmod]` (`external_api/instance.py:1723`,
   `:1728`), `[get-file]` (`:1789`) and `[execute]` (`:1848`), and
   none of them puts a `get-file` ahead of another command. Decision 5
   handles it anyway, because the fix is one line and the shape of the
   bug is invisible at the call site that would introduce it.

10. **The pre-connection wait is confirmed as described.**
    `SideChannelJob.execute()` (`main.py:124-136`) does
    `while not os.path.exists(console_path): time.sleep(1)` with no
    abort-path check and no bound, before any budget is evaluated.

Nothing else in the master plan's *Retry* section or in phase 0
decisions 3, 5 and 6 was found to be wrong.

## Decisions

1. **Terminal-only pop and the reaper are one commit, not two.**
   Today an operation orphaned in `executing` -- the sidechannel
   process killed before phase 4's `finally` could run -- leaks the
   operation but the queue drains, because the pop retires an
   `EXECUTING` head. After terminal-only pop the same event wedges
   the instance's queue permanently. The reaper is what converts that
   back into a drain, so shipping the pop rule first would put a
   window in the tree where a daemon restart is strictly worse than
   it is today. They land together, and the test that proves it is
   "a daemon restart with an operation in `executing` and no executor
   entry drains the queue".

2. **Retry is for a stalled attempt, never for a failed one.** The
   eligible triggers are the progress timeout firing, the executor
   exiting with the operation still `executing` (a dropped
   connection, a socket error the base `execute()` swallowed, an
   unexpected exception), and the reaper finding no live executor.
   The ineligible ones are:

   - **The wall-clock deadline passing.** Retrying spends time
     nobody is waiting for; the caller's budget is the thing that
     just ran out. Expire.
   - **An agent-reported command error.** Phase 4 made this
     `fail()` deliberately -- the agent told us in detail what went
     wrong, and re-running produces the same error. Error.

   This is the phase 4 distinction applied one level up: `error`
   means the operation failed, `expired` means a budget ran out, and
   retry exists only for the third case, where neither has happened
   yet and the attempt merely got nowhere.

3. **When attempts are exhausted, the outcome is the one the retry
   replaced.** A stall that runs out of attempts expires (the
   progress timeout is a caller-set budget, and phase 4 already
   expires on it), with a message naming the attempt cap so an
   operator reading `object_states.message` can tell "stalled once
   and gave up" from "stalled three times and gave up". An executor
   exit that runs out of attempts errors, preserving phase 4's
   message. The distinction survives the retry loop rather than
   collapsing into one outcome.

4. **Retryability is a property of the whole command list, not of
   the command in flight.** Because retry restarts at index 0, an
   operation containing any non-retryable command must not be
   retried -- otherwise an `[execute, get-file]` operation stalling
   in the `get-file` would re-run the `execute`, which is exactly
   what phase 0 decision 6 forbids. Today this is equivalent to the
   per-command rule, since no endpoint builds a mixed list (survey
   finding 9), so the choice costs nothing now and is the only
   version that stays correct when one does. The helper reads
   `AGENT_COMMAND_HANDLERS` (`main.py:453`) by command name and
   treats an unknown command as non-retryable.

   This is the decision most likely to be argued with: the cheaper
   reading is "the command that stalled is a `get-file`, and
   `get-file` is retryable, so retry", and it would ship a smaller
   diff. It is wrong for a reason that will not show up in any test
   we can write today, because no API endpoint can currently produce
   the list that breaks it.

5. **A retry clears the abandoned attempt's `results`.** The next
   attempt rewrites every index it reaches, so the only rows a clear
   removes are ones the retry is about to replace or was never going
   to reach. Leaving them is what makes survey finding 9 a bug: a
   stale `content_blob` pointing at a blob nothing else references,
   presented to the caller as this operation's result.

6. **A retry does not delete the blob the abandoned attempt
   registered.** That blob has no `object_references` row -- the
   result is a JSON field, not a reference -- so the cluster
   daemon's existing unreferenced-blob sweep collects it
   (`daemons/cluster/main.py:276-277`,
   `daemons/cluster/scheduled_tasks.py:330`). Having the sidechannel
   daemon hard-delete blobs too would put blob lifetime policy in
   two daemons for no gain. Phase 4's `_abandon_get_file_transfer()`
   (`main.py:839`) already handles the *incomplete* file, which is
   the case that would otherwise leave bytes on disk with no object
   at all.

7. **The reaper resolves the operation and aborts the thread, in
   that order.** For a live executor stuck in the pre-connection
   wait, expiring the operation alone leaves the thread spinning and
   the instance's executor slot held, so the reaper also sets the
   job's abort path -- and the wait loop gains an abort check it
   does not have today (survey finding 10). The order matters:
   phase 4's `finally` only rewrites an operation still in
   `executing`, so resolving first means the exiting thread cannot
   overwrite the reaper's verdict.

8. **The reaper only acts on evidence, never on suspicion.** For a
   live executor it acts solely on `deadline_passed()`, which is an
   absolute timestamp and cannot race a thread making progress. For
   a missing or dead executor it acts on the absence of the thread,
   which it can observe directly. It never second-guesses a live
   executor's progress timeout -- that is the executor's own job and
   it holds the state the reaper does not.

9. **The database read stays off the idle path.** Phase 0 decision 5
   requires it: `reap_instance_executors()` runs at the top of every
   dispatcher pass for every instance on the node. The reaper reads
   an operation only for an instance whose in-memory queue head is
   already known to be worth looking at, reusing the cheap
   unlocked attribute peek `agent_operation_next()` opens with
   (`instance.py:2486`) rather than adding a second poll.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | The retry edge, the attempt cap, and the policy helpers, with nothing calling them yet. In `shakenfist/operations/agentoperation.py`: add `BaseOperation.STATE_QUEUED` to the `STATE_EXECUTING` row of `state_targets` (line 67-69), leaving every other row alone. Add an `attempts` writer next to the existing read-only property at `:377-379` -- `record_attempt()`, which reads `self._attributes()`, increments `attempts`, and writes through `mariadb.update_agent_operation_attributes(attrs, fields=['attempts'])`; the field mask is not optional, for the reason `record_progress()` at `:399-414` spells out, and that method is the pattern to mirror exactly. Add `clear_results()` in the same style, writing `fields=['results']`, and note it must take the same `get_lock_attr('results', ...)` that `add_result()` at `:381-394` takes, since the two write the same column. In `shakenfist/config.py`, add `AGENT_OPERATION_MAX_ATTEMPTS: int = Field(3, ...)` immediately after `AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT` (`:259`), following the description style of its two neighbours: say that it counts the initial dispatch plus retries, that it is the only bound on an operation created with `deadline_seconds=0`, and that `execute` operations are never retried whatever it is set to. Add `shakenfist/tests/test_agent_operation_retry.py` covering: `executing -> queued` is permitted; `expired -> queued` and `complete -> queued` are not; `record_attempt()` increments from the default `0` and passes `fields=['attempts']`; and `clear_results()` empties the dict and passes `fields=['results']`. Commit subject: `Add the agent operation retry edge.` |
| 5b | high | opus | none | Terminal-only pop and the reaper, together -- see decision 1 for why these cannot be separate commits. In `Instance.agent_operation_next()` (`shakenfist/instance.py:2456`): change the final branch at `:2552-2558` so it pops only when `state.value in AgentOperation.TERMINAL_STATES` (the tuple is at `operations/agentoperation.py:41`), and add an `EXECUTING` branch which `break`s -- the head is being worked on, and the instance already has an executor so the dispatcher would skip it anyway. Rewrite the docstring at `:2457-2482`: the invariant becomes "the queue entry lives at the head until the operation reaches a terminal state", a strengthening of today's rule, and the sentence about the executor moving it to `EXECUTING` is now wrong. In `shakenfist/daemons/sidechannel/main.py`, extend `reap_instance_executors()` (`:1270`) to cover the three cases in decision 7 and 8. It currently only sweeps dead threads out of `self.executors`; after doing so it must also, for each instance in `self.monitors`, resolve an operation the queue is now stuck behind. Read an operation only when the instance's cheap unlocked `agent_operations` attribute peek shows a queue (decision 9 -- mirror the early-out at `instance.py:2486`; do not add a second poll). If the head is `EXECUTING` and there is no entry in `self.executors` for that instance, the executor died with the daemon or was reaped above: resolve it. If the head is `EXECUTING`, there *is* a live executor, and `agentop.deadline_passed()`, the executor is wedged somewhere no budget is evaluated (the pre-connection wait -- see step 5d): resolve it, then `daemon.set_abort_path(...)` on the executor's `t['object'].abort_path`, in that order, because phase 4's `finally` at `:512-518` only rewrites an operation still in `EXECUTING`. Do not touch a live executor for any other reason (decision 8). "Resolve" here means calling the shared policy helper step 5c adds; write step 5b against a helper named `resolve_abandoned_operation(agentop)` and let 5c fill it in, or land 5c first -- the two steps are ordered either way, not parallel. Tests: extend `shakenfist/tests/test_instance.py`'s `AgentOperationQueueTestCase` (`:733`) so an `EXECUTING` head is returned-as-nothing and left on the queue rather than popped, and each terminal state still pops; and add a reaper class to `shakenfist/tests/test_daemon_sidechannel_executor.py` proving the daemon-restart case drains (an `EXECUTING` head, an empty `self.executors`, one pass, queue drains) and that an instance with no queued operations performs no database read at all. Commit subject: `Keep agent operations queued until terminal.` |
| 5c | high | opus | none | The retry policy itself, in `shakenfist/daemons/sidechannel/main.py`. Add a module-level `operation_is_retryable(agentop)` which maps each entry of `agentop.commands` to its handler class by `cmd['command']` against `AGENT_COMMAND_HANDLERS` (`:453`) and returns True only if every command's class has `retryable` True, treating an unknown command name as non-retryable (decision 4 -- this is deliberately the whole list, not the command in flight). Add `resolve_abandoned_operation(agentop, reason, terminal)` alongside it, which is the single place the retry decision is made and is called by both the executor and the reaper: retry when `operation_is_retryable(agentop)` and `not agentop.deadline_passed()` and `agentop.attempts < config.AGENT_OPERATION_MAX_ATTEMPTS`; retrying means `agentop.clear_results()`, then `agentop.state = AgentOperation.STATE_QUEUED`, plus one `EVENT_TYPE_AUDIT` event carrying the reason and the attempt number. Otherwise apply `terminal`, which the caller passes as `agentop.expire` or `agentop.fail` per decision 3, with a message that names why no retry happened (deadline passed, attempts exhausted with the count, or the command is not retryable). The attempt counter is incremented on *dispatch*, not on retry, so add the `agentop.record_attempt()` call in `start_instance_executor()` (`:1239-1268`) next to the existing "side channel executor started" event -- counting on dispatch means a first attempt reads `attempts == 1` while executing, and the cap is a dispatch count as decision 3 and the config description both say. Then wire the two executor exit paths. In `expire_if_out_of_budget()` (`:775`), leave the deadline branch at `:799-806` exactly as it is (decision 2: a passed deadline never retries) and change only the progress-stall branch at `:830-835` to call `resolve_abandoned_operation(..., terminal=self.agentop.expire)`; it must still return True. In `execute()`'s `finally` (`:499-518`), replace the unconditional `fail()` with `resolve_abandoned_operation(..., terminal=self.agentop.fail)` and rewrite the comment, whose first sentence -- "An operation is popped from the instance's queue as soon as it reaches EXECUTING ... so it is never re-dispatched" -- is the premise step 5b removed. Do not make `_handle_command_error()` (`:520`) retry: phase 4 made it `fail()` deliberately and decision 2 keeps it that way. Tests in `shakenfist/tests/test_daemon_sidechannel_executor.py`: every branch of `operation_is_retryable()` including the mixed and unknown-command lists; that a stalled `get-file` under the cap returns to `queued` with its results cleared; that the same stall at the cap expires with a message naming the cap; that a stalled operation whose deadline has passed expires rather than retrying, even with attempts left; that an `execute` operation never retries; and that an executor exit with the operation still executing retries once and then errors. Commit subject: `Retry stalled agent operations.` |
| 5d | medium | sonnet | none | Make the pre-connection wait abortable, so step 5b's reaper can actually free the executor slot it just resolved. In `SideChannelJob.execute()` (`shakenfist/daemons/sidechannel/main.py:124-136`), the loop `while not os.path.exists(console_path): time.sleep(1)` runs before any budget is evaluated and checks nothing. Change it to also check `daemon.check_abort_path(self.abort_path)` each iteration -- the same call the socket loop already uses (`:1323` in the dispatcher is the idiom, and `_request_thread_exit()` at `:1297` shows how the abort path is set) -- and return cleanly when it fires, logging at debug the way the existing `'Abort path set, exiting'` branch does. Returning from `execute()` here reaches `SideChannelExecutorJob.execute()`'s `finally`, which is correct: by the time the reaper sets the abort path it has already resolved the operation, so the `finally` finds a terminal state and leaves it alone. Add a test to `shakenfist/tests/test_daemon_sidechannel_executor.py` that the wait returns promptly when the abort path is set and the console file never appears. Commit subject: `Let the pre-connection wait be aborted.` |
| 5e | low | sonnet | none | Documentation and closeout. In `docs/developer_guide/state_machine.md`, add the one new edge `executing --> queued` to the agent operations mermaid diagram and a sentence to the accompanying prose saying it is a retry of a stalled attempt, bounded by `AGENT_OPERATION_MAX_ATTEMPTS` and by the operation's deadline, and that `execute` operations never take it. Do not write anything else about retry anywhere in `docs/`: phase 7 writes the operator-facing and user-facing timing story once, and this page is the exception only because it is a rendering of `state_targets` (the same reasoning phase 4 recorded as its decision 9). Then set phase 5 to `Complete` in the master plan's phase table. `docs/plans/index.md` carries one row for the whole plan with a phase count; move it from `5 of 9` to `6 of 9` and leave its status `In progress`. Commit subject: `Document the agent operation retry edge.` |

## Corrections applied at source

Made as part of the planning commit, so a later step does not redo
them:

- Phase 0 decision 5's address for `reap_instance_executors()` is
  refreshed from `main.py:972` to `:1270`.
- The *Retry* section gains a sentence recording that retryability is
  evaluated over the whole command list rather than the command in
  flight, and why (decision 4), since the section's wording implies
  the per-command reading.
- The *Retry* section's partial-results paragraph gains the
  registered-blob case from survey finding 9, which it did not cover:
  it addresses only the incomplete blob, which phase 4 already
  handles.

## Risks and mitigations

- **The pop rule change wedges a queue in a case the reaper does not
  cover.** This is the one failure mode that is worse than today
  rather than merely unfixed. Mitigation: decision 1 keeps the two in
  one commit, and step 5b's test list names the daemon-restart case
  explicitly. The reviewer's job here is to look for a fourth way an
  operation can sit in `executing` with nothing watching it -- the
  three known ones are dead thread, absent entry, and wedged live
  thread.
- **The reaper races a live executor.** Mitigation: decision 8 limits
  live-executor action to `deadline_passed()`, an absolute timestamp
  that cannot be wrong about a thread still making progress, and
  decision 7's resolve-then-abort order means phase 4's guarded
  `finally` cannot overwrite the verdict. Checked by reading, and by
  the step 5c test that an operation already terminal when the
  executor exits keeps its state.
- **A retry re-runs a side effect the agent cannot take back.**
  Mitigation: decision 4's whole-list rule plus phase 0 decision 6's
  `retryable = False` on `execute`. The step 5c test list includes the
  mixed-list case even though no endpoint can build one today.
- **The attempt cap becomes the only bound on an unbounded
  operation.** An operation created with `deadline_seconds=0` retries
  until the cap and no further, which is the cap's stated purpose --
  but it also means such an operation now fails after three stalls
  where phase 4 left it running forever. That is an improvement, and
  it is a behaviour change worth naming in phase 7's release note.
  Mitigation: recorded here so phase 7 has it.
- **`clear_results()` and `add_result()` write the same column.**
  Mitigation: step 5a's brief requires the same
  `get_lock_attr('results', ...)` and the same field mask, which is
  the rule `CLAUDE.md` states and which the vanished-agent-operation
  flake came from breaking.

## Definition of done

Runnable from the repository root. The python checks need the project
importable, so run them with `.tox/py3/bin/python`.

```sh
# 1. The retry edge exists and goes only where it should.
.tox/py3/bin/python - <<'EOF'
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.operations.agentoperation import AgentOperation as A

assert A.STATE_QUEUED in A.state_targets[A.STATE_EXECUTING]
for src in (A.STATE_COMPLETE, dbo.STATE_ERROR, A.STATE_EXPIRED):
    assert A.STATE_QUEUED not in A.state_targets[src], src
print('retry edge ok')
EOF

# 2. Retryability is evaluated over the whole command list, and an
#    unknown command is not retryable (decision 4).
.tox/py3/bin/python - <<'EOF'
from unittest import mock
from shakenfist.daemons.sidechannel import main

def op(*names):
    return mock.Mock(commands=[{'command': n} for n in names])

assert main.operation_is_retryable(op('put-blob', 'chmod'))
assert main.operation_is_retryable(op('get-file'))
assert not main.operation_is_retryable(op('execute'))
assert not main.operation_is_retryable(op('execute', 'get-file'))
assert not main.operation_is_retryable(op('get-file', 'execute'))
assert not main.operation_is_retryable(op('no-such-command'))
print('retryability ok')
EOF

# 3. The pop rule is terminal-only. Both halves matter: an EXECUTING
#    head must survive, and every terminal head must still pop, or a
#    queue whose head errored blocks forever again.
test 1 -eq "$(grep -c 'state.value in AgentOperation.TERMINAL_STATES' \
    shakenfist/instance.py)" && echo 'terminal-only pop present'

# 4. The two comments survey finding 7 names have been rewritten. Both
#    assert the premise this phase removes, and a stale one here is
#    worse than none: they are what a reader consults to learn the
#    durability rule.
test 0 -eq "$(grep -c 'popped from the instance.s queue as soon as it' \
    shakenfist/daemons/sidechannel/main.py)" \
  && test 0 -eq "$(grep -c 'the executor moved it to EXECUTING or beyond' \
    shakenfist/instance.py)" \
  && echo 'stale durability comments gone'

# 5. The attempt cap is configurable and defaults to 3.
.tox/py3/bin/python -c \
  "from shakenfist.config import config; \
   assert config.AGENT_OPERATION_MAX_ATTEMPTS == 3; print('cap ok')"

# 6. The pre-connection wait is abortable.
test 1 -le "$(sed -n '/console_path = os.path.join/,/Detected console log/p' \
    shakenfist/daemons/sidechannel/main.py | grep -c check_abort_path)" \
  && echo 'pre-connection wait abortable'

# 7. Every attribute write in the object carries a field mask. The
#    two-line form is why this counts calls against masks rather than
#    grepping for a mask on the same line.
test "$(grep -c 'update_agent_operation_attributes(' \
    shakenfist/operations/agentoperation.py)" \
  -eq "$(grep -A1 'update_agent_operation_attributes(' \
    shakenfist/operations/agentoperation.py | grep -c 'fields=')" \
  && echo 'field masks present'

# 8. Full check.
pre-commit run --all-files
```

By inspection, each falsifiable:

- `docs/developer_guide/state_machine.md` shows `executing --> queued`
  and no other page in `docs/` outside `docs/plans/` describes retry,
  because phase 7 owns that story.
- Every terminal outcome reachable from the retry path carries a
  message naming why no retry happened, so an operator reading
  `object_states.message` can tell "attempts exhausted" from "deadline
  passed" from "not retryable" without reading the code -- the same
  standard phase 4 set for its four expiry messages.
- The reaper performs no database read for an instance with an empty
  agent operation queue, which is what keeps it affordable at the top
  of every dispatcher pass.

## Future work

- **`_request_thread_exit()` operates on the wrong dictionary for
  executors.** Survey finding 8. `daemons/sidechannel/main.py:1297-1310`
  joins and deletes `self.monitors[instance_uuid]` however it is
  called, but `_request_all_threads_exit()` (`:1292-1295`) calls it
  for executors too. The executor entry is never removed, the wrong
  thread is joined, the audit event says "monitor", and if the monitor
  loop immediately above already deleted the entry the call raises
  `KeyError` on the daemon's shutdown path -- and there is no
  `try`/`except` around the call site (`:1516`), so the daemon stops
  without completing teardown. Predates this plan entirely,
  introduced in `69f27b7ad` (2024-12-15). Filed as
  [#3931](https://github.com/shakenfist/shakenfist/issues/3931)
  rather than fixed here, because shutdown ordering deserves its own
  change and its own test, and would be invisible inside a commit
  about retry.
- **A retried operation's events do not distinguish attempts.** The
  dispatcher's "dispatching agent operation" event
  (`main.py:1389-1391`) is emitted identically for a first dispatch
  and a third, so reading an instance's event stream shows three
  dispatches with nothing saying they are the same operation retrying.
  The attempt number is on the operation and in the retry audit event
  added by step 5c, so the information exists; joining it to the
  dispatch event is a small improvement worth doing when something
  else touches that call site.

## Back brief

Before implementing, confirm:

1. **Decision 1** -- that terminal-only pop and the reaper ship as one
   commit. Gated: do not start step 5b as two commits. Splitting them
   leaves a tree where a sidechannel restart mid-operation wedges an
   instance's queue permanently, which is a regression against
   today's behaviour rather than an unfinished improvement.
2. **Decision 4** -- that retryability is a property of the whole
   command list. This is the decision most likely to be argued with,
   and it is cheap to agree now and awkward to change after step 5c's
   tests are written around it.
3. **Decision 2** -- that an agent-reported command error never
   retries, so phase 4's `_handle_command_error()` keeps failing the
   operation outright.
4. **Decision 3** -- that a stall which exhausts its attempts expires
   while an executor exit which exhausts its attempts errors, rather
   than both collapsing to one outcome. Cheap to reverse now,
   expensive after phase 6 ships a client that branches on the
   terminal state.
