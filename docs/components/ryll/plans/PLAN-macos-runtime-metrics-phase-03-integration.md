# Phase 3: macOS metrics integration verification and soak

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
referenced source files, understand existing patterns (the
phase-1 `LazyLock<Instant> PROCESS_START` in `mod macos`, the
bug-report assembly path that calls
`metrics::sample(Duration::from_secs(2))` at
`ryll/src/bugreport.rs:1215`, and the existing
`test_bug_report_runtime_metrics_in_zip` test), and ground
your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.

## Goal

Close out the macOS runtime-metrics master plan: confirm the
phase-1 and phase-2 implementation produces correct, complete,
and leak-free metrics on a real Mac, and tighten the one
caveat phase 1 explicitly deferred (the LazyLock-uptime
"time-since-first-sample" gap).

Phase 3 deliverables:

1. **`metrics::init_at_startup()`** that forces
   `PROCESS_START` on macOS so `uptime_secs` measures from
   `main()` entry rather than the first `sample()` call. The
   caveat documented in phase 1's module-level doc-comment
   goes away.
2. **A verification runbook** for a Mac user (or the future
   macOS CI matrix from `PLAN-ci-platform-matrix.md`) that
   walks through each of the master plan's five acceptance
   criteria with explicit pass/fail conditions.
3. **A Mach port-leak soak procedure** documenting how to
   measure the process's Mach port count, what behaviour to
   expect, and the pass criterion.

Phase 3 is **small** — most of the implementation work
happened in phases 1 and 2. The bulk of phase 3 is
verification documentation. The code change is ~5 lines plus
one test.

Out of scope:
- Automated port-leak detection in unit tests. Observing port-
  table state from inside the same process needs
  `mach_port_kobject` or similar deep introspection that is
  fragile across macOS versions; the empirical soak is the
  pragmatic check.
- Activity-Monitor cross-check tooling. The verification
  runbook references `vmmap`, `lsof`, and Activity Monitor as
  external tools; ryll itself doesn't need to wrap them.
- Additional macOS-gated integration tests. Phase 2's
  `test_macos_sample_returns_populated_variant` already
  exercises the full `sample()` path; the existing
  `test_bug_report_runtime_metrics_in_zip` covers the
  JSON→zip leg with an injected stub. Together they cover
  the integration surface without a fragile end-to-end test
  that would only run on a Mac anyway.

## Design

### `metrics::init_at_startup()`

Phase 1 documented:

> `PROCESS_START` is a `LazyLock<Instant>` initialised on the
> first call to `sample()`. This measures "time since first
> sample" rather than true process start; the gap is "the
> few seconds between `main()` and the first bug-report
> trigger" and is acceptable for diagnostic purposes.

Phase 3 closes the gap with a tiny public function:

```rust
// In shakenfist-spice-renderer/src/metrics.rs (module
// scope, alongside the existing `pub fn sample`).

/// Initialise platform-specific runtime metrics state at
/// process start.
///
/// On macOS this forces the `PROCESS_START` LazyLock so
/// subsequent `uptime_secs` values measure from `main()`
/// entry rather than the first `sample()` call.
///
/// On other platforms this is a no-op.
///
/// Idempotent and cheap; safe to call more than once.
pub fn init_at_startup() {
    #[cfg(target_os = "macos")]
    {
        macos::force_process_start();
    }
}
```

Inside `mod macos`:

```rust
pub(super) fn force_process_start() {
    // Dereferencing the LazyLock initialises it. The result
    // is discarded; the side effect is what we want.
    let _ = *PROCESS_START;
}
```

In `ryll/src/main.rs`, call once at the top of `main()`:

```rust
fn main() -> Result<()> {
    // Initialise platform-specific runtime-metrics state
    // (macOS PROCESS_START). Must run before the tokio
    // runtime so the uptime baseline is `main()` entry, not
    // the first `metrics::sample()` call from the bug-report
    // path.
    shakenfist_spice_renderer::metrics::init_at_startup();

    // ... existing main() body ...
}
```

The function is unconditionally public and unconditionally
callable. The cfg gate is inside the function body so call
sites don't need their own gates.

After this lands, the module-level doc-comment's uptime
caveat can be relaxed: `uptime_secs` reflects time since
`main()` entry, modulo the few microseconds before the call.
Bug reports filed seconds after startup will show plausible
uptime values.

### Verification runbook

A new file `docs/macos-metrics-verification.md` (not folded
into `docs/troubleshooting.md` because the procedure is
self-contained and addressed at maintainers, not users
debugging a session). Contents:

