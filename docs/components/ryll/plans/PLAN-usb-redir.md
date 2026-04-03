# USB device redirection via the SPICE usbredir channel

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, USB,
usbredir protocol, libusb), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

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
| 1. ... | PLAN-usb-redir-phase-01-foo.md | Not started |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Ryll is a Rust SPICE VDI test client that currently
implements four channels: main (session management), display
(framebuffer rendering), cursor (pointer tracking), and
inputs (keyboard/mouse). The `ChannelType::Usbredir = 9`
enum variant already exists in `src/protocol/constants.rs`
but no channel handler or protocol logic exists for it.

USB device redirection is a core SPICE feature that allows
a user to attach a local USB device (e.g. a flash drive,
smartcard reader, or security key) and have it appear inside
the remote virtual machine. This is implemented via two
protocol layers:

1. **SPICE SpiceVMC channel** — a generic bidirectional data
   pipe. The SPICE layer uses only two message types:
   `SPICEVMC_DATA` (type 101) and `SPICEVMC_COMPRESSED_DATA`
   (type 102, LZ4). The usbredir channel (type 9) is an
   instance of this SpiceVMC abstraction.

2. **usbredir protocol** — an application-level protocol
   carried inside the SpiceVMC data stream. It defines its
   own packet header (type, length, id), a hello/capability
   handshake, device lifecycle messages (connect, disconnect,
   endpoint info, interface info), and data transfer messages
   for the four USB transfer types (control, bulk, interrupt,
   isochronous).

The reference client (spice-gtk) delegates to two C
libraries: `libusb` for host USB device access and
`usbredirhost`/`usbredirparser` for the usbredir protocol
state machine. The QEMU server side is in
`hw/usb/redirect.c`, which creates a virtual USB device and
uses `usbredirparser` to handle the protocol.

Importantly, from the server's perspective, the usbredir
channel is opaque — the server doesn't care whether the
client is forwarding a real physical USB device or emulating
one in software. This means a client can present *virtual*
USB devices backed by local resources (e.g. a RAW disk image
file presented as a USB mass storage device) and the VM will
treat them identically to physical hardware.

### USB background

USB (Universal Serial Bus) devices communicate via
**endpoints** — numbered pipes with a direction (IN = device
to host, OUT = host to device) and a transfer type:

- **Control** (endpoint 0): configuration and standard
  requests (GET_DESCRIPTOR, SET_CONFIGURATION, etc.).
- **Bulk**: large reliable transfers (storage, network).
- **Interrupt**: small periodic transfers (HID devices,
  keyboards, mice).
- **Isochronous**: time-sensitive streaming with no retries
  (audio, video).

Each device has one or more **interfaces** grouped into
**configurations**. The host selects a configuration and
claims interfaces before performing I/O. The usbredir
protocol mirrors this model: the client enumerates the
device, sends its descriptor information to the server, and
then proxies USB transactions bidirectionally.

### USB Mass Storage class

USB Mass Storage (MSC) is a device class (0x08) that
exposes block storage over USB. The most common transport
is **Bulk-Only Transport (BOT)**, which uses two bulk
endpoints (IN and OUT) with a simple command/data/status
protocol:

1. **Command Block Wrapper (CBW)** — 31 bytes, sent by the
   host on the bulk OUT endpoint. Contains a SCSI command
   (typically 6, 10, or 16 bytes) wrapped in a fixed
   header with signature `USBC`, a tag for correlation,
   transfer length, direction flag, and LUN.

2. **Data phase** — optional bulk transfer (IN or OUT)
   carrying the payload for the SCSI command (e.g. sector
   data for READ/WRITE).

3. **Command Status Wrapper (CSW)** — 13 bytes, sent by
   the device on the bulk IN endpoint. Contains signature
   `USBS`, the matching tag, residue count, and a status
   byte (0 = passed, 1 = failed, 2 = phase error).

The SCSI commands needed for a basic block device are:

| Opcode | Name | Purpose |
|--------|------|---------|
| 0x00 | TEST UNIT READY | Check device is present |
| 0x03 | REQUEST SENSE | Get error details |
| 0x12 | INQUIRY | Device identification |
| 0x1A | MODE SENSE(6) | Device parameters |
| 0x1E | PREVENT ALLOW MEDIUM REMOVAL | Lock/unlock eject |
| 0x25 | READ CAPACITY(10) | Get block count and size |
| 0x28 | READ(10) | Read sectors |
| 0x2A | WRITE(10) | Write sectors |

A RAW disk image maps directly to this model: sector N in
the SCSI command corresponds to byte offset N × 512 in the
file (or N × block_size for non-512 sector sizes). No
partition table or filesystem parsing is required — the VM's
OS handles that, just as it would with a physical USB drive.

### usbredir wire format

Every usbredir message has a header:

```
Standard header (12 bytes):
  type:   uint32_le   — message type (0-27 control, 100-104 data)
  length: uint32_le   — payload length (excluding header)
  id:     uint32_le   — correlation ID for request/response matching

Extended header (16 bytes, when cap_64bits_ids negotiated):
  type:   uint32_le
  length: uint32_le
  id:     uint64_le
```

Key messages:

| Type | Name | Payload size | Purpose |
|------|------|-------------|---------|
| 0 | hello | 68 bytes | Version string (64B) + capabilities bitmask (4B) |
| 1 | device_connect | 10 bytes | Speed, class, vendor/product ID |
| 2 | device_disconnect | 0 bytes | Device removed |
| 4 | interface_info | 128 bytes | Up to 32 interfaces: class/subclass/protocol |
| 5 | ep_info | 160 bytes | Up to 32 endpoints: type/interval/max_packet_size |
| 6-11 | configuration/alt_setting | varies | Get/set USB configuration and alternate settings |
| 100 | control_packet | 10+ bytes | USB control transfer (setup packet + data) |
| 101 | bulk_packet | 10+ bytes | USB bulk transfer |
| 102 | iso_packet | varies | USB isochronous transfer |
| 103 | interrupt_packet | varies | USB interrupt transfer |

Capabilities negotiated in the hello exchange:

| Bit | Name | Meaning |
|-----|------|---------|
| 0 | bulk_streams | USB 3.0 bulk streams support |
| 1 | connect_device_version | BCD version in device_connect |
| 2 | filter | Filter reject/filter messages |
| 3 | device_disconnect_ack | Disconnect requires ACK |
| 4 | ep_info_max_packet_size | Max packet size in ep_info |
| 5 | 64bits_ids | 64-bit correlation IDs |
| 6 | 32bits_bulk_length | 32-bit bulk packet length |
| 7 | bulk_receiving | Buffered bulk input |

## Mission and problem statement

Implement USB device redirection in ryll supporting two
device sources:

1. **Real USB devices** — physical devices attached to the
   host, accessed via a Rust USB library and forwarded
   transparently to the VM.

2. **Virtual USB devices** — software-emulated devices
   backed by local resources. The first virtual device type
   is a **USB mass storage device backed by a RAW disk
   image file**. The VM sees a standard USB drive; reads
   and writes map to file I/O on the RAW image. Only the
   RAW format is supported (no qcow2, vmdk, etc.).

This serves three purposes:

1. **Learning**: USB is a rich protocol with multiple
   transfer types, descriptor hierarchies, and real-time
   constraints. Implementing both real device passthrough
   and device emulation (mass storage class, SCSI command
   set, BOT transport) provides deep understanding of USB
   at every layer.

2. **Testing**: ryll's purpose is performance testing the
   kerbside SPICE proxy. USB redirection adds a new
   channel type that exercises kerbside's SpiceVMC
   forwarding path. Virtual devices enable deterministic,
   hardware-independent testing — create a RAW image,
   redirect it, and verify I/O without needing physical
   USB hardware.

