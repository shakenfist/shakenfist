# Interactive USB device management UI

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, USB,
usbredir protocol, nusb), research as needed to give a
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
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. ... | PLAN-usb-ui-phase-01-foo.md | Not started |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll is a Rust SPICE VDI test client with a full USB
redirection stack implemented across 10 phases
(PLAN-usb-redir.md). The current USB functionality works
but is entirely CLI-driven:

- `--usb-disk <PATH>` and `--usb-disk-ro <PATH>` present
  RAW disk images as virtual USB mass storage devices.
- Virtual disks are auto-connected when the usbredir
  channel's hello exchange completes.
- Physical USB devices are enumerable via `nusb` but have
  no interactive way to select or connect them.
- The GUI shows a static "USB: ..." label in the status
  bar when a device is connected, but offers no controls.

The codebase has significant infrastructure already in
place for interactive USB management:

- `UsbCommand` enum (`ConnectDevice`, `DisconnectDevice`)
  in `src/channels/mod.rs` — ready but the sender
  (`_usb_tx`) is created and immediately dropped in
  `run_connection()` at `src/app.rs:1457`.
- `UsbDeviceInfo` struct with `label()` method for UI
  display in `src/usb/mod.rs`.
- `enumerate_devices()` function that returns both physical
  and virtual devices.
- `ChannelEvent::UsbDevicesChanged(Vec<UsbDeviceInfo>)` —
  defined but marked `#[allow(dead_code)]`.
- `DeviceBackend` enum dispatch supporting both
  `RealDevice` and `VirtualMsc` backends.

The GUI already has two status bar buttons as patterns to
follow:

1. **"Traffic"** button — toggles a right-side panel
   (`show_traffic_viewer`) displaying live protocol
   messages with channel filtering.
2. **"Report"** button — opens a modal dialog for bug
   report generation with type selection, description
   field, and optional region selection.

Both patterns are in `src/app.rs` (lines 791-798) and
demonstrate egui's immediate-mode two-pass pattern for
avoiding borrow checker issues with closures.

### Threading model challenge

The core challenge is that `run_connection()` runs in a
separate thread (GUI mode) or tokio task (headless mode),
while the egui render loop runs on the main thread. The
`usb_tx` sender must cross this boundary. Currently the
`input_tx` sender for keyboard/mouse events already
demonstrates this pattern — it is created in `RyllApp::new()`
and stored as `self.input_tx`, while `input_rx` is moved
into the connection thread. The same approach works for USB:
create the `(usb_tx, usb_rx)` channel in `RyllApp::new()`,
store `usb_tx` on the app, and pass `usb_rx` through to
`run_connection()` and into `UsbredirChannel`.

### SPICE protocol constraint

Each usbredir channel supports exactly one device at a time.
The server may advertise multiple usbredir channels (one per
available USB slot), but ryll currently only handles the
first one. This means the UI should support connecting /
disconnecting one device at a time. Supporting multiple
simultaneous redirections (multiple usbredir channels) is
deferred to future work.

## Mission and problem statement

Add an interactive USB device management UI to ryll via a
"USB" button on the status bar (alongside the existing
Traffic and Report buttons). Clicking this button opens a
panel or dialog where users can:

1. **See available devices** — enumerate both physical USB
   devices on the host and configured virtual devices
   (RAW disk images from `--usb-disk` flags).
2. **Connect a device** — select a device and redirect it
   to the VM through the usbredir channel.
3. **Disconnect the current device** — cleanly detach the
   redirected device.
4. **See connection status** — which device is currently
   connected, transfer statistics.
5. **Add virtual disks at runtime** — browse for or type a
   path to a RAW disk image and add it as a virtual device
   without restarting ryll.

This serves the project's purpose as a test client: testers
need to attach, detach, and switch USB devices during a
session without restarting. It also makes the existing USB
functionality discoverable — currently you must know about
the CLI flags.

## Open questions

1. **Panel vs dialog?** The Traffic viewer uses a
   right-side panel. The Bug Report uses a modal dialog.
   For USB device management, a right-side panel (like
   Traffic) seems more natural — it stays open while you
   interact with the VM and shows live status.
   **Recommendation**: use a right-side panel, toggled by
   the "USB" status bar button. If both Traffic and USB
   panels are open, they stack (egui handles this).

