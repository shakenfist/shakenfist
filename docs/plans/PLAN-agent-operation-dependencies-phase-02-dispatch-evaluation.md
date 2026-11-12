# Dependency-aware agent operations phase 2: dispatch evaluation

## Prompt

Plan the next phase of `PLAN-agent-operation-dependencies.md` after
phase 1 landed, following the `next-phase` skill: verify phase 1 is
genuinely complete, survey the tree for the staleness in the master
plan's phase 2 section, and write this plan. Implementation is a
separate ask.

Planning effort: **high**. This phase turns on correctness questions
that cannot be mechanically derived -- what an absent dependency
means, whether a settle delay counts inside the wait budget, and
where in a locked dispatch pass a state write is safe -- and it adds
the first code that moves an agent operation to a terminal state
nothing writes today.

## Scope

In scope:

- The dependency evaluator in the sidechannel dispatcher, between
  `Instance.agent_operation_next()` and `start_instance_executor()`
  (master plan decision 8), covering same-instance and cross-instance
  edges alike (decision 10).
- The `depends_on` cascade to `abort`, writing the
  `dependency_failure` attribute phase 1 added, and exposing it on
  `external_view()` (decision 7).
- The `runs_after` ordering wait, with the cluster side's
  warn-and-continue asymmetry preserved.
- The per-edge settle delay.
- The dependency-wait budget, its configuration option, and the
  attribute that records when a wait began (decision 2).
- The in-process terminal-dependency cache, keyed to the dispatcher
  generation (decision 8).
- One new attributes column and its migration, which the master plan
  did not anticipate -- see *What the survey found*.
- Documentation of the two ambiguities decisions 8 and 9 require to be
  written down rather than fixed.

Out of scope, explicitly:

