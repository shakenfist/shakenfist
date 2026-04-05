# Phase 5: Connect and disconnect controls

Parent plan: [PLAN-usb-ui.md](/components/ryll/plans/PLAN-usb-ui/)

## Goal

Add interactive connect and disconnect buttons to the
USB panel device list. After this phase, users can click
"Connect" on any listed device to redirect it to the VM,
and "Disconnect" to detach the current device. Error
feedback is shown inline in the panel.

## Background

### Current UsbCommand enum

The existing `UsbCommand` in `src/channels/mod.rs:115`:

```rust
pub enum UsbCommand {
    ConnectDevice(Box<DeviceBackend>),
    DisconnectDevice,
}
```

`ConnectDevice` takes an already-opened backend. This
doesn't work for the UI because opening a device
(`RealDevice::open()`, `VirtualMsc::open()`) is async,
and the egui render loop is synchronous. The master plan
specifies replacing this with identity-based variants
where the channel handler does the async open.

### Channel handler's async context

`UsbredirChannel::handle_usb_command()` runs inside a
`tokio::select!` loop and has full async capability.
It already calls `self.connect_device(backend).await`
and `self.disconnect_device().await`. Adding device
lookup and open logic here is natural.

### Device identity

Physical devices are identified by `(bus: u8, address: u8)`
from `DeviceSource::Physical`. The channel handler
re-enumerates via `nusb::list_devices()` to find the
matching device, then calls `RealDevice::open()`.

Virtual devices are identified by `(path: PathBuf,
read_only: bool)` from `DeviceSource::VirtualDisk`. The
handler calls `VirtualMsc::open()`.

### Disconnect-before-connect

If a device is already connected when a connect command
arrives (e.g. auto-connected from `--usb-disk`), the
handler must disconnect it first. The existing
`disconnect_device()` sends `usb_redir_device_disconnect`
and drops the backend.

### Error reporting

`ChannelEvent::UsbConnectFailed(String)` is defined and
handled in `process_events()` (phase 2). The handler
sends it when device lookup or open fails.
`RyllApp.usb_error_message` stores the error for
display.

### The usb_tx sender

`RyllApp.usb_tx` is `Option<mpsc::Sender<UsbCommand>>`,
stored since phase 2. It sends to the `usb_rx` in
`UsbredirChannel`. `try_send()` is appropriate from the
render loop (non-blocking, bounded channel of 16).

## Detailed steps

### Step 1: Redesign UsbCommand

In `src/channels/mod.rs`, replace the `UsbCommand` enum:

```rust
/// Commands sent from the app to the usbredir channel.
pub enum UsbCommand {
    /// Connect a physical USB device by bus/address.
    ConnectPhysical { bus: u8, address: u8 },
    /// Connect a virtual mass storage disk image.
    ConnectVirtualDisk {
        path: std::path::PathBuf,
        read_only: bool,
    },
    /// Disconnect the currently connected device.
    DisconnectDevice,
}
```

Remove the `#[allow(dead_code)]` on the enum (it will
be used now). Remove the `DeviceBackend` import from
`channels/mod.rs` if it's no longer needed there (check
if anything else uses it).

### Step 2: Update handle_usb_command in UsbredirChannel

In `src/channels/usbredir.rs`, rewrite
`handle_usb_command()` to handle the new variants:

```rust
async fn handle_usb_command(
    &mut self,
    cmd: UsbCommand,
) -> Result<()> {
    match cmd {
        UsbCommand::ConnectPhysical { bus, address } => {
            // Disconnect existing device if any
            if self.backend.is_some() {
                self.disconnect_device().await?;
            }

            // Re-enumerate to find the device
            let devices = match nusb::list_devices().wait() {
                Ok(iter) => iter,
                Err(e) => {
                    let msg = format!(
                        "Failed to enumerate USB devices: {}",
                        e,
                    );
                    warn!("usbredir: {}", msg);
                    self.event_tx
                        .send(ChannelEvent::UsbConnectFailed(msg))
                        .await
                        .ok();
                    return Ok(());
                }
            };

            let nusb_info = devices
                .into_iter()
                .find(|d| {
                    d.busnum() == bus
                        && d.device_address() == address
                });

            match nusb_info {
                None => {
                    let msg = format!(
                        "Device not found (bus {} addr {}) \
                         — may have been unplugged",
                        bus, address,
                    );
                    warn!("usbredir: {}", msg);
                    self.event_tx
                        .send(ChannelEvent::UsbConnectFailed(msg))
                        .await
                        .ok();
                }
                Some(info) => {
                    match RealDevice::open(&info).await {
                        Ok(dev) => {
                            let backend =
                                DeviceBackend::Real(dev);
                            self.connect_device(backend)
                                .await?;
                        }
                        Err(e) => {
                            let msg = format!(
                                "Failed to open device \
                                 (bus {} addr {}): {}",
                                bus, address, e,
                            );
                            warn!("usbredir: {}", msg);
                            self.event_tx
                                .send(
                                    ChannelEvent::UsbConnectFailed(
                                        msg,
                                    ),
                                )
                                .await
                                .ok();
                        }
                    }
                }
            }
        }

        UsbCommand::ConnectVirtualDisk {
            path,
            read_only,
        } => {
            // Disconnect existing device if any
            if self.backend.is_some() {
                self.disconnect_device().await?;
            }

            match VirtualMsc::open(path.clone(), read_only)
                .await
            {
                Ok(msc) => {
                    let backend =
                        DeviceBackend::Virtual(msc);
                    self.connect_device(backend).await?;
                }
                Err(e) => {
                    let msg = format!(
                        "Failed to open {}: {}",
                        path.display(),
                        e,
                    );
                    warn!("usbredir: {}", msg);
                    self.event_tx
                        .send(
                            ChannelEvent::UsbConnectFailed(msg),
                        )
                        .await
                        .ok();
                }
            }
        }

        UsbCommand::DisconnectDevice => {
            self.disconnect_device().await?;
        }
    }
    Ok(())
}
```

