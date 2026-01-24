# Memory mapped devices

We've briefly discussed memory mapped devices in the
[technology primer](/components/cloudgood/technology-primer/), but we haven't really talked about
what's happening here and why.

## A tale of two philosophies

The question of how a CPU should communicate with peripheral devices has been
answered in two fundamentally different ways throughout computing history:
memory-mapped I/O (MMIO) and port-mapped I/O (PMIO). Understanding why both
exist requires a journey back to the earliest days of computing.

### Before microprocessors

The concept of memory-mapped I/O predates microprocessors entirely. Early
mainframes and minicomputers in the 1950s and 1960s faced the same fundamental
question: how should the CPU talk to devices like card readers, tape drives,
and terminals? Some machines used dedicated I/O instructions, while others
mapped device registers into the same address space as memory.

The approach that would prove most influential came from Digital Equipment
Corporation's PDP-11, introduced in 1970. The PDP-11 used a unified address
space where everything -- RAM, ROM, device registers, and even the CPU's own
status registers -- lived in a single 16-bit address map. Want to read a
character from the terminal? Read from address 0177560. Want to check if the
disk is ready? Read from the disk controller's status register at its assigned
address. This elegance meant the same `MOV` instruction that copied data
between memory locations could also control hardware.

The PDP-11's influence cannot be overstated. Unix was developed on it, and its
clean MMIO architecture influenced generations of computer architects. When
designers at companies like Motorola, ARM, MIPS, Sun, and IBM created their
processors, they followed the PDP-11's lead: a unified address space with no
special I/O instructions.

### Intel's different path

Intel's microprocessors took a different route, one rooted in their origins as
embedded controllers rather than general-purpose computers. The Intel 8008
(1972) was designed for Datapoint Corporation's programmable terminal, and it
included separate `IN` and `OUT` instructions that accessed a dedicated I/O
address space distinct from memory. The 8080 (1974) continued this tradition,
and when the 8086 (1978) launched the x86 dynasty, it carried port-mapped I/O
forward.

This approach had practical appeal for the microprocessor market of the 1970s.
With address buses of only 16 bits (65,536 bytes of addressable memory), every
byte of address space was precious. A separate I/O space meant device registers
didn't consume any of that limited memory map. The simpler decoding logic also
reduced chip count in an era when every IC on a board added cost and complexity.

Port-mapped I/O became deeply embedded in the PC ecosystem. The original IBM PC
(1981) used I/O ports for nearly everything: the keyboard controller at ports
0x60-0x64, the interrupt controller at 0x20-0x21, the floppy controller, serial
ports, parallel ports, and the system timer all had their assigned port ranges.
Decades of software, device drivers, and operating systems were written
expecting this architecture.

### The architectures diverge

By the 1980s and 1990s, the computing world had split into two camps:

**MMIO-only architectures:**

- Motorola 68000 family (1979) -- Used in early Macintosh, Amiga, Atari ST
- ARM (1985) -- Now dominant in mobile devices and increasingly in servers
- MIPS (1985) -- Popular in embedded systems and early workstations
- SPARC (1987) -- Sun Microsystems' workstation and server processor
- PowerPC (1992) -- Apple Macintosh, IBM servers, game consoles

These architectures provided no separate I/O instructions at all. Every device
register was simply an address in memory, accessed with ordinary load and store
instructions.

**Port-mapped I/O architectures:**

- Intel x86 family -- The dominant PC architecture
- Zilog Z80 -- Popular in 8-bit home computers and embedded systems

### Convergence in the modern era

As systems grew more complex, even Intel's x86 had to embrace memory-mapped
I/O. The turning point came with PCI (Peripheral Component Interconnect),
introduced in 1992. PCI devices could use either I/O ports or memory-mapped
regions, but MMIO offered significant advantages:

- Larger address ranges (32-bit or 64-bit instead of 16-bit port addresses)
- Better performance through burst transfers and write combining
- Simpler programming model for complex devices
- Compatibility with the rest of the industry

Modern x86 systems use a hybrid approach. Legacy devices and some system
functions still use I/O ports (you'll still find the keyboard controller at
port 0x60), but graphics cards, network adapters, NVMe drives, and most modern
peripherals use memory-mapped I/O exclusively. The PCIe bus that connects these
devices maps their registers and memory regions into the system's physical
address space, often in the region above 4GB to avoid conflicts with RAM.

### Why MMIO won

Memory-mapped I/O ultimately proved more versatile for several reasons:

1. **Unified programming model**: The same instructions that access memory can
   access devices. No special I/O instructions to learn or encode.

2. **Full instruction set availability**: You can use any addressing mode, any
   register, and any instruction that works with memory. With port I/O, you're
   limited to what the `IN` and `OUT` instructions support.

