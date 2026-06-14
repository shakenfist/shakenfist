# Phase 1: sf-api health endpoints and SIGTERM drain

## Context

This is phase 1 of [`PLAN-health-checks.md`](PLAN-health-checks.md),
the **canary**: sf-api is the only load-balancer-routable
surface in SF (the routing principle), so it is the one daemon
that gets the full liveness / readiness / drain treatment. The
pattern this phase establishes is the template phases 2–3
copy from, so getting it right here is worth the high effort.

The design was settled in phase 0's Decisions section
(D2 readiness, D3 drain, D5 endpoints); this phase implements
it. Read those decisions first — this plan turns them into
code with concrete file:line seams.

sf-api is **gunicorn** (`--workers 5 --preload`, port 13000,
its own `sf-api.service`). Gunicorn workers do **not** run
`Daemon.run()`, so none of the `shakenfist/daemons/daemon.py`
machinery (READY=1, abort files, `idle()`) applies here — this
phase is self-contained to `shakenfist/external_api/` plus the
config knob and the service unit. (Liveness for the *other*
daemons via systemd `WATCHDOG` is phase 3, and uses the
daemon.py seam instead.)

One ordering note: this phase's readiness checker consumes
whatever sf-database's `grpc.health.v1.Health/Check` returns.
Today that is a static `SERVING` set at startup, so in phase 1
`/readyz` effectively means "sf-database process is up."
**Phase 2** makes `Check` dependency-aware (MariaDB reachable +
schema current), at which point `/readyz` deepens automatically
with no change here. That incremental path is intended.

## Key references in the existing code

- `shakenfist/external_api/app.py` — `_is_health_probe()`
  (`:125-126`, currently `path == '/'`), the audit
  `before_request`/`after_request` hooks (`:129-183`) that
  `_is_health_probe()` short-circuits, the unauthenticated
  `Root` resource (`:186-217`) and its registration
  (`:223`, `api.add_resource(Root, '/')`), and the lazy
  `resolve_node_uuid` before_request (`:96-116`).
- `shakenfist/external_api/base.py` — `Resource.method_decorators`
  (`:684`, none enforce JWT — auth is opt-in per method via
  `@verify_token`/`@caller_is_admin` at `:163`/`:45`), and the
  `log_request` `path == '/'` debug-downgrade (`:587`).
- `shakenfist/external_api/gunicorn_config.py` — the whole file
  (30 lines): `post_fork` (`:23`) starts the per-worker
  eventlog drainer; the module docstring explains why post-fork
  is the only correct seam for a per-worker thread under
  `--preload`. The readiness checker and the drain handler hook
  in here.
- `shakenfist/eventlog_drainer.py` — `start()` (around
  `:279-292`): the idempotent, lock-guarded thread-singleton
  pattern `start_checker()` should mirror.
- `shakenfist/database.py` — `:53-61` shows the
  `make_database_channel(config.MARIADB_GATEWAY_HOSTS,
  config.MARIADB_GATEWAY_PORT, extra_options=...)` call and the
  `grpc.channel_ready_future(c).result(timeout=2.0)` readiness
  idiom (`:40`).
- `shakenfist/util/grpc_channel.py` — `make_database_channel`
  (`:59`), round_robin + keepalive (no `healthCheckConfig` — do
  not add one).
- `shakenfist/daemons/database/main.py` — the health servicer
  `set('', SERVING)` at startup (`~:5230`), `NOT_SERVING`
  before stop (`~:5259`); the `Check` the checker calls.
- `shakenfist/config.py` — the `API_*` `Field` block
  (`API_ASYNC_WAIT` at `:126`); `API_DRAIN_GRACE` lands here.
- `shakenfist/deploy/ansible/files/sf-api.service` —
  `TimeoutStopSec=30s` (`:14`), `SuccessExitStatus`/
  `RestartPreventExitStatus` (`:15-16`), the gunicorn
  `ExecStart` with `--timeout 300` (`:23`).
- `shakenfist/deploy/ansible/files/sf.service` — the **generic**
  daemon unit; **leave it unchanged** in this phase.
- `grpc_health.v1` (`grpcio-health-checking`, pinned in
  `pyproject.toml`): `from grpc_health.v1 import health_pb2,
  health_pb2_grpc` gives `HealthStub` and `HealthCheckRequest`.

## Inherited decisions (from phase 0)

