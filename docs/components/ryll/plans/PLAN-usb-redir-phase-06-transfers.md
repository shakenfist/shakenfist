# Phase 6: Control and bulk transfers

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Handle `usb_redir_control_packet` and `usb_redir_bulk_packet`
messages from the server by delegating to the active device
backend and sending responses. After this phase, a connected
USB device can perform real I/O — the VM can read
descriptors via control transfers and exchange data via bulk
transfers.

## Background

### Transfer message flow

When the server needs to perform a USB transfer on the
redirected device, it sends a request with a correlation
`id`. The client performs the transfer on the physical (or
virtual) device and responds with the same `id` and the
result status (+ data for IN transfers):

```
Server → Client:  control_packet (id=42, request=GET_DESCRIPTOR, ...)
Client → Server:  control_packet (id=42, status=Success, data=[...])

Server → Client:  bulk_packet (id=99, endpoint=2, data=[...])
Client → Server:  bulk_packet (id=99, endpoint=2, status=Success)

Server → Client:  bulk_packet (id=100, endpoint=17, length=512)
Client → Server:  bulk_packet (id=100, endpoint=17, status=Success, data=[...])
```

### Control transfers

The `ControlPacketHeader` contains a standard USB setup
packet: `request_type`, `request`, `value`, `index`,
`length`, plus an `endpoint` (always 0 for standard
control), and `status` (0 in requests, filled in
responses).

Direction is encoded in bit 7 of `request_type`:
- `0x80` = IN (device → host): server wants data back
- `0x00` = OUT (host → device): server sends data

For IN: server sends header with `length` = max bytes
expected, no data. Client performs the transfer, responds
with header (status filled) + returned data.

For OUT: server sends header + data payload. Client
performs the transfer, responds with header only (status
filled, no data).

### Bulk transfers

The `BulkPacketHeader` contains `endpoint`, `status`,
`length`, `stream_id`, and `length_high`. Direction is
determined by the endpoint number (usbredir convention:
0-15 = OUT, 16-31 = IN).

For IN (endpoint >= 16): server sends header with
`length` = max bytes expected, no data payload. Client
reads from the device, responds with header (status,
actual length) + data.

For OUT (endpoint < 16): server sends header + data
payload. Client writes to the device, responds with
header only (status, no data).

The actual transfer length for bulk is:
`(length_high << 16) | length` — supporting 32-bit
lengths when `cap_32bits_bulk_length` is negotiated.
We don't advertise that capability, so `length_high`
will be 0 from our servers, but we should still
compute the full value for robustness.

### Mapping to ControlSetup

The `ControlPacketHeader` maps to our `ControlSetup` type:

```rust
ControlSetup {
    endpoint: header.endpoint,
    request_type: header.request_type,
    request: header.request,
    value: header.value,
    index: header.index,
    length: header.length,
}
```

## Detailed steps

### Step 1: Handle control_packet in handle_usbredir_message

Replace the `ControlPacket { .. }` stub with:

```rust
UsbredirPayload::ControlPacket { header, data } => {
    if let Some(ref mut backend) = self.backend {
        let setup = ControlSetup {
            endpoint: header.endpoint,
            request_type: header.request_type,
            request: header.request,
            value: header.value,
            index: header.index,
            length: header.length,
        };

        let result = backend
            .control_transfer(&setup, &data).await?;

        // Build response header with status filled in
        let resp_header = ControlPacketHeader {
            endpoint: header.endpoint,
            request: header.request,
            request_type: header.request_type,
            status: result.status as u8,
            value: header.value,
            index: header.index,
            length: result.data.len() as u16,
        };
        let mut buf = Vec::new();
        resp_header.write(&mut buf)?;
        buf.extend_from_slice(&result.data);
        self.send_usbredir(
            msg_type::CONTROL_PACKET, msg.id, &buf,
        ).await?;
    } else {
        warn!("usbredir: control_packet but no device");
    }
}
```

### Step 2: Handle bulk_packet in handle_usbredir_message

Replace the `BulkPacket { .. }` stub with:

