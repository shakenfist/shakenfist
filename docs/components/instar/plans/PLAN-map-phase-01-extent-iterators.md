# Phase 1: per-format extent iterators on the parser crates

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/)

## Status: Complete

`MapExtent`, `MapExtentState`, and `MapExtentCoalescer` shipped
in `src/shared/src/lib.rs` (with full coalescer unit-test
coverage). Each parser crate (`raw`, `qcow2`, `vmdk`, `vhd`,
`vhdx`) gained a `map_extents` walker mirroring the existing
`scan_allocation` shell, plus pure classification helpers
(`classify_qcow2_l2_*`, `classify_vmdk_grain_entry`,
`classify_vhd_bat_entry`, `classify_vhdx_bat_entry`) under
unit test. Workspace `make lint` + `make test-rust` clean.

## Mission

Each parser crate (`raw`, `qcow2`, `vmdk`, `vhd`, `vhdx`) gains
a `map_extents()` entry point that walks the on-disk allocation
metadata and emits a stream of coalesced `MapExtent` records.
A `MapExtent` describes one contiguous region of the source's
virtual address space, classified as `Data { file_offset } |
ZeroAllocated | Hole`, with adjacent same-state extents merged
inside the parser so the guest binary in phase 2 does not need
to coalesce.

The new shape lives next to the existing `scan_allocation()`
walks added in `PLAN-measure` phase 2 — same sector readers,
same cache buffers, same `unsafe` boundary — but yields per-cluster
information rather than rolling it up into an `AllocationSummary`.

Phase 1 ships only the library code. The guest binary that
streams extents over the serial channel arrives in phase 2; the
host CLI in phase 3; the integration tests against real testdata
images in phase 6. Phase 1's unit tests cover the pure
helpers (classification, coalescing) and exercise the per-format
walkers against small synthetic images where feasible (raw,
trivial fixed-vhd) or against pure helpers fed byte slices
matching real on-disk layouts (qcow2, vmdk, vhd dynamic, vhdx).

## Why this is its own phase

- The work is mechanically similar per format (each parser
  already walks its tables in `scan_allocation`), but it spans
  five crates and one shared-types addition. Splitting from
  phase 2 (proto, guest binary, call-table config) keeps each
  commit small enough to review.
- Putting the walkers on the parser crates rather than in a new
  `crates/map/` keeps them adjacent to `scan_allocation` so the
  two can share the per-sector reading loop where it makes
  sense — and so a future consolidation (one walker, two
  consumers) is a local refactor rather than a cross-crate
  migration.
- The `MapExtent` shared types in step 1a unblock the rest:
  until they live in `shared`, parsers cannot return them
  without creating a `qcow2 → map → qcow2` cycle (same
  argument that motivated relocating `AllocationSummary` in
  PLAN-measure phase 2a).

## Architecture

### New types in `shared`

Add `MapExtent` and `MapExtentState` to
`src/crates/shared/src/lib.rs` next to `AllocationSummary`. The
shape mirrors `qemu-img map`'s JSON object, minus
backing-chain fields (depth/filename) that the host emits and
that the parser does not know:

```rust
/// Allocation state of a virtual-address range in a source image.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MapExtentState {
    /// Region holds data and is backed by the file at `file_offset`.
    /// Compressed clusters count as Data; the file_offset is the
    /// (possibly compressed) on-disk start.
    Data { file_offset: u64 },
    /// Region reads as zero and is recorded as zero in the metadata
    /// (qcow2 ZERO_PLAIN / ZERO_ALLOC, vmdk grain marker
    /// `0xFFFFFFFE`, vhdx PAYLOAD_BLOCK_ZERO). No file_offset.
    ZeroAllocated,
    /// Region is unallocated — reads as zero but is not present
    /// in the source file (the qemu-img `present=false` case).
    Hole,
}

/// One contiguous extent of the source's virtual address space
/// with a single allocation state.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct MapExtent {
    /// Virtual offset of the extent's first byte, in bytes from
    /// the start of the source image.
    pub start: u64,
    /// Extent length in bytes. Never zero — zero-length extents
    /// are dropped at the coalescer.
    pub length: u64,
    /// Allocation state.
    pub state: MapExtentState,
}
```

