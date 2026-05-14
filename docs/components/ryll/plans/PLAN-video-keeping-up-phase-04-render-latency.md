# Phase 4: Render-side arrival-to-display latency

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
referenced source files, understand existing patterns (the
`ChannelEvent` enum, the per-channel event-tx senders, the
egui `process_events` drain loop, the existing `LatencyTracker`
that measures network-only PING-to-PING), and ground your
answers in what the code actually does today. Do not speculate
about the codebase when you could read it instead.

## Goal

Make "decode and I/O look healthy but the screen is still
laggy" a falsifiable hypothesis. After phase 4, a bug report
exposes how long display events (`ImageReady*` and
`DisplayMark`) sit in the channel→app mpsc queue before the
egui frame loop picks them up. A maintainer reading the
report can then say: phases 1–3 say socket / decode /
writer-task are fine; phase 4 lag is high; therefore the
egui loop is starved (probably by long-running synchronous
work inside `App::update`), and that's where to look.

This phase is **optional per the master plan** and is only
justified once a real "video not keeping up" bug report
collected with phases 1–3 active shows decode and I/O are not
the bottleneck. Implementing it now means the next report
already has the data, no second pass needed.

Out of scope:
- "Inside the egui paint" timing. That would require
  instrumenting eframe or the platform compositor and is
  disproportionate to the diagnostic value.
- End-to-end "pixels visible to user" timing. Unmeasurable
  without external instrumentation.
- Changes to renderer or egui architecture.

## Design

### Definition of render-side latency

**Render-side latency = the time between event emission in
the display channel and event processing in the egui frame
loop.** Specifically, for an event `e`:

```
lag_us(e) = (consumed_at_secs - e.produced_at_secs) * 1_000_000
```

where `produced_at_secs` is captured in `display.rs` just
before `event_tx.send(e)` and `consumed_at_secs` is captured
in `app.rs` immediately after `event_rx.try_recv()` returns
`Ok(e)`. Both use `self.traffic.elapsed().as_secs_f64()`,
which shares a single epoch across the renderer and the host
(verified during phase 1 — `TrafficSink::elapsed()` returns
`start.elapsed()` from a single fixed `Instant`).

Two event types are instrumented:

- **`ImageReady` / `ImageReadyChroma` / `ImageReadyAlpha`**:
  per-image emission. High cadence (many per server frame).
  Catches "egui is processing one ImageReady per egui frame
  while the channel is emitting ten" — i.e. the renderer
  cannot keep up with the decode rate.
- **`DisplayMark`**: per-server-frame boundary. Lower cadence.
  Catches "egui frame loop is paced at 30 fps but the server
  is sending at 60 fps and the marks back up".

The two metrics share plumbing and tell different stories;
both are kept.

### What this catches that phases 1–3 do not

| Phase 1 sees | Phase 4 sees |
|--------------|--------------|
| Decode time (renderer-side) | Time the event sat in the mpsc before app pickup |
| Socket-read fill ratio | egui loop pickup cadence |
| ACK-window cadence | (n/a) |

Phase 4's `ImageReady*` lag rising while phase 1's
`decode_recent_max_us` and `socket_reads_at_chunk_cap` look
healthy is the diagnostic pattern that points at the
renderer/egui loop. With phases 1–3 plus phase 4, every
phase of the pipeline has at least one observable.

### Event schema changes

In `shakenfist-spice-renderer/src/channels/mod.rs`:

```rust
pub enum ChannelEvent {
    // ... existing variants ...
    ImageReady {
        display_channel_id: u8,
        surface_id: u32,
        left: u32,
        top: u32,
        width: u32,
        height: u32,
        pixels: Vec<u8>,
        #[allow(dead_code)]
        image_id: u64,
        /// Session-relative seconds at the moment the
        /// emitting channel called `event_tx.send`. Used by
        /// the app to compute mpsc-queue lag. See
        /// PLAN-video-keeping-up-phase-04.
        produced_at_secs: f64,
    },
    ImageReadyChroma {
        // ... existing fields, plus:
        produced_at_secs: f64,
    },
    ImageReadyAlpha {
        // ... existing fields, plus:
        produced_at_secs: f64,
    },
    DisplayMark {
        produced_at_secs: f64,
    },
    // ... other variants unchanged ...
}
```

`DisplayMark` is currently a unit variant (`DisplayMark`).
Phase 4 promotes it to a struct variant so it can carry the
timestamp. All consumers must be updated, but in practice
there's only one (the app's `process_events`).

### Emission sites in `display.rs`

