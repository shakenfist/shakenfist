# Phase 5: Device connection lifecycle

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Wire the usbredir protocol into a live device session.
After this phase, when a server advertises a usbredir
channel:

1. ryll sends a `usb_redir_hello` with its version and
   capabilities.
2. The server responds with its own hello.
3. When the user (or CLI flag) selects a device to
   redirect, ryll opens the backend, sends `ep_info`,
   `interface_info`, and `device_connect` to attach the
   device.
4. The server can then send `set_configuration`,
   `get_configuration`, `set_alt_setting`,
   `get_alt_setting`, and `reset` — ryll delegates each
   to the backend and replies.
5. Device disconnection works in both directions.

No data transfers (control/bulk) yet — those are phase 6.

## Background

### Object safety and enum dispatch

The `UsbDeviceBackend` trait uses `impl Future` return
types, which makes it non-object-safe (can't use
`Box<dyn UsbDeviceBackend>`). For now we only have one
concrete implementation (`RealDevice`), and phase 7 will
add `VirtualMsc`. Rather than switching to `async-trait`
or boxing futures, use an enum that wraps both:

```rust
pub enum DeviceBackend {
    Real(RealDevice),
    // Virtual(VirtualMsc),  // phase 7
}
```

The enum implements `UsbDeviceBackend` by delegating to
the active variant. This adds zero overhead and keeps the
trait clean.

### Message flow for device attachment

The usbredir hello exchange happens immediately when the
channel connects. Device attachment is a separate step
triggered by the user (or `--usb-device` CLI flag):

```
Client                         Server
  |                              |
  |--- hello (caps) ----------->|
  |<-- hello (caps) ------------|
  |                              |
  [user selects device]          |
  |                              |
  |--- ep_info ---------------->|
  |--- interface_info ---------->|
  |--- device_connect ---------->|
  |                              |
  |<-- set_configuration --------|
  |--- configuration_status ---->|
  |                              |
  |<-- control/bulk packets -----|
  |--- control/bulk replies ---->|
  |                              |
  [user disconnects]             |
  |                              |
  |--- device_disconnect ------->|
  |<-- device_disconnect_ack ----|
```

### Command channel

The channel handler needs to receive commands from the
app/UI (e.g. "connect this device", "disconnect"). This
follows the same pattern as the inputs channel: an mpsc
channel carries `UsbCommand` messages from the app to the
channel handler, and `tokio::select!` multiplexes network
reads with command receipt.

### ID management

The usbredir `id` field in the header is used by the
server to correlate requests with responses. For lifecycle
messages (set_configuration, reset, etc.) the server picks
the ID and the client echoes it back in the response. The
client doesn't need to generate IDs for lifecycle messages
— only for data transfer packets in phase 6.

## Detailed steps

### Step 1: Define UsbCommand enum

In `src/channels/mod.rs` (alongside `InputEvent`):

```rust
/// Commands sent from the app to the usbredir channel.
#[derive(Debug)]
pub enum UsbCommand {
    /// Connect a device using the given backend.
    ConnectDevice(DeviceBackend),
    /// Disconnect the currently connected device.
    DisconnectDevice,
}
```

### Step 2: Define DeviceBackend enum

In `src/usb/mod.rs`:

```rust
/// Enum dispatch wrapper for UsbDeviceBackend implementations.
///
/// Avoids the need for `Box<dyn UsbDeviceBackend>` which
/// requires object-safety (async-trait or boxed futures).
pub enum DeviceBackend {
    Real(real::RealDevice),
}
```

Implement `UsbDeviceBackend` for `DeviceBackend` by
delegating each method to the inner variant:

```rust
impl UsbDeviceBackend for DeviceBackend {
    fn device_info(&self) -> DeviceConnect {
        match self {
            DeviceBackend::Real(d) => d.device_info(),
        }
    }
    // ... same for all methods
}
```

### Step 3: Add command channel to UsbredirChannel

Modify `UsbredirChannel`:

- Add `usb_rx: mpsc::Receiver<UsbCommand>` field.
- Add `backend: Option<DeviceBackend>` field.
- Add `server_caps: u32` field (populated from server
  hello).
- Update constructor to accept `usb_rx`.
- Update `run()` to use `tokio::select!` to multiplex
  network reads with command receipt, following the
  inputs channel pattern.

### Step 4: Send hello on channel start

In `run()`, immediately after logging "channel started",
build and send the client's hello:

```rust
let hello = Hello {
    version: "ryll usb-redir 0.1".to_string(),
    capabilities: RYLL_CAPS,
};
let mut payload = Vec::new();
hello.write(&mut payload)?;
let msg = make_usbredir_message(
    msg_type::HELLO, 0, &payload);
self.send_data(&msg).await?;
```

Note: `send_data()` wraps the usbredir message in a
SPICEVMC_DATA SPICE message.

### Step 5: Handle server hello

In `handle_usbredir_message()`, when we receive
`UsbredirPayload::Hello(hello)`:

```rust
self.server_caps = hello.capabilities;
info!(
    "usbredir: server hello: version='{}' \
     caps=0x{:08x}",
    hello.version, hello.capabilities,
);
```

### Step 6: Handle ConnectDevice command

When a `UsbCommand::ConnectDevice(backend)` arrives:

1. Store the backend: `self.backend = Some(backend)`.
2. Send `ep_info`:
   ```rust
   let ep = backend.endpoint_info();
   let mut buf = Vec::new();
   ep.write(&mut buf)?;
   let msg = make_usbredir_message(
       msg_type::EP_INFO, 0, &buf);
   self.send_data(&msg).await?;
   ```