- Readiness is a **per-worker in-memory flag** maintained by a
  background checker started in `post_fork`; `/readyz` reads
  only the flag → a probe burst makes zero DB/gRPC calls.
  Values: poll **5s**, per-`Check` timeout **2s**, hysteresis
  **K=3** failures → not-ready, **1** success → ready,
  staleness bound **15s** (stale ⇒ 503). Initial state =
  **not-ready** (so startup stays 503 until sf-database is
  reachable — this is OQ8's startup behaviour).
- Drain: SIGTERM flips the per-worker draining flag (→ `/readyz`
  503) **first**, via a handler installed in **`post_worker_init`**
  (not `post_fork` — clobbered by `init_signals`; not
  `worker_int` — only fires on SIGQUIT), using a **timer
  thread**, not `sleep()`. Knob **`API_DRAIN_GRACE`=25s**.
  Reconciled timeouts: `TimeoutStopSec` 30→**70s**, add
  gunicorn **`--graceful-timeout 30`**, `--timeout 300`
  unchanged.
- Endpoints: `/livez` (always 200 `ok`), `/readyz` (200
  `ready` / 503 `not ready`), `/healthz` ≡ `/readyz`, all
  unauthenticated like `Root`, minimal `text/plain` bodies.
  Extend `_is_health_probe()` and the `log_request` downgrade
  to the health set.

## Step-level guidance

Steps are **sequential and dependent** (1b–1d build on the
`health.py` module from 1a), and the management session
reviews and commits each before the next. I therefore
recommend **isolation `none`** throughout rather than the
master plan's blanket "worktree for phase 1": worktree-per-step
would fight the build-on-previous-step dependency chain, and
the per-step review+commit already contains the risk. One
commit per step.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a — readiness module + checker + config knob | high | opus | none | Create `shakenfist/external_api/health.py`: a per-worker readiness singleton holding `ready: bool` (init **False**), `last_update: float`, `consecutive_failures: int`, and `draining: bool`. Public API: `start_checker()` (idempotent, lock-guarded thread singleton mirroring `eventlog_drainer.start()` at `eventlog_drainer.py:279-292`); the checker loop builds a `health_pb2_grpc.HealthStub` on `make_database_channel(config.MARIADB_GATEWAY_HOSTS, config.MARIADB_GATEWAY_PORT)` and calls `Check(health_pb2.HealthCheckRequest(service=''))` every `READINESS_POLL=5` s with a **2 s** RPC timeout; on SERVING → 1 success sets `ready=True`, `consecutive_failures=0`; on error/non-SERVING increment `consecutive_failures` and set `ready=False` once it reaches `K=3`; always stamp `last_update`. `is_ready()` returns `ready and (now - last_update) <= READINESS_STALE=15` and `not draining`. `begin_drain()` sets `draining=True`; `is_draining()` reads it. Keep POLL/K/STALE as **module constants**, not config knobs. Add `API_DRAIN_GRACE: int = Field(25, description=...)` to `config.py` next to `API_ASYNC_WAIT` (`:126`). Unit tests (mock the HealthStub/`Check`, no real network): 3 consecutive failures → not ready; 1 success → ready; stale `last_update` → not ready; `draining` → not ready; `start_checker()` called twice spawns one thread. Commit subject: `external_api: readiness state and background checker.` |
| 1b — the three endpoints | medium | opus | none | In `app.py`, add `Livez` and `Readyz` (`api_base.Resource`, bare `get()`, **no** auth decorator — mirror `Root` at `:186-217`). `Livez.get` → 200 `text/plain` `ok`. `Readyz.get` → 200 `ready` if `health.is_ready()` else 503 `not ready` (use `flask.Response` with explicit `status_code`, as `Root` does). Register near `:223`: `api.add_resource(Livez, '/livez')`, `api.add_resource(Readyz, '/readyz')`, `api.add_resource(Readyz, '/healthz')`. Extend `_is_health_probe()` (`:125-126`) to match `{'/', '/livez', '/readyz', '/healthz'}`, and the `log_request` `path == '/'` downgrade (`base.py:587`) to the same set. Unit tests: `/livez`→200 `ok`; `/readyz`→200 when the flag is set, 503 when not and when draining; bodies are the exact minimal tokens; none of the three requires auth (no 401). Commit subject: `external_api: add /livez, /readyz, /healthz.` |
| 1c — start the checker in post_fork | low | sonnet | none | In `gunicorn_config.py` `post_fork` (`:23-30`), after the eventlog-drainer block, add a guarded `health.start_checker()` in the same try/except shape (log-and-continue on failure). This starts the per-worker checker after fork. Commit subject: `external_api: start readiness checker per gunicorn worker.` |
| 1d — SIGTERM drain via post_worker_init | high | opus | none | Add `post_worker_init(server, worker)` to `gunicorn_config.py`. It must install a SIGTERM handler that **survives** gunicorn's `init_signals` (which runs before this hook): capture `orig = signal.getsignal(signal.SIGTERM)`; define `handler(signum, frame)` that calls `health.begin_drain()` then arms `threading.Timer(config.API_DRAIN_GRACE, lambda: orig(signum, frame)).start()` and returns immediately (so the worker keeps serving during the grace); then `signal.signal(signal.SIGTERM, handler)`. Do **not** `time.sleep()` in the handler. Add a focused test that SIGTERM sets `health.is_draining()` True (so `/readyz`→503) and that the original handler is invoked only after `API_DRAIN_GRACE` (patch the Timer / use a tiny grace). Commit subject: `external_api: drain readiness before shutdown on SIGTERM.` |
| 1e — systemd timeout reconciliation | low | sonnet | none | In `sf-api.service`: change `TimeoutStopSec=30s` → `70s` (`:14`); add `--graceful-timeout 30` to the gunicorn `ExecStart` (`:23`); leave `--timeout 300`, `SuccessExitStatus`, `RestartPreventExitStatus` unchanged. Add a comment stating the invariant `TimeoutStopSec > API_DRAIN_GRACE + graceful_timeout + margin`. Do **not** touch `sf.service`. Commit subject: `deploy: reconcile sf-api drain and stop timeouts.` |
| 1f — functional test + docs | medium | sonnet | none | Add a `cluster_ci` / integration assertion: on a healthy node `/livez`→200 and `/readyz`→200; immediately after a SIGTERM to sf-api, `/readyz`→503 while `/livez` still →200, and the process stays up until the grace elapses. Update `ARCHITECTURE.md` (sf-api health surface), `AGENTS.md` (point at `external_api/health.py`), and `README.md` if it lists endpoints. Full operator LB-config docs are **phase 4** — keep this minimal. Commit subject: `tests, docs: sf-api health endpoints and drain.` |

## Step ordering and dependencies

- **1a first** — it creates `health.py`, which 1b, 1c and 1d
  all import.
- **1b, 1c, 1d** each depend only on 1a, not on each other, but
  run sequentially (single management session, one commit
  each).
- **1e** is independent (deploy file only) and can land any
  time after 1a defines `API_DRAIN_GRACE`.
- **1f** is last; it exercises the assembled behaviour.

## Success criteria

- `/livez`, `/readyz`, `/healthz` exist on port 13000,
  unauthenticated (no JWT, no 401), with the exact minimal
  bodies.
- A burst of `/readyz` makes **zero** DB/gRPC calls (all
  contact is in the 5 s background checker) — verifiable by
  inspection and a test that asserts the handler issues no
  `Check`.
- `/readyz` is 503 at startup until the checker confirms
  sf-database SERVING, flips to 200 when ready, and back to 503
  after K=3 failed checks (no flap on a single hiccup).
- A SIGTERM flips `/readyz` to 503 **before** gunicorn begins
  stopping workers; the process keeps serving for
  `API_DRAIN_GRACE` then exits via gunicorn's normal graceful
  path; `TimeoutStopSec=70s` never SIGKILLs inside the grace.
- No `Watch` and no client `healthCheckConfig` were introduced.
- `pre-commit run --all-files` passes (flake8, stestr, mypy);
  the `health.py` state machine has unit coverage.

## Back brief

Before executing, back-brief the operator: confirm the step
breakdown and the deviation from the master plan's "worktree
for phase 1" (sequential dependent steps → main tree, reviewed
and committed one at a time). Confirm the readiness constants
(5 s / K=3 / 15 s) and `API_DRAIN_GRACE=25s` / `TimeoutStopSec=70s`
are still wanted given the deployed LB's real probe interval.

## Review checklist for the management session

Standard checklist from the master plan, plus:

- [ ] `/readyz` handler touches only the in-memory flag — grep
      the handler for any `Check`/`mariadb.`/`stub.` call
      (there must be none).
- [ ] Initial readiness is **not-ready**, so a worker that
      cannot yet reach sf-database reports 503 rather than a
      false 200.
- [ ] The SIGTERM handler is installed in `post_worker_init`
      (survives `init_signals`) and does not block the worker
      (timer thread, no `sleep`).
- [ ] `_is_health_probe()` and the `log_request` downgrade both
      cover all three new paths, so probes don't write eventlog
      audit events or spam INFO logs.
- [ ] `TimeoutStopSec (70) > API_DRAIN_GRACE (25) +
      graceful_timeout (30) + margin`; `sf.service` untouched.
- [ ] The functional test actually sends SIGTERM and observes
      the 503-before-exit, not just a unit-level flag flip.
