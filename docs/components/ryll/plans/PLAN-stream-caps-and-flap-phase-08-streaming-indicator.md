# Phase 8 — Live streaming indicator + flap notification

Phase 8 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Goal

Two UI affordances driven by the per-stream snapshot data
phase 1 landed:

1. **Live status-bar icon** (a small video-camera-style glyph)
   whose colour reflects current streaming state. Updates
   live; hover shows per-stream tooltip.
2. **Flap notification** (one-shot, cool-down-gated `NotifySeverity::Warn`)
   that fires when ≥3 streams are destroyed in the last 30 s
   with mean lifetime < 3 s.

Both read from `streams_active` and `streams_recently_destroyed`
on the display channel — same data; different visualisation.

## Why now

Session 005's "stream lives 8 seconds then never returns"
pattern would have been faster to spot if the status bar
told us in real time. Phase 13's investigation will also
benefit — being able to *see* the stream live/die during a
test cycle without scraping snapshots after the fact will
shorten every debug loop.

## Scope

In scope:

- **Derived-state helper** in `ryll/src/app.rs` (or a small new
  module `ryll/src/streaming_state.rs` if cleaner), reading
  the latest display snapshot and producing a
  `StreamingState { Off, Active, RecentlyDestroyed, Flapping }`
  plus a per-active-stream tooltip list.
- **Status-bar widget** in the existing
  `TopBottomPanel::bottom("stats")` block (around
  `ryll/src/app.rs:2960`), placed in the right-to-left
  layout near the volume controls. Colour-coded glyph;
  `on_hover_ui` for the tooltip. Width budget: ~16 px icon,
  no text.
- **Flap watcher** that runs inside `update_snapshot` (no
  separate task — the work is constant-time per call).
  Same heuristic the master plan picked at start: ≥3
  destroyed in last 30 s, mean lifetime < 3 s, 60 s cool-down.
  Fires via `push_notification(NotifySeverity::Warn,
  NotificationSource::Internal, ...)`.
- **State plumbing**: a single `last_flap_notification_ts:
  Option<Instant>` field on `RyllApp` for the cool-down,
  plus the derived-state computation per `update`.

Out of scope:

- A new full-blown stats panel. The status bar gets the icon;
  the bug-report fields (already exposed in 12F) carry the
  detail.
- Click-to-open behaviour on the icon. Hover tooltip is enough
  for v1; an `egui::Window` for stream history is a phase-10
  nicety.
- Configurable thresholds. The 3/30/3/60 heuristic is the
  starting point; revisit after field experience per the
  master plan's "Future work" section.
- Touching `DisplaySnapshot` itself. All needed data is
  already there post-phase-1 + 12F.

## Step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8A | medium | sonnet | none | **Derived-state computation + unit tests.** Add a small `streaming_state.rs` module (or inline if it stays under ~80 lines) that takes a `&DisplaySnapshot` plus the current time and the last-flap timestamp, and returns `(StreamingState, Option<NotificationToFire>)`. `StreamingState` is `Off / Active / RecentlyDestroyed(t_since) / Flapping`. The classifier: Off iff `streams_active.is_empty()` and no destroy in last 5 s; Active iff `streams_active` non-empty; RecentlyDestroyed iff destroy within 5 s; Flapping iff the 3/30/3 heuristic fires. The notification is emitted iff Flapping AND `now - last_flap_ts >= 60 s`. Unit-test the four states with synthetic snapshots; unit-test the cool-down with two flap-eligible calls 10 s apart (one notification) and 70 s apart (two notifications). |
| 8B | medium | sonnet | none | **Wire into update + status bar.** In `ryll/src/app.rs::update` (around the stats panel), call the new helper once per frame using the latest snapshot. Render a small icon in the bottom panel's right-to-left layout, just before the volume controls (so the muted/volume buttons stay rightmost — they're the operator-action zone). Colour: grey for Off, green for Active, amber for RecentlyDestroyed, red for Flapping. On hover, render a tooltip listing each active stream's `codec_type / size / frames_decoded_ok / lifetime_secs`. If the helper returns a `NotificationToFire`, call `push_notification`. Store the last-flap timestamp on `RyllApp` and thread it through. Use `egui::widgets::Image::new` with an inline SVG or a Unicode glyph (📹 or similar) — pick whichever renders cleanly across mac/Linux/Windows. |
| 8C | low | haiku | none | **Docs.** Add a "Streaming indicator" subsection to `docs/troubleshooting.md` explaining the four colours, what each means, and what to look for in a bug report when the indicator goes amber/red. Cross-link from the phase-13 plan (the indicator is the cheapest way to monitor stream survival during investigation runs). Update `README.md` if it screenshot-tours the status bar. Verify `pre-commit run --all-files`. |
| 8D | — | — | — | **Operator smoke test.** Run any 1024×768 video session (where streams are stable per 004a/005a) — icon should go green and stay there during playback. Run a 1920×1440 session — icon should go green briefly then amber then back to grey as the stream dies. If the 005 pattern reproduces (stream destroys at ~8 s, no re-engagement), the icon should sit amber for 5 s then revert to grey. Flap notification should NOT fire on a single destroy; it requires ≥3-in-30-s. Confirm by stress-testing — repeatedly toggle a video on/off in <10 s windows to force flaps. |

## Open questions

- **Icon source.** Vector glyph from egui's built-in font (📹 is
  in the default emoji set), or a small inline SVG? Glyph is
  cheaper to ship but renders differently per platform. **8B
  decides.**
- **Tooltip layout.** Per-stream rows, or a single summary line?
  Per-stream rows for v1 (4–5 rows max in practice; if more,
  truncate with "+N more").

## Success criteria

- `make build && make test && make lint && pre-commit run --all-files` clean.
- New unit tests for the four-state classifier and the
  flap cool-down all pass.
- Operator sees the icon change colour during a video session
  in real time.
- Flap notification fires once-per-cool-down when synthetic
  stress reproduces the pattern.
- No regression in the existing status-bar layout (latency
  sparkline, FPS label, cadence label, auto-snapshot counter,
  reconnect indicator, USB label, bandwidth sparkline,
  volume controls all still render correctly).
