# A slightly unreliable history of virtualization

Virtualization of compute is a very old technology (1). IBM had something which looked a bit like modern virtualization on its mainframe platforms in the 1960s. Depending on your definition (is simulation virtualization?), the x86 platform has had virtualization since at least the mid-1990s. Then again, mainstream virtualization is really a mid-2000s development. Either way, you are grappling with at least 20 years of history when you try to understand the current state of virtualization platforms.
{ .annotate }

1. https://en.wikipedia.org/wiki/Timeline_of_virtualization_technologies has a useful timeline if you're that way inclined.

This document attempts to summarize the relevant parts of that history, while also introducing background as required.

## The mainframe era and the birth of hypervisors

The story of virtualization properly begins with IBM's mainframes in the 1960s, but the important developments happened in the early 1970s.

### CP/CMS and VM/370

In 1972, IBM released VM/370, a virtualization system for its System/370 mainframes. VM/370 grew out of earlier work on CP/CMS (Control Program / Cambridge Monitor System), a research project at IBM's Cambridge Scientific Center. The key insight was simple but powerful: create a thin layer of software -- the **Control Program** -- that presents multiple virtual copies of the underlying hardware, each of which can run its own operating system.

This was the birth of what we now call a **hypervisor** (1). The Control Program sat directly on the hardware (what we'd now call a "Type 1" or "bare metal" hypervisor) and multiplexed the physical machine into several virtual machines, each believing it had exclusive access to a complete System/370.
{ .annotate }

1. The term "hypervisor" comes from the idea that this layer supervises the supervisors (operating systems), hence "hyper" (above) + "visor" (supervisor).

This worked because the System/370 architecture was specifically designed to be virtualizable. The hardware supported multiple privilege levels, and critically, any instruction that could reveal whether you were running on real hardware or in a virtual machine would cause a trap that the Control Program could intercept and handle.

### The Popek and Goldberg requirements

In 1974, Gerald Popek and Robert Goldberg published a seminal paper, "Formal Requirements for Virtualizable Third Generation Architectures" (1), that formalized what it means for a computer architecture to be virtualizable. Their key insight was about **sensitive instructions** -- instructions that behave differently depending on whether the processor is in privileged mode or not.
{ .annotate }

1. Published in Communications of the ACM, [this paper](https://dl.acm.org/doi/10.1145/361011.361073) is still the definitive reference for understanding CPU virtualization requirements.

Popek and Goldberg identified that for an architecture to be efficiently virtualizable:

- All **sensitive instructions** must also be **privileged instructions**
- This means any instruction that could reveal the virtualization or modify critical system state must cause a trap when executed in non-privileged mode

If this property holds, you can run a guest operating system in non-privileged mode. Whenever it tries to do something sensitive (like modify page tables or access hardware), the instruction traps to the hypervisor, which can then emulate the expected behavior. The guest never knows it's not running on real hardware.

The System/370 was designed with this property. The x86 architecture, unfortunately, was not.

### The x86 problem

When Intel designed the 8086 (1978) and later the 80386 (1985, the first 32-bit x86), they weren't thinking about virtualization. The x86 architecture has approximately 17 instructions that are sensitive but not privileged -- they don't trap when executed in user mode, but their behavior reveals information about the true state of the processor.

The classic example is the `POPF` (pop flags) instruction. When executed in kernel mode, it can modify the interrupt flag (IF). When executed in user mode, it silently fails to modify IF rather than trapping. A guest kernel running in user mode would execute `POPF` expecting to disable interrupts, but nothing would happen -- and the instruction wouldn't trap to give the hypervisor a chance to emulate the correct behavior.

Other problematic instructions include `SGDT`/`SIDT` (store global/interrupt descriptor table register), which reveal the true location of system tables, and several segment register operations. These made x86 "non-virtualizable" in the Popek and Goldberg sense.

This is why for decades, the conventional wisdom was that x86 simply couldn't be efficiently virtualized. IBM's mainframes could do it because they were designed for it. Sun's SPARC and other RISC architectures could do it. But x86 was considered a lost cause.

### VMware's binary translation breakthrough

In 1998, a startup called VMware proved the conventional wisdom wrong.

VMware's key innovation was **binary translation**. Instead of trying to run guest code directly on the CPU and trapping on sensitive instructions (which didn't work because x86 sensitive instructions don't trap), VMware dynamically rewrote the guest's kernel code before executing it.

The approach worked like this:

1. Guest user-space code ran directly on the CPU (it doesn't contain sensitive instructions, so this is safe and fast)
2. Guest kernel code was scanned before execution
3. Any sensitive instructions were replaced with calls into VMware's virtualization layer (the VMM, or Virtual Machine Monitor)
4. The translated code was cached for reuse

This wasn't free -- binary translation added overhead, especially for kernel-heavy workloads. But it worked, and it worked well enough to be commercially useful. VMware Workstation shipped in 1999 for running virtual machines on Windows and Linux desktops.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Popek and Goldberg's paper | [Formal Requirements for Virtualizable Third Generation Architectures](https://dl.acm.org/doi/10.1145/361011.361073) (1974) |
    | VMware's approach | [A Comparison of Software and Hardware Techniques for x86 Virtualization](https://www.vmware.com/pdf/asplos235_adams.pdf) - VMware's own comparison |
    | The x86 virtualization hole | [Analysis of the Intel Pentium's Ability to Support a Secure Virtual Machine Monitor](https://www.usenix.org/legacy/events/sec2000/full_papers/robin/robin.pdf) - details the problematic instructions |

### The state of virtualization in 1999

So by 1999, when User Mode Linux appeared, the virtualization landscape looked like this:

- **Mainframes**: IBM's VM/370 and its successors had been doing efficient hardware virtualization for nearly 30 years
- **VMware**: Just released their first product, using binary translation to work around x86's limitations
- **Everything else**: Mostly full software emulation (slow) or nothing at all

VMware's server products (GSX in 2001, ESX in 2001) were still in the future. Xen (2003) and hardware virtualization support from Intel (VT-x in 2005) and AMD (AMD-V in 2006) were years away.

It was in this context that Jeff Dike had a different idea: what if instead of trying to virtualize hardware, you virtualized the operating system interface instead?

## User Mode Linux

!!! puzzle "Things snap together"

    *Now we can combine a few of the concepts we've discussed in [the fundamentals chapter](/components/cloudgood/fundamentals/) into a larger quite interesting idea. When we encounter one of these "things snap together" moments, we'll use this puzzle piece icon.*

In this case, we are now at the point where we have the pieces to build possibly the first example of virtualization on Linux. That is, what if we ported Linux to a new architecture where that architecture was in fact Linux system calls instead of microprocessor instructions? That is, can we run Linux as a user space program on Linux?

This in fact exists. In 1999 Jeff Dike started a port of Linux to user space called User Mode Linux (UML). At the time this was a relatively revolutionary idea, and entire companies were built upon it -- for example many of the early virtual private server hosting providers like Linode started as UML hosting platforms. In terms of context, at the time the first version of VMware had only been released the year before and was intended for workstations. The first server version of VMware (VMware GSX) wouldn't be released until 2001. Xen wasn't released until 2003, but we'll talk more about Xen later.

Because UML is a user space program, you can run it as a non-root user and it is just another program that you download. To run a simple UML instance on Debian 12, you do this:

```bash
$ sudo apt-get install user-mode-linux uml-utilities
...
$ linux.uml
```

No really, that's it. Now, that UML virtual machine wont boot because it lacks a root filesystem. Instead, you'll see something like this (heavily truncated) output:

```
$ linux.uml
...
Linux version 6.1.82 (buildd@x86-conova-01) (gcc (Debian 12.2.0-14) 12.2.0, GNU ld (GNU Binutils for Debian) 2.40) #1 Fri Mar 29 18:07:35 UTC 2024
Zone ranges:
  Normal   [mem 0x0000000000000000-0x0000000096731fff]
Movable zone start for each node
Early memory node ranges
  node   0: [mem 0x0000000000000000-0x0000000036731fff]
Initmem setup node 0 [mem 0x0000000000000000-0x0000000036731fff]
Built 1 zonelists, mobility grouping on.  Total pages: 219541
Kernel command line: root=98:0 console=tty0
...
Console initialized on /dev/tty0
printk: console [tty0] enabled
Initializing software serial port version 1
printk: console [mc-1] enabled
...
VFS: Cannot open root device "98:0" or unknown-block(98,0): error -6
Please append a correct "root=" boot option; here are the available partitions:
Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(98,0)
CPU: 0 PID: 1 Comm: swapper Not tainted 6.1.82 #1
Stack:
 60688a8e 607daa3c 93443cf0 60670226
 607b70c1 607daa3c 6004603f 00008001
 00000000 9454b000 93443d20 60693645
Call Trace:
 [<60688a8e>] ? _printk+0x0/0x94
 [<6002d380>] show_stack+0x113/0x18b
...
Aborted
```

Here the Linux kernel panicked because it could not find its root filesystem and therefore could not continue booting. Note that a kernel panic in UML results in a user space process terminating, instead of the bare metal machine halting. Note as well that there was no boot loader in this output, as opposed to what you are used to seeing in boot messages on physical machines. This is because the UML architecture does not require any pre-kernel setup before starting the kernel.

Specifically, this section of the output above is the error:

```
VFS: Cannot open root device "98:0" or unknown-block(98,0): error -6
Please append a correct "root=" boot option; here are the available partitions:
Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(98,0)
```

!!! foreshadow "Brace for learning"

    Whenever I spend more time than seems reasonable on a concept which seems tangential to the actual topic, we'll use this light bulb inserted into someone's head. In general, this is happening because this concept is important later, but it might also be that I am simply on a power trip.

Obviously, we should provide a filesystem for the UML kernel to load. Most Linux distributions these days offer pre-baked cloud images in qcow2 format, but those have a kernel and kernel modules which expect to be running on virtualized hardware and in the UML case we're bringing our own kernel for an architecture which is not one of the ones modern Linux distributions build cloud images for. So instead, we really want a root filesystem which doesn't have general cloud cruft and we're going to have to build it for ourselves.

We should also note that this pattern where the kernel is separate from the root filesystem is unusual today but was common in virtualization until about ten years ago. We moved back to all-in-one disk images as part of the movement to KVM based virtualization from a Xen paravirtualization dominated landscape. We'll talk more about paravirtualization when we get to discussing Xen.

For Debian-derived Linux distributions, the way we'd build such a filesystem is with the "debootstrap" command. This command requires a running Linux machine but does not require that the machine itself be running Debian. This works by creating a chroot (1) and then fetching and installing the various components of the operating system:
{ .annotate }

1. "chroot" is the name of a Linux system call, and is short for "change root". It changes what a given process believes to be the root of the filesystem from / to whatever path you specify. That is, the process can't see things outside of that subpath from then on. It was commonly used as a security mechanism 20 years ago, but is generally considered to be not particularly hard to escape from these days. The concept of isolating a process into a filesystem subtree lives on in containers, though modern container runtimes use the more secure `pivot_root` system call instead.

```bash
$ sudo apt-get install -y debootstrap
...
Unpacking debootstrap (1.0.128+nmu2+deb12u2) ...
Setting up debootstrap (1.0.128+nmu2+deb12u2) ...
$ mkdir debian_unstable
$ sudo debootstrap sid debian_unstable http://deb.debian.org/debian
...quite a lot of output...
$ chroot debian_unstable /bin/bash
root@uml:/# passwd
New password: ...
Retype new password: ...
passwd: password updated successfully
root@uml:/# cd dev
root@uml:/dev# mknod --mode=660 ubd0 b 98 0
mknod: cannot set permissions of 'ubd0': Operation not supported
root@uml:/dev# chown root:disk ubd0
root@uml:/# exit
```

This produces a Debian filesystem in the directory debian_unstable. However, UML requires the filesystem to be present as a block device image, which makes sense when you consider that this is effectively what a hard disk is when attached to a physical machine. We can convert our new Debian unstable filesystem into a raw block device image like this:

```bash
# qemu-img create -f raw debian_unstable.raw 1G
Formatting 'debian_unstable.raw', fmt=raw size=1073741824
# modprobe nbd max_part=8
# qemu-nbd --connect=/dev/nbd0 debian_unstable.raw -f raw
# mkfs.ext4 /dev/nbd0
mke2fs 1.47.0 (5-Feb-2023)
Discarding device blocks: done
Creating filesystem with 262144 4k blocks and 65536 inodes
Filesystem UUID: 931b4dbe-a270-4845-8088-dbf660be7f22
Superblock backups stored on blocks:
    32768, 98304, 163840, 229376
Allocating group tables:
Writing inode tables: done
Creating journal (8192 blocks): done
Writing superblocks and filesystem accounting information: done
# mount /dev/nbd0 /mnt
# cp -Rp debian_unstable/* /mnt/
# umount /mnt
# qemu-nbd --disconnect /dev/nbd0
/dev/nbd0 disconnected
```

This set of commands heavily relies of qemu's tools to create the image, but we'll discuss qemu in more detail later.

I guess I should also note that we could have created this virtual disk first, made a filesystem on it, mounted it, and then run debootstrap inside that new filesystem. This would have the same effect but provides a slightly less clear story as to why we are doing these things. Another issue I had while preparing this demo is that UML dates back to those simpler days of init scripts, and I cannot find any prior art of getting systemd working well. I am sure it's possible, but it was a tangent from what I am trying to talk about here that I did not follow. Therefore, we're just going to set init to being a shell to prove that the kernel boots and starts user space. I should note this was a common technique back in the day. Similarly, containers today often run a single application process directly as PID 1 rather than using a traditional init system.

???+ info

    PID 1? What's PID 1? We'll talk more about that in [the next chapter](/components/cloudgood/more-fundamentals/).

Regardless, we can now boot a working UML virtual machine:

```
# linux.uml mem=128M ubda=debian_unstable.raw init=/bin/bash
...a really huge amount of output...
printk: console [mc-1] enabled
registered taskstats version 1
zswap: loaded using pool lzo/zbud
Btrfs loaded, crc32c=crc32c-generic, zoned=no, fsverity=no
EXT4-fs (ubda): mounted filesystem with ordered data mode. Quota mode: none.
VFS: Mounted root (ext4 filesystem) readonly on device 98:0.
devtmpfs: mounted
This architecture does not have kernel memory protection.
Run /bin/bash as init process
bash: cannot set terminal process group (-1): Inappropriate ioctl for device
bash: no job control in this shell
root@(none):/# uptime
uptime: Cannot get system uptime: No such file or directory
root@(none):/# whoami
root
root@(none):/# ls
bin  boot  dev  etc  home  lib  lib64  lost+found  media  mnt  opt  proc  root  run  sbin  srv  sys  tmp  usr  var
root@(none):/# exit
exit
Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000100
```

Ultimately UML was an evolutionary dead end -- users want more performance than UML can offer, and they want to be able to reuse software compiled against a real architecture not the synthetic UML architecture. However, it is the conceptual basis for what came next, so it deserved some time.

## Microprocessor simulators

UML doesn't simulate a real processor architecture, so while its useful for debugging shared components of the Linux kernel, it is not useful for debugging the kernel that runs on a real processor. Microprocessor simulators (sometimes called emulators) pre-date UML and are still common today.

Bochs is an example of an older x86 simulator, having been first released in 1994, but the reality is that pretty much everyone uses the qemu project now. One of the reasons for this is that Bochs only supports Intel 32 and 64 bit architectures, whereas qemu supports a much larger number of machines. These packages are capable of emulating entire machines, not just the CPU.

### How full emulation works

In full emulation mode, the emulator simulates every aspect of the target hardware in software. When the guest operating system executes a CPU instruction, the emulator:

1. Fetches and decodes the instruction (just like a real CPU would)
2. Simulates the effect of that instruction on virtual registers, virtual memory, and virtual devices
3. Updates its internal state to reflect what a real CPU would have done
4. Moves on to the next instruction

This applies to everything -- not just CPU instructions, but also memory accesses, interrupt handling, timer ticks, and I/O operations. When the guest writes to what it thinks is a network card's registers, the emulator intercepts that write, figures out what the guest was trying to do, and simulates the network card's response.

The advantage of this approach is complete isolation and flexibility. You can run an ARM operating system on an x86 host, or vice versa. You can pause execution at any point and inspect the complete machine state. You can even simulate hardware that doesn't physically exist yet, which is valuable for hardware designers.

### The performance cost

The disadvantage is performance. Consider what happens when the guest executes a single instruction like `add eax, ebx` (add two registers):

- On real hardware, this takes perhaps 1 CPU cycle
- In an emulator, the host must: fetch the instruction bytes, decode them to understand what operation is requested, look up the virtual register values, perform the addition, store the result back, update virtual flags, and advance the virtual program counter

This might take dozens or hundreds of host CPU cycles for a single guest instruction. The ratio varies depending on the instruction -- simple arithmetic might be 10-50x slower, while complex operations involving memory or I/O can be 100-1000x slower.

In practice, pure software emulation typically runs at 1-10% of native speed, sometimes less. This is acceptable for debugging, testing, or running software that isn't performance-sensitive, but it's far too slow for production workloads. A virtual machine running at 5% of native speed would make even basic operations feel painfully sluggish.

This performance gap is what drove the development of hardware-assisted virtualization, which we'll discuss shortly. But first, let's look at what hardware qemu actually emulates.

Even qemu doesn't support every possible x86 chipset -- it supports just two: i440fx, and q35. Most OpenStack workloads are using i440fx by default. The differences between the two are:

| Attribute | i440fx | q35 |
|-----------|--------|-----|
| Hardware release | 1996 | 2007 |
| Chipset | ISA and PIIX4 | Northbridge: MCH, Southbridge: ICH9 |
| PCI | Yes | |
| PCI-e | | Yes |
| USB | Yes | Yes |
| PATA / IDE | Yes | |
| SATA | | Yes |
| BIOS / UEFI | BIOS | BIOS or UEFI with optional secure boot |

In general cloud platforms will use i440fx unless you need UEFI or secure boot. However, it should be noted that these hardware platforms are quite old and therefore stand out as virtual machines to software which is doing platform identification things. This makes virtual machines much less useful for things like malware analysis, but that's a story for another day.

## Hardware-assisted virtualization

As we discussed above, pure software emulation is slow -- typically running at 1-10% of native speed. This is fine for debugging or running legacy software, but completely impractical for production workloads. The solution came from the CPU vendors themselves.

### The x86 virtualization problem

Before hardware support, virtualizing x86 was particularly tricky. The x86 architecture has certain "sensitive" instructions that behave differently depending on the CPU's privilege level, but don't trap when executed in user mode. In a properly virtualizable architecture, any instruction that could reveal or affect the machine's true state would cause a trap, allowing the hypervisor to intercept and emulate it. x86 didn't work this way.

Early x86 hypervisors like VMware used clever techniques to work around this. They would scan the guest's code before execution and rewrite problematic instructions to trap into the hypervisor -- a technique called "binary translation". This worked, but added complexity and overhead.

### Intel VT-x and AMD-V

In 2005-2006, both Intel and AMD released CPU extensions specifically designed for virtualization:

- **Intel VT-x** (codenamed "Vanderpool") - first appeared in Pentium 4 processors in late 2005
- **AMD-V** (codenamed "Pacifica", later marketed as AMD Virtualization) - appeared in Athlon 64 processors in 2006

These extensions add a new CPU mode specifically for running hypervisors. The key concepts are:

**VMX root and non-root modes** (Intel terminology): The hypervisor runs in "root mode" with full control, while guest operating systems run in "non-root mode". The guest thinks it's running at ring 0 with full privileges, but the CPU knows it's actually a guest and will trap to the hypervisor when necessary.

**VMCS/VMCB**: The Virtual Machine Control Structure (Intel) or Virtual Machine Control Block (AMD) is a data structure that defines the guest's state and controls what operations cause exits to the hypervisor. The hypervisor configures this to trap on specific events -- like I/O operations, certain interrupts, or access to specific registers.

**VM entry and VM exit**: When the hypervisor wants to run guest code, it performs a "VM entry" which loads the guest state and switches to non-root mode. When something happens that the hypervisor needs to handle, a "VM exit" occurs, saving the guest state and returning control to the hypervisor.

This means the guest's code runs directly on the CPU at near-native speed for most operations. The hypervisor only gets involved when something interesting happens -- an I/O operation, a page fault, a timer interrupt, etc.

### Extended Page Tables (EPT/NPT)

The original VT-x and AMD-V implementations still had a significant overhead for memory operations. Every memory access in a virtual machine involves two levels of address translation:

1. Guest virtual address → Guest physical address (using the guest's page tables)
2. Guest physical address → Host physical address (using the hypervisor's shadow page tables)

Originally, the hypervisor had to maintain "shadow page tables" that combined both translations, and trap on every guest page table modification to keep them synchronized. This was expensive.

Intel's Extended Page Tables (EPT, introduced with Nehalem in 2008) and AMD's Nested Page Tables (NPT, introduced with Barcelona in 2007) solve this by doing both translations in hardware. The CPU walks both sets of page tables automatically, eliminating most of the hypervisor's involvement in memory management.

### KVM: The Linux approach

With hardware virtualization support widely available, Linux gained its own hypervisor: KVM (Kernel-based Virtual Machine), first merged in 2007. KVM takes an elegant approach -- instead of being a separate hypervisor, it turns the Linux kernel itself into a hypervisor.

KVM is implemented as a kernel module that exposes the hardware virtualization features through a simple interface. User-space programs (typically QEMU) use this interface to create and run virtual machines. The division of labor is:

- **KVM** (kernel): Handles VM entry/exit, memory management, interrupt injection
- **QEMU** (user space): Provides device emulation, disk and network I/O, VNC/SPICE console

This means QEMU, which we discussed earlier as a software emulator, can also work as a frontend for hardware-accelerated virtualization. When you run `qemu-system-x86_64` on a Linux system with KVM available, it uses hardware virtualization for the CPU and memory, falling back to emulation only for devices.

The partnership between QEMU and KVM wasn't always so tight. QEMU was started by Fabrice Bellard in 2003 as a pure software emulator. KVM was developed separately by Qumranet (later acquired by Red Hat) and merged into Linux in 2007. Initially, KVM had its own simple user-space tool, but the QEMU developers quickly added KVM support, and the combination proved more capable than either project alone. By around 2010, "QEMU/KVM" had become the standard way to refer to Linux virtualization, and the projects have been closely coordinated ever since.

## Paravirtualization

Hardware-assisted virtualization solved the CPU performance problem, but there's another approach that predates it and remains relevant today: paravirtualization.

### The concept

In paravirtualization, instead of pretending to be real hardware (which requires trapping and emulating hardware operations), the hypervisor exposes an explicitly virtual interface. The guest operating system knows it's running in a virtual machine and cooperates with the hypervisor using a purpose-designed API.

Think of it as the difference between a flight simulator that perfectly replicates every switch and dial of a real cockpit versus one that presents a simplified interface designed for efficient simulation. The latter isn't "realistic," but it might be more practical.

### Xen: The paravirtualization pioneer

Xen, first released in 2003, pioneered paravirtualization on x86. A paravirtualized Xen guest (called a "PV guest") runs a modified kernel that:

- Uses "hypercalls" instead of privileged instructions to communicate with the hypervisor
- Knows its page tables are managed cooperatively with the hypervisor
- Uses paravirtualized drivers for disk and network I/O

This required modifying the guest kernel, which meant you couldn't run unmodified operating systems. However, the performance was excellent -- often within a few percent of native speed, even before hardware virtualization support existed.

When VT-x and AMD-V arrived, Xen added support for "HVM" (Hardware Virtual Machine) guests that could run unmodified operating systems. Modern Xen supports a hybrid "PVHVM" mode that uses hardware virtualization for the CPU but paravirtualized drivers for I/O.

### Virtio: Paravirtualization for devices

Even with hardware-assisted CPU virtualization, device emulation remains a bottleneck. Emulating a real Intel e1000 network card means the hypervisor must intercept every register access, simulate the hardware's behavior, and translate between the guest's I/O operations and actual network traffic. This is slow.

Virtio, developed for use with KVM (but now used by other hypervisors too), takes the paravirtualization approach for devices. Instead of emulating real hardware, virtio defines a simple, efficient interface specifically designed for hypervisor-to-guest communication:

- **virtio-net**: Paravirtualized network card
- **virtio-blk**: Paravirtualized block device (disk)
- **virtio-scsi**: Paravirtualized SCSI controller
- **virtio-gpu**: Paravirtualized graphics
- **virtio-fs**: Paravirtualized filesystem sharing

The virtio interface uses shared memory queues ("virtqueues") for communication. The guest places requests in a queue, notifies the hypervisor with a single write, and the hypervisor processes them and places responses in a return queue. This is far more efficient than emulating hundreds of register accesses for a single I/O operation.

The trade-off is that the guest must have virtio drivers. Linux has had virtio support since 2008, and Windows virtio drivers are available (though not included by default). When you provision a cloud VM, you're almost certainly using virtio devices even if you don't realize it.

### Paravirtualization today

You'll still see the term "paravirtualization" in older documentation, particularly around AWS EC2's older instance types and Xen-based platforms. However, the term has fallen somewhat out of fashion -- we now just talk about "virtio drivers" without necessarily invoking the paravirtualization concept.

The pattern remains important though: rather than perfectly emulating legacy hardware, design an interface optimized for the virtual environment. This principle extends beyond virtualization to containers (where the "hardware" is the kernel's syscall interface) and even to some modern hardware designs.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Intel VT-x | [Intel's virtualization technology documentation](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html) (Vol. 3, Chapter 23+) is comprehensive but dense |
    | KVM internals | [This LWN article series](https://lwn.net/Articles/658511/) covers KVM's architecture |
    | Virtio specification | The [OASIS virtio specification](https://docs.oasis-open.org/virtio/virtio/v1.2/virtio-v1.2.html) describes the standard in detail |
    | Xen architecture | The [Xen Project wiki](https://wiki.xenproject.org/wiki/Xen_Project_Software_Overview) has good architectural overviews |
    | Paravirtualization explained | [This LAC2016 talk on Hyper-V](https://www.youtube.com/watch?v=wFAB0pMMk20) is a good description of paravirtualization and Hyper-V "enlightenments" |

## MicroVMs

We've discussed how QEMU emulates specific Intel chipsets (i440fx or q35) to maintain compatibility with existing operating systems. This is powerful -- you can run an unmodified Windows installation from 2005 if you want -- but it comes with costs. QEMU must initialize all those emulated devices, the guest BIOS must probe them, and the guest kernel must detect and initialize drivers for them. Even with virtio devices doing the real work, there's still overhead from the legacy compatibility layer.

MicroVMs take a radically different approach: what if we didn't pretend to be a legacy Intel PC at all?

### The Firecracker approach

Firecracker, developed by Amazon and released as open source in 2018, is the most prominent example of the microVM concept. It powers AWS Lambda (serverless functions) and AWS Fargate (serverless containers).

Firecracker is not a fork of QEMU -- it's a clean-room implementation of a minimal Virtual Machine Monitor (VMM) written in Rust. It uses KVM for hardware virtualization but provides an extremely limited device model:

- One virtio-net device (network)
- One virtio-block device (disk)
- A serial console
- A minimal "i8042" keyboard controller (just enough to handle reboot requests)
- An RTC (real-time clock)

That's it. No emulated IDE controllers, no emulated SATA, no emulated graphics, no PCI bus, no ACPI (1), no USB, no legacy anything. The guest kernel must be configured to work with this minimal environment -- you can't boot an arbitrary Linux distribution ISO.
{ .annotate }

1. ACPI (Advanced Configuration and Power Interface) is how modern operating systems discover hardware, manage power states, and handle events like pressing the power button. Firecracker's lack of ACPI means guests must be specifically built to work without it.

### The trade-offs

This minimalism has significant benefits:

**Boot time**: A Firecracker microVM can boot a Linux kernel in around 125 milliseconds. Compare this to a traditional QEMU VM which might take 2-5 seconds even with optimization. For serverless workloads where you're starting VMs in response to incoming requests, this difference is critical.

**Memory overhead**: Firecracker's VMM process uses about 5MB of memory. QEMU's process can easily use 30-50MB or more before you even count the guest's memory. When running thousands of microVMs on a single host (as AWS does), this adds up.

**Attack surface**: Less code means fewer potential security vulnerabilities. Firecracker is around 50,000 lines of code; QEMU is over 2 million. For multi-tenant environments where isolation is paramount, this matters.

**Startup overhead**: No complex device initialization, no BIOS/UEFI boot process, no GRUB. Firecracker loads the kernel directly into memory and starts executing it.

The costs are flexibility:

**Guest requirements**: The guest kernel must be built with the right drivers (virtio) and without dependencies on hardware that doesn't exist. You can't just download a cloud image and boot it.

**No graphical output**: There's no emulated graphics card. This is fine for server workloads but means you can't run desktop applications.

**Limited device support**: Need to pass through a GPU? Attach a USB device? Use SR-IOV networking? These aren't supported.

**Linux-centric**: While technically you could run other operating systems, Firecracker is really designed for Linux guests. Windows support is not a priority.

### Cloud Hypervisor and alternatives

Firecracker isn't alone in this space. Cloud Hypervisor, started by Intel and now a Linux Foundation project, takes a similar approach with some different trade-offs:

- Written in Rust (like Firecracker)
- Supports more devices (including PCI, VFIO for device passthrough, and vhost-user)
- Can boot from UEFI (using the rust-hypervisor-firmware or OVMF)
- Supports hotplug of CPUs, memory, and devices
- Generally targets a slightly broader set of use cases than Firecracker

QEMU itself has a "microvm" machine type that strips down the device model significantly, giving you some of the microVM benefits while staying within the QEMU ecosystem.

### When to use what

The choice between traditional VMs, microVMs, and containers depends on your requirements:

| Workload | Best fit | Why |
|----------|----------|-----|
| Running arbitrary guest OSes | Traditional VM (QEMU/KVM) | Full hardware emulation for compatibility |
| Long-running server workloads | Traditional VM or microVM | Boot time less critical, flexibility may matter |
| Serverless functions | MicroVM (Firecracker) | Sub-second boot, strong isolation, high density |
| Container isolation | MicroVM | Lighter than traditional VM, stronger isolation than containers |
| Desktop virtualization | Traditional VM | Need graphics, USB, broad hardware support |
| Development/testing | Traditional VM | Flexibility trumps performance |

The microVM approach represents a broader trend in systems design: rather than maintaining backwards compatibility with decades of legacy hardware, design purpose-built solutions for specific use cases. You lose generality but gain significant performance and simplicity.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Firecracker | The [Firecracker documentation](https://github.com/firecracker-microvm/firecracker/blob/main/docs/getting-started.md) is a good starting point |
    | Firecracker design | The [Firecracker design document](https://github.com/firecracker-microvm/firecracker/blob/main/docs/design.md) explains the architectural decisions |
    | Cloud Hypervisor | The [Cloud Hypervisor project](https://www.cloudhypervisor.org/) homepage and documentation |
    | AWS Lambda's use of Firecracker | [This AWS blog post](https://aws.amazon.com/blogs/aws/firecracker-lightweight-virtualization-for-serverless-applications/) announces Firecracker and explains why it was built |

## Hardware offloading: AWS Nitro

MicroVMs reduce overhead by minimizing the software hypervisor. AWS Nitro takes a different approach: offload the hypervisor's work to dedicated hardware.

### The traditional hypervisor problem

In a traditional virtualization setup, the hypervisor software running on the main CPU handles:

- CPU virtualization (with hardware assist from VT-x/AMD-V)
- Memory management and page table handling
- Network I/O (even with virtio, the hypervisor processes packets)
- Storage I/O (reading and writing to disk images)
- Security monitoring and isolation enforcement

Even with efficient implementations, all of this work competes with guest workloads for CPU cycles. The hypervisor needs CPU time to process network packets, handle storage requests, and manage VM state. This creates "noisy neighbor" problems where one VM's I/O can affect another's performance, and means customers aren't getting 100% of the CPU they're paying for.

### The Nitro approach

AWS Nitro, introduced starting around 2017 and fully deployed by 2018, moves almost all of this work off the main CPU and onto dedicated hardware:

**Nitro Cards**: Custom PCIe cards that handle specific functions:

- *Nitro Card for VPC*: Handles all network I/O, including VPC networking, security groups, and Elastic Network Adapters (ENAs). Network packets flow directly between the Nitro card and guest memory via SR-IOV, never touching the hypervisor.

- *Nitro Card for EBS*: Handles Elastic Block Store (remote storage) I/O. Storage requests go directly to the Nitro card, which handles encryption, network transport to EBS servers, and data integrity -- all without hypervisor involvement.

- *Nitro Card for Instance Storage*: Manages local NVMe storage, including encryption.

**Nitro Security Chip**: A dedicated chip on the motherboard that provides:

- Hardware root of trust
- Secure boot verification
- Continuous monitoring of firmware and hardware
- Protection against physical attacks

**Nitro Hypervisor**: What's left of the hypervisor is minimal -- it's a stripped-down, KVM-based system that handles only CPU and memory virtualization. Because network and storage are offloaded, the Nitro Hypervisor has very little to do during normal operation.

### The benefits

**Near-bare-metal performance**: With I/O offloaded to hardware, guests get nearly 100% of the CPU they're paying for. AWS claims the Nitro Hypervisor itself consumes negligible resources.

**Improved security**: The hypervisor is minimal and doesn't need to process network traffic or storage I/O, reducing attack surface. The Nitro Security Chip provides hardware-backed isolation that software can't achieve.

**Consistent performance**: Because I/O doesn't compete with compute for CPU cycles, performance is more predictable. The "noisy neighbor" problem is significantly reduced.

**New instance types**: Nitro enabled instance types that weren't previously possible, including bare-metal instances (where customers get direct hardware access with no hypervisor overhead at all) and instances with very high network bandwidth (100 Gbps+).

### The trade-offs

Nitro is not something you can deploy in your own data center -- it's proprietary AWS hardware. The concepts, however, are worth understanding:

**Hardware cost**: Building custom silicon is expensive. This only makes sense at AWS's scale where the per-unit cost is amortized across millions of instances.

**Flexibility vs. optimization**: Nitro is optimized for AWS's specific use cases. A general-purpose hypervisor like QEMU/KVM can do things Nitro can't, but with more overhead.

**Vendor lock-in**: Workloads optimized for Nitro's performance characteristics may behave differently on other cloud providers or on-premises infrastructure.

### Comparison of approaches

| Approach | How it reduces overhead | Trade-offs |
|----------|------------------------|------------|
| Hardware-assisted virtualization (VT-x) | CPU handles VM traps efficiently | Still need software for devices |
| Paravirtualization (virtio) | Efficient software protocol | Need guest drivers |
| MicroVMs (Firecracker) | Minimal software device model | Limited flexibility |
| Hardware offload (Nitro) | Dedicated hardware for I/O | Proprietary, expensive |

These approaches aren't mutually exclusive -- AWS Nitro uses hardware-assisted virtualization (KVM) combined with virtio-style interfaces to the Nitro cards. The evolution of virtualization has been about pushing different bottlenecks to wherever they can be handled most efficiently.

### Other cloud providers

AWS isn't alone in this approach:

- **Google Cloud**: Uses custom Titanium hardware for similar offloading of network and security functions
- **Microsoft Azure**: Has been moving towards hardware offload with their SmartNIC and FPGA-based approaches
- **Alibaba Cloud**: Developed their own hardware acceleration called MOC (Multi-function Offload Card)

The trend toward hardware offloading is clear: at hyperscale, custom silicon makes economic sense, and the performance benefits are substantial.

### Commercially available hardware: DPUs and SmartNICs

While AWS Nitro is proprietary, you can buy similar hardware for your own data center. The industry has settled on terms like "SmartNIC" (smart network interface card) and "DPU" (Data Processing Unit) for these devices, though the marketing terminology varies.

**NVIDIA BlueField**: The most prominent commercially available DPU. BlueField cards include:

- ARM cores that can run a full Linux distribution (including a hypervisor)
- Hardware acceleration for networking (OVS offload, VXLAN/Geneve encapsulation)
- Storage offload (NVMe-oF, encryption, compression)
- Security functions (IPsec, TLS, firewalling)

The BlueField can operate in different modes. In "DPU mode," the ARM cores on the card run the hypervisor and control plane, and the x86 host runs only guest workloads -- essentially the same architecture as Nitro. NVIDIA calls this approach "infrastructure on a chip."

**AMD/Pensando**: AMD acquired Pensando in 2022. Their DPUs focus on network and security offload, with programmable packet processing pipelines. They're used in Oracle Cloud Infrastructure and other environments.

**Intel IPU**: Intel's Infrastructure Processing Unit is their answer to the DPU trend. Intel has been working on both FPGA-based and ASIC-based solutions.

**Marvell OCTEON**: Another established player in the SmartNIC/DPU space, with a focus on programmable networking and security.

**Fungible**: Was an interesting startup building DPUs with a focus on storage and composable infrastructure. Microsoft acquired them in 2023, presumably for Azure infrastructure.

### What these can offload

Modern DPUs can handle:

| Function | What it means |
|----------|---------------|
| OVS offload | Move Open vSwitch packet processing to hardware |
| VXLAN/Geneve | Tunnel encapsulation/decapsulation in hardware |
| Connection tracking | Stateful firewall processing |
| Encryption | IPsec, TLS, or storage encryption acceleration |
| NVMe-oF | Remote storage access without CPU involvement |
| Virtio acceleration | Hardware implementation of virtio queues |

The most interesting capability is running the hypervisor itself on the DPU's embedded cores. This gives you the Nitro architecture -- a minimal trust boundary on the host, with infrastructure management isolated on the DPU.

### Why the focus on networking?

Virtual networking is disproportionately expensive in CPU terms. Open vSwitch (OVS), the software switch used by most cloud platforms, is notoriously CPU-hungry. In extreme cases, a [16-core server might dedicate 8 cores to the vSwitch](https://www.electronicdesign.com/technologies/test-measurement/article/21168521/napatech-virtual-switch-offloading-maximizes-data-center-utilization) while only 6 run actual VMs. Research on [SmartNIC offload](https://www.sciencedirect.com/science/article/abs/pii/S1389128621000244) has shown that moving OVS flow processing to hardware can reduce host CPU utilization by up to 97% for that function.

The network focus makes sense for several reasons. Every packet requires processing -- it's a per-operation cost that scales directly with traffic volume. Cloud networking adds layers of overhead: VXLAN or Geneve encapsulation for overlay networks, security group rule evaluation, VPC routing, and connection tracking for stateful firewalls. Unlike storage I/O which can be batched and is often sequential, network traffic is bursty and latency-sensitive. A few milliseconds of delay in packet processing is immediately visible to applications.

This is why AWS, before Nitro, had to [reserve multiple CPU cores from each server](https://perspectives.mvdirona.com/2019/02/aws-nitro-system/) just for network and storage emulation -- cores they couldn't sell to customers. Offloading this work to dedicated hardware means those cores become available for guest workloads.

### Should you use them?

For most organizations, the answer is probably "not yet" for several reasons:

**Cost**: DPUs are expensive -- a BlueField-2 was in the $1,000-2,000+ range when released, and newer generations cost more. You need significant scale before the performance benefits outweigh the hardware cost.

**Complexity**: These devices add another layer of infrastructure to manage. You need expertise in configuring and monitoring them, and your existing tooling may not support them.

**Software maturity**: While the hardware is capable, the software ecosystem is still maturing. Integration with hypervisors, container runtimes, and orchestration systems varies in quality.

**Workload fit**: If your workloads aren't network or storage I/O intensive, you won't see much benefit. A CPU-bound application doesn't care about DPU offload.

That said, if you're building infrastructure at significant scale (hundreds of servers), running network-intensive workloads (NFV, CDN, high-frequency trading), or have strict security isolation requirements, DPUs are worth evaluating.

### Open source platform support

If you're running an open source cloud platform, here's the current state of DPU/SmartNIC support:

**OpenStack**: Has the most mature support among open source platforms. Key integration points include:

- *OVS hardware offload*: Open vSwitch can offload flow processing to SmartNICs. This is configured through Neutron and requires the right drivers and `tc` (traffic control) flower offload support. Works with BlueField, Pensando, and others.
- *OVN offload*: Similar to OVS offload but for OVN-based deployments. Still maturing.
- *SR-IOV*: A simpler form of hardware offload where virtual functions are passed directly to VMs. Well-supported in Nova and Neutron.
- *NVIDIA DOCA/BlueField*: NVIDIA provides documentation and some upstream patches for BlueField integration, but expect to do significant integration work yourself.

The reality is that while OpenStack can use these devices, the configuration is complex, documentation is scattered, and you'll likely need vendor support or significant expertise. Most OpenStack deployments still use software-based networking.

**oVirt**: Limited native DPU support. oVirt is built on libvirt and KVM, so it inherits whatever those support:

- *SR-IOV*: Works through libvirt's hostdev passthrough. You can assign virtual functions to VMs.
- *OVS offload*: Possible in theory since oVirt uses OVS, but not well-documented or tested.
- *Full DPU mode*: Not really a supported configuration.

oVirt is more focused on traditional enterprise virtualization use cases than cutting-edge hardware offload.

**Proxmox**: Minimal DPU support. Proxmox targets small-to-medium deployments where the complexity of DPUs isn't justified:

- *SR-IOV*: Works for network passthrough to VMs, configured manually outside the GUI.
- *SmartNIC offload*: No integrated support. You'd have to configure everything at the Linux level yourself.
- *Full DPU mode*: Not supported.

Proxmox's strength is simplicity; DPUs add complexity that conflicts with that philosophy.

**Kubernetes**: Actually has some of the better DPU integration stories:

- *NVIDIA Network Operator*: Deploys and manages BlueField configuration in Kubernetes clusters.
- *SR-IOV Network Operator*: Manages SR-IOV virtual functions for container networking.
- *Multus CNI*: Allows pods to have multiple network interfaces, including SR-IOV and DPU-accelerated ones.

The container ecosystem has embraced hardware offload more readily than traditional virtualization platforms, possibly because Kubernetes is more commonly deployed at the scale where it matters.

**The honest assessment**: Unless you're running OpenStack at significant scale or have very specific performance requirements, DPU support in open source platforms is probably not mature enough for production use without vendor support contracts. SR-IOV is the exception -- it's simpler, well-understood, and works reliably across platforms.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | AWS Nitro System | [AWS Nitro System overview](https://aws.amazon.com/ec2/nitro/) from AWS |
    | Nitro deep dive | [This AWS re:Invent talk](https://www.youtube.com/watch?v=e8DVmwj3OEs) provides technical details on Nitro architecture |
    | The Nitro Hypervisor | [AWS blog post](https://aws.amazon.com/blogs/security/security-design-of-the-aws-nitro-system/) on Nitro's security design |
    | OpenStack SR-IOV | [OpenStack SR-IOV documentation](https://docs.openstack.org/neutron/latest/admin/config-sriov.html) covers the basics |
    | NVIDIA BlueField with OpenStack | [NVIDIA's DOCA documentation](https://docs.nvidia.com/doca/sdk/index.html) includes OpenStack integration guides |

## What hypervisor am I running?

We've covered a lot of history and technology. But what are you actually likely to encounter in practice? Let's survey the landscape.

### Linux server virtualization: KVM/QEMU dominates

If you're running virtual machines on Linux servers, you're almost certainly using KVM with QEMU. The open source cloud and virtualization platforms have converged on this stack:

| Platform | Hypervisor | Management Layer |
|----------|------------|------------------|
| OpenStack | KVM/QEMU | libvirt + Nova |
| oVirt | KVM/QEMU | libvirt + VDSM |
| Proxmox VE | KVM/QEMU | libvirt + custom tools |
| Shaken Fist | KVM/QEMU | libvirt |
| Red Hat Virtualization | KVM/QEMU | libvirt + VDSM |

The common thread is **libvirt** -- a virtualization API that abstracts away the details of the underlying hypervisor. While libvirt technically supports multiple hypervisors (Xen, VirtualBox, VMware ESXi, and others), surveys consistently show that 95%+ of libvirt users are running KVM/QEMU.

This convergence happened because:

- KVM is in the mainline Linux kernel, so it's always available and well-maintained
- QEMU provides mature, full-featured device emulation
- The combination is free (as in beer and freedom)
- Performance is competitive with commercial alternatives
- The ecosystem (libvirt, virtio, SPICE) is mature

### Cloud providers: proprietary stacks

The major cloud providers have moved away from off-the-shelf solutions:

**Amazon Web Services**: Modern EC2 instances run on AWS Nitro, which we discussed earlier. The Nitro Hypervisor is a minimal KVM-based system, but the overall stack is proprietary. Older instance types ran on Xen, and you may still encounter these in documentation or legacy deployments. AWS Lambda and Fargate use Firecracker.

**Google Cloud Platform**: Uses a custom hypervisor that Google doesn't publicly document in detail. It's known to be KVM-based, and Google has been a significant contributor to KVM development. Google also developed gVisor, a user-space kernel that provides container isolation without traditional virtualization.

**Microsoft Azure**: Uses Hyper-V, Microsoft's hypervisor. Azure was one of the reasons Microsoft invested heavily in making Hyper-V competitive with KVM and VMware. More recently, Azure has been moving toward hardware offload with SmartNICs and FPGAs.

**Oracle Cloud Infrastructure**: Uses KVM with their own modifications. They acquired Pensando (now AMD) DPUs and use them for network offload.

**Alibaba Cloud**: Uses a KVM-based stack with custom modifications and their MOC (Multi-function Offload Card) for hardware acceleration.

The pattern is clear: everyone is using or building on KVM, but the cloud providers have all heavily customized it and added proprietary hardware acceleration.

### Enterprise virtualization: VMware and Hyper-V

In traditional enterprise data centers, two commercial products dominate:

**VMware vSphere/ESXi**: The long-standing market leader for enterprise virtualization. ESXi is a Type 1 (bare-metal) hypervisor with its own kernel -- it's not based on Linux. VMware has comprehensive management tools (vCenter), ecosystem integration, and enterprise support. It's expensive, but many enterprises value the support and stability.

Recent developments have shaken the VMware market:

- Broadcom's acquisition of VMware in 2023 led to significant price increases and licensing changes
- Many organizations are now evaluating alternatives (particularly Proxmox and OpenStack)
- The shift to cloud has reduced the importance of on-premises virtualization for some workloads

**Microsoft Hyper-V**: Available both as a Windows Server role and as a free standalone product (Hyper-V Server, though Microsoft discontinued the standalone version in 2022). Hyper-V is a Type 1 hypervisor -- Windows actually runs as a privileged partition on top of Hyper-V, not the other way around.

Hyper-V is popular in Windows-centric environments because:

- It's included with Windows Server (no additional licensing for basic use)
- Good integration with Active Directory, System Center, and Azure
- Reasonable performance for Windows guests

For Linux guests, KVM generally offers better performance and integration than Hyper-V, which is why Linux-focused environments tend to use KVM-based solutions.

### Desktop virtualization

On personal computers, the landscape is more diverse:

**macOS**:

- *Parallels Desktop*: The most popular commercial option. Fast, polished, good Windows integration. Not cheap (~$100/year for the standard version).
- *VMware Fusion*: Long-standing competitor to Parallels. Recently made free for personal use.
- *UTM*: Free, open source, uses Apple's Hypervisor.framework (on Apple Silicon) or QEMU (on Intel). Good for running Linux, more limited for Windows.
- *VirtualBox*: Free, but performance on macOS (especially Apple Silicon) is poor compared to native solutions.

The transition to Apple Silicon (ARM-based M1/M2/M3 chips) changed this market significantly. Running x86 Windows now requires emulation, which is slower than native virtualization was on Intel Macs.

**Windows**:

- *Hyper-V*: Built into Windows 10/11 Pro and Enterprise. Free, good performance, but the UI is basic. Can conflict with other virtualization software.
- *VMware Workstation*: Long-standing commercial product. Recently made free for personal use.
- *VirtualBox*: Free, cross-platform, open source. Performance is adequate for most uses.
- *WSL 2*: Windows Subsystem for Linux 2 uses a lightweight Hyper-V VM to run a real Linux kernel. Not general-purpose virtualization, but relevant if you just need Linux.

**Linux**:

- *virt-manager*: GTK-based GUI for libvirt/KVM. Free, well-integrated with the Linux desktop.
- *GNOME Boxes*: Simpler libvirt frontend, focused on ease of use.
- *VirtualBox*: Works on Linux, though KVM generally offers better performance.
- *VMware Workstation*: Available for Linux, but less common than on Windows.

### Legacy and niche hypervisors

A few other hypervisors are worth mentioning:

**Xen**: Still exists and is actively developed, but has largely lost the Linux market to KVM. You'll encounter it in:

- Older AWS EC2 instances (pre-Nitro)
- Citrix Hypervisor (formerly XenServer)
- Some security-focused systems (Qubes OS uses Xen)

**bhyve**: FreeBSD's native hypervisor. If you're running FreeBSD, this is what you'd use. Not relevant to Linux environments.

**Jailhouse**: A partitioning hypervisor for safety-critical and real-time systems. Used in automotive and industrial applications where you need guaranteed isolation and deterministic behavior.

### How to tell what you're running

If you're on a Linux system and want to know what hypervisor you're running under (if any):

```bash
# Check if you're in a VM and what type
systemd-detect-virt

# More detailed information
hostnamectl | grep -i virtualization

# Or check DMI data directly
cat /sys/class/dmi/id/product_name
cat /sys/class/dmi/id/sys_vendor
```

Common outputs:

- `kvm` - Running under KVM/QEMU
- `microsoft` - Hyper-V
- `vmware` - VMware
- `xen` - Xen
- `oracle` - VirtualBox
- `amazon` - AWS Nitro
- `none` - Bare metal (no hypervisor detected)

## Conclusion

It would be fair to summarize this chapter by saying that virtualization is a mature technology with a surprisingly long history. However, the reality today is that unless you work for a hyperscaler, the thing to understand is KVM/QEMU -- basically everything you encounter in the Linux server market will have that under the hood.

A few key themes emerged as we traced this history:

**The march toward hardware**: We started with pure software emulation (slow), moved to hardware-assisted virtualization (fast), and are now seeing hardware offload of entire subsystems (faster still). Each generation pushed more work into silicon, trading generality for performance.

**The paravirtualization pattern persists**: Even though we don't use the term much anymore, the core idea -- designing interfaces for the virtual environment rather than emulating legacy hardware -- lives on in virtio, microVMs, and DPU architectures. The most efficient virtualization is always a collaboration between guest and host.

**Consolidation around KVM**: The fragmented hypervisor landscape of the 2000s (VMware, Xen, Hyper-V, KVM, various commercial offerings) has largely consolidated. KVM won the open source server market, VMware retained the enterprise market (though that's shifting), and the hyperscalers built custom stacks on KVM foundations.

**Specialization at scale**: AWS Nitro, Firecracker, and microVMs represent a trend toward purpose-built solutions. When you're running millions of VMs, the economics justify custom silicon and stripped-down hypervisors. For the rest of us, general-purpose KVM/QEMU remains the pragmatic choice.

### What we haven't covered

This chapter focused on compute virtualization, but there's much more to the cloud infrastructure story:

- **Containers**: How do containers relate to VMs? What's the isolation/performance trade-off? We'll explore this in a future chapter.
- **Virtual networking**: How do virtual machines talk to each other and the outside world? Software-defined networking, OVS, eBPF, and network virtualization deserve their own treatment.
- **Storage**: Block storage, object storage, distributed filesystems, and how they integrate with virtualization.
- **Orchestration**: How do you manage thousands of VMs? This is where OpenStack, Kubernetes, and friends come in.

### Practical takeaways

If you're building or operating virtualized infrastructure today, the most important thing is to learn libvirt and KVM well. These are the foundation of almost everything in the open source Linux virtualization world, and time invested here pays dividends across every platform you might use.

For performance, always use virtio devices for disk and network unless you have a specific compatibility requirement -- the performance difference compared to emulated hardware is substantial. Understanding your workload matters too; long-running server VMs, serverless functions, and container isolation have different requirements, and the right virtualization approach depends on what you're actually running.

It's worth keeping an eye on the DPU space. Hardware offload is coming to mainstream infrastructure, and even if you're not ready to deploy DPUs today, understanding the architecture will help you evaluate future options. That said, don't over-engineer: for most workloads, standard KVM/QEMU with virtio devices and a sensible management layer (Proxmox, OpenStack, or even just libvirt) is perfectly adequate. The exotic optimizations we discussed are for hyperscale environments with hyperscale problems.
