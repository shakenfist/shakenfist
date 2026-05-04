# QCOW2 L1/L2 Tables - Address Translation

QCOW2 uses a two-level table structure for mapping guest (virtual) disk offsets
to host (physical) file offsets. This design enables sparse allocation and
efficient copy-on-write operations.

## Address Translation Overview

```
Guest Offset (logical address)
        |
        v
+-------------------+
|   L1 Table        |  Memory-resident, one entry per L2 table
|   [l1_index]      |
+-------------------+
        |
        v  (L2 table offset)
+-------------------+
|   L2 Table        |  One cluster per table, cached on demand
|   [l2_index]      |
+-------------------+
        |
        v  (cluster offset + in-cluster offset)
+-------------------+
|   Data Cluster    |  Actual disk data
+-------------------+
```

## Index Calculations

Given a guest offset, compute the table indices:

```c
// Configuration derived from header
cluster_size = 1 << cluster_bits;          // e.g., 65536 (64 KB)
l2_entries = cluster_size / l2_entry_size; // e.g., 8192 for standard entries
l2_bits = log2(l2_entries);                // e.g., 13

// Address translation
l1_index = guest_offset >> (l2_bits + cluster_bits);
l2_index = (guest_offset >> cluster_bits) & (l2_entries - 1);
in_cluster_offset = guest_offset & (cluster_size - 1);

// Final host offset
l2_table_offset = l1_table[l1_index] & L1E_OFFSET_MASK;
cluster_descriptor = l2_table[l2_index];
host_cluster_offset = cluster_descriptor & L2E_OFFSET_MASK;
host_offset = host_cluster_offset + in_cluster_offset;
```

### Example: 64 KB Clusters (cluster_bits = 16)

```
cluster_size = 65536 bytes
l2_entries = 65536 / 8 = 8192 entries per L2 table
l2_bits = 13

For guest offset 0x12345678:
  l1_index = 0x12345678 >> (13 + 16) = 0x12345678 >> 29 = 0
  l2_index = (0x12345678 >> 16) & 0x1FFF = 0x1234 & 0x1FFF = 0x1234
  in_cluster_offset = 0x12345678 & 0xFFFF = 0x5678
```

## L1 Table Entry Format (64 bits)

```
 63  62       56  55                                  9  8        0
+---+----------+-------------------------------------+------------+
| C | Reserved |     L2 Table Offset (47 bits)      |  Reserved  |
+---+----------+-------------------------------------+------------+
  |                         |
  |                         +-- Must be cluster-aligned (bits 0-8 = 0)
  +-- COPIED flag (refcount == 1)
```

| Bits | Name | Description |
|------|------|-------------|
| 0-8 | Reserved | Must be zero |
| 9-55 | Offset | L2 table offset (cluster-aligned) |
| 56-62 | Reserved | Must be zero |
| 63 | COPIED | Set if L2 table refcount is exactly 1 |

**Masks:**
```c
#define L1E_OFFSET_MASK     0x00fffffffffffe00ULL
#define L1E_RESERVED_MASK   0x7f000000000001ffULL
```

**Special values:**
- Entry = 0: L2 table not allocated (all clusters in this range unallocated)

## L2 Table Entry Format - Standard (64 bits)

```
 63  62  61       56  55                          9  8     1  0
+---+---+----------+-------------------------------+-------+---+
| C | Z |  Rsvd    |    Host Cluster Offset        | Rsvd  | Z |
+---+---+----------+-------------------------------+-------+---+
  |   |                      |                              |
  |   |                      +-- Cluster-aligned offset     |
  |   +-- COMPRESSED flag                                   |
  +-- COPIED flag                                           +-- ZERO flag
```

| Bits | Name | Description |
|------|------|-------------|
| 0 | ZERO | Cluster reads as zeros (v3+ only) |
| 1-8 | Reserved | Must be zero |
| 9-55 | Offset | Host cluster offset (cluster-aligned) |
| 56-61 | Reserved/Size | Reserved for standard, size for compressed |
| 62 | COMPRESSED | Cluster is compressed |
| 63 | COPIED | Refcount is exactly 1 |

