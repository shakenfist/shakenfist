# PLAN-resize phase 6: VMDK resize planner

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (VMDK4 sparse extent header, the embedded
text descriptor, grain directory / grain table layout,
qemu-img's vmdk_co_truncate behaviour), research as needed to
give a confident answer. Flag any uncertainty explicitly rather
than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that
master plan for overall context. Phases 1–5 are complete; the
phase 5 `vhdx::plan_grow` and `VhdxGrowAction` machinery are the
closest structural template (a small two-flavour grow planner
with a metadata-only fast path).

## Mission

Replace the `UnsupportedFormat` stub in `plan_resize_vmdk`
(`src/crates/resize/src/lib.rs`) with a real **VMDK
monolithicSparse grow** planner. Other subformats (`StreamOptimized`,
`MonolithicFlat`, `TwoGbMaxExtentSparse`, `TwoGbMaxExtentFlat`)
are rejected with `UnsupportedSubformat`. Shrink is deferred to
Future work.

For monolithicSparse grow the planner:

1. Recomputes `num_gd_entries` for the new capacity.
2. If the new entry count still fits in the existing GD region
   (the common case for sub-order-of-magnitude grows), emits a
   `MetadataOnly` plan: update `header.capacity_sectors` and
   rewrite the embedded descriptor's `RW <sectors> SPARSE` line
   with the new sector count.
3. Otherwise emits a `GdGrowRelocate` plan: append a new GD
   region at end of file containing the old entries plus zero
   padding for the new entries, update `header.gd_offset_sectors`,
   then do the same `header.capacity_sectors` + descriptor update.

In both flavours the embedded descriptor's `CID`, `parentCID`,
and `createType` are preserved verbatim; only the extent line's
sector count changes.

## What the survey turned up

- **`Vmdk4Header` / `Vmdk4HeaderFull` parsers**
  (`src/crates/vmdk/src/lib.rs`) surface every field resize
  needs: `capacity_sectors`, `grain_size_sectors`,
  `descriptor_offset_sectors`, `descriptor_size_sectors`,
  `num_gtes_per_gt`, `gd_offset_sectors`, `overhead_sectors`,
  `version`, `flags`. Header magic at offset 0
  (`VMDK4_MAGIC = 0x564D444B`), capacity at offset 12 (u64 LE),
  GD offset at offset 56.
- **`build_sparse_header(buf, capacity_sectors, grain_size_sectors,
  num_gtes_per_gt, gd_offset_sectors, overhead_sectors)`**
  (line 115) writes a complete 512-byte header. Resize calls
  this with the new capacity and (for relocate) the new GD
  offset.
- **`build_descriptor`** (line 221) writes a monolithicSparse
  descriptor but **hardcodes** `CID=fffffffe`,
  `parentCID=ffffffff`, `createType="monolithicSparse"`, and
  the filename. Resize **cannot** use it directly because it
  must preserve the existing image's CID / parentCID. Phase 6
  introduces a private `format_monolithic_sparse_descriptor`
  in the resize crate that takes the preserved fields plus the
  new sector count and emits the text into a caller-supplied
  buffer via `core::fmt::Write`.
- **`parse_descriptor`** (line 1110) extracts `cid`,
  `parent_cid`, and `create_type` from the descriptor's text.
  Phase 6 calls this on the existing descriptor bytes to
  preserve the fields it shouldn't change.
- **`parse_descriptor_extents` / `parse_extent_line`**
  (line 1305) extracts the extent's `size_sectors` and
  `filename`. The filename is preserved verbatim in the
  rewritten descriptor; the size is updated.
- **`num_gd_entries()`** (line 452) on `Vmdk4HeaderFull` —
  `ceil(capacity_sectors / (num_gtes_per_gt *
  grain_size_sectors))`. Phase 6 computes this for the new
  capacity and compares to the existing region's slack.
- **Sector convention**: capacity is **sectors** (512 bytes
  each), not bytes. Both the binary header field and the
  descriptor's `RW <number> SPARSE` line use sector counts.
  Resize must update both.
- **GD region slack**: the GD region is `gd_sectors *
  512 / 4 = gd_sectors * 128` entries. For a fresh image
  with `num_gd_entries` entries, `gd_sectors =
  ceil(num_gd_entries * 4 / 512)`. Slack = `gd_sectors *
  128 - num_gd_entries`.
- **`VmdkSubformat` enum** lives in `crates/create::lib.rs`
  (line 234) with `MonolithicSparse`, `StreamOptimized`,
  `MonolithicFlat`, `TwoGbMaxExtentSparse`,
  `TwoGbMaxExtentFlat`. The resize crate's
  `VmdkSubformat` (in `src/crates/resize/src/lib.rs`) mirrors
  the same variants. Phase 6 supports `MonolithicSparse` only.
- **`plan_vmdk` (create crate)** at line 486 produces a fresh
  monolithicSparse image with: header @ sector 0, descriptor
  @ sectors 1–20, GD @ sector 21+, grain data after. Default
  `grain_size = 65536` (128 sectors), `num_gtes_per_gt = 512`.
- **Phase 5's `vhdx::plan_grow`** is the structural template:
  small `decide_action` enum, separate planners per flavour,
  stage-then-emit borrow discipline.

## Algorithmic design

### Layout-diff: `VmdkGrowAction`

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum VmdkGrowAction {
    /// New `num_gd_entries` fits in the existing GD region;
    /// only header.capacity + descriptor's extent line change.
    MetadataOnly,
    /// New `num_gd_entries` exceeds the existing GD region's
    /// capacity; relocate the GD to end of file.
    GdGrowRelocate,
}
```

The decision: compute `new_num_gd_entries` from the new
capacity. Compare against `current_gd_capacity_entries =
current_gd_sectors * 128`. If
`new_num_gd_entries <= current_gd_capacity_entries`, use
`MetadataOnly`. Otherwise `GdGrowRelocate`.

For a fresh image with sub-order-of-magnitude grows (e.g.
1 GiB → 4 GiB at default grain) the GD region's natural slack
(rounded up to a 512-byte sector) usually accommodates the
new entries — `MetadataOnly` fires in the typical case.

### MetadataOnly path

Steps:
1. Parse the existing descriptor to extract `cid`,
   `parent_cid`, `create_type`, and the extent line's
   `filename` and `size_sectors`.
2. Build the rewritten header: write 8 bytes of new
   `capacity_sectors` at offset 12 of the header sector.
   Don't touch other header fields — phase 6 preserves
   `gd_offset_sectors`, `overhead_sectors`, etc.
3. Build the rewritten descriptor: rebuild the descriptor
   text with the same `cid`, `parent_cid`, `create_type`,
   `filename`, but the new `size_sectors`. Zero-pad to the
   descriptor region's full size (`descriptor_size_sectors *
   512`, typically 10 KiB).

Patches:
1. `Write { byte_offset: 12, bytes: <new capacity bytes (8) > }`
2. `Write { byte_offset: descriptor_offset_sectors * 512,
   bytes: <new descriptor bytes> }`

`total_file_size = current_file_size`.

### GdGrowRelocate path

Steps:
1. As above, parse the existing descriptor to preserve
   `cid` / `parent_cid` / `create_type` / `filename`.
2. Compute the new GD region size:
   `new_gd_sectors = ceil(new_num_gd_entries * 4 / 512)`.
3. Compute the new GD region's file offset:
   `new_gd_offset_sectors = current_file_size / 512`
   (append at EOF).
4. Build the new GD region bytes: copy the existing GD's
   bytes for entries `0..current_num_gd_entries`, then
   zero-fill the rest. The new entries default to
   `GTE_UNALLOCATED = 0`, indicating no GT has been
   allocated for that virtual range yet — reads return
   zeros via the parser's unallocated path.
5. Build the rewritten header with the new
   `capacity_sectors` AND the new `gd_offset_sectors`. The
   header still has other fields (grain_size,
   descriptor_offset, descriptor_size, num_gtes_per_gt,
   overhead) that stay the same.
6. Build the rewritten descriptor (same as MetadataOnly).

Patches:
1. `Append { byte_offset: new_gd_offset_sectors * 512,
   bytes: <new GD region bytes> }`
2. `Write { byte_offset: 0, bytes: <new header (512 bytes)> }`
3. `Write { byte_offset: descriptor_offset_sectors * 512,
   bytes: <new descriptor bytes> }`

`total_file_size = current_file_size + new_gd_sectors * 512`.

The old GD region's bytes become orphaned garbage in the data
region (no refcounts, no harm).

### Crash-safety invariant

For MetadataOnly:
- Phase A (prepare): descriptor rewrite (a 10 KiB write that
  is not atomic).
- Phase B (commit): header rewrite (8 bytes — small enough to
  be effectively atomic at the page-cache level).

A crash during the descriptor rewrite leaves a torn
descriptor; the header still reports the old capacity. The
descriptor's CID would still parse (text fields use simple
key=value scanning), but the extent line might be truncated.
qemu's parser would error out on a torn descriptor. This is
an acknowledged risk; the file is recoverable by overwriting
the descriptor with a known-good template (matches qemu's
behaviour — qemu has no journaling for VMDK).

For GdGrowRelocate the order is: Append new GD region first
(invisible until header updated), then rewrite header (which
points at the new GD), then rewrite descriptor. A crash
between the Append and the header rewrite leaves the new GD
unreferenced — the old GD is still the canonical one. A
crash between the header rewrite and the descriptor rewrite
leaves the file with the new capacity in the header but the
old extent size in the descriptor; the parser detects the
mismatch but the file is still readable at the old capacity.

The "atomic commit" for MetadataOnly is the 8-byte header
write at offset 12. For GdGrowRelocate it's the 512-byte
header write at offset 0 (the cluster boundary makes most
filesystems treat this as a single I/O).

### Descriptor format

The rewritten descriptor follows qemu's monolithicSparse
template:

```
# Disk DescriptorFile
version=1
CID=<hex>
parentCID=<hex>
createType="monolithicSparse"

# Extent description
RW <new_capacity_sectors> SPARSE "<filename>"

# The disk Data Base
#DDB

```

Lines end with `\n`. The whole descriptor is zero-padded to
`descriptor_size_sectors * 512` bytes (typically 10 KiB).
DDB lines (geometry, adapterType) are NOT emitted by phase 6
— qemu treats them as optional and the create crate's
`build_descriptor` doesn't emit them either.

### `format_monolithic_sparse_descriptor` helper

Lives in `src/crates/resize/src/vmdk.rs`. Signature:

```rust
fn format_monolithic_sparse_descriptor(
    buf: &mut [u8],
    cid: u32,
    parent_cid: u32,
    create_type: &[u8],
    filename: &[u8],
    capacity_sectors: u64,
) -> Result<usize, ResizeError>;
```

Implementation uses `core::fmt::Write` over a tiny
`BufWriter<'_>` adapter (an inline struct that wraps a
`&mut [u8]` cursor). Zero-fills the trailing region. Returns
the byte count written.

