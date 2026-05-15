# Phase 6: cross-version `qemu-img measure` baselines

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-05-target-options.md](/components/instar/plans/PLAN-measure-phase-05-target-options/)

## Status: Not started

## Mission

Extend `instar-testdata`'s baseline generator so that for every
qemu-img binary in `qemu-img-binaries/x86_64/` (~80 versions
from 6.0.0 to 10.2.0) we record the stdout / stderr / exit-code
of:

1. `qemu-img measure -O <target> <source-image>` for each
   safe-tier image in the manifest, with `<target> ∈ {raw,
   qcow2}` (the two formats qemu-img measure supports).
2. `qemu-img measure --size <N> -O <target> -o <options>` for
   a curated list of `(size, target, options)` tuples chosen to
   exercise the size-relevant qcow2 option surface.

Phase 7 then drives `tests/test_measure.py` against the recorded
baselines, asserting `instar measure` produces byte-identical
output on every supported version.

Phase 6 is implementation in `instar-testdata/`, not the instar
repo. The only instar-repo edits are the plan file itself and
one CHANGELOG line at the end.

## Why this is its own phase

The volume is moderate (~25 MB of small text files) but the
work decomposes cleanly:

1. Script edit: teach `generate-baselines.py` and
   `detect-profiles.py` to understand a third type of command
   that has two sub-modes (`--size` and source-image) and a
   `target_format` axis. Existing commands (`info`, `check`,
   `compare`) each take a single image and emit one output —
   measure differs.
2. Long-running data generation: ~80 qemu versions × ~110
   cases per version × 2 output types ≈ 17 600 files.
3. Commit the artefact to the testdata repo.

Splitting from phase 7 means the baselines exist *before* the
test code that consumes them, so phase 7 is purely test
plumbing.

## Architecture

### Output directory layout

Mirrors the existing schema:

```
instar-testdata/expected-outputs/
├── measure-human/
│   ├── _size/                                # --size mode
│   │   ├── 6.0.0/
│   │   │   ├── 1M-raw.stdout.txt
│   │   │   ├── 1M-raw.stderr.txt
│   │   │   ├── 1M-raw.meta.json
│   │   │   ├── 1M-qcow2-default.stdout.txt
│   │   │   ├── 1M-qcow2-cs-512.stdout.txt
│   │   │   ├── 1M-qcow2-cs-64k.stdout.txt
│   │   │   ├── 1M-qcow2-rb-1.stdout.txt
│   │   │   ├── 1M-qcow2-rb-64.stdout.txt
│   │   │   ├── 1M-qcow2-extended-l2.stdout.txt
│   │   │   ├── 1M-qcow2-prealloc-metadata.stdout.txt
│   │   │   ├── 64M-qcow2-default.stdout.txt
│   │   │   ├── 1G-qcow2-default.stdout.txt
│   │   │   ├── 1G-qcow2-cs-64k.stdout.txt
│   │   │   ├── 1T-qcow2-default.stdout.txt
│   │   │   ...
│   │   ├── 7.2.0/
│   │   ...
│   ├── qcow2/                                # qcow2-source images
│   │   ├── 6.0.0/
│   │   │   ├── cirros-qcow2__qcow2.stdout.txt    # -O qcow2
│   │   │   ├── cirros-qcow2__qcow2.meta.json
│   │   │   ├── cirros-qcow2__raw.stdout.txt      # -O raw
│   │   │   ...
│   │   ├── 7.2.0/
│   │   ...
│   ├── raw/
│   ├── vmdk/
│   ├── vhd/
│   ├── vhdx/
│   ├── profiles/                              # dedup buckets
│   │   └── profile-NN/
│   ├── version-map.json
│   └── raw/                                   # raw stdout copies
└── measure-json/
    └── (same structure)
```

Key conventions:

- The pseudo-source-format bucket `_size/` holds `--size` mode
  outputs that have no source image. Leading underscore avoids
  any collision with a real format name. Filenames encode the
  size and option set: `<size>-<target>[-<option-key>].ext`.
