# Phase 2: Per-thread enumeration and naming on macOS

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
referenced source files, understand existing patterns (the
phase-1 `mod macos` block in
`shakenfist-spice-renderer/src/metrics.rs`, the existing
Linux per-thread implementation in the same file, and the
`Snapshot` → delta-math factor-out pattern), and ground your
answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.

## Goal

Populate `RuntimeMetrics::MacOS::threads` so macOS bug reports
list every thread alive across the sample window with a stable
identifier, the thread's name (from `pthread_getname_np`), and
its CPU% over the window — matching the diagnostic value of
the Linux per-thread block.

Phase 2 introduces the Mach-port lifecycle work the master
plan flagged: `task_threads` allocates an array of port rights
that **must** be released or ryll leaks one port reference per
thread per snapshot (potentially hundreds per minute under
sustained sampling). An RAII wrapper handles cleanup on every
exit path, including panic.

Out of scope:
- Integration soak / port-leak verification with `Activity
  Monitor` over a long-running session — phase 3.
- Bug-report-path integration testing on a real Mac — phase 3.
- iOS, Windows, FreeBSD — out of master-plan scope.

## Design

### Cross-snapshot thread matching: `thread_identifier_info`

The challenge for per-thread delta math is matching threads
between snapshot A and snapshot B. Mach thread ports
(`thread_act_t`) returned by `task_threads` are **not stable**
across calls — the same OS thread may have a different port
number in two snapshots taken seconds apart.

The fix: `thread_info(port, THREAD_IDENTIFIER_INFO, …)`
returns a `thread_identifier_info` struct whose `thread_id`
field is a 64-bit kernel-assigned identifier stable for the
thread's lifetime. The libc 0.2 crate exposes
`libc::THREAD_IDENTIFIER_INFO` (value 4) and
`libc::thread_identifier_info` directly. Phase 2 calls
`thread_info` **twice** per Mach port per snapshot: once with
`THREAD_BASIC_INFO` for CPU times, once with
`THREAD_IDENTIFIER_INFO` for the stable id. Two syscalls per
thread per snapshot is fine — typical ryll process has 20–40
threads, so ~80 syscalls per `sample()` call. Negligible.

### `ThreadSnapshot` and `Snapshot` extensions

In `#[cfg(target_os = "macos")] mod macos`:

```rust
#[derive(Debug, Clone)]
pub(super) struct ThreadSnapshot {
    /// 64-bit kernel-assigned thread id; stable for the
    /// thread's lifetime. Sourced from
    /// `thread_identifier_info.thread_id`.
    pub thread_id: u64,
    pub user_time_us: u64,
    pub system_time_us: u64,
    /// Name from `pthread_getname_np`; empty string if the
    /// thread is unnamed or the lookup fails.
    pub name: String,
}

#[derive(Debug, Clone)]
pub(super) struct Snapshot {
    pub user_time_us: u64,
    pub system_time_us: u64,
    pub resident_size: u64,
    pub virtual_size: u64,
    /// Phase-2: per-thread snapshot. Empty in phase-1
    /// builds; populated by `take_thread_snapshots`.
    pub threads: Vec<ThreadSnapshot>,
}
```

The existing process-level `take_snapshot()` extends to also
call `take_thread_snapshots()` and store the result. The
return is `Result<Snapshot, &'static str>` as in phase 1;
thread enumeration failure (rare) falls back to
`RuntimeMetrics::unavailable("task_threads failed")`.

### `MachThreadList` RAII wrapper

`task_threads(task, &mut list, &mut count)` allocates two
things that must be released:

1. **Each port in the list** — every `thread_act_t` entry is
   a send-right reference. Release via
   `mach_port_deallocate(mach_task_self(), port)`.
2. **The list memory itself** — allocated by the kernel via
   `vm_allocate`. Release via
   `vm_deallocate(mach_task_self(), list_ptr, list_bytes)`.

RAII wrapper:

```rust
struct MachThreadList {
    ports: *mut libc::thread_act_t,
    count: libc::mach_msg_type_number_t,
}

impl MachThreadList {
    fn as_slice(&self) -> &[libc::thread_act_t] {
        if self.ports.is_null() || self.count == 0 {
            return &[];
        }
        // SAFETY: task_threads returned a valid array of
        // `count` thread_act_t entries; we never modify it.
        unsafe { std::slice::from_raw_parts(self.ports, self.count as usize) }
    }
}

impl Drop for MachThreadList {
    fn drop(&mut self) {
        if self.ports.is_null() || self.count == 0 {
            return;
        }
        // Release every port right.
        for &port in self.as_slice() {
            // SAFETY: each port is a send-right returned by
            // task_threads; deallocating against
            // mach_task_self() is the documented inverse.
            unsafe {
                let _ = mach_port_deallocate(libc::mach_task_self(), port);
            }
        }
        // Release the array memory.
        let bytes = (self.count as usize)
            * std::mem::size_of::<libc::thread_act_t>();
        // SAFETY: task_threads allocated `bytes` worth of
        // thread_act_t entries via vm_allocate against
        // mach_task_self(); vm_deallocate is the documented
        // inverse.
        unsafe {
            let _ = libc::vm_deallocate(
                libc::mach_task_self(),
                self.ports as libc::vm_address_t,
                bytes as libc::vm_size_t,
            );
        }
    }
}
```

Drop intentionally ignores the return values: there is
nothing meaningful to do on a cleanup failure, and the
function is called from `Drop` which cannot return an error.
Cleanup failures on these specific calls would imply an
already-corrupt Mach port table, which is a process-wide
problem outside this module's scope.

### `mach_port_deallocate` declaration

`libc = "0.2.186"` does not expose `mach_port_deallocate`.
The fix is a local `extern "C"` block — three lines, no new
crate dependency:

```rust
extern "C" {
    fn mach_port_deallocate(
        task: libc::mach_port_t,
        name: libc::mach_port_t,
    ) -> libc::kern_return_t;
}
```

This matches the Apple-documented signature:
`kern_return_t mach_port_deallocate(ipc_space_t task,
mach_port_name_t name)` where both type aliases resolve to
`mach_port_t`. The function lives in `libsystem_kernel.dylib`
and is part of the stable Mach userspace ABI.

If a future libc 0.2.x adds this symbol, the local
declaration becomes redundant but not conflicting (Rust
allows multiple identical `extern "C"` decls in different
scopes); a follow-up patch can simply delete the local block.

### Per-thread snapshot

```rust
fn take_one_thread_snapshot(
    port: libc::thread_act_t,
) -> Option<ThreadSnapshot> {
    // (1) THREAD_BASIC_INFO for CPU times.
    let mut basic: libc::thread_basic_info = unsafe { std::mem::zeroed() };
    let mut count: libc::mach_msg_type_number_t =
        libc::THREAD_BASIC_INFO_COUNT;
    // SAFETY: thread_info has no preconditions beyond a
    // valid thread port and a correctly-sized buffer. The
    // call does not retain any pointer past return. If the
    // thread died between task_threads and this call,
    // thread_info returns KERN_FAILURE (or similar non-
    // success), and we drop the thread by returning None.
    let kr = unsafe {
        libc::thread_info(
            port,
            libc::THREAD_BASIC_INFO as libc::thread_flavor_t,
            &mut basic as *mut _ as libc::thread_info_t,
            &mut count,
        )
    };
    if kr != libc::KERN_SUCCESS {
        return None;
    }

    // (2) THREAD_IDENTIFIER_INFO for stable thread_id.
    let mut ident: libc::thread_identifier_info =
        unsafe { std::mem::zeroed() };
    let mut count: libc::mach_msg_type_number_t =
        libc::THREAD_IDENTIFIER_INFO_COUNT;
    // SAFETY: same as above.
    let kr = unsafe {
        libc::thread_info(
            port,
            libc::THREAD_IDENTIFIER_INFO as libc::thread_flavor_t,
            &mut ident as *mut _ as libc::thread_info_t,
            &mut count,
        )
    };
    if kr != libc::KERN_SUCCESS {
        return None;
    }

    Some(ThreadSnapshot {
        thread_id: ident.thread_id,
        user_time_us: time_value_to_us(basic.user_time),
        system_time_us: time_value_to_us(basic.system_time),
        name: read_thread_name(port),
    })
}
```

