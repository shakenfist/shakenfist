---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/more-booting.md
---

# Let's talk more about the boot process for an x86 computer

There is a lot that happens from the moment power is applied to an x86
computer until the fully configured operating system is running and ready to
handle requests from users. This process is broken into stages: Power On Self
Test (POST); first stage bootloading; second stage bootloading; the Linux
kernel loading; configuring device drives and filesystems; and then loading
your userspace configuration. This page attempts to provide an overview of
that entire process, with a focus on the hardware side of the story and only
telling the story to the point where the kernel loads userspace. We'll talk
about the userspace side of that store in a separate installment.

## Hardware power sequencing

Before any software runs, the power supply must stabilize. The PSU monitors its
output voltages and asserts a "Power Good" signal to the motherboard when all
rails are within specification. The chipset holds the CPU in reset until this
signal is received. This typically takes 100-500ms after the power button is
pressed.

## CPU reset vector

When reset is released, the CPU starts execution in **real mode** (16-bit
addressing, 1MB address space) and jumps to a hardcoded address called the
"reset vector": `0xFFFFFFF0`. This is 16 bytes below the 4GB mark. The chipset
maps this address to the platform firmware stored in SPI flash ROM, so the
first instruction executed comes from the firmware image.

## Platform firmware (BIOS or UEFI)

The code at the reset vector is the **platform firmware**, stored in an SPI
flash ROM chip soldered to the motherboard (typically 16-32MB in size). There
are two main types:

* **BIOS** (Basic Input/Output System): The traditional approach dating back to
  the IBM PC. Runs in 16-bit real mode, with implementations largely
  proprietary and based on reverse-engineering the original IBM BIOS.

* **UEFI** (Unified Extensible Firmware Interface): The modern replacement,
  defined by a formal specification from the UEFI Forum. Runs in 32-bit or
  64-bit mode, provides a richer pre-boot environment, and supports features
  like Secure Boot.

Most modern systems ship with UEFI firmware but include a "legacy BIOS"
compatibility mode (CSM - Compatibility Support Module) for booting older
operating systems.

### Firmware vendors

Motherboard manufacturers typically don't write their own firmware. Instead,
they license a base firmware implementation from vendors like AMI (American
Megatrends), Phoenix, or Insyde, then customize it for their specific hardware.

## Security processor initialization

Modern x86 systems include a separate security processor that initializes
*before* the main CPU:

* **Intel Management Engine (ME)**: An embedded processor in the Platform
  Controller Hub (PCH) running its own firmware. It handles security functions,
  remote management (AMT), and parts of the boot process.

* **AMD Platform Security Processor (PSP)**: AMD's equivalent, an ARM
  Cortex-A5 core embedded in the CPU package.

These processors have their own firmware stored in the same SPI flash as the
main BIOS/UEFI, and they begin executing before the main CPU is released from
reset. Their operation is largely opaque and has been a source of security
research and concern.

## CPU microcode update

Very early in the firmware execution, the CPU microcode is updated. Modern x86
CPUs are actually RISC processors internally, with a microcode layer that
translates x86 instructions. This microcode can be patched to fix bugs
discovered after the CPU was manufactured.

The firmware contains microcode update images and loads them into the CPU
during early boot. Operating systems can also load microcode updates later, but
the firmware update happens first and may be required for stable operation.

## Memory training (DRAM initialization)

This is one of the most complex parts of early boot. Modern DDR memory requires
precise calibration of:

* **Signal timing**: Data, address, and control signals must be precisely timed
  relative to the clock. The memory controller runs training algorithms to find
  optimal timing windows.

* **Voltage levels**: Reference voltages and termination settings must be
  calibrated for signal integrity.

* **Per-lane calibration**: Each data lane may need individual adjustment to
  account for trace length differences on the motherboard.

The training process involves writing patterns to memory and reading them back
to verify correctness, adjusting parameters until reliable operation is
achieved. This is why:

* First boot after installing new RAM or clearing CMOS takes longer
* The firmware often caches training results in NVRAM to speed subsequent boots
* Memory overclocking can be unstable if training doesn't find good parameters

The memory initialization code is highly complex and proprietary:

* **Intel Memory Reference Code (MRC)**: Intel provides this as a binary blob
  to firmware vendors.
* **AMD AGESA (AMD Generic Encapsulated Software Architecture)**: AMD's
  equivalent, also provided as binary blobs.

