# SPICE Protocol Overview

This document provides a high-level overview of the SPICE (Simple Protocol for
Independent Computing Environments) protocol as implemented in Kerbside.

## Introduction

SPICE is a remote desktop protocol developed by Red Hat for virtual machine
remote console access. It provides high-performance display, input, and device
redirection capabilities. Kerbside implements a SPICE protocol proxy that sits
between SPICE clients (such as virt-viewer or remote-viewer) and hypervisors
running QEMU/KVM with SPICE support.

## Protocol Fundamentals

### Connection Model

SPICE uses a multi-channel architecture where each type of functionality has its
own dedicated channel:

| Channel Type | ID | Purpose | Kerbside Support |
|--------------|---:|---------|------------------|
| main         |  1 | Session control, channel negotiation | Full |
| display      |  2 | Display updates, video streaming | Full |
| inputs       |  3 | Keyboard and mouse input | Full |
| cursor       |  4 | Cursor shape and position | Full |
| playback     |  5 | Audio playback | Proxy only |
| record       |  6 | Audio recording | Proxy only |
| tunnel       |  7 | Obsolete | N/A |
| smartcard    |  8 | Smart card redirection | Proxy only |
| usbredir     |  9 | USB redirection | Proxy only |
| port         | 10 | Port/device redirection (VMC protocol) | Full |
| webdav       | 11 | WebDAV file sharing | Proxy only |

### Connection Establishment

1. **Main Channel Connection**: The client first establishes a main channel
   connection, which receives a unique session ID from the server.

2. **Channel Negotiation**: The server sends a list of available channels.
   The client then opens additional connections for each channel it wants to
   use, referencing the session ID.

3. **Bidirectional Communication**: Each channel operates as a full-duplex
   communication path with its own message types.

### Protocol Versioning

SPICE protocol uses major and minor version numbers:

- **Magic**: `REDQ` (0x51444552 little-endian)
- **Major Version**: 2
- **Minor Version**: 2

All multi-byte integers use little-endian byte order unless otherwise noted.

## Message Structure

SPICE supports two message header formats: the full `SpiceDataHeader` and the
compact `SpiceMiniDataHeader`. The mini header is used when both sides advertise
the `MiniHeader` capability.

### Full Data Header (SpiceDataHeader)

```
+------------------+------------------+------------------+------------------+------------------+
|      Serial      |   Message Type   |   Message Size   |     Sub List     |  Message Payload |
|    (8 bytes)     |    (2 bytes)     |    (4 bytes)     |    (4 bytes)     |   (variable)     |
+------------------+------------------+------------------+------------------+------------------+
```

| Field | Size | Description |
|-------|------|-------------|
| serial | UINT64 | Message sequence number, starts at 1, increments per message |
| type | UINT16 | Message type identifier |
| size | UINT32 | Payload length in bytes |
| sub_list | UINT32 | Offset to sub-message list (0 if none) |

### Mini Data Header (SpiceMiniDataHeader)

When the `MiniHeader` capability (bit 3) is negotiated, the compact header is
used:

```
+------------------+------------------+------------------+
|   Message Type   |   Message Size   |  Message Payload |
|    (2 bytes)     |    (4 bytes)     |   (variable)     |
+------------------+------------------+------------------+
```

| Field | Size | Description |
|-------|------|-------------|
| type | UINT16 | Message type identifier |
| size | UINT32 | Payload length in bytes |

**Note**: Kerbside uses the mini header format, which is the common case for
modern SPICE implementations.

### Sub-Messages

Messages can contain sub-messages when `sub_list` is non-zero:

```
SpiceSubMessageList:
  UINT16 count          - Number of sub-messages
  UINT32[] offsets      - Offsets to each sub-message

SpiceSubMessage:
  UINT16 type           - Sub-message type
  UINT32 size           - Sub-message payload size
  ...    payload        - Sub-message data
```

## Capabilities Negotiation

During the handshake, both client and server exchange capability bitmaps.
Capabilities are stored as 32-bit words, with each bit representing a feature.

### Common Capabilities

| Bit | Capability      | Description |
|----:|-----------------|-------------|
|   0 | AuthSelection   | Supports authentication mechanism selection |
|   1 | AuthSpice       | Supports SPICE native authentication |
|   2 | AuthSASL        | Supports SASL authentication |
|   3 | MiniHeader      | Supports mini header format (reduced overhead) |

### Main Channel Capabilities

| Bit | Capability             | Description |
|----:|------------------------|-------------|
|   0 | SemiSeamlessMigrate    | Supports semi-seamless VM migration |
|   1 | NameAndUUID            | Supports VM name and UUID reporting |
|   2 | AgentConnectedTokens   | Supports agent connection tokens |
|   3 | SeamlessMigrate        | Supports seamless VM migration |

