# Phase 6: integration tests

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/) ·
Previous phase: [PLAN-map-phase-05-baselines.md](/components/instar/plans/PLAN-map-phase-05-baselines/)

## Status: Complete

`tests/test_map.py` shipped with five test classes
(TestMapSmoke / TestMapBaselineSource / TestMapWindowFilter /
TestMapErrorPaths / TestMapDivergenceRegression). Final
count: 95 active tests + 91 documented skips, 0 failures.
`tests/base.py` gained `'map'` in `COMMAND_OUTPUT_DIRS` and
the `get_profile_for_installed_qemu` helper for the 3
map-json profiles. The full sweep surfaced and fixed two
real bugs mid-phase: (1) JSON output was missing a trailing
newline (renderer fixed; the phase 4 "no trailing newline"
doc note was a `cat -A` misread, corrected in
`docs/quirks.md`); (2) host-side `--start-offset > file_size`
check compared against the on-disk file size rather than the
virtual size, causing spurious rejections for sparse
qcow2 sources (check removed).

## Mission

Add `tests/test_map.py` that exercises `instar map` end-to-
end against the phase 5 baseline matrix. Each safe-tier
source image × output type cell is compared byte-for-byte
against the version-keyed expected output for the qemu-img
installed on the test host. Cases where instar deliberately
diverges from qemu-img (chain sources refused, VHDX partial-
present, etc.) are skipped with documented reasons rather
than failing.

Phase 6 also covers window-filter behaviour
(`--start-offset` / `--max-length`) via in-test fixtures
(no baselines — built with `qemu-img create` + targeted
writes in `setUp`), error paths (the `--image-opts` rejection
and other host-side guards from phase 3b), and divergence
regression tests so a future change that silently lifts a
known divergence is surfaced rather than allowed to drift.

## Why this is its own phase

Phases 1-5 built the renderer and produced the baselines.
Phase 6 is where the byte-for-byte parity claim is actually
verified — every safe-tier qcow2 / raw / vmdk / vhd / vhdx
image gets run through `instar map` and compared against
the matching baseline. Without phase 6, the parity claim is
hand-verification only.

Splitting from phase 5 (baselines) keeps the bulk-data work
in `instar-testdata` and the Python plumbing in `instar`.
Splitting from phases 7-8 (fuzz) keeps the deterministic
regression suite separate from the random-input
campaigns — phase 6 fails loudly on a regression in a
specific image; phase 7-8 surface unknown bugs.

## Architecture

### `tests/base.py` extensions

Add `'map'` to `COMMAND_OUTPUT_DIRS`:

```python
COMMAND_OUTPUT_DIRS = {
    'info': 'qemu-img',
    'check': 'check',
    'compare': 'compare',
    'measure': 'measure',
    'create': 'create-info',
    'map': 'map',  # PLAN-map phase 6
}
```

Add `get_profile_for_installed_qemu(self, output_type, command)`
helper. With map-json's 3 profiles (one per qemu-img format
era), tests must select the profile matching the installed
qemu-img — `next(iter(profiles['profiles']))` is no longer
safe.

```python
def get_profile_for_installed_qemu(
    self,
    output_type: str,
    command: str,
) -> str:
    """
    Resolve the profile name for the installed qemu-img version.

    Returns the profile string (e.g. 'profile-6-0-0',
    'profile-10-0-0') that the version_to_profile map records
    for the host's qemu-img version. Falls back to the first
    profile when the exact version isn't in the map.
    """
    profiles = self.get_output_profiles(output_type, command)
    if not self._qemu_version:
        # Cached lookup failed; fall back to the first profile.
        return next(iter(profiles['profiles']))
    major, minor = self._qemu_version
    # version_to_profile keys are "X.Y.Z" strings. Find the
    # best-prefix match (e.g. installed 10.0.8 picks any
    # 10.0.x entry); if none, fall back to the first profile.
    v2p = profiles['version_to_profile']
    prefix = f'{major}.{minor}.'
    for key, profile in v2p.items():
        if key.startswith(prefix):
            return profile
    return next(iter(profiles['profiles']))
```

