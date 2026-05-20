# PLAN-create phase 1: per-format metadata emitters

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, disk image formats,
qemu-img semantics), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

This is a phase plan under `PLAN-create.md`. Refer to that master
plan for overall context, mission, and the multi-phase plan
structure.

## Mission

Land a new no_std crate `src/crates/create/` that, given
`(target_format, virtual_size, options, optional backing
reference)`, returns a `MetadataPlan` — a bounded sequence of
`(byte_offset, &[u8])` writes that constitute a valid empty image
in the requested format.

This phase ships **library code only**. No guest binary, no host
subcommand, no call-table changes. Convert is untouched in this
phase; the convert→`crates/create/` refactor is deferred per the
master plan's "opportunistic, not required" note.

## What the survey turned up

`src/operations/convert/src/main.rs` (~4640 lines) and the
parser crates already contain most of what we need:

- **VMDK / VHD / VHDX builders already live in the parser
  crates** as pure functions that return `&[u8]` slices:
  - `vmdk::build_sparse_header()`, `vmdk::build_descriptor()`,
    `vmdk::build_metadata_marker()` — called from convert at
    `src/operations/convert/src/main.rs:3204`, `:3216`,
    `:3500`, `:3528`, `:3586`.
  - `vhd::build_footer()`, `vhd::build_dynamic_header()` —
    called at `src/operations/convert/src/main.rs:3743`,
    `:3766`.
  - `vhdx::build_file_identifier()`, `vhdx::build_header()`,
    `vhdx::build_region_table()`, `vhdx::build_metadata()` —
    called at `src/operations/convert/src/main.rs:4180`,
    `:4197`, `:4220`, `:4242`, `:4312`.

  These three formats need only thin orchestrators in
  `crates/create/` that call the existing builders and stitch
  the resulting bytes into a `MetadataPlan`.

- **QCOW2 writers in convert are I/O-coupled**:
  - `write_qcow2_header()` at lines 1743–1811 (~69 lines)
  - `write_qcow2_metadata()` at lines 1944–2066 (~123 lines)
  - `write_refcount_table()` at lines 2457–2499 (~43 lines)

  Each calls `write_cluster_to_output()` / `write_bytes_to_
  output()` directly. Lifting them requires inverting control:
  the new code returns bytes; the caller writes them. The
  fixed-point refcount-table sizing logic
  (`compute_refcount_table_size` — search for it in convert)
  is the subtlest piece.

- `crates/measure/` (lines 118–159, 414–429, 762–777,
  943–955) provides the per-format option struct shapes
  (`Qcow2Opts`, `VmdkOpts`, `VhdOpts`, `VhdxOpts`) we
  should mirror.

- Convert is entirely `no_std` and uses no `alloc::*` — fixed
  scratch buffers throughout. The new crate inherits this
  constraint: no heap, fixed-size or caller-supplied buffers
  only.

## Public API

