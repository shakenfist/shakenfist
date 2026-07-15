# SPICE Channel Protocols

This document describes the message formats for each SPICE channel type. All
messages follow the standard header format described in the
[Protocol Overview](/components/kerbside/spice/protocol-overview/).

**Note**: Message IDs documented here match the constants in the ryll
`shakenfist-spice-protocol` crate that the Rust proxy uses (and from which
the L1 firewall allowlist in `rust/kerbside-proxy/src/allowlist.rs` is
derived). Some IDs may differ from the official SPICE protocol specification
for historical reasons.

## Standard Message Header

All channel messages use this header:

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       2     uint16  message_type
2       4     uint32  message_size (payload length)
6+      var   bytes   payload
```

## Channel Liveness and Disconnection

The SPICE protocol does not include an application-layer keepalive
beyond server-initiated PING / client PONG. The server
(qemu/spice-server) runs a per-channel "rcc" liveness timer; if a
channel goes more than 30 s without receiving any client message
the rcc is torn down. This produces a log entry on the host:

```
kvm: warning: Spice: <channel>:<n> (...): rcc <addr> has been
unresponsive for more than 30000 ms, disconnecting
```

The check counts any received client message, including PONG.
Clients therefore only need to remain responsive to server PINGs —
there is no requirement to send proactive client-initiated PINGs,
and no client-side message type for that purpose exists.

Reference clients (spice-gtk, virt-viewer, ryll) defend against
network-stack-level liveness failures with TCP keepalives at the OS
level:

| Setting          | Value |
|------------------|-------|
| `SO_KEEPALIVE`   | enabled |
| `TCP_KEEPIDLE`   | 30 s |
| `TCP_KEEPINTVL`  | 15 s |
| `TCP_KEEPCNT`    | 3 |

Reference: `spice-gtk/src/spice-session.c:2300`,
`spice-gtk/src/spice-channel.c:2606`. These are kernel-level
probes; they do not generate SPICE messages and therefore do not
satisfy the server's rcc check on their own. They only detect a
half-open connection promptly when the client cannot reach the
server.

When a channel disconnects (whether via the rcc timeout, a TCP
RST/FIN, or the client sending a `DISCONNECTING` message), the
session implications depend on the authentication model — see
[Channel Lifecycle Under Ticket Auth](/components/kerbside/spice/spice-link-protocol/#channel-lifecycle-under-ticket-auth).

## Main Channel (Type 1)

The main channel handles session control and channel negotiation.

### Client to Server Messages

| ID  | Message           | Description |
|----:|-------------------|-------------|
| 1   | ack_sync          | Acknowledge sync generation |
| 2   | ack               | Acknowledge message |
| 3   | pong              | Response to ping |
| 4   | migrate_flush_mark| Migration marker |
| 5   | migrate_data      | Migration data |
| 6   | disconnecting     | Client disconnecting |
| 104 | attach_channels   | Request to attach additional channels |

### Server to Client Messages

| ID  | Message        | Description |
|----:|----------------|-------------|
| 1   | migrate        | Migration request |
| 2   | migrate_data   | Migration data |
| 3   | set_ack        | Configure acknowledgement window |
| 4   | ping           | Ping request |
| 5   | wait_for_channels | Wait for channel traffic |
| 6   | disconnecting  | Server disconnecting |
| 7   | notify         | Notification message |
| 103 | init           | Session initialization |
| 104 | channels_list  | Available channels list |

### Main Init Message (Server, ID 103)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  session_id
4       4     uint32  display_channels_hint
8       4     uint32  supported_mouse_modes
12      4     uint32  current_mouse_mode
16      4     uint32  agent_connected
20      4     uint32  agent_tokens
24      4     uint32  multi_media_time
28      4     uint32  ram_hint
```

### Channels List Message (Server, ID 104)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  num_channels
4+      2*N   struct  channel entries

Each channel entry:
0       1     uint8   channel_type
1       1     uint8   channel_id
```

## Display Channel (Type 2)

The display channel handles screen updates and drawing operations.

### Client to Server Messages

| ID  | Message | Description |
|----:|---------|-------------|
| 101 | init    | Display initialization |

### Display Init Message (Client, ID 101)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   cache_id
1       8     uint64  cache_size
9       1     uint8   glz_dict_id
10      4     uint32  dict_window_size
```

