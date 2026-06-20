# Phase 2: The Loki shipper in shakenfist

## Context

This is phase 2 of
[`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md).
It is the **core implementation** phase and lives entirely in the
`shakenfist` package. It builds the in-process, on-disk-spooled,
batched Loki push — modelled directly on the eventlog
spool/drainer prior art — plus the `logging.Handler` that feeds
it, the config, the metrics, the per-process lifecycle wiring,
and the unit tests.

It depends on phase 1 (the library now emits structured JSON
unconditionally, `v0.8.5`, pinned). It does **not** remove any
rsyslog wiring (phase 5) and does **not** rework CI (phases 3–4);
when it lands, the shipper is dormant until an operator sets
`LOKI_BASE_URL`.

Almost all of the design is already settled in the master plan's
**Decisions (phase 0)** section — read it first. This phase plan
elaborates the two things phase 0 left at the design level: the
**handler-attachment mechanism** (how SF achieves "Loki-only when
configured" given the library's per-module handler placement) and
the **HTTP push/retry implementation**. Everything else (config
names, wire format, module factoring, metric names, the events
echo) is a committed decision to implement, not re-open.

## Key references in the existing code

- **Prior art to fork** (mirror faithfully):
  - `shakenfist/eventlog_spool.py` — WAL SQLite spool,
    `SPOOL_HIGH_WATER_MARK = 100_000` (`:72`), drop+counter on
    full, orphan recovery, `EVENTLOG_SPOOL_DROPPED` /
    `EVENTLOG_SPOOL_DEPTH` on the process-wide registry
    (`:45-56`, `:404-425`).
  - `shakenfist/eventlog_drainer.py` — `_DrainerThread`
    (`:161-319`), constants `DRAIN_BATCH_SIZE`/
    `DRAIN_POLL_INTERVAL`/`BACKOFF_*`/`SHUTDOWN_DRAIN_TIMEOUT`
    (`:43-63`), `_drain_one_batch` (`:224-261`),
    `start(daemon_name)` (`:279-292`), the `atexit` drain
    (`:295-308`).
  - The drainer's sink contract: `mariadb.record_event_batch`
    returns `bool` (`mariadb.py:4565`); `True` → delete acked
    rows + reset backoff, `False` → leave rows + back off.
- **Lifecycle seams:**
  - `shakenfist/daemons/daemon.py:93-113` — `write_pid_file()`,
    the universal startup hook; `eventlog_drainer.start(...)` is
    called at `:113`. `logship.start(...)` goes beside it (same
    late-import rationale, `:106-112`).
  - `shakenfist/daemons/daemon.py:61-74` — **`set_syslog_ident()`**:
    the established pattern that walks
    `logging.root.manager.loggerDict.keys() + ['']` and touches
    every `SysLogHandler`. Its comment (`:68-69`) explicitly
    anticipates the Loki migration. This is the precedent for
    phase 2's handler re-pointing.
  - `shakenfist/external_api/gunicorn_config.py:55-56` —
    `post_fork()`; `eventlog_drainer.start('sf-api')` is here.
    `logship.start('sf-api')` goes beside it (per-worker, the
    only fork-safe moment for the drainer thread).
- **Formatter source of truth:**
  `library-utilities/shakenfist_utilities/logs.py:222-238` —
  `setup()` installs a `JsonFormatter(datefmt='Z',
  enabled_fields=[...])` on each per-module logger and returns
  it. Phase 2 reuses that formatter instance rather than
  duplicating `enabled_fields` (see D-D).
- **Config:** `shakenfist/config.py` — Pydantic `Field(default,
  description=...)` style; `MARIADB_GATEWAY_*` is the BYO
  pattern; `env_prefix = 'SHAKENFIST_'`. `requests==2.34.2` is
  already a dependency (`pyproject.toml:78`).
- **Tests to mirror:** `shakenfist/tests/test_eventlog_spool.py`,
  `test_eventlog_drainer.py`, `test_eventlog.py`.

## Design items

Each item references the committed phase-0 decision it
implements; the two that carry real new design (D-D, D-C's push)
are spelled out in full.

### D-A — Config options (implements phase-0 D1)

Add to `config.py`, mirroring the `MARIADB_GATEWAY_*`
`Field(default, description=...)` style, exactly the four options
from the phase-0 config-surface table: `LOKI_BASE_URL` (str, ''),
`LOKI_TENANT` (str, ''), `LOKI_AUTH_HEADER` (str, '', treat as
secret in docs — plain `str`, no `SecretStr`), `LOG_EVENTS_TO_LOKI`
(bool, True). The spool/batch tunables are **module constants**
in the new modules (not config), mirroring eventlog. `LOG_EVENTS_TO_LOKI`
is consumed in phase 2 only if the events-echo guard is folded in
here (see D-G note); otherwise it is added now and wired in
whichever phase touches `eventlog.py`.

### D-B — `shakenfist/logship_spool.py` (implements phase-0 D3)

A near line-for-line fork of `eventlog_spool.py`:
- Spool root `/srv/shakenfist/spool/logship/<daemon>-<pid>.db`,
  WAL SQLite, per-conn lock.
- Row schema is simpler than eventlog's: store `(id INTEGER
  PRIMARY KEY AUTOINCREMENT, ts_ns INTEGER, line TEXT)`. The
  Loki labels are constant for a process (`{job, daemon, host}`)
  so they are **not** stored per row — the drainer holds them.
- `enqueue(ts_ns, line) -> bool` (drop + counter increment over
  `SPOOL_HIGH_WATER_MARK`, mirroring `eventlog_spool.enqueue`),
  `dequeue_batch(limit)`, `delete_ids(ids)`, `count()`,
  `initialise(daemon_name)` with orphan recovery (flock,
  dead-pid migration), `get_spool()`, `reset_for_tests()`.
- Metrics `logship_spool_depth` (Gauge via `set_function`) and
  `logship_spool_dropped_total` (Counter), declared module-scope
  on the default registry exactly as `eventlog_spool.py:45-56`.

### D-C — `shakenfist/logship_drainer.py` (implements phase-0 D3; push is new)

A fork of `eventlog_drainer.py` with the **sink swapped** from
`mariadb.record_event_batch` to a Loki HTTP POST. Keep the
constants, the `_DrainerThread` loop, the backoff, the leave-
failed-rows-in-spool retry contract, and the `atexit` drain
identical (thread name `logship-drainer`).

`_drain_one_batch()`:
1. `dequeue_batch(DRAIN_BATCH_SIZE)` → list of `(id, ts_ns,
   line)`.
2. Build the push body (single stream — labels are constant per
   process, so grouping is a no-op):
   ```
   {"streams": [{"stream": {"job": "shakenfist",
                            "daemon": <daemon>,
                            "host": <config.NODE_NAME>},
                 "values": [[str(ts_ns), line], ...]}]}
   ```
   `str(ts_ns)` — Loki rejects a numeric timestamp (HTTP 400);
   it must be a nanosecond **string**. Rows come off the spool in
   insertion order, so values are already time-ascending.
3. `_push_to_loki(body) -> bool`.
4. On `True`: `delete_ids(...)` + reset backoff. On `False`:
   leave rows, `_on_push_failure()` backoff. (Identical to the
   eventlog drainer's bool handling at `eventlog_drainer.py:251-261`.)

`_push_to_loki(body) -> bool`, using `requests` (already a
dependency):
- `POST {LOKI_BASE_URL.rstrip('/')}/loki/api/v1/push`, JSON body,
  `Content-Type: application/json`.
- Headers: add `X-Scope-OrgID: <LOKI_TENANT>` when non-empty;
  add `Authorization: <LOKI_AUTH_HEADER>` when non-empty (opaque,
  never logged).
- Short connect/read timeout (recommend ~5s) so a wedged Loki
  cannot stall the drainer.
- Return `True` on a 2xx; `False` on any non-2xx, timeout, or
  connection error. **Never raise** out of the drainer loop
  (catch `requests.RequestException`); log the failure at warning
  with backoff context, mirroring the eventlog drainer's
  failure-logging cadence (don't log every retry).
- `logship_push_total{result="success"|"failure"}` counter, and
  optionally a `logship_push_seconds` histogram around the POST.

`start(daemon_name)` initialises the spool and launches the
thread (idempotent), mirroring `eventlog_drainer.start`. The
`atexit` synchronous drain (bounded by `SHUTDOWN_DRAIN_TIMEOUT`)
is preserved so a clean shutdown flushes buffered logs.

### D-D — `shakenfist/logship.py`: handler + lifecycle + the Loki-only attachment (the crux)

This is the only genuinely new design beyond the phase-0
decisions, because phase 0 said "attach a handler to root" but
the library actually attaches a `SysLogHandler` to **each
per-module logger** (`logs.py:237`, `log = getLogger(name);
log.addHandler(...)`). Naively adding a Loki handler at root
would double-emit (each module's syslog handler **and** the root
Loki handler via propagation), defeating the Loki-only decision
(phase-0 D5).

**The handler.** A `logging.Handler` subclass whose `emit(record)`:
- Formats the record to a JSON line. **Reuse the library's
  `JsonFormatter`** rather than reconstructing `enabled_fields`:
  during `start()` (see below) we already walk the existing
  per-module handlers; grab the `JsonFormatter` instance off one
  of them and `setFormatter` it on the Loki handler. Single
  source of truth, no field drift. (Fallback: construct a
  pylogrus `JsonFormatter` with the documented field list only if
  no library handler exists yet — should not happen at start().)
- Computes `ts_ns = int(record.created * 1_000_000_000)`.
- Calls `logship_spool.enqueue(ts_ns, line)` — one cheap insert,
  returns immediately; over the high-water mark the row is
  dropped with the counter (graceful degradation, never blocks
  the caller).
- Is exception-safe: wrap the body so any spool/format error goes
  through `self.handleError(record)` and never propagates into
  the logging call site (a handler must not raise).

**`start(daemon_name)`** — called from the two lifecycle seams.
When `config.LOKI_BASE_URL` is empty, it is a **no-op** (Mode B:
the library's per-module `SysLogHandler`s stay, logs go to
`/dev/log`/journald locally). When `LOKI_BASE_URL` is set
(Mode A, Loki-only):
1. `logship_spool.initialise(daemon_name)`.
2. Build the Loki handler; lift the `JsonFormatter` from an
   existing per-module handler (walk
   `logging.root.manager.loggerDict` exactly like
   `set_syslog_ident`, `daemon.py:61-74`).
3. **Re-point logging to Loki-only:** walk every logger (the same
   `loggerDict` + root walk) and remove the library-installed
   `SysLogHandler`s; add the Loki handler to **root**. Records
   then propagate from per-module loggers to root → Loki handler
   only. This is the deliberate "no second local pipeline"
   posture from D5: the on-disk spool is the local-durability
   buffer; journald still gets the systemd stdout/stderr residual
   (uncaught tracebacks), which is free and not a shipping
   pipeline.
4. Start the drainer (`logship_drainer.start(daemon_name)`),
   register the `atexit` drain. Idempotent — guard against double
   start (mirror `eventlog_drainer`'s idempotence).

Known residual (accept for v1, document it): a module that does a
**lazy** `logs.setup(__name__)` *after* `start()` runs would
re-add a per-module `SysLogHandler` and that module's lines would
also reach `/dev/log` until/unless re-pointed. This is the same
limitation `set_syslog_ident` already lives with, and such lines
still reach Loki via root propagation. The clean long-term fix —
having the library configure the root logger once instead of
per-module — is recorded as **future work**, not phase-2 scope.

### D-E — Lifecycle wiring

Add `logship.start(daemon_name)` immediately after the existing
`eventlog_drainer.start(...)` calls, with the same late-import
rationale:
- `shakenfist/daemons/daemon.py` `write_pid_file()` (after
  `:113`).
- `shakenfist/external_api/gunicorn_config.py` `post_fork()`
  (after `:56`).

### D-F — Metrics

As in D-B/D-C, on the process-wide default registry so they
appear on each daemon's existing `/metrics` with no bootstrap:
`logship_spool_depth`, `logship_spool_dropped_total`,
`logship_push_total{result}`, optional `logship_push_seconds`.

### D-G — Events echo toggle (implements phase-0 D6)

The `LOG_EVENTS_TO_LOKI` guard wraps **only** the
`log.info('Added event')` emission in `eventlog.py:80-93`,
placed after the existing `suppress_event_logging` early-return
and never touching the MariaDB enqueue at `eventlog.py:116`.
This is a tiny, self-contained change; fold it into this phase
(it is logically "what flows to the log stream") or note it as a
trailing commit. Also do the phase-0-noted cleanup: drop the
redundant `fqdn` field in `eventlog.py:82,105` (it duplicates the
`host` label).

### D-H — Tests (mirror the eventlog suites)

Unit tests under `shakenfist/tests/`, mirroring
`test_eventlog_spool.py` / `test_eventlog_drainer.py`:
- spool: enqueue/dequeue/delete, high-water drop increments the
  counter and returns False, orphan recovery migrates a dead
  pid's rows.
- drainer: a batch that the sink accepts is deleted and resets
  backoff; a batch the sink rejects is retained and backs off;
  `atexit`/`drain_until_empty` bounded.
- push: mock `requests.post`; assert the body envelope
  (`streams[].stream` labels, `values` as `[str_ns, line]`
  pairs), the `X-Scope-OrgID` and `Authorization` headers appear
  only when configured, 2xx→True / 5xx/timeout→False.
- handler: `emit` produces valid JSON with a clean `message` and
  enqueues; an enqueue error does not propagate.
- gating: with `LOKI_BASE_URL` unset, `start()` adds no handler
  and starts no thread; with it set, the per-module
  `SysLogHandler`s are removed and the Loki handler is on root.

## Step-level guidance

`logship_spool.py`/`logship_drainer.py`/`logship.py` are tightly
coupled and can be one sub-agent run; the wiring, config, events
guard, and tests can follow. The core (2b–2d) is the risky part —
recommend `isolation: worktree`.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | low | sonnet | none | Add `LOKI_BASE_URL`/`LOKI_TENANT`/`LOKI_AUTH_HEADER` (str, '') and `LOG_EVENTS_TO_LOKI` (bool, True) to `config.py`, mirroring `MARIADB_GATEWAY_*` `Field(...)` style and the phase-0 config-surface table. |
| 2b–2d | high | opus | worktree | Build `logship_spool.py` (fork `eventlog_spool.py`; rows `(id, ts_ns, line)`), `logship_drainer.py` (fork `eventlog_drainer.py`; sink = `_push_to_loki` POSTing the streams envelope with `str(ts_ns)` values, tenant/auth headers, 5s timeout, bool contract, backoff), and `logship.py` (the `logging.Handler` + idempotent `start(daemon_name)` that, when `LOKI_BASE_URL` is set, lifts the library `JsonFormatter`, removes per-module `SysLogHandler`s via the `set_syslog_ident` `loggerDict` walk, attaches the Loki handler to root, starts the drainer, registers `atexit`). Metrics on the default registry. See D-B/D-C/D-D. |
| 2e | low–medium | sonnet | none | Add `logship.start(daemon_name)` after `eventlog_drainer.start(...)` in `daemon.py:write_pid_file` and `gunicorn_config.py:post_fork`, same late-import pattern. |
| 2f | medium | sonnet | none | Guard the `'Added event'` emission in `eventlog.py:80-93` behind `LOG_EVENTS_TO_LOKI` (after the `suppress_event_logging` return, not touching the enqueue at `:116`); drop the redundant `fqdn` field at `:82,105`. |
| 2g | medium–high | opus | none | Unit tests under `shakenfist/tests/` mirroring the eventlog suites; mock `requests.post` for the push payload/headers; assert the Mode A/Mode B handler-attachment behaviour. See D-H. |

## Step ordering and dependencies

- 2a (config) first — everything reads the option names.
- 2b–2d are the core, one worktree run; 2d depends on 2b/2c.
- 2e depends on 2d (`logship.start` must exist).
- 2f is independent (eventlog echo guard) — can run any time
  after 2a.
- 2g depends on 2b–2d.
- One commit per logical unit at minimum: config; the
  spool/drainer/handler core; wiring; events guard; tests. The
  core may be split (spool, then drainer+handler) if cleaner.

## Success criteria

- With `LOKI_BASE_URL` set, each daemon ships its JSON log lines
  to Loki via the on-disk spool + drainer: crash-safe (a line
  that returns from `enqueue` is on disk), drop-don't-block at
  the high-water mark (counter + WARN), exponential backoff on
  push failure with rows retained for retry, orphan recovery, and
  a bounded `atexit` drain — matching the eventlog guarantees.
- The push uses `POST /loki/api/v1/push` with nanosecond-string
  timestamps, `{job, daemon, host}` labels only, identifiers in
  the JSON body, and the tenant/auth headers when configured.
- Mode A is **Loki-only**: the library's per-module
  `SysLogHandler`s are removed and the single Loki handler sits
  on root; Mode B (no `LOKI_BASE_URL`) leaves the library's
  local handlers untouched.
- `LOG_EVENTS_TO_LOKI` gates only the `'Added event'` log line,
  never the MariaDB event write; the redundant `fqdn` field is
  gone.
- Metrics `logship_spool_depth` / `logship_spool_dropped_total` /
  `logship_push_total{result}` appear on each daemon's `/metrics`.
- `pre-commit run --all-files` passes (flake8, stestr, mypy); new
  code follows the eventlog patterns, single quotes, 120 cols.
- No rsyslog wiring removed and no CI changed (phases 5 / 3–4).

## Back brief

Before executing, back-brief the operator on: the handler-
attachment design (D-D) — specifically that Mode A **removes**
the library's per-module `SysLogHandler`s so there is no local
syslog copy when Loki is configured (the spool is the only local
buffer), reusing the `set_syslog_ident` `loggerDict` walk; the
lazy-import residual and its deferral; the decision to reuse the
library's `JsonFormatter` instance rather than duplicate
`enabled_fields`; and whether the `LOG_EVENTS_TO_LOKI` guard +
`fqdn` cleanup ride this phase or wait. These are the points
where phase 2 extends the phase-0 decisions.

## Review checklist for the management session

- [ ] `logship_spool.py` / `logship_drainer.py` preserve the
      eventlog spool's crash-safety and drop-don't-block posture
      (read them against the originals, don't trust the summary).
- [ ] `_push_to_loki` returns bool, never raises, and the drainer
      retains rows + backs off on `False` (same contract as the
      eventlog sink).
- [ ] Timestamps are nanosecond **strings**; the body envelope
      matches the Loki push schema; tenant/auth headers are
      conditional and the auth value is never logged.
- [ ] Mode A removes per-module `SysLogHandler`s and attaches one
      Loki handler at root (verify with a test that inspects the
      logger tree); Mode B is a pure no-op.
- [ ] The Loki handler reuses the library `JsonFormatter` (no
      duplicated `enabled_fields` that can drift).
- [ ] `logship.start` is idempotent and wired beside
      `eventlog_drainer.start` at both seams.
- [ ] `LOG_EVENTS_TO_LOKI` cannot affect the MariaDB event write;
      `fqdn` duplication removed.
- [ ] Metrics register on the default registry and appear on
      `/metrics`.
- [ ] `pre-commit run --all-files` is green.
