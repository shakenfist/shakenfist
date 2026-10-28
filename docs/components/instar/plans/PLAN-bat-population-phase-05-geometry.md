# Phase 5: VHDX geometry -- one shared layout, qemu's default block size, the 64 TiB ceiling

Phase 5 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation (especially "Two geometry divergences that fixed output
makes louder") and the phase 5 paragraph of "Sequencing rationale"
first. This file assumes them, and corrects that paragraph where the
survey found it incomplete.

Planning effort: high, as the master plan's guidance sets for a phase
that adds a shared crate API and changes the bytes of every VHDX
instar writes without `block_size`. The survey also found that the
default cannot be resolved in `measure` where the master plan assumed,
which makes this a design phase as well as a mechanical one
(decision 8).

## Decisions already taken by the operator

Every open question in the master plan was resolved with its
recommendation adopted (Michael Still, 2026-10-21). The ones this
phase carries out:

* **Open question 4:** the 64 TiB ceiling is enforced in the shared
  layout, before anything is written.
* **Open question 5:** close #645 and #799.

Neither is revisited here.

## Scope

In:

* **One VHDX layout function** in the vhdx crate, used by every
  writer: `plan_vhdx` (create), `convert_to_vhdx` (convert and dd),
  `measure_vhdx`, `resize`'s grow planner, and the host's output
  capacity estimate.
* **#645.** qemu's size-scaled default block size in create, convert,
  dd and measure.
* **#799.** The 64 TiB ceiling in create, convert, dd, measure and
  resize, refused by name on the host and enforced again by the
  layout in the guest.
* **#843** (filed by this survey). The layout rounds the virtual size
  up to 512 bytes, as qemu-img does, so `create` stops writing a
  `VirtualDiskSize` the spec forbids and `measure` sizes the same file
  convert writes.
* The tests and per-phase documentation all of that changes, and the
  differential fuzzer's create picker, which avoids the default today
  only because it diverged.
* **Phase 4's unaddressed review items** (step 5e, added at the back
  brief; see Execution notes): a test that breaks if a distro qemu
  fixes its BAT-index rule, phase 4's mutants kept only in scratch,
  check's region-overlap gap at a partial last block, a misleading
  overflow message, and one over-long documentation line.

Out:

* **Readers.** `VhdxState::init`, the check op and `resize`'s reading
  of the current image keep `calculate_bat_layout` and gain no
  ceiling. qemu enforces 64 TiB only when it creates an image, not
  when it opens one (`block/vhdx.c:1920`, qemu 7.0.0: the check sits in
  `vhdx_co_create`, and nothing on the open path refers to
  `VHDX_MAX_IMAGE_SIZE`). A reader that refused such an image would be
  a regression (decision 1).
* **The physical sector size.** instar writes 4096, and every pinned
  qemu build writes 512 (survey). It is a writer-structure question
  like the Required bit, so phase 6 decides it. This phase moves the
  value into the layout unchanged.
* **#844** (filed by this survey): `resize` accepts a size that is not
  a multiple of 512, on every format, where qemu-img refuses. That is
  a resize rule, not a VHDX one.
* **qemu's zero-size VHDX.** qemu creates a 0-byte VHDX with a 64 MiB
  BAT region because its no-parent count underflows at zero blocks
  (survey). instar does not copy it (decision 3). `create` keeps
  refusing size 0, and dd's `count=0` VHDX keeps its existing
  known limitation (`_SKIP_INFO_COMPARE` in `tests/test_dd_baselines.py`).
* **Fixed output, the BAT filler and the create host's output
  capacity hint.** Phases 6 and 7. A dynamic create never writes past
  its metadata region, so `virtual_size + 64 MiB` stays correct for
  dynamic output (survey).
* **Any call-table struct layout change.** Nothing here needs one.
  Any new result code is appended to an existing code list, which is
  not a layout change (phase 4 precedent).

## What the survey found

All references are to `origin/develop` at `6bafd318`. qemu claims are
cited to `instar-testdata/qemu-sources/7.0.0/` or were measured with
the static builds under `instar-testdata/qemu-img-binaries/x86_64/`.

### The layout is written out six times, not five

The master plan lists `plan_vhdx`, `convert_to_vhdx`, `measure_vhdx`,
`resize` and `compute_output_capacity`. Each computes its own copy:

| Site | What it writes out |
|------|--------------------|
| `plan_vhdx`, `src/crates/create/src/lib.rs:1368-1438` | block size range check; `LOGICAL_SECTOR_SIZE` 512, `PHYSICAL_SECTOR_SIZE` 4096, `METADATA_REGION_LEN` 1 MiB; `bat_off = 0x20_0000`; BAT region `div_ceil(MB_ALIGN) * MB_ALIGN`, **no** 1 MiB floor; metadata after it |
| `convert_to_vhdx`, `src/operations/convert/src/main.rs:4405-4447` | the same constants and offsets again, plus `log_offset = 0x10_0000`; BAT region floored to 1 MiB "because a zero-length BAT region is invalid"; `payload_start` after the metadata |
| `measure_vhdx`, `src/crates/measure/src/lib.rs:1010-1060` | `fixed_pre_bat = 2 * MB_ALIGN`; BAT region not floored; its own block size range check |
| `plan_grow`, `src/crates/resize/src/vhdx.rs:100` | the target count only (the grow relocates the BAT to end of file, so it has no fresh layout) |
| `compute_output_capacity`, `src/vmm/src/main.rs:13921-13935` | `round_up(vsize, 32 MiB) + 10 MiB`, whatever the block size |
| `target_can_address`, `src/operations/create/src/main.rs:650-671` | a sixth copy of the create default and its own `calculate_bat_layout` call |

