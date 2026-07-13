# Rust proxy phase 7: CI lane + loadtest

Part of the [Rust SPICE proxy master plan](/components/kerbside/plans/PLAN-rust-proxy/). Phases
1–6 built the Rust proxy, made the daemon supervise it, and packaged it
as an installable wheel. Phase 7 proves it in CI: the direct-qemu
functional lane runs against the **real** daemon-supervised Rust proxy
(not the phase-3/4/5 mock), passing the same assertion oracle the Python
proxy passes, plus an API-terminate end-to-end test the Python proxy
cannot satisfy, and a non-gating latency loadtest that quantifies the
performance claim.

## Prompt

Ground every decision in the existing harness. Read
`.github/workflows/direct-qemu-functional.yml` (the working Python lane),
and the `tools/direct-qemu/` scripts it calls: `lane-up.sh` (TLS → qemu →
`start-kerbside.sh` → fetch `.vv` from the REST API → launch ryll
headless → control socket), `start-kerbside.sh` (runs `kerbside daemon
run`, which today launches the Python proxy), `run-scenario.sh` (the
tempest `test_sextant_scenario` assertion oracle), `smoke-client.py`,
`lane-down.sh`, and the Rust-proxy-specific `start-rust-proxy.sh` /
`verify-rust-proxy.sh` / `mock-grpc-server.py` (local, mock-based, NOT
CI). Read `kerbside/config.py` (`PROXY_IMPLEMENTATION`),
`kerbside/proxy_supervisor.py` (`find_proxy_bin()`), and the phase-5/6
Outcomes for what was explicitly deferred here. Verify the process model
rather than assuming it. Flag uncertainty explicitly.

Planning effort: **medium** — CI wiring over an existing, well-understood
harness; the subtlety is the terminate-in-flight timing and keeping the
loadtest non-flaky.

## Repository and branch logistics

All work is in **kerbside** (the `tools/direct-qemu/` harness, the
`.github/workflows/` lane, and a new loadtest script); no other repo
changes. Phase 7 builds on phase 6 (it installs the phase-6 wheel in the
lane), which is unmerged, so branch `rust-proxy-phase-7` from the phase-6
tip; rebase onto `develop` once phases 2–6 land. This plan file lives on
the phase-7 branch.

## Situation (verified 2026-07-08)

- **The existing direct-qemu lane is proxy-agnostic.**
  `direct-qemu-functional.yml` (on `[self-hosted, vm, debian-12, l]`,
  `pull_request` → develop) installs system packages + a Rust toolchain,
  builds `ryll` from source, `pip install .`s kerbside into a venv,
  initialises MariaDB (`setup-mariadb.sh`), brings up the lane
  (`lane-up.sh`), runs `smoke-client.py`, asserts the Sextant boot
  banner, and runs the tempest scenario (`run-scenario.sh`).
- **`start-kerbside.sh` runs `kerbside daemon run` (line 126).** With the
  default config that supervises the **Python** proxy. It exports a set
  of `KERBSIDE_*` env vars (pydantic-settings reads them), runs
  `alembic upgrade head`, starts gunicorn for the REST API, then the
  daemon, and polls the API + the SPICE proxy port (5901).
- **The `.vv` endpoint and ryll do not care which proxy is running.**
  `lane-up.sh` fetches `/console/proxy/<source>/<uuid>/console.vv` from
  the REST API (which points ryll at the proxy's VDI ports), launches
  `ryll --headless --file console.vv --control-socket ... --enable-paste
  -as-keystrokes`, and waits for the control socket. `run-scenario.sh`
  drives the guest through the tempest plugin's `test_sextant_scenario`
  against that control socket, asserting on the visual digest + serial
  drain. **None of this changes with the proxy implementation** — the
  proxy is transparent to the client and the `.vv`.
- **Therefore the Rust lane is the same flow with two changes:** set
  `KERBSIDE_PROXY_IMPLEMENTATION=rust`, and make the `kerbside-proxy`
  binary resolvable by `find_proxy_bin()`
  (`env KERBSIDE_PROXY_BIN → shutil.which('kerbside-proxy') → dev tree`).
- **What phases 5/6 deferred here (verified against their Outcomes):**
  the phase-5 Outcome records that "the full daemon+API+MariaDB path with
  a deterministic headless client (ryll) is … the natural phase-7
  CI-lane driver," and that live in-flight **termination** was proven
  only through the *mock's* `ProxyControl` — the Python half (intent
  table, API write, node-scoped selection, reaper) was unit-tested, not
  run end to end. Phase 6 verified the wheel/`find_proxy_bin` path
  locally, not in CI.
