# Phase 1: SpiceVMC port channel transport for WebDAV

## Overview

Create the SPICE-level channel handler for WebDAV (channel
type 11). This is a thin transport layer — it connects,
handles the standard SpiceVMC message types
(`SPICEVMC_DATA` 101 and `SPICEVMC_COMPRESSED_DATA` 102),
BaseChannel ACK management, and passes raw byte payloads
to a callback (stubbed in this phase). It follows the exact
same pattern as `src/channels/usbredir.rs`.

This phase also adds the `WebdavCommand` enum, new
`ChannelEvent` variants, CLI flags (`--share-dir`,
`--share-dir-ro`), and extends the Makefile with a
`test-qemu-webdav` target so we can verify the channel
type the server advertises.

## Files changed

| File | Change |
|------|--------|
| `src/channels/mod.rs` | Add `pub mod webdav`, export `WebdavChannel`, add `WebdavCommand` enum, add `ChannelEvent` variants |
| `src/channels/webdav.rs` | **New file.** Channel handler struct and `run()` loop |
| `src/config.rs` | Add `--share-dir` and `--share-dir-ro` CLI flags |
| `src/app.rs` | Register WebDAV channel, handle new events, create command channel |
| `Makefile` | Add `test-qemu-webdav` target |

## Detailed steps

### Step 1: Add `WebdavCommand` and `ChannelEvent` variants

In `src/channels/mod.rs`:

- Add `pub mod webdav;` and
  `pub use webdav::WebdavChannel;` alongside the existing
  USB module declarations.
- Add a `WebdavCommand` enum:
  ```rust
  pub enum WebdavCommand {
      /// Start sharing a local directory.
      ShareDirectory {
          path: PathBuf,
          read_only: bool,
      },
      /// Stop sharing the current directory.
      StopSharing,
  }
  ```
- Add `ChannelEvent` variants:
  ```rust
  /// A WebDAV channel connected successfully.
  WebdavChannelReady,
  /// WebDAV folder sharing is now active.
  WebdavSharingStarted {
      path: String,
      read_only: bool,
  },
  /// WebDAV folder sharing stopped.
  WebdavSharingStopped,
  /// A WebDAV error occurred.
  WebdavError(String),
  ```

### Step 2: Add CLI flags

In `src/config.rs`, add to the `Args` struct:

```rust
/// Share a local directory with the guest via
/// WebDAV (SPICE folder sharing)
#[arg(long = "share-dir")]
pub share_dir: Option<String>,

/// Make the shared directory read-only
#[arg(long = "share-dir-ro")]
pub share_dir_ro: bool,
```

Add a `ShareDirConfig` struct and `parse_share_dir()`
function following the `VirtualDiskConfig` /
`parse_virtual_disks()` pattern:

```rust
pub struct ShareDirConfig {
    pub path: PathBuf,
    pub read_only: bool,
}

pub fn parse_share_dir(args: &Args)
    -> Result<Option<ShareDirConfig>>
```

Validate that the path exists and is a directory.

### Step 3: Create `src/channels/webdav.rs`

Create a new channel handler following the usbredir pattern
exactly. The struct should be:

```rust
pub struct WebdavChannel {
    stream: SpiceStream,
    event_tx: mpsc::Sender<ChannelEvent>,
    webdav_rx: mpsc::Receiver<WebdavCommand>,
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

    // Sharing state
    shared_dir: Option<ShareDirConfig>,
}
```

Implement the following methods, mirroring usbredir:

- **`new()`**: constructor taking stream, event_tx,
  webdav_rx, auto_share_dir (Option<ShareDirConfig>),
  capture, byte_counter. If `auto_share_dir` is Some,
  store it in `shared_dir`.

- **`run()`**: async method with structure:
  1. Send `ChannelEvent::WebdavChannelReady` via event_tx.
  2. If `shared_dir` is Some, send
     `WebdavSharingStarted` event (actual WebDAV serving
     is stubbed — we just track state here).
  3. Enter `tokio::select!` loop with two branches:
     - **Network read**: read up to 65536 bytes into a
       temporary buffer. Update byte_counter, feed pcap
       capture, extend internal buffer, call
       `process_messages()`.
     - **Command receive**: receive `WebdavCommand` from
       `webdav_rx`, call `handle_command()`.
  4. On stream close (n == 0), send
     `Disconnected(ChannelType::Webdav)` and return.

