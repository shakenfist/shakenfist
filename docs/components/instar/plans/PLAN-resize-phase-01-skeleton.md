# PLAN-resize phase 1: planner skeleton, raw, and shared types

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, disk image formats,
`qemu-img` semantics), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that master
plan for overall context, mission, and the multi-phase plan
structure.

## Mission

Land a new `no_std` crate `src/crates/resize/` whose type surface
is the contract that phases 2–6 will fill in. Also land the
`ResizeConfig` and `ResizeResult` structs in `src/shared/src/lib.rs`
so the guest binary in phase 7 has a stable ABI to target. Land
the raw-format planner end-to-end, because it is small enough to
finish in this phase and because shipping at least one working
planner validates the type surface against a real case before
phases 2–6 commit to it.

This phase ships **library code only**:

- No guest binary (phase 7).
- No host CLI (phase 8).
- No call-table changes (phase 7 adds `read_output_sector`).
- No protobuf changes (phase 7).
- No format-specific planner logic for qcow2 / vmdk / vhd / vhdx
  (phases 2–6).
- No preallocation handling (phase 9).
- No integration tests (phase 11).

## What the survey turned up

`src/crates/create/` (1592 lines in `src/lib.rs`) is the closest
shape match. Reuse decisions:

- **`MetadataPlan` is not directly reusable.** Resize emits
  *patches* against an existing file (in-place writes, appends,
  zero-fills), not a contiguous metadata layout. The shape is
  similar enough that the design mirrors it (fixed-size inline
  storage, `Copy`, `push()` for assembly), but the patch enum
  differs from `MetadataWrite`.
- **`crates/create/`'s sizing helpers are reusable.** Phases 2–6
  call into `qcow2::create::compute_layout`,
  `vhdx::calculate_bat_layout`, etc., for the "what would the
  L1/BAT/refcount table look like at the new virtual size"
  question. Phase 1 does not depend on these — they are pulled
  in by the format-specific planners later.
- **`shared::CreateConfig` / `shared::CreateResult`** layout
  (`src/shared/src/lib.rs:2167-2316`) is the template for
  `ResizeConfig` / `ResizeResult`. Same `#[repr(C)] derive(Clone,
  Copy)` shape, magic constant, flags word with preallocation
  bits at 4–5 (mirroring create + measure), `_reserved` padding
  for forward compat.

## Public API

