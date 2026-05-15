# Phase 7: integration tests against the cross-version baselines

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-06-baselines.md](/components/instar/plans/PLAN-measure-phase-06-baselines/)

## Status: Not started

## Mission

Wire `tests/test_measure.py` (8 smoke tests from phase 4, 13
`-o` tests from phase 5) up to the cross-version baselines
that phase 6 committed in `instar-testdata`. After phase 7,
every safe-tier image in the manifest plus every curated
`--size` case is checked against its corresponding
`qemu-img measure` baseline, and the vmdk / vpc / vhdx target
formats (which qemu-img doesn't measure) get round-trip
coverage that asserts `convert` output size lies in the
`[required, fully_allocated]` range.

Phase 7 closes the loop: phase 4 says "the CLI works for the
cases I tested"; phase 7 says "the CLI matches qemu-img on
every case we record a baseline for, on every supported qemu
version".

## Why this is its own phase

Phase 6 stored bytes on disk. Phase 7 turns those bytes into
assertions. They're separable because:

1. Phase 6 work is in the `instar-testdata` repo and the
   long-running tail is regenerating baselines whenever the
   matrix expands. Phase 7 work is in the `instar` repo and
   does not touch the testdata side.
2. Phase 6 needed scripting that runs once per
   matrix-refresh; phase 7 needs Python test infrastructure
   that runs on every CI invocation.
3. The fan-out is large (~200 test cases). Bundling phase 6's
   data generation with the test code would mean one giant
   commit that's hard to review.

## Architecture

### Baseline lookup

The existing `tests/base.py` already wraps the
`expected-outputs/<output-type>/profiles/<profile>/<image-id>.stdout.txt`
layout via `get_output_profiles()` and `get_expected_output()`.
Phase 7 reuses both, with one small extension:

```python
# tests/base.py
COMMAND_OUTPUT_DIRS = {
    'info': 'qemu-img',
    'check': 'check',
    'compare': 'compare',
    'measure': 'measure',        # NEW — phase 7
}
```

The measure-specific peculiarity is the `_size/` pseudo-bucket
in the raw layout. But for the *profile* layout
(`expected-outputs/measure-json/profiles/profile-NN/`), files
are named by `image_id` exactly like every other command —
`detect-profiles.py` already produced this naming during
phase 6. So `get_expected_output('1G-qcow2-default',
'profile-10-0-0', output_type='json', command='measure')`
returns the right file with no additional helper needed.

For the source-image cases, the `image_id` in the profile
bucket has the `__<target>` suffix already baked in
(verified in phase 6): e.g. `cirros-qcow2__qcow2.stdout.txt`.
Tests pass this composed id to `get_expected_output()`.

### Three test surfaces

#### Surface 1: `--size` mode baseline comparison

For each of the 21 SIZE_CASES (the same list phase 6's
generator iterates), and each output type (human / json):

1. Translate the case's `size_str` / `target_format` /
   `options_list` into instar's CLI flags.
2. Run `instar measure --size <SIZE> -O <TARGET> <flags>
   --output=<format>`.
3. Look up the baseline via `get_output_profiles('json',
   'measure')['profiles']` to get the profile for the
   installed qemu version (or a representative one), then
   `get_expected_output('1G-qcow2-cs-64k', profile,
   output_type='json', command='measure')`.
4. Assert `instar_output == baseline` exactly.

Skip cases where the baseline has non-zero exit (qemu-img
rejected the option on that profile's representative
version). Read the meta.json in the raw bucket
(`expected-outputs/measure-json/_size/<version>/<case-name>.meta.json`)
to determine exit code; skip if non-zero.

Since dedup put all 80 qemu versions into a single profile,
in practice every test uses `profile-6-0-0` (or whatever the
single profile is named). The version-map.json is the source
of truth — read it rather than hard-coding.

#### Surface 2: source-image baseline comparison

For each safe-tier image in `tests/manifest.json`, and each
target ∈ {raw, qcow2}, and each output type ∈ {human, json}:

1. Compute the expected `image_id` as `<manifest-id>__<target>`.
2. Skip if no baseline exists (handles caution/malicious
   images that were filtered out of phase 6's generation).
3. Skip if the baseline's meta.json shows non-zero exit
   (e.g. the qcow2-overlay-chain case whose backing-file path
   is stale).
4. Run `instar measure <image-path> -O <target>
   --output=<format>`.
5. Compare to baseline byte-for-byte. Note: baselines were
   recorded with `$TESTDATA_ROOT` placeholder for portability;
   the existing `substitute_testdata_root()` helper takes care
   of resolving paths in the comparison.

#### Surface 3: vmdk / vpc / vhdx round-trip

These target formats can't be cross-validated against qemu-img
(qemu-img errors with "does not support size measurement").
Instead:

1. Create a small empty raw tmpfile via `qemu-img create -f
   raw <tmpfile> <SIZE>` (sizes: 1 MiB, 16 MiB, 64 MiB).
2. Run `instar measure --size <SIZE> -O <fmt>
   --output=json` → parse `required` + `fully_allocated`.
3. Run `instar convert -f raw <tmpfile> -O <fmt>
   <out_tmpfile>`.
4. Assert `required <= os.path.getsize(out_tmpfile) <=
   fully_allocated`.

For *non-empty* sources (a half-allocated raw input):

1. Use one of the existing safe-tier qcow2 test images as
   source (e.g. cirros-qcow2).
2. Run `instar measure <image> -O <fmt> --output=json`.
3. Run `instar convert <image> -O <fmt> <out_tmpfile>`.
4. Same bound assertion.

Round-trip tests are slower (each one runs convert end-to-end),
so cap at ~15 cases total (3 sizes × 3 vmdk-style targets × 1
source mode + ~6 source-image cases).

### `SIZE_CASES` duplication question

The 21 SIZE_CASES list lives in
`instar-testdata/scripts/generate-baselines.py`. Two options
for phase 7's tests:

A. **Mirror the list inline in `tests/test_measure.py`** as a
   Python const. Pros: tests are self-contained, no
   cross-repo path resolution. Cons: drift risk if testdata
   adds a new case without updating instar's mirror.
B. **Walk the directory** `expected-outputs/measure-json/_size/<version>/`
   to discover cases at runtime, then derive args from
   filenames. Pros: never drifts. Cons: filename-to-args
   reverse-engineering is brittle (a typo in a case name
   silently desyncs from the expected args).

**Recommendation: A (mirror).** Drift is a controllable
problem; brittleness is not. Add a one-line cross-check test
that asserts every `*.stdout.txt` in the raw bucket has a
mirroring SIZE_CASES entry, so adding a case to phase 6
without updating instar causes a clear test failure.

### Test-class organisation

```
tests/test_measure.py
├── TestMeasureSmoke          (phase 4 — 8 tests, unchanged)
├── TestMeasureOptions        (phase 5 — 13 tests, unchanged)
├── TestMeasureBaselineSize   (phase 7 — ~42 tests)
├── TestMeasureBaselineSource (phase 7 — ~156 tests)
└── TestMeasureRoundTrip      (phase 7 — ~15 tests)
```

Total ≈ 230 tests. The two new baseline-comparison classes
together fan out to ~200 of those; each test is short
(< 1 s) so total runtime is dominated by binary launch
(~0.5 s × 200 = ~100 s). Acceptable.

If the runtime balloons under stestr (forking + venv import
overhead), enable parallel execution via stestr's
`--concurrency` flag. The existing test suite already runs
in parallel; phase 7 inherits that.

### Round-trip math

For vmdk monolithicSparse / vhd dynamic / vhdx, sparse
output skips holes. Empty source → near-minimum output
(header + tables only). The phase 1 calculator computes
`required` for that case. So:

- Empty source: `actual ≈ required`. Tolerance: `actual <=
  required + grain_size` (one grain of margin for writer
  alignment artefacts).
- Half-allocated source: `required < actual < fully_allocated`.
- Fully-allocated source: `actual ≈ fully_allocated`.

The plan picks the **simplest invariant** that always holds:

```
required - cluster_size <= actual <= fully_allocated + cluster_size
```

Where `cluster_size` is a small alignment cushion (one
output sector or one block, whichever is larger; reuse
`vhdx::MB_ALIGN = 1MB` for vhdx). This lets the test pass
even when convert pads to output-sector boundaries that
measure didn't account for (the divergence noted in phase 1e
for VHD's leading-footer alignment).

If a round-trip test fails the bound, that's a real bug —
either measure is over/underestimating, or convert is
producing a wrong-size file. Both are blocking.

### Round-trip and `instar convert` semantics

instar's `convert` already produces vmdk / vpc / vhdx output
(phase 3 of the convert plan). For phase 7's round-trip
tests, use the existing CLI surface unchanged:

```
instar convert -f raw <input> -O <target> <output>
```

For target = vmdk, default is `monolithicSparse`. For vpc,
default is `dynamic`. For vhdx, default is `dynamic`. These
defaults match what `measure` computes when given no
`--subformat`. If the test wants to exercise an alternative
subformat, both convert and measure need the matching flag.

## Open questions

1. **The single-profile dedup means surfaces 1 and 2 don't
   exercise multiple qemu versions** — every test compares
   against the same baseline regardless of installed qemu
   version. Recommendation: that's fine. The matrix exists
   so a *future* qemu-img-side change that splits the profile
   gets caught immediately; in the current state, the test
   coverage is "instar measure matches qemu-img 6.0.0
   through 10.2.0" because they're all in one profile.

2. **What about `--output=human` matching?** qemu-img's
   human output is the same shape across versions. Should
   surfaces 1 and 2 run both human and json comparisons?
   Recommendation: yes — both are baselined in phase 6, both
   are matched. Doubles the test count but keeps coverage
   symmetric.

3. **Round-trip tolerance**: the phase plan picks a
   one-cluster-size cushion. Could tighten (zero tolerance,
   exact equality) if convert and measure agree exactly. Try
   exact first; widen if a test fails on an alignment
   off-by-one.

4. **What if the installed qemu-img version isn't in any
   profile bucket?** This shouldn't happen post-phase-6 (the
   matrix covers 6.0.0–10.2.0), but a developer might have
   qemu-img 5.x or something newer installed. Fallback: use
   the only profile that exists (since measure has only one
   profile, this is trivial). For future-proofing if the
   profile space ever grows, use `version_to_profile.get(<v>,
   list(profiles)[0])` with a logged warning.

5. **VMDK monolithicFlat source rejection**: phase 4
   rejected this with a clear error. The test surface
   should confirm the error is still raised — but phase 4
   already covered that. Don't duplicate.

6. **Convert's sector-size alignment** can cause convert's
   actual output to exceed `fully_allocated` slightly (the
   leading-footer / sector-alignment gap from phase 1e).
   The cluster_size cushion in the round-trip math accounts
   for this, but the precise size depends on the output
   sector size (default 65536). Use that as the cushion
   directly for VHD specifically.

7. **Should round-trip tests run by default in `make
   test-integration`?** They each run convert end-to-end and
   the file I/O isn't free. Recommendation: yes — they're
   fast enough (~1 s each × 15 = ~15 s total) and they're
   the only way to catch vmdk/vpc/vhdx measure regressions.
   No opt-in flag.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 7a | medium | sonnet | none | Extend `tests/base.py`: add `'measure': 'measure'` to `COMMAND_OUTPUT_DIRS`. No new helper functions needed yet — `get_output_profiles()` and `get_expected_output()` already work for measure once the dict is updated; verify by writing a one-line smoke check (e.g. an `assertNotEqual(get_output_profiles('json', 'measure')['profiles'], {})` test inside an existing class). Touch only `tests/base.py` and `tests/test_measure.py` (the smoke check goes in the existing `TestMeasureSmoke` class). Run `make test-integration` to confirm the new smoke test passes. |
| 7b | medium | sonnet | none | Add `TestMeasureBaselineSize(TestMeasureSmoke)` to `tests/test_measure.py`. Define a `MEASURE_SIZE_CASES = [...]` list at module scope mirroring the 21 entries from `instar-testdata/scripts/generate-baselines.py:SIZE_CASES`. Each entry is `(case_name, size_str, target, options_list)`. Implement `_args_for_case(case)` that translates an entry to a list of `instar measure` CLI args (size → `--size`, target → `-O`, options_list → `-o opt1,opt2`). Generate one test per case × output type (use a loop that calls `setattr(cls, f'test_{case_name}_{output_type}', ...)` to register the methods, or define a parametrised helper). Each test runs `instar measure`, fetches the matching baseline via `get_expected_output(case_name, profile, output_type='json'|'human', command='measure')`, and asserts byte equality. Skip cases whose baseline meta.json (`expected-outputs/measure-<type>/_size/<version>/<case-name>.meta.json` for the profile's representative version) has non-zero return_code. Add a cross-check `test_size_cases_match_baselines()` that walks `expected-outputs/measure-json/_size/<version>/` and asserts every `*.stdout.txt` corresponds to a MEASURE_SIZE_CASES entry, catching drift. Run `make test-integration` and confirm ~42 new tests pass. |
| 7c | high | sonnet | none | Add `TestMeasureBaselineSource(TestMeasureSmoke)` to `tests/test_measure.py`. Iterate `self.get_all_safe_images()` (or whatever the existing helper is — look in `tests/base.py` for the iteration pattern; if it's not exposed, walk `self._images` or load the manifest directly). For each image × target ∈ {raw, qcow2} × output_type ∈ {human, json}, generate a test. Each test computes `image_id = f'{image.id}__{target}'`, skips if no baseline exists or if baseline meta.json shows non-zero return_code, runs `instar measure <path> -O <target> --output=<format>`, fetches the baseline via `get_expected_output(image_id, profile, output_type, command='measure')`, and asserts byte equality (after `substitute_testdata_root` if the baseline uses the placeholder). Note that the baseline filenames include the `__<target>` suffix because of phase 6's naming scheme. Expect ~156 tests; many will skip if their meta.json shows the qcow2-overlay-chain stale-backing-file failure. Run `make test-integration` and report pass/skip/fail counts. **High effort because:** iterating the manifest cleanly, composing the right image_id, and handling skip cases all interact. The sub-agent must read the manifest-loading code in `tests/base.py` carefully to find the right iteration pattern. |
| 7d | medium | sonnet | none | Add `TestMeasureRoundTrip(TestMeasureSmoke)` to `tests/test_measure.py` covering vmdk / vpc / vhdx target formats (which qemu-img can't measure). Two flavours: (a) `--size` mode — create an empty raw tmpfile via `qemu-img create -f raw <tmpfile> <SIZE>`, run `instar measure --size <SIZE> -O <fmt> --output=json`, run `instar convert -f raw <tmpfile> -O <fmt> <out>`, assert `required <= os.path.getsize(out) <= fully_allocated` with a one-output-sector cushion (65536 bytes); (b) source-image mode — use an existing safe-tier qcow2 (cirros-qcow2 is the standard pick), run measure + convert, same bound assertion. Cap at ~15 tests total (3 sizes × 3 targets for --size mode + 2 source images × 3 targets for source mode = 15). Use `tempfile.NamedTemporaryFile` for the input/output paths, clean up in `addCleanup()`. Run `make test-integration` and confirm all pass. |
| 7e | low | sonnet | none | Update `ARCHITECTURE.md`: in the existing "operations/measure/" bullet (last touched in 5d), append a sentence about the test coverage — "Integration tests in `tests/test_measure.py` cross-validate `instar measure` against the `qemu-img measure` baselines in `instar-testdata/expected-outputs/measure-*` for every safe-tier image and every curated `--size` case, plus round-trip the vmdk / vpc / vhdx outputs through `instar convert` to verify the predicted size bounds." Add to `CHANGELOG.md` Unreleased / Added: "Comprehensive integration tests for `instar measure`: cross-version baseline comparison for raw and qcow2 targets across every safe-tier test image, plus round-trip size-bound checks for vmdk / vpc / vhdx targets where qemu-img cannot validate. (PLAN-measure-phase-07-integration-tests.md)". Run `pre-commit run --all-files`. |

Total: 5 commits.

## Out of scope for phase 7

- Updating `instar-testdata` (phase 6 already covered that).
- Caution-tier / malicious-tier image coverage (phase 6
  scope decision; revisit as a follow-up).
- LUKS-encrypted source baselines (master-plan future work).
- Backing-chain composition tests (chain support isn't in
  measure yet).
- Performance benchmarking (separate effort).
- Coverage-guided fuzz updates (phase 8).
- Differential fuzz updates (phase 9).
- `docs/measure.md` user guide (phase 10).

## Success criteria

- `tests/test_measure.py` has ~230 total tests (8 smoke + 13
  options + ~42 size baseline + ~156 source baseline + ~15
  round-trip).
- `make test-integration` runs them all; ~210 pass, the rest
  skip-with-message for known-non-zero baselines (the
  qcow2-overlay-chain stale-backing-file family).
- `make instar` builds; `make lint` clean;
  `pre-commit run --all-files` clean.
- One end-to-end byte-equality check confirms parity:
  `instar measure --size 1M -O qcow2 --output=json` matches
  the baseline in `instar-testdata/expected-outputs/measure-json/profiles/profile-NN/1M-qcow2-default.stdout.txt`.
- ARCHITECTURE.md and CHANGELOG.md updated.

## Risks and mitigations

- **SIZE_CASES list drift between repos**. Mitigation: 7b's
  `test_size_cases_match_baselines()` cross-checker catches
  any case present on disk but not in the Python mirror, and
  any case in the mirror but not on disk.
- **Manifest entries without baselines (e.g. images added to
  the manifest after the phase 6 matrix was generated)**.
  Mitigation: skipTest with a clear message rather than fail.
  Surfaces the gap without blocking CI; user regenerates
  baselines when convenient.
- **Round-trip bound cushion too tight or too loose**.
  Mitigation: phase 1e flagged the VHD sector-alignment
  divergence; start with a one-output-sector cushion
  (65 536 bytes), tighten or loosen based on observed
  failures during step 7d.
- **Parallel test runner conflicts** (multiple tests writing
  to the same tmpfile). Mitigation: each round-trip test
  uses `tempfile.NamedTemporaryFile` so paths are unique;
  stestr's default forked execution handles isolation
  correctly.
- **`get_expected_output()` raises on missing files** rather
  than returning None. Mitigation: wrap with a
  baseline-exists check that returns False if the path
  doesn't exist, then `skipTest` from the caller.

## Back brief

Before executing any step, the executing agent should
back-brief: which test class is being added (or extended),
which baseline files it reads, and how it locates them
(via `get_output_profiles` / `get_expected_output`, or by
direct path construction). The reviewer should verify no
step bleeds into phase 8 (fuzzing), phase 9 (differential
fuzzing extension), or phase 10 (docs).
