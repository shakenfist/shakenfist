# Phase 9: Interrupt transfers

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Handle `usb_redir_start_interrupt_receiving`,
`usb_redir_stop_interrupt_receiving`, and
`usb_redir_interrupt_packet` messages so that HID and
other interrupt-based USB devices work through passthrough.
After this phase, USB keyboards, mice, security keys,
and other interrupt-endpoint devices can be redirected.

The virtual mass storage device does not use interrupt
transfers, so this phase only affects real device
passthrough.

## Background

### Interrupt transfer model in usbredir

Interrupt transfers differ from bulk/control in that they
are *polled* — the host periodically reads the device at a
fixed interval. In the usbredir protocol this is modelled
as:

1. **Server sends `start_interrupt_receiving(endpoint)`**
   — the client should begin periodically reading from
   the interrupt IN endpoint and forwarding any data.

2. **Client sends `interrupt_packet` data** as it arrives
   from the device (unsolicited — the server doesn't send
   a request per packet like it does for bulk).

3. **Server sends `stop_interrupt_receiving(endpoint)`**
   — the client stops polling.

4. **Server sends `interrupt_packet` for OUT** — the
   client writes data to an interrupt OUT endpoint (rare,
   but used by some HID devices for LED control).

This is fundamentally different from the request/response
model of bulk and control transfers. The client must
maintain a background polling loop per active interrupt
endpoint.

### nusb interrupt endpoint API

nusb handles interrupt endpoints identically to bulk —
`Interface::endpoint::<Interrupt, In>(addr)` returns an
`Endpoint` that supports `submit()`/`next_complete()`.
The key difference is that interrupt transfers are
naturally small and periodic.

For polling, we submit a transfer, await completion,
forward the data, and immediately submit another. This
continues until `stop_interrupt_receiving` arrives.

### Architecture

Each active interrupt endpoint runs as a spawned tokio
task that loops:

```
loop {
    submit(Buffer::new(max_packet_size))
    completion = next_complete().await
    if cancelled: break
    send interrupt_packet to server via channel
}
```

The channel handler stores `JoinHandle`s for these tasks
and aborts them when `stop_interrupt_receiving` arrives
or the device disconnects.

Communication from the polling tasks back to the channel
handler uses an mpsc channel — the tasks can't directly
call `send_usbredir()` since it requires `&mut self`.

### Scope

This phase adds interrupt transfer support for **real
devices only**. The virtual mass storage device returns
STALL for interrupt endpoints (unchanged from phase 6).
Future virtual device types (e.g. virtual HID) would
implement interrupt endpoints in their own backend.

## Detailed steps

### Step 1: Add interrupt data channel

Add an internal mpsc channel for interrupt polling tasks
to send data back to the channel handler:

```rust
/// Data from an interrupt polling task.
struct InterruptData {
    endpoint: u8,
    data: Vec<u8>,
    status: Status,
}
```

In `UsbredirChannel`, add:
```rust
interrupt_tx: mpsc::Sender<InterruptData>,
interrupt_rx: mpsc::Receiver<InterruptData>,
interrupt_handles: HashMap<u8, tokio::task::JoinHandle<()>>,
```

Add `interrupt_rx` to the `tokio::select!` loop alongside
network reads and USB commands.

### Step 2: Handle start_interrupt_receiving

When `UsbredirPayload::StartInterruptReceiving(sir)`
arrives:

1. Check that a device is connected.
2. Check the endpoint is an interrupt IN type
   (using `ep_info`).
3. If already polling this endpoint, ignore (idempotent).
4. Spawn a polling task for the endpoint.
5. Send `InterruptReceivingStatus` with `Success`.

The polling task needs access to the nusb `Interface` and
the `interrupt_tx` sender. Since `RealDevice` owns the
interfaces, add a method to `RealDevice`:

```rust
pub fn spawn_interrupt_poll(
    &self,
    endpoint: u8,
    max_packet_size: usize,
    tx: mpsc::Sender<InterruptData>,
) -> Result<JoinHandle<()>>
```

This opens the interrupt endpoint, spawns a tokio task
that loops `submit`/`next_complete`, and sends
`InterruptData` on each completion.