- **The Rust-proxy local harness is mock-based.** `start-rust-proxy.sh`
  launches the binary against `mock-grpc-server.py`, and
  `verify-rust-proxy.sh` asserts via `/metrics`. This is NOT the daemon
  path and is not what phase 7 runs in CI (phase 7 uses the real daemon);
  the mock harness stays for local, MariaDB-free verification.
- **`smoke-client.py` and `run-scenario.sh` are the oracles.** The
  scenario test is destructive (drives the guest to ACPI shutdown) and
  MUST be the last lane step. The loadtest therefore cannot share a lane
  with the scenario test — it needs its own lane bring-up (or to run
  before the scenario against a fresh connection).
- **`find_proxy_bin()` middle leg is `shutil.which('kerbside-proxy')`.**
  Installing the phase-6 wheel into the kerbside venv puts the binary on
  the venv's `bin/` (on `PATH`), so the real production resolution path
  is exercised with no env override.

## Mission

1. **Parameterise the harness** so a lane runs either proxy, selected by
   one env knob, with the Rust binary delivered by installing the phase-6
   wheel. No behaviour change for the existing Python lane.
2. **CI parity lane**: `direct-qemu-functional.yml` becomes a matrix
   `proxy: [python, rust]`; both legs run the identical smoke + banner +
   scenario steps. The Rust leg is the full daemon+API+MariaDB+ryll
   integration test deferred from phases 5/6 — the Rust proxy passing the
   exact oracle the Python proxy passes **is** the parity proof.
3. **API-terminate end-to-end test** (Rust leg only): with the real
   daemon+API+DB+Rust proxy and a connected ryll client, terminate the
   session via the REST API and assert the in-flight connection drops.
   This exercises the whole phase-5 bridge live (API →
   `session_terminations` → node-scoped `ProxyControl` poll →
   `TerminateSession` → registry cancel → relay teardown), which was only
   ever run against the mock.
4. **Non-gating latency loadtest**: measure connection-setup latency and
   relay throughput for both proxies, record the numbers as a CI artifact
   and in the Outcome. Quantifies the performance claim; does not fail the
   build on a threshold.

Out of scope (later / other plans): making Rust the default and removing
the Python proxy (phase 8); musllinux/extra-arch wheels; OpenTelemetry; a
gating performance SLO (deliberately non-gating here — see decision 3).

## Design decisions

Decisions 1–3 are settled with the operator. The rest are the planner's
calls, open to revision at back brief.

**1. Matrix lane, one workflow (settled).** Convert
`direct-qemu-functional.yml` to `strategy.matrix.proxy: [python, rust]`.
Both legs share every step; the only differences are a couple of env
values and (Rust leg) installing the wheel + the extra terminate test.
This expresses parity directly — the same oracle, both proxies — and
keeps one lane to maintain. The Python leg's behaviour is unchanged
(it is the existing lane, now one matrix value). `fail-fast: false` so a
Rust-leg failure does not cancel the Python leg (and vice versa).

**2. Deliver the Rust binary by installing the phase-6 wheel (settled).**
On the Rust leg, `maturin build --release` the `kerbside-proxy` wheel
(native x86_64, no zig needed for a same-host CI wheel) and `pip install`
it into the kerbside venv, so `find_proxy_bin()` resolves it via
`shutil.which('kerbside-proxy')` — the real production path, with no
`KERBSIDE_PROXY_BIN` override. This gives phase 6's packaging its first
CI coverage as a side effect. (The plain `maturin build` is enough; the
manylinux/zig matrix is release-only and already covered by phase 6.)

