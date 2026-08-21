# Bound the size of DatabaseService gRPC replies

## Prompt

Before responding to questions or discussion points in this document,
explore the shakenfist codebase thoroughly. Read the gRPC service
definition (`protos/database.proto`), the three-layer client in
`shakenfist/mariadb.py` (particularly `_grpc_call` and its retry and
channel-rebuild logic, `_grpc_get_objects_by_state`,
`_grpc_get_object_events`, and `get_active_blob_uuids`), the channel
options in `shakenfist/util/grpc_channel.py`, the server setup and
thread pool in `shakenfist/daemons/database/main.py`, the object
iterator machinery in `shakenfist/baseobject.py`
(`DatabaseBackedObjectIterator._find` and its subclass overrides), and
the sweeps in `shakenfist/daemons/cluster/scheduled_tasks.py` and
`shakenfist/daemons/cleaner/main.py` which consume list-shaped replies.
Ground your answers in what the code actually does today rather than
guessing.

Where a question touches on external concepts (gRPC message framing and
size limits, server-streaming semantics and flow control, protobuf
serialisation cost, MariaDB keyset versus OFFSET pagination and index
selection), research as needed to give a confident answer. Flag any
uncertainty explicitly.

Consult `ARCHITECTURE.md` for the system architecture overview and
`CLAUDE.md` for the database access patterns, the three-layer client
convention, and the SQL-pushdown priority. `docs/developer_guide/
coding_rules.md` carries the rule this plan's motivating defect
produced ("`or []` is a decision about what a failed read means"), and
`docs/developer_guide/database_internals.md` describes the object cache
and gRPC reliability machinery this plan must not regress.

<!-- shared-block: plan-file-conventions v1 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section:

  | Phase | Plan | Status |
  |-------|------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

`DatabaseService` has 226 RPCs and 68 `repeated` fields across its
messages. Several of those replies are unbounded by construction:
`GetObjectsByState` returns every uuid of a type in a set of states,
`GetObjectEvents` returns an object's event history, and the various
`Get*Uuids` / `Get*Attributes` list calls return whatever the cluster
happens to contain. Nothing in the protocol, the server, or the client
places a ceiling on any of them. The only ceiling is gRPC's own message
size limit, which is a transport-layer accident rather than a designed
bound, and which fails in the least useful way possible: the client
receives no data at all and an opaque `RESOURCE_EXHAUSTED`.

The sfcbr cluster has now crossed that limit twice, from two different
daemons, on two different RPCs (issue 3638):

- `GetObjectEvents`, nine failures on 2026-07-31 from sf-api gunicorn
  workers, replies up to 6,940,655 bytes against the 4,194,304 default.
- `GetObjectsByState` for `node_inst_op`, one failure on 2026-08-04
  from sf-cluster, 4,419,628 bytes.

This is the same failure class as the system-namespace service-key
bloat of issues 3521 and 3522, but that fix purged one pathological
object; it did not bound anything.

Two things have already been done about it, both stopgaps, both on the
`issue-fix-3638` branch:

1. The client receive cap was raised from the 4MiB default to 32MiB in
   `shakenfist/util/grpc_channel.py`. This clears observed traffic with
   roughly 4.5x headroom. It does not fix anything -- it moves the
   cliff, and deliberately does not move it far, because sf-database
   has to serialise whatever it sends and an arbitrarily generous
   client cap trades a fast client-side failure for memory pressure on
   the database tier.
2. The callers that consumed these replies were made honest about
   failure. `get_objects_by_state()` returns `None` on a failed read
   and `[]` for no matches, and three of the four call sites collapsed
   the two with `or []`. One of those was actively dangerous: the
   cleaner uses `get_active_blob_uuids()` as a *complement* set and
   unlinks every blob file on disk not named in it, so a single
   oversized reply was an instruction to delete a node's entire blob
   store. That accessor now raises `DatabaseUnavailable`; the sweeps in
   the cluster daemon route their work-list reads through
   `_sweep_work_list()`, which counts consecutive failures into the
   `cluster_sweep_work_list_failure_streak` gauge and logs a skipped
   pass.

