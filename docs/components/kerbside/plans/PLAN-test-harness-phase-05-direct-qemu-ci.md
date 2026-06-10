# Phase 5: Direct-qemu CI workflow

Part of [PLAN-test-harness.md](/components/kerbside/plans/PLAN-test-harness/). This phase
lives entirely in kerbside. The plan file lives here in
`docs/plans/` per the master plan's single-home rule.

## Goal

Stand up a new GitHub Actions lane that boots an Uncalibrated
Sextant qcow2 under direct qemu/KVM on a self-hosted runner,
fronts it with kerbside (configured against phase 2's static
source driver), and connects ryll (phase 3's headless control
socket) through kerbside as a smoke check that the spine works
end-to-end. No OpenStack, no oVirt, no Shaken Fist.

Phase 5 builds the **lane**. Phase 7 builds the rich Sextant
scenario tempest test that runs in the lane. Phase 5's smoke
check is deliberately minimal — its job is to prove kerbside +
ryll + qemu + sextant wire up correctly, not to assert anything
about Sextant's UI behaviour.

This phase is scope-bounded to:

- A new workflow file (working name
  `.github/workflows/direct-qemu-functional.yml`).
- A small set of helper scripts under `tools/direct-qemu/` that
  the workflow calls (no large inline shell in YAML, per the
  global CLAUDE.md convention).
- A committed Sextant qcow2 image under `tests/fixtures/` so
  the workflow does not have to build Sextant.
- A documented procedure for rebuilding that qcow2 when Sextant
  changes (a short shell wrapper, not a new CI job).
- A single smoke-test Python script that drives ryll's control
  socket through kerbside and asserts the lane works.

Out of scope for phase 5:

- A scenario tempest test (paste round-trip, scene transitions,
  digest assertions). That's phase 7.
