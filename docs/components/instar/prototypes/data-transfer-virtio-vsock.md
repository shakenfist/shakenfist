# Virtio-vsock for KVM Guests

This document describes virtio-vsock, a socket-based communication mechanism
for efficient data transfer between KVM guests and the host.

## Overview

Virtio-vsock provides socket semantics (SOCK_STREAM and SOCK_SEQPACKET) over
the virtio transport layer. It enables bidirectional, multiplexed
communication between guests and the host without requiring a network stack.

## Large File Support

Virtio-vsock can handle arbitrarily large files through streaming:

- **No size limit**: Data streams continuously without needing to fit in memory
- **Sequential processing**: Ideal when input can be processed linearly
- **Bidirectional**: Can stream input while simultaneously producing output
- **Flow control**: Built-in credit system prevents buffer overflow

**Limitation**: No random access. For disk images requiring seeks (e.g., sparse
files, format conversion with non-sequential access patterns), use
[virtio-block](/components/instar/prototypes/data-transfer-virtio-block/) instead.

### When to Use for Large Files

| Pattern | Use Virtio-vsock | Use Virtio-block |
|---------|------------------|------------------|
| Sequential read → process → write | ✓ | ✓ |
| Random access required | ✗ | ✓ |
| Sparse file optimization | ✗ | ✓ |
| Bidirectional streaming | ✓ | ✗ |
| RPC-style communication | ✓ | ✗ |

## Addressing

### Context IDs (CIDs)

Each endpoint has a unique Context ID:

| CID | Meaning |
|-----|---------|
| 0 | Hypervisor (reserved) |
| 1 | Reserved |
| 2 | Host |
| 3+ | Guest VMs |

### Ports

Within each CID, communication is multiplexed across 32-bit ports, similar
to TCP/UDP ports. An address is a (CID, port) tuple.

## Protocol

### Packet Header

Every vsock packet has a 44-byte header:

```c
struct virtio_vsock_hdr {
    __le64 src_cid;       // Source context ID
    __le64 dst_cid;       // Destination context ID
    __le32 src_port;      // Source port
    __le32 dst_port;      // Destination port
    __le32 len;           // Payload length
    __le16 type;          // Socket type (STREAM=1, SEQPACKET=2)
    __le16 op;            // Operation code
    __le32 flags;         // Operation-specific flags
    __le32 buf_alloc;     // Receiver's buffer allocation
    __le32 fwd_cnt;       // Forward counter (bytes consumed)
};
```

### Operation Codes

| Op Code | Name | Description |
|---------|------|-------------|
| 1 | REQUEST | Initiate connection |
| 2 | RESPONSE | Accept connection |
| 3 | RST | Reset connection |
| 4 | SHUTDOWN | Graceful shutdown |
| 5 | RW | Read/write data |
| 6 | CREDIT_UPDATE | Update credit information |
| 7 | CREDIT_REQUEST | Request peer's credit info |

### Socket Types

- `VIRTIO_VSOCK_TYPE_STREAM` (1): Reliable byte stream (like TCP)
- `VIRTIO_VSOCK_TYPE_SEQPACKET` (2): Reliable message-based with boundaries

## Flow Control

Virtio-vsock uses credit-based flow control instead of TCP-style windowing.

### Credit Tracking

Each endpoint maintains:

- `buf_alloc`: Total receive buffer space
- `fwd_cnt`: Cumulative bytes consumed by receiver
- `tx_cnt`: Cumulative bytes transmitted
- `peer_fwd_cnt`: Last known receiver's forward counter
- `peer_buf_alloc`: Last known receiver's buffer allocation

### Available Credit Calculation

```
available_credit = peer_buf_alloc - (tx_cnt - peer_fwd_cnt)
```

The sender can only transmit up to `available_credit` bytes.

### Credit Updates

Credit information is embedded in packet headers and updated:

- Automatically piggybacked on outgoing data packets
- Explicitly via CREDIT_UPDATE operation
- On request via CREDIT_REQUEST operation

## Virtqueue Structure

Virtio-vsock uses three virtqueues:

| Queue | Index | Direction | Purpose |
|-------|-------|-----------|---------|
| RX | 0 | Host → Guest | Receive packets |
| TX | 1 | Guest → Host | Transmit packets |
| EVENT | 2 | Host → Guest | Transport events |

### RX Queue Operation

1. Guest pre-allocates receive buffers
2. Guest adds buffer descriptors to RX virtqueue
3. Host writes incoming packets to buffers
4. Host marks descriptors as used
5. Guest processes received packets

### TX Queue Operation

1. Guest builds packet (header + payload)
2. Guest adds descriptors to TX virtqueue
3. Guest kicks the queue (notifies host)
4. Host processes packet
5. Host marks descriptors as used

## VMM Implementation (Host Side)

### Required Components

1. **Virtqueue Management**
   - Allocate and initialize three virtqueues
   - Handle descriptor chains
   - Process notifications (kicks)

2. **Connection Tracking**
   - Map (CID, port) pairs to connection state
   - Manage pending/established connections
   - Handle connection teardown

3. **Flow Control**
   - Track per-connection credit state
   - Honor peer's buffer limits
   - Send credit updates

4. **Packet Routing**
   - Route packets to correct connection
   - Handle control packets (REQUEST, RST, etc.)
   - Forward data to application layer

### Data Flow (Host → Guest)

```c
// 1. Get available RX buffer from guest
desc = virtqueue_pop(rx_vq);

// 2. Build packet header
struct virtio_vsock_hdr hdr = {
    .src_cid = HOST_CID,
    .dst_cid = guest_cid,
    .src_port = host_port,
    .dst_port = guest_port,
    .len = payload_len,
    .type = VIRTIO_VSOCK_TYPE_STREAM,
    .op = VIRTIO_VSOCK_OP_RW,
    .buf_alloc = local_buf_alloc,
    .fwd_cnt = local_fwd_cnt,
};

// 3. Copy header + payload to guest buffer
copy_to_guest(desc->addr, &hdr, sizeof(hdr));
copy_to_guest(desc->addr + sizeof(hdr), payload, payload_len);

// 4. Return descriptor to guest
virtqueue_push(rx_vq, desc, sizeof(hdr) + payload_len);
virtqueue_notify(rx_vq);
```

## Guest Implementation (Bare-Metal)

### Minimal Requirements

1. **Device Initialization**
   - Detect virtio-vsock device (device ID 19)
   - Negotiate features
   - Read guest CID from config space
   - Set up virtqueues

2. **Buffer Management**
   - Allocate RX buffers
   - Add buffers to RX virtqueue
   - Manage TX buffer pool

3. **Protocol Handling**
   - Build/parse packet headers
   - Implement connection state machine
   - Handle credit updates

4. **Virtqueue Operations**
   - Add descriptors to queues
   - Process used descriptors
   - Handle notifications

### Connection Establishment

```
Guest (Connector)              Host (Listener)
       |                              |
       |-- REQUEST (op=1) ----------->|
       |                              |
       |<--------- RESPONSE (op=2) ---|
       |                              |
       |<========= DATA (op=5) ======>|
       |                              |
       |-- SHUTDOWN (op=4) ---------->|
       |<--------- SHUTDOWN (op=4) ---|
       |-- RST (op=3) --------------->|
```

### Minimal Guest Code Structure

```rust
struct VsockDevice {
    rx_vq: Virtqueue,
    tx_vq: Virtqueue,
    event_vq: Virtqueue,
    guest_cid: u64,
}

impl VsockDevice {
    fn send(&mut self, dst_cid: u64, dst_port: u32, data: &[u8]) {
        let hdr = VirtioVsockHdr {
            src_cid: self.guest_cid,
            dst_cid,
            src_port: self.local_port,
            dst_port,
            len: data.len() as u32,
            type_: VIRTIO_VSOCK_TYPE_STREAM,
            op: VIRTIO_VSOCK_OP_RW,
            buf_alloc: self.buf_alloc,
            fwd_cnt: self.fwd_cnt,
            ..Default::default()
        };

        // Build scatter-gather list
        let sg = [
            ScatterGather::new(&hdr),
            ScatterGather::new(data),
        ];

        // Add to TX queue and kick
        self.tx_vq.add(&sg);
        self.tx_vq.kick();
    }
}
```