Neither addresses the actual problem, which is that the protocol permits
replies of unbounded size and we find out how big they got by having one
fail in production.

There is a second-order effect worth naming, because it is what made the
`GetObjectsByState` instance self-sustaining. The failing read was the
garbage collector's work list. Every failed pass left the backlog
uncollected, and every uncollected object made the next reply larger.
An unbounded reply feeding a sweep that shrinks the set it reads is a
system that recovers on its own; feeding a sweep that *cannot run*
because the reply is too large, it is a ratchet.

## Mission and problem statement

**No `DatabaseService` reply may be unbounded in size.** Every
list-shaped reply has a documented bound, a defined behaviour when the
result set exceeds it, and a caller contract that makes the difference
between "here is everything" and "here is some of it" impossible to
ignore. When this plan is complete, raising the client message cap is
never again the response to a `RESOURCE_EXHAUSTED`, and a new unbounded
reply cannot be added without CI noticing.

Three things this plan must not break, all of them hard-won:

- **The unary retry path.** `_grpc_call` retries on `UNAVAILABLE`,
  `DEADLINE_EXCEEDED` and `CANCELLED`, rebuilding the channel for some
  of those, and raises `DatabaseUnavailable` once the budget is spent.
  That behaviour absorbed the database-tier rolling restart (issue
  3430) and is the reason a "not found" return value can be trusted
  (issue 3373). Any change to how these reads are transported has to
  say what it does to that path.
- **The watchdog budget.** Several callers run upstream of a systemd
  watchdog pet and pass `BOUNDED_QUERY_TIMEOUT` with a reduced retry
  count precisely so a stalled tier cannot SIGABRT them (issue 3586).
  A read that becomes N round trips has an N-times-larger worst case
  unless the caller's budget is recomputed.
- **The server's thread pool.** sf-database serves from a 64-worker
  `ThreadPoolExecutor`. A transport that holds a worker for the
  duration of a client's read, rather than for the duration of a query,
  changes the tier's concurrency arithmetic.

## Open questions

These are for phase 0 to resolve. The first is the substantive one.

### Q1. Server streaming, cursor pagination, or bounded-by-construction?

Three mechanisms are available, and they are not mutually exclusive --
the likely answer is that different RPCs want different ones.

**Server streaming** (`rpc Get... (Request) returns (stream Reply)`).
The message cap applies per message, so a stream of fixed-size chunks
is bounded however large the result set is. There is no cursor to
mint, no continuation token to validate, and no question about what a
client should do with a stale one. The client wrapper accumulates
chunks into the same list callers get today, so calling code is
unchanged. Against it: a stream holds a server thread for its whole
life rather than for one query, so a slow or stalled client pins a
worker out of 64; the retry logic in `_grpc_call` is built for unary
calls, and a mid-stream failure has already delivered partial results,
so "retry the call" stops being a safe default; and this codebase's
one previous encounter with streaming on this service was the
`grpc.health.v1` Watch deadlock documented at length in
`daemons/database/main.py`, where the synchronous servicer deadlocked
against the server's event-dispatch thread. That history is not
dispositive -- Watch is a long-lived subscription and these would be
short-lived reads -- but it argues for proving the pattern on one RPC
before committing the protocol to it.

**Keyset pagination.** Unary calls stay unary, so the retry path, the
timeout budgets and the caller contract are all untouched, and a server
thread is held per page rather than per read. The cursor for the RPCs
that actually matter here is not a general-purpose stateful cursor: it
is the last uuid seen, with the query ordered by primary key, which is
index-friendly in a way `OFFSET` is not. The cost is a real one
though -- the loop has to live somewhere, and if it lives in the client
wrapper then the wrapper is doing N round trips behind a caller who
thinks it made one call, which is exactly the watchdog-budget problem
above. It also needs a stated contract for rows that change between
pages.

