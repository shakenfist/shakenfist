# Node resource health: declarative dependency checks feeding node state

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read the
relevant source: the node state machine and `ACTIVE_STATES`
(`shakenfist/node.py`), the scheduler's candidate filter
(`shakenfist/scheduler.py`), blob replica accounting
(`shakenfist/blob.py` `request_replication`, `locations`), the
resources daemon's metrics/stats loop
(`shakenfist/daemons/resources/main.py`), the cluster daemon's
dead-node handling (`shakenfist/daemons/cluster/main.py`), the
instance state machine (`shakenfist/instance.py`), and the object
type enum (`shakenfist/schema/object_types.py`). Ground every
answer in what the code does today; do not speculate where you can
read instead. Where a question touches external concepts (NFS hard
vs soft mount failure semantics, `virDomainGetDiskErrors`, sysfs
block/NVMe state), research as needed and flag uncertainty
explicitly.

All planning documents go in `docs/plans/`. Prefer a separate plan
file per detailed phase, named `PLAN-node-resource-health-phase-NN-descriptive`.
One commit per logical change, at minimum one per phase; each commit
self-contained. Consult `ARCHITECTURE.md` and `CLAUDE.md` for the
daemon inventory, build commands, and conventions.

**Status: complete.** This master plan captures the model and the
decisions reached in design discussion (recorded below). All four
implementation phases have landed on the `node-resource-health` branch;
the decisions (D1–D8) were ratified against the code as each phase went
in, so no separate phase-0 document was written. The narrow instance-
level half of this story landed earlier on the `instance-disk-errors`
branch (see Prior work) and is the first realised piece of the model.

## Situation

On 2026-07-19 the sfcbr hypervisor node sf-6 lost its dedicated
blob-storage NVMe: the controller hung, the kernel disabled the
device, and the XFS filesystem at `/srv/shakenfist/blobs` shut down.
Every access to that path returned EIO. For roughly six hours:

- The scheduler kept placing new instances on sf-6 — twenty of them
  during the outage — because nothing it consults touches the blob
  store. `find_candidates` (`scheduler.py`) filters on hypervisor
  capability, queue depth, CPU, RAM and root-filesystem free space;
  the dead blob disk lowered none of those.
- Instances already on sf-6 kept running with broken backing storage.
  qemu's default disk error policy passes EIO to the guest and leaves
  the domain RUNNING, so the cleaner's power-state poller saw only
  "on". At least one guest logged hundreds of vda / EXT4 errors
  internally while the control plane and billing recorded it as
  healthy.
- The failure only surfaced when a routine `sfcbr.yml` deploy tripped
  over the crash-looping sf-queues daemon hours later.

The root cause was a single missing idea: **nothing in Shaken Fist
models "a filesystem this node depends on has failed."** Node health,
today, is only "has the node checked in recently" (heartbeat →
`STATE_MISSING`) and "are this node's own daemons self-reporting
running" (`STATE_DEGRADED`). Neither notices a dead storage path,
and `STATE_DEGRADED` is itself still a schedulable state
(`node.py` `ACTIVE_STATES` includes it).

### Relationship to the completed health-checks plan

`PLAN-health-checks.md` is **complete** (five phases, landed on the
`health-checks` branch). It is deliberately about a **different
axis**: daemon *liveness / readiness / drain* for an operator's load
balancer and systemd — `/livez` and `/readyz` on sf-api, dependency-
aware `grpc.health.v1` on sf-database, systemd `WATCHDOG` on the
worker daemons. Its guiding principle is "health is a real-time
probe, not a state read," and its dependency model is intentionally
shallow (sf-api → sf-database, cached) and daemon-to-daemon.

This plan is a **sibling**, not an extension:

| | Health-checks plan (done) | This plan |
|---|---|---|
| Question | Is this daemon process alive / ready to serve? | Are the resources a node depends on healthy? |
| Sink | Real-time HTTP/gRPC probe | `node.state` (eventual cluster state) |
| Consumer | Operator load balancer, systemd | Scheduler, blob replicator, operator |
| Graph | daemon → daemon | object type → resource checks |