## Advantages

- **Socket Semantics**: Familiar programming model
- **Multiplexing**: Multiple connections over single device
- **Flow Control**: Built-in credit-based backpressure
- **Bidirectional**: Full-duplex communication
- **No Network Stack**: Lower overhead than virtio-net

## Limitations

- **Complexity**: More complex than direct memory I/O
- **Overhead**: Per-packet header (44 bytes)
- **Implementation Effort**: Full protocol stack needed
- **Latency**: Virtqueue processing adds latency

## Implementation Complexity

### From Scratch

For a bare-metal guest, implementing virtio-vsock requires:

1. Virtqueue driver (~500-1000 lines)
2. Vsock protocol handling (~500-1000 lines)
3. Connection state machine (~300-500 lines)
4. Buffer management (~200-400 lines)

Total: ~1500-3000 lines for minimal implementation

### With rust-vmm Crates

The [rust-vmm](https://github.com/rust-vmm/community) ecosystem provides
crates that significantly reduce implementation effort:

**VMM Side:**

| Crate | Purpose |
|-------|---------|
| [virtio-queue](https://crates.io/crates/virtio-queue) | Virtqueue handling |
| [virtio-vsock](https://crates.io/crates/virtio-vsock) | Packet parsing and construction |
| [vm-memory](https://crates.io/crates/vm-memory) | Guest memory access |

**Guest Side:**

The [virtio-drivers](https://crates.io/crates/virtio-drivers) crate does NOT
currently include a vsock driver. Guest-side vsock implementation requires
more custom code than virtio-block.

**Revised totals with crates:**

| Component | Without Crates | With Crates |
|-----------|----------------|-------------|
| VMM | ~1500 lines | ~500 lines |
| Guest | ~1500 lines | ~700 lines* |
| **Total** | **~3000 lines** | **~1200 lines** |

*Guest requires more custom code since virtio-drivers lacks vsock support.

### Example: VMM with virtio-vsock Crate

```rust
use virtio_vsock::packet::{VsockPacket, PKT_HEADER_SIZE};
use virtio_queue::Queue;

fn handle_tx_packet(queue: &mut Queue, mem: &GuestMemoryMmap) {
    while let Some(desc_chain) = queue.pop_descriptor_chain(mem) {
        // Parse the vsock packet header
        let packet = VsockPacket::from_rx_virtq_chain(mem, &desc_chain)
            .expect("failed to parse packet");

        match packet.op() {
            VIRTIO_VSOCK_OP_REQUEST => {
                // Handle connection request
            }
            VIRTIO_VSOCK_OP_RW => {
                // Handle data
                let data = packet.data_slice();
                // Process data...
            }
            VIRTIO_VSOCK_OP_SHUTDOWN => {
                // Handle shutdown
            }
        }
    }
}
```

## Use Cases

Virtio-vsock is ideal for:

- Bidirectional communication
- Multiple independent channels
- Variable-length messages
- Streaming data
- When socket semantics are natural fit

## Comparison with Virtio-block

For Instar's disk image processing use case:

| Aspect | Virtio-vsock | Virtio-block |
|--------|--------------|--------------|
| Implementation (with crates) | ~1200 lines | ~450 lines |
| Random access | ✗ | ✓ |
| Guest driver crate | ✗ (not available) | ✓ (virtio-drivers) |
| Natural fit for disk images | ✗ | ✓ |

**Recommendation**: For disk image processing, prefer virtio-block unless you
specifically need streaming without random access.

## References

- [Linux virtio_vsock.h](https://github.com/torvalds/linux/blob/master/include/uapi/linux/virtio_vsock.h)
- [Linux vsock driver](https://github.com/torvalds/linux/blob/master/net/vmw_vsock/virtio_transport.c)
- [qemu vsock device](https://github.com/qemu/qemu/blob/master/hw/virtio/vhost-vsock.c)
- [rust-vmm virtio-vsock](https://crates.io/crates/virtio-vsock) - VMM-side packet handling
- [vhost-device-vsock](https://crates.io/crates/vhost-device-vsock) - vhost-user daemon