**3. Loadtest measures and reports, does not gate (settled).** A new
`tools/direct-qemu/loadtest.py` (or `.sh`) drives N connection
setup/teardown cycles and a bounded bulk relay through the proxy,
recording p50/p95 connection-setup latency and relay throughput. The lane
runs it for both proxies and uploads the numbers as an artifact; the
Outcome records the comparison. No pass/fail threshold — comparative
timing on shared self-hosted runners is too noisy to gate on without
flaking.

**4. The terminate test drives the real REST API, not the mock.** Reuse
the lane's JWT-minting pattern (`lane-up.sh` already mints a token from
the persisted `AUTH_SECRET_SEED`) to call the session-terminate endpoint
(`POST` the same endpoint `api.py::SessionTerminate`/`ConsolesTerminate`
back), then assert the connected ryll client's session drops within a
bounded deadline. The oracle is the proxy log line
(`session terminated by control plane`) plus ryll exiting / the control
socket disappearing — mirroring `VERIFY-TERMINATION.md`, but end to end
through the DB intent table and the daemon's `ProxyControl` poll rather
than the mock's one-shot emitter. Because this test tears the session
down, it runs **before** the destructive scenario step, or in its own
lane bring-up. It is Rust-leg only (the Python proxy does not honour
in-flight termination, by design).

**5. Keep the mock harness.** `start-rust-proxy.sh` /
`verify-rust-proxy.sh` / `mock-grpc-server.py` remain for local
MariaDB-free verification; phase 7 adds the daemon-based CI path
alongside them, and documents the split (mock = local unit-ish
verification; CI matrix = real integration/parity).

## Open questions (to settle during the phase)

- **Loadtest driver**: reuse `smoke-client.py` (ryll control-socket
  based, one connection at a time) in a loop, vs a purpose-built
  concurrent SPICE connector. Lean: start with a sequential loop over
  `smoke-client.py`-style connects (setup-latency focus) plus a single
  bulk-relay throughput sample; concurrency is future work if the numbers
  need it.
- **Where the terminate test lives**: a dedicated lane bring-up
  (cleanest, but doubles qemu boot time) vs a pre-scenario step on the
  same lane (cheaper, but the scenario must still find the session
  usable — it will, since terminate drops only that connection and ryll
  can reconnect from the same `.vv`). Lean: pre-scenario step, reconnect
  ryll, then run the scenario; fall back to a dedicated lane if
  reconnection proves fragile.
- **Runner time budget**: the matrix doubles the lane's wall-clock
  (two qemu boots + two ryll runs). Confirm the `timeout-minutes: 60`
  headroom holds for the Rust leg (wheel build + extra terminate test);
  bump if needed.
- **Whether the loadtest runs on every PR** (cost) **or only on a label /
  schedule**. Lean: run a *short* loadtest inline on every PR (a few
  iterations, for the artifact) and leave a heavier sweep to
  `workflow_dispatch`.
- Exact `KERBSIDE_*` env the Rust leg needs beyond
  `PROXY_IMPLEMENTATION` (e.g. `PROMETHEUS_METRICS_ADDRESS` already
  defaults to loopback; the firewall knobs default to enforce — confirm
  the Sextant traffic passes the L1 allowlist in a real run, which the
  phase-4 `VERIFY-FIREWALL.md` capture suggests it does).

## Execution

