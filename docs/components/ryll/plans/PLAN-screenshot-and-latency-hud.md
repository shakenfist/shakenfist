# Screenshot capture and latency HUD

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
and a table of protocol reference sources.

## Situation

Ryll already has all the building blocks needed for both
features in this plan; nothing new needs to be invented.

**For the screenshot feature:**

- `bugreport::encode_png(pixels, w, h)` already encodes RGBA
  pixels to PNG bytes
  ([bugreport.rs:584](https://github.com/shakenfist/ryll/blob/develop/ryll/src/bugreport.rs#L584)).
- `Surface::pixels()` returns the RGBA buffer for a display
  surface ([display/surface.rs:107](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs#L107)).
- `App.surfaces` is a map keyed by surface id; bug reports
  already pick the largest one as the screenshot source
  ([app.rs:699-706](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L699-L706)).
- `rfd::FileDialog` is already a dependency and is used
  elsewhere in the GUI ([app.rs:1378](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1378)).
- F11/F12 are already bound; F8 is free.

**For the latency HUD:**

- A `Latency: Xms` label already renders in the bottom stats
  panel from `self.stats.last_latency`
  ([app.rs:971-973](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L971-L973)).
- The latency value is updated on `ChannelEvent::Latency`
  ([app.rs:536-538](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L536-L538)) which is
  emitted from the inputs channel
  ([channels/inputs.rs:343-345](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/inputs.rs#L343-L345)).
- `BandwidthTracker` ([app.rs:78](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L78)) shows
  the exact pattern to follow: a `VecDeque<f32>` of recent
  samples, a `tick()` to advance, and a sparkline render
  block in the status bar ([app.rs:1037-1062](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L1037-L1062)).
- The cadence mode (`--cadence`) injects a keystroke every
  2 seconds, which is the only thing that produces latency
  samples today. Without it, the sparkline stays empty —
  that's fine; we're matching existing behaviour.

This work happens on the `screenshot` branch in the
`ryll-wt-screenshot` worktree.

## Mission and problem statement

Add two small quality-of-life features:

1. **Screenshot on demand.** Press F8 (or click a status-bar
   button) to save a PNG of the current display. Use a native
   file save dialog with a sensible default filename so the
   user can pick any location. In headless mode, F8 is not
   relevant; instead expose a `--screenshot-on-exit <PATH>`
   CLI flag, or skip headless support entirely (see open
   question below).

2. **Latency sparkline.** Keep a rolling history of latency
   samples (mirroring `BandwidthTracker`) and render a
   sparkline in the bottom stats panel next to the existing
   `Latency: Xms` label. Same shape as the bandwidth
   sparkline in the top status bar.

Both features are pure-GUI additions; neither touches the
protocol, the channel wiring, or any async task.

## Open questions

1. **Headless screenshot support.** The screenshot feature
   is intrinsically interactive (a hotkey + save dialog). Do
   we want a `--screenshot-on-exit <PATH>` CLI flag for
   automated tests, or is that out of scope? Recommendation:
   **out of scope for this plan.** Capture mode already writes
   `display.mp4` for headless workflows; a single
   end-of-session PNG is redundant. Revisit if a real use
   case appears.

2. **Multi-monitor screenshots.** With `--monitors N`, we
   have multiple surfaces. Options:
   - (a) Save one PNG per surface (`screenshot-1.png`,
     `screenshot-2.png`, ...).
   - (b) Save only the largest surface (matches bug report
     behaviour at [app.rs:702](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L702)).
   - (c) Save the surface under the mouse cursor at
     keypress time.

   Recommendation: **(a) one PNG per surface** if there's
   more than one. The save dialog returns a single path; we
   strip the extension and append `-N.png` for each surface.
   Document this in the help text near the button.

3. **Latency sparkline width.** The bandwidth sparkline is
   80px wide × 12px tall in the top status bar. The latency
   sparkline goes in the bottom stats panel, which is more
   cramped (single horizontal line of small labels). Match
   the bandwidth sparkline dimensions, or shrink to fit?
   Recommendation: match (80×12) for consistency. If it
   crowds the bottom panel, shrink the bandwidth one to
   match in a follow-up.

4. **Latency history length.** Bandwidth keeps 60 samples
   (1 minute at 1Hz). Cadence mode produces a sample every
   2 seconds. Match the 60-sample window (2 minutes of
   latency data), or pick a different number? Recommendation:
   **60 samples** for consistency. The data sparsity is the
   user's problem; if they want denser samples they can
   adjust cadence.

5. **Sparkline scale.** Bandwidth uses dynamic max from the
   visible window. Latency could either do the same, or fix
   the upper bound to (say) 100ms so spikes are visually
   meaningful. Recommendation: **dynamic max** — same code
   shape as bandwidth, less special-casing.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Screenshot hotkey + save dialog | [PLAN-screenshot-and-latency-hud-phase-01-screenshot.md](/components/ryll/plans/PLAN-screenshot-and-latency-hud-phase-01-screenshot/) | Complete |
| 2. Latency sparkline | [PLAN-screenshot-and-latency-hud-phase-02-latency-sparkline.md](/components/ryll/plans/PLAN-screenshot-and-latency-hud-phase-02-latency-sparkline/) | Complete |
| 3. Documentation | [PLAN-screenshot-and-latency-hud-phase-03-docs.md](/components/ryll/plans/PLAN-screenshot-and-latency-hud-phase-03-docs/) | Complete |

Both feature phases are independent and could be done in
either order. Phase 3 collates README/ARCHITECTURE updates
into a single commit at the end.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never
in the management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean
and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step
   with the brief from the plan, at the recommended
   effort level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong.
5. **Commit** once the management session is satisfied
   with the result.

This work is small enough that sub-agents can work
directly in the `ryll-wt-screenshot` worktree without
further isolation.

### Planning effort

The phase plans here can each be created at **medium
effort**. The codebase research is mostly done in the
master plan above; each phase has a narrow scope and a
clear pattern to follow (encode_png, BandwidthTracker).

### Step-level guidance

See each phase plan for per-step tables. As a general
rule:

- Phase 1 (screenshot): **sonnet, medium effort** is
  sufficient. The brief is mechanical: bind a key, get
  pixels, call `encode_png`, write file via `rfd`.
- Phase 2 (latency sparkline): **sonnet, medium effort**.
  Direct copy of the `BandwidthTracker` pattern with
  different sample source.
- Phase 3 (docs): **haiku, low effort**. Update README
  feature bullets and the keyboard shortcut list.

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] `pre-commit run --all-files` passes.
- [ ] `make test` passes.
- [ ] Manual smoke test: launch ryll against `make
      test-qemu`, press F8, verify PNG opens. With
      `--cadence`, verify latency sparkline populates.
- [ ] Commit message follows project conventions
      (Co-Authored-By with model, context, effort).

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* Pressing F8 in the GUI saves the current display surface
  as a PNG via a native file dialog. With multiple monitors,
  one PNG per surface is saved with `-1`, `-2` suffixes.
* A "Screenshot" button in the bottom-right of the status
  bar (next to "Report") triggers the same flow.
* The bottom stats panel renders a sparkline of recent
  latency samples next to the existing `Latency: Xms`
  label, mirroring the bandwidth sparkline pattern.
* `pre-commit run --all-files` passes.
* `make test` passes; new logic has unit tests where
  practical (the `LatencyTracker` mirroring
  `BandwidthTracker` should be unit-testable).
* `README.md` mentions the F8 screenshot feature in the
  features list and in the keyboard shortcuts section.
* `ARCHITECTURE.md` does not need changes — neither
  feature adds a new module or changes data flow.

### Future work

* `--screenshot-on-exit <PATH>` for headless mode if a
  use case appears.
* Latency sparkline tooltip showing min/max/mean over
  the visible window.
* Configurable latency upper bound (fixed-scale mode)
  for consistent visual comparison across sessions.
* Apply the same sparkline pattern to FPS, which is
  already in the bottom stats panel as a single number.

### Bugs fixed during this work

(None yet.)

### Documentation index maintenance

When this plan is created, add to `docs/plans/index.md`:

* Master plans table row: 2026-04-19, link to this file,
  intent "Add F8 screenshot capture and a latency
  sparkline in the stats panel", initial status, links
  to each phase plan file.
* `order.yml`: add an entry for this master plan.

Mark status *Complete* when all phases land.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
