# Virtio-Block5 Prototype

Extends [Virtio-Block4](/components/instar/prototypes/virtio-block4/) with performance optimizations to
reduce VM exit overhead and improve I/O throughput.

## Motivation

The virtio-block4 prototype revealed that MMIO operations account for the
majority of VM exits (~46% reads, ~46% writes). Each VM exit involves a
context switch from guest to hypervisor, which is expensive. This prototype
implements three complementary optimizations:

1. **ioeventfd**: Allows queue notifications without full VM exits
2. **O_DIRECT**: Bypasses the kernel page cache for direct disk I/O
3. **mmap**: Maps the backing file directly into memory

## Key Features

1. **ioeventfd for Queue Notifications**: Uses KVM's ioeventfd mechanism to
   signal queue activity via eventfd instead of causing a VM exit
2. **O_DIRECT Backing**: Direct I/O bypasses the kernel page cache, useful for
   large sequential transfers
3. **mmap Backing**: Memory-mapped files eliminate read/write syscall overhead
4. **Configurable via CLI**: Choose backing mode at runtime
5. **All virtio-block4 features**: Statistics, configurable sectors, protobuf

## Usage

```bash
# Build
./build.sh

# Run with default (regular) backing
sudo ./target/release/vmm --input source.bin --output dest.bin guest.bin

# Run with O_DIRECT backing (bypasses page cache)
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --direct-io guest.bin

# Run with mmap backing (memory-mapped files)
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --mmap-backing guest.bin

# Disable ioeventfd (for comparison/debugging)
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --no-ioeventfd guest.bin

# Combine options
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --mmap-backing --input-sector-size 4096 --output-sector-size 4096 guest.bin
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--direct-io` | Use O_DIRECT for backing files (requires aligned I/O) |
| `--mmap-backing` | Use memory-mapped files for backing |
| `--no-ioeventfd` | Disable ioeventfd optimization |

Note: `--direct-io` and `--mmap-backing` are mutually exclusive.

## Architecture

```
┌───────────────────────────────────────────────────────────────────────────────┐
│                              VMM (Multi-threaded)                              │
│                                                                                │
│  Main Thread (vCPU)                        I/O Thread                          │
│  ─────────────────                         ─────────────                       │
│  ┌─────────────────┐                       ┌─────────────────────────────────┐ │
│  │   KVM VM        │                       │  epoll_wait() on eventfds       │ │
│  │   + vCPU        │   ───eventfd───────>  │                                 │ │
│  │   vcpu.run()    │                       │  On signal:                     │ │
│  │                 │                       │    1. Lock device               │ │
│  │   Handles:      │                       │    2. process_queue()           │ │
│  │   - IO exits    │                       │    3. Update used ring          │ │
│  │   - MMIO exits  │                       │    4. Set interrupt_status      │ │
│  │   - Other exits │                       │    5. Update shared stats       │ │
│  └────────┬────────┘                       └──────────────┬──────────────────┘ │
│           │                                               │                    │
│           │              Shared State (Arc<Mutex<>>)      │                    │
│           │         ┌─────────────────────────────────────┘                    │
│           │         │                                                          │
│  ┌────────┴─────────┴──────────────────────────────────────────────────────┐  │
│  │  Input Device (Arc<Mutex<>>)        Output Device (Arc<Mutex<>>)        │  │
│  │  ┌─────────────────────────┐        ┌─────────────────────────┐         │  │
│  │  │  VirtioBlockDevice      │        │  VirtioBlockDevice      │         │  │
│  │  │  + BackingStore         │        │  + BackingStore         │         │  │
│  │  └─────────────────────────┘        └─────────────────────────┘         │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                │
│  ┌─────────────────────────────────────────────────────────────────────────┐  │
│  │  VmmStats (Arc<Mutex<>>)  - Shared between main thread and I/O thread   │  │
│  └─────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Optimization Details

### ioeventfd with I/O Thread

ioeventfd allows KVM to handle certain MMIO writes without a VM exit. However,
our guest polls `used_idx` directly from guest memory (not via MMIO), so the
vCPU never exits to let the VMM process the eventfd. To solve this, we use a
**separate I/O thread** that processes queues asynchronously.

**Setup:**
1. Create an eventfd for each virtqueue
2. Register with KVM using `KVM_IOEVENTFD` ioctl
3. Start I/O thread that uses `epoll_wait()` on the eventfds

**Runtime flow:**
1. Guest writes to QUEUE_NOTIFY (0x50)
2. KVM signals the eventfd (no VM exit!)
3. I/O thread wakes from epoll
4. I/O thread locks device, processes queue, updates used ring
5. Guest polls `used_idx` and sees completion
6. vCPU continues running throughout

This achieves true parallelism: the vCPU continues executing guest code while
the I/O thread handles disk operations.

### O_DIRECT

O_DIRECT bypasses the kernel page cache:

**Advantages:**
- Avoids double-buffering (guest page cache + host page cache)
- Better for large sequential transfers
- More predictable latency

**Requirements:**
- Buffer must be aligned to filesystem block size (typically 4096 bytes)
- Offset must be aligned
- Length must be aligned

The `DirectBacking` struct handles alignment internally with `posix_memalign`.

### mmap

Memory-mapped files map the file directly into the process address space:

**Advantages:**
- Eliminates read/write syscall overhead
- Kernel handles page faults transparently
- Efficient for random access patterns

**Implementation:**
- Uses `memmap2` crate for safe mmap handling
- `MmapMut` provides mutable access
- `flush()` syncs changes to disk

## Why ioeventfd but not irqfd?

KVM provides two complementary eventfd-based mechanisms:

| Mechanism | Direction | Purpose |
|-----------|-----------|---------|
| **ioeventfd** | Guest → VMM | Queue notifications without VM exit |
| **irqfd** | VMM → Guest | Interrupt injection without ioctl |

We implemented ioeventfd but not irqfd. Here's why:

### What irqfd does

With irqfd:
1. VMM registers an eventfd with KVM for a specific interrupt line
2. When VMM wants to signal an interrupt, it writes to the eventfd
3. KVM automatically injects a hardware interrupt into the guest

This avoids the overhead of the VMM calling `KVM_INTERRUPT` or similar ioctls.

### Why we don't need it

Our guest uses a **polling model** rather than interrupt-driven I/O:

```
Current flow:
1. Guest submits request to virtqueue
2. Guest writes to QUEUE_NOTIFY
3. VMM processes the request
4. VMM sets interrupt_status |= 1
5. Guest polls INTERRUPT_STATUS register via MMIO read
6. Guest sees the bit is set and processes completion
```

The guest actively polls for completion rather than receiving hardware interrupt
notifications. This means:

- We never call `KVM_INTERRUPT` or similar mechanisms
- There's no interrupt injection that irqfd could optimize
- The "interrupt" is just a status bit the guest reads via MMIO

### When irqfd would help

irqfd would be beneficial if we:

1. Set up actual interrupt routing (`KVM_SET_GSI_ROUTING`)
2. Modified the guest to handle real hardware interrupts via IDT
3. Injected interrupts when I/O completes instead of setting a status bit

This would be more efficient when the guest has other work to do between I/O
operations, as it wouldn't need to poll. However, for our single-purpose file
copy guest, the polling model is simpler and sufficient.

### Design tradeoff

| Approach | Pros | Cons |
|----------|------|------|
| Polling (current) | Simple guest code, no IDT setup | CPU overhead from polling |
| irqfd + interrupts | Guest can do other work, lower latency | Complex guest setup, IDT required |

For a minimal VMM focused on demonstrating virtio concepts, the polling model
keeps the guest code straightforward while still achieving good performance.

## Implementation Details

### New Modules

| Module | Description |
|--------|-------------|
| `backing.rs` | BackingStore abstraction with Regular, Direct, and Mmap modes |
| `ioevent.rs` | IoEvent wrapper for KVM ioeventfd |
| `io_thread.rs` | I/O processing thread for async queue handling |

### BackingStore Enum

```rust
pub enum BackingStore {
    Regular(RegularBacking),  // Standard file I/O
    Direct(DirectBacking),    // O_DIRECT with aligned buffers
    Mmap(MmapBacking),        // Memory-mapped file
}
```

All variants implement:
- `read_at(offset, buf)` - Read at offset
- `write_at(offset, buf)` - Write at offset
- `sync()` - Flush to disk

### I/O Thread

The I/O thread handles queue processing asynchronously while the vCPU runs:

```rust
pub struct IoThread {
    handle: Option<JoinHandle<()>>,
    running: Arc<AtomicBool>,
}

