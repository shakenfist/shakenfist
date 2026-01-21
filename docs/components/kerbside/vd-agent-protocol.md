# VD Agent Protocol

This document describes the SPICE VD (Virtual Desktop) Agent protocol, which
enables advanced guest integration features such as clipboard sharing, file
transfer, and display configuration.

## Overview

The VD Agent is a guest-side component that communicates with the SPICE client
through the main channel's agent messages. It provides:

- Clipboard sharing between host and guest
- File transfer (drag-and-drop)
- Display/monitor configuration
- Mouse mode synchronization
- Audio volume synchronization

**Note**: Kerbside proxies agent traffic transparently without decoding. This
documentation is provided for reference and debugging purposes.

## Transport

Agent messages are carried over the main channel using:
- Server to client: `AGENT_DATA` (ID 109), `AGENT_TOKEN` (ID 110)
- Client to server: `AGENT_START` (ID 106), `AGENT_DATA` (ID 107), `AGENT_TOKEN` (ID 108)

## Agent Message Header

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  protocol (protocol identifier)
4       4     uint32  type (message type)
8       8     uint64  opaque (implementation-specific)
16      4     uint32  size (payload size)
20      var   bytes   data (payload)
```

Maximum data size: 2048 bytes per message (larger data is chunked)

## Message Types

| ID | Message                      | Direction | Description |
|---:|------------------------------|-----------|-------------|
|  1 | MOUSE_STATE                  | S->A      | Mouse position and button state |
|  2 | MONITORS_CONFIG              | C->A      | Display/monitor configuration |
|  3 | REPLY                        | A->*      | Agent acknowledgment |
|  4 | CLIPBOARD                    | Both      | Clipboard data transfer |
|  5 | DISPLAY_CONFIG               | C->A      | Display settings |
|  6 | ANNOUNCE_CAPABILITIES        | Both      | Feature capability declaration |
|  7 | CLIPBOARD_GRAB               | Both      | Clipboard ownership claim |
|  8 | CLIPBOARD_REQUEST            | Both      | Request clipboard data |
|  9 | CLIPBOARD_RELEASE            | Both      | Release clipboard ownership |
| 10 | FILE_XFER_START              | C->A      | Initiate file transfer |
| 11 | FILE_XFER_STATUS             | A->C      | File transfer status |
| 12 | FILE_XFER_DATA               | C->A      | File transfer data chunk |
| 13 | CLIENT_DISCONNECTED          | S->A      | Client disconnection notification |
| 14 | MAX_CLIPBOARD                | Both      | Maximum clipboard size |
| 15 | AUDIO_VOLUME_SYNC            | Both      | Audio volume synchronization |
| 16 | GRAPHICS_DEVICE_INFO         | C->A      | Device/monitor mapping |

Direction key: S=Server, C=Client, A=Agent, Both=Bidirectional

## Clipboard Protocol

### Clipboard Formats

| ID | Format     | Description |
|---:|------------|-------------|
|  1 | UTF8_TEXT  | UTF-8 encoded text |
|  2 | IMAGE_PNG  | PNG image data |
|  3 | IMAGE_BMP  | BMP image data |
|  4 | IMAGE_TIFF | TIFF image data |
|  5 | IMAGE_JPG  | JPEG image data |
|  6 | FILE_LIST  | WebDAV file paths |

### CLIPBOARD_GRAB (ID 7)

Announces clipboard ownership and available formats.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4*N   uint32  format_types (array of available formats)
```

### CLIPBOARD_REQUEST (ID 8)

Requests clipboard data in a specific format.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  type (requested format)
```

### CLIPBOARD (ID 4)

Transfers clipboard data.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  type (data format)
4       var   bytes   data
```

### CLIPBOARD_RELEASE (ID 9)

Releases clipboard ownership. No payload.

## Monitor Configuration

### MONITORS_CONFIG (ID 2)

Configures virtual display monitors.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  num_monitors
4       4     uint32  flags
8       N*28  struct  monitors (VDAgentMonConfig array)
```

Monitor configuration flags:
| Bit | Flag | Description |
|----:|------|-------------|
| 0 | USE_POS | Use X/Y position fields |
| 1 | PHYSICAL_SIZE | Include physical size (mm) |

VDAgentMonConfig structure:
```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  height
4       4     uint32  width
8       4     uint32  depth
12      4     int32   x (position)
16      4     int32   y (position)
```

## Mouse State

### MOUSE_STATE (ID 1)

Reports mouse position and button state.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  x
4       4     uint32  y
8       4     uint32  buttons (button mask)
12      1     uint8   display_id
```

Agent button masks (note: different bit positions from inputs channel):

| Bit | Mask | Button |
|----:|-----:|--------|
| 1 | 0x02 | Left |
| 2 | 0x04 | Middle |
| 3 | 0x08 | Right |
| 4 | 0x10 | Wheel Up |
| 5 | 0x20 | Wheel Down |
| 6 | 0x40 | Side |
| 7 | 0x80 | Extra |

