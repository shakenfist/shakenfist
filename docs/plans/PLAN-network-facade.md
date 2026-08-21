# Network operations facade and queue-only mutation

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/network/network.py` (the `Network` class
under discussion), `shakenfist/operations/net_op.py` (the
worker dispatch), `shakenfist/daemons/network/workitem.py`
(the single-threaded `net-worker`),
`shakenfist/daemons/network/maintain.py` (the parallel
`maintain` reconciliation thread),
`shakenfist/daemons/privexec/main.py` (the
privileged-execution daemon that actually mutates kernel
network state), `shakenfist/mariadb.py` (the existing
three-layer database access pattern, which is the architectural
precedent for the proposed change), and
`shakenfist/external_api/base.py` (the
`redirect_to_network_node` decorator that this plan removes).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via the table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The `Network` class in `shakenfist/network/network.py` carries
two distinct responsibilities in one type:

1. The **intent API** that callers across the cluster reach
   for when they want a network to change state — every
   daemon and REST handler calls `n.create_on_hypervisor()`,
   `n.ensure_mesh()`, `n.add_floating_ip(...)`, etc.

2. The **worker API** that actually mutates host network
   state on a given node — the same methods invoke
   `util_concurrency.create_vxlan_interface`,
   `util_concurrency.ensure_vxlan_mesh`,
   `util_concurrency.add_floating_ip`, etc., which talk to
   the local `sf-privexec` daemon and run `ip`/`bridge` /
   `iptables` commands.

Because both responsibilities live on the same class, any
node-local caller can invoke the worker API directly without
going through the queue. The `sf-net` daemon's `net-worker`
job is single-threaded by design and processes one
`net_op` work item at a time
(`daemons/network/workitem.py:31` carries a comment from
mikal to that effect), so two queued net-ops on one node
are correctly serialised. But the same node typically runs
`sf-net`'s separate `maintain` reconciliation thread plus
`sf-queues`, `sf-api`, and the instance lifecycle paths in
`instance.py`, and each of those can call `n.ensure_mesh()`
(and friends) directly without coordinating with the
net-worker. Today there are five call sites for
`ensure_mesh` across `instance.py`,
`operations/net_op.py`, `operations/node_inst_netdesc_op.py`,
`daemons/queues/startup_tasks.py` and
`daemons/network/maintain.py`, plus similar fan-out for
floating-IP and route operations.

The same bypass shape exists at the **API layer**. Endpoints
that need to run on the network node today wear the
`redirect_to_network_node` decorator
(`external_api/base.py:336`), which makes a synchronous HTTP
request from the receiving API server to the network node's
gunicorn on port 13000. This is used in
`external_api/network.py` (three sites) and
`external_api/interface.py` (one site). It is the same class
of bypass: instead of routing the intent through the
operation queue that we already have for cluster work, the
API server pretends to be on the network node by proxying.

This produced a concrete CI failure on PR #3182's merge-queue
run (GitHub Actions run 25899623744, "Guests" job). Two
threads inside `sf-privexec` ran `_ensure_mesh` for the same
VXLAN interface within 2 ms of each other, both observed the
same stale FDB entry from their initial `bridge fdb show`,
and both issued `bridge fdb del` for it. The kernel served
one and rejected the other with `RTNETLINK answers: No such
file or directory`; the losing privexec request returned
FAILURE, `network.ensure_mesh` raised `EnsureMeshFailed`, and
the resulting `ERROR sf-net`/`ERROR sf-privexec`/`Traceback`
lines tripped the post-test stable-log gate. The functional
tests themselves all passed — only the log scrape caught the
race.

A short-term fix is being landed on the `stability` branch:
wrap the six unlocked host-mutating methods on `Network`
(`ensure_mesh`, `add_floating_ip`, `remove_floating_ip`,
`route_address`, `unroute_address`, `remove_nat`) with the
existing per-network `NodeLock` (the same primitive used by
the already-locked methods). That fix is necessary but
addresses the symptom, not the structural problem.

The architectural precedent for the fix exists in this same
codebase. `shakenfist/mariadb.py` (and the `sf-database`
daemon at `daemons/database/main.py`) enforces that **every**
daemon, including local ones, must reach MariaDB through the
gRPC database service. Only `sf-database` has `MARIADB_HOST`
set; all other daemons route through gRPC. This eliminates
direct-vs-indirect access skew, gives the database service a
single point to enforce metrics, throttling, and connection
pooling, and lets the daemon evolve internally without
touching every caller. The proposal in this plan is to apply
the same shape to network operations: one daemon owns the
host-state mutations, and every other caller addresses it
through a typed API that enqueues intents.

## Mission and problem statement

Restructure `Network` so that all host-state mutations are
performed by a single owner (the `sf-net` net-worker on each
node), and every other caller — including local daemons on
the same node and the API server — interacts via the public
`Network` class whose host-mutating methods enqueue intents
through the operation queue. Sequenced callers declare their
ordering via the existing `ClusterOperation.depends_on`
machinery; callers that genuinely need to block until a chain
completes use a small generic poll helper rather than each
method having its own wait shim. After the change:

* `Network` remains the class every caller already holds.
  Its host-mutating methods (`ensure_mesh`,
  `add_floating_ip`, `create_on_hypervisor`,
  `update_dnsmasq`, etc.) keep their existing names but
  their bodies change from "do the work inline" to "build a
  NetOp task for this intent, enqueue it on the appropriate
  queue, return the op handle". They do not wait. Their
  return type changes from `None` (with raised exceptions on
  failure) to the enqueued op (with errors surfaced through
  the op's terminal state).
* A new `BridgedVXLanNetwork(network)` class, instantiated
  **only** inside the `net-worker` workitem dispatcher,
  carries the actual `_apply_*` host-state mutation methods.
  All calls to `util_concurrency.create_vxlan_interface`,
  `util_concurrency.ensure_vxlan_mesh`,
  `util_concurrency.add_floating_ip`, and friends move onto
  this class.
* **Re-entrancy is structurally impossible.** The only path
  that bypasses the queue is constructing a
  `BridgedVXLanNetwork` instance, and that constructor is
  only called inside the workitem dispatcher. A queued op
  that needs to chain host changes (e.g. `_network_deploy`
  doing `create_on_network_node` then `ensure_mesh`)
  performs those chained changes on its
  `BridgedVXLanNetwork` instance, so it never re-enqueues
  and never deadlocks the single-threaded net-worker.
  Explicitly not used: thread-local "am I inside the queue?"
  context flags. Detection by inheritance: external callers
  hold a `Network`, in-worker callers hold a
  `BridgedVXLanNetwork`.
* There is no path by which `sf-queues`, `sf-api`,
  `sf-cleaner`, the instance lifecycle code, or the
  `maintain` reconciliation thread can directly call
  `util_concurrency.ensure_vxlan_mesh`,
  `util_concurrency.add_floating_ip`, or any other
  host-mutating privexec helper for a network.
* The single-threaded `net-worker` is the only mutator and
  therefore naturally serialises all activity for a network
  on a node — `NodeLock` becomes redundant for these methods
  and the locks added by the stability-branch fix can be
  removed.
* The `maintain` reconciliation thread in `sf-net` no longer
  calls the same methods as the net-worker; instead it
  enqueues `net_op`s and lets the net-worker do the work.
* The `redirect_to_network_node` decorator and all four of
  its applications are removed. API handlers no longer need
  to be on the network node, because the operation they
  enqueue runs wherever the net-worker that owns the queue
  lives. The affected endpoints change contract from
  "block until the host change is done" to "return 202 +
  terminal op uuid and the client polls"; the client
  (`shakenfist/client-python`) is updated to match.
* Two new REST endpoints expose ClusterOperation chains:
  GET `/clusteroperations/<uuid>/chain` returns the
  transitive `depends_on` closure; GET
  `/clusteroperations?target_object_type=&target_uuid=`
  lists ops targeting an object. Both are namespace-scoped
  at the SQL layer using the existing
  `cluster_operation_targets` table.
* The "queue-jumping" fairness concern (a node that's also
  the network node, or just a node running both `sf-net`
  and `sf-queues`, can bypass the work queue) disappears
  because no caller has a bypass to take.
* Errors crossing the queue boundary are serialised as a
  structured `ErrorReport` (code, message, details,
  `origin_class`, traceback) rather than as rehydrated
  Python exception classes. In-worker exception handling
  is unchanged; the boundary translation happens in the
  worker dispatcher's outer `except`. REST endpoints
  render `ErrorReport` to HTTP via a single mapping. This
  matches the pattern every mature RPC framework
  (gRPC, JSON-RPC, Erlang OTP) has converged on, and
  explicitly avoids the `oslo.messaging` exception-
  rehydration model that produced years of version-skew
  pain in OpenStack. See open question 3.

Scope boundaries:

* **In scope:** every `Network` method that currently invokes
  `util_concurrency.*` host-mutating helpers, plus
  `update_dnsmasq`, `remove_dnsmasq`, `remove_dhcp_lease`,
  `update_dns_entry`, `remove_dns_entry` (which mutate the
  dnsmasq process state for the network).
* **In scope:** the `maintain` thread in `sf-net` — its
  direct calls to `n.ensure_mesh()`,
  `n.create_on_hypervisor()`, `n.add_floating_ip(...)`,
  `n.route_address(...)` are precisely the bypasses we are
  closing.
* **In scope:** a per-node queue lane that every node's local
  `sf-net` services for its own host. The existing
  `networknode-*` queues are network-node-only, so we cannot
  route `create_on_hypervisor` or `ensure_mesh` (which are
  per-hypervisor mutations) through them. See open question 9.
* **In scope:** removal of the
  `redirect_to_network_node` decorator and its four call
  sites in `external_api/network.py` and
  `external_api/interface.py`. After the facade lands, none
  of those handlers need to be on the network node.
* **In scope:** preserving event log fidelity through the
  refactor. The existing `add_event` calls inside
  host-mutating methods are part of the audit trail we want
  to keep, even when the body of the method moves to
  `BridgedVXLanNetwork`. Either the enqueueing `Network`
  shim or the executing `BridgedVXLanNetwork` method must
  emit equivalent events; double-emission must be avoided.
* **Out of scope:** the `NetworkInterface` and `IPAM`
  classes. Those have their own concerns (IP reservation,
  interface attach/detach) that overlap with networks but
  are not the same problem. They may benefit from the same
  pattern later, but each has its own audit work.
* **Out of scope:** the existing `nodelock`-based fix on the
  `stability` branch. That fix stays in place until the
  facade refactor lands and is proven; it can be removed in
  the final phase as cleanup.
* **Out of scope:** the other API redirect decorators
  (`redirect_instance_request`, `redirect_to_eventlog_node`,
  `redirect_upload_request`). They are structurally the same
  problem as `redirect_to_network_node` and the queue-based
  pattern established here is the future direction for them
  too, but each has its own caller fan-out and is its own
  audit. See future work.
* **Out of scope:** generalised event-based completion
  notification (subscribing to terminal-state events for a
  cluster operation, webhook notifications on object events,
  etc.). Polling is sufficient for this plan. See future
  work.
* **Out of scope:** capability advertisement / client-driven
  preference for the new 202+poll contract. Since we own
  both server and client and have no other consumers, we
  flip the contract directly. See future work if external
  API consumers ever appear.
* **Out of scope:** lowering the global
  `BaseClusterOperation.defer(delay=15)` default. The
  refactor passes the tight defer delay through the new
  chain-builder helper instead, leaving unrelated existing
  `defer()` callers undisturbed. See open question 11.
* **Out of scope:** saga / compensator semantics for
  chains. This plan keeps the existing convergence model:
  when a chain step errors, the dispatcher already aborts
  the remaining dependents (`workitem.py:84-101`), partial
  state is left as-is, and `maintain` plus the cleaner are
  responsible for detecting and correcting drift on
  subsequent passes. We are not introducing paired
  forward/undo operations or running chains backwards on
  failure. Reasons: compensators carry their own state
  (they need to know what the forward step observed, not
  just what it wrote), compensators can themselves fail
  (requiring dead-letter / halt machinery), idempotency
  requirements stack on both forward and reverse paths,
  and compensation is externally observable in confusing
  ways. The investment is better spent on Phase 6 making
  convergence robust. See future work for the lighter
  alternative.
* **Out of scope:** changing how `net_op`s are queued in
  terms of priority semantics, or how the cluster decides
  which node owns a network. Queue prioritisation and
  network-node election are unchanged; only the set of
  available queues grows.

## Open questions

1. **How sequenced callers express ordering.** Today
   `n.create_on_hypervisor()` returns when the host change
   is complete; many callers depend on that (e.g.
   `node_inst_netdesc_op.py:243` calls
   `n.create_on_hypervisor()` then `n.ensure_mesh()` then
   `n.update_dnsmasq()` in sequence, relying on each having
   finished before the next runs). Earlier framings of this
   question asked "what wait primitive do we provide" and
   considered polling, MariaDB named locks, in-process
   events, and gRPC streaming. That framing was wrong: the
   existing `ClusterOperation` machinery already has a
   `depends_on` field whose semantics are exactly what we
   need (`daemons/network/workitem.py:71-109`). The right
   answer is to use it.

   **Model.** `Network.ensure_mesh()` becomes "build a
   NetOp task for `ensure_mesh`, enqueue it on the
   appropriate queue, return the op handle". It does not
   wait. A sequenced caller that today does
   `create_on_hypervisor` → `ensure_mesh` → `update_dnsmasq`
   becomes a caller that enqueues three NetOps, with each
   declaring `depends_on=[previous]`, then returns the
   handle of the terminal op. Callers that genuinely need a
   synchronous wait (CLI tools, some test code, a stubborn
   REST endpoint) call a small `poll_until_terminal(op)`
   helper that polls `mariadb.get_cluster_operation` on a
   short interval bounded by `ASYNC_OP_TIMEOUT`. This is
   the same shape as the existing
   `_await_instance_operations_complete` in
   `deploy/shakenfist_ci/base.py:395`, just generalised.

   **Why this is better than wait-helper-as-default.**
   1. No new wait primitive — we use the dependency
      machinery the dispatcher already implements.
   2. REST handlers can return early with the terminal op's
      uuid and the client polls — no gunicorn worker held
      for the duration of the host change.
   3. The re-entrancy framing simplifies. `Network` methods
      build and enqueue; `BridgedVXLanNetwork` methods do
      the host work. There is no "inside the worker we go
      synchronous, outside the worker we go async" split —
      everything outside the dispatcher is async by
      default.
   4. Errors surface through the existing op terminal-state
      machinery; the dispatcher already aborts dependents
      whose deps end in ERROR/DELETED/ABORT
      (`workitem.py:85-101`). No new error-propagation
      plumbing.

   **Implementation note on errors.** In-worker exception
   handling is unchanged: `BridgedVXLanNetwork._apply_*`
   still raises the typed exceptions, and the workitem
   dispatcher's existing `except EnsureMeshFailed:` and
   sibling blocks in `net_op.py:84-94` still catch them
   in the same process. The boundary translation happens
   in the dispatcher's outer `except`: anything that
   escapes is converted via `ErrorReport.from_exception`
   and persisted on the op record. External callers
   using `poll_until_terminal` read `op.error_report`
   and either pattern-match on `report.code` or call
   `op.raise_for_error()` to surface the failure as a
   single generic wrapper. See open question 3 for the
   full `ErrorReport` model.

   **Implementation note on idempotency.** Chains can be
   re-attempted (e.g. a transient defer that exceeds the
   timeout, an operator-triggered retry). All `_apply_*`
   methods on `BridgedVXLanNetwork` must remain
   re-entrancy-safe at the host level. `ensure_mesh` is
   already idempotent by design (it computes desired state
   and converges). `add_floating_ip` and friends must be
   audited as part of their migration phase.

   **Implementation note on the defer delay.** See open
   question 11.

2. **Naming and class shape.** Resolved. `Network` remains
   the public class with the same call surface every caller
   already uses (no churn at call sites). A new
   `BridgedVXLanNetwork(network)` class — instantiated only
   inside the workitem dispatcher — holds the
   `_apply_*` worker methods. The name forecloses nothing
   about future alternate implementations (e.g. native L2
   underlay, OVS, etc.) and names what the current
   implementation actually is. There is no separate
   `NetworkFacade` type; the facade is `Network`.

3. **Error propagation at the queue boundary.** What
   we are building is an in-cluster RPC mechanism, and
   every mature RPC framework — gRPC, JSON-RPC,
   Twirp/Connect, Erlang's `{'EXIT', Pid, Reason}` — has
   converged on the same pattern for errors: **errors
   are data, never rehydrated exceptions, at the
   boundary**. OpenStack's `oslo.messaging` is the
   cautionary tale for the alternative; it tried to
   rehydrate exception classes from a registry and
   produced years of version-skew bugs, serialisation
   fragility, and security concerns about reconstituting
   arbitrary classes from strings. We will not repeat
   that experiment.

   **Resolved approach: `ErrorReport`.** A small typed
   record carried on the op record describing a failure:

   ```python
   class ErrorReport:
       code: str          # e.g. 'network.ensure_mesh.failed'
       message: str       # human-readable
       details: dict      # structured context
       origin_class: str  # e.g. 'shakenfist.exceptions.EnsureMeshFailed'
       traceback: str     # stored for operator debugging
   ```

   `ErrorReport` lives in
   `shakenfist/operations/error_report.py` (not in
   `shakenfist/network/`) because it is fundamentally a
   cluster-operation concept and other subsystems will
   adopt it later. It has at minimum:

   * `ErrorReport.from_exception(exc) -> ErrorReport` —
     called by the worker dispatcher when an
     `_apply_*` method raises. A small registry maps
     known exception classes to stable codes; unknown
     exceptions become `code='internal.unknown'` with
     `origin_class` set so operators still see what
     happened.
   * `ErrorReport.to_http() -> (status_code, body)` —
     used by REST endpoints to render a failed op into a
     clean HTTP response without leaking tracebacks. The
     mapping from code to HTTP status lives in one place.
   * On the op object, `op.error_report` returns the
     report (or `None` if not failed).
   * `op.raise_for_error()` raises a single generic
     `NetworkOperationFailed` carrying the report, for
     the rare caller that wants exception-flow control
     rather than pattern-matching on the code.

   **Crucial cleavage point.** In-process exception
   handling is **unchanged**. `_apply_ensure_mesh()`
   still raises `EnsureMeshFailed`; the worker dispatcher
   still catches it in the same process; the existing
   `except EnsureMeshFailed:` and `except DeadNetwork:`
   blocks in `net_op.py:84-94` and elsewhere keep
   working unchanged because they are *in-worker* and
   never crossed the queue boundary. The only change is
   that when the dispatcher's outer `except` catches
   anything that did escape, it converts to `ErrorReport`
   before persisting, rather than storing freeform text.

   **Initial code namespace** (registered in
   `from_exception` for v1):

   * `network.ensure_mesh.failed` ← `EnsureMeshFailed`
   * `network.dead` ← `DeadNetwork`
   * `network.create_vxlan.failed` ← `CreateVXLANInterfaceFailed`
   * `network.floating.assign_failed` ← `CannotAssignFloatingGateway`
   * `internal.unknown` ← anything else (with
     `origin_class` preserving the actual exception class)

   New codes are added per-subsystem as needed. The
   namespace is hierarchical so operators can grep / log-
   scrape (e.g. `code starts with 'network.'`).

   **What we gain:**
   * No registry of importable classes on the consumer
     side. Wire format is strings + dict; any consumer
     in any language can pattern-match.
   * Forward compatibility: new codes do not break old
     consumers.
   * REST clients see structured JSON, not stack traces.
   * No `oslo.messaging`-style version-skew exception
     class problem.
   * Composes with future cancellation work: a
     cancelled op gets `code='cancelled'` and downstream
     handling already knows what to do with codes it
     recognises.

   **What we give up.** External callers that today
   could `except EnsureMeshFailed:` to handle one
   specific failure mode become callers that either (a)
   pattern-match on `report.code` after polling, or (b)
   call `op.raise_for_error()` and catch the single
   generic wrapper. Today's set of such callers is
   small; the trade-off is sound and matches what every
   other RPC framework arrived at independently.

4. **What happens to the `Network` class itself.** Resolved
   together with open question 2. `Network` keeps its data
   carrier role (object_states, attributes, queries,
   `subst_dict()`, `is_okay()`, `is_dead()`) and its
   existing public method names. The bodies of host-mutating
   methods change from "do the work inline" to "build a
   `net_op` task, enqueue it on the appropriate queue, poll
   for terminal state, re-raise on error". A
   `BridgedVXLanNetwork(network)` wrapper class carries the
   `_apply_*` methods that actually run on the host; it is
   instantiated only by the net-worker dispatcher.

5. **Migration order.** Resolved: per-method migration,
   one method (or a small tightly-coupled group) per
   phase. Phases 2–5 migrated each method cleanly
   in-place without leaving temporary feature flags
   behind. The per-method migration flag approach
   discussed during planning was not needed in
   practice — each phase replaced the method body
   directly, leaving the public `Network` surface
   unchanged while the implementation moved to
   `BridgedVXLanNetwork`. The instance-start path was
   migrated last (Phase 5) as planned.

   **Safety: per-phase PR runs cluster_ci.** The "leaves
   the cluster in a runnable state, passes CI" rule for
   each phase means each phase's PR must pass not just
   `pre-commit run --all-files` and stestr but also the
   functional `cluster_ci` smoke suite. Migration phases
   that change observable behaviour (e.g. enqueue+poll
   semantics replacing direct calls) are precisely the
   places where unit tests will pass but functional tests
   may catch a regression. Phase plans treated cluster_ci
   as a required gate, not a courtesy run.

6. **Behaviour of `maintain.py`.** The reconciliation
   thread today walks all networks every interval and
   re-applies host state where it has drifted. Under the
   facade it instead enqueues reconciliation ops. The
   risks are (a) `maintain` enqueues a fresh
   reconciliation each pass even when the previous one is
   still in flight, piling up duplicates; (b) a
   permanently-broken network ("operation of death")
   loops forever, consuming queue cycles; (c) maintain's
   observation can be stale by the time it goes to
   enqueue.

   **Resolved approach: per-network gating at enqueue
   time, plus three safety guards.**

   The reconciliation thread still runs on a fixed
   interval (current default 60 s; configurable). Each
   pass:

   1. **Queue-depth safety guard.** Before doing any
      work, the pass queries `get_work_queue_length()`
      on the per-node and network-node queue families.
      If queue depth exceeds a configurable threshold,
      the pass is skipped and an event is emitted. This
      handles the genuine pathology of "queue is way
      backed up; do not pile on".
   2. **Discovery.** Walk all networks observed on the
      host (and the network node's set on the elected
      network node), compare observed vs desired state.
      Same logic as today's `is_okay`/"Recreating not
      okay network on hypervisor" code paths — we are
      not weakening the drift detection.
   3. **Per-network gating.** For each network with
      detected drift, call `has_pending_cluster_operation(
      target=network, op_type='net_op')` (the
      history-aware "any in-flight op?" query backed by
      `cluster_operation_targets`). If yes, skip
      enqueueing for this network this pass.
   4. **Cooldown.** If no in-flight op exists, look up
      the most recent terminal reconciliation op for
      this network. If it ended in ERROR within the last
      `MAINTAIN_RECONCILE_COOLDOWN_SECONDS` (default
      60 s), skip enqueueing — let the previous failure
      breathe before retrying.
   5. **Circuit breaker.** If the last K terminal
      reconciliations on this network all ended in
      ERROR (default `MAINTAIN_RECONCILE_CIRCUIT_K = 5`),
      skip enqueueing and emit a prominent event
      ("network has failed reconciliation K times in a
      row; quiesced pending operator attention"). The
      next maintain pass naturally re-checks; once the
      operator does something that lets a fresh
      reconciliation succeed, the circuit closes.
   6. **Enqueue.** Otherwise build the reconciliation
      chain (using the same `BridgedVXLanNetwork`-driven
      logic as user-facing reconciliation, just with
      `background` priority) and enqueue.

   This is one `cluster_operation_targets` query and one
   `get_work_queue_length` query per pass, both indexed.
   No new schema.

   **Why maintain is not itself a ClusterOperation in
   this plan.** A natural-looking alternative is to make
   maintain a CO that enqueues its successor with
   `runs_after=[spawned_reconciliations]` and
   `delay=60s`. We considered this and rejected it for
   two concrete reasons:
   * `depends_on` aborts the dependent if a dep ends in
     ERROR (`workitem.py:84-101`), which would
     permanently kill the maintain recurrence on the
     first failed reconciliation. `runs_after` doesn't
     abort, but it also has no max-wait semantics — a
     stuck dep defers the dependent forever.
   * The CO model assumes terminal states; maintain is
     an indefinite recurring scan. Making it a CO means
     re-enqueue-self-on-completion, which is
     "reinventing cron with extra steps".

   The proper home for "maintain is a recurring CO" is
   the future `PLAN-recurring-operations.md`, which
   introduces a typed `RecurringOperation` object,
   addresses the dispatcher gaps named above
   (max-wait `runs_after`, continue-on-failure
   recurrence), and absorbs `scheduled_tasks.py` and
   `maintain.py` as initial consumers. When that lands,
   `daemons/network/maintain.py` disappears and the
   gating logic above moves into a
   `network_maintain_pass` op triggered by an internal
   `RecurringOperation`. Until then, the thread-plus-
   gating approach above is sufficient and avoids
   pre-engineering the dispatcher changes that the
   recurrence framework will need to do properly.

7. **Performance and latency.** The instance-start path
   currently makes ~3-5 host changes back-to-back in the
   POST `/instances` handler. Under the dependency-chain
   model (open question 1), the handler enqueues a chain of
   ~3-5 NetOps and returns the terminal op's uuid; the
   chain executes asynchronously and the client polls. The
   absolute work is unchanged; the question is whether the
   *chain-defer latency* (a dep finishes, the dependent has
   to be re-dequeued before it observes that) creates a
   visible floor.

   The dispatcher's current 15s defer delay
   (`baseoperation.py:212-240`) is too coarse for chained
   ops. See open question 11 for tuning.

   The existing `redirect_to_network_node` proxy is itself
   a blocking synchronous HTTP round-trip that already
   occupies a gunicorn worker for the full host change
   (just less obviously), so the move to 202+poll for the
   four affected endpoints is a net reduction in worker
   occupancy.

   **Current leaning:** measure end-to-end before adding
   any composite-op batching. The obvious lever if it
   matters is a composite `instance_attach_network` task
   that performs the whole sequence under one queue item;
   trade-off is observability of intermediate steps. That
   goes on future work for now.

8. **What about `enable_nat`?** Resolved. `enable_nat` is
   the one host-mutating method already protected today
   (it's only called from inside `create_on_network_node`'s
   lock). Under the facade it collapses into
   `BridgedVXLanNetwork._apply_enable_nat` — an internal
   worker helper invoked by `_apply_create_on_network_node`,
   not part of the `Network` public surface. Callers who
   today call `enable_nat()` directly become callers of a
   higher-level intent (typically
   `n.create_on_network_node()` which now enqueues the
   composite chain).

9. **Per-node queue lane for sf-net.** The current
   `networknode-*` queues
   (`get_all_network_queues()` in
   `operations/baseoperation.py:87`) are global queues
   that only the elected network node services. They are
   the right home for `create_on_network_node`,
   `add_floating_ip`, `route_address`, `enable_nat`, etc.
   But `create_on_hypervisor` and `ensure_mesh` are
   per-hypervisor mutations — they need to run on each
   node that has interfaces on the network. A second
   queue family, scoped to each node, was added in
   Phase 1 for the local sf-net's net-worker.

   The new family mirrors the existing five-priority
   taxonomy exactly (the priority enum is
   `schema/operations/baseclusteroperation.py:13-17`:
   `user_waiting=10`, `user_facing=20`,
   `user_facing_high_io=25`, `background=30`,
   `background_high_io=40`):

   * `<node_uuid>-network-user_waiting`
   * `<node_uuid>-network-user_facing`
   * `<node_uuid>-network-user_facing_high_io`
   * `<node_uuid>-network-background`
   * `<node_uuid>-network-background_high_io`

   The existing dispatcher drains queues in priority
   order by iterating the list returned from the
   queue-list helper (`workitem.py:36-39`); the per-node
   family is added at the front of that iteration before
   the network-node-only family, so a local user-facing
   op outranks a network-node background op. Within a
   family, the established priority semantics apply
   unchanged.

   Priority assignment:

   * REST handlers that have returned 202 to a waiting
     client enqueue at `user_facing` (or
     `user_facing_high_io` for steps that touch the host
     disk path, e.g. dnsmasq config rewrites).
   * Operations triggered by an interactive CLI / API call
     where a human is blocked on the response use
     `user_waiting` — matching the existing semantics of
     that lane.
   * `maintain`-thread reconciliation ops enqueue at
     `background` (or `background_high_io` for FDB scans
     that may sweep many networks at once).
   * `_apply_*` chains spawned internally by a worker op
     inherit the priority of their parent op rather than
     defaulting to a fixed value.

10. **Scope of API redirect removal.** The
    `redirect_to_network_node` decorator and three of its
    four applications were removed in Phase 7; after the
    facade landed those handlers no longer needed to be on
    the network node. The one remaining application —
    `NetworkPingEndpoint.get` — was explicitly retained
    because migrating the synchronous ping to a
    queue-based op requires op-output infrastructure
    not yet built; it remains deferred future work (see
    Future work section). `redirect_instance_request`
    (~10 sites in instance.py),
    `redirect_to_eventlog_node` (several sites), and
    `redirect_upload_request` (two sites) are the same
    class of bypass and the queue-based pattern
    established here is the future direction for them, but
    each has its own caller fan-out and its own correctness
    concerns. These remain out of scope for this plan
    and are recorded as explicit future work.

11. **Defer delay for chained ops.** The existing
    dispatcher (`baseoperation.py:212-240`) defers a not-
    yet-ready op by re-enqueueing it with a 15-second
    delay. That value works well when the deferral reason
    is "a long-running parent op is still going"; it is
    far too coarse when the reason is "we are chasing the
    tail of an immediate predecessor that's about to
    finish". A three-op chain where each step takes 50 ms
    of host work could spend 30 seconds in defer-induced
    sleep — utterly unacceptable.

    **Resolved approach: exponential back-off, tracked
    in-memory on the net-worker.** The dispatcher
    maintains a `dict[op_uuid -> next_delay]` on the
    worker. First defer for an op uses 100 ms; each
    subsequent defer doubles the entry's value, capped at
    15 s. On terminal state (success, error, abort,
    detected cancellation) the entry is dropped. The
    dispatcher does not re-read op state from MariaDB on
    every defer — it just consults the map and
    re-enqueues with the recorded delay — so the cost of
    a tight back-off is paid in memory, not query load.

    Refinements:
    * **Reset on observed dep-completion.** When the
      dispatcher actually advances op B to executing
      (because A finally finished), B's entry is dropped
      and recreated at 100 ms if B itself later defers
      waiting on C. We do not carry over the back-off
      depth across distinct deps.
    * **Soft map cap.** A safety bound (e.g. 1000
      entries) with oldest-first eviction. If we ever
      reach the cap something else is wrong, but we don't
      want a memory leak in the failure mode.
    * **Cancellation check on dequeue (Phase 1
      sub-step).** Before deciding whether to defer or
      execute, the dispatcher inspects the op's current
      state. If it is `STATE_ABORT`, `STATE_DELETED`, or
      otherwise terminal, the dispatcher drops the
      in-memory entry, resolves the work item, and
      proceeds to the next job. This also fixes a latent
      bug: today `execute()` calls `self.state =
      STATE_EXECUTING`, and `state_targets[STATE_ABORT] =
      (STATE_DELETED,)` only — so executing a
      pre-aborted op raises `InvalidStateException` and
      lands an untrapped traceback in the logs. See open
      question 13 for the broader cancellation work that
      this plan does *not* take on.

    **Critical safety property.** The in-memory map is
    correct *only* because each queue this plan touches
    is serviced by exactly one worker:
    * `<node_uuid>-network-*` queues — only that node's
      `sf-net` net-worker dequeues from them.
    * `networknode-*` queues — only the elected network
      node's `sf-net` net-worker dequeues from them.

    If we ever generalise the queue infrastructure such
    that more than one worker can dequeue from the same
    queue (e.g. a worker pool inside one process, or
    multiple nodes voting on the same queue),
    **two workers can each defer the same op
    independently and double-enqueue it**, breaking the
    delay schedule. The implementation must carry a big,
    scary comment at the map's declaration explaining
    this assumption and warning that any move toward
    multi-worker dequeue requires either a shared map
    (in-process pool, lock-protected) or a return to
    DB-backed state (cross-node). Do not bury this in a
    paragraph — it has to be the first thing a future
    maintainer sees.

    Existing `defer()` callers outside this plan are not
    affected: they continue to pass an explicit
    `delay=...` (or use the unchanged 15 s default). The
    exponential schedule applies only when callers opt in
    via the new chain helper (or, equivalently, when the
    dispatcher itself defers without an explicit delay
    inside the per-node network queues — to be made
    precise in the Phase 1 plan).

    Option (C) from the earlier framing — dispatcher
    re-dequeues dependents on terminal-state transition —
    remains the right long-term answer and is recorded in
    the Future work section. It would remove the need for
    back-off entirely because dependents wake on signal
    rather than retry.

12. **API endpoints for chain discoverability.** A REST
    client that gets a 202 + terminal-op uuid needs a way
    to discover the chain so it can find which step failed
    if the chain aborts. The `depends_on` field is already
    on cluster operations and points the right way
    (dependent → dependency); from the terminal op the
    client can walk backward to find a failed predecessor.
    But there's no "give me the whole chain" endpoint
    today.

    Both endpoints were added in Phase 7:
    * **GET `/clusteroperations/<uuid>/chain`** — returns
      the transitive closure of `depends_on` starting from
      `<uuid>`, scoped to the caller's namespace (admin
      sees everything; non-admin sees only ops targeting
      objects in their namespaces). Useful for the
      "something in this chain failed; where" lookup.
    * **GET `/clusteroperations?target_object_type=...
      &target_uuid=...`** — lists ops targeting a given
      object, scoped by namespace. Useful for "what's
      currently happening on this network/instance". The
      backing query is the same one
      `has_pending_cluster_operation` already uses on the
      `cluster_operation_targets` table.

    Authorisation follows the existing
    namespace-ownership pattern (admin sees all; users see
    ops on objects they own). The
    `cluster_operation_targets` table records enough to
    scope the query at the DB layer; no Python-side
    filtering of full-table scans.

13. **Broader cluster-operation cancellation.** Out of
    scope for this plan, but flagged here so the
    in-scope cancellation-check sub-step in open question
    11 lands in a coherent direction. Today there is
    ad-hoc cancellation processing scattered across the
    codebase (e.g. the dispatcher's "abort if a dep ended
    in ERROR/DELETED/ABORT" rule at
    `workitem.py:84-101`); there is no formal model for
    "operations that should be cancelled when their
    target object is deleted" versus "operations the user
    explicitly requested whose result is independently
    valuable (e.g. snapshot)".

    A plausible shape — to be designed properly in its
    own master plan — is:
    * Each `ClusterOperation` subclass declares whether
      it is cancellable.
    * Each subclass declares whether it blocks state
      transitions of its target object (e.g. a snapshot
      blocks deletion of its instance; an ensure-mesh op
      does not).
    * `hard_delete()` of a parent object sweeps pending
      ops by target: cancellable ones are aborted, ops
      that block transitions force the parent's
      deletion to defer (or fail) until they complete.
    * A "cancel" verb on the cluster-operation REST API
      so users can explicitly cancel cancellable ops.

    **In scope for this plan:** only the dispatcher's
    cancellation-check sub-step in open question 11 (drop
    aborted/deleted ops on dequeue, fix the latent
    `InvalidStateException`). Nothing else.

    **Out of scope and explicit future work:**
    everything above is left for
    `PLAN-cluster-op-cancellation.md` (not yet written).
    This plan should not pre-engineer the per-class
    declarations or the sweep-on-delete behaviour.

Notes:
* **REST contract change.** Since we own both server and
  client (`shakenfist/client-python`), the contract for
  the affected delete endpoints changed from "block until
  the host change is done" to "return 202 + terminal op
  uuid and the client polls". A capability-negotiation
  scheme on the server (so old clients still get the
  blocking behaviour) was considered but explicitly
  declined: we are the only consumer of this API and
  would rather not carry the dual code path. It can be
  revisited if external consumers ever appear.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 0. Stability-branch lock fix (separate, lands now) | (not a sub-plan — see commit on `stability`) | Complete |
| 1. Per-node `sf-net` queue family (five priority lanes mirroring the existing taxonomy) + dispatcher changes: exponential back-off map (100 ms → 15 s, ×2, single-worker safety comment) and cancellation-check on dequeue (drop aborted/deleted ops, fix the latent `InvalidStateException` from executing a pre-aborted op) | [PLAN-network-facade-phase-01-per-node-queues.md](PLAN-network-facade-phase-01-per-node-queues.md) | Complete |
| 2. `BridgedVXLanNetwork` scaffold, `ErrorReport` infrastructure (class, `from_exception` registry, `to_http` mapping, `op.error_report` / `op.raise_for_error` API), and `ensure_mesh` migration | [PLAN-network-facade-phase-02-ensure-mesh.md](PLAN-network-facade-phase-02-ensure-mesh.md) | Complete |
| 3. Floating-IP and route migration | [PLAN-network-facade-phase-03-floating-ip.md](PLAN-network-facade-phase-03-floating-ip.md) | Complete |
| 4. dnsmasq operation migration | [PLAN-network-facade-phase-04-dnsmasq.md](PLAN-network-facade-phase-04-dnsmasq.md) | Complete |
| 5. `create_on_*` and `delete_on_*` migration | [PLAN-network-facade-phase-05-lifecycle.md](PLAN-network-facade-phase-05-lifecycle.md) | Complete |
| 6. `maintain.py` rewrite as discovery-only | [PLAN-network-facade-phase-06-maintain.md](PLAN-network-facade-phase-06-maintain.md) | Complete |
| 7. REST contract: remove `redirect_to_network_node` from three of its four sites, flip the two delete endpoints to 202+poll, add `/clusteroperations/<uuid>/chain` and `/clusteroperations?target_*=` endpoints, update `client-python` | [PLAN-network-facade-phase-07-rest-contract.md](PLAN-network-facade-phase-07-rest-contract.md) | Complete |
| 8. Remove the temporary `NodeLock`s from the stability fix (no per-method migration flags existed to remove — Phases 2–5 migrated each method cleanly in-place) | [PLAN-network-facade-phase-08-cleanup.md](PLAN-network-facade-phase-08-cleanup.md) | Complete |
| 9. Documentation and tests | [PLAN-network-facade-phase-09-docs.md](PLAN-network-facade-phase-09-docs.md) | Complete |

Phase numbering reflects dependency ordering. Phase 1 is
foundational (no per-method migration can happen without per-node
queues); the small, isolated `ensure_mesh` method comes next; the
broad-fan-out lifecycle methods are later; the redirect-decorator
removal sits at phase 7 because all of its callers need the facade
to be in place. Each phase is expected to compile, pass CI, and
leave the cluster in a runnable state; intermediate phases will
have `Network` carrying both the old direct-call API (for
unmigrated methods) and the new facade-routed API (for migrated
methods) in parallel.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean and
avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the plan, at the recommended effort level
   and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's summary
   describes what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether
   the brief was insufficient (improve it) or the model was
   too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied with
   the result.

This applies to all steps, including high-effort ones. If a
sub-agent can't succeed even with a detailed brief and the
right model, that's a signal the brief needs improving, not
that the management session should do the implementation
itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental. The worktree is discarded if the
output is unsatisfactory. For safe, well-understood changes,
sub-agents can work directly in the main tree.

### Planning effort

The master plan itself was created at **high effort** — it
requires broad codebase understanding, cross-referencing
multiple source files, and making judgment calls about scope
and sequencing.

Per-phase guidance:

* Phase 1 (per-node queues) — **high effort**. Touches queue
  infrastructure that every daemon depends on; getting
  routing wrong has cluster-wide consequences. Requires
  understanding the existing `get_all_node_queues` /
  `get_all_network_queues` taxonomy and the
  `dequeue_work_item` claim semantics.
* Phase 2 (`ensure_mesh` migration) — **high effort**. First
  use of the `BridgedVXLanNetwork` pattern; establishes the
  enqueue-and-wait shim, the exception whitelist serialisation,
  and the event-emission split. All subsequent per-method
  phases inherit from these decisions.
* Phases 3–5 (per-method migrations) — **medium effort**
  each. The pattern from phase 2 is in place; each phase
  applies it to a different method group, with phase-specific
  judgment about idempotency and the affected_objects
  parameter for floating-IP methods.
* Phase 6 (`maintain` rewrite) — **high effort**. Subtle
  reconciliation logic; risk of generating no-op churn on
  every interval if the "is it actually wrong" check is not
  carefully ported.
* Phase 7 (REST contract) — **high effort**. Larger than
  pure mechanical: removes `redirect_to_network_node`,
  flips four endpoints to 202+poll, adds two new
  cluster-operation discovery endpoints with namespace-
  scoped SQL authz, and updates `shakenfist/client-python`
  to match. The four sites need careful audit to confirm
  nothing else relies on running on the network node beyond
  what the facade has already moved.
* Phase 8 (cleanup) — **low effort**. Reverts the stability
  fix locks and removes the migration flags. Each removal is
  a small targeted diff.
* Phase 9 (docs) — **medium effort**. Updates
  `ARCHITECTURE.md`, `AGENTS.md`, the API reference docs,
  and the master plan itself. README, the developer guide
  state-machine docs, and the operator-guide network docs
  required no changes (confirmed during Phase 9 planning).

### Step-level guidance

Each phase plan should include a table like this:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
| 1b   | high   | opus   | worktree | Why this needs high effort: requires understanding X to do Y |
```

