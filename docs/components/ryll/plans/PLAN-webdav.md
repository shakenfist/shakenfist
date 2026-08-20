# WebDAV folder sharing via the SPICE port channel

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, WebDAV, phodav,
HTTP), research as needed to give a confident answer. Flag
any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. ... | PLAN-webdav-phase-01-foo.md | Not started |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll is a Rust SPICE VDI test client that currently
implements five channel types: main (session management),
display (framebuffer rendering), cursor (pointer tracking),
inputs (keyboard/mouse), and usbredir (USB device
redirection via SpiceVMC). The `ChannelType::Port = 10`
and `ChannelType::Webdav = 11` enum variants already exist
in `src/protocol/constants.rs` but no channel handler or
protocol logic exists for either.

SPICE folder sharing allows a client to export a local
directory into the guest VM. The guest's `spice-webdavd`
daemon connects through the SPICE channel and makes HTTP
WebDAV requests; the client runs an embedded WebDAV server
that fulfils those requests against the shared directory.
This is how remote-viewer/virt-viewer provide the "Share
Folder" feature.

### How SPICE folder sharing works

At the SPICE protocol level, the WebDAV channel is a
**port channel** (type 10) whose port name is
`"org.spice-space.webdav.0"`. The SPICE server (via QEMU)
recognises this specific port name and routes it to channel
type 11 (`SPICE_CHANNEL_WEBDAV`). The wire transport is
identical to usbredir: raw `SPICEVMC_DATA` (type 101)
messages carrying an opaque byte stream.

On top of this byte stream, the channel uses a simple
**multiplexing protocol** to support multiple concurrent
HTTP clients (the guest's `spice-webdavd` may issue
parallel requests). Each frame in the mux stream is:

```
Mux frame (10 + N bytes):
  client_id:  i64_le   — identifies which HTTP client
  data_size:  u16_le   — size of following payload (max 65535)
  data:       [u8; N]  — raw HTTP request/response bytes
```

When data arrives from the guest:
1. Read `client_id` (8 bytes, little-endian i64).
2. Read `data_size` (2 bytes, little-endian u16).
3. Read `data_size` bytes of HTTP request data.
4. Look up the client by `client_id` in a hash map. If
   not found, create a new client and connect it to the
   WebDAV server.
5. Write the data to the client's HTTP input stream.
6. Read the WebDAV server's HTTP response.
7. Mux the response back: write `[client_id | size | data]`
   to the SPICE channel.

A `data_size` of 0 signals client disconnection.

The reference implementation in spice-gtk
(`src/channel-webdav.c`) uses the phodav library (a
GLib/libsoup-based WebDAV server) to handle HTTP. In ryll,
we need a Rust WebDAV server that can serve files from a
local directory over an in-process byte stream (not a TCP
socket).

### QEMU server-side configuration

The guest needs a QEMU virtio serial port with the correct
name:

```
-chardev spiceport,name=org.spice-space.webdav.0,id=webdav0
-device virtserialport,chardev=webdav0,
        name=org.spice-space.webdav.0
```

Or equivalently in libvirt domain XML, add a channel device
inside the `<devices>` element:

```xml
<channel type='spiceport'>
  <source channel='org.spice-space.webdav.0'/>
  <target type='virtio'
          name='org.spice-space.webdav.0'/>
</channel>
```

Note: many distributions' libvirt templates already include
this channel when SPICE graphics are configured. Check
`virsh dumpxml <domain>` to see if it is already present.

The SPICE server in QEMU's `reds.cpp` checks the port name
and routes traffic to `SPICE_CHANNEL_WEBDAV` (type 11).
On the guest side, `spice-webdavd` opens the corresponding
`/dev/virtio-ports/org.spice-space.webdav.0` device and
issues HTTP WebDAV requests through it.

### Why this matters

The motivation for WebDAV support in ryll is passing a
user's home directory (or any local directory) from the
SPICE client machine through the SPICE session to the
virtual desktop guest. This is a key usability feature
for VDI — users want their local files accessible inside
the VM.

Critically, the filesystem being shared lives on the
**client** machine, which is typically *not* the machine
hosting the VM. This rules out hypervisor-level solutions
(virtio-fs, virtio-9p, NFS exports) because those require
host-side access to the files. SPICE folder sharing solves
this by tunnelling WebDAV through the SPICE protocol from
client to guest, regardless of whether there are proxies
or network hops in between.

