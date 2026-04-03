# Phase 10: Testing and QEMU setup

Parent plan: [PLAN-usb-redir.md](/components/ryll/plans/PLAN-usb-redir/)

## Goal

Set up end-to-end testing infrastructure and update
documentation so the USB redirection feature can be
tested, verified, and used. After this phase:

- `make test-qemu-usb` starts a QEMU VM with USB
  redirection enabled.
- A shell script creates a test RAW image and verifies
  end-to-end virtual disk passthrough.
- `docs/configuration.md` documents the USB CLI flags.
- README, ARCHITECTURE, and AGENTS are up to date.

## Background

### What already works

Phases 1-9 implemented the full USB redirection stack:

- SPICE SpiceVMC channel transport (phase 1)
- usbredir protocol parser (phase 2)
- Device backend trait (phase 3)
- Real device passthrough via nusb (phase 4)
- Hello handshake + device lifecycle (phase 5)
- Control and bulk transfers (phase 6)
- Virtual mass storage with BOT/SCSI (phase 7)
- CLI flags + auto-connect (phase 8)
- Interrupt transfers (phase 9)

### Testing approach

The virtual mass storage device is the primary testing
vehicle because it requires no physical USB hardware. A
RAW disk image is passed through to the VM, which sees
a standard USB flash drive. The guest OS can partition,
format, mount, and read/write it.

QEMU needs three extra command-line options to enable USB
redirection:

```
-device qemu-xhci,id=xhci
-chardev spicevmc,id=usbredir1,name=usbredir
-device usb-redir,chardev=usbredir1,id=redir1
```

## Detailed steps

### Step 1: Add test-qemu-usb Makefile target

Add a new target that includes USB controller and
redirection device alongside the existing QEMU config:

```makefile
# Test QEMU with USB redirection enabled
QEMU_USB_TEST_IMAGE := testdata/usb-test.raw

$(QEMU_USB_TEST_IMAGE):
	mkdir -p testdata
	dd if=/dev/zero of=$@ bs=1M count=64

test-qemu-usb: test-qemu-stop $(QEMU_TEST_IMAGE) $(QEMU_USB_TEST_IMAGE)
	cp $(OVMF_VARS) $(QEMU_VARS_COPY)
	qemu-system-x86_64 \
		-display none \
		-machine q35 \
		-m 256 \
		-drive if=pflash,format=raw,readonly=on,file=$(OVMF_CODE) \
		-drive if=pflash,format=raw,file=$(QEMU_VARS_COPY) \
		-drive file=$(QEMU_TEST_IMAGE),format=qcow2,if=virtio \
		-vga qxl \
		-spice port=$(QEMU_SPICE_PORT),disable-ticketing=on \
		-device qemu-xhci,id=xhci \
		-chardev spicevmc,id=usbredir1,name=usbredir \
		-device usb-redir,chardev=usbredir1,id=redir1 \
		-daemonize \
		-pidfile $(QEMU_PID_FILE)
	@echo "QEMU SPICE+USB server on port $(QEMU_SPICE_PORT)"
	@echo "Connect with: ryll --direct localhost:$(QEMU_SPICE_PORT) --usb-disk $(QEMU_USB_TEST_IMAGE)"
```

Key differences from the existing `test-qemu`:
- 256MB RAM (more than 128MB to handle USB subsystem)
- Three USB-related QEMU options added
- Creates a 64MB test RAW image
- Connection hint includes `--usb-disk`

Add `test-qemu-usb` to the `.PHONY` list.

### Step 2: Create test script

Create `tools/test-usb-disk.sh` (user's preference for
scripts in `tools/`):

