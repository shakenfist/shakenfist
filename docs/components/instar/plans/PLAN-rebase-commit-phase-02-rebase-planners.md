# PLAN-rebase-commit phase 02: rebase planners

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the `*Plan` /
`*Patch` types in `src/crates/resize/`, the qcow2 header /
backing-file / refcount primitives in `src/crates/qcow2/`,
the vmdk descriptor parsing in `src/crates/vmdk/`, the create
backing-file write paths in `src/crates/create/`), and ground
your answers in what the code actually does today. Do not
speculate about the codebase when you could read it instead.
Where a question touches on external concepts (QCOW2 backing
chains, vmdk monolithicSparse layout, `qemu-img rebase` safe
mode), research as needed to give a confident answer.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-rebase-commit-phase-NN-<descriptive>.md`. The master
plan is [PLAN-rebase-commit.md](/components/instar/plans/PLAN-rebase-commit/). Phase
1 ([PLAN-rebase-commit-phase-01-abi.md](/components/instar/plans/PLAN-rebase-commit-phase-01-abi/))
landed the shared ABI; this phase is the second of twelve.

I prefer one commit per logical step. The step table below
identifies seven steps; this phase can land as one commit
covering all of them, or split with one commit per step if
the management session prefers reviewing in smaller bites.
Either way each commit must be self-contained: build, lint,
pass tests.

## Situation

Phase 1 shipped the shared data types and call-table entries
that `instar rebase` will use at runtime (`RebaseConfig`,
`RebaseResult`, `send_rebase_result`, `write_input_sector`).
This phase delivers the planner crate that converts a parsed
overlay header + a rebase request into the set of mutations
the guest will execute against the overlay.

The work falls into two halves:

- **Unsafe mode (`-u`)**: rewrite only the backing-file pointer
  in the overlay's header. No chain reading, no data copy. For
  qcow2 this is a small write to the header fields plus the
  path string; for vmdk monolithicSparse it is a descriptor
  rewrite that changes `parentFileNameHint=` and `parentCID=`.
  The planner can express both as a small `RebasePlan` of
  byte-level patches that the guest applies in order.

- **Safe mode** (default): for every guest cluster, the guest
  must compare the old chain's view against the new chain's
  view, and if they differ and the overlay does not own that
  cluster, copy the old chain's data into a freshly allocated
  overlay cluster. This is the first instar operation where
  the *planner cannot pre-compute the patch list*. Data
  comparison is inherently a runtime operation. The planner's
  job in safe mode is to produce the metadata the guest needs
  to drive that comparison loop:
  - The set of guest clusters in scope (count, size).
  - The allocation state machine: where the overlay's
    refcount table lives, which refcount blocks already
    exist, where new clusters would go if allocated, and a
    pure function the guest calls each time it decides a
    copy is needed.
  - The final header rewrite patch, to be applied after the
    comparison loop completes.

The relevant existing infrastructure this phase builds on:

- **Resize crate as the planner template**
  (`src/crates/resize/src/lib.rs`). Crate layout, the
  `ResizePatch` enum (`Write` / `Append` / `ZeroFill` at
  lines 110–144), the `ResizePlan` struct with an inline
  `[ResizePatch; MAX_RESIZE_PATCHES]` (lines 164–210), and
  the `ResizeError` enum (lines 28–86) are the structural
  template for `RebasePatch` / `RebasePlan` / `RebaseError`.
  The crate is `no_std`, takes caller-supplied scratch
  buffers, and is unit-tested under `tests/`.
- **qcow2 header parsing**
  (`src/crates/qcow2/src/lib.rs`). `QcowHeader::parse` at
  line 334, `BACKING_FILE_OFFSET_OFFSET = 8` and
  `BACKING_FILE_SIZE_OFFSET = 16` at lines 44–45, the
  backing-format extension constant
  `EXT_BACKING_FORMAT = 0xE2792ACA` at line 60, and
  `parse_header_extensions` at lines 470–536.
- **qcow2 refcount primitive**
  (`src/crates/qcow2/src/lib.rs:2980` `lookup_refcount`).
  Already public; the safe-mode allocator builds on this to
  scan for free clusters across all refcount widths
  (1, 2, 4, 8, 16, 32, 64 bits).
- **qcow2 L2 entry semantics**: standard 8-byte BE entries,
  or 16-byte extended L2 entries (cluster pointer in upper
  8 bytes, subcluster bitmap in lower 8 bytes). Resize's
  qcow2 planner already treats extended L2 as "fully
  allocated" (see `src/crates/resize/src/qcow2.rs`); rebase
  will follow the same convention until subcluster-level
  rebase is needed.
- **vmdk descriptor parsing**
  (`src/crates/vmdk/src/lib.rs:1081` `read_and_parse_descriptor`
  and line 1114 `parse_descriptor`). Extracts `CID=`,
  `parentCID=`, and the extent lines that carry the
  `parentFileNameHint=` reference. The descriptor lives at
  `header.desc_offset_sectors * 512`, sized
  `desc_size_sectors * 512`.
- **Create backing-file emitters**
  (`src/crates/create/src/lib.rs`).
  `build_vmdk_descriptor_with_backing(cid, parent_cid,
  backing_path)` already emits a fresh descriptor with all
  three reference fields. The qcow2 equivalent
  `build_header` already writes the `backing_file_offset` /
  `backing_file_size` fields and appends the path string;
  rebase reuses the same byte layout for in-place updates.
- **Phase 1 ABI**
  (`src/shared/src/lib.rs`). `RebaseConfig` carries
  `new_backing_path: [u8; 1024]`, `new_backing_path_len:
  u32`, `overlay_cluster_size`, the chain-slot delimiters,
  and the `FLAG_UNSAFE` / `FLAG_DETACH` / `FLAG_QUIET`
  flags. `RebaseResult::ERROR_*` codes 0–6 are append-only
  and map to the `RebaseError` enum this phase introduces.
- **Fuzz target layout**
  (`src/fuzz/fuzz_targets/fuzz_resize_planners.rs`). Phase
  10 will add `fuzz_rebase_planners.rs` against the public
  entry points this phase ships; the planner must expose
  fuzz-friendly entry points (pure functions on raw byte
  slices, no `CallTable`).

## Mission and problem statement

After phase 2 lands:

1. A new `no_std` crate `src/crates/rebase/` exists, declared
   in the workspace `src/Cargo.toml` and depending on
   `shared`, `qcow2`, `vmdk`, and `create` (for the
   backing-path emit helpers).

2. The crate's public surface in `src/crates/rebase/src/lib.rs`
   exposes:
   - `RebasePatch<'a>` (the same Write / Append / ZeroFill
     trio as `ResizePatch`, copied not abstracted —
     resize's enum is `pub` and stable but the rebase use
     case is small enough that adding a dependency between
     the two crates would be premature).
   - `RebasePlan<'a>` with inline `[RebasePatch; N]` and
     methods `patches()`, `push()`, `total_file_size`.
   - `RebaseError` enum with at least these variants
     (one-to-one with `RebaseResult::ERROR_*` from phase 1
     where applicable): `UnsupportedFormat`,
     `NewBackingIncompatible`, `ExternalDataFile`,
     `LuksUnsupported`, `ChainDepth`, `HeaderMismatch`,
     `BackingPathTooLong`, `ScratchTooSmall`,
     `OverlayCorrupt`, `RefcountExhausted`,
     `Overflow`, `ParseFailed`. The enum is `#[derive(Debug,
     Clone, Copy, PartialEq, Eq)]`.
   - `Qcow2RebaseOpts<'a>` carrying the parsed
     `QcowHeader`, the existing header bytes, the existing
     refcount-table bytes (for the safe-mode allocator),
     the new backing path slice, the new backing format,
     and the mode flag.
   - `VmdkRebaseOpts<'a>` carrying the parsed `Vmdk4Header`,
     the existing descriptor bytes, the existing grain
     directory bytes, the new backing path slice, and the
     new parent CID. The new parent CID is the new
     backing's CID; the host pre-reads it before
     populating `VmdkRebaseOpts`.
   - `plan_rebase_qcow2(opts: &Qcow2RebaseOpts<'_>, scratch:
     &'a mut [u8]) -> Result<Qcow2RebaseOutput<'a>,
     RebaseError>` (see open question 1 for the unified vs
     split entry shape).
   - `plan_rebase_vmdk(opts: &VmdkRebaseOpts<'_>, scratch:
     &'a mut [u8]) -> Result<VmdkRebaseOutput<'a>,
     RebaseError>`.
   - `Qcow2RebaseOutput` and `VmdkRebaseOutput` — see open
     question 2.

