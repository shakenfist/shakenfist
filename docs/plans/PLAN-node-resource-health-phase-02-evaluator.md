# Phase 2: the sf-resources evaluator that drives node state

Master plan: [PLAN-node-resource-health.md](PLAN-node-resource-health.md).
Depends on phase 1 (`shakenfist/resource_health.py`: `HealthCheck`,
`PathCheck`, `DeadlineProbe`).

## Context

Phase 2 turns the standalone check primitive from phase 1 into a live
signal. It:

1. Lets object types **declare the storage subdirectories they depend
   on** (the declarative model — master plan Q1).
2. Adds a **node health evaluator** that, given this node's
   capabilities, collects the checks of the object types it hosts,
   de-duplicates them, runs each, and composes a result naming the
   failed checks and the affected object types.
3. Runs the evaluator in sf-resources and, on failure, sets
   **`node.state = STATE_ERROR`** with a human reason and a structured
   attribute — which, by existing mechanics, immediately stops
   scheduling onto the node and stops its blob copies counting as
   replicas (master plan D1).

This is the phase that delivers the headline value: after it, the sf-6
class of failure (a dead blob NVMe, a hung NFS mount) takes the node
out of service on its own. It is shippable **without** phase 3 — the
cluster-daemon cascade (dropping blob location records, erroring the
hosted instances) is a separate, additive phase; the structured
attribute this phase writes is what phase 3 will consume.

## Key references in the existing code

- **`shakenfist/daemons/resources/main.py:556`** — `n =
  Node.from_db(config.NODE_NAME)` at daemon startup; **`:615-631`** —
  the main loop (`while daemon.check_abort_path(...)`,
  `wait_for_nodelock()`, timed `update_metrics()` / billing, the broad
  `except Exception: ignore_exception('resource statistics', e)` at
  `:628`, `self.idle(1)`). The evaluator must **not** run inside this
  try (see Design: it runs in its own thread so a bounded per-probe
  block cannot stall metrics or hold the nodelock).
- **`shakenfist/daemons/resources/main.py:247-252`** — the existing
  per-path `os.statvfs` loop (`['', 'blobs', 'events', 'image_cache',
  'instances', 'uploads']`) whose EIO is swallowed at `:628`. This
  phase does not change the metrics loop; the evaluator is what now
  surfaces a dead path (as node error), so the EIO is no longer hidden.
- **`shakenfist/node.py:63-89`** — node `state_targets`. Confirmed
  valid transitions: `created → error`, `degraded → error`,
  `missing → error`, and `error → created` (operator recovery). Error
  is **not** in `ACTIVE_STATES` (`:45`), so it stops scheduling.
- **`shakenfist/baseobject.py:600-612`** — the `error` property. Its
  setter **requires the object already be in an error state** (`if not
  s.value.endswith('error')` raises), so set `node.state = error`
  **before** `node.error = reason` (the same ordering the
  instance-disk-errors branch learned). `add_event` at `:338`;
  `_db_set_attribute` / `_db_get_attribute` at `:459` for the
  structured record.
- **`shakenfist/config.py:554-570`** — `NODE_IS_HYPERVISOR`,
  `NODE_IS_NETWORK_NODE`, `NODE_IS_DATABASE_NODE` (`Field(False, ...)`).
  The capability→object-type mapping keys off these.
- **`shakenfist/external_api/upload.py:41`** — `Upload.new(...,
  config.NODE_NAME)`: uploads land on whichever node ran sf-api for the
  request. There is **no** `NODE_IS_API` flag (see Decision E3 on how
  Upload is handled).
- **`shakenfist/node.py` `nodes_by_free_disk_descending(...,
  intention='blobs')`** — blob placement is disk-based across all
  `active` nodes, **not** capability-gated: every node is a potential
  blob store (Decision E3).
- **eventlog drainer thread** (`shakenfist/eventlog_drainer.py`
  `start()` — a lock-guarded module thread singleton) — the precedent
  for the background thread this phase starts.

## Inherited decisions (master plan)

