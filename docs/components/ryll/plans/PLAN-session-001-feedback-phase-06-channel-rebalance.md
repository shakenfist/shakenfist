# Phase 06: Rebalance per-channel ring-buffer split by expected traffic

## Prompt

Before answering questions or making design decisions in this
document, read the relevant ryll source. Key files:
`ryll/src/bugreport.rs:80-173` (the `TrafficRingBuffer`
implementation — `VecDeque<TrafficEntry>` with a byte cap and
FIFO eviction), `ryll/src/bugreport.rs:175-181` (the current
`PER_CHANNEL_BYTES = 50 MB / 6` even split and the `CHANNELS`
name list), `ryll/src/bugreport.rs:185-226` (the
`TrafficBuffers` struct that owns one `Mutex<TrafficRingBuffer>`
per channel and the `buffer_for(channel)` dispatch).
Cross-reference the master plan's Open Question 1 resolution
at `docs/plans/PLAN-session-001-feedback.md:75-120` for the
quantified traffic profile observed in session-001
(`~2 MB/s typical, 6 MB/s peak` on display vs. effectively-
infinite retention on the low-bandwidth channels) and the
"Future work" note at `:225-232` that explicitly defers
dynamic sizing to a later master plan.

This phase rebalances the 50 MB total ring-buffer budget
across ryll's six tracked channels so the bytes flow to the
channel that consumes them. The master plan classifies this
as small but load-bearing for the F2 always-fileable
notification UX laid out in Phase 10: the snapshot-at-fire
window for video-lag complaints is bounded by display ring
retention.

## Situation

### What we already established

**The ring buffer is byte-capped, evenly split.** Each of
the six tracked channels (`main`, `display`, `inputs`,
`cursor`, `usbredir`, `playback`) gets exactly
`50 MB / 6 ≈ 8.33 MB`. Eviction is FIFO when the byte total
exceeds the per-channel cap (`bugreport.rs:112-118`). The
6-way split is hard-coded in `PER_CHANNEL_BYTES` and used
identically in each `TrafficRingBuffer::new()` call at
`bugreport.rs:200-205`.

**Display starves; everyone else hoards.** From session-001
B2's `bandwidth_history`, display sustains 2 MB/s during
typical interactive use and bursts to ~6 MB/s during video
playback. An 8.33 MB cap holds roughly:

| Channel  | Approx rate (session-001) | Coverage at current 8.33 MB cap |
|----------|---------------------------|----------------------------------|
| display  | 2 MB/s typical, 6 MB/s peak | **~1.5–4 seconds**             |
| playback | 16–64 KB/s stereo audio   | ~2–8 minutes                     |
| cursor   | 1–50 KB/s mouse traces    | many minutes                     |
| main     | < 1 KB/s PING/PONG + clipboard | hours                       |
| inputs   | ~100 B/s mouse + keys     | hours                            |
| usbredir | bursty: ~0 KB/s idle, ~50 MB/s during mass-storage transfer | idle: ∞; active: **~0.17 s** |

The display window is *the* binding constraint on the F2
notification-snapshot UX. Phase 10 will plumb "snapshot at
fire" against this ring buffer; if it holds only 1.5 s of
display history, a user complaining about a video stutter
ten seconds ago has nothing useful in the report.

**`usbredir` is unrescuable.** Active USB transfers can hit
50 MB/s. No realistic per-channel cap can capture more than
a fraction of a second of an in-flight transfer. The
rebalance can only meaningfully help when usbredir is idle —
which is most of the session, most of the time. The
practical floor for usbredir is "enough bytes that an idle
usbredir's session-long control traffic fits" — a few MB.

**`webdav` is missing from the ring buffer entirely.**
`buffer_for(channel)` at `bugreport.rs:216-225` has no
`"webdav"` arm, and the `CHANNELS` list at `:179-181` has
six entries. The webdav channel never records traffic to a
ring buffer at all. This is a separate gap; see "Out of
scope" below for the rationale on deferring it.

### What we don't yet know

