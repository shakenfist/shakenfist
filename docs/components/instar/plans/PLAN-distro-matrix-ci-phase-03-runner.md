# Phase 3: In-container matrix runner script

Master plan: [PLAN-distro-matrix-ci.md](/components/instar/plans/PLAN-distro-matrix-ci/).
Planning effort: **medium**. Isolation: none (main tree). Depends on
phase 1 (a package that installs on every matrix distro) and phase 2
(full-version profile selection, so a distro's live qemu-img picks the
right baseline).

## Objective

Produce `tools/test-package-functional.sh <package> <distro-image>`:
given an instar `.deb` or `.rpm` and a distro image, install the package
in that distro's container, install the test prerequisites, and run the
**full Python integration suite** in-container against the **installed
package** (`/usr/bin/instar`) and the **distro's own qemu-img**. This is
the per-matrix-entry inner runner (master-plan decision D3: in-container
execution). Phase 4 calls this script once per matrix row from the
`merge_group` workflow.

The elegant separation this phase realises: **tests come from the source
tree, the binary comes from the package.** The harness already supports
this — `get_instar_binary()` honours `INSTAR_BINARY_PATH`
(`tests/base.py:402`) — so pointing it at `/usr/bin/instar` runs the
in-tree suite against the packaged binary and its packaged guest
binaries under `/usr/lib/instar/`.

## Grounding facts (verified 2026-08-08, correcting the skeleton)

The prior skeleton for this phase said "run `pytest tests/`". That is
wrong and would run zero tests. The real, verified mechanics:

- **The suite runs under `stestr`, not pytest.** The dev image and the
  Makefile targets (`test-integration`, `test-container`) invoke
  `stestr run --exclude-regex test_info_malicious --concurrency 4` from
  inside `tests/` (`Makefile:609`, `:636`). Config is
  `tests/.stestr.conf` (`test_path=.`, `top_dir=.`). The runner must
  `cd tests && stestr run …`.
- **The whole `tests/` tree must be present in the container**, not just
  the package. `tests/base.py:15` does `from helpers.comparators import
  …`; the suite also needs `tests/helpers/`, `tests/golden/`,
  `tests/manifest.json`, `tests/.stestr.conf`, and every `test_*.py`.
  The package supplies only `/usr/bin/instar` + `/usr/lib/instar/*.bin`.
  So the container needs **both**: the repo's `tests/` tree bind-mounted
  in, and the installed package.
- **`stestr` needs a writable `test_path`.** It writes a `.stestr/`
  results directory under the test dir. The proven `test-container`
  target bind-mounts the repo **read-write** (`Makefile:623`,
  `-v "$(CURDIR):/workspace"` with no `:ro`). Mirror that, or copy the
  `tests/` tree to a writable in-container path. Mounting the repo RW is
  the proven path; it deposits a `.stestr/` under `tests/` on the host,
  which is already `.gitignore`d.
- **Test prerequisites are pip deps, not distro packages.**
  `tests/requirements.txt` = `testtools`, `python-subunit`,
  `testscenarios`, `stestr`, `oslo.utils` — installed into a venv via
  `pip install -r tests/requirements.txt` (`Makefile:633`). All are
  pure-Python / have manylinux wheels, so **no compiler is expected**;
  verify this holds on Rocky (see 3b risk).
- **qemu-img must be installed in the distro container.** It is the
  live differential oracle for `test_convert` / `test_compare` /
  `test_dd` / `test_check_*` / `test_map` / `test_measure` /
  `test_oslo_crossval` and live `test_info` (`base.py` shells out to
  `qemu-img` at :129, :480, :559, :668, :753, etc.). Its version also
  drives profile selection — the phase-2 fix makes a 7.2.22 Debian-12
  qemu-img pick `profile-7-2-19`.
- **KVM passthrough is required** (guest ops run under KVM). The
  container-test target uses `--device=/dev/kvm`,
  `-u "$(id -u):$(id -g)"`, and
  `--group-add "$(stat -c '%g' /dev/kvm)"` (`Makefile:619-621`). The
  smoke script uses `--device /dev/kvm` (`test-package-install.sh:94`).
  Reuse this exactly.