3. **Utility**: passing a RAW disk image through as a USB
   drive is genuinely useful for injecting data into VMs
   (driver images, configuration files, diagnostic tools)
   without requiring guest agent support or network access.

The implementation should be pure Rust where practical,
using the `nusb` crate for host USB device access (pure Rust
async USB library, no libusb dependency) and implementing
both the usbredir protocol parser and the USB mass storage
emulation natively. This avoids C library dependencies and
aligns with ryll's existing pure-Rust approach to SPICE
protocol handling.

## Open questions

1. **`nusb` vs `rusb` for USB device access?** `nusb` is a
   pure-Rust async library that fits naturally with ryll's
   tokio model. `rusb` wraps libusb-1.0 (C library) but is
   more mature and widely used. **Recommendation**: start
   with `nusb` for its async API and zero C dependencies;
   fall back to `rusb` only if `nusb` proves insufficient.
   Need to verify `nusb` builds in the devcontainer.
   **Cross-platform note**: `nusb` is also the better choice
   for the packaging plan (PLAN-packaging.md), which targets
   Linux, macOS, and Windows. `nusb` is pure Rust with native
   platform support on all three; `rusb` wraps libusb-1.0 (C)
   which adds build complexity for each packaging target.

2. **Which transfer types to implement initially?** Control
   and bulk cover the vast majority of USB devices (storage,
   network adapters, serial ports, security keys). Interrupt
   is needed for HID devices (keyboards, mice — though
   redirecting these is unusual). Isochronous is needed for
   audio/video (complex, real-time constraints).
   **Recommendation**: implement control and bulk first, then
   interrupt, then isochronous as a stretch goal.

3. **Device filtering and auto-connect?** spice-gtk supports
   USB device filter rules and auto-connect policies. For a
   test client, manual device selection in the UI is
   sufficient initially. **Recommendation**: defer filtering
   and auto-connect to future work.

4. **LZ4 compression for the VMC channel?** The SpiceVMC
   channel supports optional LZ4 compression
   (SPICEVMC_COMPRESSED_DATA). The display channel already
   handles LZ4 decompression via `lz4_flex`.
   **Recommendation**: implement receive-side LZ4
   decompression in phase 1 (reuse existing `lz4_flex`
   dependency), add send-side compression as an optimisation
   later.

5. **How does QEMU's test setup handle USB?** We need a QEMU
   VM with SPICE and USB redirection enabled. The existing
   `make test-qemu` target would need USB controller
   configuration (`-device qemu-xhci` and
   `-chardev spicevmc,id=usbredir,name=usbredir` plus
   `-device usb-redir,chardev=usbredir`). Need to verify
   this works with kerbside too.

6. **64-bit IDs?** The extended header with 64-bit
   correlation IDs adds complexity. QEMU's usb-redir device
   defaults to 32-bit IDs. **Recommendation**: implement
   32-bit IDs initially, add 64-bit support as a capability
   flag later.

7. **Virtual device: read-only or read-write?** A RAW image
   could be presented as read-only (safer, no risk of
   accidental writes) or read-write (more useful for
   testing bidirectional I/O). **Recommendation**: support
   both, defaulting to read-write. Add a `--usb-disk-ro`
   flag. Implement SCSI WRITE(10) but respect the flag by
   returning a WRITE PROTECTED sense code.

8. **Virtual device: USB speed to advertise?** A virtual
   mass storage device doesn't have a physical speed. USB
   High Speed (480 Mbps) is the most common for USB 2.0
   flash drives and will work with any USB controller
   (EHCI, xHCI). **Recommendation**: advertise High Speed
   by default.

9. **Virtual device: sector size?** Standard is 512 bytes.
   Some modern drives use 4096. RAW images are
   byte-addressable so either works.
   **Recommendation**: default to 512-byte sectors, add
   an option later if needed.

