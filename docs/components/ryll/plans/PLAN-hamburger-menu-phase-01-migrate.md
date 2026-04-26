# Phase 1: Migrate action buttons to hamburger menu

## Overview

Replace the five action buttons (Traffic, USB, Folders, Screenshot,
Report) in the status-bar right-to-left layout block with a single
`egui::menu::menu_button("☰", ...)` hamburger menu. Leave the Gaps
badge, transient bug-report status message, and all glanceable state
(volume, bandwidth, sparklines) untouched.

## Parent plan

[PLAN-hamburger-menu.md](/components/ryll/plans/PLAN-hamburger-menu/)

## Current state of the code

The right-to-left layout block lives at `ryll/src/app.rs:1518`
(`ui.with_layout(egui::Layout::right_to_left(...))`). Widgets
appear in code order, which renders right-to-left on screen:

| Code order | Widget | Lines | Type |
|------------|--------|-------|------|
| 1 | Mute button (`🔇`/`🔊`) | 1519-1524 | Glanceable state |
| 2 | Volume slider | 1525-1532 | Glanceable state |
| 3 | Separator | 1533 | Layout |
| 4 | Bandwidth label | 1535-1541 | Glanceable state |
| 5 | Bandwidth sparkline | 1542-1567 | Glanceable state |
| 6 | Separator | 1569 | Layout |
| 7 | **Traffic button** | 1570-1572 | Action (toggle) |
| 8 | **USB button** | 1573-1575 | Action (toggle) |
| 9 | **Folders button** | 1576-1578 | Action (toggle) |
| 10 | **Screenshot button** | 1579-1581 | Action (one-shot) |
| 11 | Gaps badge | 1582-1601 | Notification (out of scope) |
| 12 | **Report button** | 1603-1608 | Action (one-shot) |
| 13 | Transient status msg | 1610-1616 | Status (keep) |

Items in **bold** are the five actions migrating into the menu.

### Field names on `self` used by the action buttons

- `self.show_traffic_viewer: bool` (line 319) — Traffic toggle
- `self.show_usb_panel: bool` (line 295) — USB toggle
- `self.show_webdav_panel: bool` (line 306) — Folders toggle
- `self.show_bug_dialog: bool` (line 277) — Report dialog
- `self.bug_report_type: BugReportType` (line 278) — Report type
- `self.bug_description: String` (line 279) — Report text
- `self.bug_status_message: Option<(String, Instant)>` (line 280)

### F8 keyboard shortcut

F8 handling is at `app.rs:1426-1432`, early in the event loop
before the status bar renders. It calls
`self.open_screenshot_dialog()` when `!self.region_select_active`.
This wiring is independent of the button and must remain
unchanged.

### egui menu_button

`egui::menu::menu_button` is not used anywhere in the codebase
today. This will be the first use.

## Implementation steps

### Step 1: Add the hamburger menu

At `app.rs:1570` (immediately after the second separator on
line 1569), replace the five action buttons (lines 1570-1608)
with a single `egui::menu::menu_button` call.

The menu must be emitted **after** the separator at line 1569
(i.e. to the left of the bandwidth sparkline in the
right-to-left layout). In egui's right-to-left mode, widgets
added later appear further left, so adding the menu here places
it between the bandwidth area and the Gaps badge — matching the
plan's intent.

Replace lines 1570-1608 with:

```rust
egui::menu::menu_button(ui, "☰", |ui| {
    ui.checkbox(&mut self.show_traffic_viewer, "Traffic");
    ui.checkbox(&mut self.show_usb_panel, "USB");
    ui.checkbox(&mut self.show_webdav_panel, "Folders");
    if ui
        .add(egui::Button::new("Screenshot").shortcut_text("F8"))
        .clicked()
    {
        self.open_screenshot_dialog();
        ui.close_menu();
    }
    if ui.button("Report").clicked() {
        self.show_bug_dialog = true;
        self.bug_report_type = BugReportType::Display;
        self.bug_description.clear();
        self.begin_trigger_snapshot();
        ui.close_menu();
    }
});
```

Key design decisions:

- **Checkboxes for toggles**: Traffic, USB, and Folders use
  `ui.checkbox` bound directly to their `bool` fields. The
  checkbox reflects open/closed state and toggles it on click.
  No explicit `ui.close_menu()` — the panel toggles and the
  user can toggle multiple panels in one menu visit.

- **`shortcut_text("F8")` on Screenshot**: Renders "F8" in
  subdued text to the right of "Screenshot", per egui
  convention. This is purely decorative — the actual F8 key
  handling remains at `app.rs:1426`.

- **`ui.close_menu()` on one-shot actions**: Screenshot and
  Report close the menu after triggering, since there's no
  toggle state to inspect.

- **Report replicates exact existing logic**: `show_bug_dialog`,
  `bug_report_type`, `bug_description.clear()`, and
  `begin_trigger_snapshot()` — identical to the removed button
  at lines 1603-1608.