### CHS geometry in the descriptor

`ddb.geometry.cylinders` / `heads` / `sectors` are optional;
neither `crates/create::plan_vmdk` nor `vmdk::build_descriptor`
emits them. Phase 6 follows the same precedent — no DDB
emission. If a future user complains, add them as a follow-up
that computes standard IDE geometry from capacity.

### `CID` bump on resize

qemu-img does NOT bump the self CID on grow for monolithicSparse
(verified by reading qemu's vmdk_co_truncate in src). The CID
changes only when the image is overwritten as a snapshot
target. Phase 6 preserves CID verbatim.

## Public API delta from phase 5

```rust
pub struct VmdkResizeOpts<'a> {
    // ... existing phase-1 fields ...
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub grain_size: u32,
    pub subformat: VmdkSubformat,
    pub allow_shrink: bool,
    pub preallocation: Preallocation,
    // ↓ added in phase 6 ↓
    /// Existing 512-byte sparse extent header bytes (sector 0).
    /// The planner reads `capacity_sectors`, `grain_size_sectors`,
    /// `num_gtes_per_gt`, `descriptor_offset_sectors`,
    /// `descriptor_size_sectors`, `gd_offset_sectors`, and
    /// `overhead_sectors` from here.
    pub existing_header: &'a [u8],
    /// Existing descriptor bytes (typically 10 KiB starting at
    /// `descriptor_offset_sectors * 512`). The planner parses
    /// CID, parentCID, createType, and the extent line's
    /// filename / size_sectors from here.
    pub existing_descriptor: &'a [u8],
    /// Existing GD region bytes. Used by GdGrowRelocate to
    /// preserve the old entries when relocating; ignored by
    /// MetadataOnly. The slice's length must be at least
    /// `current_num_gd_entries * 4`.
    pub existing_gd: &'a [u8],
    /// Current `num_gd_entries` (decoded from the existing
    /// header by the guest's pre-pass).
    pub current_num_gd_entries: u32,
    /// Current GD region size in sectors (the number of
    /// sectors the GD region occupies in the file, used for
    /// the slack computation).
    pub current_gd_sectors: u32,
    /// Current file size in bytes (pre-resize EOF). The
    /// relocate path appends a new GD region here.
    pub current_file_size: u64,
}
```

## Test matrix

| Test name | Setup |
|-----------|-------|
| `metadata_only_grow_when_gd_has_slack` | 1 GiB → 4 GiB at default grain (64 KiB). New `num_gd_entries = 128`, fits in the 1-sector GD region (128 entries). |
| `gd_grow_relocate_when_entries_exceed_slack` | small grain (4 KiB), grow that pushes `num_gd_entries` beyond 1 sector capacity (>128 entries). |
| `noop_when_sizes_equal` | NoOp action; empty patches. |
| `metadata_only_preserves_cid_and_parent_cid` | Forge a non-default `CID=12345678 parentCID=87654321` in the starting descriptor; after resize verify both round-trip. |
| `metadata_only_preserves_filename` | Starting descriptor has a non-default filename; verify it's preserved verbatim. |
| `header_capacity_field_updated` | After resize, the header's u64 at offset 12 decodes as `new_virtual_size / 512`. |
| `descriptor_extent_size_updated` | After resize, the extent line's sector count matches the new capacity. |

Negative paths:

| Test name | Setup |
|-----------|-------|
| `rejects_shrink_without_flag` | new < current → ShrinkWithoutFlag. |
| `rejects_shrink_with_flag` | new < current + --shrink → UnsupportedShrink (deferred). |
| `rejects_stream_optimized_subformat` | StreamOptimized → UnsupportedSubformat. |
| `rejects_monolithic_flat_subformat` | MonolithicFlat → UnsupportedSubformat. |
| `rejects_two_gb_max_extent_subformats` | TwoGbMaxExtentSparse / TwoGbMaxExtentFlat → UnsupportedSubformat. |
| `rejects_zero_new_virtual_size` | new = 0 → InvalidNewVirtualSize. |
| `rejects_preallocation_metadata` | Preallocation::Metadata → PreallocationUnsupported (no VMDK metadata-mode equivalent). |
| `rejects_invalid_existing_header` | corrupted header bytes → ParseFailed. |
| `rejects_invalid_existing_descriptor` | descriptor missing CID/extent line → ParseFailed. |

## Open questions

1. **Capacity rounding**. qemu rounds `new_virtual_size` up to
   the next grain boundary. The new `capacity_sectors` is
   then `ceil(new_virtual_size / grain_size_bytes) *
   grain_size_sectors`. Match qemu.

2. **Grain-size validation**. The existing image's grain size
   comes from opts and we treat it as authoritative. We don't
   change grain size on resize. Reject if `grain_size` isn't
   a power of two in [4 KiB, 64 KiB] (matches create's
   validation).

3. **Descriptor preservation vs rebuild**. The planner
   rebuilds the descriptor text rather than text-editing it
   in place. Rationale: text editing requires line/offset
   tracking and a "shift remaining bytes" pass that's
   fiddly in `no_std`. Rebuilding is deterministic and
   easy to test. Cost: any non-default fields beyond CID /
   parentCID / createType / filename get dropped (e.g.
   `ddb.geometry.*`). For phase 6 this is acceptable —
   qemu-created images would lose their DDB geometry, but
   qemu accepts descriptors without DDB. Document in
   `docs/quirks.md`.

4. **Filename in the extent line**. The extent line's
   filename is preserved verbatim from the existing
   descriptor. If the user renames the file on disk, the
   descriptor's filename doesn't auto-update — same as
   create, same as qemu.

5. **The 1-sector GD ceiling**. At default grain (64 KiB)
   and default `num_gtes_per_gt` (512), one GD sector holds
   128 entries = 128 * 32 MiB = 4 TiB of virtual coverage.
   So MetadataOnly covers essentially every realistic
   grow. GdGrowRelocate is rarely reached in practice; for
   test coverage we use a small grain (4 KiB) to force it.

6. **`overhead_sectors` field**. The header's `overhead`
   points at the first grain data sector — typically right
   after the GD region. For GdGrowRelocate, the old GD's
   sectors become unreferenced but the header's
   `overhead_sectors` still points at them. The parser
   uses `overhead_sectors` only to know "where grain data
   starts", which is unchanged because the data clusters
   are still at the same offsets. Leave overhead unchanged.

7. **Sequence number / commit point**. VMDK has no sequence-
   number protocol like VHDX. The atomic commit is the
   header's `capacity_sectors` write (the 8-byte u64 at
   offset 12 of sector 0). Filesystem-level torn writes
   for an 8-byte aligned write are extremely unlikely on
   any modern filesystem.

