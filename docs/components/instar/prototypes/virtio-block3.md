# Virtio-Block3 Prototype

Extends [Virtio-Block2](/components/instar/prototypes/virtio-block2/) with configurable sector sizes to
investigate whether larger I/O granularity improves performance.

## Motivation

The previous virtio-block prototypes used a fixed 512-byte sector size, which
results in high overhead for large file copies due to the small I/O size. This
prototype allows experimenting with larger sector sizes (e.g., 4KB, 64KB) to
validate whether increased sector size improves performance.

## Key Features

1. **Configurable Sector Sizes**: Command-line options for both input and output
   device sector sizes
2. **Bidirectional Serial Communication**: VMM sends configuration to guest at
   startup, guest sends status/progress messages back
3. **Sector Size Translation**: Guest handles copying between devices with
   different sector sizes
4. **Debug Output**: Separate COM2 port for plain text debugging, independent of
   the protobuf protocol on COM1

## Architecture

```
VMM                                          Guest
 │                                            │
 │ ──── VmmConfig (sector sizes) ──────────> │  COM1 (0x3f8)
 │                                            │  Protobuf protocol
 │ <──── InitMessage (config received) ───── │
 │ <──── CapacityMessage (per device) ─────  │
 │ <──── ProgressMessage (copy progress) ──  │
 │ <──── CompleteMessage (done) ───────────  │
 │                                            │
 │ <──── Debug messages (plain text) ─────── │  COM2 (0x2f8)
```

### Serial Ports

- **COM1 (0x3f8)**: Protocol Buffer messages for configuration and status
- **COM2 (0x2f8)**: Plain text debug output for troubleshooting

## Usage

```bash
# Build
./build.sh

# Run with default 512-byte sectors
sudo ./target/release/vmm --input source.bin --output dest.bin guest.bin

# Run with 4KB sectors
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --input-sector-size 4096 --output-sector-size 4096 guest.bin

# Run with mixed sector sizes
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --input-sector-size 4096 --output-sector-size 512 guest.bin

# Run with 4KB sectors and progress updates every 10%
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --input-sector-size 4096 --output-sector-size 4096 \
     --progress-percent 10 guest.bin

# Run with 64KB sectors and no progress updates (maximum performance)
sudo ./target/release/vmm --input source.bin --output dest.bin \
     --input-sector-size 65536 --output-sector-size 65536 \
     --progress-percent 100 guest.bin
```

## CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--input-sector-size` | 512 | Sector size for input device in bytes |
| `--output-sector-size` | 512 | Sector size for output device in bytes |
| `--progress-percent` | 0 | Progress update interval |

Valid sector sizes are powers of 2, from 512 bytes to 64KB (65536 bytes).

### Progress Reporting

The `--progress-percent` option controls how often progress updates are sent:

- `0` - Legacy behavior: report every 10 sectors (for comparison with earlier
  prototypes)
- `1-99` - Report when crossing each N% threshold (e.g., `10` reports at 10%,
  20%, 30%, etc.)
- `100` - No progress updates (completion message only)

## Protocol Extensions

### VmmConfig Message (VMM → Guest)

New protobuf message type for sending configuration to the guest:

```protobuf
message DeviceConfig {
  string name = 1;           // "input" or "output"
  uint32 sector_size = 2;    // Sector size in bytes
}

message VmmConfig {
  repeated DeviceConfig devices = 1;
  uint32 progress_percent = 2;  // Progress update interval
}
```

The VMM sends this message over the serial port at startup. The guest reads it
before initializing the virtio devices.

## Memory Layout

| Region | Address | Size | Description |
|--------|---------|------|-------------|
| GDT | 0x1000 | 24 bytes | Global Descriptor Table |
| Page Tables | 0x2000 | 12KB | PML4, PDPT, PD for identity mapping |
| Guest Code | 0x10000 | ~20KB | Flat binary loaded from guest.bin |
| Input VQ | 0x100000 | 64KB | Virtqueue for input device |
| Output VQ | 0x110000 | 64KB | Virtqueue for output device |
| DMA Pool | 0x200000 | 1MB | Guest DMA buffers for I/O |
| Stack | 0x300000 | 1MB | Guest stack (grows down from 0x3ffff8) |

