# Other Data Transfer Mechanisms for KVM Guests

This document covers additional data transfer mechanisms beyond direct memory,
virtio-vsock, and virtio-block.

## Port I/O (IN/OUT Instructions)

### Overview

Port I/O uses x86 IN/OUT instructions to communicate with the VMM. This is
the mechanism used for legacy PC hardware like serial ports.

### Exit Structure

```c
struct {
    __u8 direction;      // KVM_EXIT_IO_IN (0) or KVM_EXIT_IO_OUT (1)
    __u8 size;           // 1, 2, 4, or 8 bytes
    __u16 port;          // I/O port number
    __u32 count;         // Number of operations
    __u64 data_offset;   // Offset in kvm_run for data
} io;
```

### Common Ports

| Port | Device |
|------|--------|
| 0x3f8-0x3ff | COM1 serial |
| 0x2f8-0x2ff | COM2 serial |
| 0x80 | Debug port |
| 0x60-0x64 | Keyboard controller |

### Trade-offs

**Advantages:**
- Simple to implement
- Fast (faster than MMIO)
- No memory mapping required

**Limitations:**
- x86 only
- Small data size (1-8 bytes per operation)
- VM exit per operation (unless using ioeventfd)
- Limited port space (65536 ports)

### Use Case

Best for low-frequency control/status operations, not bulk data.

## Memory-Mapped I/O (MMIO)

### Overview

MMIO uses memory accesses to unmapped guest physical addresses to trigger
VM exits, similar to how hardware registers work.

### Exit Structure

```c
struct {
    __u64 phys_addr;    // Guest physical address
    __u8 data[8];       // Data (up to 8 bytes)
    __u32 len;          // Access length
    __u8 is_write;      // 1 = write, 0 = read
} mmio;
```

### Trade-offs

**Advantages:**
- Works on all architectures
- Natural for device register emulation
- Can use readonly memory regions for read traps

**Limitations:**
- Slower than port I/O on x86
- VM exit per access
- Limited to 8 bytes per operation

## ioeventfd / irqfd

### ioeventfd

Avoids VM exits by signaling an eventfd on specific I/O or MMIO access.

```c
struct kvm_ioeventfd {
    __u64 datamatch;    // Match value (optional)
    __u64 addr;         // Address to monitor
    __u32 len;          // Access size
    __s32 fd;           // eventfd to signal
    __u32 flags;        // KVM_IOEVENTFD_FLAG_*
};
```

**Flags:**
- `KVM_IOEVENTFD_FLAG_DATAMATCH`: Only trigger on matching value
- `KVM_IOEVENTFD_FLAG_PIO`: Port I/O (vs MMIO)
- `KVM_IOEVENTFD_FLAG_DEASSIGN`: Remove registration

**Use Case:** Notification-only communication (doorbell pattern).

### irqfd

Inject interrupts into guest via eventfd.

```c
struct kvm_irqfd {
    __u32 fd;           // eventfd
    __u32 gsi;          // Guest interrupt line
    __u32 flags;
    __u32 resamplefd;   // For level-triggered emulation
};
```

**Use Case:** Host-to-guest notifications without VM entry overhead.

### Combined Pattern

```
Guest writes to doorbell   →  ioeventfd signals host
Host completes work        →  irqfd injects interrupt
Guest handles interrupt    →  reads result from shared memory
```

This pattern achieves near-zero-exit data transfer.

## Custom MMIO Device

### Overview

A custom MMIO-based device provides a middle ground between direct memory I/O
and full virtio implementations. It combines memory-mapped control registers
with DMA-like data transfer and ioeventfd/irqfd for efficient signaling.

This approach emulates a simplified hardware device without implementing a
real hardware protocol or the full virtio specification.

### Architecture

