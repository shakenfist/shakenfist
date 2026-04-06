# Phase 4: Integration (mux ↔ WebDAV server)

## Overview

Connect the mux protocol layer (phase 2) to the WebDAV
server (phase 3), completing the end-to-end data path from
guest HTTP requests to local filesystem operations and back.
This is the phase where folder sharing actually starts
working.

## Architecture

The channel's main `run()` loop owns `&mut self` and
processes mux frames synchronously, but each mux client
needs a long-lived hyper connection running as a separate
tokio task. Response data from those tasks needs to flow
back to the channel for muxing onto the SPICE stream.

The solution follows the same pattern as usbredir's interrupt
polling: per-client tasks send response data through an mpsc
channel, and the main `run()` loop selects on that channel
alongside network reads and commands.

Each mux client gets a `tokio::io::duplex()` pair. The
client end is split with `tokio::io::split()` into a write
half (held in `MuxClient`, written to by the main loop when
mux frames arrive) and a read half (consumed by a reader
task that sends response bytes back via mpsc). The server
end is passed whole to `WebdavServer::serve_client()`, where
hyper reads requests from and writes responses to it.

```
Guest (via SPICE VMC)
    │
    ▼
┌─────────────────────────────────────────────┐
│ WebdavChannel::run() event loop             │
│                                             │
│  tokio::select! {                           │
│    network read → demux → write request     │
│      data to client's DuplexStream write    │
│      half                                   │
│                                             │
│    response_rx → send_mux_frame() back      │
│                  to guest via VMC            │
│                                             │
│    webdav_rx → handle ShareDirectory /      │
│                StopSharing commands          │
│  }                                          │
└──────────┬──────────────────────▲────────────┘
      write half            response_tx
           │                     │
           ▼                     │
   ┌──────────────┐    ┌──────────────────┐
   │ DuplexStream │    │ Reader task      │
   │ (client end) │    │                  │
   │              │    │ Reads from       │
   │  write half ─┘    │ read half of     │
   │  read half ──────>│ client end,      │
   │              │    │ sends chunks via │
   └──────────────┘    │ response_tx      │
                       └──────────────────┘
   ┌──────────────┐
   │ DuplexStream │
   │ (server end) │
   │      │       │
   │  TokioIo     │
   │      │       │
   │  hyper http1  │
   │  serve_conn  │
   │      │       │
   │  dav-server   │
   │  DavHandler  │
   │      │       │
   │  local fs    │
   └──────────────┘
```

## Data flow

**Request path** (guest → filesystem):

1. VMC data arrives → `handle_vmc_data()` feeds demuxer.
2. Demuxer yields `MuxFrame { client_id, data }`.
3. `handle_mux_frame()`:
   - New client: create DuplexStream pair, split client
     end into read/write halves, spawn server task with
     server end, spawn reader task with read half, write
     initial data to write half.
   - Existing client: write data to client's write half.
   - Disconnect (empty data): drop write half, which
     causes hyper to see EOF and finish.

**Response path** (filesystem → guest):

1. hyper writes HTTP response to server end of DuplexStream.
2. Reader task reads chunks from client end (read half).
3. Reader task sends `MuxResponse { client_id, data }` via
   `response_tx`.
4. Main loop receives from `response_rx`, calls
   `send_mux_frame(client_id, &data)`.
5. When reader task gets EOF (hyper closed the connection),
   it sends a final empty `MuxResponse` to signal close.
6. Main loop sends `send_mux_close(client_id)` and removes
   the client from the map.

## Files changed

| File | Change |
|------|--------|
| `src/channels/webdav.rs` | Major changes: add WebdavServer, DuplexStream per client, response channel, reader tasks, wire up handle_mux_frame, wire up ShareDirectory/StopSharing commands |
| `src/webdav/server.rs` | Remove `#[allow(dead_code)]` annotations |

## Detailed steps

### Step 1: Define `MuxResponse` and update `MuxClient`

Add a response message type:

```rust
struct MuxResponse {
    client_id: i64,
    data: Vec<u8>,  // empty = client done
}
```

Update `MuxClient` to hold the write half of its
DuplexStream and task handles:

```rust
struct MuxClient {
    bytes_received: u64,
    write_half: WriteHalf<DuplexStream>,
    server_handle: tokio::task::JoinHandle<()>,
    reader_handle: tokio::task::JoinHandle<()>,
}
```

### Step 2: Add response channel and server to WebdavChannel

Add fields to `WebdavChannel`:

```rust
// Response channel: per-client reader tasks send
// response data here for muxing back to the guest.
response_tx: mpsc::Sender<MuxResponse>,
response_rx: mpsc::Receiver<MuxResponse>,

// WebDAV server (None until sharing is started)
server: Option<WebdavServer>,
```

Initialize the response channel (bounded, e.g. 256) in
`new()`. Initialize the server from `auto_share_dir` if
present.