```rust
// src/crates/resize/src/lib.rs

#![no_std]

use shared::ImageFormat;

/// Errors returned by the `plan_resize_*` family of functions.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResizeError {
    /// New virtual size is zero, misaligned, or exceeds the
    /// format's maximum addressable range.
    InvalidNewVirtualSize,
    /// Caller asked to shrink but did not pass the equivalent
    /// of qemu-img's `--shrink`.
    ShrinkWithoutFlag,
    /// Caller asked to shrink past data that is still allocated
    /// in the image.
    ShrinkBelowAllocated,
    /// The source format is not supported by this version of
    /// instar resize (QED, LUKS, encrypted qcow2, etc).
    UnsupportedFormat,
    /// The format is supported but the subformat isn't (multi-
    /// file VMDK, fixed VHDX, ...).
    UnsupportedSubformat,
    /// The format is supported but shrinking it isn't (vmdk /
    /// vhd / vhdx in v1). qemu-img-compatible: the corresponding
    /// qemu-img invocations fail too.
    UnsupportedShrink,
    /// An internal size computation overflowed.
    Overflow,
    /// The caller-supplied scratch buffer is too small.
    ScratchTooSmall,
    /// The requested preallocation mode isn't supported for the
    /// target format/subformat.
    PreallocationUnsupported,
}

/// What the resize will do to the file. Carried in [`ResizePlan`]
/// so the host can render the right success line.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResizeAction {
    /// New virtual size > current virtual size.
    Grow,
    /// New virtual size < current virtual size.
    Shrink,
    /// New == current. Plan is empty; the host should print
    /// "Image resized." and exit zero, matching qemu-img.
    NoOp,
}

/// A single byte-level operation against the file.
///
/// Patches are emitted by a planner and applied in order by the
/// guest. Ordering is load-bearing for crash safety on some
/// formats (qcow2's "refcount-then-data-then-header" sequence;
/// vhdx's two-header sequence-number dance). Planners must not
/// reorder patches; the guest must not reorder patches.
#[derive(Debug, Clone, Copy)]
pub enum ResizePatch<'a> {
    /// Overwrite an existing byte range. Used for header
    /// rewrites, footer copy updates, L1/BAT entry updates,
    /// refcount decrements.
    Write { byte_offset: u64, bytes: &'a [u8] },
    /// Extend the file by writing new bytes starting at
    /// `byte_offset`. `byte_offset` must equal the file size
    /// at the moment the patch is applied — the guest cannot
    /// "skip" forward and leave a hole, because some hosts
    /// don't punch holes the way we'd want.
    Append { byte_offset: u64, bytes: &'a [u8] },
    /// Zero `len` bytes at `byte_offset`. Equivalent to
    /// `Write` with a zero buffer of size `len`, but lets the
    /// planner declare large zero regions without paying for
    /// the staging buffer. The guest implementation writes
    /// `len / sector_size` zero sectors via
    /// `write_output_sector`.
    ZeroFill { byte_offset: u64, len: u64 },
}

impl<'a> ResizePatch<'a> {
    /// Byte offset where this patch starts.
    pub fn byte_offset(&self) -> u64 { /* ... */ }
    /// Length of the range covered by this patch.
    pub fn len(&self) -> u64 { /* ... */ }
    /// Empty placeholder used as the default array element when
    /// building a [`ResizePlan`].
    pub const EMPTY: ResizePatch<'static> = ResizePatch::Write {
        byte_offset: 0, bytes: &[],
    };
}

/// Maximum number of patch entries a [`ResizePlan`] can hold.
///
/// QCOW2 grow at small cluster sizes with a refcount table
/// extension is the dominant case: header rewrite + L1 append +
/// up to ~32 refcount-block appends + the old-L1-decrement
/// patches. 128 is conservative; tighten in phase 2 if profiling
/// shows the worst case never approaches it.
pub const MAX_RESIZE_PATCHES: usize = 128;

/// A complete in-place mutation plan.
#[derive(Debug, Clone, Copy)]
pub struct ResizePlan<'a> {
    /// File size the host should `ftruncate` to after applying
    /// every patch. For `Shrink`, this may be smaller than the
    /// pre-resize file size; for `Grow`, larger; for `NoOp`,
    /// equal.
    pub total_file_size: u64,
    /// What this plan does at a high level.
    pub action: ResizeAction,
    /// Number of populated entries in `patches_storage`.
    patch_count: u16,
    /// Inline storage; only `..patch_count` is valid.
    patches_storage: [ResizePatch<'a>; MAX_RESIZE_PATCHES],
}

impl<'a> ResizePlan<'a> {
    pub const fn new(action: ResizeAction, total_file_size: u64) -> Self;
    pub fn patches(&self) -> &[ResizePatch<'a>];
    pub fn push(&mut self, patch: ResizePatch<'a>) -> Result<(), ResizeError>;
}

/// Per-format option structs. Only the fields a planner cares
/// about; the host translates from the CLI surface (`-o
/// key=value` plus `--shrink`, `--preallocation`) into one of
/// these.

#[derive(Debug, Clone, Copy)]
pub struct RawResizeOpts {
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub preallocation: Preallocation,
}

#[derive(Debug, Clone, Copy)]
pub struct Qcow2ResizeOpts<'a> {
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    /// Cluster size in bytes (must match the existing image; the
    /// guest cross-checks against the header).
    pub cluster_size: u32,
    pub refcount_bits: u8,
    pub extended_l2: bool,
    pub preallocation: Preallocation,
    /// True iff the host CLI passed `--shrink`.
    pub allow_shrink: bool,
    /// Read-only view of the existing image's L1 table.
    /// Phase 1 leaves this `&[]` (raw has no L1); phase 2 wires
    /// it in. Holding the lifetime here keeps the API stable.
    pub existing_l1_bytes: &'a [u8],
}

