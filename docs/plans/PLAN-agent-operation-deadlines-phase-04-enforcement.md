# Agent operation deadlines phase 4: enforcement

## Prompt

Plan the next phase of `PLAN-agent-operation-deadlines.md` with the
`next-phase` skill, after phase 3 merged as PR #3883. Phases 2 and 3
stored the numbers and let a client set them; this phase is the one
that makes them bite.

## Planning effort

High. This phase adds a terminal state to a live object type, deletes
the only existing wedge backstop, and puts three new time-based exits
into a daemon whose failure mode is silent. The costly decisions are
about semantics rather than lines of code: what an expired operation
records as its reason, what anchors a deadline for a row that carries
none, and whether a progress stall is the same outcome as a deadline
passing.

## Scope

**In scope.**

- The `expired` terminal state on `AgentOperation`, with the four
  obligations the phase 0 audit enumerated: `state_targets` edges,
  `FINAL_OBJECT_STATES` membership, guarded error writes, and the
  executor's command-abort check.
- Deadline resolution helpers on `AgentOperation`, so the NULL-means-
  server-default rule is written once rather than at four enforcement
  sites.
- Enforcement at dequeue (`Instance.agent_operation_next()`), during
  preflight (`NodeAgentopOp._preflight()`, phase 0 decision 4), and in
  the executor (`SideChannelExecutorJob._execute_inner()`).
- `observe_progress()` in the reply handlers, and persistence of
  `last_progress` with a throttle.
- Deleting `AGENT_OPERATION_EXECUTION_TIMEOUT`
  (`shakenfist/daemons/sidechannel/main.py:56`).
- The `state_machine.md` page, which documents `state_targets` and is
  wrong the moment this phase lands. See decision 9 for why this one
  documentation change does not wait for phase 7.
- Unit tests for each of the above.

**Out of scope.**

- **Retry.** No `EXECUTING -> QUEUED` edge, no attempt counting, no
  partial-result cleanup. Phase 5.
- **The node-local reaper.** The master plan's *Enforcement points*
  section lists it as the third enforcement point, which reads like
  this phase; the phase table assigns it to phase 5 and the phase
  table is right, because a reaper for a dead executor is only useful
  once the queue entry survives execution (phase 5's terminal-only
  pop). This phase writes `last_progress` so phase 5's reaper has
  something to read. Corrected at source.