3. For **unsafe mode** the planner produces a `RebasePlan`
   that the guest can apply directly:
   - qcow2: a Write patch for the header fields
     (backing_file_offset, backing_file_size, optional
     backing-format extension), and either a Write patch
     for the path string if it fits in the existing slot or
     a request to relocate the path to a fresh cluster (in
     which case the planner falls back to safe-mode-style
     allocation; the only difference is no data copy
     happens). The header is rewritten last by the guest,
     not by the planner.
   - vmdk: a Write patch for the descriptor region (sector
     1, length `desc_size_sectors * 512`), with the
     descriptor text re-emitted via `create`'s helper. The
     header itself does not change.

4. For **safe mode** the planner produces a
   `Qcow2RebaseOutput::Safe { context, header_patch }` or
   `VmdkRebaseOutput::Safe { context, descriptor_patch }`:
   - `RebaseQcow2SafeContext<'a>` carries the overlay's
     cluster size, the cluster count, the refcount layout
     (refcount-table offset, refcount-bits width, where the
     free-cluster scan should start, which refcount blocks
     already exist), and a copy of the refcount-block bytes
     in scratch (the guest will mutate them as it allocates).
     Plus a small `AllocationState` struct the guest threads
     through `allocate_overlay_cluster` calls.
   - `RebaseVmdkSafeContext<'a>` carries the grain
     directory bytes and a similar `AllocationState`.
   - The `header_patch` / `descriptor_patch` is the final
     metadata rewrite the guest applies after the
     comparison loop completes.

