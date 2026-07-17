# Automated SPICE test harness

## Prompt

Before responding to questions or discussion points in this
document, explore the kerbside codebase thoroughly. Read
relevant source files, understand existing patterns (the
SPICE protocol implementation in `kerbside/spiceprotocol/`,
the proxy connection model in `kerbside/proxy.py`, the
source driver abstraction in `kerbside/sources/`, the REST
API in `kerbside/api.py`, the SQLAlchemy/alembic data model
in `kerbside/db.py` and `alembic/`, Pydantic-based config in
`kerbside/config.py`, audit logging, the .vv file generation
path, and the legacy Python SPICE client at
`testclient/ryll/`). Ground your answers in what the code
actually does today. Do not speculate about the codebase
when you could read it instead. Where a question touches on
external concepts (SPICE protocol, QXL, vdagent, libvirt
graphics, OpenStack Nova consoles, oVirt console API,
Shaken Fist), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than
guessing.

This plan also crosses repository boundaries. Phases that
land in other repos must consult those repos in the same
grounded way:

- `shakenfist/ryll` — the Rust SPICE client. The control
  socket and digest decoding land here. Explore the
  channel handlers under
  `shakenfist-spice-renderer/src/channels/`, the headless
  session at `shakenfist-spice-renderer/src/session.rs`,
  and the input/output event enums in
  `shakenfist-spice-renderer/src/channels/mod.rs`.
- `shakenfist/uncalibrated-sextant` — the UEFI Rust test
  target guest. The visual digest format spec lives at
  `docs/visual-digest-format.md`; the encoder at
  `src/digest.rs`; the serial drain at `src/serial.rs`.
  No changes are expected here for v1 of this plan, but
  the digest crate extraction must not change the
  on-wire payload.
- A new repo for the shared `shakenfist-visual-digest`
  crate, holding the encoder, decoder, and a CLI
  decoder.

All planning documents for this plan — master and every
phase plan — live in this repo's `docs/plans/`,
regardless of which repo each phase's *implementation*
lands in. Kerbside is the main driver of this work, and
keeping every phase plan co-located keeps the design
conversation in one searchable place. Sub-agents
executing a cross-repo phase must therefore be briefed
that the plan they are following lives in
`shakenfist/kerbside/docs/plans/` even when all of
their commits will land elsewhere.

Consult `ARCHITECTURE.md` for the overall proxy
architecture, channel model, and connection lifecycle.
Consult `AGENTS.md` for build commands, project
conventions, key file map, and code organisation. The
`docs/` tree contains protocol documentation derived from
upstream SPICE sources.

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be
named for the master plan, in the same directory as the
master plan, and simply have `-phase-NN-descriptive`
appended before the `.md` file extension. Phase status is
tracked in the table under the Execution section below.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

We have three components that together could form an
automated SPICE test harness, but no glue between them:

- **Kerbside** (this repo) — the SPICE proxy. Existing
  tests are unit tests in `kerbside/tests/` plus a
  tempest plugin
  (`tempest-plugin/kerbside_tempest_plugin/tests/api/test_spice_via_kerbside.py`)
  that asserts the SPICE link handshake against a Nova
  instance but does not authenticate or drive guest I/O.
- **Ryll** (`shakenfist/ryll`) — the Rust SPICE client.
  Its `--headless` mode (`shakenfist-spice-renderer/src/session.rs:405-609`)
  runs a `tokio::select!` loop that drains channel events,
  optionally emits a single keypress every 2 seconds via
  `--cadence` as a latency probe, and optionally pastes
  text via `--paste-text`. The input event enum already
  covers keyboard, mouse, and paste; the channel event
  enum already exposes display surfaces, cursor, latency
  samples, and statistics. There is no scripting layer
  and no control interface.
- **Uncalibrated Sextant**
  (`shakenfist/uncalibrated-sextant`) — the UEFI Rust
  test target guest. Three scenes
  (Awaiting → Booting → Parked) driven by keypresses and
  a clipboard-paste decryption challenge. Two assertion
  oracles already exist: a versioned QR digest in the
  bottom-right of the framebuffer (spec at
  `docs/visual-digest-format.md` in that repo, encoder
  at `src/digest.rs`) that updates per painted line and
  on cursor blink, and a structured plain-text event log
  drained over serial at shutdown (`src/serial.rs:78`).

