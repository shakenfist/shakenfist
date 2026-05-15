# Phase 1: Process-level metrics on macOS

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
referenced source files, understand existing patterns (the
`RuntimeMetrics` enum, the Linux `mod linux` block in
`shakenfist-spice-renderer/src/metrics.rs`, the
`Snapshot`/`take_snapshot()` factor-out pattern), and ground
your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.

## Goal

Replace the macOS `RuntimeMetrics::Unavailable { reason:
"per-thread metrics not implemented on macos" }` with a
populated `RuntimeMetrics::MacOS { … }` variant carrying real
process-level CPU%, RSS, VM-size, and uptime over a sampled
window. Per-thread enumeration is deferred to phase 2; phase 1
returns an empty `threads: Vec::new()` so the JSON shape is
already final.

The phase is the smallest unsafe surface that delivers a
diagnostic value: a single `task_info(mach_task_self(),
MACH_TASK_BASIC_INFO, …)` syscall per snapshot, no Mach thread
ports, no `vm_deallocate` lifecycle. If phase 1 lands and
exposes any Mach API quirk we haven't anticipated, phase 2's
thread work can be paused without leaving the diagnostic gap
open.

Out of scope:
- Per-thread enumeration via `task_threads` / `thread_info` —
  phase 2.
- Mach-port lifecycle (`mach_port_deallocate`, `vm_deallocate`)
  — phase 2 (phase 1 keeps no Mach ports).
- `pthread_from_mach_thread_np` / `pthread_getname_np` thread
  naming — phase 2.
- Integration soak / port-leak verification — phase 3.
- iOS / FreeBSD / Windows — out of master-plan scope.

## Design

### `RuntimeMetrics::MacOS` variant

In `shakenfist-spice-renderer/src/metrics.rs`, extend the
`RuntimeMetrics` enum (currently lines 52–68) with a third
variant matching the `Linux` shape exactly:

```rust
#[derive(Debug, Clone, Serialize)]
#[serde(untagged)]
pub enum RuntimeMetrics {
    Linux {
        sample_window_ms: u64,
        process: ProcessMetrics,
        threads: Vec<ThreadMetrics>,
        platform: String,
    },
    MacOS {
        sample_window_ms: u64,
        process: ProcessMetrics,
        threads: Vec<ThreadMetrics>,
        platform: String,
    },
    Unavailable {
        platform: String,
        available: bool,
        reason: String,
    },
}
```

`#[serde(untagged)]` means the JSON has no `"type"` /
`"variant"` discriminator — readers tell the variants apart
by field presence. `MacOS` and `Linux` have identical fields,
so a JSON consumer that already handles `Linux` will accept
`MacOS` unchanged. The `platform` field (already on both
variants, populated from `std::env::consts::OS`) tells a
maintainer which platform produced the report.

Phase 1 leaves `threads` populated as `Vec::new()`. The
`ThreadMetrics` type itself doesn't change.

### Module layout: `mod macos`

Mirror the existing `#[cfg(target_os = "linux")] mod linux { … }`
block. New `#[cfg(target_os = "macos")] mod macos { … }`
contains:

- `Snapshot` struct (private to the module) holding the raw
  `task_basic_info` data: `user_time_us`, `system_time_us`,
  `resident_size`, `virtual_size`.
- `take_snapshot() -> Result<Snapshot, &'static str>` doing
  one `task_info()` call. Returns `Err` if the syscall fails
  (very rare on a healthy Mach kernel; surfaced into the
  fallback path).
- `process_start_uptime_secs() -> f64` reading a static
  `LazyLock<Instant>` initialised to `Instant::now()`.
- `pub fn sample(window: Duration) -> RuntimeMetrics` wired
  into the `#[cfg(target_os = "macos")]` arm of
  `metrics::sample()`.

The dispatch in `metrics::sample()` (currently lines 349–362)
gains a macOS arm:

```rust
pub fn sample(window: Duration) -> RuntimeMetrics {
    #[cfg(target_os = "linux")]
    { linux::sample(window) }
    #[cfg(target_os = "macos")]
    { macos::sample(window) }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    { RuntimeMetrics::unavailable(
        "per-thread metrics not implemented on this platform",
    ) }
}
```

