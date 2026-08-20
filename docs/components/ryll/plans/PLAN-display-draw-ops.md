# Display draw-op coverage

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

All planning documents go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, and data flow. Consult `AGENTS.md`
for build commands, project conventions, code organisation,
and a table of protocol reference sources.

Key references for this plan in particular:

* `/srv/src-reference/spice/spice-common/common/draw.h` —
  canonical struct definitions for `SpiceFill`, `SpiceCopy`,
  `SpiceOpaque`, `SpiceBlend`, `SpiceBlackness`,
  `SpiceWhiteness`, `SpiceInvers`, `SpiceTransparent`,
  `SpiceAlphaBlend`, `SpiceRop3`, `SpiceStroke`, `SpiceText`,
  plus `SpiceBrush` and `SpiceQMask`.
* `/srv/src-reference/spice/spice-protocol/spice/enums.h` —
  message-type constants and `SPICE_ROPD_*`,
  `SPICE_BRUSH_TYPE_*` enums.
* `/srv/src-reference/spice/spice-gtk/src/channel-display.c`
  — reference handler dispatch table (lines ~2245-2270) and
  per-op handlers (`display_handle_draw_fill`,
  `display_handle_copy_bits`, etc.).
* `/srv/src-reference/spice/spice-html5/src/display.js` —
  the most legible per-op renderer; concise canvas-based
  implementations of `DRAW_FILL`, `DRAW_COPY`, `COPY_BITS`,
  `DRAW_OPAQUE`. (Notes which ops it deliberately leaves
  unimplemented; we should be more complete than html5.)
* `/srv/src-reference/spice/spice-html5/src/spicemsg.js`
  and `spicetype.js` — wire-format parsers, easier to read
  than the C marshallers.
* `/srv/src-reference/qemu/qemu/ui/spice-display.c` —
  server side; useful for confirming which ops modern QXL
  actually emits in practice.

## Situation

The display channel handler currently implements only two
of the SPICE display draw opcodes:

| Implemented            | Opcode |
|------------------------|--------|
| `DRAW_COPY` (304)      | bitmap blit with optional clip |
| `DRAW_COMPOSITE` (318) | logged but not rendered (stub) |

Every other draw opcode falls through the `_ =>` arm in
`handle_message()` at
[ryll/src/channels/display.rs:654](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L654),
which `log_unknown(...)` and discards the payload. The
discarded set (with reference numbers from
`shakenfist-spice-protocol/src/constants.rs`):

| Opcode | Constant       | Purpose |
|--------|----------------|---------|
| 104    | `COPY_BITS`         | intra-surface scroll/copy |
| 302    | `DRAW_FILL`         | rect fill with brush (solid colour or pattern) |
| 303    | `DRAW_OPAQUE`       | image blit with brush+ROP fallback |
| 305    | `DRAW_BLEND`        | image blit with ROP descriptor |
| 306    | `DRAW_BLACKNESS`    | rect fill with 0x000000 (mask-aware) |
| 307    | `DRAW_WHITENESS`    | rect fill with 0xffffff (mask-aware) |
| 308    | `DRAW_INVERS`       | rect colour inversion |
| 309    | `DRAW_ROP3`         | three-source ternary ROP |
| 310    | `DRAW_STROKE`       | line/path stroke |
| 311    | `DRAW_TEXT`         | rasterised glyph text |
| 312    | `DRAW_TRANSPARENT`  | image blit with chroma key |
| 313    | `DRAW_ALPHA_BLEND`  | image blit with constant alpha |

### Why this matters today

User-supplied bug reports
(`ryll-bugreport-2026-04-20T10-01-51Z.zip` and
`ryll-bugreport-2026-04-20T10-04-45Z.zip`) capture the
visible symptom: during BIOS, GRUB, and the kernel boot
console — anything before X/Wayland takes over — the screen
is rendered with horizontal white stripes behind text on
what should be a solid black background. Text glyphs
themselves are rendered correctly (those arrive as
`DRAW_COPY` with cached glyph bitmaps); the *backgrounds*
and *scroll regions* are not.

The artefact has two contributors:

1. The cirrus framebuffer driver and the BIOS use
   `DRAW_FILL` (with a solid black brush) to clear regions
   and `COPY_BITS` to scroll text up. Both are dropped, so
   the previously-rendered pixels remain visible.

2. `DisplaySurface::new()` initialises the pixel buffer to
   `(50, 50, 50)` "dark grey" rather than black
   ([ryll/src/display/surface.rs:21-26](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs#L21-L26)).
   The grey is what we see *between* glyph rows because no
   draw op ever overwrites it.

Once the guest reaches X/Wayland the symptom disappears
because QXL serialises essentially everything to the
offscreen surface cache and emits `DRAW_COPY` against it.
This is why the desktop renders correctly today and the bug
has gone unnoticed.

### Current architecture and how new ops will plug in

Display rendering today flows as follows:

1. The display channel parses each message, decodes any
   referenced image, and emits
   `ChannelEvent::ImageReady { surface_id, left, top,
   width, height, pixels: Vec<u8> }`
   to the main event loop
   ([ryll/src/channels/display.rs:1142-1156](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L1142-L1156)).

2. The app handles `ImageReady` by looking up the
   `DisplaySurface` and calling `surface.blit(...)`
   ([ryll/src/app.rs:562-...](https://github.com/shakenfist/ryll/blob/develop/ryll/src/app.rs#L562)).

3. `DisplaySurface::blit()` does a straightforward RGBA
   memcpy into the surface's pixel buffer
   ([ryll/src/display/surface.rs:39-64](https://github.com/shakenfist/ryll/blob/develop/ryll/src/display/surface.rs#L39-L64)).

4. Repaint is triggered via the shared
   `Arc<tokio::sync::Notify>` (decision #16 in `AGENTS.md`).

For the new draw ops we extend this pipeline:

* Solid-colour fills (`DRAW_FILL` with a solid brush,
  `DRAW_BLACKNESS`, `DRAW_WHITENESS`) only need the
  destination rect and a colour. We can either synthesise a
  pixel buffer in the channel and reuse `ImageReady`, or
  add a dedicated `FillReady { rect, colour }` event and a
  `surface.fill()` helper. The dedicated path saves an
  allocation per fill (potentially many per frame for
  console rendering); plan adopts it.

* `COPY_BITS` is intra-surface (read from a source rect on
  the *same* surface, write to a destination rect). It
  cannot fit the `ImageReady` shape because the channel
  doesn't have the pixels — only the surface does. New
  event variant `CopyBits { surface_id, src_pos, dest_rect,
  clip }` plus a `surface.copy_bits()` helper.

* Image-bearing ops (`DRAW_OPAQUE`, `DRAW_BLEND`,
  `DRAW_TRANSPARENT`, `DRAW_ALPHA_BLEND`) reuse the
  existing image-decode path, then take a different code
  path on the way to the surface. `DRAW_TRANSPARENT` needs
  per-pixel chroma-key compositing; `DRAW_ALPHA_BLEND`
  needs constant-alpha alpha-blending.

* `DRAW_INVERS` is a 1-op trivial pixel rewrite.
  `DRAW_ROP3`, `DRAW_STROKE`, `DRAW_TEXT` are rare on
  modern guests and significantly more involved (ROP3
  needs a 256-entry truth-table evaluator; STROKE needs a
  line rasteriser; TEXT needs glyph bitmap unpacking).

### Wire format

All draw-op payloads start with `SpiceMsgDisplayBase`
(surface_id, box, clip) — already parsed by
`shakenfist_spice_protocol::messages::DrawCopyBase`. The
existing struct has a misleading name (`DrawCopyBase`); a
trivial cleanup is to rename it `DrawBase` and have all the
new handlers reuse it.

After the base header each op has its own structure (from
`/srv/src-reference/spice/spice-common/common/draw.h`):

* `SpiceFill` = `SpiceBrush brush; uint16 rop_descriptor;
  SpiceQMask mask;`
* `SpiceBlackness` = `SpiceWhiteness` = `SpiceInvers` =
  `SpiceQMask mask;`
* `SpiceCopy` = `SpiceBlend` = `SpiceImage *src_bitmap;
  SpiceRect src_area; uint16 rop_descriptor; uint8
  scale_mode; SpiceQMask mask;` (already handled by
  `DRAW_COPY`)
* `SpiceOpaque` = like `SpiceCopy` but also has a
  `SpiceBrush brush;` between `src_area` and
  `rop_descriptor`.
* `SpiceTransparent` = `SpiceImage *src_bitmap; SpiceRect
  src_area; uint32 src_color; uint32 true_color;`
* `SpiceAlphaBlend` = `uint16 alpha_flags; uint8 alpha;
  SpiceImage *src_bitmap; SpiceRect src_area;`
* `SpiceMsgDisplayCopyBits` payload = base + `SpicePoint
  src_pos` (2× uint32).

`SpiceBrush` is a tagged union: `uint8 type` followed by
either a `uint32 color` (type=1, `SOLID`) or a
`SpicePattern` (type=2). `SpiceQMask` is `uint8 flags;
SpicePoint pos; uint32 bitmap_offset` — when
`bitmap_offset == 0` the mask is null. The `SpiceImage *`
pointers in the wire format are byte offsets into the
message payload (same convention as `DRAW_COPY` already
handles).

## Mission and problem statement

Implement enough of the SPICE display draw-op set that ryll
renders the BIOS, GRUB, kernel boot console, and any other
non-X workload as accurately as the spice-gtk reference
client. The bug reports above must look like the
corresponding `remote-viewer` output side-by-side.

Concretely:

* All draw ops listed in the *Situation* table must either
  be implemented or, if deferred, emit a one-line warning
  the first time they're seen per session (so we know when
  a guest workload exercises a deferred op).
* The fix must be incremental: each phase ships a
  user-visible improvement and is independently
  reviewable / revertable.
* Performance matters. The console rendering path may emit
  many small `DRAW_FILL` and `COPY_BITS` ops per second.
  Per-op heap allocation should be avoided where it's easy
  to do so.

## Open questions

1. **Surface-init colour.** The current `(50, 50, 50)`
   grey was chosen for visual debugging — it makes
   "missing draw ops" obvious. Now that we're fixing the
   underlying bug, do we want to keep the grey (and rely
   on `DRAW_FILL` to clear), or initialise to black
   (matching what spice-gtk's canvas does)? The plan
   currently switches to black in phase 1 and treats this
   as a separate, much smaller bug fix.

2. **`rop_descriptor != SPICE_ROPD_OP_PUT`.** Modern QXL
   almost always emits `SPICE_ROPD_OP_PUT` (overwrite). The
   spice-html5 client warns and ignores any other ROP, and
   that's tolerated in practice. Plan adopts the same
   stance: implement `OP_PUT` correctly, warn-once on
   anything else. Open question: is there a known guest
   workload that breaks under this simplification? If so,
   add it to the test plan.

3. **Mask handling.** `SpiceQMask` lets the server limit a
   draw to a 1-bpp bitmap mask. spice-html5 warns and
   ignores masks; spice-gtk implements them via Cairo. For
   the high-impact ops (FILL, BLACKNESS, WHITENESS,
   COPY_BITS) my expectation is masks are very rarely used
   in practice. Plan: implement non-masked path correctly,
   warn-once when a non-null mask is seen, defer mask
   support to a follow-up unless a real workload needs it.

4. **`DRAW_ROP3`, `DRAW_STROKE`, `DRAW_TEXT`.** These are
   complex (ROP3 truth-table; line rasteriser; glyph
   compositor) and rarely emitted by Linux guests under
   QXL. Should they be in scope for this plan, or punted
   to a follow-up? My recommendation is to defer all
   three: implement a one-line "first-seen this session"
   warning and revisit if a real workload needs them.

5. **Test strategy.** Wave 1 unit tests can cover the
   parsers (round-trip, malformed-input). Visual
   verification of the rendered output is the user's job
   (they reproduce the BIOS bug easily). Should we also
   land a `make test-qemu`-style integration test that
   boots a known kernel and pixel-diffs the boot screen
   against a golden image? My recommendation is no —
   unjustified complexity and brittle to kernel updates.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Plumbing: protocol structs, events, surface helpers, surface-init colour fix | [PLAN-display-draw-ops-phase-01-plumbing.md](/components/ryll/plans/PLAN-display-draw-ops-phase-01-plumbing/) | Complete |
| 2. `DRAW_FILL` (solid brush) | [PLAN-display-draw-ops-phase-02-fill.md](/components/ryll/plans/PLAN-display-draw-ops-phase-02-fill/) | Complete |
| 3. `DRAW_BLACKNESS` and `DRAW_WHITENESS` | [PLAN-display-draw-ops-phase-03-monochrome.md](/components/ryll/plans/PLAN-display-draw-ops-phase-03-monochrome/) | Complete |
| 4. `COPY_BITS` | [PLAN-display-draw-ops-phase-04-copy-bits.md](/components/ryll/plans/PLAN-display-draw-ops-phase-04-copy-bits/) | Complete |
| 5. `DRAW_OPAQUE` and `DRAW_BLEND` | [PLAN-display-draw-ops-phase-05-image-rop.md](/components/ryll/plans/PLAN-display-draw-ops-phase-05-image-rop/) | Complete |
| 6. `DRAW_TRANSPARENT` and `DRAW_ALPHA_BLEND` | [PLAN-display-draw-ops-phase-06-alpha.md](/components/ryll/plans/PLAN-display-draw-ops-phase-06-alpha/) | Complete |
| 7. `DRAW_INVERS` and warn-once for deferred ops | [PLAN-display-draw-ops-phase-07-invers-and-warnings.md](/components/ryll/plans/PLAN-display-draw-ops-phase-07-invers-and-warnings/) | Complete |
| 8. `--pedantic` mode and status-bar gap counter | [PLAN-display-draw-ops-phase-08-pedantic.md](/components/ryll/plans/PLAN-display-draw-ops-phase-08-pedantic/) | Complete |
| 9. Thread live bug-report handles to the pedantic observer | [PLAN-display-draw-ops-phase-09-pedantic-handles.md](/components/ryll/plans/PLAN-display-draw-ops-phase-09-pedantic-handles/) | Complete |
| 10. Documentation and release notes | (inline; no separate plan file) | Complete |

### Sequencing rationale

Phase 1 is foundations only — the protocol-crate types and
new `ChannelEvent` variants and surface helpers, with no
new opcodes wired up yet. It also fixes the surface-init
colour. After phase 1 the build is unchanged behaviourally.

Phase 2 is the high-impact phase: `DRAW_FILL` alone is
expected to fix the bulk of the BIOS / kernel-boot
artefact. Land this first so the user can verify the fix
in their own environment before we spend on the rest.

Phases 3 and 4 are close cousins of phase 2 and complete
the "console rendering correctly" milestone.

Phases 5 and 6 cover the image-bearing ops. These are
rarer in modern guests but trivial relative to the
`DRAW_COPY` decode logic that already exists.

Phase 7 mops up `DRAW_INVERS` (1-op fix) and adds the
warn-once instrumentation for the still-deferred ops, so
that future bug reports surface them clearly. The
warn-once registry it introduces is reused by phase 8.

Phase 8 promotes the warn-once registry into a `--pedantic`
mode: the first time per session that ryll silently drops
or partially handles a packet, an automatic bug report is
written to disk and a counter increments in the status
bar. This converts "unknown gap discovery" from a manual
F8-and-describe workflow into a single command-line flag.

Phase 9 updates `README.md`, `ARCHITECTURE.md`, `AGENTS.md`
(decision log), and `docs/plans/index.md`.

### Phase plan structure

Each phase plan should specify:

* What protocol structs (if any) the phase adds in
  `shakenfist-spice-protocol/src/messages.rs`.
* What `ChannelEvent` variants (if any) the phase adds in
  `ryll/src/channels/mod.rs`.
* What surface helpers (if any) the phase adds in
  `ryll/src/display/surface.rs`.
* Which `display_server::*` constants the phase wires up
  in `ryll/src/channels/display.rs`.
* Which app-side handlers the phase adds in
  `ryll/src/app.rs`.
* Unit tests required (parser tests in the protocol crate;
  surface-helper tests in the display crate).
* Edge cases to watch (mask flags, non-`OP_PUT` ROPs,
  destination-rect clipping, intra-surface aliasing for
  `COPY_BITS`).

### Phase 1: Plumbing — sketch

* In `shakenfist-spice-protocol/src/messages.rs`:
  rename `DrawCopyBase` → `DrawBase` (it's the generic
  `SpiceMsgDisplayBase`); add `SpiceBrush`, `SpiceQMask`,
  `SpicePoint`, `SpiceFill`, `SpiceBlackness` (and aliases
  for `Whiteness`/`Invers`), `SpiceTransparent`,
  `SpiceAlphaBlend`, `SpiceOpaque` parsers. All under unit
  tests.
* In `ryll/src/channels/mod.rs`: add new `ChannelEvent`
  variants `FillRect { display_channel_id, surface_id,
  rect, colour, clip }`, `CopyBits { …, src_pos,
  dest_rect, clip }`, `Invert { surface_id, rect, clip }`.
  Defer image-bearing ones to their phases.
* In `ryll/src/display/surface.rs`: add `fill_rect()`,
  `copy_bits()`, `invert_rect()` helpers; switch the
  initial pixel value from `(50,50,50)` to `(0,0,0)`. Unit
  tests for each helper, including aliasing in
  `copy_bits` (overlapping src/dst).
* In `ryll/src/app.rs`: add the event handlers (currently
  unreachable; phases 2-7 will start sending them).

### Phase 2: `DRAW_FILL` — sketch

* In `display.rs::handle_message`, route
  `display_server::DRAW_FILL` to a new
  `handle_draw_fill()` method.
* Parse the base, then `SpiceFill { brush, rop_descriptor,
  mask }`.
* If `brush.type != SOLID` → `warn_once!("draw_fill:
  non-solid brush, type={}")` and return.
* If `rop_descriptor != SPICE_ROPD_OP_PUT` →
  `warn_once!(...)` and return.
* If `mask.flags != 0 || mask.bitmap_offset != 0` →
  `warn_once!(...)` and continue (paint without mask).
* Convert `brush.color` (BGRX little-endian, see
  spice-html5 line ~347) to RGBA and emit
  `ChannelEvent::FillRect { ..., colour, rect: base.rect,
  clip: base.clip_rects }`.
* Add a `warn_once` helper (probably in `logging.rs`),
  keyed by an enum or string, so each new "unhandled"
  case fires only once per session.

### Phase 3: `DRAW_BLACKNESS` / `DRAW_WHITENESS` — sketch

* Both share the same wire payload (`SpiceMsgDisplayBase`
  + `SpiceQMask`).
* Implementation reuses `FillRect` with colour `0x000000ff`
  / `0xffffffff`.
* Same warn-once handling for non-null masks.

### Phase 4: `COPY_BITS` — sketch

* Wire payload is `SpiceMsgDisplayBase` + `SpicePoint
  src_pos` (8 bytes).
* Emit `ChannelEvent::CopyBits { surface_id, src_pos,
  dest_rect, clip }`.
* In `surface.copy_bits()`, handle source/dest aliasing
  correctly: if dest overlaps source, copy in the
  direction that avoids overwriting unread source pixels
  (top-down vs bottom-up; left-to-right vs right-to-left).
  This is the canonical "memmove for pixel rects"
  problem; the spice-html5 implementation gets it for
  free via `getImageData/putImageData` snapshotting,
  which is one viable strategy here too (allocate a temp
  rect, copy, write).

### Phase 5: `DRAW_OPAQUE` and `DRAW_BLEND` — sketch

* Both bring an image (parse the existing
  `ImageDescriptor`-and-decompress path the way
  `handle_draw_copy` does today).
* `DRAW_OPAQUE` adds a brush; for our purposes, treat as
  `DRAW_COPY` with a warn-once when the brush is
  non-trivial.
* `DRAW_BLEND` is `DRAW_COPY` with a non-`OP_PUT` ROP; for
  `OP_PUT` it's identical to `DRAW_COPY`. Warn-once on
  any other ROP.
* Refactor `handle_draw_copy` to share its image-decode
  path with these two so we don't duplicate ~300 lines.

### Phase 6: `DRAW_TRANSPARENT` and `DRAW_ALPHA_BLEND` — sketch

* `DRAW_TRANSPARENT`: read `src_color`; for each pixel in
  the decoded image, if it equals `src_color` (BGRX
  comparison), leave the destination unchanged; else
  overwrite. Add a `surface.blit_chroma()` helper.
* `DRAW_ALPHA_BLEND`: read `alpha`; alpha-blend the
  decoded image into the surface with constant alpha.
  Add a `surface.blit_alpha()` helper.

### Phase 7: `DRAW_INVERS` and warn-once for deferred — sketch

* `DRAW_INVERS`: parse base + mask, emit `Invert { rect,
  clip }`, surface flips `pixel = !pixel & 0xffffff |
  0xff000000`.
* For `DRAW_ROP3`, `DRAW_STROKE`, `DRAW_TEXT`: replace the
  current `_ => log_unknown(...)` path with a
  `warn_once!("display: unimplemented draw op {} ({})",
  msg_type, name)` so the first occurrence per session is
  visible without flooding logs.
* The warn-once registry needs to be designed for reuse in
  phase 8: it should expose both "fire if first seen" and
  "ask: how many distinct gaps have I seen?" so the status
  bar counter in phase 8 doesn't need a parallel data
  structure.

### Phase 8: `--pedantic` mode and status-bar gap counter — sketch

**Motivation.** Today the only way to discover that ryll
silently dropped or partially handled a packet is to read
debug logs after the fact. `--pedantic` flips that around:
ryll proactively reports each *kind* of gap the first time
it sees it, so a user testing against a new guest workload
gets a directory full of bug-report ZIPs (one per distinct
gap) at the end of the session, ready to file or attach.

**Categories of "gap" that trip pedantic mode.** All four
must be covered, because each is a real silent-degradation
class we currently have no automated visibility into:

1. **Truly unknown opcode** — the message type isn't in
   any `display_server::*` / `cursor_server::*` /
   `inputs_server::*` / `main_server::*` / etc. constant
   table. Currently caught by `_ => log_unknown(...)` in
   each channel handler.
2. **Known-but-unimplemented opcode** — the opcode is in
   the constants table but the channel doesn't render it.
   After phase 7 these are the `warn_once!(...)` sites for
   `DRAW_ROP3`, `DRAW_STROKE`, `DRAW_TEXT`,
   `DRAW_COMPOSITE` (still a stub today), etc.
3. **Implemented op with ignored sub-feature** — non-null
   `SpiceQMask`, non-`SPICE_ROPD_OP_PUT` ROP descriptor,
   non-solid brush in `DRAW_FILL`/`DRAW_OPAQUE`, etc.
   Each of these is a `warn_once!(...)` site introduced in
   phases 2-6.
4. **Decode failure** — the channel reached an op it knows
   how to render but couldn't (compression error,
   malformed payload, image cache miss, JPEG decode
   failure). These are already `warn!(...)` sites in
   `display.rs`; pedantic mode taps the same locations.

**Dedupe key.** First-seen-per-session, keyed by
`(channel_type, gap_kind, opcode_or_name)`. So the BIOS
boot generates one report for "display: dropped
DRAW_FILL", not hundreds. The same gap occurring on a
different channel (e.g. unknown opcode on `cursor` vs
`display`) is a separate key and reports separately.

**Status-bar counter.** Always visible (even without
`--pedantic`), of the form `Gaps: N` where N is the count
of distinct keys seen this session. Without `--pedantic`
this is purely informational — the user can click it to
open a small popup listing the gaps. With `--pedantic` the
counter increments in lockstep with auto-saved bug
reports.

**Auto-report destination.** A directory the user can
control: `--pedantic-dir <path>` (default
`./ryll-pedantic-reports/`). Each report uses the existing
`bugreport::assemble()` machinery with a description like
`"pedantic: <channel>: <gap kind>: <opcode>"`. Cap the
number of reports per session (e.g. 50) to bound disk use
even if the dedupe key explodes for some reason.

**Files touched.**
- `shakenfist-spice-protocol/src/logging.rs` — extend the
  warn-once registry with a public iterator over keys and
  a `register_observer` callback so pedantic mode can hook
  it without coupling the protocol crate to the bug-report
  machinery.
- `ryll/src/main.rs` — `--pedantic` and `--pedantic-dir`
  flags; wire the observer.
- `ryll/src/bugreport.rs` — a new `BugReportType::Pedantic`
  variant; an `assemble_to_path()` convenience that picks
  a unique filename in the configured directory.
- `ryll/src/app.rs` — status-bar `Gaps: N` widget plus
  click-to-list popup.
- `ryll/src/channels/display.rs` (and other channels
  later) — call sites for "decode failure" gaps that
  aren't already `warn_once`.

**Edge cases.**
- The bug-report code already needs the channel state and
  pcap ring buffer. Auto-reporting from a non-UI thread
  (channel task) means the report assembly has to grab
  the snapshot lock; this is already how the F8 path
  works, so it's not a new constraint, just one to verify.
- Headless mode (`--headless`) should still write
  pedantic reports to disk; the status-bar counter is a
  no-op there.
- `--pedantic` should not interact with capture mode
  (`--capture`); they're orthogonal toggles.

### Phase 9: Thread live bug-report handles — sketch

Phase 8e shipped the --pedantic observer wired to fresh
stub TrafficBuffers / ChannelSnapshots / AppSnapshot
because main.rs doesn't have access to the live handles
the channel tasks write to (those are built inside
`app::RyllApp::new` / `run_headless`). Pedantic zips
therefore capture gap_key + session metadata correctly
but their traffic pcaps and channel snapshots are empty
— which defeats the *debugging* half of the feature.

Phase 9 moves observer registration from main.rs into
the app constructors so the observer closure captures
the real handles. The master plan treats this as
critical for debugging user-reported issues later; the
gap key tells you *what* failed but the traffic pcap
tells you *why*.

* Derive `Clone` on `ChannelSnapshots` (its fields are
  already `Arc<Mutex<..>>` so cloning is cheap).
* Add `BugReport::register_pedantic_observer(...)` in
  `bugreport.rs` — takes the live handles + a
  `PedanticConfig { dir: PathBuf }` and registers the
  observer closure. This is the factoring that makes
  both the GUI and headless paths share the wiring.
* In `main.rs`, keep the `mkdir -p` eager-failure
  check and the `PedanticConfig` build, pass
  `Option<PedanticConfig>` into `run_gui` /
  `run_headless`. Remove the stub-handle block.
* In `app::RyllApp::new`, after the live handles are
  built, call `register_pedantic_observer` if config
  is Some. `register_gap_observer`'s replay semantics
  cover the (tiny) window between session start and
  observer registration.
* In `run_headless`, same pattern.
* Delete the KNOWN LIMITATION comment in main.rs.

### Phase 10: Documentation — sketch

* Update `ARCHITECTURE.md` to describe the now-broader
  draw-op coverage and the `FillRect`/`CopyBits`/`Invert`
  event types.
* Update `AGENTS.md`: add a decision-log entry about the
  warn-once strategy and the colour-byte-order convention
  (BGRX → RGBA conversion happens in the channel, not the
  surface).
* Update `README.md`: note that BIOS/console rendering now
  works correctly.
* Update `docs/plans/index.md` and `docs/plans/order.yml`
  with this plan.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session (this
conversation) is reserved for planning, review, and
decision-making. This keeps the management context lean and
avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step
   with the brief from the plan, at the recommended
   effort level and model.
3. **Review** the sub-agent's output in the management
   session. Check the actual files — the sub-agent's
   summary describes what it intended, not necessarily
   what it did.
4. **Fix or retry** if the output is wrong.
5. **Commit** once the management session is satisfied.

### Planning effort

The master plan (this file) is at high effort. Each phase
plan should be planned at **high effort** for phases 1, 4,
5, 6, 8 (which involve protocol parsing, intra-surface
aliasing, image compositing, or cross-cutting bug-report
plumbing) and **medium effort** for phases 2, 3, 7, 9
(which follow well-established patterns from earlier
phases).

### Step-level guidance

Each phase plan should include a table like:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a   | medium | sonnet | none     | One-sentence summary of what to do and which files to touch |
```

**When in doubt, skew to the more capable model.**

### Management session review checklist

After a sub-agent completes, the management session
should verify:

- [ ] The files that were supposed to change actually
      changed (read them, don't trust the summary).
- [ ] No unrelated files were modified.
- [ ] The code builds (`tools/audit/wave1.sh` passes).
- [ ] Tests pass.
- [ ] The changes match the intent of the brief.
- [ ] Commit message follows project conventions
      (Co-Authored-By with model, context, effort).

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented
because the following are true:

* The BIOS / GRUB / kernel-boot screenshots from the two
  bug reports cited above render with solid black
  backgrounds and correct scroll-region content (verified
  by the user reproducing the workload against the patched
  client).
* `tools/audit/wave1.sh` passes (rustfmt, clippy with
  `-D warnings`, cargo test, no raw `println!` /
  `eprintln!`, no shellcheck failures).
* Each new draw op has unit tests for its protocol parser
  and (where applicable) its surface helper.
* `ARCHITECTURE.md`, `AGENTS.md`, `README.md`, and
  `docs/plans/index.md` are updated.
* `docs/plans/order.yml` includes this master plan.

### Future work

Items deliberately deferred from this plan:

* **`DRAW_ROP3`** — three-source ternary ROP, requires a
  256-entry truth-table evaluator (`rop3.c` in spice-gtk).
  Rarely emitted by Linux guests; warn-once for now.
* **`DRAW_STROKE`** — line/path rasteriser. Requires a
  Bresenham-style implementation plus dash-pattern
  handling. Defer.
* **`DRAW_TEXT`** — rasterised glyph rendering with
  per-glyph bitmap unpacking. Defer.
* **Mask support** for FILL/BLACKNESS/WHITENESS/INVERS.
  Currently warn-once and ignore. Add if a real workload
  needs it.
* **Non-`OP_PUT` ROP descriptors** for FILL/COPY/BLEND.
  Currently warn-once and use `OP_PUT` semantics. Add if
  a real workload needs it.
* **Pattern brushes** for `DRAW_FILL` and `DRAW_OPAQUE`.
  Currently warn-once; only solid brushes are supported.
* **`DRAW_COMPOSITE`** is currently a stub. It's not in
  scope here but should be revisited once the simpler
  ops are working.
* **`SpiceCopy` / `SpiceBlend` protocol-crate parser.**
  `handle_draw_copy` (and, after phase 5,
  `handle_draw_blend`) still parse the 36-byte SpiceCopy
  header inline with `read_u32_le` / `read_u16_le` — a
  pre-phase-1 idiom that phases 1-4 did not touch. Add a
  `SpiceCopy { src_bitmap, src_top, src_left, src_bottom,
  src_right, rop_descriptor, scale_mode, mask }` struct
  (with an alias `SpiceBlend = SpiceCopy`, per draw.h)
  and migrate both call sites. Deferred from phase 5
  because doing it simultaneously with the
  `decode_image_and_emit` extraction would muddy
  bisection if a DRAW_COPY regression crept in.
* **`Rect` newtype on `ChannelEvent`.** Today every draw-
  related variant (`ImageReady` and the new `FillRect` /
  `CopyBits` / `Invert` from phase 1) carries rect
  geometry as either four separate `u32` fields or a
  `(u32, u32, u32, u32)` tuple. A `Rect { left, top,
  right, bottom }` (or `{ x, y, w, h }`) newtype would
  be clearer and would let helpers like
  `rect.clip_to(bounds)` live somewhere sensible. Out of
  scope for this plan because it churns `ImageReady` and
  every call site; land as its own refactor afterwards.

### Bugs fixed during this work

This section will list bugs encountered during
development. Pre-emptive entry:

* **Surface-init colour.** `DisplaySurface::new()`
  initialises the buffer to `(50, 50, 50)`; switched to
  `(0, 0, 0)` in phase 1.

### Documentation index maintenance

When this plan lands, update:

* **`docs/plans/index.md`** — add a row to *Master plans*
  with the date, link, intent, status, and per-phase
  links.
* **`docs/plans/order.yml`** — add an entry for this
  master plan.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
