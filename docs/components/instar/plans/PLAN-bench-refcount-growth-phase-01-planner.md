# bench refcount growth — phase 01: growth planner

Parent: [PLAN-bench-refcount-growth.md](/components/instar/plans/PLAN-bench-refcount-growth/).
Planned at high effort.

## Scope

Add pure, dependency-free planning arithmetic to
`src/crates/bench/src/lib.rs` (no_std-compatible like the rest
of the crate; unit-tested host-side with `cargo test -p bench`):

1. `worst_case_touched(params: &BenchParams, image_size: u64,
   cluster_size: u64) -> TouchedBound` — an upper bound on the
   distinct data clusters and distinct L2 tables an execution of
   the schedule can touch. Uses `params.effective_step()`; when
   `offset + (count-1)·eff_step + bufsize <= image_size` the
   schedule never wraps and the span bound applies; otherwise
   fall back to the whole-image bound. Each request touches at
   most `bufsize/cs + 2` data clusters (straddle both ends) and
   at most `bufsize/l2_coverage + 2` L2 tables, where
   `l2_coverage = cluster_size · (cluster_size/8)`. Always also
   cap at `ceil(image_size/cs)` data clusters and
   `ceil(image_size/l2_coverage)` L2 tables. Saturating u64
   arithmetic throughout; `image_size == 0` or
   `cluster_size == 0` returns zeroes rather than dividing.

2. `plan_refcount_growth(...) -> Result<RefcountGrowthPlan, GrowthOverflow>`
   with inputs: `entries_per_refblock`, `file_end_clusters`
   (current host file size in clusters, rounded up),
   `populated_refblocks` (the staged contiguous prefix count),
   `rt_capacity_slots` (on-disk RT capacity =
   `refcount_table_clusters · cluster_size / 8`),
   `worst_case_new_clusters` (touched data + L2 from #1), and
   the staging caps (`max_refblocks`, `max_refblock_bytes /
   cluster_size`, `max_rt_bytes / 8`). Output:

   ```rust
   pub struct RefcountGrowthPlan {
       pub needed_slots: u64,      // total refblock slots incl. growth
       pub new_refblocks: u64,     // needed_slots - populated (0 = no growth)
       pub structures_start: u64,  // first cluster index of new structures
       pub new_rt_clusters: u64,   // 0 = RT stays in place
       pub rt_start: u64,          // == structures_start when relocating
       pub refblocks_start: u64,   // structures_start + new_rt_clusters
       pub worst_end_clusters: u64 // coverage the plan provides
   }
   ```

   Fixed-point iteration: start from
   `end = file_end_clusters + worst_case_new_clusters`; compute
   `needed_slots = ceil(end / entries_per_refblock)`; if
   `needed_slots > rt_capacity_slots`, add
   `new_rt_clusters = ceil(needed_slots · 8 / cluster_size)`
   — pass cluster_size or derive from entries_per_refblock —
   and `new_refblocks` to `end`; recompute until stable
   (converges ≤ 3 rounds; assert with a bounded loop).
   `new_refblocks == 0` must short-circuit to a no-growth plan
   even when the RT has spare slots. Exceeding any staging cap
   returns `GrowthOverflow` (maps to `ERROR_ALLOC_EXHAUSTED`
   in the guest).

   Invariants to unit-test (and assert in debug):
   * every new-structure cluster index
     (`structures_start .. structures_start + new_rt_clusters +
     new_refblocks`) is `< needed_slots · entries_per_refblock`
     (self-coverage);
   * `worst_end_clusters >= file_end_clusters +
     worst_case_new_clusters`;
   * no-growth inputs (worst case fits populated coverage AND
     populated refblocks have ≥ worst_case free entries? — no:
     v1 semantics are coverage-based, keep it coverage-based:
     `needed_slots <= populated_refblocks` ⇒ no-growth plan)
     return `new_refblocks == 0 && new_rt_clusters == 0`;
   * plans are monotone in `worst_case_new_clusters`.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | default (Fable) | none | Implement `TouchedBound`, `worst_case_touched`, `RefcountGrowthPlan`, `plan_refcount_growth` in `src/crates/bench/src/lib.rs` per the Scope section (which is the full spec — follow it exactly, saturating arithmetic, no_std, no new deps). Add exhaustive unit tests in the crate's existing `#[cfg(test)] mod tests`: wrap/no-wrap bounds, straddle counting, zero-size guards, no-growth short-circuit, RT-relocation trigger at slot capacity, fixed-point convergence cases sized for 4M/16M/64M images at 512/4096/65536-byte clusters (compute expectations by hand in comments), staging-cap overflow, and the four invariants as property-style loops over a seedless parameter grid. Run tests via the devcontainer (`make test-rust` runs the workspace; `cargo test -p bench` inside the container is the fast loop). Match the crate's existing doc-comment style (dense, referenced back to PLAN-bench-refcount-growth.md). |

## Review checklist deltas

Standard checklist from PLAN-TEMPLATE.md, plus: confirm the
crate still has an empty `[dependencies]` table and no `std`
usage outside `#[cfg(test)]`.