- **Mixed-type edges** with cluster operations. Declined by master
  plan decision 4, kept out by construction by decision 5, filed as
  [#4824](https://github.com/shakenfist/shakenfist/issues/4824).
- **Agent-side cancellation.** No `executing -> abort` edge is added;
  phase 1 deliberately omitted it and this phase does not revisit it.
  A dependency that fails while its dependent is already executing
  does nothing.
- **Client-side chain helpers and the await UX.** Phase 3, in
  `client-python`.
- **The developer-guide page** for the whole model. Phase 4.
- **Re-deriving the database load budget.** See survey finding 1: the
  reads this phase adds fall in a pair the project has deliberately
  excluded from the budget, so there is no coefficient to amend.
- **Fixing the nine open-coded namespace ownership predicates** found
  during phase 1's review and filed as
  [#4887](https://github.com/shakenfist/shakenfist/issues/4887). This
  phase's API-layer work is confined to one bound.

## What the survey found

The master plan's phase 2 row and decisions 2, 8 and 9 were written
during phase 0, before phase 1 executed. Three of their factual
claims are wrong, and all three change what this phase has to do.
Each is corrected at source in the master plan as part of this
plan's registration commit, so a later reader does not trip over
them.

### 1. The budget term named in decision 8 does not describe this phase's reads

Decision 8 instructs that "the `(GetInstanceAttributes, sidechannel)`
entry in `shakenfist/data/database_load_budget.yaml` is amended in
the same phase as the code, as a code-derived term."

That entry exists (`database_load_budget.yaml:310-325`) and its note
is explicit about what it covers: `agent_operation_next()`'s uncached
instance-attribute read, once per `DISPATCH_CHECK_INTERVAL` per
agent-ready instance. This phase adds no instance-attribute reads at
all. Decision 9 requires the evaluator to read a dependency's **state
by UUID** without hydrating the object, and `AgentOperation.state` is
a property over a `GetObjectState` round trip
(`shakenfist/daemons/queues/workitem.py:127-132` records exactly
that, and the same comment appears at
`shakenfist/daemons/sidechannel/main.py:1179`).

The pair that grows is therefore `(GetObjectState, sidechannel)`, and
it has **no entry in the budget file** -- deliberately. It is a member
of `HARNESS_DRIVEN_PAIRS` in
`shakenfist/deploy/shakenfist_ci/load_budget.py:389`, whose preceding
comment gives the reason and states the cost in terms this phase has
to accept verbatim:

> The model has no term for load which scales with in-flight
> operations: an instance doing no agent work costs nothing here and
> one streaming a large put-blob costs the operation's whole
> lifecycle, so a `per_instance_qps` coefficient would just encode the
> rate CI's tests happen to run at, which is the hand-fitting the yaml
> header forbids. [...] this check can no longer see a new fixed-rate
> poll which reads object states [...] from the sidechannel daemon.

So there is nothing to amend and nothing to add: inventing a
coefficient for a pair the file excludes on principle would be the
hand-fitting the header forbids, and amending the
`GetInstanceAttributes` term would attribute state reads to a term
about attribute reads. What this phase owes instead is a sentence in
that comment naming the evaluator as a second producer of those
reads, so the next person to ask why the pair is unbudgeted finds
both answers in one place. That is step 2f.

### 2. This phase is not schema-free

The master plan's phase 2 row says the `dependency_failure` column
"itself lands in phase 1, which is the schema phase, so this phase
writes and exposes a column which already exists". That is true of
`dependency_failure` and it is the whole of what phase 1 landed:
`shakenfist/schema/agentoperation_attributes.py` has exactly
`uuid`, `results`, `last_progress`, `attempts`, `expiry_reason` and
`dependency_failure`.

But decision 2 requires the wait budget to be "wall-clock from the
first blocked evaluation, recorded as an operation attribute on first
block, rather than a count of dispatcher passes". There is no such
attribute. Phase 2 therefore carries a schema change after all: one
nullable float column, `AGENT_OPERATION_ATTRIBUTES_VERSION` 5 -> 6, a
migration block, a proto field with explicit presence, and the direct
and gRPC accessors -- the same seven-layer traverse phase 1 did three
times. It is the single largest piece of unanticipated work in this
phase and it is step 2a, ahead of the evaluator that depends on it.

### 3. Phase 1 published a settle ceiling this phase cannot honour

Phase 1 bounds `settle_seconds` by `config.AGENT_OPERATION_MAX_DEADLINE`
at `shakenfist/external_api/base.py:701-709`, with a comment
explaining that the ceiling belongs in the API layer rather than on
the model because it is a deployment policy value.
`AGENT_OPERATION_MAX_DEADLINE` defaults to 86400
(`shakenfist/config.py:460`).

Decision 2 sets the dependency-wait budget to 300 seconds. If the
settle delay counts inside that budget -- and decision 2 says the
budget is wall-clock from the first blocked evaluation, which a
settle wait plainly is -- then **every `settle_seconds` above 300 is
unsatisfiable**: the operation will always be aborted by the budget
before the settle elapses. The API accepts such a value today and the
OpenAPI document publishes `maximum: 86400` for it.

Nothing is broken yet, because no evaluator reads the field. Deciding
what to do about it before an evaluator exists is the point of
finding it now; see decision 4 below.

### What the survey confirmed

Everything else in the phase 2 section holds, and three claims worth
checking are exactly right:

- The dispatcher seam is where decision 8 says. `agentop =
  inst.agent_operation_next()` is at
  `shakenfist/daemons/sidechannel/main.py:2092` and
  `self.start_instance_executor(...)` at `:2111`, with a dispatcher
  generation check and one audit event between them.
- The sidechannel has **no** defer machinery: `grep -c defer
  shakenfist/daemons/sidechannel/main.py` returns 0, so decision 8's
  "a blocked operation simply stays queued" is free rather than
  something to build.
- `dispatcher_generation` exists and is already used for exactly this
  kind of liveness reasoning (seven uses in that file), so the
  terminal-dependency cache has a natural lifetime.
- `abort` is in `constants.FINAL_OBJECT_STATES`
  (`shakenfist/constants.py:204-209`), so decision 3's sweep claim
  holds, and `CLEANER_DELAY` is 3600 (`shakenfist/config.py:873`),
  which decision 5 below turns into an argument.

## Decisions

1. **The evaluator runs after the generation check, not before it.**
   The seam decision 8 names contains three things in order: the
   generation check, an audit event, and `start_instance_executor()`.
   Evaluation goes between the generation check and the audit event.

   This matters because evaluation **writes**: a cascade sets a
   terminal state and a first block writes an attribute. A pass that
   has been superseded must not write either, for the same reason it
   must not dispatch -- the comment at `:2095-2098` says the
   operation stays at the head of the queue for the new generation,
   and a cascade abort performed by a dead generation would be a
   state write with no dispatcher accountable for it. Putting
   evaluation before the check would also emit "dispatching agent
   operation" for an operation that was then not dispatched.

2. **An absent `depends_on` dependency aborts the dependent.** This
   is decision 9's ambiguity, and it needs an answer rather than only
   a caveat, because the evaluator has to do something.

   Submission-time validation (master plan decision 6) guarantees the
   dependency existed when the edge was declared. So absence at
   evaluation has exactly two causes: a user deleted it, or the
   hard-delete sweep took it `CLEANER_DELAY` (3600 seconds) after it
   reached a final state. Within the 300-second wait budget the second
   cause is impossible, so an absence observed *during a wait* is a
   deletion. An absence observed on the **first** evaluation is not
   bounded that way, because an operation's queue wait is bounded by
   its deadline, which may be 86400 seconds.

   Aborting is right in both cases. A deleted dependency is a
   fate-sharing failure by any reading. A swept dependency is
   genuinely ambiguous -- `complete`, `abort` and `expired` are all
   swept while `error` never is (issue #3922) -- and aborting
   mis-reports a success as a failure in the one case where the
   dependent waited longer than the sweep interval to reach the head
   of its queue. Proceeding would mis-report a failure as a success,
   which is worse for a fate-sharing edge, and it would make
   `depends_on` silently weaker the longer a queue is. The cost is
   documented in step 2g and the mitigation is a caller-side one: keep
   a dependent's deadline under `CLEANER_DELAY`.

   `dependency_failure` records `state: 'missing'` for this case, which
   is not an `AgentOperation` state value and is deliberately
   distinguishable from every real one.

3. **A missing `runs_after` dependency warns and proceeds, and an
   exhausted budget on a `runs_after` edge also proceeds.** This
   mirrors `shakenfist/daemons/queues/workitem.py:175-210` exactly,
   including its reasoning: `runs_after` is an ordering hint, and the
   cluster side already decided that a missing one must not make an
   object unmanageable. An operation that gave up waiting for a
   `runs_after` edge runs, and says so in an audit event. It does not
   write `dependency_failure`, which is reserved for the fate-sharing
   cascade.

   The asymmetry is the single most likely thing for an implementer to
   flatten, so the step brief says it twice and the definition of done
   asserts it with a test per branch.

4. **The settle ceiling is lowered to the dependency-wait budget, and
   the published maximum changes with it.** Survey finding 3 leaves
   three options: accept unsatisfiable values, extend the budget by the
   declared settle, or refuse a settle the budget cannot honour.

   Extending the budget is excluded by decision 2's own argument: it
   was chosen specifically so that one caller cannot stall an
   instance's queue for a day, and a settle of 86400 would restore
   exactly that. Accepting unsatisfiable values means the API's 400s
   stop being the place a caller learns their request cannot work, and
   the operation fails 300 seconds later with an abort that reads like
   a dependency problem rather than a parameter problem.

   So `settle_seconds` is bounded by
   `AGENT_OPERATION_DEPENDENCY_WAIT` (decision 5) instead of
   `AGENT_OPERATION_MAX_DEADLINE`, in the helper at
   `shakenfist/external_api/base.py:701`, and
   `AGENTDEPENDENCYSPEC_SCHEMA`'s `maximum` follows. **This is the
   decision most likely to be argued with**, because it tightens a
   published bound one phase after publishing it: a request with
   `settle_seconds: 600` is accepted on `develop` today and refused
   after this phase. The counter-argument is that nothing can have
   depended on the looser bound, since no deployed release contains
   phase 1 and no evaluator honours the field, so the window in which
   the bound meant anything has not opened. A reviewer who disagrees
   should say so at the back brief, because reversing it after the
   evaluator ships is a real compatibility break rather than a
   correction.

5. **The budget is one configuration option,
   `AGENT_OPERATION_DEPENDENCY_WAIT`, defaulting to 300.** Named for
   what it bounds rather than for the feature, in the manner of
   `AGENT_OPERATION_MAX_DEADLINE`. Its description states that it
   bounds a blocked head of an instance queue, that it is wall-clock
   from the first blocked evaluation and not a count of passes, that
   it also caps a per-edge settle delay (decision 4), and that
   raising it raises how long one caller's blocked chain can stall
   another's operations against the same instance -- which is
   decision 1's accepted cost, and the operator reading this option is
   the person who gets to re-take that trade.

6. **The settle delay is measured from the dependency's own
   completion, and the evaluator does not hydrate the dependency to
   find it.** Decision 9 forbids hydration, and the state row carries
   no timestamp the evaluator can read without one.

   So the settle clock starts at the first evaluation that observes
   the dependency terminal, recorded in the same attribute decision 2
   uses for the wait start. The consequence, which step 2g documents:
   a settle delay is "this many seconds after we noticed it finished",
   which is up to one `DISPATCH_CHECK_INTERVAL` (five seconds) later
   than the dependency actually finished. Phase 1's API documentation
   says "satisfied this many seconds after the dependency completed",
   which is now imprecise by up to five seconds and is corrected in
   step 2g rather than left to be found later.

7. **The terminal-dependency cache is a plain dict on the dispatcher
   instance, cleared whenever the generation changes, and bounded.**
   Terminal is absorbing, so a dependency observed terminal can never
   become non-terminal and the cache can never serve a stale answer in
   the direction that matters. It is keyed by operation UUID and holds
   the state string. It is cleared in `start_dispatcher()` alongside
   the generation bump, so a replacement thread starts cold, and it is
   capped at 4096 entries with the oldest evicted -- not because
   memory is tight but because an unbounded dict in a daemon that runs
   for weeks is the kind of thing a push audit finds.

   A cache **miss** is not cached: a dependency observed
   non-terminal is re-read every pass, which is the whole point.

8. **`dependency_failure` goes on `external_view()` in this phase, and
   phase 1's deliberate omission is reversed by design rather than by
   accident.** Phase 1's definition of done asserted that
   `dependency_failure` appears nowhere in
   `shakenfist/operations/agentoperation.py`, precisely so that the
   field would not be published before anything wrote it. That
   assertion is now satisfied-and-retired: step 2d adds the property
   and the view entry, and the phase 1 criterion is not re-run.
   Whoever reviews this phase should expect that grep to return
   non-zero and should not read it as a regression.

   The shape is `expiry_reason`'s: a read-only property over the
   attributes row (`shakenfist/operations/agentoperation.py:544`) and
   one key in the view dict (`:278`). Note that an instance's
   `external_view()` embeds each queued operation's whole view, so
   this field becomes visible on the instance endpoint too.

9. **No new capability token.** Phase 1's
   `agentoperation-dependencies` token already advertises the two
   parameters, and master plan decision 3 makes that same token the
   thing which makes `abort` safe for a client polling its own
   operations. A second token for "and now they are evaluated" would
   advertise a behaviour change behind a parameter whose presence
   already implies it, and would give a client two things to check for
   one feature. The token's comment is extended to say that evaluation
   landed, so a reader of `app.py` is not left thinking the parameters
   are still inert.

10. **The evaluator is a method on the dispatcher, not on
    `AgentOperation`.** The cluster side puts its walk in the work
    item (`daemons/queues/workitem.py:143`) rather than on the
    operation, and the cascade helpers it calls
    (`abort_for_unsuitable_dependency`, `abandon_dependency_wait`,
    `cancel_for_missing_dependency`) live on `BaseOperation`. This
    phase follows that split: the walk is
    `SideChannelDaemon._dependencies_met(inst, agentop)` returning a
    verdict, and the two state writes are methods on `AgentOperation`
    beside the `expire_if_out_of_budget()` precedent. That keeps the
    state transition and its attribute write in the class that owns
    the state machine, where the `state_targets` guard lives, and
    keeps the per-pass policy in the daemon that owns the pass.

11. **No retry interaction.** `AGENT_OPERATION_MAX_ATTEMPTS` and the
    head-of-queue `EXECUTING -> QUEUED` retry from the deadlines plan
    are not consulted: a cascade-aborted operation never executed, so
    it has no attempt to retry, and `abort` is terminal so the retry
    path cannot reach it. Stated because an implementer reading
    `attempts` next to `dependency_failure` in the attributes model
    will wonder.

## Step plan

Steps run **in sequence**. Every step but 2f and 2g writes to the
shared worktree, 2a is a hard prerequisite of 2b and 2c, and 2e's
tests exercise what 2b, 2c and 2d build. 2f and 2g could run beside
2e but are cheap, so the simplicity of one order wins.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | **The wait-start attribute column, all seven layers.** Read this plan's survey finding 2 and decision 2 first, then read phase 1's step 1a and 1b commits (`git log --oneline develop -- shakenfist/schema/agentoperation_attributes.py`) -- they are the shape of this one, for the same table. In `shakenfist/schema/agentoperation_attributes.py` add `dependency_wait_started: Optional[float] = None` after `dependency_failure`, with a comment saying: it is the wall-clock time of the first dispatcher pass which found this operation blocked on a dependency, it is the anchor for `AGENT_OPERATION_DEPENDENCY_WAIT`, it is written once and never updated (a second write would restart the budget and defeat it), it is NULL for every operation which was never blocked, and it is deliberately not on `external_view()` -- a caller does not need the anchor, only the outcome. In `shakenfist/mariadb.py`: bump `AGENT_OPERATION_ATTRIBUTES_VERSION` 5 -> 6 at `:494`; extend the existing `if current_ver < AGENT_OPERATION_ATTRIBUTES_VERSION:` block in `_ensure_agent_operation_attributes_schema` (`:19964`) by adding `'dependency_wait_started DOUBLE NULL'` to the column tuple that already carries `'dependency_failure LONGTEXT NULL'` at `:20028` -- **one block whose ALTER list carries every column ever added**, which is this repository's ladder convention, so do not add a second block and do not create a second version gate; `ADD COLUMN IF NOT EXISTS` makes the re-run harmless for a database already at 5. Match what `create_all` produces for an `Optional[float]`: read `shakenfist/schema/sqlalchemy.py` and confirm the type and that `nullable = is_optional and not is_pk` gives NULL, rather than assuming `DOUBLE` is right. Then the direct layer, which you find by grep rather than by line number: `_direct_create_agent_operation_attributes`, `_direct_get_agent_operation_attributes`, and `_agent_operation_attributes_column_values` -- the last is the field mask's vocabulary and a field missing from it cannot be written at all, which is the one failure in this step that no unit test will show you. In `protos/database.proto`, add `optional double dependency_wait_started = 7;` to `AgentOperationAttributesProto` (`:2352`, field 6 is the highest used) with a comment in the style of the presence comment at `:2348`: presence because absent means SQL NULL, meaning never blocked, which is different from blocked at epoch. Regenerate with `tox -e genprotos` and commit the regenerated `shakenfist/protos/` files in the same commit; never invoke `grpc_tools.protoc` directly. Carry it over the boundary in `_grpc_create_agent_operation_attributes`, `_grpc_get_agent_operation_attributes` and `_grpc_update_agent_operation_attributes` in `mariadb.py`, and `_agentop_attrs_from_proto` / `_agentop_attrs_to_proto` in `shakenfist/daemons/database/main.py`, copying the `HasField` idiom the neighbouring `dependency_failure` uses -- and remember a proto3 `optional` field cannot be assigned `None`, so assign only when the value is not None. Extend `shakenfist/tests/test_mariadb_agent_operations.py` (25 tests today) with both halves of the boundary for this field: a value survives the round trip, `None` stays `None`, `0.0` is not confused with `None` (the presence test that the `attempts`-style zero case exists for), and the new field is not transposed with `dependency_failure`. Extend the live migration module `shakenfist/tests/test_mariadb_agent_operations_live.py` the way phase 1's step 1f did: rewind to version 5 by dropping only this column, insert a row, re-run `ensure_schema()`, and assert the column's type, nullability **and collation** equal what `create_all` produces on a fresh database -- collation is load-bearing because `DATA_TYPE`, `COLUMN_TYPE` and `IS_NULLABLE` cannot distinguish MariaDB's `JSON` from `LONGTEXT`, which phase 1 established against a live server. Touch no evaluator, no config option and no API. Commit subject: `Record when a dependency wait began.` |
| 2b | high | opus | none | **The configuration option and the two state writers.** Read decisions 2, 3, 4, 5, 10 and 11 of this plan, and master plan decisions 2, 3, 7 and 9. In `shakenfist/config.py`, add `AGENT_OPERATION_DEPENDENCY_WAIT: int = Field(300, ge=1, ...)` beside the other `AGENT_OPERATION_*` options (`:378-479`), with the description decision 5 specifies -- all four facts, including that raising it raises how long one caller's blocked chain stalls another caller's operations against the same instance. Follow the surrounding options' description style, which is prose about consequences rather than a restatement of the name. In `shakenfist/operations/agentoperation.py`, add two methods beside `expire_if_out_of_budget()` (`:442-470`), which is the precedent for both -- read it first, including how it sets state, writes its reason attribute, adds its event and returns: (1) `cascade_abort_for_dependency(self, dep, dep_state)`, which sets `dependency_failure` to `{'op_type': dep['op_type'], 'op_uuid': dep['op_uuid'], 'state': dep_state}`, persists it with the same single-field `fields=[...]` update `expiry_reason` uses, assigns `self.state = BaseOperation.STATE_ABORT`, and adds an audit event naming the dependency and its state. Order matters: write the attribute **before** the state, because `delete()` removes attribute rows before assigning a state and a reader who sees `abort` must already be able to see why -- say so in a comment. (2) `note_dependency_wait_started(self)`, which writes `dependency_wait_started` only if it is currently None and returns the anchor either way, so the budget cannot be restarted by a second blocked pass; make that idempotence a property of the method rather than something the caller remembers. Add a `dependency_failure` read-only property in the attributes block beside `expiry_reason` (`:544`), and add `'dependency_failure': attrs.dependency_failure` to `external_view()` beside `'expiry_reason'` (`:278`) -- see decision 8, and do not add a second attributes read, the row is already in `attrs`. Do **not** add `dependency_wait_started` to the view. Verify by reading `state_targets` that `queued -> abort` is permitted, so the assignment passes the guard, and say in your report what the guard does for an operation whose state has changed under you between the read and the write. Unit tests in `shakenfist/tests/` beside the existing agent operation tests: a cascade writes all three keys and moves the state to `abort`; a cascade on an operation already `complete` is refused by the state guard rather than silently corrupting it; `note_dependency_wait_started()` called twice keeps the first anchor; `external_view()` carries `dependency_failure` as None for an untouched operation. Add no evaluator and no dispatcher change -- that is 2c. Commit subject: `Add the dependency cascade and its budget option.` |
| 2c | high | opus | none | **The evaluator in the dispatcher.** Read decisions 1, 2, 3, 6, 7 and 10 of this plan, then read `shakenfist/daemons/queues/workitem.py:143-215` in full -- it is the cluster-side walk this mirrors, and the `depends_on` / `runs_after` asymmetry in it is deliberate and must survive. Then read `shakenfist/daemons/sidechannel/main.py:2060-2125` so you can see the pass you are editing. Add `_dependencies_met(self, inst, agentop)` to the sidechannel daemon class, returning a two-tuple of (may dispatch, reason-for-the-log). Walk `agentop.depends_on` first: for each edge read the dependency's state **by UUID without hydrating the object** -- master plan decision 9, because `AgentOperation.delete()` removes rows before assigning `deleted` and `from_db()` therefore returns nothing for a user-deleted dependency, which a hydrating evaluator would misclassify; use the same `GetObjectState` path `AgentOperation.state` uses and consult the terminal cache described below first. An absent dependency, or one in `error`, `abort`, `expired` or `deleted`, cascades: call `agentop.cascade_abort_for_dependency(dep, state_or_the_string_missing)` and return "do not dispatch" -- see decision 2 for why absence aborts and for the `'missing'` sentinel. A dependency in `initial`, `preflight`, `queued` or `executing` blocks: call `agentop.note_dependency_wait_started()`, and if `time.time() - anchor > config.AGENT_OPERATION_DEPENDENCY_WAIT` cascade with the dependency's live state instead, which is the `abandon_dependency_wait()` analogue (`shakenfist/operations/baseoperation.py:860`); otherwise return "do not dispatch" with no write. A dependency that is `complete` and has a `settle_seconds` is satisfied only once the settle has elapsed, measured from the anchor per decision 6, and a settle wait that pushes past the budget cascades like any other exhausted wait. Then walk `runs_after` with the **opposite** disposition, mirroring `workitem.py:175-210`: an absent dependency adds a warning audit event and **continues to the next edge**; a non-terminal one blocks exactly as above, except that an exhausted budget adds a warning event and **permits dispatch** rather than cascading. `runs_after` never writes `dependency_failure`. Say in your report, in one sentence each, what your code does for the four cases that distinguish the two lists, because flattening the asymmetry is the predictable failure here. The terminal cache is decision 7: a plain dict attribute on the daemon, cleared in `start_dispatcher()` where the generation is bumped, capped at 4096 with oldest-evicted, storing only terminal states, never caching a non-terminal observation. An operation whose `depends_on` and `runs_after` are both empty must read **nothing** -- make that the first line of the method and assert it in a test, because it is the cost control master plan decision 8 promises and it is one `if` away from being lost. Wire it into the pass between the generation check at `:2093-2100` and the `inst.add_event(... 'dispatching agent operation' ...)` at `:2102` -- decision 1 says why that exact position, and the two reasons are that a superseded generation must not write state and that the event must not claim a dispatch which did not happen. Log a blocked operation at debug, not info: this runs once per instance per five seconds. Tests in a new `shakenfist/tests/test_sidechannel_dependencies.py`, driving `_dependencies_met` directly with mocked state reads rather than standing up a dispatcher: both lists empty issues zero reads; a `depends_on` on a `complete` dependency dispatches; on an `error` dependency cascades and records the state; on a missing dependency cascades with `'missing'`; on an `executing` dependency blocks without writing an anchor twice; a blocked wait past the budget cascades; a `settle_seconds` of 30 blocks at 10 seconds past the anchor and dispatches at 31; a `runs_after` on a missing dependency dispatches with a warning event; a `runs_after` past the budget dispatches with a warning event and no `dependency_failure`; and a second observation of a terminal dependency issues no second read. Commit subject: `Evaluate agent operation dependencies at dispatch.` |
| 2d | high | opus | none | **Prove the guards with mutation testing.** Read `tools/mutate-agent-dependencies.py`, which phase 1 committed and which this step extends rather than replaces -- its header warns that it edits the tree in place and must not run concurrently with anything else in the worktree, and that warning is load-bearing: phase 1 lost a bound check by starting it beside `pre-commit`, and the test that would have caught the loss was the one the mutant disabled. Add one mutant for each property this phase's tests claim, naming the test that must fail: the `depends_on` cascade on a failed dependency (invert the terminal-state membership test), the `'missing'` sentinel (return a real state instead), the budget comparison (flip `>` to `<`), the settle comparison, the `runs_after` warn-and-continue on a missing edge (make it cascade), the `runs_after` budget branch (make it cascade), the empty-lists early return (remove it, which must fail the zero-reads test), the anchor idempotence (let the second write through), the cascade's attribute-before-state order (swap them), and the terminal cache's refusal to cache a non-terminal state. Run the whole set **serially**, each mutant under `timeout`, counting a timed-out mutant as killed, with `PYTHONDONTWRITEBYTECODE=1` -- a same-size mutant restored within a second otherwise leaves live bytecode. Restore from the script's own pristine copies, never with `git checkout`. Before you report, grep every touched file for the mutation markers and confirm each guard is present **by name**: do not trust a green run, because the disabled guard's test is the one that goes quiet. Report the kill count, the per-mutant test names, and the restoration check for each file. If a mutant survives, the finding is a missing test, so write it and re-run rather than deleting the mutant. Commit subject: `Mutation test the dependency evaluator.` |
| 2e | medium | sonnet | none | **The load budget comment, and nothing else in that file.** Read survey finding 1 of this plan in full first, then the comment above `HARNESS_DRIVEN_PAIRS` in `shakenfist/deploy/shakenfist_ci/load_budget.py:360-389`. Extend the paragraph that names what produces `('GetObjectState', 'sidechannel')` so it also names the dependency evaluator in `shakenfist/daemons/sidechannel/main.py`, in that comment's own register: a named loop which produces the reads, and the reason the budget is the wrong home for them. The reason is the one already written there and must not be re-argued: the model has no term for load which scales with in-flight operations, and an evaluator reads once per edge per blocked pass, which scales with how many operations declare edges rather than with instances or nodes. Say explicitly that an operation declaring no edges reads nothing, so the pair's floor is unchanged by this phase. Do **not** add an entry to `shakenfist/data/database_load_budget.yaml`, do not amend the `(GetInstanceAttributes, sidechannel)` entry, and do not touch `tools/derive-database-load-budget.py` -- the master plan asked for the opposite and was wrong, which this plan's survey records. Confirm by reading that the comment's closing sentence ("Anything added here needs the same two things") is still satisfied by what you wrote, and check whether `test_harness_driven_pairs_are_not_budgeted` needs any change (it should not; say so either way). Commit subject: `Name the evaluator in the unbudgeted pairs note.` |
| 2f | medium | sonnet | none | **Documentation of what this phase makes true.** Read decisions 2, 3, 4, 6 and 8 of this plan. In `docs/developer_guide/api_reference/instances.md`, correct the `### agentdependency` section phase 1 wrote: `settle_seconds`' ceiling is now `AGENT_OPERATION_DEPENDENCY_WAIT`, not `AGENT_OPERATION_MAX_DEADLINE` (decision 4), and its prose "satisfied this many seconds after the dependency completed" becomes "after the dispatcher observes the dependency complete", which is up to `DISPATCH_CHECK_INTERVAL` later (decision 6) -- that imprecision is the sort of thing a reader builds a timing assumption on, so it is corrected rather than left. In `docs/developer_guide/api_reference/agentoperations.md`, add `dependency_failure` to the example response body, since `external_view()` now carries it, and document the three keys it holds plus the `'missing'` sentinel. In `docs/operator_guide/agent_operations.md`, document `AGENT_OPERATION_DEPENDENCY_WAIT` beside the other agent-operation tuning options in whatever form that page uses for them, including the stall trade-off decision 5's description states -- that page is the operator's, so the trade is the point. In `docs/operator_guide/database.md`, add `dependency_wait_started (nullable)` to the `agent_operation_attributes` column row. Then the two ambiguities the master plan requires to be written down rather than fixed, each with exactly one home, in the developer guide file that documents the dependency vocabulary: **the lazy cascade** -- a dependent which has not reached the head of its instance's queue reads as `queued` through the API even after its `depends_on` dependency has failed, because evaluation happens at dispatch (master plan decision 8); and **the swept dependency** -- a dependency which completed, aborted or expired is hard-deleted `CLEANER_DELAY` (3600 seconds by default) after it finished while an errored one never is, so a dependent which reaches the head long afterwards finds it absent, this phase treats absent as a failure (decision 2), and a caller who needs to avoid that keeps the dependent's deadline under `CLEANER_DELAY`. Do not create `docs/developer_guide/agent_operations.md` -- that page is phase 4's deliverable and starting it here would split it across two phases; put these two paragraphs where the dependency parameters are already documented and leave a note in this plan's execution notes that phase 4 is to move them. Extend the `agentoperation-dependencies` token comment in `shakenfist/external_api/app.py` to say evaluation landed in this phase (decision 9). `AGENTS.md` and `ARCHITECTURE.md` are not touched: no convention changes, and the dispatcher is not a new component. Commit subject: `Document dependency evaluation.` |

## Risks and mitigations

- **The asymmetry between `depends_on` and `runs_after` gets
  flattened.** It is the most-copied-wrongly thing in the cluster-side
  original, and an implementer who factors the two walks into one
  helper will lose it. *Mitigation:* the 2c brief states it twice and
  asks for a four-case statement in the report; 2d adds two mutants
  specifically for the `runs_after` branches; and the definition of
  done asserts one test per branch. The management session checks the
  report's four sentences against the diff rather than against the
  tests.
- **A cascade writes state from a superseded dispatcher generation.**
  Decision 1 places evaluation after the generation check for exactly
  this reason, and the position is one line's carelessness away from
  being wrong. *Mitigation:* the definition of done pins it with a
  grep for the order of the three landmarks in the pass, which is
  falsifiable without running anything.
- **The budget anchor gets rewritten and the budget never expires.**
  `note_dependency_wait_started()` is idempotent by construction
  (decision 2, step 2b), but a caller could still write the attribute
  directly. *Mitigation:* 2d's anchor-idempotence mutant, and a
  definition-of-done grep that the attribute has exactly one writer.
- **The settle ceiling change surprises a reviewer.** It tightens a
  bound published one phase ago. *Mitigation:* it is the back brief's
  first question, so it is settled before any code moves, and the
  reasoning is in decision 4 for the reviewer who reads the plan
  rather than the diff.
- **The live migration test cannot run.** It needs
  `SF_MARIADB_TEST_DSN` and skips silently without it, which is how
  phase 1 shipped a step that left two live tests failing. *Mitigation:*
  the definition of done requires the live module's run to be reported
  with a count of tests **run**, not passed, and a run of zero is a
  failed criterion. A disposable MariaDB in Docker on
  `127.0.0.1:13306` is how phase 1 did it; `tools/ci-install-mariadb.sh`
  is not the way, because it apt-installs and starts a server on the
  host.
- **`pre-commit`'s flake8 hook does not see this work.**
  `tools/flake8wrap.sh` lints `git diff --name-only HEAD~1`, so
  uncommitted work is invisible to it. *Mitigation:* run flake8 by
  hand per step, with the repository's own `grep -v _pb2` exclusion,
  before each commit.

## Definition of done

Each item is checkable, and the command is given where there is one.
An item that cannot be checked is reported as such, not ticked.

- `AGENT_OPERATION_ATTRIBUTES_VERSION` is 6 and
  `AgentOperation.current_version` is still 3. Check: `grep -n
  'AGENT_OPERATION_ATTRIBUTES_VERSION =' shakenfist/mariadb.py` and
  `grep -n 'current_version' shakenfist/operations/agentoperation.py`.
- `_ensure_agent_operation_attributes_schema` contains exactly **one**
  version-gated block, whose ALTER list names both
  `dependency_failure` and `dependency_wait_started`. Check: `grep -c
  'if current_ver <' ` over that function's body returns 1.
- `dependency_wait_started` has exactly one writer. Check: `grep -rn
  'dependency_wait_started' --include=*.py shakenfist/ | grep -v
  tests | grep -v _pb2` shows the schema field, the mariadb layers,
  the proto conversion and `note_dependency_wait_started` -- and no
  other assignment.
- The three landmarks appear in this order in the dispatch pass:
  `agent_operation_next(`, the `dispatcher_generation` comparison,
  `_dependencies_met(`, `'dispatching agent operation'`,
  `start_instance_executor(`. Check: `grep -n` for the five strings in
  `shakenfist/daemons/sidechannel/main.py` and confirm the line
  numbers ascend.
- An operation with both edge lists empty causes zero state reads,
  asserted by a test that counts calls rather than by inspection.
- `runs_after` never writes `dependency_failure`, asserted by two
  tests: a missing `runs_after` edge dispatches, and a `runs_after`
  edge past the budget dispatches, both with `dependency_failure`
  still None.
- `settle_seconds` above `AGENT_OPERATION_DEPENDENCY_WAIT` is refused
  with a 400 naming that option, and the OpenAPI document's
  `maximum` for the field equals the option's value. Check: the
  structured-schema test plus a grep that
  `AGENT_OPERATION_MAX_DEADLINE` no longer appears in the
  `settle_seconds` bound at `shakenfist/external_api/base.py`.
- `external_view()` carries `dependency_failure` and does **not**
  carry `dependency_wait_started`. Check: `grep -n
  'dependency_failure\|dependency_wait_started'
  shakenfist/operations/agentoperation.py` shows the former in the
  view dict and the latter nowhere in it.
- The live migration module runs with a **non-zero** test count and no
  skips, and its new assertions compare type, nullability and
  collation against a fresh `create_all`. Report the count of tests
  run; zero is a failure of this criterion, not a pass.
- `tools/mutate-agent-dependencies.py` kills every mutant, run
  serially, and the report names the test that failed for each.
  Phase 1 left the set at 10; this phase adds 10, so a report of
  fewer than 20 is a failed criterion.
- `shakenfist/data/database_load_budget.yaml` is unchanged. Check:
  `git diff develop -- shakenfist/data/database_load_budget.yaml` is
  empty. This is the inverse of what the master plan asked for, and
  survey finding 1 is why.
- Neither ambiguity is stated in two places. Check: `git diff develop
  -- docs/` and read it; `CLEANER_DELAY` and the lazy cascade each
  appear in exactly one documentation paragraph.
- `docs/developer_guide/agent_operations.md` does not exist. It is
  phase 4's deliverable. Check: `test -e` returns false.
- `pre-commit run --all-files` is green, and flake8 was additionally
  run by hand per step because the hook lints `HEAD~1`.

## Back brief

Answer these before any step runs. The first is the one that costs
most to reverse later.

1. **The settle ceiling (decision 4).** Lowering `settle_seconds`'
   maximum from `AGENT_OPERATION_MAX_DEADLINE` (86400) to
   `AGENT_OPERATION_DEPENDENCY_WAIT` (300) tightens a bound published
   in phase 1, one phase ago. The alternative is to accept values the
   evaluator can never honour. Tighten it now, accept them and
   document, or raise the budget instead?
2. **Absence means failure (decision 2).** An absent `depends_on`
   dependency aborts the dependent, which mis-reports a swept success
   as a failure for a dependent whose queue wait exceeded
   `CLEANER_DELAY`. Agreed, or should absence permit dispatch?
3. **No new capability token (decision 9).** Phase 1's token already
   advertises the parameters and already carries the `abort` safety
   argument. Agreed, or does evaluation want its own token?
4. **The `abort` reachability.** This phase is the first thing that
   writes `abort`, so it is the first point at which a client can
   observe it. `client-python-k3s` learned the state in its #164 and
   released it in v0.4.0, which phase 1's survey confirmed. Is any
   other consumer worth re-checking before the state becomes
   reachable, given that phase 0's audit covered 48 sites when
   nothing could reach it?
5. **Push and open the pull request**, push only, or hold for review?

## Execution notes

Filled in as the phase runs: departures from this plan, decisions
that changed, the CI run and its result, and the date with its
timezone. Correct a fact at its source when a step proves this plan
wrong, and say here that it was done.