**Bounded by construction.** Some of these reads do not want a complete
list at all and never did. A sweep that drains a work queue wants "the
next N items to work on, oldest first"; a `LIMIT N` with an explicit
ordering is a *complete and correct* answer to that question, not a
truncated one, and it bounds the reply permanently with no new protocol
machinery. This is the cheapest option and it retires a good share of
the exposure, but it is only available where the caller genuinely does
not need the whole set -- and telling those callers apart from the ones
that do is exactly what phase 1 is for. Where it does apply the reply
must still carry an explicit "there was more" signal, so nothing can
mistake a bounded page for an exhaustive answer.

**Current recommendation, to be confirmed or overturned in phase 0:**
bounded-by-construction for the sweep callers (phase 2), because it is
cheap, safe, and immediately useful; and then a single decision between
streaming and pagination for the genuinely unbounded reads, made
against the reply-size histogram phase 1 produces rather than against
intuition. The instinct that streaming avoids maintaining a cursor is
correct and is a real simplification; the counterweight is that
pagination leaves the hardened unary retry path in place, and the
retry path is load-bearing in a way the cursor is not.

### Q2. What is the caller taxonomy, and who enforces it?

The motivating defect turned on a distinction the code does not
currently express: a caller that *iterates* a list can tolerate a short
one, a caller that *complements* or *gates* on it cannot. Should that
be a type-level distinction (a `PartialList` result the caller must
unwrap), a naming convention, or a documented rule with a CI check?

### Q3. What is the bound, and is it per-RPC?

One global row or byte budget is simpler to reason about; per-RPC
budgets fit the traffic better. Phase 1's measurements should decide
this rather than a guess, which is exactly the mistake the 4MiB default
represents.

### Q4. Where does the cap end up?

If replies become genuinely bounded, the 32MiB client cap can come back
down -- possibly below the 4MiB default, which would turn any regression
into an immediate loud failure in CI rather than a slow discovery in
production. Attractive, but it needs the enforcement of phase 4 to be
trustworthy first.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 0. Decisions: mechanism per RPC class, caller taxonomy, bound policy | PLAN-grpc-bounded-replies-phase-00-decisions.md | Not started |
| 1. Audit and measure: classify every `repeated` reply field and every caller; add a server-side reply-size histogram | PLAN-grpc-bounded-replies-phase-01-audit.md | Not started |
| 2. Bounded by construction: convert sweep-style callers to explicit "next N, oldest first" queries with an explicit more-available signal | PLAN-grpc-bounded-replies-phase-02-sweeps.md | Not started |
| 3. The general mechanism for genuinely unbounded reads (`GetObjectEvents`, `GetObjectsByState`, the `Get*Uuids` family) | PLAN-grpc-bounded-replies-phase-03-mechanism.md | Not started |
| 4. Enforcement: CI fails when a new unbounded `repeated` reply field appears unregistered | PLAN-grpc-bounded-replies-phase-04-enforcement.md | Not started |
| 5. Lower the client cap, retire the stopgaps, document the contract | PLAN-grpc-bounded-replies-phase-05-closeout.md | Not started |

Named for phase 1's audit scope, so they are not rediscovered one
review round at a time: `_direct_get_expired_blob_uuids()` and
`_direct_get_stale_transcoded_blob_uuids()` both still end in `except
OperationalError: return []`, and are consumed by the same
`_cluster_wide_cleanup()` pass as the accessors this branch hardened.
Both callers only iterate, so the collapse is permitted by the rule
rather than overlooked by it — but "permitted" is a judgement that
should be recorded per caller, which is what the taxonomy in Q2 is for.
`Node.instances` is the one to look at hardest: it is not a complement
set, but a node whose instance list reads empty during an outage is a
node the scheduler believes is idle.

`DatabaseBackedObjectIterator._find()` belongs in the same audit, and
is the widest instance of the pattern in the tree. A tier-wide
`DatabaseUnavailable` propagates out of it deliberately — catching it
would rebuild the #3638 hazard one layer up, in every `Blobs()`,
`IPAMs()` and `AgentOperations()` caller at once — but a `None` return
from the per-reply failure shape still truncates to an empty
iteration. That is tolerable only for as long as every iterator caller
iterates rather than complements, which is a property of the callers
and not of the iterator, and so is exactly the kind of thing the Q2
taxonomy has to be able to state. Whichever bounding mechanism phase 3
picks, this is the call site where a partially-consumed generator has
to be distinguishable from a complete one.

