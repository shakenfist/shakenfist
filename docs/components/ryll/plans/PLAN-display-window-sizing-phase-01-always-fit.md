# Phase 1 — always fit window to guest surface

Parent: [PLAN-display-window-sizing.md](/components/ryll/plans/PLAN-display-window-sizing/).

## Goal

Make the ryll window auto-resize to match the guest surface
on **every** `SurfaceCreated` event (and every auto-create
fallback in `ImageReady`), not just the first one. This is
the bug fix for the uncalibrated-sextant report — the user
sees the window settle at a stable size matching whatever
resolution the guest has actually chosen, regardless of how
many intermediate modes the guest probed during boot.

The hamburger toggle and the `--no-obey-guest-size` CLI
flag land in **phase 2**. Tests of the full state machine
land in **phase 3**. Doc updates land in **phase 4**. This
phase is the minimum diff needed to fix the bug, plus a
small extracted helper that phase 3 can unit-test.

## Scope

In scope:

- Replace `initial_resize_done: bool` with
  `last_auto_resize: Option<(u32, u32)>` on `RyllApp`.
  The new field stores the (8-aligned) dimensions we last
  asked the viewport for, and is the single source of truth
  for "does the window already match the surface?".
- Move the resize decision into a pure helper
  `compute_auto_resize` so phase 3 can drive it from a
  unit test without touching egui.
- Honour `pending_resize` on every `update()` call, gated
  only on:
  1. The window is not maximised/fullscreen.
  2. The (8-aligned) target differs from
     `last_auto_resize`.
- Re-seed `last_sent_resize` with the (8-aligned) target
  every time we auto-resize, so
  `maybe_send_monitors_resize` doesn't echo our own resize
  back to the guest as a fresh `VDAgentMonitorsConfig`.
- Constrain `pending_resize` triggers to the **primary
  surface** — `(display_channel_id, surface_id) == (0, 0)`.
  Today both the `SurfaceCreated` arm
  (`ryll/src/app.rs:799`) and the auto-create arm in
  `ImageReady` (`ryll/src/app.rs:835`) set
  `pending_resize` for any surface, which on multi-monitor
  setups would let a secondary surface event resize the
  primary window.
- Reset `last_auto_resize` (and clear `pending_resize`,
  which is already done) in `RyllApp::reconnect` at
  `ryll/src/app.rs:646-700`, so a reconnect to a guest
  with a different resolution re-fits the window.
- One focused unit test of `compute_auto_resize` covering
  the four interesting cases (no-pending, dedup match,
  fresh size, maximised). Broader state-machine tests
  belong to phase 3.

Out of scope (later phases):

- Hamburger toggle and CLI flag (phase 2).
- Tests that drive `RyllApp::update` end-to-end with
  synthesised channel events (phase 3).
- ARCHITECTURE.md and README.md updates (phase 4).
- Any letterboxing/scaling when the window cannot match
  the surface (future work, master plan).

## Grounding — what's there today

`RyllApp` carries the resize machinery on three fields:

- `pending_resize: Option<(f32, f32)>`
  ([app.rs:242](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L242)) — set when a
  surface event arrives, consumed in `update()`.
- `initial_resize_done: bool`
  ([app.rs:248](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L248)) — the one-shot
  gate this phase removes.
- `last_sent_resize: Option<(u32, u32)>`
  ([app.rs:203](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L203)) — last
  `(width, height)` we sent on the resize channel via
  `maybe_send_monitors_resize`. Used to dedup outgoing
  `VDAgentMonitorsConfig` messages.

`pending_resize` is set in two places — both unconditional
on the surface key today:

- `ChannelEvent::SurfaceCreated` arm
  ([app.rs:785-800](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L785-L800)) sets
  it from `(width, height)` of the new surface.
- `ChannelEvent::ImageReady` auto-create branch
  ([app.rs:823-836](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L823-L836)) sets
  it when ryll has to fabricate a surface because the
  server drew before sending `SURFACE_CREATE`.

The resize is consumed in `RyllApp::update`
([app.rs:1670-1688](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1670-L1688)):

