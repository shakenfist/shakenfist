# Phase 7: First Sextant scenario tempest test

Part of [PLAN-test-harness.md](/components/kerbside/plans/PLAN-test-harness/). This phase
lives entirely in kerbside. The plan file lives here in
`docs/plans/` per the master plan's single-home rule.

## Goal

Compose the primitives that phases 1–6 built into the first real
end-to-end scenario test: boot Uncalibrated Sextant under direct
qemu/KVM, front it with kerbside via the static source driver,
drive the full Awaiting → Booting → bootloader-ignore → paste →
Parked → shutdown sequence over ryll's control socket, and assert
the run against **both** oracles — the live QR digest stream
(`digest_updated` events) and the post-mortem serial drain.

Phase 5 proved the lane wires up; phase 6 lit up the event
stream. Phase 7 is the first phase that exercises the whole
stack, and the first consumer of the `digest-decode` Cargo
feature in anger.

This phase is scope-bounded to:

- A tempest scenario test at
  `tempest-plugin/kerbside_tempest_plugin/tests/scenario/`
  driving the canonical Sextant sequence.
- A small reusable NDJSON control-socket client module inside
  the tempest plugin (the plugin cannot import from `tools/`).
- Tempest config options that point the test at the lane
  (control socket path, serial log path, artifact dir,
  per-step timeout). Unset options mean "skip the test", so
  the plugin stays loadable on the OpenStack lane unchanged.
- CI wiring: the direct-qemu workflow builds ryll with
  `--features digest-decode`, installs tempest + the plugin in
  a dedicated venv, and runs the scenario test as the last
  (deliberately destructive) lane step.
- Doc and master-plan-status touchups.

Out of scope for phase 7:

- Mouse, USB redirection, vdagent clipboard, audio, WebDAV
  scenarios. Sextant has no matching scenes yet.
- Running the scenario test on the OpenStack lane. The test
  skips when the direct-qemu config options are unset; making
  it substrate-portable (serial log access via Nova, ryll
  placement) is follow-up work.
- Recomputing or asserting the eight per-channel chained
  CRC32C hashes from the digest. v1 asserts frame-counter
  monotonicity, record contents, and the serial drain; hash
  chain verification is deferred.
- Latency assertions. The loadtest owns latency; the scenario
  test asserts behaviour, not performance.
- Retiring or demoting the OpenStack lane. Phase 8.
- New control-socket verbs or protocol changes. v1.1 as shipped
  by phase 6 is sufficient; if a gap is found, that is a new
  ryll phase, not a phase 7 commit.

## Decisions baked into this plan

These are judgment calls made while drafting, surfaced
explicitly so they can be challenged before code lands.

- **The test lives in `tests/scenario/`, not `tests/api/`.**
  The master plan's success criterion says `tests/api/`, but
  tempest's own convention separates API-surface tests from
  end-to-end scenario tests, and this is unambiguously the
  latter. The master plan's success-criteria wording is
  updated alongside this plan. The existing
  `tests/api/test_spice_via_kerbside.py` stays where it is.
- **The test does not provision anything.** The lane
  (`tools/direct-qemu/lane-up.sh`) boots qemu, kerbside, and
  ryll exactly as it does for the smoke check; the tempest
  test consumes the already-running lane via config options.
  This keeps the test substrate-agnostic in shape: a future
  OpenStack or Shaken Fist lane satisfies the same contract
  (a ryll control socket plus oracle access) with different
  glue. Provisioning inside the test would weld it to qemu.
- **The test class does not request cloud credentials.** It
  inherits from `tempest.test.BaseTestCase` directly (not
  `compute_base.BaseV2ComputeAdminTest`) and declares no
  credentials, so tempest never contacts keystone. This is
  what lets the same tempest invocation run on a runner with
  no cloud at all. If credential-less tempest turns out to
  fight the harness, the fallback is direct `stestr` /
  `python -m testtools.run` invocation of the same test class
  (see Open questions).
- **Skip, don't fail, when unconfigured.** `skip_checks`
  raises `skipException` unless
  `CONF.kerbside.control_socket_path` is set. The OpenStack
  lane's tempest.conf does not set it, so the plugin remains
  drop-in safe there. On the direct-qemu lane the option is
  always set, so a misconfigured lane fails loudly inside the
  test, never silently skips: missing socket file, missing
  `digest_updated` in `supported_events` (ryll built without
  the feature), and missing serial log are all hard failures
  once the test is running.
