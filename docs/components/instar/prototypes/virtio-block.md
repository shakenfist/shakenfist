# Virtio-Block Prototype

This prototype demonstrates virtio-block device emulation in a minimal KVM VMM,
with a bare-metal guest that copies data between two block devices.

## Goals

1. **Learn virtio-block protocol**: Implement the VIRTIO 1.1 block device
   specification from scratch
2. **MMIO transport**: Use memory-mapped I/O for device configuration and
   queue notification
3. **Virtqueue handling**: Process descriptor chains for block requests
4. **File-backed devices**: Map virtio-block operations to real file I/O

## Architecture

### VMM Components

```
vmm/
├── main.rs           # CLI, VM setup, vCPU run loop
└── virtio/
    ├── mod.rs        # Module exports
    ├── mmio.rs       # MMIO register definitions, device state
    └── block.rs      # Block device implementation
```

**Key responsibilities:**

- **main.rs**: Sets up KVM VM, loads guest, handles VM exits
- **mmio.rs**: Defines MMIO register offsets and device state machine
- **block.rs**: Processes block requests, performs file I/O

### Guest Components

```
guest/
├── main.rs           # Entry point, copy logic
└── serial.rs         # Serial port output
```

**Key responsibilities:**

- **main.rs**: Initializes devices, copies sectors, reports progress
- **serial.rs**: Debug output to VMM console

## Virtio MMIO Device State Machine

```
       RESET
         │
         ▼
    ACKNOWLEDGE ─────────────────────┐
         │                           │
         ▼                           │
      DRIVER ────────────────────────┤
         │                           │
         ▼                           │
   FEATURES_OK ──────────────────────┤
         │                           │
         ▼                           │
    DRIVER_OK                        │
         │                           │
         ├──────── Normal Operation ─┤
         │                           │
         ▼                           │
      FAILED ◄───────────────────────┘
```

## Virtqueue Memory Layout

For a queue with 256 entries:

```
Base Address
  │
  ├── Descriptor Table (256 × 16 bytes = 4KB)
  │     ├── desc[0]: addr, len, flags, next
  │     ├── desc[1]: ...
  │     └── desc[255]: ...
  │
  ├── Available Ring (6 + 256 × 2 = 518 bytes)
  │     ├── flags (2 bytes)
  │     ├── idx (2 bytes)
  │     └── ring[256] (512 bytes)
  │
  └── Used Ring (6 + 256 × 8 = 2054 bytes)
        ├── flags (2 bytes)
        ├── idx (2 bytes)
        └── ring[256] (2048 bytes)
              └── elem: id (4), len (4)
```

## Block Request Descriptor Chain

```
┌────────────────┐     ┌────────────────┐     ┌────────────────┐
│   Descriptor 0 │────▶│   Descriptor 1 │────▶│   Descriptor 2 │
│    (Header)    │     │     (Data)     │     │    (Status)    │
├────────────────┤     ├────────────────┤     ├────────────────┤
│ addr: header   │     │ addr: buffer   │     │ addr: status   │
│ len: 16        │     │ len: 512       │     │ len: 1         │
│ flags: NEXT    │     │ flags: NEXT    │     │ flags: WRITE   │
│ next: 1        │     │       +WRITE*  │     │ next: 0        │
└────────────────┘     └────────────────┘     └────────────────┘
                       * WRITE flag set for
                         READ operations
```

## Request/Response Flow

### Guest Submits Request

1. Write header to DMA buffer (type, sector)
2. Set up descriptor chain (header → data → status)
3. Add first descriptor index to available ring
4. Increment available ring idx
5. Write to QUEUE_NOTIFY register

### VMM Processes Request

1. Detect MMIO write to QUEUE_NOTIFY
2. Read available ring to find new descriptors
3. Walk descriptor chain:
   - Read header (type, sector)
   - Read/write data buffer from/to file
   - Write status byte
4. Add entry to used ring
5. Increment used ring idx

### Guest Receives Response

1. Poll used ring idx until it changes
2. Read status from DMA buffer
3. For reads: copy data from DMA buffer
4. Acknowledge interrupt

## Error Handling

| Error | VMM Response | Guest Action |
|-------|--------------|--------------|
| Invalid sector | Status = IOERR | Skip sector, continue |
| Read-only write | Status = IOERR | Skip sector, continue |
| Unknown request | Status = UNSUPP | Skip sector, continue |

## Performance Considerations

This prototype prioritizes simplicity over performance:

- **Sector-by-sector**: Each sector is a separate request
- **Polling**: Guest spins on used ring instead of interrupts
- **No batching**: Requests are processed one at a time

Production implementations would use:
- Multi-sector requests
- Interrupt-based notification
- Request batching and reordering

## Future Enhancements

1. **Batched I/O**: Multiple sectors per request
2. **Interrupt delivery**: Use ioeventfd for notifications
3. **Protocol messages**: Use guest-protocol crate for structured output
4. **Error recovery**: Handle device errors gracefully
5. **Performance metrics**: Track throughput and latency
