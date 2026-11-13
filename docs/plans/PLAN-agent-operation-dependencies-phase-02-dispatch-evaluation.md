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

   **Step 2c corrected the reasoning this decision was first written
   with, which had the mechanics backwards.** `AgentOperation.delete()`
   removes the operation's static and attribute rows and only then
   assigns `deleted`, and that state lives in a different table -- so
   a user-deleted dependency still has its state row and reads as
   `deleted`. It never presents as absent. The first draft of this
   decision argued that an absence seen during the 300-second budget
   must be a deletion; that is false, and reading state rather than
   hydrating is exactly what makes it false.

   Absence has one cause: the hard-delete sweep removed the state row,
   `CLEANER_DELAY` (3600 seconds) after the dependency reached a final
   state. So an absent dependency finished at least an hour ago, in one
   of the states the sweep collects. `error` is not one of them (issue
   #3922), so absence cannot distinguish a success from an abort or an
   expiry, and `complete` is the likeliest of the three.

   The conclusion survives the correction, on a different argument.
   Refusing is the safe side of the ambiguity: it declines work whose
   precondition probably held, where proceeding would perform work
   whose precondition may have failed, and a fate-sharing edge should
   fail safe. It is also reachable only by a dependent which sat in its
   instance's queue for over an hour, which its own deadline bounds.
   The cost is documented in step 2f and the mitigation is
   caller-side: keep a dependent's deadline under `CLEANER_DELAY`, so
   it cannot still be queued when the sweep runs.

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
   the dependency terminal, which is held in the terminal-dependency
   cache of decision 7 alongside the state. The consequence, which
   step 2f documents: a settle delay is "this many seconds after we
   noticed it finished", which is up to one `DISPATCH_CHECK_INTERVAL`
   (five seconds) later than the dependency actually finished. Phase
   1's API documentation says "satisfied this many seconds after the
   dependency completed", which is now imprecise by up to five seconds
   and is corrected in step 2f rather than left to be found later.

   **This decision as first written said the clock was "recorded in
   the same attribute decision 2 uses for the wait start", and that is
   not possible.** The wait anchor is set on the first pass which
   finds an operation blocked on anything; the settle has to start
   when a particular dependency is seen to finish, which is later and
   is per dependency. One column cannot hold both. Step 2c implemented
   the attribute reading, which is how the defect reached the tree;
   the two clocks are now separate and the execution notes record it.
   It also cited a step 2g, which this phase does not have.

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

Steps run **in sequence**. All six write to the shared worktree, so
none of them may run beside another: `pre-commit`'s stash is repository
wide and this project has already had parallel agents in one worktree
silently revert each other. 2a is a hard prerequisite of 2b and 2c, and
2d's mutants exercise what 2b and 2c build, which fixes the order of
the first four regardless. (An earlier draft of this paragraph said
"every step but 2f and 2g", which is wrong twice: there is no step 2g,
and the two documentation steps write to the worktree like the rest.)

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | high | opus | none | **The wait-start attribute column, all seven layers.** Read this plan's survey finding 2 and decision 2 first, then read phase 1's step 1a and 1b commits (`git log --oneline develop -- shakenfist/schema/agentoperation_attributes.py`) -- they are the shape of this one, for the same table. In `shakenfist/schema/agentoperation_attributes.py` add `dependency_wait_started: Optional[float] = None` after `dependency_failure`, with a comment saying: it is the wall-clock time of the first dispatcher pass which found this operation blocked on a dependency, it is the anchor for `AGENT_OPERATION_DEPENDENCY_WAIT`, it is written once and never updated (a second write would restart the budget and defeat it), it is NULL for every operation which was never blocked, and it is deliberately not on `external_view()` -- a caller does not need the anchor, only the outcome. In `shakenfist/mariadb.py`: bump `AGENT_OPERATION_ATTRIBUTES_VERSION` 5 -> 6 at `:494`; extend the existing `if current_ver < AGENT_OPERATION_ATTRIBUTES_VERSION:` block in `_ensure_agent_operation_attributes_schema` (`:19964`) by adding `'dependency_wait_started DOUBLE NULL'` to the column tuple that already carries `'dependency_failure LONGTEXT NULL'` at `:20028` -- **one block whose ALTER list carries every column ever added**, which is this repository's ladder convention, so do not add a second block and do not create a second version gate; `ADD COLUMN IF NOT EXISTS` makes the re-run harmless for a database already at 5. Match what `create_all` produces for an `Optional[float]`: read `shakenfist/schema/sqlalchemy.py` and confirm the type and that `nullable = is_optional and not is_pk` gives NULL, rather than assuming `DOUBLE` is right. Then the direct layer, which you find by grep rather than by line number: `_direct_create_agent_operation_attributes`, `_direct_get_agent_operation_attributes`, and `_agent_operation_attributes_column_values` -- the last is the field mask's vocabulary and a field missing from it cannot be written at all, which is the one failure in this step that no unit test will show you. In `protos/database.proto`, add `optional double dependency_wait_started = 7;` to `AgentOperationAttributesProto` (`:2352`, field 6 is the highest used) with a comment in the style of the presence comment at `:2348`: presence because absent means SQL NULL, meaning never blocked, which is different from blocked at epoch. Regenerate with `tox -e genprotos` and commit the regenerated `shakenfist/protos/` files in the same commit; never invoke `grpc_tools.protoc` directly. Carry it over the boundary in `_grpc_create_agent_operation_attributes`, `_grpc_get_agent_operation_attributes` and `_grpc_update_agent_operation_attributes` in `mariadb.py`, and `_agentop_attrs_from_proto` / `_agentop_attrs_to_proto` in `shakenfist/daemons/database/main.py`, copying the `HasField` idiom the neighbouring `dependency_failure` uses -- and remember a proto3 `optional` field cannot be assigned `None`, so assign only when the value is not None. Extend `shakenfist/tests/test_mariadb_agent_operations.py` (25 tests today) with both halves of the boundary for this field: a value survives the round trip, `None` stays `None`, `0.0` is not confused with `None` (the presence test that the `attempts`-style zero case exists for), and the new field is not transposed with `dependency_failure`. Extend the live migration module `shakenfist/tests/test_mariadb_agent_operations_live.py` the way phase 1's step 1f did: rewind to version 5 by dropping only this column, insert a row, re-run `ensure_schema()`, and assert the column's type, nullability **and collation** equal what `create_all` produces on a fresh database -- collation is load-bearing because `DATA_TYPE`, `COLUMN_TYPE` and `IS_NULLABLE` cannot distinguish MariaDB's `JSON` from `LONGTEXT`, which phase 1 established against a live server. Touch no evaluator, no config option and no API. Commit subject: `Record when a dependency wait began.` |
| 2b | high | opus | none | **The configuration option and the two state writers.** Read decisions 2, 3, 4, 5, 10 and 11 of this plan, and master plan decisions 2, 3, 7 and 9. In `shakenfist/config.py`, add `AGENT_OPERATION_DEPENDENCY_WAIT: int = Field(300, ge=1, ...)` beside the other `AGENT_OPERATION_*` options (`:378-479`), with the description decision 5 specifies -- all four facts, including that raising it raises how long one caller's blocked chain stalls another caller's operations against the same instance. Follow the surrounding options' description style, which is prose about consequences rather than a restatement of the name. In `shakenfist/operations/agentoperation.py`, add two methods to the `# Terminal outcomes.` block beside `expire()` (`:438-485`), which is the precedent for both -- read it first, including how it sets state, writes its reason attribute, adds its event and returns, and read `fail()` (`:486`) as its sibling. Note that `expire_if_out_of_budget()` is **not** the method to copy and is not on this class at all: it is the sidechannel daemon's caller in `shakenfist/daemons/sidechannel/main.py`, and an earlier draft of this brief named it by mistake. (1) `cascade_abort_for_dependency(self, dep, dep_state)`, which sets `dependency_failure` to `{'op_type': dep['op_type'], 'op_uuid': dep['op_uuid'], 'state': dep_state}`, persists it with the same single-field `fields=[...]` update `expiry_reason` uses, assigns `self.state = BaseOperation.STATE_ABORT`, and adds an audit event naming the dependency and its state. Order matters: write the attribute **before** the state, because `delete()` removes attribute rows before assigning a state and a reader who sees `abort` must already be able to see why -- say so in a comment. (2) `note_dependency_wait_started(self)`, which writes `dependency_wait_started` only if it is currently None and returns the anchor either way, so the budget cannot be restarted by a second blocked pass; make that idempotence a property of the method rather than something the caller remembers. Then the settle ceiling decision 4 commits to, which no other step owns: in `shakenfist/external_api/base.py`, change the bound at `:701-709` from `config.AGENT_OPERATION_MAX_DEADLINE` to `config.AGENT_OPERATION_DEPENDENCY_WAIT`, including the 400's message text which names the option, and change `AGENTDEPENDENCYSPEC_SCHEMA`'s `'maximum'` to match -- the two must agree, because the schema is what the OpenAPI document publishes and the helper is what actually refuses. Keep the comment at `:694-700` explaining why the ceiling lives in the API layer rather than on the model; it is still true. Update the `settle_seconds` description in that schema if it quotes the old ceiling, and update whichever test in `shakenfist/tests/external_api/` asserts the old maximum -- find it by grepping for `AGENT_OPERATION_MAX_DEADLINE` under `shakenfist/tests/external_api/`, and say in your report how many tests you had to change. Add a `dependency_failure` read-only property in the attributes block beside `expiry_reason` (`:544`), and add `'dependency_failure': attrs.dependency_failure` to `external_view()` beside `'expiry_reason'` (`:278`) -- see decision 8, and do not add a second attributes read, the row is already in `attrs`. Do **not** add `dependency_wait_started` to the view. Verify by reading `state_targets` that `queued -> abort` is permitted, so the assignment passes the guard, and say in your report what the guard does for an operation whose state has changed under you between the read and the write. Unit tests in `shakenfist/tests/` beside the existing agent operation tests: a cascade writes all three keys and moves the state to `abort`; a cascade on an operation already `complete` is refused by the state guard rather than silently corrupting it; `note_dependency_wait_started()` called twice keeps the first anchor; `external_view()` carries `dependency_failure` as None for an untouched operation. Add no evaluator and no dispatcher change -- that is 2c. Commit subject: `Add the dependency cascade and its budget option.` |
| 2c | high | opus | none | **The evaluator in the dispatcher.** Read decisions 1, 2, 3, 6, 7 and 10 of this plan, then read `shakenfist/daemons/queues/workitem.py:143-215` in full -- it is the cluster-side walk this mirrors, and the `depends_on` / `runs_after` asymmetry in it is deliberate and must survive. Then read `shakenfist/daemons/sidechannel/main.py:2060-2125` so you can see the pass you are editing. Add `_dependencies_met(self, inst, agentop)` to the sidechannel daemon class, returning a two-tuple of (may dispatch, reason-for-the-log). Walk `agentop.depends_on` first: for each edge read the dependency's state **by UUID without hydrating the object** -- master plan decision 9, because `AgentOperation.delete()` removes rows before assigning `deleted` and `from_db()` therefore returns nothing for a user-deleted dependency, which a hydrating evaluator would misclassify; use the same `GetObjectState` path `AgentOperation.state` uses and consult the terminal cache described below first. An absent dependency, or one in `error`, `abort`, `expired` or `deleted`, cascades: call `agentop.cascade_abort_for_dependency(dep, state_or_the_string_missing)` and return "do not dispatch" -- see decision 2 for why absence aborts and for the `'missing'` sentinel. A dependency in `initial`, `preflight`, `queued` or `executing` blocks: call `agentop.note_dependency_wait_started()`, and if `time.time() - anchor > config.AGENT_OPERATION_DEPENDENCY_WAIT` cascade with the dependency's live state instead, which is the `abandon_dependency_wait()` analogue (`shakenfist/operations/baseoperation.py:860`); otherwise return "do not dispatch" with no write. A dependency that is `complete` and has a `settle_seconds` is satisfied only once the settle has elapsed, measured from the anchor per decision 6, and a settle wait that pushes past the budget cascades like any other exhausted wait. Then walk `runs_after` with the **opposite** disposition, mirroring `workitem.py:175-210`: an absent dependency adds a warning audit event and **continues to the next edge**; a non-terminal one blocks exactly as above, except that an exhausted budget adds a warning event and **permits dispatch** rather than cascading. `runs_after` never writes `dependency_failure`. Say in your report, in one sentence each, what your code does for the four cases that distinguish the two lists, because flattening the asymmetry is the predictable failure here. The terminal cache is decision 7: a plain dict attribute on the daemon, cleared in `start_dispatcher()` where the generation is bumped, capped at 4096 with oldest-evicted, storing only terminal states, never caching a non-terminal observation. An operation whose `depends_on` and `runs_after` are both empty must read **nothing** -- make that the first line of the method and assert it in a test, because it is the cost control master plan decision 8 promises and it is one `if` away from being lost. Wire it into the pass between the generation check at `:2093-2100` and the `inst.add_event(... 'dispatching agent operation' ...)` at `:2102` -- decision 1 says why that exact position, and the two reasons are that a superseded generation must not write state and that the event must not claim a dispatch which did not happen. Log a blocked operation at debug, not info: this runs once per instance per five seconds. Tests in a new `shakenfist/tests/test_daemon_sidechannel_dependencies.py` (this repository names daemon tests `test_daemon_<daemon>_*`; an earlier draft of this brief said `test_sidechannel_dependencies.py`, which does not), driving `_dependencies_met` directly with mocked state reads rather than standing up a dispatcher: both lists empty issues zero reads; a `depends_on` on a `complete` dependency dispatches; on an `error` dependency cascades and records the state; on a missing dependency cascades with `'missing'`; on an `executing` dependency blocks without writing an anchor twice; a blocked wait past the budget cascades; a `settle_seconds` of 30 blocks at 10 seconds past the anchor and dispatches at 31; a `runs_after` on a missing dependency dispatches with a warning event; a `runs_after` past the budget dispatches with a warning event and no `dependency_failure`; and a second observation of a terminal dependency issues no second read. Commit subject: `Evaluate agent operation dependencies at dispatch.` |
| 2d | high | opus | none | **Prove the guards with mutation testing.** Read `tools/mutate-agent-dependencies.py`, which phase 1 committed and which this step extends rather than replaces -- its header warns that it edits the tree in place and must not run concurrently with anything else in the worktree, and that warning is load-bearing: phase 1 lost a bound check by starting it beside `pre-commit`, and the test that would have caught the loss was the one the mutant disabled. Add one mutant for each property this phase's tests claim, naming the test that must fail: the `depends_on` cascade on a failed dependency (invert the terminal-state membership test), the `'missing'` sentinel (return a real state instead), the budget comparison (flip `>` to `<`), the settle comparison, the `runs_after` warn-and-continue on a missing edge (make it cascade), the `runs_after` budget branch (make it cascade), the anchor idempotence (let the second write through), the cascade's attribute-before-state order (swap them), and the terminal cache's refusal to cache a non-terminal state. Run the whole set **serially**, each mutant under `timeout`, counting a timed-out mutant as killed, with `PYTHONDONTWRITEBYTECODE=1` -- a same-size mutant restored within a second otherwise leaves live bytecode. Restore from the script's own pristine copies, never with `git checkout`. Before you report, grep every touched file for the mutation markers and confirm each guard is present **by name**: do not trust a green run, because the disabled guard's test is the one that goes quiet. Report the kill count, the per-mutant test names, and the restoration check for each file. **Do not add a mutant for the empty-lists early return.** Step 2c established that it cannot be killed: with both lists empty both walks iterate zero times and the method returns the same verdict having read nothing, so removing the statement changes nothing observable. The statement stays as a contract and the zero-reads test proves the property either way; a mutant which cannot fail a test is worse than no mutant, because it reads as coverage. If a mutant survives, the finding is a missing test, so write it and re-run rather than deleting the mutant. Commit subject: `Mutation test the dependency evaluator.` |
| 2e | medium | sonnet | none | **The load budget comment, and nothing else in that file.** Read survey finding 1 of this plan in full first, then the comment above `HARNESS_DRIVEN_PAIRS` in `shakenfist/deploy/shakenfist_ci/load_budget.py:360-389`. Extend the paragraph that names what produces `('GetObjectState', 'sidechannel')` so it also names the dependency evaluator in `shakenfist/daemons/sidechannel/main.py`, in that comment's own register: a named loop which produces the reads, and the reason the budget is the wrong home for them. The reason is the one already written there and must not be re-argued: the model has no term for load which scales with in-flight operations, and an evaluator reads once per edge per blocked pass, which scales with how many operations declare edges rather than with instances or nodes. Say explicitly that an operation declaring no edges reads nothing, so the pair's floor is unchanged by this phase. Do **not** add an entry to `shakenfist/data/database_load_budget.yaml`, do not amend the `(GetInstanceAttributes, sidechannel)` entry, and do not touch `tools/derive-database-load-budget.py` -- the master plan asked for the opposite and was wrong, which this plan's survey records. Confirm by reading that the comment's closing sentence ("Anything added here needs the same two things") is still satisfied by what you wrote, and check whether `test_harness_driven_pairs_are_not_budgeted` needs any change (it should not; say so either way). Commit subject: `Name the evaluator in the unbudgeted pairs note.` |
| 2f | medium | sonnet | none | **Documentation of what this phase makes true.** Read decisions 2, 3, 4, 6 and 8 of this plan. In `docs/developer_guide/api_reference/instances.md`, correct the `### agentdependency` section phase 1 wrote: `settle_seconds`' ceiling is now `AGENT_OPERATION_DEPENDENCY_WAIT`, not `AGENT_OPERATION_MAX_DEADLINE` (decision 4), and its prose "satisfied this many seconds after the dependency completed" becomes "after the dispatcher observes the dependency complete", which is up to `DISPATCH_CHECK_INTERVAL` later (decision 6) -- that imprecision is the sort of thing a reader builds a timing assumption on, so it is corrected rather than left. In `docs/developer_guide/api_reference/agentoperations.md`, add `dependency_failure` to the example response body, since `external_view()` now carries it, and document the three keys it holds plus the `'missing'` sentinel. In `docs/operator_guide/agent_operations.md`, document `AGENT_OPERATION_DEPENDENCY_WAIT` beside the other agent-operation tuning options in whatever form that page uses for them, including the stall trade-off decision 5's description states -- that page is the operator's, so the trade is the point. In `docs/operator_guide/database.md`, add `dependency_wait_started (nullable)` to the `agent_operation_attributes` column row. Then the two ambiguities the master plan requires to be written down rather than fixed, each with exactly one home, in the developer guide file that documents the dependency vocabulary: **the lazy cascade** -- a dependent which has not reached the head of its instance's queue reads as `queued` through the API even after its `depends_on` dependency has failed, because evaluation happens at dispatch (master plan decision 8); and **the swept dependency** -- a dependency which completed, aborted or expired is hard-deleted `CLEANER_DELAY` (3600 seconds by default) after it finished while an errored one never is, so a dependent which reaches the head long afterwards finds it absent, this phase treats absent as a failure (decision 2), and a caller who needs to avoid that keeps the dependent's deadline under `CLEANER_DELAY`. Do not create `docs/developer_guide/agent_operations.md` -- that page is phase 4's deliverable and starting it here would split it across two phases; put these two paragraphs where the dependency parameters are already documented and leave a note in this plan's execution notes that phase 4 is to move them. Extend the `agentoperation-dependencies` token comment in `shakenfist/external_api/app.py` to say evaluation landed in this phase (decision 9). **Then the claims this phase makes false, which an earlier draft of this brief missed entirely.** Phase 1 wrote, in several places, that nothing evaluates an edge; this phase is the evaluator, so each of them is now wrong and none is a matter of wording. Fix all five, and grep for the shape rather than trusting this list: `docs/developer_guide/api_reference/instances.md:1004` carries an `!!! note "Not yet evaluated"` admonition whose whole subject is that edges are stored but not acted on -- it does not become true with an edit and should be replaced by a statement of what now happens, with the lazy-cascade caveat if that is where you put it; `docs/operator_guide/database.md:1489` says "Nothing evaluates an edge or writes `dependency_failure` yet: an operation created with edges is dispatched exactly as one created without"; `docs/developer_guide/state_machine.md:48` says nothing moves an operation into `abort` yet and that the state is "defined and reachable by the state machine rather than produced", which the cascade now produces -- say what produces it and keep the existing distinction between `abort`, `error` and `expired`, which is still right; and two test comments, `shakenfist/tests/external_api/test_agent_operation_parameters.py:390` and `shakenfist/tests/test_instance.py:2820`, each open "Nothing evaluates an edge yet" before explaining what the tests pin. Those two still pin storage and the API contract rather than evaluation, so keep what they say and drop only the claim that has stopped being true. Change no test code in this step. `AGENTS.md` and `ARCHITECTURE.md` are not touched: no convention changes, and the dispatcher is not a new component. Commit subject: `Document dependency evaluation.` |

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
  version-gated migration block, whose ALTER list names both
  `dependency_failure` and `dependency_wait_started`. Check: `grep -c
  'if current_ver < AGENT_OPERATION_ATTRIBUTES_VERSION'` over that
  function's body returns 1. The looser pattern `if current_ver <`
  returns 2 and always has, because the function also holds the
  create-if-absent gate `if current_ver <= 0:`; step 2a found this
  criterion written with the looser pattern, so it could never pass.
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
- **No documentation page or code comment still says an edge is not
  evaluated.** This phase is the evaluator, so every such claim phase
  1 left behind is now false. Check: `git grep -n 'evaluates them
  yet\|evaluates an edge\|not evaluated when it is dispatched\|no
  evaluator\|Not yet evaluated' -- docs/ shakenfist/ tools/` returns
  nothing outside `docs/plans/`, where a phase plan's own record of
  what was true when it was written is left alone. This criterion was
  added during execution: the step table's documentation brief had
  missed all five places, which the command above matches on six lines
  because the admonition spans two of them.
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

**2026-11-13, AEDT.** Back brief answered before any step ran, and
all four answers confirmed the plan as written: lower the
`settle_seconds` ceiling to `AGENT_OPERATION_DEPENDENCY_WAIT`
(decision 4), abort on an absent `depends_on` dependency (decision 2),
add no second capability token (decision 9), and push and open the
pull request when the phase is done.

The back brief's fourth question was settled in the management session
rather than asked, because re-auditing consumers is verification and
not a decision. Its result matters to phase 3 more than to this phase:

- Every in-repo reader of `AgentOperation.TERMINAL_STATES`
  (`instance.py:849`, `instance.py:3399`,
  `daemons/sidechannel/main.py:1405`) uses the canonical tuple, which
  has held `abort` since phase 1, so none needs a change.
- Two cluster CI modules spell their own terminal sets
  (`cluster_ci_tests/test_per_object_operations.py:12` and
  `test_observational_sweeps.py:23`) and both omit `expired`. That is
  correct rather than a defect: `STATE_EXPIRED` is an
  `AgentOperation` constant and not on `BaseOperation`, and neither
  module reads agent operations at all.
- `client-python` does **not** know `abort`:
  `TERMINAL_AGENT_OPERATION_STATES` at `apiclient.py:234` is
  `{complete, error, expired, deleted}`, and the await loop at
  `:1443` would spin on an aborted operation. It cannot reach one
  today, because `instance_execute` and its siblings take explicit
  named parameters with no `depends_on` and no `**kwargs`
  passthrough, so this phase is safe to land first. **Phase 3 must
  add `abort` to that frozenset in the same commit as the submission
  parameters, or before them, never after**: landing the parameter
  first opens a window in which a caller can create an operation
  which aborts and then hang awaiting it. That is the same
  single-commit obligation phase 0 found between `TERMINAL_STATES`
  and `state_targets`, and it is recorded on phase 3's row in the
  master plan.

**Step 2a** (`79a3637d34`) ran first and alone, as planned. Three
corrections came out of it:

- This plan's definition-of-done criterion for the single migration
  block was unfalsifiable as written, matching the create-if-absent
  gate as well as the version gate. Corrected at source above.
- The live migration tests run out of `.tox/py3`, not `.tox/shared`,
  which is what the management session's brief claimed. `pymysql` has
  to be installed there, because this project ships no MySQL driver
  (`mysqlclient` is commented out as GPL). Recorded for later steps.
- The live module's expected post-migration versions asserted
  `agent_operation_attributes: 5`, which was wrong rather than stale
  once 6 existed: because each ensure function holds a single ladder
  block, a database rewound to any earlier point arrives at the
  current target. Step 2a collapsed the per-rewind-point copies into
  one `MIGRATED_VERSIONS` dict and added `_every_later_column()`, so
  the deepest rewind picks up a newly added column without a second
  edit. Both kept.

The collation half of that column comparison is vacuous for a numeric
column: both sides report NULL, so only the type or the nullability
can diverge. Step 2a rewrote the test comment to say so rather than
imply the collation was doing work, and it stays in the shared helper
because it is the only discriminator for the text columns.

The management session reproduced step 2a's load-bearing claim rather
than accepting the report. Mutating the migration to add `LONGTEXT`
where `create_all` produces `DOUBLE` fails
`test_wait_anchor_column_matches_a_fresh_create` and nothing else, 1
of 22 live tests. Restored from a pristine copy, the guard confirmed
present by name, no mutant traces left.

**Two defects in this plan's own step 2b brief, found before it ran
and corrected at source above.** Both are the same failure as phase
1's step 1a brief: written from memory of the tree rather than from
the tree.

- It named `expire_if_out_of_budget()` as the precedent for the two
  state writers. That method is not on `AgentOperation` at all -- it
  is the sidechannel daemon's caller. The precedent is
  `AgentOperation.expire()` at `:438-485`, in the `# Terminal
  outcomes.` block, with `fail()` at `:486` as its sibling.
- **No step implemented decision 4.** The settle ceiling change was
  decided, approved at the back brief and asserted in the definition
  of done, but the step table gave it to nobody: no brief mentioned
  `shakenfist/external_api/base.py` or `AGENTDEPENDENCYSPEC_SCHEMA`.
  It is now part of 2b, which is the step that adds the configuration
  option the new ceiling reads. A definition-of-done criterion with
  no step behind it is how a decision silently fails to ship, and
  this one would have been caught only at the end.

**Step 2b** (`55df14124f`) added the configuration option, the cascade
helper, the wait anchor's writer and decision 4's ceiling. It was sent
back once.

- Its first version wrote the dependency failure attribute and then
  the state, with no guard on the operation's own state. That order is
  right -- a reader which sees `abort` always finds the reason already
  written -- but without a terminal guard it leaves a wire-visible
  lie. `_state_update()` re-reads state and refuses a transition out
  of a terminal state, so an operation which reached `expired` between
  the evaluator's read and the cascade keeps `expired` while carrying
  a `dependency_failure`. `deleted` cannot do this, because `delete()`
  writes its rows under an `UPDATE ... WHERE` whose `rowcount` is
  checked, and `complete` is very nearly unreachable; `expired` is
  reachable, because the sidechannel daemon expires operations from
  the same loop that would cascade them. The guard went in as a
  *noisy* no-op -- an audit event naming both operations and the
  dependency's state, then `return False` -- rather than a silent one,
  because a cascade which finds its dependent already finished is a
  race worth seeing in the log.
- Decision 4's ceiling now reads `config.AGENT_OPERATION_DEPENDENCY_WAIT`
  in both places that bound `settle_seconds`: the submission check at
  `external_api/base.py:714` and `AGENTDEPENDENCYSPEC_SCHEMA`'s
  `maximum` at `:1139`. The three deadline bounds at `:483`, `:489`
  and `:563` still read `AGENT_OPERATION_MAX_DEADLINE`, which is
  correct -- a deadline is not a settle.

**Step 2c** (this commit) added the dispatch evaluator. Four
corrections came out of its review.

- **Decision 2's mechanics were backwards**, and decision 2 above now
  says so. A user-deleted dependency keeps its state row and reads as
  `deleted`, so it never presents as absent; only the hard-delete
  sweep makes a dependency absent. The conclusion survives on a
  different argument -- fail safe rather than guess at what an absent
  row meant -- but the evaluator's absence-branch comment had repeated
  the management session's error, and was contradicted by the step's
  own test, `test_a_deleted_dependency_cascades_as_deleted_not_missing`.
  Comment rewritten, here and in the code.
- The audit text for giving up on a wait was the same string as the
  text for a failed dependency, so an operator reading the log could
  not tell a dependency which failed from one which never settled.
  Split into `CASCADE_EVENT_FAILED` and `CASCADE_EVENT_GAVE_UP` on the
  operation, threaded through `_cascade_abort()`'s `event` parameter
  at the two exhaustion sites, and pinned by two added tests. Setting
  `CASCADE_EVENT_GAVE_UP = CASCADE_EVENT_FAILED` fails exactly those
  two; restored, 27 pass.
- **A third defect in this plan's own brief.** 2c's brief named the new
  test module `test_sidechannel_dependencies.py`, and the step did as
  it was told. This repository names daemon tests
  `test_daemon_<daemon>_*` and already carries three
  `test_daemon_sidechannel_*` modules, so the brief was wrong rather
  than the step. Renamed to
  `test_daemon_sidechannel_dependencies.py`, and the brief corrected
  above.
- **2d's empty-lists mutant cannot be killed**, and has been dropped
  from 2d's brief above. With both edge lists empty each walk iterates
  zero times, so removing either walk returns the same verdict having
  read nothing. A mutant which no test can distinguish is not a gap in
  the tests.

One design point worth stating, because it looks like a defect and is
not: `runs_after` honours `settle_seconds`. That is not fate-sharing
leaking into the ordering-only edge. The field lives on the shared edge
model, the API validates it on both lists, and the API reference
documents it for an edge in either list, so ignoring it on `runs_after`
would silently drop a parameter this project has already published.

**Step 2d** added nine mutants rather than the ten its row lists, the
empty-lists mutant having been dropped above, for twenty in the set.
All twenty are killed.

The definition of done's arithmetic survives that change and should
not be "corrected" by a later reader: phase 1 left the set at ten,
step 2b added the cascade's terminal guard, and 2d added nine, so the
phase does add ten and fewer than twenty is still a failure. Step 2d's
own report claimed both terms of that criterion were wrong. They are
not.

**Adding a sixth file to the mutation script exposed a latent bug in
it which destroyed one of them.** The pristine copies were named by
`os.path.basename(path)`, and a daemon's module is `main.py` whichever
daemon it is, so `shakenfist/daemons/database/main.py` and
`shakenfist/daemons/sidechannel/main.py` shared one save file. The
consequences compound in a way worth recording, because every one of
them reads as success:

- the save loop wrote the sidechannel module over the database
  module's only copy;
- the restore after the first mutant in the database daemon wrote the
  sidechannel module into `shakenfist/daemons/database/main.py`;
- every later mutant then failed with a discovery `ImportError`
  (`rc=100`) rather than a test failure, which the script still counts
  as a kill, because it checks only that the run failed and that the
  named test appears in the output -- and it does appear, in the list
  of tests which could not be imported;
- the end-of-run restoration check compared **both** `main.py` files
  against the one surviving copy, so it reported both restored.

The first run therefore printed seven kills and six files restored
while a source file in the tree had been replaced with a different
module. This is a tool whose whole purpose is to distinguish a guard
which holds from a guard which cannot fail, and its own restoration
report could not fail.

Copies are now named for the whole path, and `main()` refuses to start
if two entries in `FILES` map to the same save name. That check runs
before anything is copied, because by the time a collision is
observable the copy it collided with is already gone. **A new entry in
`FILES` must map to a distinct save name**, which the guard now
enforces rather than leaving to whoever adds the seventh file.

Note what this was not: the hazard the script's own header warns about
is a *concurrent* edit, and there was none. The script destroyed the
file by itself, on a single serial run, in the course of doing exactly
what it is for.

The management session re-ran the whole set rather than accepting the
report, and verified it the ways the script's own output cannot. All
twenty killed with `rc=1`, which distinguishes a failing test from the
import failure that masked the corruption. `git diff --name-only HEAD`
lists only the script, so every mutated file is byte-identical to the
commit rather than to a copy of itself. The two daemon modules differ
from each other. No mutation string survives anywhere in the six
files, the nine new guards were confirmed present by name, and the 40
tests pass on the restored tree. The collision guard was driven
directly, by handing a deliberately colliding `FILES` tuple to a
throwaway directory of placeholder files: it refuses before copying
anything, and the old basename scheme maps these six paths to five
names, which is the bug stated as a measurement.

**A fourth defect in this plan's own briefs, and the largest.** Step
2f's brief listed the facts this phase changes -- the settle ceiling,
the settle's measurement point, the new column, the new option, the
two ambiguities -- and missed the fact that phase 1 had written "nothing
evaluates an edge" into five places. This phase is the evaluator. An
`!!! note "Not yet evaluated"` admonition in the API reference, a
sentence in the database operator guide, a paragraph in the state
machine reference saying nothing produces `abort`, and two test
comments were all going to survive the phase that falsified them.

Found by the management session while checking 2f's premises before
briefing it, which is the only reason it was not found after the pull
request opened. The brief now names all five and tells the step to grep
for the shape rather than trust the list, and the definition of done
has gained a criterion with a command, because "documentation updated"
would not have caught this and did not.

The pattern across all four brief defects is worth naming now that
there are four: every one of them is a brief written from memory of the
tree rather than from the tree, and every one was caught by reading the
tree before briefing. That check costs a few minutes per step and has
found a non-existent method, an unshipped decision, a misnamed test
module and now five false statements. It should be a step of its own in
the next phase's plan rather than something the management session
remembers to do.

**Step 2f documented the two ambiguities in the API reference, not in a
developer guide page.** `docs/developer_guide/agent_operations.md` is
phase 4's deliverable, so the lazy cascade and the swept dependency sit
in `docs/developer_guide/api_reference/instances.md` under "Declaring
dependencies between agent operations", where the dependency parameters
are documented. **Phase 4 is to move both paragraphs** into the new
page and leave a link behind, so each stays in exactly one place.

**Step 2f found a real defect in step 2c's code, which is fixed rather
than documented.** Writing down what `settle_seconds` does forced
someone to state it plainly, and stated plainly it was indefensible:
the settle clock was the dependency wait anchor, set on the first
dispatcher pass which found the operation blocked on *anything*. So for
a dependency still running at that pass -- which is the ordinary case,
since a dependency worth waiting on usually takes longer than the
settle after it -- the delay was spent before the dependency finished,
and a caller who asked for thirty seconds of quiet after a reboot got
none at all. The parameter was not imprecise. For its commonest shape
it did nothing.

Decision 6 had asked for the right thing in its first sentence, "the
settle delay is measured from the dependency's own completion", and
then specified a mechanism in its second that cannot deliver it. 2c
implemented the mechanism. Neither the step's tests nor the mutation
set could catch it, because the test asserted the behaviour the code
had -- its comment said "measured from the wait anchor rather than from
the dependency's own completion" -- so the guard and the test agreed
with each other and the mutant for that comparison died honestly
against a wrong specification. That is the failure mode mutation
testing cannot see, and the thing which did see it was being made to
write the behaviour down for a caller.

The fix separates the two clocks, which decision 6 wrongly put in one
column. The terminal-dependency cache already records that a dependency
was seen finished, so it now records when, and the settle runs from
that. The wait budget still runs from the anchor, because the budget
bounds how long the queue head is held and it has been held since the
anchor. A cache entry lost to eviction or a new dispatcher generation
restarts the settle, which delays an operation rather than releasing it
early, and the budget still bounds how long that can go on.

`test_a_settle_does_not_run_down_while_the_dependency_does` pins it,
and fails on the old measurement with exactly the right complaint. The
mutation set gained the inverse of the defect as a mutant -- anchor the
settle on the wait -- plus one for the settle's own exhaustion check,
taking it to 22.
