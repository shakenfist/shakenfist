# Phase 2: Pcap packet capture

## Overview

Implement the `packet_sent()` and `packet_received()` stub
methods in `CaptureSession` to write decrypted SPICE
protocol traffic as pcap files that Wireshark can open.

## Design

### One pcap file per channel

Each SPICE channel (main, display, cursor, inputs) gets
its own pcap file. This keeps individual files manageable
and lets you open just the channel you're debugging.

```
<DIR>/
  main.pcap
  display.pcap
  cursor.pcap
  inputs.pcap
```

### Fake TCP/IP headers

Wireshark's SPICE dissector expects TCP transport. We
construct fake Ethernet + IPv4 + TCP headers using the
`etherparse` crate so packets appear as a normal TCP
stream.

Each channel maps to a unique port pair:

| Channel | Client → Server | Server → Client |
|---------|-----------------|-----------------|
| main    | 10.0.0.1:10001 → 10.0.0.2:5900 | 10.0.0.2:5900 → 10.0.0.1:10001 |
| display | 10.0.0.1:10002 → 10.0.0.2:5900 | 10.0.0.2:5900 → 10.0.0.1:10002 |
| cursor  | 10.0.0.1:10004 → 10.0.0.2:5900 | 10.0.0.2:5900 → 10.0.0.1:10004 |
| inputs  | 10.0.0.1:10003 → 10.0.0.2:5900 | 10.0.0.2:5900 → 10.0.0.1:10003 |

Source ports use 10000 + channel_type (1=main, 2=display,
3=inputs, 4=cursor).

### TCP sequence numbers

Each direction maintains an incrementing sequence number
(starting from a fixed value like 1000). After each
packet, the sequence number advances by the payload
length. This gives Wireshark enough to reconstruct the
stream for the SPICE dissector.

### Timestamps

Packet timestamps are `Instant::now() - session_start`,
converted to `Duration` and stored as pcap
seconds + microseconds.

### Thread safety

`packet_sent()` and `packet_received()` are called from
different tokio tasks (one per channel). Each channel
writes to its own pcap file, so there's no contention
between channels. However, within a channel, sent and
received packets share the same pcap writer.

Use a `Mutex<PcapChannelWriter>` per channel, keyed by
channel name in a `HashMap`. The mutex is held only for
the duration of one `write_packet()` call (microseconds).

## Implementation steps

### Step 1: Add dependencies

In `Cargo.toml`:

```toml
# Pcap capture for --capture mode
pcap-file = "2"
etherparse = "0.16"
```

### Step 2: Add PcapChannelWriter

In `src/capture.rs`, add a per-channel writer:

```rust
struct PcapChannelWriter {
    writer: pcap_file::PcapWriter<BufWriter<File>>,
    client_seq: u32,   // TCP seq for client → server
    server_seq: u32,   // TCP seq for server → client
    client_port: u16,  // unique per channel
}
```

### Step 3: Initialise writers in CaptureSession::new()

Create one `PcapChannelWriter` per known channel name
(main, display, cursor, inputs) when the session starts.
Store in `HashMap<String, Mutex<PcapChannelWriter>>`.

Each pcap file uses `DataLink::ETHERNET` so Wireshark
decodes the full TCP stack.

### Step 4: Build fake TCP/IP packets

Helper function `build_packet()` that uses `etherparse` to
construct:

1. Ethernet header (src/dst MACs can be zeros)
2. IPv4 header (src/dst IPs, total length, TTL=64)
3. TCP header (src/dst ports, seq number, ACK flag,
   window=65535)
4. Payload (the raw SPICE bytes)

Returns the complete frame as `Vec<u8>`.

Two variants: `build_sent_packet()` (client → server) and
`build_received_packet()` (server → client), which swap
the direction and use the appropriate seq counter.

### Step 5: Implement packet_sent() and packet_received()

```rust
pub fn packet_sent(&self, channel: &str, data: &[u8]) {
    if let Some(writer) = self.pcap_writers.get(channel) {
        let mut w = writer.lock().unwrap();
        let elapsed = self.start.elapsed();
        let frame = build_sent_packet(&mut w, data);
        w.writer.write_packet(
            elapsed.as_secs() as u32,
            elapsed.subsec_micros(),
            &frame,
            frame.len() as u32,
        ).ok();
    }
}
```

Similarly for `packet_received()` but using
`build_received_packet()`.

### Step 6: Flush on close

`CaptureSession::close()` should flush all pcap writers
to ensure the last packets are written.

### Step 7: Handle unknown channel names gracefully

If `packet_sent()` is called with a channel name that
doesn't have a writer (e.g. a new channel type we haven't
mapped), log a debug message and skip. Don't create
writers on-the-fly — the set of channels is fixed.

## Files to modify

| File | Changes |
|------|---------|
| `Cargo.toml` | Add `pcap-file` and `etherparse` |
| `src/capture.rs` | PcapChannelWriter, build_packet helpers, implement packet_sent/received |

No changes needed to channel handlers — the stub calls
from phase 1 will start producing output automatically.

## Testing

- Connect to `make test-qemu` with `--capture /tmp/test-cap`
- Verify `main.pcap`, `display.pcap`, `cursor.pcap`,
  `inputs.pcap` are created
- Open each in Wireshark — should show as TCP streams
  with recognisable SPICE payloads (mini-header format:
  u16 type + u32 size at the start of each message)
- Verify sent vs received direction is correct
- Verify timestamps increase monotonically

## Success criteria

- Pcap files open in Wireshark without errors
- TCP stream reconstruction works (Follow → TCP Stream)
- SPICE mini-header payloads are visible in the hex dump
- No performance regression when capture is disabled
- `pre-commit run --all-files` passes
