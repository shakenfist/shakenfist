# Hamburger menu for status-bar actions

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

When we get to detailed planning, I prefer a separate plan
file per detailed phase. This plan is short enough that the
detail lives inline — there are no separate phase files.

I prefer one commit per logical change. This plan is one
logical change and lands as one commit.

## Situation

The right-hand cluster of `ryll/src/app.rs:1518` (the
`TopBottomPanel::bottom("stats")` block) currently renders
six action buttons in a horizontal row: **Traffic / USB /
Folders / Screenshot / Gaps / Report**. Each toggles a side
panel, opens a dialog, or opens a save flow. Alongside them
the same row hosts non-action UI: the volume mute button
and slider, latency and bandwidth labels and sparklines,
the FPS readout, the Cadence indicator, the USB device
description, and a transient status message slot for bug
reports at `app.rs:1610`.

This row has accumulated organically as features landed
(USB, WebDAV, screenshot, bug reports, gaps, traffic
viewer). It now mixes two distinct concerns:

* **Glanceable real-time state** — volume, latency, FPS,
  bandwidth, Cadence indicator, USB description. These
  belong in the status bar by design.
* **Feature actions** — Traffic / USB / Folders / Screenshot
  / Report. These are commands the operator invokes
  occasionally, not state they read continuously.

Adding a seventh action button (Paste-as-keystrokes,
[PLAN-paste-as-keystrokes.md](/components/ryll/plans/PLAN-paste-as-keystrokes/))
is the trigger for this cleanup but not the cause: the row
was already crowded.

The Gaps badge is a third category — a notification, not an
action — and is intentionally **not** in scope for this
plan. Notifications get their own treatment in
[PLAN-notifications.md](/components/ryll/plans/PLAN-notifications/), which
absorbs Gaps and the bug-report transient status alongside
SPICE protocol notifications. Doing notifications here
would conflate two refactors.

## Mission and problem statement

Migrate the five status-bar **action** buttons (Traffic,
USB, Folders, Screenshot, Report) into a single
`egui::menu::menu_button` rendered as a hamburger icon at
the right edge of the status bar. Leave the Gaps button in
its current place for now (it goes away when the
notifications plan lands and replaces it with a bell).
Leave all glanceable state (volume, latency, FPS,
bandwidth, sparklines, Cadence, USB device description) in
place — those belong in the status bar.

The result: the status bar shows volume / sparklines /
state on the left and centre, and one hamburger icon plus
the existing Gaps badge on the right. Operators reach
features via *Menu → Traffic / USB / Folders / Screenshot
/ Report*. Toggle entries (Traffic, USB, Folders) are
checkboxes that reflect open/closed state of their side
panels.

## Open questions

1. **Hamburger glyph**: egui supports either a unicode
   character (`☰` U+2630) or a custom icon via
   `egui::Image`. Recommend the unicode glyph for now —
   no asset pipeline, matches the existing Mute / Speaker
   approach (`🔇` / `🔊` at `app.rs:1521`). If we want a
   crisper PNG icon, defer to Future work.

2. **Toggle vs. button entries**: side-panel toggles
   (Traffic, USB, Folders) want a checkmark when open.
   `egui::Ui::checkbox` against the boolean works; another
   option is `ui.selectable_label`. Recommend `checkbox`
   for visual clarity.

3. **Screenshot and Report**: these are one-shot actions,
   not toggles. Plain `ui.button` entries inside the menu.

4. **Keyboard shortcuts**: should menu items advertise
   shortcuts (e.g. `F8` is already wired to Screenshot at
   `app.rs:???`)? Recommend yes — show the shortcut in
   grey to the right of the label per egui convention.
   Verify F8 exists; add nothing new in this plan.

5. **Menu position**: rightmost in the right-to-left
   layout block at `app.rs:1518`, just inside (i.e. before)
   the Gaps badge so the menu and badge form a single
   right-aligned cluster. Recommended.

6. **Bug-report transient status message**: currently at
   `app.rs:1610`, rendered inline beside the Report button.
   With Report inside the menu, the transient message has
   no host. Two options: (a) keep rendering it inline at
   the same position (it shows up next to the hamburger);
   (b) defer to the notifications plan, where the
   transient gets absorbed into the notifications system.
   Recommend (a) for this plan — minimal disruption — and
   let the notifications plan migrate it later.

## Execution

Single phase, executed inline (no separate phase file).

| Phase | Plan | Status |
|-------|------|--------|
| 1. Migrate buttons to hamburger | [Phase 1](/components/ryll/plans/PLAN-hamburger-menu-phase-01-migrate/) | Complete |

**Brief for sub-agent (medium effort, sonnet, no
isolation)**:

In `ryll/src/app.rs`, locate the right-to-left layout block
that begins at `app.rs:1518` (`ui.with_layout(egui::Layout::
right_to_left(...))`). The block currently contains, in
right-to-left order: Mute button, volume slider, separator,
bandwidth label, bandwidth sparkline, separator, Traffic
button, USB button, Folders button, Screenshot button, Gaps
badge, Report button, transient bug-report status message.

Refactor as follows:

1. Add a hamburger menu using `egui::menu::menu_button("☰",
   |ui| { ... })` at the rightmost position inside this
   block (i.e. emitted first in the right-to-left layout —
   in egui's right-to-left layout, the first widget added
   appears furthest right). Place it just before (visually
   to the left of) the Gaps badge so the menu and Gaps
   form a single cluster on the far right.

2. Inside the menu closure, add five entries:

   * `ui.checkbox(&mut self.show_traffic_viewer, "Traffic")`
   * `ui.checkbox(&mut self.show_usb_panel, "USB")`
   * `ui.checkbox(&mut self.show_webdav_panel, "Folders")`
   * `ui.button("Screenshot")` — on click, call
     `self.open_screenshot_dialog()`
   * `ui.button("Report")` — on click, set
     `self.show_bug_dialog = true`,
     `self.bug_report_type = BugReportType::Display`,
     `self.bug_description.clear()`, and call
     `self.begin_trigger_snapshot()` (replicates the
     existing button at `app.rs:1604`).

   For the Screenshot entry, advertise the existing F8
   keyboard shortcut by passing
   `egui::Button::new("Screenshot").shortcut_text("F8")`.
   Verify F8 still triggers the screenshot flow elsewhere
   in `handle_input`; do not change that wiring.

3. Remove the now-orphaned five `ui.small_button(...)`
   calls (Traffic, USB, Folders, Screenshot, Report) from
   the layout block. **Leave the Gaps badge and its hover
   tooltip in place** — Gaps is out of scope for this plan
   (it becomes a notification in the notifications plan).
   **Leave the transient bug-report status message in
   place** — it stays as inline content for now.

4. Verify behaviour by running ryll headfully (`cargo run
   -p ryll -- --direct localhost:5900`, or whatever local
   target the operator has handy):

   * Click the hamburger; menu opens.
   * Click *Traffic*; the side panel toggles open and the
     menu's checkbox reflects the open state on next open.
   * Same for *USB* and *Folders*.
   * Click *Screenshot*; the save dialog opens. Press F8;
     same.
   * Click *Report*; the bug dialog opens. After
     submitting, the transient status appears beside the
     hamburger for 5 s as before.

5. Run `pre-commit run --all-files` and `cargo test
   --workspace`. Fix any rustfmt / clippy fallout.

6. Update `README.md` to mention the menu (rename any "use
   the X button on the status bar" phrasings to "Menu →
   X"). Update `ARCHITECTURE.md` if it describes the
   status-bar layout (likely brief). Update `AGENTS.md` if
   it tells contributors to add new features as status-bar
   buttons (replace with "as menu entries").

7. Update `docs/plans/index.md` to mark this plan
   *Complete*.

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
4. **Fix or retry** if the output is wrong. Diagnose
   whether the brief was insufficient (improve it) or
   the model was too light (upgrade it), then re-run.
5. **Commit** once the management session is satisfied
   with the result.

This plan is small enough that there is one sub-agent
spawn (the brief above) and one review.

### Planning effort

Master plan: medium effort. The codebase work is well
understood and the brief above is detailed.

### Step-level guidance

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1    | medium | sonnet | none     | The brief in *Execution* above. |

### Management session review checklist

- [ ] The five action buttons (Traffic / USB / Folders /
      Screenshot / Report) are no longer in the layout
      block at `app.rs:1518`.
- [ ] A `menu_button("☰", ...)` is in the layout block
      with the five entries inside.
- [ ] Side-panel checkboxes reflect open/closed state.
- [ ] F8 still triggers the screenshot flow.
- [ ] The Gaps badge and the bug-report transient
      message are still in the layout block, unchanged.
- [ ] `pre-commit run --all-files` clean.
- [ ] `cargo test --workspace` clean.
- [ ] README, AGENTS, ARCHITECTURE updated where they
      describe the status-bar layout.

## Administration and logistics

### Success criteria

* `pre-commit run --all-files` passes.
* `cargo test --workspace` passes.
* Status bar shows volume / sparklines / state on the
  centre-left and one hamburger plus the existing Gaps
  badge on the right.
* All five migrated features are reachable from the menu
  with no behavioural change relative to the current
  buttons.
* Side-panel toggles render checkmarks in the menu.
* The bug-report transient status still appears on
  submit.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` reflect
  the menu rather than the button row.
* `docs/plans/index.md` lists this plan as *Complete*.

### Future work

* **Custom hamburger icon**: replace the `☰` glyph with a
  proper PNG / SVG icon if visual polish becomes a
  priority.
* **macOS-native menu placement**: on macOS, the
  application menu bar is the conventional home for
  feature actions. egui's menu is rendered in the window
  regardless of OS; an OS-native menu would be a separate
  effort.
* **Keyboard mnemonic for the menu itself**: a shortcut
  like `F10` or `Alt` to open the menu without mouse.
  Defer until we have evidence of demand.

### Bugs fixed during this work

(To be filled in as the work proceeds.)

### Documentation index maintenance

When this plan is created, add a row to `docs/plans/
index.md` *Master plans* table with today's date, a link
to this plan, the one-line intent "Replace the status-bar
action-button row with a single hamburger menu so the row
returns to glanceable state", initial status *Not
started*, and "(single phase, inline)" in the phases
column. Add an entry to `docs/plans/order.yml`. Mark
*Complete* when the migration commit lands.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
