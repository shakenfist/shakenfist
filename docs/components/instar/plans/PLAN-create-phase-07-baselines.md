# Phase 7: cross-version `qemu-img create` baselines

Master plan: [PLAN-create.md](/components/instar/plans/PLAN-create/) ·
Previous phase: [PLAN-create-phase-06-preallocation.md](/components/instar/plans/PLAN-create-phase-06-preallocation/)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the baseline
generator, `detect-profiles.py` dedup flow, how phase 6 of
`PLAN-measure.md` shipped its analogous matrix, how
`tests/test_measure.py` consumes versioned baselines, how the
testdata repo layout works under `expected-outputs/`), and ground
answers in what the code actually does today. Where a question
touches on external concepts (`qemu-img create` flag matrix per
qemu version, `qemu-img info --output=json` schema drift across
6.0.0 → 10.2.0, qcow2 / vmdk / vhd / vhdx on-disk layout
differences in `qemu-img create` defaults), research as needed.
Flag uncertainty explicitly rather than guessing.

## Status: Not started

## Mission

Extend `instar-testdata`'s baseline generator so that for every
qemu-img binary in `qemu-img-binaries/x86_64/` (~80 versions
from 6.0.0 to 10.2.0) we record, for each `(target_format,
options, size)` case in a curated matrix:

1. `qemu-img create -f <target> [-o KEY=VAL,...] <tmpfile> <size>`
   — exit code + stderr ("Formatting '...' ..."), then
2. `qemu-img info --output=json <tmpfile>` — stdout (the info
   JSON, which is the comparable artefact).

The recorded baselines feed phase 8's integration tests, which
compare `instar info --output=json` on an `instar create`-
produced image against the version-matched qemu baseline,
asserting field equivalence (modulo the documented divergence
whitelist — uuid, mtime, tool version, `dirty` flag, etc.).

Phase 7 is implementation in `instar-testdata/`, not the instar
repo. The only instar-repo edits are this plan file and one
`CHANGELOG.md` / `ARCHITECTURE.md` paragraph in the last step.

## Why this is its own phase

Decomposes cleanly into:

1. **Script edit** (medium): teach `generate-baselines.py` and
   `detect-profiles.py` to understand a fourth command (`create`)
   whose semantics differ from `measure`: there is no source-
   image axis, every case is a `(target_format, options, size)`
   triple, and the comparable output is `qemu-img info`'s JSON
   on the *produced* file rather than `qemu-img create`'s own
   stdout (which is only a "Formatting ..." log line and the
   exit code).
2. **Long-running data generation** (low effort, slow wall
   clock): ~80 qemu versions × ~30 cases per version × 2 steps
   (create + info) ≈ 4 800 invocations. Each is sub-second;
   total ~20 minutes serial.
3. **Commit the artefact** to the testdata repo.

Splitting from phase 8 means the baselines exist *before* the
test code that consumes them; phase 8 is then pure test
plumbing.

## What the survey turned up

### `instar-testdata/scripts/generate-baselines.py` shape today

`COMMANDS` dict at lines 68-132 has entries for `info`, `check`,
`compare`, and `measure`. The `main()` dispatch loop at lines
740-859 has an explicit `if command_name == 'measure':` branch
covering measure's two sub-modes (`--size` cases and source-
image × target_format cases) and an `else:` branch covering the
standard one-image-per-invocation commands.

`measure` was wired up in phase 6 of `PLAN-measure.md` via:

- A `'measure'` entry in `COMMANDS` with extra `target_formats`
  and `size_cases` keys.
- A module-level `SIZE_CASES` const (lines 139-174) listing
  `(case_name, size_str, target_format, options_list)` tuples,
  patched into `COMMANDS['measure']['size_cases']` after the
  dict literal closes.
- Two new generator functions: `generate_measure_size_baseline`
  (line 440) and `generate_measure_source_baseline` (line 525).
- A dedicated dispatch branch in `main()` for the measure
  command at line 758.

Phase 7 mirrors this pattern for `create` with one structural
difference: there is no source-image sub-mode, so the dispatch
branch only iterates `size_cases`.

### `qemu-img create` invocation shape

```
qemu-img create -f <FMT> [-o KEY=VAL[,KEY=VAL...]] <FILE> <SIZE>
```

