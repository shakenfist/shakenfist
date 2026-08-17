# Phase 2: DRAW_FILL (solid brush)

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

* `/srv/src-reference/spice/spice-protocol/spice/enums.h` —
  `SPICE_ROPD_*` and `SPICE_BRUSH_TYPE_*` numeric values.
* `/srv/src-reference/spice/spice-html5/src/display.js`
  lines 333-360 — the reference implementation of
  DRAW_FILL. Warns and skips on non-`OP_PUT` ROP, warns
  and skips on non-null mask, handles only
  `SPICE_BRUSH_TYPE_SOLID`, strips the alpha byte and
  converts the remaining BGR into a CSS colour string.
* `/srv/src-reference/spice/spice-gtk/src/canvas.c`
  (`canvas_draw_fill` and `canvas_draw_solid_rect`) —
  production client; for confirming colour byte order
  against a second independent implementation.
* [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/)
  §"Wire format" and §"Phase 2" for the shape.
* [PLAN-display-draw-ops-phase-01-plumbing.md](/components/ryll/plans/PLAN-display-draw-ops-phase-01-plumbing/)
  for the protocol structs and surface helpers this phase
  wires together.

## Goal

Light up `DRAW_FILL` (opcode 302) with a solid brush and
`SPICE_ROPD_OP_PUT` — enough to clear the regions of the
framebuffer that BIOS, GRUB, and the kernel boot console
blank before drawing text. After this phase the visible
horizontal-stripe artefact from
`ryll-bugreport-2026-04-20T10-*.zip` should be gone during
BIOS and GRUB (the kernel-console scroll case needs phase
4's `COPY_BITS` in addition).

By the end of this phase:

* The display channel routes `display_server::DRAW_FILL`
  to a new `handle_draw_fill()` that parses `SpiceFill`,
  handles the happy path, and emits
  `ChannelEvent::FillRect`.
* Non-solid brushes, non-`OP_PUT` ROP descriptors, and
  non-null masks each warn exactly once per session (via
  a new `warn_once!` helper in `logging.rs`) and then
  fall back to either skipping (non-`OP_PUT`, non-solid)
  or painting without the mask (non-null mask).
* A small set of new named constants
  (`SPICE_ROPD_OP_PUT`, `SPICE_BRUSH_TYPE_*`) are added
  to the protocol crate so call sites do not use magic
  numbers.
* The `#[allow(dead_code)]` tags on
  `ChannelEvent::FillRect` and on
  `DisplaySurface::fill_rect` are removed — they have a
  live caller now.

## Non-goals

* Mask support for `DRAW_FILL`. Non-null masks warn once
  and are ignored; painting proceeds without masking.
  Adding real mask support is tracked in the master
  plan's "Future work" list.
* Pattern brushes. Non-solid brushes warn once and the
  `DRAW_FILL` is dropped. Same "Future work" bucket.
* Non-`OP_PUT` ROP descriptors for `DRAW_FILL`. Same.
* Plumbing any other opcode through. `DRAW_BLACKNESS` and
  `DRAW_WHITENESS` are phase 3; they are intentionally
  held back so phase 2 is cleanly revertable if the BIOS
  symptom gets worse somehow.
* Any UI changes. The status-bar gap counter is phase 8.

## Current state

* [ryll/src/channels/display.rs:378-384](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L378-L384)
  — `DRAW_COPY` is handled; `DRAW_COMPOSITE` is logged as
  a stub; `DRAW_FILL` falls into the `_ =>
  log_unknown(...)` arm at
  [ryll/src/channels/display.rs:654](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L654).
* [shakenfist-spice-protocol/src/messages.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/messages.rs)
  has the `DrawBase`, `SpiceFill`, `SpiceBrush`,
  `SpiceQMask` parsers from phase 1.
* [shakenfist-spice-protocol/src/constants.rs:189](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/constants.rs#L189)
  defines `DRAW_FILL = 302` but no `SPICE_ROPD_*` or
  `SPICE_BRUSH_TYPE_*`.
* [shakenfist-spice-protocol/src/logging.rs:1-76](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs)
  has `log_message`, `log_detail`, `log_unknown`, and
  `hex_dump`. No warn-once helper.
* [ryll/src/channels/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs)
  `ChannelEvent::FillRect` is defined (phase 1) but
  `#[allow(dead_code)]`.
* [ryll/src/display/surface.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs)
  `DisplaySurface::fill_rect` is defined (phase 1) but
  `#[allow(dead_code)]`.

## Colour byte order

`SpiceBrush::Solid.color` is a `u32` read as little-endian.
The spice-html5 reference
(`/srv/src-reference/spice/spice-html5/src/display.js:347-348`)
extracts:

* `R = (color >> 16) & 0xff`
* `G = (color >>  8) & 0xff`
* `B =  color        & 0xff`

…which is equivalent to the wire bytes being `[B, G, R,
X]` — standard BGRX. The top byte is padding ("do brushes
ever have alpha?" — the spice-html5 comment is correct to
ignore it). Our `DisplaySurface` buffer is RGBA, so the
emit is:

```rust
let r = ((color >> 16) & 0xff) as u8;
let g = ((color >>  8) & 0xff) as u8;
let b = ( color        & 0xff) as u8;
let rgba = [r, g, b, 255];
```

Consistent with the `xRGB -> RGBA` conversion the cursor
channel already does for `cursor_type = Color32`.

## The warn-once helper

New API in
[shakenfist-spice-protocol/src/logging.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs).
The master plan calls for a registry that phase 8 can
iterate; phase 2 only needs "fire once per key",
but the shape should already allow phase 8 to hook it
later without another redesign.

### API sketch

```rust
/// Emit `tracing::warn!` exactly once per session for
/// each distinct `key`, then silently drop subsequent
/// calls. Thread-safe; safe to call from any task.
///
/// The macro form is preferred at call sites because it
/// defers the `format!` until first occurrence:
///
/// ```ignore
/// warn_once!("display:draw_fill:non_solid_brush",
///            "display: draw_fill: non-solid brush type {}, skipping",
///            brush_type);
/// ```
///
/// Behind the scenes this calls `warn_once_impl(key,
/// message)` which owns the registry lookup.
pub fn warn_once_impl(key: &'static str, message: &str);

/// Count of distinct keys that have fired so far this
/// session. Public for the status-bar gap counter
/// introduced in phase 8.
pub fn warn_once_count() -> usize;

/// Iterator-style snapshot of the fired keys. Public for
/// the phase-8 pedantic popup / bug-report assembly.
/// Returns a new Vec so the caller does not hold the
/// registry lock.
pub fn warn_once_keys() -> Vec<&'static str>;
```

Storage: a `std::sync::OnceLock<Mutex<HashSet<&'static
str>>>` (or equivalently `LazyLock`) keyed by the static
string. `&'static str` keeps the key alloc-free at call
sites — callers pass string literals.

### The macro

```rust
#[macro_export]
macro_rules! warn_once {
    ($key:expr, $($arg:tt)+) => {{
        $crate::logging::warn_once_impl(
            $key,
            &format!($($arg)+),
        );
    }};
}
```

Re-export at the crate root so call sites can
`use shakenfist_spice_protocol::warn_once;`.

### Tests

Add three unit tests in `logging.rs`:

1. `warn_once_fires_once` — call three times with the
   same key, capture `tracing` output (via
   `tracing_test` if already a workspace dependency, or
   a simpler approach: just assert
   `warn_once_count()` bumps by 1 on first call and
   stays put on subsequent calls).
2. `warn_once_distinct_keys_all_fire` — three different
   keys; `warn_once_count()` ends at 3.
3. `warn_once_keys_snapshot_is_stable` — after N keys
   fire, `warn_once_keys()` returns exactly those keys
   in some order (sort both sides of the assertion).

If `tracing_test` isn't already a dep, don't add it —
the count-based assertions are sufficient for phase 2,
and phase 8 will exercise the side effects directly.

### Cross-test pollution

Tests run in the same process. If multiple tests use the
same key, the second one sees a no-op. Fix by giving
each test a unique key prefix (e.g. include the test
name literally).

## New protocol-crate constants

Add to
[shakenfist-spice-protocol/src/constants.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/constants.rs):

```rust
/// Raster operation descriptors (SPICE_ROPD_* in enums.h).
pub mod ropd {
    pub const INVERS_SRC:   u16 = 1 << 0;
    pub const INVERS_BRUSH: u16 = 1 << 1;
    pub const INVERS_DEST:  u16 = 1 << 2;
    pub const OP_PUT:       u16 = 1 << 3;  // 0x08 — overwrite destination
    pub const OP_OR:        u16 = 1 << 4;
    pub const OP_AND:       u16 = 1 << 5;
    pub const OP_XOR:       u16 = 1 << 6;
    pub const OP_BLACKNESS: u16 = 1 << 7;
    pub const OP_WHITENESS: u16 = 1 << 8;
    pub const OP_INVERS:    u16 = 1 << 9;
    pub const INVERS_RES:   u16 = 1 << 10;
}

/// Brush-type tag values (SPICE_BRUSH_TYPE_* in enums.h).
pub mod brush {
    pub const NONE:    u8 = 0;
    pub const SOLID:   u8 = 1;
    pub const PATTERN: u8 = 2;
}
```

We include the full `ropd::*` set now rather than just
`OP_PUT`, because phases 3-7 will reference others (at
minimum for their warn-once messages); one module add is
cheaper than eight add-one-at-a-time amendments. `brush`
currently uses only `SOLID` and `NONE` (both paths warn
on the latter) but phases 5-6 use `PATTERN`.

`SpiceBrush::read` in messages.rs parses brush types
`0/1/2` directly and errors on others; rewrite those
match arms to use `brush::NONE/SOLID/PATTERN` so the
mapping is visible at one site. No behaviour change.

## The `handle_draw_fill` method

New `async fn handle_draw_fill(&mut self, payload: &[u8])
-> Result<()>` on `DisplayChannel`, after `handle_draw_copy`.
Wire it up in `handle_message`'s match on `msg_type`
alongside `DRAW_COPY` and `DRAW_COMPOSITE`.

### Shape

```rust
async fn handle_draw_fill(&mut self, payload: &[u8]) -> Result<()> {
    let base = DrawBase::read(payload)?;

    if settings::is_verbose() {
        logging::log_detail(&format!(
            "draw_fill: surface={}, rect=({},{})-({},{}), clip_type={}",
            base.surface_id, base.left, base.top,
            base.right, base.bottom, base.clip_type,
        ));
    }

    let rest = &payload[base.end_offset..];
    let (fill, _consumed) = SpiceFill::read(rest)?;

    // 1. ROP descriptor. Only OP_PUT is modelled.
    if fill.rop_descriptor != ropd::OP_PUT {
        warn_once!(
            "display:draw_fill:non_op_put",
            "display: draw_fill: unhandled ROP descriptor {:#x}, skipping",
            fill.rop_descriptor
        );
        return Ok(());
    }

    // 2. Brush. Only SOLID is modelled.
    let color = match fill.brush {
        SpiceBrush::Solid { color } => color,
        SpiceBrush::None => {
            warn_once!(
                "display:draw_fill:none_brush",
                "display: draw_fill: NONE brush, skipping"
            );
            return Ok(());
        }
        SpiceBrush::Pattern { .. } => {
            warn_once!(
                "display:draw_fill:pattern_brush",
                "display: draw_fill: PATTERN brush not yet supported, skipping"
            );
            return Ok(());
        }
    };

    // 3. Mask. Non-null mask warns and we proceed unmasked.
    if fill.mask.flags != 0 || fill.mask.bitmap_offset != 0 {
        warn_once!(
            "display:draw_fill:mask_present",
            "display: draw_fill: non-null mask (flags={:#x}, off={}), painting unmasked",
            fill.mask.flags, fill.mask.bitmap_offset
        );
    }

    let rgba = [
        ((color >> 16) & 0xff) as u8,
        ((color >>  8) & 0xff) as u8,
        ( color        & 0xff) as u8,
        0xff,
    ];

    self.event_tx
        .send(ChannelEvent::FillRect {
            display_channel_id: self.channel_id,
            surface_id: base.surface_id,
            rect: (base.left, base.top, base.right, base.bottom),
            colour: rgba,
            clip: base.clip_rects,
        })
        .await
        .ok();
    self.repaint_notify.notify_one();

    Ok(())
}
```

### Integration in `handle_message`

```rust
display_server::DRAW_FILL => {
    self.handle_draw_fill(payload).await?;
}
```

Insert alongside `DRAW_COPY` and `DRAW_COMPOSITE`. Keeps
the `_ => log_unknown(...)` arm at the bottom for every
other unimplemented opcode.

### Notes

* `DrawBase.clip_rects` already returns `Vec<(u32, u32,
  u32, u32)>` in `(left, top, right, bottom)` order,
  matching the shape `ChannelEvent::FillRect` expects.
  No translation needed.
* `base.end_offset` is already computed by
  `DrawBase::read` to account for optional
  variable-length clip data; `SpiceFill::read` consumes
  from that offset.
* Errors from `DrawBase::read` and `SpiceFill::read`
  bubble up through `?` and are handled by the channel's
  existing error path (which closes the channel). Short
  payloads on a draw op are a protocol error, not a
  warn-once.

## Dead-code annotation cleanup

With the new live caller, remove:

* `#[allow(dead_code)] // constructed in phase 2+` on
  `ChannelEvent::FillRect` in `ryll/src/channels/mod.rs`.
* `#[allow(dead_code)]` on `DisplaySurface::fill_rect`
  in `ryll/src/display/surface.rs`.

Leave the other allows (`CopyBits`, `Invert`,
`copy_bits`, `invert_rect`) in place until their
respective phases ship callers.

## Verification

### Unit-test additions

Only one new unit test outside the `warn_once` tests:

* In `ryll/src/channels/display.rs`, a `tests` module
  test for `handle_draw_fill`'s wire parsing — build a
  minimal `DRAW_FILL` payload (DrawBase with no clip +
  SpiceFill with SOLID brush, OP_PUT, null mask),
  instantiate a `DisplayChannel` with a mock `event_tx`
  (tokio `mpsc::channel(1)`), call `handle_draw_fill`,
  and assert the received event is the expected
  `FillRect { colour: [R,G,B,255], rect: (…), clip: []
  }`.

If `DisplayChannel` construction is awkward for a unit
test (it requires a `SpiceStream`, a `TrafficBuffers`, a
`repaint_notify`, etc.), extract the parser-and-translate
portion into a free function
`decode_draw_fill(payload: &[u8]) -> io::Result<FillOutcome>`
where `FillOutcome` is
`enum { Paint(u32, DrawBase), SkipNonOpPut, SkipNoneBrush,
SkipPatternBrush }`, unit-test that, and have
`handle_draw_fill` be a thin shim over it. That's also
friendlier for phase 3 to reuse.

Recommendation: start with the thin `decode_draw_fill`
helper — it unblocks the same pattern for
`decode_draw_blackness` etc. in phase 3.

### Manual verification (user)

The user reproduces the BIOS workload from
`ryll-bugreport-2026-04-20T10-*.zip`. Expected: the
horizontal-stripe artefact behind BIOS text is gone
(solid black background). GRUB text region likely also
clean. Kernel-console scroll artefact is expected to
persist until phase 4.

### Wave1 audit

`tools/audit/wave1.sh` must pass: rustfmt, clippy `-D
warnings`, cargo test, no raw `println!`/`eprintln!`, no
shellcheck failures.

### Warn-once manual check

Run with `-v`: load a guest that emits at least one
`DRAW_FILL` with a non-`OP_PUT` rop or non-null mask
(even a repeated stream emits the warn-once once);
confirm that exactly one warn line appears in the log
and that ryll does not crash or spam.

## Files touched

| File | Change |
|------|--------|
| `shakenfist-spice-protocol/src/constants.rs` | Add `ropd` and `brush` modules. |
| `shakenfist-spice-protocol/src/messages.rs` | Rewrite `SpiceBrush::read` to reference the new `brush::*` constants (no behaviour change). |
| `shakenfist-spice-protocol/src/logging.rs` | Add `warn_once_impl`, `warn_once_count`, `warn_once_keys`, `warn_once!` macro, and unit tests. |
| `shakenfist-spice-protocol/src/lib.rs` | Re-export the `warn_once!` macro at crate root. |
| `ryll/src/channels/display.rs` | Route `DRAW_FILL`; add `handle_draw_fill` (and optionally `decode_draw_fill` helper + tests). |
| `ryll/src/channels/mod.rs` | Drop `#[allow(dead_code)]` on `ChannelEvent::FillRect`. |
| `ryll/src/display/surface.rs` | Drop `#[allow(dead_code)]` on `fill_rect`. |

Documentation files unchanged (phase 9 bundles the doc
sweep).

## Open questions

1. **`warn_once` key format.** I've used
   `"display:draw_fill:non_op_put"` — colon-delimited,
   channel-first. Cheap to extend; plays nicely with
   phase 8's `(channel_type, gap_kind,
   opcode_or_name)` dedupe key, which can derive its
   tuple by splitting on `:`. Alternative: a
   structured enum. I recommend the string form because
   the registry value type stays `&'static str`
   (alloc-free at the call site) and phase 8 can parse
   the structure when it needs to; anything structured
   here is work-in-reverse.

2. **Logging at the Rust level on first fire.** I've
   used `tracing::warn!` inside `warn_once_impl`. An
   alternative is `info!` to avoid yelling about
   commonly-ignored features (non-null mask is benign
   in practice). I recommend `warn!`: the whole point of
   the registry is to surface gaps, and `info!` tends to
   be filtered out by default subscribers. Users can
   tune log level if they find the noise annoying.

3. **Short-circuit on non-`OP_PUT` vs paint anyway.**
   spice-html5 skips. spice-gtk actually *implements*
   many of the ROPs. I recommend skipping (matching
   html5) for phase 2 — correct-for-OP_PUT is more
   useful than half-correct-for-everything, and the
   warn-once tells us when we're wrong.

4. **Where does `decode_draw_fill` live?** Either
   alongside `handle_draw_fill` in `display.rs`, or in
   a new `ryll/src/channels/display/draw_ops.rs`
   sub-module. Option A is simpler, and display.rs at
   1500 lines is already the pattern. Recommendation:
   start in display.rs; split into a sub-module only
   when phases 3-7 add enough sibling helpers that
   display.rs starts groaning.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | low    | sonnet | none | Add `pub mod ropd` and `pub mod brush` to `shakenfist-spice-protocol/src/constants.rs` with the 11 ROPD constants and the 3 brush-type constants. Rewrite the match arms in `SpiceBrush::read` to use `brush::NONE`, `brush::SOLID`, `brush::PATTERN`; the unknown-type arm keeps its current `InvalidData` form. No other changes. |
| 2b | medium | sonnet | none | In `shakenfist-spice-protocol/src/logging.rs`, add `warn_once_impl(&'static str, &str)`, `warn_once_count() -> usize`, `warn_once_keys() -> Vec<&'static str>`, and a `warn_once!` macro. Registry is `OnceLock<Mutex<HashSet<&'static str>>>`. Add the three unit tests from the "Tests" subsection of this plan. In `shakenfist-spice-protocol/src/lib.rs`, `pub use` the macro (or `#[macro_export]` and re-export). Do NOT touch ryll/*. |
| 2c | high   | opus   | none | In `ryll/src/channels/display.rs`, add `handle_draw_fill` and route `display_server::DRAW_FILL` to it in `handle_message`. Implement per the "The `handle_draw_fill` method" section: parse DrawBase, parse SpiceFill, handle rop/brush/mask via `warn_once!`, emit `ChannelEvent::FillRect`, notify repaint. Factor the parse-and-classify portion into `decode_draw_fill(payload: &[u8]) -> io::Result<FillOutcome>` (as described in the "Unit-test additions" section) and add a unit test for it covering happy path, non-OP_PUT, non-solid brush, and non-null mask cases. Also drop `#[allow(dead_code)]` from `ChannelEvent::FillRect` in `ryll/src/channels/mod.rs` and from `DisplaySurface::fill_rect` in `ryll/src/display/surface.rs`. |
| 2d | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`; report results. If rustfmt asks for formatting changes, run `./scripts/check-rust.sh fix`. Do not fix anything else; surface other failures to the management session. |

Sequencing: 2a → 2b in parallel (different files, no
dependencies); 2c depends on both (uses the new
constants and the `warn_once!` macro); 2d last.

Actually 2a and 2b touch different files AND 2c depends
on both — if I run 2a and 2b in parallel they'll each
make a modification, and the management session commits
both before launching 2c. Safe. Use sequential runs if
parallel causes any Docker/cache contention.

## Success criteria

* `pre-commit run --all-files` and
  `tools/audit/wave1.sh` both pass.
* New unit tests (3 `warn_once`, 1 `SpiceBrush` match-arm
  regression if the sub-agent added one, 4 or so
  `decode_draw_fill`) all pass.
* Nothing in the existing test suite regressed.
* User verifies BIOS rendering no longer has the grey
  horizontal-stripe artefact behind text. (GRUB likely
  clean too; kernel console scroll still broken until
  phase 4.)
* First-time non-`OP_PUT`, non-solid-brush, or non-null-
  mask `DRAW_FILL` emits exactly one warn-line per gap
  kind per session; repeats are silent.

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
