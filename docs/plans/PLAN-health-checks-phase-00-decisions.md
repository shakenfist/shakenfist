# Phase 0: Research and decisions for health checks

## Context

This is phase 0 of [`PLAN-health-checks.md`](PLAN-health-checks.md).
It is a **decisions phase: no production code changes.** Its
entire output is documentation — a "Decisions" section
appended to the master plan, a daemon classification table,
and a re-cut phase table — that turns the master plan's
eleven open questions into concrete, committed answers the
later phases implement against.

Much of the design space was already closed during master-plan
authoring. The **routing principle** — the operator's load
balancer probes exactly one surface, sf-api's REST API —
collapsed open questions 1, 2, 4 and 9. Phase 0 therefore
*ratifies* those and spends its real effort on the genuinely
open remainder: the readiness dependency model (3), the drain
grace period and its reconciliation with existing timeouts
(5), the liveness/`WATCHDOG` primitive and lock proof-of-life
(11), endpoint auth (6), the `node_daemon_states` relationship
(7), startup semantics (8), and the daemon classification
(10).

Per the master plan's prompt, ground every answer in the code
as it exists today; do not speculate where you can read. Where
a decision touches external convention (the gRPC health
protocol, systemd `sd_notify` / `WatchdogSec` semantics,
HAProxy/nginx health-check expectations), research it and cite
the basis.

## Key references in the existing code

- `shakenfist/daemons/daemon.py` — the `Daemon` /
  `WorkerPoolDaemon` base classes. `record_start()` (READY=1),
  `exit_gracefully()` (SIGTERM → abort file +
  `DAEMON_STATE_STOPPING`), `record_exit()` (STOPPING=1), and
  the `_send_systemd_notification()` helper at
  `daemon.py:354` (the seam where a `WATCHDOG=1` emitter would
  live). `WorkerPoolDaemon.run()` / `reap_workers()` show the
  existing 5s-batch worker drain.
- `shakenfist/locks.py` — `ClusterLock`, the `_refresh_loop`
  refresher thread (independent of the main loop), `expires_at`
  lease, `lost_event`. The substrate for open question 11.
- `shakenfist/daemons/cluster/main.py` — `_await_election`,
  `is_elected`, the `lock.lost_event.wait(...)` idle pattern;
  the only elected daemon.
- `shakenfist/daemons/database/main.py` — the existing
  `grpc.health.v1.Health` servicer, `server.stop(grace)`, and
  `start_http_server` for Prometheus metrics.
- `shakenfist/external_api/app.py` — Flask app and the
  already-unauthenticated `Root` resource (the registration
  seam for `/livez` `/readyz` `/healthz`).
- `shakenfist/external_api/gunicorn_config.py` — the
  `post_fork` hook; the place a gunicorn lifecycle hook for
  drain would go.
- `shakenfist/deploy/ansible/files/sf-api.service` (gunicorn
  `--timeout 300`) and `.../files/sf.service`
  (`TimeoutStopSec=30s`, `Restart=on-failure`) — the two
  timeouts that disagree, and where `WatchdogSec=` would be
  added.
- `shakenfist/config.py` — ports: sf-api `13000` (hardcoded in
  the service file), `MARIADB_GATEWAY_PORT` 13005,
  `MARIADB_GATEWAY_METRICS_PORT` 13006, `RESOURCES_METRICS_PORT`
  13001, `CLUSTER_METRICS_PORT` 13007. Any new drain/watchdog
  config knobs land here.

## Deliverables

Phase 0 is complete when these exist and are committed:

1. A **Decisions** section appended to `PLAN-health-checks.md`,
   recording a concrete answer to every open question (1–11),
   each as "Decision: …" with a one-line rationale and, where
   relevant, the config knob / default chosen.
2. A **daemon classification table** (open question 10) inside
   that Decisions section: every one of the thirteen `sf-*`
   units bucketed as *sentinel/trivial*, *permanent boundary*,
   or *merge candidate*, with the health surface (if any) each
   gets.
3. A **re-cut phase table** in the master plan's Execution
   section, reflecting that the routing principle shrank
   phases 2 and 3. Update `docs/plans/index.md` phase rows to
   match.
4. No code changes, no proto changes. (If research uncovers a
   one-line doc typo it may be fixed, but production code is
   out of scope for phase 0.)

## Decision items