The current Kerbside CI deploys a full Kolla-OpenStack
all-in-one before running the tempest plugin. OpenStack
is not part of our long-term ecosystem; it was the
hypervisor stack with the most readily available
tooling. The CI workflow lives in
`.github/workflows/functional-tests.yml`. Hypervisor
backends live at `kerbside/sources/{base.py,ovirt.py,shakenfist.py}`
— there is no static / test backend today.

A latency loadtest exists at `loadtests/latency/`. It
runs against `uefi-latency-guest` (a separate image
fetched from `images.shakenfist.com`) using the legacy
Python SPICE client at `testclient/ryll/`. The tempest
plugin does **not** currently reference
`uefi-latency-guest`. The user's original framing
mentioned a tempest test using it; on inspection, only
the loadtest does. This may indicate planned work that
never landed, or a misremembered detail — to be
clarified during phase 4 planning.

The Python client at `testclient/ryll/` is the
predecessor to `shakenfist/ryll`. The Rust rewrite was
started from lessons learned building the Python
version, and is now a strict feature superset. The
loadtest is the only remaining consumer of the Python
client; removing the `testclient/ryll/` tree is part
of this plan (phase 4). Note the namespace collision:
"Ryll" in this document means the Rust client
(`shakenfist/ryll`) unless the prefix `testclient/` or
the qualifier "legacy Python" is used.

The digest format on Uncalibrated Sextant is a versioned
binary protocol (10-byte header, eight per-channel
chained CRC32C hashes, up to 44 bytes of raw recent
events, 4-byte framebuffer integrity hash). The encoder
exists in one repo; no decoder exists anywhere.

## Mission and problem statement

Build an end-to-end automated SPICE test harness that
exercises Kerbside as a proxy in front of a real qemu/KVM
guest (Uncalibrated Sextant), driven by Ryll over a
control socket, with assertions against both the live QR
digest and the post-mortem serial drain.

The harness must:

- Not require OpenStack or any other heavyweight control
  plane to run. Direct qemu + Kerbside + Ryll, glued by
  a small static hypervisor driver, is the v1 target.
- Re-cover the latency loadtest's scope on the new
  substrate early, so the control socket design is
  validated against an existing real test before we
  build new ones on top.
- Allow the tempest plugin to grow Sextant-driven
  scenarios (paste round-trip, scene transitions,
  digest assertions) without changing how it integrates
  with whatever cloud is underneath. The same plugin
  should be able to run against direct qemu, against
  the existing OpenStack lane, and (eventually) against
  a Shaken Fist deployment.
- Leave the door open for the control socket to be
  reused for non-test purposes — interactive debugging,
  one-off bug repros, and a possible SPICE MCP server
  later. This shapes the verb set: coarse, intent-
  bearing primitives, not raw protocol pokes.

Out of scope for v1:

- Mouse, USB redirection, clipboard via vdagent, audio,
  and WebDAV. These can be added once the spine is in
  place and Sextant grows the matching scenes.
- Replacing the existing OpenStack CI lane. We add a
  parallel direct-qemu lane first; whether to demote or
  retire the OpenStack lane is a follow-up decision.
- A Shaken Fist CI lane. That waits on the SF installer
  rewrite.

## Open questions

These are deferred to the relevant phase plans, not the
master plan, but listed here so they are not forgotten:

- **Shared crate naming and home.** Working name is
  `shakenfist-visual-digest`. Open: own repo vs living
  in one of the existing repos. Lean toward own repo
  since neither side naturally owns it. Decide at the
  start of phase 1.
- **Ryll digest dependency model.** Cargo optional
  feature (`digest-decode` off by default) so
  production builds carry zero digest code, vs harness-
  side decode via the crate's CLI tool with Ryll only
  exposing raw screenshots. Lean feature flag; decide
  at the start of phase 6.