10. **Virtual device: vendor/product IDs?** The virtual
    device needs to report USB vendor and product IDs in
    the device descriptor and SCSI INQUIRY response.
    **Recommendation**: use a clearly synthetic vendor ID
    (e.g. the Linux Foundation's 0x1d6b used for virtual
    USB devices) and a product ID of our choosing. Include
    "ryll" in the SCSI vendor/product strings.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. SpiceVMC channel transport | [PLAN-usb-redir-phase-01-vmc-channel.md](/components/ryll/plans/PLAN-usb-redir-phase-01-vmc-channel/) | Complete |
| 2. usbredir protocol parser | [PLAN-usb-redir-phase-02-usbredir-parser.md](/components/ryll/plans/PLAN-usb-redir-phase-02-usbredir-parser/) | Complete |
| 3. Device backend trait | [PLAN-usb-redir-phase-03-device-backend.md](/components/ryll/plans/PLAN-usb-redir-phase-03-device-backend/) | Complete |
| 4. Real device enumeration and passthrough | [PLAN-usb-redir-phase-04-real-devices.md](/components/ryll/plans/PLAN-usb-redir-phase-04-real-devices/) | Complete |
| 5. Device connection lifecycle | [PLAN-usb-redir-phase-05-device-connect.md](/components/ryll/plans/PLAN-usb-redir-phase-05-device-connect/) | Complete |
| 6. Control and bulk transfers | [PLAN-usb-redir-phase-06-transfers.md](/components/ryll/plans/PLAN-usb-redir-phase-06-transfers/) | Complete |
| 7. Virtual mass storage device (RAW images) | [PLAN-usb-redir-phase-07-virtual-msc.md](/components/ryll/plans/PLAN-usb-redir-phase-07-virtual-msc/) | Complete |
| 8. UI integration | [PLAN-usb-redir-phase-08-ui.md](/components/ryll/plans/PLAN-usb-redir-phase-08-ui/) | Complete |
| 9. Interrupt transfers | [PLAN-usb-redir-phase-09-interrupt.md](/components/ryll/plans/PLAN-usb-redir-phase-09-interrupt/) | Complete |
| 10. Testing and QEMU setup | [PLAN-usb-redir-phase-10-testing.md](/components/ryll/plans/PLAN-usb-redir-phase-10-testing/) | Complete |

### Phase 1: SpiceVMC channel transport

Implement the SPICE-level channel that carries usbredir
data. This is the thinnest possible channel — it connects,
negotiates capabilities, and passes raw byte streams
bidirectionally.

- Add `SPICEVMC_DATA` (101) and `SPICEVMC_COMPRESSED_DATA`
  (102) message type constants to `protocol/constants.rs`.
- Add SpiceVMC capability constants
  (`SPICEVMC_CAP_DATA_COMPRESS_LZ4`).
- Create `src/channels/usbredir.rs` following the standard
  channel handler pattern: struct with stream, event_tx,
  buffer, capture, byte_counter.
- Implement `new()`, `run()` (async read loop),
  `process_messages()`, `handle_message()`.
- For received `SPICEVMC_DATA`: extract the raw payload
  and pass it to the usbredir parser (stubbed in this phase).
- For received `SPICEVMC_COMPRESSED_DATA`: decompress with
  `lz4_flex` then treat as `SPICEVMC_DATA`.
- Implement `send_data()` to wrap a byte slice in a
  `SPICEVMC_DATA` message and send it.
- Register the channel in `app.rs` so it connects when
  the server advertises a usbredir channel.
- Add `ChannelEvent` variants for USB events (device list
  changes, connection status, errors).
- Add pcap capture support (reuse existing pattern).

### Phase 2: usbredir protocol parser

Implement parsing and serialisation of usbredir protocol
messages, independent of any USB device access.

- Create `src/usbredir/` module with `mod.rs`, `proto.rs`,
  `messages.rs`, `constants.rs`.
- Define the usbredir header struct (type, length, id) with
  `read()` and `write()` methods using `byteorder`.
