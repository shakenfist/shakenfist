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
   which is a client that knows the state.

   Step 0b found that argument to be half true, and it is corrected
   here rather than in the step's own section. It holds for a client
   which polls the operations it created itself —
   `_await_agentop` in `client-python` — and fails for a client which
   polls an instance's whole operation list. `client-python-k3s`'s
   `await_idle()` (`shakenfist_client_k3s/cluster.py:2321`) reads
   every operation on an instance, including other callers' and
   historical ones, deliberately treats a state it does not recognise
   as still running, and has no timeout of its own; its comment
   justifies that with "the server moves every operation out of
   whatever state it is in within its deadline", which a terminal
   state falsifies. So one aborted operation created by a new client
   stalls a released k3s plugin polling the same instance until the
   hard-delete sweep removes the operation — `CLEANER_DELAY`, an hour
   by default — after which the listing stops reporting it and the
   wait proceeds. Bounded, diagnosable (it notes the unrecognised
   state once), and still an hour of silence inside a cluster
   install. The plan's answer is that `client-python-k3s` is a
   repository we own and is already phase 5's business, so it learns
   the state there; whether it should learn it *before* the server
   can emit one is a question for the back brief.
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

## Execution notes

- **2026-11-07 (AEDT).** Steps 0a and 0b ran in parallel as
  sub-agents: both are read-only, neither writes to the worktree, and
  each delivered its section as a file for the management session to
  review and commit. Nothing ran `git` or `pre-commit` in the
  worktree but the management session.
- **0a's data sources were narrowed by the operator** before it ran.
  Loki (via the read-only `loki-query` helper) and CI node journals
  only; no live API read against `sfcbr`, which the step's brief had
  offered as an option. Queue depth is therefore inferred from
  dispatch timing rather than read from the instance attribute.
- **This session may push the branch and open the pull request** once
  the definition of done passes; the operator granted that up front
  rather than at the end.
- **0a read two CI runs, not the five its brief asked for.**
  `ci-status` returns at most the newest 100 workflow runs with no
  event filter and no pagination, and that page held three successful
  `merge_group` functional runs, one of which skipped every functional
  lane. The step says so in its own section rather than presenting two
  runs as five. It makes little difference to the finding: CI queues
  are depth 1 by construction, in both runs and in all 154 instances.
- **Decision 1 was corrected at source by step 0b's findings** — the
  capability-gate argument protects a client which polls its own
  operations and not one which polls an instance's whole list. See the
  decision itself; the audit row is in the results below.
- **0d gained a third issue.** Step 0b found a pre-existing defect
  while auditing: `Instance.external_view()`
  (`shakenfist/instance.py:793`) reads the `queued` key of the
  `agent_operations` attribute, but the key written by
  `agent_operation_enqueue()` and read everywhere else is `queue`, so
  `agent_operations_queue` in an instance's view is always empty and
  the `include-queued-agent-operations` capability token advertises
  nothing. It is out of this plan's scope and is filed rather than
  fixed.

## Results

### Measurement results (step 0a)

**Short answer.** The objection to decision 2 is not theoretical as
far as queue depth goes, and it is untestable as far as submitters
go. On `sfcbr`, half of all agent operations arrived at an instance
that already had an unfinished operation, queues reached 11 deep,
and the worst observed wait behind the head was 100.6 s. In CI the
queue never held more than one operation. But no log line records who
submitted an operation, so the data cannot say whether any deep queue
held work from more than one submitter. Every deep queue seen is
consistent with one client fanning out a burst of different commands.

**Sources and how much data.**

| Source | What | Volume |
|---|---|---|
| Loki, tenant `sfcbr`, `daemon="sidechannel"` / `"sf-api"` | 'Dispatching agent operation', API-side audit events, 'commands complete', 'Dispatcher heartbeat' | 1,805 dispatch lines, 1,804 distinct operations, 56 instances, 2026-10-15 09:25 to 2026-11-06 22:32 UTC (22.5 days) |
| CI journals, run 42190825350 (pr-4810) | `journalctl-sf-units`, four lane bundles | 252 operations, 77 instances, 2026-11-06 |
| CI journals, run 42181923881 (pr-4811) | as above | 252 operations, 77 instances, 2026-11-06 |

