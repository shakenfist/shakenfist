# Virtio-Block4 Prototype

Extends [Virtio-Block3](/components/instar/prototypes/virtio-block3/) with comprehensive performance
statistics tracking for monitoring VM behavior and enabling resource limiting.

## Motivation

The security analysis in [security.md](/components/instar/security/) identifies DoS
vulnerabilities where malicious disk images cause excessive resource
consumption. To mitigate these risks, we need:

- **CPU time limits**: Abort operations exceeding time thresholds
- **Exit rate detection**: Identify anomalous VM behavior patterns
- **Resource accounting**: Track I/O operations for tuning and debugging

Additionally, performance counters help identify bottlenecks when comparing
different sector sizes and I/O patterns.

## Key Features

1. **Internal VMM Counters**: Track VM exits by type (I/O, MMIO, HLT, etc.),
   runtime, and throughput
2. **KVM Binary Statistics**: Check for kernel support and infrastructure for
   future implementation when kvm-ioctls exposes the API
3. **Statistics Display**: Automatic summary on completion
4. **All virtio-block3 features**: Configurable sector sizes, bidirectional
   serial, protobuf messaging

## Statistics Tracked

### VM Exit Counters

| Counter | Description |
|---------|-------------|
| `total_exits` | Total number of VM exits |
| `io_in_exits` | Port I/O input exits (serial reads) |
| `io_out_exits` | Port I/O output exits (serial writes) |
| `mmio_read_exits` | MMIO read exits (virtio device reads) |
| `mmio_write_exits` | MMIO write exits (virtio device writes) |
| `hlt_exits` | HLT instruction exits |
| `shutdown_exits` | Shutdown/triple fault exits |
| `fail_entry_exits` | Failed VM entry |
| `unknown_exits` | Unhandled exit types |

### I/O Statistics

| Counter | Description |
|---------|-------------|
| `bytes_read` | Total bytes read from input device |
| `bytes_written` | Total bytes written to output device |
| `sectors_read` | Sectors read from input device |
| `sectors_written` | Sectors written to output device |

### Derived Metrics

| Metric | Calculation |
|--------|-------------|
| Read throughput | bytes_read / runtime_secs |
| Write throughput | bytes_written / runtime_secs |
| Exit rate | total_exits / runtime_secs |
| MMIO exits/sector | mmio_exits / total_sectors |
| Bytes/exit | total_bytes / total_exits |

## Usage

```bash
# Build
./build.sh

# Run with default 512-byte sectors
sudo ./target/release/vmm --input source.bin --output dest.bin guest.bin

# Run with 4KB sectors
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --input-sector-size 4096 --output-sector-size 4096 guest.bin
```

## Example Output

```
Loaded guest binary: 19672 bytes from guest.bin
Input file: test.bin (4096000 bytes, 1000 sectors @ 4096 bytes/sector)
Output file: out.bin (pre-allocated 4096000 bytes, 1000 sectors @ 4096 bytes/sector)
KVM API version: 12
KVM binary statistics: supported
  Note: kvm-ioctls 0.19 does not expose stats_fd() yet.
  Using internal VMM counters for statistics.
Created VM
...

--- Starting guest execution ---

[INFO] init stage=config device=input address=0x1000
...
[PROGRESS] progress op=copy 1000/1000 (100%)
[COMPLETE] complete op=copy count=4096000 success=true

--- Guest executed HLT ---
Guest completed successfully!

=== VMM Statistics ===

Runtime:        1.234 seconds

Total exits:    45,678
  IO in:        1,234 (2.7%)
  IO out:       1,111 (2.4%)
  MMIO read:    21,166 (46.3%)
  MMIO write:   21,166 (46.3%)
  HLT:          1 (0.0%)

Data transfer:
  Bytes read:   4.00 MB (1,000 sectors)
  Bytes written: 4.00 MB (1,000 sectors)
  Read rate:    3.24 MB/s
  Write rate:   3.24 MB/s

Efficiency:
  Exit rate:    37,014 exits/sec
  MMIO/sector:  21.2 exits/sector
  Bytes/exit:   179.4
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                              VMM                                     │
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────────┐  │
│  │   KVM VM    │  │   Input Dev     │  │    Output Dev           │  │
│  │   + vCPU    │  │   (read-only)   │  │    (write)              │  │
│  └──────┬──────┘  └────────┬────────┘  └──────────┬──────────────┘  │
│         │                  │                       │                 │
│  ┌──────┴──────────────────┴───────────────────────┴──────────────┐ │
│  │                     VmmStats                                    │ │
│  │  - total_exits, io_exits, mmio_exits, hlt_exits               │ │
│  │  - bytes_read, bytes_written, sectors_processed               │ │
│  │  - runtime tracking, exit rate calculation                    │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                              │                                       │
│  ┌───────────────────────────┴─────────────────────────────────────┐│
│  │                  KVM Binary Stats (capability check)             ││
│  │  - Detects kernel support for KVM_CAP_BINARY_STATS_FD           ││
│  │  - Infrastructure for future kernel statistics integration      ││
│  └──────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

## KVM Binary Statistics

The KVM kernel provides a binary statistics API (`KVM_GET_STATS_FD`) that
exposes per-VM and per-vCPU statistics directly from the kernel. This prototype
includes:

1. **Capability Detection**: Checks if the kernel supports
   `KVM_CAP_BINARY_STATS_FD` (capability 203)
2. **Data Structures**: Definitions for parsing the binary stats format
3. **Infrastructure**: Placeholder for future implementation

The kvm-ioctls crate (v0.19) does not yet expose the `stats_fd()` method, so
this prototype uses internal VMM counters for statistics. When the API becomes
available, the infrastructure is ready to integrate kernel-level statistics.

## Implementation Details

### VMM Modules

| Module | Description |
|--------|-------------|
| `stats.rs` | VmmStats struct for tracking exit counts and I/O |
| `kvm_stats.rs` | KVM binary stats API support and capability checking |
| `virtio/block.rs` | Updated to return IoStats from process_queue |

### Statistics Flow

1. VMM creates `VmmStats` before entering the vCPU loop
2. Each VM exit type is recorded via `record_*` methods
3. I/O operations return `IoStats` which are accumulated
4. On completion, `display()` shows the summary

## Comparison with Previous Prototypes

| Feature | virtio-block3 | virtio-block4 |
|---------|---------------|---------------|
| Exit counting | None | By type |
| Runtime tracking | None | Wall-clock |
| Throughput metrics | None | Calculated |
| KVM capability check | None | Yes |
| Statistics display | None | On completion |

## Files

| Path | Description |
|------|-------------|
| `vmm/src/main.rs` | VMM with statistics integration |
| `vmm/src/stats.rs` | VmmStats implementation |
| `vmm/src/kvm_stats.rs` | KVM binary stats API support |
| `vmm/src/virtio/block.rs` | Block device with IoStats return |

## Future Work

- **Resource Limits**: Add timeout and exit rate limit enforcement
- **KVM Stats Integration**: Implement full stats reading when kvm-ioctls
  exposes the API
- **Prometheus Metrics**: Export statistics in Prometheus format for monitoring

## Related Documentation

- [Performance Counters](/components/instar/prototypes/performance-counters/)
- [Security Vulnerabilities](/components/instar/security/)
- [Virtio-Block Data Transfer](/components/instar/prototypes/data-transfer-virtio-block/)
- [Virtio-Block3 Prototype](/components/instar/prototypes/virtio-block3/)
