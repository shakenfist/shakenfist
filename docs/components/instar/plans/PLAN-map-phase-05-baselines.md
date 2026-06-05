# Phase 5: cross-version baselines

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/) ·
Previous phase: [PLAN-map-phase-04-output-formatting.md](/components/instar/plans/PLAN-map-phase-04-output-formatting/)

## Status: Complete

`instar-testdata` commits `4e56008d8` (generator extension),
`8e0498ca3` + `315859c3d` (profile dedup), and `0f972d5b1`
(raw baselines) produced `map-human` + `map-json` baselines
for all 80 qemu-img versions (6.0.0–10.2.x) across every
safe-tier source image — ~6,240 baseline cells total.
`detect-profiles.py` deduplicates into 1 `map-human` profile
(stable across the full range) and 3 `map-json` profiles
(transitions at 6.0.x→6.1.x — likely `compressed` field
addition — and 8.1.x→8.2.x).

## Mission

Generate the `map-human` and `map-json` baseline matrix in
the sibling `instar-testdata` repository, covering the full
80-version `qemu-img-binaries/x86_64/` set against every
safe-tier source image. The matrix is consumed by phase 6's
integration tests (`tests/test_map.py`), which check
`instar map`'s output against the version-keyed expected
output for whichever `qemu-img` is installed on the host.

Phase 5's deliverable is bulk data, not code: ~80 versions
× ~44 safe-tier images × 2 output types ≈ 7,000 raw
baseline files, deduplicated by `detect-profiles.py` into a
handful of profile directories per (output_type,
src_format). The script changes are small extensions to the
existing baseline generator; the run itself takes ~30
minutes against a warm `qemu-img-binaries/` tree.

## Why this is its own phase

Phase 4 shipped byte-for-byte qemu-img-compatible output for
the current dev qemu-img. Phase 5 confirms that output
matches across the supported qemu-img version range and
captures the version-keyed expected output that phase 6's
integration tests will diff against. Without phase 5 the
phase 4 renderer's "byte-for-byte parity" claim is a
one-version snapshot.

Bundling phase 5 with phase 4 (renderer) or phase 6
(integration tests) would tangle two unrelated kinds of
work: phase 4 is pure Rust polish; phase 6 is Python
integration plumbing; phase 5 is a generator extension plus
a bulk data run. The clean split is what the
PLAN-measure / PLAN-create / PLAN-rebase / PLAN-commit
predecessors all use.

## Architecture

### Cross-repository split

Phase 5 commits land across two repositories:

1. **`instar-testdata`** (sibling repo,
   `~/src/shakenfist/instar-testdata/`, GitLab remote
   `gitlab.home.stillhq.com:private/instar-testdata.git`):
   - Generator script extensions
   - Generated baselines (raw + profiles)
   - testdata README update
   The repository is a private GitLab project; the
   convention from earlier phases is one commit for the
   script change and a second for the generated data
   (kept separate because the data commit is multi-thousand
   files and would mask the script changes in `git log`).

2. **`instar-wt-map`** (this worktree, GitHub remote
   `shakenfist/instar`):
   - PLAN-map.md execution-table status update
   - CHANGELOG.md entry
   - Possibly `docs/quirks.md` adjustments if the generator
     surfaces version-keyed divergences not already
     documented in phase 4c.

The phase plan tracks both repos but phase 5's git commits
are *not* atomic across them — instar-testdata lands first
(because the baselines are the deliverable), instar
documents that landing after the testdata commits are
pushed.

### `generate-baselines.py` extension

Add a new `'map'` entry to the `COMMANDS` dict in
`instar-testdata/scripts/generate-baselines.py`:

```python
'map': {
    'output_types': {
        'map-human': None,       # default human-readable output
        'map-json': 'json',      # JSON output (--output=json)
    },
    # qemu-img map reads every format the parser supports.
    # Same whitelist as info / check / measure.
    'supported_formats': [
        'raw', 'qcow2', 'vmdk', 'vmdk3', 'vhd', 'vhdx',
        'qcow1', 'qed', 'vdi',
        # vpc is qemu's internal name for vhd
        'vpc',
    ],
    # build_cmd: qemu-img map [--output=FMT] IMAGE
    'build_cmd': lambda binary, image_path, output_format: (
        [str(binary), 'map'] +
        ([f'--output={output_format}'] if output_format else []) +
        [str(image_path)]
    ),
},
```

Add a `generate_map_baseline` helper modelled on
`generate_measure_source_baseline` (the simpler shape — no
target-format axis, no size-mode):

