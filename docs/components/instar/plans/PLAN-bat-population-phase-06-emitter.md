# Phase 6: the streaming BAT filler, the fixed `plan_vhdx`, and qemu's VHDX structure

Phase 6 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation ("What qemu-img actually emits" and "What instar does
today"), open questions 4, 7 and 8, and the phase 6 paragraph of
"Sequencing rationale" first. This file assumes them, and corrects that
paragraph where the survey found it incomplete.

Planning effort: high, as the master plan's guidance sets for phases 6
and 7: they emit structures whose only external check is a structural
comparison. The survey also found that instar's shared VHDX builders
differ from qemu's in more fields than the master plan knew, and that
fixing them changes the bytes of every VHDX instar writes (decision 1).

## Decisions already taken by the operator

Every open question in the master plan was resolved with its
recommendation adopted (Michael Still, 2026-10-21). The ones this
phase carries out:

* **Open question 4:** never materialise a fixed BAT. Each entry is a
  pure function of its index, so a filler writes one caller-supplied
  buffer of entries at a time.
* **Open question 7:** dynamic VHDX does not honour
  `block_state_zero`. Its BAT stays `NOT_PRESENT`.
* **Open question 8:** a fixed image's default `block_state_zero` is
  qemu's, `on`, so every payload entry is `PAYLOAD_BLOCK_ZERO`.

Neither is revisited here.

## Scope

In:

* **qemu's structure in the shared VHDX builders**, for every writer
  (decision 1). The region table's Required bit, the metadata items'
  placement and flags, and the physical sector size. These are
  `vhdx::build_region_table` and `vhdx::build_metadata`, which create,
  convert and resize all call.
* **The fixed layout rule**: qemu's data start and file length for a
  fixed image, on `VhdxLayout` (decision 3).
* **The streaming BAT filler** in the vhdx crate (decision 4).
* **A fixed subformat in `plan_vhdx`**, which returns the structure
  writes plus everything the guest needs to stream the BAT and extend
  the file (decision 5).
* Crate tests against qemu's pinned structure, and mutation cases kept
  in `tools/mutate-differencing.sh` (decisions 6 and 7).

Out:

* **Any guest or host change for fixed output.** The create op keeps
  asking for a dynamic image, and the host keeps refusing
  `subformat=fixed`. Streaming the BAT through the guest, extending
  the file, the output device's capacity and the CLI are phase 7.
  Convert's fixed path is phase 8, and measure's is phase 9.
* **Dynamic VHDX's file length and BAT state** (decision 2). qemu
  extends a dynamic image to its data start and writes `ZERO` entries
  by default. instar stops at the end of the metadata region and leaves
  the BAT as a hole. After this phase those are the only two fields in
  which instar's dynamic `create` differs from qemu's.
* **A dynamic writer's payload start.** `VhdxLayout::data_start` keeps
  its meaning, the end of the metadata region, where convert's dynamic
  writer packs blocks and measure predicts it does.
* **Fuzzing the filler and the fixed planner.** Phase 11. This phase
  only keeps `fuzz_create_emitters` compiling.
* **Documentation.** No user-facing page describes the fields this
  phase changes, and fixed output is not reachable from the CLI
  until phase 7 (survey). Phase 12 owns `docs/format-internals.md`'s
  VHDX section. The changed output itself gets a `CHANGELOG.md` entry
  here, added at review (see Execution notes), because the change ships
  with this pull request rather than with phase 12.
* **Any call-table struct layout change.** Nothing here needs one.

## What the survey found

All references are to `origin/develop` at `f224e881`, built with
`make instar` in this worktree. qemu claims are cited to
`instar-testdata/qemu-sources/7.0.0/block/vhdx.c` or measured with the
pins in `tests/golden/qemu-create-pins.json`, which every one of the 80
static builds agrees on.

### instar's dynamic output differs from qemu's in five fields, not two

The master plan names two structural divergences for this phase: the
Required bit and the physical sector size. Running every dynamic pin
case through instar's `create` and `helpers.vhdx_structure.structural_diff`
gives five, on `vhdx-dynamic-64M-bsz-off`, `vhdx-dynamic-64M` and
`vhdx-dynamic-131041M-block-1M` alike:

| Field | instar | qemu | Where instar writes it |
|---|---|---|---|
| Region table Required, BAT and metadata, both tables | 1 | 0 | `build_region_table`, `src/crates/vhdx/src/lib.rs:3343`, `:3350` |
| Metadata item flags, Virtual Disk Size, Page 83, Logical and Physical Sector Size | 4 (IsRequired) | 6 (IsRequired, IsVirtualDisk) | `build_metadata`, `:3390-3423` |
| Metadata item offsets | FP 65536, VDS 65544, LSS 65552, PSS 65556, Page 83 65560 | FP 65536, VDS 65544, Page 83 65552, LSS 65568, PSS 65572 | `build_metadata`, `:3438-3468` |
| Physical sector size | 4096 | 512 | `PHYSICAL_SECTOR_SIZE`, `:3914` |
| File length | 4 MiB (end of metadata) | 8 MiB (qemu's data start) | `plan_vhdx`, `src/crates/create/src/lib.rs:1435` |

plus the BAT state at the default `block_state_zero` (instar
`NOT_PRESENT`, qemu `ZERO`), which open question 7 keeps.

qemu's values come from `vhdx_create_new_region_table`, which never
sets a region entry's `data_bits` (so Required is 0), and
`vhdx_create_new_metadata` (`:1568-1680`). The latter places File
Parameters, Virtual Disk Size, Page 83, Logical and Physical Sector
Size in that order from 64 KiB, flags File Parameters `IS_REQUIRED`
and the other four `IS_REQUIRED | IS_VIRTUAL_DISK` (`:1626-1661`),
and writes the logical sector size as the physical one (`:1611`).
`build_metadata`'s own comment says it writes "IsRequired |
IsVirtualDisk" and writes 0x04, which is IsRequired alone.

The comparator compares every one of these fields, so phase 7's
"structurally identical to qemu" criterion cannot hold for a fixed
image while the builders write instar's values.

### Other writers agree with qemu on two of the fields, not all

Two fixtures in `tests/manifest.json` were not written by qemu:
`vhdx-disk2vhd` (Microsoft Sysinternals Disk2VHD) and `qemu-vhdx`
(`iotest-dynamic-1G.vhdx`, a sample image from qemu's iotests whose
metadata region sits before its BAT in the file, which qemu's region
table builder never does). Parsed with the comparator:

| Field | Disk2VHD | iotest image | qemu | instar today |
|---|---|---|---|---|
| Required | 1 | 1 | 0 | 1 |
| Virtual-disk item flags | 6 | 6 | 6 | 4 |
| Page 83 item placement | after VDS | last | after VDS | last |
| Physical sector size | 512 | 4096 | 512 | 4096 |

So instar's metadata flags are wrong against every writer. instar's
Required bit matches both other writers and not qemu, and its physical
sector size matches the iotest image and not qemu or Disk2VHD. Decision
1 chooses qemu's values, because qemu is this plan's oracle, and says
why.

### The builders have three callers, and resize is one

`build_region_table` and `build_metadata` are called by `plan_vhdx`
(`src/crates/create/src/lib.rs:1461`, `:1470`) and `convert_to_vhdx`
(`src/operations/convert/src/main.rs:4546`, `:4616`). `resize` calls
`build_region_table` (`src/crates/resize/src/vhdx.rs:304`, `:311`)
when it moves the metadata region of an existing image, so after this
phase a resize writes Required 0 into a table that had 1. Nothing reads
the bit for the two regions every reader must know, and qemu-img
cannot resize a VHDX at all, so there is no oracle to match. The qcow2
crate's VHDX test fixture builder calls both
(`src/crates/qcow2/src/lib.rs:11405`, `:11412`) and is test-only.

### qemu's fixed layout, at the byte

From `vhdx_create_bat` (`:1690-1767`):

* The data region starts at `bat_offset + bat_region_length + 5 MiB`
  (`:1708`): 4 MiB past the end of the metadata region, "a 4 MB
  buffer for future expansion". At 64 MiB with 8 MiB blocks that is
  8 MiB. With a 17 MiB BAT (2 TiB at 1 MiB blocks) it is 24 MiB, and
  at 64 TiB with 1 MiB blocks (a 513 MiB BAT) it is 520 MiB.
  `VhdxLayout::data_start` is the end of the metadata region, 4 MiB
  earlier.
* A fixed file is truncated to `data_start + image_size` (`:1720`),
  where `image_size` is the size already rounded to 512. A dynamic
  file is truncated to `data_start` (`:1714`), which is the 8 MiB in
  the table above.
* Every payload block gets an entry by emulating one write per block
  (`:1745-1756`). The BAT index is `i + i >> chunk_ratio_bits`
  (`vhdx_block_translate`, `:1130`), so sector-bitmap slots are never
  written and stay zero. A `FULLY_PRESENT` entry's offset is
  `ROUND_UP(data_start + i * block_size, MiB)` (`:1749`), which is
  already MiB-aligned because both terms are. A `ZERO` entry's offset
  is forced to 0 (`vhdx_update_bat_table_entry`, `:1295`: Hyper-V
  refuses a non-zero FileOffsetMB on one).
* The state is `FULLY_PRESENT` for fixed, overridden to `ZERO` when
  `block_state_zero` is on (`:1741-1743`).
* The whole BAT region is written in one `blk_pwrite`, padding
  included. Entries past the last needed one are zero, which a hole
  reads as.

The pins agree: `vhdx-fixed-17M-bsz-off` is three `FULLY_PRESENT`
entries from 8388608 with stride 8388608, file length 26214400.
`vhdx-fixed-131041M-block-1M-bsz-off` is 31 repeats of 4096
`FULLY_PRESENT` entries plus one sector-bitmap slot, then 4065 more
entries from index 127007 at offset 133152374784, file length
137414836224. `vhdx-fixed-64M` and `vhdx-fixed-17M` are all `ZERO` at
offset 0. All four set `LeaveBlocksAllocated`. `vhdx-fixed-backing`
fails on every build ("Backing file not supported for file format
'vhdx'"): qemu cannot create any differencing VHDX.

### The filler belongs in the vhdx crate

`convert` links the vhdx crate but not the create crate
(`src/operations/convert/Cargo.toml`), and phase 8's fixed path must
use the same filler. qcow2's metadata preallocation is the precedent
for the split. The qcow2 crate computes a layout and fills one L2
table per call (`qcow2::create::compute_layout`, `build_l2_table`).
The guest loops over them, writes each through one reusable slot and
extends the file with a zero sector (`emit_qcow2_l2_and_extend`,
`src/operations/create/src/main.rs:1147-1224`).

### The crate tests cannot read the phase 2 fixtures

The master plan's phase 6 paragraph says crate tests compare the
planned bytes against the phase 2 fixtures. `make test-rust` mounts
only this repository into the container. instar-testdata is not there,
and no crate test reads it. The workspace has no JSON parser
either (no `serde_json` in `src/Cargo.lock`), so a test cannot read the
pin file directly. Phase 5's crate tests copied pin values into the
tests, and the management session cross-checked them against the pin
file. This phase does the same (decision 6). The phase 2 fixtures were
themselves checked against those pins, so the oracle is the same one.

### What the planner touches

`VhdxCreateOpts` (`src/crates/create/src/lib.rs:338`) is built in 14
places: the create op (`src/operations/create/src/main.rs:1059`), the
crate's unit tests, `src/crates/create/tests/round_trip.rs` and
`src/fuzz/fuzz_targets/fuzz_create_emitters.rs`. `plan_vhdx`'s body
cites `docs/plans/PLAN-differencing.md` at `:1507`, one of the 62 plan
references in `src` that #592 tracks.

### Found, and assigned elsewhere

* **Dynamic `create`'s file length.** qemu extends a dynamic image to
  its data start, 8 MiB at most sizes. instar stops at 4 MiB. No
  reader distinguishes the two, and matching it is a create-op change,
  not a crate one. Recorded with the dynamic `block_state_zero` entry
  in the master plan's Future work, since together they are what a
  byte-for-byte dynamic match would need.

### Master plan corrections made in this commit

* The phase 6 paragraph of "Sequencing rationale" now names all five
  builder divergences and says crate tests compare against the pins,
  not the fixtures. It also notes that the builder change reaches
  every VHDX writer, resize's region-table rewrite included, and that
  the filler lives in the vhdx crate because convert does not link
  the create crate.
* Future work's dynamic `block_state_zero` entry gains the file
  length.
* The Execution table and the `docs/plans/index.md` row link this
  file.

## Decisions

1. **The shared builders write qemu's structure, for every writer.**
   `build_region_table` writes Required 0 for both regions.
   `build_metadata` places the five items in qemu's order from 64 KiB
   (File Parameters 8 bytes, Virtual Disk Size 8, Page 83 16, Logical
   Sector Size 4, Physical Sector Size 4), lists them in the table in
   the same order, flags File Parameters IsRequired (0x4) and the other
   four IsRequired | IsVirtualDisk (0x6), and gains a
   `leave_blocks_allocated: bool` that sets File Parameters bit 0
   (`vhdx::FILE_PARAMETERS_LEAVE_BLOCKS_ALLOCATED`, added by phase 4).
   `PHYSICAL_SECTOR_SIZE` becomes 512, as qemu writes the logical size
   in both items. The parent locator item still goes where
   `build_metadata` says its items end.

   This is the decision a reviewer is most likely to dispute, on two
   counts:

   * **It changes the bytes of every VHDX instar writes,** dynamic
     included, in a phase the master plan calls crate-only. The
     builders are shared, and the alternative is a builder that writes
     Required 1 for a dynamic image and 0 for a fixed one, from the
     same tool. That is two structures with no reason between them.
     No reader instar or qemu ships behaves differently on any of
     these fields: Required only matters for a region the reader does
     not recognise, and BAT and metadata are the two every reader
     must. The flags fix is a correction in its own right: instar's
     4 matches no writer, and its comment says it writes 6.
   * **On Required and the physical sector size, qemu and the other
     writers disagree,** and this picks qemu. Both non-qemu fixtures
     write Required 1, and the iotest image a 4096-byte physical sector.
     This plan's success criteria define "correct" as structurally
     identical to `qemu-img create`, and instar's product is qemu-img
     parity. Keeping the other writers' values would mean every
     fixed-output test masks two fields for good, as
     `TestCreateVhdxPinLayout` does today. The cost is that a hypervisor
     that reports the image's physical sector size to its guest reports
     512 bytes for an instar VHDX, as it does for a qemu-written one.
     The back brief puts this choice to the operator before 6a starts.

2. **Dynamic output keeps its file length and its `NOT_PRESENT` BAT.**
   After 6a, `structural_diff` between instar's dynamic `create` and
   each dynamic pin must name `file_length` and nothing else for a
   `block_state_zero=off` pin. For a default pin, it may also name
   only the BAT runs that differ in state, `NOT_PRESENT` against
   `ZERO`. The tests assert exactly that, so a later divergence cannot
   hide behind the masks they carry today.

3. **qemu's fixed layout goes on `VhdxLayout`, beside `data_start`.**
   Add `fixed_data_start: u64`, which is `bat_offset + bat_region_len
   + 5 MiB`, and `fixed_file_length: u64`, which is `fixed_data_start
   + virtual_size` (the rounded size). Both use checked arithmetic and
   are computed for every layout. Their doc comments cite `:1708` and
   `:1720`, and say that qemu's dynamic file also ends at
   `fixed_data_start`, while instar's dynamic writers place payload
   from `data_start`. `data_start` keeps its meaning, so no phase 5
   consumer moves. Two fields with similar names invite the wrong
   one, so the doc comment on each names the other, and 6b adds a
   test that the two differ by exactly 4 MiB at every pin geometry.
   That test fails if either is ever redefined in terms of the other.
   At 64 TiB with 1 MiB blocks the fixed data start is 520 MiB and
   the file length is 64 TiB + 520 MiB, both well inside `u64`, and
   `FileOffsetMB`'s 44 bits.

4. **The filler.** In `src/crates/vhdx/src/lib.rs`:

   ```rust
   pub enum FixedBlockState { Zero, FullyPresent }
   pub enum BatFillError { UnalignedBuffer, PastRegion, Overflow }
   pub fn fill_fixed_bat(
       layout: &VhdxLayout,
       state: FixedBlockState,
       first_entry: u64,
       out: &mut [u8],
   ) -> Result<(), BatFillError>
   ```

   `out` holds `out.len() / 8` entries starting at BAT index
   `first_entry`. A length that is not a multiple of 8 is
   `UnalignedBuffer`. An entry at or past `bat_region_len / 8`, the
   region's capacity, is `PastRegion`. The function writes nothing in
   either case, so a caller cannot write a half-filled buffer. For each
   index `j`:

   * `j >= bat_entries` is padding: 0.
   * `(j + 1) % (chunk_ratio + 1) == 0` is a sector-bitmap slot: 0.
   * Otherwise it is payload block `p = j - j / (chunk_ratio + 1)`.
     `Zero` gives `2`, with offset 0. `FullyPresent` gives
     `(fixed_data_start + p * block_size) | 6`, an offset in bytes
     whose low 20 bits are zero, which is FileOffsetMB in bits 20-63.

   That is qemu's forward map `j = p + p / chunk_ratio` inverted,
   which a test checks for every `p` at a small chunk ratio. It is the
   rule the reader's BAT lookup applies (`block_index + block_index /
   chunk_ratio`, `src/crates/vhdx/src/lib.rs:2614`) and the
   comparator's sector-bitmap test, and 6c's round trip reads every
   filled entry back through that reader. The filler is for an image with
   no parent. The planner refuses fixed with a backing file, and the
   doc comment says so. Entries are written little-endian. The
   function is `no_std`, panic-free and allocation-free, and uses
   checked arithmetic throughout, as the crate requires. It does not
   compute the layout. The caller passes the one `layout` returned,
   so the BAT and the region table come from the same values.

5. **`plan_vhdx` gains the subformat, and returns what the guest
   streams.** `VhdxCreateOpts` gains `subformat: VhdxSubformat`
   (`Dynamic` or `Fixed`, a new enum beside `VhdSubformat`) and
   `block_state_zero: bool`. `block_state_zero` is read only for
   `Fixed`, and its doc comment says that dynamic output ignores it.
   `plan_vhdx` returns `Result<VhdxPlan<'a>, CreateError>`:

   ```rust
   pub struct VhdxPlan<'a> {
       pub metadata: MetadataPlan<'a>,
       pub fixed: Option<FixedVhdxPlan>,
   }
   pub struct FixedVhdxPlan {
       pub layout: vhdx::VhdxLayout,
       pub state: vhdx::FixedBlockState,
       /// Bytes of BAT that hold entries: bat_entries * 8. The rest of
       /// the region is padding, zero, and may stay a hole.
       pub bat_bytes: u64,
       /// The length the file must reach: layout.fixed_file_length.
       pub file_length: u64,
   }
   ```

   `Fixed` sets `LeaveBlocksAllocated` through `build_metadata`, and
   `Dynamic` never does. `Fixed` with `backing` is
   `BackingFileUnsupported`, the error `plan_vhd` gives a fixed VHD
   with a backing file, for the same reason. The metadata writes are
   the same for both subformats apart from that bit, so
   `metadata.minimum_file_size` stays the end of the metadata region.
   The round-trip harness enforces that invariant.

   One function returning both values, rather than qcow2's pattern of
   the guest calling `compute_layout` itself, is deliberate. The BAT
   the guest streams and the region table the plan writes must come
   from one call, which is the reasoning `plan_vhdx`'s own comments
   give for sizing the BAT by the same fact as `HasParent`. The cost is
   that every caller reads `.metadata`. That is the create op, the
   tests and the fuzz target, none of which changes behaviour.

6. **The oracle is the pin, copied into the tests and checked by the
   management session.** Crate tests encode, per pin case, every field
   the comparator compares. That is the region tables with Required,
   the metadata items' offsets, lengths and flags, block size, logical
   and physical sector size, virtual disk size, `LeaveBlocksAllocated`,
   `HasParent`, chunk ratio, BAT entry count and capacity, the BAT as
   runs, and the file length. Each constant names its pin case. The
   management session diffs the copied constants against
   `tests/golden/qemu-create-pins.json` before committing, as phase
   5's did.

   The cases are:

   * materialised in memory (the largest is 73 MiB) and parsed back
     through the vhdx crate's reader: `vhdx-fixed-16M`, `-17M`, `-64M`,
     `-65M`, `-64M-block-1M`, each with and without `-bsz-off`;
   * checked through the filler alone, never materialised:
     `vhdx-fixed-131041M-block-1M-bsz-off`, 2 TiB at 1 MiB blocks
     (fixed data start 24 MiB, from the Situation's measurement), and
     64 TiB at 1 MiB blocks (520 MiB, computed).

   A materialised image streams its BAT through two buffer sizes, 512
   bytes and 64 KiB, and the two must produce identical files. That
   catches an off-by-one at a buffer edge, which a single
   whole-region fill cannot.

7. **The mutants are kept, not run from scratch.** Each one below
   goes into `tools/mutate-differencing.sh` as a `rust_case` or
   `integration_case`, and the step that adds the code it breaks also
   adds the case:

   * Required back to 1, and a virtual-disk item's flags back to 4
     (6a).
   * The interleave divisor `chunk_ratio + 1` replaced by
     `chunk_ratio` (6b).
   * The 5 MiB dropped from `fixed_data_start` (6b).
   * A `ZERO` entry keeping the block offset (6b).
   * A sector-bitmap slot given a payload entry (6b).
   * Padding given an entry (6b).
   * `LeaveBlocksAllocated` set for dynamic, and cleared for fixed
     (6c).

   Phase 13's audit can then re-run every one. `docs/testing.md`'s
   case list and the script's enforced count move with them, as in
   phase 5's step 5e.

8. **No guest or host behaviour changes for fixed.** The create op
   passes `VhdxSubformat::Dynamic`, `block_state_zero: true` (unused),
   and reads `.metadata`. The fuzz target does the same, so it keeps
   compiling and fuzzing what it fuzzed. The host still refuses
   `subformat=fixed`. Fixed output stays reachable only through the
   crate until phase 7, so its integration tests are phase 7's. This
   phase's integration tests cover decision 1's change to dynamic
   output. The create, convert and resize binaries change only by the
   builder edits. `make check-binary-sizes` still runs after each
   step.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | high | opus | none | **Write qemu's VHDX structure from the shared builders.** First read decisions 1, 2 and 7 and the survey sections "instar's dynamic output differs from qemu's in five fields", "Other writers agree with qemu on two of the fields" and "The builders have three callers" in `docs/plans/PLAN-bat-population-phase-06-emitter.md`. In `src/crates/vhdx/src/lib.rs`: `build_region_table` (`:3326`) writes Required 0 for both entries. `build_metadata` (`:3365`) lays the items out in qemu's order and offsets with qemu's flags (decision 1, from `vhdx_create_new_metadata` in `instar-testdata/qemu-sources/7.0.0/block/vhdx.c`), fixes its flags comment, and gains `leave_blocks_allocated: bool` setting File Parameters bit 0 via `FILE_PARAMETERS_LEAVE_BLOCKS_ALLOCATED`. `PHYSICAL_SECTOR_SIZE` (`:3914`) becomes 512, and its doc comment says why: qemu writes the logical size as the physical one, and Disk2VHD and qemu's iotest image differ. Callers pass `false` for the new bit: `plan_vhdx` (`src/crates/create/src/lib.rs:1459`), `convert_to_vhdx` (`src/operations/convert/src/main.rs:4616`) and the qcow2 crate's test fixture builder (`src/crates/qcow2/src/lib.rs:11412`). Resize's `build_region_table` calls (`src/crates/resize/src/vhdx.rs:304`, `:311`) need no edit. Say in its comment that a resize writes Required 0 into the tables it rebuilds, and why that is harmless. Update every unit test that asserts the old values. Tighten `tests/test_create.py`'s `TestCreateVhdxPinLayout` and `TestCreateVhdxDefaultBlockSize` (`:1758-1860`) per decision 2. Drop `_region_layout`'s Required mask and the docstrings that call these "separate questions". Compare the full `structural_diff` and allow only `file_length`, plus, for a default pin, BAT runs that differ only in state (`NOT_PRESENT` against `ZERO`), and assert the allowed differences are present, so the test fails if either is fixed silently. Add a test that `instar convert -O vhdx` of a small raw image writes the same region-table Required, metadata items and physical sector size as the `vhdx-dynamic-64M-bsz-off` pin. Add the two decision 7 mutants for this step, Required 1 and flags 4, to `tools/mutate-differencing.sh`, run them, and update its case count and `docs/testing.md`'s case list. Because this changes the bytes of every VHDX instar writes, run the consumer suites, not only the create tests: `test_create`, `test_convert`, `test_dd_baselines`, `test_resize`, `test_differencing` (with libvhdi present and `INSTAR_REQUIRE_LIBVHDI=1` if the host has it; otherwise say so), `test_vhdx_structure`, `test_vhdx_fixed_fixtures`, `test_check_formats`, `test_info_safe`, `test_compare` and `test_measure`. Also confirm `qemu-img check` and `qemu-img info` are clean on a fresh instar dynamic VHDX and a converted one. Commit subject: `Write qemu's VHDX region and metadata structure.` |
| 6b | high | fable | none | **qemu's fixed layout and the streaming BAT filler.** First read decisions 3, 4, 6 and 7 and the survey section "qemu's fixed layout, at the byte" in `docs/plans/PLAN-bat-population-phase-06-emitter.md`. In `src/crates/vhdx/src/lib.rs`, add `fixed_data_start` and `fixed_file_length` to `VhdxLayout` (`:3981`), computed in `layout` (`:4064`) exactly as decision 3 says, with doc comments citing `vhdx_create_bat` (qemu 7.0.0, `block/vhdx.c`) by function name and naming each other. Add `FixedBlockState`, `BatFillError` and `fill_fixed_bat` exactly as decision 4 specifies. The BAT entry encoding is a u64 LE with the state in bits 0-2 and FileOffsetMB in bits 20-63, so the byte offset itself with its low 20 bits zero. `ZERO` is 2 with offset 0, `FULLY_PRESENT` is 6, and sector-bitmap and padding slots are 0. Payload block `p` sits at BAT index `p + p / chunk_ratio` (`vhdx_block_translate`). A `FULLY_PRESENT` offset is `fixed_data_start + p * block_size`. Every offset and count uses checked arithmetic, nothing panics, nothing allocates, and an error writes nothing. Unit tests: the four-way partition of indices (payload, sector bitmap, padding, past region) at a small chunk ratio, where the forward map `p -> p + p / c` and the filler agree for every `p`; fills that start mid-chunk and cross a sector-bitmap slot; a partial last block, whose entry is still a whole-block offset (the file ends inside it, as `vhdx-fixed-17M-bsz-off`'s pin shows); `UnalignedBuffer` and `PastRegion` leaving the buffer untouched; and `fixed_data_start - data_start == 4 MiB` at every pin geometry. Copy pin values in as decision 6 says and cite each case by name: `vhdx-fixed-17M-bsz-off`, `vhdx-fixed-64M`, `vhdx-fixed-131041M-block-1M-bsz-off` (check its run structure: 31 repeats of 4096 payload entries then one sector bitmap, then 4065 entries from index 127007 at 133152374784, file length 137414836224), 2 TiB at 1 MiB blocks (fixed data start 24 MiB) and 64 TiB at 1 MiB blocks (BAT region 513 MiB, fixed data start 520 MiB). Fill these through a 64 KiB buffer and sample the entries at each run edge. Do not materialise them. Add the five decision 7 mutants for this step as `rust_case`s, run them, and update the count and the case list. Commit subject: `Fill a fixed VHDX BAT one buffer at a time.` |
| 6c | high | opus | none | **A fixed subformat in `plan_vhdx`.** First read decisions 5 to 8 and the survey sections "What the planner touches" and "The filler belongs in the vhdx crate" in `docs/plans/PLAN-bat-population-phase-06-emitter.md`. In `src/crates/create/src/lib.rs`, add `VhdxSubformat`, the two `VhdxCreateOpts` fields (`:338`), `VhdxPlan` and `FixedVhdxPlan` exactly as decision 5 specifies. `plan_vhdx` (`:1381`) returns `VhdxPlan`, refuses `Fixed` with a backing file, and passes `leave_blocks_allocated` to `build_metadata` for `Fixed` only. While editing `plan_vhdx`, replace the `PLAN-differencing.md` citation in its body (`:1507`) with the reasoning it stands for (#592), and leave the other plan references in the file alone. Update every `VhdxCreateOpts` literal (14, survey), the create op's call (`src/operations/create/src/main.rs:1059`, which keeps building a dynamic image and reads `.metadata`), and `src/fuzz/fuzz_targets/fuzz_create_emitters.rs` (dynamic, unchanged behaviour). Build the fuzz targets with `make fuzz-build` to prove they compile. In `src/crates/create/tests/round_trip.rs`, add fixed round trips per decision 6. For each of the ten small pin cases, plan, materialise the metadata writes, and stream the BAT with `vhdx::fill_fixed_bat` through a 512-byte and then a 64 KiB buffer into a buffer extended to `file_length`. The two results must be byte-identical. Parse each back with the vhdx crate's reader (`VhdxState`, as the readers do) and assert every decision 6 field against constants copied from the pin. Also assert that a fixed plan's metadata writes equal a dynamic plan's except File Parameters bit 0, that `bat_bytes == bat_entries * 8`, and that `metadata.minimum_file_size` is the end of the metadata region. Add the two decision 7 mutants for this step, run them, and update the count and the case list. Commit subject: `Plan a fixed VHDX in the create crate.` |

The management session runs the steps in order. All three edit
`src/crates/vhdx/src/lib.rs` or the create crate, 6b's layout fields
come after 6a's builder change, and 6c consumes 6b's filler. Each step
runs a model at high effort. 6b runs on fable because its arithmetic is
the plan's highest-risk code. A plausible wrong BAT offset is invisible
to `qemu-img info`, and only a structural comparison catches it.

Every brief also carries the session's standing rules:

* Build only with `make instar` (Docker); never install a Rust
  toolchain on the host.
* Run `make test-rust`, redirecting cargo's target dir to an owned
  directory if pre-commit has left `src/target` root-owned. A
  `create` crate change needs `cargo test -p create` with its feature
  flags as the Makefile runs it.
* Do not run `make test-rust` at the same time as a mutation run.
* Run the touched test modules with stestr from `tests/`, with
  `INSTAR_BINARY_PATH` pointed at the worktree's build, and report
  "Ran: N". Replay any failure alone (`--concurrency 1`) before
  attributing it.
* Run `pre-commit run --all-files` after `git add -N` on new files,
  and `tools/ci/check-test-partition.sh`.
* Keep plan, phase, step and decision references out of code and
  docs.
* Keep a mutation script under the step's scratch directory, and run
  each mutant under `timeout`.
* Every qemu behaviour a comment or doc states is cited to
  `instar-testdata/qemu-sources/7.0.0/` by function name, or was
  measured. Say which.
* Tests stay under ext4's 16 TiB file-size ceiling by staying sparse:
  no test allocates a data region.
* Leave the work uncommitted.

## Execution notes

* **Back brief, 2026-10-29 (AEDT).** The operator kept decision 1 as
  recommended, qemu's values for both fields: Required 0 and 512-byte
  physical sectors. They kept fable for 6b, and allowed the management
  session to push the branch and open the pull request once the
  definition of done passes. The steps run in order.
* **6a.** Two changes beyond the brief, both kept:
  * `convert_to_vhdx` wrote the metadata item data by hand, in the old
    order, through a sector of its own (its buffer ends where the item
    data starts). Changing only the table would have pointed its
    entries at the wrong bytes, so the agent split the item writer out
    as `vhdx::build_metadata_items`, which `build_metadata` and convert
    now share. Both place items from one table, so the table and the
    data cannot disagree.
  * `round_trip.rs`'s `GOLDEN_VHDX_NO_BACKING` pins every byte
    `plan_vhdx` writes, so the region-table and metadata hashes (`w3`
    to `w5`) were regenerated. Every offset, length and count, and the
    `w0` to `w2` hashes, are unchanged, and the comment says which
    columns still compare with the old `develop`.

  The management session reproduced the definition of done's diff
  script (it prints nothing) and `qemu-img check` and `compare` on a
  fresh dynamic create and a convert of a raw image with data in two
  blocks (both clean, compare identical). It also confirmed that the
  converted image carries Required 0, 512-byte physical sectors and
  Page 83 at 65552, flagged 6. `instar resize` to 2T, which rebuilds
  the region tables, passed `qemu-img check` (agent). The stestr run
  of the eleven modules ran 2684: 2258 passed, 426 skipped, 0 failed.
  The skips are the 324 measure baselines of #845, the known measure,
  resize, create and info divergences, "qemu-img cannot resize", raw
  having no check, ZSTD, and seven `test_differencing` cases that
  need libvhdi, which this host lacks. `make test-rust` passed 2762.
* **6b**, on fable at high effort. Two changes to decision 4, both kept:
  * `BatFillError` has a fourth variant, `DegenerateChunkRatio`, for a
    hand-built layout with a chunk ratio of 0. `layout` never makes one,
    and without the variant such a layout would get an all-zero BAT
    with no error, or an `Overflow` that misnames the problem.
  * Every error is decided before the first byte is written, not just
    the two the decision names. An upfront bound, `bat_entries *
    block_size + fixed_data_start`, covers every offset the loop can
    compute.

  The pin test covers seven geometries where the decision listed five.
  It adds `vhdx-fixed-17M` and `vhdx-fixed-64M-block-1M-bsz-off`, so
  ZERO is checked at a partial block and FULLY_PRESENT at an explicit
  1 MiB block size. The management session diffed every copied
  constant against `tests/golden/qemu-create-pins.json`: all five pin
  cases match in every field and BAT run, including 131041M's 31
  repeats. The two computed geometries agree with the plan: 2 TiB
  gives a 17 MiB region with data at 24 MiB, and 64 TiB a 513 MiB
  region with data at 520 MiB. The management session also reran
  `vhdx-write-fixed-bat-interleaved-by-chunk-ratio` (PASS, source
  restored byte for byte) and `--check-patterns` (137 land).
  `make test-rust` passed 2769.
* **6c.** Three departures, all kept:
  * A fixed VHDX with any backing reference is refused before its path
    is examined. A path the dynamic arm refuses for its own reason (too
    long, not UTF-8) gets `BackingFileUnsupported` too, because qemu-img
    refuses every VHDX backing file.
  * `VhdxCreateOpts` has 15 literals, not the survey's 14.
    `src/crates/resize/tests/vhdx_grow.rs` holds the fifteenth.
  * `round_trip.rs` carries its own stub `CallTable` to read images
    back through `VhdxState`, as the dmg, vdi, vhdx, parallels and
    qcow1 crates' tests each do. `shared` has no shared one.

  The replacement for `plan_vhdx`'s `PLAN-differencing.md` citation
  states the reasoning that plan's phase 1 pin records: Hyper-V's
  locator also carries `volume_path`, which needs a Windows volume
  GUID, and `parent_linkage2`. The management session checked both
  claims against that file. A dynamic `create` at 64M, 1G and 131041M
  with 1 MiB blocks is byte-identical to the one built at `dab31e4d`
  (`cmp`, agent). The baseline had to be rebuilt from `git archive`,
  because the create op is `create.bin` beside the host binary, and
  `make instar` overwrites it. `create.bin` grew from 58480 to 59024
  bytes. `make test-rust` passed 2781, and `make fuzz-build` passes.
* **Definition of done, 2026-10-29 (AEDT).** Checked by the management
  session on a fresh `make instar` at `e491c7b8`. Every item passes:
  * The structural diff script prints nothing.
  * The Required and 4096-byte grep checks print nothing, and the API
    greps print three lines each.
  * `make test-rust` passed 2781, with 3 ignored, and `make
    check-binary-sizes` and `make fuzz-build` pass.
  * The stestr run of the eleven modules at 8 workers ran 2684: 2251
    passed, 426 skipped and 7 failed. The 7 were qcow2 compressed
    re-encodes in `test_convert`, and one compressed raw case, none
    touching VHDX, at a load average of about 9. All 7 passed when
    replayed alone (`--concurrency 1`, Ran: 7, 0 failed), so they were
    host contention. The skips are those 6a lists.
  * All nine new mutation cases PASS, and `--check-patterns` reports
    139 patterns land.
  * `tools/ci/check-test-partition.sh` exits 0, the plan-reference
    grep over the diff prints nothing, and `pre-commit run --all-files`
    passes.
  * `qemu-img check` is clean on `instar create -f vhdx t.vhdx 64M`
    and on a convert of a 64 MiB raw image with data in two blocks.
* **Review round 1, #853.** 0 fix, 1 document, 5 consider, 2 none.
  * **Taken:**
    * The document item: a `CHANGELOG.md` entry for the changed output,
      since it ships with this pull request. Phase 5 set the precedent
      with its own entry for #645.
    * `build_metadata_items` now takes `&mut [u8; METADATA_ITEMS_LEN]`,
      so a short buffer cannot reach it. Convert takes the items from
      its sector with `first_chunk_mut` and fails the conversion if the
      sector were ever too short.
    * The Page 83 comment now says that two images of the same size and
      block size share the identifier, which #566 tracks.
    * Three ragged paragraphs reflowed.
  * **Declined:**
    * The round-trip test's peak memory. About 300 MB is ordinary for
      this suite's hosts, and the test is correct.
    * The `fixed,` mutation anchor. The script already refuses a search
      string that does not occur exactly once, so an ambiguous anchor
      reports BROKEN rather than mutating the wrong line.

## Risks and mitigations

* **6a breaks a reader that depends on instar's old values.** Nothing
  in instar should read the Required bit, the flags or the item order
  of a region and item it knows. If something does, the consumer
  suites in 6a's brief find it. The management session also runs
  `qemu-img check`, `qemu-img info` and `instar check` on a fresh
  dynamic create and convert, and on a VHDX that `instar resize`
  grew.
* **6a's change is wider than it looks.** It reaches create, convert,
  dd and resize output. The management session diffs `instar create
  -f vhdx` at 64M, 1G and 131041M with `block_size=1M` against a build
  of `origin/develop`. Only the region tables, the metadata table and
  items, and their checksums may differ.
* **A wrong BAT offset that looks plausible.** It would pass `qemu-img
  info` and fail nothing until a reader followed it. The mitigations
  are the pin constants (decision 6), the forward-map test, the two
  buffer sizes, and the mutants (decision 7). The management session
  checks the copied constants against the pin file itself.
* **The two data starts get confused.** That is decision 3's test, and
  each doc comment names the other. Phase 7's brief must say which
  one the guest extends to.
* **The return-type change hides a behaviour change.** The create op
  and fuzz target must behave exactly as before. The management
  session diffs a dynamic `instar create -f vhdx` after 6c against
  the one after 6a, byte for byte.
* **Guest binaries grow.** `make check-binary-sizes` after each step.
  The filler is not linked into a guest op until phase 7.
* **Host contention produces failures that look like regressions.**
  Replay any failure alone (`--concurrency 1`) before attributing it,
  as phases 4 and 5 did.

## Definition of done

Each item is a command, run from the worktree root with the
worktree's build, and its expected result.

* From `tests/`, this prints nothing:

  ```bash
  python3 -I - "$INSTAR_BINARY_PATH" <<'EOF'
  import sys, tempfile
  from pathlib import Path
  sys.path.insert(0, '.')
  from helpers import qemu_pins, vhdx_structure as v
  # Each dynamic pin, and whether qemu wrote ZERO entries (block_state_zero on).
  for case, zero in (('vhdx-dynamic-64M-bsz-off', False), ('vhdx-dynamic-64M', True),
                     ('vhdx-dynamic-131041M-block-1M', True)):
      with tempfile.TemporaryDirectory() as td:
          got = qemu_pins.run_case(sys.argv[1], case, Path(td))
      for d in v.structural_diff(got['summary'], qemu_pins.expected(case)['summary']):
          state_only = zero and ((d.startswith('bat run ') and 'NOT_PRESENT' in d and 'ZERO' in d)
                                 or (d.startswith('bat: ') and d.endswith('more differing runs')))
          if not (d.startswith('file_length:') or state_only):
              print(case, d)
  EOF
  ```

* `git grep -n 'e + 28, 1)' -- src/crates/vhdx` prints nothing.
* `git grep -n 'PHYSICAL_SECTOR_SIZE: u32 = 4096' -- src` prints
  nothing.
* `git grep -n 'pub fn fill_fixed_bat\|pub fixed_data_start\|pub fixed_file_length' -- src/crates/vhdx`
  prints three lines, and `git grep -n 'pub struct VhdxPlan\|pub struct FixedVhdxPlan\|pub enum VhdxSubformat' -- src/crates/create`
  prints three.
* `make test-rust`, `make check-binary-sizes` and `make fuzz-build`
  pass.
* The stestr run of `test_create`, `test_convert`, `test_dd_baselines`,
  `test_resize`, `test_differencing`, `test_vhdx_structure`,
  `test_vhdx_fixed_fixtures`, `test_check_formats`, `test_info_safe`,
  `test_compare` and `test_measure` reports "Ran: N" equal to the tests
  in those modules, with 0 failures. The execution notes list every
  skip by reason.
* `bash tools/mutate-differencing.sh` with each of the nine new case
  names reports PASS, and `--check-patterns` passes.
* `tools/ci/check-test-partition.sh` exits 0.
* `git diff origin/develop...HEAD -- src tests docs/*.md scripts tools | grep -nE '^\+.*(PLAN-|[Pp]hase [0-9]|step 6[a-c])'`
  prints nothing.
* `git add -A && pre-commit run --all-files` passes.
* `qemu-img check` exits 0 on `instar create -f vhdx t.vhdx 64M`, and
  on `instar convert -O vhdx` of a 64 MiB raw image with data in two
  blocks.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase and how the intended work aligns with it.
Put decision 1 to the operator before 6a starts. It changes the bytes
of every VHDX instar writes, and it chooses qemu over the other writers for
the Required bit and the physical sector size. The alternatives are
qemu's values for both (the plan's recommendation), the other writers' for
both, with the comparison masking those two fields for good, or qemu's
Required bit with 4096-byte physical sectors. 6b and 6c do not depend
on the answer, but 6a's tests and every later phase's comparisons do.
Also confirm fable for 6b, or name the model to use instead.
