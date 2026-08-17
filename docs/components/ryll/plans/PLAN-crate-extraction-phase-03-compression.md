# Phase 3: Extract shakenfist-spice-compression

## Prompt

Before executing any step of this plan, back brief the operator
as to your understanding of the plan and how the work you intend
to do aligns with the master plan
([PLAN-crate-extraction.md](/components/ryll/plans/PLAN-crate-extraction/)) and
Decisions #1, #2, #5, #6, and #7 it records.

This phase is the first piece of Phase-2+ work where we actually
move source code between crates. The placeholder
`shakenfist-spice-compression` crate from Phase 2 gets replaced
with real decompression code. The invariant to preserve is that
all 99 existing ryll tests continue to pass, and ryll's
end-to-end SPICE client behaviour is bit-for-bit unchanged.

Per Decision #6, the crate is named "compression" but the
initial code it contains is **decompression-only**, matching
ryll's current scope. The broader crate name reserves
namespace for future compression implementations of the same
codecs (which a SPICE proxy will eventually want) without a
crate rename. This phase does not add any compression code.

I prefer one commit per logical change. Each commit must leave
the tree in a working state: `cargo build`, `cargo test
--workspace`, `pre-commit run --all-files`, and the CI workflow
must all pass after every commit.

## Situation

After Phases 1 and 2, the repository is a four-member Cargo
workspace:

```
ryll-repo/
├── Cargo.toml                         # workspace manifest
├── ryll/                              # the application
│   ├── Cargo.toml
│   └── src/
│       ├── channels/display.rs        # consumes decompression
│       ├── decompression/             # QUIC, GLZ, LZ
│       │   ├── mod.rs                 # DecompressedImage + re-exports
│       │   ├── glz.rs                 # 306 lines
│       │   ├── lz.rs                  # 218 lines
│       │   └── quic.rs                # 1606 lines
│       └── ...
├── shakenfist-spice-compression/      # v0.0.0 placeholder (from Phase 2)
│   ├── Cargo.toml
│   ├── README.md
│   └── src/lib.rs                     # 4-line doc-comment
├── shakenfist-spice-protocol/         # v0.0.0 placeholder
└── shakenfist-spice-usbredir/         # v0.0.0 placeholder
```

**Current state of the decompression code** (from the Phase 3
research pass):

* [ryll/src/decompression/mod.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/mod.rs)
  defines `pub struct DecompressedImage { pub width: u32, pub
  height: u32, pub pixels: Vec<u8>, pub image_id: u64 }` and
  re-exports `decompress_glz`, `decompress_lz`, and
  `quic_decode` from the three sub-modules. Not
  `#[non_exhaustive]`. ~16 lines total.