1. **Prerequisites** — Mac with a debug or release ryll
   build; a SPICE server to connect to (real QEMU or the
   project's `tools/web-smoke.sh`-style synthetic source);
   `jq` installed for JSON inspection.
2. **Test 1: MacOS variant is produced.** Run ryll, trigger
   F12 bug report, unzip, and verify `runtime-metrics.json`
   parses with the expected `MacOS` shape:
   ```sh
   unzip -p ryll-bugreport-*.zip runtime-metrics.json | \
       jq '.platform, .threads | length'
   ```
   Pass: `"macos"` and a positive integer.
3. **Test 2: `Unavailable` reason is gone.** Same zip:
   ```sh
   unzip -p ryll-bugreport-*.zip runtime-metrics.json | \
       grep -i "per-thread metrics not implemented"
   ```
   Pass: no match.
4. **Test 3: `process.cpu_percent` is plausible.** Compare
   to Activity Monitor's "% CPU" for the ryll process at the
   moment the bug report was filed. Pass: within 50%
   relative of Activity Monitor's reading (sampling skew is
   real).
5. **Test 4: `process.rss_kb` and `vm_size_kb` are
   plausible.** Compare to Activity Monitor's "Memory" and
   "Virtual Memory" columns. Pass: RSS within 50% relative;
   VmSize at least RSS, ideally much larger.
6. **Test 5: `process.uptime_secs` advances.** File two
   bug reports a few minutes apart in the same session,
   diff the `uptime_secs` values. Pass: difference matches
   real elapsed time within a few hundred ms.
7. **Test 6: `threads` is non-empty, sorted, plausibly
   named.** Same zip:
   ```sh
   unzip -p ryll-bugreport-*.zip runtime-metrics.json | \
       jq '.threads | map({tid, name})'
   ```
   Pass: at least 10 threads on a real session; at least one
   has `name == "tokio-runtime-worker"` (or similar tokio
   pattern); tids are ascending.

### Mach port-leak soak procedure

Same file, separate section:

1. Start ryll in pedantic mode against a real SPICE server.
   Pedantic mode fires bug-report assembly periodically, so
   `metrics::sample` runs every few seconds, exercising
   `task_threads` + the `MachThreadList` RAII guard.
2. Record the initial Mach port count for the ryll process:
   ```sh
   vmmap -summary $(pgrep ryll) | grep -A 2 "Mach Ports"
   ```
3. Wait at least one hour while ryll runs (more is better).
4. Record the Mach port count again with the same command.
5. **Pass criterion**: the second count is within 20% of the
   first. Some growth is expected because additional threads
   may have spawned during the session; a leak shows as
   monotonic growth scaling with the number of `sample()`
   calls. As a sanity check, count `sample()` calls
   (approximately one per pedantic-bug-report) and confirm
   the growth-per-sample-call is small (target: < 1 port per
   sample on average across the session).

If the pass criterion fails, the `MachThreadList::drop`
impl is the first suspect — either the per-port
`mach_port_deallocate` is not running (panic between
allocation and wrapper construction?) or `vm_deallocate` is
not running. The RAII wrapper's source is audited in phase 2
to be panic-safe between `task_threads` and the
`MachThreadList { … }` literal, but a real soak is the
empirical confirmation.

### Acceptance-criteria walkthrough

Phase 3 explicitly maps each acceptance criterion from the
master plan to a verification step in the runbook:

| Master-plan acceptance criterion | Runbook test |
|----------------------------------|--------------|
| Top-level JSON is `MacOS` variant, not `Unavailable` | Tests 1 & 2 |
| `process.cpu_percent` matches reality | Test 3 |
| `process.rss_kb` / `vm_size_kb` plausible | Test 4 |
| `uptime_secs` advances monotonically | Test 5 |
| `threads` populated + tid-sorted in phase 2 | Test 6 |

The port-leak soak is an additional check beyond the master
plan's acceptance criteria; it derives from the master plan's
phase-3 brief ("run a long soak to catch any port leak").

## Steps

### Step 1: Add `init_at_startup` to metrics.rs

1. Inside `mod macos`, add `pub(super) fn force_process_start()`
   that derefs `PROCESS_START`.
2. At module scope (after the existing `pub fn sample`), add
   `pub fn init_at_startup()` that calls
   `macos::force_process_start()` under `#[cfg(target_os = "macos")]`.
3. Update the module-level doc-comment: the LazyLock-uptime
   caveat is replaced with a note that `init_at_startup()`
   should be called from `main()` to baseline the uptime
   clock at process start.

### Step 2: Call `init_at_startup` from `ryll/src/main.rs`

1. At the very top of `main()` (before tokio runtime
   construction, before argument parsing if possible — early
   enough that `Instant::now()` reads the true process-start
   time), call
   `shakenfist_spice_renderer::metrics::init_at_startup();`.