```python
def generate_map_baseline(
    binary: Path,
    version: str,
    image: dict,
    images_root: Path,
    output_dir: Path,
    output_format: str = None,
    timeout: int = 30,
) -> dict:
    """
    Generate a map baseline for one source-image case.

    Runs: qemu-img map [--output=FMT] <image>
    Output filename stem is '<image-id>'.
    Writes into output_dir which should be
      <output_type>/<src_format>/<version>/
    Returns dict with status and details.
    """
    # … boilerplate identical to generate_measure_source_baseline
    # minus the target_format axis.
```

Add a dispatch branch in the main loop alongside the
existing measure / create / resize / rebase / commit
branches:

```python
elif command_name == 'map':
    # -- map command: source-image mode only --
    for output_type_name, output_format in output_types.items():
        print(f'  Output type: {output_type_name}')
        for image in images:
            image_format = image.get('format', '').lower()
            src_dir = (
                output_root / output_type_name / image_format / version
            )
            src_dir.mkdir(parents=True, exist_ok=True)
            total += 1
            result = generate_map_baseline(
                binary, version, image, images_root,
                src_dir, output_format,
            )
            label = f'{image["id"]}'
            # ... same OK / WARN / TIMEOUT / ERROR dispatch as measure
```

Update the `--command` argparse choices to include `map`,
and add `map` to the docstring's command list.

### `detect-profiles.py` extension

Add `'map-human'` and `'map-json'` to
`instar-testdata/scripts/detect-profiles.py`:

```python
# New command-based naming
CHECK_OUTPUT_TYPES = ['check-human', 'check-json']
COMPARE_OUTPUT_TYPES = ['compare-human', 'compare-json']
MEASURE_OUTPUT_TYPES = ['measure-human', 'measure-json']
CREATE_OUTPUT_TYPES = ['create-info-json']
MAP_OUTPUT_TYPES = ['map-human', 'map-json']

OUTPUT_TYPES = (
    INFO_OUTPUT_TYPES
    + CHECK_OUTPUT_TYPES
    + COMPARE_OUTPUT_TYPES
    + MEASURE_OUTPUT_TYPES
    + CREATE_OUTPUT_TYPES
    + MAP_OUTPUT_TYPES
)

# Map uses the per-bucket layout:
# <type>/<src_format>/<version>/<image-id>.stdout.txt
MULTI_BUCKET_TYPES = set(
    MEASURE_OUTPUT_TYPES + CREATE_OUTPUT_TYPES + MAP_OUTPUT_TYPES
)
```

Map's baseline layout buckets by source format (like
measure) rather than target format (like create), because
map is read-only on the source — there's no target axis.

### Baseline volume estimate

- 80 qemu-img versions × ~44 safe-tier images × 2 output
  types = ~7,040 raw baseline files.
- 3 files per baseline (`.stdout.txt`, `.stderr.txt`,
  `.meta.json`) = ~21,000 small files.
- JSON outputs for highly fragmented images may reach ~50
  KiB each; average is closer to 5 KiB.
- After `detect-profiles.py` dedup: expected 1-3 profiles
  per (output_type, src_format) bucket. qemu-img map's
  output format is stable across versions (the `compressed`
  JSON field is the only known addition; need to verify
  the exact version range during 5b).

Disk space: ~150 MiB raw + ~5 MiB profiles ≈ 155 MiB
total. Comparable to the existing measure baselines.

Runtime: ~30 minutes for the full sweep on a warm
`qemu-img-binaries/` tree (per the measure precedent in
PLAN-measure phase 6).

### `--start-offset` / `--max-length` window cases

The master plan called for a handful of window cases
analogous to measure's `SIZE_CASES`. Phase 5 ships **only
the default-window baselines** (no `--start-offset`, no
`--max-length`). Window-case behaviour is exercised in
phase 6's integration tests with bounded fixtures
constructed in `tests/test_map.py` itself — easier than
baseline-generating per-image window cases (which would
need per-image virtual-size knowledge to construct
mid-image / end-of-image / past-EOF cases sensibly).

