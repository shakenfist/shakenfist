# Coverage-guided fuzzing of parser crates (Phase 6)

## Status: In Progress (Steps 1-5 infrastructure merged, extended runs not yet complete)

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (project structure,
command-line argument handling, input source abstractions, output
formatting, error handling), and ground your answers in what the
code actually does today. Do not speculate about the codebase when
you could read it instead. Where a question touches on external
concepts (cargo-fuzz, libFuzzer, coverage instrumentation, LLVM
sanitizers), research as needed to give a confident answer. Flag
any uncertainty explicitly rather than guessing.

## Situation

Phases 1-5 of the security audit (PLAN-audit.md) are complete.
Phase 3 (differential fuzzing) compares instar against qemu-img on
randomly generated valid images, but does not explore the malformed
input space that coverage-guided fuzzing excels at. The `no_std`
parser crates (qcow2, vmdk, vhd, vhdx, raw, luks) can be fuzzed
directly without the full VMM/KVM stack, making this the
highest-value approach for finding parser bugs in adversarial
input.

## Mission and problem statement

Build `cargo-fuzz` (libFuzzer) harnesses for each format parser
crate, seed them with the existing test image corpus, and run them
for extended periods to find crashes, hangs, and panics. Integrate
the fuzzing into CI as a `workflow_dispatch` workflow for on-demand
and nightly execution. Any crashes found should be minimised and
added as regression test images.

## Architecture overview

### Parser crate I/O model

All parser crates are `#![no_std]` and perform I/O exclusively
through `CallTable` function pointers passed as parameters:

```rust
#[repr(C)]
pub struct CallTable {
    pub magic: u32,               // 0x494D4147
    pub version: u32,
    pub read_input_sector: unsafe extern "C" fn(u32, u64, *mut u8, usize) -> bool,
    pub get_input_capacity: unsafe extern "C" fn(u32) -> u64,
    pub get_input_sector_size: unsafe extern "C" fn(u32) -> usize,
    // ... (20+ additional function pointers for output, progress, etc.)
}
```

The parser functions use macro-generated sector-cached readers
(`cached_read!`) that call `read_input_sector(device_idx, sector,
buffer, len)` to fetch data one sector at a time. This design
decouples parsing from real device I/O and makes it possible to
substitute a fuzz-input-backed implementation.

### What the harnesses must provide

1. **A mock CallTable** with function pointers that read sectors
   from the fuzzer's input buffer instead of a virtio-block device.
   Since `extern "C"` function pointers cannot capture state, the
   input buffer must be stored in thread-local or static storage.

2. **A standard allocator.** The parser crates use `alloc` (Vec,
   String) for compression and some internal structures. Under
   normal operation a bump allocator is provided by the operation
   binary. Under fuzzing, the standard library's allocator suffices
   since harnesses are `std` binaries.

3. **Scratch memory buffers.** Several parsers allocate temporary
   buffers in the guest's scratch memory region (0x300000-0xFF0000)
   via raw pointer arithmetic. The harness must provide equivalent
   heap-allocated buffers and pass their addresses to parser
   functions.

### Crates to fuzz (in priority order)

| Crate | Dependencies | Complexity | Priority |
|-------|-------------|------------|----------|
| raw | none | Trivial (MBR/GPT detection) | Low (small attack surface) |
| vhd | shared | Low (footer + BAT) | High (simple, quick wins) |
| vhdx | shared | Medium (CRC-32C, metadata GUIDs) | High |
| vmdk | shared, optional miniz_oxide | Medium (descriptor parsing, grain lookup) | High |
| qcow2 | shared, optional miniz_oxide/ruzstd/aes | High (L1/L2, refcount, compression, encryption) | Critical |
| luks | shared, optional crypto crates | Medium (header parsing, KDF) | Medium |

### What we are NOT fuzzing in this phase

* The VMM (host-side) code -- this was audited in Phase 5 and
  runs in a different trust domain.
* The virtio-block emulation -- requires full KVM stack.
* Cross-format backing chain resolution -- requires multi-device
  simulation. Consider as future work once single-format harnesses
  are stable.
* The `core` binary entry point and operation binaries -- these
  are integration-level code better covered by differential
  fuzzing (Phase 3).

## Detailed plan

### Step 0: Prerequisites

#### 0a. GitLab push token for corpus storage

