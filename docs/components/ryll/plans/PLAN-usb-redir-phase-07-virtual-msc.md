# Phase 7: Virtual mass storage device (RAW images)

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Implement a `UsbDeviceBackend` that emulates a USB mass
storage device backed by a RAW disk image file. After this
phase, `--usb-disk /path/to/image.raw` creates a virtual
USB flash drive that appears in the VM — the guest OS can
partition, format, mount, read, and write it. Data goes
directly to the RAW file via standard file I/O.

This is the most complex phase because it spans three
protocol layers: usbredir → USB Mass Storage BOT → SCSI.

## Background

### Protocol stack

```
┌──────────────── usbredir ──────────────────┐
│ control_packet / bulk_packet               │
├──────────────── USB MSC BOT ───────────────┤
│ CBW (31B) → Data phase → CSW (13B)         │
├──────────────── SCSI ──────────────────────┤
│ INQUIRY, READ(10), WRITE(10), etc.         │
├──────────────── RAW file ──────────────────┤
│ seek(LBA × 512) + read/write               │
└────────────────────────────────────────────┘
```

### USB descriptors for a mass storage device

The virtual device presents itself as a standard USB 2.0
High Speed mass storage device:

- **Device descriptor**: class 0x00 (per-interface),
  vendor 0x1d6b (Linux Foundation), product 0x0104,
  1 configuration.
- **Configuration descriptor**: 1 interface, self-powered.
- **Interface descriptor**: class 0x08 (Mass Storage),
  subclass 0x06 (SCSI transparent command set),
  protocol 0x50 (Bulk-Only Transport), 2 endpoints.
- **Endpoint descriptors**:
  - Bulk OUT: USB addr 0x02 (usbredir ep 2), max packet
    size 512.
  - Bulk IN: USB addr 0x81 (usbredir ep 17), max packet
    size 512.

### Bulk-Only Transport (BOT)

All mass storage I/O goes through two bulk endpoints using
a three-phase protocol:

**1. Command phase** — host sends a Command Block Wrapper
(CBW) on the bulk OUT endpoint:

```
Offset  Size  Field
0       4     dCBWSignature = 0x43425355 ("USBC")
4       4     dCBWTag (correlation ID)
8       4     dCBWDataTransferLength
12      1     bmCBWFlags (bit 7: 0=OUT, 1=IN)
13      1     bCBWLUN (bits 3:0)
14      1     bCBWCBLength (SCSI CDB length, 1-16)
15      16    CBWCB (SCSI command descriptor block)
```

Total: 31 bytes.

**2. Data phase** (optional) — bulk transfer of
`dCBWDataTransferLength` bytes in the direction specified
by `bmCBWFlags`:

- Data-IN: device sends data on bulk IN endpoint.
- Data-OUT: host sends data on bulk OUT endpoint.
- No data: `dCBWDataTransferLength` = 0.

**3. Status phase** — device sends a Command Status Wrapper
(CSW) on the bulk IN endpoint:

```
Offset  Size  Field
0       4     dCSWSignature = 0x53425355 ("USBS")
4       4     dCSWTag (matching the CBW tag)
8       4     dCSWDataResidue
12      1     bCSWStatus (0=passed, 1=failed, 2=phase error)
```

Total: 13 bytes.

### BOT state machine

The virtual device maintains a state machine:

```
         bulk OUT (31 bytes, valid CBW)
Idle ──────────────────────────────────→ Processing
  ↑                                          │
  │                                    ┌─────┴─────┐
  │                                    │ SCSI cmd  │
  │                                    │ dispatch  │
  │                                    └─────┬─────┘
  │                                          │
  │                               ┌──────────┼──────────┐
  │                               ↓          ↓          ↓
  │                          DataIn     DataOut     NoData
  │                          (bulk IN)  (bulk OUT)     │
  │                               │          │         │
  │                               ↓          ↓         ↓
  │                          ┌──────────────────────────┐
  │                          │    Status (send CSW)     │
  │                          │    on bulk IN endpoint   │
  │                          └────────────┬─────────────┘
  │                                       │
  └───────────────────────────────────────┘
```

However, in the usbredir model the server drives the
transfers — it sends bulk_packet requests and we respond.
So the state machine is simpler: we receive a bulk OUT
with CBW data, process the SCSI command, and then
subsequent bulk IN requests get either the data phase
response or the CSW.

### SCSI commands to implement