`read_thread_name` is best-effort:

```rust
fn read_thread_name(port: libc::thread_act_t) -> String {
    // SAFETY: pthread_from_mach_thread_np maps a Mach port
    // for a thread *in the current process* to its
    // pthread_t. We only ever pass ports returned by
    // task_threads(mach_task_self()), which satisfies that
    // requirement. Returns NULL for unknown ports; we treat
    // that as "no name".
    let pthread = unsafe { libc::pthread_from_mach_thread_np(port) };
    if pthread.is_null() {
        return String::new();
    }
    // Apple's MAXTHREADNAMESIZE is 64 bytes (including nul).
    let mut buf = [0i8; 64];
    // SAFETY: pthread_getname_np writes at most `len` bytes
    // including the nul terminator into the buffer. Reading
    // up to the nul via CStr::from_ptr is safe afterwards.
    let rc = unsafe {
        libc::pthread_getname_np(pthread, buf.as_mut_ptr(), buf.len())
    };
    if rc != 0 {
        return String::new();
    }
    let cstr = unsafe { std::ffi::CStr::from_ptr(buf.as_ptr()) };
    cstr.to_string_lossy().into_owned()
}
```

64 bytes matches Apple's documented `MAXTHREADNAMESIZE`.
ryll's tokio worker threads use names like
`"tokio-runtime-worker"`; well under the cap.

### Enumeration helper

```rust
fn take_thread_snapshots() -> Result<Vec<ThreadSnapshot>, &'static str> {
    let mut ports: *mut libc::thread_act_t = std::ptr::null_mut();
    let mut count: libc::mach_msg_type_number_t = 0;
    // SAFETY: task_threads writes ports + count by pointer.
    // mach_task_self() is process-lifetime and cannot fail.
    let kr = unsafe {
        libc::task_threads(libc::mach_task_self(), &mut ports, &mut count)
    };
    if kr != libc::KERN_SUCCESS {
        return Err("task_threads failed");
    }
    // RAII wrapper takes ownership of the port array; cleanup
    // happens regardless of whether we early-return below.
    let list = MachThreadList { ports, count };

    let mut snapshots = Vec::with_capacity(list.count as usize);
    for &port in list.as_slice() {
        if let Some(snap) = take_one_thread_snapshot(port) {
            snapshots.push(snap);
        }
        // Skip threads that died between task_threads and
        // the per-thread thread_info; matches Linux's
        // tolerance for threads that disappear mid-window.
    }
    Ok(snapshots)
}
```

### Cross-snapshot delta math

Factored out for testability. Uses a `HashMap<u64, &ThreadSnapshot>`
on A keyed by `thread_id`:

```rust
pub(super) fn compute_thread_metrics(
    a: &[ThreadSnapshot],
    b: &[ThreadSnapshot],
    window: Duration,
) -> Vec<ThreadMetrics> {
    use std::collections::HashMap;
    let a_by_id: HashMap<u64, &ThreadSnapshot> =
        a.iter().map(|t| (t.thread_id, t)).collect();
    let window_us = window.as_micros().max(1) as u64;
    let mut out: Vec<ThreadMetrics> = Vec::with_capacity(b.len());
    for tb in b {
        let (user_delta, sys_delta) = match a_by_id.get(&tb.thread_id) {
            Some(ta) => (
                tb.user_time_us.saturating_sub(ta.user_time_us),
                tb.system_time_us.saturating_sub(ta.system_time_us),
            ),
            // Thread is in B but not A — started mid-window.
            // Linux parity: report 0% CPU.
            None => (0, 0),
        };
        let total = user_delta.saturating_add(sys_delta);
        out.push(ThreadMetrics {
            tid: tb.thread_id,
            name: tb.name.clone(),
            cpu_percent: (total as f64 / window_us as f64) * 100.0,
        });
    }
    // Sort by tid for deterministic JSON output; Linux's
    // implementation does the same.
    out.sort_by_key(|t| t.tid);
    out
}
```