### Server to Client Messages

| ID  | Message                | Description |
|----:|------------------------|-------------|
| 101 | mode                   | Display mode change |
| 102 | mark                   | Display mark |
| 103 | reset                  | Reset display state |
| 104 | copy_bits              | Copy bits operation |
| 105 | invalidate_list        | Invalidate regions |
| 106 | invalidate_all_pixmaps | Invalidate all pixmaps |
| 107 | invalidate_palette     | Invalidate palette |
| 108 | invalidate_all_palettes| Invalidate all palettes |
| 122 | stream_create          | Create video stream |
| 123 | stream_data            | Video stream data |
| 124 | stream_clip            | Stream clipping region |
| 125 | stream_destroy         | Destroy stream |
| 126 | stream_destroy_all     | Destroy all streams |
| 302 | draw_fill              | Fill rectangle |
| 303 | draw_opaque            | Opaque draw |
| 304 | draw_copy              | Copy with ROP |
| 305 | draw_blend             | Alpha blend |
| 306 | draw_blackness         | Fill black |
| 307 | draw_whiteness         | Fill white |
| 308 | draw_invers            | Invert region |
| 309 | draw_rop3              | ROP3 operation |
| 310 | draw_stroke            | Stroke path |
| 311 | draw_text              | Draw text |
| 312 | draw_transparent       | Transparent draw |
| 313 | draw_alpha_blend       | Alpha blend composite |
| 314 | surface_create         | Create surface |
| 315 | surface_destroy        | Destroy surface |
| 316 | stream_data_sized      | Sized stream data |
| 317 | monitors_config        | Monitor configuration |
| 318 | draw_composite         | Composite operation |
| 319 | stream_activate_report | Stream activity report |
| 320 | gl_scanout_unix        | GL scanout (Unix) |
| 321 | gl_draw                | GL draw |

### Surface Create Message (Server, ID 314)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  surface_id
4       4     uint32  width
8       4     uint32  height
12      4     uint32  format
16      4     uint32  flags
```

### Draw Copy Message (Server, ID 304)

This is one of the most common display messages, used for copying image data.

```
Offset  Size  Type    Field
------  ----  ------  -----------
-- SpiceMsgDisplayBase --
0       4     uint32  surface_id
4       4     uint32  rect_top
8       4     uint32  rect_left
12      4     uint32  rect_bottom
16      4     uint32  rect_right
20      1     uint8   clip_type

-- Clip Rectangles (if clip_type == 1) --
21      4     uint32  num_rects
25+     16*N  struct  rectangles

-- Source Info --
var     4     uint32  source_image_offset
var     4     uint32  src_rect_top
var     4     uint32  src_rect_left
var     4     uint32  src_rect_bottom
var     4     uint32  src_rect_right
var     2     uint16  raster_operations
var     1     uint8   scale_mode

-- Mask --
var     1     uint8   mask_flags
var     4     uint32  mask_pos_x
var     4     uint32  mask_pos_y
var     4     uint32  mask_bitmap_address

-- Image Data --
var     var   struct  image (see Image Format below)
```

### Clip Types

| Value | Type  | Description |
|------:|-------|-------------|
| 0     | none  | No clipping |
| 1     | rects | Rectangle list |
| 2     | path  | Path clipping |

### Raster Operations

| Bit  | Operation      |
|-----:|----------------|
| 0    | inverse_src    |
| 1    | inverse_brush  |
| 2    | inverse_dest   |
| 3    | put            |
| 4    | or             |
| 5    | and            |
| 6    | xor            |
| 7    | blackness      |
| 8    | whiteness      |
| 9    | inverse        |
| 10   | inverse_result |

### Scale Modes

| Value | Mode        |
|------:|-------------|
| 0     | interpolate |
| 1     | nearest     |

### Image Format

Images in display messages use this header:

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       8     uint64  image_id
8       1     uint8   type
9       1     uint8   flags
10      4     uint32  width
12      4     uint32  height
16+     var   bytes   image_data (format-specific)
```

