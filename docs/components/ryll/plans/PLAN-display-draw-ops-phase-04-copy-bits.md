# Phase 4: COPY_BITS

Part of [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/).

## Prompt

Before responding to questions or discussion points in
this document, explore the ryll codebase thoroughly. Read
relevant source files, understand existing patterns, and
ground your answers in what the code actually does today.
Do not speculate about the codebase when you could read
it instead.

Key references for this phase:

* `/srv/src-reference/spice/spice-protocol/spice/qxl_dev.h`
  lines 112-115, 383-385 — `QXLPoint` (`int32_t`) and
  `QXLCopyBits { QXLPoint src_pos; }`. The wire payload
  is `DrawBase` + 8 bytes of `SpicePoint`.
* `/srv/src-reference/spice/spice-html5/src/display.js`
  — reference implementation of COPY_BITS. Uses
  `getImageData`/`putImageData` to snapshot the source
  rect, which is the same strategy the phase-1
  `DisplaySurface::copy_bits` helper uses.
* `/srv/src-reference/spice/spice-gtk/src/canvas.c` —
  `canvas_copy_bits` for confirming we haven't missed
  any sub-feature.
* [PLAN-display-draw-ops-phase-02-fill.md](/components/ryll/plans/PLAN-display-draw-ops-phase-02-fill/)
  — established the `decode_*` helper + warn-once
  pattern we reuse here.
* [PLAN-display-draw-ops-phase-01-plumbing.md](/components/ryll/plans/PLAN-display-draw-ops-phase-01-plumbing/)
  — phase-1 "Surface helpers" section, which specified
  the aliasing-safe snapshot strategy that
  `DisplaySurface::copy_bits` implements.

## Goal

Route `display_server::COPY_BITS` (opcode 104) through
to `DisplaySurface::copy_bits`. COPY_BITS is a pure
intra-surface pixel copy — kernel consoles use it to
scroll text up one line at a time — so with phase 2 and
phase 3 already clearing backgrounds, this phase closes
the last of the user-visible BIOS/GRUB/kernel-boot draw-
op gaps identified in the master plan's *Situation*
table.

By the end of this phase:

* The channel routes `COPY_BITS` to a new
  `handle_copy_bits()` method.
* Payload is parsed as `DrawBase` + `SpicePoint`.
* Event emission uses the `ChannelEvent::CopyBits`
  variant added in phase 1; the app-side handler
  (phase 1f) is the path to `DisplaySurface::copy_bits`.
* `#[allow(dead_code)]` comes off
  `ChannelEvent::CopyBits` and off
  `DisplaySurface::copy_bits` (plus the private
  `copy_subrect` helper if clippy stops flagging it).
* Parse-and-classify is a pure `decode_copy_bits` helper
  for unit tests.

## Non-goals

* New protocol structs, events, or surface helpers —
  phase 1 ships them all.
* New warn-once cases. COPY_BITS has no mask, no brush,
  no ROP descriptor; every valid payload is paintable.
* Clip-rect optimisations. The phase-1 surface helper
  already applies SPICE_CLIP_TYPE_RECTS clip rects with
  union semantics, and we forward the DrawBase clip list
  unchanged.

## Current state

* `display_server::COPY_BITS = 104` is defined in
  constants.rs but falls into the `_ => log_unknown(...)`
  arm at [ryll/src/channels/display.rs:714](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L714)
  (or wherever the unknown arm sits now — the line
  number will have shifted as phase 2 and 3 added
  earlier arms).
* `ChannelEvent::CopyBits { display_channel_id,
  surface_id, src_x, src_y, dest_rect, clip }` exists
  with `#[allow(dead_code)] // constructed in phase 4`.
* `DisplaySurface::copy_bits` exists, aliasing-safe via
  snapshot, with `#[allow(dead_code)]`. Its nine unit
  tests from phase 1 already cover the hard cases
  (overlap down, overlap right, source off surface).
