# Phase 8: Documentation

Parent plan: [PLAN-usb-ui.md](/components/ryll/plans/PLAN-usb-ui/)

## Goal

Update project documentation to describe the new
interactive USB device management UI. All four
documentation files need updates.

## Detailed steps

### Step 1: Update README.md

The features list (top of the file) currently mentions:

> USB device redirection - Forward physical USB devices
> or present RAW disk images as virtual USB mass storage
> devices via `--usb-disk`

This is CLI-focused. Expand to mention the GUI panel:

> USB device redirection - Forward physical USB devices
> or present RAW disk images as virtual USB mass storage
> devices. Interactive USB panel in the GUI for device
> enumeration, connect/disconnect, and adding disk images
> at runtime. CLI flags (`--usb-disk`, `--usb-disk-ro`)
> for headless/scripted use.

The existing features list mentions "Bug reports" and
"Live traffic viewer" with their status bar buttons.
Add a similar line for USB:

> USB device management - Status bar "USB" button opens
> a side panel for browsing available devices, connecting
> physical USB devices or virtual disk images, and
> monitoring connection status

### Step 2: Update ARCHITECTURE.md

The "USB Redirection" section (around line 292) describes
the protocol layers, device backends, and CLI usage. Add
a "GUI Components" subsection after the existing content:

```
### GUI Components

The USB panel is a right-side panel toggled by the "USB"
status bar button, rendered alongside the traffic viewer
panel (both use `egui::SidePanel::right` with different
IDs).

**State tracking on RyllApp:**

- `usb_tx` — mpsc sender to the UsbredirChannel, created
  in `RyllApp::new()` and threaded through
  `run_connection()`. Mirrors the `input_tx` pattern.
- `usb_channel_ready` — set when `UsbChannelReady` event
  arrives, cleared on usbredir channel disconnect.
- `usb_connecting` / `usb_disconnecting` — operation in
  progress flags, cleared on success/failure events.
- `usb_device_description` — set by `UsbDeviceConnected`,
  cleared by `UsbDeviceDisconnected` and channel
  disconnect.
- `usb_available_devices` — enumerated device list,
  refreshed on panel open and via Refresh button.
- `usb_virtual_disks` — session-scoped virtual disk
  paths from CLI flags and runtime additions.

**Command flow:**

The GUI sends identity-based `UsbCommand` variants
(`ConnectPhysical { bus, address }`,
`ConnectVirtualDisk { path, read_only }`,
`DisconnectDevice`) via `usb_tx`. The channel handler
does async device lookup and open in its tokio context,
sending `UsbDeviceConnected`, `UsbDeviceDisconnected`,
or `UsbConnectFailed` events back to the app.

**File picker:**

The "Add Disk..." button spawns `rfd::FileDialog` on a
background thread. The result is polled via
`std::sync::mpsc::try_recv()` each frame. Selected files
are validated and added to the session's virtual disk
list.

**Bug report integration:**

USB errors show a "Report this as a bug" button that
opens the bug report dialog pre-populated with
`BugReportType::Usb`, which captures the usbredir
channel's pcap traffic.
```

### Step 3: Update AGENTS.md

In the "Code Organisation" section, the `usb/` module
description mentions the trait, real device, and virtual
MSC. No change needed there — the USB panel lives in
`app.rs`, not a separate module.

In "Key Design Decisions", add an entry:

> **USB panel uses identity-based commands.** The GUI
> sends device identity (bus/address for physical,
> path/read-only for virtual) rather than pre-opened
> device handles. The channel handler does async device
> lookup and open. This avoids async operations in the
> synchronous egui render loop and keeps device lifecycle
> management co-located in the channel handler.

### Step 4: Update docs/configuration.md

The "USB Device Redirection" section describes CLI flags
and QEMU setup. Add a "USB Panel (GUI Mode)" subsection:

```
### USB Panel (GUI Mode)

In GUI mode, click the **USB** button in the status bar
to open the USB device management panel. The panel shows:

- **Channel status** — whether a usbredir channel is
  available from the SPICE server.
- **Connected device** — the currently redirected device
  with elapsed connection time.
- **Device list** — all available USB devices (physical
  and virtual). Each device has a Connect or Disconnect
  button.
- **Add Disk...** — opens a native file picker to add a
  RAW disk image as a virtual USB device for the current
  session. A "Read-only" checkbox controls write access.
- **Error display** — connection failures appear in red
  with a Dismiss button and a "Report this as a bug"
  button for integrated bug reporting.

The panel can be open simultaneously with the Traffic
viewer panel. Devices added via the panel persist for the
session but are not saved across restarts — use
`--usb-disk` for persistent configuration.

Physical USB device passthrough requires the host to have
accessible USB devices (appropriate permissions, not
claimed by a kernel driver). The panel enumerates
whatever `nusb` can see — if the list is empty, check
host USB permissions.
```

Also add "USB" to the keyboard shortcuts section if a
shortcut exists (currently there is no keyboard shortcut
— the status bar button is the only way to open the
panel).

## Files changed

| File | Change |
|------|--------|
| `README.md` | Expand USB feature description to include GUI panel |
| `ARCHITECTURE.md` | Add "GUI Components" subsection to USB Redirection section |
| `AGENTS.md` | Add design decision about identity-based USB commands |
| `docs/configuration.md` | Add "USB Panel (GUI Mode)" subsection |

## Testing

- No code changes, so no build/test needed.
- `pre-commit run --all-files` — verify no lint issues
  (markdown files are not linted by the current hooks,
  but check anyway).
- Review each file for consistency with existing style
  and content.

## Back brief

This phase updates four documentation files to describe
the USB panel UI. README gets an expanded feature line,
ARCHITECTURE gets a GUI Components subsection covering
state tracking and command flow, AGENTS gets a design
decision about identity-based commands, and configuration
docs get a full panel usage section.