**Effort levels:**
- **high** — Requires reading multiple files, making judgment
  calls, understanding non-obvious invariants, or researching
  external references. The sub-agent needs to think carefully
  about edge cases.
- **medium** — The plan provides enough context that the
  sub-agent can follow a clear brief. May need to read a few
  files but the approach is well-defined.
- **low** — Purely mechanical changes (rename, reformat, add
  a log line, regenerate proto stubs). The brief is a
  complete instruction.

**Model choice:**
- **opus** — Best for steps that require deep reasoning,
  cross-daemon architectural understanding, subtle
  correctness judgment (locking, state machines, migration),
  or complex protocol research.
- **sonnet** — Good default for well-briefed implementation
  work. Faster and cheaper than opus.
- **haiku** — Suitable for purely mechanical tasks. The brief
  must be a near-complete instruction.

**When in doubt, skew to the more capable model.** Saving
money only matters if the outcome is still acceptable.

**Brief for sub-agent:** Write it as if briefing a colleague
who has never seen the codebase. Include: what to change,
which files to touch, what patterns to follow, and any
non-obvious constraints. The better the brief, the lower the
effort level needed and the lighter the model that can
succeed.

### Management session review checklist

After a sub-agent completes, the management session should
verify:

- [ ] The files that were supposed to change actually changed
      (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code passes `pre-commit run --all-files` (flake8,
      stestr unit tests, mypy).
- [ ] If proto files changed, stubs were regenerated with
      `tox -e genprotos` and committed.
- [ ] The changes match the intent of the brief — not just
      syntactically correct but semantically right.
- [ ] Commit message follows project conventions (including
      the Co-Authored-By line with model, context window,
      effort level, and other settings).
- [ ] For network-facade phases specifically: no new direct
      call to `util_concurrency.*` host-mutating helpers
      appears outside `BridgedVXLanNetwork`; no new caller
      bypasses the queue.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* `grep` for `util_concurrency.create_vxlan_interface`,
  `util_concurrency.ensure_vxlan_mesh`,
  `util_concurrency.add_floating_ip`,
  `util_concurrency.remove_floating_ip` outside
  `shakenfist/network/` and the `BridgedVXLanNetwork`
  implementation returns no hits. The only callers are the
  worker class.
* `grep` for `redirect_to_network_node` returns no hits
  (decorator definition and all four applications removed).
* The four affected REST endpoints return 202 with a
  terminal-op uuid in the response body, not 200 with the
  fully-applied result. The `shakenfist/client-python`
  client transparently polls these handles for callers that
  want synchronous semantics.
* `GET /clusteroperations/<uuid>/chain` returns the
  transitive `depends_on` closure scoped to the caller's
  namespace. `GET /clusteroperations?target_object_type=
  &target_uuid=` lists ops targeting a given object, also
  scoped. Filtering is done at the SQL layer using
  `cluster_operation_targets` — no Python-side filtering of
  full-table scans.
* Chained NetOps built by the new facade helper use the
  in-memory exponential back-off schedule (100 ms first
  defer, ×2 per subsequent defer, 15 s cap) rather than
  the global 15 s default. A three-op chain of short-
  running steps completes without accumulating noticeable
  extra latency from defer back-off.
* The dispatcher contains a prominent comment at the
  declaration of the in-memory back-off map warning that
  its correctness depends on each queue being serviced by
  exactly one worker, and that any change toward
  multi-worker dequeue (in-process pool or cross-node
  voting) requires either a shared / locked map or a
  return to DB-backed state.
* The dispatcher recognises aborted / deleted ops on
  dequeue: it drops the in-memory back-off entry,
  resolves the work item, and proceeds without raising
  `InvalidStateException`. Unit-test coverage for this
  path exists.
* An `ErrorReport` class exists at
  `shakenfist/operations/error_report.py` carrying
  `code`, `message`, `details`, `origin_class`, and
  `traceback`. `ErrorReport.from_exception(exc)` maps
  the four registered network exceptions to stable
  codes (`network.ensure_mesh.failed`, `network.dead`,
  `network.create_vxlan.failed`,
  `network.floating.assign_failed`) and falls back to
  `internal.unknown` with `origin_class` preserved for
  anything else.
* The worker dispatcher's outer `except` converts
  escaping exceptions to `ErrorReport` and persists them
  on the op record. REST endpoints rendering failed ops
  call `ErrorReport.to_http()` rather than catching
  individual exception classes; the traceback is not
  surfaced to clients by default. `op.error_report`
  returns the report on the op object;
  `op.raise_for_error()` raises a single generic
  `NetworkOperationFailed` carrying the report for
  callers that want exception-flow control.
* In-worker `except EnsureMeshFailed:` /
  `except DeadNetwork:` blocks in `net_op.py` and
  siblings continue to work unchanged — the boundary
  translation only applies to exceptions that escape
  past those in-worker handlers.
* The per-network `NodeLock` calls added by the
  stability-branch fix are removed; the `with
  self.get_lock(...)` blocks inside the migrated
  `_apply_*` methods on `BridgedVXLanNetwork` are removed
  too (the single-threaded net-worker is the serialisation
  mechanism now).
* `maintain.py` no longer calls `n.ensure_mesh()`,
  `n.create_on_hypervisor()`, `n.add_floating_ip(...)`, or
  `n.route_address(...)`. It only enqueues `net_op`s.
* `maintain.py` respects the four guards specified in
  open question 6: queue-depth safety, per-network
  gating, cooldown after a failed reconciliation,
  consecutive-failure circuit breaker. A deliberately-
  broken reconciliation (test scaffolding) produces no
  more than `MAINTAIN_RECONCILE_CIRCUIT_K` reconciliation
  ops over an extended period; an operator-visible event
  surfaces the circuit-breaker state.
* The PR #3182 reproduction scenario (two concurrent
  `_ensure_mesh` calls for the same VXLAN) cannot occur:
  there is no second caller on the same node, because the
  net-worker is single-threaded.
