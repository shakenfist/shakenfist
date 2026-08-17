# Phase 6: DRAW_TRANSPARENT and DRAW_ALPHA_BLEND

Part of [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/).

## Prompt

Before responding to questions or discussion points in
this document, explore the ryll codebase thoroughly. Read
relevant source files, understand existing patterns
(SPICE protocol handling, channel architecture, image
decompression, egui rendering), and ground your answers
in what the code actually does today. Do not speculate
about the codebase when you could read it instead.

Key references for this phase:

* `/srv/src-reference/spice/spice-common/common/draw.h`
  lines 204-222 — `SpiceTransparent` and
  `SpiceAlphaBlend` canonical definitions. Phase 1
  already added Rust parsers for both.
* `/srv/src-reference/spice/spice-common/common/pixman_utils.c`
  lines 820-840 — canonical chroma-key implementation:
  `if ((0xffffff & val) != transparent_color) { *d = val; }`.
  Lower 24 bits compared; alpha byte ignored on the
  source side.
* `/srv/src-reference/spice/spice-common/common/canvas_base.c`
  `canvas_draw_alpha_blend` around line 2318 — reference
  alpha-blend dispatch. Notable: when `alpha == 0` the
  whole op is skipped (early return).
* `/srv/src-reference/spice/spice-html5/src/display.js`
  lines 422-430 — spice-html5 explicitly does NOT
  implement either op (`known_unimplemented`). So with
  this phase ryll overtakes spice-html5 on draw-op
  coverage, matching the master-plan intent.
* [PLAN-display-draw-ops-phase-01-plumbing.md](/components/ryll/plans/PLAN-display-draw-ops-phase-01-plumbing/)
  §"Wire-format summary" — `SpiceTransparent` (28 bytes)
  and `SpiceAlphaBlend` (23 bytes) parser contracts.
* [PLAN-display-draw-ops-phase-05-image-rop.md](/components/ryll/plans/PLAN-display-draw-ops-phase-05-image-rop/)
  — phase 5 established `decode_image_and_emit` as the
  shared image-decode-and-emit helper. Phase 6 extends
  it with a composite-mode parameter so the decoded
  pixels can reach the surface through either
  overwrite, chroma-key, or alpha-blend paths.

## Goal

Light up `DRAW_TRANSPARENT` (opcode 312) and
`DRAW_ALPHA_BLEND` (opcode 313):

* **DRAW_TRANSPARENT** — paint the source image into the
  destination rect, but leave destination pixels
  unchanged wherever the source pixel matches the
  supplied `src_color` (chroma-key). Commonly used by
  older X server paths and by some cursor themes; the
  typical use is "paint this image with the magenta
  pixels treated as transparent".
* **DRAW_ALPHA_BLEND** — paint the source image into
  the destination rect with standard source-over
  alpha-blending using a constant `alpha` multiplier
  (0-255) applied uniformly across the image. Per-pixel
  source alpha (JpegAlpha) multiplies through.

Both ops reuse the existing image-decode path. The
novelty is the compositing — neither is a simple
memcpy — which means:

1. Two new `DisplaySurface` helpers: `blit_chroma` and
   `blit_alpha`. Phase-1 `fill_rect`/`copy_bits`/
   `invert_rect` established the direct-to-pixels
   pattern; the new helpers extend it.
2. Two new `ChannelEvent` variants:
   `ImageReadyChroma { ..., chroma_rgba }` and
   `ImageReadyAlpha { ..., alpha }`, both carrying the
   RGBA pixels the decoder produced.
3. `decode_image_and_emit` grows a `CompositeMode`
   parameter. For `CompositeMode::Overwrite` behaviour
   is unchanged (DRAW_COPY/BLEND/OPAQUE keep emitting
   `ImageReady`). New modes select the new variants.
4. Two new channel handlers `handle_draw_transparent`
   and `handle_draw_alpha_blend`, parsing the phase-1
   `SpiceTransparent`/`SpiceAlphaBlend` structs.

By the end of this phase, all ten image/paint opcodes
from the master plan's *Situation* table are live except
`DRAW_INVERS`, `DRAW_ROP3`, `DRAW_STROKE`, and
`DRAW_TEXT` (phase 7 handles the first; the others
stay warn-once-deferred).

## Non-goals

