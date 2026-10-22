# Phase 1: pin qemu-img's behaviour, build the structural oracle, correct the doc

Phase 1 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation, Open questions and the phase 1 paragraph of "Sequencing
rationale" first; this file assumes them.

Planning effort: high. Every later phase copies what this phase pins,
and tests what it builds with the comparator this phase writes. A
wrong pin or a comparator that shares a misreading with the emitter
it will judge is invisible to `qemu-img info`, which is the whole
reason the comparator exists.

## Decisions already taken by the operator

* **Every open question in the master plan is resolved with its
  recommendation adopted** (Michael Still, 2026-10-21). In brief:
  preallocation of vmdk, vpc and vhdx is qemu parity, a no-op with
  `metadata` refused (1); monolithicFlat create is in, host-only, in
  phase 3 (2); `create`, `convert` and `measure` all gain fixed VHDX,
  convert writing blocks in place (3); the BAT is never materialised
  (4); #626 and #645 are closed by this plan, and #797, #798 and #799
  are its three filed defects (5); `resize` refuses a fixed VHDX (6);
  dynamic VHDX does not honour `block_state_zero` in this plan (7);
  create's default `block_state_zero` is qemu's, `on` (8). The master
  plan records each resolution under its question.
* **This plan runs alongside PLAN-654**, not after it. The only file
  both edit is the check op; see "What the survey found" for what
  #801 moved.

## Scope

In:

* A sweep of all 80 static `qemu-img` builds in instar-testdata over
  every qemu behaviour this plan copies, recorded as a committed,
  machine-checkable pin file in this repository, plus a summary table
  in this plan.
* The structural VHDX comparator (`tests/helpers/vhdx_structure.py`),
  importable by the integration tests and by
  `scripts/differential-fuzz.py`, with tests that pin its reading of
  qemu-written images to values derived from qemu source and
  SPEC(VHDX).
* A CI test that re-measures a bounded set of versions, and the host
  `qemu-img`, against the pin, and fails when the testdata gains a
  build the pin does not cover.
* The `docs/create.md:395` correction, as this phase's first commit
  after the plan.
* Settling the master plan: the open-question resolutions, the stale
  line citations and claims the survey found, and the sweep's
  findings if any contradict the Situation.

Out:

* Any change under `src/`. #626, #797, #798 and #799 are fixed in
  phases 4 and 5; the temptation to fix one while its reproduction is
  on screen is what this line exists to resist.
* Fixtures and baselines in instar-testdata (phase 2). Nothing in
  this phase commits to instar-testdata.
* Wiring the comparator into the differential fuzzer's create and
  convert ops (phase 11). This phase only guarantees the fuzzer can
  import it.
* Replacing the four ad hoc VHDX parsers already in the test suite
  (see the survey) with the new helper. Recorded in the master plan's
  Future work.
* `docs/create.md`'s other wrong future-work bullets (the default
  block size, the preallocation "BAT-population pattern", the
  monolithicFlat reason). Each changes in the phase that changes the
  behaviour it describes: 5, 3 and 3.

## What the survey found

Surveyed on 2026-10-21 against the worktree at `7a47f9f4` (two plan
commits on `d08841e3`, develop after #801). Measurements used the
static builds in
`instar-testdata/qemu-img-binaries/x86_64/<version>/qemu-img`
(testdata at `cf67642bbb`), the host's qemu-img 10.0.13 (Debian
`1:10.0.13+ds-0+deb13u1`), and the instar binary in the primary clone
(`src/target/release/instar`, built from `da9f043f`, mtime
2026-10-19 12:29; nothing VHDX-related changed between that commit
and `d08841e3`).

### The master plan's phase 1 premises

* **80 static builds, all executable.** `ls
  qemu-img-binaries/x86_64 | wc -l` is 80: 6.0.0 to 10.2.0, every
  patch release from 7.2.0 on, every directory holding an executable
  `qemu-img`. `qemu-sources/` carries 6.0.0, 6.0.1, 6.1.0, 6.1.1,
  6.2.0 and 7.0.0 only, so 7.0.0 is the newest tree a claim can be
  cited to, as the master plan says.
* **`tests/helpers/` exists** and is the right home. It holds
  `__init__.py` (`load_manifest_images`), `comparators.py`,
  `info_json.py` (`NESTED_INFO_DIVERGENCE` at `:33`),
  `qcow2_subclusters.py` (PLAN-654's fixture builders, the precedent
  for a pure helper with its own test module), `snapshot_readback.py`
  and `types.py`. Pure-helper unit tests live in `tests/test_helpers.py`.
* **The fuzzer already shares a helper this way.**
  `scripts/differential-fuzz.py:41-47` puts `tests/` on `sys.path`
  and imports `helpers.snapshot_readback`. A new helper is importable
  the same way provided it imports nothing from `tests/base.py`.
  `_normalise_create_info` (`:1547`), `op_create` (`:1651`) and the
  picker docstring with the backwards default-block-size claim
  (`:1587`) are where the master plan says.