The helper does *not* fail when the installed qemu-img
isn't in the baseline matrix — falls back to the first
profile and lets the byte-equality assertion produce a
clear failure if drift is real.

### `tests/test_map.py` outline

Five test classes, all inheriting from `TestMapSmoke` to
share the `run_instar_map` helper and the `_testdata_root` /
`_qemu_version` class attributes:

1. **`TestMapSmoke(InstarTestBase)`**: shared helper +
   wiring checks.
   - `run_instar_map(*args, timeout=60)` — analogous to
     `run_instar_measure`.
   - `test_help_succeeds` — `instar map --help` returns 0
     and contains the documented flags.
   - `test_baselines_present` — `get_output_profiles(...)`
     returns non-empty `profiles` and `version_to_profile`
     for both map-human and map-json.
   - `test_smoke_qcow2_runs_and_returns_zero` — pick a
     small safe-tier qcow2 (e.g. `qcow2-min-cluster` or
     `cirros-qcow2`), run `instar map FILENAME`, expect
     rc==0 and stdout contains the header row.

2. **`TestMapBaselineSource(TestMapSmoke)`**: per-image
   factory generating one test per (image, output_type).
   Uses `_make_source_test` analogous to measure's pattern.
   - Skip when no baseline file exists.
   - Skip when baseline `meta.json` shows non-zero
     `return_code` (qemu-img reported an error for that
     cell — chain image without `-F` hint, etc.).
   - Skip when `KNOWN_MAP_DIVERGENCES` lists the image.
   - Otherwise run `instar map IMAGE --output=TYPE`, fetch
     the expected output via `get_expected_output(...,
     profile=get_profile_for_installed_qemu(...))`, and
     assert byte equality.
   - Generates ~78 tests (~39 images × 2 output types).

3. **`TestMapWindowFilter(TestMapSmoke)`**: in-test
   fixtures exercising `--start-offset` / `--max-length`.
   - `setUp` constructs a small qcow2 fixture in a
     `tempfile.TemporaryDirectory()` via `qemu-img create
     -f qcow2 -o cluster_size=65536 fixture.qcow2 1M`
     then writes allocated clusters at known offsets via
     a raw image + `qemu-img convert -f raw -O qcow2`
     intermediate (the same pattern phase 4a used).
   - `test_default_window_emits_all_extents` — no flags,
     same output as no-window.
   - `test_start_offset_clips_leading_extents` — emit
     starting at a known cluster boundary, assert
     subsequent extents only.
   - `test_max_length_clips_trailing_extents` — emit only
     the first N bytes, assert no extents past N.
   - `test_start_offset_plus_max_length_window` — combo.
   - `test_start_offset_past_eof_errors` — host-side
     pre-check produces a clear error.
   - `test_max_length_past_eof_clips_silently` —
     non-error; output ends at virtual_size.
   - These do *not* assert byte-equality against qemu-img
     (no baseline for window cases) — they assert
     structural properties (extent count, byte ranges
     reachable). The phase 4a `MapRenderer` unit tests
     already pin the byte-level output shape.

4. **`TestMapErrorPaths(TestMapSmoke)`**: host-side
   guards from phase 3b.
   - `test_image_opts_rejected` — `instar map
     --image-opts FILE` returns non-zero with a
     stderr message mentioning `--image-opts`.
   - `test_missing_source_file_errors` — non-existent
     FILENAME returns non-zero with an stderr message.
   - `test_invalid_sector_size_errors` — non-power-of-2
     `--sector-size` returns non-zero.
   - `test_chain_qcow2_rejected_with_has_backing` —
     pick a chain image from the safe-tier manifest
     (e.g. `qcow2-overlay-chain`), run `instar map`,
     expect non-zero exit with HAS_BACKING-style stderr
     message.
   - `test_vmdk_monolithicflat_rejected` — pick a
     `vmdk` descriptor image (if present in the safe
     tier), expect the `peek_is_vmdk_descriptor` host-
     side refusal message.