Open source firmware projects like coreboot must reverse-engineer or obtain
these components, which is a significant challenge.

### Server-class memory initialization

Server systems have significantly longer POST times than consumer hardware,
largely due to memory initialization. Several factors contribute:

**More DIMMs and higher complexity**: A consumer motherboard typically has 2-4
DIMM slots. A dual-socket server might have 24-48 slots. Each DIMM requires
individual training, and interactions between DIMMs on the same channel add
complexity. Server systems also use more complex DIMM types:

* **Registered DIMMs (RDIMMs)**: Include a register chip that buffers address
  and command signals, allowing more DIMMs per channel. The register must be
  initialized and calibrated.

* **Load-Reduced DIMMs (LRDIMMs)**: Include a memory buffer chip that buffers
  all signals, allowing even higher capacity configurations. More components
  means more calibration.

* **Higher rank counts**: Server DIMMs are often quad-rank or octal-rank. Each
  rank needs separate timing calibration, and inter-rank timing adds complexity.

**Conservative firmware philosophy**: Server firmware prioritizes reliability
over boot speed. It runs more thorough training algorithms and is less
aggressive about using cached timing data. A consumer board might accept
"good enough" timing; server firmware seeks optimal timing with safety margin.

**Multi-socket coordination**: In multi-CPU systems, memory controllers across
sockets must be coordinated, and NUMA (Non-Uniform Memory Access) topology must
be configured so the operating system knows which memory is local to which CPU.

**ECC overhead**: While ECC (Error Correcting Code) memory doesn't dramatically
slow training itself, servers use ECC during training to validate results more
thoroughly. The 72-bit bus width (64 data + 8 ECC) versus consumer's 64-bit bus
adds a small overhead.

### Memory RAS features

Server systems implement Memory RAS (Reliability, Availability, Serviceability)
features that affect both boot and runtime behavior:

**ECC and error tracking**: Every memory read is checked for errors. Single-bit
errors are silently corrected and logged as Correctable Errors (CEs). Multi-bit
errors that cannot be corrected are Uncorrectable Errors (UEs) and typically
cause a system crash or targeted process termination.

**Patrol scrubbing**: During normal operation, the memory controller
continuously reads and verifies all memory in the background. This catches and
corrects single-bit errors before they can accumulate into uncorrectable
multi-bit errors. This doesn't affect POST time but is part of the overall
memory reliability infrastructure.

**Predictive Failure Analysis (PFA)**: The system maintains per-DIMM CE
counters. When CEs exceed a configurable threshold (often something like 24 CEs
in 24 hours), the DIMM is flagged as potentially failing. This triggers:

1. An alert to management software (logged via IPMI/Redfish)
2. A request for memory retraining on the next boot
3. Potentially marking the DIMM as degraded

The "slower boot" that follows a PFA event is typically an Extended Memory Test
or Memory RAS Recovery pass, where the firmware retrains and more thoroughly
tests the flagged DIMM.

**Why flagged DIMMs are often not actually defective**: PFA thresholds are
deliberately conservative. A DIMM flagged for excessive CEs may be experiencing:

* **Timing drift**: Component characteristics change with temperature and age.
  The original training values may no longer be optimal, but retraining can
  recalibrate successfully.

* **Seating issues**: Slight oxidation or mechanical misalignment causes
  intermittent signal integrity problems. The thermal cycling during a reboot
  can shift contacts enough to resolve the issue.

* **Thermal stress**: A DIMM running at elevated temperatures may have different
  optimal timing than when it was originally trained at cooler temperatures.

* **Transient events**: Cosmic ray bit flips, power supply noise, or EMI can
  cause CEs without indicating hardware failure.

If retraining resolves the CE count, the DIMM wasn't defective - it was
operating outside its calibrated timing window. Genuine hardware failures
typically show CEs concentrated on specific memory addresses (indicating failed
cells), progress to UEs, or fail retraining entirely.

**Memory sparing and mirroring**: High-end servers may support:

* **Rank sparing**: Reserve a memory rank as a hot spare. If a rank shows
  excessive errors, data is migrated to the spare and the failing rank is
  taken offline.

* **Memory mirroring**: Duplicate all data across two memory channels. If one
  channel fails, the system continues on the mirror. This halves usable memory
  capacity but provides fault tolerance.

These features require additional firmware configuration during POST.

## POST (Power-On Self-Test)