### Image Types

| Value | Type        | Description |
|------:|-------------|-------------|
| 0     | pixmap      | Uncompressed pixmap |
| 1     | quic        | QUIC compression |
| 100   | lz_palette  | LZ with palette |
| 101   | lz_rgb      | LZ RGB compression |
| 102   | glz_rgb     | GLZ with dictionary |
| 103   | from_cache  | Reference to cached image |

### LZ/GLZ Image Header

For LZ and GLZ compressed images:

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  data_size

-- LZ Header (big-endian) --
4       4     bytes   magic ("  ZL")
8       2     uint16  version_major
10      2     uint16  version_minor
12      3     bytes   padding
15      1     uint8   type
16      4     uint32  width
20      4     uint32  height
24      4     uint32  stride
28      4     uint32  top_down

-- For GLZ only --
28      8     uint64  image_id
36      4     uint32  win_head_dist

-- Compressed data follows --
```

### LZ Image Types

| Value | Type        | Description |
|------:|-------------|-------------|
| 0     | invalid     | Invalid |
| 1     | palette1_le | 1-bit palette (LE) |
| 2     | palette1_be | 1-bit palette (BE) |
| 3     | palette4_le | 4-bit palette (LE) |
| 4     | palette4_be | 4-bit palette (BE) |
| 5     | palette8    | 8-bit palette |
| 6     | rgb16       | 16-bit RGB |
| 7     | rgb24       | 24-bit RGB |
| 8     | rgb32       | 32-bit RGB |
| 9     | rgba        | 32-bit RGBA |
| 10    | xxxa        | 32-bit with alpha |

## Inputs Channel (Type 3)

The inputs channel handles keyboard and mouse input from client to server.
See [Keyboard Scancodes](/components/kerbside/spice/scancodes/) for a complete scancode reference.

### Client to Server Messages

| ID  | Message        | Description |
|----:|----------------|-------------|
| 101 | key_down       | Key pressed |
| 102 | key_up         | Key released |
| 103 | key_modifiers  | Modifier state change |
| 104 | key_scancode   | Raw scancode |
| 111 | mouse_motion   | Relative mouse movement |
| 112 | mouse_position | Absolute mouse position |
| 113 | mouse_press    | Mouse button pressed |
| 114 | mouse_release  | Mouse button released |

### Server to Client Messages

| ID  | Message          | Description |
|----:|------------------|-------------|
| 101 | init             | Inputs initialization |
| 102 | key_modifiers    | Current modifier state |
| 111 | mouse_motion_ack | Acknowledge mouse motion |

### Key Down/Up Message (Client, ID 101/102)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  scancode
```

### Key Modifiers Message (ID 103/102)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       2     uint16  modifiers
```

Modifier flags:
| Bit | Modifier    |
|----:|-------------|
| 0   | Scroll Lock |
| 1   | Num Lock    |
| 2   | Caps Lock   |

### Key Scancode Message (Client, ID 104)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       N     uint8[] scancodes (N bytes, one per scancode)
```

### Mouse Motion Message (Client, ID 111)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     int32   delta_x
4       4     int32   delta_y
8       2     uint16  button_state
```

### Mouse Position Message (Client, ID 112)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  x
4       4     uint32  y
8       2     uint16  button_state
10      1     uint8   display_id
```

