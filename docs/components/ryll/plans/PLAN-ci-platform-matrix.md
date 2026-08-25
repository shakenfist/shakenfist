# CI platform-matrix expansion

## Prompt

Expand ryll's CI to catch platform-specific runtime bugs that
the current matrix lets through. Today's CI builds and runs
unit tests on Linux, macOS, and Windows, but the runtime smoke
tests (`tools/web-smoke.sh`, `--web` TLS) and lint run on Linux
only. Bugs that need real platform execution to surface — like
the rustls `CryptoProvider` panic that broke macOS builds at
TLS connect time but never showed on Linux (commit `a9aff050`,
2026-05-08) — slip through and only surface during dogfooding.

This master plan was spun out of bench observations during the
session-001 feedback work. It is independent of the session-001
phases and the macOS-runtime-metrics plan, and can land in any
order.

When working through phases, follow the project's plan
conventions (per-phase plan files named
`PLAN-ci-platform-matrix-phase-NN-*.md`, one logical change per
commit, master-plan table updated as work lands).

## Situation

### What CI does today

`.github/workflows/ci.yml` defines:

- **Lint** (`cargo fmt --check` + `cargo clippy`): Linux only.
- **Build matrix** across Linux / macOS / Windows. Each
  matrix cell:
  - Builds `cargo build --release -p ryll`.
  - Runs `cargo test --workspace`.
  - Linux-only: `tools/web-smoke.sh` (HTTP) and
    `tools/web-smoke.sh --tls` (HTTPS) against the just-built
    binary in `--web` mode.
  - Produces a platform-shaped artifact (.deb, .rpm,
    macOS tarball, Windows zip).
- `tools/web-smoke.sh` is a Bash script — it cannot run on the
  Windows runner without WSL or a PowerShell rewrite, and it
  is gated `if: runner.os == 'Linux'` for that reason on the
  macOS runner too even though Bash exists there.
- QEMU integration tests (`make test-qemu*`) run nowhere in
  CI today — they need KVM and a libvirt-style stack.

### Since this plan was written: the two-tier restructure

The description above is CI as of 2026-05-08. On 2026-08-10
[PLAN-two-stage-ci.md](/components/ryll/plans/PLAN-two-stage-ci/) split `ci.yml`
into a smoke tier on `pull_request` and a merge tier on
`merge_group`, and put `develop` behind a merge queue. The job
inventory is unchanged in substance, but where a job runs now
matters. See [ci.md](/components/ryll/ci/).

Two consequences for this plan:

- **The platform runtime smokes belong in the merge tier.**
  They need macOS and Windows runners, the slowest in the
  pipeline; running them on every push to a pull request is
  exactly the cost the two-tier split exists to avoid. Prefer
  adding them as steps in the existing `build` matrix, which
  is already merge-tier, over adding new jobs.
- **Any genuinely new job must be added to a gate's `needs`.**
  A merge-tier job goes in `can_merge`; a smoke-tier one goes
  in `can_enqueue` and, if the automated reviewer should wait
  for it, in `automated_reviewer`. A job no gate depends on
  can fail without blocking a merge.

Phase 3 (the smoke-test portability audit) also gains a cheaper
option it did not have: `make check-windows` cross-compiles the
`x86_64-pc-windows-gnu` triple from the Linux devcontainer in
the smoke tier, so compile-level Windows breakage is caught
before the queue. It runs nothing, so it does not substitute
for the runtime coverage this plan is about.

### Bug classes the current matrix catches

- Compile errors anywhere in the workspace, on every platform.
- Unit-test regressions, on every platform.
- Lint violations and formatting drift (Linux only — but lint
  is platform-independent in practice).
- `--web`-mode regressions, including TLS-cert loading and
  HTTP routing — Linux only.
- Build artifact packaging (.deb / .rpm / .tar.gz / .zip).

### Bug classes the current matrix misses

The session-001 work has surfaced two specific gaps:

1. **Runtime startup paths only exercised inside `--web` smoke,
   on Linux.** The rustls `install_default()` panic at
   `client.rs:157` lay dormant because no test ever
   constructed a SPICE TLS connector outside `--web` mode.
   `--web` happened to install a `CryptoProvider` early; the
   GUI / headless path did not. Tests *passed*. Local macOS
   dogfooding fell over the moment a TLS SPICE target was
   contacted.

