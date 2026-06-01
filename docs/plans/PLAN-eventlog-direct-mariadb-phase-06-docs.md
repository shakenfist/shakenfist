# Phase 6 — documentation

Parent plan:
[PLAN-eventlog-direct-mariadb.md](PLAN-eventlog-direct-mariadb.md).
Predecessors:
[Phase 1](PLAN-eventlog-direct-mariadb-phase-01-schema.md),
[Phase 2](PLAN-eventlog-direct-mariadb-phase-02-write.md),
[Phase 3](PLAN-eventlog-direct-mariadb-phase-03-prune.md),
[Phase 4](PLAN-eventlog-direct-mariadb-phase-04-read.md),
[Phase 5](PLAN-eventlog-direct-mariadb-phase-05-remove.md).

## Scope

Phase 6 brings the documentation in line with the
post-phase-5 code. After this phase ships:

- `ARCHITECTURE.md` no longer lists sf-eventlog as a
  daemon, the MariaDB schema box describes the
  `events` / `event_objects` tables, and the audit-event
  description tracks the new spool → drainer →
  sf-database write path.
- A new `docs/operator_guide/events.md` describes the
  events subsystem end-to-end: write path, read path,
  daily prune, retention configs, the Prometheus metrics
  exposed by each daemon, and the operator-visible
  cleanup of `/srv/shakenfist/events/` after upgrade.
- `docs/operator_guide/database.md` references the new
  events tables instead of the deleted `event_dlq`.
- `docs/release_notes/v07-v08.md` carries a section
  documenting the operator-visible changes: history
  loss at cut-over, ansible inventory changes (the
  `eventlog_node` group is gone), config-key removals
  (the `EVENTLOG_*` keys plus `NODE_IS_EVENTLOG_NODE`),
  and the on-disk cleanup step.
- `docs/plans/index.md` marks phases 1-6 of the
  eventlog-direct-mariadb plan complete.
- Stale references to sf-eventlog and its mechanisms in
  other docs are cleaned up.

Out of scope:

- Documentation in *other* in-flight plan files that
  reference sf-eventlog (`PLAN-health-checks.md`,
  `PLAN-embrace-tls.md`). Those reference the daemon as
  part of their own design discussion; updating them
  belongs to the next iteration of those plans, not
  here.
- README.md and AGENTS.md — survey confirms neither has
  eventlog-specific content. Phase 6 verifies they're
  still accurate but expects no edits.

## Where the release notes go

The codebase uses per-version release-notes files at
`docs/release_notes/v{N-1}-v{N}.md` (e.g.
`v07-v08.md`). The current development version's file is
already wired into the mkdocs navigation via
`mkdocs.yml.tmpl` and `order.yml`. Phase 6 appends a
section to that file rather than creating a new one.

The format mirrors the existing sections in
`v07-v08.md`: a heading, a short prose paragraph
describing the operator-visible change, then a bullet
list of concrete steps the operator needs to take or
notice. Phase 6's section covers:

- The history-loss point: events written before the
  upgrade are not reachable through the REST API
  post-cut-over. The on-disk sqlite chunks at
  `/srv/shakenfist/events/` remain present on the old
  eventlog node until the operator removes them.
- The ansible inventory change: the `eventlog_node`
  group is gone. Operators upgrading need to remove
  that group from their inventory before deploying.
- The config-key removals: `EVENTLOG_NODE_IP`,
  `EVENTLOG_API_PORT`, `EVENTLOG_METRICS_PORT`,
  `EVENTLOG_SUPPRESS_GRPC`, `NODE_IS_EVENTLOG_NODE` are
  all deleted. Setting them in environment files is
  harmless but unused; operators are encouraged to
  drop them.
- The post-upgrade cleanup: `rm -rf
  /srv/shakenfist/events/` is safe once the new code is
  running.
- The REST response shape change: `correlation_id`
  becomes `event_uuid` in `/events` endpoint responses,
  and `request_id` is a new top-level field. Clients
  that introspect either key need adjusting; clients
  that pass through the dict opaquely (the
  client-python library) need no change.
- The new metrics exposed: `database_events_rows`,
  `database_events_inserted_total{event_type}`,
  `database_events_pruned_total{event_type}`,
  `database_orphan_events_pruned_total`,
  `eventlog_spool_depth`,
  `eventlog_spool_dropped_total`. Plus the
  `database_*_total{operation}` per-RPC counters on
  sf-database that pick up the new RPCs automatically.

## Operator guide page shape

`docs/operator_guide/events.md` (new) covers:

- **Overview.** One paragraph: events are written from
  every daemon via the local spool, drained in batches
  via gRPC to sf-database, and stored in two MariaDB
  tables (`events` and `event_objects`). REST reads
  come from sf-database via `GetObjectEvents` and can
  be served by any sf-api node. The daily prune runs
  from the cluster maintainer.
- **Write path.** Spool layout
  (`/srv/shakenfist/spool/eventlog/<daemon>-<pid>.db`),
  drainer cadence (`DRAIN_POLL_INTERVAL`,
  `DRAIN_BATCH_SIZE`), backoff behaviour. The high-
  water-mark (`SPOOL_HIGH_WATER_MARK` = 100000) and
  drop semantics: events past the cap are dropped
  silently with the `eventlog_spool_dropped_total`
  counter advancing. Pointer to the spool-depth gauge
  for proactive monitoring.
- **Read path.** REST endpoints
  (`/{instance,artifact,network,node,blob}/<u>/events`)
  query MariaDB directly via the
  `GetObjectEvents` RPC. Server-side limit cap of 1000
  rows; `limit=0` or negative is treated as the default
  100. The response shape, including the
  `event_uuid` and `request_id` first-class fields.
- **Retention.** The eight `MAX_{TYPE}_EVENT_AGE`
  configs (audit, mutate, status, usage, resources,
  prune, historic, api_request) with their current
  defaults. The daily prune is run by
  `scheduled_tasks.prune_events` on the elected
  cluster maintainer; three stages (per-event-type,
  api-request object override, orphan events sweep).
  Multi-object retention semantics: an event row
  survives until its last `event_objects` row is
  pruned, so a long-retention event tied to a short-
  retention object remains visible from the long-
  retention object's stream.
- **Object hard-delete cleanup.** When a
  `DatabaseBackedObject` is hard-deleted, its
  `event_objects` rows go with it via the
  `DeleteObjectEvents` RPC; the `events` row stays
  alive if any other object still references it and
  is reaped by the next daily orphan sweep otherwise.
- **Metrics reference.** Table of every Prometheus
  metric the subsystem exposes, the daemon that hosts
  it, and what to monitor it for.
- **Operator cleanup after upgrade.** The
  `/srv/shakenfist/events/` directory on the former
  eventlog node holds pre-cut-over sqlite chunks that
  are no longer read. `rm -rf /srv/shakenfist/events/`
  is the recommended cleanup; no daemon writes there
  any more.

## ARCHITECTURE.md updates

Three concrete edits per the survey:

- Daemon table (~line 20): drop the `sf-eventlog | Event
  logging service | 13009` row.
- MariaDB schema box (~line 65): replace the `event DLQ`
  bullet with an entry describing the `events` and
  `event_objects` tables.
- Audit-events description (~lines 173-174): replace
  the "out-of-band through the eventlog gRPC service
  path, which falls back to the MariaDB `event_dlq`"
  prose with "directly into MariaDB via the local
  spool drainer's `mariadb.record_event_batch` call."

## database.md updates

Two concrete edits per the survey:

- Lines 26-27: the "Event log DLQ" bullet becomes an
  entry describing `events` and `event_objects` as
  primary storage, with a pointer to the new
  `events.md` operator guide for the full picture.
- Line 44: drop the `/sf/event/{type}/{uuid}/` etcd
  key-prefix mention (etcd is post-removal context;
  this was a relic from the etcd-DLQ migration whose
  helper was deleted in phase 5c).

## Stale-reference cleanup

The survey identified a handful of stale references
that don't naturally fall into a single doc page:

- **`docs/plans/index.md`** Health-checks row mentions
  "sf-eventlog with leader / standby readiness
  semantics" — sf-eventlog no longer exists; update to
  reference only sf-database. (The `PLAN-health-
  checks.md` body has more mentions but that's out of
  scope per the survey.)
- **`docs/release_notes/v07-v08.md`** has lines about
  the old eventlog-node-down fallback behaviour. These
  predate phase 5 and should be reviewed; reverbose
  history (descriptions of how things used to work) is
  fine if clearly tagged as such, but anything still
  written in the present tense needs updating or
  deleting.
- **`docs/operator_guide/networking/overview.md`**
  may mention eventlog gRPC cost on the dispatcher's
  path — verify and update.
- **`docs/operator_guide/upgrades.md`** may reference
  the old eventlog sqlite schema-upgrade pattern —
  rewrite or delete.