- Define message structs for: `Hello`, `DeviceConnect`,
  `DeviceDisconnect`, `InterfaceInfo`, `EpInfo`,
  `SetConfiguration`, `GetConfiguration`,
  `ConfigurationStatus`, `SetAltSetting`, `GetAltSetting`,
  `AltSettingStatus`, `FilterReject`, `FilterFilter`,
  `DeviceDisconnectAck`.
- Define data packet structs: `ControlPacket`, `BulkPacket`,
  `InterruptPacket`, `IsoPacket`.
- Implement a `UsbredirParser` that accumulates bytes from
  the VMC channel, extracts complete usbredir messages, and
  dispatches them.
- Define a `UsbredirEvent` enum for parsed messages.
- Write unit tests for serialisation round-trips and
  parsing of each message type.
- Include capability flag constants and a `Capabilities`
  struct for hello negotiation.

### Phase 3: Device backend trait

Define a `UsbDeviceBackend` trait that abstracts over real
and virtual USB devices. Both device sources must look
identical to the usbredir channel handler — the only
difference is where the USB transactions are fulfilled.

- Create `src/usb/mod.rs` with the `UsbDeviceBackend` trait:
  ```
  trait UsbDeviceBackend {
      fn device_info(&self) -> DeviceConnect;
      fn endpoint_info(&self) -> EpInfo;
      fn interface_info(&self) -> InterfaceInfo;
      async fn control_transfer(&mut self, ...) -> Result<...>;
      async fn bulk_in(&mut self, ep, len) -> Result<Vec<u8>>;
      async fn bulk_out(&mut self, ep, data) -> Result<()>;
      async fn set_configuration(&mut self, config) -> Result<()>;
      async fn reset(&mut self) -> Result<()>;
      fn is_virtual(&self) -> bool;
  }
  ```
- Define `UsbDeviceInfo` struct covering both real and
  virtual devices: vendor/product IDs, name/description,
  speed, source (Physical or Virtual), and for virtual
  devices the backing resource path.
- Define `DeviceSource` enum:
  ```
  enum DeviceSource {
      Physical { bus: u8, address: u8 },
      VirtualDisk { path: PathBuf, read_only: bool },
  }
  ```
- Implement `enumerate_all()` that returns a combined list
  of real USB devices and configured virtual devices.
- Add `ChannelEvent::UsbDevicesChanged(Vec<UsbDeviceInfo>)`
  event to notify the UI.

### Phase 4: Real device enumeration and passthrough

Implement the `UsbDeviceBackend` for physical USB devices.

- Add `nusb` dependency to `Cargo.toml` (verify it builds
  in the devcontainer first; if not, fall back to `rusb`).
- Create `src/usb/real.rs` implementing `UsbDeviceBackend`
  for physical devices:
  - `enumerate_physical()` returning `Vec<UsbDeviceInfo>`
    with vendor ID, product ID, bus, address, manufacturer
    string, product string, speed, class/subclass/protocol.
  - `open()` to claim the device via `nusb`.
  - `device_info()`, `endpoint_info()`, `interface_info()`
    read from the real USB descriptors.
  - `control_transfer()`, `bulk_in()`, `bulk_out()` forward
    to `nusb` async I/O.
  - `set_configuration()`, `reset()` forward to `nusb`.
- Implement hot-plug detection if `nusb` supports it
  (otherwise poll periodically).

### Phase 5: Device connection lifecycle

Implement the usbredir handshake and device attachment flow,
working with any `UsbDeviceBackend` implementation.

- When the usbredir channel connects, send a
  `usb_redir_hello` with ryll's version string and
  supported capabilities.
- Parse the server's `usb_redir_hello` response and store
  negotiated capabilities.
- When the user selects a device to redirect (real or
  virtual):
  - Open the device backend (`nusb` for real devices, or
    construct the virtual device).
  - Call `endpoint_info()` on the backend and send
    `usb_redir_ep_info`.
  - Call `interface_info()` on the backend and send
    `usb_redir_interface_info`.
  - Call `device_info()` on the backend and send
    `usb_redir_device_connect`.
- Handle `usb_redir_set_configuration` from server:
  delegate to `backend.set_configuration()`.
