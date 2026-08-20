# Instar and qemu-img security audit

## Status: Complete

All six phases are executed. Phases 1a-5 are recorded as Done in the
Progress table below; phase 6 was expanded into
[PLAN-coverage-fuzzing.md](/components/instar/plans/PLAN-coverage-fuzzing/), which is now
complete in its own right.

The audit found and fixed 9 bugs (listed under Progress) and verified
6 qemu-img CVEs as mitigated by instar's architecture with 0 bypasses.
Its two standing outputs — nightly coverage-guided fuzzing and nightly
differential fuzzing, both filing `security-audit` issues
automatically — outlive the plan and need no plan to keep running.

Closing this plan does not close the audit posture. Findings continue
to arrive as GitHub issues, which is exactly what the Vulnerability
tracking section below specifies; a fuzzer that runs every night has
no terminal state to wait for.


## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (project structure,
command-line argument handling, input source abstractions, output
formatting, error handling), and ground your answers in what the
code actually does today. Do not speculate about the codebase when
you could read it instead. Where a question touches on external
concepts (disk image format specs, KVM isolation, compression
algorithms, CVE databases), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

## Situation

We have been working on instar, a security focused replacement for
qemu-img for a while now. Our focus has been on basic functionality
such as implementing the `info`, `check`, and `convert` subcommands.
However, we must not forget that our primary drivers are to provide
a command line compatible replacement for `qemu-img` which resolves
the security issues that `qemu-img` has. This planning document is
an attempt to ensure we keep that focus as we get closer to releasing
`instar`.

## Mission and problem statement

Your goal in this plan is to act as an adversarial pentester. You
are authorised to run as many research agents as required in order
to achieve your goal -- which is to find exploitable vulnerabilities
in `instar`. Those vulnerabilities should be tracked in this document.
It is likely that referring to `qemu-img` or the greater `qemu` code
base might help you find flaws, and any flaws you find in those tools
should be tracked in this document as well.

## Existing test image corpus

The `shakenfist/instar-testdata` repository contains test images
used by the integration test suite, defined in `tests/manifest.json`
(currently 44 images). This corpus is a valuable asset for seeding
fuzzing and validating security properties, but it has significant
gaps that this audit should fill.

### What we already have

* **Real-world images:** CirrOS, Debian (multiple architectures),
  Shaken Fist production images, QEMU iotest images, Disk2VHD.
* **Edge cases:** Min/max cluster sizes (512B, 2MB), refcount
  widths (1-bit, 64-bit), extended L2, ZSTD compression, lazy
  refcounts, dirty/corrupt feature bits.
* **Backing chains:** Two-layer and three-layer chains, cross-
  format chains (QCOW2 on VMDK).
* **Raw format variants:** MBR, GPT, no-partition-table, sparse,
  truncated, corrupted, misleading headers, minimal 1-byte.
* **Malicious/CVE images:** Backing file path traversal
  (CVE-2015-5163), external data file (CVE-2024-32498), VMDK
  path traversal, unknown incompatible feature bits.
* **Malformed images:** AFL-discovered VHD/VMDK, corrupt format
  headers (VMDK version, VMDK descriptor, VHDX region, VHD disk
  type), overlapping clusters, refcount errors, leaked clusters.
* **Format detection:** VDI, QED, ISO, LUKS v1/v2 (detection only,
  not full parsing).

### Gaps to fill during this audit

The following categories of adversarial images are **not yet** in
the corpus and should be created during Phase 2:

* **Compression bombs:** QCOW2 with extreme expansion ratio
  compressed clusters. VMDK with DEFLATE grains that decompress
  to excessive sizes.
* **Circular backing chains:** A->B->A and A->B->C->A loops.
* **Deep backing chains:** Chains exceeding the 16-device limit.
* **Integer overflow triggers:** QCOW2 with L1 table size near
  `u32::MAX`, cluster_bits at boundaries, refcount_order
  yielding extreme widths.
* **Polyglot/format confusion:** Files valid as two formats
  simultaneously. QCOW2 magic with VMDK body.
* **Truncated structured formats:** QCOW2 with header cut short
  mid-field. VMDK truncated after magic. VHD with footer at wrong
  offset. VHDX with partial metadata region.
* **Oversized field values:** Virtual size claiming petabytes.
  L1/refcount table offsets beyond EOF. Snapshot counts in the
  millions.
* **VMDK-specific:** Zero grain size. Extremely large grain size.
  Multi-extent descriptors (should be rejected). Descriptor with
  embedded newlines/control characters.