2. **Device refresh strategy?** `enumerate_devices()` calls
   `nusb::list_devices()` which is synchronous. Calling it
   every frame would block the UI. Options:
   (a) Poll on a timer (e.g. every 2 seconds) in a
       background task.
   (b) Manual refresh button.
   (c) Use nusb's hotplug API if available.
   **Recommendation**: start with (b) manual refresh
   button plus automatic enumeration when the panel opens.
   Add timer-based polling in a later phase if needed.
   nusb does support hotplug watchers on Linux, but adding
   that adds complexity we can defer.

3. **Should the panel replace the static status bar label?**
   Currently when a device is connected, "USB: RAW Disk:
   /path" appears in the status bar. With the panel, this
   is redundant but still useful as a quick glance
   indicator. **Recommendation**: keep the status bar label
   for quick visibility; the panel provides detailed
   management.

4. **File picker for virtual disks?** egui doesn't have a
   native file picker. Options:
   (a) Text input field where the user types a path.
   (b) Use `rfd` (Rust File Dialog) crate for native OS
       file picker.
   (c) Text input only, defer file picker to future work.
   **Recommendation**: use `rfd` from the start. This
   panel is GUI-only — headless users already have
   `--usb-disk` CLI flags. Typing a full path is a poor
   experience for the only users who will see this UI.
   `rfd` provides async native file dialogs on Linux
   (GTK/portal), macOS, and Windows, which aligns with
   ryll's cross-platform packaging targets. Add a
   `rfd` dependency with the `gtk3` feature on Linux.

