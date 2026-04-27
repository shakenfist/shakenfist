# Phase 4: GUI — bell, side panel, mark-read

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

The master plan is
[PLAN-notifications.md](/components/ryll/plans/PLAN-notifications/). Phases 1–3
are complete (commits `a16f6781`, `b3e520b1`, `3780be03`).
Read the master plan's "Discoveries during this work" section
first — the QEMU-only-emits-on-main-channel finding and the
existing-parser finding both shape Phase 4's expected
on-screen behaviour.

## Goal

Surface the notification store to the operator. At the end
of this phase:

* A bell glyph sits in the status-bar right-edge cluster,
  immediately before the separator that precedes the
  hamburger menu (where the *Gaps* badge used to live
  before Phase 3 deleted it).
* When `unread_count > 0`, the bell shows a small filled
  dot coloured by the highest-severity unread entry —
  amber for Warn, red for Error, default colour for Info.
  Low-visibility entries (Q4) do not flash the bell.
* Clicking the bell toggles a right-side panel
  ("Notifications") that mirrors the layout of the
  existing Traffic viewer at `app.rs:1761`.
* Each entry renders a severity glyph, a source label
  ("SPICE/main", "Gap", "BugReport", "Internal"), a
  relative timestamp ("3s ago"), and the message text.
  Read entries dim; unread entries render at full opacity.
* Per-entry actions: a small *Dismiss* button removes
  that entry from the store entirely. Header actions:
  *Mark all read* (sets `read=true` on every entry) and
  *Clear all* (drops every entry).
* Closing the panel calls `mark_all_read` per master-plan
  Q2 — the operator gets one chance to triage what was
  unread, and the bell dot clears on close.
* The Phase 3 manual-smoke gap is closed: a TLS-off QEMU
  session shows the "channel is insecure" notification
  in the side panel.

No new producers and no changes to the Phase 1 store
shape; Phase 4 adds two small store helpers
(`remove(id)` and `highest_bell_severity()`) plus all the
egui rendering.

## Background

### Post-Phase-3 status-bar layout

`ryll/src/app.rs:1605-1758` renders the status bar inside a
`TopBottomPanel::bottom("stats")`. The right-edge cluster
ends with:

```rust
ui.separator();
egui::menu::menu_button(ui, "☰", |ui| { ... });
```

The bell goes immediately before that `ui.separator()` —
the same slot the deleted *Gaps: N* button occupied. The
hamburger keeps its position as the rightmost element.

The hamburger menu's items today are: *Traffic*, *USB*,
*Folders*, *Screenshot* (F8), *Report*, and (optionally)
*Paste*. The bell is **not** an item inside the menu — it's
a standalone button outside, because click semantics differ
("toggle a side panel" vs "menu item action").

### Traffic viewer as the rendering precedent

`app.rs:1760-1848` is the model. Same `egui::SidePanel::right`
shape, same `egui::ScrollArea::vertical()`, same `ui.heading`
+ small action buttons header pattern. Phase 4's panel
copies the structure with notification-shaped fields swapped
in.

### Hamburger plan status

[PLAN-hamburger-menu.md](/components/ryll/plans/PLAN-hamburger-menu/) is
*Complete*. The hamburger is in place at `app.rs:1722`,
which means the master plan's conditional "if hamburger
hasn't landed yet" branch in §"Phase 4" doesn't apply — we
go straight to "bell sits next to the hamburger".

### What's in the store today (Phases 2 + 3 producers)

* `Spice { channel: Main, what: N }` entries from QEMU's
  insecure-channel warnings (per the smoke-test discovery,
  these arrive on main only with the affected channel
  named in the message).
* `Gap` entries from the `register_gap_notification_observer`
  callback (one per distinct `warn_once!` key).
* `BugReport` entries from `finish_bug_report` Ok/Err.
* `Internal` entries from screenshot Ok/Err/no-surface
  and paste-completed.

The bell needs to handle every variant; the panel renders
each with the appropriate source label.

## Design

### Two new store helpers

In `ryll/src/notifications.rs` add:

```rust
impl NotificationStore {
    /// Remove the entry with the given id. No-op if id is unknown.
    /// Used by the side panel's per-entry Dismiss button.
    pub fn remove(&mut self, id: u64) {
        self.entries.retain(|e| e.id != id);
    }

    /// Highest severity among unread entries that should flash the
    /// bell. Excludes entries with `visibility == Some(SpiceVisibility::Low)`
    /// per master-plan Q4 — low-visibility is informational and
    /// must not pull the operator's eye.
    pub fn highest_bell_severity(&self) -> Option<NotifySeverity> {
        self.entries
            .iter()
            .filter(|e| !e.read)
            .filter(|e| e.visibility != Some(SpiceVisibility::Low))
            .map(|e| e.severity)
            .max()
    }
}
```

Phase 1's `highest_unread_severity()` is left untouched —
it still has its place for any future "show me the worst
of everything regardless of visibility" surface, and
keeping it stable means Phase 1's tests don't churn.

Three new unit tests in the existing `mod tests` block:

| Test | Asserts |
|------|---------|
| `remove_drops_entry` | After push of three, `remove(id_of_middle)` leaves two; the middle's id is gone. |
| `remove_unknown_id_is_noop` | `remove(999)` on a 3-entry store does not panic and leaves length 3. |
| `highest_bell_severity_skips_low_visibility` | Push Warn-with-Low, Info-with-High, Error-with-Low; `highest_bell_severity()` returns `Some(Info)` (Error is filtered for Low visibility, Warn is filtered for Low visibility, Info-High remains). |

### Helper functions on NotificationSource and severity

In `ryll/src/notifications.rs` add:

```rust
impl NotificationSource {
    /// Compact human label used by the side panel.
    /// SPICE entries render as "SPICE/<channel-name>"; other
    /// variants render as their static name.
    pub fn label(&self) -> String {
        match self {
            NotificationSource::Gap => "Gap".to_string(),
            NotificationSource::BugReport => "BugReport".to_string(),
            NotificationSource::Internal => "Internal".to_string(),
            NotificationSource::Spice { channel, .. } => {
                format!("SPICE/{}", channel.name())
            }
        }
    }
}
```

Two unit tests (deterministic, no UI):

| Test | Asserts |
|------|---------|
| `source_label_static_variants` | `Gap.label() == "Gap"`, `BugReport.label() == "BugReport"`, `Internal.label() == "Internal"`. |
| `source_label_spice_variant` | `Spice { channel: Display, what: 0 }.label() == "SPICE/display"`. |

### Severity glyph and colour

A small renderer module — keep it private inside
`ryll/src/notifications.rs` since it's the only file that
touches notification rendering primitives until somebody
needs them elsewhere:

```rust
/// (glyph, optional colour). None colour means "default text colour".
fn severity_visuals(s: NotifySeverity) -> (&'static str, Option<egui::Color32>) {
    match s {
        NotifySeverity::Info => ("\u{2139}", None),  // ℹ
        NotifySeverity::Warn => ("\u{26A0}",
            Some(egui::Color32::from_rgb(255, 180, 80))), // ⚠ amber
        NotifySeverity::Error => ("\u{2716}",
            Some(egui::Color32::from_rgb(220, 90, 90))),  // ✖ muted red
    }
}
```

The amber matches the existing "inputs" channel colour in
the Traffic viewer palette (`app.rs:1822`); the muted red
is close to the deleted Gaps button's colour
(`Color32::from_rgb(200, 80, 80)`) for visual continuity.
`egui::Color32::RED` is too saturated against a dark theme.

This function lives next to the rendering code, not in the
public API. egui import is fine inside the notifications
module — there's already no protocol-vs-GUI separation
because Phase 1 deliberately put the store in `ryll/`.

### Bell rendering

Code lives in `app.rs` inline, in the status-bar
horizontal layout. Replace the current sequence

```rust
ui.separator();
egui::menu::menu_button(ui, "☰", |ui| { ... });
```

with