USB redirection (usbredir) is already supported by SPICE
for client-to-guest data transfer, but it operates at the
block device level and requires the guest to mount a
filesystem. WebDAV provides file-level access with no
guest-side formatting or mounting of block devices required
beyond installing `spice-webdavd` and `davfs2`.

## Mission and problem statement

Implement WebDAV folder sharing in ryll, allowing users to
share a local directory with the guest VM via the SPICE
protocol. The implementation should:

1. **Embed a WebDAV server** — serve files from a
   configurable local directory using WebDAV (RFC 4918),
   handling the HTTP request/response cycle in-process.

2. **Implement the SPICE port/WebDAV channel** — connect
   to the server's WebDAV channel, handle the mux protocol
   (client multiplexing over the SPICEVMC byte stream), and
   bridge muxed HTTP traffic to/from the embedded WebDAV
   server.

3. **Provide a UI matching the USB panel** — a "Sharing"
   or "Folders" panel in the same right-side-panel style
   as the existing USB panel, with directory selection via
   native file picker, sharing status, and connect/
   disconnect controls.

4. **Support CLI operation** — `--share-dir <PATH>` flag
   for headless and scripted use, analogous to
   `--usb-disk`.

This serves three purposes:

1. **VDI usability**: users get transparent access to their
   local files inside the remote desktop without manual
   file transfer.

2. **Testing**: exercises the SPICE port channel (type 10)
   and WebDAV channel (type 11) paths through the kerbside
   proxy, which is ryll's primary purpose.

3. **Feature parity**: brings ryll closer to feature parity
   with spice-gtk/remote-viewer for folder sharing, a
   commonly-used SPICE capability.

The implementation should be pure Rust, using existing crate
ecosystem for HTTP parsing and WebDAV serving. This avoids
C library dependencies (no phodav/libsoup) and aligns with
ryll's existing approach.

## Open questions

1. **Which Rust WebDAV server crate?** We need a WebDAV
   server that can serve a directory and work over an
   in-process byte stream (not necessarily a TCP socket).
   Candidates include:
   - `dav-server` — a Rust WebDAV handler library that
     works with hyper. Supports RFC 4918 (PROPFIND, GET,
     PUT, MKCOL, DELETE, COPY, MOVE, LOCK). Has a
     `LocalFs` backend for serving local directories.
   - Implement a minimal WebDAV server from scratch — the
     guest's `spice-webdavd` only uses a subset of WebDAV
     operations (PROPFIND, GET, PUT, OPTIONS, etc.).
   **Recommendation**: evaluate `dav-server` first. If it
   can be driven from a byte stream (rather than requiring
   a TCP listener), it's ideal. If not, consider wrapping
   hyper with a custom I/O layer, or implementing the
   minimal subset of HTTP/WebDAV needed. The key constraint
   is that all I/O goes through the SPICE mux, not a real
   network socket.

2. **Read-only or read-write?** Sharing a directory
   read-write allows the guest to create/modify/delete
   files in the user's local directory. This is powerful
   but potentially dangerous. spice-gtk defaults to
   read-write.
   **Recommendation**: support both via a `--share-dir-ro`
   CLI flag (for headless/scripted use) and a "Read-only"
   checkbox in the UI directory picker (for interactive
   use), mirroring the USB panel's `--usb-disk-ro` flag
   and read-only checkbox pattern. Default to read-write
   to match spice-gtk behaviour. The WebDAV server should
   reject PUT/DELETE/MKCOL when read-only.

3. **Multiple shared directories?** spice-gtk shares a
   single directory. Multiple directories could be useful
   but adds complexity.
   **Recommendation**: single directory initially, matching
   spice-gtk. Multiple directories can be added later by
   mounting them as subdirectories under a virtual root.

