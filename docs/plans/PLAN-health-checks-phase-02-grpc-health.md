# Phase 2: dependency-aware grpc.health.v1 on sf-database

## Context

This is phase 2 of [`PLAN-health-checks.md`](PLAN-health-checks.md).
sf-database is the only daemon that runs a `grpc.server()`, and
it already serves the standard `grpc.health.v1.Health` service
(added by `PLAN-byo-mariadb.md` phase 3). Today that servicer
is **static**: `set('', SERVING)` once at startup
(`daemons/database/main.py:5232`) and `set('', NOT_SERVING)`
once before the graceful stop (`:5259`). Between those it never
changes, so a Check says SERVING even if MariaDB has become
unreachable underneath.

This phase makes the health status **dependency-aware**: while
sf-database is running, its `grpc.health.v1` status reflects
whether it can actually reach MariaDB. The payoff is automatic
and already wired from phase 1 — sf-api's readiness checker
calls this exact `Check('')`, so the moment sf-database reports
NOT_SERVING, sf-api's `/readyz` goes 503 (after its own K=3
debounce) and the LB drains the node. `grpc-health-probe` and
any operator monitoring see the same signal.

Two hard constraints carry over from byo-mariadb and **must not
be violated**:

- **Do not reintroduce `Watch`.** The synchronous `HealthServicer`
  deadlocks against the server's single event-dispatch thread on
  Watch streams. Only unary `Check` is supported. The comment at
  `daemons/database/main.py:5218-5229` is the canonical
  explanation.
- **Do not add `healthCheckConfig` to the client channel.**
  `shakenfist/util/grpc_channel.py` deliberately omits it; client
  failover is by keepalive + connectivity state, not the health
  protocol. This phase is **server-side only** — it changes what
  value the servicer holds, not how clients consume it.

This phase touches no schema and no client code.

## Key references in the existing code

- `shakenfist/daemons/database/main.py`:
  - the health servicer registration and startup `SERVING`
    (`:5230-5232`), and the shutdown `NOT_SERVING` flip
    (`:5255-5259`) — both stay.
  - `Monitor(daemon.WorkerPoolDaemon)` (`:4911`), its
    `__init__(self, id)` (`:4919`), and the construction site
    `m = Monitor('database')` (`:5244`) then `m.run()` (`:5253`).
  - `Monitor._run_inner()` (`:5116-5136`): the daemon's main
    loop. It already ticks every 10s via `self.idle(10)`
    (`:5132`) and refreshes the events-row gauge every ~60s
    (`:5125-5131`) — the natural seam for a per-tick MariaDB
    reachability check.
  - the startup verify that already proves MariaDB is reachable
    *and* schema-current before the server starts
    (`:5161-5172`): `verify_mariadb_compat` then
    `verify_schema_versions`, each `raise SystemExit(1)` on
    failure (refuse-to-start).
- `shakenfist/mariadb.py`: `_get_engine()` (`:502`) — note the
  comment at `:516` explaining `pool_pre_ping` was removed to
  avoid a per-checkout `SELECT 1`; a *periodic explicit* ping is
  a different thing and is fine. `verify_mariadb_compat`
  (`:663`) and `verify_schema_versions` (`:730`) are the place a
  `check_reachable()` helper sits alongside.
- `shakenfist/tests/test_database_health.py`: the existing
  in-process smoke test of the servicer wiring — extend it.
- `shakenfist/util/grpc_channel.py`: leave untouched (the
  no-`healthCheckConfig` constraint).

## Inherited / phase decisions

- **Runtime health = MariaDB reachability.** Schema currency is
  a *startup* precondition (refuse-to-start, `:5161-5172`), not
  a runtime-varying signal, so the running daemon's health
  reflects only "can I reach MariaDB right now." Schema is not
  re-checked in the loop.
- **Server reflects reachability directly; the client
  debounces.** A single failed ping flips the status to
  NOT_SERVING; a single success flips it back. Phase 0 D2
  deliberately put the hysteresis on the *client* (sf-api's
  checker, K=3 over 5s polls), so the server should be an honest
  direct reflection rather than adding a second layer of
  debounce. The ping uses a short timeout so a slow query cannot
  stall the 10s tick. End-to-end latency for "MariaDB down →
  sf-api 503" is ~one server tick (≤10s) + the client's K=3
  (≤15s) ≈ 25s, which is fine for a total-MariaDB-outage signal.
- **Startup stays SERVING.** The startup verify already proved
  MariaDB reachable, so the existing `set('', SERVING)` at
  `:5232` is correct and avoids a spurious initial NOT_SERVING
  window. The loop only flips it *away* if MariaDB later becomes
  unreachable.
- **Schema-stale state — decided: keep refuse-to-start, do not
  build the waiting-state now.** The master plan flagged (future
  work) whether sf-database should, instead of refusing to
  start on a schema mismatch, come up and report a distinct
  "schema out of date, awaiting upgrade" NOT_SERVING reason.
  Decision for this phase: **no.** Reversing byo-mariadb's
  deliberate refuse-to-start is out of scope, and the
  up-but-NOT_SERVING trap (clients still route to it via
  round_robin, since the channel is not health-aware) is real.
  Runtime health covers reachability only. The waiting-state and
  the cheaper `sd_notify STATUS=` observability middle-ground
  remain recorded in the master plan's future work for a later,
  deliberate decision.

