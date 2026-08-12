# Phase 8: integration tests against the cross-version create baselines

Master plan: [PLAN-create.md](/components/instar/plans/PLAN-create/) ·
Previous phase: [PLAN-create-phase-07-baselines.md](/components/instar/plans/PLAN-create-phase-07-baselines/)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (`tests/base.py`'s
`get_output_profiles` / `get_expected_output` helpers,
`tests/test_measure.py`'s phase-7 baseline-comparison classes,
`tests/test_create.py`'s phase-3 through phase-6 smoke tests,
the create-info-json layout under
`instar-testdata/expected-outputs/create-info-json/`), and
ground answers in what the code actually does today. Where a
question touches on external concepts (`qemu-img info --output=
json` schema per format, qcow2 / vmdk / vhd / vhdx info-JSON
field semantics, the divergence whitelist motivated by random
UUIDs / CIDs / header IDs), research as needed. Flag uncertainty
explicitly rather than guessing.

## Status: Not started

## Mission

Wire `tests/test_create.py` up to the cross-version baselines
that phase 7 committed in `instar-testdata`. After phase 8,
every case in the create matrix (36 cases × 5 target formats:
19 qcow2 + 5 vmdk + 5 vhd + 5 vhdx + 2 raw) is validated
against its version-matched `qemu-img info` baseline, and a
parallel cross-validation surface confirms that for any given
`(target, options, size)` triple, the bytes `instar create`
produces are info-equivalent to the bytes `qemu-img create`
produces (modulo a documented divergence whitelist for random
or path-dependent fields).

Phase 8 closes the loop the master plan opened: every prior
phase says "the writer works for the cases I tested";
phase 8 says "the writer matches qemu-img field-for-field
on every case in the matrix, against the same qemu-img
version installed on the test host".

## Why this is its own phase

Decomposes cleanly into:

1. Phase 7 stored bytes on disk. Phase 8 turns them into
   assertions. Separating the two keeps the cross-repo work
   (`instar-testdata` script edits + multi-minute baseline
   regeneration) out of the test-iteration loop in
   `instar`.
2. The fan-out is large (~36 cases × per-target option
   sweeps + cross-validation + round-trips ≈ 100 tests).
   Bundling phase 7's data generation with the test code
   would mean one giant commit that's hard to review.
3. Phase 8 surfaces the latent profile-dir-collision bug
   from phase 7 (see "What the survey turned up" below)
   without blocking on its fix — by reading the intact raw
   bucket directly.

## What the survey turned up

### `tests/test_create.py` shape today

686 lines, four classes:

- `TestCreateSmoke` (phase 3) — happy-path end-to-end per
  target format, plus a couple of error / option / JSON
  output tests.
- `TestCreateOOptions` (phase 4) — `-o key=value,...`
  parsing happy and error paths.
- `TestCreateBackingChain` (phase 5) — vhdx-as-backing,
  vmdk-from-vmdk CID round-trip, three-level chain non-
  recursion, format-mismatch auto-detect, size-too-large.
- `TestCreatePreallocation` (phase 6) — accept-set
  coverage for raw + falloc/full and qcow2 + metadata /
  falloc / full, plus the deferred-format rejections.

Helpers established: each class defines its own
`run_instar_create` and `run_instar_info` wrappers; the
phase-3 class has a `_assert_info_reports()` field-checker
that runs `instar info --output=json` and asserts a small
set of fields (`format`, `virtual-size`,
`cluster-size?`, `backing-filename?`). Phase 8 generalises
this into a shared field-extractor + comparator.

### `tests/base.py` and the COMMAND_OUTPUT_DIRS mechanism

`COMMAND_OUTPUT_DIRS` maps `command_name` → directory
prefix. Composition is `f'{prefix}-{output_type}'`:

```python
COMMAND_OUTPUT_DIRS = {
    'info':    'qemu-img',   # qemu-img-human / qemu-img-json
    'check':   'check',
    'compare': 'compare',
    'measure': 'measure',
}
```

For create the bucket is `create-info-json` (the
`-info-json` suffix is part of the bucket name — there is
no human variant for create). The natural extension is
`'create': 'create-info'` so `f'{create-info}-{json}' =
'create-info-json'`. `output_type='json'` is the only
valid option for create.

`get_output_profiles(output_type, command)` reads
`expected-outputs/<dir>/version-map.json` and returns
`{'profiles': {profile_name: representative_version},
'version_to_profile': {version: profile_name}}`.

