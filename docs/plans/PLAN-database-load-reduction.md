# Reduce steady-state MariaDB load from the sf-database tier

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
(gRPC metadata and interceptors, Prometheus label
cardinality, MariaDB connection behaviour), research as
needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Key references inside the repo
include `shakenfist/daemons/daemon.py` (the `idle()` /
`check_daemon_state()` loop this plan targets),
`shakenfist/node.py` (Node object and daemon-state
accessors), `shakenfist/baseobject.py` (object lifecycle and
the version cache), `shakenfist/mariadb.py` (three-layer
database access pattern and the `_use_database_service()`
routing decision at line ~293), and
`shakenfist/daemons/database/main.py` (gRPC database daemon
and its per-operation Prometheus counters).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases is
done via the table in the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The sfcbr cluster (six nodes, two of them database nodes)
shows a steady-state load of roughly 527 operations per
second against the sf-database gRPC tier, measured from the
`database_*_total` Prometheus counters over a quiet ten
minute window on 2026-07-19. The top of the table:

| Rate | Operation |
|------|-----------|
| 154/s | `get_node` |
| 149/s | `get_node_daemon_state` |
| 38/s | `dequeue` |
| 34/s | `get_ipam` |
| 23/s | `get_blob` |
| 19/s | `get_blob_transfers_for_node` |
| 19/s | `get_existing_locks` |
| 18/s | `get_references_from` |
| 17/s | `get_instance_attributes` |
| 10/s | `get_object_state` |

The two node operations are 57% of the total, and a code
trace shows essentially all of that comes from a single
path. `Daemon.idle(seconds)`
(`shakenfist/daemons/daemon.py:379`) splits every sleep into
0.2 second sub-ticks and calls `check_daemon_state()` on
every tick regardless of the requested sleep length.
`check_daemon_state()` (`daemon.py:336`) then performs two
database round trips per tick:

1. `Node.this_node()` → `mariadb.get_node()` — a fresh
   fetch of the node's static row.
2. `n.get_daemon_state(self.daemon_name)` →
   `mariadb.get_node_daemon_state()` — a fresh fetch of the
   daemon's own state row (this is how a daemon notices it
   has been asked to stop).

Roughly eight daemons per node sit in this loop
(net, cleaner, queues, sidechannel, resources, cluster,
transfers, database), so the theoretical ceiling is
8 daemons × 5 Hz × 6 nodes ≈ 240/s of *each* operation; the
measured 154 + 149/s is that picture with some daemons busy
rather than idle.

Secondary contributors on the `get_node` side:
`_maintain_version_cache()` (`shakenfist/baseobject.py:55`)
does a per-node `get_node()` on every cache refresh just to
recover the fqdn (~20/s cluster-wide via the daemons that
call `cluster_stable()` with a five second cache age); the
sentinel daemons' `observe_this_node()` check-ins are
negligible (~0.8/s).

Two architectural facts matter for the fix:

* **No result caching exists anywhere in the stack.** Not in
  `Node.from_db()`, not in the gRPC client in `mariadb.py`,
  not server-side in the sf-database servicer methods (which
  are pure pass-throughs: increment counter, run SQL,
  marshal reply). The only TTL caches anywhere are the
  object-version cache in `baseobject.py` (300s) and the
  health/readiness flags (5s).
* **No invalidation primitive exists.** The etcd watches are
  gone and nothing replaced them: no pub/sub, no triggers,
  no streaming RPCs. The gRPC server deliberately serves
  only unary calls — streaming health `Watch` deadlocked the
  synchronous servicer against the single event-dispatch
  thread and is documented as forbidden
  (`daemons/database/main.py` near the health servicer
  registration).

The old etcd design principle was "objects are cacheable,
attributes of objects are not". The structural split
survived the MariaDB migration (static values tables vs.
attribute/state tables), but the caching half of the
principle was never reimplemented: today we do not even
cache immutable static object data. `NodeData` is
`{uuid, fqdn, ip, version}` — four effectively constant
fields being re-fetched 150 times a second.

## Mission and problem statement

Reduce steady-state sf-database load to the point where the
MariaDB instance genuinely does not care about it — a target
of under 100 operations per second cluster-wide for the
current sfcbr shape — without weakening correctness
(shutdown responsiveness, upgrade version gating, or the
freshness of mutable state), and while restoring the
objects-cacheable / attributes-not caching principle in a
form that fits the MariaDB world.

