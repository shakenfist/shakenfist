# Phase 1: per-format size calculators (`crates/measure/`)

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/)

## Status: Not started

## Mission

Build a new `no_std` crate `src/crates/measure/` whose only job
is to answer the question "if I asked instar to write `N` bytes
of allocated data into a fresh `<format>` image of virtual size
`V` with options `O`, how big would the file be?". Two flavours:

- `required`: the size if instar's sparse writer skips holes
  exactly the way `convert` does today.
- `fully_allocated`: the size if every cluster/grain/block in
  the virtual range were allocated.

This is a pure library phase. It must not touch the call table,
guest binaries, host CLI, parsers, or any I/O. Phase 2 onwards
consumes this crate; phase 8 fuzzes it.

## Why this is its own phase

The arithmetic for each output format already exists *inside*
`src/operations/convert/src/main.rs` (entangled with the
streaming writer). Extracting it has three benefits:

1. The math becomes unit-testable in plain `cargo test` with no
   KVM, no virtio-block, no scratch memory layout. Bugs are
   pinned exactly where the wrong constant lives.
2. The fuzz harness in phase 8 becomes trivial — feed in random
   `(virtual_size, allocated_bytes, options)` and assert
   invariants. No mock CallTable needed.
3. Future operations (`create`, `resize`, eventual `convert`
   refactor) can call this crate instead of re-deriving the
   formulas. The plan does not deliver that refactor here, but
   the API is shaped so it can.

Splitting it from "wire into the guest" (phase 3) means we can
land the calculators, see them tested, and ship them
independently of the operation binary.

## Existing code to mine, not duplicate

Copy the *formulas* from these locations, but keep the new
crate independent of `convert` (no shared code yet). Convert
will be refactored to call this crate later (Future work in
the master plan); doing it now bloats the phase.

| Output format | Reference in convert | Key numbers |
|---|---|---|
| qcow2 | `calculate_refcount_layout()` at `src/operations/convert/src/main.rs:1716` | `entries_per_refblock = cluster_size / 2` (16-bit refcounts), 10-iteration fixed point |
| qcow2 layout | `init_qcow2_output_layout()` at `src/operations/convert/src/main.rs:1876` | `l1_size = ceil(virtual_size / l2_coverage)`, `l2_coverage = cluster_size * (cluster_size / l2_entry_size)`; `l2_entry_size = 8` standard, `16` extended |
| vmdk monolithicSparse | `init_vmdk_output_layout()` at `src/operations/convert/src/main.rs:2898` | header = 512B, descriptor = `vmdk::DESC_SECTORS * 512`, `gtes_per_gt = vmdk::DEFAULT_NUM_GTES_PER_GT` (512), GT entry = 4B, GD entry = 4B |
| vhd dynamic | `convert_to_vhd_dynamic` (search `block_size`/`bat_entries`) | footer 512B ×2, dynamic header 1024B, BAT entry 4B, sector bitmap = `ceil(block_size / 512 / 8)` rounded to 512B sector |
| vhdx dynamic | layout block at `src/operations/convert/src/main.rs:4140` | file ID + headers + region tables consume 0x10_0000 (1 MiB), log = 1 MiB, BAT region 1 MiB-aligned, metadata region 1 MiB, payload from `payload_start` |
| vhd helpers | `vhd::compute_vhd_geometry`, `vhd::FOOTER_SIZE` (512), `vhd::DYNAMIC_HEADER_SIZE` (1024) | `src/crates/vhd/src/lib.rs:19,57` |
| vhdx helpers | `vhdx::MB_ALIGN` (1 MiB), `vhdx::HEADER_SIZE` (4096), `vhdx::calculate_bat_layout` | `src/crates/vhdx/src/lib.rs:182,84` |

Re-use the constants from `vhd` / `vhdx` / `vmdk` crates by
adding them as dependencies of `crates/measure/`. Do **not**
re-export or duplicate them.

## qemu-img reference behaviour to match