Threads present in A but not B (died mid-window) are simply
not iterated — they don't produce a record. Matches Linux's
implicit behaviour: only threads observed in the second
snapshot make it into the output.

### `sample()` body change

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
    let threads = compute_thread_metrics(
        &snap_a.threads,
        &snap_b.threads,
        window,
    );
    RuntimeMetrics::MacOS {
        sample_window_ms: window.as_millis() as u64,
        process: ProcessMetrics {
            cpu_percent,
            rss_kb: snap_b.resident_size / 1024,
            vm_size_kb: snap_b.virtual_size / 1024,
            uptime_secs: process_uptime_secs(),
        },
        threads,
        platform: "macos".to_string(),
    }
}
```

Only the `Vec::new()` for `threads` is replaced. Everything
else from phase 1 is unchanged.

## Steps

### Step 1: Extend `Snapshot` with thread list

1. Add `ThreadSnapshot` struct in `mod macos`.
2. Add `threads: Vec<ThreadSnapshot>` field to `Snapshot`.
3. Extend `take_snapshot()` to call `take_thread_snapshots()`
   and store the result. Snapshot failure propagates as
   `Err(&'static str)` like phase 1's pattern.

### Step 2: Declare `mach_port_deallocate` and add `MachThreadList`

1. Add `extern "C" { fn mach_port_deallocate(…) -> kern_return_t; }`
   block inside `mod macos`.
2. Define `MachThreadList` struct, `as_slice()` method, and
   `Drop` impl. The `Drop` impl issues `mach_port_deallocate`
   on every entry and `vm_deallocate` on the array.

### Step 3: Implement thread enumeration + name lookup

1. Implement `take_thread_snapshots() -> Result<Vec<ThreadSnapshot>, &'static str>`
   using `task_threads` + the `MachThreadList` RAII wrapper.
2. Implement `take_one_thread_snapshot(port) -> Option<ThreadSnapshot>`
   using two `thread_info` calls (THREAD_BASIC_INFO,
   THREAD_IDENTIFIER_INFO).
3. Implement `read_thread_name(port) -> String` using
   `pthread_from_mach_thread_np` and `pthread_getname_np`
   with a 64-byte buffer.
4. Each `unsafe { … }` block carries a SAFETY comment in the
   same shape as phase 1's and the existing `clk_tck()`
   precedent.

### Step 4: Implement and unit-test `compute_thread_metrics`

1. Implement the helper as designed.
2. Wire it into `sample()`, replacing `threads: Vec::new()`
   with `threads: compute_thread_metrics(...)`.

### Step 5: Tests

The four new platform-independent tests (mirroring phase-1's
factor-out pattern) live in the existing `#[cfg(test)] mod
tests` block. Their `use super::macos::…` imports are gated
by `#[cfg(target_os = "macos")]` because `mod macos` itself
is gated. Compilation on non-macOS targets skips them.

1. **`test_macos_compute_thread_metrics_basic`** — two
   `ThreadSnapshot` vectors with matching thread_ids, known
   user/system deltas, window of 100 ms. Assert the resulting
   `ThreadMetrics` have the expected `cpu_percent` to two
   decimal places.
2. **`test_macos_compute_thread_metrics_new_thread`** —
   thread present in B but not A appears with 0% CPU and
   the correct name (no `unwrap`/`Option`).
3. **`test_macos_compute_thread_metrics_dropped_thread`** —
   thread present in A but not B is absent from the output
   (does not produce a phantom record with negative or
   massive CPU%).
4. **`test_macos_compute_thread_metrics_sorted_by_tid`** —
   input vectors in arbitrary order produce output sorted
   ascending by `tid`.
5. **`test_macos_compute_thread_metrics_zero_window`** —
   `Duration::from_millis(0)` produces finite `cpu_percent`
   for every output thread (the `.max(1)` µs guard from
   phase 1 generalises).

Plus one update to the existing phase-1 smoke test on macOS:

6. **`test_macos_sample_returns_populated_variant`** —
   extend to assert `threads.len() > 0` (every Mac process
   has at least the main thread). For each thread, assert
   `cpu_percent.is_finite()` and `cpu_percent >= 0.0`. At
   least one thread should have a non-empty `name` (the
   tokio runtime names its workers).

Mach-port-leak verification is **not** in the unit test
suite — observing port-table state from the same process
requires `mach_port_kobject` or similar deep introspection
and is fragile. The RAII wrapper's `Drop` impl is verified
by code review here; the long-running soak in phase 3 is the
empirical check.

### Step 6: Build, test, lint, pre-commit gates

`make build`, `make test`, `make lint`, and
`pre-commit run --all-files` all pass. The Linux devcontainer
compiles the `mod macos` block's source as text (so rustfmt
and clippy see it) but does not compile its code (`#[cfg]`
gates it out). Any FFI-level mistake — wrong libc symbol
name, wrong argument type — surfaces only on a real Mac.

