# VMDK Compression and StreamOptimized Format

VMDK supports DEFLATE compression for grains, primarily used in the
streamOptimized format for OVF/OVA distribution.

## Compression Algorithm

| Value | Algorithm | Library |
|-------|-----------|---------|
| 0 | None | - |
| 1 | DEFLATE | zlib |

The compression type is stored in `VMDK4Header.compressAlgorithm`.

## Header Flags for Compression

```c
#define VMDK4_FLAG_COMPRESS  (1 << 16)  // Compression enabled
#define VMDK4_FLAG_MARKER    (1 << 17)  // Grain markers present
```

Both flags are typically set together for compressed images.

## Grain Marker Structure

Each compressed grain is prefixed with a marker:

```c
typedef struct VmdkGrainMarker {
    uint64_t lba;     // Logical block address (sector offset)
    uint32_t size;    // Compressed data size in bytes
    uint8_t data[];   // Compressed grain data follows
} qemu_PACKED;
```

**Header size:** 12 bytes

**Total size:** 12 + compressed_size bytes

## Compression Process

### Writing Compressed Grains

```c
// 1. Allocate buffer for marker + compressed data
buf_len = (granularity * 512) * 2;  // 2x uncompressed size
data = malloc(sizeof(VmdkGrainMarker) + buf_len);

// 2. Compress the grain
compress(data->data, &buf_len, uncompressed_data, grain_size);

// 3. Fill marker header
data->lba = cpu_to_le64(offset >> 9);  // Sector offset
data->size = cpu_to_le32(buf_len);     // Compressed size

// 4. Write marker + compressed data
total_size = sizeof(VmdkGrainMarker) + buf_len;
write(extent->file, cluster_offset, data, total_size);
```

### Reading Compressed Grains

```c
// 1. Allocate buffers
cluster_bytes = granularity * 512;
buf_bytes = cluster_bytes * 2;  // Read extra in case data spans
cluster_buf = malloc(buf_bytes);
uncomp_buf = malloc(cluster_bytes);

// 2. Read compressed data
read(extent->file, cluster_offset, cluster_buf, buf_bytes);

// 3. Parse marker
marker = (VmdkGrainMarker *)cluster_buf;
compressed_data = marker->data;
data_len = le32_to_cpu(marker->size);

// 4. Decompress
uncompress(uncomp_buf, &cluster_bytes, compressed_data, data_len);

// 5. Return requested bytes
memcpy(output, uncomp_buf + in_grain_offset, bytes);
```

## Compression Constraints

1. **Whole grains only** - Cannot partially compress a grain
2. **Single-pass writes** - Cannot overwrite already-written grains
3. **Read-only for version 3** - qemu opens v3 compressed images read-only
4. **Marker required** - Compressed grains must have markers

```c
// Cannot write to allocated cluster in streamOptimized
if (extent->compressed && cluster_sector != 0) {
    error("Could not write to allocated cluster for streamOptimized");
    return -EINVAL;
}
```

## StreamOptimized Format

The streamOptimized subformat is designed for OVF/OVA distribution:

### Characteristics

- All grains compressed with DEFLATE
- Grain markers for each compressed grain
- Footer at end of file (for streaming reads)
- Grain directory at end (`gd_offset = VMDK4_GD_AT_END`)

### Creating StreamOptimized Images

```bash
qemu-img create -f vmdk -o subformat=streamOptimized disk.vmdk 10G
qemu-img convert -O vmdk -o subformat=streamOptimized input.raw output.vmdk
```

### File Layout

```
[Offset 0]           Header (VMDK4_MAGIC + VMDK4Header)
[desc_offset]        Descriptor (text)
[...]                Compressed Grains with Markers
                     (sequential, append-only)
[EOF - 1536]         Footer Marker (512 bytes)
[EOF - 1024]         Footer Header (512 bytes)
[EOF - 512]          End-of-Stream Marker (512 bytes)
```

## Marker Types

| Value | Constant | Description |
|-------|----------|-------------|
| 0 | MARKER_END_OF_STREAM | End of file |
| 1 | MARKER_GRAIN_TABLE | Grain table follows |
| 2 | MARKER_GRAIN_DIRECTORY | Grain directory follows |
| 3 | MARKER_FOOTER | Footer header follows |

### Marker Structure (non-grain)

```c
struct Marker {
    uint64_t val;              // Reserved (usually 0)
    uint32_t size;             // Size (usually 0)
    uint32_t type;             // Marker type
    uint8_t pad[512 - 16];     // Padding to sector
} qemu_PACKED;
```

## Footer Structure

The footer enables reading the grain directory location after streaming:

```
+--------------------+ EOF - 1536
| Footer Marker      | type = MARKER_FOOTER
+--------------------+ EOF - 1024
| VMDK4_MAGIC        | "VMDK"
| VMDK4Header        | Copy of header with correct gd_offset
+--------------------+ EOF - 512
| EOS Marker         | type = MARKER_END_OF_STREAM
+--------------------+ EOF
```

### Footer Validation

```c
// Read footer (3 sectors from end)
read(file, file_size - 1536, footer, 1536);

// Validate
if (be32_to_cpu(footer.magic) != VMDK4_MAGIC ||
    le32_to_cpu(footer.footer_marker.type) != MARKER_FOOTER ||
    le32_to_cpu(footer.footer_marker.size) != 0 ||
    le32_to_cpu(footer.eos_marker.type) != MARKER_END_OF_STREAM ||
    le32_to_cpu(footer.eos_marker.size) != 0) {
    return -EINVAL;
}
```

## StreamOptimized Header

Key differences from standard sparse:

```c
VMDK4Header {
    version = 3;
    flags = VMDK4_FLAG_COMPRESS | VMDK4_FLAG_MARKER |
            VMDK4_FLAG_RGD | VMDK4_FLAG_NL_DETECT;
    gd_offset = 0xffffffffffffffff;  // VMDK4_GD_AT_END
    compressAlgorithm = 1;           // DEFLATE
}
```

The `gd_offset = VMDK4_GD_AT_END` signals that the actual grain directory
offset must be read from the footer.

## Compression Performance

### Advantages

- Significantly smaller file size
- Faster network transfers
- Reduced storage costs
- Ideal for image distribution

### Disadvantages

- CPU overhead for compression/decompression
- Cannot modify existing grains
- Read-only in some configurations
- Slower random reads

## Use Cases

1. **OVF/OVA export** - Primary use case
2. **Image distribution** - Reduced download size
3. **Backups** - Compressed archives
4. **Templates** - Read-only base images

## Implementation Notes

1. **Buffer sizing** - Allocate 2x grain size for compressed reads
2. **Marker parsing** - Check size field to determine compressed length
3. **Sequential writes** - Maintain `next_cluster_sector` for appends
4. **Footer sync** - Write footer after all grains complete
5. **Read-only mode** - Open streamOptimized as read-only by default

## References

- qemu source: `block/vmdk.c`
- VMware VDDK 5.0 Technical Note
- zlib library: https://www.zlib.net/
