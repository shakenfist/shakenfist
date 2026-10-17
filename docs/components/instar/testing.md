# Integration Test Suite

The instar project includes a Python-based integration test suite that verifies
`instar info` produces output identical to `qemu-img info`, that `instar check`
correctly detects structural corruption in QCOW2 images, that `instar compare`
produces byte-for-byte identical output to `qemu-img compare`, and that
`instar convert` produces output identical to `qemu-img convert`. Since instar
aims to be a drop-in replacement, any difference in output is considered a bug.

## Architecture

The test suite uses:

- **testtools** - Extended unittest framework with better assertions
- **testscenarios** - Parameterized test scenarios
- **stestr** - Parallel test runner with result storage

Tests compare instar output against either:
1. Live `qemu-img` output (for safe images - info, compare, convert)
2. Stored expected output files (for malicious images)

### Shared helpers and subprocess budgets

Helpers shared by more than one test module live on `InstarTestBase`
in `tests/base.py`, not copy-pasted into each class. In particular:

- `_run_tool(argv, cwd)` runs a reference qemu tool (`qemu-img`,
  `qemu-io`) with `cwd` inside a fixture directory and asserts it
  exited 0. It is the helper used to *build* fixtures and to produce
  qemu twins for differential comparison.
- `_require_qemu_img()` skips unless `qemu-img` is installed, and
  `_require_qemu_tools()` skips unless `qemu-io` is there too.
- `sha256(path)` digests a file's full contents.

**Require the narrowest guard the tests actually need.** The two are
not interchangeable, and the difference is not hypothetical: the `deb`
legs of the distro matrix install `qemu-utils`, which carries both
binaries, while the RPM legs install only `qemu-img` and never ask for
`qemu-io` at all (see `tools/test-package-functional.sh`). A class
gated on `_require_qemu_tools()` whose tests never run `qemu-io` is
therefore one packaging decision away from skipping everywhere on
half the matrix — and a skipped test is invisible in a green run.
`TestSnapshotErrorPaths` is the worked example: eighteen of its
nineteen tests only assert that instar refuses an image and leaves it
byte-identical, so the class takes `_require_qemu_img()` and the one
zstd test that needs a `qemu-io` write asks for it itself.