Sequencing constraints:

- Phase 1 must land before phase 0 is *finalised*. The decisions in
  phase 0 depend on knowing which replies are large and how large, and
  the reply-size histogram is the only honest source of that. In
  practice phase 0 drafts the options, phase 1 measures, and phase 0 is
  then re-cut with the answer. This inversion is deliberate: the
  4MiB default is what a guessed bound looks like.
- Phase 2 is independent of the phase 0 decision and can land as soon as
  phase 1 has identified the sweep callers. It is the highest
  value-to-risk ratio in the plan.
- Phase 3 needs phase 0 settled.
- Phase 4 needs phase 3's registration surface to exist, and must land
  before phase 5 -- lowering the cap without enforcement just re-arms
  the trap.
- Phase 5 lands last and only once the operator cluster has run on the
  new shape long enough for the histogram to show the tail flattening.

## Dependencies on other plans

- **`PLAN-eventlog-direct-mariadb` is complete and is where this came
  from.** Its Future work section listed cursor pagination for
  `GetObjectEvents` as deferred; issue 3638 is that deferral coming due.
  Phase 3 should read its phase 4 plan for the REST contract
  constraints on event reads before changing them.
- **`PLAN-database-load-reduction` phase 3 (client consolidation) is
  helpful and not blocking.** It consolidates the three sf-database
  client stacks into one, which would give this plan a single seam to
  put a pagination loop or stream accumulator behind instead of three.
  If it has landed, use it; if not, phase 3 should avoid entrenching
  the duplication.
- **The OpenTelemetry thread would improve phase 1.** The reply-size
  histogram is a poor cousin of proper instrumentation. If OTel has
  landed, prefer it; if not, phase 1's histogram stands alone and is
  worth having regardless.
- **`PLAN-api-input-validation` is a useful precedent, not a
  dependency.** Its `STRUCTURED_PARAMETERS` table -- where the
  *completeness* of the table is derived from the published spec, so a
  new structure fails CI until registered, while the content of each
  entry is written by hand -- is exactly the shape phase 4 should copy.

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phase 0 and phase 3 are protocol changes to the service every
    daemon depends on, and both must be planned at high effort with
    opus or better. Phase 1 is broad but mechanical once its
    classification criteria are written down. Phase 2 is a small,
    well-understood change to three call sites plus their SQL.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    A worked brief for this plan: instead of "paginate
    GetObjectsByState", write "add `page_size` and `after_uuid` to
    `GetObjectsByStateRequest` and a `bool more_available` to
    `GetObjectsByStateReply` in `protos/database.proto`; regenerate
    with `tox -e genprotos`; push the bound into
    `_direct_get_objects_by_state` in `shakenfist/mariadb.py` as an
    `ORDER BY object_uuid LIMIT :page_size` with a
    `WHERE object_uuid > :after_uuid` predicate so the existing
    primary-key index serves it; leave the public
    `get_objects_by_state()` signature unchanged by looping in the
    gRPC wrapper, and state in its docstring what the loop costs a
    caller running under a watchdog pet."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] The code passes `pre-commit run --all-files` (flake8,
          stestr unit tests, mypy).
    - [ ] If proto files changed, stubs were regenerated with
          `tox -e genprotos` and committed.

    Plus, specific to this plan:

    - [ ] No caller has been silently converted from "the whole
          set" to "some of the set". Every bounded reply is
          consumed by a caller that either does not need
          completeness, or explicitly handles the
          more-available signal.
    - [ ] The change to how a read is transported states what it
          does to `_grpc_call`'s retry behaviour and to the
          worst-case wall time of callers running under a
          watchdog pet.
    - [ ] Any assertion that a bound is enforced has been
          mutation-tested: remove the bound, confirm the test
          fails, restore it.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented because
the following statements will be true:

* Every `repeated` field in a `DatabaseService` reply is either bounded
  by construction, paginated, streamed, or registered with a written
  justification for why its size is inherently limited.
* A newly added unbounded `repeated` reply field fails CI until it is
  registered, in the manner of `STRUCTURED_PARAMETERS` in
  `test_openapi_spec.py`.
* No caller can mistake a bounded page for a complete result set: the
  reply carries an explicit more-available signal and the callers that
  need completeness consume it.
* The client `grpc.max_receive_message_length` has been reduced from
  the 32MiB stopgap, and the value is justified by measurement rather
  than by headroom over the largest failure yet observed.
* sf-database exports a reply-size histogram per RPC, and the operator
  cluster's tail is flat rather than growing.
* The `cluster_sweep_work_list_failure_streak` gauge reads zero in
  steady state, and the sweeps it covers no longer depend on an
  unbounded read. The gauge, its labels and a sample alert expression
  are documented in `docs/operator_guide/database.md`.
* Filtering and limiting are pushed down to SQL with index-friendly
  predicates -- keyset, not `OFFSET` -- rather than materialising a
  result set and trimming it in Python.
* `_grpc_call`'s retry semantics and the `BOUNDED_QUERY_TIMEOUT`
  callers' watchdog budgets are documented as unchanged, or the change
  is explicit and its worst case is recomputed.
* There is unit coverage for the bound on each converted RPC, and
  functional coverage under `deploy/shakenfist_ci` for at least one
  read whose result set exceeds one page.
* `pre-commit run --all-files` passes (flake8, stestr unit tests,
  mypy).
* `ARCHITECTURE.md`, `AGENTS.md` and
  `docs/developer_guide/database_internals.md` are updated for the
  reply-size contract.

### Future work

- **The same question for the other services.** `agent.proto`,
  `privexec.proto` and `nodelock.proto` have not been audited here.
  They are lower risk (smaller result sets, fewer callers) but the
  argument for bounding them is the same one.
- **A general "large result" convention.** If phase 3 produces a
  mechanism that works, it should become the documented default for new
  list-shaped RPCs rather than something each author rediscovers.
- **Server-side send caps.** The server currently sets no
  `grpc.max_send_message_length`, so it will happily serialise a reply
  no client can receive. A server-side cap would fail the query rather
  than the transport, with a message naming the RPC and the size. Worth
  doing whichever mechanism phase 3 picks.
- **`GetObjectEvents` still flattens a failed read to `[]`.** It was the
  RPC that failed most often in #3638 (replies to 6.9MB), and a failed
  read still renders in the REST response as an authoritative "this
  object has no events" -- the user-visible half of the defect, left in
  place while the operator-visible half was fixed. The decision is
  deliberate and recorded in `_grpc_get_object_events()`'s docstring:
  both current callers tolerate it (the REST display path, and
  `errored_node_affected_types()`, which reads an empty result as
  "blast radius unknown, retry next pass"), and the alternative --
  answering 503 from the events endpoint -- trades a degraded page for a
  broken one. Phase 3 should either make the failure impossible, or
  revisit the choice with a metric so a recurrence is visible without
  grepping daemon logs.
- **`sf-cleaner` exports no Prometheus endpoint.** Its blob maintenance
  pass skips when the active-blob read fails, which is the correct
  behaviour but leaves the most dangerous caller in #3638 as the least
  observable one: a node that persistently cannot read the list stops
  reclaiming disk, and the only signal is a log line. Giving the cleaner
  a metrics port is its own change -- a new config option, a listener on
  every node, and firewall rules -- rather than a rider on the stopgap.

### Bugs fixed during this work

This section should list any bugs we encounter during development that
we fixed. You should also scan the project's issue tracker for directly
related issues.

Already known, both fixed by the stopgap commit that precedes this plan
on the `issue-fix-3638` branch:

- `get_active_blob_uuids()` flattened a failed read to `[]`, and the
  cleaner uses it as a complement set. An oversized `GetObjectsByState`
  reply was therefore an instruction to delete every blob file on the
  node older than two cleaner delays. Fixed by raising
  `DatabaseUnavailable` and skipping the pass; regression test
  mutation-tested.