3. **Larger address space**: Modern systems have 48-bit or larger virtual
   address spaces. The x86 I/O port space is still limited to 65,536 ports.

4. **Memory protection**: The MMU can control access to device registers just
   like it controls access to memory, enabling fine-grained security policies.

5. **Cache coherency**: Modern cache architectures can handle MMIO regions
   appropriately (typically marking them uncacheable), integrating device
   access into the memory subsystem.

The trade-off is that MMIO requires address space allocation and more complex
address decoding, but with 64-bit address spaces now standard, this is no
longer a meaningful constraint.

## MMIO and virtualization

Memory-mapped I/O turns out to be particularly well-suited for virtualization,
which is why virtio and other virtual device interfaces rely on it. But
understanding why requires examining how the hypervisor intercepts guest memory
accesses in the first place.

### How MMIO accesses become VM exits

When a guest virtual machine accesses memory, the address goes through two
levels of translation:

1. **Guest virtual → Guest physical**: The guest's own page tables (controlled
   by the guest OS) translate the virtual address the guest code uses into what
   the guest believes is a physical address.

2. **Guest physical → Host physical**: A second set of page tables, controlled
   by the hypervisor, translates the guest physical address (GPA) into an
   actual host physical address (HPA).

This second level is called Extended Page Tables (EPT) on Intel processors or
Nested Page Tables (NPT) on AMD. These are hardware features specifically
designed for virtualization -- before they existed (pre-2008), hypervisors had
to use expensive "shadow page table" techniques.

Here's where MMIO interception happens: the hypervisor deliberately leaves
certain guest physical address ranges **unmapped** in the EPT/NPT tables. When
the guest tries to access one of these unmapped addresses, the CPU generates an
**EPT violation** (Intel) or **NPT fault** (AMD). This is similar to a page
fault, but it occurs at the second level of translation and has a different
cause -- the guest's own page tables are fine, but the hypervisor's translation
tables say "this address doesn't map to real memory."

This EPT/NPT violation triggers a **VM exit**: the CPU stops executing guest
code, saves the guest's state, and transfers control to the hypervisor. The
exit information includes:

- The guest physical address that was accessed
- Whether it was a read or write
- The size of the access (1, 2, 4, or 8 bytes)
- For writes, the value being written

The hypervisor can then examine this information and emulate the device
behavior. For a read, it provides the value to return. For a write, it performs
whatever action the virtual device should take. Then it resumes the guest.

In KVM, this appears as a `KVM_EXIT_MMIO` exit reason, with the details in a
structure that userspace (like QEMU or our VMM) can process.

### This is not a page fault

It's worth being precise here: an EPT/NPT violation is **not** the same as a
page fault, even though both involve address translation failures:

- A **page fault** (`#PF`) occurs when the guest's own page tables can't
  translate a virtual address, or when permission bits are violated. The guest
  OS handles these itself (loading pages from swap, implementing copy-on-write,
  etc.).

- An **EPT violation** occurs when the guest physical address doesn't have a
  valid mapping in the hypervisor's second-level tables. The guest never sees
  this -- it causes a VM exit to the hypervisor.

The CPU distinguishes these cases in hardware. A page fault stays within the
guest; an EPT violation exits to the host.

### Where do virtual devices live in the address space?

Unlike physical hardware, virtual devices don't have fixed, standardized
addresses burned into silicon. The location of MMIO regions depends on the
transport mechanism and platform:

**virtio-pci (most common on x86):**