Total guest memory: 8MB (0x800000)

The stack size was increased to 1MB to accommodate Rust's stack probing, which
pre-allocates stack space for large local variables. The compiler generates code
that touches every 4KB page from the stack top down to ensure guard pages work
correctly.

## Implementation Details

### VMM Side

- CLI parsing via clap with sector size validation
- `VirtioBlockDevice` constructor now takes sector size parameter
- `SerialTransmitter` queues config message for transmission
- Serial port read handler returns queued config bytes
- Line Status Register (LSR) reports data available
- `DebugBuffer` collects COM2 output until newline for clean debug logging

### Guest Side

- `serial::read_config()` reads configuration from serial at startup
- Devices initialized with sector sizes from configuration
- Sector size translation during copy for mismatched sizes
- DMA buffer sized for maximum supported sector size (64KB)
- `serial::debug_print()` sends plain text to COM2 for troubleshooting

## Performance Testing

To compare performance with different sector sizes:

```bash
# Create 1GB test file
dd if=/dev/urandom of=large.bin bs=1M count=1024

# Benchmark 512-byte sectors
time sudo ./target/release/vmm --input large.bin --output out.bin \
     --input-sector-size 512 --output-sector-size 512 guest.bin

# Benchmark 4KB sectors
time sudo ./target/release/vmm --input large.bin --output out.bin \
     --input-sector-size 4096 --output-sector-size 4096 guest.bin

# Benchmark 64KB sectors
time sudo ./target/release/vmm --input large.bin --output out.bin \
     --input-sector-size 65536 --output-sector-size 65536 guest.bin
```

## Comparison with Previous Prototypes

| Feature | virtio-block | virtio-block2 | virtio-block3 |
|---------|--------------|---------------|---------------|
| Protocol | Text serial | Protobuf | Protobuf |
| Sector size | Fixed 512B | Fixed 512B | Configurable |
| Serial direction | Guest → VMM | Guest → VMM | Bidirectional |
| Configuration | Hardcoded | Hardcoded | CLI options |
| Progress updates | N/A | Every 10 sectors | Configurable |
| Debug output | N/A | N/A | COM2 (plain text) |

## Files

| Path | Description |
|------|-------------|
| `vmm/src/main.rs` | VMM with CLI options and serial transmitter |
| `vmm/src/virtio/block.rs` | Virtio-block with configurable sector size |
| `guest/src/main.rs` | Guest with config reading and sector translation |
| `guest/src/serial.rs` | Bidirectional serial I/O |

## Performance

This version introduces some early performance tuning options we can play with. These are all run with an input file that is 10,000,000 512 byte "sectors".

| progress-percent | input-sector-size | output-sector-size | Wall time | Notes |
|------------------|-------------------|--------------------|-----------|-------|
| 0 | 512 | 512 | 6m 58s | This was the behaviour of virtio-block1 and virtio-block2. |
| 10 | 512 | 512 | 5m 39s | Reporting progress 1,000,000 times over an emulated serial port is slow. Who knew? |
| 10 | 1024 | 1024 | 2m 54s | But sector size has an even bigger impact. |
| 10 | 4096 | 4096 | 0m 52s | |
| 10 | 8192 | 8192 | 0m 30s | |
| 10 | 65536 | 65536 | 0m 12s | |
| 10 | 131072 | 131072 | ... | Crashes as this is more than MAX_SECTOR_SIZE. |


## Related Documentation

- [Virtio-Block Data Transfer](/components/instar/prototypes/data-transfer-virtio-block/)
- [guest-protocol crate](/components/instar/crates/guest-protocol/)
- [Virtio-Block2 Prototype](/components/instar/prototypes/virtio-block2/)