2. **Platform-specific runtime APIs** (Phase 02 of
   session-001-feedback adds `NSProcessInfo.beginActivityWithOptions`
   for macOS App Nap opt-out; G1's macOS-runtime-metrics plan
   adds Mach `task_info` / `thread_info` calls). Both are
   cfg-gated to `target_os = "macos"`. Our current macOS
   matrix cell *would* compile them but cannot exercise them
   meaningfully — a `cargo test` running on a fresh macOS
   runner is foreground-active, so App Nap conditions never
   trigger; an FFI call returning the right struct in unit
   isolation says nothing about a real session's behaviour.
   This is harder to address than (1), and may require a
   "manual-but-checklisted" QA pass rather than full
   automation. See Phase 4.

3. **Bash-only smoke tests** force runtime-test parity gaps:
   anywhere a test is written in Bash, Windows is excluded by
   construction.

### Why "everything on every platform" is the wrong answer

GitHub Actions runners price per minute (current public
pricing, 2026):

| Runner | Linux | Windows | macOS |
|---|---|---|---|
| Cost multiplier vs Linux | 1× | 2× | 10× |
| Cold-cache test latency (this repo) | ~6 min | ~12 min | ~10 min |
| Flake rate (observed, this repo) | low | low | medium |

Naïvely promoting every Linux job to all three platforms
roughly triples wall-clock CI latency on every PR (each
matrix cell ~10 min, three concurrent cells gated by the
slowest) and quadruples the per-PR cost (because macOS
dominates the bill). That tradeoff is fine for a release
build; it is wasteful for a typo-fix PR.

The right answer is **graduated coverage**: every platform
runs the parts that catch *platform-specific* bugs, and only
Linux runs the parts that don't (lint, formatting, deep
integration). Phase plans below pick one expansion at a time
and judge it on bug-class coverage per minute spent.

## Mission and problem statement

Make ryll's CI catch the bug classes that currently only
surface during macOS / Windows dogfooding. Specifically:

1. The TLS startup path on every platform, on every PR.
   (Catches the rustls panic class.)
2. Runtime smoke tests that work on Windows, not just Linux.
   (Catches the bash-script-portability class.)
3. A clear, documented manual-QA checklist for the bug
   classes automation cannot reach (App Nap, code-signing,
   Gatekeeper interactions). (Acknowledges the limit, doesn't
   pretend automation handles it.)

Out of scope: Linux-runner-equivalent integration testing on
macOS / Windows (no KVM, no QEMU SPICE stack); browser-based
automation of `--web` mode (separate plan); cross-compilation
matrices (we already build natively).

## Approach

The plan breaks into five phases. Phases 1 and 2 are no-regret
expansions that fit comfortably in current CI budgets. Phase 3
is a portability cleanup that unblocks Phases 1 and 2 on
Windows. Phase 4 documents a manual-QA boundary; the work
itself is a doc, not automation. Phase 5 is the pre-push audit
of everything the first four phases changed.

### Phase 01 — Cross-platform GUI/headless TLS smoke test

Add a smoke test that:

- Spins up a minimal TLS-capable echo server in-process
  (rustls server config, self-signed cert, pinned CA passed
  to ryll via the same path the SPICE client uses).
- Invokes ryll's TLS client setup path (constructs a
  `SpiceClient` with a TLS-port `ConnectionConfig`,
  attempts the handshake).
- Asserts the handshake reaches "connected" (the in-process
  server logs the client hello) before tearing down.
- Does *not* require a real SPICE server — the server hello
  is enough to confirm rustls didn't panic and the client
  reached the network layer.

Where it lives: a new integration test in
`shakenfist-spice-protocol/tests/tls_handshake.rs`, picked up
automatically by the existing `cargo test --workspace` in the
build matrix. No new CI step required.

This catches:
- The rustls `CryptoProvider` install regression.
- Any future TLS feature-unification surprise (cert-loader
  pulled in via a transitive crate, etc.).
- Hostname-verifier behaviour on every platform (the
  `SpiceCaVerifier` in `client.rs` has subtle platform-
  dependent behaviour on root-store loading).

