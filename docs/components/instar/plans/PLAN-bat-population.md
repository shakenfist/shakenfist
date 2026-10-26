# Populated BAT output: fixed VHDX and non-qcow2 preallocation

## Status: In progress

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

Consult `ARCHITECTURE.md` for the overall system structure
(host VMM, KVM guest, call table, device emulation).
Consult `AGENTS.md` for build commands, project conventions,
code organisation, and the security model summary. Consult
`docs/` for format-specific documentation (`docs/qcow2/`,
`docs/raw/`, etc.) and `docs/commentary/` for architectural
decisions and design rationale.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

The 2026 technical goal *VHD/VMDK writers done* has, after
`PLAN-differencing.md` closed its leaf, two open leaves: VHDX fixed
output, and vmdk/vpc/vhdx preallocation. Both hang off a prerequisite
the goals graph calls the *per-format BAT population pattern*, which
also feeds the later VMDK `twoGbMaxExtent*` multi-extent output leaf.
This plan takes the prerequisite and the two leaves; the multi-extent
leaf stays out (see the Mission).

It runs alongside `PLAN-654-subcluster-classification`, whose first
phase merged as `d08841e3` (#801). That plan rebuilds the qcow2 read
path and touches none of the VHD, VHDX or VMDK writers, so this plan
does not depend on it. The one file both edit is the check op
(`src/operations/check/src/main.rs`), whose qcow2 arms #801 rewrote.

Line numbers were first taken at `da9f043f`. Three merges have landed
since (#793, #796, #801, ending at `d08841e3`, which this branch is
based on). None moved the check op's VHDX lines, but they shifted the
`src/vmm/src/main.rs` lines cited below by 112 to 149 and the
`src/shared/src/lib.rs` ones by 31 to 78; those were re-cited at
`d08841e3` when phase 1 was planned (2026-10-21). Each phase's survey
re-reads the lines it depends on.

Everything below was surveyed or measured on 2026-10-20. "Measured"
means one of: the host's qemu-img 10.0.13 (Debian
`1:10.0.13+ds-0+deb13u1`); the per-version static builds in
`instar-testdata/qemu-img-binaries/x86_64/<version>/qemu-img`; or an
instar binary built from the primary clone at `da9f043f` (nothing
VHDX-related changed between that commit and `d08841e3`). Source citations are to
`instar-testdata/qemu-sources/7.0.0/`, the newest qemu tree the
testdata repository carries. Every behaviour cited from it was also
measured on 10.0.13, and most on 10.2.0 and older builds as well, so
the citation explains a mechanism rather than standing as the only
evidence.

### What qemu-img actually emits

**Fixed VHDX exists, and its default BAT is not "fully present".**
`qemu-img create -f vhdx -o subformat=fixed fx.vhdx 64M` succeeds:
apparent size 75497472 (64 MiB payload plus 8 MiB of structure),
1265664 bytes allocated, `qemu-img info` cluster size 8388608. So
`docs/create.md:395`, which says VHDX `subformat=fixed` is "not
emitted by qemu-img either", is wrong. But the shape of that image is
not the one the goals graph assumed. Dumping its region table, File
Parameters item and BAT:

| `block_state_zero` | BAT payload entries | File Parameters | data start | apparent size |
|--------------------|---------------------|-----------------|------------|---------------|
| default (on) | all `PAYLOAD_BLOCK_ZERO` (2), file offset 0 | `LeaveBlocksAllocated` set | 8 MiB | 75497472 |
| `off` | all `PAYLOAD_BLOCK_FULLY_PRESENT` (6), offsets 8 MiB + i x 8 MiB | `LeaveBlocksAllocated` set | 8 MiB | 75497472 |

