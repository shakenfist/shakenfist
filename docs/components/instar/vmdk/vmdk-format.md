# VMDK Format Specification

VMware Virtual Machine Disk (VMDK) is a disk image format developed by VMware.
It supports multiple sub-formats with varying features.

## Overview

VMDK provides:
- Sparse allocation (only used clusters stored)
- Multiple extent files (split images)
- Compression (DEFLATE)
- Snapshots via delta disks
- Streaming format for OVF/OVA

**All multi-byte values are stored in Little Endian byte order.**

## Magic Numbers

| Magic | Value | Format |
|-------|-------|--------|
| VMDK3 | `0x434f5744` | "COWD" - VMFS sparse |
| VMDK4 | `0x564d444b` | "VMDK" - Standard sparse |
| SESparse | `0x00000000cafebabe` | SE Sparse constant header |
| SESparse Volatile | `0x00000000cafecafe` | SE Sparse volatile header |

## VMDK4 Header Structure (512 bytes)

```c
typedef struct VMDK4Header {
    uint32_t magic;              // 0x564d444b ("VMDK")
    uint32_t version;            // 1, 2, or 3
    uint32_t flags;              // Feature flags
    uint64_t capacity;           // Virtual disk size in sectors
    uint64_t granularity;        // Grain size in sectors
    uint64_t desc_offset;        // Descriptor offset (sectors)
    uint64_t desc_size;          // Descriptor size (sectors)
    uint32_t num_gtes_per_gt;    // Grain table entries per GT (512)
    uint64_t rgd_offset;         // Redundant grain directory offset
    uint64_t gd_offset;          // Grain directory offset
    uint64_t grain_offset;       // First grain data offset
    char filler[1];              // Padding
    char check_bytes[4];         // 0x0a, 0x20, 0x0d, 0x0a
    uint16_t compressAlgorithm;  // Compression type
} qemu_PACKED;
```

## VMDK3 Header Structure (40 bytes)

```c
typedef struct VMDK3Header {
    uint32_t version;
    uint32_t flags;
    uint32_t disk_sectors;       // Total disk sectors
    uint32_t granularity;        // Grain size in sectors
    uint32_t l1dir_offset;       // L1 directory offset (sectors)
    uint32_t l1dir_size;         // L1 directory size (entries)
    uint32_t file_sectors;       // File size in sectors
    uint32_t cylinders;          // CHS geometry
    uint32_t heads;
    uint32_t sectors_per_track;
} qemu_PACKED;
```

## Header Flags (VMDK4)

| Flag | Value | Description |
|------|-------|-------------|
| NL_DETECT | 1 << 0 | Newline detection enabled |
| RGD | 1 << 1 | Redundant grain directory present |
| ZERO_GRAIN | 1 << 2 | Zeroed-grain optimization |
| COMPRESS | 1 << 16 | Compression enabled |
| MARKER | 1 << 17 | Grain markers present |

## Compression

| Value | Algorithm |
|-------|-----------|
| 0 | None |
| 1 | DEFLATE (zlib) |

## Disk Types (createType)

| Type | Description |
|------|-------------|
| monolithicFlat | Single flat file |
| monolithicSparse | Single sparse file |
| twoGbMaxExtentFlat | Multiple flat files, max 2GB each |
| twoGbMaxExtentSparse | Multiple sparse files, max 2GB each |
| vmfs | VMFS flat extent |
| vmfsSparse | VMFS sparse (COWD) |
| streamOptimized | Compressed for streaming |
| seSparse | ESXi SE Sparse |

## Key Constants

```c
#define VMDK4_MAGIC             0x564d444b  // "VMDK"
#define VMDK3_MAGIC             0x434f5744  // "COWD"
#define VMDK4_GD_AT_END         0xffffffffffffffff
#define VMDK_GTE_ZEROED         0x00000001
#define VMDK4_COMPRESSION_DEFLATE  1

#define SECTOR_SIZE             512
#define DESC_SIZE               (20 * SECTOR_SIZE)  // 10,240 bytes
#define L2_CACHE_SIZE           16  // Cached grain tables
```

## Marker Types (StreamOptimized)

| Value | Type | Description |
|-------|------|-------------|
| 0 | END_OF_STREAM | End of file |
| 1 | GRAIN_TABLE | Grain table follows |
| 2 | GRAIN_DIRECTORY | Grain directory follows |
| 3 | FOOTER | Footer header follows |

## Size Limits

| Limit | Value |
|-------|-------|
| Max extent size | 2TB (4,294,967,296 sectors) |
| Split extent size | 2GB (for twoGbMaxExtent*) |
| Max L1 entries | 32,000,000 |

## File Layout Examples

### Monolithic Sparse

```
[Offset 0]        VMDK4_MAGIC + Header
[desc_offset]     Descriptor (text)
[rgd_offset]      Redundant Grain Directory
[gd_offset]       Grain Directory
[grain_offset]    Grain Data
```

### StreamOptimized

```
[Offset 0]        VMDK4_MAGIC + Header
[desc_offset]     Descriptor
[...]             Compressed Grains with Markers
[EOF - 1536]      Footer Marker
[EOF - 1024]      Footer Header (VMDK4_MAGIC + Header)
[EOF - 512]       End-of-Stream Marker
```

## References

- qemu source: `block/vmdk.c`
- VMware VDDK Technical Note 5.0
- See also: `vmdk-extents.md`, `vmdk-grain-tables.md`, `vmdk-compression.md`