* **No reusable VHDX parser exists, but four fragments do.**
  `tests/test_create.py:111-176` (`_vhdx_active_data_write_guid`,
  `_vhdx_bat_region`), `tests/test_info_safe.py:360-415` (metadata
  table walk), and `tests/test_differencing.py` around `:2384-2530`
  (region table, CRC-32C, first BAT entry) each parse just what one
  test needs, and none reads File Parameters' `LeaveBlocksAllocated`
  bit or classifies BAT entries. `scripts/create-vhd-testdata.sh:1191`
  has a CRC-32C as well. A structural comparator is new code; folding
  the fragments into it is out of scope (above).
* **The libvhdi oracle is not available here.** `tests/base.py:496`
  (`_require_vhdiinfo`) and `:537` (`_require_pyvhdi`) gate it, and
  CI installs it in the devcontainer, but `vhdiinfo` is not on this
  host. Whether libvhdi reports a fixed VHDX as fixed is therefore
  unmeasured; step 1b records it if the devcontainer can be used.
* **`qemu-img map` is not a BAT oracle for VHDX.** On 10.0.13 and on
  6.0.0, `map --output=json` of a fixed 64 MiB image reports one
  extent, `"data": true, "zero": false`, with no `offset`, for both
  the all-`ZERO` default image and the `FULLY_PRESENT` one. The vhdx
  driver has no block-status callback, so map cannot see BAT state;
  the comparator has to read the BAT itself.
* **`generate-baselines.py` and `detect-profiles.py`** are where the
  master plan says: `CREATE_CASES` at `:404`, the stale "vhdx is
  dynamic-only in instar; qemu defaults match" at `:455`, and
  `generate_create_baseline` at `:1542`, which records only `qemu-img
  info` JSON, so it cannot carry a structural pin without a new
  output type. `detect-profiles.py` groups versions by identical
  `stdout/stderr/meta` triples per output type. Read-only here.
* **A precedent for running the static builds in CI exists.**
  `tests/test_cow_cross_version.py` runs five pinned versions live and
  skips when the binaries are absent; `tests/stestr-timing.txt:719-721`
  shows it running in CI (6.85 to 28.76 s), and
  `tools/ci/prepare-testdata.sh:261` pulls all LFS objects, binaries
  included.
* **`docs/create.md:395`** reads "VHDX `subformat=fixed` (not emitted
  by qemu-img either)." It is the only such claim in `docs/` (grep for
  "not emitted by qemu" and "fixed vhdx"). The target table at
  `:60` says vhdx is `dynamic` only, which is true of instar.
* **The three defects are filed.** #797 (check, partial last block),
  #798 (resize, hybrid) and #799 (no 64 TiB ceiling) are open and
  labelled `bug`; #626's title has already been corrected to say it
  breaks reading qemu images. The master plan's Open issues table
  still said "(new)" for all three and its success criteria said
  "the three issues phase 1 files"; both are corrected in the master
  plan by this planning commit.

### Line citations after #801

Three merges landed between `da9f043f`, where the master plan's
citations were taken, and `d08841e3`: #793, #796 and #801.

* **`src/operations/check/src/main.rs`: unchanged for VHDX.** Every
  hunk of `git diff da9f043f d08841e3` in that file is at line 2653 or
  later. The BAT entry count (`:1676-1688`) and the block bounds check
  (`:1784-1793`) are where the master plan cites them.
* **`src/vmm/src/main.rs` moved by 112 to 149 lines** in every region
  the master plan cites, and **`src/shared/src/lib.rs` by 31 to 78
  lines** after line 840 (`GUEST_CREATE_SCRATCH_LIMIT` at `:737` did
  not move). Each citation was remapped through the diff's hunks and
  checked by content; the master plan now cites the current lines and
  says so.
* `src/crates/{vhdx,create,measure,vmdk}`, `src/operations/create`
  and `src/operations/convert` did not change; their citations hold
  (`calculate_bat_layout`'s count at `vhdx/src/lib.rs:3768-3778`,
  `VhdxState::init`'s at `:2325-2348`, `HasParent` at `:1145`,
  `:1264`, `:1353`; `plan_vhdx`'s untouched BAT at
  `create/src/lib.rs:1524-1529`; the 32 MiB default at
  `operations/create/src/main.rs:566-570` and `:664-668`).
* `tests/test_create.py` (`CREATE_CASES` `:1618`, vhdx at `:1654`,
  `KNOWN_WRITER_DIVERGENCES` `:1688`, the backwards comment
  `:1698-1703`) is unchanged.

### SPEC(VHDX), which the master plan asked phase 1 to cite

`[MS-VHDX]` revision 8.0 (2024-04-23), the PDF and DOCX downloaded
from the Microsoft Open Specifications page on 2026-10-21. The BAT
formulas are images in both (DOCX `word/media/image8.bin` to
`image11.bin`), so a text search of the PDF finds the prose and not
the arithmetic.