```
Guest Physical Address Space:

┌─────────────────────────────────────────────┐
│ Control Registers (4KB, MMIO)               │  0xFE000000
│   0x00: command (u32)                       │
│   0x04: status (u32)                        │
│   0x08: data_gpa (u64)     ← guest phys addr│
│   0x10: data_length (u64)                   │
│   0x18: result_gpa (u64)                    │
│   0x20: result_length (u64)                 │
│   0x28: doorbell (u32)     ← triggers work  │
├─────────────────────────────────────────────┤
│ Data Buffer (variable, shared memory)       │  0x10000000
│   Guest writes input here, host reads       │
├─────────────────────────────────────────────┤
│ Result Buffer (variable, shared memory)     │  0x20000000
│   Host writes output here, guest reads      │
└─────────────────────────────────────────────┘
```

### Protocol

**Commands:**

| Value | Command | Description |
|-------|---------|-------------|
| 0x01 | READ_CHUNK | Read chunk from input at offset |
| 0x02 | WRITE_CHUNK | Write chunk to output at offset |
| 0x03 | GET_SIZE | Query input file size |
| 0x04 | FLUSH | Flush output to storage |

**Status:**

| Value | Status | Description |
|-------|--------|-------------|
| 0x00 | IDLE | Ready for command |
| 0x01 | BUSY | Processing command |
| 0x02 | COMPLETE | Command finished successfully |
| 0x03 | ERROR | Command failed |

### Operation Flow

```
1. Guest writes command parameters to registers
2. Guest writes to doorbell → triggers ioeventfd (no VM exit)
3. Host receives eventfd notification
4. Host reads command from guest memory (or MMIO ring)
5. Host performs operation (read/write file)
6. Host writes result to result buffer
7. Host updates status register
8. Host injects interrupt via irqfd (optional)
9. Guest reads status and result
```

### VMM Implementation

```c
// Register ioeventfd for doorbell
struct kvm_ioeventfd doorbell = {
    .addr = 0xFE000028,      // Doorbell address
    .len = 4,
    .fd = eventfd(0, 0),
    .flags = 0,              // MMIO (not PIO)
};
ioctl(vm_fd, KVM_IOEVENTFD, &doorbell);

// Register irqfd for completion notification
struct kvm_irqfd completion_irq = {
    .fd = eventfd(0, 0),
    .gsi = 32,               // IRQ line
};
ioctl(vm_fd, KVM_IRQFD, &completion_irq);

// Main loop
while (1) {
    // Wait for doorbell
    uint64_t val;
    read(doorbell.fd, &val, sizeof(val));

    // Read command from guest control registers
    uint32_t cmd = read_guest_mmio(0xFE000000);
    uint64_t offset = read_guest_mmio(0xFE000008);
    uint64_t length = read_guest_mmio(0xFE000010);

    // Process command
    switch (cmd) {
    case CMD_READ_CHUNK:
        pread(input_fd, guest_data_buffer, length, offset);
        break;
    case CMD_WRITE_CHUNK:
        pwrite(output_fd, guest_result_buffer, length, offset);
        break;
    }

    // Signal completion
    write_guest_mmio(0xFE000004, STATUS_COMPLETE);
    uint64_t one = 1;
    write(completion_irq.fd, &one, sizeof(one));
}
```

### Guest Implementation

```rust
const CTRL_BASE: u64 = 0xFE00_0000;
const CMD_READ_CHUNK: u32 = 0x01;

struct CustomDevice {
    ctrl: *mut u8,
    data_buffer: *mut u8,
}

impl CustomDevice {
    fn read_chunk(&mut self, offset: u64, length: u64) -> &[u8] {
        unsafe {
            // Write command parameters
            (self.ctrl.add(0x00) as *mut u32).write_volatile(CMD_READ_CHUNK);
            (self.ctrl.add(0x08) as *mut u64).write_volatile(offset);
            (self.ctrl.add(0x10) as *mut u64).write_volatile(length);

            // Ring doorbell
            (self.ctrl.add(0x28) as *mut u32).write_volatile(1);

            // Poll for completion (or wait for interrupt)
            while (self.ctrl.add(0x04) as *mut u32).read_volatile() != STATUS_COMPLETE {
                core::hint::spin_loop();
            }

            // Return data slice
            slice::from_raw_parts(self.data_buffer, length as usize)
        }
    }
}
```