## File Transfer

### FILE_XFER_START (ID 10)

Initiates a file transfer.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  id (transfer ID)
4       var   bytes   metadata (key-value pairs, null-terminated)
```

Metadata format: `key1:value1\nkey2:value2\n`
Common keys: `name`, `size`

### FILE_XFER_DATA (ID 12)

Transfers file data chunk.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  id (transfer ID)
4       8     uint64  size (chunk size)
12      var   bytes   data
```

### FILE_XFER_STATUS (ID 11)

Reports file transfer status.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  id (transfer ID)
4       4     uint32  result
8       var   bytes   data (optional error details)
```

Result values:
| Value | Result | Description |
|------:|--------|-------------|
| 0 | CAN_SEND_DATA | Ready to receive more data |
| 1 | CANCELLED | Transfer cancelled by user |
| 2 | ERROR | Generic error |
| 3 | SUCCESS | Transfer completed successfully |
| 4 | NOT_ENOUGH_SPACE | Insufficient disk space |
| 5 | SESSION_LOCKED | Guest session is locked |
| 6 | VDAGENT_NOT_CONNECTED | Agent not connected |
| 7 | DISABLED | File transfer disabled |

## Capabilities

### ANNOUNCE_CAPABILITIES (ID 6)

Declares supported features.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  request (1 = request response)
4       4*N   uint32  capabilities (bitmask array)
```

Capability bits:
| Bit | Capability |
|----:|------------|
|  0  | MOUSE_STATE |
|  1  | MONITORS_CONFIG |
|  2  | REPLY |
|  3  | CLIPBOARD |
|  4  | DISPLAY_CONFIG |
|  5  | CLIPBOARD_BY_DEMAND |
|  6  | CLIPBOARD_SELECTION |
|  7  | SPARSE_MONITORS_CONFIG |
|  8  | GUEST_LINEEND_LF |
|  9  | GUEST_LINEEND_CRLF |
| 10  | MAX_CLIPBOARD |
| 11  | AUDIO_VOLUME_SYNC |
| 12  | MONITORS_CONFIG_POSITION |
| 13  | FILE_XFER_DISABLED |
| 14  | FILE_XFER_DETAILED_ERRORS |
| 15  | GRAPHICS_DEVICE_INFO |
| 16  | CLIPBOARD_NO_RELEASE_ON_REGRAB |
| 17  | CLIPBOARD_GRAB_SERIAL |

## Audio Volume Sync

### AUDIO_VOLUME_SYNC (ID 15)

Synchronizes audio volume settings.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   is_playback (1=playback, 0=record)
1       1     uint8   mute
2       1     uint8   nchannels
3       2*N   uint16  volume (per channel, 0-65535)
```

## Graphics Device Info

### GRAPHICS_DEVICE_INFO (ID 16)

Maps SPICE display channels to physical graphics devices.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  count
4       N*..  struct  device_display_info array
```

VDAgentDeviceDisplayInfo structure:
```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  channel_id
4       4     uint32  monitor_id
8       4     uint32  device_address_len
12      var   bytes   device_address (null-terminated)
16+     4     uint32  device_display_id
```

## Reply Message

### REPLY (ID 3)

Generic acknowledgment.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  type (message being acknowledged)
4       4     uint32  error (0=success)
```

## Protocol Flow

### Initialization

```
    Client                Agent               Server
      |                     |                    |
      |  ANNOUNCE_CAPABILITIES (request=1)       |
      |-------------------->|                    |
      |                     |                    |
      |  ANNOUNCE_CAPABILITIES (request=0)       |
      |<--------------------|                    |
      |                     |                    |
      |  MONITORS_CONFIG                         |
      |-------------------->|                    |
```

### Clipboard Copy (Client to Guest)

```
    Client                Agent
      |                     |
      |  CLIPBOARD_GRAB     |
      |-------------------->|
      |                     |
      |  CLIPBOARD_REQUEST  |
      |<--------------------|
      |                     |
      |  CLIPBOARD (data)   |
      |-------------------->|
```

### File Transfer

```
    Client                Agent
      |                     |
      |  FILE_XFER_START    |
      |-------------------->|
      |                     |
      |  FILE_XFER_DATA     |
      |-------------------->|
      |  FILE_XFER_DATA     |
      |-------------------->|
      |  ...                |
      |                     |
      |  FILE_XFER_STATUS   |
      |<--------------------|
```

## Security Considerations

1. **Clipboard Access**: Agent clipboard sharing allows data transfer between
   host and guest, which may expose sensitive information.

2. **File Transfer**: File transfer capabilities allow uploading files to the
   guest VM, which could be used to transfer malware.

3. **Display Configuration**: Monitor configuration changes could potentially
   be used for social engineering attacks.

Kerbside's audit logging records main channel establishment, which includes
agent communication.

## Related Documentation

- [Protocol Overview](/components/kerbside/protocol-overview.md) - High-level SPICE protocol
- [Channel Protocols](/components/kerbside/channel-protocols.md) - Main channel message details
