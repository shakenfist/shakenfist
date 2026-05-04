# KVM API and Bare-Metal Guest Setup

This document describes the Linux KVM (Kernel-based Virtual Machine) API and
how to set up a bare-metal guest using a custom VMM (Virtual Machine Monitor).

## Overview

KVM is a Linux kernel module that turns the kernel into a hypervisor. It
exposes a device file (`/dev/kvm`) that userspace programs can use to create
and manage virtual machines via ioctl calls.

A minimal VMM needs to:

1. Open `/dev/kvm` and create a VM
2. Allocate and map guest memory
3. Create one or more vCPUs
4. Configure vCPU state (registers, page tables, etc.)
5. Load guest code into memory
6. Run the vCPU in a loop, handling VM exits

## KVM Device Hierarchy

```
/dev/kvm (system-level)
    │
    ├── ioctl: KVM_CREATE_VM
    │       │
    │       └── VM file descriptor
    │               │
    │               ├── ioctl: KVM_SET_USER_MEMORY_REGION
    │               ├── ioctl: KVM_CREATE_VCPU
    │               │       │
    │               │       └── vCPU file descriptor
    │               │               │
    │               │               ├── ioctl: KVM_GET_REGS / KVM_SET_REGS
    │               │               ├── ioctl: KVM_GET_SREGS / KVM_SET_SREGS
    │               │               └── ioctl: KVM_RUN
    │               │
    │               └── ... (other VM-level ioctls)
    │
    └── ioctl: KVM_GET_API_VERSION, KVM_CHECK_EXTENSION, etc.
```

## Memory Setup

Guest memory is allocated in userspace and mapped into the VM using
`KVM_SET_USER_MEMORY_REGION`:

```c
struct kvm_userspace_memory_region {
    __u32 slot;              // Memory slot identifier
    __u32 flags;             // KVM_MEM_LOG_DIRTY_PAGES, etc.
    __u64 guest_phys_addr;   // Guest physical address
    __u64 memory_size;       // Size in bytes (must be page-aligned)
    __u64 userspace_addr;    // Host virtual address of backing memory
};
```

The VMM allocates memory (typically with `mmap` or aligned allocation), then
tells KVM to map it at a specific guest physical address. Multiple memory
regions can be configured in different slots.

## x86-64 Long Mode Setup

To run 64-bit code, the vCPU must be configured in long mode before the guest
starts. This requires setting up:

### Control Registers

| Register | Bits to Set | Purpose |
|----------|-------------|---------|
| CR0 | PE (bit 0), PG (bit 31) | Protected mode + Paging |
| CR3 | PML4 physical address | Page table root |
| CR4 | PAE (bit 5) | Physical Address Extension |
| EFER | LME (bit 8), LMA (bit 10) | Long Mode Enable/Active |

### Global Descriptor Table (GDT)

The GDT defines memory segments. For 64-bit long mode, segment limits and
bases are mostly ignored, but the GDT must still exist with valid descriptors:

| Entry | Selector | Description |
|-------|----------|-------------|
| 0 | 0x00 | Null descriptor (required) |
| 1 | 0x08 | 64-bit code segment |
| 2 | 0x10 | 64-bit data segment |

**64-bit code segment descriptor:**
- Type: Execute/Read (0xA)
- S: 1 (code/data segment)
- DPL: 0 (ring 0)
- P: 1 (present)
- L: 1 (64-bit mode)
- D: 0 (must be 0 when L=1)

**64-bit data segment descriptor:**
- Type: Read/Write (0x2)
- S: 1 (code/data segment)
- DPL: 0 (ring 0)
- P: 1 (present)

### Page Tables

Long mode requires 4-level page tables (or 5-level with LA57). For simplicity,
identity mapping with 2MB pages is often used:

```
PML4 (Page Map Level 4)
  └── Entry 0 → PDPT (Page Directory Pointer Table)
                  └── Entry 0 → PD (Page Directory)
                                  ├── Entry 0 → 0x00000000 (2MB page)
                                  ├── Entry 1 → 0x00200000 (2MB page)
                                  ├── Entry 2 → 0x00400000 (2MB page)
                                  └── ... (512 entries = 1GB)
```

Page table entry flags:
- Bit 0 (P): Present
- Bit 1 (R/W): Writable
- Bit 7 (PS): Page Size (1 = 2MB page in PD, 1GB page in PDPT)

### Segment Registers

After setting up the GDT, segment registers must be configured:

| Register | Selector | Description |
|----------|----------|-------------|
| CS | 0x08 | Code segment (must have L=1 for 64-bit) |
| DS, ES, SS | 0x10 | Data segments |
| FS, GS | 0x00 or 0x10 | Can be null or data segment |

### General Purpose Registers