Both types live in `shared`, are `no_std`-clean, and have
`derive` impls only. No methods. The convenience predicates
(`present`, `zero`, `data`) that the protobuf surface needs
live in phase 2 next to the proto translation — they are a
property of the wire format, not the type.

### Coalescer helper

`MapExtent` records emerge from the per-format walkers one
cluster / grain / block at a time. Coalescing rule:

- Two adjacent extents merge when:
  - Their virtual ranges are contiguous (`a.start + a.length
    == b.start`), AND
  - Their states match. For `Data` that requires the file
    offsets to be contiguous too:
    `a.state.file_offset + a.length == b.state.file_offset`.
    For `ZeroAllocated` and `Hole`, state equality is enough.

The merge logic is small but easy to get wrong (off-by-one
file-offset checks, zero-length inputs, `u64` overflow on
`start + length`). It lives once in `shared` so every parser
uses the same implementation:

```rust
/// Sink that swallows per-cluster `MapExtent`s and forwards
/// coalesced runs to an underlying emitter.
///
/// Usage: parser builds one of these wrapping the user's
/// `&mut FnMut(MapExtent) -> bool`, calls `push(extent)` for
/// each cluster/grain/block, and calls `finish()` at the end
/// to flush the trailing extent. `push` returns the emitter's
/// return value (false = abort iteration) — the parser must
/// stop walking when it sees `false`.
pub struct MapExtentCoalescer<'a, F: FnMut(MapExtent) -> bool> {
    pending: Option<MapExtent>,
    emit: &'a mut F,
}

impl<'a, F: FnMut(MapExtent) -> bool> MapExtentCoalescer<'a, F> {
    pub fn new(emit: &'a mut F) -> Self { /* ... */ }
    /// Returns `false` if the emitter returned false (caller should abort).
    pub fn push(&mut self, ext: MapExtent) -> bool { /* ... */ }
    /// Returns `false` if the final flush's emit returned false.
    pub fn finish(self) -> bool { /* ... */ }
}
```

The coalescer is pure (no I/O, no allocation, no `unsafe`)
and is the single highest-value unit-test target in this
phase. Tests cover: all-Hole input, all-Data with contiguous
file offsets, all-Data with one non-contiguous offset
mid-stream (must split), mixed Hole/Data/ZeroAllocated runs,
single-extent input, zero-length push (rejected), abort on
emitter returning false at every position.

### Visitor / callback pattern, not `Iterator`

Rust's `Iterator` trait does not compose well with the
existing parsers' walking model: `next()` would need to hold
mutable borrows on `&mut Qcow2State`, the call table, and
the cache buffers across yields, and our `cached_read!` macros
already burn one of those borrows. We could write a manual
generator with an explicit state machine, but it would
duplicate the L1/L2 walk in stateful form.

Established pattern in the codebase: the `vhd` and `vhdx`
`scan_allocation` walkers loop over sectors and call a pure
helper `FnMut(&[u8])` per chunk. We extend the same shape:
each per-format `map_extents` walker takes a
`&mut FnMut(MapExtent) -> bool` and calls it (through the
coalescer) once per source cluster / grain / block. The
`bool` return is the early-termination signal — the walker
checks it after every push and returns when the caller says
"stop". This is what phase 2's `start_offset` / `max_length`
window will use, and what the fuzz harness will use to bound
walker runtime on adversarial inputs.

Signature shape (per format):

```rust
/// # Safety
/// `call_table` must be valid. Cache buffers (`l1_cache_buf`,
/// `l2_cache_buf`, etc.) must still point to writable
/// `MAX_SECTOR_SIZE` regions for the duration of the call.
pub unsafe fn map_extents<F: FnMut(MapExtent) -> bool>(
    &mut self,
    call_table: &CallTable,
    sector_size: usize,
    input_capacity: u64,
    virtual_size: u64,
    bytes_read: &mut u64,
    emit: &mut F,
) -> Option<()>;
```

