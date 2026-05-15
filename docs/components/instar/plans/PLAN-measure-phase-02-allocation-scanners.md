# Phase 2: source-allocation scanners on the parser crates

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-01-calculators.md](/components/instar/plans/PLAN-measure-phase-01-calculators/)

## Status: Not started

## Mission

Each parser crate (`raw`, `qcow2`, `vmdk`, `vhd`, `vhdx`)
gains a `scan_allocation()` function that walks the on-disk
metadata once and returns an `AllocationSummary { virtual_size,
allocated_bytes }`. This is the second input to the
`crates/measure/` math: phase 1 answers "given an
`AllocationSummary`, how big is the target?" — phase 2 produces
the `AllocationSummary` for a real source image.

Phase 2 ships only the library code. The guest binary that
calls these scanners arrives in phase 3; the host CLI in phase
4; the integration tests that exercise them against real
testdata images in phase 7. Phase 2's unit tests cover the
*pure* slice-walking helpers; the CallTable-driven outer
wrappers are exercised end-to-end later.

## Why this is its own phase

- The work is mechanically simple per format (each parser
  already walks its tables in `cluster_lookup` /
  `grain_lookup` / `block_lookup`), but it spans five crates
  and one shared-types relocation. Splitting from phase 1
  (pure math) and phase 3 (guest binary, proto, call-table
  config) keeps each commit small enough to review.
- Putting the scanners on the parser crates rather than in
  `crates/measure/` keeps `measure` `no_std` *and* free of any
  CallTable dependency, which matters for the fuzz harness in
  phase 8 — the calculators stay pure functions over plain
  numbers.
- The `AllocationSummary` relocation in step 2a unblocks the
  rest: until it lives in `shared`, parsers cannot return it
  without creating a `qcow2 → measure → qcow2` cycle.

## Architecture

### Type relocation: `AllocationSummary` → `shared`

Phase 1 placed `AllocationSummary` in `crates/measure/`. The
parsers now need to *produce* it, but they cannot depend on
`measure` (which already depends on them for constants).
Both layers depend on `shared` (`no_std`, no I/O of its own
beyond the call-table function-pointer struct), so that is
the natural home.

Step 2a moves the type. The struct is small (two `u64`s plus
derives) and has no methods, so the move is a cut/paste plus a
re-export from `measure` for backwards compat:

```rust
// crates/measure/src/lib.rs
pub use shared::AllocationSummary;  // back-compat re-export
```

Existing phase 1 tests reference `AllocationSummary` via
`use super::*;` which the re-export keeps working with no
test changes.

### Two-layer scanner design

For every parser crate, `scan_allocation` is layered:

1. **Pure helpers** (no I/O, `no_std`): given an already-read
   metadata slice (an L2 table, a grain table, a BAT region,
   etc.) return the count of allocated entries. These are the
   functions phase 2 unit tests exercise.
2. **Thin outer wrapper** (CallTable-driven): walks the
   top-level table (L1 / GD / single BAT), reads each
   sub-table on demand using the existing cached sector
   readers, and sums the per-helper counts.

Why split: the inner helpers are deterministic functions over
byte slices and easy to unit-test. The outer wrappers need
mock CallTables — building a fully-valid synthetic image in
each parser's `#[cfg(test)]` is high-cost boilerplate that
phase 7 covers anyway via real testdata. So phase 2 adds
~150 LoC of pure-helper tests rather than ~600 LoC of
synthetic-image plumbing.

The split also matches phase 8: the inner helpers slot
straight into `fuzz_measure_scan.rs` without needing the
mock CallTable infrastructure.

### Per-format scanner specifications

#### `raw::scan_allocation`

Trivial. Raw has no allocation metadata; everything is
"allocated":

```rust
pub fn scan_allocation(virtual_size: u64) -> AllocationSummary {
    AllocationSummary {
        virtual_size,
        allocated_bytes: virtual_size,
    }
}
```

No CallTable needed — raw has no on-disk metadata to walk.
The host already knows `virtual_size` from the device
capacity. Three or four trivial unit tests; no helpers to
extract.

#### `vhd::scan_allocation`

Walk the dynamic-VHD BAT (one contiguous u32-be array, one
entry per virtual block, `0xFFFFFFFF` = unallocated). For
fixed VHDs, every byte is allocated (`allocated_bytes ==
virtual_size`).

Pure helper:

```rust
/// Given a BAT byte slice (length = `total_blocks * 4`,
/// entries are big-endian u32), return the count of
/// allocated blocks (entries != 0xFFFFFFFF).
pub fn count_allocated_in_bat(bat_bytes: &[u8]) -> u64 {
    bat_bytes
        .chunks_exact(4)
        .filter(|c| u32::from_be_bytes([c[0], c[1], c[2], c[3]]) != 0xFFFF_FFFF)
        .count() as u64
}
```

Outer wrapper (signature mirrors the existing methods on
`VhdState`, e.g. `block_lookup`):

```rust
impl VhdState {
    pub unsafe fn scan_allocation(
        &mut self,
        call_table: &CallTable,
        sector_size: usize,
        input_capacity: u64,
        bytes_read: &mut u64,
    ) -> Option<AllocationSummary>;
}
```

For `disk_type=Fixed`: short-circuit to
`AllocationSummary { virtual_size, allocated_bytes:
virtual_size }`. For `disk_type=Dynamic`: read the BAT in
`MAX_SECTOR_SIZE`-sized chunks, accumulating the per-chunk
helper count, then multiply by `block_size`.

#### `vhdx::scan_allocation`

VHDX BAT is also contiguous, but with two complications:

1. **Interleaved sector-bitmap entries**: every `chunk_ratio`
   payload-block entries are followed by one sector-bitmap
   entry (PAYLOAD_BLOCK_BITMAP, state value 6). These do not
   represent payload blocks and must be skipped during the
   count.
2. **Multi-state entries**: entries are 64-bit, with the
   low 3 bits being the state (`PAYLOAD_BLOCK_NOT_PRESENT`,
   `_UNDEFINED`, `_ZERO`, `_UNMAPPED`, `_FULLY_PRESENT`,
   `_PARTIALLY_PRESENT`, `_BITMAP`). Allocated states for
   measurement purposes: `FULLY_PRESENT` (5) and
   `PARTIALLY_PRESENT` (6). `ZERO` does not count (the
   block is logically all-zero; no host bytes consumed).

Pure helper:

```rust
/// Count payload-block entries that are FULLY_PRESENT or
/// PARTIALLY_PRESENT. `bat_bytes` is the BAT region, including
/// interleaved sector-bitmap entries (which are skipped).
/// `chunk_ratio` is the number of payload entries per
/// sector-bitmap entry; `total_payload_blocks` caps the count
/// (later entries are unused tail).
pub fn count_allocated_in_bat(
    bat_bytes: &[u8],
    chunk_ratio: u32,
    total_payload_blocks: u32,
) -> u64;
```

The BAT contains repeating groups of `chunk_ratio` payload
entries followed by 1 sector-bitmap entry. Use modular
arithmetic on the entry index to skip the bitmap entries.

Outer wrapper: read the BAT region, call helper, multiply
by `block_size`.

`PARTIALLY_PRESENT` is rare in convert-produced output (instar
only emits `FULLY_PRESENT` or `NOT_PRESENT`) and treating it
as one full block is a slight overcount on adversarial input.
That overcount is qemu-img-compatible and matches what
`required` is allowed to be: an upper bound. Note this in
a code comment.

#### `vmdk::scan_allocation`

VMDK has a two-level metadata structure: the grain
directory (GD) is a contiguous u32-le table of grain-table
sector pointers; each grain table (GT) is a 512-entry
u32-le table of grain sector pointers. A non-zero GT entry
means the grain is allocated. Unlike the others, the GT
walk requires per-GD-entry I/O (each GT lives at the
sector that GD points to).

Pure helpers:

```rust
/// Count non-zero entries in a grain-directory byte slice.
/// Each entry is a u32 LE sector pointer. Returns count of
/// populated GTs.
pub fn count_populated_gd_entries(gd_bytes: &[u8]) -> u64;

/// Count non-zero entries in a grain-table byte slice.
/// Each entry is a u32 LE sector pointer. Returns the count
/// of allocated grains in this GT.
///
/// Note: a `0xFFFFFFFE` entry (zero-grain marker for
/// monolithicSparse output written by qemu-img) is logically
/// "explicit zero" and counts as zero, matching qemu-img
/// measure semantics. Implement the helper to skip both
/// `0` and `vmdk::ZERO_GRAIN_MARKER`.
pub fn count_allocated_in_gt(gt_bytes: &[u8]) -> u64;
```