- QR digest decoding in ryll. That's phase 6.
- Restoring keypress-to-screen latency semantics. Phase 6 too.
- A `headless` Cargo feature for ryll. Phase 6 (already tracked
  in the master plan's phase 6 row). Phase 5's container image
  carries the same heavy GUI/audio runtime stack that phase 4's
  loadtest image does — by intent, not oversight.
- A second runner shape: phase 5 picks **one** runner label set
  that has `/dev/kvm` and ships against it. If a future phase
  wants the lane to run on additional shapes, that's separate
  work.
- Replacing or demoting the existing OpenStack lane. That's
  phase 8.
- Wiring the latency loadtest into GitHub Actions. Tracked in
  phase 4's Future work; not bundled here.
- Multi-arch (aarch64 / ppc64le) Sextant builds.

## Decisions baked into this plan

These are judgment calls made while drafting, surfaced
explicitly so they can be challenged before code lands.

- **Runner label set: try `[self-hosted, vm]` first.** The
  existing functional-tests workflow runs main matrix jobs on
  `[self-hosted, vm]` and a sanity job on `[self-hosted, vm,
  debian-12]`; `.github/actionlint.yaml` registers `vm`,
  `static`, and `debian-12`. Phase 5's first workflow step
  probes `/dev/kvm` and dumps `lsmod | grep kvm` so an absent
  KVM device fails loudly and obviously, not silently via a
  qemu hang. If `[self-hosted, vm]` does not expose `/dev/kvm`
  to the workflow, the implementing agent for step 5c iterates
  by pushing label-set variants and watching CI; this iteration
  is expected and is the reason phase 5 is rated high effort.
- **Run on the runner host, not in a container.** Self-hosted
  runners with the `vm` label are themselves VMs and provide
  the isolation boundary; nesting docker for KVM passthrough
  adds complexity (privileged containers, `--device /dev/kvm`,
  cgroup juggling) without buying isolation we don't already
  have. The workflow `apt-get install`s qemu, ovmf, openssl,
  and the runtime libs ryll needs, builds ryll fresh from
  `main` HEAD (mirroring phase 4's loadtest Dockerfile pattern
  but without the docker wrapper), and runs everything as
  processes on the runner. Each run starts clean because the
  runner is ephemeral.
- **Sextant qcow2 ships in this repo at `tests/fixtures/uncalibrated-sextant.qcow2`.**
  Current build is 576 KB (per the Sextant Makefile's
  `qemu-img convert` step) — small enough that plain git is
  fine and Git LFS is unnecessary. Storing it here makes the
  workflow deterministic, removes a cross-repo fetch step, and
  keeps the failure modes of "Sextant changed underneath us"
  visible in PR diffs. A `tools/direct-qemu/rebuild-sextant-qcow2.sh`
  wrapper documents how to refresh it (clones Sextant, runs
  `make release`, copies the output). The wrapper is not
  invoked by CI; it's a developer convenience.
- **TLS material is generated fresh inside each workflow run.**
  openssl one-liners in `tools/direct-qemu/generate-tls.sh`
  produce a self-signed CA and a kerbside proxy cert+key into
  `/tmp/kerbside-tls/`. No certs in the repo. Reasons: (a) avoids
  committing key material, (b) avoids cert expiry surprises,
  (c) the smoke client trusts the generated CA via env var.
- **Kerbside runs as a background process under the workflow,
  not as a systemd unit.** Direct binary invocation
  (`kerbside &` with PID capture and a `kill` in cleanup) is
  the simplest viable shape for an ephemeral CI run. systemd
  unit work belongs to a future "package kerbside for hosts"
  phase, not here.
- **The smoke check is a stand-alone Python script, not a
  tempest test.** Phase 7 owns the tempest scenario test;
  phase 5 deliberately does not touch
  `tempest-plugin/kerbside_tempest_plugin/`. The smoke script
  lives at `tools/direct-qemu/smoke-client.py`, opens the
  ryll control socket, runs `hello`, `status`, `screenshot`,
  asserts `agent_connected == true` and that at least one
  surface is populated and the screenshot is a non-empty PNG.
  Exits non-zero on any RPC error or missing surface. The
  script is also used as a local debugging tool by developers,
  not just CI.
- **One sources.yaml shape, generated inline by `lane-up.sh`.**
  No template file under `etc/`. The workflow knows the UUID,
  hypervisor IP, port, and ticket because it generates them.
  Inline heredoc keeps the wiring obvious and removes a layer
  of indirection.
- **Smoke check covers control-socket through kerbside, not
  control-socket direct to ryll.** The whole point of the
  phase is to prove kerbside's role in the connection path.
  Ryll's `--url` flag points at the kerbside .vv endpoint;
  ryll's TCP SPICE connection therefore goes through kerbside,
  and the control socket exposes whether that connection
  succeeded (`agent_connected`, surfaces populated). If we
  pointed ryll at qemu's SPICE port directly we'd bypass the
  thing being tested.
- **`.vv` retrieval uses kerbside's REST API directly.** Avoids
  pulling in keystoneauth1 / openstacksdk just to ask kerbside
  for a console URL. The smoke script issues a plain
  `urllib.request` GET against kerbside's API for the static
  console's `.vv` file, writes it to disk, and passes the file
  to ryll via `--file`. (`--url` would also work if kerbside's
  API is publicly addressable; the file path is simpler.)
- **Artifact retention: 90 days, matching functional-tests.**
  No reason to diverge.
- **No matrix.** A single job, single runner, single sextant
  qcow2. Matrix variants (multiple qemu machine types, multiple
  SPICE configurations, TLS vs plaintext) are deferred; we
  pick one combination that we know works.

## Situation

### Existing CI

- `.github/workflows/functional-tests.yml` is the OpenStack
  lane. Runner labels `[self-hosted, vm]` (main matrix) and
  `[self-hosted, vm, debian-12]` (sanity). Wallclock ~90–100
  minutes; depends on a kolla-ansible all-in-one deploy and
  invokes `./tools/run-tempest-tests`. The tempest plugin's
  one test (`tempest-plugin/kerbside_tempest_plugin/tests/api/test_spice_via_kerbside.py`)
  asserts the SPICE link handshake against a Nova-provisioned
  instance.
- `.github/actionlint.yaml` registers exactly three self-hosted
  labels: `vm`, `static`, `debian-12`. No `kvm` label exists;
  the master plan's open question about which set gives nested
  KVM is resolved by phase 5 empirically.
- `tox.ini` has `py3`, `flake8`, `cover`, `bindep`. No tempest
  env. Tempest is invoked by `tools/run-tempest-tests`, not
  via tox.

### Phase 2 output (already on this branch)

- `kerbside/sources/static.py` reads a YAML file at
  `SOURCES_PATH` (default `./sources.yaml`). Each console
  entry needs `uuid`, `name`, `hypervisor`, `hypervisor_ip`,
  `insecure_port`, `ticket`; `secure_port` and `host_subject`
  are optional.
- `etc/example-static-sources.yaml` is the canonical example
  and includes the QEMU command-line equivalent we'll run in
  CI:
  ```
  qemu-system-x86_64 -machine q35,accel=kvm -m 2048 \
    -drive file=sextant.qcow2,format=qcow2 \
    -spice port=5910,password=ci-ticket-vm-1,disable-ticketing=off \
    -daemonize
  ```
- Kerbside's CLI entry point is `kerbside`
  (`pyproject.toml` lines 114–116). Config comes from
  `/etc/kerbside/kerbside.ini` plus `KERBSIDE_*` env vars
  (overriding the INI). Phase 5 sets env vars rather than
  writing an INI file.

### Phase 3 output (on the ryll `test-harness-control-socket` branch)

- `--headless`, `--url <URL>`, `--file <PATH>`,
  `--control-socket <PATH>` are the relevant flags
  (`ryll/src/config.rs:23..45`).
- Protocol at `ryll/docs/control-socket-protocol.md`: NDJSON
  framed over a Unix-domain socket, mode 0600. Verbs:
  `hello`, `status`, `screenshot`, `subscribe`, `send_key`,
  `paste`, `unsubscribe`. Events: `latency`, `agent_connected`,
  `paste_completed`, `paste_failed`, `dropped`.
- Single-client; second connection is rejected with `busy`.

### Phase 4 output (already on this branch)

- Multi-stage Dockerfile at `loadtests/latency/Dockerfile`
  carries the cargo-build pattern that phase 5 reuses (without
  the docker wrapper).
- `loadtests/latency/orchestrator.py` is a 295-line
  control-socket client. Phase 5's smoke client is a smaller,
  different-shape script (no cadence, no CSV — just hello +
  status + screenshot + assert), so reuse is partial at best.
  Concretely: the framing helpers (`_send`, `_recv_line`) and
  the hello handshake are worth lifting; the cadence thread
  and CSV writer are not. Phase 5 writes its own.

### Uncalibrated Sextant

- Build: `scripts/build.sh` runs `cargo build --release` in a
  docker container; `scripts/mkesp.sh` packages the BOOTX64.EFI
  into a 33 MB raw image; the Makefile converts to qcow2.
- Current qcow2 size: 576 KB. UEFI firmware (OVMF) is required
  by the guest; the runner must `apt-get install ovmf` and
  qemu must be pointed at the OVMF code+vars files.
- Serial: `src/serial.rs` writes via UEFI Serial Protocol. The
  qemu launcher must add `-serial file:<path>` to capture it.
- The repo's `make release-verify` boots both the raw image
  and the qcow2 headless, polls `dist/serial.log` for the
  banner `"Hello from Uncalibrated Sextant\r"` within 30s.
  Phase 5 reuses that banner string as a "guest is alive"
  oracle.

## Mission and problem statement

After phase 5:

- `.github/workflows/direct-qemu-functional.yml` runs on PR
  and on push to the test-harness branch. The workflow:
  1. Probes the runner for `/dev/kvm`, qemu, ovmf; fails
     fast with a readable diagnostic if any are missing.
  2. Installs runtime deps for ryll (libgl1, libx11-6,
     libxcb1, libxkbcommon0, libwayland-client0, libasound2,
     libssl3) and build deps for the cargo step.
  3. Clones ryll from `main` HEAD and builds the release
     binary (`cargo build --release --no-default-features
     -p ryll` — same as phase 4).
  4. Installs kerbside in a venv on the runner.
  5. Generates self-signed TLS material via
     `tools/direct-qemu/generate-tls.sh`.
  6. Boots the sextant qcow2 from `tests/fixtures/` in qemu
     with KVM acceleration, OVMF firmware, SPICE on a known
     port with a known ticket, and serial captured to a file.
  7. Writes a sources.yaml inline and starts kerbside with
     `KERBSIDE_SOURCES_PATH` and TLS env vars set.
  8. Polls kerbside's healthcheck endpoint until ready,
     fetches the static console's `.vv` file via the kerbside
     API, and runs `tools/direct-qemu/smoke-client.py` against
     ryll launched headless against that `.vv` file.
  9. Asserts smoke-client exits 0 and the qemu serial log
     contains the Sextant boot banner.
  10. On any failure or success, uploads the kerbside log,
      qemu serial log, ryll stderr, and the smoke-client
      output as a workflow artifact (90-day retention).
  11. Tears down qemu and kerbside processes.
- `tests/fixtures/uncalibrated-sextant.qcow2` is committed
  (~576 KB, plain git).
- `tools/direct-qemu/rebuild-sextant-qcow2.sh` documents the
  refresh procedure; it is not called by CI.
- `tools/direct-qemu/{generate-tls.sh, start-qemu.sh,
  start-kerbside.sh, lane-up.sh, lane-down.sh, smoke-client.py}`
  are the workflow's building blocks. `lane-up.sh` and
  `lane-down.sh` are the two scripts the workflow invokes
  directly; the others are called by them.
- `pre-commit run --all-files` is clean on every commit.

## Open questions

These do not block writing this plan but must be resolved
before or during the implementation:

- **Which runner label set has `/dev/kvm`?** The plan defaults
  to `[self-hosted, vm]`; step 5c iterates by pushing variants
  if the first attempt fails. Resolution: empirical, via CI.
  No code change required if `[self-hosted, vm]` works.
- **Is OVMF (`ovmf` package on Debian) available on the
  runners, or does it need a custom build?** The Debian 12
  package is current enough for Sextant in the local build
  process; assume it works in CI. Verify in step 5c's first
  push.
- **Does the runner's qemu support `q35,accel=kvm` without
  extra flags?** `qemu-system-x86_64 -machine q35,accel=kvm`
  is the same invocation Sextant's local build uses; should
  work on a runner with KVM enabled. If not, `accel=tcg`
  fallback is acceptable for the smoke check (slow but
  functional).
- **Does kerbside have a healthcheck endpoint suitable for
  startup polling?** If yes, use it. If no, fall back to
  "wait for the configured port to accept connections". This
  is a small read-the-code task for the step 5b sub-agent,
  not a blocker for the plan.
- **Whether to add a `tox -edirect-qemu` env that runs the
  smoke client locally.** Not in scope for this phase, but
  worth considering as a follow-up convenience. Noted in
  Future work.

## Execution

Each step is one logical change → one commit on the
`test-harness` branch (the existing kerbside phase branch).
Per the master plan's single-branch discipline, do not open
new branches.

| Step | Repo | Effort | Model | Isolation | Brief for sub-agent |
|------|------|--------|-------|-----------|---------------------|
| 5a. Commit Sextant qcow2 + rebuild script | kerbside | low | sonnet | none | Build (or fetch) a fresh release qcow2 from `/srv/kasm_profiles/mikal/vscode/src/shakenfist/uncalibrated-sextant`. Run `make release` in that repo (it uses docker; the runner has docker). Copy the resulting `dist/uncalibrated-sextant.qcow2` to `tests/fixtures/uncalibrated-sextant.qcow2` in kerbside and `git add` it (plain git, no LFS). Add `tools/direct-qemu/rebuild-sextant-qcow2.sh`: a short bash script that takes an optional `--sextant-repo PATH` arg (default `/srv/kasm_profiles/mikal/vscode/src/shakenfist/uncalibrated-sextant`), `cd`s in, runs `make release`, copies the output to the kerbside fixtures path, prints the SHA256 of the new image, and reminds the operator to commit the result. Use `set -euo pipefail`. Update `tests/fixtures/README.md` (create if missing) with a one-paragraph "Sextant qcow2: how it was built, when it was last refreshed, how to refresh" note. Verify the image with `qemu-img info` and include the size + format + virtual size in the commit body. One commit. |
| 5b. Add lane glue scripts | kerbside | medium | sonnet | none | Create `tools/direct-qemu/` and add: (1) `generate-tls.sh` — uses openssl to mint a self-signed CA + a kerbside proxy cert/key with CN matching `kerbside-ci`, writes everything to a tempdir passed in argv. Idempotent. (2) `start-qemu.sh` — takes qcow2 path, OVMF code+vars paths, SPICE port, ticket, serial log path; backgrounds qemu with the canonical Sextant launch args from phase 5 plan situation section; writes PID to a file. (3) `start-kerbside.sh` — takes sources.yaml path, TLS dir, log path; writes sources.yaml inline (heredoc with UUID/port/ticket arguments substituted in), exports KERBSIDE_* env vars, backgrounds kerbside, writes PID. Polls the configured port (curl or nc) until kerbside accepts connections; gives up after 30s. (4) `smoke-client.py` — stdlib-only Python 3.10+, connects to ryll's control socket, performs hello with protocol_version "1.0", runs status, asserts `agent_connected` is true and `surfaces` is non-empty within a 30-second budget (polling status every second); runs screenshot for surface 0 and asserts the returned PNG is non-empty. Logs each step to stderr. Exits 0 on success, 1 on assertion failure, 2 on any RPC/socket/parse error. Lift the framing helpers (`_send`, `_recv_line`) from `loadtests/latency/orchestrator.py`; don't reimplement them. (5) `lane-up.sh` — top-level workflow entry point, calls 1+2+3 in order, fetches the `.vv` file from kerbside's API (read the kerbside API source to find the right URL shape; the test_spice_via_kerbside tempest plugin does the same thing — model the request on it), launches `ryll --headless --file <vv> --control-socket /tmp/ryll-ci.sock &` and writes its PID. (6) `lane-down.sh` — kills ryll/kerbside/qemu PIDs from their pidfiles (best-effort, never errors), removes the TLS tempdir, prints "lane down". Use `set -euo pipefail` everywhere except `lane-down.sh` where best-effort is the goal. `bash -n` and `shellcheck` clean. `python3 -m py_compile` on the Python script. One commit. |
| 5c. Add direct-qemu CI workflow | kerbside | medium | sonnet | none | Create `.github/workflows/direct-qemu-functional.yml`. Triggers: `push` to test-harness branch, `pull_request` against develop. Runs-on: `[self-hosted, vm]` (the open question about runner labels is resolved empirically; expect to iterate by pushing label-set variants if this combination fails to expose `/dev/kvm`). Single job, no matrix. Job timeout: 60 minutes. Steps in order: (1) `actions/checkout@v4`. (2) `ls -la /dev/kvm` and `lsmod \| grep kvm \|\| true` and `qemu-system-x86_64 --version` and `which ovmf-firmware-x86-64-code.bin` (or whatever Debian calls the OVMF path) — diagnostic; fail fast on missing `/dev/kvm`. (3) `apt-get update && apt-get install -y qemu-system-x86 ovmf openssl curl jq libgl1 libx11-6 libxcb1 libxkbcommon0 libwayland-client0 libasound2 libssl3 build-essential cmake pkg-config libasound2-dev libxcb1-dev libxkbcommon-dev libwayland-dev libegl1-mesa-dev libgl1-mesa-dev libxcb-render0-dev libxcb-shape0-dev libxcb-xfixes0-dev libx11-dev libssl-dev`. (4) Install Rust via `rustup` (the runner may already have it; check first with `which cargo`). (5) Clone ryll from `https://github.com/shakenfist/ryll.git` to a tempdir at `main` HEAD; `cargo build --release --no-default-features -p ryll`; copy `target/release/ryll` to `/usr/local/bin/ryll`. (6) Install kerbside: `python3 -m venv /tmp/kerbside-venv && /tmp/kerbside-venv/bin/pip install -e .` from the checked-out repo. Expose the venv on PATH. (7) `tools/direct-qemu/lane-up.sh` — single call, exit code is the lane health. (8) `tools/direct-qemu/smoke-client.py /tmp/ryll-ci.sock` — exits non-zero on lane failure. (9) Assert the Sextant boot banner appears in the serial log: `grep "Hello from Uncalibrated Sextant" /tmp/sextant-serial.log`. (10) Always-run step that uploads `/tmp/kerbside.log`, `/tmp/sextant-serial.log`, `/tmp/ryll-ci.stderr`, `/tmp/smoke-client.log`, and any core dumps as `direct-qemu-artifacts` (`actions/upload-artifact@v4`, retention-days: 90, if-no-files-found: warn). (11) Always-run cleanup step that calls `tools/direct-qemu/lane-down.sh`. Use `actionlint` locally before committing. Pre-commit clean. **Do not run the workflow as part of this step** — the operator will push and watch CI; if the first run fails on runner-label or KVM probe, expect a follow-up commit on this same branch to adjust. One commit. |

### Sequencing notes

- 5a is a leaf change. Land it first so steps 5b and 5c have
  a real qcow2 to point at.
- 5b depends on 5a (the lane scripts reference the qcow2
  path). Lands second.
- 5c depends on 5b (the workflow calls lane-up.sh /
  lane-down.sh / smoke-client.py). Lands third.
- After 5c lands, the operator pushes the branch and watches
  CI. The first push is expected to surface runner-label or
  KVM-probe issues; follow-up commits on the same branch
  adjust the workflow until CI is green. This is **not** a
  separate phase — it's part of finishing 5c.

**Branch and PR shape**: All phase 5 commits land on the
existing `test-harness` branch of kerbside. The operator opens
the kerbside PR (which already carries phases 1–4's kerbside
work) once phase 5 is done. Whether to split the kerbside PR
into per-phase PRs is a master-plan-level decision, not a
phase 5 decision.

## Agent guidance

This phase plan follows the conventions in `PLAN-TEMPLATE.md`
at the kerbside repo root. The execution model, effort levels,
model-choice guidance, brief-writing standards, and management-
session review checklist all apply unchanged and are not
duplicated here.

Notes specific to phase 5:

- **Don't try to use docker in the workflow.** The phase 4
  loadtest image uses docker because it's a portable
  loadtest harness shipped to users; the phase 5 lane runs
  in CI on an ephemeral runner and gains nothing from
  containerisation. Building ryll directly on the runner
  is the simpler path.
- **Don't reimplement what phase 2/3/4 already gave us.**
  The static source driver is already wired up; the control
  socket protocol is documented and tested; the smoke
  client lifts framing helpers from the loadtest
  orchestrator. If a sub-agent feels like it needs a new
  protocol verb or a new sources.yaml field, push back —
  that's protocol drift that wants its own plan.
- **CI iteration is part of finishing 5c.** The phase plan
  cannot pre-resolve which runner label set has `/dev/kvm`;
  the implementing agent must accept that pushing the
  branch and watching CI is the resolution mechanism. Each
  iteration is a small follow-up commit on the same branch,
  not a new phase.
- **Heavy ryll runtime is acceptable.** Phase 6's `headless`
  Cargo feature is the cure; phase 5 lives with the bloat.
  Don't try to slim the apt-get list by skipping libs the
  GUI stack needs at runtime — ryll will fail to start.
- **The smoke check should fail loudly.** If kerbside
  doesn't come up, fail. If the .vv fetch returns 4xx/5xx,
  fail. If ryll's hello rejects, fail. If `agent_connected`
  never becomes true within the budget, fail. Silent
  success on a half-broken lane is the worst outcome —
  better a noisy red workflow.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the step and how
the work you intend to do aligns with that step's brief.

## Administration and logistics

### Success criteria

Phase 5 is done when:

- `tests/fixtures/uncalibrated-sextant.qcow2` exists in the
  repo and `qemu-img info` reports a valid qcow2.
- `tools/direct-qemu/` contains the seven scripts described
  above; each is executable; `bash -n` and `shellcheck`
  clean; `python3 -m py_compile` clean on the Python script.
- `.github/workflows/direct-qemu-functional.yml` exists,
  `actionlint` reports no errors against the local
  registered-label list, and a CI run against the
  test-harness branch reaches the smoke-client step.
- A CI run completes the smoke-client step with exit 0 and
  the workflow finishes green. Wallclock budget: ≤ 30 min
  (ryll build dominates, ~8–15 min; everything else is
  sub-minute).
- Artifacts are uploaded and downloadable from the workflow
  run.
- The master plan's phase-5 row is marked
  "Implementation complete; PR pending operator".
- `pre-commit run --all-files` from kerbside root is clean.

### Future work

Items deliberately deferred from phase 5:

- **Scenario tempest test.** Phase 7. Phase 5 leaves the
  tempest plugin tree alone so phase 7 has a clean seam.
- **QR digest assertion in the smoke check.** Phase 6 adds
  digest decoding to ryll behind the `digest-decode` Cargo
  feature; once that lands, phase 5's smoke client can be
  extended (or phase 7's tempest test can ride on top) to
  verify the digest reflects sent input.
- **Slim CI image via ryll's `headless` Cargo feature.**
  Phase 6. The phase 5 workflow's apt-get list will shrink
  noticeably once that lands.
- **`tox -edirect-qemu` for local runs.** A local convenience
  env that runs `lane-up.sh` and the smoke client without
  requiring a full GitHub Actions environment. Small;
  worth doing as a developer-experience follow-up.
- **A second runner shape.** If the lane proves useful and
  we want it on `[self-hosted, static]` or
  `[self-hosted, vm, debian-12]` too, add a matrix later.
- **OpenStack lane disposition.** Phase 8 decides whether
  to retire, demote-to-nightly, or keep the OpenStack lane.
  Phase 5 explicitly does not change the OpenStack lane.

### Bugs fixed during this work

(None yet.)