* The per-node `<node_uuid>-network-*` queue family
  exposes all five priority lanes
  (`user_waiting`, `user_facing`, `user_facing_high_io`,
  `background`, `background_high_io`). REST handlers
  enqueue at `user_facing`; `maintain` enqueues at
  `background`. Spot-checking under load shows that
  user-facing chains complete ahead of concurrent
  background reconciliation.
* New code follows existing patterns: object lifecycle in
  `baseobject.py`, MariaDB access via the three-layer
  pattern (direct/gRPC/public), Pydantic schemas in
  `shakenfist/schema/`.
* There are unit tests for the enqueue-and-wait helper and
  for the exception whitelist serialisation, and functional
  test coverage in
  `shakenfist/deploy/shakenfist_ci/cluster_ci_tests` exercising the
  migrated paths (instance create, floating IP attach, network
  maintenance reconciliation).
* Lines are wrapped at 120 characters, single quotes for
  strings, double quotes for docstrings.
* `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` describe
  the `Network` / `BridgedVXLanNetwork` split and the
  per-node `<node_uuid>-network-*` queue family. The
  developer-guide state-machine docs and the operator-guide
  network docs are updated.

### Future work

* **General subscription mechanism for terminal-state
  events.** Polling is sufficient for v1 but is not the
  endgame. A clean shape is publishing terminal-state
  transitions through the EventLog so that REST API callers
  (and potentially registered webhooks) can subscribe
  rather than poll. This would replace the polling helper
  transparently and is useful well beyond networks. Out of
  scope here.
