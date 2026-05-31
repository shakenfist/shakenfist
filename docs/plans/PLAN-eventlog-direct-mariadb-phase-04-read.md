# Phase 4 — REST API direct-read path

Parent plan:
[PLAN-eventlog-direct-mariadb.md](PLAN-eventlog-direct-mariadb.md).
Predecessors:
[Phase 1](PLAN-eventlog-direct-mariadb-phase-01-schema.md),
[Phase 2](PLAN-eventlog-direct-mariadb-phase-02-write.md),
[Phase 3](PLAN-eventlog-direct-mariadb-phase-03-prune.md).

## Scope

Phase 4 is the read cut-over. The five REST event-list
endpoints (`/instance/<u>/events`, `/artifact/<u>/events`,
`/network/<u>/events`, `/node/<n>/events`,
`/blob/<u>/events`) stop opening sqlite via
`eventlog.EventLog(...)` and start calling a new
`mariadb.get_object_events(...)` public function that
routes through `_use_database_service` direct-vs-gRPC.
After this phase ships, sf-api can serve event reads from
any node — the sf-eventlog-locality coupling is gone.

This is also the operator-visible history-loss point
called out in master plan decision 5: pre-cutover events
in sqlite remain on disk but are no longer reachable
through the REST API. The history-loss is documented in
phase 6's release notes; it's intentional.

The phase covers:

- A new `_direct_get_object_events` function on sf-database
  returning a list of read-shaped rows (one row per
  matching event, NOT one per event_objects row — the
  read query joins through the per-object reference).