**A `_run_tool` timeout is a hang guard, not a performance
assertion.** The budget (`QEMU_TOOL_TIMEOUT`, 300s) exists so a wedged
`qemu-img` cannot block a stestr worker forever; it is not a claim
about how long qemu should take. The distro matrix fans several
4-worker suites out onto one self-hosted runner, so ~20 concurrent
qemu processes routinely stretch a five-second operation past a
minute. A tight budget there does not catch a regression — it
converts host contention into a merge queue ejection, which is
exactly what a 60s budget on one `qemu-img commit` did in
[#528](https://github.com/shakenfist/instar/issues/528). Pass a lower
`timeout=` only for a call whose duration is itself under test, and
never to "tighten up" a reference invocation.

This is distinct from the budgets on the `run_instar_*` helpers,
where a timeout *is* a real assertion: instar hanging is a product
bug, and those calls should stay bounded.

Note that the contention this budget exists for cannot be reproduced
by a pull request's own checks: the distro matrix runs only in the
merge queue (and on `workflow_dispatch`), so the first real signal
arrives at enqueue time. If a `_run_tool` `TimeoutExpired` recurs,
diagnose before raising the number. Read the failing leg's per-suite
wall clock and how many stestr workers were live alongside it; a
budget that was ample at ~20 concurrent qemu processes and blows at
~20 again is a contention change worth understanding, not a number
that wants doubling. Raising 300 to 600 reflexively buys one more
round of the same ejection.

## Test Categories

### Safe Images (`test_info_safe.py`)

Tests against known-safe disk images. These run `qemu-img` directly and
compare outputs character-for-character.

### Malicious Images (`test_info_malicious.py`)

Tests against images designed to exploit vulnerabilities (e.g., backing file
references to `/etc/passwd`). These use pre-stored expected output files
instead of running `qemu-img`, since running `qemu-img` on malicious images
defeats the security purpose of instar.

### Check Validation Tests (`test_check_formats.py`)

Tests for the `instar check` operation:
- **Format detection**: Verifies check correctly identifies QCOW2, VMDK, VHD formats
- **Per-format refusal**: `TestCheckVdiRefusal`, `TestCheckParallelsRefusal`,
  `TestCheckDmgRefusal`, and `TestCheckQedRefusal` verify `check` refuses each
  read-only-input format with the correct exit code and message
- **Corrupt images**: Tests against deliberately corrupt format headers (VMDK, VHDX, VHD)
- **QCOW2 structural validation**: Uses 4 script-generated corrupt QCOW2 images:
  - Clean baseline (should pass with 0 errors)
  - Overlapping clusters (two L2 entries pointing to same host cluster)
  - Refcount-zero (referenced cluster with refcount=0)
  - Leaked cluster (refcount>0 but no L2 reference)
- **Unsafe quirks mode**: Verifies non-QCOW2 formats are treated as raw with
  `--unsafe-quirks`

Corrupt QCOW2 test images are generated by
`instar-testdata/custom/check-validation/create-corrupt-images.py`, which creates
images with `qemu-img`/`qemu-io` and then surgically corrupts specific QCOW2
structures via binary manipulation.

### Repair Tests (`test_check_repair.py`)

Tests for `instar check --repair`, codifying the phase-7-verified
behaviour against the corrupt-fixture matrix (each on a tempdir
copy, never the committed fixture):

- **Leaks/all tiers repair to clean**: `leaked` + `--repair=leaks`,
  and `refcount-zero` / `refcount-too-high` / `stale-copied` +
  `--repair=all`, are asserted `qemu-img check`-clean afterward with
  the surviving data patterns still readable via `qemu-io read -P`.
- **Refuse paths stay byte-identical**: `corrupt-bit-set`,
  `snapshot-leak`, and `compressed-leak` are sha256-identical after
  repair, with the snapshot and compressed data intact.
- **Overlapping is a safe partial repair**: the genuine leak is
  reclaimed, the structural overlap remains, no new error classes
  appear, and instar exits 2 with `repair-incomplete`.
- **CLI**: `--repair`+`--chain` rejection, clean-image no-op,
  qcow2-only not-supported on a raw image, and idempotence.
- **Repaired counters** (`TestRepairCounters`): the per-class
  `repaired-leaks` / `repaired-refcounts` / `repaired-corruptions`
  counts reach the host over the `CheckResultMessage` protobuf and
  render in both the JSON and human `--repair` output, and are
  *omitted* on a read-only check so its schema is unchanged.

The structural tests assert post-repair *state* (`qemu-img
check`-clean, data reads, byte-identity) rather than instar's exit
code, since the exit code still reflects the detected corruption
after a clean repair; the repaired-counter output is asserted
separately by `TestRepairCounters` now that those counts travel on
the guest→host wire.

### Compare Tests (`test_compare.py`)

Tests for the `instar compare` operation, cross-validated against `qemu-img
compare`:

**Raw-vs-raw (`TestCompareRawIdentical`, `TestCompareRawDifferent`,
`TestCompareRawSizeMismatch`, `TestCompareRawJson`):**
- **Identical images**: Self-compare and two identical files
- **Different content**: Mismatch at offset 0 and at mid-file offsets
- **Size mismatch**: Non-strict (zeros = identical), non-strict (non-zero =
  differs), and strict mode (always fails on size difference)
- **JSON output**: Validates `identical`, `first-mismatch-offset`,
  `total-bytes-compared`, and `size-mismatch` fields

**QCOW2-vs-raw (`TestCompareQcow2VsRaw`):**
- Identical content across formats (including all-zeros)
- Different content reports correct mismatch offset
- Cross-validated against `qemu-img compare`

**QCOW2-vs-QCOW2 (`TestCompareQcow2VsQcow2`):**
- Identical and different content between two QCOW2 images
- Virtual size mismatch handling
- Cross-validated against `qemu-img compare`

**Compressed QCOW2 (`TestCompareQcow2Compressed`):**
- Compressed QCOW2 vs raw with same content (zlib decompression)
- Compressed vs uncompressed QCOW2 with same content
- Cross-validated against `qemu-img compare`

**Backing chains (`TestCompareBackingChain`):**
- QCOW2 overlay with raw backing file vs flattened raw (identical)
- QCOW2 overlay vs different raw (mismatch detected)
- Deep chain (3-level: top -> mid -> base) vs flattened raw (identical)
- Two different QCOW2 backing chains with same virtual content (identical)
- All scenarios cross-validated against `qemu-img compare`

Test images are created at runtime using `qemu-img create`, `qemu-io write`,
`qemu-img convert -c` (for compressed), and `qemu-img create -b` (for backing
chains), so no external testdata is needed.

**qemu-img cross-validation**: Every scenario verifies byte-for-byte
identical stdout and matching exit codes with `qemu-img compare`.

### Convert Tests (`test_convert.py`)

Tests for the `instar convert` operation (QCOW2 to raw), cross-validated
against `qemu-img convert`. Per-format convert matrices cover VDI
(`TestConvertVdiToRaw`), Parallels (`TestConvertParallelsToRaw`), QCOW1
(`TestConvertQcow1ToRaw`), and DMG (`TestConvertDmgToRaw`) input; QCOW1
also gets a dedicated smoke test module, `tests/test_qcow1_smoke.py`.

**Basic conversion (`TestConvertBasicQcow2ToRaw`):**
- Empty QCOW2 image to raw
- QCOW2 with written data to raw
- Output size matches virtual size
- All cross-validated against `qemu-img convert`

**Compressed QCOW2 (`TestConvertCompressed`):**
- Compressed QCOW2 to raw (zlib decompression)
- Compared against original raw source

**Backing chains (`TestConvertBackingChain`):**
- QCOW2 overlay with raw backing flattened to raw
- Deep chain (3-level: top -> mid -> base) flattened to raw
- Cross-validated against `qemu-img convert`

**Raw passthrough (`TestConvertRawToRaw`):**
- Raw to raw identity conversion

**Error handling (`TestConvertErrors`):**
- Unsupported output format rejected
- Nonexistent input file rejected

**Manifest images (`TestConvertManifestImages`):**
- Converts real-world QCOW2 images from the test manifest
- Cross-validates against `qemu-img convert` output
- Skips images with cluster_size > 64KB (unsupported)
- Skips images whose virtual_size exceeds available temp space

### Resize Tests (`test_resize.py`)

Tests for the `instar resize` operation, structured around six
surfaces totalling 114 tests:

**Schema-drift tripwire
(`TestResizeBaselineMatrix.test_resize_cases_match_baselines`):**
- Walks `instar-testdata/expected-outputs/resize-info-json/<target>/<version>/`
  and asserts the on-disk case set matches the in-test `RESIZE_CASES`
  mirror. Catches drift between this mirror and the testdata
  generator.

**Cross-version baseline matrix (`TestResizeBaselineMatrix`):**
- Per-`(target, case)` factory diffs `instar create → instar resize`
  output against the phase-10 `qemu-img info` JSON baseline.
- ~22 active cases for qcow2 + raw; ~20 skipped (vmdk/vhd/vhdx where
  qemu rejects, `-no-shrink` rejection cases that surface 6 covers,
  and `KNOWN_RESIZE_DIVERGENCES` carry-forwards).

**Live cross-validation (`TestResizeCrossValidation`):**
- 7 curated qcow2 + raw cases comparing instar end-to-end against
  the system `qemu-img` via `instar info` on both outputs.

**Round-trip check (`TestResizeRoundTripCheck`):**
- `instar create → resize → check` for ~30 non-raw cases.
- Catches resize-emitter regressions that produce files qemu-img
  info accepts but `instar check` flags.

**Internal consistency for vmdk/vpc/vhdx (`TestResizeConsistency`):**
- 14 cases for the formats qemu can't resize, verified via
  `instar info` virtual-size match + `instar check`.

**Targeted error paths (`TestResizeErrorPaths`):**
- 9 fixed tests pinning the host CLI rejection contracts:
  shrink-without-flag for raw + qcow2, subtractive-size without
  --shrink, invalid size strings, --preallocation=metadata on
  raw, --preallocation=falloc + --shrink, --object,
  --image-opts.

Wall clock: ~32 s serial / ~10 s with `make test-container`'s
`--concurrency 4`.

### Snapshot Tests (`test_snapshot.py`)

Tests for the `instar snapshot` subcommand (the snapshot work), 94 tests across five families:

- **List matrix (human)**: per baselined image, `TZ=UTC instar
  snapshot -l` byte-equals the host-resolved profile baseline
  from `instar-testdata/expected-outputs/snapshot-list-human/`
  (80 qemu-img versions captured; instar tracks the modern
  ≥9.0 layout); plus the bare-filename-defaults-to-list check.
- **List goldens (JSON)**: `--output=json` byte-equals the
  instar-side self-baselines in `tests/golden/snapshot-list/`,
  plus a structural vmstate cross-check and a QMP-key schema
  test.
- **Mutation round-trips**: create / delete / apply on tempdir
  copies with post-op `qemu-img check` clean, content verified
  via `qemu-img compare`, and structural behaviour on the
  name-collision / duplicate-name / cap-boundary fixtures.
- **Error paths**: qcow2-only enforcement across raw / vmdk /
  vhdx, feature-gate refusals (zstd, dirty bit, external data
  file, LUKS), not-found exit codes, `-U`+mutating refusal,
  `--image-opts` rejection.
- **Empty table**: empty stdout + exit 0; JSON emits `[]`.

The suite is the CI regression net. The **snapshot shell
harnesses** (`tools/snapshot-{create,delete,apply}-{matrix,
refusals}.sh` and `tools/snapshot-cli-parity.sh` — seven
scripts, 241 assertions) are the live differential layer:
byte-identity against the host `qemu-img` from identical
inputs, run with `make snapshot-harnesses` (requires a built
instar and `/dev/kvm`; CI runs it in the functional-tests
workflow's `snapshot-harnesses` job). They overlap by design.

### Adversarial Image Tests (`test_adversarial.py`)

Tests verifying that instar safely handles malicious and malformed images
without crashing, hanging, or consuming excessive resources. Uses the
`run_adversarial()` helper in `base.py` which enforces timeouts (hang
detection), memory limits via `RLIMIT_AS` (resource exhaustion), and signal
checks (crash detection).

**CVE-adjacent attacks:**
- **Compression bombs**: Zlib and ZSTD compressed QCOW2 images with extreme
  expansion ratios. Verifies decompression buffer bounds are enforced and
  output files stay small.
- **Circular backing chains**: 2-level cycle (A→B→A), 3-level cycle
  (A→B→C→A), and self-referencing (A→A). Verifies chain discovery detects
  the cycle and rejects it.
- **Deep backing chains**: Chains at 16 levels (device limit) and 17 levels
  (exceeds limit). Verifies depth enforcement and correct rejection.
- **Integer overflow**: L1 table size near u32::MAX, L1 size = 0, cluster_bits
  below minimum (8) and above maximum (22). Verifies checked arithmetic
  prevents undefined behavior.

**Boundary value cases:**
- **Refcount order edges**: refcount_order = 7 (128-bit, invalid) and 255
  (extreme value). Verifies clamping or rejection.
- **Oversized virtual size**: 1 petabyte and u64::MAX virtual sizes. Verifies
  info reports the size and check doesn't allocate based on virtual size alone.
- **VMDK grain size**: Zero and huge (2^63) grain sizes. Verifies checked_mul
  prevents division by zero and overflow.
- **VHDX conflicting headers**: Dual headers with different sequence numbers
  and valid CRC-32C checksums.
- **BAT beyond EOF**: VHD and VHDX images with BAT entries pointing past end
  of file. Verifies I/O error handling.

**Parent-locator attacks:**
- **Hostile parent references**: Six differencing VHDs in
  `custom/audit/vhd-diff-locator-*.vhd`, each structurally valid with correct
  footer and header checksums, a real BAT and real allocated blocks, and
  hostile only in the path string its parent unicode name and locator entries
  carry. `-etc-passwd` (absolute `/etc/passwd`) and `-dotdot`
  (`../../../etc/passwd`) verify a reader never opens or resolves the path,
  and never walks a relative one out of the child's directory. `-unc`
  (`\\attacker\share\probe`) and `-url`
  (`http://attacker.example/probe`) verify it never turns a locator into a
  network fetch. `-overlong` fills the whole 512-byte name field with no NUL
  terminator, verifying the reader stops at the field boundary instead of
  scanning past it. `-conflicting` populates all eight locator entries with
  mutually disagreeing parents, verifying the reader selects by platform code
  or refuses the table rather than silently picking one.

**Format confusion:**
- **Polyglot files**: QCOW2 magic header with VMDK descriptor body, and
  QCOW2 magic with ELF binary content. Verifies format detection works
  (magic wins) and structural validation catches inconsistencies.
- **Truncated headers**: QCOW2 v2 header cut at 32 bytes, VMDK with only 8
  bytes (magic + version), VHD footer at 48 bytes. Verifies all operations
  fail gracefully with no crash.
- **VMDK descriptor attacks**: Null bytes in descriptor, multiple extent
  declarations, and inflated 1MB descriptor size claim. Verifies parser
  handles adversarial text safely.

Test images are generated by scripts in `instar-testdata/scripts/` (the private
testdata repository). Scripts that generate adversarial or CVE-reproducer images
belong in `instar-testdata`, not in the public `instar` repository, with one
exception: `create-vhd-testdata.sh` also writes the six parent-locator fixtures
above, because they are differencing children of the chains that same script
generates and splitting them across two repositories would split one structure
pin across two. See [Test Image Generation](#test-image-generation) for the full
list of generators and where each writes. Any further exception needs the same
justification: the adversarial images share a generator with non-adversarial
ones that must stay byte-identical to them.

All generated images live in `instar-testdata/custom/audit/`.

### Security Tests (`test_security.py`)

Tests verifying instar's security properties:
- Backing file references are detected but not followed
- External data file references are reported but not read
- VMDK descriptor extent paths are not accessed

## Running Tests

### Make Targets

```bash
# Create Python virtual environment (first time only). The venv sees
# system site-packages so the libvhdi oracle (python3-libvhdi) is
# importable; an older isolated venv is deleted and rebuilt from
# tests/requirements.txt, so anything installed into it by hand is lost.
make test-venv

# Run safe tests (default, suitable for development)
make test

# Run CI-suitable tests
make test-ci

# Run all tests including malicious images (explicit opt-in)
make test-malicious

# Run with verbose output (useful for debugging diffs)
make test-report

# Clean test artifacts
make clean-tests
```

### Direct stestr Usage

```bash
cd tests
source .venv/bin/activate

# Run all tests
stestr run

# Run specific test module
stestr run test_info_safe

# Run with verbose output
stestr run --serial -- --verbose

# List available tests
stestr list
```

### Running against an installed package (distro matrix)

The make targets above run the suite against the in-tree build
(`src/target/release/instar`). The distro-matrix CI instead runs it
against the **installed `.deb`/`.rpm`**, inside a target-distro
container, using that distro's own `qemu-img` as the differential
oracle. `tools/test-package-functional.sh` is the per-distro runner:

```bash
# Full suite, against the .deb installed on Debian 12 (qemu-img 7.2.x)
tools/test-package-functional.sh src/target/debian/instar_*.deb debian:12

# Fast subset, against the .rpm installed on Rocky 9 (proves the dnf path)
tools/test-package-functional.sh --smoke \
    src/target/generate-rpm/instar-*.rpm rockylinux:9

# Dial concurrency down when several matrix containers share one KVM host
tools/test-package-functional.sh --concurrency 2 \
    src/target/debian/instar_*.deb ubuntu:22.04

# Replay one failure on one distro without paying for the whole suite
tools/test-package-functional.sh --select 'test_convert\.' \
    src/target/debian/instar_*.deb debian:13
```

To reproduce a **CI matrix entry** exactly as the merge queue runs it —
same package resolution, same summary line — use the wrapper CI calls
rather than the runner directly:

```bash
make package
PACKAGE_DIR=src/target/debian \
TESTDATA_PATH=../instar-testdata \
MATRIX_SELECT='test_map\.' \
    tools/ci/run-matrix-entry.sh 'Debian 12' debian:12 deb
```

`MATRIX_SELECT` exists for exactly this and is never set in CI: it
makes the run partial, and a partial run must not be able to report as
a green matrix entry. The wrapper prints a warning whenever it is set.

**The wrapper call above still reproduces a merge-queue entry exactly
— what it runs by default does not.** The matrix suite is now split in
two, and `run-matrix-entry.sh` reads `MATRIX_SET` to pick which half:
`queue` (the default, and what the command above runs — this is the
half that gates a merge), `nightly` (the six heavy VHD conversion
tests that do not), or `all` (the whole suite, both halves together,
which is what every matrix entry ran before the split). To reproduce
the nightly's selection on one distro instead:

```bash
PACKAGE_DIR=src/target/debian \
TESTDATA_PATH=../instar-testdata \
MATRIX_SET=nightly \
    tools/ci/run-matrix-entry.sh 'Debian 12' debian:12 deb
```

The nightly itself runs in its own workflow,
`.github/workflows/nightly-matrix.yml`, once a night across all seven
distros, plus one further Debian-13-only leg that runs the whole suite
(`MATRIX_SET=all`) to measure `tests/stestr-timing.txt`, since no other
job executes the whole suite in one stestr repository any more; a second
job with no tests in it publishes the refresh. See
"CI job layout and the partition guard" below for the two Makefile
targets that define the split and the guard that keeps the queue and
nightly selections exact complements of what a matrix entry used to
run.

Measured before this split: per-distro matrix-leg suite medians ranged
30 to 38 minutes, and every distro's own maximum exceeded the
*slowest* distro's median -- host contention moved a leg's duration
more than the distro did. That is the baseline a later reader should
compare this split's own measurement against, to check whether the
spread is still this narrow; a fresh measurement is being taken on
#543 and is not included here.

**Classify before you attribute.** Running two matrix containers at
once — or any other heavy job on the host — starves the suite, and the
resulting failures do not look like resource problems. The large
`test_convert` re-encodes fail under load with
`Error: "convert operation failed"` and `Content mismatch at offset 0!`,
which reads as data corruption, and they abort early (~26s) rather than
timing out at the ~110s they need when passing. Nine such failures
appeared across the 2026-08-09 matrix inventory and **every one passed
on an idle host**. A failure counts as a divergence only once it
reproduces with the host otherwise quiet; `--select` gives you that
replay cheaply. Same discipline as the differential-fuzzing
spurious-divergence rule.

The design is **tests from the source tree, binary from the package**:
the whole `tests/` tree is copied into the container and driven by
`stestr`, while the harness is pointed at the installed
`/usr/bin/instar` via `INSTAR_BINARY_PATH` (so the packaged binary and
its packaged guest binaries under `/usr/lib/instar/` are what runs).
The container runs as root (package + prerequisite install need it) and
the repo is mounted read-only, so no `.stestr/` artefacts leak into the
host worktree. `test_info_malicious` and `test_bench` are excluded from
the matrix run; `--smoke` restricts to a fast version/create/map/info
subset for local one-distro checks.

`TESTDATA_PATH` (default `../instar-testdata`) must already be git-LFS
materialised — in CI that is `tools/ci/prepare-testdata.sh`; the script
canary-checks for pointer files and refuses to run against them. This
runner is distinct from `tools/test-package-install.sh`, which stays the
fast packaging smoke check (file layout, `--help`, info/create/map).

Because this exercises instar against **older** qemu-img versions than
the dev host's, it is the first thing to surface output-format parity
gaps that single-version CI cannot: any command whose qemu-img output
changed across versions, where instar hard-codes the newest form, will
diverge here. See "qemu-img version profiles and the distro matrix"
below.

### Pull requests versus the merge queue

`functional-tests.yml` runs two different sets of jobs depending on the
event, and the split is deliberate:

| Event | Jobs |
|-------|------|
| `pull_request` | `ci-tooling`, `build-and-test`, `package-smoke`, the three `integration-*` jobs, `snapshot-harnesses`, `oslo-crossval-master`, `automated_reviewer`, `can_enqueue` — all against the **in-tree build** |
| `merge_group` | `build-and-test`, `package-build`, `package-matrix` (seven distros), `can_merge` |
| `workflow_dispatch` | everything except `ci-tooling` and `can_merge` — this is how you dry-run the matrix without enqueuing anything. Note `automated_reviewer` also skips here, because it needs `ci-tooling` and a skipped dependency skips its dependents; the two aggregates use `always()` precisely so they report anyway |

The merge queue does **not** re-run the PR integration jobs. That is a
coverage argument rather than a cost one: each matrix entry runs the
Python suite against the installed package on its own distro, against
that distro's `qemu-img`, so the queue's coverage is a superset of the
PR jobs on seven distros instead of one, with two exceptions — the
libvhdi differencing oracle, and the six heavy VHD conversion tests
that moved to a nightly cross-distro run instead of gating every
merge. See "What runs where, and why" at the top of
`functional-tests.yml` for both, and "Running against an installed
package" above for where the moved tests run and at what cadence.
`build-and-test` is the deliberate exception to the split itself — it
stays in both as the cheap fast-fail, and `package-build` depends on
it.

`package-build` builds one `.deb` and one `.rpm` and uploads them as a
single artifact that all seven entries consume. This works only because
the release binary is built on `debian:bullseye` (symbol floor
`GLIBC_2.30`), so one artifact set installs everywhere down to Rocky
9's glibc 2.34. **If a matrix entry fails while *installing* the
package, that is a glibc-floor regression in the build image, not a
test failure.**

**`package-smoke` is floor coverage, not distro coverage.** It is the
only packaging signal a pull request gets, because `package-build` and
the matrix run in the queue alone. It builds both packages and installs
each on the *oldest* glibc of its family in the matrix — the `.deb` on
`ubuntu:22.04` (2.35) and the `.rpm` on `rockylinux:9` (2.34) — so a
package that will not install anywhere fails before enqueue instead of
ejecting the pull request from the queue. That is the class both
recorded misses belonged to: #488 raised the binary's floor to
`GLIBC_2.39` via a base-image bump, and #501 bumped to a Rust nightly
that marks glibc version needs `WEAK`, so the `.rpm` required
`libc.so.6(GLIBC_2.18)[WEAK](64bit)` and no RPM distro could install
it. Both passed the pull request gate. Neither could have failed here
before: the `.rpm` was not built at all, and the `.deb` was installed
on `debian:trixie`, the *newest* glibc in the matrix, where a floor
regression cannot fail by construction. Keep both targets at the floor
if the matrix distro list changes; trixie is still covered by the
Debian 13 matrix entry.

**Runner size is a merge-queue latency decision, not a comfort one.**
The `xl` pool is shared across the whole Shaken Fist fleet and is the
scarce resource in the queue; the `develop` ruleset gives a merge group
`check_response_timeout_minutes: 360`, after which GitHub ejects the
pull request even though nothing failed. Merge run 33799289895 spent 75
minutes executing and 322 minutes queueing, finished green at 397, and
lost the pull request. So a merge-queue job belongs on `xl` only if it
actually needs a VM: `package-matrix` does, because each entry installs
a package and runs the suite against a distro's `qemu-img`, and
`package-build` does not, because it only builds. Putting a
non-VM job on `xl` does not merely waste a big machine, it inserts an
unbounded queue wait into the critical path.

Two things this does not fix, both still open: the `xl` ceiling itself
(seven matrix entries measured at no more than three concurrent), and
the fact that a timeout eject is silent -- a green run whose pull
request was dropped reports nothing, and the operator finds out by
noticing the queue is empty.

`can_enqueue` and `can_merge` are the two aggregate gates, and they are
the names configured as required checks — never the individual jobs or
matrix entries, whose names change whenever the distro list does.
`can_enqueue` aggregates the pull-request jobs ("may this PR enter the
queue?"); `can_merge` aggregates the merge-queue jobs ("is this merge
group good?"). Both use `always()` plus an event test and a jq
expression asserting every dependency ended `success` or `skipped`,
because a required check that never reports leaves the queue waiting
forever. See [development.md](/components/instar/development/) for the ruleset itself.

Each entry writes a row to the job summary naming the distro, its
**live** `qemu-img` version, and the test totals. The version is what
distinguishes "instar broke" from "instar diverges at this qemu version
boundary", so read it before attributing a red row.

**Flake quarantine.** Every matrix entry carries an explicit
`allow_failure: false`. An entry that fails twice consecutively for an
established environmental reason may be flipped to `true` with a linked
issue, so one flaky distro cannot block every merge; the flag comes off
when the issue closes. Note the sharp edge: `continue-on-error` makes
the job report *success* to the `needs` context, so a quarantined entry
stops gating merges entirely rather than merely tolerating its own
failure. Never leave a flag behind after its issue is fixed.

Because `merge_group` does not inherit the `pull_request` trigger's
`paths:` filter, this workflow always runs in the queue — including for
docs-only changes. That is deliberate (a required check that never runs
hangs the queue), at the cost of matrix latency on doc merges.

**Superseded merge groups are cancelled.** `build-and-test`,
`package-build` and `package-matrix` each carry a `concurrency:` block,
and its key is merge-group aware. `github.ref` is the obvious key and is
right on every other event, but on `merge_group` it is the per-attempt
queue branch `gh-readonly-queue/develop/pr-<N>-<SHA>`, and GitHub mints a
fresh SHA every time it rebuilds the group — which it does on every push
to `develop`. Keyed on that, every rebuild lands in a concurrency group
of its own, `cancel-in-progress` never matches, and superseded merge
groups run seven `xl` distro entries to completion on runners the whole
Shaken Fist fleet shares. So the key branches on the event and uses
`github.event.merge_group.base_ref` in the queue, with a `merge_group-`
prefix so a queue run does not share a group with a `workflow_dispatch`
run on `develop`.

Cancelling is only safe because the queue is serial: the develop ruleset
sets `max_entries_to_build: 1`, so any other in-flight `merge_group` run
is by definition superseded and its queue branch already abandoned.
Raising that setting and keeping this key would cancel a live queue
entry, which reports a failed required check and ejects the pull
request; the two have to move together. See
[shakenfist/kerbside#284](https://github.com/shakenfist/kerbside/issues/284)
for what the unfixed version cost across repositories, and the fleet
audit
[merge-group-cancellation](https://github.com/shakenfist/development/blob/main/audits/merge-group-cancellation.md).

### The nightly matrix's timing refresh and failure reporting

`nightly-matrix.yml` runs unattended, so everything it produces is
produced by a script: nobody opens the Actions tab on a morning when
nothing seems wrong. It fires at **22:00 UTC** — four hours ahead of the
other daily schedules, because the shared `xl` pool has a measured
ceiling of about three concurrent legs and this is the largest nightly
the repository runs — and holds two shapes of job:

| Job | Runs | Produces |
|-----|------|----------|
| `nightly-legs` (seven, one per distro) | the six moved VHD conversion tests (`MATRIX_SET=nightly`) | a GitHub issue per failing distro, and the leg's log as an artifact |
| `measure` (one, Debian 13) | the **whole** suite (`MATRIX_SET=all`), deliberately unseeded | the measured timings as a 30-day artifact, and an issue if the suite fails |
| `publish-timing` (one, no tests) | nothing — it downloads that artifact and compares it | a pull request refreshing `tests/stestr-timing.txt`, an issue if the refresh itself fails, and an issue if there was no measurement to refresh from |

`measure` is redundant as coverage — every test in it either still gates
a merge or runs on the seven legs above — and that is not why it is
there. It is the only job left in CI that executes the whole suite in a
single stestr repository, which makes it the only place the timing data
can be measured rather than guessed.

**Measuring and publishing are two jobs because of the token.** The
workflow level is `contents: read`, because every leg that runs the
suite runs instar and `qemu-img` over deliberately malicious images
outside the KVM sandbox, over a working tree the container can read.
`publish-timing` is the only job that holds a write scope
(`contents: write` for the branch it pushes, `pull-requests: write` for
the pull request it opens), it is the only checkout in the workflow that
keeps its job token, and it runs no test, installs no package and reads
no testdata — it downloads one derived text file and pushes. The
invariant to preserve when editing this workflow: **no job both runs the
suite and holds a write scope.** `issues: write` is the one exception,
on every leg, because a leg files its own failure issue and that scope
cannot push code or touch a pull request.

**A failed night publishes nothing, and says so.** `publish-timing`
checks that a measurement actually arrived and skips the publisher when
one did not, so a morning with a failure issue does not also get a
timing pull request nobody should read. It no longer skips the whole
job, though, and that distinction matters: the refresh's own failure
issue is filed from this job, so while a failed measurement skipped it
entirely, a measuring leg that failed night after night left the
committed timings quietly ageing with nothing naming that. The same hole
swallowed the quieter case — the derivation inside the measuring leg is
a warning rather than an error on purpose, so a leg could go green,
upload no timing artifact, and leave the publisher red at its download
step with no issue. Both now file one, under a key of their own (see
"Failure reporting" below).

**The timing-refresh pull request arrives with its checks not running.**
It is opened with the job's own `GITHUB_TOKEN`, so its author is
`github-actions[bot]`, and this repository sets the Actions
contributor-approval policy to `all_external_contributors`, which a bot
counts as. Measured on the `rust-nightly-bump` pull requests #584, #604,
#627 and #656, which are opened the same way: every `pull_request`
workflow run *is* created within seconds, and attempt 1 of each ends
`action_required`, waiting for an approval that never arrives on its
own — on all four a maintainer approved them hours later, attempt 2 ran
green, and the pull request merged through the queue normally. So the
commonly repeated version of this, that a `GITHUB_TOKEN`-created pull
request triggers no workflow runs at all, is not what happens here; the
runs exist and wait. The effect is the same either way, since `develop`
requires those checks. **To unblock it, use "Approve and run workflows"
on the pull request's checks tab.** Closing and reopening is the usual
advice for a pull request whose checks never fired and is probably not
the fix here, because the gate is keyed on who authored the pull
request; a PAT for an account with write access would avoid the gate
altogether, at the cost of a secret to rotate and to leave in reach of a
nightly job. The pull request body says all of this too, so whoever
finds it does not have to come here.

**The timing data and the four scripts that maintain it.**
`tests/stestr-timing.txt` is a committed list of `<test id><TAB><duration
seconds>` lines, sorted by id and rounded to 0.01s. It is a *scheduling
hint*: stestr partitions a run across workers from durations held in its
own repository, and a repository created fresh in every CI container
starts with none, so without it a test that takes minutes lands wherever
round-robin puts it and that worker becomes the run's wall time. A stale
hint degrades packing gradually and can never make a run wrong, which
sets the bias for the whole mechanism — stay quiet unless the numbers
actually moved.

- **`tools/ci/seed-stestr-timing.sh STESTR_BIN TIMING_FILE`** is the
  consumer. `tools/test-package-functional.sh` calls it from the
  directory `stestr run` is about to run in; it converts the committed
  text into a synthetic subunit v2 stream and `stestr load
  --force-init`s it, which is the only mechanism stestr offers for this
  short of a hand-written worker-assignment file. A missing or unusable
  timing file is **not** an error: the run proceeds with the scheduler
  partitioning blind, exactly as it did before the file existed.
- **`tools/ci/generate-stestr-timing.py REPO_DIR OUTPUT_FILE`** is the
  derivation, reading the durations out of a stestr repository's on-disk
  `times.dbm`. Text rather than a subunit stream, sorted and rounded, so
  a nightly refresh is a small diff instead of a fresh ~700 KB binary
  blob every night. Generating twice from the same repository must give
  byte-identical output.
- **`tools/ci/compare-stestr-timing.py OLD NEW`** decides whether a
  measurement is worth a human's attention, and exits 0 for material, 10
  for immaterial, 1 for an error. Both of its rules are expressed in each
  test's *share of the suite's weight*, because bin packing is scale-free
  — a contended or slower runner changes every number in the file without
  changing a single scheduling decision, and that is by far the commonest
  night-to-night difference. It publishes when one test's share moves by
  at least 2 percentage points, or when at least 10 points of the suite's
  weight relocate between tests. It is also where the measured test ids
  are validated, because this is the path that publishes them: an id
  must be a dotted `module.Class.method` with an optional parenthesised
  scenario suffix, drawn from a conservative charset, and anything else
  stops the refresh rather than being dropped. The ids come from the job
  that ran the suite over the malicious images, and they travel from
  here into a Markdown report, into the body of a pull request opened
  unattended, and into the committed file — so a backtick, a pipe, a
  code fence or an `@name` in one would be rendering as Markdown in a
  pull request nobody wrote. Failing closed is the opposite of the
  seeding path's behaviour above, and deliberately: this one writes the
  file, that one only reads it.
- **`tools/ci/publish-stestr-timing.sh MEASURED [--dry-run] [--force]`**
  acts on that verdict. On a material change it copies the measurement
  over the committed file, force-pushes the single long-lived
  `stestr-timing/refresh` branch, and opens a pull request — or, if one
  is already open for that branch, says so and leaves it to be updated
  in place rather than opening a second. It never pushes to `develop`: a
  scheduling hint is nowhere near a good enough reason to be the first
  thing in this repository that writes to a merge-queue-protected
  branch. On an immaterial change it prints the comparison and exits 0
  having done nothing, which is the normal case. It assigns the pull
  request to nobody unless `TIMING_PR_ASSIGNEE` names a login, and a
  failed assignment is a warning rather than a failed nightly: a
  hardcoded account that is renamed or loses access would turn a
  scheduling hint into a failure issue.

**Failure reporting.**
`tools/ci/report-nightly-matrix-failure.sh DISTRO IMAGE LOG_FILE` files
the issue, under the existing `test-failure` and `needs-attention`
labels so there is one queue of "CI found something and nobody has
looked" rather than two. The body names both the distro and the failing
tests, because that pair is the whole signal: one distro red and six
green is a `qemu-img` divergence at that distro's version, while all
seven red is an instar regression the pull-request jobs missed. It also
records the leg's live `qemu-img` version and the `Ran: N tests` totals,
and quotes a bounded excerpt anchored on stestr's failure block rather
than the end of the log — a matrix log ends with the totals and the
worker balance, so a plain tail says how long the run took and nothing
about why it failed.

Deduplication is **per distro**, keyed on a hidden `<!-- dedup_key: ...
-->` marker in the body rather than on the title alone, so a divergence
that takes a week to work through arrives as one issue with a comment
per night instead of seven issues. `--kind` switches to one of two
separate keys, because these are three different things with three
different places to look, and collapsing any of them into the others
sends a reader looking for a `qemu-img` difference that is not there:

- `--kind timing-refresh` is a bug in the publisher. The suite passed
  and the refresh broke, and the report opens by saying no test failed.
- `--kind timing-unmeasured` is no measurement to publish — the
  measuring leg did not succeed, or succeeded without deriving the file.
  Nothing is published from a run that fell over, which is right; this
  is the issue that stops the resulting staleness being invisible, and
  it collects a comment each night until a refresh lands again.

**Driving all of it by hand.** None of this needs CI:

```bash
# Re-derive the committed timing file from a local full run.
cd tests && ../tests/.venv/bin/stestr run --exclude-regex test_info_malicious
cd ..
tests/.venv/bin/python3 tools/ci/generate-stestr-timing.py \
    tests/.stestr tests/stestr-timing.txt

# Ask what the nightly would do with a measurement, touching neither
# git nor the GitHub API.
tools/ci/publish-stestr-timing.sh /path/to/measured-timing.txt --dry-run

# Ask what the reporter would file for a downloaded leg log.
tools/ci/report-nightly-matrix-failure.sh "Rocky 9" rockylinux:9 \
    matrix.log --dry-run
```

A manual `workflow_dispatch` of `nightly-matrix.yml` takes a
`force_timing_pr` input, which opens the refresh pull request even when
the comparison called the change immaterial; the scheduled run never
sets it. A dispatch runs in a concurrency group of its own, keyed on its
run id, so it cannot be the pending run that GitHub discards when the
22:00 schedule arrives — which also means a dispatch can run alongside
the schedule, and the two will share the `xl` pool rather than fit in
it. `publish-timing` carries its own group, so the branch push stays
serialised whatever the rest of the workflow does. The self-tests —
`tools/ci/test-seed-stestr-timing.sh`,
`test-generate-stestr-timing.sh`, `test-publish-stestr-timing.sh`,
`test-report-nightly-matrix-failure.sh` and
`tools/ci/test_compare_stestr_timing.py` — all run in the `ci-tooling`
job, and none of them needs docker, a venv or testdata beyond what they
build themselves.

### CI job layout and the partition guard

On a pull request the integration suite is split across several jobs
(in `.github/workflows/functional-tests.yml`) so the long-running
convert tests can run in parallel with everything else. Each job
selects a subset with stestr regex filters defined in the Makefile:

| Job | Make target | Selection |
|-----|-------------|-----------|
| `integration-core` | `test-container-core` | everything **except** `test_convert.`, `test_compare.`, `test_info_malicious.` (the catch-all) |
| `integration-convert-qcow2` | `test-container-convert-qcow2` | `test_convert.`/`test_compare.` minus `Vhd` |
| `integration-convert-vhd` | `test-container-convert-vhd` | `test_convert.TestConvert.*Vhd` |
| `oslo-crossval-master` | (inline) | `test_oslo_crossval` re-run against `oslo.utils` master |

`test_info_malicious.py` is intentionally excluded from every PR job
and runs only via `make test-malicious`.

Because `integration-core` is **exclude-based**, it silently absorbs
any new `test_*.py` module — convenient, but it means a future
refactor (e.g. turning it into an include-list like the convert jobs)
could drop a whole module from CI with no test failure. The
class-level convert split has a subtler gap: a `test_convert` class
containing `Vhd` but not matching `TestConvert.*Vhd` would be excluded
by the qcow2 job and missed by the vhd job.

The **`ci-tooling` CI job** guards against both. It runs
`tools/ci/check-test-partition.sh`, which enumerates the suite with
`stestr list`, reads the *actual* job selectors from the Makefile and
workflow (no duplicated copy to drift), and fails if any test is run
by zero jobs. The only hand-maintained input is the allowlist of
intentional exclusions in `tools/ci/check-test-partition.py`
(currently just the malicious suite); each entry carries a documented
reason. Run it locally with:

```bash
tools/ci/check-test-partition.sh
```

The same guard checks a **second partition**: the merge-queue distro
matrix and the nightly matrix, which between them must run everything
an unselected matrix entry used to run. It asserts the two halves cover
that baseline, that they do not overlap, that each half is actually
invoked by a workflow job, and that the two workflows' distro lists
agree entry for entry — those lists are deliberately copies, because
their entries carry comments and an audit marker a shared file could
not hold, so this check is the only thing keeping them in step.

Nothing there is restated in the guard. A matrix leg does not invoke a
Makefile target; it calls `tools/ci/run-matrix-entry.sh` with a
`MATRIX_SET` of `queue`, `nightly` or `all`, so the guard reads the
`MATRIX_SET` out of each workflow job (or its absence, which means that
script's own default), the set-to-target mapping out of the script, the
selector out of the named Makefile target, and the baseline exclusions
out of `tools/test-package-functional.sh`. A renamed target or a
typo'd `MATRIX_SET` is therefore a parse failure rather than a quietly
different selection.

One limit worth knowing when reading its output: `stestr list`
collapses a parameterised test to its bare id, so the figure it reports
is smaller than a run's (2378 against 3675 when last measured). Every
selector bar one is prefix-style, and a variant's id begins with its
bare id, so claiming the bare id claims every variant. The nightly's
selector is `$`-anchored instead, which means a future parameterised
variant of one of the six moved tests would stay on the merge gate
rather than move to the nightly — the safe direction.

Its own logic is unit-tested (stdlib only, no venv) via:

```bash
python3 -m unittest discover -s tools/ci -p 'test_*.py'
```

When you add a new integration job or change a test-container
selector, the guard validates the new partition automatically; if it
reports an orphan, either add the test to a job or (rarely) to the
documented allowlist.

## Test Image Manifest

Test images are defined in `tests/manifest.json`:

```json
{
    "id": "cirros-qcow2",
    "path": "downloaded/cirros/cirros-0.6.3-x86_64-disk.img",
    "format": "qcow2",
    "safety": "safe",
    "run_in_ci": true,
    "description": "CirrOS minimal cloud image",
    "tags": ["qcow2", "cloud-image"]
}
```

### Manifest Fields

| Field | Description |
|-------|-------------|
| `id` | Unique identifier for the test image |
| `path` | Path relative to testdata root |
| `format` | Expected disk format (qcow2, vmdk, vhd, etc.) |
| `safety` | `safe`, `caution`, or `malicious` |
| `run_in_ci` | Whether to include in CI test runs |
| `unsafe_quirks_required` | If true, requires `--unsafe-quirks` flag for qemu-img compatibility |
| `description` | Human-readable description |
| `tags` | Searchable tags for filtering |
| `expected_override` | Path to expected output file (for malicious images) |

### Unsafe Quirks Testing

Images marked with `unsafe_quirks_required: true` do not have valid format
headers or partition tables. In default (secure) mode, instar rejects these
files as "unknown format" rather than accepting them as raw images.

To test qemu-img compatibility for these images, use `--unsafe-quirks`:

```bash
# Default mode: rejects files without valid structure
instar info random-garbage.raw
# Error: Unknown format (no valid disk image header or partition table)

# Unsafe quirks mode: matches qemu-img behavior
instar info --unsafe-quirks random-garbage.raw
# file format: raw
```

See [configuration.md](/components/instar/configuration/) and [quirks.md](/components/instar/quirks/) for details
on safe vs unsafe quirks.

## Test Data Location

Test images are stored in a separate repository (`instar-testdata`) to keep
the main repository small. The location is resolved in order:

1. `INSTAR_TESTDATA_PATH` environment variable
2. `../instar-testdata` (sibling directory)

### Binary fixtures are git-LFS backed

Every binary fixture in `instar-testdata` (`*.qcow2`, `*.raw`, `*.vhd`,
`*.vhdx`, `*.vmdk`, `*-backing`, the bundled `qemu-img` binaries) is tracked
with **git-LFS**. A checkout whose LFS objects have not been materialised
leaves each fixture on disk as a ~131-byte pointer file, and the whole suite
then fails with `file format: unknown` for every image — a failure that
looks like a mass instar regression but is really a testdata problem. If you
clone `instar-testdata` by hand, run `git lfs pull` after cloning; a working
tree with real images (the fixture starts with its true magic, e.g. `QFI\xfb`
for qcow2) rather than pointer text is required.

CI does this through **`tools/ci/prepare-testdata.sh`** — the single,
LFS-aware testdata-prep helper shared by `functional-tests.yml`,
`coverage-fuzz.yml`, and `test-drift-fix.yml`. It clones or updates the
cached checkout, runs `git lfs pull`, and then **guards** against the pointer
failure mode: it inspects known canary fixtures and hard-fails the job with
an explicit "infrastructure problem, not a test regression" message if any is
still a pointer, so a git-LFS outage can never be silently misattributed to
instar (see GitHub issue #451 for the incident this guards against).

Which of its two branches ran is part of its output, because the two leave the
tree in materially different states and nothing else can tell them apart by
looking. Beside `TESTDATA_PATH` it exports:

| Variable | Meaning |
|---|---|
| `TESTDATA_SOURCE` | `clone` (fresh), `cache` (the fleet's testdata disk, updated in place) or `unverified-cache` (some other tree already at the cache path, updated in place). A fresh clone writes every image out fully allocated, so the whole tree needs resparsifying; the disk needs only what the update changed. That narrower treatment assumes the tree was already sparse, which the script cannot check, so it is given only when `TESTDATA_REQUIRE_CACHE=1` has asserted the tree is the fleet's pre-built disk. Any other cache — a long-lived one on a static runner, say — is `unverified-cache` and resparsified whole. |
| `TESTDATA_CHANGED_FILES` | For `cache` only: a file listing the paths the update changed, one per line, for the narrow resparsify above. Derived here because the reset that changed them happens here — by the time a later step runs there is nothing left to diff. |

The list has three sources, and the last two are not obvious. Besides the
diff between the two trees it carries every path whose index stat data was
stale before the update, and every file that was still an LFS pointer before
`git lfs pull`. Resparsifying is a `cp` and a `mv`, so the disk build's own
resparsify leaves the index disagreeing with the files it rewrote, and
`git reset --hard` then rewrites those entries even though their content is
identical — measured, with the clean-stat control alongside it, in
`tools/ci/test-testdata-changed-files.sh`. Under LFS they come back from the
smudge filter fully allocated, and no diff of the two trees names them, so a
list built from the diff alone would skip exactly the files the reset had just
re-expanded. Including them costs nothing extra: the reset already wrote them
into the overlay. The script emits a `::warning::` naming the count when it
finds any, because the real fix belongs to whatever built the cache — a
`git update-index --refresh` after its resparsify — and this is the only place
the cost is visible. The warning carries two counts, and the first is the one
to read: `git diff-files` reports every *tracked* path whose stat data
differs, images or not, and instar-testdata tracks around 770,000 of them
against a few thousand images. A total near the number of tracked files in the
tree means something other than a missed refresh — the index stat data did not
survive the trip from the disk build to the runner at all (a different uid, or
inode numbers that changed on the way), so the narrow resparsify has quietly
become a whole-tree one and the disk is saving nothing. That needs fixing in
how the disk is built or mounted; a refresh at build time will not help. The
second count, the in-scope images among them, is what the resparsify actually
pays for.

The pointers are the same kind of gap. `git lfs pull` writes each pointer out
fully allocated and then refreshes the index for it, so nothing afterwards —
not the tree diff, not a second `git diff-files` — names it; the script lists
them with `git lfs ls-files` beforehand instead, and warns, because a disk that
ships pointers was built without pulling them.

Both are printed as well as written to `$GITHUB_ENV`, and running the script
by hand with `$GITHUB_ENV` unset is fine.

`TESTDATA_REQUIRE_CACHE=1` turns a missing cache into an infra error instead
of a 7.46 GiB fallback clone, in the same idiom as the pointer guard: a job
that silently did the slow thing merely looks slow, which is the failure mode
that goes unnoticed for weeks. The assertion names the contractual mount path
(`/srv/ci/cached/instar-testdata`) literally rather than taking it from
`TESTDATA_CACHE`, so a change to either side of that contract fails there
instead of quietly following a defaulted path. `TESTDATA_CONTRACT_CACHE`
relocates it, and exists only so `tools/ci/test-prepare-testdata.sh` can
exercise both sides of the guard on a machine with no such disk; it is a
different variable from `TESTDATA_CACHE` on purpose, so the two cannot be
pointed at the same wrong place, and nothing under `.github/` sets it. What
it detects is a **path mismatch** — the disk mounted somewhere else, or a
runner advertising the disk's label without the disk. It cannot be a failed
mount: that stops the fleet's userdata before the runner registers, so such a
job stays queued and never runs this script at all.

### The `instar` runner label

Nine jobs prepare testdata. Two different eights are drawn from them below,
and they are not the same eight: the set that asks for the disk here, and the
set that resparsifies afterwards in the next section. Each lists its members,
and `coverage-fuzz` and `test-and-fix` are what separate them —
`coverage-fuzz` asks for the disk but does not resparsify, and `test-and-fix`
resparsifies but cannot ask for the disk.

All of them except `test-and-fix` ask the fleet for that disk with an extra
`runs-on` label:

```yaml
runs-on: [self-hosted, debian-13, xl, instar]
```

`instar` means "a runner carrying the pre-seeded instar-testdata dependencies
disk". The conductor matches labels as a set, so the order does not matter,
and it works with any size the conductor knows — but the arrangement is
verified for `debian-13` only, because the disk is owned by the uid `debian`
has on that image and a runner whose user has another uid fails at boot
rather than running the job. The label must also be listed under
`self-hosted-runner.labels` in `.github/actionlint.yaml`, or actionlint (which
runs in pre-commit) rejects the `runs-on` before it can be committed.

Those same eight jobs — call them the **label set** — also set
`TESTDATA_REQUIRE_CACHE: '1'` on their prepare step. A job that asked for the
disk and did not get it must say so, rather than quietly falling back to the
clone the label exists to avoid. They are `package-matrix`,
`integration-core`, `integration-convert-qcow2`, `integration-convert-vhd`
and `oslo-crossval-master` in `functional-tests.yml`; `nightly-legs` and
`measure` in `nightly-matrix.yml`; and `coverage-fuzz` in
`coverage-fuzz.yml`.

Nothing enforces that correspondence automatically. It was checked in both
directions when the label was introduced, by parsing the finished YAML rather
than reading the diff, and it is re-checked by hand when a job is added — but
a new consumer that forgets the label pays for a 7.46 GiB clone it did not
need, and a labelled non-consumer holds a scarce disk-bearing runner, and
neither shows up as anything but a slow job. Issue #674 tracks turning it into
a guard in `ci-tooling`.

The ninth testdata consumer, `test-and-fix` in `test-drift-fix.yml`, carries
neither, deliberately and permanently. It runs on `[self-hosted, claude-code]`
— the *static* runner fleet, which the conductor only watches and never
attaches a disk to, so no runner there will ever advertise `instar` and a
labelled job would queue until GitHub cancelled it 24 hours later. It keeps
cloning fresh. The reason is recorded next to that job as well as here,
because a reader about to tidy the inconsistency away is looking at the
workflow.

### Resparsifying

A different eight of those nine jobs — the **resparsify set**, which is the
label set with `coverage-fuzz` taken out and `test-and-fix` put in — then
resparsify the tree. They are every job that runs the Python integration
suite over it: five in `functional-tests.yml`, two in `nightly-matrix.yml`,
and `test-and-fix` in `test-drift-fix.yml`.

Seven of them call **`tools/ci/resparsify-prepared-testdata.sh`**, which
takes no arguments: it reads the `TESTDATA_SOURCE` and
`TESTDATA_CHANGED_FILES` above and calls `tools/ci/resparsify-testdata.sh` in
whichever of its two forms the tree needs — the whole tree for a fresh clone
or an `unverified-cache`, and only the paths the update changed for the
fleet's disk updated in place. An unset or unrecognised `TESTDATA_SOURCE` is
an error there rather than a fall back to the whole tree, because the
fallback is the expensive branch and taking it silently would cost the most
on exactly the runner that was supposed to save.

`test-and-fix` is the eighth and calls `resparsify-testdata.sh` with its one
positional argument instead, deliberately. That workflow runs from the
default branch while the scripts it invokes come from the pull request's own
checkout, so the two can be of different ages: a branch cut before the
wrapper existed would fail on a missing file, and one cut before
`prepare-testdata.sh` exported `TESTDATA_SOURCE` would have the wrapper
refuse for want of it — breaking other people's pull requests, not this
repository's own gates. The one-argument form is a cross-repository contract
and exists on every branch. The job also loses nothing by it: on the static
`claude-code` fleet it never meets the disk, so it always wants the whole
tree resparsified anyway. The exception is
`coverage-fuzz.yml`, which prepares the tree only to seed fuzz corpora and
never asks `qemu-img info` for an allocated size, so the fully allocated
tree costs it disk and nothing else. git and git-lfs write each file
out fully allocated, so a clone or an `lfs pull` fills the images' sparse
holes with real zeros: the tree becomes about 7.5 GiB of mostly zeros, and
`qemu-img info` reports an allocated size that belongs to git rather than to
the fixture, which the disk_size comparisons check. The script restores the
holes with `cp --sparse=always`, over `downloaded/` only and over the five
whole-disk image extensions only — `custom/` fixtures keep the allocation
their tests expect. It is a shared script rather than a step repeated in each
workflow because a mirrored copy's only symptom when it drifts is a slower
job.

That script is also a **cross-repository interface**: private-ci's
instar-testdata dependencies-disk image build fetches it from instar's default
branch over HTTPS and runs it against its own clone, outside GitHub Actions.
So its one-argument call means "the whole tree" permanently, it reads no
`GITHUB_*` variable, and it exits non-zero on failure so that a broken fetch
fails their build rather than quietly shipping an unsparsified image.
`tools/ci/test-resparsify-testdata.sh` exercises exactly that calling
convention, and measures allocated blocks before and after rather than
trusting the exit status.

Anything narrower therefore arrives as **further optional arguments** after
the tree path:

```
resparsify-testdata.sh <tree> [--files-from <list>]... [--] [<file>...]
```

The caller that needs this is a job whose tree came off a pre-built testdata
dependencies disk: that tree is a copy-on-write overlay of an image which was
resparsified when it was built, so rewriting all of it would write gigabytes
into the overlay the disk exists to avoid — while skipping it altogether would
leave the day's drift, which the job's own fetch and `lfs pull` land fully
allocated. Only what the update changed needs it. Paths may be tree-relative
or absolute inside the tree, and the two ways in differ on purpose:

- `--files-from <list>` takes a newline-separated **candidate** list — a
  changed-file list, which legitimately holds non-images, `custom/` fixtures
  and paths the update deleted. Entries that are not in-scope images that
  exist are dropped and counted, so the caller does not need a second copy of
  the extension list to drift from. (`--list-extensions` publishes that list
  for a caller that does want to filter.)
- Trailing positional paths are **named deliberately**, so anything the script
  will not act on is refused instead.

A path that resolves outside the tree is refused in both forms, and a refusal
resparsifies nothing at all. An empty narrow set is success, unlike the
whole-tree form: a cached tree whose update changed no image is the normal
case, not a misconfiguration.

That refusal is deliberate and its blast radius is worth knowing before you
meet it. The `--files-from` list on the cache path comes from git, not from an
operator, so a single instar-testdata commit adding a symlink under
`downloaded/` with an image extension and a target outside the checkout would
fail the resparsify step on **all eight labelled jobs at once**, and keep
failing until that commit is fixed. Fleet-wide CI failure with
`resparsify-testdata: refusing …: resolves outside …` in it is a testdata
commit to look at, not an instar regression; the refusal message names the
list file the entry came from for that reason. Refusing is still the right
behaviour — the alternative is writing outside the tree — but it is the one
failure here that is nobody's pull request.

Everything else in a list is filtered rather than refused, and the filtering
is done without resolving each entry: a candidate that is relative, free of
`..`, and either outside `downloaded/` or not an image extension is dropped on
the string alone. That matters because the list is not bounded by the number
of images — its stat-dirty component names every tracked path, so a cache
whose index did not survive the handoff names all ~770,000 — and a process per
entry would turn the step into a job timeout, hiding the warning that
diagnoses it. `tools/ci/test-resparsify-testdata.sh` counts the resolutions
rather than timing the run.

## Expected Output Overrides

For malicious images where running `qemu-img` would be dangerous, store the
expected output in `tests/expected_outputs/`:

```
tests/expected_outputs/
└── qcow2_backing_etc_passwd.txt
```

Reference this file in the manifest:

```json
{
    "id": "qcow2-backing-passwd",
    "expected_override": "expected_outputs/qcow2_backing_etc_passwd.txt"
}
```

## Image Notes

The `docs/image_notes/` directory documents which test images exposed
specific quirks or implementation details. When a test image reveals
unexpected qemu-img behavior that requires compatibility work, create a
markdown file documenting:

- The specific values that revealed the behavior
- How qemu-img handles the case
- How instar now handles it
- Links to relevant quirks documentation

See [Image Notes](/components/instar/image_notes/README/) for existing documentation.

## Adding New Test Images

1. Add the image to `instar-testdata` repository
2. Add entry to `tests/manifest.json`
3. For safe images: add scenario to `test_info_safe.py`
4. For malicious images:
   - Create expected output file in `tests/expected_outputs/`
   - Add scenario to `test_info_malicious.py`
5. If the image exposes new quirks: create `docs/image_notes/<image-id>.md`

## Output Comparison

The test suite performs exact string comparison. On failure, it shows:

- Unified diff with whitespace made visible
- `␣` for trailing spaces
- `→` for tabs
- `↵` for trailing newlines
- Raw repr() of both outputs for debugging

Before comparing, the harness normalises the fields that legitimately
vary between runs rather than between qemu versions:

- **Disk size / actual-size** is substituted from the live filesystem
  (`st_blocks * 512`, `helpers/comparators.get_disk_size`), because it
  reflects the current file's allocation, not a format decision. instar
  computes it the same way, so it always matches.
- **vmdk `cid`/`parent-cid`, the dirty flag, and vhdx log-size** are
  stripped by `assert_info_equivalent` — a vmdk content-ID is a random
  nonce written at creation, not a stable output.

This matters for the version matrix: two adjacent baseline profiles
often differ *only* in these normalised fields (see below), so a profile
mismatch is invisible to the assertions until a genuinely version-gated
field changes.

## qemu-img version profiles and the distro matrix

instar emulates the `qemu-img` build installed on the host so its output
is byte-identical to the real tool. Two mechanisms cooperate:

**Runtime model (`src/vmm/src/version.rs`).** instar runs
`qemu-img --version`, parses `major.minor.patch`, and derives just two
booleans: `include_child_node` (qemu ≥ 8.0 adds the `Child node '/file'`
section) and `include_dirty_flag` (qemu ≥ 6.1 exposes the dirty flag).
If qemu-img is absent it falls back to the newest profile. `--qemu-version`
overrides detection.

**Baseline profiles (instar-testdata).** Because qemu changed some output
*within* stable series, `expected-outputs/<command>/version-map.json`
records several empirical profiles (e.g. `profile-6-1-0`, `profile-7-2-19`,
`profile-8-0-0`, `profile-8-1-0`, `profile-10-0-0`, `profile-10-2-0`) and a
`version_to_profile` map from full version strings to profile names.

**Selecting a profile for the host qemu.** `base.py`
`get_profile_for_installed_qemu` / `_pick_baseline_version_dir` resolve
the host version against the map with **full major.minor.patch matching**
(`_select_version_match`): exact match, else the highest enumerated
version ≤ the host within the same `major.minor`, else the highest
version ≤ the host overall. A `{major}.{minor}.` *prefix* match (the
previous behaviour) could not tell `7.2.0` (profile-6-1-0) from `7.2.22`
(profile-7-2-19) and mis-selected on any 7.2.19+ host such as Debian 12.
The single-qemu-version CI never exposed this; the distro matrix does.

**Portable vs version-specific tests.** `test_info_safe` and the
`--qemu-version` baseline suites (create/resize/amend/commit/bitmap)
drive instar with an explicit version per profile and compare to the
stored baseline — they are qemu-version-independent and pass identically
on every distro. The live-oracle suites (`test_convert`, `test_compare`,
`test_dd`, `test_check_*`, `test_map`, `test_measure`,
`test_oslo_crossval`) use the host's real qemu-img as the differential
oracle and pick their comparison profile from the detected version, so
they are the ones the matrix actually exercises.

**The CI matrix (`tools/probe-qemu-versions.sh`).** The `qemu-img`
version each matrix distro ships, and the profile it selects:

| Distro | `qemu-img --version` | Profile |
|--------|----------------------|---------|
| Debian 12 (bookworm) | 7.2.22 | `profile-7-2-19` |
| Debian 13 (trixie)   | 10.0.11 | `profile-10-0-0` |
| Ubuntu 22.04 (jammy) | 6.2.0  | `profile-6-1-0` |
| Ubuntu 24.04 (noble) | 8.2.2  | `profile-8-0-0` |
| Fedora latest        | 10.2.2 | `profile-10-2-0` |
| Rocky/RHEL 9         | 10.1.0 | `profile-10-0-0` |
| Rocky/RHEL 10        | 10.1.0 | `profile-10-0-0` |

Run `tools/probe-qemu-versions.sh` to refresh this table (it pulls each
image and prints its `qemu-img --version`). Rocky 10 is pulled from the
`rockylinux/rockylinux` org repo — the Docker Official `rockylinux`
library stops at 9. Regression tests pinning the parser and the selection
rule against these exact strings live in `tests/test_version_detection.py`
and `src/vmm/src/version.rs`.

**Profile selection is not the whole story.** Debian 12's 7.2.22 is the
only distro whose `major.minor` (7.2) transitions profile mid-series (at
7.2.19), so it is the only one the full-version fix re-points, and the
profile-6-1-0 vs profile-7-2-19 baselines differ *only* in normalised
fields (disk size, vmdk cid). Running the live-oracle suites against
every distro's real qemu-img (via
`tools/test-package-functional.sh`) then found divergences the baseline
comparison never could — see below.

**Known divergences (found 2026-08-09, fixed 2026-08-10).**
instar used to hard-code its newest-qemu behaviour for three things, so
it diverged on every distro shipping an older qemu. All three are now
version-gated:

| Divergence | Affected distros | Boundary | Status |
|------------|------------------|----------|--------|
| `map --output=json` emitted `compressed` unconditionally | Debian 12 (7.2.22), Ubuntu 22.04 (6.2.0) — 19 tests each | **8.2.0** (absent at 8.1.5) | Fixed: gated on `include_map_compressed` |
| `map --output=json` emitted `present` unconditionally | none in the matrix (oldest is 6.2.0) | **6.1.0** (absent at 6.0.1) | Fixed: gated on `include_map_present` |
| `snapshot -l` used the newer column layout unconditionally | Debian 12, Ubuntu 22.04, Ubuntu 24.04 (8.2.2) | **9.0.0** (8.2.2 is old-form) | Fixed: gated on `snapshot_underscored_columns` |
| Every VHD instar wrote declared a size its CHS geometry did not cover, so qemu < 10.0 read it short and truncated the tail | Debian 12, Ubuntu 22.04, Ubuntu 24.04, RHEL 9 | reader default changed at **10.0.0** | Fixed: the writer now stamps creator app `qem2` |

The third was not an output-format divergence at all but silent data
loss in the VHD writer, found because the qcow1→vpc test compares a
flattened round-trip. See `docs/quirks.md` ("VHD Virtual Size
Calculation") and the plan.

One divergence remains documented rather than fixed: instar applies the
qemu 10.0+ VHD size rule to VHDs with an *unrecognised* creator app,
where a pre-10.0 qemu-img would use the CHS product. It needs an image
whose creator app is outside the known table and whose CHS disagrees
with its disk_size, and no such image exists in the corpus. The rule is
evaluated guest-side, so gating it would mean widening the guest ABI.

**How the boundaries were measured.** instar-testdata ships 80 static
per-version `qemu-img` builds at `qemu-img-binaries/x86_64/<version>/`,
covering 6.0.0 to 10.2.0. Run the real binary rather than reasoning
from the version map or from qemu source — two of the three boundaries
above were initially recorded wrongly from indirect evidence:

```bash
TD=../instar-testdata
$TD/qemu-img-binaries/x86_64/8.1.5/qemu-img map --output=json image.qcow2
$TD/qemu-img-binaries/x86_64/9.0.0/qemu-img snapshot -l image.qcow2
```

Both `map` and `snapshot` accept `--qemu-version` (as `info` already
did), so both sides of every boundary are exercisable on the dev host
rather than only inside the matrix.

**Cross-profile baseline tests.** The ordinary baseline tests compare
only against the profile matching the *installed* qemu-img, so a
single-version host checks exactly one side of every boundary — which
is how the `compressed` divergence survived to the distro matrix.
`TestMapCrossProfile` drives `--qemu-version` for every profile the
version map declares and compares against that profile's own
baselines, so a new boundary is caught wherever the suite runs. It
immediately found one the matrix could not: `map --output=json` gained
`present` in 6.1.0, and no matrix distro is old enough to notice.

**qemu capability is not the same as qemu version.** Distro qemu builds
do not all carry the same block drivers, and the version-profile model
says nothing about this. RHEL-family `qemu-kvm` (Rocky/RHEL 9 and 10) is
built **without** the `qed`, `qcow` (qcow1), `parallels`, `dmg`, `bochs`
and `cloop` drivers that Debian's `qemu-utils` carries — so on those
distros qemu-img cannot act as the differential oracle for those formats
at all. Tests that need such an oracle call
`skip_unless_qemu_supports(fmt)` (`tests/base.py`), which probes the
driver by opening a nonexistent file with an explicit format and looking
for `Unknown driver`; tests that exercise instar alone (the check-refusal
and adversarial suites) keep running there. Detect capability this way
rather than from `qemu-img --help`, whose "Supported formats:" line qemu
10.x no longer prints.

**A truncated run must never look green.** subunit v2 refuses to write a
packet larger than ~4MB, so a failing assertion that renders a
multi-megabyte image buffer raises `ValueError: Length too long` and
kills the stestr worker. Every test still queued on that worker silently
never runs, and stestr exits 0 if nothing else failed — a partial run
that reports as a pass (one Rocky run "passed" having executed 454 of
3253 tests). Two defences: compare image buffers with
`assert_bytes_identical` (`tests/base.py`), which reports sizes and the
first differing offset rather than the buffers, and
`tools/test-package-functional.sh`, which fails the run outright on the
crash marker, on any worker reporting `N/A` elapsed time, or on a run
that executed fewer tests than `stestr list` found for the same
selection. That floor is derived per selection rather than being a
written-down total, so it covers the merge queue's half of the suite and
the nightly's six tests as well as an unselected full run; a constant
could only ever check the last of those, and drifted every time a test
was added.

**Nor must a run where everything skipped.** A skip reports a result, so
it counts towards stestr's `Ran:` total, which means the floor above is
satisfied by a selection in which every test skipped exactly as by one
in which every test executed. That is reachable: every test in the
nightly's six calls `skipTest` when its VHD fixture is missing, so one
absent file would give a green leg that tested nothing. So
`tools/test-package-functional.sh` also fails when `Ran:` minus
`Skipped:` is zero, and prints the executed count on every run. The rule
is "nothing executed at all" rather than a minimum fraction executed,
deliberately: skips are a property of the distro rather than of the
change — the EL streams with no `qemu-storage-daemon` skip the whole
bitmap oracle set, and the full suite legitimately skips hundreds — so
any fraction tight enough to catch a real loss on Debian would fail
Rocky on a good night. One consequence worth knowing before it surprises
you: a hand-run `--select` whose every case skips on this distro now
fails rather than passing.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `INSTAR_TESTDATA_PATH` | Override default testdata location |
| `INSTAR_BINARY_PATH` | Override default instar binary location |

## Differential Fuzzing

The project includes a differential fuzzer (`scripts/differential-fuzz.py`)
that compares instar against qemu-img on randomly generated images. It is
part of the security audit plan (`PLAN-audit.md`).

### How it works

For each iteration the fuzzer:

1. Picks a random seed (logged for reproducibility).
2. Generates a random disk image with `qemu-img create`, varying format
   (qcow2, raw, vmdk, vpc), virtual size (1M-1G), cluster size,
   compression, and data patterns (zeros, random, sparse, MBR).
3. Creates separate copies for instar and qemu-img.
4. Runs a random chain of 2-4 operations (info, check, convert,
   compressed convert, create, measure, resize, rebase, commit,
   map, snapshot) against both tools.
5. Compares outputs at each stage: exit codes, normalized JSON info
   output, and converted file content (SHA-256 of raw-flattened output).

The `op_snapshot` arm (the snapshot work) generates a
fresh qcow2, then applies an identical random chain of
`snapshot -c` / `-d` / `-a` and `qemu-io` write elements to the
instar and qemu-img copies, asserting **byte-identity of the
whole image after every chain element** (the qemu side runs with
`file.discard=ignore`; dates and dead padding bytes are
normalized per docs/quirks.md). Its first runs surfaced a real
multibyte list-padding bug and delete's missing surviving-L2
COPIED refresh, both since fixed.

The `op_repair` arm (the check-repair work) builds a fresh
qcow2 with known data, injects one random qcow2 corruption
(refcount-zero / too-high / leaked / stale-copied / overlapping),
forks the corrupt file to two byte-identical copies, and repairs
one with `instar check --repair` and the other with `qemu-img check
-r` at a random tier. A three-tier oracle asserts: **safety**
(unconditional — instar must never produce check-errors or raise
the corruption/leak count above the original, even when it
refuses); **convergence** (`all` tier only, where the two tools
have matching scope — when instar claims a complete repair via
`repair-incomplete == false`, the image must be `qemu-img
check`-clean); and **data equivalence** (when both reach clean, the
raw-flattened guest data must match). instar's deliberate
refuse/partial behaviour is recorded as conservative, never a
divergence; the leaks tier is excluded from convergence because it
is intentionally narrower than `qemu-img -r leaks` (see
[quirks.md](/components/instar/quirks/#check---repairleaks-scope-vs-qemu-img-check--r-leaks)).

The `op_commit`, `op_rebase` and `op_bench` arms draw a snapshot flag
(40% probability, the qcow2-write infrastructure work) and, when
`qemu-io` is present, build a **snapshot-bearing** fixture that
exercises the copy-on-write paths opened — commit over a
backing- or overlay-snapshot span, safe rebase of a snapshot-bearing
overlay, and `bench -w` over a snapshot-shared span. For those fixtures
the oracle gains the phase-7 read-back triple: active-view `qemu-img
compare` identical + `qemu-img check` clean (with a `refcount=1
reference=2` scan) + per-carrier snapshot read-back `instar == qemu
twin` (via `tests/helpers/snapshot_readback.py`), the last of which the
active-view compare alone cannot see. Non-snapshot fixtures keep their
existing oracle. This folds in and retires the standalone
`scripts/cow-soak.py` soak; a 300-iteration local soak ran
0 divergences.

Known quirks (see [quirks.md](/components/instar/quirks/)) are excluded from comparison:
disk size fields and format-specific metadata.

### libyal cross-validation

When libyal tools are installed in the environment (`libvmdk-utils`,
`libvhdi-utils`, `libqcow-utils`), the fuzzer adds two additional
comparison layers:

1. **Info cross-check**: Parsed fields from `vmdkinfo`, `vhdiinfo`,
   and `qcowinfo` (virtual size, format version, cluster size, etc.)
   are compared against instar's JSON output for the same image.
2. **Parse-success consistency**: For each format, if the libyal tool
   successfully parses the image, instar check should report no errors
   (and vice versa). Disagreements are flagged as divergences.

This closes the gap where VMDK/VHD/VHDX had no differential reference
for `check` validation (qemu-img check only supports QCOW2), and
provides a third independent opinion for QCOW2. libyal tools are
optional here — the fuzzer degrades gracefully when they are
unavailable. That is not true of every use of libvhdi in this suite;
see the differencing oracle cross-check below, which CI treats as
mandatory.

### Running locally

```bash
python3 scripts/differential-fuzz.py \
    --instar src/target/release/instar \
    --iterations 100 \
    --seed 42 \
    --log-dir ./fuzz-logs
```

### CI integration

The fuzzer runs automatically via `.github/workflows/differential-fuzz.yml`
at three tiers:

| Trigger | Iterations | When |
|---------|-----------|------|
| `pull_request` | 100 | PR changes fuzzer script or workflow |
| `push` to develop | 200 | Post-merge smoke test |
| `schedule` | 1000 | Nightly at 02:00 UTC |
| `workflow_dispatch` | configurable | Manual trigger |

On failure, the workflow uploads logs as artifacts and auto-files GitHub
Issues with the `security-audit` label, including the seed, iteration,
image attributes, and a reproduction command.

### Reproducing a divergence

Each divergence report includes the seed and iteration number. To
reproduce:

```bash
python3 scripts/differential-fuzz.py \
    --instar src/target/release/instar \
    --iterations <ITERATION + 1> \
    --seed <SEED> \
    --fail-fast
```

## Coverage-guided fuzzing

Coverage-guided fuzzing uses `cargo-fuzz` (libFuzzer) to exercise the
`no_std` parser crates directly, without the full VMM/KVM stack. A
mock `CallTable` backed by fuzzer input provides the I/O layer,
allowing libFuzzer to explore malformed input space that differential
differential fuzzing cannot reach.

### Fuzz targets

43 targets across the parser and planner crates, organized in
`src/fuzz/`:

| Target | Crate | Type |
|--------|-------|------|
| `fuzz_format_detect` | shared | Buffer-based |
| `fuzz_qcow2_header` | qcow2 | Buffer-based |
| `fuzz_qcow2_l1l2` | qcow2 | CallTable |
| `fuzz_qcow2_refcount` | qcow2 | CallTable |
| `fuzz_qcow2_decompress` | qcow2 | CallTable |
| `fuzz_vmdk_header` | vmdk | Buffer-based |
| `fuzz_vmdk_grain` | vmdk | CallTable |
| `fuzz_vhd_footer` | vhd | Buffer-based |
| `fuzz_vhd_bat` | vhd | CallTable |
| `fuzz_vhd_parent` | vhd | Buffer-based |
| `fuzz_vhdx_header` | vhdx | Buffer-based |
| `fuzz_vhdx_metadata` | vhdx | CallTable |
| `fuzz_vhdx_parent` | vhdx | Buffer-based |
| `fuzz_raw_partition` | raw | Buffer-based |
| `fuzz_luks_header` | luks | Buffer-based |
| `fuzz_vdi_header` | vdi | Buffer-based |
| `fuzz_vdi_bat` | vdi | CallTable |
| `fuzz_parallels_header` | parallels | Buffer-based |
| `fuzz_parallels_bat` | parallels | CallTable |
| `fuzz_qcow1_header` | qcow1 | Buffer-based |
| `fuzz_qcow1_table` | qcow1 | CallTable |
| `fuzz_dmg_table` | dmg | CallTable |
| `fuzz_dmg_chunk` | dmg | CallTable |
| `fuzz_measure_calc` | measure | Buffer-based |
| `fuzz_measure_scan` | all parsers | CallTable |
| `fuzz_create_emitters` | create | Buffer-based |
| `fuzz_resize_planners` | resize | Buffer-based |
| `fuzz_rebase_planners` | rebase | Buffer-based |
| `fuzz_commit_planners` | commit | Buffer-based |
| `fuzz_amend_planners` | amend | Buffer-based |
| `fuzz_map_iter` | all parsers | CallTable |
| `fuzz_snapshot_parse` | qcow2 | CallTable |
| `fuzz_snapshot_refcount` | snapshot | Buffer-based |
| `fuzz_check_repair` | check | Buffer-based |
| `fuzz_dd_window` | dd | Buffer-based |
| `fuzz_chs_rounded_size` | dd | Buffer-based |
| `fuzz_dd_read` | dd | CallTable |
| `fuzz_bitmap_parse` | qcow2 | CallTable |
| `fuzz_bitmap_planners` | bitmap | Buffer-based |
| `fuzz_bench_schedule` | bench | Buffer-based |
| `fuzz_qcow2_write` | qcow2-write | Sim harness |
| `fuzz_qcow2_write_growth` | qcow2-write | Buffer-based |
| `fuzz_chain_compose` | qcow2 | CallTable (multi-device) |

**Buffer-based** targets call parser functions that take `&[u8]`
directly (e.g. `QcowHeader::parse(data)`). **CallTable** targets
use the mock CallTable from `src/fuzz/src/lib.rs` to simulate
sector-based I/O from the fuzzer input. **Sim harness** targets
(only `fuzz_qcow2_write`) drive a crate's own Vec-backed simulation
harness — here `crates/qcow2-write`'s `sim` module, feature-gated
`#[cfg(any(test, feature = "sim"))]` and OFF in the production build.

The two parent-locator targets cover the differencing-image read path
that an ordinary non-differencing VHD/VHDX never reaches, and that no
other target reached either. `fuzz_vhd_parent` drives
`VhdParentInfo::parse` over the eight-entry `VhdParentLocatorTable`,
`locator_defect`'s placement rules (rejecting defective, unused or
non-Windows entries), `preferred_locator`'s precedence and ambiguity
logic (a disagreeing duplicate must resolve to ambiguous, never to a
winner), and `decode_name`'s UTF-16BE decode. `fuzz_vhdx_parent`
drives `parse_parent_locator` directly against raw item bytes —
libFuzzer cannot synthesise a valid region table, a metadata table,
and an in-item offset past the 64 KB floor by chance, so the item
parser has to be fuzzed on its own rather than reached through
`parse_metadata` — and asserts that every offset and length reachable
from a returned locator lies inside the item it came from (checked
by index, not by trusting the parser's own arithmetic), that the
locator type comes from the item's GUID rather than a field the
parser set, that a declined key stays distinguishable from an absent
one, and that linkage comparison is case-insensitive.

Both targets exist because a measurement, not a guess, showed the
gap: before this phase `VhdParentInfo::parse`,
`VhdParentLocatorTable::parse`, `VhdParentLocator::parse`,
`locator_defect`, `preferred_locator`, `decode_name`,
`parse_parent_locator` and its nine helpers were all at 0.00% region
coverage, while the targets that were already running covered
unrelated code in the same crates well — `VhdState::init` at 89.26%
and `VhdFooter::parse` at 97.96% for VHD, `parse_metadata` at 33.91%
and `read_header` at 94.44% for VHDX. After adding the targets, the
VHD functions measured 86.67%-100% and the VHDX functions
75%-100% (`value_of` and `linkage_matches` are the two below 90%).
These targets assert invariants — a defective locator never winning
selection, a disagreeing duplicate never resolving to a winner, no
offset escaping its bounds — rather than only the absence of a
panic.

The two snapshot targets (the snapshot work) cover the
streaming snapshot-table parser (`fuzz_snapshot_parse` drives
`for_each_snapshot_entry` and the planner converter against
adversarial qcow2 fragments) and the mutator primitives
(`fuzz_snapshot_refcount` dispatches one of seven ops per exec —
refcount increment/decrement/swap, the COPIED-flag walker, the
contiguous allocator, the flag helpers, and the
table-serialisation round-trip — asserting semantic invariants
such as precheck-never-mutates, inc/dec byte-identity, and
allocator containment).

`fuzz_check_repair` (the check-repair work) dispatches one
of four `crates/check` repair planners per exec — leak
reclamation, count accumulation, refcount correction, and COPIED
reconciliation — asserting sub-byte-masked containment (co-resident
refcount entries in a shared byte are preserved at widths 1/2/4),
raised/lowered/freed tally correctness, the overflow→`AmbiguousCorruption`
and bounds→`MisalignedAccess` error classifications, idempotence,
and the "correct generalises reclaim" cross-check. The COPIED
walker's deep invariants are delegated to `fuzz_snapshot_refcount`.

The two `crates/qcow2-write` targets (the qcow2-write infrastructure work) fuzz the write planner rather than a
parser. `fuzz_qcow2_write` decodes a fixture archetype (clean /
backing-present / shared-data / shared-L2 nested / owned-L2 /
zero-flag-target) at a cluster size {512, 4 KiB, 64 KiB, 2 MiB} and
runs a bounded `plan_write` / `plan_flush` sequence through the `sim`
harness, asserting the copy-on-write invariant oracle after every
operation: `max_rc < 3` (the COW corruption signature — the single
most important invariant), snapshot-shared clusters byte-preserved and
never freed, no dangling / past-EOF L1/L2 pointer, and — after a flush
— `OFLAG_COPIED` set iff refcount is exactly 1. A `WriteError` refusal
is a valid outcome, not a crash. `fuzz_qcow2_write_growth` feeds
geometry to the `growth` module's `plan_refcount_growth`, asserting no
overflow, the self-coverage invariant, and cap adherence. Both are
registered in the fast tier (`tools/ci/fuzz-tier.sh`); their bring-up
shake-out found no planner bug. (Note: `plan_refcount_growth` has a
debug-only non-convergence `debug_assert!` guard that fires only in
debug builds on out-of-envelope petabyte-scale geometry; in release it
returns `GrowthOverflow` gracefully, and production only passes bounded
geometry — softening that guard to always return overflow is a recorded
follow-up, not a bug.)

`fuzz_chain_compose` is the only multi-device target, and the only one
that reaches `read_chain_virtual_cluster` — the backing-chain compose
path. Every other CallTable target serves one input device, so the
chain walk (the loop over devices, the descent into the device behind
one that does not hold a cluster, and the zero-fill at the bottom of a
chain) was unreachable rather than merely unexplored:
`fuzz_qcow2_l1l2`, which covers `cluster_lookup` at 93.67%, measured
0.00% region coverage on `read_chain_virtual_cluster`.

It opts the mock CallTable into a multi-device mode with
`instar_fuzz::set_fuzz_devices(&[child, parent])`, which installs one
buffer per device index. The mode is opt-in, and a target that never
calls it sees one input device backed by the single `set_fuzz_input`
buffer exactly as before — the default every other target depends on.
`set_fuzz_input` turns the mode back off, so a target using both calls
them in that order.

The target builds a `ChainConfig` as a Rust struct rather than
decoding one from bytes (`init_chain_states` and
`read_chain_virtual_cluster` both take `&ChainConfig` directly, so the
serialised form the host writes to guest memory is not on the path
under test), varies the declared segmentation across the three shapes
`init_chain_states` distinguishes — one chain of two, two chains of
one, and a segmentation that tiles nothing — and asserts four
invariants rather than the absence of a panic:

1. A read that reports success filled the whole buffer. Checked by
   reading the same span twice from two different sentinel prefills
   and requiring the results to agree: a byte the reader never wrote
   keeps whichever sentinel it started from, which no single sentinel
   could prove because any byte value is one an image could
   legitimately contain.
2. `init_chain_states` reporting success never leaves a device the
   reader needs state for uninitialised.
3. A device with no state refuses rather than serving its own zeros.
   This is the shape of a differencing child composed against a parent
   that was never resolved, which exits 0 with the parent's data
   replaced by zeros.
4. A device behind the child never overrides a byte the child holds,
   checked by comparing the composed read against the same span read
   as the child alone.

### Running locally

```bash
# Inside the instar-build container:
cd src/fuzz

# Run a single target for 60 seconds
cargo fuzz run fuzz_qcow2_header -- -max_total_time=60

# Run with specific corpus
cargo fuzz run fuzz_qcow2_header corpus/fuzz_qcow2_header/

# Minimize a crash
cargo fuzz tmin fuzz_qcow2_header artifacts/fuzz_qcow2_header/<crash>

# Generate coverage report
cargo fuzz coverage fuzz_qcow2_header
```

Per-function coverage — which functions a target actually reached, not
just a total — comes from `make fuzz-coverage`, which runs the same
coverage build inside the devcontainer and renders the report with
`llvm-cov`:

```bash
make fuzz-coverage FUZZ_TARGET=fuzz_vhd_parent
make fuzz-coverage FUZZ_TARGET=fuzz_vhd_parent FUZZ_COVERAGE_FILTER=locator
```

A filter narrows the report to matching function names across every
crate; `FUZZ_COVERAGE_SOURCES` narrows the files. This exists because a
target that runs clean while reaching none of the code it claims to fuzz
looks, from the outside, exactly like a target that found no bug. It
does not use `cargo cov` to render: cargo-fuzz 0.12 passes
`--no-default-features` to a clap parser that rejects it and panics.

### Corpus seeding

The seed corpus is extracted from `instar-testdata` using:

```bash
python3 scripts/extract-fuzz-corpus.py --testdata /path/to/instar-testdata
```

This copies test images into per-target corpus directories under
`src/fuzz/corpus/`, filtered by format. Header-only targets receive
truncated copies. Hand-crafted minimal valid inputs are also generated
for each format.

It additionally **restores the accumulated corpus** pushed by prior
nightly runs. Each nightly run pushes coverage-increasing inputs to
`instar-testdata/custom/fuzz-corpus/<target>/`; on the next run those
entries are copied straight back into `corpus/<target>/` *by target
name* (`restore_pushed_corpus()`), so coverage compounds across runs.
This matters most for the targets whose inputs are not recognizable
image formats — the window-math, CHS-geometry and planner fuzzers —
which would otherwise re-seed cold every night because format-based
routing cannot place their entries. Entries are content-addressed, so
restoration is idempotent.

### CI integration

The CI workflow (`.github/workflows/coverage-fuzz.yml`) runs:
- **Nightly** at 04:00 UTC, all targets, with **tiered per-target
  durations** (see below).
- **PR validation:** single-target smoke test when fuzz/parser code
  changes.
- **Post-merge** (push to `develop`): 15s per target.
- **Manual dispatch:** configurable duration and target selection.

#### Tiered nightly durations

The nightly run has a fixed wall-clock budget (450 min, inside the
480 min job timeout). Rather than splitting it evenly, the run plan is
computed by `tools/ci/fuzz-tier.sh`: the fast-saturating targets (pure
window math, CHS rounding, and the planner/emitter crates) take a short
fixed slice (300s — they reach steady coverage in well under a minute),
and the deep parser/format targets split the remainder. With the
current 43 targets (17 fast, 26 deep) that gives the 26 deep targets
~14 min each versus ~10 min under an even split (27000 / 43).

This is one of two levers for keeping per-target time useful as the
target count grows; the other is corpus persistence (see *Corpus
seeding*). **When the deep-tier share computed by `fuzz-tier.sh` falls
to the fast-tier floor (~300s), stop cutting time and shard the targets
across multiple CI jobs instead.** Sharding only adds real throughput
if the self-hosted runner pool has spare physical cores during the
nightly window, since each libFuzzer target pins a core — confirm core
availability before adding jobs.

#### Keeping the registration lists honest

Adding a target means editing several places by hand, and the
`TARGETS=(...)` array in `coverage-fuzz.yml` is the one that fails
silently: a target missing from it is never fuzzed in CI at all, while
its row in the table above keeps claiming it is.
`tools/ci/check-fuzz-targets.sh` fails the `ci-tooling` job unless the
`.rs` basenames, the `[[bin]]` names in `src/fuzz/Cargo.toml` and that
array name exactly the same set. `FAST_TIER` in `fuzz-tier.sh` is
checked as a subset rather than for equality, since an unlisted target
legitimately defaults to the deep tier. The guard needs no docker, no
cargo and no testdata, and `tools/ci/test-check-fuzz-targets.sh` proves
it still fails when one list loses an entry.

#### Crash reporting

Crashes are minimized with `cargo fuzz tmin` and filed as GitHub
Issues with the `security-audit` label immediately when found, by
`tools/ci/report-fuzz-crash.sh`. New corpus entries are pushed to
`instar-testdata/custom/fuzz-corpus/` after nightly runs, and restored
by target name on the next run so coverage compounds.

**The corpus push must stay cheap, and it is the step that decides
whether a night counted.** `tools/ci/push-fuzz-corpus.sh` adds files to
a repository it never wants to read: instar-testdata is ~12 GB with
LFS-backed fixtures, and `custom/fuzz-corpus/` alone is ~2.7 GB across
~560,000 entries. A plain `git clone` of that, followed by a per-file
walk of both trees, measured **22 minutes** — against the 30 minutes of
headroom `NIGHTLY_BUDGET_SECONDS` (450 min) leaves under the job's
480-minute timeout, which also has to cover setup. Five of six
scheduled runs in August were killed mid-push and threw away the corpus
they had just spent seven hours building (#519). The script therefore
clones with `--filter=blob:none --sparse` and reads the committed entry
names out of `git ls-tree`, so no corpus blob is ever fetched, and
copies only the entries `comm` says are new. If you change it, keep
two invariants: the index must stay fully populated (a `--no-checkout`
clone would make the commit *delete* every fixture in the repository),
and staging must use `git add --sparse`, because the corpus paths lie
outside the checked-out cone and plain `git add` refuses them. Both are
asserted by `tools/ci/test-push-fuzz-corpus.sh`, which runs against
local repositories in the `CI tooling guards` job. The step also
carries its own 20-minute `timeout-minutes`, so a future regression
fails loudly instead of eating the job.

These rules make that reporting safe, most of them learned from a month
of silently broken nightlies:

- **The log excerpt is bounded in bytes, and never travels on a command
  line.** `cargo fuzz` prints the failing input as a `std::fmt::Debug`
  dump on a *single* line, so with `-max_len=4194304` one crash can
  produce a 370KB log line. The excerpt is therefore clipped per line
  (`cut -b`), then per line count, then per byte, scrubbed to valid
  UTF-8, and handed to `jq` with `--rawfile`. The original code passed a
  `tail -30` through `jq --arg`, and an 81KB crash input in
  `fuzz_rebase_planners` made that a 371KB argv entry — over Linux's
  128KiB `MAX_ARG_STRLEN`, so `jq` exited with "Argument list too long".
- **A reporting failure is never fatal to the run.** The fuzz step runs
  under `bash -e`, so that `jq` failure aborted the whole step at the
  first crash: 21 of the 40 targets were never fuzzed, no issue was
  filed, and the corpus push was skipped — every night from 2026-07-16
  to 2026-08-11. Reporting failures are now counted and warned about
  per target, and the loop continues to the remaining targets. The
  failure is raised by a final `Fail on unreported crashes` step that
  runs with `if: always()` *after* the corpus push and the log upload,
  so an unreportable crash still turns the run red without costing the
  night's corpus — and without hiding the artifacts that are the only
  remaining way for that crash to reach a human.
- **Anything read out of a fuzz log is treated as hostile bytes.** Both
  the excerpt and the signature are NUL-stripped, length-bounded and
  re-encoded with `iconv -c`, because a panic message can itself carry
  fuzz data and `jq` — like the GitHub API — rejects invalid UTF-8. Log
  scraping uses `grep -a`: without it `grep` decides a log containing
  raw mutated bytes is binary, prints nothing, and every such crash
  silently degrades to a signature of `unknown crash`.
- **The excerpt is windowed on the crash, not on the end of the log.**
  A real `cargo fuzz run` failure prints the panic, then ~30 stack
  frames, the `SUMMARY`, the artifact path, the Debug dump and
  cargo-fuzz's reproduction block. In the 379KB log that motivated this
  change the panic is line 30 of 91, so a `tail -n 30` window starts at
  line 62 and contains no panic line and no panic message at all. The
  window is five lines before the first `panicked at`/`SUMMARY:` and 25
  after, trimmed from the *end* so the panic survives the byte cap; with
  no such line to anchor on — a build failure, a truncated log — it
  falls back to the tail.
- **The same crash is not refiled every night.** Since the run no longer
  stops at the first crash, a recurring crash would otherwise file one
  issue per target per night, and the `security-audit` queue is worked
  through by hand. An open `security-audit` issue whose title matches
  the target *and* whose body carries the same `dedup_key` gets a
  comment instead of a duplicate. A lookup that fails falls through to
  filing, because a duplicate issue is a much smaller problem than an
  unreported crash; `--no-dedup` forces that behaviour by hand.

The signature is the first `panicked at`/`SUMMARY:` line *and the line
after it*: Rust prints the location and the message separately, and
`panicked at fuzz_rebase_planners.rs:278:17` on its own does not
identify a crash.

Dedup matches on `dedup_key` rather than on that signature, because a
panic message interpolates the fuzz-derived values that provoked it —
the real crash behind this change reads `Write patch 0
(72057594037927944..72057594037928200) exceeds total_file_size
(281076066929798)`. Two inputs hitting one assertion produce two
different signatures, so exact matching would file a fresh issue every
night for a single bug. The key is the location plus the
message with standalone numbers collapsed to `N`. Digits *inside*
identifiers are left alone, so `qcow2` and `qcow3` do not merge: a
duplicate issue is only noise, whereas two different bugs sharing one
issue loses a crash. For the same reason the `file:line:col` is never
normalized — two assertion sites in one file stay two issues — but the
thread id in `thread '<unnamed>' (47) panicked at …` is dropped, since
it varies run to run.

Not every fuzz failure is a Rust panic. An OOM, a timeout or a deadly
signal anchors on libFuzzer's `SUMMARY:` line instead, and the line
after *that* is the `MS:` mutation line, which ends in a per-input
`base unit: <hex>`. Hex survives digit collapsing, so for a
SUMMARY-anchored failure the key is the `SUMMARY:` line alone;
including the `MS:` line would give every recurring OOM a new key and a
new issue every night. Issues filed before `dedup_key` existed are
still matched on their `signature`.

Run the reporter by hand against a downloaded `coverage-fuzz-logs`
artifact to check what an issue would say, without filing anything:

```bash
tools/ci/report-fuzz-crash.sh fuzz_rebase_planners \
    src/fuzz/artifacts/fuzz_rebase_planners/crash-<hash> \
    coverage-fuzz-logs/fuzz_rebase_planners.log --dry-run
```

`tools/ci/test-report-fuzz-crash.sh` exercises the reporter against
synthetic logs — the 370KB single line, raw mutated bytes, a missing
log, a missing crash file, and the dedup decisions with a stubbed `gh`
— and asserts the emitted body still carries the fields a triager
needs first. Its fixture reproduces the *layout* of a real libFuzzer
log, not just its content, because an earlier ten-line fixture let a
tail-anchored excerpt look correct while on a real log it captured 28
stack frames and no panic.

Both suites run on pull requests in the `ci-tooling` job, and need only
bash and `jq`:

```bash
tools/ci/test-report-fuzz-crash.sh
tools/ci/test-pick-fuzz-artifact.sh
```

#### Choosing which artifact to report

`tools/ci/pick-fuzz-artifact.sh` decides which file in
`src/fuzz/artifacts/<target>/` is the reproducer. It used to be inline
YAML in the workflow, which is where two of the three bugs behind the
broken month were hiding, so it is a script with tests now.

Do not pass `-max_len` to `cargo fuzz tmin`: it supplies its own, and a
second one trips libFuzzer's `assert(MaxInputLen == 0)`, so
minimization fails and leaves a 0-byte `minimized-from-*` artifact
behind — which an unsorted `find | head -1` would then happily report as
the reproducer for a later crash. The picker therefore skips
`minimized-from-*` when choosing what to minimize, and afterwards is
asked separately for the non-empty `minimized-from-*` that `tmin`
produced; without that second step the issue would quote the size of,
and the reproducer point at, the original large artifact, and
minimization would cost CI time without improving anything.

The empty-file filter applies *only* to `minimized-from-*`, where an
empty file means `tmin` failed. A 0-byte `crash-*` is a real crash —
libFuzzer writes one when a target panics on the empty input — and
filtering it out would send the workflow down its no-artifact branch,
filing nothing and telling the reader to go and check the build.

Artifacts are taken in an explicit order of preference — `crash-`,
`oom-`, `leak-`, `timeout-`, `slow-unit-`, then anything else — rather
than by sorting the names. Lexicographic order happens to put crashes
first, but it also puts `slow-unit-` ahead of `timeout-`, and libFuzzer
writes a `slow-unit-` file for any input over 10s (its default
`-report_slow_units`). Since these targets run against 4MB inputs, a
slow unit can easily be sitting in the directory when a real timeout
arrives, and the reported reproducer would then be the wrong file.

### Fixing fuzzer findings

Fuzzer crashes and differential divergences become `security-audit`
GitHub issues automatically (`tools/ci/report-fuzz-crash.sh`, called
from `coverage-fuzz.yml` and `differential-fuzz.yml`). They are fixed
by hand in interactive sessions. A workflow once attempted the fix
itself; it was retired after an audit found its safety boundary
unsound. See `docs/plans/PLAN-fuzz-autofix.md` for the history
and the reasoning.

## Differencing oracle cross-check

`tests/test_differencing.py`'s `TestDifferencingLibvhdiOracle`
runs `vhdiinfo` against a differencing VHD or VHDX child that `instar
create` wrote through the real CLI. It exists because `qemu-img`
cannot serve as the oracle for this output — it reads a differencing
image as though the parent were absent, surfacing no parent at all —
so `vhdiinfo` is the only external cross-check of the parent linkage
instar wrote, and this suite is the only place anything other than
instar's own parser reads a differencing image instar wrote.

The assertions are structural only: the disk type, the parent
filename for VHD, and the parent identifier for both formats,
each compared against what `vhdiinfo` reports for the parent itself
rather than a hardcoded GUID. No composed content is read by this
class — that is the data oracle's job, below. One limitation is
recorded rather than worked around: `vhdiinfo` prints no `Parent
filename` line for a VHDX, with or without `-v`, so the VHDX parent
locator's path key cannot be checked through this tool — the Rust
round-trip test pins it instead
(`src/crates/create/tests/round_trip.rs`).

The suite runs inside the `instar-build` devcontainer, which installs
`libvhdi-utils`, so locally these tests skip when `vhdiinfo` is absent
(`_require_vhdiinfo` in `tests/base.py`), the same bargain
`_require_qemu_img` strikes. In CI that same absence is a failure
instead: `INSTAR_REQUIRE_LIBVHDI=1` is set on the integration job's
test step and passed through by the `Makefile` to the container,
because a skipped test and a passing test look identical in a green
run and `stestr` does not name skipped tests in its output — parsing
the log could not have caught it. Removing `libvhdi-utils` from the
devcontainer turns that job red instead of leaving it quietly green.

### The data oracle: composed content, not just structure

`vhdiinfo` has no way to answer "what does this chain compose to" —
`libvhdi-utils` ships exactly two binaries, `vhdiinfo` and
`vhdimount`, a FUSE mount that is a worse thing to put in an
integration suite's path than a Python import. The `pyvhdi` binding
(package `python3-libvhdi`, installed in
`src/.devcontainer/Dockerfile` beside `libvhdi-utils` because both
come from the same upstream release) can: `pyvhdi.file.set_parent()`
stacks a parent behind a child so the pair reads as one image, making
libvhdi a second, independent reader of composed *bytes*.
`tests/base.py`'s `_require_pyvhdi` is modelled on
`_require_vhdiinfo` and honours the same `INSTAR_REQUIRE_LIBVHDI`
variable rather than a second one, so there is still one switch and
one place to look.

Two test classes in `tests/test_differencing.py` use it:

- **`TestDifferencingOracleComposes`** drives every real chain the
  project has — two formats, two and three images deep — through
  `set_parent` and compares the result, byte for byte, against both
  the recorded `*-composed.raw` golden and `instar convert -O raw`,
  three independent readers of the same bytes. `set_parent` chains
  cleanly to three levels, in either order, and still refuses a chain
  given only in part, so the depth-three chains add no new exception
  beyond the one below.
- **`TestDifferencingCreatedChildComposes`** closes a different gap:
  nothing previously read a byte back through a child
  `instar create -f {vpc,vhdx} -b PARENT` itself wrote, only its
  recorded identity fields. A freshly created child allocates
  nothing, so all three readers must agree with the parent's own
  content exactly — the one case here where a disagreement would
  indict the writer rather than the reader. A created VHD child
  composes exactly; a created VHDX child does not, for a reason that
  is libvhdi's rather than instar's (defect D, below).

**The one pinned exception.** libvhdi decodes a VHD per-block sector
bitmap with an unmasked shift, so once a higher bit in a bitmap byte
is set, every later sector that byte covers reads as present in the
child. `vhd-diff-child-mixed` is the fixture built to put parent-owned
and child-owned sectors in one bitmap byte, and sector 2 is where that
lands: libvhdi returns 512 **zero** bytes there, not the child's data
and not the parent's — it is reading the child's own (unwritten)
block, not merely using the wrong source. `vhd-diff-child-aligned` is
the fixture built to keep ownership out of shared bitmap bytes, and
libvhdi agrees with it on all 16,777,216 bytes, which is the control
that pins the fault on libvhdi's decoder rather than on the harness,
the generator, or instar. The test asserts the exception positively,
in both directions — that sector must differ, and the oracle's bytes
there must be exactly zero — so a disagreement anywhere else fails
loudly and an upstream fix turns this one test red rather than quietly
passing.

**Four libvhdi defects have been measured against this project's
fixtures in total**, recorded in `tests/manifest.json` and in the
docstrings of `TestDifferencingLibvhdiOracle`,
`TestDifferencingOracleComposes` and
`TestDifferencingCreatedChildComposes` in `tests/test_differencing.py`.
None is an instar defect — each is isolated from instar's own reader
and from the fixture generator by a control described at its source:

| Defect | What libvhdi gets wrong |
|---|---|
| A | Decodes a VHD per-block sector bitmap with an unmasked shift, so a higher set bit makes every later sector in that byte read as present in the child (`vhd-diff-child-mixed`, sector 2, above) |
| B | Looks up a VHDX parent locator's `relative_path` key with the wrong length, so `get_parent_filename()` returns `None` for a VHDX child instar wrote — instar writes that one key for the relative path `-b` was given, and the lookup never matches it |
| C | Over-reads two bytes past an unterminated 512-byte parent unicode name into the following locator table, reporting a 257th character (`vhd-diff-locator-overlong`) |
| D | Ignores a sector-bitmap BAT entry's `SB_BLOCK_NOT_PRESENT` state and reads the bitmap from file offset zero instead, misreading 56 of a freshly created, wholly unallocated VHDX child's 32768 sectors |

## Mutation harness for the differencing tests

`tools/mutate-differencing.sh` answers one question about
differencing support, on both sides of it — writing an image with
`instar create -f {vpc,vhdx} -b PARENT`, and composing one back
against its parent in the guest chain walker: **can the tests that
guard it actually fail?** It is not a coverage tool and it measures
nothing. Each case breaks one specific behaviour in `src/` and
requires one named test to notice; a test that still passes against a
deliberately broken emitter is not guarding what its name says it
guards. See the VHD and VHDX notes in
[chain-config.md](/components/instar/chain-config/#format-specific-notes) for the
composing path being guarded.

There are **105 cases**, in eight groups.

Twenty-seven cover the writer. Fifteen mutate a library crate
(`create`, `vhd`, `vhdx`) and are caught by a Rust unit or round-trip
test; twelve are caught by a Python integration test through the real
binary. Ten of those twelve mutate the `create` guest operation, which
is excluded from `cargo test --workspace`, so a unit test written
beside that code would never run; the other two mutate the `create`
crate but break something only an external parser can see. Going
through the real binary means `make instar` before the test and again
after the source is restored.

The other **47 reader cases** cover the reader — the VHD and VHDX arms of
`read_chain_virtual_cluster`, the sector-bitmap helpers in the `vhd`
and `vhdx` crates, and the per-chain judgement in `init_chain_states`
that decides whether a differencing child composes or is refused.
Eight of them are on that judgement alone, four per format: widening
it back to an unconditional refusal, removing it, deriving it from the
device-array bound instead of the chain segmentation, and naming the
wrong format in the refusal it raises.

Five more cover the one thing only `compare` does: packing two
independent backing chains into the single device array the guest
walks, with one `ChainSegment` per chain and image2's first device
derived by adding the two chain lengths. Two mutate the host's
segmentation, collapsing both chains into one segment and giving each
segment the other chain's length. One reads image2 from image1's chain
start. One asks "is there a parent behind me" with a device's array
index instead of its offset within its own chain, which is identical
for the chain that begins at index 0 and wrong for the other. The
fifth reverts `init_chain_states`' per-chain judgement to the
array-bound form the segmentation replaced — the same clause two of
the reader cases mutate, caught here as a wrong verdict on the command
line rather than inside the crate, because that is the form issue #614
was reported in.

All five are caught by `TestDifferencingCompareTwoChains` in
`tests/test_differencing.py`, through the real binary: the host half is
unreachable from a guest unit test, and the wrong answers are verdicts
rather than errors — "identical", or a difference at an offset other
than the one that was altered — so an integration test that reads the
verdict is what has to notice. None of them can be caught by a
single-chain test.

The remaining **six cover `bench` and `rebase`**, the two operations
that read through the shared chain walker with no guest code of their
own to mutate. One reverts `run_bench`'s own host-side discovery call
to `DifferencingComposition::Unsupported`, caught by the test that
reads to the end of a chain's full declared virtual size. The other
five are `rebase`'s: its Cargo.toml never gaining the `vhd-input` /
`vhdx-input` features, its own chain reader's format allowlist
narrowed back to qcow2/raw, `compressed_buf` pointed back at
`CHAIN_CACHES` — aliasing the first chain device's own L1/BAT cache
slot, which a differencing VHD chunk's mixed-ownership arm genuinely
writes through, corrupting the cached sector mid-lookup, the actual
defect this pass of cases found — the new
`differencing_refusal_error` call site in `run_rebase_guest` removed,
so a differencing refusal in the chain renders the pre-existing
generic "the overlay's header could not be parsed" instead of naming
the format, and the second of two chains losing its `ChainSegment`,
which only a rebase given a real `-b` target rather than a detach can
reach. All six run through the real binary, caught by
`TestDifferencingBenchComposes` and `TestDifferencingRebaseThroughChain`
in `tests/test_differencing.py`.

**One case covers `commit`**, which reads through no shared chain
walker at all — its guest reads only the overlay and the backing it is
committed into, and the host never lets a differencing parent further
back in the backing's own chain reach it. The case reverts the
direction the other six go: `run_commit`'s own discovery call is
correct at `Unsupported`, and the mutation flips it to `Supported`, so
the host tries to resolve a differencing ancestor's own parent instead
of leaving it unresolved. The fixture that notices has that parent
missing on purpose — `TestDifferencingCommitDoesNotOpenTheAncestor`
builds a qcow2 overlay over a qcow2 backing whose own backing is a real
differencing VHD or VHDX with its own parent absent — so the mutated
walk fails during host-side chain discovery, before KVM is even opened,
where the unmutated walk never tried to resolve that file at all.

**15 cases guard `map`, `measure` and `check`**, which read through
none of the machinery above: each reads its source on its own and
declines a differencing one outright, so composing it is not a guest
change those operations can inherit. **6 refusal cases** are the first
of them, one per operation per format — a VHD arm testing the footer's
disk type, a VHDX arm testing the metadata's `has_parent` flag — and
every case disables its guard with an added `&& false` rather than
deleting it, so the mutation is a one-line, easily reviewed change to a
condition that already compiles. All six run through the real binary
and are caught by the existing
refusal test for that operation in `tests/test_differencing.py`
(`TestDifferencingMapStillRefuses`, and `TestDifferencingRefusal`'s
`measure` and `check` tests), which already iterates every differencing
fixture rather than one. These six exist so that a later change lifting
one of these three operations' refusals — the way this phase lifted
`convert`, `dd`, `compare`, `bench` and `rebase` — cannot happen by
accident: removing a guard here must fail a named test before it can
land.

The other nine guard the one place these operations were not merely
non-composing but were answering differently from the readers. `map`
and `measure` never call `block_lookup`: each walks the whole BAT once,
through `VhdxState::map_extents` and `VhdxState::scan_allocation`
respectively. `block_lookup` refuses a present payload block on two
independent grounds — a file offset of zero, which is where the
headers live, and a byte range overlapping a region the image declares
— and until both reached both walks, `map` reported such a block as
data living at that offset and `measure` counted its bytes, while
`convert` refused to read it.

That is two cases per walk, one per guard, because neither guard
subsumes the other: in the fixture geometry a block at offset zero ends
exactly where the first declared region begins, so it touches without
overlapping and the overlap test does not catch it, while a block
inside a region has a perfectly non-zero offset. One case per walk
would have let either guard be deleted silently. All four are
`rust_case` rather than `integration_case` because the walks are crate
functions a unit test can drive directly, and because the fixture is a
hand-patched BAT entry no image in `instar-testdata` carries. Each
reverts one call site only, so the verdict names the walk and the guard
that was lost.

Two more are on the shared walk rather than on either guard, and exist
because those four cannot cover them. A refusal case cannot see a
miscount: disabling the sector-bitmap skip in
`for_each_payload_bat_entry` -- the arithmetic both the scanner and
the allocation counter go through -- leaves all four passing, because
a walk whose payload bookkeeping has drifted still reaches the
malformed entry and still refuses it. What a drift changes is where
the walk stops, since the "all payload blocks seen" cap then arrives
one slot early per chunk group. So these cases are caught by counts
taken from a **well-formed** two-group image with a block declared
either side of the group boundary, which reports one block instead of
two under the mutation -- once at 512-byte sectors and once at 4096.
They are the only cases in this group whose fixtures are not
malformed.

The last three are `check`'s, which was the fourth answer to the same
question and for a long time gave none: it builds no `VhdxState` and
runs its own file identifier, header CRC-32C, region table 1+2
cross-validation, metadata and BAT validation, so neither reader guard
reached it. Measured at the CLI before it had them, an image whose
first payload BAT entry was repointed at the metadata region, at the
BAT region or at file offset zero was refused by `convert`, `map` and
`measure` and reported by `check` as "No errors were found on the
image." with exit 0. Two of the three cases are one per guard, for the
reason above. The third is the one no "inside a region" case can
catch: an overlap test degraded into a high-water mark refuses every
block below the highest region's end, which leaves every malformed
case passing, and only a region declared *after* the blocks --
which SPEC(VHDX) permits -- tells the two apart. All three are
`integration_case`, because the arm lives in a `no_std` guest binary
with no test harness to drive it and the only observable verdict is
the real command's exit code.

Then **2 device-count cases**, guarding `bench` and
`check`'s own chain-device-count check (issue #633). Every other
operation that attaches a backing
chain -- `convert`/`dd`, `commit`, `rebase`, `compare` -- refuses one
whose device count exceeds `MAX_CHAIN_DEVICES` (16) before KVM opens;
`bench` and `check` did not, and reaching `DeviceSet::add_device` with
the device set already past that budget trips its own defensive
`assert!`, panicking with KVM and the VM already created rather than
failing cleanly. A plain backing chain cannot exercise this: a 17-image
chain is already refused by `discover_backing_chain`'s own, unrelated
`security.max_chain_depth` control (also defaulting to 16) before
either operation's code runs. The fixture has to make the chain's
*device* count exceed 16 while its *image* count does not, which a
qcow2 top image carrying an external data file (`-o data_file=`) does:
it contributes two devices for one backing-pointer hop. Both cases
disable the guard with `if false` and are caught, through the real
binary, by `TestAdversarialChainDeviceCount` in `tests/test_adversarial.py`
-- the same test module that builds the fixture, since no static
`instar-testdata` image has this shape.

The last group is **2 subcluster cases**, and is the only group not
about a differencing format at all. It is here because the defect it
guards is the same class as the rest -- a span the reader never wrote,
returned to the caller under a successful read -- and because the
differencing work's own fuzz target is what found it. An extended-L2
qcow2 entry can mark a subcluster allocated while naming host offset
zero, which is a contradiction: there is no host cluster to read it
from. The narrow arm of the fill loop has always zero-filled such a
subcluster; the wide arm, taken when the device's sector size exceeds
`cluster / 32`, matched no arm at all and left the caller's buffer
untouched (issue #650). One case removes the arm that now handles it;
the other reverts the lookup that classifies the entry, which is the
vacuity guard -- without it a green test would prove only that the
read never reached the fill loop. Both are `rust_case` against a
static in-memory fixture at 8192-byte clusters and 512-byte sectors,
the geometry that selects the wide arm; the default 64 KiB cluster
takes the narrow one. `docs/quirks.md` records why instar zero-fills
here where qemu fails the read.

Most of the reader cases name a test in the `qcow2` crate and run it
with the full input-format feature list, because the
arms are behind `vhd-input` and `vhdx-input` and a run without those
compiles the mutation away and reports a pass nobody earned. Several
also break a unit test in the `vhd` or `vhdx` crate, which is
deliberate duplication: the crate test pins the helper and the case
here pins the arm that calls it. Running one named test in one package
is what keeps them separate, since `make test-rust` stops at the first
failing crate and would otherwise never reach the arm.

Some name a `vhd` or `vhdx` test instead, because the property is
genuinely the crate's and not the arm's — `--list` shows which, so
the number is deliberately not written here. The arm calls the
ownership coalescer once per run, each time with a correct starting
byte, so a coalescer that stops at a bitmap byte boundary serves the
same bytes in two runs instead of one and no arm test can see the
difference. The same goes for a chunk group of no sectors, which is a
property of the state's geometry rather than of any read. That is not
a weak arm test; it is where the property lives. Each such case says
so where it is defined, and each was established by watching the case
fail while it named an arm test.

**1 survivor case** is among them: `rust_survivor_case` asserts that a
mutation is *not* caught, with the reason recorded at the case.
`vhd-read-classify-stops-at-the-first-mixed-verdict` applies an early
exit a review suggested for `classify_vhd_chunk_ownership`; it
survives because the classifier's full walk is also the chunk's
validation, and `read_vhd_child_runs` repeats that walk and refuses an
undescribed stretch by itself. The change is therefore
behaviour-preserving, and declining it keeps two independent checks on
a fail-closed path rather than one. The verdict is inverted for these:
surviving scores `SURVIVOR` and passes, and being *killed* scores
`FAIL` — not because catching a bug is bad, but because it means the
recorded reason no longer holds and the comment is now lying. A
surviving mutation is a real result about the tests, and keeping it as
a case means the next person to meet it reads the reason instead of
concluding the test is weak.

Four of the integration cases (`oracle-*`) name the libvhdi
cross-check in `tests/test_differencing.py`, which skips without
`vhdiinfo` on `PATH` (Debian: `libvhdi-utils`). A skipped test scores
`BROKEN`, not `PASS`, so the harness needs that package installed to
report a clean run; it warns once up front when it is missing.

A full run took **4m59s** on a warm tree with `vhdiinfo` present and
everything passing, and 4m43s when it held 57 cases; an earlier run
of the same tree took 9m38s because it also had to build a docker
image. Before the reader cases existed the 26 writer cases alone took
2m32s, 2m44s and 3m35s on three measured runs. Those are observations rather than estimates:
an earlier "about two minutes" here was a guess, and an understated
figure invites the reader to assume a run has hung. Expect a spread,
which is cargo and docker layer caching. The writer cases are
dominated by the 24 `make instar` rebuilds their integration tests
need; the reader cases need no rebuild but each recompiles the
`qcow2` crate in release with the full feature list. The five compare
cases are integration cases too, so they add ten more rebuilds: four
of them together measured 1m51s on a warm tree.

Both devcontainer images must already exist, because
`tools/cargo-in-container.sh` and the integration cases run them
rather than building them. On a machine that has neither, `make
instar-devcontainer` and `make instar` build them first, and that is
tens of minutes rather than minutes.

```bash
tools/mutate-differencing.sh          # every case
tools/mutate-differencing.sh --list   # the case names, run nothing
tools/mutate-differencing.sh NAME...  # only the named cases
tools/mutate-differencing.sh --self-test        # check the verdict classifier
tools/mutate-differencing.sh --check-patterns  # do the mutations still land?
```

Three things run in the `ci-tooling` job, because a full run needs
docker, testdata and `/dev/kvm` and so never runs on a pull request —
which would leave the harness free to rot unnoticed, and a harness
that has rotted quietly is worse than none, because its green is
trusted.

| Guard | Answers | Cost |
|---|---|---|
| `--self-test` | does the verdict classifier classify correctly? | milliseconds |
| `--check-patterns` | does every mutation still have exactly one place to land, and do the case, reader and survivor counts here and in the script still agree? | a few seconds |
| `tools/ci/test-replace-once.sh` | does the literal replace helper still refuse zero and multiple matches? | milliseconds |

None of them needs docker, a venv, testdata or a build.

`tools/check-no-capability-claims.py` is a related but separate tool,
not one of the three above: it fails, naming the line, if anything in
the tree says instar cannot read or compose a differencing image — a
claim that was true when earlier phases wrote it and became false once
five operations learned to compose one. It is not wired into
`pre-commit` or CI; a contributor gets it only by running it, the same
as this harness.

It reads whole paragraphs — runs of non-blank lines, flattened —
across `src/**/*.rs`, `tests/**/*.py` and top-level `docs/*.md`, so
Rust comments and block comments, Python docstrings, assertion
messages and Markdown prose all fall out of one pass. That breadth is
not incidental. A line-granular grep missed a claim in
`src/shared/src/lib.rs` that wrapped mid-phrase, and a
comment-granular version of the script then missed a second one in a
`tests/` assertion message, which is why it reads string literals and
prose rather than comments alone. Expect it to flag a docs page or a
test message, not only a `//` comment.

Two trees are excluded on purpose: `docs/plans/` records what was true
when each phase was planned and prescribes the sweeps that made it
false, and `CHANGELOG.md` entries were true for the release they
describe. Editing either to satisfy the script would falsify a record.
The pattern is anchored on what a claim says instar cannot act *on*
rather than on the verb, because that is what decides whether the
claim is false. An unscoped claim naming a differencing image or a
parent as the thing instar cannot read or compose is flagged; a scoped
one that happens to use the same words is not — "a chain instar cannot
compose", said of a differencing image whose own parent is itself
differencing, is still true and stays clean, as does a statement about
any unsupported source. The script's own docstring and `--self-test`
carry the specimens; this page deliberately does not spell the flagged
form out, because a page containing it verbatim is indistinguishable,
to anyone skimming, from a page making the claim — and the checker
reads this page too.

`--self-test` pins ten checks: nine claim forms it must flag or clear,
each one that actually appeared in this repository, and one that the
scan reads files at all from a working directory other than the
repository root. That last one exists because the script globbed
relative to the current directory and reported a clean tree, exit 0,
having read nothing, whenever it was run from anywhere else. It now
resolves the repository from its own path and refuses to report a
clean tree on a scan that matched almost no files.

`--self-test` exists because the classifier is the one part of the
harness nothing else checks — everything else is checked *by* it — and
it shipped misreading `FAILED (errors=1)` as a caught mutation, which
no run of the harness itself would have revealed.

`--check-patterns` exists because every search string is pinned to
`src/` byte for byte, indentation included. A `rustfmt` change that
moves a space turns a case `BROKEN`, and without this nothing would
say so until someone spent the full run. It reports `LANDS` and
`DRIFT` rather than `PASS` and `BROKEN`, because it applies no
mutation and runs no test and those words are defined above as facts
about a run that did both.

It also reads the case count out of *this page* and compares it with
the script's own constant, so the two cannot drift apart. That is a
recent correction: the check previously compared the script against
itself while the documentation claimed otherwise, which is the exact
species of unverified claim the rest of this section is about.

The verdicts are the point of the script:

| Verdict | Meaning |
|---|---|
| `PASS` | The mutation applied and the named test failed. The test guards the property. |
| `FAIL` | The mutation applied and the test still passed. The test does not guard the property. |
| `BROKEN` | The case proved nothing: the mutation could not be applied, the test never ran (a wrong package name, a build error, a filter that matched no test), the test skipped, or the test does not pass against unmutated source. |

`BROKEN` exists because the interesting failure mode of a mutation
harness is not a test that fails to fire, it is a case that scores a
pass without having earned it. There are three ways that happens, and
the harness closes each:

* **The mutation never lands.** A substitution matching nothing leaves
  the code unchanged, the test passes for the ordinary reason, and a
  naive harness calls that a pass. Every edit therefore goes through
  `tools/replace-once.py`, a **literal** find-and-replace — no regex,
  no `sed`, no escaping rules — that exits non-zero unless the search
  string occurs exactly once in the target file.
* **The test was already red.** A test broken on `develop`, or red for
  an environmental reason, fails again with the mutation applied and
  the failure gets credited to the mutation. Each named test is
  therefore run once against unmutated source first, and a case whose
  baseline does not pass is `BROKEN` rather than `PASS`. Baselines are
  cached per target, so the baseline cost is one run per distinct
  target rather than one per case, and no extra rebuilds
  — the integration baselines reuse the clean binary each case
  restores anyway.
* **The test errored rather than failed.** `unittest` reports an
  exception raised outside an assertion as `FAILED (errors=1)` — the
  same `FAILED` line a caught mutation produces. Matching on the word
  alone meant a missing testdata checkout, which makes `setUpClass`
  raise, scored `PASS` for every integration case without one
  assertion being evaluated. A genuine catch always says `failures=`,
  so `errors=` is now `BROKEN`, and the script refuses to start
  without testdata at all.

The script exits non-zero if any case is `FAIL` or `BROKEN`.

The log of any case that does not pass is kept under
`.mutation-backups/logs/` and named in the report, because the scratch
directory dies with the process and the one moment the `cargo` or
`unittest` output is wanted is when something went wrong. Those logs
are cleared at the start of each run, so a stale one cannot be read as
current.

Naming a case that does not exist is an error rather than an empty
pass: a mistyped name used to run nothing and report `0 cases: 0 PASS,
0 FAIL, 0 BROKEN` with exit 0.

Originals are copied into `.mutation-backups/` (gitignored) before each
edit and copied back afterwards, including on interrupt. Restoring with
`git checkout <path>` is deliberately avoided: it discards uncommitted
work. After a run, `git status --short` should be unchanged and the
`instar` binary rebuilt from clean source.

The backup lives inside the repository rather than in a `mktemp -d`
because a `mktemp -d` dies with the process: a run that is **killed**
rather than interrupted leaves its mutation applied with nothing to
restore from. Since a mutation is a small change that still compiles,
`pre-commit` and `cargo build` both pass on it and nothing downstream
would notice. So the script also **refuses to start when `src/` has
uncommitted modifications**, listing what changed and printing the
exact command to put it back — the surviving backup where there is one,
`git checkout -- src/` otherwise. That turns a killed run into a
refusal at the next invocation instead of a surprise at some later
commit. Pass `--allow-dirty-src` when the modifications are your own
work in progress and you want the harness to mutate on top of them.

Two properties in this area are deliberately **not** mutation-tested.
The `(_, true) => ERROR_PARENT_FORMAT_MISMATCH` arms of
`vhd_opts_from` and `vhdx_opts_from` are unreachable by construction —
the operation refuses a wrong-format parent, and then a format-right
but identity-less parent, before either arm can be reached — so no
single substitution in them changes observable behaviour. And a
fixed-subformat `vpc` child with `-b` is refused on the host before the
emitter sees it, so it is a host-argument case rather than an emitter
one.

## Related Documentation

- [Format Coverage](/components/instar/format-coverage/) - Comparison with oslo.utils format_inspector
- [Format Detection Safety](/components/instar/format-detection-safety/) - Security model for format auto-detection
- [Security Analysis](/components/instar/security/) - CVE analysis and threat model

## Test Image Generation

Synthetic test images that cannot be created by `qemu-img` are built
by scripts in `scripts/`:

- `create-vhd-testdata.sh` — Fixed VHD (disk_type=2); differencing
  VHD (disk_type=4) and differencing VHDX (HasParent) as type-only
  markers with no parent reference, via qemu-img patching; real VHD
  and VHDX differencing chains (parent, aligned and mixed children, a
  third differencing level over one child per format, and each
  chain's intended composed raw image) via Python struct packing
  against the structure pin in
  `docs/plans/PLAN-differencing-phase-01-pin.md`; and six adversarial
  parent-locator VHDs (absolute path, relative traversal, UNC, URL,
  an over-long unterminated parent name, and mutually conflicting
  locator entries) that are structurally valid and hostile only in a
  path string. It takes two output directories,
  `create-vhd-testdata.sh [output-dir] [audit-dir]`, because the six
  adversarial fixtures belong in `custom/audit/` and everything else
  in `custom/format-coverage/`. Given one argument the audit
  directory becomes `<output-dir>/audit`, so a scratch run writes
  everything under the directory it was given rather than sending
  sixteen files to the scratch tree and six into the real testdata
  checkout. Every file it writes is byte-reproducible except the four
  VHDX ones, where `qemu-img` stamps fresh GUIDs on each run; each is
  therefore skipped when it already exists, and rebuilding any of
  them needs `REGEN_VHDX=1`. The parent, the child and the
  grandchild must be committed together when they are rebuilt: each
  of the upper two records the DataWriteGuid of the one below it, so
  a regenerated parent invalidates both images above it. The
  generator re-checks both of those links on every run, including the
  runs that rebuild nothing, so a half-finished regeneration fails
  there rather than in the test suite
- `create-vmdk-testdata.sh` — Binary VMDK4 with multi-extent descriptor
- `create-luks-testdata.sh` — LUKS v1 containers with inner formats
- `create-native-luks-testdata.py` — LUKS v1/v2 with known encrypted
  content (v1 raw, v2 Argon2id, v1 wrapping QCOW2)
- `create-qcow2-luks-testdata.sh` — QCOW2 with LUKS encryption (crypt_method=2)
- `create-check-testdata.sh` — QCOW2 images with specific corruptions

Most adversarial and CVE-reproducer image generation scripts live in
`instar-testdata/scripts/` (the private testdata repository), not in
the public `instar/scripts/` directory, and include scripts for
compression bombs, circular/deep chains, integer overflow headers,
boundary values, format confusion, and CVE reproducers.
`create-vhd-testdata.sh` above is the one public exception: its
adversarial parent-locator fixtures live alongside its differencing
chains so that both share one generator and one structure pin.

Generated images live in `../instar-testdata/custom/format-coverage/`
and `../instar-testdata/custom/audit/` (adversarial images).
The test manifest (`tests/manifest.json`) references them with
`generated_by` and `skip_qemu_img: true`.

## Cross-version qemu-img baselines

`instar-testdata` ships a per-qemu-version baseline matrix
generated by `make baselines-info` / `make baselines-check` /
`make baselines-measure` (and aggregated via `make baselines`),
plus a `create-info-json` matrix produced by
`python scripts/generate-baselines.py --command create`, and a
`dd-info-json` matrix (produced by `--command dd`) recording the
`qemu-img info` JSON of the dd output for each `(format, window)`
case across all 80 qemu-img versions
(`instar-testdata/expected-outputs/dd-info-json/`).
Each command writes recorded stdout / stderr / exit-code triples
to `expected-outputs/<output-type>/[bucket/]<version>/<image-id>.{stdout,
stderr,meta.json}`, then `make profiles` deduplicates them into
profile buckets via `scripts/detect-profiles.py`. instar's
integration tests select the matching baseline for the locally
installed qemu-img version. Currently 80 versions are covered
(6.0.0 through 10.2.0). The measure baselines additionally use
a `_size/` pseudo-bucket for `--size`-mode invocations that have
no source image, and a `__<target>` suffix on source-image
filenames to record both `-O raw` and `-O qcow2` measurements
of the same image. The create baselines bucket by target format
(`create-info-json/<target>/<version>/<case-name>.{stdout,…}`)
and run a two-step pipeline (`qemu-img create` then `qemu-img
info --output=json`) — the recorded artefact is the info JSON
on the produced fixture, not create's own log line. Case names
like `1M-default` recur under every target, so `detect-profiles.py`
names create-info-json's derived profile files `<target>-<case>`;
flattening them under bare names silently discarded four of the
five targets until 2026-08-11. Output types whose case names
already encode the target (dd, map, measure, snapshot-list) are
left unprefixed, so the prefix is decided per output type rather
than per file and a consumer can always build the name it wants
from `(target, case)`. Per-
invocation random fields (vmdk `cid` / `parent-cid`, vhdx
header-id) prevent dedup from collapsing duplicate-output
versions, so each version gets its own profile; the comparison logic excludes those random fields from the
field-equivalence check.

## Integration Testing

Integration tests compare `instar info` output against `qemu-img info` to verify
drop-in replacement compatibility, validate `instar check` against deliberately
corrupt test images, cross-validate `instar compare` output against
`qemu-img compare`, cross-validate `instar convert` output against
`qemu-img convert`, and cross-validate `instar measure` output against
`qemu-img measure` for raw and qcow2 targets. Tests use Python testtools/stestr.

instar emulates the host's `qemu-img` version; the harness selects the
matching output baseline profile by full `major.minor.patch`
(`base.py::_select_version_match`). `tools/probe-qemu-versions.sh`
records the `qemu-img` version each matrix distro ships. See
[docs/testing.md](https://github.com/shakenfist/instar/blob/develop/docs/testing.md)
("qemu-img version profiles and the distro matrix") for the model and
the per-distro table.

`tools/test-package-functional.sh <package> <distro-image>` runs the
full suite against the **installed** `.deb`/`.rpm` inside a target-distro
container, using that distro's own `qemu-img` as the oracle (tests from
the tree, binary from the package via `INSTAR_BINARY_PATH`). Run by hand like
that it selects nothing, so it really is the whole suite; the
distro-matrix CI reaches the same script through
`tools/ci/run-matrix-entry.sh`, which passes the half of the split that
entry is for. It is the first check to surface version-specific
output-format parity gaps that host-only CI cannot. It
is distinct from `tools/test-package-install.sh` (fast packaging smoke)
and is driven across seven distros by the `package-matrix` job, which
runs **only** in the merge queue and on `workflow_dispatch` — never on a
pull request. `tools/ci/run-matrix-entry.sh` is the CI wrapper; use it
(with `MATRIX_SELECT`) to reproduce an entry exactly as CI runs it.
`can_merge` is the queue's required check, not the individual entries.
`--select REGEX` replays a single failure on one distro, which matters
because two containers sharing a KVM host produce timeout failures that
mimic real divergences — always re-run uncontended before attributing
one. The runner also refuses to report a **truncated** run as a pass
(see docs/testing.md); do not weaken that guard.

Two facts about distro qemu that the version model does not capture:
RHEL-family `qemu-kvm` omits the `qed`, `qcow`, `parallels`, `dmg`,
`bochs` and `cloop` drivers, so tests needing qemu-img as the oracle for
those formats must call `skip_unless_qemu_supports()`; and image buffers
must be compared with `assert_bytes_identical()` rather than
`assertEqual`, because a multi-megabyte mismatch exceeds subunit's
packet limit and silently truncates the run. An oracle that is simply
absent (`qemu-storage-daemon` on some EL streams) is a `skipTest`, never
an error.

**Never guess a qemu version boundary — measure it.** instar-testdata
carries 80 static per-version `qemu-img` builds under
`qemu-img-binaries/x86_64/<version>/`, 6.0.0 through 10.2.0, that run
directly on the host. Every boundary in `version::OutputProfile` was
established by running the real binary; the version maps and profile
baselines have been wrong before, and so has reasoning from qemu source.
`info`, `map` and `snapshot` all accept `--qemu-version`, so both sides
of a boundary are testable on the dev host without a distro container.

**A green suite does not mean the baselines are right.** The derived
`expected-outputs/*/profiles/` directories have been silently wrong
twice while the `raw/` captures they come from were correct — once for
`snapshot-list-human`, once for `create-info-json`, where every profile
carried 10.2.0's output and four of five target formats had been
overwritten out of existence. Neither showed up as a failure: a test
that skips on the host's qemu version, or asserts only that a profile
*exists*, passes just as loudly as one that compares bytes. When a
baseline is suspect, diff `profiles/` against `raw/` and check the
recorded `*_stdout_bytes` in each `.meta.json`, which is what
`detect-profiles.py`'s `validate_profiles()` now does before it will
commit anything.

```bash
# Set up test environment
make test-venv

# Run safe integration tests
make test

# Run tests with verbose output (shows diffs on failure)
make test-report

# Run all tests including malicious images (use with caution)
make test-malicious

# Run tests inside container (as CI does)
make test-container

# CI splits tests into three parallel jobs by format family:
make test-container-core              # info, check, security, oslo-crossval
make test-container-convert-qcow2    # QCOW2/VMDK/RAW convert + compare
make test-container-convert-vhd      # VHD/VHDX convert (slowest)

# Clean test artifacts
make clean-tests
```

**Test structure:**
- `tests/manifest.json` - Defines test images and their safety levels
- `tests/test_info_safe.py` - Tests against known-safe images
- `tests/test_info_malicious.py` - Tests against malicious images using expected overrides
- `tests/test_check_formats.py` - Tests for check operation
  (format detection, corruption, validation, incompatible
  feature bits, ZSTD compression, extended L2 entries, ZSTD
  + backing chains, extended L2 + compressed clusters,
  refcount widths 1-64 bit, compressed cluster leak
  detection, large cluster sizes 256K-2MB, VMDK GD/GT
  validation with overlap/compressed grain marker/RGD checks,
  VHD fragmentation/version/feature/fixed size validation,
  VHDX file identifier/region table cross-check/fragmentation,
  QCOW2 snapshot detection, TestExtendedL2Subclusters for
  partial subcluster allocation with Normal/Zero/Unallocated
  states)
- `tests/test_check_repair.py` - Tests for `check --repair`
  (leaks/all tiers repair the corrupt-fixture matrix to
  `qemu-img check`-clean with guest data preserved; the
  snapshot/compression/corrupt-bit refuse paths stay
  byte-identical; the overlapping case is a safe partial repair;
  plus `--repair`+`--chain` rejection, clean-image no-op,
  qcow2-only handling, and idempotence)
- `tests/test_compare.py` - Tests for compare operation
  (raw-vs-raw, QCOW2-vs-raw, QCOW2-vs-QCOW2, compressed
  QCOW2, backing chains, LUKS-in-QCOW2 decryption)
- `tests/test_convert.py` - Tests for convert operation
  (QCOW2-to-raw, raw-to-QCOW2, QCOW2 re-encoding,
  compressed output with `-c` flag, backing chains,
  round-trip validation, errors, large cluster sizes up to
  2MB, manifest-driven cross-validation against qemu-img
  including compressed output, encrypted QCOW2 decryption,
  LUKS-in-QCOW2 decryption, native LUKS v1/v2 decryption,
  LUKS v2 with Argon2id KDF and --max-guest-memory,
  LUKS-wrapping-QCOW2 conversion, snapshot extraction,
  extended L2 output with `--extended-l2`,
  LUKS-encrypted output with `--luks-encrypt-passphrase`)
- `tests/test_adversarial.py` - Adversarial image tests: compression bombs, circular
  backing chains, deep chains, integer overflow triggers, refcount order edges,
  oversized virtual sizes, VMDK grain size boundaries, VHDX conflicting dual
  headers, VHD/VHDX BAT beyond EOF, polyglot files (QCOW2+VMDK, QCOW2+ELF),
  truncated headers (QCOW2, VMDK, VHD), VMDK descriptor attacks (null bytes,
  multi-extent, huge size). Uses `run_adversarial()` helper with timeout/memory/
  signal enforcement.
- `tests/test_security.py` - Security feature detection tests (backing files,
  external data files, VMDK descriptors, raw format validation, backing chain
  security) and CVE reproduction tests (CVE-2024-32498 external data file,
  CVE-2015-5163 backing file traversal, CVE-2022-47951 VMDK descriptor,
  CVE-2015-5162 resource exhaustion, CVE-2014-0223 L1 integer overflow,
  CVE-2024-4467 json:{} format confusion). 19 CVE tests verify all 6 CVEs
  are mitigated.
- `tests/test_oslo_crossval.py` - Cross-validation against oslo.utils
  format_inspector (format detection, safety checks, virtual size).
  Skips if oslo.utils is not installed.
- `tests/test_snapshot.py` - Integration tests for the `instar snapshot`
  subcommand: 12-image list matrix vs cross-version baselines,
  12 JSON golden comparisons with structural cross-check, mutation
  round-trips (create/delete/apply) with `qemu-img check` post-op
  assertions, error paths and qcow2-only enforcement, empty-table
  behaviour. JSON goldens in `tests/golden/snapshot-list/`.
- `tests/test_bench.py` - Integration tests for the `instar bench`
  subcommand (62 tests): read/write request scheduling, host-side
  validation matrix, the qcow2 write-envelope gates, `--output json`
  schema validation, human-output byte-parity with `qemu-img bench`,
  and the `KNOWN_BENCH_DIVERGENCES` registry.
- `tests/expected_outputs/` - Expected output files for malicious images

**Adding new test images:**

Use the `/instar-add-test-image` skill for guided assistance, or manually:

1. Add the image to `instar-testdata/` repository
2. Add an entry to `tests/manifest.json` with appropriate safety level
3. Add a scenario to the appropriate test file (`test_info_safe.py` or `test_info_malicious.py`)
4. For malicious images, create an expected output file in `tests/expected_outputs/`

**Adversarial test images:** Generated by scripts in `instar-testdata/scripts/`
(compression bombs, circular/deep chains, integer overflow headers, boundary
values for refcount order, virtual size, grain size, dual headers, BAT entries,
polyglot files, truncated headers, VMDK descriptor attacks, CVE reproducers).
Images live in `../instar-testdata/custom/audit/`. Scripts that generate
adversarial or CVE-reproducer images must always be placed in the private
`instar-testdata` repository, never in the public `instar/scripts/` directory.

**LUKS test images:** Synthetic LUKS headers (luks-v1, luks-v2) are
generated by `scripts/create-luks-headers.py` for header parsing tests.
Real LUKS containers (luks-v1-raw-gpt, luks-v1-qcow2) are created by
`scripts/create-luks-testdata.sh` using cryptsetup, with known test
passphrases stored in the manifest. The `luks-v1-aes-xts` test image is
built by `scripts/create-native-luks-testdata.py` (no root required) with
known encrypted content for conversion testing. The `luks-v2-aes-xts`
test image is built by `scripts/create-native-luks-v2-testdata.py` with
low-memory Argon2id parameters for LUKS v2 conversion testing. The
`luks-v1-qcow2-inner` test image is created by
`scripts/create-luks-qcow2-inner-testdata.sh` for testing LUKS containers
wrapping QCOW2 images. LUKS-in-QCOW2 test images are created by
`scripts/create-qcow2-luks-testdata.sh`. These use `skip_qemu_img: true`
since qemu-img cannot inspect inside LUKS without a secret object.

See `docs/testing.md` for detailed documentation.

**Safety levels:**
- `safe` - Run by default, known-good images
- `caution` - Edge cases that may expose bugs
- `malicious` - CVE exploit images, require explicit opt-in

## Testing Prototypes

Each prototype has its own devcontainer with the required toolchain. Use the
Makefile for building and testing:

```bash
# Build a specific prototype
make build-prototype PROTOTYPE=virtio-block5

# Build the devcontainer for a prototype
make build-prototype-devcontainer PROTOTYPE=virtio-block5

# View run instructions
make run-prototype PROTOTYPE=virtio-block5
```

For manual testing without the Makefile:

```bash
cd prototypes/<prototype-name>
./build.sh                           # Build the prototype
sudo ./target/release/vmm guest.bin  # Run (requires KVM)
```

## Testing Considerations

- Test with malformed/malicious input images
- Verify sandbox containment
- Check for resource exhaustion (memory, disk, CPU)

