# Phase 3: Stand up Loki in CI and prove the push path

## Context

This is phase 3 of
[`PLAN-remove-syslog-forwarding.md`](PLAN-remove-syslog-forwarding.md).
It stands up a single-binary Loki for the functional CI cluster
the same way MariaDB is stood up, plumbs `LOKI_BASE_URL` into the
inner cluster's SF config, and adds a functional test that
asserts SF log lines actually reach Loki end-to-end. This is the
**only functional coverage of the new, riskiest code** (the
phase-2 spool → drainer → HTTP push), so it is the proof the
shipper works.

It depends on phase 2 (the shipper exists). It does **not** rework
the existing syslog-grep CI checks or the clingwrap bundle — that
is phase 4 — and it does not remove rsyslog (phase 5).

The phase-0 **D7** decision is the foundation; this plan grounds
it in current file:line anchors and resolves one thing D7 left
open: *how* `LOKI_BASE_URL` reaches the inner cluster. It uses the
**config-template env path** (render `SHAKENFIST_LOKI_BASE_URL`
into `/etc/sf/config`, which daemons read at startup, so
`logship.start()` sees it) rather than the `set-config`/
`GETSF_EXTRA_CONFIG` shortcut — `set-config` writes a DB
cluster-config value that a running daemon would only pick up on
restart, whereas `logship.start()` reads `config.LOKI_BASE_URL`
once at startup. The env path is therefore the reliable one and is
also the operator-facing plumbing, so it **realizes the master
plan's phase-5 "render the Loki endpoint config from an inventory
variable" line** — phase 5 then reduces to rsyslog removal +
switching CI to the phase-4 checks + docs.

## Key references in the existing code

- **Install helper to mirror:** `tools/ci-install-mariadb.sh`
  (`#!/bin/bash`, `set -euo pipefail`, positional args, apt-lock
  retry, `systemctl is-active` readiness poll, config drop-in,
  restart+verify). The new `tools/ci-install-loki.sh` mirrors its
  structure.
- **Workflow wiring** (`.github/workflows/functional-tests.yml`,
  parallel in `scheduled-tests.yml`):
  - MariaDB install step `:330-346` — `scp` the helper + assets to
    `${primary}`, `ssh ... 'sudo bash /tmp/ci-install-mariadb.sh ...'`.
    The Loki install step is added right after.
  - getsf invocation `:348-361` — `GETSF_MARIADB_*` env on
    `/tmp/getsf-wrapper`. `GETSF_LOKI_BASE_URL` is added here.
  - Merge/multi-node block `:890-903` — the topology-conditional
    host pattern, e.g. `${{ matrix.merge.topology == 'slim-tier'
    && '10.0.1.10' || '127.0.0.1' }}`. Mirror for the Loki host.
  - Functional test run `:407-432` — tests execute **on the inner
    primary node** over ssh (`cd shakenfist/deploy; stestr run
    --config <conf>`), so a test there reaches Loki at
    `localhost:3100`.
- **Config plumbing chain:**
  - `shakenfist/deploy/getsf` — MariaDB env reads `:534-614`;
    `GETSF_EXTRA_CONFIG` `:628-637`; the `/root/sf-deploy` export
    block `:958-983` (e.g. `export MARIADB_HOST=...`).
  - `shakenfist/deploy/ansible/deploy.py` —
    `update_if_specified(varname, default)` `:125-135` (reads the
    UPPER-cased env var into `variables[varname]`); the MariaDB
    block `:165-184`; `extra_config` `:198`; writes
    `/etc/sf/deploy-vars.json` `:200-201`.
  - `shakenfist/deploy/ansible/roles/base/templates/config` —
    `SHAKENFIST_MARIADB_GATEWAY_HOSTS` is rendered
    **unconditionally** `:34`; `SHAKENFIST_MARIADB_HOST` is gated
    on the `etcd_master` group `:41-47`. The new
    `SHAKENFIST_LOKI_BASE_URL` goes in **unconditionally** (all
    daemons push), rendered only when the var is set.
  - `shakenfist/deploy/ansible/roles/primary/tasks/cluster_config.yml`
    `:55-65` — the `extra_config` → `sf-ctl set-config` loop (the
    shortcut we are deliberately *not* using; noted for context).