For raw and qcow2 outputs, the answer is fixed by qemu-img's
own code (`block/qcow2.c`, function `qcow2_measure`). Spot-checks
during plan authoring against locally-installed qemu-img:

```
$ qemu-img measure --size 10M --output=json -O raw
{ "required": 10485760, "fully-allocated": 10485760 }

$ qemu-img measure --size 10M --output=json -O qcow2
{ "required": 393216, "fully-allocated": 10813440 }

$ qemu-img measure --size 10M --output=json -O qcow2 -o cluster_size=64k
{ "required": 393216, "fully-allocated": 10813440 }

$ qemu-img measure --size 1G --output=json -O qcow2
{ "required": 393216, "fully-allocated": 1074135040 }
```

The unit tests must pin these exact numbers (and others;
generate ~30 reference points with qemu-img during sub-agent
work — including refcount_bits=1/16, extended_l2=on, varied
cluster_size, and cross combinations for `--size 1M / 64M /
1G / 1T`). Pass these through the sub-agent's brief as a
fixed table — do *not* shell out to qemu-img inside the unit
tests, because the test must be deterministic and runnable
without qemu-img installed.

For vmdk / vhd / vhdx outputs `qemu-img measure` errors with
"does not support size measurement", so the unit tests for
those formats use:

- A few hand-derived reference sizes for `--size`-mode
  fully_allocated (we know the exact formulas).
- A round-trip check (also as a unit test using the formula
  in reverse: assert `measure_<fmt>(s).fully_allocated`
  matches `header_overhead + total_blocks * block_overhead`
  algebraically — for the cases where allocated_bytes ==
  virtual_size, required == fully_allocated by construction).

Round-trip-against-`convert` tests proper live in phase 7
(integration tests) — phase 1 is pure functions.

## Public API

```rust
// src/crates/measure/src/lib.rs

#![no_std]

/// Summary of source-side allocation as seen by a parser.
/// Phase 2 produces this; phase 1 only consumes it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct AllocationSummary {
    pub virtual_size: u64,
    /// Bytes that the source has marked as allocated (whether
    /// or not they contain non-zero data). For raw input this
    /// equals `virtual_size`. For sparse inputs it may be less.
    pub allocated_bytes: u64,
}

/// Result returned for every target format.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct MeasureOutput {
    pub required: u64,
    pub fully_allocated: u64,
}

/// Measurement error. All numeric overflow cases get
/// `Overflow`; an invalid option (e.g. cluster_size not a power
/// of two, refcount_bits not in {1,2,4,8,16,32,64}, vmdk grain
/// size <4KB, vhdx block_size not in [1 MiB, 256 MiB]) gets
/// `InvalidOption`. Out-of-range virtual_size gets
/// `InvalidSize`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MeasureError {
    Overflow,
    InvalidOption,
    InvalidSize,
}

pub type MeasureResult = core::result::Result<MeasureOutput, MeasureError>;

// --- raw ---
pub fn measure_raw(virtual_size: u64) -> MeasureResult;

// --- qcow2 ---
#[derive(Clone, Copy, Debug)]
pub struct Qcow2Opts {
    pub cluster_size: u32,        // default 65536
    pub refcount_bits: u8,        // 1, 2, 4, 8, 16, 32, 64; default 16
    pub extended_l2: bool,
    pub lazy_refcounts: bool,     // affects nothing for size; accept and ignore
    pub compat_v3: bool,          // false ⇒ v2 (no L1 size, no refcount table extension); default true
    pub compress: bool,           // does not affect required (incompressible bound matches qemu-img)
    pub preallocation: Preallocation,
    pub luks_header_overhead: Option<u64>, // crypt_method=2 LUKS-in-qcow2
}
impl Default for Qcow2Opts { /* ... */ }
pub fn measure_qcow2(s: &AllocationSummary, opts: &Qcow2Opts) -> MeasureResult;

// --- vmdk ---
#[derive(Clone, Copy, Debug)]
pub struct VmdkOpts {
    pub subformat: VmdkSubformat,  // MonolithicSparse, StreamOptimized, MonolithicFlat
    pub grain_size: u32,           // bytes; default 65536
}
pub fn measure_vmdk(s: &AllocationSummary, opts: &VmdkOpts) -> MeasureResult;

// --- vhd ---
#[derive(Clone, Copy, Debug)]
pub struct VhdOpts {
    pub subformat: VhdSubformat,   // Dynamic, Fixed
    pub block_size: u32,           // bytes; default 2 MiB; ignored for Fixed
}
pub fn measure_vhd(s: &AllocationSummary, opts: &VhdOpts) -> MeasureResult;

// --- vhdx ---
#[derive(Clone, Copy, Debug)]
pub struct VhdxOpts {
    pub block_size: u32,           // bytes; default 32 MiB; must be 1MiB-256MiB power of two
}
pub fn measure_vhdx(s: &AllocationSummary, opts: &VhdxOpts) -> MeasureResult;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Preallocation {
    Off,           // sparse (default)
    Metadata,      // qcow2: required = required_with_metadata; data still sparse
    Falloc,        // required = fully_allocated
    Full,          // required = fully_allocated
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VmdkSubformat { MonolithicSparse, StreamOptimized, MonolithicFlat }
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum VhdSubformat { Dynamic, Fixed }
```

