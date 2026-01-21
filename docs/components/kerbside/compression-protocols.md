# SPICE Compression Protocols

This document describes the proprietary graphics compression formats implemented
by the SPICE protocol, specifically the LZ and GLZ formats. These formats are
used by SPICE to compress display updates for transmission to clients.

## Overview

SPICE implements several graphics compression formats, but only two have been
commonly observed for non-video console sessions:

- **LZ** (Lempel-Ziv) - A dictionary compression algorithm
- **GLZ** (Global LZ) - An extension of LZ that references previous images

Both formats use a progressive dictionary compression scheme where previously
decompressed pixels form the dictionary for later decompression. Kerbside
includes a simple LZ/GLZ decompression tool (`kerbside-util`) that can
decompress these formats and convert them to PNG for inspection.

## LZ Compression

LZ compression as implemented in SPICE is a progressive dictionary compression
scheme. The dictionary is not transmitted separately from the image - instead,
previously decompressed elements of the image form the dictionary for later
decompression. As the image is processed (generally from top-left), runs of
previously decompressed pixels are referenced where they reappear.

### LZ Image Header

LZ images start with a header containing the following fields. Note that
multi-byte values are **big-endian**, unlike most of the SPICE protocol which
is little-endian.

| Field | Size | Description |
|-------|------|-------------|
| Magic | 4 bytes | Always `"  ZL"` (two spaces followed by ZL) in UTF-8 |
| Version Major | UINT16 | Always 1 |
| Version Minor | UINT16 | Always 1 |
| Padding | 3 bytes | Null bytes |
| Image Type | UINT8 | See Image Types table below |
| Image Width | UINT32 | Width of the image in pixels |
| Image Height | UINT32 | Height of the image in pixels |
| Image Stride | UINT32 | Length of one row in decompressed bytes (e.g., 1024 pixels = 4096 stride for RGB32) |
| Top Down | UINT32 | 1 if image is presented top row first, 0 if vertically flipped |

### Image Types

| Value | Type | Description |
|------:|------|-------------|
| 0 | invalid | Invalid/unset |
| 1 | palette1_le | 1-bit palette, little-endian |
| 2 | palette1_be | 1-bit palette, big-endian |
| 3 | palette4_le | 4-bit palette, little-endian |
| 4 | palette4_be | 4-bit palette, big-endian |
| 5 | palette8 | 8-bit palette |
| 6 | rgb16 | 16-bit RGB |
| 7 | rgb24 | 24-bit RGB |
| 8 | rgb32 | 32-bit RGB (actually BGR with implied alpha) |
| 9 | rgba | RGB with alpha |
| 10 | xxxa | Alpha channel only |

**Note**: In practice, the SPICE server implementation typically uses RGB32 for
display compression. Despite the name, this format is three bytes of **BGR**
(blue, green, red - reversed from traditional RGB order) with an implied alpha
channel of 0xFF.

### LZ Compressed Data Format

After the header, the compressed image data follows as a sequence of unsigned
single-byte commands with variable-length arguments.

#### Direct Pixel Commands (0-32)

If the command byte is 32 or less, it instructs the decoder to include N+1
direct pixels as output, where N is the command value.

For RGB32 format, a command of 0 produces:
```
[0] [B][G][R]
```

A command of 3 produces:
```
[3] [B0][G0][R0] [B1][G1][R1] [B2][G2][R2] [B3][G3][R3]
```

#### Reference Commands (> 32)

Commands greater than 32 reference previously decompressed pixels. The command
byte is structured as:

```
[LLLOOOOO]
 ^^^----- Length (top 3 bits)
    ^^^^^- Pixel offset (bottom 5 bits)
```

**Decoding Length:**

Extract the length from the top 3 bits. If the length field is maxed out at 7
(binary 111), additional length bytes follow. Read bytes and add them to the
length until a byte that is not 0xFF is encountered.

Example encoding for 512 pixels:
```
[111-----][11111111][11111010] = length: 7 + 255 + 250 = 512
```

**Decoding Pixel Offset:**

The 5 bits from the command byte form the high bits of the pixel offset. An
additional byte is always read:

```
[---OOOOO][MMMMMMMM] = OOOOOMMMMMMMMM (offset)
```

If the embedded offset bits are all ones (11111) AND the additional byte is
0xFF (255), this indicates an extended offset. Read two more bytes as a
big-endian UINT16 and add 8191:

```
[---11111][length...][11111111][HHHHHHHH][LLLLLLLL]
= HHHHHHHHLLLLLLLL + 8191 (offset for values > 8192)
```

Finally, add 1 to the offset (cannot reference the current pixel).

**Applying the Reference:**

- If offset = 1: Duplicate the immediately prior pixel `length` times
- Otherwise: Copy `length` pixels from (current position - offset)

### Implementation Reference

For a working implementation, see `kerbside/utilities/lz.py` in the Kerbside
codebase.

## GLZ Compression

GLZ (Global LZ) extends LZ by allowing references to pixels from previously
decompressed images in the session, not just the current image. This is
efficient when frames have small differences - only the delta and context
information need to be transmitted.

### GLZ Image Header

GLZ images use a similar but distinct header format. Multi-byte values are
**big-endian**.

| Field | Size | Description |
|-------|------|-------------|
| Magic | 4 bytes | Always `"  ZL"` (two spaces followed by ZL) in UTF-8 |
| Version Major | UINT16 | Always 1 |
| Version Minor | UINT16 | Always 1 |
| Image Type + Top Down | UINT8 | Packed: bottom 4 bits = image type, top 4 bits = top down flag |
| Image Width | UINT32 | Width in pixels |
| Image Height | UINT32 | Height in pixels |
| Image Stride | UINT32 | Length of one row in decompressed bytes |
| Image ID | UINT64 | Unique identifier for this image in the dictionary |
| Window Header Distance | UINT32 | Distance between current image ID and referenced image |

**Note**: The SPICE server always starts image IDs from zero. There appear to be
bugs when using very large IDs.

### GLZ Compressed Data Format

Direct pixel commands (0-32) work identically to LZ compression.

For reference commands (> 32), the command byte structure differs:

```
[LLLFOOOO]
 ^^^----- Length (top 3 bits)
    ^---- Pixel flag (1 bit)
     ^^^^- Pixel offset (bottom 4 bits)
```

**Decoding Length:**

Same as LZ - if the length field is maxed out at 7, read additional bytes
and add them to the length until a non-0xFF byte is encountered.

**Decoding Pixel Offset:**

Unlike LZ, an additional offset byte is **always** read and placed above the
embedded offset:

```
[---FOOOO][length...][MMMMMMMM] = MMMMMMMMOOOO (offset)
```

**Decoding Image Distance:**

Read another byte. The top 2 bits form the "image flag", and processing depends
on the pixel flag value:

**Pixel Flag = 0:**

The bottom 6 bits are the image distance. The image flag indicates how many
additional distance bytes to read, which are prepended:

```
[10DDDDDD][EEEEEEEE][FFFFFFFF]
= FFFFFFFFEEEEEEEEDDDDDD (distance)
```

**Pixel Flag = 1:**

Replace the pixel flag with bit 5 of the byte. The bottom 5 bits are added
to the pixel offset at bit position 12:

```
[---1OOOO][length...][MMMMMMMM][--PNNNNN]
= NNNNNMMMMMMMMOOOO (17-bit offset)
```

Where P is the new pixel flag (bit 5 of the byte).

Then read `image_flag` bytes of image distance. If the new pixel flag is
non-zero, read one final offset byte and add it at bit position 17, allowing
for 25-bit offsets.

**Applying the Reference:**

- If image distance = 0: Reference is within the current image (like LZ)
  - Offset 1: Duplicate previous pixel `length` times
  - Other offset: Copy `length` pixels from (current position - offset)
- If image distance > 0: Reference is to a previous image
  - Calculate target image ID = current image ID - distance
  - Offset is from the **start** of the target image
  - Copy `length` pixels from that location

### Implementation Reference

For a working implementation, see `kerbside/utilities/glz.py` in the Kerbside
codebase.

## Utility Tool

Kerbside includes `kerbside-util` which can:

- Decompress LZ and GLZ compressed images
- Convert compressed images to PNG for inspection
- Useful for debugging display channel issues

## Related Documentation

- [Protocol Overview](/components/kerbside/protocol-overview.md) - High-level SPICE protocol
- [Channel Protocols](/components/kerbside/channel-protocols.md) - Display channel message formats
- [Capabilities](/components/kerbside/capabilities.md) - LZ4Compression and PrefCompression capabilities
