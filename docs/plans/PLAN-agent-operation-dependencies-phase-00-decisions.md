# Dependency-aware agent operations phase 0: research and decisions

## Prompt

This phase resolves the seven open questions in
`PLAN-agent-operation-dependencies.md` into recorded decisions, and
re-cuts that plan's phase table against them, so that the
implementation phases build on settled semantics. It produces no
production code.

Ground every answer in the tree rather than in the master plan's
prose, which was written in 2026-08 before
`PLAN-agent-operation-deadlines` executed: the cluster operation
dependency machinery (`BaseClusterOperation.depends_on` /
`runs_after` and the three `_end_for_dependency()` helpers in
`shakenfist/operations/baseoperation.py`, the `dependency` model in
`shakenfist/schema/operations/baseclusteroperation.py`, and the
dequeue walks in `shakenfist/daemons/queues/workitem.py:144-213`
**and** `shakenfist/daemons/network/workitem.py:563-630`), the agent
operation object and its queue
(`shakenfist/operations/agentoperation.py`,
`Instance.agent_operation_next` at `shakenfist/instance.py:3164`),
the sidechannel dispatcher (`_dispatch_loop` at
`shakenfist/daemons/sidechannel/main.py:2008`), and the API's
declared-parameter machinery
(`shakenfist/external_api/base.py:803-840`,
`shakenfist/external_api/validation.py:626`). The client surface
lives in the sibling `client-python` repository and the first example
application in `client-python-k3s`.

**Planning effort:** high (this is the decisions phase for a plan
whose subtle parts are queue semantics, cascade failure handling and
cross-namespace authorisation). Review effort for the recorded
decisions: high.

## Scope

In: answering the master plan's seven open questions, recording each
as a numbered decision with rationale in the master plan (replacing
its "Open questions (resolve in phase 0)" section), re-cutting the
master plan's phase table against those decisions, and updating the
design-sketch text the decisions change.

Out: all implementation — no schema column, no API parameter, no
evaluation code, no client change, no documentation beyond the plan
files themselves. Issues are filed for work this phase decides to
defer, but nothing is fixed here.

## What the survey found

The survey checked every factual claim the master plan makes about
the tree. Five were wrong or incomplete, two of them load-bearing on
the plan's own rationale; the rest of the plan's reading of the code
holds. Four corrections are applied at source in this planning
commit, as marked; the two that are really decisions rather than
facts are left for step 0c, because recording a decision is this
phase's job and not the planner's.

