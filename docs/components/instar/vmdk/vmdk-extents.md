# VMDK Extent Types and Descriptor Format

VMDK images can consist of multiple extent files, each with different
characteristics. The descriptor file ties them together.

## Extent Types

### Descriptor Line Types

| Type | Description | Metadata |
|------|-------------|----------|
| FLAT | Raw/unallocated extent | No grain tables |
| SPARSE | Sparse with grain tables | L1/L2 tables |
| VMFS | VMFS flat extent | No grain tables |
| VMFSSPARSE | VMFS sparse (COWD) | L1/L2 tables |
| SESPARSE | ESXi SE Sparse | Extended L1/L2 |

### Flat vs Sparse

**Flat Extents:**
- All sectors pre-allocated
- No L1/L2 grain tables
- Direct linear mapping: `offset = flat_start_offset + sector * 512`
- Faster access (no table lookups)
- Larger file size

**Sparse Extents:**
- Only allocated grains stored
- Two-level grain table indirection
- Supports copy-on-write
- Smaller file size
- Can have backing files

## Descriptor File Format

The descriptor is a text file containing metadata and extent references.

### Structure

```
# Disk DescriptorFile
version=1
CID=<hex_value>
parentCID=<hex_value>
createType="<type>"
[parentFileNameHint="<path>"]

# Extent description
<access> <sectors> <type> "<filename>" [<offset>]
...

# The Disk Data Base
#DDB

ddb.virtualHWVersion = "<version>"
ddb.geometry.cylinders = "<value>"
ddb.geometry.heads = "<value>"
ddb.geometry.sectors = "<value>"
ddb.adapterType = "<type>"
ddb.toolsVersion = "<version>"
```

### Key Fields

| Field | Description |
|-------|-------------|
| version | Descriptor version (1, 2, or 3) |
| CID | 32-bit content ID (hex), updated on modification |
| parentCID | Parent CID (0xffffffff = no parent) |
| createType | Disk type (monolithicSparse, etc.) |
| parentFileNameHint | Path to parent image (snapshots) |

### DDB Fields

| Field | Description |
|-------|-------------|
| virtualHWVersion | Hardware version (4, 6, 7, etc.) |
| geometry.cylinders | Virtual disk cylinders |
| geometry.heads | Heads (16 for IDE, 255 for SCSI) |
| geometry.sectors | Sectors per track (usually 63) |
| adapterType | ide, lsilogic, buslogic, pvscsi |
| toolsVersion | VMware Tools version |

## Extent Line Syntax

```
<access> <sectors> <type> "<filename>" [<offset>]
```

### Access Modes

| Mode | Description |
|------|-------------|
| RW | Read-Write (required by qemu) |
| RDONLY | Read-only |
| NOACCESS | No access |

**Note:** qemu only supports RW access mode.

### Examples

```
# Flat extent starting at sector 0
RW 4194304 FLAT "disk-flat.vmdk" 0

# Sparse extent
RW 4194304 SPARSE "disk-s001.vmdk"

# Multiple split extents
RW 4194304 SPARSE "disk-s001.vmdk"
RW 4194304 SPARSE "disk-s002.vmdk"
RW 2097152 SPARSE "disk-s003.vmdk"
```

## Multi-Extent Organization

Extents are stored in an array ordered by virtual address:

```c
typedef struct VmdkExtent {
    BdrvChild *file;           // File reference
    bool flat;                 // Flat or sparse?
    bool compressed;           // Compression enabled?
    int64_t sectors;           // Sectors in this extent
    int64_t end_sector;        // Cumulative end sector
    int64_t flat_start_offset; // Offset for flat extents
    // ... grain table metadata for sparse
} VmdkExtent;
```

### Sector Mapping

To find the extent containing a sector:

```c
for (int i = 0; i < num_extents; i++) {
    if (sector_num < extents[i].end_sector) {
        // Found extent
        offset_in_extent = sector_num -
            (extents[i].end_sector - extents[i].sectors);
        return &extents[i];
    }
}
```

### Split Image Layout

For `twoGbMaxExtentSparse` with a 6GB disk:

```
disk.vmdk          (descriptor)
disk-s001.vmdk     sectors 0-4194303      (2GB)
disk-s002.vmdk     sectors 4194304-8388607 (2GB)
disk-s003.vmdk     sectors 8388608-12582911 (2GB)
```

## Extent Permissions

Extents have different child roles:

**Flat extents:**
```c
BDRV_CHILD_DATA  // Data only, no metadata
```

**Sparse extents:**
```c
BDRV_CHILD_DATA | BDRV_CHILD_METADATA  // Both data and metadata
```

## SESparse Extents