## Step-level guidance

Sequential, dependent steps; isolation `none`; one commit each.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a — `mariadb.check_reachable()` helper | low | sonnet | none | Add `check_reachable() -> bool` to `shakenfist/mariadb.py`, near `verify_mariadb_compat` (`:663`)/`verify_schema_versions` (`:730`). It opens a short-lived connection on `_get_engine()` and runs `SELECT 1` with a short timeout (a few seconds — use a SQLAlchemy `connect_args`/statement timeout consistent with how the codebase sets timeouts, or a `concurrent`-free `engine.connect()` guarded so it cannot hang the caller indefinitely; check how `verify_*` connect to match the idiom). Returns `True` on success, `False` on **any** exception (never raises — it is a health probe). Do not log on every call; debug-log failures. Unit test (mock `_get_engine`/connection): returns True when `SELECT 1` succeeds, False when the connection/execute raises. Commit subject: `mariadb: add check_reachable health helper.` |
| 2b — drive the health servicer from the daemon loop | high | opus | none | Give the `Monitor` the health servicer and update it from `_run_inner`. (a) Add a `health_servicer=None` parameter to `Monitor.__init__` (`:4919`) and store `self.health_servicer = health_servicer`. (b) At the construction site (`:5244`), pass the already-created `health_servicer` (`:5230`) into `Monitor('database', health_servicer)`. (c) In `_run_inner` (`:5116`), on each tick (alongside the existing gauge refresh, before/after `self.idle(10)`), call `mariadb.check_reachable()` and, if `self.health_servicer` is set, `self.health_servicer.set('', health_pb2.HealthCheckResponse.SERVING if reachable else health_pb2.HealthCheckResponse.NOT_SERVING)`. Only log on a *transition* (SERVING↔NOT_SERVING), not every tick. Keep the startup `set('', SERVING)` (`:5232`) and the shutdown `set('', NOT_SERVING)` (`:5259`) exactly as they are — the loop sits between them. Do NOT touch the gRPC server options, the servicer registration, or add any Watch usage. Unit-test the transition logic by driving the reachability→set mapping with `check_reachable` mocked and a fake/mock servicer, asserting SERVING when reachable, NOT_SERVING when not, and that a transition is logged once. Commit subject: `database: drive grpc health from MariaDB reachability.` |
| 2c — extend health test + docs | medium | sonnet | none | (a) Extend `shakenfist/tests/test_database_health.py` to cover the dependency-aware behaviour at the unit level (the servicer reports NOT_SERVING after a simulated unreachable poll and SERVING after a reachable one) — reuse the in-process channel smoke-test style already there. (b) `ARCHITECTURE.md`: update the sf-database `grpc.health.v1` paragraph (around the existing health discussion) to say the status is now dependency-aware — SERVING iff MariaDB is reachable, flipped by the daemon's 10s loop — and that schema currency remains a refuse-to-start precondition, not a runtime health signal. (c) `docs/operator_guide/database.md`: add a short note that `grpc-health-probe` against sf-database reflects live MariaDB reachability, and that sf-api's `/readyz` consumes it. Keep docs concise; full LB rolling-upgrade docs are phase 4. Commit subject: `tests, docs: dependency-aware sf-database health.` |

## Step ordering and dependencies

- **2a first** — 2b calls `check_reachable()`.
- **2b** depends on 2a.
- **2c** depends on 2b (documents and tests the assembled
  behaviour).

## Success criteria

- While sf-database runs, its `grpc.health.v1` `Check('')`
  returns SERVING when MariaDB is reachable and NOT_SERVING when
  it is not, updated on the daemon's existing ~10s loop.
- The status is SERVING at startup (MariaDB already verified)
  and NOT_SERVING before the graceful stop, exactly as today —
  this phase only adds the in-between dynamics.
- No `Watch` is used anywhere, and `shakenfist/util/grpc_channel.py`
  is unchanged (no `healthCheckConfig`).
- A MariaDB-reachability flip propagates to sf-api `/readyz`
  (via the phase-1 checker) without any client change.
- The reachability ping cannot hang the daemon loop (short
  timeout) and adds negligible load (one `SELECT 1` per tick).
- `pre-commit run --all-files` passes; the new logic has unit
  coverage.

## Back brief

Before executing, back-brief the operator: confirm the
runtime-health-equals-reachability model, the
direct-flip-server / debounce-client split (no server-side
hysteresis), and the decision to keep refuse-to-start for
schema mismatch (deferring the schema-stale waiting-state).

## Review checklist for the management session

Standard checklist from the master plan, plus:

- [ ] No `Watch` added; the servicer remains Check-only.
- [ ] `shakenfist/util/grpc_channel.py` untouched.
- [ ] The startup `SERVING` and shutdown `NOT_SERVING` flips are
      preserved; the loop only changes the in-between value.
- [ ] `check_reachable()` never raises and cannot block the tick
      (short timeout verified).
- [ ] Health status is logged only on transition, not every
      10s tick.
- [ ] Schema is not re-verified in the loop (it stays a
      startup-only refuse-to-start precondition).
