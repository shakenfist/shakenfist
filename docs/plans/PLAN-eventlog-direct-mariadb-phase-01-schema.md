# Phase 1 — schema, accessors, RPC, and row-count gauge

Parent plan:
[PLAN-eventlog-direct-mariadb.md](PLAN-eventlog-direct-mariadb.md).

## Scope

Phase 1 puts the new write destination in place but does **not**
flip any caller over to it. After this phase ships, the
`events` and `event_objects` tables exist on the database
node, sf-database can accept event batches over a new
`RecordEventBatch` RPC, and the events row-count is visible
as a Prometheus gauge on `DATABASE_METRICS_PORT`. The
drainer continues to send batches to sf-eventlog; nothing
the operator can see has changed yet. Phase 2 is what cuts
the writes over.

The phase covers:

- `_get_events_table()` + `_ensure_events_schema()` and the
  same pair for `event_objects` in `shakenfist/mariadb.py`,
  registered in the master `ensure_schema()`.
- A `RecordEventBatch` RPC on `protos/database.proto`, with
  message types large enough to hold a batch of multi-
  object events including `event_uuid` and `request_id` as
  first-class fields.
- Three-layer accessors (`_direct_record_event_batch`,
  `_grpc_record_event_batch`, `record_event_batch`) and a
  read-only `_direct_get_events_count()` used by the gauge.
- The daemon handler in
  `shakenfist/daemons/database/main.py`, with the counter
  registered in the `Monitor` operations list.
- A Prometheus `Gauge('database_events_rows', ...)` plus a
  cheap periodic refresh slotted into the database daemon's
  `_run_inner()` 10-second idle loop.
- Unit tests covering schema-up, idempotent re-up,
  direct-path batch insert, public-path routing, and the
  gauge refresh.

Out of scope (deferred):

- The drainer's gRPC target change (phase 2).
- The per-event insert counter on sf-database (phase 2 —
  the decision in the master plan keeps it with the cut-
  over, so the counter and the writes go live in the same
  deploy). The `RecordEventBatch` RPC will pick up an
  auto-registered `database_record_event_batch_total` per-
  call counter via the existing Monitor pattern; that's
  free and not the "insert counter" the master plan
  references.
- Spool-depth gauge and drop counter wiring on callers
  (phase 2).
- The prune query and prune-delete counter (phase 3).
- The `GetObjectEvents` read RPC (phase 4).
- Deletion of sf-eventlog, the DLQ, or sqlite chunks
  (phase 5).

## Schema design

```sql
CREATE TABLE events (
    event_uuid   CHAR(36)     NOT NULL,
    event_type   VARCHAR(32)  NOT NULL,
    timestamp    DOUBLE       NOT NULL,
    fqdn         VARCHAR(255) NOT NULL,
    duration     DOUBLE       NULL,
    message      TEXT         NOT NULL,
    extra        JSON         NULL,
    request_id   VARCHAR(64)  NULL,
    PRIMARY KEY (event_uuid),
    INDEX idx_events_type_timestamp (event_type, timestamp),
    INDEX idx_events_request_id     (request_id)
) ENGINE=InnoDB;

CREATE TABLE event_objects (
    object_type VARCHAR(32) NOT NULL,
    object_uuid VARCHAR(36) NOT NULL,
    event_uuid  CHAR(36)    NOT NULL,
    PRIMARY KEY (object_type, object_uuid, event_uuid),
    INDEX idx_event_objects_event (event_uuid)
) ENGINE=InnoDB;
```

Notes on choices:

- `event_uuid CHAR(36)` (not `BINARY(16)`) to match the
  existing convention everywhere else in `mariadb.py` —
  every other UUID column is `VARCHAR(36)` or `CHAR(36)`.
  Consistency outweighs the 20 bytes per row.
- No FK constraint between `event_objects.event_uuid` and
  `events.event_uuid`. The two-phase prune (delete child
  rows, then delete orphan parent rows) needs both
  directions to be cheap, and the existing tables in this
  codebase deliberately avoid FKs for the same kind of
  reason (`object_states`, `object_metadata`,
  `cluster_operation_targets`). Referential integrity is
  enforced by the insert path always writing both tables
  in one transaction.
- The PK on `event_objects` is the natural key
  `(object_type, object_uuid, event_uuid)`. This is
  prefix-indexed for the per-object read in phase 4
  (`WHERE object_type = ? AND object_uuid = ?`).
- The `idx_event_objects_event (event_uuid)` secondary
  index supports the prune-side join (`DELETE eo FROM
  event_objects eo JOIN events e ON eo.event_uuid =
  e.event_uuid WHERE e.event_type = ? AND e.timestamp <
  ?`) without falling back to a full scan.
- `request_id` is its own column (decision 10 in the
  master plan), with its own index. Future request-scoped
  audit views become a one-line SQL query.

Schema version: 1 (first creation). The `_ensure_*_schema`
functions follow the cluster_operation_targets pattern at
`mariadb.py:774-850`, returning the standard
`{table, start_version, end_version, target_version,
migrated}` dict.