Notes on the API shape:

- One function per output format, not a single `measure()` with
  an enum. Easier to read, easier to fuzz target individually.
- Options structs are `#[derive(Default)]` where every field has
  a sensible default; the host CLI in phase 4 passes through
  user-supplied values and falls back to defaults otherwise.
- All inputs are `u64`/`u32` — no `usize` in the API so it is
  identical between 32-bit and 64-bit hosts (matters for the
  fuzz harness which is a `std` binary on the build host).
- Errors are an enum (no `&str`), matching the no_std style of
  the existing parser crates.

## Cargo manifest

```toml
# src/crates/measure/Cargo.toml
[package]
name = "measure"
version = "0.2.0"
edition = "2021"
description = "Pre-calculate disk image file size for given format options"
license = "Apache-2.0"
publish = false

[dependencies]
shared = { path = "../../shared" }
# Constants only — no I/O paths from these crates are pulled in.
qcow2 = { path = "../qcow2", default-features = false }
vmdk  = { path = "../vmdk",  default-features = false }
vhd   = { path = "../vhd",   default-features = false }
vhdx  = { path = "../vhdx",  default-features = false }
```

Add `crates/measure` to the workspace `members` list in
`src/Cargo.toml`.

## Implementation notes per format

### raw

```
required = round_up(virtual_size, 1)
fully_allocated = round_up(virtual_size, 1)
```

qemu-img returns exactly `virtual_size`. Match that. No
sector rounding (verify with a 1023-byte size).

### qcow2

The fixed-point loop is the only subtle part. Algorithm:

```
1. l2_entries_per_cluster = cluster_size / l2_entry_size
2. l2_coverage = cluster_size * l2_entries_per_cluster
3. l1_entries = ceil(virtual_size / l2_coverage)
4. l1_size_bytes = l1_entries * 8 (rounded up to a cluster)
5. l2_clusters_for_required =
       count of distinct L1 entries that overlap the
       allocated_bytes range
   l2_clusters_for_full =
       l1_entries (every L2 fully populated)
6. data_clusters_required = ceil(allocated_bytes / cluster_size)
   data_clusters_full     = ceil(virtual_size / cluster_size)
7. used_clusters_required = 1 (header) + l1_clusters
       + l2_clusters_for_required + data_clusters_required
   used_clusters_full = same with the _full variants
8. (reftable_clusters, refblock_clusters, total) =
       calculate_refcount_layout(used_clusters, cluster_size)
   — copy the 10-iteration fixed point from convert.
9. required = total * cluster_size
   fully_allocated = total_full * cluster_size
```

Notes:

- Step 5 for `required` is intentionally the *count of distinct
  L1 entries needed*, not `ceil(allocated_bytes / l2_coverage)`.
  The two are equal when allocated bytes are contiguous from
  offset 0 (which is what `--size` mode and the typical
  `convert`-from-raw case look like) but diverge for fragmented
  allocation. qemu-img assumes the contiguous case in
  `qcow2_measure` — match that. Document the assumption.
- For `compat_v3 = false` (qcow2 v2): no extended L2 (error
  with `InvalidOption`), refcount table format unchanged.
  Skip header extensions.
- For `extended_l2 = true`: `l2_entry_size = 16`, hence
  `l2_entries_per_cluster = cluster_size / 16` and
  `l2_coverage = cluster_size * cluster_size / 16` — half the
  standard coverage. The L1 grows accordingly.
- For `luks_header_overhead = Some(N)`: add `N` to both
  `required` and `fully_allocated`. Cluster 1 onwards is the
  LUKS header in instar's writer; do not try to be clever
  about cluster alignment beyond `round_up(N, cluster_size)`.
- `preallocation`:
  - `Off` (default): as above.
  - `Metadata`: `required = used_clusters_full * cluster_size`
    (every cluster gets a refcount and L2 entry, but data
    holes stay sparse — matches qemu-img).
  - `Falloc` / `Full`: `required = fully_allocated`.
- `compress = true`: no change. qemu-img does not subtract
  anything; sparse output of incompressible data is
  worst-case the same size as uncompressed.
- `lazy_refcounts = true`: no change to size.

Validate options at function entry:

- `cluster_size` is a power of two in `[512, 2*1024*1024]`
  → otherwise `InvalidOption`.
- `refcount_bits` is in `{1, 2, 4, 8, 16, 32, 64}` →
  otherwise `InvalidOption`. (Phase 1 implements only
  `refcount_bits = 16` math correctly; for other widths,
  fix `entries_per_refblock = cluster_size * 8 / refcount_bits`.
  A unit test must pin `refcount_bits = 1` and `64` against
  qemu-img reference values.)
- `virtual_size` ≤ `2^63` (qcow2 hard cap) → otherwise
  `InvalidSize`.
- `allocated_bytes` ≤ `virtual_size` → otherwise
  `InvalidSize`.

All multiplications use `u64::checked_mul` / `checked_add`;
on overflow return `Overflow`. The fixed-point loop has a
hard 16-iteration cap (10 is enough in practice; 16 leaves
slack). If it has not converged, return `Overflow` (it can't
in the real world; this is a safety net for fuzzing).

### vmdk

```
MonolithicSparse:
  header = 512
  descriptor = vmdk::DESC_SECTORS * 512  (= 10 * 512 = 5120)
  capacity_sectors = ceil(virtual_size / 512)
  grain_size_sectors = grain_size / 512
  total_grains = ceil(capacity_sectors / grain_size_sectors)
  gtes_per_gt = vmdk::DEFAULT_NUM_GTES_PER_GT  (512)
  gt_bytes = gtes_per_gt * 4  (= 2048)
  num_gd_entries = ceil(total_grains / gtes_per_gt)
  gd_bytes = num_gd_entries * 4

  allocated_grains_required = ceil(allocated_bytes / grain_size)
  allocated_grains_full = total_grains

  required = header + descriptor
           + round_up(allocated_grains_required * grain_size,
                     output_sector_alignment)
           + num_gd_entries * gt_bytes
           + round_up(gd_bytes, sector)
  fully_allocated = same with allocated_grains_full
```

For phase 1, treat `output_sector_alignment` as 512 (the
on-disk grain alignment). The host-side rounding to a
larger sector size is a writer detail that does not affect
the measure answer.

```
StreamOptimized:
  Same as MonolithicSparse plus:
    + per-allocated-grain marker (12 bytes header + 12B trailer per grain)
    + EOS marker (12 bytes)
    + footer (512 bytes)

MonolithicFlat:
  required = descriptor_size + virtual_size
  fully_allocated = required
  (Two-file layout. The descriptor is small but variable —
   pick the round upper bound: 1 sector.)
```

