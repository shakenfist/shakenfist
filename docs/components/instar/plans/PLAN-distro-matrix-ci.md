# Plan: Multi-distro install + qemu-img differential CI

## Status: Drafted, not started

Drafted as a follow-up to v0.2 packaging work. Do not start until
the v0.2 release ships and the install smoke test in
`.github/workflows/functional-tests.yml` (job `package-smoke`) has
been observed to be stable for a couple of weeks.

## Goal

Run instar's full functional test suite against the .deb / .rpm
packages on a representative matrix of Linux distributions in the
GitHub merge queue. Each matrix entry installs the package, points
the test harness at `/usr/bin/instar`, and runs every
`tests/test_*.py` against the qemu-img version that ships with
that distribution.

The point is two-fold:

1. **Catch packaging regressions.** Asset-path errors, runtime
   dependency mistakes, mode-bit drift, resolver fallback bugs
   that don't surface in the in-tree build layout. The PR-level
   smoke test catches the obvious cases on debian:trixie; the
   merge-queue matrix catches them across distros.
2. **Catch qemu-img output-profile regressions.** instar adapts
   its qemu-img-compatible output to the locally detected qemu-img
   version (see `tests/test_oslo_crossval.py` and the version
   detection code in `src/vmm/`). Today that adaptation is only
   exercised against whatever qemu-img happens to be installed on
   the build runner. Running across distros means we exercise it
   against several real qemu-img versions that users actually
   run, and we catch the case where instar's profile selection
   logic has drifted away from the reality of a specific distro.

## Existing infrastructure to reuse

The pieces of this are mostly already in place:

- **Test harness already takes `INSTAR_BINARY_PATH`**
  (`tests/base.py:261`) and `INSTAR_TESTDATA_PATH`
  (`tests/base.py:64`). Pointing the existing test suite at a
  distro-installed binary requires no harness changes.
- **instar-testdata** already provides the test corpus and
  qemu-img baseline output. Cloning is parametrized via
  `GITLAB_TESTDATA_TOKEN` and is already done in every job in
  `functional-tests.yml`.
- **Smoke script** `tools/test-package-install.sh` accepts both
  `.deb` and `.rpm` and any distro image. It can be the
  per-matrix-entry inner test runner with minor extensions
  (running the full suite instead of the smoke checks).
- **Pattern for matrix testing inside merge_group** is well
  established in shakenfist sibling repos:
    - `shakenfist/shakenfist/.github/workflows/functional-tests.yml`
      splits `functional_matrix_pr` (PR-level smoke) from
      `functional_matrix_merge` (merge-queue cluster tests).
      Lift the same shape.
    - `shakenfist/kerbside-patches/.github/workflows/functional-tests.yml`
      has a Rocky 9/10 + Fedora cross-distro matrix that's
      conceptually almost identical to what instar needs.
- **KVM-capable self-hosted runners** are already set up
  (`[self-hosted, debian-12, xl]` is used by every functional
  test job today, with `/dev/kvm` passthrough into containers).

## Open design decisions

These need to be resolved before implementation. Each is more of a
policy call than a technical hurdle.

### 1. glibc baseline

The current build container is based on `debian:trixie`
(glibc 2.41), so the produced binaries refuse to run anywhere
older than glibc 2.39. That excludes Rocky/RHEL 9 (2.34),
Debian 12 (2.36), Ubuntu 22.04 LTS (2.35), and Fedora 38 and
older.

Three options:

- **Pin the build base to `debian:bookworm`** (glibc 2.36).
  Single artifact set covers Debian 12+, Ubuntu 22.04+,
  Fedora 38+, Rocky 10. Still excludes Rocky 9. Lowest-friction
  way to widen support without per-distro builds.
- **Pin to `rockylinux:9`** (glibc 2.34). Widest coverage
  including Rocky 9 / RHEL 9. Risk: nightly Rust + Docker
  buildkit toolchain availability on Rocky 9 is less polished
  than Debian's; some apt-side build deps (libqcow-utils,
  libvhdi-utils) may not have direct Rocky equivalents.
- **Build per-distro artifacts.** Most flexible (each artifact
  is glibc-perfect for its target distro), most expensive
  (matrix grows by N, artifact count grows by N).

**Recommendation:** start with `debian:bookworm` as a single
build base. If Rocky 9 demand materializes, add a parallel
`rockylinux:9`-based build. Don't go to per-distro builds
until forced by an actual incompatibility.

### 2. qemu-img baseline drift

instar-testdata captures qemu-img reference output. That
output was captured against one specific qemu-img version. The
matrix will run against several different qemu-img versions
(qemu 7.x on Debian 12, 8.x on Ubuntu 24.04, 9.x on Fedora
40+, etc.). Some divergence between baseline and live qemu-img
output is expected and not a bug.

`tests/test_oslo_crossval.py` already has a precedent
(`assert_known_oslo_divergence`) for "we know this differs and
that's fine". Extend that pattern: assertions that record
"these fields legitimately differ on qemu X.Y" rather than
asserting bit-for-bit equality.

The clean path:

- Categorize each test assertion as either **portable**
  (must match on every qemu-img we run against) or
  **version-specific** (matches the baseline only on the
  exact qemu-img version that produced the baseline).
- Make version-specific assertions skip or use a tolerant
  comparator on other versions, with a clear log line saying
  "skipped because qemu-img X.Y != baseline qemu-img X.Y'".
- The merge-queue matrix records per-distro qemu-img versions
  in the test output so the source of any divergence is
  obvious.

This is the largest design block; do not start the matrix
implementation until this comparator policy is resolved. A
mismatch storm on first run will make the whole job
permanently red and people will tune it out.