### Mouse Press/Release Message (Client, ID 113/114)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   button
1       2     uint16  button_state
3       1     uint8   display_id
```

### Mouse Buttons

| Value | Button |
|------:|--------|
| 0 | Invalid |
| 1 | Left |
| 2 | Middle |
| 3 | Right |
| 4 | Wheel Up |
| 5 | Wheel Down |
| 6 | Side |
| 7 | Extra |

### Mouse Button State Masks

The `button_state` field is a bitmask of currently pressed buttons:

| Bit | Mask | Button |
|----:|-----:|--------|
| 0 | 0x01 | Left |
| 1 | 0x02 | Middle |
| 2 | 0x04 | Right |
| 3 | 0x08 | Wheel Up |
| 4 | 0x10 | Wheel Down |
| 5 | 0x20 | Side |
| 6 | 0x40 | Extra |

### Mouse Modes

| Value | Mode | Description |
|------:|------|-------------|
| 1 | Server | Server controls cursor; client sends relative motion |
| 2 | Client | Client controls cursor; client sends absolute position |

## Cursor Channel (Type 4)

The cursor channel handles cursor shape and position updates.

### Server to Client Messages

| ID  | Message        | Description |
|----:|----------------|-------------|
| 101 | init           | Cursor initialization |
| 102 | reset          | Reset cursor state |
| 103 | set            | Set cursor shape and position |
| 104 | move           | Move cursor |
| 105 | hide           | Hide cursor |
| 106 | trail          | Set cursor trail |
| 107 | invalidate_one | Invalidate cached cursor |
| 108 | invalidate_all | Invalidate all cached cursors |

### Cursor Init Message (Server, ID 101)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       2     uint16  x
2       2     uint16  y
4       2     uint16  trail_length
6       2     uint16  trail_frequency
8       1     uint8   visible

-- SpiceCursor follows if size permits --
9       4     uint32  flags
13      8     uint64  unique_id
21      2     uint16  cursor_type
23      2     uint16  width
25      2     uint16  height
27      2     uint16  hot_spot_x
29      2     uint16  hot_spot_y
31+     var   bytes   cursor_data
```

### Cursor Types

| Value | Type | Description |
|------:|------|-------------|
| 0 | Alpha | 32-bit ARGB with alpha channel |
| 1 | Mono | Monochrome (1-bit) |
| 2 | Color4 | 4-bit palette indexed |
| 3 | Color8 | 8-bit palette indexed |
| 4 | Color16 | 16-bit RGB |
| 5 | Color24 | 24-bit BGR |
| 6 | Color32 | 32-bit xRGB (no alpha) |

### Cursor Flags

| Bit | Flag | Description |
|----:|------|-------------|
| 0 | None | No special flags |
| 1 | Cache Me | Server requests client to cache this cursor |
| 2 | From Cache | Use previously cached cursor (data not included) |

### Cursor Set Message (Server, ID 103)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       2     uint16  x
2       2     uint16  y
4       1     uint8   visible

-- SpiceCursor follows --
```

### Cursor Move Message (Server, ID 104)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       2     uint16  x
2       2     uint16  y
```

### Cursor Trail Message (Server, ID 106)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       2     uint16  trail_length
2       2     uint16  trail_frequency
```

### Cursor Invalidate One Message (Server, ID 107)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       8     uint64  cursor_id
```

## Port Channel (Type 9)

The port channel handles device redirection, primarily USB devices through the
VMC (Virtual Machine Communication) protocol.

### Client and Server Messages

| ID  | Message            | Description |
|----:|--------------------|-------------|
| 101 | vmc_data           | VMC protocol data |
| 102 | vmc_compressed_data| Compressed VMC data |

### VMC Data Message (ID 101)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  vmc_type (USB redir message type)
4       4     uint32  vmc_length
8       4     uint32  vmc_id
12+     var   bytes   vmc_payload (see USB Redirection)
```

For detailed VMC/USB redirection message formats, see
[USB Redirection](/components/kerbside/spice/usb-redirection/).

## Common Message Details

### Ping Message (Server)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  ping_id
4       8     uint64  timestamp
```

### Pong Message (Client)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  ping_id
4       8     uint64  timestamp
```

### Set Ack Message (Server)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  generation
4       4     uint32  window
```

### Ack Sync Message (Client)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  generation
```

### Ack Message (Client)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  generation
```

Note: Display channel ACKs may have zero-length payloads.

### Disconnecting Message

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       8     uint64  timestamp
4       4     uint32  reason (error code)
```

### Notify Message (Server)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       8     uint64  timestamp
8       4     uint32  severity
12      4     uint32  visibility
16      4     uint32  what (topic code)
20      4     uint32  message_length
24      N     bytes   message (UTF-8, null-terminated)
```

Severity values:
| Value | Severity |
|------:|----------|
| 0     | info     |
| 1     | warn     |
| 2     | error    |