Validate `grain_size`: power of two, `[4096, 65536]`. Other
values → `InvalidOption`.

### vhd

```
Fixed:
  required = fully_allocated = round_up(virtual_size, 512) + 512  (footer)

Dynamic:
  footer = 512                  (head)
  dynamic_header = 1024
  bat_entries = ceil(virtual_size / block_size)
  bat_bytes = bat_entries * 4
  bat_padded = round_up(bat_bytes, 512)

  sector_bitmap_per_block = round_up(block_size / 512 / 8, 512)
  block_overhead = sector_bitmap_per_block + block_size

  allocated_blocks_required = ceil(allocated_bytes / block_size)
  allocated_blocks_full = bat_entries

  required = footer  (head; instar omits the head footer? — verify)
           + dynamic_header + bat_padded
           + allocated_blocks_required * block_overhead
           + 512  (tail footer)
  fully_allocated = same with allocated_blocks_full
```

Verify against the convert writer whether instar emits a head
footer copy (qemu-img does, dynamic VHDs have a 512-byte
footer mirror at offset 0). Adjust the formula. The
test is: `instar convert -f raw -O vpc 1M.raw out.vhd`,
then `os.path.getsize(out.vhd)` should match
`measure_vhd(allocation_for(in.raw)).required`.

Validate `block_size`: power of two, `[512*1024, 2*1024*1024*1024]`
(qemu-img cap). Other values → `InvalidOption`.

### vhdx

```
Dynamic:
  fixed_overhead = 0x20_0000          (file ID + headers + region tables, 2 MiB)
                 + 0x10_0000          (log region, 1 MiB)
  bat_entries = ceil(virtual_size / block_size)
              + (chunk_ratio - 1) * floor(virtual_size / chunk_block_size)
              [the interleaved sector-bitmap entries — see
               vhdx::calculate_bat_layout]
  bat_bytes = bat_entries * 8
  bat_region = round_up(bat_bytes, 1 MiB)
  metadata_region = 1 MiB

  allocated_blocks_required = ceil(allocated_bytes / block_size)
  allocated_blocks_full = ceil(virtual_size / block_size)

  required = fixed_overhead + bat_region + metadata_region
           + allocated_blocks_required * round_up(block_size, 1 MiB)
  fully_allocated = same with allocated_blocks_full
```

Use `vhdx::calculate_bat_layout()` directly — it already
returns `(total_bat_entries, chunk_ratio,
total_payload_blocks)` for the chosen `(virtual_size,
block_size, logical_sector_size)`. Do not re-derive.

Validate `block_size`: power of two, `[1 MiB, 256 MiB]`. Other
values → `InvalidOption`.

## Unit tests

Every test lives in the same crate (`#[cfg(test)] mod tests`).
The test file is generated from a fixture table:

```rust
struct Q2Case {
    virtual_size: u64,
    allocated_bytes: u64,
    cluster_size: u32,
    refcount_bits: u8,
    extended_l2: bool,
    expected_required: u64,
    expected_full: u64,
}

const QCOW2_CASES: &[Q2Case] = &[
    Q2Case { virtual_size: 10*MB, allocated_bytes: 0,
             cluster_size: 65536, refcount_bits: 16,
             extended_l2: false,
             expected_required: 393216,
             expected_full: 10813440 },
    Q2Case { virtual_size: 1*GB, allocated_bytes: 0,
             cluster_size: 65536, refcount_bits: 16,
             extended_l2: false,
             expected_required: 393216,
             expected_full: 1074135040 },
    // ... ~30 cases sourced from qemu-img during sub-agent work
];
```

Coverage requirements:

- **raw**: 6 cases including 0, 1, 511, 512, `2^63 - 1`,
  overflow path.