```rust
if let Some((w, h)) = self.pending_resize.take() {
    if !self.initial_resize_done {
        let total_h = h + STATS_BAR_HEIGHT;
        ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(
            egui::vec2(w, total_h),
        ));
        // …seed last_sent_resize…
        self.initial_resize_done = true;
        info!("app: initial window resize to {}x{}", w as u32, h as u32);
    } else {
        debug!("app: surface resize {}x{} (window already sized)", w, h);
    }
}
```

`maybe_send_monitors_resize`
([app.rs:1539-1570](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1539-L1570))
runs every frame, samples the live viewport interior,
subtracts `STATS_BAR_HEIGHT` (zero when maximised or
fullscreen), 8-aligns, and sends to the guest if the
result differs from `last_sent_resize`.

`reconnect`
([app.rs:646-700](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L646-L700)) clears
`pending_resize` and `last_sent_resize` but *not*
`initial_resize_done` — meaning a reconnect today does not
re-fit the window even on the new initial surface. With
this phase's change the gate goes away, so reconnect
behaviour quietly improves alongside the main fix.

## Design

### State

Replace:

```rust
// Pending viewport resize from a new surface
pending_resize: Option<(f32, f32)>,

// Whether the window has been auto-sized to the remote
// surface yet.  …
initial_resize_done: bool,
```

with:

```rust
// Pending viewport resize from a new primary-surface event.
// Only set for (display_channel_id, surface_id) == (0, 0).
pending_resize: Option<(f32, f32)>,

// (8-aligned width, height) of the last auto-resize we
// issued. None until the first resize. Used to dedup so we
// don't re-issue ViewportCommand::InnerSize every frame
// while the window already matches the surface, and so
// reconnect can re-fit by clearing it.
last_auto_resize: Option<(u32, u32)>,
```

Initialise to `None` in `RyllApp::new` (replace the
`initial_resize_done: false` line). Reset to `None` in
`RyllApp::reconnect` alongside the existing
`pending_resize = None` reset.

### Trigger sites — primary-surface only

In `process_events` change both `pending_resize` writes to
fire only when the affected key is `(0, 0)`:

```rust
ChannelEvent::SurfaceCreated {
    display_channel_id,
    surface_id,
    width,
    height,
} => {
    info!(/* unchanged */);
    self.surfaces.insert(
        (display_channel_id, surface_id),
        DisplaySurface::new(surface_id, width, height),
    );
    if display_channel_id == 0 && surface_id == 0 {
        self.pending_resize = Some((width as f32, height as f32));
    }
}
```

```rust
// In the auto-create branch of ImageReady:
if let std::collections::hash_map::Entry::Vacant(e) =
    self.surfaces.entry((display_channel_id, surface_id))
{
    let surf_w = left + width;
    let surf_h = top + height;
    info!(/* unchanged */);
    e.insert(DisplaySurface::new(surface_id, surf_w, surf_h));
    if display_channel_id == 0 && surface_id == 0 {
        self.pending_resize = Some((surf_w as f32, surf_h as f32));
    }
}
```

The `(0, 0)` check is intentionally literal rather than
"the renderer's current primary key" — at the moment a new
surface arrives we want to react to it as the primary, not
to whatever the renderer happened to pick last frame. The
renderer's primary lookup at
[app.rs:2553-2559](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2553-L2559)
already prefers `surface_id == 0`, so the two definitions
agree.

### Decision helper

Add a free function near `maybe_send_monitors_resize`:

```rust
/// Decide whether to issue a `ViewportCommand::InnerSize`
/// to fit the remote surface, and what size to ask for.
///
/// `pending` is the surface size pulled from
/// `pending_resize` (logical pixels, f32). `last_auto`
/// is the (8-aligned) size of the last auto-resize we
/// issued, or None if we have not auto-resized yet. `is_max`
/// is true when the viewport is maximised or fullscreen
/// and we should not change the inner size.
///
/// Returns Some((width, height, aligned_w, aligned_h))
/// where `(width, height)` are the values to pass to
/// `ViewportCommand::InnerSize` (with `STATS_BAR_HEIGHT`
/// added to the height — see the call site) and
/// `(aligned_w, aligned_h)` are the values to store in
/// `last_auto_resize` and seed into `last_sent_resize`.
/// Returns None when no resize should fire.
fn compute_auto_resize(
    pending: Option<(f32, f32)>,
    last_auto: Option<(u32, u32)>,
    is_max: bool,
) -> Option<(f32, f32, u32, u32)> {
    let (w, h) = pending?;
    if is_max {
        return None;
    }
    let aligned_w = ((w as u32).max(8) / 8) * 8;
    let aligned_h = ((h as u32).max(8) / 8) * 8;
    if last_auto == Some((aligned_w, aligned_h)) {
        return None;
    }
    Some((w, h, aligned_w, aligned_h))
}
```