D1 (node error stops scheduling + discounts replicas, for free), D4
(two-tier probe: cheap statvfs every cycle, write every 300 s), D5
(deadline-guarded; a hung hard-NFS mount is the reason), D6 (node error
never auto-recovers — operator only), D7 (the slow cascade runs on a
surviving node in phase 3; phase 2 records what it needs), D8 (no
transitive dependency graph).

## Design

### E1 — where dependencies are declared (resolves Q1)

Each object type that depends on local storage gets a **class
attribute** listing the `STORAGE_PATH`-relative subdirectories it
needs, next to `state_targets`:

```python
class Instance(...):
    health_dependencies = ['instances', 'image_cache', 'blobs']
class Blob(...):
    health_dependencies = ['blobs']
class Upload(...):
    health_dependencies = ['uploads']
```

They are **path names only** — a plain list of strings, so the object
classes gain no import of the health machinery. The evaluator resolves
them against `STORAGE_PATH` and the check config. (An `Instance` needs
its own COW disk dir `instances`, the `image_cache` it boots from, and
the `blobs` store its backing image lives in — the sf-6 failure was
exactly a dead `blobs` breaking running instances.)

### E2 — the evaluator (`shakenfist/node_health.py`, new)

A new module — **not** in `resource_health.py`, which phase 1 kept free
of domain imports. `node_health.py` imports `resource_health`, `config`,
the `ObjectType` enum, and the object classes (only for their
`health_dependencies` and the capability map; those classes do not
import `node_health`, so no cycle). It exposes:

- `node_object_types() -> list[type]` — the object types this node
  hosts, from the capability flags (Decision E3).
- `build_checks(types, *, storage_path, write_interval, timeout)` —
  returns `(checks, types_by_identity)`: one `PathCheck` per **unique**
  resolved path (de-duplicated by `identity`), and a reverse map from
  check identity to the set of `ObjectType`s that depend on it (so a
  failed path maps back to the affected types).
- `evaluate(checks, types_by_identity) -> NodeHealthResult` — runs each
  check once; `NodeHealthResult` carries `healthy: bool`, the list of
  failed `HealthResult`s, `affected_types: set[ObjectType]` (union over
  failed checks), and a composed `reason` string.

The reason reads like the target diagnostic, e.g. *"resource health
check failed: instance, blob depend on /srv/shakenfist/blobs (timeout:
probe did not return within 30s …)"*.

### E3 — the capability → object-type mapping

```python
def node_object_types():
    # Every active node is a potential blob replica store
    # (nodes_by_free_disk_descending is disk-based, not capability-gated)
    # and any node may run sf-api and receive an upload.
    types = [Blob, Upload]
    if config.NODE_IS_HYPERVISOR:
        types.append(Instance)
    return types
```

Consequences, all correct: a hypervisor checks `blobs`, `image_cache`,
`instances` (Instance) + `blobs` (Blob, de-duplicated) + `uploads`
(Upload). A pure database/network node checks `blobs` + `uploads`
only. Because affected-types comes from the reverse map, an
`uploads`-only failure marks **Upload** affected but not **Instance**
— so phase 3 will not cascade it to instance death, while a `blobs`,
`image_cache`, or `instances` failure does mark **Instance** affected.
This is the master plan's blast-radius-as-membership (D-note) in
action; the old `implies_instance_death` flag is gone.

**Flagged for review:** `Upload` is mapped to *every* node because
there is no capability for "runs sf-api". This is a conservative
over-check (a node that never serves uploads still probes the
`uploads` dir), harmless because it does not cascade to instances and
the dir shares the node's storage anyway. Refining it to a real
sf-api signal is future work, tied to the sf-api-tiering in
`PLAN-remove-primary.md`.

### E4 — applying the result in sf-resources (resolves Q5)

The evaluator runs in a **dedicated background thread** the resources
daemon starts (precedent: the eventlog drainer thread), **not** in the
metrics/billing main loop. Rationale: `PathCheck.check()` blocks up to
`timeout` (30 s) the *first* time a path hangs (the outstanding-probe
guard makes every subsequent call instant), and the main loop both
holds the nodelock and drives metrics — a 30 s stall there could time
out other nodelock waiters and delay metrics. The health thread holds
no nodelock and its cadence is independent.