Visibility values:
| Value | Visibility |
|------:|------------|
| 0     | low        |
| 1     | medium     |
| 2     | high       |

## Playback Channel (Type 5)

The playback channel handles audio output from the VM to the client.

**Note**: Kerbside proxies this channel transparently without decoding.

### Server to Client Messages

| ID  | Message | Description |
|----:|---------|-------------|
| 101 | data    | Audio data packet |
| 102 | mode    | Audio mode/codec configuration |
| 103 | start   | Start audio playback |
| 104 | stop    | Stop audio playback |
| 105 | volume  | Volume control |
| 106 | mute    | Mute control |
| 107 | latency | Latency information |

### Playback Mode Message (Server, ID 102)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  time (multimedia time)
4       4     uint32  mode
8       var   bytes   codec-specific data
```

Mode values:
| Value | Mode      | Description |
|------:|-----------|-------------|
| 1     | RAW       | Uncompressed PCM |
| 2     | CELT_0_5_1| CELT 0.5.1 codec (deprecated) |
| 3     | OPUS      | Opus codec (recommended) |

**OPUS Codec Specifications:**
- Frame size: 480 samples
- Playback frequency: 48000 Hz
- Channels: 2 (stereo)
- Max compressed frame: ~480 bytes
- Max uncompressed frame: 1920 bytes (480 samples × 2 channels × 2 bytes)

### Playback Start Message (Server, ID 103)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  channels (1=mono, 2=stereo)
4       4     uint32  format (1=S16, 16-bit signed)
8       4     uint32  frequency (sample rate in Hz)
```

### Playback Data Message (Server, ID 101)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  time (multimedia time)
4       var   bytes   audio data
```

### Playback Volume Message (Server, ID 105)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       1     uint8   num_channels
1       2*N   uint16  volume per channel (0-65535)
```

## Record Channel (Type 6)

The record channel handles audio input from the client to the VM.

**Note**: Kerbside proxies this channel transparently without decoding.

### Server to Client Messages

| ID  | Message | Description |
|----:|---------|-------------|
| 101 | start   | Start recording |
| 102 | stop    | Stop recording |
| 103 | volume  | Volume control |
| 104 | mute    | Mute control |

### Client to Server Messages

| ID  | Message    | Description |
|----:|------------|-------------|
| 101 | data       | Audio data packet |
| 102 | mode       | Audio mode/codec configuration |
| 103 | start_mark | Start marker |

### Record Start Message (Server, ID 101)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  channels
4       4     uint32  format (1=S16)
8       4     uint32  frequency
```

### Record Data Message (Client, ID 101)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  time (multimedia time)
4       var   bytes   audio data
```

## Smartcard Channel (Type 8)

The smartcard channel handles smart card redirection for authentication using
the VSC (Virtual Smart Card) protocol.

**Note**: Kerbside proxies this channel transparently without decoding.

### Server to Client Messages

| ID  | Message | Description |
|----:|---------|-------------|
| 101 | data    | Smartcard data |
| 102 | error   | Error message |

### Client to Server Messages

| ID  | Message  | Description |
|----:|----------|-------------|
| 101 | data     | Smartcard data |
| 102 | header   | Header information |
| 103 | reader_add | Reader added |
| 104 | reader_remove | Reader removed |
| 105 | atr      | Answer-to-reset data |
| 106 | error    | Error message |

### VSC Message Types

The smartcard data payload contains VSC (Virtual Smart Card) messages:

| Value | Type | Description |
|------:|------|-------------|
| 1 | Init | Initialize VSC session |
| 2 | Error | Error notification |
| 3 | ReaderAdd | Smart card reader attached |
| 4 | ReaderRemove | Smart card reader removed |
| 5 | ATR | Answer-to-Reset (card identification) |
| 6 | CardRemove | Smart card removed from reader |
| 7 | APDU | Application Protocol Data Unit (command/response) |
| 8 | Flush | Flush pending operations |
| 9 | FlushComplete | Flush operation completed |

## Video Streaming

The display channel supports video streaming for efficient screen updates.

### Video Codecs

