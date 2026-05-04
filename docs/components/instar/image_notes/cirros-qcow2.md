# cirros-qcow2 Test Image

**Image ID:** `cirros-qcow2`
**Path:** `downloaded/cirros/cirros-0.6.3-x86_64-disk.img`
**Format:** QCOW2 version 3 (compat=1.1)

## Origin

CirrOS is a minimal Linux distribution designed for testing cloud
infrastructure. This is the official x86_64 disk image from the CirrOS
project.

## File Characteristics

- Actual file size: 21,692,416 bytes (20.6875 MiB)
- Virtual size: 117,440,512 bytes (112 MiB)
- Cluster size: 65,536 bytes
- Compression: zlib

## Quirks Discovered

### 1. Decimal Rounding for Values 10-99

**Observed:** qemu-img reports `disk size: 20.7 MiB` for a value of
20.6875 MiB, demonstrating standard rounding (not truncation).

**Analysis:**
- Block-rounded size: 21,700,608 bytes
- 21,700,608 / 1,048,576 = 20.6953125 MiB
- qemu-img rounds to 1 decimal: 20.7 MiB
- This contrasts with values >= 100 which truncate (see qcow2-v2)

**Implementation:** instar uses `round()` for values in the 10-99 range to
get 1 decimal place precision. Combined with `floor()` for >= 100 values,
this matches qemu-img's observed behavior across all magnitudes.

**Documentation:** [docs/quirks.md](/components/instar/quirks/#human-readable-size-formatting)

### 2. max(actual, calculated) for File Length

**Observed:** Initially instar reported the wrong file length (192 KiB)
because it was using the L1 table calculation from QCOW2 parsing, which
is much smaller than the actual file size.

**Analysis:**
- L1 table calculation: ~197 KiB (from metadata structure)
- Actual file size: 21,692,416 bytes (~20.7 MiB)
- qemu-img reports: 21,692,416 bytes (the actual size)

For this real-world image with actual data clusters, the file extends far
beyond the L1 table. qemu-img reports `max(actual_file_size, L1_table_end)`.

**Implementation:** instar now uses `max(file_size, info.actual_size)` for the
file length field, where `info.actual_size` is the L1 table calculation from
the guest and `file_size` is the actual filesystem size from the VMM.

**Documentation:** [docs/quirks.md](/components/instar/quirks/#qcow2-disk_size-calculation)

## Test Value

This image is valuable because:
1. It's a real-world production image (not synthetic)
2. The file size falls in the 10-99 MiB range, testing decimal rounding
3. It has actual data clusters extending beyond metadata, testing the
   max(actual, calculated) logic
4. It includes all QCOW2 v3 features (compression, format-specific info)