5. **`TestMapDivergenceRegression(TestMapSmoke)`**: for
   every entry in `KNOWN_MAP_DIVERGENCES`, assert the
   divergence still happens. If a future change
   accidentally fixes a divergence, this surfaces it as
   a failure so the entry can be cleanly removed and
   the corresponding entry in `KNOWN_MAP_DIVERGENCES`
   trimmed.

### `KNOWN_MAP_DIVERGENCES`

Module-scope dict mapping `image_id -> (output_type_pattern,
reason)`. Each entry documents a known instar-vs-qemu-img
divergence that the `TestMapBaselineSource` factory skips
rather than fails. Phase 6 entries cover:

```python
KNOWN_MAP_DIVERGENCES = {
    # Chain sources: instar refuses with HAS_BACKING; qemu-img
    # walks the chain and emits depth-tagged extents.
    'qcow2-overlay-chain': ('*', 'chain composition deferred; see PLAN-map.md'),
    'chain-middle-qcow2':  ('*', 'chain composition deferred; see PLAN-map.md'),
    'chain-top-qcow2':     ('*', 'chain composition deferred; see PLAN-map.md'),
    'sf-vda':              ('*', 'chain composition deferred; see PLAN-map.md'),
    # debian-12-sfagent uses sf-vda-backing as its backing image; instar
    # refuses both. (Add additional chain images as phase 6 surfaces them.)

    # Compressed-cluster reporting: instar emits compressed: false
    # unconditionally; qemu-img emits compressed: true for compressed
    # cluster extents. Affects map-json output for qcow2 sources with
    # compressed clusters.
    'qcow2-zstd': ('json', 'compressed-cluster reporting deferred; see docs/quirks.md'),

    # Raw sparse: instar reports one fully-allocated extent; qemu-img
    # walks SEEK_HOLE. Phase 4c quirks doc.
    'raw-sparse-empty': ('*', 'raw SEEK_HOLE detection not implemented'),

    # VHDX partial-present: instar treats every partially-present block
    # as fully data; qemu-img walks the per-sector bitmap.
    # (Specific image IDs filled in during 6c when the baselines are
    # consulted to find which images trigger the divergence.)

    # VMDK multi-extent: refused host-side by peek_is_vmdk_descriptor.
    # (Image IDs added during 6c.)
}
```

The list is intentionally conservative on draft — actual
entries are added during step 6b/6c as `make
test-integration` surfaces specific failing cells. The
phase 4c quirks doc enumerates the categories; phase 6
maps each category to specific image IDs.

### Window-filter fixture construction

Phase 4a established a clean pattern: `truncate` a raw
image to the desired virtual size, `python3 -c "..."`
writes bytes at known offsets, `qemu-img convert -f raw
-O qcow2` produces a fragmented qcow2. Phase 6's
`TestMapWindowFilter.setUp` follows the same recipe to
keep fixture construction obvious and self-contained:

```python
def setUp(self):
    super().setUp()
    self.tmpdir = tempfile.mkdtemp(prefix='instar-map-test-')
    self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
    raw_path = os.path.join(self.tmpdir, 'fixture.raw')
    qcow_path = os.path.join(self.tmpdir, 'fixture.qcow2')
    # 1 MiB raw with two 64 KiB allocated runs
    subprocess.run(['truncate', '-s', '1M', raw_path], check=True)
    with open(raw_path, 'r+b') as f:
        f.seek(0)
        f.write(b'\xab' * 0x10000)
        f.seek(0x80000)
        f.write(b'\xcd' * 0x10000)
    subprocess.run(
        ['qemu-img', 'convert', '-f', 'raw', '-O', 'qcow2',
         raw_path, qcow_path],
        check=True,
    )
    self.fixture = qcow_path
```

Each window-filter test then runs `instar map
self.fixture` with various window args and asserts
structural invariants.

### Tests-suite size budget

- TestMapSmoke: ~4 tests
- TestMapBaselineSource: ~78 tests (39 images × 2 output
  types); ~20 skipped due to chain / divergence / non-zero
  baseline