| Register | Initial Value | Purpose |
|----------|---------------|---------|
| RIP | Entry point address | Instruction pointer |
| RSP | Stack top address | Stack pointer (16-byte aligned) |
| RFLAGS | 0x2 | Bit 1 always set |
| Others | 0 | Cleared |

## VM Exit Handling

When the guest performs certain operations, the vCPU exits back to the VMM.
The exit reason is available in the `kvm_run` structure (mapped via `mmap` on
the vCPU file descriptor).

Common exit reasons:

| Exit Reason | Cause | VMM Action |
|-------------|-------|------------|
| KVM_EXIT_HLT | Guest executed HLT | Completion signal or idle |
| KVM_EXIT_IO | Guest executed IN/OUT | Emulate I/O device |
| KVM_EXIT_MMIO | Guest accessed unmapped memory | Emulate memory-mapped I/O |
| KVM_EXIT_SHUTDOWN | Triple fault | Guest crashed, debug or terminate |
| KVM_EXIT_FAIL_ENTRY | Invalid vCPU state | VMM configuration error |

### I/O Exit Structure

For `KVM_EXIT_IO`, details are in `kvm_run.io`:

```c
struct {
    __u8 direction;    // KVM_EXIT_IO_IN or KVM_EXIT_IO_OUT
    __u8 size;         // 1, 2, or 4 bytes
    __u16 port;        // I/O port number
    __u32 count;       // Number of iterations (for string I/O)
    __u64 data_offset; // Offset in kvm_run for data
} io;
```

Common I/O ports:
- 0x3f8-0x3ff: COM1 serial port
- 0x2f8-0x2ff: COM2 serial port
- 0x80: Debug port (often used for POST codes)

## Minimal VMM Pseudocode

```rust
// 1. Open KVM
let kvm_fd = open("/dev/kvm", O_RDWR);

// 2. Create VM
let vm_fd = ioctl(kvm_fd, KVM_CREATE_VM, 0);

// 3. Allocate and map guest memory
let guest_mem = mmap(NULL, size, PROT_READ|PROT_WRITE, ...);
let region = kvm_userspace_memory_region {
    slot: 0,
    guest_phys_addr: 0,
    memory_size: size,
    userspace_addr: guest_mem,
    flags: 0,
};
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);

// 4. Set up GDT, page tables in guest memory
setup_gdt(guest_mem);
setup_page_tables(guest_mem);

// 5. Load guest code
memcpy(guest_mem + ENTRY_POINT, guest_binary, guest_size);

// 6. Create vCPU
let vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU, 0);

// 7. Map kvm_run structure
let kvm_run = mmap(NULL, vcpu_mmap_size, ..., vcpu_fd, 0);

// 8. Configure vCPU state
let mut sregs = ioctl(vcpu_fd, KVM_GET_SREGS);
sregs.cr0 = CR0_PE | CR0_PG;
sregs.cr3 = PAGE_TABLE_ADDR;
sregs.cr4 = CR4_PAE;
sregs.efer = EFER_LME | EFER_LMA;
// ... set up segments ...
ioctl(vcpu_fd, KVM_SET_SREGS, &sregs);

let mut regs = kvm_regs {
    rip: ENTRY_POINT,
    rsp: STACK_TOP,
    rflags: 0x2,
    ..Default::default()
};
ioctl(vcpu_fd, KVM_SET_REGS, &regs);

// 9. Run loop
loop {
    ioctl(vcpu_fd, KVM_RUN, 0);

    match kvm_run.exit_reason {
        KVM_EXIT_HLT => break,
        KVM_EXIT_IO => handle_io(kvm_run),
        KVM_EXIT_SHUTDOWN => panic!("Triple fault"),
        _ => { /* handle other exits */ }
    }
}
```

## Rust Crates

The rust-vmm project provides safe Rust wrappers for KVM:

- **kvm-ioctls**: Safe wrappers for KVM ioctl operations
- **kvm-bindings**: Rust definitions of KVM structures
- **vm-memory**: Guest memory management abstractions

## Limitations Without an OS

A bare-metal guest without an OS kernel has significant limitations:

- **No IDT**: Any exception causes a triple fault
- **No interrupts**: Timer, keyboard, etc. won't work
- **No memory management**: No dynamic allocation
- **No syscalls**: No file I/O, networking, etc.
- **Single address space**: No process isolation

These limitations are acceptable (even desirable) for isolated compute tasks
where the goal is minimal attack surface.

## References

- [Linux KVM API Documentation](https://www.kernel.org/doc/html/latest/virt/kvm/api.html)
- [OSDev Wiki: Long Mode](https://wiki.osdev.org/Long_Mode)
- [OSDev Wiki: GDT](https://wiki.osdev.org/GDT)
- [OSDev Wiki: Paging](https://wiki.osdev.org/Paging)
- [Intel SDM Volume 3: System Programming Guide](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)
- [rust-vmm GitHub](https://github.com/rust-vmm)