- **The scenario runs last in the workflow, after the smoke
  check and banner assertion.** The final Parked keypress
  causes Sextant to drain serial and ACPI-shutdown; qemu
  exits and ryll's event loop ends, unlinking the control
  socket. Nothing in the lane is usable afterwards. The
  smoke client only reads state (hello / status /
  screenshot), so the guest is still sitting in Awaiting
  when the scenario test connects — ordering smoke-then-
  scenario costs nothing and keeps phase 5's coverage.
- **Drive transitions with Enter (scancode 28), never digits.**
  Sextant consumes `0`–`6` as GOP mode-switch keys in every
  scene; a digit would trigger a mode cycle instead of a
  phase transition. Enter is a non-mode printable key in all
  three blink loops. Bootloader choice keys are letters
  (`r`/`i`/`a`, case-insensitive); the test sends `i`
  (scancode 23) to take the encoded-blob path.
- **Paste via the `paste` verb, not per-key `send_key`.**
  Ryll's paste-as-keystrokes path (with a modest
  `char_delay_ms`) is exactly what Sextant's `capture_paste`
  expects. The expected payload `sextant{HELLO_OPERATOR}`
  (23 bytes) is hardcoded in the test with a comment pointing
  at `uncalibrated-sextant/src/bootloader.rs::PASTE_TARGET`
  — it is a property of the committed qcow2 fixture, not
  configuration.
- **Per-beat assertions scan a window of digest events; no
  single event is expected to carry the story.** The digest's
  raw-record region holds only the most recent run of events
  that fits in 44 bytes (typically 2–4 records), and cursor
  blinks re-encode the digest twice a second, so the stream
  is busy and each event is a tiny sliding window. The test
  helper therefore waits for "a digest_updated whose records
  match predicate P, within deadline D" while also asserting
  frame counters are strictly increasing across everything
  it consumes.
- **The serial drain is the authoritative post-mortem oracle.**
  After the final keypress the test stops trusting the
  control socket entirely (EOF is expected and tolerated),
  polls the serial log for the terminal `type=refresh_stats`
  line, then parses the full drain and asserts the canonical
  event subsequence in order: keypress, awaiting→booting
  transition, a plausible count of `type=line` events,
  `bootloader_decision choice=ignore attempt=1`,
  `paste len=23 correct=true`, booting→parked transition,
  keypress, parked→parked transition, `refresh_stats`
  present. Timestamps are asserted monotonic, not exact.
- **Screenshots on every beat, kept as artifacts.** The test
  saves a PNG via the `screenshot` verb at each scenario beat
  (and unconditionally on failure) into
  `CONF.kerbside.scenario_artifact_dir`; the workflow uploads
  that directory. Debugging a red scenario run from CI logs
  alone proved painful in phase 5; pixels are cheap.
- **Ryll gains the `digest-decode` feature in CI only.** The
  workflow build becomes `cargo build --release
  --no-default-features --features digest-decode -p ryll`.
  The loadtest Dockerfile is untouched — the latency
  orchestrator does not consume digests, and keeping the
  loadtest image slim was the point of phase 6.
- **Tempest gets its own venv on the runner.** Tempest's
  oslo/paste dependency train conflicts easily with
  kerbside's pinned requirements; `/tmp/tempest-venv` keeps
  them apart. The kerbside venv from phase 5 is unchanged.

## Situation

All upstream phases are merged as of 2026-06-11: the digest
crate (phase 1), the static source driver (phase 2), control
socket v1.0 (phase 3), the ported loadtest (phase 4), the
direct-qemu lane (phase 5), and the Cargo feature work +
protocol v1.1 + `digest_updated` (phase 6, ryll and kerbside
sides both on their default branches).

### The lane today (phase 5 + 6, on kerbside develop)

- `.github/workflows/direct-qemu-functional.yml` runs on PRs
  against develop, on `[self-hosted, vm, debian-12, l]`.
  Builds ryll from main with `--no-default-features`, brings
  the lane up via `tools/direct-qemu/lane-up.sh`, runs
  `smoke-client.py`, asserts the boot banner, uploads
  artifacts from `/tmp/kerbside-ci/`, tears down.
