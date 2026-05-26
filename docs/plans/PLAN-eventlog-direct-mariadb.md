# Remove the eventlog service and write events directly to MariaDB

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
the eventlog write API (`shakenfist/eventlog.py`), the
eventlog daemon (`shakenfist/daemons/eventlog/main.py`), the
REST API read sites that consume events
(`shakenfist/external_api/{instance,artifact,network,node,
blob}.py`), the MariaDB DLQ wiring (`mariadb.enqueue_event_dlq`
/ `drain_event_dlq` / `delete_event_dlq`), and the cluster
daemon's existing periodic-maintenance loop
(`shakenfist/daemons/cluster/`). Ground your answers in what
the code actually does today rather than guessing.

Where a question touches on external concepts (MariaDB / InnoDB
JSON column behaviour, JOIN ordering and indexing for
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

This plan is a **placeholder**. It captures intent and the
known open questions and is intentionally light on detail.
Phase 0 will resolve the open questions into a decisions
section and the phase table below will be re-cut accordingly.

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
`eventlog.add_event_multi(...)` (`shakenfist/eventlog.py`),
which sends a `RecordMultiEvent` gRPC call to the daemon. The
daemon writes the row into a sqlite file per `(object_type,
object_uuid, year-month)` chunk on the eventlog node's local
filesystem. REST API read endpoints
(`external_api/{instance,artifact,network,node,blob}.py`) open
the sqlite chunks **directly** from the local filesystem, which
requires sf-api to be on the same node as the eventlog
storage.

The write path already has a foot in MariaDB. When the gRPC
call fails or is suppressed (the
`config.EVENTLOG_SUPPRESS_GRPC` /
`set_force_event_dlq(True)` startup paths), events fall through
to `mariadb.enqueue_event_dlq`, queueing into a MariaDB DLQ
table that the eventlog daemon then drains *back out* and
writes into sqlite. So under bad-weather conditions the path
is **caller → MariaDB DLQ → sf-eventlog → sqlite**, and under
good conditions it is **caller → gRPC → sf-eventlog → sqlite**.
Both terminate at the same place. The DLQ exists primarily to
solve circular startup dependencies (the eventlog daemon
itself cannot record its own startup event through gRPC) and
gRPC unavailability, not because MariaDB is unsuitable for
the storage.

The **local spool** lands during the network-facade branch
ahead of the rest of this plan. Profiling identified the
per-event synchronous gRPC as the largest remaining
contributor to dispatch-time wrapper overhead (~200 ms each
under bursty load, multiple events per cluster operation). The
spool moves caller-side cost down to a sub-millisecond local
sqlite insert, with a background drainer thread batching
events into a new `RecordMultiEventBatch` RPC. The spool is
per-daemon (`/srv/shakenfist/spool/eventlog/<daemon>-<pid>.db`)
and survives process crashes; an orphan-spool sweep on daemon
startup drains files left behind by previously-dead PIDs. The
caller-facing `eventlog.add_event*` API is unchanged.

With the local spool in place the bootstrapping case that
originally motivated the DLQ is solved cleanly: events
generated during sf-database's own startup land in the spool
and drain as soon as the channel is up. The
`config.EVENTLOG_SUPPRESS_GRPC` / `set_force_event_dlq`
paths and the `event_dlq` table are still wired today as a
belt-and-braces fallback, but phase 0 should confirm they can
be removed alongside the rest of the eventlog plumbing.

The sqlite storage model is **denormalised at write time**:
an event touching N objects writes N rows total — one per
object — each carrying the full `message`, `extra`, and a
`correlation_id` string used to stitch the multi-object
event back together at read time. With the move to MariaDB
this can become a clean two-table normal form (`events` +
`event_objects`) where message/extra are stored once per
event regardless of object count, and `event_uuid` does the
job today's `correlation_id` does.

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
"flush local spool batch via gRPC to sf-database, which writes
to MariaDB." Note that the local-spool indirection landed in
the network-facade branch ahead of this plan, so by the time
this plan executes the change is "swap the drainer's gRPC
target from sf-eventlog to sf-database" rather than a full
rewrite of the caller path.

Pruning moves into the cluster daemon's existing periodic-
maintenance loop, alongside other regular cluster
housekeeping. The REST API read path stops opening sqlite
files and starts running parameterised SELECTs against the
new tables — which means sf-api can serve event reads from
any node, and the local-filesystem coupling vanishes.

## Mission and problem statement

`sf-eventlog` is removed. Events are written directly into
MariaDB via the same `sf-database` channel everything else
uses; pruning runs in the cluster daemon's periodic-
maintenance loop; REST API event reads become parameterised
SELECTs. The calling-site abstraction
(`eventlog.add_event(...)` / `add_event_multi(...)`) is
preserved unchanged.

Concretely, after this plan lands:

- Two new MariaDB tables exist: `events` (event_uuid PK,
  event_type, timestamp, fqdn, duration, message, extra JSON,
  request_id) and `event_objects` (event_uuid FK,
  object_type, object_uuid, composite PK on those three, with
  an index on `(object_type, object_uuid, event_uuid)` to
  support the per-object stream read).
- The local sqlite spool drainer (already shipped in the
  network-facade branch) flushes batched events through
  `sf-database` into the new MariaDB tables. The
  caller-facing `eventlog.add_event*` API does not change;
  only the drainer's gRPC target changes.
- The REST API event-list endpoints
  (`external_api/{instance,artifact,network,node,blob}.py`)
  use a JOIN-and-LIMIT query directly against the new tables.
- The cluster daemon runs the per-event-type prune sweep on
  the same cadence the old daemon used, honouring the
  existing `MAX_{TYPE}_EVENT_AGE` configs. Prune semantics
  treat an `events` row as deletable only once its last
  `event_objects` row has been pruned, so an event still
  visible on object Y is not removed just because object X's
  retention window dropped its reference.
- The historic sqlite event store is migrated into MariaDB
  by an idempotent migration tool, runnable repeatedly until
  the operator chooses to delete the sqlite files.
- The `sf-eventlog` systemd unit, daemon code, gRPC protos,
  and related config (`EVENTLOG_NODE_IP`,
  `EVENTLOG_API_PORT`, `EVENTLOG_METRICS_PORT`,
  `EVENTLOG_SUPPRESS_GRPC`) are removed.
- The MariaDB event DLQ table (`event_dlq`) and its
  drain code are removed. With the local spool in place
  there is no longer a need for a cluster-side DLQ -- the
  bootstrap chicken-and-egg the DLQ originally solved
  (events generated during sf-database / sf-eventlog
  startup) is handled cleanly by the spool: events sit on
  local disk until enough of the cluster is up for the
  drainer to deliver them. Phase 0 confirms this and
  removes the DLQ code unless it surfaces another failure
  mode the spool doesn't cover.

The principle is: **the local spool is the durability
boundary on the caller side; the existing `sf-database`
channel is the right write path on the cluster side; the
rest is removing indirection.**

## Open questions

This plan is light on detail because almost every concrete
decision depends on a phase 0 research pass. The open
questions include at least:

1. **Confirm the two-table normal form.** The proposed shape
   is `events(event_uuid PK, type, timestamp, fqdn, duration,
   message, extra JSON, request_id)` plus
   `event_objects(event_uuid, object_type, object_uuid)` with
   composite PK and an index on `(object_type, object_uuid,
   event_uuid DESC)` for the per-object read. Phase 0
   confirms this is the right shape, picks exact column
   types, and decides whether `extra` is JSON (queryable but
   heavier) or TEXT (opaque but cheaper). Also confirms
   whether a third table for message/extra content-hash
   dedup is worth doing (probably not — event JSON dedup
   ratios are not blob-like).
2. **Does the event DLQ still need to exist?** Today it
   solves two problems: gRPC unavailability and the
   eventlog-daemon-startup chicken-and-egg. Both are now
   absorbed by the local spool that landed in the
   network-facade branch -- events sit on local disk until
   the drainer can deliver them, with no caller-visible
   failure. Phase 0's job here shrinks to "confirm there is
   no remaining failure mode the spool doesn't cover, then
   delete the DLQ table and its drain code." Default outcome:
   removal.
3. **Prune cadence and the cluster daemon's existing
   maintenance loop.** The old daemon ran prune every loop
   iteration. The cluster daemon has its own cadence for
   periodic work. Phase 0 picks the cadence and confirms
   the prune query is cheap enough at scale to run inline,
   or designs a batched / paged approach if not.
4. **Prune semantics for multi-object events.** Proposed
   rule: delete `event_objects` rows whose event is older
   than that event_type's max age, then delete `events`
   rows that have no remaining `event_objects` rows. This
   means an event with N objects is fully removed only when
   *all* objects have aged it out. Phase 0 confirms this is
   the right semantics — the alternative ("delete the event
   as soon as any object's window expires") loses data
   on the other objects' streams.
5. **Historic sqlite migration shape.** Options are
   (a) one-shot migrate-everything-then-cut-over,
   (b) on-demand: REST reads query both stores and merge,
   migrating sqlite chunks as they're touched,
   (c) cut over and accept loss of pre-cutover history.
   (a) is cleanest but slow on large clusters; (b) is more
   complex but zero-downtime; (c) is honest about the cost.
   Phase 0 picks one and documents the operator-visible
   tradeoff. The migration tool itself should be idempotent
   regardless of which strategy is chosen.
6. **Write throughput / load on sf-database.** Today
   high-event-rate operations (status updates from
   resources daemon, mutate events from queue workers) hit
   sf-eventlog directly. Routing them through `sf-database`
   changes the load profile on a daemon that already
   handles much higher write rates for object state. The
   local spool's batched ``RecordMultiEventBatch``-style
   RPC means sf-database will see one round-trip per drainer
   batch (typically 50-100 events) rather than one per
   event, so the per-event amortisation is favourable. Phase 0
   confirms it's a non-issue or sketches a per-table
   partitioning / write-sharding strategy if not. If OTel
   lands first, use it to baseline the load; otherwise a
   one-off benchmark in phase 0.
7. **Calling-site abstraction signature.** Today
   `add_event(event_type, object_type, object_uuid, message,
   ...)` and `add_event_multi(event_type, objects, message,
   ...)` are two entry points. Phase 0 confirms whether
   keeping both makes sense post-cutover or whether the
   single-object form becomes a thin wrapper over the multi
   form (it's essentially that already).
8. **Per-object event ordering and pagination.** Today the
   sqlite layer reads per chunk in timestamp order with a
   limit. The MariaDB read becomes
   `SELECT ... FROM events e JOIN event_objects eo ON ...
   WHERE eo.object_type = ? AND eo.object_uuid = ?
   ORDER BY e.timestamp DESC LIMIT ?`. Phase 0 confirms the
   indexing strategy supports this without table-scans, and
   decides whether to add API-level pagination (today the
   REST endpoint exposes a `limit` parameter only).
9. **Read-path consistency during migration.** While
   historic sqlite data is being migrated, the read path
   needs to either (a) be aware of both stores, (b) be cut
   over only after migration completes, or (c) accept a
   period where pre-cutover events are temporarily
   invisible. Interacts with question 5.
10. **`request_id` and other extra-dict implicit columns.**
    Today the calling code stashes `request_id` inside the
    `extra` dict for events created during an API request.
    Phase 0 decides whether `request_id` becomes a
    first-class column on `events` (queryable, indexable;
    "show me everything that happened for request X" becomes
    a clean query) or stays inside the JSON extra (simpler
    schema, harder to query). The first-class form supports
    request-scoped audit views and aligns with the eventual
    OpenTelemetry trace-id direction.
11. **Removal of `correlation_id`.** With `event_uuid` as
    the natural key for "the same event across multiple
    objects," the old `correlation_id` becomes redundant.
    Phase 0 confirms nothing reads `correlation_id` that
    couldn't read `event_uuid` instead, and either drops it
    from the calling-site signature or maps it to
    `event_uuid` for compatibility.
12. **Event-write failure handling.** With the local spool
    in place a failed RPC no longer reaches the caller --
    the drainer retries with backoff and events sit in the
    spool until delivered. Phase 0 confirms what the
    drainer should do when the spool grows beyond its
    high-water mark (the network-facade branch picked
    "drop with a counter," matching today's posture when
    sf-eventlog is unreachable for >cooldown; the
    alternative is block-with-timeout, which reintroduces
    backpressure into callers). The
    block-vs-drop tradeoff is operator-visible -- drop loses
    forensic detail on the saturated event stream; block
    risks knock-on slowdowns -- so phase 0 documents the
    choice rather than just inheriting it.

## Execution

Provisional, to be re-cut after phase 0.

| Phase | Plan | Status |
|-------|------|--------|
| -1. Local sqlite spool + batched-RPC drainer (caller side) | _(delivered in the network-facade branch ahead of this plan)_ | Complete |
| 0. Research and decisions document | PLAN-eventlog-direct-mariadb-phase-00-decisions.md | Not started |
| 1. `events` and `event_objects` schema and migration tooling | PLAN-eventlog-direct-mariadb-phase-01-schema.md | Not started |
| 2. Swap the drainer's RPC target from sf-eventlog to sf-database | PLAN-eventlog-direct-mariadb-phase-02-write.md | Not started |
| 3. Move prune sweep into the cluster daemon | PLAN-eventlog-direct-mariadb-phase-03-prune.md | Not started |
| 4. REST API direct-read path | PLAN-eventlog-direct-mariadb-phase-04-read.md | Not started |
| 5. Historic sqlite data migration | PLAN-eventlog-direct-mariadb-phase-05-historic.md | Not started |
| 6. Delete `sf-eventlog` daemon, gRPC protos, systemd unit, config, and the MariaDB ``event_dlq`` | PLAN-eventlog-direct-mariadb-phase-06-remove.md | Not started |
| 7. Documentation | PLAN-eventlog-direct-mariadb-phase-07-docs.md | Not started |

## Dependencies on other plans

- **No hard dependency on `PLAN-remove-primary`.** This plan
  and remove-primary are mutually reinforcing — this plan
  removes one of the reasons sf-api wants to be co-located
  with eventlog storage today (the direct sqlite read path),
  and remove-primary's BYO-LB story is operationally cleaner
  once sf-api on any node can serve event reads — but neither
  blocks the other.
- **`PLAN-remove-etcd` should land first** to keep this plan
  off the etcd codepath entirely. The eventlog write path
  doesn't touch etcd directly today, but the DLQ-removal
  decision in phase 0 is easier to make against a
  post-etcd-retirement codebase.
- **OpenTelemetry instrumentation (not yet drafted) would
  inform phase 0**, especially question 6 (write throughput
  on sf-database) and question 10 (the `request_id` as
  first-class column decision, which has affinity with otel
  trace-id semantics). If OTel lands first, use it. If not,
  phase 0 produces the baseline.
- **The existing `sf-database` election work in
  `PLAN-remove-primary` phase 5 is helpful but not blocking.**
  Even with sf-database still hosted on one machine, the
  routing change "events go through sf-database" is correct
  and complete on day one.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The workflow mirrors
`PLAN-remove-primary.md`, `PLAN-sticky-transfers.md`, and
`PLAN-scheduler-reservations.md`: plan in the management
session, spawn a sub-agent per implementation step, review in
the management session, fix or retry, commit when satisfied.

The destructive phases (5 historic migration; 6 daemon
deletion) should be skewed toward **opus at high effort**
because mis-handling either loses operator-visible history.
Phases 0-4 can mix effort levels, with the schema decisions
in phase 0/1 deserving high effort.

### Planning effort

The master plan itself is **medium effort** — it's a
placeholder converging on a clear direction. Phase 0
(research and decisions, especially the DLQ-removal and
historic-migration questions) is high effort. Subsequent
phases will be re-evaluated once phase 0 lands.

### Step-level guidance

Each phase plan should include a step table in the same
format as `PLAN-remove-primary.md`, with effort, model,
isolation, and brief columns.

### Management session review checklist

Standard checklist from `PLAN-remove-primary.md`, plus:

- [ ] The calling-site abstraction
      (`eventlog.add_event*`) is unchanged in signature.
      Daemon-side callers do not need per-call adjustments
      to follow the cut-over.
- [ ] Per-object event reads via REST return the same data
      shape (and the same `limit` semantics) as the sqlite-
      backed path did, so existing clients see no behaviour
      change.
- [ ] Multi-object event normalisation is exercised by a
      test that creates an N-object event and reads it back
      from each object's stream, confirming the single
      underlying row.
- [ ] The historic sqlite migration is exercised against a
      real sqlite chunk, not a stub, and is idempotent.
- [ ] Pruning of multi-object events does not delete the
      `events` row while any `event_objects` row still
      references it.
- [ ] Object cleanup (`hard_delete()`) accounts for
      `event_objects` rows owned by a deleted object —
      either cascades, or follows the deliberate retention
      semantics the project already has for object history.
- [ ] mypy coverage for the new write/read paths is at least
      as good as today's eventlog module.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* The `sf-eventlog` daemon, its systemd unit, its gRPC
  protos, and its sqlite storage code are removed from the
  tree.
* Calling-site code uses `eventlog.add_event*` exactly as it
  did before, with implementation routed through
  `sf-database` to the new MariaDB tables.
* REST API event-list endpoints return data directly from
  MariaDB, work on any sf-api node, and require no sqlite
  files on disk.
* Pruning runs in the cluster daemon's periodic-maintenance
  loop, honours the existing `MAX_{TYPE}_EVENT_AGE` configs,
  and correctly handles multi-object events.
* The MariaDB event DLQ is either removed (phase 0
  identified no remaining need) or explicitly retained with
  documented justification.
* Historic sqlite event data is migrated into MariaDB by an
  idempotent tool, and the operator-visible cut-over story
  is documented.
* The `MAX_{TYPE}_EVENT_AGE` config keys keep working
  unchanged; eventlog-daemon-specific config keys
  (`EVENTLOG_NODE_IP`, `EVENTLOG_API_PORT`, etc.) are
  removed and any operator-facing renaming or deprecation
  is documented.
* Functional coverage under `deploy/cluster_ci` exercises
  the new write path end to end, including a multi-object
  event and a per-object read.
* `pre-commit run --all-files` passes.

### Future work

- **Request-scoped audit views.** If phase 0 chooses to make
  `request_id` a first-class column, a follow-on can add a
  REST endpoint that returns all events for a given request,
  which is genuinely useful for debugging multi-step API
  flows. Out of scope here.
- **OpenTelemetry alignment.** If the otel work lands, the
  events table is a candidate consumer of trace-id /
  span-id columns, giving cross-daemon trace context to
  every event. Out of scope here but worth keeping the
  schema friendly to that direction.
- **Per-namespace event quotas.** With events centralised in
  MariaDB, per-namespace counts and quotas become a clean
  query. Out of scope here.
- **Event compaction.** High-frequency events (resources,
  status) may eventually want time-window compaction
  ("collapse 60 identical heartbeats into one summary
  row"). Out of scope, but easier to add in MariaDB than
  it was in per-object sqlite.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Plan Status* table.
* **`order.yml`** — add an entry for the new master plan.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the
work you intend to do aligns with that plan.