* **Dispatcher re-dequeue on dep completion.** Option (C)
  from open question 11: when a cluster operation reaches a
  terminal state, the dispatcher immediately requeues any
  ops that were waiting on it, instead of relying on those
  dependents to wake up via the defer back-off. This is the
  proper long-term replacement for the defer-and-retry
  pattern and removes both the tight-defer-delay knob and
  the in-memory back-off map entirely.
* **Recurring cluster operations framework
  (`PLAN-recurring-operations.md`, stub).** A typed
  `RecurringOperation` object with a cron-like schedule,
  plus the dispatcher changes needed to support it
  (max-wait `runs_after`, continue-on-failure recurrence
  semantics). When this framework lands,
  `daemons/network/maintain.py` is absorbed into it: the
  per-network gating + cooldown + circuit-breaker
  behaviour specified in this plan's open question 6
  moves into a `network_maintain_pass` op triggered by an
  internal `RecurringOperation`, and the maintain thread
  disappears. Other internal consumers
  (`daemons/cluster/scheduled_tasks.py`) migrate at the
  same time. User-facing recurring tasks like "snapshot
  this instance every 24 hours" land in the same
  framework.
* **Broader cluster-operation cancellation
  (`PLAN-cluster-op-cancellation.md`, not yet written).**
  Formalise the ad-hoc cancellation handling scattered
  across the codebase into a coherent model: each
  `ClusterOperation` subclass declares whether it is
  cancellable and whether it blocks state transitions of
  its target object (e.g. a snapshot blocks deletion of
  its instance, an ensure-mesh op does not);
  `hard_delete()` of a parent sweeps pending ops by
  target and either aborts them or defers the parent
  deletion until they complete; a `cancel` verb is
  exposed on the cluster-operation REST API. This plan
  takes only the minimal dispatcher-side cancellation-
  check needed to make exponential back-off correct; it
  deliberately does not touch the per-class declarations
  or the sweep-on-delete behaviour.