Cost: ~5–10 s extra test runtime per matrix cell. Fits inside
the existing test step.

Bug-class coverage per minute: very high. The single test
would have caught today's bug at PR time.

### Phase 02 — `web-smoke` parity on macOS and Windows

Two tasks:

(a) Drop the `if: runner.os == 'Linux'` gate on
`tools/web-smoke.sh` for the macOS matrix cell. Bash exists on
the macOS runner, the script is plain Bash (no Linux-isms
beyond the SPICE target it speaks to, which is just
`ryll --web` itself). Verify the gate is the only blocker.

(b) Either port `tools/web-smoke.sh` to PowerShell, or
rewrite both as a small Rust integration binary that the
build matrix invokes after `cargo build --release`. The Rust
rewrite is more work but eliminates a class of "Bash on
Windows is a quagmire" headaches forever — and lets the
smoke test reuse types from the workspace.

Recommendation: do (a) first as a one-line CI change, then
plan (b) as its own follow-up phase if Windows coverage of
`--web` becomes important enough to justify the rewrite.

This catches:
- `--web`-mode regressions on macOS specifically (rustls,
  TLS cert loading, axum behaviour).
- `--web`-mode regressions on Windows (after (b)).

Cost: extra ~30–60 s on the macOS cell (web-smoke includes a
brief `ryll --web` startup + handshake exchange). On Windows
(after rewrite) similar.

Bug-class coverage per minute: medium-high — duplicates some
Phase 01 coverage but exercises the actual `--web` HTTP
endpoint path that Phase 01 doesn't.

### Phase 03 — Smoke-test portability cleanup

Address the structural issue that Phases 01 and 02 hint at:
runtime tests written in Bash exclude Windows. Audit
`tools/*.sh` and identify which are CI-relevant. For each,
choose:

- Keep as Bash (Linux-only operational scripts —
  `propose-release.sh`, `address-comments-with-claude.sh`).
  These don't run in CI per se; no action needed.
- Port to a small Rust tool in a `tools/` workspace member —
  for tests that need to run on every platform.
- Rewrite as a `cargo` test under the relevant crate — for
  tests that exercise crate behaviour and can use the test
  harness.

The goal is that every CI-relevant smoke test runs on every
matrix cell. Linux-only operational scripts are fine to leave
as Bash — they're not blocking the matrix.

This catches:
- Future smoke-test additions don't accidentally exclude a
  platform.
- Shell-portability bugs in test infrastructure don't masquerade
  as product bugs.

Cost: one-time refactor effort. No ongoing CI minute cost
delta.

