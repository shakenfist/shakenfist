# PLAN-resize phase 5: VHDX resize planner

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (VHDX format, the two-header sequence-number
protocol, CRC-32C, the metadata region's GUID-keyed item table,
qemu's vhdx_co_truncate behaviour), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that master
plan for overall context. Phases 1 (skeleton + raw + shared
types), 2 (qcow2 grow), 3 (qcow2 shrink), and 4 (VHD grow) are
complete; phase 4's `vhd::plan_grow` and the
`VhdGrowAction` / `decide_dynamic_action` machinery are the
structural template for this phase.

## Mission

Replace the `UnsupportedFormat` stub in `plan_resize_vhdx`
(`src/crates/resize/src/lib.rs`) with a real VHDX **grow**
planner. VHDX has no shrink support upstream in qemu, so phase 5
ships grow only. The planner:

1. Updates the `VirtualDiskSize` metadata item (an 8-byte u64 LE
   at absolute offset `metadata_region_offset + 0x10008`) to the
   new size.
2. If the new size requires more BAT entries than the existing
   BAT region holds, appends a new BAT region at end of file
   (preserving old entries plus
   `PAYLOAD_BLOCK_NOT_PRESENT` for the new entries) and updates
   both region-table copies to point at the new BAT.
3. Commits via the two-header sequence-number dance: the
   inactive header gets `sequence_number + 1` so it becomes the
   higher-numbered (and thus authoritative) header; the
   formerly-active header is then bumped to
   `sequence_number + 2` to restore redundancy. Both headers
   carry CRC-32C checksums computed over their 4 KiB regions.
4. Leaves `log_guid = [0; 16]` in both rewritten headers to
   signal "clean" (no pending log entries). qemu does the same
   on `vhdx_co_truncate`.

Shrink is **not supported by qemu upstream**. The planner
rejects shrink with `UnsupportedShrink`; the master plan's
Future-work list does not propose VHDX shrink either.

## What the survey turned up

- **`VhdxHeader` parser** (`src/crates/vhdx/src/lib.rs:196,210`) —
  fields: `signature`, `checksum`, `sequence_number` (u64
  monotonic), `log_guid` (16 bytes; `[0; 16]` = clean), plus
  `file_write_guid`, `data_write_guid`, `log_offset`,
  `log_length`. Parser at line 210 validates signature + CRC-32C.
  The two-header selection rule at line 698: parser picks the
  header with the higher `sequence_number`.
- **`build_header(buf, sequence_number)`** (`src/crates/vhdx/src/lib.rs:1136`) —
  writes a complete 4 KiB header including CRC-32C at offset 4
  and deterministic GUIDs derived from `sequence_number` bytes.
  `log_guid` stays zero.
- **Region table** (`src/crates/vhdx/src/lib.rs:250,261,1169`) —
  fixed 2 entries (BAT and metadata) at offset 0x30000 and
  0x40000. Each entry is GUID + file_offset + length + required.
  `parse_region_table` validates CRC-32C; `build_region_table`
  writes a fresh table with new BAT/metadata offsets and
  recomputes the CRC.
- **Metadata region** (`src/crates/vhdx/src/lib.rs:344,360,1208`)
  — fixed 1 MiB region. Item layout: table header at offsets
  0..0xC0; items at 0x10000..0x10028. `VirtualDiskSize` is the
  u64 LE at relative offset 0x10008. `build_metadata` writes
  the whole 1 MiB region; `parse_metadata` walks the entries by
  GUID.
- **BAT entries** (`src/crates/vhdx/src/lib.rs:158-178,1320`) —
  8 bytes each, encoding a 3-bit state (in bits 0–2) and an
  offset (in bits 20–63, MB-aligned).
  `PAYLOAD_BLOCK_NOT_PRESENT = 0` is the unallocated marker.
  `build_bat_entry(state, file_offset)` masks correctly.
- **`calculate_bat_layout(virtual_size, block_size,
  logical_sector_size)`** (`src/crates/vhdx/src/lib.rs:1330`) —
  returns `(total_bat_entries, chunk_ratio, payload_blocks)`.
  Resize calls this for the new size to determine whether BAT
  must grow.
- **`compute_crc32c(data, checksum_offset)`**
  (`src/crates/vhdx/src/lib.rs:48`) — the CRC algorithm used by
  headers and region tables.
- **`plan_vhdx` (create crate)** — produces a freshly-laid-out
  VHDX with: file_id @ 0x0, header1 @ 0x10000 (seq=1), header2
  @ 0x20000 (seq=2), region tables @ 0x30000 + 0x40000, BAT @
  0x200000 (MB-aligned), metadata @ (BAT region + size). The
  BAT region in a fresh image is exactly sized — no slack
  between BAT and metadata — so any grow that needs more BAT
  entries forces a relocate.

## Algorithmic design

### Layout-diff: `VhdxGrowAction`

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum VhdxGrowAction {
    /// Virtual size grew but the existing BAT can still hold
    /// every entry; only VirtualDiskSize + the headers change.
    MetadataAndHeaders,
    /// Virtual size grew enough that the BAT needs more entries
    /// than fit in the current region; relocate the BAT to end
    /// of file and update both region-table copies.
    BatGrowRelocate,
}
```

The decision: compute the target `total_bat_entries` via
`calculate_bat_layout(new_virtual_size, block_size,
logical_sector_size)`. If
`target_total_bat_entries <= current_total_bat_entries`,
use `MetadataAndHeaders`. Otherwise `BatGrowRelocate`.

Note: VHDX has a theoretical `BatGrowInPlace` flavour, but
`crates/create::plan_vhdx` produces images with no BAT slack
(BAT and metadata are adjacent), so in practice the in-place
path never fires in our test matrix. Skipped for v1; add to
Future work if a future image source produces images with
explicit BAT padding.

### MetadataAndHeaders path

Patches:
1. `Write { byte_offset: metadata_region_offset + 0x10008,
   bytes: <new_virtual_size as u64 LE (8 bytes)> }` — update
   the VirtualDiskSize item.
2. `Write { byte_offset: <inactive header's offset>,
   bytes: <rebuilt 4 KiB header with seq = old_max + 1> }` —
   atomic commit.
3. `Write { byte_offset: <previously-active header's offset>,
   bytes: <rebuilt 4 KiB header with seq = old_max + 2> }` —
   redundancy restore.

`total_file_size` = `current_file_size`.

### BatGrowRelocate path

Steps:

1. Compute new BAT region size:
   `new_bat_size_bytes = (target_total_bat_entries as u64) * 8`,
   rounded up to 1 MiB (matching the create crate's MB
   alignment).
2. Compute new BAT region offset: `current_file_size`
   (append at end). The new BAT replaces the existing data
   region "ceiling" — but VHDX doesn't have a tail footer like
   VHD, so the new BAT just lives after the existing data.
3. Build the new BAT region in scratch: copy existing BAT
   bytes for entries `0..current_total_bat_entries`, then fill
   the rest with `PAYLOAD_BLOCK_NOT_PRESENT` (zero bytes).
4. Build new region tables (both copies) with the BAT entry's
   `file_offset` updated to the new offset and `length` to the
   new size. CRC-32C recomputed by `build_region_table`.
5. Build the two new headers with the post-resize sequence
   numbers.

Patches:

```
Phase A — prepare:
  1. Append new BAT region at current_file_size
  2. Write updated VirtualDiskSize metadata (8 bytes)
  3. Write new region table 1 (at 0x30000, full 64 KiB)
  4. Write new region table 2 (at 0x40000, full 64 KiB)
Phase B — commit:
  5. Write inactive header (offset 0x10000 or 0x20000) with
     sequence_number = old_max + 1
Phase C — redundancy restore:
  6. Write the other header with sequence_number = old_max + 2
```

`total_file_size` = `current_file_size + new_bat_size_bytes`.

The header rewrite is the atomic commit point. After step 5,
the parser will pick the just-written header (highest
sequence_number); it references the new region table, which
references the new BAT, which has the new entries. The old
region table copies are still readable but lower seq → ignored.
A crash before step 5 leaves the headers untouched; the old
layout remains canonical. A crash after step 5 but before
step 6 leaves the file committed (active header is new) but
without redundancy.

### Crash-safety invariant

Encoded as a planner-internal assertion: patches partition
into three contiguous segments by index:

```
[ prepare patches (BAT append + metadata write + region tables) ]
[ inactive-header rewrite (the atomic commit) ]
[ active-header rewrite (redundancy restore) ]
```

The inactive header is the one with the lower current
sequence_number. After step 5 it's the higher (= active);
after step 6 both are higher than they were and the previously-
active is once again the highest.

### `data_write_guid` and `log_guid`

`build_header` derives both deterministically from the
sequence_number bytes. For info-equivalence with qemu, our
parity contract is "fields readable, values consistent" — not
byte-identical GUIDs. Document in `docs/quirks.md` (phase 13).

`log_guid` stays `[0; 16]` (clean) — `build_header` writes it
that way and we don't introduce any log entries.

### `data_write_guid` bump on resize

Spec: `data_write_guid` should be bumped whenever an image is
opened for write (or per the spec, "when data is changed").
qemu bumps it on resize. Our `build_header` already sets a
fresh `data_write_guid` derived from the new sequence_number,
so the resize naturally produces a different value than before
— matches qemu's intent if not its exact value.

### Sector_size assumption

VHDX `logical_sector_size` is typically 512 (sometimes 4096).
`plan_vhdx` defaults to 512. The resize planner reads it from
the parsed metadata (we'd pass it through opts) and uses it in
`calculate_bat_layout`.

## Public API delta from phase 4

```rust
pub struct VhdxResizeOpts<'a> {
    // ... existing phase-1 fields ...
    pub current_virtual_size: u64,
    pub new_virtual_size: u64,
    pub block_size: u32,
    pub preallocation: Preallocation,
    // ↓ added in phase 5 ↓
    /// Existing header bytes for the *active* header (the one
    /// with the higher sequence_number; the parser picks this
    /// one). Used to read `sequence_number` and `log_guid`.
    pub existing_active_header: &'a [u8],
    /// Which header is currently active: `0x10000` or
    /// `0x20000`. The planner writes the *other* header first
    /// to bump it past the active.
    pub current_active_header_offset: u64,
    /// Current `sequence_number` of the active header.
    pub current_sequence_number: u64,
    /// Existing region table bytes (64 KiB; either copy is
    /// fine — they're identical when consistent).
    pub existing_region_table: &'a [u8],
    /// Existing BAT bytes (current_total_bat_entries × 8). The
    /// planner walks these to preserve allocated-block
    /// references in the new BAT region.
    pub existing_bat: &'a [u8],
    /// Current BAT region's file offset (from the region
    /// table).
    pub current_bat_offset: u64,
    /// Current BAT region's length in bytes (from the region
    /// table).
    pub current_bat_length: u32,
    /// Current total_bat_entries (decoded by the parser at
    /// init time via calculate_bat_layout).
    pub current_total_bat_entries: u32,
    /// Current metadata region's file offset.
    pub current_metadata_offset: u64,
    /// Current metadata region's length (typically 1 MiB).
    pub current_metadata_length: u32,
    /// `logical_sector_size` from the existing metadata.
    pub logical_sector_size: u32,
    /// `physical_sector_size` from the existing metadata.
    pub physical_sector_size: u32,
    /// Whether the existing image has a parent (differencing
    /// disk). Resize rejects differencing with
    /// `UnsupportedSubformat`.
    pub has_parent: bool,
    /// Current file size in bytes (pre-resize EOF). The
    /// relocate path appends new BAT here.
    pub current_file_size: u64,
}
```

## Test matrix

| Test name | Setup |
|-----------|-------|
| `metadata_only_grow_when_bat_fits` | start 1 GiB at default block_size (32 MiB) → 32 entries; grow to 2 GiB → 64 entries. BAT region (1 MiB) holds 131,072 entries; fits easily → MetadataAndHeaders path. |
| `bat_grow_relocate_at_very_large_size` | start 1 GiB at small block_size (1 MiB) → 1024 entries; grow to a size whose target entries exceed 131,072 (forcing a multi-MiB BAT region). Tricky to size cleanly — see open question 3 for the threshold. May not be reachable in practice; skip and document. |
| `noop_when_sizes_equal` | NoOp action. |
| `header_sequence_numbers_bump_correctly` | After resize, the formerly-active header has sequence_number = old_max + 2 and the formerly-inactive has sequence_number = old_max + 1. |
| `header_log_guid_stays_zero` | Both rewritten headers have log_guid = [0; 16]. |
| `virtualDiskSize_metadata_updated` | After resize, the 8 bytes at metadata_offset + 0x10008 decode as the new virtual size. |
| `parses_round_trip_via_VhdxState_init` | After applying patches, the full VHDX init pipeline (parse headers → pick higher seq → parse region table → parse metadata → calculate_bat_layout) succeeds and reports the new virtual_size. |

Negative paths:

| Test name | Setup |
|-----------|-------|
| `rejects_shrink_without_flag` | new < current → ShrinkWithoutFlag. |
| `rejects_shrink_with_flag` | new < current + --shrink → UnsupportedShrink (matches qemu's no-VHDX-shrink stance). |
| `rejects_differencing_image` | has_parent=true → UnsupportedSubformat. |
| `rejects_zero_new_virtual_size` | new = 0 → InvalidNewVirtualSize. |
| `rejects_preallocation_metadata` | Preallocation::Metadata → PreallocationUnsupported. |
| `rejects_invalid_block_size` | block_size not power-of-two in [1 MiB, 256 MiB] → InvalidNewVirtualSize. |

## Open questions

1. **`current_active_header_offset` discovery**. The host /
   guest pre-pass reads both headers and picks the one with
   the higher sequence_number. The planner trusts the opts.
   *Recommendation*: yes — keep the planner pure; let the
   guest's pre-pass do the comparison.

2. **Dirty-log handling**. If the existing image's
   `log_guid != [0; 16]`, the file is in a dirty state and
   the log holds uncommitted entries. Resize should refuse
   with `RequiresCheckFirst` rather than risk amplifying
   inconsistency. *Recommendation*: yes — reject dirty
   images.

3. **In-practice unreachability of `BatGrowRelocate`**. With
   default block_size = 32 MiB and the create crate's 1 MiB
   minimum BAT region, the BAT holds 131,072 entries =
   covers up to ~4 TiB virtual size before needing more
   space. For typical test sizes (≤ 16 GiB) the
   `MetadataAndHeaders` path always fires. The
   `BatGrowRelocate` code is still worth shipping because:
   (a) phase 6 (vmdk) may need the same pattern; (b) future
   image sources (qemu) could produce images with smaller
   BAT regions; (c) it's not much extra code.

4. **Sequence-number wraparound**. u64 sequence numbers don't
   wrap in any realistic timeframe; we don't guard.

5. **Region table size**. Both region table copies are 64 KiB
   each (one full sector-region). Even with the new BAT
   relocated, the region table itself doesn't grow — only the
   one BAT entry inside it changes. The write covers the
   whole 64 KiB to recompute the CRC.

6. **VHDX without `FileParameters.has_parent`**. The
   `has_parent` flag in the metadata's `FileParameters` item
   distinguishes regular VHDX from differencing VHDX. Phase 5
   supports `has_parent = false` only.

7. **Atomic-write guarantee for header writes**. The two-header
   protocol depends on each header write being atomic with
   respect to the parser's reads. A 4 KiB write isn't
   atomic at the page-cache level, but the parser's CRC
   validation catches partial writes — a torn header fails
   CRC validation and the parser falls back to the other
   header. So the redundancy is fault-tolerant by design.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | medium | sonnet | none | Extend `VhdxResizeOpts` in `src/crates/resize/src/lib.rs` with the new fields documented in the "Public API delta" section (plus the lifetime parameter — mirroring the phase-4 VhdResizeOpts pattern). Update the existing inline test and `tests/round_trip.rs` to drop the now-out-of-date stub assertion (VHDX gets dedicated coverage in `tests/vhdx_grow.rs`, added in 5c). Create an empty private module `src/crates/resize/src/vhdx.rs` with a `pub(crate) fn plan_grow` returning `Err(UnsupportedFormat)`. Wire `plan_resize_vhdx` to dispatch into it. Add `vhdx = { path = "../vhdx" }` to both `[dependencies]` and `[dev-dependencies]` in `src/crates/resize/Cargo.toml`. `make instar`, `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |
| 5b | high | opus | worktree | Implement `vhdx::plan_grow` in `src/crates/resize/src/vhdx.rs`. Internal structure mirrors phase 4: `VhdxGrowAction` enum (`MetadataAndHeaders`, `BatGrowRelocate`), `decide_action`, `plan_metadata_and_headers`, `plan_bat_grow_relocate`. Validate: reject `new == 0` (InvalidNewVirtualSize); reject `new < current` (ShrinkWithoutFlag or UnsupportedShrink depending on `allow_shrink`); reject `Preallocation::Metadata`; reject `has_parent = true` (UnsupportedSubformat); reject `log_guid != [0; 16]` on the active header (RequiresCheckFirst); reject block_size not power-of-2 or out of [1 MiB, 256 MiB]. Use `vhdx::build_header`, `vhdx::build_region_table`, `vhdx::build_metadata` for byte construction. Sequence number protocol: write the *currently-inactive* header (the one NOT at `opts.current_active_header_offset`) with seq = `current_sequence_number + 1` as the atomic commit; then write the *currently-active* header with seq = `current_sequence_number + 2` for redundancy. Use phase 2c's stage-then-emit idiom to avoid borrow conflicts. Add inline unit tests for `decide_action`, the sequence-number computation, and the BAT growth threshold. Risky: worktree isolation. |
| 5c | medium | sonnet | none | Add `src/crates/resize/tests/vhdx_grow.rs` mirroring `tests/vhd_grow.rs`'s pattern. Use `crates/create::plan_vhdx` to build starting images, populate `VhdxResizeOpts` from the parsed header / region table / metadata / BAT, apply the patches via the existing `apply_resize` helper pattern, re-parse with `vhdx::VhdxState::init` (or via lower-level parsers for cases where init isn't suitable in pure-Rust tests), assert virtual size + sequence_number bump + log_guid stays zero. Cover every positive and negative row from the "Test matrix" section. `make lint`, `make test-rust`, `pre-commit run --all-files` clean. |