Six call sites build `ImageReady*` events:

- Line 522 — `build_image_event` for `CompositeMode::Normal`
- Line 532 — `build_image_event` for `CompositeMode::ChromaKey`
- Line 543 — `build_image_event` for `CompositeMode::AlphaBlend`
- Line 1194 — direct `event_tx.send(ImageReady { … })` for
  MJPEG stream frames
- Line 1888 — direct `build_image_event` call for one decode path
- Line 1905 — direct `build_image_event` call for another decode path

…plus one `DisplayMark` emit site (`display.rs:962`).

The cleanest implementation centralises the timestamp grab
in `build_image_event` so all three `ImageReady*` paths share
it. Direct call sites set the timestamp explicitly. The
`DisplayMark` site captures the timestamp at the
`event_tx.send` line.

### App-side lag tracking

In `ryll/src/app.rs`, add to `RyllApp` (next to `video_drop_count`):

```rust
/// Phase-04: bounded ring of mpsc-queue lag samples (μs)
/// for `ChannelEvent::ImageReady*` events. Each entry is
/// `(consumed_at - produced_at) * 1_000_000` from the app's
/// event-drain loop. Cap 32, oldest evicted.
recent_image_ready_lag_us: VecDeque<u32>,
/// Phase-04: same shape but for `ChannelEvent::DisplayMark`.
recent_display_mark_lag_us: VecDeque<u32>,
```

Define `const RECENT_LAG_RING_CAP: usize = 32;` near the
existing `FPS_WINDOW_SIZE` constant.

In `process_events` (around `ryll/src/app.rs:1398`):

```rust
while let Ok(event) = self.event_rx.try_recv() {
    let consumed_at_secs = self.traffic.elapsed().as_secs_f64();
    match event {
        ChannelEvent::ImageReady { produced_at_secs, .. }
        | ChannelEvent::ImageReadyChroma { produced_at_secs, .. }
        | ChannelEvent::ImageReadyAlpha { produced_at_secs, .. } => {
            let lag_us = ((consumed_at_secs - produced_at_secs).max(0.0)
                          * 1_000_000.0) as u32;
            push_with_cap(&mut self.recent_image_ready_lag_us, lag_us);
            // ... existing blit logic, with destructuring of the
            // remaining fields ...
        }
        ChannelEvent::DisplayMark { produced_at_secs } => {
            let lag_us = ((consumed_at_secs - produced_at_secs).max(0.0)
                          * 1_000_000.0) as u32;
            push_with_cap(&mut self.recent_display_mark_lag_us, lag_us);
            // ... existing DisplayMark handling ...
        }
        // ... other variants ...
    }
}
```

`push_with_cap` is a tiny local helper:

```rust
fn push_with_cap(ring: &mut VecDeque<u32>, value: u32) {
    ring.push_back(value);
    if ring.len() > RECENT_LAG_RING_CAP {
        ring.pop_front();
    }
}
```

Saturate negative differences at zero (`.max(0.0)`) to handle
the harmless case where `consumed_at` reads back slightly
earlier than `produced_at` due to monotonic clock granularity
on some platforms.

### Snapshot fields

In `ryll/src/bugreport.rs`, extend `AppSnapshot`:

```rust
pub struct AppSnapshot {
    // ... existing fields ...
    /// Phase-04: min / max / mean μs of mpsc-queue lag over
    /// the most recent `ImageReady*` events processed by the
    /// egui frame loop. Window size is the
    /// `RECENT_LAG_RING_CAP` constant in `app.rs`. Zero when
    /// no qualifying samples have been observed. A high mean
    /// when phase-1 decode and socket-fill metrics look
    /// healthy implicates the egui loop / GUI thread as the
    /// bottleneck. See PLAN-video-keeping-up-phase-04.
    pub image_ready_lag_recent_min_us: u32,
    pub image_ready_lag_recent_max_us: u32,
    pub image_ready_lag_recent_mean_us: u32,
    /// Same shape but for `DisplayMark` events (per-frame
    /// boundary). Lower cadence than `image_ready_*` so the
    /// recent window covers a longer real-time interval.
    pub display_mark_lag_recent_min_us: u32,
    pub display_mark_lag_recent_max_us: u32,
    pub display_mark_lag_recent_mean_us: u32,
}
```

No raw `VecDeque<u32>` rings in the snapshot — the
aggregates are sufficient for the diagnostic question. This
keeps `session.json` small and matches phase 1's
`decode_recent_*` pattern.

In `update_app_snapshot()` (`ryll/src/app.rs:1930-1970`),
compute the six values from the channel struct's rings:

```rust
let (img_min, img_max, img_mean) =
    recent_lag_stats(&self.recent_image_ready_lag_us);
snap.image_ready_lag_recent_min_us = img_min;
snap.image_ready_lag_recent_max_us = img_max;
snap.image_ready_lag_recent_mean_us = img_mean;

let (mark_min, mark_max, mark_mean) =
    recent_lag_stats(&self.recent_display_mark_lag_us);
snap.display_mark_lag_recent_min_us = mark_min;
snap.display_mark_lag_recent_max_us = mark_max;
snap.display_mark_lag_recent_mean_us = mark_mean;
```

`recent_lag_stats` is a private helper analogous to phase 1's
`recent_decode_duration_stats`:

```rust
fn recent_lag_stats(ring: &VecDeque<u32>) -> (u32, u32, u32) {
    if ring.is_empty() {
        return (0, 0, 0);
    }
    let mut min = u32::MAX;
    let mut max = 0;
    let mut sum: u64 = 0;
    for &v in ring {
        if v < min { min = v; }
        if v > max { max = v; }
        sum += v as u64;
    }
    let mean = u32::try_from(sum / ring.len() as u64)
                  .unwrap_or(u32::MAX);
    (min, max, mean)
}
```

### Within-batch correlation: a known limitation

`process_events` drains the event channel in a tight
`try_recv` loop. Every event drained in one call sees the
same `consumed_at_secs` (modulo a few clock ticks). So if 10
`ImageReady` events were pending when an egui frame fires,
the 10 lag samples will be close together in time and tightly
correlated. The max sample in the ring is therefore the most
informative single number; the mean is biased by batch size.

This is documented in the doc comments on the snapshot fields
and accepted. A future refinement could sample at most one
event per drain call to decorrelate, but the diagnostic
question — "did anything sit in the queue for a long time?"
— is answered by `max` either way.

### Where `produced_at_secs` is set

To keep emission-site changes minimal, plumb the timestamp
through `build_image_event` so the three composite variants
share one capture point:

```rust
fn build_image_event(
    &self,
    surface_id: u32,
    base: &DrawBase,
    img: DecompressedImage,
    composite: CompositeMode,
) -> ChannelEvent {
    let produced_at_secs = self.traffic.elapsed().as_secs_f64();
    match composite {
        CompositeMode::Normal => ChannelEvent::ImageReady {
            // ... existing fields ...
            produced_at_secs,
        },
        // ... similarly for ChromaKey and AlphaBlend ...
    }
}
```

The MJPEG-stream emit (`display.rs:1194`) inlines an
`ImageReady` literal; it captures the timestamp directly at
the `event_tx.send` line.

The `DisplayMark` emit (`display.rs:962`) captures the
timestamp at its single line.

## Steps

### Step 1: Extend `ChannelEvent` variants with `produced_at_secs`

1. In `shakenfist-spice-renderer/src/channels/mod.rs`, add
   `produced_at_secs: f64` to `ImageReady`,
   `ImageReadyChroma`, and `ImageReadyAlpha`. Promote
   `DisplayMark` from a unit variant to a struct variant
   with a single `produced_at_secs: f64` field.
2. Update any `match ChannelEvent::DisplayMark` patterns in
   the codebase to `match ChannelEvent::DisplayMark { .. }`.

### Step 2: Set timestamps at emission sites

1. In `shakenfist-spice-renderer/src/channels/display.rs`,
   modify `build_image_event` to capture
   `self.traffic.elapsed().as_secs_f64()` once at the top
   and embed it in all three composite variants.
2. Update the direct `ImageReady` literal at line 1194 to
   set `produced_at_secs`.
3. Update the `DisplayMark` send at line 962 to capture and
   pass the timestamp.

### Step 3: Add app-side lag rings

1. Define `const RECENT_LAG_RING_CAP: usize = 32;` near
   `FPS_WINDOW_SIZE` in `ryll/src/app.rs`.
2. Add `recent_image_ready_lag_us: VecDeque<u32>` and
   `recent_display_mark_lag_us: VecDeque<u32>` to `RyllApp`,
   initialised to empty in the constructor.
3. Add the `push_with_cap` and `recent_lag_stats` private
   helper fns (free functions in `app.rs`; co-locate with
   the existing helpers).

### Step 4: Compute lag in `process_events`

1. At the top of the `while let Ok(event) = …` loop body,
   capture `consumed_at_secs = self.traffic.elapsed().as_secs_f64()`.