Bug-class coverage per minute: zero direct (it's plumbing).
Indirect: enables higher-coverage phases.

### Phase 04 — Manual-QA checklist for un-automatable platform behaviour

Acknowledge the bug classes automation cannot reach and write
them down so a human releaser knows what to spot-check. Adds
a `docs/release-qa.md` file with a checklist organised by
platform:

- **macOS**: open binary in Finder (Gatekeeper UX), idle a
  SPICE session for >30 minutes with ryll backgrounded
  (App Nap behaviour, Phase 02 of session-001-feedback once
  it lands), confirm clipboard sync survives.
- **Windows**: clipboard, USB redirection on 32-bit USB
  drivers, multi-monitor under DPI scaling.
- **Linux**: Wayland vs. Xorg, libvirt-managed vs. raw
  QEMU, AppImage / Flatpak packaging if those land.

The checklist is run before tagging a release, by a human,
on each platform. Output is a checked-off form attached to
the release PR.

This catches:
- The bug classes that need eyes on a real device, full stop.
- Regressions in UX behaviour that pass automation but
  surprise users.

Cost: not a CI minute cost — a release-time human cost.

Bug-class coverage per minute: not applicable. The point is
to be honest that some bugs require this and to make the
boundary explicit.

### Phase 05 — Push audit

Work `PUSH-AUDIT.md` over the accumulated diff of Phases 01-04
against `develop`, rather than the last phase's diff alone —
the workflow edits, the portability cleanup and the new smoke
tests only make sense read together. Findings land as their
own PR against `develop`, recorded here under an *Items
deferred from the push audit* heading — the shape
`PLAN-web-frontend.md` uses for its *Items deferred from the
post-Phase-N pre-push audit* sections, minus the phase number,
because this phase audits the whole plan rather than a range
of it. This plan is not complete until each is fixed or
declined in writing. If the audit finds nothing, record that
here in one sentence.

Phases 01-04 have merged by the time this phase runs, so
`develop...HEAD` is empty on the audit branch and the
accumulated diff has to be assembled from the phases' merge
commits. Record each phase's merge commit in the `Merged`
column of the *Phase order* table as that phase lands; *Two
ways this runbook is invoked* in `PUSH-AUDIT.md` has the rest.

This catches:
- Cross-phase duplication and doc drift that per-phase review
  cannot see, because it only exists once several phases have
  landed.

Cost: sub-agent time at the end of the plan. No CI minutes.

Bug-class coverage per minute: not applicable. This is a
review gate, not a test.

## Phase order

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Cross-platform TLS handshake smoke | PLAN-ci-platform-matrix-phase-01-tls-smoke.md | Not started | — |
| 2. `web-smoke` on macOS (and Windows after Phase 03) | PLAN-ci-platform-matrix-phase-02-web-smoke-parity.md | Not started | — |
| 3. Smoke-test portability audit | PLAN-ci-platform-matrix-phase-03-smoke-portability.md | Not started | — |
| 4. Release QA checklist doc | PLAN-ci-platform-matrix-phase-04-release-qa.md | Not started | — |
| 5. Push audit | PLAN-ci-platform-matrix-phase-05-push-audit.md | Not started | — |

Hard dependencies: Phase 02b (Windows web-smoke) is
gated on Phase 03 if we choose the Rust-rewrite route.
Phase 02a (macOS web-smoke) has no dependencies.

## Open questions

1. **CI cost ceiling.** What is the project's monthly minutes
   budget on GitHub Actions? Phases 01 and 02a together
   probably add <60 s per matrix cell — irrelevant. If
   coverage grew larger, this would matter. Document the
   current usage in Phase 01's plan.

2. **Should clippy run on macOS / Windows too?** Clippy is
   platform-agnostic in 99% of cases; a macOS run of clippy
   would catch the 1% of cfg-gated lints. Cost: ~2 minutes on
   the macOS cell. Probably yes, but not in the first pass —
   roll into Phase 03 if it falls out cleanly.

3. **Self-hosted runners as an escape valve.** The repo
   already uses self-hosted Linux runners
   (`runs-on: [self-hosted, static]`) for the Claude bot
   workflows. A self-hosted Mac mini or Windows VM would let
   us run heavier integration tests without GitHub minute
   pricing. Out of scope here; raise as a separate
   infrastructure plan if cost ever becomes the constraint.

4. **Coverage reporting.** Phase 01 adds tests; do we want
   `cargo-llvm-cov` reporting per-platform coverage too? The
   answer is "probably yes eventually" but it has its own
   build complications (llvm-tools-preview availability) and
   should be a separate item, not bundled here.

5. **Renovate / supply-chain bot interactions.** Renovate PRs
   currently hit the same matrix; will the expanded matrix
   slow them down enough to matter? Empirically Renovate PRs
   block on no review path, so latency isn't the bottleneck.
   No action.

## Out of scope

- Adding QEMU-based integration tests on macOS / Windows — no
  KVM equivalent makes the existing `test-qemu*` recipes
  unportable. If a small synthetic SPICE server emerges (a
  Rust crate that speaks server-side SPICE just enough to
  unit-test client behaviour), revisit.
- Browser-driven automation of `--web` mode (Playwright /
  Selenium against the in-process axum server). Useful but
  its own master plan; would dominate the cost of the rest
  of this plan combined.
- Cross-compilation matrices (e.g. building macOS binaries
  on Linux). The current native-build matrix is the source
  of truth and matches user expectations; cross-compilation
  introduces a class of "works on Linux-built macOS binary,
  not on Mac-built one" bugs that would themselves be a
  testing problem.
- Code signing / notarisation automation on macOS / Windows.
  Important for releases but tangential to bug-coverage CI;
  belongs with the packaging work.
- Performance regression CI (benchmarks, frame-rate
  regression). Different motivation, different
  infrastructure, different plan.
