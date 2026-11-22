# Fetch Protocol Crate

The `fetch-protocol` crate defines the channel between instar's privileged
host process and a separate, untrusted fetcher process that serves image
bytes to it.

## Overview

The host process holds `/dev/kvm` and the user's credentials. The fetcher
holds whatever it takes to produce bytes, and is assumed hostile. The two are
joined by one `AF_UNIX` / `SOCK_SEQPACKET` socket and nothing else: no shared
memory, no filesystem path, no serialisation library. The crate is designed
for:

- **A small parse surface**: the privileged side parses four fixed-size
  headers followed by opaque bytes, and nothing more
- **Bounds checking before reading**: every decode checks the datagram's
  length against the header's own size before it reads a single field
- **Offset-addressed reads**: capacity is discovered at init, then the host
  asks for `length` bytes at `offset`
- **A closed error vocabulary**: a failure is a number from a fixed set, never
  text from the peer

Today the only thing that speaks this protocol is `instar-fetch`, a fetcher
that serves one already-open file. Nothing in the `instar` binary invokes it
yet.

## Location

```
crates/fetch-protocol/
├── Cargo.toml
└── src/
    ├── lib.rs            # Crate overview and re-exports
    ├── wire.rs           # The four headers, Status, block-size negotiation
    ├── socket.rs         # The SOCK_SEQPACKET socketpair wrapper
    └── client.rs         # FetchClient: the host side's reader
fetch/                    # The instar-fetch binary (package instar-fetch)
├── Cargo.toml
└── src/
    └── main.rs
```

## Wire Format

Every multi-byte field is **little-endian**, written with `to_le_bytes` and
read with `from_le_bytes`. Each header is `#[repr(C)]` with no implicit
padding. No datagram is ever reinterpreted as one of these structs: decoding
reads one field at a time and validates as it goes, which is what lets the
status field be a real enum rather than an arbitrary integer.

| Message | Size | Fields (offset in bytes) |
|---------|------|--------------------------|
| InitRequest | 16 | magic 0, version 4, max_datagram 8, flags 12 |
| InitReply | 32 | magic 0, version 4, status 8, block_size 12, capacity 16, flags 24 |
| ReadRequest | 32 | opcode 0, length 4, id 8, offset 16, flags 24 |
| ReadReply | 24 | opcode 0, status 4, id 8, length 16, flags 20 |

All fields are `u32` except `capacity`, `flags` (InitReply and ReadRequest),
`id` and `offset`, which are `u64`.

**The layout tests in `wire.rs` are the authority.** They assert `size_of`
and the offset of every field of every header against these numbers, so a
reordering that would change the wire format breaks a test. If this table and
those tests ever disagree, the tests are right.

Further rules that apply to every message:

- The **magic** is the little-endian encoding of the bytes `INFT`, so that it
  reads as text in a hexdump of the datagram.
- **`flags` is reserved and must be zero** in every message. A peer that sets
  a bit this build does not know is refused rather than guessed at.
- **Trailing bytes are an error.** InitRequest, InitReply and ReadRequest are
  exactly their header length. A ReadReply is its 24-byte header followed by
  exactly `length` bytes of payload in the same datagram.

## Message Types

### Host to fetcher

| Message | Purpose | Fields |
|---------|---------|--------|
| InitRequest | First datagram; says who is speaking and what the host can carry | magic, version, max_datagram, flags |
| ReadRequest | Asks for a range of the source | opcode, length, id, offset, flags |

### Fetcher to host

| Message | Purpose | Fields |
|---------|---------|--------|
| InitReply | Accepts or refuses the init; settles block size and reports capacity | magic, version, status, block_size, capacity, flags |
| ReadReply | Answers one request, matched by id | opcode, status, id, length, flags, then payload |

The only opcode is `1` (read), used in both ReadRequest and ReadReply. Values
2 and 3 are reserved for a future flush and a capacity re-query; a request
carrying either is refused today.

### Status vocabulary

