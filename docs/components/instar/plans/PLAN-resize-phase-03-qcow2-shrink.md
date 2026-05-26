# PLAN-resize phase 3: qcow2 shrink planner

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2 metadata layout, refcount semantics,
`qemu-img resize --shrink`), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that master
plan for overall context and the multi-phase plan structure.
Phase 1 (skeleton + raw + shared types) and phase 2 (qcow2 grow
planner: HeaderOnly + L1Grow + L1AndRefcountGrow) are complete;
phase 3 plugs into the existing
`qcow2::plan_grow` dispatch (which currently returns
`UnsupportedShrink` for shrink requests).

## Mission

Replace the `UnsupportedShrink` early-return in
`src/crates/resize/src/qcow2.rs:plan_grow` with a real qcow2
**shrink** planner. The shrink planner:

1. Walks every L1 entry. For each non-zero L1 entry, walks the
   pointed-at L2 table to identify entries whose guest offset
   `>= new_virtual_size`. Marks those L2 entries (and the data
   clusters they point at) for discard. Tracks the highest
   still-allocated cluster's guest offset.
2. Refuses with `ResizeError::ShrinkBelowAllocated` if any L2
   entry pointing at an allocated data cluster has its guest
   offset `>= new_virtual_size` AND `opts.allow_shrink` is
   false. *(qemu refuses with the equivalent error message;
   the host CLI in phase 8 translates this to qemu-compatible
   wording.)*
3. With `--shrink`: for each discarded data cluster, emits a
   `Write` patch zeroing the L2 entry and a `Write` patch
   decrementing the refcount entry to 0. If an entire L2 table
   becomes all-zero, also zeros the corresponding L1 entry and
   decrements the L2-table cluster's refcount to 0.
4. Reduces `header.size` to `new_virtual_size`. Does NOT change
   `header.l1_size` or `header.l1_table_offset`; leaving the
   L1 region size unchanged matches qemu and avoids the
   "free unused L1 clusters" complication.
5. Does NOT truncate the file. Orphaned cluster space (the
   discarded data clusters) stays inside the file as dead bytes
   with refcount=0. Matches qemu exactly; documented in
   `docs/quirks.md` as part of phase 13.

This phase ships **qcow2 shrink only**. The non-qcow2 planners
remain stubbed; their shrink paths land in phases 4–6 (or stay
deferred per the master plan).

## What the survey turned up

- `qcow2::QcowHeader::parse` (`src/crates/qcow2/src/lib.rs:334`)
  surfaces every header field we need.
- `qcow2::walk_l2_standard` (`src/crates/qcow2/src/lib.rs:1110`)
  iterates an L2 table's entries with their virtual addresses
  — but it's count-oriented (it feeds a `TargetUnitTracker`).
  Shrink needs a different walk shape: "for each non-zero entry
  at byte offset `i*8`, what's the host cluster offset and the
  guest offset?" The shrink planner implements its own walk in
  `qcow2.rs` to keep the parser crate's contract small.
- `qcow2::L2_OFFSET_MASK = 0x00fffffffffffe00`
  (`src/crates/qcow2/src/lib.rs:118`) extracts the host cluster
  offset from a standard L2 entry. `qcow2::OFLAG_COMPRESSED` /
  `OFLAG_COPIED` are the flag bits that share the entry.
- Phase 2 left `Qcow2ResizeOpts` with eight new fields (existing
  L1 / refcount table / refcount-block snapshots, current
  geometry, backing reference). Shrink needs **L2-table
  snapshots** too. Add `existing_l2_bytes` /
  `existing_l2_indices` mirroring the refcount-block pattern.
- The guest's pre-pass that stages the planner inputs (phase 7
  work) needs to identify which L2 tables to stage. For shrink:
  the L2 tables whose L1 entry's covered range overlaps
  `[new_virtual_size, current_virtual_size)`. Concretely: the
  L2 at L1 index `i` covers virtual range
  `[i * cluster_size * entries_per_l2 ..
   (i + 1) * cluster_size * entries_per_l2)`; stage every L2
  whose range is non-disjoint from
  `[new_virtual_size, current_virtual_size)`.