Both are sparse (1265664 bytes allocated): qemu `ftruncate`s the file
out to `data_start + virtual_size` and writes only the structure. The
mechanism is `vhdx_create_bat` (`block/vhdx.c:1690-1768`): the data
region starts at `bat_offset + bat_region_length + 5 MiB` (`:1708`),
a fixed image is truncated to `data_start + image_size` with
`PREALLOC_MODE_OFF` (`:1719-1725`), and the BAT is filled by
emulating one write per block with the state
`FULLY_PRESENT`, overridden to `ZERO` when `use_zero_blocks` is set
(`:1731-1758`). `use_zero_blocks` defaults to **true**
(`:1939-1943`), and the File Parameters bit is set for fixed images
only (`:1602-1605`; the constant's own comment at `block/vhdx.h:275`
reads "Do not change any blocks to be BLOCK_NOT_PRESENT. If set
indicates a fixed size VHDX file"). Sector-bitmap BAT entries are
never written and stay zero.

So "populating the BAT" for qemu parity means writing a non-zero
entry for every payload block in both modes: `0x2` everywhere by
default, and a sequential `FULLY_PRESENT` offset per block with
`block_state_zero=off`. It is not a sparse hole in either.

Two consequences of that default the plan has to carry:

* **qemu's dynamic VHDX is populated too.** `use_zero_blocks` is not
  subformat-specific, so a default `qemu-img create -f vhdx` also
  writes `PAYLOAD_BLOCK_ZERO` into every payload entry (measured on
  6.0.0 and 10.0.13; 64 MiB image, 8 entries of state 2) and truncates
  to `data_start` (8 MiB apparent). instar's `plan_vhdx` leaves the
  BAT as a hole, every entry `NOT_PRESENT`
  (`src/crates/create/src/lib.rs:1524-1529`), and stops at the end of
  the metadata region (4 MiB apparent). `qemu-img create -f vhdx -o
  block_state_zero=off` gives exactly instar's all-zero BAT. Neither
  `qemu-img info` nor `compare` can see the difference, which is why
  no baseline has.
* **qemu's convert into a fixed VHDX is not fixed-shaped.**
  `qemu-img convert -O vhdx -o subformat=fixed` of a 64 MiB raw image
  with three non-zero 8 MiB blocks produced a 100663296-byte file:
  the three data blocks were *appended* after the preallocated 72 MiB
  (offsets 75497472, 83886080, 92274688) and the other five entries
  stayed `ZERO`. That is qemu's write path, not a layout choice: a
  write to a `ZERO`-state block allocates a new block at end of file
  (`block/vhdx.c:1366-1378`). With `block_state_zero=off` the same
  convert writes in place (eight `FULLY_PRESENT` entries, 75497472
  bytes, 4411392 allocated). Both outputs `compare` identical to the
  source.

**Fixed VHDX is stable across every version measured.** Both table
rows above, byte-for-byte in every structural field (region table,
block size, File Parameters bits, BAT state histogram, first and last
BAT entries, apparent and allocated size), on 6.0.0, 6.2.0, 7.0.0,
7.2.0, 8.0.0, 8.2.0, 9.0.0, 9.2.0, 10.0.0, 10.1.0, 10.2.0 and 10.0.13,
with `qemu-img check` clean on every one. No version boundary was
found in the sample, and phase 1's sweep of all 80 static builds
confirmed it, pinning the result in
`tests/golden/qemu-create-pins.json`.

**Preallocation of vmdk, vpc and vhdx is a no-op in qemu, and
`metadata` is refused.** `qemu-img create -f {vpc,vmdk,vhdx} -o
preallocation=MODE x 64M`, on the same twelve versions:

| Target | `off` | `falloc` | `full` | `metadata` |
|--------|-------|----------|--------|------------|
| vpc | 2560 B / 8 blocks | identical | identical | "Unsupported preallocation mode: metadata" |
| vmdk | 65536 B / 24 blocks | identical | identical | same |
| vhdx | 8388608 B / 2472 blocks | identical | identical | same |

"Identical" is stronger than equal size: on 10.0.13 an `off` and a
`full` image differ only at byte positions where two `off` images
created a second apart also differ (timestamps and the vpc unique id,
vhdx GUIDs, the vmdk descriptor's random `CID=`). The same holds for
the subformats that are qemu's real answer to "preallocated": `vpc
subformat=fixed`, `vhdx subformat=fixed` and `vmdk
subformat=monolithicFlat` are each the same size and allocation
under `off`, `falloc` and `full` (measured on 6.0.0, 8.2.0 and
10.0.13), and all three are sparse.

The mechanism refines the measured fact the plan started from. None
of the three drivers lists `preallocation` in its create options, so
the option passes through to the protocol layer, but each driver
first strips its own options, `size` included, from what it forwards:
`qemu_opts_to_qdict_filtered(..., true)` in `vpc_co_create_opts`
(`block/vpc.c:1106`) and `vhdx_co_create_opts` (`block/vhdx.c:2077`),
and `qemu_opt_get_size_del` in `vmdk_co_create_opts`
(`block/vmdk.c:2656`). `bdrv_create_file` (`block.c:721-760`) then
asks `file-posix` for a **zero-length** file in the requested mode,
so `falloc` and `full` preallocate nothing at all, and `metadata`
falls through to the `default:` arm of `raw_regular_truncate`, which
is where the error text comes from (`block/file-posix.c:2025`). Every
later growth of the file by the format driver passes
`PREALLOC_MODE_OFF` (`block/vhdx.c:1715`, `:1722`).

**monolithicFlat refuses a backing file, and its descriptor gained a
line at 6.2.0.** `qemu-img create -f vmdk -o
subformat=monolithicFlat -b base.vmdk -F vmdk child.vmdk` fails with
"Flat image can't have backing file" (`block/vmdk.c:2429`; measured
on 10.0.13). The descriptor's `ddb.toolsVersion = "2147483647"` line
is absent on 6.0.0 and 6.1.0 and present on 6.2.0 and every later
version sampled (7.0.0, 7.2.0, 8.0.0, 8.1.0, 8.2.0, 10.0.13).

### What instar does today

* **create refuses fixed VHDX** at the `-o` parser
  (`src/vmm/src/main.rs:15435-15441`, "create: -O vhdx -o
  subformat=fixed is not yet supported"), and `--subformat fixed`
  never reaches it because `validate_create_args` only admits
  `--subformat` for vmdk and vpc (`:19486-19492`). `-o
  block_state_zero` and `-o log_size` are accept-ignored (`:15454-15456`).
  `plan_vhdx` has no subformat field at all
  (`src/crates/create/src/lib.rs:336-360`).
* **create refuses every non-`off` preallocation for vmdk, vpc and
  vhdx**, in the `-o` parser (`main.rs:15458-15476`) and again in
  `validate_create_args` (`:19513-19520`), with "is not yet supported
  ... (preallocation is implemented for raw and qcow2 only)".
* **create refuses monolithicFlat** (`main.rs:19470-19474`, "multi-file
  subformats are not implemented; use instar convert -O vmdk for
  now"), and `docs/create.md:392-394` gives the reason as needing
  multi-output-device support in the call table.
* **convert writes monolithicFlat without any multi-output device.**
  The host retargets the guest at `ImageFormat::Raw` and opens the
  `<stem>-flat.vmdk` extent as the *only* output device
  (`main.rs:13998-14012`), the guest writes raw sectors into it, and
  afterwards the host writes the text descriptor itself with
  `vmdk::build_flat_descriptor` (`main.rs:14613-14627`,
  `src/crates/vmdk/src/lib.rs:261`) and `set_len`s the extent to the
  virtual size (`main.rs:14654-14659`). The descriptor is
  `qemu-img info`-equivalent to qemu's own modulo `cid` (instar writes
  a constant `0xfffffffe`) and `actual-size`; instar omits the DDB
  geometry, adapter, hardware-version and tools-version lines
  (measured). A `create` of an empty monolithicFlat image therefore
  needs no guest at all: it is `run_create_raw`
  (`main.rs:18828`) on the extent plus that descriptor write.
* **measure refuses fixed VHDX** (`main.rs:14867`), and rejects
  `-o preallocation` for vpc, vmdk and vhdx as an *unrecognised key*
  rather than an unsupported value (measured). qemu-img cannot
  measure any of the three ("Block driver 'vhdx' does not support
  size measurement"), so measure's answers for them are instar-only
  and their oracle is instar's own writer. `measure_vhdx`
  (`src/crates/measure/src/lib.rs:1009`) predicts the convert writer's
  layout.
* **convert writes dynamic VHDX only** (`docs/format-coverage.md:333`)
  and has no `-o`; `--subformat` is vmdk-only (`main.rs:13717-13742`).
  `convert_to_vhdx` (`src/operations/convert/src/main.rs:4382`) holds
  the whole BAT in guest memory between the staging buffer and
  `ALLOC_HEAP_BASE`, and when it does not fit it prints a debug line
  and fails with no error message (`:4456-4460`). Payload blocks are
  packed sequentially from the end of the metadata region, only for
  non-zero blocks, and the BAT is written last (`:4682-4700`).
  `compute_output_capacity` sizes a VHDX output device as
  `round_up(vsize, 32 MiB) + 10 MiB` whatever the block size or BAT
  length (`main.rs:13614-13628`).
* **create's BAT, and qcow2's precedent for streaming one.** The
  create guest plans into an 8 MiB scratch region
  (`GUEST_CREATE_SCRATCH_LIMIT`, `src/shared/src/lib.rs:737`) inside a
  ~12.9 MiB guest scratch window. qcow2 metadata preallocation already
  meets the same wall: its L2 tables are deliberately not in the
  `MetadataPlan` and are streamed through a single reusable cluster
  slot by `emit_qcow2_l2_and_extend`
  (`src/operations/create/src/main.rs:1082-1110`, `:1129-1210`), which
  also writes one zero sector at `file_size_after` to extend the file.
  The host then runs `apply_preallocation` (`main.rs:19009`) over the
  data region for qcow2 only (`:18266-18347`). The host exposes an
  output device of `virtual_size + 64 MiB` (`main.rs:18110-18112`).
* **A fixed VHDX BAT does not fit in either guest budget.** Measured
  on 10.0.13: a 2 TiB fixed image at 1 MiB blocks has a 17 MiB BAT
  region (2097152 payload entries plus 511 sector-bitmap slots) and a
  data region starting at 24 MiB. At qemu's 64 TiB ceiling
  (`VHDX_MAX_IMAGE_SIZE`, `block/vhdx.h:289`) with 1 MiB blocks the BAT
  region is 513 MiB (computed, not measured: the host's filesystem
  cannot hold the file, see below). The entries are a pure function of their index, though,
  so nothing needs to hold more than one write's worth of them.
  qemu builds the same 2 TiB image in 0.06 s; it `g_malloc`s the BAT.
* **The create op binary** used 65,224 bytes of its 768KB cap when
  `PLAN-differencing` phase 7 measured it, and already links `vhd` and
  `vhdx`.
* **The config structs have room.** `CreateConfig` has a
  `vhd_subformat: u8` and a `flags` word with bits 0-5 used
  (`src/shared/src/lib.rs:3483-3549`), so a fixed VHDX subformat and a
  `block_state_zero` bit fit without changing the struct's layout.
  `ConvertConfig.flags` uses bits 0-5 and 31 (`:2675-2695`), so a
  fixed-output bit fits there too.

### Reading fixed VHDX: mostly right, with three defects

Every read operation was run against the qemu-written 64 MiB fixed
images above, in both `block_state_zero` modes. `info`, `check`,
`map`, `compare` (against an all-zero raw image) and `convert -O raw`
all succeed and agree with qemu. Three things do not:

1. **#626 is real, and it breaks reading qemu images, not just
   writing.** The issue proposes that instar's non-differencing BAT
   entry count, `total_blocks + ceil(total_blocks / chunk_ratio)`, is
   one larger than qemu's (and, per the issue, SPEC(VHDX)'s)
   `total_blocks + floor((total_blocks - 1) / chunk_ratio)`. qemu's
   `vhdx_calc_bat_entries` (`block/vhdx.c:868-882`) confirms qemu's
   half, and so does SPEC(VHDX) 2.5 (revision 8.0): for fixed and
   dynamic files "Total BAT Entries = Payload Blocks Count +
   Fl((Payload Blocks Count - 1) / Chunk Ratio)". The phase 1 plan
   quotes the section. The issue's framing is also slightly wrong: since
   `ceil(n / c) == floor((n - 1) / c) + 1` for every `n >= 1`, the two
   rules differ by one entry at **every** geometry, not only at exact
   multiples of `chunk_ratio`. It becomes visible when the extra entry
   crosses a 1 MiB region boundary. At 131041 one-MiB blocks qemu
   writes a 1 MiB BAT region and instar a 2 MiB one (metadata at
   3 MiB versus 4 MiB; `qemu-img check` accepts instar's). The
   reverse direction is the serious one: on qemu's image, instar
   `check` reports a corruption ("check: VHDX BAT region too small"),
   `convert -O raw` fails ("failed to init chain states"), `compare`
   reports "Content mismatch at offset 0!", `map` says "source format
   unrecognised" and `measure` says "unsupported format". Only `info`
   works. It is reachable with defaults: a plain `qemu-img create -f
   vhdx d.vhdx 4161792M` (3.97 TiB, default 32 MiB blocks, 130056
   payload blocks, BAT exactly 1 MiB) is clean under `qemu-img check`
   and corrupt under `instar check`. The count is written out three
   times: `calculate_bat_layout` (`src/crates/vhdx/src/lib.rs:3768-3778`),
   `VhdxState::init` (`:2325-2348`) and the check op
   (`src/operations/check/src/main.rs:1673-1688`).
2. **`instar check` rejects qemu's partial last block.** A fixed
   VHDX whose virtual size is not a block multiple (65 MiB at 8 MiB
   blocks, `block_state_zero=off`) ends 1 MiB into its ninth block.
   `qemu-img check` is clean; `instar check` exits 2 with "check: VHDX
   block offset out of bounds", because it requires
   `file_offset + block_size <= file length`
   (`src/operations/check/src/main.rs:1784-1793`). qemu deliberately
   allows it: "Allow that the last block exists only partially"
   (`block/vhdx.c:902-907`). `convert`, `compare` and `map` read the
   same image correctly. Any fixed VHDX instar writes with qemu's
   layout at such a size would fail instar's own check. Filed as
   #797.
3. **Nothing in instar knows a VHDX is fixed.** The vhdx crate reads
   only the `HasParent` bit of File Parameters
   (`src/crates/vhdx/src/lib.rs:1145`, `:1264`, `:1353`), never
   `LeaveBlocksAllocated`. So `instar resize rs.vhdx 80M` on a
   qemu-written fixed image (qemu refuses to resize any VHDX) succeeds
   and leaves a hybrid: `LeaveBlocksAllocated` still set, eight
   `FULLY_PRESENT` entries and two new `NOT_PRESENT` ones, file length
   unchanged. `qemu-img check` accepts it, but it is no longer a fixed
   image, and qemu's own definition of the bit is "do not change any
   blocks to be BLOCK_NOT_PRESENT". `qemu-img info` shows nothing
   subformat-specific for VHDX, so `info` parity is not affected.
   Filed as #798.

### Two geometry divergences that fixed output makes louder

* **Default block size (#645).** qemu scales the default with size,
  and instar always uses 32 MiB (`src/operations/create/src/main.rs:566-570`,
  `:664-668`; the convert host's capacity estimate assumes it too,
  `src/vmm/src/main.rs:13893`). Measured on 6.0.0, 8.2.0 and 10.2.0 at both sides
  of every boundary: 1024 MiB gives 8 MiB, 1025 MiB gives 16 MiB,
  102400 MiB gives 16 MiB, 102401 MiB gives 32 MiB, 33554432 MiB
  (32 TiB) gives 32 MiB and one MiB more gives 64 MiB. The source is
  `block/vhdx.c:1962-1974`. The quirk text, `docs/create.md`'s
  divergence bullet and future-work bullet, the comment on
  `KNOWN_WRITER_DIVERGENCES` in `tests/test_create.py:1698-1703` and
  the differential fuzzer's picker docstring
  (`scripts/differential-fuzz.py:1587`) all say the reverse (instar
  8 MiB, qemu 32 MiB). For a fixed image the default decides the
  layout, the file length and `info`'s `cluster-size`, so parity for
  fixed output is not reachable without it.
* **No 64 TiB ceiling.** qemu refuses a VHDX over 64 TiB ("Image size
  too large; max of 64TB", `block/vhdx.c:1919-1922`); instar creates a
  65 TiB dynamic VHDX without complaint (measured). For a dynamic
  image that is a sparse 20 MiB file; for a fixed one it is a
  65 TiB file.

### The test harness cannot see the difference

`qemu-img info` reports nothing subformat-specific for VHDX, and the
one field that would distinguish fixed from dynamic, the wrapping
file's `virtual-size` under `children[*].info`, is stripped by the
info normaliser (`tests/helpers/info_json.py`, the
`NESTED_INFO_DIVERGENCE` pass) for good reason. So the
`TestCreateBaselineMatrix` info-equivalence baselines would pass an
instar "fixed" VHDX that was really dynamic, and so would the
differential fuzzer's create op, which compares the same normalised
info (`scripts/differential-fuzz.py:1547`, `:1651`). This plan needs a
**structural** oracle: parse the region table, File Parameters, BAT
and file length of both outputs and compare them. qemu-img is a good
oracle for that, because every structural field above is
deterministic across all twelve versions measured; only GUIDs,
timestamps and the creator string vary. `qemu-img map` cannot stand in
for it: the vhdx driver reports no block status, so `map
--output=json` of a fixed image is one `"data": true` extent with no
`offset`, whether its entries are `ZERO` or `FULLY_PRESENT` (measured
on 6.0.0 and 10.0.13).

The other test surfaces this plan touches: `CREATE_CASES` and
`KNOWN_WRITER_DIVERGENCES` in `tests/test_create.py:1618-1713` (vhdx
has five dynamic cases and no fixed ones; vpc has two fixed cases);
the baseline generator in `instar-testdata/scripts/generate-baselines.py`
(its own `CREATE_CASES` at `:404`, with a stale comment at `:455` that
"vhdx is dynamic-only in instar; qemu defaults match");
`fuzz_create_emitters` (`src/fuzz/fuzz_targets/fuzz_create_emitters.rs`)
over all four planners; and the differential fuzzer's create and
convert pickers. `tests/manifest.json` has no fixed VHDX fixture at
all: the two VHDX inputs from the outside world (`qemu-vhdx`,
`vhdx-disk2vhd`) are dynamic.

A standing caution about the baseline tooling applies: the derived
`profiles/` directories in instar-testdata have been silently wrong
twice while the raw per-version baselines were correct and the suite
stayed green. Any phase that regenerates baselines validates the
derived profiles against the raw ones rather than trusting them.

### Large outputs and the host

Fixed images are real files. On this host's ext4 the 16 TiB
maximum file size is the practical ceiling, not qemu's 64 TiB: a
15 TiB fixed VHDX creates (16492685950976 bytes apparent, 4411392
allocated), a 17 TiB one fails with "The image size is too large for
file format 'vhdx'". Large-size tests have to stay under that, and
should assume the CI runners have the same limit until a phase
checks.

### Open issues

| Issue | What it is | This plan |
|-------|------------|-----------|
| #626 | Non-differencing BAT entry count over-declares by one | **Closes it.** Confirmed by source and measurement above, and it breaks reading qemu images at some sizes. Fixed layout parity needs it. |
| #645 | Default VHDX block size scaling is backwards in the docs; instar is fixed at 32 MiB | **Closes it.** Fixed output inherits the default. |
| #797 | `check` rejects qemu's partial last VHDX block | **Closes it.** instar's own fixed output would trip it. |
| #798 | `resize` turns a fixed VHDX into a hybrid; `LeaveBlocksAllocated` is never parsed | **Closes it**, per open question 6. |
| #799 | No 64 TiB VHDX ceiling | **Closes it.** |
| #566 | Created VHD/VHDX images share a constant identity | Aware. Fixed VHDX inherits the constant `DataWriteGuid` and convert's constant VMDK `CID`; the fix is a call-table change of its own. |
| #580 | create error messages are static | Aware. New refusals here are static too; they say what to do instead. |
| #578 | `VhdState::init` mislocates a fixed VHD footer above 512-byte sectors | Aware. Not reachable from `create`, which pins `--sector-size` to 512 (`main.rs:19141-19148`). |
| #582 | `VhdxState::init` discards the active header | Aware. Phase 4 reads File Parameters near it; folding #582 in is optional. |
| #576 | VHDX metadata overlap is not detected | Aware. The fixed File Parameters bit is one more item a parser trusts. |
| #623 | Differencing BAT padding (closed) | Context. Fixed output refuses a backing file, so the padded rule never applies to it. |

## Mission and problem statement

Give instar a per-format way to write a populated block allocation
table at any size the format allows, and use it to emit fixed VHDX
the way qemu-img does; make `-o preallocation` on vmdk, vpc and vhdx
behave as it does in qemu-img; and fix the VHDX read-side and
geometry defects that would otherwise make instar's own fixed output
fail instar's own `check`.

The "BAT population pattern" turns out to be narrower than the goals
graph assumed, and the plan is shaped by that. qemu-img never
preallocates data for vmdk, vpc or vhdx at any version measured, and
its "preallocated" forms of those formats are subformats that are
themselves sparse. A fixed VHD has no BAT and monolithicFlat has no
grain tables. So the only format whose output needs a populated table
is VHDX, and the pattern is: compute each BAT entry from its index,
stream the entries through one reusable guest buffer, extend the file
to its full length, and never hold the table in memory. The same
streaming shape is what a multi-extent VMDK writer would need for its
grain tables later, which is why the goals graph treats it as that
leaf's prerequisite too.

In scope:

* `instar create -f vhdx -o subformat=fixed` (and `--subformat
  fixed`), honouring `-o block_state_zero` with qemu's default (`on`):
  `LeaveBlocksAllocated` set, every payload entry `PAYLOAD_BLOCK_ZERO`
  by default or `PAYLOAD_BLOCK_FULLY_PRESENT` at qemu's sequential
  offsets with `block_state_zero=off`, qemu's data-region placement,
  and the file extended to `data_start + virtual_size`, sparse. A
  backing file with `subformat=fixed` is refused, as qemu does.
* A streaming BAT emitter in the crates (`no_std`, panic-free,
  bounded by one write buffer whatever the virtual size), used by both
  the create op and the convert writer.
* `instar convert -O vhdx --subformat fixed`, writing blocks in place.
* `instar measure -O vhdx -o subformat=fixed`.
* `-o preallocation=falloc|full` accepted for vmdk, vpc and vhdx in
  `create` and `measure`, producing the same image as `off`;
  `metadata` refused. Parity, not real preallocation; open question 1.
* `instar create -f vmdk -o subformat=monolithicFlat`, host-only (open
  question 2).
* The VHDX defects listed in the Situation: #626 (BAT entry count),
  #645 (default block size), the partial last block in `check`,
  `LeaveBlocksAllocated` parsing and what `resize` does with it, and
  the 64 TiB ceiling. Each is either a precondition of correct fixed
  output or a defect that fixed output makes reachable from instar's
  own files.
* A structural VHDX comparator for the tests and the differential
  fuzzer, because `qemu-img info` cannot tell fixed from dynamic.

Out of scope:

* VMDK `twoGbMaxExtentSparse` / `twoGbMaxExtentFlat` output. Its own
  leaf, and the one part of the goal that does need several output
  files open at once.
* Multi-output-device support in the call table. Nothing in this plan
  needs it: monolithicFlat create is host-only, and the convert path
  that writes it already uses one output device.
* *Real* preallocation of vmdk, vpc or vhdx data (allocating blocks
  that qemu-img leaves sparse). No qemu version does it, so it would
  be an unflagged instar-only behaviour with no oracle, and the
  populated-BAT forms that would express it already exist as
  subformats. Open question 1 argues this through.
* Honouring `block_state_zero` for **dynamic** VHDX, which would change
  the bytes of every default `create -f vhdx` and every `convert -O
  vhdx` to write `ZERO`-state entries. Recorded as a divergence, open
  question 7.
* `convert -O vpc` fixed output. instar's convert writes dynamic VHD
  only; qemu's `convert -O vpc -o subformat=fixed` is a separate gap
  this plan records but does not close.
* Honouring `-o log_size`, which moves qemu's BAT. instar accept-ignores
  it for dynamic VHDX today and keeps doing so; a fixed image created
  with a non-default `log_size` will differ from qemu's in layout.
  Recorded in Future work.
* Created-image identity (#566) and parameterised create error
  messages (#580), both call-table changes of their own.

## Open questions

Each question carries the planner's recommendation and its reasoning.
The operator resolved all eight on 2026-10-21, before phase 1's plan
was written, adopting every recommendation as drafted; each question
records it.

1. **Preallocation of vmdk, vpc and vhdx: qemu parity (a no-op), or
   real preallocation?** *Recommendation: parity.* Accept `falloc`
   and `full` for all three formats in `create` (and as `-o` keys in
   `measure`), produce exactly the image `off` would, and refuse
   `metadata`. Reasons:

   * Every qemu version measured, from 6.0.0 to 10.2.0, does this, and
     the source shows why: the format drivers strip `size` before the
     protocol layer sees the mode, so there is nothing for it to
     preallocate. It is not an accident of one release, so it will
     not move under us.
   * "Real" preallocation of a sparse format has no agreed meaning.
     For VHDX it would mean populating the BAT with `FULLY_PRESENT`
     entries, which is what `subformat=fixed` with
     `block_state_zero=off` already is; for vpc it would mean either
     a fixed disk (already a subformat) or a dynamic disk with every
     block allocated, which no tool produces; for vmdk it would mean a
     monolithicSparse file with every grain allocated, which no tool
     produces either. Each would be an unflagged instar-only shape
     with no external oracle, which is the situation
     `PLAN-differencing` spent a phase escaping.
   * Users who want an allocated image have the subformats, and this
     plan makes the last of them (fixed VHDX) available.

   The cost is an inconsistency inside instar that should be
   documented rather than hidden: `instar resize --preallocation=full`
   on these formats really does allocate the appended region, an
   existing instar-only divergence (`docs/format-coverage.md`, the
   resize table). That stays as it is; the create docs say so. The
   refusal text for `metadata` should say what qemu says ("Unsupported
   preallocation mode: metadata") in instar's own voice, and not
   promise that support is coming.

   Resolved (2026-10-21): recommendation adopted.

2. **Include `create -f vmdk -o subformat=monolithicFlat`?**
   *Recommendation: yes, as part of the host-only create phase.* The
   Situation's finding is that the reason given for refusing it,
   multi-output-device support, does not apply: an empty monolithicFlat
   image is a sparse raw extent plus a text descriptor, both written by
   the host, and convert already writes exactly that descriptor with
   `vmdk::build_flat_descriptor`. The work is small: share the extent
   naming rule (`<stem>-flat.vmdk`, now inline in the convert host
   path) with create; `ftruncate` the extent to the virtual size, and
   keep it sparse under every preallocation mode, because qemu's is
   (measured, question 1) -- which means *not* reusing
   `create_raw_finalize`, whose `falloc` and `full` really allocate;
   remove both files on failure; and refuse `-b` as qemu does ("Flat
   image can't have backing file"). It closes the "vmdk" third of the
   preallocation leaf with the format qemu users actually reach for.
   Two caveats to carry into the phase: the descriptor omits qemu's DDB
   lines and writes a constant `CID`, which is `info`-equivalent except
   for `cid` but is visible to anything reading the descriptor, and
   `docs/create.md` currently promises it is blocked on the call table.
   If the operator would rather keep this plan to VHDX, it is a
   self-contained follow-up and the refusal message should still be
   corrected, because its stated reason is false.

   Resolved (2026-10-21): recommendation adopted.

3. **Are `convert -O vhdx --subformat fixed` and `measure` in scope,
   or only `create`?** *Recommendation: all three, as separate
   phases.* The goals-graph leaf is "VHDX fixed output", not "create",
   and `docs/format-coverage.md` lists convert's VHDX output as dynamic
   only; a create-only answer would leave the one path users convert
   real data through unable to produce the format. `measure` must
   follow because its VHDX answers are instar-only and defined as a
   prediction of instar's own writer, and it already carries a
   refusal string for exactly this subformat.

   For convert, *recommendation: write blocks in place*, the layout
   qemu produces with `block_state_zero=off` (every payload entry
   `FULLY_PRESENT` at `data_start + i x block_size`, all-zero blocks
   left as holes, file length `data_start + virtual_size`). qemu's
   default convert-to-fixed output appends data after the preallocated
   region and leaves the rest `ZERO`, which is an artefact of its
   block-write path rather than a fixed layout, and its file is larger
   than the fixed size. The in-place layout has an exact qemu oracle
   (`qemu-img convert -O vhdx -o subformat=fixed,block_state_zero=off`)
   and is what a user asking for a fixed disk means. instar's convert
   has no `-o`, so whether to expose `block_state_zero` there at all is
   a phase 8 detail; the recommendation is not to, and to document the
   divergence from qemu's default.

   Resolved (2026-10-21): recommendation adopted.

4. **How are large BATs handled within guest limits?**
   *Recommendation: never materialise the table.* Each entry of a
   fixed VHDX BAT is a pure function of its index (interleave index
   `i + i / chunk_ratio`, state, and for `FULLY_PRESENT` an offset of
   `data_start + i x block_size`; sector-bitmap slots stay zero), so
   the emitter fills one reusable buffer (a `MAX_SECTOR_SIZE` slot,
   8192 entries) per write and the guest never holds more, at 1 MiB
   blocks or at 64 TiB. This is the qcow2 metadata-preallocation
   pattern (`emit_qcow2_l2_and_extend`) with an arithmetic body, and
   keeps the BAT out of the `MetadataPlan` and its 8 MiB scratch.
   Convert uses the same filler for its fixed path, so it does not
   inherit the dynamic writer's in-memory BAT and its silent
   too-large failure. Three host consequences to carry:

   * The output device capacity must come from the layout, not from
     `virtual_size + 64 MiB` (create) or `round_up(vsize, 32 MiB) +
     10 MiB` (convert): a 2 TiB fixed image at 1 MiB blocks needs
     24 MiB before its data region and a 64 TiB one 520 MiB.
   * The cost is I/O, not memory: a 17 MiB BAT is 34816 writes at
     create's 512-byte sector size. Phase 7 measures wall-clock time at
     2 TiB and at the largest size CI's ext4 allows, and the plan
     accepts the result or batches writes, on measurement.
   * Tests stay under ext4's 16 TiB file-size ceiling (Situation).

   The 64 TiB ceiling (qemu's, Situation) is enforced in the shared
   layout before any of this runs.

   Resolved (2026-10-21): recommendation adopted.

5. **Which open issues does this plan close?** *Recommendation:*
   close #626 and #645, and file and close three new ones (the check
   partial-last-block defect, the resize/`LeaveBlocksAllocated` defect
   and the missing 64 TiB ceiling). Be aware of #566, #580, #578,
   #582 and #576 without acting on them. The table in the Situation
   gives the reasoning per issue. The short form: #626 is not the
   benign over-declaration its body describes but a live read
   failure on well-formed qemu images, and fixed layout parity needs
   it; #645 decides every fixed image's layout and `cluster-size`;
   the three new ones are defects instar's own fixed output would
   expose. #566 and #580 each need a call-table change that this plan
   is built to avoid.

   Resolved (2026-10-21): recommendation adopted. The three new
   issues were filed on 2026-10-20 as #797, #798 and #799.

6. **What does `resize` do with a fixed VHDX?** *Recommendation:
   refuse, with a typed error naming the subformat, until a
   fixed-preserving grow exists.* Today instar grows one into a
   hybrid (Situation, read-side defect 3), which qemu's own definition
   of the flag forbids. qemu refuses to resize any VHDX, so refusing
   the fixed case is parity, not a regression against qemu; it is a
   regression against what instar did before, which produced an image
   that is not what the user had. A fixed-preserving grow (populate the
   new entries `FULLY_PRESENT`, relocate the BAT if the region grows,
   extend the file) is the better end state and goes to Future work,
   because relocating a BAT that the data region immediately follows is
   its own design problem. The phase that adds the refusal should
   confirm the flag's semantics against SPEC(VHDX) 2.6.2.1 text, which
   the repository does not carry; the qemu header comment is the only
   reading this plan has.

   Resolved (2026-10-21): recommendation adopted. SPEC(VHDX)
   2.6.2.1 (revision 8.0) bears the recommendation out: with
   LeaveBlockAllocated set "the implementation SHOULD NOT change the
   state of any blocks to a BLOCK_NOT_PRESENT state"; the phase 1 plan
   quotes it in full.

7. **Should dynamic VHDX honour `block_state_zero` too?**
   *Recommendation: no, not in this plan; record it.* qemu's default
   dynamic image writes `ZERO`-state entries and instar's writes
   `NOT_PRESENT`, and `qemu-img create -o block_state_zero=off` gives
   instar's bytes exactly. No reader instar or qemu ships distinguishes
   the two for a non-differencing image, the change would rewrite the
   bytes of every default `create -f vhdx` and every `convert -O vhdx`
   (and their pinned golden comparisons), and for convert it would turn
   a sparse BAT into a written one. The emitter this plan builds makes
   it a small change later. The divergence goes into
   `docs/quirks.md` and an issue, so the next reader of a hex dump does
   not rediscover it.

   Resolved (2026-10-21): recommendation adopted.

8. **Default `block_state_zero` for create: qemu's, or the
   fully-present one?** *Recommendation: qemu's (`on`).* It is what
   `qemu-img create -o subformat=fixed` produces at every version, the
   cross-version baselines compare against it, and a user who wants
   `FULLY_PRESENT` entries asks for `block_state_zero=off` exactly as
   they would with qemu-img. The consequence worth documenting is that
   a default fixed VHDX is all `ZERO` blocks, so a writer that honours
   them (qemu's) appends new data rather than writing in place; that
   is qemu's behaviour on qemu's own default image, not something
   instar introduced.

   Resolved (2026-10-21): recommendation adopted.

## Execution

Each phase gets its own detailed plan file before implementation
begins. The Plan column links each one once it is written and names
the file it will have until then. This table is the
tracking source of truth. The `Merged` column records what put each
phase on `develop` -- the merge commit of its pull request, or a
`first..last` range for a phase that landed directly -- because the
push-audit phase runs `PUSH-AUDIT.md` over the union of those ranges,
and `git diff develop...HEAD` is empty once the phases have landed. A
phase that lands in `instar-testdata` records `instar-testdata <sha>`
and is audited there; that repository takes direct commits to `main`
rather than merge requests.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Pin qemu's behaviour across the 80 static builds, build the structural VHDX comparator, correct `docs/create.md` | [PLAN-bat-population-phase-01-pin.md](/components/instar/plans/PLAN-bat-population-phase-01-pin/) | Complete | `fef62417` (#809) |
| 2. Fixed VHDX fixtures (instar-testdata + instar) | [PLAN-bat-population-phase-02-fixtures.md](/components/instar/plans/PLAN-bat-population-phase-02-fixtures/) | Complete | instar-testdata `d2f9467719` (direct to `main`); instar `5c5bba75` (#819) |
| 3. Host-only create: non-qcow2 preallocation parity and monolithicFlat | [PLAN-bat-population-phase-03-host-create.md](/components/instar/plans/PLAN-bat-population-phase-03-host-create/) | Complete | `a8d2f4ca` (#824) |
| 4. Read side: BAT entry count (#626), partial last block, `LeaveBlocksAllocated` and `resize` | [PLAN-bat-population-phase-04-read-side.md](/components/instar/plans/PLAN-bat-population-phase-04-read-side/) | In progress | |
| 5. VHDX geometry: default block size (#645), 64 TiB ceiling, one shared layout | PLAN-bat-population-phase-05-geometry.md | Not started | |
| 6. Streaming BAT emitter and the fixed `plan_vhdx` | PLAN-bat-population-phase-06-emitter.md | Not started | |
| 7. `create -f vhdx -o subformat=fixed`: guest streaming and host CLI | PLAN-bat-population-phase-07-create.md | Not started | |
| 8. `convert -O vhdx --subformat fixed` | PLAN-bat-population-phase-08-convert.md | Not started | |
| 9. `measure -O vhdx -o subformat=fixed` | PLAN-bat-population-phase-09-measure.md | Not started | |
| 10. Cross-version matrix and derived-profile validation | PLAN-bat-population-phase-10-matrix.md | Not started | |
| 11. Coverage and differential fuzzing | PLAN-bat-population-phase-11-fuzz.md | Not started | |
| 12. Documentation | PLAN-bat-population-phase-12-docs.md | Not started | |
| 13. Push audit: `PUSH-AUDIT.md` over every phase above | PLAN-bat-population-phase-13-push-audit.md | Not started | |

### Sequencing rationale

The order is: establish the oracle, fix what instar reads, fix the
geometry everything shares, then write. instar's merge queue costs
about 76 minutes of fan-out per pull request, so phases are sized to
one reviewable pull request each, and the two that are only tests
(10, 11) and the one that is only documentation (12) are kept
separate because they can be planned at medium effort and do not
block one another's review.

**Phase 1** comes first because it is the only phase that can
invalidate the rest. The Situation's qemu claims rest on a
twelve-version sample; phase 1 sweeps all 80 static builds for every
behaviour this plan copies (fixed VHDX structure in both
`block_state_zero` modes, at block-multiple and partial final
blocks; dynamic VHDX BAT state; the preallocation no-op and the
`metadata` error text for all three formats, including under the
fixed subformats; the default block-size thresholds; the
non-differencing BAT entry count at a region-crossing geometry; the
monolithicFlat descriptor and its `toolsVersion` boundary; the
monolithicFlat backing refusal; the 64 TiB ceiling message), and
commits the result as a pin file the tests can check against
(`tests/golden/qemu-create-pins.json`). It also builds the structural
VHDX comparator every later phase tests with (a small parser,
`tests/helpers/vhdx_structure.py`, shared with the differential
fuzzer), and corrects `docs/create.md:395` on its first commit, the
way `PLAN-differencing` phase 1 corrected the same page. The three
defects the survey found are already filed (#797, #798, #799), so
later phases have numbers to close.

**Phase 2** precedes any code because a reader with no fixed input
is a reader with no test. It commits five small qemu-written fixtures
to instar-testdata: fixed VHDX at a block multiple and at a partial
final block, each in both `block_state_zero` modes (the `off` pair
carrying data, because qemu turns an `on` image into a hybrid when it
writes to it), and the 8 MiB dynamic image at the #626 geometry,
which reproduces the miscount without the 128 GiB a fixed one would
cost through git-LFS. Each fixture's structure must equal a phase 1
pin case. The generator is `scripts/create-vhd-testdata.sh` in instar,
the house pattern from `PLAN-differencing` phase 2. A new test module
pins what instar's readers do with each fixture today, including the
#626 and #797 failures, in a form that fails once phase 4 fixes them.
Like `PLAN-differencing` phase 2 it spans both repositories, so its
`Merged` cell carries a record from each. Its survey moved the
`CREATE_CASES` work to phase 10 (see the phase 2 plan).

**Phase 3** is independent of every other code phase and is
host-only, so it goes early as the cheapest closure of most of the
preallocation leaf: accept `falloc` and `full` for vmdk, vpc and vhdx
in both the `-o` parser and `validate_create_args`, produce the `off`
image, refuse `metadata`, and do the same for `measure`'s `-o`
parser; plus monolithicFlat create (open question 2). It must not
touch the qcow2 or raw preallocation paths, which really allocate.
Its oracle is the phase 1 pin: every mode's exit status, bytes and
allocation must relate to `off` as qemu's do, with lengths taken from
instar's own `off` image, because #645 and the VHD CHS rounding make
instar's absolute lengths differ from qemu's. The cross-version info
baselines for the new cases join phase 10's, for the reason phase 2
moved the fixed VHDX ones there. Its survey found that convert names
a monolithicFlat extent `<stem>-flat.vmdk` whatever the output's
extension, where qemu keeps the extension (#820); the naming rule
create shares with convert is qemu's, so the phase fixes #820 (see
the phase 3 plan).

**Phase 4** fixes what instar reads before instar writes anything
new, for the reason `PLAN-differencing` phase 4 gave: the tree should
never be in a state where instar writes a file its own `check` calls
corrupt. Three changes, in this order:

* #626: one non-differencing BAT entry count, qemu's, defined once in
  the vhdx crate and used by `calculate_bat_layout`, `VhdxState::init`
  and the check op, which today each write it out. Because
  `calculate_bat_layout` also sizes the writers' BAT regions, this
  shrinks instar's region by 1 MiB at the geometries where the old
  count crossed a boundary, so create, convert, measure and resize all
  change at those sizes; the phase says so in its plan and its tests.
  The reader bound only gets looser, so every VHDX instar wrote before
  still loads.
* The partial-last-block check: accept a final `FULLY_PRESENT` block
  that extends past end of file by less than one block, as
  `block/vhdx.c:902-907` does, and keep refusing a block that starts
  past it.
* `LeaveBlocksAllocated`: parse it in the vhdx crate alongside
  `HasParent`, and have `resize` refuse a fixed VHDX (open question 6).
  `info` does not surface it, because `qemu-img info` does not.

Its survey found three things this paragraph did not know. The count
has a fourth copy, an independent one in the qcow2 crate's VHDX test
fixtures. `measure` was not in phase 2's read matrix, although the
success criteria name it, so it joins. And qemu's partial-last-block
allowance indexes by BAT entry rather than payload block, so past the
first chunk qemu refuses to open its own fixed `block_state_zero=off`
images (measured on all 80 builds at 4608.5 MiB with 1 MiB blocks).
instar's `check` judges by payload index, which is what qemu intends,
and `docs/quirks.md` records the divergence (see the phase 4 plan).

**Phase 5** puts the VHDX geometry in one place before the emitter is
written against it. Today the region layout is spelled out in
`plan_vhdx`, in `convert_to_vhdx`, in `measure_vhdx`'s comment and
formula, in `resize`, and in the host's `compute_output_capacity`;
fixed output adds a sixth reader of it (the data-start rule and the
file length). One layout function in the vhdx crate, consumed by all
of them, is the change that keeps fixed output from becoming the
next #626. On top of it: qemu's size-scaled default block size
(#645) in create, convert and measure, with the thresholds the
phase 1 sweep confirmed; and the 64 TiB ceiling. #645 changes the
default layout of every VHDX `create` and `convert` without
`block_size`, so `KNOWN_WRITER_DIVERGENCES` loses its three vhdx
entries in the same pull request, and the stale comments in
`tests/test_create.py`, `generate-baselines.py` and the differential
fuzzer's picker are corrected with it.

**Phase 6** is crate-only: the streaming BAT filler (fill one buffer
with the entries for an index range, given the layout and a state)
and a fixed subformat in `plan_vhdx` that returns the structure
writes plus the layout the guest streams the BAT from. Crate-level
tests compare the planned bytes against the phase 2 fixtures, field
by field, which is possible because qemu's structural fields are
deterministic. Splitting the crate from the guest wiring follows
`PLAN-differencing`'s emitter/guest split: the crate is testable
without a VM, the guest phase is not. Phase 4's survey found a
structural divergence this phase has to settle before the first
success criterion can hold: `build_region_table` writes the Required
bit as 1 for the BAT and metadata regions, and every pinned qemu
build writes 0.

**Phase 7** wires create end to end: the guest streams the BAT
through one slot and extends the file to `data_start +
virtual_size`, exactly as `emit_qcow2_l2_and_extend` does for qcow2
L2 tables; the host lifts both refusals, parses `block_state_zero`,
carries the subformat in `CreateConfig.vhd_subformat` and the state
in a new `flags` bit (no struct layout change), sizes the output
device from the layout, and refuses a backing file with
`subformat=fixed`. Integration tests compare instar's fixed images
with qemu's through the structural comparator, not only through
`qemu-img info`. This phase also measures the streaming cost
(open question 4) and records it. One trap from phase 4's survey:
with `block_state_zero=off`, a fixed image whose partial last block
lies past the first chunk is one qemu creates but cannot open again,
so for those sizes `qemu-img check`, `info` and `compare` cannot be
the oracle. Compare structure against the pin, and read back with
instar.

**Phases 8 and 9** carry the same layout into convert and measure.
Convert's fixed path uses the phase 6 filler for its BAT instead of
the dynamic path's in-memory table, writes payload in place, and
needs the host's `--subformat` validation opened to vhdx and its
capacity estimate replaced by the shared layout. Measure follows
convert because convert's output is measure's oracle: `required` and
`fully allocated` for a fixed target are both the fixed file length.
They are separate phases because they are separate operations with
separate test suites and reviewers, and measure is small. Convert's
in-place layout is the `block_state_zero=off` one, so phase 7's
warning about qemu-unreadable partial-last-block sizes applies to
phase 8 too. Phase 9 also corrects `docs/quirks.md`'s measure entry
for VHDX sources, which says instar treats every BAT block as fully
allocated. The reverse is true: qemu counts every block, and instar
counts no `ZERO` block (measured in phase 4's survey).

**Phase 10** is the cross-version matrix for everything above:
create and convert fixed cases across every profile (including the
vhdx fixed cases in `generate-baselines.py`'s `CREATE_CASES` and
instar's mirror of it, which phase 2's survey moved here because
adding them before instar can create fixed VHDX breaks
`test_create_cases_match_baselines` and they cannot see fixed-ness),
per-image cross-version baselines for phase 2's fixtures, the
preallocation and monolithicFlat create cases, and the
derived-profile validation the Situation warns about. It is separate from the feature phases because
regenerating baselines in instar-testdata and validating `profiles/`
against `raw/` is its own procedure, and because each feature phase
already carries the host-version tests that prove it works.

**Phase 11** extends `fuzz_create_emitters` to the fixed subformat
and the filler's invariants (every payload entry in range, every
sector-bitmap slot zero, offsets strictly increasing and 1 MiB
aligned, nothing past the region), adds a target for the filler at
arbitrary index ranges, and teaches the differential fuzzer's create
and convert pickers the fixed subformat, `block_state_zero`, the
non-qcow2 preallocation modes and monolithicFlat, comparing through
the structural comparator for VHDX.

**Phase 12** carries the cross-cutting documentation. Per-phase pages
change in their own phases (see the constraints below); phase 12 is
for `docs/format-coverage.md`'s output-side rows and notes,
`docs/quirks.md`'s divergences (dynamic `block_state_zero`,
`log_size`, convert's in-place fixed layout versus qemu's default),
`docs/format-internals.md`'s VHDX section, `ARCHITECTURE.md` if the
shared layout function changes the component description, and
`CHANGELOG.md`.

**Phase 13** is the push audit.

### Constraints that apply throughout

* Guest binaries stay under the 768KB per-operation cap (`make
  check-binary-sizes`). The create op was 65,224 bytes when last
  measured; convert's current size is for phase 8's survey to
  measure, since it is the larger binary and gains the filler.
* The vhdx and create crates are `no_std` and panic-free. Every
  offset and length an emitter computes is checked arithmetic; every
  value read back from an image is bounds-checked before use.
* No phase changes the call-table struct layouts. The fixed subformat
  and `block_state_zero` travel in existing `CreateConfig` fields, and
  convert's fixed bit in a spare `ConvertConfig.flags` bit. If a phase
  finds it cannot, it stops and brings that back to the operator
  rather than growing an ABI change inside a phase.
* Every qemu-img behaviour claim in a phase plan or brief is either
  cited to `instar-testdata/qemu-sources/` or measured, and every
  version boundary is measured with the static builds, never
  inferred. The management session re-measures a sample of any claim
  a sub-agent makes before it goes into a brief.
* Code, code comments and user documentation produced by this plan do
  not cite this plan's phase, step or decision numbers, or
  `PLAN-*.md` files. The reasoning is written out where it is needed.
  Plan files may cite each other freely.
* Every phase that changes user-visible behaviour updates the
  documentation describing it in the same pull request. Phase 12 is
  for the cross-cutting pages, not a licence to leave per-phase pages
  stale.
* Large-size tests stay under 16 TiB (ext4's file-size ceiling, which
  the CI runners should be assumed to share until checked), and fixed
  outputs in tests stay sparse: nothing in this plan should allocate
  the data region of a test image.

<!-- shared-block: plan-push-audit-phase v3 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did.
<!-- shared-block-end -->

!!! note "In this project"

    The Execution phases are the `Phase | Plan | Status` table each
    master plan carries, so the record goes in a `Merged` column
    added after `Status` — last, so that a row which omits it still
    reaches `Status`. Most phases here land as their own pull
    request and the cell holds that one merge commit; a phase that
    landed directly on `develop` records its `first..last` range
    instead. The audit phase itself runs `PUSH-AUDIT.md` over the
    union of those ranges rather than over `git diff develop...HEAD`,
    which is empty once the phases have merged.

    The obligation starts with plans written from here on, and with
    any plan still open that carries an audit phase. Plans that were
    already `Complete` when the rule arrived are not being backfilled,
    so a `Phase | Plan | Status` table with no `Merged` column is a
    plan that predates this, not a plan that has drifted from it.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

!!! note "In this project"

    instar does not track per-file human review: there is no
    `REVIEWS.md`, no `prune-reviews` workflow and no
    `docs/code-review-tracking.md`, so the third rule has nothing to
    act on here until that tracking arrives. The first two rules
    apply as written to the `Phase | Plan | Status | Merged` table
    each master plan carries: the next phase's first commit sets the
    finished phase's `Status` and `Merged` cells and its row in
    `docs/plans/index.md`, and the push-audit phase closes itself out.

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases involving deep protocol research, format-spec
    interpretation, or architectural decisions (call-table
    changes, new operations, new shared crates, security
    boundary changes) should be planned at high effort. Phases
    that are mechanical or follow well-established patterns can
    be planned at medium effort.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    The external references that push a step to high effort are
    format specs, the qemu-img source, and the KVM/virtio docs.
    The non-obvious constraints a brief should always state are
    memory layout, the 768KB guest binary cap, the `no_std`
    requirement of the format crates, and the call table
    boundary.

    Format-conversion work is the usual reason to need a
    large-context model: it tends to span the source format
    parser, the destination format writer, the call table, and
    the host-side glue at the same time.

    A worked brief for this codebase: instead of "add tests for
    the QCOW2 L2 parser", write "add tests for
    `parse_l2_entry()` in `src/crates/qcow2/src/lib.rs`. Use the
    adversarial fixtures in
    `instar-testdata/adversarial/qcow2/` (cluster boundary
    edges, OFLAG_COMPRESSED set with extended L2 cluster,
    refcount underflow). The function takes `(entry: u64,
    cluster_bits: u32)` and returns `Option<L2Entry>`."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] `make instar` builds and `make lint` is clean.
    - [ ] Guest binaries pass `make check-binary-sizes` (768KB
          limit per operation).
    - [ ] `make test-rust` and the relevant
          `make test-integration` targets pass.
    - [ ] `pre-commit run --all-files` passes.

### Specific to this plan

* **Planning effort.** Phases 1, 4, 5, 6 and 7 are high effort:
  phase 1 decides what every later phase copies, phase 4 changes what
  instar accepts from untrusted images, phase 5 moves a layout five
  consumers share, and phases 6 and 7 emit structures whose only
  external check is a structural comparison. Phase 8 is high effort
  for the convert writer's size and the guest/host boundary it
  crosses. Phases 2, 3, 9, 10, 11 and 12 can be planned at medium
  effort with good briefs.
* **Model choice.** Skew to opus for phases 4 to 8. Briefs for the
  emitter and the readers must state the exact entry encoding (state
  in bits 0-2, file offset in MiB units from bit 20), the interleave
  rule, qemu's data-start rule and the file-length rule, with their
  `block/vhdx.c` line citations, because a plausible wrong offset in a
  BAT is invisible to `qemu-img info`.
* **The oracle is structural.** No phase may treat an `info`
  baseline as evidence that a VHDX is fixed (Situation). Every test
  that asserts fixed output asserts it through the structural
  comparator or a direct BAT read.
* **A standing warning from this repository's history.** Agents
  assert plausible-but-wrong qemu-img capabilities; this repository
  has recorded it repeatedly, during `PLAN-format-coverage` and again
  in `PLAN-differencing`. Two assumptions this plan itself started
  from were corrected by measurement: that a fixed VHDX's BAT is
  fully present (by default every entry is `ZERO`), and that #626 is
  a benign writer-side looseness (it breaks reading qemu images).
  Require cite-or-measure for every claim, and re-measure a sample in
  the management session.
* **Read the binary you run.** A sub-agent measuring instar must say
  which binary it ran and the commit it was built from; this plan's
  own measurements used a build of `da9f043f` from the primary clone.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `instar create -f vhdx -o subformat=fixed IMG SIZE` produces an
  image structurally identical to `qemu-img create -f vhdx -o
  subformat=fixed` at the same size and block size -- region table,
  block size, File Parameters bits, every BAT entry, file length --
  in both `block_state_zero` modes, on every qemu version in the
  baseline matrix, and `qemu-img check` and `instar check` are both
  clean on it.
* `instar convert -O vhdx --subformat fixed` produces an image
  structurally identical to `qemu-img convert -O vhdx -o
  subformat=fixed,block_state_zero=off` of the same input, and
  `qemu-img compare` reports the two identical.
* `instar measure -O vhdx -o subformat=fixed` reports, as both
  `required` and `fully allocated`, the file length of what instar's
  own convert writes.
* A fixed VHDX of 2 TiB at 1 MiB blocks can be created and converted
  to without any guest buffer larger than one write, and the time it
  takes is recorded in the phase 7 plan.
* `-o preallocation=falloc|full` on vmdk, vpc and vhdx produces the
  same image as `off`, in both `create` and `measure`, and
  `metadata` is refused, matching qemu-img on every version measured.
* `instar create -f vmdk -o subformat=monolithicFlat` produces a descriptor and a sparse
  extent that `qemu-img info` reports equivalently to qemu's own,
  modulo `cid`.
* `instar check`, `convert`, `compare`, `map` and `measure` read a
  qemu-written VHDX at a #626 region-crossing geometry, and a fixed
  VHDX with a partial last block, without error; `resize` refuses a
  fixed VHDX by name rather than producing a hybrid.
* `create`, `convert` and `measure` use qemu's size-scaled default
  VHDX block size and refuse more than 64 TiB, and
  `KNOWN_WRITER_DIVERGENCES` carries no vhdx entry.
* #626, #645, #797, #798 and #799 are closed, or explicitly declined
  in writing in this plan.
* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (768KB limit).
* All Rust unit tests pass (`make test-rust`).
* All Python integration tests pass (`make test-integration`).
* `pre-commit run --all-files` passes.
* New format parsing is extracted into a shared crate under
  `src/crates/` where appropriate, and remains `no_std`
  compatible for guest use.
* Cross-validation against `qemu-img` is included for any new
  format or operation support.
* Documentation in `docs/` has been updated to describe the new
  features, and `docs/create.md` no longer says qemu-img does not
  emit fixed VHDX or that monolithicFlat needs multi-output-device
  support.
* `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and
  `CHANGELOG.md` have been updated as needed.
* The push audit has run `PUSH-AUDIT.md` over the union of the merged
  ranges, and its findings are resolved or declined in writing.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological order.
* **`order.yml`** — add an entry for the new master plan
  so it appears in the documentation navigation bar. Phase
  files should *not* be added to `order.yml`.

When all phases of a plan are complete, update the status
column in `index.md` to *Complete*.

This plan was registered in both files when it was written
(2026-10-20). Each phase plan is linked from its index row and from
the Execution table above as it is written, and is not added to
`order.yml`.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

* VMDK `twoGbMaxExtentSparse` and `twoGbMaxExtentFlat` output, the
  remaining leaf of the same goal. The streaming filler this plan
  builds is the shape a grain-table writer would want; the multiple
  output files are what it adds.
* A fixed-preserving VHDX `resize`: grow the BAT with `FULLY_PRESENT`
  entries, relocate it when its region must grow (the data region
  sits right behind it), and extend the file. Until then `resize`
  refuses fixed VHDX (open question 6).
* `block_state_zero` for dynamic VHDX, in create and convert (open
  question 7), once someone needs the bytes to match.
* `-o log_size` honoured rather than accept-ignored. It moves qemu's
  BAT, and so its data region, by the log's size.
* `convert -O vpc` fixed output. instar converts to dynamic VHD only;
  qemu converts to either.
* A failed `instar convert` leaves a zero-byte output file (seen on the
  #626 geometry). Measure what qemu-img leaves behind on the same
  failure before calling it a defect; if qemu removes or never creates
  the file, instar should match.
* `block_state_zero` on `convert`, if a user needs qemu's default
  (appended-data) convert-to-fixed layout rather than the in-place one
  (open question 3).
* Real identities for created VHD, VHDX and VMDK images (#566), which
  fixed VHDX inherits: a constant `DataWriteGuid`, and convert's
  constant VMDK `CID`.
* Create error messages that can name the value at fault (#580).
* The dynamic convert writer's in-memory BAT, which fails silently
  when the table does not fit between the staging buffer and
  `ALLOC_HEAP_BASE` (`src/operations/convert/src/main.rs:4456-4460`).
  Fixed convert avoids it by streaming; dynamic convert cannot stream
  in the same way because it learns block offsets as it goes, and a
  large dynamic BAT at a small block size is still refused with no
  message. Worth an issue of its own if phase 8's survey confirms the
  silent path is reachable from the CLI.
* qemu's DDB lines in instar's VMDK descriptors. Both the sparse
  writer and `build_flat_descriptor` omit `ddb.virtualHWVersion`,
  `ddb.geometry.*`, `ddb.adapterType` and (from qemu 6.2.0)
  `ddb.toolsVersion`, and accept-ignore `adapter_type`, `hwversion`
  and `toolsversion`. No reader uses them, but a byte comparison
  with qemu's descriptor sees the difference.
* Four test modules carry their own partial VHDX parsers
  (`tests/test_create.py:111-176`, `tests/test_info_safe.py:360-415`,
  `tests/test_differencing.py:2384-2530`, and a CRC-32C in
  `scripts/create-vhd-testdata.sh:1191`). Once phase 1's
  `tests/helpers/vhdx_structure.py` exists they could read through it.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

None fixed yet. Found while planning, and assigned to phases above:

* `docs/create.md:395` says VHDX `subformat=fixed` is "not emitted by
  qemu-img either". It is, on every version measured (Situation).
  Phase 1 corrects it on its first commit.
* #626 is a live read defect, not the benign over-declaration its
  body describes: instar refuses well-formed qemu-written VHDX images
  whose BAT exactly fills its region, reachable with qemu's defaults
  at 4161792 MiB. Its claim that the two rules differ only at exact
  multiples of `chunk_ratio` is also wrong: they differ by one entry
  at every geometry. Phase 4.
* `instar check` reports a qemu-written fixed VHDX with a partial last
  block as corrupt (#797). Phase 4.
* `instar resize` turns a fixed VHDX into a hybrid, because the vhdx
  crate never parses `LeaveBlocksAllocated` (#798). Phase 4.
* `instar create -f vhdx` accepts sizes above qemu's 64 TiB ceiling
  (#799). Phase 5.
* #645: the default block-size quirk, its `create.md` entries, the
  `KNOWN_WRITER_DIVERGENCES` comment and the differential fuzzer's
  picker docstring all state the two tools' defaults the wrong way
  round. Phase 5.
* `instar create`'s refusal of `-o subformat=monolithicFlat` gives a
  reason that is not true (multi-output-device support); convert
  already writes the format with one output device. Phase 3, which
  also adds the feature.
* qemu's partial-last-block allowance indexes by BAT entry, so qemu
  creates, by default and with no error, fixed `block_state_zero=off`
  images it then refuses to open. instar's `check` deliberately does
  not copy it. Phase 4 records the divergence; reporting it upstream
  is the operator's call.
* `build_region_table` writes the region table's Required bit as 1;
  qemu writes 0. Phase 6.
* `docs/quirks.md`'s measure entry for VHDX sources states the
  divergence backwards. Phase 9.
* Found by phase 3 and filed, outside this plan's scope: `convert`
  rounds a raw source's virtual size up to 64 KiB (#821); `check`
  refuses every monolithicFlat VMDK (#822); sparse VMDK descriptors
  name their extent `"output.vmdk"` (#823).
* Not assigned, noted for whoever owns `docs/format-coverage.md`: note
  10 says `qemu-img map` refuses dynamic VHDX. On 10.0.13 that holds
  for the human output format only; `qemu-img map --output=json`
  succeeds on the same images (measured on a qemu-created fixed and
  dynamic VHDX and on the `iotest-dynamic-1G.vhdx` fixture).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