- TestMapWindowFilter: ~6 tests
- TestMapErrorPaths: ~5 tests
- TestMapDivergenceRegression: ~5-8 tests (one per
  KNOWN_MAP_DIVERGENCES entry)

Total: ~100 tests, ~75-80 active + ~20-25 documented skips.

## Open questions

1. **Profile selection fallback**: when the installed
   qemu-img isn't in the baseline matrix at all (e.g.
   qemu-img 11.0.0 ships before we regenerate the
   baselines), should the test fail or skip? **Recommendation**:
   pick the newest profile and let the byte-equality
   assertion run; if it fails, the test surfaces a real
   format drift that the user should investigate.

2. **`KNOWN_MAP_DIVERGENCES` for VHDX partial-present**:
   which specific VHDX images trigger this? **Recommendation**:
   determine empirically during 6c — let
   `test-integration` run, observe the failures, add the
   specific image IDs to the list with the documented
   reason.

3. **Window-filter byte-exact assertions**: the master plan
   left window cases out of baseline generation. Should
   phase 6 byte-compare instar's window output against
   qemu-img run inside the test? **Recommendation**: no —
   phase 4a's `MapRenderer` unit tests already pin the
   byte-level output shape; window tests only need to
   assert *structural* properties (extent count, byte
   ranges). Adding qemu-img-comparison would duplicate
   phase 4a's coverage.

4. **VMDK monolithicFlat fixture availability**: the safe-
   tier manifest may not contain a multi-extent VMDK
   source. **Recommendation**: skip the
   `test_vmdk_monolithicflat_rejected` test if no such
   fixture exists (with a clear reason); the host-side
   guard is exercised by the unit-level `MapArgs`
   tests anyway.

