# Why Rust for Instar

This document explains why Rust is the chosen implementation language for
Instar's secure disk image processing.

## Executive Summary

Rust is uniquely suited for Instar because it:

1. **Provides memory safety** - Critical when parsing untrusted disk images
2. **Supports bare-metal guests** - Can run without an OS in KVM VMs
3. **Has a mature VMM ecosystem** - rust-vmm crates reduce implementation by 70%+
4. **Delivers native performance** - No garbage collection pauses during I/O

No other language offers this combination.

## Security: Memory Safety Without Runtime

Instar processes untrusted disk images from potentially malicious sources.
Memory corruption vulnerabilities in image parsers have been a recurring
security issue:

| CVE | Impact | Root Cause |
|-----|--------|------------|
| CVE-2021-20255 | DoS/RCE in qemu | Stack overflow in VNC |
| CVE-2020-14364 | RCE in qemu | Out-of-bounds read/write in USB |
| CVE-2019-14378 | RCE in qemu | Heap buffer overflow in SLiRP |
| CVE-2017-2615 | RCE in qemu | Out-of-bounds access in Cirrus VGA |

Rust eliminates entire classes of these vulnerabilities:

| Vulnerability Class | C/C++ | Rust |
|---------------------|-------|------|
| Buffer overflows | Common | Compile-time prevented |
| Use-after-free | Common | Compile-time prevented |
| Double-free | Common | Compile-time prevented |
| Null pointer dereference | Common | Option type prevents |
| Data races | Common | Compile-time prevented |
| Integer overflow | Silent | Checked (debug) / defined (release) |

### Rust's Safety Model

```rust
// Rust prevents buffer overflows at compile time
fn process_sector(data: &[u8], index: usize) -> Option<u8> {
    data.get(index).copied()  // Returns None if out of bounds
    // data[index] would panic on out-of-bounds, not corrupt memory
}

// Rust prevents use-after-free at compile time
fn example() {
    let data = vec![1, 2, 3];
    let reference = &data[0];
    // drop(data);  // Compile error: cannot move while borrowed
    println!("{}", reference);
}
```

## Bare-Metal Guest Support

Instar runs image processing code inside isolated KVM virtual machines with
no operating system - just raw code on virtual hardware. This requires a
language that can:

1. Compile to bare-metal (`no_std`)
2. Control memory layout precisely
3. Interface with hardware directly
4. Have no runtime dependencies

### Language Comparison for Bare-Metal

| Language | `no_std` Support | Notes |
|----------|------------------|-------|
| **Rust** | Native | First-class `#![no_std]`, `core` library |
| C | Native | Requires manual memory safety discipline |
| C++ | With effort | Exceptions, RTTI problematic |
| **Go** | Not possible | Runtime required (GC, scheduler, goroutines) |
| Python | Not possible | Interpreter required |
| Java | Not possible | JVM required |
| Zig | Native | Less mature ecosystem |

### Go's Unsuitability

Go cannot run in a bare-metal KVM guest because it requires:

- **Garbage collector** - Needs OS threads and memory management
- **Goroutine scheduler** - Needs OS primitives (futex, signals)
- **Runtime initialization** - Assumes OS environment
- **System calls** - File I/O, networking, etc.

While Go could be used for the VMM (host-side), it cannot be used for the
guest code that actually processes disk images.

### Rust's Bare-Metal Example

```rust
#![no_std]
#![no_main]

use core::panic::PanicInfo;

#[no_mangle]
pub extern "C" fn _start() -> ! {
    // Direct hardware access - no OS needed
    let serial_port = 0x3f8 as *mut u8;
    unsafe {
        serial_port.write_volatile(b'H');
        serial_port.write_volatile(b'i');
    }

    // Signal completion
    unsafe { core::arch::asm!("hlt"); }
    loop {}
}

#[panic_handler]
fn panic(_info: &PanicInfo) -> ! {
    loop {}
}
```

## The rust-vmm Ecosystem

