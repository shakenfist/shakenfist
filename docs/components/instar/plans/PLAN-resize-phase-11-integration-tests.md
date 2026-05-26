# PLAN-resize phase 11: integration tests

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read the
existing test surface (`tests/base.py`, `tests/test_create.py`,
`tests/helpers/info_json.py`, `tests/test_convert.py`,
`tests/manifest.json`), understand how
`InstarTestBase` exposes binaries / qemu-version detection /
testdata roots, how `assert_info_equivalent` normalises JSON
across writers, and how `test_create.py` walks the cross-
version baseline matrix. Phase 11 is structurally a sibling
of `test_create.py`'s phase-8 matrix; differences come from
resize's specific surface (the `[+-]SIZE` end_spec, shrink
semantics, the qemu-doesn't-support-vmdk/vhd/vhdx asymmetry).

Where a question touches on external concepts (qemu-img
resize semantics, posix preallocation, stestr discovery),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

This is a phase plan under `PLAN-resize.md`. Refer to that
master plan for overall context. Phases 1–10 shipped the
planner, the guest binary, the host CLI + preallocation
post-pass, and the cross-version baseline matrix (3,280
baselines × 80 qemu-img versions in `instar-testdata`).
Phase 11 is the runtime harness that diffs `instar resize`
against those baselines and the live system qemu-img.

## Mission

Ship `tests/test_resize.py` covering five surfaces:

1. **Schema-drift tripwire.** A single test that walks
   `expected-outputs/resize-info-json/<target>/<version>/`
   and asserts the on-disk case set matches the
   `RESIZE_CASES` mirror declared at the top of
   `test_resize.py`. Catches drift between the test mirror
   and the testdata baseline generator (the
   `test_create_cases_match_baselines` precedent at
   `tests/test_create.py:906`).

2. **Cross-version baseline matrix** — one test per
   `(target, case)` tuple generated from `RESIZE_CASES`.
   For each case:
   - `instar create` the start image (matches the
     baseline's qemu-img create step byte-for-byte modulo
     the create writer-divergence whitelist already
     established in phase 8).
   - `instar resize -f <fmt> [--shrink] [--preallocation
     <mode>] <path> <end_spec>` exactly as the baseline's
     resize step did (the `applied_shrink_flag` and
     `preallocation` fields in the matching meta.json
     dictate which flags to pass).
   - Run system `qemu-img info --output=json` on the
     post-resize file.
   - `assert_info_equivalent` against the baseline's
     `<case>.stdout.txt`.

   For vmdk / vhd / vhdx baselines where the matching meta
   records `resize_return_code != 0` (qemu rejects), this
   matrix skips with `skipTest('qemu-img cannot resize
   <fmt>; phase 11 falls back to consistency test')` and
   defers coverage to surface 5 below.

3. **Live cross-validation** — for a curated subset of
   `RESIZE_CASES` (qcow2 + raw only — the formats qemu
   supports for resize), independently run `qemu-img create
   → qemu-img resize` and `instar create → instar resize`,
   then `instar info --output=json` on both, and assert the
   normalised JSONs match. Mirrors `TestCreateCrossValidation`
   in `tests/test_create.py:1043`.

4. **Round-trip check** — for every `(target, case)` where
   `instar check` is meaningful (excludes raw): `instar
   create → instar resize → instar check`, assert `check`
   reports clean. Catches writer-after-resize regressions
   that produce a file `qemu-img info` accepts but the
   instar reader flags. Mirrors `TestCreateRoundTripCheck`
   in `tests/test_create.py:1186`.

