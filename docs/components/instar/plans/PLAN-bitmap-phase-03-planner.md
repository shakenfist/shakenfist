# PLAN-bitmap phase 03: the bitmap planner crate

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files and ground your answers in what the code actually
does today — in particular the Phase 1 bitmap primitives in
`src/crates/qcow2/src/bitmap.rs`, the refcount + allocation
mutators in `src/crates/snapshot/src/qcow2.rs`, the directory
serialization pattern in `src/crates/snapshot/src/table.rs`, and
the Phase 2 ABI (`BitmapConfig`/`BitmapResult` in
`src/shared/src/lib.rs`). Do not speculate when you could read.
Where a question touches the qcow2 persistent-dirty-bitmap format
or `qemu-img bitmap` semantics, consult the qcow2 spec and
`block/qcow2-bitmap.c`. Flag any uncertainty explicitly.

Phase plans live in `docs/plans/` as
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phase 1
([parse](/components/instar/plans/PLAN-bitmap-phase-01-parse/)) and Phase 2
([ABI](/components/instar/plans/PLAN-bitmap-phase-02-abi/)) are complete. This is the
third of ten phases and the **hardest** — the correctness core.

I prefer one commit per logical change, minimum one per phase.
Each commit must build, pass tests, and explain what changed and
why.

## Situation

This phase builds `src/crates/bitmap/`, the `no_std` crate that
computes qcow2 bitmap mutations. It is a **pure-logic library
phase**: no guest op (Phase 4), no host CLI (Phase 5). The guest
op will stage on-disk structures into scratch, call this crate's
functions to mutate those buffers, and write them back — so this
crate is I/O-free and testable entirely with in-memory fixtures.

**Convention correction (grounded in the code).** The master plan
and earlier phases loosely called the crate output a "patch list".
The codebase has **no patch model** for this shape of work: the
speculative `SnapshotPatch`/`SnapshotPlan` API was removed in
snapshot's phase 14 (`src/crates/snapshot/src/lib.rs:31-35`), and
`snapshot` + `check` instead use **pure in-place slice mutators** —
functions that take caller-staged big-endian byte slices
(directory bytes, refcount-block bytes, table bytes) plus scalar
geometry and mutate them in place, returning `Result<(),
Error>`/small values. The guest owns all I/O and write ordering.
`bitmap` follows this convention exactly (it is far closer to
`snapshot` than to the plan-emitting `resize`/`amend`).

Grounding (verified against HEAD `9762bbf`):

- **Phase 1 primitives (`src/crates/qcow2/src/bitmap.rs`)** are the
  building blocks: `parse_bitmap_dir_entry` / `serialize_bitmap_dir_
  entry` (24-byte head + name, 8-padded), `BitmapDirEntry` (with
  `is_enabled`/`is_in_use`/`granularity`/`name_bytes`),
  `BitmapTableEntry` + `decode/encode/validate_bitmap_table_entry`,
  `parse_bitmaps_extension`, `default_granularity`,
  `granularity_bits_valid`, `bitmap_bytes_needed`,
  `bitmap_table_size_entries`, and `for_each_bitmap_entry`
  (streaming enumerator). All `no_std`, panic-free.
- **Refcount + allocation reuse (`src/crates/snapshot/src/qcow2.rs`)**,
  reusable as-is (a bitmap-table/data cluster allocates exactly
  like a snapshot L1 cluster):
  - `read_refcount_in_block(block, local_idx, refcount_bits)`
    (`:51`), `set_refcount_in_block(block, local_idx, refcount_bits,
    value)` (`:152`) — **freeing a cluster is `set_..._in_block(.., 0)`;
    there is no `free_cluster` fn**.
  - `check_refcount_after_addend(current, addend, refcount_bits)`
    (`:239`) — overflow/underflow-checked; use for inc/dec.
  - `AllocCursor` (`:283`), `alloc_cluster_in_refblocks` (`:299`),
    `alloc_contiguous_clusters_in_refblocks` (`:344`) — first-fit
    over concatenated staged refblocks. **Constraint:
    `refcount_bits == 16` only** (`:353` returns `Unsupported`
    otherwise). **No refcount-table growth exists anywhere** — the
    allocator returns `RefcountExhausted` when existing refblocks
    are full; `snapshot` handles this by refusing (a v1 contiguity
    gate).
  - `check` already depends on and reuses these
    (`src/crates/check/src/qcow2.rs`), so a new `bitmap` → `snapshot`
    dependency is precedented and clean.