- **Functional test harness:**
  `shakenfist/deploy/shakenfist_ci/` with `smoke_ci_tests/`
  (localhost topology, `smoke-ci.conf`), `cluster_ci_tests/`
  (multi-node), `guest_ci_tests/`; framework is `stestr` +
  `testtools`; `base.py` `BaseTestCase` builds an
  `apiclient.Client()` (`SHAKENFIST_API_URL` or
  `http://127.0.0.1:13000`); example
  `smoke_ci_tests/test_events.py` shows the eventual-consistency
  poll pattern (deadline loop). The functional PR job runs the
  suite named by `matrix.pr.stestr_config`.
- **Two-layer topology:** the under-cloud hosts the inner cluster;
  inner egress IPs are `10.0.0.x` (primary `10.0.0.10`,
  hypervisors `10.0.0.20-24`), inner mesh IPs are `10.0.1.x`
  (primary mesh `10.0.1.10`). Topology playbooks live in the
  sibling `shakenfist/actions` repo
  (`ansible/ci-topology-{localhost,slim-primary,slim-tier}.yml`).

## Design decisions

### Loki placement: always on the primary

Run Loki on the **primary node** in every topology (it is
independent of where MariaDB lands). The functional tests run on
the primary, so they query Loki at `http://localhost:3100`; the
other inner nodes push to the primary's reachable address. This
is simpler than mirroring MariaDB's tier placement and keeps the
install step's target constant (`${primary}`).

`LOKI_BASE_URL` value (the cluster-wide push target rendered into
every node's config):
- **localhost** topology: `http://127.0.0.1:3100`.
- **multi-node** (slim-primary / slim-tier): the primary's mesh
  IP, `http://10.0.1.10:3100`. The implementing agent must
  **confirm the primary's mesh IP** from the `shakenfist/actions`
  `ci-topology-*.yml` files before wiring the conditional.

### LOG_EVENTS_TO_LOKI stays on (the phase-2 default)

The functional run uses the shipped default (`True`), so the
`'Added event'` lines reach Loki — this is what the phase-4
Loki-based log-checks will rely on. No explicit override needed.

### Plumbing: first-class `LOKI_BASE_URL` only (tenant/auth deferred)

Phase 3 wires only `LOKI_BASE_URL` through getsf → deploy.py →
config template. `LOKI_TENANT` / `LOKI_AUTH_HEADER` plumbing is
deferred to phase 5/6 (operator deploy + docs) — CI's Loki is
single-tenant and unauthenticated, and `LOKI_AUTH_HEADER` is a
secret whose all-node rendering wants its own consideration. The
phase-2 config options already exist; they simply stay at their
empty defaults until wired.

## Step items

### 3a — `tools/ci-install-loki.sh`

Mirror `tools/ci-install-mariadb.sh` (same shebang, `set -euo
pipefail`, readiness-poll style). It must:
- Download a pinned single-binary Loki release
  (`github.com/grafana/loki/releases`, `loki-linux-amd64.zip`) to
  `/usr/local/bin/loki` (pick a recent stable, e.g. a 3.x; record
  the version + sha in the script). Any Loki version is fine —
  phase 2 puts identifiers in the JSON line body, not structured
  metadata, so there is no schema-version floor.
- Write a minimal single-binary config to `/etc/loki/config.yaml`:
  `auth_enabled: false`; server `http_listen_address: 0.0.0.0`,
  `http_listen_port: 3100`; filesystem storage under a writable
  path (e.g. `/var/lib/loki`); a tsdb schema (`v13`). Use a
  known-good minimal config for the pinned version.
- Install a systemd unit `loki.service`, `systemctl enable
  --now loki`, and poll `http://localhost:3100/ready` until it
  returns `ready` (mirror the MariaDB `is-active` poll: bounded
  attempts, short backoff).
- shellcheck-clean (the repo now lints `tools/` — see the
  workflow-standards work; match `flake8wrap.sh`'s SC-handling
  style where applicable).

### 3b — Config plumbing for `LOKI_BASE_URL`

Three small edits mirroring the MariaDB plumbing:
- `getsf`: add a `GETSF_LOKI_BASE_URL` read (optional, default
  empty) near the MariaDB block (`:534-614`), and `export
  LOKI_BASE_URL="${GETSF_LOKI_BASE_URL}"` in the `/root/sf-deploy`
  block (`:958-983`).
- `deploy.py`: add `update_if_specified('loki_base_url', '')` in
  the config-reading block (near `:169`). It is **optional** —
  do **not** add a required-value check (empty = shipper
  disabled).
- `roles/base/templates/config`: add, **unconditional** (after
  the MariaDB block at `:34-47`), rendered only when set:
  ```
  {% if loki_base_url %}
  SHAKENFIST_LOKI_BASE_URL="{{ loki_base_url }}"
  {% endif %}
  ```

### 3c — Workflow wiring

In `functional-tests.yml` (and the parallel spots in
`scheduled-tests.yml`):
- After the MariaDB install step (`:330-346`), add an "Install
  Loki on primary" step: `scp` `tools/ci-install-loki.sh` (and
  any config asset) to `${primary}:/tmp/`, then `ssh ... 'sudo
  bash /tmp/ci-install-loki.sh'`.