- For source-image mode, the existing `<src_format>/<version>/<image-id>.<ext>`
  scheme gains a `__<target>` suffix on the image-id so a single
  image generates two baseline file groups (one per target).
- The `profiles/` and `version-map.json` files use the same
  scheme as the existing info / check buckets — `detect-profiles.py`
  hashes the stdouts and groups versions that produce identical
  output.

### `--size` mode case list

A curated list, sized to exercise every size-relevant qcow2
option while keeping the total small enough to commit. ~24
cases per version × 80 versions × 2 output types ≈ 3 840 files
for the `_size/` bucket.

```python
SIZE_CASES = [
    # raw target — sizes only, no options
    ("1M-raw",                "1M", "raw",   []),
    ("64M-raw",               "64M", "raw",  []),
    ("1G-raw",                "1G", "raw",   []),
    ("1T-raw",                "1T", "raw",   []),

    # qcow2 default cluster sizes across virtual-size sweep
    ("1M-qcow2-default",      "1M",  "qcow2", []),
    ("64M-qcow2-default",     "64M", "qcow2", []),
    ("1G-qcow2-default",      "1G",  "qcow2", []),
    ("1T-qcow2-default",      "1T",  "qcow2", []),

    # qcow2 cluster size sweep at 1G (the "interesting" size)
    ("1G-qcow2-cs-512",       "1G",  "qcow2", ["cluster_size=512"]),
    ("1G-qcow2-cs-4k",        "1G",  "qcow2", ["cluster_size=4k"]),
    ("1G-qcow2-cs-64k",       "1G",  "qcow2", ["cluster_size=64k"]),
    ("1G-qcow2-cs-2M",        "1G",  "qcow2", ["cluster_size=2M"]),

    # qcow2 refcount_bits
    ("1G-qcow2-rb-1",         "1G",  "qcow2", ["refcount_bits=1"]),
    ("1G-qcow2-rb-8",         "1G",  "qcow2", ["refcount_bits=8"]),
    ("1G-qcow2-rb-64",        "1G",  "qcow2", ["refcount_bits=64"]),

    # qcow2 extended_l2 + cluster size combinations
    ("1G-qcow2-extended-l2",  "1G",  "qcow2", ["extended_l2=on,cluster_size=64k"]),
    ("64M-qcow2-extended-l2", "64M", "qcow2", ["extended_l2=on,cluster_size=64k"]),

    # qcow2 compat v2
    ("1G-qcow2-compat-v2",    "1G",  "qcow2", ["compat=0.10"]),

    # qcow2 preallocation
    ("1G-qcow2-prealloc-metadata", "1G", "qcow2", ["preallocation=metadata"]),
    ("1G-qcow2-prealloc-falloc",   "1G", "qcow2", ["preallocation=falloc"]),
    ("1G-qcow2-prealloc-full",     "1G", "qcow2", ["preallocation=full"]),
]
```

A few of these (e.g. `extended_l2=on`) require qemu-img ≥ 5.0
(which is below our version floor anyway). For older versions
qemu-img will return non-zero with an "unknown option" stderr;
the baseline captures that exit code and stderr verbatim, and
phase 7 skips the comparison when the recorded baseline has
non-zero exit (no different from existing info/check tests
where a missing feature was rejected on the qemu-img side).

### Source-image mode case set

For each safe-tier image in `instar-testdata/manifest.json` (or
the existing manifest the script uses for info/check; verify
during 6a):

- `qemu-img measure -O raw <image>` → one baseline group
- `qemu-img measure -O qcow2 <image>` → one baseline group

The script computes the source format via the existing
`detect_format_from_extension` / manifest lookup that
info/check already use. Same source-format whitelist applies
(skip caution and malicious tiers by default; opt-in via
`--all-images`).

Approximate count: ~30 safe-tier images × 2 targets × 80
versions × 2 output types ≈ 9 600 files.

### `generate-baselines.py` `COMMANDS` entry

The existing `COMMANDS` dict has one entry per command with
`output_types`, `build_cmd`, and optional `supported_formats`.
Measure differs in that it has *two* output modes per
invocation (a single `-O <target>` flag) and *two* input
modes (`--size` and source-image). The cleanest extension is to
expand the build path:

```python
'measure': {
    'output_types': {
        'measure-human': None,         # default human output
        'measure-json': 'json',        # --output=json
    },
    # Targets we baseline. raw and qcow2 are the only target
    # formats qemu-img measure supports. Adding others would
    # produce error baselines, which are not useful here.
    'target_formats': ['raw', 'qcow2'],
    # Source formats we feed in. Same whitelist as info/check.
    'supported_formats': ['raw', 'qcow2', 'vmdk', 'vmdk3',
                          'vhd', 'vhdx', 'qcow1', 'qed', 'vdi'],
    # build_cmd signature differs from other commands because of
    # the target axis — handle in a dedicated branch in run_one().
    'build_cmd': lambda binary, image_path, output_format, target_format: (
        [str(binary), 'measure'] +
        ([f'--output={output_format}'] if output_format else []) +
        ['-O', target_format] +
        [str(image_path)]
    ),
    # For --size mode the size-cases helper builds its own
    # command list — handled in a dedicated branch in run_one().
    'size_cases': SIZE_CASES,
},
```

The `run_one()` (or whichever loop dispatches commands)
recognises the `target_formats` and `size_cases` keys and
iterates twice: once over source images × targets, once over
`size_cases`.

### `detect-profiles.py` updates

The existing dedup flow groups versions whose stdout output for
a given image is byte-identical. For measure:

- Compute the hash per `(image-id-with-target-suffix, output-type)`
  triple across all versions.
