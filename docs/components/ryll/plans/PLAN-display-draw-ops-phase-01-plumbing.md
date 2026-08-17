# Phase 1: Plumbing

Part of [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

Key references for this phase:

* `/srv/src-reference/spice/spice-common/common/draw.h` —
  canonical definitions of `SpiceBrush`, `SpiceQMask`,
  `SpicePoint`, `SpiceFill`, `SpiceBlackness`,
  `SpiceWhiteness`, `SpiceInvers`, `SpiceOpaque`,
  `SpiceTransparent`, `SpiceAlphaBlend`.
* `/srv/src-reference/spice/spice-protocol/spice/enums.h` —
  `SPICE_BRUSH_TYPE_*`, `SPICE_ROPD_*`, clip-type constants.
* `/srv/src-reference/spice/spice-html5/src/spicetype.js` —
  the most legible wire-format parser for these structs.
* [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/)
  §"Wire format" for the layout summary the master plan
  already distilled.

## Goal

Land all the protocol, event, and surface plumbing the
remaining phases will need, plus the surface-init colour
fix. Behaviourally the client is unchanged except for the
init-colour: the new structs have unit tests, the new
events exist but nothing sends them yet, and the new
surface helpers exist but nothing calls them yet.

By the end of this phase:

* The protocol crate can parse every draw-op payload the
  master plan enumerates (except image-bearing ops, which
  reuse the existing `ImageDescriptor` path inside
  `display.rs` and only need small protocol additions).
* The channel → app event vocabulary includes the fill,
  copy-bits, and invert cases.
* `DisplaySurface` exposes `fill_rect`, `copy_bits`, and
  `invert_rect` helpers, each correct against their
  edge cases (clipping, aliasing, zero-size).
* `DisplaySurface::new()` initialises to black `(0,0,0)`
  not grey `(50,50,50)`.
* `handle_message`'s `_ =>` arm still reaches every new
  opcode — i.e. no `DRAW_FILL` / `COPY_BITS` routing is
  added yet. That's phase 2+.

## Non-goals

* Wiring `DRAW_FILL`, `COPY_BITS`, etc. through
  `display.rs::handle_message`. Each opcode gets its own
  phase.
* Introducing a `warn_once!` helper. Phase 2 needs it
  first; adding it here means shipping dead code.
* Image-bearing ops (`SpiceOpaque`, `SpiceTransparent`,
  `SpiceAlphaBlend`) only need their *non-image* preamble
  structs in this phase. Image payload parsing is phase
  5 / 6, reusing `display.rs`'s existing
  `ImageDescriptor`-and-decompress path.
* Any change to `app.rs`'s second (`--headless`) event
  loop at `ryll/src/app.rs:2580`. New variants can fall
  into its `_ => {}` arm; they're no-ops in headless mode.

## Current state

* [shakenfist-spice-protocol/src/messages.rs:255](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/messages.rs#L255)
  defines `DrawCopyBase` — the generic
  `SpiceMsgDisplayBase` (surface_id, box, clip). Misnamed;
  every draw op uses it. Rename to `DrawBase`.
* [ryll/src/channels/display.rs:21](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L21)
  imports `DrawCopyBase`; that's the only ryll consumer.
* [ryll/src/channels/mod.rs:46](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs#L46)
  `ChannelEvent::ImageReady` is the only "draw-result"
  variant today.
* [ryll/src/display/surface.rs:16-26](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs#L16-L26)
  `DisplaySurface::new()` fills with `(50, 50, 50, 255)`.
* [ryll/src/app.rs:562](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L562) handles
  `ImageReady`. No `FillRect`, `CopyBits`, or `Invert`
  handlers exist yet.

## Wire-format summary

Offsets are from the start of each struct, little-endian,
no padding.

### `SpicePoint` (8 bytes)

| Off | Size | Field |
|-----|------|-------|
| 0   | 4    | x (i32 LE) |
| 4   | 4    | y (i32 LE) |

Both call sites in this plan use the canonical
`SpicePoint` type: `SpiceQMask.pos` (via
`/srv/src-reference/spice/spice-common/common/draw.h:51-54,
175-179`) and `QXLCopyBits.src_pos` (via `QXLPoint` in
`/srv/src-reference/spice/spice-protocol/spice/qxl_dev.h:112-115,
383-385`). Both are `int32_t`. Values are non-negative in
practice but the protocol doesn't promise that, so we
preserve the sign in the parser and let call sites
handle negatives defensively (clip to zero for
`COPY_BITS.src_pos`, keep as signed offset for mask pos).

### `SpiceBrush` (variable, tagged union)

| Off | Size | Field |
|-----|------|-------|
| 0   | 1    | type (0=NONE, 1=SOLID, 2=PATTERN) |
| 1   | var  | body — depends on type |

Bodies:

* type=0 (NONE): no body.
* type=1 (SOLID): `uint32 color` (BGRX).
* type=2 (PATTERN): `uint64 pat_bitmap_offset;
  SpicePoint pos;` (16 bytes). `pat_bitmap_offset` is a
  byte offset into the surrounding message payload, same
  convention as `SpiceCopy`'s `src_bitmap` pointer.

The parser returns a `SpiceBrush` enum plus the number of
bytes consumed so callers can advance their cursor.

### `SpiceQMask` (13 bytes)

| Off | Size | Field |
|-----|------|-------|
| 0   | 1    | flags (bit 0 = INVERS) |
| 1   | 8    | pos (SpicePoint32 = two i32) |
| 9   | 4    | bitmap_offset (u32, byte offset, 0 = null) |

### `SpiceFill`

| Off | Size | Field |
|-----|------|-------|
| 0   | var  | brush (SpiceBrush) |
| var | 2    | rop_descriptor (u16) |
| var | 13   | mask (SpiceQMask) |

Total size is `1 + brush_body + 2 + 13`. The parser
returns the struct and `end_offset` so the caller can
locate any trailing bitmap bytes referenced by the brush
or mask.

### `SpiceBlackness` / `SpiceWhiteness` / `SpiceInvers`

All three are identical:

| Off | Size | Field |
|-----|------|-------|
| 0   | 13   | mask (SpiceQMask) |

One parser, three type aliases.

### `SpiceOpaque`

| Off | Size | Field |
|-----|------|-------|
| 0   | 4    | src_bitmap (u32 offset) |
| 4   | 16   | src_area (SpiceRect: top, left, bottom, right as u32) |
| 20  | var  | brush (SpiceBrush) |
| var | 2    | rop_descriptor (u16) |
| var | 1    | scale_mode (u8) |
| var | 13   | mask (SpiceQMask) |

### `SpiceTransparent`

| Off | Size | Field |
|-----|------|-------|
| 0   | 4    | src_bitmap (u32 offset) |
| 4   | 16   | src_area (SpiceRect) |
| 20  | 4    | src_color (u32, BGRX) |
| 24  | 4    | true_color (u32, BGRX) |

### `SpiceAlphaBlend`

| Off | Size | Field |
|-----|------|-------|
| 0   | 2    | alpha_flags (u16) |
| 2   | 1    | alpha (u8) |
| 3   | 4    | src_bitmap (u32 offset) |
| 7   | 16   | src_area (SpiceRect) |

Total 23 bytes.

Note: `spice-common`'s `draw.h` declares the
`SpiceImage *` fields as a pointer; on the wire that is a
`uint32` byte offset into the current message (identical
to `SpiceCopy.src_bitmap`, which `display.rs` already
parses this way). In this phase we only parse the offset;
image decode is phase 5/6.

### `COPY_BITS` payload

| Off | Size | Field |
|-----|------|-------|
| 0   | var  | SpiceMsgDisplayBase (DrawBase) |
| var | 8    | src_pos (SpicePoint, u32,u32) |

## ChannelEvent additions

In [ryll/src/channels/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs):

```rust
/// Solid-colour fill of a destination rect (used by
/// DRAW_FILL with solid brush, DRAW_BLACKNESS,
/// DRAW_WHITENESS).
FillRect {
    display_channel_id: u8,
    surface_id: u32,
    rect: Rect,               // dest rect from DrawBase
    colour: [u8; 4],          // RGBA
    clip: ClipRects,          // from DrawBase
},

/// Intra-surface pixel copy (DRAW_COPY_BITS).
CopyBits {
    display_channel_id: u8,
    surface_id: u32,
    src_x: u32,
    src_y: u32,
    dest_rect: Rect,
    clip: ClipRects,
},

/// In-place pixel inversion (DRAW_INVERS).
Invert {
    display_channel_id: u8,
    surface_id: u32,
    rect: Rect,
    clip: ClipRects,
},
```

Shape notes:

* **`Rect` / `ClipRects` representation.** `DrawBase`
  today exposes `top/left/bottom/right: u32` and
  `clip_rects: Vec<(u32, u32, u32, u32)>`. Adding
  dedicated `Rect` and `ClipRects` types in this phase is
  tempting but scope-creeps the rename; instead, mirror
  the existing shape: `rect: (u32, u32, u32, u32)` as
  `(left, top, right, bottom)` and `clip: Vec<(u32, u32,
  u32, u32)>`. `#[derive(Debug, Clone)]` carries through.
* **`display_channel_id` and `surface_id`.** Match
  `ImageReady` so the app-side dispatch looks identical.
* **`Debug + Clone`.** `ChannelEvent` already derives
  both; the new variants must too.

## Surface helpers

In [ryll/src/display/surface.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs):

### Signature sketch

```rust
impl DisplaySurface {
    /// Paint `colour` (RGBA) into `rect` on this surface,
    /// clipped to the surface bounds and to `clip`
    /// (empty = no extra clipping).
    pub fn fill_rect(
        &mut self,
        left: u32, top: u32, right: u32, bottom: u32,
        colour: [u8; 4],
        clip: &[(u32, u32, u32, u32)],
    );

    /// Copy `(w, h)` pixels from (`src_x`, `src_y`) on
    /// this surface to `(dest_left, dest_top)` — handles
    /// overlapping src/dst correctly (memmove semantics).
    pub fn copy_bits(
        &mut self,
        src_x: u32, src_y: u32,
        dest_left: u32, dest_top: u32, dest_right: u32, dest_bottom: u32,
        clip: &[(u32, u32, u32, u32)],
    );

    /// Invert the RGB channels of every pixel in `rect`,
    /// leaving alpha unchanged. Clipped same as fill_rect.
    pub fn invert_rect(
        &mut self,
        left: u32, top: u32, right: u32, bottom: u32,
        clip: &[(u32, u32, u32, u32)],
    );
}
```

### Behaviour rules

* All three helpers set `self.dirty = true` when they
  write at least one pixel, and do nothing otherwise.
* All three clip to the surface bounds *and* to each
  `clip` rect (if any). Empty `clip` = no clip rects =
  surface-bounds clipping only. Multiple `clip` rects =
  union (paint where any rect applies), matching SPICE
  `SPICE_CLIP_TYPE_RECTS` semantics.
* `fill_rect` and `invert_rect` tolerate empty rects and
  rects wholly outside the surface (no-op, no panic).
* `copy_bits` handles aliasing by snapshotting the source
  rect before the write (the spice-html5 strategy the
  master plan calls out). A ~512-pixel scratch `Vec<u8>`
  per call is acceptable for phase 1; optimisation is
  follow-up work.
* `copy_bits` must also handle source rects that extend
  past the surface edge by clipping *both* source and
  destination (shrink the rect so neither overflows).

### Unit tests (placed in `surface.rs` alongside the
impl):

* `fill_rect_within_bounds` — 4×4 surface, fill
  (1,1)-(3,3) red, assert affected/unaffected pixels.
* `fill_rect_clipped_to_surface` — fill partially
  off-surface, assert no panic and only on-surface
  pixels change.
* `fill_rect_with_clip_union` — two disjoint clip rects,
  assert only their union is painted.
* `fill_rect_noop` — empty rect → `is_dirty()` remains
  whatever it was before the call.
* `copy_bits_non_overlapping` — copy (0,0)-(2,2) to
  (2,0), assert destination matches original source.
* `copy_bits_overlapping_down` — copy a row to the row
  below (simulating console scroll), assert every
  destination pixel matches the original source row even
  though they now overlap. This is the canonical BIOS
  scroll case.
* `copy_bits_overlapping_right` — shift a column
  rightward by one, same assertion in the other axis.
* `copy_bits_source_off_surface` — source rect extends
  past right edge; assert clipping does not panic and
  only the in-bounds portion is copied.
* `invert_rect_basic` — fill with (100, 150, 200, 255),
  invert, assert pixels are (155, 105, 55, 255).
* `surface_init_is_black` — `DisplaySurface::new(0, 2,
  2).pixels()` returns `[0, 0, 0, 255]` for every pixel.

### Correctness notes for `copy_bits`

`pixels` is `Vec<u8>` laid out as `height` rows of
`width * 4` RGBA bytes. The aliasing-safe strategy:

1. Clip src and dst to surface bounds, taking care that
   shrinking src also shrinks dst by the same amount on
   the corresponding edge.
2. Clip dst against each `clip` rect; if the rect list is
   empty, only surface bounds apply.
3. Allocate `Vec<u8>` of `effective_w * effective_h * 4`
   bytes, copy src rows into it.
4. Copy from the scratch buffer into the dst rect row by
   row.

Matches spice-html5's `getImageData`/`putImageData`
pattern exactly. Clearer than a direction-aware in-place
copy, and the allocation is bounded by the SPICE wire
rect size (typically a console line or a small square).

## App-side handlers

In [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) inside the
main (non-headless) `match event` at ~line 529:

```rust
ChannelEvent::FillRect {
    display_channel_id, surface_id,
    rect: (left, top, right, bottom),
    colour,
    clip,
} => {
    let Some(surface) = self
        .surfaces
        .get_mut(&(display_channel_id, surface_id))
    else {
        // Auto-create? DRAW_FILL / BLACKNESS / WHITENESS
        // against an uncreated surface is very unusual
        // (QXL always emits SURFACE_CREATE first for
        // these) — debug! and drop for phase 1.
        debug!(
            "app: FillRect on unknown surface {}",
            surface_id
        );
        continue;
    };
    surface.fill_rect(left, top, right, bottom, colour, &clip);
    self.stats.frames_received += 1;
}

ChannelEvent::CopyBits {
    display_channel_id, surface_id,
    src_x, src_y,
    dest_rect: (left, top, right, bottom),
    clip,
} => { ... similar ... }

ChannelEvent::Invert {
    display_channel_id, surface_id,
    rect: (left, top, right, bottom),
    clip,
} => { ... similar ... }
```

In every case, end with `self.repaint_notify.notify_one()`
if the surface reports it became dirty — look at how
`ImageReady` currently does it (it unconditionally
notifies; matching that is fine in this phase).

Confirm these handlers compile even though no channel
sends them yet; `cargo build` will flag unused variants
only if `#[warn(unused)]`-style lints apply, which they
don't here (`ChannelEvent` is `pub`).

The second (headless) event loop at
[ryll/src/app.rs:2580](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L2580)
already has a `_ => {}` default arm — the new variants
fall into it unchanged. Headless mode doesn't render, so
a no-op is correct.

## Protocol-crate changes in detail

### Rename `DrawCopyBase` → `DrawBase`

Mechanical across two files: the struct and its `impl`
in `messages.rs`, and the `use` line in
[ryll/src/channels/display.rs:21](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L21).
The documentation-level plans
([PLAN-crate-extraction-phase-04-protocol.md](/components/ryll/plans/PLAN-crate-extraction-phase-04-protocol/),
[PLAN-deferred-debt-phase-06-tests.md](/components/ryll/plans/PLAN-deferred-debt-phase-06-tests/),
[PLAN-deferred-debt.md](/components/ryll/plans/PLAN-deferred-debt/),
[PLAN-display-iteration-followups.md](/components/ryll/plans/PLAN-display-iteration-followups/))
may reference the old name — update them if the reference
is a code citation; leave them alone if it's historical
narrative. The master plan
[PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/)
already uses `DrawBase` prose.

No `pub use DrawCopyBase` re-export is needed for
backwards compatibility — the protocol crate is internal
to the workspace.

### New parser structs

Each gets `Debug + Clone`, a `SIZE` constant where the
wire size is fixed, and a `pub fn read(data: &[u8]) ->
io::Result<Self>` that matches the style of the existing
`DrawCopyBase::read`. For `SpiceBrush` and `SpiceFill`,
`read` also reports bytes consumed so the caller can
advance past a variable-sized brush body:

```rust
pub fn read(data: &[u8]) -> io::Result<(Self, usize)>;
```

Parsers should reject unknown brush types with a clear
`io::Error` (`InvalidData`) — the call sites will map
that to a warn-once in later phases, but in phase 1 we
just need the error to be distinguishable.

### Parser unit tests

One module per struct, co-located in `messages.rs`. Each
test covers:

* Minimal valid input → parsed fields match expected.
* Too-short input → returns `UnexpectedEof`.
* For `SpiceBrush`: one test per brush type (NONE,
  SOLID, PATTERN), plus "unknown brush type = 99 →
  InvalidData".
* For `SpiceFill` and `SpiceOpaque`: exercise each
  brush-type branch (at minimum SOLID and PATTERN), so
  the variable-size end offset is covered.
* For `SpiceQMask`: `bitmap_offset == 0` (null) and
  `!= 0` (non-null) cases.

Reuse the existing `DrawCopyBase` test block
[messages.rs:639-720](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/messages.rs#L639)
as a style reference (manual byte construction, explicit
expected values, `expect()` on the parse, `assert_eq!`
on fields).

## Files touched

| File | Change |
|------|--------|
| `shakenfist-spice-protocol/src/messages.rs` | Rename `DrawCopyBase`→`DrawBase`; add `SpicePoint`, `SpiceBrush`, `SpiceQMask`, `SpiceFill`, `SpiceBlackness` (+ `Whiteness`/`Invers` aliases), `SpiceOpaque`, `SpiceTransparent`, `SpiceAlphaBlend`; unit tests for each. |
| `ryll/src/channels/display.rs` | Update `use` for `DrawBase`. |
| `ryll/src/channels/mod.rs` | Add `FillRect`, `CopyBits`, `Invert` variants on `ChannelEvent`. |
| `ryll/src/display/surface.rs` | Add `fill_rect`, `copy_bits`, `invert_rect` + tests; change init colour to `(0,0,0,255)`. |
| `ryll/src/app.rs` | Add handlers for the three new variants in the main event loop. Headless loop unchanged. |

Documentation files (`ARCHITECTURE.md`, `AGENTS.md`,
`README.md`) are **not** updated in phase 1; phase 9
collects all doc updates in one commit.

## Open questions

1. **Surface-init colour bundled or split?** The master
   plan calls the init-colour fix "a separate, much
   smaller bug fix". I recommend landing it as its own
   commit within phase 1 (step 1c below). Rationale:
   reviewers can revert it independently if black turns
   out to be wrong for some reason we haven't spotted;
   it's also a trivially testable one-line change that
   doesn't need to wait on the protocol additions.

2. **`SpicePoint` signed vs unsigned.** Resolved: both
   call sites (`SpiceQMask.pos` in
   `/srv/src-reference/spice/spice-common/common/draw.h`
   and `QXLCopyBits.src_pos` via `QXLPoint` in
   `/srv/src-reference/spice/spice-protocol/spice/qxl_dev.h`)
   are `int32_t`. One parser — `SpicePoint { x: i32, y:
   i32 }` — covers both. The `COPY_BITS` handler in phase
   4 will clip negatives to zero; this phase does not
   need to worry about that.

3. **Should `ChannelEvent` carry a `Rect` newtype now?**
   Resolved: no. Phase 1 keeps the tuple-of-four shape
   to avoid churning `ImageReady` and every existing call
   site. Tracked as deferred work in the "Future work"
   section of
   [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/)
   so it isn't forgotten.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high   | opus   | none | Add protocol structs to `shakenfist-spice-protocol/src/messages.rs`: `SpicePoint` (`i32, i32`), `SpiceBrush`, `SpiceQMask`, `SpiceFill`, `SpiceBlackness` (alias `SpiceWhiteness`/`SpiceInvers`), `SpiceOpaque`, `SpiceTransparent`, `SpiceAlphaBlend`. Match the style of `DrawCopyBase::read`. Unit tests per struct covering valid/short/union-branch inputs. Do NOT rename `DrawCopyBase` in this step. Do NOT touch ryll/*. |
| 1b | medium | sonnet | none | Rename `DrawCopyBase` → `DrawBase` across `shakenfist-spice-protocol/src/messages.rs` and `ryll/src/channels/display.rs`. Update the `_tests` block accordingly. Do not rename anything else. |
| 1c | low    | sonnet | none | In `ryll/src/display/surface.rs`, change the init colour from `(50, 50, 50, 255)` to `(0, 0, 0, 255)`. Add a `surface_init_is_black` unit test. No other changes. |
| 1d | high   | opus   | none | In `ryll/src/display/surface.rs`, add `fill_rect`, `copy_bits`, `invert_rect` methods per the "Surface helpers" section of this plan. Implement clipping against surface bounds and against each `clip` rect (union semantics). `copy_bits` must snapshot the source rect to handle aliasing. Add the ten unit tests listed under "Unit tests". No other ryll changes. |
| 1e | medium | sonnet | none | In `ryll/src/channels/mod.rs`, add `ChannelEvent::FillRect`, `ChannelEvent::CopyBits`, `ChannelEvent::Invert` with the field sets given in this plan. Derive `Debug + Clone`. No other changes. |
| 1f | medium | sonnet | none | In `ryll/src/app.rs`'s main event loop (near the existing `ChannelEvent::ImageReady` arm), add handlers for `FillRect`, `CopyBits`, `Invert` that look up the surface in `self.surfaces`, call the corresponding helper on it, bump `self.stats.frames_received`, and trigger `self.repaint_notify.notify_one()`. If the surface is unknown, `debug!` and drop. Do not alter the headless event loop. |
| 1g | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`; report any failures with file/line references. Do not fix failures; surface them to the management session. |

**Sequencing.** 1a–1c are independent and can run in
parallel. 1d is independent of 1a–1c (surface.rs doesn't
depend on protocol changes). 1e depends on nothing. 1f
depends on 1d and 1e. 1g runs last.

**Review gates.** The management session reviews after
each step completes (not only at the end). 1a is the
largest code change and warrants careful reading against
`draw.h`.

## Success criteria

* `cargo build` and `cargo test -p
  shakenfist-spice-protocol` both pass. The new parser
  tests all pass.
* `cargo test -p ryll display::surface` (or whatever the
  test module resolves to) passes all ten surface tests.
* `tools/audit/wave1.sh` passes (rustfmt, clippy -D
  warnings, cargo test, no raw `println!`/`eprintln!`,
  no shellcheck failures).
* `pre-commit run --all-files` passes.
* Manually running the client against a guest shows the
  surface background is now black, not dark grey. No
  other visible behaviour change.
* No new opcodes route to a handler — the `_ =>
  log_unknown(...)` arm in
  [ryll/src/channels/display.rs:654](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L654)
  is unchanged, confirming phase 1 did not accidentally
  land phase 2's work.

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
