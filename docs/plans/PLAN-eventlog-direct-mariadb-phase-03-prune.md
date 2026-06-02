# Phase 3 — prune sweep in the cluster daemon

Parent plan:
[PLAN-eventlog-direct-mariadb.md](PLAN-eventlog-direct-mariadb.md).
Predecessors:
[Phase 1](PLAN-eventlog-direct-mariadb-phase-01-schema.md),
[Phase 2](PLAN-eventlog-direct-mariadb-phase-02-write.md).

## Scope

Phase 3 moves the per-event-type prune sweep from
sf-eventlog into the cluster daemon's existing scheduled-
task loop, running daily. After this phase ships, the
cluster maintainer (one node at a time, behind the
`cluster` ClusterLock) ages rows out of the new `events` /
`event_objects` tables according to the existing eight
`MAX_{TYPE}_EVENT_AGE` configs. sf-eventlog's own per-
sqlite prune loop still runs against the (now stale)
sqlite chunks and is harmless; it's deleted in phase 5.

The phase covers:

- Three new `_direct_prune_*` functions on sf-database
  for the per-event-type prune, the api-request object-
  type override, and the orphan-events sweep.
- Two new Prometheus counters:
  `database_events_pruned_total{event_type=...}` for the
  per-type and api-request sweeps, and
  `database_orphan_events_pruned_total` for the post-
  sweep orphan cleanup. Both at module scope in
  `mariadb.py`, picked up automatically on sf-database's
  existing metrics endpoint via the shared default
  registry.
- One new sf-database RPC: `PruneEvents`. Single RPC
  rather than three because the orchestration (seven
  event_types + api-request + orphan sweep, each
  internally batched) is simpler inside the direct
  function than it is over the wire, and the prune is a
  once-a-day call where "long RPC" is acceptable.
- A new scheduled task `scheduled_tasks.prune_events`
  running `schedule.every(1).days.do(...)` from the
  cluster daemon's main loop, gated behind the `cluster`
  election lock that the maintainer already holds.
- Multi-object retention semantics preserved exactly per
  the master plan's decision 4: an `events` row is
  deleted only once its last `event_objects` row has
  been pruned. An event touching N objects survives
  until all N objects' retention windows have dropped
  it.

Out of scope (deferred):

- The read RPC and REST cut-over (phase 4).
- Deleting sf-eventlog and the DLQ (phase 5).
- Docs (phase 6).
- sf-eventlog's own per-sqlite prune loop. It keeps
  running against the now-stale sqlite chunks until
  phase 5 deletes it; harmless.

## Prune semantics

