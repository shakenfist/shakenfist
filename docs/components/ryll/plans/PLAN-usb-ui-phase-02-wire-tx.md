# Phase 2: Wire usb_tx and channel events

Parent plan: [PLAN-usb-ui.md](/components/ryll/plans/PLAN-usb-ui/)

## Goal

Wire the USB command channel sender (`usb_tx`) from
`RyllApp` through to `UsbredirChannel`, following the
same pattern as `input_tx`/`input_rx`. Add new
`ChannelEvent` variants for device disconnect and
connection failure. Update `process_events()` to handle
all USB-related events including usbredir channel
disconnect.

After this phase:

- `RyllApp` holds a `usb_tx` sender that can send
  `UsbCommand` messages to the `UsbredirChannel`.
- The `_usb_tx` that was created and dropped in
  `run_connection()` is gone — the channel is created
  in `RyllApp::new()` and threaded through.
- `ChannelEvent::UsbDeviceDisconnected` and
  `ChannelEvent::UsbConnectFailed(String)` exist and
  are handled in `process_events()`.
- Usbredir channel disconnect resets all USB UI state.
- Headless mode handles the new events too.

No UI changes in this phase — just plumbing.

## Detailed steps

### Step 1: Add new ChannelEvent variants

In `src/channels/mod.rs`, add two variants to
`ChannelEvent`:

```rust
/// A USB device was disconnected
UsbDeviceDisconnected,

/// A USB device connection attempt failed
UsbConnectFailed(String),
```

Remove the `#[allow(dead_code)]` from
`UsbDevicesChanged` while we're here — it will be used
in phase 4.

### Step 2: Send UsbDeviceDisconnected from the channel handler

In `src/channels/usbredir.rs`, update
`disconnect_device()` to send the event after
disconnecting:

```rust
async fn disconnect_device(&mut self) -> Result<()> {
    if self.backend.is_some() {
        info!("usbredir: disconnecting device");
        self.stop_all_interrupt_polls();
        self.send_usbredir(msg_type::DEVICE_DISCONNECT, 0, &[])
            .await?;
        self.backend = None;
        self.event_tx
            .send(ChannelEvent::UsbDeviceDisconnected)
            .await
            .ok();
    }
    Ok(())
}
```

### Step 3: Add new fields to RyllApp

In `src/app.rs`, add three fields to `struct RyllApp`:

```rust
// USB command sender (None if no usbredir channel)
usb_tx: Option<mpsc::Sender<UsbCommand>>,

// USB channel readiness and operation state
usb_channel_ready: bool,
usb_connecting: bool,

// USB error message from failed operations
usb_error_message: Option<String>,
```

Import `UsbCommand` in `app.rs` (add to the existing
`use crate::channels::{ ... }` block).

Initialise all four fields in `RyllApp::new()`:

```rust
usb_tx: Some(usb_tx),
usb_channel_ready: false,
usb_connecting: false,
usb_error_message: None,
```

### Step 4: Create usb channel in RyllApp::new() (GUI mode)

In `RyllApp::new()`, next to the existing channel
creation at line 214-215:

```rust
let (event_tx, event_rx) = mpsc::channel(EVENT_CHANNEL_SIZE);
let (input_tx, input_rx) = mpsc::channel(INPUT_CHANNEL_SIZE);
```

Add:

```rust
let (usb_tx, usb_rx) = mpsc::channel(16);
```

Then pass `usb_rx` into the `run_connection()` call at
line 248. This means adding it to the closure's move
captures and to the function call.

The `std::thread::spawn` closure currently captures
`input_rx` by move. Add `usb_rx` the same way.

### Step 5: Add usb_rx parameter to run_connection()

Change the signature of `run_connection()` from:

```rust
async fn run_connection(
    config: Config,
    event_tx: mpsc::Sender<ChannelEvent>,
    input_rx: mpsc::Receiver<InputEvent>,
    virtual_disks: Vec<VirtualDiskConfig>,
    capture: Option<Arc<CaptureSession>>,
    byte_counter: Arc<ByteCounter>,
    traffic: Arc<TrafficBuffers>,
    snapshots: ChannelSnapshots,
) -> Result<()> {
```

to:

```rust
async fn run_connection(
    config: Config,
    event_tx: mpsc::Sender<ChannelEvent>,
    input_rx: mpsc::Receiver<InputEvent>,
    usb_rx: mpsc::Receiver<UsbCommand>,
    virtual_disks: Vec<VirtualDiskConfig>,
    capture: Option<Arc<CaptureSession>>,
    byte_counter: Arc<ByteCounter>,
    traffic: Arc<TrafficBuffers>,
    snapshots: ChannelSnapshots,
) -> Result<()> {
```

### Step 6: Use the passed-in usb_rx in run_connection()

In `run_connection()`, in the `ChannelType::Usbredir`
arm (line 1453-1467), replace:

```rust
let (_usb_tx, usb_rx) = mpsc::channel(16);
```

with just using the `usb_rx` parameter directly. Since
`usb_rx` is moved into `UsbredirChannel::new()`, and
`run_connection` only handles one usbredir channel, this
works. If the server advertises multiple usbredir
channels, only the first gets the `usb_rx` — subsequent
ones would need their own channels, but that's out of
scope (the code already only processes one due to the
loop structure).

However, there's a subtlety: `usb_rx` is a parameter
that can only be moved once. The channel loop iterates
over potentially multiple channels. We need to wrap it
in an `Option` and `take()` it:

At the top of the channel connection loop (after the
`let mut handles` line), add:

```rust
let mut usb_rx = Some(usb_rx);
```

Then in the `ChannelType::Usbredir` arm:

```rust
ChannelType::Usbredir => {
    if let Some(usb_rx) = usb_rx.take() {
        let stream = client
            .connect_channel(
                session_id,
                channel_type,
                channel_id,
            )
            .await?;
        let mut channel = UsbredirChannel::new(
            stream,
            event_tx.clone(),
            usb_rx,
            virtual_disks.clone(),
            capture.clone(),
            byte_counter.clone(),
        );
        handles.push(tokio::spawn(
            async move { channel.run().await },
        ));
    } else {
        info!(
            "Skipping additional usbredir channel \
             (id={}): only one supported",
            channel_id
        );
    }
}
```

This pattern mirrors how `input_rx` is handled — it's
moved once and then the loop breaks (line 1449-1450).
For usbredir we don't break the loop (other channel
types may follow), but we skip subsequent usbredir
channels with a log message.

### Step 7: Wire usb_rx in headless mode

In `run_headless()` (line 1489), create the USB channel
alongside the existing channels:

```rust
let (event_tx, mut event_rx) = mpsc::channel(EVENT_CHANNEL_SIZE);
let (input_tx, input_rx) = mpsc::channel(INPUT_CHANNEL_SIZE);
let (_usb_tx, usb_rx) = mpsc::channel(16);
```

In headless mode, no UI sends USB commands, so the
sender is intentionally unused. The `_usb_tx` prefix
suppresses the warning. Pass `usb_rx` to
`run_connection()`.

### Step 8: Update process_events() in RyllApp

Update the event handling in `process_events()` (around
line 427-441) to handle all USB events:

```rust
ChannelEvent::UsbChannelReady => {
    info!("app: USB redirection channel connected");
    self.usb_channel_ready = true;
}

ChannelEvent::UsbDeviceConnected(desc) => {
    info!("app: USB device connected: {}", desc);
    self.usb_device_description = Some(desc);
    self.usb_connecting = false;
}

ChannelEvent::UsbDeviceDisconnected => {
    info!("app: USB device disconnected");
    self.usb_device_description = None;
    self.usb_connecting = false;
}

ChannelEvent::UsbConnectFailed(err) => {
    error!("app: USB connect failed: {}", err);
    self.usb_connecting = false;
    self.usb_error_message = Some(err);
}

ChannelEvent::Disconnected(channel) => {
    info!(
        "app: channel {} disconnected",
        channel.name(),
    );
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

The key change to the existing `Disconnected` handler
is adding the `ChannelType::Usbredir` check to reset
`usb_channel_ready`, `usb_device_description`, and
`usb_connecting`.

### Step 9: Update headless event loop

In `run_headless()` (around line 1561-1571), add
handling for the new events:

```rust
ChannelEvent::UsbDeviceConnected(desc) => {
    info!("headless: USB device connected: {}", desc);
}
ChannelEvent::UsbDeviceDisconnected => {
    info!("headless: USB device disconnected");
}
ChannelEvent::UsbConnectFailed(err) => {
    error!("headless: USB connect failed: {}", err);
}
```

### Step 10: Remove dead_code allow from UsbCommand

In `src/channels/mod.rs`, the `UsbCommand` enum has
`#[allow(dead_code)]`. Since `usb_tx` is now stored on
`RyllApp` (not dropped), the variants will be used in
phase 5. However, they won't actually be sent until
phase 5, so the `allow(dead_code)` may still be needed
to pass clippy. Check after building — if clippy
complains, keep the allow; if not, remove it.

## Files changed

| File | Change |
|------|--------|
| `src/channels/mod.rs` | Add `UsbDeviceDisconnected` and `UsbConnectFailed(String)` to `ChannelEvent`; remove `#[allow(dead_code)]` from `UsbDevicesChanged` if clippy allows |
| `src/channels/usbredir.rs` | Send `UsbDeviceDisconnected` event in `disconnect_device()` |
| `src/app.rs` | Add `usb_tx`, `usb_channel_ready`, `usb_connecting`, `usb_error_message` fields to `RyllApp`; create USB channel in `new()`; pass `usb_rx` to `run_connection()`; update `process_events()` for all USB events including usbredir channel disconnect; add `usb_rx` parameter to `run_connection()` and use it via `Option::take()`; update `run_headless()` to create and pass USB channel; handle new events in headless loop |

## What is NOT in scope

- Any UI changes (no buttons, no panel).
- Changes to `UsbCommand` variants (that's phase 5).
- Sending any `UsbCommand` messages (that's phase 5).
- `UsbConnectFailed` is not yet sent by the channel
  handler — the variant is defined and handled in
  `process_events()`, but the handler code that sends
  it comes in phase 5 when `UsbCommand` is redesigned.

## Testing

- `make build` — compiles without errors.
- `make test` — all existing tests pass (77 tests).
- `pre-commit run --all-files` — rustfmt and clippy
  clean.
- No new unit tests needed for this phase — it's pure
  plumbing. The correctness is verified by building
  (type-checked by the compiler) and by the existing
  test suite still passing.
- Manual verification: run ryll with `--usb-disk` and
  confirm auto-connect still works (the auto-connect
  path in the hello handler is unchanged).

## Back brief

This phase threads the USB command channel through the
same path as the input channel: created in
`RyllApp::new()`, `usb_tx` stored on the app, `usb_rx`
passed through `run_connection()` into
`UsbredirChannel`. It adds two new event variants
(`UsbDeviceDisconnected`, `UsbConnectFailed`) and
updates `process_events()` to handle them plus the
usbredir channel disconnect case that was previously
missing. The auto-connect flow is unaffected — it calls
`connect_device()` directly without going through
`UsbCommand`. Headless mode creates the channel but
drops the sender since no UI drives it.