```rust
// src/crates/create/src/lib.rs

#![no_std]

use shared::ImageFormat;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CreateError {
    InvalidVirtualSize,
    InvalidClusterSize,
    InvalidBlockSize,
    InvalidGrainSize,
    InvalidSubformat,
    BackingFileTooLong,
    BackingFileUnsupported,    // backing on a format that doesn't support it
    Overflow,
    ScratchTooSmall,           // caller passed too small a scratch buffer
}

#[derive(Debug, Clone, Copy)]
pub struct BackingRef<'a> {
    pub path: &'a [u8],            // bytes the user typed; written verbatim
    pub format: Option<ImageFormat>,
}

// One contiguous write of metadata bytes at a known file offset.
#[derive(Debug, Clone, Copy)]
pub struct MetadataWrite<'a> {
    pub byte_offset: u64,
    pub bytes: &'a [u8],
}

// A complete empty-image metadata layout: an ordered list of
// writes plus the resulting on-disk size of the file *after
// only the metadata has been emitted*. Preallocation modes
// extend the file beyond this in a later phase.
#[derive(Debug, Clone, Copy)]
pub struct MetadataPlan<'a> {
    pub total_metadata_bytes: u64,   // bytes covered by writes
    pub minimum_file_size: u64,      // file size required to hold all metadata
    pub writes: &'a [MetadataWrite<'a>],
}

// Option structs mirror crates/measure/*Opts where they overlap,
// with create-specific additions (preallocation, backing ref).
#[derive(Debug, Clone, Copy)]
pub struct Qcow2CreateOpts<'a> {
    pub virtual_size: u64,
    pub cluster_size: u32,           // 512..=2 MiB, power of two
    pub refcount_bits: u8,           // 1, 2, 4, 8, 16, 32, 64
    pub extended_l2: bool,
    pub lazy_refcounts: bool,
    pub compat_v3: bool,
    pub backing: Option<BackingRef<'a>>,
    // preallocation handled by phase 6; phase 1 supports `off` only.
}

#[derive(Debug, Clone, Copy)]
pub struct VmdkCreateOpts<'a> {
    pub virtual_size: u64,
    pub subformat: VmdkSubformat,    // MonolithicSparse, StreamOptimized for v1
    pub grain_size: u32,             // 4 KiB..=64 KiB, power of two
    pub backing: Option<BackingRef<'a>>,
}

#[derive(Debug, Clone, Copy)]
pub struct VhdCreateOpts<'a> {
    pub virtual_size: u64,
    pub subformat: VhdSubformat,     // Dynamic, Fixed
    pub block_size: u32,             // 512 KiB..=256 MiB, power of two
    pub backing: Option<BackingRef<'a>>,
}

#[derive(Debug, Clone, Copy)]
pub struct VhdxCreateOpts<'a> {
    pub virtual_size: u64,
    pub block_size: u32,             // 1 MiB..=256 MiB, power of two
    pub backing: Option<BackingRef<'a>>,
}

// Per-format planners. Each writes into the caller-supplied
// scratch buffer and returns a plan referencing it. The plan's
// lifetime is tied to `scratch`. If the scratch is too small
// for the format's needs, returns Err(ScratchTooSmall).
//
// Required scratch sizes (worst case, declared as consts so
// callers can size buffers correctly):
//   - qcow2: ~2 MiB worst case (large L1 + refcount tables
//     for very large virtual sizes); see QCOW2_MAX_METADATA_SCRATCH
//   - vmdk monolithicSparse: 16 KiB (header + descriptor + GD)
//   - vhd dynamic: ~256 KiB (footer + dynamic header + BAT)
//   - vhd fixed: 512 (footer only)
//   - vhdx dynamic: ~4 MiB (file id + headers + regions + BAT)
//
// Exact constants live as `pub const` in the module so callers
// (the guest binary in phase 2) can statically allocate scratch.

pub const QCOW2_MAX_METADATA_SCRATCH: usize = /* TBD per design */;
pub const VMDK_MAX_METADATA_SCRATCH: usize  = /* TBD */;
pub const VHD_MAX_METADATA_SCRATCH: usize   = /* TBD */;
pub const VHDX_MAX_METADATA_SCRATCH: usize  = /* TBD */;

pub fn plan_qcow2<'a>(
    opts: &Qcow2CreateOpts<'_>,
    scratch: &'a mut [u8],
) -> Result<MetadataPlan<'a>, CreateError>;

pub fn plan_vmdk<'a>(
    opts: &VmdkCreateOpts<'_>,
    scratch: &'a mut [u8],
) -> Result<MetadataPlan<'a>, CreateError>;

pub fn plan_vhd<'a>(
    opts: &VhdCreateOpts<'_>,
    scratch: &'a mut [u8],
) -> Result<MetadataPlan<'a>, CreateError>;

pub fn plan_vhdx<'a>(
    opts: &VhdxCreateOpts<'_>,
    scratch: &'a mut [u8],
) -> Result<MetadataPlan<'a>, CreateError>;
```

Notes on the shape:

- `MetadataWrite` slices borrow from `scratch`; the
  planner is free to reuse non-overlapping regions of
  scratch for different writes (qcow2's header and refcount
  table never alias in the output, so they don't need
  separate scratch regions). The implementation chooses the
  layout.
- `total_metadata_bytes` is the sum of `writes[*].bytes.len()`.
  `minimum_file_size` is the smallest file size that contains
  every write's byte range — equal to the max of
  `(byte_offset + bytes.len())` across all writes.
