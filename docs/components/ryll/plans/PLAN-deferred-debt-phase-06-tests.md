# Phase 6: Test coverage

Part of [PLAN-deferred-debt.md](/components/ryll/plans/PLAN-deferred-debt/).

## Scope

Add tests for modules with zero or minimal coverage.
Five sub-tasks, each targeting a different area of the
codebase. Each produces one commit.

| Step | Description | Source |
|------|-------------|--------|
| 6a | QUIC decoder tests | PLAN-display-iteration-followups.md |
| 6b | JPEG parsing tests | PLAN-pr20-followup.md |
| 6c | Display message parsing tests | PLAN-display-iteration-followups.md |
| 6d | Audio tests | PLAN-pr23-followup.md |
| 6e | Input and clipboard tests | PLAN-pr20-followup.md |

## Agent guidance

### Phase plan effort

This phase plan was written at **high effort** because
it required reading function signatures, understanding
codec internals, and determining what makes a useful test
vector for each target. The implementation steps are
mostly **medium effort** -- the briefs below provide
enough context that the implementing agent doesn't need
to repeat the research.

### Execution model

All implementation is done by sub-agents. The management
session spawns each sub-agent with the brief from this
plan, reviews the output, then commits if satisfied.

### Build and test commands

Sub-agents should know:
- Build and lint: `./scripts/check-rust.sh check`
  (runs rustfmt and clippy via Docker).
- Run tests: use Docker directly:
  `docker run --rm -v "$(pwd)":/workspace
  -v "$(pwd)/.cargo-cache/registry":/build/.cargo/registry
  -v "$(pwd)/.cargo-cache/git":/build/.cargo/git
  -w /workspace -u "$(id -u):$(id -g)" -e HOME=/build
  ryll-dev cargo test --workspace`
- Pre-commit: `pre-commit run --all-files`
- Cargo is NOT available on the host -- all Rust
  commands must go through the Docker container or the
  check-rust.sh script.

### Step-level guidance

| Step | Effort | Model | Isolation | Brief summary |
|------|--------|-------|-----------|---------------|
| 6a | high | opus | none | Requires understanding the 1500-line QUIC codec to construct meaningful error-case tests; the file is too large for sonnet's 200K context alongside the brief |
| 6b | medium | sonnet | none | JPEG format is well-known; test construction is mechanical. Functions must be made pub(crate) first. Brief provides all needed context. |
| 6c | medium | sonnet | none | Protocol structs have pub read() methods; construct byte arrays matching the wire format. Brief specifies exact byte layouts. |
| 6d | medium | sonnet | none | VolumeControl is trivial; Resampler must be made pub(crate) first, then test with known input/output. Brief explains the API. |
| 6e | medium | sonnet | none | key_to_scancode is pub and pure; test a representative sample of the 50+ key mappings. Brief lists expected scancodes. |

## 6a. QUIC decoder tests

### Target

`quic_decode()` in
`shakenfist-spice-compression/src/quic.rs` (~1500 lines,
zero tests).

```rust
pub fn quic_decode(
    data: &[u8], width: u32, height: u32
) -> Option<Vec<u8>>
```

Takes compressed image data, returns
`Option<Vec<u8>>` of RGBA pixels (width × height × 4
bytes).

### Approach

The QUIC codec (Golomb coding, adaptive model buckets,
three colour spaces) is too complex to construct test
vectors by hand. Two viable approaches:

**Option A: Capture-based tests.** Run a real SPICE
session with `--capture`, extract QUIC-encoded draw_copy
payloads from the pcap, and use them as test fixtures.
Store as `tests/fixtures/quic_*.bin` alongside expected
pixel checksums.

**Option B: Reference encoder.** The SPICE server's
QUIC encoder is in
`/srv/src-reference/qemu/qemu/ui/spice-display.c` and
`/srv/src-reference/spice/spice-common/common/quic.c`.
Write a small C program that encodes a known image (e.g.
solid red 8×8) and save the output as a test fixture.

Either way, the test structure is:

```rust
#[test]
fn test_quic_decode_8x8_rgb32() {
    let data = include_bytes!("../tests/fixtures/quic_8x8.bin");
    let result = quic_decode(data, 8, 8);
    assert!(result.is_some());
    let pixels = result.unwrap();
    assert_eq!(pixels.len(), 8 * 8 * 4);
    // Verify known pixel values or checksum
}
```

Also add a graceful-failure test:

```rust
#[test]
fn test_quic_decode_truncated() {
    let result = quic_decode(&[0x51, 0x55, 0x49, 0x43], 8, 8);
    assert!(result.is_none());
}
```

### Brief for sub-agent

Read `shakenfist-spice-compression/src/quic.rs` to
understand the `quic_decode` entry point. The QUIC magic
is `b"QUIC"` (4 bytes). The file has zero tests. Add a
`#[cfg(test)] mod tests` at the bottom with:

1. A test that verifies `quic_decode` returns `None` for
   truncated input (just the magic bytes, or empty).