- The per-blob and per-instance sweeps in the cluster daemon had the
  same `or []` collapse as the deleted-object sweep, so a failed read
  reported a healthy pass over an empty queue.
- `DatabaseServicer.GetObjectsByState` and `GetStatelessObjectUuids`
  answered a failed read with an OK status and an empty `repeated`
  field, which left the blob-store deletion hazard reachable through
  the likelier failure -- MariaDB unreachable while sf-database itself
  is healthy and answering -- even after the client-side fix. They now
  set `UNAVAILABLE` (transient, retried into `DatabaseUnavailable`) or
  `INTERNAL` (handler bug, non-retryable, mapped to `None`). Any
  bounding mechanism phase 3 picks has to preserve this: a reply that
  can only carry a list cannot report its own failure.
- The `break` added to bound the sweeps' worst-case wall time would
  have starved every object type behind a persistently slow one, since
  both loops start from the same place every pass. They now resume
  after whichever type stopped the previous pass.
- The cleaner's deletion test is an OR over *two* lists, and only the
  active-blob one had been hardened. `Node.blobs` ->
  `get_references_from()` flattened failure to `[]` at all three
  layers, so a plain MariaDB `OperationalError` still emptied the
  node's blob store -- and that is the *likelier* trigger, since it
  breaks while sf-database stays healthy. Fixed with a raising
  `get_node_blob_uuids()` over a truthful `_get_references_from()`,
  plus `UNAVAILABLE`/`INTERNAL`/`INVALID_ARGUMENT` on
  `GetReferencesFrom`. Five mutations confirmed the guards.
- Skipping the blob section of `_cluster_wide_cleanup()` also skips the
  `record_usage()` pass that refreshes `last_used` for
  instance-backed blobs, while the stale-transcode reaper further down
  selects on that same column. A database outage longer than
  `BLOB_TRANSCODE_MAXIMUM_IDLE_TIME` would therefore have reaped
  transcodes of blobs in active use. The reaper now sits out any pass
  which could not read the active blob list. Self-healing (transcodes
  regenerate on demand), but it is the first case found of the
  degraded-pass comment's "the rest is independent" claim not holding,
  and any future partial-failure skip has to ask the same question of
  the sections downstream of it.

### Deliberately deferred: fault-injected functional coverage

Everything the `issue-fix-3638` branch adds is unit-level, including
two changes that are observable outside the process: `GET /blobs` now
answers `503` where it used to answer `200` with an empty list, and the
cleaner now declines to delete anything on a pass whose reads failed.
`CLAUDE.md` prefers functional coverage over unit coverage where only
one is possible, so the absence is recorded here rather than left to be
rediscovered.

The reason is that both tests need a *failing* database rather than an
absent one. `deploy/shakenfist_ci` runs against a real cluster with no
fault-injection surface: there is no supported way to make MariaDB
return `OperationalError` for one query, or to make a `GetObjectsByState`
reply exceed the receive cap on demand, without stopping `sf-database`
outright — which fails every other daemon on the node and tests
something else. Phase 1 instruments reply sizes, and is the first point
at which a deliberately oversized reply can be produced as a test
fixture rather than as an outage. The functional coverage in the
success criteria above belongs there, and should cover this branch's
two contract changes as well as the bound itself.

### Documentation index maintenance

When creating this master plan from the template, the following files
in `docs/plans/` should be updated:

* **`index.md`** — add one row to the *Master plans* table for this
  master plan. Phases are tracked in the Execution table above.
* **`order.yml`** — add an entry for this master plan so it appears in
  the documentation navigation. Phase files are *not* added to
  `order.yml`.

The site navigation in `mkdocs.yml` is produced from `mkdocs.yml.tmpl`
by the docs-sync workflow, which consumes `order.yml`. No manual
`mkdocs.yml` edits are needed.

When all phases are complete, update the status column in
`docs/plans/index.md`.

### Back brief

Before executing any step of this plan, please back brief the operator
as to your understanding of the plan and how the work you intend to do
aligns with that plan.