- The existing `plan_grow` dispatch in
  `src/crates/resize/src/qcow2.rs:73-79` already returns
  `UnsupportedShrink` for the shrink path. Phase 3 replaces
  that early-return with a call into a new private
  `plan_shrink` function in the same module.

## Algorithmic design

### Inputs the planner expects

```rust
// Existing Qcow2ResizeOpts (from phase 2) plus two new fields:
pub struct Qcow2ResizeOpts<'a> {
    // ... existing fields ...
    /// Read-only snapshots of the L2 tables the planner may need
    /// to walk. The guest's pre-pass identifies which L2 tables
    /// cover virtual addresses in
    /// `[new_virtual_size, current_virtual_size)` and stages them
    /// here.
    pub existing_l2_bytes: &'a [u8],
    /// L1 indices of the L2 tables staged in `existing_l2_bytes`,
    /// in the same order. Block `i` lives in
    /// `&existing_l2_bytes[i * cluster_size .. (i + 1) * cluster_size]`.
    /// If the planner needs an L2 table not present here, it
    /// returns `ResizeError::ScratchTooSmall`.
    pub existing_l2_indices: &'a [u32],
}
```

### High-level flow

```text
plan_shrink(opts, scratch):
    boundary_cluster = new_virtual_size / cluster_size   # ceil-rounded for boundary inclusion
    entries_per_l2 = cluster_size / (extended_l2 ? 16 : 8)
    l2_coverage = cluster_size * entries_per_l2
    first_discarded_l1_idx = new_virtual_size.div_ceil(l2_coverage) as u32

    # The L1 entry whose range *straddles* new_virtual_size (if any)
    straddle_l1_idx = if new_virtual_size % l2_coverage == 0
                      { None } else { Some(first_discarded_l1_idx - 1) }

    # === Walk: identify what to discard ===
    discarded_data_clusters = []     # (host_offset)
    discarded_l2_clusters   = []     # (host_offset)
    l2_rewrites             = []     # (l1_index, l2_table_bytes_in_scratch)
    l1_zeros                = []     # (l1_index)

    # 1. L1 entries fully above new_virtual_size: zero them and
    #    free the L2 table cluster they pointed at.
    for i in first_discarded_l1_idx .. current_l1_entries:
        entry = read_be_u64(existing_l1_bytes, i * 8)
        if entry == 0:
            continue                                   # already unallocated
        host = entry & L2_OFFSET_MASK
        # Walk the L2 table to find allocated data clusters.
        if !opts.allow_shrink:
            l2_bytes = lookup_l2(opts, i)?
            if any_nonzero_entry(l2_bytes):
                return Err(ShrinkBelowAllocated)
            # else: L2 has no allocated clusters; OK to discard
            # the L1 entry without --shrink
        else:
            l2_bytes = lookup_l2(opts, i)?
            for j in 0 .. entries_per_l2:
                l2e = read_be_u64(l2_bytes, j * 8)
                if l2e == 0:
                    continue
                discarded_data_clusters.push(l2e & L2_OFFSET_MASK)
        discarded_l2_clusters.push(host)
        l1_zeros.push(i)

    # 2. The straddling L1 entry (if any): walk its L2 and
    #    discard entries above the boundary.
    if let Some(i) = straddle_l1_idx:
        entry = read_be_u64(existing_l1_bytes, i * 8)
        if entry != 0:
            l2_bytes = lookup_l2(opts, i)?
            base_virtual = i * l2_coverage
            entries_to_zero = []
            for j in 0 .. entries_per_l2:
                l2e = read_be_u64(l2_bytes, j * 8)
                v_start = base_virtual + j * cluster_size
                if v_start < new_virtual_size:
                    continue
                if l2e == 0:
                    continue
                if !opts.allow_shrink:
                    return Err(ShrinkBelowAllocated)
                discarded_data_clusters.push(l2e & L2_OFFSET_MASK)
                entries_to_zero.push(j)
            if !entries_to_zero.is_empty():
                # Stage the L2 table in scratch with the
                # relevant entries zeroed.
                staged = stage_l2_with_zeros(l2_bytes, entries_to_zero, scratch)?
                l2_rewrites.push((i, staged.offset))

    # === Emit patches in crash-safe order ===
    # No appends; no header-move. Phases A and B only — no
    # cleanup phase because nothing is "orphaned post-commit"
    # (the discarded clusters are visible-but-unreferenced
    # before and after; the header rewrite just shrinks the
    # virtual_size field).
    #
    # Phase A (prepare):
    #   - Rewrite straddling L2 (one Write per modified L2 table)
    #   - Rewrite L1 (one Write covering the zeroed L1 entries)
    #   - Decrement refcount entries for each discarded data
    #     cluster and each discarded L2 cluster (Write patches
    #     against existing refcount blocks)
    # Phase B (commit):
    #   - Rewrite header (new size)

    plan = ResizePlan::new(Shrink, current_file_size)
    for (i, l2_off) in l2_rewrites:
        plan.push(Write { byte_offset: l2_host_offset(i), bytes: scratch[l2_off..] })
    if !l1_zeros.is_empty():
        # Build a new L1 table with the relevant entries zeroed,
        # then Write the whole L1 region (it's small — typically
        # one cluster).
        plan.push(Write { byte_offset: l1_table_offset, bytes: scratch[l1_buf..] })
    for &cluster in (discarded_data_clusters ∪ discarded_l2_clusters):
        # Decrement refcount entry for the cluster
        block_idx = cluster_idx / entries_per_refblock
        patch_block_set_refcount_to_0(scratch, block_idx, local_idx, cluster_idx)
    # ... emit one Write per modified existing refcount block ...
    plan.push(Write { byte_offset: 0, bytes: scratch[header..] })  # commit
    Ok(plan)
```

