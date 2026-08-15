# Per-format implementation notes

What instar's parsers support for each disk image format, and the
deliberate limits. For the qemu-img parity comparison and the coverage
matrix, see [format-coverage.md](/components/instar/format-coverage/); for user-facing
reference, see [usage.md](/components/instar/usage/) and the per-operation pages.

## Format Support

**Measurable target formats**: raw, qcow2 (qemu-img-parity),
vmdk, vpc (VHD), vhdx (instar-only — qemu-img does not
implement `measure` for these targets).

**Creatable target formats**: raw (host-only —
`open + ftruncate + posix_fallocate`), qcow2 (qemu-img
info-equivalent modulo `refcount_bits` / `compat` / `zstd`
hardcodes), vmdk monolithicSparse + streamOptimized, vpc
dynamic + fixed (modulo CHS `virtual_size` rounding), vhdx
dynamic (modulo default `block_size` when unspecified).
Backing-file references supported on qcow2, vmdk, vpc, vhdx
(matches qemu-img's permission set). See
[docs/create.md](/components/instar/create/) for the user reference and
[docs/quirks.md](/components/instar/quirks/) for the documented writer
divergences.

## qcow2

QEMU Copy-On-Write version 2/3. Supported features:
- Sparse allocation with cluster sizes 512B-2MB (cluster_bits 9-21)
- Compression (zlib, zstd) for clusters up to 2MB
- Backing file chains (automatic flattening)
- Refcount widths: 1, 2, 4, 8, 16, 32, 64 bits
- Extended L2 entries (16-byte with subcluster bitmaps;
  full subcluster support — the bitmap is parsed for
  per-subcluster data reading: Normal, Zero, and
  Unallocated states; the read path narrows I/O for mixed-
  subcluster clusters when sector_size ≤ subcluster_size).
  Output with `--extended-l2` writes 16-byte L2 entries with
  `incompatible_features` bit 4 and per-subcluster sparse
  bitmaps (`compute_subcluster_bitmap()`).
- Incompatible feature bit validation
- External data files (metadata/data separation, chain discovery with allowlist)
- Legacy AES-128-CBC encryption (crypt_method=1) decryption via `--qcow2-password`
- LUKS-in-QCOW2 encryption (crypt_method=2) decryption via `--luks-passphrase`
- LUKS-encrypted output (crypt_method=2) via `--luks-encrypt-passphrase`
  (AES-256-XTS with PBKDF2-SHA256 key derivation, LUKS v1 headers)
- Snapshot table parsing, detection, and extraction via `--snapshot`

### qcow2 write infrastructure

In-place mutation of an existing qcow2 (used by `commit`, `rebase`
safe mode and `bench -w`) runs on two shared `no_std` crates: the
**`crates/qcow2-write`** planner (pure, I/O-free, address-free —
turns a write into a typed step program; handles the envelope,
classification, allocate-on-write and copy-on-write) and the
**`crates/qcow2-write-exec`** executor (the literal step interpreter
plus the byte-range/device layer). Refcount growth is split the same
way across each crate's `growth` module. The maintainer reference for
this machinery — the step-program ABI, the write envelope, COW, growth
and the crash-ordering contract — is
[docs/qcow2/qcow2-write-planner.md](/components/instar/qcow2/qcow2-write-planner/).

## raw

Simple byte-for-byte disk representation. No metadata, just data.

## vmdk

VMware Virtual Machine Disk. Supported sub-formats for input/output:
- monolithicSparse (input, output, check)
- streamOptimized (input, output with `-c`, check)
- monolithicFlat (input and output): two-file descriptor + flat extent.
  The VMM detects the descriptor prefix on the host, parses the
  extent line via `vmdk::parse_descriptor_extents`, validates
  the flat path against the backing-file allowlist, and opens
  the flat extent as a second virtio-block device. Guest
  operations read content from that device through the same
  `ChainConfig.data_device_idx` redirect used for QCOW2
  external data files. Output via `--subformat monolithicFlat`.
- twoGbMaxExtentFlat (input): multi-extent flat descriptors with
  multiple flat extent files. Each extent is opened as a separate
  virtio-block device and reads are dispatched to the correct
  device based on the extent offset map.
- monolithicFlat with `parentFileNameHint=` (input): descriptors
  referencing a parent are followed as a backing chain, enabling
  flat images in overlay hierarchies.

Detected but not yet supported for I/O:
- twoGbMaxExtentSparse (multi-extent sparse, detected and rejected
  gracefully)

The check operation performs full structural validation: grain directory
and grain table walk, grain offset bounds checking, compressed grain
marker validation (LBA consistency and compressed size bounds),
redundant grain directory (RGD) cross-check, overlap detection via
1-bit-per-grain bitmap, streamOptimized footer validation, fragmentation
measurement, and multi-extent detection.

## vhd

Microsoft Virtual Hard Disk. Supported sub-formats:
- Fixed (type 2): raw data with 512-byte footer appended
- Dynamic (type 3): BAT-based block allocation with 2 MiB blocks (input,
  output, check)

The check operation performs full structural validation: footer cookie
and checksum, format version and feature flag validation, dynamic header
cookie/checksum/version, BAT offset and entry bounds checking, overlap
detection via 1-bit-per-block bitmap, fragmentation tracking, fixed VHD
size validation, and footer copy consistency (start vs end of file).

## vhdx

Microsoft VHDX Virtual Hard Disk v2 (Hyper-V). Supported:
- Dynamic VHDX: BAT-based block allocation with 32 MiB blocks (input,
  output, check)

VHDX uses CRC-32C (Castagnoli) checksums, GUID-identified metadata,
64-bit BAT entries with interleaved sector bitmap entries, and 1MB-aligned
structures. All on-disk fields are little-endian.

The check operation performs full structural validation: file identifier
signature check, dual header CRC-32C validation with active header
selection by sequence number, dirty log detection, region table 1 and 2
CRC-32C validation with cross-consistency check, GUID-based metadata
parsing, BAT entry validation (offset bounds, 1MB alignment, overlap
detection, state validation), and fragmentation tracking.

## luks

LUKS encrypted containers (v1 and v2). The info operation parses:
- Version, cipher name, cipher mode, hash algorithm
- UUID, payload offset, master key length, active key slots
- LUKS v2: JSON metadata area for cipher/hash extraction

With `--luks-passphrase`, LUKS v1 and v2 containers are decrypted inside
the KVM guest using pure-Rust RustCrypto crates (software AES, no
hardware acceleration needed in bare-metal). Key derivation uses PBKDF2
(v1) or Argon2id (v2, requires `--max-guest-memory` for the 1GB+ working
memory). The decrypted first block is passed through format detection to
report the inner format and virtual size.

The convert operation supports decrypting native LUKS containers
(`--luks-passphrase`) and LUKS-in-QCOW2 images (crypt_method=2). Both
use AES-XTS-plain64 for payload decryption. Native LUKS containers
wrapping QCOW2 images are transparently handled: the convert operation
detects the inner QCOW2 format and wraps the CallTable I/O function
pointers to offset and decrypt reads, allowing the qcow2 crate to
process the inner image without modification. LUKS v2 containers
using Argon2id KDF require `--max-guest-memory` to allocate the
working memory needed for key derivation.

## vdi

VirtualBox Disk Image. Read-only input for convert / compare / dd /
bench (`src/crates/vdi/`, the PLAN-format-coverage work), both
dynamic and static images. Key structures: a single header
(validated against qemu's 12 open-time rules) plus a flat block
map, walked with an allocation-order lookup through the standard
sector-cached read path. Discarded and unallocated block-map
entries read as zeros, and reads at or past device capacity
zero-fill rather than error, matching qemu's lack of length
validation. `check` still refuses VDI (exit 63); `map`, `measure`,
and `resize` are unchanged refusals. See
[docs/format-coverage.md](/components/instar/format-coverage/) and
[docs/quirks.md](/components/instar/quirks/) for the full parity and quirks
detail.

## parallels

Parallels disk images. Read-only input for convert / compare / dd /
bench (`src/crates/parallels/`, the PLAN-format-coverage work),
both the legacy "WithoutFreeSpace" (v1) and "WithouFreSpacExt"
(v2/ext) magics. Key structures: a header (tracks, catalog/BAT
size, `ext_off`) plus a per-magic BAT (sector-valued under v1,
cluster-valued under v2), walked through the standard sector-cached
read path. BAT value 0 and offsets beyond BAT coverage read as
zeros; `ext_off != 0` is refused at init (a deliberate divergence —
instar does not parse the format extension). `check` still refuses
Parallels (exit 63); `map`, `measure`, and `resize` are unchanged
refusals. See [docs/format-coverage.md](/components/instar/format-coverage/)
and [docs/quirks.md](/components/instar/quirks/) for the full parity and
quirks detail.

## qcow1 (qcow)

QEMU's original copy-on-write format ("qcow", superseded by qcow2
but not formally deprecated by qemu). Read-only input for convert /
compare / dd / bench (`src/crates/qcow1/`, the PLAN-format-coverage work), including backing chains and compressed clusters. Key
structures: a header plus two-level (L1/L2) block lookup, with
compressed clusters as raw DEFLATE (no zlib wrapper) — distinct
from qcow2's zlib-first decompression helper. QCOW1 is the first
non-QCOW2 backing format: unallocated clusters fall through to the
next chain device rather than zero-filling. `check` still refuses
QCOW1 (exit 63) — genuine parity, since qemu's own qcow driver also
refuses checks. `map` and `measure` stay refusals (a deliberate
divergence; qemu supports both on qcow1). See
[docs/format-coverage.md](/components/instar/format-coverage/) and
[docs/quirks.md](/components/instar/quirks/) for the full parity and quirks
detail.

## dmg

Apple UDIF disk image. Read-only input for convert / compare / dd /
bench (`src/crates/dmg/`, the PLAN-format-coverage work). Key
structures: the koly trailer (shared trailer helpers), the
XML-plist or resource-fork chunk table, and per-sector chunk
lookup. Supports zlib, raw, ADC, bzip2, and LZFSE-compressed
chunks. The read-error model inverts every prior phase's posture:
most malformed inputs are refused rather than best-effort parsed.
DMG is supported at any backing-chain position. `check` still
refuses DMG (exit 63), but the refusal reports format `"raw"`, not
`"dmg"`, matching qemu-img's passthrough divergence. See
[docs/format-coverage.md](/components/instar/format-coverage/) and
[docs/quirks.md](/components/instar/quirks/) for the full parity and quirks
detail.