The [rust-vmm](https://github.com/rust-vmm/community) project provides
production-tested virtualization components used by major cloud providers:

| VMM | Company | Production Use |
|-----|---------|----------------|
| Firecracker | AWS | Lambda, Fargate |
| crosvm | Google | ChromeOS |
| Cloud Hypervisor | Intel/others | Cloud workloads |

### Available Crates

**VMM-side (host):**

| Crate | Purpose | Maturity |
|-------|---------|----------|
| kvm-ioctls | Safe KVM API | Production |
| vm-memory | Guest memory abstraction | Production |
| virtio-queue | Virtqueue implementation | Production |
| virtio-blk | Block device parsing | Production |
| virtio-vsock | Socket communication | Production |

**Guest-side:**

| Crate | Purpose | Maturity |
|-------|---------|----------|
| virtio-drivers | `no_std` virtio drivers | Production |

### Impact on Implementation

Without rust-vmm:
- Virtio-block: ~1600 lines of code
- Development time: weeks

With rust-vmm:
- Virtio-block: ~450 lines of code
- Development time: days

This 72% reduction in code also means 72% less attack surface.

## Performance

Rust compiles to native code with performance equivalent to C/C++:

| Aspect | Rust | Go | Python |
|--------|------|-----|--------|
| Compilation | Native | Native | Interpreted |
| Garbage collection | None | Stop-the-world | Reference counting + GC |
| I/O overhead | Zero | Channel overhead | Significant |
| Memory footprint | Minimal | Runtime + GC heap | Large |

For disk image processing at 100+ GB:

- **Rust**: Consistent throughput, predictable latency
- **Go**: GC pauses can cause I/O stalls
- **Python**: 10-100x slower, inappropriate for this scale

### Zero-Cost Abstractions

Rust's abstractions compile away:

```rust
// This high-level code...
let sum: u64 = sectors.iter()
    .filter(|s| s.is_allocated())
    .map(|s| s.size())
    .sum();

// ...compiles to the same assembly as hand-written C
```

## Alternatives Considered

### C

**Pros:**
- Mature, understood
- Bare-metal capable
- Maximum control

**Cons:**
- Memory safety is manual discipline
- No ecosystem for VMM components
- Higher bug rate in practice

**Verdict:** Rust provides C's capabilities with memory safety.

### C++

**Pros:**
- RAII for resource management
- Abstractions without overhead

**Cons:**
- Complex, error-prone
- Bare-metal support varies
- Memory safety still manual

**Verdict:** Rust's ownership model is simpler and safer than RAII.

### Go

**Pros:**
- Memory safe
- Good standard library
- Fast compilation

**Cons:**
- Cannot run bare-metal (runtime required)
- GC pauses during I/O
- No rust-vmm equivalent

**Verdict:** Unsuitable for guest code; could be used for VMM only.

### Zig

**Pros:**
- Memory safe (optional)
- Bare-metal capable
- C interop

**Cons:**
- Less mature (pre-1.0)
- Smaller ecosystem
- No VMM crate ecosystem

**Verdict:** Promising but ecosystem not ready.

## Conclusion

Rust is the optimal choice for Instar because:

1. **Security**: Memory safety prevents the vulnerability classes that plague
   image parsers, without runtime overhead

2. **Bare-metal**: First-class `no_std` support enables isolated guest
   execution without an OS

3. **Ecosystem**: rust-vmm crates provide production-tested VMM components,
   reducing implementation effort by 70%+

4. **Performance**: Native compilation with no GC pauses, critical for
   processing large disk images

No other language provides this combination. Go cannot run bare-metal, C lacks
memory safety, and other memory-safe languages lack the ecosystem maturity.

## References

- [rust-vmm Community](https://github.com/rust-vmm/community)
- [Firecracker](https://firecracker-microvm.github.io/)
- [The Rust Programming Language](https://doc.rust-lang.org/book/)
- [Rust Embedded Book](https://docs.rust-embedded.org/book/)
- [virtio-drivers](https://github.com/rcore-os/virtio-drivers)