The fuzz corpus will be stored in the private GitLab repo
`gitlab.home.stillhq.com/private/instar-testdata` alongside
the existing test images (under `custom/fuzz-corpus/`). CI
already clones this repo using `GITLAB_TESTDATA_TOKEN` (read-
only). A separate **`GITLAB_TESTDATA_PUSH_TOKEN`** secret with
`write_repository` scope is used for committing new corpus
entries after nightly fuzzing runs.

**Token usage in the CI workflow:**
* **Read (clone):** `GITLAB_TESTDATA_TOKEN` (existing, read-only).
* **Write (push corpus):** `GITLAB_TESTDATA_PUSH_TOKEN` (new,
  write-capable). Used only in the corpus commit step of the
  coverage-fuzz workflow.

This separation keeps the existing read-only token unchanged
for all other workflows (functional tests, test-drift-fix,
differential fuzzing), limiting write access to just the
coverage fuzzing corpus update.

**Action required:** create a GitLab personal access token (or
project access token) with `write_repository` scope for the
`private/instar-testdata` project, and add it as
`GITLAB_TESTDATA_PUSH_TOKEN` in the instar GitHub repo secrets.

### Step 1: Harness infrastructure

Create a `fuzz/` directory at the workspace root (`src/fuzz/`)
with the standard `cargo-fuzz` layout. This is a separate Cargo
project that depends on the parser crates as library dependencies.

#### 1a. Mock CallTable module

Create a shared harness support module (`src/fuzz/src/harness.rs`
or similar) that provides:

* **`FuzzInput` struct** -- wraps a `&[u8]` fuzzer input and
  exposes it as a virtual disk image with configurable sector
  size (default 512 bytes).
* **Thread-local storage** for the current `FuzzInput`, so that
  `extern "C"` function pointers can access it without captured
  state.
* **`build_call_table()` function** -- returns a `CallTable`
  populated with mock function pointers:
  - `read_input_sector`: reads from the thread-local `FuzzInput`,
    returns `false` for out-of-bounds sectors (mimicking a
    truncated image).
  - `get_input_capacity`: returns the fuzzer input length.
  - `get_input_sector_size`: returns 512 (configurable).
  - `get_input_device_count`: returns 1 (single device, no
    backing chain).
  - Output functions (`write_output_sector`, etc.): no-op or
    write to a bounded discard buffer.
  - Progress/error/debug functions: no-op (or optionally log
    to stderr for debugging).
  - Config functions (`get_operation_config`, `get_chain_config`):
    return empty/default configs.
  - Result-reporting functions (`send_info_result`, etc.): no-op
    or capture results for optional validation.

The mock must handle the following edge cases:
* Zero-length input (empty file).
* Input shorter than one sector.
* Sector reads at the boundary of the input (partial last sector
  should be zero-padded).
* Very large sector numbers (must not panic or allocate
  unboundedly).

#### 1b. Scratch memory simulation

Parser functions that use scratch memory (e.g. QCOW2 overlap
bitmap in `check`, compression buffers) reference addresses in
the 0x300000-0xFF0000 range. In a fuzz harness running under
`std`, these addresses are not mapped.

Two approaches (choose one during implementation):

**Option A: Heap-allocated scratch buffers.** Allocate a
Vec<u8> of SCRATCH_MEM_SIZE bytes and pass its base address
to parser functions that need scratch memory. This requires
that parser functions accept scratch pointers as parameters
(verify this is the case).

**Option B: Refactor parser APIs.** If parser functions
hard-code scratch addresses via constants, create thin wrapper
functions that redirect scratch access to heap buffers. This
may require minor refactoring of parser crate public APIs to
accept scratch base/size parameters.

Document which approach was chosen and why.

#### 1c. Cargo-fuzz project setup

```
src/fuzz/
  Cargo.toml          # cargo-fuzz project
  src/
    harness.rs         # Mock CallTable and FuzzInput
  fuzz_targets/
    fuzz_format_detect.rs
    fuzz_qcow2_header.rs
    fuzz_qcow2_l1l2.rs
    fuzz_qcow2_refcount.rs
    fuzz_qcow2_decompress.rs
    fuzz_vmdk_header.rs
    fuzz_vmdk_grain.rs
    fuzz_vhd_footer.rs
    fuzz_vhd_bat.rs
    fuzz_vhdx_header.rs
    fuzz_vhdx_metadata.rs
    fuzz_raw_partition.rs
    fuzz_luks_header.rs
```

