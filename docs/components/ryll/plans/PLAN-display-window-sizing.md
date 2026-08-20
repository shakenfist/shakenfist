# Window-follows-guest display sizing

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section.

I prefer one commit per logical change, and at minimum one
commit per phase.

## Situation

Bug report `ryll-bugreport-2026-04-30T04-29-47Z.zip` (filed
against shakenfist/uncalibrated-sextant) shows the symptom:
the ryll window is sometimes ~20% wider than the guest
surface, sometimes ~20% narrower. The session JSON in the
report shows surface 0 at 1024×768 with `report_type:
Display`, description `"Screen too small?"`. The bundled
screenshot is the surface buffer (1024×768) — it is
correctly sized, because the screenshot path captures
surface pixels, not the window. So the visible
window-vs-surface mismatch is invisible inside the report
itself; we have to infer it from the description and from
the boot sequence shown in the screenshot.

uncalibrated-sextant boots through several display modes
(`640×480x8`, `800×600x16`, `1024×768x32`, then `mode locked:
1024×768x32`). Each probe is a SPICE `SURFACE_CREATE` (or an
auto-create from a draw before SURFACE_CREATE, see
`ryll/src/app.rs:823-836`) at a different size.

The bug is in `RyllApp::update` at `ryll/src/app.rs:1670-1688`:

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

The window resizes only on the *first* surface event of the
session. After that, every subsequent `SurfaceCreated` (and
every auto-create) updates `pending_resize` but the resize
branch is skipped. The surface still renders at its native
pixel size via `egui::Image::new(texture).fit_to_exact_size(size)`
at `ryll/src/app.rs:2566-2572`, so when the guest changes
resolution we end up with a surface drawn at, say, 1024×768
inside a window the user (or the very first probe) sized to
640×480 or 1280×800. That is the mismatch the report
describes.