| Opcode | Name | Response |
|--------|------|----------|
| 0x00 | TEST UNIT READY | Success (no data) |
| 0x03 | REQUEST SENSE | 18-byte sense data |
| 0x12 | INQUIRY | 36-byte inquiry data |
| 0x1A | MODE SENSE(6) | 4+ byte mode page header |
| 0x1E | PREVENT ALLOW MEDIUM REMOVAL | Success (no-op) |
| 0x25 | READ CAPACITY(10) | 8-byte capacity data |
| 0x28 | READ(10) | Sector data from file |
| 0x2A | WRITE(10) | Write sector data to file |
| other | (unknown) | CHECK CONDITION + ILLEGAL REQUEST |

### Sense data

SCSI uses "sense data" to report error details. After
a failed command, the host sends REQUEST SENSE to get
the error. The device maintains a "current sense" that
is set by the last failing command and cleared on the
next successful command.

Fixed format sense data (18 bytes):
```
Offset  Field
0       0x70 (current errors, fixed format)
1       0x00
2       sense_key (4 bits)
3-6     0x00 (information)
7       0x0A (additional sense length = 10)
8-11    0x00
12      ASC (additional sense code)
13      ASCQ (additional sense code qualifier)
14-17   0x00
```

Key sense codes used:
- No error: key=0x00, ASC/ASCQ=0x00/0x00
- Not ready: key=0x02, ASC/ASCQ=0x04/0x00
- Illegal request: key=0x05, ASC/ASCQ=0x20/0x00
  (invalid command)
- Write protected: key=0x07, ASC/ASCQ=0x27/0x00
- Medium error: key=0x03, ASC/ASCQ=0x11/0x00
  (unrecovered read error)

## Detailed steps

### Step 1: Create `src/usb/virtual_msc.rs` scaffold

New file with the `VirtualMsc` struct:

```rust
pub struct VirtualMsc {
    file: tokio::fs::File,
    file_path: PathBuf,
    read_only: bool,
    block_count: u64,
    block_size: u32,  // always 512 for now

    // BOT state
    bot_state: BotState,
    pending_data: Vec<u8>,    // data to return on bulk IN
    pending_csw: Option<Csw>, // CSW to return after data

    // SCSI sense state
    sense_key: u8,
    sense_asc: u8,
    sense_ascq: u8,
}
```

### Step 2: Implement descriptor methods

The three descriptor query methods return fixed data:

**device_info():**
```rust
DeviceConnect {
    speed: 3,           // High Speed
    device_class: 0x00, // per-interface
    device_subclass: 0x00,
    device_protocol: 0x00,
    vendor_id: 0x1d6b,
    product_id: 0x0104,
    device_version_bcd: 0x0100,
}
```

**endpoint_info():**
All entries set to `Invalid` (255) except:
- Index 2 (bulk OUT 0x02): type=Bulk, interval=0,
  interface=0, max_packet_size=512.
- Index 17 (bulk IN 0x81): type=Bulk, interval=0,
  interface=0, max_packet_size=512.

**interface_info():**
All entries zeroed except:
- Index 0: count=1, class=0x08, subclass=0x06,
  protocol=0x50.

### Step 3: Implement control transfers

Most control transfers to a mass storage device are
standard USB requests or MSC class-specific requests.
The virtual device handles them in software:

**Standard requests** (request_type & 0x60 == 0x00):
- `GET_DESCRIPTOR` (request=0x06): return pre-built
  descriptors based on `value` field (device=0x0100,
  config=0x0200, string=0x03xx).
- `SET_CONFIGURATION` (request=0x09): accept, no-op.
- `GET_STATUS` (request=0x00): return 2 bytes of zeros
  (self-powered, no remote wakeup).
- Others: return STALL.

**Class-specific requests** (request_type & 0x60 == 0x20):
- `GET_MAX_LUN` (request=0xFE): return single byte 0x00
  (one LUN).
- `BULK_ONLY_RESET` (request=0xFF): reset BOT state
  machine, return success.

**Pre-built descriptors** stored as `const` byte arrays:
device descriptor (18B), configuration descriptor (32B,
includes interface and endpoint descriptors), string
descriptors (manufacturer, product, serial).

### Step 4: Implement BOT command processing

When the server sends a bulk OUT with CBW data:

1. Parse the 31-byte CBW:
   - Validate signature (0x43425355).
   - Extract tag, transfer length, direction, CDB.
