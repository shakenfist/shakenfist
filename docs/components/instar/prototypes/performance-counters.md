# KVM Performance Counters and Resource Monitoring

This document describes the performance monitoring and resource limiting options
available when building custom VMMs with KVM, with specific focus on the instar
use case of sandboxed image conversion.

## Why Performance Monitoring Matters

The security analysis in [security.md](/components/instar/security/) identifies several DoS
vulnerabilities (CVE-2024-4467, CVE-2015-5162, CVE-2018-10908) where maliciously
crafted disk images cause excessive CPU or memory consumption. The recommended
mitigations include:

- **CPU time limit**: 2 seconds maximum for image inspection operations
- **Memory limit**: 1GB maximum for qemu-img processes
- **Exit rate detection**: Identify anomalous VM behavior patterns

Performance counters also help with tuning prototype implementations by
identifying bottlenecks such as excessive VM exits, inefficient I/O patterns,
or suboptimal sector sizes.

## Available Monitoring Options

### Option A: Internal VMM Counters (Recommended for instar)

The simplest and most portable approach is to track statistics directly in the
VMM's main loop. Since the VMM already processes every VM exit, adding counters
has minimal overhead.

**Advantages:**
- No external dependencies
- Works in any environment (containers, restricted systems)
- Can enforce limits and abort immediately
- Directly integrated with VMM logic

**Example structure:**

```rust
use std::time::Instant;

struct VmmStats {
    start_time: Instant,
    total_exits: u64,
    io_exits: u64,       // Serial port reads/writes
    mmio_exits: u64,     // Virtio MMIO accesses
    hlt_exits: u64,
    shutdown_exits: u64,
    unknown_exits: u64,
}

impl VmmStats {
    fn new() -> Self {
        Self {
            start_time: Instant::now(),
            total_exits: 0,
            io_exits: 0,
            mmio_exits: 0,
            hlt_exits: 0,
            shutdown_exits: 0,
            unknown_exits: 0,
        }
    }

    fn elapsed_secs(&self) -> f64 {
        self.start_time.elapsed().as_secs_f64()
    }

    fn check_limits(&self, max_runtime_secs: f64, max_exits: u64) -> Result<(), &'static str> {
        if self.elapsed_secs() > max_runtime_secs {
            return Err("CPU time limit exceeded");
        }
        if self.total_exits > max_exits {
            return Err("Exit count limit exceeded");
        }
        Ok(())
    }
}
```

**Integration with main loop:**

```rust
loop {
    stats.total_exits += 1;
    stats.check_limits(2.0, 10_000_000)?;  // 2 second timeout, 10M exit limit

    match vcpu.run()? {
        VcpuExit::IoOut(port, data) => {
            stats.io_exits += 1;
            // ... handle I/O
        }
        VcpuExit::MmioRead(addr, data) => {
            stats.mmio_exits += 1;
            // ... handle MMIO
        }
        // ... other exits
    }
}
```

### Option B: KVM Binary Statistics API

Linux kernel 5.14+ provides `KVM_GET_STATS_FD` for accessing per-VM and per-vCPU
statistics through a binary file descriptor interface. This provides
kernel-level precision for statistics the kernel already tracks.

**Capability check:** `KVM_CAP_BINARY_STATS_FD`

**Available via:** `kvm-ioctls` crate

**File structure:**

| Block | Contents |
|-------|----------|
| Header | Metadata (flags, descriptor count, offsets) |
| ID String | VM or vCPU identifier (e.g., "kvm-12345/vcpu-0") |
| Descriptors | Describes each statistic (name, type, unit, offset) |
| Stats Data | Actual counter values (64-bit unsigned integers) |

**Statistic types:**

| Type | Description |
|------|-------------|
| CUMULATIVE | Monotonically increasing counters (read/write) |
| INSTANT | Point-in-time measurements (read-only) |
| PEAK | Maximum value seen (read-only) |
| LINEAR_HIST | Linear histogram with bucket_size |
| LOG_HIST | Logarithmic histogram (power-of-2 ranges) |

**Units:**

| Unit | Description |
|------|-------------|
| NONE | Simple counter |
| BYTES | Memory measurement |
| SECONDS | Time/latency |
| CYCLES | CPU clock cycles |
| BOOLEAN | 0 or 1 |

**Example usage:**

```rust
use kvm_ioctls::Cap;

// Check if capability is available
if kvm.check_extension(Cap::StatsBinaryFd) {
    // Get stats file descriptor for vCPU
    let stats_fd = vcpu.stats_fd()?;

    // Read header to get offsets
    let mut header = kvm_stats_header::default();
    pread(stats_fd, &mut header, 0)?;

    // Read descriptors (immutable, read once)
    let descriptors = read_descriptors(stats_fd, &header)?;

    // Read stats data (can be read repeatedly)
    let data = read_stats_data(stats_fd, &header)?;
}
```

**Note:** The specific statistics available depend on architecture and kernel
version. Common x86 statistics include VM exit counts by reason, interrupt
injection counts, and halt polling statistics.

### Option C: Perf Events (Tracepoints)

The Linux perf subsystem provides tracepoints for KVM events. These can be
accessed via command-line tools or programmatically.

**Available tracepoints:**

| Event | Description |
|-------|-------------|
| `kvm_entry` | Guest entry into hypervisor |
| `kvm_exit` | Guest exit (includes exit reason) |
| `kvm_pio` | Port I/O operations |
| `kvm_mmio` | Memory-mapped I/O access |
| `kvm_hypercall` | Hypercall invocations |
| `kvm_msr` | MSR read/write operations |
| `kvm_cr` | Control register modifications |
| `kvm_page_fault` | Guest page faults |
| `kvm_apic` | APIC operations |
| `kvm_inj_virq` | Virtual interrupt injections |
| `kvm_pic_set_irq` | PIC IRQ settings |
| `kvm_ioapic_set_irq` | I/O APIC IRQ operations |
| `kvm_msi_set_irq` | MSI configuration |
| `kvm_ack_irq` | IRQ acknowledgments |

