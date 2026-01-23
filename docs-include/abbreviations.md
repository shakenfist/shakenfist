<!-- Shared abbreviations for cloudgood documentation pages -->
<!-- Include at the bottom of cloudgood files with: --8<-- "docs-include/abbreviations.md" -->
<!-- These definitions are invisible - they enable hover tooltips for the defined terms -->

<!-- CPU and processor terms -->
*[CPU]: Central Processing Unit -- the component of a computer responsible for executing program instructions.
*[MMU]: Memory Management Unit -- hardware that translates virtual addresses to physical addresses.
*[TLB]: Translation Lookaside Buffer -- a cache of recent virtual-to-physical address translations.
*[FPU]: Floating Point Unit -- hardware dedicated to floating-point arithmetic operations.
*[SIMD]: Single Instruction Multiple Data -- parallel processing where one instruction operates on multiple data points.
*[MSR]: Model-Specific Register -- CPU registers that control various processor features and report status.

<!-- Memory terms -->
*[RAM]: Random Access Memory -- fast, volatile storage used by the CPU for active data and instructions.
*[COW]: Copy-On-Write -- a technique where copies share memory until one is modified.
*[DRAM]: Dynamic Random Access Memory -- the main memory technology used in computers.
*[SRAM]: Static Random Access Memory -- faster but more expensive memory used for caches.

<!-- Virtualization - CPU extensions -->
*[VT-x]: Intel Virtualization Technology -- Intel's hardware virtualization extensions for x86 processors.
*[AMD-V]: AMD Virtualization -- AMD's hardware virtualization extensions for x86 processors.
*[VMX]: Virtual Machine Extensions -- Intel's instruction set for hardware virtualization.
*[VMCS]: Virtual Machine Control Structure -- Intel data structure controlling VM behavior.
*[VMCB]: Virtual Machine Control Block -- AMD's equivalent to Intel's VMCS.

<!-- Virtualization - memory -->
*[EPT]: Extended Page Tables -- Intel's hardware support for nested page table translation.
*[NPT]: Nested Page Tables -- AMD's hardware support for nested page table translation.
*[IOMMU]: I/O Memory Management Unit -- hardware that provides address translation for device DMA.
*[VT-d]: Virtualization Technology for Directed I/O -- Intel's IOMMU implementation.
*[AMD-Vi]: AMD I/O Virtualization -- AMD's IOMMU implementation.

<!-- Virtualization - general -->
*[VM]: Virtual Machine -- an emulation of a computer system running on a host.
*[VMM]: Virtual Machine Monitor -- software that creates and manages virtual machines.
*[KVM]: Kernel-based Virtual Machine -- Linux's built-in hypervisor using hardware virtualization.
*[QEMU]: Quick Emulator -- an open source machine emulator and virtualizer.
*[HVM]: Hardware Virtual Machine -- a VM using hardware virtualization extensions.
*[PV]: Paravirtualization -- virtualization where the guest cooperates with the hypervisor.
*[PVHVM]: Paravirtualized Hardware Virtual Machine -- HVM with paravirtualized drivers.
*[UML]: User Mode Linux -- Linux running as a user space process on another Linux kernel.

<!-- Networking -->
*[NIC]: Network Interface Card -- hardware for connecting a computer to a network.
*[OVS]: Open vSwitch -- a multilayer virtual switch for virtualized environments.
*[SR-IOV]: Single Root I/O Virtualization -- hardware support for sharing a device among VMs.
*[DMA]: Direct Memory Access -- hardware capability to access memory without CPU involvement.
*[DPU]: Data Processing Unit -- a programmable processor for infrastructure offload.
*[VPC]: Virtual Private Cloud -- an isolated virtual network within a cloud provider.
*[VXLAN]: Virtual Extensible LAN -- a network virtualization technology for overlay networks.
*[ENA]: Elastic Network Adapter -- AWS's enhanced networking interface.

