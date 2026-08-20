# PLAN-resize followup 01: targeted refcount-block pre-pass

## Status: Complete

Steps 01a-01e.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the
qcow2 resize planner (`src/crates/resize/src/qcow2.rs` —
especially `plan_grow`, `plan_l1_grow`,
`plan_l1_and_refcount_grow`, `ensure_block_staged`,
`block_offset_in_file`, `stage_increment`,
`stage_decrement`). Read the current guest pre-pass at
`src/operations/resize/src/main.rs:382-420` and the
`EXISTING_STATE_LIMIT` carve-up immediately above.

Where a question touches on external concepts (qcow2 refcount-
table layout, refcount-block coverage math), research as
needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

This is a follow-up to PLAN-resize.md. It addresses a
real-world limitation surfaced by the automated reviewer on
PR #326 — the guest's "stage every refcount block" pre-pass
imposes an image-size ceiling proportional to cluster size,
hitting ~128 GiB at the common 64 KiB cluster size.

## Mission

Replace the guest's stage-every-refcount-block pre-pass for
qcow2 grow with a **targeted pre-pass** that stages only the
specific refcount blocks the chosen grow flavour will modify.
The planner already has the right contract
(`ensure_block_staged` returns `ScratchTooSmall` if a needed
block isn't present in `existing_refcount_block_indices`), so
the entire change is on the guest side plus a small new helper
in the planner crate to expose the block-identification logic.

Post-fix, the qcow2 resize image-size ceiling at the default
64 KiB cluster lifts from ~128 GiB to **multi-PB** (bounded
only by what the filesystem can store). Real-world cloud
workloads (1 TiB+ qcow2 disks growing by some fixed delta)
work end-to-end.

Out of scope: the qcow2 shrink path. Shrink stages L2 tables,
not refcount blocks; its staging is already targeted (only the
L2 tables covering the discard range) and bounded by a
separate cap (256 L2 tables = ~512 GiB of discardable range
per operation). Lifting that cap is a different problem with
its own design space and is queued as a separate item under
PLAN-resize.md Future work.

## What the survey turned up

- **Current stage-everything pre-pass**
  (`src/operations/resize/src/main.rs:382-420`): walks the
  refcount table, collects every non-zero entry's block_idx
  into a `block_indices` array (cap 1024), then reads each
  block at `cluster_size` bytes into `EXISTING_STATE`. The
  cumulative byte cost is `non_zero_block_count *
  cluster_size`, capped at `EXISTING_STATE_LIMIT = 4 MiB`.

- **Block-coverage math**:
  - `entries_per_refblock = cluster_size * 8 / refcount_bits`
  - `bytes_per_block = entries_per_refblock * cluster_size`
  - At cluster=64 KiB, refcount_bits=16:
    `2^15 entries * 64 KiB = 2 GiB per block`
  - Image-size ceiling: `EXISTING_STATE_LIMIT / cluster_size
    * bytes_per_block = 4 MiB / 64 KiB * 2 GiB = 128 GiB`

- **`Qcow2ResizeOpts`** carries
  `existing_refcount_block_bytes: &[u8]` (flat concatenation
  in `existing_refcount_block_indices` order) plus
  `existing_refcount_block_indices: &[u64]`. The planner's
  `ensure_block_staged(opts, block_idx, ...)` linear-scans
  `existing_refcount_block_indices` for the requested
  block_idx and returns `ScratchTooSmall` if absent.

- **What `plan_l1_grow` actually demands**
  (`src/crates/resize/src/qcow2.rs:202-292`): registers an
  increment patch for every cluster in
  `[new_l1_first_cluster, new_l1_last_cluster]` (the new L1
  region, appended at EOF) and a decrement patch for every
  cluster in `[old_l1_first_cluster, old_l1_last_cluster]`
  (the freed old L1 region). For each registered patch the
  planner calls `ensure_block_staged(block_idx = cluster /
  entries_per_refblock)`. Worst case is ~1-2 distinct blocks
  on each side; same block can appear on both sides
  (rare overlap).

- **What `plan_l1_and_refcount_grow` demands** (similar
  reading at `src/crates/resize/src/qcow2.rs:605` onward;
  full enumeration needed during 12a): same as L1Grow plus
  blocks containing the new refcount-table region. The new
  refcount-block clusters themselves are written from
  scratch (no existing block to stage — `synthetic_layout`
  builds them fresh).

- **What `plan_header_only` demands**: nothing — header
  rewrite only, no refcount mutation.

- **`decide_action`**: takes scalar fields (cluster_size,
  refcount_bits, current/new virtual_size, current_l1_entries,
  current_refcount_table_clusters) and returns the grow
  flavour. **No refcount-block data needed** to make the
  decision. This is the structural pivot that makes targeted
  staging possible — the guest can call `decide_action`
  before reading any refcount blocks.

- **`ensure_block_staged` contract**
  (`src/crates/resize/src/qcow2.rs:1452-1475`): returns
  `Err(ScratchTooSmall)` on a miss. The hint at line
  1471-1473 ("Block not staged. The guest's pre-pass should
  have caught this; surface as ScratchTooSmall so the host
  can retry with a wider stage list") is forward-looking
  — the guest *can* retry, but with the targeted approach we
  expect this to never fire in practice.

- **The shrink path** stages L2 tables (not refcount blocks)
  into `EXISTING_STATE` after the refcount-block region.
  Capped at 256 L2 tables via a separate guard.

## Algorithmic design

### New planner helper: `compute_grow_action_and_required_blocks`

Public function on the qcow2 planner module that the guest
calls *before* staging refcount blocks:

```rust
pub fn compute_grow_action_and_required_blocks(
    opts: &Qcow2ResizeGrowQuery,
) -> Result<GrowPlan, ResizeError>;
```

Where `Qcow2ResizeGrowQuery` is a struct of scalar fields the
guest already has from `parsed: QcowHeader`:

```rust
pub struct Qcow2ResizeGrowQuery {
    pub cluster_size: u32,
    pub refcount_bits: u8,
    pub extended_l2: bool,
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub current_file_size: u64,
    pub current_l1_entries: u32,
    pub current_l1_table_offset: u64,
    pub current_refcount_table_clusters: u32,
    pub current_incompatible_features: u64,
}
```

And `GrowPlan` is:

```rust
pub struct GrowPlan {
    pub action: Qcow2GrowAction,  // HeaderOnly / L1Grow / L1AndRefcountGrow
    pub required_blocks: ArrayVec<u64, MAX_REQUIRED_BLOCKS>,
}
```

`MAX_REQUIRED_BLOCKS` is small (8 is generous — L1Grow needs ≤
4, L1AndRefcountGrow ≤ 6 even with adversarial layouts).

This function does no I/O, allocates nothing, and is pure-
function over the scalar inputs. Same restrictions as the
existing planner.

### Per-flavour block-identification rules

| Action | Blocks needed |
|--------|--------------|
| HeaderOnly | (none) |
| L1Grow | distinct `block_idx`s for clusters in `[new_l1_first_cluster, new_l1_last_cluster]` ∪ `[old_l1_first_cluster, old_l1_last_cluster]` |
| L1AndRefcountGrow | L1Grow's set ∪ distinct `block_idx`s for clusters in `[new_refcount_table_first_cluster, new_refcount_table_last_cluster]` |

The cluster ranges are computable from the scalar inputs only:

```
new_l1_size_bytes = new_l1_entries * 8
new_l1_clusters = ceil(new_l1_size_bytes / cluster_size).max(1)
new_l1_first_cluster = current_file_size / cluster_size
new_l1_last_cluster = new_l1_first_cluster + new_l1_clusters - 1

old_l1_first_cluster = current_l1_table_offset / cluster_size
old_l1_size_bytes = current_l1_entries * 8
old_l1_clusters = ceil(old_l1_size_bytes / cluster_size).max(1)
old_l1_last_cluster = old_l1_first_cluster + old_l1_clusters - 1

entries_per_refblock = cluster_size * 8 / refcount_bits
block_idx(c) = c / entries_per_refblock
```

For L1AndRefcountGrow, the new refcount-table region offset
follows the new L1 region (see `synthetic_layout_after_*`
helpers — phase 12a must read these and document the exact
formula in the helper). Same arithmetic shape.

The returned `required_blocks` is the *distinct*-block-idx
union of the per-cluster lookups. Dedupe is done in the
helper (small fixed-size array; linear scan is fine).

### Updated guest pre-pass

```rust
// 1. Read L1 + refcount-table into EXISTING_STATE (small).
// 2. Compute grow plan via the new helper.
let grow_plan = qcow2::compute_grow_action_and_required_blocks(&query)?;

// 3. Stage exactly the blocks the planner will need.
let mut block_indices: [u64; MAX_REQUIRED_BLOCKS] = [0; MAX_REQUIRED_BLOCKS];
let count = grow_plan.required_blocks.len();
block_indices[..count].copy_from_slice(&grow_plan.required_blocks);

let blocks_off = rt_end;
let blocks_total = count * cluster_size;
// blocks_total bounded by MAX_REQUIRED_BLOCKS * cluster_size
// = 8 * 2 MiB max = 16 MiB worst case (cluster_size cap), but
// at cluster_size=64KiB only 512 KiB. Fits comfortably in
// EXISTING_STATE_LIMIT.
debug_assert!(blocks_off + blocks_total <= EXISTING_STATE_LIMIT);

for (slot, &block_idx) in block_indices[..count].iter().enumerate() {
    let block_file_off = block_offset_in_table(rt_slice, block_idx);
    read_byte_range(call_table, sector_size, block_file_off,
                    state_base.add(blocks_off + slot * cluster_size),
                    cluster_size)?;
}

// 4. Call plan_resize_qcow2 with the targeted stage list.
// 5. Apply patches as before.
```

The L2-staging logic for shrink stays unchanged.

### `EXISTING_STATE_LIMIT` stays at 4 MiB

After this change, the actual peak usage at the default
cluster size is:
- L1 region: ≤ 64 KiB (1 cluster) for any virtual size up
  to ~512 GiB; ≤ 128 KiB up to 1 TiB; ≤ 256 KiB up to 2 TiB.
- Refcount-table region: similar order.
- ≤ 6 refcount blocks × 64 KiB = 384 KiB.
- Total: well under 1 MiB at default cluster.

At pathological cluster sizes (2 MiB cluster), 6 blocks ×
2 MiB = 12 MiB — would exceed `EXISTING_STATE_LIMIT`. But
that combination is already blocked by other limits
(`QCOW2_MAX_RESIZE_SCRATCH` at 32 MiB), and the differential
fuzz picker filters cluster_size=2 MiB anyway. Document the
remaining `cluster_size > 1 MiB + multi-TB grow` edge case
as a known limit rather than working around it.

### Naming / file layout

- New helper lives in `src/crates/resize/src/qcow2.rs`,
  exported via `src/crates/resize/src/lib.rs`. Following the
  pattern of `Preallocation`, `Qcow2ResizeOpts`, etc.
- New types: `Qcow2ResizeGrowQuery`, `GrowPlan`,
  `Qcow2GrowAction` (the latter already exists internally
  as `decide_action`'s return — promote to `pub`).
- The internal `decide_action` becomes the implementation
  detail behind `compute_grow_action_and_required_blocks`.

## Test surface

**Unit tests** in `src/crates/resize/src/qcow2.rs::tests`:
- `compute_grow_action_and_required_blocks` for each grow
  flavour at default cluster size — verify action +
  expected block-idx set.
- Same at 4 KiB and 1 MiB cluster — confirm block-idx math
  scales correctly.
- 1 TiB virtual size grow at 64 KiB cluster — confirms the
  required-block set stays bounded (≤ 4 blocks).
- HeaderOnly: required_blocks is empty.
- Forward-compat assert: returned `required_blocks.len() <=
  MAX_REQUIRED_BLOCKS`.

**Integration tests** in `src/crates/resize/tests/`:
- A new `qcow2_grow_large.rs` file with a fabricated 500 GiB
  qcow2 fixture (header + L1 + refcount-table only; no data
  region). Grow to 1 TiB. Re-parse header to verify
  `virtual_size = new`. Demonstrates the limit is lifted.
- Edge case: virtual_size just past the old 128 GiB ceiling
  (e.g. 130 GiB) to verify the regression boundary.

**Python integration test** in `tests/test_resize.py`:
- Add `TestResizeLargeImages` with 1-2 cases that create a
  qcow2 of e.g. 200 GiB (small file, large virtual via
  sparse), resize to 256 GiB, verify info JSON. Conditional
  on filesystem support — skip when `df` reports < a few
  MiB free at the test tmpdir.

**Fuzz coverage**: drop the 40-bit size clamp in
`fuzz_resize_planners.rs` for the qcow2 branch (or lift it
from 40 to 56 bits = ~64 PiB). The planner-side defensive-
input gap that motivated the clamp is still relevant for
the *other* formats, so the clamp stays for vhd/vhdx/vmdk.

**Differential fuzz**: remove the size ceiling implicit in
the `_resize_option_picker` for qcow2 (currently caps at
64 MiB virtual). Bump qcow2 picker to include 200 MiB or
1 GiB sizes so the differential surface exercises the new
code path against qemu.

## Public API delta

- New `pub fn compute_grow_action_and_required_blocks` in
  `crates/resize`.
- New `pub struct Qcow2ResizeGrowQuery`,
  `pub struct GrowPlan`, `pub enum Qcow2GrowAction`.
- Internal `decide_action` and per-flavour helpers may need
  to become `pub(crate)` if they aren't already — verify
  during implementation.

`Qcow2ResizeOpts` shape and `plan_resize_qcow2` signature
are unchanged. The old planner contract still works for
backwards compat: a caller that wants to stage everything
(e.g. a test or a fallback path) can still do so and
`ensure_block_staged` will accept it.

## Open questions

1. **Should the helper return only block indices, or also
   what each block is wanted for (debug aid)?** Returning a
   richer struct is more friendly for diagnostics but adds
   complexity. *Recommendation*: indices only; if a future
   incident needs more context, the planner can be
   instrumented with `debug!` traces.

2. **Should `MAX_REQUIRED_BLOCKS` be tight (e.g. 8) or
   generous (e.g. 16)?** L1Grow needs ≤ 4 in normal cases.
   L1AndRefcountGrow ≤ 6. Doubling is cheap (`ArrayVec`
   storage is `8 × MAX = 128` bytes either way). *Recommendation*:
   16 to leave headroom for the refcount-table grow case at
   non-default cluster sizes; document the calculation.

3. **Should `compute_grow_action_and_required_blocks`
   handle the shrink case too?** No — shrink needs L2-table
   staging, not refcount-block staging. The query function
   is grow-specific. Shrink will get its own targeted
   pre-pass in a separate follow-up if/when needed.

4. **Two-pass safety net?** Should the guest, if
   `plan_resize_qcow2` returns `ScratchTooSmall`, fall back
   to staging *all* blocks and retry? *Recommendation*:
   no. The targeted helper is the single source of truth.
   If it's wrong, the integration test should catch it; the
   fuzz harness too. A silent fallback masks bugs.

5. **Naming: `compute_grow_action_and_required_blocks` is
   long.** `prepare_grow` is shorter but vague.
   `qcow2_grow_query` matches the input type. *Recommendation*:
   `compute_qcow2_grow_query` (mirrors input name)? Or
   simply `qcow2_grow_query`? Decide at implementation time.

6. **Should we add a `compute_*` for vhd / vhdx / vmdk for
   symmetry?** No — those formats stage scalar header fields
   only, not refcount blocks. Their pre-passes don't have
   this issue.

7. **Should the limit lift be gated behind a host CLI flag
   in case the new code regresses on some workload?** No.
   The new pre-pass is strictly more conservative (stages
   fewer bytes, never more). If anything regresses, it'll
   be a `ScratchTooSmall` for a case the helper missed —
   surfaced loudly, easy to fix.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 01a | medium | opus | none | Refactor `decide_action` and the internal grow-flavour helpers into a clean `compute_qcow2_grow_query(query: &Qcow2ResizeGrowQuery) -> Result<GrowPlan, ResizeError>` public function in `src/crates/resize/src/qcow2.rs`. Promote `Qcow2GrowAction` to `pub`. New `pub struct Qcow2ResizeGrowQuery` and `pub struct GrowPlan` per the design. The helper computes the action *and* the distinct `required_blocks` set per the per-flavour rules. Unit tests cover all three flavours at default + 4 KiB + 1 MiB clusters, plus the 1 TiB-virtual case. Re-export the new types from `src/crates/resize/src/lib.rs`. No host or guest changes yet; the existing stage-everything path keeps working. Wave-1 audit clean. |
| 01b | medium | sonnet | none | Update the guest pre-pass at `src/operations/resize/src/main.rs:382-420`: call `compute_qcow2_grow_query` first, then stage only `grow_plan.required_blocks`. Delete the old `MAX_RB_INDICES = 1024` scan + the cap check at line 406 (replaced by the bounded `required_blocks.len() <= MAX_REQUIRED_BLOCKS`). The L2-staging logic for shrink stays unchanged. Build + test-rust + test-integration clean. Smoke a 200 GiB qcow2 grow manually (create sparse, resize, verify info). |
| 01c | small | sonnet | none | Add `src/crates/resize/tests/qcow2_grow_large.rs` exercising a 500 GiB → 1 TiB grow on a fabricated qcow2 fixture (header + L1 + refcount-table + a few refcount blocks; no data region). Verify the planner accepts the targeted block stage list and emits a correct plan. Add `TestResizeLargeImages` to `tests/test_resize.py` with a 200 GiB → 256 GiB end-to-end case (skipif filesystem can't accommodate the small physical file). |
| 01d | small | sonnet | none | Fuzz updates: relax the size clamp in `fuzz_resize_planners.rs` for the qcow2 branch (40 → 56 bits, or branch-specific); bump the qcow2 picker in `differential-fuzz.py` to include 200 MiB / 1 GiB sizes (still below qemu-img-resize runtime limits but exercising the new pre-pass with non-trivial L1 sizes). Run a 5-minute coverage smoke + 200-iteration differential smoke at fixed seed; both clean. |
| 01e | low | sonnet | none | Doc + master-plan housekeeping. Update `docs/resize.md` and `docs/quirks.md`: remove the "~128 GiB ceiling" note (now stale); add a one-line note that the qcow2 grow limit is now bounded only by the filesystem. Remove the matching item from `docs/plans/PLAN-resize.md`'s `## Future work` section (or move to a "Lifted" subsection). Add this followup plan's status to `docs/plans/index.md`. Commit. |

## Out of scope for this followup

- Shrink-path L2 staging cap (separate problem, separate
  design space; queued in PLAN-resize.md Future work).
- Per-format data-region preallocation parity (separate
  Future-work item; orthogonal).
- Bumping `EXISTING_STATE_LIMIT` (the targeted pre-pass
  removes the need for this).
- Adding similar targeted pre-passes to vhd / vhdx / vmdk
  (those formats don't stage refcount-block-equivalents;
  their pre-passes are already small and scalar).

## Success criteria

- `compute_qcow2_grow_query` exposed; unit tests pass for
  all three flavours at three cluster sizes (default, 4 KiB,
  1 MiB).
- Guest pre-pass uses the targeted stage list; the old
  `MAX_RB_INDICES = 1024` scan is gone.
- `make test-rust` + `make test-integration` clean.
- New `qcow2_grow_large.rs` integration test passes
  (500 GiB → 1 TiB).
- New `TestResizeLargeImages` passes when filesystem has
  room; skips cleanly otherwise.
- Manual smoke: `instar create -f qcow2 t.qcow2 256G &&
  instar resize -f qcow2 t.qcow2 512G && instar info t.qcow2`
  succeeds end-to-end and reports `virtual-size: 549755813888`.
- 5-minute fuzz smoke + 200-iter differential smoke clean.
- Docs updated: ceiling note removed; Future-work entry
  consolidated.

## Sub-agent guidance

Read these files before any step:

- `src/crates/resize/src/qcow2.rs` lines 66-330 (the grow
  planner entry point + `plan_l1_grow`'s block-staging
  pattern), and 600-920 (`plan_l1_and_refcount_grow` for
  the additional block-identification rules), and 1450-1490
  (`ensure_block_staged` + `block_offset_in_file`).
- `src/operations/resize/src/main.rs` lines 39-90 (memory
  layout constants) and 350-475 (the pre-pass to replace).
- `src/crates/resize/src/lib.rs` (the `Qcow2ResizeOpts`
  shape that the helper's input mirrors).
- `tests/test_resize.py:TestResizeBaselineMatrix` and
  `TestResizeConsistency` for the test-class style to
  mirror for `TestResizeLargeImages`.
- This plan's §"Algorithmic design" for the exact math the
  helper must implement.

The management session review checklist is the same as the
PLAN-resize phase plans: per-step `git diff` review, smoke
before commit, escalate any planner-test divergence to the
user before papering it over.
