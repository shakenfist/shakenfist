# Phase 1: Reuse survey and shared draw dispatch

Master plan: [PLAN-app-split.md](/components/ryll/plans/PLAN-app-split/).

## Planning effort

Planned at medium effort; the survey was the expensive part and
is recorded below. Step 1b should be reviewed at high effort: it
touches the GUI's hot path, and a texture that stops refreshing is
easy to miss in tests and obvious to a user.

## Scope

In:

* Make `SurfaceMirror::apply_event` the single draw-op dispatch for
  all three modes, by having it report what it did, so the GUI can
  hang its GUI-only reactions (auto-fit, resolution notification,
  frame statistics) off that report instead of re-implementing the
  dispatch.
* Replace the GUI's `HashMap<(u8, u32), GuiSurface>` with a
  `SurfaceMirror` plus a separate egui texture cache.
* Answer the master plan's open questions 1 and 2.

Out:

* Any other movement of code out of `app.rs` (phases 2-4).
* The `frames_received` divergence between GUI and headless (see
  finding 4): recorded as an issue, not fixed here.

## What the survey found

1. **The draw dispatch is duplicated, and the copies have already
   diverged.** `SurfaceMirror::apply_event`
   (`shakenfist-spice-renderer/src/surface_mirror.rs`) and the
   display arms of `RyllApp::process_events` (`ryll/src/app.rs`)
   handle the same eight events -- `SurfaceCreated`,
   `SurfaceDestroyed`, `ImageReady`, `ImageReadyChroma`,
   `ImageReadyAlpha`, `FillRect`, `CopyBits`, `Invert` -- with the
   same `DisplaySurface` calls. The mirror serves web mode and the
   headless control socket (`ryll/src/main.rs`, the
   `SurfaceMirror::new()` passed to the session, and
   `shakenfist-spice-renderer/src/session.rs`, which feeds
   every event through `apply_event`), so headless and web already
   share one dispatch and the GUI is the odd one out.
2. **A bug the divergence caused.** The `ImageReady` auto-create
   path computes the new surface's size as `left + width` and
   `top + height` in the GUI, but with `saturating_add` in the
   mirror. `[profile.dev]` in the root `Cargo.toml` leaves
   overflow checks on, so a server sending an `ImageReady` for an
   unknown surface with `left + width > u32::MAX` panics a debug
   GUI build; a release build wraps to a tiny surface instead.
   Sharing the dispatch fixes it by construction. Recorded under
   *Bugs fixed during this work* in the master plan.
3. **The GUI-only reactions are small and separable.** Beyond the
   shared calls, the GUI arms do four things: log creation,
   destruction, auto-creation and draws to unknown surfaces; queue
   an auto-fit resize and a resolution notification when the
   primary surface `(0, 0)` is created or auto-created
   (`is_primary_surface`, `auto_fit_size_acceptable`); and count
   `stats.frames_received` for every draw that lands on a known
   surface. All four can be driven by a report of what
   `apply_event` did.
4. **`frames_received` means different things per mode.** The GUI
   counts all six draw events that land; headless
   (`HeadlessStats` in `session.rs`) counts `ImageReady` only.
   Out of scope here; an issue is filed in step 1c.
5. **`GuiSurface` is only a texture cache.** `ryll/src/display_gui.rs`
   wraps a `DisplaySurface` to add a lazily allocated
   `TextureHandle` refreshed via `consume_dirty()`. The GUI owns its
   own surfaces, so nothing else consumes their dirty bits, and the
   cache can be keyed by `(display_channel_id, surface_id)` beside a
   mirror rather than wrapping each surface.
6. **Open question 2: the reconnect state machine stays in ryll.**
   `docs/multi-mode-parity.md` records SPICE reconnect as GUI-only:
   headless exits on main-channel disconnect, and web mode
   reconnects only the WebRTC peer while holding the SPICE session.
   No other mode needs `ReconnectState`, so phase 2 moves it to
   `ryll/src/app/reconnect.rs` as planned.
7. **No other duplication worth sharing.** `HeadlessStats` and the
   GUI's `Statistics` overlap in three counters, but the GUI's
   carries FPS and latency state headless does not track; the
   resize helpers, `NotificationSnapshotStore`, and the bug-report
   flow are GUI-only. Nothing else in `app.rs` has a second copy.

The master plan's claims about phase 1 held. Its open questions 1
and 2 are answered by decisions 1 and 5 below; this commit updates
the master plan's text to say so.

