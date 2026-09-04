# Security Audits

This document records the results of security audits performed on
instar. For the threat model and CVE analysis, see [security.md](/components/instar/security/).

## Reporting Vulnerabilities

If you discover a security vulnerability in instar, please report it
via [GitHub Security Advisories](https://github.com/shakenfist/instar/security/advisories/new).
Do not file public issues for security vulnerabilities.

## Static analysis and code review

**Date:** 2026-03-14
**Scope:** All Rust source code in the instar workspace (15 crates)
**Techniques:** Manual unsafe code audit, integer arithmetic review,
automated linting (cargo clippy, cargo audit)

### Unsafe Code Audit

Every `unsafe` block and `unsafe fn` in the codebase was classified
as **sound** (invariants enforced), **fragile** (invariants hold but
depend on implicit assumptions), or **unsound** (invariants can be
violated by untrusted input).

#### VMM (host-side) — highest priority

The VMM runs on the host with full privileges. Bugs here bypass the
KVM sandbox entirely.

| Location | Category | Verdict | Notes |
|----------|----------|---------|-------|
| vmm/main.rs: ACTIVE_MMIO_BASE | Static mut read/write | Fragile | Should use OnceLock; safe (single-threaded) |
| vmm/main.rs: set_user_memory_region | KVM API | Sound | Memory region lifetime managed correctly |
| vmm/io_thread.rs: epoll syscalls | libc FFI | Sound | All FDs validated, errors checked |
| vmm/ioevent.rs: KVM_IOEVENTFD ioctls | libc FFI | Sound | Structs fully initialized, errors checked |
| vmm/kvm_stats.rs: KVM_CHECK_EXTENSION | libc FFI | Sound | Capability check, no safety concern |
| vmm/virtio/block.rs: ByteValued impls | Trait impls | Sound | All 5 structs are repr(C) POD with no padding |

**VMM summary:** 0 unsound, 1 fragile (cosmetic — static mut could
use OnceLock), all others sound.

#### Guest core

| Location | Category | Verdict | Notes |
|----------|----------|---------|-------|
| core/main.rs: SingleThreadCell | Interior mutability | Sound | Single vCPU enforces invariant |
| core/main.rs: cstr_to_str | Pointer scan | Fragile | Bounded to 4096B, UTF-8 validated; relies on valid memory |
| core/main.rs: call_operation | Transmute to fn ptr | Inherent | Design boundary — operation IS untrusted code |
| core/main.rs: ct_read_input_sector | Buffer pointer | Fragile | Device index checked; buffer ptr trusted from op |
| core/main.rs: ct_write_output_sector | Buffer pointer | Fragile | Buffer pointer trusted from operation |
| core/main.rs: ct_send_info_result_* | Struct pointer | Fragile | Null-checked but non-null validity trusted |
| core/main.rs: ct_get_chain_config | Config pointer | Sound | Magic validated before returning |
| core/main.rs: setup_call_table | CallTable write | Sound | Fixed address, guaranteed by memory layout |
| core/virtio.rs: MMIO access | Volatile read/write | Sound | Fixed addresses, bounds-checked ring indices |
| core/serial.rs: asm! blocks | x86 I/O | Sound | Compile-time port constants |
| shared/lib.rs: BumpAllocator | GlobalAlloc | Sound | Bounds-checked, no overflow possible on x86-64 |
| shared/bitmap.rs: Bitmap ops | Scratch memory | Sound | Bounds-checked, returns BeyondCapacity on overflow |

**Guest core summary:** 0 unsound. The "fragile" items are inherent
to the call table FFI boundary — operations must provide valid
pointers. This is acceptable because operations run inside the KVM
sandbox with no access to host memory.

#### Format crates

| Crate | Unsafe fns | Verdict | Notes |
|-------|-----------|---------|-------|
| qcow2 | 18 | All sound | Bounds checking on all image offsets; checked arithmetic for L1/L2/refcount |
| vmdk | 5 | All sound | Grain marker validation, descriptor bounds checking |
| vhd | 2 | All sound | Block index bounds-checked, BAT uses checked arithmetic |
| vhdx | 4 | All sound | Multi-layer validation; BAT truncation **fixed** (see below) |
| raw | 0 | N/A | No unsafe code |
| luks | 0 | N/A | No unsafe code |

#### Operations

| Operation | Unsafe fns | Verdict | Notes |
|-----------|-----------|---------|-------|
| info | 6 | All sound | Header parsing delegates to format crates with validation |
| check | 8 | All sound | Extensive bounds checking; careful lifetime management in check_vhd |
| convert | 21 | All sound | Layout calculations bounds-checked against scratch memory; VHDX BAT overflow **fixed** |
| compare | 2 | Sound | Entry point + call table helper |
| copy | 2 | Sound | Entry point + call table helper |

### Bugs Found and Fixed

#### VHDX BAT calculation integer overflow

**Severity:** Medium
**Location:** `src/crates/vhdx/src/lib.rs`, lines 754-755 and
`calculate_bat_layout()` at line 1092
**Issue:** `total_bat_entries` and `chunk_ratio` were cast from u64
to u32 via `as u32` without overflow checking. A crafted VHDX image
with `virtual_disk_size` near u64::MAX and small `block_size` would
silently truncate these values, potentially causing undersized BAT
allocation or incorrect block lookups.
**Fix:** Replaced `as u32` with `u32::try_from().ok()?` in both
`init()` and `calculate_bat_layout()`. The function now returns
`None` for images whose BAT layout exceeds u32 capacity.
**Commit:** (this commit)

### Integer Arithmetic Review

All integer arithmetic on untrusted (image-derived) input was
reviewed. Key findings:

**Protected patterns (good):**
- QCOW2 L1/L2 lookups use chained `checked_mul`/`checked_add`
- VMDK grain lookups use `checked_mul(512)` chains
- VHD BAT lookups use `checked_add(block_idx.checked_mul(4))`
- QCOW2 cluster_bits validated to range 9..=21 before shift
- Decompression buffer sizes explicitly bounded

**Fixed:**
- VHDX `total_bat_entries as u32` truncation (see above)

**Accepted (platform-dependent):**
- Widespread `u64 as usize` casts for buffer indexing. These are
  safe on x86-64 (usize is 64-bit). The guest code targets bare-
  metal x86-64 exclusively, so no truncation occurs. Documented
  as a platform assumption.

**Accepted (bounded by construction):**
- VHD CHS geometry casts (`cylinders as u16`, `heads as u8`).
  The algorithm caps values to CHS limits (65535, 16, 255) before
  casting, following the VPC specification exactly.
- QCOW2 compressed cluster shift `62 - (cluster_bits - 8)`.
  Safe because `cluster_bits` is validated to 9..=21 at parse time.

### Truncating Cast Audit

All `as u32`, `as u16`, and `as u8` casts on values wider than the
target type were catalogued and classified.

#### Format crates (library code)

| Location | Cast | Classification | Notes |
|----------|------|----------------|-------|
| qcow2: cluster_bits parsing | `as u32` | Bounded | Validated to 9..=21 before cast |
| qcow2: L1/L2 index calculations | `as u32` | Guarded | Preceded by checked arithmetic |
| qcow2: refcount_bits | `as u32` | Bounded | Derived from refcount_order (0..=6) |
| vmdk: grain marker fields | `as u32` | Bounded | Grain size constant (128 sectors) |
| vmdk: descriptor parsing | `as u32` | Bounded | Descriptor limited to 20KB |
| vhd: CHS geometry | `as u16`, `as u8` | Bounded | Algorithm caps to CHS limits before cast |
| vhdx: BAT entries | `as u32` | **Fixed** | Was truncating; now uses `u32::try_from()` |

#### Guest core and shared

| Location | Cast | Classification | Notes |
|----------|------|----------------|-------|
| shared/bitmap.rs | `as usize` | Platform | u64 to usize, safe on x86-64 |
| core/main.rs: call table | `as usize` | Platform | u64 to usize, safe on x86-64 |
| core/virtio.rs: ring indices | `as u16` | Bounded | Queue size ≤ 256, index masked |

#### Operations (guest-side)

| Location | Cast | Classification | Notes |
|----------|------|----------------|-------|
| convert: `reftable_clusters as u32` (ln 1624) | u64→u32 | Bounded | Reftable fits in scratch (~12.5MB), ≤ ~200 |
| convert: `l1_size` (line 1682) | u64→u32 | Bounded | L1 fits in scratch; checked at line 1693 before use |
| convert: `entries_per_l2` (line 1680) | u64→u32 | Bounded | cluster_size/8; cluster_bits ≤ 21 so max = 262144 |
| convert: `num_gd_entries` (line 2495) | u64→u32 | Bounded | GD must fit in scratch memory; checked at line 2510 |
| convert: GTE sector offset (lines 2695, 2987) | u64→u32 | Spec | VMDK GTE is 32-bit sector offset per specification |
| convert: GDE sector offset (line 2726) | u64→u32 | Spec | VMDK GDE is 32-bit sector offset per specification |
| convert: progress percentage | u64→u32 | Bounded | Result of `(n * 100 / total)`, always ≤ 100 |
| info/check: format header fields | various | Bounded | Values validated by format crate parsers |

#### VMM (host-side)

| Location | Cast | Classification | Notes |
|----------|------|----------------|-------|
| main.rs: sector size | `as u32` | Bounded | Sector sizes are 512 or 4096 |
| main.rs: MMIO/config offsets | `as u32` | Bounded | Fixed layout constants |
| virtio/block.rs: queue operations | `as u16` | Bounded | Queue size ≤ 256 |

**Summary:** 0 unguarded truncating casts on untrusted input remain.
The VHDX BAT truncation (the only finding) has been fixed. All other
casts are either bounded by construction, guarded by preceding checks,
constrained by the VMDK/QCOW2 specification, or platform-safe
(u64→usize on x86-64). The operations marked "Bounded" are safe
because the corresponding data structures must fit within the guest's
12.5MB scratch memory — any value large enough to overflow u32 would
have already been rejected by the scratch memory bounds check.

### Static Analysis Tooling

| Tool | Status | Result |
|------|--------|--------|
| cargo clippy (nightly, -D warnings) | Pass | 0 warnings on VMM + all library crates |
| cargo audit | Pass | 0 vulnerabilities in 136 dependencies |
| rustfmt | Pass | All code formatted |
| check-binary-sizes.sh | Pass | All guest binaries within memory layout |
| shellcheck | Pass | All scripts clean |

### Standing Security Properties

These architectural properties are verified during every audit:

1. **KVM isolation:** All format parsing runs inside a KVM guest
   with a 32MB address space. A bug in format parsing cannot access
   host memory, files, or network.

2. **Rust memory safety:** The codebase is written in Rust with
   explicit `unsafe` blocks for hardware access and FFI. All unsafe
   blocks have been audited and classified.

3. **Bounded decompression:** Decompression buffers are statically
   bounded (COMPRESSED_BUF_SIZE for input, cluster_size for output).
   Compression bombs cannot cause unbounded memory allocation.

4. **Backing chain allowlist:** The VMM validates backing file paths
   against a user-configured allowlist before mapping them into the
   guest. Path traversal attacks are blocked at the host level.

5. **Feature bit enforcement:** Unknown QCOW2 incompatible feature
   bits cause immediate rejection. External data file and other
   dangerous features are blocked.

6. **Raw format hardening:** Files without valid format headers or
   partition tables are rejected by default. The `--unsafe-quirks`
   flag is required to accept them (matching qemu-img behaviour).

7. **Checked arithmetic:** All critical address calculations
   (L1/L2 lookups, BAT indexing, grain table offsets) use Rust's
   checked arithmetic (`checked_mul`, `checked_add`) to prevent
   integer overflow.

## CVE reproduction

**Date:** 2026-03-16
**Scope:** 6 known qemu-img CVEs verified against instar
**Techniques:** Purpose-built reproducer images, automated tests

### CVEs Verified

| CVE | Class | CVSS | Result | Tests |
|-----|-------|------|--------|-------|
| CVE-2024-32498 | External data file path traversal | 6.5 | Mitigated | 4 |
| CVE-2015-5163 | Backing file path traversal | 3.5 | Mitigated | 5 |
| CVE-2022-47951 | VMDK descriptor path traversal | 5.7 | Mitigated | 2 |
| CVE-2015-5162 | Resource exhaustion (oversized vsize) | 7.5 | Mitigated | 2 |
| CVE-2014-0223 | Integer overflow in L1 table size | 7.5 | Mitigated | 3 |
| CVE-2024-4467 | json:{} block device specification | 7.8 | Mitigated | 3 |

**Total:** 19 tests, 7 reproducer images, 0 bypasses found.

### Reproducer Images

All reproducer images are in `instar-testdata/custom/audit/cve/`,
generated by `instar-testdata/scripts/create-cve-reproducer-testdata.py`.
See `instar-testdata/ADVERSARIAL.md` for validation instructions
including how to confirm qemu-img IS vulnerable to each CVE.

### Mitigation Details

**CVE-2024-32498 (external data file):** instar detects the QCOW2
`data-file` header extension and reports the path in info output.
The host-side path allowlist (`vmm/chain.rs`) rejects external
data files outside allowed directories when `--chain` or convert
is used. The file is never opened.

**CVE-2015-5163 (backing file traversal):** The host-side
`resolve_backing_path()` function canonicalises all paths (resolving
`../` and symlinks) before checking against the allowlist. Paths
with embedded null bytes are handled by Rust's `CStr`/`Path` types
which stop at the first null. Both relative traversal and null-byte
bypass variants are tested.

**CVE-2022-47951 (VMDK descriptor):** Text-only VMDK descriptors
(no binary magic) are rejected as "unknown format". Binary VMDKs
with embedded descriptors are parsed inside the KVM guest, which
has no access to host files. The extent path is parsed but cannot
be followed — the guest can only read data through the virtio-block
device provided by the VMM.

**CVE-2015-5162 (resource exhaustion):** All format parsing runs
in a 32MB KVM guest. No code path allocates memory proportional to
the declared virtual size. Decompression buffers are statically
bounded. Info and check operations complete in under 5 seconds on
a 1 PB virtual size image.

**CVE-2014-0223 (L1 integer overflow):** Instar does not support
QCOW1. All QCOW2 L1/L2 size calculations use `checked_mul()` and
`checked_add()` chains. An L1 size at the u32 overflow boundary
(536870913) is handled without crash — checked arithmetic returns
an error.

**CVE-2024-4467 (json:{} specification):** Instar does not support
the `json:{}` block device specification. All CLI arguments are
treated as literal file paths. A file whose content starts with
`json:` is rejected as "unknown format" (no valid format magic or
partition table). This attack class is architecturally impossible.

### Bugs Found

None. All 6 CVEs are fully mitigated by instar's existing
architecture. No new code changes were required.

## VMM boundary audit

**Date:** 2026-03-17
**Scope:** All host-side VMM code — virtio-block device emulation,
serial protocol handling, KVM memory mapping, MMIO dispatch, and
KVM exit handling.
**Techniques:** Manual code review of all VMM source files
(main.rs, virtio/block.rs, virtio/mmio.rs, backing.rs, io_thread.rs,
ioevent.rs, error.rs).

### Motivation

The KVM sandbox is instar's primary security boundary. All format
parsing runs inside the guest, but the VMM runs on the host with
full privileges. Bugs in the VMM bypass the sandbox entirely. This
audit examined every path where guest-controlled data influences
VMM behaviour.

### Areas Audited

| Area | Files | Verdict |
|------|-------|---------|
| Virtio-block I/O bounds | virtio/block.rs, backing.rs | 5 bugs found, all fixed |
| Serial protocol decoding | main.rs (SerialDecoder, DebugBuffer) | 2 bugs found, all fixed |
| KVM memory mapping | main.rs (memory setup) | Sound |
| MMIO dispatch | virtio/block.rs, virtio/mmio.rs, main.rs | 1 bug found, fixed |
| KVM exit handling | main.rs (6 run loops) | 1 bug found, fixed |
| Guest memory isolation | main.rs, vm-memory crate | Sound |
| Port I/O dispatch | main.rs | Sound |
| Page table setup | main.rs | Sound |
| Config area writes | main.rs, shared/lib.rs | Sound |

### Bugs Found and Fixed

#### 1. No sector bounds check in virtio-block I/O (High)

**Location:** `virtio/block.rs`, `do_read()` and `do_write()`
**Issue:** The guest-supplied `sector` field from the virtio-block
request header was used directly to compute a file offset without
checking against the device capacity. A malicious guest could
write to arbitrary offsets in the output file.
**Fix:** Added `validate_sector_offset()` method that checks
`sector < capacity` and uses `checked_mul` for the offset
calculation. Both `do_read` and `do_write` reject out-of-bounds
requests with IOERR status.

#### 2. Integer overflow in sector offset calculation (High)

**Location:** `virtio/block.rs`, `do_read()` and `do_write()`
**Issue:** `sector * sector_size` used wrapping multiplication.
With a large `sector` value, the result could wrap to a small
offset, causing I/O to target the wrong location.
**Fix:** Uses `checked_mul` in `validate_sector_offset()`,
returning IOERR on overflow.

#### 3. Integer overflow in BackingStore offset arithmetic (Medium)

**Location:** `backing.rs`, `read_at()` and `write_at()`
**Issue:** `offset + buf.len() as u64` could wrap around if
offset was near `u64::MAX`, bypassing bounds checks.
**Fix:** Added `checked_end()` helper using `checked_add`,
returning an error on overflow.

#### 4. No capacity enforcement on BackingStore writes (Medium)

**Location:** `backing.rs`, `write_at()`
**Issue:** The backing store had no independent enforcement of
its capacity limit for write operations. A write beyond capacity
would silently extend the file.
**Fix:** `write_at()` now rejects writes where `offset + len`
exceeds `capacity`, returning an `InvalidInput` error.

#### 5. Unbounded I/O buffer allocation (Medium)

**Location:** `virtio/block.rs`, `do_read()` and `do_write()`
**Issue:** The I/O buffer was allocated as `vec![0u8; len]`
where `len` came from the guest-controlled descriptor. A guest
could request up to 4GB (u32::MAX), causing VMM-side OOM.
**Fix:** Added `MAX_IO_BUFFER_SIZE` constant (1 MB). Requests
exceeding this limit are rejected with IOERR.

#### 6. run_sandboxed_info silently ignores unknown exits (Medium)

**Location:** `main.rs`, sandboxed info run loop
**Issue:** The catch-all arm was `_ => {}` (silently ignore),
while all other run loops properly break with an error. This
could cause an infinite loop if the guest triggered an unexpected
KVM exit type like InternalError.
**Fix:** Changed to `exit => { return Err(...) }` matching the
pattern used in all other run loops.

#### 7. DebugBuffer unbounded String growth (Moderate)

**Location:** `main.rs`, `DebugBuffer`
**Issue:** If a guest wrote to COM2 without sending newlines,
the `String` buffer grew without bound, eventually exhausting
host memory.
**Fix:** Added `MAX_DEBUG_LINE` constant (4096 bytes). Lines
exceeding this length are forcibly flushed.

#### 8. SerialDecoder buffer no size cap (Low)

**Location:** `main.rs`, `SerialDecoder`
**Issue:** A guest sending a length prefix claiming 65535 bytes
would cause the decoder to accumulate up to ~64KB before
attempting a decode. While bounded by the u16 wire format, no
explicit size cap existed.
**Fix:** Added `MAX_SERIAL_BUFFER` constant. Length prefixes
exceeding `MAX_MESSAGE_SIZE + FRAME_HEADER_SIZE + 256` are
rejected immediately by discarding the leading byte.

### Additional Hardening

**Descriptor index validation:** The `process_request()` function
now validates `desc_idx < queue_size` before indexing the
descriptor table, via a new `read_descriptor()` helper. Previously,
`_queue_size` was accepted but unused.

### Sound Areas (no issues found)

**KVM memory isolation:** A single KVM memory region maps exactly
to an anonymous mmap. No host memory is mapped into the guest.
All guest memory access goes through `GuestMemoryMmap` with bounds
checking.

**MMIO dispatch:** MMIO addresses are dispatched via linear scan
over registered devices. Unmapped addresses return 0 (read) or
are silently ignored (write). All register offsets use exhaustive
`match` with safe defaults. Queue selector uses modulo indexing
(`queue_sel % queues.len()`).

**Page tables and config areas:** Guest page tables identity-map
the MMIO region correctly. KVM EPT provides the actual isolation.
Config writes are all within the 32MB guest memory boundary with
explicit size bounds.

**Port I/O:** Unknown ports return 0 (read) or are logged (write).
No crash or corruption paths.

**HLT/Shutdown/FailEntry:** All run loops handle these exits
correctly by breaking with appropriate error messages.

### Fragile Areas (defence-in-depth, not exploitable)

These items are not security bugs but were noted as potential
improvements:

- `static mut ACTIVE_MMIO_BASE`: Should use `OnceLock<u64>`.
  Safe in practice (written once before concurrent access).
- No runtime binary size validation (build-time check exists).
- No stack guard page in guest memory (guest-only concern).
- I/O thread silently drops `process_queue` errors via `if let`.
- Mutex `unwrap()` could cascade if another thread panics.

## Coverage-guided fuzzing (2026-03-19)

### Scope

Coverage-guided fuzzing of all `no_std` parser crates using
`cargo-fuzz` (libFuzzer). The campaign began with the parser targets
listed below — format detection, header parsing, L1/L2 cluster lookup,
refcount table traversal, decompression, grain directory lookup, BAT
traversal and VHDX metadata parsing — and has grown to **40 targets**
(`src/fuzz/fuzz_targets/`): every subcommand implemented since brought
its own planner and emitter targets onto the same harness
infrastructure, and the later input-format work added VDI, Parallels,
QCOW1 and DMG. The table below is the original parser roster; the
directory is the current one.

### Technique

A mock `CallTable` backed by fuzzer input (`src/fuzz/src/lib.rs`)
replaces the real VMM/KVM I/O layer, allowing libFuzzer to explore
deeply malformed inputs without the full virtual machine stack. The
seed corpus is extracted from `instar-testdata` test images plus
hand-crafted minimal valid inputs for each format.

### Targets

| Target | Crate | Entry points |
|--------|-------|-------------|
| `fuzz_format_detect` | shared | `detect_format_from_header`, `detect_vhd_footer` |
| `fuzz_qcow2_header` | qcow2 | `QcowHeader::parse`, `parse_header_extensions` |
| `fuzz_qcow2_l1l2` | qcow2 | `Qcow2State::init`, `cluster_lookup` |
| `fuzz_qcow2_refcount` | qcow2 | `lookup_refcount` |
| `fuzz_qcow2_decompress` | qcow2 | `read_compressed_cluster` |
| `fuzz_vmdk_header` | vmdk | `Vmdk4Header::parse`, `parse_descriptor` |
| `fuzz_vmdk_grain` | vmdk | `VmdkState::init`, `grain_lookup` |
| `fuzz_vhd_footer` | vhd | `VhdFooter::parse`, `VhdDynamicHeader::parse` |
| `fuzz_vhd_bat` | vhd | `VhdState::init`, `block_lookup` |
| `fuzz_vhdx_header` | vhdx | `VhdxHeader::parse`, `parse_region_table` |
| `fuzz_vhdx_metadata` | vhdx | `VhdxState::init`, `block_lookup`, `parse_metadata` |
| `fuzz_raw_partition` | raw | `detect_partition_table` |
| `fuzz_luks_header` | luks | `parse_v1_header`, `parse_v2_keyslot`, `parse_v2_digest` |

### Infrastructure

- CI workflow: `.github/workflows/coverage-fuzz.yml`
- Nightly runs: all targets at 04:00 UTC, per-target durations tiered
  against a 450-min budget (`tools/ci/fuzz-tier.sh`)
- Corpus seeding: `scripts/extract-fuzz-corpus.py`
- Corpus storage: `instar-testdata/custom/fuzz-corpus/`
- Crash reporting: automatic GitHub Issue filing with `security-audit` label
- Fixes: by hand, in interactive sessions. An earlier workflow attempted
  this automatically; it was retired after an audit found its safety
  boundary unsound (see `docs/plans/PLAN-fuzz-autofix.md`).

### Findings

The campaign runs nightly and its findings are tracked as GitHub
issues rather than in this document, so that CI can file and update
them without merge conflicts. As of 2026-08-19, 258 `security-audit`
issues have been closed and 3 are open.

Two backlog drains were large enough to warrant their own plans, and
both are complete:

- [PLAN-fuzzing-bugs.md](/components/instar/plans/PLAN-fuzzing-bugs/) — 44 issues
  resolved to 5 root causes (`plan_vmdk` capacity overflow, qcow2
  `scan_allocation` out-of-bounds L2 entries, measure-calculator sum
  overflow, vhd/vhdx/vmdk `allocated_bytes` clamp, and a differential
  external-timeout misclassification).
- [PLAN-bug-fixes.md](/components/instar/plans/PLAN-bug-fixes/) — 10 issues resolved to
  3 root causes (Fixed-VHD `virtual_size` overflow in `plan_vhd`, an
  unchecked VHDX resize sequence-number increment, and qcow2
  `resize --shrink` sub-byte refcount corruption).

The one open defect at the time of writing is #483: `fuzz_rebase_planners`
emits a write patch outside `total_file_size` (#485 and #492 are
duplicate crash reports of the same signature).