### `Snapshot` and the delta math

```rust
#[derive(Debug, Clone)]
struct Snapshot {
    /// Total user CPU time across all threads, microseconds.
    user_time_us: u64,
    /// Total system CPU time across all threads, microseconds.
    system_time_us: u64,
    /// Resident set size in bytes (from `task_basic_info.resident_size`).
    resident_size: u64,
    /// Virtual memory size in bytes (from `task_basic_info.virtual_size`).
    virtual_size: u64,
}
```

`time_value_t` stores CPU time as `seconds: integer_t,
microseconds: integer_t`. The conversion to a single `u64`
microsecond count is:

```rust
fn time_value_to_us(t: libc::time_value_t) -> u64 {
    (t.seconds as u64).saturating_mul(1_000_000)
        .saturating_add(t.microseconds as u64)
}
```

`saturating_*` because session uptime in microseconds fits in
`u64` for ~584 thousand years, but the saturating form removes
any panic surface from arithmetic on attacker-controlled
values (here: kernel-controlled, but defensive cost is zero).

Delta math is testable off a Mac:

```rust
fn process_cpu_percent(a: &Snapshot, b: &Snapshot,
                       window: Duration) -> f64 {
    let user_delta = b.user_time_us.saturating_sub(a.user_time_us);
    let sys_delta  = b.system_time_us.saturating_sub(a.system_time_us);
    let total_us   = user_delta.saturating_add(sys_delta);
    let window_us  = window.as_micros().max(1) as u64;
    (total_us as f64 / window_us as f64) * 100.0
}
```

`window.as_micros().max(1)` guards against the
zero-duration edge case so the percent is finite (returns 0
when both deltas are zero, never NaN).

### The unsafe block

One `unsafe { … }` per `take_snapshot()` call. The pattern
mirrors the existing `clk_tck()` precedent in metrics.rs:

```rust
fn take_snapshot() -> Result<Snapshot, &'static str> {
    let mut info: libc::mach_task_basic_info_data_t =
        unsafe { std::mem::zeroed() };
    let mut count: libc::mach_msg_type_number_t =
        (std::mem::size_of::<libc::mach_task_basic_info_data_t>()
         / std::mem::size_of::<libc::natural_t>()) as _;
    // SAFETY: task_info has no preconditions beyond a live
    // task port and a correctly-sized output buffer. We pass
    // mach_task_self() (the current process's port, which
    // cannot fail) and a stack-local `info` of exactly the
    // shape declared by MACH_TASK_BASIC_INFO. `count` is
    // computed from the same struct and is in/out by pointer.
    // The call does not retain any pointer past return.
    let kr = unsafe {
        libc::task_info(
            libc::mach_task_self(),
            libc::MACH_TASK_BASIC_INFO,
            &mut info as *mut _ as *mut libc::integer_t,
            &mut count,
        )
    };
    if kr != libc::KERN_SUCCESS {
        return Err("task_info(MACH_TASK_BASIC_INFO) failed");
    }
    Ok(Snapshot {
        user_time_us:   time_value_to_us(info.user_time),
        system_time_us: time_value_to_us(info.system_time),
        resident_size:  info.resident_size,
        virtual_size:   info.virtual_size,
    })
}
```

`mach_task_self()` returns the current task's port, which
ryll already owns; the port is process-lifetime, no
`mach_port_deallocate` needed. This is **why phase 1 has no
Mach-port lifecycle work** — that complexity lives entirely
on the `task_threads` path in phase 2.

### Uptime

Per the master plan's "Approach" section:

```rust
static PROCESS_START: LazyLock<Instant> =
    LazyLock::new(Instant::now);

fn process_uptime_secs() -> f64 {
    PROCESS_START.elapsed().as_secs_f64()
}
```

`LazyLock` initialises on first read, **not** at process
start. So this strictly measures "time since the first
`sample()` call". For diagnostic purposes the difference is
"the few seconds between `main()` and the first bug-report
trigger", which is negligible. The phase plan documents this
explicitly; phase 3 may consider promoting `PROCESS_START` to
a global initialised from `main.rs` if real bug reports show
the gap matters.

### `sample(window)` body

