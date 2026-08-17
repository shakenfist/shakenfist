# Phase 3: DRAW_BLACKNESS and DRAW_WHITENESS

Part of [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read
relevant source files, understand existing patterns
(SPICE protocol handling, channel architecture, async
task model, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead.

Key references for this phase:

* `/srv/src-reference/spice/spice-common/common/draw.h`
  lines 247-249 — canonical C definitions. Note the
  struct body is *just* `SpiceQMask mask;` — no brush, no
  ROP descriptor, no source image. The opcode itself is
  the whole operation.
* `/srv/src-reference/spice/spice-html5/src/display.js`
  — reference client. spice-html5 deliberately does not
  implement these (it warns and drops), so spice-gtk is
  the second opinion. Not high-priority reading for
  phase 3; the wire format is trivially small.
* [PLAN-display-draw-ops-phase-02-fill.md](/components/ryll/plans/PLAN-display-draw-ops-phase-02-fill/)
  — phase 2 established the pattern we're mirroring:
  `decode_*(payload) -> Outcome` pure helper,
  `handle_*(payload)` async shim, warn-once for each
  ignored sub-feature.

## Goal

Light up `DRAW_BLACKNESS` (opcode 306) and
`DRAW_WHITENESS` (opcode 307). Both have the same wire
payload (`DrawBase` + `SpiceQMask`) and both reduce to
painting a solid RGBA colour into the `DrawBase` rect —
black `[0,0,0,255]` for blackness, white
`[255,255,255,255]` for whiteness. Reuse the existing
`ChannelEvent::FillRect` pipeline added in phase 1 and
wired up in phase 2.

Expected user-visible effect: the brief "static" flash
the user reported after phase 2 landed. Firmware and
cirrus use blackness to clear the screen between
BIOS / GRUB / kernel stages; without it the previous
stage's pixels stay on the surface until something
overpaints them.

By the end of this phase:

* `display_server::DRAW_BLACKNESS` routes to a new
  handler that emits
  `ChannelEvent::FillRect { colour: [0,0,0,255], .. }`.
* `display_server::DRAW_WHITENESS` routes to the same
  handler with a different colour argument.
* Non-null masks warn once (key per opcode) and paint
  unmasked. There is no rop/brush to worry about.
* Parser structs from phase 1 (`SpiceBlackness`,
  `SpiceWhiteness`) are used directly — no new protocol
  additions.
* The `decode_*` helper pattern established in phase 2
  is extended with a sibling `decode_draw_solid_fill`
  (or similar) that both `DRAW_BLACKNESS` and
  `DRAW_WHITENESS` share — and that later phases can
  reuse where they reduce to "solid-colour rect with
  optional mask".

## Non-goals

* Mask support. Non-null masks warn-once and proceed
  unmasked. Still in "Future work" in the master plan.
* `DRAW_INVERS` — a sibling op with the same wire
  payload but different semantics (pixel inversion, not
  solid paint). Deferred to phase 7, which already has
  a surface helper (`invert_rect`) and a
  `ChannelEvent::Invert` variant waiting. Bundling here
  would couple two unrelated user-visible changes.
* No UI changes, no new protocol structs, no new
  surface helpers.

## Current state

* [shakenfist-spice-protocol/src/messages.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/messages.rs)
  has `SpiceBlackness` (with `SpiceWhiteness` and
  `SpiceInvers` as type aliases) from phase 1. 13 bytes,
  one `SpiceQMask` field.
* [ryll/src/channels/display.rs:378-384](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L378-L384)
  handles `DRAW_FILL` (phase 2) and `DRAW_COPY`; every
  other draw op falls into `_ => log_unknown(...)` at
  ~line 654.
* `ChannelEvent::FillRect` (phase 1) and
  `DisplaySurface::fill_rect` (phase 1) are both live
  after phase 2 and ready to be called again.
* `warn_once!` macro (phase 2) is available for the
  mask-fallback warnings.

## Wire format

From draw.h:

```c
typedef struct SpiceBlackness {
    SpiceQMask mask;
} SpiceBlackness, SpiceInvers, SpiceWhiteness;
```

13 bytes, all consumed by `SpiceQMask`. The full
DRAW_BLACKNESS / DRAW_WHITENESS payload is therefore:

```
DrawBase (21 bytes minimum, +4+16*N for clip rects)
  + SpiceQMask (13 bytes)
```

No brush, no ROP descriptor, no source bitmap. Simpler
than DRAW_FILL.

## Implementation shape

### Decode helper

```rust
/// What we decided to do with a DRAW_BLACKNESS /
/// DRAW_WHITENESS. Mirrors `FillOutcome` from phase 2.
#[derive(Debug, Clone)]
enum SolidFillOutcome {
    /// Happy path. Caller supplied the colour so this
    /// helper is reusable by both opcodes.
    Paint {
        base: DrawBase,
        masked_fallback: bool,
    },
}

fn decode_draw_solid_fill(payload: &[u8]) -> std::io::Result<SolidFillOutcome> {
    let base = DrawBase::read(payload)?;
    let rest = &payload[base.end_offset..];
    let body = SpiceBlackness::read(rest)?;
    let masked_fallback =
        body.mask.flags != 0 || body.mask.bitmap_offset != 0;
    Ok(SolidFillOutcome::Paint { base, masked_fallback })
}
```

The single-variant enum is deliberate: `SpiceBlackness`
has no rop/brush, so there is no skip case — every
payload is paintable. Returning a struct would work
too; the enum shape keeps it visually parallel to
`FillOutcome` and leaves room for future skip cases
(e.g. if we later want to skip on certain mask flag
combos).

### Async handler

One handler, parameterised by colour and opcode name
(for the warn-once key namespace):

```rust
async fn handle_draw_solid_fill(
    &mut self,
    payload: &[u8],
    op_name: &'static str,   // "draw_blackness" or "draw_whiteness"
    mask_warn_key: &'static str,
    colour: [u8; 4],
) -> Result<()> {
    if settings::is_verbose() {
        if let Ok(base) = DrawBase::read(payload) {
            logging::log_detail(&format!(
                "{}: surface={}, rect=({},{})-({},{}), clip_type={}",
                op_name,
                base.surface_id, base.left, base.top,
                base.right, base.bottom, base.clip_type,
            ));
        }
    }

    let SolidFillOutcome::Paint { base, masked_fallback } =
        decode_draw_solid_fill(payload)?;

    if masked_fallback {
        logging::warn_once_impl(
            mask_warn_key,
            &format!("display: {}: non-null mask, painting unmasked", op_name),
        );
        // or equivalently, use the warn_once! macro — but
        // passing a static key through a parameter is the
        // one case where `warn_once_impl` is cleaner.
    }

    self.event_tx
        .send(ChannelEvent::FillRect {
            display_channel_id: self.channel_id,
            surface_id: base.surface_id,
            rect: (base.left, base.top, base.right, base.bottom),
            colour,
            clip: base.clip_rects,
        })
        .await
        .ok();
    self.repaint_notify.notify_one();

    Ok(())
}
```

**Why parameterise by `mask_warn_key` as a `&'static
str`?** The `warn_once!` macro's first argument must be
`&'static str` because the registry stores
`HashSet<&'static str>`. Using `warn_once_impl`
directly lets callers pass a static they own. Two thin
wrappers around a shared body is the alternative:

```rust
async fn handle_draw_blackness(&mut self, payload: &[u8]) -> Result<()> {
    self.handle_draw_solid_fill(
        payload,
        "draw_blackness",
        "display:draw_blackness:mask_present",
        [0, 0, 0, 0xff],
    ).await
}
async fn handle_draw_whiteness(&mut self, payload: &[u8]) -> Result<()> {
    self.handle_draw_solid_fill(
        payload,
        "draw_whiteness",
        "display:draw_whiteness:mask_present",
        [0xff, 0xff, 0xff, 0xff],
    ).await
}
```

Both shim + handler fit in ~70 lines total. This pair
keeps the `handle_message` match arms symmetric with
the phase 2 pattern:

```rust
display_server::DRAW_FILL => { self.handle_draw_fill(payload).await? }
display_server::DRAW_BLACKNESS => { self.handle_draw_blackness(payload).await? }
display_server::DRAW_WHITENESS => { self.handle_draw_whiteness(payload).await? }
```

### Unit tests

Three tests in `ryll/src/channels/display.rs`'s existing
`#[cfg(test)] mod tests` block (added in phase 2):

1. `decode_draw_solid_fill_happy_path` — build DrawBase
   with no clip + SpiceQMask with flags=0,
   bitmap_offset=0. Assert
   `SolidFillOutcome::Paint { masked_fallback: false, base: ... }`
   and check the base fields round-trip.
2. `decode_draw_solid_fill_masked_fallback` — same but
   set `mask.bitmap_offset = 0x200`. Assert
   `masked_fallback == true`.
3. `decode_draw_solid_fill_rejects_short_payload` —
   12-byte payload (one byte short of the minimum).
   Assert `Err(UnexpectedEof)`.

Each test can reuse the `build_draw_fill_payload` shape
from phase 2 as inspiration but needs its own builder —
the mask layout is identical but there's no brush or
rop_descriptor. Keep the two builders separate and
untangle only if a third phase shares a shape.

We do NOT add visual/behavioural tests that exercise
the full DisplayChannel path — phase 2 covered that
pattern with the same `FillRect` pipeline, and
re-testing it here adds no coverage that the integration
test case would not already provide.

## Files touched

| File | Change |
|------|--------|
| `ryll/src/channels/display.rs` | Route `DRAW_BLACKNESS` and `DRAW_WHITENESS`; add `decode_draw_solid_fill`, `SolidFillOutcome`, `handle_draw_solid_fill`, and two thin shims. Three new unit tests. |

Nothing else. `channels/mod.rs` and `display/surface.rs`
already export `FillRect` and `fill_rect` from phase 2;
no dead-code attributes to lift; no protocol-crate
changes.

## Open questions

1. **Shared `handle_draw_solid_fill` vs two copies.** The
   sketch uses a parameterised shared body. Alternative
   is two ~25-line handlers that duplicate the decode
   call and send. The shared form is ~10 lines shorter
   and trivially DRYer; the parameterised warn-once key
   is a small cost. Plan adopts the shared body. If the
   sub-agent finds the `&'static str` parameter
   genuinely ugly, two copies is an acceptable
   alternative.

2. **Should `decode_draw_solid_fill` also fail-fast on
   a non-null mask?** spice-gtk *implements* masks for
   these ops (via Cairo). We don't, and painting
   unmasked can paint pixels the server wanted to
   preserve. Two positions:
   
   a. Paint unmasked (current plan) — matches our
      phase-2 DRAW_FILL mask fallback, keeps the pixel
      pipeline simple, gives the user *something*
      rather than nothing.
   b. Skip on non-null mask — correct-or-nothing;
      matches spice-html5's "warn + drop" behaviour.
   
   I lean (a): the user reported the static is better
   after phase 2, but the firmware path that hits
   DRAW_BLACKNESS is almost certainly emitting null
   masks (it's clearing the whole screen; no reason to
   mask). If QXL surprises us with masked ops on real
   workloads, the warn-once will surface it. Happy to
   flip to (b) if you want the tighter contract.

3. **`DRAW_INVERS` alongside?** It shares the wire
   format exactly. But the semantic (inversion) needs
   the `ChannelEvent::Invert` variant and
   `invert_rect` helper, which are already
   `#[allow(dead_code)]` waiting for phase 7. Landing
   all three here would bundle two unrelated user
   stories (solid paint + inversion); keeping phase 7
   separate preserves the master plan's sequencing and
   lets each phase stay independently revertable.
   Plan keeps phase 7 separate.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | In `ryll/src/channels/display.rs`, add `SolidFillOutcome`, `decode_draw_solid_fill`, `handle_draw_solid_fill`, `handle_draw_blackness`, `handle_draw_whiteness`. Route `DRAW_BLACKNESS` and `DRAW_WHITENESS` in `handle_message`. Add three unit tests for the decoder as described in "Unit tests". No changes outside display.rs. Use the phase-2 DRAW_FILL implementation as a structural reference; keep the warn-once key strings namespaced (`"display:draw_blackness:mask_present"`, `"display:draw_whiteness:mask_present"`). Import `SpiceBlackness` from the protocol crate; you may ignore `SpiceWhiteness` since it's the same type alias. |
| 3b | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`; if rustfmt asks for formatting changes, run `./scripts/check-rust.sh fix`. Report results. Do not fix other failures; surface them to the management session. |

3a and 3b are sequential (3b can only run after 3a's
code is in place).

Since this phase's diff is small (~100 lines including
tests), an alternative is for the management session to
drive it directly — as with phase 1e/1f/1b the review-
a-summary overhead may not be worth it. The table above
keeps the sub-agent option available; use management-
direct if it's clearly faster on the day.

## Success criteria

* `pre-commit run --all-files` and
  `tools/audit/wave1.sh` both pass.
* All three new unit tests pass; no existing tests
  regressed.
* User reproduces the same BIOS/GRUB/kernel-boot
  workload. Expected: the brief "static" flash after
  phase 2 is either gone or significantly reduced. If
  phase 3 doesn't fully clear it, the residual is
  likely `COPY_BITS` (phase 4) or possibly
  `DRAW_OPAQUE` (phase 5).
* First-time non-null mask on either opcode emits
  exactly one warn-line per gap kind per session.

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