The status is a closed set owned by the privileged side. It is rich enough to
act on, and it cannot carry a payload: the sentence a user reads is written by
the host, from a number.

| Value | Name | Meaning |
|-------|------|---------|
| 0 | Ok | The request was satisfied in full |
| 1 | BadRequest | The request was malformed: a reserved field set, a length past the negotiated maximum, an offset and length that overflow |
| 2 | Unsupported | Well formed, but asks for something the fetcher does not implement, such as an unknown opcode |
| 3 | OutOfRange | The range lies outside the capacity the fetcher advertised |
| 4 | ShortRead | The source ended before the requested length; the reply carries the bytes that did exist |
| 5 | IoError | Reading the source failed |
| 6 to 11 | reserved | Documented, not defined |

Values 6 to 11 are reserved, in order, for `NOT_FOUND`, `FORBIDDEN`,
`RANGE_UNSUPPORTED`, `SOURCE_CHANGED`, `NETWORK` and `TIMEOUT`, for a fetcher
that speaks to something further away than a file. They are **refused today**:
a peer sending 6 to 11 is treated exactly like one sending any other unknown
value, as a protocol error. Adding one is a deliberate change to the `Status`
enum and to the sentence that renders it.

The client maps each non-Ok status to an `io::ErrorKind` chosen by this crate:
BadRequest and OutOfRange to `InvalidInput`, Unsupported to `Unsupported`,
ShortRead to `UnexpectedEof`, IoError to `Other`. A reply that cannot be
decoded, or that names an unknown status or id, is `InvalidData`.

## Init Negotiation

The host sends an InitRequest first. It carries the protocol version and
`max_datagram`, the largest single datagram the host's end can send or
receive. The fetcher replies with an InitReply carrying its own version, a
status, the settled `block_size` (the most payload one ReadReply may carry),
and `capacity`, the size of the source in bytes.

The version must match exactly; a mismatch is fatal and names both versions,
because a development build talking to a packaged fetcher is a mistake that
will happen. The magic is checked on both init messages.

**What a side can offer comes from the socket buffer the kernel actually
granted, not from a constant or a sysctl.** Creating a socket pair asks the
kernel for buffers large enough to carry the largest block the protocol
permits, on both ends, and the fetcher asks again for its own end in case it
was handed a socket it did not create. Each side then reads back what it was
given. The kernel clamps a request to `wmem_max` and *then* doubles it, and
charges 32 bytes of overhead per datagram, so the largest datagram a side can
manage is `granted - 32` -- a figure that cannot be derived from the sysctl by
inspection, which is why it is measured rather than assumed. A caller that
wants a smaller buffer shrinks it after creating the pair, and nothing puts it
back.

The fetcher then settles the block size:

1. take the smaller of the two sides' maximum datagram sizes,
2. deduct the 24-byte ReadReply header,
3. cap the result at the 65536-byte default block, and
4. round down to a multiple of 512 so a split read stays sector-aligned.

Anything below 512 bytes is refused rather than used.

The cap is the default block rather than the 262144-byte maximum the wire
format allows, so a well-provisioned pair settles on 65536 rather than on the
largest block it could carry. A larger block buys nothing while a guest's own
request is at most that size: it would only change how a larger read is
divided, and would hold a receive buffer four times the size for payloads
that never arrive. Reading ahead is a matter of several requests being in
flight, not of one request being bigger. The 262144-byte maximum is what the
headers and the requested socket buffers are built to reach, so raising the
default later is a change to one constant rather than to the protocol.

As a measured example: on the host this was developed on, a pair is granted
425984 bytes an end, which is a 425952-byte datagram ceiling -- comfortably
more than the default block needs, so the negotiation settles on 65536. A
socket deliberately shrunk to ask for only 4096 is granted 8192, which is an
8160-byte ceiling and a 7680-byte block, and reads larger than that are split.
The client accepts the fetcher's proposed block only if it satisfies every
rule the negotiation itself follows: at least 512, a multiple of 512, and no
larger than what this side could carry -- which, being capped at the default
block, is the figure a refusal names. The alignment is checked rather than
assumed, because it is what keeps a split read on sector boundaries and the
fetcher is the untrusted side: a peer proposing 513 would otherwise impose
both a misaligned split and a round trip for every 513 bytes.