### Step 7: Documentation

1. Update the module-level doc-comment in `metrics.rs` to
   describe the per-thread layer (it currently says phase 1
   leaves `threads` empty).
2. Update `ARCHITECTURE.md` "Runtime metrics in bug reports"
   bullet to drop the phase-1 caveat that the macOS `threads`
   array is empty.
3. The master plan's execution table marks phase 2 `Done`
   when this lands; phase 3 (integration + soak) remains
   `Not started`.

## Administration and logistics

### Success criteria

- `RuntimeMetrics::MacOS::threads` is populated on a real
  Mac with one `ThreadMetrics` entry per Mach thread alive at
  snapshot-B time.
- Each entry carries a stable `tid` (the kernel `thread_id`
  from `THREAD_IDENTIFIER_INFO`).
- Threads named via `pthread_setname_np` (e.g. tokio
  workers) report their name; unnamed threads report an
  empty string.
- Threads that disappear mid-window do not appear in the
  output; threads that start mid-window appear with 0% CPU.
- The four new platform-independent unit tests pass on every
  CI matrix entry.
- The macOS smoke test (gated to `target_os = "macos"`) runs
  green on a real Mac.
- `make build`, `make test`, `make lint`,
  `pre-commit run --all-files` all pass.
- No port leak is observable over a long-running session
  (verified empirically in phase 3, not here).

### Risks

- **`mach_port_deallocate` signature drift.** The function is
  part of the stable Mach userspace ABI; signature change
  would break every Mac process on the system. The risk is
  effectively zero, but the local `extern "C"` block adds a
  failure surface that would not exist if libc 0.2 exposed
  the symbol. If we want to remove the local decl entirely,
  use the `mach2` crate — but adding a dependency for one
  function is the larger cost.
- **`pthread_from_mach_thread_np` returning NULL.** Apple
  documents this as a possible return for ports not
  associated with a pthread (e.g. kernel-internal threads
  that surface via `task_threads`). Phase 2 handles this by
  reporting an empty name; the thread still appears in the
  output with its `thread_id`.
- **Port count under stress.** `task_threads` allocates a
  port reference per thread. The RAII wrapper releases them,
  so the leak risk is "we forget to wrap" — caught by the
  fact that `MachThreadList::new` is the only constructor.
  But a panic *between* `task_threads` and the
  `MachThreadList { … }` literal would leak. Mitigation:
  there is no operation between the two that can panic
  (`null_mut()` and `0` are infallible literals); the
  literal initialiser cannot panic. Audited in step 2.
- **Many threads.** ryll's tokio runtime can have 8–16 worker
  threads, plus egui main, audio, USB, and capture tasks —
  total often 20–40 in a real session. Each thread incurs
  two `thread_info` syscalls per snapshot, two snapshots per
  `sample()`. Worst case ~160 syscalls per `sample()`. At
  the existing 2-second sample window this is well under
  ambient overhead.
- **Cannot exercise the FFI surface in the Linux
  devcontainer.** Same constraint as phase 1. The platform-
  independent delta-math tests catch most regressions; the
  Mach calls themselves rely on macOS CI (from
  `PLAN-ci-platform-matrix.md`) or manual Mac compile.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