### Large File Support

For large files (10s-100s of GB), the custom device uses chunked access:

```
For a 100GB file with 1MB chunks:
  - 100,000 READ_CHUNK commands
  - Each command: ~1μs doorbell + ~10ms I/O = ~10ms per chunk
  - Total: ~17 minutes (similar to virtio-block)
```

The 64-bit offset field in the command structure supports files up to 16 EB.

### Comparison with Virtio-block

**Without rust-vmm crates:**

| Aspect | Custom MMIO | Virtio-block |
|--------|-------------|--------------|
| Protocol complexity | Low (~600 lines) | High (~1600 lines) |
| Complexity advantage | ✓ ~60% less code | |

**With rust-vmm crates:**

| Aspect | Custom MMIO | Virtio-block |
|--------|-------------|--------------|
| Protocol complexity | ~550 lines | ~450 lines |
| Complexity advantage | | ✓ ~20% less code |
| Standardization | None | VIRTIO spec |
| Tooling | None | qemu, libvirt |
| Batching | Manual | Native (virtqueue) |
| Scatter-gather | Manual | Native |
| Error handling | Custom | Standardized |
| Guest driver crate | ✗ | ✓ (virtio-drivers) |

> **Key insight**: The rust-vmm ecosystem has eliminated Custom MMIO's main
> advantage (implementation simplicity). Virtio-block is now easier to
> implement AND provides better features.

### Trade-offs

**Advantages:**
- Zero VM exits with ioeventfd/irqfd
- Flexible protocol design
- Easy to debug (simple state machine)
- Natural fit for request/response patterns
- Good for learning how device emulation works

**Limitations:**
- Non-standard (no ecosystem support)
- **No longer simpler than virtio-block** (with rust-vmm crates)
- Must implement batching manually for throughput
- Requires careful synchronization design
- No existing implementations to reference
- No guest driver crate available

### When to Use

**Good fit:**
- Learning/educational purposes
- Protocols that don't fit virtio semantics
- When zero VM exits is absolutely critical
- Very simple, single-purpose devices

**Consider virtio-block instead when:**
- Using Rust (rust-vmm crates available)
- Production deployment planned
- Need scatter-gather for performance
- Want standardized error handling
- Integration with existing tooling required

> **Recommendation changed**: With rust-vmm crates, virtio-block is now the
> simpler choice for most use cases. Custom MMIO is primarily useful for
> learning or specialized protocols.

### Implementation Complexity

**From scratch:**

| Component | Lines of Code |
|-----------|---------------|
| Guest driver | ~100-200 |
| VMM device | ~200-400 |
| Protocol handling | ~100-200 |
| **Total** | **~400-800** |

**With kvm-ioctls (for ioeventfd):**

| Component | Lines of Code |
|-----------|---------------|
| Guest driver | ~100-200 |
| VMM device | ~150-300 |
| Protocol handling | ~100-200 |
| **Total** | **~350-700** |

**Compared to Virtio-block with rust-vmm crates: ~450 lines**

The complexity advantage of Custom MMIO has largely disappeared. Use
virtio-block unless you have a specific reason not to.

## Virtio-console

### Overview

Stream-based communication device, more sophisticated than serial port I/O.

### Features

- Multiple ports (up to 32768)
- Bidirectional streams
- Integration with host console/tty
- Supports both console and generic port modes

### Device ID

`VIRTIO_ID_CONSOLE` (3)

### Trade-offs

**Advantages:**
- Higher throughput than serial
- Multiple channels
- Standard virtio transport

**Limitations:**
- Stream semantics (no message boundaries)
- Still requires virtio implementation