- Exit 0 on success, 1 on most failures.
- Always writes a one-line "Formatting '<file>', fmt=...
  size=..." to stderr regardless of `-q` (qemu-img has no
  reliable suppression for this; we capture stderr verbatim).
- Output file path is positional — we use a `tempfile`-scoped
  path under the testdata repo's temp dir.
- After success, `qemu-img info --output=json <FILE>` produces
  the JSON the integration tests will compare against.

`qemu-img create` validates options against the *running*
qemu-img version's known key set; on old binaries newer options
(e.g. `extended_l2=on` before 5.0, `compression_type=zstd`
before 5.1) return non-zero with "Unknown option". We record
the failure verbatim — phase 8 will skip comparison when the
baseline's create-step exit is non-zero, matching the
info/check/measure precedent.

### What instar implements that we want baselined

Phases 1–6 of `PLAN-create.md` ship:

- **qcow2**: `cluster_size`, `refcount_bits`, `extended_l2`,
  `lazy_refcounts`, `compat`, `preallocation` (off / metadata /
  falloc / full), `compression_type` (accept-ignore), backing
  (`-b` / `-F` / `-u`).
- **vmdk**: `subformat` (`monolithicSparse`,
  `streamOptimized`), `grain_size`, backing.
- **vhd (vpc)**: `subformat` (`dynamic`, `fixed`),
  `force_size` (accept-ignore), backing.
- **vhdx**: `subformat` (`dynamic` only), `block_size`,
  `log_size` (accept-ignore), backing.
- **raw**: `preallocation` (off, falloc, full) — but raw has no
  metadata to baseline since `qemu-img info` on a raw file
  just reports size + format. We baseline raw anyway as a
  sanity floor (any drift in info's raw JSON schema becomes
  visible).

Backing-file cases need a parent fixture; defer to phase 8's
integration tests which can construct chains at test time
(matching what `tests/test_create.py::TestCreateBackingChain`
already does in phase 5). Phase 7 baselines stick to the no-
backing matrix.

### Size matrix discipline

The master plan suggested 1M / 1G / 1T as the size sweep. For
phase 7 we restrict to **1M, 64M, 1G** as the canonical sweep
and **drop 1T** for these reasons:

- The info JSON for a 1T qcow2 differs from a 1G qcow2 only in
  the `virtual-size` field and the `lazy-refcounts` /
  `extended-l2`-influenced `cluster_size` derivations — every
  *other* field is sweep-independent. So 1T adds little
  coverage beyond 1G.
- `preallocation=full` and `preallocation=falloc` at 1T would
  actually allocate 1 TiB on disk during baseline generation.
  Even capped to 1G these modes need real disk space.
- 1T fixtures stay sparse for `off`/`metadata` but the create
  step takes noticeably longer on some qemu versions where the
  refcount table is written eagerly.

For `preallocation=falloc` and `preallocation=full` we cap at
**1M** in the case list — these modes write real blocks and a
1G allocation per case × 80 versions = wall-clock waste.

### Output schema

```
instar-testdata/expected-outputs/
└── create-info-json/                          # only output type
    ├── qcow2/
    │   ├── 6.0.0/
    │   │   ├── 1M-default.stdout.txt           # info JSON
    │   │   ├── 1M-default.stderr.txt           # create + info stderr combined
    │   │   ├── 1M-default.meta.json
    │   │   ├── 1G-cs-64k.stdout.txt
    │   │   ├── 1G-extended-l2.stdout.txt
    │   │   ├── 1M-prealloc-full.stdout.txt
    │   │   ...
    │   ├── 7.2.0/
    │   ...
    ├── vmdk/
    ├── vhd/
    ├── vhdx/
    ├── raw/
    ├── profiles/                               # detect-profiles dedup buckets
    │   └── profile-NN/
    └── version-map.json
```

Key conventions:

- One output type — `create-info-json`. No human variant: the
  info-human output embeds the absolute path which complicates
  cross-host portability, and the JSON view is the contract.
- Per-format bucket directly under `create-info-json/`
  (`qcow2/`, `vmdk/`, `vhd/`, `vhdx/`, `raw/`); no `_size/`
  pseudo-bucket because every create case is "size-mode" — the
  format-axis is the natural grouping.
