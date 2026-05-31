# Phase 2 — write cut-over and metrics

Parent plan:
[PLAN-eventlog-direct-mariadb.md](PLAN-eventlog-direct-mariadb.md).
Predecessor:
[PLAN-eventlog-direct-mariadb-phase-01-schema.md](PLAN-eventlog-direct-mariadb-phase-01-schema.md).

## Scope

Phase 2 is the write cut-over: it makes events flow into the
new `events` / `event_objects` tables on sf-database via the
`RecordEventBatch` RPC that landed in phase 1. After this
phase ships, sf-eventlog still runs and still serves the
REST read path (that's phase 4), but it no longer receives
anything new from the caller-side drainer. The caller-
facing `eventlog.add_event*` signature is unchanged.

The phase covers:

- Promote `event_uuid` and `request_id` to first-class
  top-level keys on the spool payload. `event_uuid` is
  generated in `add_event_multi` for **every** event (not
  only multi-object events as `correlation_id` was), so
  the RPC's idempotency story is the same on retries.
  `request_id` is lifted out of `extra` into its own
  top-level key, leaving `extra` for genuinely free-form
  payload.
- Swap the drainer's gRPC target. Today
  `eventlog_drainer._send_batch` calls
  `stub.RecordMultiEventBatch` on `sf-eventlog` via a
  drainer-managed channel to `EVENTLOG_NODE_IP:
  EVENTLOG_API_PORT`. Phase 2 replaces that with
  `mariadb.record_event_batch(records)`, where `records`
  is a `list[EventRecord]` (the pydantic model added in
  phase 1c). The mariadb public function routes
  direct-vs-gRPC via `_use_database_service()`, so on
  sf-database itself the drainer writes through the
  direct path and on every other daemon it goes via the
  database gRPC channel that mariadb.py already manages.
- Remove the drainer's `_fallback_to_dlq` path. With the
  spool as the durability boundary, the right behaviour
  on RPC failure is "leave the batch in the spool and
  retry next loop iteration." The DLQ table itself still
  exists in phase 2 — only the drainer stops writing to
  it. The DLQ table, the `EVENTLOG_SUPPRESS_GRPC` /
  `set_force_event_dlq` paths, and the DLQ-drain code on
  sf-eventlog are deleted together in phase 5.
- Wire the caller-side metrics:
  - Convert the existing `_dropped_total` bare int in
    `eventlog_spool.py` to a `prometheus_client.Counter`
    named `eventlog_spool_dropped_total`.
  - Add an `eventlog_spool_depth` Gauge using
    `Gauge.set_function(callback)` so the count is
    sampled on each Prometheus scrape rather than tracked
    incrementally on every enqueue / dequeue.
  - Both metric objects live at module scope in
    `eventlog_spool.py`. Because `prometheus_client` uses
    a single process-wide default registry, every daemon
    that imports `eventlog.py` (which transitively imports
    `eventlog_spool.py`) picks the metrics up automatically
    on its existing metrics endpoint — no per-daemon
    bootstrap.
- Wire the cluster-side insert counter:
  `database_events_inserted_total{event_type=...}` on
  sf-database, incremented inside
  `_direct_record_event_batch` by one per event written.
  Stays at zero until phase 2 ships; with phase 2 it
  pairs with the `database_events_rows` gauge to give an
  operator both a rate (counter) and a depth (gauge) view.

Out of scope (deferred):

- Removing `EVENTLOG_SUPPRESS_GRPC`,
  `set_force_event_dlq`, `EVENTLOG_NODE_IP`,
  `EVENTLOG_API_PORT`, the DLQ table, and the
  `_fallback_to_dlq` call site in `eventlog.py` (phase 5).
  The drainer stops *calling* `_fallback_to_dlq` in phase
  2, but the function itself and the DLQ writer in
  `eventlog.py:_add_event_dlq_inner` are still wired so
  sf-eventlog's own callers (the legacy startup paths)
  keep working until phase 5.
- The prune sweep and `database_events_pruned_total`
  counter (phase 3).
- The read RPC and REST cut-over (phase 4).
- Deleting sf-eventlog and the DLQ (phase 5).

## Payload shape change

Current spool payload (one row in the per-daemon spool
sqlite), JSON-encoded:

```json
{
  "event_type": "audit",
  "fqdn": "node-01.example.com",
  "duration": 0.12,
  "message": "Created instance",
  "extra": "{\"request-id\": \"req-abc\", \"foo\": 1}",
  "timestamp": 1748736000.123,
  "objects": [["instance", "uuid-1"]]
}
```

Notes on the current shape:
- `extra` is a *JSON-encoded string*, not a nested object —
  the API layer JSON-dumps before enqueue.
- `request-id` (note the hyphen) is buried inside that
  JSON string and only surfaces if the consumer parses
  `extra`.
- `correlation_id` is generated in `add_event_multi` only
  for multi-object events and is **not** persisted to the
  spool — it lives as a local variable used by the legacy
  gRPC and DLQ paths that the spool bypasses.

Phase 2 payload:

```json
{
  "event_uuid": "550e8400-e29b-41d4-a716-446655440000",
  "event_type": "audit",
  "fqdn": "node-01.example.com",
  "duration": 0.12,
  "message": "Created instance",
  "extra": "{\"foo\": 1}",
  "request_id": "req-abc",
  "timestamp": 1748736000.123,
  "objects": [["instance", "uuid-1"]]
}
```

Changes:

- `event_uuid` added (always present, generated by
  `sf_random.random_id()` in `add_event_multi` for every
  event). Replaces the role `correlation_id` played for
  multi-object events; the new key is consistent for
  single- and multi-object events alike. This is the key
  that lets `RecordEventBatch` retries be idempotent
  against the `events` table's PK.
- `request_id` lifted to a top-level key. The original
  `extra['request-id']` injection in `add_event_multi`
  (lines 264-269 of `eventlog.py`) moves *out* of
  `extra`. The pickup from `flask.request.environ
  ['FLASK_REQUEST_ID']` is unchanged; only the destination
  key on the payload changes. Explicit `request_id=...`
  kwarg callers continue to work unchanged.
- `extra` keeps the free-form-JSON role and no longer
  carries `request-id`.

Backward compatibility for in-flight spool rows: at upgrade
time a daemon may have spool rows in the old format. The
drainer reads with fallbacks: `payload.get('event_uuid')
or sf_random.random_id()` and
`payload.get('request_id') or
json.loads(payload.get('extra', '{}')).get('request-id')`.
The generated UUID for a missing `event_uuid` is *not*
ideal (retry idempotency depends on a stable UUID), but a
single drain cycle empties the spool of old-format rows
and any duplicate inserts caused by a retry in that
narrow window are caught by the events PK and treated as
idempotent at the database side. Acceptable transient
behaviour for an upgrade.

## Drainer RPC swap

Today `_send_batch` in `eventlog_drainer.py` builds an
`EventMultiBatchRequest` proto and calls
`stub.RecordMultiEventBatch(request, timeout=10.0,
wait_for_ready=False)` on a drainer-managed channel.

Phase 2 replaces the per-batch RPC call with:

```python
from shakenfist import mariadb
from shakenfist.schema.event import EventRecord

def _build_records(rows):
    records = []
    for row_id, payload in rows:
        records.append(EventRecord(
            event_uuid=payload.get('event_uuid')
                       or sf_random.random_id(),
            event_type=payload['event_type'],
            timestamp=payload['timestamp'],
            fqdn=payload['fqdn'],
            duration=payload.get('duration') or None,
            message=payload['message'],
            extra=json.loads(payload['extra'])
                  if payload.get('extra') else None,
            request_id=payload.get('request_id')
                       or json.loads(payload.get('extra', '{}'))
                              .get('request-id')
                       or None,
            objects=[
                (obj_type, obj_uuid)
                for obj_type, obj_uuid in payload['objects']
            ],
        ))
    return records

# In the drain loop:
records = _build_records(rows)
if mariadb.record_event_batch(records):
    spool.delete_ids([row_id for row_id, _ in rows])
    backoff = BACKOFF_INITIAL
else:
    backoff = min(backoff * 2, BACKOFF_MAX)
    time.sleep(backoff)
```

Things to delete from `eventlog_drainer.py`:

- The drainer-managed gRPC channel and stub setup
  (currently lines 143-153). The drainer no longer needs
  to know about gRPC at all; `mariadb.record_event_batch`
  reuses the database channel that `mariadb._get_database_stub`
  already manages process-wide.
- The `_fallback_to_dlq` function (currently lines
  239-265) and its call site. With the spool providing
  durability, leaving failed batches in the spool for
  retry is strictly better than re-queueing into the DLQ
  — the DLQ exists only because the original sf-eventlog
  pre-spool world had nowhere else to put events on RPC
  failure. Removing the call site here in phase 2 means
  the DLQ writers reduce to the SUPPRESS_GRPC / force-
  event-dlq paths in `eventlog.py`, which phase 5 deletes
  along with the DLQ table.
- `EVENTLOG_NODE_IP` / `EVENTLOG_API_PORT` imports from
  `config`. These config keys themselves stay until phase
  5; the drainer just stops reading them.

Behaviour preserved:

- The drain-loop cadence, batch size cap
  (`DRAIN_BATCH_SIZE = 100`), `DRAIN_POLL_INTERVAL`,
  exponential backoff (`BACKOFF_INITIAL`, `BACKOFF_MAX`),
  and the daemon's `_stop_event` shutdown path. Phase 2
  changes the RPC target, not the transport policy
  around it.

## Metrics design

### Spool depth and drop counter

Both live at module scope in `eventlog_spool.py` so any
process that imports the module exposes them on its
existing Prometheus endpoint. No per-daemon registration
or per-daemon metrics-port plumbing is required —
`prometheus_client` uses a single global default registry
and every daemon already calls `start_http_server(...)`
on its own port at startup. The metrics show up
automatically.

```python
from prometheus_client import Counter, Gauge

EVENTLOG_SPOOL_DROPPED = Counter(
    'eventlog_spool_dropped_total',
    'Events dropped at the spool high-water mark.'
)

EVENTLOG_SPOOL_DEPTH = Gauge(
    'eventlog_spool_depth',
    'Rows currently pending in the local eventlog spool.'
)

def _sample_depth() -> int:
    spool = _get_spool()  # the global Spool singleton
    if spool is None:
        return 0
    try:
        return spool.count()
    except Exception:
        return 0

EVENTLOG_SPOOL_DEPTH.set_function(_sample_depth)
```

Notes:

- `Gauge.set_function(callback)` is `prometheus_client`'s
  idiom for "sample on each scrape." The callback runs at
  scrape time (which is operator-controlled cadence,
  typically 15-60 s), so the cost of `SELECT COUNT(*) FROM
  events` against the spool sqlite is paid at most that
  often — well under the cost of incrementing a depth
  gauge on every enqueue and decrement on every dequeue,
  and free of races between the drainer and the API
  threads.
- `spool.count()` is a new helper on the `Spool` class —
  trivial `SELECT COUNT(*)` against the spool's `events`
  table.
- The existing `_dropped_total` bare-int counter in
  `eventlog_spool.py` is replaced by the
  `EVENTLOG_SPOOL_DROPPED.inc()` call at the drop site.
  The "log every 100 drops" diagnostic stays — it's
  useful for grep without prometheus.

### Per-event insert counter on sf-database

Added in `daemons/database/main.py` alongside the
existing `events_rows_gauge`:

```python
self.events_inserted_counter = Counter(
    'database_events_inserted_total',
    'Events inserted into the events table.',
    ['event_type']
)
```

Incremented inside `mariadb._direct_record_event_batch`
once per event written:

```python
for record in events:
    # ... existing insert ...
    _events_inserted_metric(record.event_type)
```

`_events_inserted_metric` is a tiny module-level helper
that does the `Counter.labels(event_type=...).inc()`
dance and is import-safe even when the Counter isn't
registered (test environments). Pattern lives in
`mariadb.py` so the counter is incremented uniformly
whether the insert came from the gRPC handler or from a
direct caller (the database daemon's own spool drainer).

The Counter object itself can be defined either at
module scope in `mariadb.py` (mirroring the spool
metrics' pattern) or on the Monitor in
`daemons/database/main.py`. Module-scope is simpler and
keeps the metric definition next to the code that
increments it; the database daemon picks it up via the
shared registry just like the spool metrics. Recommend
module-scope.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | opus | none | Promote `event_uuid` and `request_id` to first-class top-level keys on the spool payload. In `shakenfist/eventlog.py`'s `add_event_multi` (currently around line 224), generate `event_uuid = sf_random.random_id()` *always* (not only multi-object — replaces the existing `correlation_id` generation at lines 258-260). Lift `request_id` out of the `extra` dict mutation at lines 264-269 — keep the Flask-context lookup, but put the result into a new top-level `request_id` key on the spool dict instead of mutating `extra['request-id']`. Explicit `request_id=...` kwarg callers continue to work; they end up in the same top-level key. Update the spool dict built at lines 304-315 to include `event_uuid` and `request_id`. Leave the legacy gRPC and DLQ paths (lines 327-355) alone — those are deleted in phase 5; in this step they just see the same dict with extra keys they ignore. Commit message subject: "eventlog: promote event_uuid and request_id on spool payloads." |
| 2b | high | opus | none | Swap the drainer's RPC target from sf-eventlog to sf-database. In `shakenfist/eventlog_drainer.py`: (i) replace `_build_batch_request` with a `_build_records` that returns `list[EventRecord]` per the "Drainer RPC swap" section of the phase 2 plan, including the dual-key fallbacks for `event_uuid` and `request_id` so any in-flight spool rows from before 2a still drain cleanly; (ii) replace the `stub.RecordMultiEventBatch(...)` call with `mariadb.record_event_batch(records)`; (iii) delete the drainer-managed gRPC channel/stub setup (currently lines 143-153) and the `EVENTLOG_NODE_IP` / `EVENTLOG_API_PORT` imports; (iv) delete the `_fallback_to_dlq` function and its call site — failed batches stay in the spool for retry, period. Keep the existing backoff/poll/batch-size policy. Note that the old `EventMultiBatchRequest` / `EventMultiRequest` / `EventObject` proto types from `event.proto` are no longer referenced by the drainer; the imports go too. Do NOT touch the proto file itself — `event.proto` is deleted in phase 5. Run `tox -e py3 -- shakenfist.tests.test_eventlog_drainer` (or whatever drainer tests exist) and fix any breakage. Commit message subject: "eventlog_drainer: write events via sf-database." |
| 2c | medium | opus | none | Caller-side metrics in `shakenfist/eventlog_spool.py`. Convert the existing `_dropped_total` bare int to a `prometheus_client.Counter` named `eventlog_spool_dropped_total`. Add a `Gauge` named `eventlog_spool_depth` using `Gauge.set_function(callback)` per the phase 2 plan's "Metrics design" section. Add a `count()` method on the `Spool` class doing a trivial `SELECT COUNT(*) FROM events`. Both metric objects live at module scope so the process-wide prometheus_client default registry picks them up on every daemon's metrics endpoint. Keep the "log every 100 drops" diagnostic. Commit message subject: "eventlog_spool: prometheus depth gauge and drop counter." |
| 2d | low | sonnet | none | Per-event insert counter on sf-database. In `shakenfist/mariadb.py`, define `EVENTS_INSERTED = Counter('database_events_inserted_total', 'Events inserted into the events table.', ['event_type'])` at module scope near the imports (mirror the eventlog_spool pattern). In `_direct_record_event_batch`, after each successful event insert, call `EVENTS_INSERTED.labels(event_type=record.event_type).inc()`. Counter increments per event, not per batch. Note: this is registered everywhere mariadb.py is imported, but it only moves on the database daemon (the only place `_direct_record_event_batch` actually runs); the metric is harmlessly zero on other daemons' endpoints. Commit message subject: "mariadb: events insert counter." |
| 2e | medium | sonnet | none | Tests. Add unit tests covering: (i) `add_event_multi` produces a spool dict with `event_uuid` and `request_id` as top-level keys, and `extra` no longer contains `request-id`; (ii) `_build_records` in the drainer correctly converts both new-format and old-format spool rows to `EventRecord` (old format = no `event_uuid`, `request-id` only inside `extra`); (iii) the drainer calls `mariadb.record_event_batch` and deletes spool ids on success / leaves them on failure; (iv) `Spool.count()` returns the correct row count; (v) `EVENTS_INSERTED` counter increments once per event in a batch. Add to `shakenfist/tests/test_eventlog.py` and `shakenfist/tests/test_events_storage.py` (extending the existing file from phase 1), or a new `test_eventlog_drainer.py` if none exists. Run `pre-commit run --all-files` and `tox -e py3` to confirm green. Commit message subject: "tests: phase 2 spool, drainer, and counter coverage." |

Ordering:

- 2a, 2c, and 2d are independent and may be sub-agented in
  parallel.
- 2b depends on 2a (the drainer needs `event_uuid` in the
  payload for stable retries).
- 2e depends on all four.

Per the lesson from phase 1's 1c+1d combination, the
management session should run `pre-commit run --all-files`
between each commit (not just on staged files) so any
cross-file mypy or type-checker issue surfaces
immediately. If 2b's mypy run trips an interim error
because of dropped imports referenced elsewhere, the
session combines the affected commits rather than
shipping a broken intermediate.

## Risks and mitigations

- **Risk:** Phase 2 ships but sf-eventlog stays running.
  Operators see no DB write to sf-eventlog's sqlite and
  may wonder where new events went.
  **Mitigation:** Documented in phase 6 release notes
  (drafted alongside phase 2 in the release announcement).
  Operationally also visible because
  `database_events_inserted_total` starts rising and
  sf-eventlog's gRPC request counter goes flat.

- **Risk:** Old-format spool rows fail to drain after
  upgrade because of a missing field the dual-key
  fallback doesn't cover.
  **Mitigation:** The `_build_records` fallbacks cover
  the two fields that changed (`event_uuid`,
  `request_id`); every other key has been on the payload
  since the spool's introduction. Phase 2e test (ii)
  exercises an old-format row explicitly.

- **Risk:** `Gauge.set_function` callback raises during
  scrape and breaks the metrics endpoint.
  **Mitigation:** `_sample_depth` is try/except wrapped
  and returns 0 on any error (matches the phase 1
  `events_rows_gauge` refresh's try/except posture).

- **Risk:** Per-event Counter increment is on the hot
  path of every batch insert.
  **Mitigation:** `prometheus_client`'s Counter.inc() is
  lock-free and on the order of hundreds of nanoseconds
  per call. At realistic event rates (the resources
  daemon's 60s status loop is the noisiest source —
  order of hundreds of events per second across the
  cluster) the overhead is well below 1% of the per-event
  cost.

- **Risk:** sf-database is unreachable while a daemon is
  generating events (network blip, sf-database restart).
  **Mitigation:** Spool absorbs the burst; drainer backs
  off and retries; the
  `eventlog_spool_depth` gauge climbs visibly, giving
  operators a leading indicator before the high-water-
  mark drop counter starts moving.

- **Risk:** The DLQ table is still wired in `eventlog.py`
  but the only writer left after phase 2 is the
  `SUPPRESS_GRPC` / `force_event_dlq` startup paths —
  these may write events that nothing drains, since the
  drainer no longer calls `_fallback_to_dlq` and the
  sf-eventlog drain loop is the only consumer of the
  DLQ. Until phase 5, events in the DLQ from those
  startup paths drain via sf-eventlog as today, into
  sf-eventlog's sqlite (which is no longer the source of
  truth).
  **Mitigation:** These startup paths fire rarely (at
  daemon start) and the event volume from them is small.
  The phase 5 plan removes both the SUPPRESS_GRPC /
  force_event_dlq writers and the DLQ table in a single
  change.

## Definition of done

- [ ] New events appear in MariaDB `events` and
      `event_objects` tables after a daemon-induced event
      (e.g. create a small instance, query the events
      table).
- [ ] `sf-eventlog`'s `RecordMultiEventBatch` RPC counter
      stops incrementing after the cut-over deploy.
- [ ] `database_events_inserted_total{event_type=...}`
      increases on sf-database's metrics endpoint at the
      expected rate.
- [ ] `eventlog_spool_depth` and
      `eventlog_spool_dropped_total` are visible on every
      daemon's metrics endpoint (sampled via
      `/metrics`).
- [ ] A simulated sf-database outage (kill -STOP the
      daemon for 30 s, then SIGCONT) shows depth gauge
      climb then drain to zero, with zero dropped events
      and the drainer backoff visibly reset on resume.
- [ ] Unit tests pass under `tox`.
- [ ] `pre-commit run --all-files` is clean.
- [ ] Each commit is self-contained; commit messages
      follow project conventions including the Prompt
      paragraph and Co-Authored-By line with model and
      effort.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the management session on its
understanding of the brief and the surrounding context.