* **VHD/VHDX-specific:** BAT entries pointing beyond EOF. Block
  size of zero. VHDX with conflicting dual headers (different
  sequence numbers, both with valid CRC).

All new test images should be added to `instar-testdata/custom/`
under an appropriate subdirectory (e.g. `custom/audit/`) and
registered in `tests/manifest.json` with `safety: "malicious"` or
`safety: "malformed"` as appropriate.

## Open questions

* How do we find security flaws in `instar` and `qemu-img`? What
  techniques apart from reading the code are we going to use?

**Answer:** We should use a layered approach combining multiple
techniques, each of which catches different classes of bugs:

1. **Differential fuzzing** (random walks) -- finds behavioral
   divergences and crashes via randomized operation sequences.
2. **Adversarial image crafting** -- hand-crafted malformed images
   targeting specific vulnerability classes (integer overflows,
   compression bombs, backing chain attacks, format confusion).
3. **CVE reproduction** -- confirm that every known qemu-img CVE
   is mitigated by instar's architecture.
4. **Static analysis and unsafe code review** -- audit all `unsafe`
   blocks, review integer arithmetic for overflow potential, and
   run `cargo clippy` / `cargo audit` for known issues.
5. **Boundary/interface auditing** -- specifically audit the VMM
   (host-side) code since bugs there bypass the KVM sandbox
   entirely.
6. **Coverage-guided fuzzing** -- use `cargo-fuzz` (libFuzzer) or
   AFL on the `no_std` parser crates directly to find crashes in
   format parsing code without needing the full VMM.

## Progress

