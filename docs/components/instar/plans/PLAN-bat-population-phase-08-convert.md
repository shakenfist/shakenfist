# Phase 8: `convert -O vhdx --subformat fixed`

Phase 8 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation ("What qemu-img actually emits", "What instar does today" and
"Large outputs and the host"), open question 3 (convert writes blocks in
place, no `block_state_zero` on convert), and the "Phases 8 and 9"
paragraph of "Sequencing rationale" first. Also read
[the phase 6 plan](/components/instar/plans/PLAN-bat-population-phase-06-emitter/)'s decisions 3
to 5, which define `vhdx::layout`, `VhdxLayout::fixed_data_start` /
`fixed_file_length` and `vhdx::fill_fixed_bat`, and
[the phase 7 plan](/components/instar/plans/PLAN-bat-population-phase-07-create/)'s decisions 1
and 5, which this phase mirrors for convert. This file assumes them.

Planning effort: high, as the master plan sets for phase 8 (the convert
writer's size and the guest/host boundary it crosses). The output's only
external check is a structural comparison against a live `qemu-img
convert`.

## Decisions already taken by the operator

Every open question in the master plan was resolved with its
recommendation adopted (Michael Still, 2026-10-21). The ones this phase
carries out:

* **Open question 3:** `convert -O vhdx --subformat fixed` writes blocks
  **in place** -- every payload BAT entry `FULLY_PRESENT` at
  `fixed_data_start + block_idx * block_size`, all-zero blocks left as
  holes, file length `fixed_data_start + virtual_size`. This is qemu's
  `-o subformat=fixed,block_state_zero=off` layout, not qemu's default
  convert-to-fixed layout (which appends new data after the preallocated
  region and leaves the rest `ZERO`). Convert exposes no
  `block_state_zero`; the divergence from qemu's default is documented.
* **Open question 4:** never materialise a fixed BAT. The guest streams
  the BAT entries one buffer at a time with `vhdx::fill_fixed_bat`, as
  `create` does, instead of the dynamic path's in-memory `bat_buf`. The
  output device's capacity comes from the layout.

None of these is revisited here.

## Scope

In:

* **The host CLI.** `--subformat fixed` (and `dynamic`) accepted for
  `-O vhdx`. The `-o subformat=fixed` path through `apply_create_overrides`
  is a `create`-only parser and does not apply to convert; convert takes
  only `--subformat` (survey).
* **The config.** A new `ConvertConfig` flag bit carries the fixed
  signal to the guest. Convert has no subformat *field* in its config
  and needs none (decision 1). The struct layout does not change.
* **The host output file.** For a fixed vhdx convert the host sizes and
  opens the output at `vhdx::layout(...).fixed_file_length`, sparse,
  before the guest runs, and maps `EFBIG` to a by-name refusal, exactly
  as phase 7's create does (decision 2).
* **The guest.** `convert_to_vhdx` gains a fixed branch that streams the
  BAT through one buffer with `vhdx::fill_fixed_bat` and writes each
  non-zero payload block at its fixed offset, leaving zero blocks as
  holes (decision 3). The dynamic path is unchanged byte for byte.
* **Tests.** Structural comparison of instar's fixed convert output
  against a live `qemu-img convert -O vhdx -o
  subformat=fixed,block_state_zero=off` of the same input, through
  `tests/helpers/vhdx_structure.py`; `qemu-img compare` identical;
  `instar check` clean; the 2 TiB case; the qemu-unreadable
  partial-last-block-past-first-chunk geometry (structure + `instar
  check` only). Mutation cases.
* **Documentation.** `docs/convert.md`, `docs/quirks.md` (the in-place
  vs qemu-default convert-to-fixed divergence), `convert --help`, and
  `CHANGELOG.md`.

Out:

* **measure.** `measure -O vhdx -o subformat=fixed` is phase 9 and
  keeps its "not yet supported" refusal until then. Convert's output is
  measure's oracle, which is why measure follows convert.
* **Cross-version baselines.** The fixed convert cases across every
  profile are phase 10. This phase proves it against the host `qemu-img`
  (one version); phase 1 found the structure version-independent.
* **Fuzzing.** The differential fuzzer's convert picker learns the fixed
  subformat in phase 11.
* **`docs/format-coverage.md`.** Its convert output-side rows and notes
  are phase 12's.
* **`block_state_zero` on convert.** Future work (open question 3). The
  fixed convert layout is always the in-place one.
* **`convert -O vpc` fixed output.** instar converts to dynamic VHD
  only; Future work.
* **The dynamic convert writer's in-memory-BAT silent failure**
  (`src/operations/convert/src/main.rs:4462`). Fixed convert avoids it
  by streaming, but the dynamic path keeps its in-memory `bat_buf`.
  The survey confirms whether the dynamic silent path is reachable from
  the CLI and records the answer (decision 6); fixing it is Future work.

## What the survey found

All references are to `origin/develop` at `d88c6e37` (phase 7 merged as
`03269d7f`, #858). qemu claims are cited to
`instar-testdata/qemu-sources/7.0.0/block/vhdx.c` or measured with the
host `qemu-img` 10.0.13. No instar binary was built for this survey;
step 8a's brief measures `convert.bin`.

### Phase 6 and 7 left convert almost everything it needs

* `vhdx::layout(virtual_size, block_size, false)` already returns
  `fixed_data_start` and `fixed_file_length`
  (`src/crates/vhdx/src/lib.rs:4134`, `:4141`), and
  `vhdx::fill_fixed_bat(&layout, state, first_entry, buf)`
  (`src/crates/vhdx/src/lib.rs:4374`) fills a buffer of BAT entries from
  an index range. Both are in the **vhdx** crate, which convert already
  links (`convert_to_vhdx` calls `vhdx::layout`,
  `vhdx::build_bat_entry`, `vhdx::PAYLOAD_BLOCK_FULLY_PRESENT`,
  `vhdx::METADATA_ITEMS_MIN_OFFSET`). `VhdxSubformat`, `VhdxPlan` and
  `plan_vhdx` live in the **create** crate, which convert does *not*
  link; convert does not use them and computes its geometry directly
  from `vhdx::layout`, as it does today.
* The host's `compute_output_capacity`
  (`src/vmm/src/main.rs:13957`) already sizes VHDX convert output from
  `vhdx::layout(vsize, block_size, false)` (phase 5). For a **dynamic**
  convert it returns `payload_blocks * block_size + data_start`. For a
  fixed convert the capacity is `fixed_file_length`; this is the only
  new branch the capacity needs (the master plan's "capacity estimate
  replaced by the shared layout" is already done for dynamic).

### `convert_to_vhdx` builds its BAT in memory and packs payload

`convert_to_vhdx` (`src/operations/convert/src/main.rs:4382`):

* allocates `bat_buf` after the staging buffer and zero-fills it to all
  `NOT_PRESENT` (`:4459-4470`), refusing with "convert: VHDX BAT too
  large for scratch" and a **successful** completion if it does not fit
  (`:4462-4466`);
* packs payload blocks sequentially from `payload_start`
  (`next_free_offset`), writing only non-zero blocks, and records each
  as a `FULLY_PRESENT` BAT entry at the packed offset
  (`block_file_offset = align_up(next_free_offset, MB_ALIGN)`,
  `vhdx_payload_bat_index(block_idx, chunk_ratio)`,
  `vhdx::build_bat_entry(PAYLOAD_BLOCK_FULLY_PRESENT, block_file_offset)`,
  `:4756-4762`);
* writes `bat_buf` out to the BAT region last, in `oss`-sized chunks
  ("Rewrite BAT with actual offsets", from `:4900`).

The fixed path replaces all three: the BAT entries are a pure function
of the index (`FULLY_PRESENT` at `fixed_data_start + block_idx *
block_size`), so they stream through one buffer with `fill_fixed_bat`
and need no `bat_buf`; payload blocks are written at their fixed offsets,
not packed; and the file length is `fixed_file_length`, not the end of
the last packed block.

### The host refuses `--subformat` for anything but vmdk

`--subformat` is validated at `src/vmm/src/main.rs:14080-14102`: a
non-empty `--subformat` on non-vmdk output is refused with "--subformat
is only supported with VMDK (-O vmdk) output", and the vmdk arm admits
`monolithicSparse`, `streamOptimized`, `monolithicFlat`.
`ConvertArgs.subformat` (`src/vmm/src/main.rs:7382-7386`, struct at
`:4978`) has a `value_parser` of `["", "monolithicSparse",
"streamOptimized", "monolithicFlat"]` -- it does **not** accept
`dynamic` or `fixed`, so `convert --subformat fixed` is refused by clap
before the host code runs. `CreateArgs.subformat` (`:7478-7485`) already
carries `dynamic` and `fixed` (phase 7). Phase 8 adds them to
`ConvertArgs.subformat`'s parser and its doc comment, and opens the host
gate to vhdx.

### Convert carries no subformat field to the guest

`ConvertConfig` (`src/shared/src/lib.rs:2606`) has no `vhd_subformat` or
`vmdk_subformat` field. The vmdk `monolithicFlat` subformat is handled
entirely on the host (`is_vmdk_flat_output` retargets the guest at raw
and the host writes the descriptor, `:14439`); `monolithicSparse` and
`streamOptimized` are distinguished by other means in the sparse writer.
So the fixed signal must travel as a **flag bit**. `ConvertConfig`'s
flags use bits 0-5 (`FLAG_SKIP_ZEROS` .. `FLAG_DD_WINDOW`) and 31
(`FLAG_VERBOSE`), so bits 6-30 are free (`:2697-2718`). One bit,
`FLAG_VHDX_FIXED = 1 << 6`, is all this phase needs: convert's fixed
layout is always `block_state_zero=off`, so no second bit for the block
state (unlike create, which carries `FLAG_VHDX_FULLY_PRESENT` for its
`block_state_zero=off` variant).

### The error paths keep a zero-byte output

Every `convert_to_vhdx` failure arm calls `send_complete(..., false)`
(`:4463`, `:4475`, and the layout-error arm at `:4428`), whose comment
notes that without a failed completion the host keeps an empty output
file. A failed `instar convert` already leaves a zero-byte file (master
plan Future work); the fixed path inherits that and does not change it.

### The tests and docs that see this

* `TestConvertToVhdx` (`tests/test_convert.py:3426`) holds convert's
  VHDX round-trip tests; `tests/test_vhdx_structure.py` and
  `tests/test_vhdx_fixed_fixtures.py` hold the structural comparator and
  the fixed-fixture builders. The new fixed-convert class goes beside
  `TestConvertToVhdx` and reads through `tests/helpers/vhdx_structure.py`.
* `docs/convert.md:85-103` describes vhdx as dynamic-only output and
  lists `--subformat` as a vmdk option; `:110-133` cover differencing.
  The `--subformat` help on `ConvertArgs` (`:7382-7386`) names vmdk
  subformats only.
* `docs/quirks.md` carries the VHDX partial-last-block entry (phase 7
  added instar's create to it); convert at the same sizes writes the
  same qemu-unreadable image, so the entry gains convert.
* No test or fuzzer expects `convert -O vhdx --subformat fixed` to be
  refused; `scripts/differential-fuzz.py` never asks for it.

### Master plan refinements made in this commit

* The "Phases 8 and 9" paragraph says convert's capacity estimate is
  "replaced by the shared layout". The survey found phase 5 already did
  that for the dynamic case; phase 8 adds only the fixed
  `fixed_file_length` branch. The Execution table and the
  `docs/plans/index.md` row link this file and record phase 7's landing.

## Decisions

1. **The fixed signal is a `ConvertConfig` flag bit, not a subformat
   field.** `ConvertConfig::FLAG_VHDX_FIXED: u32 = 1 << 6` in
   `src/shared/src/lib.rs`, with its host mirror
   `CONVERT_CONFIG_FLAG_VHDX_FIXED` beside the others at
   `src/vmm/src/main.rs:119-127`. The host sets it only for `-O vhdx`
   with `--subformat fixed`. Convert's fixed layout is always
   `block_state_zero=off` (open question 3), so one bit is enough and no
   `block_state_zero` flag is added. `ConvertConfig`'s size and field
   offsets do not change, and the existing layout assertions must still
   pass unchanged.

2. **The host extends the fixed output to `fixed_file_length` at open,
   and maps `EFBIG` by name.** This mirrors
   [phase 7](/components/instar/plans/PLAN-bat-population-phase-07-create/) decision 1, for the
   same three reasons (the filesystem failure is the host's to report;
   the guest cannot make the file the wrong length when the device
   capacity *is* the layout's file length; it is what qemu does). For a
   fixed vhdx convert the host computes `vhdx::layout(out_vsize,
   block_size, false)` and opens the output with capacity
   `layout.fixed_file_length`, sparse (`set_len` before the guest runs).
   An `EFBIG` from that becomes `convert: a fixed VHDX of <virtual size>
   bytes needs a <file length>-byte file, which the filesystem holding
   '<filename>' cannot store`, followed by the OS error, matching phase
   7's create message in shape. `compute_output_capacity` gains a fixed
   argument (or a sibling that returns `fixed_file_length`); for dynamic
   output it is unchanged.

   This is the decision a reviewer is most likely to dispute, for the
   same reason it was in phase 7: the master plan's prose leaves file
   extension unspecified for convert, and a reader might expect the
   guest to extend the file as the dynamic packed writer effectively
   does by writing its last block. Host extension is chosen to keep the
   create and convert fixed paths identical in where the file length is
   decided, and because the guest's capacity check then catches a
   host/guest layout disagreement.

3. **The guest streams the BAT and writes payload in place.** When
   `FLAG_VHDX_FIXED` is set, `convert_to_vhdx`:

   1. computes `layout.fixed_data_start` and `fixed_file_length` from
      the same `vhdx::layout` call it already makes;
   2. checks the device capacity is at least `fixed_file_length` before
      writing anything, failing with a failed completion if not;
   3. streams the BAT with `vhdx::fill_fixed_bat(&layout,
      PAYLOAD_BLOCK_FULLY_PRESENT_state, first_entry, buf)` one buffer at
      a time, writing each slot's sectors into the BAT region, exactly as
      `create`'s `emit_vhdx_fixed_bat` does -- **no `bat_buf`, no
      `ALLOC_HEAP_BASE` ceiling check**, so the fixed path cannot hit the
      dynamic path's "VHDX BAT too large for scratch" failure;
   4. writes each non-zero payload block at `fixed_data_start +
      block_idx * block_size` (not packed), leaving zero blocks as holes
      so the file stays sparse;
   5. reports `fixed_file_length` as the bytes-written / file-size-after
      the host prints.

   The streaming lives in one `#[inline(never)]` function beside the
   dynamic path, for the `x86_64-unknown-none` codegen reason the
   create, commit and bitmap ops document
   (`src/operations/amend/src/main.rs:211`). The dynamic path keeps its
   `bat_buf` and packing, byte for byte unchanged.

4. **Convert exposes no `block_state_zero`, and the divergence is
   documented.** `instar convert -O vhdx --subformat fixed` always
   produces the in-place (`block_state_zero=off`, every entry
   `FULLY_PRESENT`) layout. qemu's **default** `qemu-img convert -O vhdx
   -o subformat=fixed` appends new data after the preallocated region
   and leaves the untouched entries `ZERO` (Situation: a write to a
   `ZERO`-state block allocates at end of file). instar's layout equals
   qemu's `-o subformat=fixed,block_state_zero=off`, which is the oracle
   the tests use. `docs/quirks.md` records that instar's convert matches
   qemu's `block_state_zero=off` convert, not its default.

5. **The oracle is a live `qemu-img convert`, compared structurally.**
   A new class `TestConvertVhdxFixed` in `tests/test_convert.py`, beside
   `TestConvertToVhdx`, for each input (a raw and a qcow2 source with a
   mix of zero and non-zero blocks):

   * runs `instar convert -O vhdx --subformat fixed` and `qemu-img
     convert -O vhdx -o subformat=fixed,block_state_zero=off` of the same
     source to separate outputs;
   * asserts `structural_diff` through `tests/helpers/vhdx_structure.py`
     is empty for every structural field (region table, File Parameters
     `LeaveBlocksAllocated`, block size, every BAT entry, file length),
     with no masking;
   * asserts `qemu-img compare` reports the two identical, and `instar
     check` and `qemu-img check` both exit 0;
   * asserts `--subformat fixed` on a non-vhdx target, and a
     `--subformat` value the parser does not know, are refused.

6. **The dynamic silent-failure reachability is settled, not fixed.**
   Step 8a determines whether the dynamic `bat_buf` ceiling
   (`:4462`) is reachable from the CLI (a large dynamic VHDX at a small
   `--block-size` of a source big enough to need a BAT past the scratch
   budget), by computing the smallest `--block-size` / source size that
   trips it and checking the host does not refuse first. The finding is
   recorded in the master plan's Future work (and an issue filed if the
   operator's permission allows); fixed convert's streaming is not a fix
   for the dynamic path.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | high | opus | none | **Convert to a fixed VHDX.** First read decisions 1 to 4 and 6, and the survey sections "Phase 6 and 7 left convert almost everything", "`convert_to_vhdx` builds its BAT in memory", "The host refuses `--subformat`", "Convert carries no subformat field" in `docs/plans/PLAN-bat-population-phase-08-convert.md`. Also read phase 6 plan decisions 3-5 (`vhdx::layout`, `fixed_data_start`, `fixed_file_length`, `fill_fixed_bat`) and phase 7 plan decisions 1 and 5 (host extension, `emit_vhdx_fixed_bat`), and `src/operations/create/src/main.rs`'s `emit_vhdx_fixed_bat` as the model to mirror. **Shared** (`src/shared/src/lib.rs`): add `ConvertConfig::FLAG_VHDX_FIXED = 1 << 6` (`:2697-2718`) with a doc comment; existing `ConvertConfig` layout assertions must still pass. **Host** (`src/vmm/src/main.rs`): add `CONVERT_CONFIG_FLAG_VHDX_FIXED` at `:119-127`. Add `"dynamic"` and `"fixed"` to `ConvertArgs.subformat`'s `value_parser` and update its doc comment (`:7382-7386`). In the `--subformat` validation (`:14080-14102`), accept `dynamic`/`fixed` for `-O vhdx` (set a local fixed flag), keep the vmdk arm, and keep refusing `--subformat` on other targets and unknown values. Set `FLAG_VHDX_FIXED` when assembling the convert flags for a fixed vhdx. For a fixed vhdx output, size the output device from `vhdx::layout(out_vsize, block_size, false).fixed_file_length` and open it sparse with `set_len` to that length, mapping `EFBIG` to decision 2's message (`raw_os_error() == Some(libc::EFBIG)`); extend `compute_output_capacity` (`:13957`) for the fixed case. **Guest** (`src/operations/convert/src/main.rs`): in `convert_to_vhdx` (`:4382`), add an `#[inline(never)]` fixed branch per decision 3 -- stream the BAT with `vhdx::fill_fixed_bat`, no `bat_buf`, write non-zero blocks at `fixed_data_start + block_idx*block_size`, leave zeros as holes, report `fixed_file_length`. Dynamic path unchanged. **Tests** (`tests/test_convert.py`): `TestConvertVhdxFixed` per decision 5, comparing through `helpers.vhdx_structure.structural_diff`, never `qemu-img info`. Add decision 9-style mutants to `tools/mutate-differencing.sh` (fixed flag ignored so a dynamic image is written; BAT streamed one slot short; payload written packed instead of in place; host opening the fixed output with the dynamic capacity). Settle decision 6 and report. Build only with `make instar`; run `make test-rust`, `make check-binary-sizes` (report `convert.bin` size before and after -- it is the larger binary and gains the filler), `make fuzz-build`; run stestr for `test_convert`, `test_vhdx_structure`, `test_vhdx_fixed_fixtures`, `test_check_formats`, `test_compare`. Prove the dynamic convert output is unchanged with a `cmp` of `instar convert -O vhdx` of a fixed input against a build of `d88c6e37`. Commit subject: `Convert to a fixed VHDX.` |
| 8b | high | opus | none | **Fixed VHDX convert at large and awkward sizes.** First read decisions 2, 4 and 5 and phase 7 plan's `TestCreateVhdxFixedLarge` (decision 7) and `docs/quirks.md`'s partial-last-block entry. Add `TestConvertVhdxFixedLarge` to `tests/test_convert.py`: a 2 TiB fixed convert at 1 MiB blocks (source a sparse qcow2 with a few non-zero blocks), structure equal to the host `qemu-img convert -O vhdx -o subformat=fixed,block_state_zero=off`, `instar check` and `qemu-img check` clean; the partial-last-block-past-first-chunk geometry (reuse `tests/test_vhdx_fixed_fixtures.py`'s `PARTIAL_PAST_FIRST_CHUNK` build args), structure vs host qemu + `instar check` only (`qemu-img check`/`info` not used, for the reason phase 7's plan gives); and the 16 TiB filesystem-ceiling refusal behind a probe-based skip, plus an `RLIMIT_FSIZE` variant that exercises the `EFBIG` path on any filesystem (as phase 7's `test_file_size_limit_is_refused_like_a_filesystem_ceiling` does). Every image sparse; none allocates a data region. Check `tools/ci/check-test-partition.sh` placement. Add the 8b mutant (`EFBIG` mapping removed). Commit subject: `Test fixed VHDX convert at large and awkward sizes.` |
| 8c | medium | sonnet | none | **Document fixed VHDX convert.** First read decision 4 and the survey section "The tests and docs that see this". In `docs/convert.md`: update the vhdx target description to dynamic + fixed, add `--subformat fixed` to the vhdx examples and the per-target option list, and state that fixed convert writes blocks in place. In `docs/quirks.md`: extend the VHDX partial-last-block entry to say `instar convert --subformat fixed` writes qemu's `block_state_zero=off` layout at those sizes too (one qemu cannot reopen; `instar check` reads it), and add that instar's fixed convert matches qemu's `block_state_zero=off` convert, not qemu's default convert-to-fixed (which appends). Update the `ConvertArgs.subformat` help text (`:7382-7386`) to name vhdx. Add a `CHANGELOG.md` entry under `[Unreleased]` / `### Added`. Do not touch `docs/format-coverage.md` (phase 12). Every qemu claim comes from the plan's survey or measurement; add no new ones. Run `pre-commit run --all-files`. Commit subject: `Document fixed VHDX convert.` |

The management session runs the steps in order. 8b's tests need 8a's
binary, and 8c documents the messages 8a wrote. 8a is one step rather
than split host/guest, because neither half is testable alone (the guest
is unreachable without the host gate, and the host alone produces a
dynamic image). It runs on opus at high effort: the arithmetic is phase
6's, already pinned and mutated, so what is new is the guest branch and
the call-table wiring, which is opus's ground.

Every brief also carries the session's standing rules:

* Build only with `make instar` (Docker); never install a Rust
  toolchain on the host.
* Run `make test-rust`, redirecting cargo's target dir to an owned
  directory if pre-commit has left `src/target` root-owned.
* Do not run `make test-rust` at the same time as a mutation run.
* Run the touched test modules with stestr from `tests/`, with
  `INSTAR_BINARY_PATH` pointed at the worktree's build, and report
  "Ran: N". Replay any failure alone (`--concurrency 1`) before
  attributing it.
* Run `pre-commit run --all-files` after `git add -N` on new files, and
  `tools/ci/check-test-partition.sh`.
* Keep plan, phase, step and decision references out of code and docs.
* Keep a mutation script under the step's scratch directory, and run
  each mutant under `timeout`.
* Every qemu behaviour a comment or doc states is cited to
  `instar-testdata/qemu-sources/7.0.0/` by function name, or was
  measured. Say which.
* Tests stay under ext4's 16 TiB file-size ceiling by staying sparse:
  no test allocates a data region.
* Leave the work uncommitted.

## Execution notes

* **Back brief, 2026-11-04 (AEDT).** The operator kept decision 2 as
  recommended: the host extends the fixed convert output to its
  `fixed_file_length` at open, mirroring phase 7's create. They kept the
  planned models (opus/high for 8a and 8b, sonnet/medium for 8c) and
  allowed this session to push the branch and open the pull request once
  the definition of done passes. The steps run in order.
* **8a.** Implemented as planned. Two management-session corrections,
  both in the step commit (`86ffcebd`):
  * the brief carried a wrong line reference for `ConvertArgs.subformat`
    (`:7382-7386` is `MeasureArgs`, not `ConvertArgs`), so the agent
    added `dynamic`/`fixed` to **measure**'s `--subformat` value_parser,
    which is out of scope (measure is phase 9). Reverted. `ConvertArgs`
    has no `value_parser`, so convert already accepts any `--subformat`
    string and the host `match` validates it; the convert path needed no
    parser change. Its stale "Only valid with -O vmdk" doc comment was
    corrected to name vhdx instead.
  * one over-long line in `docs/testing.md` was rewrapped.
* **8a verification (management session, built at `86ffcebd`).**
  `make instar` clean; `make check-binary-sizes` OK (`convert.bin`
  375,800 B, 47% of the 768 KiB cap, up from 370,960 B at `d88c6e37`);
  `pre-commit run --all-files` passes; `tools/mutate-differencing.sh
  --check-patterns` reports 149 land, 0 drifted. `TestConvertVhdxFixed`
  (4 tests) passes, including the structural comparison against live
  `qemu-img convert -o subformat=fixed,block_state_zero=off`,
  `qemu-img compare` identical and both `check`s clean. The dynamic
  convert output is unchanged: the agent's `cmp` against a `d88c6e37`
  build was byte-identical, and the management corrections touch neither
  `convert.bin` nor the dynamic convert host path, so it carries over.
  The agent's full-suite stestr run showed 5 failures that all passed on
  `--concurrency 1` replay (host-contention flakes in untouched
  vhd/vmdk/qcow2 paths), as earlier phases saw.
* **Decision 6 settled.** The dynamic path's silent in-memory-BAT
  ceiling is reachable from the CLI; recorded in the master plan's
  Future work, not fixed.
* **8b (`69de3893`).** `TestConvertVhdxFixedLarge` and the convert-host
  EFBIG mutation case. One departure, the operator's call: the plan's
  2 TiB case mirrored phase 7's create timing (0.74 s), but convert
  reads every source block, so a 2 TiB fixed convert is O(source size)
  and takes about fifteen minutes. The 2 TiB convert was verified once
  (its structure equalled qemu-img's and both tools checked it clean);
  the permanent gate test uses 256 GiB instead, which still streams a
  many-slot BAT and places a data block past 128 GiB and runs in 113 s
  (measured), keeping the heavy case out of the merge-queue gate as the
  merge-queue critical-path work did for VHD conversion tests.
* **8b verification (management session, built at `86ffcebd`).** The
  256 GiB case passes in 113 s; the partial-last-block, 16 TiB-ceiling
  (ran on ext4) and RLIMIT_FSIZE cases pass. `pre-commit run
  --all-files` passes, `tools/mutate-differencing.sh --check-patterns`
  reports 150 land, 0 drifted, and `tools/ci/check-test-partition.sh`
  is clean. The 2 TiB convert measured about fifteen minutes standalone.

## Risks and mitigations

* **A fixed convert image that is structurally right and still has the
  wrong data.** The structural comparator sees the BAT and regions, not
  payload. `qemu-img compare` against qemu's own fixed convert (decision
  5) covers the data for the small cases; `instar check` and a
  `convert -O raw` round-trip cover the large ones. The management
  session spot-checks one round-trip to raw equals the source.
* **The dynamic convert path changes by accident.** The `cmp` against a
  `d88c6e37` build in 8a's brief, repeated by the management session on
  one size.
* **The host and guest disagree about the fixed layout.** Both call
  `vhdx::layout` with the same `out_vsize` and `block_size`. The guest's
  capacity check (decision 3) catches a host that sized the device too
  small; the structural comparison catches a file of the wrong length.
  The management session confirms the host passes the guest the same
  `block_size` it sized the device with.
* **`convert.bin` grows past the 768 KiB cap.** `make
  check-binary-sizes` after 8a; 8a reports the size before and after.
  The filler is already compiled in the vhdx crate convert links, so the
  addition is the one new guest function plus the branch.
* **The 16 TiB test skips everywhere CI runs.** Acceptable (a
  host-filesystem fact), but the `RLIMIT_FSIZE` variant exercises the
  `EFBIG` path on any filesystem, and the skip reason names the probe.
* **Host contention looks like a regression.** Replay any failure alone
  (`--concurrency 1`) before attributing it.

## Definition of done

Each item is a command, run from the worktree root with the worktree's
build, and its expected result.

* `"$INSTAR_BINARY_PATH" convert -O vhdx --subformat fixed src.qcow2
  out.vhdx` on a qcow2 with a mix of zero and non-zero blocks produces
  an image whose `helpers.vhdx_structure` summary has an empty
  `structural_diff` against `qemu-img convert -O vhdx -o
  subformat=fixed,block_state_zero=off src.qcow2 ref.vhdx`, and
  `qemu-img compare out.vhdx ref.vhdx` reports them identical.
* `"$INSTAR_BINARY_PATH" check out.vhdx` and `qemu-img check out.vhdx`
  both exit 0.
* `"$INSTAR_BINARY_PATH" convert -O qcow2 --subformat fixed a b` is
  refused (fixed is vhdx-only), and `--subformat bogus` is refused.
* `git grep -n 'FLAG_VHDX_FIXED' -- src` prints at least three lines
  (shared constant, host mirror, guest read).
* `make test-rust`, `make check-binary-sizes` and `make fuzz-build`
  pass; the execution notes record `convert.bin`'s size before and
  after.
* The stestr run of `test_convert`, `test_vhdx_structure`,
  `test_vhdx_fixed_fixtures`, `test_check_formats` and `test_compare`
  reports "Ran: N" with 0 failures; every skip is listed by reason,
  including whether the 16 TiB test ran.
* `cmp` of `instar convert -O vhdx out.vhdx` (dynamic, no `--subformat`)
  of a fixed input from this build and from a build of `d88c6e37`
  reports no difference.
* `bash tools/mutate-differencing.sh` with each new case name reports
  PASS (or the documented survivor outcome), and `--check-patterns`
  passes.
* `tools/ci/check-test-partition.sh` exits 0.
* `git diff origin/develop...HEAD -- src tests docs/*.md scripts tools
  CHANGELOG.md | grep -nE '^\+.*(PLAN-|[Pp]hase [0-9]|step 8[a-c])'`
  prints nothing.
* `git add -A && pre-commit run --all-files` passes.
* The dynamic silent-BAT reachability finding (decision 6) is recorded
  in the master plan's Future work.

## Back brief

Before executing any step, back brief the operator on your understanding
of this phase and how the intended work aligns with it. Put decision 2
(the host extends the file at open, mirroring phase 7) to the operator
before 8a starts, since it resolves a point the master plan leaves
unspecified for convert. Confirm opus for 8a and 8b, and whether this
session may push and open the pull request once the definition of done
passes.
