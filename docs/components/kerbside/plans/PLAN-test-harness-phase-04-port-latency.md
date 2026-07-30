# Phase 4: Port latency loadtest to control socket

Part of [PLAN-test-harness.md](/components/kerbside/plans/PLAN-test-harness/). This phase
lives entirely in kerbside. The plan file lives here in
`docs/plans/` per the master plan's single-home rule.

## Goal

Rewrite `loadtests/latency/` so it drives Ryll's headless control
socket (landed in phase 3) instead of the in-tree Python SPICE
client at `testclient/ryll/`, and delete `testclient/ryll/` along
with every reference to it. After this phase, the only SPICE
client kerbside builds against is the upstream Rust Ryll, and
`testclient/ryll/` no longer exists.

This phase is strictly a replacement of what we have today. The
scope is:

- Replace the loadtest's SPICE-client implementation. Same
  invocation surface (Docker image, `makeconsole.sh` entrypoint),
  same output shape (one CSV per run with one float per line),
  same CI consumers.
- Delete the now-orphaned `testclient/ryll/` tree.
- Update every doc that references either path.

Out of scope for phase 4:

- A new tempest test. The master plan flagged "does a tempest
  uefi-latency-guest test exist?" as an open question; the
  operator has resolved it as "no, and this phase does not add
  one". The legacy loadtest is the only consumer we replace.
- Wiring the latency loadtest into GitHub Actions. It is run
  out-of-band today; that continues unchanged.
- Adding new control-socket verbs or events. v1 of the protocol
  is sufficient for the replacement metric (see decisions below).
- Generalising `loadtests/latency/` to consume the static source
  driver (phase 5 covers the direct-qemu lane). This phase keeps
  the existing OpenStack-backed flow in `makeconsole.sh` intact.
- A Prometheus / metrics-server output. The CSV stays the only
  output artefact.
- Reworking `cleanupconsoles.sh`. It is untouched in this phase.

## Decisions baked into this plan

These were judgment calls made while drafting the phase plan
rather than questions to ask the operator. Flagged explicitly so
they can be challenged before code lands.

