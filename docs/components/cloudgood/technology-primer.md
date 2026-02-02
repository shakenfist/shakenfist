---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/technology-primer.md
---

# Technology Primer

There is a fair bit of assumed knowledge embodied in modern cloud that
needs to be explained in order to really explain what is happening here. This page attempts to walk through the background steps in a relatively
complete way, but as a result is fairly long. My apologies if this is too
detailed an explanation.

## What Is A Process?

To my mental model, a Linux process is a data structure. It has no real
existence at the silicon level apart from state in the instruction pointer,
registers, and page tables. When the kernel switches between two processes,
it stores the current CPU state in the outgoing process's kernel data
structure (called a `task_struct`), and loads the incoming process's saved
state back into the CPU.

Beyond CPU registers, a process also encompasses its virtual address space,
open file descriptors, credentials, and scheduling metadata - all managed
through kernel data structures. Specifically, a `task_struct` contains or
points to:

CPU context
:   General-purpose registers, instruction pointer, stack pointer, flags
    register, and FPU/SIMD state.

Memory mapping
:   A pointer to the `mm_struct` which describes the virtual address space
    via page tables and virtual memory areas (VMAs).

File descriptors
:   A table of open files, sockets, and pipes.

Credentials
:   User ID, group ID, and capabilities.

Namespaces
:   Isolated views of system resources like PIDs, network interfaces, and
    mount points (this is how containers work).

Scheduling state
:   Priority, CPU affinity, and time accounting.

Signal handling
:   Pending signals and registered signal handlers.

Process relationships
:   Parent process, children, and thread group.

**A note on threads**: Linux threads are also `task_struct` instances, but
they share certain resources with other threads in the same process. Most
notably, threads share the `mm_struct` (and thus the entire address space
and page tables), file descriptor table, and credentials. Each thread has
its own CPU context (registers, stack pointer) and scheduling state. From
the kernel's perspective, threads and processes are both "tasks" - the
difference is which resources they share. This is why "process isolation"
really means "address space isolation" - threads within a process can access
each other's memory freely, while separate processes cannot.

**A note on fork and exec**: Process creation in Linux reveals how central the
`task_struct` is. When a process calls `fork()`, the kernel creates a new
`task_struct` by copying the parent's - then modifies specific fields like the
PID, parent pointer, and some statistics. Memory pages are set up as
copy-on-write rather than immediately duplicated, and file descriptors are
cloned. The new process is essentially a duplicate of the parent with a few
fields tweaked.

The `exec()` family works differently: rather than copying a `task_struct`, it
*replaces* parts of the existing one. The process keeps its PID, parent
relationship, file descriptors (unless marked close-on-exec), and credentials,
but its memory map is completely replaced with the new program's code and data.
The instruction pointer is reset to the new program's entry point. From the
kernel's perspective, `exec()` transforms the current process in place rather
than creating a new one.

This is why the classic pattern for spawning a new program is `fork()` followed
by `exec()` in the child: fork creates the new `task_struct` (the new process
identity), and exec loads the new program into it.

**Copy-on-write and fork efficiency**: You might wonder how `fork()` can be
fast if it copies the parent's entire address space. The answer is that it
doesn't - not immediately. Instead, the kernel uses copy-on-write (COW).

When `fork()` creates the child process, both parent and child page tables
are set to point to the same physical pages. But crucially, those pages are
marked read-only in both processes' page tables (even if they were originally
writable). At this point, parent and child share all their memory - no copying
has occurred.

The magic happens when either process tries to write to a shared page. The
write triggers a page fault (because the page is marked read-only). The kernel's
page fault handler recognizes this as a COW fault: it allocates a new physical
page, copies the contents of the original page, updates the faulting process's
page table to point to the new copy (now marked writable), and resumes
execution. The other process keeps its mapping to the original page.

This means `fork()` is nearly instantaneous regardless of process size - only
the page tables themselves need to be copied (and even that can be optimized).
The actual memory copying is deferred until writes occur, and pages that are
never written (like read-only code segments) are never copied at all.