```rust
pub fn sample(window: Duration) -> RuntimeMetrics {
    let snap_a = match take_snapshot() {
        Ok(s) => s,
        Err(reason) => return RuntimeMetrics::unavailable(reason),
    };
    std::thread::sleep(window);
    let snap_b = match take_snapshot() {
        Ok(s) => s,
        Err(reason) => return RuntimeMetrics::unavailable(reason),
    };
    let cpu_percent = process_cpu_percent(&snap_a, &snap_b, window);
    RuntimeMetrics::MacOS {
        sample_window_ms: window.as_millis() as u64,
        process: ProcessMetrics {
            cpu_percent,
            rss_kb: snap_b.resident_size / 1024,
            vm_size_kb: snap_b.virtual_size / 1024,
            uptime_secs: process_uptime_secs(),
        },
        threads: Vec::new(),
        platform: std::env::consts::OS.to_string(),
    }
}
```

`sleep(window)` matches the Linux implementation
(`metrics.rs:262`). The call is blocking — `BugReport::new()`
already calls `metrics::sample(Duration::from_secs(2))` on
the same path, so the blocking semantics are unchanged.

If either snapshot fails (`Err`), the function returns
`RuntimeMetrics::Unavailable { reason: "task_info(MACH_TASK_BASIC_INFO) failed" }`
— graceful fallback that preserves the existing contract that
`sample()` never panics.

## Steps

### Step 1: Add the `MacOS` variant to `RuntimeMetrics`

1. In `shakenfist-spice-renderer/src/metrics.rs`, extend the
   enum at line 52–68 with a `MacOS` variant identical in
   shape to `Linux`.
2. Update the doc comment above the enum (around lines 43–51)
   to mention the new variant and the shared JSON shape.
3. The existing `RuntimeMetrics::unavailable()` constructor
   stays unchanged — it's still the fallback for unsupported
   platforms and per-snapshot Mach failures.

### Step 2: Add `mod macos` with the snapshot/sample machinery

1. Add `#[cfg(target_os = "macos")] mod macos { … }` next to
   the existing `mod linux`.
2. Define `Snapshot` struct, `time_value_to_us`,
   `take_snapshot`, `process_cpu_percent`,
   `process_uptime_secs`, and `pub fn sample(window: Duration)`.
3. The single `unsafe { … }` wrapping `task_info()` has a
   SAFETY comment in the same format as the existing
   `clk_tck()` block (`metrics.rs:184–185`).

### Step 3: Wire `macos::sample` into the public `sample()`

1. Extend `pub fn sample(window: Duration) -> RuntimeMetrics`
   (currently lines 349–362) with a
   `#[cfg(target_os = "macos")]` arm calling
   `macos::sample(window)`.
2. The `#[cfg(not(target_os = "linux"))]` fallback narrows to
   `#[cfg(not(any(target_os = "linux", target_os = "macos")))]`.
3. The existing `RuntimeMetrics::unavailable()` call site for
   non-Linux platforms moves into the new
   `#[cfg(not(any(…)))]` branch.

### Step 4: Tests

1. **`time_value_to_us_zero_and_max`** — platform-independent
   unit test: convert `{seconds: 0, microseconds: 0}` → 0;
   `{seconds: 1, microseconds: 500_000}` → 1_500_000; large
   values saturate.
2. **`process_cpu_percent_computes_delta`** — synthesise two
   `Snapshot` instances 100 ms apart, compute percent.
   Includes a zero-window guard test
   (`Duration::from_millis(0)` returns finite 0, not NaN).
3. **`process_cpu_percent_handles_clock_reset`** — second
   snapshot's `*_time_us` < first's (rare but observable
   across thread accounting under load). Saturating subtract
   yields a 0 delta, percent = 0.
4. **`macos_variant_serialises`** — platform-independent JSON
   shape test mirroring `test_linux_variant_serialises`
   (`metrics.rs:441`): construct a `RuntimeMetrics::MacOS { … }`
   and assert the JSON fields match the documented shape and
   that no `type`/`variant` discriminator leaks
   (`#[serde(untagged)]` invariant).
