# Phase 8: UI integration and CLI flags

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Add CLI flags for virtual disk attachment and wire the
USB command channel so devices can actually be connected.
After this phase:

- `--usb-disk /path/to/image.raw` presents a RAW image
  as a USB mass storage device to the VM.
- `--usb-disk-ro /path/to/image.raw` does the same in
  read-only mode.
- Both GUI and headless modes auto-connect the virtual
  device when the usbredir channel is ready.
- The GUI shows a status line for the connected USB device.

## Background

### Architecture challenge

The usbredir channel is spawned inside `run_connection()`,
which runs in a separate thread (GUI mode) or tokio task
(headless mode). The `usb_tx` sender was created there but
immediately dropped (`_usb_tx`). To enable the app to send
USB commands, we need to either:

(a) Thread `usb_tx` back to the GUI — complex because
    `run_connection()` runs in a different thread.
(b) Have the channel handler auto-connect based on config —
    simple because the handler has async context.

**Decision: option (b).** Pass the virtual disk paths into
`UsbredirChannel` as an `auto_connect` field. After the
hello exchange completes (server hello received), the
channel handler opens the `VirtualMsc` and connects it
automatically. This avoids cross-thread channel plumbing
and works identically in GUI and headless modes.

For future phases that need interactive UI-driven
device management (connecting/disconnecting devices
via buttons), we'll add a proper `usb_tx` back-channel.
For now the CLI flags are sufficient.

### USB command channel (future-proofing)

We keep the `usb_rx` field in `UsbredirChannel` and the
`UsbCommand` enum. Even though auto-connect doesn't use
the command channel, it's ready for phase 8.5 (UI buttons)
or manual testing. The `_usb_tx` becomes a real `usb_tx`
passed through `run_connection()` to the app, but we don't
wire UI buttons in this phase.

## Detailed steps

### Step 1: Add CLI flags to Args

In `src/config.rs`, add to the `Args` struct:

```rust
/// Present a RAW disk image as a USB mass storage device.
/// May be specified multiple times.
#[arg(long)]
pub usb_disk: Vec<String>,

/// Present a RAW disk image as a read-only USB mass
/// storage device. May be specified multiple times.
#[arg(long)]
pub usb_disk_ro: Vec<String>,
```

Using `Vec<String>` with `#[arg(long)]` allows multiple
`--usb-disk` flags. The `clap` derive handles this
automatically.

### Step 2: Collect virtual disk configs

Create a helper type or pass a `Vec<(PathBuf, bool)>`
through the call chain:

```rust
/// Parsed virtual disk configuration.
pub struct VirtualDiskConfig {
    pub path: PathBuf,
    pub read_only: bool,
}
```

In `main.rs`, after parsing Args, collect:
```rust
let mut virtual_disks = Vec::new();
for path in &args.usb_disk {
    virtual_disks.push(VirtualDiskConfig {
        path: PathBuf::from(path),
        read_only: false,
    });
}
for path in &args.usb_disk_ro {
    virtual_disks.push(VirtualDiskConfig {
        path: PathBuf::from(path),
        read_only: true,
    });
}
```

### Step 3: Thread virtual disk config to UsbredirChannel

The config needs to flow:
`main()` → `run_gui()`/`run_headless()` → `RyllApp::new()`
→ `run_connection()` → `UsbredirChannel::new()`

Add a `virtual_disks: Vec<VirtualDiskConfig>` parameter
to each function in the chain. In `run_connection()`, pass
it to `UsbredirChannel::new()`.

Update `UsbredirChannel`:

```rust
pub struct UsbredirChannel {
    // ... existing fields ...
    auto_connect_disks: Vec<VirtualDiskConfig>,
}
```

### Step 4: Auto-connect after hello exchange

In `handle_usbredir_message()`, when we receive
`UsbredirPayload::Hello`:

```rust
UsbredirPayload::Hello(hello) => {
    self.server_caps = hello.capabilities;
    info!("usbredir: server hello: ...");

    // Auto-connect virtual disks if configured
    for disk in std::mem::take(&mut self.auto_connect_disks)
    {
        match VirtualMsc::open(
            disk.path.clone(),
            disk.read_only,
        ).await {
            Ok(msc) => {
                let backend = DeviceBackend::Virtual(msc);
                self.connect_device(backend).await?;
                info!(
                    "usbredir: auto-connected {}",
                    disk.path.display(),
                );
            }
            Err(e) => {
                warn!(
                    "usbredir: failed to open {}: {}",
                    disk.path.display(), e,
                );
            }
        }
    }
}
```

`std::mem::take()` moves the vec out so we only
auto-connect once (on the first hello, not reconnects).