```rust
UsbredirPayload::BulkPacket { header, data } => {
    if let Some(ref mut backend) = self.backend {
        let is_in = is_ep_in(header.endpoint);

        let result = if is_in {
            let max_len =
                header.actual_length() as usize;
            backend
                .bulk_in(header.endpoint, max_len)
                .await?
        } else {
            backend
                .bulk_out(header.endpoint, &data)
                .await?
        };

        let data_len = result.data.len() as u32;
        let resp_header = BulkPacketHeader {
            endpoint: header.endpoint,
            status: result.status as u8,
            length: (data_len & 0xFFFF) as u16,
            stream_id: header.stream_id,
            length_high: ((data_len >> 16) & 0xFFFF)
                as u16,
        };
        let mut buf = Vec::new();
        resp_header.write(&mut buf)?;
        buf.extend_from_slice(&result.data);
        self.send_usbredir(
            msg_type::BULK_PACKET, msg.id, &buf,
        ).await?;
    } else {
        warn!("usbredir: bulk_packet but no device");
    }
}
```

### Step 3: Handle cancel_data_packet

For now, `cancel_data_packet` is logged and acknowledged.
Full cancellation of in-flight async transfers would
require tracking pending transfers by ID, which adds
complexity we can defer:

```rust
UsbredirPayload::CancelDataPacket => {
    debug!(
        "usbredir: cancel_data_packet (id={}) \
         — cancellation not yet supported",
        msg.id,
    );
}
```

The existing code already logs this. The change is minor —
just adjust the log message to clarify the limitation.

### Step 4: Handle interrupt_packet (stub)

Interrupt packets follow the same IN/OUT pattern as bulk
but are handled in phase 9. For now, respond with a
STALL status so the server knows the transfer failed:

```rust
UsbredirPayload::InterruptPacket { header, .. } => {
    if self.backend.is_some() {
        let resp = InterruptPacketHeader {
            endpoint: header.endpoint,
            status: Status::Stall as u8,
            length: 0,
        };
        let mut buf = Vec::new();
        resp.write(&mut buf)?;
        self.send_usbredir(
            msg_type::INTERRUPT_PACKET, msg.id, &buf,
        ).await?;
    }
}
```

### Step 5: Add required imports

The channel handler will need:

```rust
use crate::usb::{ControlSetup, is_ep_in};
use crate::usbredir::constants::Status;
use crate::usbredir::messages::{
    BulkPacketHeader, ControlPacketHeader,
    InterruptPacketHeader,
};
```

### Step 6: Verbose logging for data transfers

Add debug logging for data transfers when verbose mode is
enabled, showing endpoint, direction, data size, and
status. This is valuable for protocol debugging:

```rust
if settings::is_verbose() {
    debug!(
        "usbredir: control {} ep={} req=0x{:02x} \
         rtype=0x{:02x} val=0x{:04x} idx=0x{:04x} \
         → status={:?} {}B",
        if is_in { "IN" } else { "OUT" },
        header.endpoint,
        header.request,
        header.request_type,
        header.value,
        header.index,
        result.status,
        result.data.len(),
    );
}
```

Similar for bulk:

```rust
if settings::is_verbose() {
    debug!(
        "usbredir: bulk {} ep={} → status={:?} {}B",
        if is_in { "IN" } else { "OUT" },
        header.endpoint,
        result.status,
        result.data.len(),
    );
}
```

## Files changed

| File | Change |
|------|--------|
| `src/channels/usbredir.rs` | Replace data transfer stubs with real implementations: control_packet, bulk_packet, interrupt_packet (stall stub); add imports for ControlSetup, is_ep_in, Status, packet headers |

## What is NOT in scope

- Transfer cancellation tracking (deferred — would need
  a HashMap of pending transfer IDs and async join
  handles).
- Interrupt transfer implementation (phase 9).
- Send-side LZ4 compression (future work).
- Bandwidth statistics (can be added later using the
  existing `ByteCounter`).

## Testing

### Build and lint

```
./scripts/check-rust.sh fix
pre-commit run --all-files
make test
```

### Manual integration test

Full end-to-end testing requires phases 7 or 8 to provide
a way to connect a device. However, with a QEMU VM and a
physical USB device (connected via phase 8's CLI flag or
manual `UsbCommand` injection), the flow would be:

1. Start QEMU with USB redirection.
2. Connect ryll with `--verbose`.
3. Connect a USB device.
4. Observe control transfers in the log (GET_DESCRIPTOR
   etc. during device enumeration by the guest OS).
5. For a storage device, observe bulk transfers during
   read/write operations.

## Back brief

Before starting this phase, confirm understanding: we are
adding data transfer handling to the channel handler.
Control and bulk packets from the server are dispatched to
the active backend, and the results are sent back with
matching correlation IDs. The implementation maps
`ControlPacketHeader` to `ControlSetup` for control
transfers, and uses `is_ep_in()` to determine bulk
direction. Interrupt packets get a STALL stub. Transfer
cancellation is logged but not implemented.
