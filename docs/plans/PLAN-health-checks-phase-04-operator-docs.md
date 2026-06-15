# Phase 4: operator documentation and rolling-upgrade-with-drain

## Context

This is phase 4 (final) of [`PLAN-health-checks.md`](PLAN-health-checks.md).
Phases 1–3 built the machinery; this phase makes it usable and
verifiable by operators: the load-balancer probe configuration
and example configs, the rolling-upgrade-with-drain procedure,
and the end-to-end CI test that proves a node drains out of the
pool on SIGTERM before it stops serving (the assertion phase 1f
deferred).

The shape of the deliverables is set by two facts found while
planning:

1. **The doc homes already exist.** `docs/operator_guide/load_balancing.md`
   already explains *why* you put an LB in front of sf-api and
   that SF ships none; it just lacks the *health-probe* config.
   `docs/operator_guide/upgrades.md` already describes online
   upgrades; it lacks the drain-aware rolling procedure (and
   still carries stale etcd-era prose). We extend both rather
   than add pages, so `mkdocs.yml.tmpl` nav needs no change.
2. **The cluster_ci Python harness cannot restart a daemon** —
   `BaseTestCase` talks to the cluster only through
   `system_client` (the API), with no SSH/systemctl seam. So the
   end-to-end drain test is **not** a pytest. It is a node-level
   shell script in `tools/`, invoked from `functional-tests.yml`
   via the existing `tools/run_remote ${primary} "sudo bash
   tools/<script>.sh"` pattern (the same mechanism as
   `tools/ci_log_checks.sh` / `tools/ci_event_checks.sh`). This
   matches CLAUDE.md's "no large scripts inline in CI — put them
   in `tools/`" rule. (The drain *handler* logic already has
   unit coverage in `test_gunicorn_drain.py`; this phase adds
   the live, on-a-node proof.)

## Key references

- `docs/operator_guide/load_balancing.md` — existing page (port
  13000, plain HTTP, operator-provided LB, TLS terminated at the
  LB). Extend with the health-probe section + examples.
- `docs/operator_guide/upgrades.md` — existing page; extend with
  the drain-aware rolling procedure. Note: its opening prose
  still describes reading/writing objects "from etcd" — etcd is
  gone (byo-mariadb); flag/fix the stale references touched by
  the new section, but a full rewrite of the page is out of
  scope.
- The behaviour to document (all already implemented):
  - `/livez` (always 200 `ok`), `/readyz` (200 `ready` / 503
    `not ready`), `/healthz` (alias of `/readyz`), unauthenticated
    on 13000. **The LB probes `/readyz`** (routing = readiness).
  - SIGTERM → `/readyz` 503 first → serve `API_DRAIN_GRACE`
    (default 25s) → gunicorn graceful shutdown (`--graceful-timeout
    30`) → exit, with `TimeoutStopSec=70s` as the systemd cap.
  - sf-database exposes `grpc.health.v1` (probe with
    `grpc-health-probe`), already documented in
    `docs/operator_guide/database.md`.
  - The routing principle: the LB routes to **sf-api only**;
    other daemons are internal (gRPC / mesh / WATCHDOG).
  - The two PKI domains (edge cert the LB terminates vs mesh
    mTLS) — health rides the existing LB→sf-api leg (phase-0 OQ9).
- `.github/workflows/functional-tests.yml` — the `tools/run_remote
  ${primary} "..."` steps near the end (log checks ~`:544`, the
  failure-grep at ~`:553`, event checks ~`:590`). The new drain
  step plugs in here.
- `tools/ci_log_checks.sh`, `tools/ci_event_checks.sh`,
  `tools/run_remote` — the precedent for a node-level CI check.

## Inherited decisions

- The LB probes `/readyz`; 200 = in rotation, 503 = drain. The
  drain window (`API_DRAIN_GRACE=25s`) assumes a ~10s LB probe
  interval — the docs must tell operators to set their probe
  interval and unhealthy-threshold so the LB notices the 503
  within the grace, and (phase-0 D6) a generous start-period so
  a slow first boot is not read as failure.
- nginx **FOSS** has only passive health checks (active
  `health_check` is NGINX Plus); the docs must say so and give
  the passive (`max_fails`/`fail_timeout`) pattern, not pretend
  FOSS can actively poll `/readyz`.

## Step-level guidance