- `<case-name>` = `<size>-<options-key>` where `<options-key>`
  is `default`, `cs-64k`, `extended-l2`, `prealloc-metadata`,
  etc. — same naming convention measure uses.
- `meta.json` records the exit codes for both the `create` step
  and the `info` step, plus stderr byte counts, plus the
  options list as raw strings (so phase 8 can reconstruct the
  invocation).

### Generator function signature

A single new function:

```python
def generate_create_baseline(
    binary: Path,
    version: str,
    case_name: str,
    size_str: str,
    target_format: str,
    options_list: list[str],
    output_dir: Path,
    tmp_dir: Path,
    timeout: int = 60,
) -> dict:
    """
    Generate one create + info baseline.

    Pipeline:
      1. qemu-img create -f TARGET [-o KEY=VAL,...] <tmp> SIZE
      2. if (1) succeeded: qemu-img info --output=json <tmp>
      3. write <case_name>.stdout.txt = info JSON
      4. write <case_name>.stderr.txt = create stderr + (if
         info ran) info stderr
      5. write <case_name>.meta.json with both exit codes
    Always deletes the tmp file before returning.
    """
```

The `tmp_dir` argument is a per-version subdir under
`/tmp/create-baselines/<version>/` so concurrent generator runs
(if ever parallelised) don't collide.

### `CREATE_CASES` shape

Per-target case lists. Following the measure precedent, define
as a module-level dict near the top of the script:

```python
# Curated cases for create baselines.
# Each entry is (case_name, size_str, options_list).
# options_list entries are passed as individual -o arguments.
# Non-zero exits (e.g. extended_l2=on before 5.0) recorded verbatim.
CREATE_CASES = {
    'qcow2': [
        # Sizes only
        ('1M-default',              '1M',  []),
        ('64M-default',             '64M', []),
        ('1G-default',              '1G',  []),
        # cluster_size sweep at 1G
        ('1G-cs-512',               '1G',  ['cluster_size=512']),
        ('1G-cs-4k',                '1G',  ['cluster_size=4k']),
        ('1G-cs-64k',               '1G',  ['cluster_size=64k']),
        ('1G-cs-1M',                '1G',  ['cluster_size=1M']),
        ('1G-cs-2M',                '1G',  ['cluster_size=2M']),
        # refcount_bits
        ('1G-rb-1',                 '1G',  ['refcount_bits=1']),
        ('1G-rb-8',                 '1G',  ['refcount_bits=8']),
        ('1G-rb-64',                '1G',  ['refcount_bits=64']),
        # extended_l2 (requires qemu-img >= 5.0)
        ('1G-extended-l2',          '1G',  ['extended_l2=on,cluster_size=64k']),
        ('64M-extended-l2',         '64M', ['extended_l2=on,cluster_size=64k']),
        # compat
        ('1G-compat-v2',            '1G',  ['compat=0.10']),
        # lazy_refcounts
        ('1G-lazy-refcounts',       '1G',  ['lazy_refcounts=on']),
        # preallocation (capped at 1M for falloc/full to avoid 1G of disk per case)
        ('1M-prealloc-metadata',    '1M',  ['preallocation=metadata']),
        ('1M-prealloc-falloc',      '1M',  ['preallocation=falloc']),
        ('1M-prealloc-full',        '1M',  ['preallocation=full']),
    ],
    'vmdk': [
        ('1M-default',              '1M',  []),                                 # monolithicSparse default
        ('64M-default',             '64M', []),
        ('1G-default',              '1G',  []),
        ('1G-stream-optimized',     '1G',  ['subformat=streamOptimized']),
        ('1G-monolithic-sparse',    '1G',  ['subformat=monolithicSparse']),
        ('1G-grain-4k',             '1G',  ['subformat=monolithicSparse']),     # qemu default grain
        # grain_size sweep (instar exposes --grain-size; qemu-img does not have
        # an -o key for grain). For phase 7 we keep grain at default and let
        # phase 8 cover instar-only grain combinations via round-trip checks.
    ],
    'vhd': [
        ('1M-default',              '1M',  []),                                 # dynamic by default
        ('64M-default',             '64M', []),
        ('1G-default',              '1G',  []),
        ('1M-fixed',                '1M',  ['subformat=fixed']),                # fixed allocates SIZE bytes
        ('16M-fixed',               '16M', ['subformat=fixed']),
    ],
    'vhdx': [
        ('1M-default',              '1M',  []),
        ('64M-default',             '64M', []),
        ('1G-default',              '1G',  []),
        ('1G-block-16M',            '1G',  ['block_size=16M']),
        ('1G-block-32M',            '1G',  ['block_size=32M']),
    ],
    'raw': [
        # raw produces no metadata; baselines exist as a sanity floor
        # against info-JSON schema drift.
        ('1M-default',              '1M',  []),
        ('1G-default',              '1G',  []),
    ],
}
```