Each item below is a unit of phase-0 work. The **recommended
decision** is a strong prior from master-plan authoring; the
executing agent confirms it against the code or refines it,
and writes the "Decision: …" prose for the master plan. An
item is not done until its recommendation is either ratified
or replaced with a reasoned alternative.

### D1 — Daemon inventory classification (open question 10)

Read each `shakenfist/daemons/*/main.py` and classify all
thirteen units. Recommended buckets (confirm each against the
code):

- **sentinel / trivial — no health surface:** `sentinel-first`,
  `sentinel-last` (pure systemd-ordering; mark node
  state only), `nodelock` (node-local unix-socket lock; serves
  only same-node daemons, never an LB).
- **permanent boundary:** `database` (deliberate tier per
  `PLAN-byo-mariadb.md`; keeps gRPC health), `privexec`
  (privilege-separation boundary, unix socket).
- **worker / periodic — liveness (`WATCHDOG`) only:** `cleaner`,
  `queues`, `network`, `resources`, `transfers`, `sidechannel`.
- **elected — liveness + lock proof-of-life:** `cluster`.

For each, the table records: bucket, transport (HTTP / gRPC /
unix socket / vsock / TCP / none), whether it gets a health
surface and which (`/readyz`, gRPC health, `WATCHDOG`, none),
and a one-line reason. Flag any daemon as a *merge candidate*
only with a concrete reason; this is advisory input to a
future `PLAN-consolidate-daemons.md`, not a commitment.

### D2 — Readiness dependency model and cache design (open question 3)

The meatiest decision. Define precisely what sf-api readiness
means and how the probe stays cheap.

Recommended decision:
- sf-api `/readyz` reads an **in-memory** ready flag +
  timestamp maintained by a **background checker thread**, not
  a per-request dependency call. A burst of probes touches no
  gRPC/DB.
- The dependency graph is shallow: **sf-api ready ⇔ sf-database
  reachable and SERVING** (consulted via the gRPC health
  `Check` we extend in phase 2), which in turn means **MariaDB
  reachable + schema at the expected version** (sf-database's
  own readiness, defined in phase 2). No other hard
  dependency.
- The checker polls every ~5s. Apply **hysteresis**: flip to
  not-ready only after K consecutive failures (e.g. 3) and
  back to ready after 1 success, so a momentary peer hiccup
  does not deassert readiness and cause LB flap.
- Note the gunicorn wrinkle: with N preforked workers, either
  each worker runs its own lightweight checker, or the check
  is process-shared. Decide (recommended: per-worker checker;
  it is cheap and avoids shared-state machinery, and a worker
  that cannot reach sf-database genuinely is not ready).

Output: the dependency graph, the cache/refresh parameters
(interval, hysteresis K, staleness bound), and the per-worker
vs shared decision, all as committed values.

### D3 — Drain grace period and timeout reconciliation (open question 5)

Recommended decision:
- On SIGTERM, sf-api flips readiness to 503 **first** (before
  gunicorn begins stopping workers), via a draining flag the
  `/readyz` handler reads, set from a gunicorn lifecycle hook
  in `gunicorn_config.py` (e.g. `worker_int` / `on_exit`) or
  an app-level SIGTERM handler. The LB removes the node on its
  next probe; in-flight requests then finish.
- Introduce a config knob (e.g. `API_DRAIN_GRACE`, default
  ~25s) and **reconcile the two existing timeouts**:
  `TimeoutStopSec` (systemd, 30s) must exceed
  `API_DRAIN_GRACE` + the LB's probe interval, and gunicorn's
  `--timeout`/`graceful_timeout` must be set consistently
  rather than the current contradictory 300s. Pick concrete
  values and state them. (This reconciliation is the latent
  bug recorded in the master plan's Bugs section — treat it as
  a fix.)
- Long-running requests (mid-stream blob upload) that cannot
  finish within the grace: out of scope to drain gracefully;
  document that a rolling upgrade may interrupt them and the
  client retries. The per-request "drainable" flag stays
  future work.

Output: the ordering guarantee, the knob + defaults, the
reconciled timeout values, and the long-request disposition.

### D4 — Liveness primitive (`WATCHDOG`) and lock proof-of-life (open question 11)

Recommended decision:
- Wire `WATCHDOG=1` (via `_send_systemd_notification`) emitted
  from each non-trivial daemon's main loop, gated on
  `NOTIFY_SOCKET`, at an interval comfortably under
  `WatchdogSec`. Add `WatchdogSec=` to `sf.service`. systemd
  already has `Restart=on-failure`, so a missed watchdog
  triggers kill-and-restart.
