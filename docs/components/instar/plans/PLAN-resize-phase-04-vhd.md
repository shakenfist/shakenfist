# PLAN-resize phase 4: VHD resize planner

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (VHD/VPC format, CHS geometry, the footer
checksum algorithm, `qemu-img resize` semantics), research as
needed to give a confident answer. Flag any uncertainty
explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that master
plan for overall context. Phases 1 (skeleton + raw + shared
types), 2 (qcow2 grow), and 3 (qcow2 shrink) are complete; the
qcow2 dispatch and the `Qcow2GrowAction` / `decide_action`
machinery in `src/crates/resize/src/qcow2.rs` are the structural
template for this phase's vhd module.

## Mission

Replace the `UnsupportedFormat` stub in `plan_resize_vhd`
(`src/crates/resize/src/lib.rs`) with a real VHD **grow** planner
covering both subformats:

1. **Fixed VHD grow**: zero the old footer at
   `current_virtual_size` and write a fresh footer at
   `new_virtual_size` with updated `current_size` / `cylinders`
   / `heads` / `sectors_per_track` / `checksum`. `total_file_size
   = new_virtual_size + 512`.
2. **Dynamic VHD grow**: extend the BAT (in place if the
   existing region has slack; relocated to end-of-file
   otherwise), rewrite the dynamic header to reflect the new
   `max_table_entries` and (if relocated) the new `table_offset`,
   rewrite both footer copies with the updated `current_size`
   and CHS geometry, and emit the BAT extension entries as
   `BAT_UNALLOCATED` (`0xFFFFFFFF`).

Shrink is **deferred to future work** per the master plan. The
planner rejects shrink requests with `UnsupportedShrink`
(matching the existing behaviour of the vmdk / vhdx stubs).

This phase ships **VHD only**. The vmdk and vhdx planners stay
stubbed; their work lands in phases 6 and 5 respectively.

## What the survey turned up

- **VhdFooter / VhdDynamicHeader parsers**
  (`src/crates/vhd/src/lib.rs:124-217`) already surface every
  field we need: `current_size`, `disk_type`, `cylinders`,
  `heads`, `sectors_per_track`, `table_offset`,
  `max_table_entries`, `block_size`.
- **`build_footer(buf, current_size, disk_type, data_offset,
  uuid)`** (`src/crates/vhd/src/lib.rs:765`) writes a complete
  512-byte footer including the recomputed checksum and the
  recomputed CHS geometry (via `compute_vhd_geometry`). Resize
  uses this for both fixed and dynamic; the only complication
  is `uuid` — resize must read the existing UUID from the
  parsed footer and pass it through verbatim so the rewritten
  footer keeps the same disk identity.
- **`build_dynamic_header(buf, table_offset, max_table_entries,
  block_size)`** (`src/crates/vhd/src/lib.rs:816`) writes the
  full 1024-byte dynamic header with recomputed checksum.
- **`compute_vhd_geometry(size)`**
  (`src/crates/vhd/src/lib.rs:248`) implements the VPC CHS
  algorithm; resize calls it once for the new size and embeds
  the resulting `(cylinders, heads, sectors_per_track)` into
  the rewritten footers.
- **`count_allocated_in_bat`**
  (`src/crates/vhd/src/lib.rs:314`) walks BAT entries treating
  `BAT_UNALLOCATED = 0xFFFFFFFF` as the marker. Resize doesn't
  use this directly but does need a similar walk: "what is
  the smallest non-`BAT_UNALLOCATED` block-host-offset?" That
  tells us whether the BAT can grow in place.
- **Constants**: `FOOTER_SIZE = 512`, `DYNAMIC_HEADER_SIZE =
  1024`, `BAT_UNALLOCATED = 0xFFFFFFFF`, `DEFAULT_BLOCK_SIZE
  = 2 MiB`, `DISK_TYPE_FIXED = 2`, `DISK_TYPE_DYNAMIC = 3`,
  `DISK_TYPE_DIFFERENCING = 4` (all in
  `src/crates/vhd/src/lib.rs:80-99`).
- **Phase 2's `Qcow2ResizeOpts` and `qcow2` submodule**
  pattern is the template: extend `VhdResizeOpts` with the
  existing-image-state fields the planner needs (footer bytes,
  dynamic header bytes, BAT bytes, current geometry), then
  add a private `src/crates/resize/src/vhd.rs` module that
  hosts `plan_grow_fixed` and `plan_grow_dynamic`.
- **Phase 2's `decide_action` for qcow2** has a counterpart
  here: `VhdGrowAction { FixedGrow, DynamicGrowInPlace,
  DynamicGrowRelocate }`. The dispatch decision is local to
  the vhd module; the public `plan_resize_vhd` calls
  `vhd::plan_grow`.