- The plan does **not** describe holes. The caller (host or
  guest) is responsible for extending the file (via
  `ftruncate` or by writing zero clusters) when
  preallocation requires it.
- The plan is `Copy`, fixed-size on the stack (the writes
  slice is a borrow). Fits naturally in the guest's stack
  frame.

## Design decisions and rationale

1. **Lift qcow2 builders into the `qcow2` parser crate
   (mirroring vmdk/vhd/vhdx), not into `crates/create/`
   directly.** The other parser crates own both read and
   write sides of their format; qcow2 should too. Then
   `crates/create/` is a uniformly thin orchestrator across
   all four formats. Concretely: add a new
   `src/crates/qcow2/src/create.rs` module gated behind a
   new `create` cargo feature, exposing `build_header()`,
   `build_l1_table()`, `build_refcount_table()`,
   `build_refcount_block()`, `compute_layout()` as pure
   functions. The convert operation continues to use its
   private copies — extraction is by *copy*, not by *move*,
   per the master plan.

2. **Builders return `&[u8]`, not bytes-written counts.**
   Convert's existing qcow2 functions take a buffer and a
   write callback; the new shape takes a buffer and returns
   the populated slice. This matches the vmdk/vhd/vhdx
   pattern (e.g., `vhd::build_footer` returns a `&[u8; 512]`).
   Refcount table and refcount blocks need careful buffer
   slicing because they live at different offsets — the
   builder API is `build_refcount_table(buf, &layout) ->
   &[u8]` with the caller managing per-region sub-slices.

3. **`compute_layout()` is the fixed-point pass.** Convert's
   `compute_refcount_table_size` iterates until the refcount-
   table size and the cluster count it covers reach a fixed
   point. Lift this into a pure `qcow2::create::compute_layout(
   virtual_size, cluster_bits, refcount_bits, extended_l2)
   -> Qcow2Layout` that returns a struct with header offset,
   L1 offset, L1 size, refcount table offset, refcount blocks
   offsets, and total file size. Every other builder is
   parametrised over this `Qcow2Layout`.

4. **No alloc.** Same constraint as every other no_std crate
   in the project. Scratch buffers are caller-supplied.
   Worst-case sizes are exposed as `pub const` so the guest
   binary in phase 2 can statically allocate one buffer
   sized for the largest format it might emit.

5. **Round-trip tests are the validation contract.** Each
   `plan_*` function gets a test that:
   1. Builds the plan.
   2. Materialises it into a contiguous byte buffer (zero-
      padded between writes; size = `minimum_file_size`).
   3. Parses the buffer with the matching parser crate's
      `parse_header()` / equivalent.
   4. Asserts: `virtual_size`, `cluster_size`/`grain_size`/
      `block_size`, `backing_file` (if set), and the
      relevant flag/version fields all round-trip.

   These tests run with `cargo test -p create`. No KVM
   needed.

6. **Preallocation is out of scope for phase 1.** Plans
   describe metadata only. Phase 6 layers preallocation on
   top. The qcow2 planner exposes a `preallocation` field
   on `Qcow2CreateOpts` only when phase 6 wires it in —
   leave it off the struct for phase 1 to keep the surface
   focused.

7. **Backing-file references are accepted at the API level
   but only emitted into the metadata.** This phase does
   not handle reading a backing image; it only takes a
   `BackingRef` and writes the path string + format hint
   into the qcow2 header / vhd parent locator / vhdx
   parent locator. Phase 5 wires up the host-side backing-
   file plumbing and the guest-side header-size lookup.

8. **VMDK subformats**: phase 1 ships `MonolithicSparse`
   and `StreamOptimized` only (single-file subformats).
   The master plan defers multi-file subformats. The
   `VmdkSubformat` enum should still list the deferred
   variants so phase 4's `-o` parser can reject them with
   a clear "not yet supported" error rather than an
   "unknown subformat" one.

9. **VHDX subformats**: phase 1 ships `Dynamic` only.
   Fixed VHDX is not produced by qemu-img and convert
   doesn't emit it; defer.

## Open questions

These should be answered during execution; if a sub-agent hits
them, escalate to the management session rather than guessing.