- **Control socket transport and framing.** Unix socket
  + line-delimited JSON is the working assumption. Open:
  whether to also stream `DigestUpdated` events
  asynchronously, or require polling. Recommend
  bidirectional: requests/responses for actions,
  unsolicited event stream for observations (digest,
  latency, frames). Decide during phase 3.
- **Phase 4 scope: does a tempest uefi-latency-guest
  test exist?** Grep says no, only the loadtest does.
  Either the user has planned-but-not-landed work, or
  the framing was approximate. Resolve before drafting
  phase 4.
- **Sextant image distribution.** Working assumption is
  commit the qcow2 to this repo. Open: size budget, LFS
  vs plain git, whether to also expose it as a CI
  artifact for cross-repo consumers.
- **Direct-qemu CI runner shape.** GitHub Actions
  runners need `/dev/kvm`. We have self-hosted runners
  with `vm`, `static`, `debian-12` labels (per
  `.github/actionlint.yaml`). Confirm which label set
  gives us nested KVM during phase 5.
- **OpenStack CI lane fate.** Once direct-qemu CI is
  proven, do we retire it, demote to nightly, or keep
  on PRs? Defer until after phase 7. **Resolved in phase
  8 (2026-06-20): keep the cloud-compat lane per-PR,
  both legs blocking, for now** — Kerbside PRs are
  frequent and getting Nova reliable with Kerbside is the
  primary focus, so the per-PR Nova coverage is worth its
  cost. A future demotion to a schedule is foreseeable;
  see the revisit criteria in Future work.

## Execution

Phases land across three repos. The "Repo" column tells
you where the code goes. Every phase plan lives in this
directory regardless of repo, so the plan stays
searchable from one place.