Note that COW affects *both* processes, not just the child. After `fork()`,
the parent's previously-writable pages are now marked read-only too. The
parent's first write to each shared page will trigger a COW fault just like
the child's would. For a long-running server that forks handler processes,
this means the parent pays a small penalty (one page fault per modified page)
after each fork. This is one reason `posix_spawn()` exists as an alternative
to `fork()` + `exec()` - it can avoid this overhead in cases where the parent
doesn't need a full copy of itself.

This is why the `fork()` + `exec()` pattern isn't as wasteful as it might
seem. The child calls `exec()` almost immediately after `fork()`, which
replaces the entire address space anyway. Thanks to COW, the child never
actually copies most of the parent's memory - it just sets up page table
entries that are immediately discarded when `exec()` loads the new program.

## Virtual Memory and Page Tables

Before diving into context switching, it helps to understand how virtual memory
works. Every process believes it has access to a large, contiguous address
space starting from zero (or near zero). In reality, this is an illusion
maintained by the CPU's Memory Management Unit (MMU) and the kernel.

The MMU translates virtual addresses (what the process sees) to physical
addresses (actual RAM locations) using a data structure called a page table.
On x86-64, this is a four-level hierarchy:

1. **PML4 (Page Map Level 4)**: The top-level table, containing 512 entries
2. **PDPT (Page Directory Pointer Table)**: Second level, 512 entries each
3. **PD (Page Directory)**: Third level
4. **PT (Page Table)**: Bottom level, pointing to actual 4KB physical pages

A virtual address is essentially split into indices for each level, plus an
offset within the final page. The CPU walks this hierarchy to find the
physical address. Because this walk is expensive (four memory accesses per
translation), the CPU caches recent translations in the Translation Lookaside
Buffer (TLB).

The TLB is implemented in dedicated SRAM inside the CPU die itself - it sits
right next to the MMU and operates at speeds comparable to or faster than L1
cache. A TLB hit adds essentially zero observable latency to a memory access
because it's pipelined with address generation. To put this in perspective:

| Component | Typical Latency |
|-----------|-----------------|
| TLB hit   | ~1 cycle        |
| L1 cache  | ~4-5 cycles     |
| L2 cache  | ~12-14 cycles   |
| L3 cache  | ~40-50 cycles   |
| DRAM      | ~100-300 cycles |

On a TLB miss, the CPU must walk the four-level page table hierarchy, which
in the worst case means four separate DRAM accesses - potentially over a
thousand cycles. Modern CPUs mitigate this in several ways: page table
entries themselves are cacheable in L1/L2/L3, dedicated "page walk caches"
store intermediate page table levels, and hardware page walkers perform the
lookup in parallel with other operations rather than completely stalling the
pipeline.

The TLB is small - typically a few hundred to a few thousand entries across
its L1 and L2 levels - so it relies heavily on temporal and spatial locality.
Programs that access memory randomly across a large address space pay a
significant performance penalty.

Each process has its own page table hierarchy stored in RAM. The kernel
maintains these structures and updates them as processes allocate memory,
map files, or share memory regions.

## Context Switching

When the kernel switches from one process to another, it needs to:

1. Save the outgoing process's CPU registers to its `task_struct`
2. Switch to the incoming process's page tables
3. Restore the incoming process's CPU registers

The page table switch is surprisingly efficient. The CPU has a control
register called CR3 that holds the physical address of the current process's
top-level page table (PML4). To switch address spaces, the kernel simply
writes the new process's PML4 address to CR3 - a single register write.

The actual page table data structures in RAM are not copied or overwritten.
Each process maintains its own page table hierarchy, and switching just
changes which hierarchy the CPU consults.

However, changing CR3 has a side effect: the TLB becomes invalid. The cached
translations belong to the old process's address space and are now wrong.
Traditionally, changing CR3 flushes the entire TLB, forcing the CPU to
re-walk the page tables for every memory access until the cache warms up
again. This TLB flush is one of the main costs of context switching.

Modern x86 CPUs provide optimizations to reduce this cost:

- **PCID (Process Context Identifiers)**: The CPU can tag TLB entries with a
  12-bit process identifier. When CR3 is changed, only entries with
  non-matching PCIDs are invalidated. This allows TLB entries to survive
  context switches and be reused when switching back to a process.