<!-- Storage -->
*[NVMe]: Non-Volatile Memory Express -- a protocol for accessing SSDs over PCIe.
*[NVMe-oF]: NVMe over Fabrics -- NVMe extended to work over network fabrics.
*[SSD]: Solid State Drive -- storage using flash memory instead of spinning disks.
*[SATA]: Serial ATA -- an interface for connecting storage devices.
*[SCSI]: Small Computer System Interface -- a standard for connecting storage devices.
*[iSCSI]: Internet SCSI -- SCSI commands sent over IP networks.
*[EBS]: Elastic Block Store -- AWS's block storage service for EC2 instances.
*[QCOW2]: QEMU Copy-On-Write version 2 -- a disk image format supporting sparse allocation and snapshots.
*[NBD]: Network Block Device -- a protocol for accessing block devices over a network.

<!-- Boot and firmware -->
*[BIOS]: Basic Input/Output System -- legacy firmware for PC initialization and boot.
*[UEFI]: Unified Extensible Firmware Interface -- modern replacement for BIOS.
*[POST]: Power-On Self Test -- hardware diagnostics run during boot.
*[MBR]: Master Boot Record -- legacy partitioning scheme limited to 2TB disks.
*[GPT]: GUID Partition Table -- modern partitioning scheme supporting larger disks.
*[PXE]: Preboot Execution Environment -- standard for network booting.
*[GRUB]: GRand Unified Bootloader -- the most common Linux bootloader.

<!-- Linux and OS concepts -->
*[PID]: Process ID -- a unique number identifying a running process.
*[UID]: User ID -- a unique number identifying a user account.
*[GID]: Group ID -- a unique number identifying a group.
*[IPC]: Inter-Process Communication -- mechanisms for processes to exchange data.
*[UDS]: Unix Domain Socket -- a socket for communication between processes on the same host.
*[LSM]: Linux Security Module -- kernel framework for security policies like SELinux and AppArmor.
*[VFS]: Virtual File System -- Linux abstraction layer for filesystem operations.
*[initramfs]: Initial RAM Filesystem -- a temporary filesystem loaded at boot before the real root.

<!-- Containers -->
*[LXC]: Linux Containers -- OS-level virtualization using namespaces and cgroups.
*[OCI]: Open Container Initiative -- standards for container formats and runtimes.
*[CRI]: Container Runtime Interface -- Kubernetes interface for container runtimes.

<!-- x86 architecture -->
*[CR3]: Control Register 3 -- x86 register holding the page table base address.
*[CR0]: Control Register 0 -- x86 register controlling processor operating mode.
*[CR4]: Control Register 4 -- x86 register controlling various CPU features.
*[PCID]: Process Context Identifier -- tags TLB entries to survive context switches.
*[KPTI]: Kernel Page Table Isolation -- mitigation for Meltdown separating kernel page tables.
*[IDT]: Interrupt Descriptor Table -- x86 table mapping interrupts to handlers.
*[GDT]: Global Descriptor Table -- x86 table defining memory segments.
*[CPL]: Current Privilege Level -- the processor's current ring level (0-3).

<!-- APIs and interfaces -->
*[API]: Application Programming Interface -- a defined interface for software interaction.
*[REST]: Representational State Transfer -- an architectural style for web APIs.
*[ACPI]: Advanced Configuration and Power Interface -- standard for hardware discovery and power management.

<!-- Miscellaneous -->
*[OSI]: Open Systems Interconnection -- a conceptual model for network communication layers.
*[NMI]: Non-Maskable Interrupt -- a hardware interrupt that cannot be ignored.
*[IRQ]: Interrupt Request -- a hardware signal requesting CPU attention.
*[RTC]: Real-Time Clock -- hardware that keeps track of time even when powered off.
*[PCIe]: PCI Express -- high-speed serial bus for connecting hardware components.
*[FPGA]: Field-Programmable Gate Array -- reconfigurable integrated circuit.
*[ASIC]: Application-Specific Integrated Circuit -- custom chip designed for a specific purpose.
*[TCP/IP]: Transmission Control Protocol/Internet Protocol -- the fundamental protocols of the internet.