```rust
let unread_count = self.notifications
    .lock().map(|s| s.unread_count()).unwrap_or(0);
let bell_severity = self.notifications
    .lock().map(|s| s.highest_bell_severity()).unwrap_or(None);

let mut text = egui::RichText::new("\u{1F514}");  // 🔔
if let Some(sev) = bell_severity {
    if let (_, Some(colour)) = notifications::severity_visuals(sev) {
        text = text.color(colour);
    }
}
let response = ui.add(egui::Button::new(text));
if unread_count > 0 {
    response.clone().on_hover_text(format!(
        "{} unread notification{}",
        unread_count,
        if unread_count == 1 { "" } else { "s" }));
}
if response.clicked() {
    if self.show_notifications_panel {
        // Closing — mark all read per Q2.
        if let Ok(mut s) = self.notifications.lock() {
            s.mark_all_read();
        }
    }
    self.show_notifications_panel = !self.show_notifications_panel;
}

ui.separator();
egui::menu::menu_button(ui, "☰", |ui| { ... });
```

Two notes worth pinning:

* **Two locks per frame.** Acquiring the notifications
  mutex twice (once for `unread_count`, once for
  `highest_bell_severity`) is fine but mildly wasteful.
  The cleaner shape combines them into a single
  `lock().map(|s| (s.unread_count(), s.highest_bell_severity()))`
  call. Implementer's choice; the wasteful form is more
  readable.
* **The bell glyph itself doesn't change colour when
  unread.** The master plan §Q7 says "the bell's dot picks
  up the highest-severity unread item's colour", but that's
  a "dot next to the bell" intent, not a tint of the bell.
  We approximate by tinting the bell glyph itself, which is
  a single character and so renders simpler. If a separate
  dot is needed later, that's an iteration — Future work.

### Side-panel rendering

```rust
if self.show_notifications_panel {
    egui::SidePanel::right("notifications")
        .default_width(360.0)
        .show(ctx, |ui| {
            // Header: title + counts + actions
            ui.horizontal(|ui| {
                ui.heading("Notifications");
                ui.with_layout(
                    egui::Layout::right_to_left(egui::Align::Center),
                    |ui| {
                        if ui.small_button("Clear all").clicked() {
                            if let Ok(mut s) = self.notifications.lock() {
                                s.clear();
                            }
                        }
                        if ui.small_button("Mark all read").clicked() {
                            if let Ok(mut s) = self.notifications.lock() {
                                s.mark_all_read();
                            }
                        }
                    },
                );
            });

            // Total/unread summary
            let (total, unread, snapshot) = match self.notifications.lock() {
                Ok(s) => (s.len(), s.unread_count(),
                          s.iter_newest_first().cloned().collect::<Vec<_>>()),
                Err(_) => (0, 0, Vec::new()),
            };
            ui.label(format!("{} total / {} unread", total, unread));
            ui.separator();

            // Entries
            let mut to_remove: Vec<u64> = Vec::new();
            egui::ScrollArea::vertical().show(ui, |ui| {
                if snapshot.is_empty() {
                    ui.label("No notifications.");
                }
                for entry in &snapshot {
                    ui.horizontal(|ui| {
                        let (glyph, colour) =
                            notifications::severity_visuals(entry.severity);
                        let mut g = egui::RichText::new(glyph);
                        if let Some(c) = colour { g = g.color(c); }
                        if entry.read { g = g.weak(); }
                        ui.label(g);
                        ui.monospace(notifications::format_relative(entry.when));
                        ui.colored_label(
                            egui::Color32::GRAY,
                            entry.source.label());
                        let mut msg_text =
                            egui::RichText::new(&entry.message);
                        if entry.read { msg_text = msg_text.weak(); }
                        if entry.count > 1 {
                            msg_text = msg_text;  // base message
                            // Append a [N×] suffix for folded entries.
                            ui.label(msg_text);
                            ui.label(egui::RichText::new(
                                format!("[{}\u{00D7}]", entry.count))
                                .weak());
                        } else {
                            ui.label(msg_text);
                        }
                        ui.with_layout(
                            egui::Layout::right_to_left(egui::Align::Center),
                            |ui| {
                                if ui.small_button("Dismiss").clicked() {
                                    to_remove.push(entry.id);
                                }
                            },
                        );
                    });
                }
            });
            if !to_remove.is_empty() {
                if let Ok(mut s) = self.notifications.lock() {
                    for id in to_remove {
                        s.remove(id);
                    }
                }
            }
        });
} else if self.notifications_panel_was_open_last_frame {
    // The panel was closed this frame (operator clicked the bell
    // again or used the panel-close affordance). Mark all read so
    // the bell dot clears on the next frame's render — Q2.
    if let Ok(mut s) = self.notifications.lock() {
        s.mark_all_read();
    }
}
self.notifications_panel_was_open_last_frame =
    self.show_notifications_panel;
```