### Crash-safety ordering

Shrink does not need a Phase C (cleanup), because no metadata is
relocated:

- Phase A — write the L2-entry zeroing, the L1-entry zeroing,
  and the refcount-block decrements. All disjoint byte ranges.
- Phase B — write the new header (atomic commit).

If the guest crashes between Phase A and Phase B: the L2 / L1
entries are zeroed (the cluster is no longer reachable from L1
walk), the refcounts are 0 (cluster is free for reuse), but
header.size is still the old size. A subsequent
`instar check` would see virtual_size=old but everything past
new_virtual_size is unreachable — no inconsistency, since reads
beyond the L1=0 path return zero. The image is effectively in
the "discarded but not committed" state. A second resize
attempt would re-walk and find nothing more to discard, then
commit the header.

If the guest crashes mid-Phase A (e.g. after some L2 rewrites
but before others): the partially-zeroed L2 entries still
point at clusters whose refcounts haven't been decremented
yet. Those clusters are leaked but not corrupted. `instar check`
would flag the leak; `instar check --repair` (future work)
fixes it.

### `extended_l2` handling

The 16-byte extended L2 entry format adds a subcluster bitmap.
For shrink:
- We must read the 16-byte entry pair and clear both
  `l2_entry` and `sc_bitmap` for discarded entries.
- The data cluster discard logic is the same (the host offset
  lives in `l2_entry & L2_OFFSET_MASK`).
- Walk loop iterates with stride 16, not 8.

