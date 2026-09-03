# Events

Shaken Fist records audit, status, mutation, and usage events for
every object in the cluster. Events are written from every daemon
via a local disk-backed spool, drained in batches to `sf-database`
via gRPC, and stored in two MariaDB tables (`events` and
`event_objects`). REST reads come from `sf-database` via the
`GetObjectEvents` RPC and can be served by any `sf-api` node.
The daily prune runs from the elected cluster maintainer.

## Write path

Each Shaken Fist daemon process holds its own sqlite spool file at:

```
/srv/shakenfist/spool/eventlog/<daemon>-<pid>.db
```

When code calls `eventlog.add_event()` or `eventlog.add_event_multi()`
the event is written synchronously to the local spool (a single
cheap sqlite insert, sub-millisecond) and the call returns. A
background drainer thread (`shakenfist.eventlog_drainer`) picks
batches of up to 100 rows off the spool every 100 ms and writes
them to MariaDB via `mariadb.record_event_batch`.

On the database daemon itself `record_event_batch` writes directly
to MariaDB. On every other daemon it routes through the database
gRPC interface to `sf-database`.

### Drainer cadence and backoff

The drainer runs at `DRAIN_POLL_INTERVAL = 0.1 s` (100 ms). When
the spool is empty it sleeps between polls. When the spool has
rows the drainer sends back-to-back batches of
`DRAIN_BATCH_SIZE = 100` rows until the spool drains, so a burst
of events is on the wire within a second or two under normal
conditions.

If `sf-database` refuses or times out, the batch is left in the
spool and retried on the next drain tick. The backoff schedule
(initial 0.5 s, doubling up to 30 s, resetting on any success)
prevents a downed database service from hammering the network.

### High-water mark

The spool is bounded by `SPOOL_HIGH_WATER_MARK = 100 000` rows
(roughly 50 MiB on disk). When the cap is reached, incoming
`enqueue()` calls drop the event silently rather than blocking
the caller. Each dropped event advances the
`eventlog_spool_dropped_total` counter. The
`eventlog_spool_depth` gauge shows how many rows are currently
pending.

Monitor `eventlog_spool_depth` proactively. A depth that climbs
toward 100 000 means `sf-database` has been unreachable for an
extended period or the cluster is producing events faster than
the drainer can flush.

### Orphan spool recovery

On startup the spool module scans for leftover spool files from
previously-dead PIDs. Their rows are migrated into the fresh-pid
spool so the drainer picks them up automatically. No operator
action is needed.

## Read path

Five REST endpoints return events for each object type:

| Endpoint | Object type |
|----------|-------------|
| `GET /instances/<ref>/events` | Instance |
| `GET /artifacts/<ref>/events` | Artifact |
| `GET /networks/<ref>/events` | Network |
| `GET /nodes/<node>/events` | Node |
| `GET /blobs/<uuid>/events` | Blob |

All endpoints accept an optional `limit` query parameter
(default 100). The server enforces a hard cap of 1000 rows
per request; a `limit` of 0 or any negative value is treated
as the default 100.

Events are returned newest-first. Each event in the response
includes at minimum:

| Field | Description |
|-------|-------------|
| `event_uuid` | Unique identifier for the event row |
| `request_id` | HTTP request ID of the API call that caused the event, if available; `null` otherwise |
| `timestamp` | Unix timestamp (float) when the event was recorded |
| `event_type` | String label for the event category |
| `message` | Human-readable description |
| `extra` | Dict of additional structured fields |
| `node` | Name of the node that emitted the event |

Note: the `event_uuid` field was named `correlation_id` in
releases before the events-on-MariaDB migration completed.
Clients that introspect either key need updating; clients that
pass through the response dict opaquely need no change.

## Retention

Event rows are pruned daily by `scheduled_tasks.prune_events`,
running under `ClusterLock` election on the cluster maintainer.
The daily cadence is anchored to a cluster-wide last-run stamp
(the `SCHEDULED_TASK_LAST_RUN_PRUNE_EVENTS` key in
`cluster_config`), so it survives both daemon restarts and the
maintenance lock changing hands -- a cluster whose daemons never
run for a full day still prunes daily.
The prune runs in three stages:

1. **Per-event-type sweep.** Removes `event_objects` rows older
   than the configured age for each event type. The `events` row
   is not yet deleted here.
2. **api-request object-type override sweep.** The `api-request`
   object type accumulates very verbose events. A second sweep
   removes `event_objects` rows associated with `api-request`
   objects that are older than `MAX_API_REQUEST_EVENT_AGE`,
   regardless of the event's own type. This truncates the API
   request history much more aggressively than the per-type caps.
3. **Orphan events sweep.** Removes any `events` rows that are
   no longer referenced by any `event_objects` row. This is the
   step that actually deletes the event content.