virtio-pci devices appear as standard PCI devices with specific vendor and
device IDs (vendor 0x1AF4, Red Hat's PCI vendor ID). Like any PCI device, they
have Base Address Registers (BARs) that the system firmware or OS configures
during PCI enumeration.

The actual addresses are assigned dynamically -- the firmware scans the PCI
bus, discovers the devices, and allocates MMIO regions from available address
space (typically above 4GB on modern systems to avoid the legacy "memory hole").
The guest OS reads the BAR values to discover where each device's registers are
mapped.

This is why virtio-pci "just works" on x86 systems -- it piggybacks on the
existing PCI infrastructure that every PC has.

**virtio-mmio (common on ARM and embedded):**

For platforms without PCI, virtio-mmio provides a simpler transport. Each
device occupies a fixed region of memory (typically 0x200 bytes) at an address
specified in the system's hardware description:

- On ARM systems with Device Trees, the MMIO addresses are specified in the
  `.dts` file that describes the virtual machine's hardware.
- On x86 systems with ACPI (less common for virtio-mmio), the addresses would
  be in ACPI tables.

QEMU, for example, typically places virtio-mmio devices at addresses like
`0x10000000`, `0x10001000`, etc. when emulating ARM systems. The exact addresses
are arbitrary -- what matters is that the hypervisor and guest agree on them.

**virtio-ccw (IBM z/Architecture):**

IBM mainframes use channel I/O rather than MMIO, so there's a third transport
that maps virtio onto the s390's native I/O architecture. This is highly
specialized and only relevant for mainframe virtualization.

### The virtio-mmio register layout

Regardless of where a virtio-mmio device is placed in the address space, its
internal register layout is standardized by the VIRTIO specification. From the
base address, the registers are:

| Offset | Name | Description |
|--------|------|-------------|
| 0x000 | MagicValue | Always 0x74726976 ("virt" in little-endian) |
| 0x004 | Version | VIRTIO version (1 or 2) |
| 0x008 | DeviceID | Type of device (1=net, 2=block, etc.) |
| 0x00C | VendorID | Vendor identifier |
| 0x010 | DeviceFeatures | Feature bits the device offers |
| 0x014 | DeviceFeaturesSel | Which 32-bit feature word to read |
| 0x020 | DriverFeatures | Feature bits the driver accepts |
| ... | ... | ... |
| 0x050 | QueueNotify | Write here to notify device of new buffers |
| 0x070 | Status | Device status bits |

The guest driver reads and writes these registers to negotiate features, set up
virtqueues, and notify the device of pending work. Each register access is an
MMIO operation -- and initially, each one causes a VM exit.

### The performance problem and its solution

You might notice a problem: if every register access causes a VM exit, and VM
exits are expensive (hundreds to thousands of CPU cycles), then device I/O will
be painfully slow. A single disk read might involve:

1. Writing to several registers to set up the request
2. Writing to QueueNotify to tell the device to process it
3. Reading status registers to check completion

That's multiple VM exits per I/O operation, which would destroy performance.

The solution is **ioeventfd** and **irqfd**, kernel mechanisms that allow
certain MMIO accesses to be handled without a full VM exit:

- **ioeventfd**: The hypervisor can register that writes to a specific address
  (like QueueNotify) should signal an eventfd instead of causing a VM exit.
  The KVM kernel module handles this entirely in kernel space -- the guest
  writes to the address, KVM sees it's a registered ioeventfd, signals the
  eventfd, and immediately returns to the guest. No exit to userspace needed.

- **irqfd**: Similarly, the hypervisor can register that writing to an eventfd
  should inject an interrupt into the guest. This allows the device emulation
  thread to notify the guest of completion without forcing a VM exit.

With these optimizations, the steady-state I/O path involves zero VM exits:
the guest writes to QueueNotify (handled by ioeventfd), the VMM processes the
request, and signals completion via irqfd. The guest continues running
throughout.

The expensive MMIO exits only happen during device setup and negotiation, which
occurs once at boot time.

### Why this all matters for virtio

Putting this together, MMIO provides an ideal foundation for virtual devices:

1. **Universal mechanism**: Every architecture supports memory-mapped I/O,
   so virtio works on x86, ARM, MIPS, PowerPC, and others without
   architecture-specific code paths.

2. **Hardware-assisted interception**: EPT/NPT violations provide a clean,
   fast way for the hypervisor to intercept device accesses without modifying
   guest code.

3. **Optimization path**: The ioeventfd/irqfd mechanisms allow the hot path
   to bypass VM exits entirely, achieving near-native I/O performance.

4. **Software-defined devices**: Adding a new device type is purely a software
   change -- define new register semantics, implement the emulation, and
   update the guest driver. No CPU changes, no new instructions, no hardware
   modifications.

This is the real power of MMIO for virtualization: it transforms device
emulation from a hardware problem into a software problem, with the performance
characteristics to make it practical.

## Physical device passthrough

So far we've discussed emulated devices, where MMIO accesses cause VM exits and
the hypervisor simulates hardware behavior in software. But there's another
approach: give the guest direct access to real physical hardware. This is
called **device passthrough** or **direct device assignment**.

The EPT/NPT mechanism works in reverse here. Instead of leaving MMIO regions
unmapped to trap accesses, the hypervisor **maps the physical device's real
MMIO regions** into the guest's address space. When the guest reads or writes
to the device's registers, those accesses go directly to the hardware with no
VM exit, no emulation, and no hypervisor involvement.

### The IOMMU: making passthrough safe

There's an obvious problem with this approach: hardware devices can initiate
DMA (Direct Memory Access) transfers, reading and writing system memory without
CPU involvement. If we give a guest direct access to a device, what stops that
device from DMA'ing into the hypervisor's memory, or another guest's memory?

The answer is the **IOMMU** (I/O Memory Management Unit), called **VT-d** on
Intel systems or **AMD-Vi** on AMD systems. Just as the MMU translates CPU
memory accesses through page tables, the IOMMU translates device-initiated
memory accesses through its own set of tables.

The IOMMU provides address translation for DMA:

```
Device DMA address → IOMMU translation → Physical memory address
```

The hypervisor programs the IOMMU to restrict each passed-through device to
only access memory regions belonging to its assigned guest. If the device (or
malicious guest driver) attempts to DMA to an unauthorized address, the IOMMU
blocks the access and can raise a fault.

This creates a complete isolation picture:

| Access type | Translation mechanism | Controls |
|-------------|----------------------|----------|
| Guest CPU → Memory | EPT/NPT | Hypervisor controls guest physical memory |
| Guest CPU → Device MMIO | EPT/NPT | Hypervisor maps real device into guest |
| Device DMA → Memory | IOMMU | Hypervisor restricts device to guest memory |

### Setting up passthrough on Linux

On Linux, device passthrough uses the **VFIO** (Virtual Function I/O) framework.
The process involves:

1. **Unbind the device from its host driver**: The device must not be in use by
   the host. You remove it from its normal driver (e.g., `nvme`, `i915`) so
   the host kernel stops accessing it.

2. **Bind the device to vfio-pci**: The `vfio-pci` driver is a stub that does
   nothing except make the device available for passthrough. It sets up the
   IOMMU mappings and provides a userspace interface for the hypervisor.

3. **Configure IOMMU groups**: The IOMMU operates on groups of devices that
   share addressing context. All devices in a group must be passed through
   together (or isolated) to maintain security.

4. **Map into the guest**: The hypervisor creates EPT/NPT mappings that point
   the guest's view of the device's MMIO regions to the real physical addresses.
   It also configures interrupt remapping so device interrupts go to the guest.

From this point, the guest can interact with the hardware directly. Writes to
MMIO registers go straight to the device. DMA operations initiated by the
device go through the IOMMU to reach guest memory. Interrupts from the device
get delivered to the guest vCPU.

### What passthrough doesn't give you

Device passthrough provides near-native performance, but comes with trade-offs:

**No live migration**: A guest with a passed-through device is tied to that
physical machine. You can't live-migrate it to another host because the device
doesn't exist there. (Some devices support SR-IOV virtual functions that can be
migrated, but the physical function cannot.)