Returns `Some(())` on a successful complete walk (or successful
early-termination); `None` on a read failure (matches
`scan_allocation`'s convention).

### `scan_allocation` is not refactored in phase 1

The master plan's overview suggested rewriting
`scan_allocation` as `map_extents().fold(...)`. After looking
at the existing implementations, **deferring that refactor is
the safer call.** Reasons:

1. **`AllocationSummary` carries `target_units_with_data`**,
   a per-cluster bitmap of which target-aligned regions touch
   any source data. Computing it from a stream of *coalesced*
   `MapExtent`s requires a target-aware fold; threading the
   target unit size through `MapExtent` (which has no business
   knowing it) or computing it from the post-coalesce stream
   (which has already merged adjacent target-units) is more
   complex than the existing direct walks.
2. **Existing scanners are battle-tested.** They are covered
   by the entire `measure` baseline matrix (~40 k expected
   outputs) and the differential fuzzer. A refactor that
   regresses one of them blocks measure CI without helping
   map.
3. **The cost of duplication is bounded.** Each `map_extents`
   walker is ~80 LoC sharing the per-sector-read shell with
   `scan_allocation`; the duplication is in the per-cluster
   classification, not the sector walking.

Phase 1 therefore adds `map_extents` **alongside**
`scan_allocation`. The "single walker" consolidation is
listed under Future work; a follow-up plan can take it on
once measure's `target_units_with_data` accounting has a
stable target-aware iterator shape.

### Per-format walker specifications

#### `raw::map_extents`

Trivial. Raw has no allocation metadata. Single
`MapExtent { start: 0, length: virtual_size, state: Data {
file_offset: 0 } }`. No CallTable needed (matches
`raw::scan_allocation`'s pure signature). For
`virtual_size == 0`, emit nothing.

The `SEEK_HOLE` / `SEEK_DATA` host-side prepass that would
split a sparse raw file into multiple extents is listed as
future work in the master plan; the no_std raw scanner
cannot do it. Documented divergence.

#### `vhd::map_extents`

- **Fixed VHD** (`disk_type == DISK_TYPE_FIXED`): single
  `Data { file_offset: 0 }` extent covering `current_size`.
  No BAT walk.
- **Dynamic / Differencing VHD**: walk the BAT (existing
  reader pattern in `VhdState::scan_allocation`,
  `src/crates/vhd/src/lib.rs:661`). For each BAT entry of
  `block_size` virtual bytes:
  - `entry == 0xFFFFFFFF`: push `Hole { length: block_size }`.
  - Otherwise: push `Data { file_offset: entry_sector *
    SECTOR_SIZE + sector_bitmap_size }` (the data offset
    skips the per-block sector bitmap that precedes the
    payload).

The walker computes `file_offset` as the on-disk payload
offset, not the sector-bitmap offset, matching qemu-img map's
output.

Pure helper to add alongside `count_allocated_in_bat`:

```rust
/// Classify one VHD BAT entry into a MapExtent state, given
/// the virtual offset and block size. Returns None if the
/// entry should be skipped (never, for VHD — every entry
/// covers exactly one block).
pub fn classify_vhd_bat_entry(
    entry: u32,
    virtual_offset: u64,
    block_size: u64,
    sector_bitmap_size: u64,
) -> MapExtent;
```

The helper is the unit-test target. Tests cover both
`entry == 0xFFFFFFFF` (Hole) and allocated entries with
varying file offsets.

#### `vhdx::map_extents`

Walk the BAT exactly like `VhdxState::scan_allocation`
(`src/crates/vhdx/src/lib.rs:987`), but classify per entry:

- `PAYLOAD_BLOCK_NOT_PRESENT (0)`,
  `PAYLOAD_BLOCK_UNDEFINED (1)`,
  `PAYLOAD_BLOCK_ZERO (2)`,
  `PAYLOAD_BLOCK_UNMAPPED (3)`: push `Hole`.
- `PAYLOAD_BLOCK_FULLY_PRESENT (6)`: push
  `Data { file_offset: file_offset_mb * 1024 * 1024 }`.
- `PAYLOAD_BLOCK_PARTIALLY_PRESENT (7)`: treat as
  `Data { file_offset }` in v1 (matches
  `scan_allocation`'s simplification; per-sector-bitmap walk
  listed as future work).

The chunk_ratio interleaving (every `chunk_ratio` payload
entries followed by one sector-bitmap entry) is identical to
`scan_allocation` — skip the bitmap entries.

Pure helper to add alongside `count_allocated_in_bat`:

```rust
/// Classify one VHDX BAT payload entry into a MapExtent state.
pub fn classify_vhdx_bat_entry(
    entry: u64,
    virtual_offset: u64,
    block_size: u64,
) -> MapExtent;
```

`entry` is the raw u64-LE BAT entry; the function extracts the
low 3 bits for the state and the high bits for the 1 MiB-unit
file offset. Tests cover every state value.

#### `vmdk::map_extents`

For monolithicSparse, walk grain directory → grain table
exactly like `VmdkState::scan_allocation`
(`src/crates/vmdk/src/lib.rs:817`). For each grain table
entry of `grain_size` virtual bytes:

- `entry == 0` (unallocated): push `Hole { length:
  grain_size }`.
- `entry == ZERO_GRAIN_MARKER (0xFFFFFFFE)`: push
  `ZeroAllocated { length: grain_size }`.
- Otherwise: push `Data { file_offset: entry * SECTOR_SIZE }`.

For monolithicFlat, single `Data { file_offset: 0 }` extent
covering the flat-extent virtual size.

For streamOptimized, the existing parser resolves
grain → file offset for `convert`; reuse the same resolver.

Multi-extent descriptors (multi-file vmdk) report the top
extent only in v1; the master plan tracks multi-extent
propagation as future work. The guest binary will refuse
multi-extent sources alongside backing-chain sources in
phase 2.

Pure helper to add alongside `count_populated_gd_entries` /
`count_allocated_in_gt`:

```rust
/// Classify one VMDK grain-table entry into a MapExtent state.
pub fn classify_vmdk_gt_entry(
    entry: u32,
    virtual_offset: u64,
    grain_size: u64,
) -> MapExtent;
```

Tests cover unallocated, `ZERO_GRAIN_MARKER`, and three
ordinary allocated entries with varying file offsets.

#### `qcow2::map_extents`

The hard one. Walk L1 → L2 exactly like
`Qcow2State::scan_allocation`
(`src/crates/qcow2/src/lib.rs:1674`), but classify each L2
entry per `cluster_lookup`'s decision tree
(`src/crates/qcow2/src/lib.rs:1303` and following). Per cluster:

- L1 entry is 0 or its L2 table offset is 0: every cluster
  in the L2's coverage is `Hole`.
- L2 entry is 0 (standard): `Hole`.
- L2 entry has `QCOW_OFLAG_COMPRESSED`: `Data { file_offset:
  compressed_offset }` (the file_offset is the start of the
  compressed cluster's encoded bytes; phase 1 does not need
  to decode the length).
- L2 entry has `QCOW_OFLAG_ZERO` set, standard L2: emit
  `ZeroAllocated`. If the masked offset is also non-zero,
  the cluster is `ZERO_ALLOC` (has a backing data cluster but
  reads as zero); still `ZeroAllocated` for map's purposes.
- Otherwise (normal allocated): `Data { file_offset:
  l2_entry & L2_OFFSET_MASK }`.

For extended L2 (the subcluster bitmap case): the
classification is per *subcluster*, not per cluster. Each
of the 32 subclusters of size `cluster_size / 32` is
classified by the (alloc bit, zero bit) pair in the
64-bit bitmap:
- alloc=0, zero=0: `Hole`.
- alloc=0, zero=1: `ZeroAllocated`.
- alloc=1, zero=0: `Data { file_offset: cluster_offset +
  subcluster_index * subcluster_size }`.
- alloc=1, zero=1: `ZeroAllocated` (zero overrides).

Subcluster-level emission then relies on the coalescer to
merge consecutive same-state subclusters back into one
extent on uniform-bitmap clusters. This is the right
factoring: the walker is per-subcluster; the coalescer
collapses the common cases (alloc_bits = `0xFFFFFFFF` or
zero_bits = `0xFFFFFFFF`) into one extent.

The qemu-img reference for the (alloc, zero) → state
decision is `block/qcow2-cluster.c
qcow2_co_block_status` — confirm during step 1c that the
boundary matches the version range our matrix covers.

Pure helper to add alongside
`count_allocated_in_l2_standard` /
`count_allocated_in_l2_extended`:

```rust
/// Classify one standard-L2 entry into a MapExtent state.
pub fn classify_qcow2_l2_standard(
    entry: u64,
    virtual_offset: u64,
    cluster_size: u64,
) -> MapExtent;

/// Classify the 32 subclusters of one extended-L2 entry into
/// a sequence of subcluster-sized MapExtents, pushed in order
/// through the supplied coalescer. The coalescer merges
/// adjacent same-state subclusters back into one extent.
pub fn classify_qcow2_l2_extended(
    l2_entry: u64,
    sc_bitmap: u64,
    virtual_offset: u64,
    cluster_size: u64,
    sink: &mut MapExtentCoalescer<'_, impl FnMut(MapExtent) -> bool>,
) -> bool;
```

Tests for `classify_qcow2_l2_standard`: every cluster type
(Hole, normal Data, compressed Data, ZeroAllocated with
zero-bit + offset=0, ZeroAllocated with zero-bit +
offset!=0).

Tests for `classify_qcow2_l2_extended`: all-zero bitmap
(32×Hole, collapses to 1 extent), all-alloc bitmap
(32×Data, collapses to 1 extent), all-zero-bits bitmap
(32×ZeroAllocated, collapses to 1), checkerboard alloc/Hole
(coalescer keeps 32 separate extents), one alloc subcluster
in a sea of Holes (3 extents), alloc + zero combined.

### Edge cases the walkers must handle correctly

- `virtual_size == 0`: emit nothing; return `Some(())`.
- `virtual_size > 2^63` (qcow2 cap): existing parsers reject;
  walkers inherit.
- L1 / BAT / GD entries pointing past EOF: existing walkers
  return `None` from the underlying sector read; walkers
  propagate `None`.
- Adversarial L2 / BAT with refcount-ordering attacks: walkers
  do not validate refcounts (that is `check`'s job); they
  classify entries exactly as `cluster_lookup` /
  `block_lookup` would, so the map output is consistent with
  what `info` / `convert` see.
- Walker called on a source with a backing-file pointer:
  walker emits the active layer only; the *guest binary*
  (phase 2) is responsible for refusing chain sources. The
  walker has no opinion on chain composition.
- Emit callback returning `false` mid-walk: walker pushes
  no further extents and returns `Some(())`. The coalescer's
  `finish()` is still called by the walker so any pending
  trailing extent flushes (or is discarded if the emitter
  already said stop).

### Trailing-hole emission

`qemu-img map` always emits a trailing extent that reaches
`virtual_size` even if it is a hole — the JSON array's last
entry covers `[last_data_end, virtual_size)`. The walkers
must do the same. The qcow2 walker already iterates L1 entries
to cover the full virtual range; ensure each format walker
either pushes Hole records for unwalked tail clusters or
relies on the coalescer's final flush to fill the gap. The
coalescer alone is not enough — if the walker simply stops at
the last allocated cluster, the trailing range vanishes.
Tests for each format must include "image ends with a hole"
to catch this.

## Open questions

1. **Compressed-cluster file-offset semantics**: qemu-img map
   reports a compressed cluster's file offset with the
   high-bit-set convention from `block/qcow2.c`
   (`offset | QCOW2_OFLAG_COMPRESSED_LARGE`). Phase 1 emits the
   plain offset; phase 4 (output formatting) decides whether
   to add the marker bit for wire compatibility. Default
   recommendation: emit the plain offset from the walker;
   apply the marker bit (if any) in the host-side renderer.
   Confirm during step 1c.

2. **Coalescing across L2 boundaries**: A sequence of
   contiguous-offset Data clusters that straddle two L2
   tables should coalesce into one extent. The current
   `scan_allocation` walker processes L2 tables one at a
   time. Verify that `map_extents` carries the coalescer's
   `pending` state across L2-table iterations rather than
   flushing per L2. The unit tests should include a
   two-L2-table fixture (small `cluster_size` so two L2
   tables fit in a tiny image) to catch a per-L2 flush bug.

3. **VMDK monolithicFlat without descriptor**: the existing
   vmdk parser supports both descriptor-driven and
   header-extension-driven layouts. Step 1d should match
   `scan_allocation`'s coverage exactly; don't widen.

4. **Should the walker pre-validate `virtual_size` against the
   on-disk header's `virtual_size`?** No. `scan_allocation`
   takes `virtual_size` as an argument because `Qcow2State`
   doesn't store it; the caller is responsible for passing
   the same value the rest of the operation uses. The walker
   inherits the same contract. (Phase 7's fuzz harness can
   feed a deliberately-wrong virtual_size and assert the
   walker doesn't panic — that exercises the trailing-hole
   path on adversarial input.)

5. **Returning `Some(())` vs a richer success type**: every
   other walker returns `Option<AllocationSummary>` with
   carry-back data. The map walker carries data through the
   callback, so the return is just a success/failure
   signal. `Option<()>` is the lightest weight; an
   `enum MapWalkResult { Complete, EarlyStop, Error }`
   would carry one more bit. Recommendation: keep
   `Option<()>`. The early-stop signal is already conveyed
   to the caller through the callback (the caller drove the
   stop).

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Add `MapExtent`, `MapExtentState`, and `MapExtentCoalescer` to `src/crates/shared/src/lib.rs` next to `AllocationSummary` (around line 525). Types are `#[derive(Clone, Copy, Debug, PartialEq, Eq)]` plain data; coalescer is a struct holding `pending: Option<MapExtent>` and `emit: &mut F`. The coalescer merges adjacent same-state extents per the rules in the Architecture section (state equality + virtual contiguity + Data file-offset contiguity). Write ≥10 unit tests in `#[cfg(test)] mod map_extent_tests`: empty input, single Data, two-contiguous-Data merge, two-Data with non-contiguous file_offsets must split, Hole + Hole merge, Hole + Data must split, Data + ZeroAllocated must split, abort on emitter returning false at first push, abort at second push, trailing flush emits pending extent. `make test-rust && make lint && pre-commit run --all-files`. **Do not touch any parser crate.** |
| 1b | low | haiku | none | Add `raw::map_extents` to `src/crates/raw/src/lib.rs` next to `scan_allocation` (around line 45). Trivial body: if `virtual_size == 0` return `Some(())`; otherwise call the supplied emitter once with `MapExtent { start: 0, length: virtual_size, state: Data { file_offset: 0 } }` and return `Some(())`. The function is pure — no CallTable parameter. Add 5 unit tests: `virtual_size == 0` emits nothing, `virtual_size == 512` emits one Data extent, `virtual_size == 1 GiB` likewise, emitter returning false on first call still returns `Some(())`, the emitted extent's fields are exactly right. |
| 1c | high | opus | worktree | Add `qcow2::classify_qcow2_l2_standard` and `qcow2::classify_qcow2_l2_extended` (pure helpers) plus `Qcow2State::map_extents` (outer walker) in `src/crates/qcow2/src/lib.rs`. Match `Qcow2State::scan_allocation` (line 1674) exactly for sector reading and L1/L2 traversal — the only change is the per-entry classification and that the coalescer carries `pending` across L2 boundaries rather than re-initialising per L2. Decision tree for standard L2 entries lives in `cluster_lookup` (line 1303); mirror it. For extended L2, the (alloc bit, zero bit) → state table is in the Architecture section. Compressed clusters: emit `Data { file_offset: entry & ((1 << 62) - 1) & !cluster_offset_low_bits }` — confirm the masking against `cluster_lookup`'s compressed-offset extraction. Add ≥15 unit tests covering: empty L2, all-standard-allocated, all-compressed, all-zero-plain (zero-bit + offset=0), all-zero-alloc (zero-bit + offset!=0), mixed standard, extended-L2 all-allocated (collapses to 1 extent), extended-L2 all-zero-bits, extended-L2 checkerboard (32 separate extents), extended-L2 one-alloc-amid-holes, two-L2-table walk with contiguous Data crossing the boundary (must coalesce), trailing Hole at end of image. **High effort because:** qcow2 cluster classification is the highest-risk surface in the phase and the coalescer-across-L2 case is the easy-to-miss bug. |
| 1d | high | opus | worktree | Add `vmdk::classify_vmdk_gt_entry` (pure helper) plus `VmdkState::map_extents` (outer walker) in `src/crates/vmdk/src/lib.rs`. Match `VmdkState::scan_allocation` (line 817) for sector reading and GD/GT traversal. Single-extent monolithicSparse and monolithicFlat only — multi-extent layouts produce an error matching `scan_allocation`'s existing behaviour. ZERO_GRAIN_MARKER is `0xFFFFFFFE` (check whether the existing crate exports a constant; if so use it, otherwise hard-code with a comment cross-referencing the spec). Add ≥8 unit tests: empty GT, all-zero entries (Hole), all-ZERO_GRAIN_MARKER (ZeroAllocated), all allocated with consecutive file offsets (coalesces), mixed Hole/Data/ZeroAllocated, two-GT walk with contiguous Data crossing the GT boundary (must coalesce), trailing Hole at end. **High effort because:** the GD → GT two-level walk has the same cross-boundary coalescing risk as qcow2, and vmdk's offset arithmetic (sectors × SECTOR_SIZE) is a fertile bug spot. |
| 1e | medium | sonnet | none | Add `vhd::classify_vhd_bat_entry` (pure helper) plus `VhdState::map_extents` (outer walker) in `src/crates/vhd/src/lib.rs`. Match `VhdState::scan_allocation` (line 661) for the sector-walking shell. Fixed VHD: single Data extent covering `current_size`. Dynamic / Differencing: per-BAT-entry classification: `0xFFFFFFFF` → Hole, otherwise Data with `file_offset = entry_sector * SECTOR_SIZE + sector_bitmap_size`. The `sector_bitmap_size` is already available on `VhdState`; confirm by reading the struct definition. Add ≥6 unit tests for the helper (Hole, three allocated with different file offsets, coalescing across two consecutive BAT entries, edge: max u32 minus one). |
| 1f | medium | sonnet | none | Add `vhdx::classify_vhdx_bat_entry` (pure helper) plus `VhdxState::map_extents` (outer walker) in `src/crates/vhdx/src/lib.rs`. Match `VhdxState::scan_allocation` (line 987) for the chunk_ratio-aware BAT walk. State enum: see PLAN; `PAYLOAD_BLOCK_PARTIALLY_PRESENT` treated as Data in v1. File offset extraction: `(entry >> 20) << 20` (top bits are 1 MiB-unit offset). Add ≥7 unit tests for the helper: every state value (NOT_PRESENT/UNDEFINED/ZERO/UNMAPPED → Hole, FULLY/PARTIALLY → Data), three Data entries with consecutive 1 MiB offsets (coalesce), ZERO → Hole transition. |

Total: 6 commits.

### Why 1c and 1d are high-effort opus, in worktree isolation

- 1c (qcow2): the extended-L2 subcluster classification is the
  one place in the phase where the wrong (alloc, zero) →
  state table silently produces wrong but plausible map
  output. Worktree isolation gives us a safe sandbox if the
  classification needs iteration against the qemu-img source
  code.
- 1d (vmdk): the GD → GT walk has the same cross-boundary
  coalescing trap as qcow2, plus vmdk's `ZERO_GRAIN_MARKER`
  is unique to the format and easy to miss. Worktree
  isolation protects against the parser drift the existing
  scan_allocation walks have been refactored around.

The other three (1a coalescer, 1b raw, 1e vhd, 1f vhdx) are
mechanical enough to run in the main tree.

### Out of scope for phase 1

- No call-table additions (walkers use the existing
  `read_input_sector` and the existing per-State cache
  buffers).
- No proto changes (phase 2).
- No guest binary (phase 2).
- No host CLI (phase 3).
- No baseline generation (phase 5).
- No integration tests against real testdata images (phase 6);
  phase 1 unit tests cover only the pure helpers and the
  coalescer.
- No fuzz harness updates (phase 7 — but writing the helpers
  with a clean byte-slice signature is what *enables* phase 7
  to fuzz them with no extra plumbing).
- No `luks::map_extents` (LUKS mapping deferred alongside
  LUKS measurement; same future-work entry).
- No `start_offset` / `max_length` window filtering — the
  walkers always cover the full virtual range. Phase 2's
  guest binary clamps via the emit callback returning
  `false` when the desired window is exhausted.
- No `scan_allocation` refactor onto the walker (deferred —
  see Architecture section).

## Success criteria

- Each parser crate (raw, qcow2, vmdk, vhd, vhdx) exposes a
  `map_extents` entry point returning `Option<()>` and
  emitting `shared::MapExtent` records through the supplied
  callback.
- `MapExtent`, `MapExtentState`, and `MapExtentCoalescer` live
  in `src/crates/shared/src/lib.rs`.
- Each format crate has new pure helpers
  (`classify_*_entry` family) testable from a byte slice or
  raw entry value with no I/O. Total ≥ 51 new unit tests
  across the six commits (10 coalescer + 5 raw + 15 qcow2 +
  8 vmdk + 6 vhd + 7 vhdx).
- `make instar` builds and `make lint` is clean.
- `make test-rust` passes; existing scanner / measure tests
  (measure totals: 62) are unchanged.
- `pre-commit run --all-files` passes.
- No regression in the existing measure baseline matrix
  (phase 1 does not touch `scan_allocation` so this should
  be free; verify by running `make test-integration
  TEST=test_measure` after step 1f).
- The coalescer-across-table-boundary case is covered by at
  least one unit test in each of 1c (qcow2 two-L2) and
  1d (vmdk two-GT).
- Trailing-hole emission is covered by at least one unit
  test in each format walker.

## Risks and mitigations

- **Coalescer + L2-boundary bug**: the easy mistake is to
  flush the coalescer's pending extent at the end of each
  L2 table, splitting an extent that should have spanned the
  boundary. Mitigation: 1c's two-L2 unit test catches this
  directly. Reviewer should read the walker to confirm
  `coalescer` outlives the inner L2 loop.

- **qcow2 extended-L2 subcluster bitmap math**: the (alloc,
  zero) → state mapping is small but easy to encode wrong.
  Mitigation: 1c's unit tests pin every cell of the 2×2
  table (alloc=0/zero=0, 0/1, 1/0, 1/1) plus
  representative checkerboard and all-uniform cases. Cross-
  check against `cluster_lookup`'s decision tree.

- **Compressed-cluster file_offset width**: qcow2 packs the
  compressed offset with the length-in-sectors field, and
  the bit count for the length varies by cluster size. The
  walker reads the offset only; confirm the mask against
  `cluster_lookup`'s extraction during 1c, not after.

- **VMDK ZERO_GRAIN_MARKER constant**: check during 1d
  whether the existing crate exports it. If not, declare
  locally with a `// vmdk spec, qemu-img block/vmdk.c`
  comment matching what 2e of PLAN-measure did.

- **VHDX `PAYLOAD_BLOCK_PARTIALLY_PRESENT` classification**:
  treating it as Data overcounts allocated bytes vs
  qemu-img's per-sector-bitmap walk. Mitigation: same
  posture as `scan_allocation`; document as a known
  divergence and let phase 8's differential fuzzer surface
  any cases where the gap actually matters.

- **Sector-bitmap-size offset on VHD**: 1e's brief assumes
  `VhdState` exposes `sector_bitmap_size`. If it doesn't,
  step 1e needs to compute it (one sector per block_size
  per 512 virtual bytes, rounded to a sector boundary).
  Sub-agent should verify by reading the struct definition
  before writing the walker.

- **MapExtent in shared adds a public type**: minor surface
  change; backwards-compatible (additive). No re-exports
  needed for back-compat (unlike the AllocationSummary move
  in PLAN-measure phase 2a) because `MapExtent` is new.

## Back brief

Before executing any step, the executing agent should
back-brief: which crate, which helper, which existing
function in the crate is being mirrored, and what the
test fixture looks like. The reviewer should confirm that
no step bleeds into phase 2 (guest binary, proto, config),
phase 5 (baselines), or phase 6 (integration tests against
real testdata images). The reviewer should specifically
verify that `scan_allocation` is **untouched** in every
step — the walker / scanner consolidation is deferred to
a follow-up.
