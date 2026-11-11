# Dependency-aware agent operations

## Prompt

Before responding to questions or discussion points in this document,
explore the shakenfist codebase thoroughly. Read the cluster operation
dependency machinery (`depends_on` / `runs_after` on
`BaseClusterOperation` in `shakenfist/operations/baseoperation.py`,
the `dependency` schema model in
`shakenfist/schema/operations/baseclusteroperation.py`, and the
dequeue-time evaluation, of which there are two near-identical
copies, in `shakenfist/daemons/queues/workitem.py` and
`shakenfist/daemons/network/workitem.py`),
the agent operation object and its queue
(`shakenfist/operations/agentoperation.py`,
`Instance.agent_operation_next` in `shakenfist/instance.py`), the
sidechannel dispatch loop
(`shakenfist/daemons/sidechannel/main.py`), and the deadlines plan
this plan is gated on
(`docs/plans/PLAN-agent-operation-deadlines.md`), whose design this
plan assumes has landed. The client-side surface lives in the sibling
`client-python` repository, and the first example application in the
sibling `client-python-k3s` repository. Ground your answers in what
the code actually does rather than guessing.

All planning documents go into `docs/plans/`.

This plan began as a **placeholder**: intent, the decisions already
made in discussion, and the known open questions, intentionally light
on implementation detail, with phase 0 to resolve those questions
into a decisions section and re-cut the phase table. Phase 0 has done
both -- see "Decisions from phase 0" below, which replaces the open
questions, and the re-cut table at the end -- so the sections above
it are the record of how the plan was conceived and the sections
below it are what will be built. Where the two disagree, phase 0's
decisions win and say so. It could not begin until
`PLAN-agent-operation-deadlines` had landed, because it builds
directly on that plan's expiry semantics, terminal-only queue pop,
and per-command capability registry. That plan is now complete (its
nine phases all merged, the last of them `9bca46412`), so the gate
is clear and phase 0 is in progress.

When we get to detailed planning, I prefer a separate plan file per
detailed phase, named for the master plan with `-phase-NN-descriptive`
appended before the `.md` extension.

I prefer one commit per logical change, and at minimum one commit per
phase. Do not batch unrelated changes into a single commit.

## Situation

Agent operations execute linearly per instance with independent
outcomes — ordering, not dependency; shell `;`, not `&&` (see the
failure semantics section of `PLAN-agent-operation-deadlines.md`).
Callers who need dependency semantics today either submit-and-await
each operation serially, or rely on the intra-operation command list
(which aborts remaining commands on error but is not composable from
the public API).

Meanwhile cluster operations already carry a full dependency
vocabulary, evaluated at dequeue in `daemons/queues/workitem.py`:

