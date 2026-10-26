# Phase 4: read side -- BAT entry count, partial last block, fixed VHDX resize

Phase 4 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation (especially "Reading fixed VHDX: mostly right, with three
defects") and the phase 4 paragraph of "Sequencing rationale" first.
This file assumes them, and corrects that paragraph where the survey
found it incomplete.

Planning effort: high, as the master plan sets for this phase. Every
change here alters what instar accepts from an untrusted image, and the
survey found that qemu's own partial-last-block rule is wrong in a way
instar must deliberately not copy (decision 3).

## Decisions already taken by the operator

Every open question in the master plan was resolved with its
recommendation adopted (Michael Still, 2026-10-21). The two this phase
carries out:

* **Open question 5:** close #626 now, and #797 and #798 (filed during
  master planning) with it.
* **Open question 6:** `resize` refuses a fixed VHDX with a typed error
  naming the subformat, until a fixed-preserving grow exists. SPEC(VHDX)
  2.6.2.1 (revision 8.0) bears it out: with LeaveBlockAllocated set "the
  implementation SHOULD NOT change the state of any blocks to a
  BLOCK_NOT_PRESENT state".

Neither is revisited here.

## Scope

In:

* **#626.** One non-differencing BAT entry count, qemu's and
  SPEC(VHDX) 2.5's, defined once in the vhdx crate and used by every
  reader and writer that counts today.
* **#797.** `check` accepts a final `FULLY_PRESENT` payload block that
  ends at the end of the virtual disk, short of a whole block.
* **#798.** The vhdx crate parses `LeaveBlocksAllocated`, and `resize`
  refuses a fixed VHDX by name instead of growing it into a hybrid.
* Tests that turn phase 2's `KNOWN_READ_FAILURES` entries into passing
  reads, and the per-phase documentation: `docs/check.md`,
  `docs/resize.md`, `docs/quirks.md` and `docs/testing.md`.

Out:

* **One shared VHDX layout, the chunk-ratio formula, #645 and the
  64 TiB ceiling.** Phase 5. This phase only folds the entry count.
  The chunk ratio is computed in three places as well, but phase 5
  replaces all of it with one layout function, so unifying it here
  would be work phase 5 redoes.
* **The region table's Required bit.** instar writes 1 for both
  regions and qemu writes 0 (survey). It is a writer-structure
  question, for phase 6.
* **The measure quirk that states the VHDX-source divergence the
  wrong way round** (survey). Phase 9 owns measure.
* **#582** (`VhdxState::init` discards the active header). The master
  plan made folding it in optional. Nothing here reads the active
  header, so it stays separate.
* **`check` policing `LeaveBlocksAllocated`** (for example flagging a
  fixed image with `NOT_PRESENT` entries). qemu does not, and the
  spec's wording is SHOULD NOT, not MUST NOT.
* **Any call-table struct layout change.** The new resize error code
  is appended to `ResizeResult`'s code list, which is not a layout
  change (decision 5).

## What the survey found

Line numbers are against `develop` at `a8d2f4ca`. Measurements used
the host's qemu-img 10.0.13, the 80 static builds under
`instar-testdata/qemu-img-binaries/x86_64/`, and an instar built on
2026-10-19 from the primary clone. No commit since then touches the
VHDX read code, the check op or the measure crate's VHDX code
(`git log --since=2026-10-19` over those paths shows only `3943c781`,
which is qcow2 data-file documentation).

### #626 is as the master plan says, and has a fourth copy

The non-differencing count `total_blocks + ceil(total_blocks /
chunk_ratio)` is written out in:

* `calculate_bat_layout` (`src/crates/vhdx/src/lib.rs:3758-3780`),
  which create (`src/crates/create/src/lib.rs:1416`), convert
  (`src/operations/convert/src/main.rs:4415`), measure
  (`src/crates/measure/src/lib.rs:1033`) and resize
  (`src/crates/resize/src/vhdx.rs:91`) all call;
* `VhdxState::init` (`src/crates/vhdx/src/lib.rs:2325-2348`);
* the check op (`src/operations/check/src/main.rs:1677-1688`);
* and, as a deliberately independent oracle, the qcow2 crate's VHDX
  test fixture builder (`src/crates/qcow2/src/lib.rs:11371-11376`),
  whose comment at `:13552-13562` also describes `VhdxState::init`'s
  count as `total_blocks + sb_entries`.

