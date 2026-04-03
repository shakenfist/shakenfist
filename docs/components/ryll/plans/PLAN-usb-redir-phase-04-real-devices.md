# Phase 4: Real device enumeration and passthrough

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Implement the `UsbDeviceBackend` for physical USB devices
using the `nusb` crate. After this phase, ryll can
enumerate USB devices attached to the host and forward USB
transactions for a selected device to the remote VM. The
device appears in the VM exactly as it would if plugged in
directly.

## Background

### nusb crate

`nusb` is a pure-Rust USB library with no C dependencies
(no libusb, no libudev). It talks to the Linux kernel's
usbdevfs directly. Key API surface:

**Enumeration:**
- `nusb::list_devices()` returns an iterator of `DeviceInfo`
- `DeviceInfo` provides: `vendor_id()`, `product_id()`,
  `class()`, `subclass()`, `protocol()`, `speed()`,
  `device_address()`, `bus_id()`, `manufacturer_string()`,
  `product_string()`, `interfaces()`
- `nusb::watch_devices()` returns a `HotplugWatch` stream
  of `HotplugEvent::Connected` / `Disconnected`

**Device access:**
- `DeviceInfo::open()` → `Device`
- `Device::claim_interface(n)` → `Interface`
- `Device::detach_and_claim_interface(n)` → `Interface`
  (detaches kernel driver first)
- `Device::set_configuration(n)`, `active_configuration()`
- `Device::reset()`

**Control transfers (on Device or Interface):**
- `control_in(ControlIn { control_type, recipient,
  request, value, index, length }, timeout)` → `Vec<u8>`
- `control_out(ControlOut { control_type, recipient,
  request, value, index, data }, timeout)` → `()`

**Bulk transfers (on Endpoint):**
- `Interface::endpoint::<Bulk, In>(addr)` → `Endpoint`
- `Endpoint::transfer_blocking(buf, timeout)` →
  `Completion { buffer, actual_len, status }`
- `Endpoint::next_complete()` → async completion
- `Endpoint::cancel_all()`, `clear_halt()`

**MaybeFuture pattern:**
Most nusb operations return `impl MaybeFuture<Output = T>`,
which supports both `.wait()` (blocking) and `.await`
(async). For our tokio context, we need to wrap blocking
calls in `tokio::task::spawn_blocking` to avoid blocking
the async runtime, or use the `.await` form if nusb
supports it natively.

### Endpoint numbering

USB endpoints are numbered 0-15, with direction encoded
in the high bit: 0x00-0x0F for OUT, 0x80-0x8F for IN.
The usbredir protocol uses a flat numbering 0-31 where
endpoints 0-15 are OUT and 16-31 are IN. The mapping is:

```
usbredir ep 0  → USB ep 0x00 (control, OUT direction)
usbredir ep 1  → USB ep 0x01 (OUT)
usbredir ep 2  → USB ep 0x02 (OUT)
usbredir ep 16 → USB ep 0x80 (control, IN direction)
usbredir ep 17 → USB ep 0x81 (IN)
usbredir ep 18 → USB ep 0x82 (IN)
```

Helper functions to convert between the two schemes are
needed.

### Kernel driver detachment

When a USB device is plugged in, the Linux kernel often
claims it (e.g. `usb-storage` for mass storage devices).
We must detach the kernel driver before claiming the
interface for passthrough. `nusb` provides
`detach_and_claim_interface()` for this.

### Trait compatibility

The `UsbDeviceBackend` trait from phase 3 uses
`impl Future` return types, which makes it non-object-safe.
For phase 4 this is fine — the channel handler can use
`RealDevice` directly or via an enum dispatch. If
object-safety becomes needed later, we can switch to
`async-trait` or boxing.

## Detailed steps

### Step 1: Add nusb dependency

Add to `Cargo.toml`:

```toml
# USB device access (pure Rust, no libusb)
nusb = "0.2"
```

Verify it builds in the devcontainer:

```
make lint
```

