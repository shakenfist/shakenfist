# Direct Memory I/O for KVM Guests

This document describes how to implement direct memory-based data transfer
for bare-metal KVM guests, where input data is mapped into guest memory
before execution and output is read from a different region after VM exit.

## Size Limitations

> **Warning**: Direct memory I/O is NOT suitable for large files.
>
> For disk images in the 10-100+ GB range, use [virtio-block](/components/instar/prototypes/data-transfer-virtio-block/)
> instead. Direct memory requires mapping the entire input/output into the
> guest's physical address space, which is impractical for files larger than
> ~1-2 GB.

**Practical size limits:**

| Constraint | Limit | Impact |
|------------|-------|--------|
| Guest physical address space | ~1 TB (40-bit) | Hard limit on total mapped memory |
| Host memory allocation | System RAM | Cannot map more than available |
| Practical single buffer | ~1-2 GB | Beyond this, use virtio-block |

For large file processing, direct memory would require implementing a
complex chunking protocol - at which point virtio-block is simpler and
provides random access natively.

## Overview

Direct memory I/O is the simplest approach for **small** data transfers. The
VMM allocates memory regions, maps them into the guest's physical address
space, and the guest reads/writes directly to these regions. No virtio
protocol or complex device emulation is required.

**Best for:** Metadata, configuration, small files (< 1 GB)

## Memory Region Setup

KVM uses memory slots (memslots) to map guest physical addresses to host
virtual addresses. The key ioctl is `KVM_SET_USER_MEMORY_REGION`:

```c
struct kvm_userspace_memory_region {
    __u32 slot;              // Slot ID (0-509, higher bits for address space)
    __u32 flags;             // KVM_MEM_LOG_DIRTY_PAGES, KVM_MEM_READONLY
    __u64 guest_phys_addr;   // Guest physical address
    __u64 memory_size;       // Size in bytes (page-aligned)
    __u64 userspace_addr;    // Host virtual address backing this memory
};
```

### Key Constraints

- Maximum memslots: ~512 (query via `KVM_CAP_NR_MEMSLOTS`)
- Slots must not overlap in guest physical address space
- Memory size must be page-aligned
- For large page support, align lower 21 bits of guest and host addresses

### Memory Region Flags

- `KVM_MEM_LOG_DIRTY_PAGES` - Track modified pages (for migration)
- `KVM_MEM_READONLY` - Writes trigger `KVM_EXIT_MMIO` instead of succeeding

## Recommended Memory Layout

```
Guest Physical Address Space:

0x00000000 - 0x000FFFFF    Reserved (real mode structures if needed)
0x00100000 - 0x0FFFFFFF    Guest RAM (code, stack, heap)
0x10000000 - 0x100FFFFF    Input buffer (1MB example)
0x10100000 - 0x101FFFFF    Output buffer (1MB example)
0xF0000000 - 0xF0000FFF    Coalesced MMIO ring (if using)
```

### Buffer Placement Best Practices

1. Place I/O buffers outside guest code/stack to avoid memslot overlap
2. Use read-only memslots for input data to catch accidental writes
3. Align buffers to 2MB boundaries for large page efficiency
4. Consider placing output buffer at higher addresses for clear separation

## Guest Completion Signaling

The guest must signal when it has finished processing. Options:

### HLT Instruction

The simplest approach - guest executes `HLT`, causing `KVM_EXIT_HLT`:

```rust
// Guest code
unsafe { asm!("hlt"); }
```

```rust
// VMM handling
match vcpu.run()? {
    VcpuExit::Hlt => {
        // Read output from guest memory
        let output = read_guest_memory(output_buffer_addr);
    }
}
```

### Port I/O Signal

Guest writes to a specific port to signal completion with status:

```rust
// Guest code - signal success
unsafe {
    asm!("out dx, al", in("dx") 0x3f8u16, in("al") 0u8);
}
```

```rust
// VMM handling
VcpuExit::IoOut(port, data) if port == 0x3f8 => {
    let status = data[0];
    // Read output based on status
}
```

### Memory-Mapped Status

Guest writes status to a known memory location:

```rust
// Guest code
unsafe {
    let status_ptr = 0x10200000 as *mut u32;
    *status_ptr = PROCESSING_COMPLETE;
}
```

VMM can poll this location or use `KVM_EXIT_MMIO` with readonly region.

## Coalesced MMIO/PIO

For higher-performance scenarios, KVM supports coalesced I/O where multiple
writes are batched into a ring buffer without causing VM exits.

### Ring Buffer Structure

```c
struct kvm_coalesced_mmio {
    __u64 phys_addr;      // Guest physical address of write
    __u32 len;            // Length (1, 2, 4, or 8 bytes)
    union {
        __u32 pad;
        __u32 pio;        // 1 if port I/O, 0 if MMIO
    };
    __u8 data[8];         // Data written
};

struct kvm_coalesced_mmio_ring {
    __u32 first, last;    // Ring indices
    struct kvm_coalesced_mmio coalesced_mmio[];
};
```