### Display Channel Capabilities

| Bit | Capability             | Description |
|----:|------------------------|-------------|
|   0 | InvalidateList         | Supports invalidate list messages |
|   1 | StreamReportSupport    | Supports stream reporting |
|   2 | MultiCodec             | Supports multiple video codecs |
|   3 | CodecMJPEG             | Supports MJPEG video codec |
|   4 | CodecVP8               | Supports VP8 video codec |
|   5 | CodecH264              | Supports H.264 video codec |
|   6 | A8Surface              | Supports A8 surface format |
|   7 | StreamReport           | Stream report enabled |
|   8 | LZ4Compression         | Supports LZ4 image compression |
|   9 | CodecVP9               | Supports VP9 video codec |
|  10 | GLScanout              | Supports GL scanout |
|  11 | CodecH265              | Supports H.265/HEVC video codec |

### Playback Channel Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
|   0 | CELT_0_5_1 | CELT 0.5.1 codec (deprecated) |
|   1 | Volume     | Volume control support |
|   2 | Latency    | Latency reporting |
|   3 | Opus       | Opus audio codec |

### Record Channel Capabilities

| Bit | Capability | Description |
|----:|------------|-------------|
|   0 | CELT_0_5_1 | CELT 0.5.1 codec (deprecated) |
|   1 | Volume     | Volume control support |
|   2 | Opus       | Opus audio codec |

### Inputs Channel Capabilities

| Bit | Capability   | Description |
|----:|--------------|-------------|
|   0 | KeyScancode  | Supports raw keyboard scancodes |

### SPICEVMC Channel Capabilities (Port/USB)

| Bit | Capability       | Description |
|----:|------------------|-------------|
|   0 | DataCompression  | Supports compressed VMC data |

## Security

SPICE supports multiple security mechanisms:

1. **TLS/SSL Encryption**: All traffic between client and server can be
   encrypted using TLS. Kerbside requires TLS for all client connections.

2. **Password Authentication**: Passwords are encrypted using RSA-OAEP with
   SHA-1 before transmission. The server provides its public key during
   handshake.

3. **Token-Based Authentication**: Kerbside extends this by using time-limited
   tokens instead of static passwords, providing an additional security layer.

## Error Codes

The protocol defines standard error codes:

| Code | Name              | Description |
|-----:|-------------------|-------------|
|    0 | ok                | Success |
|    1 | error             | Generic error |
|    2 | invalid_magic     | Invalid protocol magic number |
|    3 | invalid_data      | Malformed data received |
|    4 | version_mismatch  | Incompatible protocol version |
|    5 | need_secured      | TLS connection required |
|    6 | need_unsecured    | Non-TLS connection required |
|    7 | permission_denied | Authentication failed |
|    8 | bad_connection_id | Invalid session/connection ID |
|    9 | channel_unavailable | Requested channel not available |

## Common Message Types

Certain message types are shared across all channels:

### Client to Server (Common)

| ID | Message              | Description |
|---:|----------------------|-------------|
|  1 | ack_sync             | Acknowledge sync with generation counter |
|  2 | ack                  | Acknowledge message receipt |
|  3 | pong                 | Response to ping |
|  4 | migrate_flush_mark   | Migration marker |
|  5 | migrate_data         | Migration data transfer |
|  6 | disconnecting        | Client disconnecting |

### Server to Client (Common)

| ID | Message              | Description |
|---:|----------------------|-------------|
|  1 | migrate              | Migration request |
|  2 | migrate_data         | Migration data transfer |
|  3 | set_ack              | Configure acknowledgement window |
|  4 | ping                 | Ping request |
|  5 | wait_for_channels    | Wait for channel traffic |
|  6 | disconnecting        | Server disconnecting |
|  7 | notify               | Notification message |

## External References

- [SPICE Protocol Documentation](https://www.spice-space.org/spice-protocol.html)
  (incomplete but useful)
- [SPICE Protocol Source](https://gitlab.freedesktop.org/spice/spice-protocol)
- [SPICE Server Source](https://gitlab.freedesktop.org/spice/spice)
- [USB Redirection Protocol](https://www.spice-space.org/usbredir.html)

## Related Documentation

- [Link Protocol](/components/kerbside/spice-link-protocol.md) - Connection handshake details
- [Channel Protocols](/components/kerbside/channel-protocols.md) - Per-channel message formats
- [USB Redirection](/components/kerbside/usb-redirection.md) - USB device redirection protocol