- `lane-up.sh` boots the Sextant qcow2
  (`tests/fixtures/uncalibrated-sextant.qcow2`) under
  qemu+OVMF with SPICE on 5910, starts kerbside against a
  generated `sources.yaml`, mints a JWT, fetches the proxy
  `.vv`, and launches
  `ryll --verbose --headless --file console.vv
  --control-socket /tmp/kerbside-ci/ryll-ci.sock`.
- The serial log lands at `/tmp/kerbside-ci/sextant-serial.log`
  (qemu `-serial file:`). The Sextant banner and the GOP mode
  dump appear at boot; the event drain appears only at
  shutdown.
- Known sharp edge from phase 5: ryll exits its event loop —
  and unlinks the control socket — the moment the SPICE
  connection ends. Anything that outlives the guest must not
  assume the socket still exists.

### Ryll control socket v1.1 (phase 6, on ryll develop)

- Verbs: `hello`, `status`, `screenshot`, `subscribe`,
  `unsubscribe`, `send_key` (`{"scancode": N, "state":
  "down"|"up"|"press"}`), `paste` (`{"text": "...",
  "char_delay_ms": N}`).
- Events: `latency`, `agent_connected`, `paste_completed`,
  `paste_failed`, `dropped`, `surface_drawn` (always), and
  `digest_updated` (only when built with `digest-decode`;
  otherwise absent from `supported_events` and subscribe
  returns an empty list — the test treats absence as a lane
  misconfiguration and fails).
- `digest_updated` data: `frame_counter` (u32, dedup key),
  `framebuffer_hash` (u32), `events` (serde passthrough of
  `shakenfist_visual_digest::Digest::raw_records` — the
  `Event` enum serialised by serde, so variant names like
  `Keypress`/`SceneTransition`/`PasteReceived` with their
  fields), `wallclock_us`.
- Single client at a time; a second connection gets `busy`.
- Spec: `ryll/docs/control-socket-protocol.md`. Read the
  whole document before writing the client module.

### The Sextant scenario (uncalibrated-sextant @ HEAD)

Source of truth: `src/scene.rs`, `src/bootloader.rs`,
`src/serial.rs` in the Sextant repo.

1. **Awaiting** — blinking cursor at (0,0), QR digest already
   painted and re-encoded on every blink transition. Any
   non-mode keypress pushes `Keypress` +
   `SceneTransition{awaiting→booting}` and moves on.
2. **Booting PRE** — 19 scripted lines at 200 ms pacing
   (≈4 s), each pushing `LineRendered` and refreshing the
   digest.
3. **Locked bootloader** — an R/I/A prompt.
   `r` replays a retry animation and re-prompts; `a` cold-
   resets (the drain never runs); `i` renders the base64
   blob of `sextant{HELLO_OPERATOR}` and an
   `Awaiting decoded payload>` prompt. The paste capture
   echoes printable ASCII, validates byte-exact, allows 3
   wrong attempts, and has a 60 s idle timeout — generous
   but not infinite, so the test must not dawdle between
   beats. Success pushes `PasteReceived{len, correct}` and
   returns to the boot script.
4. **Booting POST** — one final line, then
   `SceneTransition{booting→parked}`.
5. **Parked** — `SYSTEM ONLINE. AWAITING INSTRUCTIONS.` with
   blinking cursor. Any non-mode key pushes `Keypress` +
   `SceneTransition{parked→parked}`, after which the scene
   drains the ring buffer to serial (one line per event,
   `t=<ms> type=<tag> ...`, CRLF-terminated, terminated by a
   `type=refresh_stats ...` summary line) and ACPI-shutdowns.
   qemu exits; ryll's connection drops; the control socket
   vanishes.

### Tempest plugin today

- One test file, `tests/api/test_spice_via_kerbside.py`,
  inheriting Nova's compute base — needs a cloud, used only
  by the OpenStack lane.
- `config.py` registers the `kerbside` oslo.config group with
  `ca_cert_path` and `handshake_timeout`; `plugin.py` is the
  standard tempest plugin shim. New options join the same
  group.
- No `tests/scenario/` directory yet; no shared control-
  socket client anywhere in the plugin.
- Two NDJSON client implementations exist in kerbside
  (`loadtests/latency/orchestrator.py`,
  `tools/direct-qemu/smoke-client.py`) but the tempest plugin
  is an installable package and cannot import from either.
  Phase 7 adds a third, deliberately small, client inside the
  plugin; consolidating all three is noted as future work.