qemu's rule is `data_blocks_cnt + ((data_blocks_cnt - 1) >>
chunk_ratio_bits)` (`block/vhdx.c:868-882` in
`instar-testdata/qemu-sources/7.0.0/`). The Python comparator already
uses it (`tests/helpers/vhdx_structure.py:301-309`).

Re-measured at `a8d2f4ca` on the committed
`vhdx-dynamic-131041M-block-1M` fixture: `instar info` exits 0, and
`check` (2), `map` (1), `measure -O qcow2` (1, "source image is
unsupported format") and `convert -O qcow2` (1) all fail. `qemu-img
check` is clean.

The writer side is measured too. `instar create -f vhdx -o block_size=1M
d.vhdx 131041M` declares a 2 MiB BAT region at 2 MiB and metadata at
4 MiB. qemu's pin case `vhdx-dynamic-131041M-block-1M` declares a 1 MiB
BAT at 2 MiB and metadata at 3 MiB. After the fix the offsets and
lengths should agree.

Resize is less affected than the master plan implied. It takes the
current entry count from the region's capacity (`bat_len / 8`,
`src/operations/resize/src/main.rs:822`), not from the rule, so only
its target count moves. A grow to a boundary geometry that relocates
the BAT today will fit in place after the fix.

Shrinking the count also shrinks `sb_bat_entry_bound` for an image with
no parent. That changes nothing, because `block_lookup` already refuses
`PARTIALLY_PRESENT` when there is no parent
(`src/crates/vhdx/src/lib.rs:2583-2600`), so `sector_bitmap_lookup` is
never reached for such an image.

### qemu's partial-last-block allowance is wrong past the first chunk

`vhdx_check_bat_entries` (`block/vhdx.c:884-960`) computes the length
of block `i` as `MIN(block_size, virtual_size - i * block_size)`. `i` is
the **BAT** index, which counts sector-bitmap slots, and not the payload
index. The two are equal only inside the first chunk. Past it,
`i * block_size` exceeds the virtual size, so the unsigned subtraction
wraps, `MIN` picks `block_size`, and qemu requires the whole block to be
in the file. The check runs on every open (`:1082-1087`), so qemu then
refuses to open the image.

Measured: `qemu-img create -f vhdx -o
subformat=fixed,block_size=1M,block_state_zero=off f.vhdx 4608.5M`
succeeds on **all 80 static builds** (6.0.0 to 10.2.0). On every one of
them, `qemu-img info f.vhdx` then exits 1 and `qemu-img check` exits 2:
"VHDX BAT entry 4609 end offset 4841275391 points after end of file
(4840751104)". Entry 4609 is payload block 4608 plus one sector-bitmap
slot. At 2048.5M, inside the first 4096-block chunk, and at 4097M and
4609M, which have no partial block, both tools are clean. With
`block_state_zero` on, every entry is `ZERO`, the check never fires, and
qemu opens the image.

So qemu writes, by default and with no error, fixed images that it
cannot open again. Decision 3 settles what instar does with them. The
same images are what phase 7's `create` and phase 8's in-place `convert`
will write for parity, so the master plan's phase 7 and 8 paragraphs
now carry a warning that `qemu-img` cannot be the oracle for them.

### check's whole-block assumption is in three places, not one

The `FULLY_PRESENT` arm (`src/operations/check/src/main.rs:1771-1903`)
uses `file_offset + block_size` for the bounds test (`:1784-1793`), the
declared-region overlap test (`:1847-1863`), and `max_block_offset`
(`:1866-1868`), which becomes `image_end_offset` (`:1931-1933`). On the
17M fixture today, `image-end-offset` is 25165824 (24 MiB). That is
only because the partial block takes the `continue` at `:1792` and is
left out. If only the bounds test were relaxed, the field would report
32 MiB, past the 25 MiB end of file. `qemu-img check --output=json`
reports no `image-end-offset` for VHDX at all. That is an existing
divergence and stays as it is.

### LeaveBlocksAllocated is unparsed, and resize is its only writer

`FILE_PARAMETERS_HAS_PARENT` (`src/crates/vhdx/src/lib.rs:1145`) is the
only File Parameters bit the crate reads. It reads it in two parsers:
`parse_metadata_region` (`:1262-1264`, building `VhdxMetadataItems` at
`:1276`) and `parse_metadata` (`:1352-1353`, building `VhdxMetadata` at
`:1443`). Bit 0 is `LeaveBlocksAllocated` (qemu `block/vhdx.h:275`).

Of the operations that handle VHDX, only `convert`, `create`, `resize`,
`rebase` and `bench` write. `convert` and `create` write new files.
`rebase` reads VHDX only as a chain member
(`src/operations/rebase/src/main.rs:1846-1902`), and `bench` writes to
its own scratch. `resize` is the only operation that modifies an
existing VHDX, so it is the only consumer of the bit this phase needs.

resize refuses a differencing VHDX with `UnsupportedSubformat`
(`src/crates/resize/src/vhdx.rs:52-54`). The host renders that as the
generic "subformat does not support resize"
(`src/vmm/src/main.rs:8548`), which does not name the subformat.
`BitmapsPresent` is the precedent for a refusal with its own code:
`ResizeError::BitmapsPresent` (`src/crates/resize/src/lib.rs:91`), then
`ResizeResult::ERROR_BITMAPS_UNSUPPORTED = 14`
(`src/shared/src/lib.rs:3937`, pinned at `:5639`), then the host
constant (`src/vmm/src/main.rs:309`), message (`:8567`) and a code-range
test (`:20740`). `VhdxResizeOpts` (`src/crates/resize/src/lib.rs:504`) is
built in two places: the op (`src/operations/resize/src/main.rs:824-845`)
and `src/crates/resize/tests/vhdx_grow.rs`. The fuzz target
`fuzz_resize_planners.rs:288-319` also builds it, with its flags taken
from a fuzzed byte (`has_parent` is bit 7).

### Tests and documentation that name these defects

* `tests/test_vhdx_fixed_fixtures.py:122-130` lists six #626 pairs and
  one #797 pair in `KNOWN_READ_FAILURES`. Each fails with "remove this
  entry" once fixed. `METADATA_OPS` (`:97`) is info, check and map.
  Measure is not in the matrix, although the master plan's success
  criteria name it.
* `docs/testing.md:2723` describes the table as listing #626 and #797.
* `docs/check.md:103-104` says BAT entries are validated for "block
  offsets within file bounds".
* `docs/resize.md:62` describes vhdx resize as "dynamic". `:215-220`
  is the differencing refusal bullet to put the fixed refusal beside.
  `:210-213` still says multi-file VMDK resize needs "the same
  multi-output-device call-table extension that create's roadmap
  already calls out". Phase 3 removed that from create's roadmap
  because monolithicFlat create needs no such extension, so the
  sentence is stale.

### Found, and assigned elsewhere

* **The region table's Required bit.** `build_region_table`
  (`src/crates/vhdx/src/lib.rs:3268`, `:3275`) writes `required = 1` for
  the BAT and metadata regions. Every pinned qemu build writes 0
  (`tests/golden/qemu-create-pins.json`, every VHDX case). The master
  plan's first success criterion compares region tables, so phase 6
  has to decide this. Recorded in the master plan's phase 6 paragraph.
* **`docs/quirks.md:918` has the measure divergence backwards.** It
  says "VHDX sources: instar treats every BAT block as fully
  allocated". Measured on `vhdx-fixed-16M` (all `ZERO` entries):
  `qemu-img measure -O qcow2` reports `required` 17104896, which is
  everything, because the vhdx driver reports no block status. instar
  reports 327680, because it counts no `ZERO` block. On the `bsz-off`
  fixtures the two agree. Recorded in the master plan for phase 9.

### Master plan corrections made in this commit

* The phase 4 paragraph of "Sequencing rationale" now records qemu's
  BAT-index defect and this phase's answer to it, the fourth count
  site, and `measure` joining the read matrix.
* The phase 6 paragraph gains the Required bit. The phase 7 and 8
  paragraphs gain the warning that qemu cannot open some of the parity
  images they write. The phase 9 paragraph gains the backwards quirk.
* "Bugs fixed during this work" gains these findings, and the three
  issues phase 3 filed (#821, #822, #823).
* The Execution table and the `docs/plans/index.md` row link this
  file.

## Decisions

1. **One count function, in the vhdx crate, replacing every
   non-differencing copy.** Add `pub fn bat_entry_count(
   total_payload_blocks: u64, chunk_ratio: u32, has_parent: bool) ->
   Option<u64>` beside `differencing_bat_entry_count`
   (`src/crates/vhdx/src/lib.rs:1912`), which it calls when `has_parent`
   is set. With no parent it returns:
   * `0` for zero payload blocks;
   * the payload count for a zero `chunk_ratio`, as
     `calculate_bat_layout` does today. Every reader refuses a zero
     chunk ratio before counting, so this only keeps a total function
     total;
   * otherwise `n + (n - 1) / chunk_ratio`.

   It uses checked arithmetic throughout. Its doc comment quotes
   SPEC(VHDX) 2.5 ("Total BAT Entries = Payload Blocks Count +
   Fl((Payload Blocks Count - 1) / Chunk Ratio)") and cites
   `block/vhdx.c:868-882`. It also states the identity that made #626
   easy to misread: `ceil(n / c) == floor((n - 1) / c) + 1`, so the old
   rule was one entry over at **every** geometry, and the excess only
   showed when it crossed a 1 MiB region boundary.

   `calculate_bat_layout`, `VhdxState::init` and the check op call it.
   The qcow2 crate's fixture builder keeps its own arithmetic, because
   its comment says it is an independent oracle, but switches to
   qemu's rule, and its stale comment at `:13552-13562` is corrected.

2. **The writers change at boundary geometries, and the tests say
   so.** `calculate_bat_layout` sizes the BAT region for create,
   convert, measure and resize, so at a geometry like 131041 MiB with
   1 MiB blocks each now writes or predicts a region 1 MiB shorter.
   That is the point of the fix: qemu's layout. The reader bound only
   loosens, so every VHDX instar wrote before still opens, including
   one with the old, longer region. Unit tests pin the boundary in the
   vhdx crate (131041 blocks at a chunk ratio of 4096 gives 131072
   entries, exactly 1 MiB), in `plan_vhdx`, in `measure_vhdx`, and in
   resize's `decide_action`: a grow from 131040M to 131041M at 1 MiB
   blocks no longer relocates. One integration test creates
   `-o block_size=1M` at 131041M and compares the region table's
   offsets and lengths with the pin case's, but not the Required bit,
   which is phase 6's.

3. **A partial last block is judged by its payload index, which is
   what qemu means, not by its BAT index, which is what qemu does.**
   The check op computes each `FULLY_PRESENT` block's length as
   `min(block_size, virtual_disk_size - payload_block_idx *
   block_size)`, with checked arithmetic, and uses that length for the
   bounds test, the declared-region overlap test and
   `max_block_offset`. A block whose start is at or past end of file
   is still a corruption. So is a block that falls short of its own
   length, and only the last block can be short.

   This is the decision a reviewer is most likely to dispute, because
   it makes `instar check` accept images that `qemu-img check` calls
   truncated: fixed `block_state_zero=off` images whose partial last
   block lies past the first chunk. Copying qemu would mean calling
   qemu's own default output corrupt, although no reader has trouble
   with it: the bytes the virtual disk needs are all in the file. It
   would also mean phase 7 writing images that instar's own `check`
   then fails. qemu's comment ("Allow that the last block exists only
   partially") says what it intends, and only the arithmetic falls
   short. `docs/quirks.md` records the divergence, with the
   measurement, so that the next reader of a `qemu-img check` failure
   on such an image finds it.

   Reporting the qemu defect upstream is outside this repository and
   is the operator's call. This phase does not do it.

4. **`LeaveBlocksAllocated` is parsed in both metadata parsers, and
   used only by resize.** Add `FILE_PARAMETERS_LEAVE_BLOCKS_ALLOCATED:
   u32 = 1 << 0`, documented from SPEC(VHDX) 2.6.2.1 and
   `block/vhdx.h:275`. Add `leave_blocks_allocated: bool` to
   `VhdxMetadata` and `VhdxMetadataItems`, set by `parse_metadata` and
   `parse_metadata_region` alike, so the two parsers cannot disagree.
   It does not go on `VhdxState`, which nothing here needs. `info` does
   not surface it, because `qemu-img info` does not, and `check` does
   not police it (Scope).

5. **resize refuses a fixed VHDX with its own error code.**
   * Add `ResizeError::FixedVhdx`.
   * Add `VhdxResizeOpts::leave_blocks_allocated`, set from the parsed
     metadata.
   * In `plan_grow`, refuse right after the `has_parent` refusal, so
     that a same-size request and a shrink get this refusal too. Both
     are refused by `qemu-img resize` for any VHDX.
   * Append `ResizeResult::ERROR_FIXED_VHDX = 15`, mapped in the op.
   * On the host, add the constant and a message such as "refusing to
     resize a fixed VHDX: a fixed image keeps every block allocated,
     and growing one would add blocks that are not; convert it to a
     dynamic VHDX to resize it". Extend the code-range test to 15.

   Reusing `UnsupportedSubformat` would be smaller, but its message is
   exactly what open question 6 asked to improve on. Appending a code
   is the pattern `ERROR_BITMAPS_UNSUPPORTED` set, and it changes no
   struct layout. The fuzz target sets the new field from a free flag
   bit, so the refusal is fuzzed rather than always off.

6. **The phase 2 read matrix is the integration oracle, and grows.**
   * Each fix removes its `KNOWN_READ_FAILURES` entries. The table and
     its "remove this entry" mechanism stay, empty, for the next
     regression.
   * `measure` joins `METADATA_OPS`, as `_op_measure`: `instar measure
     -O qcow2 --output json` must exit 0, and its `fully-allocated`
     must equal `qemu-img measure -f vhdx -O qcow2 --output=json`'s.
     `required` is not compared, because of the divergence the survey
     found.
   * The dynamic #626 fixture gains a convert test: `instar convert
     -O qcow2` exits 0, and `qemu-img info` of the output reports the
     same virtual size. Every entry is `ZERO`, so this walks the BAT
     without reading 128 GiB. `compare` at the #626 geometry is not
     added, because both tools would read the whole virtual disk.
     `compare` goes through the same `VhdxState::init` that `convert`
     does, and it is covered on the partial-block fixtures.
   * A runtime image `vhdx-fixed-4608.5M-block-1M-bsz-off` joins
     `RUNTIME_CREATE_IMAGES` with the metadata operations. instar's
     `info`, `check`, `map` and `measure` must all pass on it. A
     separate test pins that the host `qemu-img check` exits 2 on it,
     so that a qemu fix shows up as a test failure that points at the
     quirk text.
   * Negative cases, on copies of `vhdx-fixed-17M-bsz-off-data`:
     truncate it 512 KiB into the partial block, and truncate it to the
     partial block's start. `instar check` must exit 2 on both.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | high | opus | none | **One VHDX BAT entry count, qemu's (#626).** First read decisions 1, 2 and 6 and the "#626" survey section of `docs/plans/PLAN-bat-population-phase-04-read-side.md`. In `src/crates/vhdx/src/lib.rs`, add `bat_entry_count` beside `differencing_bat_entry_count` (`:1912`) exactly as decision 1 specifies: no-parent rule `n + (n - 1) / chunk_ratio`, `0` for `n == 0`, `n` for a zero chunk ratio, the differencing arm calling the existing function, all arithmetic checked, and a doc comment quoting SPEC(VHDX) 2.5, citing `block/vhdx.c:868-882` (qemu 7.0.0, under `instar-testdata/qemu-sources/`) and stating the `ceil(n/c) == floor((n-1)/c) + 1` identity. Use it in `calculate_bat_layout` (`:3758-3780`, and correct its doc comment, which describes the old rule as "the sector bitmap entry that ends each group those blocks reach into"), in `VhdxState::init` (`:2325-2348`; keep the region-size check on the non-padded count and keep its comment's reasoning) and in the check op (`src/operations/check/src/main.rs:1677-1688`). The crate is `no_std` and panic-free. Unit tests in the vhdx crate: `bat_entry_count` at `n` = 0, 1, `c`, `c + 1`, `2c`, `2c + 1`, and 131041 at `c` = 4096 (giving 131072, while the old rule gave 131073), each with and without a parent; then update the existing `calculate_bat_layout` expectations (`:3931-4044`) to the new values, with comments showing the arithmetic. Also add: a `plan_vhdx` test in `src/crates/create` at 131041 MiB with 1 MiB blocks asserting a 1 MiB BAT region with metadata at 3 MiB; a `measure_vhdx` test at the same geometry; and a resize `decide_action` / `plan_grow` test asserting that 131040M to 131041M at 1 MiB blocks with a 1 MiB BAT region is `MetadataAndHeaders`. Update the arithmetic comments in `src/crates/measure/src/lib.rs` (`:1003-1005`, `:2851-2941`) that spell out the old rule. In `src/crates/qcow2/src/lib.rs`, switch the fixture builder's no-parent count (`:11371-11376`) to qemu's rule, keeping it independent of the crate under test, and correct the comment at `:13552-13562`. In `tests/test_vhdx_fixed_fixtures.py`: remove the six #626 entries from `KNOWN_READ_FAILURES` (`:122-130`), keeping the dict and its mechanism; add `OP_MEASURE` to `METADATA_OPS` with an `_op_measure` that requires exit 0 and `fully-allocated` equal to `qemu-img measure -f vhdx -O qcow2 --output=json` (do not compare `required`; the docstring must say why); add a test converting `vhdx-dynamic-131041M-block-1M` with `instar convert -O qcow2` that requires exit 0 and the same `qemu-img info` virtual size, and report its wall-clock time in your summary. In `tests/test_create.py` (or the module that holds VHDX create structure tests), add a test that `instar create -f vhdx -o block_size=1M` at `131041M` declares the region offsets and lengths of the pin case `vhdx-dynamic-131041M-block-1M` (read it through `helpers.qemu_pins`; the pin stores BAT runs in a compressed form, so compare only `region_table_1`/`region_table_2` offsets and lengths, not `required`). Update `docs/testing.md` (`:2686` and `:2723`) so it no longer says #626 is a known failure. Commit subject: `Count VHDX BAT entries the way qemu does.` with `Fixes #626.` in the body. |