Two design points:

* **Snapshot-then-render.** `iter_newest_first().cloned()`
  builds an owned `Vec<NotificationEntry>` so the lock is
  released before any UI code runs. The Traffic viewer
  follows the same pattern with `traffic_viewer_entries:
  Vec<TrafficViewEntry>` (`app.rs:320`). For
  notifications, we don't bother with a refresh-cadence
  field — the store mutates rarely (a few per session)
  and rebuilding the snapshot every frame at typical
  notification volumes is trivial.
* **Close detection.** `egui::SidePanel` doesn't expose a
  close button; closing happens by clicking the bell again
  (or via the `show` boolean toggling for any other
  reason). To mark-all-read on close, we track
  `notifications_panel_was_open_last_frame` and act on the
  `true → false` edge. This is the same trick used for
  `last_sent_resize` and friends elsewhere in
  `app.rs`.

### `format_relative`

```rust
pub fn format_relative(when: SystemTime) -> String {
    let now = SystemTime::now();
    let delta = now.duration_since(when).unwrap_or(Duration::ZERO);
    let secs = delta.as_secs();
    if secs < 1 { "now".to_string() }
    else if secs < 60 { format!("{}s ago", secs) }
    else if secs < 3600 { format!("{}m ago", secs / 60) }
    else if secs < 86_400 { format!("{}h ago", secs / 3600) }
    else { format!("{}d ago", secs / 86_400) }
}
```

Public on the module so the side panel can call it. Two
unit tests:

| Test | Asserts |
|------|---------|
| `format_relative_seconds` | A `when` 5 seconds before now formats as `"5s ago"`. (Uses `SystemTime::now() - Duration::from_secs(5)`.) |
| `format_relative_in_future_returns_now` | A `when` 1 second AFTER now formats as `"now"` (since `duration_since` errors and falls back to ZERO). Pins the clock-skew defence. |

`format_relative_seconds` has a small race risk if the test
host pauses between constructing `when` and calling
`format_relative`. Mitigate by allowing either `"5s ago"`
or `"6s ago"` in the assertion; alternatively skip the
specific-value test and just assert the result ends with
`"s ago"`. The second test is fully deterministic.

### New `RyllApp` fields

```rust
show_notifications_panel: bool,
notifications_panel_was_open_last_frame: bool,
```

Both initialised `false` in `RyllApp::new`. No persistence
across sessions — the panel always starts closed.

### What does NOT change

* The Phase 1 store API beyond the two new helpers.
* Producer behaviour from Phases 2 and 3.
* Any tracing / log output.
* The hamburger menu's items.

### Manual smoke test

1. `make build`
2. `make test-qemu`
3. `./target/debug/ryll --verbose --direct localhost:5900`
4. After channels connect, observe the bell in the
   right-edge cluster of the status bar. With QEMU's
   `disable-ticketing=on,plaintext-channel=all` config
   from the Phase 2 smoke test, the bell should show a
   coloured dot once the main channel's NOTIFY arrives.
5. Click the bell → side panel opens. Confirm an entry
   labelled `SPICE/main` with a Warn glyph and the
   "channel is insecure" message text is visible.
6. Click *Dismiss* on that entry. It vanishes.
7. Trigger F8 (screenshot); cancel the dialog. No
   notification fires (the cancel path is silent).
8. Trigger F8 and save to a path that fails (e.g. a
   read-only path). An Error/Internal notification
   appears.
9. Trigger F12 (bug report) → submit. An Info/BugReport
   notification appears.