## Decisions

1. **The GUI holds a `SurfaceMirror`; textures live beside it.**
   `RyllApp::surfaces` becomes `surfaces: SurfaceMirror`, and
   `GuiSurface` is replaced by a `TextureCache` in
   `display_gui.rs`: a `HashMap<(u8, u32), TextureHandle>` with a
   `texture(&mut self, ctx, key, &mut DisplaySurface) ->
   &TextureHandle` method that keeps today's lazy-allocate and
   refresh-on-dirty behaviour, and a `remove(key)` / `clear()`.
   This keeps the renderer crate egui-free (AGENTS.md) and makes
   the GUI a consumer of the same type the other two modes use.
   The alternative -- a generic `SurfaceMirror<S>` over a
   surface-like trait -- was rejected: it adds a trait with one
   non-trivial implementation, which is the over-engineering the
   master plan argues against.
2. **`apply_event` returns a `DrawOutcome`.** Variants:
   `NotDisplay`; `Created { key, width, height }`;
   `AutoCreated { key, width, height }`; `Destroyed { key }`;
   `Drawn { key }`; `UnknownSurface { key }`. The type is not
   `#[must_use]`: web and headless callers legitimately ignore it,
   and the dozen test call sites would otherwise need noise. A
   `SurfaceCreated` that replaces an existing key reports `Created`,
   and the GUI drops that key's texture so the new size is
   allocated fresh.
3. **Logging moves into the mirror.** The info-level
   create/destroy/auto-create lines and the debug-level
   unknown-surface line move from `process_events` into
   `apply_event`, so web and headless gain the same diagnostics.
   Messages keep their text; the `app:` prefix becomes
   `surface_mirror:`. Surface lifecycle events are rare, so the
   extra info lines in web mode are not a volume concern.
4. **The per-blit `debug!` in the GUI's `ImageReady` arm moves
   too**, at debug level. The survey checked whether the display
   channel already logs draw ops and it does not, so dropping the
   line would lose the only per-draw trace.
5. **The reconnect state machine stays in ryll** (finding 6).
6. **`frames_received` is not unified in this phase** (finding 4).
   Unifying it changes a statistic that bug reports and the status
   bar both show, so it deserves its own decision about what the
   number should mean.

The decision most likely to be argued with is 1: deleting
`GuiSurface` rather than keeping it as a thin wrapper around a
mirror entry. Keeping it would mean the mirror stores something
other than `DisplaySurface`, which is decision 1's rejected
generic in disguise.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | opus | none | Renderer: add `DrawOutcome` and return it from `SurfaceMirror::apply_event`; move logging into the mirror. Detail below. |
| 1b | high | opus | worktree | GUI: replace the surface map with `SurfaceMirror` + `TextureCache`; collapse the eight display arms. Detail below. |
| 1c | low | sonnet | none | Docs, issue, and master plan updates. Detail below. |

### Step 1a: `DrawOutcome` in the renderer

In `shakenfist-spice-renderer/src/surface_mirror.rs`:

* Add `pub enum DrawOutcome` per decision 2, deriving `Debug`,
  `Clone`, `Copy`, `PartialEq`, `Eq`. `key` is `(u8, u32)`.
  Re-export it from the crate root beside `SurfaceMirror` (find the
  existing `pub use` in `src/lib.rs`).
* `apply_event` returns it. `ImageReady` reports `AutoCreated` when
  the entry was vacant (keep the existing `saturating_add` sizing)
  and `Drawn` otherwise. The other five draw events report `Drawn`
  or `UnknownSurface`. Unmatched events report `NotDisplay`.
* Move the log lines per decisions 3 and 4, copying their text
  from the arms of `process_events` in `ryll/src/app.rs`.
* Rewrite the doc comments that say the mirror "mirrors
  `ryll/src/app.rs::process_events`" (module comment,
  `apply_event`, the `ImageReady` arm) to say it is the single
  dispatch all modes use.
* Extend the existing `#[cfg(test)]` module with one test per
  outcome variant, plus one for the overflow case: an `ImageReady`
  on an unknown surface with `left = u32::MAX - 1, width = 4`
  must not panic and must report `AutoCreated`.
* Existing callers (`session.rs`, `main.rs`, `ryll/src/web/`,
  `encoder/frame_source.rs` tests) ignore the return value and
  need no change; confirm they still build.