The fix is to always honour guest surface size by default —
the same way virt-viewer does. The user remains in control
because resizing the window still sends `VDAgentMonitorsConfig`
to the guest via `maybe_send_monitors_resize`
(`ryll/src/app.rs:1539-1570`); if the guest accepts the hint,
its next `SURFACE_CREATE` confirms the new size and the dedup
in `last_sent_resize` keeps things stable. If the guest
rejects the hint and stays at its preferred size, the window
should follow the guest — that is the user's question
("perhaps I resized to a resolution the guest doesn't
support so it tries to revert the change?") and is the
reason a one-shot initial resize is the wrong policy.

A hamburger toggle ("Obey guest size hints", default **on**)
is the right escape hatch for users who deliberately want a
fixed window size while the guest cycles modes — for example,
recording a fixed-size capture, or watching a guest that
flaps between resolutions on its own. The toggle is in
addition to, not a replacement for, fixing the default
behaviour.

## Mission and problem statement

Make the ryll window track the guest surface size by default,
so a guest mode change (during boot, on user-driven resize
that the guest re-negotiates, or on guest-initiated mode
change) always leaves the window matching the surface. Add
a hamburger menu toggle so the operator can opt out when
they want a fixed window.

Concretely:

1. Remove the `initial_resize_done` one-shot gate in
   `RyllApp::update`. Honour every `pending_resize` whose
   target differs from the current viewport interior, not
   just the first.
2. Re-seed `last_sent_resize` with the (8-aligned) new
   surface dimensions on every auto-resize, so the
   round-trip with `maybe_send_monitors_resize` does not
   echo our own resize back to the guest as a fresh
   `VDAgentMonitorsConfig`.
3. Skip the auto-resize when the window is maximised or
   fullscreen, matching the existing logic in
   `maybe_send_monitors_resize` (which sets
   `bar_height = 0` in that case). In maximised/fullscreen
   we cannot meaningfully change the inner size; the
   surface should letterbox/scale within the available
   area instead. Letterboxing is out of scope for this
   plan — see Future work.
4. Add an `obey_guest_size: bool` field to `RyllApp`,
   default `true`. Guard the auto-resize on it. When
   `false`, behave like today after the initial resize:
   leave the window where it is, and let the surface
   render at native size inside it (clipped or with empty
   space).
5. Surface the toggle in the hamburger menu (`ryll/src/app.rs:1922`)
   as a `ui.checkbox(&mut self.obey_guest_size, "Obey guest size hints")`,
   placed near the other view toggles. No persistence
   across sessions in this plan — settings persistence
   is a separate concern (none exists today; see
   `ryll/src/settings.rs`).
6. Add a `--no-obey-guest-size` CLI flag in `ryll/src/config.rs`
   for users (and CI) who want to start ryll with the
   toggle already off — useful for fixed-size captures
   without having to click the menu after every launch.
7. Document the behaviour in `ARCHITECTURE.md` (under the
   existing Multi-Monitor Support section, or a new
   sibling "Window sizing" section) and in the README's
   user-facing options table.

## Open questions

1. **What does "current viewport interior" mean for the
   skip check?** The cleanest test is "do not resize if
   `(viewport.inner.width, viewport.inner.height -
   STATS_BAR_HEIGHT)` already equals
   `(surface.width, surface.height)`, modulo 8-pixel
   alignment". This avoids resizing on every frame when
   the window already matches. But egui's reported
   inner size sometimes lags by a frame after we issue
   `ViewportCommand::InnerSize`; the dedup may need a
   small tolerance, or to track "last resize we asked for"
   independently. Recommend: add a `last_auto_resize:
   Option<(u32, u32)>` field, dedup against it, and only
   issue a new resize when the surface differs from
   `last_auto_resize` (not from the live viewport
   inner-size). This mirrors how `last_sent_resize`
   already works on the outgoing side.

2. **Should the auto-resize fire on every `SurfaceCreated`,
   or only on the primary (channel 0, surface 0)?** Today
   `pending_resize` is set unconditionally for any
   surface (`app.rs:799` and `app.rs:835`), which means a
   secondary monitor's surface event could trigger a
   primary-window resize. The fix should constrain the
   trigger to the primary surface: only set
   `pending_resize` when `(display_channel_id, surface_id)
   == (0, 0)`, or — more simply — when the affected
   surface key matches the primary key the renderer uses
   at `app.rs:2553-2559`. Recommend: tie the trigger to
   the primary surface explicitly.

3. **What about scaling?** When `obey_guest_size = false`,
   the surface currently renders at native pixel size and
   either overflows or leaves empty space. virt-viewer
   has a "Scale display" mode that keeps aspect ratio
   while filling the window. That is genuinely a separate
   feature — call it out as future work and keep it out
   of this plan.

4. **HiDPI / `pixels_per_point`?** egui logical pixels are
   not necessarily physical pixels. `ViewportCommand::InnerSize`
   takes logical pixels. `surface.width × pixels_per_point`
   is what the user actually sees. We do not need to
   account for this in the sizing logic itself — we
   want the window to be exactly `surface.width` logical
   pixels wide so that the `Image::fit_to_exact_size`
   maps 1:1 — but the docs should note that on HiDPI
   displays the physical window is larger than the
   surface in pixel count, which is fine because the
   image is resampled by the GPU.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Always-fit + dedup | [PLAN-display-window-sizing-phase-01-always-fit.md](/components/ryll/plans/PLAN-display-window-sizing-phase-01-always-fit/) | Complete |
| 2. Hamburger toggle + CLI flag | [PLAN-display-window-sizing-phase-02-toggle.md](/components/ryll/plans/PLAN-display-window-sizing-phase-02-toggle/) | Complete |
| 3. Tests | [PLAN-display-window-sizing-phase-03-tests.md](/components/ryll/plans/PLAN-display-window-sizing-phase-03-tests/) | Complete |
| 4. Docs | [PLAN-display-window-sizing-phase-04-docs.md](/components/ryll/plans/PLAN-display-window-sizing-phase-04-docs/) | Complete |
| 5. Resolution-change notifications | [PLAN-display-window-sizing-phase-05-notify.md](/components/ryll/plans/PLAN-display-window-sizing-phase-05-notify/) | Complete |

Phase 1 is the bug fix proper. Phase 2 adds the escape
hatch. Phase 3 covers regression tests for the resize
state machine (the round-trip between
`maybe_send_monitors_resize` and the auto-resize on
`SurfaceCreated` is fiddly enough to deserve a unit test
that drives both with synthesised events). Phase 4 updates
ARCHITECTURE.md and README.md.

Phase 5 surfaces guest-driven resolution changes through
the existing notifications channel
(`ryll/src/notifications.rs`). The motivation is operator
visibility: when uncalibrated-sextant cycles through
`640×480 → 800×600 → 1024×768` during boot the journey is
informative ("the test actually walked through various
modes"), but it happens too quickly to read off the screen.
A short rate-limit caps the chattiness when the user
drag-resizes the ryll window through many sizes (each drag
that aligns to a fresh 8-pixel bucket can round-trip
through the guest and produce a `SURFACE_CREATE`). Sketch:

* Trigger: every primary `SurfaceCreated` (and primary
  `ImageReady` auto-create) whose `(width, height)` differs
  from the previous primary surface.
* Source: `NotificationSource::Internal` at
  `NotifySeverity::Info`.
* Message: something like
  `"Display resolution: WxH"`. Phase 5 settles the wording.
* Rate limiting: at least one of either (a) coalesce
  in-flight entries during a short debounce window
  (~500ms), so a boot-time storm collapses but distinct
  changes separated by user pauses still all surface, or
  (b) reuse the existing 30-second dedup window in
  `notifications.rs:NOTIFICATION_DEDUP_WINDOW` if the
  message string is shaped so identical resolutions fold
  naturally. The phase plan picks one and explains the
  tradeoff. Do **not** notify on user-driven resizes
  (those originate from `maybe_send_monitors_resize`, not
  from inbound `SurfaceCreated`) and do **not** notify on
  ryll's own auto-fit (it does not produce its own
  `SurfaceCreated`).
* The very first `SurfaceCreated` of a session was an
  open question — is it "initial connection", not a
  "change"? Resolved by phase 5: we **do** notify on the
  first surface, as a cheap "connected at WxH"
  confirmation that gives operators visibility into the
  starting mode (especially useful when a guest then
  walks through several modes during boot). See
  `PLAN-display-window-sizing-phase-05-notify.md`
  ("Open question — resolved") for the rationale.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never
in the management session. The management session (this
conversation) is reserved for planning, review, and
decision-making.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each phase with the brief
   from the phase plan.
3. **Review** the sub-agent's output — read the actual
   files, do not trust the summary.
4. **Fix or retry** if the output is wrong.
5. **Commit** once satisfied.

### Planning effort

The phase plans should be created at **medium effort** —
the surface area is small (one file, one feature) and the
mechanics of the fix are well understood. Each phase plan
should specify the recommended effort level for the
implementing sub-agent.

### Step-level guidance

Each phase plan should include a table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
```

For this plan most steps will be **medium effort, sonnet,
no isolation** — the change is localised to `app.rs`,
`config.rs`, and the README/ARCHITECTURE docs, and the
brief will be detailed enough that opus is overkill.

### Management session review checklist

After a sub-agent completes:

- [ ] `ryll/src/app.rs` changes match the plan: the
      `initial_resize_done` gate is gone, the auto-resize
      is guarded on `obey_guest_size`, `last_sent_resize`
      is re-seeded on every auto-resize, the
      maximised/fullscreen short-circuit is in place.
- [ ] `ryll/src/config.rs` exposes `--no-obey-guest-size`
      and threads it into `RyllApp::new`.
- [ ] The hamburger menu has the new checkbox.
- [ ] `cargo test --workspace` passes.
- [ ] `pre-commit run --all-files` passes.
- [ ] `ARCHITECTURE.md` and `README.md` describe the
      new behaviour and toggle.

## Administration and logistics

### Success criteria

We will know this plan is done when:

* Booting against uncalibrated-sextant ends with the ryll
  window matching the final guest surface size, no matter
  which intermediate mode probes the guest cycles through.
* Resizing the ryll window sends `VDAgentMonitorsConfig` and
  the window then re-syncs to whatever resolution the guest
  actually chose (which may differ from the requested
  size).
* Toggling "Obey guest size hints" off in the hamburger
  menu freezes the window at its current size; the
  surface then renders at its native size inside that
  window, with no further auto-resize.
* `--no-obey-guest-size` on the command line starts ryll
  with the toggle already off.
* `pre-commit run --all-files` and `cargo test --workspace`
  pass.
* `ARCHITECTURE.md` and `README.md` are updated.

### Future work

* **Scaled / letterboxed rendering.** When the window does
  not match the surface (toggle off, maximised, or
  fullscreen), we currently render at native pixel size
  with overflow or empty space. A scaled mode that fits
  the surface to the window while preserving aspect ratio
  would be a natural next step, and is the obvious
  expansion of the `display-mode-ui` branch beyond this
  plan.
* **Settings persistence.** Today there is no on-disk
  settings layer (`ryll/src/settings.rs` only holds CLI
  flags). When persistence lands, "Obey guest size hints"
  should be one of the persisted preferences.
* **Per-monitor handling.** Multi-monitor setups
  (`--monitors N`) have one surface per monitor. The
  current plan only auto-resizes the primary window;
  secondary monitors are out of scope here and deserve
  their own treatment.

### Bugs fixed during this work

* uncalibrated-sextant bug report
  `ryll-bugreport-2026-04-30T04-29-47Z.zip`: window
  smaller/larger than the guest surface after boot mode
  probes.

### Documentation index maintenance

When this plan is created:

* Add a row to the *Master plans* table in
  `docs/plans/index.md`.
* Add an entry to `docs/plans/order.yml`.

When all phases are complete, update the status column
in `index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