## Algorithmic design

### Layout-diff: `VhdGrowAction`

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum VhdGrowAction {
    /// Fixed VHD: move footer + rewrite.
    FixedGrow,
    /// Dynamic VHD: extend BAT in place (existing BAT region
    /// has slack to fit new entries before the first allocated
    /// block).
    DynamicGrowInPlace,
    /// Dynamic VHD: append a relocated BAT region at end of
    /// file (the existing BAT region collides with allocated
    /// blocks).
    DynamicGrowRelocate,
}
```

Decision inputs:
- `subformat: VhdSubformat` — selects `FixedGrow` vs Dynamic.
- For Dynamic: `(table_offset, current_bat_size_bytes,
  new_bat_size_bytes, first_allocated_block_offset)`. If
  `table_offset + new_bat_size_bytes <=
  first_allocated_block_offset`, in-place. Else relocate.
- An empty dynamic image (no allocated blocks) always grows
  in place because there's nothing to collide with — the BAT
  can expand up to `(file_size - 512 - table_offset)` bytes
  before bumping into the tail footer. Detect via
  `count_allocated_in_bat(existing_bat_bytes) == 0`.

### FixedGrow path

Patches:
1. `Write { byte_offset: current_virtual_size, bytes:
   zero[512] }` — zero out the bytes the old footer occupied.
2. `Write { byte_offset: new_virtual_size, bytes:
   <rebuilt 512-byte footer> }` — the new footer with
   `current_size = new_virtual_size`, recomputed CHS, recomputed
   checksum, same UUID.

`total_file_size = new_virtual_size + 512`.

The host's pre-pass `set_len(total_file_size)` extends the
file with sparse zeros from `current_virtual_size + 512` up
to `new_virtual_size` before any patch is applied; the
zero-fill bytes that constitute the new data region are
implicit. Patch 1 only zeros the 512 bytes the old footer
sat in.

### DynamicGrowInPlace path

The BAT can grow without relocation. Steps:

1. Compute `new_max_table_entries = ceil(new_virtual_size /
   block_size)`.
2. Compute `bat_extension_bytes = (new_max_table_entries -
   old_max_table_entries) * 4`. These are appended to the BAT
   region in place. Their values are all `BAT_UNALLOCATED`
   (`0xFFFFFFFF`).
3. Build the new dynamic header bytes with
   `max_table_entries = new_max_table_entries` and the same
   `table_offset` as before.
4. Build the new footer bytes (both copies) with
   `current_size = new_virtual_size`, recomputed CHS,
   recomputed checksum.
5. Compute `new_total_file_size`: same as `current_file_size`
   unless the BAT extension pushes past the old tail-footer
   position (it shouldn't if the in-place check passed). The
   tail footer stays where it is at `current_file_size - 512`,
   but its content is rewritten.

Patches in crash-safe order:

```
Phase A (prepare):
  1. Write { byte_offset: bat_extension_start,
              bytes: <0xFFFFFFFF repeated for new entries> }
  2. Write { byte_offset: 512,
              bytes: <new dynamic header (1024 bytes)> }
  3. Write { byte_offset: current_file_size - 512,
              bytes: <new tail footer (512 bytes)> }
Phase B (commit):
  4. Write { byte_offset: 0,
              bytes: <new head footer (512 bytes)> }
```

The head-footer rewrite is the atomic commit point. A VHD
parser reads the head footer first; if invalid, falls back to
the tail. A crash before patch 4 leaves the file pointing at
the OLD size (head footer unchanged) but with a new dynamic
header in place. The parser's head-footer authority means
reads come out as before. The new BAT entries are
`BAT_UNALLOCATED`, indistinguishable from "this BAT slot was
never populated", so they're benign.

`total_file_size = current_file_size`.

### DynamicGrowRelocate path

The existing BAT region collides with allocated blocks; the
new BAT goes at end of file (between the data and the tail
footer position, after the data shifts up).

Layout post-resize (in order, low → high offset):
- `0..512`: new head footer
- `512..1536`: new dynamic header (pointing at new BAT)
- `1536..bat_offset`: original BAT region (orphaned;
  contains the old BAT bytes — left as garbage)
- `bat_offset..first_block_offset`: untouched data blocks
- `first_block_offset..end_of_data`: untouched data blocks
- `end_of_data..end_of_data + new_bat_size`: new BAT (all
  preserved old entries + new `0xFFFFFFFF` entries)
- `end_of_data + new_bat_size..total_file_size - 512`:
  padding (none normally)
- `total_file_size - 512..total_file_size`: new tail footer

Patches:

```
Phase A (prepare):
  1. Append { byte_offset: <end of data>,
              bytes: <new BAT region (old entries + new BAT_UNALLOCATED)> }
  2. Write  { byte_offset: 512,
              bytes: <new dynamic header (table_offset = new BAT offset)> }
  3. Write  { byte_offset: <new tail footer offset>,
              bytes: <new tail footer> }