Commit: "Report what SurfaceMirror::apply_event did."

### Step 1b: the GUI consumes the mirror

In `ryll/src/display_gui.rs`, replace `GuiSurface` with
`TextureCache` per decision 1. Keep the existing texture options
(`Nearest` magnification, `Linear` minification) and the
`surface_{id}` texture name.

In `ryll/src/app.rs`:

* `surfaces: HashMap<(u8, u32), GuiSurface>` becomes
  `surfaces: SurfaceMirror` plus `textures: TextureCache`. Both
  `self.surfaces.clear()` sites (in `reconnect` and
  `handle_critical_disconnect`) clear both.
* The eight display arms in `process_events` collapse to one arm
  that calls `self.surfaces.apply_event(&event)` and matches the
  outcome:
  * `Created` / `AutoCreated` with `is_primary_surface(key)`: the
    existing `auto_fit_size_acceptable` check, then
    `pending_resize` and `pending_resolution_notify`, or the
    existing oversized-surface `warn!`. Both also drop the key's
    texture.
  * `AutoCreated` and `Drawn`: `stats.frames_received += 1`.
  * `Destroyed`: drop the key's texture.
  * `UnknownSurface`, `NotDisplay`: nothing.
  `process_events` currently matches on the event by value in
  places; the new arm must borrow, because `apply_event` takes
  `&ChannelEvent`. The lag-recording `match &event` at the top of
  the loop is unchanged.
* The remaining `self.surfaces` users (the bug-report capture, the
  screenshot functions, the central panel draw and the
  primary-surface lookup for region selection) read
  `self.surfaces.surfaces` or use `primary_key()` /
  `primary_surface()`. Where a site needs a texture, call
  `self.textures.texture(ctx, key, surface)`; borrow `surfaces` and
  `textures` as separate fields so the borrow checker accepts it.
  Note that `primary_key()` falls back to any surface when `(0, 0)`
  is absent; where the GUI today looks up `(0, 0)` exactly, keep
  exact lookup rather than silently adopting the fallback.
* No change to `is_primary_surface`, `auto_fit_size_acceptable`, or
  their tests.

Verify with `make lint` and `make test` (both run in the
devcontainer), and by running the GUI against a guest: the display
paints, resizing the guest resolution refits the window and raises
one resolution notification, and F8 screenshots still work.

Commit: "Use SurfaceMirror for the GUI's draw dispatch."

### Step 1c: documentation and follow-ups

* `docs/rendering-pipeline.md`: the code sketch near the top
  iterates `self.surfaces` and calls `surface.texture(...)`; update
  it to the mirror plus texture cache, and say that all three
  modes apply draw ops through `SurfaceMirror::apply_event`.
* File a GitHub issue: "frames_received counts different events
  in GUI and headless mode", citing finding 4, and add it to the
  master plan's close-out notes.
* Master plan: mark open questions 1 and 2 answered, pointing at
  this phase's decisions 1 and 5, and add the overflow bug from
  finding 2 to the close-out notes.

Commit: "Document the shared draw dispatch."

## Risks and mitigations

* **Textures stop refreshing.** The dirty bit is now consumed via
  the cache rather than inside `GuiSurface`. Mitigation: the
  management session reads `TextureCache::texture` against the old
  `GuiSurface::texture` line by line, and the operator runs the GUI
  against a guest before the PR is opened.
* **A replaced surface keeps a stale-sized texture.** Mitigation:
  decision 2's rule that `Created` drops the texture; the reviewer
  checks the `Created` arm does so.
* **Primary-surface fallback changes behaviour.** Mitigation: the
  step 1b brief requires exact `(0, 0)` lookup where the GUI uses
  it today; the reviewer greps the diff for `primary_key` and
  checks each use.

## Definition of done

* `grep -n 'GuiSurface' -r ryll/src` finds nothing.
* `grep -n 'blit\|fill_rect\|copy_bits\|invert_rect' ryll/src/app.rs`
  finds nothing: no draw-op call remains in the GUI.
* `grep -rn 'mirrors.*process_events' shakenfist-spice-renderer/src`
  finds nothing.
* The overflow test from step 1a exists and passes.
* `make lint` and `make test` pass, and `pre-commit run
  --all-files` passes.
* The GUI paints, refits and screenshots against a live guest
  (operator check).
* The `frames_received` issue exists and is linked from the master
  plan.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