- **Directory serialization template (`src/crates/snapshot/src/table.rs`)**:
  `build_snapshot_table` (`:259`, verbatim-copy old entries +
  8-align gaps + append new entry), `build_snapshot_table_without`
  (`:307`, compaction skipping one index), `snapshot_table_byte_len`
  (`:205`), `snapshot_table_entry_bounds` (`:226`), `round_up_8`
  (`:565`). The bitmap directory (variable-length entries,
  8-aligned) is the structural twin; these are the direct template.
- **Phase 2 ABI (`src/shared/src/lib.rs`)** froze
  `BitmapResult::ERROR_*` (0..=17) and the `ACTION_*` opcodes.
  This crate's `BitmapError` maps onto those wire codes exactly as
  `snapshot::SnapshotError`'s `From<…> for u32`
  (`src/crates/snapshot/src/lib.rs:118-138`) maps onto
  `SnapshotResult::ERROR_*`.
- **Test convention**: inline `#[cfg(test)] mod tests` with
  hand-built `Vec<u8>`/array fixtures (std is available under
  `cfg(test)` in a `no_std` crate). `snapshot` has no `tests/`
  dir and no feature gate — mirror that.

## Mission and problem statement

Create `src/crates/bitmap/` exposing the pure, `no_std`,
panic-free functions the Phase-4 guest op composes to apply the
ordered action list. Concretely:

1. **Crate skeleton + errors.** `src/crates/bitmap/Cargo.toml`
   (`no_std`; deps `shared`, `qcow2 { default-features = false }`,
   `snapshot`); registered in `src/Cargo.toml` workspace members.
   `src/crates/bitmap/src/lib.rs` with `BitmapError` (variants for
   every failure the actions can produce) and `impl
   From<BitmapError> for u32` mapping to `BitmapResult::ERROR_*`.