POST is a series of diagnostic tests and initialization steps:

1. **CPU self-test**: Verify CPU registers and basic ALU operations
2. **Memory test**: At minimum, verify memory is accessible; optionally run
   pattern tests (often skipped for speed on modern systems)
3. **Chipset initialization**: Configure the PCH/southbridge, set up interrupt
   routing, initialize timers
4. **PCI/PCIe enumeration**: Discover all devices on the PCI buses, assign
   memory and I/O resources
5. **Video initialization**: Find and initialize the display adapter so POST
   messages can be shown
6. **Option ROM execution**: Run firmware embedded in expansion cards (RAID
   controllers, network cards, GPUs) to initialize those devices
7. **USB initialization**: Bring up USB controllers so keyboards work for BIOS
   setup
8. **Storage initialization**: Initialize storage controllers and detect
   connected drives

POST errors are typically indicated by:

* **Beep codes**: Patterns of beeps from the motherboard speaker
* **LED codes**: Diagnostic LEDs on some motherboards
* **POST codes**: Two-digit hex codes shown on debug displays or sent to a
  diagnostic port

## Boot device selection

After POST completes, the firmware determines where to load the bootloader
from:

* Consults the configured boot order (stored in NVRAM)
* For each boot device, checks if it contains a valid bootloader
* In UEFI mode, looks for EFI executables in the EFI System Partition
* In legacy BIOS mode, looks for a valid MBR with boot signature

Once a valid boot device is found, the firmware loads and executes the first
stage bootloader, ending the pre-boot phase.

## Standardization summary

| Component | Standardized? | Notes |
|-----------|---------------|-------|
| CPU reset vector | Yes | Defined by Intel/AMD processor specifications |
| UEFI interface | Yes | UEFI Forum specification |
| Legacy BIOS | No | De facto IBM PC compatibility, no formal spec |
| Memory training | No | Proprietary (Intel MRC, AMD AGESA) |
| POST implementation | No | Vendor-specific |
| Security processors | No | Proprietary Intel ME / AMD PSP firmware |

## First stage bootloader

The bootloader's job is to load the operating system kernel into memory and start it. On Linux systems, this is typically GRUB (GRand Unified Bootloader). GRUB:

1. Presents a menu of operating systems to boot (if configured)
2. Loads the Linux kernel image into memory
3. Loads an initial RAM filesystem (initramfs) containing drivers and tools needed for early boot
4. Passes control to the kernel with command-line parameters

## Kernel initialization

Once the kernel is running, it:

1. Initializes its own data structures
2. Detects and initializes hardware
3. Mounts the root filesystem
4. Starts the init process (PID 1)

The init process (systemd on modern Linux distributions) then starts all the other services and processes that make up a running system.

## Why this matters for virtualization

Virtual machines can boot in several ways:

* **Full emulation**: The VM firmware (like SeaBIOS or OVMF) runs and boots like a physical machine
* **Direct kernel boot**: The hypervisor loads the kernel directly, skipping the firmware and bootloader
* **PVH boot**: A paravirtualized boot protocol that's faster than full emulation but more compatible than direct kernel boot

The User Mode Linux example in the previous chapter used direct kernel boot -- we passed the kernel and root filesystem directly, without any BIOS or bootloader.

## CPUs: cores, sockets, and NUMA

...

## Block devices and disk images

You've probably noticed that we use "disk images" when working with virtual machines. Understanding what these are requires understanding block devices.

### What is a block device?

A block device is the kernel's abstraction for storage hardware. It presents storage as a linear sequence of fixed-size blocks (typically 512 bytes or 4KB) that can be read or written randomly. Hard drives, SSDs, USB sticks, and SD cards are all block devices.

In Linux, block devices appear as files in `/dev`:

* `/dev/sda` -- first SCSI/SATA disk
* `/dev/sda1` -- first partition on that disk
* `/dev/nvme0n1` -- first NVMe drive
* `/dev/nvme0n1p1` -- first partition on that drive

### Disk images

A disk image is simply a file that contains the raw contents of a block device. If you copy every byte from `/dev/sda` into a file, you have a disk image of that drive.

```bash
# Create a raw disk image (don't actually run this without thinking!)
dd if=/dev/sda of=disk.img bs=4M
```

This "raw" format is simple but has downsides -- the image file is as large as the disk, even if the disk is mostly empty.

### QCOW2 and other formats

