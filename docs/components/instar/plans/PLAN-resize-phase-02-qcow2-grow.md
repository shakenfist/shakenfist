# PLAN-resize phase 2: qcow2 grow planner

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2 metadata layout, refcount-table
sizing, atomic header swap, `qemu-img resize`), research as
needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that master
plan for overall context, mission, and the multi-phase plan
structure. Phase 1 (skeleton + raw + shared types) is complete;
the type surface this phase plugs into lives in
`src/crates/resize/src/lib.rs`.

## Mission

Replace the `UnsupportedFormat` stub in `plan_resize_qcow2()`
(`src/crates/resize/src/lib.rs:419`) with a real qcow2 **grow**
planner that emits a [`ResizePlan`] of byte-level patches
sufficient to:

1. Extend the file's reachable virtual size up to `new_virtual_size`,
   covering the simple case (new virtual size fits within the
   existing L1's L2 coverage; only the header changes) and the
   harder cases (L1 table extension; refcount-table extension;
   both).
2. Preserve every existing allocated cluster (the round-trip
   contract: writing a recognisable pattern before resize and
   reading it back after must succeed).
3. Pass `instar check` and `qemu-img check` after the patches are
   applied.
4. With `preallocation=metadata`, pre-populate L2 tables and zero
   data clusters for the newly-addressable virtual range so reads
   in that range short-circuit the L1=0 fast path. With
   `preallocation=off`, the new range is sparse and reads through
   the zero-cluster path.

This phase ships **qcow2 grow only**. Shrink lands in phase 3.
The non-qcow2 planners stay stubbed.

## What the survey turned up

The shape of the survey work that informs this plan; cite by
file:line so the implementing sub-agent can re-read in context:

- **`Qcow2Layout`** (`src/crates/qcow2/src/create.rs:127-177`) is
  the load-bearing reusable. Fields: `cluster_bits`,
  `cluster_size`, `virtual_size`, `refcount_bits`, `extended_l2`,
  `preallocation`, `l1_offset`, `l1_entries`, `l1_size_bytes`,
  `l1_clusters`, `refcount_table_offset`,
  `refcount_table_clusters`, `refcount_block_count`,
  `refcount_blocks_base_offset`, `l2_clusters`, `l2_base_offset`,
  `data_clusters`, `data_base_offset`, `total_clusters`,
  `total_file_size`.
- **`compute_layout()`** (`src/crates/qcow2/src/create.rs:191-297`)
  does the fixed-point refcount-table sizing (16 iterations;
  inner loop at lines 255-265). Validates virtual_size,
  cluster_bits, refcount_bits. Rejects `extended_l2 +
  non-Off-preallocation` (subcluster bitmap population for
  prealloc is deferred). Phase 2 reuses this for the *target*
  layout; the *current* layout is read out of the existing
  header.
- **`build_header()`** (`src/crates/qcow2/src/create.rs:329-415`)
  takes `BuildHeaderOptions` with the layout's offsets/sizes as
  parameters, so resize can call it with a layout whose
  `l1_offset` / `refcount_table_offset` differ from "fresh"
  defaults. Writes header extensions (backing format / encrypt /
  end). For resize, the planner uses `build_header` to produce
  the new header bytes; the patch is a `ResizePatch::Write` at
  offset 0 with `header_length` bytes.
- **`build_l1_table()`** (`src/crates/qcow2/src/create.rs:424-446`)
  zero-fills the L1 (or populates L2 offsets in prealloc modes).
  Phase 2 **cannot** call it directly for resize because resize
  must *preserve* existing L1 entries — instead, the planner
  copies `opts.existing_l1_bytes` into the start of the new L1
  buffer and zero-pads the tail. For prealloc=metadata, the
  newly-added L1 entries are populated with the offsets of the
  newly-allocated L2 tables (the existing tail).
- **`build_refcount_block()`** (`src/crates/qcow2/src/create.rs:538-566`)
  populates every entry from 0..total_clusters with refcount=1.
  Phase 2 calls this for the **fresh** refcount block when the
  refcount table grows past its old bounds (the new blocks cover
  newly-allocated clusters from end-of-old-file through new EOF);
  it does **not** rewrite blocks that exclusively cover the
  preserved old clusters, because their entries are already
  correct.
- **`build_refcount_table()`** (`src/crates/qcow2/src/create.rs:514-530`)
  writes 8-byte entries pointing at refcount blocks. Phase 2
  always builds a fresh refcount-table region (an Append) when
  the refcount table grows; otherwise it patches the
  newly-added pointer entries in the existing refcount table.
- **`QcowHeader::parse()`** (`src/crates/qcow2/src/lib.rs:334-410`)
  is the entry point. Fields phase 2 needs from the parsed
  header: `cluster_bits`, `cluster_size`, `virtual_size`,
  `l1_size` (entries), `l1_table_offset`, `refcount_table_offset`,
  `refcount_table_clusters`, `refcount_bits`,
  `incompatible_features`, `extended_l2`. Phase 2 must reject
  resizes of images with `INCOMPAT_EXTERNAL_DATA` (bit 2;
  `src/crates/qcow2/src/lib.rs:70`) or
  `INCOMPAT_COMPRESSION` (bit 3) or any unknown incompat bit;
  `INCOMPAT_DIRTY` (bit 0) and `INCOMPAT_CORRUPT` (bit 1) should
  trigger a "run instar check first" error.
- **`create::plan_qcow2()`** (`src/crates/create/src/lib.rs:315-442`)
  is the structural template: validate options, call
  `compute_layout`, carve scratch, call the builders, emit
  patches. Phase 2's `plan_resize_qcow2` follows the same
  shape but with two layouts (old + new) and patch types
  matching the resize-specific diff.

## Algorithmic design

### Layout diff: `Qcow2GrowAction`

Three growth flavours emerge from a (current_layout, new_layout)
pair. They are decided up-front by a private helper:

```rust
enum Qcow2GrowAction {
    /// new_virtual_size fits inside the existing L1's L2
    /// coverage and the refcount-table doesn't need more
    /// blocks. Only the header.size field changes; one
    /// patch.
    HeaderOnly,

    /// L1 needs more entries (new_l1_entries > l1_size) but
    /// the existing refcount table can still accommodate
    /// the new clusters from the new L1 region (no new
    /// refcount blocks needed). The new L1 region is
    /// appended; refcount entries for it land in existing
    /// refcount blocks.
    L1Grow,

    /// L1 grows AND the refcount table needs more blocks
    /// (possibly also a larger refcount table itself). The
    /// full algorithm runs.
    L1AndRefcountGrow,
}
```

For `preallocation=metadata`, an orthogonal flag in the layout
diff triggers L2-table population and zero-data-cluster
allocation regardless of which action fires.

### The HeaderOnly case

`new_virtual_size > current_virtual_size`, and the new L1 entry
count is `<= header.l1_size`. This happens when the existing L1
already addresses more virtual bytes than the user is asking
for (L1 has slack from a previous resize, or qemu allocated a
generously-sized L1 at create time).

Patches:
1. `Write { byte_offset: 0, bytes: <new header bytes> }` — the
   new header is identical to the old except for the `size`
   field at offset 24 (8 bytes).

`total_file_size` is unchanged from current EOF. `action` is
`Grow`. Two-patch alternative: a single 8-byte write at offset
24 with the new `virtual_size` in big-endian. We choose to
rewrite the **entire header_length** bytes (`build_header`'s
natural output) so future field additions (e.g. a hypothetical
`resize_count` field) Just Work.

### The L1Grow case

`new_l1_entries > header.l1_size`, but `new_refcount_blocks ==
current_refcount_blocks`. The new L1 region must be appended at
end of file; the refcount entries for the new L1 clusters land
in existing refcount blocks (we patch those entries).

Steps:

1. Allocate cluster region at current EOF for the new L1:
   `new_l1_clusters = ceil(new_l1_entries * 8 / cluster_size)`.
2. Build new L1 bytes: copy `opts.existing_l1_bytes` to the
   start of the buffer; zero-pad to `new_l1_clusters *
   cluster_size`.
3. For each cluster `c` in `[old_file_clusters .. old_file_clusters
   + new_l1_clusters)`, identify the refcount block that covers
   `c` and the entry index within that block. Stage the patched
   refcount-block bytes in scratch (reading the existing block
   from `opts.existing_refcount_blocks` — see open question 2
   below — and incrementing the relevant entries from 0 to 1).
4. Emit patches in order:
   - `Append { byte_offset: old_eof, bytes: <new L1 region> }`
   - For each affected refcount block: `Write { byte_offset:
     <block offset>, bytes: <patched block bytes> }`
   - `Write { byte_offset: 0, bytes: <new header bytes> }` —
     the new header points `l1_table_offset = old_eof`,
     `l1_size = new_l1_entries`, `size = new_virtual_size`.
   - For each cluster `c` in `[old_l1_first_cluster ..
     old_l1_last_cluster + 1)`: a `Write` patch decrementing the
     refcount entry for `c` from 1 to 0. These patches MUST come
     after the header rewrite (see open question 3 on the
     ordering invariant).

`total_file_size` = `old_eof + new_l1_clusters * cluster_size`.

### The L1AndRefcountGrow case

The full algorithm. `new_l1_entries > header.l1_size` AND
`new_refcount_blocks > current_refcount_blocks`.

The fixed-point iteration in `compute_layout` already converged
on the right block / table sizing for the target layout, so the
planner doesn't iterate again — it consumes the
`Qcow2Layout::refcount_block_count` and `refcount_table_clusters`
values directly.

Steps:

1. Compute target layout via `compute_layout(new_virtual_size,
   cluster_bits, refcount_bits, extended_l2,
   Preallocation::Off)`. Read off `l1_clusters`,
   `refcount_table_clusters`, `refcount_block_count`.
2. Decide where new metadata lives. Strategy: **always append**
   the new L1 region, new refcount table region, and new
   refcount blocks at the end of file. This avoids "is there
   free space after the existing refcount table" edge cases
   and matches qemu's behaviour for non-trivial grow.
3. Allocation order (each at the next available cluster after
   old EOF):
   - new L1 region: `new_l1_clusters` clusters
   - new refcount table region: `new_refcount_table_clusters`
     clusters (if `new_refcount_table_clusters >
     current_refcount_table_clusters`; else this is empty)
   - new refcount blocks: `(new_refcount_block_count -
     current_refcount_block_count)` clusters
4. Build buffers:
   - new L1 buffer: old L1 entries + zeros (as L1Grow above).
   - new refcount table buffer: 8 bytes per block pointer.
     For block indices `0..current_refcount_block_count`, the
     entries point at the EXISTING refcount block offsets
     (read out of the current refcount table). For block
     indices `current_..new_`, the entries point at the just-
     allocated refcount-block clusters.
   - new refcount block buffers: one per appended block. Each
     block's entries cover its slice of the cluster-number
     space; for clusters in `[old_file_clusters ..
     new_total_clusters)` the entry is 1 (those clusters are
     allocated by this resize). For clusters past
     `new_total_clusters` the entry is 0.
5. Patch existing refcount blocks (the ones that span the new
   L1's first cluster, the new refcount-table region's first
   cluster, etc.) by reading them from scratch, incrementing
   the relevant entries, and emitting `Write` patches.
6. Emit patches in order:
   - `Append` for new L1 region
   - `Append` for new refcount table (if grown)
   - `Append` for new refcount blocks
   - `Write` patches for any existing refcount-block updates
     (refcount=1 for new clusters that fall into existing
     blocks)
   - `Write` patch for the new header (points at new L1, new
     refcount table, new sizes)
   - `Write` patches for old-L1 refcount decrements (entries
     in either the new or old refcount blocks, depending on
     where the old L1's clusters landed in the cluster space —
     usually they're in old refcount blocks because the L1
     was small and at low offset)
   - `Write` patches for old refcount-table refcount decrements
     (only if the refcount table moved)

`total_file_size` = new EOF after all the appends.

The key invariant: **everything before the header rewrite is
the new state; the header rewrite atomically commits it;
everything after the header rewrite is cleanup of orphaned
clusters**. A crash before the header rewrite leaves the file
indistinguishable from pre-resize (the new metadata is
unreachable and reads as junk that the parser ignores). A crash
between header rewrite and cleanup leaves the file usable but
with leaked clusters (`instar check` flags them; `instar check
--repair` will fix them once that ships).

### The preallocation=metadata layer

When `opts.preallocation == Preallocation::Metadata`, the
planner emits additional patches BEFORE the header rewrite:

1. Allocate L2 tables for each new L1 entry: append
   `(new_l1_entries - old_l1_entries)` L2 clusters at end of
   file. Each L2 table is zero-filled because every data
   cluster is the all-zeros cluster.
2. Populate new L1 entries with the offsets of the just-
   allocated L2 tables.
3. Allocate refcount entries for the L2 clusters (same pattern
   as the L1 region in L1Grow: increment entries in existing
   refcount blocks if they cover the L2 region, else write
   into appended refcount blocks).
4. Skip the zero-data-cluster allocation. qemu does allocate
   zero data clusters with `preallocation=metadata`, but we
   match qemu only on info-equivalence: with prealloc=metadata,
   `qemu-img info`'s `disk size` includes the zero data
   clusters. Phase 9 owns whether instar matches that
   precisely or accepts the divergence; phase 2 emits the L2
   tables only.

Note: `extended_l2 + preallocation=metadata` is rejected by
`compute_layout` (it returns `UnsupportedSubclusterPrealloc`);
phase 2 mirrors that rejection and returns
`ResizeError::PreallocationUnsupported`.

### Crash-safety ordering invariant

Encoded as a planner-internal assertion (debug build only):
patches partition into three contiguous segments by index:

```
[ prepare patches (Append + Write to refcount entries) ]
[ header rewrite (single Write at offset 0)            ]
[ cleanup patches (Write to old-L1 refcount entries)   ]
```

The planner constructs them in that order and never reorders.
The guest applies them in order. Phase 7's call-table
implementation makes this concrete (every patch flushes before
the next is issued, modulo virtio batching; the header rewrite
must fit in one virtio descriptor so the OS treats it as
atomic).

## Public API delta from phase 1

```rust
// src/crates/resize/src/lib.rs

// New: existing-refcount-table bytes for the planner to walk
// when computing per-block patches without re-reading via the
// call table (the guest reads the table into scratch before
// calling).
pub struct Qcow2ResizeOpts<'a> {
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub cluster_size: u32,
    pub refcount_bits: u8,
    pub extended_l2: bool,
    pub preallocation: Preallocation,
    pub allow_shrink: bool,
    pub existing_l1_bytes: &'a [u8],
    // ↓ added in phase 2:
    /// Existing refcount-table bytes (current_refcount_table_clusters
    /// * cluster_size). Read by the guest from the file into
    /// scratch before calling the planner.
    pub existing_refcount_table_bytes: &'a [u8],
    /// Snapshot of the *existing* refcount blocks that the
    /// planner may need to patch (those that span the new L1
    /// region's first cluster). The guest reads only the blocks
    /// it'll patch (decided by a pre-pass) into this slice. If
    /// the planner asks for a block not present here, it returns
    /// ResizeError::ScratchTooSmall — phase 7's guest is then
    /// responsible for re-running with more blocks staged.
    pub existing_refcount_block_bytes: &'a [u8],
    /// Stride for indexing existing_refcount_block_bytes: each
    /// block is `cluster_size` bytes; entries are stored in the
    /// same order their cluster indices appear in the refcount
    /// table.
    pub current_file_size: u64,
    /// Current header.l1_size (entries, not bytes).
    pub current_l1_entries: u32,
    /// Current header.l1_table_offset.
    pub current_l1_table_offset: u64,
    /// Current header.refcount_table_offset.
    pub current_refcount_table_offset: u64,
    /// Current header.refcount_table_clusters.
    pub current_refcount_table_clusters: u32,
    /// Current header.incompatible_features (so the planner can
    /// reject INCOMPAT_EXTERNAL_DATA / INCOMPAT_COMPRESSION
    /// without reading the header itself; the guest passes this
    /// through).
    pub current_incompatible_features: u64,
}
```

Rationale for the new fields: the planner is a pure function on
`(opts, scratch)`. It cannot perform I/O. So every piece of
existing-file state the planner needs must arrive via opts. The
guest binary (phase 7) is responsible for reading the right
bytes into scratch first. This keeps the planner unit-testable
without any I/O scaffolding.

## Test matrix

Mirroring `src/crates/create/tests/round_trip.rs::sweep_qcow2`,
adapted for grow:

| Test name | Setup |
|-----------|-------|
| `grow_header_only_small_to_within_l1_coverage` | start 1 MiB, end 64 MiB, default cluster (64 KiB), default refcount_bits (16). Default L1 covers way more than 64 MiB, so HeaderOnly fires. |
| `grow_l1_extends_default_cluster` | start 1 GiB, end 16 GiB, default cluster. L1 grows; refcount table doesn't. |
| `grow_l1_and_refcount_default_cluster` | start 1 GiB, end 1 TiB, default cluster. Both grow. |
| `grow_small_cluster_512` | start 1 MiB, end 1 GiB, cluster_size=512. L1 grows steeply because L2 coverage is tiny at small clusters. |
| `grow_large_cluster_2mib` | start 1 GiB, end 1 TiB, cluster_size=2 MiB. L1 grows slowly. |
| `grow_extended_l2` | start 1 GiB, end 16 GiB, cluster_size=64 KiB, extended_l2=true. |
| `grow_refcount_bits_1` | start 1 GiB, end 1 TiB, refcount_bits=1. Forces more refcount-block churn (fewer entries per block). |
| `grow_with_existing_allocated_clusters` | start with a fixture image that has some clusters allocated (not just empty); grow it; verify the allocated clusters are still reachable from the new L1. |
| `grow_with_backing_file` | start with a qcow2 that has a backing file reference; grow; verify backing-file extension survives the header rewrite. |
| `grow_prealloc_metadata` | start 1 GiB, end 4 GiB, prealloc=metadata. Verify new L1 entries point at appended L2 tables. |
| `noop_when_new_eq_current` | start 1 GiB, end 1 GiB. Returns ResizeAction::NoOp with empty patches. |

Negative paths:

| Test name | Setup |
|-----------|-------|
| `rejects_external_data_file` | image with INCOMPAT_EXTERNAL_DATA → `UnsupportedFormat`. |
| `rejects_compression` | image with INCOMPAT_COMPRESSION → `UnsupportedFormat`. |
| `rejects_dirty` | image with INCOMPAT_DIRTY → `UnsupportedFormat` (or a new `RequiresCheckFirst` variant; see open question 5). |
| `rejects_shrink` | new < current → tested in phase 3, but the stub here returns `UnsupportedShrink` until phase 3 lands. |
| `rejects_prealloc_metadata_with_extended_l2` | extended_l2 + Preallocation::Metadata → `PreallocationUnsupported`. |
| `rejects_zero_new_virtual_size` | new = 0 → `InvalidNewVirtualSize`. |

For the "round-trip" assertion: materialise the patch sequence
onto a `Vec<u8>` starting from a fixture image's bytes, then
parse the result with `qcow2::QcowHeader::parse` and assert
the header's `size` / `l1_size` / `l1_table_offset` /
`refcount_table_clusters` match the expected new layout. For the
"existing data survives" assertion: walk the new L1 with the
helper that returns L2 entries, walk those, dereference the
cluster, and assert the pre-resize byte pattern is intact.

## Open questions

These should be answered during execution; if a sub-agent hits
them, escalate to the management session rather than guessing.

1. **Patching individual refcount blocks vs. rewriting them
   wholesale.** When the L1 extension adds new clusters that
   fall into an existing refcount block, the planner can
   (A) re-read that block, increment the affected entries, and
   emit a Write patch of the whole block; or (B) emit a Write
   patch of just the affected bytes (one entry, ~2 bytes for
   16-bit refcounts). Option B produces tighter patches; option
   A produces simpler ones. *Recommendation*: A. Patch granularity
   is bounded by sector size in the guest anyway (every write
   rounds up to 512-byte alignment), so the bandwidth difference
   is negligible. A is simpler to reason about for crash safety.

2. **How much of the existing refcount block region does the
   guest need to pass to the planner?** Worst case: every block
   that's affected by L1/refcount-table extension. For
   `preallocation=metadata` that's potentially the *entire*
   refcount block region, which can be megabytes. *Recommendation*:
   the guest does a pre-pass: walks the existing refcount table
   to identify which blocks contain entries the planner will
   modify (the clusters spanning old EOF through new EOF, plus
   the old L1 cluster span). Only those blocks are staged into
   `existing_refcount_block_bytes`. The planner records the
   block indices it expects in scratch metadata so the guest
   knows what to stage.

3. **Cleanup-patch ordering.** The crash-safety argument requires
   old-L1 refcount decrements to come AFTER the header rewrite.
   But putting them in the same `ResizePlan` patches list means
   the call-table's natural order has them after the header.
   Is that good enough, or do we need an explicit "barrier"
   patch? *Recommendation*: good enough. The call-table emits
   patches synchronously in order; each `write_output_sector`
   completes before the next is issued (modulo virtio batching
   inside the kernel, which is not user-visible). A separate
   barrier would only matter if we ran multiple patches in
   parallel, which we don't.

4. **Refcount-table relocation vs. in-place extension.** Cleaner
   to *always relocate* the refcount table (append) when it
   needs to grow, vs. trying to grow it in place (only possible
   when the bytes immediately after it are free). *Recommendation*:
   always relocate. Matches qemu's strategy for non-trivial
   grow. Costs `current_refcount_table_clusters` of leaked space
   (the old refcount table region) per grow that requires
   table-extension; tolerable.

5. **`INCOMPAT_DIRTY` / `INCOMPAT_CORRUPT` handling.** Should
   resize refuse with `UnsupportedFormat` (current error), or a
   more specific error pointing at `instar check`? *Recommendation*:
   add `ResizeError::RequiresCheckFirst` so the host can render
   a helpful message ("the image is marked dirty; run `instar
   check` first"). Append to the error enum; existing variants
   keep their discriminants.

6. **`current_file_size` in opts.** Does the planner need this,
   or can it derive it from
   `qemu_disk_size(current_l1_table_offset, current_l1_entries)`?
   *Recommendation*: include it explicitly. The file may have
   data clusters allocated past the metadata region (the common
   case for any image with writes), so the planner's "append
   here" offset is `current_file_size`, not derived. Also makes
   the planner robust against image files with trailing
   garbage past the last metadata cluster.

7. **L1 entry's `OFLAG_COPIED` bit during resize.** Existing L1
   entries copied into the new L1 region keep their flags (the
   `OFLAG_COPIED` bit at position 63 says "this L2 table is
   only referenced from this L1"). New L1 entries are zero (no
   L2 yet). For `prealloc=metadata`, the new L1 entries point
   at fresh L2 tables and the `OFLAG_COPIED` bit must be set
   on them. Verify against `qcow2::create::build_l1_table` —
   it sets OFLAG_COPIED for prealloc modes (line 442 region).

8. **Max appended bytes vs. `QCOW2_MAX_RESIZE_SCRATCH`.** A
   1 TiB grow with cluster_size=512 produces a multi-MiB L1
   region. The current `QCOW2_MAX_RESIZE_SCRATCH = 32 MiB`
   bound is plenty. Phase 2 should add an explicit assertion
   in the planner: if the new L1 bytes + new refcount-table
   bytes + new refcount-block bytes exceed scratch, return
   `ScratchTooSmall`. Tighten the constant in phase 2's final
   commit if profiling shows real-world worst case is smaller.

## Execution

Phase 2 splits into four sub-steps. Each step is a separate
sub-agent invocation. Land one commit per step. Step 2a is
prerequisite to 2b; 2b is prerequisite to 2c; 2d depends on all
three.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | opus | none | In `src/crates/resize/src/lib.rs`, extend `Qcow2ResizeOpts` with the new fields specified in the "Public API delta from phase 1" section above (`existing_refcount_table_bytes`, `existing_refcount_block_bytes`, `current_file_size`, `current_l1_entries`, `current_l1_table_offset`, `current_refcount_table_offset`, `current_refcount_table_clusters`, `current_incompatible_features`). Add a new error variant `ResizeError::RequiresCheckFirst` for dirty/corrupt images. In a new private module `src/crates/resize/src/qcow2.rs` (gated `#[cfg(feature = "qcow2")]` only if you add a feature; otherwise just declare it), add a `Qcow2GrowAction` enum with the three variants documented above and a private function `decide_action(current_layout, new_layout) -> Qcow2GrowAction`. No `plan_resize_qcow2` body change yet; the stub still returns UnsupportedFormat. Add the `qcow2 = { path = "../qcow2", features = ["create"], default-features = false }` dependency to `src/crates/resize/Cargo.toml`. Unit tests in `src/crates/resize/src/qcow2.rs` cover `decide_action` across the three flavours at representative sizes. `make instar`, `make lint`, `make test-rust` clean; `pre-commit run --all-files` clean. |
| 2b | high   | opus | worktree | Implement `plan_resize_qcow2` for the HeaderOnly and L1Grow cases. Reject L1AndRefcountGrow with `ScratchTooSmall` for now (raised in 2c) so unit tests exercise only the two simpler shapes. Flow: parse-don't-call, validate incompat bits, build `Qcow2Layout` for the target via `qcow2::create::compute_layout`, decide via `decide_action`, branch. HeaderOnly: build new header with `qcow2::create::build_header` using the EXISTING `l1_table_offset` / `refcount_table_offset` / `refcount_table_clusters` from opts and the NEW `virtual_size` / `l1_entries`; emit `ResizePatch::Write` at offset 0. L1Grow: stage new L1 buffer (copy `opts.existing_l1_bytes` + zero-pad), identify the refcount blocks that cover the new-L1 cluster span by walking `opts.existing_refcount_table_bytes`, patch them via `Write` patches (the existing refcount-block bytes for the affected blocks must be in `opts.existing_refcount_block_bytes`; if not, return `ScratchTooSmall`), Append the new L1 region, build the new header with updated offsets, emit the header `Write`, emit old-L1 refcount decrements as final `Write` patches. Reject `extended_l2 + Preallocation::Metadata` with `PreallocationUnsupported`. Reject `Preallocation::Metadata` entirely for now (lifted in 2c — actually phase 9; check the master plan; the metadata layer lands in step 2c). Add unit tests covering the per-row entries in the "Test matrix" table above for HeaderOnly and L1Grow rows. The "existing data survives" assertion uses a fixture from `instar-testdata` (or a synthetic in-test fixture: write a known cluster at L2 entry 5, run the planner's patches, walk the new layout, verify byte recovery). Risky: worktree isolation. |
| 2c | high   | opus | worktree | Extend `plan_resize_qcow2` to handle L1AndRefcountGrow and `Preallocation::Metadata`. L1AndRefcountGrow: append new L1 region, append new refcount-table region (if needed), append new refcount-block region; emit refcount-entry patches for the newly-allocated clusters; emit existing-refcount-block patches for any new clusters that fall into existing blocks; build and emit the new header; emit old-L1 and (if relocated) old-refcount-table decrement patches. `Preallocation::Metadata`: as L1Grow / L1AndRefcountGrow but additionally append L2 tables for new L1 entries (zero-filled — every data cluster is the all-zeros cluster), populate the new L1 entries with the L2-table offsets and OFLAG_COPIED, increment refcounts for the new L2 clusters. Reject `extended_l2 + Preallocation::Metadata` early. Add unit tests for the remaining rows in the "Test matrix" table. Verify the crash-safety ordering invariant in a debug-build assertion at the end of `plan_resize_qcow2` (patches partition into prepare / header / cleanup; assert the indices). Risky: worktree isolation. |
| 2d | medium | sonnet | none | Add an integration test file `src/crates/resize/tests/qcow2_grow.rs` exercising the full test matrix from the "Test matrix" table above (positive and negative paths). Each test materialises a synthetic starting image (the helper from `src/crates/create/tests/round_trip.rs` is the closest template — copy/adapt the materialise-plan-to-vec idiom), applies the resize patches, parses the result, asserts the expected post-resize layout. Tests run with `cargo test -p resize --tests`. `make lint` clean; `pre-commit run --all-files` clean. Update `src/crates/resize/src/lib.rs`'s doc comment on `plan_resize_qcow2` to reflect that grow is supported, shrink is still phase 3. |

## Out of scope for phase 2

- QCOW2 shrink (phase 3).
- Non-qcow2 planners (phases 4, 5, 6).
- Guest binary (phase 7).
- Host CLI (phase 8).
- Preallocation modes other than `Off` and `Metadata` (phase 9
  layers `Falloc` and `Full` host-side; phase 2 returns
  `PreallocationUnsupported` for those).
- Call-table changes (phase 7 adds `read_output_sector`).
- Documentation updates (phase 13).

## Success criteria for phase 2

- `cargo build -p resize` clean.
- `cargo test -p resize` and `cargo test -p resize --tests` pass
  with the new HeaderOnly / L1Grow / L1AndRefcountGrow tests and
  the prealloc=metadata test.
- `make instar` builds (no regression).
- `make check-binary-sizes` passes (no new operation binary in
  this phase; the existing ones still fit).
- `make lint` clean across the workspace.
- `pre-commit run --all-files` clean.
- `plan_resize_qcow2` returns a valid `ResizePlan` for every
  positive-path test case, with patches in the documented order
  (prepare → header → cleanup).
- A materialise-and-parse round trip recovers the expected
  `(virtual_size, l1_size, l1_table_offset,
  refcount_table_offset, refcount_table_clusters)` after every
  positive-path test case.
- For tests that start with allocated-cluster fixtures (the
  `grow_with_existing_allocated_clusters` and
  `grow_with_backing_file` cases), the pre-resize byte pattern
  is recoverable after applying the patches.
- The crash-safety partition invariant (prepare / header /
  cleanup) holds for every positive-path output.

## Sub-agent guidance

Read these files before starting any step:

- `src/crates/qcow2/src/create.rs` lines 127-566 (the layout
  and builders the planner consumes).
- `src/crates/qcow2/src/lib.rs` lines 34-130, 280-410, 460-520
  (the parser fields and header extension walker).
- `src/crates/create/src/lib.rs` lines 315-442 (`plan_qcow2`
  as the structural template).
- `src/crates/create/tests/round_trip.rs::sweep_qcow2` (the
  test template for the materialise-and-parse idiom).
- `src/operations/convert/src/main.rs` lines 1716-1740 (the
  reference refcount fixed-point implementation).
- The master plan `docs/plans/PLAN-resize.md` (specifically the
  "QCOW2 grow" subsection of "Per-format resize plans") for the
  algorithmic specification this phase implements.

For each step the management session will:

- Read the actual files (not just trust the diff summary).
- Run `cargo build -p resize`, `cargo test -p resize`,
  `cargo test -p resize --tests`, `make lint`, `pre-commit run
  --all-files`.
- For 2b and 2c: verify the patches list, ordering, and the
  crash-safety partition by reading a sample plan's output in
  the test harness.
- Confirm the success-criteria items above.
- Commit if green with the standard commit-message format.

If a step fails review (something unrelated changed, a test
regressed, the brief was misinterpreted), discard the sub-
agent's worktree where applicable and re-spawn with a refined
brief rather than patching by hand.
