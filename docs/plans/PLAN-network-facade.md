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
database access patterns. Key references inside the repo
include `shakenfist/network/network.py` (the `Network` class
under discussion), `shakenfist/operations/net_op.py` (the
worker dispatch), `shakenfist/daemons/network/workitem.py`
(the single-threaded `net-worker`), `shakenfist/daemons/network/maintain.py` (the parallel `maintain` reconciliation
thread), `shakenfist/daemons/privexec/main.py` (the
privileged-execution daemon that actually mutates kernel
network state), and `shakenfist/mariadb.py` (the existing
three-layer database access pattern, which is the architectural
precedent for the proposed change).

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
the same node — interacts via a typed facade that enqueues
intents and (where appropriate) awaits the result. After the
change:

* There is no path by which `sf-queues`, `sf-api`,
  `sf-cleaner`, the instance lifecycle code, or the
  `maintain` reconciliation thread can directly call
  `util_concurrency.ensure_vxlan_mesh`,
  `util_concurrency.add_floating_ip`, or any other
  host-mutating privexec helper for a network. They go
  through the facade.
* The single-threaded `net-worker` is the only mutator and
  therefore naturally serialises all activity for a network
  on a node — `NodeLock` becomes redundant for these methods
  and the locks added by the stability-branch fix can be
  removed.
* The `maintain` reconciliation thread in `sf-net` no longer
  calls the same methods as the net-worker; instead it
  enqueues `net_op`s and lets the net-worker do the work.
* The "queue-jumping" fairness concern (a node that's also
  the network node, or just a node running both `sf-net`
  and `sf-queues`, can bypass the work queue) disappears
  because no caller has a bypass to take.

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
* **In scope:** changing the call signature/contract of the
  affected `Network` methods. Callers will need to handle
  enqueue-and-wait semantics, and operation failures will
  surface differently.
* **Out of scope:** the `NetworkInterface` and `IPAM`
  classes. Those have their own concerns (IP reservation,
  interface attach/detach) that overlap with networks but
  are not the same problem. They may benefit from the same
  pattern later, but each has its own audit work.
* **Out of scope:** the existing `nodelock`-based fix on the
  `stability` branch. That fix stays in place until the
  facade refactor lands and is proven; it can be removed in
  the final phase as cleanup.
* **Out of scope:** changing how `net_op`s are queued or how
  the cluster decides which node owns a network. The work
  queue, queue prioritisation, and network-node election are
  unchanged.

## Open questions

1. **Synchronous-wait API.** Today
   `n.create_on_hypervisor()` returns when the host change
   is complete; many callers depend on that (e.g.
   `node_inst_netdesc_op.py:243` calls `n.create_on_hypervisor()`
   then `n.ensure_mesh()` then `n.update_dnsmasq()` in
   sequence, relying on each having finished before the
   next runs). The facade needs an equivalent
   "enqueue-and-wait" idiom. Options: (a) a blocking
   helper `facade.do_and_wait(...)` that polls the op state;
   (b) an explicit two-step `op = facade.enqueue(...)`
   followed by `facade.wait(op)`; (c) make every method a
   coroutine and provide both sync and async variants.
   **Current leaning:** (a). It preserves the call shape
   callers expect today, surfaces errors via exception,
   and concentrates the polling logic in one place. The
   instance-start path is the largest beneficiary because
   it composes several host changes back-to-back; pushing
   the await pattern into every call site would be noisy.

2. **Where the facade lives.** Three plausible locations:
   (a) a new module `shakenfist/network/facade.py` exposing
   module-level functions keyed by network UUID; (b) a new
   `NetworkFacade` class that wraps a `Network` instance and
   exposes the intent API; (c) keep the methods on `Network`
   but have them dispatch to "do locally" or "enqueue"
   based on whether the caller is the net-worker. Option (c)
   re-creates the bypass we are trying to remove (the
   net-worker now needs an explicit escape hatch), so it's
   not really a separation. Between (a) and (b), the class
   form is more discoverable because callers already hold a
   `Network` reference. **Current leaning:** (b) — add
   `NetworkFacade(network)` as the public surface and rename
   the existing direct-call methods on `Network` to internal
   helpers (e.g. `_apply_create_on_hypervisor`) that only the
   net-worker invokes.

