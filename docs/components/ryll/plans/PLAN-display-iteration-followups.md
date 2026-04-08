# Display Iteration Follow-ups

Deferred work identified during the PUSH-TEMPLATE review of the display
rendering, QUIC decode, multi-monitor and agent support PR.

## Status: Open

## GLZ cross-image retry loop

The GLZ decompressor uses a polling retry loop (20 retries x 5ms sleep)
when a cross-image reference is not yet in the shared dictionary. This
works but adds up to 100ms latency per image on cross-channel misses.

**Improvement:** Replace the polling loop with a `tokio::sync::Notify`
or `tokio::sync::watch` so the decompressor wakes immediately when
another channel inserts the referenced image.

**Files:** `src/decompression/glz.rs` (CROSS_REF_MAX_RETRIES /
CROSS_REF_RETRY_MS constants and the retry loop around line 210).

## QUIC decoder test coverage

The QUIC decoder (`src/decompression/quic.rs`, ~1500 lines) has zero
test coverage. At minimum we should add:

- Unit tests with known-good QUIC bitstreams (test vectors from the
  reference C implementation or captured from a real SPICE session).
- Fuzz-style tests with truncated or corrupted bitstreams to verify
  graceful failure (no panics).
- Round-trip tests if we ever add a QUIC encoder.

## Missing image type support

The image type enum defines 12 types; 8 are implemented. The following
are not yet supported and will log a warning if encountered:

- **LzPalette** — paletted LZ images
- **FromCacheLossless** — lossless variant of image cache
- **Surface** — surface-to-surface copy
- **JpegAlpha** — JPEG with separate alpha plane

These are uncommon in modern SPICE sessions but could appear with
certain guest drivers or older QEMU versions.

## Byte-parsing helper opportunity

There are ~30 instances of manual `u32::from_le_bytes([data[offset],
data[offset+1], ...])` patterns across display.rs, main_channel.rs,
and messages.rs. A `read_u32_le(data, offset)` helper would reduce
repetition. Low priority — the current code is correct and clear.

## DrawCopyBase and agent infrastructure test coverage

Neither `DrawCopyBase::read()` (with variable-length clip rects) nor
the agent message handling in `main_channel.rs` have unit tests. The
clip rect parsing in particular handles untrusted input and would
benefit from test coverage.
