# PLAN-bitmap phase 08: cross-version baselines

## Prompt

Before responding to questions or discussion points in this
document, explore both repos thoroughly. Read the actual code and
ground every claim in it — the baseline generator
`../instar-testdata/scripts/generate-baselines.py` (its
`AMEND_CASES`, `generate_amend_baseline`, the `COMMANDS` dict, the
per-version dispatch, and the commit block), the consumer side in
`tests/test_amend.py` (`TestAmendBaselineMatrix`,
`_make_amend_baseline_test`, `_baseline_version_dir`) and
`tests/helpers/info_json.py` (`normalise_info_json` /
`assert_info_equivalent`), and the amend baselines phase plan
`docs/plans/PLAN-amend-phase-07-baselines.md`. Do not speculate.
Flag any uncertainty explicitly.

Phase plans live in `docs/plans/` as
`PLAN-bitmap-phase-NN-<descriptive>.md`. The master plan is
[PLAN-bitmap.md](/components/instar/plans/PLAN-bitmap/); Phases 1–7 are complete —
`instar bitmap` runs end-to-end and passes a 35-test `qemu-img`
integration suite. This is the eighth of ten phases. **It spans two
repos** (instar + the sibling `instar-testdata`) and the testdata
**push is operator-gated**. One commit per logical change.

## Situation

Cross-version baselines let the integration suite run green on any
CI host regardless of its installed `qemu-img` version, by comparing
instar's output against a **version-matched stored baseline** rather
than a single live oracle. This phase adds bitmap to that
machinery, mirroring `amend`.

**The model, and why instar's missing `info` bitmaps don't break
it.** The baseline matrix does **not** compare instar-`info` to a
stored baseline. It runs the *op* with **instar** on a
qemu-img-created image, then reads the result with the **system
`qemu-img info --output=json`**, and compares that to a stored
`qemu-img info` recorded from a *pure-qemu* pipeline at the matching
version. qemu-img is the oracle observing instar's output. Since
`qemu-img info` emits the bitmaps array (`format-specific.data.
bitmaps`, entries `{name, granularity, flags}`), and instar's own
`info` emitting no bitmaps is irrelevant here, the amend baseline
model transfers **directly** (this is option (a) from the phase-8
research). What it validates: bitmap **directory metadata** (which
bitmaps exist, their granularity, enabled/disabled/in-use flags) is
consistent across the qemu-img version matrix when instar produces
the image. (Actual **dirty-bit content** is invisible to `info` and
is validated separately by the Phase-7 NBD `x-dirty-bitmap` oracle —
not part of the info-json baseline, exactly as amend's data
equivalence uses `qemu-img compare`, not the baseline.)

**Honest value assessment.** The bitmaps info format (name /
granularity / flags) has been stable since ~qemu 4.1, so unlike
`info`/`amend` (where the JSON genuinely drifts across versions,
e.g. the 8.0+ "Child node" section) the bitmap baselines will be
nearly identical across versions. The value is therefore mostly
(a) **parity** with the other subcommands' test scaffolding, (b)
**future-proofing** against any qemu info-format change, and (c)
confirming instar's on-disk bitmap layout is **read-consistent
across the whole qemu-img range**, not just 10.0.8. Real enough to
do; modest enough not to over-engineer.

Grounding (verified against the phase-8 research, HEAD `89b0ea5`):

- **Baselines live in `../instar-testdata`** (`Makefile:469`
  `TESTDATA_PATH ?= $(CURDIR)/../instar-testdata`), layout
  `expected-outputs/<cmd>-info-json/<target>/<version>/<case>.{stdout.
  txt,stderr.txt,meta.json}`. amend uses
  `expected-outputs/amend-info-json/qcow2/<version>/`.
- **Generator** `../instar-testdata/scripts/generate-baselines.py`:
  `AMEND_CASES` (`:550-559`, a dict of `(name, create_opts,
  amend_opts)` tuples), `generate_amend_baseline` (`:1837-2039`,
  per case: `qemu-img create` → `qemu-img amend` → `qemu-img info
  --output=json`, writing the three files), `COMMANDS['amend']`
  config (`:172-186`, `output_types: {'amend-info-json': 'json'}`,
  `targets: ['qcow2']`), the dispatch loop building `output_root /
  output_type / target / version` (`:3018-3060`), and the commit
  block (`:3510-3557`, gated by `command_name in (...)` at
  `:3515-3518`; `--no-commit` skips it). The version matrix is
  **80 compiled binaries** `qemu-img-binaries/x86_64/<version>/`
  (6.0.0 → 10.2.0; `get_qemu_binaries` `:850-873`) — **no docker**.
  `qemu-img bitmap` exists even in 6.0.0 (no version floor).