**Masks:**
```c
#define L2E_OFFSET_MASK         0x00fffffffffffe00ULL
#define L2E_STD_RESERVED_MASK   0x3f000000000001feULL
#define QCOW_OFLAG_COPIED       (1ULL << 63)
#define QCOW_OFLAG_COMPRESSED   (1ULL << 62)
#define QCOW_OFLAG_ZERO         (1ULL << 0)
```

## Cluster Type Determination

```c
typedef enum QCow2ClusterType {
    QCOW2_CLUSTER_UNALLOCATED,  // offset=0, zero=0: read from backing
    QCOW2_CLUSTER_ZERO_PLAIN,   // offset=0, zero=1: reads as zeros
    QCOW2_CLUSTER_ZERO_ALLOC,   // offset!=0, zero=1: allocated but zeros
    QCOW2_CLUSTER_NORMAL,       // offset!=0, compressed=0: standard data
    QCOW2_CLUSTER_COMPRESSED,   // compressed=1: compressed data
} QCow2ClusterType;

QCow2ClusterType get_cluster_type(uint64_t l2_entry) {
    if (l2_entry & QCOW_OFLAG_COMPRESSED) {
        return QCOW2_CLUSTER_COMPRESSED;
    }

    uint64_t offset = l2_entry & L2E_OFFSET_MASK;

    if (l2_entry & QCOW_OFLAG_ZERO) {
        return offset ? QCOW2_CLUSTER_ZERO_ALLOC : QCOW2_CLUSTER_ZERO_PLAIN;
    }

    return offset ? QCOW2_CLUSTER_NORMAL : QCOW2_CLUSTER_UNALLOCATED;
}
```

## Compressed Cluster Descriptor

When bit 62 (COMPRESSED) is set, the entry format changes:

```
 63  62  61                        csize_shift                    0
+---+---+---------------------------+------------------------------+
| 0 | 1 |   Compressed Sectors - 1 |     Compressed Data Offset   |
+---+---+---------------------------+------------------------------+
      ^            ^                              ^
      |            |                              |
      |            +-- Number of 512B sectors     +-- NOT cluster-aligned!
      +-- COMPRESSED = 1 (COPIED always 0)
```

The bit positions are dynamic based on cluster size:

```c
csize_shift = 62 - (cluster_bits - 8);
csize_mask = (1 << (cluster_bits - 8)) - 1;
cluster_offset_mask = (1ULL << csize_shift) - 1;

// Parsing a compressed entry
uint64_t compressed_offset = l2_entry & cluster_offset_mask;
int nb_sectors = ((l2_entry >> csize_shift) & csize_mask) + 1;
int compressed_size = nb_sectors * 512 -
                      (compressed_offset & 511);  // Adjust for alignment
```

**Example for 64 KB clusters (cluster_bits = 16):**
```
csize_shift = 62 - 8 = 54
csize_mask = 0xFF (8 bits for sector count)
cluster_offset_mask = (1 << 54) - 1

Bits 0-53: Compressed data offset (54 bits)
Bits 54-61: Sector count - 1 (8 bits, max 255+1 = 256 sectors = 128 KB)
Bit 62: COMPRESSED = 1
Bit 63: COPIED = 0 (always for compressed)
```

## Extended L2 Entries (Subclusters)

When incompatible feature bit 4 (EXTL2) is set, L2 entries are 128 bits
(16 bytes) instead of 64 bits, enabling subcluster allocation.

Each cluster is divided into 32 subclusters, each tracked independently.

### Extended L2 Entry Format (128 bits)

**First 64 bits:** Standard L2 entry (bit 0 unused)

**Second 64 bits:** Subcluster bitmap

```
 63                              32  31                            0
+----------------------------------+--------------------------------+
|     Zero Bitmap (32 bits)        |   Allocation Bitmap (32 bits) |
+----------------------------------+--------------------------------+
       |                                        |
       +-- Bit N: subcluster N reads as zeros   |
                                                +-- Bit N: subcluster N allocated
```

```c
#define QCOW_EXTL2_SUBCLUSTERS_PER_CLUSTER  32

#define QCOW_OFLAG_SUB_ALLOC(X)    (1ULL << (X))
#define QCOW_OFLAG_SUB_ZERO(X)     (1ULL << ((X) + 32))

// Subcluster index calculation
subcluster_bits = cluster_bits - 5;  // 32 subclusters
subcluster_size = cluster_size / 32;
sc_index = (guest_offset >> subcluster_bits) & 31;

// Check subcluster status
bool is_allocated = bitmap & QCOW_OFLAG_SUB_ALLOC(sc_index);
bool is_zero = bitmap & QCOW_OFLAG_SUB_ZERO(sc_index);
```

