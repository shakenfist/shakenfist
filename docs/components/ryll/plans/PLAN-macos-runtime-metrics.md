# macOS runtime metrics for bug reports

## Prompt

Implement per-thread and process-level runtime metrics on macOS
so bug reports filed from Mac clients carry the same diagnostic
data as Linux clients. Today,
`shakenfist-spice-renderer/src/metrics.rs` returns
`RuntimeMetrics::Unavailable { reason: "per-thread metrics not
implemented on macos" }` on every non-Linux platform; every
report from session-001 (a macOS dogfooding session) lacks any
CPU or memory data as a result.

This master plan was spun out of `PLAN-session-001-feedback.md`
item **G1**. It is independent of the session-001 feedback work
and the video-not-keeping-up work and can land in any order.

When working through phases, follow the project's plan
conventions (per-phase plan files named
`PLAN-macos-runtime-metrics-phase-NN-*.md`, one logical change
per commit, master-plan table updated as work lands).

## Situation

Current state (`shakenfist-spice-renderer/src/metrics.rs`):

- The public `sample(window: Duration) -> RuntimeMetrics` entry
  point at line 349 dispatches via `#[cfg(target_os = "linux")]`
  and returns `Unavailable` for everything else.
- `RuntimeMetrics` is `#[serde(untagged)]` with two variants:
  `Linux { sample_window_ms, process, threads, platform }` and
  `Unavailable { platform, available, reason }`. Adding a
  third `MacOS { … }` variant with the same shape as `Linux`
  is a non-breaking JSON change.
- `ProcessMetrics` reports `cpu_percent`, `rss_kb`, `vm_size_kb`,
  `uptime_secs`. `ThreadMetrics` reports `tid`, `name`,
  `cpu_percent`. The same fields apply on macOS.
- `libc = "0.2"` is already a dependency on
  `shakenfist-spice-renderer`. On Apple targets it exposes
  the Mach APIs we need (`task_info`, `task_threads`,
  `thread_info`, `mach_task_self`, `mach_port_deallocate`,
  `vm_deallocate`, `pthread_from_mach_thread_np`,
  `pthread_getname_np`). No new crate is required.

API mapping for macOS:

- **Process CPU + memory:** `task_info(mach_task_self(),
  MACH_TASK_BASIC_INFO, …)` returns `resident_size` and
  `virtual_size` directly — no `/proc/self/status` analogue.
  Total CPU time can come from the same call (`user_time` +
  `system_time` in `mach_task_basic_info`) or by summing
  per-thread `THREAD_BASIC_INFO`.
- **Per-thread CPU:** `task_threads(mach_task_self(),
  &thread_list, &thread_count)` enumerates Mach thread ports;
  `thread_info(thread, THREAD_BASIC_INFO, …)` returns
  `user_time` + `system_time` as `time_value_t`
  (seconds + microseconds of real wall-clock CPU — no
  `clk_tck` conversion).
- **Thread names:** `pthread_from_mach_thread_np(mach_thread)`
  yields a `pthread_t` for the same thread; pass it to
  `pthread_getname_np(pthread, buf, len)`. Tokio names its
  worker threads, so this gets us useful labels without
  building our own registry.
- **Uptime:** record `Instant::now()` once at process start in a
  `LazyLock` (or `OnceLock`) inside this module and report
  elapsed seconds.

## Mission and problem statement

`RuntimeMetrics::sample(window)` on macOS should return a
populated `MacOS { … }` variant with process CPU%, RSS, VmSize,
uptime, and a sorted list of `ThreadMetrics` covering every
thread alive across the sample window — matching the diagnostic
value of the Linux implementation. Reports from Mac clients
should stop saying "per-thread metrics not implemented".

## Approach

Mirror the Linux module's structure: factor snapshot acquisition
out so the delta math is unit-testable, even though the Mach
calls themselves are only exercisable on a Mac. Use a small RAII
wrapper for the Mach thread-port array so the `vm_deallocate` +
per-port `mach_port_deallocate` cleanup happens on every exit
path, including panic.

Land the work in three phases so each commit is self-contained
and reviewable:

1. **Process-level metrics** first (single `task_info` call, no
   thread enumeration, no port lifecycle). Smallest unsafe
   surface, highest immediate value — even without per-thread
   data this closes most of the diagnostic gap.
2. **Per-thread enumeration + naming** second, behind the same
   `MacOS` variant. This is where the Mach port lifecycle and
   the `pthread_from_mach_thread_np` lookup live.
3. **Integration / soak** third — wire the `MacOS` variant into
   the existing untagged enum, ensure the bug-report path
   produces it on Mac, and run a long soak to catch any port
   leak (verify with `Activity Monitor` or `lsof`-equivalent
   port count).

## Open questions

1. **iOS scope.** This plan targets `target_os = "macos"` only.
   iOS sandbox restrictions on Mach APIs are out of scope; if we
   ever ship an iOS client, it gets its own plan. Confirm before
   phase 1 we're happy with that scoping. Lean yes.

2. **Total process CPU source.** Either read it once via
   `task_info(MACH_TASK_BASIC_INFO)` or compute it as the sum of
   per-thread CPU. The former is one syscall and matches what
   Linux does (process-level read, not summed); the latter
   guarantees `process.cpu_percent == sum(threads.cpu_percent)`
   exactly. Resolve before phase 1. Lean read-once for
   simplicity and parity with Linux.

3. **What to do with threads that disappear mid-window.** Linux
   tolerates this (`metrics.rs:309`) by attributing the
   second-snapshot value to itself, yielding a 0 delta. macOS
   should do the same. This question is really just a checklist
   item: confirm the same policy when phase 2 lands.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Process-level metrics on macOS | PLAN-macos-runtime-metrics-phase-01-process.md | Not started |
| 2. Per-thread enumeration + naming | PLAN-macos-runtime-metrics-phase-02-threads.md | Not started |
| 3. Integration into bug-report path + soak | PLAN-macos-runtime-metrics-phase-03-integration.md | Not started |

Phases must run in order — phase 2 builds on the module
structure phase 1 introduces; phase 3 requires both. Each phase
should add or extend tests for any code that can be tested off
a Mac (delta math, JSON shape) and document any code that can
only be exercised on a Mac.

Out of scope: iOS, Windows, FreeBSD; per-thread memory metrics
(neither Linux nor macOS expose these cheaply); GPU metrics.
