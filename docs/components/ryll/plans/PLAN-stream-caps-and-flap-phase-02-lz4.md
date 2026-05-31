# Phase 2 — LZ4 compression

Phase 2 of [PLAN-stream-caps-and-flap.md](/components/ryll/plans/PLAN-stream-caps-and-flap/).

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read the
master plan and the relevant source files; ground your
answers in what the code actually does today. For SPICE
protocol details, cross-check against the reference sources
at `/srv/src-reference/spice/` cited inline.

## Goal

Advertise `SPICE_DISPLAY_CAP_LZ4_COMPRESSION = 5` so the
spice-server is free to choose LZ4 over Zlib for static-UI
image updates. Add unit-test coverage for the existing decoder
so the first time a real server sends us LZ4 isn't also the
first time the decoder is exercised end-to-end.

## Scope

In scope:

- One-bit edit to `capabilities::DEFAULT_DISPLAY` in
  `shakenfist-spice-protocol/src/constants.rs` adding
  `DISPLAY_LZ4_COMPRESSION`.
- Unit tests for `decompress_spice_lz4` in
  `shakenfist-spice-compression/src/lz4.rs` covering at least:
  spice_format 0/4 (BGRX/unspecified → 32 bpp), 6 (BGRA →
  32 bpp), 3 (BGR → 24 bpp); top_down true and false; truncated
  input; zero dimensions; a multi-row image to exercise the
  per-row offset loop. Round-trip via `lz4_flex::compress` for
  the encoder side.
- Manual smoke test against a real spice-server after the cap
  flips on — note in the phase-completion writeup whether the
  cap was negotiated, whether LZ4 frames were seen in the
  pcap, and whether any decoder warnings fired
  (`grep -i 'LZ4' ryll.log`).

Out of scope for this phase:

- 16-bit pixel format support (the decoder explicitly warns
  and skips at `lz4.rs:111-115`; very rare in modern
  spice-server output).
- Wiring an LZ4-vs-Zlib preference into a future
  `PREF_COMPRESSION` message — that lands in phase 7.

## Background

`shakenfist-spice-protocol/src/constants.rs:125` already
defines `DISPLAY_LZ4_COMPRESSION: u32 = 1 << 5`, but the
`DEFAULT_DISPLAY` cap set at lines 130-135 (post-phase-1) does
*not* OR it in. As a result the spice-server falls through to
Zlib/GLZ for any image where it would otherwise prefer LZ4 —
verified by the fact that all `recent_decodes` entries in every
bug report this session show `ZlibGlzRgb` and `LzRgb`, never
`Lz4`.

`shakenfist-spice-compression/src/lz4.rs` ships a
`decompress_spice_lz4(data, width, height) -> Option<DecompressedImage>`
implementation that:

- Reads the 2-byte SPICE LZ4 header `(top_down, spice_format)`.
- Loops rows, each prefixed with a 4-byte big-endian
  compressed-size, decoding via `lz4_flex::decompress(...)` into
  a `width * bpp` row buffer.
- Converts the row to RGBA based on `spice_format` (0/4 → BGRX
  → RGBA opaque; 6 → BGRA → RGBA preserving alpha; 3 → BGR →
  RGBA opaque).
- Handles `top_down` by mapping the source row to either `row`
  or `height - 1 - row` of the destination.

It is dispatched at `shakenfist-spice-renderer/src/channels/display.rs:1991-2003`
inside the existing image-decode match. The
`shakenfist-spice-compression` crate's default features
include `lz4`, so the dependency on `lz4_flex` is already on.

What is missing: unit tests for the decoder, and the cap
advertisement that gets the server to send any.

The decoder's lossless behaviour means the safest correctness
test is a round trip through `lz4_flex::compress` (same
library, same wire byte layout) on a synthetic input.

## Open questions

- **Q1 (decide in implementation):** Is there an existing
  spice-common test vector we should consume verbatim instead
  of round-tripping? Search
  `/srv/src-reference/spice/spice-common/` for any
  `tests/data/*.lz4` or fixture before writing round-trip
  tests; if found, prefer the fixture. If not, round-trip
  tests are the right answer.

## Design notes

### The cap flip

`DEFAULT_DISPLAY` currently reads (post-phase-1):