### Fixture vintage risk

`tests/fixtures/uncalibrated-sextant.qcow2` was committed in
phase 5. Sextant has since migrated its encoder to the shared
digest crate (wire-format-preserving, by phase 1's contract).
The committed image must still be verified to carry the
locked-bootloader paste challenge and a digest the phase 6
decoder can read; if stale, refresh it with
`tools/direct-qemu/rebuild-sextant-qcow2.sh` as part of step
7c and commit the new image (see Open questions).

## Mission and problem statement

After phase 7:

- `tempest-plugin/kerbside_tempest_plugin/ryll_client.py` is a
  stdlib-only NDJSON control-socket client usable by any test
  in the plugin: connect/hello, request/response correlation,
  event demux with predicate-and-deadline waiting, send_key,
  paste, screenshot-to-file, clean close, EOF tolerance.
- `tempest-plugin/kerbside_tempest_plugin/tests/scenario/test_sextant_scenario.py`
  drives the full canonical sequence and asserts:
  - hello advertises protocol 1.1 with `surface_drawn` and
    `digest_updated`;
  - a live digest stream before any input (Awaiting blinks);
  - per-beat digest records: the Awaiting keypress, the
    awaiting→booting transition, boot-line progress, the
    `bootloader_decision`-adjacent records, the
    paste-received record with `correct=true`, the
    booting→parked transition;
  - frame counters strictly increasing across all consumed
    digest events;
  - post-shutdown: the serial drain parses and contains the
    canonical ordered subsequence with monotonic timestamps
    and the terminal `refresh_stats` line;
  - screenshots saved per beat into the artifact dir.
- `config.py` gains `control_socket_path`, `serial_log_path`,
  `scenario_artifact_dir`, and `scenario_step_timeout`
  (default 60 s) in the `kerbside` group; unset
  `control_socket_path` skips the scenario test.
- The direct-qemu workflow builds ryll with
  `--features digest-decode`, installs tempest + the plugin
  into `/tmp/tempest-venv`, writes a minimal tempest.conf,
  and runs the scenario test as its final lane step via
  `tools/direct-qemu/run-scenario.sh`. Scenario artifacts
  (screenshots, tempest log) are uploaded with the existing
  artifact step.
- The master plan's phase 7 row reads "Implementation
  complete; PR pending operator", and its success-criteria
  bullet about the tempest test reflects `tests/scenario/`.
- `pre-commit run --all-files` clean on every commit;
  `actionlint` clean on the workflow.

## Open questions

These do not block writing this plan but must be resolved
before or during implementation:

- **Does credential-less tempest run cleanly?** A test class
  with no `credentials` declared should never touch keystone,
  and a minimal tempest.conf should satisfy config loading.
  This is believed true but unproven in this repo. Step 7d
  resolves it empirically; the fallback is invoking the test
  via `stestr run` (or `python -m testtools.run`) from the
  plugin directory with `TEMPEST_CONFIG` pointed at the
  generated conf, which sidesteps the `tempest run` CLI
  while keeping the test a real tempest test.
- **Is the committed qcow2 fixture current enough?** It must
  contain the locked-bootloader paste challenge and emit
  digests the shared-crate decoder parses. Step 7c verifies
  by driving the lane; if the fixture predates the challenge
  or the digest format diverged, rebuild and commit the new
  image (the rebuild script exists since phase 5).
- **Does the paste capture need a terminating Enter?** Read
  `capture_paste` in `bootloader.rs`: if submission is
  CR-triggered, the test sends `paste` with the payload then
  a `send_key` Enter press (or includes `\n` in the paste
  text if ryll's paste path forwards it). Resolve by reading
  the source; verify against the lane.
- **How does the test detect "bootloader prompt is up"?**
  Candidates: wait for digest records showing `LineRendered`
  rows past the PRE script length; wait a fixed generous
  sleep (PRE is ≈4 s, and the prompt then waits tens of
  seconds before any timeout path); or poll a screenshot.
  Default: digest-record progress with a hard per-step
  deadline and a screenshot dump on expiry. The implementing
  agent refines this against the real lane — it is the most
  likely flake point in the whole test.
- **Serial log visibility while qemu is running.** qemu's
  `-serial file:` appends as the guest writes, so the banner
  and mode dump are visible early; only the drain is
  shutdown-time. Assumed true (phase 5's banner poll relies
  on it); re-verify the drain is flushed before qemu exits.

## Execution

Each step is one logical change → one commit on a new
kerbside branch `test-harness-phase-7`, branched from
`develop`. The plan file itself travels on this branch.

Model guidance note for this phase: **Fable 5 is available
and sits a tier above Opus.** This phase is the deliberate
first experiment with it; step 7c (the scenario test) is the
Fable step, chosen because it composes two oracles, a busy
event stream, destructive teardown ordering, and timing
judgment into one piece of novel test code where subtle
wrongness is expensive (a flaky scenario test poisons every
future PR). The remaining steps map to the established
sonnet/opus tiers per the master template's guidance.

| Step | Repo | Effort | Model | Isolation | Brief for sub-agent |
|------|------|--------|-------|-----------|---------------------|
| 7a. Control-socket client module in the tempest plugin | kerbside | medium | opus | none | On branch `test-harness-phase-7`. Create `tempest-plugin/kerbside_tempest_plugin/ryll_client.py`: a stdlib-only (socket, json, time, os) NDJSON client for ryll's control socket, protocol v1.1. Read `ryll/docs/control-socket-protocol.md` end-to-end first (clone or use the local checkout at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/ryll`), and read both existing kerbside clients (`loadtests/latency/orchestrator.py`, `tools/direct-qemu/smoke-client.py`) for framing precedent. Single-threaded design: one buffered reader; every line is demuxed — frames with `"id"` resolve pending requests, frames with `"event"` append to an internal `collections.deque`. Public API: `connect(path, timeout)`, `hello()` (returns the result dict; sends protocol_version "1.1"), `call(method, params=None, timeout=...)` (raises `RyllRpcError` carrying the error code on `ok: false`), `subscribe(names)`, `wait_for_event(predicate, deadline)` (scans the deque then reads the socket with per-read timeouts until predicate matches or deadline expires; returns the matched event; raises `RyllTimeout` with the count and names of events seen — that detail matters for CI debugging), `screenshot_to_file(surface_id, path)` (base64 PNG per the spec), `close()`. EOF during a read raises `RyllConnectionClosed` — a distinct type, because the scenario test treats EOF as expected after the final keypress. Mode 0600 socket, `busy` error on connect must surface clearly. Python 3.10+, single quotes, 120-char lines. No tempest imports in this module (keep it reusable). Verify: `python3 -m py_compile`, `tox -eflake8` from the tempest-plugin directory if it has one (else repo-root pre-commit), `pre-commit run --all-files`. One commit. |
| 7b. Tempest config options for the scenario | kerbside | low | sonnet | none | Same branch. In `tempest-plugin/kerbside_tempest_plugin/config.py`, extend `KerbsideGroup` with: `control_socket_path` (StrOpt, default None — unset means the scenario test skips), `serial_log_path` (StrOpt, default None), `scenario_artifact_dir` (StrOpt, default None — when unset the test skips screenshot capture but still runs), `scenario_step_timeout` (IntOpt, default 60, help text naming it the per-beat deadline). Follow the existing option style (help strings, ordering). Check `plugin.py` registers the group via the standard `register_opts` / `get_opt_lists` pattern and needs no change beyond what the new options inherit. Verify: `python3 -m py_compile` on both files, `pre-commit run --all-files`. One commit. |
| 7c. The Sextant scenario test | kerbside | high | **fable** | none | Same branch. Create `tempest-plugin/kerbside_tempest_plugin/tests/scenario/__init__.py` and `tempest-plugin/kerbside_tempest_plugin/tests/scenario/test_sextant_scenario.py`. Before writing code, read: `ryll/docs/control-socket-protocol.md` (whole doc); `uncalibrated-sextant/src/scene.rs`, `src/bootloader.rs`, `src/serial.rs` (local checkouts under `/srv/kasm_profiles/mikal/vscode/src/shakenfist/`); the phase 7 plan's Situation section; `ryll_client.py` from step 7a. Class `SextantScenarioTest(tempest.test.BaseTestCase)` — no credentials, no cloud clients. `skip_checks`: skip unless `CONF.kerbside.control_socket_path`. The single test method drives: (1) connect + hello; assert protocol 1.1 and `digest_updated` ∈ supported_events (fail with a message naming the `--features digest-decode` build requirement if absent); (2) subscribe `digest_updated` (and `paste_completed` if useful); (3) wait for any digest event — proves the Awaiting QR decodes; screenshot beat 0; (4) `send_key` Enter (scancode 28, state press) — NEVER digits 0–6, they are Sextant mode keys; wait for a digest whose records include the awaiting→booting `SceneTransition` (records are serde-serialised `shakenfist_visual_digest::Event` variants; dump one event to the log first and match the actual shape rather than guessing field spelling); (5) wait for boot progress then the bootloader prompt (default detection: `LineRendered` rows advancing past the 19-line PRE script; hard deadline `scenario_step_timeout`; on expiry, screenshot + raise with events-seen diagnostics); (6) `send_key` `i` (scancode 23, press); (7) paste `sextant{HELLO_OPERATOR}` via the `paste` verb, `char_delay_ms` ≈ 20; read `capture_paste` in bootloader.rs to determine whether a terminating Enter is required and send it if so; (8) wait for a digest with `PasteReceived{correct: true}`; (9) wait for booting→parked transition; screenshot the Parked screen; (10) `send_key` Enter again, then tolerate `RyllConnectionClosed` on all subsequent socket activity — the guest is shutting down and ryll's socket vanishes; (11) poll `CONF.kerbside.serial_log_path` for a `type=refresh_stats` line (deadline `scenario_step_timeout`); (12) parse the drain into structured records and assert the canonical ordered subsequence: keypress, transition awaiting→booting, ≥10 `type=line` records, `bootloader_decision choice=ignore attempt=1`, `paste len=23 correct=true`, transition booting→parked, keypress, transition parked→parked; assert `t=` values are monotonically non-decreasing; (13) across all digest events consumed in the whole run, assert `frame_counter` strictly increased. Throughout: every digest event consumed is logged at debug with its frame counter; every beat saves a screenshot into `scenario_artifact_dir` when set; every deadline expiry screenshots before raising. Helper functions live in the test module (drain parser, record matcher); keep them dependency-free so they are unit-testable later. Local verification: the runner host has `/dev/kvm` and docker. Build ryll with digest-decode inside the ryll-dev Docker image (the host rustc is too old — phase 6 did exactly this; see the ryll repo's devcontainer pattern), copy the binary onto PATH, then bring the lane up locally with `tools/direct-qemu/lane-up.sh` (needs `setup-mariadb.sh` once) and run the test against it via `TEMPEST_CONFIG` + stestr or testtools. If the lane reveals the qcow2 fixture predates the bootloader challenge or the digest format, rebuild it with `tools/direct-qemu/rebuild-sextant-qcow2.sh` and include the refreshed image in this commit with a note in the commit body. Iterate until the test passes locally twice in a row (flake check). Verify: `python3 -m py_compile`, `pre-commit run --all-files`. One commit (plus the fixture refresh inside it if needed). |
| 7d. CI wiring for the scenario | kerbside | medium | opus | none | Same branch. (1) `.github/workflows/direct-qemu-functional.yml`: change the ryll build line to `cargo build --release --no-default-features --features digest-decode -p ryll`. Add a step after "Assert Sextant boot banner": "Run Sextant scenario test" calling `tools/direct-qemu/run-scenario.sh`. Extend the artifact `path` list with `/tmp/kerbside-ci/scenario/` (screenshots) and `/tmp/kerbside-ci/tempest.log`. (2) Create `tools/direct-qemu/run-scenario.sh` (`set -euo pipefail`, shellcheck-clean; no large inline YAML shell per repo convention): creates `/tmp/tempest-venv` (separate from the kerbside venv — tempest's oslo pins conflict with kerbside's), `pip install tempest` plus `pip install ./tempest-plugin`; writes a minimal tempest.conf to `/tmp/kerbside-ci/tempest.conf` containing only the `[kerbside]` options (`control_socket_path=/tmp/kerbside-ci/ryll-ci.sock`, `serial_log_path=/tmp/kerbside-ci/sextant-serial.log`, `scenario_artifact_dir=/tmp/kerbside-ci/scenario`, `scenario_step_timeout=60`) plus whatever minimal `[DEFAULT]`/lock-path boilerplate tempest needs to load; runs the one test by regex (`tempest run --config-file ... --regex test_sextant_scenario` from a workspace-less invocation, falling back to `stestr run` in the plugin dir with `TEMPEST_CONFIG` exported if `tempest run` insists on cloud config — resolve empirically, document which path won in the commit body); tees output to `/tmp/kerbside-ci/tempest.log`; exits with the test's status. Verify: `actionlint`, `shellcheck`, `bash -n`, `pre-commit run --all-files`. Do NOT run the workflow; the operator pushes and watches CI. Expect CI iteration follow-up commits on this branch — that is part of finishing this step, mirroring phase 5's pattern. One commit to start. |
| 7e. Docs + master plan status | kerbside | low | sonnet | none | Same branch. Update kerbside `README.md`, `AGENTS.md`, `ARCHITECTURE.md`: the tempest plugin now contains a scenario test driving Sextant over ryll's control socket on the direct-qemu lane; mention the skip-when-unconfigured contract and the new `[kerbside]` tempest options. Update `docs/plans/PLAN-test-harness.md`: phase 7 row → "Implementation complete; PR pending operator". If any `docs/` page describes the direct-qemu lane, add the scenario step to it. `pre-commit run --all-files` clean. One commit. |

### Sequencing notes

- 7a → 7b → 7c → 7d → 7e, strictly: the client precedes the
  test that imports it; the config options precede the test
  that reads them; the test precedes the CI step that runs
  it.
- 7c is the long pole and the Fable experiment. Its
  local-lane iteration loop is the heart of the phase; the
  step is not done until the test passes locally twice
  consecutively.
- 7d's first CI push is expected to need follow-up commits
  (tempest-without-cloud is the known unknown). As with
  phase 5, CI iteration is part of finishing the step, not a
  new phase.
- The operator opens the PR once the lane is green with the
  scenario step included.

### Branch and PR shape

- New branch `test-harness-phase-7` from kerbside `develop`.
  All five steps land there, one commit each (plus CI
  iteration commits on 7d and a possible fixture refresh in
  7c). This plan file and the master-plan row updates travel
  on the same branch.

## Agent guidance

This phase plan follows the conventions in `PLAN-TEMPLATE.md`
at the kerbside repo root. The execution model, effort
levels, brief-writing standards, and management-session
review checklist apply unchanged.

Model-choice addendum for this phase: the template's
model guidance predates Fable 5. Treat Fable as a tier above
opus for steps where novel design judgment and subtle
correctness interact — and as overkill everywhere sonnet or
opus already succeed with a good brief. Phase 7 uses Fable
for exactly one step (7c) as a deliberate experiment; the
management session should note in the phase wrap-up whether
the Fable output needed less rework than comparable opus
steps in phases 5–6, so the master template's guidance can
be updated from evidence rather than vibes.

Notes specific to phase 7:

- **The digest stream is busy and tiny-windowed.** Cursor
  blinks re-encode the digest about twice a second, and each
  event carries only the records that fit in 44 bytes. Never
  assert "the next event shows X"; always "an event within
  the deadline shows X", and log what was actually seen.
- **Sextant's digit keys are mode switches.** Sending `0`–`6`
  anywhere in the scenario triggers GOP mode changes (and
  `0` starts a multi-second cycle walk). Transitions use
  Enter; the bootloader uses letters.
- **After the final keypress, the lane is gone.** Guest
  drains serial and ACPI-shutdowns; qemu exits; ryll exits
  and unlinks the socket. EOF is success-shaped there.
  Everything the test needs from the socket must be
  collected before that keypress.
- **Don't let the test provision.** If a sub-agent reaches
  for subprocess/qemu/kerbside startup inside the test,
  push back — the lane owns provisioning; the test consumes
  config. That seam is what keeps the plugin
  substrate-agnostic.
- **Don't grow the protocol.** If a beat seems to need a new
  verb or event, stop and surface it to the management
  session; that is ryll work with its own plan, not a quiet
  phase 7 addition.
- **Match serde's actual record shape, not the plan's
  prose.** The `events` array in `digest_updated` is a serde
  passthrough of the digest crate's `Event` enum. Log a real
  event first; write the matcher against what serde actually
  emits (externally-tagged variant names, field spelling).

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the step and how
the work you intend to do aligns with that step's brief.

## Administration and logistics

### Success criteria

Phase 7 is done when:

- `tempest-plugin/kerbside_tempest_plugin/ryll_client.py`
  exists, stdlib-only, with the demux/wait/EOF semantics
  described above.
- `tempest-plugin/kerbside_tempest_plugin/tests/scenario/test_sextant_scenario.py`
  drives the full Awaiting → Booting → ignore → paste →
  Parked → shutdown sequence and asserts both oracles.
- The test skips cleanly when `control_socket_path` is unset
  (verified by running it without a tempest.conf `[kerbside]`
  section).
- The direct-qemu workflow runs the scenario test as its
  final lane step and a CI run is green end-to-end,
  including the scenario.
- Scenario screenshots and the tempest log appear in the
  uploaded CI artifacts.
- The committed Sextant qcow2 is verified current (or has
  been refreshed) with respect to the paste challenge and
  digest format.
- `pre-commit run --all-files`, `actionlint`, `shellcheck`
  clean on every commit.
- The master plan's phase 7 row reads "Implementation
  complete; PR pending operator" and its success-criteria
  bullet points at `tests/scenario/`.

### Future work

Items deliberately deferred from phase 7:

- **Consolidating the three Python control-socket clients**
  (loadtest orchestrator, smoke client, tempest plugin) into
  one shared implementation. Three small copies is two too
  many, but the packaging seams (tools/ scripts vs an
  installable plugin) make consolidation its own task.
- **Substrate-portable scenario runs.** Running the same
  test against the OpenStack lane needs ryll placement and
  serial-log access (Nova console-log?) decisions.
- **Channel-hash chain verification.** Recompute the eight
  per-channel CRC32C accumulators host-side from the driven
  inputs and assert them against the digest's hash block.
- **Digest/serial cross-oracle reconciliation.** Assert the
  last digest's records are a suffix of the serial drain —
  a stronger end-to-end integrity claim than asserting each
  oracle independently.
- **Mode-switch scenario coverage.** Sextant's `1`–`6`/`0`
  keys, `ModeSwitch`/`ModeCycle` records, and repaint
  behaviour are completely untested by phase 7's happy
  path.
- **Wrong-paste and retry/abort bootloader paths.** The
  `r` animation, the wrong-paste cap, and the timeout
  countdown are all assertable with the same machinery.
- **Flake telemetry.** If the scenario test flakes in CI,
  capture per-beat timings into an artifact CSV before
  reaching for retries.

### Bugs fixed during this work

Found and fixed in kerbside during step 7c's local lane
iteration:

- **`start-qemu.sh` used the removed inline SPICE
  `password=` parameter.** QEMU 10 (newer developer hosts)
  rejects it with "Invalid parameter 'password'". Switched
  to `-object secret` + `password-secret=`, supported since
  QEMU 5.2, so the debian-12 CI runner (QEMU 7.2) is
  unaffected.
- **`lane-up.sh` launched ryll without
  `--enable-paste-as-keystrokes`.** The control socket's
  `paste` verb returned ok but the inputs channel silently
  dropped the keystrokes (the Sextant fixture has no
  vdagent). The scenario's paste beat cannot work anywhere
  without the flag.

Found in ryll during the same iteration; workarounds in
kerbside, upstream fixes pending (raise as ryll issues):

- **`send_key` `press`/`up` writes the supplied scancode
  verbatim into the SPICE KEY_UP message** without setting
  the AT-set-1 release bit, so every `press` double-types
  the key in the guest (diagnosed via the digest keypress
  echo: the paste buffer became `isextant{...}`, len 24).
  The scenario test works around it with an explicit
  down(make) / up(make|0x80) pair, which stays correct
  against a fixed server.
- **`control-socket-protocol.md` documents `digest_updated`
  records as `{"kind": ..., "payload": ...}`**, but the
  implementation passes the digest crate's serde shape
  through: externally-tagged snake_case single-key objects,
  e.g. `{"scene_transition": {"from": "awaiting", ...}}`.
  The doc should be corrected to match the implementation.

Operational wart noted for reused (non-CI) hosts:
`lane-down.sh` kills the kerbside daemon but not its
`kerbside-proxy` / `kerbside-insecure-new` children, so
stale listeners EADDRINUSE the next lane. CI's fresh VMs
are immune; a proper lane-down fix is follow-up work.
