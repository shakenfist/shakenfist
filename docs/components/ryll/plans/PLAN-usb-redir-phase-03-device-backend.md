# Phase 3: Device backend trait

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Define the `UsbDeviceBackend` trait and supporting types
that abstract over real and virtual USB devices. After this
phase, the trait exists with full documentation, the
`DeviceSource` and `UsbDeviceInfo` enums are defined, and
a combined enumeration function can list both physical and
virtual devices. No concrete backend implementations yet —
those come in phases 4 (real devices) and 7 (virtual mass
storage).

This phase is purely types, traits, and enums. It compiles
and has unit tests for the info types, but nothing connects
to real hardware or files.

## Background

### Why a trait

The master plan calls for two kinds of USB device:

1. **Physical** — a real USB device on the host, accessed
   via `nusb` (phase 4).
2. **Virtual** — a software-emulated device backed by a
   local resource, e.g. a RAW disk image presented as a
   USB mass storage device (phase 7).

From the usbredir channel handler's perspective, both must
be interchangeable. The server sends `set_configuration`,
`control_packet`, `bulk_packet`, etc. and the handler
delegates to whichever backend is active. The trait is the
boundary that makes this possible.

### Trait method design

The trait methods map directly to what the channel handler
needs from phases 5 and 6:

- **Descriptor queries** (`device_info`, `endpoint_info`,
  `interface_info`): called once at device attach time to
  build the usbredir messages that describe the device to
  the server. These return the phase 2 message structs
  directly (`DeviceConnect`, `EpInfo`, `InterfaceInfo`).

- **Configuration management** (`set_configuration`,
  `get_configuration`, `set_alt_setting`, `get_alt_setting`,
  `reset`): called when the server configures the device.
  For real devices these forward to the USB stack. For
  virtual devices they update internal state.

- **Data transfers** (`control_transfer`, `bulk_in`,
  `bulk_out`): the core I/O operations. The channel handler
  dispatches usbredir data packets to these methods and
  sends the results back.

- **Interrupt transfers** (`start_interrupt_in`,
  `stop_interrupt_in`): for phase 9. Defined in the trait
  now so implementations know the full interface, but the
  default implementation returns `Stall`.

All async methods use `&mut self` — the channel handler
owns one backend at a time and calls it sequentially (no
concurrent transfers within a single channel).

### Return types

Transfer methods return `TransferResult` rather than raw
`Result<Vec<u8>>` so that USB-level status (stall, timeout,
babble) can be communicated separately from Rust errors.
A Rust `Err` means the backend itself failed (e.g. file
I/O error); a `TransferResult` with status `Stall` means
the USB transfer was rejected normally.

## Detailed steps

### Step 1: Create module structure

Create `src/usb/mod.rs` with the trait and type
definitions. Add `mod usb;` to `src/main.rs`.

The module will contain:

```
src/usb/
└── mod.rs  — UsbDeviceBackend trait, UsbDeviceInfo,
               DeviceSource, TransferResult, ControlSetup
```

Later phases add files here:

```
src/usb/
├── mod.rs
├── real.rs          (phase 4)
└── virtual_msc.rs   (phase 7)
```

### Step 2: Define TransferResult

Data transfers can succeed with data, succeed with no data,
or fail with a USB status code. This needs its own type:

```rust
use crate::usbredir::constants::Status;

/// Result of a USB data transfer.
#[derive(Debug, Clone)]
pub struct TransferResult {
    pub status: Status,
    pub data: Vec<u8>,
}

impl TransferResult {
    pub fn success(data: Vec<u8>) -> Self {
        TransferResult {
            status: Status::Success,
            data,
        }
    }

    pub fn success_empty() -> Self {
        TransferResult {
            status: Status::Success,
            data: Vec::new(),
        }
    }

    pub fn stall() -> Self {
        TransferResult {
            status: Status::Stall,
            data: Vec::new(),
        }
    }

    pub fn error(status: Status) -> Self {
        TransferResult {
            status,
            data: Vec::new(),
        }
    }
}
```

### Step 3: Define ControlSetup

The control transfer method needs the USB setup packet
fields. Rather than passing 6 separate arguments, bundle
them into a struct that mirrors the ControlPacketHeader
from phase 2 (minus `status`, which is a response field):

```rust
/// USB control transfer setup packet fields.
#[derive(Debug, Clone)]
pub struct ControlSetup {
    pub endpoint: u8,
    pub request_type: u8,
    pub request: u8,
    pub value: u16,
    pub index: u16,
    pub length: u16,
}
```

This maps 1:1 to the incoming `ControlPacketHeader` fields.
The channel handler constructs it from the parsed usbredir
message.

### Step 4: Define the UsbDeviceBackend trait