They intersect at one clean seam: the daemon-liveness signals the
health-checks plan already produces (systemd `WATCHDOG`,
`node_daemon_states`, the future dnsmasq-serving probe of issue #730)
are natural *inputs* to this model's non-path checks — this plan
consumes them, it does not rebuild them.

### Prior work already landed (`instance-disk-errors` branch)

The instance-level, reactive half of the sf-6 response is done and is
the first realised slice of this model:

- `error_policy='stop'` / `rerror_policy='stop'` on libvirt-managed
  disks (`libvirt.tmpl`) and the qemu-commandline equivalent
  `werror=stop,rerror=stop` on NVME-bus disks
  (`instance.py` `_initialize_block_devices`), so a backing-store I/O
  error *pauses* the domain instead of being swallowed.
- `util/libvirt.py` reads the pause reason code and
  `virDomainGetDiskErrors`; the cleaner's power-state poller marks an
  I/O-error-paused instance `<state>-error` (terminal, still
  snapshottable — not auto-deleted).
- Cleanup: removed the orphaned `deploy/templates/` directory.

That path detects a *running* instance whose disk errors. It does
**not** cover a node whose storage has failed but whose instances
have not yet touched the dead path, and — critically — it does not
fire at all when the backing store is a **hard NFS mount that hangs**
rather than returning an error (see Decision D5). That gap is what
this plan closes at the node level.

## Mission and problem statement

Give each object type a declarative statement of the local resources
it depends on to be healthy, let sf-resources evaluate those
dependencies for the object types a node actually hosts, and drive
`node.state` from the result — so that a failed dependency takes the
node out of scheduling, stops its blob replicas from counting, and is
explained to the operator in plain language.

The target diagnostic is: **"node sf-6 is unhealthy because it is a
hypervisor node and instance storage is unhealthy: `/srv/shakenfist/
instances` is a broken NFS mount (probe timed out)."**

### The model

1. **Declarative, per-object-type dependency lists.** Each
   `DatabaseBackedObject` subclass that has local-resource
   dependencies declares them as a static class attribute, alongside
   the `state_targets` / `object_type` / version attributes it already
   carries. Initially these are **path** checks:
   - `Instance` → `blobs`, `image_cache`, `instances`
   - `Blob` → `blobs`
   - `Upload` → `uploads`
   (paths under `STORAGE_PATH`; see Decision D2 on configurability).

2. **Checks are an abstraction, not hard-coded to paths.** A check is
   a small object with a stable identity (for de-duplication) and a
   `check() -> (healthy, detail)` method. A **path check** is the
   first and only implementation built now. The abstraction must admit
   other kinds later — "libvirtd is answering", "the mesh has
   connectivity" — **without enumerating them now**; we add each check
   type when we first need it (Decision D3).

3. **Node health = the checks of the object types it hosts.**
   sf-resources determines which object types this node hosts from the
   existing capability flags — `NODE_IS_HYPERVISOR`,
   `NODE_IS_NETWORK_NODE`, `NODE_IS_DATABASE_NODE` (`config.py`) —
   collects the union of their checks, **de-duplicates** (the `blobs`
   path appears for both Instance and Blob but is probed once), runs
   each once, and composes the result into `node.state` plus a
   human-readable reason. A database-only node hosts no Instance/Blob
   and so never probes those paths — role-awareness falls out for
   free.

4. **Blast radius is dependency membership, not a separate flag.**
   Earlier design carried an explicit `implies_instance_death` boolean
   per path. The dependency model **subsumes it**: instances are
   marked unhealthy when a check that `Instance` depends on fails.
   `uploads` failing does not touch instances because `uploads` is not
   in `Instance`'s dependency list — it is only in `Upload`'s. This is
   the main reason to prefer the declarative model over the flag.

### Type-level, not per-instance (a firm scope boundary)

"Instance depends on Blob" is read at the **type** level — "the blob
*subsystem* on this node is healthy" — evaluated per node against
local resources. It is **not** the per-object-instance question "does
*this* instance's *these* specific backing blobs read correctly."
Per-instance health graphs are a much larger and more expensive thing
and are explicitly **out of scope**. Node health needs only the type
level.

## Decisions already reached (design discussion)

These bind the implementation unless phase 0 overturns them against
the code.

- **D1 — Node error stops scheduling and discounts replicas, for
  free.** `node.py` `ACTIVE_STATES = {initial, created, degraded}`
  excludes `error`, and the scheduler queries `prefilter='active'`, so
  a node in `error` is not a scheduling candidate. `blob.py`
  `request_replication` already drops any location whose node
  `state != STATE_CREATED`, so an errored node's blob copies stop
  counting as replicas and the replicator rebuilds to
  `BLOB_REPLICATION_FACTOR` on its own. The sink for this model is
  therefore simply `node.state = error`.

- **D2 — Per-path config, uniquified.** The dependency lists resolve
  against configuration, defaulting to the standard `STORAGE_PATH`
  subdirectories. All checked paths across all hosted types are
  collected and de-duplicated so each real path is probed once per
  cycle.

- **D3 — Extensible check abstraction, path check only for now.** The
  check interface admits non-path checks (libvirtd liveness, mesh
  connectivity, …) but we build only the path check now and add others
  as we encounter the need. The plan does **not** attempt to enumerate
  the full check taxonomy.

- **D4 — Two-tier probing.** A cheap `statvfs`-based check every
  resources cycle (its raising `OSError`/EIO means the store is gone;
  `f_flag & ST_RDONLY` means remounted read-only — both are already
  paid for, since the daemon calls `statvfs` on these paths today),
  plus an authoritative `_heartbeat` write+fsync every **300 s** that
  catches write-only failures a read cannot, and doubles as a forensic
  "last seen live" timestamp. `os.makedirs(exist_ok=True)` on an
  existing directory does **not** prove writability, so the write
  probe is necessary, not redundant.

- **D5 — Probes must be timeout-guarded (the NFS finding).** sfcbr
  mounts NFS `hard` (`sfcbr.yml`: `opts: rw,sync,hard`). A hard NFS
  mount **hangs indefinitely** on server death rather than returning
  EIO, so any `statvfs`/write on it blocks forever. The probe must run
  under a deadline off the daemon's main thread; **a probe that times
  out is itself the unhealthy signal.** This also means the
  instance-level `werror=stop` pause mechanism (prior work) does *not*
  fire for a hung hard-NFS mount — the I/O blocks rather than
  erroring, so no `PAUSED_IOERROR` — making this node-level probe the
  **primary** detector for NFS-backed storage, not a backup.

- **D6 — Node error never auto-recovers.** `node.py` `state_targets`
  makes `error → created` a valid transition (node error is
  non-terminal — node is effectively the only object whose error state
  is recoverable), but recovery is **operator-only**. sf-resources
  does not clear it automatically; flapping a node between error and
  created on a marginal disk is worse than staying errored until a
  human looks.

- **D7 — The slow cascade runs on a surviving node.** Marking the node
  error is a single fast local write sf-resources does on the affected
  (possibly dying) node. The heavier reactions — dropping the node's
  blob location records and re-replicating, and moving its instances
  to `<state>-error` — are done by the **cluster daemon**, which runs
  on another node and already has the pattern for deleted nodes
  (`cluster/main.py`, the deleted-node blob/instance cleanup). This
  survives the affected node going fully offline. The per-type death
  implication (D4/blast-radius) propagates via a node attribute set
  alongside the error state, so the cluster daemon knows whether to
  cascade to instances.

- **D8 — No transitive dependency tree yet.** Each type lists its
  resource checks **directly** (Instance lists `blobs` itself rather
  than "depends on Blob, which owns `blobs`"). The transitive type →
  type graph is deferred to Future work; with a handful of types and
  1–3 paths each, the duplication is trivial and de-duplication (D2)
  erases its runtime cost, whereas the graph adds cycle-and-ordering
  complexity for diagnostic elegance we do not yet need.

## Open questions (for phase 0)

1. **Where does the dependency declaration live?** A static attribute
   on each `DatabaseBackedObject` subclass (idiomatic, next to
   `state_targets`) versus a central registry mapping object type →
   checks (keeps health concerns out of the domain model). Lean:
   static attribute for path checks the type genuinely "owns"; central
   registry for node-capability checks (libvirtd, mesh) that are not
   naturally any single object's property. Phase 0 decides the split.

2. **How is per-node "object type health" surfaced and stored?**
   `node.state = error` + a reason string is the minimum. Do we also
   persist per-check / per-type health somewhere queryable (events,
   a node attribute, a metrics gauge), or is the composed reason
   enough for now? Avoid over-building persistence.

3. **Reason-string vs structured detail.** The 2am diagnostic wants
   prose, but the API/metrics may want structure. Phase 0 fixes the
   shape of the health result (which check, which type, which node
   capability, the detail) and how it renders to the operator.

4. **Probe timeout value and the leaked-thread bound.** A hung hard-
   NFS probe leaves a worker thread blocked in the kernel until the
   mount recovers. Phase 0 sets the per-probe deadline and the policy
   for not launching a second probe of a path whose previous probe is
   still outstanding (bounding leaked threads to one per path).

5. **Interaction with `STATE_DEGRADED`.** A node with a dead daemon is
   already `STATE_DEGRADED` (still schedulable). A node with dead
   storage should be `STATE_ERROR` (not schedulable). Phase 0 confirms
   the precedence and transitions between `degraded`, `error`, and
   `missing` so the three health inputs (daemon self-report, resource
   checks, heartbeat) compose without fighting.

6. **Config surface.** Exact knobs: the write-probe interval (default
   300 s per D4), the per-probe timeout (Q4), and whether the
   dependency paths are operator-overridable or fixed
   `STORAGE_PATH`-relative subdirectories.

7. **Instance cascade mechanics.** Mirror the deleted-node path
   (`cluster/main.py`) to move a hosted instance not already in an
   error state to `<state>-error`. Confirm this is a pure DB state
   change the cluster daemon can perform for an instance on another
   node (the libvirt teardown waits for node recovery / operator),
   matching how deleted-node instances are handled.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 0. Research and decisions | (folded into phases 1–4; decisions ratified inline against the code) | Complete |
| 1. Check abstraction + path check + tests | PLAN-node-resource-health-phase-01-checks.md | Complete |
| 2. sf-resources evaluator: hosted-type checks → `node.state` | PLAN-node-resource-health-phase-02-evaluator.md | Complete |
| 3. Cluster-daemon cascade (blob locations + instances) | PLAN-node-resource-health-phase-03-cascade.md | Complete |
| 4. Operator docs + observability | PLAN-node-resource-health-phase-04-docs.md | Complete |

**All phases complete** on the `node-resource-health` branch. Phase 0
was not written as a separate document — its decisions (D1–D8) are
recorded above and were ratified against the code as each phase landed.

Sequencing notes:

- **Phase 1 is the reusable primitive**: the `HealthCheck` interface
  and the path check (statvfs cheap tier + timeout-guarded write
  heartbeat, D4/D5), fully unit-tested against mocked `statvfs`/write
  failures and a simulated hang, with **no** wiring into node state
  yet. It must stand alone so later check types slot in beside it.
- **Phase 2 delivers the headline value**: sf-resources walks the
  object types this node hosts (from `NODE_IS_*`), de-duplicates and
  runs their checks, and sets `node.state = error` with the composed
  reason — and stops swallowing the blob-path EIO it currently hides.
  On its own this already stops scheduling onto a dead-storage node
  and discounts its replicas (D1), so it is shippable without phase 3.
- **Phase 3 is the cascade**: the cluster daemon reacts to an errored
  node by dropping blob locations + re-replicating and erroring the
  hosted instances per the propagated death implication (D7),
  mirroring the deleted-node path.
- **Phase 4** documents the model and the operator recovery procedure
  (D6: node error is operator-cleared) and adds any metrics/events
  from Q2.

## Dependencies on other plans

- **`PLAN-health-checks.md` (complete):** sibling, not a dependency.
  This plan may consume its daemon-liveness signals as future non-path
  checks (D3) — e.g. issue #730's dnsmasq-serving probe becomes a
  network-node check — but needs nothing from it to land phases 0–3.
- **`instance-disk-errors` branch (landed):** the instance-level
  reactive detection this plan complements at the node level. No code
  dependency; the two compose (per-instance pause detection for
  error-returning stores, node-level probe for hung/silent stores).
- **`PLAN-recurring-operations.md`:** the cluster-daemon cascade
  (phase 3) adds to the same maintenance loop that plan aims to
  restructure; coordinate so the new cascade is a clean recurring
  operation rather than more accretion in `_cluster_wide_cleanup`.

## Future work

- **Transitive type → type dependency graph (D8).** Let a type declare
  dependence on *another type's* health ("Instance depends on Blob")
  instead of re-listing shared paths, so a shared expensive check is
  declared once and the node-level rollup composes the DAG. Deferred
  until the type/check set is large enough that direct listing hurts,
  or a genuinely shared non-path check appears. Requires cycle
  detection and evaluation ordering.
- **More check types (D3).** libvirtd answering (hypervisor), mesh
  connectivity (all nodes), MariaDB reachability (database node), the
  dnsmasq-serving probe (network node, issue #730). Add each when
  first needed; each is a new `HealthCheck` implementation.
- **Per-instance backing-store health.** The instance-level rather
  than type-level question — does a specific instance's specific
  backing chain read correctly. Much larger; explicitly out of scope
  here.
- **Automatic node-error recovery (revisit D6).** If operators find
  operator-only recovery too manual, a hysteresis-guarded auto-clear
  (N consecutive healthy probes → `error → created`) could be added —
  but only with anti-flap protection, as its own decision.
- **Functional (cluster_ci) coverage.** The unit suite exercises the
  probe, evaluator, cascade and `clear-node-error` directly, but there
  is no `cluster_ci` test that forces a node's storage read-only, waits
  for `STATE_ERROR`, runs `sf-ctl clear-node-error`, or scrapes the
  `node_resource_health` gauge. The harness already supports both
  (`_node_exec`, and the metrics-scrape pattern in
  `test_database_tier.py`), so this is achievable, but deliberately
  deferred: forcing a real storage fault on a shared CI cluster is
  invasive (the same reason the sf-api drain test in `test_health.py`
  is deferred). Add it when a dedicated/disposable CI node is available.

## Success criteria

- A node whose declared storage dependency fails (dead disk, read-only
  remount, or **hung hard-NFS mount**) transitions to `STATE_ERROR`
  within one write-probe interval, with a reason string naming the
  node capability, the object type, the failed check and its detail.
- That node stops receiving new instances (D1) and its blob copies
  stop counting toward replica targets, so the replicator rebuilds
  them elsewhere (D1) — verified without any explicit location drop.
- The cluster daemon, from a surviving node, drops the errored node's
  blob location records and re-replicates, and moves its hosted
  instances to `<state>-error` only when a check the `Instance` type
  depends on failed (an `uploads`-only failure does not touch
  instances).
- A hung hard-NFS mount does not hang the resources daemon (the probe
  is deadline-bounded, D5).
- Node error is not auto-cleared (D6); an operator procedure to clear
  it is documented.
- Adding a new check type (e.g. libvirtd liveness) requires only a new
  `HealthCheck` implementation and a one-line addition to a type's
  dependency list — no change to the evaluator.
- `pre-commit run --all-files` passes.

### Completion note (criteria review)

All criteria are met, with one **partial**: the "adding a new check
type" criterion. The `HealthCheck` abstraction (phase 1) and the
`evaluate()` composer (phase 2) are already check-type-agnostic, so a
new check type needs no evaluator change. But the *declaration → check*
wiring built now (`node_health.build_checks`) reads each type's
`health_dependencies` as **path** subdirectories and constructs
`PathCheck`s; adding a non-path check (e.g. libvirtd liveness) will
require a small generalisation of that wiring (a richer dependency
descriptor than a bare path string), not merely a one-line list
addition. This is consistent with D3/Future work, which deferred
non-path checks until first needed; the seam is in the right place and
the generalisation is local to `build_checks`.

## Back brief

Before executing any phase, back-brief the operator on your
understanding of the model — especially the type-level (not
per-instance) scope boundary, the sibling relationship to the
completed health-checks plan, and the D7 split (fast local
node-error mark vs slow surviving-node cascade).

## Documentation index maintenance

This master plan is registered in `docs/plans/index.md` (*Master
plans* table + sequencing note) and `docs/plans/order.yml`. Keep
both in sync as phases land.