1. **The cluster-operation dependency wait is already bounded, and
   the master plan's description of it is incomplete.** Both
   dispatchers give up once the defer budget is spent —
   `DEPENDENCY_DEFER_BUDGET = 30` at
   `shakenfist/operations/baseoperation.py:44`, roughly six minutes
   of exponential backoff, then `abandon_dependency_wait()` aborts
   the dependent operation (issue #3992). The master plan's Situation
   section says only that "an in-flight dependency defers it", which
   reads as an unbounded wait. **Corrected at source.**
2. **Dependency cycles are structurally impossible, so the master
   plan's decision 4 rests on a hazard that cannot occur.** A
   `depends_on` edge can only name an operation that already exists —
   the API assigns the UUID (`AgentOperation.new(str(uuid.uuid4()),
   ...)` at `shakenfist/external_api/instance.py:2334`, `:2401`,
   `:2467`), a client cannot pre-declare one, and an edge is static
   at creation with no API to add one later. Every edge therefore
   points backwards in creation order and the graph is a DAG by
   construction. What actually needs bounding is a wait on a
   dependency that never goes terminal, which is the hazard #3992
   found in the cluster-operation machinery and fixed with a budget
   rather than with a deadline. The gate on
   `PLAN-agent-operation-deadlines` still holds — queue-time expiry,
   the terminal-only lazy pop, the reaper and the capability-token
   precedent are all load-bearing below — but "deadlines are the
   cycle-breaker" is not the reason. Left for step 0c, since it is
   decision 4 that has to change and that is a decision.
3. **Widening `VALID_CLUSTER_OPERATIONS` is not the right mechanism,
   and is wider than the design sketch intends.** That frozenset
   (`shakenfist/schema/operations/baseclusteroperation.py:37`) has
   exactly one consumer, the `dependency.validate_op_type` validator
   at `:117` — so the sketch's "admit `ObjectType.AGENTOPERATION` to
   the dependency schema's permitted operation types" is mechanically
   cheap, but it admits the edge in *both* directions and so settles
   open question 2 (mixed-type edges) by accident, in favour of
   allowing them, with no evaluator on the cluster side that can read
   agent operation state. The name is also then a lie. **Corrected at
   source** as a note on the sketch; which mechanism replaces it is
   decision work for 0c.
4. **The API already has the shape this plan needs for an
   array-of-objects body parameter, and a worked precedent.**
   `shakenfist/external_api/base.py:821-829` carries
   `arrayofdiskspec` and `arrayofnetworkspec`, each nesting a
   structured schema (`DISKSPEC_SCHEMA`, `NETWORKSPEC_SCHEMA`), and
   `validation.py:626` compiles an `array` of `object` into a
   `fields.List` of a nested schema, so a `DEPENDENCYSPEC_SCHEMA`
   plus an `arrayofdependency` token is a pattern the repository
   already runs. The master plan says only "API parameters".
   **Corrected at source.**
5. **Documentation is less of a greenfield than phase 5's row
   assumes.** `docs/operator_guide/agent_operations.md` already
   exists (252 lines: one executor per instance, the two budgets,
   where they are enforced, `expired` versus `error`, retry and the
   reaper, what to tune) because `PLAN-agent-operation-deadlines`
   phase 7 wrote it, and
   `docs/developer_guide/api_reference/agentoperations.md` and
   `instances.md` carry the API surface. There is no developer-guide
   page for the agent operation *model*. **Corrected at source.**

Premises the survey checked and found unchanged or true:

- `depends_on` / `runs_after` are on `BaseClusterOperation`
  (`baseoperation.py:392-402`) and not on `BaseOperation`, so
  `AgentOperation` inherits neither. The three cascade helpers
  (`abandon_dependency_wait`, `abort_for_unsuitable_dependency`,
  `cancel_for_missing_dependency`) and `_end_for_dependency()` are
  likewise cluster-only, and they reach for `defer()`,
  `current_defer_count` and the schema model's `target_fields`, none
  of which an agent operation has. Sharing the *evaluation* with the
  cluster dispatchers is therefore a bigger refactor than the master
  plan's "evaluation semantics mirror `workitem.py`" implies, and
  there are two copies of that walk to mirror, not one.
- `depends_on` semantics as the plan states them: missing dependency
  errors (`cancel_for_missing_dependency` →
  `BaseClusterOperation.STATE_ERROR`), a dependency in
  `error`/`deleted`/`abort` aborts (`abort_for_unsuitable_dependency`
  → `STATE_ABORT`), an in-flight one defers — with correction 1
  above. `runs_after` is ordering only and a missing dependency is a
  logged warning (`workitem.py:174-213`).
- The sidechannel has no defer machinery, and a blocked operation
  staying queued costs nothing to express: `agent_operation_next()`
  leaves a dispatchable head on the queue and the dispatcher skips an
  instance that already has an executor.
- `node_aop_op` wraps an agent operation's preflight in a cluster
  operation and already carries `depends_on` / `runs_after`
  (`shakenfist/schema/operations/node_aop_op.py:55-56`), so the type
  systems are adjacent as the plan says.
- `_dispatch_loop` is throttled per instance at
  `DISPATCH_CHECK_INTERVAL = 5`
  (`daemons/sidechannel/main.py:94`), and that constant is not just a
  comment: `shakenfist/data/database_load_budget.yaml:311-323` derives
  the `(GetInstanceAttributes, sidechannel)` per-instance term from
  it, `tools/derive-database-load-budget.py` restates it, and a unit
  test fails if they drift (issue #4039). Any per-pass read this plan
  adds has a budget entry to amend — the master plan's open question
  6 names the cost but not the file that holds it.
- `client-python-k3s` is a de-facto example application as claimed:
  `shakenfist_client_k3s/cluster.py:2593` has
  `execute_and_await(instance_uuids, cmds)`, which submits one
  operation per instance and then awaits each in turn — exactly the
  fan-out a declared chain would replace — and it stores cluster
  state in namespace metadata (`primitives.py:293`, `:569`).
- The prerequisite plan has genuinely landed, not merely been
  marked Complete: `AgentOperation.STATE_EXPIRED`,
  `TERMINAL_STATES` and the `executing -> queued` retry edge are in
  `operations/agentoperation.py:35-123`, `reap_instance_executors()`
  and `expire_unpromoted_head()` are in the sidechannel daemon
  (`main.py:1727`, `:710`), the per-command handler classes declare
  `reports_progress` and `retryable` (`main.py:456-591`), and the
  four merge commits spot-checked from its phase table
  (`45debf843`, `9bca46412`, `4a122bcd3`, `185de6b32`) are all
  ancestors of `develop`.

## Decisions this plan already takes

The survey settles enough that deferring every question to the
research steps would be a pretence. These are taken now, with the
reasoning; steps 0a and 0b test the two that rest on something other
than reading the code, and 0c records the set in the master plan
after the back brief.

1. **The cascade state is `abort`, and the capability token is what
   makes it safe.** A dependent operation whose `depends_on` failed
   moves to `abort` — a state `AgentOperation.state_targets` does
   not currently reach — rather than to `error` or to `expired`. It
   is the vocabulary the cluster-operation cascade already uses, it
   is already in `constants.FINAL_OBJECT_STATES` so an aborted
   operation is hard-delete swept (an errored one is not: #3922), and
   it does not claim either that the operation failed or that a
   timing budget ran out, neither of which is true of a cascade. The
   cost is that `client-python` pins the terminal set by hand
   (`TERMINAL_AGENT_OPERATION_STATES` at `apiclient.py:234`,
   commented as "exactly `AgentOperation.TERMINAL_STATES`"), so a
   released client would poll an aborted operation to its full
   timeout — the #363 bug the deadlines plan's phase 6 just fixed.
   That cost is avoidable rather than accepted: the new parameters
   are gated behind an `API_CAPABILITIES` token the way
   `deadline_seconds` was (#4005), so an operation can only reach
   `abort` if it was created by a client that declared a dependency,
   which is a client that knows the state. No released client can be
   hung by a state it cannot cause.
2. **Strict FIFO is preserved: a dependency-blocked head blocks the
   operations behind it** (open question 1), and declaring a
   dependency does *not* opt an operation out of queue ordering.
   `agent_operation_next()`'s durability argument is that the head of
   the queue is the unit of recovery, and its crash-safety argument is
   that there is one executor per instance; scanning past a blocked
   head replaces the head pointer with per-operation claims, which
   the deadlines plan named as the reason concurrency is its own
   master plan (`PLAN-agent-operation-deadlines.md`, "Why concurrency
   stays out of scope"). Two semantics for queue order, depending on
   whether an operation declared an edge, is also the kind of subtlety
   decision 5 of the master plan says the documentation exists to
   avoid.
3. **The stall that decision 2 accepts is bounded by a dedicated
   dependency-wait budget, not by the caller's deadline.** A blocked
   head whose wait exceeds the budget is aborted, mirroring
   `abandon_dependency_wait()`. The deadline cannot be the bound:
   `AGENT_OPERATION_DEFAULT_DEADLINE` is 600 seconds but a caller may
   ask for up to `AGENT_OPERATION_MAX_DEADLINE`, 86400
   (`shakenfist/config.py:378`, `:460`), so with deadlines as the
   only bound one caller could stall an instance's queue for a day by
   asking for a day. The budget is wall-clock from the first blocked
   evaluation rather than a defer count, because the dispatcher's pass
   rate is throttled and variable where the cluster dispatchers'
   backoff is not, and a wall-clock budget needs one attribute write
   on first block rather than one per pass. Proposed value: 300
   seconds, in the same ballpark as the cluster-operation budget's
   effective six minutes; the operator confirms it at the back brief.
4. **Edges are validated at submission, which is also the cycle
   answer** (open questions 3 and 5). The API handler reads each
   named dependency once and refuses with a 400 if it does not exist,
   is not an agent operation, or belongs to a namespace the caller
   could not read; it does not walk the graph, because survey finding
   2 shows there is no cycle to find. Namespace is checked at
   submission only and never re-checked at evaluation: the edge was
   authorised when it was declared, and re-checking would let a later
   namespace change break a chain already in flight.
5. **Evaluation happens in the dispatcher, between
   `agent_operation_next()` and `start_instance_executor()`** (open
   question 6), not inside `agent_operation_next()`. The deadline
   check lives inside that method, which is the tempting precedent,
   but it runs under the `agent_operations` attribute lock and a
   multi-edge walk under that lock would hold it across several
   database reads. In the dispatcher the blocked operation simply
   stays queued — which is what the master plan's design sketch says
   and what the sidechannel's lack of defer machinery makes free —
   and a cascade abort is a state write that the next pass's lazy pop
   retires. Cost control: nothing is read for an operation that
   declares no edges (the overwhelming majority), reads happen at
   most once per `DISPATCH_CHECK_INTERVAL` per instance because they
   hang off the existing throttled check, and a dependency observed
   in a terminal state is cached in process for the life of the
   dispatcher generation, since terminal is absorbing. The
   `database_load_budget.yaml` entry is amended in the same phase as
   the code, as a code-derived term.
6. **Cross-instance edges cost nothing extra at evaluation, so they
   land with same-instance edges rather than in a phase of their
   own.** The evaluator reads the dependency's state by UUID and
   never looks at which instance it belongs to; the only
   instance-aware work is the namespace check decision 4 puts in the
   API handler. The master plan's separate phase 3 therefore
   collapses into its phase 2, which is part of what 0c re-cuts.
7. **Mixed-type edges are out of scope** (open question 2), and the
   mechanism chosen for decision 1 keeps them out by construction: a
   new `VALID_DEPENDENCY_OPERATIONS` set and an agent-operation-only
   edge model, rather than widening `VALID_CLUSTER_OPERATIONS`, so a
   cluster operation still cannot name an agent operation and the
   two cluster dispatchers need no change at all. The use case ("run
   this script after the artifact fetch") is real and is expressible
   today by awaiting the fetch client-side; admitting it would mean a
   third and fourth evaluation site, in processes whose cascade
   helpers write `STATE_ABORT` through machinery agent operations do
   not have. Filed as an issue and recorded under Future work rather
   than silently dropped.
8. **Settle delay is a field on the agent edge model, not on the
   shared one.** Decision 3 of the master plan is kept as an edge
   attribute, but it goes on the agent-operation edge model, because
   a `settle_seconds` on the shared `dependency` model would appear
   on every cluster-operation edge with no evaluator honouring it.
9. **The documentation deliverable is a developer-guide page plus an
   example index** (open question 7). The model treatment —
   ordering, independence, deadlines, dependencies, retry, with
   worked examples — goes to a new
   `docs/developer_guide/agent_operations.md`, because the existing
   `docs/operator_guide/agent_operations.md` is a tuning and
   enforcement page for operators and the dependency vocabulary is an
   API contract. The example suite is an index section naming sibling
   repositories, with `client-python-k3s` as its first entry and its
   rolling-update chain as the worked example; no new CI is built to
   exercise examples, because each example repository already has its
   own. The second example is left to be chosen when there is a
   second feature to exercise, recorded as an issue rather than
   invented in a planning document.

The decision most likely to be argued with is decision 2, strict
FIFO. The case against it is real: one caller's blocked chain stalls
every other caller's operations on that instance for up to the
dependency-wait budget, and a cross-instance chain makes that easy to
cause by accident — instance X's operation waiting on instance Y's
slow transfer blocks X's queue for callers who have nothing to do
with either. The case for it is that the alternative is not "better
utilisation" but a second queue contract: an operation that declared
an edge would be dispatchable out of order, which makes "put a file,
then execute it" true only for operations that declared nothing, and
which asks `agent_operation_next()` to hold a dispatchable candidate
that is not the head — the exact head-pointer-to-claims change the
deadlines plan refused. Step 0a measures how often an instance's
queue actually holds operations from more than one submitter, because
if the queue is nearly always shallow and single-caller then the
objection is theoretical and decision 2 is free; if it is not, the
operator should hear that before 0c records it.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 0a | high | sonnet | none | Measure how deep agent operation queues actually get, and whether one instance's queue ever carries work from more than one submitter — the evidence decision 2 (strict FIFO) rests on. Two sources. (1) `sfcbr`, the production-ish cluster: use the `sf-db-load` and `loki-query` skills' helpers, and `sf-client --json instance agentoperations` style reads if a live read is easier than logs, to count for each instance over a representative window the number of agent operations in flight at once and the distinct `request_id` / token subjects that created them. The sidechannel logs 'Dispatching agent operation' per dispatch (`daemons/sidechannel/main.py` around line 2082) and the instance carries an `agent_operations` attribute with `queue` and `all` lists (`shakenfist/instance.py:3308`). (2) CI: the five most recent successful cluster `merge_group` runs (find them with the `ci-status` helper; note that Loki dumps in CI artifacts are empty, use the node journals under `bundle/sf*/_commands/journalctl-sf-units`), same counts. Report: distribution of queue depth per instance, the fraction of instance-queue-seconds where depth > 1, and whether any depth > 1 case had two distinct submitters. An honest "depth is almost always 1, so the stall objection is theoretical" is the most likely and most useful answer; say so plainly if that is what the data shows, and say how much data was behind it. Output: a table appended to this phase plan under "Measurement results". |
| 0b | medium | sonnet | none | Audit every consumer of agent operation state and of agent operation *queue order* for compatibility with (i) a new terminal state `abort` and (ii) an operation that sits dispatchable-but-blocked at the head of a queue. Enumerate with file:line and give each site a verdict of breaks / ignores / needs-update-in-phase-N. Cover, in this repository: `AgentOperation.state_targets` and `TERMINAL_STATES` (`shakenfist/operations/agentoperation.py:25-123`), `BaseOperation.ACTIVE_STATES` (`operations/baseoperation.py:56`), `constants.FINAL_OBJECT_STATES` (`constants.py:204`) and the hard-delete sweep in `daemons/cluster/scheduled_tasks.py`, `Instance.agent_operation_next` and its unknown-state branch (`instance.py:3164-3306`), the instance delete sweep of agent operations (`instance.py` around line 1084), every `== STATE_` or `in TERMINAL_STATES` guard in `daemons/sidechannel/main.py`, `operations/node_aop_op.py`, the agent operation API endpoints (`external_api/agentoperation.py`, `external_api/instance.py:2249-2500`), `docs/developer_guide/state_machine.md`, and the CI suite's await loops (`shakenfist/deploy/shakenfist_ci/`). Then in the sibling repositories: `client-python`'s `TERMINAL_AGENT_OPERATION_STATES` (`shakenfist_client/apiclient.py:230-234`), `_await_agentop`, `await_agent_command`, `await_agent_fetch` and the CLI renderers; and `client-python-k3s`'s `await_execute` / `reap_execute` / `execute_and_await` (`shakenfist_client_k3s/cluster.py:2462-2610`). The equivalent audit for the deadlines plan is in `PLAN-agent-operation-deadlines-phase-00-decisions.md` under "State audit" — follow its format and its grep discipline (state the patterns used). Output: a table appended to this phase plan under "State audit". |
| 0c | high | opus | none | Management session with the operator: back-brief the nine proposed decisions and the two measurement-dependent values (see the back brief below), incorporating 0a and 0b. Then edit `docs/plans/PLAN-agent-operation-dependencies.md`: replace the "Open questions (resolve in phase 0)" section with numbered decisions and rationale, amend decision 4 for survey finding 2 (cycles are structurally impossible; the gate on the deadlines plan holds for queue-time expiry, the terminal-only pop and the capability-token precedent instead), update the design sketch where the decisions change it, and re-cut the phase table — phase 3 collapsing into phase 2 per decision 6, and each row naming its phase plan file. Update this plan's row in `docs/plans/index.md` to match the re-cut arithmetic and say what was decided. |
| 0d | low | haiku | none | File the two issues this phase defers, and reference each from the master plan's Future work: (1) mixed-type dependency edges between agent operations and cluster operations, declined for now with the reasoning in decision 7; (2) a second example application for the agent-operation example suite, to be chosen when there is a second feature to exercise. Both against shakenfist/shakenfist. Use "Fixes #NNNN" nowhere — these are not fixed, only recorded. |

Each step is its own commit; 0c and 0d may combine if the edits turn
out small.

## Risks and mitigations

- **0a finds nothing usable**, because agent operation traffic on
  `sfcbr` is dominated by one submitter (the k3s plugin) and CI
  queues are shallow by construction. Mitigation: that *is* the
  answer decision 2 wants, and the brief makes it a reportable
  result. The fallback is adopting decision 2 on the structural
  argument alone and saying so in the master plan, so a later reader
  knows it was not measured rather than assuming it was. The operator
  judges this at the back brief.
- **Decision 1's capability gate is only as good as the gate.** If a
  dependency parameter ever reaches an endpoint without its token,
  released clients meet `abort`. Mitigation: 0b's audit must record
  the client's hardcoded terminal set as a site needing update in the
  client phase, and the implementation phase that adds the parameters
  adds the token in the same commit — the pattern and its test
  precedent are `#4005` and `test_agent_operation_parameters.py`.
- **The decisions drift from the master plan during review of this
  phase's pull request.** Mitigation: the master plan edit is step
  0c, so it lands in the same pull request as these proposals and the
  reviewer sees both; any reshaping in review reopens 0c before
  merge.
- **A later phase discovers that sharing the cluster dispatchers'
  evaluation is unavoidable** after all, making decision 7's "no
  change to the cluster side" false. Mitigation: 0b enumerates the
  guard sites, and decision 5 deliberately puts the agent evaluator
  in the dispatcher rather than refactoring `workitem.py`, so the
  blast radius of being wrong is one new function and not two
  dispatchers.

## Definition of done

- `PLAN-agent-operation-dependencies.md` contains no section titled
  "Open questions" and no text saying "resolve in phase 0"; each of
  the seven questions appears as a numbered decision with a stated
  rationale. Check: `grep -n 'Open questions\|resolve in phase 0'
  docs/plans/PLAN-agent-operation-dependencies.md` returns nothing.
- That plan's decision about cycles no longer asserts that deadlines
  break user-created cycles, and states instead why the gate on
  `PLAN-agent-operation-deadlines` holds.
- Its phase table has a `Plan`, a `Status` and a `Merged` column,
  every `Merged` cell reads `—`, and no row describes cross-instance
  edges as a phase of their own (decision 6).
- This file contains a "Measurement results" section with either the
  queue-depth table or an explicit statement of how much data was
  examined and that it could not settle the question.
- This file contains a "State audit" section in which every site
  listed carries a file:line reference and a
  breaks/ignores/needs-update verdict, no site marked "breaks" lacks
  a phase assignment, and the grep patterns used are stated.
- The two deferred issues exist on GitHub and are linked from the
  master plan's Future work section.
- `docs/plans/index.md`'s row for this plan reads `In progress` with
  phase arithmetic matching the re-cut table, and the master plan's
  own phase 0 status agrees with it.
- `pre-commit run --all-files` passes.

## Back brief

Before step 0c edits the master plan, back-brief the operator on all
nine proposed decisions, and do not record either of these two
without explicit agreement:

- **Decision 2, strict FIFO**, with 0a's measurement in hand. This is
  the one with a live alternative, and choosing pass-by instead
  changes what phase 2 builds rather than merely what it is called.
- **Decision 3's 300 second dependency-wait budget**, which is a
  number chosen by analogy to the cluster-operation defer budget and
  not by measurement.

Also surface, as information rather than as a question: decision 1's
capability-gate argument, since it is what makes a new terminal state
safe, and survey finding 2, since it removes the stated reason this
plan was gated on the deadlines plan at all without removing the
gate's substance.

## Results

Nothing yet: this phase has been planned and not executed.