```rust
use anyhow::Result;
use crate::usbredir::messages::{
    DeviceConnect, EpInfo, InterfaceInfo,
};

pub trait UsbDeviceBackend: Send {
    // ── Descriptor queries ──────────────────────

    /// USB device descriptor info for usb_redir_device_connect.
    fn device_info(&self) -> DeviceConnect;

    /// Endpoint descriptor info for usb_redir_ep_info.
    fn endpoint_info(&self) -> EpInfo;

    /// Interface descriptor info for usb_redir_interface_info.
    fn interface_info(&self) -> InterfaceInfo;

    // ── Configuration management ────────────────

    /// Set USB configuration. Returns status.
    async fn set_configuration(
        &mut self,
        configuration: u8,
    ) -> Result<Status>;

    /// Get current USB configuration number.
    async fn get_configuration(
        &mut self,
    ) -> Result<u8>;

    /// Set alternate setting for an interface.
    async fn set_alt_setting(
        &mut self,
        interface: u8,
        alt_setting: u8,
    ) -> Result<Status>;

    /// Get current alternate setting for an interface.
    async fn get_alt_setting(
        &mut self,
        interface: u8,
    ) -> Result<u8>;

    /// Reset the USB device.
    async fn reset(&mut self) -> Result<()>;

    // ── Data transfers ──────────────────────────

    /// Execute a USB control transfer.
    ///
    /// For IN transfers (device→host), the returned
    /// TransferResult contains the response data.
    /// For OUT transfers (host→device), `data` carries
    /// the payload and the result data is empty.
    async fn control_transfer(
        &mut self,
        setup: &ControlSetup,
        data: &[u8],
    ) -> Result<TransferResult>;

    /// Bulk IN transfer: read up to `max_len` bytes from
    /// the given endpoint.
    async fn bulk_in(
        &mut self,
        endpoint: u8,
        max_len: usize,
    ) -> Result<TransferResult>;

    /// Bulk OUT transfer: write `data` to the given
    /// endpoint.
    async fn bulk_out(
        &mut self,
        endpoint: u8,
        data: &[u8],
    ) -> Result<TransferResult>;

    // ── Interrupt transfers ─────────────────────
    // Defined now for interface completeness; default
    // implementations return Stall. Phase 9 will
    // implement these for real devices.

    /// Start receiving interrupt IN transfers on the
    /// given endpoint. The backend should begin polling
    /// and return data via interrupt_in().
    async fn start_interrupt_in(
        &mut self,
        _endpoint: u8,
    ) -> Result<Status> {
        Ok(Status::Stall)
    }

    /// Stop receiving interrupt IN transfers.
    async fn stop_interrupt_in(
        &mut self,
        _endpoint: u8,
    ) -> Result<Status> {
        Ok(Status::Stall)
    }

    /// Read pending interrupt IN data. Returns empty
    /// data if nothing is available.
    async fn interrupt_in(
        &mut self,
        _endpoint: u8,
        _max_len: usize,
    ) -> Result<TransferResult> {
        Ok(TransferResult::stall())
    }

    /// Write interrupt OUT data.
    async fn interrupt_out(
        &mut self,
        _endpoint: u8,
        _data: &[u8],
    ) -> Result<TransferResult> {
        Ok(TransferResult::stall())
    }

    // ── Metadata ────────────────────────────────

    /// Whether this is a virtual (emulated) device.
    fn is_virtual(&self) -> bool;

    /// Human-readable description for logging and UI.
    fn description(&self) -> String;
}
```

**Design notes:**

- `Send` bound: the backend is owned by a tokio task,
  which requires `Send`.
- No `Sync` bound: single-owner, no shared references.
- Async methods use the native Rust async-in-trait
  (stable since 1.75). No `async-trait` crate needed.
- Interrupt methods have default implementations so that
  phase 4 (real devices) and phase 7 (virtual MSC) don't
  have to stub them.
- `description()` provides a UI label ("SanDisk Ultra USB
  3.0" for real devices, "RAW Disk: /path/to/image.raw"
  for virtual).

### Step 5: Define DeviceSource and UsbDeviceInfo

These types describe available devices before they're
opened. Used for enumeration and UI display.

```rust
use std::path::PathBuf;

/// Where a USB device comes from.
#[derive(Debug, Clone)]
pub enum DeviceSource {
    /// A physical USB device on the host.
    Physical {
        bus: u8,
        address: u8,
    },
    /// A virtual mass storage device backed by a RAW
    /// disk image file.
    VirtualDisk {
        path: PathBuf,
        read_only: bool,
    },
}

/// Information about an available USB device (real or
/// virtual) for display in the UI and device selection.
#[derive(Debug, Clone)]
pub struct UsbDeviceInfo {
    pub source: DeviceSource,
    pub vendor_id: u16,
    pub product_id: u16,
    pub name: String,
    pub speed: u8,
    pub device_class: u8,
    pub device_subclass: u8,
    pub device_protocol: u8,
}

impl UsbDeviceInfo {
    /// Short label for UI display.
    pub fn label(&self) -> String {
        match &self.source {
            DeviceSource::Physical { bus, address } => {
                format!(
                    "{} [{:04x}:{:04x}] (bus {} addr {})",
                    self.name,
                    self.vendor_id,
                    self.product_id,
                    bus,
                    address,
                )
            }
            DeviceSource::VirtualDisk { path, read_only }
            => {
                let ro = if *read_only { " [RO]" } else { "" };
                format!(
                    "RAW Disk: {}{}",
                    path.display(),
                    ro,
                )
            }
        }
    }
}
```