// Shared state types
pub type SharedDevice = Arc<Mutex<VirtioBlockDevice>>;
pub type SharedStats = Arc<Mutex<VmmStats>>;
```

The thread uses `epoll_wait()` with a 100ms timeout to poll both input and
output eventfds simultaneously. When an eventfd is signaled, it locks the
device, calls `process_queue()`, and updates the shared statistics.

## Dependencies

| Crate | Version | Purpose |
|-------|---------|---------|
| `vmm-sys-util` | 0.12 | EventFd for ioeventfd |
| `memmap2` | 0.9 | Safe memory-mapped files |

## Comparison with Previous Prototypes

| Feature | virtio-block4 | virtio-block5 |
|---------|---------------|---------------|
| Threading | Single-threaded | Multi-threaded (I/O thread) |
| Queue notifications | VM exit | ioeventfd + I/O thread |
| File I/O | Regular | Regular/O_DIRECT/mmap |
| Exit reduction | None | QUEUE_NOTIFY bypassed |
| CLI options | Sector sizes | + backing mode |

## Expected Performance Impact

| Optimization | Impact | Best For |
|--------------|--------|----------|
| ioeventfd | Reduces QUEUE_NOTIFY exits | All workloads |
| O_DIRECT | Eliminates page cache overhead | Large sequential I/O |
| mmap | Eliminates syscall overhead | Random access patterns |

## Files

| Path | Description |
|------|-------------|
| `vmm/src/main.rs` | Multi-threaded VMM with CLI options and shared device state |
| `vmm/src/io_thread.rs` | I/O thread for async queue processing |
| `vmm/src/backing.rs` | BackingStore abstraction |
| `vmm/src/ioevent.rs` | IoEvent for ioeventfd |
| `vmm/src/virtio/block.rs` | Uses BackingStore instead of File |

## Future Work

- **Interrupt-driven I/O with irqfd**: Replace polling with real interrupts
  (requires guest IDT setup and `KVM_SET_GSI_ROUTING`)
- **io_uring**: Asynchronous I/O for better parallelism
- **Virtqueue batching**: Process multiple requests per exit
- **Request merging**: Combine adjacent I/O requests

## Related Documentation

- [Virtio-Block4 Prototype](/components/instar/prototypes/virtio-block4/) - Statistics tracking
- [Performance Counters](/components/instar/prototypes/performance-counters/)
- [Data Transfer](/components/instar/prototypes/data-transfer-virtio-block/)