8. **Descriptor region size**. The existing image's
   descriptor region is `descriptor_size_sectors * 512`
   bytes (typically 10 KiB / 20 sectors). The rewritten
   descriptor zero-pads to that exact size. If the new
   descriptor text is longer than the region's capacity
   (extremely unlikely — descriptors are < 1 KiB), return
   `ScratchTooSmall`.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Extend `VmdkResizeOpts` in `src/crates/resize/src/lib.rs` with the 6 new fields documented in the "Public API delta" section plus a lifetime parameter (mirroring phase 5's VhdxResizeOpts pattern). Drop the now-stale stub assertions in the inline test in `lib.rs` and in `tests/round_trip.rs` (VMDK gets dedicated coverage in `tests/vmdk_grow.rs`, added in 6c). Create an empty private module `src/crates/resize/src/vmdk.rs` with a `pub(crate) fn plan_grow` returning `Err(UnsupportedFormat)`. Wire `plan_resize_vmdk` to dispatch into it. Add `vmdk = { path = "../vmdk" }` to both `[dependencies]` and `[dev-dependencies]` in `src/crates/resize/Cargo.toml`. `make instar`, `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |
| 6b | high | opus | worktree | Implement `vmdk::plan_grow` in `src/crates/resize/src/vmdk.rs`. Internal structure mirrors phase 5: `VmdkGrowAction` enum (`MetadataOnly`, `GdGrowRelocate`), `decide_action`, `plan_metadata_only`, `plan_gd_grow_relocate`. Add a private `format_monolithic_sparse_descriptor` that wraps `core::fmt::Write` over a small `BufWriter` adapter to emit the descriptor text into a buffer. Validate: reject non-`MonolithicSparse` subformats with `UnsupportedSubformat`; reject `new == 0` (InvalidNewVirtualSize); reject `new < current` (ShrinkWithoutFlag or UnsupportedShrink depending on `allow_shrink`); reject `Preallocation::Metadata`; reject invalid `grain_size`. Parse the existing descriptor via `vmdk::parse_descriptor` and `vmdk::parse_descriptor_extents` to extract CID / parentCID / createType / filename / current capacity (and cross-check current capacity against `opts.current_virtual_size`). Use phase 2c's stage-then-emit idiom to avoid borrow conflicts. Add inline unit tests for `decide_action`, the descriptor-format helper, and the new-capacity rounding. Risky: worktree isolation. |
| 6c | medium | sonnet | none | Add `src/crates/resize/tests/vmdk_grow.rs` mirroring `tests/vhdx_grow.rs`'s pattern. Use `crates/create::plan_vmdk` to build starting monolithicSparse images, populate `VmdkResizeOpts` from the parsed header / descriptor / GD, apply the patches via the `apply_resize` helper pattern, re-parse and assert the new geometry. Cover every positive and negative row from the "Test matrix" section. `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |

## Out of scope for phase 6

- VMDK shrink. Add to Future work (the master plan's list
  already includes it).
- StreamOptimized / multi-file subformats. Rejected with
  UnsupportedSubformat; future work tracks them under the
  "VMDK multi-file" entry already in the master plan.
- DDB geometry emission. Optional in qemu; documented
  divergence in `docs/quirks.md` (phase 13).
- Guest binary / host CLI / call-table changes / protobuf
  (phases 7 and 8).
- Preallocation modes other than `Off` (the post-pass
  `falloc` / `full` modes layer on top in phase 9; VMDK
  doesn't have a qcow2-style metadata-mode).

## Success criteria for phase 6

- `cargo build -p resize` clean.
- `cargo test -p resize` and `cargo test -p resize --tests`
  pass; the new vmdk unit tests (~4) and the new vmdk
  integration tests (~10) raise the total.
- All prior resize tests continue to pass.
- `make instar` builds.
- `make check-binary-sizes`, `make lint`, `pre-commit run
  --all-files` all clean.
- `plan_resize_vmdk` for a grow request returns a valid
  `ResizePlan` for every positive-path test case, with
  patches in the documented order.
- For round-trip tests: post-resize file parses correctly
  via `vmdk::Vmdk4HeaderFull::parse` and the
  `parse_descriptor`/`parse_descriptor_extents` chain, with
  the new capacity surfaced through both binary header and
  descriptor.

## Sub-agent guidance

Read these files before starting any step:

- `src/crates/vmdk/src/lib.rs:100-340` (header parser +
  builders).
- `src/crates/vmdk/src/lib.rs:408-510` (`Vmdk4HeaderFull`
  with `num_gd_entries`, populated-entry helpers).
- `src/crates/vmdk/src/lib.rs:1110-1360` (descriptor parser
  + extent-line parser).
- `src/crates/create/src/lib.rs::plan_vmdk` (the
  freshly-created monolithicSparse layout).
- `src/crates/resize/src/vhdx.rs` (the structural template
  including the stage-then-emit borrow discipline).
- `src/crates/resize/tests/vhdx_grow.rs` (the integration
  test template).

The management session review checklist is the same as prior
phases (read the diff, run lint/tests/pre-commit, check that
the patch ordering invariants hold).
