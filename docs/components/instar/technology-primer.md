# Technology Primer

There is a fair bit of assumed knowledge embodied in instar that needs to be
explained in order to really explain what is happening here. This page
attempts to walk through the background steps in a relatively complete way,
but as a result is fairly long. My apologies if this is too detailed an
explanation.

This description is also by definition Linux specific, and to a large
extent x86 specific.

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

- **CPU context**: General-purpose registers, instruction pointer, stack
  pointer, flags register, and FPU/SIMD state
- **Memory mapping**: A pointer to the `mm_struct` which describes the
  virtual address space via page tables and virtual memory areas (VMAs)
- **File descriptors**: A table of open files, sockets, and pipes
- **Credentials**: User ID, group ID, and capabilities
- **Namespaces**: Isolated views of system resources like PIDs, network
  interfaces, and mount points (this is how containers work)
- **Scheduling state**: Priority, CPU affinity, and time accounting
- **Signal handling**: Pending signals and registered signal handlers
- **Process relationships**: Parent process, children, and thread group

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

For instar's use case with virtio devices, IOMMU protection is less critical
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
bug in qemu, and various virtio vulnerabilities have been found. But they're
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

## And Then Finally... Instar

Instar takes a different approach from all of the above. Rather than running
a full OS, a container, or even a unikernel inside a VM, instar runs
**bare-metal code with no kernel at all**. The guest is compiled in
"freestanding" mode - no standard library, no system calls, no kernel
interface whatsoever.

There isn't a widely-adopted industry term for this approach, though it
shares DNA with several concepts:

- **Bare-metal programming**: Code running directly on hardware (or
  virtualized hardware) with no OS layer
- **Freestanding execution**: The C/Rust term for code compiled without
  a standard library
- **Kernel bypass**: Techniques like DPDK and SPDK that skip the kernel
  for specific operations, taken to its logical extreme
- **Split-trust architecture**: Where untrusted code runs in an isolated
  sandbox while trusted code handles privileged operations

**Why no kernel?**

Instar exists to solve a specific problem: safely parsing and converting
disk image formats. Image formats like qcow2 and VMDK have complex,
feature-rich structures - compression, encryption, snapshots, backing
files, sparse allocation. Parsing these formats has historically been a
source of security vulnerabilities. Any bug in the parser can potentially
be exploited by a malicious disk image.

The traditional approach is to parse images in a userspace process on the
host. But a vulnerability in that parser gives the attacker access to the
host system - exactly what we want to prevent when handling untrusted
images.

Instar's insight is to separate the concerns:

1. **Untrusted code** (image format parsing) runs inside a KVM sandbox
   with no access to host resources
2. **Trusted code** (actual file I/O) runs on the host, never interpreting
   image format structures

Even if an attacker completely compromises the guest through a parser bug,
they're trapped in a sandbox with no filesystem access, no network, no
syscalls - just the ability to read and write to virtual block devices that
the host controls.

**How it works without syscalls:**

The guest is compiled as bare-metal Rust:

```rust
#![no_std]    // No standard library
#![no_main]   // No main() - custom entry point

pub extern "C" fn _start() -> ! {
    // Initialize devices via MMIO, run operation, halt
    unsafe { asm!("hlt"); }
}
```

**The boot sequence:**

Before `_start()` runs, something has to get the CPU into a usable state. On
a normal Linux boot, the BIOS or UEFI firmware handles hardware initialization,
then a bootloader (GRUB, systemd-boot) loads the kernel, which then sets up
paging, interrupts, and drivers before reaching userspace. That's a lot of
machinery.

Instar takes a shortcut: the VMM (running on the host) configures the virtual
CPU's initial state directly via KVM ioctls. Instead of emulating a BIOS boot,
the VMM can set the vCPU's registers to whatever state it wants before starting
execution.

Instar skips real mode and protected mode entirely, starting the guest directly
in 64-bit long mode with paging already enabled. The VMM sets up:

- **Initial page tables**: A simple identity mapping where virtual addresses
  equal physical addresses (at least for the memory regions the guest needs).
  These page tables are placed in guest memory before boot.

- **Control registers**: CR0 with paging and protected mode enabled, CR3
  pointing to the page tables, CR4 with PAE (Physical Address Extension)
  enabled.