The Loki window is the whole reachable history, not a sample: the
helper refuses lookbacks of 24 days or more, and 23 days returns
everything back to 2026-10-15. It is complete for these events: the
API audit log shows 1,804 agent operations created, the dispatcher
logged 1,805 dispatches (one operation was dispatched twice), and
1,802 logged 'commands complete'. The three independent counts
agree, so no ingestion gap is visible. Queue depth below means
operations created on an instance and not yet finished (finish is
the sidechannel's 'commands complete' event).

**CI runs: only two usable of the five asked for.** `ci-status`
returns at most the newest 100 workflow runs, has no event filter and
no pagination, and `--branch` needs an exact queue-branch name. The
newest 100 runs held three successful `merge_group` "Functional
tests" runs: 42190825350, 42181923881 and 42294554568. The third
(pr-4812, a documentation sync) skipped every functional lane, so it
has no cluster journals and was excluded. I did not find older runs
by other means (no git in any checkout was allowed), so the CI half
rests on two runs, not five. Both ran the same tests, which is why
their counts are identical. Of the four bundles per run, only
`bundle-shakenfist-full-guests` and
`bundle-shakenfist-full-debian-13-slim-primary` contain agent
operations; the ansible-modules and node-lifecycle bundles contain
none. Each bundle is a separate cluster.

**Dispatch volume and window.**

| Source | Dispatches | Instances | Ops/instance (median / max) | Busy instance-seconds |
|---|---|---|---|---|
| sfcbr (Loki) | 1,805 | 56 | 10 / 317 | 10,689 |
| CI, both runs (4 bundles x 2) | 504 | 154 | 2 / 16 | 1,987 |

sfcbr traffic is bursty: over 23 calendar days, 8 days with 75 or
more operations carry 1,466 of the 1,804 (475 on 2026-10-16 alone)
and 12 days carry 24 or fewer. Busy instance-seconds is the time
during which an instance had at least one unfinished operation, summed
over instances: 10,689 s is 3.0 hours across 22.5 days.

**Time between consecutive dispatches on the same instance.**

| Gap | sfcbr (n=1,748) | CI (n=350) |
|---|---|---|
| minimum | 5.03 s | 5.04 s |
| p50 | 5.98 s | 5.07 s |
| p95 | 246 s | 39.1 s |
| maximum | 98,172 s (27 h) | 236.6 s |
| 5 to 10 s | 1,010 (57.8%) | 302 (86.3%) |
| 10 to 30 s | 357 (20.4%) | 29 (8.3%) |
| 30 to 60 s | 182 (10.4%) | 3 (0.9%) |
| 60 to 300 s | 115 (6.6%) | 16 (4.6%) |
| over 300 s | 84 (4.8%) | 0 |

The floor of about 5.0 s is `DISPATCH_CHECK_INTERVAL`; no two
dispatches on one instance have ever been closer, so a gap of
5 to 10 s is the signature of a backlog draining one operation per
pass. On sfcbr that is 58% of all gaps. In CI the 5 to 10 s gaps are
sequential tests (submit, await, submit) with no overlap, which the
next table shows; a tight gap alone does not prove a deep queue,
the arrival table does.

**Arrival depth: what an operation found when it was created.**

| Source | Arrived to an empty queue | Arrived behind an op still queued (undispatched) | Behind an op still executing only | Behind both |
|---|---|---|---|---|
| sfcbr | 891 (49.4%) | 849 (47.1%) | 16 (0.9%) | 48 (2.7%) |
| CI | 504 (100%) | 0 | 0 | 0 |

On sfcbr 913 of 1,804 operations (50.6%) were certainly queued
behind another operation on the same instance: when they were
created, an earlier operation on that instance had not yet finished.
For 897 of those, the earlier operation had not even been dispatched
yet. That is dominated by the 5 s dispatch interval: two operations
submitted within about 5 s of each other always overlap. It is real
queueing, but most of it is not a stall caused by slow execution.

**Queue depth, weighted by time (sfcbr).** Share of busy
instance-seconds spent at each depth:

| Depth | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10+ |
|---|---|---|---|---|---|---|---|---|---|---|
| Seconds | 5,701 | 2,622 | 1,039 | 548 | 472 | 101 | 98 | 49 | 35 | 23 |
| Share | 53.3% | 24.5% | 9.7% | 5.1% | 4.4% | 0.9% | 0.9% | 0.5% | 0.3% | 0.2% |

Depth above 1 accounts for 46.7% of busy instance-seconds (4,988 s,
1.4 hours). Against all time it is negligible: 4,988 s out of
56 instances x 22.5 days is 0.005% (an upper-bound denominator,
since instance lifetimes are not in the logs). Maximum depth per
instance over the window: 6 of 56 instances never exceeded 1, 4 hit
2, 2 hit 3, 14 hit 4, 14 hit 5, 4 hit 7, 5 hit 8, 2 hit 9, 3 hit 10
and 2 hit 11. In CI, 100% of 1,987 busy instance-seconds were at
depth 1 and every one of 154 instances had a maximum depth of 1.

**The stall that decision 2 accepts, as observed (sfcbr).** Time from
creation to dispatch, 1,804 operations: p50 6.2 s, p90 17.5 s,
p95 28.1 s, p99 60.8 s, maximum 100.6 s. 552 operations waited more
than 10 s, 85 more than 30 s and 21 more than 60 s. Execution time
(dispatch to 'commands complete') is p50 0.57 s, p95 11.0 s, p99 33.7 s,
maximum 41.8 s, with 107 operations over 10 s. The long waits are
the cumulative execution time of several operations ahead (the 101 s
wait had seven operations ahead of it, the slowest running 39.8 s).
So the head-of-line effect that decision 2 would extend to dependency
blocks already exists today at up to 100 s, bounded by execution
time. The proposed 300 s dependency-wait budget would allow a stall
three times longer than anything observed. In CI, creation to
dispatch is p50 3.9 s and maximum 5.10 s, i.e. nothing but the
dispatch interval.

**Concurrent executors.** Dispatcher heartbeats report `executors`
(instances with a running executor on that node). Over the last two
days on sfcbr, 1 of 3,365 heartbeats (300 s apart per node) showed a
non-zero count. In CI, 3 of 212 did, with a maximum of 1. This is
consistent with the busy-time figure: executors exist for a tiny
fraction of wall time, so deep queues are brief bursts, not a standing
condition.

**Can two operations on one instance be attributed to different
submitters?** Not with what is logged. Checked, not assumed:

| Candidate signal | Present? | Verdict |
|---|---|---|
| API audit event 'agent operation `<cmd>` request from REST API' | Yes, in Loki and in the CI primary journal, with `request-id` and `instance` | Reaches the logs, but carries no caller identity |
| `request-id` | Yes, unique to every operation (0 of 1,873 overlapping pairs share one) | One HTTP request per operation; cannot group or separate callers |
| 'API request handled' `identity` field | Present but null on all 5,000 sampled lines (the newest 20 minutes) and on all 87 agent-execute POSTs | Caller identity is not logged |
| Operation `namespace` | Yes, in the 'db record created' event | Useless: `AgentOperation.new` is given `instance_from_db.namespace` (`external_api/instance.py:2334`, `:2401`, `:2467`), so it is the instance's namespace, not the caller's. All 56 instances have exactly one namespace, so "no cross-namespace overlap" is true by construction |
| Command type | Yes | 1,756 execute and 48 get-file on sfcbr; every one of the 1,873 overlapping pairs is execute vs execute, so no mix of types to suggest two callers |
| API user agent, token subject | No | Not in any log line |

What the data does show about the overlapping operations: 448 bursts
(runs of creations on one instance no more than 10 s apart) contain
two or more operations, covering 1,451 of the 1,804 operations. Burst
sizes are 2 (153), 3 (198), 4 (24), 5 (47), 6 (5), 7 (4), 8 (8),
10 (1) and 11 (8). In every one of the 448 bursts the operations carry
different commands (0 bursts repeat an identical command), which looks
like one client running a set of different checks in parallel and
not like retries or duplicate pollers. It is equally what several
cooperating clients would look like, so this is a weak hint at one
submitter, not a finding. 82 of the 448 bursts mix commands that
mention k3s with commands that do not (see below), which is the only
sign of possibly distinct logic.

**Is sfcbr dominated by one client?** Probably, by inference only.
The k3s plugin cannot be identified from the logs, so I classified
operations by whether the command text or file path contains the
substring `kubectl` or `k3s`. By that rule 1,270 of 1,804 operations
(70.4%; 1,222 execute and 48 get-file) are k3s-flavoured, which is
76.2% of the non-`system` traffic. All 138 operations in the `system`
namespace (7 instances, active on 2 days, 26 distinct command shapes)
are non-k3s. Of the 913 operations that arrived behind another, 646
(71%) are k3s-flavoured, about the same share as overall, so k3s
traffic is not over-represented in the deep-queue cases. Only 14 of
the 85 operations that waited over 30 s are k3s-flavoured, so the long
stalls are mostly non-k3s traffic. By namespace, 1,166 operations are
in 8 namespaces whose names look like per-run CI namespaces, 500 are in one
long-lived namespace (4 instances; one of them took 317 operations)
and 138 are in `system`. So the traffic is also mostly test-driven,
not production use.

**What the data cannot settle, and why.**

- Whether any queue held work from two submitters. No submitter
  identity exists in any log, and the namespace is inherited from the
  instance. Settling it needs a submitter field on the audit event
  or the operation, which is a code change, and the constraint on
  this step ruled out the live API reads (instance `agent_operations`
  attribute, operation records) that would also not carry one.
- Whether the k3s plugin specifically is the dominant client; the
  70% figure is a command-text heuristic.
- Anything about the five-run CI sample as specified; two runs were
  reachable, identical in shape, and CI queues are depth 1 by
  construction (sequential tests), so more runs would be very unlikely
  to change that.
- Dependency-blocked heads: no operation today can be blocked, so the
  waits above are execution waits, not a measurement of the proposed
  budget.

**What the measurement says about decision 3's budget (management
session's reading, not the step's).** The step compares the proposed
300 s against the worst wait observed today, 100.6 s, and calls it
three times longer than anything seen. That is the right comparison
for a wait caused by execution and the wrong one for a dependency
wait, which legitimately contains the dependency's *own* queue wait
and execution as well: on these numbers a one-hop wait is p95
28.1 + 11.0 = 39 s and worst-case 100.6 + 41.8 = 142 s, and a
two-hop chain roughly doubles the p95. So 300 s is about twice the
worst single-hop latency this cluster has produced, which is a
margin rather than an indulgence -- and it is now a number with
evidence behind it instead of an analogy to the cluster-operation
defer budget. A chain more than two hops deep on a busy instance
could legitimately exceed it, which is a thing to document rather
than to design around: the budget aborts, it does not corrupt.

