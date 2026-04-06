# Phase 2: Mux protocol (demux and remux)

## Overview

Implement the client-multiplexing protocol that sits between
the raw SpiceVMC byte stream and the per-client HTTP
connections. The SPICE WebDAV channel carries HTTP traffic
for potentially many concurrent clients (the guest's
`spice-webdavd` may issue parallel requests) over a single
byte stream. Each chunk of data is prefixed with a client ID
and size, allowing the client to route data to/from the
correct HTTP connection.

This phase implements the mux framing layer only. The actual
WebDAV server that processes the HTTP requests is stubbed —
each client connection simply logs the data it receives. The
WebDAV server integration happens in phases 3 and 4.

## Wire format

Each mux frame is:

```
client_id:  i64 LE  (8 bytes) — identifies the HTTP client
data_size:  u16 LE  (2 bytes) — payload size (0..65535)
data:       [u8]    (data_size bytes) — HTTP request/response
```

The demux flow reads from the accumulated VMC data buffer:

1. Read 8 bytes → `client_id`.
2. Read 2 bytes → `data_size`.
3. Read `data_size` bytes → payload.
4. Look up `client_id` in the client map:
   - **Found, data_size > 0**: forward data to that client.
   - **Not found, data_size > 0**: create a new client,
     then forward data.
   - **Found, data_size == 0**: client disconnected, remove
     from map.
   - **Not found, data_size == 0**: ignore (stale close).
5. Repeat from step 1 while data remains.

The remux flow (sending responses back to the guest) writes
frames in the same format: `[client_id | size | data]`,
sent as `SPICEVMC_DATA` messages via the channel's
`send_data()` method.

A response with `data_size == 0` signals the server (client
side) closing the connection for that client ID.

## Reference implementation

The spice-gtk implementation in `channel-webdav.c` uses
GLib async I/O callbacks:

- `start_demux()` → reads `client_id` via
  `spice_vmc_input_stream_read_all_async()`.
- `client_read_cb()` → reads `data_size`.
- `size_read_cb()` → reads `data_size` bytes of payload.
- `data_read_cb()` → dispatches to existing or new client.
- `server_reply_cb()` → reads server response, muxes it
  back via `spice_vmc_write_async()`.

Key constants:
- `MAX_MUX_SIZE` = `G_MAXUINT16` = 65535
- Clients are stored in a `GHashTable<gint64, Client*>`
- Each `Client` has its own mux output buffer with pre-set
  `client_id` for efficient response framing.

## Files changed

| File | Change |
|------|--------|
| `src/webdav/mod.rs` | **New file.** Module declaration |
| `src/webdav/mux.rs` | **New file.** `MuxDemuxer` and mux frame serialisation |
| `src/channels/webdav.rs` | Replace `handle_vmc_data()` stub with demuxer integration, add response sending |
| `src/main.rs` | Add `mod webdav;` |

## Detailed steps

### Step 1: Create `src/webdav/mod.rs`

Create the webdav module with a public mux submodule:

```rust
pub mod mux;
```

Add `mod webdav;` to `src/main.rs` alongside the existing
`mod usbredir;`.

### Step 2: Implement `src/webdav/mux.rs`

This module implements mux frame parsing, serialisation, and
client tracking.

**Mux frame constants and types:**

```rust
/// Maximum payload per mux frame (u16::MAX).
pub const MAX_MUX_SIZE: usize = 65535;

/// A parsed mux frame from the guest.
#[derive(Debug)]
pub struct MuxFrame {
    pub client_id: i64,
    pub data: Vec<u8>,  // empty vec = client disconnect
}
```

**`MuxDemuxer` struct:**

Accumulates raw bytes from the VMC channel and extracts
complete mux frames. The demuxer is a simple state machine:

```rust
pub struct MuxDemuxer {
    buf: Vec<u8>,
}
```

Methods:

- `pub fn new() -> Self` — create with empty buffer.
- `pub fn feed(&mut self, data: &[u8])` — append data.
- `pub fn next_frame(&mut self) -> Option<MuxFrame>` —
  try to extract the next complete frame:
  - Need at least 10 bytes (8 for client_id + 2 for size).
  - Read `client_id` as i64 LE from bytes 0..8.
  - Read `data_size` as u16 LE from bytes 8..10.
  - Need at least `10 + data_size` bytes total.
  - If complete: drain `10 + data_size` bytes from buffer,
    return `Some(MuxFrame { client_id, data })`.
  - If incomplete: return `None`.

