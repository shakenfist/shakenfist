# KVM Hello World 2 - Using rust-vmm Crates

A second iteration of the KVM hello world prototype that uses the `vm-memory`
crate from the rust-vmm project for simplified and safer guest memory
management.

## Motivation

The original helloworld prototype demonstrated that KVM-based bare-metal
execution works. This prototype demonstrates how rust-vmm crates can simplify
VMM development:

- **Safer memory operations**: No manual pointer arithmetic
- **Automatic cleanup**: Memory freed when dropped
- **Type safety**: `GuestAddress` prevents address confusion
- **Bounds checking**: `write_obj` validates writes stay in bounds

These improvements become critical as complexity increases with virtio devices.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ VMM (Host Process)                                      │
│  - Uses vm-memory for guest memory management           │
│  - GuestMemoryMmap for automatic mmap allocation        │
│  - GuestAddress for type-safe address handling          │
│  - write_obj/write_slice for safe memory writes         │
└─────────────────────────────────────────────────────────┘
                          │
                          │ KVM ioctls
                          ▼
┌─────────────────────────────────────────────────────────┐
│ Guest (Bare-Metal Binary)                               │
│  - Unchanged from helloworld                            │
│  - Same no_std bare-metal code                          │
│  - Guest is unaware of VMM implementation details       │
└─────────────────────────────────────────────────────────┘
```

## Key Improvements

### Guest Memory Allocation

**Before (helloworld)**:
```rust
// Manual allocation requiring unsafe
let layout = std::alloc::Layout::from_size_align(size, 4096)?;
let ptr = unsafe { std::alloc::alloc_zeroed(layout) };
if ptr.is_null() {
    return Err("Failed to allocate guest memory".into());
}
// ... must manually track for cleanup
```

**After (helloworld2)**:
```rust
// Automatic allocation with vm-memory
let regions = vec![(GuestAddress(0), size as usize)];
let guest_mem = GuestMemoryMmap::<()>::from_ranges(&regions)?;
// Cleanup is automatic when guest_mem is dropped
```

### Memory Writes

**Before (helloworld)**:
```rust
fn setup_gdt(guest_mem: *mut u8) {
    let gdt = unsafe { guest_mem.add(GDT_BASE as usize) as *mut u64 };
    unsafe {
        ptr::write(gdt.add(0), 0u64);
        ptr::write(gdt.add(1), 0x00AF_9A00_0000_FFFFu64);
        ptr::write(gdt.add(2), 0x00CF_9200_0000_FFFFu64);
    }
}
```

**After (helloworld2)**:
```rust
fn setup_gdt(guest_mem: &GuestMemoryMmap) -> Result<(), ...> {
    guest_mem.write_obj(0u64, GuestAddress(GDT_BASE))?;
    guest_mem.write_obj(0x00AF_9A00_0000_FFFFu64, GuestAddress(GDT_BASE + 8))?;
    guest_mem.write_obj(0x00CF_9200_0000_FFFFu64, GuestAddress(GDT_BASE + 16))?;
    Ok(())
}
```

### Loading Guest Code

**Before (helloworld)**:
```rust
fn load_guest_code(guest_mem: *mut u8, code: &[u8]) {
    unsafe {
        let dest = guest_mem.add(GUEST_CODE_BASE as usize);
        ptr::copy_nonoverlapping(code.as_ptr(), dest, code.len());
    }
}
```

**After (helloworld2)**:
```rust
// One-liner with bounds checking
guest_mem.write_slice(&guest_code, GuestAddress(GUEST_CODE_BASE))?;
```

## Dependencies

| Crate | Version | Purpose |
|-------|---------|---------|
| `kvm-ioctls` | 0.19 | Safe KVM API wrappers |
| `kvm-bindings` | 0.10 | KVM FFI bindings |
| `vm-memory` | 0.15 | Guest memory abstraction |

## Code Comparison

| Metric | helloworld | helloworld2 | Change |
|--------|------------|-------------|--------|
| VMM lines | 325 | ~230 | -29% |
| Unsafe blocks | 8 | 1 | -87% |
| Manual ptr ops | Yes | No | Eliminated |
| Auto cleanup | No | Yes | Added |
| Error handling | Panics | Results | Improved |

## Memory Layout

Unchanged from helloworld:

| Address Range     | Purpose              |
|-------------------|----------------------|
| 0x1000-0x1FFF     | GDT (4KB)            |
| 0x2000-0x5FFF     | Page tables (16KB)   |
| 0x10000-0x1FFFF   | Guest code (64KB)    |
| 0x20000-0x2FFFF   | Stack (64KB)         |

## Building

### Prerequisites

Same as helloworld:

- Linux with KVM support (`/dev/kvm`)
- Rust nightly toolchain
- `rust-src` component
- `cargo-binutils` (provides `rust-objcopy`)

### Using DevContainer (Recommended)

The prototype includes a devcontainer with all dependencies:

```bash
cd prototypes/helloworld2

# Using devcontainer CLI
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . ./build.sh

# Or using Docker directly
docker build -t helloworld2-dev .devcontainer/
docker run --rm -it --device=/dev/kvm \
    -v "$(pwd)":/workspace -w /workspace \
    helloworld2-dev ./build.sh
```

### Manual Build

```bash
cd prototypes/helloworld2

# Install Rust nightly with required components
rustup install nightly
rustup component add rust-src --toolchain nightly
rustup component add llvm-tools-preview
cargo install cargo-binutils

# Build
./build.sh
```

## Running

```bash
sudo ./target/release/vmm guest.bin
```

### Expected Output

```
Loaded guest binary: 99 bytes from guest.bin
KVM API version: 12
Created VM
Allocated 2097152 bytes of guest memory via vm-memory
Configured memory region
Set up GDT at 0x1000
Set up page tables at 0x2000
Loaded guest code at 0x10000
Created vCPU
Configured special registers for long mode
Configured general registers (RIP=0x10000, RSP=0x2fff8)

--- Starting guest execution ---

Hello from KVM guest!

--- Guest executed HLT ---
Guest completed successfully!
```

## Why This Matters

The vm-memory crate is just the beginning. rust-vmm provides:

| Crate | Estimated Code Reduction |
|-------|-------------------------|
| vm-memory | ~30% for memory management |
| virtio-queue | ~50% for virtqueue handling |
| virtio-blk | ~70% for block device |
| Overall | ~72% for full virtio-block |

As noted in the [comparison document](/components/instar/prototypes/data-transfer-comparison/), using
rust-vmm crates reduces a complete virtio-block implementation from ~1600 lines
to ~450 lines.

## Next Steps

Future prototypes will build on this foundation:

1. **helloworld3**: Add virtio-queue for virtqueue implementation
2. **virtio-blk-prototype**: Full block device with rust-vmm crates
3. **image-conversion**: Actual disk image transcoding

## References

- [rust-vmm project](https://github.com/rust-vmm)
- [vm-memory crate](https://crates.io/crates/vm-memory)
- [kvm-ioctls crate](https://crates.io/crates/kvm-ioctls)
- [Original helloworld prototype](/components/instar/prototypes/kvm-hello-world/)
