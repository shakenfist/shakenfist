# Phase 2: usbredir protocol parser

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Implement parsing and serialisation of usbredir protocol
messages as a standalone `src/usbredir/` module. Wire the
parser into the existing `UsbredirChannel` so that received
VMC data is decoded into typed messages and logged. After
this phase, connecting to a QEMU VM with USB redirection
will show the usbredir hello exchange in the logs.

No USB device access, device backend trait, or UI changes
in this phase — those come in phases 3-8.

## Background

### Protocol layering

Phase 1 established the SPICE SpiceVMC transport: the
`UsbredirChannel` receives `SPICEVMC_DATA` messages and
accumulates the raw payloads in `usbredir_buf`. The
usbredir protocol is layered inside this byte stream.

Each usbredir message has a header followed by an optional
payload:

```
┌─────────────────── usbredir header ──────────────────┐
│ type (u32le) │ length (u32le) │ id (u32le)           │
├──────────────┴────────────────┴──────────────────────┤
│ payload (length bytes)                                │
└──────────────────────────────────────────────────────┘
```

The `type` field identifies the message. The `length` field
gives the payload size (excluding the 12-byte header). The
`id` field is a correlation ID used to match requests with
responses for data transfer messages.

### Message catalogue

**Control messages (types 0-27):**

| Type | Name | Payload bytes | Direction |
|------|------|--------------|-----------|
| 0 | hello | 68 | both (first message) |
| 1 | device_connect | 10 | client→server |
| 2 | device_disconnect | 0 | client→server |
| 3 | reset | 0 | server→client |
| 4 | interface_info | 128 | client→server |
| 5 | ep_info | 160 | client→server |
| 6 | set_configuration | 1 | server→client |
| 7 | get_configuration | 0 | server→client |
| 8 | configuration_status | 2 | client→server |
| 9 | set_alt_setting | 2 | server→client |
| 10 | get_alt_setting | 1 | server→client |
| 11 | alt_setting_status | 3 | client→server |
| 15 | start_interrupt_receiving | 1 | server→client |
| 16 | stop_interrupt_receiving | 1 | server→client |
| 17 | interrupt_receiving_status | 2 | client→server |
| 21 | cancel_data_packet | 0 | server→client |
| 22 | filter_reject | 0 | server→client |
| 24 | device_disconnect_ack | 0 | server→client |

**Data transfer messages (types 100-103):**

| Type | Name | Header bytes | Has data |
|------|------|-------------|----------|
| 100 | control_packet | 10 | yes |
| 101 | bulk_packet | 10 | yes |
| 102 | iso_packet | 4 | yes |
| 103 | interrupt_packet | 4 | yes |

### Scope decisions

- **32-bit IDs only**: the master plan defers 64-bit IDs
  (`cap_64bits_ids`) to future work. The header is always
  12 bytes.
- **No iso/buffered_bulk**: types 12-14, 18-20, 25-27,
  102, 104 are out of scope (isochronous and bulk streams
  are future work). The parser will log them as unknown.
- **Filter messages**: type 22 (filter_reject) is parsed
  as a zero-payload message. Type 23 (filter_filter) is
  variable-length and rare; parse header only, log payload.
- **All messages get structs**: even zero-payload messages
  get entries in the `UsbredirMessage` enum for completeness.

## Detailed steps

### Step 1: Create module structure

Create `src/usbredir/` with four files:

```
src/usbredir/
├── mod.rs         — re-exports, module declarations
├── constants.rs   — message types, capabilities, status codes
├── messages.rs    — per-message structs with read()/write()
└── parser.rs      — UsbredirParser: accumulate bytes, extract messages
```

Add `mod usbredir;` to `src/main.rs` (or wherever modules
are declared — check the existing pattern).

### Step 2: Define constants (`src/usbredir/constants.rs`)

**Message type constants:**