5. **Internal-consistency suite** (vmdk / vhd / vhdx where
   qemu-img can't resize): per case, `instar create → instar
   resize → instar info --output=json`, assert key fields
   match the expected post-resize state — at minimum
   `virtual-size == expected_final_size` from the meta. For
   vhd / vhdx this is also followed by `instar check` (vmdk
   check is a no-op today, same as create's matrix). These
   are the only resize tests for vmdk / vhd / vhdx; the
   tripwire skip in surface 2 references this surface so
   reviewers know coverage isn't lost.

6. **Targeted error-path tests** — small fixed set, not
   matrix-generated, exercising the host CLI rejections that
   never reach a baseline:
   - Subtractive end_spec without `--shrink` (mirrors
     baseline `64M-to-1M-no-shrink` but covers raw and qcow2
     both; surface 2 covers it for qcow2 + raw via baseline,
     surface 6 makes the error-message contract explicit).
   - Invalid `[+-]SIZE` strings (`+0`, `abc`, `0`, empty).
   - `--preallocation=metadata` on raw → rejected by host
     (covered by phase 9 unit test but worth a smoke).
   - `--preallocation=falloc` + shrink → rejected by host
     (phase 9 again; smoke).
   - `--object` / `--image-opts` rejected with the "not
     supported" message (already-shipped phase 8 surface,
     but the integration test pins it).

## What the survey turned up

- **`tests/base.py`'s `InstarTestBase`** at lines 36–428
  is the parent class for every integration test. Provides
  `get_instar_binary()` (falls back to
  `src/target/release/instar` and skipTest if missing),
  `_qemu_version` tuple (best-effort
  `qemu-img --version` parse for matching baselines),
  `_testdata_root` (env var
  `INSTAR_TESTDATA_PATH` or sibling-dir fallback). No
  `run_instar_resize` helper exists yet — phase 11 adds it
  to the test class (or to `base.py` if more than one test
  module needs it; for v1 a private helper on
  `TestResizeSmoke` is enough).
- **`tests/test_create.py`'s phase-8 structure** at lines
  817–1245 is the closest precedent: a top-level mirror
  dict (`CREATE_CASES`), a `_make_baseline_test()` factory
  that builds one method per case, a setattr loop in
  module scope that attaches the methods to the
  `TestCreateBaselineMatrix` class, a sibling
  `TestCreateCrossValidation` with `_make_xval_test()`,
  and a `TestCreateRoundTripCheck` with `_make_check_test()`.
  Phase 11 reuses the same shape.
- **`tests/helpers/info_json.py`'s
  `assert_info_equivalent`** at lines 126–166 is the
  comparison primitive. Already handles
  `UNIVERSAL_DIVERGENCE` (`actual-size`, `dirty-flag`),
  `CACHE_HINT_FIELDS`, per-target divergence (vhdx
  `log-size`, vmdk `cid` / `parent-cid`), and `$FILENAME`
  substitution. Resize doesn't introduce new divergence
  fields *for the post-resize info JSON specifically* —
  the same set applies. Confirmed by spot-checking three
  baselines from phase 10's output (qcow2
  `64M-minus-32M-shrink`, raw `1M-to-4M-prealloc-full`,
  vhdx `1M-to-64M-default`).
- **`KNOWN_WRITER_DIVERGENCES`** at
  `tests/test_create.py:788–814` is the create-time
  whitelist that phase 11 must respect — if instar's
  create-time output diverges from qemu's for a given
  case, the post-resize info will *also* diverge (the
  divergence is sticky), and the resize test must skip
  the same cases. The dict is currently 16 entries
  covering `refcount_bits=*`, `compat=0.10`, `zstd`,
  vhdx default block_size, and vhd CHS rounding. Phase 11
  imports it from `test_create` and applies the same skip
  logic.
- **The phase-10 baselines** at
  `instar-testdata/expected-outputs/resize-info-json/`:
  41 cases per target × 80 versions = 3,280 baselines.
  Each case has `<case_name>.{stdout.txt,stderr.txt,meta.json}`.
  The meta records `create_return_code`,
  `resize_return_code`, `info_return_code`,
  `applied_shrink_flag`, `expected_final_size`,
  `preallocation`, and `options_list` — every field phase
  11 needs for filtering and command-line reconstruction.
- **The cross-tool capability gap**: phase 10 confirmed
  qemu-img does not support resize on vmdk / vhd / vhdx
  on any shipped version. Of the 41 cases per version,
  16 record qemu's "Image format driver does not support
  resize" rejection. Phase 11 must:
  - In surface 2 (baseline matrix), skip these cases
    with a message pointing at surface 5.
  - In surface 3 (live cross-validation), skip these
    targets entirely.
  - In surface 5 (consistency suite), exercise them via
    `instar create → instar resize → instar info → instar
    check`.

## Algorithmic design

### File layout

`tests/test_resize.py`, modelled on `test_create.py`.
Single file because the surfaces share most of their
plumbing (the `RESIZE_CASES` mirror, the
`_args_for_resize_case` helper, the qemu-version dir
resolution). Total estimated size ~900 lines including
the mirror and the per-case factories — comparable to
`test_create.py`'s 1,245.

Class hierarchy:

```
class TestResizeSmoke(InstarTestBase):
    # Smoke tests: small fixed set covering the happy paths
    # per format + a handful of error paths.

class TestResizeBaselineMatrix(TestResizeSmoke):
    # Schema-drift tripwire + per-(target, case) baseline diff.
    # Generated via _make_baseline_test() / setattr loop.

class TestResizeCrossValidation(TestResizeSmoke):
    # Curated subset, instar vs system qemu-img for qcow2 + raw.

class TestResizeRoundTripCheck(TestResizeSmoke):
    # instar create -> resize -> check across all cases
    # where check is meaningful.

class TestResizeConsistency(TestResizeSmoke):
    # vmdk / vhd / vhdx: instar-only round-trip + virtual-size
    # assertion + (where applicable) instar check.
```

### The `RESIZE_CASES` mirror

Phase 11's mirror lives at the top of `test_resize.py`,
shape:

```python
# (case_name, start_size, end_spec, create_opts, prealloc)
RESIZE_CASES = {
    'qcow2': [
        ('1M-to-4M-default',           '1M',  '4M',   [],                       None),
        # ... 18 more entries matching RESIZE_CASES in
        # instar-testdata/scripts/generate-baselines.py.
    ],
    # vmdk / vhd / vhdx / raw lists identical to the generator.
}
```

The schema-drift tripwire (surface 1) asserts the on-disk
case-name set in
`expected-outputs/resize-info-json/<target>/<version>/`
matches the in-test mirror, so any future generator
change without a test update fails CI loudly. Exactly
mirrors `test_create.py:906`'s
`test_create_cases_match_baselines`.

### Per-case command-line reconstruction

Each baseline's `meta.json` records the exact flags that
were passed. Phase 11 builds the instar command line from
the *case tuple* (not the meta) so the test fails if the
mirror drifts away from what the generator produces, then
sanity-checks the case against the meta to confirm:

- `meta['applied_shrink_flag']` matches our locally
  inferred flag (subtractive end_spec OR
  resolved-end-bytes < start-bytes AND
  `not case_name.endswith('-no-shrink')`).
- `meta['preallocation']` matches the case's `prealloc`
  field.
- `meta['expected_final_size']` matches our
  `resolve_resize_end_bytes(start, end_spec)`.

If any mismatch: fail the test with a clear "mirror/baseline
divergence — regenerate" message. The infer-locally-and-
compare pattern catches a generator bug that silently
records the wrong flag set.

A small `_apply_resize_args_for_case(args, case, meta)`
helper builds the resize command's argv. It returns the
list `[*f_flag, *shrink_flag, *prealloc_flag, path,
end_spec]` ready to pass to `run_instar_resize`.

### The `run_instar_resize` helper

Adds to `TestResizeSmoke` (parallels `run_instar_create`
in `test_create.py:26`):

```python
def run_instar_resize(self, *args, timeout=120):
    instar = self.get_instar_binary()
    cmd = [str(instar), 'resize', *[str(a) for a in args]]
    try:
        r = subprocess.run(cmd, capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        return '', f'Timeout after {timeout}s', -1
```

Timeout is `120` (vs create's `60`) because resize on a
non-raw format spins up the guest VMM, which costs ~5–8 s
per case. 41 cases × 7 s = ~5 min for the matrix; well
under stestr's per-test timeout.

### Per-(target, case) factory

Mirrors `_make_baseline_test` at `test_create.py:938`:

```python
def _make_resize_baseline_test(target, case):
    case_name, start_size, end_spec, create_opts, prealloc = case

    def test(self):
        # 1. Skip if qemu didn't produce a usable baseline.
        meta = self._baseline_meta(target, case_name)
        if meta is None:
            self.skipTest(...)
        if meta['create_return_code'] != 0:
            self.skipTest('baseline: qemu create rejected ' ...)
        if meta['resize_return_code'] != 0:
            # vmdk/vhd/vhdx land here universally — covered
            # by TestResizeConsistency instead.
            self.skipTest(
                f'qemu-img cannot resize {target} '
                f'(meta resize_rc={meta["resize_return_code"]}); '
                f'phase 11 covers via TestResizeConsistency'
            )
        if meta['info_return_code'] != 0:
            self.skipTest('baseline info rejected')

        # 2. Skip if the case is a known writer divergence.
        # (Create-time divergence transfers to post-resize info.)
        known = KNOWN_WRITER_DIVERGENCES.get((target, case_name))
        if known is not None:
            self.skipTest(f'create-time divergence carries over: {known}')

        # 3. Skip the shrink-rejection cases — those exercise
        # a host CLI error path, not a baseline-diff.
        if case_name.endswith('-no-shrink'):
            self.skipTest('rejection case; covered by smoke tests')

        # 4. Sanity-check: mirror vs meta.
        self._assert_case_matches_meta(target, case, meta)

        # 5. Build the start image with instar create.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / f'image.{ext_for(target)}'
            # Pass the create opts as -o key=val, plus -f.
            c_args = ['-f', _instar_target_name(target)]
            for opt in create_opts:
                c_args.extend(['-o', opt])
            c_args.extend([str(path), start_size])
            _, c_stderr, c_rc = self.run_instar_create(*c_args)
            self.assertEqual(c_rc, 0, ...)

            # 6. Run the resize.
            r_args = self._apply_resize_args_for_case(target, case, meta)
            r_args = [a.replace('$PATH', str(path)) for a in r_args]
            _, r_stderr, r_rc = self.run_instar_resize(*r_args)
            self.assertEqual(r_rc, 0, ...)

            # 7. Run system qemu-img info on the post-resize file
            # and compare to the baseline.
            info_stdout, info_stderr, info_rc = self._run_qemu_img_info(path)
            if info_rc == -1 and 'not installed' in info_stderr:
                self.skipTest('system qemu-img not installed')
            self.assertEqual(info_rc, 0, ...)
            expected = self._baseline_stdout(target, case_name).read_text()
            assert_info_equivalent(self, info_stdout, expected,
                                    target, tmp_path=str(path),
                                    msg=f'{target}/{case_name}')

    test.__name__ = f'test_baseline_{target}_{case_name.replace("-", "_")}'
    test.__doc__ = (
        f'instar resize -f {target} {" ".join(create_opts)} '
        f'{start_size} -> {end_spec} '
        f'(prealloc={prealloc}) matches phase-10 baseline.'
    )
    return test
```

The mirror-vs-meta sanity check is the divergence-catcher: if
the test mirror falls out of sync with the testdata
generator the test fails with a clear message before
reaching the more expensive qemu/instar invocations.

### Surface 3: cross-validation subset

```python
RESIZE_CROSS_VALIDATION_CASES = [
    # qcow2 only (qemu supports), small + default options to
    # keep run time bounded.
    ('qcow2', ('1M-to-4M-default',     '1M', '4M',  [],                None)),
    ('qcow2', ('1M-to-64M-default',    '1M', '64M', [],                None)),
    ('qcow2', ('1M-to-4M-prealloc-full','1M','4M',  [],                'full')),
    ('qcow2', ('64M-minus-32M-shrink', '64M','-32M',[],                None)),
    # raw paths
    ('raw',   ('1M-to-64M-default',    '1M', '64M', [],                None)),
    ('raw',   ('64M-to-1M-shrink',     '64M','1M',  [],                None)),
    ('raw',   ('1M-to-4M-prealloc-full','1M','4M',  [],                'full')),
]
```

For each entry: independently run qemu and instar through the
full create + resize pipeline, then compare via
`instar info` on both outputs (not qemu info on both — using
instar info on both sides ensures we're measuring writer
agreement, not reader agreement; matches the create pattern at
`test_create.py:1120`).

### Surface 4: round-trip check

For every `(target, case)` in `RESIZE_CASES`:
- Skip raw (instar check is no-op).
- Skip cases where `instar create` is known to fail
  (`KNOWN_CHECK_FAILURES` from `test_create.py:1176`
  — currently just `qcow2/1G-rb-64`; none of the resize
  case set hits this today, but the dict is imported
  defensively in case it grows).
- Skip the `-no-shrink` rejection cases.
- `instar create → instar resize → instar check`, expect
  rc==0.

Catches a resize emitter regression that produces a file
qemu-img info accepts but instar check flags. Mirrors
`test_create.py:1198`.

### Surface 5: consistency suite (vmdk / vhd / vhdx)

For every `(target, case)` where
`meta['resize_return_code'] != 0` (i.e. qemu rejects but
instar supports) — vmdk × 3 + vhd × 6 + vhdx × 5 = 14 cases:

```python
def test(self):
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f'image.{ext_for(target)}'
        # Create the start image at the baseline's start_size.
        _, _, c_rc = self.run_instar_create(...)
        self.assertEqual(c_rc, 0, ...)
        # Resize.
        _, _, r_rc = self.run_instar_resize(...)
        self.assertEqual(r_rc, 0, ...)
        # Info: virtual-size matches expected_final_size.
        info_stdout, _, info_rc = self.run_instar_info(
            path, output_format='json')
        self.assertEqual(info_rc, 0, ...)
        info = json.loads(info_stdout)
        self.assertEqual(
            info['virtual-size'],
            meta['expected_final_size'],
            ...
        )
        # Check (vhd / vhdx only; vmdk check is a no-op).
        if target in ('vhd', 'vhdx'):
            _, k_stderr, k_rc = self.run_instar_check(path)
            self.assertEqual(k_rc, 0, ...)
```

The vmdk subset skips `instar check` since the integration
suite as a whole treats vmdk checks as unsupported today.

Each case has known divergences inherited from
`KNOWN_WRITER_DIVERGENCES` (vhdx default block_size, vhd
CHS rounding); for *consistency* purposes those don't
matter — the post-resize virtual size is the only field we
assert. The create-time divergence is what trips the baseline
matrix, not what tripa virtual-size.

### Surface 6: targeted error-path tests

Six methods on `TestResizeSmoke`, no factory:

```python
def test_shrink_without_flag_rejected_raw(self): ...
def test_shrink_without_flag_rejected_qcow2(self): ...
def test_invalid_size_string_rejected(self): ...  # 4 subtests
def test_metadata_on_raw_rejected(self): ...
def test_preallocation_falloc_with_shrink_rejected(self): ...
def test_object_flag_rejected(self): ...
def test_image_opts_flag_rejected(self): ...
```

Each is a small standalone case targeting a specific error
message. Pins the contract that phase 8 / 9 established and
catches regressions in the human-facing strings.

## Test surface

The full `test_resize.py` adds **roughly 200 generated
tests + 9 fixed tests = 209 cases**:

- Surface 1 (schema tripwire): 1 test.
- Surface 2 (baseline matrix): 41 case-names per format ×
  5 formats = 205 entries, of which:
  - Run end-to-end (qcow2 + raw): 19 + 8 = 27 cases minus
    the 2 shrink-without-flag cases minus the known
    writer divergences ≈ ~22 actively-run.
  - Skip via meta (vmdk + vhd + vhdx baselines all record
    qemu rejection): 3 + 6 + 5 = 14 cases skipped.
  - Skip via writer divergence: ~6 cases.
  - Total: 205 entries, ~22 actively run, the rest skipped
    with documented reasons.
- Surface 3 (cross-validation): 7 cases.
- Surface 4 (round-trip check): ~33 cases (all non-raw,
  non-no-shrink), ~30 actively run.
- Surface 5 (consistency suite): 14 cases.
- Surface 6 (error paths): ~9 fixed tests.

End-to-end wall-clock estimate:
- Each non-raw resize ≈ 6 s (KVM spin-up + planning +
  guest exec + post-pass).
- Raw resize ≈ 0.1 s.
- Total: roughly 70 actively-run KVM tests × 7 s + 30
  raw × 0.1 s ≈ **~8 minutes** under stestr's default
  serial run. With `--concurrency=4` (what
  `make test-container` uses) ≈ 2 minutes.

## Public API delta

None. Phase 11 is purely an addition under `tests/`.

## Open questions

1. **Should the baseline matrix skip on *qemu-img missing*
   or hard-fail?** `test_create.py` uses `skipTest`. Same
   here — CI installs qemu-img; dev hosts may not. Skip
   keeps the test honest about what it actually
   measured.

2. **Should we cross-validate via qemu-img info or instar
   info?** The create precedent uses `instar info` on both
   outputs. Reason: we want to measure writer agreement,
   not reader agreement. Phase 11 follows suit. (Separately,
   surface 2 uses *qemu-img info* against the baseline,
   matching the baseline-generator's recording — that's a
   reader agreement check.)

3. **Should the per-case factory build the start image
   via `instar create` or `qemu-img create`?** Building
   via `instar create` is the simpler path — we already
   have it in the harness, no shell-out to qemu-img beyond
   the final info call. The downside: if `instar create`
   has a divergence for a given case, the post-resize
   info will also diverge, and the baseline test fails
   for a reason that's really a create bug. The fix:
   skip via `KNOWN_WRITER_DIVERGENCES` (already imported)
   so create-time divergences don't pollute resize
   results. *Recommendation*: use `instar create`. The
   create matrix is well-covered by phase 8; carrying
   the writer-divergence whitelist forward into resize is
   honest and simple.

4. **Should we also test additive end_spec via
   `qemu-img info` after `qemu-img create` (no instar
   involvement) to confirm baselines are well-formed?**
   No — that's phase 10's job (schema sanity check is
   done at generation time and via surface 1's tripwire).
   Phase 11's purpose is to validate instar against the
   recorded truth, not the truth against itself.

5. **Should the live cross-validation surface include
   `--preallocation=metadata` for qcow2?** Today instar
   rejects `qcow2 + metadata + resize` via the
   `PreallocationUnsupported` planner rejection (phase
   2c, deferred). qemu accepts it. Including the case
   would fail consistently until the planner gains
   support, which is queued under master-plan Future
   work. *Recommendation*: skip the metadata cross-
   validation for now; add a TODO comment pointing at the
   future-work entry so it lands the day the planner
   lifts the rejection.

6. **Stestr concurrency.** Resize tests spin up the
   guest VMM, which acquires `/dev/kvm` per test. The
   container path uses `--concurrency 4`; KVM is
   re-entrant per-process, so this is fine. No special
   handling needed.

7. **stestr filtering / `--no-discover`.** No changes to
   the existing test-discovery config. `test_resize.py`
   gets picked up automatically by the same stestr.conf
   that catches `test_create.py`.

8. **The shrink-without-flag rejection cases**
   (`64M-to-1M-no-shrink` for qcow2 and raw). The
   baseline records qemu's error. Phase 11's surface 2
   skips them (we don't want a baseline-diff on an error
   message — too brittle); surface 6 covers the rejection
   contract explicitly via a fixed assertion on the
   stderr substring. Trade-off accepted.

9. **`virtual-size` field name** — qemu-img info JSON
   uses `virtual-size` (hyphen). Confirmed by reading
   `tests/test_create.py:261` and a phase-10 baseline.
   Phase 11's surface 5 uses the same key.

10. **VHD `expected_final_size` mismatch from CHS
    rounding.** qemu's `virtual-size` differs from
    instar's because qemu rounds VHD virtual_size up to
    the next CHS-aligned multiple (documented in
    `KNOWN_WRITER_DIVERGENCES`). For surface 5's
    consistency check on vhd, the assertion is
    `info['virtual-size'] == meta['expected_final_size']`
    using the value instar wrote (no rounding). Since the
    consistency check runs only instar (qemu rejected the
    resize), no divergence. The risk is the value drifts
    if a future phase teaches the vhd planner CHS
    rounding — at which point the test catches it and we
    update the expected formula. *Recommendation*:
    document the CHS-rounding caveat in a comment so
    future-us doesn't get surprised.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 11a | medium | sonnet | none | Create `tests/test_resize.py` containing surfaces 1, 6, plus the `TestResizeSmoke` parent class. Surface 1: schema-drift tripwire walking `expected-outputs/resize-info-json/<target>/<version>/` and asserting against the in-test `RESIZE_CASES` mirror (mirrors `tests/test_create.py:906`). Surface 6: the nine targeted error-path tests covering shrink-without-flag, invalid size strings, metadata-on-raw, falloc+shrink, --object, --image-opts. Add `run_instar_resize()` to `TestResizeSmoke` (parallel to `run_instar_create` in `test_create.py:26`). Add helper `_apply_resize_args_for_case(target, case, meta)` that builds the resize argv from a case + its meta. Add helper `resolve_resize_end_bytes(start, end_spec)` (port the function from phase 10's generator). Run the smoke + error tests via `make test-integration -- tests.test_resize`. Commit. |
| 11b | medium | sonnet | none | Add surface 2 (`TestResizeBaselineMatrix`): the per-`(target, case)` factory `_make_resize_baseline_test()` and the setattr loop. Add helpers `_baseline_root` / `_baseline_version_dir` / `_baseline_stdout` / `_baseline_meta` / `_run_qemu_img_info` (lift from `test_create.py:834–904`). Add the mirror-vs-meta `_assert_case_matches_meta()` sanity check. Import `KNOWN_WRITER_DIVERGENCES` from `test_create` and apply it as a skip. Run the matrix; expect ~22 actively-run + ~180 documented skips. Commit. |
| 11c | small | sonnet | none | Add surfaces 3, 4, 5: `TestResizeCrossValidation` (~7 cases for qcow2 + raw), `TestResizeRoundTripCheck` (~30 cases create→resize→check), `TestResizeConsistency` (~14 cases for vmdk/vhd/vhdx). All three use the same setattr-factory pattern. Confirm wall-clock under 10 min in serial; under 3 min with `make test-container`'s `--concurrency 4`. Commit. |
| 11d | small | sonnet | none | Wall-clock smoke: run `make test-rust && make test-integration` end-to-end on a clean tree. Capture timings. If any surface unexpectedly skips more than half its cases, investigate (likely a meta-decode bug). Mark phase 11 complete in `docs/plans/PLAN-resize.md`. Commit. |

## Out of scope for phase 11

- Differential fuzz (phase 12).
- Documentation (phase 13).
- Lifting `qcow2 + metadata` planner rejection (master-
  plan Future work).
- Cross-tool consistency for vmdk / vhd / vhdx (qemu
  doesn't support resize; surface 5 is the substitute).
- Coverage on the multi-file vmdk subformats
  (twoGbMaxExtentSparse etc.) — instar rejects them
  outright; covered by existing planner unit tests.

## Success criteria for phase 11

- `make test-integration` passes (including phase 11)
  in serial mode and with `--concurrency 4`.
- ~22 baseline cases actively diff against the recorded
  qemu-img output; ~180 skip with documented reasons
  (qemu doesn't support format, create-time divergence,
  rejection case).
- Cross-validation runs ~7 cases, all pass.
- Round-trip check passes for every actively-run case.
- Consistency suite covers all 14 vmdk/vhd/vhdx cases,
  asserting `virtual-size` matches `expected_final_size`
  and (for vhd / vhdx) `instar check` reports clean.
- Targeted error tests all pass.
- The schema-drift tripwire fails loudly the next time
  `RESIZE_CASES` in the generator changes without a
  matching mirror update — verify by intentionally
  removing one entry from the in-test mirror, running
  the tripwire, observing the failure, restoring.
- No `actual-size` / `dirty-flag` / cache-hint mismatch
  surfaces (the existing
  `assert_info_equivalent` normalisation covers
  everything we've seen in the phase-10 baselines).

## Sub-agent guidance

Read these files before starting any step:

- `tests/test_create.py` (the entire file — phase 11 is
  structurally a sibling; reuse every helper /
  patterns).
- `tests/base.py:36–428` (`InstarTestBase` surface).
- `tests/helpers/info_json.py` (`assert_info_equivalent`,
  the divergence sets — confirm nothing new is needed for
  resize).
- `instar-testdata/scripts/generate-baselines.py`
  (`RESIZE_CASES` definition + `parse_qemu_size` /
  `resolve_resize_end_bytes` — port the resolver into
  the test file so the mirror-vs-meta sanity check can
  recompute byte sizes).
- `instar-testdata/expected-outputs/resize-info-json/qcow2/10.2.0/`
  (sample baselines + meta to confirm field shapes
  before writing the harness).
- `docs/plans/PLAN-resize-phase-10-baselines.md` (the
  capability-gap section explaining why vmdk / vhd /
  vhdx baselines record qemu rejection).
- `docs/plans/PLAN-resize-phase-09-preallocation.md`
  (the host CLI rejections phase 11 surfaces 6 pins).

The management session review checklist is the same as
prior phases: per-step `git diff` review; smoke before
full matrix; report any surface that unexpectedly skips
more than half its cases (signals a meta-decoding bug,
not a feature gap).
