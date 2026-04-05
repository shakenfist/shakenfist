# Phase 3: USB panel and status bar button

Parent plan: [PLAN-usb-ui.md](/components/ryll/plans/PLAN-usb-ui/)

## Goal

Add a "USB" button to the status bar and a toggleable
right-side panel. After this phase, clicking "USB" in
the status bar opens/closes a panel with a heading and
placeholder content. No device enumeration or controls
yet — that comes in phases 4 and 5.

## Background

### Render order in app.rs

egui requires side panels and top/bottom panels to be
rendered before `CentralPanel` (which takes all remaining
space). The current render order in `update()` is:

1. Bottom panel (stats bar) — line 733
2. Traffic viewer side panel (conditional) — line 841
3. Bug report dialog (conditional modal) — line 1059
4. Region selection overlay (conditional) — line 1141
5. Central panel (display surface) — line 928

The USB side panel should be rendered at step 2, alongside
the traffic viewer. egui supports multiple side panels —
they stack horizontally, each taking its width from the
remaining space before `CentralPanel`.

### Status bar button pattern

The Traffic and Report buttons are in the right-to-left
layout section of the status bar (line 820-828):

```rust
ui.separator();
if ui.small_button("Traffic").clicked() {
    self.show_traffic_viewer = !self.show_traffic_viewer;
}
if ui.small_button("Report").clicked() {
    self.show_bug_dialog = true;
    self.bug_report_type = BugReportType::Display;
    self.bug_description.clear();
}
```

The USB button goes between Report and Traffic (since
right-to-left layout renders from right to left, it
will appear between them visually).

### Panel ID uniqueness

Each side panel needs a unique string ID. The traffic
viewer uses `"traffic_viewer"`. The USB panel will use
`"usb_panel"`.

## Detailed steps

### Step 1: Add show_usb_panel field to RyllApp

In `src/app.rs`, add to `struct RyllApp`:

```rust
// USB panel state
show_usb_panel: bool,
```

Initialise it to `false` in `RyllApp::new()`.

### Step 2: Add USB button to the status bar

In the right-to-left layout section of the status bar,
add the USB button between the existing Traffic and
Report buttons:

```rust
if ui.small_button("Traffic").clicked() {
    self.show_traffic_viewer = !self.show_traffic_viewer;
}
if ui.small_button("USB").clicked() {
    self.show_usb_panel = !self.show_usb_panel;
}
if ui.small_button("Report").clicked() {
    ...
}
```

In the right-to-left layout, items render right-to-left,
so the visual order will be (from left): ... | Report |
USB | Traffic | sparkline | bandwidth label. This puts
USB between the two existing buttons, which groups the
panel-toggle buttons (Traffic, USB) together.

### Step 3: Add USB side panel rendering

After the traffic viewer side panel block (around line
926), add:

```rust
// USB device management panel (conditional)
if self.show_usb_panel {
    egui::SidePanel::right("usb_panel")
        .default_width(300.0)
        .show(ctx, |ui| {
            ui.heading("USB Devices");
            ui.separator();

            // Channel status
            if self.usb_channel_ready {
                ui.label("Channel: Ready");
            } else {
                ui.colored_label(
                    egui::Color32::GRAY,
                    "Channel: Not available",
                );
            }

            // Connected device (if any)
            if let Some(ref desc) = self.usb_device_description {
                ui.separator();
                ui.label(format!("Connected: {}", desc));
            }

            ui.separator();
            ui.colored_label(
                egui::Color32::GRAY,
                "Device list and controls coming soon.",
            );
        });
}
```

This renders before `CentralPanel` as required. The
panel shows:

- Heading "USB Devices"
- Channel readiness status
- Currently connected device (if any)
- Placeholder text for future phases

The `default_width(300.0)` gives a reasonable panel
width. The user can resize it by dragging the edge
(egui built-in behaviour).

### Step 4: Handle panel ordering with traffic viewer

If both panels are open, egui stacks them. The panel
rendered first (in code order) gets the rightmost
position. Currently the traffic viewer renders first.
The USB panel should render after it so it appears to
the left of the traffic viewer (closer to the content).

Ordering:

1. Traffic viewer side panel (`"traffic_viewer"`)
2. USB panel (`"usb_panel"`)
3. Central panel

This means the USB panel code goes after the traffic
viewer block, before `CentralPanel`. Both panels are
independently closeable.

## Files changed

| File | Change |
|------|--------|
| `src/app.rs` | Add `show_usb_panel` field to `RyllApp`; add "USB" button to status bar; add `SidePanel::right("usb_panel")` block with placeholder content |

## What is NOT in scope

- Device enumeration (phase 4).
- Connect/disconnect buttons (phase 5).
- Virtual disk addition (phase 6).
- Any changes to `ChannelEvent` or `UsbCommand`.

## Testing

- `make build` — compiles without errors.
- `make test` — all existing tests pass.
- `pre-commit run --all-files` — rustfmt and clippy
  clean.
- Manual: run ryll, verify "USB" button appears in the
  status bar, clicking it toggles a side panel with the
  heading and placeholder text. Verify Traffic and USB
  panels can be open simultaneously.

## Back brief

This phase adds a "USB" button to the status bar and a
right-side panel toggled by that button, following the
traffic viewer pattern. The panel shows channel status,
connected device info, and placeholder text. No device
enumeration or interactive controls — just the shell
that later phases will populate.
