# Rust proxy phase 8: cutover (Rust is the only proxy)

Part of the [Rust SPICE proxy master plan](/components/kerbside/plans/PLAN-rust-proxy/). Phases
1–7 built the Rust proxy, made the daemon supervise it, packaged it, and
proved it at parity in CI. Phase 8 is the cutover: the Rust proxy becomes
the sole proxy, the Python proxy and the Python SPICE stack are removed,
the docs are rewritten to describe the split as the norm, and the
Kolla/kerbside-patches deployment is reviewed. This is the final phase;
completing it closes the master plan.

## Prompt

Ground every deletion in the import graph — do not remove anything still
referenced. Read `kerbside/main.py` (`daemon_run`, `_run_rust_proxy`, the
`multiprocessing` fork branch), `kerbside/proxy.py` (the Python proxy
being removed), `kerbside/config.py` (`PROXY_IMPLEMENTATION`,
`TRAFFIC_INSPECTION*`), `kerbside/spiceprotocol/` and its consumers
(`kerbside/utilities/{main,glz,lz}.py`, and the functional tests
`kerbside/tests/functional/test_{proxy,shakenfist,openstack}.py`),
`tools/direct-qemu/` (the phase-7 matrix), and the docs
(`ARCHITECTURE.md`, `AGENTS.md`, `README.md`,
`docs/proxy-architecture.md`, `docs/configuration.md`). After each
removal, grep the tree for dangling references. Flag uncertainty
explicitly.

Planning effort: **high** — safe removal of a large, partly-shared code
body (the Python SPICE stack is used by more than the proxy) plus a test
re-platforming onto ryll.

## Repository and branch logistics

All work is in **kerbside**, except a review of (and possibly a patch to)
the separate **kerbside-patches** repo for Kolla — now checked out at
`/home/tars/src/shakenfist/kerbside-patches`, so 8g is an in-repo review,
not operator-driven (patches are proposed there but, per the operator's
standing rule, the PR is left for the operator to open). Phase 8 is the
top of the unmerged stack (phases 2–7), so branch `rust-proxy-phase-8`
from the phase-7 tip; it should be the last PR to land. This plan file
lives on the phase-8 branch.

## Situation (verified 2026-07-08)

- **`kerbside.proxy` is imported only by `main.py`.** `daemon_run`
  (`main.py:254`) validates the firewall config, then branches: if
  `PROXY_IMPLEMENTATION == 'rust'` it calls `_run_rust_proxy` and returns;
  otherwise it forks `multiprocessing.Process(target=kerbside_proxy.run)`
  and runs the legacy supervision loop. Removing the Python proxy is a
  clean deletion on the import side (only `main.py` references it).
- **`PROXY_IMPLEMENTATION` is settled: remove it entirely.** The whole
  rust-proxy stack is unmerged, so no released deployment depends on the
  field. The daemon becomes unconditionally Rust.
- **The Python SPICE stack is NOT proxy-only.** `kerbside/spiceprotocol/`
  (the `SpiceClient` + the `packets/*` parsers) is imported by:
  - `kerbside/proxy.py` (removed),
  - `kerbside/utilities/main.py` — the `kerbside-util` CLI, whose command
    groups are ALL SPICE-diagnostic: `client` (`connect-file`/
    `connect-url`, via `SpiceClient`) and `glz`/`lz` (SPICE image-frame
    decompression via `utilities/{glz,lz}.py`),
  - the functional tests `test_proxy.py` (5 negative handshake/auth
    tests against a live endpoint) and `test_shakenfist.py` /
    `test_openstack.py` (positive "SPICE connects, direct + via proxy"
    against live clouds, using `SpiceClient.connect()` as the oracle).
  So Full removal must also retire `kerbside-util` and re-platform the
  live connectivity tests — see the testing-gap analysis below.