For the trait, add to `UsbDeviceBackend`:
```rust
fn spawn_interrupt_poll(
    &self,
    endpoint: u8,
    max_packet_size: usize,
    tx: mpsc::Sender<InterruptData>,
) -> Result<JoinHandle<()>>;
```

With a default implementation that returns an error
("not supported") for virtual devices.

### Step 3: Handle stop_interrupt_receiving

When `UsbredirPayload::StopInterruptReceiving(sir)` arrives:

1. Look up the `JoinHandle` for this endpoint.
2. Abort the task.
3. Remove it from `interrupt_handles`.
4. Send `InterruptReceivingStatus` with `Success`.

### Step 4: Forward interrupt data to server

In the `tokio::select!` loop, when `interrupt_rx` yields
`InterruptData`:

```rust
Some(idata) = self.interrupt_rx.recv() => {
    let header = InterruptPacketHeader {
        endpoint: idata.endpoint,
        status: idata.status as u8,
        length: idata.data.len() as u16,
    };
    let mut buf = Vec::new();
    header.write(&mut buf)?;
    buf.extend_from_slice(&idata.data);
    self.send_usbredir(
        msg_type::INTERRUPT_PACKET, 0, &buf
    ).await?;
}
```

The `id` field is 0 for unsolicited interrupt packets
(the server didn't request them with a specific ID).

### Step 5: Handle interrupt OUT packets

When `UsbredirPayload::InterruptPacket { header, data }`
arrives and the endpoint is OUT (< 16):

1. Forward to backend via a new `interrupt_out()` method
   on the trait (or reuse the existing one if defined).
2. Respond with `interrupt_packet` containing the status.

For IN direction packets from the server — this shouldn't
happen (the client sends unsolicited data). Log and ignore.

### Step 6: Clean up on disconnect

In `disconnect_device()`, abort all active interrupt
polling tasks:

```rust
for (ep, handle) in self.interrupt_handles.drain() {
    handle.abort();
    debug!("usbredir: stopped interrupt poll ep={}", ep);
}
```

### Step 7: DeviceBackend enum extension

Add the `spawn_interrupt_poll` method delegation in the
`DeviceBackend` enum impl. The `Virtual` variant returns
an error (MSC doesn't use interrupts).

## Files changed

| File | Change |
|------|--------|
| `src/channels/usbredir.rs` | Add `interrupt_tx`/`interrupt_rx`/`interrupt_handles` fields; handle `StartInterruptReceiving`, `StopInterruptReceiving`; forward interrupt data in select loop; clean up on disconnect; replace STALL stub for interrupt packets with real IN/OUT handling |
| `src/usb/mod.rs` | Add `InterruptData` struct; add `spawn_interrupt_poll` to trait with default; extend `DeviceBackend` enum |
| `src/usb/real.rs` | Implement `spawn_interrupt_poll` — open interrupt endpoint, spawn polling task |

## What is NOT in scope

- Interrupt transfers for virtual devices (the MSC device
  has no interrupt endpoints; future virtual HID would
  add this).
- Interrupt OUT for real devices (very rare — can add if
  a specific device needs it).
- Configurable polling interval (nusb handles this based
  on the endpoint descriptor's `bInterval`).

## Testing

### Build and lint

```
./scripts/check-rust.sh fix
pre-commit run --all-files
make test
```

### Manual testing

Requires a physical USB device with interrupt endpoints
(e.g. a USB keyboard or mouse) and a host with USB access:

1. Connect ryll with `--usb-device VID:PID` (once phase 8
   adds the flag) or via UI.
2. The guest OS should enumerate the HID device.
3. Type on the keyboard / move the mouse.
4. Verify input reaches the guest.

### Unit tests

Limited scope — the polling loop requires a real USB
device. Test what we can:
- `InterruptData` struct construction.
- The `start_interrupt_receiving` → status response flow
  (mock or error path).
- Cleanup on disconnect (verify handles are aborted).

## Back brief

Before starting this phase, confirm understanding: we are
adding interrupt transfer support for real USB device
passthrough. The key architectural difference from bulk is
that interrupt IN is *polled* — the client maintains
background tokio tasks that continuously read from
interrupt endpoints and forward data unsolicited to the
server. These tasks communicate back via an internal mpsc
channel. The server controls the lifecycle with
`start_interrupt_receiving` / `stop_interrupt_receiving`
messages. The virtual MSC device is unaffected.