5. Pure allocation helpers live in the crate:
   - `allocate_overlay_cluster_qcow2(context: &mut
     RebaseQcow2SafeContext, state: &mut AllocationState)
     -> Result<u64, RebaseError>` — returns the host-byte
     offset of a freshly claimed cluster, mutates the
     refcount-block bytes in `context` to bump the refcount
     from 0 to 1, mutates `state` to remember where the
     scan should continue next time. Pure: no I/O, no
     side-effects outside the supplied buffers.
   - `allocate_overlay_grain_vmdk(...)` — analogous for
     vmdk's grain table.
   - `overlay_has_cluster_qcow2(l2_entry_bytes: &[u8],
     extended_l2: bool) -> bool` — pure decoder; helps the
     guest decide whether to skip a cluster.

6. Unit and integration tests under
   `src/crates/rebase/tests/` cover, at minimum:
   - qcow2 unsafe-mode rebase: build a small qcow2 image
     via `create::plan_qcow2`, rebase it onto a different
     backing path, apply the patches, re-parse, assert the
     new backing reference is in place.
   - qcow2 unsafe-mode rebase with a longer new path that
     forces relocation to a fresh cluster.
   - qcow2 safe-mode rebase smoke: plan against a known
     overlay+chain shape, assert the context's claims
     (cluster count, allocation scan start) match the
     overlay metadata.
   - qcow2 safe-mode allocator: hand-craft a refcount block
     with two free entries, allocate two clusters in
     sequence, assert offsets and refcount-block bytes are
     correct, assert a third call returns
     `RefcountExhausted`.
   - vmdk unsafe-mode rebase: build a monolithicSparse
     image via `create::plan_vmdk`, rebase the backing,
     assert descriptor lines are correct.
   - Error paths: incompatible virtual sizes
     (`NewBackingIncompatible`), oversized backing path
     (`BackingPathTooLong`), qcow2 with external data file
     (`ExternalDataFile`).