- **qcow2**: ≥ 30 cases. Sweep `(--size: 1M, 64M, 1G, 1T)` ×
  `(cluster_size: 512, 4k, 64k, 2M)` × `(allocated: 0,
  1/4 V, V/2, V)` × `(extended_l2: false, true)`. Plus
  edge cases: `refcount_bits = 1` and `64` (3 cases each),
  `compat_v3 = false` (3 cases), `preallocation = Metadata
  / Falloc / Full` (3 cases each).
- **vmdk**: 9 cases (3 subformats × 3 sizes). Sources are
  hand-derived per the formulas above; cross-check phase 1
  by running `instar convert -O vmdk` in phase 7 and
  asserting actual file size ≤ `fully_allocated` and
  `required <= actual <= fully_allocated`. Phase 1 unit
  tests pin the formula numbers; phase 7 confirms
  alignment with the writer.
- **vhd**: 6 cases (Fixed × 1 size, Dynamic × 5 size /
  block_size combos).
- **vhdx**: 4 cases. Larger-scale (1 GiB+) because the 32 MiB
  default block size makes small-image numbers boring.

Plus sanity invariants for all formats, asserted in a
`proptest` style hand-rolled loop:

```rust
for s in &[AllocationSummary { virtual_size: V, allocated_bytes: 0 }, ..] {
    for opts in /* every option combo */ {
        let m = measure_qcow2(s, &opts).unwrap();
        assert!(m.required <= m.fully_allocated);
        assert!(m.fully_allocated >= s.virtual_size); // qcow2 worst case
        assert!(m.required >= header_lower_bound(opts));
    }
}
```

Overflow tests: pass `virtual_size = u64::MAX`, expect
`MeasureError::Overflow` (or `InvalidSize` for qcow2 since
the format caps at 2^63).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | low | sonnet | none | Create `src/crates/measure/` with `Cargo.toml` (deps as specified above), `src/lib.rs` containing only the public API definitions (structs, enums, error type, function signatures returning `unimplemented!()`). Add `crates/measure` to the workspace `members` list in `src/Cargo.toml`. Run `cargo check -p measure` and confirm it builds. Commit. |
| 1b | low | sonnet | none | Implement `measure_raw` plus its 6 unit-test cases (zero, 1, 511, 512, 1 MiB, `u64::MAX` → `Overflow`). Run `cargo test -p measure`. Commit. |
| 1c | high | opus | none | Implement `measure_qcow2` per the spec above, including the `calculate_refcount_layout` 16-iteration fixed point, all option validation (cluster_size power-of-two, refcount_bits set, virtual_size cap), `extended_l2`, `compat_v3`, `preallocation`, `luks_header_overhead`. Add a fixture table of ≥30 `Q2Case` rows with `expected_required` and `expected_full` columns sourced by running `qemu-img measure` locally during the sub-agent's work — paste the qemu-img command output into commit log so the values are auditable. Run `cargo test -p measure`. Commit. **High effort because:** the refcount fixed point and the extended-L2 / compat-v3 interactions are easy to get subtly wrong, and the failure mode is silent numeric drift against qemu-img across the whole baseline matrix in phase 6. |
| 1d | medium | sonnet | none | Implement `measure_vmdk` per the spec above for all three subformats. Reuse `vmdk::DESC_SECTORS` and `vmdk::DEFAULT_NUM_GTES_PER_GT` directly. Add 9 unit-test cases (3 subformats × 3 sizes/grain combos). Validate `grain_size` (power of two, 4 KiB–64 KiB). Run `cargo test -p measure`. Commit. |
| 1e | medium | sonnet | none | Implement `measure_vhd` for both subformats. Reuse `vhd::FOOTER_SIZE`, `vhd::DYNAMIC_HEADER_SIZE`. Verify by reading `convert_to_vhd_dynamic` whether instar emits a head footer copy and adjust the formula accordingly — note the answer in the unit test commentary. 6 unit-test cases. Run `cargo test -p measure`. Commit. |
| 1f | medium | opus | none | Implement `measure_vhdx`, calling `vhdx::calculate_bat_layout()` for the BAT entry count rather than re-deriving it. 4 unit-test cases. Run `cargo test -p measure`. Add the format-wide invariant test (`required <= fully_allocated`, etc.) covering all five formats. Commit. **Medium-high effort because:** the chunk-ratio / interleaved-sector-bitmap math in `vhdx::calculate_bat_layout` is easy to misuse — verify against the actual writer in `convert` for a 1 GiB image. |
| 1g | low | sonnet | none | Run `pre-commit run --all-files`. Ensure `make instar`, `make lint`, `make test-rust` all pass. Update `ARCHITECTURE.md` to mention the new `crates/measure/` (one paragraph alongside the other format crates). Update `CHANGELOG.md` under Unreleased. Commit. |

