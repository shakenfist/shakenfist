# virtualpc-vhd Test Image

**Image ID:** `virtualpc-vhd`
**Path:** `downloaded/qemu-iotests/virtualpc-dynamic.vhd`
**Format:** VPC (VHD) - Dynamic disk created by Microsoft Virtual PC

## File Characteristics

- File size: 262,656 bytes (256.5 KiB)
- Footer disk_size field: 136,365,211,648 bytes
- CHS geometry: 65,278 cylinders × 16 heads × 255 sectors
- Creator application: "vpc " (Microsoft Virtual PC)
- Cluster size: 2 MiB

## Quirks Discovered

### 1. CHS-Based Virtual Size Calculation

**Observed:** qemu-img reports `virtual size: 127 GiB (136363130880 bytes)`,
not the 136,365,211,648 bytes stored in the footer's disk_size field.

**Analysis:**
- Footer disk_size: 136,365,211,648 bytes
- CHS geometry: 65,278 × 16 × 255 × 512 = 136,363,130,880 bytes
- Difference: 2,080,768 bytes (992 × 2 MiB blocks)
- qemu-img uses CHS calculation for "vpc " creator apps

**Root cause:** Virtual PC relies on CHS geometry for disk addressing. The
disk_size field may exceed what CHS can address, so qemu-img uses the CHS
calculation to ensure compatibility with Virtual PC.

**Implementation:** instar reads the creator_app field from the VHD footer
and uses CHS calculation for "vpc " and "qemu" creators, falling back to
disk_size for all others.

**Documentation:** [docs/quirks.md](/components/instar/quirks/#vhd-virtual-size-calculation)

### 2. Banker's Rounding for File Length

**Observed:** qemu-img reports `file length: 256 KiB (262656 bytes)` for a
file that's 262,656 / 1024 = 256.5 KiB exactly.

**Analysis:**
- 262,656 / 1024 = 256.5 KiB (exactly at midpoint)
- qemu-img rounds to nearest even integer: 256 (not 257)
- This is "round half to even" (banker's rounding) behavior

**Implementation:** instar uses banker's rounding for human-readable size
formatting to match qemu-img output.

**Documentation:** [docs/quirks.md](/components/instar/quirks/#human-readable-size-formatting)

## Test Value

This image is valuable for testing because:
1. It's created by Virtual PC, triggering CHS-based size calculation
2. The file size (256.5 KiB) is exactly at a rounding midpoint
3. The CHS geometry demonstrates the size discrepancy between disk_size and
   actual addressable space
4. It tests the creator_app detection logic for VHD format handling
