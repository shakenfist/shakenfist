# SPICE Capabilities

This document describes the capability negotiation system used by the SPICE
protocol. Capabilities are exchanged during the link handshake to determine
which features both client and server support.

## Capability Negotiation

During the SPICE link handshake, both client and server exchange capability
bitmaps. A feature is only enabled if both sides advertise support for it.
Capabilities are organized into:

- **Common capabilities**: Apply to all channel types
- **Channel-specific capabilities**: Apply only to specific channels

## Capabilities Through the Proxy

Kerbside negotiates capabilities twice: as a server, answering the client's
link, and as a client, linking to the hypervisor backend. The two directions
are deliberately not symmetric.

**Offered to the client.** The link reply carries the RSA key the client
encrypts its ticket to, and the ticket is what selects the backend, so the
reply has to be sent before Kerbside knows which hypervisor the session will
reach. The capabilities in it are therefore a fixed table per channel type
(`caps::reply_channel_caps` in `rust/kerbside-proxy/src/caps.rs`), taken from
what spice-server itself advertises for that channel type — the table cites
the spice-server source for every bit — and reduced to what Kerbside relays.
The offered set is tabulated per channel in
[Proxy Architecture](/components/kerbside/proxy-architecture/#link-capabilities), and a client
whose common capabilities lack MiniHeader or AuthSelection is refused at link
time, as [the link protocol](/components/kerbside/spice/spice-link-protocol/#capability-handling)
describes.

**Forwarded to the backend.** The channel capabilities the client advertised
go to the backend verbatim, through `caps::backend_channel_caps`, which
`backend::run` in `rust/kerbside-proxy/src/backend.rs` calls for every relayed
channel. That is what lets spice-server pick image compression, stream codecs
and drawing operations for the client actually connected, rather than for
whatever the proxy's own SPICE client would ask for. The common capabilities
are forwarded too, except for the authentication bits, which describe
Kerbside's own ticket exchange with the backend rather than the client's:
AuthSpice is set and AuthSASL cleared, since the backend authenticates
Kerbside and Kerbside only performs SPICE ticket authentication. Forwarding a
client's AuthSASL would make a SASL-enabled spice-server skip the ticket key
pair Kerbside's ticket is encrypted to, for which `caps.rs` cites the
spice-server source. The experimental transcoding feature is the one
exception: a transcoded display channel is read by the proxy's renderer
instead of by the client, so it is dialled with the renderer's capabilities.

### The Reply Mask Is Not a Security Boundary

The reply table bounds what Kerbside advertises as a server. It does not bound
what the session can carry. Because the client's channel capabilities reach
the backend unmasked, a client can negotiate a spice-server feature Kerbside
never offered, and spice-server will then send the messages that feature
enables. The display channel is the standing example: the reply does not offer
SizedStream, but a client that advertises it has it forwarded, and the
firewall admits the display channel's `stream_data_sized` (316), which
only that capability enables. A unit test in `caps.rs`
(`unoffered_client_cap_still_admits_its_server_message`) pins that case, so
this is checked rather than asserted.

Masking the forwarded capabilities down to the offered set was considered and
declined, because it would be wrong on both counts:

- **It would break real clients.** A capability is effective only where both
  ends advertise it, which is exactly why forwarding the client's set is what
  makes spice-server encode for the client connected. Ryll's own default
  display set (`DEFAULT_DISPLAY` in its `shakenfist-spice-protocol`) asks for
  SizedStream, Composite, A8Surface, LZ4Compression, MultiCodec and the MJPEG
  and H.264 codec bits, none of which the reply table offers. Clamping them
  would drop every guest to a slower software render path and give up LZ4 on
  static regions — on every session, not some unusual one.
- **It would buy no enforcement.** The
  [firewall's](/components/kerbside/proxy-architecture/#the-spice-firewall) L1 allowlist
  classifies by opcode and never by the negotiated capability set, and on a
  channel whose grammar it models it is fail-closed: an opcode that grammar
  does not hold is a violation, which the firewall observes or terminates on
  according to the configured verdict. A capability cannot talk a message past
  it. Message *bodies* are not checked at all, but that is a separate layer
  and the reply mask would not change it either way.

Two consequences do follow from the asymmetry, and neither of them is about
containment.

The first is the one to watch. Because the client's capabilities reach the
backend unmasked, spice-server may send an opcode gated behind a capability
Kerbside never offered, and fail-closed means an opcode the allowlist does not
model would terminate a *legitimate* session. That is safe only because the L1
table is derived from ryll's complete per-channel message-type tables rather
than from the reply table, so the opcodes an unoffered capability can enable
are modelled already. If one ever is not, the fix is to model the opcode in
the table; it is never to widen a verdict so the traffic passes.

The second is resource cost on the hypervisor. Forwarding a capability
Kerbside never offered also lets a client turn on backend work — a video
encoder, for instance — that the proxy did not advertise, so what a session
costs the hypervisor is set by what the client asks for and not by the reply
table.

## Common Capabilities

These capabilities apply to all SPICE channels.

| Bit | Capability | Description |
|----:|------------|-------------|
| 0 | AuthSelection | Client can select authentication mechanism |
| 1 | AuthSpice | SPICE ticket-based authentication supported |
| 2 | AuthSASL | SASL authentication supported |
| 3 | MiniHeader | Use compact 6-byte message headers instead of 16-byte |

### Mini Header Format

When both sides support `MiniHeader`, messages use a compact format:

**Standard Header (16 bytes):**
```
Offset  Size  Type    Field
------  ----  ------  -----------
0       8     uint64  serial
8       2     uint16  type
10      4     uint32  size
14      4     uint32  sub_list
```

**Mini Header (6 bytes):**
```
Offset  Size  Type    Field
------  ----  ------  -----------
0       2     uint16  type
2       4     uint32  size
```

## Main Channel Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
| 0 | SemiSeamlessMigrate | Semi-seamless VM migration support |
| 1 | NameAndUuid | Server sends VM name and UUID to client |
| 2 | AgentConnectedTokens | Token-based agent connection tracking |
| 3 | SeamlessMigrate | Seamless VM migration support |

## Display Channel Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
| 0 | SizedStream | Stream data messages include dimensions |
| 1 | MonitorsConfig | Multi-monitor configuration support |
| 2 | Composite | Composite drawing operations supported |
| 3 | A8Surface | 8-bit alpha-only surfaces supported |
| 4 | StreamReport | Stream quality/performance reporting |
| 5 | LZ4Compression | LZ4 image compression supported |
| 6 | PrefCompression | Client compression preference |
| 7 | GLScanout | GPU acceleration via DMA-buf scanout |
| 8 | MultiCodec | Multiple video codec support |
| 9 | CodecMJPEG | MJPEG video codec |
| 10 | CodecVP8 | VP8 video codec |
| 11 | CodecH264 | H.264/AVC video codec |
| 12 | PrefVideoCodecType | Client video codec preference |
| 13 | CodecVP9 | VP9 video codec |
| 14 | CodecH265 | H.265/HEVC video codec |
| 15 | GLScanout2 | GL scanout with multiple planes |

### GL Scanout

When `GLScanout` is supported, the server can send GPU-rendered frames directly
to the client using DMA-buf file descriptors, bypassing software rendering.

**GL Scanout Message (ID 133):**
```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     int32   fd (DMA-buf file descriptor)
4       4     uint32  width
8       4     uint32  height
12      4     uint32  stride
16      4     uint32  drm_fourcc (DRM pixel format)
20      4     uint32  flags (1=Y0_TOP)
```

## Inputs Channel Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
| 0 | KeyScancode | Raw scancode input supported (key_scancode message) |

## Playback Channel Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
| 0 | CELT_0_5_1 | CELT 0.5.1 audio codec (deprecated) |
| 1 | Volume | Per-channel volume control |
| 2 | Latency | Audio latency reporting |
| 3 | Opus | Opus audio codec |

## Record Channel Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
| 0 | CELT_0_5_1 | CELT 0.5.1 audio codec (deprecated) |
| 1 | Volume | Per-channel volume control |
| 2 | Opus | Opus audio codec |

## SpiceVMC Channel Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
| 0 | DataCompressLZ4 | LZ4 compression for VMC data |

## Capability Bitmap Encoding

Capabilities are transmitted as a bitmap where each bit position corresponds
to a specific capability. The bitmap is sent as an array of 32-bit words.

```python
# Example: Check if capability N is supported
def has_capability(caps_bitmap, cap_num):
    word_index = cap_num // 32
    bit_index = cap_num % 32
    return (caps_bitmap[word_index] >> bit_index) & 1

# Example: Set capability N
def set_capability(caps_bitmap, cap_num):
    word_index = cap_num // 32
    bit_index = cap_num % 32
    caps_bitmap[word_index] |= (1 << bit_index)
```

## Recommended Capability Set

For a modern SPICE client, the recommended minimum capabilities are:

**Common:**
- AuthSelection (0)
- AuthSpice (1)
- MiniHeader (3)

**Display:**
- SizedStream (0)
- MonitorsConfig (1)
- StreamReport (4)
- LZ4Compression (5)
- MultiCodec (8)
- CodecMJPEG (9)
- CodecVP8 (10)
- CodecH264 (11)

**Playback/Record:**
- Volume (1)
- Opus (3/2)

**Inputs:**
- KeyScancode (0)

## Related Documentation

- [Link Protocol](/components/kerbside/spice/spice-link-protocol/) - Capability exchange during handshake
- [Protocol Overview](/components/kerbside/spice/protocol-overview/) - High-level protocol structure
- [Channel Protocols](/components/kerbside/spice/channel-protocols/) - Per-channel message formats
- [Keyboard Scancodes](/components/kerbside/spice/scancodes/) - KeyScancode capability usage
- [Compression Protocols](/components/kerbside/spice/compression-protocols/) - LZ4/GLZ compression details
- [Proxy Architecture](/components/kerbside/proxy-architecture/#link-capabilities) - The
  capabilities Kerbside offers per channel, and the opcode allowlist that does
  the enforcing the reply mask does not
