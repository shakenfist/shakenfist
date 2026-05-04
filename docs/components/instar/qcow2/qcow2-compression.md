# QCOW2 Compression System

QCOW2 supports transparent compression of data clusters, reducing storage
requirements for compressible data.

## Supported Compression Types

| Value | Name | Description |
|-------|------|-------------|
| 0 | ZLIB | Default, always supported (deflate algorithm) |
| 1 | ZSTD | Optional, requires qemu compiled with zstd support |

The compression type is stored at header offset 104 (version 3+ only).

### Feature Flag Requirements

- **ZLIB:** No incompatible feature flag required (backward compatible)
- **ZSTD:** Incompatible feature bit 3 (COMPRESSION) must be set

```c
if (compression_type != ZLIB) {
    if (!(incompatible_features & QCOW2_INCOMPAT_COMPRESSION)) {
        // Error: feature bit must be set for non-ZLIB
    }
}
```

## Compressed Cluster Storage

Compressed clusters are stored differently from normal clusters:

1. **Not cluster-aligned:** Compressed data can start at any 512-byte boundary
2. **Variable size:** Stored size depends on compression ratio
3. **Sector granularity:** Size tracked in 512-byte sectors

### Compressed L2 Entry Format

```
 63  62  61                      csize_shift                       0
+---+---+------------------------+----------------------------------+
| 0 | 1 | Sectors - 1 (n bits)   |    Compressed Offset (m bits)    |
+---+---+------------------------+----------------------------------+
      ^           ^                              ^
      |           |                              |
      |           +-- Number of 512B sectors     +-- Byte offset in file
      +-- COMPRESSED = 1 (COPIED always 0)
```

The bit positions depend on cluster size:

```c
csize_shift = 62 - (cluster_bits - 8);
csize_mask = (1 << (cluster_bits - 8)) - 1;
cluster_offset_mask = (1ULL << csize_shift) - 1;
```

### Bit Field Sizes by Cluster Size

| Cluster Size | cluster_bits | csize_shift | Offset Bits | Sectors Bits |
|--------------|--------------|-------------|-------------|--------------|
| 512 B | 9 | 61 | 61 | 1 |
| 4 KB | 12 | 58 | 58 | 4 |
| 64 KB | 16 | 54 | 54 | 8 |
| 1 MB | 20 | 50 | 50 | 12 |
| 2 MB | 21 | 49 | 49 | 13 |

### Parsing Compressed Entries

```c
void parse_compressed_l2_entry(uint64_t l2_entry, int cluster_bits,
                               uint64_t *offset, int *size) {
    int csize_shift = 62 - (cluster_bits - 8);
    int csize_mask = (1 << (cluster_bits - 8)) - 1;
    uint64_t offset_mask = (1ULL << csize_shift) - 1;

    *offset = l2_entry & offset_mask;

    int nb_sectors = ((l2_entry >> csize_shift) & csize_mask) + 1;
    *size = nb_sectors * 512 - (*offset & 511);  // Adjust for alignment
}
```

## Compression Process

### Writing Compressed Data

```
1. Compress cluster data using selected algorithm
2. If compressed_size >= cluster_size:
   - Store as uncompressed (no benefit)
3. Calculate number of 512-byte sectors needed
4. Allocate contiguous space (may not be cluster-aligned)
5. Write compressed data
6. Create L2 entry with COMPRESSED flag and size/offset
```

### Reading Compressed Data

```
1. Parse L2 entry to get compressed offset and size
2. Allocate buffer for compressed data
3. Read compressed data from file
4. Allocate buffer for decompressed cluster (full cluster_size)
5. Decompress data
6. Return requested range from decompressed buffer
```

## ZLIB Implementation Details

### Compression

```c
ssize_t qcow2_zlib_compress(void *dest, size_t dest_size,
                            const void *src, size_t src_size) {
    z_stream strm = {0};

    // Initialize for raw deflate (no zlib header)
    deflateInit2(&strm, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                 -12,    // Window bits: -12 = raw deflate
                 9,      // Memory level
                 Z_DEFAULT_STRATEGY);

    strm.avail_in = src_size;
    strm.next_in = src;
    strm.avail_out = dest_size;
    strm.next_out = dest;

    int ret = deflate(&strm, Z_FINISH);

    deflateEnd(&strm);

    if (ret == Z_STREAM_END) {
        return dest_size - strm.avail_out;  // Compressed size
    }
    return -1;  // Compression failed or didn't fit
}
```