**Limited sharing**: One physical device goes to one guest. You can't share a
passed-through GPU between multiple VMs like you can with emulated devices.
(Again, SR-IOV changes this equation for supported devices.)

**Host driver unavailable**: While a device is passed through, the host can't
use it. If you pass through your only network card, the host loses network
access.

**IOMMU group constraints**: PCI topology sometimes groups devices together in
ways that force you to pass through more than you intended. A GPU might be
grouped with an audio controller on the same card, requiring both to be
assigned together.

### SR-IOV: virtual functions

Some devices support **SR-IOV** (Single Root I/O Virtualization), a PCI
specification that allows one physical device to present itself as multiple
independent virtual devices.

An SR-IOV-capable network card, for example, might expose:

- One **Physical Function (PF)**: The real device, with full configuration
  capabilities, typically kept by the host
- Many **Virtual Functions (VFs)**: Lightweight device instances with limited
  configuration but full data-path capabilities

Each VF appears as a separate PCI device with its own MMIO regions, and can be
passed through to a different guest independently. The physical device handles
multiplexing internally, often in hardware.

This combines the performance benefits of passthrough with the flexibility of
sharing. It's widely used in cloud environments where high-performance network
and storage I/O is critical.

### Passthrough and MMIO: the connection

The reason passthrough fits naturally into this discussion is that it's the
same MMIO mechanism, just with different EPT/NPT mappings:

| Scenario | EPT/NPT mapping | Result |
|----------|-----------------|--------|
| Emulated device | MMIO region unmapped | VM exit, hypervisor emulates |
| Passthrough device | MMIO region maps to real hardware | Direct hardware access |

The guest code doesn't need to know the difference. It writes to an MMIO
address either way. The hypervisor's choice of EPT/NPT configuration determines
whether that write causes a VM exit for emulation or goes directly to silicon.

## Other types of device passthrough

What about other device types like USB and SATA? Can we attach them directly
to a virtual machine? Do they work the same way as PCI passthrough?

The answer is nuanced: some devices can be passed through using the same
IOMMU/VFIO mechanism, while others use protocol-level redirection that looks
like passthrough but works quite differently.

### The PCI passthrough model

Everything we discussed above -- VFIO, IOMMU isolation, direct MMIO access --
applies to devices that sit on the PCI (or PCIe) bus. This includes:

- **GPUs**: Both integrated and discrete graphics cards
- **Network cards**: High-performance NICs, especially with SR-IOV
- **NVMe drives**: NVMe is a PCI-native protocol, so NVMe SSDs can be passed
  through directly