- This closes the lock proof-of-life gap by the **preferred,
  no-`locks.py`-change** path: a wedged elected daemon misses
  its watchdog → systemd kills it → the refresher thread dies
  → the lease expires → a standby steals the lock.
- **Critical subtlety (cite issue #1206):** several daemons
  sleep for long intervals (the cleaner's 60s sleep). A main
  loop must pet the watchdog on a tight cadence **independent
  of its work sleep** — i.e. sleep on an event with a timeout
  shorter than `WatchdogSec` and pet on each wake, mirroring
  the existing `lock.lost_event.wait(...)` idiom. Decide the
  `WatchdogSec` value (recommended generous, e.g. 60s) and the
  pet cadence (e.g. ≤20s), and confirm no legitimate single
  iteration exceeds `WatchdogSec`.
- **Defer** the belt-and-suspenders coupling (refresher
  consults the liveness heartbeat and sheds the lease without
  killing the process). Record it as future work / its own
  micro-plan with hysteresis tests, per the master plan; it is
  not in this plan's implementation phases.

Output: the `WATCHDOG` wiring design, the `WatchdogSec` + pet
cadence values, the per-daemon confirmation re long sleeps,
and the explicit deferral of renewal-coupling.

### D5 — Endpoint shape, auth, and the sf-database-HTTP residual (open questions 2, 6)

Recommended decision:
- sf-api exposes `/livez` (200 while the process serves),
  `/readyz` (200/503 from D2's cached flag), and `/healthz`.
  Decide `/healthz`'s alias target and document it
  (recommended: `/healthz` ≡ `/readyz`, since an LB configured
  for `/healthz` wants "route here?" = readiness). State the
  exact status codes and a **minimal** body that does not leak
  version/topology to a scanner.
- **Unauthenticated**, on the existing port 13000, **no
  separate health port** (keeps it one gunicorn surface).
  Mitigate cluster-scanning by documenting the operator
  firewall expectation (health reachable from the LB subnet
  only) rather than building a port. Confirm the endpoints
  reveal nothing an unauthenticated `/` (`Root`) does not
  already.
- sf-database does **not** also expose HTTP health —
  `grpc-health-probe` is sufficient and nothing LB-routes to
  it. Record this as the resolution of OQ2's residual.

Output: the three endpoints with codes/bodies, the auth +
no-separate-port decision with its documented firewall
expectation, and the no-HTTP-on-sf-database ruling.

### D6 — `node_daemon_states` relationship and startup semantics (open questions 7, 8)

Recommended decision:
- The real-time probe does **not** write heartbeats into
  `node_daemon_states`. That table keeps its orderly-transition
  role (`DAEMON_STATE_RUNNING/STOPPING/STOPPED`); health is a
  separate, real-time substrate. Confirm the two never need to
  be consulted together and record the orthogonality.
- **No separate startup probe.** `/readyz` staying 503 until
  dependencies are satisfied already gives the
  startup-vs-stuck behaviour an operator LB needs. Document the
  first-boot latency expectation (operators set the LB's
  healthy-threshold / timeout generously) so a slow bootstrap
  is not read as failure.

Output: the orthogonality ruling and the no-startup-endpoint
confirmation with the operator-doc note.

### D7 — Ratifications (open questions 1, 4, 9)

No new research; formally record the master-plan resolutions
as "Decision: …" entries so the Decisions section is complete:
- **OQ1:** no per-node readiness aggregator; liveness via
  `WATCHDOG`, not per-daemon HTTP listeners.
- **OQ4:** elected daemons have no readiness probe; their need
  is liveness + lock proof-of-life. sf-database is not elected.
- **OQ9:** health adds no TLS surface; it rides the existing
  LB→sf-api leg and the mesh gRPC channel. Phase-4 doc note for
  L4 passthrough only.

### D8 — Synthesis: write Decisions, re-cut phases, update index

Assemble D1–D7 into the master plan's new **Decisions**
section, **re-cut the Execution phase table** (phase 2 = the
sf-database dependency-aware gRPC health with no election
shape; phase 3 = `WATCHDOG` wiring into the non-trivial
daemons; phases 1 and 4 unchanged in intent), and update the
phase rows in `docs/plans/index.md`. This is management-session
synthesis work.

## Step-level guidance

All steps are isolation `none` (no code). Each produces a
markdown subsection for the master plan's Decisions section.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| D1 classification | medium | sonnet | none | Read each `shakenfist/daemons/*/main.py`; confirm the bucket assignments in this plan's D1; produce the classification table (bucket, transport, health surface, reason) for all 13 units. Flag merge candidates only with a concrete reason. |
| D2 readiness/cache | high | opus | none | Design sf-api's readiness: in-memory flag + background checker (interval, hysteresis K, staleness), the shallow dependency graph (sf-api→sf-database SERVING→MariaDB+schema), and per-worker-vs-shared checker. Read `external_api/app.py`, `gunicorn_config.py`, `util/grpc_channel.py`, the sf-database health servicer. Prove a probe burst touches no DB. |
| D3 drain grace | high | opus | none | Decide the SIGTERM→readiness-503-first ordering and its seam (gunicorn hook vs app SIGTERM handler), the `API_DRAIN_GRACE` knob + default, and reconcile `TimeoutStopSec=30s` with gunicorn `--timeout 300` into consistent values. Read `sf-api.service`, `sf.service`, `gunicorn_config.py`. State the long-request disposition. |
| D4 watchdog + lock | high | opus | none | Design `WATCHDOG=1` emission (seam at `daemon.py:354`), `WatchdogSec` value, and the tight pet cadence independent of work sleeps (cite #1206's 60s cleaner sleep). Trace the wedged-elected-daemon → kill → lease-expiry → failover chain through `locks.py`. Explicitly defer renewal-coupling. |
| D5 endpoints + auth | medium | opus | none | Decide the three endpoints (codes, bodies, `/healthz` alias), unauthenticated-no-separate-port with the documented firewall expectation, and the no-HTTP-on-sf-database ruling. Read `external_api/app.py` (`Root`, decorator order) and `auth.py`. Security judgment on info leakage warrants opus. |
| D6 states + startup | medium | sonnet | none | Confirm and record: real-time probe does not write `node_daemon_states` (read the writes in `daemon.py`/`mariadb.py`); no separate startup endpoint, `/readyz`-stays-503 suffices, plus the operator LB-threshold doc note. |
| D7 ratifications | low | sonnet | none | Write the OQ1/OQ4/OQ9 resolutions as formal "Decision:" entries from the master-plan text. No research. |
| D8 synthesis | high | opus | none | Management session. Assemble D1–D7 into the Decisions section, re-cut the master-plan phase table, update `index.md` phase rows. |

## Step ordering and dependencies

- D1 and D7 are independent and can run first / in parallel.
- D2 → D3 (drain references the readiness flag) and D2 → D5
  (endpoints expose the readiness flag), so D2 lands before
  D3/D5.
- D4 and D6 are independent of D2/D3/D5 and can run in
  parallel with them.
- D8 is last; it consumes all of D1–D7.
- One commit for the whole phase is acceptable (it is a single
  document), or two (classification table, then the rest) —
  per the master plan's "at minimum one commit per phase."

## Success criteria

- Every open question 1–11 has a committed "Decision: …" entry
  in the master plan's Decisions section.
- The daemon classification table covers all thirteen units
  with bucket, transport, health surface, and reason.
- The phase table in the master plan and the phase rows in
  `index.md` are re-cut to match the decisions (phases 2 and 3
  reflect the routing-principle shrink).
- No production code, proto, or schema changed.
- `pre-commit run --all-files` passes (markdown only, so this
  is a formality, but run it).

## Back brief

Before executing phase 0, back-brief the operator: confirm
this decomposition (D1–D8), the recommended decisions you
intend to ratify versus genuinely re-open, and any decision
where you expect to depart from the recommended prior. Phase 0
changes no code, but its decisions bind every later phase, so
surprises are cheaper to surface here than in phase 1.

## Review checklist for the management session

- [ ] Each decision is grounded in a file the agent actually
      read, not asserted from the master plan alone.
- [ ] D2's design demonstrably keeps probes off the DB (no
      per-probe gRPC/MariaDB call).
- [ ] D3's reconciled timeouts are internally consistent
      (`TimeoutStopSec` > drain grace > 0; gunicorn timeout
      aligned) and the SIGTERM-flips-readiness-first ordering
      is explicit.
- [ ] D4 confirms no legitimate main-loop iteration exceeds
      `WatchdogSec`, and the renewal-coupling deferral is
      explicit (no `locks.py` change in scope).
- [ ] D5 keeps the health body free of version/topology leak.
- [ ] The re-cut phase table and `index.md` agree.