Future work: if differential fuzzing (phase 8) surfaces
version-keyed window-handling drift, add a `WINDOW_CASES`
list to the generator that runs each window case under
`_window` bucket (analogous to measure's `_size` bucket).

### Old qemu-img versions

The 80-version matrix runs from 6.0.0 to 10.2.0. Spot
checks during plan research suggest qemu-img map output is
stable across this range modulo one known addition: the
`compressed` JSON field was introduced at some point.
Phase 5 records whatever each version emits; the dedup
machinery handles version-keyed differences cleanly. If a
particular version segfaults or rejects an image, the
`.meta.json` records the non-zero exit and the integration
test in phase 6 skips that cell with a documented reason.

### Cross-version edge cases to verify during the run

During phase 5b, eyeball the output of a handful of
representative versions to catch surprises:

1. **qemu-img 6.0.0**: oldest version in the matrix; verify
   `compressed` field presence / absence.
2. **qemu-img 7.2.0**: mid-range; sanity check.
3. **qemu-img 10.0.8 / 10.2.0**: newest; matches the
   phase 4 dev target.
4. **One source per format**: qcow2, raw, vmdk, vhd, vhdx
   — confirm each format produces sensible output and that
   no format hits a "block driver does not support" error
   uniformly (those would imply we're listing the wrong
   `supported_formats` set).
5. **Empty image**: confirm the all-zero qcow2 produces
   the expected one-extent `present: false, zero: true`
   line in JSON / header-only in human.

The expected results match the phase 4a fixtures I already
verified against qemu-img 10.0.8.

### Documentation outside the generator

- `instar-testdata/README.md` (if it has a baseline-
  inventory section) gets a one-line entry for the new map
  baselines.
- `instar-wt-map/CHANGELOG.md` Unreleased / Added gets a
  one-line entry citing the new baselines.
- `instar-wt-map/docs/plans/PLAN-map.md` execution table:
  phase 5 row flipped from "Not started" to "Complete".

## Open questions

1. **Window-case baselines**: defer to phase 6 (as
   per-test fixtures) or include in phase 5 (as additional
   baseline buckets)? **Recommendation**: defer. The
   master plan was permissive; per-test fixtures are
   easier to maintain and don't need version-keyed dedup.

2. **VMDK monolithicFlat sources in the matrix**: instar
   refuses these host-side. qemu-img map handles them.
   The baseline-generator runs qemu-img, not instar, so
   the qemu-img-side baselines record valid output;
   phase 6's integration test will need a skip-list for
   monolithicFlat sources. **Recommendation**: include
   them in the baseline run (they're in the safe-tier
   manifest), and document the integration-test skip in
   phase 6.

3. **Chain images**: instar refuses sources with backing
   files. qemu-img map walks the chain. The
   baseline-generator records qemu-img's chain-walking
   output. **Recommendation**: same as #2 — include in the
   baseline run, integration test skips chain sources
   with a documented reason pointing at the chain
   follow-up.

4. **Profile naming**: existing profiles use sha256-prefix
   names like `profile-a3f4e2d8`. Map follows the same
   convention; no special handling needed.

5. **Empty (zero-extent) JSON output**: an all-zero qcow2
   emits one extent `[{ ..., "present": false, "zero":
   true, ... }]`. This is well-defined; just confirms
   during 5b that older qemu-img versions don't emit
   `[]` instead.

6. **Run-the-generator host**: the script is heavy enough
   that it warrants a beefy host. Same as measure's
   precedent — run on the dev box, commit the result,
   push.

7. **Re-generating after a format-detection change**: if
   instar's format detection changes (e.g. better
   raw/vhd disambiguation), the baseline-generator's
   image-format assignments don't change (they come from
   the manifest, which is authored separately). No
   re-run needed unless the manifest changes.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 5a (instar-testdata) | medium | sonnet | none | Extend `instar-testdata/scripts/generate-baselines.py` with the `'map'` COMMAND entry per the schema in the Architecture section. Add `generate_map_baseline(binary, version, image, images_root, output_dir, output_format, timeout=30)` helper modelled on `generate_measure_source_baseline` (line ~946) but without the target_format axis. Add a `elif command_name == 'map':` dispatch branch in the main loop (around line 2334, next to the measure branch) that iterates over output_types × images, calling `generate_map_baseline`. Update the `--command` argparse choices and the docstring's command list to include `map`. Extend `instar-testdata/scripts/detect-profiles.py` with `MAP_OUTPUT_TYPES = ['map-human', 'map-json']` added to `OUTPUT_TYPES` and `MULTI_BUCKET_TYPES`. Smoke-test with `./scripts/generate-baselines.py --command map --version 10.0.0` and confirm the directory structure (`expected-outputs/map-{human,json}/<src_format>/10.0.0/<image>.{stdout,stderr,meta.json}`) is created with sensible content. Commit to instar-testdata as one commit: `scripts: add map command to baseline generator (PLAN-map phase 5a).` |