```bash
#!/bin/bash
# End-to-end test for virtual USB mass storage.
#
# Usage: tools/test-usb-disk.sh [SPICE_PORT]
#
# Starts QEMU with USB redirection, connects ryll with
# a test RAW image, and verifies the device appears.
# Requires: qemu, ryll (in PATH or ./target/debug/ryll)

set -euo pipefail

PORT="${1:-5900}"
IMAGE="testdata/usb-test.raw"
RYLL="${RYLL:-./target/debug/ryll}"

echo "=== USB disk passthrough test ==="

# Create test image if needed
if [ ! -f "$IMAGE" ]; then
    echo "Creating ${IMAGE}..."
    dd if=/dev/zero of="$IMAGE" bs=1M count=64 2>/dev/null
fi

# Start QEMU
echo "Starting QEMU with USB redirection..."
make test-qemu-usb

# Give QEMU time to boot
echo "Waiting for QEMU to start..."
sleep 3

# Connect ryll with the virtual disk (headless, short run)
echo "Connecting ryll with --usb-disk ${IMAGE}..."
timeout 10 "$RYLL" \
    --direct "localhost:${PORT}" \
    --headless \
    --usb-disk "$IMAGE" \
    --verbose \
    2>&1 | tee /tmp/ryll-usb-test.log || true

# Check log for expected messages
echo ""
echo "=== Checking results ==="

if grep -q "server hello" /tmp/ryll-usb-test.log; then
    echo "PASS: usbredir hello exchange completed"
else
    echo "FAIL: no hello exchange in log"
fi

if grep -q "auto-connected" /tmp/ryll-usb-test.log; then
    echo "PASS: virtual disk auto-connected"
else
    echo "FAIL: virtual disk not auto-connected"
fi

if grep -q "USB device connected" /tmp/ryll-usb-test.log; then
    echo "PASS: UsbDeviceConnected event received"
else
    echo "FAIL: no UsbDeviceConnected event"
fi

# Cleanup
make test-qemu-stop
echo ""
echo "=== Test complete ==="
```

This is a basic smoke test — it verifies the protocol
exchange works but doesn't verify guest-side device
enumeration (that would require guest agent or serial
console access, which is more complex).

### Step 3: Update docs/configuration.md

Add a "USB Device Redirection" section documenting:

- `--usb-disk PATH` — attach a RAW disk image as a USB
  mass storage device
- `--usb-disk-ro PATH` — same, read-only
- How to create a test image:
  `dd if=/dev/zero of=test.raw bs=1M count=64`
- QEMU requirements (xHCI controller + spicevmc chardev +
  usb-redir device)
- Example usage for both GUI and headless modes
- Notes: only first disk is connected per channel, file
  must be >= 512 bytes, non-512-aligned files lose
  trailing bytes

### Step 4: Update README.md

Add to the Features list:
```
- **USB redirection** — Forward physical USB devices or
  present RAW disk images as virtual USB mass storage
  devices via `--usb-disk`
```

Update the Quick Start section with a USB example.

### Step 5: Update ARCHITECTURE.md

Add a "USB Redirection" section covering:

- Protocol stack (SPICE SpiceVMC → usbredir → USB MSC
  BOT → SCSI → file I/O)
- Channel handler architecture (hello, lifecycle,
  transfers, interrupt polling)
- Device backend trait and enum dispatch
- Virtual MSC: descriptors, BOT state machine, SCSI
  commands
- Command channel and auto-connect flow

### Step 6: Verify pcap capture

Verify that `--capture` produces a `usbredir.pcap` file
that contains the hello exchange and any subsequent
traffic. Document in troubleshooting.md how to open
it in Wireshark.

### Step 7: Clean target in Makefile

Add cleanup of the test RAW image:

```makefile
clean-testdata:
	rm -f testdata/usb-test.raw
```

Add to the `clean` target or as a separate target.

## Files changed

| File | Change |
|------|--------|
| `Makefile` | Add `test-qemu-usb` target, `QEMU_USB_TEST_IMAGE` variable, `clean-testdata` |
| `tools/test-usb-disk.sh` | **New** — end-to-end smoke test script |
| `docs/configuration.md` | Add USB device redirection section |
| `README.md` | Add USB feature to list, usage example |
| `ARCHITECTURE.md` | Add USB redirection architecture section |
| `docs/troubleshooting.md` | Add USB-related troubleshooting notes |

## What is NOT in scope

- Automated CI testing (requires QEMU + SPICE in the CI
  environment; document as manual test for now).
- Guest-side verification (mounting filesystem, reading
  files) — requires guest agent or serial console, which
  is beyond the current test infrastructure.
- Performance benchmarks for USB throughput.
- Testing with kerbside proxy (separate test once
  kerbside is updated for VMC forwarding).

## Testing

This phase IS the testing — the output is the test
infrastructure itself.

### Verification checklist

1. `make test-qemu-usb` starts QEMU successfully.
2. `tools/test-usb-disk.sh` runs and reports PASS for
   hello exchange and auto-connect.
3. `--capture` produces `usbredir.pcap` with traffic.
4. `docs/configuration.md` renders correctly.
5. `pre-commit run --all-files` passes.
6. `make test` passes (55 unit tests).
7. `shellcheck tools/test-usb-disk.sh` passes.

## Back brief

Before starting this phase, confirm understanding: we are
building test infrastructure and documentation, not new
features. The Makefile gets a USB-enabled QEMU target, a
smoke test script verifies the protocol exchange works, and
documentation is updated throughout (configuration, README,
ARCHITECTURE, troubleshooting). This closes out the
usb-redir implementation plan.
