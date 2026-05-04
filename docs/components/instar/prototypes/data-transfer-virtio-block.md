# Virtio-block for KVM Guests

This document describes virtio-block, a block device interface that can be
used for data transfer between KVM guests and the host.

## Overview

Virtio-block provides a standardized block device interface over the virtio
transport. It is the **recommended mechanism for large file transfers**,
particularly disk images ranging from tens of gigabytes to hundreds of
gigabytes.

## Large File Support

Virtio-block is ideal for processing large disk images because:

- **64-bit sector addressing**: The `sector` field in requests is 64-bit,
  supporting devices up to 8 exabytes (2^64 × 512 bytes)
- **No memory mapping required**: Guest reads/writes in chunks without
  needing to map the entire file into memory
- **Random access**: Can seek to any position, essential for sparse files
  and format conversion
- **Efficient batching**: Multiple requests can be queued for throughput
- **Natural semantics**: Input IS a disk image, output IS a disk image

### Capacity Calculation

```
Maximum capacity = 2^64 sectors × 512 bytes/sector = 8 exabytes

For a 100 GB image:
  sectors = 100 × 1024³ / 512 = 209,715,200 sectors
  (easily within 64-bit range)
```

## Device Configuration

### Config Structure

```c
struct virtio_blk_config {
    __le64 capacity;              // Capacity in 512-byte sectors
    __le32 size_max;              // Maximum segment size
    __le32 seg_max;               // Maximum segments per request
    struct {
        __le16 cylinders;
        __u8 heads;
        __u8 sectors;
    } geometry;
    __le32 blk_size;              // Block size (typically 512)
    __le32 opt_io_size;           // Optimal I/O size
    __le16 num_queues;            // Number of virtqueues (if MQ)
    // ... additional fields for advanced features
};
```

### Feature Bits

| Feature | Description |
|---------|-------------|
| `VIRTIO_BLK_F_SIZE_MAX` | Max segment size available |
| `VIRTIO_BLK_F_SEG_MAX` | Max segments per request |
| `VIRTIO_BLK_F_RO` | Device is read-only |
| `VIRTIO_BLK_F_BLK_SIZE` | Block size available |
| `VIRTIO_BLK_F_MQ` | Multiple virtqueues support |
| `VIRTIO_BLK_F_DISCARD` | Discard command support |
| `VIRTIO_BLK_F_WRITE_ZEROES` | Write zeroes support |

## Command Types

| Type | Value | Description |
|------|-------|-------------|
| `VIRTIO_BLK_T_IN` | 0 | Read data |
| `VIRTIO_BLK_T_OUT` | 1 | Write data |
| `VIRTIO_BLK_T_FLUSH` | 4 | Flush cache |
| `VIRTIO_BLK_T_GET_ID` | 8 | Get device ID |
| `VIRTIO_BLK_T_DISCARD` | 11 | Discard sectors |
| `VIRTIO_BLK_T_WRITE_ZEROES` | 13 | Write zeros |

## Request Format

### Request Header (Out)

```c
struct virtio_blk_outhdr {
    __le32 type;      // Command type (T_IN, T_OUT, etc.)
    __le32 ioprio;    // I/O priority
    __le64 sector;    // Starting sector (512-byte units)
};
```

### Status (In)

```c
struct virtio_blk_inhdr {
    __u8 status;      // VIRTIO_BLK_S_OK, S_IOERR, S_UNSUPP
};
```

### Status Codes

| Status | Value | Meaning |
|--------|-------|---------|
| `VIRTIO_BLK_S_OK` | 0 | Success |
| `VIRTIO_BLK_S_IOERR` | 1 | I/O error |
| `VIRTIO_BLK_S_UNSUPP` | 2 | Unsupported operation |

## Virtqueue Structure

Virtio-block typically uses a single virtqueue (or multiple with MQ feature).

### Descriptor Chain Layout

For a read operation:

```
Descriptor 0 (OUT): virtio_blk_outhdr  [type=T_IN, sector=N]
Descriptor 1 (IN):  data buffer        [size bytes to read]
Descriptor 2 (IN):  status byte        [1 byte]
```

For a write operation:

```
Descriptor 0 (OUT): virtio_blk_outhdr  [type=T_OUT, sector=N]
Descriptor 1 (OUT): data buffer        [data to write]
Descriptor 2 (IN):  status byte        [1 byte]
```

### Scatter-Gather Support

Multiple data descriptors can be chained:

```
[outhdr] -> [data chunk 1] -> [data chunk 2] -> ... -> [status]
```

This allows large transfers without contiguous buffers.

## VMM Implementation (Host Side)

### Device Setup

```c
struct VirtIOBlock {
    VirtIODevice parent;
    BlockBackend *blk;        // Storage backend
    VirtQueue *vq;            // Request queue
    uint64_t capacity;        // Size in sectors
};
```