2. Dispatch the SCSI command (CDB opcode).
3. Set up the response:
   - If the command produces data (e.g. READ), store it
     in `pending_data` and set `bot_state = DataIn`.
   - If the command consumes data (e.g. WRITE), set
     `bot_state = DataOut` and store the expected length.
   - If no data phase, prepare the CSW immediately.
4. Build the CSW with the matching tag and status.

When the server sends a bulk IN:
- If `pending_data` is non-empty, return data from it
  (draining up to `max_len` per request).
- If `pending_data` is empty and CSW is pending, return
  the CSW.
- If nothing pending, return empty (shouldn't happen in
  normal operation).

When the server sends a bulk OUT (after initial CBW):
- If `bot_state == DataOut`, accumulate the data, then
  when the expected amount is received, process it
  (e.g. write to file for WRITE(10)) and prepare CSW.

### Step 5: Implement SCSI command handlers

Each handler receives the CDB bytes and returns a
`ScsiResult`:

```rust
struct ScsiResult {
    status: u8,         // 0=good, 2=check condition
    data: Vec<u8>,      // response data (IN direction)
    sense_key: u8,
    sense_asc: u8,
    sense_ascq: u8,
}
```

**TEST UNIT READY (0x00):** Return good status, no data.

**REQUEST SENSE (0x03):** Return 18-byte fixed sense data
using the current sense state. Clear sense after returning.

**INQUIRY (0x12):** Return 36 bytes:
```
bytes 0-3:   0x00, 0x80, 0x04, 0x02
             (disk, removable, SPC-2, response format 2)
byte 4:      0x1F (additional length = 31)
bytes 5-7:   0x00, 0x00, 0x00
bytes 8-15:  "ryll    " (vendor, 8 bytes, space-padded)
bytes 16-31: "Virtual Disk    " (product, 16 bytes)
bytes 32-35: "0001" (revision, 4 bytes)
```

**MODE SENSE(6) (0x1A):** Return 4-byte header:
```
byte 0: data length (3)
byte 1: medium type (0x00)
byte 2: device-specific parameter
        (bit 7 = write-protect if read-only)
byte 3: block descriptor length (0)
```

**PREVENT ALLOW MEDIUM REMOVAL (0x1E):** Return good
status, no-op.

**READ CAPACITY(10) (0x25):** Return 8 bytes:
```
bytes 0-3: last LBA (block_count - 1), big-endian
bytes 4-7: block size (512), big-endian
```

**READ(10) (0x28):** Parse LBA (CDB bytes 2-5, big-endian)
and transfer length (CDB bytes 7-8, big-endian). Seek to
`LBA × 512` in the file, read `length × 512` bytes. If
read past end of file, return medium error sense.

**WRITE(10) (0x2A):** If read-only, set write-protect
sense and return check condition. Otherwise parse LBA and
length, prepare to receive data on bulk OUT, then write
to file at `LBA × 512`.

**Unknown opcodes:** Set sense to ILLEGAL REQUEST
(key=0x05, ASC=0x20, ASCQ=0x00), return check condition.

### Step 6: Implement file I/O

Use `tokio::fs::File` for async I/O:

```rust
impl VirtualMsc {
    pub async fn open(
        path: PathBuf,
        read_only: bool,
    ) -> Result<Self> {
        let file = if read_only {
            tokio::fs::File::open(&path).await?
        } else {
            tokio::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .open(&path)
                .await?
        };

        let metadata = file.metadata().await?;
        let file_size = metadata.len();
        let block_count = file_size / 512;

        if file_size % 512 != 0 {
            warn!(
                "usb-disk: file size {} is not a multiple \
                 of 512, {} bytes will be inaccessible",
                file_size, file_size % 512,
            );
        }

        info!(
            "usb-disk: opened {} ({} blocks, {})",
            path.display(),
            block_count,
            if read_only { "read-only" } else { "read-write" },
        );

        Ok(VirtualMsc {
            file,
            file_path: path,
            read_only,
            block_count,
            block_size: 512,
            bot_state: BotState::Idle,
            pending_data: Vec::new(),
            pending_csw: None,
            sense_key: 0,
            sense_asc: 0,
            sense_ascq: 0,
        })
    }
}
```