- `depends_on` — fate-sharing. A missing dependency errors the
  dependent operation; a dependency in `ERROR`/`DELETED`/`ABORT`
  aborts it (the cascade); an in-flight dependency defers it, but
  not forever — once the defer budget is spent
  (`DEPENDENCY_DEFER_BUDGET`, 30 defers of exponential backoff and
  so roughly six minutes) `abandon_dependency_wait()` aborts the
  dependent operation, because a dependency which never goes
  terminal must not park one indefinitely (issue #3992).
- `runs_after` — ordering only. Wait for the named operation to
  finish; its outcome is irrelevant; a missing operation is a logged
  warning, not fatal.

That is exactly the dependency-versus-ordering distinction agent
operations lack, with the cascade following declared edges rather
than queue adjacency — which is what makes it correct in a queue
shared by unrelated callers. A fate-shared chain of `depends_on`
edges is a transaction in every sense that is meaningful against a
running operating system (rollback is not).

Separately: agent operations are a fairly unique idea and subtle to
use well — this has become clear from how much design discussion the
deadlines plan required. The documentation needs to be genuinely
good, with worked examples, and the `client-python-k3s` plugin (which
orchestrates k3s clusters over the `sf-agent2` side channel, storing
cluster state in namespace metadata) is already a de-facto example
application. It should become the first of a curated suite of
example applications that adopt each new agent operation feature as
it lands, serving as both living documentation and functional
exercise of the feature set.

## Decisions already made

Settled in discussion (2026-08-14, the same thread that produced the
deadlines plan) and not open questions:

1. **Extend `depends_on` / `runs_after` to agent operations** rather
   than introducing a lane or session identifier or a new operation
   type. Dependencies are declared per-operation on the existing
   verbs; operations that declare nothing keep today's contract
   unchanged (linear order, independent outcomes). The cascade
   follows declared edges only.
2. **Cross-instance edges are in scope.** Agent operation state lives
   in MariaDB and is cluster-visible, so an operation on instance A
   depending on an operation on instance B is a state read at
   dispatch evaluation, the same read `workitem.py` performs for
   cluster operations. This enables fire-and-await orchestration: a
   rolling update submitted as one dependency chain across a fleet,
   awaited as a unit by awaiting its final operation.
3. **Settle delays are an attribute on the dependency edge** —
   "satisfied N seconds after the dependency completed" — not a
   sleep operation, which would hold an instance's executor slot to
   do nothing and require a healthy agent to run a no-op.
4. **Deadlines are the cycle-breaker.** *Amended by phase 0: there
   are no cycles to break.* As written, this said that a
   dependency-blocked operation accrues queue time against its
   deadline (from `PLAN-agent-operation-deadlines`) so that
   user-created dependency cycles resolve by expiry rather than
   deadlocking. The premise is false. A `depends_on` edge can only
   name an operation which already exists: the API assigns every
   agent operation's UUID, a client cannot pre-declare one, an edge
   is static at creation and there is no API to add one later. Every
   edge therefore points backwards in creation order and the graph is
   a DAG by construction, which is also why decision 6 below does no
   graph walk.

   The first clause still holds and is still useful — queue time does
   accrue against the deadline, so a blocked operation is not
   immortal — but it is not what makes this plan safe, and it is not
   a sufficient bound either, because a caller may ask for a deadline
   of a day (see decision 2 below). What the plan actually needs from
   `PLAN-agent-operation-deadlines`, and why that plan remains a hard
   prerequisite, is its queue-time expiry at dequeue, its
   terminal-only lazy queue pop, its node-local reaper, and the
   capability-token precedent which lets a new terminal state be
   introduced without hanging a released client's await.
5. **Documentation with worked examples is a first-class
   deliverable**, not an afterthought phase: a developer-guide
   treatment of the agent operation model (ordering, independence,
   deadlines, dependencies, retry) plus a curated example-application
   suite starting with `client-python-k3s`, updated as features land.
   Note that this is no longer greenfield:
   `PLAN-agent-operation-deadlines` phase 7 wrote
   `docs/operator_guide/agent_operations.md` (one executor per
   instance, the two budgets and where they are enforced, `expired`
   versus `error`, retry and the reaper, what to tune), and the API
   surface is in `docs/developer_guide/api_reference/`. What does not
   exist is a developer-guide page for the model itself.

## Design sketch

Mechanically, the extension is: add `depends_on` / `runs_after` (and
per-edge settle seconds) to `AgentOperation` static values and the
creating API endpoints; admit `ObjectType.AGENTOPERATION` as a
permitted dependency target; and evaluate edges at dispatch time in
the sidechannel dispatcher, before an operation is handed to an
executor.

Two notes on that sketch, from phase 0's survey of the tree. The
permitted-types set is `VALID_CLUSTER_OPERATIONS` in the dependency
schema, whose only consumer is the `dependency` model's own
validator — so widening it is mechanically cheap, but it admits the
edge in both directions and so answers open question 2 by accident,
in favour of mixed-type edges, with no evaluator on the cluster side
which can read agent operation state. The API parameter has a worked
precedent rather than being novel: an array of structured objects is
declared the way `arrayofdiskspec` and `arrayofnetworkspec` are
(`shakenfist/external_api/base.py:1038-1039`), each nesting a schema
which `shakenfist/external_api/validation.py:641-660` compiles into a
list of nested fields. The evaluation semantics mirror
`workitem.py`: missing dependency errors, failed dependency cascades
(abort/skip), in-flight dependency waits. The sidechannel has no
defer machinery — a blocked operation simply stays queued and is
re-evaluated on later dispatch passes, with the deadline bounding how
long that can continue.

The awaited-transaction experience is the client's: submit the chain,
await the final operation (which transitively awaits the rest), and
report per-operation outcomes from the chain on failure.

## Decisions from phase 0

Phase 0 resolved the seven open questions this section used to hold.
Each decision below says which question it answers, and the reasoning
is kept because the next reader of this plan is the person
implementing it. The measurement and the consumer audit behind them
are in
[the phase 0 plan](PLAN-agent-operation-dependencies-phase-00-decisions.md).

1. **Strict FIFO is preserved: a dependency-blocked operation keeps
   its place in its instance's queue and blocks the operations behind
   it.** Declaring a dependency does not opt an operation out of
   queue ordering. (Open question 1.)

   `Instance.agent_operation_next()` is built on the head of the
   queue being the unit of recovery, and its crash-safety argument is
   that an instance has one executor; returning a dispatchable
   non-head candidate replaces that head pointer with per-operation
   claims, which `PLAN-agent-operation-deadlines.md` named as the
   reason concurrency is its own master plan. The alternative is also
   not merely "better utilisation": it gives the instance queue two
   contracts, so "put a file, then execute it" would hold only for
   operations which declared nothing.

   The cost is that one caller's blocked chain stalls operations
   another caller submitted against the same instance.
   **Whether that happens in practice could not be measured, and the
   plan records it as an assumption rather than a finding.** Over the
   22.5 days of `sfcbr` history Loki retains, half of all agent
   operations (913 of 1,804) arrived at an instance which already had
   an unfinished operation and the deepest queue reached eleven, so
   deep queues are real. But nothing records who submitted an
   operation: a request id is unique per operation, the API request
   log's `identity` field is null, and an operation's namespace is
   copied from its instance rather than from the caller, so "no
   cross-namespace overlap" is true by construction. Every deep queue
   observed was a burst of different commands on one instance, which
   is what a single client fanning out looks like and equally what
   several cooperating clients would look like. Treating "effectively
   one caller at a time per instance" as an operating assumption is
   therefore a choice, taken deliberately, and a reader who needs it
   to be a fact needs a submitter field on the operation first.

2. **The stall decision 1 accepts is bounded by a dedicated
   dependency-wait budget, defaulting to 300 seconds, and not by the
   caller's deadline.** A blocked head whose wait exceeds the budget
   is aborted, in the manner of `abandon_dependency_wait()`. The
   budget is wall-clock from the first blocked evaluation, recorded
   as an operation attribute on first block, rather than a count of
   dispatcher passes: the dispatcher's pass rate is throttled and
   variable where the cluster dispatchers' backoff is not, and one
   write beats one write per pass. It is a configuration option.

   The deadline cannot be that bound.
   `AGENT_OPERATION_DEFAULT_DEADLINE` is 600 seconds but a caller may
   ask for `AGENT_OPERATION_MAX_DEADLINE`, 86400, so with deadlines
   as the only bound one caller could stall an instance's queue for a
   day by asking for a day.

   300 seconds is chosen against measurement rather than by analogy.
   A dependency wait legitimately contains the dependency's own queue
   wait and its execution, which on `sfcbr` run to 39 seconds at p95
   and 142 seconds at worst for a single hop, so the budget is about
   twice the worst single-hop latency that cluster has produced. A
   chain more than two hops deep on a busy instance could legitimately
   exceed it; that is documented rather than designed around, because
   the budget aborts an operation and does not corrupt anything.

3. **A failed `depends_on` moves the dependent operation to `abort`,
   a terminal state `AgentOperation` does not reach today.** Not to
   `error`, which would claim the operation failed when its
   dependency did, and not to `expired`, which would claim a timing
   budget ran out. `abort` is the vocabulary the cluster-operation
   cascade already uses, and it is already in
   `constants.FINAL_OBJECT_STATES`, so an aborted operation is
   hard-delete swept where an errored one is not (issue #3922).

   Introducing a terminal state is only safe because the dependency
   parameters are gated behind an `API_CAPABILITIES` token, the way
   `deadline_seconds` was: an operation can only reach `abort` if a
   client which declared a dependency created it, and such a client
   knows the state. That argument covers a client which polls the
   operations it created and **not** one which polls an instance's
   whole operation list, which phase 0's audit found
   `client-python-k3s` does. Its `await_idle()` treats an
   unrecognised state as still running by design and has no timeout,
   so one aborted operation would stall a released plugin on that
   instance until the hard-delete sweep cleared it an hour later.
   Teaching `client-python-k3s` the state, in its own pull request
   and release, is therefore a **prerequisite of phase 1** rather than
   part of phase 5's adoption work.

   Two obligations come with the state, both single-commit: `abort`
   must enter `TERMINAL_STATES` in the same commit that gives
   `state_targets` an edge to it, or `agent_operation_next()` takes
   its unknown-state branch and wedges the instance's queue
   permanently; and `state_targets` needs `abort -> deleted`, because
   `delete()` removes an operation's rows before assigning the state.

4. **Mixed-type edges between agent and cluster operations are out of
   scope**, and the mechanism in decision 5 keeps them out by
   construction. (Open question 2.)

   The use case is real — "run this script after the artifact fetch
   completes" — and is expressible today by awaiting the fetch
   client-side. Admitting it would mean evaluating edges in the two
   cluster-operation dispatchers as well, whose cascade helpers reach
   for `defer()`, `current_defer_count` and a schema model's
   `target_fields`, none of which an agent operation has. Recorded
   under Future work and filed as an issue.

5. **An agent operation's edges use their own model and their own
   permitted-target set, not the shared `dependency` model.** Phase 0
   found that widening `VALID_CLUSTER_OPERATIONS`, as the design
   sketch proposed, would admit the edge in both directions and so
   settle open question 2 by accident. Instead
   `VALID_DEPENDENCY_OPERATIONS` admits `ObjectType.AGENTOPERATION`
   for the agent edge model alone, the cluster validator keeps the set
   it has, and a cluster operation still cannot name an agent
   operation. The per-edge settle delay (decision 3 of the section
   above) is a field on the agent edge model for the same reason: on
   the shared model it would appear on every cluster-operation edge
   with nothing honouring it.

6. **Edges are validated at submission, and that is also the answer
   to cycles.** (Open questions 3 and 5.) The API handler reads each
   named dependency once and refuses with a 400 if it does not exist,
   is not an agent operation, or belongs to a namespace the caller
   could not read. It does not walk the graph, because there is no
   cycle to find: see the amended decision 4 above. Namespace is
   checked at submission and never re-checked at evaluation — the
   edge was authorised when it was declared, and re-checking would let
   a later namespace change break a chain already in flight.

7. **Why an operation was cascade-aborted is on the wire.** (Open
   question 4.) `external_view()` carries the failed dependency's
   type, UUID and state, in the manner of `expiry_reason`. The cause
   cannot ride on `error_message`, because the `error` setter refuses
   any state not ending in `error`, so without a field of its own the
   reason would live only on the state row and an instance audit
   event, neither of which a client sees. Issue #4075 is the
   precedent: a reason not on the wire is a defect, so this one is on
   the wire from the start.

8. **Edges are evaluated in the sidechannel dispatcher, between
   `agent_operation_next()` and `start_instance_executor()`.** (Open
   question 6.) Not inside `agent_operation_next()`, which is the
   tempting precedent because the deadline check lives there, but
   which holds the `agent_operations` attribute lock across every
   read it makes. In the dispatcher a blocked operation simply stays
   queued, which is what the sidechannel's lack of defer machinery
   makes free, and a cascade abort is a state write the next pass's
   lazy pop retires. Evaluating after `agent_operation_next()` also
   means an expired head is expired rather than cascaded.

   Cost control, because this loop's per-instance read cost is
   budgeted: an operation which declares no edges reads nothing; the
   reads hang off the existing per-instance throttle
   (`DISPATCH_CHECK_INTERVAL`, five seconds) rather than adding a
   cadence; a dependency observed in a terminal state is cached in
   process for the life of the dispatcher generation, since terminal
   is absorbing; and the `(GetInstanceAttributes, sidechannel)` entry
   in `shakenfist/data/database_load_budget.yaml` is amended in the
   same phase as the code, as a code-derived term. The dispatcher's
   supervisor replaces a thread which has not completed a pass in 120
   seconds, which is the ceiling on how much work a pass may grow by.

   One consequence to document rather than fix: the cascade is lazy,
   so a dependent which has not reached the head of its queue reads
   as `queued` through the API even after its dependency has failed.

9. **An evaluator reads a dependency's state by UUID and never
   hydrates the object.** `AgentOperation.delete()` removes an
   operation's static and attribute rows before assigning `deleted`,
   so `from_db()` returns nothing for a user-deleted dependency and an
   evaluator which hydrated would misclassify it as missing. There is
   a related ambiguity with no clean answer, which the documentation
   must state: a dependency which completed, aborted, expired or was
   deleted is swept an hour after it finished, while an errored one
   never is, so a dependent which reaches the head long afterwards
   finds the dependency absent and cannot tell success from failure.

10. **Cross-instance edges cost nothing extra at evaluation and land
    with same-instance edges.** The evaluator reads a dependency's
    state by UUID and never looks at which instance owns it; the only
    instance-aware work is decision 6's namespace check in the API
    handler. The phase which was to do them separately is folded into
    the evaluation phase below.

11. **The documentation deliverable is a developer-guide page plus an
    example index.** (Open question 7.) The model treatment —
    ordering, independence, deadlines, dependencies, retry, with
    worked examples — goes to a new
    `docs/developer_guide/agent_operations.md`, because
    `docs/operator_guide/agent_operations.md` is a tuning and
    enforcement page for operators while the dependency vocabulary is
    an API contract. The example suite is an index section naming
    sibling repositories, with `client-python-k3s` first and its
    rolling update as the worked example. No new CI exercises the
    examples: each example repository has its own. The second example
    is chosen when there is a second feature to exercise, and is filed
    as an issue rather than invented here.

## Non-goals

- Rollback. Compensating actions against a running operating system
  are the caller's domain; the transaction concept here is ordering
  plus fate-sharing, nothing more.
- Concurrent execution of independent agent operations on one
  instance. Dependency edges express *ordering constraints*; they do
  not change the one-executor-per-instance execution model. If
  parallel lanes are ever wanted, that is the separate re-engineering
  recorded in the deadlines plan's non-goals.
- Agent-side cancellation of in-flight work (tracked from the
  deadlines plan; would sharpen cascade behaviour but is not
  required by it).

## Phases

Re-cut by phase 0. Two changes from the table this replaces: the
cross-instance phase is folded into the evaluation phase, because
decision 10 found that cross-instance edges cost nothing extra at
evaluation, and the phases after it move up one. Phase 1 also carries
a prerequisite which is not a phase of this plan: `client-python-k3s`
must learn the `abort` state, in its own pull request and release,
before this repository can emit one (decision 3).

| Phase | Plan | Status | Content | Merged |
|-------|------|--------|---------|--------|
| 0 | [PLAN-agent-operation-dependencies-phase-00-decisions.md](PLAN-agent-operation-dependencies-phase-00-decisions.md) | Complete | Resolve the open questions into the decisions section above, and re-cut this table. Its survey found five of this plan's factual claims wrong or incomplete; its measurement found that instance queues do get deep but that no log can say whether a deep one held two submitters; its consumer audit found one firm break outside this repository and one single-commit obligation inside it | `4c14523a7b` |
| 1 | [PLAN-agent-operation-dependencies-phase-01-schema-and-api.md](PLAN-agent-operation-dependencies-phase-01-schema-and-api.md) | In progress | Schema, object and API: `depends_on` and `runs_after` on `AgentOperation` with an additive table migration, the agent edge model and `VALID_DEPENDENCY_OPERATIONS` (decision 5), per-edge settle seconds, `abort` in `state_targets` and `TERMINAL_STATES` in one commit (decision 3), the parameters on the three creating endpoints with their declarations and capability token, and submission-time validation (decision 6). Prerequisite: `client-python-k3s` has learned `abort` and released -- met, by [client-python-k3s#164](https://github.com/shakenfist/client-python-k3s/pull/164) in `v0.4.0`. Its plan also lands the `dependency_failure` attribute column phase 2 writes, since this is the schema phase | — |
| 2 | — | Not started | Dispatch evaluation (decisions 1, 2, 8, 9, 10): same-instance and cross-instance edges evaluated in the dispatcher between `agent_operation_next()` and `start_instance_executor()`, the cascade to `abort`, the settle delay, the dependency-wait budget and its configuration option, the failed dependency on the wire in `external_view()` (the `dependency_failure` attribute column itself lands in phase 1, which is the schema phase, so this phase writes and exposes a column which already exists), the `database_load_budget.yaml` amendment, and the terminal-dependency cache | — |
| 3 | — | Not started | client-python: chain submission helpers, await-the-chain UX, failure reporting from the chain's first failure, and `abort` in `TERMINAL_AGENT_OPERATION_STATES`. Lands in the sibling repository, so its plan file lives there, following the `PLAN-agent-operation-deadlines` phase 6 precedent | — |
| 4 | — | Not started | Documentation and examples (decision 11): a new `docs/developer_guide/agent_operations.md` for the model, the dependency vocabulary in the API reference, the lazy-cascade and swept-dependency ambiguities written down, the example index, and `client-python-k3s` adopting dependencies (rolling update as one submitted chain) as the first example application | — |
| 5 | — | Not started | Push audit: runs `PUSH-AUDIT.md` over the accumulated diff of every phase in this plan against `develop`, not the last phase's diff alone. Findings land as their own pull request, and the plan is not complete until each is resolved or declined in writing here; if the audit finds nothing, that is recorded in one sentence | — |

The `Merged` column records what put each phase on `develop`: the
merge commit of its pull request, or an explicit `first..last` range
where the phase landed directly. Phase 0 landed as pull request
[#4827](https://github.com/shakenfist/shakenfist/pull/4827), merge
commit `4c14523a7b`; every other cell reads `—` because nothing
else has landed yet.

## Future work

Work this plan has deliberately declined or deferred, so that it is
not lost:

- **Mixed-type dependency edges** between agent operations and
  cluster operations, declined by decision 4 for the evaluation
  complexity rather than for want of a use case. Filed as
  [#4824](https://github.com/shakenfist/shakenfist/issues/4824).
- **A second example application** for the agent operation example
  suite, to be chosen when there is a second feature worth
  exercising. Filed as
  [#4825](https://github.com/shakenfist/shakenfist/issues/4825).
- **A submitter identity on an agent operation.** Decision 1 rests on
  an operating assumption which phase 0 could not measure, because
  nothing records who created an operation. A submitter field would
  make the question answerable and is not otherwise needed by this
  plan.

### Bugs found during this work

- **`agent_operations_queue` in an instance's external view is always
  empty.** `Instance.external_view()` reads the `queued` key of the
  `agent_operations` attribute, but every writer and every other
  reader uses `queue`, so the field is empty and the
  `include-queued-agent-operations` capability token advertises
  nothing. Found by phase 0's consumer audit, predates this plan, and
  filed as
  [#4826](https://github.com/shakenfist/shakenfist/issues/4826)
  rather than fixed here. **Fixed since**, in pull request
  [#4832](https://github.com/shakenfist/shakenfist/pull/4832) (merge
  `29b9a16226`): the `queue` key now has a single reader,
  `Instance.agent_operations_queued()`, and the external view goes
  through it. Phase 1's survey confirmed it, which matters to this
  plan only because an instance's external view embeds each queued
  operation's whole `external_view()`, so a field added to an agent
  operation's view appears in the instance's too.