| 5b (instar-testdata) | low | sonnet | none | Run the full sweep: `./scripts/generate-baselines.py --command map` (no version filter — exercises all 80 binaries) followed by `./scripts/detect-profiles.py --output-type map-human` and `--output-type map-json`. Expected runtime ~30 minutes. Spot-check the generated profiles against a representative set of versions (6.0.0, 7.2.0, 10.0.8, 10.2.0 — see "Cross-version edge cases" in the Architecture section). Commit to instar-testdata as one commit: `expected-outputs: add map baselines for 80 qemu-img versions (PLAN-map phase 5b).` Expected commit size: ~7,000 raw baseline files + a handful of profile directories. Disk usage: ~155 MiB. **Low effort because:** mechanical run-and-commit; the script has been smoke-tested in 5a. If a particular version's output is surprising (e.g. unexpected segfault on a specific image), capture in the commit message and proceed. |
| 5c (instar-wt-map) | low | sonnet | none | Update `docs/plans/PLAN-map.md` execution table to flip phase 5's status to *Complete*. Update `CHANGELOG.md` Unreleased / Added with one line citing the new map baselines in instar-testdata. If 5b surfaced any version-keyed divergences not already documented in phase 4c's quirks, add them to `docs/quirks.md`'s map section. Run `pre-commit run --all-files`. Commit to instar (this worktree) as: `map: close out phase 5 of PLAN-map (cross-version baselines).` |

Total: 3 commits across two repositories.

### Why no high-effort step

Phase 5 is entirely mechanical extension of an existing
generator. The schema is well-understood from measure /
create / resize / rebase / commit. The bulk-data commit
needs visual sanity-checking but no judgement calls — if a
particular cell errors out, the meta.json records it and
phase 6 handles the skip.

### Out of scope for phase 5

- Integration tests against the baselines (phase 6).
- Window-case (`--start-offset` / `--max-length`)
  baselines (deferred to phase 6 per-test fixtures).
- Coverage-guided fuzz harness updates (phase 7).
- Differential fuzz against qemu-img map (phase 8).
- New testdata fixtures specifically for map (the safe-tier
  manifest already covers the formats we need).
- Output-profile machinery additions in instar's VMM (phase
  4 deferred this to phase 5; phase 5 confirms whether any
  is needed; based on dev-machine spot checks, none is
  expected).

## Success criteria

- `instar-testdata/scripts/generate-baselines.py --command
  map --help` lists the new map command.
- `instar-testdata/scripts/detect-profiles.py
  --output-type map-human` runs cleanly against the
  generated raw data.
- `instar-testdata/expected-outputs/map-{human,json}/`
  directories exist with one bucket per source format and
  one version directory per qemu-img binary.
- Profile directories exist under
  `expected-outputs/map-{human,json}/<src_format>/profiles/`
  with a small (1-3) count of profile-hash directories per
  bucket.
- Spot-check on a handful of cells matches qemu-img map's
  output for those versions (eyeball comparison during
  5b).
- The instar-testdata commits push cleanly to GitLab.
- `instar-wt-map`'s PLAN-map.md table and CHANGELOG reflect
  phase 5 completion.

## Risks and mitigations

- **Old qemu-img versions reject `--output=json`**: some
  qemu-img versions may not support the flag. Mitigation:
  the generator records the non-zero exit; phase 6 skips
  comparison for cells where the qemu-img-side baseline
  shows a non-zero exit. Same pattern as the existing
  measure baselines (see
  `KNOWN_MEASURE_VERSION_SKIPS` in `tests/test_measure.py`).

- **qemu-img map segfaults / hangs on a specific image**:
  the generator's per-baseline timeout (30s) caps hangs;
  segfaults appear as non-zero exits. Both are recorded
  verbatim. If a known qemu CVE affects a specific
  version-image combination, document in the commit
  message and let phase 6 skip.

- **Disk usage spike during the run**: ~155 MiB peak is
  well within budget. Mitigation: run on the dev box;
  baseline storage doesn't compete with anything.

- **Version-keyed format drift**: the `compressed` field
  was added at some unknown version. The dedup machinery
  surfaces this as 2+ profiles per (output_type,
  src_format) bucket; phase 6's integration test selects
  the right profile by qemu-img version. No special
  handling needed in the generator.

- **GitLab push size**: a ~7,000-file commit may be slow
  but is well-trodden ground (measure / create / resize /
  rebase / commit all pushed similar volumes).

- **Test-image manifest changes mid-run**: the manifest
  pinning is at the testdata-repo level. If a new image
  is added between 5a and 5b, re-run 5b. No special
  handling needed.

## Back brief

Before executing any step, the executing agent should
back-brief: which repository the step affects (`instar-
testdata` for 5a / 5b, `instar` for 5c), which existing
function is the closest template (`generate_measure_-
source_baseline` for 5a's helper, `expected-outputs/
measure-*/` for 5b's directory layout), and the
runtime budget for 5b (~30 min). The reviewer should
verify that 5a's smoke-test on one version produced a
plausibly-shaped output before running the full sweep,
that 5b's commit includes both raw and profile
directories, and that 5c references the right
testdata-repo commit hash in the CHANGELOG line.