```rust
pub mod msg_type {
    pub const HELLO: u32 = 0;
    pub const DEVICE_CONNECT: u32 = 1;
    pub const DEVICE_DISCONNECT: u32 = 2;
    pub const RESET: u32 = 3;
    pub const INTERFACE_INFO: u32 = 4;
    pub const EP_INFO: u32 = 5;
    pub const SET_CONFIGURATION: u32 = 6;
    pub const GET_CONFIGURATION: u32 = 7;
    pub const CONFIGURATION_STATUS: u32 = 8;
    pub const SET_ALT_SETTING: u32 = 9;
    pub const GET_ALT_SETTING: u32 = 10;
    pub const ALT_SETTING_STATUS: u32 = 11;
    pub const START_ISO_STREAM: u32 = 12;
    pub const STOP_ISO_STREAM: u32 = 13;
    pub const ISO_STREAM_STATUS: u32 = 14;
    pub const START_INTERRUPT_RECEIVING: u32 = 15;
    pub const STOP_INTERRUPT_RECEIVING: u32 = 16;
    pub const INTERRUPT_RECEIVING_STATUS: u32 = 17;
    pub const ALLOC_BULK_STREAMS: u32 = 18;
    pub const FREE_BULK_STREAMS: u32 = 19;
    pub const BULK_STREAMS_STATUS: u32 = 20;
    pub const CANCEL_DATA_PACKET: u32 = 21;
    pub const FILTER_REJECT: u32 = 22;
    pub const FILTER_FILTER: u32 = 23;
    pub const DEVICE_DISCONNECT_ACK: u32 = 24;
    pub const START_BULK_RECEIVING: u32 = 25;
    pub const STOP_BULK_RECEIVING: u32 = 26;
    pub const BULK_RECEIVING_STATUS: u32 = 27;

    pub const CONTROL_PACKET: u32 = 100;
    pub const BULK_PACKET: u32 = 101;
    pub const ISO_PACKET: u32 = 102;
    pub const INTERRUPT_PACKET: u32 = 103;
    pub const BUFFERED_BULK_PACKET: u32 = 104;
}
```

**Capability bits:**

```rust
pub mod cap {
    pub const BULK_STREAMS: u32 = 1 << 0;
    pub const CONNECT_DEVICE_VERSION: u32 = 1 << 1;
    pub const FILTER: u32 = 1 << 2;
    pub const DEVICE_DISCONNECT_ACK: u32 = 1 << 3;
    pub const EP_INFO_MAX_PACKET_SIZE: u32 = 1 << 4;
    pub const _64BITS_IDS: u32 = 1 << 5;
    pub const _32BITS_BULK_LENGTH: u32 = 1 << 6;
    pub const BULK_RECEIVING: u32 = 1 << 7;
}
```

Note: prefix with `_` for constants starting with a digit,
since Rust identifiers cannot start with a number.

**Ryll's default capabilities** (what we advertise in hello):

```rust
pub const RYLL_CAPS: u32 =
    cap::CONNECT_DEVICE_VERSION
    | cap::DEVICE_DISCONNECT_ACK
    | cap::EP_INFO_MAX_PACKET_SIZE;
```

We do NOT advertise `cap::_64BITS_IDS` (32-bit only),
`cap::BULK_STREAMS`, `cap::BULK_RECEIVING`, or
`cap::_32BITS_BULK_LENGTH` initially.

**Status codes:**

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Status {
    Success = 0,
    Cancelled = 1,
    Inval = 2,
    Ioerror = 3,
    Stall = 4,
    Timeout = 5,
    Babble = 6,
}
```

**USB speed values:**

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum UsbSpeed {
    Unknown = 0,
    Low = 1,
    Full = 2,
    High = 3,
    Super = 4,
}
```