**Reading for decision 2.** The data does not say the objection is
theoretical, and does not say it is real. Queues do get deep on sfcbr
(50.6% of operations arrive behind another), but the depth comes from
bursts of different commands on one instance within seconds, and
nothing distinguishes that from a single caller. If the operator
treats "one caller at a time per instance" as an acceptable operating
assumption, strict FIFO costs nothing measurable beyond what the
dispatch interval already imposes. If cross-caller isolation on one
instance matters, this step provides no evidence either way, and
decision 2 should be taken on the structural argument already in the
plan, recorded as not measured.

### State audit (step 0b)

Audited against the tree as it stands on 2026-11-07, after the
deadlines plan landed. Two changes are assessed: (i) a new terminal
state `abort` on `AgentOperation`, and (ii) a queued operation at the
head of its instance's queue that is deliberately not dispatchable
for minutes because a declared dependency has not finished.

Two facts frame the table. First, `abort` is not new vocabulary:
`BaseOperation.STATE_ABORT = 'abort'` already exists
(`shakenfist/operations/baseoperation.py:53`) and is inherited by
`AgentOperation`, which simply has no `state_targets` edge to it.
Second, the 2026-08 audit's "unguarded `state = STATE_ERROR`" rows no
longer exist: every error write in the sidechannel daemon and
`node_aop_op` now goes through `AgentOperation.fail()`
(`agentoperation.py:410-430`), which returns early from any state in
`TERMINAL_STATES`. That makes membership of `TERMINAL_STATES` the one
fact most verdicts below hang on: with `abort` in it, those sites are
safe for free; without it, `fail()` and `expire()` would raise
`InvalidStateException` from `abort` (no edge) and
`agent_operation_next()` would wedge the queue on its unknown-state
branch.

Phase numbers refer to the master plan's table as it stands before the
re-cut (1 schema and API, 2 dispatch evaluation, 4 client-python,
5 documentation and `client-python-k3s`); the cross-instance phase is
treated as folded into 2.

#### Server repository: state consumers