Phase 2 handled extended_l2 in `compute_layout` validation only
(it accepts the bit; doesn't change the planner shape). Phase 3
handles extended_l2 in the walk explicitly. The straddling-L2
test matrix includes one extended_l2 case.

### Refcount-decrement granularity

Same call as phase 2's `set_refcount(block, local_idx, bits, 0)`.
The refcount blocks affected:
- The blocks covering the discarded data clusters.
- The blocks covering the discarded L2 table clusters (if any).

The guest stages these blocks before calling the planner (just
like phase 2's existing_refcount_block_bytes pattern). If a
block isn't staged but the planner needs to patch it, return
`ScratchTooSmall`.

## Public API delta from phase 2

```rust
pub struct Qcow2ResizeOpts<'a> {
    // ... existing fields from phase 2 ...
    /// Read-only snapshots of L2 tables the planner needs to
    /// walk for shrink (and, in a future phase, for backing-
    /// chain operations). The guest pre-pass identifies which
    /// L2 tables cover the discarded virtual range and stages
    /// them here.
    pub existing_l2_bytes: &'a [u8],
    /// L1 indices of the L2 tables staged in `existing_l2_bytes`,
    /// in the same order.
    pub existing_l2_indices: &'a [u32],
}
```

Two new fields, mirroring the refcount-block snapshot pattern.

## Test matrix

| Test name | Setup |
|-----------|-------|
| `shrink_within_single_l2_entry` | start 64 MiB, end 32 MiB. Both fit in L2 entry 0 → straddling case only; no L1 entries fully discarded. |
| `shrink_drops_multiple_l1_entries` | start 4 GiB, end 1 GiB. Default cluster → L1 covers 512 MiB per entry → L1 entries 2..8 fully discarded. |
| `shrink_drops_one_l1_and_straddles_another` | start 2 GiB, end 768 MiB. L1[1] straddles (covers 512 MiB to 1 GiB); L1[2..] would be the discarded range — actually since L1 only had 4 entries, just L1[1] straddles and L1[2..3] fully discard. Cleanest mid-case. |
| `shrink_extended_l2_path` | start 1 GiB extended_l2, end 256 MiB. Walk 16-byte entries; clear both halves. |
| `shrink_noop_when_l2_already_empty` | start 4 GiB, end 1 GiB, but every L2 in [1 GiB, 4 GiB) has no allocated entries — happens for freshly-created images. Verifies the "fast path" still emits the header rewrite and L1 zeroing but no refcount-decrement patches. |
| `shrink_to_one_cluster_minimum` | start 4 GiB, end 65536 (one cluster). Edge case for minimum virtual size. |
| `shrink_to_cluster_boundary_no_straddle` | start 2 GiB, end 512 MiB (exact L1-entry boundary at default cluster). No straddling case; only fully-discarded L1 entries. |
| `shrink_preserves_remaining_data` | Pre-write a recognisable pattern at L2 entry 5 of L1[0] (well below the shrink boundary). After resize, walk L1/L2 again, dereference, verify byte pattern intact. |

Negative paths:

| Test name | Setup |
|-----------|-------|
| `shrink_without_flag_when_clusters_above_size` | start 2 GiB with an allocated cluster at virtual address 1.5 GiB; shrink to 1 GiB without --shrink → `ShrinkBelowAllocated`. |
| `shrink_with_flag_proceeds` | Same setup; with --shrink → success; the cluster is discarded. |
| `shrink_to_zero_rejected` | new_virtual_size=0 → `InvalidNewVirtualSize`. |
| `shrink_below_minimum_cluster` | new_virtual_size=1 (less than one cluster) → `InvalidNewVirtualSize` (one cluster is the minimum). Actually — qemu rounds down to 0 if asked for less than a cluster; clarify in open question 4. |
| `missing_l2_in_staging` | Provide opts with `existing_l2_indices = []` even though an L1 entry is non-zero in the discarded range → `ScratchTooSmall`. |

## Open questions

1. **Cluster-boundary rounding of `new_virtual_size`.** qemu
   rounds `new_virtual_size` down to a cluster boundary before
   doing the shrink walk. *Recommendation*: match qemu exactly
   — round down to `(new_virtual_size / cluster_size) *
   cluster_size`. If the user asked for a non-cluster-aligned
   size, the planner records the rounded value in
   `ResizePlan::total_file_size` (well, in the header it
   writes). The host's success line shows the rounded value.

2. **What if `new_virtual_size` falls inside an existing
   cluster's data?** E.g. shrink to 1 MiB + 1 byte. The
   cluster containing that boundary byte is "partially within"
   the new range. qemu keeps the entire cluster (rounds up
   to cluster boundary effectively, so the data at the
   boundary is still readable). The planner does the same:
   the cluster containing `new_virtual_size - 1` stays; only
   clusters whose first byte is >= new_virtual_size are
   discarded.

3. **`extended_l2`'s subcluster bitmap and partial-cluster
   shrink.** If the boundary falls in the middle of a cluster's
   subcluster range, do we clear the affected subclusters in
   the bitmap? *Recommendation*: no, match the standard L2
   case — keep the entire cluster, even though some of its
   subclusters are above the new virtual size. Document as a
   deliberate qemu-parity choice.

4. **Minimum virtual size.** qemu refuses
   `new_virtual_size < cluster_size`. Match. Return
   `InvalidNewVirtualSize`.

5. **Internal snapshots.** qemu-img resize does NOT touch
   internal snapshots — their L1 tables (pointed at by
   snapshot extension entries) keep their old virtual_size.
   Match qemu; document in `docs/quirks.md`.

6. **Compressed clusters above the shrink boundary.** A
   compressed L2 entry has `OFLAG_COMPRESSED` set and a
   different bit layout (csize + host offset). For shrink,
   we still discard the entry (zero it) and decrement the
   refcount; the host cluster offset extraction differs
   (`L2_OFFSET_MASK` still applies, but the cluster is
   partial — only the compressed bytes need refcount=0).
   *Recommendation*: phase 3 supports compressed clusters
   in the standard sense (treat as one cluster, zero the
   L2 entry, decrement the cluster's refcount). Edge cases
   around multi-cluster compressed-entry layouts (the
   nb_sectors field encodes how many sectors the compressed
   data spans) are subtle; if profiling shows real-world
   compressed images, lift to a follow-up. For v1, reject
   shrink on images containing compressed entries above the
   new boundary with a `CompressedClustersInDiscardRange`
   error. *(Add to `ResizeError`?)*

7. **`OFLAG_COPIED` bit on discarded L2 entries.** Setting to
   zero clears the flag along with the offset. Correct.

8. **L1-region clearing.** For the L1 entries we zero, we
   emit a single Write patch covering the entire L1 region
   (with the zeroed entries baked in) — simpler than
   per-entry patches and the L1 region is typically tiny
   (one cluster). The patch's bytes mirror existing_l1_bytes
   with the relevant slots zeroed.

## Execution

Phase 3 splits into three sub-steps. One commit per step.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | opus | none | Extend `Qcow2ResizeOpts` in `src/crates/resize/src/lib.rs` with `existing_l2_bytes: &[u8]` and `existing_l2_indices: &[u32]` as documented in "Public API delta". Update existing call sites that construct Qcow2ResizeOpts (inline test in lib.rs, integration tests in tests/qcow2_grow.rs, tests/round_trip.rs) to pass `&[]` for the two new fields so phase 2's tests still compile and pass. Add a new ResizeError variant if needed — phase 3 may want a `CompressedClustersInDiscardRange` variant per open question 6; alternatively reuse `UnsupportedFormat` (no new variant). Make this judgment call when reading the existing compressed-cluster handling. `make test-rust`, `make lint`, `pre-commit run --all-files` clean. |
| 3b | high   | opus | worktree | Implement `plan_shrink(opts, scratch) -> Result<ResizePlan, ResizeError>` in `src/crates/resize/src/qcow2.rs`. Dispatch from `plan_grow`: replace the `UnsupportedShrink` early-return at lines 73–79 with a call into `plan_shrink`. Algorithm per the "High-level flow" section above. Helpers needed: `lookup_l2(opts, l1_index)` returns the staged L2 bytes (or `ScratchTooSmall`); a private `walk_l2_for_shrink` that iterates entries, handling both 8-byte and 16-byte (extended_l2) cases; `round_down_to_cluster(new_virtual_size, cluster_size)` for the open question 1 rounding. Stage all scratch buffers (L2 rewrites, L1 rewrite, refcount-block patches, header) BEFORE assembling the patch list (same idiom as phase 2's `plan_l1_and_refcount_grow` to avoid borrow conflicts). Honour the prepare → header partition (no cleanup phase). Add unit tests for the helper functions (round-down, walk-l2-for-shrink, L1-index identification). Risky: worktree isolation. |
| 3c | medium | sonnet | none | Add an integration test file `src/crates/resize/tests/qcow2_shrink.rs` mirroring `tests/qcow2_grow.rs`'s pattern. Use `crates/create` to build starting images, optionally write known patterns at specific L2 entries via direct byte manipulation (or via a helper that allocates a cluster and points an L2 entry at it), call `plan_resize_qcow2` with `allow_shrink: true`, apply the patches, re-parse and assert. Cover every positive and negative row from the "Test matrix" section. `make test-rust`, `make lint`, `pre-commit run --all-files` clean. |

## Out of scope for phase 3

- Non-qcow2 shrink (phases 4–6 for vmdk / vhd / vhdx — most
  remain `UnsupportedShrink` per master plan).
- Guest binary (phase 7).
- Host CLI (phase 8).
- Preallocation modes (phase 9). Shrink with
  `Preallocation::Off` only — the others are nonsensical for
  shrink (you can't pre-allocate metadata for a region being
  removed). Reject with `PreallocationUnsupported`.
- File truncation post-shrink. The file stays the same size;
  discarded clusters become dead bytes. Matches qemu.
- L1-region shrinking. The L1 entry count in the header stays
  the same.
- Internal snapshot L1-table adjustment.
- Compressed-cluster shrink with multi-cluster compressed
  entries (phase 3 may reject with the dedicated error variant
  if open question 6 lands that way).

## Success criteria for phase 3

- `cargo build -p resize` clean.
- `cargo test -p resize` and `cargo test -p resize --tests`
  pass with the new shrink tests plus the existing grow tests
  unchanged.
- `make instar` builds.
- `make check-binary-sizes`, `make lint`, `pre-commit run
  --all-files` all clean.
- `plan_resize_qcow2` for a shrink request returns a valid
  `ResizePlan` for every positive-path test case, with patches
  in prepare → header order (no cleanup phase).
- For each positive-path test that pre-writes known data
  *below* the shrink boundary: the data is recoverable after
  applying the patches.
- For each negative-path test: the documented error variant
  is returned.

## Sub-agent guidance

Read these files before starting any step:

- `src/crates/qcow2/src/lib.rs:1100-1230` (L2 walking helpers
  in the parser crate; for reference, not direct use).
- `src/crates/qcow2/src/lib.rs:107-130` (the L2 flag-bit
  constants and the L1/L2 entry layouts).
- `src/crates/resize/src/qcow2.rs:60-140` (the `plan_grow`
  dispatch where shrink currently early-returns).
- `src/crates/resize/src/qcow2.rs:plan_l1_and_refcount_grow`
  (phase 2c's structurally similar planner — same
  "stage-then-emit" idiom phase 3 should follow).
- `src/crates/resize/tests/qcow2_grow.rs` (the materialise-
  and-parse test pattern; phase 3 mirrors it for shrink).
- The master plan `docs/plans/PLAN-resize.md` (specifically
  the "QCOW2 shrink" subsection of "Per-format resize plans")
  for the algorithmic specification.

For each step the management session will:

- Read the actual files (not just trust the diff summary).
- Run `cargo build -p resize`, `cargo test -p resize`,
  `cargo test -p resize --tests`, `make lint`, `pre-commit
  run --all-files`.
- For 3b: verify the patch list and ordering by reading a
  sample plan's output in the test harness.
- Confirm the success-criteria items above.
- Commit if green with the standard commit-message format.

If a step fails review, discard the worktree (where
applicable) and re-spawn with a refined brief rather than
patching by hand.
