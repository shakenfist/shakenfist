# Raw Disk Image Format

The raw format is the simplest disk image format - a bit-for-bit representation
of a disk device with no metadata, headers, or structure.

## Overview

Raw images are completely unstructured:
- **No magic header** - Cannot be identified by content alone
- **No metadata** - No embedded size, geometry, or format information
- **Direct data** - Entire file is pure disk data from byte 0
- **Transparent** - Acts as a pass-through to underlying storage

## Format Structure

```
+------------------+
| Sector 0         |  <- First 512 bytes (often MBR/GPT)
+------------------+
| Sector 1         |
+------------------+
| ...              |
+------------------+
| Sector N         |  <- Last sector
+------------------+
```

**That's it.** There is no header, footer, or metadata structure.

## qemu Raw Format Options

qemu's raw driver supports two runtime options:

### offset

```c
uint64_t offset;  // Starting byte offset in backing file
```

- Default: 0 (beginning of file)
- Purpose: Present a view starting at a specific offset
- Constraint: Must not exceed actual file size

### size

```c
uint64_t size;    // Virtual disk size in bytes
bool has_size;    // Whether size is explicitly set
```

- Default: Calculated as `file_size - offset`
- Purpose: Limit visible size independent of file size
- Constraint: Must be multiple of 512 bytes (sector size)

## Sparse File Support

Raw images can be sparse files where unwritten regions don't consume disk space.

### Filesystem Requirements

| Filesystem | Sparse Support | Hole Punching |
|------------|----------------|---------------|
| ext4 | Yes | Linux 3.0+ |
| XFS | Yes | Linux 2.6.38+ |
| Btrfs | Yes | Linux 3.7+ |
| NTFS | Yes | Limited |

### Creating Sparse Files

```bash
# Using truncate (creates sparse file)
truncate -s 10G disk.raw

# Using qemu-img
qemu-img create -f raw disk.raw 10G

# Check actual vs apparent size
du -h disk.raw           # Actual size on disk
du -h --apparent-size disk.raw  # Logical size
```

### Hole Punching

The `FALLOC_FL_PUNCH_HOLE` flag allows returning space to the filesystem:

```c
fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE,
          offset, length);
```

This enables:
- Reclaiming space when VM deletes files
- SSD TRIM passthrough (Linux 3.2+)
- Maintaining sparseness over time

## Preallocation Modes

| Mode | Description | Performance | Creation Speed |
|------|-------------|-------------|----------------|
| off | Sparse, allocate on write | Good | Instant |
| falloc | Reserve space via fallocate() | Better | Fast |
| full | Write zeros to entire file | Best | Slow |

```bash
# Create preallocated raw image
qemu-img create -f raw -o preallocation=full disk.raw 10G
```

## Format Detection

Raw is detected as a **last resort** with the lowest priority score:

```c
static int raw_probe(const uint8_t *buf, int buf_size, const char *filename) {
    return 1;  // Minimum positive score
}
```

qemu examines the first 512 bytes and each format driver returns a confidence
score. Raw always returns 1, so it's selected only if no other format matches.

### Probed Image Restrictions

When raw is auto-detected (not explicitly specified):
- Writes to first 512 bytes trigger re-probing
- If format signature appears, write is rejected with `-EPERM`
- Prevents accidental corruption of formatted images

**Best practice:** Always specify `format=raw` explicitly.

## I/O Operations

All I/O is direct pass-through with offset adjustment:

```c
// Read operation
int raw_co_preadv(offset, bytes) {
    offset += state->offset;  // Adjust for raw image offset
    if (state->has_size && offset > state->size) {
        return -EINVAL;
    }
    return bdrv_co_preadv(file, offset, bytes, ...);
}
```

## Performance Characteristics

Raw images offer the best performance:
- **No metadata overhead** - Direct block access
- **Best IOPS** - Optimal for random I/O
- **Predictable latency** - No COW or compression
- **Ideal for databases** - Recommended for high-performance workloads

### Performance Ranking

```
raw (preallocated) > raw (sparse) > qcow2
```

## Tools

### losetup (Loop Devices)

```bash
# Attach raw image to loop device
sudo losetup -f --show disk.raw

# With partition scanning
sudo losetup -fP disk.raw

# Mount a partition
sudo mount /dev/loop0p1 /mnt

# Cleanup
sudo umount /mnt
sudo losetup -d /dev/loop0
```

### qemu-nbd

```bash
# Load NBD module
sudo modprobe nbd max_part=63

# Connect image
sudo qemu-nbd -f raw -c /dev/nbd0 disk.raw

# Mount partition
sudo mount /dev/nbd0p1 /mnt

# Cleanup
sudo qemu-nbd -d /dev/nbd0
```

### qemu-img

```bash
# Create raw image
qemu-img create -f raw disk.raw 20G

# Get image info
qemu-img info disk.raw

# Convert to/from raw
qemu-img convert -f qcow2 -O raw disk.qcow2 disk.raw
qemu-img convert -f raw -O qcow2 disk.raw disk.qcow2
```

### virt-sparsify

```bash
# Reclaim unused space
virt-sparsify disk.raw output.raw

# In-place sparsification
virt-sparsify --in-place disk.raw
```

## When to Use Raw

**Good for:**
- Performance-critical workloads (databases, HPC)
- Simple deployments and testing
- Data recovery scenarios (simpler format)
- When snapshots aren't needed

**Avoid for:**
- Cloud deployments (qcow2 more storage-efficient)
- When snapshots are needed
- Network transfers (qcow2 transfers better)
- Multi-tenant environments

## Implementation Notes

1. **Sector alignment** - All sizes must be multiples of 512 bytes
2. **Offset addition** - All I/O offsets adjusted by image offset
3. **Bounds checking** - Every operation validates against size limits
4. **No format validation** - Cannot verify if file is actually a raw image

## References

- qemu source: `block/raw-format.c`
- qemu docs: https://qemu-project.gitlab.io/qemu/system/images.html