Rough totals: 18 qcow2 + 6 vmdk + 5 vhd + 5 vhdx + 2 raw = **36
cases per version**. 36 × 80 versions × 3 files per case
(stdout / stderr / meta.json) = **~8 640 files**, ≈ 15 MiB on
disk (within the testdata repo's existing baseline footprint).

### `qemu-img create` argument shape per format

qemu-img uses `-f` for *source* format and `-O` for *target*
format in commands like `convert`, but for `create` the format
is set with `-f`. We pass `-f <target_format>` per case. For
target_format=`vhd`, qemu-img accepts both `vpc` (canonical)
and `vhd`. Phase 7's case dict uses `vhd` for the directory
name (matches the instar `-f vhd` UI) and translates to `vpc`
when building the qemu-img command, mirroring the existing
shared/format_code mapping.

### `detect-profiles.py` updates

The dedup flow groups versions whose stdout output for a given
case is byte-identical. For create:

- Hash per `(case-name, target-format)` triple across all
  versions.
- We expect *high* dedup: qemu-img's create defaults for qcow2
  shifted at well-known version boundaries (compat default at
  3.0, default cluster_size at 1.0 — both well before our
  version floor). For 6.0.0 → 10.2.0 we expect ≤ 10 distinct
  profiles for most cases.
- The script likely already handles arbitrary output_type
  strings — verify during 7b.

### Execution mechanics

- 36 cases × 80 versions × ~0.3s per case (create + info +
  unlink) ≈ 14 minutes serial.
- Sanity pass: run against latest (10.2.0) only; verify all
  36 expected baseline files appear with non-empty `stdout.txt`
  for non-error cases.
- Full pass: run with no `--version` filter; let it complete.
- Verify the existing generator already supports `--version
  <V>` for single-version runs (line 233-240 in
  `get_qemu_binaries`).

### Why not subset to a few qemu versions

Same reason as measure phase 6: the cross-version matrix is the
contract phase 8 enforces. Cutting it saves disk for one
release cycle and accumulates unverified versions thereafter.

### What `qemu-img info --output=json` reports per format

Quick reminder of the comparable fields (phase 8 will pick the
comparison set):

- All targets: `filename`, `format`, `virtual-size`,
  `actual-size`, `format-specific.type`.
- qcow2: `cluster-size`, `format-specific.data.compat`,
  `format-specific.data.lazy-refcounts`,
  `format-specific.data.refcount-bits`,
  `format-specific.data.extended-l2`,
  `format-specific.data.preallocation` (if set), `dirty-flag`
  (always `false` on a fresh create).
- vmdk: `cluster-size` (= grain size),
  `format-specific.data.cid`,
  `format-specific.data.parent-cid`,
  `format-specific.data.create-type`,
  `format-specific.data.extents[*]`.
- vhd: `cluster-size` (= block size for dynamic; ignored for
  fixed), `format-specific.data.subformat` (qemu reports this
  via a newer field; instar exposes it via the disk-type byte
  in the footer).
- vhdx: `cluster-size` (= block size),
  `format-specific.data.log-size`,
  `format-specific.data.has-parent` (always false here).

Fields that *will* differ between qemu-img and instar at
phase 8 comparison time and need a divergence whitelist:

- `filename` — absolute path differs between baseline-time
  (qemu tmp path) and test-time (instar tmp path). Strip /
  replace with `$FILENAME` before recording, similarly to
  measure's `TESTDATA_ROOT_PLACEHOLDER`.
- `actual-size` — depends on the host filesystem (block size,
  whether `preallocation=full` was actually zero-filled vs
  fallocated). Compare with a tolerance, or exclude.
- vmdk `cid` / `parent-cid` — random 32-bit values per
  invocation. Always differ. Exclude from comparison.
- vhdx `header-id` / data-region UUIDs — random. Exclude.

These exclusions are phase 8's problem; phase 7 just records
the raw qemu output. Document the expected drift list in 7e's
docs paragraph so phase 8 has a starting point.

## Public surface added in phase 7

In `instar-testdata/scripts/generate-baselines.py`:

```python
COMMANDS['create'] = {
    'output_types': {
        'create-info-json': 'json',
    },
    'targets': ['qcow2', 'vmdk', 'vhd', 'vhdx', 'raw'],
    'create_cases': None,  # patched in after CREATE_CASES is defined
}

CREATE_CASES = { ... }  # the dict above

COMMANDS['create']['create_cases'] = CREATE_CASES
```

A new `generate_create_baseline()` function (signature above)
and an `elif command_name == 'create':` branch in `main()`'s
dispatch loop that iterates targets × cases.

In `instar-testdata/scripts/detect-profiles.py`: add
`create-info-json` to whatever drives the per-output-type
iteration (verify the shape during 7b — measure's wiring
already established the precedent).

In the `instar` repo:

- `CHANGELOG.md` — a "create cross-version baselines committed
  to instar-testdata" paragraph.
- `ARCHITECTURE.md` — add `create-info-json` to the test-image-
  generation / baseline-matrix paragraph if such a paragraph
  exists.

## Open questions

These should be answered during execution; escalate to the
operator rather than guessing.

1. **`-q` quiet on `qemu-img create`**. qemu-img's `-q` flag
   suppresses the "Formatting ..." stderr line on some
   versions but not all. Recommendation: do *not* pass `-q`,
   capture stderr verbatim; phase 8's comparison only looks at
   the `stdout.txt` JSON, not the stderr file.

2. **What about `-o backing_file=...` cases?** Out of scope —
   needs a parent fixture, complicates the per-case cleanup,
   and phase 8 already plans backing tests as round-trips
   rather than baseline comparisons. Defer to a follow-up if
   needed.

3. **Should we also baseline `qemu-img create` failure
   modes** (e.g. SIZE=0, conflicting options)? Negative-path
   coverage is valuable but better placed in phase 8 as
   instar-specific error-text assertions. Phase 7 sticks to
   happy-path option combinations.

4. **`compression_type=zstd`** (qcow2). instar accept-ignores
   it. qemu-img added support in 5.1. We could include
   `('1G-zstd', '1G', ['compression_type=zstd'])` to verify
   the info JSON's `compression-type` field round-trips.
   Recommendation: include it; it's one extra case.

5. **vmdk `grain_size` via `-o`**. qemu-img does *not* accept
   `grain_size` as an `-o` key — grain is fixed by subformat.
   instar exposes `--grain-size` independently. Phase 7's
   vmdk case list cannot baseline grain via qemu-img; phase 8
   covers grain via round-trip checks. The cases marked
   `1G-grain-4k` in the draft above are removed — keeping a
   case named "grain" with no `-o grain_size=` would be
   misleading. Strike that line; final vmdk list is 5 cases.

6. **VHDX subformat=fixed**. qemu-img doesn't emit it; instar
   doesn't either (phase 1d notes vhdx is dynamic-only). Skip
   from phase 7's case list (already absent).

7. **Tmp directory location**. The script runs in the
   testdata repo dir. Use `tempfile.mkdtemp(prefix='create-
   baselines-')` under the system tmp; clean up at the end of
   each version's loop iteration. Do *not* use the testdata
   repo's directory — accidental commits of the tmp fixtures
   would balloon the repo.

8. **Per-case timeout**. Default 60s. qemu-img create at 1G
   non-preallocated completes in < 1s on a normal host;
   `preallocation=full` at 1M is < 1s. 60s is a generous
   ceiling for the slow disk / VM case.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | In the `instar-testdata` repo at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-testdata`, edit `scripts/generate-baselines.py`. Add the `'create'` entry to `COMMANDS` mirroring the measure precedent: single `output_types` entry (`create-info-json`: 'json'), `'targets'` list of the five target formats we baseline, `'create_cases': None` to be patched in after `CREATE_CASES` is defined. Define `CREATE_CASES` as a module-level dict near `SIZE_CASES`, populated with the case lists above (qcow2 = 18 cases, vmdk = 5, vhd = 5, vhdx = 5, raw = 2 — 35 total per version). Add a new `generate_create_baseline()` function next to `generate_measure_size_baseline()`, with the pipeline described under "Generator function signature" above (qemu-img create into a tmp path, then qemu-img info --output=json, capture both exit codes, write stdout/stderr/meta.json, always clean up the tmp file). Strip the absolute tmp path from the info JSON (replace with a `$FILENAME` placeholder, mirror the `TESTDATA_ROOT_PLACEHOLDER` pattern). Add an `elif command_name == 'create':` branch to `main()`'s dispatch loop, mirroring the measure branch but iterating `for target, cases in create_cases.items(): for case in cases:`. Translate target `'vhd'` to the qemu-img canonical `'vpc'` at command-build time only — the directory name and case dict key stay `vhd` for UI symmetry with instar. Run a sanity check: `python scripts/generate-baselines.py --command create --version 10.2.0`. Confirm `expected-outputs/create-info-json/qcow2/10.2.0/1M-default.stdout.txt` exists and contains a valid JSON object with `"format": "qcow2"`, `"virtual-size": 1048576`. Spot-check one vhd-fixed (`vhd/10.2.0/1M-fixed.stdout.txt`, `"format": "vpc"`, `"virtual-size": 1048576`) and one vhdx (`vhdx/10.2.0/1M-default.stdout.txt`). Do NOT generate the full matrix yet — that is step 7c. The instar repo is not touched in this step. |
| 7b | medium | sonnet | none | In the `instar-testdata` repo, edit `scripts/detect-profiles.py` to handle the `create-info-json` output type. The existing script's iteration should already pick up arbitrary `expected-outputs/<output_type>/` directories — verify by reading the script's directory-discovery logic. If it has a hard-coded output-type whitelist, extend it; if it walks the directory tree, no edit needed beyond confirming behaviour. Run `python scripts/detect-profiles.py --output-type create-info-json` after the 7a sanity pass and confirm a `expected-outputs/create-info-json/profiles/` subdir + `version-map.json` are generated for the 10.2.0-only matrix (which will have exactly one profile per case since only one version is recorded). instar repo not touched. |
| 7c | medium | sonnet | none | **This step is execution, not coding.** Run the generator against the full qemu-img-binaries matrix to produce all baselines. Command: `python scripts/generate-baselines.py --command create` (no `--version` filter). Expected wall clock ~15 min on a normal host (~80 versions × 35 cases × ~0.3 s each). Watch for warnings or skips. Old qemu versions will fail some option combinations (`extended_l2=on` before 5.0, `compression_type=zstd` before 5.1, `subformat=streamOptimized` in some early 6.x point releases) — these record as non-zero exit baselines and are expected, not errors. After completion, run `python scripts/detect-profiles.py --output-type create-info-json` to regenerate the dedup profiles. Spot-check a handful: (a) `create-info-json/qcow2/10.2.0/1G-cs-64k.stdout.txt` should report `"cluster-size": 65536`; (b) `create-info-json/vhd/10.2.0/1M-fixed.stdout.txt` should report `"virtual-size": 1048576`, `"format-specific"` carrying the fixed subformat indicator; (c) `create-info-json/qcow2/6.0.0/1G-extended-l2.stderr.txt` should mention "Unknown option" and the matching `.meta.json` shows a non-zero `create_return_code`. **If the runtime exceeds 45 minutes or anything looks wrong, stop and surface the issue rather than continuing.** Operator may run this step themselves rather than delegating — it's long-running and best in a tmux/nohup session. instar repo not touched. |
| 7d | low | sonnet | none | In the `instar-testdata` repo, `git add expected-outputs/create-info-json scripts/generate-baselines.py scripts/detect-profiles.py`. Inspect `git status --short` and verify the diff is roughly "two scripts modified, ~8 600 new files under expected-outputs/create-info-json/". Total disk delta should be ≤ 20 MiB. Commit with a clear message. Push to the remote only if the operator approves (do not push unprompted). The instar repo is still not touched. |
| 7e | low | sonnet | none | Back in the instar repo (`/srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-wt-resize`), update `CHANGELOG.md` Unreleased / Added with a paragraph: "Cross-version `qemu-img create` baselines committed to `instar-testdata/expected-outputs/create-info-json/`. Generated against every qemu-img binary in `qemu-img-binaries/x86_64/` (6.0.0 through 10.2.0). Consumed by phase 8's integration tests. ([phase 7](https://github.com/shakenfist/instar/blob/develop/docs/plans/PLAN-create-phase-07-baselines.md))". Add to the create paragraph in `ARCHITECTURE.md` near the existing measure / info baseline mention, noting `create-info-json` joins the matrix. Also mark phase 7 of PLAN-create.md as Complete in the execution table. Run `pre-commit run --all-files`. Only `CHANGELOG.md`, `ARCHITECTURE.md`, and `docs/plans/PLAN-create.md` modified in the instar repo. |

Total: 5 commits (4 in `instar-testdata`, 1 in `instar`).

## Out of scope for phase 7

- Test code that consumes the baselines (phase 8).
- Backing-file baselines (defer to phase 8 round-trips; needs
  parent fixtures, not a baseline-generator concern).
- Negative-path baselines (size=0, invalid options) — better as
  instar-specific assertions in phase 8.
- `monolithicFlat` or `twoGbMaxExtent*` vmdk subformats —
  instar doesn't emit these.
- Encrypted-create (`encrypt.*`) — instar rejects with a clear
  "not yet supported" error.
- Updating existing info / check / compare / measure baselines.
- Pinning `instar-testdata` to a specific commit from the
  instar side (not done for any other operation).

## Success criteria

- `instar-testdata/scripts/generate-baselines.py` recognises
  the `create` command and produces baselines under
  `expected-outputs/create-info-json/<target>/<version>/`.
- `instar-testdata/scripts/detect-profiles.py` produces
  `create-info-json/profiles/` with dedup buckets plus a
  populated `version-map.json`.
- The full matrix is generated and committed to
  `instar-testdata`: ≥ 35 cases × ≥ 80 versions × 3 files per
  case = ≥ 8 400 baseline files (≈ 15 MiB on disk).
- Spot-check pass: at least 3 baseline files (a qcow2 default,
  a qcow2 with options, a vhd fixed) carry the expected
  `virtual-size` / `cluster-size` / `format-specific` fields
  per the `qemu-img info` schema for the recorded version.
- The `instar` repo's `CHANGELOG.md` + `ARCHITECTURE.md` note
  the new baselines.
- No instar code changes in this phase beyond docs.

## Risks and mitigations

- **Old qemu versions reject a recent option** → silent
  baseline corruption (a recorded non-zero exit that phase 8
  can't compare against an instar success). Mitigation:
  `meta.json` carries the create-step exit code; phase 8's
  test logic skips comparison when the baseline's
  `create_return_code` is non-zero.
- **Disk volume**: ~15 MiB worst case. Existing
  `expected-outputs/` is larger. Acceptable.
- **Wall-clock**: ~15 min for the full matrix. Long enough to
  risk operator distraction. Mitigation: the script already
  logs per-version progress; run in tmux/nohup.
- **Tmp-dir cleanup**: a crash mid-loop leaves fixture files.
  Mitigation: per-version `tempfile.mkdtemp` + a `try/finally`
  removing the dir. Cleanup is best-effort; orphaned files
  are harmless (in `/tmp`).
- **`-o` key drift across qemu versions** — some keys renamed
  or removed between versions (e.g. `preallocation_mode` →
  `preallocation` in qemu 2.0). Phase 7's case list uses the
  modern names; old-version failures get recorded as non-zero
  exit baselines and skipped at phase 8 comparison time.
- **`CREATE_CASES` drift over time** — as instar adds new
  qcow2 features (e.g. data files, encryption) the matrix
  grows. Acceptable; it's config-as-code in one place.
- **VHD canonical name `vpc`** — easy to get this wrong in the
  command-build path. Mitigation: 7a's brief explicitly calls
  out the `vhd` → `vpc` translation; a test in 7a's sanity
  pass exercises it (the `vhd/10.2.0/` directory only
  populates if the translation works).

## Bugs fixed during this work

(To be filled in.)

## Back brief

Before executing any step, the executing agent should back-
brief: which repo is being edited (`instar-testdata` vs
`instar`), which scripts are being modified, and which paths
are being written. The reviewer should verify nothing in the
instar repo changes except `CHANGELOG.md`, `ARCHITECTURE.md`,
and the PLAN-create.md execution row in step 7e.