2. The call is unconditional and unconditional in cost (no-op
   on Linux / Windows / unsupported platforms).

### Step 3: Test the init function

1. Add `test_init_at_startup_runs_without_panic` (platform-
   independent — no `#[cfg]`) that calls `init_at_startup()`
   and asserts nothing else. The test confirms the public
   function compiles and runs everywhere; the actual side
   effect on macOS is verified indirectly by the existing
   `test_macos_sample_returns_populated_variant` which now
   has the eager-init in place.

### Step 4: Write `docs/macos-metrics-verification.md`

1. Create the new docs file with the structure described
   above: prerequisites, six numbered verification tests
   keyed to the master plan's acceptance criteria, and the
   Mach port-leak soak procedure.
2. The file ends with a "What to do if a test fails" section
   pointing at the relevant phase plan / module for each
   failure mode.

### Step 5: Update existing docs

1. `docs/troubleshooting.md` — if the "Bug Reports" section
   mentions runtime metrics, add a one-liner pointing at the
   new `docs/macos-metrics-verification.md` for Mac
   verification.
2. `ARCHITECTURE.md` — the "Runtime metrics in bug reports"
   bullet (last touched in phase 2) is accurate; no change
   required. Confirm during step.
3. The master plan's execution table marks phase 3 `Done`.
4. The master plan's "Approach" section (or a new note)
   acknowledges that phase 1's LazyLock-uptime caveat is now
   closed by phase 3's `init_at_startup` call.

### Step 6: Build, test, lint, pre-commit gates

`make build`, `make test`, `make lint`, and
`pre-commit run --all-files` all pass. The platform-
independent `test_init_at_startup_runs_without_panic` runs
on the Linux devcontainer; the macOS-side effect requires a
Mac to verify but is exercised by the existing phase-2 smoke
test.

### Step 7: User-side verification

This step does not land in code or docs; it is a checklist
for the user (or the future macOS CI matrix) to execute on
real hardware:

- Run through `docs/macos-metrics-verification.md` tests 1–6
  on a Mac.
- Run the Mach port-leak soak for ≥ 1 hour.
- Report results back into the master plan (e.g. as a small
  "phase 3 acceptance" note appended to the master).

If any test fails, the fix lands as a phase-3 follow-up
patch. The expected outcome is "all green" since phases 1
and 2 were each individually unit-tested for the FFI shape
and the delta math.

## Administration and logistics

### Success criteria

- `metrics::init_at_startup()` exists, is unconditionally
  callable, and is invoked at the top of `main()` in
  `ryll/src/main.rs`.
- The module-level doc-comment in `metrics.rs` no longer
  carries the "time-since-first-sample" caveat.
- A new `docs/macos-metrics-verification.md` documents
  step-by-step verification for the master plan's five
  acceptance criteria plus the port-leak soak.
- `make build`, `make test`, `make lint`,
  `pre-commit run --all-files` all pass.
- The master plan's execution table marks phase 3 `Done`.
- (User-side) The verification runbook runs green on a real
  Mac.

### Risks

- **`init_at_startup` is in the wrong place.** If a previous
  `metrics::sample` call happens before main() — e.g. from a
  static initialiser or a test harness — `PROCESS_START` is
  already set and `init_at_startup` is a no-op. Audit during
  step 2: the only call sites for `sample` today are in
  `ryll/src/bugreport.rs` (constructor and pedantic path),
  both reached only after `main()` runs. No static
  initialiser path. Risk: a future commit adding a
  pre-main() sample call would silently break the baseline.
  Mitigation: the doc-comment on `init_at_startup` calls out
  the ordering requirement.
- **Soak depends on real hardware.** The phase 3 work cannot
  be fully validated in CI without the macOS CI matrix from
  `PLAN-ci-platform-matrix.md`. Until then, the user runs
  the soak manually. Documented; same constraint as phases
  1 and 2 for the FFI surface.
- **Activity Monitor's CPU% is also sampled.** Comparing
  ryll's `process.cpu_percent` to Activity Monitor's reading
  is subject to sampling skew on both sides. The runbook's
  "within 50% relative" pass criterion is generous on
  purpose; a tighter tolerance would create false negatives.
- **`vmmap -summary` output format may change.** Apple has
  reshaped vmmap output across macOS releases. If the
  runbook's grep pattern breaks, the user can fall back to
  Activity Monitor's "Inspect Process" → "Open Files and
  Ports" which shows the same number with a different
  display.
- **`init_at_startup()` is the wrong abstraction if other
  platforms grow eager-init needs.** Today the function is
  "macos-only" in body. If Linux or Windows ever need
  process-start state, the function generalises naturally.
  No design lock-in.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