3. Send `interface_info` (same pattern).
4. Send `device_connect` (same pattern).
5. Log the connection.

### Step 7: Handle lifecycle messages from server

Expand `handle_usbredir_message()` to dispatch lifecycle
messages to the backend:

**SetConfiguration (type 6):**
```rust
if let Some(ref mut backend) = self.backend {
    let status = backend
        .set_configuration(sc.configuration).await?;
    let resp = ConfigurationStatus {
        status: status as u8,
        configuration: sc.configuration,
    };
    let mut buf = Vec::new();
    resp.write(&mut buf)?;
    let msg = make_usbredir_message(
        msg_type::CONFIGURATION_STATUS,
        msg.id, &buf);
    self.send_data(&msg).await?;
}
```

**GetConfiguration (type 7):**
```rust
if let Some(ref mut backend) = self.backend {
    let config = backend.get_configuration().await?;
    let resp = ConfigurationStatus {
        status: Status::Success as u8,
        configuration: config,
    };
    // ... send response with same id
}
```

**SetAltSetting (type 9):**
```rust
if let Some(ref mut backend) = self.backend {
    let status = backend.set_alt_setting(
        sa.interface, sa.alt_setting).await?;
    let resp = AltSettingStatus {
        status: status as u8,
        interface: sa.interface,
        alt_setting: sa.alt_setting,
    };
    // ... send response
}
```

**GetAltSetting (type 10):**
```rust
if let Some(ref mut backend) = self.backend {
    let alt = backend.get_alt_setting(ga.interface).await?;
    let resp = AltSettingStatus {
        status: Status::Success as u8,
        interface: ga.interface,
        alt_setting: alt,
    };
    // ... send response
}
```

**Reset (type 3):**
```rust
if let Some(ref mut backend) = self.backend {
    backend.reset().await?;
}
```

**FilterReject (type 22):**
Log and ignore — the server is telling us a device was
rejected by a filter. No action needed.

**DeviceDisconnectAck (type 24):**
Log. If we initiated the disconnect, this confirms it.
Drop the backend if still held.

### Step 8: Handle DisconnectDevice command

When a `UsbCommand::DisconnectDevice` arrives:

1. Send `device_disconnect` (zero payload, id=0):
   ```rust
   let msg = make_usbredir_message(
       msg_type::DEVICE_DISCONNECT, 0, &[]);
   self.send_data(&msg).await?;
   ```
2. Drop the backend: `self.backend = None`.
3. Log the disconnection.

### Step 9: Update app.rs to create command channel

In `run_connection()`, when spawning the usbredir channel:

```rust
let (usb_tx, usb_rx) = mpsc::channel(16);

let mut channel = UsbredirChannel::new(
    stream,
    event_tx.clone(),
    usb_rx,
    capture.clone(),
    byte_counter.clone(),
);
```

Store `usb_tx` for later use by the UI (phase 8) or CLI
flags. For now, `usb_tx` is created but not used — phase 8
will wire it to the UI, and phase 10 will wire it to
`--usb-device` CLI flag.

### Step 10: Add send_usbredir_message helper

Add a convenience method to `UsbredirChannel` that builds
a usbredir message and sends it via the VMC channel:

```rust
async fn send_usbredir(
    &mut self,
    msg_type: u32,
    id: u32,
    payload: &[u8],
) -> Result<()> {
    let usbredir_msg = make_usbredir_message(
        msg_type, id, payload);
    self.send_data(&usbredir_msg).await
}
```

This avoids repeating the two-step build+send pattern
throughout the handler.

## Files changed

| File | Change |
|------|--------|
| `src/channels/mod.rs` | Add `UsbCommand` enum |
| `src/channels/usbredir.rs` | Add `usb_rx`, `backend`, `server_caps` fields; hello exchange; lifecycle message handling; command processing via `tokio::select!`; `send_usbredir()` helper |
| `src/usb/mod.rs` | Add `DeviceBackend` enum with `UsbDeviceBackend` impl |
| `src/app.rs` | Create `usb_tx`/`usb_rx` channel, pass `usb_rx` to `UsbredirChannel::new()` |

## What is NOT in scope

- Control and bulk data transfers (phase 6).
- Virtual mass storage device backend (phase 7).
- UI for selecting and connecting devices (phase 8).
- CLI `--usb-device` flag for headless mode (phase 8).
- Interrupt transfers (phase 9).
- Actually sending `UsbCommand::ConnectDevice` from
  anywhere — this phase adds the infrastructure; phase 8
  wires it to the UI and CLI.

## Testing

### Build and lint

```
./scripts/check-rust.sh fix
pre-commit run --all-files
make test
```

### Manual integration test

1. Start QEMU with USB redirection.
2. Connect ryll with `--verbose`.
3. Verify logs show:
   - `"usbredir: sending hello: version='ryll ...'"`
   - `"usbredir: server hello: version='...' caps=0x..."`
   - The hello exchange completes without errors.

Full device connection can't be tested until phase 8
provides a way to trigger `ConnectDevice`.

## Back brief

Before starting this phase, confirm understanding: we are
wiring the usbredir protocol into a live bidirectional
session. The channel handler gains a command channel
(like the inputs channel), sends its hello on startup,
stores server capabilities, and handles all lifecycle
messages by delegating to the active `DeviceBackend`.
The `DeviceBackend` enum provides non-object-safe dispatch.
No data transfers yet. The command channel is created but
not fed from the UI (that's phase 8).
