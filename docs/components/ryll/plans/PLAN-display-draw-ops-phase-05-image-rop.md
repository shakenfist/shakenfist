# Phase 5: DRAW_OPAQUE and DRAW_BLEND

Part of [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/).

## Prompt

Before responding to questions or discussion points in
this document, explore the ryll codebase thoroughly. Read
relevant source files, understand existing patterns
(SPICE protocol handling, channel architecture, async
task model, image decompression, egui rendering), and
ground your answers in what the code actually does today.
Do not speculate about the codebase when you could read
it instead.

Key references for this phase:

* `/srv/src-reference/spice/spice-common/common/draw.h`
  lines 187-202 — `SpiceOpaque`, `SpiceCopy`,
  `SpiceBlend` canonical definitions. `SpiceBlend` is a
  typedef for `SpiceCopy` (bit-for-bit identical wire
  layout). `SpiceOpaque` is `SpiceCopy` with a
  `SpiceBrush brush` inserted between `src_area` and
  `rop_descriptor`.
* `/srv/src-reference/spice/spice-gtk/src/canvas.c`
  functions `canvas_draw_opaque` and
  `canvas_draw_blend` — production client. Note that
  `canvas_draw_opaque` uses the brush only when the ROP
  actually references the pattern source. For
  `SPICE_ROPD_OP_PUT` the brush is never consulted.
* `/srv/src-reference/spice/spice-html5/src/display.js`
  — reference client skips both ops with a log line.
  Confirms that "warn-once + skip on non-OP_PUT" is
  industry-standard tolerance.
* [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/)
  §"Phase 5: `DRAW_OPAQUE` and `DRAW_BLEND` — sketch".
* [PLAN-display-draw-ops-phase-01-plumbing.md](/components/ryll/plans/PLAN-display-draw-ops-phase-01-plumbing/)
  — phase 1 added the `SpiceOpaque` parser we reuse
  here, plus `SpiceBrush` and `SpiceQMask`.
* [PLAN-display-draw-ops-phase-02-fill.md](/components/ryll/plans/PLAN-display-draw-ops-phase-02-fill/)
  — established the `decode_*` helper + warn-once
  pattern. Adopted here.

## Goal

Light up `DRAW_OPAQUE` (opcode 303) and `DRAW_BLEND`
(opcode 305). Both carry an image and both reduce to
"paint this image, DRAW_COPY-style" when the ROP
descriptor is `SPICE_ROPD_OP_PUT` — which is what modern
QXL emits ≥99% of the time. Non-OP_PUT variants and
non-trivial brushes are rare enough in Linux guests to
be warn-once-and-skip per the master-plan stance.

Structurally this is the phase where the massive
`handle_draw_copy` (~490 lines) gets its image-decode
tail extracted into a shared helper so all three image-
bearing opcodes can reuse it without each growing a
full copy of the image-type match.

By the end of this phase:

* `handle_draw_copy`'s image-decode-and-emit tail is
  extracted into a shared `async fn
  decode_image_and_emit(&mut self, ...)` method on
  `DisplayChannel`. `handle_draw_copy` keeps its
  SpiceCopy header parsing inline; the tail delegates.
* `handle_draw_blend` parses the identical
  `SpiceBlend`/`SpiceCopy` header, warn_once's on
  non-OP_PUT, and calls the shared helper.
* `handle_draw_opaque` uses the phase-1
  `SpiceOpaque` parser, warn_once's on non-OP_PUT,
  ignores the brush (harmless on OP_PUT), and calls
  the shared helper.
* Both opcodes are routed from `handle_message`.
* `DRAW_COPY` behaviour is unchanged — same pixel
  bytes emitted, same cache interactions, same
  logging. The refactor is verified by the existing
  tests plus a new golden-trace test where possible.

## Non-goals

* Implementing non-OP_PUT ROP descriptors for
  `DRAW_OPAQUE` / `DRAW_BLEND`. They warn_once and skip.
  Proper ROP support remains in the master plan's
  "Future work".
* Implementing pattern brushes for `DRAW_OPAQUE`.
  Brushes are silently ignored on OP_PUT (correct, per
  draw.h semantics), warn_once-and-skipped otherwise —
  which is subsumed by the non-OP_PUT skip because the
  only reason the brush would matter is a ROP that uses
  it.
