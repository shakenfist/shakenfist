# Phase 4: Device enumeration display

Parent plan: [PLAN-usb-ui.md](/components/ryll/plans/PLAN-usb-ui/)

## Goal

Populate the USB panel with a list of available devices
(physical and virtual). After this phase, opening the
USB panel shows all available devices with their details,
and a "Refresh" button re-enumerates. No connect/disconnect
controls yet — that comes in phase 5.

## Background

### enumerate_devices API

`usb::enumerate_devices()` in `src/usb/mod.rs:396` takes
`&[(PathBuf, bool)]` (virtual disk paths and read-only
flags) and returns `Vec<UsbDeviceInfo>`. It calls
`real::enumerate_physical()` for host USB devices (via
`nusb::list_devices()`) and appends virtual disk entries.

`UsbDeviceInfo` has a `label()` method that formats the
device for display:
- Physical: `"SanDisk Ultra [0781:5583] (bus 1 addr 5)"`
- Virtual: `"RAW Disk: /tmp/test.raw"` or
  `"RAW Disk: /tmp/test.raw [RO]"`

### Virtual disk paths

The `virtual_disks: Vec<VirtualDiskConfig>` parameter is
passed into `RyllApp::new()` and moved into the connection
thread closure. The app doesn't retain a copy. Phase 4
needs the virtual disk list for re-enumeration, so we need
to clone it before moving it into the thread.

`VirtualDiskConfig` already derives `Clone`, so this is
straightforward. We'll store a `Vec<(PathBuf, bool)>` on
`RyllApp` (matching the `enumerate_devices` signature) by
converting from `VirtualDiskConfig` before the move.

### Enumeration is blocking but fast

`nusb::list_devices().wait()` is synchronous. In practice
it takes < 100ms even with many USB devices. Calling it
from the egui render loop on button click is acceptable.
Polling every frame would not be.

### Identifying the connected device

The panel needs to highlight which device (if any) is
currently connected. The app has
`usb_device_description: Option<String>` which contains
the backend's `description()` string. This matches the
output of `UsbDeviceInfo::label()` for virtual devices
but may differ slightly for physical devices (the backend
description comes from `RealDevice::description()` which
may format differently from `UsbDeviceInfo::label()`).

For now, compare `usb_device_description` against each
device's `label()` to determine which is connected. This
is imprecise but sufficient until phase 5 tracks
connected device identity explicitly.

## Detailed steps

### Step 1: Add fields to RyllApp

In `src/app.rs`, add to `struct RyllApp`:

```rust
// USB device enumeration
usb_available_devices: Vec<UsbDeviceInfo>,
usb_virtual_disks: Vec<(PathBuf, bool)>,
```

Import `UsbDeviceInfo` and `PathBuf`:
- Add `use crate::usb::UsbDeviceInfo;` to imports.
- `PathBuf` is from `std::path::PathBuf` — check if
  already imported; if not, add it.

### Step 2: Retain virtual disk paths in RyllApp::new()

In `RyllApp::new()`, before the `std::thread::spawn`
closure that moves `virtual_disks`, convert and clone:

```rust
let usb_virtual_disks: Vec<(PathBuf, bool)> = virtual_disks
    .iter()
    .map(|d| (d.path.clone(), d.read_only))
    .collect();
```

Then initialise the fields:

```rust
usb_available_devices: Vec::new(),
usb_virtual_disks,
```

### Step 3: Enumerate on panel open

In the USB panel rendering code, detect when the panel
transitions from hidden to shown and enumerate. The
simplest approach: enumerate whenever the device list
is empty and the panel is open. This auto-enumerates
on first open.

Alternatively, enumerate immediately when `show_usb_panel`
becomes true. Since egui is immediate-mode, we can detect
this by checking if the list is empty when the panel
renders:

```rust
if self.show_usb_panel {
    // Auto-enumerate on first open
    if self.usb_available_devices.is_empty() {
        self.usb_available_devices =
            usb::enumerate_devices(&self.usb_virtual_disks);
    }
    ...
}
```

This has a minor issue: if enumeration genuinely returns
zero devices, it will re-enumerate every frame. Fix by
using an `Option<Vec<UsbDeviceInfo>>` instead, where
`None` means "not yet enumerated":

Actually, the simpler approach is fine — just add a
`usb_devices_enumerated: bool` flag:

```rust
usb_devices_enumerated: bool,
```

Set to `false` initially. Set to `true` after first
enumeration. The Refresh button resets it to re-trigger.
When `show_usb_panel && !usb_devices_enumerated`:
enumerate and set the flag.

### Step 4: Add Refresh button

In the panel header, add a Refresh button:

```rust
ui.horizontal(|ui| {
    ui.heading("USB Devices");
    if ui.small_button("Refresh").clicked() {
        self.usb_available_devices =
            usb::enumerate_devices(&self.usb_virtual_disks);
    }
});
```

### Step 5: Render the device list

Replace the placeholder text with a scrollable device
list:

```rust
ui.separator();
egui::ScrollArea::vertical().show(ui, |ui| {
    if self.usb_available_devices.is_empty() {
        ui.colored_label(
            egui::Color32::GRAY,
            "No USB devices found.",
        );
    } else {
        for device in &self.usb_available_devices {
            let is_connected = self
                .usb_device_description
                .as_ref()
                .map_or(false, |d| *d == device.label());

            ui.horizontal(|ui| {
                if is_connected {
                    ui.colored_label(
                        egui::Color32::GREEN,
                        "\u{25CF}",  // filled circle
                    );
                }
                ui.label(device.label());
            });
        }
    }
});
```

Physical devices show their full label (name, VID:PID,
bus/addr). Virtual devices show "RAW Disk: /path [RO]".
Connected devices get a green dot indicator.

### Step 6: Clear device list on panel close

When `show_usb_panel` transitions to false, clear the
enumerated flag so the list is refreshed next time the
panel opens. This ensures the user sees current devices
if hardware changed while the panel was closed.

The simplest way in immediate-mode: don't clear on close.
Instead, the Refresh button handles staleness, and the
auto-enumerate-on-first-open handles the initial case.
Clearing on close would cause a re-enumeration (and
potential brief freeze) every time the panel opens, which
is worse than showing slightly stale data with a Refresh
button available.

Decision: don't clear on close. Rely on Refresh button
and initial auto-enumeration.

## Files changed

| File | Change |
|------|--------|
| `src/app.rs` | Add `usb_available_devices`, `usb_virtual_disks`, `usb_devices_enumerated` fields; retain virtual disk paths in `new()`; auto-enumerate on panel open; add Refresh button; render device list with connected indicator; import `UsbDeviceInfo` |

## What is NOT in scope

- Connect/disconnect buttons (phase 5).
- Adding virtual disks at runtime (phase 6).
- Device speed display or detailed info beyond `label()`.
- Hotplug detection or timer-based re-enumeration.

## Testing

- `make build` — compiles without errors.
- `make test` — all existing tests pass.
- `pre-commit run --all-files` — rustfmt and clippy clean.
- Manual: run ryll, open the USB panel, verify device list
  appears. If `--usb-disk` was specified, virtual devices
  should appear. If USB hardware is accessible, physical
  devices should appear. Click Refresh to re-enumerate.

## Back brief

This phase populates the USB panel with enumerated devices
by calling `usb::enumerate_devices()` on panel open and
via a Refresh button. Virtual disk paths are retained from
CLI flags so they can be included in re-enumeration.
Connected devices are highlighted with a green indicator.
No interactive controls — just a read-only device list.