- **`TRAFFIC_INSPECTION` / `TRAFFIC_INSPECTION_INTIMATE` /
  `TRAFFIC_OUTPUT_PATH`** (`config.py:153-164`) are consumed only inside
  `spiceprotocol/packets/*` (the parsers being deleted). They die with
  the Python SPICE stack.
- **The REST API is unaffected.** `api.py` does not import
  `spiceprotocol`; `consoletoken.py` (used by `api.py`/`db.py`) does not
  either. `.vv` generation, tokens, sources, and the gRPC control service
  (`rpc/*`, used by the Rust proxy) all stay.
- **Phase 7 left a `proxy: [python, rust]` matrix.** With the Python
  proxy gone the matrix collapses to a single Rust lane; the
  `PROXY_IMPLEMENTATION` plumbing in `start-kerbside.sh` / `lane-up.sh` /
  `run-loadtest.sh` simplifies to "always Rust." The wheel-install, the
  live terminate test, and the loadtest stay (the loadtest becomes
  single-proxy; the Python baseline was captured in phase 7).
- **kerbside-patches / Kolla** (now checked out) installs kerbside via
  pip. Post-phase-6 that transitively pulls the `kerbside-proxy`
  manylinux wheel (x86_64 + aarch64), so the container needs no Rust
  toolchain — but the base image must be glibc ≥2.28 and the right arch,
  and the deployment simply runs `kerbside daemon run` (now always Rust).
  The repo already splits **`kerbside_api`** and **`kerbside_proxy`** into
  separate Kolla-Ansible inventory groups/roles (matching the distributed
  model), so the review focuses on: how the Kolla image installs the
  kerbside package (does it pull the proxy wheel?), and whether the
  `kerbside_proxy` role/container config assumes the Python proxy.

## Testing-gap analysis (settled: Full removal + port tests to ryll)

Deleting the Python SPICE stack removes the coverage below; the operator
chose to preserve it by re-platforming onto **ryll** (the real Rust
client, already the phase-7 test driver):

| Removed coverage | Disposition |
|---|---|
| Positive "connects through the proxy" (direct-qemu) | Already covered by the phase-7 ryll direct-qemu lane; no action. |
| Positive "SPICE connects" vs live SF/OpenStack (`test_shakenfist`/`test_openstack`) | **Re-express with ryll headless** (fetch `.vv` → `ryll --headless` → assert channels via the control socket, the `smoke-client.py` pattern). Preserves the coverage via the real client. |
| Negative handshake/auth rejection (`test_proxy.py`) | Retired; the proxy's rejection logic is already Rust-unit-tested (phase 1 handshake parsers + phase 3 proxy/TLS tests). A ryll/raw-socket negative probe is optional future work. |
| `kerbside-util` SpiceClient + glz/lz diagnostics | Retired; ryll (with `--features digest-decode`) is the replacement client/decoder. |

## Mission

1. The daemon runs **only** the Rust proxy; `PROXY_IMPLEMENTATION` and the
   Python fork path are gone.
2. The Python SPICE stack (`proxy.py`, `spiceprotocol/`, the `kerbside
   -util` utility, `TRAFFIC_INSPECTION*`) is removed, with **no dangling
   references** and green `flake8`/`py3`.
3. Live-cloud SPICE-connectivity coverage is preserved by re-platforming
   `test_shakenfist`/`test_openstack` onto ryll; `test_proxy.py` is
   retired.
4. The direct-qemu lane collapses to a single Rust lane.
5. The docs describe the Rust proxy as **the** proxy (not a migration in
   progress); the Kolla/kerbside-patches deployment is reviewed (and
   patched if needed).

Out of scope: L2/L3 body inspection or session recording (future master
plan); a Rust re-implementation of the Python traffic-dump feature
(retired, not ported); OpenTelemetry.

## Design decisions

Both operator decisions are settled (Full removal + ryll test port;
remove `PROXY_IMPLEMENTATION`). The rest are the planner's calls.