Note: only the first disk is actually connected (the
SPICE protocol supports one device per usbredir channel).
If multiple `--usb-disk` flags are given, log a warning
and connect only the first.

### Step 5: Add USB status to GUI

In `RyllApp`, add a field:

```rust
usb_device_description: Option<String>,
```

In `process_events()`, handle a new `ChannelEvent`:

```rust
ChannelEvent::UsbDeviceConnected(description) => {
    info!("app: USB device connected: {}", description);
    self.usb_device_description = Some(description);
}
```

Add `UsbDeviceConnected(String)` to the `ChannelEvent`
enum.

In the channel handler's `connect_device()`, after
successfully connecting, send this event:

```rust
self.event_tx
    .send(ChannelEvent::UsbDeviceConnected(
        backend.description(),
    ))
    .await
    .ok();
```

In `eframe::App::update()`, add a small status indicator
in the statistics panel (bottom bar) showing the connected
USB device:

```rust
if let Some(ref desc) = self.usb_device_description {
    ui.label(format!("USB: {}", desc));
}
```

### Step 6: Update headless mode

Pass `virtual_disks` to `run_headless()` and through to
`run_connection()`. The auto-connect logic in the channel
handler works the same in headless mode. In the headless
event loop, handle `UsbDeviceConnected`:

```rust
ChannelEvent::UsbDeviceConnected(desc) => {
    info!("headless: USB device connected: {}", desc);
}
```

### Step 7: Validate CLI flags

In `main()`, validate:
- Each `--usb-disk` and `--usb-disk-ro` path exists
  (fail early with a clear error).
- File size is at least 512 bytes.
- Warn if file size is not a multiple of 512.
- If more than one virtual disk is specified, warn that
  only the first will be connected (SPICE limitation:
  one device per usbredir channel).

## Files changed

| File | Change |
|------|--------|
| `src/config.rs` | Add `usb_disk` and `usb_disk_ro` to `Args`; add `VirtualDiskConfig` struct |
| `src/main.rs` | Parse virtual disk args, validate paths, pass through to `run_gui()`/`run_headless()` |
| `src/app.rs` | Thread `virtual_disks` through `RyllApp::new()` → `run_connection()` → `UsbredirChannel::new()`; add `usb_device_description` field; handle `UsbDeviceConnected` event in GUI and headless |
| `src/channels/mod.rs` | Add `UsbDeviceConnected(String)` to `ChannelEvent` |
| `src/channels/usbredir.rs` | Add `auto_connect_disks` field; auto-connect after hello; send `UsbDeviceConnected` event on successful connect |

## What is NOT in scope

- UI buttons for connect/disconnect (future work — needs
  `usb_tx` wired to the GUI thread).
- Device enumeration panel listing real USB devices
  (future work — useful but needs container USB access).
- Multiple simultaneous device redirections (SPICE
  protocol supports multiple usbredir channels, but our
  implementation handles one).
- `--usb-device VID:PID` flag for physical devices
  (future work — needs root/udev permissions in the
  container).

## Testing

### Build and lint

```
./scripts/check-rust.sh fix
pre-commit run --all-files
make test
```

### Manual integration test

1. Create a test image:
   ```
   dd if=/dev/zero of=/tmp/test.raw bs=1M count=64
   ```

2. Start QEMU with USB redirection:
   ```
   -device qemu-xhci,id=xhci \
   -chardev spicevmc,id=usbredir1,name=usbredir \
   -device usb-redir,chardev=usbredir1,id=redir1
   ```

3. Connect ryll with virtual disk:
   ```
   ryll --file connection.vv --usb-disk /tmp/test.raw
   ```

4. Verify in the logs:
   - `"usbredir: auto-connected /tmp/test.raw"`
   - `"app: USB device connected: RAW Disk: /tmp/test.raw"`
   - The guest OS sees a USB mass storage device.

5. Test read-only mode:
   ```
   ryll --file connection.vv --usb-disk-ro /tmp/test.raw
   ```

6. Test headless mode:
   ```
   ryll --file connection.vv --headless --usb-disk /tmp/test.raw
   ```

### Error cases

- Non-existent path: should fail with clear error before
  connecting.
- Empty file: should warn about 0 blocks.
- Non-512-aligned file: should warn about inaccessible
  bytes.

## Back brief

Before starting this phase, confirm understanding: we are
adding `--usb-disk` and `--usb-disk-ro` CLI flags and
auto-connecting virtual disks when the usbredir channel's
hello exchange completes. The auto-connect happens inside
the channel handler's async context, avoiding cross-thread
plumbing. The GUI gets a simple status indicator. The
headless mode logs the connection. Path validation happens
early in main(). UI buttons for interactive device
management are deferred to future work.