- A new `_direct_delete_object_events` function for the
  hard_delete cleanup path. Phase 4 wires this into
  `baseobject.DatabaseBackedObject.hard_delete` (which
  currently cleans up `object_states` and
  `object_metadata` but not events — this gap is called
  out in the master plan's review checklist).
- Two new RPCs (`GetObjectEvents`, `DeleteObjectEvents`)
  on `protos/database.proto`, with handlers, gRPC
  wrappers, and public dispatchers. Combined per the
  established pattern that abstract-method proto changes
  must land with their handlers.
- A new pydantic model `EventReadRow` in
  `shakenfist/schema/event.py` (alongside `EventRecord`),
  carrying the read-shaped fields. Distinct from
  `EventRecord` so the read path doesn't have to
  populate the `objects: list[tuple[str, str]]` field
  that's only meaningful at write time.
- Cut-over of the five REST endpoints in
  `external_api/{instance,artifact,network,node,blob}.py`.
  Each endpoint preserves its existing auth decorator
  stack (master plan checklist item) and the response
  shape is unchanged except `correlation_id` →
  `event_uuid` (master plan decision 11) and a new
  top-level `request_id` field (phase 2a's promotion).
- Wiring `mariadb.delete_object_events` into
  `baseobject.DatabaseBackedObject.hard_delete` so that
  hard-deleting an instance, artifact, network, blob,
  etc. also cleans up its event_objects rows. The
  `events` row itself remains alive if other objects
  still reference it; if it becomes orphaned, the daily
  cluster prune (phase 3 stage C) catches it.

Out of scope (deferred):

- Deleting sf-eventlog and the DLQ (phase 5).
- Deleting the on-disk sqlite chunks (phase 5).
- Removing the `EVENTLOG_*` configs (phase 5).
- Cursor-style pagination (Future work in the master
  plan).
- Removing `_get_event_logs_path` and `EventLog` class
  from `eventlog.py` — those are still needed by the
  sf-eventlog daemon until phase 5 deletes it.

## Read RPC and response shape

Proto:

```proto
message GetObjectEventsRequest {
  ObjectType object_type = 1;
  string object_uuid = 2;
  int32 limit = 3;
  string event_type_filter = 4;
}

message EventReadRow {
  string event_uuid = 1;
  string event_type = 2;
  double timestamp = 3;
  string fqdn = 4;
  double duration = 5;
  string message = 6;
  string extra_json = 7;
  string request_id = 8;
}

message GetObjectEventsReply {
  repeated EventReadRow events = 1;
}

rpc GetObjectEvents (GetObjectEventsRequest)
    returns (GetObjectEventsReply) {}
```

Field encoding matches the phase 1 `EventBatchEntry`
convention: `duration == 0.0`, empty `extra_json`, empty
`request_id` all encode "absent" and are mapped to SQL
NULL in the direct function. `event_type_filter` empty
means "any event type."

The new pydantic model `EventReadRow` in
`shakenfist/schema/event.py`:

```python
class EventReadRow(BaseModel):
    event_uuid: str
    event_type: str
    timestamp: float
    fqdn: str
    duration: Optional[float] = None
    message: str
    extra: Optional[dict] = None
    request_id: Optional[str] = None
```

Distinct from `EventRecord` (write-side model):
`EventReadRow` has no `objects` field because the read
query joins through a specific `(object_type,
object_uuid)`; the consumer is looking at one object's
stream.

The REST response keeps the existing JSON list-of-dicts
shape:

```json
[
  {
    "event_uuid": "550e8400-...",
    "event_type": "audit",
    "timestamp": 1748736000.123,
    "fqdn": "node-01.example.com",
    "duration": 0.12,
    "message": "Created instance",
    "extra": {"foo": 1},
    "request_id": "req-abc"
  },
  ...
]
```

Only two differences from today's shape:
- `correlation_id` (today) → `event_uuid` (post-phase-4).
  Per master plan decision 11 and confirmed by the
  survey that nothing reads `correlation_id` (the
  client-python library treats the dict opaquely; no
  internal callers reference the key).
- `request_id` is a new top-level field. Today it lives
  inside `extra['request-id']` for events originating
  from the API; post-phase-4 it's a first-class field
  on every event row. Old events written before phase 2a
  shipped have `request_id = null`.

## SQL design

```sql
SELECT
  e.event_uuid,
  e.event_type,
  e.timestamp,
  e.fqdn,
  e.duration,
  e.message,
  e.extra,
  e.request_id
FROM event_objects eo
JOIN events e ON eo.event_uuid = e.event_uuid
WHERE eo.object_type = :object_type
  AND eo.object_uuid = :object_uuid
  AND (:event_type_filter = '' OR e.event_type = :event_type_filter)
ORDER BY e.timestamp DESC
LIMIT :limit;
```

Index usage:
- Drives off the `event_objects` PK prefix
  `(object_type, object_uuid)`.
- Joins to `events` via the `events.event_uuid` PK.
- Filters and sorts use the `events` row directly.

The query plan should be: index range scan on
`event_objects` for the object's references, primary-key
lookup on `events` for each, filter and limit. Bounded by
LIMIT so the worst case is `O(limit * log(N))`.

### Limit hardening

Server-side caps for limit:
- `limit <= 0` → use default 100.
- `limit > 1000` → cap at 1000.

The current REST API allows negative limit which
`eventlog.EventLog.read_events()` interprets as "all
rows." Phase 4 removes that foot-gun without changing
the well-behaved path. Cursor-style pagination for
genuinely-large queries is filed under Future work in
the master plan.

## Delete RPC for hard_delete cleanup

```proto
message DeleteObjectEventsRequest {
  ObjectType object_type = 1;
  string object_uuid = 2;
}

rpc DeleteObjectEvents (DeleteObjectEventsRequest)
    returns (StatusReply) {}
```

`_direct_delete_object_events(object_type, object_uuid)`:

```sql
DELETE FROM event_objects
WHERE object_type = :object_type
  AND object_uuid = :object_uuid;
```

Drives off the `event_objects` PK prefix.

Notes on semantics:

- Only event_objects rows are deleted directly. The
  `events` row stays alive if other objects still
  reference it (e.g. an audit event tied to both an
  instance and a network — hard-deleting the instance
  drops the (instance, event_uuid) row but the
  (network, event_uuid) row keeps the event reachable
  from the network's stream).
- If hard-deleting drops the last reference, the
  `events` row becomes an orphan. The daily cluster
  prune's stage C sweeps it up. Worst-case lag is one
  prune cycle (24h); orphan rows take negligible space
  and aren't reachable from any object's stream during
  the lag, so this is acceptable.
- No row-count returned from the public function. The
  hard_delete caller doesn't care.

## hard_delete wiring

In `shakenfist/baseobject.py`,
`DatabaseBackedObject.hard_delete` currently cleans up
`object_states` and `object_metadata`. Phase 4 adds:

```python
mariadb.delete_object_events(self.object_type, self.uuid)
```

Placed alongside the existing
`mariadb.delete_state` / `mariadb.delete_object_metadata`
calls. The `DatabaseBackedObjectWithOperations`
subclass's `hard_delete` calls `super().hard_delete()`
and so picks up the new cleanup automatically.

Master plan checklist item: **"Object cleanup
(hard_delete) accounts for event_objects rows owned by a
deleted object — either cascades, or follows the
deliberate retention semantics the project already has
for object history."** Decision: cascades (the new
`delete_object_events` call from hard_delete), with
events surviving if other objects still reference them.
The "deliberate retention" alternative — keeping the
deleted-object's events around for forensic purposes —
is rejected because the REST endpoint that would expose
them is keyed by `(object_type, object_uuid)` of a now-
deleted object; nothing can reach those events.

## REST endpoint cut-over

For each of the five endpoint files
(`external_api/{instance,artifact,network,node,blob}.py`),
the change is mechanical:

```python
# Old (sqlite via EventLog):
eventdb = eventlog.EventLog(object_type, object_uuid)
return list(eventdb.read_events(limit=limit,
                                event_type=event_type))

# New (MariaDB via mariadb.get_object_events):
return [
    row.model_dump(mode='json')
    for row in mariadb.get_object_events(
        object_type, object_uuid,
        limit=limit, event_type=event_type)
]
```

`row.model_dump(mode='json')` produces a dict matching
the REST response shape, with `Optional[None]` fields
rendered as JSON `null`. The `extra` field round-trips
through `extra_json` on the proto path; the public
function re-parses the JSON string into a dict before
returning so the REST handler stays clean.

Auth decorators on each endpoint stay exactly as they
are today. The survey confirmed the auth posture varies
by endpoint (instance/network/artifact = namespace
ownership; node = admin-only; blob = token-verify only).
Phase 4 changes the storage backend, not the
authorization model.

The five endpoint files all import `eventlog` today.
Post-phase-4 they import `mariadb` instead. The
`eventlog` module itself stays around (the
`add_event*` write-side API is unchanged); only the
read-side `EventLog` class instantiation moves.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | Add `EventReadRow` pydantic model to `shakenfist/schema/event.py` per the "Read RPC and response shape" section. Add `_direct_get_object_events` and `_direct_delete_object_events` to `shakenfist/mariadb.py`, placed adjacent to the existing `_direct_record_event_batch` / `_direct_prune_*` block (grep to find). `_direct_get_object_events` runs the SELECT-JOIN-LIMIT query from the "SQL design" section, applies the limit hardening (clamp `limit <= 0` to 100, cap at 1000), parses each row's JSON `extra` back to a dict, and returns `list[EventReadRow]`. `_direct_delete_object_events` runs the simple DELETE query (no batching needed — it's bounded by one object's event count). Both use `sa.text(...)` with bound parameters per the phase 3 pattern, and try/except OperationalError logging a warning and returning empty list / silent no-op respectively. No proto, no RPC, no gRPC wrapper, no public dispatcher in this commit. Commit message subject: "mariadb: per-object events read and delete helpers." |
| 4b | high | opus | none | Add the `GetObjectEvents` and `DeleteObjectEvents` RPCs end to end. Combined commit per the established pattern. `protos/database.proto`: add `EventReadRow`, `GetObjectEventsRequest`, `GetObjectEventsReply`, `DeleteObjectEventsRequest` messages plus the two `rpc` lines. Run `tox -e genprotos`. `shakenfist/daemons/database/main.py`: add handlers mirroring `RecordEventBatch` and `PruneEvents`, register `'get_object_events'` and `'delete_object_events'` in the Monitor operations list. `shakenfist/mariadb.py`: add `_grpc_get_object_events`, `_grpc_delete_object_events`, public `get_object_events`, public `delete_object_events`. The get RPC marshals `EventReadRow` lists back through `extra_json` JSON-string serialisation (proto can't carry arbitrary dicts cleanly); on the reply side the gRPC wrapper re-parses `extra_json` into a dict before returning to the caller. The delete RPC returns `StatusReply`. Run `pre-commit run --all-files` — must be green. Commit message subject: "database: GetObjectEvents and DeleteObjectEvents RPCs." |
| 4c | high | opus | none | Cut over the five REST endpoint files (`shakenfist/external_api/{instance,artifact,network,node,blob}.py`) per the "REST endpoint cut-over" section. Each file: remove the `eventlog.EventLog(...)` instantiation, replace with `mariadb.get_object_events(...)`, render the response via `row.model_dump(mode='json') for row in ...`. Preserve the exact auth decorator stack on each endpoint (instance/network/artifact use namespace-ownership decorators; node uses `@caller_is_admin`; blob uses `@verify_token` only). Do NOT touch the auth model. Wire `mariadb.delete_object_events(self.object_type, self.uuid)` into `shakenfist/baseobject.py:DatabaseBackedObject.hard_delete`, placed adjacent to the existing `mariadb.delete_state` / `mariadb.delete_object_metadata` calls. The `DatabaseBackedObjectWithOperations.hard_delete` override calls `super().hard_delete()` and inherits the new cleanup. Run `pre-commit run --all-files`. Commit message subject: "events: REST endpoints query MariaDB; hard_delete cleans up." |
| 4d | medium | sonnet | none | Tests in `shakenfist/tests/test_events_storage.py` (extending the existing file). Cover: (i) `_direct_get_object_events` SQL shape — assert the JOIN, the WHERE clauses for `object_type` / `object_uuid` / optional `event_type`, the `ORDER BY timestamp DESC`, the `LIMIT`; (ii) limit hardening — `limit=0` → 100, `limit=-1` → 100, `limit=5000` → 1000; (iii) result rows parsed correctly — `extra` JSON string → dict, `duration=NULL` → None, `request_id=NULL` → None; (iv) empty result returns []; (v) `_direct_delete_object_events` runs the right DELETE; (vi) public `get_object_events` router (direct-vs-gRPC) — mirror the existing `RecordEventBatchRoutingTestCase`; (vii) hard_delete integration — patch the `mariadb.delete_*` functions and call `DatabaseBackedObject.hard_delete` on a fake object, assert `mariadb.delete_object_events` is called with the right (object_type, uuid). Add one REST endpoint smoke test in an appropriate test file (find one for `external_api/instance.py` event endpoint, e.g. `test_instance_endpoints.py` if it exists) showing the new code path: mock `mariadb.get_object_events` to return two `EventReadRow` instances, hit the endpoint, assert the JSON response matches. Run `tox -e py3` and `pre-commit run --all-files`. Commit message subject: "tests: phase 4 read path and hard_delete coverage." |

Ordering: 4a → 4b → 4c → 4d. Each step depends on the
previous. Per the established pattern, run
`pre-commit run --all-files` between every step; if a
step trips mypy because of cross-file issues, combine
with the previous step rather than ship a broken
intermediate.

## Risks and mitigations

- **Risk:** REST clients rely on `correlation_id` in the
  response dict.
  **Mitigation:** Survey confirmed no internal callers
  and no structural client-side expectation. The rename
  is announced in phase 6 release notes. If a wild
  client breaks, the rename is reversible by adding a
  one-line compatibility alias at the REST response
  layer; defer that decision until evidence appears.

- **Risk:** A limit-cap regression breaks existing
  operator tooling that asks for `limit=10000` and
  expects to get them.
  **Mitigation:** 1000 is generous compared to the
  default 100; nothing the operator can do
  interactively is bounded by it. Phase 6 release notes
  call out the new cap. If a real workflow needs more,
  cursor pagination (Future work) is the right answer,
  not lifting the cap.

- **Risk:** `hard_delete` cleanup interaction —
  hard_delete is called from many places; introducing a
  new mariadb call could surface latent ordering bugs.
  **Mitigation:** `delete_object_events` is a single
  DELETE bounded by the object's event count. It runs
  alongside the existing `delete_state` and
  `delete_object_metadata` calls; if either of those
  works, so does this one. Phase 4d test (vii) covers
  the integration.

- **Risk:** The `extra_json` round-trip through proto
  loses information for events with non-JSON-safe
  `extra` payloads (e.g. binary blobs).
  **Mitigation:** The write-side spool already JSON-
  encodes `extra` via `util_json.json_dump`, so any
  payload that landed in the spool round-trips cleanly.
  Phase 4 doesn't introduce new encoding paths.

- **Risk:** Pre-cutover sqlite events become invisible
  immediately on phase 4 deploy, before phase 5
  (history-loss point).
  **Mitigation:** Documented in master plan decision 5
  and called out in phase 6 release notes. The single
  operating deployment has signed off on this.

## Definition of done

- [ ] Five REST endpoints (`/instance/<u>/events`,
      `/artifact/<u>/events`, `/network/<u>/events`,
      `/node/<n>/events`, `/blob/<u>/events`) return
      events from MariaDB.
- [ ] Existing auth decorator stacks on each endpoint
      are unchanged.
- [ ] Response shape preserved except
      `correlation_id` → `event_uuid` and the new
      `request_id` top-level field.
- [ ] hard-deleting an instance (or any
      DatabaseBackedObject) removes its event_objects
      rows; the events row stays alive if other objects
      still reference it; orphans are caught by the
      next daily prune.
- [ ] `pre-commit run --all-files` is clean.
- [ ] Each commit is self-contained; commit messages
      follow project conventions including the Prompt
      paragraph and Co-Authored-By line with model and
      effort.

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the management session on its
understanding of the brief and the surrounding context.