Sequential where dependent; isolation `none`; one commit each.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a — LB health-probe docs + example configs | medium | opus | none | Extend `docs/operator_guide/load_balancing.md` with a "Health checks" section: the three endpoints and their codes; that the LB should health-check **`/readyz`** (200 = route, 503 = drain) and that `/livez` is for an orchestrator/`systemctl`, not the LB; the unauthenticated-but-firewall-to-the-LB-subnet note; and that probes are cheap (cached, no DB hit). Then **example configs for all three** the master plan requires: **HAProxy** (`backend` with `option httpchk GET /readyz` + `http-check expect status 200`, an `inter`/`fall`/`rise` tuned so the 503 is seen within `API_DRAIN_GRACE`); **nginx (FOSS)** — be correct: FOSS has only *passive* checks, so show `upstream` with `max_fails`/`fail_timeout` and `proxy_next_upstream`, and explicitly state active `/readyz` polling needs NGINX Plus (or an external prober); **one cloud LB** — AWS ALB target group (health-check path `/readyz`, success matcher `200`, interval/threshold guidance), noting TLS is terminated at the LB (edge cert) per the two-PKI model. Keep examples minimal and copy-pasteable. Verify the HAProxy/nginx directive names are real. Commit subject: `docs: load-balancer health-check configuration for sf-api.` |
| 4b — rolling-upgrade-with-drain procedure | medium | sonnet | none | Extend `docs/operator_guide/upgrades.md` with a "Rolling upgrade with drain" section: the per-node loop — (1) `systemctl stop sf-api` (SIGTERM) → `/readyz` flips to 503 → the LB drains the node on its next probe → in-flight requests finish within the grace; (2) upgrade the node's venv; (3) `systemctl start sf-api` → `/readyz` returns 200 → back in rotation — repeated node by node for zero-downtime. Cover the **ordering with schema migration**: run `sf-ctl ensure-mariadb-schema` (operator-driven, byo-mariadb) **before** rolling the daemons, since sf-database refuses to start on a stale schema. Note that rolling the elected `sf-cluster` triggers the watchdog/lease failover to a standby (phase 3), and that non-sf-api daemons are not LB-probed so they just stop/start. While here, fix the stale "from etcd / written back to etcd" prose in the section you touch (MariaDB, not etcd). Commit subject: `docs: rolling-upgrade-with-drain procedure.` |
| 4c — end-to-end drain CI check | high | opus | none | Add `tools/ci_drain_check.sh` (a node-level bash script, run as root on a cluster node) that proves the drain live: (1) assert `curl -s -o /dev/null -w '%{http_code}' http://localhost:13000/readyz` is `200` and `/livez` is `200`; (2) start a tight background poller of `/readyz`; (3) `systemctl stop sf-api &` (SIGTERM → drain); (4) assert that within the `API_DRAIN_GRACE` window `/readyz` returns **503 while `/livez` still returns 200 and the gunicorn process is still up** (the drain window — readiness flipped before shutdown); (5) wait for the stop to complete; (6) `systemctl start sf-api` and poll until `/readyz` is `200` again, so the node is left healthy. Make it robust: bounded timeouts, clear pass/fail echo + non-zero exit on failure, and **always** restart sf-api on exit (trap) so a failure does not leave the node down. Invoke it from `functional-tests.yml` via `tools/run_remote ${primary} "sudo bash tools/ci_drain_check.sh"` at a safe point — **before** the log/event-failure-grep steps would otherwise flag the expected sf-api stop, and confirm the clean SIGTERM stop (exit 143, `SuccessExitStatus`) does not match the failure greps at `functional-tests.yml:~553` (`Main process exited`, `stop-sigterm.* timed out`); if it would, place the drain step after, or add a scoped allowance. Read `tools/ci_log_checks.sh` and an existing `run_remote` step for the exact invocation idiom. Commit subject: `ci: end-to-end sf-api drain check on a live node.` |
| 4d — mark plan complete + final sweep | low | sonnet | none | Flip phase 4 to Complete in the master plan execution table and `docs/plans/index.md`, and add the master plan's "all phases complete" note. Re-read the master plan's Success criteria and confirm each is met (or note any residual), and ensure `README.md` / `ARCHITECTURE.md` / `AGENTS.md` mention the health surface (most was added in phases 1–3 — fill any gap, e.g. a one-line README pointer). Commit subject: `plans: mark health-checks complete.` |

## Step ordering and dependencies

- **4a** and **4b** are doc-only and independent (different
  pages); either order.
- **4c** is independent of the docs but is the riskiest (it
  manipulates a live CI node) — land it after 4a/4b so the
  branch already carries the operator-facing description of what
  it verifies.
- **4d** is last; it confirms the whole plan's success criteria.

## Success criteria

- `docs/operator_guide/load_balancing.md` documents probing
  `/readyz` and carries correct, minimal HAProxy, nginx-FOSS,
  and one-cloud-LB examples (with the nginx-FOSS passive-only
  caveat stated).
- `docs/operator_guide/upgrades.md` documents the
  rolling-upgrade-with-drain procedure including the
  ensure-mariadb-schema ordering, and no longer claims objects
  live in etcd in the touched section.
- `tools/ci_drain_check.sh` exists, is invoked from
  `functional-tests.yml`, proves `/readyz`→503-before-exit on a
  live node, and always restarts sf-api so the cluster is left
  healthy; it does not trip the CI log/event failure greps.
- The master plan and `index.md` show all phases Complete, and
  the master plan's Success criteria are each met or have a
  recorded residual.
- `pre-commit run --all-files` passes; `actionlint` accepts the
  workflow change.

## Back brief

Before executing, back-brief the operator: confirm the
three LB examples to ship (HAProxy, nginx-FOSS, AWS ALB — or a
different cloud LB if preferred), the decision to extend the
existing pages rather than add new ones, and the approach to the
CI drain check (node-level `tools/` script via `run_remote`,
self-healing restart) given the harness cannot restart daemons
from pytest. Flag that the drain step manipulates a live CI
node and must be sequenced so it neither breaks concurrent
checks nor trips the failure greps.

## Review checklist for the management session

Standard checklist from the master plan, plus:

- [ ] The LB examples use real directive names and the
      nginx-FOSS active-vs-passive caveat is stated (no
      pretending FOSS can poll `/readyz`).
- [ ] The upgrade doc has the schema-migration-before-daemons
      ordering and drops the stale etcd prose it touches.
- [ ] `ci_drain_check.sh` is bounded, self-healing (restarts
      sf-api on any exit path), and asserts 503-while-still-up
      (not merely 503 after exit).
- [ ] The drain CI step does not cause `functional-tests.yml`'s
      log/event failure greps to fire on the expected clean
      stop/start.
- [ ] All phases show Complete and the master plan's Success
      criteria are revisited.
