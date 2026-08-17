# Phase 3 — tests for the resize state machine

Parent: [PLAN-display-window-sizing.md](/components/ryll/plans/PLAN-display-window-sizing/).

## Goal

Phases 1 and 2 introduced the always-fit logic and its
opt-out toggle. The pure decision helper
`compute_auto_resize` already has unit tests
(`compute_auto_resize_decisions` in `ryll/src/app.rs`).
What still has no automated coverage is the *outgoing* side
of the resize round-trip — `maybe_send_monitors_resize`'s
inline alignment-and-dedup logic — and the composition of
the two halves: the property that "after auto-fit to a
guest surface, the next frame's outgoing-resize check
dedupes to no-op so we do not echo our own resize back to
the guest as a fresh `VDAgentMonitorsConfig`."

This phase extracts the outgoing-resize math into a pure
helper to mirror `compute_auto_resize`, tests it
comprehensively, and adds a small round-trip composition
test. The two helpers together cover the parts of the
state machine that *can* be tested without standing up an
egui context. The parts that cannot — channel-event
dispatch, `ViewportCommand` delivery, mpsc back-pressure —
are validated manually using the
`uncalibrated-sextant` display-mode keystrokes that the
sibling repo just landed.

## Scope

In scope:

- Extract `compute_outgoing_resize(viewport: (f32, f32),
  is_max: bool) -> (u32, u32)` from
  `maybe_send_monitors_resize`. Replace the inline math
  with a call to the helper.
- Unit-test `compute_outgoing_resize` covering:
  * Even-aligned input passes through unchanged.
  * Non-aligned widths/heights round *down* to the 8-px
    grid (matching the existing `width -= width % 8`
    behaviour, not rounding to nearest).
  * Sub-8 viewport dimensions clamp to the 8-px floor.
  * `is_max = true` removes `STATS_BAR_HEIGHT` from the
    height subtraction.
  * `is_max = false` subtracts `STATS_BAR_HEIGHT`.
  * Negative (`f32 < 0`) viewport dims clamp to zero
    (then to the 8-px floor) without panicking.
- One `round_trip_no_echo` test that composes both
  helpers to prove the dedup property: starting from "we
  just auto-fitted to (1024, 768)", the next outgoing
  computation against viewport `(1024, 768 + STATS_BAR_HEIGHT)`
  produces `(1024, 768)` and matches `last_sent_resize`,
  so no `VDAgentMonitorsConfig` would fire.