- **Segment registers**: CS, DS, SS, etc. configured for 64-bit flat memory
  model. The GDT (Global Descriptor Table) is set up with minimal descriptors.

- **RIP (instruction pointer)**: Set to the entry point (`_start`).

- **RSP (stack pointer)**: Pointing to a pre-allocated stack region.

When `KVM_RUN` executes, the vCPU begins executing at `_start` in 64-bit mode
with a working stack and identity-mapped memory. No BIOS, no bootloader, no
mode transitions - the guest code runs immediately.

This is why instar's boot time is measured in microseconds rather than seconds.
A traditional VM boot involves: firmware initialization → bootloader → kernel
decompression → kernel initialization → init system → application startup.
Instar skips all of that. The "boot" is just: set registers → run.

The tradeoff is that this requires careful coordination between the VMM and
the guest. The guest must be compiled to expect the specific memory layout and
initial state that the VMM provides. There's no flexibility to boot different
operating systems or use standard boot protocols - but for instar's single-
purpose use case, that flexibility isn't needed.

Instead of system calls, the guest uses two communication mechanisms:

1. **Virtio-block devices** for data I/O: The guest reads and writes disk
   data through standard VIRTIO block devices, communicating via MMIO
   registers and virtqueues in shared memory. No syscalls - just memory
   writes to specific addresses that the VMM interprets.

2. **Serial port** for control messages: Configuration, progress updates,
   and status messages flow through the serial port using x86 IN/OUT
   instructions. The host and guest exchange Protocol Buffer messages over
   this channel.

The guest never calls `open()`, `read()`, `write()`, `mmap()`, or any other
system call. It doesn't need to - it's not running on an OS. It accesses
hardware (virtual hardware provided by the VMM) directly.

**How virtio actually works:**

Virtio deserves a closer look since it's central to instar's I/O. The protocol
is designed for efficient communication between a guest and a hypervisor,
avoiding the overhead of emulating real hardware.

At its core, virtio uses a data structure called a "virtqueue" - a ring buffer
in shared memory that both guest and host can access. Each virtqueue has three
components:

1. **Descriptor table**: An array of buffer descriptors. Each descriptor
   contains a physical address, length, and flags (read/write, whether there's
   a next descriptor in a chain).

2. **Available ring**: Written by the guest, read by the host. When the guest
   wants to send a request, it populates descriptors with buffer addresses,
   chains them together, and adds the head descriptor's index to the available
   ring.

3. **Used ring**: Written by the host, read by the guest. When the host
   completes a request, it adds the descriptor index and the number of bytes
   written to the used ring.

A typical virtio-block read operation works like this:

1. The guest allocates three descriptors:
   - Descriptor 0: Points to a request header (containing the operation type
     and sector number), marked as device-readable
   - Descriptor 1: Points to the data buffer, marked as device-writable
   - Descriptor 2: Points to a status byte, marked as device-writable
   - These are chained: descriptor 0's "next" points to 1, descriptor 1's
     "next" points to 2

2. The guest adds descriptor 0's index to the available ring

3. The guest writes to a "doorbell" MMIO register to notify the host that
   work is available (more on how this notification works below)

4. The host (VMM) processes the request:
   - Reads the descriptor chain from shared memory
   - Extracts the sector number from the header
   - Reads the data from the underlying file
   - Writes the data to the guest's buffer (via the address in descriptor 1)
   - Writes a success status to the status buffer (descriptor 2)
   - Adds the completed descriptor to the used ring

5. The host injects an interrupt into the guest (or the guest polls)

6. The guest reads the used ring, sees the completion, and processes the data

This design minimizes VM exits: the guest can batch multiple requests before
ringing the doorbell, and the host can complete multiple requests before
injecting an interrupt. The shared memory model means data doesn't need to be
copied between address spaces - the host directly accesses guest memory
through the descriptor addresses (translated via EPT).

For instar, each virtio-block device represents a file on the host. The input
device exposes the source disk image; the output device exposes the destination.
The guest reads from one and writes to the other, with the VMM translating
virtqueue operations into actual file I/O.

**Avoiding VM exits with ioeventfd:**