5. **`macos_sample_returns_populated_variant`** —
   `#[cfg(target_os = "macos")]`-gated smoke test that calls
   `macos::sample(Duration::from_millis(100))` and asserts a
   `MacOS` variant comes back with positive `rss_kb` and
   non-negative `cpu_percent`. Does not run on the Linux
   devcontainer; runs on the macOS CI matrix once that lands
   (or under `cargo test` invoked by a Mac developer).
6. **`test_bug_report_runtime_metrics_in_zip`** in
   `ryll/src/bugreport.rs:2848` already exercises the JSON
   shape under `unavailable` semantics; phase 1 doesn't break
   it (the test runs on Linux and gets the Linux variant).
   No change required.

### Step 5: Documentation

1. Update the existing `metrics.rs` module-level doc comment
   to mention the macOS implementation and note the
   `LazyLock` "uptime from first sample" caveat.
2. Update `ARCHITECTURE.md` if it has a runtime-metrics
   section (verify during the step) to mention the new
   `MacOS` variant.
3. The master plan's execution table marks phase 1 `Done`
   when this lands.
4. No `docs/troubleshooting.md` change in phase 1 — the
   "Bug Reports" section there already covers
   `runtime-metrics.json` generically; reader doesn't need to
   know the per-platform implementation.

### Step 6: Build, test, lint, pre-commit gates

`make build`, `make test`, `make lint`, and
`pre-commit run --all-files` all pass. Note: the devcontainer
is Linux-only, so `make build` exercises the
`#[cfg(target_os = "macos")]` block only via
`cargo check --target aarch64-apple-darwin` if that target is
installed. Otherwise the macOS code path is type-checked but
not compiled in the standard `make build`. Acceptable: phase
1 lands on Linux CI; a Mac developer or the macOS CI matrix
phase confirms the binary runs.

## Administration and logistics

### Success criteria

- `RuntimeMetrics` exposes a `MacOS` variant matching the
  `Linux` shape exactly.
- A debug build on macOS calling `metrics::sample(window)`
  returns `RuntimeMetrics::MacOS { … }` with populated
  process fields.
- A debug build on Linux is unaffected (still returns
  `RuntimeMetrics::Linux`).
- Platform-independent unit tests for delta math and JSON
  serialisation pass on every CI matrix entry.
- `make build`, `make test`, `make lint`,
  `pre-commit run --all-files` all pass.
- A bug report assembled on a Mac stops carrying
  `reason: "per-thread metrics not implemented on macos"` in
  `runtime-metrics.json`.

### Risks

- **Cannot exercise the `unsafe` block in the devcontainer.**
  The Mach call only compiles and runs on macOS. The
  workspace lint runs on Linux only today, so phase 1 relies
  on the macOS CI matrix from `PLAN-ci-platform-matrix.md`
  (or a manual Mac compile) to catch any FFI declaration
  mismatch. Mitigation: keep the unsafe block tiny, document
  every field access against Apple's `task_info` man page,
  and exercise the shape via the platform-independent
  `time_value_to_us` and `process_cpu_percent` helpers in
  unit tests.
- **`LazyLock` "uptime from first sample" caveat.** As noted
  in the Design section, `PROCESS_START` initialises on first
  read. The first bug report's `uptime_secs` will be ~0; the
  difference matters only for reports filed within the first
  few seconds. Phase 3 may revisit by promoting
  `PROCESS_START` to a `static` initialised from `main.rs`.
  Not blocking for phase 1.
- **`task_info` failures.** Extremely rare on a healthy Mac
  (would imply the current process's Mach port is invalid).
  Fallback returns `RuntimeMetrics::Unavailable { reason:
  "task_info(MACH_TASK_BASIC_INFO) failed" }`, preserving the
  no-panic contract.
- **`mach_task_basic_info_data_t` field layout mismatch
  between libc versions.** `libc = "0.2"` has been stable on
  this struct for years (the kernel ABI is fixed). If a
  future libc version reshapes the struct, the compiler
  catches it; no silent breakage.
- **`Vec::new()` for threads vs. expected non-empty.** Phase
  1 returns no threads. Any consumer that expects at least
  one thread entry (e.g. a future bug-report viewer) needs to
  tolerate the empty list. The Linux variant can also produce
  an empty `threads` if `/proc/self/task` enumeration fails,
  so this is not a new contract.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