1. **Where does `LuksHeaderData` live in the new shape?**
   Convert's `write_qcow2_header` accepts `luks_header_data`
   to embed the LUKS v2 header in cluster 0+. Phase 1 ships
   unencrypted only (per the master plan's "encryption
   deferred" call), so the cleanest answer is: `Qcow2CreateOpts`
   has no LUKS field in phase 1. The qcow2 `build_header`
   helper in the parser crate *should* accept an optional LUKS
   blob parameter so future encrypted-create work can use it
   without redesign, but `plan_qcow2` passes `None`.

2. **Should the `qcow2::create` module be feature-gated?**
   The convert operation is the only existing caller and
   wouldn't import it; the new `create` crate would import it
   via `qcow2 = { path = "../qcow2", features = ["create"] }`.
   Feature-gating keeps the qcow2 crate's compile units lean
   for callers that only parse. Recommend: yes, gate it.

3. **Scratch buffer sizing precision.** The "TBD" constants
   above need exact upper bounds derived from format limits
   (max virtual size per format, max L1 entries, etc.). Pick
   conservative powers-of-two that fit the guest's static
   memory budget (the guest has ~64 KiB of scratch by default;
   see `src/operations/measure/src/main.rs` for the existing
   pattern). qcow2's worst case (huge L1) may exceed 64 KiB —
   if so, document the practical max virtual size and reject
   larger requests in phase 4's option parser.

4. **Should `BackingRef.path` length be bounded in the API?**
   `CreateConfig.backing_file` is fixed at 1024 bytes (per the
   master plan's struct sketch). The crate should accept
   anything up to a documented limit (matching the call-
   table struct) and return `BackingFileTooLong` past that.
   Recommend: `pub const MAX_BACKING_FILE_LEN: usize = 1024;`
   exposed from `crates/create/`, the call-table struct
   imports from the same constant.

## Execution

Phase 1 splits into seven sub-steps. Each step is a separate
sub-agent invocation. Land one commit per step. Steps 1b/1c
are coupled (qcow2 lift + orchestrator); steps 1d/1e/1f are
independent and can run in parallel if desired.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Scaffold `src/crates/create/` as a new no_std workspace crate. Mirror `src/crates/measure/Cargo.toml` structure exactly. The `Cargo.toml` declares `edition = "2021"`, `[lib]` with `crate-type = ["rlib"]`, and dependencies `shared = { path = "../../shared" }` only for this step. Add the crate to the workspace `members` in `src/Cargo.toml`. Create `src/crates/create/src/lib.rs` with `#![no_std]`, the `CreateError` enum, `BackingRef`, `MetadataWrite`, `MetadataPlan` types, and the four `*CreateOpts` structs exactly as specified in the "Public API" section above. The four `plan_*` functions exist as stubs that return `Err(CreateError::ScratchTooSmall)`. Add a `MAX_BACKING_FILE_LEN` const of 1024. No implementation logic in this step — just types and stubs. Run `cargo build -p create` and `cargo test -p create` (the latter should pass with zero tests). Confirm `make lint` is clean. |
| 1b | high   | opus   | worktree | Lift qcow2 metadata writers from `src/operations/convert/src/main.rs` into a new `src/crates/qcow2/src/create.rs` module behind a new `create` cargo feature in `src/crates/qcow2/Cargo.toml`. Source functions to lift (read them at the cited lines first): `write_qcow2_header` (lines 1743-1811), `write_qcow2_metadata` (lines 1944-2066), `write_refcount_table` (lines 2457-2499), and any `compute_refcount_table_size` helper they call. **Do not modify convert's copies — leave them intact and working.** The lifted functions become pure builders: each takes a `&mut [u8]` buffer and a layout struct, populates the buffer, returns `&[u8]`. Define a new `pub struct Qcow2Layout` with header_offset (always 0), cluster_bits, l1_offset, l1_size_bytes, refcount_table_offset, refcount_block_offsets (a fixed-size array or a small inline-vec pattern), and total_file_size. Add `pub fn compute_layout(virtual_size: u64, cluster_bits: u32, refcount_bits: u8, extended_l2: bool) -> Result<Qcow2Layout, Qcow2CreateError>` implementing the fixed-point sizing that convert's `compute_refcount_table_size` (or equivalent) does — read that logic carefully. New module is `no_std`, no `alloc`. Feature-gate the module: `#[cfg(feature = "create")] pub mod create;` in `src/crates/qcow2/src/lib.rs`. Add unit tests in `src/crates/qcow2/src/create.rs` asserting that `compute_layout` matches the layout convert's existing code produces for at least three sizes (1 MiB, 1 GiB, 1 TiB) with default cluster_size and refcount_bits. Run `cargo test -p qcow2 --features create` and `make lint`. Do **not** change convert. Risky: worktree isolation. |
| 1c | high   | opus   | worktree | Implement `plan_qcow2()` in `src/crates/create/src/lib.rs` by orchestrating the `qcow2::create::*` helpers landed in step 1b. Add `qcow2 = { path = "../qcow2", features = ["create"], default-features = false }` to `src/crates/create/Cargo.toml`. The implementation: validate options (cluster_size power-of-two in 512..=2 MiB; refcount_bits in {1,2,4,8,16,32,64}); call `qcow2::create::compute_layout`; allocate sub-slices of the scratch buffer for header (1 cluster), L1 table (`l1_size_bytes`), refcount table (1 cluster), refcount blocks; call each `qcow2::create::build_*` to populate them; assemble `MetadataWrite` entries at the correct file offsets per the layout; return `MetadataPlan`. Backing file: if `opts.backing` is Some, pass its path through to `build_header` (which writes the path into the backing_file region of the header). Round-trip tests: for `(1 MiB, default opts)`, `(1 GiB, cluster_size=512)`, `(1 GiB, extended_l2=true)`, `(1 TiB, refcount_bits=1)`, `(1 GiB, backing=Some(...))`: build the plan, materialise into a contiguous `Vec<u8>` in tests (tests are std, so `Vec` is fine in `#[cfg(test)]`), parse with `qcow2::parse_header` (or whatever the parser's entry point is — read `src/crates/qcow2/src/lib.rs` to confirm), assert virtual_size/cluster_size/extended_l2/backing_file all round-trip. Risky: worktree isolation. |
| 1d | medium | sonnet | none | Implement `plan_vmdk()` in `src/crates/create/src/lib.rs` for the `MonolithicSparse` and `StreamOptimized` subformats only. Use the existing `vmdk::build_sparse_header()` and `vmdk::build_descriptor()` helpers — read `src/operations/convert/src/main.rs:2898-2957` (`init_vmdk_output_layout`) and `:3204-3240` to see how convert calls them. Add `vmdk = { path = "../vmdk", default-features = false }` to `src/crates/create/Cargo.toml`. Validate options (grain_size 4 KiB..=64 KiB, power of two; subformat is one of the supported variants — reject deferred ones with `CreateError::InvalidSubformat`). Compute layout: header at sector 0, descriptor embedded after header per the descriptor offset/size convention vmdk uses, grain directory after that. Backing-file support for VMDK is via the descriptor's `parentCID` and `parentFileNameHint` fields — if `opts.backing` is Some, populate them through `vmdk::build_descriptor`. Round-trip tests: for `(1 MiB, MonolithicSparse, default grain)`, `(1 GiB, StreamOptimized, grain_size=4096)`, `(1 GiB, MonolithicSparse, backing=Some(...))`: materialise the plan and parse via `vmdk::parse_*`, assert virtual_size and grain_size round-trip. |
| 1e | medium | sonnet | none | Implement `plan_vhd()` in `src/crates/create/src/lib.rs` for both `Dynamic` and `Fixed` subformats. Use `vhd::build_footer()` and `vhd::build_dynamic_header()` — read `src/operations/convert/src/main.rs:3738-3820` for the dynamic path and the matching fixed-path block. Add `vhd = { path = "../vhd", default-features = false }` to `src/crates/create/Cargo.toml`. Dynamic layout: footer at offset 0 (head copy), dynamic header at offset 512, BAT at offset 1536 sized `ceil(virtual_size / block_size) * 4` rounded to 512, footer tail copy at end of file (offset = total_file_size - 512). Fixed layout: just the footer at `virtual_size` (the data region is left to preallocation in phase 6; phase 1 returns a plan with `minimum_file_size = virtual_size + 512` and the footer as the sole write). BAT entries are all `0xFFFFFFFF`. Round-trip tests: `(1 MiB, Dynamic, default block_size)`, `(1 GiB, Dynamic, block_size=2MiB)`, `(1 GiB, Fixed)`, materialise and parse via `vhd::parse_*`. |
| 1f | medium | sonnet | none | Implement `plan_vhdx()` in `src/crates/create/src/lib.rs` for `Dynamic` subformat only. Use `vhdx::build_file_identifier()`, `vhdx::build_header()`, `vhdx::build_region_table()`, `vhdx::build_metadata()` — read `src/operations/convert/src/main.rs:4175-4330` for how convert assembles them. Add `vhdx = { path = "../vhdx", default-features = false }` to `src/crates/create/Cargo.toml`. VHDX layout is rigid: file identifier at 0 (64 KiB region), header 1 at 64 KiB, header 2 at 128 KiB, region table 1 at 192 KiB, region table 2 at 256 KiB, log region at 1 MiB, metadata region at 2 MiB (referenced by region table), BAT region after metadata (1 MiB-aligned). BAT entries default to `PAYLOAD_BLOCK_NOT_PRESENT` (0). Header sequence numbers: header 1 has seq=1, header 2 has seq=0 (so header 1 is the active one). Round-trip tests: `(1 MiB, Dynamic, default block)`, `(1 GiB, Dynamic, block_size=32MiB)`, materialise and parse via `vhdx::parse_*`, assert virtual_size and block_size round-trip. |
| 1g | medium | opus   | none | Add a `tests/round_trip.rs` integration test under `src/crates/create/` that exercises every `plan_*` function against the full safe-tier option matrix (collect a small fixed list: ~5 sizes × ~3 option combinations per format = ~60 test cases). For each: build plan, materialise to bytes, parse with the matching parser crate, assert virtual_size and the format-specific layout parameters round-trip. Also assert the structural invariants: `total_metadata_bytes == sum(writes[*].bytes.len())`, `minimum_file_size == max(write.byte_offset + write.bytes.len())`, no two writes overlap. Set up a small helper in `tests/common.rs` for the materialise-and-parse pattern so individual test cases stay terse. Confirm `cargo test -p create --tests` passes and `make lint` clean. |

## Out of scope for phase 1

Reminders so a sub-agent doesn't drift:

- Convert is **not** modified. Its private writer copies stay
  in `src/operations/convert/src/main.rs` exactly as they are
  today. Convert is later refactored to call into the new
  `qcow2::create::*` builders (Future work in the master plan).
- No guest binary. No `src/operations/create/` directory in
  this phase.
- No host CLI. No changes to `src/vmm/src/main.rs`.
- No call-table changes. No `CreateConfig`. No protobuf
  changes.
- No preallocation. No reading of backing-file headers
  (just accepting and embedding the path).
- No encryption / LUKS. Even if convert's qcow2 writers can
  emit LUKS headers, phase 1's `Qcow2CreateOpts` omits the
  field entirely; the lifted `qcow2::create::build_header`
  should accept an optional LUKS blob for forward
  compatibility but always be passed `None`.

## Success criteria for phase 1

- `cargo build -p create` clean.
- `cargo test -p create` passes with the round-trip tests
  from steps 1c-1g (qcow2 + vmdk + vhd + vhdx).
- `cargo test -p qcow2 --features create` passes the
  `compute_layout` unit tests from step 1b.
- `make instar` builds (no regression from changes to
  `src/Cargo.toml` workspace members or `src/crates/qcow2/
  Cargo.toml` feature flags).
- `make lint` clean across the workspace.
- `pre-commit run --all-files` clean.
- `src/operations/convert/src/main.rs` has zero modifications
  in any commit produced by this phase. Verify with
  `git log --stat phase-1-base..HEAD -- src/operations/`
  showing no convert hits.
- New code is no_std, no `alloc`. Verify with
  `grep -r 'extern crate alloc\|use alloc' src/crates/create/`
  returning no hits.

## Bugs fixed during this work

(To be filled in.)

## Back brief

Before executing each step of this phase, please back brief
the operator as to your understanding of the step and how the
work you intend to do aligns with the brief. In particular,
flag if the brief refers to file/line locations that don't
match what you find when you read them (the survey was a
snapshot; the codebase may have moved).