A read larger than the negotiated block is split by the client into several
requests. **The timeout bounds the whole `read_at`, not each datagram.** A
split read is several round trips, and a peer answering each one just inside
a per-datagram timeout could hold the privileged process indefinitely
without ever tripping it, which is the hang the partition exists to prevent.
Each block is given what remains of the call's deadline, and a deadline that
runs out is `TimedOut` naming the offset the peer stopped at.

The deadline covers both directions of each round trip. A peer that stops
answering blocks the reply, and a peer that stops reading blocks the request
itself once its receive buffer is full, which is the same hang reached from
the other end; `SO_SNDTIMEO` and `SO_RCVTIMEO` are both set from what remains
of the deadline. Below one microsecond a `timeval` rounds to zero, which the
kernel reads as no timeout at all, so the socket refuses such a value rather
than quietly removing the bound -- and the client refuses a whole-read
deadline shorter than the window one round trip is given, which would fail
every read before sending anything.

## Framing

`SOCK_SEQPACKET` preserves message boundaries in the kernel, so there is no
length prefix: one `recv` is one message, or an error. The host never has to
reassemble a stream or recover from a framing desynchronisation, which is the
failure a byte-stream channel would add to a protocol whose peer is hostile.

Rust's standard library cannot create such a socket. `UnixStream`'s
constructors hardcode `SOCK_STREAM`, `UnixDatagram`'s hardcode `SOCK_DGRAM`,
and nothing takes a socket type. The choice was a third-party socket crate in
the process that holds `/dev/kvm`, or a few `libc` calls behind a type that
owns its descriptor. This crate does the latter: `SeqPacketSocket::pair()` is
a `libc::socketpair(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0)` behind a safe
wrapper that closes on drop, in the same shape as the VMM's ioeventfd
wrapper.

It is a socketpair rather than a socket bound to a path because the host is
the fetcher's parent: there is no name for anything else to connect to, no
directory to choose, and nothing for a filesystem sandbox to have to permit.

## No Free-Form Text

There is no free-form text anywhere on the wire. A failure is a value from
`Status`, and the sentence a user eventually reads is written by the host from
that value. A reason string from the untrusted side would be
attacker-influenced text heading for a terminal, and would need sanitising,
length bounds and encoding validation in the one process that can least afford
a parsing mistake. Every `ProtocolError` carries numbers this side computed or
read, never bytes from the peer. The fetcher's own stderr remains available
for diagnostics that never become part of the protocol.

That stderr carries one line per refused or non-`Ok` request, so its volume
is set by the peer rather than by the fetcher. Nothing reads it today -- the
only peer is the privileged side itself -- but whatever eventually routes it
into a log owns that rate, and should bound or sample it rather than assume
the fetcher is quiet.

## Usage

### Host side

```rust
use fetch_protocol::{FetchClient, SeqPacketSocket};

let (host_end, fetcher_end) = SeqPacketSocket::pair()?;
// ... hand fetcher_end and an open file to instar-fetch as descriptor numbers ...
let mut client = FetchClient::new(host_end)?;   // performs the init exchange
let mut buf = vec![0u8; 4096];
client.read_at(0, &mut buf)?;                    // fills buf or returns an error
```

`read_at` has the same signature and contract as the VMM's local backing
store: fill the whole buffer or return an error. That includes the end of the
source. A block device's capacity is its image's length rounded up to a whole
sector, so a guest reading the last sector of an image that is not a sector
multiple legitimately reads past the end of the file; both stores answer the
overhang with zeros, and the client produces those zeros itself rather than
asking the fetcher for bytes it would refuse as `OutOfRange`. A read starting
past the capacity is zeros and no round trip at all.

