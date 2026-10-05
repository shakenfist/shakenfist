# Image decoder hardening

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
LZ/GLZ/QUIC compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

Consult `ARCHITECTURE.md` for the system architecture
overview, `docs/development.md` for build commands, and
`AGENTS.md` and `STYLEGUIDE.md` for project conventions. The
canonical QUIC decoder is
`/srv/src-reference/spice/spice-common/common/quic.c` and
`quic_tmpl.c`; LZ and GLZ live alongside them in
`spice-common/common/`.

## Situation

In July 2026 a security review of the client filed twelve issues
(#171 to #182). Each one lets a malicious SPICE server, or anyone
in the middle of a non-TLS connection, crash ryll with a panic or
make it allocate gigabytes. PR #437 fixes the three in
`shakenfist-spice-protocol` and the channel read loops (#179,
#180, #181), and #178 was already fixed by 80ff6e8. PR #437 says
outright that "the compression-crate half of the cluster
(#171-#177) is a separate PR". This plan is that PR, plus #182,
which is in the renderer.

All eight issues were re-checked against `develop` at 78c7238 on
2026-10-03, and every one still applies. Line numbers have drifted
since the issues were filed; the ones below are current.

| Issue | Kind | Where (at 78c7238) |
|-------|------|--------------------|
| #171 | panic | `quic.rs:923`, `:1001`, `:1312`, `:1379` compute `run_end = i + run` unclamped; `quic.rs:646-648` reads `J[32]` on a 32-entry array |
| #172 | alloc bomb | `quic.rs:624`/`:629` take width and height off the wire; `reset_channels` (`:586`) resizes four rows at `:451` before the cap at `:1574` runs |
| #173 | panic | `display.rs:2179-2187` copies `row_bytes = width * 4` per row but only checked `stride * height` against the buffer |
| #174 | panic | `display.rs:2304` FromCache arm pairs cached pixels with descriptor dimensions; crop and clip copies at `:2511` and `:2545` slice past the buffer |
| #175 | alloc bomb | `lz.rs:67`, `glz.rs:276`, `lz4.rs:55` size the output from wire dimensions with `checked_mul` only |
| #176 | alloc bomb | `display.rs:2268` `read_to_end` on a zlib stream with no output limit; `_glz_size` at `:2262` is read and ignored |
| #177 | alloc bomb | `cursor.rs:532` allocates from `u16` cursor dimensions before the pixel-data length checks |
| #182 | debug panic | `surface.rs:81` `dst_stride - left * 4` underflows when `left >= width`; `blit_chroma` (`:280`) and `blit_alpha` (`:342`) already guard this |

### The root cause is the convention, not the call sites

Six of the eight bugs come from one gap. There is no shared limit
on how large a decoded image may be. Each decoder either picks its
own limit or has none. Today there are four:

- `MAX_DECODED_JPEG_DIMENSION = 16384` per side, in
  `shakenfist-spice-compression/src/jpeg.rs:60`, reused by
  `video.rs:52`.
- A bare `16384` literal in `quic.rs:1574`.
- `MAX_SURFACE_DIMENSION = 16_384` in
  `shakenfist-spice-renderer/src/display/surface.rs:16`.
- `MAX_PIXMAP_PIXELS = 64 Mi` pixels (8192 x 8192, 256 MiB of
  RGBA), local to one match arm at `display.rs:2140`. PR #437
  promotes it to module scope and derives `MAX_MESSAGE_BODY` from
  it.

LZ, GLZ, LZ4, zlib-GLZ, the cursor decoder and QUIC's per-channel
rows have no limit at all. `STYLEGUIDE.md`'s "Dimension safety"
section tells authors to use `checked_mul` on `width * height * 4`,
and nothing more. That is the advice these decoders followed. It
prevents overflow, but a 65535 x 65535 image passes it and asks for
17 GiB.

Patching each call site would leave the next decoder to make the
same mistake. The fix is one shared limit that every decoder calls,
plus a style guide that says to use it.

## Mission and problem statement

Make every image decoder in `shakenfist-spice-compression` and
`shakenfist-spice-renderer` refuse hostile input without panicking
or allocating more than one bounded image buffer, and leave a
single shared helper and style rule behind so new decoders get
this by default.

Specifically:

1. One pair of limits, a per-side dimension cap and a total pixel
   cap, defined once in `shakenfist-spice-compression` and used by
   every decoder in both crates.
2. One helper that turns `(width, height)` into a byte length, or
   refuses. It replaces the checked-multiply idiom the style guide
   currently recommends.
3. `DecompressedImage` cannot be constructed with a pixel buffer
   whose length disagrees with its dimensions. This closes #174 at
   the type rather than at one consumer.
4. The QUIC run decoder matches upstream spice-common's bounds.
5. One commit per issue, each with a regression test that fails
   (panics, or would allocate past the cap) against the old code.

Out of scope: per-channel message size caps (#436), a declarative
bounded parser (#136), and fuzz targets for the compression crate
(#135). These are recorded under Future work.

## Open questions

Questions 1 to 3 were resolved with the operator on 2026-10-03, and
each records its decision. Question 4 is resolved by the Step 1a/1b
split.

1. **What is the cap?** **Decided: 16384 per side and 64 Mi
   pixels, both applied everywhere.** The recommendation was to keep both existing values
   and apply both everywhere. 16384 per side is already used by
   JPEG, video, QUIC and surfaces. 64 Mi pixels is already used by
   pixmaps and, after #437, sets `MAX_MESSAGE_BODY`. With both
   applied, a 16384 x 4096 image passes and a 16384 x 16384 image
   (1 GiB of RGBA) does not. The largest realistic surface is an
   8K display, 7680 x 4320 = 33 Mi pixels, so this leaves 2x
   headroom. The behaviour change is that JPEG and video frames
   above 64 Mi pixels, which are allowed today, will be refused. No
   SPICE server sends those, so this should be accepted, but it is
   a change and the PR description must say so.

2. **Should `DecompressedImage::new` become fallible?**
   **Decided: yes, `new` and `new_glz` return `Option`.** The
   compression crate is published on crates.io at 0.x, so a
   breaking change costs a minor version bump and nothing else.
   Recommendation: `new` and `new_glz` return
   `Option<DecompressedImage>`, returning `None` when
   `pixels.len() != width * height * 4`. Callers in the renderer
   already handle a `None` image as a decode failure. The
   alternative, a `debug_assert!` plus a check in the FromCache
   arm, fixes #174 and leaves the invariant to be broken again by
   the next constructor call.

3. **Cursor cap.** **Decided with question 1: cursors use the
   shared limits.** #177 suggests 16384 to match the other
   decoders. Real cursors are 32 to 256 pixels on a side, so a
   much tighter cap (512, say) is defensible. Recommendation: use
   the shared helper and its limits, for consistency. The cursor
   then costs at most 256 MiB, which is the same worst case as any
   other image. Do not add a cursor-specific constant unless review
   asks for one.

4. **Sequencing with PR #437.** #437 moves `MAX_PIXMAP_PIXELS` to
   module scope in `display.rs` and builds `MAX_MESSAGE_BODY` from
   it. Re-pointing that constant at the shared limit has to wait
   for #437. **Resolved by splitting Step 1.** Step 1a (the
   `limits` module, the `DecompressedImage` change, JPEG and QUIC)
   does not touch what #437 changes, so it lands first and Steps 2
   and 3 build on it. Step 1b re-points the renderer's
   `MAX_PIXMAP_PIXELS` and `MAX_SURFACE_DIMENSION` after #437
   merges and this branch is rebased onto it. Neither PR blocks
   the other's review, but this one should merge second.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Decoder hardening | This file, steps below | In progress | |
| 2. Push audit | This file, below | Not started | |

### Phase 1: Decoder hardening

This phase is a single pull request with one commit per step
below, except Steps 2 to 4, which commit once per issue. Every
commit builds and passes `make test` on its own.

| Step | Effort | Model | Isolation | Status | Brief for sub-agent |
|------|--------|-------|-----------|--------|---------------------|
| 1a | high | opus | none | Complete | Shared limit, helper and `DecompressedImage` invariant. See brief 1, except its renderer-constant bullet. |
| 1b | low | sonnet | none | Complete | After #437 merges and the branch is rebased: the renderer-constant bullet of brief 1. |
| 2 | high | opus | none | Complete | QUIC: #171 then #172, as two commits. See brief 2. |
| 3 | medium | sonnet | none | Complete | LZ, GLZ, LZ4 and zlib-GLZ: #175 then #176, as two commits. See brief 3. |
| 4 | medium | opus | none | Complete | Renderer: #177, #173, #174, #182, as four commits. See brief 4. |
| 5 | medium | sonnet | none | Complete | Documentation. See brief 5. |

All six steps are complete on the `decoder-hardening` branch (PR
#443); Step 1b ran after the branch was rebased onto #437. The phase
stays `In progress` until the PR merges and its merge commit is
recorded.

Because `DecompressedImage::new`/`new_glz` now return `Option` and
`MAX_DECODED_RGBA_BYTES` dropped from 1 GiB to 256 MiB, the next
release must be a minor bump (0.2.0), not a patch; see
[Choosing the version](/components/ryll/releasing/#choosing-the-version).
The kerbside proxy needs no bump: it depends only on
`shakenfist-spice-protocol`, at a pinned git revision, and the only
change to that crate on this branch is an added constant
(`IMAGE_FLAGS_CACHE_REPLACE_ME`, from #457).

**Brief 1: shared limit and helper (Step 1a and Step 1b, one
commit each).**

- Add `shakenfist-spice-compression/src/limits.rs` and re-export it
  from `lib.rs`. It holds `pub const MAX_IMAGE_DIMENSION: u32 =
  16384`, `pub const MAX_IMAGE_PIXELS: usize = 64 * 1024 * 1024`,
  and `pub fn rgba_len(width: usize, height: usize) ->
  Option<usize>`. `rgba_len` returns `None` when either side is
  zero, either side exceeds the dimension cap, or the pixel count
  exceeds the pixel cap. Otherwise it returns `width * height * 4`,
  which cannot overflow once both caps hold.
- Point `MAX_DECODED_JPEG_DIMENSION` at `MAX_IMAGE_DIMENSION`, and
  route its byte ceiling through `rgba_len`. Replace the literal at
  `quic.rs:1574`.
- Step 1b only: the renderer's `MAX_PIXMAP_PIXELS` (module
  scope in `display.rs` once #437 has landed) is deleted, and
  `MAX_MESSAGE_BODY` and the Pixmap arm use `MAX_IMAGE_PIXELS` and
  `rgba_len` directly. `MAX_SURFACE_DIMENSION` in `surface.rs:16`
  becomes `MAX_IMAGE_DIMENSION`.
- Make `DecompressedImage::new` and `new_glz` return `Option`,
  refusing a pixel buffer whose length is not `rgba_len(width,
  height)`. Update every caller (`grep -rn
  'DecompressedImage::new'`). The FromCache arm at `display.rs:2304`
  is left for Step 4, which adds a warning there. In this commit it
  only adapts to the new signature.
- Rewrite `STYLEGUIDE.md`'s "Dimension safety" section: decoders
  size buffers with `rgba_len` and do not hand-roll the checked
  multiply. Keep the reason, which is attacker-controlled
  dimensions.
- Tests in `limits.rs` cover zero, exactly at each cap, one over
  each cap, and `u32::MAX` sides.

**Brief 2: QUIC (two commits).**

- #171: in all four run loops (`quic.rs:923`, `:1001`, `:1312`,
  `:1379`), refuse the run when `run > end - i`, and return `false`
  the way the surrounding decode errors do. Do not silently clamp.
  This mirrors `quic_tmpl.c:567`, where `run_end > (end - i)` is a
  decode error. In `decode_run` (`quic.rs:646`) and its counterpart
  for the RGB state, change the guard to `melcstate < J.len() - 1`.
  Upstream uses `MELCSTATES - 1` (`quic.c:514`, `:551`). Add tests:
  one with a crafted stream whose run overshoots the row, and one
  that drives `melcstate` to 31. Both panic on the old code. The
  existing error-handling tests from 8839e63 show how to build
  streams.
- #172: in `quic_decode_begin` (`quic.rs:595`), check width and
  height with `limits::rgba_len` straight after they are read at
  `:624` and `:629`, before `reset_channels`. Return `false` on
  refusal. The later check at `:1574` then becomes redundant;
  delete it rather than leaving two. The test is a header declaring
  `width = 0xFFFF_FFFF`, which must return `None` without
  allocating.

**Brief 3: LZ, GLZ, LZ4 and zlib-GLZ (two commits).**

- #175: replace the checked-multiply blocks before `vec![0u8; ...]`
  in `lz.rs:63-67`, `glz.rs` (around `:272-276`) and `lz4.rs:52-55`
  with `limits::rgba_len`. LZ and GLZ return the existing `anyhow`
  error with the dimensions in the message. LZ4 returns `None`. LZ4
  still needs its `row_bytes` checked multiply, because `bpp`
  varies. Each decoder gets a test with a 65535 x 65535 header and
  a tiny payload, which must fail fast. `lz4.rs:421` already has an
  "absurd dimensions" test; check what it covers and extend it
  rather than duplicating it.
- #176: in the zlib-GLZ arm (`display.rs:2255-2275`), bound the
  inflate with `decoder.by_ref().take(limit + 1).read_to_end(...)`
  and refuse when more than `limit` bytes come out. Derive `limit`
  from `rgba_len(img_desc.width, img_desc.height)`; a GLZ stream
  can be slightly larger than its RGBA output (33-byte header,
  a control byte per 32 literals, and the alpha pass for RGBA),
  so the implementation bounds the inflate at
  `rgba_len + rgba_len/4 + 64` (`glz_stream_limit` in
  `display.rs`). Validate the declared
  `_glz_size` against the same limit and against the inflated
  length, and rename it now that it is used. The test is a small
  DEFLATE stream of zeros that inflates past the limit.

**Brief 4: renderer (four commits).**

- #177: in `decode_cursor_pixels` (`cursor.rs:522`), size `rgba`
  with `limits::rgba_len`, and move the pixel-data length checks
  (around `:570`, `:591` and `:612` per the issue, so re-locate
  them) ahead of the allocation. The test is a 65535 x 65535 cursor
  header with four bytes of pixel data.
- #173: in the Pixmap arm (`display.rs:2116-2190`), refuse unless
  `width * 4 <= stride`, next to the existing `needed_bytes` check
  at `:2159`. Use `checked_mul`, because `width` is from the wire.
  Follow the arm's existing `warn_once!` pattern. The test is the
  issue's case: `width = 1_000_000`, `height = 1`, `stride = 4`,
  with four bytes of data.
- #174: Step 1 made the mismatch unconstructible. Here, give the
  FromCache arm a `warn_once!` when the cached buffer does not fit
  the descriptor's dimensions, matching the cache-miss warning
  beside it. The test is the issue's two-message sequence: cache a
  2 x 2 pixmap as id 42, then draw FromCache id 42 at 10000 x 10000.
  Drive it through the same path the display channel's existing
  draw tests use.
- #182: give `DisplaySurface::blit` (`surface.rs:66`) the same
  `left >= self.width || top >= self.height` early return as
  `blit_chroma` and `blit_alpha`, and compute `copy_width` with
  saturating arithmetic. At `left == width` the old code
  copied nothing but marked the surface dirty; the debug underflow
  needs `left > width`, or a `left` large enough to overflow
  `left * 4`. The test uses such a `left`.

**Brief 5: documentation (one commit).**

- `docs/spice-protocol.md`: after "All decompressors output RGBA
  pixels" (around line 297), add a short "Decode limits" paragraph
  saying that every decoder refuses images over the shared caps,
  why, and that a refused image is dropped and not painted, in the
  same way as the truncated-LZ4 paragraph above it. Name the
  constants. Do not quote their values, so the prose cannot go
  stale.
- `shakenfist-spice-compression/README.md`: mention the
  `DecompressedImage` constructor change and the `limits` module,
  if the README documents the API.
- `AGENTS.md` item 5, which covers untrusted wire input, gains one
  sentence pointing at `limits::rgba_len`. This is a convention
  change, so it belongs there.

### Phase 2: Push audit

Run `PUSH-AUDIT.md` over Phase 1's merge commit, once it is known.
Findings land as their own pull request, and that pull request
records Phase 1's `Merged` cell and closes this plan out. If the
audit finds nothing, this phase sets the plan `Complete` in its own
pull request.

## Administration and logistics

### Success criteria

* All eight issues are closed by the Phase 1 pull request body.
* `grep -rn 'vec!\[0u8' shakenfist-spice-compression/src
  shakenfist-spice-renderer/src` shows no non-test allocation sized
  from wire dimensions without going through `rgba_len`.
* Each fix has a regression test that fails against `develop` at
  78c7238. Exceptions: #177's 65535x65535 short-data test does not
  fail on a host with lazy overcommit (the 16 GiB zeroed
  allocation is never touched), so its regression proof is the
  over-cap test instead; #172's and #175's oversized tests are
  proven by reasoning, not by running the old code, which would
  attempt multi-GiB allocations.
* `pre-commit run --all-files` and `make test` pass on every commit.
* The style guide, `docs/spice-protocol.md` and `AGENTS.md` describe
  the shared limit, and no prose quotes its value.

### Future work

* `decode_image_and_emit` (`display.rs`, the `no_image_data` guard
  `if image_data_start >= payload.len()`) refuses an image whose
  descriptor ends exactly at the end of the payload. A FromCache
  `SpiceImage` has no bytes after its descriptor, so if it is the
  last pointed-to data in a DRAW_COPY (no mask), every such draw
  would be dropped. Found during Step 4 and tracked as #442; fixed
  by #457, which merged into this branch.
* Fuzz targets for the compression crate's decoders (#135). The
  helper makes "never allocates past the cap" a property a fuzzer
  can assert.
* Per-channel message size caps (#436).

### Bugs fixed during this work

Issues #171, #172, #173, #174, #175, #176, #177 and #182, plus #442
(found here, fixed by #457, which merged into this branch).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