### 3. PR vs merge-queue split

Mirror the shakenfist pattern:

- **Pull request** events: build, unit tests, integration on
  the build tree (existing functional-tests.yml jobs), plus
  the `package-smoke` job introduced for v0.2 (debian:trixie
  install + minimal smoke).
- **Merge queue** events: everything above, plus the new
  `package-matrix` job described below. Entries: 6+ distro
  packages, full test suite each.

Gate via `if: github.event_name == 'merge_group'` /
`!= 'merge_group'`.

### 4. Build-once vs build-per-distro

The simple model is one build job that produces the .deb and
.rpm, then N matrix entries that consume those artifacts. That
works only if a single artifact is glibc-compatible with every
matrix entry, which constrains decision (1).

If we go per-distro builds, the matrix is `(build, test)`
pairs and the workflow is more complex. Avoidable by picking
the right glibc baseline.

## Distro matrix

Initial proposal once the above decisions are made:

| Distro          | Package | qemu-img range | Notes                     |
| --------------- | ------- | -------------- | ------------------------- |
| Debian 12       | .deb    | 7.2            | LTS, current "stable"     |
| Debian 13       | .deb    | 9.x            | "trixie", current build base for v0.2 |
| Ubuntu 22.04    | .deb    | 6.2            | LTS, oldest target        |
| Ubuntu 24.04    | .deb    | 8.2            | LTS                       |
| Fedora latest   | .rpm    | 9.x            | Bleeding edge qemu-img    |
| Rocky/RHEL 9    | .rpm    | 8.2            | Wide enterprise install   |
| Rocky/RHEL 10   | .rpm    | 9.x            | Newer enterprise          |

Seven entries. Add openSUSE Leap, Arch, or Alpine only when
specific user demand surfaces. Each entry runs the full
functional suite, so total wall time is roughly
`max(per-entry runtime) ≈ 30-45 min` if they parallelize on
distinct runners.

## Implementation phases

### Phase 1: glibc baseline

- Decision: pin build base to `debian:bookworm` (or whatever
  is decided in design block 1).
- Update `src/.devcontainer/Dockerfile` `FROM` line.
- Re-run `make instar` and confirm artifact still works in
  the existing test suite and in the v0.2 `package-smoke`
  job.
- Re-run smoke test against the older-glibc image
  (e.g. `debian:bookworm`, `ubuntu:22.04`, `rockylinux:9`)
  to confirm the package now installs there.
- Update CHANGELOG/README minimum-glibc note.

This phase is independently valuable -- it widens v0.2.x
patch-release compatibility without any of the matrix work.
Could be merged as soon as decided.

### Phase 2: comparator policy for qemu-img drift

- Audit `tests/` for assertions that depend on qemu-img output.
- For each, decide: portable, version-specific, or
  baseline-only.
- Implement `assert_qemu_compatible(...)` helper that takes a
  baseline value and a tolerance (exact, semver-compatible,
  field-subset, or skip).
- Refactor the existing assertions to use it.
- This phase ships independently; no CI changes yet.

### Phase 3: matrix runner script

- Generalize `tools/test-package-install.sh` into something
  like `tools/test-package-functional.sh` that:
    - Installs the package in a distro container,
    - Runs the full Python integration suite inside the
      container (or by ssh into a per-distro VM, depending on
      KVM access strategy),
    - Sets `INSTAR_BINARY_PATH=/usr/bin/instar` and
      `INSTAR_TESTDATA_PATH=...`.
- Decide whether the test runs *inside* the container (faster,
  but the container needs Python + pytest + qemu-utils) or
  *outside* with the container providing only the installed
  binary (cleaner, but container shells must surface enough
  back to the host runner).
- Reuse the smoke script's docker run skeleton.

### Phase 4: workflow integration

- Add `merge_group:` trigger to `.github/workflows/functional-tests.yml`.
- Add `package-matrix` job, gated to merge_group only.
- Matrix over the seven distros above.
- Each entry: checkout, Docker, build .deb/.rpm, prepare
  testdata, call the runner script with the right (package,
  distro-image) pair.
- Output: per-distro test result JSON, summarized on the
  merge queue check.

### Phase 5: enable GitHub merge queue

- One-time GitHub setting: Settings -> Branches -> branch
  protection on `develop`/`main` -> "Require merge queue".
- Verify a real PR merge through the queue exercises the
  matrix and gates on it.

## Dependencies and risks

- **GITLAB_TESTDATA_TOKEN** must be available to merge_group
  events. Self-hosted runners already get it; verify the same
  for this new job class.
- **Self-hosted runner pool** sizing -- seven concurrent
  matrix entries running KVM workloads, each pulling distro
  images and running ~30 min of tests. Probably not an issue
  given shakenfist/kerbside-patches handle multi-machine
  cluster topologies in the same merge-queue model, but worth
  measuring on first runs.
- **GitHub merge queue** has its own quirks (no rerun on
  individual entries, retries reset the whole queue). If a
  single distro is flaky it'll block all merges. Plan: any
  matrix entry that fails twice in a row gets temporarily
  marked `continue-on-error: true` while the underlying issue
  is investigated; do not let one flaky distro hold the whole
  queue.

## Out of scope

- Publishing the packages to apt/dnf repositories (PPA, Copr,
  internal mirrors). Separate project.
- Code-signing the packages. Separate project.
- macOS/Windows packaging -- instar requires `/dev/kvm` and
  cannot run there.
- ARM (aarch64) packaging -- explicitly deferred per
  PLAN-release.md until test hardware exists.