**Endpoint types:**

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum EpType {
    Control = 0,
    Iso = 1,
    Bulk = 2,
    Interrupt = 3,
    Invalid = 255,
}
```

**Message type to name mapping** (for logging):

```rust
pub fn msg_type_name(t: u32) -> &'static str { ... }
```

### Step 3: Define message structs (`src/usbredir/messages.rs`)

Follow the existing ryll pattern: each struct has a
`pub const SIZE: usize`, a `pub fn read(data: &[u8])`,
and a `pub fn write(&self, buf: &mut Vec<u8>)`. Use
`byteorder` for serialisation.

**Header:**

```rust
#[derive(Debug, Clone)]
pub struct UsbredirHeader {
    pub msg_type: u32,
    pub length: u32,
    pub id: u32,
}
// SIZE = 12
```

**Hello (type 0):**

```rust
#[derive(Debug, Clone)]
pub struct Hello {
    pub version: String,  // max 64 bytes, null-terminated
    pub capabilities: u32,
}
// SIZE = 68
```

The `version` field is a 64-byte buffer. On read, find the
first null byte and take the string up to that point. On
write, pad with zeros to exactly 64 bytes.

**DeviceConnect (type 1):**

```rust
#[derive(Debug, Clone)]
pub struct DeviceConnect {
    pub speed: u8,
    pub device_class: u8,
    pub device_subclass: u8,
    pub device_protocol: u8,
    pub vendor_id: u16,
    pub product_id: u16,
    pub device_version_bcd: u16,
}
// SIZE = 10
```

**InterfaceInfo (type 4):**

```rust
#[derive(Debug, Clone)]
pub struct InterfaceInfo {
    pub interface_count: [u8; 32],
    pub interface_class: [u8; 32],
    pub interface_subclass: [u8; 32],
    pub interface_protocol: [u8; 32],
}
// SIZE = 128
```

**EpInfo (type 5):**

```rust
#[derive(Debug, Clone)]
pub struct EpInfo {
    pub ep_type: [u8; 32],
    pub ep_interval: [u8; 32],
    pub ep_interface: [u8; 32],
    pub ep_max_packet_size: [u16; 32],
}
// SIZE = 160
```

Read the 32 `u16` values as little-endian.

**SetConfiguration (type 6):**

```rust
#[derive(Debug, Clone)]
pub struct SetConfiguration {
    pub configuration: u8,
}
// SIZE = 1
```

**ConfigurationStatus (type 8):**

```rust
#[derive(Debug, Clone)]
pub struct ConfigurationStatus {
    pub status: u8,
    pub configuration: u8,
}
// SIZE = 2
```

**SetAltSetting (type 9):**

```rust
#[derive(Debug, Clone)]
pub struct SetAltSetting {
    pub interface: u8,
    pub alt_setting: u8,
}
// SIZE = 2
```

**GetAltSetting (type 10):**

```rust
#[derive(Debug, Clone)]
pub struct GetAltSetting {
    pub interface: u8,
}
// SIZE = 1
```

**AltSettingStatus (type 11):**

```rust
#[derive(Debug, Clone)]
pub struct AltSettingStatus {
    pub status: u8,
    pub interface: u8,
    pub alt_setting: u8,
}
// SIZE = 3
```

**StartInterruptReceiving (type 15):**

```rust
#[derive(Debug, Clone)]
pub struct StartInterruptReceiving {
    pub endpoint: u8,
}
// SIZE = 1
```

**StopInterruptReceiving (type 16):**

```rust
#[derive(Debug, Clone)]
pub struct StopInterruptReceiving {
    pub endpoint: u8,
}
// SIZE = 1
```

**InterruptReceivingStatus (type 17):**

```rust
#[derive(Debug, Clone)]
pub struct InterruptReceivingStatus {
    pub status: u8,
    pub endpoint: u8,
}
// SIZE = 2
```

**CancelDataPacket (type 21):**

No payload — identified by the header's id field.

**ControlPacket (type 100):**

```rust
#[derive(Debug, Clone)]
pub struct ControlPacketHeader {
    pub endpoint: u8,
    pub request: u8,
    pub request_type: u8,
    pub status: u8,
    pub value: u16,
    pub index: u16,
    pub length: u16,
}
// SIZE = 10
// data follows: payload[10..] when length > 0
```

**BulkPacketHeader (type 101):**

```rust
#[derive(Debug, Clone)]
pub struct BulkPacketHeader {
    pub endpoint: u8,
    pub status: u8,
    pub length: u16,
    pub stream_id: u32,
    pub length_high: u16,
}
// SIZE = 10
// data follows: payload[10..]
// actual_length = (length_high as u32) << 16 | length as u32
```

**InterruptPacketHeader (type 103):**

```rust
#[derive(Debug, Clone)]
pub struct InterruptPacketHeader {
    pub endpoint: u8,
    pub status: u8,
    pub length: u16,
}
// SIZE = 4
// data follows: payload[4..]
```

### Step 4: Define the parsed message enum

In `src/usbredir/messages.rs` (or `mod.rs`):

```rust
/// A fully parsed usbredir message with header and payload.
#[derive(Debug, Clone)]
pub struct UsbredirMessage {
    pub id: u32,
    pub payload: UsbredirPayload,
}

#[derive(Debug, Clone)]
pub enum UsbredirPayload {
    Hello(Hello),
    DeviceConnect(DeviceConnect),
    DeviceDisconnect,
    Reset,
    InterfaceInfo(InterfaceInfo),
    EpInfo(EpInfo),
    SetConfiguration(SetConfiguration),
    GetConfiguration,
    ConfigurationStatus(ConfigurationStatus),
    SetAltSetting(SetAltSetting),
    GetAltSetting(GetAltSetting),
    AltSettingStatus(AltSettingStatus),
    StartInterruptReceiving(StartInterruptReceiving),
    StopInterruptReceiving(StopInterruptReceiving),
    InterruptReceivingStatus(InterruptReceivingStatus),
    CancelDataPacket,
    FilterReject,
    DeviceDisconnectAck,
    ControlPacket {
        header: ControlPacketHeader,
        data: Vec<u8>,
    },
    BulkPacket {
        header: BulkPacketHeader,
        data: Vec<u8>,
    },
    InterruptPacket {
        header: InterruptPacketHeader,
        data: Vec<u8>,
    },
    /// Message type we recognise but don't fully parse yet
    Unknown {
        msg_type: u32,
        data: Vec<u8>,
    },
}
```

### Step 5: Implement the parser (`src/usbredir/parser.rs`)

The parser is a simple state machine that accumulates bytes
and extracts complete messages.

```rust
pub struct UsbredirParser {
    buf: Vec<u8>,
}