* [ryll/src/decompression/glz.rs:19-22](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/glz.rs#L19-L22):
  `pub async fn decompress_glz(data: &[u8], previous_images:
  &Arc<Mutex<HashMap<u64, Vec<u8>>>>) -> Result<DecompressedImage>`.
  Cross-channel retry loop with
  `tokio::time::sleep(Duration::from_millis(5)).await` at
  [glz.rs:214-236](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/glz.rs#L214-L236),
  retrying up to 20 times to let another display channel finish
  populating the shared GLZ dictionary. This is the only thing
  forcing the `tokio` dependency on the crate, and per Decision
  #2 we accept it. One unit test at
  [glz.rs:275](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/glz.rs#L275).

* [ryll/src/decompression/lz.rs:21](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/lz.rs#L21):
  `pub fn decompress_lz(data: &[u8]) -> Result<DecompressedImage>`.
  Pure sync. One unit test at
  [lz.rs:189](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/lz.rs#L189).

* [ryll/src/decompression/quic.rs:1539](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/quic.rs#L1539):
  `pub fn quic_decode(data: &[u8], width: u32, height: u32) ->
  Option<Vec<u8>>`. Note the **asymmetric return type**:
  returns `Option<Vec<u8>>` rather than
  `Result<DecompressedImage>`. The caller at
  [display.rs:921](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L921)
  wraps the raw `Vec<u8>` into a `DecompressedImage` using the
  `img_desc` metadata. No unit tests. The file starts with
  `#![allow(dead_code, clippy::needless_range_loop)]` at
  [quic.rs:1](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/quic.rs#L1),
  suggesting latent cleanup work.

* [ryll/src/channels/display.rs:30](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L30):
  `fn decompress_spice_lz4(data: &[u8], width: usize, height:
  usize) -> Option<DecompressedImage>`. **Private** to
  `display.rs` (not `pub`). This is the fourth decoder that
  currently lives outside the `decompression/` module for
  historical reasons. ~115 lines. Internally calls
  `lz4_flex::decompress` at line 90. Returns
  `Option<DecompressedImage>` — a third asymmetric return type.
  Single caller at
  [display.rs:863](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L863).

**Decompression dispatch in display.rs** (summary of the
research findings):

| `ImageType` | Handler | Decoder call |
|---|---|---|
| `GlzRgb` | `decompress_glz(&image_data[4..], &self.glz_dictionary).await` | [display.rs:790](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L790) |
| `LzRgb` | `decompress_lz(&image_data[4..])` | [display.rs:805](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L805) |
| `ZlibGlzRgb` | zlib then `decompress_glz` | [display.rs:838](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L838) |
| `Lz4` | `decompress_spice_lz4(image_data, width, height)` | [display.rs:863](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L863) |
| `Quic` | `quic_decode(quic_data, img_desc.width, img_desc.height)` | [display.rs:921](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L921) |
| `Pixmap`, `FromCache`, `Jpeg` | (non-decompression paths) | — |

**Import from decompression in display.rs** is exactly one
line at
[display.rs:13](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L13):

```rust
use crate::decompression::{decompress_glz, decompress_lz, quic_decode, DecompressedImage};
```

**External dependencies** each algorithm uses (all already
present in ryll's `Cargo.toml`):

| Algorithm | Deps |
|---|---|
| QUIC | `tracing` |
| GLZ | `anyhow`, `byteorder`, `tokio` (sync + time features) |
| LZ | `anyhow`, `byteorder` |
| LZ4 | `anyhow`, `tracing`, `lz4_flex` |

**Coupling to ryll internals**: **none**, except for LZ4's
current location inside `channels/display.rs`. The three
existing `decompression/*.rs` files import only from
`super::DecompressedImage`, `std::*`, and their external deps.
No `crate::config::*`, no logging helpers, no mock channels.

## Mission and problem statement

Replace the Phase 2 `shakenfist-spice-compression v0.0.0`
placeholder with a real crate containing the four SPICE image
decompression algorithms (QUIC, GLZ, LZ, LZ4), feature-gated
per Decision #6, and switch ryll to consume the extracted
crate via its workspace path. After this phase completes:

- The `shakenfist-spice-compression` crate exists at
  `shakenfist-spice-compression/` as a real (not placeholder)
  workspace member, with `DecompressedImage`, `decompress_glz`,
  `decompress_lz`, `quic_decode`, and `decompress_spice_lz4`
  as its public API, each gated behind a cargo feature
  (`glz`, `lz`, `quic`, `lz4` respectively).
- Ryll's [channels/display.rs](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs)
  no longer has a local `decompress_spice_lz4` function, and
  its decompression imports come from
  `shakenfist_spice_compression::*` instead of
  `crate::decompression::*`.
- `ryll/src/decompression/` is deleted; `ryll/src/lib.rs`'s
  mod declaration for it is removed.
- All 99 existing ryll tests continue to pass.
- The two existing unit tests in the decompression module
  (`test_glz_header_parse`, `test_lz_header_parse`) are moved
  to the new crate and continue to pass there.
- `cargo test --workspace` now exercises both ryll and the
  new compression crate.
- The crate is still at version `0.0.0` on its local
  `Cargo.toml` — **no `0.1.0` publish happens in this phase**.
  Publishing the real crate to crates.io is future work per
  the master plan's Future Work section, and requires
  additional polish (README rewrite, categories, keywords,
  dependency review, possibly the return-type normalisation
  flagged in Open Question #1 below).

There must be no behavioural changes to ryll. Every SPICE
message that currently renders correctly must continue to
render correctly; every pixel must be bit-identical. This is
a pure refactor.

## Approach

The phase splits into two discrete commits, each independently
buildable, testable, and CI-green:

1. **Prerequisite commit** (move LZ4 within ryll, no
   cross-crate movement). Extract `decompress_spice_lz4` from
   `ryll/src/channels/display.rs` into a new
   `ryll/src/decompression/lz4.rs` file. Make it `pub`.
   Update `ryll/src/decompression/mod.rs` to declare the new
   module and re-export the function. Update `display.rs` to
   import it from `crate::decompression::decompress_spice_lz4`
   instead of calling the local function. This commit
   establishes `lz4` as a peer of `glz`, `lz`, and `quic`
   inside the existing decompression module, so that the
   subsequent extraction commit can move all four decoders
   as a single symmetric group. No behavioural change; ~115
   lines of code move one file to the left.

2. **Extraction commit** (the real work). Replace the
   `shakenfist-spice-compression/` placeholder contents with
   the real crate implementation:
   - Rewrite `shakenfist-spice-compression/Cargo.toml` with
     dependencies (`anyhow`, `byteorder`, `tracing`, optional
     `tokio`, optional `lz4_flex`) and feature flags (`glz`,
     `lz`, `quic`, `lz4` all enabled by default).
   - Rewrite `shakenfist-spice-compression/src/lib.rs` to
     declare the four feature-gated modules and re-export the
     public items. Move `DecompressedImage` definition into
     `lib.rs` (where it currently lives in ryll's `mod.rs`).
   - Move `ryll/src/decompression/{glz,lz,quic,lz4}.rs` into
     `shakenfist-spice-compression/src/` via `git mv`.
   - Update each moved file's `use super::DecompressedImage`
     to `use crate::DecompressedImage` (or similar) to match
     the new crate-relative paths.
   - Update ryll's
     [channels/display.rs:13](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L13)
     import from `crate::decompression::...` to
     `shakenfist_spice_compression::...` (add the missing
     `decompress_spice_lz4` to the import list since it
     moved).
   - Delete `ryll/src/decompression/` entirely (after the
     `git mv`s, only `mod.rs` should remain — delete it).
   - Remove the `pub mod decompression;` declaration from
     `ryll/src/lib.rs` (or wherever it currently lives — the
     research did not explicitly identify which file carries
     the `mod decompression;` declaration, so it may also be
     in `main.rs` if ryll is binary-only).
   - Add `shakenfist-spice-compression = { path =
     "../shakenfist-spice-compression" }` to ryll's
     `[dependencies]` in `ryll/Cargo.toml`.
   - Update the README for the extracted crate to describe it
     as the real crate (not a placeholder), but keep the
     `version = "0.0.0"` and keep the "not published" status.
     Publishing `0.1.0` is future work.

The README update in the extraction commit is debatable: we
could also leave the placeholder README in place until the
future-work polish phase. I prefer to update it now so the
README accurately reflects what the crate contains at every
commit, even if the crate isn't yet published.

## Pre-flight verification

Before starting Commit 1, Claude verifies:

1. **Working tree is clean** on `display-iteration`, and
   Phase 2 is fully committed
   (`git log --oneline -15` should show `a74d14a` near the
   top and `42c8e8a` among the recent commits).
2. **CI is green** on the most recent pushed commit
   (`ci-status shakenfist/ryll runs --branch display-iteration
   --limit 3`). If CI has not been run on Phase 1/2 yet
   because nothing has been pushed, note this and consider
   whether to push before proceeding — stacking Phase 3 on
   top of 13 unverified commits is riskier than pushing first.
3. **All 99 ryll tests pass currently** via
   `make test` or a direct
   `docker run ... cargo test --workspace`. This is the
   baseline we must not regress.
4. **`cargo clippy --workspace --all-targets -- -D warnings`
   passes**. If new lints have surfaced since Phase 2 for any
   reason, fix them in a separate commit before starting
   Phase 3.
5. **The decompression module structure matches the research
   findings above**: `ls ryll/src/decompression/` shows
   exactly `mod.rs`, `glz.rs`, `lz.rs`, `quic.rs` (no stray
   files). `grep -c '#\[test\]' ryll/src/decompression/*.rs`
   shows 1 test in `glz.rs`, 1 test in `lz.rs`, 0 in
   `quic.rs`, 0 in `mod.rs`. If these counts differ, stop and
   re-plan.
6. **The `decompress_spice_lz4` function in display.rs is
   still private** and has a single caller. If it has been
   made public or picked up additional callers since the
   research pass, the prerequisite commit needs re-planning.

If any of these checks fail, stop and re-plan rather than
improvising.

## Crate layout

### `shakenfist-spice-compression/Cargo.toml`

```toml
[package]
name = "shakenfist-spice-compression"
version = "0.0.0"
edition.workspace = true
license.workspace = true
authors.workspace = true
repository.workspace = true
description = "Pure-Rust SPICE image decompression algorithms (QUIC, GLZ, LZ, LZ4), extracted from the ryll SPICE client. Reserved crate name; the first published release will follow as 0.1.0 once the API has stabilised."
readme = "README.md"

[features]
default = ["quic", "glz", "lz", "lz4"]
quic = []
glz = ["dep:tokio"]
lz = []
lz4 = ["dep:lz4_flex"]

[dependencies]
anyhow = "1"
byteorder = "1"
tracing = "0.1"
tokio = { version = "1", features = ["sync", "time"], optional = true }
lz4_flex = { version = "0.11", optional = true }
```

Notes:

- The `version` stays at `0.0.0`. **This phase does not
  publish.** Bumping to `0.1.0` is the future-work polish
  task.
- `anyhow`, `byteorder`, and `tracing` are unconditional
  because `quic.rs` uses `tracing` and the other modules use
  `anyhow` + `byteorder`; they are all cheap. The feature
  flags gate the *modules*, not their transitive dependencies.
  Even a `default-features = false, features = ["quic"]`
  consumer still pulls `anyhow` and `byteorder`, which is
  acceptable given how small those crates are.
- The `glz` feature gates the `tokio` dependency via
  `dep:tokio`. A consumer who doesn't need GLZ pays nothing
  for tokio.
- The `lz4` feature gates `lz4_flex` similarly.
- No `[dev-dependencies]`. The existing unit tests
  (`test_glz_header_parse`, `test_lz_header_parse`) don't
  need any.

### `shakenfist-spice-compression/src/lib.rs`

```rust
//! Pure-Rust implementations of the SPICE image-stream
//! decompression algorithms: QUIC (the SPICE wavelet
//! /arithmetic codec, not the QUIC transport protocol), GLZ
//! (dictionary-based cross-frame LZ), LZ (single-frame LZ),
//! and LZ4.
//!
//! Each algorithm is gated behind a cargo feature (`quic`,
//! `glz`, `lz`, `lz4`) with all four enabled by default.
//! Consumers who only need a subset can disable default
//! features and opt in to the ones they need.
//!
//! All four decoders return a [`DecompressedImage`] on
//! success. The struct is marked `#[non_exhaustive]` to
//! allow adding metadata fields in future minor releases
//! without breaking consumers.
//!
//! Extracted from the
//! [ryll](https://github.com/shakenfist/ryll) SPICE client.

#[cfg(feature = "glz")]
pub mod glz;

#[cfg(feature = "lz")]
pub mod lz;

#[cfg(feature = "quic")]
pub mod quic;

#[cfg(feature = "lz4")]
pub mod lz4;

#[cfg(feature = "glz")]
pub use glz::decompress_glz;

#[cfg(feature = "lz")]
pub use lz::decompress_lz;

#[cfg(feature = "quic")]
pub use quic::quic_decode;

#[cfg(feature = "lz4")]
pub use lz4::decompress_spice_lz4;

/// A decompressed SPICE image: raw RGBA pixels plus their
/// dimensions and an image id used for cross-frame GLZ
/// dictionary lookup.
#[derive(Debug)]
#[non_exhaustive]
pub struct DecompressedImage {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
    pub image_id: u64,
}

impl DecompressedImage {
    /// Construct a new [`DecompressedImage`] with no
    /// metadata beyond the four core fields. Exists because
    /// the struct is `#[non_exhaustive]`, which prevents
    /// external crates from using struct literal syntax.
    pub fn new(width: u32, height: u32, pixels: Vec<u8>, image_id: u64) -> Self {
        Self {
            width,
            height,
            pixels,
            image_id,
        }
    }
}
```

A constructor is needed because `#[non_exhaustive]` prevents
external crates from using struct literal syntax. Without the
constructor, `ryll/src/channels/display.rs` would not compile
after the extraction — it currently builds `DecompressedImage`
via struct literal syntax (confirmed at
[display.rs:921-927](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L921-L927)
in the QUIC wrapping code, though this needs rechecking during
execution). The constructor is a minimal, non-breaking add.

### `shakenfist-spice-compression/README.md`

Rewrite the Phase 2 placeholder README to reflect reality at
this point in the extraction. The new README should:

- Explicitly state the crate is still at `0.0.0` and not yet
  published, pointing at the Future Work section of the
  master plan for the publish polish list.
- List the four algorithms and their features.
- Link to `ryll` as the reference consumer / source of the
  code.
- Include a one-paragraph usage example (the GLZ dictionary
  pattern is the least obvious).
- Link to the extraction plan for context.

Full README text is drafted in the execution section below.

### `ryll/Cargo.toml` change

Add the new path-dependency to `[dependencies]`:

```toml
shakenfist-spice-compression = { path = "../shakenfist-spice-compression" }
```

This is a pure path-dependency. Per Decision #1 in the master
plan, the published form would use `{ path = "...", version =
"0.1" }` (the dual-spec idiom) so that ryll's own future
publish carries a semver requirement. Since Phase 3 does not
publish anything, the `version` qualifier can be omitted and
added later when ryll is being prepared for its real first
crates.io release. Alternatively, add the `version = "0.1"`
qualifier now as forward preparation — since the extracted
crate will be `0.1.0` when it publishes, the qualifier is
accurate for the future state even if it has no effect
today. **Decision: add the version qualifier now**, because it
costs nothing and documents intent. The line in
`ryll/Cargo.toml` will read:

```toml
shakenfist-spice-compression = { path = "../shakenfist-spice-compression", version = "0.1" }
```

Which means that while 0.0.0 is what cargo resolves today (via
`path`), any publish of ryll would expect `shakenfist-spice-
compression` to be on crates.io as `0.1.x`. Ryll is also not
yet published on crates.io (Decision #7's future-work item),
so the `version` qualifier is currently documentary only.

### `ryll/src/channels/display.rs` import update

Replace the existing line 13:

```rust
use crate::decompression::{decompress_glz, decompress_lz, quic_decode, DecompressedImage};
```

With:

```rust
use shakenfist_spice_compression::{
    decompress_glz, decompress_lz, decompress_spice_lz4, quic_decode, DecompressedImage,
};
```

Note the addition of `decompress_spice_lz4` which moved out of
`display.rs` in the prerequisite commit and is now imported
from the crate. The local `fn decompress_spice_lz4` definition
is gone (deleted in the prerequisite commit), so the import is
the only change to the dispatch logic.

### `ryll/src/` module changes

After the prerequisite commit, `ryll/src/decompression/`
contains `mod.rs`, `glz.rs`, `lz.rs`, `quic.rs`, `lz4.rs`.
After the extraction commit, this entire directory is gone.

The `mod decompression;` declaration also needs to be removed
from wherever it currently lives in ryll (likely `main.rs` or
`lib.rs` — confirm during execution).

## Execution

Each step below is one commit.

### Step 1: Move LZ4 into the decompression module

Prerequisite commit. No cross-crate movement; pure
reorganisation within ryll. The goal is to establish `lz4` as
a peer of `glz`, `lz`, and `quic` inside
`ryll/src/decompression/`, so the subsequent extraction commit
can move all four decoders as a single symmetric group.

Concrete steps within the commit:

1. Create `ryll/src/decompression/lz4.rs` containing the
   current `decompress_spice_lz4` function from
   `ryll/src/channels/display.rs`. Change the visibility
   from `fn` to `pub fn`. The function takes `data: &[u8],
   width: usize, height: usize` and returns
   `Option<DecompressedImage>`; keep these signatures
   unchanged. Also move its inline `tracing` calls and its
   use of `lz4_flex::decompress` — these become module-level
   imports in the new file.
2. Add `use super::DecompressedImage;` at the top of the new
   `lz4.rs` so it can refer to the shared struct.
3. Update `ryll/src/decompression/mod.rs`:
   - Add `pub mod lz4;` alongside the existing `pub mod {glz,
     lz, quic};` declarations.
   - Add `pub use lz4::decompress_spice_lz4;` alongside the
     existing re-exports.
4. Update `ryll/src/channels/display.rs`:
   - Remove the `fn decompress_spice_lz4(...) -> ...`
     definition entirely (currently at
     [display.rs:30-144](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L30-L144)
     or thereabouts).
   - Add `decompress_spice_lz4` to the import list at line 13:
     ```rust
     use crate::decompression::{
         decompress_glz, decompress_lz, decompress_spice_lz4, quic_decode, DecompressedImage,
     };
     ```
   - Remove any imports that are now unused in `display.rs`
     (specifically `lz4_flex::*` if it was imported at module
     level). `tracing` stays because `display.rs` uses it for
     other logging.
5. Verify locally (all inside the devcontainer):
   - `cargo fmt --all --check`
   - `cargo clippy --workspace --all-targets -- -D warnings`
   - `cargo build --workspace`
   - `cargo test --workspace` — all 99 ryll tests plus 2
     decompression tests (GLZ + LZ) pass.
   - `cargo build --release -p ryll` produces a binary that
     still handles `ImageType::Lz4` correctly. No runtime
     verification is possible without a real SPICE server,
     so the test suite is our safety net.
   - `pre-commit run --all-files`
6. Commit.

**Commit message (subject ≤ 50 chars):**
> Move LZ4 decoder into decompression module.

The commit is ~115 lines of code moving one file to the left
(from `channels/display.rs` to `decompression/lz4.rs`), plus
~3 lines of module-declaration additions in `mod.rs`, plus
~2 lines of import updates in `display.rs`.

### Step 2: Extract shakenfist-spice-compression from ryll

The real work. This commit is larger than Step 1 because it
touches all four decoder files, the placeholder crate, ryll's
`Cargo.toml`, ryll's `channels/display.rs`, and deletes
`ryll/src/decompression/`. It must all happen in one commit
because the intermediate states are not buildable.

Concrete steps within the commit:

1. **Delete the Phase 2 placeholder contents**:
   - Delete `shakenfist-spice-compression/src/lib.rs` (the
     4-line placeholder).
   - Delete `shakenfist-spice-compression/README.md` (the
     placeholder README) — will be rewritten.
   - Keep `shakenfist-spice-compression/Cargo.toml` — will be
     rewritten in-place rather than deleted.

2. **Move the four decoder files** via `git mv` to preserve
   rename history:
   ```sh
   git mv ryll/src/decompression/glz.rs   shakenfist-spice-compression/src/glz.rs
   git mv ryll/src/decompression/lz.rs    shakenfist-spice-compression/src/lz.rs
   git mv ryll/src/decompression/quic.rs  shakenfist-spice-compression/src/quic.rs
   git mv ryll/src/decompression/lz4.rs   shakenfist-spice-compression/src/lz4.rs
   ```

3. **Delete** `ryll/src/decompression/mod.rs`. The
   `DecompressedImage` struct it currently holds will be
   re-defined in the new crate's `lib.rs`.

4. **Delete the now-empty** `ryll/src/decompression/`
   directory (git will do this automatically when the four
   files have moved and `mod.rs` is deleted; no explicit step
   needed).

5. **Write the new `shakenfist-spice-compression/src/lib.rs`**
   from the template in the "Crate layout" section above.
   Note: `DecompressedImage` is now defined in `lib.rs` with
   `#[non_exhaustive]` and a `new()` constructor.

6. **Write the new `shakenfist-spice-compression/Cargo.toml`**
   from the template in the "Crate layout" section above.

7. **Write the new `shakenfist-spice-compression/README.md`**
   following the text below:

   ```markdown
   # shakenfist-spice-compression

   Pure-Rust implementations of the SPICE image-stream
   decompression algorithms:

   - **QUIC** — the SPICE wavelet/arithmetic codec (not the
     QUIC transport protocol). Feature `quic` (default).
   - **GLZ** — dictionary-based cross-frame LZ with a shared
     previous-images dictionary. Async because of a
     cross-channel retry loop. Feature `glz` (default), pulls
     in `tokio`.
   - **LZ** — single-frame LZ. Feature `lz` (default).
   - **LZ4** — SPICE's per-row LZ4 image format (each row is
     independently compressed with `lz4_flex` and a 4-byte
     length prefix). Feature `lz4` (default), pulls in
     `lz4_flex`.

   All four decoders return a `DecompressedImage { width,
   height, pixels: Vec<u8>, image_id }` on success. The
   struct is `#[non_exhaustive]`; construct via
   `DecompressedImage::new(...)`.

   ## Status

   This crate is not yet published to crates.io. The
   `0.0.0` entry there is a Phase 2 name reservation; the
   real `0.1.0` release will follow once API polish is
   complete (see the
   [extraction plan](https://github.com/shakenfist/ryll/blob/develop/docs/plans/PLAN-crate-extraction.md)
   for what that involves).

   Internal consumers (ryll itself and the planned Rust
   rewrite of the shakenfist kerbside SPICE proxy) should
   depend on this crate via a workspace path or a git
   dependency until `0.1.0` ships.

   ## Usage

   ```rust,ignore
   use shakenfist_spice_compression::{decompress_glz, DecompressedImage};
   use std::collections::HashMap;
   use std::sync::{Arc, Mutex};

   // Shared GLZ dictionary across all display channels.
   let dict: Arc<Mutex<HashMap<u64, Vec<u8>>>> =
       Arc::new(Mutex::new(HashMap::new()));

   // Decompress a GLZ image from wire bytes.
   let image: DecompressedImage =
       decompress_glz(glz_bytes, &dict).await?;
   # Ok::<(), anyhow::Error>(())
   ```

   ## Source

   Extracted from the
   [ryll](https://github.com/shakenfist/ryll) SPICE client.
   ```

8. **Update each moved file's `DecompressedImage` reference**:
   - `glz.rs`: change `use super::DecompressedImage;` to
     `use crate::DecompressedImage;`.
   - `lz.rs`: same.
   - `quic.rs`: `quic.rs` doesn't currently use
     `DecompressedImage` directly (it returns `Option<Vec<u8>>`
     — the caller wraps). No change needed in `quic.rs`
     itself, but the caller wrapping code in `ryll/src/
     channels/display.rs` will need to switch to
     `DecompressedImage::new(...)` because of the new
     `#[non_exhaustive]` attribute.
   - `lz4.rs`: change `use super::DecompressedImage;` to
     `use crate::DecompressedImage;`.

9. **Update the two unit tests** in `glz.rs` and `lz.rs` to
   still reference `DecompressedImage` via the crate root
   (should be automatic if step 8 is done correctly — the
   tests are inside a `#[cfg(test)] mod tests` inside the
   module file, so they see `super::*` which still resolves
   correctly).

10. **Audit `quic.rs`'s top-level attributes**. The existing
    file has `#![allow(dead_code, clippy::needless_range_loop)]`
    at [quic.rs:1](https://github.com/shakenfist/ryll/blob/develop/ryll/src/decompression/quic.rs#L1).
    When the file becomes part of a public crate, it's worth
    asking whether this allow is still appropriate. Options:
    - Keep it as-is (minimal change, can be revisited in
      future polish).
    - Drop `dead_code` and fix whatever clippy complains
      about (may be non-trivial if there really is dead code).
    - Drop both and see what breaks.
    **Decision for this phase: keep it as-is.** Touching
    `quic.rs` internals is out of scope; the
    `#![allow]` travels with the file to the new crate
    unchanged. If future polish wants to clean it up, that's
    a separate commit on the path to `0.1.0`.

11. **Update `ryll/Cargo.toml`** to add the new dependency:
    ```toml
    shakenfist-spice-compression = { path = "../shakenfist-spice-compression", version = "0.1" }
    ```
    The exact position in the `[dependencies]` block is
    stylistic; alphabetical ordering would place it after
    `serde_json` and before `socket2`, but the existing
    file groups deps by purpose rather than alphabetically
    (e.g. "GUI framework" comments). Place it in its own
    group with a comment like `# Extracted SPICE codecs`.

12. **Update `ryll/src/channels/display.rs`**:
    - Replace the `use crate::decompression::{...}` line with
      `use shakenfist_spice_compression::{...}` as described
      in the "Crate layout" section above.
    - Find the QUIC handler wrapping code (around
      [display.rs:921](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L921))
      that wraps the `Option<Vec<u8>>` from `quic_decode` into
      a `DecompressedImage`. This currently uses struct literal
      syntax like `Some(DecompressedImage { width: ..., ... })`.
      Switch to `DecompressedImage::new(...)` because of the
      new `#[non_exhaustive]` attribute. **This is the most
      likely source of a compile break during the extraction
      commit — verify carefully.**
    - Check the LZ4 handler at
      [display.rs:863](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/display.rs#L863)
      — it shouldn't need changes because
      `decompress_spice_lz4` already returns
      `Option<DecompressedImage>` which the dispatch code
      matches on. But double-check.
    - Check whether any other code in `display.rs` constructs
      `DecompressedImage` directly via struct literal; if so,
      update those too.

13. **Remove the `mod decompression;` declaration** from
    wherever in `ryll/src/` it currently lives. The research
    pass didn't identify the declaration's exact location —
    during execution, `grep -rn 'mod decompression' ryll/src/`
    will find it.

14. **Verify locally** (same suite as Step 1, plus some new
    checks):
    - `cargo fmt --all --check`
    - `cargo clippy --workspace --all-targets -- -D warnings`
    - `cargo build --workspace`
    - `cargo test --workspace` — all 99 ryll tests must still
      pass; the 2 decompression unit tests must now appear
      under `shakenfist_spice_compression` in the test
      output, not under `ryll::decompression`.
    - `cargo build --release -p ryll` produces a working
      binary.
    - `cargo build -p shakenfist-spice-compression` (with
      default features) succeeds.
    - `cargo build -p shakenfist-spice-compression
      --no-default-features --features quic` succeeds (tests
      the feature-gating).
    - `cargo build -p shakenfist-spice-compression
      --no-default-features --features lz` succeeds.
    - `cargo build -p shakenfist-spice-compression
      --no-default-features --features glz` succeeds and
      pulls in tokio.
    - `cargo build -p shakenfist-spice-compression
      --no-default-features --features lz4` succeeds and
      pulls in lz4_flex.
    - `cargo build -p shakenfist-spice-compression
      --no-default-features` succeeds with no features —
      verifying that `lib.rs` compiles even when none of the
      modules are included.
    - `cargo deb --no-build -p ryll` and
      `cargo generate-rpm -p ryll` still produce the same
      artefacts.
    - `pre-commit run --all-files`

15. **Inspect git diff stats** before committing. Expected
    shape:
    - 4 file renames from `ryll/src/decompression/*.rs` to
      `shakenfist-spice-compression/src/*.rs` (100%
      similarity except for the `super::DecompressedImage`
      imports in 3 of them).
    - `ryll/src/decompression/mod.rs` deleted.
    - `shakenfist-spice-compression/src/lib.rs` rewritten
      (was a 4-line placeholder, now ~40 lines).
    - `shakenfist-spice-compression/Cargo.toml` rewritten
      (was minimal placeholder, now has features and deps).
    - `shakenfist-spice-compression/README.md` rewritten.
    - `ryll/Cargo.toml` modified (+1 dep line).
    - `ryll/src/channels/display.rs` modified (1 import line,
      ~6 lines for the `DecompressedImage::new` conversion).
    - Wherever `mod decompression;` lives: that line
      removed (-1 line).
    - `Cargo.lock` updated with the new dependency edge.
    If the diff includes anything outside that list, stop and
    investigate before committing.

16. Commit.

**Commit message (subject ≤ 50 chars):**
> Extract shakenfist-spice-compression crate.

## Open questions

1. **Should `quic_decode` be normalised to return
   `Result<DecompressedImage>` during extraction?** Currently
   it returns `Option<Vec<u8>>`, which is asymmetric with
   `decompress_glz`/`decompress_lz` (which return
   `Result<DecompressedImage>`) and
   `decompress_spice_lz4` (which returns
   `Option<DecompressedImage>`). A public crate should have
   symmetric return types across its decoder functions.

   **Decision for this phase: no, leave them as-is.**
   Normalising `quic_decode` means constructing a
   `DecompressedImage` inside the decoder, which requires
   threading an `image_id` parameter through (because
   `DecompressedImage::image_id` is currently populated by
   the caller from the SPICE message, not by the decoder
   from the compressed bytes). That's a real API change and
   a small amount of logic movement, and it's exactly the
   kind of API polish that belongs in the eventual `0.1.0`
   release work, not in the extraction refactor. Document
   this as a `0.1.0` polish item in the crate README's
   "Status" section. Flag as issue for future cleanup.

2. **Should `decompress_spice_lz4` be renamed to just
   `decompress_lz4`?** The `_spice_` infix is a legacy of
   the function living in `display.rs` where it needed a
   distinctive name; in the new crate where there's only one
   LZ4 decoder, the infix is noise. Renaming it requires
   updating the one caller in
   `ryll/src/channels/display.rs`.

   **Decision for this phase: keep `decompress_spice_lz4`.**
   The rename is pure cosmetic, and mixing it into the
   extraction commit obscures the structural change. This
   can happen later as a one-line polish commit in the
   `0.1.0` preparation phase.

3. **Should `DecompressedImage` really be
   `#[non_exhaustive]`?** This forces external consumers to
   use `DecompressedImage::new(...)` instead of struct
   literal syntax, which is slightly more verbose. But it
   allows adding fields later (e.g. a `format: PixelFormat`
   enum for when compression is added) without breaking
   consumers.

   **Decision for this phase: yes, use `#[non_exhaustive]`
   and provide `new()`.** The small ergonomic cost is
   outweighed by the forward-compatibility win for a public
   API. Internal consumers (only ryll's `display.rs` today)
   are easy to update.

4. **Where does `mod decompression;` live in ryll?** Not
   identified by the research pass. Likely candidates:
   `ryll/src/main.rs`, `ryll/src/lib.rs`, or (less likely)
   `ryll/src/app.rs`. During execution, `grep -rn 'mod
   decompression' ryll/src/` will find it in seconds. If
   it's in `main.rs` that's fine; if it's in a `lib.rs`
   that tells us ryll has a library crate as well as a
   binary, which may have other implications.

5. **Should this phase push to `develop`?** The master plan
   says each phase should produce CI-green commits. As of
   the start of Phase 3, 13 commits from Phases 1-2 are
   still unpushed. Running Phase 3 on top of 13 unverified
   commits is riskier than running it after a push-and-CI
   cycle. Recommend pushing and confirming CI before
   starting Phase 3 implementation work. The operator can
   decide.

## Administration and logistics

### Success criteria

We will know Phase 3 has been successfully implemented when:

* `ryll/src/decompression/` no longer exists.
* `shakenfist-spice-compression/` is a real workspace member
  with the four decoder modules feature-gated per Decision
  #6.
* The crate's `Cargo.toml` still has `version = "0.0.0"`.
  No publish has happened.
* The crate's `README.md` describes the real crate, not a
  placeholder.
* `cargo build --workspace`, `cargo test --workspace`, and
  `cargo clippy --workspace --all-targets -- -D warnings`
  all pass.
* All 99 existing ryll tests pass unchanged.
* The 2 existing decompression unit tests (GLZ and LZ header
  parse) pass as tests of
  `shakenfist_spice_compression::{glz, lz}` instead of
  `ryll::decompression::{glz, lz}`.
* Feature-gated builds of the new crate succeed for every
  singleton subset (`--no-default-features --features quic`,
  `... --features glz`, etc.), plus the `--no-default-features`
  empty build.
* Ryll's `channels/display.rs` imports from
  `shakenfist_spice_compression::*` rather than
  `crate::decompression::*`.
* The `mod decompression;` declaration is removed from
  wherever it lives in `ryll/src/`.
* `cargo deb --no-build -p ryll` and
  `cargo generate-rpm -p ryll` still produce working
  artefacts at the same paths.
* CI is green on the branch after Step 2 is pushed.
* `git log --follow` on each of the four decoder files
  (`glz.rs`, `lz.rs`, `quic.rs`, `lz4.rs`) shows an
  unbroken history back to their original locations.

### Future work

* **Publish `shakenfist-spice-compression v0.1.0`** —
  listed in the master plan's Future Work. Involves the API
  polish items flagged in the open questions (normalising
  `quic_decode` return type, renaming
  `decompress_spice_lz4`, cleaning up `quic.rs`'s latent
  `#![allow(dead_code)]`, potentially the kerbside-driven
  compression additions), plus the usual first-publish
  metadata (categories, keywords, homepage, documentation
  URL), plus a more substantial README with example code
  and benchmarks.
* **Compression (encoder) implementations** — the whole
  reason the crate is named "compression" rather than
  "decompression". Adding encoders for QUIC, GLZ, LZ, and
  LZ4 will be driven by the kerbside proxy rewrite's needs.
  New cargo features like `quic-encode`, `glz-encode`, etc.
  can gate the encoder code without affecting the existing
  decoder feature set.
* **Fuzz-testing** the extracted decoders (master plan's
  existing future-work item). QUIC in particular has a
  large decoder state machine and is an ideal fuzz target.
  Now that the decoders are in their own crate with
  well-defined byte-slice inputs, adding `cargo-fuzz`
  targets is straightforward.
* **Consider removing the GLZ retry loop and tokio
  dependency** — Decision #2 accepted the tokio dep, but
  the retry loop is genuinely ryll-specific concurrency
  adaptation rather than part of the GLZ algorithm. The
  cleaner long-term design is for the caller to ensure the
  dictionary is populated before calling `decompress_glz`,
  which would allow dropping the `tokio` dep entirely. This
  is a bigger change than Phase 3 should take on, but worth
  revisiting once the kerbside rewrite informs what the
  other consumers actually want.

### Bugs fixed during this work

(None expected. Phase 3 is purely a refactor — moving code
between crates with no behavioural changes. If a bug is
discovered during execution, record it here and fix it in
a separate commit before Step 2 lands, so the extraction
commit remains a pure refactor.)

### Discoveries during execution

* **Open Question #4 answered.** The `mod decompression;`
  declaration was at [ryll/src/main.rs:22](https://github.com/shakenfist/ryll/blob/develop/ryll/src/main.rs#L22).
  Ryll is binary-only with no `lib.rs`, so the declaration
  lived in `main.rs` rather than a library root. Removed
  cleanly as part of Step 2.

* **The dual-spec idiom does not work when the local crate is
  at 0.0.0 (unanticipated).** The plan recommended adding the
  `version = "0.1"` qualifier to ryll's path-dependency on
  `shakenfist-spice-compression` "as forward preparation",
  expecting it to be a no-op while the local crate stayed at
  `0.0.0`. In practice, cargo refuses to resolve `^0.1`
  against `0.0.0`:

  ```
  error: failed to select a version for the requirement
  `shakenfist-spice-compression = "^0.1"`
  candidate versions found which didn't match: 0.0.0
  ```

  The dual-spec idiom only becomes usable once the local
  version reaches `0.1.0`. The fix in Step 2 was to drop the
  `version` qualifier entirely until the eventual `0.1.0`
  release; the `Cargo.toml` comment explains why. The plan's
  recommendation was wrong on this point — added a note to
  the master plan's Decision #1 in a follow-up commit so
  future phases (Phases 4, 5) don't repeat the same mistake
  for the protocol and usbredir crates.

* **All four moved files registered as proper git renames.**
  Three (`glz.rs`, `lz.rs`, `lz4.rs`) registered at 99%
  similarity because of the one-line `super::DecompressedImage`
  → `crate::DecompressedImage` change. `quic.rs` registered at
  100% because it doesn't reference `DecompressedImage`
  directly (it returns `Option<Vec<u8>>` and the caller
  wraps). `git log --follow` walks back through Phase 3 to
  the original locations cleanly. For `lz4.rs` it walks back
  through Step 1's move from `ryll/src/channels/display.rs`,
  which is exactly the rationale for the prerequisite commit
  structure.

* **`display.rs` had four `DecompressedImage` struct literal
  sites, not one.** The plan flagged the QUIC handler as the
  most likely break point under `#[non_exhaustive]`, but the
  full count was four: the GlzRgb path (line 656), the
  FromCache path (line 749), the JPEG path (line 776), and
  the QUIC path (line 803). All four were updated to
  `DecompressedImage::new(...)` in the same commit. The
  total `display.rs` diff was ~50 lines because of the
  per-site reformatting, larger than the plan's "~6 lines"
  estimate.

* **Rustfmt enforces a strict import-block grouping.** The
  initial placement of the `use shakenfist_spice_compression::*`
  import in `display.rs` (between the `use crate::*` lines
  and the `use crate::protocol::*` lines) tripped rustfmt's
  group-ordering check, which wants external-crate imports
  after all `use crate::*` imports in the same block.
  Re-ordered to put the new import at the end of the block,
  after `use crate::settings;`.

* **`lz4_flex` stays as a direct ryll dependency** even after
  the LZ4 decoder moves to the new crate. Ryll's webdav
  channel ([webdav.rs:482, 585](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/webdav.rs#L482))
  and usbredir channel
  ([usbredir.rs:296](https://github.com/shakenfist/ryll/blob/develop/ryll/src/channels/usbredir.rs#L296))
  still use `lz4_flex` for general data
  compression/decompression (not for SPICE images). The
  Cargo.toml comment was updated to clarify this. The new
  crate has its own optional `lz4_flex` dep gated behind the
  `lz4` feature; cargo dedups the two via the lockfile.

* **All five feature-matrix builds succeed.** Verified
  `--no-default-features` (empty crate, only `tracing`
  pulled in unconditionally), `--features quic`,
  `--features glz` (pulls `tokio`), `--features lz`, and
  `--features lz4` (pulls `lz4_flex`). The
  `--no-default-features` build of `shakenfist-spice-compression`
  with all features off compiles a crate that exports only
  `DecompressedImage` and `DecompressedImage::new` — useful
  for consumers who want the type but provide their own
  decoders.

* **Test count is exactly preserved.** Pre-Phase-3 baseline
  was 99 ryll tests; post-Phase-3 is 97 ryll + 2
  `shakenfist-spice-compression` (the `test_glz_header_parse`
  and `test_lz_header_parse` unit tests that travelled with
  their files). 99 total, no regression. The
  `cargo test --workspace` output shows them under their new
  crate.

### Back brief

Before executing any step of this plan, back brief the
operator as to your understanding of the plan and how the
work you intend to do aligns with the master plan.
Specifically confirm:

1. The two-commit sequence: (Step 1) move LZ4 within ryll
   to establish it as a peer of GLZ/LZ/QUIC, then (Step 2)
   extract all four into the shakenfist-spice-compression
   crate.
2. That Phase 3 does **not** publish anything to crates.io —
   the placeholder stays at `0.0.0`, and a real `0.1.0`
   publish is deferred future work.
3. That the API asymmetry between `quic_decode` (returns
   `Option<Vec<u8>>`), `decompress_spice_lz4` (returns
   `Option<DecompressedImage>`), and the other two (return
   `Result<DecompressedImage>`) is **preserved as-is** in
   this phase, with normalisation deferred to `0.1.0`
   polish.
4. That `DecompressedImage` is being made `#[non_exhaustive]`
   with a `new()` constructor, and that this will force a
   small update to ryll's QUIC wrapping code in
   `display.rs`.
5. The pre-flight checks will be run, and any failure will
   stop execution rather than being worked around.