- **testdata is LFS-backed and bind-mounted RO** at `/testdata`, with
  `INSTAR_TESTDATA_PATH=/testdata`. In CI it is materialised by
  `tools/ci/prepare-testdata.sh` (canary-guarded; memory:
  testdata_lfs_pointer_drift). This script consumes an already-prepared
  testdata dir on the host and mounts it in — it does **not**
  re-implement LFS handling. Phase 4 wires `prepare-testdata.sh` ahead
  of it, exactly as the existing functional jobs do
  (`functional-tests.yml:314`).
- **`test-package-install.sh` is the docker-run skeleton to generalise**
  (package-path + distro-image args, apt-vs-dnf branch, `/dev/kvm`
  passthrough, `/pkg` mount). Do **not** start from scratch; do **not**
  duplicate its install-smoke assertions — that script stays the
  fast packaging check, and phase 1's `verify-glibc-floor.sh` keeps
  calling it. The new script is the *functional* runner alongside it.
- **This session's docker constraint.** A bare `docker run` in Bash is
  denied, but a `tools/` script that invokes docker internally is the
  allowed pattern (phase 1 ran `verify-glibc-floor.sh` →
  `test-package-install.sh` → `docker run` this way). So the acceptance
  runs below are achievable in-session through the tools/ script.

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | **Write `tools/test-package-functional.sh <package> <distro-image>`.** Start from `test-package-install.sh`'s structure (arg parsing, `realpath`, apt-vs-dnf branch on the package extension, `/dev/kvm` passthrough). The docker invocation must: mount the repo at `/workspace` **RW** (for `stestr`'s `.stestr/`), the prepared testdata at `/testdata` **RO**, and the package dir at `/pkg` **RO**; pass `--device=/dev/kvm`, `-u "$(id -u):$(id -g)"`, `--group-add "$(stat -c '%g' /dev/kvm)"`, `-e HOME=/build` (writable), `-e INSTAR_TESTDATA_PATH=/testdata`, `-e INSTAR_BINARY_PATH=/usr/bin/instar`; `-w /workspace`. Inside the container, in order: (1) install the package (`apt-get update && apt-get install -y /pkg/<file>.deb`, or the dnf form); (2) install python3 + venv + pip + qemu-img via the family table from 3b; (3) `python3 -m venv /build/test-venv && /build/test-venv/bin/pip install -q -r tests/requirements.txt`; (4) `cd tests && /build/test-venv/bin/stestr run --exclude-regex "<EXCLUDE>" --concurrency "${CONCURRENCY:-4}"`. Surface the container exit code as the script's exit code and print a one-line `PASS/FAIL: <pkg> on <distro>` summary. Accept a `TESTDATA_PATH` env (default `../instar-testdata`) mirroring the Makefile, and error clearly if it is missing. |
| 3b | medium | sonnet | none | **Per-family prerequisite install (the fiddly part).** Table it by package manager, reusing the provides-agnostic RPM trick already proven in `probe-qemu-versions.sh`: **Debian/Ubuntu (apt):** `apt-get install -y python3 python3-venv python3-pip qemu-utils` (venv is mandatory under PEP 668). **Fedora (dnf):** `dnf install -y python3 python3-pip qemu-img`. **Rocky/RHEL 9 & 10 (dnf):** `dnf install -y python3 python3-pip` then `dnf install -y qemu-img || dnf install -y /usr/bin/qemu-img` (the package that *provides* qemu-img differs across EL streams). Pin nothing the repo does not already pin; the venv + `tests/requirements.txt` is the single source of Python deps. **Verify** a pure-pip install of `tests/requirements.txt` needs no compiler on Rocky 9 (oldest toolchain); only if it does, add `gcc python3-devel` to the dnf line — do not add them pre-emptively. Detect the manager by probing `command -v apt-get` / `command -v dnf` rather than parsing the image name. |
| 3c | medium | sonnet | none | **`--smoke` fast mode + default exclude set.** Full run is the default (all `test_*.py` except the excludes). Add `--smoke` selecting a fast, KVM-exercising subset — the version/parsing + create/map/info core (e.g. a positive regex over `test_version_detection|test_info_safe|test_create|test_map`) — so the script doubles as a quick local one-distro check, mirroring today's `package-smoke` role. Default `<EXCLUDE>` is `test_info_malicious` (never run malicious images in the matrix); **decide and document** whether to also exclude `test_bench` (benchmarks — slow, perf-sensitive, no oracle value in the matrix). Expose `--concurrency N` (default 4) so phase 4 can tune KVM contention. |
| 3d | low | sonnet | none | **Lint, self-test, docs.** `shellcheck` clean (repo runs shellcheck in pre-commit and CI; add the new script to whatever `tools/run-shellcheck.sh` enumerates if it lists files explicitly). Add a `docs/testing.md` subsection: running the functional suite against an installed package locally (`tools/test-package-functional.sh src/target/debian/instar_*.deb debian:12`), the `--smoke` shortcut, and the tests-from-tree / binary-from-package split. Mention the new script in `AGENTS.md` alongside the other `tools/` entries. Do not touch README (readme-discipline). |