10. Click *Mark all read* — entries dim. Bell dot clears
    on next frame.
11. Click *Clear all* — panel empties.
12. With unread entries present, click the bell to close
    the panel. Re-open — entries are present but read
    (dimmed); bell has no dot.

Record outcome in the commit message.

## Steps

### Step 1: store helpers + module helpers + tests

In `ryll/src/notifications.rs`:

1. Add `NotificationStore::remove(id: u64)` and
   `NotificationStore::highest_bell_severity(&self) ->
   Option<NotifySeverity>`.
2. Add `NotificationSource::label(&self) -> String`.
3. Add `pub fn format_relative(when: SystemTime) -> String`.
4. Add `pub(crate) fn severity_visuals(s: NotifySeverity)
   -> (&'static str, Option<egui::Color32>)`. This is the
   first egui-typed item in the module — add the egui
   import. Sub-agent should verify the `egui` crate is
   already available to ryll (it is — eframe re-exports
   it and `app.rs` uses `egui::*` everywhere).
5. Add the seven unit tests listed in the §"Tests"-style
   sub-tables: `remove_drops_entry`,
   `remove_unknown_id_is_noop`,
   `highest_bell_severity_skips_low_visibility`,
   `source_label_static_variants`,
   `source_label_spice_variant`,
   `format_relative_seconds` (with the ±1s tolerance),
   `format_relative_in_future_returns_now`.
6. Run `make test`; ryll test count goes from 213 to 220.
7. Run `pre-commit run --all-files`.

### Step 2: bell + side panel in app.rs

In `ryll/src/app.rs`:

1. Add `show_notifications_panel: bool` and
   `notifications_panel_was_open_last_frame: bool` fields,
   both `false` in `RyllApp::new`.
2. Insert the bell-button block immediately before the
   `ui.separator()` that precedes the hamburger
   `menu_button` (currently at `app.rs:1721-1722` —
   verify line by reading context).
3. Add the side-panel rendering block right after the
   hamburger's enclosing `TopBottomPanel::bottom("stats")
   ...show(...)` returns — co-located with the existing
   `if self.show_traffic_viewer { ... }` block at
   `app.rs:1761`.
4. Add the "panel was just closed" `mark_all_read` shim
   after the side-panel block (handles the `true → false`
   transition of `show_notifications_panel`).
5. Run `make build` (must succeed; expect a few
   Phase-1-era `#[allow(dead_code)]` exemptions to start
   firing as their items get used — clean those allows
   off the items that are now used).
6. Run `make test`.
7. Run `pre-commit run --all-files`.

### Step 3: cleanup

After step 2, the module-level `#![allow(dead_code)]` at
the top of `ryll/src/notifications.rs` may be removable
(every public item except `snapshot` and possibly some
`mark_*` variants is now used). Remove the allow if
clippy permits; if a few items remain unused, attribute
them individually with `#[allow(dead_code)]` or remove
them. Phase 5 will need `snapshot` for bug-report
serialisation, so leave that one — perhaps with an
item-level allow.

## Sub-agent execution table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 (helpers + tests) | medium | sonnet | none | In `ryll/src/notifications.rs`, add the two `NotificationStore` methods (`remove(id)`, `highest_bell_severity()`), `NotificationSource::label()`, `format_relative(SystemTime)`, and `severity_visuals(NotifySeverity)` per PLAN-notifications-phase-04-gui.md §"Two new store helpers" through §"`format_relative`". Add the seven unit tests listed across those sections. Use `egui::Color32::from_rgb` for the colour pair (Warn=255,180,80; Error=220,90,90); Info returns `None`. Read the existing imports in the file before adding `use egui;` — the module is in the ryll crate which already depends on eframe/egui. The `format_relative_seconds` test should accept `"5s ago"` or `"6s ago"` to absorb the ±1s host pause race. Run `make test` (expecting 220 ryll tests) and `pre-commit run --all-files`. Rust toolchain runs in Docker via the Makefile. |
| 2 (bell + side panel) | medium | sonnet | none | In `ryll/src/app.rs`, add the bell button + side panel + close-detection shim per PLAN-notifications-phase-04-gui.md §"Bell rendering", §"Side-panel rendering", and §"New RyllApp fields". The bell goes immediately BEFORE the `ui.separator()` that precedes the hamburger `menu_button` (currently around line 1722). The side panel goes alongside the existing `if self.show_traffic_viewer { ... }` block (currently around line 1761). The close-detection shim sits in the same area; track `notifications_panel_was_open_last_frame: bool` and call `mark_all_read` on the true→false edge. Use the helpers from step 1 (`notifications::format_relative`, `notifications::severity_visuals`, `NotificationSource::label`). Take an `iter_newest_first().cloned().collect::<Vec<_>>()` snapshot inside the lock so rendering happens off-lock — same pattern the Traffic viewer uses with `traffic_viewer_entries`. Run `make build` (watch for new dead-code warnings), `make test`, `pre-commit run --all-files`. The line numbers above are approximate — read context before editing. Do not commit. |

