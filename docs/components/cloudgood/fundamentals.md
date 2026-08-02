---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/fundamentals.md
---

# Fundamentals

Let's start back at the very very beginning. What even is a computer?

## What is a computer?

If we're going to build some virtual computers, we first need to define what a computer actually is. Note that here I am using the term "computer" to refer to what we'd probably call a "core" or "hyperthread" in a modern architecture, but the concepts are close enough for now. We'll also ignore pipelining and speculative execution to simplify things.

Fundamentally, a computer is a machine (1) which knows how to execute a linear (2) set of instructions in order. Those instructions can take arguments (either directly in the instruction or from various forms of memory) and produce outputs. It's normally possible to jump around in the sequence of instructions if you need to skip over or repeat some portions of the sequence. Even today all computers I am aware of start execution after a restart from a fixed point in memory, although that address is not always location "0" in the memory map. By way of example, the initial program counter is address 0 on the Intel 4004 / 8008 / 8080 series of microprocessors, whereas it is 0xFFFF0000 for 80186 onwards, the MOS 6502 stores the address of reset, non-maskable interrupt (NMI) and interrupt request (IRQ) handlers in the last six bytes of memory, and so on.
{ .annotate }

1. This was not always true. Initially "computer" was a job title for a human whose job it was to perform mathematical operations by hand. Often this was in the form of producing lookup tables of values which could then be used for later calculations -- so for example you could buy a book of logarithm tables. The most common example these days of such a lookup table is the multiplication posters still used by many junior schools.
2. It's that linear word again. As a reminder, "Linear" here is fancy for "in a line". That is, a simple list of data blocks or instructions, one after the other.

It should also be noted that the instructions you execute on modern CPUs (modern being anything since at least the Intel 8086 released in June 1978) are translated into a smaller set of "microcode instructions" that the CPU actually executes. That is, even the machine instructions we send to the CPU to execute are in fact an abstraction (1).
{ .annotate }

1. This is true to the extent that it is asserted that the Via C3 x86 CPU is in fact a proprietary RISC CPU which converts x86 instructions on the fly to its native instruction set.