Add `use crate::usb::real::RealDevice;` to the imports
in usbredir.rs (it's not currently imported — the hello
handler's auto-connect only uses `VirtualMsc`). Also add
`use nusb::MaybeFuture;` if needed for `.wait()`.

### Step 3: Remove dead_code allows

Now that `UsbCommand` variants are used:
- Remove `#[allow(dead_code)]` from the `UsbCommand` enum
  in `channels/mod.rs`.
- Remove `#[allow(dead_code)]` from `UsbConnectFailed` in
  `ChannelEvent` (it's now constructed by the handler).
- Remove `#[allow(dead_code)]` from `usb_tx` field in
  `RyllApp` (it's now read by the panel UI).
- Check if `DeviceBackend` import in `channels/mod.rs` is
  still needed — if `UsbCommand` no longer references it,
  remove the import. Check if `UsbDeviceInfo` is still
  needed (it is, for `UsbDevicesChanged`).

### Step 4: Add connect/disconnect buttons to the USB panel

In `src/app.rs`, in the USB panel's device list
rendering, replace the simple label with a row containing
both the label and a button. Use a two-pass pattern
(collect action, execute after closure) to avoid borrow
issues:

```rust
// Device list
let mut usb_action = None;
egui::ScrollArea::vertical().show(ui, |ui| {
    if self.usb_available_devices.is_empty() {
        ui.colored_label(
            egui::Color32::GRAY,
            "No USB devices found.",
        );
    } else {
        for device in &self.usb_available_devices {
            let label = device.label();
            let is_connected = self
                .usb_device_description
                .as_ref()
                .is_some_and(|d| *d == label);
            let buttons_disabled =
                !self.usb_channel_ready
                    || self.usb_connecting;

            ui.horizontal(|ui| {
                if is_connected {
                    ui.colored_label(
                        egui::Color32::GREEN,
                        "\u{25CF}",
                    );
                    ui.label(&label);
                    if ui
                        .add_enabled(
                            !buttons_disabled,
                            egui::Button::new("Disconnect"),
                        )
                        .clicked()
                    {
                        usb_action =
                            Some(UsbCommand::DisconnectDevice);
                    }
                } else {
                    ui.label(&label);
                    if ui
                        .add_enabled(
                            !buttons_disabled
                                && self
                                    .usb_device_description
                                    .is_none(),
                            egui::Button::new("Connect"),
                        )
                        .clicked()
                    {
                        usb_action = Some(
                            match &device.source {
                                DeviceSource::Physical {
                                    bus,
                                    address,
                                } => UsbCommand::ConnectPhysical {
                                    bus: *bus,
                                    address: *address,
                                },
                                DeviceSource::VirtualDisk {
                                    path,
                                    read_only,
                                } => {
                                    UsbCommand::ConnectVirtualDisk {
                                        path: path.clone(),
                                        read_only: *read_only,
                                    }
                                }
                            },
                        );
                    }
                }
            });
        }
    }
});
```

After the `ScrollArea` closure, execute the action:

```rust
// Execute USB action outside the closure
if let Some(cmd) = usb_action {
    self.usb_error_message = None;
    self.usb_connecting = true;
    if let Some(ref tx) = self.usb_tx {
        if let Err(e) = tx.try_send(cmd) {
            self.usb_connecting = false;
            self.usb_error_message =
                Some(format!("Failed to send command: {}", e));
        }
    }
}
```

Import `DeviceSource` and `UsbCommand` in app.rs. Check
existing imports — `UsbCommand` is already imported.
Add `use crate::usb::DeviceSource;`.

### Step 5: Show error messages in the panel

Add error display between the channel status and the
device list:

```rust
// Error message (if any)
if let Some(ref err) = self.usb_error_message {
    ui.separator();
    ui.colored_label(egui::Color32::RED, err);
}

// Connecting indicator
if self.usb_connecting {
    ui.separator();
    ui.label("Connecting...");
}
```

The error is cleared when the user initiates a new action
(step 4 sets `usb_error_message = None` before sending).

### Step 6: Clear connecting state on disconnect event

The `process_events()` handler already clears
`usb_connecting` on `UsbDeviceDisconnected` (phase 2).
Verify this works for the disconnect button flow:

1. User clicks Disconnect → `usb_connecting = true`,
   sends `UsbCommand::DisconnectDevice`.
2. Handler calls `disconnect_device()` → sends
   `ChannelEvent::UsbDeviceDisconnected`.
3. `process_events()` clears `usb_device_description`
   and `usb_connecting`.

This is correct. No additional changes needed.

## Files changed

| File | Change |
|------|--------|
| `src/channels/mod.rs` | Replace `UsbCommand` variants with `ConnectPhysical`, `ConnectVirtualDisk`, `DisconnectDevice`; remove dead_code allows; remove `DeviceBackend` import if unused |
| `src/channels/usbredir.rs` | Rewrite `handle_usb_command()` for new variants with device re-lookup, disconnect-before-connect, error reporting; add `RealDevice` and `nusb` imports |
| `src/app.rs` | Add connect/disconnect buttons per device in the panel; add two-pass action pattern; show error messages and connecting indicator; import `DeviceSource`; remove dead_code from `usb_tx` |

## What is NOT in scope

- Adding virtual disks at runtime (phase 6).
- Visual polish like green indicator dots, timestamps,
  dismiss buttons (phase 7).
- Hotplug or auto-refresh of the device list.

## Testing

- `make build` — compiles without errors.
- `make test` — all existing tests pass.
- `pre-commit run --all-files` — rustfmt and clippy clean.
- Manual: run ryll with a SPICE server that has USB
  redirection enabled.
  - Verify auto-connect from `--usb-disk` still works.
  - Open USB panel, verify device appears with green dot.
  - Click Disconnect, verify device disconnects and green
    dot disappears.
  - Click Connect on a virtual disk, verify it connects.
  - If physical USB devices are available, test Connect
    on a physical device.
  - Test error cases: disconnect USB device between
    Refresh and Connect click (should show error in panel).

## Back brief

This phase replaces the `UsbCommand` variants with
identity-based commands (`ConnectPhysical`,
`ConnectVirtualDisk`, `DisconnectDevice`). The channel
handler does async device lookup and open, with
disconnect-before-connect and error reporting via
`UsbConnectFailed`. The USB panel gets Connect/Disconnect
buttons per device, disabled when the channel isn't
ready or an operation is in progress. Errors are shown
in the panel in red. The two-pass pattern avoids egui
borrow checker issues.