- The dedup tooling should detect that ~70 of 80 qemu versions
  produce identical output for most measure cases (qemu-img
  measure's algorithm changed less often than info / check).
  The resulting profile files will be much smaller than info's
  ~50 profiles.

The script likely already handles arbitrary `output_type`
strings — verify during 6b. If not, extend its `OUTPUT_TYPES`
list or whatever drives the iteration.

### Execution mechanics

The total wall-clock for the full matrix is bounded by:

- ~80 versions × ~110 invocations per version = ~8 800
  qemu-img invocations.
- Each invocation is < 1 s (qemu-img measure is metadata-only).
- Add filesystem overhead and a generous serial floor: ~30 min
  end-to-end on a single host.

Acceptable to run interactively; not pleasant to ask a
sub-agent to wait for it. Two-step execution:

1. **Sanity pass**: run against the latest qemu version
   (10.2.0) only, verify ~110 baseline files appear with
   sensible content (e.g. `1M-raw.stdout.txt` contains
   `required size: 1048576`).
2. **Full pass**: re-invoke the generator with no
   `--version` filter; let it run to completion.

The existing generator already supports `--version <V>` for
single-version runs — confirm by reading
`generate-baselines.py` lines 50-90.

### Why not subset to a few qemu versions

Tempting, but a moving target. The cross-version baseline
matrix is the contract that phase 7 enforces: any regression
against any version surfaces as a failed test. Cutting the
matrix saves disk for one release cycle and accumulates
unverified versions thereafter. Better to bite the cost once.

## Open questions

1. **Where do the size-case definitions live — in the script
   or in a separate data file?** ~24 entries doesn't warrant a
   data file. Keep them as a Python const at the top of
   `generate-baselines.py`. If the list grows past ~40,
   re-evaluate.

2. **Should measure baselines for *non-safe-tier* images be
   generated?** Caution and malicious images would produce
   useful coverage too (they exercise the parsers on
   adversarial inputs). Recommendation: stick to safe-tier in
   phase 6 to keep volume bounded. Add `--include-caution` /
   `--include-malicious` flags in a follow-up if useful.

3. **Versions that don't support a particular qcow2 option**
   (e.g. `extended_l2=on` on qemu-img 6.0.0). Recommendation:
   record the actual stderr + non-zero exit. Phase 7 skips
   comparison when the baseline has non-zero exit (matches
   the info/check pattern).

4. **The pseudo-source-format `_size/` bucket** — does
   `detect-profiles.py` need special-casing for the leading
   underscore? Recommendation: no, treat it as an opaque
   directory name. The leading `_` simply sorts it before real
   format names.

5. **Source-image-mode baseline volume**: ~30 safe-tier images
   × 2 targets × 80 versions × 2 output types ≈ 9 600 files.
   If a test image is missing locally (e.g. a referenced image
   has been removed from the manifest since the version
   matrix was last generated), the script should skip with a
   logged warning rather than fail. Verify the existing
   `info` flow has this behaviour.

6. **The `instar-testdata` repo isn't pinned by instar via a
   commit hash** — it's a sibling directory referenced at
   runtime. So phase 7's tests will pick up whatever is on
   disk. No version pin to bump. (Confirm during 6e if a pin
   exists anywhere.)

7. **What about LUKS-encrypted images?** qemu-img measure
   refuses to open encrypted images without `--object` /
   `--image-opts`. The script should skip these (already
   handled in the existing flows since info/check have the
   same constraint). Verify during 6a.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | In the `instar-testdata` repo at `/srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-testdata`, edit `scripts/generate-baselines.py`. Add a `'measure'` entry to the `COMMANDS` dict mirroring the info/check pattern but with two extra keys: `'target_formats': ['raw', 'qcow2']` and `'size_cases': SIZE_CASES`. Define `SIZE_CASES` near the top of the file with the ~24 entries listed in the plan's "`--size` mode case list" section. Extend the main dispatch loop (search for `run_one(...)` or whatever runs the per-command iteration) to recognise `target_formats` and iterate source images × targets, plus a separate pass over `size_cases`. The size-cases pass writes outputs under `<output-type>/_size/<version>/<case-name>.{stdout,stderr,meta.json}`. The source-image pass writes under `<output-type>/<src_format>/<version>/<image-id>__<target>.{stdout,stderr,meta.json}`. Reuse the existing meta.json schema. Run a sanity check: `python scripts/generate-baselines.py --command measure --version 10.2.0 --output-type measure-json` and confirm `expected-outputs/measure-json/_size/10.2.0/1M-raw.stdout.txt` exists and contains `{"required": 1048576, "fully-allocated": 1048576}`. Do NOT generate the full matrix yet — that is step 6c. The instar repo is not touched in this step. |
| 6b | medium | sonnet | none | In the `instar-testdata` repo, edit `scripts/detect-profiles.py` to handle the new `measure-human` and `measure-json` output types. The existing script probably enumerates `OUTPUT_TYPES` or iterates `expected-outputs/*/`; add the measure types so the dedup flow runs for them too. The `_size/` pseudo-format directory should be treated as a regular bucket (it groups by case-name across versions like any other source-format bucket). Run `python scripts/detect-profiles.py --output-type measure-json` after the 6a sanity pass and confirm a `profiles/` subdir + `version-map.json` are generated for the single 10.2.0-only matrix produced in 6a. instar repo not touched. |
| 6c | medium | sonnet | none | **This step is execution, not coding.** Run the generator against the full qemu-img-binaries matrix to produce all baselines. Command: `python scripts/generate-baselines.py --command measure` (no `--version` filter). Watch for warnings or skips. Expected wall-clock ~30 min on a normal host. If a particular qemu version errors out for some option combinations (e.g. extended_l2 unknown in 6.0.0), that's expected and gets recorded as a non-zero exit baseline. After completion, run `python scripts/detect-profiles.py` to regenerate the dedup profile files for both `measure-human` and `measure-json`. Spot-check a handful of baselines for correctness (e.g. `measure-json/_size/10.2.0/1G-qcow2-default.stdout.txt` must show `{"required": 393216, "fully-allocated": 1074135040}` matching the phase 1 fixture). **If the runtime exceeds 60 minutes or anything looks wrong, stop and surface the issue rather than continuing.** Operator may take this step themselves rather than delegating to a sub-agent — it's a long-running interactive command. instar repo not touched. |
| 6d | low | sonnet | none | In the `instar-testdata` repo, `git add expected-outputs/measure-*` + `git add scripts/generate-baselines.py scripts/detect-profiles.py`. Inspect `git status --short` and verify the diff is roughly "two scripts modified, ~18000 new files under expected-outputs/measure-*". Commit with a clear message. Push to the remote if the operator approves (do not push unprompted). The instar repo is still not touched. |
| 6e | low | sonnet | none | Back in the instar repo (`/srv/kasm_profiles/mikal/vscode/src/shakenfist/instar-wt-measure`), update `CHANGELOG.md` Unreleased / Added with: "Cross-version `qemu-img measure` baselines committed to `instar-testdata/expected-outputs/measure-{human,json}/`. Generated against every qemu-img binary in `qemu-img-binaries/x86_64/` (6.0.0 through 10.2.0). Consumed by phase 7's integration tests. (PLAN-measure-phase-06-baselines.md)". Also update `ARCHITECTURE.md` if the existing "Test Image Generation" section mentions the baseline matrix — add measure to the list. Run `pre-commit run --all-files`. Only `CHANGELOG.md` and possibly `ARCHITECTURE.md` modified in the instar repo. |

Total: 5 commits (4 in `instar-testdata`, 1 in `instar`).

## Out of scope for phase 6

- Test code that consumes the baselines (phase 7).
- Non-safe-tier image baselines (potential follow-up).
- Backing-chain measurement baselines (no source images
  exercise this, and chain support isn't in measure yet).
- `vmdk`, `vpc`, `vhdx` target baselines (qemu-img doesn't
  support them as measure targets; phase 7's round-trip
  tests cover those).
- Updating the existing info/check/compare baselines (this
  plan only adds measure; the others are independent).
- Pinning instar-testdata to a specific commit from the instar
  side (not currently done for any other operation).

## Success criteria

- `instar-testdata/scripts/generate-baselines.py` recognises
  the `measure` command and produces correct output for both
  `--size` and source-image modes.
- `instar-testdata/scripts/detect-profiles.py` produces
  `measure-human/profiles/` and `measure-json/profiles/`
  directories with dedup buckets, plus a populated
  `version-map.json` for each.
- The full matrix is generated and committed to
  `instar-testdata`:
  - ≥ ~20 `_size/` cases × 80 versions × 2 output types
  - ≥ ~25 safe-tier source images × 2 targets × 80 versions
    × 2 output types
- Spot-check pass: at least 3 baseline files (a `_size/raw`
  case, a `_size/qcow2` case with options, and a source-image
  case) match the values phase 1's fixture table pinned.
- The `instar` repo's `CHANGELOG.md` notes the new baselines.
- No instar code changes in this phase.

## Risks and mitigations

- **Old qemu versions reject a recent option** — silent
  baseline corruption (we record an error baseline that phase
  7 then can't compare against an instar run that succeeds).
  Mitigation: the meta.json carries the exit code; phase 7's
  test logic skips baselines with non-zero exit.
- **Disk volume**: ~25 MB worst case. Existing
  `expected-outputs/` is already larger. Acceptable.
- **`generate-baselines.py` runtime**: ~30 min for the full
  matrix. Long enough to risk operator distraction. Mitigation:
  the script already logs per-version progress; let the
  operator decide whether to run it in a `tmux` / `nohup`
  session.
- **Missing safe-tier images**: if a manifest entry references
  a path that doesn't exist on the host, the script should
  skip with a warning. Verify in step 6a.
- **`detect-profiles.py` schema drift**: if the existing
  profile-generator hard-codes the `info`/`check` flavour, the
  measure-flavour run may need shape adjustments. 6b's brief
  calls this out.
- **`SIZE_CASES` drift over time**: as qemu-img adds new
  options worth measuring (e.g. extended_l2 + subclusters
  becomes more nuanced), the list grows. Acceptable; it's
  config-as-code in one place.

## Back brief

Before executing any step, the executing agent should
back-brief: which repo is being edited (instar-testdata vs
instar), which scripts are being modified, and which paths
are being written. The reviewer should verify nothing in the
instar repo changes except `CHANGELOG.md` (and maybe
`ARCHITECTURE.md`) in step 6e.