`get_expected_output(image_id, profile, output_type,
command)` reads
`expected-outputs/<dir>/profiles/<profile>/<image_id>.stdout.txt`
with the `$TESTDATA_ROOT` placeholder substituted.

### Phase 7 baseline layout (recap)

```
instar-testdata/expected-outputs/create-info-json/
├── qcow2/<version>/<case>.{stdout.txt,stderr.txt,meta.json}
├── vmdk/<version>/<case>.{stdout.txt,stderr.txt,meta.json}
├── vhd/<version>/<case>.{stdout.txt,stderr.txt,meta.json}
├── vhdx/<version>/<case>.{stdout.txt,stderr.txt,meta.json}
├── raw/<version>/<case>.{stdout.txt,stderr.txt,meta.json}
├── profiles/profile-NN/<case>.{stdout.txt,stderr.txt,meta.json}
└── version-map.json
```

Where `<case>.stdout.txt` carries the `qemu-img info
--output=json` output (after the absolute tmp path is
replaced with the `$FILENAME` placeholder).

80 qemu versions × ~36 cases = ~8 640 baselines per file
type. Dedup ratio is 1:1 (every version is its own profile)
because vmdk's per-invocation random `cid` and `parent-cid`
break per-version dedup.

### Latent profile-dir-collision bug

`detect-profiles.py:225-241` copies all of a version's
`*.stdout.txt` files **flat** into `profiles/profile-NN/`,
on the assumption (line 231) that *"within a bucket,
filenames are unique because the case-name or image-id
encodes the format/target"*. For `measure`, case names like
`1G-qcow2-cs-64k` and image-ids like `cirros-qcow2__qcow2`
do encode the target. For `create`, case names like
`1M-default` exist independently under qcow2, vmdk, vhd,
vhdx, and raw — they collide silently. As-shipped,
`profiles/profile-10-2-0/1M-default.stdout.txt` carries
**only vmdk's content**; qcow2/vhd/vhdx/raw 1M-default
were overwritten without warning. The same applies to
`64M-default` (4 collisions) and `1G-default` (5
collisions).

Phase 8's tests **must not** read from the `profiles/`
bucket for create. The intact ground truth is the
per-target raw bucket
(`create-info-json/<target>/<version>/<case>.stdout.txt`),
which has no collisions because the target is encoded in
the directory path. Use that directly.

Recording this as a bug to be fixed as a phase-7 follow-up
(see "Bugs to fix"). Until the fix lands, the test code
indexes by target-version-case rather than by profile.

> **Resolved 2026-08-11.** instar-testdata `03c379a08a` and
> `769e463f7e` fixed the generator and regenerated the
> corpus: create-info-json profile files are now named
> `<target>-<case>`, so all 36 files per profile survive
> instead of 25. `TestCreateBaselineMatrix` reads the
> profile like every other output type and the raw-bucket
> workaround is gone. The defect was worse than recorded
> here — as well as the collisions, every profile carried
> 10.2.0's output whatever version it claimed, so 855 files
> disagreed with their own metadata.

### Three test surfaces

#### Surface 1: per-target baseline comparison

For each `(target, case)` pair in the canonical
`CREATE_CASES` mirror (35-36 entries depending on whether
the `1G-zstd` qcow2 case is included — phase 7 included
it, so 19 qcow2 cases):

1. Determine the installed qemu-img version via the
   existing `_detect_qemu_version()` helper.
2. Locate the version-matched baseline at
   `create-info-json/<target>/<version_str>/<case>.stdout.txt`.
   Skip if absent (e.g. test host runs qemu 5.2 — outside
   the phase-7 matrix floor of 6.0.0).
3. Skip if the baseline's `meta.json` reports
   `create_return_code != 0` (qemu-img rejected the
   option set on this version — there is no successful
   output to compare against).