#[derive(Debug, Clone, Copy)]
pub struct VmdkResizeOpts {
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub grain_size: u32,
    pub subformat: VmdkSubformat,
    pub allow_shrink: bool,
    pub preallocation: Preallocation,
}

#[derive(Debug, Clone, Copy)]
pub struct VhdResizeOpts {
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub block_size: u32,
    pub subformat: VhdSubformat,
    pub allow_shrink: bool,
    pub preallocation: Preallocation,
}

#[derive(Debug, Clone, Copy)]
pub struct VhdxResizeOpts {
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub block_size: u32,
    pub preallocation: Preallocation,
    // No allow_shrink — vhdx shrink is rejected unconditionally.
}

/// Re-exported from `create` for symmetry, or defined here to
/// avoid pulling in the create crate just for an enum.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Preallocation { Off, Metadata, Falloc, Full }

/// Subformat enums mirror `crates/create/`. Match the variants
/// exactly so the host CLI can translate once.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VmdkSubformat {
    MonolithicSparse, StreamOptimized,
    MonolithicFlat, TwoGbMaxExtentSparse, TwoGbMaxExtentFlat,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VhdSubformat { Dynamic, Fixed }

// Per-format planners. Phase 1 lands every signature but only
// implements `plan_resize_raw`; the rest stub to
// `Err(ResizeError::UnsupportedFormat)`.
//
// Each planner takes a caller-supplied scratch buffer (mirrors
// create) for staging append/write payload bytes. Raw needs no
// scratch.

pub fn plan_resize_raw(opts: &RawResizeOpts) -> Result<ResizePlan<'static>, ResizeError>;

pub fn plan_resize_qcow2<'a>(
    opts: &Qcow2ResizeOpts<'_>,
    scratch: &'a mut [u8],
) -> Result<ResizePlan<'a>, ResizeError>;

pub fn plan_resize_vmdk<'a>(
    opts: &VmdkResizeOpts,
    scratch: &'a mut [u8],
) -> Result<ResizePlan<'a>, ResizeError>;

pub fn plan_resize_vhd<'a>(
    opts: &VhdResizeOpts,
    scratch: &'a mut [u8],
) -> Result<ResizePlan<'a>, ResizeError>;

pub fn plan_resize_vhdx<'a>(
    opts: &VhdxResizeOpts,
    scratch: &'a mut [u8],
) -> Result<ResizePlan<'a>, ResizeError>;

// Worst-case scratch buffer sizes per format. TBD-marked entries
// are conservative placeholders for phase 1; phases 2–6 tighten.

