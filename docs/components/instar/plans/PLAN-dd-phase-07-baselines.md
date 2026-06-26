# PLAN-dd phase 07: Cross-version baselines

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-06-integration.md](/components/instar/plans/PLAN-dd-phase-06-integration/)

## Status: Complete (test 6ebe645; testdata baselines pending operator push)

> **Outcome.** 7a: added `dd` to the testdata `generate-baselines.py`
> (`DD_CASES`, `generate_dd_baseline` mirroring resize) + a
> `baselines-dd` Makefile target, and registered `dd-info-json` in
> `detect-profiles.py` (the assumption it handled arbitrary types was
> wrong — `MULTI_BUCKET_TYPES` needed the entry). 7b: generated 1440
> baselines across 80 qemu versions → 80 profiles (one per version,
> like `create` — qemu's info-JSON format drifts per version) +
> `version-map.json`. 7c (`6ebe645`): `tests/test_dd_baselines.py`
> compares instar dd's result info to the qemu baseline for the
> host's profile — 16 cases compared, 3 skipped (count=0 vmdk: qemu
> exits 1; two vhdx: pre-existing 32-vs-8 MiB block-size writer
> divergence, not a dd bug). 7d docs folded into phase 10 (holistic
> dd docs).
>
> **Operator step:** the testdata repo changes (generator +
> `detect-profiles.py` + the generated `expected-outputs/dd-info-json/`
> tree) are committed on a branch there and need review + push to the
> protected `main` ([[testdata-push-token]]).

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

## Mission

Add `dd` cross-version baselines using the **established** testdata
baseline mechanism — the same `generate-baselines.py` +
`detect-profiles.py` + `Makefile` flow that produces the
`create-info-json` / `resize-info-json` / `amend-info-json`
baselines. No new mechanism: dd is a producing command exactly like
`resize`/`amend` (create a fixture → run the operation → capture
`qemu-img info --output=json` of the result), so it slots into the
generator as one more command with a curated case list.

The baselines capture **qemu-img dd's** result info across all
installed qemu versions; the consuming test runs **instar dd** on
the same fixture and asserts its result info matches the captured
qemu baseline (per profile). This complements the live
cross-validation from phases 3/4/6 (which runs against a single
installed qemu) with a committed, multi-version reference.

Two repos are involved:
- **instar-testdata** (`../instar-testdata`): generator + Makefile
  target + the generated `expected-outputs/dd-info-json/` tree.
- **instar** (this repo): the consuming test + docs.

## Design

### Mirror `resize`/`amend`, not the image-driven commands