* Scaling. SpiceAlphaBlend's `alpha_flags` includes
  `SPICE_ALPHA_FLAGS_SRC_SURFACE_HAS_ALPHA` and friends;
  the canonical impl uses them to pick pixman scaling
  paths. Our sources are always rectangles matching the
  destination rect (no scaling in any SPICE message
  we've seen land in practice), so we ignore the flags
  entirely for phase 6 and warn_once if we ever see
  them set.
* JPEG_ALPHA image type. It carries per-pixel alpha via
  a separate alpha plane, which the current
  `decode_image_and_emit` image-type match explicitly
  rejects with a "not yet implemented" warn. Leave it
  alone; most DRAW_ALPHA_BLEND sources are LZ_RGB or
  BITMAP with source alpha always 255.
* Non-OP_PUT rop descriptors. Neither opcode carries
  one on the wire.
* Pre-multiplied alpha. We use straight alpha
  (non-premultiplied), matching egui's
  `ColorImage::from_rgba_unmultiplied`.
* Optimisation. The compositing is a plain per-pixel
  loop. If it ever becomes a hotspot, a SIMD pass is
  future work.

## Current state

* [shakenfist-spice-protocol/src/messages.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/messages.rs)
  has `SpiceTransparent::read` and
  `SpiceAlphaBlend::read` from phase 1.
* [ryll/src/channels/display.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs)
  `decode_image_and_emit(payload, op_name, base,
  src_bitmap_offset, src_top, src_left, src_bottom,
  src_right)` from phase 5. Emits `ChannelEvent::ImageReady`
  at its two emit sites (clip-rects branch and fallback).
  All decode-and-cache logic is in this single method.
* [ryll/src/channels/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs)
  `ChannelEvent::ImageReady` — the existing overwrite
  emit variant.
* [ryll/src/display/surface.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs)
  `DisplaySurface::blit` — the straight memcpy used by
  `ImageReady`.
* [ryll/src/app.rs:562](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L562)
  — `ImageReady` handler that calls `surface.blit`.
* `warn_once!` macro from phase 2. Available for the
  alpha_flags warn site.
* `DRAW_TRANSPARENT` and `DRAW_ALPHA_BLEND` currently
  fall into `handle_message`'s `_ => log_unknown(...)`
  arm.

## Wire format (from phase 1)

### DRAW_TRANSPARENT payload

```
DrawBase                          (21 bytes + clip)
  + SpiceTransparent              (28 bytes)
      src_bitmap          u32
      src_top             u32
      src_left            u32
      src_bottom          u32
      src_right           u32
      src_color           u32  (BGRX — the chroma key)
      true_color          u32  (deprecated; ignore)
```

`src_color` is the 24-bit chroma-key colour. Wire bytes
are `[B, G, R, X]` (little-endian u32), matching brush
colours from phase 2. The `true_color` field is
deprecated — spice-gtk ignores it for 32-bpp surfaces,
and we do the same.

### DRAW_ALPHA_BLEND payload

```
DrawBase                          (21 bytes + clip)
  + SpiceAlphaBlend               (23 bytes)
      alpha_flags         u16
      alpha               u8
      src_bitmap          u32
      src_top             u32
      src_left            u32
      src_bottom          u32
      src_right           u32
```

`alpha` is the constant alpha multiplier, 0-255.
Per-pixel alpha in the source (if any; almost always
just 255 from non-JpegAlpha types) is multiplied by
`alpha/255` before blending.

`alpha_flags` is a bitfield with flags for dest-has-
alpha and src-surface-has-alpha. For our ever-opaque
RGBA surface and source images these flags are
irrelevant; we warn_once if a non-zero value shows up
and paint anyway (the correct fallback — we already
handle the per-pixel-alpha case).

## Implementation shape

### Surface helpers

Both new helpers live in
[ryll/src/display/surface.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs)
alongside the phase-1 `fill_rect`/`copy_bits`/
`invert_rect`. Their signatures mirror `blit`:

```rust
impl DisplaySurface {
    /// Paint `pixels` (RGBA) into `(left, top)` but
    /// skip any pixel whose RGB equals the lower 24
    /// bits of `chroma_rgba`. Caller is responsible for
    /// RGBA-packing the chroma value (alpha byte of
    /// the argument is ignored — we compare R/G/B
    /// only).
    pub fn blit_chroma(
        &mut self,
        left: u32,
        top: u32,
        width: u32,
        height: u32,
        pixels: &[u8],
        chroma_rgba: [u8; 4],
    );

    /// Paint `pixels` (RGBA, straight/non-premultiplied)
    /// into `(left, top)` via source-over alpha
    /// blending with a constant `alpha` multiplier
    /// (0-255).
    pub fn blit_alpha(
        &mut self,
        left: u32,
        top: u32,
        width: u32,
        height: u32,
        pixels: &[u8],
        alpha: u8,
    );
}
```

Per-pixel semantics:

* `blit_chroma`: for each source pixel `[r,g,b,a]`,
  if `[r,g,b]` matches the first three bytes of
  `chroma_rgba`, leave destination untouched; else
  copy `[r,g,b,a]` onto destination.
* `blit_alpha`: for each source pixel `[sr,sg,sb,sa]`
  and destination `[dr,dg,db,da]`:
  * `effective_a = sa * alpha / 255` (u8 math with
    rounding)
  * `out_r = (sr * effective_a + dr * (255 - effective_a)) / 255`
  * `out_g`, `out_b` likewise
  * `out_a = 255` (destination stays opaque; ryll
    surfaces never have translucent regions)
  
  Use `u16` intermediates to avoid overflow. Rounding
  via `(x + 127) / 255` or bit-trick `x = x + 128; x =
  (x + (x >> 8)) >> 8;` — pick whichever compiles
  cleaner. Consistency over cleverness; match the
  shape `fill_rect` / `invert_rect` use.

Dirty flag behaviour matches phase-1 helpers: set
`self.dirty = true` only if at least one pixel changed.
For `blit_chroma` that's "at least one non-chroma
pixel in-bounds"; for `blit_alpha` that's "alpha !=
0 and the clipped rect is non-empty". Simpler
approximation: set dirty if any pixel is written,
which includes "pixel == dest pre-blend" cases. The
approximation is fine — egui tolerates over-notifying.

Neither helper supports `clip` rect lists. Reason: the
event-side handlers can fall back to the existing
per-clip-rect sub-emit approach from
`decode_image_and_emit` (the same pattern DRAW_COPY
already uses), and that keeps surface.rs free of a
clip-list parameter on three of its helpers vs. two.
If a future phase needs it we can add it; for now,
keep surgical.

### Unit tests (surface.rs)

Additions to the existing `#[cfg(test)] mod tests`
block. Pattern matches phase-1 surface tests:

1. `blit_chroma_skips_matching_pixels` — 2×2 surface
   pre-filled with dark blue; blit a 2×2 source where
   one pixel matches the chroma key and three don't.
   Assert the matching pixel is still blue.
2. `blit_chroma_ignores_chroma_alpha_byte` — same but
   chroma arg alpha = 0 and source alpha = 255. Assert
   the chroma match still works on the lower 24 bits.
3. `blit_chroma_clipped_to_surface` — source extends
   past right edge; assert no panic and only in-bounds
   pixels considered.
4. `blit_alpha_full_opaque` — `alpha = 255`, source
   pixels with `sa = 255`: result equals source (plain
   blit).
5. `blit_alpha_half` — `alpha = 128`, source red on
   black dest: result is roughly half-red, half-black
   (test within ±1 tolerance because of rounding).
6. `blit_alpha_zero_is_noop` — `alpha = 0`: destination
   unchanged.
7. `blit_alpha_per_pixel_alpha_multiplies` — source
   pixel with `sa = 128`, `alpha = 128` (constant):
   `effective_a = 64`, so result is 64/255 of the way
   from dest to src.
8. `blit_alpha_clipped_to_surface` — pass a rect wider
   than the surface. Assert no panic, only in-bounds
   pixels touched.

No test for the alpha-arithmetic edge case of
`sa=255, alpha=254` (near-opaque) — covered by the
half test being within tolerance.

### ChannelEvent variants

In [ryll/src/channels/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs),
after `ImageReady`:

```rust
/// Image-bearing paint with chroma-keying (DRAW_TRANSPARENT).
ImageReadyChroma {
    display_channel_id: u8,
    surface_id: u32,
    left: u32,
    top: u32,
    width: u32,
    height: u32,
    pixels: Vec<u8>,  // RGBA
    chroma_rgba: [u8; 4],
    #[allow(dead_code)]
    image_id: u64,
},

/// Image-bearing paint with constant-alpha blending
/// (DRAW_ALPHA_BLEND).
ImageReadyAlpha {
    display_channel_id: u8,
    surface_id: u32,
    left: u32,
    top: u32,
    width: u32,
    height: u32,
    pixels: Vec<u8>,  // RGBA
    alpha: u8,
    #[allow(dead_code)]
    image_id: u64,
},
```

Separate variants (not a single `ImageCompose { mode }`)
to match the phase-1 precedent of distinct
`FillRect` / `CopyBits` / `Invert` variants. Makes
match exhaustiveness obvious and keeps the app-side
handler free of nested match blocks.

### App-side handlers

In [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) alongside
the existing `ImageReady` / `FillRect` / `CopyBits` /
`Invert` arms in `process_events`:

```rust
ChannelEvent::ImageReadyChroma {
    display_channel_id, surface_id,
    left, top, width, height, pixels, chroma_rgba, ..
} => {
    if let Some(surface) = self.surfaces.get_mut(&(display_channel_id, surface_id)) {
        surface.blit_chroma(left, top, width, height, &pixels, chroma_rgba);
        self.stats.frames_received += 1;
    } else {
        debug!("app: ImageReadyChroma on unknown surface {}", surface_id);
    }
}
ChannelEvent::ImageReadyAlpha {
    display_channel_id, surface_id,
    left, top, width, height, pixels, alpha, ..
} => {
    if let Some(surface) = self.surfaces.get_mut(&(display_channel_id, surface_id)) {
        surface.blit_alpha(left, top, width, height, &pixels, alpha);
        self.stats.frames_received += 1;
    } else {
        debug!("app: ImageReadyAlpha on unknown surface {}", surface_id);
    }
}
```

Same shape as `FillRect` / `CopyBits` / `Invert`. The
headless event loop's `_ => {}` default covers the new
variants; headless mode doesn't render.

### The `CompositeMode` plumbing

`decode_image_and_emit` grows a parameter:

```rust
#[derive(Debug, Clone, Copy)]
enum CompositeMode {
    /// Standard overwrite — emits ChannelEvent::ImageReady.
    Overwrite,
    /// Chroma-key — emits ChannelEvent::ImageReadyChroma.
    ChromaKey { chroma_rgba: [u8; 4] },
    /// Constant-alpha blend — emits ChannelEvent::ImageReadyAlpha.
    AlphaBlend { alpha: u8 },
}

#[allow(clippy::too_many_arguments)]
async fn decode_image_and_emit(
    &mut self,
    payload: &[u8],
    op_name: &str,
    base: &DrawBase,
    src_bitmap_offset: usize,
    src_top: u32, src_left: u32, src_bottom: u32, src_right: u32,
    composite: CompositeMode,
) -> Result<()> { ... }
```

At each of the two emit sites today (clip-rects branch
and fallback emit), construct the appropriate variant
based on `composite`. Helper:

```rust
fn build_image_event(
    composite: CompositeMode,
    display_channel_id: u8,
    surface_id: u32,
    left: u32, top: u32,
    width: u32, height: u32,
    pixels: Vec<u8>,
    image_id: u64,
) -> ChannelEvent {
    match composite {
        CompositeMode::Overwrite => ChannelEvent::ImageReady {
            display_channel_id, surface_id,
            left, top, width, height, pixels, image_id,
        },
        CompositeMode::ChromaKey { chroma_rgba } => ChannelEvent::ImageReadyChroma {
            display_channel_id, surface_id,
            left, top, width, height, pixels, chroma_rgba, image_id,
        },
        CompositeMode::AlphaBlend { alpha } => ChannelEvent::ImageReadyAlpha {
            display_channel_id, surface_id,
            left, top, width, height, pixels, alpha, image_id,
        },
    }
}
```

`handle_draw_copy`, `handle_draw_blend`, and
`handle_draw_opaque` pass `CompositeMode::Overwrite`;
the new phase-6 handlers pass the chroma/alpha
variants. No observable behaviour change for DRAW_COPY.

### Channel handlers

```rust
async fn handle_draw_transparent(&mut self, payload: &[u8]) -> Result<()> {
    if settings::is_verbose() {
        if let Ok(base) = DrawBase::read(payload) {
            logging::log_detail(&format!(
                "draw_transparent: surface={}, rect=({},{})-({},{}), clip_type={}",
                base.surface_id, base.left, base.top, base.right, base.bottom, base.clip_type,
            ));
        }
    }

    let base = DrawBase::read(payload)?;
    let transparent = SpiceTransparent::read(&payload[base.end_offset..])?;

    let chroma_rgba = [
        ((transparent.src_color >> 16) & 0xff) as u8,
        ((transparent.src_color >>  8) & 0xff) as u8,
        ( transparent.src_color        & 0xff) as u8,
        0xff,
    ];

    self.decode_image_and_emit(
        payload, "draw_transparent", &base,
        transparent.src_bitmap as usize,
        transparent.src_top, transparent.src_left,
        transparent.src_bottom, transparent.src_right,
        CompositeMode::ChromaKey { chroma_rgba },
    ).await
}

async fn handle_draw_alpha_blend(&mut self, payload: &[u8]) -> Result<()> {
    if settings::is_verbose() { ... log ... }

    let base = DrawBase::read(payload)?;
    let ab = SpiceAlphaBlend::read(&payload[base.end_offset..])?;

    if ab.alpha == 0 {
        // Canvas-base reference returns early for alpha == 0.
        // No pixels would change; skip the decode entirely.
        return Ok(());
    }

    if ab.alpha_flags != 0 {
        warn_once!(
            "display:draw_alpha_blend:alpha_flags",
            "display: draw_alpha_blend: non-zero alpha_flags {:#x} ignored, painting with straight alpha",
            ab.alpha_flags
        );
    }

    self.decode_image_and_emit(
        payload, "draw_alpha_blend", &base,
        ab.src_bitmap as usize,
        ab.src_top, ab.src_left, ab.src_bottom, ab.src_right,
        CompositeMode::AlphaBlend { alpha: ab.alpha },
    ).await
}
```

Route in `handle_message`:

```rust
display_server::DRAW_TRANSPARENT => {
    self.handle_draw_transparent(payload).await?;
}
display_server::DRAW_ALPHA_BLEND => {
    self.handle_draw_alpha_blend(payload).await?;
}
```

### Decoder unit tests

Unlike phases 2-5 these handlers don't need a
`decode_*` free function — the channel handler is
already a thin wrapper over the phase-1 parser and the
phase-5 helper. The phase-1 parser tests cover
`SpiceTransparent::read` and `SpiceAlphaBlend::read`.
Chroma and alpha maths are covered by the surface-
helper tests.

If a test is *really* wanted to prove the channel-
handler wire-up (e.g. that non-zero `alpha_flags` fires
the warn_once), add one that exercises warn_once via
the public `warn_once_keys()` query after calling the
handler — but it'd need a mock `DisplayChannel`, which
is invasive. Recommend skipping.

## Files touched

| File | Change |
|------|--------|
| `ryll/src/display/surface.rs` | Add `blit_chroma` and `blit_alpha` plus eight unit tests. |
| `ryll/src/channels/mod.rs` | Add `ImageReadyChroma` and `ImageReadyAlpha` ChannelEvent variants. |
| `ryll/src/app.rs` | Add two new match arms in `process_events`. |
| `ryll/src/channels/display.rs` | Add `CompositeMode` enum, thread through `decode_image_and_emit`, add `handle_draw_transparent` and `handle_draw_alpha_blend`, route both opcodes. |

No protocol-crate changes — phase 1 already did.

## Resolved questions

All five resolved per the user's explicit approval
before execution; recorded here so a future reader
sees the rationale.

1. **Straight alpha, not premultiplied.** Matches
   egui's `from_rgba_unmultiplied` surface boundary;
   no conversion needed at render time.

2. **`true_color` on SpiceTransparent is ignored
   silently** (no warn_once). Deprecated field per
   spice-gtk; would be log spam on every op.

3. **`alpha_flags != 0` on SpiceAlphaBlend
   warn_once's + paints anyway**, not skips. Our
   straight-alpha path is correct for
   SRC_SURFACE_HAS_ALPHA (we respect per-pixel alpha)
   and benign for DEST_HAS_ALPHA (our surface is
   always opaque, destination alpha ends as 255
   regardless). A skip would drop legitimate pixels.

4. **Alpha rounding via `(s * a + 127) / 255`.** Integer
   round-to-nearest, readable, within ±1 of the float
   ideal. Unit tests assert ±1 tolerance so the exact
   rounding form is a future-work tuning knob, not a
   correctness hazard.

5. **No `clip` arg on the new surface helpers.**
   `decode_image_and_emit` already does per-clip-rect
   sub-emits; each emit carries a clean rect, so the
   helpers receive pre-clipped pixels. Adding a clip
   arg would duplicate work. `fill_rect` /
   `invert_rect` take `clip` because they're called
   from channel handlers that don't do the sub-emit
   dance — a different code path.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high   | opus   | none | Add `blit_chroma` and `blit_alpha` methods on `DisplaySurface` in `ryll/src/display/surface.rs` per the "Surface helpers" section. Add the eight unit tests listed under "Unit tests (surface.rs)". Use `u16` intermediates for the alpha arithmetic to avoid overflow. Tolerate empty / fully-clipped rects without panicking and without setting the dirty flag. Tag both helpers `#[allow(dead_code)]` — step 6b will lift it when the new ChannelEvent variants are wired. Do NOT touch anything outside `surface.rs`. |
| 6b | medium | sonnet | none | Add `ChannelEvent::ImageReadyChroma` and `ChannelEvent::ImageReadyAlpha` variants in `ryll/src/channels/mod.rs`, both `#[allow(dead_code)] // constructed in phase 6`. Add corresponding match arms in the main `process_events` in `ryll/src/app.rs` (NOT the headless loop) that call `surface.blit_chroma` and `surface.blit_alpha`. Headless loop's `_ => {}` default covers the new variants; do not touch it. Drop `#[allow(dead_code)]` from `DisplaySurface::blit_chroma` and `DisplaySurface::blit_alpha` in the same commit since they now have a live reader path. |
| 6c | high   | opus   | none | Extend `decode_image_and_emit` in `ryll/src/channels/display.rs` with a `composite: CompositeMode` parameter. Add the `CompositeMode` enum (Overwrite / ChromaKey / AlphaBlend) near the other outcome enums. Introduce a private `build_image_event(composite, ..., pixels, image_id) -> ChannelEvent` helper that dispatches to the right variant. Use it at both event-emit sites (clip-rects branch + fallback). Update `handle_draw_copy`, `handle_draw_blend`, and `handle_draw_opaque` to pass `CompositeMode::Overwrite`. Drop the phase-6 `#[allow(dead_code)]` from the new ChannelEvent variants if 6b set them with the comment. Existing DRAW_COPY tests must still pass with no visible behaviour change. |
| 6d | medium | sonnet | none | Add `handle_draw_transparent` and `handle_draw_alpha_blend` in `ryll/src/channels/display.rs` per the "Channel handlers" section. Route both opcodes in `handle_message` next to their numerical neighbours. Use `warn_once!` for non-zero `alpha_flags`. For `alpha == 0`, short-circuit and return before decode (matches canvas_base.c reference). Import `SpiceTransparent` and `SpiceAlphaBlend` from the protocol crate's messages module. No new unit tests needed — surface helpers and phase-1 parsers cover the maths and the parse. |
| 6e | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`. If rustfmt requests changes, run `./scripts/check-rust.sh fix`. Report results; do not fix other failures. |

Sequencing: 6a → (6b and 6c in parallel) → 6d → 6e.
6b and 6c both touch different files; 6b touches
`channels/mod.rs` + `app.rs`, 6c touches
`channels/display.rs` only. If the management session
wants to avoid parallel Docker contention, run them
sequentially instead (6a → 6b → 6c → 6d → 6e).

## Success criteria

* `pre-commit run --all-files` and
  `tools/audit/wave1.sh` both pass.
* All eight new surface-helper unit tests pass;
  existing tests unchanged. DRAW_COPY rendering must
  remain identical after 6c (verified via existing
  tests plus management-session diff review of the
  two emit sites in `decode_image_and_emit`).
* First-time non-zero `alpha_flags` on DRAW_ALPHA_BLEND
  emits exactly one warn line per session.
* Zero visible-render regressions in the
  user's existing workloads (X / desktop / BIOS).
  DRAW_TRANSPARENT and DRAW_ALPHA_BLEND are rare
  enough in Linux QXL guests that this phase may be
  invisible in practice; we nevertheless land it to
  drop the `log_unknown` warnings when they do fire
  and to prepare for phase 8's pedantic-mode gap
  accounting.

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