### Request Processing

```c
void virtio_blk_handle_request(VirtIOBlock *s, VirtQueueElement *elem) {
    struct virtio_blk_outhdr hdr;
    uint8_t status = VIRTIO_BLK_S_OK;

    // Read header from first descriptor
    iov_to_buf(elem->out_sg, elem->out_num, 0, &hdr, sizeof(hdr));

    uint64_t offset = hdr.sector * 512;
    size_t len = /* calculate from descriptors */;

    switch (hdr.type) {
    case VIRTIO_BLK_T_IN:
        // Read from backend into guest buffers
        blk_aio_preadv(s->blk, offset, &qiov, 0, complete_cb, req);
        break;

    case VIRTIO_BLK_T_OUT:
        // Write guest data to backend
        blk_aio_pwritev(s->blk, offset, &qiov, 0, complete_cb, req);
        break;

    case VIRTIO_BLK_T_FLUSH:
        blk_aio_flush(s->blk, complete_cb, req);
        break;
    }
}

void complete_cb(void *opaque, int ret) {
    // Set status byte
    status = (ret == 0) ? VIRTIO_BLK_S_OK : VIRTIO_BLK_S_IOERR;
    iov_from_buf(elem->in_sg, elem->in_num, status_offset, &status, 1);

    // Return to guest
    virtqueue_push(vq, elem, len);
    virtio_notify(vdev, vq);
}
```

## Guest Implementation (Bare-Metal)

### Minimal Driver Structure

```rust
struct VirtioBlock {
    vq: Virtqueue,
    capacity: u64,
    blk_size: u32,
}

impl VirtioBlock {
    fn read(&mut self, sector: u64, buf: &mut [u8]) -> Result<(), Error> {
        let hdr = VirtioBlkOutHdr {
            type_: VIRTIO_BLK_T_IN,
            ioprio: 0,
            sector,
        };
        let mut status: u8 = 0;

        // Build descriptor chain
        let descs = [
            Descriptor::out(&hdr),      // Header (device reads)
            Descriptor::in_(buf),       // Data (device writes)
            Descriptor::in_(&mut status), // Status (device writes)
        ];

        self.vq.add(&descs);
        self.vq.kick();
        self.vq.wait_completion();

        match status {
            VIRTIO_BLK_S_OK => Ok(()),
            _ => Err(Error::IoError),
        }
    }

    fn write(&mut self, sector: u64, buf: &[u8]) -> Result<(), Error> {
        let hdr = VirtioBlkOutHdr {
            type_: VIRTIO_BLK_T_OUT,
            ioprio: 0,
            sector,
        };
        let mut status: u8 = 0;

        let descs = [
            Descriptor::out(&hdr),      // Header
            Descriptor::out(buf),       // Data (device reads)
            Descriptor::in_(&mut status), // Status
        ];

        self.vq.add(&descs);
        self.vq.kick();
        self.vq.wait_completion();

        match status {
            VIRTIO_BLK_S_OK => Ok(()),
            _ => Err(Error::IoError),
        }
    }
}
```

### Data Transfer Pattern

For using virtio-block as a data transfer mechanism:

```rust
// Input: Read from "disk" at sector 0
block_dev.read(0, &mut input_buffer)?;

// Process data...
let output = process(&input_buffer);

// Output: Write to "disk" at sector N
let output_sector = input_sectors;  // After input data
block_dev.write(output_sector, &output)?;

// Signal completion (e.g., write to status sector)
block_dev.write(STATUS_SECTOR, &[COMPLETE_FLAG])?;
```

## Advantages

- **Standardized**: Well-defined specification
- **Mature**: Extensively tested in production
- **Efficient**: Designed for high-throughput I/O
- **Scatter-Gather**: Native support for fragmented buffers
- **Async**: Natural async completion model

## Limitations

- **Block Semantics**: Data must fit block model (sectors)
- **Overhead**: Request header per operation
- **Complexity**: Full virtio stack required
- **Alignment**: 512-byte sector alignment typical

## Using for Data Transfer

Virtio-block can serve as a data transfer mechanism:

### Approach 1: Memory-Backed Block Device

VMM creates a block device backed by host memory:

```c
// Create memory-backed block device
size_t size = 64 * 1024 * 1024;  // 64MB
void *buffer = mmap(NULL, size, PROT_READ|PROT_WRITE, ...);

// Configure as virtio-blk backend
BlockBackend *blk = blk_new_with_memory(buffer, size);
```

Guest reads/writes to "disk" actually access shared memory.

### Approach 2: Pipe-Like Usage

Designate regions of the virtual disk:

| Sector Range | Purpose |
|--------------|---------|
| 0 - 1023 | Input data (host writes, guest reads) |
| 1024 - 2047 | Output data (guest writes, host reads) |
| 2048 | Status/control |