7. `make instar`, `make lint`, `make test-rust`,
   `make check-binary-sizes`, and `pre-commit run --all-files`
   all pass.

Nothing in phase 2 changes user-visible behaviour. The crate
is consumed only by phase 3 (the rebase guest binary) and
phase 10 (the fuzz harness).

## Open questions

### 1. Unified entry point per format, or split unsafe vs safe?

Working choice: **unified per format, dispatch by `mode` field
in opts**. Mirrors how resize collapses grow + shrink + noop
into one `plan_resize_qcow2` entry. Reduces the public surface
the guest binary has to wire up.

The dispatching looks like:

```rust
pub fn plan_rebase_qcow2<'a>(
    opts: &Qcow2RebaseOpts<'_>,
    scratch: &'a mut [u8],
) -> Result<Qcow2RebaseOutput<'a>, RebaseError> {
    if opts.flags & FLAG_UNSAFE != 0 {
        plan_qcow2_unsafe(opts, scratch).map(Qcow2RebaseOutput::Unsafe)
    } else {
        plan_qcow2_safe(opts, scratch).map(Qcow2RebaseOutput::Safe)
    }
}
```

The internal `plan_qcow2_unsafe` / `plan_qcow2_safe` are
`pub(crate)` so unit tests can reach them directly.

### 2. Output shape: enum or single struct with optional context?

Working choice: **enum**, with `Unsafe` carrying a `RebasePlan`
ready to apply and `Safe` carrying a context plus a
deferred-apply metadata patch:

```rust
pub enum Qcow2RebaseOutput<'a> {
    Unsafe {
        plan: RebasePlan<'a>,
    },
    Safe {
        context: RebaseQcow2SafeContext<'a>,
        // Header rewrite the guest applies after the
        // comparison loop completes. Kept separate from
        // `context` because it is the *last* thing the
        // guest writes; the allocator may also append
        // small refcount-block patches as it goes.
        header_patch: RebasePatch<'a>,
    },
}
```

An enum makes the two-mode contract explicit at compile
time. The downside is the guest binary needs a match; the
alternative (a single struct with an `Option<Context>`)
hides the contract in runtime checks.

### 3. Path-relocation policy when the new path is longer

When the new backing path is longer than the slot the old
path occupied, qemu-img relocates the path string to a fresh
cluster and updates the header pointer. We follow the same
approach:

- If `new_backing_path_len <= old_backing_file_size` (the
  slot capacity that was reserved with the original path):
  rewrite in place at the existing `backing_file_offset`.
  This is the common case; even a moderately shorter path
  still fits.
- Else, allocate a fresh cluster via the same
  `allocate_overlay_cluster_qcow2` helper used in safe
  mode, write the new path there, and update
  `backing_file_offset` to point at it. The old slot
  becomes leaked bytes; `instar check` reports it as
  uninteresting (refcount 0). `qemu-img check -r` can
  reclaim it later.

This means **unsafe mode can also allocate clusters**, which
slightly broadens the "no data writes" guarantee — but the
only thing it writes is the new path string, which is bytes
the user explicitly named. Confirm: this is fine.

### 4. Backing-format extension handling

QCOW2 v3 carries the backing format in a header extension
(`type = 0xE2792ACA`). Rebase must:

- If the user passed `-F BACKING_FMT`, encode it as that
  extension; if the existing header had an extension of
  this type, overwrite or rewrite the extension block to
  match.
- If the user did **not** pass `-F`, leave the existing
  extension untouched. This means rebase preserves the
  hint when only the path is changing.
- If the new backing path is empty (`FLAG_DETACH`), also
  remove the extension if present.

Working draft: the planner takes an
`opts.new_backing_format: Option<BackingFormat>` (None = leave
unchanged) and emits a separate patch for the extension
block when needed. The planner is responsible for the byte
layout; `qcow2` parsing already has `EXT_BACKING_FORMAT`
constants but not yet a writer — phase 2 adds the writer to
either `crates/qcow2/` (preferred — keeps format knowledge in
the format crate) or `crates/rebase/` (acceptable fallback).

### 5. Allocator failure mode