- **`make baselines-amend`** (`instar-testdata/Makefile:210`):
  `generate-baselines.py --command amend --no-commit`. Not in the
  umbrella `baselines:` target (operator-run).
- **Consumer** `tests/test_amend.py`: `TestAmendBaselineMatrix`
  (`:645`) + `_make_amend_baseline_test` (`:716-824`) — create
  start with system `qemu-img`, run **instar** amend, read result
  with system `qemu-img info`, `assert_info_equivalent(self,
  info_stdout, stored_baseline, 'qcow2', tmp_path=path)`;
  `_baseline_version_dir` (`:126-149`) picks the baseline dir
  matching the host qemu major.minor (else newest); skips cases the
  baseline `meta.json` recorded as failing, or in
  `KNOWN_AMEND_DIVERGENCES`. `test_amend_cases_match_baselines`
  (`:684-713`) is a drift tripwire (on-disk case stems ==
  `_BASELINE_CASES`).
- **`normalise_info_json`** (`tests/helpers/info_json.py:85-123`)
  strips `actual-size`/`dirty-flag`/cache-hints/random-ids/nested
  `virtual-size` but **does NOT touch `format-specific.data.bitmaps`
  nor sort it** — so a multi-bitmap array is compared order-
  sensitively (Open question 2).
- **No manifest entry needed**: amend/resize/dd/create self-generate
  ephemeral `qemu-img create` fixtures; bitmap does the same. No
  `tests/manifest.json` change.
- **Push is operator-gated**: the generator commits into
  `instar-testdata` (unless `--no-commit`); the agent generates on a
  dedicated `instar-testdata` branch and commits locally, and does
  **not push** — pushing to protected `main` needs the operator's
  `GITLAB_TESTDATA_PUSH_TOKEN` (per
  `PLAN-amend-phase-07-baselines.md:223-237`).

## Mission and problem statement

Add bitmap to the cross-version baseline machinery:

1. **Generator (`instar-testdata` repo)**: a `BITMAP_CASES` table, a
   `generate_bitmap_baseline()` (per case: `qemu-img create` → a
   **sequence** of `qemu-img bitmap …` ops → `qemu-img info
   --output=json`; write `<case>.{stdout.txt,stderr.txt,meta.json}`),
   a `COMMANDS['bitmap']` config (`output_types:
   {'bitmap-info-json': 'json'}`, `targets: ['qcow2']`), the dispatch
   branch, inclusion in the commit `command_name` set, and a
   `baselines-bitmap` Makefile target. Note the one shape difference
   from amend: a bitmap case runs a **list of bitmap invocations**
   (e.g. add then disable), not a single command.

2. **Normaliser (instar repo)**: extend `normalise_info_json` to
   **sort `format-specific.data.bitmaps` by `name`** (Open question
   2), so multi-bitmap cases compare order-insensitively. Small,
   reusable, correct; keep it targeted to that array.

3. **Consumer (instar repo)**: `TestBitmapBaselineMatrix` +
   `_make_bitmap_baseline_test` in `tests/test_bitmap.py` (mirror
   `TestAmendBaselineMatrix`): create start with system `qemu-img`,
   run the case's op sequence with **instar bitmap** (each op via
   `run_instar_bitmap`), read the result with system `qemu-img info
   --output=json`, `assert_info_equivalent` against the
   version-matched stored baseline; plus a
   `test_bitmap_cases_match_baselines` drift tripwire and a
   `KNOWN_BITMAP_BASELINE_DIVERGENCES` gate.

4. **Generate + commit the baselines (`instar-testdata` repo)**: run
   `make baselines-bitmap` (generates for the full installed version
   matrix), review a sample, and commit locally on an
   `instar-testdata` branch — **do not push** (operator-gated).

5. The baseline-matrix tests pass against the generated baselines on
   the host qemu-img version; the rest of the suite and harness are
   unaffected.

**Out of scope:** coverage + differential fuzzing (Phase 9); docs
(Phase 10); pushing `instar-testdata` to `main` (operator). No
instar production changes (test-helper + test only).

## Open questions