```rust
pub const DEFAULT_DISPLAY: u32 = DISPLAY_SIZED_STREAM
    | DISPLAY_MONITORS_CONFIG
    | DISPLAY_COMPOSITE
    | DISPLAY_A8_SURFACE
    | DISPLAY_STREAM_REPORT;
```

Add `| DISPLAY_LZ4_COMPRESSION` on a new line at the end. Place
a brief comment noting the cap's effect: it tells the server we
can decode SPICE LZ4 images, so the encoder is free to choose
LZ4 (typically chosen for static UI regions).

### Test surface for the LZ4 decoder

The decoder's branches that need coverage:

1. `spice_format == 4` (BGRX, the default-looking case). Top-down
   single row, then multi-row. Assert RGBA bytes match expected
   (R from src[2], G from src[1], B from src[0], A = 255).
2. `spice_format == 6` (BGRA). Same as above but A from src[3].
3. `spice_format == 3` (BGR, 24-bit). Different `bpp`, exercises
   the 3-byte row stride.
4. `spice_format == 0` (unspecified → 32 bpp BGRX). One test
   asserting the same behaviour as format 4.
5. `top_down == false` — verify rows land at `height - 1 - row`.
6. Truncated input — assert `None` (or a partial image with
   later rows zeroed; the current implementation breaks the loop
   and returns whatever was decoded so far inside a
   `DecompressedImage` — verify the actual behaviour in the
   code and write the test to match).
7. Zero width OR zero height — assert `None` (already explicit
   guard at `lz4.rs:12-15`).
8. Single-row image so the row loop runs exactly once.

Test helper to build a wire-format SPICE LZ4 image from raw
pixel rows:

```rust
fn encode_spice_lz4(top_down: bool, spice_format: u8, width: usize, rows: &[Vec<u8>]) -> Vec<u8> {
    let mut out = Vec::new();
    out.push(if top_down { 1 } else { 0 });
    out.push(spice_format);
    for row in rows {
        debug_assert_eq!(row.len(), width * bpp_for_format(spice_format));
        let comp = lz4_flex::compress(row);
        out.extend_from_slice(&(comp.len() as u32).to_be_bytes());
        out.extend_from_slice(&comp);
    }
    out
}
```

Note: `lz4_flex::compress` produces "raw" (block-only, no frame
framing) output, which is what the SPICE LZ4 format wants per
the existing decoder's `lz4_flex::decompress(...)` call (with
explicit expected size). Confirm by reading
`lz4_flex::decompress`'s signature in the crate docs before
relying on it.

### Verification after the cap is on

The pcap from a session with LZ4 enabled will show the server
issuing `IMAGE_TYPE_LZ_RGB` and/or `IMAGE_TYPE_LZ4` images.
`channel-state.json::recent_decodes` will show entries with
`image_type: "Some(Lz4)"`. If they do not appear, that means
either the server didn't choose LZ4 for this workload (which is
fine — LZ4 is for static, lossless regions; video uses streams)
or our cap advertisement is being rejected (check the link
handshake; `RUST_LOG=shakenfist_spice_protocol=debug` will show
the cap exchange).