The plan can't enumerate every stray reference in
advance; phase 6 step 6a's brief includes a final
`grep -ri 'sf-eventlog\|EVENTLOG_\|event_dlq\|
correlation_id\|/srv/shakenfist/events\|
redirect_to_eventlog_node' docs/` sweep with cleanup
of anything that surfaces in present tense.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Write the new `docs/operator_guide/events.md` per the "Operator guide page shape" section of the phase 6 plan. Update `ARCHITECTURE.md` per the "ARCHITECTURE.md updates" section. Update `docs/operator_guide/database.md` per the "database.md updates" section. Verify `README.md` and `AGENTS.md` don't need eventlog edits (survey says no, but confirm by reading both end-to-end). Run a `grep -ri 'sf-eventlog\\|EVENTLOG_\\|event_dlq\\|correlation_id\\|/srv/shakenfist/events\\|redirect_to_eventlog_node' docs/` and clean up any present-tense reference in `docs/operator_guide/`, `docs/developer_guide/`, and `docs/components/`. Skip other-plan files (`PLAN-health-checks.md`, `PLAN-embrace-tls.md`) and the existing eventlog-direct-mariadb plan files — those are deliberately retained as historical record. Run `pre-commit run --all-files` to confirm clean (the docs hooks should be no-ops; lint hooks pass through documentation). Commit message subject: "docs: operator guide and architecture for events on MariaDB." |
| 6b | medium | sonnet | none | Add the operator-visible release-notes section to `docs/release_notes/v07-v08.md` per the "Where the release notes go" section of the phase 6 plan. Cover: history loss, ansible inventory change, config-key removals, post-upgrade cleanup, REST response shape change (`correlation_id` -> `event_uuid`, new `request_id` field, 1000-row limit cap), new Prometheus metrics. Read the existing `v07-v08.md` to match style. Run `pre-commit run --all-files`. Commit message subject: "release notes: events on MariaDB cut-over." |
| 6c | low | sonnet | none | Mark phases 1-6 of the eventlog-direct-mariadb plan complete in `docs/plans/index.md`: each row's status column changes from "Not started" to "Complete." Also update the `docs/plans/index.md` health-checks row referenced in the "Stale-reference cleanup" section of the phase 6 plan (drop the sf-eventlog mention from that row's description; keep the rest). Run `pre-commit run --all-files`. Commit message subject: "docs: mark eventlog-direct-mariadb phases 1-6 complete." |

Ordering: 6a → 6b → 6c. Each step is independent in
content but committing in order keeps the narrative
clean (architecture docs reflect the new shape, then
the release notes announce it, then the plan-status
table reflects completion).

## Risks and mitigations

- **Risk:** A new operator who hasn't followed the
  branch's history reads `events.md` and is confused
  by references to the spool / drainer they've never
  seen before.
  **Mitigation:** `events.md` is self-contained and
  introduces the spool / drainer concepts in its
  Overview paragraph before referencing them. Pointer
  to `ARCHITECTURE.md` for the broader daemon picture.

- **Risk:** The release-notes section is written for
  the wrong version's file.
  **Mitigation:** Confirm the in-development file is
  `v07-v08.md` by reading the mkdocs nav block before
  editing. If the active version has rolled over since
  this plan was written (unlikely on this timeline),
  use the new file.

- **Risk:** A stale reference is missed during the
  grep sweep because of a non-obvious phrasing
  ("the events service", "the audit subsystem", etc).
  **Mitigation:** Acceptable — phase 6 isn't a
  guarantee that no reference survives, it's a best-
  effort cleanup. If a reference is found post-phase-6
  it can be fixed in a one-line follow-up commit.

- **Risk:** Marking phases 1-5 "Complete" in the
  status table before this branch merges is
  technically wrong (those phases ship when the branch
  is merged, not when the commits land on the branch).
  **Mitigation:** The status reflects the *plan's*
  implementation status. When the branch merges to
  develop, the rows are already marked Complete and
  no follow-up is needed. If for some reason the
  branch never merges, this is an obvious anomaly
  that would be caught at PR review.

## Definition of done

- [ ] `docs/operator_guide/events.md` exists and
      covers the write path, read path, retention,
      hard-delete cleanup, metrics, and operator-side
      sqlite cleanup.
- [ ] `ARCHITECTURE.md` no longer references
      sf-eventlog in any present-tense context.
- [ ] `docs/operator_guide/database.md` describes the
      `events` / `event_objects` tables and no longer
      references `event_dlq` in present tense.
- [ ] `docs/release_notes/v07-v08.md` contains the
      operator-visible-cut-over section.
- [ ] `docs/plans/index.md` shows phases 1-6 of the
      eventlog-direct-mariadb plan as Complete.
- [ ] `pre-commit run --all-files` is clean.
- [ ] Each commit is self-contained; commit messages
      follow project conventions including the Prompt
      paragraph and Co-Authored-By line with model and
      effort.

## Back brief

Before executing any step of this phase, the
implementing sub-agent should back-brief the management
session on its understanding of the brief and the
surrounding context.