## Out of scope for phase 5

- VHDX shrink. qemu doesn't implement it upstream; no future-
  work entry.
- Differencing VHDX. Rejected with `UnsupportedSubformat`.
- The theoretical `BatGrowInPlace` path. Add to Future work
  if/when an image source produces VHDX images with BAT slack.
- Guest binary / host CLI / call-table changes / protobuf
  (phases 7 and 8).
- Preallocation modes other than `Off` (the post-pass
  `falloc` / `full` modes layer on top in phase 9; VHDX
  doesn't support `metadata` preallocation in the qcow2
  sense).
- WAL log replay. The planner refuses dirty images with
  RequiresCheckFirst.

## Success criteria for phase 5

- `cargo build -p resize` clean.
- `cargo test -p resize` and `cargo test -p resize --tests`
  pass; the new vhdx unit tests (~5) and the new vhdx
  integration tests (~10) raise the total.
- All prior resize tests continue to pass.
- `make instar` builds.
- `make check-binary-sizes`, `make lint`, `pre-commit run
  --all-files` all clean.
- `plan_resize_vhdx` for a grow request returns a valid
  `ResizePlan` for every positive-path test case, with patches
  in the documented order (prepare → inactive-header-commit →
  active-header-redundancy).
- For round-trip tests: post-resize file parses correctly via
  `VhdxState::init` (or equivalent lower-level chain) and
  reports the new virtual_size.

## Sub-agent guidance

Read these files before starting any step:

- `src/crates/vhdx/src/lib.rs:48` (`compute_crc32c`).
- `src/crates/vhdx/src/lib.rs:60-180` (constants, GUIDs, state
  encoding).
- `src/crates/vhdx/src/lib.rs:196-280` (header + region-table
  parsers).
- `src/crates/vhdx/src/lib.rs:344-450` (metadata parser).
- `src/crates/vhdx/src/lib.rs:1121-1330` (file-id /
  header / region-table / metadata / BAT-entry builders +
  `calculate_bat_layout`).
- `src/crates/resize/src/vhd.rs` (the structural template, in
  particular the stage-then-emit borrow-checker discipline).
- `src/crates/resize/tests/vhd_grow.rs` (the integration test
  template).
- `src/crates/create/src/lib.rs::plan_vhdx` (the
  freshly-created-image layout).

The management session review checklist is the same as prior
phases (read the diff, run lint/tests/pre-commit, check that
the patch ordering invariants hold).