- **`process_messages()`**: loop while buffer contains a
  complete message (check `MessageHeader::SIZE` then
  total size). For each complete message, drain from
  buffer, increment `message_count`, call
  `handle_message()`, check ACK window.

- **`handle_message(msg_type, payload)`**: match on:
  - `spicevmc_server::DATA` → call `handle_vmc_data()`.
  - `spicevmc_server::COMPRESSED_DATA` → call
    `handle_vmc_compressed_data()`.
  - `spicevmc_server::SET_ACK` → read generation/window,
    store, send ACK_SYNC response.
  - `spicevmc_server::PING` → read ID/timestamp, send
    PONG.
  - Unknown → log warning.

- **`handle_vmc_data(payload)`**: in this phase, just log
  the payload size at debug level. This is where phase 2
  will plug in the mux demultiplexer.

- **`handle_vmc_compressed_data(payload)`**: decompress
  LZ4 using the same logic as usbredir (check compression
  type byte, decompress with `lz4_flex`, cap at 64MB,
  call `handle_vmc_data()` on result). Extract this logic
  into a shared helper if practical (see step 3a below).

- **`send_data(data)`**: wrap in SPICEVMC_DATA message
  via `make_message()`, call `send_with_log()`.

- **`send_compressed_data(data)`**: compress with
  `lz4_flex::compress_prepend_size()`, wrap in
  `SPICEVMC_COMPRESSED_DATA` with the compression type
  byte prepended, call `send_with_log()`. Only compress
  if the compressed output is smaller than the input;
  otherwise fall back to uncompressed `send_data()`.

- **`send_with_log(msg_type, data)`**: log type and size
  at trace level, write to stream, flush, increment
  `bytes_out`.

- **`send(data)`**: feed pcap capture, write_all to
  stream, flush, increment bytes_out.

- **`handle_command(cmd)`**: match on:
  - `ShareDirectory { path, read_only }` → store config,
    send `WebdavSharingStarted` event. (Actual serving
    stubbed to phase 3/4.)
  - `StopSharing` → clear config, send
    `WebdavSharingStopped` event.

### Step 3a: Extract shared VMC helpers (optional)

If the duplication between usbredir.rs and webdav.rs for
LZ4 decompression, ACK management, and PING/PONG handling
is excessive, extract into a shared module. Candidates:

- `fn decompress_vmc_data(payload: &[u8]) -> Result<Vec<u8>>`
  in a new `src/channels/vmc_common.rs`.
- ACK state management could be a small struct with
  `check_and_send_ack()` method.
- PING/PONG handler as a standalone function.

Only do this if it meaningfully reduces duplication without
over-abstracting. If the duplicated code is under ~40 lines
per function, it may be cleaner to keep it inline. Use
judgement.

### Step 4: Register channel in `app.rs`

Add WebDAV channel support to the application:

- **Command channel**: create
  `let (webdav_tx, webdav_rx) = mpsc::channel(16);`
  alongside the USB command channel. Store `webdav_tx`
  in `RyllApp` as
  `webdav_tx: Option<mpsc::Sender<WebdavCommand>>`.

- **Channel connection**: in the channel type match (around
  line 1799), add a `ChannelType::Webdav` arm:
  ```rust
  ChannelType::Webdav => {
      if let Some(webdav_rx) = webdav_rx.take() {
          let stream = client.connect_channel(
              session_id, channel_type, channel_id,
          ).await?;
          let mut channel = WebdavChannel::new(
              stream,
              event_tx.clone(),
              webdav_rx,
              share_dir_config.clone(),
              capture.clone(),
              byte_counter.clone(),
          );
          handles.push(tokio::spawn(async move {
              channel.run().await
          }));
      }
  }
  ```

- **Parse CLI args**: call `parse_share_dir(&args)` and
  pass the result to the channel constructor.

