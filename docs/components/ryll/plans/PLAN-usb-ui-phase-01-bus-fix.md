# Phase 1: Fix enumerate_physical bus field

Parent plan: [PLAN-usb-ui.md](/components/ryll/plans/PLAN-usb-ui/)

## Goal

Fix a pre-existing bug where `enumerate_physical()` sets
both `bus` and `address` in `DeviceSource::Physical` to
`info.device_address()`. The `bus` field should use
`info.busnum()` so that physical devices can be uniquely
identified by (bus, address) in later phases.

## Background

In `src/usb/real.rs:98-100`, the current code is:

```rust
source: DeviceSource::Physical {
    bus: info.device_address(),
    address: info.device_address(),
},
```

Both fields get the device address. This means the `bus`
value in `UsbDeviceInfo` is wrong, and the `label()` method
in `src/usb/mod.rs:349-353` displays incorrect bus numbers:

```rust
format!(
    "{} [{:04x}:{:04x}] (bus {} addr {})",
    self.name, self.vendor_id, self.product_id, bus, address
)
```

Phase 5 of this plan will use `ConnectPhysical { bus,
address }` to re-find a physical device via nusb during
connection. If `bus` is wrong, the re-lookup will fail or
match the wrong device.

### nusb API

The `nusb::DeviceInfo` struct provides:

- `busnum() -> u8` — Linux-only, returns the USB bus
  number. This matches the `bus: u8` field type in
  `DeviceSource::Physical`.
- `device_address() -> u8` — cross-platform, returns the
  device address within its bus.
- `bus_id() -> &str` — cross-platform string identifier
  for the bus/host controller.

Since `DeviceSource::Physical` already uses `u8` for both
fields, and physical USB passthrough is primarily a Linux
feature, `busnum()` is the correct fix. A cross-platform
change to use `bus_id()` (which returns `&str`) would
require changing the field type and all consumers — that's
out of scope for this bugfix.

### Cross-platform note

On macOS and Windows, `busnum()` is not available. Physical
USB device passthrough on those platforms is already limited
(nusb support varies, and the usbredir channel requires
a SPICE server advertising the channel). When we reach
cross-platform physical device support, we should revisit
this — likely changing `DeviceSource::Physical` to use
`bus_id: String` instead of `bus: u8`. For now, on
non-Linux platforms `enumerate_physical()` returns whatever
nusb provides, and `busnum()` will need a `#[cfg(target_os
= "linux")]` guard or equivalent. However, checking the
nusb 0.2 docs, `busnum()` appears to be available in the
API on all platforms (it just returns the bus number where
available). Need to verify at build time — if it's truly
Linux-only and doesn't compile elsewhere, gate it behind
`cfg` and default to `0` on other platforms.

## Detailed steps

### Step 1: Fix the bus field in enumerate_physical

In `src/usb/real.rs`, line 100, change:

```rust
bus: info.device_address(),
```

to:

```rust
bus: info.busnum(),
```

If `busnum()` is gated behind `#[cfg(target_os = "linux")]`
in nusb 0.2 and doesn't compile cross-platform, use:

```rust
#[cfg(target_os = "linux")]
let bus = info.busnum();
#[cfg(not(target_os = "linux"))]
let bus = 0u8;
```

and then `bus: bus,` in the struct literal. However, first
try the simple `info.busnum()` — it may compile on all
platforms even if the value is only meaningful on Linux.

### Step 2: Verify build

```
make imago  # (or equivalent ryll build)
```

If `busnum()` causes a compile error on the current
platform, apply the `cfg` guard from step 1.

### Step 3: Run existing tests

```
make test
```

The existing unit test `device_info_label_physical` in
`src/usb/mod.rs:460-475` uses synthetic data
(`DeviceSource::Physical { bus: 1, address: 5 }`) so it
will not be affected by this change. No test changes needed.

### Step 4: Run pre-commit checks

```
pre-commit run --all-files
```

Fix any clippy or rustfmt issues.

## Files changed

| File | Change |
|------|--------|
| `src/usb/real.rs` | Line 100: `info.device_address()` → `info.busnum()` |

## What is NOT in scope

- Changing `DeviceSource::Physical` field types (stays
  `u8`).
- Adding `bus_id: String` for cross-platform support.
- Any UI changes.
- Any changes to `UsbCommand` or channel events.

## Testing

- `make test` — existing tests pass unchanged.
- `pre-commit run --all-files` — lint clean.
- If USB hardware is available, run ryll with `--usb-disk`
  and check the status bar label shows correct bus numbers
  for any physical devices. This is a manual visual check
  only — no automated test exists for physical device
  enumeration (it depends on what hardware is plugged in).

## Back brief

This phase fixes a one-line bug: `bus: info.device_address()`
should be `bus: info.busnum()` in `enumerate_physical()`.
The fix is a prerequisite for phase 5, which uses bus/address
to re-find physical devices. The change is isolated to
`src/usb/real.rs` and does not affect any other code. The
only risk is cross-platform compilation of `busnum()` — if
it fails, we add a `cfg` guard.