Total: 7 commits. Each step is independently buildable and
testable.

## Out of scope for phase 1

- `MeasureConfig` struct in `shared` (phase 3).
- `MeasureResultMessage` proto (phase 3).
- New guest binary (phase 3).
- Host CLI subcommand (phase 4).
- `-o` option parser (phase 5).
- Any baseline generation (phase 6).
- Integration tests against real qemu-img versions (phase 7).
- Fuzz harness (phase 8).
- Documentation beyond ARCHITECTURE.md and CHANGELOG (phase 10).
- Refactoring `convert` to call into `crates/measure/`
  (Future work in master plan).

## Success criteria

- `src/crates/measure/` exists, builds, and is in the workspace.
- All five `measure_<fmt>` functions are implemented.
- `cargo test -p measure` passes with ≥55 test cases total
  (≥30 qcow2, 6 raw, 9 vmdk, 6 vhd, 4 vhdx) plus the invariant
  loop.
- `make instar` builds (the new crate compiles into the
  workspace; nothing else uses it yet but adding it to
  `members` should not break unrelated builds).
- `make lint` is clean.
- `pre-commit run --all-files` passes.
- `ARCHITECTURE.md` lists `crates/measure/` alongside the
  other format crates.
- `CHANGELOG.md` has an Unreleased entry noting the new
  crate.

## Risks and mitigations

- **Wrong qcow2 math**: catastrophic — every downstream phase
  inherits the bug, and phase 6 baselines lock the wrong
  number into expected outputs. Mitigation: step 1c is
  high effort + opus + ≥30 fixture rows pinned to live
  `qemu-img measure` output. The fixture table is the
  contract; any later qemu-img version mismatch surfaces in
  phase 7 not as a regression but as a documented quirk.
- **VHD head footer**: a 512-byte off-by-one is invisible
  until phase 7 round-trip tests fail. Mitigation: step 1e's
  brief explicitly tells the sub-agent to check the writer.
- **VHDX chunk_ratio surprises**: small images produce
  `chunk_ratio = 1`, large images > 256 MiB virtual produce
  ratios > 1 and interleaved sector-bitmap BAT entries.
  Mitigation: step 1f calls `vhdx::calculate_bat_layout()`
  directly, which the writer also uses, so the two cannot
  drift apart.
- **`refcount_bits != 16`**: the existing convert writer
  hard-codes `refcount_bits = 16`. The measure crate must
  support all widths because qemu-img does. Mitigation: the
  fixture table in step 1c includes `refcount_bits = 1` and
  `64` cases. If a sub-agent skips them, the test count
  flag in success criteria catches it.
- **No upstream qemu-img on the dev host**: step 1c needs
  qemu-img to source fixture values. The host has it
  (verified during plan authoring); if a sub-agent's
  isolated sandbox does not, fall back to using the
  `instar-testdata/qemu-img-binaries/x86_64/<version>/qemu-img`
  binaries directly.

## Back brief

Before executing any step, the executing agent should
back-brief: which crate, which formulas, which fixture
table entries, and which existing constants are being
reused vs duplicated. The reviewer should confirm that
the brief matches a step in the table above and that no
step has been skipped or merged.
