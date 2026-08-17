# Phase 5: Capture runtime metrics in bug reports

Parent plan: [PLAN-idle-cpu-and-latency.md](/components/ryll/plans/PLAN-idle-cpu-and-latency/)

## Motivation

When the user originally reported "ryll burns 6 cores at
idle", we had no choice but to spawn a profiling sub-agent,
launch ryll under `top -H` and `/proc/<pid>/task/*/stat`,
and reverse-engineer where the CPU was going.  All of that
information is trivially available *inside* ryll's own
process — we just don't capture it.

If the bug-report ZIP had included per-thread CPU and
thread names, the user's first bug report would have shown
`llvmpipe-{0..15}` each at ~36% of one core, and phase 1
of this plan would not have needed to exist.  This phase
makes future "ryll is slow / hot / leaking" reports
self-debugging.

## Goal

Capture process and per-thread runtime metrics into every
bug report.  Metrics include:

- Process-level: total CPU%, RSS, VmSize, uptime.
- Per-thread: thread name (from `/proc/self/task/*/comm`),
  CPU% over the sample window, total CPU time.

The numbers must reflect *current rate* (e.g. last 2
seconds), not lifetime averages, because lifetime numbers
get diluted by startup costs and hide steady-state
behaviour.

Linux-first.  macOS and Windows can log "metrics
unavailable on this platform" and omit the section
gracefully — better than no bug reports at all on
non-Linux.

## Background

Bug reports are assembled in
[ryll/src/bugreport.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs).  The
existing flow:

- `BugReport::new(...)` collects metadata, channel state,
  optional pcap traffic, and an optional screenshot
  ([bugreport.rs:608+](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L608)).
- `metadata.json` already contains ryll version, platform,
  and target host (see `chrono_now()` and friends at
  [bugreport.rs:540-579](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L540-L579)).
- The ZIP layout is documented in
  [README.md](https://github.com/shakenfist/ryll/blob/develop/README.md) (capture mode section).

The natural extension: add a `runtime_metrics` field to
the report struct, populated at report-creation time, and
either embed it in `metadata.json` or write it as a
separate `runtime-metrics.json` file in the ZIP.

## Approach

### Sampling

CPU% requires two reads of `/proc/self/stat` (and per-task
equivalents) separated by a sample window, with the delta
divided by elapsed wall time and `sysconf(_SC_CLK_TCK)`.
A 2-second sample is a good default — long enough to be
meaningful, short enough that the user doesn't notice the
button took longer than usual.

Pseudocode:

```
fn sample_metrics(window: Duration) -> RuntimeMetrics {
    let snapshot_a = read_proc_self_and_tasks();
    sleep(window);
    let snapshot_b = read_proc_self_and_tasks();
    compute_deltas(snapshot_a, snapshot_b, window)
}
```

The 2-second sleep blocks the report-creation path.  Two
options:

a) Block on the report-creation thread (simplest, user
   sees a brief "collecting metrics..." message).

b) Sample continuously in the background (a small task
   that polls every N seconds, ring-buffers the result,
   and reports the most recent sample).

**Recommendation: option (a) for v1.**  Bug reports are
already a deliberate, non-interactive operation — F12
opens a dialog, the user types a description, then clicks
Save.  An extra 2 seconds is invisible.  Continuous
sampling adds a thread that runs forever and contradicts
the spirit of this plan's CPU-reduction goal.

### Per-thread data

`/proc/self/task/<tid>/comm` gives the thread name (set
by `pthread_setname_np` or `prctl(PR_SET_NAME)` — egui,
tokio, cpal, and Mesa all set sensible names).  Tokio
worker threads will be named like `tokio-runtime-worker`;
the egui main thread inherits the binary name; cpal sets
its own; Mesa's llvmpipe threads are `llvmpipe-N`.

`/proc/self/task/<tid>/stat` gives utime + stime in
clock ticks, same format as `/proc/<pid>/stat`.

### Cross-platform

For v1, gate the implementation behind
`#[cfg(target_os = "linux")]`.  Non-Linux platforms emit
a `RuntimeMetrics::Unavailable { reason: "..." }` variant.
macOS and Windows support is reasonable to add later via
`mach_task_info` and `GetProcessTimes` respectively, but
the dominant ryll user base today is Linux.

### Output format