pub const QCOW2_MAX_RESIZE_SCRATCH: usize = 32 * 1024 * 1024;  // TODO(phase-2): tighten
pub const VMDK_MAX_RESIZE_SCRATCH:  usize =  4 * 1024 * 1024;  // TODO(phase-6): tighten
pub const VHD_MAX_RESIZE_SCRATCH:   usize =  4 * 1024 * 1024;  // TODO(phase-4): tighten
pub const VHDX_MAX_RESIZE_SCRATCH:  usize =  8 * 1024 * 1024;  // TODO(phase-5): tighten
```

Notes on the shape:

- The patch enum carries `&[u8]` slices that borrow from
  `scratch`. The planner is free to interleave them in the
  scratch buffer; the guest applies them in order, so the
  planner can reuse a single header-staging slice across
  multiple `Write` patches if the layout permits (it usually
  doesn't — each patch's bytes live in disjoint slices).
- `Append { byte_offset, bytes }` requires the guest to have
  already grown the file to `byte_offset + bytes.len()` via a
  prior patch (or the host's pre-resize `set_len`). Phase 7
  details the guest-side application order.
- `ZeroFill { byte_offset, len }` does not consume scratch.
  Used by phase 9 (`--preallocation=full`) and by phase 2 (new
  L1 region's tail past the copied old L1 contents).
- `ResizePlan` is `Copy`. The patches array inflates the struct
  size (128 entries × 24 bytes per `ResizePatch` ≈ 3 KiB).
  Allocate `ResizePlan` only at the guest's top-level stack
  frame; do not pass by value through deep call stacks. The
  guest is `no_std`/static-stack so this matters.

### `ResizeConfig` and `ResizeResult` in `shared/`

Mirror `CreateConfig` / `CreateResult` at
`src/shared/src/lib.rs:2160-2316`. Concretely:

```rust
// Following CreateConfig at line 2167 +/-
#[repr(C)]
#[derive(Clone, Copy)]
pub struct ResizeConfig {
    /// Magic (`0x52455349` = "RESI" little-endian).
    pub magic: u32,
    /// Source/target format (`ImageFormat as u32`).
    pub target_format: u32,
    /// Flags. See `FLAG_*` constants and `PREALLOC_*`.
    pub flags: u32,
    /// Sector size for I/O.
    pub sector_size: u32,
    /// Current virtual size in bytes (host populates from the
    /// existing header before launching the guest; the guest
    /// cross-checks against its own parse and errors out on
    /// mismatch).
    pub current_virtual_size: u64,
    /// Requested new virtual size in bytes.
    pub new_virtual_size: u64,
    /// Per-format options, parallel to CreateConfig.
    pub qcow2_cluster_size: u32,
    pub qcow2_refcount_bits: u8,
    pub vmdk_subformat: u8,
    pub vhd_subformat: u8,
    pub _pad: u8,
    pub vmdk_grain_size: u32,
    pub block_size: u32,
    /// Reserved padding for forward compat.
    pub _reserved: [u8; 64],
}

impl ResizeConfig {
    pub const MAGIC: u32 = 0x52455349; // "RESI"

    /// Flag: `--shrink` was passed.
    pub const FLAG_SHRINK: u32 = 1 << 0;
    /// Flag: extended-L2 entries in the existing qcow2 image.
    pub const FLAG_EXTENDED_L2: u32 = 1 << 1;
    /// Flag: quiet mode (host-side only; guest ignores).
    pub const FLAG_QUIET: u32 = 1 << 2;

    /// Preallocation mode encoded in flags bits 4-5, exactly
    /// mirroring CreateConfig and MeasureConfig.
    pub const PREALLOC_MASK: u32 = 0b11 << 4;
    pub const PREALLOC_OFF: u32 = 0 << 4;
    pub const PREALLOC_METADATA: u32 = 1 << 4;
    pub const PREALLOC_FALLOC: u32 = 2 << 4;
    pub const PREALLOC_FULL: u32 = 3 << 4;

    pub fn is_valid(&self) -> bool { self.magic == Self::MAGIC }
    pub fn preallocation(&self) -> u32 { self.flags & Self::PREALLOC_MASK }
    pub fn allow_shrink(&self) -> bool { self.flags & Self::FLAG_SHRINK != 0 }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct ResizeResult {
    /// Magic (`0x52524553` = "RRES").
    pub magic: u32,
    pub target_format: u32,
    /// Resolved new virtual size in bytes (after relative-size
    /// resolution by the host).
    pub resolved_new_virtual_size: u64,
    /// File size before the host's post-pass `set_len`.
    pub file_size_before: u64,
    /// File size after the guest's last patch.
    pub file_size_after: u64,
    /// Action taken: 0 = NoOp, 1 = Grow, 2 = Shrink.
    pub action: u32,
    /// Error code. See `ERROR_*`.
    pub error: u32,
}

impl ResizeResult {
    pub const MAGIC: u32 = 0x52524553; // "RRES"
    pub const ACTION_NOOP: u32 = 0;
    pub const ACTION_GROW: u32 = 1;
    pub const ACTION_SHRINK: u32 = 2;