5. **Sf-vda as a divergence**: `sf-vda` is in
   `KNOWN_SOURCE_SCANNER_DIVERGENCES` for measure (qcow2
   scanner difference). For map, `sf-vda` likely has a
   chain (it's an overlay) and so is a chain divergence
   here. **Recommendation**: add to map's list with the
   chain reason; the measure entry stays unchanged.

6. **Compressed-cluster divergence — which images?**: the
   safe tier includes `qcow2-zstd` (a deliberately
   compressed qcow2 fixture). Other compressed-cluster
   images may also need entries. **Recommendation**:
   start with `qcow2-zstd`, add more during 6c if
   baselines reveal them.

7. **Test runtime budget**: ~100 tests × ~1s each = ~2
   minutes for the full suite. Acceptable; comparable
   to test_measure.py's ~3-minute runtime.

8. **`--start-offset` semantics divergence**: phase 4c
   quirks doc noted that instar's window filter is
   byte-level while qemu-img clamps to cluster boundaries
   on output. Phase 6's window tests should assert
   instar's byte-level behaviour rather than qemu-img
   parity — the divergence is documented and intentional
   for v1.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Extend `tests/base.py`: add `'map': 'map'` to `COMMAND_OUTPUT_DIRS` (line 27 area). Add `get_profile_for_installed_qemu(self, output_type, command)` method per the schema in the Architecture section — pick the profile whose `version_to_profile` entry matches the host's qemu-img version by major-minor prefix, falling back to the first profile when no match. Create `tests/test_map.py` with `TestMapSmoke(InstarTestBase)` containing: `run_instar_map(*args, timeout=60)` helper, `test_help_succeeds`, `test_baselines_present` (asserts non-empty profiles for both human/json), `test_smoke_qcow2_runs_and_returns_zero` (pick `cirros-qcow2` or `qcow2-min-cluster` from the safe tier; assert rc==0 and stdout starts with the `Offset` header row). Run `make test-integration TEST=test_map` and confirm the smoke tests pass. |
| 6b | high | sonnet | none | Add `TestMapBaselineSource(TestMapSmoke)` to `tests/test_map.py`. Define `KNOWN_MAP_DIVERGENCES` per the Architecture section starting with the chain images (`qcow2-overlay-chain`, `chain-middle-qcow2`, `chain-top-qcow2`, `sf-vda`), `qcow2-zstd` (compressed), and `raw-sparse-empty` (SEEK_HOLE). Implement `_make_map_source_test(image_dict, output_type)` factory analogous to measure's `_make_source_test` (test_measure.py line 693): skip cases without baseline meta.json, skip non-zero-exit baselines, skip KNOWN_MAP_DIVERGENCES entries, run `instar map IMAGE --output=TYPE`, fetch the expected output via `get_expected_output(image_id, profile, output_type, command='map')` where `profile = self.get_profile_for_installed_qemu(output_type, 'map')`, and assert byte equality after `substitute_testdata_root`. Loop over `_safe_tier_images()` × `{human, json}` to setattr ~78 test methods. Run `make test-integration TEST=test_map` and report pass/skip/fail counts. Iterate the KNOWN_MAP_DIVERGENCES list based on actual failures — add specific image IDs that trigger the documented categories (VHDX partial-present, VMDK multi-extent, etc.). **High effort because:** the per-image factory generates many tests, the version-keyed profile selection has edge cases, and the KNOWN_MAP_DIVERGENCES list needs empirical iteration to capture every image-specific divergence cleanly. |
| 6c | medium | sonnet | none | Add `TestMapWindowFilter(TestMapSmoke)` to `tests/test_map.py`. `setUp` constructs a small fragmented qcow2 fixture per the "Window-filter fixture construction" section above (truncate raw → write bytes at 0 and 0x80000 → qemu-img convert). Tests: `test_default_window_emits_all_extents` (no flags), `test_start_offset_clips_leading_extents` (`--start-offset=0x80000`), `test_max_length_clips_trailing_extents` (`--max-length=0x10000`), `test_start_offset_plus_max_length_window` (combination), `test_start_offset_past_eof_errors` (host-side rejection), `test_max_length_past_eof_clips_silently` (output ends at virtual_size). Tests assert *structural* properties — extent count, byte ranges, presence/absence of specific offsets — not byte-equality against qemu-img. Run `make test-integration TEST=test_map.TestMapWindowFilter`. |
| 6d | medium | sonnet | none | Add `TestMapErrorPaths(TestMapSmoke)` and `TestMapDivergenceRegression(TestMapSmoke)` to `tests/test_map.py`. Error-path tests: `test_image_opts_rejected` (stderr contains `--image-opts`), `test_missing_source_file_errors`, `test_invalid_sector_size_errors`, `test_chain_qcow2_rejected_with_has_backing` (runs `instar map qcow2-overlay-chain.qcow2`, expects non-zero exit + stderr mentioning backing/chain). Divergence regression: for each entry in `KNOWN_MAP_DIVERGENCES`, assert the divergence is still observable (i.e. when the test runs `instar map <image>`, instar produces output that differs from the baseline in a way matching the documented reason). Use `assertNotEqual(stdout, expected)` so an accidental fix is surfaced loudly. Run `make test-integration TEST=test_map`. |
| 6e | low | sonnet | none | Update `ARCHITECTURE.md` `operations/map/` entry: append "Integration tests in `tests/test_map.py` cross-validate `instar map` against the `qemu-img map` baselines in `instar-testdata/expected-outputs/map-*` for every safe-tier image, plus in-test fixtures for window-filter behaviour, error paths, and divergence-regression assertions for the known instar-vs-qemu-img gaps." Update `CHANGELOG.md` Unreleased / Added with one line citing the new integration tests. Run `pre-commit run --all-files`. |

Total: 5 commits.

### Why no opus step

Phase 6 is plumbing — extending an established test pattern
(test_measure.py) to a new command. No new algorithmic
work; no subtle correctness arguments. Sonnet with a
detailed brief for the per-image factory in 6b is the
right tool. The high-effort flag on 6b is for *iteration*
volume (empirically discovering the right
KNOWN_MAP_DIVERGENCES entries) rather than reasoning depth.

### Out of scope for phase 6

- Coverage-guided fuzz harness (phase 7).
- Differential fuzz against qemu-img map (phase 8).
- Window-case byte-exact comparison against qemu-img
  (deferred; phase 4a unit tests cover output shape).
- Backing-chain composition support (future work).
- Compressed-cluster reporting fix (future work).
- New testdata fixtures specifically for map (the safe-
  tier manifest already covers the formats needed).
- Output-profile machinery additions in instar's VMM
  (phase 5 produced 1 + 3 profiles cleanly; no
  vmm-side handling needed).

## Success criteria

- `tests/test_map.py` exists with the five test classes
  enumerated above.
- `tests/base.py` has `'map'` in `COMMAND_OUTPUT_DIRS`
  and a `get_profile_for_installed_qemu` helper.
- `make test-integration TEST=test_map` runs to
  completion with a documented mix of pass / skip /
  no-fail outcomes (typical: ~75 pass, ~25 skip, 0
  fail).
- `KNOWN_MAP_DIVERGENCES` covers every cell that would
  otherwise produce a `assertEqual` mismatch; each entry
  has a clear reason citing PLAN-map.md or docs/quirks.md.
- `TestMapDivergenceRegression` catches accidental
  divergence fixes (`assertNotEqual` against the
  baseline).
- `TestMapWindowFilter` verifies the window-filter
  behaviour without requiring qemu-img comparison.
- `make lint`, `make test-rust`, and `pre-commit run
  --all-files` remain clean (phase 6 is Python-only and
  doesn't touch Rust code).
- `ARCHITECTURE.md` and `CHANGELOG.md` reflect phase 6.

## Risks and mitigations

- **Per-image factory generates noise on failure**: with
  ~78 generated tests, a single regression looks like 78
  failures unless the factory short-circuits. Mitigation:
  the factory's skip logic catches missing/non-zero
  baselines and known divergences. Real failures are
  rare and indicate real format drift.

- **Version-keyed profile selection picks the wrong
  profile**: with 3 map-json profiles and qemu-img
  installed at an in-between version (8.2.0 falls in
  profile-10-0-0; 8.1.5 falls in profile-6-1-0), the
  profile lookup must be careful. Mitigation: 6a's
  `get_profile_for_installed_qemu` uses major-minor
  prefix matching; explicit unit tests verify the
  matcher for at least 3 distinct version strings.

- **Window-filter test fixtures depend on qemu-img**:
  `setUp` calls `qemu-img convert` which fails if
  qemu-img isn't on PATH. Mitigation: skip the class
  if qemu-img is unavailable (the rest of the test
  suite already depends on qemu-img for baseline
  generation; same constraint applies).

- **`KNOWN_MAP_DIVERGENCES` is incomplete on first run**:
  step 6b's brief explicitly calls out the iterative
  process — run the full suite, observe specific
  failures, add to the list with documented reasons,
  repeat until pass/skip is clean. The list is a
  living document.

- **`sf-vda` etc. may have ambiguous reasons**: an image
  may be both a chain source AND have compressed
  clusters. Mitigation: pick the more-fundamental
  reason (chain wins over compressed), document both
  in the comment.

- **CI runs on a host with a qemu-img not in the baseline
  matrix**: the profile fallback picks the first
  profile, byte-equality fails, test reports a real
  failure. Mitigation: instar-testdata's matrix is
  regenerated periodically; if a new qemu-img ships
  before the next regen, the test fails loudly which
  is the right signal.

## Back brief

Before executing any step, the executing agent should
back-brief: which file is being edited (`tests/base.py`
for 6a, `tests/test_map.py` for 6a-d, `ARCHITECTURE.md` +
`CHANGELOG.md` for 6e), which existing test class is the
closest template (test_measure.py's
`TestMeasureBaselineSource` for the per-image factory;
in-test fixture construction follows phase 4a's pattern),
and the iteration loop expected during 6b (run, observe
failures, extend `KNOWN_MAP_DIVERGENCES`, repeat). The
reviewer should verify that the per-image factory
correctly handles all four skip categories (no baseline,
non-zero baseline, KNOWN_MAP_DIVERGENCES, profile-not-
found), that `TestMapWindowFilter`'s fixture setUp is
self-cleaning, and that `TestMapDivergenceRegression`
genuinely asserts continued divergence (assertNotEqual,
not just skip).