## RPC shape

Add to `protos/database.proto`:

```proto
message EventObject {
  ObjectType object_type = 1;
  string object_uuid = 2;
}

message Event {
  string event_uuid = 1;
  string event_type = 2;
  double timestamp = 3;
  string fqdn = 4;
  double duration = 5;       // optional; 0 means unset
  string message = 6;
  string extra_json = 7;     // JSON-encoded string, empty means null
  string request_id = 8;     // empty means null
  repeated EventObject objects = 9;
}

message RecordEventBatchRequest {
  repeated Event events = 1;
}

rpc RecordEventBatch (RecordEventBatchRequest)
    returns (StatusReply) {}
```

Notes:

- `extra_json` is the JSON-encoded payload sent as a string,
  not a `google.protobuf.Struct`. The drainer already holds
  `extra` as a Python dict and json.dumps-es it; mirroring
  that as a string field keeps the proto small and avoids
  Struct's quirks around `None` vs empty.
- `duration = 0` and empty `request_id` / `extra_json`
  encode "absent." The direct accessor maps them to SQL
  NULL on insert. This avoids `oneof` plumbing which the
  database proto doesn't use elsewhere.
- `ObjectType` is the existing shared enum
  (`shakenfist_enums.proto`); reuses the convention from
  `CreateClusterOperationTargetRequest` at
  `database.proto:1774-1780`.

The batch shape matches what the existing
`RecordMultiEventBatch` RPC on `event.proto` already
delivers, with `event_uuid` and `request_id` promoted to
first-class fields. The drainer change in phase 2 is then
a near-mechanical re-marshal.

## Gauge design

A single Prometheus `Gauge` registered in the database
daemon:

```python
self.events_rows_gauge = Gauge(
    'database_events_rows',
    'Current row count in the events table.'
)
```

Refresh approach: in `_run_inner` (`daemons/database/main.py`
line 5077), every Nth tick of the existing 10-second idle
loop, call `self.events_rows_gauge.set(
mariadb._direct_get_events_count())`. N = 6 gives a one-
minute refresh, which is plenty for an operator-visible
gauge. The query is `SELECT COUNT(*) FROM events`, which
on InnoDB is O(rows) but cheap enough at the cluster sizes
Shaken Fist actually targets; if a future operator
complains, an `INFORMATION_SCHEMA.TABLES.TABLE_ROWS`
approximation is available, but it's only approximate and
the trade-off isn't worth pre-empting.