### 1. Which cases to baseline (the metadata surface)

Propose `BITMAP_CASES['qcow2']` as `(name, create_opts, [op_args…])`
where each op is a `qemu-img bitmap` arg list (the target name is
the trailing positional; the generator supplies the tmp path):
- `add-default` — `[['--add','b0']]` ⇒ b0 auto, granularity =
  cluster default (65536 at the default cluster).
- `add-granularity` — `[['--add','-g','131072','b0']]` ⇒ gran 131072.
- `add-disabled` — `[['--add','--disable','b0']]` ⇒ b0 disabled
  (ordered, one invocation).
- `disable` — `[['--add','b0'],['--disable','b0']]`.
- `enable` — `[['--add','b0'],['--disable','b0'],['--enable','b0']]`.
- `clear` — `[['--add','b0'],['--clear','b0']]` ⇒ b0 present, auto.
- `two-bitmaps` — `[['--add','b0'],['--add','-g','131072','b1']]` ⇒
  two bitmaps (exercises the array sort).
- `remove-last` — `[['--add','b0'],['--remove','b0']]` ⇒ no bitmaps
  (extension dropped).
Confirm the set in 8a. Keep to compat=1.1 default cluster (bitmaps
need v3); cluster-size variation is covered by Phase 7's live
matrix, not needed in the baseline set.

### 2. Bitmaps-array ordering

`normalise_info_json` doesn't sort the bitmaps array; instar's and
qemu's directory order *should* match for identical op sequences
(both append), but sort by `name` to be robust — the `two-bitmaps`
case depends on it. Extend the normaliser (Mission §2). This is the
same order-safety the Phase-7 `assert_bitmaps_equivalent` already
applies; unifying it in the normaliser is cleaner.

### 3. Generate the full 80-version matrix, or a subset?

The generator iterates all installed `qemu-img` binaries; amend has
80 version-dirs. For parity, generate the full set (it is
mechanical, ~minutes). If runtime/size is a concern, note it — but
the convention is one dir per built binary, and the consumer picks
the host-matched dir, so a partial set would fail on hosts whose
version isn't present. **Recommend the full matrix.** Confirm the
`instar-testdata` binaries are present (research found 80).

### 4. Cross-repo commit + operator gating

instar-side changes (info_json.py sort, test_bitmap.py matrix) land
on the **bitmap branch** (instar repo). instar-testdata-side changes
(generator, Makefile, generated `expected-outputs/bitmap-info-json/`)
land on an **`instar-testdata` branch** and are committed locally but
**not pushed** (operator holds the push token). The plan's steps
must keep the two repos' commits separate and clearly labelled.
Confirm `../instar-testdata` is a writable git repo in this
environment.

### 5. Value vs effort

Bitmap metadata is version-stable, so the baselines mostly provide
parity + future-proofing (Situation). Do the standard thing for
consistency, but do not gold-plate: single canonical cluster size,
the ~8 cases above, no manifest fixture. If generation reveals the
baselines are byte-identical across all 80 versions, that's the
expected (good) outcome — record it.

## Step-level guidance