impl UsbredirParser {
    pub fn new() -> Self {
        UsbredirParser {
            buf: Vec::with_capacity(65536),
        }
    }

    /// Feed raw bytes from the VMC channel.
    pub fn feed(&mut self, data: &[u8]) {
        self.buf.extend_from_slice(data);
    }

    /// Try to parse and return the next complete message.
    /// Returns None if not enough data is available.
    pub fn next_message(&mut self)
        -> Result<Option<UsbredirMessage>>
    {
        if self.buf.len() < UsbredirHeader::SIZE {
            return Ok(None);
        }

        let header = UsbredirHeader::read(&self.buf)?;
        let total = UsbredirHeader::SIZE
            + header.length as usize;

        if self.buf.len() < total {
            return Ok(None);
        }

        let payload_bytes = self.buf
            [UsbredirHeader::SIZE..total].to_vec();
        self.buf.drain(..total);

        let payload = parse_payload(
            header.msg_type,
            &payload_bytes,
        )?;

        Ok(Some(UsbredirMessage {
            id: header.id,
            payload,
        }))
    }
}
```

The `parse_payload()` function dispatches on `msg_type`:

```rust
fn parse_payload(
    msg_type: u32,
    data: &[u8],
) -> Result<UsbredirPayload> {
    match msg_type {
        msg_type::HELLO => {
            Ok(UsbredirPayload::Hello(
                Hello::read(data)?))
        }
        msg_type::DEVICE_CONNECT => {
            Ok(UsbredirPayload::DeviceConnect(
                DeviceConnect::read(data)?))
        }
        msg_type::DEVICE_DISCONNECT => {
            Ok(UsbredirPayload::DeviceDisconnect)
        }
        msg_type::RESET => {
            Ok(UsbredirPayload::Reset)
        }
        // ... etc for all supported types
        msg_type::CONTROL_PACKET => {
            let header = ControlPacketHeader::read(data)?;
            let pdata = data.get(
                ControlPacketHeader::SIZE..)
                .unwrap_or(&[]).to_vec();
            Ok(UsbredirPayload::ControlPacket {
                header, data: pdata,
            })
        }
        // ... bulk, interrupt similarly
        _ => {
            Ok(UsbredirPayload::Unknown {
                msg_type,
                data: data.to_vec(),
            })
        }
    }
}
```

### Step 6: Wire the parser into UsbredirChannel

Modify `src/channels/usbredir.rs`:

1. Replace `usbredir_buf: Vec<u8>` with
   `parser: UsbredirParser`.

2. In `handle_vmc_data()`, instead of extending
   `usbredir_buf`, call `self.parser.feed(payload)` then
   drain messages in a loop:

   ```rust
   async fn handle_vmc_data(
       &mut self,
       payload: &[u8],
   ) -> Result<()> {
       self.parser.feed(payload);

       while let Some(msg) = self.parser.next_message()? {
           self.handle_usbredir_message(msg).await?;
       }

       Ok(())
   }
   ```

3. Add `handle_usbredir_message()` which logs each parsed
   message. For phase 2 this is logging only — no device
   interaction:

   ```rust
   async fn handle_usbredir_message(
       &mut self,
       msg: UsbredirMessage,
   ) -> Result<()> {
       match &msg.payload {
           UsbredirPayload::Hello(hello) => {
               info!(
                   "usbredir: server hello: version='{}' \
                    caps=0x{:08x}",
                   hello.version, hello.capabilities,
               );
               // Send our hello back (phase 5 will do
               // this properly; for now just log)
           }
           UsbredirPayload::Reset => {
               info!("usbredir: reset requested (id={})",
                     msg.id);
           }
           // ... log each message type
           _ => {
               debug!(
                   "usbredir: received {:?} (id={})",
                   msg.payload, msg.id,
               );
           }
       }
       Ok(())
   }
   ```

### Step 7: Add serialisation helpers for client messages

The client needs to send hello, device_connect, ep_info,
interface_info, and data transfer responses. Add `write()`
methods to:

- `Hello::write(&self, buf: &mut Vec<u8>)`
- `DeviceConnect::write(&self, buf: &mut Vec<u8>)`
- `InterfaceInfo::write(&self, buf: &mut Vec<u8>)`
- `EpInfo::write(&self, buf: &mut Vec<u8>)`
- `ConfigurationStatus::write(&self, buf: &mut Vec<u8>)`
- `AltSettingStatus::write(&self, buf: &mut Vec<u8>)`
- `InterruptReceivingStatus::write(&self, buf: &mut Vec<u8>)`
- `ControlPacketHeader::write(&self, buf: &mut Vec<u8>)`
- `BulkPacketHeader::write(&self, buf: &mut Vec<u8>)`
- `InterruptPacketHeader::write(&self, buf: &mut Vec<u8>)`

Also add a helper to build a complete usbredir message
(header + payload):

```rust
pub fn make_usbredir_message(
    msg_type: u32,
    id: u32,
    payload: &[u8],
) -> Vec<u8> {
    let mut buf = Vec::with_capacity(
        UsbredirHeader::SIZE + payload.len());
    let header = UsbredirHeader {
        msg_type,
        length: payload.len() as u32,
        id,
    };
    header.write(&mut buf).unwrap();
    buf.extend_from_slice(payload);
    buf
}
```

### Step 8: Unit tests

Create `src/usbredir/tests.rs` (or use `#[cfg(test)]`
modules inline). Test:

1. **Header round-trip**: write a header, read it back,
   verify fields match.

2. **Hello round-trip**: construct a Hello with a version
   string and capabilities, write it, read it back.
   Test edge cases: empty version string, max-length
   (64 bytes) version string, version with embedded nulls.

3. **DeviceConnect round-trip**: all fields populated,
   verify endianness is correct.

4. **EpInfo round-trip**: verify the 32 u16 values are
   serialised as little-endian and the total size is 160.

5. **InterfaceInfo round-trip**: verify 128 bytes.

6. **ControlPacketHeader round-trip**: verify all 10 bytes.

7. **BulkPacketHeader round-trip**: verify length_high.

8. **Parser: single message**: feed exactly one hello
   message's bytes, verify `next_message()` returns it.

9. **Parser: split delivery**: feed a hello in two halves,
   verify first `next_message()` returns None, second
   returns the message.

10. **Parser: multiple messages**: feed two messages back
    to back, verify both are returned in order.

11. **Parser: unknown type**: feed a message with an
    unrecognised type, verify it returns `Unknown`.

12. **Zero-payload messages**: verify DeviceDisconnect,
    Reset, CancelDataPacket parse correctly with length=0.

## Files changed

| File | Change |
|------|--------|
| `src/usbredir/mod.rs` | **New** — module declarations and re-exports |
| `src/usbredir/constants.rs` | **New** — message types, caps, status, speed, ep types |
| `src/usbredir/messages.rs` | **New** — message structs, UsbredirPayload enum, read/write |
| `src/usbredir/parser.rs` | **New** — UsbredirParser with feed()/next_message() |
| `src/channels/usbredir.rs` | Replace `usbredir_buf` with `UsbredirParser`; add `handle_usbredir_message()` |
| `src/main.rs` | Add `mod usbredir;` (if needed — check module declaration pattern) |

## What is NOT in scope

- Sending usbredir hello or any other message to the server
  (phase 5 does the hello handshake, but phase 2 adds the
  write helpers so they're ready).
- Device backend trait or USB device access (phases 3-4).
- Acting on parsed messages (phases 5-6 will dispatch
  parsed messages to the device backend).
- Isochronous or bulk streams message types (future work).
- 64-bit correlation IDs (future work).

## Testing

### Build and lint

```
./scripts/check-rust.sh fix
pre-commit run --all-files
```

### Unit tests

```
cargo test --lib usbredir
```

All round-trip and parser tests should pass.

### Manual integration test

1. Start QEMU with USB redirection (same as phase 1).
2. Connect ryll with `--verbose`.
3. Verify the log shows:
   - `"usbredir: server hello: version='...' caps=0x..."`
     shortly after channel connection.
   - Any subsequent usbredir messages from the server are
     logged with their parsed type rather than raw bytes.

## Back brief

Before starting this phase, confirm understanding: we are
building the usbredir protocol parser as a standalone module
with no SPICE or device dependencies. The parser consumes
raw bytes, extracts typed messages, and returns them to the
channel handler for logging. Serialisation helpers are
provided so future phases can send messages. Unit tests
cover round-trip correctness and parser edge cases.
