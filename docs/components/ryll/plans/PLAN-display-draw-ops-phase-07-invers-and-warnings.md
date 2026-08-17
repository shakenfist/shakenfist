# Phase 7: DRAW_INVERS and warn-once for deferred ops

Part of [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/).

## Prompt

Before responding to questions or discussion points in
this document, explore the ryll codebase thoroughly. Read
relevant source files, understand existing patterns
(SPICE protocol handling, channel architecture, async
task model, egui rendering, the phase-2 warn_once
registry), and ground your answers in what the code
actually does today. Do not speculate about the codebase
when you could read it instead.

Key references for this phase:

* `/srv/src-reference/spice/spice-common/common/draw.h`
  lines 247-249 — `SpiceInvers` is a typedef of
  `SpiceBlackness`, so the wire format is mask-only
  (`SpiceQMask`). Already parseable via phase 1's
  `SpiceBlackness` parser.
* [PLAN-display-draw-ops-phase-03-monochrome.md](/components/ryll/plans/PLAN-display-draw-ops-phase-03-monochrome/)
  — established the mask-only `decode_draw_solid_fill`
  + `handle_draw_solid_fill` pattern used by
  DRAW_BLACKNESS / DRAW_WHITENESS. Phase 7 mirrors it
  for DRAW_INVERS.
* [PLAN-display-draw-ops-phase-02-fill.md](/components/ryll/plans/PLAN-display-draw-ops-phase-02-fill/)
  — defined the `warn_once!` macro, registry storage,
  and key-format convention. Phase 7 extends the key
  set with per-unimplemented-opcode entries.
* [PLAN-display-draw-ops.md](/components/ryll/plans/PLAN-display-draw-ops/)
  §"Phase 7 — sketch" and §"Phase 8" — phase 8's
  pedantic-mode registry reads `warn_once_keys()`, so
  the keys we pick here need to be parseable (the
  master plan's dedupe tuple is
  `(channel_type, gap_kind, opcode_or_name)`).

## Goal

Close out the visible-rendering half of the master plan
by landing `DRAW_INVERS` and converting the four
still-unrendered display opcodes (`DRAW_ROP3`,
`DRAW_STROKE`, `DRAW_TEXT`, `DRAW_COMPOSITE`) from
"silent or log-spam" to "one warn per session, with
hex dump on first occurrence". Every opcode in the
master plan's *Situation* table is then either
implemented or has its unimplemented state clearly
surfaced to the user.

By the end of this phase:

* `display_server::DRAW_INVERS` (opcode 308) routes
  through a new `handle_draw_invers()` that parses
  `DrawBase` + `SpiceQMask`, warn_once's on non-null
  masks, and emits `ChannelEvent::Invert`.
* `ChannelEvent::Invert` and
  `DisplaySurface::invert_rect` lose their
  `#[allow(dead_code)]` tags.
* `DRAW_ROP3`, `DRAW_STROKE`, `DRAW_TEXT`, and
  `DRAW_COMPOSITE` each have an explicit match arm in
  `handle_message` that fires `warn_once!` with a
  per-opcode key, with a hex dump on first occurrence
  (useful for diagnosis but silent on repeats).
* The generic `_ => log_unknown(...)` arm for
  *truly*-unknown opcodes remains unchanged — those are
  phase 8's concern.

## Non-goals

* Implementing `DRAW_ROP3` / `DRAW_STROKE` /
  `DRAW_TEXT` / `DRAW_COMPOSITE`. Each would be a
  plan of its own — ROP3 needs a 256-entry truth-table
  evaluator, STROKE a line rasteriser, TEXT a glyph
  compositor. Per the master plan they stay deferred.
* Upgrading the truly-unknown-opcode `_ =>` arm to
  warn_once. Phase 8 is where the pedantic-mode
  registry picks that up along with truly-unknown
  cursor/inputs/main messages; doing it here would
  muddy scope.
* Touching `DRAW_INVERS` mask handling. Non-null masks
  warn_once and paint anyway (same policy as phases 2
  and 3). Real mask support is master-plan future work.
* Changing the phase-1 `DisplaySurface::invert_rect`
  implementation. The phase-1 helper already computes
  `pixel = (255-r, 255-g, 255-b, alpha_unchanged)`,
  which is what SPICE's `SPICE_ROPD_OP_INVERS`
  semantic requires for a 32-bpp surface with opaque
  alpha.

## Current state

* [shakenfist-spice-protocol/src/messages.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/messages.rs)
  exposes `SpiceInvers = SpiceBlackness` from phase 1;
  the 13-byte mask-only payload parses via
  `SpiceBlackness::read`.