If nusb doesn't build (e.g. missing kernel headers), fall
back to `rusb`. Based on research, nusb is pure Rust and
should build fine.

### Step 2: Add endpoint mapping helpers

In `src/usb/mod.rs` (or a new `src/usb/endpoint.rs`), add
conversion functions between usbredir and USB endpoint
numbering:

```rust
/// Convert a usbredir endpoint number (0-31) to a USB
/// endpoint address (0x00-0x8F).
///
/// usbredir: 0-15 = OUT, 16-31 = IN
/// USB: 0x00-0x0F = OUT, 0x80-0x8F = IN
pub fn usbredir_ep_to_usb(ep: u8) -> u8 {
    if ep >= 16 {
        0x80 | (ep - 16)
    } else {
        ep
    }
}

/// Convert a USB endpoint address to usbredir numbering.
pub fn usb_ep_to_usbredir(addr: u8) -> u8 {
    if addr & 0x80 != 0 {
        16 + (addr & 0x0F)
    } else {
        addr & 0x0F
    }
}

/// Check if a usbredir endpoint is an IN endpoint.
pub fn is_ep_in(ep: u8) -> bool {
    ep >= 16
}
```

Add unit tests for these conversions.

### Step 3: Create `src/usb/real.rs`

This file implements `UsbDeviceBackend` for physical USB
devices.

**Struct definition:**

```rust
use nusb::{Device, Interface};

pub struct RealDevice {
    device: Device,
    interfaces: Vec<Interface>,
    device_info: DeviceConnect,
    ep_info: EpInfo,
    iface_info: InterfaceInfo,
    configuration: u8,
}
```

The struct holds the opened `nusb::Device`, claimed
`Interface`s, and cached descriptor info (read once at
open time and returned by the trait methods).

**Constructor:**

```rust
impl RealDevice {
    pub async fn open(
        nusb_info: &nusb::DeviceInfo,
    ) -> Result<Self> {
        let device = nusb_info.open().await?;

        // Read the active configuration
        let config = device.active_configuration()
            .unwrap_or(1);

        // Build DeviceConnect from nusb info
        let device_info = DeviceConnect {
            speed: speed_to_usbredir(nusb_info.speed()),
            device_class: nusb_info.class(),
            device_subclass: nusb_info.subclass(),
            device_protocol: nusb_info.protocol(),
            vendor_id: nusb_info.vendor_id(),
            product_id: nusb_info.product_id(),
            device_version_bcd: nusb_info.device_version(),
        };

        // Build EpInfo and InterfaceInfo from descriptors
        let (ep_info, iface_info, interfaces) =
            claim_all_interfaces(&device, nusb_info)?;

        Ok(RealDevice {
            device,
            interfaces,
            device_info,
            ep_info,
            iface_info,
            configuration: config,
        })
    }
}
```

**claim_all_interfaces():**

Iterate over the device's interfaces, detach kernel
drivers, and claim each one. While doing so, collect
endpoint and interface descriptor data to populate the
usbredir `EpInfo` and `InterfaceInfo` structs:

```rust
fn claim_all_interfaces(
    device: &Device,
    info: &nusb::DeviceInfo,
) -> Result<(EpInfo, InterfaceInfo, Vec<Interface>)> {
    let mut ep_info = EpInfo { /* all zeros */ };
    let mut iface_info = InterfaceInfo { /* all zeros */ };
    let mut interfaces = Vec::new();

    for iface_summary in info.interfaces() {
        let iface_num = iface_summary.interface_number();
        let iface = device
            .detach_and_claim_interface(iface_num)
            .await?;

        // Read interface descriptor
        let desc = iface.descriptor();
        iface_info.interface_count[iface_num as usize] = 1;
        iface_info.interface_class[iface_num as usize] =
            desc.class();
        iface_info.interface_subclass[iface_num as usize] =
            desc.subclass();
        iface_info.interface_protocol[iface_num as usize] =
            desc.protocol();

        // Read endpoint descriptors
        for ep_desc in desc.endpoints() {
            let usb_addr = ep_desc.address();
            let redir_ep = usb_ep_to_usbredir(usb_addr);
            let idx = redir_ep as usize;

            ep_info.ep_type[idx] =
                transfer_type_to_u8(ep_desc.transfer_type());
            ep_info.ep_interval[idx] = ep_desc.interval();
            ep_info.ep_interface[idx] = iface_num;
            ep_info.ep_max_packet_size[idx] =
                ep_desc.max_packet_size();
        }

        interfaces.push(iface);
    }

    Ok((ep_info, iface_info, interfaces))
}
```