### Subcluster Types

```c
typedef enum QCow2SubclusterType {
    QCOW2_SUBCLUSTER_UNALLOCATED_PLAIN,  // Not allocated, read backing
    QCOW2_SUBCLUSTER_UNALLOCATED_ALLOC,  // Cluster allocated, SC not
    QCOW2_SUBCLUSTER_ZERO_PLAIN,         // Reads zeros, no allocation
    QCOW2_SUBCLUSTER_ZERO_ALLOC,         // Allocated but reads zeros
    QCOW2_SUBCLUSTER_NORMAL,             // Normal allocated data
    QCOW2_SUBCLUSTER_COMPRESSED,         // Entire cluster compressed
    QCOW2_SUBCLUSTER_INVALID,            // Invalid state
} QCow2SubclusterType;
```

**Important constraints:**
- Compressed clusters cannot use subclusters (bitmap must be 0)
- Extended L2 requires cluster_bits >= 14 (minimum 16 KB clusters)
- Minimum subcluster size is 512 bytes

### Subcluster Bitmap Validation (check operation)

The `instar check` operation validates extended-L2 subcluster bitmaps
against the QCOW2 spec's invalid-combination rules. The following
invariants are enforced:

**Standard (non-compressed) entries:**

| # | Condition | Meaning |
|---|-----------|---------|
| I1 | `alloc_bits & zero_bits != 0` | Subcluster simultaneously allocated and all-zero |
| I2 | `host_offset == 0 && alloc_bits != 0` | Bitmap claims subclusters allocated but no host cluster |
| I3 | `host_offset != 0 && alloc_bits == 0 && zero_bits == 0` | Host cluster allocated but no subcluster references it |

**Compressed entries:**

| # | Condition | Meaning |
|---|-----------|---------|
| C1 | `sc_bitmap != 0` (excluding legacy `alloc_bits=0xFFFF_FFFF`) | Spec reserves all 64 bitmap bits as zero |

Note: C1 accepts `alloc_bits == 0xFFFF_FFFF` with `zero_bits == 0`
for compatibility with images produced by older QEMU versions.

Each violation increments `corruptions`, `total_errors`, and
`subcluster_errors` in the check result. Detailed messages are
emitted via `debug_print` (visible with `--verbose`).

Implemented by `qcow2::validate_subcluster_bitmap()` in the qcow2
crate, called from the L2 walk in the check operation.

## The COPIED Flag

The COPIED flag (bit 63) is an optimization hint:

- **Set (1):** Cluster/table refcount is exactly 1; can modify in-place
- **Clear (0):** May be shared with snapshots; must COW before writing

This avoids refcount lookups during normal writes when the flag is set.

**Rules:**
1. COPIED is never set for compressed clusters
2. COPIED must be consistent with actual refcount
3. When creating snapshots, COPIED is cleared on shared clusters
4. When refcount drops to 1, COPIED should be set

## L2 Table Caching

qemu loads L2 tables on demand and caches them:

```c
// L2 cache sizing formula
l2_cache_max_disk = l2_cache_size * cluster_size / l2_entry_size;

// For 1 MB cache, 64 KB clusters, standard entries:
// 1 MB * 64 KB / 8 = 8 GB of disk addressable
```

L2 tables are loaded in "slices" (typically 4 KB) rather than full clusters
for memory efficiency.

## Table Constraints

1. **L1 table:** Must be contiguous in image file
2. **L2 tables:** Each exactly one cluster; cluster-aligned
3. **Offsets:** All table offsets must be cluster-aligned
4. **Reserved bits:** Must be zero; reject images with non-zero reserved bits
5. **Maximum L1 size:** 32 MB (qemu limit)

## Backing File Chain Resolution

When a cluster is unallocated:

```
1. Check local L2 entry
2. If unallocated (offset=0, zero=0):
   a. If backing file exists: recurse into backing file
   b. If no backing file: return zeros
3. Continue until allocated cluster found or chain ends
```

The backing file path is stored after the header (see backing_file_offset).