Outer wrapper:

```rust
impl VmdkState {
    pub unsafe fn scan_allocation(
        &mut self,
        call_table: &CallTable,
        sector_size: usize,
        input_capacity: u64,
        bytes_read: &mut u64,
    ) -> Option<AllocationSummary>;
}
```

Walk the GD entries (in `MAX_SECTOR_SIZE` chunks via the
existing `gd_cache_buf`). For each non-zero GD entry, read
the GT it points to and call `count_allocated_in_gt`. Sum,
multiply by `grain_size_bytes`.

`virtual_size = capacity_sectors * 512`.

Multi-extent flat / streamOptimized footer cases: the scanner
operates on whichever extent the active VmdkState describes.
For multi-extent flat, allocated_bytes == virtual_size of that
extent (flat is dense), and the host-side code in phase 3
sums across extents. For streamOptimized, the GD/GT walk
works the same as monolithicSparse — the markers do not
change the layout.

#### `qcow2::scan_allocation`

The most complex of the four. QCOW2 has an L1 → L2 chain;
extended L2 entries carry a per-cluster subcluster bitmap.
Encryption, compression, and backing chains all interact:

- **Backing file**: the scanner reports allocations at the
  *active* image only. Holes in the active image that get
  filled from the backing file count as unallocated for the
  active scan. Phase 3's host code calls `scan_allocation`
  per chain layer and sums across the chain (with
  shadowing, since allocations in upper layers obscure
  lower ones — the simplest sound rule is to sum the active
  layer plus the *unshadowed* parts of each backing layer).
  Phase 2 ships only the per-layer scan; the chain logic
  is in phase 3.