* **Capability negotiation on the REST API.** If external
  API consumers ever appear, the server may want to
  advertise the 202+poll contract as a capability and let
  clients signal whether they prefer it (with the blocking
  contract as fallback). Out of scope while we are the sole
  consumer.
* **Extend `ErrorReport` to other subsystems.**
  `ErrorReport` is introduced here for the network
  operations queue boundary, but it is a general
  cluster-operation concept and lives at
  `shakenfist/operations/error_report.py` rather than
  inside `shakenfist/network/`. Other subsystems
  (artifact, blob, instance lifecycle) should adopt the
  same pattern as their operations migrate to the same
  shape: register their exceptions in
  `ErrorReport.from_exception` with stable codes, and
  use `op.error_report` / `op.raise_for_error` on the
  poll side. Each subsystem can adopt independently;
  the registry is open for extension.
* **Generic background-lane starvation fix.** If
  `background` / `background_high_io` reconciliation ops
  are observed to starve behind a steady stream of
  user-facing work, the fix is a generic work-queue
  fairness change (e.g. weighted draining across priority
  lanes, occasional forced dequeue from the next-lower
  lane) applied to every queue family, not a special case
  for the network family. Out of scope here; record the
  observation if it surfaces during phase rollout.
* **Explicit cleanup-on-error chains.** If a concrete case
  arises where the convergence model is genuinely
  insufficient — i.e. partial state left by a failed chain
  cannot be reliably reconciled by `maintain` or the
  cleaner, and the caller has a precise idea of what to
  undo — we could extend the dependency vocabulary with a
  `runs_on_error_of` variant so that a caller can enqueue a
  cleanup op that fires only when a specific chain step
  ends in `ERROR`. This is much lighter than full saga
  support: cleanup is just another op the caller chooses to
  enqueue, paired with a specific failure trigger, rather
  than a per-op compensator that every operation must
  implement. Deferred until a real need surfaces; we should
  not pre-engineer this.