The peak rate ryll actually sustains under modern guest
workloads (e.g. 4K screen sharing, high-fps video
conferencing). session-001's profile is from a Linux guest
at moderate resolutions and is treated here as a
representative-rather-than-extreme baseline. The rebalance
below is sized against it; if session-002 shows display
peaking notably higher, we should revisit. Dynamic / system-
memory-aware sizing is the eventual answer (master plan's
"Future work" item) but disproportionate for this phase.

## Mission and problem statement

Reallocate the existing 50 MB ring-buffer budget across the
six tracked channels so that:

- The display channel holds at least **5 s of peak traffic**
  (≥ 30 MB at 6 MB/s) or **15 s of typical** (≥ 30 MB at
  2 MB/s) — whichever bounds is hit first.
- Low-rate channels (main, inputs) drop below 8.33 MB
  without losing useful history depth (still many minutes,
  which is well beyond any realistic notification or
  disconnect window).
- The total remains 50 MB — no budget growth in this phase
  (dynamic sizing is explicitly out of scope per master plan).
- The eviction policy (FIFO byte-cap) stays as-is.

The phase succeeds when:

- A Phase 10–shaped notification-snapshot pcap from a busy
  display session contains noticeably more pre-fire display
  history than the same scenario captured today.
- No regression in low-rate channels — their snapshot pcaps
  cover the same wall-time history they always did (in
  practice, the entire session, since their traffic is
  far below the new caps).
- The new constants are documented (numbers and rationale)
  so a future session that wants to retune them has the
  weights and units to reason about.

## Approach

### Weight-based allocation

Assign each channel an integer weight roughly proportional
to its sustained traffic rate, then divide the 50 MB
budget by total weight:

| Channel  | Weight | Cap         | Rough retention at typical rate |
|----------|--------|-------------|----------------------------------|
| display  | 16     | 32 MB       | 16 s @ 2 MB/s, 5 s @ 6 MB/s peak |
| usbredir |  2     |  4 MB       | session-long when idle           |
| playback |  2     |  4 MB       | many minutes                     |
| cursor   |  2     |  4 MB       | many minutes                     |
| main     |  2     |  4 MB       | hours                            |
| inputs   |  1     |  2 MB       | hours                            |
| **Total**| **25** | **50 MB**   |                                  |

The unit budget is 2 MB / weight × 25 weights = 50 MB. The
weights are deliberately small integers so a future tweak
("display should be even bigger") is a one-line bump that
rebalances visibly. The numeric caps are explicit rather
than computed from weights at compile time — keeps the
constants greppable and avoids `const fn` arithmetic ergonomics.

Why these weights:

- display:16 is dominant because display traffic is the
  problem the rebalance exists to solve. 32 MB covers the
  busy case (~5 s at peak) without starving everyone else.
- usbredir:2 acknowledges that no rebalance can capture an
  active USB transfer; 4 MB is enough for the idle/control
  case and for a fraction-of-a-second tail at the start of
  a transfer (which is when the interesting protocol
  state changes happen anyway).
- playback:2, cursor:2, main:2 — all roughly an order of
  magnitude below display, but with non-trivial sustained
  rates. 4 MB each keeps them at "many minutes" of
  retention, well past any practical notification or
  disconnect window.
- inputs:1 is the lowest sustained-rate channel by a wide
  margin; 2 MB holds hours of keystrokes and mouse events.

### Constants and validation

Replace `PER_CHANNEL_BYTES` in `bugreport.rs` with six named
constants:

```rust
// Per-channel ring-buffer caps. Total = 50 MB, weighted by
// observed session-001 traffic rates (see Phase 06 plan).
// Weights documented per-line so a retune is a one-line edit.
const DISPLAY_BUFFER_BYTES: usize  = 32 * 1024 * 1024;  // weight 16
const USBREDIR_BUFFER_BYTES: usize =  4 * 1024 * 1024;  // weight  2
const PLAYBACK_BUFFER_BYTES: usize =  4 * 1024 * 1024;  // weight  2
const CURSOR_BUFFER_BYTES: usize   =  4 * 1024 * 1024;  // weight  2
const MAIN_BUFFER_BYTES: usize     =  4 * 1024 * 1024;  // weight  2
const INPUTS_BUFFER_BYTES: usize   =  2 * 1024 * 1024;  // weight  1

/// Budget shared across all per-channel ring buffers. Pinned
/// at 50 MB; dynamic sizing keyed on system memory is the
/// master plan's "Future work" item and explicitly out of
/// scope for this phase.
const TOTAL_TRAFFIC_BUFFER_BYTES: usize = 50 * 1024 * 1024;
```

Update `TrafficBuffers::new()` to use the channel-specific
constants in each `Mutex::new(TrafficRingBuffer::new(...))`
slot. The body is otherwise unchanged.

A compile-time sum assertion plus a unit test guard
against arithmetic drift:

```rust
const _: () = assert!(
    DISPLAY_BUFFER_BYTES
        + USBREDIR_BUFFER_BYTES
        + PLAYBACK_BUFFER_BYTES
        + CURSOR_BUFFER_BYTES
        + MAIN_BUFFER_BYTES
        + INPUTS_BUFFER_BYTES
        == TOTAL_TRAFFIC_BUFFER_BYTES,
    "per-channel caps must sum to the total traffic-buffer budget"
);
```

`const _: () = assert!(...)` fails at build time — the
compiler refuses to construct an `assert!` that's `false` at
const eval. Cheap, no runtime cost, no test harness needed.

The accompanying runtime test in `bugreport::tests` confirms
each channel's buffer was actually constructed with its
intended cap (via `TrafficRingBuffer::total_bytes`-like
introspection or a per-channel `max_bytes` accessor — the
existing `total_bytes()` getter at `:170` returns the live
count, not the cap, so a small `max_bytes()` getter is
added at the same time).

### Testing

Three new unit tests in `bugreport::tests`:

- `traffic_buffer_per_channel_caps_match_plan` — constructs
  a `TrafficBuffers`, locks each channel's mutex, asserts the
  underlying `TrafficRingBuffer::max_bytes()` equals the
  expected constant per channel. Pin against silent regressions
  (e.g. a future refactor that accidentally collapses back to
  even-split).
- `traffic_buffer_total_budget_is_50mb` — duplicates the
  compile-time check at runtime as a guard that's visible in
  CI test output. Belt and suspenders.
- `traffic_buffer_display_holds_more_than_other_channels` —
  shape assertion: display > everything else. Catches a
  weight-typo regression (e.g. someone swapping display and
  inputs by mistake) without pinning specific numbers, which
  the first test already does.

A new `pub fn max_bytes(&self) -> usize { self.max_bytes }`
accessor on `TrafficRingBuffer` is added (private field today;
only exposed via tests). One-liner, no behaviour change.

### Manual verification

The integration check pairs with the Phase 10 work it
unblocks (notification-snapshot UX). For now, a focused
manual test:

1. Run `ryll --file <busy-display.vv>` against a guest that
   produces sustained display traffic (e.g. play a YouTube
   clip in the guest).
2. After ~30 s of playback, hit F8 (manual bug report).
3. Open the resulting pcap in Wireshark, filter by display
   channel, observe the wall-clock span of the captured
   frames. Target: substantially more than the pre-Phase-06
   1.5–4 s window.
4. Spot-check the other channels' pcaps in the same zip —
   they should still cover at least the full session
   duration (their traffic is well below the new caps).

## Tasks

- [x] Replace `PER_CHANNEL_BYTES` in `ryll/src/bugreport.rs`
      with the six per-channel named constants (display,
      usbredir, playback, cursor, main, inputs) plus
      `TOTAL_TRAFFIC_BUFFER_BYTES`. Comment block above the
      constants summarises the weighting and references this
      phase plan.
- [x] Add the `const _: () = assert!(...)` compile-time
      sum-check immediately after the constants
      (`#[allow(clippy::assertions_on_constants)]` to suppress
      the dead-assert lint — the const-time check *is* the
      point).