### Use Case

Text/log output, interactive console, moderate data transfer.

## Virtio-fs (virtiofs)

### Overview

Shared filesystem using FUSE protocol over virtio transport.

### Architecture

```
Guest                          Host
  |                              |
  +-- virtiofs driver            |
  |       |                      |
  |       +-- virtio-fs device --|-- virtiofsd daemon
  |                              |       |
  +-- /mnt/shared <--------------|-- /host/shared
```

### Features

- DAX (Direct Access) for zero-copy file access
- Metadata caching
- Multiple request queues

### Use Case

When file semantics are natural (config files, large data files).

## Vhost Mechanism

### Overview

Kernel-accelerated virtio backend that bypasses qemu for data path.

### Components

- **vhost-net**: Network acceleration
- **vhost-scsi**: SCSI acceleration
- **vhost-vsock**: Vsock acceleration

### Benefits

- Kernel handles data path
- Reduces context switches
- Near-native performance

### Limitations

- Requires kernel support
- More complex setup

## PCI Passthrough (VFIO)

### Overview

Assign physical PCI devices directly to guests for bare-metal performance.

### Requirements

- IOMMU (Intel VT-d, AMD-Vi)
- Compatible device
- VFIO driver

### Trade-offs

**Advantages:**
- Native hardware performance
- Full device capabilities

**Limitations:**
- Device dedicated to single VM
- Hardware dependency
- Complex setup
- No live migration

### Use Case

When specific hardware acceleration is required (GPU, NIC, NVMe).

## Shared Memory (ivshmem)

### Overview

Inter-VM shared memory device for zero-copy communication.

### Features

- Direct memory sharing between VMs
- Optional interrupt mechanism
- Flexible size

### Use Case

High-performance inter-VM communication.

## Hypercalls

### Overview

Direct guest-to-hypervisor calls for special operations.

### Common Hypercalls

| Number | Name | Purpose |
|--------|------|---------|
| 1 | KVM_HC_VAPIC_POLL_IRQ | APIC interrupt polling |
| 5 | KVM_HC_KICK_CPU | Wake target CPU |
| 9 | KVM_HC_CLOCK_PAIRING | TSC synchronization |
| 12 | KVM_HC_MAP_GPA_RANGE | Request page mapping |

### Trade-offs

**Advantages:**
- Very low latency
- Direct communication

**Limitations:**
- Architecture-specific
- Register-sized data only
- Custom protocol required

## Comparison Summary

| Mechanism | Throughput | Latency | Complexity | Architecture |
|-----------|-----------|---------|------------|--------------|
| Port I/O | Low | Medium | Low | x86 only |
| MMIO | Low | Medium | Low | All |
| ioeventfd/irqfd | High | Low | Medium | All |
| Custom MMIO Device | High | Low | Low-Medium | All |
| Virtio-console | Medium | Medium | Medium | All |
| Virtio-fs | High | Medium | High | All |
| Vhost | Very High | Low | High | All |
| VFIO | Native | Native | Very High | Requires HW |
| Hypercalls | Low | Very Low | Low | Varies |

## Recommendations

| Use Case | Recommended Mechanism |
|----------|----------------------|
| Simple signaling | ioeventfd + irqfd |
| Debug output | Port I/O (serial) |
| Bulk data, one-shot | Direct memory |
| Streaming data | Virtio-vsock |
| Block semantics | Virtio-block |
| Prototyping block I/O | Custom MMIO device |
| File sharing | Virtio-fs |
| Maximum performance | Vhost or VFIO |
| Inter-VM | ivshmem |

## References

- [Linux KVM API](https://www.kernel.org/doc/html/latest/virt/kvm/api.html)
- [VIRTIO Specification](https://docs.oasis-open.org/virtio/virtio/v1.1/virtio-v1.1.html)
- [VFIO Documentation](https://www.kernel.org/doc/html/latest/driver-api/vfio.html)