### Approach 3: Ring Buffer

Implement a ring buffer protocol over block sectors:

```
Sector 0: Ring metadata (head, tail pointers)
Sector 1-N: Ring data entries
```

## Implementation Complexity

### From Scratch

For a bare-metal guest without existing crates:

1. Virtqueue driver: ~500-1000 lines
2. Block protocol: ~200-400 lines
3. Request handling: ~200-300 lines

Total: ~900-1700 lines for minimal implementation

### With rust-vmm Crates

The [rust-vmm](https://github.com/rust-vmm/community) ecosystem dramatically
reduces implementation effort:

**VMM Side:**

| Crate | Lines Saved | Purpose |
|-------|-------------|---------|
| [virtio-queue](https://crates.io/crates/virtio-queue) | ~500 | Virtqueue handling |
| [virtio-blk](https://github.com/rust-vmm/vm-virtio) | ~200 | Request parsing |
| [vm-memory](https://crates.io/crates/vm-memory) | ~100 | Guest memory access |

**Guest Side:**

| Crate | Lines Saved | Purpose |
|-------|-------------|---------|
| [virtio-drivers](https://crates.io/crates/virtio-drivers) | ~800 | Complete `no_std` driver |

**Revised totals with crates:**

| Component | Without Crates | With Crates |
|-----------|----------------|-------------|
| VMM | ~800 lines | ~300 lines |
| Guest | ~800 lines | ~150 lines |
| **Total** | **~1600 lines** | **~450 lines** |

### Example: Using virtio-drivers in Guest

```rust
#![no_std]
use virtio_drivers::{VirtIOBlk, Hal, MmioTransport};

// Implement Hal trait for your environment
struct MyHal;
impl Hal for MyHal {
    fn dma_alloc(pages: usize) -> (PhysAddr, NonNull<u8>) { /* ... */ }
    fn dma_dealloc(paddr: PhysAddr, _vaddr: NonNull<u8>, pages: usize) { /* ... */ }
    fn phys_to_virt(paddr: PhysAddr) -> NonNull<u8> { /* ... */ }
    // ...
}

// Create and use the block device
let transport = unsafe { MmioTransport::new(header_ptr) }?;
let mut blk = VirtIOBlk::<MyHal, MmioTransport>::new(transport)?;

// Read sectors
let mut buffer = [0u8; 512];
blk.read_blocks(0, &mut buffer)?;

// Write sectors
blk.write_blocks(100, &data)?;
```

### Example: VMM with rust-vmm

```rust
use virtio_queue::{Queue, QueueState};
use virtio_blk::request::{Request, RequestType};
use vm_memory::GuestMemoryMmap;

// Process a request from the guest
fn handle_request(queue: &mut Queue, mem: &GuestMemoryMmap) {
    while let Some(mut desc_chain) = queue.pop_descriptor_chain(mem) {
        let request = Request::parse(&mut desc_chain, mem).unwrap();

        match request.request_type() {
            RequestType::In => {
                // Read from backing file into guest buffer
                let offset = request.sector() * 512;
                // ... pread into desc_chain buffers
            }
            RequestType::Out => {
                // Write from guest buffer to backing file
                // ... pwrite from desc_chain buffers
            }
            RequestType::Flush => {
                // fsync backing file
            }
        }

        // Signal completion
        queue.add_used(mem, desc_chain.head_index(), 0).unwrap();
    }
}
```

## Comparison with Direct Memory

| Aspect | Direct Memory | Virtio-block |
|--------|---------------|--------------|
| Complexity | Low | Medium |
| Overhead | None | Header per request |
| Flexibility | Fixed layout | Sector-addressable |
| Standards | Custom | VIRTIO spec |
| Tooling | None | Standard block tools |

## Use Cases

Virtio-block is suitable when:

- Block device semantics are natural
- Standard tooling is valuable
- Variable-size transfers needed
- Integration with existing block layer

Consider alternatives when:

- Lowest latency required (direct memory for small data)
- Simple fixed-size transfers (direct memory)
- Streaming without random access (virtio-vsock)

## References

- [VIRTIO Specification](https://docs.oasis-open.org/virtio/virtio/v1.1/virtio-v1.1.html)
- [Linux virtio_blk.h](https://github.com/torvalds/linux/blob/master/include/uapi/linux/virtio_blk.h)
- [Linux virtio_blk.c](https://github.com/torvalds/linux/blob/master/drivers/block/virtio_blk.c)
- [rust-vmm/vm-virtio](https://github.com/rust-vmm/vm-virtio) - VMM-side crates
- [virtio-drivers](https://github.com/rcore-os/virtio-drivers) - Guest-side crate
- [Firecracker virtio-block](https://github.com/firecracker-microvm/firecracker) - Production example