| Phase | Repo | Plan | Status |
|-------|------|------|--------|
| 1. Shared visual-digest crate | new (`shakenfist-visual-digest`) | [PLAN-test-harness-phase-01-digest-crate.md](/components/kerbside/plans/PLAN-test-harness-phase-01-digest-crate/) | Implementation complete; Sextant PR pending operator |
| 2. Static source driver | kerbside | [PLAN-test-harness-phase-02-static-hypervisor.md](/components/kerbside/plans/PLAN-test-harness-phase-02-static-hypervisor/) | Merged |
| 3. Control socket on Ryll | ryll | [PLAN-test-harness-phase-03-control-socket.md](/components/kerbside/plans/PLAN-test-harness-phase-03-control-socket/) | Merged to ryll develop |
| 4. Port latency loadtest to control socket and remove legacy `testclient/ryll/` | kerbside | [PLAN-test-harness-phase-04-port-latency.md](/components/kerbside/plans/PLAN-test-harness-phase-04-port-latency/) | Merged |
| 5. Direct-qemu CI workflow | kerbside | [PLAN-test-harness-phase-05-direct-qemu-ci.md](/components/kerbside/plans/PLAN-test-harness-phase-05-direct-qemu-ci/) | Merged; lane green in CI |
| 6. Ryll Cargo feature work: digest decoding, headless feature, restore keypress-to-screen latency | ryll | [PLAN-test-harness-phase-06-digest-decoding.md](/components/kerbside/plans/PLAN-test-harness-phase-06-digest-decoding/) | Merged (ryll and kerbside sides) |
| 7. First Sextant scenario tempest test | kerbside | [PLAN-test-harness-phase-07-scenario-test.md](/components/kerbside/plans/PLAN-test-harness-phase-07-scenario-test/) | Merged (kerbside PR #82) |
| 8. OpenStack CI lane disposition + oVirt provisioning flake | kerbside (+ shakenfist/actions) | [PLAN-test-harness-phase-08-openstack-disposition.md](/components/kerbside/plans/PLAN-test-harness-phase-08-openstack-disposition/) | Merged (kerbside PR #88) |

Indicative effort and model recommendations (firmed up
when each phase plan is written):

| Phase | Effort | Model | Notes |
|-------|--------|-------|-------|
| 1 | medium | sonnet | Self-contained crate extraction; spec already exists. New repo bootstrap is the main novelty. |
| 2 | medium | sonnet | Follows the existing `BaseSource` interface in `kerbside/sources/base.py`. Bounded scope, clear pattern. |
| 3 | high | opus | New protocol design that must serve both tests and future MCP. Touches Ryll's tokio event loop. Getting the verb set wrong is expensive to undo. |
| 4 | medium | sonnet | Rewrite the loadtest to drive the control socket, then delete the legacy `testclient/ryll/` tree (the loadtest is its only consumer). Also touches `loadtests/latency/Dockerfile` and any README / AGENTS.md references. Validates phase 3's API; small but high-signal. |
| 5 | high | opus | New CI workflow integrating multiple binaries, debugging KVM/runner-environment unknowns, many edge cases. |
| 6 | high | opus | Three concerns bundled because they all touch Ryll's Cargo features and channel handlers. (1) SurfaceMirror integration + QR detection on draw-event change + correctness around when to re-decode, gated behind a `digest-decode` Cargo feature off by default. (2) A `headless` Cargo feature that excludes the GUI/audio stack (eframe, egui, egui-winit, cpal) so kerbside's loadtest and direct-qemu CI images can drop libgl1 / libx11-6 / libxcb1 / libxkbcommon0 / libwayland-client0 / libasound2 from the runtime layer. The phase 4 Dockerfile and phase 5 CI image both switch to `cargo build --release --no-default-features --features headless` once this lands; flagged in phase 4's `Bugs fixed during this work` for cross-reference. (3) A `surface_drawn` control-socket event so phase 4's loadtest can restore keypress-to-screen latency semantics (the user-perceivable metric Kerbside is being measured against); orchestrator switch-back is part of this phase. |
| 7 | high | mixed (fable / opus / sonnet) | Originally rated medium/sonnet ("mostly glue"); re-rated while drafting the phase plan. The scenario test composes two oracles (busy digest event stream, post-mortem serial drain) with destructive teardown ordering and credential-less tempest integration — more than glue. The phase plan assigns Fable 5 (a tier above opus, released after this table was first written) to the scenario-test step as a deliberate first experiment, opus to the CI wiring, sonnet to the glue. |
| 8 | low–medium | opus / sonnet | Originally "a CI workflow tweak plus a documentation update". The disposition decision (keep the cloud-compat lane per-PR for now) is indeed just a documentation record, but during drafting the phase absorbed the root-cause fix for the oVirt provisioning flake — a `shakenfist/actions` ansible change (medium, opus, CI-verified) gating instance readiness on cloud-init completion rather than just an open SSH port — plus a kerbside workflow fix for the cosmetic `workflow_dispatch` target-skip false-red (low, sonnet). Fable not used; phase 7 was the experiment. |

### Sequencing notes

- Phases 1 and 2 are independent and can run in either
  order (or in parallel).
- Phase 3 is the long-lead item; ideally we start it as
  soon as phase 1 has a working decoder we can prototype
  against in Ryll. Note that phase 3 itself does **not**
  depend on phase 1 — the control socket lands without
  any digest knowledge.
- Phase 4 validates phase 3 in isolation; it does not
  need phase 6. The loadtest image built in phase 4 carries
  the full GUI/audio runtime stack because ryll has no
  headless Cargo feature yet; phase 6 adds that feature and
  the loadtest Dockerfile switches to it then. Tracked in
  phase 4's Future work and phase 6's effort notes.
- Phase 5 needs phases 2 (static hypervisor) and 3
  (control socket) but not phases 1 / 6 / 7. Like phase 4,
  the phase 5 CI image carries the heavy ryll runtime until
  phase 6's `headless` feature lands.
- Phase 6 needs phase 1 (the decoder crate). It is the
  natural home for any other ryll Cargo feature work,
  including the `headless` feature that slims the kerbside
  loadtest and CI images.
- Phase 7 needs phases 1, 2, 3, 5, and 6 — the first
  phase that exercises the full stack.
- Phase 8 is a follow-up to phase 7.

So the critical path is roughly: 2 → 3 → 5, with 1 → 6
in parallel, joining at 7. Phase 4 fits opportunistically
after 3.

## Agent guidance

This plan follows the conventions in `PLAN-TEMPLATE.md`
at the repo root. The execution model, effort levels,
model-choice guidance, brief-writing standards, and
management-session review checklist all apply unchanged
and are not duplicated here. Phase plans should fill in
the per-step tables described there.

Specific notes for this plan:

- Cross-repo work needs cross-repo back-briefing. Before
  starting a phase whose code lives in another repo, the
  management session should confirm the operator has the
  matching repo checked out and that the sub-agent
  understands the phase plan lives in
  `shakenfist/kerbside/docs/plans/` even though the
  commits will land elsewhere.
- Each phase that crosses repos should produce its own
  commit(s) in the right repo. Do not bundle ryll and
  kerbside changes into the same git operation. Commit
  messages in the implementing repo should reference the
  master plan path (e.g.
  `shakenfist/kerbside/docs/plans/PLAN-test-harness.md`)
  so the trail back is obvious.
- The control socket protocol is the highest-impact
  design decision in this plan. Phase 3's plan should be
  reviewed end-to-end before any implementation starts,
  and the phase 4 port should be drafted concurrently to
  catch verb-set gaps early.

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully
implemented because the following statements will be
true:

* `pre-commit run --all-files` passes on every commit
  on this branch.
* A new tempest test in
  `tempest-plugin/kerbside_tempest_plugin/tests/scenario/`
  (originally written as `tests/api/`; corrected during
  phase 7 planning — it is a scenario test by tempest's
  own taxonomy) boots an Uncalibrated Sextant qcow2 under
  direct qemu/KVM, fronts it with Kerbside via the static
  hypervisor driver, drives the
  Awaiting → Booting → paste → Parked sequence via
  Ryll's control socket, asserts the QR digest reflects
  the inputs sent, and asserts the serial drain at
  shutdown matches the expected event sequence. All of
  this runs without OpenStack. (The test consumes a lane
  brought up by `tools/direct-qemu/`; it does not boot
  qemu itself.)
* The legacy latency loadtest in `loadtests/latency/`
  drives Ryll's control socket. Its latency numbers
  are unchanged within noise vs the pre-port baseline.
* The legacy Python SPICE client at `testclient/ryll/`
  has been deleted, along with any remaining references
  to it in the Dockerfile, README, AGENTS.md, and
  ARCHITECTURE.md.
* Ryll's production binary does not carry the
  visual-digest crate unless built with the
  `digest-decode` feature.
* Ryll's `headless` Cargo feature lets the loadtest and
  direct-qemu CI images build a slim binary that does not
  link eframe / egui / egui-winit / cpal, and their runtime
  layers drop the corresponding system shared libraries
  (libgl1, libx11-6, libxcb1, libxkbcommon0,
  libwayland-client0, libasound2).
* The shared `shakenfist-visual-digest` crate is the
  sole home of the digest format. Uncalibrated Sextant
  consumes it for encoding; Ryll consumes it for
  decoding; the format spec lives there.
* `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, and the
  relevant pages under `docs/` are updated to describe
  the static hypervisor driver, the direct-qemu test
  workflow, and the way Sextant + Ryll wire into the
  tempest plugin.

### Future work

Items deliberately deferred:

- **Mouse / USB redirect / clipboard via vdagent /
  audio / WebDAV scenario tests.** Need matching scenes
  or hooks in Sextant first; Sextant's pointer
  collector is explicitly marked deferred in its
  `ARCHITECTURE.md`.
- **Shaken Fist CI lane.** Blocked on the SF installer
  rewrite (per session conversation 2026-06-02).
- **Demote the cloud-compat lane to a schedule.** Phase 8
  kept the "Test cloud compatibility" lane (oVirt + kolla
  legs) per-PR and blocking. Demote it to nightly +
  `workflow_dispatch` when any of: the Nova+Kerbside
  integration has been stable for a sustained period and
  the per-PR signal stops catching regressions; PR volume
  drops enough that per-PR cost outweighs value; or runner
  capacity becomes a binding constraint. Until then it
  stays per-PR.
- **Audit the multi-node provisioning playbooks for the
  same port-only SSH wait.** Phase 8 fixed the readiness
  gate in `shakenfist/actions`
  `ansible/kerbside-single-node.yml`; the sibling
  `kerbside-multi-node.yml` and `kerbside-multi-node-2.yml`
  carry the identical `wait_for port:22 search_regex:OpenSSH`
  pattern and want the same cloud-init readiness gate.
- **SPICE MCP server.** The control socket is shaped so
  that an MCP server fronting it is a follow-up
  exercise, not a redesign. Out of scope here.
- **Type checking via mypy.** Kerbside has no `tox -emypy`
  env; setting one up is a separate cleanup.
- **`pre-commit install` per clone.** The hook config
  is in the repo but each developer/runner must run
  `pre-commit install` to wire it as a git hook. Worth
  a contributors' note when we touch CONTRIBUTING / the
  readme.
- **Remove the "direct" .vv endpoint and its UI affordance.**
  `kerbside/api.py:368` exposes `ConsolesDirectVirtViewer` at
  `/console/direct/<source>/<uuid>/console.vv`, which builds
  a virt-viewer file pointing at the hypervisor's SPICE port
  instead of kerbside's proxy. `kerbside/api/templates/consoles.html:62`
  surfaces it as a "Connect directly" dropdown item. The
  direct endpoint exists for historical debugging convenience
  but bypasses every value kerbside provides (authentication,
  TLS termination, audit, ticket abstraction). Phase 5's
  smoke-check exposure of this trap (a sub-agent reached for
  the direct endpoint and had to be redirected to the proxy
  endpoint to actually test the proxy path) is the second
  time it has caused confusion. Remove both the endpoint
  registration and the UI link in a follow-up commit; do an
  initial grep across the codebase, docs, and tempest plugin
  to make sure no test or doc relies on the direct flow
  before deleting.

### Bugs fixed during this work

This section should list any bugs we encounter during
development that we fixed. Per-phase bugs are logged in the
phase plans; cross-phase interaction bugs are logged here.

- **The phase 5 smoke client hard-asserted control-socket
  protocol version "1.0".** When phase 6's ryll merge bumped
  main to v1.1, every direct-qemu lane run began failing at
  the smoke step (the workflow builds ryll from main), which
  also masked the phase 7 scenario step on the phase 7 PR
  itself. Fixed post-phase-7-merge: the smoke client now
  asserts the major version only, matching the protocol's
  compatibility model. Lesson: lane checks against a
  fresh-from-main ryll must never assert exact minor
  versions.
- **The oVirt CI leg's provisioning readiness gate waited
  only for an open SSH port, not cloud-init completion.**
  The `shakenfist/actions` `kerbside-single-node.yml` wait
  (`wait_for port:22 search_regex:OpenSSH`) returned as
  soon as sshd presented a banner; cloud-init then
  regenerated host keys and restarted sshd, and the
  runner's next real SSH was dropped with "connection
  refused" (~10% of oVirt `pull_request` runs, load-
  dependent). Fixed in phase 8 by adding a `cloud-init
  status --wait` readiness gate (plus `wait_for_connection`)
  after the banner wait. Tracked as `shakenfist/actions`
  issue #2. Lesson: an open port is not a ready instance;
  gate on cloud-init.
- **The cloud-compat lane reported unselected
  `workflow_dispatch` targets as red.** Each matrix job
  began with a step calling `core.setFailed('Target
  skipped')` for the non-selected target, and the
  `always()` artifact steps then timed out SSHing a guest
  that was never provisioned — so manual/develop runs
  looked perpetually broken and masked the real per-PR
  gate health. Fixed in phase 8 by replacing the
  `setFailed` step with a job-level `if:` so unselected
  targets skip cleanly. Lesson: skip, don't fail, for a
  deliberately-not-run matrix target.

### Documentation index maintenance

`docs/plans/index.md` should gain a Master-plans entry
for this plan when the first phase plan is written, and
the row should track overall status. Phase plan files
themselves should not be added to `index.md`.