The `Cargo.toml` should:
* Depend on `shared`, `qcow2`, `vmdk`, `vhd`, `vhdx`, `raw`,
  and `luks` as path dependencies.
* Enable relevant features (e.g. `decompress`, `decompress-zstd`
  for qcow2; `decompress` for vmdk).
* Use `[profile.release]` with `debug = true` for meaningful
  stack traces on crashes.

### Step 2: Fuzz target implementation

Each fuzz target should follow this pattern:

```rust
#![no_main]
use libfuzzer_sys::fuzz_target;
use fuzz::harness::{set_fuzz_input, build_call_table};

fuzz_target!(|data: &[u8]| {
    set_fuzz_input(data);
    let call_table = build_call_table();
    // Call parser function(s) with &call_table
    // Any panic or crash is automatically captured by libFuzzer
});
```

#### 2a. Format detection target

Fuzz `shared::format_detection::detect_format_from_header()`.
This is the entry point for all operations and routes to
format-specific parsers. Feed the raw fuzz input as the header
buffer.

#### 2b. QCOW2 targets (highest priority)

QCOW2 has the largest attack surface. Split into multiple targets
to give libFuzzer better coverage signal:

* **Header parsing** -- parse the 104-byte (v2) or 112-byte (v3)
  header, validate fields, parse header extensions. This is the
  first code to touch untrusted input.
* **L1/L2 cluster lookup** -- given a parsed header, exercise
  `read_cluster_sectors()` for various virtual offsets. The fuzz
  input serves as both the header and the L1/L2 table data.
* **Refcount lookup** -- exercise `lookup_refcount()` which
  traverses the refcount table and refcount blocks.
* **Decompression** -- exercise `read_compressed_cluster()` (zlib)
  and `read_compressed_cluster_zstd()`. Feed compressed cluster
  data from the fuzz input. This is where compression bombs would
  be caught.

For the L1/L2 and refcount targets, the fuzz input must be large
enough to contain a header plus at least one table. Use a minimum
input size check (e.g. skip inputs < 512 bytes) to avoid wasting
cycles on trivially invalid inputs.

#### 2c. VMDK targets

* **Header and descriptor parsing** -- VMDK has both a binary
  header (VMDK4, 79 bytes) and a text descriptor. The descriptor
  parser handles key-value pairs and extent definitions. Focus on
  descriptor parsing as it processes complex text input.
* **Grain directory/table lookup** -- exercise `GrainLookup` for
  sector reads through the grain indirection tables.

#### 2d. VHD targets

* **Footer parsing** -- 512-byte footer with checksum validation.
  Test both standard footer (at EOF) and copy header (at offset 0
  for dynamic/differencing images).
* **BAT lookup** -- Block Allocation Table traversal for dynamic
  VHD images.

#### 2e. VHDX targets

* **Header and region table parsing** -- dual headers with
  sequence numbers, CRC-32C validation, region table with GUID-
  based entries.
* **Metadata lookup** -- GUID-keyed metadata entries with typed
  parsing (virtual size, block size, logical sector size, etc.).

#### 2f. RAW target

* **Partition table detection** -- `detect_partition_table()` on
  the first sector. Small attack surface but trivial to fuzz.

#### 2g. LUKS target

* **Header parsing** -- LUKS v1 (592-byte header) and v2 (4096-
  byte header with JSON metadata area). Do NOT fuzz KDF execution
  (PBKDF2/Argon2) as it is intentionally slow and would cause
  timeouts. Focus on header field validation and JSON metadata
  parsing for v2.

### Step 3: Corpus seeding

#### 3a. Extract seed corpus from test images

Create a script (`scripts/extract-fuzz-corpus.sh` or Python)
that:

1. Copies all images from `instar-testdata/` into per-target
   corpus directories under `src/fuzz/corpus/`.
2. Filters by format: QCOW2 images go to `corpus/fuzz_qcow2_*`,
   VMDK images to `corpus/fuzz_vmdk_*`, etc.
3. Includes the Phase 2 adversarial images from
   `instar-testdata/custom/audit/`.
4. For header-only targets, truncates images to the first N
   sectors (e.g. 8KB) to keep the corpus compact and avoid
   wasting fuzzer time on data-heavy images.

#### 3b. Synthesise minimal seed inputs

For each target, also create a handful of hand-crafted minimal
inputs:

* **QCOW2:** Minimal valid v2 header (104 bytes, cluster_bits=16,
  l1_size=0). Minimal valid v3 header (112 bytes).
* **VMDK:** Minimal VMDK4 header (79 bytes) + minimal descriptor.
* **VHD:** Minimal 512-byte footer with valid cookie and checksum.
* **VHDX:** Minimal file identifier + two headers + region table.
* **RAW:** 512 bytes with valid MBR signature (0x55AA). 512 bytes
  with GPT protective MBR + EFI signature.

These ensure the fuzzer can explore beyond the "invalid magic"
early-exit path from the very first iteration.

### Step 4: Local execution and validation

Before setting up CI, validate that all harnesses work locally:

1. Build each target: `cargo fuzz build <target>`
2. Run each target for a short duration (60 seconds) to confirm
   it finds no immediate crashes and achieves non-trivial
   coverage.
3. Check coverage with `cargo fuzz coverage <target>` and review
   the LLVM coverage report to verify that parser code is being
   reached (not just the harness scaffolding).
4. Deliberately introduce a bug (e.g. remove a bounds check) and
   confirm the fuzzer finds it within a reasonable time.

If any target fails to build due to `no_std` incompatibilities or
missing symbols, fix the issue before proceeding. Document any
parser API changes needed.

### Step 5: CI workflow

Create `.github/workflows/coverage-fuzz.yml` following the
patterns established by `differential-fuzz.yml`.

#### 5a. Workflow triggers

```yaml
on:
  schedule:
    - cron: '0 4 * * *'    # Nightly at 04:00 UTC (after differential at 02:00)
  workflow_dispatch:
    inputs:
      duration:
        description: 'Fuzz duration per target (seconds)'
        default: '3600'    # 1 hour per target
      targets:
        description: 'Comma-separated target names (empty = all)'
        default: ''
      seed_corpus_only:
        description: 'Only run seed corpus (no fuzzing)'
        default: 'false'
  pull_request:
    paths:
      - 'src/fuzz/**'
      - 'src/crates/**'
      - 'src/shared/**'
```

#### 5b. Runner and environment

* Runner: `self-hosted, debian-12, xl` (same as differential
  fuzzing). Coverage-guided fuzzing is CPU-intensive but does not
  need KVM since we are fuzzing parser crates directly.
* Container: `instar-build` devcontainer image (has nightly Rust
  with llvm-tools-preview). May need `cargo-fuzz` added.
* Timeout: `duration * number_of_targets + 30 minutes` build
  overhead. Cap at 8 hours for nightly runs.

#### 5c. Workflow steps

1. **Checkout** instar and instar-testdata repos.
2. **Install cargo-fuzz** (`cargo install cargo-fuzz` if not
   already in the devcontainer image).
3. **Build all fuzz targets** (`cargo fuzz build`).
4. **Extract seed corpus** (run the script from Step 3a).
5. **Run each target** via a wrapper script that handles crash
   detection and immediate issue filing. For each target:
   a. Run `cargo fuzz run <target>` with the configured duration:
      ```bash
      cargo fuzz run <target> \
        -- -max_total_time=$DURATION \
           -max_len=4194304 \
           -rss_limit_mb=4096
      ```
      Use `-max_len` to cap input size (4MB is generous for disk
      image headers; increase for full-image targets). Use
      `-rss_limit_mb` to prevent OOM on the runner.
   b. If `cargo fuzz` exits with a crash, **immediately:**
      - Minimise the crash input with `cargo fuzz tmin`.
      - File a GitHub issue with `gh issue create`:
        ```bash
        gh issue create \
          --label security-audit \
          --title "Coverage fuzz crash: <target> - <signature>" \
          --body "..."
        ```
        The issue body should include: target name, crash
        signature (panic message or signal), minimised input
        (base64-encoded or as artifact link), stack trace,
        reproduction command, and a link to the CI run.
      - Continue to the next target (do not abort the run).
        This matches the differential fuzzer's behaviour of
        filing issues as they are found and continuing.
   c. If no crash, proceed to the next target.
6. **Collect coverage** (`cargo fuzz coverage` for each target).
7. **Upload artifacts:**
   - Crash inputs (if any) with minimised reproducers.
   - Coverage reports (HTML).
   - Corpus snapshots (for seeding future runs).

#### 5d. Corpus persistence

