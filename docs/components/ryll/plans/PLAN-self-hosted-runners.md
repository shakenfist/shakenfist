# Self-hosted runner migration (workflow-standards audit)

## Situation

The daily consistency audit
([shakenfist/development `audits/workflow-standards.md`](https://github.com/shakenfist/development/blob/main/audits/workflow-standards.md))
flagged ryll as non-compliant in
[shakenfist/ryll#155](https://github.com/shakenfist/ryll/issues/155):
22 unmarked GitHub-hosted runner references across `ci.yml`,
`manual-build.yml`, and `release.yml`. The standard requires
self-hosted runners except for documented exceptions, which must be
marked with an `audit-ok: github-hosted-runner` comment on (or
immediately above) the line referencing the GitHub-hosted label.

Ryll is a public repository, so GitHub-hosted minutes are free; the
migration is about org-wide consistency and queue control rather
than cost. The audit spec itself names ryll's macOS/Windows builds
as the canonical legitimate exception.

## Mission and problem statement

Bring ryll into compliance with the workflow-standards audit by:

1. Migrating all Linux x86_64 compute jobs to self-hosted runners,
   with the build wrapped in the devcontainer (the pattern instar
   uses, and the same Makefile targets developers run locally).
2. Moving small orchestration jobs to `[self-hosted, static]`.
3. Marking the remaining GitHub-hosted references (macOS, Windows,
   aarch64 Linux — platforms we own no hardware for) with
   `audit-ok` exception markers.

## Execution

Decisions taken (operator-confirmed 2026-07-14):

* **Heavy Linux x86_64 jobs → self-hosted now** (not deferred):
  `ci.yml` lint / fuzz / Linux build, `manual-build.yml` Linux
  build, `release.yml` Linux build and `publish-crates`. These run
  on `[self-hosted, vm, debian-12-docker, l]` — the docker.io
  preinstalled image that `supply-chain.yml` already uses for
  cargo-deny, at l size because cargo builds are compute-bound —
  with cargo wrapped in the devcontainer via `make` targets.
* **Small orchestration jobs → `[self-hosted, static]`**:
  `manual-build.yml` resolve-matrix, `release.yml` check-version,
  github-release, and update-homebrew.
* **audit-ok markers** for macOS (`macos-latest`), Windows
  (`windows-latest`, `windows-11-arm`), and aarch64 Linux
  (`ubuntu-24.04-arm`) matrix entries in all three workflows — no
  self-hosted hardware exists for these platforms. The aarch64
  Linux and Windows ARM labels are the public-repo-only free
  GitHub runners.

Changes made:

* `.devcontainer/Dockerfile` — added `libopus-dev` (parity with
  what CI previously apt-installed, so cargo-deb records `libopus0`
  as a runtime dep) and `openssl` (for `tools/web-smoke.sh --tls`);
  baked in `cargo-deb` and `cargo-generate-rpm`.
* `.devcontainer/fuzz/Dockerfile` — new image layered on
  `ryll-dev` with the nightly toolchain (plus rustfmt) and
  `cargo-fuzz`.
* `Makefile` — factored the repeated `docker run` invocation into a
  shared `DOCKER_RUN` variable (which also forwards
  `CARGO_BUILD_JOBS` when set); new targets: `deb`, `rpm`,
  `web-smoke`, `web-smoke-tls`, `fuzz-devcontainer`,
  `fuzz-fmt-check`, `fuzz-build-%`, `fuzz-smoke-%`, and
  `publish-crates`.
* `tools/publish-crates.sh` — publishes the six workspace crates
  serially in dependency order; run inside the devcontainer with
  `CARGO_REGISTRY_TOKEN` forwarded.
* `.github/workflows/ci.yml` — lint, fuzz, and a new `build-linux`
  job run self-hosted through the Makefile; the build matrix keeps
  the four no-hardware platforms with `audit-ok` markers;
  `automated_reviewer` now also depends on `build-linux`.
* `.github/workflows/manual-build.yml` — resolve-matrix runs on
  `static` and emits a `linux_x86_64` flag consumed by a new
  self-hosted `build-linux-x86_64` job; the hosted matrix skips
  when only linux-x86_64 was requested.
* `.github/workflows/release.yml` — same Linux build split as
  `ci.yml` (artifacts keep their `release-*` names so the
  github-release download pattern is unchanged); `publish-crates`
  runs `make publish-crates` self-hosted; check-version,
  github-release, and update-homebrew run on `static`.
* `.github/actionlint.yaml` — new, matching the house pattern, so
  actionlint knows the self-hosted labels; also fixed the SC2129
  style nit in `release.yml`'s check-version step so actionlint
  runs clean.

## Administration and logistics

### Success criteria

* `scripts/audit-check.py` (development repo) reports
  `self-hosted-runners: pass` for ryll — verified locally.
* `actionlint` runs clean — verified locally (via the
  `rhysd/actionlint` container).
* `pre-commit run --all-files` passes — verified locally.
* A full CI run on the self-hosted runners goes green — to be
  verified by pushing this branch and watching the run.

### Risks and things to watch on first CI run

* **Cold-build wall time**: the self-hosted runners have no
  Swatinem/rust-cache equivalent; each run rebuilds the
  devcontainer image and does a cold cargo build. The compile
  jobs run on l-size runners for this reason, and timeouts are
  set generously (45–120 min). If wall time is still
  unacceptable, `xl` exists in the fleet, or add image/registry
  caching.
* **Disk**: a full ryll workspace build plus the devcontainer
  image is tens of GB; if the VM disk is small this will
  surface as ENOSPC.

### Future work

* The audit also newly reports `readme-absolute-links: fail`
  (17 relative links in README.md) — a separate audit, not part
  of issue #155, and worth its own change.
* Consider pushing prebuilt `ryll-dev` / `ryll-fuzz` images to a
  registry so CI runs pull instead of rebuilding, if image build
  time dominates job wall time.