**Mux frame serialisation (for responses):**

```rust
/// Serialise a mux frame for sending to the guest.
pub fn encode_mux_frame(
    client_id: i64,
    data: &[u8],
) -> Vec<u8>
```

Writes `[client_id as i64 LE | data.len() as u16 LE | data]`.
Panics if `data.len() > MAX_MUX_SIZE`.

**Unit tests:**

- Parse a single frame from exact bytes.
- Parse a frame from bytes arriving in multiple `feed()`
  calls (split mid-header, split mid-payload).
- Parse multiple frames from a single `feed()` call.
- Parse a zero-length frame (client disconnect).
- `next_frame()` returns `None` when buffer is incomplete.
- Round-trip: encode a frame, feed it to a demuxer, verify
  the parsed frame matches.
- Edge cases: maximum size frame (65535 bytes), empty
  buffer, client_id of 0 and negative values.

### Step 3: Add client tracking to `WebdavChannel`

Add a `HashMap<i64, ClientState>` to the `WebdavChannel`
struct for tracking active mux clients. In this phase,
`ClientState` is minimal — it just records that the client
exists:

```rust
struct MuxClient {
    /// Bytes of HTTP request data received so far
    /// (for logging/debugging until the WebDAV server
    /// is connected in phase 4).
    bytes_received: u64,
}
```

Add a `MuxDemuxer` field to the channel struct.

### Step 4: Wire up demuxer in `handle_vmc_data()`

Replace the stub `handle_vmc_data()` in
`src/channels/webdav.rs` (currently at line 234) with:

```rust
async fn handle_vmc_data(
    &mut self,
    payload: &[u8],
) -> Result<()> {
    self.demuxer.feed(payload);

    while let Some(frame) = self.demuxer.next_frame() {
        self.handle_mux_frame(frame).await?;
    }

    Ok(())
}
```

Implement `handle_mux_frame()`:

- If `frame.data.is_empty()` (client disconnect):
  - Remove client from the map if present.
  - Log at debug level.
- Else if client exists in the map:
  - Update `bytes_received`.
  - Log data receipt at debug level.
  - (Phase 4 will forward to the WebDAV server here.)
- Else (new client):
  - Insert new `MuxClient` into the map.
  - Log new client creation at info level.
  - (Phase 4 will create a WebDAV server connection here.)

### Step 5: Add response sending helper

Add a method to `WebdavChannel` for sending mux-framed
responses back to the guest:

```rust
/// Send a mux-framed response to the guest.
async fn send_mux_frame(
    &mut self,
    client_id: i64,
    data: &[u8],
) -> Result<()> {
    let frame = mux::encode_mux_frame(client_id, data);
    self.send_data(&frame).await
}
```

This is not called in this phase (no WebDAV server to
produce responses yet), but having it in place makes the
phase 4 integration straightforward. Mark with
`#[allow(dead_code)]`.

Also add a helper to send a client-disconnect frame:

```rust
/// Signal to the guest that we are closing a client.
async fn send_mux_close(
    &mut self,
    client_id: i64,
) -> Result<()> {
    self.send_mux_frame(client_id, &[]).await
}
```

## Testing

- `make test` passes (existing tests unbroken).
- `cargo fmt --check` and `cargo clippy -- -D warnings`
  pass.
- Unit tests in `src/webdav/mux.rs` cover:
  - Single-frame parsing.
  - Multi-frame parsing from one buffer.
  - Incremental feeding (partial frames).
  - Zero-length (disconnect) frames.
  - Round-trip encode/parse.
  - Maximum-size frames.
- When connected to `make test-qemu-webdav` and a
  `--share-dir` is specified, the channel logs received mux
  frames from the guest (client IDs, data sizes) at debug
  level. This confirms the demuxer is parsing real traffic
  correctly even though responses are not yet generated.

## Back brief

Before executing, please confirm your understanding of:
1. This phase implements mux framing only — no HTTP parsing,
   no WebDAV server, no file I/O.
2. The `MuxDemuxer` accumulates bytes and extracts frames;
   it does not assume one VMC message = one mux frame
   (frames may span multiple VMC messages or multiple frames
   may arrive in one VMC message).
3. Client tracking is minimal (just existence + byte count)
   and will be extended in phase 4 with actual I/O streams.
4. Response sending helpers are added but not yet called.
5. Unit tests cover the mux parsing thoroughly since this
   is the foundation for all WebDAV communication.