* [ryll/src/channels/display.rs:215-223](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L215)
  has `decode_draw_solid_fill` returning a
  `SolidFillOutcome::Paint { base, masked_fallback }`.
  DRAW_INVERS can't share the *outcome* (caller
  semantics differ) but can share the parsing shape.
* [ryll/src/channels/display.rs:677](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L677)
  currently has `DRAW_COMPOSITE` as a `debug!`-level
  stub, and `DRAW_INVERS`/`DRAW_ROP3`/`DRAW_STROKE`/
  `DRAW_TEXT` all fall into the `_ => log_unknown(...)`
  arm at line 949.
* [ryll/src/channels/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/mod.rs)
  `ChannelEvent::Invert` still carries
  `#[allow(dead_code)] // constructed in phase 7`.
* [ryll/src/display/surface.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs)
  `invert_rect` still carries `#[allow(dead_code)]`,
  plus `invert_subrect` (private helper).
* [ryll/src/app.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs) already has
  the `ChannelEvent::Invert` dispatch arm from phase 1f.
  No change needed there.
* [shakenfist-spice-protocol/src/logging.rs](https://github.com/shakenfist/ryll/blob/develop/shakenfist-spice-protocol/src/logging.rs)
  `warn_once!` macro, `warn_once_count()`, and
  `warn_once_keys()` are live from phase 2. Key format
  is a static string; convention is colon-delimited
  `"channel:kind:detail"`.

## Implementation shape

### DRAW_INVERS

The wire format is identical to DRAW_BLACKNESS (both are
`DrawBase + SpiceQMask`), so we reuse the phase-3
`decode_draw_solid_fill` and `SolidFillOutcome` rather
than cloning them. Phase-3's outcome name reads slightly
loose when you see it return from `handle_draw_invers`,
but the call-site destructure makes the geometry obvious
and a one-line comment at the reuse handles the rest.

```rust
async fn handle_draw_invers(&mut self, payload: &[u8]) -> Result<()> {
    if settings::is_verbose() {
        if let Ok(base) = DrawBase::read(payload) {
            logging::log_detail(&format!(
                "draw_invers: surface={}, rect=({},{})-({},{}), clip_type={}",
                base.surface_id, base.left, base.top, base.right, base.bottom, base.clip_type,
            ));
        }
    }

    let InversOutcome::Invert { base, masked_fallback } = decode_draw_invers(payload)?;

    if masked_fallback {
        warn_once!(
            "display:draw_invers:mask_present",
            "display: draw_invers: non-null mask, inverting unmasked"
        );
    }

    self.event_tx
        .send(ChannelEvent::Invert {
            display_channel_id: self.channel_id,
            surface_id: base.surface_id,
            rect: (base.left, base.top, base.right, base.bottom),
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
display_server::DRAW_INVERS => {
    self.handle_draw_invers(payload).await?;
}
```

Insert next to DRAW_WHITENESS in the match — opcode 308
immediately follows 307.

### Warn-once for deferred opcodes

Replace the current DRAW_COMPOSITE stub and the three
no-op fall-throughs with explicit arms. Each carries a
namespaced `warn_once!` key and a first-occurrence
hex-dump.

The master plan phase 8 dedupe-key convention is
`(channel_type, gap_kind, opcode_or_name)`. We pre-fit
it here as `"display:unimpl:draw_rop3"` etc. — so phase
8 can split on `:` and extract the tuple without
parsing heuristics.

```rust
display_server::DRAW_ROP3 => {
    warn_once!(
        "display:unimpl:draw_rop3",
        "display: draw_rop3: unimplemented, skipping (256-entry ROP truth-table evaluator not yet ported)"
    );
    log_unknown_once("display", msg_type, payload);
}
display_server::DRAW_STROKE => {
    warn_once!(
        "display:unimpl:draw_stroke",
        "display: draw_stroke: unimplemented, skipping (line/path rasteriser not yet ported)"
    );
    log_unknown_once("display", msg_type, payload);
}
display_server::DRAW_TEXT => {
    warn_once!(
        "display:unimpl:draw_text",
        "display: draw_text: unimplemented, skipping (glyph rendering not yet ported)"
    );
    log_unknown_once("display", msg_type, payload);
}
display_server::DRAW_COMPOSITE => {
    warn_once!(
        "display:unimpl:draw_composite",
        "display: draw_composite: unimplemented, skipping"
    );
    log_unknown_once("display", msg_type, payload);
}
```

### `log_unknown_once` helper

The current `logging::log_unknown` fires `warn!` plus a
hex dump every time. For a known-but-unimpl opcode we
want the hex dump exactly once (for diagnosis when the
gap first appears) and silence after. Small helper
colocated with `warn_once!` in the protocol crate:

```rust
// in shakenfist-spice-protocol/src/logging.rs

/// One-shot variant of `log_unknown`: hex-dumps the payload on
/// the first call for (channel, msg_type) in this session, and
/// is silent on repeats. Used by the display channel's
/// known-but-unimplemented opcode arms (phase 7) and by the
/// truly-unknown arms elsewhere (phase 8).
pub fn log_unknown_once(channel: &'static str, msg_type: u16, payload: &[u8]) {
    // Compose a stable key. We only get channel × msg_type
    // worth of uniqueness (payload size varies per call), which
    // is the right granularity: the hex dump is most useful the
    // first time we see a given opcode.
    let key = format!("{}:hexdump:{}", channel, msg_type);
    // Intern the formatted key into a static slot so the
    // warn_once registry (which stores &'static str) can take
    // it. The intern uses OnceLock<Mutex<HashMap<String,
    // &'static str>>>; leaks a String per distinct key, which
    // is bounded by the number of distinct opcodes (maybe 50).
    let interned = intern_key(key);
    if warn_once_impl_if_new(interned) {
        hex_dump(payload, 64);
    }
}
```

The interning and the "fired or not" query are both new
public surface in `logging.rs`:

```rust
/// Intern a dynamically-composed key as a `'static str`
/// so the warn_once registry can store it. The key is
/// leaked into process-lifetime memory; use for
/// channel × msg_type style keys, not for per-message
/// data.
pub fn intern_key(key: String) -> &'static str { ... }

/// Thread-safe: insert `key` into the warn_once registry.
/// Returns `true` if the key was new (fire side-effects),
/// `false` if already present.
pub fn warn_once_impl_if_new(key: &'static str) -> bool { ... }
```

`warn_once_impl` (phase 2) uses the same insert logic
but always formats+fires the message on new keys;
`warn_once_impl_if_new` is the caller-side variant that
lets the caller decide what to do on new-vs-repeat.
Factor a private helper both go through:

```rust
fn register_key(key: &'static str) -> bool { ... }  // returns is_new

pub fn warn_once_impl(key: &'static str, message: &str) {
    if register_key(key) { warn!("{}", message); }
}
pub fn warn_once_impl_if_new(key: &'static str) -> bool {
    register_key(key)
}
```

### Dead-code cleanup

In `ryll/src/channels/mod.rs`, remove the
`#[allow(dead_code)] // constructed in phase 7` on
`ChannelEvent::Invert`.

In `ryll/src/display/surface.rs`, remove the
`#[allow(dead_code)]` on `invert_rect`. Check whether
clippy still flags `invert_subrect`; if not, remove it
from there too.

### Unit tests

No new DRAW_INVERS parser tests. Phase 3's
`decode_draw_solid_fill_happy_path`,
`decode_draw_solid_fill_masked_fallback`, and
`decode_draw_solid_fill_rejects_short_payload` already
cover the parser we're reusing. Adding three
identically-shaped copies named `decode_draw_invers_*`
would just be the naming churn the Open-Questions
resolution rejected.

For the `logging.rs` additions:

1. `log_unknown_once_fires_once` — call with the same
   (channel, msg_type) three times and assert that
   `warn_once_keys()` contains exactly one matching entry.
2. `log_unknown_once_distinct_opcodes` — call with two
   different msg_types, assert both keys appear.
3. `intern_key_returns_same_str_for_same_input` —
   sanity check that `intern_key(s.clone()) ==
   intern_key(s)` returns the same pointer.

(These tests use the same unique-key-per-test discipline
established in phase 2.)

## Files touched

| File | Change |
|------|--------|
| `shakenfist-spice-protocol/src/logging.rs` | Add `intern_key` + `warn_once_impl_if_new` + `log_unknown_once`. Factor `register_key` shared between `warn_once_impl` and `warn_once_impl_if_new`. Three unit tests. |
| `ryll/src/channels/display.rs` | Add `InversOutcome`, `decode_draw_invers`, `handle_draw_invers`; route `DRAW_INVERS`; replace the `DRAW_COMPOSITE` debug-stub with a `warn_once!` arm and add explicit arms for `DRAW_ROP3`/`DRAW_STROKE`/`DRAW_TEXT` calling `warn_once!` + `log_unknown_once`. Three new unit tests for `decode_draw_invers`. |
| `ryll/src/channels/mod.rs` | Drop `#[allow(dead_code)]` on `ChannelEvent::Invert`. |
| `ryll/src/display/surface.rs` | Drop `#[allow(dead_code)]` on `invert_rect` (and maybe `invert_subrect`). |

Protocol crate gets a small new public API surface
(`intern_key`, `warn_once_impl_if_new`,
`log_unknown_once`); the rest stays behind the existing
`warn_once!` macro.

## Resolved questions

All five resolved before execution; recorded here so a
future reader sees the rationale.

1. **Reuse `decode_draw_solid_fill` for DRAW_INVERS.**
   The wire format is identical, and a ceremonial
   parallel `decode_draw_invers` + `InversOutcome`
   would just duplicate code for a slightly more
   accurate variant name. The call-site destructure
   makes the geometry obvious and a one-line comment
   handles the rest.

2. **Hex dump on first occurrence** via
   `log_unknown_once`. Diagnostic value on first
   appearance outweighs the on-disk cost; subsequent
   repeats stay silent.

3. **Upgrade `DRAW_COMPOSITE`'s `debug!` stub to
   `warn_once!`.** Current `debug!`-level is silent
   under default `RUST_LOG` so users don't see the gap
   today; upgrading brings it in line with the other
   deferred ops and puts it in the phase-8 registry.

4. **Per-opcode `warn_once` keys** rather than a
   shared `"display:unimpl"` bucket. Makes phase 8's
   pedantic mode generate one bug report per distinct
   unimpl opcode and keeps the gap counter
   informative.

5. **`log_unknown_once` in the protocol crate's
   `logging` module.** Phase 8 will want it for every
   channel's pedantic-mode dispatch, not just display.
   Marginal cost to put it there now, avoids a move
   later.

The warn_once convention established across phases 2-7
has been codified in
[STYLEGUIDE.md §"warn_once for protocol gaps"](https://github.com/shakenfist/ryll/blob/develop/STYLEGUIDE.md#warn_once-for-protocol-gaps)
so future phases follow it consistently.

## Sub-agent execution plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | In `shakenfist-spice-protocol/src/logging.rs`, add `intern_key(String) -> &'static str` (via `OnceLock<Mutex<HashMap<String, &'static str>>>`; leaks keys for process lifetime, safe since callers key off bounded sets like `channel × msg_type`), `warn_once_impl_if_new(&'static str) -> bool`, and `log_unknown_once(channel: &'static str, msg_type: u16, payload: &[u8])`. Factor a private `register_key` helper so `warn_once_impl` and `warn_once_impl_if_new` share the mutex-and-insert dance. Add the three unit tests from "Unit tests" subsection. Do NOT touch ryll/*. |
| 7b | medium | sonnet | none | In `ryll/src/channels/display.rs`, add `handle_draw_invers` (which reuses the existing phase-3 `decode_draw_solid_fill` — DRAW_INVERS and DRAW_BLACKNESS share a wire format) and route `display_server::DRAW_INVERS`. The handler destructures `SolidFillOutcome::Paint { base, masked_fallback }`, warn_once's on masked_fallback with key `"display:draw_invers:mask_present"`, and emits `ChannelEvent::Invert`. Replace the existing `DRAW_COMPOSITE` debug-stub arm and add explicit arms for `DRAW_ROP3`/`DRAW_STROKE`/`DRAW_TEXT` — each calls `warn_once!` with the key/message shown in "Warn-once for deferred opcodes" and then `logging::log_unknown_once(...)`. Drop `#[allow(dead_code)]` from `ChannelEvent::Invert` in `ryll/src/channels/mod.rs` and from `DisplaySurface::invert_rect` (and `invert_subrect` if clippy no longer flags it) in `ryll/src/display/surface.rs`. Do NOT add new parser unit tests — phase-3's `decode_draw_solid_fill_*` tests already cover the shared parser. Import `log_unknown_once` from `shakenfist_spice_protocol::logging`. |
| 7c | low    | sonnet | none | Run `pre-commit run --all-files` and `tools/audit/wave1.sh`; if rustfmt requests changes, run `./scripts/check-rust.sh fix`. Report results. |

Sequencing: 7a → 7b → 7c. 7b depends on the new
`logging.rs` symbols from 7a.

## Success criteria

* `pre-commit run --all-files` and
  `tools/audit/wave1.sh` both pass.
* All three new unit tests (for the `logging.rs`
  additions) pass; existing tests unchanged.
* User reproduces their BIOS / GRUB / desktop
  workload. Expected: no visible rendering changes
  (DRAW_INVERS is rare; the four newly-warn-once'd
  ops are even rarer on modern QXL workloads). The
  logs will gain a handful of new `warn!` lines on
  first occurrence if any of the ops fire, and stay
  quiet after — that's the intended win.
* `warn_once_keys()` at the end of a session lists
  `display:draw_invers:mask_present` (if masked),
  `display:unimpl:draw_rop3`, `display:unimpl:draw_stroke`,
  `display:unimpl:draw_text`, `display:unimpl:draw_composite`
  as appropriate, one per op that fired. Phase 8 will
  read this list to drive its status-bar counter.

## Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
