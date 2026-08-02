---
issue_repo: https://github.com/shakenfist/cloudgood
issue_path: docs/accounting.md
---

# CPU and Resource Accounting

We've established that the kernel manages the resources of the physical
computer and shares them among programs. But sharing implies fairness, and
fairness requires measurement. How does the kernel actually know how much
CPU time each process has consumed? And when virtual machines enter the
picture -- running in a completely different CPU mode -- how does accounting
still work?

## The scheduler tick

At its core, CPU time accounting on Linux relies on a deceptively simple
mechanism: a periodic timer interrupt called the scheduler tick.

The kernel configures a hardware timer to fire at a regular interval,
typically every 1 to 4 milliseconds depending on the kernel's `HZ`
configuration (1). When the timer fires, it generates an interrupt that
forces the CPU to stop whatever it was doing and transfer control to the
kernel's interrupt handler. At that moment, the kernel looks at what was
running on the CPU and charges that tick's worth of time to whatever it
finds.
{ .annotate }

1. The kernel configuration option `CONFIG_HZ` controls this. Common
   values are 100 (10ms ticks), 250 (4ms ticks), and 1000 (1ms ticks).
   Higher tick rates give finer-grained accounting but increase overhead.

This is important to understand: the kernel doesn't continuously monitor
the CPU. It gets periodic snapshots. If a process runs for 3.7
milliseconds between two ticks, the kernel might attribute either one
full tick or zero ticks to that process depending on exactly when the
interrupts land. Over time these errors average out, but for very
short-lived processes the accounting can be imprecise.

## Accounting for userspace processes

When the timer interrupt fires and the CPU was executing a userspace
process in Ring 3, the kernel attributes that tick to the running
process. But it goes further than just "this process used CPU" -- it
categorizes the time:

`user` time
:   Time spent executing the process's own code in user mode (Ring 3).

`system` time
:   Time spent in the kernel on behalf of this process -- handling
    system calls, page faults, and other kernel work triggered by the
    process.

`nice` time
:   User time for processes running at a lowered priority (positive
    nice value).

`idle` time
:   Time when the CPU had nothing to do.

`iowait` time
:   Time when the CPU was idle but there were outstanding I/O
    requests. This is a somewhat misleading metric -- it doesn't mean a
    specific process is waiting, just that the system had pending I/O
    while the CPU was idle.

You can see these categories in `/proc/stat`, which reports cumulative
tick counts for each CPU, and in `/proc/<pid>/stat`, which reports
per-process times (1).
{ .annotate }

1. The `time` command is a convenient way to see the user and system time
   for a single command. Tools like `top` and `htop` calculate
   percentages from the per-tick counters in `/proc`.

### The completely fair scheduler

The scheduler tick doesn't just measure time -- it drives scheduling
decisions. The kernel's Completely Fair Scheduler (CFS) (1) uses the
accumulated runtime of each process to decide what should run next. The
basic idea is that the process with the least accumulated "virtual
runtime" gets the CPU next. Priorities and nice values adjust how fast
virtual runtime accumulates -- a low-priority process accumulates
virtual runtime faster per wall-clock second, so it gets scheduled
less often.
{ .annotate }

1. CFS was the default Linux scheduler from kernel 2.6.23 (2007) until
   kernel 6.6 (2023), when it was replaced by EEVDF (Earliest Eligible
   Virtual Deadline First). The accounting principles are similar --
   EEVDF still uses virtual runtime tracking but adds deadline-based
   scheduling to reduce latency for interactive tasks.

The scheduler tick is also when the kernel checks whether the current
process has run long enough and should be preempted in favor of another
process. This is what makes Linux a preemptive multitasking system --
processes don't have to voluntarily yield the CPU; the kernel will take
it from them at the next tick if something else deserves a turn.

### High-resolution accounting

The tick-based approach has a clear limitation: its granularity is
bounded by the tick interval. If your ticks are 4ms apart, you can't
measure anything shorter than 4ms with tick-based accounting.

Modern kernels supplement tick counting with high-resolution timers
(hrtimers) and the CPU's timestamp counter (TSC) to provide finer-grained
measurements. The TSC is a per-CPU counter that increments at a constant
rate (usually the base clock frequency), giving nanosecond-precision
timestamps essentially for free (1).
{ .annotate }

1. "For free" meaning no I/O or interrupt is needed -- reading the TSC
   is a single `RDTSC` instruction. However, TSC synchronization across
   CPUs and behavior during power management transitions have been
   historical sources of bugs.

