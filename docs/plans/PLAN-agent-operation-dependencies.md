# Dependency-aware agent operations

## Prompt

Before responding to questions or discussion points in this document,
explore the shakenfist codebase thoroughly. Read the cluster operation
dependency machinery (`depends_on` / `runs_after` on
`BaseClusterOperation` in `shakenfist/operations/baseoperation.py`,
the `dependency` schema model in
`shakenfist/schema/operations/baseclusteroperation.py`, and the
dequeue-time evaluation in `shakenfist/daemons/queues/workitem.py`),
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

This plan is a **placeholder**. It captures intent, the decisions
already made in discussion, and the known open questions, and is
intentionally light on implementation detail. Phase 0 will resolve
the open questions into a decisions section and re-cut the phase
table accordingly. It must not begin until
`PLAN-agent-operation-deadlines` has landed, because it builds
directly on that plan's expiry semantics, terminal-only queue pop,
and per-command capability registry.

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
  aborts it (the cascade); an in-flight dependency defers it.
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
4. **Deadlines are the cycle-breaker.** A dependency-blocked
   operation accrues queue time against its deadline (from
   `PLAN-agent-operation-deadlines`), so user-created dependency
   cycles resolve by expiry rather than deadlocking. This is why the
   deadlines plan is a hard prerequisite.
5. **Documentation with worked examples is a first-class
   deliverable**, not an afterthought phase: a developer-guide
   treatment of the agent operation model (ordering, independence,
   deadlines, dependencies, retry) plus a curated example-application
   suite starting with `client-python-k3s`, updated as features land.

## Design sketch

Mechanically, the extension is: add `depends_on` / `runs_after` (and
per-edge settle seconds) to `AgentOperation` static values and the
creating API endpoints; admit `ObjectType.AGENTOPERATION` to the
`dependency` schema's permitted operation types; and evaluate edges
at dispatch time in the sidechannel dispatcher, before an operation
is handed to an executor. The evaluation semantics mirror
`workitem.py`: missing dependency errors, failed dependency cascades
(abort/skip), in-flight dependency waits. The sidechannel has no
defer machinery — a blocked operation simply stays queued and is
re-evaluated on later dispatch passes, with the deadline bounding how
long that can continue.

The awaited-transaction experience is the client's: submit the chain,
await the final operation (which transitively awaits the rest), and
report per-operation outcomes from the chain on failure.

## Open questions (resolve in phase 0)

1. **Head-of-queue interplay.** Does a dependency-blocked operation
   at the head of the instance's FIFO queue block the operations
   behind it (strict linearity preserved, but one blocked chain
   stalls unrelated callers until it expires), or is it passed by
   operations that are themselves dispatchable (better utilisation,
   but declared-dependency operations then sit outside the implicit
   FIFO contract)? The answer probably follows from deciding whether
   declaring dependencies opts an operation out of implicit queue
   ordering entirely.
2. **Mixed-type edges.** Should an agent operation be able to depend
   on a *cluster* operation (e.g. "run this script after the
   artifact fetch completes"), or a cluster operation on an agent
   operation? `node_aop_op` already wraps agent operations in cluster
   operations, so the type system is adjacent; the question is
   whether the use cases justify the evaluation complexity now.
3. **Namespace boundaries.** Cross-instance edges presumably must be
   restricted to operations the caller could see anyway (same
   namespace, or namespace-trust rules). Decide and enforce at
   submission time.
4. **Failure attribution.** How a caller inspects *why* an operation
   was cascade-aborted: the cluster-op machinery emits
   "dependency is unsuitable" events; agent operations should surface
   the failed dependency in `external_view()` so the awaiting client
   can report the chain's first failure directly.
5. **Cheap cycle rejection.** Expiry breaks cycles eventually, but
   same-queue cycles (an operation depending on one behind it in its
   own instance queue) are detectable at submission time for a few
   database reads. Decide how much validation is worth doing
   synchronously in the API handler.
6. **Dispatch-pass cost.** Edge evaluation adds database reads per
   blocked operation per pass to a loop that is already careful about
   per-instance read cost (see the throttling comments in
   `_dispatch_loop`). Decide the evaluation cadence and caching.
7. **Example suite shape.** Where the suite lives (a docs section
   indexing sibling repositories, starting with `client-python-k3s`),
   what the second example is, and whether examples get CI that
   exercises them against a real cluster.

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

## Phases (to be re-cut by phase 0)

| Phase | Content |
|-------|---------|
| 0 | Resolve open questions; record decisions; re-cut this table |
| 1 | Schema and object: dependency fields on `AgentOperation`, `AGENTOPERATION` admitted to the dependency model, API parameters |
| 2 | Dispatch evaluation: same-instance edges, cascade, settle attribute |
| 3 | Cross-instance edges and namespace enforcement |
| 4 | client-python: chain submission helpers, await-the-chain UX, failure reporting |
| 5 | Documentation: developer-guide agent operation model, worked examples; `client-python-k3s` adopts dependencies (rolling update via one submitted chain) as the first example application |
| 6 | Push audit: runs `PUSH-AUDIT.md` over the accumulated diff of every phase in this plan against `develop`, not the last phase's diff alone. Findings land as their own pull request, and the plan is not complete until each is resolved or declined in writing here; if the audit finds nothing, that is recorded in one sentence |