- Handle `usb_redir_get_configuration`: respond with
  current configuration value.
- Handle `usb_redir_set_alt_setting` and
  `usb_redir_get_alt_setting` similarly.
- Handle `usb_redir_reset`: delegate to `backend.reset()`.
- Implement device disconnection: send
  `usb_redir_device_disconnect`, drop the backend, handle
  server-initiated disconnect and ACK.

### Phase 6: Control and bulk transfers

Implement the two most common USB transfer types, delegating
to the active `UsbDeviceBackend`.

- Handle `usb_redir_control_packet` from server:
  - Parse the setup packet (request_type, request, value,
    index, length).
  - Call `backend.control_transfer()`.
  - On completion, send a `usb_redir_control_packet`
    response with status and data (for IN transfers).
- Handle `usb_redir_bulk_packet` from server:
  - For OUT: call `backend.bulk_out(ep, data)`, respond
    with status.
  - For IN: call `backend.bulk_in(ep, len)`, respond with
    status and data.
- Implement `usb_redir_cancel_data_packet` to cancel
  pending async transfers.
- Track in-flight transfers by correlation ID for
  cancellation and timeout handling.
- Add bandwidth statistics for USB traffic to the existing
  stats framework.

### Phase 7: Virtual mass storage device (RAW images)

Implement a `UsbDeviceBackend` that emulates a USB mass
storage device backed by a RAW disk image file. This is
the most complex phase as it involves three protocol
layers: usbredir → USB Mass Storage BOT → SCSI.

- Add `--usb-disk <PATH>` CLI flag (and `--usb-disk-ro`
  for read-only mode) to `config.rs`.