3. **Error propagation.** Today exceptions like
   `CreateVXLANInterfaceFailed`, `DeadNetwork`,
   `EnsureMeshFailed`, `CannotAssignFloatingGateway` are
   raised inline. With a queue indirection the exception
   has to be serialised in the operation's failure record
   and re-raised on the caller side. We already store
   operation state and error messages on the `*_op`
   classes, but not structured exception types. Options:
   (a) re-raise a single generic `NetworkOperationFailed`
   exception with the underlying message text; (b) include
   the exception class name in the op record and dispatch
   on it client-side; (c) keep exception fidelity for a
   small whitelist and degrade the rest to the generic
   form. **Current leaning:** (c). The caller-side switch
   on `EnsureMeshFailed` in `net_op.py:89-94` and on
   `DeadNetwork` in `node_inst_netdesc_op.py` are the
   patterns worth preserving; the others are logging-only.

4. **What happens to the `Network` class itself.** Two
   shapes: (a) keep `Network` as the data carrier
   (object_states, attributes, queries) and add a separate
   `NetworkFacade` for intent; the worker code that lives
   inside `net-worker` calls thin internal helpers on
   `Network`. (b) split into `NetworkRecord` (data) and
   `NetworkWorker` (intent target, used only inside
   `net-worker`) with `NetworkFacade` as the public face. (a)
   is a smaller change; (b) is cleaner. **Current leaning:**
   (a), to keep the diff manageable and reduce the
   migration surface.

5. **Migration order.** The fan-out of direct call sites
   is broad (instance.py, four operations modules, two
   daemons). All-at-once migration is risky; doing it
   per-method (`ensure_mesh` first, then `add_floating_ip`,
   etc.) is safer but means `Network` carries both shapes
   for a while. **Current leaning:** per-method migration,
   with a temporary `Network` shim that calls the facade
   for callers that haven't migrated yet. Each phase
   removes one method's direct callers and removes the
   shim line.

6. **Behaviour of `maintain.py`.** The reconciliation
   thread today walks all networks every interval and
   re-applies host state where it has drifted. Under the
   facade it would instead enqueue reconciliation ops. That
   has two implications: (a) `maintain` becomes much
   smaller, basically a scanner that compares observed vs.
   desired and enqueues deltas; (b) the net-worker has to
   absorb the resulting op rate, which today is mostly
   idle. Need to make sure `maintain` does not enqueue
   no-op ops on every interval. **Current leaning:** at
   each pass, `maintain` does the discovery (e.g. `bridge
   fdb show`) and only enqueues when something is actually
   wrong, mirroring its current "Recreating not okay
   network on hypervisor" logic.

7. **Performance and latency.** The instance-start path
   currently makes ~3-5 host changes back-to-back in the
   POST `/instances` handler. Each change becomes
   enqueue-plus-wait, with the queue round-trip dominated
   by polling latency. We should measure the floor; if the
   round-trip is >100 ms per op we'd be making
   instance-start noticeably slower. **Current leaning:**
   measure before designing the wait helper. If
   round-trips are bad, batch the steps into a single
   composite op (e.g. `instance_attach_network`) so the
   net-worker performs the whole sequence under one queue
   item.

8. **What about `enable_nat`?** It's the one host-mutating
   method already protected (it's only called from inside
   `create_on_network_node`'s lock). Under the facade it
   collapses into the implementation of
   `create_on_network_node`'s queued op rather than being a
   public method at all. **Current leaning:** make
   `_enable_nat` an internal worker helper, not part of
   the facade. Callers who today call `enable_nat()`
   directly become callers of a higher-level intent.

Please confirm the leanings above before phase 1 planning
begins.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 0. Stability-branch lock fix (separate, lands now) | (not a sub-plan — see commit on `stability`) | Complete |
| 1. Facade scaffold and `ensure_mesh` migration | TBD | Planning |
| 2. Floating-IP and route migration | TBD | Planning |
| 3. dnsmasq operation migration | TBD | Planning |
| 4. `create_on_*` and `delete_on_*` migration | TBD | Planning |
| 5. `maintain.py` rewrite as discovery-only | TBD | Planning |
| 6. Remove the temporary `NodeLock`s and the shim | TBD | Planning |
| 7. Documentation and tests | TBD | Planning |

Phase numbering reflects rough complexity ordering — small,
isolated method first; the broad-fan-out lifecycle methods
last. Each phase is expected to compile, pass CI, and leave
the cluster in a runnable state; intermediate phases will
have `Network` carrying both the old direct-call API and the
new facade-routed API in parallel.