ESXi SE Sparse is an advanced format with:
- 64-bit L1/L2 entries
- Fixed 4KB grain size (8 sectors)
- Fixed 64 entries per grain table
- State bits in L2 entries

### SESparse Header

```c
typedef struct VMDKSESparseConstHeader {
    uint64_t magic;              // 0x00000000cafebabe
    uint64_t version;            // 0x0000000200000001
    uint64_t capacity;           // Virtual size in sectors
    uint64_t grain_size;         // Must be 8
    uint64_t grain_table_size;   // Must be 64
    uint64_t flags;              // Must be 0
    // ... offsets for various structures
    uint64_t grain_dir_offset;
    uint64_t grain_tables_offset;
    uint64_t grains_offset;
} qemu_PACKED;
```

### SESparse L2 Entry States

| State | Bits [63:60] | Meaning |
|-------|--------------|---------|
| Unallocated | 0x0 | Not allocated |
| Unmapped | 0x1 | SCSI unmapped |
| Zero | 0x2 | Reads as zeros |
| Allocated | 0x3 | Data present |

## Version Support

### Descriptor Versions

| Version | Features |
|---------|----------|
| 1 | Basic descriptor |
| 2 | Extended features |
| 3 | Change block tracking |

### Header Versions (VMDK4)

| Version | Features |
|---------|----------|
| 1 | 32-bit grain references |
| 2 | Similar to v1 |
| 3 | 64-bit references, RGD, CBT |

**Note:** Version 3 must be opened read-only in qemu.

## Creating VMDK Images

```bash
# Create sparse VMDK
qemu-img create -f vmdk disk.vmdk 20G

# Create specific subformat
qemu-img create -f vmdk -o subformat=monolithicFlat disk.vmdk 20G
qemu-img create -f vmdk -o subformat=twoGbMaxExtentSparse disk.vmdk 20G
qemu-img create -f vmdk -o subformat=streamOptimized disk.vmdk 20G
```

## instar support

instar recognises these VMDK input variants:

- **monolithicSparse / streamOptimized** (single-file,
  binary KDMV header at offset 0): parsed entirely in the
  guest via the grain directory / grain table two-level
  lookup in `crates/vmdk`.
- **monolithicFlat, twoGbMaxExtentFlat and twoGbMaxExtentSparse**
  (text descriptor plus one or more extent files, flat or sparse):
  the descriptor has no binary magic and starts with
  `# Disk DescriptorFile`. The VMM detects the prefix on the host
  and resolves it with `resolve_vmdk_flat_descriptor` in
  `src/vmm/src/chain.rs`: it parses the extent lines with
  `vmdk::parse_descriptor_extents`, requires every extent to be
  `FLAT` or `SPARSE` with offset 0, and validates each extent path
  against the backing-file allowlist. The descriptor and its
  extents are then attached as one virtio-block device, the files
  laid end to end, and `ChainConfig` carries `format =
  VmdkDescriptor` and an extent table recording where each extent
  begins, how long its file is, how much of the disk it covers and
  whether it is flat or sparse. The guest reads through
  `qcow2::read_chain_virtual_cluster`, which finds the extent
  covering the wanted offset in that table. A descriptor may name
  at most 64 extents: that is the table's size, instar's own bound,
  and qemu-img has none. See [chain-config.md](/components/instar/chain-config/).

  The host also reads the first 79 bytes of each *sparse* extent,
  for the grain size `info` reports as the image's and the extent's
  cluster size — a descriptor is text and declares none of its own.
  That is the one place the host parses a binary format structure
  rather than descriptor text, so it is bounded on purpose: a
  fixed-size stack array, `read_exact` so a short file is a failure
  rather than a partial parse, the VMDK4 magic checked before
  anything is decoded, a parser that is safe Rust over a slice, and
  a result that is only ever printed — never an offset or a length
  the host computes with. Only a regular file is opened, and with
  `O_NONBLOCK`, so a path the descriptor named cannot block the
  host. `extent_cluster_size` in `src/vmm/src/chain.rs` is the whole
  of it. The record of this as a deviation from "all format parsing
  runs inside the guest" is in
  [security-audits.md](/components/instar/security-audits/#standing-security-properties).
- **A flat descriptor with `parentFileNameHint=`** is followed as
  a backing chain: the hint is validated against the allowlist
  like any backing path, and chain discovery continues with the
  parent, whose device follows the descriptor's own. The extents
  take no device slots of their own, so there is nothing between
  the two.

`info`, `check`, `compare` and `convert` read all three. `map` and
`measure` refuse every descriptor-driven source; see
[quirks.md](/components/instar/quirks/#vmdk-multi-extent-sources-are-refused).

## References

- qemu source: `block/vmdk.c`
- VMware VDDK 5.0 Technical Note