- [x] Update each `Mutex::new(TrafficRingBuffer::new(...))`
      slot in `TrafficBuffers::new()` to use the
      channel-specific constant.
- [x] Add a `pub fn max_bytes(&self) -> usize` accessor on
      `TrafficRingBuffer`.
- [x] Add three unit tests in `bugreport::tests`:
  - `traffic_buffer_per_channel_caps_match_plan`
  - `traffic_buffer_total_budget_is_50mb`
  - `traffic_buffer_display_holds_more_than_other_channels`
- [x] Update `PLAN-session-001-feedback.md` Execution table
      row for Phase 06 → Done. There is no master-plan
      "bug" entry for Phase 06 (it's an improvement, not a
      bug fix), so the Confirmed-Bugs table needs no edit.
- [ ] Manual integration check (deferred operator action,
      bundles with the running Phase 02–05 manual checklist):
      capture a busy-display F8 report, spot-check the
      pcap's display-channel wall-clock span against the
      pre-Phase-06 1.5–4 s window.

## Out of scope

- **Dynamic / system-memory-aware sizing.** The master plan
  pins this as "Future work" — scaling the total budget to
  `min(total_ram * fraction, ceiling)` with a 50 MB floor
  for parity and a `RYLL_TRAFFIC_CAP_MB` override.
  Disproportionate for this phase, would tangle with the
  static-rebalance decision. Revisit only if Phase 06 + 09
  combined don't produce useful pre-fire windows on real
  hardware.
- **Wiring webdav into the ring buffer.** Currently the
  webdav channel does not call `traffic.record_*` at all,
  and `buffer_for()` has no `"webdav"` arm. Adding it
  requires (a) plumbing `Arc<dyn TrafficSink>` into the
  webdav channel constructor, (b) calling `record_received`
  / `record_sent` from the webdav mux read/write paths, and
  (c) adding a `webdav` field + cap to `TrafficBuffers`.
  Each is small but together they form a separate phase
  ("Webdav traffic recording"). When that phase lands, it
  will need to take a small slice from the budget here —
  e.g. shrinking display from 32 MB to 30 MB and giving
  webdav a 2 MB weight-1 share — but doing it now would
  speculate against work that has not been planned yet.
- **Eviction policy changes.** FIFO byte-cap stays. Smarter
  eviction (e.g. retaining a coarser "history" sample once
  the ring is full) is its own master-plan item.
- **`Arc<[u8]>` for `TrafficEntry::pcap_frame`.** This is
  explicitly Phase 07 in the master plan; Phase 06's
  rebalance does not depend on it and vice versa.

## Open questions

1. **Should the rebalance also lift the total budget?** No
   in this phase. The master plan defers the total-budget
   question to a "Future work" dynamic-sizing item. The
   rebalance is a low-risk static change; bundling a budget
   bump would conflate two decisions and make rollback (if
   it turns out we mis-sized) harder. Revisit once Phase 10
   is live and we have evidence about whether 50 MB is the
   binding constraint.

2. **Should weights be exposed as CLI overrides for
   debugging?** No. The "Future work" item already covers
   the operator-knob direction (`RYLL_TRAFFIC_CAP_MB`); a
   per-channel override would proliferate. If a developer
   needs to retune mid-investigation, a recompile is fine.

3. **Does the asymmetric weighting risk surprising a
   maintainer reading a pcap?** The Phase 10 notification-
   snapshot work surfaces "snapshot at fire" vs. "post-event
   only" markers in the report metadata. The same marker
   layer can record per-channel cap diagnostics — but only
   when the cap actually matters (i.e. when the ring evicted
   anything during the captured window). Defer the
   per-channel-cap-record decision to Phase 10 where the
   context is correct.

## Companion docs

None new. `ARCHITECTURE.md`'s ring-buffer description (if
present) describes the byte-capped FIFO mechanism, which is
unchanged; the new sizing is documented in code comments and
in this plan. `AGENTS.md` likewise has no entry to update —
the rebalance does not introduce a new pattern.
