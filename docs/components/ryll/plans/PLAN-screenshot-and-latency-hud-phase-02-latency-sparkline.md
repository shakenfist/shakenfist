# Phase 2: Latency sparkline

Parent plan: [PLAN-screenshot-and-latency-hud.md](/components/ryll/plans/PLAN-screenshot-and-latency-hud/)

## Goal

Render a rolling sparkline of recent latency samples in the
bottom stats panel, immediately after the existing
`Latency: Xms` label, mirroring the bandwidth sparkline
pattern from the top status bar.

## Background

The bandwidth sparkline is the model to copy:

- `BandwidthTracker` struct at
  [ryll/src/app.rs:78-122](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L78-L122) holds
  a `Vec<f32>` of samples capped at `BANDWIDTH_HISTORY_LEN`
  (60). It exposes `tick()` (advance), `label()` (format
  the current value), and `history` (read for rendering).
- The render block at
  [ryll/src/app.rs:1037-1062](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1037-L1062)
  computes a dynamic max from the visible window and draws
  fixed-width green bars.
- Bandwidth samples are pushed once per second from a
  shared `AtomicU64` byte counter. Latency works
  differently: samples arrive only when `ChannelEvent::Latency`
  fires (currently only via `--cadence` mode at
  [channels/inputs.rs:343-345](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs#L343-L345)).
- The latency value already lives at
  `App.stats.last_latency: Option<f64>`
  ([app.rs:53](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L53)) and is updated on each
  `ChannelEvent::Latency` at
  [app.rs:536-538](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L536-L538). The render
  site is [app.rs:971-973](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L971-L973):

  ```rust
  if let Some(latency) = self.stats.last_latency {
      ui.label(format!("Latency: {:.1}ms", latency * 1000.0));
      ui.separator();
  }
  ```

## Design

Two reasonable shapes for the history storage:

- **Option A:** A new `LatencyTracker` struct mirroring
  `BandwidthTracker`, with `tick()` doing nothing (or absent)
  and a `record(sample: f64)` method called from the
  `ChannelEvent::Latency` arm.
- **Option B:** Just add a `latency_history: Vec<f32>` field
  to `Statistics` and push directly from the event arm.

Choose **Option A**. It keeps the rendering site symmetric
with bandwidth, makes the sample-capacity constant explicit
(`LATENCY_HISTORY_LEN = 60`), and keeps the formatting logic
(`label()` for "X.Yms") in one place. It's also easier to
unit-test in isolation.

The tracker should hold milliseconds (not seconds), since
that's what the user sees and what makes the sparkline scale
intuitively.

## Constraints and edge cases

- **Empty history:** matches the bandwidth case — the
  existing render block guards with `history.len() >= 2`.
  Re-use that guard.
- **Single sample:** also caught by `>= 2` guard; bar won't
  render until two samples exist. Acceptable.
- **Render order in bottom panel:** after `Latency: Xms`,
  before the existing `ui.separator()`. The bottom panel is
  a single horizontal `ui.horizontal` block at
  [app.rs:970](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L970).
- **Headless mode:** `Statistics` is shared with the
  headless path, but the tracker only renders in the GUI.
  Adding the tracker as a field on `RyllApp` (not on the
  shared `Statistics`) keeps it GUI-only and avoids touching
  headless code. If `Statistics::last_latency` is the
  source of truth for the value, the tracker can listen on
  the same event arm.
- **Sparkline width/height:** match bandwidth: 80 × 12.
- **Bar colour:** use a different colour from bandwidth
  (green) so they're visually distinct. Suggest
  `Color32::from_rgb(180, 140, 80)` (amber) — latency is
  conceptually a "this should stay low" metric, amber reads
  better than green for that.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a   | medium | sonnet | none     | In [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs), add a `LATENCY_HISTORY_LEN: usize = 60` constant near `BANDWIDTH_HISTORY_LEN` (line 36). Add a `LatencyTracker` struct directly mirroring `BandwidthTracker` at lines 78-122: `history: Vec<f32>` (storing milliseconds), `record(&mut self, sample_ms: f32)` (push, trim to capacity), `label(&self) -> String` (format the most recent value as `"X.Yms"` or `"--ms"` if empty). No `tick()` needed — samples are event-driven, not time-driven. Add unit tests for `record` (capacity trimming) and `label` (empty / non-empty formatting). |
| 2b   | medium | sonnet | none     | Wire `LatencyTracker` into `RyllApp`. Add a `latency: LatencyTracker` field next to `bandwidth` ([app.rs:172](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L172)). Initialise it in the constructor ([app.rs:353](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L353)). In the `ChannelEvent::Latency` arm at [app.rs:536-538](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L536-L538), call `self.latency.record((key_timestamp * 1000.0) as f32)` after the existing `self.stats.last_latency = ...` assignment. |
| 2c   | medium | sonnet | none     | In [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) bottom stats panel around line 971-974, after `ui.label(format!("Latency: {:.1}ms", ...))` and before the existing `ui.separator()`, add a sparkline render block that exactly copies the structure of the bandwidth sparkline at lines 1037-1062 but reads from `self.latency.history` and uses `Color32::from_rgb(180, 140, 80)` for the bars. Keep the same `>= 2` guard, same dynamic max calculation, same 80×12 dimensions. |

## Success criteria for this phase

- Running `ryll --file connection.vv --cadence` shows an
  amber-coloured sparkline in the bottom stats panel
  immediately after the `Latency: Xms` label, populating
  with one bar every 2 seconds.
- Running without `--cadence` shows only the existing
  label (no sparkline) until a latency sample arrives,
  matching the bandwidth behaviour with no traffic.
- `pre-commit run --all-files` passes; `make test` passes;
  the new `LatencyTracker` unit tests pass.
- Commits: one for `LatencyTracker` struct + tests
  (step 2a), one for the wiring + render (steps 2b + 2c).
  Two commits total for this phase.