2. A test that verifies `quic_decode` returns `None` for
   corrupted/garbage data.
3. If you can construct a minimal valid QUIC bitstream
   from reading the code (the header format starts with
   magic + version + type + width + height), add a test
   that verifies the header is parsed correctly even if
   decompression fails. Otherwise, note that capture-
   based test vectors are needed for positive tests.

Do not attempt to manually construct valid compressed
QUIC data -- the codec is too complex. Focus on error
handling and edge cases.

### Files to modify

- `shakenfist-spice-compression/src/quic.rs` -- add
  tests module

## 6b. JPEG parsing tests

### Target

Three private functions in
`ryll/src/channels/display.rs`:

```rust
fn extract_dht_segments(jpeg: &[u8]) -> Vec<u8>
fn inject_dht(jpeg: &[u8], dht: &[u8]) -> Vec<u8>
fn decode_mjpeg_frame(data: &[u8])
    -> Option<(Vec<u8>, u32, u32)>
```

### Prerequisite

Make these functions `pub(crate)` so they can be tested
from a `#[cfg(test)]` module within the file. They are
currently private.

### Test cases

**extract_dht_segments:**
- JPEG with DHT marker (0xFF 0xC4): returns the segment.
- JPEG without DHT: returns empty Vec.
- JPEG with multiple DHT markers: returns all
  concatenated.
- Minimal valid JPEG: SOI + SOF + DHT + SOS + EOI.

**inject_dht:**
- JPEG without DHT: DHT is inserted after SOI.
- JPEG with APP0 marker: DHT is inserted after APP0.
- JPEG with APP0 + APP1: DHT is inserted after both.
- Empty JPEG (just SOI + EOI): DHT is inserted between.

**decode_mjpeg_frame:**
- Valid small JPEG (e.g. 2×2 RGB): returns RGBA pixels.
- Truncated JPEG: returns None.
- JPEG with L8 (grayscale) pixel format: returns RGBA
  with R=G=B=gray.

### Brief for sub-agent

In `ryll/src/channels/display.rs`, change
`fn extract_dht_segments`, `fn inject_dht`, and
`fn decode_mjpeg_frame` from `fn` to `pub(crate) fn`.

Then add a `#[cfg(test)] mod tests` block at the bottom
of the file. For JPEG test data, construct minimal
valid JPEG byte arrays. A minimal JPEG is:
`[0xFF, 0xD8]` (SOI) + markers + `[0xFF, 0xD9]` (EOI).
The DHT marker is `0xFF, 0xC4` followed by a 2-byte
big-endian length.

For `decode_mjpeg_frame`, use the `image` crate (already
a dependency) to encode a 2×2 RGB image to JPEG in
memory, then decode it with `decode_mjpeg_frame` and
verify the pixel values.

Don't test `decode_mjpeg_frame` with hand-crafted JPEG
bytes -- use the `image` crate to generate valid JPEG
data programmatically.

### Files to modify

- `ryll/src/channels/display.rs` -- change visibility,
  add tests module

## 6c. Display message parsing tests

### Target

Two public structs in
`shakenfist-spice-protocol/src/messages.rs`:

```rust
// 21-byte fixed header + variable clip rects
pub fn DrawCopyBase::read(data: &[u8])
    -> io::Result<Self>

// 18-byte fixed struct
pub fn ImageDescriptor::read(data: &[u8])
    -> io::Result<Self>
```

### Test cases

**DrawCopyBase::read:**
- Minimal 21 bytes with clip_type=0 (no clip rects):
  verify surface_id, left, top, right, bottom,
  end_offset=21.
- With clip_type=1 and 2 clip rects: verify
  clip_rects contains the two rectangles,
  end_offset=21+4+2*16=41.
- Too-short input (< 21 bytes): returns error.

**ImageDescriptor::read:**
- Valid 18-byte descriptor: verify image_id, image_type,
  flags, width, height.
- Too-short input (< 18 bytes): returns error.

### Brief for sub-agent

Add tests to the existing test infrastructure in
`shakenfist-spice-protocol/src/messages.rs`. Both
`DrawCopyBase` and `ImageDescriptor` have `pub fn read`
methods. Construct byte arrays in little-endian format
matching the wire layout.

`DrawCopyBase` wire format (21 bytes):
- surface_id: u32 (offset 0)
- top: u32 (offset 4), left: u32 (offset 8)
- bottom: u32 (offset 12), right: u32 (offset 16)
- clip_type: u8 (offset 20)
- If clip_type == 1: u32 count, then count × (u32 top,
  u32 left, u32 bottom, u32 right) rects follow.

`ImageDescriptor` wire format (18 bytes):
- image_id: u64 (offset 0)
- image_type: u8 (offset 8)
- flags: u8 (offset 9)
- width: u32 (offset 10)
- height: u32 (offset 14)

All fields are little-endian. Use the `read_u32_le` etc.
helpers from `crate::parse` if convenient, or construct
raw byte arrays directly.

### Files to modify

- `shakenfist-spice-protocol/src/messages.rs` -- add
  tests