## Execution step table

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2A | medium | sonnet | none | **Unit tests for `decompress_spice_lz4`.** Add a `#[cfg(test)] mod tests` block to `shakenfist-spice-compression/src/lz4.rs`. First search `/srv/src-reference/spice/spice-common/` for any `*.lz4` fixture (`find /srv/src-reference/spice/spice-common -iname '*.lz4' -o -iname '*lz4*test*' -o -iname '*test*lz4*'`); if you find one, prefer it. Otherwise round-trip via `lz4_flex::compress`. Cover the eight cases in *Design notes / Test surface for the LZ4 decoder*: spice_format 0/4 (BGRX), 6 (BGRA), 3 (BGR-24); top_down true and false; truncated input; zero dimensions; single-row image. Use `lz4_flex::compress` as the encoder side; the decoder uses `lz4_flex::decompress(slice, expected_size)`. Read the encoder/decoder signature in `Cargo.lock` or by grepping `lz4_flex` to be sure of the API contract (the crate has both `compress` and `compress_prepend_size` — for SPICE we want raw-block output without the prepended size, since the decoder gets the expected size from `row_bytes`). Verify `make build && make test && make lint` pass. **Why medium effort**: round-trip test infrastructure plus three pixel-format branches plus row-order edge cases is more than a one-pattern repeat. Sonnet is fine because the brief enumerates the cases; opus would be overkill. |
| 2B | low | haiku | none | **Advertise `DISPLAY_LZ4_COMPRESSION`.** In `shakenfist-spice-protocol/src/constants.rs`, add `\| DISPLAY_LZ4_COMPRESSION` to the `DEFAULT_DISPLAY` expression at lines 130-135. Add a brief comment on the bit's effect ("server may now choose LZ4 over Zlib for static UI regions"). Verify `make build && make test && make lint`. No new tests; the decoder is already covered by 2A. |
| 2C | — | — | — | **Manual smoke test — deferred to phase 3's step 3H.** After 2B lands, the operator runs a real ryll session against the test VM and confirms (a) the link handshake negotiates cap 5 (b) `channel-state.json::recent_decodes` shows at least one entry with `image_type: "Some(Lz4)"` or that none appear because the workload didn't need LZ4 (c) no `LZ4` warnings in the log. **Status update 2026-05-18:** test session 002b ran on the cap-on build (`241ba13f`) with no LZ4 warnings but also no `image_type: "Some(Lz4)"` entries, because the workload was drag/video-heavy and the user couldn't drive enough static-UI activity to surface LZ4 — that activity was blocked by the MJPEG-decode unresponsiveness that phase 3 fixes. Verification of (b) is therefore folded into phase 3's step 3H (per-platform smoke tests), where the per-platform fast JPEG path is expected to make a sustained static-UI workload feasible. Phase 2 will be marked Complete when 3H confirms LZ4 frames decoded successfully. |

Commits: one per step. 2A and 2B are separate logical changes
(test coverage vs cap advertisement) so deserve separate
commits per the repo convention. 2C is verification, not a
code change.

## Test plan

Automated (added by 2A):

- `decompress_spice_lz4_format4_bgrx_top_down` — single-row,
  4 bpp BGRX, top_down=true, asserts exact RGBA byte values.
- `decompress_spice_lz4_format6_bgra` — 4 bpp BGRA preserving
  alpha.
- `decompress_spice_lz4_format3_bgr24` — 3 bpp BGR, no alpha
  (255 written).
- `decompress_spice_lz4_format0_treated_as_bgrx` — format 0
  produces identical output to format 4.
- `decompress_spice_lz4_bottom_up_row_order` — top_down=false
  inverts row mapping.
- `decompress_spice_lz4_multi_row` — exercises the per-row
  4-byte BE size prefix loop more than once.
- `decompress_spice_lz4_truncated_returns_partial_or_none` —
  match whatever the decoder actually does today (assert
  current behaviour; if it's a bug we fix later, but don't
  silently change semantics here).
- `decompress_spice_lz4_zero_dimensions_returns_none` —
  zero width or height.

Manual (2C):

- Connect ryll to the test VM with the new build.
- Trigger a workload likely to produce static-UI LZ4: scroll
  a long text document, switch between desktop windows, etc.
- After ~30 s, file a Display bug report.
- Verify `recent_decodes` contains at least one `Lz4` entry,
  OR confirm the workload simply didn't trigger LZ4 (check the
  pcap for `IMAGE_TYPE_LZ4` opcodes if curious).
- No `LZ4 decompression failed` / `LZ4 unsupported spice
  format` / `LZ4 truncated` warnings.

## Documentation impact

`ARCHITECTURE.md`'s capability list (if any) needs the LZ4
entry. Strictly that's phase 10's job; if step 2B's
implementer happens to be editing nearby docs, they may add it
opportunistically.

## Success criteria

- `DISPLAY_LZ4_COMPRESSION` (bit 5) is in `DEFAULT_DISPLAY`.
- `decompress_spice_lz4` has the test coverage listed under
  *Test plan*.
- `make build && make test && make lint && pre-commit run
  --all-files` clean.
- Operator smoke test confirms either (a) LZ4 frames decoded
  successfully against a real server or (b) the workload
  didn't need LZ4 (with the cap successfully negotiated).

## Back brief

Before executing any step of this phase, the implementing
sub-agent should back-brief the operator with their
understanding of the step, the files they intend to touch,
and any deviations from the brief.