- **SATA/AHCI controllers**: The controller chip (not individual drives) is a
  PCI device that can be passed through
- **USB controllers**: xHCI, EHCI, and other USB host controllers are PCI
  devices

For these, the guest gets direct hardware access. The guest's driver talks
directly to the device's MMIO registers, and DMA goes through the IOMMU.

### USB device "passthrough" is different

When people talk about "USB passthrough," they usually mean attaching a
specific USB device (a webcam, a hardware token, a USB drive) to a guest VM.
This is **not** the same mechanism as PCI passthrough.

USB device passthrough typically works through **protocol-level redirection**:

1. The host's USB stack enumerates the device and handles the low-level USB
   protocol (packet framing, error recovery, hub management).

2. The hypervisor presents a virtual USB controller (like xHCI) to the guest.

3. USB requests from the guest are forwarded to the real device through the
   host's USB stack, and responses are forwarded back.

```
Guest USB driver → Virtual xHCI → Hypervisor → Host USB stack → Physical device
```

This means:

- The guest never touches the real USB controller's MMIO registers
- The host kernel remains involved in every USB transaction
- There's more latency than true hardware passthrough
- But it's much more flexible -- you can attach/detach devices dynamically

QEMU implements this through its USB redirection code. The guest sees what
looks like a standard USB device attached to a virtual controller, unaware
that the hypervisor is proxying the USB protocol.

**True USB passthrough** is possible by passing through the entire USB
controller (the xHCI or EHCI chip) via VFIO. Then the guest owns all the ports
on that controller and handles USB directly. But this is all-or-nothing: you
can't share a controller between host and guest, and hot-plugging requires
guest OS support rather than hypervisor intervention.

### SATA and block devices

SATA is a storage protocol, not a system bus, so "SATA passthrough" doesn't
quite fit the PCI passthrough model. There are several approaches:

**Controller passthrough**: The SATA/AHCI controller is a PCI device, so you
can pass through the entire controller using VFIO. The guest then sees all
drives attached to that controller and manages them directly. This gives the
best performance but requires dedicating a whole controller.

**Raw block device access**: More commonly, hypervisors provide block-level
access to a disk:

```
Guest block driver → virtio-blk → Hypervisor → Host block layer → SATA/AHCI → Disk
```

The guest uses virtio-blk or virtio-scsi, which is an emulated (or
para-virtualized) device. The hypervisor translates block requests and
forwards them through the host's storage stack. This isn't passthrough --
the guest never touches the SATA controller's MMIO registers -- but it's
often called "raw disk access" because the guest sees the real disk's
contents.

**NVMe is the exception**: Because NVMe is a PCI-native protocol, NVMe drives
can be passed through directly using VFIO. The guest's NVMe driver talks to
the device's MMIO registers and submission/completion queues with no host
involvement in the data path. This is why NVMe passthrough is popular for
high-performance storage in VMs.

### When to use which approach

| Device type | Passthrough method | Guest hardware access? | Use case |
|-------------|-------------------|----------------------|----------|
| GPU | VFIO/PCI | Yes | Gaming VMs, GPU compute |
| NVMe SSD | VFIO/PCI | Yes | High-performance storage |
| NIC | VFIO/PCI or SR-IOV | Yes | Low-latency networking |
| USB controller | VFIO/PCI | Yes | Need all ports, low latency |
| USB device | Protocol redirect | No | Specific device, flexible |
| SATA controller | VFIO/PCI | Yes | All attached drives |
| SATA disk | virtio + raw | No | Single disk, shared controller |

The key insight is that "passthrough" means different things depending on
where the device sits in the system hierarchy. PCI devices can be passed
through with direct MMIO access. Devices behind another controller (USB
devices behind a USB hub, SATA drives behind an AHCI controller) typically
use protocol-level redirection unless you pass through the controller itself.

## Userspace processes as device backends

We've now covered the full spectrum from fully emulated devices (VMM handles
everything) to fully passed-through hardware (guest talks directly to silicon).
But there's an interesting middle ground: what if a userspace process *other
than the VMM* provided a device to the guest?

The question is: can arbitrary host userspace processes participate in device
emulation? And if so, why isn't this more common?

### The technical possibility

There's nothing fundamentally preventing this. The mechanism would work like
this:

1. The VMM sets up the guest with a virtio device at some MMIO address
2. When the guest accesses that device, KVM generates a VM exit
3. Instead of the VMM handling the exit directly, it forwards the request to
   another process
4. That process does the actual work and sends the response back
5. The VMM injects the result into the guest

The challenge is that the VMM process owns the KVM file descriptors. All VM
exits go to whatever process called `KVM_RUN`, which is the VMM. So any other
process that wants to participate in device emulation must coordinate with the
VMM somehow.