- **Global pages**: Pages can be marked as "global" in the page table entry.
  Global pages are not flushed when CR3 changes. The kernel uses this for
  kernel memory mappings, which are identical across all processes anyway.

Speaking of kernel mappings: on x86-64, the virtual address space is split
between user space (lower half) and kernel space (upper half). Every
process's page tables map the kernel into the same location using the same
physical pages. This means the kernel doesn't need to switch page tables
when handling system calls - it's already mapped into every process's
address space.

This design also explains why kernel memory is protected from user processes:
the page table entries for kernel pages have a "supervisor" bit set, causing
the CPU to fault if user-mode code tries to access them.

## Meltdown and Kernel Page Table Isolation

The elegant design described above - mapping the kernel into every process
and using global pages to preserve kernel TLB entries - worked well for
decades. Then, in January 2018, the Meltdown vulnerability was disclosed,
and everything changed.

Meltdown exploited a fundamental property of modern CPUs: speculative
execution. When the CPU encounters a memory access, it doesn't wait to
verify permissions before speculatively loading the data and continuing
execution. If the access turns out to be illegal, the CPU rolls back the
architectural state - but not before the speculatively loaded data has
affected the cache in measurable ways.

This meant that even though user-mode code couldn't directly read kernel
memory (the supervisor bit caused a fault), it could speculatively access
kernel memory and then use cache timing side-channels to extract the data.
The kernel was mapped into every process, so every process could potentially
read any kernel memory - including passwords, encryption keys, and the
memory of other processes that the kernel had accessed.

The mitigation, Kernel Page Table Isolation (KPTI, also known as KAISER),
fundamentally restructured the relationship between user and kernel address
spaces:

- Each process now maintains **two separate page table hierarchies**: one
  for user mode and one for kernel mode
- The user-mode page tables have the kernel almost entirely unmapped - only
  a tiny "trampoline" region remains to handle the transition into kernel
  mode
- On every system call, interrupt, or exception, the CPU must switch between
  these two page table hierarchies by writing to CR3
- Global pages can no longer be used for kernel mappings, since the kernel
  shouldn't be visible in user-mode TLB entries at all

The performance implications were significant. Before KPTI, entering the
kernel was relatively cheap - the kernel was already mapped. After KPTI,
every user-to-kernel transition requires a CR3 write, and kernel TLB entries
must be rebuilt from scratch. Workloads with frequent system calls (like
database servers or I/O-heavy applications) saw measurable slowdowns.

PCID helps mitigate this cost. With PCID, the user and kernel page tables
can have different process context identifiers. When switching from user to
kernel mode, the CPU can preserve the user-mode TLB entries (tagged with the
user PCID) rather than flushing them entirely. When returning to user mode,
those entries are still valid. This doesn't eliminate the overhead - the
kernel TLB still needs warming on every entry - but it significantly reduces
the impact on user-space performance.

The broader lesson from Meltdown is that isolation has real costs. The
supervisor bit was supposed to provide isolation, but speculative execution
broke that assumption. KPTI restored isolation at the cost of TLB efficiency.
This pattern - adding isolation layers and paying performance penalties -
recurs throughout systems design, from process isolation to containers to
virtual machines.

**A note on Spectre**: Meltdown had a sibling vulnerability disclosed at the
same time. Spectre exploits a different aspect of speculative execution:
branch prediction. By training the CPU's branch predictor, an attacker can
cause a victim process to speculatively execute code paths it wouldn't
normally take, leaking data through cache side-channels. Unlike Meltdown,
Spectre doesn't require the kernel to be mapped - it can leak data between
processes, or between user code and kernel code even with KPTI. Mitigations
include retpolines (indirect branch replacement), microcode updates, and
careful code auditing for "spectre gadgets." Spectre is harder to exploit
but also harder to mitigate comprehensively. Both vulnerabilities demonstrated
that performance optimizations (speculative execution, branch prediction)
can have security implications that weren't anticipated when they were
designed.

## Protection Rings