With TSC-based accounting (`CONFIG_SCHEDSTAT` or `CONFIG_SCHED_DEBUG`),
the kernel records exact timestamps at context switches and system call
boundaries, then calculates precise durations. This gives much more
accurate per-process accounting than tick sampling alone.

## Accounting for virtual machines

Now we arrive at the question that motivated this chapter. A KVM virtual
machine's vCPU runs in VMX non-root mode -- a different CPU mode entirely
from both Ring 0 (kernel) and Ring 3 (user). The host kernel cannot see
what instructions the guest is executing. So how does it account for the
CPU time consumed?

The answer is that the mechanism is fundamentally the same as for
userspace processes, and for the same reason: the kernel doesn't need
to observe what code is running; it just needs regular opportunities to
note that time has passed.

### KVM vCPUs are threads

In the KVM architecture, each virtual CPU of a guest is represented as
a host kernel thread, typically managed by QEMU (1). When the host kernel
schedules this thread onto a physical CPU, the thread executes a
`VMLAUNCH` or `VMRESUME` instruction to enter VMX non-root mode, and
the guest begins executing. From the host kernel's perspective, that
thread is simply "running" -- it happens to be running guest code
instead of normal userspace code, but the scheduling mechanics are the
same.
{ .annotate }

1. Each vCPU appears as a separate thread in the QEMU process. You can
   see them in `ps` or `top` as individual threads within the QEMU
   process, each consuming its own CPU time.

### Timer interrupts cause VM exits

Here is the key insight: when the host's timer interrupt fires while a
vCPU is running in VMX non-root mode, it causes a VM exit (1). The CPU
saves the guest state to the VMCS (Virtual Machine Control Structure),
transitions back to VMX root mode, and delivers the interrupt to the
host kernel. The host kernel's timer interrupt handler runs exactly as
it would for any other interrupted context -- it sees that the vCPU
thread was running, and charges the elapsed time to it.
{ .annotate }

1. External interrupts are configured to cause VM exits via the
   "external-interrupt exiting" control in the VMCS. The host kernel
   sets this so that hardware interrupts always return control to the
   host, even when guest code is executing.

This is almost exactly analogous to how userspace process accounting
works. The key parallel:

| Scenario | Code runs in | Interrupted by | Returns to |
|----------|-------------|----------------|------------|
| Userspace process | Ring 3 | Timer interrupt | Ring 0 (kernel) |
| KVM guest vCPU | VMX non-root | Timer interrupt (VM exit) | VMX root (host kernel) |

In both cases, the kernel regains control at regular intervals via
timer interrupts, notes what was running, and charges the time
accordingly.

### The guest_time field

Linux actually distinguishes between time a process spent executing
its own userspace code and time it spent running guest code. If you
examine `/proc/<pid>/stat` for a QEMU process, you'll find a
`guest_time` field (field 43) that reports cumulative time spent in
VMX non-root mode (1).
{ .annotate }

1. The `guest_time` field was added in Linux 2.6.24. It is also
   included in the per-CPU totals in `/proc/stat` as the `guest` and
   `guest_nice` columns.

This distinction matters for monitoring. When `top` shows a QEMU
process using 95% CPU, it's helpful to know whether that's 95% guest
execution or 95% QEMU userspace overhead (device emulation, I/O
handling, etc.). The `guest_time` field makes this visible.

Tools like `virt-top` and `perf kvm` use these counters to provide
VM-specific performance views.

## Steal time: the guest's perspective

So far we've discussed accounting from the host's point of view. But
what about the guest? A guest OS also has its own scheduler and its own
accounting. It sees what it believes are physical CPUs, and it runs its
own timer interrupts to account for time.

But there's a problem: the guest's vCPU might not always be running.
The host scheduler might preempt the vCPU thread to run something else
on that physical CPU. From the guest's perspective, time just... passes.
The guest's timer fires, and when it checks how much wall-clock time
has elapsed, more time has passed than the guest actually executed. The
guest was descheduled and didn't know it.

This is where **steal time** comes in. The host kernel tracks how much
time each vCPU spent waiting to be scheduled (i.e., time the vCPU was
runnable but not actually running). It exposes this to the guest through
a shared memory page that the guest kernel can read (1).
{ .annotate }

1. The mechanism uses a per-vCPU `steal_time` structure in a shared
   memory page. The host updates it before each VM entry, and the
   guest reads it during its own timer interrupt handling. This is
   part of the KVM paravirtual clock interface.