2. **Directory-level byte helpers** (`directory.rs`), templated on
   `snapshot::table`, reusing Phase 1's entry codec:
   - `find_bitmap(dir, dir_len, nb_bitmaps, name) ->
     Result<Option<FoundBitmap>, BitmapError>` — locate an entry by
     name; `FoundBitmap { index, entry: BitmapDirEntry,
     byte_offset, entry_size }`.
   - `directory_byte_len(dir, nb_bitmaps) -> Result<usize, _>` and
     per-index bounds (mirror `snapshot_table_byte_len` /
     `_entry_bounds`).
   - `build_directory_with_added(old_dir, old_len, nb, &new_entry,
     out) -> Result<usize, _>` — verbatim-copy + append.
   - `build_directory_without(old_dir, old_len, nb, remove_index,
     out) -> Result<usize, _>` — compaction.
   - `build_directory_replacing(old_dir, old_len, nb, index,
     &replacement_entry, out) -> Result<usize, _>` — rewrite one
     entry (used by enable/disable to flip the `auto` flag, and by
     clear to reset a bitmap's table pointer).
   - `serialize_bitmaps_extension(nb_bitmaps, dir_size, dir_offset,
     out) -> Result<usize, _>` — the 24-byte extension-body writer
     (inverse of Phase 1's `parse_bitmaps_extension`).

3. **Per-action logic** (`action.rs`), each a pure function over
   caller-staged slices (existing directory bytes, refcount-block
   bytes) + geometry, reusing `snapshot::qcow2` for
   allocation/refcount and Phase 1 for codecs/geometry. Each
   validates first (so a refusal leaves buffers untouched), then
   mutates. The exact signatures are Open question 2; the semantics:
   - **add** — validate: qcow2 v3 (v2 ⇒ `UnsupportedVersion`), name
     1..=1023 bytes (`NameTooLong`), name not already present
     (`BitmapExists`), `granularity_bits ∈ 9..=31`
     (`GranularityRange`), `nb_bitmaps < 65535` (`TooManyBitmaps`),
     `refcount_bits == 16` (else `UnsupportedRefcountWidth` — see
     OQ4). Compute `bitmap_table_size = bitmap_table_size_entries(
     bitmap_bytes_needed(virtual_size, granularity), cluster_size)`;
     allocate `ceil(bitmap_table_size*8 / cluster_size)` **table
     clusters** (zero-filled — an empty bitmap has an all-zero table
     and **no data clusters**, master OQ8); build the new directory
     entry (`flags = BME_FLAG_AUTO` enabled, `in_use` clear, `type =
     1`, granularity_bits, name); `build_directory_with_added`.
   - **remove** — find (else `BitmapNotFound`); free the bitmap's
     table clusters and any allocated data clusters (refcount → 0
     via `set_refcount_in_block`); `build_directory_without`; caller
     handles the last-bitmap case (extension removal + autoclear
     clear) — expose whether it was the last (`nb_bitmaps` becomes
     0). **remove is the only action allowed on an `in_use`
     bitmap.**
   - **clear** — find (else `BitmapNotFound`); refuse `in_use`
     (`BitmapInUse`); free data clusters, reset table entries to
     all-zeroes (rewrite the table cluster(s) to zero); the
     directory entry stays (same name/granularity/flags).
   - **enable / disable** — find (else `BitmapNotFound`); refuse
     `in_use` (`BitmapInUse`); flip `BME_FLAG_AUTO` in the entry
     via `build_directory_replacing`. No allocation.
   - **merge** — see step 3d / Open question 5.

4. **The bitmap crate does NOT touch the device, does NOT own the
   autoclear dance or write ordering** (Phase 4), and does NOT do
   scratch management. It produces correct bytes in caller buffers
   and reports what the caller must do (clusters freed/allocated,
   whether the extension must be created/removed).

**Out of scope for this phase:** the guest op orchestration,
staging, write-groups, fsync ordering, and the autoclear
crash-safe dance (Phase 4); the host CLI (Phase 5); cross-file
merge (`-b`, master OQ2 — deferred; the ABI has no source-file
field). Refcount-table growth is out of scope everywhere (see OQ4).

## Open questions

### 1. Crate module layout

Proposed: `lib.rs` (crate root, `BitmapError`, `From` impl,
re-exports), `directory.rs` (§2 helpers), `action.rs` (§3 per-action
functions). Merge may warrant its own `merge.rs` (step 3d).
Confirm before 3a.

### 2. Per-action function signatures (the load-bearing design)

Each action mutates caller-staged buffers. The evolving state
across the ordered list is: the in-memory **directory bytes**, the
**refcount-block bytes**, and the **allocation cursor**. Proposed
shape (refine in 3c against the snapshot mutator ergonomics):

```rust
pub struct BitmapGeometry {
    pub cluster_size: u64,
    pub cluster_bits: u32,
    pub refcount_bits: u32,      // gated == 16 in v1
    pub virtual_size: u64,
    pub refblock_count: u64,
    pub host_refblocks_start: u64,
}

/// Result of an action that changed allocation, so the guest knows
/// what to write / zero and how the extension changed.
pub struct ActionOutcome {
    pub new_dir_len: usize,          // bytes written to the out dir buffer
    pub new_nb_bitmaps: u32,
    pub table_clusters_to_zero: /* small fixed list */,  // host offsets to zero-fill
    pub extension_now_present: bool, // false ⇒ last bitmap removed
    // (data-cluster writes for merge handled separately)
}

pub fn action_add(
    old_dir: &[u8], old_len: usize, nb_bitmaps: u32,
    name: &[u8], granularity: u64,
    refblocks: &mut [u8], cursor: &mut snapshot::qcow2::AllocCursor,
    geom: &BitmapGeometry,
    out_dir: &mut [u8],
) -> Result<ActionOutcome, BitmapError>;
// … action_remove / action_clear / action_enable / action_disable analogously
```

Subquestion: does the guest re-stage the directory between actions,
or does each action write into a fresh `out_dir` that becomes the
next action's `old_dir` (double-buffered)? The double-buffer
approach (ping-pong two directory scratch buffers) matches
snapshot's `build_..._table` verbatim-copy idiom and keeps each
action pure. Recommend double-buffering; confirm in 3c and note the
guest-side buffer requirement for Phase 4.

### 3. Empty-bitmap representation (master OQ8) — resolved

An `add`ed enabled bitmap has an **all-zero bitmap table and no
data clusters**; only the table cluster(s) are allocated (and
zero-filled). `clear` returns a bitmap to this state (frees data,
zeroes the table). Confirmed against qemu's lazy `store_bitmap_data`.

### 4. Refcount width + no growth (master OQ6) — resolved with a caveat

Reuse `snapshot::qcow2` allocation/refcount primitives directly.
Two hard constraints inherited from them:
- **`refcount_bits == 16` only.** Non-16-bit qcow2 v3 images are
  refused. The Phase-2 ABI froze `ERROR_*` at 0..=17 with no
  refcount-width code; **append `ERROR_UNSUPPORTED_REFCOUNT_WIDTH =
  18`** to `BitmapResult` (append-only, ABI-safe) and add the
  matching host message in Phase 5. (Alternative: reuse
  `ERROR_UNSUPPORTED_VERSION` with a clarifying message — decide in
  3c; a distinct code is clearer.)
- **No refcount-table growth.** If `alloc_*` returns
  `RefcountExhausted` (existing refblocks full), the action fails
  with `ERROR_NO_SPACE` — `bitmap` refuses rather than growing the
  refcount tree (matching snapshot). Realistic small bitmaps fit
  existing free clusters; worst-case add on a near-full image is
  refused. Document as a v1 limitation.

### 5. Merge scope (master OQ2) — same-image only in v1

`merge` is the hardest action: it must read the **source** bitmap's
data (walk its bitmap table → data clusters), OR the set bits into
the **destination** bitmap's data, and reallocate destination data
clusters to cover the union (a cleared/all-zero destination cluster
that gains bits needs a freshly allocated, written data cluster).
Unlike the other actions it reads and writes **bitmap data**, not
just directory/table metadata. v1 supports **same-image** merge
(source and dest in the same qcow2). Cross-file `-b` is deferred
(no source-file field in the ABI; Phase 5 rejects `-b` with
`ERROR_UNSUPPORTED_ACTION`). **If merge proves too large for this
phase, it may be split to a follow-up** — the ABI already reserves
the action opcode and `ERROR_UNSUPPORTED_ACTION`; the other five
actions deliver a useful subcommand on their own. Decide at 3d
review whether merge lands here or defers.

### 6. Crash-safety ordering (master OQ5) — informs Phase 4, noted here

This crate produces final bytes only. The guest (Phase 4) owns the
two store paths, modeled on `check --repair`'s `INCOMPAT_CORRUPT`
guard sequence (`src/operations/check/src/main.rs:3737+`):
- **Full rewrite** (add/remove/clear/merge): allocate + write new
  clusters, write the new directory, update refcounts, update the
  bitmaps extension, and set/keep the autoclear bit — ordered so a
  crash leaves either the old state or a consistent new one.
- **In-place flag flip** (enable/disable): the crash-safe
  clear-autoclear → rewrite-directory-in-place → set-autoclear
  dance. This crate supplies the rewritten directory bytes and the
  extension body; the guest supplies the ordering + fsyncs. This
  crate must therefore expose enough (e.g. `serialize_bitmaps_
  extension`, the directory builders) for the guest to sequence it.

### 7. Device wiring (input vs output) — flagged for Phase 4

The Phase-2 ABI text said bitmap "reuses `read_output_sector` /
`write_output_sector`" (the resize/amend idiom). But `snapshot` /
`commit` / `check` — the ops that do refcount mutation like bitmap
will — use the **input-device** RW helpers
(`read_input_byte_range` / `write_input_byte_range`, dev 0). This
crate is I/O-agnostic so it does not matter here, but **Phase 4
should resolve to the input-device idiom** (bitmap is a
snapshot-shaped RW mutation), overriding the ABI plan's offhand
"output" wording. Recorded so Phase 4 does not blindly follow the
Phase-2 note.

## Step-level guidance

Plan at **high** effort throughout except 3e. This is the
correctness core; getting refcount/allocation or the directory
rebuild wrong corrupts images. Skew to opus.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | high | opus | none | Create `src/crates/bitmap/` (`Cargo.toml`: `no_std`; deps `shared`, `qcow2 { path=..., default-features=false }`, `snapshot { path=... }`); add `"crates/bitmap"` to `src/Cargo.toml` workspace members. `src/crates/bitmap/src/lib.rs`: `#![no_std]`, `pub enum BitmapError` with a variant per failure (UnsupportedFormat, UnsupportedVersion, ParseFailed, HeaderMismatch, BitmapNotFound, BitmapExists, BitmapInUse, NameTooLong, GranularityRange, TooManyBitmaps, NoSpace, WriteFailed, ReadFailed, ScratchTooSmall, InternalOverflow, MergeSourceNotFound, UnsupportedAction, UnsupportedRefcountWidth), and `impl From<BitmapError> for u32` returning the matching `shared::BitmapResult::ERROR_*` (mirror `snapshot::SnapshotError`'s `From` at `src/crates/snapshot/src/lib.rs:118-138`; append `ERROR_UNSUPPORTED_REFCOUNT_WIDTH = 18` to `BitmapResult` in `src/shared/src/lib.rs` — append-only, update its error-code stability test). `pub mod directory; pub mod action;` (stubs OK this step). Inline test: every BitmapError maps to a distinct wire code and each equals the intended `ERROR_*`. Verify `cargo test -p bitmap` builds/runs (dockerized). |
| 3b | high | opus | none | Implement `src/crates/bitmap/src/directory.rs`: `FoundBitmap`, `find_bitmap`, `directory_byte_len`, entry-bounds, `build_directory_with_added`, `build_directory_without`, `build_directory_replacing`, `serialize_bitmaps_extension` — per Mission §2, reusing Phase 1's `parse_bitmap_dir_entry`/`serialize_bitmap_dir_entry` and mirroring `snapshot::table` (`build_snapshot_table` `:259`, `_without` `:307`, `snapshot_table_byte_len` `:205`, `round_up_8` `:565`). All `no_std`, panic-free, bounds-checked (return `BitmapError` on overflow/short buffer). Inline tests: build a directory of several entries (varied names/flags/granularity via Phase-1 serialize), then find by name (present/absent), add (verbatim-copy + append + 8-align), remove (compaction), replace-flags (enable↔disable round-trip), and extension-body round-trip vs `parse_bitmaps_extension`. Mirror `snapshot::table`'s test style. |
| 3c | high | opus | none | Implement `src/crates/bitmap/src/action.rs`: `BitmapGeometry`, `ActionOutcome` (Open question 2), and `action_add` / `action_remove` / `action_clear` / `action_enable` / `action_disable` per Mission §3, with validate-before-mutate. Reuse `snapshot::qcow2::{AllocCursor, alloc_contiguous_clusters_in_refblocks, read_refcount_in_block, set_refcount_in_block, check_refcount_after_addend}` for cluster alloc/free + refcount, and Phase-1 geometry (`bitmap_bytes_needed`, `bitmap_table_size_entries`, `granularity_bits_valid`, `default_granularity`). Gate `refcount_bits == 16` (else `UnsupportedRefcountWidth`); map `alloc` `RefcountExhausted`/`Unsupported` to `NoSpace`/`UnsupportedRefcountWidth`. Decide + document the double-buffer directory approach (OQ2). Inline tests with hand-built directory + refblock fixtures: add allocates a zero table cluster and no data clusters and appends an enabled entry; add duplicate ⇒ BitmapExists; add on v2/granularity-out-of-range/65535-bitmaps/non-16-bit ⇒ the right errors; remove frees clusters (refcounts drop to 0) and compacts; remove last ⇒ extension_now_present false; clear zeroes the table + frees data + keeps the entry; enable/disable flip only the auto flag; every action refuses an in_use bitmap except remove. Assert refblock bytes and directory bytes post-mutation. |
| 3d | high | opus | none | Implement same-image `merge` (Open question 5), in `action.rs` or a new `merge.rs`. Given a source and destination bitmap (both found in the directory; source may equal dest — no-op-ish), OR the source's set bits into the destination: walk the source bitmap table (Phase-1 `decode_bitmap_table_entry`), for each source data cluster OR its bits into the destination's corresponding cluster, allocating a destination data cluster (via `alloc_*`, zero-filled then OR'd) where the destination was all-zeroes, and freeing/rewriting as needed; update the destination's bitmap table entries. Validate: both bitmaps exist (`BitmapNotFound` / `MergeSourceNotFound`), neither is `in_use` (`BitmapInUse`), granularities compatible (qemu requires equal granularity to merge — confirm and enforce, else a clear error). Because this reads/writes bitmap DATA (not just metadata), define how the guest stages source + dest data clusters (bounded; document the limit). Inline tests: merge a source with set bits into an empty dest allocates + populates dest data clusters with the OR; merge disjoint and overlapping bit patterns; self-merge; granularity-mismatch refusal; in_use refusal. **If this step balloons, STOP and recommend deferring merge to a follow-up** (return `UnsupportedAction`, reserve the tests) — the other five actions stand alone. Flag the decision at review. |
| 3e | medium | sonnet | none | Ensure `cargo test -p bitmap` is covered by `make test-rust` (add to the Makefile test-rust target if workspace membership alone doesn't run it; check how `snapshot`/`amend` crates are invoked there). Full gate: `make test-rust`, `make lint`, `make instar` (the new crate must not break the guest builds — it is not yet linked into any op, so dead-code/unused-dep warnings must be clean), `make check-binary-sizes` (no op links it yet, so binaries unchanged — confirm), `pre-commit run --all-files`. |

## Management session review checklist

- [ ] Intended files changed, no others (read them).
- [ ] `bitmap` crate is `no_std`, panic-free, bounds-checked
      (it is fuzzed in Phase 9); reuses `snapshot::qcow2` for
      refcount/alloc rather than reimplementing.
- [ ] Validate-before-mutate: a refused action leaves the caller's
      directory + refblock buffers byte-identical.
- [ ] Empty-add allocates only a zero table cluster (no data
      clusters); clear returns to that state.
- [ ] `refcount_bits != 16` and `RefcountExhausted` are refused
      with clear errors, not mishandled.
- [ ] `BitmapError` → `BitmapResult::ERROR_*` mapping is complete
      and distinct; `ERROR_UNSUPPORTED_REFCOUNT_WIDTH = 18` appended
      ABI-safely with its stability test updated.
- [ ] Merge (if landed) OR-s bits correctly and enforces equal
      granularity + in_use refusal; (if deferred) `UnsupportedAction`
      + a recorded decision.
- [ ] `make test-rust`, `make lint`, `make instar`,
      `make check-binary-sizes`, `pre-commit run --all-files` green.
- [ ] Commit messages follow conventions (Co-Authored-By with
      model/context/effort/settings; Signed-off-by; Prompt:).

## Success criteria

- `src/crates/bitmap/` exists, `no_std`, registered in the
  workspace, depending on `snapshot` for refcount/alloc reuse.
- `directory.rs` builds/finds/adds/removes/replaces directory
  entries and serializes the extension body, round-tripping against
  Phase 1's parsers.
- `action.rs` implements add/remove/clear/enable/disable (and merge,
  or a documented deferral) as pure validate-then-mutate functions
  over staged slices, reusing `snapshot::qcow2`, with the empty-
  bitmap and refcount-width/no-growth semantics above.
- Every failure maps to the correct `BitmapResult` wire code.
- `make test-rust` (incl. the new `bitmap` crate tests), `make
  lint`, `make instar`, `make check-binary-sizes`, and
  `pre-commit run --all-files` are all green.

### Execution notes

#### 3c design decisions

**Double-buffered directory (OQ2, confirmed).** Each action reads
`old_dir` and writes a fresh `out_dir`. The Phase-4 guest keeps two
directory scratch buffers of the worst-case directory size and swaps
them so a just-written `out_dir` becomes the next action's `old_dir`.
The refcount blocks (`refblocks: &mut [u8]`) and the `AllocCursor` are
threaded across all actions and mutated in place. This keeps every
action a pure verbatim-copy + edit (mirroring
`snapshot::table::build_*`).

**`ActionOutcome` final shape.** Small, `Copy`, `no_std`:
`new_dir_len: usize`, `new_nb_bitmaps: u32`,
`extension_now_present: bool` (false only when the last bitmap was
removed), `table_clusters_to_zero: [u64; MAX_TABLE_CLUSTERS]` +
`num_table_clusters_to_zero: usize` (host offsets of newly-allocated
`add` table clusters the guest must zero-fill), and — the
data-cluster-freeing split below — `freed_table_offset: u64`,
`freed_table_size: u32`, `zero_freed_table: bool`. `MAX_TABLE_CLUSTERS`
is 8; a realistic bitmap table is one cluster and `add` refuses a
table needing more than 8 (`NoSpace`).

**Validation ordering for `add`.** `refcount_bits == 16`, then name
length (`1..=1023`, `NameTooLong` covers *both* empty and over-long —
documented on the fn), then `granularity_bits_valid`, then the count
guard (`nb_bitmaps < 65535`, `TooManyBitmaps`) **before** `find_bitmap`
(so an image already at the max is refused cheaply and without walking
the directory), then `BitmapExists`. All validation precedes the
single allocation, so a refused `add` leaves `refblocks` + `cursor`
byte-identical (asserted in tests).

**remove/clear data-cluster freeing — the crate/guest split (OQ5
adjacent).** The crate performs **no I/O**, so it cannot read a
bitmap's on-disk **bitmap table** and therefore cannot know which
**data clusters** the bitmap owns. The split is:

- The crate frees what it can compute directly from the directory
  entry: the **table clusters** (host offset = `bitmap_table_offset`,
  count = `ceil(bitmap_table_size * 8 / cluster_size)`), by setting
  their refcounts to 0 in the staged `refblocks`. It validates every
  table-cluster offset maps into the staged refblocks *before* writing
  any refcount, preserving validate-before-mutate.
- It returns the table's on-disk location
  (`freed_table_offset` / `freed_table_size`) so the **Phase-4 guest**
  walks the on-disk table (Phase-1 `decode_bitmap_table_entry`) and
  frees the data clusters the crate cannot see.

`remove` frees the table clusters (the whole bitmap is going away) and
sets `zero_freed_table = false`. `clear` keeps the directory entry and
the table clusters *allocated* (an empty bitmap keeps its all-zero
table), so it changes **no** refcounts — it only validates, copies the
directory through verbatim, and sets `zero_freed_table = true` to tell
the guest to (a) walk the table and free the data clusters, then
(b) zero the table cluster(s) back to an all-zero empty table.

No `table: &[u8]` staging parameter was added: passing the table into
the crate would still leave the *write-back* (freeing + zeroing on
disk) to the guest, so the walk is cleaner kept guest-side next to the
I/O it already owns. This is a deviation from Mission §3's wording
("free … any allocated data clusters" inside the action) driven by the
no-I/O constraint; the net freeing is identical, just split across the
crate/guest boundary.

#### 3d design decisions (merge — the crate/guest split)

**Merge landed here, as pure pieces only (not deferred).** `merge`
inherently reads on-disk bitmap **data** clusters (source and dest) and
writes dest data clusters — I/O this pure `no_std` crate cannot do. So,
exactly like the 3c remove/clear split, the crate owns the pure,
testable logic and the Phase-4 guest owns the I/O orchestration. The
crate side is *not* empty (validation + `or_bitmap_data` +
`merge_cluster_action` are clean pure logic), so merge was **not
deferred**; the ABI still reserves `ACTION_MERGE` and
`ERROR_UNSUPPORTED_ACTION` for the cross-file `-b` case, which remains
deferred (no source-file field in the ABI).

**Module choice.** The merge pieces live in a new
`src/crates/bitmap/src/merge.rs` (`pub mod merge;` in `lib.rs`), not in
`action.rs`. Merge introduces its own vocabulary (`MergeSpec`,
`MergeClusterAction`, `or_bitmap_data`) and, unlike the other five
actions, does not follow the `action_*(…, out_dir) -> ActionOutcome`
shape (it reads/writes bitmap *data*, not the directory), so a separate
module reads more cleanly. It reuses `action::BitmapGeometry` and
`directory::{find_bitmap, FoundBitmap}`.

**New ABI error.** `ERROR_INCOMPATIBLE_MERGE = 19` was appended to
`BitmapResult` (append-only, ABI-safe; `bitmap_result_error_codes_
distinct` updated to cover 19), with `BitmapError::IncompatibleMerge`
and its `From` mapping. Returned when the two bitmaps' `granularity_
bits` differ (hence unequal bit-count / `bitmap_table_size`).

**Crate/guest contract for merge.**

- `merge_validate(dir, nb_bitmaps, dest_name, source_name, geom) ->
  MergeSpec` — pure, no I/O, no mutation. Validates: dest exists
  (`BitmapNotFound`); source exists (`MergeSourceNotFound`); neither is
  `in_use` (`BitmapInUse`, dest checked first); equal `granularity_bits`
  and equal `bitmap_table_size` (`IncompatibleMerge`). **Self-merge**
  (`source_name == dest_name`) is a legal no-op (matches qemu): it
  passes validation and returns `MergeSpec { self_merge: true, … }` so
  the guest can skip the table walk. `MergeSpec` hands the guest both
  bitmaps' on-disk table offsets/sizes (via `FoundBitmap`) plus the
  equal `table_size` and `granularity_bits`.
- `merge_cluster_action(source_entry: u64, dest_entry: u64) ->
  MergeClusterAction` — decode both raw table words (Phase-1
  `decode_bitmap_table_entry`; a bad word ⇒ `ParseFailed`) and tell the
  guest what to do for that table index.
- `or_bitmap_data(dst: &mut [u8], src: &[u8])` — byte-wise `dst[i] |=
  src[i]`; requires **equal length** (a whole data cluster), else
  `InternalOverflow` (no partial OR). The guest calls this per
  `OrIntoExisting`/`AllocDestFromSource` cluster after staging both
  clusters into scratch.

**`MergeClusterAction` truth table (source × dest table entry).**

| source \ dest | AllZeroes           | AllOnes | Allocated       |
|---------------|---------------------|---------|-----------------|
| **AllZeroes** | Skip                | Skip    | Skip            |
| **AllOnes**   | CopyAllOnes         | Skip    | CopyAllOnes     |
| **Allocated** | AllocDestFromSource | Skip    | OrIntoExisting  |

- **Skip** — nothing to write: source contributes no set bits
  (AllZeroes), or dest is already AllOnes (cannot gain bits).
- **CopyAllOnes** — dest becomes AllOnes: the guest sets the dest table
  entry's all-ones flag and, if the dest previously had an `Allocated`
  data cluster, **frees** that cluster. (This folds the flagged "source
  all-ones + dest allocated ⇒ dest all-ones, free its data cluster"
  case — recorded in the variant's doc; the crate reports the action,
  the guest performs the free since only it can read the dest table.)
- **OrIntoExisting** — both allocated: guest reads both clusters, calls
  `or_bitmap_data`, writes the result to the dest's existing data
  cluster; dest table entry unchanged.
- **AllocDestFromSource** — source allocated, dest AllZeroes: guest
  allocates a fresh dest data cluster, copies the source bits in, writes
  it, and points the dest table entry at the new cluster.

**What the guest (Phase 4) owns for merge (NOT in this crate):** walking
the two on-disk bitmap tables and pairing entry `i`; reading/writing
data clusters; allocating dest data clusters (`snapshot::qcow2::alloc_*`)
and freeing dest clusters that `CopyAllOnes` obsoletes; rewriting the
dest bitmap table entries + refcounts; and re-serializing the directory
(the dest directory entry is unchanged unless a full-rewrite path is
chosen). The bounded staging of source+dest data clusters (one cluster
of each at a time) is guest-side scratch management.

**`read_cluster_refcount` is `#[cfg(test)]`.** The actions only need
the refcount *write* path (freeing table clusters via
`set_cluster_refcount`). The read companion is exercised by tests and
gated `#[cfg(test)]` so clippy (`-D warnings`, no `--all-targets`) does
not flag it as dead code; the Phase-4 guest can promote it when it
needs to inspect refcounts.

## Back brief

Before executing any step, the executing agent should back brief
the operator on its understanding — in particular that this is a
**pure-logic crate** (no device I/O, no guest orchestration, no
autoclear dance — those are Phase 4), that it follows the
**in-place slice-mutator convention** (not a patch list) and
**reuses `snapshot::qcow2`** for refcount/allocation (16-bit-only,
no growth ⇒ refuse), and that **merge may be deferred** if it
proves too large, since the other five actions stand alone.