Step 1 lands first because step 2 calls the helpers. Both
land in one phase commit.

## Administration and logistics

### Success criteria

* Bell glyph appears in the status-bar right-edge cluster,
  immediately before the hamburger separator.
* Bell shows a coloured dot/tint when `unread_count > 0`,
  picking up the highest-severity unread (excluding
  low-visibility) entry's colour. Plain when zero unread.
* Clicking the bell toggles the right-side
  Notifications panel.
* Closing the panel via the bell calls `mark_all_read`.
* Per-entry *Dismiss* button removes the entry.
* Header *Mark all read* and *Clear all* behave as
  named.
* Read entries render dimmed; unread render full.
* `[N×]` suffix appears on entries with `count > 1`.
* `make build`, `make test`, `pre-commit run --all-files`
  pass; ryll tests grow from 213 to 220.
* Manual smoke test confirms QEMU "channel is insecure"
  message visible in panel; F12 bug report and F8
  screenshot land in panel; *Dismiss* / *Mark all read*
  / *Clear all* / panel-close-marks-read all work.

### Risks

* **Lock acquisition every frame.** The bell-rendering
  code grabs `self.notifications.lock()` once or twice
  per frame at 60 FPS. Mutex contention with the
  channel-handler tokio tasks (which also `lock()` on
  push) is theoretically possible. In practice push
  frequency is low (a handful per session) and lock
  hold time is microseconds; the existing
  `Arc<TrafficBuffers>` has the same shape and works
  fine at hundreds of pushes per second.
* **Side panel layout regression.** The side panel
  shares space with the central display surface; on
  small windows the panel may eat the surface area.
  The Traffic viewer has the same constraint and is
  not gated; we'll inherit whatever its UX has been.
* **`SystemTime::duration_since` and clock skew.** If
  the system clock moves backward between push and
  render, `duration_since` errors. `format_relative`
  falls back to `Duration::ZERO` ("now") on that error.
  Pinned by test
  `format_relative_in_future_returns_now`.
* **Severity glyph font support.** The chosen glyphs
  (ℹ ⚠ ✖) are in egui's default font family. If a
  glyph fails to render on a particular Linux setup
  it'll show as a tofu box — annoying but not a
  correctness bug. Future iteration could swap to
  ASCII labels or eframe's icon font.

### Future work (deferred from this phase)

* **A real coloured dot next to the bell** (rather than
  tinting the bell glyph). Requires custom painting via
  `Painter::circle_filled` overlaid on the button rect;
  enough rendering ceremony that it's not worth it
  unless the tinted-glyph approach proves
  unintelligible.
* **Per-severity / per-source filters in the panel.**
  Master plan §"Future work".
* **Persistence across sessions.** Master plan
  §"Future work".
* **Click-to-detail for SPICE entries** — link the
  `what` enum to documentation. Master plan §"Future
  work".
* **Configurable severity threshold for bell flash**
  (e.g. Info never flashes). Master plan §"Future
  work" item 5; Q4's recommendation is implemented but
  the configuration knob isn't.

### Documentation index maintenance

When this phase lands, update the master plan's
*Execution* table row 4 from "Not started" to "Complete"
with a link to the merge commit.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
