# Phase 2: Repaint cadence fix

Parent plan: [PLAN-idle-cpu-and-latency.md](/components/ryll/plans/PLAN-idle-cpu-and-latency/)

## Goal

Stop driving the egui render loop at an unconditional 60 Hz.
Phase 1 measured this single line as the cause of ~6 of
6.24 idle cores: each repaint marks the frame dirty, wgpu
re-rasterises the full scene, and Mesa's llvmpipe spreads
that across all 16 CPU rasteriser threads.  The same
behaviour exists on systems with real GPUs — it just costs
power instead of CPU there.

Target: idle CPU under 10% of one core, with no perceptible
change in interactive responsiveness.

## Background

[ryll/src/app.rs:2169](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2169):

```rust
ctx.request_repaint_after(std::time::Duration::from_millis(16));
```

This is the only repaint trigger that runs every frame.
Two other repaint sites exist:

- [app.rs:411](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L411): `ctx.request_repaint()`
  in the constructor's connection setup.
- [app.rs:1438](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1438) and
  [app.rs:1659](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1659):
  `request_repaint_after(1s)` inside conditional dialog
  branches.

Channel events arrive on `event_rx` (a tokio `mpsc::Receiver`)
and are drained by `process_events()` at
[app.rs:493](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L493) which uses `try_recv()`
in a loop.  egui only wakes if something has called
`request_repaint`; if it doesn't wake, the events sit in
the queue.

Channel tasks themselves run on the tokio runtime and have
no access to `egui::Context` today.  That's the obstacle:
the natural fix is "wake egui when an event is pushed", but
the pusher (a channel handler) doesn't have a context
handle.

## Approach

Two viable shapes:

### Option A: Pass `egui::Context` to channel tasks

Plumb `egui::Context` (cheaply cloneable; it's an `Arc`
internally) into each channel handler's spawn site, store
it on the handler, and call `ctx.request_repaint()` after
every `event_tx.send(...)`.

Pros: precisely event-driven; egui sleeps fully when idle.
Cons: touches many files (every channel handler), grows
constructor signatures, couples the protocol crates to
egui (or requires a trait abstraction to avoid that
coupling).

### Option B: Single bridging task

Spawn one tokio task at startup that owns an
`egui::Context` clone and a clone of the event sender's
notification source.  Whenever an event is enqueued, the
task calls `ctx.request_repaint()`.

Implementation: add a `tokio::sync::Notify` shared between
the producer (channel handlers) and the bridging task.
Channel handlers call `notify.notify_one()` after each
`event_tx.send(...)`.  The bridging task does
`notify.notified().await; ctx.request_repaint();` in a loop.

Pros: minimal change to channel handlers (one new line per
send site).  egui still sleeps when idle.  No egui
dependency in protocol crates.

Cons: slightly indirect; a stray `notify_one` without a
matching `send` would cause a wasted repaint (harmless).

### Option C: Leave the polling loop, slow it down

Change `Duration::from_millis(16)` to `from_millis(33)` or
`from_millis(50)`.  Half or a third of the CPU.  Trivial,
no other changes.

Pros: one-character change.  Reversible.  No event-routing
work.  Still responsive to most inputs.

Cons: still rasterises 20-30 full frames per second of
nothing.  Doesn't fix the underlying behaviour.  CPU
target (<10% of one core) is unreachable with this alone.

### Recommendation

**Option B with a fallback timer.**  Event-driven repaints
for everything that matters (channel events, mouse
movement, keyboard input — egui handles the latter two
itself), plus a *slow* periodic repaint (say, 1 Hz) so
sparklines and time-based UI elements still update.

This gives:

- Idle CPU collapse from ~6 cores to near zero (egui
  sleeps; one tokio task wakes once a second to ping the
  bandwidth tracker).
- Full responsiveness to channel events (display updates,
  cursor moves, audio).
- Sparklines tick once a second, matching their actual
  data-arrival cadence.

The 1 Hz fallback is much weaker than the 60 Hz current
state — 60x less work — but still catches anything that
relies on `Instant::elapsed()` style logic (transient
status messages, the bandwidth tracker's `tick()`).

## Constraints and edge cases

- **Mouse-over-surface and keyboard input** already trigger
  egui repaints via egui's own input handling; no fix
  needed there.
- **Bandwidth tracker** ticks once per second
  ([app.rs:bandwidth.tick() at line ~1050](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs)).
  The 1 Hz fallback covers this exactly.
- **Cadence mode** (`--cadence`) injects a keystroke every
  2 seconds; that's its own task that calls
  `request_repaint` already, or relies on the existing 60
  Hz.  Need to verify and possibly add an explicit repaint
  call there.
- **Bug-report status message timeout** (5-second fade at
  [app.rs:1080-1086](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1080-L1086)) needs at
  least one repaint after the deadline to clear the label.
  The 1 Hz fallback covers this too.
- **Connection-state transitions** (connect, disconnect)
  are channel events; the new event-driven path handles
  them.
- **TLS handshake and connection retries** happen in async
  tasks before any `event_rx.send`.  If they need to update
  the UI mid-handshake, they need a repaint trigger too.
  Verify.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a   | high   | opus  | none     | In [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs), spawn a "repaint bridge" tokio task during `RyllApp::new`.  The task holds (1) an `egui::Context` clone obtained from `cc.egui_ctx.clone()` in the eframe `creator`, and (2) a clone of an `Arc<tokio::sync::Notify>`.  The task body is `loop { notify.notified().await; ctx.request_repaint(); }`.  Store the `Arc<Notify>` on `RyllApp` so the management code that pushes channel events can call `notify.notify_one()`.  Then, every place that sends on `event_tx` (the same `mpsc::Sender<ChannelEvent>` that channel handlers hold) must call `notify.notify_one()` immediately after.  Identify those sites: search for `event_tx.send(` in `ryll/src/channels/`.  Pass the `Arc<Notify>` into each channel handler's `new()` alongside the existing `event_tx`.  This is intrusive — multiple files change.  Then in `update()` at [app.rs:2169](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2169), replace `request_repaint_after(16ms)` with `request_repaint_after(1s)` as a fallback for time-based UI elements (sparklines, status message expiry).  Verify the cadence mode keystroke injection still wakes egui; if not, add a `notify.notify_one()` there too.  Add a brief `// Repaint when channel events arrive; 1s fallback for time-based UI.` comment near the new code. |
| 2b   | low    | sonnet | none    | Manual smoke test against `make test-qemu`: (a) connect, observe idle CPU drops to <10% of one core after a few seconds with no input; (b) move mouse over the surface — UI responds without lag; (c) type — guest sees keystrokes; (d) trigger a status message (F8 with no surface) — message appears and clears within ~5 seconds; (e) bandwidth sparkline updates once per second.  Document the measured idle CPU in this plan file and update the master plan's success-criteria check. |

## Success criteria for this phase

- Idle CPU under 10% of one core (measured: connected, no
  input, no display activity, mouse outside window).
- All interactive behaviour unchanged: typing, mouse,
  scroll, dialog open/close, status messages, sparklines.
- `pre-commit run --all-files` passes; `make test` passes.
- Single commit for step 2a (the implementation), single
  commit for step 2b (the measurement note appended to
  this file).