    pub const ERROR_OK: u32 = 0;
    pub const ERROR_INVALID_OPTION: u32 = 1;
    pub const ERROR_INVALID_NEW_SIZE: u32 = 2;
    pub const ERROR_SHRINK_WITHOUT_FLAG: u32 = 3;
    pub const ERROR_SHRINK_BELOW_ALLOCATED: u32 = 4;
    pub const ERROR_UNSUPPORTED_FORMAT: u32 = 5;
    pub const ERROR_UNSUPPORTED_SUBFORMAT: u32 = 6;
    pub const ERROR_UNSUPPORTED_SHRINK: u32 = 7;
    pub const ERROR_PREALLOCATION_UNSUPPORTED: u32 = 8;
    pub const ERROR_SCRATCH_TOO_SMALL: u32 = 9;
    pub const ERROR_READ_FAILED: u32 = 10;
    pub const ERROR_WRITE_FAILED: u32 = 11;
    pub const ERROR_PARSE_FAILED: u32 = 12;
    pub const ERROR_HEADER_MISMATCH: u32 = 13;

    pub fn is_valid(&self) -> bool { self.magic == Self::MAGIC }
}
```

Error codes are stable: only appended in later phases, never
reordered. Same discipline as `CreateResult::ERROR_*`.

## Design decisions and rationale

1. **Patch-typed enum, not a flat `MetadataWrite` list.** Resize
   needs to distinguish in-place rewrites from appends from
   zero-fills because the guest's application order, the
   crash-safety contract, and (eventually) the preallocation
   integration all key off the patch type. A flat
   `(offset, bytes)` list works for create's emit-into-empty
   model but obscures these distinctions for resize. Pay the
   extra ergonomics cost now to avoid a refactor in phase 9.

2. **`current_virtual_size` is on every per-format opts struct.**
   Resize is the first operation where the planner must
   *compare* old and new sizes to pick Grow vs Shrink. Putting
   `current_virtual_size` next to `new_virtual_size` in the
   opts keeps each planner self-contained without reaching for
   thread-local state. The guest populates `current_virtual_size`
   from the parsed header before calling the planner — matches
   the `ResizeConfig.current_virtual_size` field the host
   pre-populates as a cross-check.

3. **Raw lands end-to-end in phase 1, the others stub.** Raw
   is two lines of logic (`if new > old { Grow } else if new <
   old { Shrink } else { NoOp }`); it would be artificial to
   spread it across phases. Shipping it in phase 1 also gives
   us a non-trivial test case for the type surface before
   phases 2–6 commit to it. Phases 2–6 each upgrade one
   `UnsupportedFormat` stub to a real implementation.

4. **Subformat enums are not feature-gated.** Even if
   `MonolithicFlat`'s planner is `UnsupportedSubformat` for the
   life of v1, the variant exists in the enum so the host CLI
   in phase 8 can map qemu-img's subformat string to a variant
   and emit a structured error rather than treating it as
   unknown. Mirrors `crates/create/`'s `VmdkSubformat`
   approach.

5. **`Preallocation` lives in `crates/resize/`, not re-exported
   from `crates/create/`.** Mechanically equivalent enums; keeping
   them separate means resize doesn't pull in the create crate
   transitively. Phase 9's host-side preallocation code will
   handle the translation if it needs to call into shared
   helpers from `crates/create/`.

6. **`ResizeConfig` and `ResizeResult` are sized to fit the
   existing `OPERATION_CONFIG_ADDR` budget.** `CreateConfig` is
   ~1300 bytes (1024-byte backing path dominates). `ResizeConfig`
   is much smaller (~120 bytes) because it has no backing-file
   field. There is no risk of overrunning the operation-config
   region, but step 1b should assert the struct's `size_of` in
   a compile-time check or a unit test for forward-compat.

7. **No `existing_header_bytes` field in opts.** Phases 2–6 will
   need to inspect more than just the header (L1 contents for
   qcow2 shrink, BAT contents for vhdx grow when the BAT-tail
   collides with allocations). Each format's opts struct grows
   its own borrowed-slice field in the phase that adds it
   (`existing_l1_bytes` in `Qcow2ResizeOpts` is the first such
   field). Phase 1 declares the field for qcow2 and leaves it
   empty; the other formats add their fields in their own
   phases.

## Open questions

These should be answered during execution; if a sub-agent hits
them, escalate to the management session rather than guessing.

1. **`MAX_RESIZE_PATCHES = 128` — is 128 the right number?**
   Pulled from a worst-case estimate for qcow2 grow at small
   cluster sizes. Verify in phase 2 by counting patches
   emitted at `(cluster_size=512, refcount_bits=1,
   virtual_size=1 TiB)` and adjusting if needed. Phase 1
   commits to 128 and phase 2 either confirms or raises.

2. **`ResizePatch` size on `Copy`-ness.** The enum is ~24 bytes
   (largest variant: `Append { byte_offset: u64, bytes: &[u8] }`
   = 8 + 16 = 24 bytes). Times 128 entries = 3 KiB per
   `ResizePlan`. Confirm this fits the guest's stack budget by
   reviewing `src/operations/create/src/main.rs`'s
   `_start()` stack frame — that operation already allocates
   the larger `MetadataPlan` (96 × 24 = 2.3 KiB) plus the
   scratch buffer, so 3 KiB is within precedent.

3. **`existing_l1_bytes` lifetime: borrowed from where?** In
   phase 2's guest binary, the L1 table is read into a slice of
   the scratch buffer before the planner is called. The opts
   struct borrows from there. Phase 1 declares the field as
   `&'a [u8]` and the planner stub does not touch it. Confirm
   in phase 2 that the lifetime hands out cleanly without
   needing a separate `Read<L1>` helper type.

4. **Should `ResizeAction::NoOp` be its own variant or fold
   into `Grow` with `total_file_size == current_file_size`?**
   qemu-img emits `Image resized.` even when new == current.
   Keep `NoOp` explicit so the host renderer can distinguish
   "we wrote zero bytes" from "we grew by zero bytes" if
   future telemetry wants it. Cost is one enum variant; benefit
   is clarity. Recommend keep.

5. **Patch overlap checking.** `ResizePlan::push()` should
   probably reject patches whose byte ranges overlap an earlier
   patch. Phase 1's `push` does the bookkeeping (count, total
   bytes, file size) but does not check overlap. Defer the
   overlap check to phase 12 (fuzz harnesses can assert it as
   an invariant). If we add it in phase 1 it might falsely
   reject legitimate patterns we haven't anticipated.

