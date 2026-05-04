# QCOW2 Implementation Notes

Practical guidance for implementing a qcow2 parser or converter, including
common pitfalls and best practices.

## Byte Order

**All multi-byte integers in qcow2 are Big Endian.**

```c
// Reading a 64-bit field
uint64_t read_be64(const uint8_t *buf) {
    return ((uint64_t)buf[0] << 56) |
           ((uint64_t)buf[1] << 48) |
           ((uint64_t)buf[2] << 40) |
           ((uint64_t)buf[3] << 32) |
           ((uint64_t)buf[4] << 24) |
           ((uint64_t)buf[5] << 16) |
           ((uint64_t)buf[6] << 8) |
           ((uint64_t)buf[7]);
}

// Or use standard functions
#include <arpa/inet.h>  // ntohl, etc.
#include <endian.h>     // be64toh, etc.
```

## Version Differences

### Version 2 (Legacy)

- Header is exactly 72 bytes
- No feature flags
- 16-bit refcounts only (order = 4)
- No extended L2 entries
- No compression type field (always zlib)

### Version 3 (Current)

- Header is at least 104 bytes (check `header_length`)
- Feature flags present
- Variable refcount widths
- Extended L2 entries possible
- Compression type in header

**Always check version before accessing v3-only fields.**

## Cluster Alignment Validation

Critical offsets must be cluster-aligned:

```c
bool is_cluster_aligned(uint64_t offset, int cluster_bits) {
    uint64_t cluster_size = 1ULL << cluster_bits;
    return (offset & (cluster_size - 1)) == 0;
}

// Validate on read:
if (!is_cluster_aligned(l1_table_offset, cluster_bits)) {
    return ERROR_INVALID_L1_OFFSET;
}
```

Validate alignment for:
- L1 table offset
- L2 table offsets (in L1 entries)
- Refcount table offset
- Refcount block offsets
- Snapshot table offset
- LUKS header offset (if encrypted)

## Reserved Bits

**Reject images with non-zero reserved bits.**

```c
if (l1_entry & L1E_RESERVED_MASK) {
    return ERROR_RESERVED_BITS_SET;
}

if (l2_entry & L2E_STD_RESERVED_MASK) {
    return ERROR_RESERVED_BITS_SET;
}
```

This catches:
- Corrupted images
- Future format extensions you don't understand

## Zero vs Unallocated Clusters

These are semantically different:

| Condition | Meaning |
|-----------|---------|
| L2 entry = 0 | Unallocated: read from backing file |
| L2 entry with ZERO flag, offset = 0 | Reads as zeros (no backing) |
| L2 entry with ZERO flag, offset != 0 | Allocated but reads as zeros |

```c
if (l2_entry == 0) {
    // Unallocated - check backing file
    if (backing_file) {
        return read_from_backing(backing_file, guest_offset, buf, len);
    } else {
        memset(buf, 0, len);
        return 0;
    }
} else if (l2_entry & QCOW_OFLAG_ZERO) {
    // Explicitly zero
    memset(buf, 0, len);
    return 0;
}
```

## Backing File Chain

Backing files can chain arbitrarily deep:

```
image.qcow2 --> base.qcow2 --> golden.qcow2 --> raw.img
```

Resolution algorithm:
```c
int read_cluster(QCow2Image *img, uint64_t offset, void *buf) {
    uint64_t l2_entry = get_l2_entry(img, offset);

    if (l2_entry == 0) {
        // Unallocated - recurse to backing
        if (img->backing) {
            return read_cluster(img->backing, offset, buf);
        }
        memset(buf, 0, img->cluster_size);
        return 0;
    }

    // Read from this image
    return read_data_cluster(img, l2_entry, buf);
}
```

**Prevent infinite loops** - track chain depth or detect cycles.

## Header Extension Parsing

```c
int parse_header_extensions(const uint8_t *buf, size_t len) {
    size_t offset = header_length;  // Start after header

    while (offset + 8 <= len) {
        uint32_t type = read_be32(buf + offset);
        uint32_t length = read_be32(buf + offset + 4);

        if (type == 0) {
            break;  // End marker
        }

        // Process extension based on type
        switch (type) {
        case 0xe2792aca:  // Backing format
            // ...
            break;
        case 0x0537be77:  // Crypto header
            // ...
            break;
        // Unknown extensions: skip
        }

        // Advance to next (8-byte aligned)
        offset += 8 + ((length + 7) & ~7);
    }

    return 0;
}
```

## L2 Table Caching

L2 tables should be cached for performance:

```c
typedef struct L2Cache {
    uint64_t l1_index;      // Which L1 entry this covers
    uint64_t *entries;      // Cached L2 entries
    bool dirty;             // Modified since load
} L2Cache;

// Simple LRU cache
#define L2_CACHE_SIZE 16
L2Cache l2_cache[L2_CACHE_SIZE];
```