Phase 2's spool-depth gauges and the (deferred) per-event
insert counter live on different metrics endpoints, on
different daemons; phase 1 only owns this one.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | Add `_get_events_table()` and `_ensure_events_schema(engine)` in `shakenfist/mariadb.py`, mirroring `_get_cluster_operation_targets_table()` (lines 726-771) and `_ensure_cluster_operation_targets_schema()` (lines 774-850). Schema version 1. Columns and indexes exactly as in the "Schema design" section of `docs/plans/PLAN-eventlog-direct-mariadb-phase-01-schema.md`. Do the same for `event_objects`. Register both `_ensure_*_schema(engine)` calls in `ensure_schema()` (lines 2019-2088), adding them after the `_ensure_event_dlq_schema` line for proximity. Do not touch `DATA_MIGRATIONS` — phase 1 has no migration. Commit message subject: "mariadb: add events and event_objects tables." |
| 1b | medium | opus | none | Add the `EventObject`, `Event`, and `RecordEventBatchRequest` messages plus the `RecordEventBatch` RPC to `protos/database.proto`, following the placement and style of `CreateClusterOperationTargetRequest` (lines 1774-1780) and the service rpc entry near line 264. Exact field layout in the "RPC shape" section of the phase 1 plan. Then run `tox -e genprotos` (per CLAUDE.md, never call grpc_tools.protoc directly). Verify the regenerated `shakenfist/protos/database_pb2.py` and `database_pb2.pyi` carry the new messages and the import-rewrites worked (`from shakenfist.protos import ...`). Commit the proto change and regenerated stubs together. Commit message subject: "protos: add RecordEventBatch on the database service." |
| 1c | high | opus | none | Add the three-layer accessor stack in `shakenfist/mariadb.py` for the new RPC, mirroring `create_cluster_operation_target` (direct at 5561-5610, gRPC at 5918-5943, public at 6139-6164). Specifically: `_direct_record_event_batch(events: list[EventBatchEntry])` inserts into `events` and `event_objects` inside one `with engine.begin() as conn` transaction (use `engine.begin` not `engine.connect` so a partial failure rolls back the whole batch); handles `IntegrityError` on `event_uuid` PK as idempotent (log and skip); other `IntegrityError`s as a warning. `_grpc_record_event_batch` marshals to `RecordEventBatchRequest`, calls `_grpc_call()`, catches `grpc.RpcError`. Public `record_event_batch` routes via `_use_database_service()` (line 199). Also add `_direct_get_events_count(engine) -> int` (no gRPC wrapper, it's only called from the database daemon itself). The `EventBatchEntry` dataclass goes in `shakenfist/schema/event.py` (new file) — pydantic model with `event_uuid, event_type, timestamp, fqdn, duration, message, extra, request_id, objects: list[tuple[str, str]]`. Use `Optional` for `duration`, `extra`, `request_id`. Commit message subject: "mariadb: add three-layer accessors for RecordEventBatch." |
| 1d | high | opus | none | Add the `RecordEventBatch` handler to `shakenfist/daemons/database/main.py`, mirroring `CreateClusterOperationTarget` (lines 4205-4234). Counter increment `self.monitor.counters['record_event_batch'].inc()` at top. Marshals proto back to `list[EventBatchEntry]`, calls `mariadb._direct_record_event_batch(...)`, returns `StatusReply`. Register `'record_event_batch'` in the operations list at lines 4884-5027 (find the right alphabetical position; the list is roughly grouped but not strictly ordered, so place near existing object-write entries). Add the gauge: in `Monitor.__init__` (around line 4880), `self.events_rows_gauge = Gauge('database_events_rows', 'Current row count in the events table.')`. In `_run_inner` (line 5077), add a tick counter; every 6 ticks (~60 s), call `self.events_rows_gauge.set(mariadb._direct_get_events_count(mariadb._get_engine()))`. Wrap the set call in a try/except — a gauge refresh failure must not kill the daemon. Commit message subject: "sf-database: serve RecordEventBatch and export events row gauge." |
| 1e | medium | sonnet | none | Add unit tests in `shakenfist/tests/test_events_storage.py` (new file), mirroring the layout of `test_cluster_operation_targets.py` (mock-the-public-API style) and `test_event_dlq.py` (mock-engine/connection style for direct-path tests). Cover: (i) `_ensure_events_schema` returns version 1 on a fresh engine; (ii) `_ensure_events_schema` is idempotent (second call returns unchanged); (iii) same pair for `event_objects`; (iv) `_direct_record_event_batch` writes one event with one object to both tables in a single transaction (mock connection records the SQL); (v) `_direct_record_event_batch` writes one event with three objects → one events row, three event_objects rows; (vi) duplicate `event_uuid` is skipped silently; (vii) `record_event_batch` routes to `_grpc_*` when `_use_database_service()` is True and to `_direct_*` when False; (viii) `_direct_get_events_count` returns the scalar from the connection. Use `ShakenFistTestCase` as the base. Run `tox` and fix any flake8/mypy issues. Commit message subject: "tests: unit coverage for the events schema and accessors." |

After 1e the management session runs:

- `pre-commit run --all-files` (must be clean).
- A manual sanity-check that `tox -e genprotos` is a no-op
  (the regenerated files committed in 1b should be
  stable).
- A read-through of the modified `mariadb.py` chunks to
  confirm no stray edits leaked into unrelated accessors.
- An eyeball of `daemons/database/main.py` to confirm the
  operations list still parses cleanly and the counter
  registration loop covers the new entry.

## Risks and mitigations

- **Risk:** `SELECT COUNT(*) FROM events` becomes the
  dominant query on a busy cluster.
  **Mitigation:** sample at 1 minute (decision above), not
  per-scrape; fallback to `INFORMATION_SCHEMA.TABLES`
  approximate count is a one-line change if needed. Filed
  as Future work on the master plan only if the symptom
  appears.

- **Risk:** Bulk insert in a single transaction holds row
  locks longer than per-row inserts and contends with
  prune.
  **Mitigation:** batch sizes are 50-100 events per RPC
  (spool drainer's existing batching) and prune is a daily
  cron sweep — overlap is negligible. If it surfaces in
  phase 2's load benchmark we can chunk the transaction.

- **Risk:** The proto field layout (`extra_json` as
  string, `duration = 0` meaning unset) makes phase 2's
  marshalling subtly wrong.
  **Mitigation:** phase 1's unit tests assert the absent-
  vs-present semantics on the direct-path side; phase 2's
  marshalling tests pair-up against the same fixtures.

- **Risk:** Adding two table-ensures in `ensure_schema()`
  affects daemon startup order or timing in CI.
  **Mitigation:** `_ensure_*_schema` is idempotent and
  cheap on an already-created table; CI startup-time
  impact is one extra `SHOW TABLES`-style probe per fresh
  cluster. No production behaviour change until phase 2
  ships.

## Definition of done

- [ ] Both new tables present and CREATE-validated against
      a real MariaDB in CI startup.
- [ ] `RecordEventBatch` RPC registered, callable, and
      observable in `database_record_event_batch_total`.
- [ ] `database_events_rows` gauge appears on
      `DATABASE_METRICS_PORT` (default 13006) and tracks
      the table count to within ~1 minute.
- [ ] Unit tests pass under `tox`.
- [ ] `pre-commit run --all-files` is clean.
- [ ] `tox -e genprotos` is a no-op against the committed
      tree.
- [ ] Each step is a single self-contained commit; commit
      messages follow project conventions including the
      Prompt paragraph and Co-Authored-By line with model
      and effort.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the management session on its
understanding of the brief and the surrounding context.