`build_header` (`src/crates/vhdx/src/lib.rs:3311-3318`) holds the log
offset and length as literals too. Three sites disagree with each
other today: create has no BAT floor and convert does, and measure
predicts convert without the floor. The disagreement is reachable
only at a virtual size of zero, which `create` refuses and `measure`
of an empty source reaches.

### The default lives in five places, and one is not the create op

`32 * 1024 * 1024` as the VHDX default appears in
`vhdx::DEFAULT_BLOCK_SIZE` (`src/crates/vhdx/src/lib.rs:238`), both
create op branches (`src/operations/create/src/main.rs:566-570`,
`:664-668`), `ConvertConfig::output_block_size_vhdx`
(`src/shared/src/lib.rs:2826-2833`, which also silently substitutes it
for an out-of-range value), `VhdxOpts::default`
(`src/crates/measure/src/lib.rs:946-952`) and
`compute_output_capacity`. The master plan's "the convert host's
capacity estimate assumes it" is right, but convert's *output* block
size is chosen in the guest from `ConvertConfig`, not on the host.

**dd is a consumer the master plan does not name.** `instar dd -O
vhdx` writes through `convert_to_vhdx` (second call site,
`src/operations/convert/src/main.rs:1175`), so #645 changes its output
too. `tests/test_dd_baselines.py:142-157` carries two
`KNOWN_DD_DIVERGENCES` entries for the same divergence, and
`docs/dd.md:127` and `:175-183` document it.

### qemu's rules, measured at the byte

The phase 1 pin (`tests/golden/qemu-create-pins.json`) confirms every
threshold on all 80 builds. The source (`vhdx_co_create`,
`block/vhdx.c:1962-1974`) compares with a strict `>`. Measured on
6.0.0, 8.2.0 and 10.2.0 at the boundary and one sector above it:

| Size (bytes) | Block size |
|--------------|------------|
| 1073741824 (1 GiB) | 8 MiB |
| 1073742336 | 16 MiB |
| 107374182400 (100 GiB) | 16 MiB |
| 107374182912 | 32 MiB |
| 35184372088832 (32 TiB) | 32 MiB |
| 35184372089344 | 64 MiB |
| 70368744177664 (64 TiB) | 64 MiB |
| 70368744177665 and 70368744178176 | refused: "Image size too large; max of 64TB" |

Two details measured on 6.0.0 and 10.2.0 that the master plan does not
state:

* **qemu rounds the size up to 512 first.** `vhdx_co_create_opts`
  ("Silently round up sizes", `block/vhdx.c:2114-2121`): `create -f
  vhdx t 1073741825` reports virtual size 1073742336 and a 16 MiB
  block. Because every threshold is a multiple of 512, the
  comparisons give the same answer before or after rounding, so this
  matters for the size instar records, not for the default.
* **qemu leaves an empty file behind when it refuses.** The file
  protocol creates the image file before the driver checks the size,
  so a refused `create` leaves a zero-length `t.vhdx`.

### instar does not round, and #843 records it

`instar create -f vhdx t.vhdx 1073741825` (develop build of
2026-10-19) exits 0 and `info` reports virtual size 1073741825, a
`VirtualDiskSize` SPEC(VHDX) forbids. Convert already declares
`round_up(out_vsize, 512)` (`declared_512`, `:600-640`), so create and
convert disagree, and `measure_vhdx` sizes from the unrounded source
size while predicting convert. Filed as #843, in scope here because
the layout is where the size is decided.

`instar resize` on a VHDX (and on a qcow2, where qemu-img refuses
"The new size must be a multiple of 512") accepts 33554433 and records
it. Filed as #844, out of scope (see Scope).

### qemu's zero-size VHDX is an underflow

`qemu-img create -f vhdx z.vhdx 0` (10.2.0) writes a 64 MiB BAT region
at 2 MiB and metadata at 66 MiB. `vhdx_calc_bat_entries`
(`block/vhdx.c:868-882`) computes `data_blocks_cnt +
((data_blocks_cnt - 1) >> chunk_ratio_bits)` in `uint32_t`, which at
zero blocks is `0xFFFFFFFF >> 9` = 8388607 entries. That is a qemu
defect, not a layout to copy.

### measure cannot resolve the default where the master plan puts it

