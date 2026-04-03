# Remaining issues after initial bring-up

## Status

As of 2026-03-31, ryll can connect to a real Shaken Fist
VM through the kerbside SPICE proxy, render the display,
and accept keyboard input. The UEFI latency guest on a
local QEMU instance works fully. The following issues
remain.

## 1. GLZ cross-frame image corruption

**Symptom**: Random areas of the display have incorrect
pixels after incremental updates. The corruption appears
in regions updated by ZLIB_GLZ_RGB draw_copy messages.

**Likely cause**: The GLZ decompressor's cross-frame
reference handling. GLZ can reference pixels from
previously decompressed images by ID. The cache lookup
or pixel copy logic may have bugs for certain offset
or distance values. The decompressor was rewritten from
the kerbside Python reference but may have edge cases
in the image_dist calculation or pixel_offset handling.

**Approach**: Compare the Rust GLZ decompressor against
the Python reference line-by-line, focusing on the
cross-image reference path (image_dist != 0). Add
logging for cross-frame references to see which source
images are being looked up and whether they exist in
the cache. The reference sources at
`/srv/src-reference/spice/spice-html5/` may have an
independent JavaScript implementation to cross-check.

## 2. Mouse clicks not working through kerbside proxy

**Symptom**: MOUSE_PRESS and MOUSE_RELEASE messages are
sent on the wire and the server acknowledges mouse
position updates (MOUSE_MOTION_ACK), but clicks produce
no display response. The Python test tool
(`tools/test_click.py`) confirmed the same behaviour
using kerbside's own SpiceClient library — key presses
produce draw_copy responses but clicks do not.

**Likely cause**: Unknown. The wire format matches the
kerbside Python reference and the spice-gtk C reference.
Possible causes:
- The VM login screen may require the SPICE agent to
  translate absolute mouse positions to guest events.
  If the agent isn't running, absolute positioning may
  not work.
- The kerbside proxy may need to see specific
  capabilities or mouse mode negotiation.
- The VM guest may be in server mouse mode (mode=1)
  and need relative MOUSE_MOTION instead of absolute
  MOUSE_POSITION for click targeting.

**Approach**: Test with `virt-viewer` or the Python ryll
test client to confirm whether clicks work at all through
this kerbside instance. If they do, capture the wire
traffic and compare. If they don't, the issue is
server-side.

## 3. Verbose logging cleanup

Many INFO-level log messages were added during debugging
and should be demoted to DEBUG before release:
- `display: draw_copy:` per-message details
- `inputs: key down/up:` per-keystroke logging
- `inputs: mouse down:` per-click logging
- `cursor: init/set/move:` per-message logging
- `app: cursor position:` per-event logging
- `app: cursor shape:` per-event logging
- `LZ4 header:` hex dump

These are useful with `-v` but too noisy for default use.

## 4. Documentation updates needed

- README.md should mention ZLIB_GLZ_RGB and the corrected
  image type enum values.
- ARCHITECTURE.md should document the image decompression
  pipeline (LZ, GLZ, ZLIB_GLZ_RGB, LZ4, Pixmap).
- STYLEGUIDE.md should note the `data_size` prefix rule:
  LZ_RGB and GLZ_RGB have it, LZ4 does not,
  ZLIB_GLZ_RGB has its own 8-byte header.