Notes on the alignment: `maybe_send_monitors_resize` uses
`x -= x % 8; x = x.max(8)` which yields the same result
as `((x.max(8)) / 8) * 8` for `x >= 8`. For `x < 8` the
two differ: the existing code returns 8 (subtract-mod
underflows? no — `0 - 0 = 0`, then `.max(8) = 8`; for
`x = 7`, `7 - 7 = 0`, `.max(8) = 8`); the new helper
returns 8 too because `.max(8) / 8 * 8 = 8`. They agree.
Use whichever is cleaner; I lean to the new form because
it's a single expression. Either is fine — pick one and
match it inside `maybe_send_monitors_resize` only if
trivial; do not refactor that function as part of this
phase if the change is awkward.

### Call site

In `RyllApp::update`, replace the existing block at
[app.rs:1667-1688](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1667-L1688):

```rust
// Resize viewport to match the remote surface (plus stats
// bar) whenever a new primary surface differs from the
// size we last fitted to. Maximised/fullscreen windows
// are left alone — we cannot meaningfully change their
// inner size, and the surface will render at native size
// inside the available area.
let pending = self.pending_resize.take();
let is_max = ctx.input(|i| {
    i.viewport().maximized.unwrap_or(false)
        || i.viewport().fullscreen.unwrap_or(false)
});
if let Some((w, h, aw, ah)) =
    compute_auto_resize(pending, self.last_auto_resize, is_max)
{
    let total_h = h + STATS_BAR_HEIGHT;
    ctx.send_viewport_cmd(egui::ViewportCommand::InnerSize(
        egui::vec2(w, total_h),
    ));
    // Seed last_sent_resize so maybe_send_monitors_resize
    // doesn't echo our own resize back to the guest as a
    // VDAgentMonitorsConfig change.
    self.last_sent_resize = Some((aw, ah));
    self.last_auto_resize = Some((aw, ah));
    info!(
        "app: window resize to {}x{} (surface)",
        w as u32, h as u32
    );
}
```

Two subtle behaviours flowing from this:

- When the window is maximised and a surface event
  arrives, we drop the resize on the floor (consumed by
  `take()` and discarded). The surface still renders at
  native size inside the maximised window, which means it
  may overflow or leave empty space until the user
  un-maximises. This matches the existing
  maximised/fullscreen story in
  `maybe_send_monitors_resize`.
- The previous "initial" log line goes away. Replace with
  a single log line for every successful auto-resize (per
  the snippet above). The `else` branch's
  `debug!("app: surface resize … (window already sized)")`
  also goes away; the dedup is silent. If we want to keep
  visibility in `--verbose`, add a `debug!` inside
  `compute_auto_resize`'s caller for the `None` branch.
  Skip the debug log for now to keep the diff minimal —
  phase 4's docs phase can revisit if needed.

### Reconnect

In `reconnect()` at
[app.rs:646-700](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L646-L700), the
existing `self.pending_resize = None;` stays. Add
`self.last_auto_resize = None;` next to it. Remove the
`initial_resize_done` field entirely so there is no
`self.initial_resize_done = …` to write at all.

### Constructor

In `RyllApp::new` at
[app.rs:551-643](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L551-L643), remove
`initial_resize_done: false,` and add
`last_auto_resize: None,` in the same place.

## Tests

Add one focused unit test inside the existing
`#[cfg(test)] mod tests` block at
[app.rs:3611](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L3611):