`measure`'s guest computes the target unit size before it scans the
source (`target_unit_size_for(config)`,
`src/operations/measure/src/main.rs:117`, `:320-330`), because the
scanner counts target units with data (#286). A size-scaled default
needs the source's virtual size, which only the scan's format arm
knows. Each arm of `detect_and_scan` (`:377` on) learns the virtual
size before it calls `scan_allocation`, so the unit can be resolved
there (decision 8). The `--size` path (`virtual_size_override`,
`:123-128`) knows its size up front.

### The capacity estimate is too small at small block sizes

`round_up(vsize, 32 MiB) + 10 MiB` assumes the pre-payload area is
under 10 MiB. At 1 MiB blocks the BAT region alone passes 7 MiB from
about 896 GiB of virtual size, so a fully allocated source that large
converted with `--block-size 1M` runs out of output device before it
finishes. No test reaches it, because the tests' sources are sparse.
Sizing from the layout fixes it (decision 7).

### The comments the master plan calls stale

* `tests/test_create.py:2146-2151` (`KNOWN_WRITER_DIVERGENCES`) and
  `scripts/differential-fuzz.py:1587` and `:1640-1642` state the
  defaults backwards ("instar 8 MiB vs qemu 32 MiB"), as #645 says.
* `generate-baselines.py` lives in instar-testdata, not instar. Its
  only VHDX comment (`scripts/generate-baselines.py:455` at
  instar-testdata `4512a835e9`) says "vhdx is dynamic-only in instar;
  qemu defaults match". That is wrong today and becomes true when this
  phase lands. **This phase therefore needs no instar-testdata
  commit.** Phase 7 corrects "dynamic-only".
* `tests/test_convert.py:6534` calls 32 MiB "the explicit default".
* `tests/test_measure.py:1069` says "vhdx default block_size is 32 MiB".
* `docs/quirks.md:1248-1256`, `docs/create.md:62`, `:437-441`,
  `:460-461` and `:512-516`, `docs/convert.md:53` and `:96-97`,
  `docs/format-coverage.md:336`, `:495` and `:503`, and `docs/dd.md:127`
  and `:175-183` all state or rely on the 32 MiB default.
* `src/operations/create/src/main.rs:558` cites
  `docs/plans/PLAN-differencing.md` in landed code. That function is
  rewritten here, so the pointer goes with it, replaced by the
  reasoning it stands for.

### Found, and assigned elsewhere

* **Physical sector size.** `plan_vhdx` and `convert_to_vhdx` write
  4096. Every pinned qemu case records 512. The master plan's first
  success criterion does not list the field, but phase 6 is where
  instar's structure is matched to qemu's field by field. Recorded in
  the master plan's phase 6 paragraph.

### Master plan corrections made in this commit

* The phase 5 paragraph of "Sequencing rationale" now names dd as a
  consumer, records that measure resolves the default after it learns
  the source size, that the layout rounds the size (#843), that the
  capacity estimate was too small at small block sizes, and that
  `generate-baselines.py` needs no change.
* The phase 6 paragraph gains the physical sector size.
* "Bugs fixed during this work" records phase 4's fixes and this
  survey's findings (#843, #844, qemu's zero-size underflow).
* The Execution table and the `docs/plans/index.md` row link this
  file.

## Decisions

1. **One function, in the vhdx crate, for writers only.** Add
   `pub struct VhdxLayout` and `pub fn layout(virtual_size: u64,
   block_size: u32, has_parent: bool) -> Result<VhdxLayout,
   LayoutError>` to `src/crates/vhdx/src/lib.rs`. `block_size == 0`
   means "qemu's default for this size" (decision 4). The struct
   carries everything a writer places: the rounded `virtual_size`, the
   resolved `block_size`, `chunk_ratio`, `payload_blocks`,
   `bat_entries`, `log_offset` and `log_length` (1 MiB each),
   `bat_offset` (2 MiB), `bat_region_len`, `metadata_offset`,
   `metadata_region_len` (1 MiB), `data_start` (end of the metadata
   region), and the logical and physical sector sizes (512 and 4096,
   decision 6). `LayoutError` is `InvalidBlockSize`, `TooLarge` and
   `Overflow`, all documented. All arithmetic is checked; the crate
   stays `no_std` and panic-free.

   `calculate_bat_layout` stays, as the count primitive, for the
   readers and for `layout` itself. Readers do not call `layout`,
   because the ceiling and the rounding are rules for writing an image,
   and qemu opens images it would not create. Every writer calls
   `layout` and stops computing offsets of its own. The offsets in
   `build_header` become named constants that `layout` returns too, so
   the header and the region table cannot disagree.

   The reasoning a reviewer may dispute: a single function could have
   applied the ceiling to readers as well, "to be safe". It would not
   be safe. It would refuse images qemu and Hyper-V open, and phase 4
   showed what refusing well-formed images costs (#626).

2. **The block size range check moves into `layout`.** Power of two,
   1 MiB to 256 MiB (`VHDX_BLOCK_SIZE_MIN`/`MAX`, `block/vhdx.h:280-281`).
   `plan_vhdx`, `measure_vhdx` and `resize` keep no copy of their own.
   Host-side option validation stays, because it gives a message
   before a VM boots.

3. **The BAT region is never shorter than 1 MiB.** That is convert's
   rule today, whose comment records that qemu rejects a file with a
   zero-length BAT region (not re-measured here; 5a's brief asks for
   it). It only applies at a virtual size of zero. qemu's 64 MiB
   region there is an underflow (survey), not a layout. `create`
   keeps refusing size 0, and `measure` of an empty source now
   predicts convert's floored region, which it did not before.

4. **qemu's default, as one function.** `pub fn default_block_size(
   virtual_size: u64) -> u32`: 64 MiB above 32 TiB, 32 MiB above
   100 GiB, 16 MiB above 1 GiB, otherwise 8 MiB, each a strict `>` as
   in `vhdx_co_create` (`block/vhdx.c:1962-1974`), on the rounded size.
   `vhdx::DEFAULT_BLOCK_SIZE` is removed rather than kept as "the
   default for small images", because a constant named DEFAULT is how
   this defect survived. `ConvertConfig::output_block_size_vhdx`
   returns 0 for unset rather than 32 MiB, and stops substituting a
   value for an out-of-range one: an invalid block size reaching the
   guest is refused by `layout`, not silently replaced. `VhdxOpts`'s
   `Default` sets 0 for the same reason.

5. **The ceiling is 64 TiB, refused on the host first.** `pub const
   MAX_VIRTUAL_SIZE: u64 = 64 << 40`, refused when the rounded size is
   above it. The host refuses up front wherever it knows the size:
   `create` with an explicit size, `convert` and `dd` from the output
   virtual size, `measure` with `--size`, and `resize` from the
   requested size. The message names the limit and the size:
   `create: virtual size 71468255805440 exceeds the VHDX maximum of
   64 TiB (70368744177664 bytes)`, with the command's own prefix. The
   guest's `layout` refusal is the backstop for what the host cannot
   see (a `create -b` that takes its size from a parent, a measured
   source). It maps to the existing invalid-size result code of each
   operation, unless an operation's existing code would say something
   untrue (measure's `ERROR_INVALID_SIZE` reads "not an image measure
   can read"). Then a code is appended, following phase 4's
   `ERROR_FIXED_VHDX`.

   instar refuses before it creates the output file, so unlike qemu it
   leaves nothing behind. That is a deliberate divergence, recorded in
   `docs/quirks.md`.

6. **The physical sector size stays 4096 in this phase.** It moves
   into `layout` unchanged. Changing it would alter the bytes of every
   VHDX instar writes, in a phase about block size and limits, and
   phase 6 is matching structure field by field anyway.

7. **The host's capacity estimate comes from the layout.**
   `compute_output_capacity` takes the requested block size as well
   (0 for the default) and, for VHDX, returns `data_start +
   payload_blocks * block_size` from `vhdx::layout`. That is the
   largest file convert's packed writer can produce. A size `layout`
   refuses has already been refused by the host (decision 5), so the
   estimate falls back to today's formula only for a size the host
   has not checked, and says so in a comment.

8. **measure resolves the block size once it knows the source size.**
   `target_unit_size_for` takes the virtual size, and `detect_and_scan`
   calls it from inside each format arm, after the arm has the size
   and before `scan_allocation`. The `--size` path resolves it from the
   override. The block size measure reports, and the one
   `measure_vhdx` sizes with, are that same resolved value: measure
   resolves it once and passes it on, instead of resolving it twice
   and hoping both agree. Only the VHDX target's unit depends on the
   size. qcow2, vmdk and vhd keep their current units, and a test
   proves they are unchanged.

9. **Four steps, ordered so each change is attributable.** 5a adds the
   crate API with no caller. 5b moves every writer onto it, passing
   today's 32 MiB explicitly where no block size was given, so the
   default is unchanged; the guest-side ceiling and the rounding
   become live there. 5c adds the host refusals and their tests and
   documentation (#799, #843). 5d removes the explicit 32 MiB and turns
   on qemu's default (#645), with every test and page that names it.
   After 5b, any change to an integration test's output is either a
   bug in 5b or the rounding of an odd size, and the step report must
   say which.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a | high | opus | none | **One VHDX layout function in the vhdx crate.** First read decisions 1 to 6 and the survey sections "The layout is written out six times", "qemu's rules, measured at the byte" and "qemu's zero-size VHDX is an underflow" in `docs/plans/PLAN-bat-population-phase-05-geometry.md`. In `src/crates/vhdx/src/lib.rs`, beside `calculate_bat_layout` (`:3840`), add `MAX_VIRTUAL_SIZE`, `default_block_size`, `VhdxLayout`, `LayoutError` and `layout` exactly as decisions 1 to 5 specify, with named constants for the log offset and length (1 MiB each), the BAT offset (2 MiB), the metadata region length (1 MiB) and the two sector sizes (512 and 4096). Use the log constants in `build_header` (`:3311-3318`). Doc comments cite qemu 7.0.0 under `instar-testdata/qemu-sources/` by function name: `vhdx_co_create` for the default thresholds and the ceiling, `vhdx_co_create_opts` for the 512 rounding, `vhdx_create_new_region_table` for the region placement (`ROUND_UP(VHDX_HEADER_SECTION_END + log_size, MiB)` for the BAT, the metadata right after it at 1 MiB), and `vhdx_calc_bat_entries` for why the zero-size floor does not copy qemu. Do not remove `DEFAULT_BLOCK_SIZE` yet (5d does). Do not change any caller. Unit tests: `default_block_size` at each size in the survey's table and one sector either side; `layout` at every `vhdx-dynamic-*` case in `tests/golden/qemu-create-pins.json` with `block_size = 0`, asserting `block_size` and both regions' offsets and lengths equal the pin (write the expected values into the test with a comment naming the pin case; the crate tests cannot read the JSON); `vhdx-dynamic-131041M-block-1M` with an explicit 1 MiB block; 64 TiB accepted and 64 TiB + 1 and + 512 `TooLarge`; 1073741825 rounded to 1073742336; size 0 has a 1 MiB BAT region (and confirm with the host `qemu-img info` that a file with a zero-length BAT region is refused, as convert's comment says, reporting what you measured); block sizes 0x80000, 3 MiB and 512 MiB `InvalidBlockSize`; `u64::MAX` and `(1 << 53)` do not panic; with `has_parent` the BAT region matches `calculate_bat_layout(.., true)`; `data_start == metadata_offset + metadata_region_len`. Mutation run (each under `timeout`): `>=` for `>` in each threshold; drop the rounding; drop the floor; `>=` in the ceiling; each must be killed by a named test. Commit subject: `Lay out a VHDX in one place.` |
| 5b | high | opus | none | **Move every VHDX writer onto the shared layout, default unchanged.** First read decisions 1, 2, 3, 6, 7 and 9 and the survey's "The layout is written out six times" and "The capacity estimate is too small at small block sizes". Behaviour must not change except where this brief says. Where a caller has no block size today it passes `32 * 1024 * 1024` explicitly, through one temporary named constant in each crate or op that 5d removes. Sites: `plan_vhdx` (`src/crates/create/src/lib.rs:1368-1438`: take offsets, lengths, sector sizes and the rounded virtual size from `layout`; write `layout.virtual_size` as `VirtualDiskSize`; drop its own block size check; map `LayoutError` to `CreateError`, `TooLarge` to `InvalidVirtualSize`); the create op (`src/operations/create/src/main.rs`: `vhdx_opts_from` `:548-575`, replacing the `docs/plans/PLAN-differencing.md` pointer at `:558` with the sentence it stands for, and the VHDX arm of `target_can_address` `:650-671`, which becomes a `layout(..).is_ok()` call); `convert_to_vhdx` (`src/operations/convert/src/main.rs:4405-4447`: all offsets, the floor and the sector sizes from `layout`, reporting `LayoutError` through `send_error` with a message per variant instead of the one "VHDX BAT layout overflow"); `measure_vhdx` (`src/crates/measure/src/lib.rs:1010-1060`: `payload_start` is `layout.data_start`, and the block size check goes; this changes measure's answer at virtual size 0 only, by decision 3, so update or add the test that pins it); `plan_grow` (`src/crates/resize/src/vhdx.rs:100`: the target count from `layout(new_virtual_size, opts.block_size, false)`, so a grow past 64 TiB is refused as `InvalidNewVirtualSize`); and `compute_output_capacity` (`src/vmm/src/main.rs:13921-13935`, caller `:14398`; decision 7: add the block size parameter, pass `args.block_size`, and size VHDX output from `vhdx::layout`, keeping the old formula only as the fallback the decision describes). Update the capacity unit tests (`:22457` on), adding 2 TiB at 1 MiB blocks, which must now exceed `2 TiB + 10 MiB`. Then grep `src/` (not `src/crates/vhdx`) for `0x20_0000`, `0x10_0000`, `2 * MB_ALIGN`, `MB_ALIGN.checked_mul(2)` and `4096` in VHDX code, and report any VHDX layout constant left outside the vhdx crate. Tests: `make test-rust` (create with its feature flags); stestr `test_create`, `test_convert`, `test_measure`, `test_dd_baselines`, `test_resize`, `test_vhdx_fixed_fixtures`, `test_vhdx_structure` and `test_check_formats`, each reporting "Ran: N"; any test whose expected output changes is reported with the reason (only virtual size 0 in measure, or an odd size's rounding, is acceptable); `make check-binary-sizes`, reporting the create, convert, measure and resize op sizes before and after. Mutation run: make `compute_output_capacity` ignore the block size (the 2 TiB test must fail); remove the floor from `layout` (a convert or measure test must fail; name it). Commit subject: `Size every VHDX writer from the shared layout.` |
| 5c | medium | opus | none | **Refuse a VHDX larger than 64 TiB, and round its size (#799, #843).** First read decisions 5 and 8 and the survey's "qemu's rules, measured at the byte" and "instar does not round". The guest backstops are live since 5b. Add the host refusals of decision 5, each before the output file is opened or created: create (`validate_create_args`, `src/vmm/src/main.rs:19693`, for an explicit size with `-f vhdx`), convert and dd (beside the VHDX block size validation at `:14071`, from the output virtual size), measure (`run_measure` `:15833`, for `--size` with `-O vhdx`), and resize (for a VHDX target size; find where the host knows the image format). Every message has the form in decision 5, uses `vhdx::MAX_VIRTUAL_SIZE` rather than a literal, and is built by one helper. Check what each guest maps `LayoutError::TooLarge` to, and confirm by running it that the message the host then prints is true: craft the case by `instar create -b` of a VHDX parent whose `VirtualDiskSize` item is patched to 65 TiB (the metadata region has no checksum; build the parent at 64 TiB dynamic and patch the 8-byte item at the offset `tests/helpers/vhdx_structure.py` reports), and by `instar measure -O vhdx` of a 65 TiB sparse qcow2. If measure's existing code would misdescribe it, append `MeasureResult::ERROR_TOO_LARGE` following `ResizeResult::ERROR_FIXED_VHDX` (shared code list, its pin test, the host constant, the message, the code-range test). Integration tests (new class in `tests/test_create.py`, and beside the existing refusals in `tests/test_convert.py`, `tests/test_measure.py`, `tests/test_resize.py`): `create -f vhdx` at 64 TiB succeeds (sparse dynamic output; assert its apparent file length is under 32 MiB), and at 64 TiB + 1 and + 512 fails with the message and leaves no file; convert and measure `-O vhdx` of a 65 TiB sparse qcow2 source fail with the message (convert leaves no output file); resize of a 64 TiB VHDX to 65 TiB fails and leaves the file byte-identical (sha256); `info` and `check` on the patched 65 TiB parent above still succeed, because readers have no ceiling (decision 1); `create -f vhdx t 1073741825` reports virtual size 1073742336 in `instar info` and in host `qemu-img info` (skip without qemu-img), and a 16 MiB block in neither case (that is 5d: assert only the size here). Documentation: `docs/create.md`, `docs/convert.md`, `docs/dd.md`, `docs/measure.md` and `docs/resize.md` each state the ceiling where they list VHDX's limits; `docs/quirks.md` gains one entry recording the ceiling, that instar leaves no file where qemu leaves an empty one, and the 512 rounding for create and measure. Commit subject: `Refuse a VHDX larger than 64 TiB.` with `Fixes #799.` and `Fixes #843.` in the body. |
| 5d | high | opus | none | **Default the VHDX block size the way qemu does (#645).** First read decisions 4 and 8 and the survey's "The default lives in five places", "measure cannot resolve the default where the master plan puts it" and "The comments the master plan calls stale". Remove `vhdx::DEFAULT_BLOCK_SIZE` and the temporary 32 MiB constants 5b added, so every writer passes 0 and `layout` resolves the default: `plan_vhdx` and the create op; `ConvertConfig::output_block_size_vhdx` (`src/shared/src/lib.rs:2826-2833`, decision 4: 0 when unset, no substitution) and both `convert_to_vhdx` callers (`src/operations/convert/src/main.rs:623`, `:1175`, so dd is covered); `VhdxOpts::default` (`src/crates/measure/src/lib.rs:946-952`); and the measure op per decision 8 (`src/operations/measure/src/main.rs:117`, `:307-330`, `:377` on). Replace the vhdx crate's tests that used the constant with explicit values. Correct the host's help strings (`src/vmm/src/main.rs:5084`, `:7393`, `:7493`). Tests: in `tests/test_create.py`, remove the three vhdx entries from `KNOWN_WRITER_DIVERGENCES` (`:2146-2151`) with their comment; add a test that creates a dynamic VHDX with no block size at each size in the survey's table up to 64 TiB, and compares the region tables and block size with the pin case of the same size through `helpers.qemu_pins` and `tests/helpers/vhdx_structure.py` (all are sparse; the largest file is 16 MiB); in `tests/test_dd_baselines.py`, remove the two vhdx `KNOWN_DD_DIVERGENCES` entries and the comment above them, and correct the module docstring (`:31`); in `tests/test_convert.py`, add a test that `convert -O vhdx` with no `--block-size` of a 1 GiB and a 1 GiB + 512 sparse raw source reports 8 MiB and 16 MiB in `qemu-img info`'s `cluster-size`, and correct `test_vhdx_block_default_explicit`'s docstring (`:6534`); in `tests/test_measure.py`, correct the comment at `:1069`, keep the measure-versus-convert round-trip tests passing (they are the oracle that measure and convert resolved the same default), and add a test that `measure -O qcow2`, `-O vmdk` and `-O vpc` of a fixed source give the same answer as before this step (record the before values from a build of the 5c commit). Differential fuzzer: correct the docstring (`scripts/differential-fuzz.py:1587`) and let the vhdx create picker (`:1640-1645`) also draw no `block_size`. Documentation: rewrite `docs/quirks.md`'s "vhdx default `block_size` differs from qemu-img" (`:1248`) as a statement that instar now uses qemu's size-scaled default, with the thresholds; correct `docs/create.md` `:62`, `:437-441`, `:460-461` and the example at `:512-516`, `docs/convert.md` `:53` and `:96-97`, `docs/format-coverage.md` `:336`, `:495` and `:503`, and `docs/dd.md` `:127` and `:175-183`; and add a `CHANGELOG.md` entry, because the default output of create, convert, dd and measure changes. Run the 5b test list plus `test_differencing` and the fuzzer's own tests, each reporting "Ran: N". Mutation run: resolve measure's unit before the scan with the override size of 0 (a measure test must fail); swap two thresholds; leave convert's default at 32 MiB (the convert test must fail). Commit subject: `Default the VHDX block size the way qemu does.` with `Fixes #645.` in the body. |
| 5e | medium | opus | none | **Phase 4's review leftovers.** First read the Execution notes of `docs/plans/PLAN-bat-population-phase-05-geometry.md` and the review at https://github.com/shakenfist/instar/pull/835#pullrequestreview-5647000536 (items 2, 3, 4, 5 and 9). Five independent fixes; report which files belong to which. (1) Review item 4: `test_qemu_cannot_open_its_partial_block_past_the_first_chunk` (`tests/test_vhdx_fixed_fixtures.py:456`) requires the host `qemu-img check` to exit 2. Keep that as the expected result, but when the host qemu exits 0 call `skipTest` with a message saying the host qemu-img now opens the image and the `docs/quirks.md` entry may be obsolete; any other exit status still fails. (2) Review item 5: add phase 4's three mutants to `tools/mutate-differencing.sh`, following its `rust_case` (`:456`) and `integration_case` (`:555`) helpers: replacing the `block_len` clamp in check's `FULLY_PRESENT` arm (`src/operations/check/src/main.rs:1835` on) with `block_size` (killed by the `vhdx-fixed-17M-bsz-off-data` check test), using the BAT index in place of `payload_block_idx` for the clamp (killed by the 4608.5M check test), and `(n - 1) / c` in `vhdx::bat_entry_count` (killed by `bat_entry_count_at_group_edges`). Run each new case through the script, under `timeout`, and report the results. (3) Review item 2: the declared-region overlap test (`:1926`) uses the short `block_len`, so a region placed in the unused tail of a partial last block's slot is not flagged, and a later dynamic grow could map data onto it. Use `block_size` for the overlap test alone, keeping `block_len` for the end-of-file bound, with a comment saying why the two differ. Add an integration test with a crafted image (a scratch copy of a partial-last-block fixture whose region table, with its checksum recomputed, declares a region inside that tail; `tests/helpers/vhdx_structure.py` may help) that `instar check` must report as corrupt, and a mutant (`block_len` back in the overlap test) that it kills; add that mutant to the script too. (4) Review item 3: the count-overflow arm (`:1685-1695`) prints the same "VHDX BAT region too small" as the undersized case (`:1699-1704`); give it its own message saying the BAT entry count overflows. (5) Review item 9: rewrap note 8 in `docs/format-coverage.md` (`:208-212`) to the width of the notes around it. Run `make test-rust`, stestr `test_vhdx_fixed_fixtures` and `test_check_formats` (reporting "Ran: N"), and the pre-commit and partition checks. Commit subjects (the management session splits them): `Skip, not fail, if qemu opens its own partial block.`, `Keep the VHDX read-side mutants in the script.`, `Check region overlap against the whole last block.` (with the overflow message), and `Rewrap note 8 in format-coverage.md.` |

The management session runs the steps in order. Every step edits
`src/crates/vhdx/src/lib.rs` or its consumers, and 5d's tests depend on
5c's helpers. Every brief also carries the session's standing rules:

* Build only with `make instar` (Docker); never install a Rust
  toolchain on the host.
* Run `make test-rust`, redirecting cargo's target dir to an owned
  directory if pre-commit has left `src/target` root-owned. A
  `create` crate change needs `cargo test -p create` with its feature
  flags as the Makefile runs it.
* Do not run `make test-rust` at the same time as a mutation run.
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
* Tests stay under ext4's 16 TiB file-size ceiling by staying sparse:
  no test allocates a data region.
* Leave the work uncommitted.

## Execution notes

* **Back brief, 2026-10-28 (AEDT).** The operator kept decision 9's
  split, with the temporary 32 MiB constant from 5b to 5d, and allowed
  the management session to push the branch and open the pull request
  once the definition of done passes. The steps run in order, one opus
  agent each.
* **Step 5e added at the back brief.** The session that implemented
  phase 4 reported that #835 merged with five of its automated
  review's consider items unaddressed and unrecorded (items 2, 3, 4,
  5 and 9 of
  https://github.com/shakenfist/instar/pull/835#pullrequestreview-5647000536).
  The operator chose to take all five here, as 5e after 5d. They are
  read-side and test hygiene, not geometry, which is why they are a
  step of their own.
* **Phase 4's skip note corrected at source.** Its 4a note blamed the
  324 `test_measure` "no baseline meta" skips on a missing 10.0.0
  measure profile. Every one of the 80 versions holds the same 132
  qcow2-target measure baselines (counted in instar-testdata). The real
  cause is fixtures with no measure baseline at any version, tracked
  as #845. The phase 4 plan now says so.
* **5a.** The management session cross-checked the eleven pin
  entries the agent copied into the crate tests against
  `tests/golden/qemu-create-pins.json`, and all match. The agent also
  measured the zero-size case: qemu refuses a zero-length BAT region
  at any size, and also a zero-size image with a 1 MiB region, because
  its count underflows to 8388607 entries. So convert's comment that
  qemu's own `count=0` VHDX is a valid header-only image was wrong.
  Decision 3 stands, and 5b deletes the comment.
* **5b.** Behaviour-neutral against a build of `6bafd318`: `cmp`
  finds `create` at 1G, `create` at 64M with 1 MiB blocks and a sparse
  `convert` identical, and `create 1073741825` different in only 4
  bytes. Those are `VirtualDiskSize` and the Virtual Disk ID, which
  `build_metadata` derives from the size (and from the block size, so
  5d moves it for every defaulted image too). The agent departed from
  the brief in three places, all kept:
  * `resize` checks the grown size with `layout`, but counts BAT entries
    by the image's own logical sector size when that is not 512,
    because `layout` counts for the 512 instar writes. It also refuses
    block size 0, which `layout` would read as "the default".
  * `target_can_address` refuses only `TooLarge` and `Overflow`, so an
    invalid block size still reaches the planner's own error, not
    "backing file too large".
  * `measure` maps `TooLarge` to `InvalidSize`, which the host prints as
    an unsupported source format. 5c must fix that message (decision 5
    anticipated it).

  The 16 `test_convert` failures under a load average of about 18 were
  qcow2, vmdk and vpc re-encodes and VHD compares. All 16 passed when
  replayed alone.
* **5c.** Measure gained `MeasureResult::ERROR_TOO_LARGE = 5`, as
  decision 5 allowed, because its existing code read as an unreadable
  source. The agent's mutation run, with each host refusal removed,
  found that the convert and dd guest backstop exited 0 and kept an
  empty output file. Its layout refusal sent an error but no failed
  completion, as the overflow path had before 5b. Fixed. Exit 1 still
  leaves the empty file, because the host does not clean up after a
  guest failure in general, which is out of scope. Two more fixes went
  beyond the brief, kept because each makes a printed message true:
  `create -b` of an over-limit VHDX parent now names the VHDX maximum
  instead of advising a larger cluster size, and `create`'s `Created:`
  line reports the rounded size. The management session reproduced
  every host refusal's message, `create` at 64 TiB (a 20 MiB sparse
  file), and resize leaving a 64 TiB image unchanged. A measure of a
  scanned source prints its message twice, once from the guest and
  once from the host, which is the existing pattern for every guest
  measure failure (checked against the `6bafd318` build). 4
  `test_convert` failures at load averages of 28 to 40 all passed when
  replayed alone.
* **5d.** The management session confirmed `instar info` and the host
  `qemu-img info` agree on `cluster-size` at all seven sizes of the
  survey's table, for images instar created with no block size.
  `measure` resolves its unit once: `detect_and_scan` returns it with
  the summary, from each format arm's virtual size, or from the
  override for `--size`. That one value feeds the calculator and the
  report, and it is 0 for raw and a fixed VHD target exactly as before.
  21 `measure` answers for the qcow2, vmdk and vpc targets, recorded
  with a build of `b924aa34`, are unchanged and now pinned. The
  convert test checks `cluster-size` only, not `virtual-size`, because
  a raw source that is not a multiple of 64 KiB is rounded (#821). The
  create test runs all nine dynamic pin cases. measure's guest binary
  grew from 38048 to 39384 bytes. 8 `test_convert` failures (qcow2
  re-encodes) passed when replayed alone. `test_differencing` ran with
  libvhdi present and `INSTAR_REQUIRE_LIBVHDI=1`.
* **5e.** Three commits, not four. The new overlap mutant and the
  three phase 4 mutants share `tools/mutate-differencing.sh`'s
  enforced case count and `docs/testing.md`'s case list, so splitting
  them would have meant a hand-edited count in a state no test ran.
  The overlap fix and the script cases therefore land together. Item 5
  of the review quoted `(n - 1) / c` as the mutant, but that is the
  current, correct rule, so the mutant restores the old `n / c`. The
  crafted image appends a 1 MiB unrecognised, not-required region at
  25 MiB, inside the unused tail of `vhdx-fixed-17M-bsz-off-data`'s
  last 8 MiB slot, with both region tables' CRC-32C recomputed. A
  control test with the region at 32 MiB, where the slot ends, must
  pass, and it was kept. All six affected script cases (the four new
  ones and the two whose search string moved) are killed.
* **Definition of done, 2026-10-28 (AEDT).** Checked by the management
  session on a fresh `make instar` at `3d2d3f6c`. Every item passes:
  * The `DEFAULT_BLOCK_SIZE`, layout-literal, stale-claim and
    plan-reference greps print nothing. The `32 * 1024 * 1024` grep
    prints only QCOW2 scratch constants and explicit block sizes inside
    `#[cfg(test)]` modules and crate integration tests, none of them a
    VHDX default.
  * `cluster-size` at all seven threshold sizes is as the survey's
    table says, and `qemu-img info` agrees.
  * 64 TiB + 1 is refused with the 64 TiB message and leaves no file.
    1073741825 records 1073742336.
  * Both divergence tables are empty of VHDX entries.
  * `make test-rust` passes 2757, with 3 ignored, and
    `make check-binary-sizes` passes.
  * The partition check passes. The four new mutation-script cases are
    4 PASS, and all 130 patterns land.
  * The stestr run of the nine modules at 12 workers ran 1599 of the
    1599 listed: 1187 passed, 412 skipped and 0 failed. The skips:
    * 324 are the measure baselines missing at every version (#845).
    * About 20 are measure sources where instar succeeds but the qemu
      baseline recorded a failure. Develop's build gives the same exit
      status on the same fixtures.
    * 7 are `test_differencing` cases that need libvhdi, absent on this
      host. 5d ran that module with libvhdi present.
    * The rest are documented writer, scanner and resize divergences,
      qemu-img being unable to resize a format, raw having no check,
      and missing ZSTD support.

## Risks and mitigations

* **5b changes bytes it should not.** It must be behaviour-neutral
  apart from virtual size 0 in measure and the rounding of odd sizes.
  The management session runs the 5b test list itself and diffs
  `instar create -f vhdx` output at 16M, 1G and 131041M with
  `block_size=1M` against a build of `origin/develop`, byte for byte,
  except the File and Data Write GUIDs, which are constant (#566) and
  so should not differ either.
* **The default changes every VHDX instar writes without
  `block_size`.** That is the point of #645, but it moves outputs no
  test pins. The management session greps `tests/`, `scripts/` and
  `src/` for `33554432`, `32M` and `32 * 1024 * 1024` near `vhdx`
  after 5d, and reads each hit.
* **measure resolves the unit at the wrong time.** That would
  silently miscount allocated blocks (#286) for VHDX targets, and the
  totals would still look plausible. The measure-versus-convert
  round-trip tests are the oracle, and 5d's mutation run checks they
  can fail. The before/after test for the other targets guards the
  arms 5d touches but should not change.
* **A reader gains the ceiling.** It would refuse images qemu opens.
  5c's patched 65 TiB parent is read by `info` and `check`. The
  management session also greps the diff for `layout(` in
  `src/crates/vhdx` readers and the check op.
* **The capacity estimate becomes too small somewhere else.** The
  fallback keeps the old formula for an unchecked size, and the unit
  tests pin both arms.
* **Guest binaries grow.** `make check-binary-sizes` after each step.
  The layout is a few hundred bytes of arithmetic.
* **Host contention produces failures that look like regressions.**
  Replay any failure alone (`--concurrency 1`) before attributing it,
  as phase 4 did.

## Definition of done

Each item is a command, run from the worktree root with the
worktree's build, and its expected result.

* `git grep -n 'DEFAULT_BLOCK_SIZE' -- src/crates/vhdx` prints nothing.
* `git grep -nE '32 \* 1024 \* 1024' -- src/crates/create
  src/crates/measure src/crates/resize src/operations/create
  src/operations/convert src/operations/measure src/shared/src/lib.rs`
  prints no line that is a VHDX block size.
* `git grep -n 'bat_off: u64 = 0x20_0000\|let bat_offset: u64 = 0x20_0000\|fixed_pre_bat' -- src`
  prints nothing.
* For each size in 1073741824 1073742336 107374182400 107374182912
  35184372088832 35184372089344 70368744177664, `instar create -f vhdx
  t.vhdx SIZE` then `instar info --output json t.vhdx` reports the
  `cluster-size` in the survey's table.
* `instar create -f vhdx t.vhdx 70368744177665` exits non-zero, prints
  a message containing `64 TiB`, and `test -e t.vhdx` fails.
* `instar create -f vhdx t.vhdx 1073741825; instar info --output json
  t.vhdx` reports `virtual-size` 1073742336.
* From `tests/`, with the test venv: `python3 -c "import
  test_create as c, test_dd_baselines as d; print([k for k in
  c.KNOWN_WRITER_DIVERGENCES if k[0] == 'vhdx'], [k for k in
  d.KNOWN_DD_DIVERGENCES if k.endswith('-vhdx')])"` prints `[] []`.
* `git grep -nE '8 MiB vs qemu 32|instar uses 32 MiB|emits 32 MiB data blocks|always defaults *to 32 MiB' -- docs tests scripts ':!docs/plans'`
  prints nothing. (`docs/plans` is excluded because this plan quotes
  the phrases; the first version of this item did not exclude it.)
* `make test-rust` and `make check-binary-sizes` pass.
* The stestr run of `test_create`, `test_convert`, `test_measure`,
  `test_dd_baselines`, `test_resize`, `test_vhdx_fixed_fixtures`,
  `test_vhdx_structure`, `test_check_formats` and `test_differencing`
  reports "Ran: N" equal to the tests in those modules, with 0
  failures. Every skip is listed by reason in the execution notes.
* `tools/ci/check-test-partition.sh` exits 0.
* `git diff origin/develop...HEAD -- src tests docs/*.md scripts | grep -nE
  'PLAN-|[Pp]hase [0-9]|step 5[a-d]'` prints nothing.
* `git add -A && pre-commit run --all-files` passes.
* `bash tools/mutate-differencing.sh` (or its per-case selector)
  reports the four read-side mutants 5e adds as killed.
* `instar check` reports 5e's crafted overlapping-region image as
  corrupt.
* The pull request body carries `Fixes #645`, `Fixes #799` and
  `Fixes #843`. A keyword in a commit message does not close an issue
  when the PR lands through a merge commit.

## Back brief

Before executing any step, back brief the operator on your
understanding of this phase and how the intended work aligns with it.
Decision 9's split puts a temporary 32 MiB constant into the tree for
one commit (5b to 5d); if the operator would rather fold 5b and 5d
into one step, agree that before 5b starts, because it changes how 5b
is verified.
