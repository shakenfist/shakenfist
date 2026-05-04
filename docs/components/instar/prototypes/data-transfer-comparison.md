# Data Transfer Mechanisms: Comparison

This document compares the various data transfer mechanisms available for
bare-metal KVM guests, helping you choose the right approach for your use case.

## Large File Considerations

For Instar, we must handle disk images ranging from tens of gigabytes to
hundreds of gigabytes. This significantly constrains our options:

- **Direct memory mapping of entire files is impractical** - A 100GB image
  cannot be fully mapped into guest physical address space
- **Chunked/streaming processing is required** - Must read/write in manageable
  pieces
- **64-bit addressing is essential** - Mechanisms limited to 32-bit offsets
  cannot address large files
- **Throughput matters** - Processing 100GB at 100MB/s takes ~17 minutes;
  protocol overhead adds up

## Overview

| Mechanism | Best For | Max Practical Size | Implementation |
|-----------|----------|-------------------|----------------|
| [Virtio-block](/components/instar/prototypes/data-transfer-virtio-block/) | Large files, disk images | Unlimited (64-bit sectors) | Medium |
| [Virtio-vsock](/components/instar/prototypes/data-transfer-virtio-vsock/) | Streaming, bidirectional | Unlimited (streaming) | High |
| [Custom MMIO](/components/instar/prototypes/data-transfer-other/#custom-mmio-device) | Prototyping, simple I/O | Unlimited (64-bit offset) | Low-Medium |
| [Direct Memory](/components/instar/prototypes/data-transfer-direct-memory/) | Small fixed-size data | ~1-2 GB | Low |
| [ioeventfd/irqfd](/components/instar/prototypes/data-transfer-other/#ioeventfd--irqfd) | Notifications only | N/A | Low |
| Port I/O | Control/status | Bytes | Very Low |

## Detailed Comparison

### Performance Characteristics

| Mechanism | Throughput | Latency | VM Exits |
|-----------|-----------|---------|----------|
| Direct Memory | Very High | Very Low | 1 (completion) |
| Virtio-vsock | High | Low | Per-batch |
| Virtio-block | High | Low | Per-batch |
| Custom MMIO | High | Low | 0 (with ioeventfd) |
| ioeventfd | N/A | Very Low | 0 |
| Port I/O | Very Low | Medium | Per-byte |
| MMIO | Very Low | Medium | Per-access |

### Implementation Complexity

**From scratch:**

| Mechanism | Guest Code | VMM Code | Protocol |
|-----------|-----------|----------|----------|
| Direct Memory | ~50 lines | ~100 lines | Custom |
| Virtio-vsock | ~1500 lines | ~1500 lines | Standard |
| Virtio-block | ~800 lines | ~800 lines | Standard |
| Custom MMIO | ~200 lines | ~400 lines | Custom |
| ioeventfd/irqfd | ~30 lines | ~50 lines | Custom |
| Port I/O | ~10 lines | ~50 lines | Custom |

**With rust-vmm crates** (see [Rust Crate Ecosystem](#rust-crate-ecosystem)):

| Mechanism | Guest Code | VMM Code | Notes |
|-----------|-----------|----------|-------|
| Direct Memory | ~50 lines | ~80 lines | vm-memory helps |
| Virtio-vsock | ~200 lines | ~500 lines | virtio-drivers + virtio-vsock |
| Virtio-block | ~150 lines | ~300 lines | virtio-drivers + virtio-blk |
| Custom MMIO | ~200 lines | ~350 lines | kvm-ioctls for ioeventfd |

The rust-vmm ecosystem significantly reduces virtio complexity, making it the
clear winner for production use.

### Feature Comparison

| Feature | Direct Memory | Custom MMIO | Virtio-vsock | Virtio-block |
|---------|---------------|-------------|--------------|--------------|
| **Max addressable size** | ~2 GB practical | 16 EB (64-bit offset) | Unlimited | 8 EB (64-bit sectors) |
| **Large file support** | Requires chunking | Native (chunked) | Native streaming | Native |
| Bidirectional | ✓ (separate regions) | ✓ (commands) | ✓ (native) | ✓ (read/write) |
| Streaming | ✗ | ✗ | ✓ | ✗ |
| Random access | ✓ | ✓ | ✗ | ✓ |
| Message boundaries | ✗ | ✓ (commands) | ✓ (SEQPACKET) | ✗ |
| Flow control | Manual | Manual | Built-in | N/A |
| Zero-copy | ✓ | ✓ | Possible | Possible |
| Standard protocol | ✗ | ✗ | ✓ | ✓ |
| VM exit free | ✗ | ✓ (ioeventfd) | ✗ | ✗ |

## Decision Matrix

### Choose Virtio-block When:

- ✓ **Large files (GBs to TBs)**
- ✓ Block/disk device semantics are natural
- ✓ Random access patterns needed
- ✓ Processing disk images (natural fit)
- ✓ 64-bit addressing required

**Example:** Disk image transcoding, large file processing, backup/restore

### Choose Virtio-vsock When:

- ✓ **Streaming large data** (sequential processing)
- ✓ Bidirectional communication needed
- ✓ Multiple independent channels required
- ✓ Socket semantics are natural fit
- ✓ Don't need random access

**Example:** Log streaming, RPC services, pipeline processing

### Choose Custom MMIO Device When:

- ✓ Learning/educational purposes
- ✓ Need protocol flexibility virtio doesn't offer
- ✓ Zero VM exit overhead is critical
- ✓ Very simple request/response patterns
- ✗ No existing tooling or ecosystem support
- ✗ **Note**: With rust-vmm crates, virtio-block is now similarly easy

**Example:** Learning projects, specialized protocols, when virtio is overkill

> **Recommendation changed**: Previously, Custom MMIO was recommended for
> prototyping due to lower complexity. With rust-vmm crates reducing
> virtio-block to ~450 lines, this advantage is largely eliminated.

### Choose Direct Memory When:

- ✓ **Small data only** (< 1-2 GB total)
- ✓ Single-shot processing
- ✓ Lowest possible latency required
- ✓ Minimal implementation complexity preferred
- ✗ NOT suitable for large disk images

**Example:** Small config processing, cryptographic operations, metadata extraction

### Choose ioeventfd/irqfd When:

- ✓ Notification-only (no data payload)
- ✓ Lowest latency signaling required
- ✓ Combined with shared memory for data
- ✓ Zero VM exit overhead critical

**Example:** Completion notifications, doorbell patterns, work queues

### Choose Port I/O When:

- ✓ Very simple status/control data
- ✓ Compatibility with existing code
- ✓ x86 architecture only acceptable
- ✓ Minimal implementation effort

**Example:** Debug output, simple status reporting

## Architecture Recommendations

### For Instar (Disk Image Processing)

**Primary recommendation: Virtio-block**

Rationale:
- Disk images are large (10s-100s of GB) - cannot map entirely into memory
- 64-bit sector addressing supports files up to 8 exabytes
- Natural fit for disk-like operations (the input IS a disk image)
- Random access allows efficient sparse file handling
- Guest reads/writes in chunks without memory constraints
- Well-understood protocol with mature implementations

Architecture:
```
VMM Side:
- Input image file  → virtio-blk device 0 (read-only)
- Output image file → virtio-blk device 1 (read-write)

Guest Side:
- Read from device 0, process, write to device 1
- Use scatter-gather for efficient large transfers
- Process in chunks (e.g., 1MB at a time)
```

**Alternative: Virtio-vsock (for streaming)**

If processing is strictly sequential (no random access needed):
- Stream input through vsock connection
- Stream output through separate vsock connection
- Lower implementation complexity than virtio-block
- But cannot seek - must process linearly

**Alternative: Custom MMIO Device (for learning)**

With rust-vmm crates, Custom MMIO has lost its complexity advantage:
- Custom MMIO: ~550 lines
- Virtio-block with crates: ~450 lines
- Virtio-block gains: batching, scatter-gather, tooling, ecosystem

Custom MMIO is still useful for:
- Learning how device emulation works
- Protocols that don't fit virtio semantics
- When zero VM exits is absolutely critical

**NOT recommended: Direct Memory**

While simpler to implement, direct memory is unsuitable because:
- Cannot map 100GB image into guest address space
- Would require complex chunking protocol on top
- Loses random access capability
- No advantage over virtio-block for this use case

### Hybrid Approaches

#### Direct Memory + ioeventfd

For zero-exit data transfer with completion notification:

```
1. VMM sets up shared memory for input/output
2. VMM registers ioeventfd on doorbell address
3. Guest processes input, writes to output
4. Guest writes to doorbell (triggers ioeventfd)
5. VMM receives eventfd notification
6. VMM reads output from shared memory
```

#### Direct Memory + Port I/O

For progress reporting during long operations:

```
1. Guest periodically writes progress to port 0x80
2. VMM polls port I/O ring (coalesced)
3. On completion, guest writes final status
4. Guest executes HLT
```

## Performance Guidelines

### Minimizing VM Exits

| Pattern | Exits | Notes |
|---------|-------|-------|
| HLT on completion | 1 | Simplest |
| ioeventfd doorbell | 0* | Requires kernel support |
| Coalesced port I/O | ~1 per batch | Good for progress |
| Virtio batching | 1 per batch | Natural with virtio |

*ioeventfd doesn't cause traditional VM exit but does require kernel handling.

### Buffer Sizing

| Total Data Size | Recommended Approach |
|-----------------|---------------------|
| < 4 KB | Port I/O, MMIO, or direct memory |
| 4 KB - 1 GB | Direct memory (single mapping) |
| 1 GB - 10 GB | Virtio-block, custom MMIO, or direct memory with chunking |
| 10 GB - 1 TB | **Virtio-block** or custom MMIO (for prototyping) |
| > 1 TB | Virtio-block with careful memory management |

### Large File Processing Strategy

For files in the 10-100+ GB range:

1. **Use virtio-block** with file-backed storage
2. **Process in chunks** (1-16 MB is typical)
3. **Minimize seeks** by processing sequentially when possible
4. **Use scatter-gather** for efficient descriptor usage
5. **Batch requests** to reduce virtqueue overhead

### Memory Alignment

For optimal performance:

- Align buffers to 2MB boundaries for huge pages
- Use page-aligned (4KB) sizes at minimum
- Match guest and host buffer alignment

## Security Considerations

| Mechanism | Isolation | Attack Surface |
|-----------|-----------|----------------|
| Direct Memory | High | Minimal (just memory) |
| Custom MMIO | High | Custom protocol parsing |
| Virtio-vsock | High | Protocol parsing |
| Virtio-block | High | Block protocol |
| Port I/O | High | Simple |
| VFIO | Medium | Hardware dependent |

For security-sensitive workloads (like Instar):

1. Use read-only memory regions for input
2. Validate output size before reading
3. Clear memory after use
4. Consider memory encryption (SEV/TDX)

## Summary Recommendations

### For Instar Project

| Phase | Mechanism | Rationale |
|-------|-----------|-----------|
| Prototype | Direct Memory | Validates VMM/guest basics (small test files) |
| MVP | **Virtio-block** | rust-vmm crates make this ~450 lines; supports 10-100+ GB |
| Production | Virtio-block + optimizations | Batch requests, scatter-gather, sparse handling |

> **Note**: With the rust-vmm ecosystem, the "Prototype+" phase using Custom
> MMIO is no longer recommended. Virtio-block implementation effort is now
> comparable (~450 vs ~550 lines) while providing better features and tooling.

### General Guidelines

1. **Use rust-vmm crates**: They reduce virtio complexity by 70%+
2. **Consider data size first**: Large files (>1GB) require virtio-block or vsock
3. **Match semantics**: Disk images → virtio-block; streams → virtio-vsock
4. **Don't over-engineer**: Direct memory is fine for small data
5. **Profile before optimizing**: Measure actual I/O bottlenecks
6. **Consider security**: Use read-only devices for input, clear memory

## Rust Crate Ecosystem

The [rust-vmm](https://github.com/rust-vmm/community) project provides
production-tested virtualization components used by Firecracker (AWS
Lambda/Fargate), crosvm (ChromeOS), and Cloud Hypervisor. This significantly
reduces implementation effort for virtio-based mechanisms.

### VMM-Side Crates

| Crate | Purpose | Used By |
|-------|---------|---------|
| [kvm-ioctls](https://crates.io/crates/kvm-ioctls) | Safe KVM API wrappers (ioeventfd, irqfd) | All |
| [kvm-bindings](https://crates.io/crates/kvm-bindings) | KVM FFI bindings | All |
| [vm-memory](https://crates.io/crates/vm-memory) | Guest memory abstraction | All |
| [virtio-queue](https://crates.io/crates/virtio-queue) | Virtqueue implementation | Virtio devices |
| [virtio-blk](https://github.com/rust-vmm/vm-virtio) | Block device request parsing | Virtio-block |
| [virtio-vsock](https://crates.io/crates/virtio-vsock) | Vsock packet abstraction | Virtio-vsock |

### Guest-Side Crates

| Crate | Purpose | Notes |
|-------|---------|-------|
| [virtio-drivers](https://crates.io/crates/virtio-drivers) | `no_std` guest drivers | Requires `Hal` trait impl |

The [virtio-drivers](https://github.com/rcore-os/virtio-drivers) crate from
rcore-os provides ready-to-use drivers for bare-metal guests:

- virtio-blk (block devices)
- virtio-net (networking)
- virtio-gpu (graphics)
- virtio-console (serial)
- virtio-input (keyboard/mouse)

### Impact on Mechanism Choice

With rust-vmm crates, the complexity gap between custom solutions and virtio
shrinks dramatically:

| Approach | Without Crates | With Crates | Reduction |
|----------|----------------|-------------|-----------|
| Virtio-block | ~1600 lines | ~450 lines | 72% |
| Virtio-vsock | ~3000 lines | ~700 lines | 77% |
| Custom MMIO | ~600 lines | ~550 lines | 8% |

**Recommendation**: The rust-vmm ecosystem makes virtio-block the clear choice
for Instar. The implementation effort is now comparable to custom solutions,
while gaining batching, scatter-gather, error handling, and tooling for free.

### Example: VMM with rust-vmm

```rust
use kvm_ioctls::{Kvm, VmFd};
use virtio_queue::Queue;
use virtio_blk::request::Request;
use vm_memory::GuestMemoryMmap;

// Queue setup handled by virtio-queue
let queue = Queue::new(256)?;

// Request parsing handled by virtio-blk
let request = Request::parse(&desc_chain, &mem)?;
match request.request_type() {
    RequestType::In => { /* read */ }
    RequestType::Out => { /* write */ }
}
```

### Example: Guest with virtio-drivers

```rust
#![no_std]
use virtio_drivers::{VirtIOBlk, Hal};

// Implement HAL for your environment
impl Hal for MyHal {
    fn dma_alloc(pages: usize) -> PhysAddr { /* ... */ }
    fn phys_to_virt(paddr: PhysAddr) -> VirtAddr { /* ... */ }
}

// Use the driver
let blk = VirtIOBlk::<MyHal>::new(transport)?;
blk.read_blocks(0, &mut buffer)?;
```

## References

- [Direct Memory I/O](/components/instar/prototypes/data-transfer-direct-memory/)
- [Virtio-vsock](/components/instar/prototypes/data-transfer-virtio-vsock/)
- [Virtio-block](/components/instar/prototypes/data-transfer-virtio-block/)
- [Other Mechanisms](/components/instar/prototypes/data-transfer-other/)
- [KVM API Guide](/components/instar/prototypes/kvm/)
- [rust-vmm Community](https://github.com/rust-vmm/community)
- [virtio-drivers (rcore-os)](https://github.com/rcore-os/virtio-drivers)
- [Firecracker](https://firecracker-microvm.github.io/)