For READ(10):
```rust
use tokio::io::{AsyncReadExt, AsyncSeekExt};

let offset = lba as u64 * self.block_size as u64;
self.file.seek(SeekFrom::Start(offset)).await?;
let mut buf = vec![0u8; transfer_len * self.block_size as usize];
self.file.read_exact(&mut buf).await?;
```

For WRITE(10):
```rust
use tokio::io::{AsyncWriteExt, AsyncSeekExt};

let offset = lba as u64 * self.block_size as u64;
self.file.seek(SeekFrom::Start(offset)).await?;
self.file.write_all(&data).await?;
self.file.flush().await?;
```

### Step 7: Add VirtualMsc variant to DeviceBackend

In `src/usb/mod.rs`, add:

```rust
pub mod virtual_msc;

pub enum DeviceBackend {
    Real(real::RealDevice),
    Virtual(virtual_msc::VirtualMsc),
}
```

And extend every match arm in the `UsbDeviceBackend` impl
to include `DeviceBackend::Virtual(d) => d.method()`.

### Step 8: Unit tests

Tests in `src/usb/virtual_msc.rs`:

1. **CBW parsing**: valid CBW → correct tag, direction,
   CDB. Invalid signature → error.

2. **CSW building**: given tag and status, verify 13-byte
   output matches expected format.

3. **INQUIRY**: verify 36-byte response with correct
   vendor/product strings.

4. **READ CAPACITY**: create a 1MB temp file, verify
   response reports correct block count and block size.

5. **READ(10)**: write known data to temp file, issue
   READ(10), verify returned data matches.

6. **WRITE(10)**: issue WRITE(10), read file directly,
   verify data was written.

7. **WRITE(10) read-only**: open read-only, attempt
   WRITE(10), verify write-protect sense.

8. **TEST UNIT READY**: verify success status.

9. **REQUEST SENSE after error**: trigger an error (e.g.
   invalid command), then REQUEST SENSE, verify sense
   data matches. Verify second REQUEST SENSE returns
   no-sense.

10. **MODE SENSE read-only vs read-write**: verify
    write-protect bit set/unset.

11. **Unknown SCSI opcode**: send opcode 0xFF, verify
    CHECK CONDITION with ILLEGAL REQUEST sense.

12. **Read past end**: attempt READ(10) beyond the file
    size, verify medium error sense.

13. **Full BOT sequence**: send CBW for READ(10) via
    bulk_out, then bulk_in to get data, then bulk_in
    to get CSW. Verify the complete flow.

## Files changed

| File | Change |
|------|--------|
| `src/usb/virtual_msc.rs` | **New** — `VirtualMsc` struct, BOT protocol, SCSI command handlers, USB descriptors, file I/O, unit tests |
| `src/usb/mod.rs` | Add `pub mod virtual_msc;`, add `Virtual` variant to `DeviceBackend` enum, extend all dispatch match arms |

## What is NOT in scope

- CLI flags (`--usb-disk`, `--usb-disk-ro`) — phase 8.
- UI integration — phase 8.
- Other disk formats (qcow2, VMDK, etc.) — future work.
- Caching layer — rely on OS page cache.
- Multi-LUN support — always LUN 0.
- SCSI commands beyond the 8 listed (READ(16), WRITE(16),
  SYNCHRONIZE CACHE, etc.) — can add if guest OSes need
  them, but the core set works for Linux and Windows.

## Testing

### Build and lint

```
./scripts/check-rust.sh fix
pre-commit run --all-files
make test
```

### Unit tests

```
cargo test --lib usb::virtual_msc
```

### Manual integration test

Once phase 8 adds the CLI flag:

1. Create a test image:
   ```
   dd if=/dev/zero of=/tmp/test.raw bs=1M count=64
   mkfs.ext4 /tmp/test.raw
   ```
2. Connect ryll with `--usb-disk /tmp/test.raw`.
3. In the VM, verify a USB mass storage device appears.
4. Mount and read/write files.

## Back brief

Before starting this phase, confirm understanding: we are
building a complete USB mass storage device emulation in
software. The `VirtualMsc` struct implements
`UsbDeviceBackend` and presents a RAW file as a USB flash
drive. It handles three protocol layers: usbredir messages
→ USB MSC Bulk-Only Transport (CBW/CSW) → SCSI commands
→ file I/O. The implementation includes fixed USB
descriptors, a BOT state machine, 8 SCSI command handlers,
sense data tracking, and async file I/O via tokio. All
response data is queued and returned when the server
requests it via bulk IN transfers.
