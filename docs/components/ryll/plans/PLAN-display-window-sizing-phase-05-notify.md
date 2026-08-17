# Phase 5 — resolution-change notifications

Parent: [PLAN-display-window-sizing.md](/components/ryll/plans/PLAN-display-window-sizing/).

## Goal

Surface guest-driven resolution changes through the
existing notifications channel
(`ryll/src/notifications.rs`). The motivation, from the
master plan: when uncalibrated-sextant cycles through
`640×480 → 800×600 → 1024×768` during boot the journey is
informative ("the test actually walked through various
modes"), but it happens too quickly to read off the screen
and we have no record afterwards. A short debounce keeps
the chattiness bounded when a user drags the ryll window
through many sizes (each drag that aligns to a fresh
8-pixel bucket can round-trip through the guest and
produce a `SURFACE_CREATE`).

## Scope

> **Implementation note (post-merge):** the two pending-
> notification fields below were collapsed into a single
> `pending_resolution_notify: Option<((u32, u32), Instant)>`
> during PR review (the value and its timestamp are always
> set and cleared together, so one Option removes the
> representable-but-invalid state where one is Some and
> the other is None). The shipped state is two fields
> total, not three. The historic three-field design is
> preserved below for the design record.

In scope:

- Three new fields on `RyllApp` for the debounce state:
  `pending_resolution_notify: Option<(u32, u32)>`,
  `pending_resolution_notify_at: Option<Instant>`, and
  `last_notified_resolution: Option<(u32, u32)>`.
- A `RESOLUTION_NOTIFY_DEBOUNCE: Duration = Duration::from_millis(500)`
  module-level constant near `STATS_BAR_HEIGHT`.
- A pure helper
  `resolution_notification_due(pending, pending_at,
  last_notified, now, debounce) -> Option<(u32, u32)>`
  next to `compute_auto_resize` /
  `compute_outgoing_resize`. Returns the resolution to
  emit (and consume) when the debounce window has
  elapsed and the target differs from the last-emitted
  value; returns None to leave state alone.
- Trigger sites: alongside the two existing
  `pending_resize` writes (the `SurfaceCreated` arm and
  the `ImageReady` auto-create branch). Both already
  short-circuit on `(display_channel_id == 0,
  surface_id == 0)`, so we colocate the new state writes
  inside the same `if` blocks.
- Drain site: in `RyllApp::update`, immediately after the
  existing auto-resize block. Calls the helper, emits
  the notification on Some, clears pending state,
  updates `last_notified_resolution`. Also requests an
  egui repaint when the deadline has not yet elapsed
  so the notification fires snappily even when no other
  events are arriving.
- Reset of all three fields in `RyllApp::reconnect`
  alongside the existing `pending_resize` /
  `last_auto_resize` resets — a fresh session should
  re-notify on its first surface.
- Three unit tests for the helper covering the four
  interesting cases: nothing pending, pending but inside
  debounce window, pending past the window, pending past
  the window but matching last_notified.
- README Features bullet: extend the existing
  Window auto-fit entry with a sentence about the
  notification, or add a separate one-liner — the phase
  plan picks the cleaner shape after seeing the diff.
- ARCHITECTURE.md Window sizing section: extend with a
  short paragraph on the notification.
- Master plan and `docs/plans/index.md` housekeeping:
  flip phase 5 to `Complete` and the index row to
  `Complete`.

Out of scope (future work):

- The richer "Display resolution: 640×480 → 1024×768
  (after 3 mode changes in 0.4s)" message shape. The
  debounce coalesces transit info; surfacing it would
  need a counter and would push wording into territory
  better suited to a follow-up. Single-target message
  is enough for now.
- Tests that drive `RyllApp` end-to-end with synthesised
  `SurfaceCreated` events (out of scope across the whole
  master plan — see phase 3).
- Notification on a *secondary*-monitor surface change.
  Same `(0, 0)` gating as `pending_resize`; multi-monitor
  treatment is master-plan future work.

## Grounding — what's there today

The notification system at
[ryll/src/notifications.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/notifications.rs)
provides `NotificationStore::push` with built-in dedup
across a 30-second window for matching
`(source, severity, message, visibility)` tuples
(`NOTIFICATION_DEDUP_WINDOW`). Same-message repeats fold
into the existing entry with a `count++`. Different
messages — including `"Display resolution: 1024x768"` vs
`"Display resolution: 800x600"` — do not fold; that is
why a debounce is needed in addition to the existing
dedup.

`RyllApp::push_notification` at
[ryll/src/app.rs:1436-1446](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1436-L1446)
is the standard entry point. Takes `&self` (no `&mut`),
locks the store mutex, calls `push`. Existing call sites
use `NotifySeverity::Info` + `NotificationSource::Internal`
for app-internal events (e.g. screenshot saved at
[app.rs:1647](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1647)).

The two `pending_resize` write sites already short-circuit
on the primary surface key:

- `SurfaceCreated` arm at
  [app.rs:799-807](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L799-L807) —
  inside `if display_channel_id == 0 && surface_id == 0`.
- `ImageReady` auto-create at
  [app.rs:835-844](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L835-L844) —
  same guard.

The existing auto-resize block in `RyllApp::update` lives
at
[app.rs:1689-1707](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1689-L1707).
The new drain block goes immediately after it, before
the call to `maybe_send_monitors_resize`.

`reconnect` at
[app.rs:646-700](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L646-L700) already
resets `pending_resize` and `last_auto_resize`. The new
fields slot into the same block.

The repaint hint pattern is already used at
[app.rs:3043](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L3043):
`ctx.request_repaint_after(Duration::from_secs(1))` as
the steady-state fallback. We add a tighter
`request_repaint_after` for the debounce deadline.

`Instant` is already in scope at the top of
`ryll/src/app.rs` via `std::time::Instant`. `Duration`
is already imported.

## Design

### State

Add adjacent to `obey_guest_size` on `RyllApp`:

```rust
// Resolution-change notification state. The debounce
// coalesces a burst of guest-side mode changes (boot
// probes, drag-resize storms) into a single user-visible
// notification per quiescent window so the panel does
// not spam.
//
// pending_resolution_notify is the latest (w, h) we have
// seen on a primary SurfaceCreated; pending_resolution_notify_at
// is the timestamp of that observation. RyllApp::update
// emits when (now - pending_resolution_notify_at) >=
// RESOLUTION_NOTIFY_DEBOUNCE and the value differs from
// last_notified_resolution. last_notified_resolution
// suppresses re-emitting the same value (e.g. when the
// guest re-confirms an existing mode after a fullscreen
// toggle).
pending_resolution_notify: Option<(u32, u32)>,
pending_resolution_notify_at: Option<Instant>,
last_notified_resolution: Option<(u32, u32)>,
```

Initialise all three to `None` in `RyllApp::new` (alongside
`obey_guest_size`). Reset all three to `None` in
`RyllApp::reconnect` (alongside `pending_resize` /
`last_auto_resize`).

### Constant

Near `STATS_BAR_HEIGHT` at
[app.rs:38](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L38):

```rust
/// Debounce window for resolution-change notifications.
/// A burst of SurfaceCreated events within this window
/// (boot mode probes, drag-resize storms) collapses to a
/// single notification carrying the latest resolution.
const RESOLUTION_NOTIFY_DEBOUNCE: Duration = Duration::from_millis(500);
```

### Helper

Adjacent to `compute_outgoing_resize`:

```rust
/// Decide whether the pending resolution-change
/// notification has been quiet long enough to emit, and
/// what value to emit.
///
/// Returns Some((w, h)) when:
/// * a value is pending,
/// * at least `debounce` has elapsed since the value
///   was queued, and
/// * the value differs from `last_notified` (so we do
///   not re-emit a confirmation of the existing mode).
///
/// Returns None to leave the pending state in place
/// (still inside the debounce window) or to drop it
/// silently (matches last_notified — the caller should
/// also clear the pending fields in that case; see the
/// call site).
///
/// Pure for unit-testability — `now` is injected.
fn resolution_notification_due(
    pending: Option<(u32, u32)>,
    pending_at: Option<Instant>,
    last_notified: Option<(u32, u32)>,
    now: Instant,
    debounce: Duration,
) -> Option<(u32, u32)> {
    let target = pending?;
    let queued_at = pending_at?;
    if now.saturating_duration_since(queued_at) < debounce {
        return None;
    }
    if last_notified == Some(target) {
        return None;
    }
    Some(target)
}
```

### Trigger sites

Inside the `(0, 0)` guard in the `SurfaceCreated` arm
(currently sets `pending_resize`), append:

```rust
self.pending_resolution_notify = Some((width, height));
self.pending_resolution_notify_at = Some(Instant::now());
```

Inside the matching guard in the `ImageReady` auto-create
branch (currently sets `pending_resize` from
`(surf_w, surf_h)`), append the same two lines using
`(surf_w, surf_h)`.

The `last_notified_resolution` short-circuit is *not*
applied at the trigger site. We deliberately re-arm the
debounce timer on every primary-surface event so that a
storm of identical-then-different events
(`1024 → 800 → 1024`) emits the latest, not the first.
The duplicate-suppression happens at drain time inside
the helper.

### Drain site

In `RyllApp::update`, immediately after the existing
auto-resize block (the
`if let Some((w, h, aw, ah)) = compute_auto_resize(…)`
block at
[app.rs:1695-1706](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1695-L1706)),
add:

```rust
let now = Instant::now();
if let Some((w, h)) = resolution_notification_due(
    self.pending_resolution_notify,
    self.pending_resolution_notify_at,
    self.last_notified_resolution,
    now,
    RESOLUTION_NOTIFY_DEBOUNCE,
) {
    self.push_notification(
        NotifySeverity::Info,
        NotificationSource::Internal,
        format!("Display resolution: {}x{}", w, h),
    );
    self.last_notified_resolution = Some((w, h));
    self.pending_resolution_notify = None;
    self.pending_resolution_notify_at = None;
} else if let Some(at) = self.pending_resolution_notify_at {
    // Still inside the debounce window. Ask egui for a
    // repaint right at the deadline so the notification
    // fires promptly even if no other events arrive.
    let elapsed = now.saturating_duration_since(at);
    if elapsed < RESOLUTION_NOTIFY_DEBOUNCE {
        ctx.request_repaint_after(RESOLUTION_NOTIFY_DEBOUNCE - elapsed);
    } else {
        // Past the deadline but skipped (target matches
        // last_notified). Drop the pending state so we
        // do not keep retrying.
        self.pending_resolution_notify = None;
        self.pending_resolution_notify_at = None;
    }
}
```

The `else if … past the deadline but skipped` branch
clears the no-op pending state explicitly — without it,
the next `update()` would call the helper again with the
same skip-because-matches result and never clear the
fields.

### Tests

Three pure-function tests inside the existing
`#[cfg(test)] mod tests` block:

```rust
#[test]
fn resolution_notification_due_nothing_pending() {
    let now = Instant::now();
    assert_eq!(
        resolution_notification_due(
            None,
            None,
            None,
            now,
            Duration::from_millis(500),
        ),
        None,
    );
}

#[test]
fn resolution_notification_due_inside_debounce() {
    let now = Instant::now();
    let queued_at = now - Duration::from_millis(100);
    assert_eq!(
        resolution_notification_due(
            Some((1024, 768)),
            Some(queued_at),
            None,
            now,
            Duration::from_millis(500),
        ),
        None,
        "100 ms < 500 ms debounce — must not fire",
    );
}

#[test]
fn resolution_notification_due_past_window_emits() {
    let now = Instant::now();
    let queued_at = now - Duration::from_millis(600);
    assert_eq!(
        resolution_notification_due(
            Some((1024, 768)),
            Some(queued_at),
            None,
            now,
            Duration::from_millis(500),
        ),
        Some((1024, 768)),
    );
}

#[test]
fn resolution_notification_due_past_window_dedupes() {
    // Pending value matches last_notified — caller
    // should suppress so we do not announce the same
    // resolution twice in a row.
    let now = Instant::now();
    let queued_at = now - Duration::from_millis(600);
    assert_eq!(
        resolution_notification_due(
            Some((1024, 768)),
            Some(queued_at),
            Some((1024, 768)),
            now,
            Duration::from_millis(500),
        ),
        None,
    );
}
```

Four tests in fact — the master plan said three but the
fourth case (past window + matches last_notified) is
distinct and worth covering. End-to-end "drive a
SurfaceCreated, observe a notification" tests are out of
scope; they need the same `RyllApp` test scaffold that
phase 3 punted on, validated by the UC keystroke smoke
test instead.

### README

Extend the existing Window auto-fit Features bullet with
one trailing sentence:

```markdown
Resolution changes are also surfaced as Info notifications
("Display resolution: WxH"), debounced over 500 ms so a
boot-time mode probe storm or a drag-resize through many
sizes collapses to a single entry rather than spamming
the panel.
```

### ARCHITECTURE.md

Extend the Window sizing section with a short paragraph
after the existing "auto-fit can be turned off" paragraph:

```markdown
Every primary-surface mode change is also surfaced as an
Info notification ("Display resolution: WxH") through the
existing notification panel, debounced by
`RESOLUTION_NOTIFY_DEBOUNCE` (500 ms) so a burst of
events — boot probes that step `640×480 → 800×600 →
1024×768` over a second, or a drag-resize that steps
through dozens of 8-pixel-aligned sizes — collapses to a
single entry carrying the latest resolution. The
debounce is on top of the 30-second
`NOTIFICATION_DEDUP_WINDOW` from
`ryll/src/notifications.rs`, which folds same-resolution
repeats into a `count++` on the existing entry. The
decision is in the pure
`resolution_notification_due` helper next to the
window-fit helpers, and is unit-tested alongside them.
```

### Master plan and index housekeeping

- `docs/plans/PLAN-display-window-sizing.md`: flip phase
  5's status to `Complete`.
- `docs/plans/index.md`: flip the master-plan row's
  status from `In progress` to `Complete`.

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 5a   | medium | sonnet | none      | Implement everything in this phase as described in *Design* and *Tests*. Files: `ryll/src/app.rs` (state, constant, helper, trigger sites, drain site, reset, four tests), `README.md` (extend the existing Window auto-fit bullet with the trailing sentence from *Design / README*), `ARCHITECTURE.md` (append paragraph to the existing Window sizing section per *Design / ARCHITECTURE.md*), `docs/plans/PLAN-display-window-sizing.md` (flip phase 5 to Complete in the Execution table), `docs/plans/index.md` (flip the master-plan row's status from `In progress` to `Complete`). Use the wording from *Design* verbatim — adjust only formatting (line wrapping, brace style) to match the surrounding code/markdown style. Run `pre-commit run --all-files` and `make test`. Single commit titled `Notify on guest-driven resolution change.`. Do not commit until the management session has reviewed. |

Medium effort and sonnet because every diff is spelled
out and the helper shape mirrors the two existing pure
helpers. Opus would not add value. No worktree
isolation: pure additions plus small in-place edits in
two markdown files.

## Success criteria

* `pre-commit run --all-files` passes.
* `make test` passes, with four new test entries in the
  output:
  `resolution_notification_due_nothing_pending`,
  `resolution_notification_due_inside_debounce`,
  `resolution_notification_due_past_window_emits`,
  `resolution_notification_due_past_window_dedupes`.
* When the UC display-mode keystrokes drive a sequence of
  mode changes, ryll's notification panel shows one
  entry per quiescent window with the latest resolution
  in the message.
* Drag-resizing the ryll window through many sizes
  produces at most ~2 notifications/second (one per
  debounce window), not one per drag step.
* The master plan and the index both show every phase
  Complete.

## Review checklist

- [ ] Three new fields on `RyllApp` initialised in
      `new` and reset in `reconnect`.
- [ ] `RESOLUTION_NOTIFY_DEBOUNCE` constant defined
      near `STATS_BAR_HEIGHT`.
- [ ] `resolution_notification_due` is a free function
      next to `compute_auto_resize` /
      `compute_outgoing_resize`.
- [ ] Trigger sites set `pending_resolution_notify`
      and `pending_resolution_notify_at` *inside* the
      existing `(0, 0)` guards, not outside them.
- [ ] Drain site clears the pending fields both on
      successful emit and on the past-window-but-skipped
      branch.
- [ ] Drain site requests a repaint at the debounce
      deadline when still inside the window.
- [ ] Four new tests exist and pass.
- [ ] README Window auto-fit bullet has the trailing
      notification sentence.
- [ ] ARCHITECTURE Window sizing section has the
      notification paragraph.
- [ ] Master plan phase 5 → Complete; index row →
      Complete.

## Hostile-server bound (post-merge)

The pre-push security review pointed out that without an
upper bound on the announced surface dimensions, a
hostile (or buggy) SPICE server can send
`SurfaceCreated { width: u32::MAX, height: u32::MAX }`,
which the auto-fit arms would forward as
`ViewportCommand::InnerSize` and quote verbatim in the
notification string. Mitigation: `MAX_AUTO_FIT_DIMENSION
= 16384` (`GL_MAX_TEXTURE_SIZE` on common hardware),
checked at both trigger sites via the
`auto_fit_size_acceptable` helper. Oversized surfaces
log a `warn!` and skip both the auto-fit and the
notification; the SPICE renderer's own surface
bookkeeping is untouched (this was a pre-existing
concern at the renderer layer, out of scope for the
display-window-sizing master plan).

## Open question — resolved

The master plan asked: do we suppress the very first
`SurfaceCreated` of a session (it is "initial connection",
not a "change") or notify anyway as a "you are connected
at WxH" confirmation?

**Decision: notify.** `last_notified_resolution` starts as
`None`, so the first primary `SurfaceCreated` after
connect (or reconnect) emits a notification. Rationale:

- Operator visibility — for the QEMU-pretending-to-be-an-
  uncalibrated-sextant case the very first `640×480`
  observation is itself useful (it confirms the mode we
  came up in, before the boot mode-walk starts).
- No extra state needed — suppressing the first surface
  would mean either seeding `last_notified_resolution`
  before the trigger fires (fragile: requires another
  hook on connect) or carrying a "have we ever notified
  this session" flag on `RyllApp`.
- Debounce already bounds chattiness — one notification
  per quiescent window, so the worst case for a chatty
  guest is a single extra "connected at WxH" line in
  the notification panel.

Reconnect resets the three fields, so a fresh session
re-notifies on its first surface, which matches the
operator-visibility argument.

## Follow-up

This is the last phase of `PLAN-display-window-sizing.md`.
The natural next pieces of display-mode UI work — scaled
/ letterboxed rendering when the window cannot match the
surface, and a per-monitor treatment for multi-monitor
setups — are listed under *Future work* in the master
plan. Neither is in scope here.
