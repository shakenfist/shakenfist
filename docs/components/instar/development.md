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
`tests/test_snapshot.py` (phase 11) adds 94 snapshot-subcommand tests: the
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