QEMU's QCOW2 (QEMU Copy-On-Write version 2) format solves several problems:

* **Sparse allocation**: Only stores blocks that have been written to, so a 100GB disk with 10GB of data only takes ~10GB of space
* **Snapshots**: Can store multiple point-in-time snapshots within a single file
* **Compression**: Can compress blocks to save space
* **Encryption**: Can encrypt the disk contents
* **Backing files**: Can use another image as a base, storing only the differences

This is why cloud images are typically distributed in QCOW2 format -- a minimal Debian install might be only 500MB as a QCOW2, versus several gigabytes as a raw image.

!!! info

    The NBD (Network Block Device) protocol we used in the UML example allows you to mount and manipulate disk images without needing a running VM. The `qemu-nbd` command exposes a QCOW2 or raw image as a block device that you can partition, format, and mount normally.

## Interrupts

Interrupts are how hardware gets the CPU's attention. Without interrupts, the CPU would have to constantly check (poll) every device to see if it had data ready. With interrupts, devices can signal the CPU when they need attention.

When an interrupt occurs:

1. The CPU stops what it's currently doing
2. Saves its current state (registers, program counter, etc.)
3. Jumps to an interrupt handler -- a piece of kernel code that deals with this type of interrupt
4. The handler does whatever is needed (reads data from the device, etc.)
5. The CPU restores its state and continues what it was doing

### Hardware vs software interrupts

Hardware interrupts come from external devices -- a keyboard key press, a network packet arriving, a disk completing a read operation.

Software interrupts (also called traps or exceptions) come from the CPU itself -- a division by zero, an invalid memory access, or a deliberate system call instruction.

System calls, which we discussed earlier, are implemented as software interrupts. When a program executes the `syscall` instruction (or `int 0x80` on older systems), it triggers a software interrupt that transfers control to the kernel.

### Why this matters for virtualization

Virtualizing interrupts is one of the trickier parts of building a hypervisor:

* Hardware interrupts need to be delivered to the right VM (or handled by the hypervisor)
* The guest kernel expects to receive and handle interrupts normally
* Timer interrupts need to be virtualized to give each VM the illusion of time passing normally

Hardware virtualization extensions (VT-x, AMD-V) include features specifically for handling interrupts efficiently in virtualized environments.

## Device drivers

A device driver is kernel code that knows how to communicate with a specific piece of hardware. The kernel provides generic interfaces (block devices, network devices, etc.) and drivers translate between those interfaces and the actual hardware.

For example, the kernel's block layer doesn't know anything about SATA commands or NVMe protocols. It just knows how to read and write blocks. The AHCI driver (for SATA) and nvme driver know how to talk to their respective hardware and present it as a generic block device.

### Why drivers run in the kernel

Drivers typically run in kernel mode because they need to:

* Access hardware directly (I/O ports, memory-mapped registers)
* Handle interrupts
* Have low latency (no context switches)

This is also why a buggy driver can crash your entire system -- it's running with full kernel privileges.

### Virtio -- paravirtualized drivers

When running in a virtual machine, emulating real hardware is slow. The hypervisor has to intercept every access to the emulated hardware and simulate what real hardware would do.

Virtio is a standard for paravirtualized devices -- instead of pretending to be real hardware, virtio devices use a simple, efficient protocol designed specifically for virtualization. A virtio network card doesn't emulate an Intel e1000 or Realtek NIC; it uses a straightforward queue-based interface that both the guest driver and hypervisor understand.

This is why you'll see virtio devices recommended for VM performance -- they're not emulating anything, they're using a protocol designed from the ground up for the hypervisor-to-guest communication.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Virtio specification | The [OASIS virtio specification](https://docs.oasis-open.org/virtio/virtio/v1.2/virtio-v1.2.html) describes the standard in detail |
    | Linux device drivers | [Linux Device Drivers, 3rd Edition](https://lwn.net/Kernel/LDD3/) is freely available and still relevant for understanding driver concepts |

## TODO

* BIOS boot

https://www.intel.com/content/www/us/en/content-details/772756/bios-boot-specification.html
https://medium.com/@tunacici7/first-stage-loaders-bios-u-efi-iboot1-u-boot-spl-5c0bee7feb15

◦ UEFI boot
◦ PXE / TFTP boot
◦ IPMI / Redfish / UCS

--8<-- "docs-include/abbreviations.md"
