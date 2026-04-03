# Phase 1: SpiceVMC channel transport

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Implement the SPICE-level channel that carries usbredir
data. After this phase, ryll will connect a usbredir channel
when the server advertises one, exchange SPICE-level
messages (DATA, COMPRESSED_DATA, SET_ACK, PING), and
forward the raw payload bytes to a stub handler that logs
them. No usbredir protocol parsing yet — that is phase 2.

## Background

### SpiceVMC channel model

The usbredir channel (type 9) is an instance of
`SpicevmcChannel`, which inherits from `BaseChannel`. The
SPICE protocol defines only two channel-specific message
types for both directions:

| ID | Server→Client | Client→Server |
|----|--------------|---------------|
| 101 | `SPICEVMC_DATA` | `SPICEVMC_DATA` |
| 102 | `SPICEVMC_COMPRESSED_DATA` | `SPICEVMC_COMPRESSED_DATA` |

Plus the standard BaseChannel messages inherited by all
channels:

| ID (server) | ID (client) | Name |
|-------------|-------------|------|
| 3 | 1 | SET_ACK / ACK_SYNC |
| 4 | 3 | PING / PONG |
| — | 2 | ACK |

### SpiceMsgCompressedData wire format

When the server sends `SPICEVMC_COMPRESSED_DATA` (102),
the payload after the 6-byte mini-header contains:

```
Offset  Size  Field
------  ----  -----
0       1     compression_type (0=none, 1=LZ4)
1       4     uncompressed_size (uint32_le)
5       ...   compressed_data (rest of payload)
```

The `compressed_data` length is
`message_size - 5` (the message_size from the SPICE
mini-header minus the 5-byte compressed-data header).
Decompress with `lz4_flex::decompress()` using
`uncompressed_size` as the expected output length.

### Capability negotiation

The usbredir channel advertises one channel-specific
capability during the link handshake:

- `SPICEVMC_CAP_DATA_COMPRESS_LZ4` (bit 0, value 1)

This tells the server that the client can receive
LZ4-compressed VMC data. Since ryll already depends on
`lz4_flex`, we should advertise this capability.

## Detailed steps

### Step 1: Add SpiceVMC constants to `src/protocol/constants.rs`

Add three new modules after the existing cursor constants
(after line 247):

```rust
/// SpiceVMC channel message types (server -> client)
///
/// Used by usbredir (type 9), port (type 10), and
/// webdav (type 11) channels.
pub mod spicevmc_server {
    pub const DATA: u16 = 101;
    pub const COMPRESSED_DATA: u16 = 102;
    pub const SET_ACK: u16 = 3;
    pub const PING: u16 = 4;
}

/// SpiceVMC channel message types (client -> server)
pub mod spicevmc_client {
    pub const DATA: u16 = 101;
    pub const COMPRESSED_DATA: u16 = 102;
    pub const ACK_SYNC: u16 = 1;
    pub const ACK: u16 = 2;
    pub const PONG: u16 = 3;
}
```

Add a capability constant in the `capabilities` module
(near line 126):

```rust
/// SpiceVMC channel capabilities
pub const SPICEVMC_LZ4: u32 = 1; // bit 0
```

Add a default capabilities set:

```rust
pub const DEFAULT_SPICEVMC: u32 = SPICEVMC_LZ4;
```

**Rationale for naming**: Using `spicevmc_server` /
`spicevmc_client` rather than `usbredir_server` /
`usbredir_client` because these message types are shared
by all SpiceVMC-derived channels (usbredir, port, webdav).
If we later add port or webdav channels, the constants
are already correct.

### Step 2: Add SpiceVMC capability to link handshake

In `src/protocol/link.rs`, update the `perform_link()`
capability selection (around line 282):

```rust
let channel_caps = match channel_type {
    ChannelType::Display => capabilities::DEFAULT_DISPLAY,
    ChannelType::Usbredir => capabilities::DEFAULT_SPICEVMC,
    _ => capabilities::DEFAULT_MAIN,
};
```

This advertises LZ4 compression support to the server
during the usbredir channel link handshake.