2. For each of the four event variants, destructure
   `produced_at_secs`, compute the lag, and `push_with_cap`
   into the matching ring. Saturate negatives at zero.
3. Keep all existing handler logic intact — phase 4 only
   adds the lag-recording lines.

### Step 5: Extend `AppSnapshot`

1. Add the six new fields to `AppSnapshot` in
   `ryll/src/bugreport.rs` with `Default` covered by `0`.
2. In `update_app_snapshot()`, call `recent_lag_stats` over
   each ring and assign the three values to the matching
   snapshot fields.

### Step 6: Tests

1. **`recent_lag_stats_empty_ring_returns_zeros`** — pure
   function test.
2. **`recent_lag_stats_computes_min_max_mean`** — feed
   `[100, 300, 200]` and assert `(100, 300, 200)`.
3. **`push_with_cap_caps_at_RECENT_LAG_RING_CAP`** — push 40
   distinct values; assert ring length is 32 and the oldest
   8 are evicted.
4. **`app_snapshot_image_ready_lag_serialises`** — extend
   `test_app_snapshot_serialises` in
   `ryll/src/bugreport.rs` to set the six new fields and
   assert they appear in the JSON.

A direct integration test "ImageReady emitted with timestamp
T, app processes at T+lag, snapshot reflects lag" requires
spinning up a display channel with a fake stream; that's
heavier than the diagnostic value warrants. The unit tests
plus the existing snapshot-serialisation test cover the
contract.

### Step 7: Build, test, lint, pre-commit gates

`make build`, `make test`, `make lint`, and
`pre-commit run --all-files` all pass.

### Step 8: Documentation

1. Extend the "Reading display `channel-state.json` for
   'video not keeping up'" section in
   `docs/troubleshooting.md` to cover the new `AppSnapshot`
   fields in `session.json` and how to read them in
   combination with phases 1–3's data. Specifically: a high
   `image_ready_lag_recent_max_us` when phase-1
   `decode_recent_max_us` and `socket_reads_at_chunk_cap`
   look healthy points at the egui loop.
2. Update `ARCHITECTURE.md`'s `AppSnapshot` row to mention
   the new fields.
3. Mark phase 4 `Done` in the master plan's execution
   table.
4. Promote the master plan's Open Question to a Resolved
   decision: render-side latency = mpsc-queue lag for
   `ImageReady*` and `DisplayMark` events; rationale
   already in this plan's Design section.

## Administration and logistics

### Success criteria

- All four event variants (`ImageReady`, `ImageReadyChroma`,
  `ImageReadyAlpha`, `DisplayMark`) carry
  `produced_at_secs: f64`.
- `AppSnapshot` exposes six new fields: min/max/mean μs of
  recent-window lag for `ImageReady*` and `DisplayMark`.
- A bug report from a real session shows
  `image_ready_lag_recent_mean_us` in the low tens of μs on
  a healthy egui loop and rises observably under artificial
  load.
- All existing tests pass.
- New tests in step 6 pass.
- `make build`, `make test`, `make lint`, and
  `pre-commit run --all-files` all pass.

### Risks

- **Within-batch correlation** (documented in Design). The
  `max` over the ring is the most informative number; the
  mean is biased toward batch size. Accepted; documented.
- **`DisplayMark` enum-variant promotion is a breaking API
  change inside the renderer crate.** The crate's only
  consumer in this repo is ryll itself; both
  `display.rs` (emit) and `app.rs` (consume) are updated in
  the same commit. No external consumers.
- **`produced_at_secs` field clash with future Serde
  round-tripping.** `ChannelEvent` is not serialised today
  (it lives entirely in-process); the new field is fine for
  now. If `ChannelEvent` ever grows a `Serialize` derive,
  the field naturally serialises with the rest.
- **Lag during egui startup.** Before the first egui paint,
  events may queue for hundreds of ms. The ring fills with
  startup-lag samples that aren't representative of
  steady-state. After 32 events the ring rotates and
  steady-state values dominate; the doc comment notes this.
- **Clock granularity on some platforms.** `Instant` on
  macOS / Linux is nanosecond-grade; on Windows it's
  100 ns. Either way far below the μs unit we report.
  Saturating negative differences at zero handles the
  theoretical edge case.
- **Wasted work if real bug reports show phases 1–3 always
  catch the bottleneck.** Phase 4 was framed as optional;
  the cost of doing it now is one focused change-set, and
  the cost of skipping it is needing a second collection
  pass when the first report says "decode and I/O look
  fine". Pre-empting that round-trip seems worth the cost.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