| Phase | Status | Branch | Notes |
|-------|--------|--------|-------|
| 1a. Unsafe code audit | **Complete** | `static-analysis` | All unsafe blocks classified (0 unsound, 1 fragile in VMM, ~5 fragile in guest FFI boundary). SAFETY comments added to VMM. |
| 1b. Integer arithmetic review | **Complete** | `static-analysis` | VHDX BAT `as u32` truncation found and fixed (`u32::try_from().ok()?`). All checked arithmetic patterns verified. |
| 1c. Static analysis tooling | **Complete** | `static-analysis` | Nightly clippy clean (12 lints fixed), cargo audit 0 vulns, truncating cast table added to docs/security-audits.md. |
| 2. Adversarial image crafting | **Complete** | `adversarial-testing` | 61 adversarial images across 12 categories. Scripts in instar-testdata/scripts/. |
| 3. Differential fuzzing | **Complete** | `differential-fuzzing` | 1070-line fuzzer with libyal cross-validation. CI workflow: 100/200/1000 iterations (PR/merge/nightly). Auto-files issues on divergences. |
| 4. CVE reproduction | **Complete** | `cve-reproduction` | 6 CVEs verified (19 tests, 7 reproducer images, 0 bypasses). All mitigated by existing architecture. |
| 5. VMM boundary audit | **Complete** | (main) | 8 bugs fixed: sector bounds checking (2 High), BackingStore overflow/capacity (2 Medium), IO buffer cap (Medium), sandboxed info exit handling (Medium), DebugBuffer OOM (Moderate), SerialDecoder cap (Low). Plus desc_idx validation. |
| 6. Coverage-guided fuzzing | **Complete** | `audit` | 40 fuzz targets, corpus seeding, nightly CI at a 450-minute tiered budget, automatic issue filing. ~200 `security-audit` issues closed; 1 open defect (#483). Detailed plan: [PLAN-coverage-fuzzing.md](/components/instar/plans/PLAN-coverage-fuzzing/) |

**Commits on `static-analysis`:**
1. `9f40bdd` Fix VHDX BAT calculation integer overflow
2. `403e3fc` Add SAFETY comments to VMM unsafe blocks
3. `0433bb9` Add Phase 1 security audit results
4. `c3205bc` Update docs for static analysis audit
5. `c1e6519` Fix nightly clippy lints, add truncating cast audit

**Bugs found:** 9 total (all fixed).
- Phase 1b: VHDX BAT integer overflow (medium severity)
- Phase 5: No sector bounds check in do_read/do_write (high)
- Phase 5: Integer overflow in sector*sector_size (high)
- Phase 5: Integer overflow in offset+buf.len() in BackingStore (medium)
- Phase 5: No capacity enforcement on BackingStore writes (medium)
- Phase 5: Unbounded IO buffer from guest data_desc.len (medium)
- Phase 5: run_sandboxed_info silently ignores unknown exits (medium)
- Phase 5: DebugBuffer (COM2) unbounded String growth (moderate)
- Phase 5: SerialDecoder buffer no size cap (low)

## Execution

### Phase 1: Static analysis and code review

Before running anything, audit the code for structural weaknesses.

#### 1a. Unsafe code audit

Enumerate every `unsafe` block in the guest code and VMM. For each
one, document:
* What invariant it relies on.
* Whether that invariant is enforced or merely assumed.
* Whether a malicious image could violate the invariant.

Pay special attention to:
* Raw pointer dereferences in the call table and config areas.
* The `CallTable` magic validation (0x494D4147) -- is this
  sufficient? Can a malformed image influence the config area?
* Static mutable state accessed via `unsafe` cells.
* Memory layout assumptions (e.g. .bss not overlapping config at
  0x80000).

#### 1b. Integer arithmetic review

Search for integer arithmetic in header parsing that could overflow:
* L1/L2 table size calculations in QCOW2 (CVE-2014-0223 analog).
* Cluster size shifts and multiplications.
* Refcount table offset computations.
* VMDK grain directory/table size calculations.
* VHD/VHDX BAT size and offset calculations.

For each case, verify that overflow is either impossible (types are
wide enough) or explicitly checked.

#### 1c. Static analysis tooling

* Run `cargo clippy` with all warnings on the full workspace.
* Run `cargo audit` to check for known vulnerable dependencies.
* Grep for `as u32`, `as u16`, `as usize` truncating casts that
  could lose bits.

### Phase 2: Adversarial image crafting

Generate hand-crafted malicious images targeting specific attack
classes. For each class, create test images and verify instar either
rejects them or handles them safely. All new images should be added
to `shakenfist/instar-testdata/custom/audit/` and registered in
`tests/manifest.json` (see "Gaps to fill" in the test corpus
section above for the full list of missing categories).

#### 2a. Header corruption attacks

* QCOW2 with `cluster_bits` outside valid range (< 9 or > 21).
* QCOW2 with `l1_size` that would cause integer overflow when
  multiplied by entry size.
* QCOW2 with `refcount_order` yielding extreme refcount widths.
* QCOW2 v3 with unknown incompatible feature bits set.
* VMDK with grain size of 0 or extremely large values.
* VHD with footer checksum mismatch.
* VHDX with invalid CRC-32C in headers.
* Files with valid magic bytes but truncated headers.

#### 2b. Compression bomb attacks

* QCOW2 with a compressed cluster that expands to a very large
  size relative to the compressed input.
* Verify decompression buffers are bounded (current limit is
  2 sectors / 128KB -- is this enforced for all cluster sizes?).
* VMDK with DEFLATE-compressed grains that expand excessively.

#### 2c. Backing chain attacks

* Image with backing file path containing `../` traversal.
* Image with backing file path pointing to `/etc/passwd` or
  similar sensitive file.
* Image with circular backing chain (A -> B -> A).
* Image with deeply nested backing chain (> 16 levels).
* Image with backing file that claims a different format than
  it actually is.
* Verify the host-side path allowlist rejects all of these
  before they reach the guest.

#### 2d. Format confusion attacks

* A file with QCOW2 magic but VMDK structure after the header.
* A file that is valid as multiple formats simultaneously
  (polyglot).
* A raw file with no partition table (should be rejected by
  default, accepted only with `--unsafe-quirks`).
* A file with format magic at unusual offsets.

#### 2e. Oversized field attacks

* QCOW2 claiming virtual size > physical file size by orders
  of magnitude.
* Snapshot count fields with extreme values.
* L1 table offset pointing beyond end of file.
* Refcount table offset pointing beyond end of file.
* VMDK descriptor claiming more extents than exist.

### Phase 3: Differential fuzzing (random walks)

This is the original plan, refined with specifics.

* Pick a random seed (so a given chain can be recreated later for
  debugging). Log this seed to a per-run log file.
* Randomly select a format and various attributes for it and
  generate an input file. Log the format and attributes for the run.
  Attributes to vary:
  - Format: qcow2, raw, vmdk, vpc, vhdx.
  - Virtual size: powers of 2 from 1MB to 8GB.
  - Cluster size (qcow2): 512, 4K, 64K, 256K, 2M.
  - Compression: on/off.
  - Backing files: 0, 1, or 2 levels deep.
  - Data patterns: zeros, random, structured (partition tables).
* Randomly select a chain of operations to perform, and execute them
  against both `qemu-img` and `instar` (on separate copies of the
  image of course) and compare output at each stage. Log each
  operation performed for debugging purposes. If the output differs,
  exit with a descriptive message describing how to achieve the
  difference. Operations `instar` has already tracked as unsafe
  quirks in docs/ should be avoided as they will by definition
  result in differences.
* Operations to chain:
  - `info` (compare JSON output).
  - `check` (compare exit codes and error detection).
  - `convert` to each supported output format.
  - `compare` of original vs converted (should be identical).
  - `convert` with `-c` (compressed output).
  - Multi-step: convert A->B->C, verify content preserved.
* Run for a configurable number of iterations (suggest 1000+
  per session) with timeout per operation (30 seconds).

### Phase 4: CVE reproduction

For each known qemu-img CVE, verify that instar is not vulnerable.
Where possible, obtain or craft a reproducer image and confirm
instar handles it safely.

Priority CVEs to test:
* **CVE-2024-32498** -- external data file QCOW2 feature used to
  read host files. Instar rejects this feature bit, verify it.
* **CVE-2015-5163** -- backing file path traversal. Verify
  host-side allowlist blocks this.
* **CVE-2022-47951** -- VMDK descriptor used to access host files.
  Verify VMDK descriptor parsing doesn't follow file paths.
* **CVE-2015-5162** -- resource exhaustion via compressed images.
  Verify decompression bounds.
* **CVE-2014-0223** -- integer overflow in QCOW1 L1 table size.
  Instar doesn't support QCOW1, but verify analogous QCOW2 paths.
* **CVE-2024-3567** -- qemu-img info DoS via crafted input.
  Verify instar doesn't hang or consume excessive resources.

### Phase 5: VMM boundary audit

The KVM sandbox is instar's primary security boundary. Bugs in the
VMM (host-side) code bypass this entirely. Audit:

* **Virtio-block emulation** -- Can a malicious guest craft virtio
  requests that cause the VMM to read/write outside the image file?
  Check bounds validation on sector offsets and lengths.
* **Serial protocol handling** -- Can malformed protobuf messages
  from the guest cause the VMM to crash or misbehave? Test with
  truncated, oversized, and malformed serial messages.
* **Memory mapping** -- Can the guest access VMM memory through
  the KVM memory map? Verify no host memory is mapped into the
  guest address space beyond what's intended.
* **Device MMIO handling** -- Are all MMIO register accesses
  bounds-checked? Can the guest trigger out-of-bounds access in
  the MMIO handlers?
* **Signal handling** -- Can the guest cause the VMM to enter an
  unexpected state via signals or KVM exits?

### Phase 6: Coverage-guided fuzzing of parser crates

This phase has been expanded into a detailed standalone plan.
See **[PLAN-coverage-fuzzing.md](/components/instar/plans/PLAN-coverage-fuzzing/)** for
the full plan covering harness architecture, fuzz targets, corpus
seeding, CI workflow, and success criteria.

**Summary:** Build `cargo-fuzz` (libFuzzer) harnesses for each
`no_std` parser crate (qcow2, vmdk, vhd, vhdx, raw, luks) using
a mock `CallTable` that reads from fuzzer input. Seed with the
existing test image corpus (including Phase 2 adversarial images).
Run nightly via CI with automatic issue filing for crashes. Target
24+ hours cumulative with no unresolved crashes.

## Vulnerability tracking

Discovered vulnerabilities are **not** tracked in this file.
This plan is the audit methodology; findings go elsewhere so
that CI automation can create and update them without merge
conflicts, and so that they have proper workflow states.

### Two-stage approach

1. **While the repo is private:** Use GitHub Issues with the
   `security-audit` label. All issues are already invisible to
   the public since the repo is private.
2. **Once the repo is public:** Enable GitHub Security Advisories
   (Settings > Code security -- this option only appears for
   public repos). Migrate any open `security-audit` issues into
   GHSA drafts and close the issues. GHSA drafts remain private
   until explicitly published, so unresolved findings never
   become visible to attackers.

CI fuzzing jobs should create issues automatically via
`gh issue create --label security-audit` when they find
failures. Each issue should include the seed, format, operation
chain, and enough detail to reproduce the finding.

## Public audit log

At the conclusion of each audit (or audit phase), update a public
document at `docs/security-audits.md` recording:

* **What was audited** -- which components and phases were covered.
* **Techniques used** -- from the methodology list above.
* **Duration and scale** -- how long fuzzing ran, how many
  differential iterations were executed, etc.
* **Resolved findings** -- number of bugs found and fixed, with
  links to commits. Enough detail for users to understand the
  scope of what was tested.
* **Test images added** -- how many new adversarial images were
  added to the corpus as a result.
* **Standing security properties** -- a summary of the
  architectural properties (KVM isolation, RAW hardening, backing
  chain allowlist, feature bit enforcement, bounded decompression,
  Rust memory safety) that are verified during every audit.

The document should **not** include details of unresolved
vulnerabilities -- those remain tracked privately in GitHub
Security Advisories (GHSA) until fixed. The goal is to give
users confidence that instar takes security seriously, without
creating a roadmap for attackers.

The document should also include instructions for reporting
vulnerabilities via GitHub Security Advisories and link to the
existing CVE analysis in `docs/security.md`.

Link the new document from `docs/index.md` in the "Platform
Analysis" table alongside the existing security.md entry.

## Administration and logistics

### Infrastructure

The audit phases have different compute requirements:

* **Phases 1, 2, 4, 5** (code review, image crafting, CVE
  reproduction, VMM boundary audit) are interactive work done
  in Claude Code sessions. No dedicated infrastructure needed.
* **Phase 3** (differential fuzzing) and **Phase 6** (coverage-
  guided fuzzing) are long-running jobs that need dedicated
  machines.

The long-running phases will run on VM-based CI runners
(not bare metal) so that a runaway fuzzing job cannot damage
the host. The runners need nested virtualisation enabled for
KVM access:

* **Differential fuzzing (Phase 3):** Runs on a
  `[self-hosted, debian-12, xl]` runner (or a dedicated
  `fuzzing` label if isolation from the regular CI queue is
  desired). Requires KVM (for running instar) and qemu-img
  (for comparison). Runs 1000+ iterations per session with
  a 30-second timeout per operation.
* **Coverage-guided fuzzing (Phase 6):** Runs on a standard
  VM runner. Runs `cargo-fuzz` against parser crates
  directly -- no KVM needed. Intended to run for extended
  periods (24+ hours cumulative across all parser crates).

These should be implemented as `workflow_dispatch` CI workflows
so they can be triggered on demand and also scheduled nightly
once stable. Each workflow should:

* Accept configurable parameters (iteration count, seed, fuzz
  duration).
* Log results to artifacts.
* Automatically file `security-audit` issues via `gh issue
  create` when failures are found.

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* All six phases have been executed.
* All known qemu-img CVEs have been verified as mitigated.
* Coverage-guided fuzzing has run for a minimum duration (suggest
  24+ hours cumulative across all parser crates) with no new
  crashes.
* Differential fuzzing has run 1000+ iterations with no
  unexplained divergences.
* All `unsafe` blocks have been documented and justified.
* All discovered bugs are tracked as GitHub Issues (with the
  `security-audit` label) or GitHub Security Advisories, with
  sufficient detail to reproduce each finding.

### Future work

We should list obvious extensions, known issues, unrelated bugs
we encountered, and anything else we should one day do but have
chosen to defer to here so that we don't forget them.

* **Continuous fuzzing infrastructure** -- Set up OSS-Fuzz or
  similar to continuously fuzz instar parser crates.
* **Property-based testing** -- Use `proptest` or `arbitrary`
  crates for structured random input generation within the Rust
  test suite.
* **Formal verification** -- For critical arithmetic paths
  (cluster lookup, refcount computation), consider using tools
  like Kani or Prusti to prove absence of overflow.
* **QEMU iotests integration** -- QEMU has an extensive iotest
  suite (tests/qemu-iotests/) that exercises edge cases. Running
  a subset against instar would increase confidence.
* **Multi-device backing chain fuzzing** -- Phase 6 fuzzes
  single-device parsing only. Simulating backing chains (split
  fuzz input across multiple virtual devices) would cover chain
  resolution logic and cross-format backing file handling.
* **Sanitizer variants** -- Run coverage-guided fuzzing with
  AddressSanitizer (ASan), MemorySanitizer (MSan), and
  UndefinedBehaviorSanitizer (UBSan) to catch memory safety
  and undefined behaviour issues that don't manifest as crashes
  under the default libFuzzer configuration.
* **Automated fuzzer bug fixes** -- Scheduled CI job that picks
  up `security-audit` issues filed by fuzzers and uses Claude
  Code to propose fixes as PRs. Detailed plan:
  [PLAN-fuzz-autofix.md](/components/instar/plans/PLAN-fuzz-autofix/).

### Bugs fixed during this work

Bugs found and fixed during audit execution are tracked as
closed GitHub Issues with the `security-audit` label. The
`docs/security-audits.md` public log summarises resolved
findings with links to commits.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