### Step 3: Add ChannelEvent variants

In `src/channels/mod.rs`, add to the `ChannelEvent` enum:

```rust
/// A USB redirection channel connected successfully.
UsbChannelReady,
```

This is a minimal event for phase 1 — later phases will
add richer events for device enumeration, connection
status, etc. For now it lets the app know the channel
is alive.

### Step 4: Create `src/channels/usbredir.rs`

This is the main deliverable. The structure follows the
cursor channel pattern exactly.

**Struct definition:**

```rust
pub struct UsbredirChannel {
    stream: SpiceStream,
    event_tx: mpsc::Sender<ChannelEvent>,
    buffer: Vec<u8>,
    capture: Option<Arc<CaptureSession>>,
    byte_counter: Arc<ByteCounter>,

    // BaseChannel ACK state
    ack_generation: u32,
    ack_window: u32,
    message_count: u32,
    last_ack: u32,

    // Statistics
    bytes_in: u64,
    bytes_out: u64,

    // Usbredir payload buffer: accumulates deframed
    // VMC data payloads for the usbredir parser
    // (stubbed in this phase — just logged).
    usbredir_buf: Vec<u8>,
}
```

**Constructor:**

```rust
pub fn new(
    stream: SpiceStream,
    event_tx: mpsc::Sender<ChannelEvent>,
    capture: Option<Arc<CaptureSession>>,
    byte_counter: Arc<ByteCounter>,
) -> Self
```

Same signature as CursorChannel. Initialise `buffer` with
capacity 65536, `usbredir_buf` with capacity 65536.

**`run()` method:**

Follow the cursor channel's async read loop exactly:

1. Log channel start.
2. Send `ChannelEvent::UsbChannelReady`.
3. Loop:
   a. Read up to 64KB from stream.
   b. On EOF, send `Disconnected(ChannelType::Usbredir)`
      and break.
   c. Update byte_counter, capture, buffer.
   d. Call `process_messages().await?`.

**`process_messages()` method:**

Identical to cursor channel: parse MessageHeader, check
complete message available, extract payload, drain buffer,
increment message_count, call handle_message(), check ACK
window.

**`handle_message()` method:**

```rust
async fn handle_message(
    &mut self,
    msg_type: u16,
    payload: &[u8],
) -> Result<()> {
    match msg_type {
        spicevmc_server::DATA => {
            self.handle_vmc_data(payload).await?;
        }
        spicevmc_server::COMPRESSED_DATA => {
            self.handle_vmc_compressed_data(payload).await?;
        }
        spicevmc_server::SET_ACK => {
            // Standard BaseChannel ACK window setup
            let set_ack = SetAck::read(payload)?;
            self.ack_generation = set_ack.generation;
            self.ack_window = set_ack.window;
            let mut buf = Vec::new();
            SetAck::write_ack_sync(
                set_ack.generation,
                &mut buf,
            )?;
            let response = make_message(
                spicevmc_client::ACK_SYNC,
                &buf,
            );
            self.send_with_log(
                spicevmc_client::ACK_SYNC,
                &response,
            ).await?;
        }
        spicevmc_server::PING => {
            // Standard PONG response
            let ping = Ping::read(payload)?;
            let mut buf = Vec::new();
            Ping::write_pong(ping.id, &mut buf)?;
            let response = make_message(
                spicevmc_client::PONG,
                &buf,
            );
            self.send_with_log(
                spicevmc_client::PONG,
                &response,
            ).await?;
        }
        _ => {
            logging::log_unknown(
                "usbredir",
                "received",
                msg_type,
                payload.len() as u32,
                payload,
            );
        }
    }
    Ok(())
}
```

**`handle_vmc_data()` method:**