| 4b | high | opus | none | **Accept a partial last VHDX block in check (#797).** First read decision 3 and the two survey sections "qemu's partial-last-block allowance is wrong past the first chunk" and "check's whole-block assumption is in three places" in `docs/plans/PLAN-bat-population-phase-04-read-side.md`. In the check op's `FULLY_PRESENT` arm (`src/operations/check/src/main.rs:1771-1903`), compute the block's length from its **payload** index (`payload_block_idx`, not `bat_idx`) as `min(block_size, virtual_disk_size - payload_block_idx * block_size)` with checked arithmetic (an underflow is itself a corruption: a payload index past the virtual disk cannot happen given the loop bound, but say so in a comment rather than trust it silently), and use that length for the bounds test (`:1784-1793`), the declared-region overlap test (`:1847-1863`) and `max_block_offset` (`:1866-1868`). A block starting at or past end of file, and a block shorter in the file than its own length, stay corruptions with the existing message. Write a comment explaining that qemu's `vhdx_check_bat_entries` (`block/vhdx.c:884-960`, qemu 7.0.0) intends the same allowance but indexes by BAT entry, and so refuses its own images past the first chunk. Do not touch the readers in `src/crates/vhdx`, which already read these images correctly. In `tests/test_vhdx_fixed_fixtures.py`: remove the #797 entry from `KNOWN_READ_FAILURES`, leaving the line `KNOWN_READ_FAILURES = {}` and the comment above it rewritten to say what the table is for now that it is empty; add `vhdx-fixed-4608.5M-block-1M-bsz-off` (`subformat=fixed,block_size=1M,block_state_zero=off`, size `4608.5M`) to `RUNTIME_CREATE_IMAGES` and give it `METADATA_OPS` in `READ_MATRIX`; add a test that the host `qemu-img check` exits 2 on that image (skip without qemu-img), whose docstring points at the `docs/quirks.md` entry; add the two truncation cases of decision 6 on scratch copies of `vhdx-fixed-17M-bsz-off-data` (truncate to `25165824 + 524288`, and to `25165824`), each requiring `instar check` to exit 2; and add a test that `instar check --output json` on the untruncated fixture reports `image-end-offset` equal to the file length. Documentation: `docs/check.md:103-104` (a final block may end with the virtual disk, short of a whole block, and must still start inside the file), and a short `docs/quirks.md` section in the check quirks recording the qemu divergence with the 4608.5M measurement (all 80 builds 6.0.0 to 10.2.0: `qemu-img create` succeeds, `info` exits 1, `check` exits 2), noting that with `block_state_zero` on such an image opens in qemu. Commit subject: `Accept a partial last VHDX block in check.` with `Fixes #797.` in the body. |
| 4c | high | opus | none | **Refuse to resize a fixed VHDX (#798).** First read decisions 4 and 5 and the "LeaveBlocksAllocated is unparsed" survey section of `docs/plans/PLAN-bat-population-phase-04-read-side.md`. In `src/crates/vhdx/src/lib.rs`: add `FILE_PARAMETERS_LEAVE_BLOCKS_ALLOCATED: u32 = 1 << 0` beside `FILE_PARAMETERS_HAS_PARENT` (`:1145`), documented from SPEC(VHDX) 2.6.2.1 ("SHOULD NOT change the state of any blocks to a BLOCK_NOT_PRESENT state") and qemu `block/vhdx.h:275`; add `leave_blocks_allocated: bool` to `VhdxMetadata` (`:1119`) and `VhdxMetadataItems` (`:1228`), set in `parse_metadata_region` (`:1262-1276`) and `parse_metadata` (`:1352-1443`); unit tests that both parsers report it set and clear, and agree. Follow the `BitmapsPresent` precedent end to end: `ResizeError::FixedVhdx` in `src/crates/resize/src/lib.rs` (`:28-91`, documented); `leave_blocks_allocated` on `VhdxResizeOpts` (`:504`); the refusal in `plan_grow` (`src/crates/resize/src/vhdx.rs`) immediately after the `has_parent` refusal (`:52-54`), so no-op and shrink requests on a fixed image get it too; set the field in the op (`src/operations/resize/src/main.rs:824-845`) from the parsed metadata, and map the error (`:110-118`) to a new `ResizeResult::ERROR_FIXED_VHDX = 15` appended in `src/shared/src/lib.rs` (`:3918-3937`, with its pin test at `:5639`); in `src/vmm/src/main.rs` add the constant (`:301-309`), a message in `map_resize_error` (`:8536-8576`) per decision 5, and extend the code-range test (`:20740`) to the new code. Update `src/crates/resize/tests/vhdx_grow.rs` and `src/fuzz/fuzz_targets/fuzz_resize_planners.rs` (`:288-319`; set the new field from an unused bit of the fuzzed flags, not a constant). Crate tests: fixed grow, no-op and shrink are each `FixedVhdx`; a dynamic image still grows. In `tests/test_resize.py`, beside `_assert_differencing_vhdx_refused` (`:773-794`), add tests that resizing a scratch copy of each of `vhdx-fixed-16M` and `vhdx-fixed-16M-bsz-off-data` to `80M` fails, printing the new message, and leaves the file byte-identical (compare sha256); that `--shrink` to `8M` gets the same refusal; and that a qemu-written dynamic VHDX (`qemu-vhdx`, or one made with the host qemu-img) still grows. Documentation in `docs/resize.md`: the support table row at `:62`, a fixed-VHDX bullet beside the differencing one at `:215-220` saying why and what to do instead, and rewrite the stale multi-file VMDK sentence at `:210-213` so it no longer says create's roadmap needs a multi-output-device extension (create writes monolithicFlat on the host now; say only that multi-file resize would need several output devices open at once). Commit subject: `Refuse to resize a fixed VHDX.` with `Fixes #798.` in the body. |

The management session runs the steps in order. 4a and 4b both edit
the check op and `tests/test_vhdx_fixed_fixtures.py`, and 4c's crate
work sits beside 4a's in `src/crates/vhdx/src/lib.rs`. Every brief also carries the session's standing rules:
* Build only with `make instar` (Docker); never install a Rust
  toolchain on the host.
* Run `make test-rust`, redirecting cargo's target dir to an owned
  directory if pre-commit has left `src/target` root-owned. A
  `create` crate change needs `cargo test -p create` with its feature
  flags as the Makefile runs it.
* Run the touched test modules with stestr from `tests/`, with
  `INSTAR_BINARY_PATH` pointed at the worktree's build, and report
  "Ran: N".
* Run `pre-commit run --all-files` after `git add -N` on new files,
  and `tools/ci/check-test-partition.sh`.
* Keep plan, phase, step and decision references out of code and
  docs.
* Keep a mutation script under the step's scratch directory, and run
  each mutant under `timeout`.
* Every qemu behaviour a comment or doc states is cited to
  `instar-testdata/qemu-sources/7.0.0/` or was measured. Say which.
* Leave the work uncommitted.

## Execution notes

* **Back brief, 2026-10-25 (AEDT).** The operator kept decision 3
  (payload index, not qemu's BAT index), and allowed the management
  session to push the branch and open the pull request once the
  definition of done passes. The steps ran in order, one opus agent
  each at high effort.
* **4a.** Converting the 131041M fixture to qcow2 takes about 61 s,
  inside the 300 s tool timeout, and it was kept. 324 `test_measure`
  skips are "no baseline meta": instar-testdata's 10.0.0 measure
  profile is missing those files. The skip depends only on the file
  existing, not on this change. The agent once ran its mutants at the
  same time as `make test-rust`, which compiled a mutant into the test
  build. It reran cleanly, and the later briefs forbade running the
  two together.
* **4b.** qemu cannot open the 4608.5M image, so the read matrix gained
  a `QEMU_CANNOT_OPEN` table. For that image, the size it was created
  with stands in for `qemu-img info`, and `qemu-img measure --size`
  stands in for measuring the file. The `_op_*` helpers now take the
  image name. `_test_name` also replaces `.`. Dropping only the
  start-at-or-past-end-of-file test is an equivalent mutant, because
  every block length is at least one byte, so the guard stays with a
  comment saying it is implied. The management session confirmed the
  quirk text's claim that instar's other readers handle such images:
  `instar convert -O qcow2` of a 4608.5M image read back a marker
  written into its partial last block.
* **4c.** The fuzz target takes `leave_blocks_allocated` from flag bit
  6, which only the qcow2 arm used. The agent also added one sentence
  each to `docs/quirks.md`, `docs/format-coverage.md` and
  `docs/testing.md`, which would otherwise have said instar resizes
  every VHDX. They were kept. The management session confirmed the
  documented workaround: convert a fixed image to dynamic VHDX,
  resize it, and `compare` the result with the original.
* **Definition of done, 2026-10-25 (AEDT).** After a fresh `make
  instar` at the last step commit, `make test-rust` passed 2727 tests
  with 3 ignored. The stestr run of the eight modules reported Ran:
  1678, with 1252 passed, 414 skipped and 12 failed under 16 workers.
  None of the 12 touch VHDX: they are qcow2 re-encodes, the
  `vmdk-v3` convert, a VHD differencing compose and a create over a
  flat VMDK, and each failed with "convert operation failed" or
  "write to output device failed". Replayed alone (`--concurrency
  1`), all 12 pass (Ran: 12, 0 failed), so they are host contention,
  not this change. Of the 414 skips, 324 are the instar-testdata
  measure-profile gap noted under 4a. Most of the other 90 are
  existing, documented skips: known writer and scanner divergences,
  "qemu-img cannot resize", raw has no check, and missing libvhdi
  tools or ZSTD support. So the definition of done's "only
  missing-binary and missing-driver skips" was too narrow as written,
  and does not hold literally.

## Risks and mitigations

* **The count change moves a pinned output.** It changes writer bytes
  only where the old count crossed a 1 MiB boundary. The management
  session greps `tests/`, `scripts/` and `src/` for golden VHDX sizes
  and BAT lengths, and runs `test_create`, `test_measure`,
  `test_convert` and `test_differencing` in full after 4a, not only the
  modules 4a touched. That follows the standing rule that a
  crate-only change can alter its consumers.
* **Loosening check hides a truncated image.** Only the last block
  may be short, and only by the virtual disk's own arithmetic. The
  two truncation cases in decision 6 must fail. The management
  session's mutation run replaces the clamp with `block_size` (the
  partial fixture must then fail), uses `bat_idx` in place of
  `payload_block_idx` (the 4608.5M image must then fail), and drops
  the start-past-end test (the truncation case must then fail). Each
  mutant must be killed.
* **A walk skips a slot it used to read.** The count now stops before
  the trailing sector-bitmap slot. Every walk classifies that slot as
  a bitmap slot and skips it, and the survey shows no no-parent read
  consults it. The existing VHDX map, check and convert suites on
  every committed VHDX fixture are the check. The management session
  confirms that they run, not skip, by looking for "Ran: N".
* **Guest and host disagree on the new resize code.** The shared pin
  test, the host's code-range test and an integration test that
  matches the message text cover all three sides. The mutation run
  maps `FixedVhdx` to `ERROR_UNSUPPORTED_SUBFORMAT` and expects the
  message test to fail.
* **The refusal is never exercised by the fuzzer.** Decision 5 has the
  fuzz target draw the bit from its input. The management session
  reads the target diff to confirm it is not a constant.

## Definition of done

Each item is a command, run from the worktree root with the
worktree's build, and its expected result. `TD` is the
instar-testdata checkout and `F=$TD/custom/format-coverage`.

* `git diff --name-only origin/develop...HEAD -- src/` lists only files
  under `src/crates/{vhdx,create,measure,resize,qcow2}/`,
  `src/operations/{check,resize}/`, `src/shared/src/lib.rs`,
  `src/vmm/src/main.rs` and `src/fuzz/fuzz_targets/`.
* `grep -n 'div_ceil(chunk_ratio)' src/crates/vhdx/src/lib.rs
  src/operations/check/src/main.rs` finds no non-test line outside
  `differencing_bat_entry_count`.
* `instar check $F/vhdx-dynamic-131041M-block-1M.vhdx`, `instar map
  --output json` and `instar measure -O qcow2` on the same file each
  exit 0.
* `instar check $F/vhdx-fixed-17M-bsz-off-data.vhdx` exits 0, and a
  copy truncated with `truncate -s 25690112` makes it exit 2.
* `instar create -f vhdx -o block_size=1M d.vhdx 131041M`, read with
  `tests/helpers/vhdx_structure.py`, has a 1048576-byte BAT region at
  2097152 and metadata at 3145728.
* `cp $F/vhdx-fixed-16M.vhdx r.vhdx; instar resize r.vhdx 80M` exits
  non-zero with a message containing `fixed VHDX`, and `sha256sum`
  of `r.vhdx` is unchanged.
* `grep -c '^KNOWN_READ_FAILURES = {}$' tests/test_vhdx_fixed_fixtures.py`
  prints 1.
* `make test-rust` passes, and `make check-binary-sizes` passes.
* The stestr run of `test_vhdx_fixed_fixtures`, `test_vhdx_structure`,
  `test_check_formats`, `test_resize`, `test_create`, `test_measure`,
  `test_convert` and `test_differencing` reports "Ran: N" equal to the
  tests in those modules, with 0 failures. The only skips are
  missing-binary and missing-driver ones.
* `tools/ci/check-test-partition.sh` exits 0.
* `git diff origin/develop...HEAD -- src tests docs/*.md | grep -nE
  'PLAN-|[Pp]hase [0-9]|step 4[a-c]'` prints nothing.
* `git add -A && pre-commit run --all-files` passes.
* The pull request body carries `Fixes #626`, `Fixes #797` and
  `Fixes #798`. A keyword in a commit message does not close an issue
  when the PR lands through a merge commit.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase and how the intended work aligns with it.
Ask then whether decision 3 should stand: `instar check` accepting
fixed images that `qemu-img check` (and qemu's own open) refuses,
because qemu indexes the partial-last-block allowance by BAT entry.
The alternative is copying qemu exactly, which would make instar call
qemu's default `block_state_zero=off` output corrupt past the first
chunk.