## 6d. Audio tests

### Target

`VolumeControl` (public) and `Resampler` (private) in
`ryll/src/channels/playback.rs`.

### Prerequisite

Make `Resampler` and `Resampler::new` / `next_frame`
`pub(crate)` so they can be tested.

### Test cases

**VolumeControl:**
- Default volume is 80, not muted.
- `set_volume(150)` clamps to 100.
- `effective_volume()` returns 0.0 when muted.
- `effective_volume()` returns 0.8 at default.
- Mute/unmute cycle preserves volume.

**Resampler:**
- 1:1 ratio (48000→48000), mono: output equals input.
- 1:1 ratio, stereo: output preserves L/R separation.
- 2:1 upsampling (24000→48000), mono: output has
  interpolated intermediate samples.
- Underrun: returns silence (zeros) without modifying
  the buffer.
- Empty buffer: returns silence immediately.

### Brief for sub-agent

In `ryll/src/channels/playback.rs`:

1. Change `struct Resampler` from private to
   `pub(crate) struct Resampler`.
2. Change `Resampler::new` and `Resampler::next_frame`
   from `fn` to `pub(crate) fn`.
3. Add a `#[cfg(test)] mod tests` block.

`VolumeControl` is already public. Test it via
`VolumeControl::new()` which returns `Arc<VolumeControl>`.

`Resampler::new(from_rate, to_rate, channels)` creates a
resampler. `next_frame(&mut self, buffer, out)` takes a
`&mut VecDeque<i16>` and a `&mut [i16]` output slice.
For mono, the out slice is 1 element; for stereo, 2.

For a 1:1 mono test: push `[100, 200, 300]` into a
VecDeque, call `next_frame` three times, and verify the
output. The resampler needs 2 frames of lookahead for
interpolation, so you need at least `(idx+2)*channels`
samples in the buffer.

### Files to modify

- `ryll/src/channels/playback.rs` -- change visibility,
  add tests

## 6e. Input and clipboard tests

### Target

`key_to_scancode()` (public) in
`ryll/src/channels/inputs.rs`.

```rust
pub fn key_to_scancode(key: egui::Key)
    -> Option<(u32, u32)>
```

Returns `Option<(press_scancode, release_scancode)>`.
Release scancode = press | 0x80 for standard keys, or
uses E0-extended format for navigation/arrow keys.

### Test cases

- Letter key (e.g. `Key::A`): returns `Some((0x1E, 0x9E))`.
- Function key (e.g. `Key::F1`): returns `Some((0x3B, 0xBB))`.
- Arrow key (e.g. `Key::ArrowUp`): returns E0-extended
  scancode. The `make_scancode` helper encodes extended
  keys as `(0xE0 << 8) | code`, so ArrowUp = 0xE048,
  release = 0xE0C8.
- Non-mapped key: returns `None`.

### Brief for sub-agent

In `ryll/src/channels/inputs.rs`, add a
`#[cfg(test)] mod tests` block. The `key_to_scancode`
function is already `pub`. Import `egui::Key` for test
inputs.

Test a representative sample: at least one letter
(Key::A → 0x1E), one digit (Key::Num0 → 0x0B), one
function key (Key::F1 → 0x3B), one arrow key
(Key::ArrowUp → should be E0-extended), Space (0x39),
Enter (0x1C), and Escape (0x01). Verify both press
and release scancodes.

Also test that a key not in the map (e.g.
`Key::F20` if it exists, or any unmapped variant)
returns `None`.

The scancode values are in the static `SCANCODE_MAP`
HashMap in the same file. Read it to get the expected
values -- don't guess them.

### Files to modify

- `ryll/src/channels/inputs.rs` -- add tests module

### Items deferred from this phase

The following test targets from the original master plan
are deferred because they require async test harnesses
or integration scaffolding beyond simple unit tests:

- **Agent data message chunking**: `send_agent_data_message`
  is private and async, requiring a mock SpiceStream.
- **Clipboard grab/request/data round-trip**: Private
  async methods with network I/O.
- **Keepalive timeout triggering disconnect**: Requires
  async timer interaction.
- **Stream create/data/destroy lifecycle**: Requires full
  display channel setup.

These are better addressed as integration tests in a
dedicated plan, not as part of this unit test phase.

## Commit sequence

1. **6c**: Message parsing tests (protocol crate,
   independent).
2. **6d**: Audio tests (VolumeControl + Resampler).
3. **6e**: Input scancode tests.
4. **6b**: JPEG parsing tests (requires visibility
   change in display.rs).
5. **6a**: QUIC decoder tests (may require test fixtures
   from a real session).

Steps 6c, 6d, and 6e are independent and can be done
in any order. Step 6b requires a visibility change.
Step 6a may need to be split into error-case tests
(implementable now) and positive tests (requiring
captured test vectors, deferred).

## Administration

### Back brief

Before executing any step of this plan, please back
brief the operator as to your understanding of the plan
and how the work you intend to do aligns with that plan.
