# Phase 1: Parse SpiceCursor image data

## Overview

Parse the cursor image data from CURSOR_INIT and CURSOR_SET
messages, convert it to RGBA, cache by unique_id, and
forward to the app via a new `CursorShape` channel event.

## Current state

- `CursorInit::read()` parses 9 bytes (position, trail,
  visible) but ignores any cursor data that follows.
- `CursorSet::read()` parses 5 bytes (position, visible)
  but ignores any cursor data that follows.
- The cursor channel sends `CursorPosition` events with
  (x, y, visible) only — no image data.
- There is no cursor image cache.

## Wire format

### SpiceCursor structure (follows position data)

```
Offset  Size  Field
------  ----  -----------
0       2     flags (u16 LE)
                bit 0: NONE — no cursor data follows
                bit 1: CACHE_ME — client should cache this
                bit 2: FROM_CACHE — no pixel data follows;
                        look up by unique_id
2       8     unique_id (u64 LE)
10      1     cursor_type (u8)
11      2     width (u16 LE)
13      2     height (u16 LE)
15      2     hot_spot_x (u16 LE)
17      2     hot_spot_y (u16 LE)
19      var   pixel_data (only if FROM_CACHE is NOT set)
```

Total header size: 19 bytes (flags + header fields).
When FLAG_NONE is set, only the 2-byte flags field is present.

### Where SpiceCursor appears

- **CURSOR_INIT** (opcode 101): 9 bytes of position/trail/
  visible, then SpiceCursor if payload > 9 + 2 bytes.
- **CURSOR_SET** (opcode 103): 5 bytes of position/visible,
  then SpiceCursor if payload > 5 + 2 bytes.

### Pixel data formats (by cursor_type)

| Type | Name    | Bytes/pixel | Conversion to RGBA         |
|-----:|---------|-------------|----------------------------|
|    0 | Alpha   | 4           | ARGB → RGBA (swap A to end)|
|    1 | Mono    | 1-bit       | Not planned initially      |
|    5 | Color24 | 3           | BGR → RGBA (add A=255)     |
|    6 | Color32 | 4           | xRGB → RGBA (set A=255)    |

Type 0 (Alpha) is the most common from QEMU/KVM.

## Implementation steps

### Step 1: Add SpiceCursorHeader struct

In `protocol/messages.rs`, add:

```rust
pub struct SpiceCursorHeader {
    pub flags: u16,
    pub unique_id: u64,
    pub cursor_type: u8,
    pub width: u16,
    pub height: u16,
    pub hot_spot_x: u16,
    pub hot_spot_y: u16,
}
```

With `const FLAGS_SIZE: usize = 2`, `const SIZE: usize = 19`,
and a `read()` method that returns `Option<Self>` (None when
FLAG_NONE is set). Define flag constants:

```rust
pub const FLAG_NONE: u16 = 1 << 0;
pub const FLAG_CACHE_ME: u16 = 1 << 1;
pub const FLAG_FROM_CACHE: u16 = 1 << 2;
```

### Step 2: Add CursorImage type and CursorShape event

In `channels/mod.rs`, add:

```rust
pub struct CursorImage {
    pub width: u16,
    pub height: u16,
    pub hot_spot_x: u16,
    pub hot_spot_y: u16,
    pub pixels: Vec<u8>,  // RGBA
}
```

Add a new event variant:

```rust
ChannelEvent::CursorShape(CursorImage)
```

### Step 3: Add cursor image cache to CursorChannel

In `channels/cursor.rs`, add fields:

```rust
cursor_cache: HashMap<u64, CursorImage>,
```

### Step 4: Parse SpiceCursor in INIT and SET handlers

After reading the position/visible fields, check whether
the payload is large enough to contain the SpiceCursor
flags (2 more bytes). If so:

1. Parse `SpiceCursorHeader` from the remaining payload.
   If `read()` returns `None` (FLAG_NONE set), there is no
   cursor data — return early.
2. If `FROM_CACHE` flag is set: look up `unique_id` in
   `cursor_cache` and emit `CursorShape` with the cached
   image. If not found, log a warning.
3. If `FROM_CACHE` is NOT set: read pixel data after the
   header, convert to RGBA based on `cursor_type`, emit
   `CursorShape`.
4. If `CACHE_ME` flag is set: store a clone of the image
   in `cursor_cache` keyed by `unique_id`.

### Step 5: Pixel data conversion

Implement conversion for the common types:

- **Alpha (type 0):** Read 4 bytes per pixel as ARGB, emit
  as RGBA: `[A, R, G, B] → [R, G, B, A]`
- **Color32 (type 6):** Read 4 bytes per pixel as xRGB,
  emit as RGBA: `[x, R, G, B] → [R, G, B, 255]`
- **Color24 (type 5):** Read 3 bytes per pixel as BGR,
  emit as RGBA: `[B, G, R] → [R, G, B, 255]`
- **Other types:** Log a warning and skip.

Use checked arithmetic for `width * height * bytes_per_pixel`
as per the STYLEGUIDE.md dimension safety rule.

### Step 6: Handle INVALIDATE_ONE and INVALIDATE_ALL

- **INVALIDATE_ONE** (opcode 107): payload is 8 bytes
  (u64 unique_id). Remove from `cursor_cache`.
- **INVALIDATE_ALL** (opcode 108): clear `cursor_cache`.

These are already defined as constants but currently fall
through to the unknown handler.

### Step 7: Unit test

Add a test that constructs a minimal CURSOR_SET payload
with a 2x2 Alpha cursor, parses it, and verifies the
RGBA output has A moved from the first byte to the last.

## Files to modify

| File | Changes |
|------|---------|
| `src/protocol/messages.rs` | Add `SpiceCursorHeader` struct |
| `src/protocol/constants.rs` | Add cursor flag constants |
| `src/channels/mod.rs` | Add `CursorImage`, `CursorShape` event |
| `src/channels/cursor.rs` | Parse SpiceCursor, cache, convert pixels |

## Success criteria

- Running with `-v` against a real server shows
  `cursor: set: ... cursor_type=0, 24x24` (or similar)
  instead of just position.
- `CursorShape` events appear in `/tmp/ryll.log`.
- FROM_CACHE lookups succeed when the server reuses a
  cursor shape.
- `pre-commit run --all-files` passes.
