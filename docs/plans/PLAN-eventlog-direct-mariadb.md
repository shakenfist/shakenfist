# Remove the eventlog service and write events directly to MariaDB

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
the eventlog write API (`shakenfist/eventlog.py`), the local
spool (`shakenfist/eventlog_spool.py`) and its batched drainer
(`shakenfist/eventlog_drainer.py`), the eventlog daemon
(`shakenfist/daemons/eventlog/main.py`), the REST API read
sites that consume events
(`shakenfist/external_api/{instance,artifact,network,node,
blob}.py`), the MariaDB DLQ wiring (`mariadb.enqueue_event_dlq`
/ `drain_event_dlq` / `delete_event_dlq` plus
`_get_event_dlq_table`, `_ensure_event_dlq_schema`, and the
`event_dlq` entry in `DATA_MIGRATIONS`), and the cluster
daemon's existing periodic-maintenance loop
(`shakenfist/daemons/cluster/main.py` plus its
`scheduled_tasks` module). Ground your answers in what the
code actually does today rather than guessing.

Where a question touches on external concepts (MariaDB /
InnoDB JSON column behaviour, JOIN ordering and indexing for
event-stream reads, retention-policy implementation patterns),
research as needed to give a confident answer. Flag any
uncertainty explicitly.

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture overview
and the event logging subsystem. Consult `CLAUDE.md` for build
commands, project conventions, the existing data-stored-in-
MariaDB pattern, the systemd service ordering, and the
preserve-event-logging priority that constrains how aggressive
the cut-over can be.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named for the master plan with
`-phase-NN-descriptive` appended before the `.md` extension.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit.

## Situation

The `sf-eventlog` daemon today is a thin gRPC wrapper in
front of per-object sharded sqlite storage. Calling sites use
the in-process abstraction `eventlog.add_event(...)` /
`eventlog.add_event_multi(...)` (`shakenfist/eventlog.py`
lines 111-124 and 224-355), which either flushes through the
local spool drainer or sends a `RecordMultiEvent` /
`RecordMultiEventBatch` gRPC call to the daemon. The daemon
(`shakenfist/daemons/eventlog/main.py` lines 77-218) writes
each row into a sqlite file per
`(object_type, object_uuid, year-month)` chunk on the
eventlog node's local filesystem. All five REST API event-
list endpoints (`external_api/instance.py:1104`,
`artifact.py:556`, `network.py:408`, `node.py:179`,
`blob.py:285`) instantiate `eventlog.EventLog(...)` and call
`.read_events()` **directly against the sqlite chunks**,
which requires sf-api to be on the same node as the eventlog
storage. There is no abstraction layer in front of the read
path today.

The write path already has a foot in MariaDB. When the gRPC
call fails or is suppressed (the `EVENTLOG_SUPPRESS_GRPC`
config flag or the `set_force_event_dlq(True)` thread-local
used at daemon startup), events fall through to
`mariadb.enqueue_event_dlq` (defined in `shakenfist/mariadb.py`
lines ~1870-1892, table at lines 1272-1303), queueing into a
MariaDB DLQ table that the eventlog daemon then drains *back
out* (`daemons/eventlog/main.py` lines 297-332) and writes
into sqlite. So under bad-weather conditions the path is
**caller → MariaDB DLQ → sf-eventlog → sqlite**, and under
good conditions it is **caller → gRPC → sf-eventlog →
sqlite**. Both terminate at the same place. The DLQ exists
primarily to solve circular startup dependencies (the
eventlog daemon itself cannot record its own startup event
through gRPC) and gRPC unavailability, not because MariaDB is
unsuitable for the storage.