The thread loops every `NODE_HEALTH_CHECK_INTERVAL` (default 60 s;
`PathCheck`'s own `write_interval` gates the writes to 300 s), and:

- If the result is **unhealthy** and `node.state != error`:
  `add_event(EVENT_TYPE_AUDIT, reason, extra={affected_types,
  failed})`, then set `node.state = STATE_ERROR`. **Implementation
  note:** node attributes are a fixed typed schema
  (`schema/node_attributes.py` `NodeAttributesData`) with no free-form
  field, and `node.error` is not persisted for nodes (the base
  `_db_set_attribute` warns "subclass should override" and `Node` does
  not, storing typed columns instead). Rather than add a schema
  migration for a `resource_health` column in this lightweight phase,
  the durable record is the **audit event** — which phase 3 consumes
  for the affected object types. `node.state = STATE_ERROR` is the
  load-bearing action (it alone stops scheduling and discounts blob
  replicas); the reason and structure live in the event.
- If unhealthy and the node is **already** `error`: do nothing (no
  re-set, no event spam; the transition already recorded the
  diagnosis).
- If **healthy**: do nothing. It does **not** clear `error` (D6:
  operator-only) and does **not** touch `created`/`degraded`, which the
  daemon-state and heartbeat logic own.

Q5 composition holds because the evaluator only ever transitions
*toward* `error`, which is the most restrictive state (excluded from
`ACTIVE_STATES`), and nothing auto-downgrades it: the cluster daemon's
missing-logic (`cluster/main.py`) only touches nodes in
`[INITIAL, CREATING, CREATED, DEGRADED]`, so an `error` node is left
alone. `created → error` and `degraded → error` are both valid
(`node.py:63-89`), so the transition never raises regardless of the
prior daemon-state-driven state.

### E5 — config knobs (resolves Q6)

Three `Field`s in `config.py`, near `STORAGE_PATH`:

- `NODE_HEALTH_CHECK_INTERVAL` (int, default 60) — how often the health
  thread evaluates. Bounds detection latency for a fully-dead path
  (the cheap statvfs runs every evaluation).
- `NODE_HEALTH_WRITE_INTERVAL` (int, default 300) — passed to each
  `PathCheck`; how often the authoritative write probe runs (D4).
- `NODE_HEALTH_PROBE_TIMEOUT` (int, default 30) — the `DeadlineProbe`
  deadline (D5/Q4). Provisional value from phase 1 now gets its home.

## Step-level guidance

Sequential; review and commit each. Isolation `none`.

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 2a — declarations + config | low | sonnet | none | Add the `health_dependencies` class attribute to `Instance` (`['instances', 'image_cache', 'blobs']`), `Blob` (`['blobs']`), and `Upload` (`['uploads']`) — a plain list of `STORAGE_PATH`-relative subdir names, placed near each class's `state_targets`/`object_type`; no new imports on those classes. Add `NODE_HEALTH_CHECK_INTERVAL=60`, `NODE_HEALTH_WRITE_INTERVAL=300`, `NODE_HEALTH_PROBE_TIMEOUT=30` as `Field(...)` in `config.py` near `STORAGE_PATH` (`:607`), each with a description. A tiny test asserting the three attributes are present and are lists of str. Commit subject: `object types: declare storage health dependencies.` |
| 2b — the evaluator | high | opus | none | Create `shakenfist/node_health.py`: `node_object_types()` per E3; `build_checks(types, *, storage_path, write_interval, timeout)` returning `(list_of_unique_PathChecks, {identity: set(ObjectType)})` (de-dup by `PathCheck.identity`, resolve each subdir against `storage_path`); `NodeHealthResult` (a frozen dataclass: `healthy`, `failed: list[HealthResult]`, `affected_types: set`, `reason: str`); and `evaluate(checks, types_by_identity)` that runs each check once, collects failures, unions affected types, and composes the reason. Fully unit-tested and hermetic (construct checks over a `TemporaryDirectory`, and use fakes / a stub check for failure and dedup cases): dedup (Instance+Blob both wanting `blobs` → one check, identity mapped to both types); an `instances` failure → `affected_types` includes INSTANCE; an `uploads`-only failure → affected is {UPLOAD}, **not** INSTANCE; healthy → `NodeHealthResult.healthy` True and empty failed/affected; the reason names the failed path(s) and status(es). Add to the mypy rollout (fully typed). Commit subject: `node_health: capability-aware health evaluator.` |
| 2c — sf-resources integration | high | opus | none | Add `apply_result(node, result)` to `node_health` (record an `EVENT_TYPE_AUDIT` event with `extra={affected_types, failed}` then set `node.state = STATE_ERROR`, only on unhealthy-and-not-already-error; never clear error; never touch created/degraded). In the resources daemon, build the evaluator's checks **once** at startup (`build_for_this_node()`), and start a **daemon background thread** (guarded with the daemon's abort path, sleeping in 1 s slices) that every `NODE_HEALTH_CHECK_INTERVAL` s calls `evaluate(...)` then `apply_result(...)`. Do not run this in the metrics loop and do not hold the nodelock. Unit tests (a fake node): unhealthy from `created` → node goes to `error` with one audit event carrying the reason and `affected_types`; from `degraded` → also `error`; a second cycle while already `error` → no event, unchanged; healthy → state untouched, error not cleared. Commit subject: `resources: evaluate node resource health and mark node errored.` |