The fuzz corpus is stored in the private GitLab repo
`shakenfist/instar-testdata` under `custom/fuzz-corpus/<target>/`
(one subdirectory per fuzz target). This keeps potentially
malicious fuzzer-discovered inputs safely in the same private
repo as the existing adversarial test images.

After each nightly run, the CI workflow should:

1. Clone `instar-testdata` using `GITLAB_TESTDATA_PUSH_TOKEN`
   (the write-capable token -- see Step 0a).
2. Merge new corpus entries from `cargo fuzz`'s output into
   the corresponding `custom/fuzz-corpus/<target>/` directories.
3. Commit and push only if there are new entries.
4. Use a descriptive commit message:
   `"Add fuzz corpus entries from nightly run <date>"`.

The seed corpus extraction script (Step 3a) should read from
`instar-testdata` and the CI workflow should write back to it,
so the corpus grows monotonically across runs. The `cargo fuzz`
working corpus (under `src/fuzz/corpus/`) is ephemeral and
populated at the start of each CI run by copying from
`instar-testdata/custom/fuzz-corpus/`.

#### 5e. PR validation

When fuzz harness code changes (paths `src/fuzz/**`), run a short
smoke test (60 seconds per target) to verify harnesses still build
and don't immediately crash on the seed corpus.

### Step 6: Documentation and integration

#### 6a. Update docs/testing.md

Add a section documenting coverage-guided fuzzing:
* How the harnesses work (mock CallTable, thread-local input).
* How to run locally (`cargo fuzz run <target>`).
* How to interpret coverage reports.
* How to add new fuzz targets.

#### 6b. Update docs/security-audits.md

Add Phase 6 results:
* Number of fuzz targets created.
* Cumulative fuzzing duration.
* Crashes found and fixed (with commit links).
* Coverage percentages for each parser crate.

#### 6c. Update ARCHITECTURE.md

Add coverage fuzzing to the testing architecture section,
explaining how harnesses decouple parsers from the VMM/KVM stack.

#### 6d. Update README.md

Add brief mention of coverage-guided fuzzing alongside the
existing differential fuzzing documentation.

#### 6e. Update Makefile

Add convenience targets:
```makefile
fuzz-build:    # Build all fuzz targets
fuzz-run:      # Run all targets for default duration
fuzz-coverage: # Generate coverage reports
```

### Step 7: Triage and regression

For each crash found:

1. **Minimise** with `cargo fuzz tmin <target> <crash_input>`.
2. **Classify** severity (crash/hang/panic, exploitability).
3. **Fix** the root cause in the parser crate.
4. **Add regression image** to `instar-testdata/custom/audit/fuzz/`
   and register in `tests/manifest.json` with appropriate safety
   level.
5. **Verify** the fix by re-running the minimised crash input.
6. **Close** the GitHub issue with a link to the fix commit.

## Success criteria

Phase 6 is complete when:

* Fuzz targets exist for all six parser crates (qcow2, vmdk, vhd,
  vhdx, raw, luks) covering header parsing and (where applicable)
  table lookup and decompression paths.
* The seed corpus includes all existing test images plus
  hand-crafted minimal inputs.
* Each target has run for a minimum of 4 hours cumulative (24+
  hours total across all targets) with no unresolved crashes.
* A CI workflow runs nightly and on-demand, with automatic issue
  filing for crashes.
* Coverage reports show that parser code (not just harness
  scaffolding) is being exercised -- target >60% line coverage
  for header parsing paths.
* All discovered crashes are fixed, minimised, and added as
  regression test images.
* Documentation is updated (testing.md, security-audits.md,
  ARCHITECTURE.md, README.md).

## Future work

* **Structured-aware fuzzing** -- use `arbitrary` or `bolero`
  crates to generate structured inputs (valid QCOW2 headers with
  randomised fields) rather than pure byte mutation. This would
  improve coverage of deep parser paths.
* **Multi-device fuzzing** -- simulate backing chains by
  splitting fuzz input into multiple virtual devices. Useful for
  finding bugs in chain resolution logic.
* **Continuous fuzzing** -- submit to OSS-Fuzz or ClusterFuzz
  for 24/7 coverage once the project is public.
* **Sanitizer variants** -- run with AddressSanitizer (ASan),
  MemorySanitizer (MSan), and UndefinedBehaviorSanitizer (UBSan)
  in addition to the default libFuzzer configuration.
* **LUKS KDF fuzzing** -- fuzz the key derivation paths with
  reduced iteration counts to avoid timeouts.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
