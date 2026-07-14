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
that resolve through a parent image. instar's phase 1 / 2
walkers report the active layer only and refuse sources
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
different in the `present` field. Phase 6's
`KNOWN_MAP_DIVERGENCES` (`hyperv-dynamic-vhd`,
`virtualpc-vhd`) and phase 8's differential fuzzer
(`MAP_FIELD_SKIPS` in `scripts/differential-fuzz.py`)
both skip the `present` field on vpc sources for this
reason.

### VHDX `PAYLOAD_BLOCK_PARTIALLY_PRESENT` is reported as `data: true`

`qemu-img map` walks the per-sector bitmap for partially-
present VHDX blocks and emits per-sector extents. instar's
phase 1 vhdx walker treats `PARTIALLY_PRESENT` as fully
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
back compressed-cluster L2 entries. instar's phase 1 qcow2
walker classifies compressed clusters as `Data` with the
compressed-payload file offset, but does not carry the
compressed bit through the FFI / protobuf path. The phase 4
renderer emits `compressed: false` for every extent
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
miss in spot-check verification. Phase 6's full baseline
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
`virtual-size` field reported by `qemu-img info`. Phase 8b's
`tests/test_create.py::KNOWN_WRITER_DIVERGENCES` skips every
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
explicitly avoids the divergence — phase 8b's matrix
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
qemu's native behaviour and not a bug in either tool. Phase 7's
baselines were recorded without `-f`, so phase 8's matrix
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
every qemu-img version from 6.0.0 through 10.2.0 (the matrix
phase 10 exercises). instar resizes all three. The phase 10
baselines record qemu's rejection verbatim, both as
documentation of the cross-tool gap and as a tripwire for the
day qemu adds support. Phase 11's `TestResizeConsistency`
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
intent explicit when they pass conflicting flags. Phase 11's
`TestResizeErrorPaths` pins the rejection message.

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
was deferred from phase 2c; the integration matrix carries it
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
preserves the qemu CHS-rounded size in the output. Phase 11's
`TestResizeConsistency` for vhd uses a `>= expected_final_size`
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

The resize guest binary reads via `read_output_sector` (new
in phase 7) and writes via `write_output_sector`, both
dispatching to the output device at MMIO slot 1. The core
init unconditionally probes input device 0; the host
satisfies the probe with a 1-sector tempfile stub that the
resize op never reads, then attaches the real read-write
output backing at slot 1. Mirrors the same pattern
`run_create_nonraw` uses for the same reason. The first
phase-11 integration run surfaced this contract: an earlier
revision attached the output at slot 0, which broke the
guest's `init stage=probe device=output address=0x10001000`
walk. Caught and fixed before phase 11 landed.

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