Explicitly in scope: eliminating redundant reads on hot
periodic paths, a narrow client-side cache for immutable
static object values, caller attribution on the existing
per-operation counters so future regressions are
diagnosable, and data-driven reduction of the next tier of
operations (`dequeue`, `get_ipam`, `get_existing_locks`,
`get_blob_transfers_for_node`).

Explicitly out of scope (deferred, see Future work): any
watch/subscribe change-notification mechanism, and any
caching of mutable attribute or state data.

## Decisions

These were discussed before drafting and are recorded here
so the phase plans do not reopen them:

1. **Fix the callers before adding caches.** The dominant
   load is a 5 Hz poll re-reading data it already has. The
   first move is to stop issuing those reads, not to make
   them cheaper.
2. **Cache only immutable static object values.** With no
   invalidation channel, mutable data (states, attributes,
   metadata, daemon states) stays strictly read-through.
   This is the etcd principle restated for MariaDB.
3. **Consolidate the gRPC client stacks before
   instrumenting — which investigation reduced to removing a
   dead one.** There appeared to be three code paths opening
   a channel to the sf-database daemon: `mariadb.py`'s
   `_get_database_stub()` / `_grpc_call()`, `database.py`'s
   `get_database_client()` / `_retry_database` (locks,
   cluster config), and `config.load_cluster_config()`'s
   inline one-shot at import time. Reading the code showed
   `shakenfist/database.py` is **orphaned**: nothing outside
   its own unit test imports it. The live lock path is
   `locks.py` → `mariadb.py`
   (`acquire_cluster_lock` / `release_cluster_lock` /
   `get_all_cluster_locks` / …) → `_grpc_call`, and the live
   cluster-config path is `mariadb.get_cluster_config()` /
   `set_cluster_config()` → the same `_grpc_call`. Commit
   `e48d3257f` ("Route cluster locks through the three-layer
   mariadb API") moved locks off `database.py` and left its
   `acquire_lock` / `get_cluster_config` / … functions
   stranded; the only later edits to the file were mechanical
   (the channel-factory refactor, a config rename). So
   `database.py`'s distinct retry decorator and 200ms
   keepalive are not "a second live policy to reconcile" —
   they are dead. Consolidation is therefore deleting
   `database.py` (and its test), leaving `mariadb.py`'s
   `_grpc_call` as the single live client with the one
   deliberate exception of the import-time bootstrap
   one-shot in `config.py` (which genuinely cannot import a
   not-yet-initialised `config` and stays as-is). Phase 4
   attribution then hangs on exactly one interceptor seam.
4. **Caller attribution rides gRPC metadata and is
   mTLS-compatible.** The planned `PLAN-embrace-tls.md` work
   will (per its open question 3) validate peer certificates
   with at most node-or-role granularity SANs; certificates
   will be per-node, not per-daemon-process. Daemon-level
   attribution therefore always needs an application-level
   channel regardless of mTLS. After the phase 3
   consolidation there is exactly one client stack to
   instrument — a single interceptor attaching
   `caller-daemon` and `caller-node` metadata keys — and when
   mTLS lands, the server can additionally cross-check the
   `caller-node` claim against the verified peer SAN. mTLS
   hardens this path rather than replacing it; there is no
   second attribution mechanism later.
5. **Watch/subscribe is deferred, not designed here.** The
   server threading model forbids streaming RPCs today, and
   the expectation is that phases 1–5 make the remaining
   poll load unimportant. If that turns out wrong, the
   sketch in Future work (batched "changed versions since
   X" unary poll) is the starting point, informed by
   post-phase-4 per-caller numbers.

## Open questions

1. **Daemon-state poll interval.** Phase 1 decouples the
   daemon-state read from the 0.2s tick. The provisional
   interval is 2 seconds (worst-case shutdown latency 2s,
   well inside the systemd stop timeout, and a 10x load
   reduction). Confirm nothing depends on sub-second
   observation of externally-set daemon state — the
   phase 1 plan must grep for writers of daemon state other
   than the daemon itself (e.g. `sf-ctl`, cluster daemon)
   and check their expectations.
2. **TTL for the static-values cache.** *(Resolved in the
   phase 2 plan.)* Two config-tunable tiers, both TTL-bounded
   (0 disables): immutable types with no post-creation writer
   (Instance, Network, NetworkInterface, AgentOperation) at a
   long TTL (default 300s, bounding only cross-process
   deletion); upgradeable types (Blob, Node, Artifact,
   Upload, DnsMasq, Namespace) at a short TTL (default 30s,
   bounding rare cross-process upgrade-persist). No unbounded
   "process-lifetime" entries — a cross-process `hard_delete`
   would otherwise resurrect a dead object. Misses are never
   cached; the public `update_<type>`/`delete_<type>` hooks
   invalidate local writes, and because `_persist_pydantic_upgrade`
   routes through the public `update_<type>`, the cache
   self-heals after an online upgrade.
3. **Confirm `database.py` is fully dead before deleting.**
   Decision 3 concludes it is orphaned. The phase plan's
   first step must re-verify this against the tip of the
   branch it executes on: no non-test import of
   `shakenfist.database`, no dynamic/string import, no deploy
   or console-script reference, and the live lock/config
   paths demonstrably route through `mariadb.py`. If any live
   consumer survives, do not delete — fall back to the
   shared-core extraction (per the phase 3 summary). Also
   decide whether to keep or delete the `is_available()` /
   `reset_client()` helpers if anything references them
   (the sweep found none).
4. **Counter label shape.** *(Resolved in the phase 4
   plan.)* Rather than relabel the ~150 existing
   `database_<op>_total` counters (invasive, breaks current
   queries), phase 4 adds one additive counter
   `database_requests_total{operation, caller_daemon}`
   incremented centrally by a server interceptor. `caller_node`
   is sent as gRPC metadata (for the mTLS cross-check) but not
   used as a label — the `operation` axis (~150) dominates
   cardinality and there are only ~6 nodes recoverable via
   `context.peer()`. Existing dashboards are unaffected; a new
   panel uses the additive counter.
5. **Is success criterion 2 still the right criterion?**
   *(Raised and resolved 2026-08-19 in phase 6: no. Success
   criterion 2 has been restated as an absolute floor, and
   measured met.)* "`get_node`
   and `get_node_daemon_state` no longer appear in the top
   five operations by rate" was written against the 527/s
   picture, where those two were 57% of load. `get_node` is
   gone as intended. `get_node_daemon_state` is not, and
   cannot be: phase 1 reduced it from ~149/s to ~20/s, but
   that ~20/s is the designed floor of 48 daemon processes
   polling at 0.5 Hz, and it is now the *second* operation by
   rate precisely because everything around it got so much
   cheaper. Being high in a much shorter list is not the
   same defect the criterion was written to catch. Phase 6
   Restated as an absolute floor rather than a ranking. The
   ranking form would also have failed on a healthy cluster
   that simply had fewer object types in play, which is the
   general problem with ranked criteria.

6. **Does the queues daemon need a 0.2s dequeue poll?**
   `dequeue` at 38/s suggests tight-loop polling by the
   queues and transfers daemons. Adaptive backoff when the
   queue is empty (e.g. 0.2s → 2s ramp, reset on work)
   is the obvious shape, but the phase 5 plan must check
   coalescing behaviour and CI latency sensitivity first.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Stop the idle-loop polls | [PLAN-database-load-reduction-phase-01-idle-loop.md](PLAN-database-load-reduction-phase-01-idle-loop.md) | Complete |
| 2. Static object value caching | [PLAN-database-load-reduction-phase-02-static-cache.md](PLAN-database-load-reduction-phase-02-static-cache.md) | Complete |
| 3. Consolidate the gRPC client stacks | [PLAN-database-load-reduction-phase-03-client-consolidation.md](PLAN-database-load-reduction-phase-03-client-consolidation.md) | Complete |
| 4. Caller attribution on counters | [PLAN-database-load-reduction-phase-04-attribution.md](PLAN-database-load-reduction-phase-04-attribution.md) | Complete |
| 5. Next-tier reductions | [PLAN-database-load-reduction-phase-05-next-tier.md](PLAN-database-load-reduction-phase-05-next-tier.md) | Complete |
| 6. Residual load and the regression | [PLAN-database-load-reduction-phase-06-residual-load.md](PLAN-database-load-reduction-phase-06-residual-load.md) | Complete |
| 7. Deployer-visible regression detection | [PLAN-database-load-reduction-phase-07-regression-detection.md](PLAN-database-load-reduction-phase-07-regression-detection.md) | In progress |
| 8. Push audit | PLAN-database-load-reduction-phase-08-push-audit.md | Not started |

Phase summaries:

**Phase 1 — stop the idle-loop polls.** Two independent
changes in `daemons/daemon.py`: (a) `check_daemon_state()`
stops constructing a `Node` at all — it needs only the node
UUID, which `Daemon._resolve_node_uuid()` already places in
`config.NODE_UUID` at process startup — eliminating the
`get_node` call entirely; (b) the `get_node_daemon_state`
read is decoupled from the 0.2s tick and performed at most
every `DAEMON_STATE_POLL_INTERVAL` (provisionally 2s),
while the tick itself continues to provide loop
responsiveness. Expected effect: ~150/s of `get_node` and
~125/s of `get_node_daemon_state` removed; cluster total
drops from ~527/s to roughly 250/s. Measured before/after
via the existing counters. This phase is deliberately
small and ships alone.

**Phase 2 — static object value caching.** A narrow
read-through cache for immutable static object values,
client-side in `mariadb.py` above the `_use_database_service()`
branch so both direct and gRPC callers benefit. A single
process-global dict keyed `(type, uuid)` → frozen model under
a lock, TTL-bounded in two tiers (immutable types at ~300s,
upgradeable types at ~30s; 0 disables), invalidated on the
public `update_<type>`/`delete_<type>` so it self-heals after
online upgrades, and never caching misses. Explicitly excludes
states, attributes, metadata, daemon states, IPAM, queues and
locks. Also fixes `_maintain_version_cache()` to source fqdn
from `get_all_node_metrics()` rather than per-node `get_node()`
calls. Expected effect: remaining steady-state `get_node` load
(~20/s) approaches zero, the hot Blob/Node/Instance point
re-hydration in the scheduler/cluster/resources loops drops,
and bursty API object hydration gets cheaper. Detailed design,
including the full invalidation surface and correctness
invariants, is in the phase 2 plan file.

**Phase 3 — consolidate the gRPC client stacks (remove the
orphan).** Investigation (recorded in Decision 3 and the
phase plan) found `shakenfist/database.py` is dead: no
non-test importer, superseded by commit `e48d3257f`. The
work is therefore to delete `shakenfist/database.py` and
`shakenfist/tests/test_database.py`, fix the now-stale
reference to `database.py`'s 200ms keepalive in the
`util/grpc_channel.py` docstring, and confirm the delete is
behaviour-neutral (nothing live changes path). The live
client stays `mariadb.py`'s `_grpc_call` / `_get_database_stub`;
the import-time bootstrap one-shot in `config.py` is left as
the one deliberate exception. Sole external effect: phase 4
attribution now has exactly one interceptor seam instead of
two. CI (which exercises lock contention, cluster config,
and bootstrap) must stay green. If the phase plan's
verification instead finds any live consumer of
`database.py`, the phase falls back to the original
extract-a-shared-core design (preserved in git history of
this plan) rather than a delete.

**Phase 4 — caller attribution on counters.** A single gRPC
client interceptor on the phase 3 consolidated client
attaches `caller-daemon` / `caller-node` metadata (caller
daemon from a process-global set at startup, node from
`config.NODE_NAME`); a gRPC server interceptor on sf-database
increments one new additive counter
`database_requests_total{operation, caller_daemon}`, leaving
the ~150 existing `database_<op>_total` counters — and every
dashboard/alert on them — untouched. The Grafana dashboard
(in the separate 33fl repo) gains a per-caller breakdown
panel. Attribution must be server-side because only three
daemons expose a scraped metrics endpoint. Designed per
Decision 4 to compose with the future mTLS work (the
`caller-node` metadata is what mTLS later cross-checks against
the peer SAN) rather than duplicate it. Detailed design,
including the label-cardinality resolution of open question 4,
is in the phase 4 plan file.

**Phase 5 — next-tier reductions, diagnosed and ratcheted.**
Phase 4 has now been deployed on `sfcbr` for several days,
so the targets are diagnosed from 24h per-caller data. That
data corrected the guess: total steady-state load is ~174/s
(down from ~527/s), and the floor is dominated not by
`GetIPAM` (which averages ~14/s but bursts to ~48/s under
load) but by fixed-rate idle polling — `Dequeue` (38.8/s:
net + queues), `GetExistingLocks` (19.2/s: queues) and
`GetBlobTransfersForNode` (19.5/s: transfers), ~78/s of
workload-independent cost. The phase splits in two: the
individual reductions are filed as issues #3499 (queue-poll
backoff, highest), #3500 (transfer-poll backoff), #3501
(cluster IPAM re-reads) and #3502 (cluster sweep re-reads),
to be fixed and reviewed separately; and phase 5's own
deliverable is a ratchet — teaching the nightly infra report
precompute (in the 33fl ops repo) to mine per-caller
sf-database load, encode the honest 24h baseline, and flag
regressions so the gains cannot silently rot. Detail,
including the baseline-honesty argument, is in the phase 5
plan file. **Outcome:** all four issues
landed; the ~78/s of workload-independent polling they
targeted is now ~9.5/s and every `known-reducible` baseline
entry reads `cleared`. The ratchet is live and has already
both corrected a false regression (standing-instance-count
scaling, which is why several baselines are now per-instance
coefficients rather than absolute ceilings) and caught the
real one that phase 6 exists to chase.

**Phase 6 — the residual load and the regression.** Phase 5
appeared to drive the cluster to 89-92/s on 2026-08-05 to
2026-08-07, below this plan's target, after which it climbed
back to ~142/s at a *lower* standing instance count. Phase 6
was created with two jobs which must not be confused: find
what *regressed* since 2026-08-07, and reduce the residual
floor that was always there. Its re-measurement step found
that the first job was chasing a measurement artefact — see
below — while doing it anyway turned up two real defects. Its targets were the ~19/s of `GetObjectState` from
the cluster daemon (#3814), the floating-IP maintenance
path's per-address reservation sweeps (#3655, ~10/s),
`GetReferencesFrom`/api above its per-instance ceiling, the
`POST /auth` re-authentication volume, and the ~22/s long
tail spread across ~361 low-rate pairs no baseline watches.
It also resolves Open question 5.

The phase is complete: 6a-6f landed in PR #3818 and 6g
re-measured three clean days afterwards. Three claims made
above before the work started turned out to be wrong, and
are corrected here rather than left to mislead.

The ~19/s was **not** `_cluster_wide_cleanup()`'s 60s
duty-cycle gate: the raw counter series showed ~15,200 calls
arriving in a burst every 16 minutes, which is a
`schedule.every(15).minutes` job, and the cost was
`reap_expired_namespace_keys()` reading `key.state` once per
key. That fix and #3655 together removed a measured ~21/s,
confirmed on three separate days against a nine-day
regression fit.

The other two corrections are the same mistake seen twice.
The cluster did **not** grow from four nodes to six on
2026-08-12; `sfcbr` has been `sf-1` through `sf-6` throughout.
`database_requests_total` is incremented by a gRPC server
interceptor, and until #3708 landed every daemon co-located
with MariaDB bypassed the tier, so the counter could see four
of six nodes — and, whenever the cluster maintenance lock sat
on one of those two nodes, none of the cluster daemon at all.
2026-08-05 to 2026-08-07 are precisely such a window, so
**the target was never actually met** and the "regression"
was in substantial part the measurement widening. Only
figures taken after 2026-08-11 are comparable with each
other. See the phase plan's Findings and 6g sections.

**Phase 7 — deployer-visible regression detection.** The
only thing standing between us and a silent repeat of this
regression is a nightly job in a private operations
repository watching one cluster. Phase 7 moves the
capability into the product: a committed load *model*
(a per-node base plus per-standing-object coefficients, the
one genuinely portable thing the hunt produced — an absolute
QPS expectation tells a deployer nothing about their own
cluster), a functional-CI check that fails when a change
adds a new fixed-rate poll, drop-in Prometheus rules and an
`sf-ctl` subcommand for deployers without a monitoring
stack, and a public dashboard that is no longer worse than
our private one. The private report then becomes one
consumer of a public mechanism.

**Phase 8 — push audit.** Runs `PUSH-AUDIT.md` over the
accumulated diff of every phase in this plan against
`develop`, not the last phase's diff alone. Findings land as
their own pull request, and the plan is not complete until
each is resolved or declined in writing here. If the audit
finds nothing, that is recorded in one sentence.

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

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental. The worktree is discarded if the
output is unsatisfactory. For safe, well-understood changes,
sub-agents can work directly in the main tree.

### Planning effort

Phase 1 touches the shutdown-signalling path of every daemon
and should be planned at **high effort** despite the small
diff — the correctness question (who else writes daemon
state, and how quickly must a daemon observe it) is subtle.
Phase 2 involves cache-correctness judgment and should also
be planned at high effort. Phase 3 turned out to be a dead-code
removal (the live lock/config path never went through
`database.py`), so it is low-risk and can be planned at
medium effort — but the phase plan's dead-code
re-verification step is mandatory and gated: if it finds a
live consumer, the phase reverts to a behaviour-preserving
shared-core extraction and should then be re-planned at high
effort. Phase 4 is largely
mechanical plumbing following existing counter patterns and
can be planned at medium effort, and now hangs on a single
consolidated client rather than two. Phase 5 cannot be
planned until phase 4 data exists.

### Step-level guidance

Each phase plan should include the step table
(step / effort / model / isolation / brief) per
`PLAN-TEMPLATE.md`. Briefs should front-load the research
already recorded in this master plan — for example, phase 1
briefs should cite `daemon.py:336-385`, `node.py:291-309`
and `config.NODE_UUID` resolution directly rather than
asking the sub-agent to rediscover the call chain.

### Measurement discipline

Every phase lands with a before/after measurement from the
`database_*_total` counters on the sfcbr cluster (the
33fl Prometheus at maui already scrapes both database
nodes). Record the numbers in the phase plan when marking
it complete. If a phase does not move its predicted
operations, that is a finding to investigate, not a detail
to skip past.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Steady-state sf-database load on a quiet sfcbr cluster is
  at or below 100 operations per second, measured over a
  window of at least thirty minutes from the phase 4
  per-caller counter; and cluster load overall is described
  by a model carrying a per-node term as well as a
  per-standing-instance one, rather than by a single number.
  *(Restated in phase 6's re-measurement, 2026-08-24.
  **Not met, narrowly**: a quiet sfcbr -- six nodes, its
  floor of eight standing instances -- measures 102.4/s,
  against 134.0/s for the same measurement before phase 6.
  The model is `QPS ~= 32 + 4.9 x nodes + 4.65 x
  standing_instances`, which predicts 163.4 and 138.5
  against 163.1 and 138.9 measured on 2026-08-22 and
  2026-08-23.*
  *Three things changed about this criterion and all three
  are corrections rather than concessions. The earlier
  reading of "met at 89-92/s on 2026-08-05 to 2026-08-07"
  is **withdrawn**: before #3708 the counter could not see
  daemons co-located with MariaDB, and on those three days
  it could not see the cluster daemon at all, so only
  measurements taken after 2026-08-11 are comparable with
  each other or with this criterion. The window grew from
  ten to thirty minutes because ten minutes cannot separate
  a bursty operation from a floor. And "quiet" is now
  explicit, because the 24h mean that superseded it in
  practice folds in CI workload and therefore answers a
  different question -- which the per-instance term now
  answers properly. The number stays at 100.)*
* `get_node` no longer appears at all on the idle path, and
  `get_node_daemon_state` is at or under the arithmetic floor
  implied by `DAEMON_STATE_POLL_INTERVAL` and the cluster's
  daemon-process count -- not above it. *(Restated in phase 6
  per Open question 5; the original wording asked that
  `get_node_daemon_state` leave the top five by rate, which
  phase 1's own fix makes unreachable. Met as at 2026-08-18:
  `get_node` is absent from the idle path, and
  `get_node_daemon_state` measures ~0.5/s per daemon process,
  which is exactly one read per 2s interval. Re-confirmed
  2026-08-24 against the whole cluster: 20.29/s for seven
  polling daemon types across six nodes, less the one
  elected cluster daemon, which sleeps on its lock rather
  than in `idle()` and so never polls -- 41 processes at one
  read per 2s is 20.5/s.)*
* Daemon shutdown latency remains within the systemd stop
  timeout, verified by a rolling restart of a compute node.
* Mutable data (object states, attributes, metadata, daemon
  states) remains strictly read-through — no cache sits in
  front of it.
* Per-caller attribution is visible in the shakenfist
  Grafana dashboard and the counter label design is
  compatible with the planned mTLS peer-identity model.
* The code passes `pre-commit run --all-files` (flake8,
  stestr unit tests, and mypy type checking).
* There are unit tests for the new cache and poll-interval
  logic, and CI (which exercises full cluster lifecycle)
  passes.
* Documentation in `docs/` has been updated —
  `docs/operator_guide/` for any new config options, and
  `ARCHITECTURE.md` for the caching model.
* `docs/plans/index.md` and `docs/plans/order.yml` reflect
  this plan and its phases.

### Future work

* **Watch/subscribe change notification.** Deferred per
  Decision 5. If post-phase-5 numbers still justify it, the
  starting sketch is a batched unary "return object versions
  changed since X" RPC that many-object consumers poll on a
  coarse interval, avoiding the forbidden streaming pattern.
  Any streaming design requires reworking the sf-database
  servicer threading model first.
* **Server-side caching in sf-database.** If a future
  workload shows hot immutable reads arriving from many
  processes (where per-process client caches multiply), a
  server-side cache in the servicer is the next lever. Not
  needed for the current load shape.
* **Client-side load balancing across the database tier**
  remains the not-yet-landed part of
  `PLAN-byo-mariadb-phase-03-grpc-tier.md`; the per-caller
  attribution from phase 4 will show the current imbalance
  between the two sf-database instances (measured 2026-07-19:
  the split flips per operation, sf-1 serving 96/s of
  daemon-state reads vs sf-2's 53/s).

### Bugs fixed during this work

* **#3499** queue-worker idle polling, **#3500** transfers
  idle polling, **#3501** cluster IPAM re-reads, **#3502**
  cluster sweep attribute/reference re-reads -- the phase 5
  thread A reductions.
* **#3532** in-memory-only objects leaking `object_states`
  rows, **#3533** the deleted-object sweep unable to drain
  its backlog, **#3534** orphan/zombie row reconciliation --
  found while chasing #3501, and the actual cause of the
  `cluster` caller's `GetIPAM` floor.
* **#3595** the sidechannel dispatcher polling every
  agent-ready instance at 1 Hz (fixed by #3596). Found by
  the hunt that phase 4's attribution counter made possible;
  the single largest reducible line, ~0.8 QPS per standing
  instance, and it cut cluster load by roughly a third.
* **#3654** `Instance.external_view()` issuing ~7 separate
  full-row `GetInstanceAttributes` RPCs per instance API GET.

* **#3655** floating-IP maintenance sweeping every in-use
  address reservation three times per 30s cycle, and
  **#3814** `reap_expired_namespace_keys()` reading
  `key.state` once per key every 15 minutes -- both fixed in
  phase 6, together worth a measured ~21/s. Two caveats on
  what was said about #3814 when it was filed: the diagnosis
  was wrong (see the phase 6 plan's Findings), and it was
  not, as claimed, the larger half of the climb back to
  ~142/s -- most of that climb was the counter's coverage
  widening rather than new load, which 6g establishes. The
  defects and their cost are real regardless; they simply
  were not new.

Still open: **#3815** (`POST /auth` is ~37% of mutating API
requests because the client caches its token on the `Client`
object, so every process invocation re-authenticates). The
fix most likely belongs in `client-python`, not here: the
server already returns `expires_in`, so a cross-process
cache needs no server change. Also **#3876**, split out of
phase 6: `GetReferencesFrom`/api runs at 11.6x its paired
`GetReferencesTo`, which localises it to two unpaired read
sites -- `Blob.external_view()` fetching three reference
lists where one would do, and `Artifact.external_view()`
reading one per blob version. And **#3874**, found by 6g:
the elected cluster daemon sleeps on its lock rather than in
`idle()`, so it never calls `check_daemon_state()` and never
notices an `sf-ctl stop`. Immaterial to load -- 0.5/s -- but
a real shutdown-path gap.

### Documentation index maintenance

When this plan file lands, add it to `docs/plans/index.md`
(*Master plans* table, one row for the whole plan) and
`docs/plans/order.yml` (master plan only). Phase files are
linked from the Execution table above only.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