`info`/`check`/`measure`/`map` iterate manifest images. The
**producing** commands (`create`, `resize`, `amend`, `rebase`,
`commit`) instead use **curated operand-driven case lists** and
**procedurally created fixtures** (`qemu-img create`). dd is a
producing command, so it follows the producing pattern. `resize` is
the closest analog: its pipeline is `qemu-img create -f FMT <tmp>
<start>` → `qemu-img resize ...` → `qemu-img info --output=json`,
driven by `RESIZE_CASES` of `(case_name, start_size, end_spec,
create_opts, prealloc)`. dd's pipeline is `qemu-img create -f
<in_fmt> <tmp> <size>` → `qemu-img dd <operands> -O <out_fmt> if=…
of=…` → `qemu-img info --output=json <out>`.

The generator runs **qemu-img** throughout (`str(binary)` is the
per-version qemu-img). It does **not** run instar — the baseline is
the qemu reference. (The exploration that fed this plan initially
suggested generating from the instar binary; that is wrong and must
not be done.)

### dd output info doesn't depend on input data

A windowed dd's output `virtual-size`/`format` derive from the
input virtual size + window + output format, not the input's data
content. So fixtures can be empty `qemu-img create` images (no
`qemu-io` pattern needed), exactly like resize/amend. `actual-size`
(allocation) is normalised at test time by the existing
`substitute_actual_size` helper.

### Curated `DD_CASES` — target the cross-version-sensitive behaviour

The dd-specific thing worth pinning across versions is the **output
virtual-size rounding** (qcow2/vmdk/vhdx → `round_up(out_vsize,
512)`, vhd → CHS) and the **empty-window** per-format behaviour.
Whole-image dd (out_vsize == input size, already 512-aligned)
exercises *no* rounding, so the list must include windowed cases.
Keep it curated (~20–25 cases), not a manifest × format matrix.

Representative case axes (the implementer finalises exact names):
- **Input formats:** `raw` and `qcow2` (output-rounding is largely
  input-format-independent; vmdk/vhd/vhdx *inputs* are covered by
  phase-6 live tests).
- **Output formats:** `raw`, `qcow2`, `vmdk`, `vpc` (vhd), `vhdx`.
- **Windows:**
  - whole-image (baseline sanity per output format),
  - `bs=1000 count=3` — non-512 `out_vsize` 3000 → exercises the
    512 rounding (and VHD's CHS 34816), the highest-value cross-
    version case **per output format**,
  - `bs=65536 skip=2 count=4` — an aligned window,
  - `count=0` — empty window **per output format** (captures the
    qcow2/vpc readable-vsize-0, the vmdk qemu-exits-1, and the vhdx
    behaviour; note the known `count=0 -O vhdx` instar limitation —
    see below).

A `DD_CASES` shape like `RESIZE_CASES`:
`(case_name, input_size, input_format, window_operands, output_format)`,
e.g. `('1M-raw-bs1000-count3-vhd', '1M', 'raw', ['bs=1000','count=3'], 'vpc')`.

### Versions where `qemu-img dd -O` is unsupported

`-f`/`-O` were added to `qemu-img dd` in a later qemu series; very
old versions in `qemu-img-binaries/` may reject `-O` (or `dd`
entirely). The generator records the non-zero exit in the
`.meta.json` (as resize/amend already do for unsupported
transitions), and the consuming test **skips** any profile/version
whose baseline meta shows a non-zero qemu exit. Report which
versions are skipped.

### Known limitation interaction

`count=0 -O vhdx`: instar's empty VHDX is rejected by `qemu-img
info` (master plan Future work). For that single case the consuming
test must **not** compare instar's info to the qemu baseline (which
*is* readable) — assert instar exit 0 only, matching the phase-4
handling. The generator still records qemu's baseline normally.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | In **`../instar-testdata/scripts/generate-baselines.py`**, add dd following `resize` exactly. Read `generate_resize_baseline` (≈1511) and `RESIZE_CASES` (≈433) and the `resize` `COMMANDS` entry (≈160) + main dispatch. Add: a `'dd'` `COMMANDS` entry with `'output_types': {'dd-info-json': 'json'}` and `dd_cases`; a `DD_CASES` curated list (axes per the Design — ~20–25 cases); `generate_dd_baseline(binary, version, case_name, input_size, input_format, window_operands, output_format, output_dir, tmp_dir, ...)` whose pipeline is `qemu-img create -f <input_format> <tmp_in> <input_size>` → `qemu-img dd <window_operands> -O <qemu_out_fmt> if=<tmp_in> of=<tmp_out>` → `qemu-img info --output=json <tmp_out>`, writing `<case>.stdout.txt` (info JSON, `$FILENAME`-normalised), `<case>.stderr.txt` (dd stderr + marker + info stderr), `<case>.meta.json` (both exit codes, window operands, input/output formats); the `main()` dispatch branch iterating `DD_CASES`; and the `'dd'` output-dir layout matching resize. Map `vpc`↔ instar's vhd naming as resize/create do for their format args. Run a one-version sanity gen (e.g. `generate-baselines.py --command dd --version <newest>` or the resize-equivalent flag) and show one produced `dd-info-json/.../<case>.stdout.txt` is valid JSON. Then in **`../instar-testdata/Makefile`** add a `baselines-dd` target mirroring `baselines-amend`/`baselines-resize`. Report the diff and the sample output. Do NOT run the full multi-version generation yet (step 7b) and do NOT commit. |
| 7b | — | — | — | **Management/operator-run, not a sub-agent.** From `../instar-testdata`: `make baselines-dd` (full multi-version generation across `qemu-img-binaries/`) then `make profiles` (`detect-profiles.py` regenerates `dd-info-json/profiles/` + `version-map.json`). Inspect: confirm `expected-outputs/dd-info-json/` populated, the profile count is sane, and which old versions were skipped (no dd `-O`). This runs the real qemu binaries; the management session does it and reviews output. The testdata repo is a separate repo with a protected `main` — committing/pushing it is an operator step (see [[testdata-push-token]]). |
| 7c | medium | sonnet | none | In the **instar** repo, add the consuming baseline test (in `tests/test_dd.py` or a `tests/test_dd_baselines.py`), mirroring the `resize`/`amend` baseline test (read `tests/test_resize.py` / `tests/test_amend.py` and the `get_output_profiles` / `get_expected_output` helpers in `tests/base.py` ≈111–243, plus `substitute_testdata_root` / `substitute_actual_size` in `tests/helpers/comparators.py`). For each dd case × profile: recreate the fixture with `qemu-img create` (same spec the generator used), run `instar dd <window> -O <fmt>`, run `qemu-img info --output=json` on instar's output, normalise `$FILENAME`/`actual-size`, and assert it equals the loaded baseline. Skip any (case, version/profile) whose baseline meta records a non-zero qemu exit. Special-case `count=0 -O vhdx`: assert instar exit 0 only (do not compare info). Add the `dd` mapping to `COMMAND_OUTPUT_DIRS` in `tests/base.py` if needed (e.g. `'dd': 'dd-info'`). Gate the test so it skips cleanly when the `dd-info-json` baselines are absent (until 7b is committed in testdata). Run `^test_dd\.` and report. |
| 7d | low | sonnet | none | Docs: note `dd-info-json` baselines in `ARCHITECTURE.md` (alongside the other `*-info-json` baseline types) and add a `CHANGELOG.md` entry. (`README.md`/`AGENTS.md` only if they enumerate baseline types.) Flip the phase-7 row in this plan + the master plan after 7b/7c land. |

## Verification

- [ ] `generate-baselines.py --command dd` produces valid
      `dd-info-json` baselines; `make baselines-dd` + `make profiles`
      populate `expected-outputs/dd-info-json/` + `version-map.json`.
- [ ] The consuming test passes (`^test_dd\.`), iterating profiles,
      with instar dd's result info matching the qemu baseline for
      every supported (case, version); unsupported old versions are
      skipped via meta exit codes.
- [ ] `count=0 -O vhdx` is asserted exit-0-only (not info-compared).
- [ ] The dd-specific rounding (512 for qcow2/vmdk/vhdx, CHS for
      vhd) is represented in the baselines via the `bs=1000 count=3`
      cases — confirm the captured `virtual-size` values match the
      phase-4 findings (3072 / 34816).
- [ ] `pre-commit run --all-files` passes in the instar repo.
- [ ] instar-repo changes limited to `tests/` + docs; testdata-repo
      changes are the generator, Makefile target, and generated
      baselines.
- [ ] Commit messages follow conventions (model/context/effort).
      The testdata repo is committed/pushed as an operator step.

## Hand-off

Remaining phases: 8 coverage-guided fuzzing (operand parser, window
math, `chs_rounded_size`, read primitives), 9 differential fuzzing
vs qemu-img dd (random `bs`/`count`/`skip`/`-O`; resolve the
`count=0 -O vhdx` limitation here), 10 docs. The
[[dd-qemu-img-parity-contract]] memory records the verified rules.