```rust
#[test]
fn compute_auto_resize_decisions() {
    // No pending event => no resize.
    assert_eq!(compute_auto_resize(None, None, false), None);

    // Pending size, never resized before, not maximised =>
    // resize to the aligned target.
    assert_eq!(
        compute_auto_resize(Some((1024.0, 768.0)), None, false),
        Some((1024.0, 768.0, 1024, 768)),
    );

    // Same target as last_auto => skip (dedup).
    assert_eq!(
        compute_auto_resize(
            Some((1024.0, 768.0)),
            Some((1024, 768)),
            false,
        ),
        None,
    );

    // Maximised => skip even when target differs.
    assert_eq!(
        compute_auto_resize(
            Some((1024.0, 768.0)),
            Some((640, 480)),
            true,
        ),
        None,
    );

    // Non-aligned size gets aligned to 8 px boundary.
    assert_eq!(
        compute_auto_resize(Some((1366.0, 770.0)), None, false),
        Some((1366.0, 770.0, 1360, 768)),
    );

    // Differs after alignment from last_auto => resize.
    assert_eq!(
        compute_auto_resize(
            Some((1280.0, 800.0)),
            Some((1024, 768)),
            false,
        ),
        Some((1280.0, 800.0, 1280, 800)),
    );
}
```

This is a pure-function test — no egui context, no async,
no channels. Phase 3 covers the round-trip with
`maybe_send_monitors_resize` and the channel-event path.

## Step-level guidance

Single step, single commit.

| Step | Effort | Model  | Isolation | Brief for sub-agent |
|------|--------|--------|-----------|---------------------|
| 1a   | medium | sonnet | none      | Implement everything in this phase as described in *Design* and *Tests*. Files: `ryll/src/app.rs` only. Concretely: (1) drop the `initial_resize_done: bool` field declaration, the `false` initialiser in `RyllApp::new`, and the `self.initial_resize_done = …` write in `update`; (2) add `last_auto_resize: Option<(u32, u32)>` next to `pending_resize` on the struct, initialise to `None` in `RyllApp::new`, reset to `None` in `RyllApp::reconnect`; (3) add the `compute_auto_resize` free function as specified — place it directly above or below `maybe_send_monitors_resize` to keep the resize logic colocated; (4) replace the resize block in `update` with the `compute_auto_resize`-driven version; (5) gate both `pending_resize` writes (in `SurfaceCreated` and the auto-create branch of `ImageReady`) on `display_channel_id == 0 && surface_id == 0`; (6) add the `compute_auto_resize_decisions` unit test inside the existing `tests` module. Do not refactor `maybe_send_monitors_resize` — alignment differences between its inline form and the helper's expression form are equivalent for the values we care about. Run `pre-commit run --all-files` and `make test` before handing back. Single commit titled `Always fit window to guest surface size.`. Do not commit until the management session has reviewed. |

Medium effort and sonnet because the surface area is one
file, the design specifies every change, and the brief
front-loads the call sites by line number. Opus would not
add value. No worktree isolation: a plain `git revert` is
trivial if something goes wrong.

## Success criteria

* `pre-commit run --all-files` passes.
* `make test` passes, including the new
  `compute_auto_resize_decisions` test.
* The `initial_resize_done` field and all references to
  it are gone (`grep -rn initial_resize_done` returns
  nothing).
* `pending_resize` is only written from primary-surface
  events.
* Booting against uncalibrated-sextant (or any guest that
  cycles modes during boot) leaves the window matching the
  final guest surface size, regardless of intermediate
  probes.

## Review checklist

- [ ] `initial_resize_done` is removed entirely (struct
      field, `RyllApp::new` initialiser, and the write in
      `update`).
- [ ] `last_auto_resize` is initialised in `RyllApp::new`
      and reset in `reconnect`.
- [ ] `compute_auto_resize` is a free function (not a
      method) and is unit-tested.
- [ ] Both `pending_resize` writes check `(0, 0)`.
- [ ] `last_sent_resize` is re-seeded with the new
      aligned dimensions on every auto-resize, not only
      the first.
- [ ] No new `info!`/`debug!` lines beyond the single
      "window resize to WxH (surface)" entry.
- [ ] No changes outside `ryll/src/app.rs`.
- [ ] Commit message follows the project's conventions
      (Co-Authored-By with model + context window + effort
      level + any active settings, Signed-off-by, Prompt:
      paragraph).

## Follow-up

Phase 2 adds the `obey_guest_size` field, the
`--no-obey-guest-size` CLI flag, and the hamburger
checkbox. The check inside `compute_auto_resize` (or the
caller) becomes `if !obey_guest_size || is_max { return
None; }` — phase 2's brief will spell out the exact
threading.