* **2.5 BAT.** "Payload Blocks Count = Ceil(VirtualDiskSize /
  BlockSize)"; "Sector Bitmap Blocks Count = Ceil(Payload Blocks Count
  / Chunk Ratio)"; "For dynamic and fixed VHDX, the last BAT entry
  MUST locate the last payload block of the virtual disk. The total
  number of BAT entries ... can be calculated as Total BAT Entries =
  Payload Blocks Count + Fl((Payload Blocks Count - 1) / Chunk
  Ratio)"; for differencing, "Sector Bitmap Blocks Count * (Chunk
  Ratio + 1)". So the spec agrees with qemu's `vhdx_calc_bat_entries`
  (`block/vhdx.c:868-882`) and #626's diagnosis, on both halves.
* **2.6.2.1 File Parameters.** "LeaveBlockAllocated (1 bit): Specifies
  whether blocks can be unallocated from the file. If this field is
  set to 1, the implementation SHOULD NOT change the state of any
  blocks to a BLOCK_NOT_PRESENT state, and the implementation MUST NOT
  reduce the size of the file to a value lower than would be required
  to allocate every block. This field is intended to be used to create
  a fixed VHDX file that is fully provisioned. Ignored if HasParent is
  set." That settles open question 6's request for the spec text:
  `resize` adding `NOT_PRESENT` entries under the bit (#798) is what
  the spec says not to do.
* **2.5.1.1** lists `PAYLOAD_BLOCK_ZERO` as valid in a fixed VHDX
  (footnote 8), and `NOT_PRESENT`/`UNDEFINED`/`UNMAPPED` as acceptable
  to read but not to transition to in a fixed file (footnote 6).
* **No file-growth rule.** qemu's comment at `block/vhdx.c:902-907`
  says "The VHDX spec states that the image file can only grow in
  blocksize increments"; revision 8.0 says no such thing (the only
  "increment" in the text is the log's sequence number). The partial
  last block (#797) is a qemu tolerance of its own past output, not a
  spec rule instar has been enforcing.

### Spot sample, measured for this plan

Run on 2026-10-21 on the oldest and newest static builds, 6.0.0 and
10.2.0, with the earlier survey's `vhdxdump.py` and `sweep.sh`. The
two versions agreed on every line below, and every line agrees with
the master plan's Situation.

| Probe | Result on 6.0.0 and 10.2.0 |
|-------|----------------------------|
| `create -f vhdx -o subformat=fixed fx.vhdx 64M` | 75497472 B apparent, 1265664 allocated; BAT region 2 MiB + 1 MiB, metadata 3 MiB + 1 MiB; block size 8 MiB; File Parameters bits `0x1`; 8 entries state 2 at offset 0; `check` clean |
| same, `,block_state_zero=off` | as above, but 8 entries state 6 at 8 MiB, 16 MiB ... 64 MiB |
| same, `65M`, `block_state_zero=off` | 76546048 B (8 MiB + 65 MiB); 9 entries state 6, the last at 75497472; `check` clean |
| `create -f vhdx dyn.vhdx 64M` | 8388608 B; 8 entries state 2 |
| same, `-o block_state_zero=off` | 8388608 B; all 131072 slots zero |
| `create -f vhdx -o block_size=1M x.vhdx 131041M` | BAT region 1 MiB at 2 MiB, metadata at 3 MiB; 131041 entries state 2, 31 zero sector-bitmap slots |
| default block size at 1024M / 1025M / 102400M / 102401M / 33554432M / 33554433M | 8 / 16 / 16 / 32 / 32 / 64 MiB |
| `create -f vhdx t.vhdx 64T` / `65T` | accepted, 16777216 B / "Image size too large; max of 64TB", rc 1 |
| `-o preallocation=off,falloc,full` for vpc, vmdk, vhdx at 64M | 2560/8, 65536/24, 8388608/2472 (bytes/blocks), the same for all three modes |
| same with `metadata`, and `vpc`/`vhdx` `subformat=fixed,preallocation=metadata` | rc 1, "Unsupported preallocation mode: metadata" |
| `vpc subformat=fixed`, `vhdx subformat=fixed`, `vmdk subformat=monolithicFlat` under off/falloc/full | 67125760/16, 75497472/2472, extent 67108864/8, identical across modes |
| `vmdk subformat=monolithicFlat` with `-b base.vmdk -F vmdk` | rc 1, "Flat image can't have backing file" |
| `vhdx subformat=fixed` with `-b b.vhdx -F vhdx` | rc 1, "Backing file not supported for file format 'vhdx'" |
| `convert -O vhdx -o subformat=fixed` of a 64 MiB raw with 8 MiB blocks 1, 3 and 6 non-zero | 100663296 B; entries 1, 3, 6 state 6 at 75497472, 83886080, 92274688; the other five state 2; `compare` identical |
| same, `,block_state_zero=off` | 75497472 B; all 8 entries state 6 at 8 MiB + i x 8 MiB; `compare` identical |
| `ddb.toolsVersion` in a monolithicFlat descriptor | absent on 6.1.0, present on 6.2.0 |

And against instar (`da9f043f` binary): `instar check` exits 2 on the
65M fixed `block_state_zero=off` image (#797) and on the 131041M
1 MiB-block dynamic image (#626); `instar create -f vhdx -o
subformat=fixed` fails with "create: -O vhdx -o subformat=fixed is not
yet supported (only the dynamic vhdx subformat is implemented)".

The master plan's 10.0.13 convert measurement (4411392 B allocated)
used a source with 1 MiB of data in each of three blocks, where this
sample filled whole blocks; the layouts agree, only the allocation
differs.

## Decisions

1. **The pin lives in this repository, as
   `tests/golden/qemu-create-pins.json`, grouped by outcome.** The
   shape is `{"versions": [...], "cases": {name: [{"versions": [...],
   "result": {...}}, ...]}}`: each case holds one group per distinct
   result, so a version boundary is two groups and "no boundary" is
   one group listing all 80 versions. `result` is always `rc` and a
   filename-normalised `stderr`, plus, per case kind, the comparator's
   summary (VHDX), file length and the mode-identity booleans
   (preallocation), or the normalised descriptor (monolithicFlat).
   Later phases consume it through `tests/helpers/qemu_pins.py`
   (`expected(case, version=None)`, which with no version demands a
   single group and raises if there is a boundary), so a phase 7 test
   asserting "instar's fixed image equals the structure every qemu
   version writes" is one comparator call against the pin rather than
   80 qemu runs. Why not instar-testdata's `expected-outputs/`, the
   house home for cross-version qemu output: that tree holds
   `qemu-img`'s stdout, and the thing to pin here is a parse of the
   image, made by a parser that lives in this repository; a testdata
   copy would either import across repositories or duplicate the
   parser. The derived `profiles/` there have also been silently
   wrong twice, and a 13-group-per-case JSON with an explicit version
   list is easier to review by eye than 80 directories.
2. **The sweep tool lives in instar `tools/`, as
   `tools/sweep-qemu-create.py`,** next to `probe-qemu-versions.sh`.
   It imports the comparator and the case table from `tests/helpers/`
   the way the fuzzer does, takes `--testdata` (default
   `../instar-testdata`), `--versions` (default: every directory under
   `qemu-img-binaries/x86_64`), `--scratch`, and either `--write PIN`
   or `--check PIN`. `--check` re-measures the named versions and
   exits non-zero on any difference from the pin, naming the case,
   version and field. Nothing is written to instar-testdata; that
   repository takes direct commits to `main` and nothing in this phase
   needs one.
3. **One case table, shared.** The cases are defined once, in
   `tests/helpers/qemu_pins.py` (`CASES`), and run by one function
   (`run_case(qemu_img, case, workdir)`), which both the tool and the
   CI test call. The case list is in step 1c's brief and is the
   master plan's phase 1 list made concrete; adding a case later is a
   table row plus a re-run of the tool.
4. **The comparator is a parser plus a summary plus a diff, and it
   compares only deterministic fields.** `parse_vhdx(path)` returns a
   frozen dataclass; `summarise(structure)` returns a JSON-able dict;
   `structural_diff(a, b)` returns a list of human-readable
   differences, empty when equal. Compared: file length; both region
   tables (entries in order, and whether the two copies agree); the
   active header's log offset and length; metadata items present and
   their offsets; block size, logical and physical sector size,
   virtual disk size; the `LeaveBlocksAllocated` and `HasParent` bits;
   chunk ratio; the BAT region's capacity; every BAT entry, run-length
   encoded as `(first_index, count, kind, state, first_offset,
   stride)` where kind is `payload`, `sector_bitmap` or `padding`
   (past SPEC(VHDX) 2.5's entry count). Not compared: GUIDs, sequence
   numbers, checksums, the creator string, and allocated bytes
   (`st_blocks` differs between ext4, xfs and tmpfs); allocation is
   exposed as a separate field for relative assertions only. Malformed
   input raises `VhdxParseError`, never `IndexError` or
   `struct.error`, because the fuzzer will feed it instar output that
   may be wrong.
5. **The comparator is checked against qemu's arithmetic, not against
   instar.** Its tests assert, on qemu-written images, the values that
   `block/vhdx.c` and SPEC(VHDX) 2.5 predict (data start `bat_offset +
   bat_length + 5 MiB`, `:1708`; file length; entry count; states and
   offsets), computed in the test from the cited formulas. It is never
   tested against an instar-written fixed VHDX, because there is none
   yet and because the emitter it will judge must not be its
   reference. If the devcontainer's libvhdi can read a qemu fixed
   image, step 1b records what it reports as a second, independent
   reading; it is not a gate.
6. **CI re-measures a bounded set, and enforces coverage.**
   `tests/test_qemu_create_pins.py` runs every case on 6.0.0, 6.2.0
   (the `toolsVersion` boundary), 8.2.0 and 10.2.0 when the binaries
   are present, and on the host `qemu-img` against the group of the
   newest pinned version at or below it, skipping with a reason where
   the host lacks a driver (`skip_unless_qemu_supports`). It also
   fails when `qemu-img-binaries/x86_64` holds a version the pin does
   not list, with a message naming the tool to re-run, so the pin
   cannot silently fall behind the testdata. The full 80-version run
   is the tool, run by hand, as `generate-baselines.py` is.
7. **The doc correction is step 1a, its own commit, before anything
   else.** It replaces only the `:395` bullet, with what qemu-img
   actually writes and the fact that instar refuses it today. It does
   not claim a version range: the claim "qemu-img emits fixed VHDX"
   is already measured on 14 versions including both ends, and the
   sweep only widens it. If step 1d were to find a version that
   refuses, the bullet gains a qualifier in step 1f.
8. **This phase lands on `bat-population-phase-01-pin`, with the master
   plan.** The master plan is not on `develop` yet, and this branch
   already carries it. Phase 1's `Merged` cell will therefore record
   the merge commit of the pull request that carries the master plan
   and this phase together, as PLAN-differencing's phase 1 did.

The decision most likely to be argued with is 1: putting a
cross-version qemu pin in instar's `tests/golden/` rather than in
instar-testdata's `expected-outputs/`, which is where every other
per-version qemu result lives and which `detect-profiles.py` already
knows how to group. The case for the house pattern is consistency and
a single place to look. The case against, for this pin, is that what
is pinned is not qemu's output but a reading of qemu's image by a
parser in this repository, so storing it in testdata either splits the
parser from its results across two repositories with no shared
review, or copies the parser; and phase 10, which does need
per-version create baselines in testdata, can still generate them
there with this comparator if the operator prefers, using the pin as
the cross-check that its derived profiles are right.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | low | sonnet | none | **Correct `docs/create.md:395`.** Replace the bullet "- VHDX `subformat=fixed` (not emitted by qemu-img either)." in the Future work list with one stating that instar refuses VHDX `subformat=fixed` today while qemu-img does emit it: `qemu-img create -f vhdx -o subformat=fixed` sets the `LeaveBlocksAllocated` file parameter, writes a BAT entry for every payload block (`PAYLOAD_BLOCK_ZERO` by default, `PAYLOAD_BLOCK_FULLY_PRESENT` at sequential offsets with `-o block_state_zero=off`) and extends the file sparsely to the end of its data region. Do not give a qemu version range. Re-measure before writing: run `qemu-img create -f vhdx -o subformat=fixed fx.vhdx 64M` in a scratch directory and confirm exit 0 and a 75497472-byte file. Change nothing else in the file: the neighbouring bullets on the default block size, preallocation and monolithicFlat are wrong too, but each is corrected by the phase that changes that behaviour. No plan, phase or step references in the doc. Commit subject: `docs: qemu-img does emit fixed VHDX.` with a body citing `block/vhdx.c:1602-1605` and `:1690-1768` (qemu 7.0.0 source in `instar-testdata/qemu-sources/7.0.0/`) and the measurement. |
| 1b | high | opus | none | **The structural VHDX comparator.** Write `tests/helpers/vhdx_structure.py`: stdlib only, no import from `tests/base.py` (it must import from `scripts/differential-fuzz.py` the way `helpers.snapshot_readback` does at `differential-fuzz.py:41-47`), single quotes, 120 columns. API and compared fields are decision 4 of `docs/plans/PLAN-bat-population-phase-01-pin.md`; implement exactly that: `VhdxParseError(ValueError)`; `parse_vhdx(path) -> VhdxStructure` (frozen dataclass); `summarise(structure) -> dict`; `structural_diff(a, b) -> list[str]`; `allocated_bytes(path)`. Layout facts (SPEC(VHDX) rev 8.0, and qemu `block/vhdx.h` in `instar-testdata/qemu-sources/7.0.0/`): headers at 64 KiB and 128 KiB, active = higher sequence number; region tables at 192 KiB and 256 KiB, 16-byte header (`regi`, checksum, entry count u32 at +8) then 32-byte entries (16-byte GUID mixed-endian, offset u64, length u32, required u32); BAT region GUID `2DC27766-F623-4200-9D64-115E9BFD4A08`, metadata `8B7CA206-4790-4B9A-B8FE-575F050F886E`; metadata table: `metadata` signature, entry count u16 at +10, 32-byte entries from +32 (item GUID, offset u32 relative to the region, length u32); File Parameters item `CAA16737-FA36-4D43-B3B6-33F0AA44E76B` = block size u32 then flags u32 (bit 0 `LeaveBlocksAllocated`, bit 1 `HasParent`); Virtual Disk Size `2FA54224-CD1B-4876-B211-5DBED83BF4B8`; Logical Sector Size `8141BF1D-A96F-4709-BA47-F233A8FAAB5F`; Physical Sector Size `CDA348C7-445D-4471-9CC9-E9885251C556`. Chunk ratio = 2^23 x logical sector size / block size. A BAT entry is u64 LE: state in bits 0-2, `FileOffsetMB` in bits 20-63. Entry `i` is a sector-bitmap slot iff `(i + 1) % (chunk_ratio + 1) == 0`. Entry count per SPEC(VHDX) 2.5: payload = ceil(vsize / block), non-differencing total = payload + floor((payload - 1) / chunk_ratio), differencing = ceil(payload / chunk_ratio) x (chunk_ratio + 1); slots past that inside the region are `padding`. Run-length encode entries with equal kind and state whose offsets advance by a constant stride. Bounds-check every offset and length against the file before reading; read with `seek`/`read`, never the whole file (fixed images are tens of GiB apparent). Mirror the existing ad hoc parsers for GUID byte order (`tests/test_create.py:139-140`, `tests/test_info_safe.py:366-371`) but do not edit them. Tests in a new `tests/test_vhdx_structure.py` (an `InstarTestBase` subclass using `_require_qemu_img`, `tests/base.py:473`): for qemu-written images (host qemu-img; fixed and dynamic, `block_state_zero` on and off, 64M and 65M, `block_size=1M` at 64M, dynamic 131041M at `block_size=1M`), assert the parse equals values computed in the test from the qemu formulas, citing them: data start = BAT offset + BAT length + 5 MiB (`block/vhdx.c:1708`), fixed file length = data start + vsize (`:1719-1725`), state 2 at offset 0 by default and state 6 at data start + i x block size with `block_state_zero=off` (`:1731-1758`), fixed bit set only for fixed (`:1602-1605`), entry count per `vhdx_calc_bat_entries` (`:868-882`); that `structural_diff` is empty for two qemu images of the same command (GUIDs differ) and names the field for fixed versus dynamic and for the two `block_state_zero` modes; that every manifest VHDX fixture with `safe` safety parses (`helpers.load_manifest_images`); and that truncated files, a bad `regi` signature, a region past EOF, a zero block size and a block size that is not a power of two raise `VhdxParseError`. If you can run the devcontainer (`make test-container-core` builds it; `docker run` may be denied on this host), run `vhdiinfo` on a qemu fixed image and record its verbatim output in the commit message; otherwise say you could not. Run the module with stestr and confirm "Ran: N" matches the test count. Commit subject: `Add a structural VHDX parser for tests and the fuzzer.` |
| 1c | high | opus | none | **The case table and the sweep tool.** Write `tests/helpers/qemu_pins.py` (stdlib only; imports `helpers.vhdx_structure`; no `tests/base.py`) holding `PIN_PATH` (`tests/golden/qemu-create-pins.json`), `CASES`, `run_case(qemu_img, case, workdir) -> dict`, `load_pin()`, and `expected(case_name, version=None)` (decision 1: with no version it raises unless the case has exactly one group). Cases, each a dict with a name, a kind and the argv tail: **VHDX structure** (kind `vhdx-create`, result = `rc`, normalised `stderr`, `summarise()` when rc is 0, `qemu-img check` rc): fixed 64M, fixed 64M `block_state_zero=off`, fixed 65M both modes, fixed 64M `block_size=1M` both modes, fixed 131041M `block_size=1M,block_state_zero=off`, dynamic 64M both modes, dynamic 131041M `block_size=1M`, dynamic 4161792M (default block size, BAT exactly 1 MiB), default-block-size dynamic at 1024M, 1025M, 102400M, 102401M, 33554432M, 33554433M, dynamic 64T, dynamic 65T (refused), fixed with `-b` a dynamic vhdx (refused), fixed with `preallocation=metadata` (refused). **Convert** (kind `vhdx-convert`): a 64 MiB raw source built by `run_case` with 8 MiB blocks 1, 3 and 6 filled with byte `0x5a` and the rest zero; `convert -O vhdx -o subformat=fixed` and `-o subformat=fixed,block_state_zero=off`; result adds `qemu-img compare` rc. **Preallocation** (kind `prealloc`): for vpc, vmdk, vhdx at 64M, and for `vpc subformat=fixed`, `vhdx subformat=fixed`, `vmdk subformat=monolithicFlat` (measure the `-flat.vmdk` extent), modes off, falloc, full, metadata; result per mode = rc, stderr, file length, `same_bytes_as_off` and `same_allocation_as_off`. Compute `same_bytes_as_off` against a volatile-byte mask: create four `off` images, sleeping 1.1 s after the first (timestamps are in seconds), mask every byte position that differs between any two of them, then compare each mode's file to the first `off` outside the mask; on a mismatch rebuild everything once before reporting `false`. **monolithicFlat** (kind `vmdk-flat`): the descriptor text with the `CID=` value and the extent filename replaced by placeholders, the extent length; and `-b` a vmdk (refused). Normalise `stderr` by replacing the scratch path with `$DIR`. Then write `tools/sweep-qemu-create.py` per decision 2 of the phase plan (`--testdata`, `--versions`, `--scratch`, `--write`/`--check`, `--jobs` for parallel versions defaulting to 1), importing `tests/helpers` via `sys.path` as `scripts/differential-fuzz.py:41-47` does. `--write` groups versions by identical `result` (compare canonical JSON, `sort_keys=True`) and writes the pin with versions in numeric order. `--check` exits 1 naming case, version and the `structural_diff` or field difference. It must delete each image after measuring (the 32 TiB and 64 TiB cases write 8 MiB BATs; the 131041M fixed case is a 128 GiB sparse file). Smoke-test with `--versions 6.0.0,10.2.0 --write <scratch>/pin.json` and confirm the fixed 64M cases have one group and the results match the phase plan's spot-sample table; do not commit a pin in this step. Commit subject: `Add a qemu-img create behaviour sweep.` |
| 1d | medium | opus | none | **Run the full sweep and commit the pin.** `python3 tools/sweep-qemu-create.py --write tests/golden/qemu-create-pins.json --scratch <session scratchpad>/sweep` over all 80 versions (do not include the host qemu-img). Host load has made qemu commands fail spuriously in this repository before: before attributing any boundary, re-run that case alone on the versions either side of it with `--check` and confirm it reproduces. For every case with more than one group, name the boundary (last version of one group, first of the next) and say which result field changed. Expected, from the master plan and the spot sample: one group for every VHDX and preallocation case, and two groups for the monolithicFlat descriptor (`ddb.toolsVersion` absent on 6.0.0, 6.0.1, 6.1.0, 6.1.1; present from 6.2.0). Anything else is a finding: report it to the management session before writing anything that depends on it, because it may contradict the master plan's Situation. Then add a "Sweep results" section to `docs/plans/PLAN-bat-population-phase-01-pin.md`: the command, the date, wall time, the testdata commit, and a table of cases with their group count and any boundary. Verify the pin lists exactly the 80 versions present. Commit the pin and the plan section together. Commit subject: `Pin qemu-img create behaviour across 80 builds.` |
| 1e | medium | sonnet | none | **CI check of the pin.** Add `tests/test_qemu_create_pins.py`, an `InstarTestBase` subclass, following `tests/test_cow_cross_version.py` for discovering the pinned binaries (`self._testdata_root / 'qemu-img-binaries' / 'x86_64'`, skip when absent). Tests: (1) every version directory present under that root is listed in the pin, failing with "re-run tools/sweep-qemu-create.py --write tests/golden/qemu-create-pins.json" otherwise; (2) for 6.0.0, 6.2.0, 8.2.0 and 10.2.0, when present, every case's `run_case` result equals `expected(case, version)`, one subTest per (version, case), reporting `structural_diff` output on failure; (3) the host `qemu-img` (`_require_qemu_img`, `tests/base.py:473`), compared against the group of the newest pinned version at or below the host's version (parse `qemu-img --version`), calling `skip_unless_qemu_supports` (`:456`) for vpc, vmdk and vhdx first; (4) every VHDX create case except the 65T, backing and metadata refusals has exactly one group (`expected(case)` without a version succeeds), so a boundary appearing in a re-sweep is a test failure until a later phase handles it. Use a `tempfile.TemporaryDirectory` per test. If the 131041M fixed case fails on the CI temp filesystem for size rather than behaviour, do not weaken the assertion: report it. Run `tools/ci/check-test-partition.sh` (integration-core is exclusion-based and should claim the module), then run the module with stestr and confirm "Ran: N" and 0 failures. Add lines to `tests/stestr-timing.txt` only if the module takes more than 30 s, sorted by id, per `docs/testing.md:728-760`. Commit subject: `Check the qemu-img create pins in CI.` |
| 1f | low | sonnet | none | **Close the phase.** If step 1d found a boundary or a result that contradicts `docs/plans/PLAN-bat-population.md`'s Situation, correct the Situation at its source and say so in the commit; if it found none, add one sentence to the Situation's "Fixed VHDX is stable" paragraph saying the 80-build sweep confirmed it, naming the pin file. Confirm `git diff --name-only origin/develop...HEAD -- src/` is empty and `git diff origin/develop...HEAD -- tests tools docs/create.md \| grep -nE 'PLAN-\|[Pp]hase [0-9]\|step 1[a-f]'` prints nothing. Run `git add -A` then `pre-commit run --all-files` (it cannot see untracked files). Present the commits. Do not change the Execution table's status: the next phase's first commit closes this one out. Commit subject: `Record the qemu-img create sweep in the bat-population plan.` |

Commits: one per step, each passing `pre-commit run --all-files`
after `git add`. Steps 1b and 1c are separate commits because the
comparator is useful, and reviewable, without the sweep. Step 1d's
commit carries data and the plan section that explains it.

## Sweep results

Run on 2026-10-21 against instar-testdata `cf67642b`
(`cf67642bbb93fdce0cd5adbae032951f74838563`), all 80 static builds
from 6.0.0 to 10.2.0, the host `qemu-img` excluded:

    python3 tools/sweep-qemu-create.py \
        --write tests/golden/qemu-create-pins.json \
        --scratch <scratchpad>/sweep --jobs 2

Wall time 377 s (load average falling from about 14 to 7 during the
run). The pin lists exactly the 80 versions in
`qemu-img-binaries/x86_64`, and is 71622 bytes.

The result is the expected one: every case is a single group except
the monolithicFlat descriptor, which splits exactly at the
`ddb.toolsVersion` boundary.

| Case | Groups | Boundary |
|------|--------|----------|
| `vmdk-flat-64M` | 2 | 6.1.1 / 6.2.0: `descriptor` gains `ddb.toolsVersion = "2147483647"`; `rc`, `stderr` and `extent_length` unchanged |
| the other 30 cases (23 VHDX, 6 preallocation, `vmdk-flat-backing`) | 1 | none, 6.0.0 to 10.2.0 |

The first group is 6.0.0, 6.0.1, 6.1.0 and 6.1.1. The boundary was
replayed on its own with `--check --cases vmdk-flat-64M --versions
6.1.0,6.1.1,6.2.0` and matched the pin. Every pinned value that the
spot sample above also measured agrees with it, so the master plan's
Situation stands.

## Execution notes

Departures from the plan as written, recorded 2026-10-21
(Australia/Sydney).

* Step 1b found three qemu 7.0.0 `block/vhdx.c` citations in the
  plans off by a few lines: data start is `:1708` not `:1712`, the
  fixed truncate is `:1719-1725` not `:1722-1728`, and
  `vhdx_create_bat` ends at `:1768` not `:1765`. Corrected at source
  in both plans in commit `7a796640`.
* Step 1b could not measure libvhdi's reading of a fixed VHDX,
  because `docker run` was denied on the host. It is still
  unmeasured; it is not a gate.
* Step 1c added, beyond its brief and kept: BAT run lists folded in
  the pin file (`compact_bat`/`expand_bat`), per-directory
  preallocation images with the sparse VMDK's embedded descriptor
  compared as normalised text (its CID is unpadded hex), a `--cases`
  filter for `--check` replays, and a `parse_error` result instead of
  an aborted sweep.
* Step 1e: the single planned 58 s `test_pinned_versions_reproduce`
  was split, by the management session, into one test per version
  (`test_pinned_6_0_0_reproduces` and so on, about 20 s each) so
  stestr can spread them across workers. Ran: 7.
* Step 1d: the management session independently re-measured 6.0.1
  and 7.2.17 with `--check`; both matched on all 31 cases.

## Risks and mitigations

* **Correlated error between the comparator and a future emitter.** If
  the parser misreads, say, the interleave rule, and phase 6 writes the
  same misreading, every phase 7 test passes. Mitigated by decision 5:
  the comparator's tests assert qemu-written images against values
  computed from cited qemu lines and SPEC(VHDX) 2.5 formulas, before any
  instar fixed output exists, and the management session re-derives a
  sample of those expected values by hand when reviewing step 1b.
* **A plausible-but-wrong qemu claim in a brief.** This repository has
  recorded it repeatedly. Every claim in the briefs above is cited to
  `qemu-sources/7.0.0` or measured in the spot sample; step 1d's sweep
  is the 80-version check; the management session re-runs `--check`
  on two versions of its own choosing before accepting the pin.
* **Spurious boundaries from host load.** Concurrent KVM load has made
  qemu commands fail here before. Mitigated by step 1d's rule: replay a
  suspected boundary alone before believing it.
* **A pin that falls behind the testdata.** New static builds arrive in
  instar-testdata over time. Mitigated by decision 6's coverage test,
  which fails CI until the pin is regenerated.
* **Filesystem-dependent allocation.** `st_blocks` differs between
  ext4, xfs and the tmpfs `/tmp` on Debian 13. Mitigated by decision 4:
  allocation is never compared absolutely, only "same as `off`" and,
  later, "same as qemu's on the same filesystem".
* **Large sparse files in CI.** The 131041M fixed case is 128 GiB
  apparent, well under ext4's 16 TiB ceiling, and tmpfs counts only
  allocated pages, so it should be harmless. Step 1e reports rather
  than weakens if it is not.
* **Scope creep into `src/`.** The read defects reproduce in seconds
  and their fixes look small. Mitigated by the scope line and the
  `git diff -- src/` check in step 1f and the definition of done.

## Definition of done

Each item is a command, run from the worktree root, with its
expected result.

* `grep -n 'not emitted by qemu-img' docs/create.md` prints nothing,
  and `git log --format=%s origin/develop..HEAD` shows `docs: qemu-img
  does emit fixed VHDX.` before every other step's subject.
* `git diff --name-only origin/develop...HEAD -- src/` prints nothing.
* `python3 -I -c "import json; d = json.load(open('tests/golden/qemu-create-pins.json')); print(len(d['versions']))"`
  prints `80`.
* `python3 tools/sweep-qemu-create.py --check tests/golden/qemu-create-pins.json --versions 6.0.0,7.2.10,9.1.0,10.2.0`
  exits 0 (the management session picks two of these at random to
  re-measure itself).
* `cd scripts && python3 -c "import sys; sys.path.insert(0, '../tests'); import helpers.vhdx_structure, helpers.qemu_pins"`
  exits 0, so the differential fuzzer can import both helpers.
* The stestr runs of `test_vhdx_structure`, `test_qemu_create_pins`
  and `test_helpers` report "Ran: N" equal to the number of tests in
  the three modules, 0 failures, and no skips other than the documented
  missing-binary and missing-driver skips.
* `tools/ci/check-test-partition.sh` exits 0.
* `git diff origin/develop...HEAD -- tests tools docs/create.md | grep -nE 'PLAN-|[Pp]hase [0-9]|step 1[a-f]'`
  prints nothing.
* This plan has a "Sweep results" section naming every case with more
  than one group, and the master plan's Situation either agrees with
  it or has been corrected.
* `git add -A && pre-commit run --all-files` passes.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase and how the intended work aligns with
it. Step 1d additionally reports its group table to the management
session before writing the plan section: a boundary nobody expected
changes what phases 3 to 10 copy, and it is cheap to hear now.
