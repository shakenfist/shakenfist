# USB Redirection Protocol

This document describes the USB redirection protocol used within SPICE's port
channel. USB redirection allows USB devices connected to the client machine to
be redirected to the virtual machine.

## Overview

USB redirection in SPICE uses the VMC (Virtual Machine Communication) protocol
as a transport layer. The USB-specific protocol is called "usbredir" and is
encapsulated within VMC data messages on the port channel (channel type 9).

## Protocol References

- [USB Redirection Protocol Spec](https://www.spice-space.org/usbredir.html)
- [usbredir Source Code](https://gitlab.freedesktop.org/spice/usbredir)
- [Protocol Header](https://gitlab.freedesktop.org/spice/usbredir/-/blob/main/usbredirparser/usbredirproto.h)
- [Protocol Documentation](https://gitlab.freedesktop.org/spice/usbredir/-/blob/main/docs/usb-redirection-protocol.md)

## USB Redir Packet Header

USB redirection uses its own packet header format within the VMC encapsulation.
The protocol supports two header formats depending on negotiated capabilities.

### Standard Header (32-bit IDs)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  type (message type)
4       4     uint32  length (payload length)
8       4     uint32  id (sequence/correlation ID)
```

### Extended Header (64-bit IDs)

When both sides advertise `usb_redir_cap_64bits_ids`:

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  type (message type)
4       4     uint32  length (payload length)
8       8     uint64  id (sequence/correlation ID)
```

All integers use little-endian byte order. Structures are packed without padding.

## VMC Encapsulation

USB redirection messages are wrapped in VMC data packets:

```
-- SPICE Port Channel Header (6 bytes) --
Offset  Size  Type    Field
------  ----  ------  -----------
0       2     uint16  message_type (101 = vmc_data)
2       4     uint32  message_size

-- VMC Header (12 bytes) --
6       4     uint32  vmc_type (usb_redir message type)
10      4     uint32  vmc_length (usb_redir payload length)
14      4     uint32  vmc_id (sequence/correlation ID)

-- USB Redir Payload --
18      var   bytes   usb_redir message data
```

## USB Redirection Message Types

### Control Messages

| ID | Message                        | Description |
|---:|--------------------------------|-------------|
|  0 | usb_redir_hello                | Protocol version exchange |
|  1 | usb_redir_device_connect       | Device connection notification |
|  2 | usb_redir_device_disconnect    | Device disconnection notification |
|  3 | usb_redir_reset                | Reset USB device |
|  4 | usb_redir_interface_info       | Interface descriptor info |
|  5 | usb_redir_ep_info              | Endpoint descriptor info |
|  6 | usb_redir_set_configuration    | Set USB configuration |
|  7 | usb_redir_get_configuration    | Get current configuration |
|  8 | usb_redir_configuration_status | Configuration status response |
|  9 | usb_redir_set_alt_setting      | Set alternate interface setting |
| 10 | usb_redir_get_alt_setting      | Get current alternate setting |
| 11 | usb_redir_alt_setting_status   | Alternate setting status response |
| 12 | usb_redir_start_iso_stream     | Start isochronous stream |
| 13 | usb_redir_stop_iso_stream      | Stop isochronous stream |
| 14 | usb_redir_iso_stream_status    | Isochronous stream status |
| 15 | usb_redir_start_interrupt_receiving | Start interrupt endpoint |
| 16 | usb_redir_stop_interrupt_receiving  | Stop interrupt endpoint |
| 17 | usb_redir_interrupt_receiving_status | Interrupt status |
| 18 | usb_redir_alloc_bulk_streams   | Allocate bulk streams |
| 19 | usb_redir_free_bulk_streams    | Free bulk streams |
| 20 | usb_redir_bulk_streams_status  | Bulk streams status |
| 21 | usb_redir_cancel_data_packet   | Cancel pending data packet |
| 22 | usb_redir_filter_reject        | Filter rejection notification |
| 23 | usb_redir_filter_filter        | Device filter rules |
| 24 | usb_redir_device_disconnect_ack | Acknowledge disconnection |
| 25 | usb_redir_start_bulk_receiving | Start bulk endpoint receiving |
| 26 | usb_redir_stop_bulk_receiving  | Stop bulk endpoint receiving |
| 27 | usb_redir_bulk_receiving_status | Bulk receiving status |

### Data Messages

| ID  | Message                        | Description |
|----:|--------------------------------|-------------|
| 100 | usb_redir_control_packet       | Control transfer data |
| 101 | usb_redir_bulk_packet          | Bulk transfer data |
| 102 | usb_redir_iso_packet           | Isochronous transfer data |
| 103 | usb_redir_interrupt_packet     | Interrupt transfer data |
| 104 | usb_redir_buffered_bulk_packet | Buffered bulk transfer |

## Message Formats

### usb_redir_hello (ID 0)

The first message exchanged between client and server to establish protocol
version and capabilities.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       64    string  version (null-terminated, padded)
64      4     uint32  capabilities (bitmask)
```

The version string identifies the usbredir implementation and version (e.g.,
"qemu usb-redir guest 0.8.0").

### usb_redir_device_connect (ID 1)

Sent when a USB device is connected and available for redirection.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   speed (USB speed class)
1       1     uint8   device_class
2       1     uint8   device_subclass
3       1     uint8   device_protocol
4       2     uint16  vendor_id
6       2     uint16  product_id
8       2     uint16  device_version_bcd
```

USB Speed values:
| Value | Speed |
|------:|-------|
| 0     | Unknown |
| 1     | Low (1.5 Mbps) |
| 2     | Full (12 Mbps) |
| 3     | High (480 Mbps) |
| 4     | Super (5 Gbps) |
| 5     | Super+ (10 Gbps) |

### usb_redir_device_disconnect (ID 2)

Sent when a redirected USB device is disconnected. This message has no payload.

### usb_redir_reset (ID 3)

Request to reset the USB device. This message has no payload.

### usb_redir_interface_info (ID 4)

Provides information about all interfaces on the device.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       32    uint8[] interface_count per alt setting
32      32    uint8[] interface_class
64      32    uint8[] interface_subclass
96      32    uint8[] interface_protocol
```

### usb_redir_ep_info (ID 5)

Provides information about all endpoints on the device.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       32    uint8[] ep_type (per endpoint)
32      32    uint8[] ep_interval
64      32    uint8[] ep_interface
96      64    uint16[] ep_max_packet_size
```

Endpoint types:
| Value | Type |
|------:|------|
| 0     | Control |
| 1     | Isochronous |
| 2     | Bulk |
| 3     | Interrupt |
| 255   | Invalid |

### usb_redir_set_configuration (ID 6)

Request to set a specific USB configuration.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   configuration
```

### usb_redir_get_configuration (ID 7)

Request the current USB configuration. This message has no payload.

### usb_redir_configuration_status (ID 8)

Response to set/get configuration requests.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   status
1       1     uint8   configuration
```

### usb_redir_set_alt_setting (ID 9)

Request to set an alternate interface setting.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   interface
1       1     uint8   alt_setting
```

### usb_redir_get_alt_setting (ID 10)

Request the current alternate setting for an interface.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   interface
```

### usb_redir_alt_setting_status (ID 11)

Response to set/get alternate setting requests.

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   status
1       1     uint8   interface
2       1     uint8   alt_setting
```

## Data Transfer Messages

### usb_redir_control_packet (ID 100)

USB control transfer (used for device configuration and standard requests).

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   endpoint
1       1     uint8   request
2       1     uint8   request_type
3       1     uint8   status
4       2     uint16  value
6       2     uint16  index
8       2     uint16  length
10      var   bytes   data (if length > 0)
```

### usb_redir_bulk_packet (ID 101)

USB bulk transfer (used for large data transfers).

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   endpoint
1       1     uint8   status
2       2     uint16  length
4       4     uint32  stream_id
8       2     uint16  length_high (extended length)
10      var   bytes   data (if length > 0)
```

### usb_redir_iso_packet (ID 102)

USB isochronous transfer (used for streaming data like audio/video).

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   endpoint
1       1     uint8   status
2       2     uint16  length
4       var   bytes   data
```

### usb_redir_interrupt_packet (ID 103)

USB interrupt transfer (used for periodic small data transfers).

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   endpoint
1       1     uint8   status
2       2     uint16  length
4       var   bytes   data
```

## Status Codes

USB redirection operations return status codes:

| Value | Status |
|------:|--------|
| 0     | Success |
| 1     | Cancelled |
| 2     | INVAL (invalid parameter) |
| 3     | IOERROR |
| 4     | STALL |
| 5     | TIMEOUT |
| 6     | BABBLE |

## Capabilities

The hello message includes a capabilities bitmask indicating supported features:

| Bit | Capability                    | Description |
|----:|-------------------------------|-------------|
| 0   | bulk_streams                  | USB 3.0 bulk streams support |
| 1   | connect_device_version        | Device version BCD in connect packet |
| 2   | filter                        | Filter reject/filter packets supported |
| 3   | device_disconnect_ack         | Disconnect acknowledgment required |
| 4   | ep_info_max_packet_size       | Max packet size in endpoint info |
| 5   | 64bits_ids                    | 64-bit packet IDs |
| 6   | 32bits_bulk_length            | 32-bit bulk packet length |
| 7   | bulk_receiving                | Buffered bulk input support |

## Additional Control Messages

### usb_redir_start_iso_stream (ID 12)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   endpoint
1       1     uint8   pkts_per_urb
2       1     uint8   no_urbs
```

### usb_redir_stop_iso_stream (ID 13)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   endpoint
```

### usb_redir_iso_stream_status (ID 14)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   status
1       1     uint8   endpoint
```

### usb_redir_start_interrupt_receiving (ID 15)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   endpoint
```

### usb_redir_stop_interrupt_receiving (ID 16)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   endpoint
```

### usb_redir_interrupt_receiving_status (ID 17)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   status
1       1     uint8   endpoint
```

### usb_redir_alloc_bulk_streams (ID 18)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  endpoints (bitmask: bit N = endpoint N)
4       4     uint32  no_streams
```

### usb_redir_free_bulk_streams (ID 19)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  endpoints (bitmask)
```

### usb_redir_bulk_streams_status (ID 20)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  endpoints (bitmask)
4       4     uint32  no_streams
8       1     uint8   status
```

### usb_redir_start_bulk_receiving (ID 25)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  stream_id
4       4     uint32  bytes_per_transfer
8       1     uint8   endpoint
9       1     uint8   no_transfers
```

### usb_redir_stop_bulk_receiving (ID 26)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  stream_id
4       1     uint8   endpoint
```

### usb_redir_bulk_receiving_status (ID 27)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  stream_id
4       1     uint8   endpoint
5       1     uint8   status
```

### usb_redir_filter_filter (ID 23)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       N     string  filter rules (null-terminated)
```

Filter format: Rules separated by `|`, each rule is:
`<class>,<vendor>,<product>,<version>,<allow>`

- Use `-1` as wildcard for any value
- Values can be decimal or `0x`-prefixed hex
- `<allow>` is 0 (deny) or 1 (allow)

Example: `-1,-1,-1,-1,1` allows all devices

### usb_redir_buffered_bulk_packet (ID 104)

For buffered bulk input (requires `bulk_receiving` capability):

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  stream_id
4       4     uint32  length
8       1     uint8   endpoint
9       1     uint8   status
10      var   bytes   data
```

## Protocol Rules

### Message Direction

- **Guest initiates**: Control packets and outbound data packets
- **Host responds**: Same message type with matching ID
- **Unsolicited from host**: `device_connect`, `device_disconnect`,
  `filter_reject`, input data streams

### Initialization Sequence

The host must send messages in this order before `device_connect`:
1. `ep_info` - Endpoint information
2. `interface_info` - Interface descriptors
3. `device_connect` - Device availability

### Configuration Changes

After successful `set_configuration` or `set_alt_setting`, the host must send:
1. `ep_info` - Updated endpoint information
2. `interface_info` - Updated interface descriptors
3. `configuration_status` or `alt_setting_status` - Status response

### Endpoint Numbering

Endpoints are numbered 0-31:
- Endpoints 0-15: OUT direction (host to device)
- Endpoints 16-31: IN direction (device to host)
- Endpoint 0 and 16: Control endpoint (always present)

### Stream ID Management

For isochronous and buffered bulk streams:
- IDs start at 0 and increment per packet
- IDs reset after stall condition recovery
- Host resets ID on `_start_*` messages

## Connection Flow

A typical USB redirection session follows this flow:

```
    Client                                     Server (VM)
      |                                            |
      |  usb_redir_hello (version + caps)          |
      |------------------------------------------->|
      |                                            |
      |  usb_redir_hello (version + caps)          |
      |<-------------------------------------------|
      |                                            |
      |  usb_redir_device_connect                  |
      |------------------------------------------->|
      |                                            |
      |  usb_redir_interface_info                  |
      |------------------------------------------->|
      |                                            |
      |  usb_redir_ep_info                         |
      |------------------------------------------->|
      |                                            |
      |  usb_redir_set_configuration               |
      |<-------------------------------------------|
      |                                            |
      |  usb_redir_configuration_status            |
      |------------------------------------------->|
      |                                            |
      |  [ Data transfers: bulk, interrupt, etc ]  |
      |<------------------------------------------>|
      |                                            |
      |  usb_redir_device_disconnect               |
      |------------------------------------------->|
      |                                            |
      |  usb_redir_device_disconnect_ack           |
      |<-------------------------------------------|
```

## Kerbside Implementation

The Rust proxy relays the USB redirection (`usbredir`) channel between client
and server, subject to the L0+L1 firewall: message types are checked against
the per-channel allowlist (`rust/kerbside-proxy/src/allowlist.rs`) and the
payload is relayed opaquely. Deep VMC / `usb_redir_hello` decoding is not
implemented (it was part of the removed Python-proxy traffic-inspection
feature); reintroducing it would belong with a future L2/L3 inspection
capability.

## Security Considerations

USB redirection presents security considerations:

1. **Device Access**: Redirected USB devices have direct access to the VM,
   potentially exposing sensitive data.

2. **Malicious Devices**: USB devices can potentially be used for attacks
   (e.g., BadUSB).

3. **Data Exposure**: USB traffic may contain sensitive data that should be
   protected.

Kerbside's audit logging records USB redirection channel establishment for
security monitoring purposes.

## Related Documentation

- [Protocol Overview](/components/kerbside/protocol-overview/) - High-level protocol introduction
- [Channel Protocols](/components/kerbside/channel-protocols/) - Per-channel message formats