- Create `src/usb/virtual_msc.rs` implementing
  `UsbDeviceBackend`:

  **USB descriptors:**
  - Device: class 0x00 (per-interface), vendor 0x1d6b
    (Linux Foundation), product TBD.
  - Configuration: single configuration, self-powered.
  - Interface: class 0x08 (Mass Storage), subclass 0x06
    (SCSI), protocol 0x50 (Bulk-Only Transport).
  - Endpoints: bulk IN (ep 1) and bulk OUT (ep 2), max
    packet size 512 (High Speed).
  - `device_info()` returns High Speed, class 0x00.
  - `endpoint_info()` returns two bulk endpoints.
  - `interface_info()` returns MSC/SCSI/BOT.

  **Control transfers:**
  - Handle standard USB control requests
    (GET_DESCRIPTOR, SET_CONFIGURATION, etc.) by
    returning pre-built descriptors.
  - Handle MSC class-specific requests:
    - `GET_MAX_LUN` (0xFE): return 0 (single LUN).
    - `BULK_ONLY_RESET` (0xFF): reset BOT state machine.

  **Bulk transfers (BOT protocol):**
  - Parse CBW (31 bytes) from bulk OUT data:
    - Validate signature (`USBC` = 0x43425355).
    - Extract tag, transfer length, direction, LUN,
      SCSI command.
  - Dispatch SCSI command and execute data phase.
  - Send CSW (13 bytes) on bulk IN with matching tag
    and status.

  **SCSI command handlers:**
  - `INQUIRY` (0x12): return vendor "ryll", product
    "Virtual Disk", revision string.
  - `TEST UNIT READY` (0x00): always succeed.
  - `REQUEST SENSE` (0x03): return current sense data
    (no sense after success, appropriate codes after
    errors).
  - `MODE SENSE(6)` (0x1A): return minimal mode
    parameter header. If read-only, include write-protect
    bit.
  - `PREVENT ALLOW MEDIUM REMOVAL` (0x1E): accept and
    no-op (virtual device can't be ejected).
  - `READ CAPACITY(10)` (0x25): return block count
    (file size / 512) and block size (512).
  - `READ(10)` (0x28): seek to LBA × 512 in the RAW
    file, read the requested sectors, return via bulk IN.
  - `WRITE(10)` (0x2A): if read-only, return WRITE
    PROTECTED sense. Otherwise seek and write to the
    RAW file.
  - Unknown commands: return CHECK CONDITION with
    ILLEGAL REQUEST sense key.

  **File I/O:**
  - Open the RAW file with `tokio::fs::File` (read-write
    or read-only per `--usb-disk-ro`).
  - Use `seek()` + `read_exact()` / `write_all()` for
    sector-aligned I/O.
  - No caching layer initially — let the OS page cache
    handle it.
  - Report file size / 512 as the block count. If the
    file size is not a multiple of 512, round down and
    log a warning.

  **State machine:**
  - BOT states: Idle (waiting for CBW), DataOut (receiving
    host data), DataIn (sending device data), Status
    (sending CSW).
  - Track current command tag for CSW correlation.
  - Handle protocol errors (invalid CBW, unexpected data)
    by entering error recovery (STALL + CSW with phase
    error status).

- Write unit tests for:
  - CBW/CSW parsing and serialisation.
  - Each SCSI command handler (with a small temp file as
    the backing image).
  - Read-only enforcement.
  - Edge cases: zero-length transfers, reads past end of
    image, invalid SCSI opcodes.

### Phase 8: UI integration

Add USB device management to the egui interface, showing
both real and virtual devices.

- Add a "USB Devices" panel (collapsible) to the main
  window showing:
  - Real devices: vendor/product name, IDs, bus/address.
  - Virtual devices: type (e.g. "RAW Disk"), file path,
    size, read-only status.
  - Connection state for each device.
- Add "Connect" / "Disconnect" buttons per device.
- Show transfer statistics (bytes in/out, active endpoints).
- Add an `InputEvent` variant or separate mpsc channel for
  USB control commands (connect device, disconnect device)
  from the UI to the usbredir channel.
- Show connection errors and status messages in the UI.
- CLI flags for headless mode:
  - `--usb-device VID:PID` — connect a physical device by
    vendor/product ID on startup.
  - `--usb-disk <PATH>` — present a RAW image as a USB
    mass storage device on startup.
  - `--usb-disk-ro` — make the virtual disk read-only.

### Phase 9: Interrupt transfers

Add support for interrupt transfer type.

- Handle `usb_redir_start_interrupt_receiving` from server:
  begin periodic interrupt IN transfers on the specified
  endpoint.
- Forward received interrupt data via
  `usb_redir_interrupt_packet`.
- Handle `usb_redir_stop_interrupt_receiving`.
- Handle outbound `usb_redir_interrupt_packet` for
  interrupt OUT transfers.
- Manage interrupt polling intervals per endpoint.
- Note: the virtual mass storage device does not use
  interrupt transfers, so this phase only applies to
  real device passthrough.

### Phase 10: Testing and QEMU setup

Set up end-to-end testing infrastructure. The virtual mass
storage device makes testing straightforward — no physical
USB hardware required.

- Update `make test-qemu` to include USB controller and
  redirection device:
  ```
  -device qemu-xhci,id=xhci
  -chardev spicevmc,id=usbredir1,name=usbredir
  -device usb-redir,chardev=usbredir1,id=redir1
  ```
- Create a test RAW image:
  ```
  dd if=/dev/zero of=test.raw bs=1M count=64
  mkfs.ext4 test.raw
  ```
- Write a test script that:
  - Starts QEMU with USB redirection enabled.
  - Connects ryll with `--usb-disk test.raw`.
  - Verifies the USB mass storage device appears in
    the guest (check `dmesg` or `/dev/sd*`).
  - Mounts the filesystem in the guest and performs
    basic I/O (create a file, read it back).
  - Verifies the written data persists in `test.raw`
    after disconnection.
- Test read-only mode: connect with `--usb-disk-ro`,
  verify writes are rejected by the guest.
- Test with kerbside proxy in the path to verify VMC
  channel forwarding works correctly for both real and
  virtual devices.
- Test real device passthrough with a physical USB drive
  (manual test, documented in the test plan).
- Verify pcap capture includes usbredir protocol traffic
  and is parseable.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* A physical USB device plugged into the host machine can be
  redirected to a QEMU VM via the SPICE usbredir channel,
  and basic I/O (control and bulk transfers) works.
* A RAW disk image file can be presented as a USB mass
  storage device to the VM via `--usb-disk <PATH>`, and
  the guest can mount it, read files, and write files
  (unless `--usb-disk-ro` is specified).
* The virtual mass storage device correctly implements the
  USB Mass Storage Bulk-Only Transport protocol and handles
  the core SCSI command set (INQUIRY, TEST UNIT READY,
  READ CAPACITY, READ(10), WRITE(10), REQUEST SENSE,
  MODE SENSE).
* The `UsbDeviceBackend` trait cleanly abstracts over real
  and virtual devices — the usbredir channel handler does
  not contain device-type-specific logic.
* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* New code follows existing patterns: channel handler
  structure, message parsing via `byteorder`, async tasks
  via tokio, event communication via mpsc channels.
* There are unit tests for usbredir protocol parsing and
  serialisation, SCSI command handling, and BOT state
  machine logic. Existing tests still pass (`make test`).
* Lines are wrapped at 120 characters, single quotes for
  Rust strings where applicable.
* `README.md`, `ARCHITECTURE.md`, and `AGENTS.md` have been
  updated to describe the usbredir channel, USB device
  management, virtual mass storage, and any new CLI flags.
* Documentation in `docs/` has been updated to describe
  USB redirection configuration and usage, including
  examples of creating and attaching RAW disk images.
* The usbredir channel integrates with the existing capture
  mode (pcap files for VMC traffic).

### Future work

- **Other virtual device types**: the `UsbDeviceBackend`
  trait is designed to support additional virtual device
  implementations beyond mass storage. Candidates include:
  - Virtual USB serial port (CDC ACM) for console/debug.
  - Virtual USB network adapter (CDC ECM/NCM) for
    injecting network connectivity.
  - Virtual USB HID device for automated input testing.
    This could serve as an MCP-style interface for LLM
    agents to interact with VMs — present the agent as a
    virtual USB keyboard/mouse redirected through SPICE.
    Combined with display frame capture, this gives an AI
    agent protocol-level type/click/screenshot capabilities
    without injecting events into ryll's internal channels.
    Could also serve as a GUI testing mechanism for CI.
- **Other disk image formats**: only RAW is supported
  initially. qcow2, VMDK, VHD, etc. are explicitly
  excluded. Adding them later would involve implementing
  format-specific I/O layers behind the SCSI handler.
- **Isochronous transfers**: needed for USB audio/video
  devices. Complex due to real-time scheduling and
  bandwidth reservation. Deferred because control + bulk +
  interrupt cover the vast majority of USB devices.
- **64-bit correlation IDs**: the `cap_64bits_ids`
  capability for larger ID space. Not needed for initial
  implementation as QEMU defaults to 32-bit.
- **LZ4 send-side compression**: compress outbound VMC data
  when the server advertises LZ4 capability. Receive-side
  decompression is in phase 1; send-side is an optimisation.
- **USB device filtering**: filter rules to auto-allow or
  auto-reject devices by vendor/product ID, class, etc.
  Mirrors spice-gtk's filter infrastructure.
- **Auto-connect policies**: automatically redirect devices
  matching certain criteria when they are plugged in.
- **Multiple simultaneous redirections**: the SPICE protocol
  supports multiple usbredir channels (one per device).
  Initial implementation handles one device; extend to
  support N concurrent redirections.
- **USB 3.0 bulk streams**: the `cap_bulk_streams`
  capability for USB 3.0 SuperSpeed bulk stream transfers.
- **Partition table creation**: for virtual disks, optionally
  create a GPT/MBR partition table and format the image
  before presenting it, so the guest sees a ready-to-use
  filesystem without manual setup.

### Bugs fixed during this work

(none yet)

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