Step 3 above mentioned that writing to the doorbell register "causes a VM
exit." That's the naive implementation, and it's expensive - as we discussed
earlier, a VM exit costs thousands of cycles. For high-throughput I/O, those
exits add up quickly.

KVM provides a mechanism called **ioeventfd** that avoids most of these exits.
The idea is simple: instead of trapping a guest MMIO write into the VMM via a
full VM exit, KVM can be configured to recognize writes to specific addresses
and signal a Linux eventfd directly in the kernel.

Here's how it works:

1. The VMM creates an eventfd (a Linux file descriptor that acts as a simple
   counter/signaling mechanism)

2. The VMM tells KVM: "when the guest writes to MMIO address X (the virtio
   doorbell), don't cause a VM exit - instead, signal this eventfd"

3. The VMM runs the vCPU in one thread while another thread (or async runtime)
   waits on the eventfd using epoll/io_uring

4. When the guest writes to the doorbell, KVM recognizes the address, signals
   the eventfd, and *continues guest execution* without a VM exit

5. The VMM's waiting thread wakes up, processes the virtqueue, and handles
   the I/O

The guest doesn't know or care whether a VM exit occurred - it just wrote to
an MMIO address. But from the VMM's perspective, the notification happened
asynchronously without stopping the guest. The vCPU can continue executing
(perhaps preparing the next I/O request) while the VMM processes the current
one.

There's a complementary mechanism called **irqfd** for the reverse direction.
Instead of the VMM injecting an interrupt by making a KVM ioctl (which
requires a syscall), the VMM can write to an irqfd, and KVM will inject the
interrupt directly. Combined with ioeventfd, this means the entire virtio
notification path - guest to host and back - can happen without expensive
transitions.

Instar uses ioeventfd for its virtio doorbell notifications. When the guest
submits an I/O request, the VMM receives an eventfd signal and can process
the request while the guest continues running. This is particularly valuable
for instar's workload, which involves streaming large amounts of data between
virtio-block devices - minimizing per-request overhead directly improves
throughput.

**Where KVM fits in:**

Instar uses Linux's KVM (Kernel-based Virtual Machine) for hardware
virtualization. KVM is a kernel module that exposes VT-x/AMD-V capabilities
through `/dev/kvm`. The architecture has two components:

1. **KVM kernel module**: Handles the hardware virtualization - creating VMs,
   managing vCPUs, configuring EPT, and processing VM exits that require
   kernel involvement
2. **Userspace VMM**: Instar implements its own minimal VMM that uses KVM's
   ioctl interface to configure the VM and handles device emulation (the
   virtio-block and serial devices) in userspace

Unlike qemu (which provides a full-featured VMM with dozens of emulated
devices), instar's VMM exposes only the devices the guest needs: two
virtio-block devices and a serial port. This minimal device model is part
of the security story - fewer emulated devices means less code that could
contain vulnerabilities.

**The data flow:**

```
+-------------------------------------------------------------------+
| Host                                                              |
|  +-------------------------------------------------------------+  |
|  | VMM (Virtual Machine Monitor)                               |  |
|  |  - Opens source and destination files on host               |  |
|  |  - Exposes them as virtio-block devices to guest            |  |
|  |  - Handles virtqueue requests (reads raw bytes)             |  |
|  |  - Never interprets image format structures                 |  |
|  +-------------------------------------------------------------+  |
|       |  virtio-block         |  virtio-block                     |
|       |  (input)              |  (output)                         |
|       v                       v                                   |
|  +-------------------------------------------------------------+  |
|  | KVM Sandbox (isolated VM)                                   |  |
|  |  +-------------------------------------------------------+  |  |
|  |  | Guest (bare-metal Rust, no kernel)                    |  |  |
|  |  |  - Parses image formats (qcow2, raw, vmdk)            |  |  |
|  |  |  - Performs conversions                               |  |  |
|  |  |  - Any exploit is contained - no escape possible      |  |  |
|  |  +-------------------------------------------------------+  |  |
|  +-------------------------------------------------------------+  |
+-------------------------------------------------------------------+
```

**What the guest includes:**

- A minimal boot sequence (CPU and memory initialization)
- A virtio-block driver (MMIO access to virtual devices)
- A serial port driver (x86 IN/OUT instructions)
- Image format parsers
- The conversion logic

**What the guest explicitly excludes:**