| Value | Codec  | Description |
|------:|--------|-------------|
| 1     | MJPEG  | Motion JPEG |
| 2     | VP8    | VP8 (WebM) |
| 3     | H264   | H.264/AVC |
| 4     | VP9    | VP9 (WebM) |
| 5     | H265   | H.265/HEVC |

### Stream Create Message (Server, ID 122)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  stream_id
4       4     uint32  flags (1=TOP_DOWN)
8       4     uint32  codec_type
12      8     uint64  reserved
20      4     uint32  stream_width
24      4     uint32  stream_height
28      4     uint32  source_width
32      4     uint32  source_height
36      16    struct  destination rect (top, left, bottom, right)
52      var   struct  clip (type + data)
```

### Stream Data Message (Server, ID 123)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  stream_id
4       4     uint32  multimedia_time
8       4     uint32  data_size
12      4     uint32  pad_size (for alignment)
16      var   bytes   compressed video frame
```

### Stream Clip Message (Server, ID 124)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  stream_id
4       var   struct  clip (type + rectangles)
```

### Stream Destroy Message (Server, ID 125)

```
Offset  Size  Type    Field
------  ----  ------  -----------
0       4     uint32  stream_id
```

## Additional Image Types

Beyond the basic compression types documented earlier, SPICE supports
these image types (values from `spice-protocol/spice/enums.h`):

| Value | Type                | Description |
|------:|---------------------|-------------|
| 0     | PIXMAP              | Uncompressed bitmap (BGRX) |
| 1     | QUIC                | QUIC compression |
| 100   | LZ_PLT              | LZ with palette |
| 101   | LZ_RGB              | LZ RGB compression |
| 102   | GLZ_RGB             | GLZ with cross-frame dictionary |
| 103   | FROM_CACHE          | Cached image reference |
| 104   | SURFACE             | Reference to existing surface |
| 105   | JPEG                | JPEG compression |
| 106   | FROM_CACHE_LOSSLESS | Lossless cached image reference |
| 107   | ZLIB_GLZ_RGB        | Zlib-compressed GLZ data |
| 108   | JPEG_ALPHA          | JPEG with alpha channel |
| 109   | LZ4                 | LZ4 per-row compression |

## Raster Operations (ROP)

Drawing commands use raster operation flags to control how pixels are combined:

| Bit | Flag | Description |
|----:|------|-------------|
| 0 | INVERS_SRC | Invert source before operation |
| 1 | INVERS_BRUSH | Invert brush before operation |
| 2 | INVERS_DEST | Invert destination before operation |
| 3 | OP_PUT | Copy operation |
| 4 | OP_OR | Bitwise OR |
| 5 | OP_AND | Bitwise AND |
| 6 | OP_XOR | Bitwise XOR |
| 7 | OP_BLACKNESS | Fill with black |
| 8 | OP_WHITENESS | Fill with white |
| 9 | OP_INVERS | Invert destination |
| 10 | INVERS_RES | Invert result after operation |

## Clip Types

| Value | Type | Description |
|------:|------|-------------|
| 0 | none | No clipping |
| 1 | rects | Rectangular clipping region |
| 2 | path | Path-based clipping |

## Brush Types

| Value | Type | Description |
|------:|------|-------------|
| 0 | none | No brush |
| 1 | solid | Solid color fill |
| 2 | pattern | Pattern fill |

## Scale Modes

| Value | Mode | Description |
|------:|------|-------------|
| 0 | interpolate | Bilinear interpolation |
| 1 | nearest | Nearest-neighbor sampling |

## Mouse Button Masks

| Bit | Button |
|----:|--------|
| 0   | Left   |
| 1   | Middle |
| 2   | Right  |
| 3   | Up (scroll) |
| 4   | Down (scroll) |
| 5   | Side   |
| 6   | Extra  |

## Related Documentation

- [Protocol Overview](/components/kerbside/spice/protocol-overview/) - High-level protocol introduction
- [Link Protocol](/components/kerbside/spice/spice-link-protocol/) - Connection handshake details
- [USB Redirection](/components/kerbside/spice/usb-redirection/) - USB device redirection protocol
- [VD Agent Protocol](/components/kerbside/spice/vd-agent-protocol/) - Guest agent for clipboard and file transfer