The **local spool** has already landed in this tree
(`shakenfist/eventlog_spool.py`,
`shakenfist/eventlog_drainer.py`) ahead of the rest of this
plan. Profiling identified the per-event synchronous gRPC as
the largest remaining contributor to dispatch-time wrapper
overhead (~200 ms each under bursty load, multiple events
per cluster operation). The spool moves caller-side cost
down to a sub-millisecond local sqlite insert, with a
background drainer thread batching events into the existing
`RecordMultiEventBatch` RPC. The spool is per-daemon
(`/srv/shakenfist/spool/eventlog/<daemon>-<pid>.db`) and
survives process crashes; an orphan-spool sweep on daemon
startup drains files left behind by previously-dead PIDs.
The high-water mark is 100,000 rows (~50 MiB); excess is
dropped with a counter rather than blocked. The caller-
facing `eventlog.add_event*` API is unchanged.

With the local spool in place the bootstrapping case that
originally motivated the DLQ is solved cleanly: events
generated during sf-database's own startup land in the spool
and drain as soon as the channel is up. The
`EVENTLOG_SUPPRESS_GRPC` / `set_force_event_dlq` paths and
the `event_dlq` table are still wired today as a belt-and-
braces fallback; the decisions section below confirms they
can be removed alongside the rest of the eventlog plumbing.

The sqlite storage model is **denormalised at write time**:
an event touching N objects writes N rows total — one per
object — each carrying the full `message`, `extra`, and a
`correlation_id` string used to stitch the multi-object
event back together at read time. One quirk worth flagging:
`correlation_id` is generated in `eventlog.py` (lines
259-260), passed to the gRPC `_add_event_dlq_inner` path,
and stored in the sqlite chunk (chunk schema VERSION 8 at
`daemons/eventlog/main.py:631-639`, indexed at line 872),
but it is **not present on the `EventMultiRequest` proto
message itself**. The daemon's `_record_with_dlq` reattaches
it server-side. With the move to MariaDB this can become a
clean two-table normal form (`events` + `event_objects`)
where message/extra are stored once per event regardless of
object count, and `event_uuid` does the job today's
`correlation_id` does — making the proto / wire-format
mismatch disappear.

Given all of the above, sf-eventlog has stopped earning its
keep:

- It is a singleton tied to local sqlite storage on one host.
- Its existence forces sf-api to be on the same node, or
  proxy reads back to wherever the sqlite lives. That
  proxying is the kind of thing `PLAN-remove-primary` is
  trying to eliminate.
- The gRPC indirection adds latency and a failure mode that
  the DLQ already exists to paper over.
- The systemd unit ordering chain has another node in it.
- Its core function — "persist this structured event" — is
  something every other daemon already does directly against
  MariaDB via the `sf-database` service.

The proposal is to delete `sf-eventlog` entirely. The
in-process abstraction at calling sites is preserved; its
implementation changes from "send gRPC to sf-eventlog" to
"flush local spool batch via gRPC to sf-database, which
writes to MariaDB." Because the local-spool indirection
already landed, the change is **swap the drainer's gRPC
target from sf-eventlog to sf-database** rather than a full
rewrite of the caller path.

Pruning moves into the cluster daemon's existing periodic-
maintenance loop (`shakenfist/daemons/cluster/main.py`
alongside its `scheduled_tasks` module), running on a 24-
hour cadence to mirror today's "collect targets once per
day, sweep" structure. The REST API read path stops opening
sqlite files and starts running parameterised SELECTs
through new sf-database RPCs — which means sf-api can serve
event reads from any node, and the local-filesystem
coupling vanishes.

## Mission and problem statement

`sf-eventlog` is removed. Events are written directly into
MariaDB via the same `sf-database` channel everything else
uses; pruning runs in the cluster daemon's periodic-
maintenance loop; REST API event reads become parameterised
SELECTs routed through sf-database. The calling-site
abstraction (`eventlog.add_event(...)` /
`add_event_multi(...)`) is preserved unchanged.

Concretely, after this plan lands:

- Two new MariaDB tables exist:
  - `events(event_uuid CHAR(36) PK, event_type VARCHAR(32),
    timestamp DOUBLE, fqdn VARCHAR(255), duration DOUBLE
    NULL, message TEXT, extra JSON NULL, request_id
    VARCHAR(64) NULL)` with indexes on `(event_type,
    timestamp)` and `(request_id)`.
  - `event_objects(object_type VARCHAR(32), object_uuid
    VARCHAR(36), event_uuid CHAR(36),
    PRIMARY KEY (object_type, object_uuid, event_uuid))`
    with a secondary index on `(event_uuid)` for the prune
    join.
  - The per-object stream read uses the composite PK
    (`object_type, object_uuid` prefix) followed by a join
    to `events` for `ORDER BY timestamp DESC LIMIT N`.
- The existing local sqlite spool drainer
  (`shakenfist/eventlog_drainer.py`) flushes batched events
  through `sf-database` into the new MariaDB tables via a
  new `RecordEventBatch` RPC on `protos/database.proto`.
  The caller-facing `eventlog.add_event*` API does not
  change; only the drainer's gRPC target changes, and the
  batch RPC's payload picks up a first-class
  `correlation_id`/`event_uuid` field.
- The REST API event-list endpoints
  (`external_api/{instance,artifact,network,node,blob}.py`)
  call a new `GetObjectEvents(object_type, object_uuid,
  limit, event_type)` RPC on `sf-database` instead of
  opening sqlite directly. The returned data shape and
  `limit` semantics match the current sqlite-backed path so
  existing clients see no behaviour change.
- The cluster daemon runs the per-event-type prune sweep
  daily, honouring the existing eight `MAX_{TYPE}_EVENT_AGE`
  configs (`shakenfist/config.py` lines 365-394). Prune
  semantics treat an `events` row as deletable only once
  its last `event_objects` row has been pruned, so an event
  still visible on object Y is not removed just because
  object X's retention window dropped its reference.
- Historic sqlite event data is **not migrated**. The sole
  operating deployment of Shaken Fist does not need the
  legacy history preserved, and a one-shot or staged
  migration tool is meaningful complexity to build, test,
  and document for zero operator benefit. Phase 6 deletes
  the on-disk sqlite chunks as part of removing the daemon;
  the cut-over story documented in
  `docs/operator_guide/eventlog.md` is explicit that pre-
  cutover events are lost.
- The `sf-eventlog` systemd unit, daemon code, gRPC protos
  (`protos/event.proto`), and related config
  (`EVENTLOG_NODE_IP`, `EVENTLOG_API_PORT`,
  `EVENTLOG_METRICS_PORT`, `EVENTLOG_SUPPRESS_GRPC`) are
  removed.
- `sf-database` exposes a Prometheus gauge for the row
  count of the `events` table (and a counter for inserts
  and prune-deletes) on its existing metrics endpoint, so
  the cluster's total stored-event volume is visible at a
  glance. Each daemon's local spool exposes its current
  depth (rows pending in
  `/srv/shakenfist/spool/eventlog/<daemon>-<pid>.db`) and
  the existing "dropped at high-water mark" counter on the
  daemon's own metrics endpoint, so spool backpressure or
  drainer stalls show up before they cascade.
- The MariaDB event DLQ table (`event_dlq`),
  `_get_event_dlq_table`, `_ensure_event_dlq_schema`, all
  six `*_event_dlq` accessors (direct/gRPC/public), and the
  `event_dlq` entry in `DATA_MIGRATIONS` are removed. The
  legacy etcd→DLQ migration (`_migrate_etcd_event_dlq` at
  `mariadb.py:4645`) is preserved for one release cycle so
  clusters upgrading through this version still drain
  leftover etcd keys; it is removed in the following
  release.

The principle is: **the local spool is the durability
boundary on the caller side; the existing `sf-database`
channel is the right write path on the cluster side; the
rest is removing indirection.**

## Decisions

The placeholder plan listed twelve open questions. The
codebase survey behind this revision (notably: confirming
the spool is in tree, confirming the REST API reads sqlite
directly with no abstraction, and confirming the existing
table/schema/migration patterns) resolves all of them. The
decisions are recorded here so the phase plans do not have
to re-litigate them.