- A second `round_trip_guest_overrides` test: ryll
  *requested* `(1280, 800)` (so `last_sent_resize =
  (1280, 800)`), the guest's `SurfaceCreated` came back
  at `(1024, 768)` instead, auto-fit fires, the new
  `last_sent_resize` is seeded to `(1024, 768)`, and the
  next outgoing check against viewport
  `(1024, 768 + STATS_BAR_HEIGHT)` dedupes — proving the
  state machine recovers cleanly when the guest does not
  honour our requested size (the user's original "what if
  the guest rejects the hint?" question).
- Update the master-plan status table to mark phase 2 as
  Complete (it landed in commit `7528846f` but the table
  still says `Not started`).

Out of scope (later phases):

- Tests that drive `RyllApp::update` end-to-end with a
  fake `egui::Context` and synthesised channel events.
  Worth doing eventually as a project-wide test
  scaffold; out of scope for this surgical fix series.
- Tests of the `(channel, surface) == (0, 0)` gating on
  `pending_resize` writes. The condition is one line of
  data flow inside the channel-event dispatch and is
  validated by the UC keystroke smoke test, not by a
  unit test against `RyllApp` (which requires the
  scaffold mentioned above).
- Doc updates (phase 4).
- Resolution-change notifications (phase 5).

## Grounding — what's there today

`maybe_send_monitors_resize` lives at
[ryll/src/app.rs:1557-1588](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1557-L1588):

```rust
let viewport_size = ctx.input(|i| { … });
let is_max = ctx.input(|i| {
    i.viewport().maximized.unwrap_or(false)
        || i.viewport().fullscreen.unwrap_or(false)
});
let bar_height = if is_max { 0.0 } else { STATS_BAR_HEIGHT };
let mut width = viewport_size.x.max(0.0) as u32;
let mut height = (viewport_size.y - bar_height).max(0.0) as u32;

width -= width % 8;
height -= height % 8;
width = width.max(8);
height = height.max(8);

if self.last_sent_resize == Some((width, height)) {
    return;
}

if tx.try_send((width, height)).is_ok() {
    self.last_sent_resize = Some((width, height));
}
```

The `viewport_size` and `is_max` extraction is bound to
egui input. The arithmetic that follows — alignment, the
8-px floor, the bar-height subtraction — is pure and
testable.

`compute_auto_resize` lives at
[ryll/src/app.rs:3070-3082](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L3070-L3082)
and uses the alignment expression `((w as u32).max(8) / 8)
* 8`. For `w ≥ 0` (which is enforced by the call site's
`.max(0.0)` clamp) this is bit-for-bit equivalent to the
inline `w -= w % 8; w = w.max(8)` form in
`maybe_send_monitors_resize`. Phase 3 picks one form for
the new helper — the expression form is shorter, so use
that — and leaves the inline form in
`maybe_send_monitors_resize` to *call* the helper, so the
two paths cannot drift in future.

`STATS_BAR_HEIGHT` is at
[ryll/src/app.rs:38](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L38) and is
`20.0`. Tests should reference it by name, not duplicate
the constant.

## Design

### Helper

Place adjacent to `compute_auto_resize` (currently around
line 3070) so the resize logic stays colocated:

```rust
/// Decide what `(width, height)` to send to the guest as a
/// `VDAgentMonitorsConfig` from a given viewport size.
///
/// `viewport` is the live inner-rect size in logical
/// pixels (typically `egui::ViewportInputState::inner_rect`).
/// `is_max` is true when the viewport is maximised or
/// fullscreen — in that case we do not subtract
/// `STATS_BAR_HEIGHT` from the height because the stats
/// bar overlays inside the maximised area rather than
/// adding to it.
///
/// The result is 8-pixel aligned (rounded down, matching
/// what the SPICE display-channel mode-set machinery
/// expects) and clamped to a minimum of 8 on each axis so
/// we never send a degenerate `(0, 0)` resize during a
/// pathological viewport report.
fn compute_outgoing_resize(viewport: (f32, f32), is_max: bool) -> (u32, u32) {
    let bar_height = if is_max { 0.0 } else { STATS_BAR_HEIGHT };
    let w_raw = viewport.0.max(0.0) as u32;
    let h_raw = (viewport.1 - bar_height).max(0.0) as u32;
    let aligned_w = (w_raw.max(8) / 8) * 8;
    let aligned_h = (h_raw.max(8) / 8) * 8;
    (aligned_w, aligned_h)
}
```

### `maybe_send_monitors_resize` rewrite

Replace the inline alignment block with a call to the
helper. The egui-bound viewport extraction stays where it
is. After the change the function looks like:

```rust
fn maybe_send_monitors_resize(&mut self, ctx: &egui::Context) {
    let Some(tx) = &self.resize_tx else {
        return;
    };

    let viewport_size = ctx.input(|i| {
        i.viewport()
            .inner_rect
            .map(|rect| rect.size())
            .unwrap_or_else(|| i.screen_rect().size())
    });

    let is_max = ctx.input(|i| {
        i.viewport().maximized.unwrap_or(false) || i.viewport().fullscreen.unwrap_or(false)
    });

    let (width, height) = compute_outgoing_resize(
        (viewport_size.x, viewport_size.y),
        is_max,
    );

    if self.last_sent_resize == Some((width, height)) {
        return;
    }

    if tx.try_send((width, height)).is_ok() {
        self.last_sent_resize = Some((width, height));
    }
}
```

### Tests

All tests go inside the existing `#[cfg(test)] mod tests`
block at the bottom of `ryll/src/app.rs`, alongside the
existing `compute_auto_resize_decisions`.

```rust
#[test]
fn compute_outgoing_resize_decisions() {
    // Even-aligned input passes through unchanged
    // (height has STATS_BAR_HEIGHT subtracted first).
    assert_eq!(
        compute_outgoing_resize((1024.0, 768.0 + STATS_BAR_HEIGHT), false),
        (1024, 768),
    );

    // Non-aligned widths round DOWN to the 8 px grid
    // (matches the historical `w -= w % 8` form).
    assert_eq!(
        compute_outgoing_resize((1366.0, 768.0 + STATS_BAR_HEIGHT), false),
        (1360, 768),
    );

    // Non-aligned heights round DOWN too.
    assert_eq!(
        compute_outgoing_resize((1024.0, 770.0 + STATS_BAR_HEIGHT), false),
        (1024, 768),
    );

    // Sub-8 viewport dims clamp to the 8 px floor on each
    // axis. Use 4×4 (after bar subtraction) — both axes
    // hit the clamp.
    assert_eq!(
        compute_outgoing_resize((4.0, 4.0 + STATS_BAR_HEIGHT), false),
        (8, 8),
    );

    // Negative viewport dims (f32 < 0) clamp to zero
    // before the 8 px floor — must not panic on the
    // `as u32` conversion. f32 -> u32 is saturating in
    // Rust, but we still rely on the .max(0.0) up front.
    assert_eq!(
        compute_outgoing_resize((-100.0, -50.0), false),
        (8, 8),
    );

    // is_max = true skips the STATS_BAR_HEIGHT
    // subtraction. Same viewport, different is_max ->
    // different height.
    assert_eq!(
        compute_outgoing_resize((1024.0, 768.0), true),
        (1024, 768),
    );
    assert_eq!(
        compute_outgoing_resize((1024.0, 768.0), false),
        (1024, 768 - (STATS_BAR_HEIGHT as u32)),
    );
}

/// After auto-fitting to a fresh guest surface, the next
/// frame's outgoing-resize computation must produce the
/// same (aligned_w, aligned_h) so `last_sent_resize`
/// dedupes and we do not echo our own resize back to the
/// guest as a fresh VDAgentMonitorsConfig.
#[test]
fn round_trip_no_echo() {
    // Guest sends SurfaceCreated 1024x768. compute_auto_resize
    // returns the (w, h, aligned_w, aligned_h) we will fit
    // to and seed into last_sent_resize.
    let auto = compute_auto_resize(
        Some((1024.0, 768.0)),
        None,
        false,
        true,
    )
    .expect("auto-fit should fire on first surface");
    let (fit_w, fit_h, aligned_w, aligned_h) = auto;
    assert_eq!((fit_w as u32, fit_h as u32), (1024, 768));
    assert_eq!((aligned_w, aligned_h), (1024, 768));

    // egui then reports the new viewport inner-rect: the
    // surface size plus STATS_BAR_HEIGHT (we asked for
    // total_h = h + STATS_BAR_HEIGHT in the resize block).
    let viewport = (fit_w, fit_h + STATS_BAR_HEIGHT);
    let outgoing = compute_outgoing_resize(viewport, false);

    // The outgoing computation must match the seeded
    // last_sent_resize, so maybe_send_monitors_resize
    // dedupes and does NOT fire.
    assert_eq!(outgoing, (aligned_w, aligned_h));
}

/// If the guest answers a ryll-driven resize request with
/// a *different* size (e.g. ryll asked for 1280x800, guest
/// can only do 1024x768), auto-fit re-seeds last_sent_resize
/// to the guest's choice. The next outgoing computation
/// against the new viewport must dedupe so ryll does not
/// then ask for 1024x768 again as if it were a user-driven
/// resize.
#[test]
fn round_trip_guest_overrides_request() {
    // State at the start of the test: last_sent_resize
    // was (1280, 800) because the user dragged the window
    // to that size and we sent a VDAgentMonitorsConfig
    // accordingly. last_auto_resize is None because no
    // auto-fit has fired yet this session.
    let last_sent = Some((1280u32, 800u32));
    let last_auto: Option<(u32, u32)> = None;

    // Guest replies with a 1024x768 SurfaceCreated.
    let auto = compute_auto_resize(
        Some((1024.0, 768.0)),
        last_auto,
        false,
        true,
    )
    .expect("auto-fit should fire — surface differs from last_auto");
    let (_, _, aligned_w, aligned_h) = auto;
    assert_eq!((aligned_w, aligned_h), (1024, 768));

    // Caller seeds both last_sent_resize and
    // last_auto_resize from the auto-fit result.
    let new_last_sent = Some((aligned_w, aligned_h));
    assert_ne!(new_last_sent, last_sent, "last_sent must update — we did not request 1024x768");

    // egui reports the new viewport size. Outgoing
    // computation against it must match new_last_sent so
    // we do NOT then fire a fresh VDAgentMonitorsConfig
    // asking the guest for a size it just gave us.
    let viewport = (aligned_w as f32, aligned_h as f32 + STATS_BAR_HEIGHT);
    let outgoing = compute_outgoing_resize(viewport, false);
    assert_eq!(Some(outgoing), new_last_sent);
}
```

Three tests, all pure-function, no egui mocking, no async.
Together with the existing `compute_auto_resize_decisions`
they cover every branch of the two helpers and the one
non-obvious composition property (no echo).

### Master-plan housekeeping

Edit `docs/plans/PLAN-display-window-sizing.md`'s Execution
table to flip phase 2's status from `Not started` to
`Complete` (commit `7528846f`). The phase 3 row is also
flipped to `Complete` as part of this phase's commit.

Edit `docs/plans/index.md` if needed (the master-plan row
already says `In progress`; leave that until the whole
plan is done).

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 3a   | medium | sonnet | none      | Implement everything in this phase as described in *Design* and *Tests*. Files touched: `ryll/src/app.rs` (helper extraction + three new tests), `docs/plans/PLAN-display-window-sizing.md` (status table). Concretely: (1) add the `compute_outgoing_resize` free function next to `compute_auto_resize` with the signature, body, and doc comment from the *Design / Helper* section; (2) replace the inline alignment block in `maybe_send_monitors_resize` (lines ~1572-1579) with a single call to the new helper, leaving the egui input extraction and the `last_sent_resize` dedup in place; (3) add the three tests verbatim from *Tests* into the existing `#[cfg(test)] mod tests` block; (4) flip the phase 2 status in the master-plan Execution table from `Not started` to `Complete` and the phase 3 status to `Complete`. Run `pre-commit run --all-files` and `make test`. Single commit titled `Test resize state machine; extract outgoing-resize helper.`. Do not commit until the management session has reviewed. |

Medium effort and sonnet because every change is spelled
out by line number and the code involved is mechanical.
No worktree isolation: pure addition plus a one-block
in-place refactor that keeps behaviour identical.

## Success criteria

* `pre-commit run --all-files` passes.
* `make test` passes, with three new test entries in
  the output:
  `compute_outgoing_resize_decisions`,
  `round_trip_no_echo`,
  `round_trip_guest_overrides_request`.
* `maybe_send_monitors_resize` no longer contains the
  inline `width -= width % 8` arithmetic; it calls
  `compute_outgoing_resize` instead.
* The master plan's Execution table shows phase 2 and
  phase 3 as `Complete`.

## Review checklist

- [ ] `compute_outgoing_resize` is a free function (not a
      method on `RyllApp`) so tests can call it without
      constructing a `RyllApp`.
- [ ] The helper subtracts `STATS_BAR_HEIGHT` only when
      `!is_max`, matching pre-extraction behaviour.
- [ ] The helper uses the same alignment expression as
      `compute_auto_resize` (rounded down, 8-px floor).
- [ ] `maybe_send_monitors_resize` calls the helper and
      its dedup-and-send logic is otherwise unchanged.
- [ ] All three new tests exist and pass.
- [ ] Master-plan status table updated for phases 2 and 3.
- [ ] No edits outside `ryll/src/app.rs` and the master
      plan file.

## Follow-up

Phase 4 documents the new toggle and the always-fit
behaviour in `README.md` and `ARCHITECTURE.md`. Phase 5
adds resolution-change notifications. Neither needs the
test scaffold this phase touches.

The "test `RyllApp::update` end-to-end with a fake egui
context" gap remains. That is a project-wide concern, not
a window-sizing one — if it ever lands, the existing
helper tests stay valid and the round-trip tests can be
collapsed into the higher-level scenario tests.
