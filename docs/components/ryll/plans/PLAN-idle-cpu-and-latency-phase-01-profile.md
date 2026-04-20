# Phase 01: Idle CPU profile

## Goal

Identify the dominant contributor(s) to ryll's ~6-core idle
CPU consumption. Do not modify source. Gather data only.

## Method

Environment: Kasm container, Debian, 16-core machine (no GPU,
Mesa llvmpipe software renderer). Debug binary at
`target/debug/ryll` (built with Docker nightly toolchain,
unoptimised, full debug info). Connected to production SPICE
server at sf-3:46133 (TLS).

Profiling tools available: strace, valgrind. `perf` not
available (`perf_event_paranoid=3`). `cargo flamegraph` not
available. Measurement method: `/proc/<pid>/task/<tid>/stat`
jiffies before/after timed windows; `ps -o pcpu`; `top -H`
per-thread; strace -c syscall summary.

Two modes tested:
1. **Headless** (`--headless`): no GUI, tokio async channels
   only.
2. **GUI** (`DISPLAY=:10.0`, XFCE desktop): egui + wgpu +
   llvmpipe software renderer.

## Findings

### Headless mode

- Total process CPU: **~0.6% of one core** over 60 s.
- Only one tokio worker thread shows any activity: ~0.3%/core
  (network I/O for the active SPICE channels).
- Remaining 15 tokio workers: 0 jiffies each. All sleeping.
- strace summary over 15 s: 44 ms of total syscall time.
  `futex` (64 %) = tokio workers blocking on empty work
  queues. `epoll_wait` (16 %) = async I/O wake-ups.
  Completely idle; no busy-polling.
- Playback channel ping log rate (non-verbose, no `--verbose`
  flag): **4 messages in 60 s** (~0.07 Hz). The sf-3 server
  emits a burst of 2 pings at connect time then goes quiet
  on the playback channel.
- With `--verbose`: 64 opcode log lines in 30 s (~2.1 Hz
  across all channels). Manageable even if they all fired.

**Conclusion:** In headless mode, ryll is essentially idle.
All three suspects — repaint loop, logging, channel read
loops — contribute zero measurable CPU.

### GUI mode (debug binary + llvmpipe)

Thread snapshot at t=8 s wall clock (582 % total CPU):

| Thread group | Threads | Jiffies (8 s window) | % of one core |
|---|---|---|---|
| ryll main (egui) | 1 | 344 | 43 % |
| llvmpipe-0..15 | 16 | ~290 each, 4 640 total | 36 % each |
| Tokio workers | 16 | ~9 total | <1 % |
| ctrl-c, async-io | 2 | 0 | 0 % |

Total cores consumed: (344 + 4 640 + 9) / 800 jiffies/core
= **6.24 cores**. This matches the user-reported ~6 cores.

**Dominant contributor: llvmpipe software rasterisation.**

llvmpipe is Mesa's CPU-based OpenGL/Vulkan renderer. eframe
0.29 uses wgpu, which falls back to llvmpipe on machines with
no GPU. When `ctx.request_repaint_after(Duration::from_millis(16))`
fires unconditionally at the end of every `update()` call
(app.rs:2169), it schedules a new egui frame 16 ms later.
egui marks the frame dirty and wgpu submits a draw call.
llvmpipe then re-rasterises the full 1024×768 scene at 60 Hz
using all available CPU rasteriser threads (16 on this
machine). There is no "skip if unchanged" optimisation in
llvmpipe; every submitted draw call triggers a full render.

The surface dirty-flag check in `surface.rs:68` correctly
avoids re-uploading the texture when content has not changed.
However the wgpu render of the scene still runs regardless,
because egui always issues a draw call when it thinks a repaint
is pending — and with `request_repaint_after(16ms)` the repaint
is always pending.

### Suspect 2: Protocol logging (INFO-level on every message)

**Partially confirmed** as a code-quality issue, not a CPU
issue on this server. The unguarded `log_message` calls in
`playback.rs` fire on every received message with no
`is_verbose()` guard. Other channels (display, inputs, cursor,
usbredir, main) are all already guarded by `is_verbose()` or
`is_intimate()`. The playback channel is the only outlier.

On the sf-3 test server, the playback ping rate is low enough
(a burst at connect time then quiet) that this contributes
~0 measurable CPU in steady state. On a busier server or one
that sends periodic audio data, the unguarded logging would
matter. The embedded `format_timestamp()` String allocation on
every call is an unnecessary cost.

### Suspect 3: Channel read loops (spurious wakeups)

**Refuted.** Tokio workers consume <1 % of one core in both
modes. No spurious CPU usage is visible in the select! loops.

### Third contributor discovered: egui is debug-mode overhead

The debug binary amplifies the egui main-thread cost (344
jiffies ≈ 43 % core) relative to a release build. However,
the llvmpipe rasterisation cost is a function of scene
complexity and frame rate, not Rust optimisation level, so
the 16× llvmpipe threads dominate regardless. A release build
will reduce the egui thread overhead but will not significantly
reduce the llvmpipe threads.

On a machine with a real GPU, wgpu would use the GPU driver
and the llvmpipe threads would disappear. The 60 fps forced
repaint would still cause unnecessary GPU work, but modern
GPU drivers handle idle scenes much more efficiently.

## Recommendation

**Priority 1 (high impact everywhere): Fix the repaint
trigger.** Replace the unconditional
`ctx.request_repaint_after(16ms)` with targeted
`ctx.request_repaint()` calls from within the channel event
handler, fired only when a frame update, cursor move, or
meaningful status change arrives. This eliminates the forced
60 Hz render loop entirely. Expected result: llvmpipe drops
to 0 threads active during idle; GPU-equipped machines stop
unnecessary GPU work at idle.

**Priority 2 (code quality): Add `is_verbose()` guard to
playback `log_message` calls.** The receive path in
`playback.rs:415` calls `logging::log_message` without a
verbose guard, unlike every other channel. Also drop the
redundant embedded `[unix_timestamp]` from `log_message`
output since `tracing-subscriber` already adds one. Change
ping/pong log calls from `info!` to `debug!` at the same
time (this matches PLAN-idle-cpu-and-latency item 3).

**Priority 3 (lower impact): Tokio worker count.** Currently
defaults to all available cores (16 on this machine). For a
network client doing mostly I/O, a smaller pool (2–4 workers)
would reduce memory footprint without affecting throughput.
Not a CPU contributor today but worth noting.

## Measurement limitations

- Debug binary only (release build requires Docker toolchain).
  Debug amplifies egui thread cost; llvmpipe cost is
  release-independent.
- No GPU available; llvmpipe software rendering is the only
  backend. On a GPU-equipped machine, the cost of the 60 fps
  loop would be much smaller but still unnecessary.
- `perf` not available; no call-graph flamegraph. Thread
  jiffies give clear enough attribution without it.
- sf-3 SPICE server is lightly loaded (idle guest, infrequent
  pings). A busier session (active display, audio streaming)
  would change the tokio worker profile.