* `SpicePoint` parser from phase 1 returns `{ x: i32,
  y: i32 }`. Per the phase-1 open-question resolution,
  the `COPY_BITS.src_pos` is `int32_t` on the wire but
  values are non-negative in practice; this phase clips
  negatives to 0 at the call site.

## Wire format

```
DrawBase                        (21 bytes + 4+16*N for clip rects)
  + SpicePoint src_pos          (8 bytes: i32 x, i32 y)
```

No mask, no brush, no ROP, no image descriptor.
Simpler than both DRAW_FILL and DRAW_BLACKNESS.

## Implementation shape

### Decode helper

```rust
#[derive(Debug, Clone)]
enum CopyBitsOutcome {
    Copy {
        base: DrawBase,
        src_x: u32,
        src_y: u32,
    },
}

fn decode_copy_bits(payload: &[u8]) -> std::io::Result<CopyBitsOutcome> {
    let base = DrawBase::read(payload)?;
    let src_pos = SpicePoint::read(&payload[base.end_offset..])?;
    // SPICE wire type is int32 but source coords are
    // logically unsigned (they index into the surface).
    // Clip negatives to 0 at the seam so the surface
    // helper, which takes u32, never sees weirdness.
    let src_x = src_pos.x.max(0) as u32;
    let src_y = src_pos.y.max(0) as u32;
    Ok(CopyBitsOutcome::Copy {
        base,
        src_x,
        src_y,
    })
}
```

The single-variant enum is deliberate for symmetry with
`SolidFillOutcome` (phase 3) and `FillOutcome` (phase 2);
room for future skip-cases without churning the call
site.

### Async handler

```rust
async fn handle_copy_bits(&mut self, payload: &[u8]) -> Result<()> {
    if settings::is_verbose() {
        if let Ok(base) = DrawBase::read(payload) {
            logging::log_detail(&format!(
                "copy_bits: surface={}, rect=({},{})-({},{}), clip_type={}",
                base.surface_id, base.left, base.top,
                base.right, base.bottom, base.clip_type,
            ));
        }
    }

    let CopyBitsOutcome::Copy { base, src_x, src_y } =
        decode_copy_bits(payload)?;

    self.event_tx
        .send(ChannelEvent::CopyBits {
            display_channel_id: self.channel_id,
            surface_id: base.surface_id,
            src_x,
            src_y,
            dest_rect: (base.left, base.top, base.right, base.bottom),
            clip: base.clip_rects,
        })
        .await
        .ok();
    self.repaint_notify.notify_one();

    Ok(())
}
```

Route in `handle_message`:

```rust
display_server::COPY_BITS => {
    self.handle_copy_bits(payload).await?;
}
```

Insert alongside the existing `DRAW_FILL` / `DRAW_COPY`
/ `DRAW_BLACKNESS` / `DRAW_WHITENESS` arms. COPY_BITS is
opcode 104 — earlier than any of those — so sensible
placement is immediately after the `SURFACE_DESTROY` arm
(the last of the sub-300 opcodes).

### Unit tests

Three tests in the existing `#[cfg(test)] mod tests`
block:

1. `decode_copy_bits_happy_path` — build DrawBase with
   no clip + SpicePoint `(15, 7)`. Assert
   `CopyBitsOutcome::Copy { src_x: 15, src_y: 7, base: ... }`
   with the base fields round-tripping.

2. `decode_copy_bits_negative_src_clamped` — SpicePoint
   `(-3, -2)` encoded as `i32` LE. Assert
   `src_x == 0, src_y == 0`.

3. `decode_copy_bits_rejects_short_payload` — DrawBase
   followed by only 7 bytes of src_pos (one byte short).
   Assert `Err(UnexpectedEof)`.

Test payload builder mirrors phase 2/3 style:

```rust
fn build_copy_bits_payload(src_x: i32, src_y: i32) -> Vec<u8> {
    let mut v = Vec::new();
    // DrawBase: surface_id, top, left, bottom, right (u32 LE), clip_type u8
    v.extend_from_slice(&0u32.to_le_bytes());   // surface_id
    v.extend_from_slice(&50u32.to_le_bytes());  // top
    v.extend_from_slice(&100u32.to_le_bytes()); // left
    v.extend_from_slice(&70u32.to_le_bytes());  // bottom
    v.extend_from_slice(&200u32.to_le_bytes()); // right
    v.push(0);                                  // clip_type = NONE
    v.extend_from_slice(&src_x.to_le_bytes());
    v.extend_from_slice(&src_y.to_le_bytes());
    v
}
```

## Files touched

| File | Change |
|------|--------|
| `ryll/src/channels/display.rs` | Add `CopyBitsOutcome`, `decode_copy_bits`, `handle_copy_bits`; route `COPY_BITS`; three unit tests. |
| `ryll/src/channels/mod.rs` | Drop `#[allow(dead_code)]` on `ChannelEvent::CopyBits`. |
| `ryll/src/display/surface.rs` | Drop `#[allow(dead_code)]` on `copy_bits` (and on `copy_subrect` if clippy now accepts it). |

No protocol-crate changes, no AGENTS.md / README.md
changes (phase 9 batches the doc sweep).

## Open questions

1. **Clip negatives to 0 vs error on negative.** I've
   chosen clip-to-zero (cheap, matches what a naïve
   `as u32` cast almost does). Alternative is to
   warn_once and drop the whole op on a negative src.
   In practice QXL never emits negatives here, so the
   distinction is theoretical; clipping is friendlier
   if a non-conforming server shows up.

2. **Should `handle_copy_bits` have a verbose log?**
   Yes — mirrors the phase 2 and phase 3 verbose-log
   preamble. COPY_BITS frequency can be high (console
   scroll can emit one per line of output) so the log
   line is only meaningful with `-v`. No surprises
   there; the code snippet above shows the shape.

3. **Phase 4 vs deferring to the GLZ investigation.**
   The user's residual "static" is a GLZ bug (see the
   2026-04-21 status note on
   [PLAN-deferred-debt-phase-01-display.md](/components/ryll/plans/PLAN-deferred-debt-phase-01-display/)),
   not a missing draw op. Landing phase 4 will not
   affect the static but *will* fix kernel-console
   scroll if the user's workload exercises it. Plan
   proceeds because phase 4 is small, independently
   revertable, and keeps the master plan on track.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Implement phase 4 per "Implementation shape" and "Files touched". Parse `SpicePoint` via `shakenfist_spice_protocol::messages::SpicePoint`. Route `display_server::COPY_BITS` in `handle_message` after the `SURFACE_DESTROY` arm. Factor parse-and-classify into `decode_copy_bits`. Drop `#[allow(dead_code)]` on `ChannelEvent::CopyBits` and `DisplaySurface::copy_bits` (and on `copy_subrect` if clippy no longer flags it). Add the three unit tests. Use the phase-2 and phase-3 decode helpers + payload builders as structural reference. No changes outside the three files listed in "Files touched". Do not run pre-commit or create commits. |
| 4b | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`. If rustfmt requests changes, run `./scripts/check-rust.sh fix`. Report results; do not fix other failures. |

Small enough that the management session may also drive
directly (phases 1b/1e/1f/3 precedent). Use sub-agent
only if it's genuinely faster than editing in-place.

## Success criteria

* `pre-commit run --all-files` and
  `tools/audit/wave1.sh` both pass.
* All three new unit tests pass; existing tests
  unchanged.
* User reproduces the same BIOS → GRUB → kernel boot
  workload. Expected: kernel-console scroll regions now
  render correctly (each new line appears at the
  bottom, previous lines visible above). The residual
  GLZ "static" is unchanged — that's a separate bug.
* No new warn-once messages (COPY_BITS has no skip or
  fallback path).

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