**Command-line usage:**

```bash
# Real-time statistics during execution
perf kvm stat live -- ./vmm --input file.img --output out.img guest.bin

# Record for later analysis
perf kvm stat record -- ./vmm --input file.img --output out.img guest.bin
perf kvm stat report

# Count specific events
perf stat -e 'kvm:kvm_exit,kvm:kvm_mmio,kvm:kvm_pio' -- ./vmm ...
```

**Sample output:**

```
Analyze events for all VMs, all VCPUs:

             VM-EXIT    Samples  Samples%     Time%    Min Time    Max Time         Avg Time

       EXTERNAL_INTERRUPT     12345     45.2%    12.3%      0.50us    150.00us       2.50us
               MSR_WRITE      8234     30.1%     8.5%      0.30us     10.00us       1.20us
                     HLT      3456     12.6%    65.2%    100.00us  50000.00us    2500.00us
          IO_INSTRUCTION      2100      7.7%     5.0%      0.80us     50.00us       3.00us
                    MMIO      1200      4.4%     9.0%      1.00us    200.00us      10.00us
```

### Option D: BPF-based Tools

The BCC (BPF Compiler Collection) provides `kvmexit`, a lightweight tool for
monitoring KVM exit reasons across all VMs on a system.

**Installation:**

```bash
# Debian/Ubuntu
apt install bpfcc-tools

# Fedora/RHEL
dnf install bcc-tools
```

**Usage:**

```bash
# Monitor all VMs
kvmexit

# Monitor specific VM by PID
kvmexit -p $(pgrep vmm)
```

**Advantages:**
- Very low overhead (in-kernel aggregation)
- No modification to VMM code required
- Useful for quick performance analysis

### Option E: debugfs Statistics

KVM exposes statistics through debugfs at `/sys/kernel/debug/kvm/`. This
requires root access and debugfs to be mounted.

```bash
# Mount debugfs if not already mounted
mount -t debugfs none /sys/kernel/debug

# View global KVM statistics
cat /sys/kernel/debug/kvm/*

# Enable tracing
echo 1 > /sys/kernel/debug/tracing/events/kvm/enable
cat /sys/kernel/debug/tracing/trace_pipe
```

**Note:** The debugfs interface is primarily useful for debugging and may not
be available in production environments or containers.

## Exit Reasons by Architecture

### AMD-V (SVM) Exit Reasons

The instar development environment uses AMD processors with the `kvm_amd` module.
Common SVM exit reasons:

| Exit Code | Name | Description |
|-----------|------|-------------|
| 0x60-0x6F | CR0-CR15 read | Control register read |
| 0x70-0x7F | CR0-CR15 write | Control register write |
| 0x7B | IOIO | Port I/O instruction |
| 0x7C | MSR | MSR access |
| 0x78 | HLT | HLT instruction |
| 0x400 | NPF | Nested page fault (used for MMIO) |
| 0x402 | AVIC_INCOMPLETE_IPI | AVIC IPI delivery |
| 0x403 | AVIC_NOACCEL | AVIC not accelerated |

### Intel VT-x (VMX) Exit Reasons

For Intel processors with the `kvm_intel` module:

| Exit Code | Name | Description |
|-----------|------|-------------|
| 0 | EXCEPTION_NMI | Exception or NMI |
| 1 | EXTERNAL_INTERRUPT | External interrupt |
| 2 | TRIPLE_FAULT | Triple fault |
| 7 | INTERRUPT_WINDOW | Interrupt window |
| 10 | CPUID | CPUID instruction |
| 12 | HLT | HLT instruction |
| 28 | CR_ACCESS | Control register access |
| 30 | IO_INSTRUCTION | Port I/O |
| 31 | RDMSR | MSR read |
| 32 | WRMSR | MSR write |
| 48 | EPT_VIOLATION | EPT violation (used for MMIO) |

## Recommendations for instar

### For Development and Benchmarking

1. **Use internal counters** (Option A) in the VMM main loop
2. **Use `perf kvm stat`** (Option C) for detailed exit analysis
3. **Compare sector sizes** by tracking MMIO exits per byte transferred

### For Production Security

1. **Implement timeout enforcement** using wall-clock time
2. **Implement exit rate limiting** to detect DoS attempts
3. **Log statistics** for post-mortem analysis of failures
4. **Consider memory limits** via cgroups if running multiple conversions

### Metrics to Track

| Metric | Purpose |
|--------|---------|
| Total runtime | Enforce CPU time limits |
| Total VM exits | Detect anomalous behavior |
| MMIO exits | Measure virtio efficiency |
| I/O exits | Measure serial port overhead |
| Bytes transferred | Calculate throughput |
| Exits per sector | Identify optimization opportunities |

## References

- [KVM Perf Events](https://www.linux-kvm.org/page/Perf_events)
- [perf-kvm(1) man page](https://man7.org/linux/man-pages/man1/perf-kvm.1.html)
- [KVM API Documentation](https://docs.kernel.org/virt/kvm/api.html)
- [kvm_stat tool](https://github.com/torvalds/linux/blob/master/tools/kvm/kvm_stat/kvm_stat)
- [kvmexit BPF tool](https://manpages.ubuntu.com/manpages/lunar/man8/kvmexit-bpfcc.8.html)
- [DigitalOcean vmtop](https://github.com/digitalocean/vmtop)