A new file in the ZIP: `runtime-metrics.json`.  Schema:

```json
{
  "sample_window_ms": 2000,
  "process": {
    "cpu_percent": 624.3,
    "rss_kb": 184320,
    "vm_size_kb": 1572864,
    "uptime_secs": 47.2
  },
  "threads": [
    { "tid": 12345, "name": "ryll", "cpu_percent": 43.1 },
    { "tid": 12346, "name": "tokio-runtime-worker", "cpu_percent": 0.4 },
    { "tid": 12347, "name": "llvmpipe-0", "cpu_percent": 36.2 },
    ...
  ],
  "platform": "linux"
}
```

Or for non-Linux:

```json
{
  "platform": "macos",
  "available": false,
  "reason": "per-thread metrics not implemented on macOS"
}
```

## Constraints and edge cases

- **Sample window blocks the GUI.**  Acceptable for bug
  reports, which already block on the file dialog.
- **Thread count can be high** (16 llvmpipe + 16 tokio
  workers + a handful of others = ~35 on the user's
  machine).  Keep the JSON compact; include all threads
  rather than truncating.
- **Counter wraparound** for `utime`/`stime` is u64 ticks
  → not a real concern for any realistic ryll session.
- **Permissions**: `/proc/self/*` is always readable by
  the same UID, no privilege issues.
- **Test environment**: tests should not actually sample
  for 2 seconds.  Either expose the window as a parameter
  for testing, or skip the sampling test entirely and
  only test the parsing.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a   | medium | sonnet | none     | Add a new module `ryll/src/metrics.rs` with a `RuntimeMetrics` struct, a `sample(window: Duration) -> RuntimeMetrics` function (Linux only — gated by `#[cfg(target_os = "linux")]`), and a non-Linux fallback returning `RuntimeMetrics::unavailable(reason)`.  Read `/proc/self/stat`, `/proc/self/status`, `/proc/self/task/<tid>/stat`, `/proc/self/task/<tid>/comm`.  Compute CPU% as `(delta_utime + delta_stime) / sysconf(_SC_CLK_TCK) / window.as_secs_f64() * 100.0`.  Use `serde::Serialize` so the struct serialises directly to JSON.  Add unit tests for the parsing helpers (parse a sample `/proc/self/stat` string, verify field extraction) — do NOT add a test that actually sleeps for 2 seconds. |
| 5b   | medium | sonnet | none     | Wire `metrics::sample()` into `BugReport::new()` in [ryll/src/bugreport.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs).  Sample at the start of report assembly with a 2-second window.  Add a `runtime_metrics: RuntimeMetrics` field to the `BugReport` struct.  In the `write_zip()` method, write a new `runtime-metrics.json` file alongside the existing files.  Add a unit test that constructs a `BugReport` with a stub metrics value and verifies the ZIP contains `runtime-metrics.json` with the expected JSON shape. |
| 5c   | low    | sonnet | none     | Update [README.md](https://github.com/shakenfist/ryll/blob/develop/README.md) bug-report bullet (around line 24) to mention runtime metrics.  Update [docs/plans/PLAN-idle-cpu-and-latency.md](/components/ryll/plans/PLAN-idle-cpu-and-latency/) phase 5 status to Complete. |

## Success criteria for this phase

- F12 → Save → unzip the result → `runtime-metrics.json`
  is present and contains process + per-thread CPU% from
  the last 2 seconds.
- On Linux, llvmpipe threads (or whatever's hot) are
  visible in the per-thread list.
- On non-Linux, the file exists with a clear "platform
  unsupported" payload.
- `pre-commit run --all-files` and `make test` pass.
- The 2-second sample window is visible to the user but
  unobtrusive — the bug report dialog can show
  "Collecting metrics..." or just block the Save button.
- README.md mentions runtime metrics in the bug-report
  feature bullet.

## Open question

Should `runtime-metrics.json` also include version info
about the GPU stack (Mesa version, renderer string from
`glGetString(GL_RENDERER)`)?  That would have made the
"llvmpipe is the bottleneck" diagnosis even more
immediate.  **Recommendation: yes, if it's free** — wgpu
already queries adapter info during init; if we can capture
the adapter name and backend at startup and embed it here,
that's a one-liner.  If it requires a fresh wgpu context,
defer to follow-up.
