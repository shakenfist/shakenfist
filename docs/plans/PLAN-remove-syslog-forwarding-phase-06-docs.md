# Phase 6: Documentation

## Context

This is phase 6 (final) of
[`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md).
It documents the BYO-Loki logging contract for operators, the
events-vs-logs authoring convention for developers/agents, and
updates the top-level project docs. No code changes.

Phases 0–5 are implemented. This phase makes the feature
discoverable and operable: where logs go, how to point SF at a
Loki, what the two modes are, the label/field contract, the
buffering/drop semantics, and how events relate to logs.

## Key references

- **Library field contract** (the producer side, already
  written in phase 1):
  `library-utilities/docs/log-record-fields.md` — base JSON
  fields, `with_fields` normalisation, and "labels are
  downstream". The operator doc references and complements this.
- **Phase-0 Decisions** in
  [`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md):
  the config surface (`LOKI_BASE_URL`/`LOKI_TENANT`/
  `LOKI_AUTH_HEADER`/`LOG_EVENTS_TO_LOKI`), the `{job, daemon,
  host}` label set, the two-mode behaviour, the events
  relationship.
- **Existing operator docs** to match in style/length:
  `docs/operator_guide/events.md` (the closest analog — same
  spool/drainer prose), `database.md`, `load_balancing.md`.
- **Nav source:** `mkdocs.yml.tmpl` (operator_guide list at
  `:127-139`); the docs-sync workflow regenerates `mkdocs.yml`
  from it — do **not** hand-edit `mkdocs.yml`.
- **Top-level docs:** `ARCHITECTURE.md`, `README.md`, `AGENTS.md`
  (its "Working with This Codebase" section is where the
  events-vs-logs convention belongs).

## Step items

### 6a — `docs/operator_guide/logging.md` (the main deliverable)

A new operator guide page covering:
- **What changed**: SF no longer aggregates logs onto a primary
  via rsyslog; it emits structured JSON and (optionally) ships it
  to an operator-provided Loki. Local journald still captures
  per-node logs.
- **Two modes**: Loki configured (spool → drainer → push;
  Loki-only, no second local pipeline) vs Loki unconfigured
  (structured JSON to the local journal, ship nothing). A node is
  never silent.
- **Configuration**: the four `LOKI_*` / `LOG_EVENTS_TO_LOKI`
  options, with the BYO-endpoint pattern and the configurable
  tenant (`X-Scope-OrgID`) and its volume/co-mingling rationale.
- **Labels & field contract**: the bounded `{job, daemon, host}`
  label set, identifiers-in-the-body rule, and a pointer to the
  library `log-record-fields.md` for the full field list. A
  sample LogQL query (`{job="shakenfist"} | json | instance_uuid="..."`).
- **Buffering / backpressure / drop**: the on-disk spool as the
  durability buffer, the high-water-mark drop (counter + WARN),
  exponential backoff, orphan recovery, atexit drain; the
  `logship_*` Prometheus metrics.
- **The node-agent alternative**: an operator already running
  promtail/Alloy/vector can leave `LOKI_BASE_URL` unset and
  scrape journald instead.
- **Events vs logs (operator view)**: where events live (MariaDB,
  authoritative, queryable via the REST API / `events.md`) vs
  logs (Loki/journald), the `LOG_EVENTS_TO_LOKI` echo, and how to
  get a single interleaved pane (Grafana with a Loki panel + a
  MariaDB events panel, or echo on).

### 6b — events-vs-logs convention (developer/agent side)

Add the authoring convention to `AGENTS.md` (the "Working with
This Codebase" area): **if a message relates to SF objects, emit
an event** (`eventlog.add_event[_multi]`) so it lands in the
authoritative per-object MariaDB store *and* the log stream;
**if it has no associated object, emit a log**. Note the
dual-destination reality and that events stay in MariaDB (not
moved to Loki). Cross-link `operator_guide/logging.md` and
`operator_guide/events.md`.

### 6c — top-level docs

- `ARCHITECTURE.md`: a short "Logging" note (structured JSON,
  optional Loki shipper modelled on the eventlog spool/drainer,
  `logship_*` modules) near the eventlog/observability content.
- `README.md`: a one-liner pointing at the new logging guide.
- `docs/operator_guide/installation.md`: if it references the
  old primary/syslog story, update it; add a pointer to the
  logging guide for the BYO-Loki setup.

### 6d — nav + plan bookkeeping

- Add `- "Logging": operator_guide/logging.md` to
  `mkdocs.yml.tmpl` (after "Load Balancing" / near "Events").
  Do not edit `mkdocs.yml`.
- Mark phase 6 complete in this master plan's Execution table and
  `docs/plans/index.md`; mark the whole plan complete once 6
  lands.

## Step-level guidance

Documentation; low risk. The management session can write these
directly (it holds the whole plan in context), or delegate to a
sonnet sub-agent with this plan as the brief. Validate with
`pre-commit run --all-files` (markdown only — a formality, but
run it; actionlint/ansible-lint/flake8/tests/mypy must stay
green).

| Step | Effort | Model | Brief |
|------|--------|-------|-------|
| 6a | medium | sonnet | Write `docs/operator_guide/logging.md` per the outline; match `events.md` style; reference the library field contract. |
| 6b | low | sonnet | Add the events-vs-logs convention to `AGENTS.md`; cross-link logging.md/events.md. |
| 6c | low | sonnet | ARCHITECTURE.md logging note; README pointer; installation.md update. |
| 6d | low | haiku | `mkdocs.yml.tmpl` nav entry; mark phase 6 + the plan complete in the master plan and index. |

## Success criteria

- `docs/operator_guide/logging.md` exists and covers: the two
  modes, the four config options + tenant rationale, the label
  and field contract (pointing at the library doc), the
  buffering/drop semantics + metrics, the node-agent alternative,
  and the events-vs-logs operator story.
- `AGENTS.md` documents the events-vs-logs authoring convention.
- `ARCHITECTURE.md` / `README.md` / `installation.md` reference
  the new logging model; no stale "logs aggregate on the primary
  via rsyslog" claims remain.
- `mkdocs.yml.tmpl` lists the logging page; `mkdocs.yml` is left
  for the docs-sync workflow.
- Phase 6 and the overall plan are marked complete in the master
  plan and `docs/plans/index.md`.
- `pre-commit run --all-files` is green.

## Back brief

Before executing, confirm: the new page lives at
`operator_guide/logging.md` and the convention goes in
`AGENTS.md`; the field contract is referenced (not duplicated)
from the library doc; and `mkdocs.yml.tmpl` (not `mkdocs.yml`) is
the nav file to edit.