* Auditing `DRAW_COPY`'s rop handling. Today
  `handle_draw_copy` paints regardless of the
  rop_descriptor value. The master plan notes this is
  technically wrong but tolerated; fixing it would
  change DRAW_COPY's observable behaviour, and a
  refactor phase is not the place. Tracked as a
  follow-up in the master plan's "Future work" list if
  we decide we want the consistency.
* `SpiceCopy` as a protocol-crate struct. Currently
  `handle_draw_copy` hand-parses the 36-byte header
  with `read_u32_le` / `read_u16_le`. Adding a
  `SpiceCopy` struct to `messages.rs` and migrating
  that code is a good cleanup but expands the blast
  radius of this phase; do it as a dedicated tidy-up
  later. The new code in this phase parses inline like
  its neighbour.
* Image-decode error-handling changes. The existing
  decode paths' warnings on truncation or unsupported
  types are kept verbatim.

## Current state

* [ryll/src/channels/display.rs:789-1283](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L789)
  `handle_draw_copy`. ~490 lines. Inline SpiceCopy
  header parse, then a big match on `ImageType`
  producing an `Option<DecompressedImage>`, then cache
  + record_decode + emit-ImageReady (with optional
  src-area crop and per-clip-rect sub-emit).
* [shakenfist-spice-protocol/src/messages.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/messages.rs)
  `SpiceOpaque::read(data) -> io::Result<(Self,
  usize)>` from phase 1. Already knows about the
  embedded `SpiceBrush`.
* `SpiceFill` / `SpiceBlackness` / `SpiceWhiteness` —
  phase-1 parsers, wired by phases 2 and 3.
* `ropd::OP_PUT` constant from phase 2. `brush::*`
  likewise.
* `warn_once!` macro from phase 2. Already used by
  phases 2 and 3.

## Wire format

All three opcodes share a DrawBase preamble. After the
base:

### SpiceCopy / SpiceBlend (36 bytes fixed)

| Off | Size | Field |
|-----|------|-------|
| 0   | 4    | src_bitmap (u32 byte offset) |
| 4   | 16   | src_area (4× u32) |
| 20  | 2    | rop_descriptor (u16) |
| 22  | 1    | scale_mode (u8) |
| 23  | 13   | mask (SpiceQMask) |

(The existing `handle_draw_copy` already parses this
layout inline.)

### SpiceOpaque (variable: 36 + brush body)

| Off | Size | Field |
|-----|------|-------|
| 0   | 4    | src_bitmap (u32) |
| 4   | 16   | src_area |
| 20  | var  | brush (SpiceBrush: 1-byte tag + 0/4/16 byte body) |
| var | 2    | rop_descriptor (u16) |
| var | 1    | scale_mode (u8) |
| var | 13   | mask (SpiceQMask) |

The phase-1 `SpiceOpaque::read` returns `(SpiceOpaque,
bytes_consumed)` so the caller does not have to
track the variable offset manually. Good.

## Implementation shape

### The refactor: `decode_image_and_emit`

The image-decode-and-emit tail of `handle_draw_copy`
becomes a shared helper:

```rust
/// Decode the image at `src_bitmap_offset` in `payload`
/// (it's a byte offset into the same payload, same
/// convention DRAW_COPY already uses), crop to
/// `src_area`, apply clip rects, cache as appropriate,
/// emit ChannelEvent::ImageReady.
///
/// Extracted from `handle_draw_copy` in phase 5 so
/// DRAW_OPAQUE and DRAW_BLEND reuse the same ~350 lines
/// of image-type dispatch and event emission.
///
/// `op_name` is used only for log lines ("draw_copy",
/// "draw_opaque", "draw_blend") so timestamps and
/// record_decode entries point at the right caller.
#[allow(clippy::too_many_arguments)]
async fn decode_image_and_emit(
    &mut self,
    payload: &[u8],
    op_name: &str,
    base: &DrawBase,
    src_bitmap_offset: usize,
    src_top: u32, src_left: u32, src_bottom: u32, src_right: u32,
) -> Result<()> {
    // Body: exactly what today's handle_draw_copy does
    // from "let image_start = src_bitmap_offset;" through
    // the final self.repaint_notify.notify_one().
    //
    // Literal lift-and-shift from the current body —
    // same log lines (with `op_name` substituted for
    // the hard-coded "draw_copy"), same cache policy,
    // same src-area crop, same clip-rect sub-emit.
}
```

`handle_draw_copy` becomes:

```rust
async fn handle_draw_copy(&mut self, payload: &[u8]) -> Result<()> {
    if payload.len() < 21 {
        warn!("display: draw_copy payload too short");
        return Ok(());
    }
    let base = DrawBase::read(payload)?;
    // ... existing SpiceCopy header parse ...
    self.decode_image_and_emit(
        payload, "draw_copy", &base,
        src_bitmap_offset,
        src_top, src_left, src_bottom, src_right,
    ).await
}
```

### Refactor verification

`handle_draw_copy`'s byte-for-byte output must not
change. Strategies for catching a regression:

1. **Live unit tests.** The existing MJPEG, DHT, and
   `decode_draw_*` tests run clean today and they
   exercise parts of this file; run them after the
   refactor to confirm nothing is broken at the
   compile/test level.
2. **Existing manual-check flow.** The user can
   reproduce the same desktop workload before / after
   and confirm rendering is identical. For the
   refactor sub-commit specifically, keep the
   management-session review sharp: read the full diff
   of the lift-and-shift, confirm no bytes have
   sneaked into or out of the body during motion.
3. **No new golden/integration test in this phase.**
   Would be valuable but adds scope; add to the "Future
   work" list if it keeps biting us.

The lift-and-shift is the risky step. Do it as its own
sub-commit (5a) *before* adding DRAW_BLEND or
DRAW_OPAQUE, so a regression in DRAW_COPY bisects
cleanly.

### Decode + handler: DRAW_BLEND

Identical wire format to DRAW_COPY:

```rust
#[derive(Debug, Clone)]
enum BlendOutcome {
    Paint {
        base: DrawBase,
        src_bitmap_offset: usize,
        src_top: u32, src_left: u32, src_bottom: u32, src_right: u32,
    },
    SkipNonOpPut { rop: u16 },
}

fn decode_draw_blend(payload: &[u8]) -> std::io::Result<BlendOutcome> {
    let base = DrawBase::read(payload)?;
    let copy_start = base.end_offset;
    if payload.len() < copy_start + 36 {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "Not enough data for SpiceBlend",
        ));
    }
    let src_bitmap_offset = read_u32_le(payload, copy_start) as usize;
    let src_top = read_u32_le(payload, copy_start + 4);
    let src_left = read_u32_le(payload, copy_start + 8);
    let src_bottom = read_u32_le(payload, copy_start + 12);
    let src_right = read_u32_le(payload, copy_start + 16);
    let rop = read_u16_le(payload, copy_start + 20);
    // scale_mode and mask are ignored by phase 5 (same
    // as DRAW_COPY today).

    if rop != ropd::OP_PUT {
        return Ok(BlendOutcome::SkipNonOpPut { rop });
    }
    Ok(BlendOutcome::Paint {
        base,
        src_bitmap_offset,
        src_top, src_left, src_bottom, src_right,
    })
}

async fn handle_draw_blend(&mut self, payload: &[u8]) -> Result<()> {
    match decode_draw_blend(payload)? {
        BlendOutcome::SkipNonOpPut { rop } => {
            warn_once!(
                "display:draw_blend:non_op_put",
                "display: draw_blend: unhandled ROP descriptor {:#x}, skipping",
                rop
            );
            Ok(())
        }
        BlendOutcome::Paint {
            base, src_bitmap_offset,
            src_top, src_left, src_bottom, src_right,
        } => {
            self.decode_image_and_emit(
                payload, "draw_blend", &base,
                src_bitmap_offset,
                src_top, src_left, src_bottom, src_right,
            ).await
        }
    }
}
```

Note: DRAW_COPY today paints regardless of rop. DRAW_BLEND
in this phase skips on non-OP_PUT. That asymmetry is
deliberate — DRAW_COPY's existing behaviour is
out of scope, but DRAW_BLEND's raison d'être is the
ROP, so skipping when we can't honour it is more
honest than silently painting.

### Decode + handler: DRAW_OPAQUE

Uses the phase-1 `SpiceOpaque` parser:

```rust
#[derive(Debug, Clone)]
enum OpaqueOutcome {
    Paint {
        base: DrawBase,
        src_bitmap_offset: usize,
        src_top: u32, src_left: u32, src_bottom: u32, src_right: u32,
    },
    SkipNonOpPut { rop: u16 },
}

fn decode_draw_opaque(payload: &[u8]) -> std::io::Result<OpaqueOutcome> {
    let base = DrawBase::read(payload)?;
    let (opaque, _consumed) = SpiceOpaque::read(&payload[base.end_offset..])?;
    if opaque.rop_descriptor != ropd::OP_PUT {
        return Ok(OpaqueOutcome::SkipNonOpPut {
            rop: opaque.rop_descriptor,
        });
    }
    // Brush is irrelevant on OP_PUT (per draw.h
    // semantics) — silently ignore. If a later phase
    // adds a non-OP_PUT handler it'll need to look at
    // opaque.brush and opaque.rop_descriptor together.
    // Mask is likewise ignored on OP_PUT and no warn is
    // needed here: the payload that walks through
    // handle_draw_copy today also ignores it.
    //
    // src_bitmap_offset is relative to the start of
    // `payload` (the byte immediately after the
    // mini-header), same convention DRAW_COPY uses.
    Ok(OpaqueOutcome::Paint {
        base,
        src_bitmap_offset: opaque.src_bitmap as usize,
        src_top: opaque.src_top,
        src_left: opaque.src_left,
        src_bottom: opaque.src_bottom,
        src_right: opaque.src_right,
    })
}

async fn handle_draw_opaque(&mut self, payload: &[u8]) -> Result<()> {
    match decode_draw_opaque(payload)? {
        OpaqueOutcome::SkipNonOpPut { rop } => {
            warn_once!(
                "display:draw_opaque:non_op_put",
                "display: draw_opaque: unhandled ROP descriptor {:#x}, skipping",
                rop
            );
            Ok(())
        }
        OpaqueOutcome::Paint {
            base, src_bitmap_offset,
            src_top, src_left, src_bottom, src_right,
        } => {
            self.decode_image_and_emit(
                payload, "draw_opaque", &base,
                src_bitmap_offset,
                src_top, src_left, src_bottom, src_right,
            ).await
        }
    }
}
```

### Routing

```rust
display_server::DRAW_OPAQUE => {
    self.handle_draw_opaque(payload).await?;
}
display_server::DRAW_BLEND => {
    self.handle_draw_blend(payload).await?;
}
```

Insert next to `DRAW_COPY` / `DRAW_FILL` /
`DRAW_BLACKNESS` in `handle_message`.

### Unit tests

Four tests, mirroring phase 2/3 style, in the existing
`#[cfg(test)] mod tests` block:

1. `decode_draw_blend_happy_path_op_put` — build a
   DRAW_BLEND payload with rop=OP_PUT and a
   SpiceImage-offset pointing past the full 36-byte
   header. Assert `BlendOutcome::Paint` with the right
   base/src fields.
2. `decode_draw_blend_non_op_put_skips` — same but
   rop=OP_OR (0x10). Assert
   `BlendOutcome::SkipNonOpPut { rop: 0x10 }`.
3. `decode_draw_opaque_happy_path_op_put` — build a
   DRAW_OPAQUE payload with SOLID brush (SOLID body is
   4 bytes), rop=OP_PUT. Assert
   `OpaqueOutcome::Paint` with the right offsets.
4. `decode_draw_opaque_non_op_put_skips` — same but
   rop=OP_OR. Assert
   `OpaqueOutcome::SkipNonOpPut { rop: 0x10 }`.

We do NOT add a unit test for `decode_image_and_emit`
because it needs a full `DisplayChannel`. Refactor
safety is covered by running the existing tests plus
manual comparison of pre-/post-refactor DRAW_COPY
rendering.

## Files touched

| File | Change |
|------|--------|
| `ryll/src/channels/display.rs` | Extract `decode_image_and_emit`; refactor `handle_draw_copy` to call it; add `decode_draw_blend`, `decode_draw_opaque`, `handle_draw_blend`, `handle_draw_opaque`; route `DRAW_OPAQUE` and `DRAW_BLEND`; four new unit tests. |

Nothing else. No protocol-crate changes, no new
constants. The phase-1 `SpiceOpaque` parser handles
everything on that side.

## Open questions

1. **Should we add a `SpiceCopy` / `SpiceBlend` parser
   struct to the protocol crate?** Yes would be
   cleaner — `handle_draw_copy`'s ~30 lines of
   `read_u32_le` calls are a lingering pre-phase-1
   idiom. No would keep phase 5 smaller and leave the
   cleanup for a dedicated follow-up. I recommend **no**
   for this phase, documented as future work in the
   master plan, because touching `handle_draw_copy`'s
   header parse *and* refactoring its tail in the same
   phase makes bisection harder if a regression creeps
   in.