## Step ordering and dependencies

- **2a first** (declarations + config); **2b** builds checks from 2a's
  attributes and config; **2c** wires 2b into the daemon.
- No deploy or proto changes. `pre-commit run --all-files` after each
  (note: the mypy hook runs the whole rollout list and takes a few
  minutes).

## Success criteria

- On a hypervisor whose `blobs` (or `image_cache` or `instances`) path
  fails — dead disk (`statvfs` EIO → `missing`), read-only remount
  (`readonly`), or hung hard-NFS mount (`timeout`) — the node
  transitions to `STATE_ERROR` within `NODE_HEALTH_CHECK_INTERVAL`,
  with one audit event whose message names the failed path and status
  and whose `extra` records the failed checks and affected types
  (INSTANCE among them).
- That node stops receiving new instances and its blob copies stop
  counting as replicas — **with no scheduler or replicator change**,
  purely from being in `error` (verify against `ACTIVE_STATES` and
  `request_replication`'s state filter).
- An `uploads`-only failure marks the node `error` (stops scheduling)
  but records affected types **{UPLOAD}** — not INSTANCE — so phase 3
  will not cascade it to instances.
- The health check runs off the metrics loop and holds no nodelock; a
  first-hang 30 s probe block does not stall metrics or billing.
- Node error is not auto-cleared when the path recovers (D6).
- `pre-commit run --all-files` passes; new modules are type-hinted and
  in the mypy rollout.

## Back brief

Confirm the understanding that phase 2 only ever moves a node **into**
`error` (never out — D6), that the evaluator runs in its own thread
(not the metrics loop) to avoid the nodelock/first-hang-block hazard,
and that the audit event written here (message + `extra` with
`affected_types`) is the interface phase 3 consumes. Flag the
Upload-on-every-node conservative mapping (E3) for the operator to
confirm.

## Review checklist for the management session

- [ ] `health_dependencies` are plain string lists on the classes; the
      classes gained no health-machinery import.
- [ ] `build_checks` de-duplicates by identity and the reverse map
      attributes a failed path to *all* depending types.
- [ ] `evaluate` never raises for an unhealthy resource (it reads
      `HealthResult`s, which `PathCheck` returns rather than raising).
- [ ] The daemon records the audit event and moves the node to `error`
      only on the not-already-error transition (no event spam).
- [ ] Healthy results never clear `error` and never touch
      `created`/`degraded`.
- [ ] The evaluator runs in its own abort-aware thread, not the metrics
      loop, and takes no nodelock.
- [ ] `affected_types` is recorded in the audit event for phase 3.
- [ ] `pre-commit run --all-files` passes; mypy rollout updated.