### Registration

```c
struct kvm_coalesced_mmio_zone zone = {
    .addr = 0xF0000000,
    .size = 0x1000,
};
ioctl(vm_fd, KVM_REGISTER_COALESCED_MMIO, &zone);
```

### Accessing the Ring

The ring is accessible via mmap at `KVM_COALESCED_MMIO_PAGE_OFFSET`:

```c
void *ring_page = mmap(NULL, PAGE_SIZE, PROT_READ | PROT_WRITE,
                       MAP_SHARED, vm_fd,
                       KVM_COALESCED_MMIO_PAGE_OFFSET * PAGE_SIZE);
struct kvm_coalesced_mmio_ring *ring = ring_page;
```

### Processing Pattern

```c
while (ring->first != ring->last) {
    struct kvm_coalesced_mmio *entry = &ring->coalesced_mmio[ring->first];
    process_write(entry->phys_addr, entry->data, entry->len);
    ring->first = (ring->first + 1) % KVM_COALESCED_MMIO_MAX;
}
```

## Synchronization

### Memory Barriers

When sharing memory between host and guest:

```c
// Before updating shared index
smp_wmb();  // Write memory barrier

// After reading shared data
smp_rmb();  // Read memory barrier
```

### Cache Coherency

- x86 maintains cache coherency automatically for normal memory
- Use volatile access or compiler barriers to prevent optimization issues
- For non-coherent DMA, explicit cache management may be needed

## Implementation Example (Rust)

### VMM Setup

```rust
// Allocate input and output buffers
let input_buffer = allocate_aligned(INPUT_SIZE, PAGE_SIZE);
let output_buffer = allocate_aligned(OUTPUT_SIZE, PAGE_SIZE);

// Copy input data
input_buffer.copy_from_slice(&input_data);

// Map input buffer (read-only for guest)
let input_region = kvm_userspace_memory_region {
    slot: 1,
    flags: KVM_MEM_READONLY,
    guest_phys_addr: INPUT_GPA,
    memory_size: INPUT_SIZE as u64,
    userspace_addr: input_buffer.as_ptr() as u64,
};
vm.set_user_memory_region(input_region)?;

// Map output buffer (read-write for guest)
let output_region = kvm_userspace_memory_region {
    slot: 2,
    flags: 0,
    guest_phys_addr: OUTPUT_GPA,
    memory_size: OUTPUT_SIZE as u64,
    userspace_addr: output_buffer.as_ptr() as u64,
};
vm.set_user_memory_region(output_region)?;
```

### Guest Access

```rust
// Guest code - process data
let input = unsafe {
    slice::from_raw_parts(INPUT_GPA as *const u8, INPUT_SIZE)
};
let output = unsafe {
    slice::from_raw_parts_mut(OUTPUT_GPA as *mut u8, OUTPUT_SIZE)
};

// Process input, write to output
process(input, output);

// Signal completion
unsafe { asm!("hlt"); }
```

### VMM Completion Handling

```rust
loop {
    match vcpu.run()? {
        VcpuExit::Hlt => {
            // Copy output back
            let result = output_buffer[..output_len].to_vec();
            return Ok(result);
        }
        VcpuExit::Shutdown => {
            return Err("Guest crashed");
        }
        _ => continue,
    }
}
```

## Advantages

- **Simplicity**: No protocol overhead, just memory access
- **Performance**: Zero-copy possible, no virtqueue processing
- **Latency**: Minimal exit overhead (just HLT or I/O)
- **Debugging**: Easy to inspect memory contents

## Limitations

- **Size Constraints**: Cannot handle files larger than available memory (~1-2 GB practical limit)
- **Fixed Layout**: Input/output sizes determined at setup
- **No Streaming**: Cannot process data larger than allocated buffers
- **Synchronization**: Guest must complete before VMM reads output
- **No Flow Control**: Guest cannot signal partial progress easily
- **No Random Access for Large Files**: Would require complex chunking protocol

## When to Use

Direct memory I/O is ideal for:

- **Small data only** (< 1 GB)
- Single-shot processing tasks
- Metadata or configuration processing
- Low-latency requirements
- Simple guest implementations
- Initial prototyping before implementing virtio

**Do NOT use for:**

- Large disk images (10s-100s of GB) - use [virtio-block](/components/instar/prototypes/data-transfer-virtio-block/)
- Streaming data - use [virtio-vsock](/components/instar/prototypes/data-transfer-virtio-vsock/)
- Any file larger than available system memory

## References

- [Linux KVM API](https://www.kernel.org/doc/html/latest/virt/kvm/api.html)
- [KVM Memory Slots](https://www.kernel.org/doc/html/latest/virt/kvm/api.html#kvm-set-user-memory-region)
- [Coalesced MMIO](https://www.kernel.org/doc/html/latest/virt/kvm/api.html#kvm-register-coalesced-mmio)
