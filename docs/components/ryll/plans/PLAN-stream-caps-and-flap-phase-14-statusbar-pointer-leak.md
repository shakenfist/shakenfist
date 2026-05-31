# Phase 14 — Stop status-bar pointer events leaking into the guest

**Status:** Not started.

**Driven by:** Session 004f, where the operator reported:

> There are definitely misclicks in this video because clicks on the
> volume control widget in the status bar appear to be registering as
> clicks _inside_ the guest as well.

## Bug

In `ryll/src/app.rs` the SPICE display surface is drawn as an
`egui::Image` with `Sense::click_and_drag()`, and the resulting
`response` is interrogated to forward mouse events to the SPICE
inputs channel. Two paths exist (lines ~3814-3885):

1. **Motion path (correct).** Uses `response.hover_pos()`, which
   returns `Some` only when the pointer is over the image rect.
   So mouse movement over the status bar is correctly ignored.

2. **Button + scroll path (buggy).** Uses
   `ctx.input(|i| i.pointer.button_pressed(button))` and
   `i.smooth_scroll_delta`. These are **window-wide** — they
   fire when the user clicks *anywhere* in the egui window,
   including the status-bar volume slider, the mute button,
   reconnect indicator, USB-device label, FPS label, etc.
   When such a click happens, the code forwards a
   `MouseDown` / `MouseUp` at `self.last_mouse_pos` — the
   last image-relative coordinate the pointer was over.

   Net effect: every status-bar click that occurs after the
   pointer has been over the SPICE surface fires a phantom
   click into the guest at whatever pixel the cursor last
   touched on the image. The user sees it as clicks landing
   "inside the guest as well" when adjusting volume, dismissing
   notifications, or interacting with anything else in the
   status bar.

## Fix shape

Gate the button-press / button-release / scroll forwarding on
whether the pointer is currently over the SPICE surface, using
the existing `response` (not the global `ctx.input`).

The candidate primitives in egui 0.34:

- `response.contains_pointer()` — true iff the pointer is over
  the response's rect this frame. Cheapest gate.
- `response.is_pointer_button_down_on()` — true iff a button
  was pressed *on the response* and is still down. Tracks
  drags through the status bar correctly (a drag started on
  the image stays attributed to the image even if the
  pointer wanders out).
- `response.clicked_by(button)` / `response.dragged_by(button)`
  — edge events scoped to the response.

Recommended: keep the existing `ctx.input(...)` block, but
wrap it in `if response.contains_pointer() { ... }`. Reason:
it preserves the existing semantics for the relative-mouse-mode
delta path and keeps the diff minimal; the only behavioural
change is that presses/releases/scroll-wheel events whose
*press moment* was outside the image rect are dropped. This
matches what spice-gtk does — input forwarding is bound to
the surface widget, not the chrome.

Edge case worth verifying: **drag-out-then-release.** If the
user presses inside the image, drags the cursor out over the
status bar, and releases there, today the `button_released`
fires regardless of position. With `contains_pointer()` we'd
drop the release and the guest's button stays "stuck down".
The fix: track button-pressed state in the existing
`self.forwarded_buttons` mask (which already exists for the
input-suppressed path at line ~3887), and forward the release
unconditionally if the corresponding bit is set. Use
`response.is_pointer_button_down_on(button)` to know whether
the press originated on the image, then forward press only
when `contains_pointer` AND not previously down, and forward
release whenever `forwarded_buttons & bit != 0` and the
underlying button is now released — regardless of pointer
position. That symmetric pattern is what spice-gtk does
(`spice-widget.c::motion-notify-event` etc.).

Scroll wheel: gate `smooth_scroll_delta` on
`response.contains_pointer()`. There's no "started on the
image" notion for scroll events — they're discrete — so the
simple gate is correct.

## Verification

Manual smoke test against any guest:

1. Open a terminal in the guest, position it under the
   client-side volume widget (so a phantom click would land
   in the terminal and be visible).
2. Click the mute button. Confirm the guest terminal does
   NOT receive a click.
3. Drag the volume slider. Confirm the guest does NOT see
   button-down / motion / button-up.
4. Click the FPS label, the reconnect indicator (force a
   reconnect first), the USB-device label, the bug-report
   "Save bug report" button. Confirm none of them leak.
5. Verify the existing image-area behaviour is intact:
   - Click inside the image — guest sees the click.
   - Drag-and-drop inside the image — guest sees the drag.
   - Drag out of the image and release — guest sees the
     button-up (not a stuck button).
   - Scroll wheel over the image — guest sees the scroll.
   - Scroll wheel over the status bar — guest does NOT
     see the scroll.

Unit-test surface is thin (the code is glue between
egui::Response and the SPICE input channel — hard to test
without standing up egui). A regression test that the
existing `input_suppressed` path still forwards button-up
for already-pressed buttons (lines ~3887-3899) is worth
keeping; do not delete that block, just make sure the
new gating path uses the same `forwarded_buttons` mask
for the stuck-button defence.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 14A  | low    | sonnet | none     | Edit `ryll/src/app.rs` around lines 3844-3885: gate the button-pressed and scroll-wheel forwarding on `response.contains_pointer()`. Preserve the symmetric button-released forwarding (release whenever `forwarded_buttons & mask != 0`, regardless of pointer position) so drag-out-then-release doesn't leave a stuck button in the guest. Run `pre-commit run --all-files` and `make test`. Commit per project conventions. |
| 14B  | low    | n/a   | none      | Operator smoke test per the "Verification" section above. Capture an auto-snapshot bundle if any phantom click is still observed; otherwise just confirm in chat. |

## Out of scope

- Refactoring the input forwarding architecture. The current
  shape (one big inline block in `update()`) is fine for the
  scope.
- Keyboard event gating. egui already routes keyboard events
  through focus, so the status-bar widgets don't steal key
  presses; and if they did, it would be a separate phase.
- Touch events. Not exercised by current ryll users; not
  covered by 004's bug report.

## Cross-references

- `ryll/src/app.rs:3814-3899` — input forwarding block.
- Session 004f notes in
  `private:ryll-test-sessions/manual-test-instructions/004-notes.md`
  for the operator's original report.
- spice-gtk `src/spice-widget.c::motion_notify_event` —
  reference for surface-scoped input forwarding semantics.