Plan at **medium** effort (well-trodden generator + consumer
scaffolding; the one novelty is the per-case op *sequence*). The
generation run is mechanical but heavy; the consumer test is the
part that needs care.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | sonnet | none | In `../instar-testdata/scripts/generate-baselines.py`: add `BITMAP_CASES` (Open question 1), `generate_bitmap_baseline()` (mirror `generate_amend_baseline` `:1837-2039`, but run the case's **list** of `qemu-img bitmap` ops in order — `qemu-img bitmap <op_args> <tmp> <name>`; the name is the trailing token of each op list, tmp inserted before it; then `qemu-img info --output=json`; write the three files; record all rc's + the op lists in `meta.json`), `COMMANDS['bitmap']` (`output_types: {'bitmap-info-json':'json'}`, `targets:['qcow2']`), the dispatch branch (mirror `:3018-3060`), and include `'bitmap'` in the commit `command_name` set (`:3515-3518`) + a manual-commit hint. Add `baselines-bitmap:` to `../instar-testdata/Makefile` (mirror `:210`). Test-generate ONE version: `python scripts/generate-baselines.py --command bitmap --version <host> --no-commit` and inspect the produced `expected-outputs/bitmap-info-json/qcow2/<host>/*.stdout.txt` (confirm the bitmaps arrays look right). Report the generator diff + a sample baseline. Do NOT commit yet. |
| 8b | medium | sonnet | none | In the instar repo (bitmap branch): (1) extend `tests/helpers/info_json.py` `normalise_info_json` to sort `format-specific.data.bitmaps` by `name` (Open question 2), guarded for missing keys; keep it minimal + add a comment. (2) Add `TestBitmapBaselineMatrix` + `_make_bitmap_baseline_test` + `_baseline_version_dir`/`_baseline_stdout`/`_baseline_meta` helpers to `tests/test_bitmap.py` (mirror `test_amend.py:126-149,645,716-824`): pick the host-matched `expected-outputs/bitmap-info-json/qcow2/<version>/` dir, skip cases the baseline meta recorded as failing or in `KNOWN_BITMAP_BASELINE_DIVERGENCES = {}`, run the op sequence with `run_instar_bitmap`, read with system `qemu-img info`, `assert_info_equivalent` vs the stored baseline; plus `test_bitmap_cases_match_baselines` (on-disk stems == the case list) and `_require_kvm` on the guest-launching matrix tests. This step CANNOT fully run until 8c generates the baselines — so gate the matrix on the baseline dir existing (skipTest if absent), and verify compile/collection via `make lint` + a stestr collection run. Report the test code. |
| 8c | medium | sonnet | none | Generate + verify. In `../instar-testdata`: run `make baselines-bitmap` (full version matrix), spot-check a few versions' outputs, and commit locally on an `instar-testdata` branch (e.g. `bitmap-baselines`) — **do NOT push** (operator-gated; note this in the report). Back in the instar repo: `make instar`, then run `cd tests && ../tests/.venv/bin/stestr run test_bitmap` and confirm `TestBitmapBaselineMatrix` now runs (not skipped) and passes against the host-version baselines, and `test_bitmap_cases_match_baselines` passes; `make lint`; `pre-commit run --all-files`. Mark the phase-8 row complete in `docs/plans/PLAN-bitmap.md` + `index.md` (note the testdata push is operator-gated). Report: the generation summary (versions × cases, whether baselines are identical across versions), the matrix test result, and the two repos' local commit state (instar committed; instar-testdata committed-not-pushed). |

## Management session review checklist

- [ ] The baseline model is understood correctly: **qemu-info of
      instar output** vs stored qemu pipeline (not instar-info).
- [ ] `BITMAP_CASES` covers the metadata surface; the generator runs
      a per-case **op sequence** (the amend-vs-bitmap difference).
- [ ] `normalise_info_json` sorts the bitmaps array by name
      (order-safe); the change is minimal + guarded.
- [ ] `TestBitmapBaselineMatrix` mirrors amend; skips failing/
      divergent cases; `_require_kvm`s; the drift tripwire is present.
- [ ] Baselines generated for the full version matrix; the matrix
      test passes against the host-matched dir.
- [ ] **Cross-repo hygiene**: instar changes on the bitmap branch;
      `instar-testdata` changes committed on its own branch and
      **NOT pushed** (operator-gated) — clearly reported.
- [ ] No instar production code changed (test-helper + test only);
      `bitmap.bin`/`core.bin` unchanged.
- [ ] `make lint` / `make test-rust` / the `test_bitmap` stestr run /
      `pre-commit run --all-files` green.
- [ ] Commit messages follow conventions.

## Success criteria

- The generator produces `expected-outputs/bitmap-info-json/qcow2/
  <version>/` for the full installed matrix; a `baselines-bitmap`
  Makefile target exists.
- `normalise_info_json` sorts the bitmaps array;
  `TestBitmapBaselineMatrix` runs instar's op sequences and matches
  the version-matched `qemu-img` baselines; the drift tripwire
  passes.
- instar-side changes are committed on the bitmap branch;
  `instar-testdata` baselines are committed locally and left for the
  operator to push (gated).
- All instar gates green; no production code changed.

## Back brief

Before executing any step, the executing agent should back brief the
operator — in particular that the baseline compares **qemu-img info
of instar's output** against a stored pure-qemu baseline (so instar's
missing `info` bitmaps are irrelevant), that a bitmap case runs a
**sequence** of `qemu-img bitmap` ops (unlike amend's single
command), that the bitmaps array must be **sorted by name** for
order-safety, that this phase **spans two repos**, and that the
`instar-testdata` baselines are committed locally but **not pushed**
(operator-gated).