4. Translate the case's `(size_str, target, options_list)`
   tuple to `instar create` CLI flags
   (mirroring measure's `_args_for_case`).
5. Run `instar create <flags> <tmpfile>` against a temp
   directory.
6. Run `qemu-img info --output=json <tmpfile>` (using the
   *system* `qemu-img`, not one of the matrix binaries —
   the comparison is against the matching baseline, which
   was recorded by the same version).
7. Normalise both sides:
   - Replace the absolute tmpfile path with `$FILENAME`
     (matching the baseline's pre-recorded placeholder).
   - Strip divergence-whitelist fields (see below) from
     both the produced and the baseline JSON.
8. Assert the normalised dicts are equal. On failure,
   `assertEqual` on the normalised JSON pretty-prints both
   sides so the diff is readable.

Fan-out: 36 cases × 1 (one test per case, using the
matching baseline). The version axis is the installed
host's qemu — we don't sweep across versions inside the
test loop (that would require launching the matrix
binaries, which is phase-7's job).

#### Surface 2: instar-create / qemu-create cross-validation

The master plan's primary contract: "`instar create |
instar info` and `qemu-img create | instar info` produce
identical info output (modulo divergence whitelist)".
Phase 8 implements this as a runtime comparison rather
than a baseline lookup:

For each `(target, case)` in a curated subset (~12
representative cases, not the full matrix — this surface
is slower because each test runs *two* create
invocations and *two* info invocations):

1. Translate the case to CLI flags.
2. Create the same image twice: once with `instar create`,
   once with the system `qemu-img create`.
3. Run `instar info --output=json` on both outputs.
4. Normalise both JSON outputs (strip divergence
   whitelist + absolute paths).
5. Assert the normalised dicts are equal.

This surface is independent of phase 7's baselines and
exercises the instar info parser as the comparison tool
— validates that *given matched inputs*, instar's
writer + parser combination agrees with qemu-img's
writer (via instar's parser). The baseline surface
above validates *given matched inputs*, instar's writer
agrees with qemu-img's writer (via qemu-img's parser).

These are complementary: surface 1 catches writer
divergences from qemu-img's contract; surface 2 catches
divergences between instar's writer and instar's own
parser (an internal self-consistency check that costs
nothing extra to set up).

#### Surface 3: round-trip via `instar check`

For each successful create case, run `instar check` on
the produced file and assert it reports clean. This is
the lightest-weight write-then-read sanity check
already validated for individual cases in
`tests/test_check_formats.py` — phase 8 extends it
across the matrix to catch any case-specific writer
bug that produces a file `qemu-img info` accepts but
`instar check` flags.

Fan-out: 36 cases × 1 check call. Each is fast (~0.3 s).

### Divergence whitelist

Fields excluded from cross-format JSON comparison
(phase 7's "What `qemu-img info --output=json` reports
per format" section already enumerates these; phase 8
codifies them as the whitelist):

Universal (every target):

- `filename` — absolute path differs between runs; replace
  with `$FILENAME` in both sides before comparison.
- `actual-size` — filesystem-dependent block accounting.
  Compare with a relative tolerance, or exclude entirely.
  Recommendation: **exclude entirely** for phase 8 (the
  test doesn't care whether the host runs ext4 vs xfs).
- `children[*].info.filename`, `children[*].info.actual-size`
  — same as above, for the nested file-level info.
- `format-specific.data.refcount-block-cache-size`,
  `format-specific.data.l2-cache-size`, and other cache-
  hint fields — qemu may report these on some versions
  and not others; not part of the metadata contract.

vmdk:

- `format-specific.data.cid` — random per
  `qemu-img create` invocation. Cannot match across runs.
- `format-specific.data.parent-cid` — random; in the
  no-backing case both sides emit a sentinel, but in the
  backing case (phase-5 vmdk-from-vmdk) each invocation
  reads a fresh parent CID. Exclude.
- `format-specific.data.create-type` — should match; do
  not exclude.
- `format-specific.data.extents[*].cluster-size` and
  related — should match; do not exclude.

vhdx:

- `format-specific.data.log-size` — qemu defaults to 1
  MiB; instar may use a different default. Compare only if
  both sides report the same value, otherwise exclude.
  (To be confirmed during 8b; if instar matches qemu's 1
  MiB default, this field stays in the comparison.)
- Any UUID-like field in the vhdx metadata (page-83 GUID,
  logical sector GUID, data write GUID, file write GUID,
  metadata GUID) — exclude. qemu-img info may not surface
  these at all in older versions; treat their presence as
  best-effort.

vhd / vpc:

- No known random fields. The fixed-subformat detection
  divergence noted in phase 7 (`format=raw` for
  `1M-fixed`) is an artefact of qemu-img-without-`-f`
  auto-detection; phase 8 invokes `qemu-img info` *with
  `-f vpc`* explicitly on the produced files to bypass
  the auto-detect path and get the canonical
  `format=vpc` result. (Phase 7's baselines were
  captured *without* `-f` to match qemu-img's default;
  phase 8 either: (a) invokes phase-8 info with no `-f`
  and accepts the same auto-detection result, or
  (b) strips the `format` field from the comparison
  when target is vhd-fixed. Decide during 8b; option (a)
  is simpler if it works.)

qcow2:

- No known random fields. `format-specific.data.compat`,
  `lazy-refcounts`, `refcount-bits`, `extended-l2`,
  `compression-type` all match deterministically when
  the create-time options match.

raw:

- The raw info JSON is mostly file-level — `format`,
  `virtual-size`, `filename`, `actual-size`. The
  comparison is nearly trivial once `filename` and
  `actual-size` are excluded.

The whitelist is implemented as a small helper in
`tests/helpers/info_json.py` (new file):

```python
DIVERGENCE_FIELDS = {
    'filename',
    'actual-size',
    # ... and a nested-path version for children[*].info.*
}

VMDK_DIVERGENCE = {
    'cid',
    'parent-cid',
}

VHDX_DIVERGENCE = {
    'log-size',  # tentative; remove if instar matches qemu's default
    # plus any UUID-like fields surfaced by the comparison
}

def normalise_info_json(obj: dict, target: str,
                        tmp_path: str | None = None) -> dict:
    """Recursively strip divergence fields and substitute
    $FILENAME for any absolute path matching tmp_path."""
```

The helper is pure-Python, no I/O. Unit-test it in
`tests/test_helpers.py` (if such a file exists; create
otherwise) with a couple of fixture dicts.

### `CREATE_CASES` mirror

Following measure's precedent (`MEASURE_SIZE_CASES`
mirrored from the generator), define a module-level
`CREATE_CASES` dict in `tests/test_create.py` mirroring
phase 7's `CREATE_CASES` from
`instar-testdata/scripts/generate-baselines.py`. Same
shape: `{target: [(case_name, size_str, options_list),
...]}`. Add a one-line cross-check
`test_create_cases_match_baselines()` that walks
`expected-outputs/create-info-json/<target>/<version>/`
for each target and asserts every `*.stdout.txt`
corresponds to a `CREATE_CASES` entry — catching drift
between the two mirrors.

Drift risk is real (the two repos evolve independently),
but the cross-check makes any divergence a loud test
failure rather than a silent miss. Same trade-off measure
made in its phase 7.

### Test-class organisation

```
tests/test_create.py
├── TestCreateSmoke              (phase 3)  — unchanged
├── TestCreateOOptions           (phase 4)  — unchanged
├── TestCreateBackingChain       (phase 5)  — unchanged
├── TestCreatePreallocation      (phase 6)  — unchanged
├── TestCreateBaselineMatrix     (phase 8)  — ~36 tests
├── TestCreateCrossValidation    (phase 8)  — ~12 tests
└── TestCreateRoundTripCheck     (phase 8)  — ~36 tests
```

Total ≈ 110 tests. Each `instar create` run takes
~0.5–1 s (guest launch cost dominates); the matrix and
round-trip surfaces together fan out to ~72 tests so
~60 s wall-clock serially, less under stestr's
parallel execution. The cross-validation surface runs
two creates + two info calls so ~3 s × 12 = ~36 s.
Total runtime in the 90–120 s ballpark — acceptable
given test_measure.py's surface already runs longer
in the same suite.

### Edge-case handling

**Falloc / full preallocation cases** (1M-prealloc-
falloc, 1M-prealloc-full): the baseline records the
`actual-size` for the version that generated it. Both
will report `actual-size = 1 MiB + metadata` — but the
exact value depends on the underlying filesystem.
Excluding `actual-size` from the whitelist solves this
without losing coverage (the *format-specific* fields
are what matter for create correctness).

**vmdk monolithicSparse vs streamOptimized**: baselines
for `1G-stream-optimized` carry `"create-type":
"streamOptimized"`; instar emits the same on its
output. Cross-check.

**vhd `1M-fixed` / `16M-fixed`**: phase 7's baselines
report `"format": "raw"` because qemu-img info auto-
detects on file start (fixed VHDs have only a 512-byte
footer at end of file, no leading magic). instar's
fixed VHD output has the same property. The phase-8
test for these cases invokes `qemu-img info` with no
`-f` flag (matching the baseline-recording invocation)
and the comparison naturally agrees.

**`1G-zstd` qcow2 case**: instar accept-ignores
`compression_type=zstd` and emits zstd metadata
(matching qemu-img's behaviour from 5.1+). The
baseline carries `"compression-type": "zstd"`; instar
should match.

**`1G-cs-512` qcow2 case**: 512-byte cluster size,
~2 MiB L2 table at 1 GiB virtual. Phase 1's emitter
handles this; phase 8 confirms via the baseline match.

**Backing-file cases**: not in phase 7's matrix (out of
scope per phase 7's plan). Phase 5's existing
`TestCreateBackingChain` already covers backing
behaviour with runtime fixture construction. Phase 8
adds *no* backing tests to the matrix surface; the
runtime fixtures stay in `TestCreateBackingChain`.

### Running cross-validation against the system qemu-img

Surface 2 calls the system `qemu-img create` and
compares its output (via `instar info`) against
instar's output (also via `instar info`). The
installed qemu-img may be any version 6.0.0+; the
comparison only requires that both writers agree on
the metadata at runtime, which they do if both
implement the same format spec.

If the system qemu-img is missing (`which qemu-img`
returns nothing) or runs an unsupported version,
surface-2 tests `skipTest` with a clear message.

### Test runtime budget

Worst case (serial, cold caches): 110 tests × ~1 s
avg = 110 s. Under stestr's default parallel
execution (`--concurrency=auto`, typically 4-8
workers on a developer laptop) drop to 20-40 s.
CI machines with fewer cores may take 60-90 s.
Acceptable. If a developer wants to skip phase-8
surfaces during fast inner-loop iteration, the
existing `INSTAR_TEST_TIER` filter (if it exists —
verify during 8a; if not, document a `-k` pytest
filter as the workaround) covers them.

## Open questions

These should be answered during execution; escalate to
the operator rather than guessing.

1. **Should `TestCreateBaselineMatrix` and
   `TestCreateCrossValidation` inherit from
   `TestCreateSmoke` (matching measure's pattern of
   inheriting from `TestMeasureSmoke`) or stand alone**?
   Inheritance gives free access to `run_instar_create`
   /  `run_instar_info` helpers. Recommendation: inherit
   from `TestCreateSmoke` for the same DRY reason.

2. **`-O` vs `-f`**. qemu-img uses `-f` for *create*'s
   target format (different from `convert`'s `-O`).
   Phase 7's generator uses `-f`. instar's CLI also uses
   `-f` for create. No conflict; just don't accidentally
   write `-O` in the test args.

3. **Profile lookup vs raw bucket lookup**. The phase-7
   profile-dir-collision bug (see "Latent profile-dir-
   collision bug" above) means `get_expected_output()`
   silently returns the wrong content for non-vmdk
   `1M-default` / `64M-default` / `1G-default` cases.
   Recommendation: phase 8 reads
   `expected-outputs/create-info-json/<target>/<version>/<case>.stdout.txt`
   directly via a new helper rather than going through
   `get_expected_output()`. This sidesteps the bug. File
   the bug fix as a follow-up in instar-testdata; do not
   block phase 8 on it.

4. **What if the installed qemu-img version is older
   than the matrix floor (6.0.0)**? Surface 1 tests
   `skipTest` per case with a message naming the missing
   baseline; surface 2 still runs (the system qemu-img
   is the comparator, not a baseline lookup). Surface 3
   (instar check round-trip) is also unaffected.

5. **Does qemu-img info on a fixed-VHD without `-f`
   return `format=raw` consistently across qemu
   versions**? Phase 7's baselines from 10.2.0 do.
   Verify during 8b that older qemu versions also do
   (relevant because surface 1 uses the *installed*
   qemu-img, which may be 6.x on some CI hosts). If
   any version reports differently, add `format` to
   the divergence whitelist when target is vhd and the
   case includes `subformat=fixed`.

6. **Should the test compare meta.json as well as
   stdout**? Phase 7's `meta.json` carries
   `create_return_code` and `info_return_code` per
   case, which the test already reads to decide skip.
   Beyond that, no — the meta.json is generator
   bookkeeping, not part of the comparison contract.

7. **Cross-validation curated subset selection**.
   ~12 representative cases for surface 2. Suggested
   set: 5 × `1M-default` (one per target), 4 × `1G-
   default` (excluding raw — already covered),
   `1G-cs-64k` (qcow2 option exercise),
   `1G-extended-l2` (qcow2 option), `1G-zstd` (qcow2
   option), `1G-stream-optimized` (vmdk), `1M-fixed`
   (vhd). 12-13 cases.

8. **Tolerance on the cross-validation comparison**.
   Surface 2 compares instar info on two files
   created by different writers (instar vs qemu-img).
   Divergence whitelist is the same as surface 1.
   If a field disagrees outside the whitelist, that's
   a real divergence — file as a bug, don't widen
   the whitelist to paper over it.

9. **Time budget for the qemu-img matrix binaries**.
   Phase 8 does NOT iterate `qemu-img-binaries/x86_64/
   <version>/` — that's phase 7's job. Phase 8 uses
   only the system qemu-img + the recorded baselines.
   This keeps the phase-8 runtime bounded.

10. **What if `instar info` doesn't surface a field
    that qemu-img info does** (e.g. `format-specific.
    data.lazy-refcounts`)? Surface 1's comparison is
    against qemu-img info on both sides, so this is
    a non-issue for surface 1. For surface 2, both
    sides are instar info; if instar info doesn't
    surface a field, it's missing from both sides
    and the comparison still passes. The case where
    instar info reports a field qemu-img doesn't is
    handled by the divergence whitelist (extend if
    needed during 8c).

## Public surface added in phase 8

In `tests/base.py`:

```python
COMMAND_OUTPUT_DIRS = {
    ...,
    'create': 'create-info',   # NEW — only output_type='json' valid
}
```

In `tests/helpers/info_json.py` (new file):

```python
def normalise_info_json(obj, target, tmp_path=None): ...
def assert_info_equivalent(actual, expected, target, msg=''): ...
```

In `tests/test_create.py`:

```python
CREATE_CASES = { ... }  # mirror of phase-7's generator

class TestCreateBaselineMatrix(TestCreateSmoke): ...
class TestCreateCrossValidation(TestCreateSmoke): ...
class TestCreateRoundTripCheck(TestCreateSmoke): ...
```

Two factory functions (`_make_baseline_test`,
`_make_round_trip_test`) registered via `setattr` on
the new classes, one per `(target, case)`.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 8a | medium | sonnet | none | Extend `tests/base.py`: add `'create': 'create-info'` to `COMMAND_OUTPUT_DIRS`. Create `tests/helpers/info_json.py` with `normalise_info_json(obj, target, tmp_path=None)` that recursively (a) substitutes `$FILENAME` for any absolute path equalling `tmp_path` in `filename` and `children[*].info.filename`, (b) deletes the divergence-whitelist keys per target as documented in the plan, (c) returns the cleaned dict. Add a small `assert_info_equivalent(actual_json_str, expected_json_str, target, msg='')` wrapper that parses both, normalises both, and `assertEqual`s. Add a one-line smoke test in the existing `TestCreateSmoke` class: `def test_create_baselines_present(self): profiles = self.get_output_profiles(output_type='json', command='create'); self.assertNotEqual(profiles['profiles'], {})`. If `tests/test_helpers.py` exists, add a couple of unit tests for `normalise_info_json` covering qcow2, vmdk (cid stripping), vhdx (log-size stripping). If not, add them as static methods on a new helper test class. Touch only `tests/base.py`, `tests/helpers/info_json.py`, and `tests/test_create.py`. Run `make test-integration` to confirm the smoke test passes. |
| 8b | high | sonnet | none | Add `CREATE_CASES` module-level dict to `tests/test_create.py` mirroring `instar-testdata/scripts/generate-baselines.py:CREATE_CASES` (35 cases: 19 qcow2 + 5 vmdk + 5 vhd + 5 vhdx + 2 raw). Add `TestCreateBaselineMatrix(TestCreateSmoke)` with: (i) a `_baseline_path(target, case_name)` helper that reads `expected-outputs/create-info-json/<target>/<installed_qemu_version>/<case>.stdout.txt` directly (NOT through `get_expected_output` — see plan's open question 3 re profile-dir collision bug); falls back to the most recent version dir if the installed version isn't in the matrix, with a logged warning; returns None if no baseline at all. (ii) a `_baseline_meta(target, case_name)` parallel helper for the meta.json. (iii) a `_args_for_case(target, case)` translator that emits `['-f', target, '-o', 'k=v,k=v', '<file>', size_str]`, handling the empty options_list case. (iv) a factory `_make_baseline_test(target, case)` that returns a test method which: creates a temp dir, runs `instar create`, asserts rc==0 (skipTest if baseline rc != 0), runs system `qemu-img info --output=json <tmpfile>` (NOT instar info — the comparison tool must match the baseline-generating tool), reads the matching baseline, calls `assert_info_equivalent(produced, baseline, target, msg=f'{target}/{case[0]}')`. (v) the loop that `setattr`s one test per `(target, case)` onto the class. (vi) a `test_create_cases_match_baselines()` cross-check that walks each `<target>/<latest-version>/` dir and asserts every `*.stdout.txt` corresponds to a `CREATE_CASES[target]` entry. **Skip rules**: skip if baseline file missing, skip if baseline `create_return_code != 0`, skip if installed qemu-img < 6.0 or unavailable. Run `make test-integration` and report pass/skip/fail counts per target. **High effort because**: 35 test cases, divergence-whitelist tuning may need iteration on real failures, and the vhd-fixed `format=raw` quirk needs careful handling (verify that the system qemu-img without `-f` matches the baseline; if not, special-case in the comparator). |
| 8c | medium | sonnet | none | Add `TestCreateCrossValidation(TestCreateSmoke)` with a curated subset (~12 cases — see plan's open question 7 for the suggested set). Factory `_make_xval_test(target, case)`: creates two temp files in two temp dirs, runs `instar create <flags> file_a` and the system `qemu-img create <flags> file_b` with translated args, runs `instar info --output=json` on both, normalises both via `normalise_info_json(..., target, tmp_path=...)`, asserts dict equality. Skip if system qemu-img unavailable or older than 6.0 or rejects the option set (capture qemu-img's exit code; skipTest with the stderr message). The whitelist used here may diverge slightly from 8b's (8b compared qemu-img info on both sides; 8c compares instar info on both sides). Tune by running the tests and treating any non-whitelist divergence as a bug to file rather than as a whitelist extension. Run `make test-integration` and confirm ~10 pass, ~2 skip-with-message. |
| 8d | low | sonnet | none | Add `TestCreateRoundTripCheck(TestCreateSmoke)` iterating the full `CREATE_CASES` matrix: per `(target, case)`, create + run `instar check` + assert rc==0. Skip if create itself failed (e.g. unsupported option set rejected by instar). One test per case = ~35 tests. Factory `_make_check_test(target, case)` registered via `setattr` mirroring the prior surfaces. Run `make test-integration` and confirm all pass. This surface catches any case-specific writer bug that produces a file qemu-img info reads but instar check rejects. |
| 8e | low | sonnet | none | Update `ARCHITECTURE.md`: in the existing `operations/create/` paragraph, append a sentence: "Integration tests in `tests/test_create.py` cross-validate `instar create` against the `qemu-img create` baselines in `instar-testdata/expected-outputs/create-info-json/<target>/` for every case in the create matrix, plus a direct instar-vs-qemu-img cross-validation surface (both info-read via `instar info`) and a `instar check` round-trip surface for writer self-consistency." Add to `CHANGELOG.md` Unreleased / Added: "Comprehensive integration tests for `instar create`: cross-version baseline comparison via `qemu-img info` against the create-info-json matrix, runtime cross-validation against the system `qemu-img create`, and `instar check` round-trip coverage for every case in the matrix. (PLAN-create-phase-08-integration-tests.md)". Mark phase 8 of PLAN-create.md as Complete in the execution table. Run `pre-commit run --all-files`. |

Total: 5 commits.

## Out of scope for phase 8

- Updating `instar-testdata` (phase 7 already covered the
  baseline regeneration; the profile-dir-collision bug fix
  is a phase-7 follow-up).
- Backing-file matrix coverage — phase 5's existing
  `TestCreateBackingChain` already handles backing
  semantics with runtime fixtures.
- Preallocation matrix coverage beyond what's in the
  baseline list — phase 6's `TestCreatePreallocation` covers
  the accept set.
- Encrypted-create coverage (master-plan future work).
- Multi-file vmdk subformats (`monolithicFlat`,
  `twoGbMaxExtent*`) — instar doesn't emit these.
- vhdx fixed subformat — neither qemu-img nor instar emits
  it.
- Coverage-guided fuzz coverage (phase 9).
- Differential fuzz extension (phase 10).
- `docs/create.md` user guide (phase 11).
- Performance benchmarking (separate effort).

## Success criteria

- `tests/test_create.py` has ~110 total tests
  (existing smoke + options + backing + preallocation
  + ~35 baseline + ~12 cross-validation + ~35 check).
- `make test-integration` runs them all; the new
  surfaces pass-or-skip-with-message — no
  unexpected failures.
- `make instar` builds; `make lint` clean;
  `pre-commit run --all-files` clean.
- One end-to-end demonstration: `instar create -f qcow2
  -o cluster_size=64k tmp.qcow2 1G` produces a file whose
  `qemu-img info --output=json` matches the baseline at
  `instar-testdata/expected-outputs/create-info-json/qcow2/
  <installed-version>/1G-cs-64k.stdout.txt` after the
  divergence whitelist is applied.
- ARCHITECTURE.md, CHANGELOG.md, and PLAN-create.md
  execution row updated.

## Risks and mitigations

- **Divergence whitelist drift**: the whitelist is a
  living artefact — new qemu-img versions may add fields
  instar doesn't emit (or vice-versa). Mitigation: on
  test failure, the diff output makes the offending field
  visible; whitelist updates are a one-line code change
  with a comment explaining the field's randomness or
  format-version origin.
- **Profile-dir-collision bug from phase 7**: surfaces
  silently as wrong-baseline-content. Mitigation: phase
  8 reads the per-target raw bucket directly, bypassing
  the broken profile dir entirely. File the bug as a
  phase-7 follow-up to be fixed when the
  `instar-testdata` matrix is next regenerated.
  (Fixed 2026-08-11; the mitigation has been removed.)
- **Installed qemu-img version not in phase 7's matrix**:
  test host runs qemu-img 5.x. Mitigation: surface 1
  skips with a clear message; surface 2 still runs (uses
  the installed qemu-img as live comparator); surface 3
  is qemu-agnostic.
- **Cross-validation surface flakiness from
  non-deterministic qemu-img output**: if qemu-img
  embeds a timestamp in some field the whitelist
  missed, surface 2 will fail. Mitigation: 8c's
  brief explicitly says treat unexpected divergences
  as bugs to file rather than as whitelist extensions
  — surface them so a real fix happens.
- **vhd-fixed `format=raw` auto-detection quirk**: if
  the installed qemu-img doesn't behave like 10.2.0
  (auto-detects fixed as raw), the comparison
  fails. Mitigation: 8b's brief calls this out; the
  fallback is to strip `format` from the comparator
  when target is vhd and case includes
  `subformat=fixed`.
- **Test runtime budget exceeded under serial
  execution**: ~110 tests × ~1 s each = ~110 s. If
  the CI runner is single-core and that's too long,
  drop the cross-validation surface to ~6 cases or
  guard it behind an environment variable.
  Mitigation: stestr's parallel execution already
  amortises this; revisit only if CI complains.
- **`instar info` parser bug on instar's own
  output**: surface 2 would catch this immediately —
  it's the test surface designed to catch
  writer/parser asymmetry within instar.

## Bugs to fix

- **`detect-profiles.py` flat-copy collision for create
  output type** (phase 7). The script's
  `copy_multi_bucket_version_to_profile()` flattens all
  per-bucket files into `profiles/profile-NN/`,
  assuming case names encode the target. For
  `create-info-json`, case names like `1M-default`
  collide across qcow2/vmdk/vhd/vhdx/raw — the last
  bucket processed wins. Fix: prefix the destination
  filename with the bucket name when copying, or have
  the create generator emit case names that include
  the target prefix from the start. The latter is
  preferable for symmetry with measure's convention.
  Either way, regenerate the profile dir afterward.
  This is a phase-7 follow-up; phase 8 does not block
  on it.
  **Done 2026-08-11** in instar-testdata `03c379a08a`
  (prefix on copy, per output type so collision-free
  types keep their filenames) and `769e463f7e`
  (regenerate). instar `tests/test_create.py` now reads
  the profile directly.

## Back brief

Before executing any step, the executing agent should
back-brief: which test class is being added (or
extended), which baselines it reads (and by what
path-construction logic, since phase 8 deliberately
bypasses `get_expected_output` for create due to the
collision bug), and how skip / fail are distinguished.
The reviewer should verify no step bleeds into phase 9
(fuzz), phase 10 (differential), or phase 11 (docs).