5. **Multiple usbredir channels?** If the server advertises
   multiple usbredir channels, should the UI let users
   manage devices on each one independently?
   **Recommendation**: defer to future work. Currently
   ryll connects at most one usbredir channel. The UI
   should handle this gracefully (grey out "Connect" if
   no usbredir channel is available, show an error if the
   channel isn't ready).

6. **Physical device permissions?** In containerised
   environments (like dev containers), USB device access
   may require specific permissions or device pass-through
   from the host. Should the UI show a warning?
   **Recommendation**: show the enumeration result as-is.
   If `nusb::list_devices()` returns an empty list or
   errors, display that. Don't try to diagnose permissions
   — just surface what happens.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Fix enumerate_physical bus field | [PLAN-usb-ui-phase-01-bus-fix.md](/components/ryll/plans/PLAN-usb-ui-phase-01-bus-fix/) | Complete |
| 2. Wire usb_tx and channel events | [PLAN-usb-ui-phase-02-wire-tx.md](/components/ryll/plans/PLAN-usb-ui-phase-02-wire-tx/) | Complete |
| 3. USB panel and status bar button | [PLAN-usb-ui-phase-03-panel.md](/components/ryll/plans/PLAN-usb-ui-phase-03-panel/) | Complete |
| 4. Device enumeration display | [PLAN-usb-ui-phase-04-enumerate.md](/components/ryll/plans/PLAN-usb-ui-phase-04-enumerate/) | Complete |
| 5. Connect and disconnect controls | [PLAN-usb-ui-phase-05-connect.md](/components/ryll/plans/PLAN-usb-ui-phase-05-connect/) | Complete |
| 6. Add virtual disk at runtime | [PLAN-usb-ui-phase-06-add-disk.md](/components/ryll/plans/PLAN-usb-ui-phase-06-add-disk/) | Complete |
| 7. Status feedback and polish | [PLAN-usb-ui-phase-07-polish.md](/components/ryll/plans/PLAN-usb-ui-phase-07-polish/) | Complete |
| 8. Documentation | [PLAN-usb-ui-phase-08-docs.md](/components/ryll/plans/PLAN-usb-ui-phase-08-docs/) | Complete |

### Phase 1: Fix enumerate_physical bus field

Pre-existing bug: in `src/usb/real.rs:98-100`,
`enumerate_physical()` sets both `bus` and `address` to
`info.device_address()`:

```rust
source: DeviceSource::Physical {
    bus: info.device_address(),   // BUG: should be busnum()
    address: info.device_address(),
},
```

Phase 5 uses `ConnectPhysical { bus, address }` to re-find
a device from the enumeration list. If bus is wrong, the
re-lookup will fail silently or match the wrong device.

Fix:
- Change `bus: info.device_address()` to
  `bus: info.busnum()` (nusb provides this method).
- Verify the `DeviceSource::Physical` fields are correct
  by logging a few real devices if USB hardware is
  available, or by reading nusb docs/source to confirm
  the API.
- Update the unit test `device_info_label_physical` if
  needed (it uses synthetic data so it won't break, but
  confirm).

This is a small fix but must land before any code that
relies on bus/address for device identity.

### Phase 2: Wire usb_tx and channel events

Thread the USB command channel sender from `RyllApp` through
to `run_connection()`, following the same pattern used for
`input_tx`/`input_rx`.

Currently in `run_connection()` (app.rs:1457):
```rust
let (_usb_tx, usb_rx) = mpsc::channel(16);
```

This creates the channel locally and drops the sender. The
fix is to create the channel in `RyllApp::new()` instead,
store `usb_tx` as a field on `RyllApp`, and pass `usb_rx`
through the same path as `input_rx`:

- Add `usb_tx: Option<mpsc::Sender<UsbCommand>>` field to
  `RyllApp`.
- Create `(usb_tx, usb_rx) = mpsc::channel(16)` in
  `RyllApp::new()`.
- Add `usb_rx: mpsc::Receiver<UsbCommand>` parameter to
  `run_connection()`.
- Pass `usb_rx` into `UsbredirChannel::new()` (replacing
  the locally-created one).
- Store `usb_tx` on `RyllApp` for use by the UI in later
  phases.

Also wire the same for headless mode in `run_headless()`.

Additionally, add the following state tracking fields to
`RyllApp`:

- `usb_channel_ready: bool` — set to `true` when
  `ChannelEvent::UsbChannelReady` is received. The UI
  needs this to know when it's safe to send connect
  commands.
- `usb_connecting: bool` — tracks whether a connect
  operation is in progress (set true when user clicks
  Connect, cleared on success/failure events). Prevents
  double-clicks and provides spinner state for later
  phases.

Add two new `ChannelEvent` variants:

- `UsbDeviceDisconnected` — sent when a device has been
  detached (either by user command completing or
  server-initiated disconnect). Send this from
  `UsbredirChannel::disconnect_device()` and when the
  channel detects a server-side disconnect.
- `UsbConnectFailed(String)` — sent when a connect
  command fails (device open error, permission denied,
  etc.). This must exist from the start so the UI never
  gets stuck in "Connecting..." state with no way to
  recover.

Update `RyllApp::process_events()` to handle all USB
events, including the full usbredir channel disconnect
path:

```rust
ChannelEvent::UsbDeviceDisconnected => {
    self.usb_device_description = None;
    self.usb_connecting = false;
}
ChannelEvent::UsbConnectFailed(err) => {
    self.usb_connecting = false;
    self.usb_error_message = Some(err);
}
ChannelEvent::Disconnected(channel) => {
    if channel == ChannelType::Main {
        self.connected = false;
    }
    if channel == ChannelType::Usbredir {
        self.usb_channel_ready = false;
        self.usb_device_description = None;
        self.usb_connecting = false;
    }
}
```

The existing `Disconnected` handler only checks for
`ChannelType::Main`. It must also handle
`ChannelType::Usbredir` to reset all USB UI state when
the channel drops (e.g. server restart, network failure).
Without this, the UI would show stale "Channel: Ready"
and "Connected" state after the channel is gone.

### Phase 3: USB panel and status bar button

Add a "USB" button to the status bar (between "Report" and
the bandwidth sparkline) and a toggleable right-side panel,
following the Traffic viewer pattern.

- Add `show_usb_panel: bool` field to `RyllApp`.
- Add the "USB" button in the status bar's right-to-left
  layout section (app.rs, near line 794):
  ```rust
  if ui.small_button("USB").clicked() {
      self.show_usb_panel = !self.show_usb_panel;
  }
  ```
- Add a `egui::SidePanel::right("usb_panel")` block
  (similar to the traffic viewer panel) rendered when
  `show_usb_panel` is true. Initially show a heading
  "USB Devices" and a placeholder message.
- If both Traffic and USB panels are open, egui handles
  stacking them. The USB panel should use a different panel
  ID ("usb_panel" vs "traffic_viewer") so they coexist.

### Phase 4: Device enumeration display

Populate the USB panel with a list of available devices.

- Add `usb_available_devices: Vec<UsbDeviceInfo>` field to
  `RyllApp`.
- Add `usb_virtual_disks: Vec<(PathBuf, bool)>` field to
  track configured virtual disks (from CLI flags) for
  re-enumeration.
- When the USB panel opens (transitions from hidden to
  shown), enumerate devices by calling
  `usb::enumerate_devices()` and store the result. This
  is a blocking call but fast (< 100ms typically).
- Add a "Refresh" button in the panel header that
  re-enumerates.
- Display each device in a list:
  - Physical devices: icon/label with name, VID:PID,
    bus/address, speed.
  - Virtual devices: label with "Virtual Disk", file
    path, RO indicator.
- Show the usbredir channel status at the top:
  "Channel: Ready" / "Channel: Not available" based on
  `usb_channel_ready`.
- If a device is currently connected, highlight it in the
  list (e.g. bold label or coloured indicator).

### Phase 5: Connect and disconnect controls

Add interactive connect/disconnect functionality. This
phase has the most moving parts, so each concern is
addressed explicitly below.

#### UsbCommand redesign

Replace the existing `UsbCommand` variants with
identity-based variants. The GUI sends device identity;
the channel handler (which has an async context) does
the async open. This avoids needing async in the egui
render loop.

```rust
pub enum UsbCommand {
    /// Connect a physical USB device by bus/address.
    ConnectPhysical { bus: u8, address: u8 },
    /// Connect a virtual mass storage disk image.
    ConnectVirtualDisk { path: PathBuf, read_only: bool },
    /// Disconnect the currently connected device.
    DisconnectDevice,
}
```

Remove the old `ConnectDevice(Box<DeviceBackend>)` variant.
The auto-connect code in the hello handler calls
`self.connect_device(backend)` directly and does not go
through `UsbCommand`, so removing the old variant does not
break auto-connect.

#### Physical device re-lookup in the channel handler

When `handle_usb_command()` receives `ConnectPhysical`,
it must re-enumerate USB devices via nusb to find the
matching one, because nusb device handles are not
`Send`/`Clone` across the channel boundary. The handler:

1. Calls `nusb::list_devices().wait()` to get the current
   device list.
2. Iterates to find the device matching `(bus, address)`
   using `info.busnum()` and `info.device_address()`.
3. If found, calls `RealDevice::open(&info).await`.
4. If not found (device unplugged between enumeration and
   connect), sends `ChannelEvent::UsbConnectFailed(
   "Device not found (bus X addr Y) — may have been
   unplugged")` and returns.
5. If `open()` fails (permissions, busy, etc.), sends
   `UsbConnectFailed` with the error message.
6. On success, proceeds to `connect_device(backend)` as
   normal.

This depends on phase 1's bus field fix — without correct
bus numbers, the re-lookup would match the wrong device
or fail spuriously.

#### Disconnect-before-connect behaviour

When a connect command arrives and a device is already
connected, the channel handler must disconnect the current
device first. This handles two scenarios:

- User clicks "Connect" on device B while device A is
  already connected (the UI in this phase disables other
  Connect buttons when a device is connected, but
  auto-connect from `--usb-disk` may have already
  attached a device before the UI was involved).
- Race condition where two connect commands arrive in
  quick succession.

In `handle_usb_command()`, before opening the new device:

```rust
UsbCommand::ConnectPhysical { bus, address } => {
    // Disconnect existing device if any
    if self.backend.is_some() {
        self.disconnect_device().await?;
    }
    // Now open and connect the new device
    ...
}
```

The same pattern applies to `ConnectVirtualDisk`. The
`disconnect_device()` method already sends
`usb_redir_device_disconnect` to the server and drops the
backend, so this is safe.

#### Error feedback

`UsbConnectFailed(String)` was added to `ChannelEvent` in
phase 2. The channel handler sends it when:

- Physical device not found during re-lookup.
- `RealDevice::open()` fails (permission denied, device
  busy, I/O error).
- `VirtualMsc::open()` fails (file not found, permission
  denied, file too small).

The app's `process_events()` handler (also from phase 2)
clears `usb_connecting` and stores the error message.
This phase adds UI rendering of that error:

- In the USB panel, show `usb_error_message` in red text
  below the device list.
- Clear the error when the user initiates a new action
  (Connect, Disconnect, Refresh).

Without this error path in the same phase as the connect
buttons, a failed connection would leave the UI stuck in
"Connecting..." state with no recovery.

#### UI controls

- For each device in the list, show a "Connect" button
  (if not currently connected) or "Disconnect" button
  (if this device is currently connected).
- Grey out / disable buttons when:
  - No usbredir channel is available
    (`!usb_channel_ready`).
  - A connect/disconnect operation is in progress
    (`usb_connecting`).
- On "Connect" click:
  - Set `usb_connecting = true`.
  - Clear `usb_error_message`.
  - For physical devices: send
    `UsbCommand::ConnectPhysical { bus, address }` via
    `usb_tx`.
  - For virtual devices: send
    `UsbCommand::ConnectVirtualDisk { path, read_only }`
    via `usb_tx`.
- On "Disconnect" click:
  - Set `usb_connecting = true` (reuse for "operation in
    progress" state).
  - Send `UsbCommand::DisconnectDevice` via `usb_tx`.
- Show "Connecting..." or "Disconnecting..." indicator
  while `usb_connecting` is true.
- When `UsbDeviceConnected`, `UsbDeviceDisconnected`, or
  `UsbConnectFailed` arrives, `usb_connecting` is cleared
  (handled in `process_events()` from phase 2).

### Phase 6: Add virtual disk at runtime

Allow users to add a new virtual disk image from within the
USB panel without restarting ryll.

- Add `rfd` dependency to `Cargo.toml`. `rfd` (Rusty File
  Dialog) provides native async file dialogs on all
  platforms ryll targets (Linux via GTK3/xdg-portal, macOS
  via Cocoa, Windows via COM). Use the `gtk3` feature on
  Linux.
- Add an "Add Disk..." button in the USB panel that opens
  a native file picker dialog via `rfd::AsyncFileDialog`.
  Since the egui render loop is synchronous, use the same
  pattern as bug reports: set a flag, spawn the dialog on
  a background thread, receive the result via a one-shot
  channel or by polling a shared `Option` next frame.
  Alternatively, `rfd::FileDialog` (blocking) can be
  called from a `std::thread::spawn` with the result sent
  back via the existing `event_tx` channel as a new
  `ChannelEvent::VirtualDiskSelected(PathBuf)` variant.
- Add a "Read-only" checkbox next to the button (persists
  its state between clicks).
- On file selection:
  - Validate the path is a regular file.
  - Validate file size >= 512 bytes.
  - Warn (but allow) if file size is not a multiple of
    512 bytes. Show the warning in the panel.
  - Add the disk to `usb_virtual_disks` so it appears in
    future enumerations.
  - Re-enumerate the device list immediately.
- Show validation errors inline (red text below the
  button).
- The added virtual disk persists for the session but is
  not saved to CLI args or config files.
- Filter the file dialog to show common disk image
  extensions (`.raw`, `.img`, `.*`) but allow all files.

### Phase 7: Status feedback and polish

Refine the UI with visual polish and edge case handling.
The core event plumbing (`UsbConnectFailed`,
`UsbDeviceDisconnected`, usbredir channel disconnect
handling) was done in phases 2 and 5. This phase focuses
on presentation.

- Show the currently connected device prominently at the
  top of the panel with a green indicator dot or
  coloured background.
- Add connection/disconnection timestamps (store
  `usb_connected_at: Option<Instant>` on `RyllApp`,
  display elapsed time in the panel).
- Improve error message presentation: red background,
  dismiss button, auto-clear after 10 seconds.
- Handle edge cases:
  - User closes USB panel while connecting — connection
    continues in the background, state updates normally
    via `process_events()`, panel shows correct state
    when reopened. No special code needed (egui
    immediate-mode handles this naturally).
  - Multiple rapid Connect/Disconnect clicks — guarded
    by `usb_connecting` flag from phase 2; verify that
    rapid toggling doesn't queue contradictory commands.
    If it does, add a `usb_tx.try_send()` check.
- Ensure the status bar "USB: ..." label updates in sync
  with panel state (both are driven by
  `usb_device_description`, so they should already be
  consistent — verify and fix if not).
- Visual consistency pass: match the panel styling to the
  Traffic viewer (font sizes, spacing, separator usage).

### Phase 8: Documentation

Update project documentation to describe the new USB
device management UI.

- Update `README.md` feature list to mention interactive
  USB device management.
- Update `ARCHITECTURE.md` to describe the USB command
  channel flow (app -> usb_tx -> UsbredirChannel).
- Update `AGENTS.md` if new module structure is added.
- Update `docs/configuration.md` to describe the USB
  panel and its capabilities alongside the existing
  `--usb-disk` CLI flags.
- Add a "USB Device Management" section to `docs/` with
  screenshots or descriptions of the panel UI, showing
  how to connect physical devices, add virtual disks,
  and monitor connection status.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* A "USB" button appears on the status bar alongside
  Traffic and Report.
* Clicking the USB button opens a right-side panel showing
  available USB devices (physical and virtual).
* Users can connect a physical USB device or virtual disk
  image by clicking "Connect" in the panel.
* Users can disconnect the current device by clicking
  "Disconnect".
* Users can add a new virtual disk image path at runtime
  without restarting ryll.
* The panel shows clear status: channel readiness,
  connected device, errors.
* The `usb_tx` channel is properly wired from `RyllApp`
  through to `UsbredirChannel`, not dropped.
* Existing CLI-based USB functionality (`--usb-disk`,
  `--usb-disk-ro`, auto-connect) continues to work
  unchanged.
* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* New code follows existing patterns: channel handler
  structure, message parsing via `byteorder`, async tasks
  via tokio, event communication via mpsc channels.
* There are unit tests for new logic, and the existing tests
  still pass (`make test`).
* Lines are wrapped at 120 characters, single quotes for
  Rust strings where applicable.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have been
  updated if the change adds or modifies channels, message
  types, or UI components.
* Documentation in `docs/` has been updated to describe
  the new USB device management panel.

### Future work

- **nusb hotplug watcher**: automatically refresh the
  device list when USB devices are plugged in or removed,
  instead of requiring manual refresh. nusb supports this
  on Linux via `nusb::watch_devices()`.
- **Multiple simultaneous redirections**: support multiple
  usbredir channels so users can connect several devices
  at once. Requires tracking multiple `usb_tx` senders
  and showing per-channel device management.
- **Device filtering**: allow users to filter the device
  list by vendor/product ID, class, or name.
- **Auto-connect rules**: UI for configuring auto-connect
  policies (e.g. "always redirect devices matching
  VID:PID X:Y").
- **Transfer statistics in panel**: show bytes in/out,
  active endpoints, and throughput for the connected
  device.
- **Keyboard shortcut**: add a keyboard shortcut to toggle
  the USB panel (e.g. Ctrl+U), similar to how other
  panels might be toggled.
- **Persist virtual disk paths**: save added virtual disk
  paths to a config file so they survive across sessions.
- **Consistent error reporting in the status bar**: ryll
  currently reports errors inconsistently — some channel
  errors appear at the top of the UI, while USB status
  lives in the bottom status bar. Consolidate all
  transient status and error reporting into the status
  bar for consistency. This would affect the existing
  connection error display, channel disconnect messages,
  and the new USB error feedback.
- **UsbredirSnapshot for bug reports**: add a dedicated
  `UsbredirSnapshot` struct to `ChannelSnapshots` (like
  the existing `DisplaySnapshot`, `InputsSnapshot`, etc.)
  that captures usbredir channel state — connected device
  info, backend type, transfer counters, active endpoints,
  interrupt poll state. Currently `BugReportType::Usb`
  captures pcap traffic but uses an empty JSON object for
  channel state.

### Bugs fixed during this work

- **enumerate_physical bus field** (phase 1): both `bus`
  and `address` in `DeviceSource::Physical` were set to
  `info.device_address()`. The `bus` field should use
  `info.busnum()`. This pre-existing bug from the
  original USB implementation (PLAN-usb-redir phase 4)
  would have caused physical device re-lookup by identity
  to fail or match the wrong device.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