**Speed conversion:**

```rust
fn speed_to_usbredir(speed: Option<nusb::Speed>) -> u8 {
    match speed {
        Some(nusb::Speed::Low) => 1,
        Some(nusb::Speed::Full) => 2,
        Some(nusb::Speed::High) => 3,
        Some(nusb::Speed::Super) => 4,
        Some(nusb::Speed::SuperPlus) => 4,
        _ => 0, // Unknown
    }
}
```

### Step 4: Implement UsbDeviceBackend for RealDevice

**Descriptor queries** (trivial — return cached data):

```rust
fn device_info(&self) -> DeviceConnect {
    self.device_info.clone()
}

fn endpoint_info(&self) -> EpInfo {
    self.ep_info.clone()
}

fn interface_info(&self) -> InterfaceInfo {
    self.iface_info.clone()
}
```

**Configuration management:**

```rust
async fn set_configuration(
    &mut self,
    configuration: u8,
) -> Result<Status> {
    self.device.set_configuration(configuration).await?;
    self.configuration = configuration;
    Ok(Status::Success)
}

async fn get_configuration(&mut self) -> Result<u8> {
    Ok(self.configuration)
}

async fn set_alt_setting(
    &mut self,
    interface: u8,
    alt_setting: u8,
) -> Result<Status> {
    if let Some(iface) = self.interfaces
        .iter()
        .find(|i| i.interface_number() == interface)
    {
        iface.set_alt_setting(alt_setting).await?;
        Ok(Status::Success)
    } else {
        Ok(Status::Inval)
    }
}

async fn get_alt_setting(
    &mut self,
    interface: u8,
) -> Result<u8> {
    if let Some(iface) = self.interfaces
        .iter()
        .find(|i| i.interface_number() == interface)
    {
        Ok(iface.get_alt_setting()?)
    } else {
        anyhow::bail!("interface {} not claimed", interface)
    }
}

async fn reset(&mut self) -> Result<()> {
    self.device.reset().await?;
    Ok(())
}
```

**Control transfers:**

Map our `ControlSetup` to nusb's `ControlIn`/`ControlOut`
structs. The `request_type` byte encodes direction (bit 7),
type (bits 6-5), and recipient (bits 4-0):

```rust
async fn control_transfer(
    &mut self,
    setup: &ControlSetup,
    data: &[u8],
) -> Result<TransferResult> {
    let control_type = match (setup.request_type >> 5) & 3 {
        0 => nusb::transfer::ControlType::Standard,
        1 => nusb::transfer::ControlType::Class,
        2 => nusb::transfer::ControlType::Vendor,
        _ => nusb::transfer::ControlType::Vendor,
    };
    let recipient = match setup.request_type & 0x1f {
        0 => nusb::transfer::Recipient::Device,
        1 => nusb::transfer::Recipient::Interface,
        2 => nusb::transfer::Recipient::Endpoint,
        _ => nusb::transfer::Recipient::Other,
    };
    let is_in = setup.request_type & 0x80 != 0;
    let timeout = Duration::from_secs(5);

    if is_in {
        match self.device.control_in(
            nusb::transfer::ControlIn {
                control_type,
                recipient,
                request: setup.request,
                value: setup.value,
                index: setup.index,
                length: setup.length,
            },
            timeout,
        ).await {
            Ok(data) => Ok(TransferResult::success(data)),
            Err(e) => Ok(transfer_error_to_result(e)),
        }
    } else {
        match self.device.control_out(
            nusb::transfer::ControlOut {
                control_type,
                recipient,
                request: setup.request,
                value: setup.value,
                index: setup.index,
                data,
            },
            timeout,
        ).await {
            Ok(()) => Ok(TransferResult::success_empty()),
            Err(e) => Ok(transfer_error_to_result(e)),
        }
    }
}
```