## Acceptance

- `tools/test-package-functional.sh src/target/debian/instar_*.deb
  debian:12` installs the `.deb` and runs the full suite **green** in a
  `debian:12` container against its 7.2.22 qemu-img (this is the row
  phase 2's fix re-points to `profile-7-2-19`; a green full run here is
  the end-to-end confirmation that the fix is correct against a live
  older qemu-img, which phase 2 could only assert structurally).
- Same for the `.rpm` on `rockylinux:9` — proving the dnf path, the
  qemu-img provides-agnostic install, and the phase-1 glibc floor
  together in one run.
- `--smoke` mode works and is fast (minutes, not tens of minutes).
- The script exits non-zero when the in-container suite fails, and the
  failing distro is named in the summary line.
- `shellcheck` clean; `pre-commit run --all-files` clean.

## Runtime / sizing notes (inform phase 4, do not solve here)

- The full suite is large (~2.5k tests; ~15 min across 16 workers on the
  dev host). At `--concurrency 4` in a single container it is
  materially longer, and phase 4 fans out seven such containers. This is
  a phase-4 runner-sizing and timeout concern; phase 3 only has to make
  one row runnable and fast-mode-able. Surface measured wall-clock from
  the two acceptance runs so phase 4 can size timeouts and decide
  concurrency.
- **Per-distro trimming is a phase-4 option, not a phase-3 default.**
  Phase 2 established that the `--qemu-version` baseline tests are
  version-independent (identical on every distro), so most per-distro
  signal comes from the live-oracle differential tests + version-string
  parsing + the packaging/resolver path. The master plan's D3 chose the
  **full** suite per distro (it also catches packaging regressions), so
  phase 3 keeps the full run as default; phase 4 may introduce a
  reduced per-distro selection if wall-clock forces it, and must
  `log()` anything it drops (no silent truncation).

## Execution results (2026-08-09)

Executed on the develop/matrix-ci worktree; the runner drives real
distro containers (deb via apt, rpm via dnf).