4. **Port channel vs WebDAV channel type?** The SPICE
   protocol has both `Port = 10` and `Webdav = 11`. On the
   wire, the server presents WebDAV as channel type 11.
   Ryll already has both enum variants. We need to verify
   whether the server advertises type 10 or type 11 in the
   channel list, and whether the link handshake uses the
   same capabilities as usbredir's VMC channel.
   **Recommendation**: inspect the channel list from a QEMU
   instance with WebDAV enabled. The reference code
   (`reds.cpp` line 3190) uses `SPICE_CHANNEL_WEBDAV`
   (= 11 in ryll's enum), so the server likely advertises
   type 11. Implement as a channel type 11 handler that
   uses the SpiceVMC transport (same as usbredir). Phase 1
   includes updating the QEMU Makefile target so we can
   verify this empirically before building further.

5. **LZ4 compression?** Same as the usbredir channel —
   `SPICEVMC_COMPRESSED_DATA` (type 102) may be used.
   **Decision**: implement both receive-side decompression
   *and* send-side compression from the start. Extract
   common VMC channel logic (LZ4 compress/decompress,
   `SPICEVMC_DATA`/`SPICEVMC_COMPRESSED_DATA` handling,
   `send_data()`) into shared code reused by both the
   usbredir and WebDAV channel handlers.

6. **Kerbside proxy support?** The kerbside SPICE proxy
   will likely need changes to forward port/WebDAV
   channels. Our current test environment does not use
   kerbside, so this is out of scope for now.
   **Decision**: defer to future work. Track as a known
   gap.

7. **HTTP parsing approach?** The mux protocol carries raw
   HTTP bytes. We need to parse HTTP requests from the
   guest and generate HTTP responses. Options:
   - Use hyper as the HTTP engine, with a custom I/O
     transport that reads/writes from the mux stream.
   - Use `httparse` for low-level parsing and build
     responses manually.
   - Use a WebDAV-specific crate that handles both HTTP
     and WebDAV semantics.
   **Recommendation**: evaluate whether `dav-server` +
   hyper can be wired to a byte-stream transport. If so,
   this gives us correct HTTP and WebDAV handling for free.
   If the abstraction doesn't fit, fall back to `httparse`
   + manual WebDAV response generation for the subset of
   operations `spice-webdavd` actually uses.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. SpiceVMC port channel transport | [PLAN-webdav-phase-01-port-channel.md](/components/ryll/plans/PLAN-webdav-phase-01-port-channel/) | Complete |
| 2. Mux protocol (demux and remux) | [PLAN-webdav-phase-02-mux-protocol.md](/components/ryll/plans/PLAN-webdav-phase-02-mux-protocol/) | Complete |
| 3. Embedded WebDAV server | [PLAN-webdav-phase-03-webdav-server.md](/components/ryll/plans/PLAN-webdav-phase-03-webdav-server/) | Complete |
| 4. Integration (mux ↔ WebDAV server) | [PLAN-webdav-phase-04-integration.md](/components/ryll/plans/PLAN-webdav-phase-04-integration/) | Complete |
| 5. UI panel | [PLAN-webdav-phase-05-ui.md](/components/ryll/plans/PLAN-webdav-phase-05-ui/) | Complete |
| 6. Testing and QEMU setup | [PLAN-webdav-phase-06-testing.md](/components/ryll/plans/PLAN-webdav-phase-06-testing/) | Complete |

### Phase 1: SpiceVMC port channel transport

Implement the SPICE-level channel handler for the WebDAV
channel. This reuses the same SpiceVMC transport as usbredir
(message types 101/102) but connects as channel type 11.

- Create `src/channels/webdav.rs` following the same channel
  handler pattern as `usbredir.rs`: struct with stream,
  event_tx, buffer, capture, byte_counter.
- Implement `new()`, `run()` (async read loop with
  `tokio::select!` for network reads and commands),
  `process_messages()`, `handle_message()`.
- For received `SPICEVMC_DATA` (101): extract raw payload
  and pass to the mux demultiplexer (stubbed in this phase).
- For received `SPICEVMC_COMPRESSED_DATA` (102): decompress
  with `lz4_flex` then treat as `SPICEVMC_DATA`.
- Implement `send_data()` to wrap a byte slice in a
  `SPICEVMC_DATA` message and write to the stream.
- Register the channel in `app.rs` so it connects when
  the server advertises a WebDAV channel (type 11).
- Add `ChannelEvent` variants: `WebdavChannelReady`,
  `WebdavError(String)`, `WebdavSharingActive(bool)`.
- Add `WebdavCommand` enum (analogous to `UsbCommand`):
  `ShareDirectory { path, read_only }`, `StopSharing`.
- Add pcap capture support (reuse existing pattern from
  usbredir).
- Consider extracting common VMC channel boilerplate shared
  with usbredir into a helper or trait (read loop, LZ4
  decompression, send_data). Defer this refactoring if it
  would make the phase too large.
- Update `make test-qemu` (or add a `make test-qemu-webdav`
  target) to include the WebDAV spiceport chardev and
  virtserialport device. This lets us verify early what
  channel type (10 vs 11) the server advertises and close
  open question 4 before building further phases on an
  assumption.

### Phase 2: Mux protocol (demux and remux)

Implement the client-multiplexing protocol that sits between
the raw SpiceVMC byte stream and the per-client HTTP
connections.

- Create `src/webdav/mod.rs` and `src/webdav/mux.rs`.
- Implement `MuxDemuxer`: accumulates bytes from the VMC
  channel and extracts mux frames:
  - Read i64 LE `client_id`.
  - Read u16 LE `data_size`.
  - Read `data_size` bytes of payload.
  - Return `(client_id, data)` tuples.
- Implement `MuxMuxer`: takes a `(client_id, data)` and
  serialises it to the mux wire format for sending back
  through the VMC channel.
- Track active clients in a `HashMap<i64, ClientState>`.
- Handle client lifecycle:
  - New `client_id` → create client, connect to WebDAV
    server (stubbed in this phase).
  - `data_size == 0` → remove client from map.
  - Data for existing client → forward to that client's
    HTTP input stream.
- Write unit tests for mux frame parsing and serialisation,
  including edge cases (zero-length data, max-size frames,
  multiple clients interleaved).

### Phase 3: Embedded WebDAV server

Implement or integrate a WebDAV server that serves a local
directory and communicates via in-process byte streams
rather than TCP sockets.

- Evaluate `dav-server` crate: can it serve a `LocalFs`
  backend and be driven from a byte stream? If yes, use it.
  If not, evaluate alternatives or implement a minimal
  server.
- The server must handle the WebDAV methods used by
  `spice-webdavd`: OPTIONS, PROPFIND, GET, PUT, DELETE,
  MKCOL, COPY, MOVE, LOCK, UNLOCK.
- For the read-only mode, reject PUT, DELETE, MKCOL, COPY,
  MOVE with HTTP 403 Forbidden.
- The server interface should accept raw HTTP request bytes
  and return raw HTTP response bytes, to fit the mux
  protocol's byte-stream model.
- If using hyper, implement a custom `AsyncRead`/`AsyncWrite`
  I/O layer backed by tokio mpsc channels so that each mux
  client's byte stream connects to a hyper HTTP connection.
- Create `src/webdav/server.rs` for the server integration.
- Write unit tests: serve a temp directory, issue PROPFIND /
  GET / PUT requests as raw HTTP, verify correct responses.

### Phase 4: Integration (mux ↔ WebDAV server)

Connect the mux protocol layer to the WebDAV server and the
SPICE channel, completing the end-to-end data path.

- When a new `client_id` appears in the mux stream, spawn a
  per-client tokio task that:
  - Creates an in-process byte-stream pair (e.g. via
    `tokio::io::duplex()`).
  - Connects one end to the WebDAV server (as a hyper
    HTTP connection or direct request handler).
  - Reads HTTP request data from the mux demuxer and writes
    it to the server's input.
  - Reads HTTP response data from the server's output and
    sends it back through the mux muxer → VMC channel.
- Handle client cleanup when `data_size == 0` or when the
  WebDAV server closes the connection.
- Handle backpressure: if the VMC channel write is slow,
  buffer server responses (up to a reasonable limit).
- Wire the `WebdavCommand::ShareDirectory` command to
  configure the server's root directory.
- Wire `WebdavCommand::StopSharing` to tear down all
  clients and stop serving.
- Add `--share-dir <PATH>` and `--share-dir-ro` CLI flags
  to `config.rs`.
- End-to-end integration test: start ryll with `--share-dir`
  pointing at a temp directory, verify the channel connects
  and WebDAV requests from a mocked guest stream are
  correctly handled.

### Phase 5: UI panel

Add a "Folders" panel to the egui interface, matching the
USB panel's look and feel.

- Add a "Folders" toggle button to the status bar (alongside
  "USB", "Traffic", "Report").
- Create a right-side panel (300px, same as USB panel):
  - **Header**: "Shared Folders" heading + current status.
  - **Channel status indicator**: "Channel: Ready" (green)
    or "Channel: Not available" (grey), same pattern as USB.
  - **Active share display** (conditional): shows the shared
    directory path and how long it has been active (elapsed
    timer, same pattern as USB connected-device display).
  - **Error display** (conditional): red text with dismiss
    button and "Report this as a bug" option, same pattern
    as USB error display.
  - **Share controls**:
    - "Select Directory..." button → native directory picker
      (rfd::FileDialog::pick_folder, spawned on background
      thread same as USB file picker pattern).
    - Checkbox: "Read-only".
    - "Share" button (enabled when directory selected and
      channel ready).
    - "Stop Sharing" button (when actively sharing).
  - **Transfer statistics**: bytes in/out, active client
    count.
- State tracking in `RyllApp` (mirroring USB pattern):
  `show_webdav_panel`, `webdav_channel_ready`,
  `webdav_shared_dir`, `webdav_sharing_active`,
  `webdav_read_only`, `webdav_connected_at`,
  `webdav_error_message`, `webdav_error_time`,
  `webdav_tx`, `webdav_pick_dir_rx`.
- The panel should be mutually exclusive with the USB panel
  (only one side panel visible at a time), or stacked — match
  whatever pattern feels right for the UI. The USB panel
  already shares the right side with the traffic viewer.

### Phase 6: Testing and QEMU setup

Set up end-to-end testing infrastructure.

- Update `make test-qemu` to include WebDAV port device:
  ```
  -chardev spiceport,name=org.spice-space.webdav.0,
           id=webdav0
  -device virtserialport,chardev=webdav0,
          name=org.spice-space.webdav.0
  ```
- In the guest VM, install `spice-webdavd` and `davfs2`.
- Write a test script that:
  - Starts QEMU with WebDAV enabled.
  - Connects ryll with `--share-dir /tmp/test-share`.
  - Creates test files in the shared directory.
  - Verifies files are visible from the guest via
    `spice-webdavd` + `davfs2` mount.
  - Creates a file from the guest side and verifies it
    appears in the host directory (read-write mode).
  - Verifies read-only mode rejects writes from the guest.
- Verify pcap capture includes WebDAV channel traffic.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* A local directory can be shared with a QEMU VM via the
  SPICE WebDAV channel using `--share-dir <PATH>`, and
  the guest can mount it via `spice-webdavd` + `davfs2`
  and browse/read files.
* Write operations from the guest (creating files, writing
  data) are reflected in the local directory when sharing
  in read-write mode.
* The `--share-dir-ro` flag correctly prevents guest write
  operations (PUT, DELETE, MKCOL return HTTP 403).
* The mux protocol correctly handles multiple concurrent
  HTTP clients from the guest.
* The "Folders" UI panel matches the look and feel of the
  USB panel: directory picker, share/stop controls, status
  indicators, error display with bug report integration.
* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* New code follows existing patterns: channel handler
  structure, message parsing via `byteorder`, async tasks
  via tokio, event communication via mpsc channels.
* There are unit tests for mux frame parsing/serialisation
  and WebDAV server request handling. Existing tests still
  pass (`make test`).
* Lines are wrapped at 120 characters, single quotes for
  Rust strings where applicable.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have been
  updated to describe the WebDAV channel, folder sharing,
  and any new CLI flags.
* Documentation in `docs/` has been updated to describe
  folder sharing configuration and usage.
* The WebDAV channel integrates with the existing capture
  mode (pcap files for VMC traffic).

### Future work

- **Multiple shared directories**: share more than one
  directory, each appearing as a subdirectory in the
  guest's WebDAV mount.
- **Clipboard file sharing**: spice-gtk integrates
  clipboard file drag-and-drop with the WebDAV channel.
  Files placed on the clipboard are shared via a virtual
  `/.spice-clipboard` directory. This is a natural
  extension once the base WebDAV channel works.
- **File change notifications**: notify the guest when
  files change in the shared directory (via inotify on the
  client side). This would improve the guest's view of the
  shared directory without requiring manual refresh.
- **Bandwidth throttling**: limit WebDAV transfer speed to
  avoid saturating the SPICE connection, especially over
  low-bandwidth links.
- **Progress indication**: show file transfer progress in
  the UI for large file operations.
- **Access control**: per-subdirectory read-only/read-write
  permissions, or path-based exclusion filters.
- **Kerbside proxy support**: kerbside likely needs
  explicit support for forwarding port/WebDAV channels
  (type 10/11) as opaque byte streams. Not needed for
  the current direct-to-QEMU test environment.
- **LZ4 send-side compression for usbredir**: the shared
  VMC LZ4 code built here should also be wired into the
  usbredir channel's send path, which currently only
  decompresses.
- **Extract shared VMC channel boilerplate**: usbredir.rs
  and webdav.rs share ~105 lines of identical code
  (process_messages, SET_ACK/PING/PONG handling,
  handle_vmc_compressed_data, send helpers). Worth
  extracting if a third VMC channel is added.

### Bugs fixed during this work

(none yet)

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