1. **Remove `PROXY_IMPLEMENTATION` entirely; the daemon is
   unconditionally Rust.** `_run_rust_proxy` becomes the body of
   `daemon_run` (the firewall-config validation stays as-is). Drop the
   `multiprocessing` fork, the `import proxy`, and any now-unused imports.
2. **Delete the whole `kerbside/utilities/` package and its
   `kerbside-util` entry point.** Every command in it is SPICE-diagnostic
   and superseded by ryll; there is no non-SPICE command to preserve
   (verify by reading `utilities/main.py` before deleting, and grep for
   external importers).
3. **Re-platform, don't just delete, the connectivity tests.**
   `test_shakenfist`/`test_openstack` keep their source-driver `setUp`
   (provisioning a live instance, fetching the `.vv`) but swap
   `SpiceClient.connect()` for a ryll-headless connectivity assertion via
   a shared helper (reuse `tools/direct-qemu/smoke-client.py`'s
   control-socket probe, or factor a small `ryll_connect_check` helper).
   Confirm whether these run in CI (`functional-tests.yml`) or are
   operator-only, and wire ryll availability if they gate CI.
4. **Keep the local mock harness.** `start-rust-proxy.sh` /
   `verify-rust-proxy.sh` / `mock-grpc-server.py` remain (MariaDB-free
   local proxy verification); only their "Python is the default proxy"
   framing is updated.
5. **Docs become descriptive, not transitional.** Remove "being
   reimplemented / Python is the default / full cutover is a later phase"
   language throughout; describe the two-package split (pure-Python
   `kerbside` + Rust `kerbside-proxy`) as the architecture.

6. **`kerbside-util` is retired, not re-platformed into ryll (settled).**
   A pre-phase-8 phase to rebuild its SPICE parts in ryll was considered
   and declined: the `client connect-*` commands are already fully
   covered by ryll (`--headless` / GUI — 8d uses exactly this), and the
   `glz`/`lz` decoders consume the `TRAFFIC_INSPECTION` dump format that
   this phase retires, while ryll already carries GLZ/LZ decode
   (`--features digest-decode`). Porting now would recreate existing ryll
   capability and a decoder for a dying format. A ryll decode subcommand
   is recorded as future work tied to a future L3 capture feature (below).

## Open questions (to settle during the phase)

- Whether `test_shakenfist`/`test_openstack` execute in
  `functional-tests.yml` today (cloud lanes) or only against the
  operator's home cloud — determines how the ryll port is validated and
  whether ryll must be added to those CI lanes.
- The shape of the shared ryll connectivity helper (reuse
  `smoke-client.py` as a module vs a new small helper) and where it lives
  so both the tests and the direct-qemu lane can share it.
- `kerbside-patches` specifics (repo not local): does any patch reference
  the Python proxy, `PROXY_IMPLEMENTATION`, `TRAFFIC_INSPECTION`, or a
  proxy process unit that the daemon-supervised model changes?
- Whether to drop `multiprocessing` from `main.py` entirely (only if no
  other use remains after the fork branch is removed).
- Whether any `pyproject.toml` dependency becomes unused once the Python
  SPICE stack is gone (e.g. `Pillow`/PIL, used by `utilities/glz.py`) and
  should be removed from the dependency list.

## Execution