- **Runner built and validated on both families.**
  `tools/test-package-functional.sh` installs the package, installs
  prerequisites, copies the `tests/` tree into the container, points the
  harness at `/usr/bin/instar`, and runs the `stestr` suite. shellcheck
  clean. Two script bugs were found and fixed during validation:
  - `--smoke` used bare module names as the stestr selector, which
    substring-matched across modules (`test_create` also selected
    `test_snapshot`'s `test_create_list_agreement`). Anchored each with a
    trailing `\.`.
  - Rocky/RHEL 9's default `python3` is 3.9, but `testtools >= 2.9.1`
    requires Python >= 3.10, so the venv build failed. The runner now
    installs and selects `python3.12`/`python3.11` on the dnf family
    (both are in RHEL 9 AppStream) and picks the newest `python3.x >=
    3.10` present. (Also: `instar` has no `--version` flag; the info line
    was corrected.)
- **rockylinux:9 (.rpm / dnf): PASS, 0 failures** — 1225 passed, 181
  skipped, using `python3.12` and the distro's `qemu-img 10.1.0`
  (→ profile-10-0-0). This proves the dnf path, the provides-agnostic
  `qemu-img || /usr/bin/qemu-img` install, the interpreter selection, and
  the phase-1 glibc floor together. Note: Rocky 9 ships **qemu 10.1.0**,
  not the 8.2 the master-plan matrix table estimated — the estimate is
  stale.
- **debian:12 (.deb / apt): runner works; suite is NOT green — it
  surfaced two real, pre-existing instar parity gaps.** These appear only
  against qemu older than the dev host's 10.x, so single-version CI never
  caught them. Both stem from instar hard-coding the newest qemu output
  while `version.rs` carries only two booleans:
  1. **`map --output=json` emits `"compressed"` unconditionally**
     (`src/vmm/src/main.rs:15141,15149`). qemu added the field at
     **8.2.0** (0/38 profile-6-1-0 baselines carry it; 38/38 of
     profile-10-0-0 do). instar diverges for any pre-8.2 emulation —
     19 `test_map` failures on debian:12; Ubuntu 22.04 (6.2.0) is equally
     affected.
  2. **`snapshot -l` header format** — instar emits `VM SIZE`/`VM CLOCK`
     + `00:00:00.000`; qemu 7.2.22 emits `VM_SIZE`/`VM_CLOCK` +
     `0000:00:00.000`. The live-oracle `test_create_list_agreement`
     fails on debian:12.
- **This revises phase 2's "no version.rs widen needed" conclusion.**
  That held for `info` (its adjacent-profile diffs are harness-
  normalised), but `map` and `snapshot` have genuine parity gaps. Fixing
  them is the widen-vs-document decision phase 2d reserved for
  management, and is out of phase-3 scope (phase 3 is the runner; these
  are instar emitter/version-model changes). Tracked as follow-up; a full
  (non-`--smoke`) debian:12 + ubuntu:22.04 inventory run should scope the
  complete set of pre-10.x parity gaps before the fix is designed.

## Risks

- **KVM-under-load flakiness.** Seven parallel KVM containers, each at
  concurrency 4, can trip the spurious-divergence-under-contention class
  (memory: diffuzz_spurious_divergence_contention). Keep `--concurrency`
  tunable (3c) so phase 4 can dial it down; classify any first-run
  divergence by isolated same-seed replay before calling it a
  regression.
- **testdata must be LFS-materialised before the container sees it.** A
  container that mounts pointer files gives the mass "file format:
  unknown" failure (memory: testdata_lfs_pointer_drift). This script
  assumes an already-prepared `TESTDATA_PATH`; phase 4 must run
  `prepare-testdata.sh` first. Consider a cheap canary check in the
  script (reuse the prepare-testdata canary idea) so a pointer-file
  mount fails loudly here rather than as 200 test failures.
- **RW repo mount side effects.** Mounting the repo RW lets `stestr`
  write `.stestr/` (and `__pycache__`) into the host tree. That matches
  `test-container` today and is `.gitignore`d, but note it; the
  alternative (copy `tests/` into the container, keep the mount RO) is
  cleaner isolation if the RW mount proves troublesome under the
  merge-queue runner's permissions.
- **Rocky pip wheel availability.** If `oslo.utils`/`stestr` pull a dep
  without an EL-compatible wheel, the venv install fails without a
  compiler; 3b's verification catches this and adds `gcc python3-devel`
  only if needed.
- **`GITLAB_TESTDATA_TOKEN` in `merge_group`.** Not this script's
  problem, but the phase-4 caller needs the token available to
  `merge_group` events (memory: testdata_push_token — Maintainer role;
  master-plan dependency note). Flag for phase 4.
