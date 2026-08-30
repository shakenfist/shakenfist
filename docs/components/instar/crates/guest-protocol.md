# Guest Protocol Crate

The `guest-protocol` crate provides structured bidirectional messaging between
guests and VMMs over the serial port.

## Overview

This crate uses Protocol Buffers (via `micropb`) to define schemas for both
guest status messages and VMM configuration messages. It is designed for:

- **no_std environments**: Works in bare-metal guests without heap allocation
- **Machine-parseable output**: VMM can extract structured status information
- **Bidirectional communication**: VMM can send configuration to guest at startup
- **Interoperability**: Protocol Buffers schema allows future tooling

## Location

```
crates/guest-protocol/
├── Cargo.toml
├── build.rs              # micropb-gen code generation
├── proto/
│   └── guest.proto       # Protocol Buffers schema
└── src/
    └── lib.rs            # Re-exports and helpers
```

## Schema

The protocol defines several message types:

```protobuf
enum Level {
  DEBUG = 0;
  INFO = 1;
  PROGRESS = 2;
  ERROR = 3;
  COMPLETE = 4;
}

message GuestMessage {
  Level level = 1;
  oneof payload {
    InitMessage init = 2;
    CapacityMessage capacity = 3;
    ProgressMessage progress = 4;
    ErrorMessage error = 5;
    CompleteMessage complete = 6;
    InfoResultMessage info_result = 7;
  }
}
```

## Message Types

### Guest → VMM Messages

| Message | Purpose | Fields |
|---------|---------|--------|
| InitMessage | Device initialization stages | stage, device, address |
| CapacityMessage | Device capacity info | device, sectors, bytes |
| ProgressMessage | Operation progress | operation, current, total, percent |
| ErrorMessage | Error details | operation, device, sector, status |
| CompleteMessage | Operation completion | operation, count, success |
| InfoResultMessage | Image format detection results | format, version, virtual_size, actual_size, cluster_size, flags, backing_file, external_data_file |

### VMM → Guest Messages

| Message | Purpose | Fields |
|---------|---------|--------|
| DeviceConfig | Single device configuration | name, sector_size |
| VmmConfig | Configuration for all devices | devices, progress_percent |

## Framing

Messages are framed with a 2-byte little-endian length prefix:

```
[len_lo][len_hi][protobuf_data...]
```

This allows the VMM to parse message boundaries from the serial stream.

## Usage

### Guest Side (no_std)

```rust
use guest_protocol::{init_message, encode_framed};

let msg = init_message("probe", "input", 0x100000);
let mut buf = [0u8; 128];
if let Some(len) = encode_framed(&msg, &mut buf) {
    serial_write(&buf[..len]);
}
```

### VMM Side (std)

```rust
use guest_protocol::decode_framed;

// Read from serial buffer
if let Some((msg, consumed)) = decode_framed(&buffer) {
    match msg.payload {
        Some(Payload::Progress(p)) => {
            println!("Progress: {}/{}", p.current, p.total);
        }
        _ => {}
    }
    // Consume `consumed` bytes from buffer
}
```

## Dependencies

- `micropb`: Protocol Buffers encoding/decoding (no_std compatible)
- `heapless`: Fixed-capacity containers for string fields

## Build Requirements

The `micropb-gen` build dependency requires `protoc` (Protocol Buffers
compiler) to be installed on the system.

## Features

- `std`: Enables VMM-side decoding support (not needed for guests)

## Current Status

The crate is actively used by:

- **virtio-block2**: Guest → VMM protobuf messages for status reporting
- **virtio-block3**: Bidirectional communication with VMM → guest configuration
- **info**: InfoResultMessage for image format detection results

## Limitations

The `MAX_MESSAGE_SIZE` constant (currently 600 bytes) limits the maximum
protobuf message payload. This is sized to accommodate `InfoResultMessage`
with file paths up to 256 characters. If larger messages are needed in the
future, consider refactoring to use caller-provided buffers instead of
fixed-size stack allocations.

## Prototypes Using This Crate

| Prototype | Direction | Usage |
|-----------|-----------|-------|
| [virtio-block2](/components/instar/prototypes/virtio-block2/) | Guest → VMM | Status, progress, errors |
| [virtio-block3](/components/instar/prototypes/virtio-block3/) | Bidirectional | Status + configuration |
| [info](https://github.com/shakenfist/instar/tree/develop/prototypes/info) | Bidirectional | Config + InfoResultMessage |
