# Development

How to build, test, and contribute to instar. See
[AGENTS.md](https://github.com/shakenfist/instar/blob/develop/AGENTS.md)
for conventions and the Claude Code skills, and
[testing.md](/components/instar/testing/) for the integration test suite in detail.

## Building instar

```bash
# Build the main instar project
make instar

# The binaries will be in src/target/release/
sudo src/target/release/instar info <IMAGE>
sudo src/target/release/instar copy <INPUT> <OUTPUT>
```

### Build and dev containers

The build runs in Docker, and there are **two** devcontainer images:

- **`instar-release`** — a minimal `debian:bullseye` image
  (`src/.devcontainer/build/Dockerfile`) carrying only the toolchain
  that produces the release artifacts: the C linker,
  `protobuf-compiler`, the pinned Rust nightly with `rust-src` +
  `llvm-tools`, `cargo-binutils`, `cargo-deb`, `cargo-generate-rpm`.
  Used by `make instar`, `make deb`, `make rpm`. It is built on
  bullseye deliberately: glibc is forward-compatible, so building the
  host binary against glibc 2.31 lets one artifact run on every distro
  down to Rocky/RHEL 9 and Ubuntu 22.04 (see
  [installation.md](/components/instar/installation/)).
- **`instar-build`** — the full Debian dev/test image
  (`src/.devcontainer/Dockerfile`, base pinned by digest) with
  `qemu-utils`, the libyal parsers, `cargo-fuzz`, `cargo-audit`, and
  `gh`. Used by everything else: `make test`, `make test-rust`, the
  `make test-container*` targets, `make audit`, the fuzz targets, and
  the VS Code devcontainer.

`make clean-devcontainers` removes both. To prove the release binary's
glibc floor empirically, `tools/verify-glibc-floor.sh <deb> <rpm>`
installs the packages on every target distribution and runs
`info`/`create`/`map` under KVM.

### Do not bump the release image's base

`src/.devcontainer/build/Dockerfile` pins `debian:bullseye`, and that
pin is the product rather than an implementation detail: the base's
glibc *is* the floor of every binary we ship, so a newer Debian is a
regression dressed as an update. `renovate.json` excludes the file for
that reason; the dev/test image next door stays managed normally.

Two checks back the pin up, because a comment is not a control:

- `tools/ci/check-glibc-floor.sh` runs on the line immediately after
  `make instar`, in both `build-and-test` and the release workflow, and
  fails if the binary references a symbol above `GLIBC_2.31` — the
  floor published in [installation.md](/components/instar/installation/), not the matrix
  CI's oldest distro (Rocky 9, 2.34), which is looser than the promise
  and would let a smaller base movement through unnoticed. It needs
  nothing but the built binary, so it gates every pull request. The
  placement is deliberate: the unit test run that follows builds bin
  targets into the same `src/target/release/` from the *dev* image, so
  a later check could read a binary that image relinked.
- `tools/verify-glibc-floor.sh` is the empirical version above, and
  remains the real acceptance gate — but it needs containers, packages
  and `/dev/kvm`, so it runs in the merge queue matrix or by hand.

The gap between those two is not hypothetical. Renovate raised the base
to `debian:trixie` in #488; the floor went from `GLIBC_2.30` to
`GLIBC_2.39`; pull request CI passed completely, because nothing on the
pull request path looks at the floor; and the failure surfaced as three
red distros in the merge queue after the change was already on
`develop`. The cheap check exists to make that a pull request failure.

#### When bullseye reaches end of life

**Debian 11 LTS ends on 2026-08-31**
([wiki.debian.org/LTS](https://wiki.debian.org/LTS)). "Do not bump this
tag" is not meant to outlive that date unexamined, so here is what
happens and what the options are.

The failure mode is not a glibc change — it is the image's
`apt-get update` starting to fail once bullseye moves to
`archive.debian.org`, which breaks from-scratch builds of
`instar-release` while an already-built image keeps working. Expect it
to surface as a red CI job on a runner that had no cached image, not as
a support-matrix regression.

No option is free, because "move to the next Debian" is not available:
bookworm ships glibc 2.36, above both the 2.31 we publish and the 2.34
the matrix needs, so taking it silently drops Debian 11, Ubuntu 22.04
and Rocky 9.

- **Pin a `snapshot.debian.org` bullseye base** and point apt at the
  same snapshot. Keeps the floor and the support matrix exactly as
  published; costs a frozen, unpatched build toolchain. The binary's
  security posture depends on the Rust toolchain and our own code far
  more than on the build image's C library, so this is the least
  disruptive option, and the one to reach for if the deadline arrives
  before a decision does.
- **Move to bookworm and narrow the promise** to glibc 2.36. This drops
  Debian 11, Ubuntu 22.04 LTS and Rocky/RHEL 9 — the last of which is
  in the matrix CI and, per
  [PLAN-distro-matrix-ci.md](/components/instar/plans/PLAN-distro-matrix-ci/) decision
  D1, was the reason the floor was set this low. It needs
  `installation.md`, `README.md`, `MAX_GLIBC` in
  `check-glibc-floor.sh`, and the matrix distro list all updated
  together.
- **Change build strategy** — a `zig cc` or `cargo-zigbuild` style
  cross-link against an explicitly chosen older glibc, decoupling the
  floor from the base image entirely. The most work, and the only
  option that stops this recurring every few years.

## Pre-commit hooks

This project uses pre-commit hooks for Rust code quality:

```bash
# Install pre-commit (if not already installed)
pip install pre-commit

# Install the hooks
pre-commit install

# Run manually on all files
pre-commit run --all-files
```

The hooks run rustfmt (formatting) and clippy (linting) on all Rust code via
Docker, ensuring consistent tooling regardless of local Rust installation.

To auto-fix formatting issues:

```bash
./scripts/check-rust.sh fix
```

## Makefile

A Makefile is provided for common development tasks:

```bash
# Show all available targets
make help

# List available prototypes
make list-prototypes
```

**Main Instar Project:**
```bash
# Build instar
make instar

# Clean instar build
make clean-instar

# Show how to run instar
make run-instar
```

**Prototypes:**
```bash
# Build a specific prototype
make build-prototype PROTOTYPE=virtio-block5

# Build all prototypes
make build-all

# Build the shared guest-protocol crate
make guest-protocol

# Build devcontainer for a prototype
make build-prototype-devcontainer PROTOTYPE=virtio-block5

# Build the rust-lint Docker container
make build-lint-container
```

**Cleaning:**
```bash
# Clean a specific prototype's target directory
make clean-prototype PROTOTYPE=virtio-block5

# Clean all build directories (main + prototypes)
make clean-all

# Remove all devcontainer Docker images
make clean-devcontainers

# Remove the rust-lint Docker image
make clean-lint-container

# Remove everything (all targets + all containers)
make distclean
```

**Linting:**
```bash
# Run rustfmt and clippy checks
make lint

# Run with auto-fix
make lint-fix

# Install pre-commit hooks
make install-hooks
```

**Integration Testing:**
```bash
# Create Python venv for tests (testtools/stestr)
make test-venv

# Run safe integration tests
make test

# Run tests with verbose output (shows diffs)
make test-report

# Run all tests including malicious images (explicit opt-in)
make test-malicious

# Run tests inside container (as CI does)
make test-container

# Run split test targets (used by CI for parallel execution)
make test-container-core              # info, check, security, oslo-crossval
make test-container-convert-qcow2    # QCOW2/VMDK/RAW convert + compare
make test-container-convert-vhd      # VHD/VHDX convert (slowest)

# Clean test artifacts
make clean-tests
```

**Fuzz Testing:**
```bash
# Build a single coverage-guided fuzz target (uses the devcontainer)
make fuzz-build FUZZ_TARGET=fuzz_resize_planners

# Build every coverage-guided fuzz target
make fuzz-build

# Run a single target for a bounded wall-clock budget (seconds; default 60)
make fuzz-run FUZZ_TARGET=fuzz_resize_planners FUZZ_DURATION=300

# Run the seven snapshot shell harnesses (live differential
# verification against qemu-img; needs a built instar + /dev/kvm)
make snapshot-harnesses
```

See the "Coverage-Guided Fuzzing" section below for the target list and the
nightly CI rotation.

**Running:**
```bash
# Show run instructions for a prototype
make run PROTOTYPE=virtio-block5
```

## What the integration tests cover

The integration tests compare `instar info` output against `qemu-img info` to
verify drop-in replacement compatibility, validate `instar check` against
deliberately corrupt test images, cross-validate `instar compare` output
against `qemu-img compare`, and cross-validate `instar convert` output against
`qemu-img convert`. oslo.utils `format_inspector` cross-validation tests
verify that instar's format detection, safety checks, and virtual size
reporting agree with OpenStack's image safety gate. Adversarial image tests
verify safe handling of compression bombs, circular/deep backing chains,
integer overflow headers, boundary value edge cases (refcount order,
oversized virtual sizes, VMDK grain sizes, VHDX dual headers, BAT beyond EOF),
and format confusion attacks (polyglot files, truncated headers, VMDK
descriptor attacks). CVE reproduction tests verify that 6 known qemu-img CVEs
(CVE-2024-32498, CVE-2015-5163, CVE-2022-47951, CVE-2015-5162, CVE-2014-0223,
CVE-2024-4467) are fully mitigated by instar's architecture.
`tests/test_snapshot.py` adds 94 snapshot-subcommand tests: the
12-image list matrix against cross-version baselines, 12 JSON golden
comparisons with a structural cross-check, mutation round-trips
(create/delete/apply) with `qemu-img check` post-op assertions, error paths
and qcow2-only enforcement, and empty-table behaviour. JSON goldens live in
`tests/golden/snapshot-list/`. Test images are in the sibling
`instar-testdata/` repository.

See [testing.md](/components/instar/testing/) for the full test suite documentation, and
`testdata/README.md` for the test image catalogue (benign, malicious,
edge-case, and AFL-discovered images).

## Directory structure

```
instar/
├── .devcontainer/  # Development containers
│   └── rust-lint/  # Stable Rust for linting
├── src/            # Main instar implementation
│   ├── vmm/        # Virtual machine monitor (host-side)
│   ├── core/       # Core guest initialization
│   ├── shared/     # Shared library code
│   ├── crates/     # Shared format parsing crates (no_std)
│   │   ├── qcow2/  # QCOW2 header, L1/L2, decompression, refcounts
│   │   ├── raw/    # MBR/GPT partition table detection
│   │   ├── vhd/    # VHD footer, dynamic header, BAT parsing
│   │   ├── vhdx/   # VHDX headers, region table, metadata, BAT, CRC-32C
│   │   ├── vmdk/   # VMDK4 header and descriptor parsing
│   │   ├── luks/   # LUKS header parsing, KDF, AFsplitter, decryption
│   │   ├── vdi/    # VDI header parsing, block-map lookup
│   │   ├── parallels/ # Parallels header parsing, BAT lookup
│   │   ├── qcow1/  # QCOW1 (v1) header, L1/L2 block-lookup
│   │   ├── dmg/    # DMG koly trailer, chunk-table, chunk lookup
│   │   └── ...     # Per-operation planner crates (measure, create,
│   │               # resize, rebase, commit, snapshot)
│   ├── operations/ # Pluggable operations (info, copy, check, compare, convert, measure, create, resize, rebase, commit, map, snapshot, amend, dd, bitmap, bench)
│   └── build.sh    # Build script
├── crates/         # Shared Rust crates
│   └── guest-protocol/ # Protocol Buffers messaging for guests
├── prototypes/     # Experimental implementations (reference)
│   ├── helloworld/     # Minimal KVM VMM with bare-metal guest
│   ├── helloworld2/    # Same, using rust-vmm vm-memory crate
│   ├── virtio-block/   # Virtio-block device emulation
│   ├── virtio-block2/  # With guest-protocol integration
│   ├── virtio-block3/  # With configurable sector sizes
│   ├── virtio-block4/  # With performance statistics
│   ├── virtio-block5/  # With ioeventfd optimization
│   ├── virtio-block6/  # With sparse/dynamic output support
│   ├── pluggable/      # Modular operations architecture
│   ├── pluggable2/     # Separate binary loading for operations
│   └── info/           # Image format detection (qemu-img info)
├── scripts/        # Build and check scripts
├── tests/          # Integration tests (Python/testtools)
│   ├── base.py         # Base test class
│   ├── manifest.json   # Test image definitions
│   ├── helpers/        # Test utilities
│   └── test_*.py       # Test files
├── docs/           # Design documents and research
│   ├── index.md    # Documentation index
│   ├── usage.md    # Platform usage analysis (oVirt, Proxmox, OpenStack)
│   ├── security.md # CVE analysis for image handling
│   ├── qcow2/      # QCOW2 format documentation
│   ├── vmdk/       # VMDK format documentation
│   └── raw/        # Raw format documentation
├── testdata/       # Test images for security validation
│   ├── benign/     # Safe test images (qcow2, raw, vmdk, vhdx, vpc)
│   ├── malicious/  # CVE exploit images (DANGEROUS)
│   └── downloaded/ # External test images (CirrOS, QEMU iotests, etc.)
├── Makefile        # Build and development automation
├── CHANGELOG.md    # Release history
├── SECURITY.md     # Vulnerability reporting and security policy
└── README.md
```

## Releases

See [CHANGELOG.md](https://github.com/shakenfist/instar/blob/develop/CHANGELOG.md)
for release notes.

Release artifacts (pre-compiled Linux binaries) are published to
[GitHub Releases](https://github.com/shakenfist/instar/releases)
via the release workflow (`.github/workflows/release.yml`). Tags
are signed with Sigstore. To cut a release:

```bash
make release VERSION=0.2.0
git push origin HEAD
git push origin v0.2.0
```

## GitHub automation

This project uses Claude Code-powered GitHub automation for PR management.

### Bot commands

Comment on a PR with these commands (requires write access):

| Command | Description |
|---------|-------------|
| `@shakenfist-bot please re-review` | Request a fresh automated code review |
| `@shakenfist-bot please attempt to fix` | Attempt to fix failing tests |
| `@shakenfist-bot please address comments` | Address automated review comments |

The "address comments" command extracts the structured JSON review from the PR
comment (embedded in a collapsed `<details>` section) and creates one commit per
actionable item (those marked with `action: fix` or `action: document`). If Claude
disagrees with a suggestion, it will explain its rationale instead of making changes.

### GitHub issues

The automated reviewer creates GitHub issues for actionable items (fix/document).
These issues are linked in the review comment with "Closes #N" syntax, so they're
automatically closed when the PR merges.

### Workflows

- **Automated Review**: same-repository PRs automatically receive code review
  after CI passes, and GitHub issues are created for actionable items
- **Test Fixing**: On-demand test failure resolution via PR comment
- **Comment Addressing**: On-demand resolution of review feedback via PR comment

Pull requests from forks are not reviewed automatically. The reviewer runs
Claude Code with `--dangerously-skip-permissions` on a runner holding a token
with `pull-requests: write`, and the PR diff it reads is untrusted input, so a
prompt injection in a fork's diff could reach a write-capable token. Fork
contributions are reviewed by a human instead; asking a maintainer to push the
branch to this repository will get it the automated review as well.

The reviewer itself is not defined here. `automated_reviewer` in
`.github/workflows/functional-tests.yml` is a thin caller which names this
project's test jobs in its `needs:` list -- the "CI passed" gate -- and
delegates everything else to
`shakenfist/actions/.github/workflows/pr-auto-review.yml`, which is shared
across the Shaken Fist projects.

See `.github/workflows/` for implementation details.

### Self-hosted runners and Docker

Almost every job in this repository runs on the self-hosted runner pool
(`[self-hosted, debian-12, ...]`), and those runners do **not** ship
Docker. Since instar is built and tested inside the devcontainer image,
any job that runs `docker`, `make instar`, `make test-rust`, `make lint`
or any other container-backed Makefile target must install it first:

```yaml
    env:
      DOCKER_BUILDKIT: 1

    steps:
      - name: Install Docker
        run: |
          sudo apt-get update
          sudo apt-get install -y docker.io
          sudo systemctl start docker
          sudo chmod 666 /var/run/docker.sock
```

Omitting the step does not fail at job start -- it fails part way through
with `docker: command not found`, whenever the first container command is
reached.

### Merge queue and the `develop` ruleset

`develop` is gated by a repository **ruleset** named "Develop branch"
(not classic branch protection — the whole Shaken Fist fleet uses
rulesets). It requires merges to go through GitHub's merge queue, which
is what runs the seven-distro package matrix; see
[testing.md](/components/instar/testing/) for what runs on a pull request versus in the
queue.

The configuration is recorded here so it can be recreated if the
repository ever is. It mirrors `shakenfist/shakenfist`'s ruleset of the
same name:

| Setting | Value |
|---------|-------|
| Ruleset | "Develop branch", id `20783686` (created 2026-08-12) |
| Target | `refs/heads/develop` |
| Enforcement | active |
| Bypass | team `shakenfist/sf-can-skip-merge-queue`, mode `always` |
| Rules | `deletion`, `non_fast_forward`, `merge_queue`, `pull_request`, `required_status_checks` |
| Required checks | `Can enqueue` and `Can merge` (GitHub Actions, integration 15368) |
| Queue grouping | `ALLGREEN`, `max_entries_to_build: 1`, `max_entries_to_merge: 5` |
| Queue merge method | `MERGE`, min 1 entry, 5 minute wait |
| Check timeout | 360 minutes |
| Required approvals | 0 (`dismiss_stale_reviews_on_push: true`) |

**`Can merge` was added second, and the gap between the two was not
theoretical.** `Can merge` only runs on `merge_group` events, so until
a real merge group had executed GitHub had never seen that check
context — and requiring a context that has never reported blocks every
merge, on a branch that had no protection to fall back to. The ruleset
therefore shipped on 2026-08-12 requiring `Can enqueue` alone, and
`Can merge` was added on 2026-08-15 once the queue had made the context
exist.

In between, two PRs merged through the queue **without the matrix
gating them**, because a job that is *skipped* reports success while a
job that never runs reports nothing at all. `Can enqueue` carries an
`if` test that excludes `merge_group`, so inside a merge group it skips,
reports success, and satisfies the only required check. Both merges
followed the same clock: `Can enqueue` skipped, and GitHub merged the
entry thirty one seconds later with the seven-distro matrix still
running. #496's matrix then took until 14:10Z to finish, and was green.
#493's was not — Rocky 9 went red seventeen minutes after that PR had
already merged, Ubuntu 22.04 and Debian 12 followed, and the `Can
merge` aggregate reported `failure` seventy five minutes post-merge.
The regression it was reporting (the release image's glibc floor) sat
on `develop` until #496 fixed it.

If you are recreating this repository, add both contexts up front only
if you can also arrange for a merge group to have run; otherwise
reproduce the two-step order above. To add a required context to a live
ruleset, transform the exported object rather than PUTting the `GET`
response back unchanged — the response carries fields that are not part
of the update schema (`id`, `_links`, and `"parameters": null` on the
rules that take no parameters):

```bash
gh api repos/shakenfist/instar/rulesets/20783686 | jq '{
  name, target, enforcement, conditions,
  bypass_actors: [.bypass_actors[] | {actor_id, actor_type, bypass_mode}],
  rules: [.rules[]
    | if .type == "required_status_checks"
      then .parameters.required_status_checks
             += [{context: "Can merge", integration_id: 15368}]
      else . end
    | if .parameters == null then {type: .type} else . end]}' > ruleset.json
gh api -X PUT repos/shakenfist/instar/rulesets/20783686 --input ruleset.json
```

Read it back and diff it against the intent afterwards; dropping
`bypass_actors` from the payload silently removes the bypass.

Two other settings deserve explanation:

- **`max_entries_to_build: 1`** bounds the cost of the matrix. Only one
  merge group builds at a time, so the seven-wide fan-out is seven
  on-demand runners for one PR, not seven per queued PR.
- **The required checks are the two aggregate jobs, never the individual
  matrix entries.** Entry names change whenever the distro list does,
  and a required check whose name no longer exists blocks every merge
  permanently. `can_enqueue` aggregates the pull-request jobs;
  `can_merge` aggregates the merge-queue jobs. Both use `always()` plus
  an event test so they always report, because a required check that
  never reports leaves the queue waiting forever.

To inspect or recreate it:

```bash
gh api repos/shakenfist/instar/rulesets --jq '.[] | "\(.id) \(.name) \(.enforcement)"'
gh api repos/shakenfist/instar/rulesets/<id>
```

`.github/exported-config/` carries the nightly export of the live
ruleset state, which is the machine-readable companion to the table
above; the export proposes its updates as pull requests rather than
committing directly.

### Differential fuzzing

On-demand differential fuzzing compares instar against qemu-img on randomly
generated images to find behavioral divergences:

```bash
# Run locally (requires instar binary and qemu-img)
python3 scripts/differential-fuzz.py \
    --instar src/target/release/instar \
    --iterations 100 \
    --seed 42

# Trigger via GitHub Actions (workflow_dispatch)
gh workflow run differential-fuzz.yml \
    -f iterations=1000 \
    -f seed=42
```

The fuzzer generates random images (varying format, size, cluster size,
compression, data patterns), runs chains of operations (info, check, convert)
against both tools, and reports divergences with full reproduction details.

When libyal tools are available (`vmdkinfo`, `vhdiinfo`, `qcowinfo`), the
fuzzer also cross-checks instar output against these independent forensic-grade
parsers. This provides a third opinion for QCOW2 (alongside qemu-img) and
fills the gap for VMDK/VHD/VHDX where qemu-img check is unavailable.

See `scripts/differential-fuzz.py` for implementation details.

### Coverage-guided fuzzing

Coverage-guided fuzzing uses `cargo-fuzz` (libFuzzer) to exercise the
parser crates directly without the VMM/KVM stack:

```bash
# Inside the instar-build container:
cd src/fuzz
cargo fuzz run fuzz_qcow2_header -- -max_total_time=60
```

40 fuzz targets cover all parser crates (QCOW2, VMDK, VHD, VHDX,
VDI, Parallels, QCOW1, DMG, RAW, LUKS) including header parsing,
L1/L2 lookup, refcount traversal, and decompression, plus the
create / resize / rebase / commit planners, the qcow2 check-repair
planners (`fuzz_check_repair`), the map extent walkers, the
snapshot table parser (`fuzz_snapshot_parse`), the snapshot
refcount mutators (`fuzz_snapshot_refcount`), the dd window math
(`fuzz_dd_window`), CHS geometry rounding
(`fuzz_chs_rounded_size`), windowed read primitives
(`fuzz_dd_read`), and the qcow2-write planner (`fuzz_qcow2_write`,
which drives the write/copy-on-write planner through the crate's
`sim` harness asserting the `max_rc < 3` COW invariant oracle, and
`fuzz_qcow2_write_growth`). Seed the corpus from `instar-testdata`:

```bash
python3 scripts/extract-fuzz-corpus.py --testdata /path/to/instar-testdata
```

The CI workflow runs nightly at 04:00 UTC. Crashes are minimized and
filed as GitHub Issues with the `security-audit` label immediately.
See `src/fuzz/` for target implementations.

## Build and dev containers

The build runs in two devcontainer images: a minimal `debian:bullseye`
release build image (`src/.devcontainer/build/Dockerfile`, image
`instar-release`) that produces the binary and packages at a low glibc
floor, and the full Debian dev/test image
(`src/.devcontainer/Dockerfile`, image `instar-build`) that runs the
test, fuzz, and audit suites. `make instar`/`deb`/`rpm` use the former;
everything else uses the latter. See
[docs/development.md](https://github.com/shakenfist/instar/blob/develop/docs/development.md)
for which target uses which image and why bullseye.

## RPM dependency generation

`make rpm` runs `cargo generate-rpm`, and `src/vmm/Cargo.toml` sets
`auto-req = "auto"` so the `Requires` list is derived from the built
binary rather than hand-maintained. "auto" is not one implementation:
cargo-generate-rpm uses `/usr/lib/rpm/find-requires` when that path
exists, and otherwise silently falls back to a builtin parser of
`ldd -v` output.

**The fallback is broken and must never be taken.** The builtin parser
strips whitespace out of each `ldd -v` line, so a *weak* symbol-version
reference — which glibc's `ldd` renders as
`libc.so.6 (GLIBC_2.25) [WEAK]` — collapses into the dependency
`libc.so.6(GLIBC_2.25)[WEAK](64bit)`. No glibc package Provides that
string, so the .rpm fails to install on every RPM distro with
`nothing provides libc.so.6(GLIBC_2.25)[WEAK](64bit)`. rpm's own
`elfdeps` ignores `VER_FLG_WEAK` and emits the plain
`libc.so.6(GLIBC_2.25)(64bit)`, which resolves normally.

Both container images therefore install Debian's `rpm` package purely
so `/usr/lib/rpm/find-requires` is present. It is not used to *build*
the package. Do not remove it from either Dockerfile.

This is latent, not theoretical: it only bites when the toolchain
starts marking glibc version needs weak. `nightly-2026-07-22` emitted
`Flags: none` for `GLIBC_2.18/2.25/2.28/2.29/2.30`;
`nightly-2026-08-17` emitted `Flags: WEAK` for the same five, which
took out the Rocky 9, Rocky 10 and Fedora matrix jobs in the merge
queue. `.deb` packaging is unaffected — cargo-deb uses
`dpkg-shlibdeps`. To inspect what a built package actually requires:

```bash
rpm -qpR src/target/generate-rpm/instar-*.rpm
readelf -V src/target/release/instar   # Version needs section
```

Note that this class of breakage is only caught by the distro matrix,
which runs in the merge queue rather than in the pull-request gate.

## Toolchain pinning

Both devcontainer Dockerfiles pin the same Rust nightly via
`ARG RUST_NIGHTLY=nightly-YYYY-MM-DD` — a broken floating nightly
otherwise breaks every from-scratch image build (a 2026-07-24 nightly
ICE'd compiling tokio inside `cargo install cargo-audit` and took out
CI's "Build devcontainer" step). Renovate cannot bump rustup toolchain
pins; instead the weekly `rust-nightly-bump` workflow
(`tools/ci/bump-rust-nightly.sh`) rewrites and test-builds **both**
images, then instar and the Rust test suite, against the newest
published nightly and opens a bump PR only when everything passes. Do
not un-pin the toolchain, and do not bump the pin by hand without at
least building both images. (The lint container is separate and uses a
stable `rust:` tag Renovate does manage; the dev image's Debian base is
pinned by digest and Renovate walks it forward.)

## CI tooling guards

The `ci-tooling` CI job runs the cheap guards over CI's own tooling:
the test-partition check below, plus
`tools/ci/test-report-fuzz-crash.sh` and
`tools/ci/test-pick-fuzz-artifact.sh` for the coverage-fuzz helpers
(see "Crash reporting" in [docs/testing.md](/components/instar/testing/)). It is
also the job named in `automated_reviewer`'s `needs` list, which is
required to list every job that can fail a PR.

Integration tests are split across several CI jobs by stestr regex
selectors (the `test-container-*` Makefile targets).
`tools/ci/check-test-partition.sh` fails if
any `test_*.py` test is run by **no** pull-request job. When you add a
new integration test module or a new integration job, the guard
validates that the new partition still covers everything; an orphan is
a hard CI failure. Deliberate exclusions live in an allowlist in
`tools/ci/check-test-partition.py` (currently just the malicious
suite). See [docs/testing.md](/components/instar/testing/).

## GitHub Automation

The project includes Claude Code-powered GitHub automation for common PR tasks.

## Available Bot Commands

Comment on a PR with these commands (requires write access to the repository):

- `@shakenfist-bot please re-review` - Request a fresh automated code review
- `@shakenfist-bot please retest` - Re-run functional tests without pushing a new commit
- `@shakenfist-bot please attempt to fix` - Have Claude attempt to fix failing tests
- `@shakenfist-bot please address comments` - Have Claude address automated review
  feedback, creating one commit per valid issue

## How Automated Review Works

The review job lives in the shared workflow
`shakenfist/actions/.github/workflows/pr-auto-review.yml`, not in this
repository. `automated_reviewer` in `functional-tests.yml` is only the caller:
its `needs:` list names this project's test jobs, which is what gates the
review on CI passing. The runner, the timeout, the bot-commit check and the
fork restriction all live in the shared workflow.

Reviews run on same-repository pull requests only. The reviewer runs Claude
Code with `--dangerously-skip-permissions` while holding a token with
`pull-requests: write` and `issues: write`, and the PR diff is untrusted
input, so a fork PR is skipped rather than reviewed. Fork PRs get a skipped
job, not a failing one.

The automated reviewer outputs structured JSON that is:
1. Validated against a JSON schema (`tools/review-schema.json`)
2. GitHub issues are created for actionable items (action=fix or action=document)
3. Rendered to human-readable markdown and posted as a PR comment
4. The raw JSON is embedded in a collapsed `<details>` section at the end of
   the comment, allowing the address-comments automation to extract it

The review comment includes links to the created issues with "Closes #N" syntax,
so issues are automatically closed when the PR merges.

Each review item has an `action` field:
- `fix` - Must be fixed before merging (creates an issue)
- `document` - Documentation should be added (creates an issue)
- `consider` - Optional improvement (reviewer suggestion)
- `none` - Informational observation only

## How Automated Comment Addressing Works

When you trigger `@shakenfist-bot please address comments`:

1. The bot extracts the `review.json` from the PR review comment (from the
   embedded `<details>` section)
2. It extracts items where `action` is `fix` or `document`
3. For each actionable item, Claude Code:
   - Analyzes whether the item should be addressed
   - If valid: makes the fix and runs pre-commit, staging nothing
   - If disagreeing: provides a rationale explaining why
4. CI stages what the fix touched, via
   `tools/ci/stage-autofix-changes.sh --tracked-only` and the new files
   the item created.
5. Each valid fix gets its own commit with attribution
6. All commits are pushed and a summary is posted to the PR

This allows reviewers to cherry-pick or drop individual fixes as needed.

Claude Code edits the working tree and does not reliably stage, and the
script judges an item by reading the index, so before step 4 existed an
unstaged fix reached that test with an empty index and was recorded as
"No changes needed" (#510). Past runs of this workflow are worth
re-reading with that in mind before their skipped rows are believed.

New files are staged here, unlike in the fuzz autofix, which refuses an
attempt that created one: a review item can legitimately ask for a new
file, and the result lands on a pull request a human reads before it goes
anywhere. Only files the item itself created, though -- the untracked and
ignored listings are snapshotted before Claude runs and compared
afterwards, so a scratch file that was already in the tree is not swept
into someone else's commit.

Two kinds of file are named rather than staged, because neither can go in
the commit:

- Edits under `.github/workflows/`, because a commit touching one cannot
  be pushed with the token the workflow holds -- and since the loop
  commits per item and pushes once at the end, that failure would discard
  every other item's commit with it. An item whose whole fix was a
  workflow edit is reported as "Not pushable".
- New files matching a `.gitignore` rule, which need `git add -f` and a
  human deciding they belong in the tree. `**/*.bin` is in this repo's
  `.gitignore`, which is what a fuzz regression fixture is called. These
  are reported as "Not staged".

Every path that abandons an item resets the work tree with
`tools/ci/reset-autofix-worktree.sh`, so the next item's commit holds
only its own work, and so does the path that succeeds -- the workflow
edits the stager refused are still in the tree after the commit. Because
that reset is unconditional and repo-wide, a local run refuses to start
on a dirty tree unless `--ci` is passed, and refuses an `--output-dir`
inside the work tree.

## Workflow Files

- `.github/workflows/functional-tests.yml` - Main CI, and the caller for the
  shared automated review workflow
  (`shakenfist/actions/.github/workflows/pr-auto-review.yml`)
- `.github/workflows/release.yml` - Release workflow (Sigstore-signed tags, GitHub Releases with pre-compiled binaries)
- `.github/workflows/pr-re-review.yml` - Manual re-review trigger
- `.github/workflows/pr-retest.yml` - Manual retest trigger via bot command
- `.github/workflows/pr-fix-tests.yml` - Test failure fixing
- `.github/workflows/pr-address-comments.yml` - Review comment addressing
- `.github/workflows/test-drift-fix.yml` - Scheduled/on-demand test maintenance
- `.github/workflows/differential-fuzz.yml` - On-demand differential fuzzing (instar vs qemu-img + libyal)
- `.github/workflows/coverage-fuzz.yml` - Coverage-guided fuzzing of parser crates (nightly + PR)
- `.github/workflows/fuzz-autofix.yml` - Automated fuzzer bug fix (daily Claude Code, 30-turn limit)
- `.github/workflows/rust-nightly-bump.yml` - Weekly devcontainer Rust nightly pin bump (see "Toolchain pinning" above)
- `.github/workflows/codeql-analysis.yml` - CodeQL static analysis (push/PR to develop, plus weekly cron)
- `.github/workflows/supply-chain.yml` - gitleaks secret scanning on debian-13 (PR/push, plus weekly cron)

The self-hosted runners have no Docker preinstalled, so any job touching
`docker` or a container-backed Makefile target needs an "Install Docker"
step -- see "Self-hosted runners and Docker" in `docs/development.md`.

## Scripts

- `tools/address-comments-with-claude.sh` - Addresses review comments (reads JSON); CI stages each fix for it via `tools/ci/stage-autofix-changes.sh --tracked-only` (see "How Automated Comment Addressing Works" above)
- `tools/render-review.py` - Renders review JSON to markdown, and validates
  it against `tools/review-schema.json` in `--validate` mode, which is how
  `tools/address-comments-with-claude.sh` checks the review it was handed
- `tools/review-schema.json` - JSON schema for review output validation
- `scripts/differential-fuzz.py` - Differential fuzzing script (instar vs qemu-img + libyal)
- `scripts/extract-fuzz-corpus.py` - Seeds + restores the coverage-fuzz corpus from instar-testdata
- `tools/ci/fuzz-tier.sh` - Computes tiered nightly per-target fuzz durations
- `tools/ci/report-fuzz-crash.sh` - Files the `security-audit` issue for a coverage-fuzz crash (bounds the log excerpt, dedups against open issues; see "Crash reporting" in `docs/testing.md`)
- `tools/ci/pick-fuzz-artifact.sh` - Chooses which libFuzzer artifact to report as the reproducer
- `tools/ci/test-report-fuzz-crash.sh`, `tools/ci/test-pick-fuzz-artifact.sh` - Tests for those two; run them after any change (the `ci-tooling` CI job does)
- `tools/ci/check-glibc-floor.sh` - Fails if the built `instar` binary needs a glibc above the published floor (`GLIBC_2.31`, Debian 11; see `docs/installation.md`). Runs immediately after `make instar` in both `build-and-test` and the release workflow. Do not raise the ceiling to make it pass: it means the release image's base moved, and `src/.devcontainer/build/Dockerfile` must stay on `debian:bullseye`
- `tools/ci/test-check-glibc-floor.sh` - Tests for that check; the `ci-tooling` CI job runs it
- `tools/ci/stage-autofix-changes.sh` - Stages the tracked edits a Claude Code autofix run left in the working tree, before the steps that judge whether a fix exists read the index, and refuses the attempt (exit 3) if it created a file that cannot be staged safely. Called from `fuzz-autofix.yml`, and in `--tracked-only` mode from `tools/address-comments-with-claude.sh`; see "Automated bug fixes" in `docs/testing.md`
- `tools/ci/test-stage-autofix-changes.sh` - Tests for that stager; the `ci-tooling` CI job runs it
- `tools/ci/reset-autofix-worktree.sh` - Discards staged edits, unstaged edits and new files, keeping ignored build output, and fails if the tree is still dirty afterwards; called on every path that finishes a review item, so the next item's commit holds only its own work
- `tools/ci/autofix-artifact-patterns.sh` - Sourced, not executed: the editor leftovers and build output that are not part of a fix, shared by the stager and the review-comment addresser so the two cannot drift
- `tools/ci/test-reset-autofix-worktree.sh` - Tests for that reset; the `ci-tooling` CI job runs it
- `tools/ci/test-address-comments-staging.sh` - Drives the `tools/address-comments-with-claude.sh` item loop against a scratch repo with `claude` and `gh` stubbed; the `ci-tooling` CI job runs it