2. **Skip or paint on DRAW_BLEND non-OP_PUT?** Plan skips
   and warn_once's. Painting regardless (matching
   DRAW_COPY's current quirk) would be cheap but paints
   incorrect pixels. Skipping is more honest. I
   recommend skip.

3. **Unit-test `decode_image_and_emit`?** Would need
   either a mock `DisplayChannel` or extracting the
   entire body as a free function taking all the state
   as parameters. The latter is possible (everything it
   touches is on `self`: `self.glz_dictionary`,
   `self.image_cache`, `self.event_tx`, `self.channel_id`,
   `self.repaint_notify`, `self.traffic`,
   `self.record_decode`) but invasive. I recommend
   skipping unit tests for the helper in this phase;
   cover its correctness via the refactor's lift-and-
   shift discipline plus existing integration-style
   tests.

4. **Op_name threading.** I've added `op_name: &str` to
   `decode_image_and_emit` so existing log lines like
   `"display: draw_copy: null src_bitmap"` become
   `"display: {op_name}: null src_bitmap"` etc. This
   shows up in a lot of places. Alternative: use a
   single hardcoded "draw" prefix and let the caller
   log its own opcode. Recommend threading `op_name`
   — it preserves existing DRAW_COPY log strings
   exactly for grep-compat, and gives DRAW_BLEND /
   DRAW_OPAQUE their own identifiable lines.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high   | opus   | none | In `ryll/src/channels/display.rs`, extract the image-decode-and-emit portion of `handle_draw_copy` (from `let image_start = src_bitmap_offset;` through `self.repaint_notify.notify_one();` at the end of each emit branch — both the clip-rects branch and the fallback) into a new `async fn decode_image_and_emit(&mut self, payload: &[u8], op_name: &str, base: &DrawBase, src_bitmap_offset: usize, src_top: u32, src_left: u32, src_bottom: u32, src_right: u32) -> Result<()>`. Replace the extracted region with a call to it. Preserve every log message string (substitute the hard-coded "draw_copy" with `op_name` where present — there are ~5 sites). Do NOT change cache policy, record_decode, or event emission. Do NOT add DRAW_OPAQUE or DRAW_BLEND yet. Compile + tests must pass. |
| 5b | medium | sonnet | none | Add `decode_draw_blend`, `BlendOutcome`, `handle_draw_blend` per "Decode + handler: DRAW_BLEND" in this plan. Route `display_server::DRAW_BLEND` in `handle_message` between DRAW_COPY and DRAW_BLACKNESS. Add two unit tests (`decode_draw_blend_happy_path_op_put`, `decode_draw_blend_non_op_put_skips`). Use `warn_once!` key `"display:draw_blend:non_op_put"`. |
| 5c | medium | sonnet | none | Add `decode_draw_opaque`, `OpaqueOutcome`, `handle_draw_opaque` per "Decode + handler: DRAW_OPAQUE" — uses the phase-1 `SpiceOpaque::read` parser from `shakenfist_spice_protocol::messages`. Route `display_server::DRAW_OPAQUE` in `handle_message` next to DRAW_OPAQUE's numerical neighbours. Add two unit tests. Use `warn_once!` key `"display:draw_opaque:non_op_put"`. Import `SpiceOpaque` into display.rs's messages `use` list. |
| 5d | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`. If rustfmt requests changes, run `./scripts/check-rust.sh fix`. Report results; do not fix other failures. |

Sequencing:
* 5a alone — the refactor is the risky step and must
  land cleanly on its own commit.
* 5b and 5c are independent (different Outcome enums,
  different handlers), but both depend on 5a
  landing first.
* 5d last.

## Success criteria

* `pre-commit run --all-files` and
  `tools/audit/wave1.sh` both pass.
* All new unit tests pass; all existing tests
  unchanged.
* After 5a: user reproduces a workload they have
  cached impressions of (BIOS → desktop login) and
  confirms rendering looks identical — no colour
  shifts, no clipping differences, no cache weirdness.
* After 5b and 5c: user workload is unchanged (Linux
  guests almost never emit these ops; the win is
  dropping the `log_unknown` warnings from the -v log
  when they *do* show up).
* First-time non-OP_PUT DRAW_BLEND or DRAW_OPAQUE
  fires exactly one warn line each, then stays silent.

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