### Step 2: Leave Gaps badge and transient message in place

The Gaps badge (lines 1582-1601) and transient bug-report
status message (lines 1610-1616) remain exactly where they are.
After Step 1, the widget order becomes:

| Code order | Widget | Type |
|------------|--------|------|
| 1 | Mute button | Glanceable state |
| 2 | Volume slider | Glanceable state |
| 3 | Separator | Layout |
| 4 | Bandwidth label | Glanceable state |
| 5 | Bandwidth sparkline | Glanceable state |
| 6 | Separator | Layout |
| 7 | **Hamburger menu** | Actions (new) |
| 8 | Gaps badge | Notification (unchanged) |
| 9 | Transient status msg | Status (unchanged) |

The hamburger icon sits just to the left of the Gaps badge
on screen (to the right of the bandwidth area), forming a
compact right-side cluster.

### Step 3: Verify F8 shortcut is undisturbed

Confirm that the F8 handling at `app.rs:1426-1432` is
untouched — it must continue to call
`self.open_screenshot_dialog()` independently of the menu.
No code change needed; this is a review checkpoint.

### Step 4: Update README.md

Lines 22, 28, 29, 30, 31 reference clicking buttons "in the
status bar". Update them to use "Menu" phrasing:

| Line | Current text | New text |
|------|-------------|----------|
| 22 | `Press F8 or click "Screenshot" in the status bar` | `Press F8 or use Menu -> Screenshot` |
| 28 | `Press F12 or click "Report"` | `Press F12 or use Menu -> Report` |
| 29 | `Press F11 or click "Traffic"` | `Press F11 or use Menu -> Traffic` |
| 30 | `Click "USB" in the status bar` | `Use Menu -> USB` |
| 31 | `Click "Folders" in the status bar` | `Use Menu -> Folders` |

Do **not** change the Gaps reference on line 24 — Gaps
remains a status-bar button.

### Step 5: Update ARCHITECTURE.md

`ARCHITECTURE.md` references status-bar buttons in several
places:

- Line 549: `"USB" status bar button` -> `Menu -> USB`
- Line 675: `"Folders" status bar button` -> `Menu -> Folders`
- Line 875: `clicking the **Report** button in the status bar`
  -> `using **Menu -> Report**`
- Line 930: `clicking the **Traffic** button in the status bar`
  -> `using **Menu -> Traffic**`

### Step 6: Update AGENTS.md

`AGENTS.md` has no direct "add new features as status-bar
buttons" guidance, but references exist:

- Line 192: `always-visible Gaps: N status-bar` — unchanged
  (Gaps stays)
- Line 357: `always-visible Gaps: N button in the bottom
  status` — unchanged

No changes needed unless the sub-agent finds additional
references during implementation.

### Step 7: Run validation

```bash
pre-commit run --all-files
cargo test --workspace
```

Fix any rustfmt, clippy, or test failures before considering
the phase complete.

## Files to modify

| File | Changes |
|------|---------|
| `ryll/src/app.rs` | Replace five action buttons (lines 1570-1608) with `menu_button` call |
| `README.md` | Update five status-bar button references to Menu phrasing |
| `ARCHITECTURE.md` | Update four status-bar button references to Menu phrasing |

## Risks and mitigations

- **`menu_button` API mismatch**: egui's `menu_button` is
  stable and well-documented. If the import path differs
  from `egui::menu::menu_button`, check `egui::menu` module
  docs. The function signature is
  `menu_button(ui, title, add_contents)`.

- **Checkbox auto-close**: `ui.checkbox` in an egui menu does
  **not** close the menu by default, which is the desired
  behaviour for toggles. Verify this in testing — if it
  auto-closes, wrap in a `ui.menu_item_with_response` or
  suppress via `ui.set_close_menu_on_click(false)`.

- **Menu placement**: In a right-to-left layout, the menu
  popup should appear below the `☰` button. egui handles this
  automatically. If the popup is clipped by the window edge,
  egui will reposition it. No special handling needed.

## Success criteria

- The five action buttons (Traffic / USB / Folders /
  Screenshot / Report) are no longer in the layout block.
- A `menu_button("☰", ...)` is in the layout block with the
  five entries inside.
- Side-panel checkboxes reflect open/closed state.
- F8 still triggers the screenshot flow (unchanged wiring).
- The Gaps badge and the bug-report transient message are
  still in the layout block, unchanged.
- `pre-commit run --all-files` passes.
- `cargo test --workspace` passes.
- README.md and ARCHITECTURE.md reference Menu instead of
  status-bar buttons for the five migrated actions.

## Sub-agent brief

**Effort**: medium | **Model**: sonnet | **Isolation**: none

Execute Steps 1-7 above in order. The code change is
concentrated in `ryll/src/app.rs` lines 1570-1608 — replace
those lines with the `menu_button` block from Step 1. Then
update documentation per Steps 4-5. Run validation per Step 7.