- Any kernel (Linux, library OS, or otherwise)
- System call interface
- Process management (there's only one execution context)
- Memory protection within the guest (single address space)
- Filesystem, network stack, or any other OS services

The entire guest binary is typically tens of kilobytes. Boot time is
measured in microseconds - the VM starts, the guest initializes its
devices, does its work, and halts.

**Comparison with other approaches:**

| Aspect            | Container      | VM + Linux    | Unikernel     | Instar         |
|-------------------|----------------|---------------|---------------|---------------|
| Kernel            | Shared host    | Full guest    | Library OS    | None          |
| Syscall interface | Yes (host)     | Yes (guest)   | Function calls| None          |
| Startup time      | ~Instant*      | Seconds       | Milliseconds  | Microseconds  |
| Guest image size  | 10s-100s MB    | 100s MB - GBs | KBs - MBs     | 10s of KB     |
| Attack surface    | Host kernel    | Guest kernel  | Minimal libOS | Device model  |
| I/O mechanism     | Syscalls       | Syscalls      | Function calls| Virtio/MMIO   |

*Container processes start instantly via fork/exec, but container runtime
setup (namespace creation, cgroup configuration, filesystem mounts) adds
overhead that varies by runtime and configuration.

**The security posture:**

The attack surface is reduced to:

1. The VMM's virtio-block device emulation
2. The serial port emulation
3. KVM's VM isolation (hardware-enforced)

This is a tiny fraction of the kernel attack surface (hundreds of syscalls,
filesystems, drivers) that containers expose. And unlike a unikernel, there's
no library OS code in the guest that could contain vulnerabilities - just
the bare minimum to read virtio queues and parse image formats.

An attacker who exploits a bug in the qcow2 parser gains control of a
process that:

- Cannot make any system calls (there's no kernel to call)
- Cannot access the host filesystem (only virtual block devices)
- Cannot access the network (none is provided)
- Cannot escape the VM (KVM hardware isolation)

The worst they can do is corrupt the output - which is undesirable but not
a host compromise.

**Tradeoffs:**

This extreme minimalism has costs:

- **Limited applicability**: This approach only makes sense for specific
  workloads where untrusted data parsing is the primary concern
- **Development complexity**: Writing bare-metal code without standard
  library support requires more careful engineering
- **Debugging difficulty**: Like unikernels, there's no shell or standard
  debugging tools in the guest
- **Single-purpose**: Each guest binary does one thing; no general-purpose
  computing

But for instar's use case - safely converting potentially malicious disk
images - these tradeoffs are worthwhile. The goal isn't to replace
containers or VMs for general workloads; it's to provide the strongest
possible isolation for a specific, dangerous operation.

**Operational considerations:**

A few questions naturally arise about running instar in practice:

- **Error handling**: If the guest encounters a malformed image or I/O error,
  it sends an error message over the serial port and halts. The VMM detects
  the halt (via a VM exit with exit reason HLT) and reports the failure to
  the caller. Parsing errors don't crash the host - they're contained.

- **Timeouts and DoS prevention**: A malicious image could cause the guest
  to infinite-loop or consume excessive time. The VMM can enforce timeouts,
  terminating the VM if it runs too long. Since the guest has no network
  access and limited resources (only the memory allocated to the VM), the
  DoS impact is bounded to the resources explicitly granted.

- **Attestation**: Unlike Nitro Enclaves, instar doesn't currently implement
  cryptographic attestation. For instar's threat model (protecting the host
  from malicious input, not protecting secrets from the host), attestation
  is less critical - we control both sides of the trust boundary. However,
  attestation could be valuable for verifying that a specific guest binary
  is running, which might matter in some deployment scenarios.

## Prior Art and Related Approaches

Instar's architecture - an isolated VM with restricted I/O channels and
minimal attack surface - has precedents in the industry. The most notable
is AWS Nitro Enclaves, though the threat model is interestingly inverted.

### AWS Nitro Enclaves

Nitro Enclaves are isolated compute environments that run alongside EC2
instances. They share several architectural properties with instar:

- **Hardware-enforced isolation**: Built on the Nitro Hypervisor, enclaves
  are fully isolated VMs with dedicated vCPUs and memory
- **No persistent storage**: Enclaves have no disk access
- **No network access**: External networking is completely disabled
- **No interactive access**: No SSH, no shell, no console
- **Single communication channel**: A vsock (virtual socket) provides the
  only path between the enclave and its parent instance

The vsock channel is conceptually similar to instar's virtio-block and serial
port combination - a restricted, well-defined interface that limits what can
flow between isolated and non-isolated code.

**The inverted threat model:**

Despite the architectural similarities, Nitro Enclaves and instar address
opposite threat scenarios:

| Aspect              | AWS Nitro Enclaves           | Instar                        |
|---------------------|------------------------------|------------------------------|
| **Protects**        | Sensitive data in enclave    | Host from malicious data     |
| **Threat**          | Compromised parent instance  | Malicious input files        |
| **Trust direction** | Enclave trusts nothing       | Host trusts nothing from guest|
| **Attestation**     | Cryptographic proof of code  | Not applicable               |
| **Use case**        | Process secrets securely     | Parse untrusted formats safely|

Nitro Enclaves are designed for **confidential computing** - processing
sensitive data (private keys, PII, healthcare records) such that even a
compromised parent instance or malicious administrator cannot access it.
The enclave protects its contents from the outside world.

Instar inverts this: the **host** is what we're protecting. The guest
processes untrusted, potentially malicious data, and we want to ensure that
even a complete compromise of the guest cannot affect the host. The sandbox
protects the outside world from its contents.

**Architectural differences:**

Nitro Enclaves run a minimal but complete Linux environment inside the
enclave. The enclave image includes a kernel, and applications make system
calls normally. The isolation comes from the hypervisor boundary and the
lack of I/O channels, not from the absence of a kernel.

Instar goes further by eliminating the kernel entirely. There's no system
call interface to potentially exploit, no kernel code that could contain
vulnerabilities. The guest is truly bare-metal.

| Aspect            | Nitro Enclaves         | Instar                  |
|-------------------|------------------------|------------------------|
| Guest kernel      | Minimal Linux          | None                   |
| System calls      | Yes (to guest kernel)  | None                   |
| Guest image       | 10s-100s MB            | 10s of KB              |
| Communication     | vsock                  | virtio-block + serial  |
| Attestation       | Built-in (PCR hashes)  | Not implemented        |

### Other Related Work

Several other projects explore similar territory:

- **Google Sandboxed API**: Runs untrusted code in a sandboxed process,
  communicating over a restricted RPC interface. Uses seccomp-bpf rather
  than VM isolation.

- **gVisor's Sentry**: Implements a user-space kernel that intercepts
  system calls, reducing the host kernel attack surface. The application
  still makes syscalls, but to a sandboxed kernel implementation.

- **Firecracker**: AWS's microVM technology prioritizes minimal attack
  surface in the VMM itself. While guests still run full kernels,
  Firecracker's device model is deliberately minimal.

- **Solo5**: A "unikernel monitor" that provides a minimal interface for
  unikernels, similar in spirit to instar's approach but targeting library
  OS workloads.

### Does this count as prior art?

The concept of "isolated compute with restricted I/O" is well-established.
Nitro Enclaves, in particular, demonstrate that meaningful computation can
happen with only a single communication channel and no filesystem or network
access.

What's less common is the combination of:

1. **No kernel at all** in the isolated environment
2. **Untrusted input processing** as the primary use case (vs. confidential
   computing)
3. **Virtio-block as the data channel** rather than a socket-based protocol

Instar draws inspiration from these prior systems while adapting the approach
to its specific threat model: safely handling data that might be actively
trying to exploit the parser.

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
9. **Instar** takes this further by eliminating the kernel entirely, running
   bare-metal code that communicates only through carefully constrained
   channels

Each step along this spectrum trades off between isolation strength,
performance, flexibility, and operational complexity. There is no universally
"right" choice - the appropriate level of isolation depends on the threat
model, the workload, and the operational constraints.

For instar's specific problem - safely handling untrusted disk images that
might be crafted to exploit parser vulnerabilities - the extreme end of the
spectrum makes sense. The narrow interface (virtio-block devices and serial
port), the absence of any kernel attack surface in the guest, and the
hardware-enforced VM boundary combine to provide defense in depth against a
class of attacks that has historically been difficult to defend against
through code review and testing alone.