The capacity is the size the fetcher advertised at init, and it is not
re-read. A source that *grows* afterwards therefore reads back as zeros past
the advertised size rather than as the new bytes, and a source that *shrinks*
fails with `UnexpectedEof` from the fetcher's `ShortRead`. Both are what the
VMM's local backing store does with a file that changes under it, which is
the point: the two stores answer a changing source alike, so whatever sits
above them cannot tell which one it has. The client matches each
reply to its request by id rather than assuming order, and a reply for an id
it did not ask for is an error. The first failure ends the client, because the socket
cannot be resynchronised without knowing how many answers are outstanding.

### Fetcher side

```
instar-fetch <socket-fd> <file-fd>
```

Both arguments are descriptor numbers the parent has already opened. The
fetcher takes no paths and opens nothing, so it can in principle be given no
filesystem access at all. It is single-threaded, answers each read with
`pread` on the file, and never panics on anything the peer sends: a malformed
request is answered with a status, or noted on stderr. It exits 0 when the
peer closes the socket and for `--help`, 2 for a usage error, and 1 for
anything else -- including a descriptor that is not a regular file, which is
refused before the init reply rather than failing every request.

A datagram longer than the fetcher's request buffer is refused by its id like
any other malformed request. `MSG_TRUNC` reports the length that was really
sent while the kernel keeps only the head of it, and the head is where the id
lives, so the remainder being gone is no reason to leave the peer without an
answer.

One malformed shape is answered with silence: a datagram too short to carry a
request id. A reply is addressed by the id it carries back, so there is
nothing to address, and an invented id could be matched by a peer that had
lost framing to a request that really is outstanding. The client's read
deadline is what covers it, and no correct client can produce the shape,
since the request header is fixed-size.

## Dependencies

- `libc`: the `socketpair` and `setsockopt` calls. This is the crate's only
  dependency.

## Current Status

The only thing that speaks this protocol today is `instar-fetch` serving one
open file. It is built and packaged, but nothing in the `instar` binary
starts it or links the client. Tests cover the layout, each malformed shape
decoding to a typed error, an end-to-end comparison of the fetcher against
`pread`, a set of hostile peers, and a fuzz target on every decode entry
point.

## Limitations

- **One request in flight.** Replies are matched by id, so the protocol
  permits out-of-order replies, but the client keeps one request outstanding.
- **Read only.** The flush and capacity re-query opcodes are reserved, not
  implemented.
- **Capacity is fixed at init.** There is no way to learn that the source
  changed size.
- **Block size is capped at 64 KiB by the negotiation**, however much socket
  buffer the kernel grants, and at 256 KiB by the wire format. Raising the
  first is a change to one constant; raising the second is a change to the
  protocol.

## Why Not NBD, and Why Not a Virtqueue

The model is borrowed from virtio-blk and NBD: capacity discovered at init,
offset-addressed reads, a typed status, request ids. Neither mechanism is.

**Not a virtqueue.** The repository already depends on `virtio-queue` and
`vm-memory` and has eventfd notification, and `SCM_RIGHTS` could pass a memfd
over the socket. It would work, but it would hand the untrusted fetcher a
writable mapping shared with the privileged process, where whoever controls
the ring controls indices the VMM dereferences. That is the memory-corruption
channel this partition exists to remove, and defending it means writing
adversarial-ring validation in privileged code to save a copy that does not
matter: a 256 KiB `memcpy` costs tens of microseconds.

**Not NBD.** NBD has exactly this shape, and `nbdkit` is an off-the-shelf
implementation of much of it. But speaking real NBD means parsing a negotiated,
extensible protocol (option haggling, structured replies) from an untrusted
peer in the privileged process. A few fixed-size headers are a far smaller
surface, small enough to fuzz the whole decoder. The shape is borrowed; the
protocol is not linked.

**Not protobuf, unlike the guest serial channel.** [guest-protocol](/components/instar/crates/guest-protocol/)
uses protobuf because guest results are variable-shape messages with optional
fields. This channel carries fixed-size headers and raw bytes, so a
serialisation library would add a dependency and a decoder with nothing to
offer in return.