The whole sweep runs under a 25 minute wall-clock budget so
that its reply always reaches the cluster maintainer before the
RPC deadline, however large the tables have grown. A sweep
which exhausts the budget stops cleanly between batches and
logs that it did so; the orphan sweep persists its position
(the `EVENTS_PRUNE_ORPHAN_CURSOR` key in `cluster_config`) and
resumes from there at the next daily sweep, so an oversized day
delays completion rather than losing work.

### Retention configuration

Retention ages are in seconds. The defaults below are the
cluster-wide defaults; override any of them in `/etc/sf/config`
or as environment variables.

| Config key | Default | Description |
|------------|---------|-------------|
| `MAX_AUDIT_EVENT_AGE` | 7 776 000 (90 days) | Audit events |
| `MAX_MUTATE_EVENT_AGE` | 7 776 000 (90 days) | Mutation events |
| `MAX_STATUS_EVENT_AGE` | 604 800 (7 days) | Status events |
| `MAX_USAGE_EVENT_AGE` | 2 592 000 (30 days) | Usage events |
| `MAX_RESOURCES_EVENT_AGE` | 604 800 (7 days) | Resource events |
| `MAX_PRUNE_EVENT_AGE` | 2 592 000 (30 days) | Prune events |
| `MAX_HISTORIC_EVENT_AGE` | 7 776 000 (90 days) | Historic events |
| `MAX_API_REQUEST_EVENT_AGE` | 86 400 (1 day) | api-request object override |

### Multi-object retention semantics

A single `events` row can be referenced by `event_objects` rows
for multiple objects (for example, a network-interface event is
associated with both the interface and its parent network). The
`events` row survives until its *last* referencing
`event_objects` row is pruned. This means an event that carries
a long-retention type tied to a short-retention object remains
visible from the long-retention object's event stream until that
object's retention window closes.

## Object hard-delete cleanup

When a `DatabaseBackedObject` is hard-deleted, `hard_delete()`
calls `mariadb.delete_object_events`, which issues the
`DeleteObjectEvents` gRPC RPC. That RPC removes every
`event_objects` row for the deleted object's UUID. The `events`
row itself is not deleted immediately: if another object still
references it (because it was a multi-object event) it remains
visible from that other object's event stream. If no other object
references the `events` row it becomes an orphan, and the next
daily orphan sweep removes it.

## Metrics reference

The events subsystem exposes the following Prometheus metrics.
All sf-database metrics are scraped from `MARIADB_GATEWAY_METRICS_PORT`
(default 13006). Per-daemon metrics are scraped from each
daemon's own metrics port.

| Metric | Type | Source daemon | Description |
|--------|------|---------------|-------------|
| `database_events_rows` | Gauge | sf-database | Estimated row count of the `events` table (the statistics-based `information_schema` estimate, matching what mysqld-exporter reports), sampled roughly every 60 seconds. It can be off by tens of percent between statistics refreshes but tracks growth trends; use it to watch for unchecked growth if pruning stops. An exact `COUNT(*)` is deliberately not used: on a large events table the full scan churns the InnoDB buffer pool and can stall the daemon's watchdog loop (issue 3586). |
| `database_events_inserted_total` | Counter | sf-database | Events inserted, labelled by `event_type`. Watch the per-label rate for unexpected spikes. |
| `database_events_pruned_total` | Counter | sf-database | `event_objects` rows pruned per prune run, labelled by `event_type`. The synthetic label `event_type='api-request'` covers the object-type-override sweep. |
| `database_orphan_events_pruned_total` | Counter | sf-database | `events` rows removed by the orphan sweep. A non-zero rate here is normal after each daily prune. |
| `eventlog_spool_depth` | Gauge | every daemon | Rows currently pending in the local spool. Should be close to zero under normal conditions. |
| `eventlog_spool_dropped_total` | Counter | every daemon | Events dropped at the spool high-water mark. Any non-zero rate indicates that `sf-database` has been unreachable for an extended period. |

The `database_*_total{operation}` per-RPC counters on `sf-database`
also pick up the `RecordEventBatch`, `GetObjectEvents`, and
`DeleteObjectEvents` RPCs automatically via the shared counter
registration.

## Operator cleanup after upgrade

The events-on-MariaDB migration (Shaken Fist v0.8) retired the
`sf-eventlog` daemon and its on-disk sqlite event chunks. After
upgrading all nodes the directory:

```
/srv/shakenfist/events/
```

on the former eventlog node holds pre-cut-over sqlite chunks
that are no longer read by any daemon. It is safe to remove
them once the new code is running:

```bash
rm -rf /srv/shakenfist/events/
```

The new spool files live under `/srv/shakenfist/spool/eventlog/`
and are managed automatically by the spool module.

See [Database Architecture](database.md) for the broader
MariaDB schema, and `ARCHITECTURE.md` in the source repository for
the full daemon picture.