The safe-mode allocator scans for refcount entries == 0. If
every existing refcount block is full, the allocator needs to
*append* a new refcount block. That is the same logic resize
phase 7 already implemented for grow (see
`src/crates/resize/src/qcow2.rs` `plan_l1_and_refcount_grow`).

Working choice: **defer refcount-block extension to a phase 2
follow-up**. v1 of the safe-mode allocator returns
`RefcountExhausted` when the existing blocks are full. The
guest binary surfaces this as `RebaseResult::ERROR_REFCOUNT_EXHAUSTED`
(append a new error code in phase 3) and the user reruns with
`-u` or runs `qemu-img rebase` for now. This bound is large
in practice (a 64 GB qcow2 with default cluster size has
~16k clusters worth of refcount entries, of which ~16k must
become free for the allocator to exhaust — unrealistic for a
typical rebase).

If integration testing shows this bound is too tight in
practice, lift the deferral and bring `plan_l1_and_refcount_grow`'s
refcount-block-append logic into the rebase crate.

### 6. How does the guest "iterate guest clusters" in safe
mode?

Not a phase 2 concern — that lives in phase 3. But the
planner's `RebaseQcow2SafeContext` must expose enough
information that the guest can do it efficiently. Minimum
fields:

- `overlay_cluster_count: u64` — total guest clusters to
  iterate (`overlay.virtual_size / cluster_size`).
- `overlay_l1_table_offset: u64`, `overlay_l1_table_size:
  u32` — so the guest can decode L2 pointers per guest
  cluster.
- `old_chain_first_input_idx`, `old_chain_input_count` (from
  `RebaseConfig`) — for chain-reads against the old chain.
- `new_chain_first_input_idx`, `new_chain_input_count` —
  for chain-reads against the new chain.
- A pointer into scratch where the refcount-block bytes
  live (the allocator mutates them in place; the guest
  flushes them via Write patches once the comparison loop
  completes).

### 7. Should `RebasePatch` be a copy of `ResizePatch` or a
type alias?

Working choice: **separate copy**. Resize's `ResizePatch`
includes a `ZeroFill` variant that rebase will not emit
(qcow2 backing-path slots are written, not zeroed). Phase 2
defines a minimal `RebasePatch` with `Write` and `Append`
only. If phase 6 (commit planners) finds it needs `ZeroFill`
for the overlay-clear pass, it can add the variant then.
Either way, no `pub use resize::ResizePatch`.

## Execution