1. **Schema shape.** Two tables, `events` and
   `event_objects`, as detailed in the Mission section.
   `extra` is **JSON** rather than TEXT — Shaken Fist
   already uses JSON columns extensively
   (`instance_attributes.block_devices`,
   `agent_operation_attributes.results`,
   `node_metrics.metrics_json`), querability is worth
   having for future audit views, and InnoDB JSON storage
   is not meaningfully more expensive than TEXT for the
   sizes we are writing. No content-hash dedup table —
   event payloads do not have blob-like dedup ratios.

2. **DLQ removal.** Removed in full in phase 5. The spool
   is the durability boundary; the bootstrap chicken-and-
   egg is fully covered (events generated during
   sf-database startup sit in the spool until the
   drainer's channel comes up). The `event_dlq` table,
   accessors, DATA_MIGRATIONS entry, and the
   `_migrate_etcd_event_dlq` legacy etcd-drain migration
   all go in phase 5. The single operating deployment
   upgrades all nodes together in a coordinated outage,
   so the "preserve the migration for one release" hedge
   that an earlier draft of this decision had is not
   needed.

3. **Prune cadence.** Daily, run from the cluster daemon's
   periodic-maintenance loop (the `scheduled_tasks` module
   pattern), matching today's "collect once per 24h" cycle
   on `daemons/eventlog/main.py:336-346`. The per-type
   prune query is `DELETE eo FROM event_objects eo JOIN
   events e ON eo.event_uuid = e.event_uuid WHERE
   e.event_type = ? AND e.timestamp < ?` followed by
   `DELETE e FROM events e LEFT JOIN event_objects eo ON
   e.event_uuid = eo.event_uuid WHERE eo.event_uuid IS
   NULL`. Both are bounded by `LIMIT 10000` per pass and
   looped until they return zero rows, matching the
   eventlog daemon's existing batched-sweep posture. Phase
   3 baselines on a populated dev DB; if a per-pass
   delete-with-limit-and-loop is too slow, the fallback is
   a paged `event_uuid` cursor, which the indexing
   supports cleanly.

4. **Prune semantics for multi-object events.** Confirmed:
   delete `event_objects` rows whose event is older than
   that event_type's max age, then delete `events` rows
   that have no remaining `event_objects` rows. An event
   with N objects is fully removed only when *all* objects
   have aged it out. Documented in the phase 3 plan and in
   `docs/operator_guide/database.md`.

5. **Historic sqlite migration.** Option (c) accept the
   loss. The sole operating deployment does not need the
   legacy history preserved; building, testing, and
   documenting an idempotent migration tool is real cost
   for zero benefit. The cut-over sequence is: deploy
   phase 2 (writes go to MariaDB), then deploy phase 4
   (reads from MariaDB and stop returning sqlite history),
   then phase 6 deletes the sqlite chunks alongside the
   daemon. Between phase 2 and phase 4, new events are
   visible in MariaDB and old events remain visible from
   sqlite — the read path is single-store at every point,
   but old events become invisible the instant phase 4
   ships. The operator-visible loss is called out
   prominently in the release notes and in
   `docs/operator_guide/eventlog.md`.

6. **Write throughput / load on sf-database.** Non-issue
   given current numbers, but verify with a one-off
   benchmark in phase 1 once the schema lands. The spool
   already batches 50-100 events per RPC; sf-database
   already handles per-second `set_state` writes for every
   instance and node; per-event amortisation is favourable.
   If the benchmark surprises us, the fallback is a per-
   type write-sharding strategy via a second RPC route,
   added in phase 2; design is sketched but not committed.

7. **Calling-site abstraction signature.** Both
   `add_event` and `add_event_multi` are preserved.
   `add_event` is already a thin wrapper over
   `add_event_multi` (`eventlog.py:111-124` calls into the
   multi path). No signature changes; no caller has to
   adjust.

8. **Per-object event ordering and pagination.** The read
   query is `SELECT e.event_uuid, e.event_type,
   e.timestamp, e.fqdn, e.duration, e.message, e.extra,
   e.request_id FROM event_objects eo JOIN events e ON
   eo.event_uuid = e.event_uuid WHERE eo.object_type = ?
   AND eo.object_uuid = ? [AND e.event_type = ?] ORDER BY
   e.timestamp DESC LIMIT ?`. Drives off the
   `event_objects` PK prefix, joins by `event_uuid`
   (covered by the secondary index in phase 1's schema),
   and limits in the events table. EXPLAIN-validated in
   phase 4 against a populated test DB. Cursor-style
   pagination is **out of scope** here — phase 4 keeps
   the existing `limit`-only API for compatibility, and a
   cursor parameter is filed under Future work.

9. **Read-path consistency during migration.** Moot under
   decision 5: there is no migration, the read path stays
   single-store at every point, and the only operator-
   visible discontinuity is "historic events disappear
   when phase 4 ships." No dual-store read code is needed.

10. **`request_id` as a first-class column.** Yes,
    promoted out of `extra` into its own `events.request_id
    VARCHAR(64) NULL` column with a `(request_id)` index.
    Justification: it is the cleanest way to support
    request-scoped audit views, aligns with the eventual
    OpenTelemetry trace-id direction (which lands as a
    sibling column when OTel work begins), and matches the
    project's preference for pushing filterable fields
    down to SQL. The write path in
    `external_api/base.py` already populates `extra`
    with `request_id`; phase 2 lifts it out of `extra`
    into a dedicated field on the spool payload and the
    new RPC. Phase 4's read path returns `request_id`
    inline so existing clients still see it.

11. **Removal of `correlation_id`.** Removed in favour of
    `event_uuid`. The calling-site signatures do not
    expose `correlation_id` (it is generated internally at
    `eventlog.py:259-260`), and the daemon-side proto
    doesn't carry it (it is reattached in
    `_record_with_dlq`), so the removal is internal-only.
    The new spool payload and `RecordEventBatch` RPC
    carry `event_uuid` as a first-class field, and the
    REST read returns `event_uuid` in place of
    `correlation_id` in the response dict. Phase 4 covers
    the rename in the response shape; existing clients
    that introspect `correlation_id` get `event_uuid`
    instead (functionally equivalent string UUID).

12. **Event-write failure handling.** Keep "drop with
    counter" at the spool high-water mark, as already
    implemented in `eventlog_spool.py`. The block-vs-drop
    trade-off is documented in
    `docs/operator_guide/eventlog.md` (new file in phase
    7) so the operator-visible choice is explicit, not
    inherited by accident. The drop counter is wired into
    the metrics work in decision 13.

13. **Volume metrics.** Both ends of the pipeline expose
    Prometheus metrics so the operator can spot growth or
    backpressure before it becomes an incident:

    - `sf-database` exposes a gauge for the row count of
      the `events` table, plus counters for inserts and
      prune-deletes. The gauge is sampled rather than
      kept perfectly live (a per-insert decrement-counter
      style is too chatty); a periodic refresh inside the
      database daemon's existing metrics loop is
      sufficient. Phase 1 lands the gauge alongside the
      schema; phase 2 wires the insert counter; phase 3
      wires the prune counter.
    - Each daemon's local spool exposes its current depth
      (rows pending in
      `/srv/shakenfist/spool/eventlog/<daemon>-<pid>.db`)
      as a gauge, plus the existing "dropped at high-
      water mark" counter, on the daemon's own metrics
      endpoint. The depth gauge is sampled cheaply via a
      `SELECT COUNT(*)` against the spool sqlite on each
      metrics scrape — small enough that a `count(*)` on
      the spool is genuinely cheap. Phase 2 wires both up
      to the spool/drainer modules that already exist.

    Justification: removing sf-eventlog removes the
    obvious "look at the eventlog daemon's metrics" page
    operators currently lean on. Without explicit
    metrics on the new path, the only visible signal of
    a stuck drainer or runaway table growth would be
    disk fill, which is too late. The split (cluster-
    side gauge on sf-database, caller-side gauge per
    daemon) matches where the data actually lives.

## Execution

Phase 0 (research and decisions) from the placeholder is
folded into this revision; the table below reflects the
post-decisions plan.

| Phase | Plan | Status |
|-------|------|--------|
| -1. Local sqlite spool + batched-RPC drainer (caller side) | _(delivered in the network-facade branch ahead of this plan)_ | Complete |
| 1. `events`/`event_objects` schema, accessors, the `RecordEventBatch` RPC on sf-database, and the events-row-count gauge | PLAN-eventlog-direct-mariadb-phase-01-schema.md | Complete |
| 2. Swap the drainer's RPC target from sf-eventlog to sf-database; promote `event_uuid` and `request_id` to first-class fields; wire spool-depth, drop, and insert metrics | PLAN-eventlog-direct-mariadb-phase-02-write.md | Complete |
| 3. Move prune sweep into the cluster daemon's scheduled tasks, with prune-delete counter | PLAN-eventlog-direct-mariadb-phase-03-prune.md | Complete |
| 4. REST API direct-read path via a new `GetObjectEvents` RPC on sf-database | PLAN-eventlog-direct-mariadb-phase-04-read.md | Complete |
| 5. Delete `sf-eventlog` daemon, gRPC protos, systemd unit, config, the MariaDB `event_dlq`, and the on-disk sqlite chunks | PLAN-eventlog-direct-mariadb-phase-05-remove.md | Complete |
| 6. Documentation (operator guide for the new eventlog, ARCHITECTURE/README/AGENTS updates, cut-over loss called out in release notes) | PLAN-eventlog-direct-mariadb-phase-06-docs.md | Complete |

Sequencing constraints between phases:

- Phase 1 must land before phase 2 (the drainer needs
  somewhere to write).
- Phase 2 must land before phase 3 (no point pruning a
  table that nothing writes to yet — and the prune query
  needs the indexes phase 1 introduces).
- Phase 4 should land soon after phase 2 to minimise the
  window in which new events are in MariaDB and old
  events are still in sqlite. The two stores coexist
  cleanly during that window (writes go to MariaDB,
  reads still go to sqlite for everything old plus a
  growing-empty MariaDB), but the longer the window the
  more confusing the operator-visible state.
- Phase 5 must land last among the code phases. It
  deletes the daemon, the legacy DLQ table, and the
  sqlite chunks; it is unsafe to ship before phases 2, 3,
  and 4 have stabilised in the operator cluster.
- Phase 6 can run in parallel with any other phase but
  should be re-checked at the end for accuracy.

## Dependencies on other plans

- **No hard dependency on `PLAN-remove-primary`.** This plan
  and remove-primary are mutually reinforcing — this plan
  removes one of the reasons sf-api wants to be co-located
  with eventlog storage today (the direct sqlite read path),
  and remove-primary's BYO-LB story is operationally
  cleaner once sf-api on any node can serve event reads —
  but neither blocks the other.
- **`PLAN-remove-etcd` should land first.** Mostly to keep
  this plan off the etcd codepath entirely. The eventlog
  write path doesn't touch etcd directly today, but the
  `_migrate_etcd_event_dlq` retention decision in phase 6
  is easier to make against a post-etcd-retirement
  codebase.
- **OpenTelemetry instrumentation (not yet drafted) would
  inform phase 2's load benchmark.** If OTel lands first,
  use it to baseline. If not, phase 2 produces the
  baseline as a one-off.
- **The existing `sf-database` election work in
  `PLAN-remove-primary` phase 5 is helpful but not
  blocking.** Even with sf-database still hosted on one
  machine, the routing change "events go through
  sf-database" is correct and complete on day one.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session,
   producing the per-phase plan file before any code is
   written.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the phase plan, at the recommended
   effort level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or the
   model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

Use `isolation: "worktree"` for sub-agents on phase 5
(daemon, DLQ, and sqlite-chunk deletion) because the
on-disk and table-drop steps are irreversible by sub-
agent and benefit from a discardable worktree if the
output is unsatisfactory. Phases 1-4 and 6 can work
directly in the main tree unless the management session
has a reason to be cautious on a specific step.

### Planning effort

This master plan is **high effort** — schema design,
cross-daemon coordination, and the historic-migration
correctness questions all require careful reasoning.
Per-phase planning effort:

- Phase 1 (schema + row-count gauge): **high effort,
  opus**. The schema shape determines query performance
  for the lifetime of the cluster.
- Phase 2 (write cut-over, spool/drop/insert metrics):
  **high effort, opus**. The drainer-to-sf-database swap
  is the highest-blast-radius step; it changes the
  destination of every event in the system.
- Phase 3 (prune + prune-delete counter): **medium effort,
  opus**. The prune query is well-defined but multi-
  object semantics need careful test coverage.
- Phase 4 (read cut-over): **high effort, opus**. The
  REST contract must not change for clients (only the
  `correlation_id` → `event_uuid` field rename in the
  response dict).
- Phase 5 (delete daemon, DLQ, sqlite chunks): **high
  effort, opus**. Once shipped, rollback is a code
  revert.
- Phase 6 (docs): **medium effort, sonnet**. Mostly
  prose and index updates, with the read of the final
  state serving as a documentation-correctness check.

### Step-level guidance

Each phase plan should include a step table in the same
format as `PLAN-remove-primary.md`, with effort, model,
isolation, and brief columns. **When in doubt, skew to the
more capable model** — saving money only matters if the
outcome is still acceptable.

The brief is the load-bearing field. It should front-load
the research the planner already did (file paths, line
numbers, existing patterns to mirror), so the implementing
agent doesn't repeat it. For example, instead of "add the
events table", write "add `_get_events_table()` and
`_ensure_events_schema()` in `shakenfist/mariadb.py`
mirroring `_get_event_dlq_table` (lines 1272-1303) and
`_ensure_event_dlq_schema` (lines 1306-1327), then register
`_ensure_events_schema(engine)` in `ensure_schema()`
(lines 2042-2080)."

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

- [ ] The calling-site abstraction
      (`eventlog.add_event*`) is unchanged in signature.
      Daemon-side callers do not need per-call
      adjustments to follow the cut-over.
- [ ] Per-object event reads via REST return the same
      data shape (and the same `limit` semantics) as the
      sqlite-backed path did, so existing clients see no
      behaviour change. The only field rename is
      `correlation_id` → `event_uuid` in the response
      dict.
- [ ] Multi-object event normalisation is exercised by a
      test that creates an N-object event and reads it
      back from each object's stream, confirming the
      single underlying `events` row.
- [ ] The historic sqlite migration is exercised against
      a real sqlite chunk (not a stub) and is idempotent.
- [ ] Pruning of multi-object events does not delete the
      `events` row while any `event_objects` row still
      references it. Covered by a test that creates an N-
      object event, ages out N-1 objects, runs prune, and
      asserts the event is still visible from the
      remaining object.
- [ ] Object cleanup (`hard_delete()`) accounts for
      `event_objects` rows owned by a deleted object —
      either cascades, or follows the deliberate
      retention semantics the project already has for
      object history. The decision is documented in the
      phase 4 plan.
- [ ] mypy coverage for the new write/read paths is at
      least as good as today's eventlog module. Phase 1's
      schema accessors and phase 4's new RPC handler are
      the most important to type cleanly.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be true:

* The `sf-eventlog` daemon, its systemd unit, its gRPC
  protos (`protos/event.proto`), and its sqlite storage
  code (`shakenfist/daemons/eventlog/`) are removed from
  the tree.
* Calling-site code uses `eventlog.add_event*` exactly as
  it did before, with implementation routed through
  `sf-database` to the new MariaDB tables.
* REST API event-list endpoints return data directly from
  MariaDB via the new `GetObjectEvents` RPC, work on any
  sf-api node, and require no sqlite files on disk.
* Pruning runs in the cluster daemon's periodic-
  maintenance loop, honours the existing eight
  `MAX_{TYPE}_EVENT_AGE` configs, and correctly handles
  multi-object events.
* The MariaDB `event_dlq` table, all its accessors, the
  DATA_MIGRATIONS entry, and the
  `_migrate_etcd_event_dlq` legacy migration are all
  removed in phase 5. Nothing is preserved for
  upgrade-compatibility.
* Historic sqlite event data is **not** migrated; the
  operator-visible loss of pre-cutover history is
  documented in `docs/operator_guide/eventlog.md` and
  called out in the release notes.
* `sf-database` exposes a Prometheus gauge for the row
  count of `events` plus insert and prune-delete counters;
  every daemon exposes its local spool's depth and the
  existing high-water-mark drop counter on its own
  metrics endpoint.
* The `MAX_{TYPE}_EVENT_AGE` config keys keep working
  unchanged; eventlog-daemon-specific config keys
  (`EVENTLOG_NODE_IP`, `EVENTLOG_API_PORT`,
  `EVENTLOG_METRICS_PORT`, `EVENTLOG_SUPPRESS_GRPC`) are
  removed and any operator-facing renaming or deprecation
  is documented.
* Functional coverage under
  `deploy/shakenfist_ci/cluster_ci_tests` exercises the new write
  path end to end, including a multi-object event and a
  per-object read.
* New code follows existing patterns: MariaDB access via
  the three-layer pattern (direct/gRPC/public), filtering
  pushed down to SQL where indexes can make it faster,
  single quotes / 120-char lines / trailing-whitespace
  hygiene.
* `pre-commit run --all-files` passes (flake8, stestr unit
  tests, mypy).
* `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and
  `docs/operator_guide/database.md` are updated for the
  schema and daemon changes.

### Future work

- **Request-scoped audit views.** With `request_id` as a
  first-class column, a REST endpoint that returns all
  events for a given request becomes a clean SQL query and
  is genuinely useful for debugging multi-step API flows.
  Out of scope here.
- **OpenTelemetry alignment.** When the OTel work lands,
  the `events` table is a natural consumer of `trace_id` /
  `span_id` columns, giving cross-daemon trace context to
  every event. The schema here is friendly to that
  direction.
- **Per-namespace event quotas.** With events centralised
  in MariaDB, per-namespace counts and quotas become a
  clean query. Out of scope here.
- **Event compaction.** High-frequency events (resources,
  status) may eventually want time-window compaction
  ("collapse 60 identical heartbeats into one summary
  row"). Out of scope, but easier to add in MariaDB than
  it was in per-object sqlite.
- **Cursor-style pagination.** Phase 4 keeps the existing
  `limit`-only API for client compatibility. A follow-on
  can add cursor pagination once a client wants it.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When creating this master plan from the template, the
following files in `docs/plans/` should be updated:

* **`index.md`** — add one row to the *Master plans* table
  for this master plan, keyed to a one-line intent and its
  current status. Phases are tracked in the Execution
  table above, not in the index.
* **`order.yml`** — add an entry for this master plan so
  it appears in the documentation navigation. Phase files
  are *not* added to `order.yml`; they are linked from
  the Execution table and from `index.md` only.

The site navigation in `mkdocs.yml` is produced from
`mkdocs.yml.tmpl` by the docs-sync workflow, which
consumes `order.yml`. No manual `mkdocs.yml` edits are
needed.

When all phases are complete, update the status column in
`docs/plans/index.md`.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