One commit per logical change. Every step passes `tox -eflake8`/`-epy3`
and `pre-commit run --all-files`; after each removal, grep the tree for
dangling references before committing.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | high | opus | none | **Make the daemon unconditionally Rust; remove `PROXY_IMPLEMENTATION`.** In `kerbside/main.py`, delete the `multiprocessing.Process(target=kerbside_proxy.run)` branch and its supervision loop; make `_run_rust_proxy` the body of `daemon_run` (keep the firewall-config validation). Remove `from . import proxy as kerbside_proxy` and `import multiprocessing` if now unused. In `kerbside/config.py`, remove the `PROXY_IMPLEMENTATION` field. Grep for any other `PROXY_IMPLEMENTATION` consumer in `kerbside/` and remove/adjust. Do NOT touch the harness yet (8d). Tests: the daemon entrypoint imports and the existing unit suite (incl. `test_proxy_supervisor.py`) still pass; add/adjust a unit test if one asserted the branch. |
| 8b | high | opus | none | **Delete `kerbside/proxy.py`.** Remove the file. Confirm (grep) nothing but the now-updated `main.py` referenced it. `flake8`/`py3` green. Small, isolated commit so the proxy removal is reviewable on its own. |
| 8c | high | opus | none | **Delete the Python SPICE stack + `kerbside-util` + traffic-inspection config.** Remove `kerbside/spiceprotocol/` entirely and `kerbside/utilities/` entirely (all SPICE-diagnostic; verify no external importers first). Remove the `kerbside-util` entry point from `pyproject.toml` and any now-unused dependency it alone pulled (e.g. `Pillow`/PIL — verify). Remove `TRAFFIC_INSPECTION`, `TRAFFIC_INSPECTION_INTIMATE`, `TRAFFIC_OUTPUT_PATH` from `config.py`. Delete `kerbside/tests/functional/test_proxy.py`. Grep the whole tree for `spiceprotocol`, `SpiceClient`, `utilities`, `TRAFFIC_` and fix every dangling reference. `flake8`/`py3` green. |
| 8d | high | opus | none | **Re-platform the live-cloud connectivity tests onto ryll.** In `test_shakenfist.py`/`test_openstack.py`, keep the source-driver `setUp` and `.vv` fetch but replace `SpiceClient.connect()` with a ryll-headless connectivity assertion: launch `ryll --headless --file <vv> --control-socket <tmp>`, wait for the control socket, and assert a live session (reuse `smoke-client.py`'s hello/surfaces probe or factor a shared `ryll_connect_check` helper). Determine if these run in `functional-tests.yml`; if so, ensure ryll is built/available on that lane. Keep the assertions equivalent (connects direct AND via the proxy). |
| 8e | medium | opus | none | **Collapse the direct-qemu lane to Rust-only.** Remove the `proxy: [python, rust]` matrix from `direct-qemu-functional.yml` (single Rust lane); drop the `PROXY_IMPLEMENTATION` plumbing from `start-kerbside.sh`/`lane-up.sh`/`run-loadtest.sh` (always Rust — keep the `find_proxy_bin` pre-check and the wheel install, which are now unconditional). Keep the terminate test and the loadtest (now single-proxy; note the Python baseline lives in phase-7 history). actionlint + a local dry-run of the harness scripts. |
| 8f | medium | opus | none | **Rewrite the docs as descriptive, not transitional.** `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, `docs/proxy-architecture.md`, `docs/configuration.md`: describe the Rust `kerbside-proxy` as the sole proxy and the pure-Python `kerbside` + Rust `kerbside-proxy` split as the architecture; remove all Python-proxy, `PROXY_IMPLEMENTATION`, and `TRAFFIC_INSPECTION*` content and the "migration in progress / Python is default / cutover is a later phase" framing; drop `kerbside-util` from any command reference. Update the mock-harness VERIFY docs' framing. Remove stale Python-proxy sections from the config reference. |
| 8g | medium | opus | none | **kerbside-patches / Kolla review.** In `/home/tars/src/shakenfist/kerbside-patches`: grep for references to the Python proxy, `PROXY_IMPLEMENTATION`, `TRAFFIC_INSPECTION`, or a separate proxy process/unit; find how the Kolla image installs the kerbside package and confirm it pulls the `kerbside-proxy` wheel on a glibc≥2.28 base for both x86_64 and arm64, that the binary lands on `PATH`, and that the `kerbside_proxy` role/container runs `kerbside daemon run` (now always Rust) with no Python-proxy assumptions. Propose patches in that repo if needed (do NOT open the PR — hand to the operator). Record findings in the Outcome even if no change is required. |
| 8h | medium | sonnet | none | **Close the master plan.** Write the phase-8 Outcome after the pre-push audit; flip the phase-8 row to Complete in `PLAN-rust-proxy.md`, mark the master plan **Complete** in `docs/plans/index.md`, and note the whole stack (phases 2–8) is ready to land. |

Sequencing: 8a → 8b → 8c is the removal spine (each a small reviewable
deletion, grep-checked). 8d (ryll test port) depends on 8c and is the
highest-effort new work. 8e/8f are independent of 8d and of each other.
8g can start any time (separate repo). 8h last. Because this deletes a
lot, prefer small commits and run `flake8`/`py3` after every one.

## Success criteria

- `kerbside daemon run` starts the Rust proxy with no `PROXY_IMPLEMENTATION`
  knob; there is no Python proxy code, no `spiceprotocol/`, no
  `kerbside-util`, and no `TRAFFIC_INSPECTION*` config left in the tree,
  and `flake8`/`py3` are green with no dangling references.
- Live-cloud SPICE connectivity is still asserted by
  `test_shakenfist`/`test_openstack`, now via ryll.
- The direct-qemu lane is a single Rust lane and still passes the Sextant
  scenario, the live terminate test, and records loadtest numbers.
- The docs describe the Rust proxy as the sole proxy; no "migration in
  progress" language remains.
- The kerbside-patches/Kolla deployment is reviewed; any required patch is
  identified (and handed to the operator).
- Pre-push audit clean; the master plan is marked Complete.

## Future work (recorded)

- A ryll/raw-socket negative-handshake probe in the integration lane, if
  proxy-side rejection coverage is wanted back at the integration level.
- A ryll GLZ/LZ **decode subcommand** (reusing ryll's existing
  `digest-decode` machinery, not a new binary), added if/when the Rust
  proxy grows an **L3 traffic-capture** feature that produces a format
  worth decoding — the retired `kerbside-util glz/lz` tools decoded the
  Python proxy's dump format, so the replacement belongs with the
  capture feature that would replace it.
- L2 body validation / L3 session recording (a future master plan).
- Retiring `PUBLIC_INSECURE_PORT` / the plaintext listener if the Rust
  proxy reduces it to a `need_secured` responder (master-plan open
  question, not required for cutover).

## Outcome

Completed 2026-07-08 on the kerbside `rust-proxy-phase-8` branch,
unmerged and unpushed pending operator review. This closes the master
plan. All planned steps landed; the removal spine was a series of small,
grep-checked deletions, and `flake8`/`py3` (the unit gate) stayed green
throughout.

- 8a (`613b284`): `daemon_run` runs the Rust proxy unconditionally;
  removed the `multiprocessing` fork, `import proxy`/`import
  multiprocessing`, and the `PROXY_IMPLEMENTATION` config field.
- 8b (`055e034`): deleted `kerbside/proxy.py` (642 lines) — imported only
  by `main.py`, which no longer references it.
- 8d, done before 8c to avoid a broken intermediate (`c08b4b8`):
  re-platformed the live-cloud connectivity tests onto ryll — a new
  `kerbside/tests/functional/ryll_helper.py` (`assert_spice_connects`
  driving `ryll --headless` + a `vv_from_static` builder for the OpenStack
  spice-direct host/port case), with `test_shakenfist`/`test_openstack`
  swapping `SpiceClient` for it.
- 8c (`8bc00cc`): deleted the Python SPICE stack — `kerbside/spiceprotocol/`,
  `kerbside/utilities/` (the `kerbside-util` CLI) + its entry point, the
  `TRAFFIC_INSPECTION*` config, and `test_proxy.py` (2914 lines). A tree
  grep confirmed no dangling references; the REST API, tokens, sources,
  and gRPC control service are untouched.
- 8e (`68c80fe`): collapsed the direct-qemu `proxy: [python, rust]` matrix
  to a single Rust lane and unwound the `PROXY_IMPLEMENTATION` plumbing
  across the workflow and the four harness scripts (the `find_proxy_bin`
  pre-check and the wheel install are now unconditional).
- 8f (`0597bf6`, `1500cc1`, `eed2ba8`, `3aafc60`): rewrote the docs as
  descriptive-not-transitional — `README.md` + `docs/configuration.md`
  (drop `PROXY_IMPLEMENTATION` and the whole Traffic Inspection section),
  `ARCHITECTURE.md` (section 3 is now the Rust proxy), `docs/proxy-
  architecture.md` (banner reframes the Python-internals deep-dive as
  removed-code background; a full Rust-internals rewrite of those sections
  is recorded as follow-up rather than rushed), and `AGENTS.md` +
  `VERIFY-RUST-PROXY.md`.
- 8h (`this commit`): this Outcome, the audit below, and the status flips.

### kerbside-patches / Kolla review (step 8g, 2026-07-08)

Reviewed `/home/tars/src/shakenfist/kerbside-patches`. **No patch change is
required for the release deployment path**, and no patch references the
removed Python proxy machinery:

- **No Python-proxy assumptions in the patches.** Grepping the `_patches/`
  set for `proxy.py`, `PROXY_IMPLEMENTATION`, `TRAFFIC_INSPECTION`,
  `kerbside-util`, `multiprocessing`, and `spiceprotocol` found only
  OpenStack SDK's unrelated `_proxy.py` (patch006). Nothing encodes the
  Python proxy.
- **The image model already fits the cutover.** The images split into
  `kerbside-base` → `kerbside` (API, runs uwsgi) and `kerbside-proxy`
  (runs `kerbside daemon run`), both `FROM kerbside-base`. `kerbside-base`
  pip-installs kerbside from a release tarball (`kolla/common/sources.py`
  → `https://shakenfist.com/kerbside/releases/...`). Post-phase-6 that
  release carries the exact `kerbside-proxy==X.Y.Z` pin (stamped by
  `tools/stamp-proxy-version.sh` in `release.yml`), so `pip3 install
  /kerbside` transitively pulls the `kerbside-proxy` manylinux wheel; both
  images then have the binary on `PATH`, and the proxy container's
  `kerbside daemon run` resolves it via `find_proxy_bin()`.
- **Prerequisites for the operator (recorded, not blocking):** the base
  image build needs PyPI reachability to fetch the `kerbside-proxy` wheel,
  on a glibc≥2.28 base for the target arch — satisfied by Rocky 10 /
  Ubuntu Noble on x86_64 and arm64, which phase 6's `manylinux_2_28`
  x86_64+aarch64 wheels cover.
- **Dev/source builds caveat:** a `kolla_dev_repos`-style build from a
  kerbside **git checkout of `develop`** installs a tree whose committed
  `pyproject.toml` has no `kerbside-proxy` pin (the pin is inserted only at
  release), so it would not pull the proxy wheel. Such builds must install
  `kerbside-proxy` explicitly (from PyPI or a locally-built wheel) or set
  `KERBSIDE_PROXY_BIN`. Left for the operator; no speculative Kolla patch
  was written (it could not be built/tested in this environment).

### Pre-push audit (2026-07-08)

Reviewed the phase-8 diff (`git diff rust-proxy-phase-7..HEAD`): Python
deletions, the ryll test port, the harness/CI simplification, and the doc
rewrites — no Rust source changes. `pre-commit` (flake8 + the 66-test py3
unit suite + actionlint) is green after every commit; all harness scripts
pass `bash -n`. **No blocking, high, or medium findings.**

- **No dangling references.** After each deletion the tree was grepped for
  `spiceprotocol`, `SpiceClient`, `TRAFFIC_INSPECTION`, `kerbside.utilities`,
  `kerbside-util`, `PROXY_IMPLEMENTATION`, and `kerbside_proxy` (the Python
  module); the only remaining mentions are explanatory comments in
  `ryll_helper.py` and benign historical "(phase 5)" attributions.
- **Ordering was chosen for clean intermediates.** 8d (ryll port) ran
  before 8c (stack deletion) so no committed state has functional tests
  importing a deleted module, even though `.stestr.conf` scopes the gate to
  `kerbside/tests/unit` and would not have caught it.
- **Inspection-only surfaces, flagged honestly:** the ryll connectivity
  port (8d) targets live-cloud tests not in the unit gate; the collapsed
  direct-qemu lane (8e) runs on the CI runner; the Kolla image build (8g)
  runs in the deployment pipeline. None could be executed here; each is
  verified by inspection and confirmed on push / the operator's cloud /
  the deployment, consistent with phases 5–7.

### Full-series pre-push audit (2026-07-09)

After the master plan was marked complete, the `PUSH-AUDIT.md` audit was
run across the **whole** stacked series (`git diff develop..HEAD`, phases
2–8), not just phase 8. Wave 1 (flake8 + the 66-test py3 suite + style
greps) and the wave-2 mechanical script were green; the four wave-2
judgment dimensions were run as parallel sub-agents (2a/2b/2c sonnet, 2d
security opus) and, after a first run where three hit the account session
limit, re-run to completion once quota returned.

**No blocking, critical, or high findings survived.** Fixes were appended
as commits on this branch:

- Stale references to the removed Python SPICE stack in docs and comments
  (AGENTS, ARCHITECTURE, README, several `docs/` protocol pages,
  PLAN-TEMPLATE, and Rust doc-comments) were purged; `docs/proxy
  -architecture.md` was rewritten from the Python deep-dive into an
  accurate Rust account — **this supersedes the "deferred follow-up"
  note above**: the rewrite was done during the audit rather than left
  banner-labelled.
- Two more DB functions orphaned by the Python-proxy removal
  (`remove_proxy_channel`, `get_node_channels`) were deleted, along with
  the terminate-path duplication (new `db.terminate_session`) and the dead
  `setproctitle` dependency.
- Doc claims were corrected to match reality: the Prometheus metric set,
  the removal of "traffic inspection / worker management", the L1-firewall
  channel model, and the high-level architecture diagram.
- The gRPC-contract (`kerbside.proto`) comments' now-false status claims
  were corrected and the stubs regenerated (descriptor byte-identical).

Accepted advisories / tracked follow-ups (none blocking):

- **Backend hypervisor TLS subject pinning (wave 2d, MEDIUM).** The
  backend leg validates the CA chain but not the certificate *subject*
  (`host_subject`); the fix belongs in the ryll `shakenfist-spice-protocol`
  verifier. Pre-existing parity gap, documented in `backend.rs`, not a
  regression from this series.
- **ProxyControl stream is not reconnected (wave 2a).** If the daemon↔proxy
  control stream drops, the proxy does not re-establish it this phase; a
  documented, pre-existing design tradeoff.
- **Coverage of orchestration glue (wave 2b).** `main.py`'s daemon
  supervision + startup firewall-validation, `find_proxy_bin`'s PATH/dev
  -tree branches, `db.terminate_session` against a real DB, and the alembic
  migrations have no unit coverage (migrations follow the repo's
  pre-existing no-migration-test pattern). Advisory.
- **Backend plaintext-first (wave 2d, LOW)** — parity with the prior proxy;
  the SPICE ticket is RSA-encrypted regardless. Deployments on untrusted
  networks must require hypervisor TLS.

## Back brief

Before executing any step, back brief the operator. The two settled
decisions: **Full removal of the Python SPICE stack** (proxy, the
`spiceprotocol` library, the `kerbside-util` diagnostics, and the
`TRAFFIC_INSPECTION*` knobs) **with the live-cloud connectivity tests
re-platformed onto ryll** so no coverage is lost, and **removing
`PROXY_IMPLEMENTATION`** so the daemon is unconditionally Rust. The
removal spine (8a–8c) is deliberately several small grep-checked
deletions; 8d (the ryll test port) is the real new work; 8g
(kerbside-patches) needs that repo, which is not checked out here.