```rust
async fn handle_vmc_data(
    &mut self,
    payload: &[u8],
) -> Result<()> {
    if settings::is_verbose() {
        logging::log_message(
            "received", "usbredir",
            spicevmc_server::DATA, "vmc_data",
            payload.len() as u32,
        );
    }

    // Append to usbredir buffer for future parser
    self.usbredir_buf.extend_from_slice(payload);

    // Phase 1 stub: log the accumulated bytes
    debug!(
        "usbredir: received {} bytes VMC data \
         ({} bytes buffered)",
        payload.len(),
        self.usbredir_buf.len(),
    );

    Ok(())
}
```

**`handle_vmc_compressed_data()` method:**

```rust
async fn handle_vmc_compressed_data(
    &mut self,
    payload: &[u8],
) -> Result<()> {
    // Parse compressed data header (5 bytes minimum)
    if payload.len() < 5 {
        warn!(
            "usbredir: compressed_data too short: {} bytes",
            payload.len(),
        );
        return Ok(());
    }

    let compression_type = payload[0];
    let uncompressed_size = u32::from_le_bytes(
        payload[1..5].try_into().unwrap(),
    ) as usize;
    let compressed = &payload[5..];

    if settings::is_verbose() {
        logging::log_message(
            "received", "usbredir",
            spicevmc_server::COMPRESSED_DATA,
            "vmc_compressed_data",
            payload.len() as u32,
        );
        logging::log_detail(&format!(
            "type={} uncompressed={} compressed={}",
            compression_type,
            uncompressed_size,
            compressed.len(),
        ));
    }

    match compression_type {
        1 => {
            // LZ4
            let decompressed = lz4_flex::decompress(
                compressed,
                uncompressed_size,
            ).map_err(|e| {
                anyhow::anyhow!(
                    "usbredir: LZ4 decompress failed: {}",
                    e,
                )
            })?;

            if decompressed.len() != uncompressed_size {
                warn!(
                    "usbredir: LZ4 size mismatch: \
                     expected {} got {}",
                    uncompressed_size,
                    decompressed.len(),
                );
            }

            // Treat decompressed data as VMC_DATA
            self.handle_vmc_data(&decompressed).await?;
        }
        0 => {
            // No compression — treat payload as raw data
            self.handle_vmc_data(compressed).await?;
        }
        _ => {
            warn!(
                "usbredir: unknown compression type {}",
                compression_type,
            );
        }
    }

    Ok(())
}
```

**`send_data()` method** (for future phases to send VMC
data to the server):

```rust
pub async fn send_data(
    &mut self,
    data: &[u8],
) -> Result<()> {
    let msg = make_message(spicevmc_client::DATA, data);
    self.send_with_log(spicevmc_client::DATA, &msg).await
}
```

**`send_ack()`, `send_with_log()`, `send()` methods:**

Copy from cursor channel verbatim, replacing channel
name string `"cursor"` with `"usbredir"` in all logging
and capture calls.

### Step 5: Register the channel module

In `src/channels/mod.rs`:

```rust
pub mod usbredir;
pub use usbredir::UsbredirChannel;
```

### Step 6: Spawn the channel in `src/app.rs`

In `run_connection()`, inside the channel type match
(around line 818 where the `_ =>` fallthrough currently
skips unknown channels), add:

```rust
ChannelType::Usbredir => {
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
        capture.clone(),
        byte_counter.clone(),
    );
    handles.push(tokio::spawn(async move {
        channel.run().await
    }));
}
```

In `process_events()`, add a match arm for the new event:

```rust
ChannelEvent::UsbChannelReady => {
    info!("USB redirection channel connected");
}
```

### Step 7: Add pcap capture support

In `src/capture.rs`:

1. Add `"usbredir"` to the `CHANNELS` array (line 161):
   ```rust
   const CHANNELS: &[&str] = &[
       "main", "display", "cursor", "inputs", "usbredir",
   ];
   ```

2. Add the port mapping in `channel_port()` (line 150):
   ```rust
   "usbredir" => 10009,
   ```

   Using 10009 to match the channel type ID (9), following
   the pattern of the other channels (main=10001 for
   type 1, display=10002 for type 2, etc.).

### Step 8: Add message name lookup for logging

In `src/protocol/logging.rs`, add two functions in the
`message_names` module:

```rust
/// Get usbredir/SpiceVMC server message name
pub fn spicevmc_server(msg_type: u16) -> &'static str {
    match msg_type {
        spicevmc_server::DATA => "vmc_data",
        spicevmc_server::COMPRESSED_DATA
            => "vmc_compressed_data",
        spicevmc_server::SET_ACK => "set_ack",
        spicevmc_server::PING => "ping",
        _ => "unknown",
    }
}

/// Get usbredir/SpiceVMC client message name
pub fn spicevmc_client(msg_type: u16) -> &'static str {
    match msg_type {
        spicevmc_client::DATA => "vmc_data",
        spicevmc_client::COMPRESSED_DATA
            => "vmc_compressed_data",
        spicevmc_client::ACK_SYNC => "ack_sync",
        spicevmc_client::ACK => "ack",
        spicevmc_client::PONG => "pong",
        _ => "unknown",
    }
}
```

## Files changed

| File | Change |
|------|--------|
| `src/protocol/constants.rs` | Add `spicevmc_server`, `spicevmc_client` modules; add `SPICEVMC_LZ4` and `DEFAULT_SPICEVMC` capabilities |
| `src/protocol/link.rs` | Add `Usbredir` arm to capability match |
| `src/protocol/logging.rs` | Add `spicevmc_server()` and `spicevmc_client()` name lookup functions |
| `src/channels/mod.rs` | Add `pub mod usbredir` and `UsbChannelReady` event |
| `src/channels/usbredir.rs` | **New file** — UsbredirChannel struct and implementation |
| `src/app.rs` | Add `Usbredir` arm to channel spawn match; handle `UsbChannelReady` event |
| `src/capture.rs` | Add `"usbredir"` to CHANNELS array and channel_port mapping |

## What is NOT in scope

- Parsing usbredir protocol messages (phase 2).
- USB device access or enumeration (phases 3-4).
- Sending usbredir data to the server (phase 5 will use
  `send_data()`).
- UI changes (phase 8).
- The `usbredir_buf` is populated but not consumed — it
  accumulates VMC payloads that the phase 2 parser will
  process.

## Testing

### Build and lint

```
make imago   # (if shared build target)
./scripts/check-rust.sh fix
pre-commit run --all-files
```

### Manual integration test

1. Start a QEMU VM with USB redirection enabled:
   ```
   -device qemu-xhci,id=xhci \
   -chardev spicevmc,id=usbredir1,name=usbredir \
   -device usb-redir,chardev=usbredir1,id=redir1
   ```

2. Connect ryll with verbose logging:
   ```
   ryll --verbose --file connection.vv
   ```

3. Verify in the log output:
   - `"usbredir: channel started"` appears.
   - `"USB redirection channel connected"` appears.
   - SET_ACK/ACK_SYNC exchange is logged.
   - PING/PONG exchange is logged.
   - If the server sends any initial VMC data,
     `"usbredir: received N bytes VMC data"` appears.

4. If capture mode is enabled (`--capture /tmp/cap`),
   verify `usbredir.pcap` is created in the output
   directory.

### What can go wrong

- **Server doesn't advertise usbredir channel**: if the
  QEMU VM doesn't have `-chardev spicevmc` +
  `-device usb-redir`, no usbredir channel will appear
  in the channels list and the code path won't execute.
  This is expected — the channel only connects when the
  server offers it.

- **LZ4 decompression failure**: if the compressed data
  header format doesn't match what the server sends,
  decompression will fail with an error log. The
  compressed_data header format (1 byte type + 4 byte
  uncompressed_size + data) comes from the SPICE protocol
  specification and spice-gtk reference implementation.

## Back brief

Before starting this phase, confirm understanding: we are
adding the SPICE transport layer only. The usbredir
channel will connect, handle BaseChannel messages
(SET_ACK, PING), receive VMC_DATA and
VMC_COMPRESSED_DATA, decompress LZ4 payloads, and buffer
the raw bytes. The buffered bytes are logged but not
parsed — usbredir protocol parsing is phase 2. The
`send_data()` method is provided but not called yet.