### Step 6: Define enumeration function

A function that combines real and virtual device lists:

```rust
/// Enumerate all available USB devices (physical +
/// configured virtual devices).
///
/// `virtual_disks` comes from CLI flags (--usb-disk).
/// Physical device enumeration is stubbed until phase 4.
pub fn enumerate_devices(
    virtual_disks: &[(PathBuf, bool)],
) -> Vec<UsbDeviceInfo> {
    let mut devices = Vec::new();

    // Physical devices (phase 4 will fill this in)
    // devices.extend(enumerate_physical());

    // Virtual disk devices
    for (path, read_only) in virtual_disks {
        devices.push(UsbDeviceInfo {
            source: DeviceSource::VirtualDisk {
                path: path.clone(),
                read_only: *read_only,
            },
            vendor_id: 0x1d6b,
            product_id: 0x0104,
            name: format!(
                "Virtual Disk ({})",
                path.file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| path.display().to_string()),
            ),
            speed: 3, // High Speed
            device_class: 0x00,
            device_subclass: 0x00,
            device_protocol: 0x00,
        });
    }

    devices
}
```

Phase 4 will add `enumerate_physical()` using `nusb`.

### Step 7: Add ChannelEvent for device list

In `src/channels/mod.rs`, add:

```rust
use crate::usb::UsbDeviceInfo;

/// Available USB devices changed (enumeration result).
UsbDevicesChanged(Vec<UsbDeviceInfo>),
```

This event will be sent by the usbredir channel when
devices are enumerated. For phase 3 this is just the type
definition — nothing sends it yet.

### Step 8: Unit tests

Tests in `src/usb/mod.rs` using `#[cfg(test)]`:

1. **TransferResult constructors**: verify `success()`,
   `success_empty()`, `stall()`, `error()` set the correct
   status and data.

2. **UsbDeviceInfo::label()** for physical device: verify
   format includes name, VID:PID, bus, address.

3. **UsbDeviceInfo::label()** for virtual disk: verify
   format includes path and read-only marker.

4. **enumerate_devices() with no virtual disks**: returns
   empty list (no physical enumeration yet).

5. **enumerate_devices() with virtual disks**: returns
   one `UsbDeviceInfo` per entry, correct vendor/product
   IDs, name includes filename.

6. **DeviceSource clone and debug**: verify the enum
   derives work correctly.

## Files changed

| File | Change |
|------|--------|
| `src/usb/mod.rs` | **New** — `UsbDeviceBackend` trait, `TransferResult`, `ControlSetup`, `DeviceSource`, `UsbDeviceInfo`, `enumerate_devices()`, unit tests |
| `src/main.rs` | Add `mod usb;` |
| `src/channels/mod.rs` | Add `UsbDevicesChanged` event variant (type only, not sent yet) |

## What is NOT in scope

- Concrete backend implementations (phases 4 and 7).
- Wiring the backend into the channel handler (phase 5).
- CLI flags for `--usb-disk` (phase 7 adds the flag,
  phase 8 wires up the UI).
- Physical device enumeration via `nusb` (phase 4).
- Sending `UsbDevicesChanged` events (phases 4/7).
- Interrupt transfer implementations (phase 9) — the trait
  defines the methods with default stubs.

## Dependencies on phase 2

This phase imports types from the `src/usbredir/` module:

- `usbredir::constants::Status` — for `TransferResult`
  and configuration method return types.
- `usbredir::messages::DeviceConnect` — returned by
  `device_info()`.
- `usbredir::messages::EpInfo` — returned by
  `endpoint_info()`.
- `usbredir::messages::InterfaceInfo` — returned by
  `interface_info()`.

Phase 2 must be implemented before this phase. The trait
methods return phase 2 structs directly so that the
channel handler can serialise them into usbredir messages
without intermediate conversion.

## Testing

### Build and lint

```
./scripts/check-rust.sh fix
pre-commit run --all-files
```

### Unit tests

```
cargo test --lib usb
```

### Verification

The trait compiles and is documented. No integration test
is possible until a concrete implementation exists (phase 4
or 7), but the types are exercised by the unit tests for
`TransferResult`, `UsbDeviceInfo`, and
`enumerate_devices()`.

## Back brief

Before starting this phase, confirm understanding: we are
defining the abstraction layer only. The `UsbDeviceBackend`
trait specifies the contract between the usbredir channel
handler and any USB device (real or virtual). No concrete
implementations exist yet. The trait methods return
phase 2 usbredir message types directly. Supporting types
(`TransferResult`, `ControlSetup`, `DeviceSource`,
`UsbDeviceInfo`) and an enumeration stub are included.
The `UsbDevicesChanged` channel event type is added but
not emitted.