Three logical stages per daily sweep, ordered as below
because stage A and B both target `event_objects` rows
(order doesn't matter between them) but stage C runs
after both to clean up events that lost their last
reference.

**Stage A — per event_type prune.** For each of the seven
event_type configs:

```
MAX_AUDIT_EVENT_AGE      90 d
MAX_MUTATE_EVENT_AGE     90 d
MAX_STATUS_EVENT_AGE      7 d
MAX_USAGE_EVENT_AGE      30 d
MAX_RESOURCES_EVENT_AGE   7 d
MAX_PRUNE_EVENT_AGE      30 d
MAX_HISTORIC_EVENT_AGE   90 d
```

Delete every `event_objects` row whose event has
`event_type = X` and `timestamp < now - MAX_X_EVENT_AGE`.
A `MAX_X_EVENT_AGE = -1` config disables that type's
prune, mirroring the existing eventlog daemon's
behaviour.

**Stage B — api-request object-type override.** The
existing eventlog daemon special-cases events tied to
`api-request` objects: regardless of `event_type`, they
age out at `MAX_API_REQUEST_EVENT_AGE` (1 day). In the
two-table model this becomes a prune of `event_objects`
rows where `object_type = 'api-request'` and the
referenced event is older than `MAX_API_REQUEST_EVENT_AGE`.
An event tied to both an api-request and an instance loses
its api-request reference after 1 day but the event row
stays alive (and visible from the instance's stream)
until the instance reference is dropped by stage A.

**Stage C — orphan events sweep.** After stages A and B,
delete `events` rows whose `event_uuid` is no longer
referenced by any `event_objects` row. This is the rule
that gives the "delete event only once its last object
reference is gone" semantics.

## SQL design

All three stages use a paged LIMIT-and-loop pattern to
avoid long lock holds on what is potentially the largest
table on the database node. Batch size 10000 matches the
existing eventlog daemon's per-sweep cap and is small
enough to commit in a fraction of a second on a healthy
InnoDB even when the rows being deleted are spread across
many pages.

### Stage A

```sql
DELETE eo
FROM event_objects eo
JOIN events e ON eo.event_uuid = e.event_uuid
WHERE e.event_type = %s
  AND e.timestamp < %s
LIMIT 10000;
```

Drives off the `(event_type, timestamp)` index on
`events` for the cutoff scan, joins to `event_objects` via
the secondary `(event_uuid)` index, and deletes the join
row. Loop until rowcount < 10000.

### Stage B

```sql
DELETE eo
FROM event_objects eo
JOIN events e ON eo.event_uuid = e.event_uuid
WHERE eo.object_type = 'api-request'
  AND e.timestamp < %s
LIMIT 10000;
```

Drives off the `event_objects` PK prefix
`(object_type, ...)` for the api-request filter and
joins to `events` for the cutoff. Loop until rowcount <
10000.

### Stage C

```sql
DELETE e
FROM events e
LEFT JOIN event_objects eo ON e.event_uuid = eo.event_uuid
WHERE eo.event_uuid IS NULL
LIMIT 10000;
```

Anti-join via the events PK and the event_objects
secondary `(event_uuid)` index. Loop until rowcount <
10000.

### Why no FK CASCADE

The phase 1 schema deliberately omitted the foreign key
between `event_objects.event_uuid` and
`events.event_uuid`, matching the project convention on
`object_states` / `object_metadata` /
`cluster_operation_targets`. With no FK there is no
cascade and the two-stage delete is explicit. The
benefit: stages A and B don't pay any per-row FK check
cost, and stage C can be a clean anti-join without the
referential-integrity overhead.

## Counter design

Two counters at module scope in `mariadb.py`, mirroring
the phase 2d `EVENTS_INSERTED` pattern:

```python
EVENTS_PRUNED = Counter(
    'database_events_pruned_total',
    'Event-object rows pruned, by event type (and the '
    'synthetic "api-request" type for the object-type '
    'override sweep).',
    ['event_type']
)

ORPHAN_EVENTS_PRUNED = Counter(
    'database_orphan_events_pruned_total',
    'Events rows pruned because no event_objects row '
    'referenced them.'
)
```

Incremented inside the respective `_direct_prune_*`
functions by the rowcount of each successful batch. The
api-request override increments
`EVENTS_PRUNED.labels(event_type='api-request')`, which
is a synthetic label value (not a real event_type) chosen
so operators can distinguish object-type-override prunes
from regular per-type prunes in the same metric.

Counters are registered on every daemon that imports
`mariadb`, but only move on sf-database (the only daemon
that runs the direct prune). Other daemons report them
at zero — harmless.

## RPC and accessor design

One new RPC on `protos/database.proto`:

```proto
message PruneEventsRequest {
  // Empty -- the cluster daemon decides cadence;
  // sf-database decides per-type retention from config.
}

message PruneEventsReply {
  bool success = 1;
  string error = 2;
  int64 rows_pruned = 3;
}

rpc PruneEvents (PruneEventsRequest) returns (PruneEventsReply) {}
```

The single RPC orchestrates all three stages inside the
direct function. The cluster maintainer doesn't see the
internal batching — it gets back a total rows-pruned
count for the daily summary log line. RPC timeout on the
client side is generous (5 min) because the daily prune
is allowed to be slow.

Three-layer accessor stack in `mariadb.py`:

- `_direct_prune_events_by_type(event_type, max_age) -> int`
  runs stage A's loop for one event_type, increments
  `EVENTS_PRUNED.labels(event_type=event_type)` by the
  per-batch rowcount, returns total deleted.
- `_direct_prune_api_request_events(max_age) -> int`
  runs stage B's loop, increments
  `EVENTS_PRUNED.labels(event_type='api-request')`,
  returns total deleted.
- `_direct_prune_orphan_events() -> int` runs stage C's
  loop, increments `ORPHAN_EVENTS_PRUNED`, returns total
  deleted.
- `_direct_prune_events() -> int` is the orchestrator:
  iterates the seven event_types from config, calls each
  stage, sums the rows, returns the total.
- `_grpc_prune_events() -> int` marshals to
  `PruneEventsRequest`, calls
  `stub.PruneEvents(request, timeout=300.0)`, returns
  `reply.rows_pruned`.
- Public `prune_events() -> int` routes via
  `_use_database_service()`.

Per the phase 1 / phase 2 pattern: the proto RPC's
abstract method on the daemon servicer must land in the
same commit as the proto change, or pre-commit's mypy
hook fails.

## Cluster daemon wiring

A new function in `shakenfist/daemons/cluster/
scheduled_tasks.py`:

```python
from shakenfist import mariadb

def prune_events() -> None:
    """Daily prune sweep of the events / event_objects tables.

    Runs on the elected cluster maintainer. Drives the
    three-stage prune described in
    docs/plans/PLAN-eventlog-direct-mariadb-phase-03-prune.md.
    """
    try:
        rows = mariadb.prune_events()
        LOG.info(f'Events prune sweep removed {rows} rows.')
    except Exception as e:
        LOG.warning(f'Events prune sweep failed: {e}')
```

Wired in `shakenfist/daemons/cluster/main.py` alongside
the existing `schedule.every(...).do(...)` calls
(currently around lines 427-436):

```python
schedule.every(1).days.do(scheduled_tasks.prune_events)
```

The cluster maintainer election lock guarantees only one
node runs this per day. If the maintainer loses the
election mid-prune, the in-flight RPC completes against
sf-database (the work is done) and the cluster daemon's
next loop iteration enters `_await_election` cleanly.
Worst case under a maintainer flap: a backup takes over
and re-runs the prune later the same day — the per-batch
DELETEs are idempotent so the second run is just a fast
no-op.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Add the three `_direct_prune_*` functions plus the orchestrator `_direct_prune_events` in `shakenfist/mariadb.py`, mirroring the placement and pattern of `_direct_delete_stale_cluster_operation_targets` (currently around lines 6004-6050). Per-type and api-request stages use the paged `LIMIT 10000` loop with the SQL from the "SQL design" section of the phase 3 plan. Orphan sweep uses the LEFT JOIN anti-join. All three increment their respective Counter (`EVENTS_PRUNED` for per-type and api-request, `ORPHAN_EVENTS_PRUNED` for orphan) at module scope; define those Counters near the existing `EVENTS_INSERTED` Counter (added in phase 2d). The orchestrator iterates the seven event_types from config (audit, mutate, status, usage, resources, prune, historic), skipping any with `MAX_X_EVENT_AGE = -1`, plus the api-request stage and the orphan sweep. Returns the total row count. Wrap each `_direct_prune_*` with try/except OperationalError per the existing prune patterns; a DB error returns the partial count rather than raising. Commit message subject: "mariadb: per-type, api-request, and orphan prune helpers." |
| 3b | high | opus | none | Add the `PruneEvents` RPC to `protos/database.proto`, add the `PruneEventsRequest` / `PruneEventsReply` messages following the placement of the existing event-related messages from phase 1 step 1b. Then run `tox -e genprotos`. In the same commit (per the phase 1/2 lesson — abstract methods on the proto require their handlers to land together for mypy), add the `PruneEvents` handler in `shakenfist/daemons/database/main.py` mirroring `RecordEventBatch` (currently around lines 4237-4280). The handler increments `self.monitor.counters['prune_events']`, calls `mariadb._direct_prune_events()`, returns `PruneEventsReply(success=True, error='', rows_pruned=rows)`. Register `'prune_events'` in the Monitor operations list. Also add `_grpc_prune_events` and the public `prune_events` in `shakenfist/mariadb.py` mirroring `_grpc_record_event_batch` / `record_event_batch`. The gRPC call uses `timeout=300.0`. Run `pre-commit run --all-files` — must be fully green. Commit message subject: "database: PruneEvents RPC, handler, and dispatcher." |
| 3c | medium | opus | none | Cluster daemon wiring in `shakenfist/daemons/cluster/scheduled_tasks.py` and `shakenfist/daemons/cluster/main.py`. Add `prune_events()` to `scheduled_tasks.py` as described in the "Cluster daemon wiring" section of the phase 3 plan. Add `schedule.every(1).days.do(scheduled_tasks.prune_events)` to `main.py`'s schedule registration block (currently around lines 427-436). Read both files first to confirm the exact placement and import style. The function logs the row count at info; an exception is caught and logged at warning so a transient DB failure doesn't kill the maintainer. Commit message subject: "cluster: schedule daily events prune." |
| 3d | medium | sonnet | none | Tests for phase 3. Add to `shakenfist/tests/test_events_storage.py`: (i) `_direct_prune_events_by_type` deletes only matching event_type rows older than cutoff, leaves newer ones, returns the correct count, increments the labeled counter; (ii) multi-object semantics — an event with three object refs, of which one is aged out by stage A and two by separate stages, ultimately gets the event row deleted in stage C; (iii) `_direct_prune_api_request_events` deletes only the api-request object_type rows; (iv) `_direct_prune_orphan_events` deletes events with no remaining event_objects rows; (v) orchestrator skips event_types whose MAX_*_EVENT_AGE is -1; (vi) Counter deltas (use the read-before / compare-after pattern from the phase 2d EVENTS_INSERTED tests). Use the same mock-engine/connection pattern from existing tests where possible; for the multi-stage semantic test you may need a deeper mock that lets the rowcount-driven loop terminate. Also add an integration-ish test for `scheduled_tasks.prune_events` calling `mariadb.prune_events` and logging the result. Run `tox -e py3` and `pre-commit run --all-files`. Commit message subject: "tests: phase 3 prune coverage." |

Ordering:

- 3a is independent; ships alone.
- 3b depends on 3a (its handler calls
  `_direct_prune_events`). Self-contained commit because
  proto+handler must land together.
- 3c depends on 3b (its scheduled task calls
  `mariadb.prune_events`).
- 3d depends on all of the above.

Per the phase 1/2 lesson: management session runs
`pre-commit run --all-files` between every commit so
cross-file mypy issues surface immediately. If any single
step trips mypy on the abstract-method front, fold it
into the predecessor commit rather than shipping a
broken intermediate.

## Risks and mitigations

- **Risk:** A single batched DELETE locks
  `event_objects` long enough to delay incoming
  `RecordEventBatch` writes from the drainer.
  **Mitigation:** 10000-row batches commit in well under
  a second on InnoDB at the cluster sizes Shaken Fist
  targets. The per-row write rate from the drainer
  (50-100 events per batch, hundreds of events per
  second peak across the cluster) is much smaller than
  the per-batch delete rate. If contention surfaces in
  production, the batch size is a single-line tuning
  knob.

- **Risk:** The api-request override drops the wrong
  rows because of subtle differences from the old per-
  sqlite-chunk semantics.
  **Mitigation:** Phase 3 step 3d test (iii) exercises
  the override explicitly. The new semantics
  ("`object_type='api-request'` row drops at
  `MAX_API_REQUEST_EVENT_AGE` regardless of event_type")
  is a one-line specification — easier to reason about
  than the old per-event branching.

- **Risk:** Stage C anti-join is slow on a large events
  table.
  **Mitigation:** The LEFT JOIN uses the
  `event_objects.event_uuid` secondary index and the
  `events.event_uuid` primary key — both covering. The
  daily cadence means stage C runs against at most
  ~yesterday's worth of new orphans (the prior day's
  prune already cleared everything older).

- **Risk:** The cluster maintainer loses the lock
  mid-prune. The RPC continues on sf-database; the
  cluster daemon re-enters election and a backup picks
  up. The backup re-runs the prune later in the day.
  **Mitigation:** Per-batch DELETEs are idempotent;
  re-running is a fast no-op against rows already gone.
  Counter values double-count the re-run's no-op
  batches, which an operator sees as a slightly noisy
  rate but no incorrect data. The next normal sweep
  cycle resumes the expected cadence.

- **Risk:** Phase 3 ships but sf-eventlog's own prune
  loop is still running against the (now stale) sqlite
  chunks. Two prune loops, but they target different
  storage.
  **Mitigation:** This is intentional. The sf-eventlog
  loop is harmless work against data nothing reads.
  Phase 5 deletes the loop along with the daemon.

## Definition of done

- [ ] `database_events_pruned_total{event_type=...}` and
      `database_orphan_events_pruned_total` are visible
      on sf-database's metrics endpoint.
- [ ] A manual integration test (insert N events across
      multiple event_types with a small `MAX_*_EVENT_AGE`,
      wait a day or fast-forward the time, observe
      sweep deletes the expected rows) passes.
- [ ] The multi-object semantic test (3d ii) passes
      against a real MariaDB in CI.
- [ ] `pre-commit run --all-files` is clean.
- [ ] Each commit is self-contained; commit messages
      follow project conventions including the Prompt
      paragraph and Co-Authored-By line with model and
      effort.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the management session on its
understanding of the brief and the surrounding context.