- **The latency metric definition changes — temporarily**. The
  legacy loadtest reports wall-clock time between a synthetic key
  press leaving the SPICE inputs channel and the next `draw_copy`
  arriving on the display channel (i.e. keypress-to-screen
  end-to-end). v1 of the control socket exposes the `latency`
  event sourced from SPICE PING/PONG
  (`main_channel.rs:~1032`). These are *different* measurements:
  PING/PONG is round-trip channel latency, not keypress-to-screen.
  After this phase, the CSV column will be PING/PONG sample
  latencies. The CSV file shape stays identical (one float per
  line, seconds, no header), so any consumer that treats the file
  as opaque keeps working — but the *numbers* are not directly
  comparable to the legacy run. This is a deliberate
  scope-bounding choice. **The operator's intent is to measure
  user-perceivable latency introduced by Kerbside, not just
  underlying connection latency, so this regression is explicitly
  temporary.** Restoring keypress-to-screen semantics requires a
  new event in the control socket (a `surface_drawn` event, or
  equivalent), which is committed to land in phase 6 (see the
  master plan's phase 6 row and the Future work section below).
  Document the metric change in the
  loadtest README so anyone reading old vs new numbers knows why
  they differ, and that it is a known-temporary state.
- **The orchestrator is a single Python file at
  `loadtests/latency/orchestrator.py`**. Stdlib-only (`socket`,
  `json`, `argparse`, `time`, `signal`, `threading`, `sys`,
  `pathlib`). No new pip dependency. The phase-3 starter at
  `shakenfist/ryll/examples/control-socket-demo.py` is the
  copy-paste base; this script is a hardened version of it with
  CSV output and signal handling for clean shutdown.
- **The Dockerfile becomes multi-stage**. Stage 1 (Rust build):
  pulls a `rust:1.XX-bookworm` base, clones the ryll repo from
  `main` (no pinned ref), runs `cargo build --release -p ryll`,
  produces the binary. Stage 2 (runtime): the existing
  `debian:bookworm` base, with Python deps, kerbside, the
  orchestrator script, the `makeconsole.sh` /
  `cleanupconsoles.sh` scripts, and the ryll binary copied from
  stage 1. No `testclient/` install line. **The ryll source is
  not pinned in this phase.** We always build from `main`. Pinning
  is deferred until ryll either cuts a release tag or we hit a
  reproducibility incident; both are out of scope for phase 4.
- **`makeconsole.sh` orchestrates two processes**: it starts
  ryll headless in the background with `--headless --url <spice
  url> --control-socket /tmp/ryll.sock`, waits for the socket
  file to appear (bounded loop with timeout), then runs the
  Python orchestrator against the socket. On orchestrator exit
  (or signal), it sends SIGTERM to ryll. The exit code of the
  orchestrator is the exit code of the script.
- **No Rust code lives in kerbside**. The Rust toolchain is a
  build-time concern of the Docker image only. The orchestrator
  is Python, the kerbside repo continues to be Python-only at
  the source-tree level.
- **Cadence stays 2 seconds**. The legacy CadenceThread sleeps
  2s between spacebar presses; the orchestrator does the same so
  any rough comparison of "samples per minute" between old and
  new runs is meaningful (even though the per-sample number's
  semantics change). The cadence is a `--cadence-seconds` flag
  on the orchestrator with a default of `2.0`.
- **Run duration is bounded by sample count, not wall clock**.
  Today the legacy loadtest runs until the SPICE display closes
  (effectively unbounded). The orchestrator takes
  `--sample-count N` (default 60, i.e. ~2 minutes at the default
  cadence) and exits after writing N samples to the CSV. A
  `--max-seconds` safety cap defaults to 600s. Making the run
  bounded keeps CI deterministic; the legacy "run forever" mode
  is genuinely undesirable here.
- **No retry of dropped events**. The orchestrator subscribes to
  `latency` and `dropped`. A `dropped` event is logged to stderr
  with the count; sample collection continues. We don't pause or
  back off — the bounded queue (256) is plenty for a 0.5 Hz
  event stream.
- **`testclient/` package install is removed wholesale from the
  Dockerfile**. The `pip install` of the testclient currently
  happens in the loadtest Dockerfile alongside `kerbside`. After
  this phase that line and the `ADD testclient/` line are gone.
- **The `testclient/` directory in the repo root is deleted**.
  Survey confirms `loadtests/latency/` is its only consumer, and
  README/AGENTS.md are the only docs mentioning it. No other
  source-tree reference exists.

## Situation

Today's latency-loadtest pipeline:

- `loadtests/latency/Dockerfile` builds a single-stage
  `debian:bookworm` image, pip-installs `kerbside` and
  `testclient/` as editable packages, and `ADD`s
  `makeconsole.sh` / `cleanupconsoles.sh`. `CMD` is
  `/srv/makeconsole.sh`.
- `loadtests/latency/makeconsole.sh` (83 lines): provisions an
  OpenStack instance from the `uefi-latency-guest` image (UEFI
  qcow2 from `images.shakenfist.com`), waits for ACTIVE, fetches
  the SPICE console URL, then invokes:
  ```bash
  ryll connect --display-type none --statistics-type none \
      --input-type cadence \
      --url ${console_url} \
      --logfile-path /tmp/results/logs-$$.txt \
      --latency-path /tmp/results/latency-$$.csv
  ```
  The `ryll` command above is the testclient/ryll Python entry
  point installed by the Dockerfile's `pip install`. It is not
  the upstream Rust ryll.
- `testclient/ryll/` is a self-contained Python SPICE client
  (~1400 lines across `main.py`, `common.py`,
  `decompressors.py`, `display_types.py`, `scancodes.py`,
  `statistics_types.py`). It implements MainThread,
  CursorThread, DisplayThread, InputsThread, and an optional
  CadenceThread that sends spacebar keydown/up every 2 seconds.
  Latency is captured in DisplayThread when a `draw_copy`
  arrives and a `last_key_event` timestamp is set — the
  difference is written to the CSV (`testclient/ryll/main.py`
  around line 812-817; cadence loop around lines 642-652).
- The CSV format is one float per line, no header, no metadata.
  Loadtest CI consumers (out-of-band, not in
  `.github/workflows/`) presumably aggregate or threshold on
  these values; the survey found no in-repo aggregator.
- `testclient/ryll/` is referenced in `AGENTS.md` (one table
  row), `README.md` (the loadtest section), and the loadtest
  Dockerfile. It is referenced nowhere else in the repo: no
  GitHub Actions workflow, no `pyproject.toml` entry, no
  tempest-plugin import, no docs/.

Control-socket contract (post-phase-3):

- Wire-format spec: `shakenfist/ryll/docs/control-socket-protocol.md`
  (commit `a54bd785` on the `test-harness-control-socket`
  branch; will be on `main` once phase 3's PR merges).
- Python starter: `shakenfist/ryll/examples/control-socket-demo.py`
  (224 lines, stdlib-only, copy-paste base for the orchestrator).
- Verbs the orchestrator needs: `hello`, `status`, `send_key`,
  `subscribe`.
- Events the orchestrator subscribes to: `latency`, `dropped`.
- The `latency` event payload is
  `{"sample_ms": f64, "wallclock_us": u64}`. Sample frequency
  is whatever SPICE PING/PONG produces (one per channel
  round-trip).

Ryll binary distribution: at the time of this phase, the
upstream `shakenfist/ryll` repo does not publish a release
binary kerbside can pull. The simplest answer is to build from
source in the Dockerfile (stage 1 of the multi-stage build); if
ryll later publishes binaries, this can change.

## Mission and problem statement

After phase 4:

- `loadtests/latency/Dockerfile` builds a multi-stage image
  whose runtime layer carries a release `ryll` binary, a Python
  3 orchestrator, and the existing shell scripts. No
  `testclient/` install.
- `loadtests/latency/orchestrator.py` connects to
  `/tmp/ryll.sock`, completes the hello handshake, subscribes to
  `latency` + `dropped`, sends a spacebar-down/up cadence at 2s,
  collects N latency samples (default 60), writes them to
  `/tmp/results/latency-$$.csv` in the legacy format (one float
  per line, seconds), then exits.
- `loadtests/latency/makeconsole.sh` continues to provision the
  OpenStack instance and fetch the SPICE URL as today, but its
  last block launches ryll headless in the background and runs
  the orchestrator in the foreground.
- `testclient/` is deleted from the repo. `AGENTS.md`,
  `README.md`, and `ARCHITECTURE.md` are updated.
- `pre-commit run --all-files` is clean. `loadtests/latency/`
  builds via `docker build`. A manual run against any reachable
  SPICE source produces a CSV with N rows of positive floats.

## Open questions

These need answers before or during the phase, but do not block
writing this plan:

- **Whether to emit the `wallclock_us` field anywhere**. Default
  plan: drop it; the legacy CSV has only one column. If a future
  consumer wants both, we can switch to two columns at that
  point. Document the choice in the orchestrator docstring.
- **Spacebar scancode**. Phase 3's `send_key` takes a raw
  `u16` scancode. The orchestrator hardcodes `0x39` (US-QWERTY
  PC keyboard set-1 spacebar). The legacy testclient also used
  set-1 scancodes. No question to resolve, but flagging because
  if a future SUT has a non-PC keyboard mapping the cadence is
  meaningless and the orchestrator silently produces 0 samples.

## Execution

Each step is one logical change → one commit on the `test-harness`
branch (the existing kerbside phase branch). Per the master plan's
single-branch discipline, do not open new branches.

| Step | Repo | Effort | Model | Isolation | Brief for sub-agent |
|------|------|--------|-------|-----------|---------------------|
| 4a. Write the Python orchestrator | kerbside | medium | sonnet | none | Land `loadtests/latency/orchestrator.py` as a stdlib-only Python 3.10+ script. Use `shakenfist/ryll/examples/control-socket-demo.py` (224 lines) as the structural starter. CLI: `--socket PATH` (default `/tmp/ryll.sock`), `--output PATH` (required, CSV path), `--sample-count N` (default 60), `--cadence-seconds F` (default 2.0), `--max-seconds F` (default 600.0), `--scancode HEX` (default `0x39`). Behaviour: connect, hello (client_name `kerbside-latency-loadtest`, protocol_version `"1.0"`), assert server's hello response, subscribe to `["latency", "dropped"]`, start a key-press cadence thread that sends `send_key {scancode, state: "down"}` then sleeps 0.1s then `send_key {scancode, state: "up"}` then sleeps `cadence_seconds - 0.1`. Main thread reads events; for each `latency` event append `f"{sample_ms / 1000.0}\n"` to the CSV (note the unit conversion: protocol gives ms, legacy CSV is seconds); for each `dropped` event print to stderr `dropped <count> events`; exit cleanly after N latency samples have been written OR after `max_seconds` wall-clock OR on SIGTERM / SIGINT. On any error during the run (parse failure, socket close, hello rejection) print to stderr and exit non-zero. Open the CSV with line-buffered writes so partial runs leave usable data. Include a module docstring linking to `https://github.com/shakenfist/ryll/blob/main/docs/control-socket-protocol.md` and to `docs/plans/PLAN-test-harness-phase-04-port-latency.md`. Add no integration test — manual verification against `examples/control-socket-demo.py` style mock is enough; the loadtest is exercised via its Docker image in CI. |
| 4b. Multi-stage Dockerfile + makeconsole.sh wiring | kerbside | medium | sonnet | none | Rewrite `loadtests/latency/Dockerfile` as multi-stage. Stage 1: base `rust:1.83-bookworm` (or whatever ryll's `rust-toolchain.toml` pins; check that file first), `git clone --depth 1 https://github.com/shakenfist/ryll.git /src` (no `--branch`, no pinned ref — always builds from `main`), then `cargo build --release -p ryll` (find the correct binary package name in ryll's workspace; the binary may be at `target/release/ryll`). Stage 2: base `debian:bookworm`, install `python3` + `python3-openstackclient` + `openstacksdk` + the system libs ryll needs at runtime (read ryll's runtime deps — typically `libgtk-3-0`, `libgstreamer1.0-0`, etc.; if it's a heavy list, consider whether headless mode actually requires the GUI libs — Ryll's GUI is opt-in, so the headless runtime should be lighter. Confirm by inspecting the ryll Cargo features used in stage 1.). Install kerbside. Do NOT install `testclient/`. `COPY --from=stage1 /src/target/release/ryll /usr/local/bin/ryll`. `COPY` the orchestrator and the two shell scripts. CMD remains `/srv/makeconsole.sh`. Then update `loadtests/latency/makeconsole.sh`: keep all the OpenStack provisioning logic (instance create, wait for ACTIVE, fetch console URL) unchanged. Replace the final `ryll connect ...` invocation with two steps: (1) `ryll --headless --url "${console_url}" --control-socket /tmp/ryll.sock &` (capture the PID); (2) a bounded wait loop (up to 30s) for `/tmp/ryll.sock` to appear; (3) `python3 /srv/orchestrator.py --socket /tmp/ryll.sock --output /tmp/results/latency-$$.csv` (capture exit code); (4) `kill -TERM ${RYLL_PID}` and `wait ${RYLL_PID}`; (5) `exit ${ORCH_EXIT_CODE}`. Use `set -euo pipefail` and a `trap` to clean up the background ryll on any script exit. Verify the script with `bash -n` and `shellcheck`. |
| 4c. Delete testclient/ryll/ and update docs | kerbside | low | sonnet | none | `git rm -r testclient/ryll/` and `git rm -r testclient/` if the directory becomes empty (verify nothing else lives under `testclient/` first). Remove the testclient table row from `AGENTS.md`. Update `README.md`'s loadtest section to describe the new flow: built image now bundles upstream Ryll's Rust binary; orchestrator drives the control socket; metric is PING/PONG round-trip (note the change from keypress-to-screen). Update `ARCHITECTURE.md` if it mentions the testclient — replace with a one-line pointer at `loadtests/latency/orchestrator.py` and a link to `shakenfist/ryll/docs/control-socket-protocol.md`. Grep the whole repo for any other reference to `testclient/` (`pyproject.toml`, `Makefile`, `tox.ini`, `tools/`, `docs/`, `.github/workflows/`) and remove or rewrite as appropriate. Update the master plan's phase 4 row in `docs/plans/PLAN-test-harness.md` from "Not started" to "Implementation complete; PR pending operator". |

### Sequencing notes

- 4a stands alone — the orchestrator is a leaf script that
  doesn't import anything from the rest of the repo. Land it
  first so the Dockerfile work in 4b has a real file to COPY.
- 4b lands second. It is the only step that exercises Docker;
  the sub-agent should `docker build` the image at the end of
  the step (and document the build time / image size in the
  commit body so we can track bloat). If `docker build` is not
  available in the sub-agent's environment, document the
  blocker in the commit message and the operator runs the
  build by hand.
- 4c is last so the testclient delete doesn't break any
  in-between intermediate state. The grep pass in 4c may turn
  up references the survey missed; resolve them inline.

**Branch and PR shape**: All phase 4 commits land on the
existing `test-harness` branch of kerbside. The operator opens
the kerbside PR (which already carries phases 1-3's kerbside
work) once phase 4 is done; the master plan can decide whether
to split that PR or ship it as one.

## Agent guidance

This phase plan follows the conventions in `PLAN-TEMPLATE.md` at
the kerbside repo root. The execution model, effort levels,
model-choice guidance, brief-writing standards, and management-
session review checklist all apply unchanged and are not
duplicated here.

Notes specific to phase 4:

- **The metric definition changes.** Sub-agents must NOT try to
  preserve the legacy keypress-to-screen semantics by inventing
  a new event or doing screenshot polling. The protocol-v1
  `latency` event is what this phase ships. Document the change
  visibly (in the orchestrator docstring, the loadtest README,
  and the phase-4 commits) so consumers of old CSVs know not to
  compare apples-to-oranges.
- **Headless ryll runtime deps.** Ryll has GUI features (gtk,
  gstreamer) and headless features. When the Dockerfile builds
  ryll in stage 1, prefer `--no-default-features --features
  headless` (or whatever ryll's headless feature flag actually
  is — read ryll's Cargo.toml). If the binary still pulls in
  the GUI libs at runtime, the runtime image gets noticeably
  larger; flag that in the commit body so the operator can
  decide whether to fix it now or defer.
- **The orchestrator's wallclock dependency.** The CSV legacy
  reader (if any) expects seconds as float. The protocol
  delivers `sample_ms` (milliseconds). Convert. Easy to miss;
  the orchestrator's test plan should include "open the CSV
  and check the numbers look like reasonable latency seconds
  (e.g. 0.001 to 0.5), not milliseconds (1 to 500)".
- **No tempest test in this phase.** The master plan question
  about a "uefi-latency-guest tempest test" is explicitly
  out-of-scope per the operator's resolution. If a sub-agent
  feels tempted to add a tempest harness for the new loadtest,
  push back: that is phase 7 territory.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the step and how the work
you intend to do aligns with that step's brief.

## Administration and logistics

### Success criteria

Phase 4 is done when:

- `loadtests/latency/orchestrator.py` exists, runs against a
  reachable control socket, and produces a CSV of the legacy
  shape (one float per line, seconds).
- `loadtests/latency/Dockerfile` builds in two stages. Stage 1
  carries the Rust toolchain and produces a `ryll` binary;
  stage 2 has only the runtime artefacts (Python, kerbside,
  ryll binary, scripts).
- `loadtests/latency/makeconsole.sh` orchestrates ryll + the
  Python orchestrator and exits with the orchestrator's status.
- `testclient/ryll/` is deleted from the repo. No source-tree
  reference to `testclient/` remains.
- `README.md`, `AGENTS.md`, and (if applicable) `ARCHITECTURE.md`
  are updated.
- The master plan's phase-4 row is marked
  "Implementation complete; PR pending operator".
- `pre-commit run --all-files` from kerbside root is clean.

### Future work

Items deliberately deferred from phase 4:

- ~~**Restore keypress-to-screen latency semantics (committed for
  phase 6+).**~~ **Resolved in phase 6 step 6d** (merged to
  develop via PR #110).  ryll v1.1 added a
  `surface_drawn` control-socket event (phase 6 step 6b) that
  fires once per display draw command; the orchestrator now
  subscribes to it, FIFO-pairs each `surface_drawn` with the
  oldest pending keypress wallclock_us, and writes the
  resulting delta as keypress-to-screen latency in seconds to
  the CSV.  The CSV shape (one float per line) is unchanged,
  so downstream consumers see the same column with the
  intended metric.  Hard-fails at startup if the server does
  not advertise `surface_drawn` in `supported_events` (no
  PING/PONG fallback by design).
- ~~**Shrink the loadtest image via a ryll `headless` Cargo
  feature (committed for phase 6).**~~ **Resolved in phase 6
  steps 6a + 6d.**  ryll's GUI and audio stacks are now gated
  behind default-on `gui` and `audio` Cargo features.
  `cargo build --release --no-default-features -p ryll` drops
  eframe / egui / egui-winit / arboard / rfd / cpal /
  opus-decoder / rtrb / winit from the dependency tree.
  Phase 6 step 6d switches `loadtests/latency/Dockerfile`
  stage 2 to that build and drops libasound2, libgl1,
  libx11-6, libxcb1, libxkbcommon0, libwayland-client0 from
  the runtime apt-get list.  The phase 5 direct-qemu CI lane
  workflow shrinks identically.  The features are named
  `gui` / `audio` (additive) rather than `headless`
  (subtractive) to keep `cargo build --all-features` working;
  the user-facing effect is identical.
- **Wiring the latency loadtest into GitHub Actions.** Today
  it's run out-of-band; CI integration is a separate piece of
  work.
- **Cross-version comparability.** Old CSVs and new CSVs are
  not directly comparable. A small companion script that
  annotates CSVs with the metric definition (e.g. a sidecar
  `.json` with the protocol version, cadence, scancode) would
  help future consumers — not done here.
- **Publishing a release ryll binary** kerbside can pull
  instead of cargo-building in stage 1. Worth doing when ryll
  cuts a stable release; halves the loadtest image build time.

### Bugs fixed during this work

(None yet.)
