# macOS runtime-metrics verification runbook

This runbook is the manual test suite for ryll's macOS
runtime-metrics implementation. It is addressed at a
maintainer running ryll on a real Mac. Re-run it whenever
`shakenfist-spice-renderer/src/metrics.rs` changes, and
before a release where macOS bug reports matter.

## Why this exists

The Mach FFI surface in `metrics.rs` cannot be compiled on
the Linux devcontainer, so the platform-independent unit
tests for delta math and JSON shape do not catch FFI-level
mistakes. The `#[cfg(target_os = "macos")]`-gated tests do
cover the FFI shape end-to-end, and CI does run them: the
macOS cell of the build matrix in
`.github/workflows/ci.yml` runs `cargo test --workspace`
with no `runner.os` guard. That job is in the merge tier,
though, so its `if:` restricts it to `merge_group` and
`workflow_dispatch` events — the macOS test signal arrives
when a change is enqueued to land, not on every push to a
pull request.

What no automated test can do is judge whether the values
are *plausible*: CPU% against Activity Monitor, RSS
against real memory usage, port-leak safety over hours.
That judgement is the whole point of the six tests and the
soak below, so they stay worth running even though the FFI
shape itself is covered automatically.

## Prerequisites

- A Mac with a debug or release ryll build.
- A SPICE server to connect to. Real QEMU exposing SPICE,
  or the project's `tools/web-smoke.sh` synthetic source,
  both work.
- `jq` installed (`brew install jq`).
- Optional: Activity Monitor open for the visual
  cross-checks.

## Acceptance tests

### Test 1 — MacOS variant is produced

Start ryll, connect to the SPICE server, file a bug report
(F12 or Menu → Report, then submit), save the zip, and
parse:

```sh
unzip -p ryll-bugreport-*.zip runtime-metrics.json | jq '.platform'
unzip -p ryll-bugreport-*.zip runtime-metrics.json | jq '.threads | length'
```

**Pass:** the first command prints `"macos"`; the second
prints a positive integer (the number of threads alive at
sample-B time).

**Fail:** any of:
- `"freebsd"` / other → the `cfg` dispatch in
  `metrics::sample` is wrong.
- `null` → the JSON does not have a top-level `platform`
  field; the JSON shape diverged from the `Linux` variant.
- `0` → the thread-enumeration path failed silently;
  inspect `task_threads` return in the Mac console log.

### Test 2 — `Unavailable` reason is gone

```sh
unzip -p ryll-bugreport-*.zip runtime-metrics.json | \
    grep -i "per-thread metrics not implemented"
```

**Pass:** no match.

**Fail:** any match means `metrics::sample` returned the
`Unavailable` variant. Most likely cause: one of
`task_info`, `task_threads`, or `thread_info` returned
non-`KERN_SUCCESS`. Check Console.app for warnings.

### Test 3 — `process.cpu_percent` is plausible

Open Activity Monitor and note the ryll process's "% CPU"
column at the moment of the bug-report trigger. Then:

```sh
unzip -p ryll-bugreport-*.zip runtime-metrics.json | jq '.process.cpu_percent'
```

**Pass:** within 50% relative of Activity Monitor's reading
(sampling skew is significant on both sides — ryll's window
is 2 seconds, Activity Monitor's is typically 5 seconds and
out of phase).

**Fail:** consistently > 2× or < 0.5× of Activity Monitor.
Most likely cause: a unit mistake in `time_value_to_us`
(seconds vs. microseconds vs. ticks) or in
`process_cpu_percent`'s division by `window_us`.

### Test 4 — `process.rss_kb` and `vm_size_kb` are plausible

In Activity Monitor's "Memory" tab, note the "Memory" and
"Virtual Memory" columns for the ryll process. Then:

```sh
unzip -p ryll-bugreport-*.zip runtime-metrics.json | jq '.process | {rss_kb, vm_size_kb}'
```

**Pass:** `rss_kb` within 50% relative of Activity Monitor's
Memory column (converted to kB); `vm_size_kb >= rss_kb`,
and typically much larger (VM overcommit is significant on
macOS).

**Fail:** RSS values orders of magnitude off → the bytes →
kilobytes conversion (`resident_size / 1024`) is wrong, or
`task_info` is returning a different `resident_size` than
expected. Check Apple's `mach/task_info.h` for the field
semantics.

### Test 5 — `process.uptime_secs` advances monotonically

File two bug reports a few minutes apart in the same ryll
session:

```sh
unzip -p ryll-bugreport-A-*.zip runtime-metrics.json | jq '.process.uptime_secs'
unzip -p ryll-bugreport-B-*.zip runtime-metrics.json | jq '.process.uptime_secs'
```

**Pass:** the second value is greater than the first by
roughly the wall-clock time elapsed between the two
triggers (within a few hundred ms).

**Fail:** the second value is *less* → `PROCESS_START` is
being reinitialised between samples (impossible with a
`LazyLock<Instant>` — flag as a bug).

**Bonus pass:** the first bug report filed within a few
seconds of `ryll` startup should already show a
small-but-non-zero `uptime_secs` (e.g. ≥ 1.0). The
`init_at_startup` call from `main()` is what makes this
true — without it the first report can read ~0. If the
first report shows a value matching the
real time since `ryll` started, `init_at_startup` is wired
correctly.