| Site | file:line | Verdict | Notes |
|---|---|---|---|
| `AgentOperation.state_targets` | `shakenfist/operations/agentoperation.py:66-90` | NEEDS UPDATE (1) | Needs `abort` as a target of at least `queued` (the dispatcher cascades from there) and an `abort -> deleted` edge. The edge matters: `delete()` (`:553-561`) removes the MariaDB rows and then assigns `deleted`, so without it the API DELETE of an aborted operation raises `InvalidStateException` after the data is already gone. Values must stay tuples (see the NOTE at `:57-60`). The cascade write itself must be a no-op from a terminal state, in the manner of `fail()`, because `deleted` has no edge to `abort` and a user DELETE can race the dispatcher. |
| `AgentOperation.TERMINAL_STATES` | `shakenfist/operations/agentoperation.py:51-52` | NEEDS UPDATE (1) | Load bearing, see the framing above. Add `BaseOperation.STATE_ABORT`. Every guard in the sidechannel daemon that says "terminal" reads this tuple. |
| `AgentOperation.external_view()` | `shakenfist/operations/agentoperation.py:196-212` | NEEDS UPDATE (1) | Carries `expiry_reason` and `error_message`. `error_message` is `self.error`, whose setter refuses any state not ending in `error` (see `expire()`'s comment at `:372-376`), so the prose cause of an abort lives only on the state row and the instance audit event, neither of which a client sees. A client told `abort` cannot learn which dependency failed unless the view gains a field analogous to `expiry_reason`. |
| `BaseOperation.ACTIVE_STATES` | `shakenfist/operations/baseoperation.py:55-56` | IGNORES SAFELY | `{created, queued, executing, complete}`; `abort` absent, exactly as `error` and `expired` are. Agent operations have no names, so the `from_db_by_ref` name lookup (`baseobject.py:423`) never applies to them. The only default-prefilter consumer is the instance delete sweep, next row. Note this set also omits `initial` and `preflight`, which pre-dates this work. |
| Prefilter resolution | `shakenfist/baseobject.py:1021-1030` | IGNORES SAFELY | `prefilter=None` resolves to `ACTIVE_STATES`; aborted operations drop out of default iteration like errored and expired ones. |
| Instance delete sweep of agent operations | `shakenfist/instance.py:1745-1748` | IGNORES SAFELY | `AgentOperations([...])` uses the active prefilter, so a queued (blocked) operation is deleted with its instance, while an aborted one is not soft-deleted -- the hard-delete sweep reaps it instead because `abort` is in `FINAL_OBJECT_STATES`. A deleted instance's blocked operations become `deleted`, which is the "failed dependency" outcome for any cross-instance dependent (see the queue list). |
| `constants.FINAL_OBJECT_STATES` | `shakenfist/constants.py:204-209` | IGNORES SAFELY | Already `{deleted, complete, abort, expired}`. An aborted operation is hard-deleted after `CLEANER_DELAY` (`config.py:826`, 3600 s; `_deleted_object_delay()` at `scheduled_tasks.py:939-942` only shortens it for `*_op` types, and `agentoperation` is not one). Consequence worth documenting: `GET /agentoperations/<uuid>` for an aborted operation returns 404 after an hour, same as `complete` today. |
| Hard-delete sweep | `shakenfist/daemons/cluster/scheduled_tasks.py:1000,1090,1146,1185` | IGNORES SAFELY | All four read `FINAL_OBJECT_STATES` and are type-agnostic. Aborted operations are counted, paged and deleted with no change. |
| `Instance.agent_operation_next` terminal branch | `shakenfist/instance.py:3279-3286` | IGNORES SAFELY | Pops any state in `TERMINAL_STATES` and continues, so an aborted head is retired on the next pass and the following entry considered. Safe only because of the `TERMINAL_STATES` change; see the framing. |
| `Instance.agent_operation_next` unknown-state branch | `shakenfist/instance.py:3288-3301` | BREAKS (if `abort` is added to `state_targets` but not to `TERMINAL_STATES`) | Logs an error and `break`s without popping, by design ("preserve order rather than guessing"). An aborted head would then block its instance's queue permanently and log an error every five seconds. Not reachable today; becomes reachable the moment the cascade writes the state. The branch is correct and should stay; the phase that adds the state must update `TERMINAL_STATES` in the same commit. |
| `Instance.agent_operation_next` queued branch and deadline check | `shakenfist/instance.py:3228-3246` | NEEDS UPDATE (2) | A `queued` head whose `deadline_passed()` is expired and popped, and the next entry considered. This is the enforcement point that fires for a dependency-blocked head, and it fires before any dependency evaluation provided the dispatcher calls `agent_operation_next()` first (it does, `main.py:2071`). The deadline is an absolute time computed at request receipt, so queue time accrues, and time spent blocked is queue time. Two gaps for the evaluation phase: a blocked operation that is *not* the head is not deadline-checked until it reaches the head; and an operation with `deadline_seconds=0` and a live progress timeout has no stored deadline and falls back to `AGENT_OPERATION_MAX_DEADLINE` (86400 s) anchored on its last state transition (`agentoperation.py:262-320`), so the deadline alone is no bound on a stall. |
| `Instance.agent_operation_next` initial/preflight branch | `shakenfist/instance.py:3248-3269` | IGNORES SAFELY | Breaks without popping and without a deadline check, leaving enforcement to preflight and the reaper. An aborted operation is not in these states. The note for evaluation: a blocked operation must stay `queued`. If it were held in `preflight` it would be exempt from `agent_operation_next()`'s deadline check and eventually expired by `expire_unpromoted_head()` (`main.py:710-755`) at deadline plus `UNPROMOTED_HEAD_GRACE` (60 s), with the wrong reason text. |
| Instance external view queue | `shakenfist/instance.py:792-797` | NEEDS UPDATE (5) | Pre-existing bug, relevant here: it reads `ops.get('queued', [])` but the attribute key is `queue` (`:3204,3315`), so `agent_operations_queue` is always empty and no test references it. The capability token `include-queued-agent-operations` (`external_api/app.py:508`) therefore advertises nothing. If queue visibility matters for explaining a blocked head, this needs fixing; the per-instance listing `GET /instances/<ref>/agentoperations` (`external_api/agentoperation.py:211-228`) reads the right key and is the working surface. |
| `STATE_ABORT` users elsewhere | `shakenfist/daemons/network/maintain.py:68`, `shakenfist/schema/operations/baseclusteroperation.py:33`, `operations/baseoperation.py:289-295,680,1049-1060`, `daemons/queues/workitem.py:153`, `daemons/network/workitem.py:542,573` | IGNORES SAFELY | All are about cluster operations, whose `state_targets` are separate from `AgentOperation`'s. No code branches on the object type's state being `abort` for agent operations. |
| `node_aop_op._preflight` | `shakenfist/operations/node_aop_op.py:110-155` | IGNORES SAFELY | `!= STATE_PREFLIGHT` early returns at `:121` and `:151` make an aborted (or expired, or deleted) operation a silent no-op. |
| `node_aop_op.dispatch_task` error path | `shakenfist/operations/node_aop_op.py:105-108` | IGNORES SAFELY | Was unguarded in the earlier audit; now `aop.fail()`, a no-op from `TERMINAL_STATES`. Same for `:134`. |
| Sidechannel executor exit guard | `shakenfist/daemons/sidechannel/main.py:844-853` | IGNORES SAFELY | `== STATE_EXECUTING` guard; an operation in any other state is left alone. |
| Sidechannel terminal-during-dispatch check | `shakenfist/daemons/sidechannel/main.py:1383-1392` | IGNORES SAFELY | `in TERMINAL_STATES`: covers `abort` for free, and is the backstop for a replaced dispatcher generation that starts an executor on an operation the current generation has just aborted. |
| Sidechannel executing write and completion write | `shakenfist/daemons/sidechannel/main.py:1426-1428,1583-1587` | IGNORES SAFELY | Both guarded by the terminal check above or by `== STATE_EXECUTING`. |
| Sidechannel `fail()` call sites | `shakenfist/daemons/sidechannel/main.py:490,495,852,889,1349,1559,1904` | IGNORES SAFELY | All via `fail()`, which returns early from a terminal state. |
| Sidechannel error-abort check | `shakenfist/daemons/sidechannel/main.py:1303-1330` | IGNORES SAFELY | Tests `ERROR` and `EXPIRED` only, deliberately not the whole of `TERMINAL_STATES` (see its docstring). `abort` is only written to a `queued` operation the dispatcher has not handed to an executor, so no running executor can observe it. Adding `abort` here is harmless and would be defensible if an `executing -> abort` edge were ever added; do not add that edge. |
| `reap_instance_executors` and `_resolve_stuck_queue_head` | `shakenfist/daemons/sidechannel/main.py:1727,1793-1900` | IGNORES SAFELY | Acts only on an `initial`/`preflight` head (`:1862`) or an `executing` head (`:1870`). A `queued` head returns at `:1870` untouched, so a blocked head is never expired, resolved or disturbed by the reaper. |
| `expire_unpromoted_head` | `shakenfist/daemons/sidechannel/main.py:703-755` | IGNORES SAFELY | Keyed on `UNPROMOTED_HEAD_REASONS` (`initial`, `preflight`); never sees `queued` or `abort`. |
| `resolve_abandoned_operation` and `operation_is_retryable` | `shakenfist/daemons/sidechannel/main.py:591-690` | IGNORES SAFELY | Reached only for an `executing` operation; the requeue write at `:677` is `executing -> queued`. Retry of a dependent re-enters the queue at the head it already holds. |
| Agent operation API: get, delete | `shakenfist/external_api/agentoperation.py:107-126` | IGNORES SAFELY | State passed through opaquely. `delete()` works provided `abort -> deleted` is in `state_targets`. |
| Agent operation API: instance listing | `shakenfist/external_api/agentoperation.py:211-228` | IGNORES SAFELY | Renders `external_view()` of every entry in `queue` or `all`. An aborted operation stays in `queue` until the next `agent_operation_next()` pass pops it, so a client can briefly see an `abort` in the "still in flight" list; same as `error` today. |
| Agent operation API: creating endpoints | `shakenfist/external_api/instance.py:2334-2342,2401-2409,2467-2475` | NEEDS UPDATE (1) | Today they only set `preflight`/`queued` on fresh operations and never read state. The new parameters land here; the capability token gate belongs in `external_api/app.py` beside `agentoperation-deadlines` (`:508-515`). Each endpoint also refuses unless the instance's agent is `ready` at submission (`:2283,2381,2444`), which says nothing about whether it stays ready while the operation waits. |
| `sf-ctl` object type list | `shakenfist/client/ctl.py:843` | IGNORES SAFELY | Type names only. |
| Metrics, health checks, eventlog, cleaner | (none found) | IGNORES SAFELY | No Prometheus metric, gauge or health check enumerates agent operations, their states or queue lengths. The only counters touching them are the generic deleted-object sweep metrics in `scheduled_tasks.py`, which are type-labelled. |
| `docs/developer_guide/state_machine.md` | `docs/developer_guide/state_machine.md:20-84` | NEEDS UPDATE (5) | Documents the current eight states and the mermaid graph; needs an `abort` node, `queued --> abort` (and any other source edge) and `abort --> deleted`, plus prose distinguishing it from `error` and `expired`. Its closing sentence that "only `expired` is currently swept for hard deletion" is already stale: `complete` and `abort` are in `FINAL_OBJECT_STATES` too. |
| `docs/operator_guide/agent_operations.md` | `docs/operator_guide/agent_operations.md:15-28,41-47,215-230` | NEEDS UPDATE (5) | "Every other queued operation against the same instance, however unrelated, waits behind it" is written about a wedged operation; it becomes true of a deliberate wait too and needs a section on the dependency-wait budget, since the deadline paragraphs treat queue time as incidental. |
| `docs/developer_guide/api_reference/agentoperations.md` | `docs/developer_guide/api_reference/agentoperations.md:1-30` | NEEDS UPDATE (5) | Describes the timing fields and `expiry_reason`; needs the dependency parameters, the `abort` state and whatever field carries its cause. |

#### CI suite (`shakenfist/deploy/shakenfist_ci/`)

| Site | file:line | Verdict | Notes |
|---|---|---|---|
| `AGENT_OPERATION_FAILED_STATES` | `base.py:1612` | NEEDS UPDATE (2) | `('error', 'expired', 'deleted')`. Three consumers share it: `_await_command` (`:1618-1634`), `_await_agentop_complete` (`:1981-1997`) and `_await_agentop_state` (`smoke_ci_tests/test_agentop_deadlines.py:208`). A test awaiting an aborted operation would poll to its timeout (900 s for `_await_command`) instead of failing at once. Add `'abort'` in the same phase as the first functional test that cascades. |
| `AGENT_OPERATION_TIMEOUT` | `base.py:1605` | IGNORES SAFELY | 900 s backstop; comfortably above a bounded dependency wait. |
| Agent exception tuple | `base.py:91-110` | IGNORES SAFELY | Enumerates the three client exceptions; `abort` surfaces as `AgentOperationFailed` once the client knows the state. |
| Other agent operation awaits | `smoke_ci_tests/test_agentops.py`, `guest_ci_tests/test_agentops.py`, `instance_hotplug.py`, `cluster_ci_tests/test_floating_ips.py:63` | IGNORES SAFELY | All go through the helpers above or the client's `await_agent_command` and `await_agent_fetch`. None submits a dependency, so none can see an `abort`. |
| Cluster operation terminal sets | `cluster_ci_tests/test_api.py:49,62`, `test_per_object_operations.py:12`, `test_observational_sweeps.py:23` | IGNORES SAFELY | Cluster operations; already include `abort`. |
| `load_budget.py` | `load_budget.py:187,354,387` | NEEDS UPDATE (2) | Budgets the suite's agent operation polling against the database load budget. A blocked head adds a steady read load (see the queue list), and `shakenfist/data/database_load_budget.yaml:311-323` derives the sidechannel per-instance term from `DISPATCH_CHECK_INTERVAL`; a unit test fails on drift. |

#### client-python

| Site | file:line | Verdict | Notes |
|---|---|---|---|
| `TERMINAL_AGENT_OPERATION_STATES` | `shakenfist_client/apiclient.py:230-234` | NEEDS UPDATE (4) | Hand-maintained copy of `AgentOperation.TERMINAL_STATES`. A released client does not list `abort`, so `_await_agentop` would poll an aborted operation to its full deadline (the client-python#363 shape). Capability gating makes this unreachable *for the creating client*; see the BREAKS summary for who else polls these operations. Update the set, and keep the comment accurate. |
| `_await_agentop` | `apiclient.py:1418-1456` | IGNORES SAFELY | Once `abort` is in the set above, any non-`complete` terminal state raises `AgentOperationFailed` with the state in the message. No change beyond the set. |
| `await_agent_command` / `await_agent_fetch` | `apiclient.py:1877-1970,1972-2030` | IGNORES SAFELY | Catch `AgentOperationFailed` and enrich it; the "state" in the message is opaque. A blocked dependent simply consumes the caller's `timeout` and raises `AgentAwaitTimeout` with its non-terminal state, which reads correctly. |
| Failure enrichment helpers | `apiclient.py:1514-1550` | IGNORES SAFELY | `_agent_failure_context` and `_enriched_agent_failure` print `op["state"]` verbatim. |
| CLI exception handler | `shakenfist_client/main.py:163-170` | IGNORES SAFELY | Catches `AgentOperationFailed` generically. |
| CLI renderers | `shakenfist_client/commandline/instance.py:276-298` | IGNORES SAFELY | Print `agentop['state']` as an opaque string. |

#### client-python-k3s

| Site | file:line | Verdict | Notes |
|---|---|---|---|
| State constants | `shakenfist_client_k3s/cluster.py:118-122` | NEEDS UPDATE (5) | `AGENT_OP_FAILED_STATES = ('error', 'expired')`, `AGENT_OP_FINISHED_STATES = ('complete', 'deleted')`, `AGENT_OP_KNOWN_STATES` the union plus pending. `abort` is in none of them. |
| `await_idle` | `cluster.py:2321-2443` | BREAKS | Waits while any of the instance's operations (`all=True`, so including other clients' and historical ones) is in neither the finished nor the failed set (`:2384-2385`); an unrecognised state is deliberately treated as "still running" with a note (`:2391-2397`), and the loop has no timeout (only a stall warning, `:2428-2437`). The comment at `:107-110` justifies this with "the server moves every operation out of whatever state it is in within its deadline", which is false for a terminal state. A single `abort` operation left on an instance, created by any newer client, holds every released `await_idle` against that instance, and the `own_operations` failure check at `:2367` cannot raise because `abort` is not in `AGENT_OP_FAILED_STATES`. The wait is bounded rather than permanent, but only by the hard-delete sweep: the aborted operation's rows go after `CLEANER_DELAY` (3600 s) and the listing endpoint skips an entry whose rows are gone (`external_api/agentoperation.py:224`), at which point the wait proceeds. An hour of silent waiting inside a cluster install is still a break. |
| `await_fetch` | `cluster.py:2445-2460` | IGNORES SAFELY | Loops while pending; `abort` leaves the loop and `!= 'complete'` raises. |
| `await_execute` and `reap_execute` | `cluster.py:2462-2560` | IGNORES SAFELY | `await_execute` returns as soon as the state leaves the pending set, and `reap_execute` raises for anything not `complete`; an `abort` is reported as a failure. The docstring's claim that the check is "exhaustive" holds. |
| `execute_and_await` | `cluster.py:2593-2606` | NEEDS UPDATE (5) | Submits one operation per instance and then calls `await_idle`, so it inherits the `await_idle` hang; it is also the fan-out a declared chain replaces. |
| Health probe collection | `cluster.py:2820-2880` | IGNORES SAFELY | Pending operations are reported as abandoned; an unrecognised terminal state is reported as "not one this version recognises" (`:2870-2873`). |

#### sfui

The sfui repository (`/srv/kasm_profiles/mikal/vscode/src/shakenfist/sfui`)
still contains no references to agent operations (recursive grep for
`agent_operation|agentoperation|agentop`, case insensitive, is empty):
IGNORES SAFELY.

**Queue-order assumptions: does anything assume the queue drains promptly?**

Each item states what would misbehave if a healthy instance's queue
head sat undispatched for minutes, and where the obligation lands.

1. **The deadline fires, and it is the only enforcement point that
   does.** A blocked head is `queued`; `agent_operation_next()`
   (`instance.py:3228-3246`) expires and pops it when
   `deadline_passed()`, with the reason "the operation deadline passed
   while queued" and `expiry_reason = deadline`. The reaper ignores
   `queued` heads (`main.py:1870`) and `_preflight` has already run.
   Time spent blocked accrues against the deadline because it is
   absolute from request receipt. Consequences: (a) the dependency
   evaluation must run *after* `agent_operation_next()` in the
   dispatcher or an expired head could be cascaded rather than
   expired; (b) a dedicated wait budget is genuinely needed, because
   the default deadline is 600 s but a caller may ask for 86400 s, and
   `deadline_seconds=0` with a live progress timeout is unbounded up
   to the 86400 s backstop (obligation: evaluation phase); (c) only
   the *head* is deadline-checked, so a blocked operation behind
   another blocked operation is not expired until it becomes head.
2. **The dispatcher only looks at ready agents.** `_dispatch_loop`
   (`main.py:2008-2110`) skips an instance whose agent is not
   `ready` or `ready-degraded` (`:2060-2064`) before ever calling
   `agent_operation_next()`. A blocked head on an instance whose agent
   went away is neither evaluated nor deadline-expired for as long as
   the agent is away. Pre-existing for any queued head, but a
   dependency wait makes it likelier to be observed.
3. **Per-instance throttle and database cost.** `DISPATCH_CHECK_INTERVAL
   = 5` (`main.py:94`) is claimed before the read, so a blocked head
   is re-examined every five seconds. Today a dispatchable head is
   read once and handed to an executor; a blocked head costs an
   attributes read, an operation read and a state read every five
   seconds for the whole wait, plus the dependency's own state read(s),
   per blocked instance. The budget term at
   `shakenfist/data/database_load_budget.yaml:311-323` must be
   amended, and a terminal dependency should be cached in process.
4. **Dispatcher pass duration and the supervisor.** The dispatcher
   evaluates instances serially in one thread and records
   `dispatcher_last_pass` once per pass (`main.py:2096`);
   `supervise_dispatcher()` replaces it after 120 s of no pass
   (`:2122-2142`). The loop's own docstring records that a wedged gRPC
   read can consume about 90 s. Extra dependency reads inside the pass
   enlarge the window in which one wedged call starves every other
   instance on the node; the 300 s heartbeat log is unaffected. A
   dependency read that blocks must not be allowed to approach 120 s.
5. **`reap_instance_executors` does not disturb a blocked head**
   (see the table), but it also does not help one: if a blocked head
   is somehow left blocked forever there is no reaper for it. The
   dedicated budget is the only bound, which is why it must be
   enforced somewhere that runs without a live executor (the
   dispatcher pass is the candidate).
6. **Strict FIFO, and the stall it accepts.** Everything behind a
   blocked head waits, including a second operation that the same
   caller submitted *without* a dependency and operations from other
   callers. `agent_operation_next()` has no notion of a claim: it
   returns the head or nothing, and an `initial`/`preflight` head also
   returns nothing (`:3248-3269`).
7. **Cascade is lazy and ordered by queue position.** The dispatcher
   can only observe a dependency failure for an operation that has
   reached the head. A dependent that sits behind a long-running head
   remains `queued` in the API (not `abort`) for as long as the head
   runs, even though its dependency failed minutes earlier, and a chain
   (C depends on B depends on A) propagates one hop per head-arrival.
   Clients awaiting the tail see `queued` for the duration. The
   dedicated wait budget bounds the stall once the dependent is head
   but does not make a failure visible earlier.
8. **Hard-delete of a dependency looks like a missing one.** A
   dependency that completed, expired, aborted or was deleted is swept
   from the database after `CLEANER_DELAY` (3600 s,
   `FINAL_OBJECT_STATES`); an *errored* dependency is never swept
   (issue #3922). A dependent that reaches the head more than an hour
   after its dependency finished (possible because a caller may set a
   deadline of up to 86400 s) will find the dependency absent, and
   absence is ambiguous between "completed successfully" and
   "failed". Separately, `delete()` (`agentoperation.py:553-561`)
   removes the operation's static and attribute rows *before* setting
   `deleted`, so `AgentOperation.from_db()` returns nothing for a
   user-deleted dependency while its state row still reads
   `deleted`: the evaluator must read the dependency's *state* by
   UUID, not hydrate the object, or it will misclassify a deleted
   dependency as missing.
9. **A deleted blocked operation pops, but a deleted dependency
   cascades.** A user DELETE of the blocked head removes its static
   rows, so `agent_operation_next()` finds `from_db()` empty and pops
   it (`instance.py:3215-3220`): no change needed. The dependent of a
   deleted operation is the case in item 8.
10. **Retry interaction.** A retried operation is re-queued at the
    head it already holds (`main.py:677`), and `attempts` counts
    dispatches, so a blocked operation does not consume attempts.
    A dependency that is in a terminal state stays there, so re-evaluation
    after a retry is cheap and need not be repeated if cached.
11. **No metric or health check exists to be misled.** Nothing exports
    queue length or oldest-queued age, so a multi-minute head wait is
    invisible to operators except through the instance audit events and
    the INFO "Dispatching agent operation" log. Not an incompatibility,
    but the evaluation phase should log (and probably event) the first
    blocked evaluation and the cascade, or a stalled instance is hard
    to diagnose. The existing `inst.add_event('dispatching agent
    operation')` (`main.py:2082`) is emitted only on dispatch.
12. **CI awaits.** `_await_command` allows 900 s and
    `_await_agentop_complete` takes a per-call timeout, so a bounded
    wait fits. Any test that submits a dependent and a follower on one
    instance, expecting the follower to run, will be held for the
    budget; tests of the cascade should assert on `abort` using
    `_await_agentop_state` once `abort` is classified (see CI table).
13. **Instance externally visible queue is empty.** See the
    `agent_operations_queue` row: the key bug means the one surface
    designed to show "what is queued behind what" does not work.
14. **Other callers: `client-python-k3s`.** `await_idle` waits for an
    instance's *entire* operation list to settle, so a dependent
    waiting in the queue is simply waited for, which is correct; the
    hazard is the terminal-state one in the table.

**BREAKS verdicts**

Two things break, and neither is in the dispatcher. The first is
`client-python-k3s` `await_idle` (`cluster.py:2321-2443`): it classifies
an unknown state as still running, has no timeout of its own, and
justifies that with a premise (every state leaves within its deadline)
that `abort` falsifies. This matters beyond the k3s plugin because the
capability gate protects only the *creating* client: `await_idle` and
the CLI both read an instance's whole operation list (`all=True`), so a
single aborted operation created by a newer client stalls every
released client that polls the same instance -- for up to
`CLEANER_DELAY`, an hour by default, after which the hard-delete sweep
removes the operation and the listing stops reporting it. The claim that no released client
can be hung by a state it cannot cause holds for the clients that poll
their own operations (`_await_agentop`) and fails for list-polling
clients. Whether to treat that as acceptable, or to add the state to
`client-python-k3s` and ship it before the server change, is a
decision for the plan, not for this audit. The second is the
conditional BREAK in `agent_operation_next()`'s unknown-state branch
(`instance.py:3288-3301`): adding `abort` to `state_targets` without
adding it to `TERMINAL_STATES` wedges the queue permanently; this is a
single-commit obligation rather than a design question. The
unguarded error writes that the 2026-08 audit worried about no longer
exist (all go through `fail()`), so nothing in the sidechannel daemon
breaks on `abort`. Two further hazards are design obligations rather
than breakage: the ambiguity between a hard-deleted and a failed
dependency (queue list item 8) and the unbounded dedicated budget
interaction with `deadline_seconds=0` (item 1).

**Grep patterns used**

Server repository (`shakenfist/`, `docs/`, including
`shakenfist/deploy/shakenfist_ci/`, excluding `tests/` where noted):
`ACTIVE_STATES|state_targets|TERMINAL_STATES|FINAL_OBJECT_STATES`,
`STATE_(QUEUED|PREFLIGHT|EXECUTING|COMPLETE|ERROR|EXPIRED|DELETED|ABORT)`,
`'abort'|STATE_ABORT`, `'complete'|'executing'|'queued'|'preflight'`
(and double-quoted forms), `agentop|agent_operation|AgentOperation|AGENTOPERATION|agentoperation`,
`agent_operation_next|agent_operations|'queue'`,
`state\.value|\.state\b` within `daemons/sidechannel/main.py` and
`operations/node_aop_op.py`, `\.fail\(|\.expire\(`,
`hard_delete|STATE_DELETED|_deleted_object_delay|CLEANER_DELAY`,
`Gauge|Counter|Histogram|prometheus` intersected with files mentioning
agent operations, `AGENT_OPERATION_FAILED_STATES|\['state'\]`, and
`agent_operations_queue`.

client-python (`shakenfist_client/`): `TERMINAL_AGENT_OPERATION_STATES`,
`_await_agentop|await_agent_command|await_agent_fetch`,
`AgentOperationFailed|AgentAwaitTimeout`,
`agentop|agent_op|\['state'\]`, `expired|expiry_reason`.

client-python-k3s (`shakenfist_client_k3s/`):
`AGENT_OP_(PENDING|FAILED|FINISHED|KNOWN)_STATES`,
`aop\['state'\]|\.get\('state'\)|TERMINAL|'complete'|'error'|'expired'`.

sfui: recursive, case insensitive `agent_operation|agentoperation|agentop`.

Not checked: runtime behaviour on a live cluster (this audit is
static); the unit tests under `shakenfist/tests/` and the client test
suites were not audited, on the grounds that they use existing states
and gain new cases rather than failing.