- **Event handling**: add match arms for the new
  `ChannelEvent` variants in both GUI and headless mode:
  - `WebdavChannelReady` → set
    `self.webdav_channel_ready = true`, log.
  - `WebdavSharingStarted { path, read_only }` → store
    state, record timestamp, log.
  - `WebdavSharingStopped` → clear state, log.
  - `WebdavError(msg)` → store error, record timestamp,
    log.
  - `Disconnected(ChannelType::Webdav)` → clear all
    WebDAV state.

- **App state fields**: add to `RyllApp`:
  ```rust
  webdav_channel_ready: bool,
  webdav_shared_dir: Option<String>,
  webdav_read_only: bool,
  webdav_sharing_active: bool,
  webdav_connected_at: Option<Instant>,
  webdav_error_message: Option<String>,
  webdav_error_time: Option<Instant>,
  webdav_tx: Option<mpsc::Sender<WebdavCommand>>,
  ```

### Step 5: Add `test-qemu-webdav` Makefile target

Add a new target that extends the base QEMU configuration
with the WebDAV spiceport device:

```makefile
# Start a test QEMU instance with SPICE and WebDAV
# folder sharing enabled.
# Connect with: ryll --direct localhost:$(QEMU_SPICE_PORT)
#               --share-dir /path/to/dir
test-qemu-webdav: test-qemu-stop $(QEMU_TEST_IMAGE)
	cp $(OVMF_VARS) $(QEMU_VARS_COPY)
	qemu-system-x86_64 \
		-display none \
		-machine q35 \
		-m 256 \
		-drive if=pflash,format=raw,readonly=on,\
file=$(OVMF_CODE) \
		-drive if=pflash,format=raw,\
file=$(QEMU_VARS_COPY) \
		-drive file=$(QEMU_TEST_IMAGE),format=qcow2,\
if=virtio \
		-vga qxl \
		-spice port=$(QEMU_SPICE_PORT),\
disable-ticketing=on \
		-device virtio-serial-pci,id=virtio-serial0 \
		-chardev spiceport,\
name=org.spice-space.webdav.0,id=webdav0 \
		-device virtserialport,chardev=webdav0,\
name=org.spice-space.webdav.0 \
		-daemonize \
		-pidfile $(QEMU_PID_FILE)
	@echo "QEMU SPICE+WebDAV server on port \
$(QEMU_SPICE_PORT) (PID $$(cat $(QEMU_PID_FILE)))"
	@echo "Connect: ryll --direct \
localhost:$(QEMU_SPICE_PORT) --share-dir /tmp/test-share"
```

Note the `virtio-serial-pci` controller device — this is
needed for the `virtserialport` device. Some machine types
create one automatically but `q35` may not.

Also consider a combined target `test-qemu-all` that
includes both USB and WebDAV devices for full-feature
testing.

### Step 6: Verify channel type

After implementing the above, start QEMU with
`make test-qemu-webdav` and connect ryll. Observe the
channel list logged during the main channel's
`ChannelsAvailable` event to confirm whether the server
advertises channel type 10 (Port) or 11 (Webdav). Update
open question 4 in the master plan with the result.

If the server advertises type 10 instead of 11, adjust the
`ChannelType` match in app.rs accordingly.

## Testing

- `make test` passes (existing tests unbroken).
- `pre-commit run --all-files` passes.
- `ryll --direct localhost:5900` connects to a
  `test-qemu-webdav` instance and logs
  "WebDAV channel connected" (or similar).
- `ryll --direct localhost:5900 --share-dir /tmp/test`
  logs that sharing has started (even though the server
  connection is stubbed — the state management works).
- `ryll --help` shows the new `--share-dir` and
  `--share-dir-ro` flags.
- The channel type advertised by QEMU is confirmed and
  documented.

## Back brief

Before executing, please confirm your understanding of:
1. This phase creates the channel transport only — no mux
   protocol, no WebDAV server, no UI panel.
2. The `handle_vmc_data()` method is deliberately stubbed
   (log only) and will be connected to the mux layer in
   phase 2.
3. The `send_compressed_data()` method implements LZ4
   send-side compression as agreed. If extracting shared
   VMC code from usbredir, limit refactoring to what
   reduces clear duplication.
4. The Makefile target is needed early to verify the
   channel type assumption before building further phases.