The guest kernel uses steal time to correct its accounting. When the
guest's timer interrupt fires and it sees that, say, 10ms of wall-clock
time has passed but 3ms of that was steal time, it knows the vCPU only
actually ran for 7ms. It can then:

- Attribute only 7ms to its running processes
- Report the 3ms as "steal" time in its own `/proc/stat`
- Make better scheduling decisions (not penalizing processes for time
  they didn't actually consume)

You can see steal time in the `st` column in `top` inside a virtual
machine. High steal time indicates that the host is overcommitting CPU
-- the VM isn't getting the CPU time it expects because the host is
sharing the physical CPUs with other workloads.

!!! info "Want to know more?"

    | Topic | Resource |
    |-------|----------|
    | KVM steal time implementation | The Linux source file `arch/x86/kvm/x86.c` contains the steal time accounting code. Search for `kvm_steal_time`. |
    | /proc/stat fields | The `proc(5)` man page documents all fields in `/proc/stat` and `/proc/<pid>/stat`, including the guest and steal time columns. |
    | Completely Fair Scheduler | The kernel source `kernel/sched/fair.c` is the authoritative reference, but [this LWN article](https://lwn.net/Articles/230574/) provides a gentler introduction. |
    | EEVDF scheduler | [This LWN article](https://lwn.net/Articles/925371/) explains the successor to CFS and why it was adopted. |
    | virt-top | The `virt-top` tool provides a `top`-like interface for monitoring virtual machine resource usage on a hypervisor. |

## Cgroups: limits and accounting combined

So far we've discussed how the kernel measures resource usage. But
measurement alone doesn't prevent a process (or VM) from consuming more
than its fair share. This is where control groups (cgroups) come in.

Cgroups are a kernel mechanism that groups processes together and applies
resource limits and accounting to the group as a whole. They were briefly
mentioned in the [technology primer](/components/cloudgood/technology-primer/) as a building
block of container isolation, but they're equally important for VM
resource management.

A cgroup can enforce limits on:

CPU time
:   How much CPU time the group can consume, expressed either as a share
    of available CPU (relative weighting) or as a hard limit (e.g., no
    more than 200ms per 100ms period, effectively capping at 2 CPUs).

Memory
:   Maximum resident memory for the group. If the group exceeds this
    limit, the kernel's OOM (Out of Memory) killer will terminate
    processes within the group.

I/O bandwidth
:   Limits on read/write bytes or operations per second to block devices.

PIDs
:   Maximum number of processes in the group (preventing fork bombs).

For virtual machines, cgroups provide a way for the host to limit the
total resources consumed by a VM's QEMU process and all its vCPU
threads. Libvirt, the most common VM management layer, automatically
creates cgroups for each VM it manages. When you configure a VM's CPU
quota or memory limit through libvirt (or higher-level tools like
OpenStack), those settings are translated into cgroup parameters.

### Cgroup accounting

Beyond enforcement, cgroups also provide accounting. The cgroup
filesystem (usually mounted at `/sys/fs/cgroup/`) exposes per-group
statistics:

- `cpu.stat` -- Total CPU time consumed by the group (user, system)
- `memory.current` -- Current memory usage
- `io.stat` -- Per-device I/O statistics

This dual role -- both limiting and measuring -- makes cgroups the
foundation for both container and VM resource management. The
measurement enables billing, capacity planning, and overcommit
detection. The limits enable multi-tenancy, where multiple workloads
share physical hardware without any single workload starving the
others.

## Conclusion

CPU accounting in Linux relies on a simple but effective principle: the
kernel doesn't need to continuously observe what code is running. It
uses timer interrupts to regain control at regular intervals, notes
what was executing, and charges the time accordingly. This works
regardless of the privilege level of the running code -- whether it's a
Ring 3 userspace process or a VMX non-root virtual machine guest.

For virtual machines, the mechanism is essentially the same as for
regular processes: timer interrupts cause VM exits, returning control
to the host kernel, which then performs accounting just as it would for
any other interrupted context. The guest_time field in `/proc` lets
monitoring tools distinguish between guest execution and QEMU overhead.

Steal time closes the loop by giving the guest visibility into time it
lost to host scheduling decisions, enabling accurate accounting within
the guest itself.

And cgroups sit on top of all this, providing both measurement and
enforcement of resource limits -- the mechanism that makes multi-tenant
environments like cloud computing possible.

In the next chapter we'll discuss [the history of
virtualization](/components/cloudgood/virtualization-history/) and how we got from full
system emulation to the hardware-accelerated KVM we use today.

--8<-- "docs-include/abbreviations.md"
