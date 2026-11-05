# Phase 9: `measure -O vhdx -o subformat=fixed`

Phase 9 of [PLAN-bat-population.md](/components/instar/plans/PLAN-bat-population/). Read its
Situation ("What qemu-img actually emits" and "What instar does today",
in particular the paragraph that records qemu-img cannot measure VHDX at
all), the "Phases 8 and 9" paragraph of "Sequencing rationale", and open
question 4. Also read
[the phase 6 plan](/components/instar/plans/PLAN-bat-population-phase-06-emitter/)'s decisions 3
to 5, which define `vhdx::layout`, `VhdxLayout::fixed_data_start` /
`fixed_file_length`, and
[the phase 8 plan](/components/instar/plans/PLAN-bat-population-phase-08-convert/), whose
`ConvertConfig::FLAG_VHDX_FIXED` spare-flag-bit pattern this phase
mirrors for `MeasureConfig`. This file assumes them.

Planning effort: medium, as the master plan sets for phase 9 ("measure
is small"). The feature follows a pattern already established twice (the
convert flag bit, and `measure`'s own fixed-VHD dispatch). The one
part that needs judgement -- the two backwards source-scanner claims --
is a measure-then-correct task, not a design question.

## What the survey found

Surveyed on 2026-11-04 against `develop` at `e49b6275` (phase 8's merge).
The master plan's phase 9 premises hold; its line references are stale
(code moved as phases 4-8 landed). Corrected here so a later step does
not chase them:

* **`measure` already parses `-o subformat=fixed` and refuses it.** The
  refusal is `src/vmm/src/main.rs:15415-15418`, `"measure: -O vhdx -o
  subformat=fixed is not yet supported"`, inside `parse_o_options`'s
  `("vhdx", "subformat")` arm (the master plan's `main.rs:14867` is
  stale). The `("vpc", "subformat")` arm just above it (`:15396-15407`)
  already accepts `fixed` and sets `out.vhd_subformat = Some(1)`, which
  is the exact pattern this phase copies. `block_state_zero` is
  accept-ignored at `:15432`.
* **`measure_vhdx` is `src/crates/measure/src/lib.rs:1011`** (the master
  plan's `:1009` is one annotation off). It takes `&VhdxOpts`, which
  today carries only `block_size` (`:946`), and always runs the dynamic
  prediction: `required = data_start + ceil_div(allocated, bs) *
  block_alloc`, `fully_allocated = data_start + payload_blocks *
  block_alloc`. `measure_vhd` (`:829`) is the shape to mirror: it
  matches on `opts.subformat` and dispatches fixed to `measure_vhd_fixed`
  (`:857`), which returns `(total, total)` -- a dense image where
  `required == fully_allocated`.
* **The layout already exposes the fixed answer.** `vhdx::layout(...)`
  returns `VhdxLayout` with `fixed_data_start`
  (`src/crates/vhdx/src/lib.rs:4134`) and `fixed_file_length` (`:4141`,
  `= fixed_data_start + round_up(virtual_size, 512)`). `measure_vhdx`
  already calls `vhdx::layout(s.virtual_size, opts.block_size, false)`
  at `:1023`, so the fixed file length is one field away.
* **`MeasureConfig` has a free flag bit.** `src/shared/src/lib.rs:2887`:
  `flags` is a `u32` using bits 0-3 (`FLAG_EXTENDED_L2 .. FLAG_COMPRESS`)
  and bits 4-5 (`PREALLOC_MASK`). Bit 6 is free, so `FLAG_VHDX_FIXED =
  1 << 6` is available -- the same bit, by the same name, that
  `ConvertConfig` uses (`:2727`). The struct layout does not change: no
  new field. There is no `vhdx_subformat` byte field and none is added.
* **The guest builds `VhdxOpts` at `src/operations/measure/src/main.rs:161`**
  as `VhdxOpts { block_size: unit }` and already reads its config via
  `MeasureConfig`. `vhdx_block_size_for` (`:305`) and `unit_size_for`
  (`:313`) resolve the block size; the fixed flag joins them.
* **Both backwards source-scanner claims the master plan names still
  exist**, unchanged since phase 4 reported them:
  * `docs/quirks.md:989-990`: "VHDX sources: instar treats every BAT
    block as fully allocated."
  * `tests/test_measure.py:683-685`: the comment "VHDX scanner treats
    every block as fully allocated; qemu-img returns ..." on
    `KNOWN_SOURCE_SCANNER_DIVERGENCES['qemu-vhdx']` (value `('qcow2',
    'instar vhdx scanner reports full allocation for all blocks')`).
    These are about VHDX as a measured **source**, not the fixed VHDX
    **target** this phase adds -- see decision 6.
* **No part of phase 9 was already shipped.** `measure` has no fixed
  VHDX path today; the refusal is the only fixed-VHDX code in the
  measure surface.

The stale line references above are corrected at their source in the
master plan's phase 9 paragraphs and the `index.md` row as part of this
phase's planning commit, per the survey step.

## Decisions

1. **Interface: `-o subformat=fixed` only.** `measure` grows no
   `--subformat fixed`. qemu-img `measure` has no `--subformat` (it takes
   target options through `-o` alone), and instar's existing fixed-VHD
   measure is `-o subformat=fixed` through `parse_o_options` -- the
   `("vpc", "subformat")` arm, with no `MeasureArgs` flag. `MeasureArgs.subformat`
   stays vmdk-scoped (`["", "monolithicSparse", "streamOptimized",
   "monolithicFlat"]`, `main.rs:7385-7388`). This is deliberately
   narrower than create (phase 7) and convert (phase 8), which both take
   `--subformat fixed`: those are writers with a subformat as a
   first-class CLI concept; measure is a predictor whose target options
   are `-o`, matching qemu-img.

2. **Carry fixed on the wire as a flag bit.** Add
   `MeasureConfig::FLAG_VHDX_FIXED = 1 << 6` (bit 6, free), mirroring
   `ConvertConfig::FLAG_VHDX_FIXED` by name and position. No struct field
   is added and no struct layout changes, as the master plan's
   call-table constraint requires. `block_state_zero` does not travel:
   it cannot change a fixed image's file length (see decision 3), so it
   stays accept-ignored.

3. **A fixed target's `required` and `fully_allocated` are both
   `fixed_file_length`.** A fixed VHDX is dense: every payload byte is in
   the file whether or not the source block held data, exactly as a fixed
   VHD is (`measure_vhd_fixed`). So `measure_vhdx` dispatches a fixed
   target to a new `measure_vhdx_fixed` that returns
   `(layout.fixed_file_length, layout.fixed_file_length)` and reads
   neither `allocated_bytes` nor `target_units_with_data`. `block_size`
   still matters -- it sizes the BAT region and therefore
   `fixed_data_start` -- so the layout call stays. `block_state_zero`
   does not: `off` and `on` produce the same file length (the difference
   is BAT entry state, `FULLY_PRESENT` versus `ZERO`, not size), so one
   answer serves both and measure need not know which the eventual
   writer would pick.

4. **`VhdxOpts` gains `subformat: VhdxSubformat`**, a new two-variant enum
   (`Dynamic` default, `Fixed`) in the measure crate, mirroring
   `VhdOpts { subformat: VhdSubformat }` and `VhdSubformat`. This is the
   decision a reviewer is most likely to argue, because a bare `fixed:
   bool` is less code and maps one-to-one onto the single wire bit. The
   enum wins on local consistency: `measure_vhd` already dispatches on a
   `VhdSubformat`, so a VHDX dispatch on a `VhdxSubformat` reads the same
   way in the same file, and it names the closed set of VHDX subformats
   rather than leaving "not fixed" implicit. `VhdxOpts` keeps `#[derive(Default)]`
   with `subformat: VhdxSubformat::Dynamic`, so the field default is
   dynamic and every construction site that does not set it is unchanged
   in behaviour (but must still name the field -- see the 9a brief).

5. **Oracle: instar's own convert writer, because qemu-img cannot measure
   VHDX.** qemu-img rejects `measure -O vhdx` outright ("Block driver
   'vhdx' does not support size measurement"), so there is no external
   number to compare against -- for dynamic or fixed. The existing VHDX
   measure tests already use instar's writer as the oracle (predict with
   `measure`, create/convert, compare the real file size to the predicted
   range). Fixed is stronger: `required == fully_allocated ==
   fixed_file_length` is an exact equality, not a range, so the fixed
   integration test asserts that `measure`'s two numbers are equal to
   each other and to the byte length of the file `convert -O vhdx
   --subformat fixed` (phase 8) actually writes for the same source and
   block size.

6. **Correct the two backwards source-scanner claims by measurement, not
   by flipping the words.** The master plan says the quirks/`test_measure`
   claim ("instar treats every BAT block as fully allocated") is
   backwards -- that qemu counts every block and instar counts no `ZERO`
   block. That is a load-bearing claim about a *different* code path
   (reading a VHDX **source**, `vhdx::scan_allocation`), not the fixed
   **target** this phase adds. The 9c sub-agent measures instar's and
   qemu-img's `measure -O qcow2` of the phase 2 dynamic VHDX fixture,
   pastes both outputs into its report, and rewrites the two descriptions
   to state what the numbers actually show. Management re-measures the
   sample before the commit, per the master plan's claim-sourcing
   constraint. If the measurement does not match the master plan's
   predicted direction, the plan is wrong and gets corrected at its
   source, not worked around.

## Step plan

Steps run in sequence: 9b and 9c both depend on 9a's code, and 9b and 9c
both edit `tests/test_measure.py` (9b adds a class, 9c corrects a comment),
so they cannot share the worktree concurrently. Each step is its own
commit.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 9a | high | opus | none | Wire the fixed-VHDX measure path end to end. **(1) `src/shared/src/lib.rs`:** add `pub const FLAG_VHDX_FIXED: u32 = 1 << 6;` to the `impl MeasureConfig` block near `:2924` (bit 6 is free; bits 0-5 are taken by `FLAG_EXTENDED_L2..FLAG_COMPRESS` and `PREALLOC_MASK`), with a doc comment matching `ConvertConfig::FLAG_VHDX_FIXED` at `:2727`, and a `pub fn vhdx_fixed(&self) -> bool { (self.flags & Self::FLAG_VHDX_FIXED) != 0 }` accessor. Do **not** add a struct field or change the struct layout. **(2) `src/crates/measure/src/lib.rs`:** add `pub enum VhdxSubformat { Dynamic, Fixed }` deriving `Clone, Copy, Debug, PartialEq, Eq, Default` with `#[default] Dynamic`, mirroring `VhdSubformat` at `:114`; add `pub subformat: VhdxSubformat` to `VhdxOpts` (`:946`) and set it in the `Default` impl / derive; in `measure_vhdx` (`:1011`), after the existing `vhdx::layout(...)` call and the `allocated_bytes > virtual_size` check, `match opts.subformat { VhdxSubformat::Fixed => measure_vhdx_fixed(&geometry), VhdxSubformat::Dynamic => <existing body> }`; add `fn measure_vhdx_fixed(geometry: &vhdx::VhdxLayout) -> MeasureResult { MeasureOutput::try_new(geometry.fixed_file_length, geometry.fixed_file_length) }` with a doc comment mirroring `measure_vhd_fixed` (`:857`). **(3) `src/operations/measure/src/main.rs:161`:** change `VhdxOpts { block_size: unit }` to also pass `subformat: if config.vhdx_fixed() { VhdxSubformat::Fixed } else { VhdxSubformat::Dynamic }`, importing `VhdxSubformat` alongside the existing `measure_vhdx` import at `:35`. **(4) `src/vmm/src/main.rs`:** add a `MEASURE_CONFIG_FLAG_VHDX_FIXED` mirror constant next to the other `MEASURE_CONFIG_*` mirrors; add `vhdx_fixed: Option<bool>` to `MeasureOptionOverrides` (`:15204`); in `parse_o_options`'s `("vhdx", "subformat")` arm (`:15413-15428`), replace the `"fixed"` refusal with `out.vhdx_fixed = Some(true)` and set `"dynamic"` to `out.vhdx_fixed = Some(false)` (keep the catch-all error); in `run_measure`, after the other `-o` overrides are applied and while building `measure_flags`, set `measure_flags |= MEASURE_CONFIG_FLAG_VHDX_FIXED` when `args.target_format == "vhdx"` and `overrides.vhdx_fixed == Some(true)`. Add crate unit tests in `measure/src/lib.rs` for `measure_vhdx` with `VhdxSubformat::Fixed` at several sizes/block sizes (mirror the existing `vhdx_*` tests near `:2835`), asserting `required == fully_allocated == fixed_file_length` from a direct `vhdx::layout` call, including a partial-last-block size and a zero virtual size. Constraints: the vhdx and measure crates are `no_std` and panic-free (checked arithmetic only -- `fixed_file_length` is already checked in `layout`); the call-table struct layouts must not change; measure.bin stays under the 768 KiB cap. Run `make instar`, `make check-binary-sizes`, `cargo test -p measure`, `pre-commit run --all-files`. Leave everything uncommitted; report which files changed and the measure.bin size before/after. ≤400 words. |
| 9b | medium | sonnet | none | Add integration tests and mutation cases for fixed-VHDX measure, on top of 9a's code. **(1) `tests/test_measure.py`:** add a `TestMeasureVhdxFixed(InstarTestBase)` class. For a small qcow2 source with a mix of zero and non-zero data, run `"$INSTAR_BINARY_PATH" measure -O vhdx -o subformat=fixed <src>` and assert (a) the output parses to `required size: N` and `fully allocated size: N` with the two `N` **equal**, and (b) `N` equals the byte length of the file produced by `"$INSTAR_BINARY_PATH" convert -O vhdx --subformat fixed <src> out.vhdx` (phase 8) for the same source -- the writer is the oracle because qemu-img cannot measure VHDX (decision 5; confirm by asserting `qemu-img measure -O vhdx` exits non-zero with "does not support size measurement"). Add a case passing `-o subformat=fixed,block_state_zero=off` and `...=on` and assert both give the same `N` (decision 3). Add a case at a non-default `-o block_size=` and one at a partial-last-block virtual size. Mirror the construction and helpers of the existing VHDX measure tests (`test_measure_small_vhdx_with_allocated_block` at `:241`, and the round-trip class near `:910`). **(2) `tools/mutate-differencing.sh`:** add measure mutation cases following the five convert cases phase 8 added -- at minimum: the `FLAG_VHDX_FIXED` bit ignored in `run_measure` (fixed measured as dynamic), `measure_vhdx_fixed` returning `data_start` instead of `fixed_file_length`, and the `("vhdx","subformat")` `"fixed"` arm left as the old refusal -- each with a `--check-patterns` entry, and bump `EXPECTED_CASES` (currently 150, `:685`) by the number added. Run the new tests via `tests/.venv/bin/stestr` with `INSTAR_BINARY_PATH` set (replay any VHDX/convert contention flakes at `--concurrency 1` before reporting them -- see the testdata notes), `tools/mutate-differencing.sh --check-patterns`, and `pre-commit run --all-files` (after `git add -N` on nothing new -- the files already exist). Leave uncommitted; report test names, pass counts, the new `EXPECTED_CASES`, and any flakes you replayed. ≤350 words. |
| 9c | medium | sonnet | none | Documentation and the two backwards-claim corrections, on top of 9a. **(1) Measure the real divergence first.** Create a qcow2 and a dynamic VHDX of the same content (or use the phase 2 dynamic VHDX fixture under `instar-testdata`), run both `"$INSTAR_BINARY_PATH" measure -O qcow2 <the.vhdx>` and `qemu-img measure -O qcow2 <the.vhdx>`, and paste both full outputs into your report. State plainly which tool reports the larger `required`/`fully allocated` and by how much. **(2) Correct `docs/quirks.md:989-990`** ("VHDX sources: instar treats every BAT block as fully allocated") to state what the measurement actually shows about how instar's VHDX source scanner (`vhdx::scan_allocation`) counts allocated bytes versus qemu-img -- the master plan says the current wording is backwards (qemu counts every block; instar counts no `ZERO` block), but write what you measured, not what the plan predicts. **(3) Correct the matching comment at `tests/test_measure.py:683-685`** and, if the measurement shows it is wrong, the divergence string in `KNOWN_SOURCE_SCANNER_DIVERGENCES['qemu-vhdx']` (`:685`) -- keep the registration (the divergence is real), fix only the description. **(4) `docs/measure.md`:** document `measure -O vhdx -o subformat=fixed` -- that `required` and `fully allocated` are equal and are the fixed file length, that `block_state_zero` does not change the answer, and that qemu-img cannot measure VHDX so instar's number has no external oracle. Do not touch `docs/format-coverage.md` or `docs/format-internals.md` (phase 12 owns the cross-cutting pages). **(5) `CHANGELOG.md`:** one entry for fixed-VHDX measure. No plan/phase/decision numbers or `PLAN-*.md` references in any code, comment, or user doc. Run `pre-commit run --all-files`. Leave uncommitted; report the two pasted measurement outputs and every file changed. ≤350 words. |

## Risks and mitigations

* **The backwards-claim correction is re-flipped the wrong way.** A
  sub-agent told "the plan says this is backwards" may invert the words
  without measuring, and land a claim that is now wrong in the other
  direction. Mitigation: decision 6 and the 9c brief require the agent to
  paste both tools' measured output, and the management session
  re-measures the sample before committing (master plan constraint). The
  commit does not land on the plan's say-so.
* **`VhdxOpts`'s new field breaks construction sites.** Adding
  `subformat` to `VhdxOpts` touches the guest op and the crate's own
  tests. Mitigation: the field has a `Dynamic` default, so sites using
  `..Default::default()` are unaffected; the 9a brief names the one
  explicit site (`measure/src/main.rs:161`) and tells the agent to build
  `cargo test -p measure` to surface any it missed.
* **A fixed measure that silently reads the source allocation.** If
  `measure_vhdx_fixed` accidentally used `allocated_bytes`, a fixed
  prediction would vary with source content and stop equalling
  `fixed_file_length`. Mitigation: the 9a crate tests assert exact
  equality to `fixed_file_length` at a size with a partial last block and
  at zero virtual size; 9b's integration test asserts `required ==
  fully_allocated == actual convert output length`; and a 9b mutation
  case breaks exactly this.
* **measure.bin over the 768 KiB cap.** A fixed branch is a few hundred
  bytes, but the cap is checked regardless. Mitigation: 9a runs `make
  check-binary-sizes` and reports the before/after size.

## Definition of done

Each item is a command run from the worktree root with the worktree's
build, and its expected result.

* `"$INSTAR_BINARY_PATH" measure -O vhdx -o subformat=fixed <src.qcow2>`
  prints `required size: N` and `fully allocated size: N` with the two
  `N` equal, and that `N` equals the byte length of the file
  `"$INSTAR_BINARY_PATH" convert -O vhdx --subformat fixed <src.qcow2>
  out.vhdx` writes for the same source and block size.
* `"$INSTAR_BINARY_PATH" measure -O vhdx -o
  subformat=fixed,block_state_zero=off <src>` and `...block_state_zero=on
  <src>` print the same `N`.
* `"$INSTAR_BINARY_PATH" measure -O vhdx -o subformat=dynamic <src>` is
  byte-for-byte what `measure -O vhdx` printed before this phase (the
  dynamic path is unchanged), checked against a `develop` build.
* `cargo test -p measure` passes, including the new fixed cases asserting
  `required == fully_allocated == vhdx::layout(...).fixed_file_length`.
* `grep -n 'not yet supported' src/vmm/src/main.rs` no longer matches the
  `-O vhdx -o subformat=fixed` line.
* `make instar` builds, `make lint` clean, `make check-binary-sizes` OK
  (measure.bin under 768 KiB), `pre-commit run --all-files` passes.
* `tools/mutate-differencing.sh --check-patterns` reports all cases land,
  0 drifted, with `EXPECTED_CASES` raised to the new total.
* `docs/quirks.md` and `tests/test_measure.py`'s `qemu-vhdx` comment
  describe the VHDX source-scanner divergence in the direction measured
  in 9c (both tools' outputs recorded in the commit body), and
  `docs/measure.md` documents fixed-VHDX measure.

## Execution notes

* **Back brief (Michael Still, 2026-11-05, Australia/Brisbane).** All
  three gates answered with the recommendation: D4 the `VhdxSubformat`
  enum (not a bool); D1 `-o subformat=fixed` only (no `--subformat`);
  push and open the pull request once the Definition of done passes.
* **9a.** Implemented as planned, no deviations in the four briefed
  files. One extra kept deliberately: the agent also extended
  `src/fuzz/fuzz_targets/fuzz_measure_calc.rs` to drive the fixed path
  (a free `flag_bits` bit selects the subformat), so the no_std
  panic-free guarantee covers the new calculator. Kept because the
  fixed path is a new measure calculator and this is its natural fuzz
  home; phase 11's fuzz scope is create/convert and the differential
  fuzzer, not this target. The management session verified it with
  `make fuzz-build` (the agent had not run it): clean.
* **9a verification (management session).** `make instar`, `make lint`,
  `make check-binary-sizes` (measure.bin 39,496 B after, up from
  39,448 B, 5% of the 768 KiB cap), `make test-rust` and `pre-commit
  run --all-files` all pass; the four new crate tests
  (`vhdx_fixed_*`) pass; `make fuzz-build` clean; the `-O vhdx -o
  subformat=fixed` "not yet supported" refusal is gone from the VMM.
* **9b.** `TestMeasureVhdxFixed` (6 tests) and three measure mutation
  cases added (`EXPECTED_CASES` 150 to 153). The management session
  corrected the mutation-doc group count (`docs/testing.md`: the three
  measure cases are a new twelfth group, not the eleven the agent left)
  and reproduced the headline equality independently: a 64 MiB source
  measures `required == fully allocated == 75497472`, equal to the
  fixed convert output length; `--check-patterns` reports 153 land, 0
  drifted. The agent kept the partial-last-block test's source data in
  the last full 64 KiB rather than the partial block, to sidestep the
  convert bug below.
* **9b found a convert bug (out of scope; recorded, not fixed).**
  `convert -O vhdx --subformat fixed` fails (`convert operation
  failed`) when the source virtual size is not a block multiple *and*
  has data in the partial last block -- the phase 8 writer does not
  clamp the final block's write to `fixed_file_length`. qemu-img
  converts the same source fine, and raw/dynamic/qcow2 targets do too,
  so it is an instar-specific defect in the merged phase 8 `convert`
  writer, not in phase 9's `measure` (whose answer is correct
  regardless). Recorded in the master plan's Future work; the GitHub
  issue is pending (the session's sandbox denied `gh issue create`,
  so it is filed with the operator's go-ahead). Measure is unaffected:
  `measure_vhdx_fixed` returns the fixed file length without touching
  the source, so it gives the right number even for the sizes convert
  cannot currently write.

## Back brief

Before executing any step, back brief the operator on this understanding
of the plan. The gates that need an answer:

1. **The `VhdxSubformat` enum versus a `fixed: bool`** on `VhdxOpts`
   (decision 4) -- the one most likely to be argued. Confirm the enum, or
   switch to the bool.
2. **The interface is `-o subformat=fixed` only**, no `--subformat`
   (decision 1). Confirm, or add `--subformat fixed` to `measure` for
   symmetry with create/convert.
3. **Whether to push and open the pull request** once the Definition of
   done passes, or hold for review.
