# Virtio-Block2 Prototype (with Protobuf)

This prototype extends virtio-block with structured guest-to-VMM communication
using Protocol Buffers. The guest sends typed messages over the serial port,
enabling machine-readable progress reporting and error handling.

## Goals

1. **Structured messaging**: Replace plain text serial output with protobuf
2. **Type-safe communication**: Use enum-based message types with typed payloads
3. **Machine-readable output**: VMM can parse and act on structured messages
4. **no_std compatibility**: Protobuf encoding works in bare-metal guest

## Changes from Virtio-Block

| Aspect | virtio-block | virtio-block2 |
|--------|--------------|---------------|
| Serial output | Plain text strings | Framed protobuf messages |
| Progress reporting | "Progress: 100/500" | ProgressMessage { current: 100, total: 500 } |
| Error messages | "Read error at sector 5" | ErrorMessage { sector: 5, status: 1 } |
| VMM decoding | String matching | Type-safe protobuf decode |

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        VMM                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │   KVM VM    │  │  Input Dev  │  │   Output Dev    │  │
│  │   + vCPU    │  │  (read-only)│  │   (write)       │  │
│  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘  │
│         │                │                   │           │
│         │                │ Backed by        │ Backed by │
│         │                ▼ source file      ▼ dest file │
│  ┌──────┴───────────────────────────────────────────┐   │
│  │              Guest Memory (8MB)                   │   │
│  └──────────────────────────────────────────────────┘   │
│                         │                                │
│                         ▼ Serial port (0x3f8)           │
│              ┌─────────────────────┐                    │
│              │ Protobuf decoder    │                    │
│              └─────────────────────┘                    │
└─────────────────────────────────────────────────────────┘
```

## Protocol Messages

Messages are framed with a 2-byte little-endian length prefix:

```
[len_lo][len_hi][protobuf_data...]
```

### Message Types

| Type | Level | Purpose |
|------|-------|---------|
| InitMessage | Info | Device initialization stages (probe, features, queue) |
| CapacityMessage | Info | Device capacity in sectors and bytes |
| ProgressMessage | Progress | Copy progress (current/total, percent) |
| ErrorMessage | Error | I/O errors with operation, device, sector, status |
| CompleteMessage | Complete | Operation completion with count and success flag |

### Example Output

```
[INFO] init stage=probe device=input address=0x10000000
[INFO] init stage=features device=input address=0x100000020
[INFO] init stage=queue device=input address=0x100
[INFO] capacity device=input sectors=50 bytes=25600
[INFO] init stage=probe device=output address=0x10001000
[INFO] init stage=features device=output address=0x100000000
[INFO] init stage=queue device=output address=0x100
[INFO] capacity device=output sectors=50 bytes=25600
[PROGRESS] progress op=copy 1/50 (2%)
[PROGRESS] progress op=copy 11/50 (22%)
[PROGRESS] progress op=copy 21/50 (42%)
[PROGRESS] progress op=copy 31/50 (62%)
[PROGRESS] progress op=copy 41/50 (82%)
[PROGRESS] progress op=copy 50/50 (100%)
[COMPLETE] complete op=copy count=50 success=true
```

## Guest Protocol Integration

### Guest Side (no_std)

```rust
use guest_protocol::{init_message, progress_message, encode_framed};

// Create a progress message
let msg = progress_message("copy", current, total, percent);

// Encode with length prefix
let mut buf = [0u8; 128];
if let Some(len) = encode_framed(&msg, &mut buf) {
    for &byte in &buf[..len] {
        serial_write(byte);
    }
}
```

### VMM Side (std)

```rust
use guest_protocol::{decode_framed, guest_};

struct SerialDecoder {
    buffer: Vec<u8>,
}

impl SerialDecoder {
    fn add_byte(&mut self, byte: u8) -> Option<guest_::GuestMessage> {
        self.buffer.push(byte);
        // Check for complete message
        if let Some((msg, consumed)) = decode_framed(&self.buffer) {
            self.buffer.drain(..consumed);
            return Some(msg);
        }
        None
    }
}
```

## Dependencies

### Guest (no_std)

- `guest-protocol`: Protobuf encoding (no_std compatible)
- `heapless`: Fixed-capacity containers for strings

### VMM (std)

- `guest-protocol` (with `std` feature): Protobuf decoding
- `kvm-ioctls`, `kvm-bindings`: KVM interface
- `vm-memory`: Guest memory management
- `clap`: CLI argument parsing

## Performance

The protobuf messaging adds overhead compared to plain text:

| Test | virtio-block | virtio-block2 |
|------|--------------|---------------|
| 10M sectors (4.8GB) | ~5 min | ~7 min |

The overhead comes from:
- Protobuf encoding in guest (heapless Vec allocation)
- Per-byte serial transmission of framed messages
- Protobuf decoding in VMM

For production use, consider batching messages or using a more efficient
transport (virtio-console, shared memory).

## Related Documentation

- [guest-protocol crate](/components/instar/crates/guest-protocol/)
- [Virtio-Block Prototype](/components/instar/prototypes/virtio-block/)
- [Virtio-Block Data Transfer](/components/instar/prototypes/data-transfer-virtio-block/)
- [VIRTIO 1.1 Specification](https://docs.oasis-open.org/virtio/virtio/v1.1/virtio-v1.1.html)
