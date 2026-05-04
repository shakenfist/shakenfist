# QCOW2 Format Specification

A comprehensive reference for the qemu Copy-On-Write version 2 disk image format,
compiled from qemu source code analysis and official documentation.

## Overview

QCOW2 is a disk image format used by qemu that provides:
- Copy-on-write support with backing files
- Sparse allocation (only allocated clusters consume space)
- Snapshots with independent L1 tables
- Compression (zlib, zstd)
- Encryption (AES legacy, LUKS)
- Subcluster allocation (extended L2 entries)

**All multi-byte values are stored in Big Endian byte order.**

## Header Structure

The QCOW2 header occupies the first cluster of the image file.

### Version 2 Header (72 bytes)

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 0 | 4 | magic | Magic number: `0x514649fb` ("QFI\xfb") |
| 4 | 4 | version | Format version (2 or 3) |
| 8 | 8 | backing_file_offset | Offset to backing file name (0 if none) |
| 16 | 4 | backing_file_size | Length of backing file name (max 1023) |
| 20 | 4 | cluster_bits | Log2 of cluster size (9-21) |
| 24 | 8 | size | Virtual disk size in bytes |
| 32 | 4 | crypt_method | Encryption: 0=none, 1=AES, 2=LUKS |
| 36 | 4 | l1_size | Number of L1 table entries |
| 40 | 8 | l1_table_offset | Offset to L1 table (cluster-aligned) |
| 48 | 8 | refcount_table_offset | Offset to refcount table (cluster-aligned) |
| 56 | 4 | refcount_table_clusters | Size of refcount table in clusters |
| 60 | 4 | nb_snapshots | Number of snapshots |
| 64 | 8 | snapshots_offset | Offset to snapshot table (cluster-aligned) |

### Version 3 Additional Fields (104+ bytes)

| Offset | Size | Field | Description |
|--------|------|-------|-------------|
| 72 | 8 | incompatible_features | Features that must be understood |
| 80 | 8 | compatible_features | Optional features (safe to ignore) |
| 88 | 8 | autoclear_features | Features cleared if unknown |
| 96 | 4 | refcount_order | Log2 of refcount bits (0-6) |
| 100 | 4 | header_length | Header length (min 104 for v3) |
| 104 | 1 | compression_type | 0=zlib, 1=zstd |
| 105 | 7 | padding | Reserved, must be zero |

### Feature Flags

**Incompatible Features (must understand to use image):**

| Bit | Name | Description |
|-----|------|-------------|
| 0 | DIRTY | Image not closed cleanly; refcounts may be inconsistent |
| 1 | CORRUPT | Image metadata may be corrupted |
| 2 | DATA_FILE | External data file in use |
| 3 | COMPRESSION | Non-zlib compression type used |
| 4 | EXTL2 | Extended L2 entries with subclusters |

**Compatible Features (safe to ignore if unknown):**

| Bit | Name | Description |
|-----|------|-------------|
| 0 | LAZY_REFCOUNTS | Refcounts updated lazily |

**Autoclear Features (cleared when opened by unknown software):**

| Bit | Name | Description |
|-----|------|-------------|
| 0 | BITMAPS | Dirty bitmaps present |
| 1 | DATA_FILE_RAW | External data file is standalone raw image |

## Header Extensions

Header extensions follow immediately after the header, padded to 8-byte boundaries.

| Offset | Size | Field |
|--------|------|-------|
| 0 | 4 | type |
| 4 | 4 | length |
| 8 | length | data |
| 8+length | 0-7 | padding to 8-byte boundary |

**Extension Types:**

| Value | Name | Description |
|-------|------|-------------|
| 0x00000000 | End | End of header extensions |
| 0xe2792aca | Backing Format | Backing file format name string |
| 0x6803f857 | Feature Table | Feature name table |
| 0x23852875 | Bitmaps | Dirty bitmap extension |
| 0x0537be77 | Crypto Header | Full disk encryption header pointer |
| 0x44415441 | Data File | External data file name |

## Key Constants

```c
#define QCOW_MAGIC              0x514649fb  // "QFI\xfb"

// Encryption methods
#define QCOW_CRYPT_NONE         0
#define QCOW_CRYPT_AES          1
#define QCOW_CRYPT_LUKS         2

// Cluster constraints
#define MIN_CLUSTER_BITS        9           // 512 bytes minimum
#define MAX_CLUSTER_BITS        21          // 2 MB maximum
#define DEFAULT_CLUSTER_SIZE    65536       // 64 KB default

// Size limits (qemu implementation)
#define QCOW_MAX_L1_SIZE        (32 * 1024 * 1024)   // 32 MB
#define QCOW_MAX_REFTABLE_SIZE  (8 * 1024 * 1024)    // 8 MB
#define QCOW_MAX_SNAPSHOTS      65536
#define QCOW_MAX_CLUSTER_OFFSET ((1ULL << 56) - 1)   // ~64 PB

// Entry sizes
#define L1E_SIZE                8           // 8 bytes per L1 entry
#define L2E_SIZE_NORMAL         8           // 8 bytes per standard L2 entry
#define L2E_SIZE_EXTENDED       16          // 16 bytes per extended L2 entry
#define REFTABLE_ENTRY_SIZE     8           // 8 bytes per refcount table entry

// Compression
#define QCOW2_COMPRESSED_SECTOR_SIZE  512

// Flags in L1/L2 entries
#define QCOW_OFLAG_COPIED       (1ULL << 63)  // Refcount == 1
#define QCOW_OFLAG_COMPRESSED   (1ULL << 62)  // Cluster is compressed
#define QCOW_OFLAG_ZERO         (1ULL << 0)   // Reads as zeros (v3+)
```

## References

- qemu source: `block/qcow2.h`, `block/qcow2.c`
- Official docs: `docs/interop/qcow2.txt`
- See also: `qcow2-l1l2-tables.md`, `qcow2-refcount.md`, `qcow2-snapshots.md`,
  `qcow2-compression.md`, `qcow2-encryption.md`