We've mentioned the "supervisor bit" that protects kernel memory from user
processes, but this is part of a broader CPU feature: protection rings. On
x86, the CPU supports four privilege levels, numbered 0 through 3, often
visualized as concentric rings:

- **Ring 0**: Most privileged - full access to all CPU instructions and
  hardware. The kernel runs here.
- **Ring 1**: Originally intended for device drivers
- **Ring 2**: Originally intended for device drivers
- **Ring 3**: Least privileged - restricted instruction set, no direct
  hardware access. User applications run here.

In practice, mainstream operating systems only use Ring 0 (kernel) and
Ring 3 (user). Rings 1 and 2 were designed for a more granular privilege
model that never gained widespread adoption. Some older systems like OS/2
used them, but Linux, Windows, and macOS all use the simpler two-ring model.

The current privilege level (CPL) is stored in the lowest two bits of the
CS (code segment) register. When code attempts to execute a privileged
instruction or access memory with incompatible privilege requirements, the
CPU raises a general protection fault (#GP) or page fault (#PF).

Certain operations are restricted to Ring 0:

- Modifying control registers (CR0, CR3, CR4, etc.)
- Accessing I/O ports directly (unless explicitly permitted via the I/O
  permission bitmap)
- Executing instructions like `HLT` (halt), `LGDT` (load global descriptor
  table), `LIDT` (load interrupt descriptor table), and `MOV` to debug
  registers
- Modifying the interrupt flag (enabling/disabling interrupts)
- Accessing memory pages marked as supervisor-only

The transition between rings is carefully controlled. User code (Ring 3)
cannot simply jump to kernel code (Ring 0). Instead, the CPU provides
specific mechanisms:

- **System calls**: The `SYSCALL`/`SYSRET` instructions (or older
  `INT 0x80`/`SYSENTER`) provide controlled entry points into the kernel.
  The CPU automatically switches to Ring 0, loads a known kernel code
  address, and saves the user-mode state.
- **Interrupts and exceptions**: Hardware interrupts and CPU exceptions
  (like page faults) cause automatic transitions to Ring 0 handlers defined
  in the Interrupt Descriptor Table (IDT).
- **Call gates**: A legacy mechanism allowing controlled calls between
  privilege levels, rarely used in modern systems.

When returning from kernel to user mode, the kernel uses `SYSRET` or `IRET`
instructions, which restore the user-mode state and drop the privilege level
back to Ring 3.

### Virtualization and Ring -1

The two-ring model worked well until virtualization became mainstream. The
problem: a guest operating system's kernel expects to run in Ring 0 with
full hardware access, but the hypervisor also needs Ring 0 to maintain
control. Early virtualization solutions used "ring deprivileging" - running
the guest kernel in Ring 1 or Ring 3 and trapping privileged operations -
but this was complex and slow.

Intel VT-x and AMD-V solved this by adding hardware virtualization support,
effectively creating a new privilege level sometimes called "Ring -1" or
"VMX root mode." The hypervisor runs in this new most-privileged mode, while
guest operating systems run in "VMX non-root mode" where they can use Ring 0
normally - but certain operations (controlled by the hypervisor) cause a
"VM exit" that transfers control back to the hypervisor.

This added another layer to the isolation hierarchy: hypervisor → guest
kernel → guest user space, each with hardware-enforced boundaries.

### Extended Page Tables (EPT/NPT)

Virtualization also required solving the memory management problem. A guest
OS expects to manage physical memory through page tables, but the hypervisor
can't give it actual physical memory - it needs to maintain isolation between
VMs and control memory allocation.

Early virtualization used "shadow page tables": the hypervisor maintained a
hidden copy of the guest's page tables, translating guest-physical addresses
to host-physical addresses on the fly. Every time the guest modified its
page tables, the hypervisor had to intercept and update the shadow copy.
This caused frequent VM exits and was a major performance bottleneck.

Intel EPT (Extended Page Tables) and AMD NPT (Nested Page Tables) added
hardware support for two-level address translation:

1. **Guest page tables**: Translate guest-virtual to guest-physical addresses
   (controlled by the guest OS, just like on bare metal)
2. **EPT/NPT tables**: Translate guest-physical to host-physical addresses
   (controlled by the hypervisor, invisible to the guest)

The CPU performs both translations in hardware. When a guest accesses memory,
the MMU walks the guest page tables to get a guest-physical address, then
walks the EPT/NPT tables to get the actual host-physical address. This
eliminates most memory-related VM exits - the guest can modify its own page
tables freely without hypervisor intervention.

The cost is that TLB misses become more expensive: a full page walk now
involves walking two four-level hierarchies (potentially 24 memory accesses
in the worst case). Modern CPUs mitigate this with combined TLB entries that
cache the full guest-virtual to host-physical translation.

EPT/NPT also provides memory isolation between VMs. A guest can only access
host-physical memory that the hypervisor has mapped into its EPT tables.
Attempting to access unmapped memory causes an "EPT violation" VM exit,
allowing the hypervisor to handle the fault (or terminate the VM).

### IOMMU and Device Isolation (VT-d/AMD-Vi)

EPT protects memory from guest CPU access, but there's another path to
physical memory: DMA (Direct Memory Access). Devices like network cards,
storage controllers, and GPUs can read and write to RAM directly, bypassing
the CPU entirely. Without protection, a compromised or malicious device could
DMA into arbitrary memory - including the hypervisor or other VMs.

The IOMMU (I/O Memory Management Unit) extends the page table concept to
devices. Intel calls their implementation VT-d (Virtualization Technology
for Directed I/O); AMD calls theirs AMD-Vi. The IOMMU sits between devices
and memory, translating device-physical addresses to host-physical addresses
through its own set of page tables.

Just as EPT creates a layer of address translation for guest CPUs, the IOMMU
creates a layer for device DMA:

| Access Path     | Translation                              | Protection Unit |
|-----------------|------------------------------------------|-----------------|
| Guest CPU       | Guest-virtual → Guest-physical → Host-physical | EPT/NPT        |
| Device DMA      | Device-physical → Host-physical          | IOMMU           |

When a device is assigned to a VM ("device passthrough"), the hypervisor
configures the IOMMU so that the device can only DMA to memory belonging to
that VM. The device sees what it believes are physical addresses, but those
are translated through IOMMU page tables that the hypervisor controls. If
the device tries to access memory outside its allowed range, the IOMMU
blocks the access and raises an interrupt.

This completes the memory isolation picture:

- **EPT/NPT**: Prevents guest CPUs from accessing memory outside their VM
- **IOMMU**: Prevents devices from accessing memory outside their assigned VM

Without IOMMU protection, device passthrough would be a gaping security hole.
A guest with a passed-through network card could program it to DMA anywhere
in host memory, completely bypassing VM isolation. The IOMMU ensures that
even with direct hardware access, the device remains confined to its VM's
memory space.

The IOMMU also enables another important feature: interrupt remapping. Just
as devices can DMA to arbitrary addresses, they can also send interrupts
that could be used to attack the host. The IOMMU can filter and remap device
interrupts, ensuring they're delivered only to the appropriate VM.

For imago's use case with virtio devices, IOMMU protection is less critical
because virtio devices are emulated in userspace - they don't have direct
hardware DMA capabilities. But understanding the IOMMU completes the picture
of how modern systems achieve full memory isolation in virtualized
environments.

### The Cost of VM Exits

VM exits are significantly more expensive than system calls - typically by an
order of magnitude or more. Understanding why requires looking at what each
transition must accomplish.

**System call overhead:**

A system call using `SYSCALL` is relatively streamlined. The CPU:

1. Saves the user-mode instruction pointer and flags
2. Loads a kernel code address from MSRs (model-specific registers)
3. Switches to Ring 0
4. (With KPTI) Switches CR3 to the kernel page tables

The instruction itself takes roughly 50-100 cycles on modern CPUs. With KPTI,
the CR3 switch and subsequent TLB misses add overhead, but the total is still
typically in the range of 100-500 cycles for the transition itself (not
counting the actual work the kernel does).

**VM exit overhead:**

A VM exit is far more involved. The CPU must:

1. Save the entire guest state to the VMCS (Virtual Machine Control
   Structure): all general-purpose registers, control registers, segment
   registers, the guest's interrupt state, and various other fields
2. Load the hypervisor's state from the VMCS: its own registers, control
   registers, and segment state
3. Switch to VMX root mode
4. Transfer control to the hypervisor's exit handler

The VM exit itself typically costs 500-1000 cycles. But that's not the whole
story - the hypervisor must then:

1. Determine why the exit occurred (read the exit reason from VMCS)
2. Handle the exit (emulate an instruction, handle I/O, etc.)
3. Prepare for VM entry (update VMCS fields as needed)
4. Execute `VMRESUME` to return to the guest

The VM entry is similarly expensive - another 500+ cycles to restore all the
guest state. Round-trip, a VM exit followed by immediate re-entry can easily
cost 1500-3000 cycles, compared to a few hundred for a system call.

| Transition          | Typical Cost      | State Saved                    |
|---------------------|-------------------|--------------------------------|
| System call         | 100-500 cycles    | IP, flags, a few registers     |
| VM exit + entry     | 1500-3000 cycles  | Entire CPU state, VMCS fields  |

This cost differential explains why hypervisors work hard to minimize VM
exits. Techniques include:

- **Paravirtualization**: Modifying guest kernels to use hypercalls instead
  of operations that cause exits
- **Hardware acceleration**: Features like EPT (Extended Page Tables) and
  posted interrupts reduce exits for memory and interrupt operations
- **Exit batching**: Handling multiple pending operations in a single exit
- **VMCS shadowing**: Reducing exits for nested virtualization

The performance gap also explains why containers became popular for workloads
that don't require strong isolation: they avoid VM exit overhead entirely by
running directly on the host kernel.

### The Security Advantages of Virtualization Over Containers

Containers and virtual machines both provide isolation, but the nature of
that isolation differs fundamentally. Understanding why VMs are harder to
escape requires examining where the trust boundary lies in each model.

**The container isolation model:**

Containers run directly on the host kernel. As described earlier, process
creation in Linux works via `fork()` and `exec()` - the kernel copies the
parent's `task_struct` and then loads a new program. Container processes are
created exactly this way; they're ordinary Linux processes. The "container"
aspect comes entirely from kernel features that restrict what those processes
can see and do:

- **Namespaces**: Provide isolated views of system resources (PIDs, network,
  mounts, users, etc.)
- **Cgroups**: Limit resource consumption (CPU, memory, I/O)
- **Seccomp**: Filters which system calls a process can make
- **Capabilities**: Fine-grained privileges replacing the all-or-nothing root
  model
- **LSMs (AppArmor, SELinux)**: Mandatory access control policies

The critical point: all container isolation is enforced by the same kernel
that the container's processes are making system calls into. If an attacker
finds a kernel vulnerability - a bug in any of the hundreds of system calls,
any filesystem, any network protocol, any device driver - they can
potentially escape the container and gain full host access.

The Linux kernel exposes an enormous attack surface. It has roughly 400+
system calls, dozens of filesystems, hundreds of device drivers, and complex
subsystems like networking and BPF. Despite decades of hardening, kernel
vulnerabilities are discovered regularly. A container escape typically
requires just one exploitable bug in this vast codebase.

**The VM isolation model:**

Virtual machines have a fundamentally different trust boundary. The guest
kernel runs in VMX non-root mode, and all its interactions with hardware are
mediated by the hypervisor. The guest can execute any instruction, make any
system call to its own kernel, and access any of its own memory - none of
this is visible to or validated by the hypervisor.

The hypervisor only becomes involved when the guest attempts certain
privileged operations that cause VM exits:

- Accessing emulated hardware (disk controllers, network cards)
- Executing certain privileged instructions
- Accessing memory outside its allocated range (caught by EPT/NPT)

The attack surface is dramatically smaller. Instead of 400+ system calls,
an attacker must find a vulnerability in:

- The VM exit handling code
- Emulated device models (virtio, IDE, network cards)
- The memory management (EPT violations)

Modern hypervisors like KVM have a much smaller trusted codebase than the
full kernel. And critically, the guest kernel's bugs are contained - a
buffer overflow in the guest's ext4 implementation doesn't help the attacker
escape, because that code runs in non-root mode with no special access.

**Why VM escapes are rare:**

| Aspect                | Container                    | Virtual Machine              |
|-----------------------|------------------------------|------------------------------|
| Trust boundary        | Kernel system call interface | VM exit interface            |
| Attack surface        | 400+ syscalls, huge codebase | Dozens of exit reasons       |
| Guest kernel bugs     | Directly exploitable         | Contained within guest       |
| Hardware access       | Shared, namespace-isolated   | Emulated, hypervisor-mediated|
| Escape complexity     | Single kernel bug            | Hypervisor + hardware bug    |

To escape a VM, an attacker typically needs to:

1. Find a bug in the hypervisor's VM exit handling or device emulation
2. Craft input that triggers the bug from within the guest
3. Achieve code execution in the hypervisor context (Ring -1 / root mode)

These bugs exist - VENOM (2015) exploited a floppy disk controller emulation
bug in QEMU, and various virtio vulnerabilities have been found. But they're
rarer than kernel bugs, and the exploitation is more constrained because the
attacker controls less of the environment.

**The hybrid approaches:**

Recognizing this tradeoff, several projects attempt to combine container
ergonomics with VM-like isolation:

- **gVisor**: Implements a user-space kernel that handles system calls,
  reducing the host kernel attack surface
- **Kata Containers**: Runs each container inside a lightweight VM
- **Firecracker**: AWS's microVM technology optimized for serverless,
  providing VM isolation with minimal overhead

These approaches accept some VM overhead in exchange for stronger isolation
than traditional containers provide.

## Unikernels

The discussion so far has implicitly assumed that a virtual machine runs a
full operating system: a general-purpose kernel with hundreds of drivers,
multiple filesystems, network stacks, user management, and all the machinery
needed to support arbitrary workloads. But this raises an uncomfortable
question: if we're running a single application in a VM, why do we need all
that complexity?

**The overhead of a full OS in a VM:**

A typical Linux VM includes:

- A complete kernel with support for hardware it will never see (the
  hypervisor presents a small set of emulated or paravirtualized devices)
- Dozens of system services (init, logging, cron, udev, networking daemons)
- A full userspace with shells, utilities, and package management
- Multiple filesystems, often with journaling overhead
- User/group management, even though there's typically only one "user"

This imposes real costs:

- **Memory**: The guest kernel, its caches, and system services consume RAM
  that could be used by the application
- **Boot time**: A full Linux boot takes seconds - an eternity for workloads
  that need to scale rapidly
- **Attack surface**: Every service running in the guest is potential attack
  surface, even if the VM escape is hard
- **Maintenance burden**: The guest OS needs patching, updates, and
  configuration management

For a single-purpose workload like a web server or a function-as-a-service
handler, most of this machinery is pure overhead.

**The unikernel approach:**

Unikernels take a radical approach: instead of running an application on top
of an operating system, they compile the application directly with just the
OS components it needs into a single bootable image. There is no kernel/user
separation, no multiple processes, no shell - just the application and its
minimal runtime.

The concept originates from "library operating systems" (libOS), where OS
functionality is provided as libraries linked into the application rather
than as a separate privileged kernel. The application runs in a single
address space, makes function calls instead of system calls, and boots
directly on the hypervisor (or bare metal).

A unikernel typically includes:

- A minimal boot sequence (just enough to initialize the CPU and memory)
- A network stack (often a streamlined implementation like lwIP)
- Storage drivers for the specific virtual devices available
- The language runtime (if applicable)
- The application code

What it explicitly excludes:

- Process management (there's only one "process")
- User management (no users, no permissions within the unikernel)
- A shell or any interactive debugging tools
- Drivers for hardware that doesn't exist in the target environment
- Most of POSIX (or provides only the subset the application needs)

**The benefits:**

| Aspect         | Traditional VM          | Unikernel              |
|----------------|-------------------------|------------------------|
| Image size     | Hundreds of MB to GB    | Tens of KB to a few MB |
| Boot time      | Seconds to minutes      | Milliseconds           |
| Memory footprint| Hundreds of MB minimum | Single-digit MB possible|
| Attack surface | Full kernel + services  | Application-specific   |
| Maintenance    | OS updates + app updates| Single artifact        |

The security story is interesting. While the unikernel still runs inside a
VM (so the hypervisor trust boundary discussion still applies), the attack
surface within the VM is dramatically reduced. There's no shell to drop into,
no unnecessary services to exploit, no privilege escalation because there
are no privilege levels. An attacker who compromises the application has...
the application, which they already had.

**The tradeoffs:**

Unikernels are not without significant challenges:

- **Debugging**: With no shell, no strace, no gdb, and no logging
  infrastructure, debugging a misbehaving unikernel is genuinely difficult.
  Many unikernel projects provide special debugging builds or rely on the
  hypervisor's debugging facilities.

- **Single application**: By design, a unikernel runs one application. If
  your workload requires multiple cooperating processes, you need multiple
  unikernels (or a different approach entirely).

- **Language constraints**: Many unikernel systems are tightly coupled to
  specific languages. MirageOS is OCaml-only; Nanos supports C/C++/Go but
  not arbitrary binaries. This limits what you can run.

- **POSIX compatibility**: Applications written for Linux expect POSIX
  system calls. Unikernels either provide incomplete POSIX implementations
  (causing subtle breakage) or require applications to be written against
  their specific APIs.

- **Ecosystem immaturity**: Compared to containers or traditional VMs, the
  unikernel ecosystem is small. Tooling, documentation, and community support
  are limited.

**Notable unikernel projects:**

- **MirageOS**: A pioneering project in OCaml, emphasizing type safety and
  minimal trusted code
- **IncludeOS**: C++ unikernel focused on high-performance networking
- **Unikraft**: A modular approach allowing you to select exactly which OS
  components to include, supporting multiple languages
- **Nanos**: Focuses on running existing Linux binaries with a compatibility
  layer
- **OSv**: Designed to run a single JVM or other managed runtime efficiently

**Where unikernels fit:**

Unikernels occupy a specific niche: single-purpose, performance-sensitive
workloads where the operational overhead of a full OS is unjustifiable. They
excel at:

- Network functions (routers, load balancers, firewalls)
- Microservices with well-defined interfaces
- Function-as-a-service / serverless workloads
- Edge computing where resources are constrained
- High-security applications where minimal attack surface matters

They are poorly suited to:

- Applications requiring multiple processes or complex IPC
- Workloads that need to be debugged in production
- Legacy applications with deep POSIX dependencies
- Environments where operators expect traditional Linux tooling

The unikernel vision - specialized, minimal, single-purpose VMs - represents
one extreme of the isolation/overhead tradeoff. Containers represent another
extreme (minimal isolation overhead, shared kernel). Traditional VMs sit in
the middle. The right choice depends on the workload's security requirements,
performance constraints, and operational reality.

## Conclusion

This document has traced a path through the landscape of compute isolation:

1. **Processes** provide the basic unit of isolation through separate address
   spaces, enforced by page tables and the MMU
2. **Virtual memory** creates the illusion of private memory for each process,
   with the TLB providing the performance needed to make this practical
3. **Protection rings** give the kernel privileged access to enforce isolation
   between processes
4. **Speculative execution vulnerabilities** (Meltdown, Spectre) showed that
   even hardware-enforced isolation can have subtle gaps, leading to costly
   mitigations like KPTI
5. **Virtualization** adds another layer of isolation (Ring -1), with EPT
   providing memory isolation between VMs
6. **Containers** trade strong isolation for performance, sharing the host
   kernel but using namespaces, cgroups, and seccomp to limit access
7. **VMs** provide stronger isolation at the cost of VM exit overhead and
   resource duplication
8. **Unikernels** reduce the overhead by eliminating the general-purpose OS,
   leaving only application-specific components

Each step along this spectrum trades off between isolation strength,
performance, flexibility, and operational complexity. There is no universally
"right" choice - the appropriate level of isolation depends on the threat
model, the workload, and the operational constraints.

--8<-- "docs-include/abbreviations.md"