### Step 3: Add response_rx to the select! loop

Add a third arm to `tokio::select!` in `run()`:

```rust
Some(resp) = response_rx.recv() => {
    self.handle_response(resp).await?;
}
```

Implement `handle_response()`:
- If `resp.data.is_empty()`: client is done, call
  `send_mux_close(resp.client_id)`, remove from clients
  map, abort task handles.
- Otherwise: call
  `send_mux_frame(resp.client_id, &resp.data)`.

### Step 4: Rewrite `handle_mux_frame()` for live clients

**New client (client_id not in map, data not empty):**

1. Check that `self.server` is Some. If not, log a warning
   and return (sharing not active).
2. Create `tokio::io::duplex(65536)` →
   `(client_end, server_end)`.
3. Split client end:
   `let (read_half, write_half) = tokio::io::split(client_end)`.
4. Spawn server task:
   ```rust
   let server = self.server.clone().unwrap();
   tokio::spawn(async move {
       if let Err(e) = server.serve_client(server_end)
           .await
       {
           // Log but don't propagate — individual
           // client errors shouldn't kill the channel
       }
   })
   ```
5. Spawn reader task:
   ```rust
   let tx = self.response_tx.clone();
   let cid = frame.client_id;
   tokio::spawn(async move {
       let mut buf = [0u8; 65536];
       loop {
           match read_half.read(&mut buf).await {
               Ok(0) | Err(_) => {
                   tx.send(MuxResponse {
                       client_id: cid,
                       data: vec![],
                   }).await.ok();
                   break;
               }
               Ok(n) => {
                   tx.send(MuxResponse {
                       client_id: cid,
                       data: buf[..n].to_vec(),
                   }).await.ok();
               }
           }
       }
   })
   ```
6. Write initial data to write_half.
7. Store `MuxClient` with write_half and task handles.

**Existing client (client_id in map, data not empty):**

Write data to `client.write_half`.

**Client disconnect (data empty):**

Remove from map. Dropping the `MuxClient` drops the
write_half, which causes hyper to see EOF. The server task
and reader task will finish naturally. Abort task handles
as a safety net.

### Step 5: Wire up ShareDirectory / StopSharing commands

**ShareDirectory:**
1. Create `WebdavServer::new(path, read_only)`.
2. Store in `self.server`.
3. Send `WebdavSharingStarted` event.

**StopSharing:**
1. Drop all clients (abort tasks, drop write halves).
2. Set `self.server = None`.
3. Send `WebdavSharingStopped` event.

### Step 6: Handle channel shutdown

When the SPICE channel disconnects (n == 0 in the read
loop), clean up all clients:
- Abort all server and reader task handles.
- Drop all write halves.
- Clear the clients map.

### Step 7: Remove dead_code annotations

Remove `#[allow(dead_code)]` from:
- `send_data()`
- `send_mux_frame()`
- `send_mux_close()`
- `WebdavServer` struct and impl

These are now all actively used.

### Step 8: Add --share-dir auto-start

In `run()`, after sending `WebdavChannelReady`, if
`self.shared_dir` is Some, create the `WebdavServer` and
store it in `self.server`. The existing code already sends
the `WebdavSharingStarted` event.

## Testing

- `make test` passes (all existing tests unbroken).
- `cargo fmt --check` and `cargo clippy -- -D warnings`
  pass.
- Manual test with `make test-qemu-webdav`:
  1. Start QEMU: `make test-qemu-webdav`
  2. Create test directory:
     `mkdir -p /tmp/test-share`
     `echo "hello" > /tmp/test-share/test.txt`
  3. Connect:
     `cargo run -- --direct localhost:5900 \
         --share-dir /tmp/test-share --verbose`
  4. Observe logs showing:
     - WebDAV channel connected
     - Sharing started
     - Mux client connections from spice-webdavd
     - HTTP requests being processed
     - Responses flowing back
  5. In the guest, mount and verify:
     ```
     mount -t davfs http://localhost:9843 /mnt
     ls /mnt  # should show test.txt
     cat /mnt/test.txt  # should show "hello"
     ```
     (Note: the davfs2 mount point and port depend on
     how spice-webdavd is configured in the guest.)

## Back brief

Before executing, please confirm your understanding of:
1. The core pattern: per-client DuplexStream pair, split
   into write half (in MuxClient, written by main loop)
   and read half (consumed by reader task that sends
   responses back via mpsc channel).
2. The main loop has three select arms: network read,
   response_rx, and webdav_rx commands.
3. Error handling: individual client errors are logged
   but don't kill the channel. A client failure removes
   that client and sends a mux close frame.
4. Server lifecycle: created on ShareDirectory command
   (or auto-start from CLI), destroyed on StopSharing.
5. The `send_data()` / `send_mux_frame()` /
   `send_mux_close()` helpers from phase 2 are now
   actively called.
