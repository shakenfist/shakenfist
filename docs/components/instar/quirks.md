# qemu-img Quirks

This document describes known behaviors in qemu-img that differ from what one
might expect, and how instar handles these cases.

## Quirk Classification: Safe vs Unsafe

Quirks are classified into two categories based on their security implications:

### Safe Quirks

Safe quirks affect output formatting or calculation methods but do not introduce
security vulnerabilities. Examples include:

- Size rounding (to block or sector boundaries)
- Number formatting (banker's rounding, significant figures)
- VHD size calculation methods

instar **mimics safe quirks by default** for qemu-img compatibility. Use
`--ignore-quirks` to get more intuitive behavior.

### Unsafe Quirks

Unsafe quirks are behaviors that can enable security vulnerabilities or
reduce format identification accuracy. Examples include:

- **RAW as fallback format** - Treating any unrecognized file as a valid
  raw disk image, which enables backing file disclosure attacks
- **ISO reported as RAW** - Not detecting ISO 9660 format, reducing format
  visibility for policy decisions

instar **does NOT mimic unsafe quirks by default**. Instead, instar applies
additional validation (e.g., requiring MBR/GPT partition tables for raw images,
detecting ISO 9660 format). Use `--unsafe-quirks` to match qemu-img's behavior
for compatibility testing.

### Summary

| Flag | Safe Quirks | Unsafe Quirks |
|------|-------------|---------------|
| (default) | Enabled (qemu-img compatible) | Disabled (secure) |
| `--ignore-quirks` | Disabled (intuitive output) | Disabled (secure) |
| `--unsafe-quirks` | Enabled (qemu-img compatible) | Enabled (insecure) |

See [configuration.md](/components/instar/configuration/) for full flag documentation.

## Extra Detail Mode

instar can provide additional format-specific information that qemu-img does not
output. This extra information is disabled by default for qemu-img compatibility,
but can be enabled with the `--extra-detail` flag.

### VDI Format-Specific Information

qemu-img does not output `format-specific` information for VDI (VirtualBox)
images, even though the format contains useful metadata:

```json
{
    "format": "vdi",
    "format-specific": {
        "type": "vdi",
        "data": {
            "image-type": "dynamic",
            "block-size": 1048576,
            "blocks-in-image": 10,
            "blocks-allocated": 0,
            "uuid": "914d94c9-e6a6-4968-9064-29fd03a9cdc2"
        }
    }
}
```

**Default behavior**: instar matches qemu-img by not outputting VDI format-specific
information.

**With `--extra-detail` flag**: instar outputs the VDI format-specific section,
providing additional metadata about the image structure.

### When to Use `--extra-detail`

Use this flag when you need:
- VDI image type (dynamic vs fixed)
- VDI block allocation statistics
- VDI image UUID

The extra information is particularly useful for:
- Debugging VirtualBox image issues
- Migration planning (understanding allocation patterns)
- Image inspection and auditing

---

## QCOW2 disk_size Calculation

**Classification: Safe Quirk**

### Observed Behavior

For QCOW2 files, `qemu-img info` reports a `disk size` that may differ from the
actual file size on disk. For example, with a generated QCOW2 v2 test file:

- Actual file size (from `stat` or `ls -l`): 196616 bytes
- qemu-img reported disk size: 197120 bytes (192 KiB)
- Difference: 504 bytes

### Root Cause

qemu-img calculates the "disk size" based on the QCOW2 internal structure,
specifically by finding the highest allocated offset in the file's metadata
(L1 table, refcount table, etc.) and rounding up to a sector boundary (512
bytes).

For the test file:
- L1 table offset: 196608 (0x30000)
- L1 table has 1 entry (8 bytes)
- Actual file end: 196608 + 8 = 196616 bytes
- qemu-img calculation: 196608 + 512 = 197120 bytes (sector-aligned)

### Why This Happens

qemu-img appears to calculate "disk size" as the expected size based on the
image's internal structure, not the actual filesystem size. This calculation:

1. Finds the highest used offset in metadata structures
2. Rounds up to the nearest sector boundary (512 bytes)
3. Reports this as the "disk size"

This approach makes sense for images that might be sparse or have trailing
allocations, but can report larger sizes than the actual file.

### instar Behavior

**Default behavior**: instar matches qemu-img by calculating disk size based
on the image's internal metadata structure, rounded up to sector boundaries.
This ensures drop-in replacement compatibility.

**With `--ignore-quirks` flag**: instar reports the actual file size from the
underlying storage, matching what `stat` or `ls -l` reports.

### Why Match qemu-img?

Since instar aims to be a drop-in replacement for `qemu-img info`, matching
the output exactly (including this calculation) reduces friction for users
migrating from qemu-img. Scripts and tools that parse qemu-img output will
work unchanged.

The `--ignore-quirks` flag provides an escape hatch for users who need the
true filesystem size.

### Test Implications

The test file `qcow2_v2.qcow2` in instar-testdata was generated with qemu-img
(`qemu-img create -f qcow2 -o compat=0.10 ...`). By matching qemu-img's
calculation, tests can perform exact output comparison.

## Block-Rounded Disk Size

**Classification: Safe Quirk**

### Observed Behavior

qemu-img reports "disk size" rounded up to filesystem block boundaries (4096
bytes), not the actual file size.

For the QCOW2 v2 test file:
- Actual file size: 196616 bytes
- qemu-img disk size: 200704 bytes (196 KiB)
- Calculation: ceil(196616 / 4096) * 4096 = 49 * 4096 = 200704

### instar Behavior

**Default behavior**: instar matches qemu-img by rounding file size up to
4096-byte blocks.

**With `--ignore-quirks` flag**: instar reports the actual file size.

## Human-Readable Size Formatting

**Classification: Safe Quirk**

### Observed Behavior

qemu-img uses `%0.3g` printf format (3 significant figures) for human-readable
sizes. This rounds to 3 significant figures, with the number of decimal places
depending on the magnitude:

**For values >= 100** (displayed as integers):

Rounds to nearest integer using "round half to even" (banker's rounding):
- 126.998 GiB → "127 GiB" (rounds up from 126.998)
- 192.5 KiB → "192 KiB" (rounds to even from 192.5)
- 256.5 KiB → "256 KiB" (rounds to even from 256.5)
- 127.5 GiB → "128 GiB" (rounds to even from 127.5)

**For values 10-99** (displayed with 1 decimal place):

Standard rounding applies:
- 20.6875 MiB → "20.7 MiB" (rounds from 20.6875)
- 15.44 KiB → "15.4 KiB" (rounds from 15.44)

### Technical Details

This behavior stems from C printf's `%0.3g` format which:
1. Rounds to 3 significant figures using "round half to even" (banker's rounding)
2. Removes trailing zeros after the decimal point
3. For integer results, displays no decimal point

The key distinction is at exact midpoints (like 192.5): C rounds to the nearest
even number (192), while Rust's default `round()` rounds away from zero (193).

### instar Behavior

**Default behavior**: instar matches qemu-img's formatting using banker's rounding:
- Values >= 100: round to nearest integer (ties to even)
- Values 10-99: round to 1 decimal place (ties to even)
- Values 1-9: round to 2 decimal places (ties to even)
- Values < 1: round to 3 decimal places (ties to even)

**With `--ignore-quirks` flag**: instar uses consistent rounding with 1 decimal
place when the value is not a whole number (e.g., "192.5 KiB" instead of
"192 KiB").

## Child Node File Length

**Classification: Safe Quirk**

### Observed Behavior

In qemu-img 8.0+, the Child node '/file' section reports a "file length" (human)
or "virtual-size" (JSON) that may differ from the actual filesystem size.

qemu-img reports the **larger** of:
1. The actual filesystem file size
2. The calculated size based on internal metadata (e.g., L1 table offset
   rounded up to sector boundary for QCOW2)

For files with data beyond the metadata structures (like real disk images),
qemu-img reports the actual file size. For minimal files where the metadata
calculation exceeds the actual size (like empty test images), it reports the
metadata-based calculation.

### Example

For a minimal QCOW2 v2 test file:
- Actual file size: 196616 bytes
- L1 table calculation: (196608 + 512) = 197120 bytes
- qemu-img file length: max(196616, 197120) = 197120 bytes

For a real disk image (cirros):
- Actual file size: 21692416 bytes
- L1 table calculation: much smaller (metadata is at the start)
- qemu-img file length: max(21692416, calc) = 21692416 bytes

### instar Behavior

**Default behavior**: instar matches qemu-img by reporting the larger of the
actual file size and the internal metadata calculation.

**With `--ignore-quirks` flag**: instar reports the actual filesystem size.

## Summary of `--ignore-quirks` Effects

When `--ignore-quirks` is specified:

| Field | Default (qemu-img compatible) | With --ignore-quirks |
|-------|------------------------------|---------------------|
| disk size | Block-rounded (4096 bytes) | Actual file size |
| file length | max(actual, metadata calc) | Actual file size |
| Size formatting | 3 significant figures | 1 decimal place |

## File Sparseness and Git

**Classification: Safe Quirk** (environmental, not a qemu-img behavior)

### Observed Behavior

qemu-img's reported "disk size" depends on the actual allocation of sparse
files on disk. When disk images are transferred through git (clone, fetch),
sparse holes may be filled with zeros, increasing the reported disk size.

For example, the `iotest-dynamic-1G.vhdx` file:
- Original (sparse): disk size 66.1 MiB
- After git clone: disk size 100 MiB (holes filled with zeros)
- After `fallocate -d`: disk size 66.1 MiB (holes restored)

### Root Cause

Git stores file contents as blobs and does not preserve sparse file semantics.
When git writes a file during checkout, it writes all bytes sequentially,
effectively "filling in" sparse holes with actual zero bytes. This increases
the file's allocated blocks on disk.

### CI/Testing Implications

Test baselines are generated with sparse files. When the testdata repository
is cloned in CI, the files may lose sparseness, causing disk_size mismatches.

### Solution

After cloning the testdata repository, restore sparse holes using
`cp --sparse=always` which is more robust than `fallocate -d`:

```bash
find downloaded/ -type f \( \
    -name "*.qcow2" -o \
    -name "*.vmdk" -o \
    -name "*.vhd" -o \
    -name "*.vhdx" -o \
    -name "*.img" \
\) -print0 | while IFS= read -r -d '' file; do
    cp --sparse=always "$file" "$file.sparse"
    mv "$file.sparse" "$file"
done
```

**Why `cp --sparse=always` instead of `fallocate -d`?**

`fallocate -d` (FALLOC_FL_PUNCH_HOLE) can only punch holes in contiguous
zero-filled regions that are aligned to filesystem block boundaries. Files
with partial zero blocks (blocks containing mostly zeros but a few non-zero
bytes) cannot have those regions converted to holes.

`cp --sparse=always` reads the file content and writes a new file, skipping
zero-filled blocks entirely. This correctly handles files with complex sparse
patterns where `fallocate -d` would leave extra blocks allocated.

### Test Framework Handling

Even with `cp --sparse=always`, re-sparsified files may not have identical
block allocation patterns to the original. Different filesystems, kernel
versions, or sparse detection algorithms can result in significantly different
allocation patterns.

For this reason, the test comparison framework (`tests/helpers/comparators.py`)
**looks up the actual disk size** from the filesystem at test time using
`os.stat().st_blocks * 512` and substitutes this value into the expected
output before comparison. This ensures:

1. Tests compare against the filesystem's actual view of the file
2. No reliance on potentially stale baseline values for disk size
3. Exact matching instead of arbitrary tolerance thresholds

This approach is more scientifically correct than using tolerance, because:
1. `actual-size` reflects filesystem allocation, not image content
2. We're testing that instar correctly reports what the filesystem says
3. Both instar and the test framework query the same filesystem state

### Note

This is not a qemu-img quirk per se, but rather a filesystem/git interaction
that affects qemu-img output consistency in CI environments.

## VHD Virtual Size Calculation

**Classification: Safe Quirk**

### Observed Behavior

qemu-img calculates VHD virtual size differently depending on the creator
application that produced the VHD file. The VHD footer contains both a
"current size" field (explicit virtual size in bytes) and CHS geometry values
(cylinders, heads, sectors per track).

**For Virtual PC and legacy qemu VHDs** (creator_app = "vpc " or "qemu"):

qemu-img calculates virtual size from CHS geometry:
```
virtual_size = cylinders × heads × sectors_per_track × 512
```

**For modern applications** (Hyper-V, Disk2vhd, XenServer, Azure, etc.):

qemu-img uses the disk_size field directly from the VHD footer.

### Example

For the `virtualpc-dynamic.vhd` test image (created by Virtual PC):
- Footer disk_size field: 136,365,211,648 bytes
- CHS geometry: 65,278 cylinders × 16 heads × 255 sectors
- CHS-calculated size: 65,278 × 16 × 255 × 512 = 136,363,130,880 bytes
- qemu-img reports: 136,363,130,880 bytes (CHS calculation)

The difference (2,080,768 bytes) exists because Virtual PC's geometry algorithm
cannot exactly represent the requested size, so it rounds down to the nearest
CHS-representable value.

### Why This Matters

Virtual PC and original qemu create VHD files that rely on CHS geometry for
compatibility with legacy systems. Using the disk_size field directly for
these images would report a larger virtual size than the geometry can address,
potentially causing data corruption if writes exceed the CHS-addressable range.

### Maximum CHS Geometry

When CHS geometry reaches maximum values (65,535 × 16 × 255 = 267,382,800
sectors = ~127 GiB), qemu-img falls back to using the disk_size field
regardless of creator application. This prevents truncation for large disks.

### Known Creator Applications

| Creator App | Size Method | Application |
|-------------|-------------|-------------|
| `vpc `      | CHS         | Microsoft Virtual PC |
| `qemu`      | CHS         | qemu (legacy) |
| `qem2`      | disk_size   | qemu (modern) |
| `win `      | disk_size   | Microsoft Hyper-V |
| `d2v `      | disk_size   | Disk2vhd |
| `tap\0`     | disk_size   | XenServer |
| `CTXS`      | disk_size   | XenConverter |
| `wa\0\0`    | disk_size   | Microsoft Azure |

### The rule changed in qemu-img 10.0

The table above is the **qemu-img 10.0+** rule, and it is the one instar
implements. Before 10.0, qemu-img worked from the opposite default: it used
CHS geometry for *every* creator application except `win ` and `qem2` (or when
CHS was at maximum). The two rules agree for every creator app in the table
whose behaviour anyone has documented — `vpc `, `qemu`, `win `, `qem2` and
`d2v ` all resolve identically on both sides of 10.0 — but they disagree for
any creator app not on that list.

Measured across qemu-img 6.0.0 to 10.2.0 on a VHD whose CHS addresses less
than its disk_size field:

| Creator app | qemu-img < 10.0 | qemu-img >= 10.0 | instar |
|-------------|-----------------|------------------|--------|
| `vpc `      | CHS             | CHS              | CHS |
| `win `, `qem2` | disk_size    | disk_size        | disk_size |
| anything else (e.g. `xen `, `azur`, zeros) | **CHS** | **disk_size** | disk_size |

### Known divergence: unknown creator apps under emulated qemu < 10.0

instar always applies the 10.0+ rule, so when it is emulating an older
qemu-img (`--qemu-version 7.2`, or running on a distro whose qemu-img predates
10.0) it reports the disk_size field for an unrecognised creator app where the
real older qemu-img would report the CHS product.

The exposure is narrow: it needs a VHD whose creator app is outside the table
*and* whose CHS geometry disagrees with its disk_size field. No such image
exists in the test corpus — the Virtual PC, Hyper-V and Disk2vhd fixtures all
resolve identically on every qemu version. instar's own VHD writer cannot
produce one either, because it stamps `qem2` (see below).

This is documented rather than fixed because the size rule is evaluated
**guest-side** (`src/operations/info`), so gating it on the output profile
would mean widening the guest ABI to carry the emulated version. That is a
disproportionate change for a case with no demonstrated real-world image; see
`docs/plans/PLAN-distro-matrix-ci-phase-02b-qemu-output-parity.md` (step 2b-F)
for the measurements and the decision.

### instar's own VHD output uses `qem2`

instar writes `qem2` — qemu's `force_size` marker — as the creator app of
every VHD it produces, which makes every qemu-img version read the size from
the disk_size field.

This is deliberate and load-bearing. instar preserves the exact requested
virtual size rather than rounding it up to a CHS-representable value the way
`qemu-img create -f vpc` does, so its footers can declare a size the CHS
geometry does not fully address (for a 2 MiB image, by 8192 bytes). Under any
creator app that resolves to CHS, every qemu-img before 10.0 would read those
images short and silently truncate the tail. `qem2` states explicitly that the
declared size is authoritative, which is what instar means.

### instar Behavior

**Default behavior**: instar matches qemu-img by checking the creator_app field
and using CHS calculation for "vpc " and "qemu" creators (unless CHS is at
maximum), or disk_size field for all others.

**With `--ignore-quirks` flag**: Currently no change; the VHD size calculation
always matches qemu-img for maximum compatibility.

## RAW as Fallback Format

**Classification: Unsafe Quirk** - This behavior enables security vulnerabilities.

### Observed Behavior

qemu-img treats **any** file that does not match a known format's magic number
as a "raw" disk image. This includes:

- Actual raw disk images (with MBR/GPT partition tables)
- Plain text files
- Binary data files
- Corrupted or truncated images
- Random garbage

For example, a simple text file:

```bash
$ echo "This is just a plain text file." > /tmp/test.txt
$ qemu-img info /tmp/test.txt
image: /tmp/test.txt
file format: raw
virtual size: 512 B (512 bytes)
disk size: 4 KiB
```

### Why This Matters

This behavior has important implications:

1. **No format validation**: qemu-img cannot distinguish between a genuine raw
   disk image and arbitrary data. A user could upload a PDF, JPEG, or executable
   and qemu-img would happily call it a "raw" disk image.

2. **Testing considerations**: When testing format detection, any file that
   fails to match known formats will be reported as "raw" rather than
   "unknown" or generating an error.

### Security Implications: The Root Cause of Backing File Attacks

**This "raw as fallback" behavior is the fundamental design flaw that enables
backing file disclosure attacks (CVE-2015-5163, CVE-2024-32498, etc.).**

Consider what happens when qemu-img processes a QCOW2 image with
`backing_file = "/etc/shadow"`:

1. qemu-img opens the QCOW2 image and parses its header
2. qemu-img sees the backing file reference to `/etc/shadow`
3. qemu-img opens `/etc/shadow` and tries to detect its format
4. `/etc/shadow` has no recognized magic number (it's a text file)
5. qemu-img treats `/etc/shadow` as a "raw" disk image
6. qemu-img reads the file contents as disk data

If qemu-img instead **rejected** files that don't match any known disk image
format, the attack would fail at step 5. The backing file would be rejected
as "not a valid disk image" rather than being slurped up as "raw" data.

This design choice - treating unknown files as valid raw images rather than
rejecting them - is what transforms a simple path reference into a data
exfiltration vulnerability. A more defensive design would require backing
files to have recognizable disk image headers (QCOW2, VMDK, VHD, or at minimum
a valid MBR/GPT partition table for raw images).

**Note**: instar avoids this vulnerability entirely through its KVM sandbox
architecture - the guest cannot open arbitrary files regardless of format
detection behavior. See [format-detection-safety.md](/components/instar/format-detection-safety/)
for details.

### Cloud Environment Implications

In cloud environments (OpenStack, etc.), format validation cannot rely solely
on qemu-img. OpenStack's Glance uses oslo.utils `format_inspector` which
detects GPT/MBR partition tables to distinguish "actual disk images" from
"files we don't recognize."

### Comparison with oslo.utils format_inspector

oslo.utils takes a different approach:

| File Type | qemu-img | oslo.utils |
|-----------|----------|------------|
| MBR-partitioned disk | raw | gpt (detects MBR) |
| GPT-partitioned disk | raw | gpt |
| FAT filesystem (no partition) | raw | raw |
| Plain text file | raw | raw |
| Random garbage | raw | raw |
| Corrupted QCOW2 | raw (usually) | error or raw |

oslo.utils can distinguish between "files with valid partition tables" (likely
real disk images) and "files we don't recognize" (both labeled "raw" but with
different confidence levels).

### instar Behavior

**Default behavior (secure)**: instar requires files detected as "raw" to have
a valid partition table (MBR or GPT). Files without recognized format headers
AND without valid partition tables are rejected as "unknown format" rather
than being silently accepted as raw images.

This prevents the backing file disclosure attacks described above, because
`/etc/shadow` would be rejected as "not a valid disk image" rather than
being treated as a raw disk.

**With `--unsafe-quirks` flag**: instar matches qemu-img's behavior, treating
any unrecognized file as a valid raw image. This is required for exact
qemu-img output compatibility but should only be used in controlled testing
environments, never in production.

**Partition table detection**: instar checks for:
- **MBR**: Valid 0xAA55 signature at offset 510-511, with at least one
  partition entry having a valid boot flag (0x00 or 0x80)
- **GPT**: Protective MBR with partition type 0xEE, followed by valid
  GPT header at LBA 1

See [format-coverage.md](/components/instar/format-coverage/) for comparison with oslo.utils
format_inspector.

### Test Images

The instar-testdata repository includes several test cases for this behavior:

- `raw-random-garbage.raw` - Random bytes (detected as raw)
- `raw-misleading-header.raw` - QCOW2 magic but invalid header (detected as raw)
- `raw-minimal-1byte.raw` - Single byte file (detected as raw)

## ISO 9660 Detection vs RAW

**Classification: Unsafe Quirk** - Related to format identification accuracy.

### Observed Behavior

qemu-img does not specifically detect ISO 9660 (CD/DVD image) format. Instead,
it treats ISO files as "raw" disk images:

```bash
$ qemu-img info ubuntu.iso
image: ubuntu.iso
file format: raw
virtual size: 4.7 GiB (5046586880 bytes)
disk size: 4.7 GiB
```

### Why This Matters

ISO 9660 is a distinct filesystem format used for CD/DVD images, with a
well-defined structure:
- Primary Volume Descriptor at sector 16 (byte offset 32768)
- Standard identifier "CD001" at bytes 1-5 of the PVD

Treating ISO files as "raw" means:
1. Cloud platforms cannot distinguish ISOs from actual raw disk images
2. Policy decisions (e.g., "reject ISO uploads") require external detection
3. Format-specific handling (e.g., mount options) cannot be automated

### instar Behavior

**Default behavior (secure)**: instar detects ISO 9660 format by checking for
the "CD001" magic at byte offset 32769. ISO files are reported as `file format: iso`
rather than raw. This allows:
- OpenStack/Glance to identify and policy-control ISO uploads
- Better format reporting for administrators
- Accurate format statistics

**With `--unsafe-quirks` flag**: instar matches qemu-img's behavior, treating
ISO files as "raw" disk images. This is required for exact qemu-img output
compatibility but provides less information about the actual file format.

### Technical Details

ISO 9660 detection checks for:
- "CD001" identifier at byte offset 32769 (32768 + 1)
- Works with both small (512-byte) and large (65536-byte) sector sizes

The detection is performed after other format checks (QCOW2, VMDK, VHD, etc.)
but before the partition table validation for raw images.

## Check Operation Format Handling

**Classification: Unsafe Quirk** - Related to format identification and validation
accuracy.

### Quirk 1: Format Misidentification

#### Observed Behavior

`qemu-img check` only recognizes QCOW2 format. All other image formats (VMDK,
VHDX, VHD, VDI, etc.) are treated as "raw" format:

```bash
$ qemu-img check image.vmdk
qemu-img: Could not open 'image.vmdk': Unknown image format
# Or with older versions:
This image format does not support checks
```

qemu-img does not attempt to detect the actual format when running check.

#### Why This Matters

1. **Format misidentification**: A valid VMDK image is not recognized as VMDK -
   it's either rejected or processed as unknown/raw format.

2. **Reduced visibility**: Administrators cannot determine what format an image
   actually is using `qemu-img check`.

#### instar Behavior

**Default behavior (secure)**: instar detects the actual format of the image
using the same detection logic as `instar info`. VMDK images are identified as
"vmdk", VHDX as "vhdx", etc.

**With `--unsafe-quirks` flag**: instar matches qemu-img's behavior, only
detecting QCOW2 format. All other formats are reported as "raw".

### Quirk 2: Lack of Validation for Non-QCOW2 Formats

#### Observed Behavior

`qemu-img check` only performs structural validation for QCOW2 images. For all
other formats, it reports that checks are not supported and exits with success:

```bash
$ qemu-img check simple.vmdk
This image format does not support checks
$ echo $?
0  # Success exit code despite no validation performed
```

This means a corrupt VMDK, VHDX, or VHD file would appear to "pass" the check
simply because qemu-img didn't actually examine it.

#### Why This Matters

1. **False sense of security**: Users may believe an image has been validated
   when no validation occurred.

2. **Missed corruptions**: Corrupt headers, invalid offsets, and malformed
   metadata are not detected for non-QCOW2 formats.

#### instar Behavior

**Default behavior (secure)**: instar performs format-appropriate validation for
supported formats:

- **VMDK**: Validates header version (1-3), capacity > 0, grain size power of 2,
  descriptor offset within file bounds
- **VHDX**: Validates file signature and region table signature at offset 0x30000
- **VHD**: Validates footer cookie and disk type (2=fixed, 3=dynamic, 4=diff)

Images with structural problems are marked with `FLAG_HAS_CORRUPTIONS` and
report specific error counts. Images that pass validation are marked `FLAG_VALID`.

**With `--unsafe-quirks` flag**: instar skips validation for non-QCOW2 formats,
matching qemu-img's behavior. Non-QCOW2 images are marked as
`FLAG_NOT_SUPPORTED | FLAG_VALID` without examination.

### Test Images (Planned)

The following corrupt test images are planned for instar-testdata to validate
corruption detection. Tests skip gracefully if these files do not exist:

| Image | Format | Corruption |
|-------|--------|------------|
| `vmdk-corrupt-version.vmdk` | VMDK | Invalid version (255) |
| `vhdx-corrupt-region.vhdx` | VHDX | Invalid region table signature |
| `vhd-corrupt-disktype.vhd` | VHD | Invalid disk type (255) |

These images should be placed in `custom/format-coverage/` when created.

### Summary

| Mode | Format Detection | Validation |
|------|------------------|------------|
| Default (secure) | All formats | QCOW2, VMDK, VHDX, VHD |
| `--unsafe-quirks` | QCOW2 only | QCOW2 only |

## Check JSON Schema Consistency

**Classification: Safe Quirk** - Affects JSON output schema predictability.

### Observed Behavior

`qemu-img check --output=json` conditionally omits fields from its JSON
output when their values are zero. For example, a QCOW2 image with no
corruptions produces:

```json
{
    "filename": "test.qcow2",
    "format": "qcow2",
    "check-errors": 0,
    "image-end-offset": 262144,
    "total-clusters": 2,
    "allocated-clusters": 0,
    "fragmented-clusters": 0
}
```

The `corruptions`, `leaks`, and `refcount-errors` fields are absent.
They only appear when their values are greater than zero:

```json
{
    "filename": "corrupt.qcow2",
    "format": "qcow2",
    "check-errors": 3,
    "corruptions": 3,
    "image-end-offset": 262144,
    ...
}
```

### Why This Matters

1. **Inconsistent schema**: Callers must handle both the presence and
   absence of these fields, adding complexity to JSON parsing.

2. **Brittle tooling**: Tools that expect a fixed set of fields may
   break when corruptions are first encountered, or may silently
   treat missing fields as absent rather than zero.

3. **API contract ambiguity**: It is unclear whether a missing field
   means "zero errors" or "not checked".

### instar Behavior

**Default behavior (consistent schema)**: instar always includes
`corruptions`, `leaks`, and `refcount-errors` in JSON output,
regardless of their values. This provides a predictable, fixed schema
that callers can rely on:

```json
{
    "filename": "test.qcow2",
    "format": "qcow2",
    "check-errors": 0,
    "corruptions": 0,
    "leaks": 0,
    "refcount-errors": 0,
    "image-end-offset": 262144,
    "total-clusters": 2,
    "allocated-clusters": 0,
    "fragmented-clusters": 0
}
```

**With `--unsafe-quirks` flag**: instar matches qemu-img's behavior,
omitting `corruptions`, `leaks`, and `refcount-errors` when their
values are zero.

### Current Validation Limitations

instar's QCOW2 check implementation has the following limitations compared
to qemu-img:

1. **Partial L2 table validation**: Only the first sector of each L2 table
   is validated (approximately 12.5% coverage for 64KB clusters). The
   fragmentation calculation is based on this partial sample.

2. **No refcount validation**: The refcount table offset is verified, but
   individual refcount entries are not read or validated. This means:
   - `refcount-errors` will always be 0
   - `leaks` will always be 0

Users comparing instar output against `qemu-img check` may notice these
discrepancies, particularly for images with refcount issues or extensive
L2 table corruption beyond the first sector.

## measure subcommand quirks

### `--image-opts` is rejected

`qemu-img measure --image-opts driver=qcow2,file.filename=...`
accepts a descriptor-based source specification. instar does
not support this form and errors out with a clear message.
Use the positional `INPUT` argument or `--size SIZE` instead.

### `-o help` is rejected

`qemu-img measure -o help -O qcow2` prints the available
options for the target format. instar errors out with a
clear message. Use `instar measure --help` for the available
individual flags; see `docs/measure.md` for the `-o` key
reference per target.

### `bitmaps` field emission rule

For `--output=json` with `-O qcow2` and a qcow2 v3 source
image, instar emits a leading `"bitmaps": 0` field (and the
equivalent `bitmaps size: 0` trailing line in human output).
This matches qemu-img's behaviour exactly:

- target = qcow2 AND source = qcow2 v3 (compat=1.1): emit
  the field.
- target = qcow2 AND source = qcow2 v2 (compat=0.10): omit.
- target = qcow2 AND `--size SIZE` mode: omit.
- target ≠ qcow2: omit.

instar's gate is a 4+4 byte peek of the source's first
sector (magic + version field). See `src/vmm/src/chain.rs`
for the helper `peek_is_qcow2_v3`.

### Convert-vs-measure size bounds for vmdk / vpc / vhdx

For target formats qemu-img cannot measure, the bound that
`instar measure` predicts must accommodate the convert
writer's actual output size. The relationship is:

- `instar convert -O <fmt>` output ≤
  `fully_allocated + max(1 MiB, fully_allocated / 16)`.
- The lower bound (`actual >= required`) is permissive:
  instar's parser scanners can over-report `allocated_bytes`
  and convert's zero-skipping can produce strictly less than
  `required`. That is not a bug.

The cushion absorbs the convert writer's per-block sector
alignment slack — each allocated block and metadata region
is padded to the output sector size (default 64 KiB), so the
cumulative overhead scales with block count.
`scripts/differential-fuzz.py::op_measure` and the round-
trip tests in `tests/test_measure.py::TestMeasureRoundTrip`
both use this same bound.

### Known scanner divergences from qemu-img

For raw and qcow2 targets, instar measure matches qemu-img
exactly on the cross-version baseline matrix. A handful of
source-image cases exhibit small numeric divergences because
instar's parser scanners are simpler than qemu-img's. The
canonical list lives at
`tests/test_measure.py::KNOWN_SOURCE_SCANNER_DIVERGENCES`.
Categories:

- Raw sources with on-disk sparse extents: instar over-
  reports `required` because the raw scanner does not use
  `SEEK_HOLE`/`SEEK_DATA`.
- QCOW2 sources for some real-world images: instar's
  scanner counts allocated bytes slightly differently
  (compressed-cluster or extended-L2 subcluster edge
  case under investigation).
- QCOW2 sources with backing chains: instar reports the
  top layer's allocations only.
- VHDX sources: instar treats every BAT block as fully
  allocated.
- VMDK multi-extent source layouts: instar's extent map
  propagation differs.
- VHD legacy CHS-only sources: instar's reported
  virtual_size differs by approximately 2 MiB.

See `docs/measure.md` for the user-facing presentation of
these divergences.

## map subcommand quirks

### `--image-opts` is rejected

`qemu-img map --image-opts driver=qcow2,file.filename=...`
accepts a descriptor-based source specification. instar does
not support this form and errors out before launching the
guest. Use the positional `FILENAME` argument instead.

### Backing-chain `depth` is always 0 in v1

`qemu-img map` walks the backing chain when present and
emits a non-zero `depth` field in JSON output for extents
that resolve through a parent image. instar's walkers report the active layer only and refuse sources
that carry a backing/parent reference (qcow2
`backing_file_offset != 0`, vhd `disk_type ==
DISK_TYPE_DIFFERENCING`, vhdx `has_parent`). Chain
composition is tracked as a follow-up under
[PLAN-map.md](/components/instar/plans/PLAN-map/). In v1 the `depth` JSON
field is always `0`.

### Raw source sparseness is not detected

`qemu-img map` calls `lseek(SEEK_HOLE)` / `lseek(SEEK_DATA)`
on the underlying file for raw sources and reports the
sparse vs. dense regions as separate extents
(`present: true, zero: true, data: false` for the sparse
runs). instar's no_std raw walker has no syscall surface
inside the guest and reports one fully-allocated `data:
true` extent covering the whole virtual size. A host-side
`SEEK_HOLE` pre-pass that feeds an extent list through
`MapConfig` is tracked as future work.

### VHD unallocated blocks are reported as `present: false`

`qemu-img map` reports a dynamic VHD's unallocated BAT entries
(`0xFFFFFFFF`) as `present: true, zero: true, data: false` —
the same `ZeroAllocated` convention it applies to raw sparse
runs. instar's vhd walker reports them as `present: false,
zero: true, data: false` (`Hole`), faithful to the on-disk
BAT marker. Functionally equivalent for downstream consumers
that care only about which bytes contain data; visually
different in the `present` field. The `KNOWN_MAP_DIVERGENCES` (`hyperv-dynamic-vhd`,
`virtualpc-vhd`) and the differential fuzzer
(`MAP_FIELD_SKIPS` in `scripts/differential-fuzz.py`)
both skip the `present` field on vpc sources for this
reason.

### VHDX `PAYLOAD_BLOCK_PARTIALLY_PRESENT` is reported as `data: true`

`qemu-img map` walks the per-sector bitmap for partially-
present VHDX blocks and emits per-sector extents. instar's vhdx walker treats `PARTIALLY_PRESENT` as fully
present (same posture as `scan_allocation`) and reports
the entire block as one `data: true` extent. The
per-sector-bitmap walk is tracked as future work.

### VMDK multi-extent sources are refused

`qemu-img map` reads the VMDK descriptor and walks the
multi-extent layout. instar's `VmdkState::init` only parses
the VMDK4 binary header, so descriptor-driven (multi-extent
monolithicFlat / 2GbMaxExtent…) sources fail init. The
host CLI also refuses them via `peek_is_vmdk_descriptor`
before launching the guest, pointing the user at `qemu-img
map` as the workaround.

### qcow2 v3 standard-L2 `QCOW_OFLAG_ZERO` honoured (fixed)

In qcow2 v3 (compat=1.1) images that use *standard* L2 tables
(8-byte entries, not extended L2), the `QCOW_OFLAG_ZERO` bit
(bit 0) on an L2 entry signals `QCOW2_CLUSTER_ZERO_PLAIN`
(when `host_offset == 0`) or `QCOW2_CLUSTER_ZERO_ALLOC` (when
`host_offset != 0`) — both of which qemu-img reports as
`present: true, zero: true, data: false` (`ZeroAllocated`).
Historically instar's `classify_qcow2_l2_standard` ignored
the bit and treated any non-zero L2 entry without
`OFLAG_COMPRESSED` as `Data` (reporting `Hole` when
`host_offset == 0`) — a pre-existing gap in the qcow2 parser
(`cluster_lookup` had no `OFLAG_ZERO` branch either) inherited
by map for consistency.

**Fixed** as step 7z of PLAN-qcow2-write-infrastructure
(alongside the chain-reader fix for
[#432](https://github.com/shakenfist/instar/issues/432)):
`classify_qcow2_l2_standard` now reports a zero-flag standard
entry as `ZeroAllocated` for both `host_offset == 0` and
`host_offset != 0`, and `cluster_lookup` gained a matching
`ClusterLookup::Zero` verdict. Extended-L2 subcluster-bitmap
`ZeroAllocated` reporting was always correct — instar walks
the bitmap and classifies subclusters directly.

### qcow2 compressed clusters report `compressed: false`

`qemu-img map` emits `compressed: true` for extents that
back compressed-cluster L2 entries. instar's qcow2
walker classifies compressed clusters as `Data` with the
compressed-payload file offset, but does not carry the
compressed bit through the FFI / protobuf path. The renderer emits `compressed: false` for every extent
unconditionally. Extending `MapExtentRecord` and
`MapExtentMessage` with a `compressed: bool` field is
tracked as future work; once landed, the differential
fuzzer will catch any remaining divergence on
compressed-cluster sources.

### Trailing newline after JSON `]`

`qemu-img map --output=json` emits a single trailing
newline after the closing `]`. instar matches this
byte-for-byte. (An earlier draft of this document
incorrectly stated "no trailing newline" based on a
misread of `cat -A` output — `cat -A` places the `$`
end-of-line marker *before* each newline, including the
trailing one, which made the trailing newline easy to
miss in spot-check verification. The full baseline
sweep surfaced the discrepancy and corrected the
renderer.)

### Partial output on guest failure

The renderer writes the human header (or the JSON `[`)
before any `MapExtentMessage` arrives. If the guest fails
to start, or reports an error code mid-stream, the user
sees a partial table or an unclosed JSON array on stdout
plus a clear stderr message and a non-zero exit code.
JSON consumers should always check the process exit code
before parsing stdout. The trade-off keeps the streaming
path clean — the alternative (buffer everything host-side
until the success path is known) defeats the streaming
memory bound.

### Window filter is byte-level, not cluster-aligned

`qemu-img map --start-offset=N --max-length=M` silently
clamps `--start-offset` to a cluster boundary on output
(the extent containing the offset is emitted in full
starting from the cluster boundary). instar's
`clip_to_window` operates at the byte level, which can
produce a leading partial extent that qemu-img would not.
Functionally equivalent for downstream consumers that
care about byte ranges; visually different in human
output.

## create subcommand quirks

### Raw `create` runs entirely host-side

`instar create -f raw` opens the output file with
`O_CREAT|O_TRUNC|O_RDWR`, calls `ftruncate(virtual_size)`, and
optionally applies `posix_fallocate` (`--preallocation falloc`)
or zero-fills via `fallocate(FALLOC_FL_ZERO_RANGE)` with a
`pwrite` fallback (`--preallocation full`). No KVM guest is
launched — raw has no metadata to emit, so the single-code-path
principle yields to a pure host-side shortcut. Every other target
format runs `create.bin` in the sandbox. See open question 6 in
`docs/plans/PLAN-create.md` for the design rationale.

### Backing-file path is written verbatim

The user-typed `-b BACKING` argument lands in the new image's
metadata verbatim — relative paths stay relative, absolute paths
stay absolute. The host resolves the path **relative to the new
image's directory** when opening the backing file for the
parser, so the resulting reference is portable across moves of
the parent. Matches qemu-img exactly.

### Backing-file format inference requires `-F` or `-u`

`instar create -b BACKING ...` requires either `-F BACKING_FMT`
(explicit format hint) or `-u` (unsafe; assume raw). Newer
qemu-img versions enforce the same rule. The hint is the
initial format guess; if the backing file's first sector
contradicts the hint via its magic bytes, auto-detection wins
and the metadata records the detected format. Three-level
chains record only the immediate parent — instar does not
recurse, matching qemu-img.

### Preallocation accept set

| Mode | raw | qcow2 | vmdk / vpc / vhdx |
|------|-----|-------|-------------------|
| `off` | yes | yes | yes |
| `metadata` | rejected | yes | rejected (future work) |
| `falloc` | yes | yes | rejected (future work) |
| `full` | yes | yes | rejected (future work) |

`raw + metadata` is rejected because raw has no metadata to
preallocate. Non-qcow2 sparse formats reject non-`off`
preallocation with a "future work" pointer — each format needs
its own BAT-population pattern plus the same host
`apply_preallocation` post-pass qcow2 already uses.

### VHD `virtual_size` diverges from qemu-img by CHS rounding

`qemu-img create -f vpc` rounds the requested `virtual_size` up
to the next CHS-aligned multiple (legacy VHD geometry layout);
`instar create -f vpc` emits exact bytes. The divergence is
typically < 256 KiB across the supported size range. Both files
are valid VHDs — the difference surfaces only in the
`virtual-size` field reported by `qemu-img info`. The `tests/test_create.py::KNOWN_WRITER_DIVERGENCES` skips every
affected case; closing this gap is documented future work.

### qcow2 `compat=0.10` is silently upgraded to `1.1`

The writer hardcodes `compat=1.1` in the header. qemu-img
honours `compat=0.10` for compatibility with pre-3.0 qemu
releases. instar always emits the v3 header. Future work.

### qcow2 `compression_type=zstd` is accept-ignored

The `-o compression_type=zstd` option is accepted at parse
time but the header records `zlib` regardless. A fresh image
has no compressed cluster data so the field-only divergence
has no functional impact — the discrepancy only surfaces in
`qemu-img info`'s `format-specific.data.compression-type`
field. Future work: drop the accept-ignore and emit the zstd
header bit so a subsequent convert / write into the image can
emit zstd-compressed clusters.

### vhdx default `block_size` differs from qemu-img

At virtual sizes ≤ 1 GiB, `instar create -f vhdx` defaults to
an 8 MiB block size; `qemu-img create -f vhdx` always defaults
to 32 MiB. Specifying `-o block_size=...` (or `--block-size`)
explicitly avoids the divergence — the matrix
demonstrates clean round-trip for explicit block-size cases
(`1G-block-16M`, `1G-block-32M`). Future work is to match
qemu's 32 MiB default at all virtual sizes.

### VHD fixed subformat carries footer-only metadata

`instar create -f vpc -o subformat=fixed FILENAME SIZE` produces
a file of `SIZE + 512` bytes — zero data plus a 512-byte footer
at end-of-file. The footer is the only metadata.
`qemu-img info` without an explicit `-f` flag auto-detects the
file as `format=raw` because the leading bytes carry no magic;
pass `-f vpc` explicitly to surface the vhd format. This is
qemu's native behaviour and not a bug in either tool. The baselines were recorded without `-f`, so the matrix
comparison naturally agrees on both sides.

## resize subcommand quirks

### Raw `resize` runs entirely host-side

`instar resize -f raw` opens the file `O_RDWR`, calls
`ftruncate(new_virtual_size)`, and optionally applies
`posix_fallocate` (`--preallocation falloc`) or zero-fills via
`fallocate(FALLOC_FL_ZERO_RANGE)` with a `pwrite` fallback
(`--preallocation full`) over the newly-added byte range.
No KVM guest is launched — raw has no metadata to mutate.
Every other target format runs `resize.bin` in the sandbox.
Same shortcut and rationale as `create`.

### qemu-img cannot resize vmdk / vpc / vhdx on any shipped version

`qemu-img resize -f vpc|vmdk|vhdx ...` rejects with
`qemu-img: Image format driver does not support resize` on
every qemu-img version from 6.0.0 through 10.2.0 (which the
matrix exercises). instar resizes all three. The baselines record qemu's rejection verbatim, both as
documentation of the cross-tool gap and as a tripwire for the
day qemu adds support. The `TestResizeConsistency`
covers vmdk/vhd/vhdx via an `instar create → resize → info →
check` round-trip rather than a cross-tool diff. If
`vmdkinfo` / `vhdiinfo` (libyal) ever gain resize support,
the differential surface gets a third axis.

### Preallocation covers only the appended file region

For `--preallocation=falloc|full` on grow, instar preallocates
only `[file_size_before, file_size_after)` — the bytes the
planner physically appended past the pre-resize EOF.
`qemu-img resize` preallocates the entire data region of the
new virtual size (i.e. every cluster / block the resized
image's metadata can address). Both behaviours satisfy the
"reserve disk blocks" intent, but they're not identical: a
qemu-resized 1 GiB qcow2 with `--preallocation=full` writes
~1 GiB of zeros to disk; an instar-resized one writes only
the new L1 region. This is a deliberate divergence — closing
it requires per-format walk-and-populate logic comparable to
a `dd if=/dev/zero` over the data region. Documented in
[docs/plans/PLAN-resize-phase-09-preallocation.md](/components/instar/
plans/PLAN-resize-phase-09-preallocation/) and queued under
PLAN-resize.md's Future-work section.

### `--preallocation=falloc|full` + `--shrink` is rejected

instar rejects the combination outright with
`resize: --preallocation=<mode> is meaningless when shrinking`.
qemu silently accepts the combination and discards the
preallocation flag (the shrink still happens; the prealloc is
a no-op). The deliberate divergence makes the user's
intent explicit when they pass conflicting flags. The `TestResizeErrorPaths` pins the rejection message.

### `--preallocation=metadata` on raw is rejected

instar rejects with `resize: --preallocation=metadata is not
supported for raw`. qemu accepts the flag and silently
no-ops (raw has no metadata to populate, so the operation
degrades to a plain `ftruncate`). Same rationale as the
shrink-+-prealloc rejection: explicit-reject for clarity.

### qcow2 `--preallocation=metadata` is rejected by the planner

The qcow2 grow planner returns
`ResizeError::PreallocationUnsupported` for `metadata` mode
(`resize: guest reported error 8: preallocation mode not
supported by this format`). qemu supports it. The planner gap
was deferred; the integration matrix carries it
in `KNOWN_RESIZE_DIVERGENCES` and the differential fuzz
picker filters the case so it doesn't show up as a finding.
Closing the gap requires the same `Qcow2Layout` extension
work that ships in create's metadata mode, adapted for the
grow path. Future work.

### VHD CHS-rounded `virtual_size` carries forward through resize

The create-time CHS-rounding divergence (qemu rounds
virtual_size up to the next CHS-aligned multiple; instar
emits exact bytes — see `create subcommand quirks` above)
persists across resize. The resize planner preserves whatever
the create writer chose, so an `instar create -f vpc` →
`instar resize -f vpc` round-trip stays internally
consistent; an `instar resize` against a qemu-created VHD
preserves the qemu CHS-rounded size in the output. The `TestResizeConsistency` for vhd uses a `>= expected_final_size`
assertion (rather than equality) to accommodate any future
CHS-rounding alignment in the resize writer.

### `Image resized.` output matches qemu byte-for-byte

`instar resize` emits the literal string `Image resized.`
(followed by a newline) on success in human mode — identical
to qemu-img's output. `-q` suppresses it. `--output=json`
swaps in a structured envelope (filename, format, action,
old/new virtual size, new file size) and ignores `-q`.

### qcow2 overlays with a backing file are rejected up-front

`instar resize` of a qcow2 image whose header carries a
`backing_file_offset` / `backing_file_size` rejects with
`resize: qcow2 images with a backing file are not yet
supported (resize would orphan the backing reference);
resize the base image directly or flatten via
`instar convert` first`. The qcow2 resize planners do not
yet thread the existing backing reference through the
header-rewrite path, so without this guard the rewritten
header would have `backing_file_offset = 0` and the overlay
would lose its parent. The rejection mirrors VHDX's
`has_parent` guard. Lifting it is queued under PLAN-resize.md
Future work — see the "Planner gaps" section.

### Same file is exposed as input device 0 and output device 1

The resize guest binary reads via `read_output_sector` and writes via `write_output_sector`, both
dispatching to the output device at MMIO slot 1. The core
init unconditionally probes input device 0; the host
satisfies the probe with a 1-sector tempfile stub that the
resize op never reads, then attaches the real read-write
output backing at slot 1. Mirrors the same pattern
`run_create_nonraw` uses for the same reason. The first
phase-11 integration run surfaced this contract: an earlier
revision attached the output at slot 0, which broke the
guest's `init stage=probe device=output address=0x10001000`
walk. Caught and fixed during development.

### qcow2 grow has no image-size ceiling; qcow2 shrink does

After followup-01, qcow2 *grow* is bounded only by what the
filesystem can hold — the guest's targeted pre-pass stages a
small bounded set of refcount blocks (≤ 16) regardless of
image size. Tested end-to-end through 1 TiB → 2 TiB in 163 ms.

qcow2 *shrink* still uses the older "stage every non-zero
refcount block" pre-pass and so retains a per-cluster-size
ceiling: 4 MiB of `EXISTING_STATE` divided by `cluster_size`
gives the maximum number of refcount blocks stage-able, each
covering `cluster_size² / 2` bytes of file. At the default
64 KiB cluster the ceiling is ~128 GiB; at 4 KiB it's ~8 GiB;
at 1 MiB it's ~512 TiB (no practical limit). Lifting it
requires a two-phase shrink pre-pass that walks the L2 tables
first to identify which clusters are discarded, then stages
only the refcount blocks containing those clusters; queued
under PLAN-resize.md Future-work as a separate followup.

Raw / vmdk / vpc / vhdx grow and shrink have no analogous
metadata-staging step and are bounded only by filesystem
capacity.

## rebase subcommand quirks

### Unsafe (`-u`) is byte-equivalent across qemu-img versions

The post-rebase `qemu-img info --output=json` for `instar
rebase -u` matches `qemu-img rebase -u` byte-for-byte across
qemu-img 6.0.0 through 10.2.0 after the
`KNOWN_REBASE_DIVERGENCES` whitelist
(`tests/helpers/info_json.py`). Cross-version coverage:
`tests/test_rebase.py:TestRebaseBaselineMatrix`.

### qemu-img cannot rebase vmdk / vhd / vhdx on any shipped version

`qemu-img rebase -f vpc|vmdk|vhdx ...` rejects with
`qemu-img: Image format driver does not support rebase` on
every version 6.0.0 through 10.2.0. instar rebase
unsafe-mode supports vmdk monolithicSparse; the
post-rebase descriptor records the new
`parentFileNameHint` via the cross-tool comparison in
`TestRebaseSuccessPaths`. Cross-version baselines cover
qcow2 only — there is nothing to record for the
instar-only targets.

### Safe-mode rebase for vmdk is not yet supported

instar's safe-mode planner refuses vmdk with
`ERROR_UNSUPPORTED_FORMAT`; qemu-img refuses vmdk rebase
entirely. Lifting the gap (cluster comparison loop +
descriptor rewrite atomicity for vmdk grain tables) is
tracked under PLAN-rebase-commit Future work.

### Long-path relocation is rejected

If the new backing-file path is longer than the overlay's
existing slot (qcow2 `backing_file_size` field), instar
refuses with `ERROR_BACKING_PATH_TOO_LONG`. qemu-img
silently relocates the path string to a fresh cluster and
updates the header offset. Lifting the gap (planner +
guest scratch budget for the appended path cluster) is
tracked under PLAN-rebase-commit Future work; until then,
keep the new backing path's length ≤ the overlay's
existing slot.

### Cross-cluster-size rebase is rejected

Safe-mode rebase requires the old and new backings to
share a cluster size. If they differ, instar refuses with
`ERROR_NEW_BACKING_INCOMPATIBLE`. qemu-img silently
succeeds but the resulting overlay has inconsistent
metadata; the master plan tracks this as a future
hardening item. Use unsafe-mode rebase (`-u`) when the
caller knows the new backing's data is bit-identical to
the old.

### Safe-mode rebase copy-on-writes snapshot-bearing overlays

Since the phase-7 copy-on-write work (issue #421 resolved),
safe-mode rebase of an overlay that carries internal
snapshots no longer refuses — it succeeds by copying. Where
the safe-mode allocator would previously have mutated a
snapshot-shared active L2 table in place and under-counted
refcounts (`refcount=1 reference=2` on the newly-allocated
clusters, enabling data loss via a later `snapshot -d`,
issue #421), it now COWs the shared L2 (copy `T → T'`,
repoint the L1, `rc(T')=1`, `rc(T)`−1) so no live cluster is
ever left with a refcount below its reference count.

The load-bearing subtlety is the **snapshot-view semantic**,
matching qemu's contract exactly: `qemu-img rebase` covers
the **active view only** and leaves internal snapshots
untouched, so after the rebase a snapshot's unallocated
ranges silently **read through the NEW backing** rather than
staying at their pre-rebase content. instar reproduces that
read-through-new-backing semantic; the snapshot read-back
oracle asserts each snapshot resolves to qemu's result for
the same op, not to a frozen pre-rebase baseline. The proof
is qemu-parity — `qemu-img check` clean + active-view
`qemu-img compare`-identical to a qemu twin + the snapshot
read-back oracle — never image-byte identity (qemu's own COW
placement is nondeterministic at 512-byte clusters). See
`tests/test_rebase.py:TestRebaseSnapshotGate`,
`tests/test_cow_cross_version.py` and the cross-cutting
"Copy-on-write for snapshot-bearing qcow2 images" section
below.

Unsafe (`-u`) rebase is unchanged: it only rewrites the
header backing-pointer region, which is never
snapshot-shared, and stays parity-tested against qemu-img.

**Growth sizing (known limitation).** rebase's COW gates
refcount growth on `nb_snapshots > 0` and sizes it at
`2 × overlay_cluster_count` — coarser than commit's
allocated-cluster bound, because rebase writes into clusters
it does not own and cannot cheaply bound the allocation
ahead of the walk. The over-provisioned refblocks are
check-clean (they carry the #433 materialization fix), so
this is a sizing conservatism, not a correctness gap; a
tighter bound is recorded follow-up work.

### Overlays with extended L2 entries or unknown/compression feature bits are refused

Since the phase-5 migration onto `crates/qcow2-write`,
safe-mode rebase (including safe detach) refuses an overlay
whose header carries the extended-L2 incompatible bit, the
zstd compression-type bit, or any unknown
incompatible-features bit (`RebaseResult` error 15): ``the
overlay uses features instar rebase does not support
(extended L2 entries, or unknown/compression feature bits).
Use -u for a metadata-only rebase or fall back to `qemu-img
rebase` ``.

The extended-L2 half is a live-defect fix: previously
the safe-mode walk misread the 16-byte extended-L2 entries
as 8-byte classic entries and silently corrupted the
overlay's virtual content — exit 0, damage visible only on
read-back (issue
[#431](https://github.com/shakenfist/instar/issues/431),
identified during the PLAN-qcow2-write-infrastructure work). The
zstd/unknown-bit half is spec-mandated (the qcow2 spec
requires refusing unknown incompatible bits) and is a
narrowing: the zstd bit is inert when the image contains no
compressed clusters, so such images rebased correctly
previously and now refuse — the same posture as
commit's error 16. The refusal fires before any staging or
mutation; `-u` metadata-only rebase only rewrites
header/path bytes and stays allowed.

### Overlays with inconsistent metadata are refused

Safe-mode rebase refuses overlays whose metadata is
inconsistent as a write substrate (`RebaseResult` error
16): ``the overlay's metadata is inconsistent (refcounts,
table flags or layout); refusing to write into it. Run
`qemu-img check` on the overlay, or fall back to `qemu-img
rebase` ``. This covers a sparse (holed) refcount table,
reserved bits in refcount-table/L1/L2 entries, and
qcow2-write classification refusals (snapshot-shared or
refcount-inconsistent clusters on an image whose header
says it has no snapshots).

The sparse-refcount-table shape matters, exactly as it did
for commit's backing side
([#428](https://github.com/shakenfist/instar/issues/428)):
it is stock-producible (a discard history followed by
`qemu-img resize --shrink` frees all-zero refblocks below
still-populated ones), passes `qemu-img check` cleanly, and
previously rebase's staging compacted the nonzero table
entries and indexed them as if dense — silently writing
refcounts into the wrong refblocks (1092 check errors plus
32 leaked clusters at exit 0 in the probe that found it;
the overlay-side rebase sibling of #428; issue
[#430](https://github.com/shakenfist/instar/issues/430),
identified during the PLAN-qcow2-write-infrastructure work). qemu-img rebases the same
shape check-clean. The
refusal fires at staging time, before any mutation, and is
byte-idempotent
(`tests/test_rebase.py:TestRebaseOverlayClassification`).

### Overlay staging capacity widened by the write-infrastructure migration

The migrated safe mode retires the stage-everything model
for existing L2 tables (and with it the growable L2 arena
and its count caps — the #422 hazard class of the arena
clobbering refblock staging is gone by construction).
Overlays whose populated-L2 count previously refused
`ERROR_SCRATCH_TOO_SMALL` at staging time — even when
nothing needed copying — now rebase: the probe exemplar
(cs=512, 64 MiB overlay, 512 populated L2 tables, identical
chains) refused previously and now succeeds check-clean
with qemu-img parity. The L2 window is
`min(256, 2 MiB / cluster_size)` slots with reachable (and
safe) eviction; refblock staging is byte-capacity-driven at
`min(2048, 3 MiB / cluster_size)` refblocks (formerly a
joint 4 MiB bump arena shared with L2 staging); the
refcount table stages as a bounded prefix (the planner
reads only the entries covering the staged refblocks), so
large-cluster refcount tables no longer bound the run. The
remaining ceiling is refcount exhaustion (`RebaseResult`
error 10 — v1 never appends new refblocks); retiring it is
the master plan's refcount-growth generalization.

### Beyond-EOV tail bytes of copied clusters are zeros

When the old backing chain is LARGER than the overlay's
virtual size and the tail cluster diverges, safe-mode
rebase copies the tail cluster with bytes beyond
end-of-virtual-size zero-filled. Both pre-phase-5 instar
and `qemu-img rebase` instead carry the old chain's
beyond-EOV bytes into the overlay's raw file. Virtual
content is identical either way (bytes past EOV are not
virtual content, and no tool reads them back); this is the
one sanctioned raw-level divergence from the phase-5
migration proof (divergence D9 — the only non-byte-identical
row in the 69-combo matrix, isolated to this shape by its
byte-identical plain-unaligned twin).

### Compressed chain members still refuse where qemu succeeds

A compressed cluster in an old-chain member surfaces
`ERROR_PARSE_FAILED` (error 12) mid-loop — the rebase
binary does not enable the decompress feature — where
`qemu-img rebase` succeeds. Pre-existing divergence,
unchanged by the phase-5 migration (lifting it means
enabling decompression in the rebase binary, a size and
scope question tracked as future work). Compressed entries
in the OVERLAY itself are skipped, before and subsequently: the skip probe treats any non-zero L2 entry as
mapped.

### The chain reader honours the zero flag on classic L2 entries (fixed)

Historically `cluster_lookup`'s classic (non-extended-L2)
arm in `crates/qcow2` ignored bit 0 (`QCOW_OFLAG_ZERO`) of
v3 standard L2 entries, so a zero-flag cluster (e.g. from
`qemu-io write -z`) in a chain member read as fall-through
to the backing (`host_offset == 0`) or as stale data bytes
(`host_offset != 0`) instead of zeros — silent active-view
corruption. Blast radius: every consumer of the chain
reader — rebase, convert, compare and bench. Pre-existing
`crates/qcow2` defect
([#432](https://github.com/shakenfist/instar/issues/432)),
identified during the PLAN-qcow2-write-infrastructure work and explicitly NOT fixed by
it.

**Fixed** as step 7z of that plan (the standalone read-path
fix landed before any COW work): `cluster_lookup` now
returns a dedicated `ClusterLookup::Zero` verdict whenever
bit 0 is set on a classic entry — for both `host_offset ==
0` and `host_offset != 0` — and `read_chain_virtual_cluster`
zero-fills that cluster without falling through to a backing
layer or reading the host offset. The map subcommand's
sibling classifier `classify_qcow2_l2_standard` was fixed in
the same change (see "qcow2 v3 standard-L2 `QCOW_OFLAG_ZERO`
honoured" above). Differential matrices no longer need to
avoid `write -z` seeds.

### Deep-allocation safe rebase refuses on refcount exhaustion instead of hanging

Issue #422's apparent 512-byte-cluster livelock was a guest
panic spinning in the panic handler, fixed during the PLAN-qcow2-write-infrastructure work (the staged-L2 lookup slice
went stale after arena growth; the growth arena could also
clobber the refblock staging regions). Safe-mode rebases
that allocate deeply no longer hang: shapes that exceed the
overlay's existing refcount-block capacity now terminate
promptly with ``the overlay's refcount blocks are full; v1
doesn't append new ones. Fall back to -u or use `qemu-img
rebase` `` — qemu-img completes these (it grows the refcount
table). Retiring that capacity ceiling is the master plan's
refcount-growth generalization. Note the exhaustion refusal
is not byte-idempotent (semantically-inert data clusters
are written before the guest refuses; the image stays
check-clean); making envelope refusals mutation-free is
folded into the ordering contract.

### `Image rebased.` / `Image detached.` output matches qemu byte-for-byte

instar emits the same trailing-newline-terminated strings
as `qemu-img rebase`. `--output=json` adds a structured
envelope unique to instar (see
[docs/rebase.md](/components/instar/rebase/)).

## commit subcommand quirks

### Implicit `-b` matches the overlay's recorded parent

`instar commit FILENAME` (no `-b`) reads the overlay's
recorded backing-file pointer and uses it as the commit
target. Matches `qemu-img commit`'s implicit-`-b`
semantics. v1 supports only the overlay's immediate
parent; if `-b BASE` is supplied and resolves to a
different file than the recorded parent, instar refuses
with `commit through an intermediate layer is not yet
supported`.

### qemu-img cannot commit vhd / vhdx / raw

qemu-img commit accepts qcow2 and vmdk monolithicSparse —
the only formats with backing-chain support — and
refuses every other format. instar matches that surface.

### vmdk implicit-`-b` is blocked by a host info gap

The host info operation doesn't currently surface vmdk
monolithicSparse's `parentFileNameHint` via the
`backing_file` field, so the host's `-b`-against-
recorded-parent check refuses every vmdk commit without
an explicit `-b`. The matrix and round-trip vmdk
cases all pass an explicit `-b base.vmdk`. Tracked
separately under PLAN-info's vmdk follow-ups; once the
info-side gap lifts, the implicit form will work too.

### Cluster-size mismatch is refused up-front

If the overlay and backing have different qcow2 cluster
sizes, the host pre-check refuses with `commit between
mismatched cluster sizes is not yet supported`. qemu-img
silently succeeds with limited efficiency. Lifting the
gap requires cluster-size adapters in the planner's
per-cluster loop.

### Cross-format commit is refused

qcow2 → qcow2 and vmdk → vmdk only. Cross-format commit
(e.g. qcow2 overlay onto a vmdk backing) is refused with
`ERROR_UNSUPPORTED_FORMAT`. Lifting needs planner
extensions plus a cluster-size translation layer.

### Snapshot-bearing images copy-on-write (backing preserved)

Since the phase-7 copy-on-write work (issues #420 and #423
resolved), `instar commit` succeeds on snapshot-bearing
images by copying instead of refusing. The phase-2 interim
refusal gates (backing-side error 14, overlay-side error 15)
are lifted.

- **Backing side** (was issue #420): where the per-cluster
  loop previously blind-overwrote snapshot-shared backing
  clusters, commit now COWs them (copy the shared data
  cluster `D → D'`, repoint the L2, `rc(D')=1`, `rc(D)`−1;
  `D` is never freed because the snapshot still holds it).
  Every pre-existing backing snapshot is **preserved
  bit-identically** — its read-back after the commit equals
  its pre-commit content, matching qemu, which COWs and
  preserves on every version tested.
- **Overlay side** (was issue #423): the post-commit
  overlay-clear pass — which zeroes the overlay's active L2
  and refcount entries in place — is **skipped when the
  overlay has internal snapshots**, because zeroing shared
  active metadata in place is exactly the corruption #423
  described. The overlay is left byte-unchanged, its
  snapshots preserved, and its active view stays
  `qemu-img compare`-identical to qemu (the committed
  clusters now resolve identically through the new backing).

The proof is qemu-parity, never image-byte identity:
`qemu-img check` clean + active-view compare-identical to a
qemu twin + the snapshot read-back oracle asserting each
backing snapshot equals its pre-commit content. See
`tests/test_commit.py:TestCommitSnapshotGate` and
`tests/test_cow_cross_version.py`.

**Known limitation (documented non-emptying).** Because the
overlay-clear pass is skipped for snapshot-bearing overlays,
the overlay is not byte-emptied the way a snapshot-free
commit empties it — the committed clusters remain mapped in
the overlay's active L2 (reading identically through the new
backing) rather than being zeroed out. Full byte-emptying
parity would need an overlay-side COW-clear primitive that
copies the shared active metadata before zeroing it; that is
recorded follow-up work. The active view and every snapshot
are correct either way.

### Backings with unknown or compression feature bits are refused

Since the phase-4 migration onto `crates/qcow2-write`,
commit refuses a backing whose header carries the zstd
compression-type bit or any unknown incompatible-features
bit (`CommitResult` error 16): ``the backing file uses
features instar commit does not support (unknown or
compression feature bits). Fall back to `qemu-img
commit` ``. The qcow2 spec mandates refusing unknown
incompatible bits; commit previously proceeded in
violation of the spec. The refusal fires before any
staging or mutation. qemu-img builds with zstd support
proceed on the zstd shape; instar defers zstd to the
compressed-write future work.

### Compressed backing clusters are refused

Commit refuses when the committed extent lands on a
compressed L2 entry in the backing, using the existing
`ERROR_UNSUPPORTED_FORMAT` code (the same code the
overlay side has always used for compressed entries).
Before this shape was silently corrupted: the
per-cluster loop masked the compressed entry's offset and
overwrote it in place, destroying the deflate streams of
every virtual cluster packed into that host cluster —
exit 0, `qemu-img check` clean, damage visible only on
read-back (issue
[#427](https://github.com/shakenfist/instar/issues/427),
identified during the PLAN-qcow2-write-infrastructure work). qemu-img handles the
same shape correctly: it allocates a fresh uncompressed
cluster and leaves the other packed streams intact. The
refusal is a classification refusal: clusters committed
earlier in the same run remain written (unreferenced
scaffolding, metadata never flushed, check-clean — the
same posture as the refcount-exhaustion refusal).

### Backings with inconsistent metadata are refused

Commit refuses backings whose metadata is inconsistent as
a write substrate (`CommitResult` error 17): ``the backing
file's metadata is inconsistent (refcounts, table flags or
layout); refusing to write into it. Run `qemu-img check`
on the backing, or fall back to `qemu-img commit` ``.
This covers a sparse (holed) refcount table, reserved bits
in refcount-table/L1/L2 entries, and snapshot-shared or
refcount-inconsistent clusters on an image whose header
says it has no snapshots.

The sparse-refcount-table shape matters: it is producible
with stock qemu-img operations (a discard history followed
by `qemu-img resize --shrink` frees all-zero refblocks
below still-populated ones) and passes `qemu-img check`
cleanly, and previously instar's staging compacted the
nonzero table entries and indexed them as if dense —
silently writing refcounts into the wrong refblocks (2654
check errors in the probe that found it; issue
[#428](https://github.com/shakenfist/instar/issues/428),
identified during the PLAN-qcow2-write-infrastructure work). qemu-img
commits into the same shape check-clean. The sparse-table
refusal fires at staging time, before any mutation; the
other error-17 shapes are classification refusals with the
same scaffolding posture as the compressed-cluster refusal
above.

### Backing staging capacity widened by the write-infrastructure migration

The migrated backing side stages refblocks by byte
capacity — `min(2048, 3 MiB / cluster_size)` refblocks,
strictly wider than the old 32-refblock cap on every
cluster size — and replaces the old stage-everything
backing-L2 cap (`min(256, 2 MiB / cluster_size)` tables)
with a windowed model of the same slot count that has no
total-count refusal at all. Strictly more images succeed;
backing shapes that previously refused
`ERROR_SCRATCH_TOO_SMALL` (e.g. a cs=512 backing with more
than 32 populated refcount-table entries) now commit with
qemu-img info/check parity. Overlay-side staging caps are
unchanged, so overlay-bound shapes refuse exactly as
before. The remaining backing-side ceiling is refcount
exhaustion (`CommitResult` error 11 — v1 never appends new
refblocks); retiring it is the master plan's
refcount-growth generalization.

### Unaligned virtual sizes commit cleanly

Images whose `virtual_size` is not a multiple of the
cluster size commit byte-identically to qemu-img's
observed tail behaviour, including when the backing has
its own backing file. The final (tail) cluster's write is
clamped to `virtual_size` and classifies as full coverage
in `crates/qcow2-write` — bytes beyond end-of-virtual-size
are not virtual content, so the beyond-EOV zero-fill is
the correct pre-image regardless of backing. Probed
empirically: tail bytes past EOV are zeros
under both tools on stock fixtures, and the proof matrix's
unaligned combo passed byte-identical with zero fallbacks
to virtual-content comparison.

### `cluster_size > 64 KiB` overflows the commit scratch budget

The commit guest binary's `OVERLAY_RT_LIMIT` and
`BACKING_RT_LIMIT` scratch regions are sized at
`MAX_SECTOR_SIZE` (64 KiB), so a single-cluster refcount
table for any `cluster_size > 64 KiB` overflows the
budget and returns `ERROR_SCRATCH_TOO_SMALL`. The
differential fuzzer picker
(`scripts/differential-fuzz.py:_commit_option_picker`)
caps `cluster_size` at 64 KiB to match; lifting the
guest-side limit is a master-plan TODO.

### `-d` / `-p` / `-r` / `-t` are not implemented

qemu-img commit's `-d` (drop overlay after commit), `-p`
(progress bar), `-r` (rate limit), and `-t` (cache mode)
flags are not implemented in instar v1. The user can
manually `rm` the overlay after a successful commit when
the equivalent of `-d` is needed. All four are tracked
under PLAN-rebase-commit Future work.

### `Image committed.` output matches qemu byte-for-byte

instar emits the same trailing-newline-terminated string
as `qemu-img commit`. `--output=json` adds a structured
envelope unique to instar (see
[docs/commit.md](/components/instar/commit/)).

### Same file is exposed as input device 0 and output device 1

Commit's two-device layout has the overlay attached at
input slot 0 opened RW (so the guest's overlay-clear
pass can write through `write_input_sector(0, ...)`)
and the backing attached as the output device opened
RW. The backing's own ancestor chain occupies input
slots [1..N) read-only — v1 doesn't consult them, but
the slots are populated so the future "skip when chain
provides this data" mode (see
[PLAN-rebase-commit-phase-08-commit-host.md](/components/instar/
plans/PLAN-rebase-commit-phase-08-commit-host/)) can
plug in without an ABI change.

## bench subcommand quirks

Since the phase-6 migration (PLAN-qcow2-write-infrastructure),
`instar bench -w` on a qcow2 image runs its allocate-on-write
path on the shared `crates/qcow2-write` planner and
`crates/qcow2-write-exec` executor — bench is the third
consumer after commit and rebase. The read
path, raw `-w`, and the vmdk/vhd/vhdx read support are
untouched. The quirks below record how the migration changed
`-w` behaviour. bench's own oracle is `qemu-img compare` +
`qemu-img check` (not byte identity), so unlike commit and
rebase the migration deliberately relaxes byte parity for
allocating schedules; the rest is behaviour-preserving.

### Allocating writes no longer produce byte-identical images

For a `-w` schedule that allocates (a write to an unallocated
cluster), the post-run qcow2 image is **not** byte-identical to
what pre-migration bench produced, nor to `qemu-img bench -w`.
Pre-migration bench allocated the data cluster first and a
fresh L2 table second; the shared planner allocates the L2
table first (its proven order, shared with commit and rebase).
Under the single linear allocation cursor the two host offsets
swap for every fresh-L2 write, so the physical layout differs.
The images are still equivalent: `qemu-img compare` reports
identical virtual content and `qemu-img check` is clean. This
is sound because bench's `-w` oracle has always been
compare + check, never a byte hash — bench was never a
byte-parity consumer. Overwrite-only schedules allocate
nothing, so their output stays byte-identical across the
migration.

### New refusal code 9 for classification-inconsistent images

The migration appends one wire code,
`BenchResult::ERROR_IMAGE_INCONSISTENT = 9`, rendered as
`bench: image metadata is inconsistent`. It carries the
planner's classification refusals that had no existing bench
rendering:
unknown/reserved L1 or L2 entry bit patterns, refcount
inconsistencies, refcount-coverage gaps, and a staged-regions
mismatch. `RefcountExhausted` keeps `ERROR_ALLOC_EXHAUSTED = 8`
(``image too large for in-place bench write``); a mid-run
compressed cluster keeps the gate-2 `ERROR_WRITE_UNSUPPORTED`
rendering; snapshot-shared clusters keep the gate-7 rendering
(bench's defensive posture — the image is already gated on
`nb_snapshots > 0` at setup). These refusals are narrower than
pre-migration bench, which blind-allocated over exotic entries.

### The contiguity gate keeps `ERROR_PARSE_FAILED` (code 3)

The staging-time refcount-table contiguity gate (a sparse /
holed refcount table refuses before any mutation) keeps
returning bench's existing `ERROR_PARSE_FAILED = 3`, not the
new code 9. It refuses identically to pre-migration bench, so
the pure-refactor bar wins over cosmetically unifying it with
commit's (error 17) and rebase's (error 16) equivalents. The
refusal is pre-mutation and byte-idempotent.

### Zero-flag L2 entries: target-side refused, backing-side fixed

A qcow2 v3 zero-flag (`QCOW_OFLAG_ZERO`) on the L2 entry bench
is about to overwrite refuses with code 9 (Variant A) —
pre-migration bench blind-allocated over it and chain-filled a
pre-image the reader mis-handled. A zero flag in a **backing**
cluster reached through the COW read (Variant B) was, at, still mis-filled: a read-path defect in `crates/qcow2`'s
`cluster_lookup`
([#432](https://github.com/shakenfist/instar/issues/432)), not
since fixed.

**Fixed** in step 7z (the standalone read-path fix landed
before the COW work): `cluster_lookup` now returns
`ClusterLookup::Zero` for a classic zero-flag entry and the
chain reader zero-fills it, so Variant B no longer corrupts.
Variant A's code-9 refusal is unchanged.

### Flush and durability posture (fsync census preserved)

The migration preserves bench's fsync census exactly, with no
change to the shared crate. All `plan_flush` calls run through
a fsync-DISABLED `CallTableIo` (`CallTableIo::new(ct, false)`),
so the executor never fsyncs; at each count-based
`--flush-interval` cadence point the op drives a full flush
epoch and then issues exactly one `fsync_input(0)` itself — as
pre-migration bench did. `flushes-issued` (`--output json`)
counts cadence points only, never growth fsyncs; it is zero at
end-of-bracket and for `--flush-interval 0`. Setup-time
refcount growth keeps its own 1-2 fsyncs (1 in-place, 2 on a
refcount-table relocation), which are not counted in
`flushes-issued`. Because bench's image is input slot 0 opened
RW, `fsync_input(0)` genuinely syncs here (unlike commit and
rebase, whose output-device writes have no fsync primitive and
rely on ordering alone). Timing character inside the bracket
is not comparable across versions by design.

### Refcount growth materializes over-provisioned refblocks (#433)

Setup-time growth now marks every newly provisioned refcount
block dirty before its eager flush, so every block the
refcount table points at is materialized on disk. Before the
fix (landed just before the migration,
[#433](https://github.com/shakenfist/instar/issues/433)), an
overwrite-dominant schedule that crossed the growth threshold
provisioned refblocks and wrote their table pointers but
allocated nothing to dirty them, so `flush_dirty_refblocks`
(which writes only dirty blocks) never materialized them and
the refcount table dangled past EOF — silent (exit 0),
`qemu-img check`-dirty on a check-clean input. The fix restores
qemu's every-RT-referenced-block-is-allocated invariant and
rides the existing growth fsync, so the census is unchanged.
One growth-side write was relocated by the migration: the
relocating old-refcount-table free is now persisted inside
growth (an extra byte-range write, no extra fsync), because the
crate's `plan_flush` writes back only its own dirty state.

## convert subcommand quirks

### `--snapshot` resolves ID-then-name over a bounded 16-entry table

`instar convert --snapshot ARG` resolves `ARG` with the same
two-full-pass matcher as `qemu-img convert -l` (qemu's
`find_snapshot_by_id_or_name`, shared with `snapshot -a`): one
full pass over the snapshot table comparing **IDs**, then — only
if no ID matched — a second full pass comparing **names**. A
later entry matching by ID beats an earlier entry matching by
name; see the `snapshot -a` matcher-asymmetry table below for
the collision example. (Before the PLAN-snapshot work, instar
returned the first per-entry id-or-name hit, which picked the
wrong snapshot on ID/name-collision images.)

**Residual divergence**: the lookup walks the bounded in-memory
table from `parse_snapshot_table`, which caps at 16 entries
(`MAX_SNAPSHOTS`). A snapshot stored beyond the first 16 table
entries is reported not-found by `instar convert --snapshot`
where `qemu-img convert -l` finds it. This is the same v1
16-snapshot cap family as the snapshot subcommand's create cap
(see "16-snapshot cap" under the `snapshot -c` quirks); raising
it is future work.

## snapshot subcommand quirks

### Bare `snapshot FILE` defaults to list mode (D2)

`qemu-img snapshot` documents `-l` as "the default" — running
`qemu-img snapshot image.qcow2` without a mode flag lists the
snapshot table and exits 0. Previously, instar's clap ArgGroup
had `required = true`, so the bare form produced a clap usage error
(exit 2). This is now fixed: the ArgGroup is `required = false`,
and `run_snapshot` routes an absent mode flag to the real list path
(`run_snapshot_list`), producing byte-identical output to the
explicit `-l` form.

### `--force-share` (`-U`) is list-only (D1)

`qemu-img` refuses `-U` combined with any mutating mode (`-c`, `-d`,
`-a`) with exit 1 and the message:

```
qemu-img: Could not open 'IMAGE': force-share=on can only be used with read-only images
```

Previously, instar accepted `-U` with mutating modes and
performed the mutation (the flag was plumbed to the guest but
unenforced at the host). A host-side gate exists in
`run_snapshot` that fires before any file access: `-U` combined with
`-c`, `-d`, or `-a` exits 1 with:

```
snapshot: --force-share (-U) can only be used with read-only operations; -l is the only sharing-safe mode
```

The message wording differs from qemu's (which mentions
`force-share=on` and "read-only images" — artefacts of qemu's
open-flags machinery). The substance is the same: refusal, exit 1,
image untouched.

`-U -l` is accepted by both tools. instar takes no image locks, so
the flag is a no-op for the read-only path; the bit is still
forwarded to the guest via `FLAG_FORCE_SHARE` but the guest likewise
ignores it.

### `-q` is a no-op for all snapshot modes

`-q` (quiet) has no visible effect on any snapshot mode under either
tool:

- `-c` (create): success is always silent (no stdout line exists to
  suppress). `-q` changes nothing.
- `-d` (delete): success is always silent. Error messages (e.g.
  "snapshot not found") are printed to stderr and are **not**
  suppressed by `-q` under either tool; both exit 1.
- `-a` (apply): success is always silent. Error messages not
  suppressed.
- `-l` (list): the snapshot table goes to stdout regardless of `-q`.

The flag is accepted for CLI compatibility and forwarded to the guest
via `FLAG_QUIET`, but the guest likewise ignores it for all modes
implemented so far. The note ("`-q` has no visible effect on
create") generalises to all four modes.

### Mixed mode flags: clap exits 2, qemu exits 1 (D3)

Supplying two or more mode flags (`-c snap -d snap`, `-l -c snap`,
etc.) is a mutually-exclusive-argument violation under both tools,
but the exit codes and messages differ:

- `qemu-img`: prints `Cannot mix '-l', '-a', '-c', '-d'`, exits 1.
- `instar`: clap detects the conflict at parse time, prints its own
  usage-error message, exits 2.

The behaviours agree in substance (refusal, non-zero exit, no image
access); the exit code and message differ cosmetically. Fighting clap
for a one-digit exit-code delta buys nothing — instar's other
subcommands already expose clap usage-error semantics — so this
divergence is documented rather than fixed.

### `DATE` column is rendered in local time

`instar snapshot -l` formats the `DATE` column using the host's
local timezone, matching `qemu-img snapshot -l`'s behaviour
(both use `strftime("%Y-%m-%d %H:%M:%S", localtime(&date_sec))`).
For deterministic output (CI runs, cross-version baselines, byte-
exact diff harnesses), set `TZ=UTC` in the environment before
invoking either tool. Without `TZ=UTC` the rendered date depends
on the operator's locale and the two tools' output will only
match when they're invoked under the same TZ.

The `--output=json` form is an instar extension; its `date`
object reports the raw `seconds` since the Unix epoch alongside
the `nanoseconds` subsecond component, so JSON consumers do not
need to round-trip the human-readable column to recover the
underlying timestamp.

### TAG / ID columns pad to a byte-measured minimum width

qemu's `qemu-img snapshot -l` renders rows with C
`printf("%-7s %-16s …")`, whose minimum field widths count
**bytes**. Rust's `{:<7}` / `{:<16}` count chars, which over-pads
multibyte UTF-8 names (`snäp-名前` is 7 chars but 12 bytes).
instar's renderer pads the ID and TAG columns by byte length so
the row layout is byte-identical to qemu's for any name. Found
by the PLAN-snapshot differential fuzzer on its first
smoke run — the fixture names were all ASCII, where
the two semantics agree.

### Inter-entry snapshot-table padding bytes may differ

Snapshot-table entries start 8-aligned, leaving up to 7 padding
bytes between an entry's unaligned end and the next entry's
start. instar serializes the whole table with zeroed gaps;
qemu's `qcow2_write_snapshots` writes each entry field-by-field
and never touches the pad bytes. On a table allocated into a
**reused** (previously freed, dirty) cluster — e.g. a create or
delete following an apply that freed data clusters — qemu's
padding therefore retains stale bytes while instar's reads zero.
Both images are valid: the padding is dead bytes no parser
reads. Unreachable in the byte-identity matrices
(their tables always landed in fresh zero clusters); found by
the differential fuzzer, whose comparator zeroes the
live table's pad bytes on both sides per step, alongside its
date normalization.

### Snapshot names are rendered raw, like qemu-img

`instar snapshot -l` writes snapshot IDs and names to stdout
byte-for-byte as stored in the image, exactly as `qemu-img
snapshot -l` does (qemu `printf`s them raw). A hostile image can
therefore embed terminal control characters (ANSI escapes,
carriage returns, newlines) in a snapshot name and have them
reach the operator's terminal — a cosmetic output-spoofing
vector, noted by the PLAN-snapshot pre-push security review and
**accepted deliberately as qemu-img parity**: sanitizing would
break the byte-identical `-l` contract the cross-version
baselines and harnesses pin. The JSON output path escapes per
the JSON spec (`"`, `\`, and C0 controls) and is the right
choice for untrusted automation. Pipe human output through
`less` or similar when listing images you do not trust.

### Zero `date_sec` renders the epoch (since fixed)

For a snapshot-table entry whose `date_sec` is 0, `instar
snapshot -l` renders the Unix epoch in local time
(`1970-01-01 00:00:00` under `TZ=UTC`), byte-identical to
`qemu-img snapshot -l`, which feeds 0 through `localtime` like
any other value. instar originally early-returned a blank
`DATE` column here; the PLAN-snapshot work resolved the
divergence in favour of parity (the project's standing
principle) and removed the early return — the `localtime_r`
path handles 0 fine, and the JSON output path carries raw
numeric date fields either way.

The input is degenerate: it is unreachable via qemu-created
images — both `qemu-img snapshot -c` and `instar snapshot -c`
always stamp the wall-clock creation time, so a zero `date_sec`
requires a hand-crafted table. The original divergence was
found by the PLAN-snapshot date-normalization probes,
which is why the differential fuzzer's comparator normalizes
`date_sec`/`date_nsec` to a fixed **nonzero** sentinel
(`0x60000000`/`0`): with the nonzero value both tools rendered
identically even before the fix, and nothing depends on the
zero case, so the sentinel stays as-is.

### `vm_state_size == 0` renders as `0 B`

qemu's `qemu-img snapshot -l` uses `size_to_str()` for the
`VM_SIZE` column, which emits the literal string `"0 B"` for a
zero `vm_state_size`. The shared `format_size_human(_, qemu_compat
= true)` helper used elsewhere in instar (e.g. `instar info`)
returns the bare string `"0"` for zero bytes, matching qemu-img's
**info** output. The snapshot renderer therefore wraps the helper
with a `0`-byte short-circuit so the `VM_SIZE` column matches the
qemu-img snapshot dump rather than the qemu-img info dump.

### Cross-version listing format: instar tracks the modern layout

`qemu-img snapshot -l` output changed format between qemu 8.x and
9.0. The cross-version baseline matrix captures exactly
two profile families:

- **Old format** (qemu 6.0.0 through 8.2.x): column headers `VM
 SIZE` and `VM CLOCK` (space-separated), clock rendered with
 2-digit hours (`00:00:00.000`).
- **New format** (qemu 9.0.0 onward): column headers `VM_SIZE` and
 `VM_CLOCK` (underscore-separated), clock rendered with 4-digit
 hours (`0000:00:00.000`), matching instar's renderer.

instar implements the new (≥9.0) format. The integration tests
compare `instar snapshot -l` output against the newest-format
profile and use the old-format profiles only to validate the
captured baselines. The raw per-version baselines for all 80
matrix versions live in
`instar-testdata/expected-outputs/snapshot-list-human/`.

### Snapshot names up to 255 bytes are listed in full

`SnapshotEntry::name` was widened from `[u8; 64]` to `[u8; 256]`
and the parser's copy cap raised from `.min(63)` to `.min(255)`.
The wire record's `name` field is 256 bytes, so no truncation
occurs for any name qemu-img can produce (qemu caps creation at
255 bytes). Fixture `snap-qcow2-longname` (200-byte name) in the baseline matrix produces byte-identical output to
`qemu-img snapshot -l`.

**Residual note**: names longer than the 256-byte wire buffer
(i.e. longer than 255 usable bytes) would still be silently
truncated at the converter. This is unreachable via `qemu-img
snapshot -c`, which caps creation at 255 bytes; instar's own
create path refuses 256+ byte names with an error.

### `snapshot -c` (create) quirks

The following apply to `instar snapshot -c NAME` (create mode,
landed during the PLAN-snapshot work).

- **Duplicate names are allowed.** Creating two snapshots with the
  same `NAME` succeeds and yields two distinct entries (IDs `1`
  and `2`, both tagged `NAME`), matching `qemu-img snapshot -c`
  exactly. There is *no* "already exists" error — that message
  belongs to QEMU's HMP `savevm`, not to `qemu-img snapshot -c`.
  (`ERROR_DUPLICATE_NAME` remains reserved in the ABI for a future
  savevm-style mode.)

- **16-snapshot cap.** instar v1 refuses to create the 17th
  snapshot (`ERROR_SNAPSHOT_TABLE_FULL`). The qcow2 spec allows up
  to 65536; raising the cap is future work. Delete a snapshot
  first, or use `qemu-img` for images that need more than 16.

- **`refcount_bits != 16` refused for mutating modes.** The v1
  cluster allocator is 16-bit-refcount only (the `qemu-img`
  default since qcow2 v3, and the only width v2 uses). Images with
  a different `refcount_order` are refused by `-c`
  (`ERROR_UNSUPPORTED_FEATURE`); list mode still works on them.

- **Create may exhaust the image's existing refblocks.** instar
  v1 allocates new clusters (the snapshot's L1 copy, the
  reallocated snapshot table) only from the refblocks already
  present in the image's refcount table — it never allocates a
  new refblock and never grows the refcount table. When no free
  run remains in the present refblocks, `-c` fails with
  `ERROR_ALLOCATION_FAILED` ("no free clusters available") and
  the image is untouched; `qemu-img snapshot -c` grows the
  refcount structures and succeeds. In practice this bites at
  small cluster sizes, where per-create allocations are many
  clusters (at `cluster_size=512` a 64M image's L1 copy alone is
  32 clusters) and each refblock covers little file range. Found
  by the differential fuzzer; its chain generator pairs
  512-byte clusters only with 4M images (the matrix
  pairing). Refcount-structure growth is future work (open question 7).

- **Dirty / corrupt images refused.** `qemu-img` auto-repairs a
  dirty lazy-refcount image when it opens it read-write; instar v1
  refuses instead (`ERROR_UNSUPPORTED_FEATURE`). Refcounts in a
  dirty image are not trustworthy, and instar will not mutate on
  top of them. Run `qemu-img check -r all` first to clear the
  dirty bit, then retry.

- **Compressed clusters refused.** Images with zstd compression
  (header bit) or any zlib-compressed cluster (detected during the
  L2 walk) are refused by the mutating modes
  (`ERROR_UNSUPPORTED_FEATURE`). Refcounting a compressed extent
  needs a multi-cluster walk deferred to future work. List mode
  works regardless.

- **External data file / encryption / dirty bitmaps refused.**
  Same `ERROR_UNSUPPORTED_FEATURE` posture as the other mutating
  modes — these features change the refcount semantics or require
  a write path instar does not yet have.

- **`-q` has no visible effect on create.** `qemu-img snapshot -c`
  prints nothing on success and exits 0; instar matches that, so
  `-q` changes nothing visible for `-c`. See the general `-q`
  no-op note above for all four modes.

- **Names longer than 255 bytes are refused (not truncated).**
  The qcow2 on-disk `name` field tops out at 255 usable bytes.
  `qemu-img snapshot -c` *silently truncates* a longer name to 255
  bytes and exits 0; instar refuses loudly with a clear host-side
  error instead, on the principle that silently dropping bytes the
  user typed is surprising. An **empty** name is likewise refused
  (qemu-img accepts an empty name); supply a non-empty `NAME`.

- **The created file may be physically larger than `qemu-img`'s.**
  instar writes through 64 KiB virtio sectors, so the final
  snapshot-table write rounds the file up to the next sector
  boundary; `qemu-img` writes at byte granularity and leaves the
  trailing cluster sparse. The result is a benign difference in
  `qemu-img info`'s `disk size` / `file length` — the trailing
  bytes are zero, `qemu-img check` is clean with no leaks, and the
  qcow2 structure (snapshot table, L1 copy, refcounts, COPIED
  flags) is byte-for-byte identical to `qemu-img`'s. This is an
  instar-wide property of its sector-granular I/O, not specific to
  snapshots.

### `snapshot -d` (delete) quirks

The following apply to `instar snapshot -d SNAPSHOT` (delete
mode, landed during the PLAN-snapshot work). The feature gates
(`refcount_bits != 16`, compressed clusters, encryption, external
data file, bitmaps, dirty/corrupt) are the same uniform set as
`-c` above.

- **`-d` matches by NAME only, first match in table order.** The
  modern `qemu-img` this tracks (10.x) resolves the `-d` argument
  via `bdrv_snapshot_find`, which is a plain name comparison —
  **there is no ID matching on the delete path**. On an image
  whose snapshots are `alpha` (id 1) and `gamma` (id 3),
  `qemu-img snapshot -d 3` fails with "snapshot not found", and
  instar matches that exactly. With duplicate names, the first
  entry in table order is deleted; with a snapshot *named* "2"
  and another with *ID* 2, `-d 2` deletes the one named "2".
  *Cross-version note:* older `qemu-img` releases resolved IDs
  first (the since-removed `bdrv_snapshot_delete_by_id_or_name`);
  instar follows 10.x, and the cross-version baseline phases must
  pin delete baselines accordingly.

- **Deleting never truncates the file.** Freed clusters (the
  snapshot's L1, the old snapshot table, and any data / L2
  cluster whose refcount reaches 0) remain in the file until
  reused, matching qemu.

- **Freed-cluster bytes may differ from `qemu-img`'s.** By
  default qemu-img passes a *discard* down to the file for the
  clusters a delete frees (`QCOW2_DISCARD_SNAPSHOT` /
  `QCOW2_DISCARD_ALWAYS` default on), punching holes so those
  regions read back as zeros; qemu's `-1` refcount walk also
  rewrites COPIED flags inside the about-to-be-freed L1/L2
  clusters. instar never writes to freed clusters at all — their
  stale bytes remain. All *live* metadata is byte-for-byte
  identical: running the qemu side with
  `--image-opts driver=qcow2,file.filename=…,file.discard=ignore`
  (which disables only the protocol-level hole punching) yields
  post-delete images that are **bit-for-bit identical** to
  instar's, modulo the sector-granular file-tail quirk above.
  `qemu-img check` is clean either way.

- **An empty `-d` argument is passed through.** `qemu-img
  snapshot -c ''` happily creates an empty-named snapshot, and
  `-d ''` deletes it; instar refuses *creating* empty names (see
  the `-c` quirks) but still deletes them for parity. There is no
  host-side validation of the delete argument; an argument longer
  than the 256-byte wire buffer cannot name any matchable
  snapshot (qemu-img truncates names to 255 bytes at creation)
  and resolves to the same not-found error.

### `snapshot -a` (apply) quirks

The following apply to `instar snapshot -a SNAPSHOT` (apply /
"goto" mode, landed during the PLAN-snapshot work). The feature gates
are the same uniform set as `-c` / `-d` above.

- **Snapshot argument matching is asymmetric between `-d` and
  `-a`.** qemu 10.x resolves the two modes' arguments through
  *different* matchers, and instar matches each exactly:

  | Mode | Matcher | Semantics |
  |------|---------|-----------|
  | `-d` | `bdrv_snapshot_find` | name only, first match |
  | `-a` | `find_snapshot_by_id_or_name` | one full pass over the table comparing **IDs**, then — only if no ID matched — a second full pass comparing **names** |

  The two-full-pass structure means a *later* entry matching by
  ID beats an *earlier* entry matching by name. Example: on an
  image with `id=1 name="2"` and `id=2 name="x"`, `-a 2` applies
  the snapshot with **ID 2** (the one named "x"), while `-d 2`
  deletes the one **named** "2". A pure-ID argument (`-a 1`)
  works for apply but is not-found for delete.
  *Cross-version note:* as with delete (above), older `qemu-img`
  releases resolved delete arguments differently; instar follows
  10.x and the cross-version baseline phases must pin per-version
  behaviour.

- **Applying a snapshot to a since-resized image is refused.**
  Modern `qemu-img` allows `resize` on images with internal
  snapshots, and a later `qemu-img snapshot -a` **truncates the
  image** back to the snapshot's stored `disk_size`
  (`blk_truncate` inside `qcow2_snapshot_goto`). instar refuses
  instead (`ERROR_L1_SIZE_MISMATCH`) and leaves the image
  untouched — a full virtual-size truncate embedded in apply is
  out of scope for v1. Workaround: `qemu-img resize` the image
  back to the snapshot's size, then apply. (A snapshot entry
  with *absent* extra data carries no `disk_size`; qemu defaults
  it to the current virtual size, so such entries always pass
  the check — instar mirrors that.) For the same reason a
  hand-crafted snapshot whose L1 is *larger* than the active L1
  is refused (qemu would grow the active L1); a *smaller*
  snapshot L1 is supported via zero-padding, like qemu.

- **Apply is best-effort crash-consistent, like qemu.** Apply
  rewrites the active L1 in place; it writes no timestamps, no
  snapshot-table bytes and no header bytes. instar's write order
  is: refblock increments (group A), fsync; the snapshot's raw
  L1 over the active L1 — the commit point (group B), fsync;
  refblock decrements + refreshed COPIED flags (group C), fsync.
  A crash before B leaves the image unchanged except
  over-referenced refcounts (repairable leaks); a crash between
  B and C leaves the active view switched with leaks and stale
  COPIED flags — `qemu-img check` reports repairable issues,
  never a dangling reference. qemu's goto has the same
  best-effort character; one window differs cosmetically (qemu
  scrubs the snapshot's stored L1 before its active-L1
  overwrite, instar after), but both orders leave only
  repairable states and the final bytes are identical.

- **Freed-cluster bytes may differ from `qemu-img`'s.** Same as
  delete: qemu punches holes over the clusters the apply frees
  (the old active chain's exclusive L2/data clusters) unless run
  with `file.discard=ignore`, while instar never writes freed
  clusters. With the protocol-level discard disabled, post-apply
  images are **bit-for-bit identical** to instar's across every
  scenario the matrices verified, including diverged
  applies — with one cache-pressure exception the differential
  fuzzer later surfaced (issue #381): qemu's `-1` refcount walk
  refreshes COPIED flags inside the old active chain's L2s
  through its metadata cache, and when cache pressure (512-byte
  clusters mean tiny L2/refcount caches) forces an eviction
  flush mid-walk, a **partially refreshed** L2 lands on disk
  before the cluster is freed and its remaining dirty flags are
  discarded with the cache entry. instar never writes freed L2s,
  so the two tools leave different residue in a cluster both
  agree is free (refcount 0, `check` clean, `compare`
  identical). The differential fuzzer's snapshot comparator
  handles this with its dead-cluster rule: byte differences
  confined to clusters with refcount 0 in *both* images are
  residue, not divergence.

## `check --repair=leaks` Scope vs `qemu-img check -r leaks`

### Observed Behavior

`qemu-img check -r leaks` repairs more than literal leaks: it also trims an
*over-counted* but still-referenced cluster's refcount down to its true value
(it treats, for example, `refcount=2 reference=1` as a repairable leak).
`instar check --repair=leaks` does **not** — it only frees clusters that are
unreferenced (refcount > 0 with no L2 reference), and never lowers a
*referenced* cluster's refcount. So a refcount-too-high cluster stays flagged
by `qemu-img check` after `instar --repair=leaks`, but is cleaned by `qemu-img
-r leaks`.

### instar Behavior

This is intentional. instar's safe (`leaks`) tier is strictly lossless and
monotonic: freeing an unreferenced cluster cannot lose live data, whereas
lowering a referenced cluster's refcount is a metadata rewrite that belongs to
the lossy tier. Over-count correction is therefore deferred to `instar check
--repair=all`, which recounts every cluster and corrects in both directions
under the crash-safe `corrupt`-bit ordering. Run `--repair=all` to match (and
exceed) `qemu-img -r leaks`'s over-count trimming.

Note that the leaks tier reports its own work as *complete* once it has freed
every genuine leak — a residual over-count is simply outside its remit, not a
failure, so it is not flagged as `repair-incomplete`; a subsequent read-only
`instar check` still reports the over-count. The difference is surfaced by the
differential fuzzer (`scripts/differential-fuzz.py`, the `repair` op), which
gates its cleanliness-convergence check on the `all` tier precisely because
the two tools' `leaks` tiers have deliberately different scope.

## Copy-on-write for snapshot-bearing qcow2 images

**Classification: Safe behaviour** (qemu-parity, not a divergence).

Since the PLAN-q workcow2-write-infrastructure, writes into a
snapshot-bearing qcow2 image **copy-on-write** the shared clusters
instead of refusing (the phase-2 interim gates) or corrupting them.
This cross-cutting change lifts the snapshot caveats from `commit`
(issues #420 / #423), `rebase` safe mode (issue #421) and `bench -w`,
so all three now succeed on images that carry internal snapshots.

### The per-op snapshot-view semantic

This is net-new behaviour, so the correctness bar is **qemu-parity,
not before/after byte identity**: `qemu-img check` clean + the active
view `qemu-img compare`-identical to a qemu twin + a snapshot
**read-back oracle** that extracts each pre-existing snapshot's virtual
view (apply-on-a-copy → convert to raw → sha256) and compares it to
qemu's result for the same op. Byte placement of the COW output is
explicitly **not** constrained — qemu's own COW placement is
nondeterministic at 512-byte clusters, so instar takes its own layout
freedom.

The read-back oracle's expected value is **per op**, not a blanket
"snapshot unchanged":

- **commit** preserves every backing snapshot bit-identically — a
  snapshot's post-commit read-back equals its **pre-commit** content
  (qemu COWs and preserves).
- **rebase** safe mode covers the active view only; a snapshot's
  unallocated ranges read **through the new backing** afterwards, so
  its expected value is the snapshot applied against the new backing,
  not its pre-rebase content (qemu's read-through-new-backing
  contract). An overlay snapshot in the post-write shape resolves the
  same way.
- **bench -w** writes into the active view and preserves snapshots
  like commit.

### The refcount COW machinery

Two shared cluster shapes are copied before modification:

- **Data-cluster COW** (a shared `D`, `OFLAG_COPIED` clear, refcount
  > 1): copy `D → D'`, patch the L2 entry to `D' | COPIED`, set
  `rc(D')=1`, and **decrement** `rc(D)` (2→1). The old `D` is never
  freed — the snapshot still references it.
- **L2-table COW** (a shared L2 table `T`): copy `T → T'`, patch the
  L1 entry to `T' | COPIED`, set `rc(T')=1`, decrement `rc(T)`, and
  **leave the child data clusters' refcounts untouched**. This last
  point is a subtlety worth flagging for future maintainers: qemu
  eagerly bumps every reachable data cluster to refcount ≥ 2 at
  *snapshot-creation* time, so by the time an L2 table is COWed its
  children are already rc ≥ 2 with `OFLAG_COPIED` clear. Copying
  `T → T'` merely redistributes the reference (net child delta 0); a
  literal "increment every child" would drive them to rc 3 and make
  `qemu-img check` dirty. The children already classify shared, so a
  write through `T'` into a child triggers the per-child data-cluster
  COW above. The decrement is a net-new refcount primitive (v1 only
  ever incremented on allocation); an underflow maps to each op's
  existing inconsistency error.

### The zero-flag WRITE-target policy

Independent of the #432 read fix below, the write planner classifies a
v3 `QCOW_OFLAG_ZERO` (bit 0) *target* L2 entry by qemu's exact
semantic (qemu does **not** free the old host offset):

- **host offset == 0** (zero flag, no allocation) → treated as
  unallocated: allocate a fresh cluster / zero-fill the range.
- **host offset != 0, refcount 1** → overwrite in place, clearing the
  zero bit (qemu reuses the offset — no free).
- **host offset != 0, refcount > 1** → copy-on-write (shared).

The earlier blanket "treat as unallocated → allocate fresh" would have
leaked the old host cluster (rc 1, unreferenced → check-dirty) or
skipped a required COW.

### #432: classic-L2 zero flag reads as zeros (fixed)

The chain reader previously ignored `QCOW_OFLAG_ZERO` on classic
(non-extended) L2 entries, so a v3 zero-flagged backing cluster read as
the wrong bytes (host == 0 fell through to a lower backing; host != 0
read stale host bytes) — silent active-view corruption with blast
radius rebase / convert / compare / bench. Fixed fix-first (step 7z):
`cluster_lookup` gained a `ClusterLookup::Zero` verdict and the chain
reader zero-fills for it (both host == 0 and host != 0). See also the
"qcow2 v3 standard-L2 `QCOW_OFLAG_ZERO` honoured (fixed)" entry in the
map section for the parser-side twin.

### Known limitations / follow-ups

- **commit does not byte-empty a snapshot-bearing overlay.** The
  overlay-clear pass is skipped for such overlays (zeroing shared
  active metadata in place was #423); the committed clusters stay
  mapped in the overlay's active L2, reading identically through the
  new backing. Full byte-emptying parity would need an overlay-side
  COW-clear primitive. Active view and snapshots are correct.
- **rebase's COW growth is coarsely sized** at
  `2 × overlay_cluster_count` (rebase writes into unowned clusters);
  the over-provisioned refblocks are check-clean via the #433
  materialization fix. A tighter bound is follow-up work.

Verified check-clean and read-back-parity against pinned qemu-img
6.2.0 / 7.2.0 / 8.2.0 / 9.2.0 / 10.2.0 by
`tests/test_cow_cross_version.py`, and across 50 randomized
snapshot-bearing iterations (0 divergences) by `scripts/cow-soak.py`;
`tests/helpers/snapshot_readback.py` is the reusable read-back oracle.

## Parallels, Bochs, cloop and DMG detection

The PLAN-f workormat-coverage.md` added content-based detection and
info parity for Parallels, Bochs, cloop, and DMG. The five entries below
record the deliberate divergences this introduced, plus the closure of a
pre-existing consumer defect the phase surfaced along the way. See
[docs/plans/PLAN-format-coverage-phase-01-detection.md](/components/instar/plans/PLAN-format-coverage-phase-01-detection/)
for the full design and findings.

### DMG Detection: Content-Based Trailer Probing vs qemu's Filename Extension

**Classification: Safe Quirk**

#### Observed Behavior

qemu-img's DMG probe is almost entirely extension-based: it recognises a
file as DMG chiefly by the `.dmg` filename suffix, not by content. A
file containing a byte-perfect UDIF koly trailer but named without a
`.dmg` suffix probes as `raw` under qemu-img.

instar instead detects DMG the same way it already detects fixed VHD:
by content. When the header probe returns Raw, instar scans the file's
final 1024 bytes for the `koly` magic (mirroring qemu's own
`dmg_find_koly_offset` candidate window, `[len-1023, len-512]`) and, if
found, reports `dmg` regardless of filename.

#### Why This Matters

Reporting "this raw-looking file is actually a compressed UDIF
container" is precisely the class of fact instar's safety-detection
charter exists to surface — the same stance already taken for ISO 9660
(see "ISO 9660 Detection vs RAW" above). Extension-based detection
would make instar's format report dependent on how a file happens to be
named, which is not a security-relevant signal.

#### instar Behavior

**Always** (no flag toggles this): instar reports `dmg` for any file
whose final bytes carry a valid koly trailer, independent of filename.
For fixtures actually named `*.dmg` (the phase-1 baseline fixture,
`dmg-simple`), both tools agree. The divergence is confined to misnamed
files: a koly-bearing file without a `.dmg` suffix reports `dmg` under
instar and `raw` under qemu-img. Unlike the other safe quirks in this
document, `--unsafe-quirks` does **not** change DMG detection — instar
never adopts qemu's extension probe, matching the design decision
recorded as OQ2 in the phase-1 plan.

### cloop Full-Magic Match vs qemu's Prefix-Match Truncation

**Classification: Safe Quirk**

#### Observed Behavior

qemu's cloop probe compares `min(strlen(magic), buf_size)` bytes of the
83-byte V2.0 shell-script magic against the file's leading bytes. A
file shorter than 83 bytes whose available bytes are a prefix of the
magic still probes as `cloop` under qemu-img — the comparison degrades
to "how many bytes do we have" rather than "is the full magic present."

instar requires the complete 83-byte magic (`len >= 83` and an exact
match); a truncated file that qemu-img would call `cloop` detects as
`raw` (or falls through to the partition-table gate) under instar.

#### Why This Matters

This is a degenerate edge case — only files truncated mid-magic are
affected, which in practice means a corrupted or incomplete cloop
image, not a legitimate one. qemu's prefix-match behaviour arguably
over-detects; instar's stricter full-match requirement is the more
conservative reading of the same magic string, not a loss of format
identification accuracy for any real cloop image (the shortest real
cloop file must carry the full 83-byte header to be valid anyway).

#### instar Behavior

**Default and only behavior**: instar always requires the full 83-byte
magic. There is no flag to relax this to qemu's prefix-match rule.

### DMG Info: Trailer-Only Report vs qemu's Chunk-Table Open Requirement

**Classification: Safe Quirk** (adversarial fixtures only)

#### Observed Behavior

`qemu-img info` on a DMG must fully *open* the image, which requires
parsing a valid BLKX chunk table (from the rsrc-fork or XML plist
region referenced by the koly trailer). A DMG with a structurally valid
koly trailer but no parseable chunk table fails to open, and
`qemu-img info` errors out.

instar's `info` support parses only the koly trailer — it does not
walk the chunk table, even now that convert/compare/dd/
bench a full chunk-table reader
— so it reports format name and virtual size directly from the
trailer's `SectorCount` field, successfully, even when the chunk
table is missing or empty. This is a deliberate scope boundary, not
an oversight: `info` never needed the chunk table, and the read work did
not add one to it.

#### Why This Matters

For every well-formed DMG (a real UDIF image with a working chunk
table), both tools agree. The divergence is confined to the
`dmg-no-chunk-table` adversarial fixture (`RsrcForkLength` and
`XMLLength` both zero): `qemu-img info` errors, instar reports `dmg`
with the trailer's declared virtual size. This fixture carries
`skip_qemu_img: true` in `tests/manifest.json` precisely because no
qemu-img baseline exists to compare against.

#### instar Behavior

**Default behavior**: instar reports whatever the koly trailer alone
can support, without requiring the chunk table to be present or valid.
This is intentionally more permissive than qemu-img for `info`
specifically — instar's sandboxed architecture means there is no
safety cost to reporting a best-effort trailer-derived size for a
container it cannot fully open, unlike qemu-img's open-then-read model.

### Detect-Only Format Refusal in convert / compare / dd (#444)

**Classification: closes an Unsafe Quirk** (was silently mimicking
qemu-img's raw-fallback behaviour; now refuses instead)

#### Observed Behavior (before the fix)

`instar convert`, `compare`, and `dd` discover their input's format via
a guest-side `info` probe, then map the reported format string to a
`chain::ImageFormat`. Any format instar detects but has no read path
for (`qed`, `vdi` and `parallels` at the time, and — after this phase
— `bochs`, `cloop`, `dmg`) collapsed to `ImageFormat::Unknown`, and the
chain reader's default arm read `Unknown` images as **raw bytes** —
emitting the container's bytes zero-padded to the header-declared
virtual size, with no error and no indication anything was wrong. This
was tracked as [#444](https://github.com/shakenfist/instar/issues/444),
confirmed by an empirical pin (see the plan's
Findings section), and fixed by step 3b. `vdi` gained a full read path
 and `parallels`; neither is part of this refused
set any longer — see the format-coverage sections and the format-coverage sections below.

#### instar Behavior (after the fix)

`discover_backing_chain` now refuses centrally — covering the top-level
image and every mid-chain backing position identically — whenever the
guest-reported format string is detected but maps to `Unknown` and is
not `raw`, `unknown`, or `iso`. The typed error names the format,
e.g.:

```
convert: input format 'bochs' is detected but not supported for
reading (detection and info only)
```

This closed the hole for `qed` (previously silently read as raw,
contradicting its documented "detected, refused" stance) as well as
the four newly detected formats. `vdi` and `parallels` were in the
same set at the time; `vdi` and `parallels` were later
graduated to full read paths instead, so neither
appears among the refused formats any longer (see the format-coverage sections and the format-coverage sections below). There is no flag to
disable this refusal — unlike `instar info`, convert/compare/dd have
no `--unsafe-quirks` path at all, so the refusal is unconditional.

#### The ISO exemption and the info-vs-consumer asymmetry

**iso is deliberately exempt** and keeps its raw pass-through
everywhere: unlike qed/bochs/cloop/dmg — where a raw interpretation
misrepresents the content (container metadata plus zero padding, not
the virtual disk) — an ISO's container bytes *are* its virtual disk
content, so reading it as raw is semantically correct. qemu-img
(which has no ISO driver at all) converts ISOs as raw routinely;
refusing them would be a parity regression on a common workflow, not
a safety fix. `vdi` and `parallels` were in the same "raw would
misrepresent it" group as the others until they were graduated
respectively, which gave them full readers instead of a refusal —
see the format-coverage sections and the format-coverage sections below.

This produces an asymmetry worth noting explicitly: standalone `instar
info` on an ISO reports `iso` by default (secure mode) and `raw` only
with `--unsafe-quirks` (see "ISO 9660 Detection vs RAW" above). But
`discover_backing_chain` always probes via `info` in secure mode
internally — regardless of any flag convert/compare/dd don't even
accept — so it always sees `"iso"`, and then explicitly declines to
refuse it. In other words, convert/compare/dd behave *as if* they were
always in `--unsafe-quirks` mode for ISO specifically, while every
other detect-only format is refused unconditionally in every mode.
This is intentional (see the phase-1 plan's post-1a management review
decision) and is pinned by tests asserting the exact ISO pass-through
byte sizes (`convert` 393216, `dd` 376832).

### DMG pass-through as raw in the in-place ops (accepted behaviour)

**Classification: Safe Quirk** (accepted, tracked for future work)

#### Observed Behavior

The koly-trailer probe added is wired only into the guest
`info` op, as the phase-1 plan specified. The in-place single-image ops
— `resize`, `map`, `measure`, and the other guest ops that detect via
`detect_format_from_header` directly rather than through the `info`
chain — never see the trailer probe, so they detect a DMG (valid or
adversarial) as `Raw` and pass it through as a raw disk image, exactly
as they would for any other undetected file. Parallels, Bochs, and
cloop are unaffected for these in-place ops — they are header-detected
at offset 0, which *is* wired into `detect_format_from_header`, so
`resize`/`map`/`measure` refuse all three correctly. Bochs and cloop
still refuse in every op; `parallels`'s convert/compare/dd/bench
refusal was lifted, which gave it a full reader instead —
see the format-coverage sections below for the current per-op picture.

#### Why This Matters

This mirrors qemu-img's own treatment of a misnamed DMG (qemu's
extension-based probe also fails to recognise it, so qemu-img reads it
as raw too), and the data-copying consumers that actually matter for
safety — convert, compare, dd — already refuse DMG via the #444 gate
above (they route through `info`, which does run the trailer probe).
Only the single-image in-place ops are affected, and only for DMG.

#### instar Behavior

**Accepted and unchanged, pinned by tests**
(`test_dmg_{resized,measured,reads}_as_raw`): resize, map, and measure
treat a DMG the same as any other raw-detected file. The DMG read work
(PLAN-format-coverage-phase-05-dmg-read.md) graduated DMG to a full
chunk-table reader for convert/compare/dd/bench — see
the format-coverage sections below — but deliberately did **not** wire
the koly probe into these in-place ops or their host prefix probes;
their raw pass-through is explicitly retained, not merely left over.
Wiring the koly trailer probe into the host in-place-op prefix probes
and the guest map/measure detection paths remains master-plan future
work (`docs/plans/PLAN-format-coverage.md`, "Future work"). `check`
has the same retained-raw-pass-through shape for a different reason —
it refuses DMG outright (exit 63) rather than reading it, but still
names the format "raw" because its own dispatch never runs the koly
probe either; see the format-coverage sections below.

## VDI convert-from (read path)

The PLAN-f workormat-coverage.md` graduated VDI (VirtualBox Disk
Image) from detect + info only to a full read format for convert,
compare, and dd, via a new `src/crates/vdi/` parser crate wired into
the qcow2 crate's chain reader (the same pattern VHD and VHDX use).
The five entries below record the deliberate qemu-parity choices this
introduced and the divergences that remain. See
[docs/plans/PLAN-format-coverage-phase-02-vdi-read.md](/components/instar/plans/PLAN-format-coverage-phase-02-vdi-read/)
for the full design and findings.

### Odd `disk_size` Rounds Up to 512 (qemu Parity, oslo Divergence)

**Classification: Safe Quirk**

#### Observed Behavior

qemu's `vdi_open` does not reject a `disk_size` that is not a multiple
of 512 (VBoxManage-created images can have one); it rounds the value
up to the next 512-byte boundary in memory and reports the rounded
size everywhere, including `qemu-img info`'s `virtual size` field.

#### Why This Matters

instar's reader and its `info` parser (`parse_vdi_header`) must agree
with qemu's rounded value, not the header's raw bytes, to stay
byte-identical against the qemu-img baselines. oslo.utils'
`VDIInspector` does not round — it reports the raw `disk_size` — so
this is also a genuine oslo divergence, not just an internal
consistency question.

#### instar Behavior

**Always** (no flag toggles this): instar rounds `disk_size` up to 512
at open, both for the reader's virtual-size view and for `info`'s
reported size, matching qemu exactly. Pinned by the `vdi-odd-size`
fixture (`disk_size` patched to 1048577, rounds to 1049088) with a
byte-parity convert test and a `KNOWN_VSIZE_DIVERGENCES` entry in
`tests/test_oslo_crossval.py` recording the oslo/qemu split.

### Past-EOF Block Reads Zero-Fill, Never Error

**Classification: Safe Quirk**

#### Observed Behavior

qemu's VDI driver never validates the on-disk file length against the
header's declared geometry. A block-map entry pointing past the end
of the file opens fine, `qemu-img map` reports it as ordinary data,
and `qemu-img convert` exits 0 with that block read as all-zero — a
straddling block (starts in-file, extends past EOF) zero-fills only
the missing tail.

#### Why This Matters

A reader that instead errored on a past-EOF block would refuse images
qemu-img converts successfully — a parity regression, not a safety
improvement, since the sandboxed read of a header-consistent VDI
carries no additional risk from an undersized backing file.

#### instar Behavior

**Always**: the chain reader's VDI arm zero-fills any portion of an
allocated read at or past the device capacity, including the straddle
case, mirroring qemu exactly. Pinned by the `vdi-bmap-past-eof`
fixture (one block-map entry ~256 MiB past EOF) with a byte-parity
convert test against `qemu-img convert`.

### `image_type` Leniency and `block_extra` Is Ignored

**Classification: Safe Quirk**

#### Observed Behavior

qemu does not validate the VDI header's `image_type` field at all:
values 0, 3, and 4 all open and behave identically to the documented
"dynamic" type (1); only type 2 ("static") gets different handling,
and even that difference is just that a static image's block map
happens to be the identity map written at creation time — the read
path is the same block-map walk either way. Similarly, `block_extra`
is parsed by qemu but never used in any offset computation.

#### Why This Matters

Rejecting an unrecognised `image_type` would refuse images qemu-img
opens without complaint. Since a static image's identity block map is
just ordinary block-map data, no special-casing is needed or correct.

#### instar Behavior

**Always**: `VdiHeader::parse` accepts any `image_type` value and the
reader never special-cases type 2 — the block-map walk is identical
for dynamic and static images. `block_extra` is parsed but never
included in offset arithmetic. Pinned by unit tests in
`src/crates/vdi/src/lib.rs` and the `vdi-static-data` fixture's
byte-parity convert test.

### `check` Still Refuses VDI; `qemu-img check` Does Not (Future Work)

**Classification: Safe Quirk** (documented gap, not a defect)

#### Observed Behavior

qemu-img's VDI driver supports `qemu-img check`, which validates the
block map (duplicate/overlapping entries, out-of-range values). instar
`check` links format crates directly rather than through the chain
reader that gained VDI support in this phase, and has no VDI arm.

#### instar Behavior

**Unchanged by this phase**: `instar check vdi-simple` exits 63 with
`This image format (vdi) does not support checks`, exactly as before
VDI's convert/compare/dd graduation — check was never in this phase's
scope (see the phase plan's "Out of scope" section). instar-testdata
already carries unconsumed VDI check baselines from `generate-baselines.py`
(qemu-img generates them because its own driver supports the
operation); wiring instar's `check` up to consume them is recorded as
master-plan future work.

### Malformed VDI: `compare` Reports a Mismatch, `info` Stays Lenient

**Classification: Safe Quirk**

#### Observed Behavior

The five malformed VDI adversarial fixtures (bad version, unaligned
block-map offset, wrong block size, non-NULL parent UUID, too many
blocks) each violate one of qemu's twelve `vdi_open` validation rules;
qemu-img refuses all five at open. instar's new reader refuses them
identically — but *how* each op surfaces that refusal differs, because
`info` uses a separate, more lenient parser than the reader.

#### Why This Matters

`convert` and `dd` surface the reader's init failure as a normal
operation error on stderr. `compare`, however, treats a source it
cannot open as a content mismatch rather than a hard error: comparing
a malformed VDI against any other file reports a non-zero-exit
mismatch on stdout, not "Images are identical" — proving compare is
not silently falling back to a raw read of the malformed container.
`info`'s header parser (`parse_vdi_header`) is intentionally out of
scope for the reader graduation — it still reports whatever
detection-level fields (format name, a plausible virtual size) the
raw header bytes yield, and exits 0, even for images the reader itself
refuses. This mirrors the same info-stays-lenient stance
established for malformed DMGs (see above).

#### instar Behavior

**Always**: `convert`/`dd` exit non-zero with a clean error;
`compare` exits non-zero reporting a mismatch; `info` exits 0 with
best-effort header fields. Pinned by `TestAdversarialVdiManifest` in
`tests/test_adversarial.py`, which asserts exit codes and non-empty
output for all four ops across the five malformed fixtures without
pinning instar's exact error string (only qemu-img's error strings are
version-stable enough to pin).

## Parallels convert-from (read path)

The PLAN-f workormat-coverage.md` graduated Parallels from detect +
info only to a full read format for convert, compare, dd, and bench,
via a new `src/crates/parallels/` parser crate wired into the qcow2
crate's chain reader (the same pattern VDI, VHD, and VHDX use). Both
magics — the legacy `WithoutFreeSpace` (v1) and the newer
`WithouFreSpacExt` (v2/ext) — are supported. The entries below record
the deliberate qemu-parity choices this introduced and the
divergences that remain. See
[docs/plans/PLAN-format-coverage-phase-03-parallels-read.md](/components/instar/plans/PLAN-format-coverage-phase-03-parallels-read/)
for the full design and findings.

### Per-Magic `off_multiplier` and the v1 32-bit `nb_sectors` Mask

**Classification: Safe Quirk**

#### Observed Behavior

qemu's Parallels BAT entries mean different things depending on the
magic: under `WithoutFreeSpace` (v1) a BAT entry is a *sector* number
(`off_multiplier == 1`); under `WithouFreSpacExt` (v2/ext) it is a
*cluster* index (`off_multiplier == tracks`). The two encodings can
address identical file content — verified byte-level on the fixture
pair (v2 BAT `[1,2,3,4]` and v1 BAT `[0x80,0x100,0x180,0x200]` with
`tracks=128` decode to the same host offsets). Separately, `nb_sectors`
(virtual size in sectors) is a 64-bit header field, but qemu masks it
to the low 32 bits when the magic is v1 and reads it full-width under
v2 — byte-patch verified: the same field value reports 2 MiB under the
v1 magic and 2 TiB under the v2 magic.

#### Why This Matters

Getting `off_multiplier` or the mask wrong per magic silently
misreads every allocated cluster in a v1 image (or reports a garbage
virtual size) without erroring — a correctness bug, not a crash, so
it needs explicit per-magic coverage rather than relying on the v2
path to catch it.

#### instar Behavior

**Always**: `ParallelsHeader::parse` stores `off_multiplier` (1 for
v1, `tracks` for v2/ext) and masks `nb_sectors` to 32 bits only under
the v1 magic; `virtual_size = masked_nb_sectors * 512`. Pinned by
per-magic unit tests in `src/crates/parallels/src/lib.rs` (the
BAT-decoding equivalence and the mask-under-v1-vs-v2 cases) and by the
`parallels-data-v1`/`parallels-data-v2` fixture pair, which are the
same image content under both magics and BAT encodings — `compare`
between them reports identical, and both convert byte-identically to
qemu-img's raw output.

### Past-EOF and Truncated Reads Zero-Fill — Except qemu's 8.1.x Open-Time Regression

**Classification: Safe Quirk**

#### Observed Behavior

qemu's Parallels driver never validates the on-disk file length
against the header's declared BAT/geometry. Out-of-image BAT entries,
a straddling cluster (starts in-file, extends past EOF), a truncated
BAT, and even a 30-byte file all read as zeros wherever bytes are
missing, with `qemu-img convert` exiting 0 — verified identical on
6.0.0/7.0.0/10.2.0. The one drift across the matrix: qemu 8.1.0
through 8.1.5 refuse a past-EOF BAT entry **at open** ("Offset ... in
BAT[n] entry is larger than file size"), a regression window closed
again in 8.2.0.

#### Why This Matters

A reader that errored on a past-EOF cluster would refuse images
qemu-img converts successfully on every version except the narrow
8.1.x window — a parity regression, not a safety improvement, since
the sandboxed read of a header-consistent Parallels image carries no
extra risk from an undersized backing file. Faithfully recording the
8.1.x refusal (rather than papering over it) keeps the baseline
matrix honest about the one version range where instar and qemu
genuinely disagree.

#### instar Behavior

**Always**: the chain reader's Parallels arm zero-fills any portion
of an allocated read at or past the device capacity, including the
straddle case, uniformly across all qemu versions — instar does not
special-case the 8.1.x behaviour. Pinned by the `parallels-bat-past-eof`
fixture with a byte-parity convert test. The 8.1.0-8.1.5 open
refusals are recorded faithfully in the instar-testdata baseline
matrix via a dedicated `profile-8-1-0` bucket (split out from the
neighbouring profile so the two refusing versions don't corrupt a
shared baseline); `tests/test_info_safe.py` gained a general
mechanism (commit `30ecf77`) that skips scenario generation whenever
a profile's baseline meta records a non-zero qemu-img return code —
there is no output parity to assert when qemu itself refused the
image, and the mechanism is not Parallels-specific, so any future
per-profile refusal drift is handled the same way.

### `inuse`-Dirty Images and Ignored Header Fields Read Normally

**Classification: Safe Quirk**

#### Observed Behavior

qemu refuses a read/write open of a Parallels image whose `inuse`
field (offset 44) is `0x746f6e59` ("opened uncleanly"), but a
**read-only** open succeeds and converts correctly. Separately,
`data_off` (offset 48, the write-path allocation frontier) is parsed
by qemu but never participates in any read-path offset computation —
byte-patch verified: garbage `data_off` values are harmless to reads.

#### Why This Matters

instar always opens Parallels images read-only, so refusing on
`inuse` would reject images qemu-img itself can read (via `-O raw`
without `-rw`) — a pure parity regression. `data_off` needing no
special handling means the reader can parse and discard it without
risk of a latent bug in unreachable code.

#### instar Behavior

**Always**: `ParallelsHeader::parse` never refuses on `inuse`, and the
field plays no role in `ParallelsState::init`/`block_lookup`;
`data_off` is parsed but never used in offset math. Pinned by the
`parallels-inuse` fixture (a `parallels-data-v2` copy with `inuse` set
dirty), which converts byte-identically to its clean twin.

### `ext_off != 0` Refused — Deliberate Divergence from qemu's Extension Parsing

**Classification: Safe Quirk** (documented divergence, not a defect)

#### Observed Behavior

A non-zero `ext_off` (offset 56) points qemu at a format extension
(currently used for dirty-bitmap metadata) that it parses read-only
and refuses only on a bad extension magic. instar's reader refuses
**any** non-zero `ext_off` at init, regardless of what the extension
contains — including a hypothetical valid one qemu would open
successfully.

#### Why This Matters

This is instar choosing not to implement extension parsing rather
than a parity bug: no shipped or creatable fixture has `ext_off` set
to a valid extension (`qemu-img create -f parallels` never writes
one), the extension adds no data to the read path needs, and
silently ignoring an unparsed extension would risk misreading an
image whose extension actually matters once one exists. Refusing
cleanly is the safe default until a real need for extension support
appears.

#### instar Behavior

**Always**: any non-zero `ext_off` is refused at
`ParallelsHeader::parse` time, with no attempt to read or validate
the extension's own magic. Pinned by unit tests and by the
`parallels-ext-bad-magic` fixture (qemu also refuses this specific
fixture, on its bad extension magic, so the fixture pins the refusal
path without yet exercising the valid-extension divergence — there is
no fixture for that case by design). Recorded as master-plan future
work: Parallels format extensions / dirty bitmaps.

### `cluster_size` Reported Internally by `info`, Suppressed in Both Emitters

**Classification: Safe Quirk**

#### Observed Behavior

`qemu-img info` prints no `cluster_size` and no format-specific block
at all for Parallels — only the generic 8.0 child-node fields. But
the chain reader's chunking relies on `ChainDeviceInfo.cluster_size`
to keep chunks from straddling non-contiguous clusters, and Parallels'
cluster size (`tracks << 9`) is user-settable via `-o cluster_size` at
creation, so it cannot be hardcoded.

#### Why This Matters

VDI worked for free here because qemu (and instar's VDI parser)
already report `cluster_size` for VDI. Parallels needed a new
mechanism: compute the value for internal use without changing
user-visible `info` output, which the existing byte-identical-output
contract with qemu-img requires.

#### instar Behavior

**Always**: the guest `info` op's `parse_parallels_header` now also
reads `tracks` (offset 28) and sets `result.cluster_size = tracks <<
9` internally; the host emitters (`print_info_result` and
`print_info_result_json`, `src/vmm/src/main.rs`) suppress
`cluster_size` for the `"parallels"` format string specifically, in
both human and JSON output — the same format-gated suppression
mechanism used for the dirty-flag JSON field. The suppression
is format-gated, not value-gated, so a real nonzero `tracks` value
stays hidden exactly as qemu's own silence does. Verified by a full
`test_info_safe` run passing byte-identical against the qemu-img
baselines (zero regressions) plus the small-cluster
`parallels-cluster-4k` fixture, which pins that chunk boundaries never
cross the populated cluster size end-to-end.

### `check` Still Refuses Parallels; qemu's Own Check Crashes on Newer Versions

**Classification: Safe Quirk** (documented gap, not a defect)

#### Observed Behavior

`qemu-img check` supports the Parallels driver, validating the BAT
for duplicate/out-of-range entries. But on 10.2.0, `qemu-img check`
**asserts and crashes** (the `parallels_check_duplicate` assertion) on
an out-of-image BAT entry that 6.0.0 reports cleanly — a real qemu
regression, not a theoretical one.

#### instar Behavior

**Unchanged by this phase**: `instar check parallels-v2` exits 63 with
`This image format (parallels) does not support checks`, identically
to VDI's stance and to Parallels' own pre-phase-3 behaviour — check
was out of scope for this phase (see the phase plan's "Out of scope"
section), and instar's own format dispatch has no Parallels arm to
lift even though the host gate for other ops was lifted by
graduation. Given qemu's own check is crash-prone on adversarial
Parallels input on current versions, the refusal is the conservative,
correct stance rather than a coverage gap to close blindly; the qemu
`parallels_check_duplicate` assertion is recorded as master-plan
future work to report upstream.

### Tracks Cap Corrected to 4186127 (Planning Research Was Off by 681)

**Classification: Safe Quirk** (internal correction, no behaviour change to ship)

#### Observed Behavior

qemu refuses to open a Parallels image whose `tracks` (sectors per
cluster) exceeds `INT32_MAX / 513`. The phase plan's initial research
computed this as 4185446; step 3d's empirical fixture validation
found the real boundary is 4186127 — `tracks=4185447` opens cleanly
and `tracks=4186128` is the smallest value qemu refuses ("Invalid
image: Too big cluster").

#### Why This Matters

An off-by-681 cap would make instar refuse a narrow band of
`tracks` values (4185447–4186127) that qemu itself opens fine — a
pure parity regression that only an empirical fixture sweep, not
integer arithmetic alone, catches.

#### instar Behavior

**Always**: `PARALLELS_TRACKS_MAX` in `src/crates/parallels/src/lib.rs`
is `4_186_127`, corrected by commit `8dbf89f` from step 3a's original
value once step 3d's fixture validation pinned the real boundary; the
crate's boundary unit tests reference the constant symbolically and
needed no rewrite. Pinned by the `parallels-huge-tracks` fixture
(`tracks` patched to 4186128, the smallest refused value).

## QCOW1 convert-from (read path)

The PLAN-f workormat-coverage.md` graduated QCOW1 ("qcow", qemu's
original deprecated format, magic `QFI\xfb` + version 1) from a
misdetected-as-QCOW2 dead end to a full read format for convert,
compare, dd, and bench, via a new `src/crates/qcow1/` parser crate
wired into the qcow2 crate's chain reader (the same pattern VDI,
Parallels, VHD, and VHDX use) — commits `23b240f` (crate), `77f32ca`
(reader arm), `3aa7f50`/`467d24a`/`c421f75`/`a0f757b` (info, naming,
emitters, detection split, pins), `7b3762f` (fixtures/oslo), `efdc42e`
(integration matrix), `dbb5ff2` (fuzz). QCOW1 is the first non-QCOW2
format with backing-chain support. See
[docs/plans/PLAN-format-coverage-phase-04-qcow1-read.md](/components/instar/plans/PLAN-format-coverage-phase-04-qcow1-read/)
for the full design and findings.

### QCOW1 Was Misdetected as QCOW2 — Fixed

**Classification: closes an Unsafe Quirk** (was producing garbage
`info` output and a misleading convert error; also corrects a wrong
claim this document previously made)

#### Observed Behavior (before the fix)

`detect_format_from_header` (`src/shared/src/format_detection.rs`)
checked the 4-byte big-endian magic against `QCOW2_MAGIC =
0x514649fb` first and never consulted the version field. A real
QCOW1 image's magic **is** `QFI\xfb` — identical to QCOW2's, version
distinguishes them — so every real QCOW1 image detected as `Qcow2`.
The dead-code 3-byte `QCOW1_MAGIC` branch below it only matched
`"QFI"` plus a fourth byte that was *not* `0xfb`, which no real QCOW1
image has. `instar info` on a fresh `qemu-img create -f qcow` image
printed `file format: qcow2`, `virtual size: 0`, and a garbage
QCOW2-shaped `compat: 0.10` block; `instar convert` then failed with
the misleading `Error: "input image has zero virtual size"` — not a
silent raw read, but wrong info and a wrong error. This document
previously (incorrectly) listed QCOW1 detection as "Yes" in the
Format Detection Comparison table; that claim was wrong until this
phase.

A second, latent hazard sat behind the misdetection:
`chain::ImageFormat::from_str` already mapped `"qcow1"` to a real
`Qcow1` variant with `supports_backing() == true`, so the #444
detect-only-format gate (see above) would **not** have refused it —
the guest chain reader's `_ => read_raw_sectors` default arm would
have read the container's bytes as raw the moment detection became
version-aware without a reader arm in place. This is why the reader
arm (step 4b) had to land strictly before the detection fix (step
4c) — see the phase plan's Situation section.

#### instar Behavior (after the fix)

**Always**: detection is now version-aware — `QFI\xfb` + version u32
BE at offset 4 `== 1` routes to `Qcow1`; any other version keeps the
existing QCOW2 route (whose own open-time version check produces the
refusal for QCOW2-driver-incompatible versions, the same division of
labour qemu's own probes use). The dead 3-byte-magic branch was
removed in the same commit (`c421f75`). One latent divergence from
qemu was found and recorded, not fixed: a `QFI\xfb` image with
version 0 probes as **raw** under qemu (its QCOW2 probe requires
version >= 2, its qcow probe requires version == 1, so version 0
satisfies neither and qemu's generic probe falls through to raw),
but instar routes any non-1 version to the QCOW2 driver, which
refuses version 0 — so instar refuses an image qemu would read as
raw. Version 99 is not a divergence: both instar and qemu refuse it
via the QCOW2 driver's own version check. No fixture exists for the
version-0 case (it is a probe-routing curiosity, not a data-safety
issue); the pin was verified against the qemu-img matrix during step
4c.

### Naming: `"qcow"`, Not `"qcow1"` — With `"qcow1"` Kept as an Input Alias

**Classification: Safe Quirk**

#### Observed Behavior

qemu-img and oslo.utils both call this format `"qcow"` (the `-f
qcow` driver name); instar previously emitted `"qcow1"` in both
`format_to_str` (`src/operations/info/src/main.rs`) and
`chain::ImageFormat`'s `Display` impl (`src/vmm/src/chain.rs`) —
harmless while QCOW1 was detect+info only and no byte-parity
baseline existed, but the new QCOW1 info baselines require exact
`"qcow"` to match qemu-img's `file format:` line and JSON `format`
key.

#### instar Behavior

**Always**: instar now emits `"qcow"` everywhere a format string is
reported (human and JSON `info` output, JSON backing-format
reporting). `chain::ImageFormat::from_str` accepts **both** `"qcow"`
and `"qcow1"` as input aliases (so any config or script still using
the old string keeps working), but `Display` and every emitter only
ever produce `"qcow"`. Two related emitter-parity fixes landed in the
same pass: the JSON `backing-filename-format` field is now suppressed
for `"qcow"` backing (the format stores no backing-format field in
its header — qemu probes it at open instead — so reporting one would
be fabricated), and `"qcow"` joined the protocol-length 512-rounding
sets alongside the other formats whose child-node file length qemu
rounds to a sector boundary.
`instar-testdata/scripts/generate-baselines.py`'s check/compare
allowlists already used `'qcow'`; its measure/info source
allowlists, which had drifted to `'qcow1'`, were aligned to
`'qcow'` in step 4d.

### `encrypted:` Info Line — New Emitters, Gated Off for LUKS

**Classification: Safe Quirk** (pre-existing gap fix; QCOW1 is the
first fixture to exercise it)

#### Observed Behavior

`INFO_RESULT_FLAG_ENCRYPTED` (`src/vmm/src/main.rs`) has existed
since before this phase but was never consumed by either host
emitter — instar never printed qemu's `encrypted: yes` human line or
JSON `"encrypted": true`, for *any* format, because no baseline in
the whole tree needed it (verified by grep; the hand-maintained LUKS
goldens match qemu in omitting the line for bare LUKS containers).
The QCOW1 AES fixture (`qcow1-encrypted`, crypt_method=1) is the
first baseline in the tree to require it.

#### Why This Matters

Consuming the flag naively for every format would have broken the
LUKS goldens, since qemu prints no `encrypted:` line for bare LUKS
containers even though instar's LUKS info parser does set the flag —
the emitter had to be gated per-format to keep those goldens
byte-identical, not just wired up.

#### instar Behavior

**Always**: both emitters now consume `INFO_RESULT_FLAG_ENCRYPTED`.
The human emitter prints `encrypted: yes` between the disk size and
`cluster_size:` lines; the JSON emitter adds `"encrypted": true`
positioned after `actual-size` and before `dirty-flag`, matching
qemu's real `info --output=json` field order on an AES QCOW1 image.
The line is **gated off for the `"luks"` format string** specifically
— qemu prints no encrypted line for bare LUKS, and the hand-maintained
LUKS goldens pin that — verified by a full `test_info_safe` run with
zero regressions before the emitter change landed. Encrypted QCOW2
images (crypt_method=1 AES, or LUKS-wrapping) also gain the line now
that the consumption is general rather than QCOW1-specific; this is a
**pre-existing gap fix with no baseline churn**, since no existing
fixture's golden covers an encrypted-QCOW2 info baseline. The QCOW1
info parser itself sets the flag whenever `crypt_method != 0`;
`crypt_method == 1` (AES) is the only value that reaches the flag,
since `crypt_method >= 2` is refused at parse time.

### Backing Fall-Through: QCOW1 is the First Non-QCOW2 Backing Format

**Classification: Safe Quirk**

#### Observed Behavior

Every prior read-only format's chain-reader arm (VDI, Parallels)
zero-fills unallocated regions directly, because none of them
support backing files. QCOW1 does: an unallocated L1 or L2 entry
(entry value 0) must fall through to the **next device in the
backing chain** — the base image, if present, else zeros — exactly
like QCOW2's own backing semantics. `qemu-img create -f qcow -b
...` and its overlay reads were byte-verified to match this rule
during phase-4 research.

#### Why This Matters

Zero-filling unallocated QCOW1 clusters unconditionally (the
VDI/Parallels arms' approach) would silently discard base-image data
on any QCOW1-over-something overlay — a correctness bug specific to
the one format in this batch that actually supports backing files.

#### instar Behavior

**Always**: the QCOW1 reader arm (in `src/crates/qcow2/`, behind the
`qcow1-input` feature) mirrors the existing QCOW2 arm's mechanism for
signalling "unallocated, recurse into the backing device" to the
chain walker, rather than reusing the VDI/Parallels arm's zero-fill
shape — the same sub-span recursion the QCOW2 arm already uses for
its own backing chains, now shared by QCOW1. The reader also walks
**per-cluster**, not one-lookup-per-chunk like the VDI arm: QCOW1
clusters go down to 512 bytes (`create -f qcow -b ...` defaults to
512-byte clusters, `cluster_bits=9`), far smaller than typical chunk
sizes, so a single chunk can span many clusters. Pinned by the
`qcow1-backing`/`qcow1-backing-base` fixture pair (relative backing
name, `-F qcow` hint, two overlay clusters masking the base, every
other offset reading through) which doubles as the small-cluster
walk coverage.

### Compressed Clusters Are Raw DEFLATE, Not zlib

**Classification: Safe Quirk**

#### Observed Behavior

QCOW1's bit-63 compressed L2 entries (`coffset = entry & ((1 << (63
- cluster_bits)) - 1)`, `csize = (entry >> (63 - cluster_bits)) &
((1 << cluster_bits) - 1)`, byte-granular size) decompress with
**raw DEFLATE** (`windowBits -12`, no zlib header/trailer) —
`zlib.decompress` fails on them, `decompressobj(-12)` succeeds. This
is *not* the same as QCOW2 compressed clusters, which try zlib
framing first and fall back.

#### Why This Matters

Reusing QCOW2's zlib-first two-try decompression helper on QCOW1
data would either misdecompress or spuriously fail every compressed
QCOW1 cluster; the two formats' compressed-cluster encodings look
similar (DEFLATE-family) but are not byte-compatible framings.

#### instar Behavior

**Always**: the QCOW1 reader arm inflates compressed clusters via
`miniz_oxide` with the zlib-header-parsing flag *off* — raw
DEFLATE only, matching qemu's `qcow_decompress_cluster` — never the
QCOW2 crate's zlib-first helper. Inflate failure is a clean guest
failure, not a panic. Pinned by the `qcow1-compressed` fixture (a
`convert -c` twin of `qcow1-data`), which compares identical to its
uncompressed twin and round-trips to the same raw md5; the fixture
generator tolerates qemu's `convert -c -O qcow` exit-1-despite-valid-
output quirk (see below) by validating via roundtrip instead of exit
code.

### Odd Header Sizes Truncate Down (Opposite of VDI's Round-Up)

**Classification: Safe Quirk**

#### Observed Behavior

qemu's `qcow_open` computes `total_sectors = size / 512` (integer
division) and uses `total_sectors * 512` as the effective virtual
size wherever the header's `size` field is not a multiple of 512 —
an odd size **truncates down**. Byte-patch verified and
version-stable (6.0.0 through 10.2.0): `size = 1048577` reports and
converts as `1048576`. This is the opposite of VDI's rule (an odd
`disk_size` rounds **up** to the next 512-byte multiple).

#### Why This Matters

A reader that rounded QCOW1 sizes up (following the VDI precedent
uncritically) would report and convert one sector's worth of extra
data qemu never exposes — silent size drift on a header a real tool
would never produce via `create`, but which a byte-patched or
hand-authored image can carry.

#### instar Behavior

**Always**: the QCOW1 parser truncates the header `size` down to the
nearest 512-byte boundary before treating it as virtual size,
matching qemu exactly. Pinned by the `qcow1-odd-size` fixture (`size`
byte-patched to 1048577; instar and qemu both report/convert
1048576). oslo.utils diverges here — it reads the header `size` field
verbatim (1048577) with no truncation, recorded as a genuine vsize
divergence in `docs/format-coverage.md`'s oslo cross-validation
table.

### Past-EOF Zero-Fill, Except a Truncated L1/L2 Table Read

**Classification: Safe Quirk** (with one unpinned adversarial corner)

#### Observed Behavior

qemu zero-fills any portion of a DATA cluster read that lands past
EOF or in a truncated file, on every qemu version — unlike Parallels,
there is no 8.1.x-style regression window here; this behaviour is
version-stable 6.0.0 through 10.2.0. Separately, qemu also
zero-fills a read that lands in a **truncated or past-EOF L1/L2
TABLE** (not just a data cluster) — the table lookup itself silently
reads zeros for the missing bytes rather than erroring.

#### Why This Matters

instar's reader matches qemu for the DATA-cluster case, which is the
one every safe and malformed fixture exercises. The TABLE case is a
narrower, more adversarial corner: it requires a file truncated
partway through the L1 or L2 metadata itself, which no shipped
`qemu-img` tooling produces and no phase-4 fixture constructs.

#### instar Behavior

**Data clusters, always**: the QCOW1 reader arm zero-fills any
allocated-but-past-EOF or straddling read, capacity-clamped exactly
like the VDI/Parallels arms. Pinned by the `qcow1-past-eof` fixture
(one data cluster's L2 entry redirected ~4 GiB past EOF; the other
clusters stay intact).

**Truncated L1/L2 table reads**: instar's reader returns a **clean
failure** rather than zero-filling — a documented divergence from
qemu's more permissive table-read behaviour. No fixture pins this
corner; it carries the same unpinned posture as the equivalent
truncated-table-read corners already recorded for VDI and Parallels
(their block-map/BAT reads have the same instar-refuses-qemu-
zero-fills shape). Revisit only if a real adversarial-table fixture
need appears.

### Malformed QCOW1 Images: `info` Falls Back to an Empty Default (Diverges from VDI/Parallels' Leniency)

**Classification: Safe Quirk** (documented posture difference, not
a defect)

#### Observed Behavior

VDI and Parallels' `info` parsers are lenient on malformed input by
design: they check only the magic/version and report best-effort
nonzero fields even when a malformed field (block size, tracks,
BAT/catalog size) would cause the *reader* to refuse. QCOW1's new
`info` arm is stricter: it validates `cluster_bits`, `l2_bits`,
`size`, `crypt_method`, and the backing-name length — the same rules
the reader enforces — and falls back to an **empty default** (virtual
size 0) on any validation failure, rather than reporting whatever
partial fields it could still read.

#### Why This Matters

This is a deliberate, pinned posture choice rather than an oversight:
all five malformed QCOW1 fixtures still detect correctly (`format:
qcow`, exit 0) but report virtual size 0, and `convert`/`dd` then
refuse cleanly on the zero-size input rather than attempting a read
that the reader would refuse anyway. The behaviour is consistent and
tested, just a different leniency posture from VDI/Parallels — worth
calling out explicitly so a future phase doesn't assume all
detect-then-refuse formats behave identically on malformed input.

#### instar Behavior

**Always, pinned in `test_adversarial`**: all five malformed QCOW1
fixtures (`qcow1-bad-cluster-bits`, `qcow1-bad-l2-bits`,
`qcow1-huge-size`, `qcow1-crypt-invalid`,
`qcow1-backing-name-too-long`) get `info` exit 0, format `"qcow"`,
virtual size 0 — the info arm's parse validates the same fields the
reader does and falls back to the empty default on failure.
`convert`/`compare`/`dd` then refuse cleanly on the zero virtual
size, never hanging or misreading.

### `check`, `map`, and `measure`: a Wording Coincidence and Two Recorded Divergences

**Classification: Safe Quirk**

#### Observed Behavior

`instar check` on a QCOW1 image exits 63 with "This image format
(qcow) does not support checks" — and, unlike the Parallels case
(where qemu's own check crashes on newer versions), this is **actual
parity with qemu**: qemu's own qcow driver refuses `qemu-img check`
outright on every version (it has no qcow1 check implementation),
just with a shorter message that omits the `"(qcow)"` parenthetical
instar's generic not-supported wording always includes. `map` and
`measure` remain refusals in instar, but qemu-img actually **supports
both** on qcow1 sources — a deliberate divergence, not an accident,
recorded as master-plan future work alongside the existing VDI and
Parallels map/measure gaps.

#### instar Behavior

**Unchanged by this phase**: `check` exits 63 (the wording difference
is cosmetic and not worth chasing — only qemu-img's own error strings
are pinned as version-stable, per the established policy for
malformed-fixture messages elsewhere in this document). `map` and
`measure` stay clean refusals on qcow1 input, fuzzer-gated like the
VDI/Parallels refusals, tracked as future work rather than this
phase's scope.

### Two External-Tool Oddities: qemu's `convert -c` Exit Code and oslo's qcow1→qcow2 Detection

**Classification: Safe Quirk** (both are properties of the external
tools, not instar defects)

#### Observed Behavior

`qemu-img convert -c -O qcow` writes a **valid** compressed qcow1
image but **exits 1 with empty stderr**, on every qemu version
spot-checked — a real qemu quirk on the *output* side (instar does
not write qcow, so this never affects instar's own behaviour
directly, only the testdata fixture generator that has to create the
`qcow1-compressed` fixture using qemu-img). Separately, oslo.utils
(git master) detects qcow1 as `"qcow2"` purely by magic — it never
consults the version field, matching instar's own pre-fix bug almost
exactly — with the *virtual size* agreeing regardless (the `size` u64
field sits at the same offset 24 in both formats' headers), and
`safety_check()` raises `SafetyCheckFailed` since there is no
`get_inspector('qcow')`.

#### Why This Matters

Both are worth recording so a future differential-fuzz or baseline
regeneration doesn't misread rc=1 or oslo's qcow2 report as a new
instar regression.

#### instar Behavior

**Fixture generation and differential fuzzing tolerate rc 0/1** for
`qemu-img convert -c -O qcow` and verify the output independently
(roundtrip md5 / re-read), rather than gating on the exit code.
**oslo cross-validation** records `KNOWN_FORMAT_DIVERGENCES` entries
(`'qcow'`, `'qcow2'`) for every safe QCOW1 fixture and handles the
`SafetyCheckFailed` exception per the existing test flow, with no
`KNOWN_VSIZE_DIVERGENCES` entry needed for the safe fixtures (only
`qcow1-odd-size` diverges on vsize, per the odd-size section above)
— confirmed live against real oslo.utils during step 4d.

## DMG convert-from (read path)

The PLAN-f workormat-coverage.md` graduated DMG (Apple UDIF,
detect + info only now) to a full read format for convert,
compare, dd, and bench, via a new `src/crates/dmg/` parser crate
wired into the qcow2 crate's chain reader (the same pattern VDI,
Parallels, and QCOW1 use) — commits `f53817f` (plan), `e77b30b`
(plan correction), `71a20d9` (5a crate), `ba78d35` (5b reader arm),
`ede8fd4` (5c graduation), `9033505` (5c pins), `a0ea960` (5d
manifest/oslo), `8904592` (5f fuzz), `9d8111c` (5e integration
matrix). DMG is the fifth format-coverage read path and the first
whose error model *inverts* every prior phase's zero-fill posture.
See
[docs/plans/PLAN-format-coverage-phase-05-dmg-read.md](/components/instar/plans/PLAN-format-coverage-phase-05-dmg-read/)
for the full design and findings.

### EIO Parity: DMG Reads ERROR Where Every Other Format Zero-Fills

**Classification: Safe Quirk** (a deliberate posture inversion, not
an inconsistency — matches qemu exactly)

#### Observed Behavior

Every prior read-only format in this document (VDI, Parallels,
QCOW1) treats an unallocated or past-EOF region as **zero-fill**: a
block-map miss, a discarded entry, or a read that lands past the
declared capacity all resolve to zeros, matching qemu. DMG is the
opposite. qemu's `block/dmg.c` binary-searches a sector into its
chunk table and, when no chunk covers it, **fails the read** rather
than returning zeros — and the same applies to a raw chunk whose
bytes lie past EOF (a short `pread` becomes an I/O error) and to
truncated compressed data. This covers three distinct gap shapes:
a between-chunk hole in the mish table, a chunk dropped at open
(an unsupported codec, in qemu's build — see below), and the
tail of the virtual disk beyond mish coverage when the koly
trailer's `SectorCount` exceeds it (see the koly-wins section
below). All three are read ERRORS on real qemu, verified across
the static/host qemu-img matrix.

#### Why This Matters

Reusing the VDI/Parallels/QCOW1 arms' zero-fill shape for DMG would
have been a **silent data-integrity divergence from qemu**, not a
harmless simplification: a caller comparing instar's converted
output against `qemu-img convert` would see instar quietly
synthesise zeros for guest sectors that qemu-img explicitly refuses
to produce at all. Because DMG's chunk table is attacker-shaped
input (a plist string-scan and a lenient base64 decoder — see
below), an image can trivially manufacture gaps.

#### instar Behavior

**Always, prominently commented in the reader arm** (per the plan's
explicit instruction to comment this inversion): a sector covered by
no chunk (gap, dropped/refused chunk, or the koly-wins tail), a raw
span whose bytes are unavailable (past EOF or truncated), or a zlib
span with truncated compressed data all make the reader return
`false` — the same clean-failure signal QCOW1's truncated-table
corner uses, propagated up through `convert`/`compare`/`dd`/`bench`
as a non-zero exit — never zeros. Overlapping chunks are **not**
treated as an error: qemu's binary search deterministically resolves
to whichever chunk the search lands on first, and instar's sorted-
table walk matches that behaviour exactly (no shipped fixture
exercises an overlap; it is a recorded corner, matching the
established policy elsewhere in this document for un-fixtured
adversarial shapes). Pinned by the `dmg-gap` fixture: `info`
succeeds on both instar and qemu at virtual size 8192 bytes (a koly
`SectorCount` of 16 against 8 sectors of real mish coverage), while
`convert`/`dd` FAIL cleanly on **both** sides — qemu with an I/O
error on the uncovered tail, instar with its own clean gap refusal —
an error-parity fixture, deliberately never a byte-parity one.

### The qemu Zero-Chunk NULL-Deref Crash — instar Refuses Cleanly Instead

**Classification: closes what would otherwise be an Unsafe Quirk**
(instar does not mirror qemu's crash; a candidate upstream report)

#### Observed Behavior

An image with a structurally valid koly trailer but **zero parsed
chunks** — a corrupted mish magic inside an otherwise well-formed
`<data>` block, a base64 blob that decodes to garbage, or a plist
with no `<data>` blocks at all — makes qemu's `dmg_open` build an
empty `sectors[]` table. `info` never touches this table and
succeeds normally (rc 0), but **any read dereferences the NULL
table pointer and SIGSEGVs**, verified universal: static qemu-img
6.0.0, static 10.2.0, and host 10.0.11 all crash with rc 139 on
`convert`. This is distinct from a simpler-looking case that is
**not** the crash: `dmg-no-chunk-table` (both `RsrcForkLength` and
`XMLLength` are zero, so qemu has no chunk-table *source* at all)
never reaches table-build and fails with a clean `EINVAL` at open on
every version — qemu's ordinary, non-crashing refusal path. The
actual crash requires a *source* that parses successfully down to
zero chunks, which the plan's step-5d correction identified and
shipped as the dedicated `dmg-empty-table` fixture (a well-formed
XML plist whose single `<data>` block decodes with a corrupted mish
magic).

#### Why This Matters

instar's sandboxed guest reads untrusted disk images by design; a
crash-on-read defect in the reference tool is exactly the class of
input instar's KVM isolation exists to survive without imitating.
Mirroring qemu's crash would have been actively worse than refusing
cleanly — a caller feeding instar a `dmg-empty-table`-shaped image
should get a clean non-zero exit, not a segfault.

#### instar Behavior

**Always**: `DmgState::init` refuses at reader init the moment the
assembled chunk table has zero entries (`DmgRefusal::EmptyChunkTable`
in `src/crates/dmg/src/lib.rs`), before any read is attempted —
convert/compare/dd/bench all fail cleanly and immediately, with no
crash on any input. Pinned by `dmg-empty-table` (`skip_qemu_img`,
since qemu crashes on convert and no baseline can exist) and kept
distinct in the manifest and in `test_adversarial.py` from
`dmg-no-chunk-table`'s ordinary EINVAL shape. Recorded as
master-plan future work: reporting the qemu NULL-deref crash
upstream (`docs/plans/PLAN-format-coverage.md`, "Future work").

### Bounded-Memory Capacity Caps: A Documented Divergence from qemu's Larger Legal Range

**Classification: Safe Quirk**

#### Observed Behavior

qemu's own per-chunk limits allow a compressed chunk up to 64 MiB
(`DMG_LENGTHS_MAX`) and an uncompressed span up to 64 MiB
(`DMG_SECTORCOUNTS_MAX`, 131072 sectors; zero/ignore chunks are
exempt from this cap). instar's guest sandbox has a fixed, much
smaller scratch budget, so those qemu-legal sizes cannot always be
staged. instar layers its own, smaller, typed caps distinct from
qemu's: the staged plist/resource-fork region is capped at 1 MiB
(real plists are KBs; qemu's own cap is 16 MiB), the chunk table at
32768 entries (~1 MiB of scratch, covering ~32 GiB of default
1 MiB-chunk UDZO output), and per-chunk staging at 4096 sectors
(2 MiB) for the uncompressed side. hdiutil's default UDZO chunk size
is 1 MiB, so real-world images fit with 2x headroom.

#### Why This Matters

A chunk that is entirely legal under qemu's own rules can still
exceed instar's staging budget — a genuine, unavoidable capacity
divergence rather than a bug, and one that needed an explicit
fixture so it reads as "documented" rather than "silently wrong."

#### instar Behavior

**Always**: a chunk whose `comp_len` or `sector_count` fits under
qemu's own limits but exceeds instar's smaller staging caps gets a
typed refusal (`dmg: chunk exceeds staging cap`) distinct from both
qemu's own cap-refusal messages and instar's codec refusals. Pinned
by `dmg-overcap-chunk` (one zlib chunk, `sector_count` 8192 = 4 MiB
uncompressed — under qemu's 131072-sector cap but over instar's
4096-sector cap): **qemu converts it fine on every version** (md5
`dd8d16c0893059dd98d1a3bf1f8675bd`), while instar refuses typed —
`skip_qemu_img` in the manifest, with an explicit divergence note.
Separately, `dmg-chunk-len-over` (comp_len 64 MiB + 1) and
`dmg-sc-over` (sector_count 131073) exceed *qemu's own* limits and
are refused by both tools — qemu at open with the exact recorded
strings ("length 67108865 for chunk 0 is larger than max
(67108864)"; "sector count 131073 for chunk 0 is larger than max
(131072)"), instar at reader init with its own typed message; these
two are ordinary cross-tool refusal parity, not a capacity
divergence.

### Codec Support: Typed Refusals vs qemu's Build-Dependent Bzip2/lzfse/ADC

**Classification: Safe Quirk** (a deliberate scope decision — no
single qemu parity target exists for these codecs anyway)

#### Observed Behavior

DMG chunk codec support is compile-flag dependent across the qemu-img
matrix: bzip2 (UDBZ, `0x80000006`) decodes only on static 6.0.0 and
host 10.0.11; every other static build in the matrix (8.2.0, 10.2.0,
...) lacks the module, opens with a "dmg-bzip2 module is missing"
warning (from 7.2.0 on; 6.0.0 emits none), and the chunk is dropped
from the table, producing a gap that reads EIO. lzfse (ULFO,
`0x80000007`) has no working module anywhere in the tested matrix —
always dropped, always EIO. ADC (`0x80000004`) is enum-named in
qemu's source but **never implemented** by any qemu version — always
dropped, always EIO, with a generic "unknown type 80000004" warning
from 7.2.0 on. zstd (`0x80000008`) and any other unrecognised type
code follow the same drop-then-EIO shape.

#### Why This Matters

Because qemu's own codec support is build-dependent, there is no
single "qemu converts this" oracle to byte-match for bzip2/lzfse/ADC
chunks — implementing decode support for any of them would still
diverge from *some* qemu build in the matrix. The plan's chosen scope
(zero/raw/ignore/zlib only, with typed refusals for the rest) sidesteps
that by making the divergence explicit and self-describing rather than
mimicking one arbitrary qemu build's behaviour.

#### instar Behavior

**Always**: an unsupported or unknown chunk type gets a typed refusal
at reader init naming the exact code (`dmg: unsupported chunk codec
0x80000006` for bzip2, `0x80000007` for lzfse, `0x80000004` for ADC,
and so on for any other unrecognised code), rather than qemu's
drop-then-gap-then-EIO shape. Comment (`0x7ffffffe`) and terminator
(`0xffffffff`) entries are still dropped silently, matching qemu.
Pinned by `dmg-codec-bzip2`, `dmg-codec-lzfse`, and `dmg-codec-adc`
(all `skip_qemu_img`, with the per-version build-dependence recorded
honestly in each fixture's manifest description rather than
asserting one qemu build as the oracle). bzip2/lzfse/ADC decode
support is recorded as master-plan future work.

### Chunk-Table Source: Both the XML-Plist and the Old Resource-Fork Paths, with Lenient (glib-Parity) Base64

**Classification: Safe Quirk**

#### Observed Behavior

qemu supports chunk-table discovery from **either** of two koly-
referenced regions: the modern XML plist (`XMLOffset`/`XMLLength`)
or the older Mac OS resource fork
(`RsrcForkOffset`/`RsrcForkLength`) — path selection is
`RsrcForkLength != 0` first, else `XMLLength != 0`, else `EINVAL`.
Plist parsing is **not** real XML parsing: qemu `strstr`s every
`<data>…</data>` span and base64-decodes each block with glib's
**lenient** decoder, which silently skips invalid characters rather
than erroring; the only well-formedness requirement is a matching
`</data>` — no `<key>blkx</key>` or plist schema validation at all. A
decoded block is accepted as a mish table only if it carries the
mish magic and is at least 244 bytes (the 204-byte header plus one
40-byte entry); everything else — including a block whose base64 was
mostly garbage — is silently ignored, not an error.

#### Why This Matters

Real-world DMGs from both eras exist, so read parity requires
supporting both table sources, not just the modern one. The lenient
base64 semantics matter for parity on real (and adversarial) images:
a strict base64 decoder would reject blocks qemu accepts, and would
accept blocks (or reject them) differently than qemu on hand-crafted
adversarial input — mismatching qemu's actual attack surface.

#### instar Behavior

**Always**: `src/crates/dmg/` implements both chunk-table paths —
the XML-plist `<data>` string scan plus a byte-for-byte port of
glib's lenient base64 (invalid characters skipped, never erroring;
a missing `</data>` is the one case that is "malformed XML" and
refused), and the older resource-fork walk (`u32 rsrc_data_offset`,
`u32 count`, then `[u32 size][mish]` resources). Pinned by
`dmg-rsrc-fork` (the resource-fork path, no XML) alongside
`dmg-simple`/`dmg-mixed`/`dmg-multipart` (the XML-plist path) — all
four are byte-parity convert fixtures.

### koly `SectorCount` Always Wins for Virtual Size

**Classification: Safe Quirk**

#### Observed Behavior

The koly trailer's `SectorCount` field is the sole source of the
reported and converted virtual size — the mish chunk table's actual
sector coverage is irrelevant to sizing. When `SectorCount` exceeds
what the assembled chunk table covers, the uncovered tail is not
truncated or resized away; it becomes exactly the gap shape the EIO
Parity section above describes, read as an error rather than
silently shrinking the disk to the mish-covered extent.

#### Why This Matters

A reader that derived virtual size from mish coverage instead of the
trailer would silently under-report DMGs whose SectorCount is
legitimately larger than any single mish block's range (e.g. a
disk with declared-but-unwritten trailing space) — a data-shape
divergence from qemu, not just a sizing cosmetic.

#### instar Behavior

**Always**: virtual size is `SectorCount * 512`, computed from the
koly trailer alone (reusing the phase-1 shared trailer helpers), with
no cross-check against mish coverage at size-computation time. Pinned
by `dmg-gap`, which deliberately declares a SectorCount larger than
its single mish block's coverage: `info` reports the trailer-derived
8192-byte virtual size successfully on both tools, while `convert`
fails on the uncovered tail on both tools (see the EIO Parity section
above).

### Probe Divergence Extended to convert: The Extensionless-DMG Divergence

**Classification: Safe Quirk** (extends the phase-1 detection
divergence into a real convert-time behavioural difference)

#### Observed Behavior

It is already recorded above that qemu-img's DMG probe is almost
entirely `.dmg`-filename-extension based, while instar detects DMG
by content (the koly-trailer scan) regardless of filename (see
"DMG Detection: Content-Based Trailer Probing vs qemu's Filename
Extension" above). Previously, this divergence was purely a
detection-report difference, since convert/compare/dd refused all
detected-but-unsupported formats via the #444 gate either way. Now
that DMG has a real read path, the divergence has a real behavioural
consequence: a copy of a valid DMG renamed without its `.dmg` suffix
is a **different converted output** on the two tools. Under qemu-img
(no `-f` given), the missing extension makes the probe fall through
to raw, and `convert` emits the container's raw bytes — koly
trailer, XML plist, and all — as if it were the virtual disk.
Under instar, the koly-trailer scan still finds the trailer
regardless of filename, so `convert` emits the real, decoded guest
disk content.

#### Why This Matters

This is the sharpest illustration in the whole DMG phase of *why*
instar's detection charter is content-based rather than extension-
based (see the phase-1 rationale) — an extension is not a
security-relevant signal, and two tools disagreeing about what a
byte-identical file actually *is* is exactly the class of ambiguity
a sandboxed converter should resolve in the more conservative
direction (treating it as the format its content proves it to be).

#### instar Behavior

**Always, pinned by a dedicated test**
(`test_convert_dmg_extensionless_divergence`): both behaviours are
recorded, not just instar's — the test copies `dmg-simple` to an
extensionless filename, runs `qemu-img convert` with no `-f` (raw
pass-through of the 11776-byte container) and `instar convert` (the
real 4 MiB decoded disk), and asserts both succeed with their
respective, deliberately different outputs. No flag makes instar
adopt qemu's extension probe for DMG, consistent with the phase-1
decision (OQ2) that `--unsafe-quirks` does not touch DMG detection.

### `check` Names the Format "(raw)", Not "(dmg)"

**Classification: Safe Quirk**

#### Observed Behavior

Unlike VDI, Parallels, and QCOW1 — whose `check` refusal message
names the real format (`"(vdi)"`, `"(parallels)"`, `"(qcow)"`) because
those formats are header-detected at offset 0, which *is* wired into
`detect_format_from_header` — `check`'s own format dispatch has no
DMG arm and never runs the koly-trailer probe (that probe lives only
in the `info` op's guest chain, as established). So
`instar check` on a DMG image sees the UDIF container as `Raw` and
refuses with `This image format (raw) does not support checks`,
exit 63 — a message naming the *wrong* format, unlike every other
graduated format in this document.

#### Why This Matters

This looks like a defect at first glance (the exit code and general
"not supported" shape are right, but the parenthetical names raw
instead of dmg), except that qemu-img's own `check` on the exact
same DMG **also** exits 63 with "does not support checks" — so the
exit-code and refusal-class parity is genuine; only the specific
wording differs, and qemu's message happens to omit a format name
entirely. Chasing exact wording here would require wiring the koly
probe into `check`'s dispatch for a cosmetic message-text gain with
no functional difference, which is out of scope for this phase (see
the retained-pass-through section above).

#### instar Behavior

**Unchanged, pinned by `TestCheckDmgRefusal`**: `check` on a DMG
image (with or without `--output json`) exits 63 with `This image
format (raw) does not support checks` — genuine rc parity with
qemu-img's own dmg-check refusal, with only the named format
differing, a documented consequence of `check` not being a
chain-discovery consumer (see "DMG Pass-Through as Raw in the
In-Place Ops" above, which covers `check` alongside `map`/`measure`/
`resize`). Real DMG check support is future work.

### `dmg-sectorcount-negative`: A Pre-Existing Unknown-Format Pass-Through, Now Pinned

**Classification: Safe Quirk** (a pre-existing, deliberately
exempted corner, newly pinned rather than newly introduced)

#### Observed Behavior

A koly trailer whose `SectorCount` has its top bit set (a negative
value when read signed) makes the shared trailer helper's detection
collapse to `unknown` rather than `dmg` — `dmg_sector_count` treats a
negative total as "not a DMG after all," so `discover_backing_chain`
never routes this image through the DMG reader at all. Because
`unknown`/raw-shaped detections are the deliberate exemption the
issue-#444 gate already carves out (see "Detect-Only Format Refusal
in convert / compare / dd (#444)" above), `dmg-sectorcount-negative`
passes straight through as a raw read of its small container — no
gate refusal, no DMG reader involvement at all.

#### Why This Matters

This behaviour predates the DMG read work (the detection collapse is original
logic), but the graduation of DMG to a real read format makes
it worth pinning explicitly: without a test, a future change to the
#444 gate or the DMG reader's init path could accidentally start
routing this fixture through the DMG reader (which would then need
its own opinion about a negative SectorCount) without anyone
noticing the behavioural change.

#### instar Behavior

**Unchanged, newly pinned**: `dmg-sectorcount-negative` reads as raw
pass-through on both `convert` and `dd` — the unknown-format
exemption applies exactly as it does for any other raw-shaped
detection, with no DMG-specific code path ever entered. `info` still
reports whatever the trailer helper's `unknown`-collapsed view
produces (unchanged). This is treated as an accepted,
pre-existing corner of the #444 gate's design, not a phase-5 defect.

### Typed Refusal Strings Are Guest-Side Debug Output, Not the User-Facing Failure

**Classification: Safe Quirk** (consistent with the VDI-era
precedent for adversarial pins)

#### Observed Behavior

Every DMG-specific refusal string this section describes (e.g.
`dmg: unsupported chunk codec 0x80000006`, `dmg: chunk exceeds
staging cap`, `dmg: empty chunk table`) is written via the guest's
debug-print channel at the point of refusal — it is diagnostic
output, not the message a caller actually receives. The user-facing
failure for every one of these fixtures is the generic "convert
operation failed" wrapper the host CLI already emits for any guest-
side refusal, regardless of the specific reason.

#### Why This Matters

This is the same shape already established for VDI's adversarial
pins: asserting on the specific debug string would couple tests to
an internal implementation detail that is not part of instar's
actual CLI contract, while asserting on rc + clean termination
matches what a real caller can observe and rely on.

#### instar Behavior

**Always, consistent across every DMG adversarial fixture**: the
typed guest-side strings are recorded as documentation in each
fixture's `expected_error` field in `tests/manifest.json` (so the
exact reason is discoverable and pinned at the source level), while
the adversarial test assertions themselves check rc and clean
termination — no hang, no crash, no partial output — per the
VDI-precedent posture, not string-matching the debug text.

### Scratch Design: Per-Device Slots, Any Chain Position, and Bit-63 Cache Keying

**Classification: Safe Quirk** (an implementation-detail note, not a
qemu-parity divergence)

#### Observed Behavior

Each DMG device in a chain gets a fixed `DMG_REQUIRED_SCRATCH`
(~3.25 MiB: a 1.25 MiB persistent chunk table plus a 2 MiB transient
plist/decode region) slot carved from the caller's reserved scratch
region. `convert` reuses its existing staging buffer for the
transient init suffix, so the net addition to convert's memory
layout is only the persistent 1.25 MiB table region, not the full
3.25 MiB. `compare` reserves two such slots (one per side of the
comparison, since either side may be a DMG). `bench` and `rebase`
only need the write-only overlay-scratch shape, since neither reads
two DMG sides at once. DMG has no backing-file field of its own — a
DMG image is always a chain-leaf — but the DMG *reader* itself is
usable at ANY position within a mixed-format chain, proven by a
`qcow2 -F dmg` overlay-over-DMG test whose converted output converges
byte-for-byte with qemu-img's own `-b ... -F dmg` chain. The
decompressed-chunk staging cache is keyed by the chunk's host file
offset with bit 63 OR'd in
(`cache_key = host_offset | (1u64 << 63)`) — safe because every real
file offset is well under 2^63, so tagging with the top bit yields a
key space that can never collide with the qcow2/vmdk staging cache's
own offset-keyed entries.

#### Why This Matters

This is purely an implementation note (no qemu-parity claim is being
made here), documented because it is a nontrivial memory-budget and
correctness design that a future consumer of the DMG reader (or a
sixth format-coverage phase) needs to understand before adding
another per-device scratch consumer to the same reserved region.

#### instar Behavior

**Internal to the reader arm, not user-visible**: the scratch layout
and bit-63 cache-keying scheme are implemented in
`src/crates/qcow2/src/lib.rs` (the chain-reader integration) and
`src/crates/dmg/src/lib.rs` (the parser crate itself), with the
convert memory-layout compile-time assertion
(`src/operations/convert/src/main.rs`) extended to account for the
new region. Binary size grew by roughly +9.5 KB per DMG-enabled
guest operation (convert now sits at roughly 41% of the 768 KB
per-operation cap, per `make check-binary-sizes`).

## QED read-refusal as policy

The PLAN-f workormat-coverage.md` resolved the master plan's Open
question 1 — does QED get a read path, like VDI/Parallels/QCOW1/DMG in
earlier work, or a principled, documented, fully-tested refusal? — by
choosing refusal as deliberate policy, not a read path. Step 6a added
QED-named refusal pins for every op that lacked one (check, map,
measure, bench, resize, rebase, commit, amend, snapshot, bitmap;
convert/compare/dd/oslo were already pinned) and reconciled a stale,
unconsumed testdata baseline set; step 6b is this documentation
record. Commits: `3fd48e6` (pins, instar), `cecb16565a` (baseline
retirement, instar-testdata main). See
[docs/plans/PLAN-format-coverage-phase-06-qed.md](/components/instar/plans/PLAN-format-coverage-phase-06-qed/)
for the full decision record and findings.

### QED Read-Refusal Is Deliberate Policy, Not a Parity Gap

**Classification: Safe Quirk** (a recorded scope decision, not a
defect)

#### Observed Behavior

Every prior format-coverage phase (2-5) graduated a detect-only format
to a full read path. QED does not get one. `instar info` reads QED
correctly (byte-parity with qemu-img, human and JSON); every other
subcommand refuses it cleanly with a typed message and no file
modification, verified by byte-hash after every mutating op. The
per-op audit behind this decision (recorded in the phase-6 plan's
Situation section) found **zero dangerous cases**: no raw
pass-through, no crash, no silent-wrong output for any of the fifteen
subcommands.

#### Why This Matters

Three grounded facts justify refusal over a read path:

1. **Nil demand.** QED was a short-lived qcow2 alternative that never
   saw wide deployment; no user demand for reading QED archives has
   surfaced during five phases of format work.
2. **oslo.utils bans QED outright.** `format_inspector.detect_file_format`
   returns a real `QEDInspector`, whose safety check then raises
   `SafetyCheckFailed: ... banned` ("This file format is not
   allowed") — a stronger ecosystem statement than the DMG/VDI/
   Parallels/QCOW1 case, where oslo merely *lacks* an inspector and
   instar deliberately reads what oslo cannot. For QED, oslo has an
   inspector and refuses by policy; instar's refusal aligns with that
   stance rather than filling a gap oslo doesn't have.
3. **The refusal is already complete and safe.** The audit's
   zero-dangerous-cases result means finishing the job costs only
   test pins and documentation, not new parser code.

**Revisit criteria**, recorded so the decision is cheap to reverse: a
real user request to read QED input, or QED images surfacing in a
workload instar serves. The phase-6 plan preserves a path-(b) sketch
(a qcow1-class reader — 68-byte LE header, two-level L1/L2 tables, no
compression/encryption) as the starting point if that day comes.

#### instar Behavior

**Always**: `info` supports QED; every other subcommand refuses it.
The per-op refusal/divergence table:

| Op | qemu-img on QED | instar on QED | Notes |
|----|------------------|----------------|-------|
| info | Supported (rc 0) | Supported (rc 0), byte-parity | Only fully-supported op |
| convert | Supported (rc 0) | Refused (issue-#444 chain gate) | "input format 'qed' is detected but not supported for reading (detection and info only)"; mid-chain backing position also refused |
| compare | Supported (rc 0) | Refused (chain gate) | Same message shape as convert |
| dd | Supported (rc 0) | Refused (chain gate) | Same message shape as convert |
| bench | Supported (rc 0) | Refused (chain gate) | Same underlying gate as convert, but with no `"bench:"` message prefix — a deviation from the other three ops, and empty stdout |
| check | Supported (rc 0) | Refused, exit 63 | "This image format (qed) does not support checks" — check's own probe DOES see QED's offset-0 magic, so (unlike DMG) it names the real format |
| map | Supported (rc 0) | Refused, exit 1 | "source format unrecognised" |
| measure | Supported (rc 0) | Refused, exit 1 | "source image is unsupported format" |
| resize | Supported (rc 0) | Refused | "format Qed is not supported for in-place resize" — divergence, qemu's QED driver resizes fine |
| rebase | Supported (rc 0) | Refused | "format 'Qed' does not support rebase (qcow2 and vmdk only)" — divergence, qemu rebases QED overlays fine |
| commit | Supported (rc 0) | Refused | "format 'qed' does not support commit (qcow2 and vmdk only)" — divergence, qemu commits QED overlays fine |
| amend | Refused (rc 1) | Refused | qemu: "Format driver 'qed' does not support option amendment"; instar: "only qcow2 images can be amended" — not a divergence |
| snapshot | Refused (rc 1) | Refused | qemu: "Operation not supported"; instar: "snapshot: source is not qcow2" — not a divergence |
| bitmap | Refused (rc 1) | Refused | qemu: "Operation not supported" (no persistent-bitmap store); instar: "not a qcow2 image" — not a divergence |

The convert/compare/dd/bench/check/map/measure/resize/rebase/commit
rows are genuine divergences — qemu-img performs these successfully on
QED (all rc 0, empirically verified against qemu-img 10.0.11), instar
refuses by policy, in the same recorded-divergence class as the
map/measure scope refusals chosen for VDI/Parallels/QCOW1/
DMG. Only the amend/snapshot/bitmap rows are **not** divergences:
qemu-img itself refuses those on QED (no amend driver, no internal
snapshots, no persistent-bitmap store), so there instar's refusal
matches qemu's own posture.

### Cosmetic Refusal Inconsistencies, Pinned As-Is

**Classification: Safe Quirk**

#### Observed Behavior

Two wording/exit-code inconsistencies exist across the QED refusal
surface, both pre-existing and orthogonal to this phase's scope:

- `resize` and `rebase` render the Rust `Debug` spelling `"Qed"`
  (capital Q) in their refusal messages, while `commit`, `check`, and
  the chain-gate messages use lowercase `"qed"`.
- `check` exits 63 ("This image format (qed) does not support
  checks"), matching qemu-img's own check-refusal exit code for
  unsupported formats; every other QED refusal in the table above
  exits 1.

#### Why This Matters

Normalising these would touch shared refusal-message code paths used
by every other format's equivalent refusals, for a purely cosmetic,
zero-user-value gain — explicitly out of scope (see the
phase plan's "Out of scope" section).

#### instar Behavior

**Unchanged, pinned as-is with comments** in the step-6a test suites:
`resize`/`rebase`'s `"Qed"` spelling and `check`'s 63-vs-1 exit code
are asserted verbatim, not normalised.

### The qemu-Deprecation Claim Was Wrong — Corrected

**Classification: closes a stale documentation claim** (not a code
behaviour change)

#### Observed Behavior

Earlier drafts of `docs/plans/PLAN-format-coverage.md` (and other
repository docs) described QED as "(deprecated)" in qemu-img. The empirical research found this to be **false**: QED has no entry in any
`deprecated.rst`/`removed-features.rst`, no runtime warning on any op
or qemu version, and `qemu-img create -f qed` still succeeds on
10.2.0. qemu-img reads, writes, checks, maps, measures, and benches
QED normally on every version in the matrix (all rc 0, convert md5
version-stable).

#### Why This Matters

The refusal decision is instar's own scope choice (nil demand +
alignment with oslo.utils' explicit ban), not a response to qemu
sunsetting the format. Documenting QED as "deprecated" would have
implied a removal timeline that does not exist and misattributed the
rationale for instar's refusal.

#### instar Behavior

**Documentation only**: the master plan's Open question 1 carries a
dated RESOLVED addendum correcting the framing (the original question
text itself is left as historical record); other repository docs that
called QED "(deprecated)" without qualification have been corrected
by this phase to state plainly that qemu does not deprecate it. QED
detection and refusal behaviour is unchanged by this correction — it
is a documentation-accuracy fix, not a functional one.

## VHD/VHDX differencing: qemu-parity silent misread, and an asymmetry between the two formats

Recorded by step 1c of
[PLAN-differencing-phase-01-pin.md](/components/instar/plans/PLAN-differencing-phase-01-pin/),
part of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/). Every command
below was re-run on 2026-09-05 against `qemu-img version 10.0.11 (Debian
1:10.0.11+ds-0+deb13u1)`, the instar binary built from `d59cc40`, and
`vhdiinfo 20240509` (Debian's `libvhdi-utils`/`libvhdi1`/`python3-libvhdi`,
all `20240509-2+b1`, used through the `python3-libvhdi` binding — see the
phase 1 plan's step 1a result for why there is no `vhdiexport` CLI). The
differencing chains read below (`fat-differential.vhd`/`.vhdx` and their
parents) are Hyper-V produced, from `log2timeline/dfvfs`'s test corpus, not
generated by this project. Unlike every other divergence in this document,
the read-side behaviour here is a live correctness defect, not a deliberate
scope decision: it is filed as
[issue #547](https://github.com/shakenfist/instar/issues/547) and phase 4 of
the differencing plan turns it into a typed refusal; phases 11-16 then
implement real chain composition. This section records what is true today,
before either of those phases lands.

### qemu-img creates neither differencing VHD nor differencing VHDX

**Classification: Not applicable** (there is no output to diverge from qemu)

#### Observed Behavior

```
$ qemu-img create -f vpc base.vhd 16M
Formatting 'base.vhd', fmt=vpc size=16777216
$ qemu-img create -f vpc -b base.vhd -F vpc child.vhd 16M
qemu-img: child.vhd: Backing file not supported for file format 'vpc'
$ echo $?
1
$ qemu-img create -f vhdx base.vhdx 16M
Formatting 'base.vhdx', fmt=vhdx size=16777216 log_size=1048576 block_size=0
$ qemu-img create -f vhdx -b base.vhdx -F vhdx child.vhdx 16M
qemu-img: child.vhdx: Backing file not supported for file format 'vhdx'
$ echo $?
1
```

#### Why This Matters

There is no qemu-img write path for either format, so nothing about
differencing *output* can be cross-validated against qemu the way every
other instar write path has been. This is why the differencing plan spends
its whole first phase establishing an external oracle (libvhdi, see below)
before writing an emitter at all.

#### instar Behavior

instar does not create differencing output either today: `plan_vhd`
(`src/crates/create/src/lib.rs:767`) and `plan_vhdx` (`:919`) both reject a
backing reference with `CreateError::BackingFileUnsupported`. Not a
divergence — instar matches qemu-img's refusal to create either format,
because it has not yet implemented the differencing emitters this plan
adds.

### The read-side asymmetry: VHD is silently mis-composed, VHDX is refused outright

**Classification: Unsafe Quirk** (silently wrong output, exit 0 — tracked
as issue #547, not deliberate policy)

#### Observed Behavior

`qemu-img info` on a real Hyper-V differencing VHD child reports a plain
image and never mentions a parent:

```
$ qemu-img info fat-differential.vhd
image: fat-differential.vhd
file format: vpc
virtual size: 4 MiB (4194304 bytes)
disk size: 2.08 MiB
cluster_size: 2097152
Child node '/file':
    filename: fat-differential.vhd
    protocol type: file
    file length: 2.08 MiB (2183168 bytes)
    disk size: 2.08 MiB
$ echo $?
0
```

The same command on the VHDX equivalent refuses outright — this is not
merely a silent-read difference, `info` itself cannot open the file:

```
$ qemu-img info fat-differential.vhdx
qemu-img: Could not open 'fat-differential.vhdx': Could not open
'fat-differential.vhdx': Operation not supported
$ echo $?
1
```

`qemu-img convert -O raw` on the VHD child exits 0 and writes 4 MiB composed
as though the parent did not exist:

```
$ qemu-img convert -f vpc -O raw fat-differential.vhd qemu-child-only.raw
$ echo $?
0
$ cmp qemu-child-only.raw fat-composed.raw
qemu-child-only.raw fat-composed.raw differ: byte 1, line 1
```

(`fat-composed.raw` is libvhdi's correct composition of the same chain,
described below.) The crispest demonstration is `file(1)` on the two
outputs. The chain's first block is parent-owned, so a reader that ignores
the parent produces a file with no valid boot sector at all:

```
$ file qemu-child-only.raw
qemu-child-only.raw: data
$ file fat-composed.raw
fat-composed.raw: DOS/MBR boot sector MS-MBR Windows 7 english at offset
0x163 "Invalid partition table" at offset 0x17b "Error loading operating
system" at offset 0x19a "Missing operating system", disk signature
0x2598ade5; partition 1 : ID=0xe, start-CHS (0x80,0,1), end-CHS
(0x3ff,0,1), startsector 128, 6016 sectors
```

So the two formats are not symmetric in qemu: differencing VHD is silently
mis-read (rc 0, wrong data), differencing VHDX is refused (rc 1,
"Operation not supported") at every op including `info`.

#### Why This Matters

A silent, wrong composition that exits 0 is worse than a refusal — it is
exactly the "raw as fallback" shape of unsafe quirk this document otherwise
warns about, except here the wrongness comes from *dropping* data (the
parent's blocks) rather than accepting an unintended input. Anyone piping
`qemu-img convert`'s exit code as a success signal gets corrupted output
with no diagnostic.

#### instar Behavior

instar matches the asymmetry exactly, and the reason is visible in the two
format crates' state-init functions. `VhdState::init`
(`src/crates/vhd/src/lib.rs:578`) accepts `DISK_TYPE_DIFFERENCING` (`4`)
into the state it builds; `VhdxState::init`
(`src/crates/vhdx/src/lib.rs:842`) rejects any image with `has_parent` set,
returning `None` before any op-specific code runs. Every instar op that
reads a VHDX through `VhdxState::init` therefore fails immediately and
generically on a differencing VHDX, while every op that reads a VHD through
`VhdState::init` receives a state that looks like an ordinary dynamic disk
and must decide for itself whether to add a differencing-specific check on
top — most do not (see the per-op table below).

`instar info` and `instar convert -O raw` on the same Hyper-V VHD child
reproduce the qemu behaviour byte for byte:

```
$ instar info fat-differential.vhd
image: fat-differential.vhd
file format: vpc
virtual size: 4 MiB (4194304 bytes)
disk size: 2.08 MiB
cluster_size: 2097152
Child node '/file':
    filename: fat-differential.vhd
    protocol type: file
    file length: 2.08 MiB (2183168 bytes)
    disk size: 2.08 MiB
$ instar convert -O raw fat-differential.vhd instar-child-only.raw
$ echo $?
0
$ cmp instar-child-only.raw qemu-child-only.raw
$ echo $?
0
$ file instar-child-only.raw
instar-child-only.raw: data
```

instar's output is bit-identical to qemu-img's mis-composed output and
`file(1)` calls it unidentifiable `data`, for the same reason: sector 0
belongs to the parent, and neither reader ever looks at it. **instar's
read is qemu-parity, and both are silently wrong rather than right.**

### Per-op behaviour inside instar today

**Classification: Unsafe Quirk** (see above — tracked as issue #547)

#### Observed Behavior

Run against the same Hyper-V `fat-differential.vhd` / `fat-differential.vhdx`
chain. VHDX's blanket rejection at `VhdxState::init` means every VHDX row
below fails for the *same* underlying reason regardless of op; VHD's rows
differ because `VhdState::init` accepts the state and each op makes its own
choice on top of it.

| Op | VHD (differencing) | VHDX (differencing) |
|----|---------------------|----------------------|
| info | Succeeds, rc 0, no parent mentioned | Succeeds, rc 0, no parent mentioned (`info` does not route through `VhdxState::init`'s rejection) |
| map | **Refuses**, rc 1: `"map: source has a backing/parent reference; chain composition is deferred (see PLAN-map.md)"` (`src/operations/map/src/main.rs:459-462`) | Refuses, rc 1: `"map: source format unrecognised"` — a generic message, because `VhdxState::init` already returned `None` before map's own differencing check ever runs |
| check | **Does not refuse.** rc 0, `"No errors were found on the image."` — validates the child as an ordinary dynamic disk | **Refuses**, rc 2: `"1 errors were found on the image."` (debug trace: `"check: VHDX differencing disk unsupported"`, `src/operations/check/src/main.rs:1555`) |
| convert | Does not refuse. rc 0, silently composes without the parent (shown above) | Refuses, rc 1: `Error: "convert operation failed"` |
| compare | Does not refuse. rc 0, `"Images are identical."` when compared against itself | Refuses, rc 1: `"Content mismatch at offset 0!"` even comparing the file against itself, because both reads fail to parse and the comparison falls through to raw bytes |
| dd | Does not refuse. rc 0, copies silently | Refuses, rc 1: `Error: "convert operation failed"` |
| bench | Does not refuse. rc 0, runs the read benchmark | Refuses, rc 1: `Error: "bench: failed to parse the image"` |
| measure | Does not refuse. rc 0, `"required size: 4194304"` etc | Refuses, rc 1: `"measure: source image is unsupported format"` |

#### Why This Matters

The plan's framing — "map refuses, check refuses only the VHDX case,
convert/compare/dd/bench/measure do not refuse" — is only literally true of
the VHD column above. On VHDX every op already fails, because the format
crate rejects differencing state before any op-level logic sees it; the
apparent inconsistency between `check`'s explicit VHDX-only message and the
other ops' generic-sounding VHDX failures comes from the same underlying
`VhdxState::init` rejection being reached by different code paths, not from
five separate policy decisions. VHD is the format actually at risk of
silent data corruption today, since it is the only one of the two accepted
into any op's state at all.

#### instar Behavior

Unchanged pending phase 4. `map`'s VHD-specific refusal
(`src/operations/map/src/main.rs:459-462`) and `check`'s VHDX-specific
refusal (`src/operations/check/src/main.rs:1555`) are the only two
op-level, differencing-aware checks that exist; every other op's VHDX
failure is an accident of `VhdxState::init`'s blanket rejection rather than
a deliberate per-op decision, and every other op's VHD behaviour is the
silent misread described above.

### libvhdi, for contrast

**Classification: Not applicable** (describes a third-party tool, not
instar)

#### Observed Behavior

libvhdi refuses to read a differencing child with no parent attached:

```
$ python3 compose.py out.raw fat-differential.vhd
media_size=4194304 disk_type=4
parent_identifier=5fa21a55-f394-aa4d-9958-1951a67d5540
parent_filename=C:\Projects\dfvfs\test_data\fat-parent.vhd
Traceback (most recent call last):
  ...
OSError: pyvhdi_file_seek_offset: unable to seek offset.
libvhdi_internal_file_seek_offset: invalid file - missing parent file.
libvhdi_file_seek_offset: unable to seek offset.
```

And it enforces the parent identifier when a parent *is* attached — the
wrong one is rejected rather than silently composed against:

```
$ python3 compose.py out.raw fat-differential.vhd ntfs-parent.vhd
Traceback (most recent call last):
  ...
OSError: pyvhdi_file_set_parent: unable to set parent file.
libvhdi_file_set_parent_file: mismatch in identifier.
```

The correct parent composes cleanly and produces the byte-exact,
MBR-bearing image shown above:

```
$ python3 compose.py out.raw fat-differential.vhd fat-parent.vhd
media_size=4194304 disk_type=4
parent_identifier=5fa21a55-f394-aa4d-9958-1951a67d5540
parent_filename=C:\Projects\dfvfs\test_data\fat-parent.vhd
wrote 4194304 bytes to out.raw
$ echo $?
0
```

#### Why This Matters

libvhdi's stance — refuse when there is no parent to compose against,
verify identity when there is one — is the shape of the fix phase 4 and
phases 11-16 move instar toward: a typed refusal now, and identity-checked
composition later. It is quoted here as the contrast that makes plain that
neither qemu-img's nor instar's current behaviour is a reasonable "reading
without a parent" default; it is simply unimplemented composition wearing
the exit code of success.

#### instar Behavior

Not applicable — libvhdi is the oracle referenced by the differencing plan,
not code instar ships. No instar behaviour changes as a result of this
subsection; it exists to give the two rows above something to be compared
against.

## Future Additions

Additional quirks will be documented here as they are discovered during
compatibility testing.