### Test 6 — `threads` populated, sorted, named where applicable

```sh
unzip -p ryll-bugreport-*.zip runtime-metrics.json | \
    jq '.threads | map({tid, name})'
```

**Pass:** all of:
- The array has at least 10 entries on a real session (ryll
  uses tokio + egui + audio + USB threads).
- At least one `name` is non-empty and matches a tokio-style
  pattern (e.g. `"tokio-runtime-worker"`).
- The `tid` values appear in ascending order.

**Fail:**
- Empty array → `task_threads` returned zero threads. Check
  whether the snapshot captured an enumeration window in
  which all worker threads happened to be parked. Re-run
  the test under load; the issue is more likely if you can
  reproduce it consistently.
- All names empty → `pthread_from_mach_thread_np` is
  returning NULL for every port, or `pthread_getname_np` is
  failing. Check whether ryll's tokio configuration sets
  thread names; the default does.
- Tids unsorted → `compute_thread_metrics`'s sort step is
  not running; flag as a bug.

## Mach port-leak soak

`task_threads` allocates an array of port rights every time
ryll samples runtime metrics. The `MachThreadList` RAII guard
in `mod macos` is responsible for releasing them. The unit
tests confirm the RAII shape; the soak confirms the *runtime*
behaviour over many calls.

### Procedure

1. Start ryll under pedantic mode against a real SPICE
   server. `--pedantic` is a boolean flag (auto-write a bug
   report zip per distinct protocol gap, capped at 50 per
   session); `--pedantic-dir <DIR>` is the separate flag
   that controls where those zips land. Both come before
   the SPICE connection arguments:
   ```sh
   ryll --pedantic --pedantic-dir /tmp/ryll-soak \
       spice://example/?password=…
   ```
   Pedantic mode triggers bug-report assembly on every new
   protocol gap (up to the 50-per-session cap), so
   `metrics::sample` runs each time, exercising
   `task_threads` + `MachThreadList::drop`. If the cap is
   hit early in the soak, leak detection still works
   (constant port count once sampling stops); for a longer
   stress, file bug reports manually (F12, then submit)
   every few minutes to keep `sample()` firing.
2. Record the initial Mach port count for the ryll process:
   ```sh
   RYLL_PID=$(pgrep ryll)
   vmmap -summary "$RYLL_PID" | grep -A 2 "Mach Ports"
   ```
   Note the line that says `Mach Ports: N`, where `N` is the
   port count.
3. Wait at least one hour while ryll runs. More is better;
   leaks scale linearly with sample count, so a tighter
   pedantic interval and a longer wait give a stronger
   signal.
4. Record the Mach port count again with the same command.
5. Count the approximate number of `metrics::sample` calls
   during the soak (one per pedantic-bug-report assembled —
   visible in `/tmp/ryll-soak/`).

### Pass criterion

- Final port count is within 20% of the initial count, AND
- (Final − Initial) / sample-count is less than 1.

Some growth is normal: tokio may spawn additional worker
threads under load, and each thread carries its own port
references in the system. A clean run typically shows
single-digit growth over a one-hour session.

### Fail diagnostics

If the count grows monotonically with sample count:

- First suspect: `MachThreadList::drop` is not running.
  Audit whether any code path between `task_threads` and the
  `MachThreadList { … }` literal could panic. There is no
  such path today (the only operations are infallible).
- Second suspect: `mach_port_deallocate` is failing
  silently. The current code ignores the return value
  because `Drop` cannot meaningfully recover. Temporarily
  un-ignore the return and log non-success codes; rerun the
  soak. A persistently failing `mach_port_deallocate`
  indicates the port was already released or the task port
  is bad — both unusual and worth root-causing.
- Third suspect: `vm_deallocate` is failing. Same
  diagnostic recipe.

## What to do if a test fails

| Symptom | Most likely cause | Where to look |
|---------|-------------------|---------------|
| Test 1 fails with `"freebsd"` etc. | `cfg` dispatch wrong | `metrics::sample` in `metrics.rs` |
| Test 2 fails | Mach syscall returned non-KERN_SUCCESS | `take_snapshot` / `take_thread_snapshots` |
| Test 3 fails | Unit mistake | `time_value_to_us`, `process_cpu_percent` |
| Test 4 fails | Memory unit / field semantics | `task_info` field read in `take_snapshot` |
| Test 5 fails | `PROCESS_START` reinit | Impossible by construction; flag as bug |
| Test 5 bonus fails | `init_at_startup` not called | `ryll/src/main.rs` top of `main()` |
| Test 6 empty array | `task_threads` returned 0 | `take_thread_snapshots` error path |
| Test 6 all names empty | `pthread_*_np` API change | `read_thread_name` |
| Test 6 unsorted | Sort step removed | `compute_thread_metrics` |
| Soak fails | Port leak | `MachThreadList::drop` |

After fixing, re-run the failing test plus any subsequent
test that depends on it. Tests 1–6 are largely independent;
the soak depends on the per-sample integration working
correctly (tests 1–6 passing).