One commit per logical change. Every Python/shell step passes
`tox -eflake8`/`-epy3` where it touches Python; `pre-commit run
--all-files` (which runs actionlint on the workflow YAML) before each
commit. CI-step bodies stay ≤5 lines; logic goes in `tools/` scripts.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | opus | none | **Parameterise the harness for proxy selection.** Add an opt-in `PROXY_IMPLEMENTATION` env knob to `tools/direct-qemu/start-kerbside.sh` (default `python`, unchanged behaviour): when `rust`, export `KERBSIDE_PROXY_IMPLEMENTATION=rust` before `kerbside daemon run`, and confirm the binary is resolvable (`command -v kerbside-proxy` or a `KERBSIDE_PROXY_BIN` override), failing fast with a clear message if not. Thread the knob through `lane-up.sh` (pass-through env). Do NOT change the Python path. Keep the existing port-poll readiness (5901) — it is proxy-agnostic. Update `VERIFY-RUST-PROXY.md`/a harness README note to describe the daemon-based path vs the mock path. No new qemu/ryll logic. |
| 7b | medium | opus | none | **Wheel build + install helper.** Add `tools/direct-qemu/install-proxy-wheel.sh` (or inline ≤5-line CI step calling the phase-6 `tools/build-proxy-wheel.sh` in a native-only mode) that `maturin build --release`s the `kerbside-proxy` wheel and `pip install`s it into the kerbside venv, then asserts `shutil.which('kerbside-proxy')` resolves. Prefer reusing phase-6 tooling; if `build-proxy-wheel.sh`'s `--zig`/manylinux path is too heavy for the lane, add a `--native` fast path to it rather than duplicating. Verify locally that after install, `find_proxy_bin()` returns the wheel binary with no env override. |
| 7c | high | opus | none | **Convert the lane to a `proxy: [python, rust]` matrix.** Edit `.github/workflows/direct-qemu-functional.yml`: add `strategy.matrix.proxy: [python, rust]`, `fail-fast: false`; gate the wheel-build/install step and the `PROXY_IMPLEMENTATION=rust` env on `matrix.proxy == 'rust'`; keep smoke + banner + scenario identical for both legs; namespace artifacts by `${{ matrix.proxy }}`. Ensure the Rust leg has the maturin prerequisite (a venv with maturin) and the toolchain (the lane already ensures cargo). Confirm `timeout-minutes` headroom. This is the parity proof: both legs must pass the same `run-scenario.sh`. Keep CI-step bodies ≤5 lines (call harness scripts). |
| 7d | high | opus | none | **API-terminate end-to-end test (Rust leg).** Add `tools/direct-qemu/verify-terminate-live.sh`: against a live Rust lane with a connected ryll client, mint a JWT (reuse `lane-up.sh`'s pattern), call the REST session-terminate endpoint, and assert the in-flight connection drops within a bounded deadline — oracle = the proxy log `session terminated by control plane` line and/or ryll exit / control-socket removal, per `VERIFY-TERMINATION.md`. Wire it into the matrix as a Rust-leg-only step that runs BEFORE the destructive scenario (reconnect ryll from the same `.vv` afterwards); fall back to a dedicated lane bring-up if reconnection is fragile (open question). This is the first end-to-end exercise of the phase-5 DB→ProxyControl bridge (previously mock-only). |
| 7e | medium | opus | none | **Non-gating latency loadtest.** Add `tools/direct-qemu/loadtest.py` (or `.sh`) that runs N connection setup/teardown cycles and a bounded bulk-relay sample through the proxy, printing p50/p95 setup latency and throughput as machine-readable lines. Add a matrix step that runs it for the leg's proxy (short inline iteration count on PRs; a heavier sweep left to `workflow_dispatch`), uploads results as an artifact, and NEVER fails on a threshold. Keep it robust to CI noise (warm-up, medians, generous per-op timeouts). Record the driver decision (reuse smoke-client vs bespoke). |
| 7f | medium | sonnet | none | **Docs + Outcome.** Update `AGENTS.md` (the direct-qemu section: the proxy matrix, the wheel-install path, the live terminate test, the loadtest), `README.md` (a line on the CI parity lane), and a `tools/direct-qemu/` VERIFY/README note distinguishing the mock harness from the CI daemon path. Write the phase-7 Outcome (including the recorded loadtest numbers) after the pre-push audit; flip the phase-7 row in `PLAN-rust-proxy.md` + `docs/plans/index.md` to Complete. |

Sequencing: 7a → 7b (harness plumbing) → 7c (matrix) is the backbone; 7d
and 7e both build on a working Rust lane and are independent of each
other; 7f last. Prove each lane change by running the harness locally
(the operator's host has KVM/qemu) before relying on CI, since
self-hosted-runner iteration is slow.

## Success criteria

- The direct-qemu lane runs as a `proxy: [python, rust]` matrix; **both
  legs pass the same `run-scenario.sh` oracle** (banner + Sextant
  scenario), proving functional parity.
- The Rust leg runs the **real daemon** (`PROXY_IMPLEMENTATION=rust`)
  supervising a **wheel-installed** `kerbside-proxy` resolved via
  `find_proxy_bin()` on `PATH`, against real MariaDB + the REST API +
  ryll — the integration test deferred from phases 5/6.
- Terminating a session via the REST API drops the in-flight ryll
  connection on the Rust leg, exercising the phase-5 DB→`ProxyControl`
  bridge end to end (not the mock).
- A latency loadtest produces recorded Python-vs-Rust numbers as a CI
  artifact; it does not gate the build.
- The Python leg is unchanged; `pre-commit` (flake8 + py3 + actionlint)
  is green; docs updated; pre-push audit clean.

## Future work (recorded)

- Gating performance SLO once the loadtest numbers are stable enough to
  set a non-flaky threshold.
- Concurrent-connection loadtest (many simultaneous SPICE clients) if the
  sequential numbers do not fully characterise the performance claim.
- Fold the mock-based `verify-rust-proxy.sh` metrics assertions into a
  cheap non-qemu unit lane if useful.
- Phase 8 removes the Python proxy; at that point the matrix collapses
  back to a single (Rust) lane and the Python-specific harness paths are
  deleted.

## Outcome

Completed 2026-07-08 on the kerbside `rust-proxy-phase-7` branch,
unmerged and unpushed pending operator review. All planned steps landed.
Harness logic was verified locally where it can be without the full
qemu+MariaDB+ryll stack; the end-to-end matrix lane itself runs on the
self-hosted CI runner and is confirmed when the branch is pushed/PR'd
(the same mock-vs-live honesty as phases 5/6).

- 7a (`b49798a`): the `PROXY_IMPLEMENTATION` knob in `start-kerbside.sh`
  (default `python`, unchanged), which for `rust` exports the daemon's
  config env and pre-checks the binary by calling the daemon's own
  `find_proxy_bin()` (no bash reimplementation of the resolution),
  inherited through `lane-up.sh`. Verified: `bash -n`; `find_proxy_bin()`
  imports and resolves the wheel binary on PATH (last-line capture, since
  kerbside logs to stdout on import); PATH wins over the dev-tree
  fallback, as CI needs.
- 7b (`ebf201f`): a `--native` fast path on `build-proxy-wheel.sh` (plain
  host build, no zig/manylinux/rustup) and `install-proxy-wheel.sh`, which
  builds that wheel and pip-installs it into a venv, asserting
  `shutil.which('kerbside-proxy')` resolves. Verified end to end into a
  fresh venv locally.
- 7c (`b54c40b`): the `proxy: [python, rust]` matrix (`fail-fast: false`)
  over the existing lane; identical smoke + banner + scenario for both
  legs, `PROXY_IMPLEMENTATION` from the matrix value, the Rust leg also
  building+installing the wheel, artifacts namespaced per leg. Validated
  with actionlint.
- 7d (`60061bd`): `verify-terminate-live.sh` — an isolated Rust lane on
  which the REST terminate endpoint (`GET /console/<source>/<uuid>/
  terminate`, keyed by the lane's console) is called and the in-flight
  drop is asserted via the proxy log `session terminated by control
  plane`. Confirmed that line is `info!` in `relay.rs` and the daemon lane
  runs at the default `info` level (no `--verbose`), so the oracle is
  emitted. Wired Rust-leg-only, before the destructive scenario; its log
  is stashed for artifact upload before teardown.
- 7e (`1157a70`): `run-loadtest.sh`, reusing the pre-existing
  `loadtests/latency/orchestrator.py` (SPICE PING/PONG RTT through the
  proxy) to record p50/p95 per leg, run on the shared lane (it only pings
  the existing connection) and non-gating (`continue-on-error` + a
  best-effort `exit 0`). Verified the nearest-rank percentile math and the
  no-socket skip path locally.
- 7f (`this commit`): docs (`AGENTS.md` direct-qemu matrix subsection,
  `README.md` parity line, the `VERIFY-RUST-PROXY.md` mock-vs-daemon split
  added in 7a), this Outcome, and the status flips below.

Notable decisions, deliberate:

- **Parity is expressed as one oracle, two proxies.** The Rust leg reuses
  the exact `run-scenario.sh` the Python leg runs; there is no separate
  Rust assertion to drift. This is the integration test phases 5/6
  deferred here.
- **The terminate test gets its own isolated lane, not a reconnect.**
  `ConsolesTerminate` expires the token, so the same `.vv` cannot
  reconnect; a dedicated `WORKDIR` lane (torn down in a trap) is simpler
  and more robust than re-fetching a `.vv` and relaunching ryll on the
  scenario lane. It runs sequentially before the scenario lane and shares
  the default ports safely (full teardown frees them first;
  `ClearNodeChannels` at the next proxy startup clears stale DB rows).
- **The loadtest reuses existing tooling and never gates.** The RTT
  orchestrator already existed; phase 7 only wraps + summarises it. The
  Python-vs-Rust numbers are read off the two legs' artifacts, not
  thresholded (shared-runner timing is too noisy).

### Pre-push audit (2026-07-08)

Reviewed the phase-7 diff (`git diff rust-proxy-phase-6..HEAD`; harness
shell scripts, one workflow, and docs — no Python/Rust package code).
`pre-commit` (flake8 + the 66-test py3 suite + actionlint on the workflow)
is green; every shell script passes `bash -n`. **No blocking, high, or
medium findings.**

- **Oracle validity** (the one real risk): the terminate assertion greps
  for an `info!`-level line, and the daemon lane runs at the default
  `info` level, so it is emitted without `--verbose`. The proxy's tracing
  reaches `kerbside.log` because the daemon (run with `>> kerbside.log
  2>&1`) launches the proxy child inheriting stderr (phase 5).
- **Isolation**: the terminate lane derives all state from its own
  `WORKDIR`; it is sequential with the scenario lane and both fully tear
  down, so the shared fixed ports and the shared MariaDB do not collide
  (`ClearNodeChannels` + the intent-row TTL reaper clear anything stale).
- **Non-gating discipline**: the loadtest step is `continue-on-error` and
  the script is `set -uo pipefail` (not `-e`) with `exit 0`, so a
  measurement hiccup cannot fail the lane; the summariser handles the
  empty-CSV case.
- **No secrets**: the JWT is minted from the lane's own generated seed
  (as `lane-up.sh` already does); nothing sensitive is logged.

Observations (not fixed): the Rust leg now runs two qemu boots (terminate
lane + scenario lane) plus the ryll and wheel builds — the `timeout
-minutes: 60` headroom should hold but is confirmed on the first CI run
(Open questions). The firewall runs in its default **enforce** mode
against real Sextant traffic; if the L1 allowlist has a gap the Rust leg
will fail and surface it (the phase-4 capture suggests it will not) —
this is deliberately the stronger test rather than warn-only.

## Back brief

Before executing any step, back brief the operator on the approach and
its alignment with this plan and the master plan. The three operator
decisions are settled: a single `proxy: [python, rust]` **matrix** lane
(parity expressed as both proxies passing one oracle); deliver the Rust
binary by **installing the phase-6 wheel** (real `find_proxy_bin` PATH
resolution, and CI coverage for phase 6); and a **non-gating**
measure-and-report loadtest. The remaining choices — loadtest driver,
where the terminate test lives, runner time budget, and PR-vs-dispatch
loadtest cadence — are the planner's leans recorded under Open questions.
