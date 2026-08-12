# Phase 0: Research and decisions for Loki logging

## Context

This is phase 0 of
[`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md).
It is a **decisions phase: no production code changes.** Its
entire output is documentation — a "Decisions" section appended
to the master plan and a log-record **field-name contract**
table — that turns the master plan's eleven open questions into
concrete, committed answers that phases 1–6 implement against.

Much of the design space was already closed during master-plan
authoring, and several decisions were taken with the operator
in the loop:

- **JSON-only daemon logging** (JSON is the sole daemon format,
  text formatter paths removed).
- **Two operating modes** — Loki configured (preferred;
  spool-buffered push, no deliberate second local pipeline) vs
  Loki unconfigured (structured JSON to the local journal only).
- **Configurable tenant** (`X-Scope-OrgID`) so SF logs do not
  co-mingle with other tenants.
- **Events stay in MariaDB**; Loki receives only an optional
  operational echo (`LOG_EVENTS_TO_LOKI`).
- **Shipper lives in `shakenfist`**, not the library; the
  library owns only the JSON format and the field contract.

Phase 0 therefore *ratifies* those and spends its real effort on
the genuinely open remainder: the Loki push wire format and the
full field-name contract (OQ3), the spool/handler module
factoring (OQ5), the JSON-only/gunicorn/console mechanics (OQ8,
OQ9), and the CI Loki topology across the two-layer test cloud
(OQ7, OQ10).

Per the master plan's prompt, ground every answer in the code as
it exists today; do not speculate where you can read. Where a
decision touches external convention (the Loki HTTP push API,
Loki labels vs structured metadata and the Loki version that
gates them, `X-Scope-OrgID` multi-tenancy, gunicorn's logging
integration, pylogrus `JsonFormatter`), research it and cite the
basis. Flag any uncertainty explicitly.

## Key references in the existing code

- `library-utilities/shakenfist_utilities/logs.py` — `setup()`
  (`logs.py:178`), the `JsonFormatter` path (`logs.py:209-222`),
  the text-formatter branches to be removed (`logs.py:223-231`),
  `setup_console()` (`logs.py:237`), and `with_fields()` field
  normalisation. The producer side of the field contract.
- `shakenfist/eventlog_spool.py` — the prior-art spool: WAL
  SQLite, `SPOOL_HIGH_WATER_MARK = 100_000` (`:72`), drop+counter
  on full, orphan recovery, the `SPOOL_ROOT` comment that already
  anticipates "a future loki-style log-spool peer" (`:59-62`),
  and the `EVENTLOG_SPOOL_DROPPED` / `EVENTLOG_SPOOL_DEPTH`
  metrics (`:50-56`).
- `shakenfist/eventlog_drainer.py` — the prior-art drainer
  thread: `DRAIN_BATCH_SIZE`/`DRAIN_POLL_INTERVAL`/backoff
  constants (`:43-63`), the `_DrainerThread` loop (`:161-319`),
  `_drain_one_batch` (`:224-261`), and the `atexit` drain.
- `shakenfist/eventlog.py` — `add_event_multi()` (`:36-116`):
  the `log.info('Added event')` echo (`:80-93`) that becomes the
  event→Loki signal, and the MariaDB spool enqueue (`:101-116`)
  that stays authoritative. This is the seam for
  `LOG_EVENTS_TO_LOKI`.
- `shakenfist/daemons/daemon.py` — `write_pid_file()` (`:113`),
  where `eventlog_drainer.start(...)` is called and where
  `logship.start(...)` will sit beside it.
- `shakenfist/external_api/gunicorn_config.py` — `post_fork()`
  (`:56`), the per-worker start seam; also the place gunicorn's
  own logger config / `logger_class` would be set.
- `shakenfist/config.py` — the Pydantic config; the
  `MARIADB_GATEWAY_HOSTS` / `MARIADB_GATEWAY_PORT` group is the
  BYO-endpoint pattern to mirror for the `LOKI_*` knobs.
- `shakenfist/deploy/shakenfist_ci/cluster_ci_tests` and
  `shakenfist/deploy/ansible` — how MariaDB is stood up and how
  `MARIADB_*` is plumbed into the inner cluster; the template for
  standing up Loki in CI.
  Mind the two-layer CI topology (see the project memory: the
  under-cloud hosts the nested test cluster).

## Deliverables

Phase 0 is complete when these exist and are committed:

1. A **Decisions** section appended to
   `PLAN-remove-syslog-forwarding.md`, recording a concrete
   answer to every open question (1–11), each as "Decision: …"
   with a one-line rationale and, where relevant, the config
   knob / default / metric name chosen.
2. A **log-record field-name contract** table inside that
   Decisions section (OQ3): every `with_fields(...)` key in
   active use, its JSON field name, and its disposition (Loki
   **label**, **structured metadata**, or **JSON body only**),
   plus the fixed label set.
3. A **config-surface table**: every new `LOKI_*` /
   `LOG_EVENTS_TO_LOKI` option with type, default, secret-ness,
   and one-line meaning, ready for phase 2 to add to
   `config.py`.
4. Confirmation (or reasoned revision) that the master plan's
   seven-phase Execution table still holds after the decisions;
   update `docs/plans/index.md` phase rows only if a decision
   changes a phase's scope.
5. No code changes, no proto changes, no schema changes. (A
   one-line doc typo found in passing may be fixed; production
   code is out of scope.)

## Decision items

Each item below is a unit of phase-0 work. The **recommended
decision** is a strong prior from master-plan authoring; the
executing agent confirms it against the code (or external
reference) or refines it, and writes the "Decision: …" prose for
the master plan. An item is not done until its recommendation is
either ratified or replaced with a reasoned alternative.

### D1 — Config surface (OQ1, OQ2, OQ4, OQ11)

Define the full set of new config options, mirroring the
Pydantic `MARIADB_GATEWAY_*` style in `config.py`. Recommended
decision (confirm names/types against `config.py` conventions):

| Option | Type | Default | Secret | Meaning |
|--------|------|---------|--------|---------|
| `LOKI_BASE_URL` | str | `''` | no | Loki base URL; empty disables the shipper |
| `LOKI_TENANT` | str | `''` | no | `X-Scope-OrgID` value; empty sends no tenant header |
| `LOKI_AUTH_HEADER` | str | `''` | yes | Opaque `Authorization` header value; empty sends none |
| `LOG_EVENTS_TO_LOKI` | bool | `True` | no | Whether the per-event `'Added event'` line is emitted to the log stream (never affects the MariaDB write) |

- Ratify the **single base URL** over a `LOKI_GATEWAY_HOSTS`
  list (OQ1): Loki is operator-fronted; client-side fan-out is
  future work. Name it so a future list does not force a rename.
- Ratify a **single cluster-wide tenant** (OQ2); per-namespace
  tenancy is future work.
- Ratify **opaque auth header**, mTLS deferred to
  `PLAN-embrace-tls.md` (OQ4).
- Decide whether the spool/batch tunables (high-water, batch
  size, poll/backoff, spool root) are **module constants**
  mirroring `eventlog_spool.py`/`eventlog_drainer.py` (recommended,
  for consistency and because they need no field tuning) or
  config options. State the choice; if any becomes config, add
  it to the table.

Output: the config-surface table with final names/types/defaults
and the constants-vs-config decision.

### D2 — Loki push wire format and field-name contract (OQ3)

The meatiest item. Two coupled sub-decisions.

**(a) Wire format.** Define how a batch of spooled records
becomes a Loki `POST /loki/api/v1/push` body. Recommended:
group records by their label set into `streams`, each with
`values` of `[<nanosecond-ts string>, <line>]`. The `<line>` is
the full JSON log record (the pylogrus `JsonFormatter` output).
**Research and decide** whether to also use Loki **structured
metadata** (the optional third element per value) for the
high-cardinality identifiers, noting the Loki version that gates
it (`allow_structured_metadata`, on by default in Loki 3.x) and
that an operator on older Loki must still work. Recommended:
identifiers live in the JSON body for v1 (works on any Loki),
with structured metadata as an optional enhancement flagged for
later; confirm the version floor and state it.

**(b) Field-name contract.** Enumerate every `with_fields(...)`
key in active use across the codebase (grep for `with_fields`;
known examples include `instance_uuid`, `network_uuid`,
`artifact_uuid`, `blob_uuid`, `node`/`fqdn`, `event_type`,
`request_id`, `duration`, `message`) and produce the contract
table: key → JSON field name → disposition. Recommended label
set is **bounded and low-cardinality**:

- Labels: `{job="shakenfist", daemon=<daemon name>,
  host=<config.NODE_NAME>}`.
- Everything else (especially the per-object UUIDs and
  `request_id`) stays in the JSON body (and/or structured
  metadata), **never a label**, to avoid Loki cardinality
  explosion.

Output: the wire-format decision (with the structured-metadata
version finding), the fixed label set, and the full field-name
contract table — this is the producer-side contract phase 1
implements and phase 6 documents.

### D3 — Spool and handler module factoring (OQ5)

Ratify **forking** a sibling spool/drainer rather than
generalising the eventlog one (lower risk to the event path,
which is under the preserve-event-logging priority). Define the
concrete module surface:

- `shakenfist/logship_spool.py` — mirror `eventlog_spool.py`:
  WAL SQLite under `/srv/shakenfist/spool/logship/<daemon>-<pid>.db`,
  high-water drop+counter, orphan recovery.
- `shakenfist/logship_drainer.py` — mirror `eventlog_drainer.py`:
  daemon thread, batch/poll/backoff constants, `atexit` drain,
  `start(daemon_name)` entry point.
- A `logging.Handler` subclass (decide the home — recommended in
  `shakenfist/logship.py` or alongside the drainer) that formats
  each record (the library's `JsonFormatter` output) into a
  spool row carrying the record's labels + body, and enqueues it.
- Metric names, mirroring the eventlog gauges/counters:
  recommended `logship_spool_depth`, `logship_spool_dropped_total`,
  `logship_push_total` (labelled by result), and optionally a
  push-latency histogram. Confirm they register on the
  process-wide default registry like the eventlog metrics.

Decide the **unify-the-two-spools** question's disposition: it is
future work (already recorded), not phase 0/2 scope.

Output: the module list, the handler's home and contract, the
metric names, and the explicit fork (not share) ruling.

### D4 — JSON-only logging, gunicorn, and the console (OQ8, OQ9)

Three coupled mechanics.

- **JSON-only daemon output.** Decide *how* the library makes
  JSON the only daemon format: change the default of `setup()`'s
  `json` parameter, or have SF call `setup(json=True)`
  everywhere, **and** remove the text-formatter branches at
  `logs.py:223-231` so no daemon path can emit non-JSON. Pick the
  approach that does not break non-SF library consumers (the
  library is released independently); recommended: keep the
  `json` parameter but flip SF's call sites and remove the
  SF-used text paths, or gate the removal behind the next major
  library version. State the chosen approach.
- **Handler attachment.** SF attaches its Loki `logging.Handler`
  to the root logger *after* `logs.setup()` returns, only when
  `LOKI_BASE_URL` is set, so the library never depends on
  `shakenfist`. Decide where this attachment happens (recommended:
  inside `logship.start()`, called from `daemon.py:write_pid_file`
  and `gunicorn_config.py:post_fork` beside `eventlog_drainer.start`).
- **gunicorn (OQ8).** Research how gunicorn emits its own
  access/error logs and decide how they become JSON and reach the
  Loki handler once `--log-syslog` is dropped (phase 5).
  Options: a gunicorn `logger_class`, a `logconfig`, or routing
  gunicorn's loggers through the root. State the mechanism so
  phase 5 can drop the flag without losing API access logs.
- **Console (OQ9).** Ratify that `setup_console(...)` stays
  human-readable (it is terminal UI for `sf-ctl`, not a shipped
  log stream); the JSON-only rule scopes to the daemon `setup()`
  path.

Output: the JSON-only mechanism (with library-compat note), the
handler-attachment seam, the gunicorn-logging decision, and the
console ratification.

### D5 — Two-mode behaviour: Loki-only vs local fallback (OQ6)

Ratify and make precise the two operating modes:

- **Loki configured:** attach the Loki handler; do **not**
  attach a deliberate second local syslog/file handler. The
  spool is the local-durability buffer. Drop the Loki copy
  (counter + WARN) only past the high-water mark.
- **Loki unconfigured:** attach a local handler so the node is
  not silent. Decide the exact local sink — `SysLogHandler('/dev/log')`
  (lands in journald even after rsyslog is removed, since
  systemd-journald owns `/dev/log`) vs `StreamHandler(stdout)`
  (systemd captures stdout to journald). Recommended: confirm
  which is cleaner once the rsyslog package is gone (phase 5) and
  state it; either way it is local-only JSON.
- Record the residual: systemd captures stdout/stderr to
  journald regardless of mode (pre-logging tracebacks); this is
  free, local-only, and not a second shipping pipeline.

Output: the per-mode handler set and the disabled-mode local
sink choice.

### D6 — Events relationship and the echo toggle (OQ11)

Ratify the master plan's "Relationship to the eventlog" section
as committed decisions:

- Events remain the **authoritative per-object record in
  MariaDB**; event-lookup REST APIs are **not** moved to Loki.
- `LOG_EVENTS_TO_LOKI` (D1) gates only whether
  `add_event_multi()` emits its `log.info('Added event')` line
  (`eventlog.py:80-93`); it must **not** touch the MariaDB spool
  enqueue (`eventlog.py:101-116`). Default **on** for the
  single-pane operational view.
- Confirm by reading `eventlog.py` that the echo and the MariaDB
  write are cleanly separable at that seam.

Output: the events-stay-in-MariaDB ruling, the precise toggle
semantics, and the seam confirmation.

### D7 — CI Loki topology and dual-path coverage (OQ7, OQ10)

Design how Loki appears in CI. Read
`shakenfist/deploy/shakenfist_ci/cluster_ci_tests` and the
workflow plumbing for MariaDB (the
`tools/ci-install-mariadb.sh` + `GETSF_MARIADB_*` pattern from
`PLAN-byo-mariadb` phase 5) as the template.

Recommended decision:
- A **single-binary Loki** stood up the way CI MariaDB is, with
  its address plumbed into `LOKI_BASE_URL` for the inner cluster.
  Sketch where it runs in the **two-layer** topology (under-cloud
  vs inner cluster) and how the inner cluster reaches it.
- **Keep the CI Loki** to functionally test the push path (the
  newest, riskiest code) — do not collapse CI to local-only.
- **Also** collect each node's local logs into the clingwrap
  bundle (phase 4) so a shipper failure stays debuggable, and add
  cheap coverage that the disabled-shipper path emits local JSON.
- Decide which CI mode runs with `LOG_EVENTS_TO_LOKI` on vs off,
  since it changes whether event lines appear in Loki for the
  phase-4 log-checks.

Output: the CI Loki placement + address plumbing sketch, the
keep-Loki-and-also-collect-local ruling, and the CI echo-mode
choice. (Implementation is phases 3–4; phase 0 only fixes the
shape.)

### D8 — Synthesis: write Decisions, confirm phases, update index

Assemble D1–D7 into the master plan's new **Decisions** section
(including the field-name contract and config-surface tables),
confirm the seven-phase Execution table still holds (revising it
only if a decision changed a phase's scope), and update
`docs/plans/index.md` phase rows if anything changed. This is
management-session synthesis work.

## Step-level guidance

All steps are isolation `none` (no code). Each produces a
markdown subsection for the master plan's Decisions section.
Planning effort for this phase is **high** — the decisions bind
every later phase.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| D1 config surface | medium | sonnet | none | Confirm the `LOKI_*` / `LOG_EVENTS_TO_LOKI` names/types/defaults against `config.py`'s Pydantic `MARIADB_GATEWAY_*` style; decide spool/batch tunables as constants vs config; produce the config-surface table. |
| D2 wire format + field contract | high | opus | none | Research the Loki `/loki/api/v1/push` body and structured-metadata version floor; decide the stream/labels/values shape. Grep `with_fields(` across `shakenfist/` and the library; enumerate every active key and produce the field-name contract table (key → JSON name → label/structured-metadata/body), with the bounded label set `{job,daemon,host}`. |
| D3 module factoring | medium | opus | none | Define `logship_spool.py` / `logship_drainer.py` / the handler module mirroring `eventlog_spool.py`/`eventlog_drainer.py`; pick the handler's home and the metric names; confirm process-wide-registry registration; ratify fork-not-share. |
| D4 json/gunicorn/console | high | opus | none | Decide the JSON-only mechanism in `logs.py` (param default vs SF call sites + removing `logs.py:223-231`) with a library-compat note; the handler-attachment seam; research gunicorn's own logging and how it becomes JSON + reaches the handler; ratify `setup_console` stays human-readable. |
| D5 two-mode behaviour | medium | sonnet | none | Make OQ6 precise: per-mode handler set, the disabled-mode local sink (`/dev/log` vs stdout once rsyslog is gone), and the systemd-stdout residual. |
| D6 events + echo | medium | sonnet | none | Read `eventlog.py:80-116`; confirm the `'Added event'` echo and the MariaDB enqueue are separable; write the events-stay-in-MariaDB + `LOG_EVENTS_TO_LOKI` semantics (never touches the DB write). |
| D7 CI topology | high | opus | none | Read `deploy/shakenfist_ci/cluster_ci_tests` + the CI MariaDB plumbing (`tools/ci-install-mariadb.sh`, `GETSF_MARIADB_*`); sketch single-binary Loki placement in the two-layer topology and `LOKI_BASE_URL` plumbing; rule keep-Loki-and-also-collect-local; pick the CI echo mode. |
| D8 synthesis | high | opus | none | Management session. Assemble D1–D7 into the Decisions section (with both tables), confirm/adjust the phase table, update `index.md` rows if needed. |

## Step ordering and dependencies

- D1 feeds D2/D3/D4/D6 (they reference the option names), so D1
  lands first.
- D2 is the long pole; it is independent of D3–D7 once D1's names
  exist and can run in parallel with them.
- D4 depends on D3's handler-home decision (attachment seam).
- D5, D6, D7 are independent of each other.
- D8 is last; it consumes all of D1–D7.
- One commit for the whole phase is acceptable (it is a single
  document), or split D2's contract table into its own commit —
  per the master plan's "at minimum one commit per phase."

## Success criteria

- Every open question 1–11 has a committed "Decision: …" entry
  in the master plan's Decisions section.
- The field-name contract table covers every active
  `with_fields(...)` key with a label/structured-metadata/body
  disposition, and the label set is bounded and low-cardinality.
- The config-surface table lists every new option with
  type/default/secret-ness, ready for phase 2.
- The Loki push wire format is stated, including the
  structured-metadata version finding and the any-Loki-version
  fallback.
- The seven-phase Execution table is confirmed (or revised with
  reason) and `index.md` agrees.
- No production code, proto, or schema changed.
- `pre-commit run --all-files` passes (markdown only, so this is
  a formality, but run it).

## Back brief

Before executing phase 0, back-brief the operator: confirm this
decomposition (D1–D8), the recommended decisions you intend to
ratify versus genuinely re-open, and any decision where you
expect to depart from the recommended prior — in particular the
D2 structured-metadata-vs-body call and the D4 gunicorn-logging
mechanism, since those are the least pre-decided. Phase 0 changes
no code, but its decisions bind every later phase, so surprises
are cheaper to surface here than in phase 1.

## Review checklist for the management session

- [ ] Each decision is grounded in a file the agent actually
      read (or an external reference it cited), not asserted from
      the master plan alone.
- [ ] D2's field contract enumerates real `with_fields` keys
      from a grep, not an invented list, and no high-cardinality
      key is assigned to a Loki label.
- [ ] D2 states the Loki version floor for structured metadata
      and a fallback that works on older Loki.
- [ ] D4's JSON-only mechanism does not break independent
      (non-SF) consumers of the released library, and the
      gunicorn-logging path is concrete enough for phase 5 to act
      on.
- [ ] D6 confirms `LOG_EVENTS_TO_LOKI` cannot affect the MariaDB
      event write (read the seam).
- [ ] D7's CI Loki placement is coherent with the two-layer CI
      topology and reuses the MariaDB-plumbing pattern.
- [ ] The config-surface and field-contract tables are complete
      and internally consistent with the rest of the Decisions
      section.