!!! quote

    You might think that machine instructions are the basic steps that a computer performs. However, instructions usually require multiple steps inside the processor. One way of expressing these multiple steps is through microcode, a technique dating back to 1951. To execute a machine instruction, the computer internally executes several simpler micro-instructions, specified by the microcode. In other words, microcode forms another layer between the machine instructions and the hardware. The main advantage of microcode is that it turns the processor's control logic into a programming task instead of a difficult logic design task.

    https://www.righto.com/2023/07/undocumented-8086-instructions.html

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Pre-electronic computing | [Crash Course Computer Science Episode 2](https://www.youtube.com/watch?v=O5nskjZ_GoI&list=PL8dPuuaLjXtNlUrzyH5r6jN9ulIgZBpdo&index=2) (YouTube) |
    | Registers and RAM | [Crash Course Computer Science Episode 6](https://www.youtube.com/watch?v=fpnE6UAfbtU) (YouTube) |
    | High level design of a microprocessor | [Crash Course Computer Science Episode 7](https://www.youtube.com/watch?v=FZGugFqdr60) (YouTube). Note that this video describes one possible microarchitecture. There are others though, for example accumulator based machines and machines which support immediate arguments as part of the operand. |
    | A worked example of how a linear sequence of machine code instructions (opcodes) form a program in a microprocessor | [Crash Course Computer Science Episode 8](https://www.youtube.com/watch?v=zltgXvg6r3k&list=PL8dPuuaLjXtNlUrzyH5r6jN9ulIgZBpdo&index=9) (YouTube) |
    | How many instructions do modern microprocessors have? | That's... complicated. [This academic paper from 2021](https://d1wqtxts1xzle7.cloudfront.net/89180149/enumerating-x86-64-instructions-libre.pdf) has a reasonable explanation of why. |
    | Intel 8086 microcode | [Ken Shirriff's blog post](https://www.righto.com/2023/07/undocumented-8086-instructions.html) about undocumented microcode in the 8086 processor is the source of the quote above, but is quite interesting in general. |

## Abstraction layers

Now that we know what a computer is, we should talk about how we think about them. Pretty much everything in modern computing is built upon a set of abstraction layers -- simplifying assumptions which hide complexity while providing the next layer up with a foundation upon which to build. A really good example of this is the OSI seven layer model for networking (1) -- each higher numbered layer can rely on the ones below to perform its functions, but also it highlights an important point -- the OSI seven layer model is also a lie. The reality is that no modern networking system strictly follows the OSI model, especially given OSI was a standards body which proposed something much more complicated than TCP/IP and has now largely died out. OSI only lives on as the mental model we teach new network engineers to help them understand how our networks operate.
{ .annotate }

1. https://en.wikipedia.org/wiki/OSI_model

Exactly the same thing has happened with all areas of modern computing. For example, our mental model of how a SSD behaves is very much like a linear set of sectors (1), but that's not at all how they're implemented under the hood where an embedded microprocessor is moving sectors around to handle wear leveling and to avoid sectors which are acting unreliably. Even Micro SD cards have microprocessors in them now (2).
{ .annotate }

1. "Linear" here is fancy for "in a line". That is, a simple list of data blocks or instructions, one after the other.
2. https://www.bunniestudios.com/blog/on-microsd-problems/

Why does this matter? It mostly matters in a document like this because it is easy to nit-pick -- there is a line between providing sufficient detail and being completely accurate. Accuracy can sometimes confuse the reader when what we're trying to do is create a foundation we can build on later.

I have tried hard to provide links with further detail on areas I think I am making simplifications in, but I am also sure I have missed things. Feel free to dig into things you find interesting but also try not to get bogged down in things which ultimately possibly aren't relevant to your overall goals.

## What is a program?

A program is what we call some number of these instructions grouped together to perform a task. Fundamentally, apart from hardware, a program is all you need. There is nothing except from it being really annoying stopping you from running your program on even modern CPUs without any of the things we normally associate with computers like a BIOS or an operating system. This was how all computers worked in the early days, but it's largely died out because interacting with modern hardware at the raw code level is so massively annoying that basically no one wants to do it. One common example of where we still do this is embedded systems like Arduinos, which often have a single program that just starts executing immediately from power on.

!!! note

    As mentioned in the [technology primer](/components/cloudgood/technology-primer/), some amount
    of this documentation is lifted from an unreleased project. One day when
    I release that thing someone should remind me to update this section to
    explain how it is also sometimes quite cool to execute raw code on modern
    CPUs. Or at least I think so.

An instance of a program running on top of a kernel is called a process.

## What is a kernel?

A kernel is a special program whose role is to manage the resources of the actual physical computer and share those resources among several programs which do things the user of the machine cares about. This creates a divide in terminology -- code which runs in the kernel is said to run in "kernel mode" and has unrestricted access to the computer's resources (1). On the other hand, code which runs for a user and performs a task that user has requested is said to run in "user mode". User mode programs use services provided by the kernel to implement their functionality.
{ .annotate }

1. This is likely not strictly true – the CPU itself may impose rules upon the kernel as to what it can do.

As an example, if the kernel wants to read or write a file on a disk, it must understand a stack of things needed to know how to do that thing -- how to talk to the disk controller, how to determine where in the disk's logical address space the data lives (probably using a filesystem), and how to then ask the disk to either set or get the data at those locations (1).
{ .annotate }

1. A former draft of that sentence used to say "[the kernel] must understand _the complete stack of things_ needed", but of course that's also not true and invites nit picking. The kernel understands how to talk to a standardized interface with the storage hardware -- a SATA controller or a NVMe device for example. The other side of that controller is of course yet another stack of software (as firmware) and hardware that will have its own abstraction layers.

On the other hand, a user space program would simply request that the kernel read or write data at the current location in a file for it. These requests are generally made via things called system calls, which we will discuss in a little bit.

All this also means that while already being "more complicated", kernel code is also riskier -- a crash in the kernel will often cause the computer to reset to an initial starting state or display a scary blue error screen, whereas an error in a user mode program might cause that program to crash but is unlikely to cause the entire machine to crash.

It should be noted that modern CPUs have hardware protections to stop user space programs from accessing outside their assigned address space, but that is largely outside the scope of the current discussion. You should for now just know that the CPU also has hardware protection "rings" that control what instructions can be executed. On Intel CPUs the kernel runs in ring 0, user space programs generally run in ring 3, and that there are a variety of negatively numbered rings that run under the kernel.

For the purposes of this content, the only kernel we care about is Linux.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | Protection rings | [Wikipedia](https://en.wikipedia.org/wiki/Protection_ring) has a good page explaining hardware protection rings. |
    | Breaking the x86 instruction set | [This DEF CON 25 talk](https://www.youtube.com/watch?v=ajccZ7LdvoQ) about why we shouldn't trust microprocessors more than we trust software (YouTube) |
    | Negative rings in the Intel architecture | [This blog post](https://medium.com/swlh/negative-rings-in-intel-architecture-the-security-threats-youve-probably-never-heard-of-d725a4b6f831) has a reasonable discussion of "negatively numbered protection rings". |
    | The Ring 0 Facade: Awakening the Processors Inner Demons | [This DEF CON 26 talk](https://www.youtube.com/watch?v=XH0F9r0siTI) about model specific registers and undocumented microcode is a good watch (YouTube) |
    | God Mode Unlocked: Hardware backdoors in x86 | [This DEF CON 28 talk](https://www.youtube.com/watch?v=jmTwlEh8L7g) is not only a good watch, but contains a good explanation of the x86 ring model of privilege (YouTube) |
    | The performance implications of modern Intel CPU designs | [This QCon talk](https://www.youtube.com/watch?v=rglmJ6Xyj1c) gives a nice overview of the design of relatively recent Intel CPUs and the performance implications of that design (YouTube) |

## System calls

A system call is how a user space program requests a service from the kernel. Effectively, it's the API that the kernel exposes to the programs it is managing. System calls do many things -- reading and writing filesystems, queueing network packets for dispatch, retrieving queued network packets for processing, starting sub-processes and so on. System calls are what `strace` shows you, which is a useful debugging technique for disobedient user space software.

On 32-bit x86 you call a system call from user space by placing your arguments (including the system call number) into registers and then triggering the 0x80 interrupt. The kernel's interrupt handler will then extract the arguments and service the request. On 64-bit x86 there is a dedicated syscall instruction, which is faster to execute but the concept is the same.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | The Linux x86 System Call mechanism | [This online book](https://en.wikibooks.org/wiki/X86_Assembly/Interfacing_with_Linux) has a nice section on the system call mechanism. |

## Linux architectures

Linux runs on many different microprocessors. In fact, more than one type of machine can share a microprocessor design but still operate in different ways -- ARM is probably the most common example at the moment, especially in embedded devices like mobile phones. Linux therefore calls the unique combination of a microprocessor and its supporting hardware an "architecture". The process of adding support for a new architecture is called "porting" and the newly supported architecture is called a "port". Thus, Linux supports the x86_64 architectural port, as well as the PowerPC (1) architectural port.
{ .annotate }

1. Let’s ignore that PowerPC has a variety of sub-architectures for now.

There can sometimes be surprising differences between Linux architectures because of hardware differences -- the system calls might have different numbers, or constant values passed as arguments to a function might change. However, that's not actually why I mention architectures here -- that will become clear in the next chapter.

## Conclusion

So we've built a foundation in this document. We've talked about what a computer is at a high level. We've explored programs and how privilege levels affect what they can do. We've introduced the concept of the kernel as a special program which both abstracts away much of the complexity of real hardware, while also turning a machine which can only do one thing at once (for a single core machine) into a thing which can have its resources shared between many programs.

In the next chapter we'll discuss [CPU and resource
accounting](/components/cloudgood/accounting/) -- how the kernel actually measures CPU
time for both regular processes and virtual machines, and how cgroups
enforce resource limits in multi-tenant environments.

--8<-- "docs-include/abbreviations.md"