### Decompression

```c
ssize_t qcow2_zlib_decompress(void *dest, size_t dest_size,
                              const void *src, size_t src_size) {
    z_stream strm = {0};

    // Initialize for raw inflate (no zlib header)
    inflateInit2(&strm, -12);

    strm.avail_in = src_size;
    strm.next_in = src;
    strm.avail_out = dest_size;
    strm.next_out = dest;

    int ret = inflate(&strm, Z_FINISH);

    inflateEnd(&strm);

    // Z_BUF_ERROR is OK if output buffer exactly filled
    if ((ret == Z_STREAM_END || ret == Z_BUF_ERROR) && strm.avail_out == 0) {
        return 0;  // Success
    }
    return -1;  // Decompression failed
}
```

**Key point:** Window bits = -12 means raw DEFLATE without zlib header.

## ZSTD Implementation Details

### Compression

```c
ssize_t qcow2_zstd_compress(void *dest, size_t dest_size,
                            const void *src, size_t src_size) {
    ZSTD_CCtx *cctx = ZSTD_createCCtx();

    ZSTD_outBuffer output = { dest, dest_size, 0 };
    ZSTD_inBuffer input = { src, src_size, 0 };

    size_t ret = ZSTD_compressStream2(cctx, &output, &input, ZSTD_e_end);

    ZSTD_freeCCtx(cctx);

    if (ret == 0) {
        return output.pos;  // Compressed size
    }
    return -1;  // Compression failed
}
```

### Decompression

```c
ssize_t qcow2_zstd_decompress(void *dest, size_t dest_size,
                              const void *src, size_t src_size) {
    ZSTD_DCtx *dctx = ZSTD_createDCtx();

    ZSTD_outBuffer output = { dest, dest_size, 0 };
    ZSTD_inBuffer input = { src, src_size, 0 };

    while (output.pos < output.size) {
        size_t ret = ZSTD_decompressStream(dctx, &output, &input);
        if (ZSTD_isError(ret)) {
            ZSTD_freeDCtx(dctx);
            return -1;
        }
    }

    ZSTD_freeDCtx(dctx);
    return 0;  // Success
}
```

## Threading

qemu offloads compression/decompression to a thread pool:

```c
#define QCOW2_MAX_THREADS 4  // Maximum concurrent operations
```

This prevents blocking the main I/O path during CPU-intensive compression.

## Constraints and Limitations

1. **Compressed clusters cannot be modified in-place**
   - Must decompress, modify, recompress to new location
   - Old compressed data freed when refcount drops

2. **COPIED flag is never set for compressed clusters**
   - Cannot use COW optimization
   - Always treated as potentially shared

3. **Subclusters not supported with compression**
   - Extended L2 bitmap must be 0 for compressed clusters
   - Entire cluster compressed as unit

4. **Compression not always beneficial**
   - Already-compressed data (JPEG, ZIP) may expand
   - Incompressible data stored uncompressed

5. **Random access penalty**
   - Must decompress entire cluster to read any part
   - Sequential reads can buffer decompressed data

## Compression Ratios

Typical results vary by data type:

| Data Type | Typical Ratio |
|-----------|---------------|
| Empty/zero | Near 0% (use ZERO clusters instead) |
| Text/code | 20-40% |
| Binaries | 50-70% |
| Pre-compressed | 95-105% (may expand!) |

## Best Practices

1. **Don't compress already-compressed data**
   - VM disk images often contain compressed files

2. **Consider cluster size vs compression**
   - Larger clusters compress better (more context)
   - But waste more space for small writes

3. **Use ZERO clusters for zeroed regions**
   - More efficient than compressing zeros
   - Instant read without decompression

4. **Balance CPU vs storage**
   - ZSTD: better ratio, more CPU
   - ZLIB: faster, slightly worse ratio

5. **Avoid compression for frequently-written data**
   - Compression overhead on every write
   - Better for read-heavy or archival images