**transfer_error_to_result():**

Map nusb's `TransferError` to our `TransferResult`:

```rust
fn transfer_error_to_result(
    e: nusb::transfer::TransferError,
) -> TransferResult {
    match e {
        TransferError::Stall =>
            TransferResult::stall(),
        TransferError::Cancelled =>
            TransferResult::error(Status::Cancelled),
        TransferError::Disconnected =>
            TransferResult::error(Status::Ioerror),
        TransferError::Fault =>
            TransferResult::error(Status::Ioerror),
        _ =>
            TransferResult::error(Status::Ioerror),
    }
}
```

**Bulk transfers:**

Bulk transfers go through the endpoint. We need to find
the right `Interface` for the endpoint and use its
endpoint API:

```rust
async fn bulk_in(
    &mut self,
    endpoint: u8,
    max_len: usize,
) -> Result<TransferResult> {
    let usb_addr = usbredir_ep_to_usb(endpoint);
    let iface = self.find_interface_for_endpoint(endpoint)?;

    let mut ep = iface.endpoint::<Bulk, In>(usb_addr)?;
    let buf = nusb::transfer::Buffer::new(max_len);
    let completion = ep.transfer_blocking(
        buf,
        Duration::from_secs(30),
    );

    match completion.status {
        Ok(()) => {
            let data = completion.buffer
                .into_vec()[..completion.actual_len]
                .to_vec();
            Ok(TransferResult::success(data))
        }
        Err(e) => Ok(transfer_error_to_result(e)),
    }
}

async fn bulk_out(
    &mut self,
    endpoint: u8,
    data: &[u8],
) -> Result<TransferResult> {
    let usb_addr = usbredir_ep_to_usb(endpoint);
    let iface = self.find_interface_for_endpoint(endpoint)?;

    let mut ep = iface.endpoint::<Bulk, Out>(usb_addr)?;
    let mut buf = nusb::transfer::Buffer::new(data.len());
    buf.extend_from_slice(data);
    let completion = ep.transfer_blocking(
        buf,
        Duration::from_secs(30),
    );

    match completion.status {
        Ok(()) => Ok(TransferResult::success_empty()),
        Err(e) => Ok(transfer_error_to_result(e)),
    }
}
```

**Metadata:**

```rust
fn is_virtual(&self) -> bool { false }

fn description(&self) -> String {
    format!(
        "{:04x}:{:04x}",
        self.device_info.vendor_id,
        self.device_info.product_id,
    )
}
```

### Step 5: Add enumerate_physical()

In `src/usb/real.rs`:

```rust
pub fn enumerate_physical() -> Vec<UsbDeviceInfo> {
    let Ok(devices) = nusb::list_devices().wait() else {
        warn!("usb: failed to enumerate devices");
        return Vec::new();
    };

    devices.map(|info| {
        UsbDeviceInfo {
            source: DeviceSource::Physical {
                bus: info.device_address(),
                address: info.device_address(),
            },
            vendor_id: info.vendor_id(),
            product_id: info.product_id(),
            name: info.product_string()
                .unwrap_or("Unknown USB Device")
                .to_string(),
            speed: speed_to_usbredir(info.speed()),
            device_class: info.class(),
            device_subclass: info.subclass(),
            device_protocol: info.protocol(),
        }
    }).collect()
}
```

Update `enumerate_devices()` in `src/usb/mod.rs` to call
`enumerate_physical()`:

```rust
pub fn enumerate_devices(
    virtual_disks: &[(PathBuf, bool)],
) -> Vec<UsbDeviceInfo> {
    let mut devices = real::enumerate_physical();
    // ... virtual disks as before
    devices
}
```

### Step 6: Export and register the module

In `src/usb/mod.rs`:

```rust
pub mod real;
```

### Step 7: Devcontainer considerations

The nusb crate accesses USB devices via `/dev/bus/usb/`.
In a Docker devcontainer, this requires:

- The container must be run with `--privileged` or with
  `--device` flags for USB access.
- The devcontainer is primarily for building and testing
  protocol code — actual USB device passthrough testing
  will need to run on the host or in a container with
  USB access.

For CI builds, the code compiles fine without USB access.
Enumeration returns an empty list if `/dev/bus/usb/`
is inaccessible. Transfer operations will fail at open
time with a permission error.

No Dockerfile changes are needed for building.

## Files changed

| File | Change |
|------|--------|
| `Cargo.toml` | Add `nusb = "0.2"` dependency |
| `src/usb/mod.rs` | Add `pub mod real;`, endpoint helpers, update `enumerate_devices()` |
| `src/usb/real.rs` | **New** — `RealDevice` struct, `UsbDeviceBackend` impl, `enumerate_physical()` |

## What is NOT in scope

- Hot-plug monitoring via `watch_devices()` — defer to
  future work. Polling on UI refresh is sufficient.
- Interrupt transfer passthrough (phase 9).
- Wiring the backend into the channel handler (phase 5).
- UI for device selection (phase 8).
- Comprehensive error recovery (stall clearing, device
  re-enumeration after reset).

## Open issues to resolve during implementation

1. **nusb MaybeFuture + tokio**: nusb's `.await` support
   may need a specific runtime feature flag. If `.await`
   doesn't work directly, wrap blocking calls in
   `tokio::task::spawn_blocking()`. Test during
   implementation.

2. **Endpoint lifetime management**: nusb's `Endpoint` is
   obtained from `Interface` and borrows it. We may need
   to get endpoints on-demand per transfer rather than
   caching them, since the `Endpoint` type is
   `!Send`/`!Clone` in some versions. Verify during
   implementation.

3. **Bus number vs device address**: `DeviceInfo` provides
   `bus_id()` (a string) and `device_address()` (a u8).
   On Linux there's also `busnum()`. The `DeviceSource`
   stores `bus: u8`, so we need `busnum()` which is
   Linux-specific. Use `device_address()` for both if
   `busnum()` isn't available.

4. **Trait object-safety**: the current trait uses
   `impl Future` returns and is not object-safe. If the
   channel handler needs dynamic dispatch, we'll need
   either an enum wrapper (`DeviceBackend::Real(RealDevice)
   | Virtual(VirtualMsc)`) or switch to `async-trait`.
   For now, enum dispatch is simplest.

## Testing

### Build and lint

```
./scripts/check-rust.sh fix
pre-commit run --all-files
make test
```

### Unit tests

- Endpoint mapping helpers (usbredir↔USB address).
- Speed conversion.
- `enumerate_physical()` returns without panicking (even
  if no devices are present or USB is inaccessible).

### Manual testing

1. Run ryll on a host with a USB device attached.
2. Call `enumerate_physical()` and verify the device
   appears with correct VID/PID/name.
3. Open the device, verify interfaces are claimed.
4. Submit a control transfer (GET_DESCRIPTOR for device
   descriptor) and verify data is returned.

This requires running outside Docker on a machine with
actual USB devices — it's a manual test, not CI.

## Back brief

Before starting this phase, confirm understanding: we are
implementing the `UsbDeviceBackend` trait for physical USB
devices using the `nusb` crate. The implementation
enumerates real devices, opens them by detaching kernel
drivers and claiming interfaces, reads USB descriptors to
populate the usbredir info structs, and forwards control
and bulk transfers via nusb's API. Hot-plug is deferred.
The code must handle the case where no USB devices are
available (empty enumeration, not a panic).