- **`client-python`.** Phase 6, including making await loops treat
  `expired` as terminal (client-python#363).
- **Release notes, operator and user guide.** Phase 7, which writes
  the whole timing story once.
- **Functional CI coverage.** Phase 7.
- Any ceiling on an explicitly unbounded operation. See decision 6.

## What the survey found

The master plan's design is sound and nothing in it needs reversing.
Its *addresses* are all stale, and four facts it does not state change
what this phase has to do.

**Everything phases 2 and 3 promised is present and unconsumed.** The
`deadline` and `progress_timeout` columns exist with the corrected NULL
semantics (`shakenfist/schema/agentoperation_data.py:75-89`), the
`last_progress` and `attempts` attributes exist
(`shakenfist/schema/agentoperation_attributes.py:52-59`),
`update_agent_operation_attributes` takes a field mask
(`shakenfist/mariadb.py:19108`), all four values are in
`external_view()` (`shakenfist/operations/agentoperation.py:139-152`),
and `agent_operation_timing()` (`shakenfist/external_api/base.py:227`)
converts requests into stored values on all three endpoints.
`AGENT_OPERATION_EXECUTION_TIMEOUT = 900` is intact at
`shakenfist/daemons/sidechannel/main.py:56` and used at 757-762, and
phase 3's "no enforcement consumer" check still returns zero hits.

**Every line number in phase 0 decision 1 has moved**, because phase 1's
handler refactor rewrote the middle of the sidechannel daemon. The
audit's count of five unguarded `state = STATE_ERROR` writes is still
correct; their addresses are now
`shakenfist/daemons/sidechannel/main.py:344` and `:350`
(`PutBlobCommand.dispatch`), `:844` (the `GetException` handler),
`:886` (unknown command), and
`shakenfist/operations/node_aop_op.py:89`. The sixth write, at
`main.py:496`, is already guarded by
`if self.agentop.state.value == AgentOperation.STATE_EXECUTING`. The
command-abort check named as `main.py:869` is now `main.py:910`;
`reap_instance_executors()`, named as `main.py:972`, is now
`main.py:1013`; and `FINAL_OBJECT_STATES` is at
`shakenfist/constants.py:191`, not 190. The master plan's decision 1
text is corrected at source as part of the planning commit.

Four things the plan does not say:

1. **An expired operation cannot carry an `error` message.** The
   `error` setter raises `InvalidStateException` unless the current
   state value ends in `error`
   (`shakenfist/baseobject.py:626-634`), and `expired` does not. Every
   existing failure path in the sidechannel daemon writes `state` then
   `error` as a pair, so the obvious `expired` implementation would
   raise on the second line. The reason has to travel some other way —
   decision 2.

2. **`external_view()` publishes only `state.value`.**
   `BaseExternalView.serialize_state` returns `state.value`
   (`shakenfist/schema/external_view.py:47-50`) and neither `error` nor
   the state message appears in any agent operation response. So a
   client sees `"state": "expired"` and nothing else, exactly as it
   sees `"state": "error"` today, and the reason is only in the event
   stream. This is pre-existing and this phase does not change it, but
   it decides what decision 2 is worth: the audit event is the reason's
   real home, and the state message is for operators reading the
   database.

3. **The executor keeps no reference to the command in flight.**
   `_execute_inner()` does `cmd = self.commands.pop(0)` and then
   `handler = self.command_handlers.get(cmd['command'])`
   (`main.py:875-882`), and the handler goes out of scope at the end of
   the iteration. "Apply the progress timeout while a progress-capable
   command is in flight" therefore needs new state on the job, which
   the plan assumes is already available.

4. **`state_targets` values are inconsistently typed.**
   `BaseOperation.STATE_COMPLETE: (dbo.STATE_DELETED)` and
   `dbo.STATE_ERROR: (dbo.STATE_DELETED)`
   (`shakenfist/operations/agentoperation.py:36-37`) are bare strings,
   not one-tuples. `baseobject.py:587` tests
   `new_value not in self.state_targets.get(orig.value, [])`, so a
   string does substring membership: `'deleted'` is admitted correctly
   by accident, and so would `'delete'` be. Adding an `expired` row to
   the same dict is the moment to make all three one-tuples.

Two smaller confirmations, both of which the plan asserts and which
hold: `last_data` really is refreshed by any socket traffic
(`main.py:775`) and the ping goes out every two seconds
(`main.py:764-766`), so it never ages past ~2 s and is useless as a
progress signal; and `AgentOperation.STATE_*` has a small, enumerable
consumer set — ten sites outside the sidechannel daemon and the tests —
which is what makes adding a state cheap.

One thing to note but not act on. `FINAL_OBJECT_STATES` does not
contain `error`, so errored agent operations are never swept for hard
deletion and leak their `object_states` rows indefinitely — the same
class of leak as issue 3532. Adding `expired` to that list therefore
makes an expired operation reap *better* than an errored one. That is
not this phase's to fix; it is filed as future work below.

## Decisions

1. **`expired` is defined on `AgentOperation`, not `BaseOperation`.**
   `AgentOperation.STATE_EXPIRED = 'expired'`, alongside its
   `state_targets`. The master plan's non-goals scope deadlines to
   agent operations deliberately ("if it proves useful the schema
   pattern can be lifted to `BaseOperation` later"), and putting the
   constant where the only user is keeps that scoping visible. The
   string still has to be added to the cluster-wide
   `FINAL_OBJECT_STATES` in `constants.py`, because that list is keyed
   by state value across every object type; no other object type can
   reach `expired`, so the widening is inert for them.

2. **An expiry records its reason as a state message plus an audit
   event, and never as `.error`.** `AgentOperation.expire(reason)`
   calls `self._state_update(STATE_EXPIRED, message=reason)` and emits
   one `EVENT_TYPE_AUDIT` event against both the operation and the
   instance, mirroring the `add_event_multi` calls the executor
   already makes. The alternative — relaxing the `error` setter to
   accept `expired` — is rejected: that setter is what keeps "this
   object has an error message" and "this object is in an error state"
   in step for every object type in the system, and loosening it
   globally to serve one new state on one object is a bad trade. The
   consequence, from survey finding 2, is that the reason reaches
   clients only through events. That is already true of `error`, so
   this is not a regression, and phase 7 documents where to look.

3. **The fallback anchor for a NULL deadline is
   `self.state.update_time`, not "now".** A NULL row was written by an
   API node that predates this work and carries no receipt timestamp,
   so the master plan says to anchor at dispatch time. Taken literally
   at the dequeue check that means anchoring at the moment of the
   check, and `now + 600 > now` is never expired — a legacy row could
   never expire in the queue at all. `state.update_time` is a
   persisted, node-agnostic timestamp of when the operation entered
   its current state: for a queued operation, when it was queued; for
   an executing one, when it was dispatched, which is exactly what the
   plan asks for. The cost is that a legacy operation's budget resets
   at each transition, so it can consume up to one default deadline
   per state. That is honest — a row with no receipt time has no
   correct answer — and it is still strictly tighter than today's
   unbounded queue time plus a 900 second executor backstop. Rows
   written by a phase 3 API node are unaffected: they carry an
   absolute timestamp and never consult the anchor.

4. **Resolution lives in three helpers on `AgentOperation`, and no
   enforcement site reads the raw columns.**
   `effective_deadline()` returns an absolute timestamp or `None` for
   "no deadline" (`0.0` sentinel); `deadline_passed()` is the boolean
   the four call sites use; `effective_progress_timeout()` returns
   seconds or `None` for disabled. Four sites each re-deriving
   "NULL means the default, `0.0` means none, anything else is the
   value" is four chances to invert a sentinel, and phase 2's own plan
   records that the NULL semantics were got wrong once already in
   prose.

5. **A progress stall and a passed deadline are both `expired`,
   distinguished only by the message and the event.** They are the same
   kind of thing: a timing budget the caller set, exhausted. Splitting
   them — stall as `error`, deadline as `expired` — would mean phase 6's
   await loop has two terminal outcomes to learn instead of one, and
   would assert that a stall is the operation's fault when the common
   cause is a slow or wedged guest. The distinction a caller actually
   needs is in the message: `the operation deadline passed while
   executing` against `no progress from the agent for N seconds`.

6. **Deleting the 900 second constant leaves an explicitly unbounded
   operation genuinely unbounded, and that is correct.** A caller who
   sends `deadline_seconds=0` and `progress_timeout_seconds=0` gets an
   operation nothing will ever time out, which can hold its instance's
   executor slot forever — the #3516 symptom, on request. Phase 3 has
   already published "0 means no wall-clock deadline at all" and "0
   disables the progress timeout" in the API specification, and
   quietly capping either would make the published contract false.
   Every path that does not opt out is bounded: the default is 600
   seconds, an omitted progress timeout is 30 for progress-capable
   commands, and a legacy NULL row falls back per decision 3. This is
   the decision most likely to be argued with; the alternative — an
   `AGENT_OPERATION_MAX_DEADLINE` operator ceiling — is recorded as
   future work rather than smuggled in here, because a ceiling that
   overrides an explicit client request is an operator policy feature
   and deserves its own design.

7. **The five unguarded error writes are converted to an
   `AgentOperation.fail(message)` helper rather than wrapped in five
   inline state checks.** Each site today is the same two lines
   (`state = STATE_ERROR` then `error = ...`), and each needs the same
   new guard (skip when the operation has already reached a terminal
   state). One helper that no-ops from `expired`, `complete`, `error`
   and `deleted` removes the duplication and makes the next such site
   correct by construction. `node_aop_op.py:89`, which sets the state
   with no message at all, gains one.

8. **The progress timeout applies while `not self.ready` and the
   in-flight handler declares `reports_progress`.** The executor gains
   `self.in_flight_handler`, set where the handler is looked up
   (`main.py:882`), and `self._last_progress`, seeded at that same
   point so the window measures time since the command was sent rather
   than since the connection opened. `observe_progress()` updates the
   in-memory value on every call and persists to `last_progress` at
   most every 10 seconds (`PROGRESS_PERSIST_INTERVAL`). The in-memory
   value is what this phase's check reads; the persisted one exists
   only for phase 5's reaper, which is why the throttle is acceptable.
   Hooks go in `_handle_execute_reply`, `_handle_stat_result`,
   `_handle_file_chunk` and the inline `file_chunk_reply` branch, as
   the master plan says. The `execute_reply` hook is inert today
   (`ExecuteCommand.reports_progress` is `False`) and is added anyway,
   so `last_progress` means the same thing in every operation's
   `external_view()` and so the hook is already in place if `execute`
   ever streams output.

9. **`docs/developer_guide/state_machine.md` is updated in this phase,
   not phase 7.** That page is a rendering of `state_targets` — it
   lists the states and draws the transition diagram — so leaving it
   alone means the tree contains a page that contradicts the code for
   three phases. It is also not the "half the feature" problem that
   deferred the release note from phase 3: the state machine is
   complete the moment this phase lands, and phase 5 adds exactly one
   more edge. Everything else stays in phase 7. The master plan's
   phase 7 row is corrected at source to say the state machine page is
   already done.

10. **The 30 second agent-welcome deadline stays.** It guards
    connection establishment, not the operation, and it returns for
    retry rather than failing anything (`main.py:743-751`). Deleting
    it along with the 900 second constant would remove the only bound
    on an executor that connects to an agent which never speaks.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | The `expired` state and the resolution helpers, with no enforcement site calling them yet. In `shakenfist/operations/agentoperation.py`: add `STATE_EXPIRED = 'expired'` to `AgentOperation` (decision 1); add `expired` to `state_targets` as a target of `STATE_INITIAL`, `STATE_PREFLIGHT`, `STATE_QUEUED` and `STATE_EXECUTING`, and add the row `STATE_EXPIRED: (dbo.STATE_DELETED,)`; while in that dict, convert the two bare-string values `BaseOperation.STATE_COMPLETE: (dbo.STATE_DELETED)` and `dbo.STATE_ERROR: (dbo.STATE_DELETED)` (lines 36-37) into one-tuples — they work today only because `baseobject.py:587` does substring membership on a string, which would also admit `'delete'`. Add `'expired'` to `FINAL_OBJECT_STATES` in `shakenfist/constants.py:191` so the hard-delete sweep in `shakenfist/daemons/cluster/scheduled_tasks.py:803` reaps expired operations; no other object type can reach the state, so the widening is inert for them. Then add three resolution helpers and two action helpers, all on `AgentOperation` (this file will need `from shakenfist.config import config` and `import time`, neither of which it imports today). `effective_deadline()`: returns `None` when `self.deadline == 0.0` (the client asked for none); returns `self.deadline` when it is a positive float; returns `self.state.update_time + config.AGENT_OPERATION_DEFAULT_DEADLINE` when it is `None` (decision 3 — the anchor is the current state's transition time, not `time.time()`, because a check anchored at "now" can never fire). `deadline_passed()`: `d = self.effective_deadline(); return d is not None and time.time() > d`. `effective_progress_timeout()`: `None` when `self.progress_timeout == 0.0`, the value when positive, `float(config.AGENT_OPERATION_DEFAULT_PROGRESS_TIMEOUT)` when `None`. Beware `0.0` versus `None` throughout — test `is None` explicitly and never truthiness, exactly as `external_api/base.py:284` does. `expire(reason)`: no-op if the state is already one of complete/error/expired/deleted; otherwise `self._state_update(self.STATE_EXPIRED, message=reason)` and one `add_event(EVENT_TYPE_AUDIT, ...)` carrying the reason. Do **not** set `self.error` — the setter at `shakenfist/baseobject.py:626` raises `InvalidStateException` for any state not ending in `error` (decision 2). `fail(message)`: the same terminal-state no-op guard, then `self.state = dbo.STATE_ERROR` followed by `self.error = message` (decision 7). Add `shakenfist/tests/test_agent_operation_expiry.py` using `MockMariaDB` the way `AgentOperationQueueTestCase` in `shakenfist/tests/test_instance.py:733-775` does, covering: each of the three resolution helpers across all three input values (`None`, `0.0`, positive) with `time.time` patched so the assertions are exact; that `deadline_passed()` is `False` for a `0.0` deadline however old the operation is; that a NULL-deadline operation expires exactly `AGENT_OPERATION_DEFAULT_DEADLINE` after its state transition; that `expire()` from each of the four non-terminal states succeeds and records the reason as the state message; that `expire()` from `complete` is a no-op; that `expired -> deleted` is permitted and `expired -> error` is not; and that `fail()` from `expired` is a no-op rather than an `InvalidStateException`. Commit subject: `Add the expired agent operation state.` |
| 4b | medium | sonnet | none | Route the five unguarded error writes through `fail()`. In `shakenfist/daemons/sidechannel/main.py`, replace the `state = AgentOperation.STATE_ERROR` / `error = ...` pairs at lines 344-346 and 350-352 (`PutBlobCommand.dispatch`, `self.job.agentop`), 844-845 (the `except GetException` branch) and 886-888 (the unknown-command branch) with single `fail(...)` calls carrying the same message. In `shakenfist/operations/node_aop_op.py:89`, replace `aop.state = Instance.STATE_ERROR` with `aop.fail('preflight task raised an exception')` — note that line currently records no message at all, and that `Instance.STATE_ERROR` there is just `dbo.STATE_ERROR` reached through an unrelated class, so the import of `Instance` may become unused; check and remove it if so. Leave `main.py:496` alone: it is inside `if self.agentop.state.value == AgentOperation.STATE_EXECUTING` and is already guarded. Then widen the command-abort check at `main.py:910` from `== AgentOperation.STATE_ERROR` to `in (AgentOperation.STATE_ERROR, AgentOperation.STATE_EXPIRED)`, so an operation expired mid-iteration clears its remaining commands the same way an errored one does. Add tests to `shakenfist/tests/test_daemon_sidechannel_executor.py` — extend the existing `_FakeAgentOp` (line 15) with a `fail()` that records its argument and a state that can be preset — asserting that a `fail()` call from `expired` leaves the state expired and does not raise, and that the command-abort check clears `self.commands` for both terminal states. Commit subject: `Guard agent operation error writes.` |
| 4c | high | opus | none | Dequeue expiry. In `Instance.agent_operation_next()` (`shakenfist/instance.py:2428`), inside the existing `while queue:` loop, after `state = agentop.state.value` and before the `if state == AgentOperation.STATE_QUEUED` branch at line 2470, check `agentop.deadline_passed()`; if it has, call `agentop.expire('the operation deadline passed while queued')`, `queue.pop(0)`, set `changed = True` and `continue`, so the next entry is considered in the same pass. Two constraints. First, only check operations that are actually dispatchable or waiting — a head in `INITIAL` or `PREFLIGHT` is mid-creation and its own enforcement point is step 4d, and a head already in a terminal state must fall through to the existing pop rather than being expired again (`expire()` no-ops there, but the pop is what matters). Second, this method's cheap early-out at line 2450 reads the attribute without the lock and must not change: the deadline check goes inside the locked section only. Update the docstring, which currently explains the pop rule and now also has to say that an expired head is retired here rather than occupying the executor. Extend `AgentOperationQueueTestCase` in `shakenfist/tests/test_instance.py:733` — give `_make_agentop()` an optional `deadline` argument passed through to `AgentOperation.new()` — with tests that: an expired queued head is expired, popped, and the next queued operation returned in the same call; a head whose deadline is in the future is returned untouched; an operation with `deadline=0.0` is never expired however old; two consecutive expired heads are both retired in one call; and a `PREFLIGHT` head with a passed deadline is left alone (it returns `None` today and must continue to). Commit subject: `Expire agent operations at dequeue.` |
| 4d | medium | sonnet | none | Preflight expiry, phase 0 decision 4. In `NodeAgentopOp._preflight()` (`shakenfist/operations/node_aop_op.py:91`), check `aop.deadline_passed()` once on entry, immediately after the existing `if aop.state.value != AgentOperation.STATE_PREFLIGHT: return` guard, and again immediately after each `b.ensure_local()` call at line 103 — that copy is the longest pre-queue delay in the system and is precisely the wait a receipt-anchored deadline exists to count. On expiry call `aop.expire('the operation deadline passed during preflight')` and `return`, following the existing early-return shape at lines 105-108 (the deleted-during-copy case): do **not** set `self.state = NodeAgentopOp.STATE_ERROR`, because the cluster operation did its job correctly and only the agent operation ran out of budget. The check after `ensure_local()` goes before the existing state re-read, since an expired operation is no longer in `PREFLIGHT` and would otherwise be caught by that guard and returned without an explanation. Add `shakenfist/tests/test_operation_node_aop_op.py` if no test file for this operation exists, or extend the existing one, asserting: an already-expired operation entering preflight is expired and never reaches `ensure_local()` (patch `Blob.from_db` and assert not called); an operation whose deadline passes during `ensure_local()` (patch it with a side effect that advances a patched `time.time`) is expired and does not reach `STATE_QUEUED`; and an operation within its deadline still reaches `STATE_QUEUED`. Commit subject: `Expire agent operations during preflight.` |
| 4e | high | opus | none | The executor, and the deletion of the 900 second constant. In `shakenfist/daemons/sidechannel/main.py`, delete `AGENT_OPERATION_EXECUTION_TIMEOUT` (line 56, with its comment block at 49-55) and the check that uses it (lines 753-762). Keep the 30 second welcome deadline immediately above it (decision 10) — it guards connection establishment and returns for retry rather than failing the operation. In `SideChannelExecutorJob.__init__` (line 448) add `self.in_flight_handler = None`, `self._last_progress = None` and `self._last_progress_persisted = 0.0`. Add a module constant `PROGRESS_PERSIST_INTERVAL = 10`. Add `SideChannelExecutorJob.observe_progress()`: set `self._last_progress = time.time()`, and when more than `PROGRESS_PERSIST_INTERVAL` seconds have passed since `self._last_progress_persisted`, write the `last_progress` attribute through `mariadb.update_agent_operation_attributes` with `fields=['last_progress']` — read the current row with the operation's `_attributes()` helper (`shakenfist/operations/agentoperation.py:154`) and build an updated `AgentOperationAttributesData`, mirroring `add_result()` at line 216, and note the field mask is not optional here (see the attribute-field-mask rule in `CLAUDE.md`: an unmasked write would clobber a concurrent `results` update). Call `observe_progress()` from `_handle_execute_reply` (line 518), `_handle_stat_result` (line 610), `_handle_file_chunk` (line 634) and the inline `file_chunk_reply` branch (line 802). Where the command is dispatched (`main.py:875-890`), set `self.in_flight_handler = handler` and seed `self._last_progress = time.time()` at the same point, so the progress window measures time since this command was sent (decision 8). Then replace the deleted 900 second check with two new ones at the top of the loop, both of which `return` after expiring so `execute()`'s finally block sees a terminal state and does not overwrite it with `error`: first, `if self.agentop.deadline_passed(): self.agentop.expire('the operation deadline passed while executing'); return`; second, a progress check that fires only when `self.in_flight_handler is not None and self.in_flight_handler.reports_progress and not self.ready`, whose window is `self.agentop.effective_progress_timeout()` and which does nothing when that is `None` (the client disabled it), expiring with `f'no progress from the agent for {window} seconds'`. Log both at `error` level with the operation and instance fields the job's logger already carries. Do not use `self.last_data` for the progress check under any circumstances: it is refreshed by every `recv()` including the two-second ping reply (`main.py:764-775`), so it never ages and would make the check dead code. Extend `shakenfist/tests/test_daemon_sidechannel_executor.py` with a class covering, against a `_FakeAgentOp` whose `deadline_passed()` and `effective_progress_timeout()` are controllable: that a passed deadline expires the operation and returns; that a stalled progress-capable command expires it after the window; that a stalled command whose handler has `reports_progress = False` does not; that `self.ready` being true suppresses the progress check; that `effective_progress_timeout()` returning `None` suppresses it; that `observe_progress()` moves the in-memory timestamp on every call but persists at most once per `PROGRESS_PERSIST_INTERVAL`; and that the persisting write passes `fields=['last_progress']`. Commit subject: `Enforce agent operation deadlines in the executor.` |
| 4f | medium | sonnet | none | Documentation and closeout. Update the two config option descriptions in `shakenfist/config.py:240-270`, both of which currently end by saying enforcement does not exist yet ("nothing enforces either value until phase 4 ... Until then both exist and only the constant bites") — that sentence is now false and, in the deadline option, so is the claim that `AGENT_OPERATION_EXECUTION_TIMEOUT` still exists. Say instead where each is enforced: the deadline at dequeue, during preflight and in the executor; the progress timeout in the executor while a progress-capable command is in flight. In `docs/developer_guide/state_machine.md`, add `expired` to the Agent Operations state list (a terminal state meaning a caller-set timing budget — the wall-clock deadline or the progress timeout — was exhausted, distinct from `error`, which means the operation itself failed) and add the five new edges to the mermaid diagram: `initial --> expired`, `preflight --> expired`, `queued --> expired`, `executing --> expired`, `expired --> deleted`. Do not touch the operator guide, the user guide or `docs/release_notes/v07-v08.md`: phase 7 writes the timing story once, and this page is the exception only because it is a rendering of `state_targets` (decision 9). Check `docs/developer_guide/api_reference/agentoperations.md` and `.../instances.md` for any statement that an agent operation only ever reaches `complete` or `error`, and correct it if present. Then set phase 4 to `Complete` in the master plan's phase table and link this file, and update `docs/plans/index.md`'s count from `4 of 9` to `5 of 9`. Commit subject: `Document the expired agent operation state.` |

## Corrections applied at source

Made as part of the planning commit, so a later step does not redo
them:

- The master plan's phase 0 decision 1 line numbers are refreshed to
  the post-phase-1 addresses, and a note records that `main.py:496` is
  already guarded.
- The *Enforcement points* section gains a sentence saying which phase
  owns each of the three points, because the reaper reads as phase 4
  scope and is phase 5's.
- The *Enforcement points* section notes that preflight (phase 0
  decision 4) is a fourth point, which the section's "three places"
  never mentioned.
- The phase 7 row records that the state machine page was updated in
  phase 4, so phase 7 does not rewrite it.

## Departures from the plan

Five, all found while implementing.

- **`fail()` records its message on the state as well as in
  `self.error`.** `AgentOperation` never overrides
  `_db_set_attribute()` (`shakenfist/baseobject.py:470` warns and
  discards; only `Instance` overrides it), so every existing
  `agentop.error = ...` write in the tree has reached nothing but a
  warning log and a mutate event. Decision 2 said the reason travels
  as a state message for `expire()` only; it turned out `fail()` needs
  the same treatment for the reason to be readable at all. The
  `self.error` write is kept so the call sites become correct if that
  persistence gap is closed. Recorded as future work below.
- **Two guards were extracted into methods rather than left inline.**
  Steps 4b and 4e as briefed put the command-abort check and the two
  budget checks inside `_execute_inner()`, which is only reachable
  through a vsock connection and cannot be unit tested. They are now
  `_abort_commands_if_terminal()` and `expire_if_out_of_budget()`,
  called from the same places. The first draft of the step 4b tests
  reimplemented the guard in the test and asserted on the
  reimplementation, which proved nothing; that is what prompted the
  extraction.
- **The progress hooks sit below the in-flight guards, not at the top
  of their handlers.** `_handle_stat_result()` and
  `_handle_file_chunk()` both begin by rejecting a reply for a
  transfer which is not in flight. Calling `observe_progress()` above
  that guard counts such a reply as progress, which it is not, and it
  broke the existing `ExecutorGetFileGuardTestCase`.
- **Step 4d also moved the missing-blob path onto `fail()`.**
  `NodeAgentopOp._preflight()` assigned `aop.error` directly from the
  preflight state. The `error` setter refuses that, so the assignment
  raised `InvalidStateException`, `dispatch_task()`'s handler caught
  it, and the message naming the missing blob was discarded. It is a
  sixth instance of the same defect step 4b exists to fix, so it was
  fixed with it.
- **The preflight tests live at
  `shakenfist/tests/operations/test_node_aop_op.py`.** A file of that
  name already exists at `shakenfist/tests/schema/operations/`,
  testing the schema rather than the operation. The module paths
  differ so the two coexist.

## Risks and mitigations

- **A new terminal state reaches a consumer nobody audited.** The
  phase 0 audit covered three repositories and found none, and the
  survey re-confirmed the server-side consumer set is ten sites. The
  residual risk is a client that switches on state strings. Mitigation:
  `expired` behaves for old clients exactly as `error` does today (both
  unrecognised, both terminal), which is client-python#363 and phase
  6's to fix; the management session checks step 4a's diff against a
  fresh `grep -rn 'STATE_COMPLETE\|STATE_ERROR' --include='*.py'`
  restricted to agent operation call sites.
- **The 900 second backstop is deleted before the reaper that replaces
  its dead-process coverage exists.** Between this phase and phase 5, an
  operation whose executor thread dies without running its finally
  block is orphaned in `executing` with no timeout at all — the
  constant at least bounded the wedged-but-alive case. Mitigation: the
  wedged-but-alive case is the one this phase covers *better*, at 30
  seconds instead of 900; the dead-thread case is already covered by
  `execute()`'s finally block (`main.py:483-500`) for everything except
  the daemon dying outright, which the constant never covered either
  because it lived in the dead process's own loop. Net exposure is
  unchanged. The management session verifies this by reading that
  finally block before approving step 4e.
- **`expire()` racing the executor's finally block.** The finally block
  writes `error` when it sees `EXECUTING`; if the loop expires the
  operation and returns, the state is `expired` and the block is
  skipped. This depends on the expiry write committing before the
  return, which it does because `_state_update` is synchronous.
  Mitigation: step 4e's tests assert the finally block leaves an
  expired operation alone, and step 4b's `fail()` guard makes it a
  no-op even if the ordering were ever reversed.
- **A throttled `last_progress` write clobbers a concurrent `results`
  write.** This is exactly the cross-attribute lost update `CLAUDE.md`
  warns about, and both writes happen in the same executor thread on
  the same operation. Mitigation: the field mask is mandatory in the
  brief and is a named test assertion in step 4e; phase 1 added the
  mask parameter for this.
- **Decision 3's anchor gives a legacy row more budget than intended.**
  A NULL-deadline operation can consume one default deadline per state
  transition. Mitigation: accepted and documented; the path exists only
  for rows written before phase 3 and for the length of a rolling
  upgrade, and is still tighter than the status quo it replaces.

## Definition of done

Runnable from the repository root. The python checks need the project
importable, so run them with `.tox/py3/bin/python`.

```sh
# 1. The 900 second constant is gone, along with every live
#    reference to it. The plan files under docs/plans/ are a
#    historical record and are deliberately excluded; today the only
#    two other files are shakenfist/config.py (the deadline option's
#    description names it) and the sidechannel daemon itself, so this
#    passes only when step 4e and step 4f have both landed.
test 0 -eq "$(grep -rl 'AGENT_OPERATION_EXECUTION_TIMEOUT' \
    shakenfist/ docs/ | grep -vc '^docs/plans/')" \
  && echo 'constant removed'

# 2. The expired state exists, is terminal, and is reachable from
#    every non-terminal state but no terminal one.
.tox/py3/bin/python - <<'EOF'
from shakenfist.constants import FINAL_OBJECT_STATES
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.operations.agentoperation import AgentOperation as A

assert A.STATE_EXPIRED == 'expired'
assert 'expired' in FINAL_OBJECT_STATES
for src in (dbo.STATE_INITIAL, A.STATE_PREFLIGHT, A.STATE_QUEUED,
            A.STATE_EXECUTING):
    assert 'expired' in A.state_targets[src], src
assert A.state_targets['expired'] == (dbo.STATE_DELETED,)
for src in (A.STATE_COMPLETE, dbo.STATE_ERROR):
    assert 'expired' not in A.state_targets[src], src
# Survey finding 4: every value is a tuple, not a bare string.
for src, targets in A.state_targets.items():
    assert targets is None or isinstance(targets, tuple), src
print('state machine ok')
EOF

# 3. The three-valued semantics are honoured, including that 0.0 is
#    not falsy-collapsed. This is the check that would have caught
#    the sentinel inversion the plan warns about twice.
.tox/py3/bin/python - <<'EOF'
from unittest import mock
from shakenfist.operations.agentoperation import AgentOperation as A

class Op(A):
    def __init__(self, deadline, progress_timeout, update_time):
        self._d, self._p, self._u = deadline, progress_timeout, update_time
    deadline = property(lambda s: s._d)
    progress_timeout = property(lambda s: s._p)
    state = property(lambda s: mock.Mock(update_time=s._u))

with mock.patch('time.time', return_value=2000.0):
    assert Op(0.0, None, 0.0).effective_deadline() is None
    assert Op(0.0, None, 0.0).deadline_passed() is False
    assert Op(1500.0, None, 0.0).deadline_passed() is True
    assert Op(2500.0, None, 0.0).deadline_passed() is False
    # NULL anchors on the state transition, not on now (decision 3).
    assert Op(None, None, 1000.0).effective_deadline() == 1600.0
    assert Op(None, None, 1000.0).deadline_passed() is True
    assert Op(None, None, 1900.0).deadline_passed() is False
    assert Op(None, 0.0, 0.0).effective_progress_timeout() is None
    assert Op(None, 5.0, 0.0).effective_progress_timeout() == 5.0
    assert Op(None, None, 0.0).effective_progress_timeout() == 30.0
print('sentinels ok')
EOF

# 4. No enforcement site reads the raw columns (decision 4). The
#    helpers are the only readers outside the object itself.
test 0 -eq "$(grep -rnE '\.(deadline|progress_timeout)\b' \
    shakenfist/instance.py shakenfist/daemons/sidechannel/main.py \
    shakenfist/operations/node_aop_op.py \
  | grep -vE 'deadline_passed|effective_deadline|effective_progress_timeout' \
  | wc -l)" && echo 'helpers are the only readers'

# 5. Every error write in the enforcement path goes through fail(),
#    leaving exactly one direct assignment: the already-guarded one
#    in SideChannelExecutorJob.execute()'s finally block, identified
#    by the STATE_EXECUTING test above it rather than by a line
#    number, which step 4e shifts. Six of these exist today.
test 1 -eq "$(grep -rc 'state = AgentOperation.STATE_ERROR\|state = Instance.STATE_ERROR' \
    shakenfist/daemons/sidechannel/main.py \
    shakenfist/operations/node_aop_op.py | cut -d: -f2 \
  | paste -sd+ | bc)" \
  && grep -B 4 'self.agentop.state = AgentOperation.STATE_ERROR' \
       shakenfist/daemons/sidechannel/main.py | grep -q 'STATE_EXECUTING' \
  && echo 'error writes guarded'

# 6. The config descriptions no longer promise enforcement is coming.
test 0 -eq "$(grep -c 'until phase 4' shakenfist/config.py)" \
  && echo 'config descriptions current'

# 7. Full check.
pre-commit run --all-files
```

By inspection, each falsifiable:

- The state machine page lists `expired` and its diagram has all five
  new edges, and no other page in `docs/` says an agent operation ends
  only in `complete` or `error`.
- The one-sentence meaning of `expired` is written the same way in the
  state machine page, `AgentOperation`'s docstring or comment, and the
  two config option descriptions — no page contradicts another.
- Every enforcement site calls `expire()` with a distinct message
  naming which budget was exhausted and where, so an operator reading
  `object_states.message` can tell dequeue from preflight from
  deadline from stall without consulting the code.
- `observe_progress()` is called from exactly four reply sites, and
  from nowhere that a ping reply reaches.

## Future work

- **`AgentOperation` attribute writes go nowhere.** Found while
  implementing step 4a. `_db_set_attribute()` is overridden only by
  `Instance` (`shakenfist/instance.py:477`); the base implementation
  (`shakenfist/baseobject.py:470`) logs a warning and discards the
  value for every other object type that uses it. For agent
  operations the only user is the `error` attribute, so every
  `agentop.error = ...` in the tree has been writing to nothing. The
  visible consequence is small, because `external_view()` does not
  publish `error` either, but it means an operator cannot read why an
  operation failed from the object. This phase works around it by
  recording the reason on the state, which does persist. Fixing it
  properly means deciding whether agent operations get a generic
  attributes path or whether `error` becomes a typed column, which is
  a schema question and its own change. Worth an issue.
- **Errored agent operations still leak `object_states` rows.**
  `FINAL_OBJECT_STATES` (`shakenfist/constants.py:191`) contains
  `deleted`, `complete` and `abort` but not `error`, so the hard-delete
  sweep never reaps an errored object of any type. After this phase an
  expired agent operation is reaped and an errored one is not, which is
  backwards. This is the same class of leak as issue 3532 and is
  cluster-wide rather than agent-operation-shaped, so it wants its own
  issue and its own reasoning about which object types can safely be
  swept out of `error`. File it during step 4f; do not widen the list
  here.
- **No operator ceiling on an explicitly unbounded operation.**
  Decision 6. If deployments turn out to need one, an
  `AGENT_OPERATION_MAX_DEADLINE` clamping the client's request is the
  shape, and it needs a decision about whether exceeding it is a 400 or
  a silent clamp — the same question phase 3's decision 3 answered for
  the published minimum.
- **The expiry reason is only visible in events.** Survey finding 2:
  `external_view()` publishes `state.value` and nothing else, so a
  client sees `expired` with no message. This is pre-existing behaviour
  for `error` too. If phase 6 finds the await loop wants the reason,
  adding a `state_message` field to the external view is additive and
  cheap, but it is a cross-object-type change to `BaseExternalView` and
  belongs in its own change.
- **The pop rule is described two ways.** `agent_operation_next()`'s
  docstring (`shakenfist/instance.py:2429-2446`) says the entry stays
  until the operation has "provably left the QUEUED state"; the
  executor's finally-block comment (`main.py:483-484`) says it is
  "popped from the instance's queue as soon as it reaches EXECUTING".
  Both describe the same behaviour from different ends and the second
  is misleading. Phase 5 rewrites this rule outright, so it is left
  alone here rather than being corrected twice.

## Back brief

Before implementing, confirm:

1. **Decision 6** — that deleting the 900 second constant leaves an
   operation created with `deadline_seconds=0` and
   `progress_timeout_seconds=0` genuinely unbounded, holding its
   instance's executor slot indefinitely. This is a deliberate
   regression in the worst case, taken because phase 3 has already
   published those sentinels as meaning exactly that. Gated: do not
   start step 4e until this is agreed, because reinstating a backstop
   afterwards means changing published API semantics.
2. **Decision 5** — that a progress stall and a passed deadline are
   the same terminal state, distinguished only by message. Cheap to
   reverse now, expensive after phase 6 ships a client that recognises
   the set of terminal states.
3. **Decision 3** — the fallback anchor for a NULL deadline, and its
   consequence that a legacy operation's budget resets at each state
   transition.
4. **Decision 9** — that the state machine page is updated here rather
   than in phase 7.