### vhost-user: the standardized approach

This pattern actually exists and is widely used. It's called **vhost-user**,
and it's how high-performance networking and storage are often implemented in
production virtualization.

The architecture looks like this:

```
Guest virtio driver
        ↓
    [virtqueues in shared memory]
        ↓
   vhost-user backend process ←──── eventfd notifications
        ↓
   [actual I/O: DPDK, kernel, disk, etc.]
```

The key insight is that virtio's design enables this naturally:

1. **Virtqueues live in shared memory**: The guest and backend share memory
   pages containing the descriptor rings. No copying needed.

2. **Notifications use eventfd**: Instead of MMIO exits going through the VMM,
   ioeventfd delivers notifications directly to the backend process.

3. **Interrupts use irqfd**: The backend can inject interrupts into the guest
   without involving the VMM.

4. **The VMM handles setup only**: The VMM negotiates features, sets up shared
   memory regions, and passes file descriptors to the backend. Then it gets out
   of the data path.

The result is that the steady-state I/O path completely bypasses the VMM. The
guest writes to QueueNotify, KVM signals the backend's eventfd, the backend
processes the request, and signals completion via irqfd. The VMM isn't involved
at all.

### Real-world vhost-user backends

Several production systems use this architecture:

**DPDK (Data Plane Development Kit)**: High-performance networking applications
can run as vhost-user backends, providing virtio-net devices to guests. DPDK
bypasses the kernel's network stack entirely, using userspace drivers to talk
directly to NICs. Combined with vhost-user, this gives guests near-line-rate
networking performance.

**OVS-DPDK (Open vSwitch with DPDK)**: Virtual switches can use vhost-user to
connect to guest VMs. Each guest's virtio-net device is backed by a vhost-user
connection to the switch process, which handles forwarding in userspace.

**SPDK (Storage Performance Development Kit)**: Similar to DPDK but for
storage. SPDK can provide virtio-blk or virtio-scsi backends using userspace
NVMe drivers, bypassing the kernel's block layer entirely.

**virtiofsd**: The virtio-fs daemon provides shared filesystem access between
host and guest. It runs as a separate process from the VMM, using vhost-user
to communicate.

### Why isn't this universal?

If vhost-user allows arbitrary processes to provide devices, why don't we see
more creative uses? Several factors limit its adoption:

**Complexity**: Setting up vhost-user requires careful coordination between the
VMM and backend. The protocol for establishing shared memory, passing file
descriptors, and negotiating features is non-trivial to implement correctly.

**Security**: The backend process needs direct access to guest memory. This is
a significant trust requirement -- a compromised backend could read or corrupt
anything in the guest. You're essentially extending the VMM's trusted computing
base to include the backend.

**Memory management**: The backend must handle the same memory lifecycle as the
VMM: what happens when the guest's memory is remapped, when the VM is migrated,
when the guest changes its memory layout? All of this must be coordinated.

**Limited to virtio**: vhost-user is specifically designed for virtio devices.
If you want to emulate a different device type (say, an Intel e1000 NIC or an
IDE disk controller), you can't use vhost-user -- you need the VMM to handle
the emulation.

**Startup dependencies**: The backend process must be running before the guest
can use the device. This creates operational complexity around process
lifecycle, restart handling, and crash recovery.

### The spectrum of device implementation

We can now see the full picture of how devices can be provided to guests:

| Approach | Who handles I/O? | Performance | Flexibility |
|----------|-----------------|-------------|-------------|
| Full emulation | VMM process | Lowest | Highest |
| vhost-kernel | Host kernel | Medium | Medium |
| vhost-user | Separate userspace process | High | Medium |
| Hardware passthrough | Physical device | Highest | Lowest |

**Full emulation**: The VMM handles every I/O operation. Maximum flexibility
(can emulate any device) but every I/O involves the VMM.

**vhost-kernel**: A kernel module (like vhost-net or vhost-scsi) handles
virtio backends in kernel space. Avoids userspace transitions but still
involves the kernel.

**vhost-user**: A separate userspace process handles the backend. Can use
specialized libraries (DPDK, SPDK) for maximum performance without kernel
involvement.

**Hardware passthrough**: The guest talks directly to real hardware. Maximum
performance but no virtualization flexibility.

### Could this go further?

The vhost-user model suggests an interesting direction: if device backends can
be separate processes, could they be separate *machines*? This is essentially
what projects like **vhost-user-blk over network** or various remote storage
protocols aim to achieve -- the device backend runs on a different physical
host, with the virtqueue protocol tunneled over the network.