Phase B (commit):
  4. Write  { byte_offset: 0,
              bytes: <new head footer> }
```

The "end of data" offset is the highest byte the existing
data spans — practically, `current_file_size - 512` (the old
tail footer position). The new BAT lands there, the new tail
footer lands after the new BAT.

`total_file_size = current_file_size - 512 +
new_bat_size_bytes + 512 = current_file_size +
new_bat_size_bytes`.

### Crash-safety invariant

For both DynamicGrow paths: the head footer is the atomic
commit point. Patch order is:

```
[ prepare patches (BAT, dynamic header, tail footer) ]
[ head footer rewrite (single 512-byte Write at offset 0) ]
```

No cleanup phase. The orphaned bytes in the old BAT region
(for the Relocate path) are harmless because nothing
references them after the dynamic-header rewrite.

For FixedGrow: the only "metadata" is the footer at end of
file. We zero the old footer first (so a crash leaves a
file with no recoverable footer in that position), then
write the new footer at the new EOF. A crash between the
two patches leaves the file with no valid footer at all —
catastrophic. Mitigation: swap the order:

```
Phase A (prepare):  Write new footer at new_virtual_size
Phase B (commit):   Zero the old footer at current_virtual_size
```

Now if we crash after Phase A, the file has two footers (old
in the middle of the data region, new at the new EOF). The
parser reads the LAST 512 bytes of the file as the footer,
which is the new footer. The old footer sits in the data
region as 512 bytes of garbage — readable as data but
indistinguishable from any other data. Acceptable. After
Phase B, the old footer is zeroed and the file is clean.

(This is a deliberate departure from "prepare → commit" for
fixed VHD, motivated by the format's lack of a second
metadata anchor. Documented in the planner's comments.)

### `block_size` is not user-tunable on resize

Dynamic VHD's `block_size` is fixed at create time and cannot
be changed by resize. The planner reads it from the existing
dynamic header and uses it as-is. If the new virtual size
isn't `block_size`-aligned, qemu does NOT round up — it
allows non-block-aligned virtual sizes (last block of the
virtual range is partially addressable). Match qemu.

### Differencing VHD (DISK_TYPE_DIFFERENCING = 4)

Differencing VHDs have a parent reference and a different
header layout (parent locator extension after the dynamic
header). Phase 4 supports `DISK_TYPE_FIXED` and
`DISK_TYPE_DYNAMIC` only; differencing is rejected with
`UnsupportedSubformat`. Add to Future work.

## Public API delta from phase 3

```rust
// src/crates/resize/src/lib.rs