The extended-L2 half is a live-defect fix: before phase 5
the safe-mode walk misread the 16-byte extended-L2 entries
as 8-byte classic entries and silently corrupted the
overlay's virtual content — exit 0, damage visible only on
read-back (issue
[#431](https://github.com/shakenfist/instar/issues/431),
identified during phase 5 of
PLAN-qcow2-write-infrastructure). The
zstd/unknown-bit half is spec-mandated (the qcow2 spec
requires refusing unknown incompatible bits) and is a
narrowing: the zstd bit is inert when the image contains no
compressed clusters, so such images rebased correctly
before phase 5 and now refuse — the same posture as
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
before phase 5 rebase's staging compacted the nonzero table
entries and indexed them as if dense — silently writing
refcounts into the wrong refblocks (1092 check errors plus
32 leaked clusters at exit 0 in the probe that found it;
the overlay-side rebase sibling of #428; issue
[#430](https://github.com/shakenfist/instar/issues/430),
identified during phase 5 of
PLAN-qcow2-write-infrastructure). qemu-img rebases the same
shape check-clean. The
refusal fires at staging time, before any mutation, and is
byte-idempotent
(`tests/test_rebase.py:TestRebaseOverlayClassification`).

### Overlay staging capacity widened by the phase-5 migration

The migrated safe mode retires the stage-everything model
for existing L2 tables (and with it the growable L2 arena
and its count caps — the #422 hazard class of the arena
clobbering refblock staging is gone by construction).
Overlays whose populated-L2 count previously refused
`ERROR_SCRATCH_TOO_SMALL` at staging time — even when
nothing needed copying — now rebase: the probe exemplar
(cs=512, 64 MiB overlay, 512 populated L2 tables, identical
chains) refused before phase 5 and now succeeds check-clean
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
in the OVERLAY itself are skipped, before and after
phase 5: the skip probe treats any non-zero L2 entry as
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
identified during phase 5 of
PLAN-qcow2-write-infrastructure and explicitly NOT fixed by
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
panic spinning in the panic handler, fixed in phase 2 of
PLAN-qcow2-write-infrastructure (the staged-L2 lookup slice
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
folded into phase 3's ordering contract.

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
an explicit `-b`. Phase 9's matrix and round-trip vmdk
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
Before phase 4 this shape was silently corrupted: the
per-cluster loop masked the compressed entry's offset and
overwrote it in place, destroying the deflate streams of
every virtual cluster packed into that host cluster —
exit 0, `qemu-img check` clean, damage visible only on
read-back (issue
[#427](https://github.com/shakenfist/instar/issues/427),
identified during phase 4 of
PLAN-qcow2-write-infrastructure). qemu-img handles the
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
cleanly, and before phase 4 instar's staging compacted the
nonzero table entries and indexed them as if dense —
silently writing refcounts into the wrong refblocks (2654
check errors in the probe that found it; issue
[#428](https://github.com/shakenfist/instar/issues/428),
identified during phase 4 of
PLAN-qcow2-write-infrastructure). qemu-img
commits into the same shape check-clean. The sparse-table
refusal fires at staging time, before any mutation; the
other error-17 shapes are classification refusals with the
same scaffolding posture as the compressed-cluster refusal
above.

### Backing staging capacity widened by the phase-4 migration

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
refcount-growth generalization (phase 6).

### Unaligned virtual sizes commit cleanly

Images whose `virtual_size` is not a multiple of the
cluster size commit byte-identically to qemu-img's
observed tail behaviour, including when the backing has
its own backing file. The final (tail) cluster's write is
clamped to `virtual_size` and classifies as full coverage
in `crates/qcow2-write` — bytes beyond end-of-virtual-size
are not virtual content, so the beyond-EOV zero-fill is
the correct pre-image regardless of backing. Probed
empirically during phase 4: tail bytes past EOV are zeros
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
consumer after commit (phase 4) and rebase (phase 5). The read
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
cluster reached through the COW read (Variant B) was, at phase
6, still mis-filled: a read-path defect in `crates/qcow2`'s
`cluster_lookup`
([#432](https://github.com/shakenfist/instar/issues/432)), not
fixed by phase 6.

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
the collision example. (Before PLAN-snapshot phase 14, instar
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
snapshot table and exits 0. Before phase 9, instar's clap ArgGroup
had `required = true`, so the bare form produced a clap usage error
(exit 2). Phase 9 fixes this: the ArgGroup is now `required = false`,
and `run_snapshot` routes an absent mode flag to the real list path
(`run_snapshot_list`), producing byte-identical output to the
explicit `-l` form.

### `--force-share` (`-U`) is list-only (D1)

`qemu-img` refuses `-U` combined with any mutating mode (`-c`, `-d`,
`-a`) with exit 1 and the message:

```
qemu-img: Could not open 'IMAGE': force-share=on can only be used with read-only images
```

Before phase 9, instar accepted `-U` with mutating modes and
performed the mutation (the flag was plumbed to the guest but
unenforced at the host). Phase 9 adds a host-side gate in
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
implemented so far. The phase 6 note ("`-q` has no visible effect on
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
by PLAN-snapshot phase 13's differential fuzzer on its first
smoke run — the phase 10/11 fixture names were all ASCII, where
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
reads. Unreachable in the phase 6–8 byte-identity matrices
(their tables always landed in fresh zero clusters); found by
the phase 13 differential fuzzer, whose comparator zeroes the
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

### Zero `date_sec` renders the epoch (fixed in phase 14)

For a snapshot-table entry whose `date_sec` is 0, `instar
snapshot -l` renders the Unix epoch in local time
(`1970-01-01 00:00:00` under `TZ=UTC`), byte-identical to
`qemu-img snapshot -l`, which feeds 0 through `localtime` like
any other value. instar originally early-returned a blank
`DATE` column here; PLAN-snapshot phase 14 resolved the
divergence in favour of parity (the project's standing
principle) and removed the early return — the `localtime_r`
path handles 0 fine, and the JSON output path carries raw
numeric date fields either way.

The input is degenerate: it is unreachable via qemu-created
images — both `qemu-img snapshot -c` and `instar snapshot -c`
always stamp the wall-clock creation time, so a zero `date_sec`
requires a hand-crafted table. The original divergence was
found by PLAN-snapshot phase 13's date-normalization probes,
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
9.0. The cross-version baseline matrix (phase 10) captures exactly
two profile families:

- **Old format** (qemu 6.0.0 through 8.2.x): column headers `VM
  SIZE` and `VM CLOCK` (space-separated), clock rendered with
  2-digit hours (`00:00:00.000`).
- **New format** (qemu 9.0.0 onward): column headers `VM_SIZE` and
  `VM_CLOCK` (underscore-separated), clock rendered with 4-digit
  hours (`0000:00:00.000`), matching instar's renderer from phase 4.

instar implements the new (≥9.0) format. Phase 11 integration tests
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
255 bytes). Fixture `snap-qcow2-longname` (200-byte name) in the
phase 10 baseline matrix produces byte-identical output to
`qemu-img snapshot -l`.

**Residual note**: names longer than the 256-byte wire buffer
(i.e. longer than 255 usable bytes) would still be silently
truncated at the converter. This is unreachable via `qemu-img
snapshot -c`, which caps creation at 255 bytes; instar's own
create path refuses 256+ byte names with an error.

### `snapshot -c` (create) quirks

The following apply to `instar snapshot -c NAME` (create mode,
landed in PLAN-snapshot phase 6).

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
  by the phase 13 differential fuzzer; its chain generator pairs
  512-byte clusters only with 4M images (the phase 6–8 matrix
  pairing). Refcount-structure growth is future work (phase 6
  open question 7).

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
mode, landed in PLAN-snapshot phase 7). The feature gates
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
"goto" mode, landed in PLAN-snapshot phase 8). The feature gates
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
  scenario the phase 6-8 matrices verified, including diverged
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

Since phase 7 of PLAN-qcow2-write-infrastructure, writes into a
snapshot-bearing qcow2 image **copy-on-write** the shared clusters
instead of refusing (the phase-2 interim gates) or corrupting them.
This cross-cutting change lifts the snapshot caveats from `commit`
(issues #420 / #423), `rebase` safe mode (issue #421) and `bench -w`,
so all three now succeed on images that carry internal snapshots.

### The per-op snapshot-view semantic

Phase 7 is net-new behaviour, so the correctness bar is **qemu-parity,
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

## Future Additions

Additional quirks will be documented here as they are discovered during
compatibility testing.