## Execution

Phase 1 splits into four sub-steps. Each step is a separate
sub-agent invocation. Land one commit per step. Steps 1a and 1b
are independent (different crates); 1c depends on 1a; 1d
depends on all three.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Scaffold `src/crates/resize/` as a new `no_std` workspace crate. Mirror `src/crates/create/Cargo.toml` structure exactly (omit dependencies on the per-format parser crates — phase 1 does not need them; phases 2–6 add them). The Cargo.toml declares `edition = "2021"`, `[lib]` with `crate-type = ["rlib"]`, and dependencies `shared = { path = "../../shared" }` only. Add the crate to the workspace `members` in `src/Cargo.toml` (alphabetically between `crates/raw` and `crates/vhd` would match the existing ordering; place it after `crates/measure` if alphabetical isn't followed). Create `src/crates/resize/src/lib.rs` with `#![no_std]`, the `ResizeError`, `ResizeAction`, `ResizePatch`, `ResizePlan`, `Preallocation`, `VmdkSubformat`, `VhdSubformat`, and the five `*ResizeOpts` structs exactly as specified in the "Public API" section above. The five `plan_resize_*` functions exist as stubs that return `Err(ResizeError::UnsupportedFormat)`. Add the four `*_MAX_RESIZE_SCRATCH` constants with the values specified above. Add `MAX_RESIZE_PATCHES = 128`. No tests in this step (step 1c and 1d cover them). No implementation logic. Run `cargo build -p resize` and `cargo test -p resize` (the latter should pass with zero tests). Confirm `make lint` is clean and `pre-commit run --all-files` passes. |
| 1b | medium | sonnet | none | Add `ResizeConfig` and `ResizeResult` to `src/shared/src/lib.rs` exactly as specified in the "`ResizeConfig` and `ResizeResult` in `shared/`" section above. Place them after `CreateResult` (after line 2316) and before the chain-config section. Magic constants `0x52455349` ("RESI") and `0x52524553` ("RRES"). Same flag/prealloc layout as `CreateConfig`. Same error-code stability discipline as `CreateResult` (only append, never reorder). Add a unit test in `src/shared/src/lib.rs` (in the existing test module if one exists at the bottom of the file; otherwise add one) asserting (a) `ResizeConfig::MAGIC` is `0x52455349`, (b) `ResizeResult::MAGIC` is `0x52524553`, (c) `core::mem::size_of::<ResizeConfig>()` ≤ 256 bytes (the struct should be much smaller; the assertion is a forward-compat tripwire — if a future phase grows it past 256 bytes, that's a deliberate ABI change and the test fails on purpose), (d) `core::mem::size_of::<ResizeResult>()` ≤ 64 bytes. Run `cargo test -p shared` and confirm green. Run `make lint` clean. Do not touch any other file. |
| 1c | high | opus | none | Implement `plan_resize_raw` in `src/crates/resize/src/lib.rs`. Logic: if `opts.new_virtual_size == opts.current_virtual_size` return `Ok(ResizePlan::new(NoOp, opts.new_virtual_size))`; else if `opts.new_virtual_size > opts.current_virtual_size` return `Ok(ResizePlan::new(Grow, opts.new_virtual_size))`; else return `Ok(ResizePlan::new(Shrink, opts.new_virtual_size))`. Raw resize emits zero patches — the host's post-pass `set_len` does the actual work. Then implement the missing trivial pieces: `ResizePlan::new`, `ResizePlan::patches`, `ResizePlan::push`, `ResizePatch::byte_offset`, `ResizePatch::len`. Add a `tests` module at the bottom of `lib.rs` (gated on `#[cfg(test)]`) covering: (1) raw with `new == current` → NoOp + empty patches + total_file_size == new; (2) raw with `new > current` → Grow + empty patches; (3) raw with `new < current` → Shrink + empty patches (note: raw does NOT need `allow_shrink` — the host's `set_len` to a smaller value is a deliberate user choice and qemu-img doesn't require `--shrink` for raw either; verify by running `qemu-img resize -f raw test.raw 100M` against a 1 GiB raw and confirming success without `--shrink`); (4) `ResizePlan::push` increments `patch_count` and updates `total_file_size` is NOT — `push` should NOT touch `total_file_size`; that's set at construction; (5) `ResizePlan::push` returns `ScratchTooSmall` when the storage is full; (6) `ResizePatch::Write::byte_offset` and `len` return the right values; (7) the four stubbed planners (`plan_resize_qcow2`, `plan_resize_vmdk`, `plan_resize_vhd`, `plan_resize_vhdx`) all return `Err(ResizeError::UnsupportedFormat)`. Tests use `#[cfg(test)] extern crate alloc;` only if needed for `Vec<u8>` — most tests need no allocation. Run `cargo test -p resize`, confirm all pass. `make lint` clean. |
| 1d | medium | sonnet | none | Add an `integration tests` directory at `src/crates/resize/tests/round_trip.rs` covering the type-surface invariants: (a) build a `ResizePlan` with `ResizeAction::Grow`, push several `Write` and `Append` patches, iterate `patches()`, assert the count and total length match; (b) push patches until `MAX_RESIZE_PATCHES` is hit, confirm the `MAX_RESIZE_PATCHES`-th push returns `ScratchTooSmall`; (c) assert `Preallocation::Off as u32` is 0 and the enum has exactly four variants (forward-compat tripwire for phase 9); (d) call `plan_resize_raw` with three representative size pairs (`(1 MiB, 2 MiB)`, `(2 MiB, 1 MiB)`, `(1 MiB, 1 MiB)`) and assert action + total_file_size for each; (e) confirm `ResizeError::UnsupportedFormat` is returned by each non-raw planner stub. Tests in `tests/` are std-enabled by default so any helper code can use `Vec` / `String` freely. Run `cargo test -p resize --tests`, confirm green. `make lint` clean. `pre-commit run --all-files` clean. |

## Out of scope for phase 1

Reminders so a sub-agent doesn't drift:

- No guest binary. No `src/operations/resize/` directory in
  this phase.
- No host CLI. No changes to `src/vmm/src/main.rs`.
- No call-table changes. `read_output_sector` is added in
  phase 7, not here.
- No protobuf changes. `ResizeResultMessage` is added in
  phase 7.
- No actual format-specific planning logic for qcow2, vmdk,
  vhd, or vhdx. Those planners exist as stubs returning
  `UnsupportedFormat`. Phases 2, 3, 4, 5, 6 fill them in.
- No preallocation handling. The `Preallocation` enum exists
  in the type surface but `plan_resize_raw` ignores the value
  (the host applies preallocation post-resize in phase 9).
- No reading of existing image headers. The `existing_l1_bytes`
  field on `Qcow2ResizeOpts` exists for phase 2 but phase 1's
  stub does not touch it.
- No `[+-]SIZE` parsing. The host CLI translates relative
  sizes into absolute `new_virtual_size` before populating the
  opts struct. Phase 8 implements the parser.
- Convert is not modified. Create is not modified.
- No documentation changes (phase 13 owns `docs/resize.md`).

## Success criteria for phase 1

- `cargo build -p resize` and `cargo build -p shared` clean.
- `cargo test -p resize` and `cargo test -p resize --tests`
  pass (steps 1c and 1d).
- `cargo test -p shared` passes the new size/magic assertions
  from step 1b.
- `make instar` builds (no regression from the new workspace
  member or the new fields in `shared`).
- `make check-binary-sizes` passes (no new operation binary
  in this phase; the assertion is that existing binaries still
  fit).
- `make lint` clean across the workspace.
- `pre-commit run --all-files` clean.
- `src/operations/convert/src/main.rs` and
  `src/operations/create/src/main.rs` have zero modifications.
- `src/vmm/src/main.rs` has zero modifications.
- `crates/guest-protocol/proto/guest.proto` has zero
  modifications.

## Sub-agent guidance

Each step's brief is self-contained — the sub-agent should not
need to consult this document beyond the `Brief for sub-agent`
column unless it hits one of the open questions above. If it
does, the sub-agent should stop and escalate to the management
session rather than guessing.

Read these files before starting:

- `src/crates/create/src/lib.rs` (1-200, plus the `plan_*`
  function bodies for shape reference).
- `src/crates/create/Cargo.toml` (the Cargo.toml template).
- `src/shared/src/lib.rs:2160-2316` (the `CreateConfig` /
  `CreateResult` template).
- `src/Cargo.toml` (workspace `members` line — add `resize`).
- The master plan `docs/plans/PLAN-resize.md` if you need
  context on what later phases assume from the type surface.

After each step, the management session will:

- Read the actual files (not just trust the diff summary).
- Run `cargo build -p resize`, `cargo test -p resize`,
  `cargo test -p shared`, `make lint`, `pre-commit run
  --all-files`.
- Confirm the success-criteria items above.
- Commit if green, with the standard commit-message format:
  - First line ≤50 chars ending in a period.
  - `Prompt:` paragraph summarising intent across the step.
  - `Assisted-By:` line with model / context / effort.
  - `Signed-off-by: Michael Still <mikal@stillhq.com>`.
  - `Co-Authored-By: Claude <model> ...` line.

If a step fails review (something unrelated changed, a test
regressed, the brief was misinterpreted), discard the
sub-agent's worktree where applicable and re-spawn with a
refined brief rather than patching by hand.
