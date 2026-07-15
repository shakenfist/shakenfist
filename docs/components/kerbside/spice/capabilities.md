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