Cache invalidation needed when:
- Switching to different image
- After writes that modify L2 tables

## Compressed Cluster Handling

Decompression requires full cluster buffer:

```c
int read_compressed_cluster(QCow2Image *img, uint64_t l2_entry,
                            uint64_t guest_offset, void *buf, size_t len) {
    uint64_t coffset;
    int csize;
    parse_compressed_entry(l2_entry, &coffset, &csize);

    // Read compressed data
    uint8_t *compressed = malloc(csize);
    pread(img->fd, compressed, csize, coffset);

    // Decompress to full cluster
    uint8_t *decompressed = malloc(img->cluster_size);
    decompress(compressed, csize, decompressed, img->cluster_size);

    // Copy requested range
    size_t in_cluster = guest_offset & (img->cluster_size - 1);
    memcpy(buf, decompressed + in_cluster, len);

    free(compressed);
    free(decompressed);
    return 0;
}
```

## Error Handling

Robust parsing should handle:

```c
typedef enum QCow2Error {
    QCOW2_OK = 0,
    QCOW2_ERR_INVALID_MAGIC,
    QCOW2_ERR_UNSUPPORTED_VERSION,
    QCOW2_ERR_UNKNOWN_FEATURE,      // Incompatible feature set
    QCOW2_ERR_CORRUPT,              // CORRUPT flag set
    QCOW2_ERR_INVALID_CLUSTER_BITS,
    QCOW2_ERR_INVALID_REFCOUNT_ORDER,
    QCOW2_ERR_MISALIGNED_OFFSET,
    QCOW2_ERR_RESERVED_BITS,
    QCOW2_ERR_IO_ERROR,
    QCOW2_ERR_DECOMPRESSION,
    QCOW2_ERR_DECRYPTION,
} QCow2Error;
```

## Consistency Checking

Validate on open:

```c
int validate_qcow2(QCow2Image *img) {
    // Check magic
    if (img->header.magic != QCOW_MAGIC) {
        return QCOW2_ERR_INVALID_MAGIC;
    }

    // Check version
    if (img->header.version < 2 || img->header.version > 3) {
        return QCOW2_ERR_UNSUPPORTED_VERSION;
    }

    // Check incompatible features
    uint64_t unknown = img->header.incompatible_features & ~KNOWN_INCOMPAT;
    if (unknown) {
        return QCOW2_ERR_UNKNOWN_FEATURE;
    }

    // Check corrupt flag
    if (img->header.incompatible_features & QCOW2_INCOMPAT_CORRUPT) {
        return QCOW2_ERR_CORRUPT;
    }

    // Validate cluster_bits
    if (img->header.cluster_bits < 9 || img->header.cluster_bits > 21) {
        return QCOW2_ERR_INVALID_CLUSTER_BITS;
    }

    // Validate refcount_order (v3 only)
    if (img->header.version >= 3 && img->header.refcount_order > 6) {
        return QCOW2_ERR_INVALID_REFCOUNT_ORDER;
    }

    return QCOW2_OK;
}
```

## qemu Implementation Limits

These are qemu-specific but useful guidelines:

| Limit | Value | Notes |
|-------|-------|-------|
| Max L1 table | 32 MB | ~2 PB addressable |
| Max refcount table | 8 MB | ~2 PB clusters |
| Max cluster size | 2 MB | cluster_bits <= 21 |
| Max backing file name | 1023 bytes | |
| Max snapshots | 65536 | |
| Max bitmaps | 65535 | |

## External References

### qemu Source Code

- `block/qcow2.h` - Structure definitions
- `block/qcow2.c` - Core implementation
- `block/qcow2-cluster.c` - Cluster operations
- `block/qcow2-refcount.c` - Reference counting
- `block/qcow2-snapshot.c` - Snapshot handling
- `docs/interop/qcow2.txt` - Official specification

### Other Implementations

- **qcow2-rs** (Rust): https://github.com/panda-re/qcow-rs
- **go-qcow2** (Go): https://github.com/zchee/go-qcow2
- **ocaml-qcow** (OCaml): https://github.com/mirage/ocaml-qcow
- **qcow2-parser** (Python): https://github.com/nirs/qcow2-parser

### Tools

- `qemu-img info` - Display image information
- `qemu-img check` - Validate image consistency
- `qemu-img convert` - Convert between formats
- `qemu-img snapshot` - Manage snapshots

### Articles

- Subcluster allocation: https://blogs.igalia.com/berto/2020/12/03/subcluster-allocation-for-qcow2-images/
- Metadata checks: https://blogs.igalia.com/berto/2017/02/08/qemu-and-the-qcow2-metadata-checks/
- L2 cache tuning: https://www.ibm.com/blog/how-to-tune-qemu-l2-cache-size-and-qcow2-cluster-size/