- In the getsf invocation env (`:348-361`, and the merge block
  `:890-903`), add `GETSF_LOKI_BASE_URL=http://<host>:3100` with
  the topology-conditional host (127.0.0.1 for localhost; primary
  mesh IP for multi-node — confirmed in 3a/3b research).
- Keep top-level `permissions:` blocks intact; the change must
  pass `actionlint` (the repo's pre-commit lints workflows).

### 3d — The "logs reach Loki" functional test

Add `test_loki.py` to the suite that runs in the Loki-enabled
functional topology (confirm whether the PR functional job uses
`smoke-ci.conf`/`smoke_ci_tests` and place it there; it must run
on every PR). The test:
- Skips cleanly if `LOKI_BASE_URL` is not configured (so it is a
  no-op in any topology where Loki was not stood up) — e.g. check
  an env/marker or `GET /ready` on `http://localhost:3100` and
  `self.skipTest(...)` if unreachable.
- Performs an action that reliably emits a log line carrying a
  known token — simplest is creating a network or instance, whose
  UUID then appears in operational logs and (with
  `LOG_EVENTS_TO_LOKI` on) `'Added event'` lines.
- Polls Loki `GET http://localhost:3100/loki/api/v1/query_range`
  with `query={job="shakenfist"} |= "<uuid>"`, `start`/`end`
  bracketing the action (nanosecond unix), `limit` modest, under
  an eventual-consistency deadline (~30s, mirroring
  `test_events.py`), and asserts the result has at least one
  stream/value. Optionally assert a label (e.g. `daemon`) is
  present on the returned stream to prove the label contract.
- Uses `requests` (a project dependency) and the existing
  `BaseTestCase`/`apiclient` patterns for the setup action and
  cleanup.

### 3e — Deferred: disabled-shipper local-JSON coverage

D7 also called for a cheap test that the disabled-shipper path
emits *local JSON*. That asserts the JSON-only library behavior,
which is only true once `shakenfist-utilities==0.9.0` is pinned
(under the current `0.8.4`, local output is text). **Defer** this
until the phase-1 pin bump lands; note it here so it is not lost.
(Phase 2 already covers the Mode-B "no Loki handler" path in unit
tests.)

## Step-level guidance

Phase 3's real validation is CI itself (the two-layer cluster
can't be fully run locally). Locally the agent can: shellcheck
`ci-install-loki.sh`, `actionlint` the workflows, flake8 the test,
and optionally smoke `ci-install-loki.sh` against a throwaway
local Loki. Otherwise correctness is established by careful review
+ a CI run.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Write `tools/ci-install-loki.sh` mirroring `ci-install-mariadb.sh`: download a pinned single-binary Loki to `/usr/local/bin/loki`, write a minimal filesystem `/etc/loki/config.yaml` (auth off, listen 0.0.0.0:3100, tsdb v13), install+enable a `loki.service`, poll `/ready`. shellcheck-clean. |
| 3b | medium | sonnet | none | Plumb `LOKI_BASE_URL` (only) through `getsf` (`GETSF_LOKI_BASE_URL` read + `export LOKI_BASE_URL`), `deploy.py` (`update_if_specified('loki_base_url','')`, optional — no required check), and `roles/base/templates/config` (unconditional `SHAKENFIST_LOKI_BASE_URL`, rendered only when set). Mirror the MariaDB plumbing exactly; confirm the primary mesh IP from `shakenfist/actions/ci-topology-*.yml`. |
| 3c | medium | sonnet | none | Add the Loki install step (scp+ssh, after MariaDB) and `GETSF_LOKI_BASE_URL` (topology-conditional host) to `functional-tests.yml` and `scheduled-tests.yml`. Must pass actionlint; keep `permissions:` blocks. |
| 3d | high | opus | none | Add `test_loki.py` to the functional suite that runs with Loki: create a network/instance, poll `query_range` for `{job="shakenfist"} \|= "<uuid>"` under a ~30s deadline, assert arrival + a label; skip cleanly if Loki isn't reachable. Mirror `test_events.py` patterns and `BaseTestCase`/`apiclient`. |

## Step ordering and dependencies

- 3a and 3b are independent and can land together.
- 3c depends on 3a (it invokes the helper) and 3b (it sets
  `GETSF_LOKI_BASE_URL`, consumed by the 3b plumbing).
- 3d depends on 3a–3c being in place in CI to actually pass, but
  the test code can be written in parallel and validated by review
  + the CI run.
- One commit per logical unit at minimum: the install helper; the
  config plumbing; the workflow wiring; the test.

## Success criteria

- A single-binary Loki is stood up on the primary in the
  functional CI topology, listening on `:3100`, via
  `tools/ci-install-loki.sh`.
- `LOKI_BASE_URL` is rendered into every inner node's
  `/etc/sf/config` (`SHAKENFIST_LOKI_BASE_URL`) from
  `GETSF_LOKI_BASE_URL`, so daemons ship to Loki from startup.
- A functional test creates an object and asserts its log lines
  arrive in Loki (queried via `query_range`) with the expected
  `{job, daemon, host}` labels — the end-to-end proof of the
  phase-2 push path. The test skips cleanly where Loki is absent.
- `actionlint` (workflows) and `shellcheck` (`ci-install-loki.sh`)
  pass; the test passes flake8; `pre-commit run --all-files` is
  green.
- No rsyslog wiring removed, no existing CI log-checks changed
  (phases 5 / 4). `LOKI_TENANT`/`LOKI_AUTH_HEADER` deploy plumbing
  deferred.

## Back brief

Before executing, back-brief the operator on: Loki-on-the-primary
placement; the config-template env path (vs `set-config`) and
that it realizes phase 5's "render Loki endpoint config" line so
phase 5 shrinks accordingly; the deferral of `LOKI_TENANT`/
`LOKI_AUTH_HEADER` deploy plumbing and the disabled-shipper
local-JSON test (the latter blocked on the `v0.9.0` pin); and that
phase 3's true validation is a CI run, not local execution.

## Review checklist for the management session

- [ ] `ci-install-loki.sh` mirrors the MariaDB helper's
      structure, pins a Loki version, binds `0.0.0.0:3100`, and
      waits for `/ready`; shellcheck-clean.
- [ ] `SHAKENFIST_LOKI_BASE_URL` is rendered unconditionally (all
      nodes), only when set; the getsf→deploy.py→template chain
      matches the MariaDB pattern; `loki_base_url` is optional (no
      required check).
- [ ] Workflow host conditional uses the correct primary address
      per topology (127.0.0.1 vs the confirmed primary mesh IP);
      both `functional-tests.yml` and `scheduled-tests.yml`
      updated; actionlint green.
- [ ] The functional test proves arrival via `query_range`,
      handles eventual consistency with a deadline, asserts a
      label, and skips cleanly when Loki is absent.
- [ ] Nothing in phases 4/5 was pulled in (no log-check rework, no
      rsyslog removal); tenant/auth plumbing not added.