pub struct VhdResizeOpts<'a> {
    // ... existing phase-1 fields ...
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub block_size: u32,
    pub subformat: VhdSubformat,
    pub allow_shrink: bool,
    pub preallocation: Preallocation,
    // ↓ added in phase 4 ↓
    /// Existing footer bytes (512). For dynamic, this is the
    /// head footer at offset 0. Resize reads UUID and disk_type
    /// from here so the rewritten footers preserve them.
    pub existing_footer: &'a [u8],
    /// Existing dynamic header bytes (1024). Only meaningful
    /// for dynamic; `&[]` for fixed.
    pub existing_dynamic_header: &'a [u8],
    /// Existing BAT bytes. The planner walks these to detect
    /// whether in-place extension is safe (no allocated block
    /// at offset < table_offset + new_bat_size).
    pub existing_bat: &'a [u8],
    /// Current file size in bytes (pre-resize EOF). The
    /// relocate path uses this to compute where the new BAT
    /// region lands.
    pub current_file_size: u64,
    /// Disk type (DISK_TYPE_FIXED / DYNAMIC). The planner
    /// rejects DIFFERENCING with UnsupportedSubformat.
    pub disk_type: u32,
    /// Current dynamic header's `table_offset`. 0 for fixed.
    pub current_table_offset: u64,
    /// Current dynamic header's `max_table_entries`. 0 for fixed.
    pub current_max_table_entries: u32,
}
```

`VhdSubformat` already has `Dynamic` and `Fixed` variants from
phase 1; no enum change.

## Test matrix

| Test name | Setup |
|-----------|-------|
| `fixed_grow_small` | start 16 MiB fixed, end 64 MiB. Verify total_file_size = 64 MiB + 512; new footer at 64 MiB; old footer zeroed. |
| `fixed_grow_large` | start 1 GiB fixed, end 4 GiB. |
| `dynamic_grow_in_place_no_blocks_allocated` | start 1 GiB dynamic, end 2 GiB. Default block_size (2 MiB) → old max_table_entries = 512, new = 1024. New BAT bytes fit in the existing region. |
| `dynamic_grow_in_place_with_some_blocks_allocated_but_BAT_has_slack` | start 1 GiB dynamic with first block at offset = bat_offset + (512 + slack), end 2 GiB. The slack accommodates the new BAT entries. |
| `dynamic_grow_relocate_when_blocks_block_the_bat` | start 1 GiB dynamic with allocated blocks crowding the BAT region, end 2 GiB. Forces relocation. |
| `dynamic_grow_preserves_uuid_and_disk_identity` | Compare footer UUID before and after; must match. |
| `noop_when_sizes_equal` | NoOp action, empty patches. |

Negative paths:

| Test name | Setup |
|-----------|-------|
| `rejects_shrink` | new < current → UnsupportedShrink. |
| `rejects_shrink_with_flag_pending` | --shrink + new < current → UnsupportedShrink (future work). |
| `rejects_differencing_subformat` | DISK_TYPE_DIFFERENCING in opts → UnsupportedSubformat. |
| `rejects_zero_new_virtual_size` | new = 0 → InvalidNewVirtualSize. |
| `rejects_preallocation_metadata` | Preallocation::Metadata on vhd → PreallocationUnsupported (no qcow2-style L2 prealloc for vhd). |

## Open questions

1. **`current_virtual_size` cross-check.** The opts carry
   `current_virtual_size`; the planner could also read it from
   `existing_footer`'s `current_size` field and cross-check.
   *Recommendation*: yes, cross-check; return `HeaderMismatch`
   if they disagree, mirroring qcow2's behaviour.

2. **CHS geometry rounding.** `compute_vhd_geometry` truncates
   `total_sectors / sectors_per_track` to compute cylinders.
   For some sizes, `cylinders * heads * sectors_per_track <
   total_sectors`, leaving some sectors above the CHS
   addressable range. qemu accepts this — the `current_size`
   field is authoritative. Match qemu; don't fail when CHS
   under-represents the size.

3. **Block-size-misaligned new_virtual_size.** Decided above:
   match qemu, allow non-aligned sizes. The last block is
   partially addressable.

4. **In-place vs relocate threshold.** The "BAT region has
   slack" check is precise: in-place iff
   `table_offset + new_bat_size_bytes <=
   first_allocated_block_offset`. If no blocks are allocated,
   `first_allocated_block_offset = current_file_size - 512`
   (the tail footer position). *Recommendation*: implement
   exactly this check.

5. **Old BAT region cleanup on relocate.** The relocated path
   leaves the old BAT bytes in place (no longer pointed at
   by the dynamic header). They become garbage in the data
   region. The data region is sparse for unallocated blocks
   so this is a contained leak. *Recommendation*: do NOT
   zero the old BAT — it'd add a wide Write patch for no
   integrity gain. Document as a deliberate trade-off.

6. **`disk_type` validation.** Reject anything other than
   FIXED, DYNAMIC, DIFFERENCING. Differencing returns
   UnsupportedSubformat; unknown types return
   UnsupportedFormat.

7. **Fixed-VHD footer location during grow.** Discussed
   above; new footer first, then zero old footer. Crash
   between the two leaves the file with two valid footers,
   the last of which (the new one) is the one the parser
   uses.

8. **Scratch sizing for the new BAT region in the
   Relocate case.** Worst case: `(target_size /
   block_size) * 4` bytes. For 64 GiB at default 2 MiB
   block: ~32K entries * 4 = 128 KiB. The current
   `VHD_MAX_RESIZE_SCRATCH = 4 MiB` is comfortable but
   needs the planner to enforce the cap.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Extend `VhdResizeOpts` in `src/crates/resize/src/lib.rs` with the new fields documented in the "Public API delta" section (`existing_footer`, `existing_dynamic_header`, `existing_bat`, `current_file_size`, `disk_type`, `current_table_offset`, `current_max_table_entries`). Update existing call sites: the inline test in `lib.rs` and the integration tests in `tests/round_trip.rs` (the latter constructs VhdResizeOpts to confirm the stub returns `UnsupportedFormat`). Thread `&[]` and `0` defaults through the test fixtures so phase 4b's planner has the fields it needs but pre-existing tests continue to pass. Create an empty private module `src/crates/resize/src/vhd.rs` with a doc comment (just `//! VHD-specific resize planning.`); phase 4b fills it. Add `vhd = { path = "../vhd" }` to `src/crates/resize/Cargo.toml`. `make instar`, `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |
| 4b | high   | opus | worktree | Implement the VHD grow planner in `src/crates/resize/src/vhd.rs`. Public entry from the lib: replace `plan_resize_vhd`'s stub with `vhd::plan_grow(opts, scratch)`. Internal structure: `VhdGrowAction` enum, `decide_action` function, `plan_fixed_grow`, `plan_dynamic_grow_in_place`, `plan_dynamic_grow_relocate`. Use `vhd::build_footer` and `vhd::build_dynamic_header` from the parser crate; pass the UUID extracted from `opts.existing_footer` so disk identity is preserved. Cross-check `opts.current_virtual_size` against the footer's `current_size` field and return `HeaderMismatch` on disagreement. Reject `disk_type == DIFFERENCING` with `UnsupportedSubformat`; reject unknown disk types with `UnsupportedFormat`. Reject `Preallocation::Metadata` with `PreallocationUnsupported`. Reject shrink (new < current) with `UnsupportedShrink` (phase 4 doesn't ship shrink); reject `new == current` with `NoOp`. Follow phase 2c's stage-then-emit idiom for borrow-safety: do all scratch mutations first, then assemble patches. Risky: worktree isolation. Add inline unit tests for `decide_action` (Fixed / DynamicInPlace / DynamicRelocate / NoOp/shrink reject) and for the per-flavour patch counts. |
| 4c | medium | sonnet | none | Add `src/crates/resize/tests/vhd_grow.rs` mirroring `tests/qcow2_grow.rs`'s pattern. Use `crates/create::plan_vhd` to build starting images (both Fixed and Dynamic at representative sizes), populate `VhdResizeOpts` from the parsed footer/dynamic header/BAT bytes, apply the patches via the existing `apply_resize` helper pattern, re-parse with `vhd::VhdFooter::parse` and `vhd::VhdDynamicHeader::parse`, assert virtual size + CHS + disk identity round-trip. Forge an allocated block (similar to qcow2 shrink's `forge_allocated_cluster`) to exercise the Relocate path. Cover every positive and negative row from the "Test matrix" section. `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |

## Out of scope for phase 4

- VHD shrink. Same allocation-walk story as qcow2 shrink; add
  to Future work entries in the master plan (already noted).
- VHDX. Phase 5.
- VMDK. Phase 6.
- Differencing VHD. Reject; add to Future work.
- Encrypted VHD. VHD doesn't natively encrypt; n/a.
- Guest binary / host CLI / call-table changes / protobuf
  (phases 7 and 8).
- Preallocation modes other than `Off` (the post-pass
  `falloc` / `full` modes layer on top in phase 9; VHD
  doesn't support `metadata` preallocation in the same sense
  qcow2 does).

## Success criteria for phase 4

- `cargo build -p resize` clean.
- `cargo test -p resize` and `cargo test -p resize --tests`
  pass; the new vhd unit tests (~5) and the new vhd
  integration tests (~10) raise the total.
- All prior resize tests (raw, qcow2 grow, qcow2 shrink)
  continue to pass.
- `make instar` builds.
- `make check-binary-sizes`, `make lint`, `pre-commit run
  --all-files` all clean.
- `plan_resize_vhd` for a grow request returns a valid
  `ResizePlan` for every positive-path test case, with
  patches in the documented order (prepare → head footer for
  dynamic; new footer → zero old footer for fixed).
- For positive-path tests that round-trip parse: virtual
  size, CHS geometry, and UUID match the expected post-resize
  values.

## Sub-agent guidance

Read these files before starting any step:

- `src/crates/vhd/src/lib.rs:100-220` (parsers).
- `src/crates/vhd/src/lib.rs:227-298` (checksum + CHS).
- `src/crates/vhd/src/lib.rs:300-320` (BAT-walk helper).
- `src/crates/vhd/src/lib.rs:760-838` (builders).
- `src/crates/resize/src/qcow2.rs` (the structural template,
  especially `plan_grow` dispatch, `decide_action`, and the
  stage-then-emit idiom in `plan_l1_and_refcount_grow`).
- `src/crates/resize/tests/qcow2_grow.rs` (the integration
  test template).
- `src/crates/create/src/lib.rs` (`plan_vhd` for building
  starting images in tests).

The management session review checklist is the same as prior
phases (read the diff, run lint/tests/pre-commit, check that
the patch ordering invariants hold).