This creates a spectrum from local emulation to remote hardware, all unified
by the virtio interface that the guest sees. The guest driver doesn't need to
change; only the backend implementation varies.

### Transparent protocol translation: DMA to RDMA

This abstraction enables something even more interesting: transparent protocol
translation. Consider a guest that thinks it has a normal virtio-net device. It
writes packet data to memory, posts descriptors to a virtqueue, and rings the
doorbell. From the guest's perspective, this is ordinary DMA-style I/O.

But the backend doesn't have to implement this as local networking. It could:

1. Receive the virtqueue notification
2. Read the descriptor and access the guest's packet buffer
3. Instead of sending via the kernel network stack, issue an **RDMA** operation
   to a remote host
4. The remote host receives the data directly into its memory via RDMA
5. Completion comes back, and the backend signals the guest

The guest never knows RDMA is involved. It used a standard virtio-net driver,
wrote to standard virtqueue structures, and got standard completion
interrupts. But under the covers, the data moved via RDMA -- with all the
latency and CPU overhead benefits that implies.

```
Guest sees:                    Backend implements:
┌─────────────────┐           ┌─────────────────────────────────┐
│ virtio-net      │           │ vhost-user backend              │
│ driver          │           │                                 │
│    ↓            │           │ 1. Read virtqueue descriptor    │
│ virtqueue       │ ────────> │ 2. Access guest packet buffer   │
│ doorbell write  │           │ 3. RDMA send to remote host     │
│    ↓            │           │ 4. Wait for RDMA completion     │
│ completion IRQ  │ <──────── │ 5. Signal guest via irqfd       │
└─────────────────┘           └─────────────────────────────────┘
```

This pattern appears in several production systems:

**vhost-user with RDMA backends**: Research projects and commercial systems
have demonstrated vhost-user backends that use RDMA for the actual data
transfer, giving guests near-RDMA performance without RDMA-aware guest drivers.

**NVMe over Fabrics (NVMe-oF)**: While not exactly virtio-based, NVMe-oF
achieves something similar for storage. A guest (or host) issues NVMe commands
to what looks like a local SSD, but the backend translates these to RDMA
operations targeting remote storage. The submitter sees local NVMe semantics;
the data moves via RDMA.

**AWS Nitro and similar offload cards**: Cloud providers use custom hardware
that presents virtio interfaces to guests but implements the backend in
dedicated silicon. These cards can use RDMA or proprietary fabrics to connect
to the cloud provider's storage and network infrastructure. The guest sees
virtio; the card handles protocol translation.

**Mellanox/NVIDIA ConnectX SmartNICs**: These devices can present SR-IOV
virtual functions that speak virtio to the guest, while the physical NIC
handles the actual network protocol -- which might include RoCE (RDMA over
Converged Ethernet).

### Why this matters

This transparent translation has profound implications:

**Guest simplicity**: The guest doesn't need RDMA drivers, InfiniBand stacks,
or specialized paravirtualization. Standard virtio drivers work everywhere,
and the backend decides how to actually move the data.

**Operational flexibility**: The same guest image can run on hosts with
different network fabrics. One host might use standard Ethernet, another might
use RDMA, a third might use a proprietary interconnect. The guest doesn't
change.

**Live migration**: Because the guest only knows about virtio, live migration
becomes simpler. You migrate the guest to a new host, the new host's backend
establishes new RDMA connections to the fabric, and the guest continues
unaware.

**Hardware evolution**: As new network technologies emerge, the backend can
adopt them without touching guest images. The virtio interface provides a
stable contract between guest and host.

### The memory registration challenge

There's a catch with RDMA: it requires **memory registration**. Before RDMA
hardware can transfer data to or from a memory region, that region must be
registered with the RDMA subsystem. This pins the memory (so it can't be
swapped out) and gives the NIC a mapping to access it.

For a vhost-user backend doing RDMA, this means:

1. The backend must register guest memory regions with the RDMA subsystem
2. This requires the guest memory to be pinned (which it typically is for VMs)
3. The RDMA NIC needs to be able to DMA to these regions through the IOMMU
4. Memory layout changes (VM resize, migration) require re-registration

This is why transparent RDMA translation often appears in environments with
dedicated hardware (SmartNICs, DPUs) that can handle the registration
complexity, rather than pure software solutions.

### The convergence of virtualization and fabric

What emerges from all this is a convergence: the clear separation between
"local device" and "network-attached device" blurs when the abstraction layers
work correctly. A guest's disk might be:

- A local file, accessed via virtio-blk through the VMM
- A local NVMe drive, passed through via VFIO
- A remote iSCSI target, accessed via virtio-scsi with a network backend
- A remote NVMe-oF target, accessed via RDMA
- A distributed storage system, accessed via a custom vhost-user backend