* **Composite per-instance network operations.** The
  instance-start path performs ~3-5 host changes
  back-to-back; if queue round-trip latency dominates,
  collapsing the sequence into a single
  `instance_attach_network` composite op amortises the
  cost. Worth doing only with measured evidence.
* **Apply the facade pattern to `NetworkInterface` and
  `IPAM`.** Each has its own audit work but the same
  bypass problem exists in principle.
* **Queue-based ping endpoint migration.** Phase 7
  retained the `@redirect_to_network_node` decorator on
  `NetworkPingEndpoint.get` because migrating a
  synchronous ping to the queue-based op model requires
  op-output infrastructure (a way to surface the ping
  result back through the op terminal state) that is not
  yet built. This remains deferred beyond this master
  plan.
* **Remove the remaining API redirect decorators.**
  `redirect_instance_request`,
  `redirect_to_eventlog_node`, and
  `redirect_upload_request` are structurally the same
  problem as `redirect_to_network_node`. Each needs its
  own audit because the operations they proxy do not all
  have queue-based equivalents today.
* **Move the REST API layer to its own tier of machines.**
  If the API tier ever becomes the bottleneck, separating
  gunicorn workers from the compute/network nodes lets
  them scale independently. Not required for v1.
* **Promote the per-node `<node_uuid>-network-*` queue
  family to a general "per-node daemon-owned" pattern.**
  If other daemons end up needing the same "this work must
  run on this specific node, serialised through a single
  worker" shape, the queue infrastructure introduced here
  is a starting point.

### Bugs fixed during this work

* **Latent `InvalidStateException` on pre-aborted ops.**
  (Fixed in Phase 1.) Before the cancellation-check on
  dequeue was added, the dispatcher could pick up an op
  already in `STATE_ABORT`, call `self.state =
  STATE_EXECUTING`, and hit `InvalidStateException`
  because `state_targets[STATE_ABORT] = (STATE_DELETED,)`
  only. The Phase 1 cancellation-check drops such ops
  cleanly before attempting to execute them.

### Documentation index maintenance

When this master plan is updated:

* **`docs/plans/index.md`** — the row for this plan should
  track the current overall status (Planning → In Progress
  → Complete). Phase rows are not added to `index.md`;
  phases are tracked in the Execution table above.
* **`docs/plans/order.yml`** — this master plan is already
  listed; phase files are not added to `order.yml`.

When all phases of this plan are complete, update the status
column in `docs/plans/index.md`.

### Back brief

Before executing any step of this plan, the implementing
sub-agent must back brief the operator as to its
understanding of the phase plan and how the work it intends
to do aligns with that plan.