The phase plan recommends seven steps. Each step is small
enough to review independently; consolidating into one
commit at the end is also fine. The step table below is for
sub-agent assignment.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | **Shipped** as `6395d97`. Scaffold `src/crates/rebase/`: `Cargo.toml` declaring no-std with path-deps on `shared`, `qcow2`, `vmdk`, `create`; `src/lib.rs` with crate-level `#![no_std]`, `#![allow(clippy::too_many_arguments)]`, the public types (`RebasePatch<'a>`, `RebasePlan<'a>` with `MAX_REBASE_PATCHES = 64`, `RebaseError` enum, `RebaseMode { Unsafe, Safe }`), and stub entry points `plan_rebase_qcow2` and `plan_rebase_vmdk` that return `RebaseError::UnsupportedFormat`. Add the crate to the workspace members list in `src/Cargo.toml`. Mirror the `src/crates/resize/Cargo.toml` layout exactly. After this step `make instar` builds clean and clippy is silent. |
| 2b | high | opus | none | **Shipped** as `0e4c4b9` (with step 2c). Implement qcow2 unsafe-mode planner in `src/crates/rebase/src/qcow2.rs` (new private module). Public-to-crate function `plan_qcow2_unsafe(opts, scratch) -> Result<RebasePlan, RebaseError>`. Computes: (1) validation — refuse external-data-file qcow2 with `ERROR_EXTERNAL_DATA_FILE`, refuse if `new_backing_path_len > backing_file_size_max` (1024), refuse on `OverlayCorrupt` if `QcowHeader::parse` returns None; (2) header rewrite bytes — `backing_file_offset` (u64 BE) and `backing_file_size` (u32 BE) at header offsets 8 and 16; (3) the path string at the existing `backing_file_offset` slot if `new_len <= old_size`, else allocate via the qcow2 allocator from step 2c (which is the same allocator used in safe mode) and write at the new offset; (4) optional backing-format extension — see open question 4. Emit patches into the `RebasePlan` in the order the guest must apply them: refcount + L2 + path string first, header field rewrite last. **Caveat:** long-path relocation deferred — both modes reject with `BackingPathTooLong` when the new path doesn't fit the existing slot. Backing-format extension rewrite also deferred. |
| 2c | high | opus | none | **Shipped** as `0e4c4b9`. Implement qcow2 safe-mode planner + allocator in `src/crates/rebase/src/qcow2.rs`. Adds `RebaseQcow2SafeContext<'a>`, `AllocationState`, `plan_qcow2_safe`, and `allocate_overlay_cluster_qcow2`. **Caveat:** v1 supports `refcount_bits == 16` only (qemu-img's default); 1/2/4/8/32/64-bit widths return `UnsupportedFormat`. The bit-packing reference for the future widths is `qcow2::lookup_refcount` in `src/crates/qcow2/src/lib.rs:2980`. |
| 2d | medium | sonnet | none | **Shipped** as `54caf37`. Implement vmdk unsafe-mode planner in `src/crates/rebase/src/vmdk.rs` (new private module). Public-to-crate function `plan_vmdk_unsafe(opts, scratch) -> Result<RebasePlan, RebaseError>`. Implements a descriptor rewriter that scans the existing descriptor line-by-line and substitutes `parentCID=` and `parentFileNameHint=` lines, preserving everything else; this avoids depending on `create::build_vmdk_descriptor_with_backing` (which would clobber the existing extent line and createType). Detach emits `parentCID=ffffffff` and an empty `parentFileNameHint=""` line. |
| 2e | high | opus | none | **Shipped.** Implement vmdk safe-mode planner + grain allocator in `src/crates/rebase/src/vmdk.rs`. `RebaseVmdkSafeContext<'a>` carries the staged grain-directory bytes, the concatenated grain-table bytes (one block per allocated GT), the per-GT host sector offsets, and a per-GT dirty bitmap. `allocate_overlay_grain_vmdk(ctx, state)` bumps a `next_grain_sector` cursor by `overlay_grain_size_sectors` and returns the host byte offset of a freshly claimed grain; the caller (the guest's per-grain comparison loop) writes the data, updates the matching GTE in `grain_tables`, and marks the containing GT dirty. v1 only allocates grains into already-allocated GTs — the GD-extension follow-up (allocating a fresh GT when GDE == 0) is tracked under "Future work created by this phase". The unsafe-mode `VmdkRebaseOpts` callers get an `unsafe_only(...)` constructor that zeros the safe-mode-only fields. |
| 2f | medium | sonnet | none | **Shipped.** Cross-format integration tests under `src/crates/rebase/tests/`. Four test files plus a shared `tests/common/mod.rs` helper: `qcow2_unsafe.rs` (basic in-place rebase, detach, and long-path rejection — the v1 planner refuses relocation), `qcow2_safe.rs` (smoke: context geometry matches the overlay; allocator: two sequential claims advance the host-offset cursor), `vmdk_unsafe.rs` (descriptor rewrite and detach round-trip), and `vmdk_safe.rs` (smoke + allocator landing at EOF). Each builds the starting image via `create::plan_*`, materialises bytes, runs the planner, applies the patches, and re-parses with the format crate's header parser. |
| 2g | low | sonnet | none | **Complete.** `pre-commit run --all-files` passes (rustfmt + clippy + binary-size + GitHub Actions + shellcheck). Phase 2 rows in the master plan and this phase plan reflect step 2e and 2f shipping; remaining scope reductions (long-path relocation, non-16-bit refcount widths, backing-format extension writer, vmdk GD extension) are tracked under "Future work created by this phase". |

## Agent guidance

### Execution model

Same model as phase 1: implementation work runs in the
management session unless explicitly delegated. The model
guidance in the step table reflects what a sub-agent would
need *if* this work were delegated; the management session
should also use opus when working on steps 2b, 2c, 2e
because the cross-format reasoning load is real even when
the operator is doing the typing.

### Planning effort

The master plan flagged this phase as **high effort**. The
high-effort steps within the phase are 2b, 2c, 2e. Steps 2a,
2d, 2f, 2g are medium-low.

### Step ordering

Steps 2a → 2c → 2b → 2d → 2e → 2f → 2g is the
dependency order. 2b depends on the allocator landing in 2c
because unsafe-mode-with-long-path needs the same allocator
safe mode uses. If the operator prefers the simpler "2b
first, with TODO for relocation, then 2c, then come back to
2b", that is acceptable but produces an interim commit that
fails the "long path" integration test in 2f.

### Management session review checklist

After each step:

- [ ] The files that were supposed to change actually
      changed (read them).
- [ ] No unrelated files modified.
- [ ] The new crate compiles in isolation (`cargo build -p
      rebase` via the lint container or pre-commit).
- [ ] `make instar` builds, `make lint` is clean.
- [ ] `make test-rust` passes (and the new tests are
      actually exercised, not silently skipped).
- [ ] `make check-binary-sizes` unchanged.
- [ ] `pre-commit run --all-files` clean.
- [ ] No `pub use` from rebase re-exports anything that
      should stay private to qcow2 / vmdk / create. Promote
      a `pub(crate)` to `pub` in the format crate if needed;
      do not work around visibility by copying.
- [ ] Refcount width semantics (1, 2, 4, 8, 16, 32, 64 bits)
      have at least one test each in step 2c's allocator
      tests, or the test brief documents which widths are
      out of scope and why.

## Administration and logistics

### Success criteria

Phase 2 is complete when:

* `src/crates/rebase/` exists and is wired into the
  workspace.
* The public surface from the Mission section is implemented.
* Integration tests from the Mission section pass.
* `make instar`, `make lint`, `make test-rust`,
  `make check-binary-sizes`, `pre-commit run --all-files`
  all pass.
* The execution-table row for phase 2 in
  `PLAN-rebase-commit.md` is marked Complete with the
  shipping commit hash.

### Future work created by this phase

- **Refcount-block extension in safe-mode rebase**
  (open question 5). v1 returns `RefcountExhausted` when
  existing blocks fill up. If the bound is too tight in
  practice, fold the resize-phase-7 refcount-block-append
  logic into the rebase allocator.
- **Subcluster-level rebase for extended L2**. v1 treats
  extended L2 as "fully allocated" same as resize does;
  subcluster-granularity rebase would let the planner
  copy only the subclusters that diverged between chains.
  Out of scope until extended-L2 rebase is asked for.
- **Backing-format extension writer in `qcow2` crate**
  (open question 4). If step 2b puts the writer in the
  rebase crate, file a follow-up to migrate it to
  `src/crates/qcow2/` so commit and any future operation
  can reuse it.
- **vmdk twoGbMaxExtent rebase**. v1 only supports
  monolithicSparse on the overlay; rebase of a
  twoGbMaxExtent overlay is rejected with
  `UnsupportedSubformat`. Track as a vmdk-specific
  follow-up alongside the create-side twoGbMaxExtent
  output gap from PLAN-create's Future work.
- **vmdk safe-mode GD extension**. The step 2e allocator
  only fills GTEs in already-allocated GTs. When the
  guest needs to allocate a grain whose GD entry is zero
  (i.e. the covering GT itself does not exist), it must
  first allocate a fresh GT, write a 512-GTE zero block at
  the new sector, and bump the GD entry — then set
  `gd_dirty[0]` so the host flushes the GD. Until that
  follow-up lands, the guest is expected to fall back to
  `-u` (unsafe) mode for overlays whose pre-existing GD
  coverage is incomplete.

### Bugs fixed during this work

To be filled in as work progresses.

### Documentation index maintenance

This is a phase plan, not a master plan. Not added to
`docs/plans/order.yml`.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the
work you intend to do aligns with that plan.