The guest doesn't know. It issues block I/O operations to a virtio device.
The infrastructure decides where the data actually lives and how it gets there.

This is, in many ways, the ultimate realization of what memory-mapped I/O
enables: by making device access just "write to an address," we've created an
interface abstract enough that the implementation can be anything from a
register on a chip to a storage array across the planet.

## Conclusion: the device is dead, long live the device

We started this chapter with a historical question: how should a CPU talk to
its peripherals? The answer -- memory-mapped I/O -- seemed like a pragmatic
engineering choice. Map device registers into the address space, let software
read and write them like memory, and avoid the complexity of dedicated I/O
instructions.

But that simple abstraction turns out to have profound consequences for
virtualization. By making device access indistinguishable from memory access,
MMIO created a universal interception point. The hypervisor can trap those
accesses, emulate arbitrary devices, or pass them through to real hardware.
The guest never needs to know which is which.

### What we've learned

The device landscape in modern virtualization is far richer than "emulated
versus real":

- **Emulated devices** let the VMM simulate any hardware, but involve it in
  every I/O operation
- **Kernel-accelerated backends** (vhost-net, vhost-scsi) move virtio
  processing into the kernel, reducing context switches
- **Userspace backends** (vhost-user with DPDK, SPDK) enable specialized,
  high-performance I/O without kernel involvement
- **Hardware passthrough** gives guests direct access to physical devices
  with near-native performance
- **SR-IOV** lets a single device present multiple independent interfaces,
  combining passthrough performance with multi-tenancy
- **SmartNICs and DPUs** offload device emulation to dedicated hardware,
  presenting virtio to guests while implementing complex networking or
  storage protocols
- **Remote backends** extend the model across the network, making the location
  of the "device" irrelevant to the guest

Each layer can evolve independently. A guest running a standard virtio-blk
driver doesn't care whether its storage is a local file, a passed-through
NVMe drive, an iSCSI target, or a distributed storage system accessed via
RDMA. The virtio interface is the contract; everything behind it is
implementation detail.

### An actively evolving space

This isn't a solved problem -- the boundaries continue to shift:

**DPUs and infrastructure processing units** are moving more VMM
functionality into hardware. The hypervisor becomes thinner, with device
emulation, network policy enforcement, and storage processing handled by
dedicated silicon. This changes the trust model: instead of trusting the
host kernel and VMM, you're trusting the DPU firmware.

**CXL (Compute Express Link)** is introducing new kinds of devices that blur
the line between memory and I/O. CXL Type 3 devices appear as additional
memory, but with different performance characteristics and potentially shared
across hosts. How these fit into the virtualization model is still being
worked out.

**Confidential computing** (AMD SEV, Intel TDX, ARM CCA) changes what the
hypervisor can even see. In a confidential VM, the hypervisor can't read
guest memory -- which breaks assumptions underlying vhost-user and other
shared-memory approaches. New device models are emerging that work within
these constraints.

**Disaggregated infrastructure** pushes the boundaries of what "local" means.
When compute, memory, and storage are separate pools connected by high-speed
fabrics, the notion of a device being "attached" to a particular host becomes
fluid. The guest sees a virtio device; the infrastructure decides, moment by
moment, where that device's operations actually execute.

### The abstraction ladder

What makes all of this possible is the layering of abstractions:

1. **MMIO** provides a universal mechanism for the CPU to talk to devices
2. **EPT/NPT** lets the hypervisor intercept and redirect those accesses
3. **virtio** standardizes the interface between guest and virtual device
4. **vhost/vhost-user** decouples the device backend from the VMM process
5. **RDMA and fabric protocols** extend device semantics across the network
6. **Hardware offload** moves virtual device implementation into silicon

Each layer isolates the layers above and below from changes. Guests don't need
to know about fabric protocols. Fabric protocols don't need to know about
guest OS details. The VMM doesn't need to know about specific storage backends.
This separation of concerns is what enables the ecosystem to evolve.

### Why this matters

For anyone building cloud infrastructure, this evolution changes what's
possible:

- You can offer guests near-native I/O performance without giving up
  multi-tenancy or live migration
- You can change your network or storage fabric without touching guest images
- You can implement custom devices for specialized workloads without modifying
  guest kernels
- You can shift workloads between local and remote resources transparently

The humble memory-mapped device register, an idea from the 1970s, turns out
to be the foundation for infrastructure flexibility we're still exploring
today. The device, in its traditional sense of a fixed piece of hardware at
a fixed address, is dead. What's replaced it is something far more powerful:
a programmable interface where the "implementation" can be anything from a
chip on the motherboard to a service running across a global network.

That's not just a conclusion -- it's an invitation. The tools exist to build
device abstractions we haven't imagined yet. The question is: what should
we build?
