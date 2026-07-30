# Scheduler reservations phase 1: typed node capacity columns

## Prompt

Before implementing any step, read the phase 0 decisions
(`PLAN-scheduler-reservations-phase-00-decisions.md`, Decisions
section) and findings
(`PLAN-scheduler-reservations-phase-00-findings.md`, Parts 1 and
3) so the reasoning behind this phase is understood. Read the
current `node_metrics` implementation end to end:
`_get_node_metrics_table()`, `_ensure_node_metrics_schema()`,
`_direct_upsert_node_metrics()` and `_direct_get_node_metrics()`
in `shakenfist/mariadb.py`; the resources daemon's metrics
assembly and 60 s upsert loop
(`shakenfist/daemons/resources/main.py`, especially
`_compute_reservations()` and the delta-counter block); and
every reader (`shakenfist/scheduler.py`,
`shakenfist/daemons/daemon.py` around line 650,
`shakenfist/node.py:839`, `shakenfist/baseobject.py:79-84`).
Ground the work in what the code does, not what this plan says
it does.

## Situation

Phase 0 decided (D1/D2) that scheduling admission moves to
guarded single-row UPDATEs over materialised capacity counters,
and that phase 2 builds `scheduler_node_capacity` whose `limit_*`
columns are derived from node capacity data. Today that data is
trapped in `node_metrics.metrics_json`, a deliberately
schemaless JSON column (docstring: "SQL queryability of
individual fields is not needed" — no longer true). SQL-side
capacity arithmetic — phase 2's limit derivation, the
reconciler's set-based recompute, `GET /admin/resources`
consistency — needs the capacity-relevant fields as typed
columns.

Phase 0 also found two live, never-worked bugs in this exact
surface (findings, server-side surprise 3; filed as issues 3567
and 3568): the scheduler's disk-bandwidth filter reads
`disk_busy_time_delta_per_sec` and the queue worker's `high_io`
gate reads `disk_busy_time_delta_per_seconds`, while the
resources daemon publishes `disk_busy_time_delta_per_second`.
Both `.get(..., '0')` defaults make the checks silently pass;
the scheduler unit test bakes the wrong key into its fixture.
A further trap found while filing: the published value is a
float rendered as a string (`delta / spacing`, e.g. `'16.6'`),
so correcting only the key would turn the silent no-op into an
`int()` `ValueError` crash on the scheduling path.

## Mission and problem statement

Promote the capacity-relevant metrics fields to typed, nullable
columns on `node_metrics`, populated server-side at upsert time,
without changing any scheduling behaviour; and separately fix
issues 3567 and 3568, which *does* intentionally activate two
dormant gates. Phase 2 (capacity tables and reconciler) is the
consumer of the columns; nothing in phase 1 reads them yet
beyond tests.

## Design

### Columns promoted (15)

All nullable — rows written by an older resources daemon simply
lack the fields, and NULL is honest about that (the existing
in-Python fallbacks for `cpu_schedulable` and
`memory_reserved_mb` show the pattern):

| Column | Type | Source key | Notes |
|---|---|---|---|
| `cpu_max` | INTEGER | `cpu_max` | total hardware threads |
| `cpu_schedulable` | INTEGER | `cpu_schedulable` | threads after per-host reservation |
| `cpu_max_per_instance` | INTEGER | `cpu_max_per_instance` | per-instance vCPU cap |
| `cpu_total_instance_vcpus` | INTEGER | `cpu_total_instance_vcpus` | allocated vCPUs |
| `cpu_load_1` | DOUBLE | `cpu_load_1` | load ordering (phase 00a) |
| `cpu_load_5` | DOUBLE | `cpu_load_5` | |
| `cpu_load_15` | DOUBLE | `cpu_load_15` | |
| `memory_max` | INTEGER | `memory_max` | MB |
| `memory_available` | INTEGER | `memory_available` | MB |
| `memory_reserved_mb` | INTEGER | `memory_reserved_mb` | per-host reservation, half-machine cap |
| `memory_total_instance_actual` | INTEGER | `memory_total_instance_actual` | MB, KSM-aware |
| `disk_free_instances` | BIGINT | `disk_free_instances` | **bytes** (findings surprise 12: unit conversions happen at the read edge) |
| `disk_reservation_gb` | INTEGER | `disk_reservation_gb` | per-host reservation |
| `disk_busy_time_delta_per_second` | DOUBLE | `disk_busy_time_delta_per_second` | the correctly-spelled key |
| `node_queue_waiting` | INTEGER | `node_queue_waiting` | queue-state filter |

`metrics_json` remains the full payload and remains
authoritative for readers in this phase; the typed columns are
a projection of it. No new indexes: the table is one row per
node, PK lookups and tiny full scans only.

### Extraction at upsert, server-side

`_direct_upsert_node_metrics()` extracts the fields from the
incoming metrics dict into the typed columns in the same
`INSERT ... ON DUPLICATE KEY UPDATE`. Doing it in `mariadb.py`
(executed by `sf-database`) rather than in the resources daemon
means the gRPC surface is unchanged and rows written by
old-version resources daemons during a rolling upgrade still
get their columns populated. A single module-level table of
`(key, column, coercion)` drives extraction, so column list and
coercion rules live in exactly one place; values that fail
coercion extract as NULL with a warning rather than failing the
upsert (metrics writes must never be blocked by one bad field).
Coercions parse via `float()` first (the delta fields are
float-strings) and truncate to int where the column is integral.

### Migration

`NODE_METRICS_VERSION` 2 → 3. `_ensure_node_metrics_schema()`
gains idempotent `ALTER TABLE node_metrics ADD COLUMN` steps
modelled on the `cluster_locks` `expires_at` migration
(`mariadb.py:1842`), guarded by an information-schema existence
check so re-runs are safe. Existing rows keep NULL columns
until the next 60 s upsert cycle repopulates every live node —
no backfill needed for a table whose rows are ephemeral by
design. The standard machinery gives the rest: `sf-ctl
ensure-mariadb-schema` applies it; `verify_schema_versions`
makes `sf-database` refuse to start until it has been applied.

### Bug fixes 3567 / 3568 (behaviour-activating, kept separate)

A shared constant (e.g.
`constants.METRIC_DISK_BUSY_TIME_DELTA_PER_SECOND`) becomes the
single spelling, used by the publisher
(`daemons/resources/main.py` derives it generically today — the
constant documents it), the scheduler filter and the `high_io`
gate. Both consumers parse with `float()`. The scheduler test
fixture moves to the correct key and gains a regression test
that the filter actually rejects at `busy_time > 1200`; the
`high_io` gate gets whatever unit coverage is practical for
`daemon.py`. The commit says `Fixes #3567` / `Fixes #3568`.

This is the one intentional behaviour change in the phase:
two admission/gating checks that have never fired become live.
The collection-module port taught us that activating dormant
checks is where the risk hides, so step 1 measures the real
`disk_busy_time_delta_per_second` distribution on sfcbr first
to confirm the 1200 (scheduler) and 800 (high_io) thresholds
will not reject or gate under normal load. If the measured
distribution says the thresholds are wrong, fix the thresholds
in the same commit and record the numbers in this plan.

### Explicitly out of scope

- No reader migrates off `metrics_json` (phase 2+ consumes the
  typed columns via SQL).
- No `scheduler_node_capacity`, no claims, no reconciler
  (phase 2).
- No change to the resources daemon's payload or cadence.
- No new indexes.

## Execution

| Step | Description | Effort | Model | Isolation | Status |
|------|-------------|--------|-------|-----------|--------|
| 1 | Measure sfcbr `disk_busy_time_delta_per_second` distribution (Prometheus/Loki) to validate the 1200/800 thresholds; record numbers in the Measurements section | low | management session | none | Not started |
| 2 | Schema: 15 typed columns, extraction table in `_direct_upsert_node_metrics()`, migration v3, unit tests (extraction coercions incl. float-string and garbage values; migration idempotency) | high | opus | worktree | Not started |
| 3 | Fix issues 3567/3568: shared constant, `float()` parsing, corrected test fixture, regression tests; threshold adjustments if step 1 says so | medium | opus | worktree | Not started |
| 4 | Docs: `docs/operator_guide/database.md` table description, CLAUDE.md MariaDB-storage list entry, AGENTS/ARCHITECTURE if touched surfaces warrant | low | sonnet | worktree | Not started |
| 5 | Management-session code review of the branch against the review checklist; update master plan phase table row | medium | management session | none | Not started |
| 6 | Operator review and PR | — | operator | — | Not started |

## Measurements

### Step 1: sfcbr disk-busy distribution

To be recorded when step 1 runs: percentiles of
`disk_busy_time_delta_per_second` per node class over a
representative window with CI load present, and the resulting
verdict on the 1200 (scheduler filter) and 800 (`high_io`
gate) thresholds.

## Administration and logistics

### Success criteria

* The 15 columns exist, are populated by the next upsert cycle
  on every live node, and match the `metrics_json` values they
  project (verified by a unit test and a manual spot-check on
  sfcbr after deploy).
* `sf-ctl ensure-mariadb-schema` migrates a v2 table to v3
  idempotently; `sf-database` refuses to start against v2.
* A rolling-upgrade window (old resources daemon, new
  `sf-database`) leaves NULL columns at worst, never a failed
  upsert.
* The disk-bandwidth filter and `high_io` gate demonstrably
  fire in unit tests with realistic float-string values, and
  issues 3567/3568 auto-close on merge.
* No scheduling behaviour change other than the two activated
  gates; `tox` and cluster CI pass.
* mypy coverage of touched code does not regress.

### Review checklist (management session, step 5)

- [ ] Extraction coercion failures cannot fail an upsert.
- [ ] Migration is idempotent and re-runnable (run twice in a
      test).
- [ ] The two uuid formats pitfall does not apply (node_uuid is
      already `sa.Uuid`) — confirm no new dashed/undashed joins.
- [ ] No reader behaviour depends on the typed columns yet.
- [ ] Threshold activation is justified by step 1 numbers
      recorded in this plan.
- [ ] Commit messages reference `Fixes #3567` / `Fixes #3568`
      correctly (fixes commit) and the plan file rides in the
      PR branch.

### Back brief

Before executing any step of this plan, back brief the operator
as to your understanding of the plan and how the work you
intend to do aligns with that plan.