- **Compression**: a compressed L2 entry counts as one full
  source cluster. (Compressed clusters contain valid data;
  the scanner is counting "is there data here", not "how
  many host bytes does it take".)
- **Extended L2 with partial subclusters**: each L2 entry
  has a 32-bit `alloc_bits` and 32-bit `zero_bits`. For the
  measure count, the active sub-cluster count is
  `popcount(alloc_bits)`; treat each as `subcluster_size =
  cluster_size / 32` bytes. Zero subclusters
  (`zero_bits` set, `alloc_bits` unset) are *not* counted
  (logically all-zero, no host bytes). Standard L2 entries
  count as full clusters.
- **Encrypted images**: the scanner walks the same L1/L2
  metadata; encryption only affects data reads. No special
  handling needed.

Pure helpers:

```rust
/// Count allocated source bytes in a standard (8-byte entry)
/// L2 table slice. Returns bytes, not entry count, because the
/// caller does not always have `cluster_size` in scope when
/// composing across L2 tables. `cluster_size` is the source
/// cluster size in bytes.
pub fn count_allocated_in_l2_standard(
    l2_bytes: &[u8],
    cluster_size: u64,
) -> u64;

/// Count allocated source bytes in an extended-L2 (16-byte
/// entry) table slice. Counts standard entries as
/// `cluster_size`, and subcluster entries by popcount of
/// `alloc_bits` * `cluster_size / 32`. Compressed entries
/// count as `cluster_size`.
pub fn count_allocated_in_l2_extended(
    l2_bytes: &[u8],
    cluster_size: u64,
) -> u64;
```

Outer wrapper:

```rust
impl Qcow2State {
    /// Walk L1, read each populated L2, sum allocated bytes.
    /// Reports active-image only; chain handling is the
    /// caller's responsibility.
    pub unsafe fn scan_allocation(
        &mut self,
        call_table: &CallTable,
        sector_size: usize,
        input_capacity: u64,
        virtual_size: u64,
        bytes_read: &mut u64,
    ) -> Option<AllocationSummary>;
}
```

`virtual_size` is passed in because `Qcow2State` does not
currently store it (the existing readers do not need it).
Adding it to the struct is also viable; passing it keeps
the change small.

The walk is L1 (`l1_size` u64-be entries) → for each
non-zero L1 entry → read the L2 cluster (`cluster_size`
bytes) → call the matching pure helper. Sum.

### Why not reuse `cluster_lookup` / `grain_lookup` per
virtual offset

The existing per-offset lookup methods take a virtual offset
and return a `ClusterLookup`. Calling them in a loop over
every cluster would yield a correct count but with O(n)
redundant L1/L2 reads per call. Walking the metadata directly
is the same code the writer uses (and the same code phase 8's
fuzz harness will exercise), and matches what `qemu-img info`
does internally. Cost in the largest realistic case (1 TiB
qcow2, 64 KiB cluster, ~16M clusters): a direct scan reads
~512 MB of metadata once; a per-offset lookup pass reads it
~16M times. The direct walk is the only viable option for
the host-side cron of integration tests.

### Edge cases the scanners must handle correctly

- `virtual_size == 0` (degenerate but valid input from a
  mis-built image): return `AllocationSummary { virtual_size:
  0, allocated_bytes: 0 }`.
- `virtual_size > 2^63` (per the qcow2 cap): the underlying
  parser already rejects these in `init`; the scanner inherits
  the rejection.
- L1 entries pointing past EOF: the existing `cluster_lookup`
  returns `None`; the scanner should propagate `None` from any
  failing read (caller handles it as an error).
- Adversarial L2 with refcount-ordering attacks: the scanner
  does not validate refcounts (that is `instar check`'s job).
  It just counts L2 entries that would be considered
  allocated by `cluster_lookup`. Mirroring the same
  decision-tree avoids divergence.
- `extended_l2` images where every subcluster is unallocated
  but bitmap bits are set: the spec allows
  `alloc_bits = 0, zero_bits = 0xFFFFFFFF` (all explicit
  zeros). Count as 0, matching qemu-img.

## Open questions

1. **Should compressed clusters count as `cluster_size` or as
   the *compressed* on-disk size?** For `measure` semantics
   (estimating output file size), the source-side decompressed
   size is what matters — the target writer will re-encode.
   So count as `cluster_size`. Confirm during phase 7 that
   round-trip to qcow2 with `-c` matches expectations.

2. **VHDX `PARTIALLY_PRESENT`**: count as one block
   (overcount, qemu-img-compatible) or sum the
   sector-bitmap bits (exact)? For phase 2 the simple
   answer is the safer `required`-bound; the exact
   sector-bitmap sum is a phase-2 follow-up if we ever see
   it produce visibly wrong numbers in differential
   fuzzing. Default: count as one block.

3. **Backing chain**: phase 2 reports active-image only.
   Phase 3 will sum across the chain. Should the chain
   summing live in the host (loops over chain entries,
   calls `scan_allocation` per layer) or in the guest
   (one operation invocation that walks the chain)? The
   simpler answer is host-side, matching how
   `instar info --chain` works; defer the decision to
   phase 3 and revisit if the guest needs a richer signal.

4. **`Qcow2State::virtual_size`**: add the field or pass it
   as an argument? The field is more ergonomic but is
   technically a new public state. For phase 2, **pass as
   an argument** — keeps the struct unchanged. Phase 3 may
   re-evaluate when wiring into the guest.

5. **VMDK `ZERO_GRAIN_MARKER`**: the constant
   `0xFFFFFFFE` exists in the vmdk crate as
   `ZERO_GRAIN_MARKER` (verify; if not exported, hard-code
   with a comment). The pure helper must skip it.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | low | sonnet | none | Move `AllocationSummary` from `src/crates/measure/src/lib.rs` to `src/crates/shared/src/lib.rs` (a `#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)] pub struct AllocationSummary { pub virtual_size: u64, pub allocated_bytes: u64 }`). Re-export from measure with `pub use shared::AllocationSummary;`. Run `make test-rust` and confirm all 62 measure tests still pass. Run `make lint` and `pre-commit run --all-files`. **Do not edit any other crate.** |
| 2b | low | sonnet | none | Add `raw::scan_allocation(virtual_size: u64) -> AllocationSummary` in `src/crates/raw/src/lib.rs`. Trivial body: returns `AllocationSummary { virtual_size, allocated_bytes: virtual_size }`. Add 4 unit tests (`virtual_size = 0`, `512`, `1 GiB`, `u64::MAX`). The function is pure — no CallTable. Run lint, test, pre-commit. |
| 2c | medium | sonnet | none | Add `vhd::count_allocated_in_bat(bat_bytes: &[u8]) -> u64` (pure helper, counts u32-be entries `!= 0xFFFF_FFFF`) plus `VhdState::scan_allocation(...)` (outer wrapper using the existing `bat_cache_buf`-backed reader). Brief specifies the signature, the BAT entry size (4 bytes BE), and the unallocated marker (`0xFFFF_FFFF`). For `disk_type=Fixed`, short-circuit. Add ≥6 unit tests for the helper covering: empty BAT, all-allocated BAT (4 entries), all-unallocated, mixed (2 of 5 allocated), adversarial single-byte BAT (length not a multiple of 4 — `chunks_exact(4)` discards the tail; assert the count uses only complete entries), large BAT (1024 entries with every 7th allocated). |
| 2d | medium | opus | none | Add `vhdx::count_allocated_in_bat(bat_bytes: &[u8], chunk_ratio: u32, total_payload_blocks: u32) -> u64` plus `VhdxState::scan_allocation`. The helper must skip interleaved sector-bitmap entries (every `chunk_ratio` payload entries) and count entries whose low-3-bit state is `PAYLOAD_BLOCK_FULLY_PRESENT` (5) or `PAYLOAD_BLOCK_PARTIALLY_PRESENT` (6). `chunk_ratio = 0` is invalid input → return 0 (or assert). Brief includes the entry layout (8-byte LE, low 3 bits state) and the canonical state values. Add ≥6 unit tests: zero entries, 1 chunk_ratio worth + bitmap, all FULLY_PRESENT, mix of states (FULLY, PARTIALLY, ZERO, NOT_PRESENT), `total_payload_blocks` clipping. **Medium-high effort because:** the chunk_ratio interleaving + multi-state encoding is exactly where one-off bugs hide. Cross-check against `vhdx::PAYLOAD_BLOCK_*` constants. |
| 2e | medium | sonnet | none | Add `vmdk::count_populated_gd_entries(gd_bytes: &[u8]) -> u64` and `vmdk::count_allocated_in_gt(gt_bytes: &[u8]) -> u64` (pure helpers; both walk u32-LE arrays. The GT helper counts `entry != 0` *and* `entry != ZERO_GRAIN_MARKER` — verify the constant name in the vmdk crate, hard-code `0xFFFFFFFE` with a comment if not exported). Plus `VmdkState::scan_allocation`. Brief specifies the GD/GT entry sizes, the `ZERO_GRAIN_MARKER` value, and the existing cache buffer use pattern (`gd_cache_buf`, `gt_cache_buf`). Add ≥7 unit tests: empty GD, populated GD (3 of 8 entries), GT with all zeros, GT with all allocated, GT with `ZERO_GRAIN_MARKER` mixed in (must be excluded), GT with adversarial single-byte tail. |
| 2f | high | opus | none | Add `qcow2::count_allocated_in_l2_standard(l2_bytes: &[u8], cluster_size: u64) -> u64` and `qcow2::count_allocated_in_l2_extended(l2_bytes: &[u8], cluster_size: u64) -> u64`, plus `Qcow2State::scan_allocation(call_table, sector_size, input_capacity, virtual_size, bytes_read)`. The standard helper counts u64-be entries: `entry != 0` (matches `cluster_lookup`'s "Standard" or "Compressed" verdict), masking off `OFLAG_COMPRESSED` and `L2_OFFSET_MASK` is unnecessary — any non-zero entry is allocated for measure purposes. The extended helper iterates 16-byte entries; for each, decode `(l2_entry, sc_bitmap)` (both u64-be); standard non-zero entry → `cluster_size`; if extended-L2 with `l2_entry == 0` and `(sc_bitmap >> 32) != 0` (zero subclusters) → 0; if `l2_entry != 0` and `extended_l2` with bitmap → `popcount(alloc_bits) * (cluster_size / 32)` where `alloc_bits = sc_bitmap as u32`. The outer wrapper walks L1 (read in cached chunks via `l1_cache_buf`), reads each L2 (via `l2_cache_buf`, `cluster_size`-sized), calls the helper, sums. Brief includes the existing `cluster_lookup` decision tree as the reference (`src/crates/qcow2/src/lib.rs:1303` — match the same allocated/unallocated boundary). Add ≥10 unit tests: empty L2, all standard allocated, mix of standard/zero, extended L2 with full subcluster allocation, extended L2 with partial subcluster (count `popcount * sc_size`), extended L2 with all-zero subclusters (count 0), extended L2 with compressed entry (count `cluster_size`), boundary cases (cluster_size=512 with 64 entries / cluster_size=2 MiB with 262144 entries). **High effort because:** the extended-L2 subcluster math is the part of QCOW2 most likely to silently disagree with qemu-img. The unit tests are the contract. |
| 2g | low | sonnet | none | Update `ARCHITECTURE.md` to mention the new `scan_allocation` functions on each format crate (one line in each crate's existing paragraph; e.g. add ", plus an `AllocationSummary` producer used by phase 1's measure crate"). Update `CHANGELOG.md` Unreleased / Added. Run `pre-commit run --all-files`. |

Total: 7 commits.

### Why 2d and 2f are high-priority for opus

- 2d (VHDX): the chunk_ratio interleaving is a non-trivial
  invariant. A wrong skip rule produces silently wrong
  counts that are within an order of magnitude of right —
  exactly the kind of bug that escapes review.
- 2f (QCOW2 extended L2): subcluster bitmap math is unique
  to extended L2 and the only place phase 2 needs `popcount`.
  Subtle off-by-thirty-twos here could flow into every phase
  6 baseline.

### Out of scope for phase 2

- No call-table additions (the scanners use the existing
  `read_input_sector` and the existing per-State cache
  buffers).
- No proto changes (phase 3).
- No guest binary (phase 3).
- No host CLI (phase 4).
- No baseline generation (phase 6).
- No integration tests against real testdata images (phase 7);
  phase 2 unit tests cover only the pure helpers.
- No fuzz harness updates (phase 8 — but writing the helpers
  with a clean byte-slice signature is what *enables* phase
  8 to fuzz them with no extra plumbing).
- No `luks::scan_allocation` (LUKS measurement deferred to
  the master plan's Future work).
- No backing-chain composition logic (phase 3 host code).
- No `Qcow2State::virtual_size` field (pass as argument; the
  state struct stays unchanged).

## Success criteria

- Each parser crate (raw, qcow2, vmdk, vhd, vhdx) exposes a
  `scan_allocation` entry point returning
  `shared::AllocationSummary`.
- `AllocationSummary` lives in `src/crates/shared/src/lib.rs`,
  with a back-compat re-export from `crates/measure`.
- Each format crate has new pure helpers (`count_allocated_*`)
  testable from a byte slice with no I/O. Total ≥ 33 new unit
  tests across the five crates.
- `make instar` builds and `make lint` is clean.
- `make test-rust` passes; the per-crate test counts increase
  by at least: raw +4, vhd +6, vhdx +6, vmdk +7, qcow2 +10,
  measure stays at 62.
- `pre-commit run --all-files` passes.
- `ARCHITECTURE.md` and `CHANGELOG.md` are updated.

## Risks and mitigations

- **`AllocationSummary` move breaks downstream usage**.
  Mitigation: re-export from measure as a back-compat alias
  (step 2a). The phase 1 tests use `use super::*;` and pick
  the re-export with no source change. Run `make test-rust`
  immediately after 2a to confirm.
- **VHDX state values**: instar's writer only emits
  `FULLY_PRESENT` and `NOT_PRESENT`; adversarial images may
  carry the others. Mitigation: 2d's brief enumerates every
  state and tests cover the full mix. The match arm uses
  named constants from the `vhdx` crate (verify exports)
  rather than magic numbers.
- **QCOW2 `cluster_size = 512` corner**: extended L2 needs
  `cluster_size >= 16384` to make sense (32 subclusters × 512
  bytes minimum). For `cluster_size < 16384` the helper would
  silently return zero subcluster bytes; the input is invalid
  and the writer would never produce it, but the helper
  should still not panic. Mitigation: 2f tests include
  `cluster_size = 512` standard L2 to cover the small cluster
  edge.
- **VMDK `ZERO_GRAIN_MARKER` not exported**: confirm during
  step 2e; if not exported, declare it locally in the helper
  with a `// vmdk spec, qemu-img block/vmdk.c` comment.
- **Phase 7 round-trip mismatch**: scanners inevitably
  estimate slightly differently from `qemu-img measure` for
  partial-allocation cases. Phase 7 will surface concrete
  numbers; phase 2 plans for that by keeping the helpers
  small and adjustable rather than hand-tuning constants.

## Back brief

Before executing any step, the executing agent should
back-brief: which crate, which helper, which existing
function in the crate is being mirrored, and what the
test fixture looks like. The reviewer should confirm that
no step bleeds into phase 3 (guest binary, proto, config),
phase 6 (baselines), or phase 7 (integration tests).
