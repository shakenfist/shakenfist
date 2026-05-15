# Phase 3: guest `measure` operation + protobuf result + call-table boundary

Master plan: [PLAN-measure.md](/components/instar/plans/PLAN-measure/) ·
Previous phase: [PLAN-measure-phase-02-allocation-scanners.md](/components/instar/plans/PLAN-measure-phase-02-allocation-scanners/)

## Status: Not started

## Mission

Ship every cross-boundary piece needed to actually run the
measure operation inside the KVM guest, *except* the host
CLI surface (that is phase 4). After phase 3 the system can:

- accept a `MeasureConfig` written to `OPERATION_CONFIG_ADDR`,
- launch a fresh `measure.bin` guest binary at `0x20000`,
- have the guest read the config, scan the source via the
  phase 2 scanners (or skip the scan for `--size` mode),
  call the phase 1 calculators, and emit a
  `MeasureResultMessage` over the serial command channel,
- have the host decode that protobuf message into a
  `MeasureResultMessage` struct ready for phase 4 to render.

Phase 3 deliberately does *not* add the `Commands::Measure`
clap variant or `run_measure()` function on the host —
that is phase 4. The end-to-end functional test of measure
therefore lives in phase 7. Phase 3's verification is
compile-and-link plus binary-size cap plus the existing fuzz
harness's mock-CallTable build.

## Why this is its own phase

This is the single phase where the call-table ABI changes
(`VERSION: u32 = 13` → `14`), where a new protobuf payload is
added to the `GuestMessage` oneof, and where a new guest
operation binary appears in the build outputs. Each of those
is a small change in isolation but each ripples through
multiple files (shared, core, vmm, guest-protocol, fuzz mock,
build scripts, binary-size check, operation cargo manifest /
linker / main). Bundling them lets one phase's review check
the whole boundary at once; splitting them would mean three
half-broken intermediate states.

## Architecture

### Boundary changes (ABI)

#### `CallTable` extension

Add one new function pointer at the end of `CallTable` in
`src/shared/src/lib.rs`, mirroring `send_check_result` /
`send_compare_result`:

```rust
/// Send measure result message.
/// Args: measure_result pointer containing required +
/// fully_allocated bytes for the target format.
pub send_measure_result: unsafe extern "C" fn(*const MeasureResult),
```

Bump `pub const VERSION: u32 = 14;`. This is breaking — every
existing operation binary built against version 13 will be
rejected by the `validate_call_table!` macro until rebuilt.
Acceptable because the project ships all operation binaries
together with the VMM (see `build.sh`).

The fuzz harness's mock CallTable in `src/fuzz/src/lib.rs`
gains a matching no-op stub:

```rust
unsafe extern "C" fn mock_send_measure_result(_r: *const shared::MeasureResult) {}
```

#### New shared types

Add to `src/shared/src/lib.rs`:

```rust
/// Configuration for the measure operation.
///
/// Written to OPERATION_CONFIG_ADDR by the VMM before launching
/// the measure guest binary. The guest reads this directly via
/// `&*(OPERATION_CONFIG_ADDR as *const MeasureConfig)`.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct MeasureConfig {
    /// Magic number (0x4D454153 = "MEAS")
    pub magic: u32,
    /// Target output format (`ImageFormat as u32`). Only RAW,
    /// QCOW2, VMDK, VHD, and VHDX are valid for measure.
    pub target_format: u32,
    /// Configuration flags (see FLAG_* constants).
    pub flags: u32,
    /// Padding for alignment.
    pub _pad: u32,

    /// Non-zero virtual size in `--size` mode (skip source scan).
    /// Zero means "use the source device".
    pub virtual_size_override: u64,

    // qcow2 options
    /// Output cluster size in bytes (qcow2). 0 = default (65536).
    pub qcow2_cluster_size: u32,
    /// Refcount entry width in bits (qcow2). 0 = default (16).
    pub qcow2_refcount_bits: u8,
    /// VMDK subformat selector (0=MonolithicSparse, 1=StreamOptimized,
    /// 2=MonolithicFlat).
    pub vmdk_subformat: u8,
    /// Reserved padding.
    pub _pad2: u16,
    /// VMDK grain size in bytes. 0 = default (65536).
    pub vmdk_grain_size: u32,
    /// VHD subformat selector (0=Dynamic, 1=Fixed).
    pub vhd_subformat: u8,
    /// Reserved padding.
    pub _pad3: [u8; 3],
    /// VHD/VHDX block size in bytes. 0 = use format default.
    pub block_size: u32,
    /// LUKS-in-qcow2 header overhead in bytes (0 = no LUKS).
    pub luks_header_overhead: u64,
}

impl MeasureConfig {
    pub const MAGIC: u32 = 0x4D454153; // "MEAS"

    /// Flag: produce qcow2 extended L2 (16-byte entries).
    pub const FLAG_EXTENDED_L2: u32 = 1 << 0;
    /// Flag: enable qcow2 lazy refcounts. Accepted but ignored for size.
    pub const FLAG_LAZY_REFCOUNTS: u32 = 1 << 1;
    /// Flag: produce qcow2 v3 (compat 1.1). Default true; clear for v2.
    pub const FLAG_COMPAT_V3: u32 = 1 << 2;
    /// Flag: qcow2 compressed output. Does not change required.
    pub const FLAG_COMPRESS: u32 = 1 << 3;
    /// Flag: preallocation mode (low 2 bits in dedicated nibble — see
    /// preallocation()/set_preallocation() helpers).
    pub const PREALLOC_MASK: u32 = 0b11 << 4;
    pub const PREALLOC_OFF: u32 = 0 << 4;
    pub const PREALLOC_METADATA: u32 = 1 << 4;
    pub const PREALLOC_FALLOC: u32 = 2 << 4;
    pub const PREALLOC_FULL: u32 = 3 << 4;

    pub fn is_valid(&self) -> bool { self.magic == Self::MAGIC }
    pub fn preallocation(&self) -> u32 { self.flags & Self::PREALLOC_MASK }
}

/// Result structure for the measure operation.
///
/// Returned via send_measure_result call table function.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct MeasureResult {
    /// Magic (0x4D524553 = "MRES").
    pub magic: u32,
    /// Target format echoed back so the host can render the right output.
    pub target_format: u32,
    /// Bytes required when only allocated extents are written.
    pub required: u64,
    /// Bytes required when every cluster/grain/block is allocated.
    pub fully_allocated: u64,
    /// Cluster/grain/block size actually used after resolving defaults
    /// (host renders this in JSON output where qemu-img varies).
    pub resolved_unit_size: u32,
    /// Error code: 0 = ok, non-zero = MeasureError variant from
    /// crates/measure (1=Overflow, 2=InvalidOption, 3=InvalidSize).
    pub error: u32,
}

impl MeasureResult {
    pub const MAGIC: u32 = 0x4D524553;

    pub const ERROR_OK: u32 = 0;
    pub const ERROR_OVERFLOW: u32 = 1;
    pub const ERROR_INVALID_OPTION: u32 = 2;
    pub const ERROR_INVALID_SIZE: u32 = 3;

    pub fn is_valid(&self) -> bool { self.magic == Self::MAGIC }
    pub const fn ok(target: u32, m: crate::measure::MeasureOutput) -> Self { ... }
}
```

(The `MeasureResult::ok` constructor uses `MeasureOutput` from
`crates/measure/`. Since `shared` cannot depend on `measure`
without creating the cycle phase 2 fixed, the constructor lives
in the *guest binary*, not `shared`. The struct itself stays
plain `#[repr(C)]` data.)

Magic value choices (verified unique against existing struct
magics in `shared/src/lib.rs`):
- `MeasureConfig::MAGIC = 0x4D454153 = "MEAS"` — does not
  collide with `CHEC`, `CMPR`, `CONV`, `COPY`, `INFO`.
- `MeasureResult::MAGIC = 0x4D524553 = "MRES"` — does not
  collide with `CHRS`, `CMRS`, `RESU`.

#### New protobuf message

In `crates/guest-protocol/proto/guest.proto`:

```proto
message MeasureResultMessage {
  // Target format echoed back. e.g. "raw", "qcow2", "vmdk", "vhd", "vhdx".
  string target_format = 1;
  // Bytes required when only allocated extents are written.
  uint64 required = 2;
  // Bytes required when every cluster/grain/block is allocated.
  uint64 fully_allocated = 3;
  // Cluster / grain / block size actually used (0 if not applicable).
  uint32 resolved_unit_size = 4;
  // Error code: 0 = ok, non-zero values mirror MeasureResult::ERROR_*.
  uint32 error = 5;
}
```

Extend the `GuestMessage` oneof:

```proto
message GuestMessage {
  Level level = 1;
  oneof payload {
    InitMessage init = 2;
    CapacityMessage capacity = 3;
    ProgressMessage progress = 4;
    ErrorMessage error = 5;
    CompleteMessage complete = 6;
    InfoResultMessage info_result = 7;
    CheckResultMessage check_result = 8;
    CompareResultMessage compare_result = 9;
    MeasureResultMessage measure_result = 10;
  }
}
```

Field number 10 — confirmed by reading the current proto that
9 is the last assigned tag.

Add a helper in `crates/guest-protocol/src/lib.rs`:

```rust
pub fn measure_result_message(
    target_format: &str,
    required: u64,
    fully_allocated: u64,
    resolved_unit_size: u32,
    error: u32,
) -> guest_::GuestMessage { ... }
```

The proto regen is automatic — the existing build.rs handles
micropb code generation.

### Guest binary layout

`src/operations/measure/` follows the same layout as
`src/operations/info/`:

```
Cargo.toml      — name = "measure", deps: shared, measure,
                  qcow2, vmdk, vhd, vhdx, raw
linker.ld       — identical to other operations (load 0x20000)
src/main.rs     — entry point per the algorithm below
```

The Cargo.toml mirrors `convert/Cargo.toml` for feature flags
on `qcow2` (decompress / decompress-zstd for compressed source
support — measure walks metadata only, but the same parser
crate is depended on, and clipping features at the measure
op's boundary keeps the binary lean), plus `shared` and the new
`measure` crate.

#### Cargo features and binary size

The 384 KB cap (`OPERATION_MAX_SIZE = 0x60000`) is enforced by
`scripts/check-binary-sizes.sh`. Reference: `info` is the
smallest and lightest operation. The measure binary should
land well under the cap because it uses the parser crates'
*metadata-walking* paths only — no decompression, no
encryption, no chain composition. Bring in the minimum set of
features:

```toml
[dependencies]
shared = { path = "../../shared" }
measure = { path = "../../crates/measure" }
qcow2 = { path = "../../crates/qcow2" }     # no decompress, no aes
raw = { path = "../../crates/raw" }
vmdk = { path = "../../crates/vmdk" }
vhd = { path = "../../crates/vhd" }
vhdx = { path = "../../crates/vhdx" }
```

If the binary exceeds the cap after first build, audit which
feature-gated code is being pulled in (cargo bloat is the
diagnostic). Likely culprits: aes / argon2 if they accidentally
leak via the qcow2 crate's default features. Confirm during
step 3f.

#### Guest entry-point algorithm

```rust
#[no_mangle]
pub unsafe extern "C" fn _start() -> u64 {
    let call_table = get_call_table();
    validate_call_table!(call_table, "measure");

    // 1. Read config.
    let config = &*(OPERATION_CONFIG_ADDR as *const MeasureConfig);
    if !config.is_valid() {
        send_measure_error(call_table, ImageFormat::Unknown,
                           MeasureResult::ERROR_INVALID_OPTION);
        return 0;
    }

    // 2. Build AllocationSummary, either from --size override or by
    //    scanning device 0.
    let summary = if config.virtual_size_override != 0 {
        // --size mode: produce both required (empty) and
        // fully_allocated (full) by calling measure twice with
        // different allocated_bytes. The crates/measure functions
        // already return both fields from one call when
        // allocated_bytes == 0, so a single call suffices: required
        // is what we want for the "empty" answer, fully_allocated
        // for the "full" answer. We pass allocated_bytes = 0.
        AllocationSummary {
            virtual_size: config.virtual_size_override,
            allocated_bytes: 0,
        }
    } else {
        // Source-image mode: detect format on device 0, run the
        // matching parser's scan_allocation.
        match detect_and_scan(call_table) {
            Some(s) => s,
            None => {
                send_measure_error(call_table, ImageFormat::Unknown,
                                   MeasureResult::ERROR_INVALID_SIZE);
                return 0;
            }
        }
    };

    // 3. Compute the measure output for the target format.
    let target = ImageFormat::from_u32(config.target_format);
    let out = match target {
        ImageFormat::Raw   => measure::measure_raw(summary.virtual_size),
        ImageFormat::Qcow2 => measure::measure_qcow2(&summary, &qcow2_opts_from(config)),
        ImageFormat::Vmdk  => measure::measure_vmdk(&summary, &vmdk_opts_from(config)),
        ImageFormat::Vhd   => measure::measure_vhd(&summary, &vhd_opts_from(config)),
        ImageFormat::Vhdx  => measure::measure_vhdx(&summary, &vhdx_opts_from(config)),
        _ => Err(measure::MeasureError::InvalidOption),
    };

    // 4. Build and send the result.
    let result = match out {
        Ok(o)  => MeasureResult { magic: ..., target_format, required: o.required,
                                  fully_allocated: o.fully_allocated,
                                  resolved_unit_size: resolved_unit_size(config, target),
                                  error: MeasureResult::ERROR_OK },
        Err(e) => MeasureResult { magic: ..., target_format, required: 0,
                                  fully_allocated: 0, resolved_unit_size: 0,
                                  error: map_measure_error(e) },
    };
    (call_table.send_measure_result)(&result);
    (call_table.send_complete)(b"measure\0".as_ptr(), 0, true);

    0
}

unsafe fn detect_and_scan(ct: &CallTable) -> Option<AllocationSummary> {
    // Read first sector, run shared::format_detection::detect_format_from_header
    // (already used by info / check / convert). Dispatch to the matching
    // parser's *State::scan_allocation. Format-specific initialisation
    // mirrors what info does: allocate cache buffers in scratch memory
    // (positions defined by the SCRATCH_MEM_BASE layout), call the
    // parser's init function, then scan_allocation.
    ...
}
```

Cache-buffer allocation in scratch memory follows the `info`
operation pattern — measure only needs the L1/L2 (qcow2) or
GD/GT (vmdk) or BAT (vhd/vhdx) cache buffers, *not* the
decompression buffers. Define a small `ScratchLayout` struct
local to the measure binary.

#### What the guest does *not* do in phase 3

- No backing-chain composition. Single-device source only.
  (Chain support is a phase-3 follow-up — see Open questions
  #1 — or deferred to phase 4 when the host CLI is wired.)
- No LUKS-encrypted source decryption. Native LUKS measurement
  is master-plan future work.
- No `-o cluster_size=N` or `-o block_size=N` parsing on the
  guest side — those land already-resolved in `MeasureConfig`.
  Parsing is phase 4's job.
- No snapshot extraction (`-l SNAPSHOT`). Master-plan future
  work.

### Host wiring (minimal)

Phase 3's host changes are limited to what is needed to handle
the new protobuf message without crashing if a stray
`MeasureResult` arrives, plus building the new function
pointer in the VMM-side CallTable that the core guest sees:

- `src/core/src/serial.rs`: `pub fn send_measure_result(result:
  &shared::MeasureResult)` builds the protobuf and calls
  `send_message(&msg)`.
- `src/core/src/main.rs`: `ct_send_measure_result(*const
  MeasureResult)` wrapper, wired into the `CallTable { ... }`
  literal next to `ct_send_compare_result`.
- `src/vmm/src/main.rs`: `format_message` adds an arm for
  `Some(guest_::GuestMessage_::Payload::MeasureResult(m))`
  that produces a debug-log string mirroring the existing
  `info_result` / `check_result` arms. **No** new `run_measure`
  function; **no** new `Commands::Measure` variant; **no** new
  `MeasureArgs` struct.

### Build infrastructure

- `src/build.sh`: add a new section for the measure operation
  (cargo build, rust-objcopy ELF → bin) following the
  copy/check/compare/convert pattern.
- `Makefile`: add `measure` to `make clean-instar` target if
  it enumerates operation binaries explicitly; otherwise no
  change.
- `scripts/check-binary-sizes.sh`: add `measure` to the loop
  in line 65 (`for op in info copy check compare convert; do`
  becomes `for op in info copy check compare convert measure;
  do`).
- `src/Cargo.toml`: add `operations/measure` to the workspace
  `members` list, right after `operations/convert`.

## Open questions

1. **Backing-chain composition**: should phase 3 ship
   single-device measure (no chain) and defer chain support
   to a phase-3 follow-up step, or build it in? Recommendation:
   **defer to a follow-up step or to phase 4**. Chain support
   needs at minimum a `ChainConfig` write path on the host,
   guest-side iteration across devices, and a union/shadow
   rule for overlapping allocations. Phase 3 has enough moving
   parts already. The single-device path covers `--size` mode
   and the majority of the source-image case (top of chain
   only). The active layer is what `qemu-img measure` reports
   when there's no chain, and it is a sound *lower* bound for
   the chained case — match qemu-img's no-chain behaviour
   first, then add chain awareness.

2. **`MeasureResult::ok()` constructor location**: the natural
   home is `shared/src/lib.rs`, but the constructor wants a
   `crates::measure::MeasureOutput` value, and `shared` can't
   depend on `measure`. Recommendation: **the guest binary
   builds the result manually**, no helper constructor in
   `shared`. Add an `impl MeasureResult` with the `MAGIC` and
   `ERROR_*` constants only; the four-field constructor pattern
   lives in `operations/measure/src/main.rs`.

3. **`resolved_unit_size` semantics**: for `qcow2` the unit
   is `cluster_size`; for `vmdk` it's `grain_size`; for
   `vhd` and `vhdx` it's `block_size`. For `raw` there is no
   unit — set to 0. Confirm phase 4's JSON output formatter
   matches qemu-img's "info" subset; if qemu-img doesn't emit
   a unit size for measure (it doesn't — only required and
   fully-allocated), we still report it for our own JSON
   audit trail. Mark as instar-specific JSON extra in
   `docs/quirks.md`.

4. **`MeasureConfig` size budget**: the struct is ~64 bytes
   excluding padding. The config region at
   `OPERATION_CONFIG_ADDR` is 4 KiB. Plenty of room. No
   issue, just noting for future extensions (e.g. snapshot
   ID, image-opts strings).

5. **Sector size**: every other operation reads a
   `sector_size` field from its config. Measure does too —
   add it before `qcow2_cluster_size`. Default 65536. Reused
   by `init` for each parser state. (Note: this field is
   present in the struct above implicitly via `..reserved`;
   make it explicit during step 3a.)

6. **What if the source is `unknown` format?** The scanner
   returns `None`, and the result reports
   `ERROR_INVALID_SIZE`. Phase 4 renders this as a clear
   error message to the user. The `--size` override path
   bypasses detection entirely. Document in the operation's
   doc comment.

7. **Subcluster-aware extended-L2 measurement on the source
   side**: the scanner in phase 2 already handles this. No
   additional logic in phase 3 — the guest just calls
   `Qcow2State::scan_allocation` which returns the correct
   `allocated_bytes`.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Add `MeasureConfig` and `MeasureResult` to `src/shared/src/lib.rs` per the schema above. Include `sector_size: u32` as an explicit field. Magic constants `0x4D454153` ("MEAS") and `0x4D524553` ("MRES"). `impl` blocks expose `MAGIC`, `is_valid`, `ERROR_*` constants, `FLAG_*` constants, `PREALLOC_*` constants, and a `preallocation()` accessor. No `MeasureResult::ok` constructor (kept in the guest binary; `shared` cannot depend on `measure`). Add ~4 unit tests inside `shared`'s existing `#[cfg(test)] mod tests` verifying magic uniqueness, `is_valid`, `preallocation()`, and the FLAG/PREALLOC bit layout. Run `make lint`, `make test-rust`, `pre-commit run --all-files`. Only `src/shared/src/lib.rs` changes. |
| 3b | medium | sonnet | none | Add `MeasureResultMessage` to `crates/guest-protocol/proto/guest.proto` as field 10 in the `GuestMessage` oneof (after `compare_result = 9`). Fields per the schema above. Then add a `pub fn measure_result_message(...)` helper in `crates/guest-protocol/src/lib.rs` mirroring `check_result_message` and `compare_result_message`. Run `make lint` (the proto regen runs as part of cargo build), `make test-rust`. Verify the generated rust module exposes `MeasureResultMessage` and the oneof variant `Payload::MeasureResult`. Touch only `crates/guest-protocol/proto/guest.proto` and `crates/guest-protocol/src/lib.rs`. |
| 3c | high | opus | none | Add the `send_measure_result` function pointer to `CallTable` in `src/shared/src/lib.rs` (immediately after `send_compare_result`). Bump `pub const VERSION: u32 = 14;`. Add the matching `mock_send_measure_result` no-op stub to `src/fuzz/src/lib.rs` and include it in `build_call_table()`. Run `make lint`, `make test-rust`, `pre-commit run --all-files`. The version bump is the breaking change; everything must rebuild cleanly. **High effort because:** the CallTable layout is consumed by the guest via raw memory casts. Any field ordering mistake silently miscompiles. The sub-agent must read the existing `CallTable { ... }` literal in `src/core/src/main.rs:261` and confirm field-order parity. |
| 3d | medium | sonnet | none | Wire the new function pointer through core. Add `pub fn send_measure_result(result: &shared::MeasureResult)` to `src/core/src/serial.rs` (builds the protobuf via `guest_protocol::measure_result_message(...)`, calls `send_message(&msg)`). Add `ct_send_measure_result(result: *const MeasureResult)` wrapper to `src/core/src/main.rs` mirroring `ct_send_check_result` at line 633. Add `send_measure_result: ct_send_measure_result,` to the `CallTable { ... }` literal at line 261. Run `make lint`, `make test-rust`. Touches `src/core/src/main.rs` and `src/core/src/serial.rs`. |
| 3e | medium | sonnet | none | Extend `format_message` in `src/vmm/src/main.rs` (around line 645–680) to handle `Some(guest_::GuestMessage_::Payload::MeasureResult(m))`, producing a debug-log string. No CLI or render — phase 4 owns that. The main event loop already routes anything in `format_message` through `debug!`; no changes needed there. Run `make instar`, `make lint`. Touches only `src/vmm/src/main.rs`. |
| 3f | high | opus | none | Create the new guest binary `src/operations/measure/` (`Cargo.toml`, `linker.ld`, `src/main.rs`) following the layout of `src/operations/info/`. The `src/main.rs` implements the algorithm described in the "Guest entry-point algorithm" section above: read MeasureConfig, build AllocationSummary (either from virtual_size_override or by detecting source format + calling the matching `scan_allocation`), call the matching `crates/measure/` function, send result via `(call_table.send_measure_result)(&result)`. Use the existing format_detection helpers. Allocate scratch-memory cache buffers for the parsers (L1/L2 caches for qcow2, GD/GT for vmdk, BAT for vhd/vhdx) at fixed offsets within `SCRATCH_MEM_BASE`; follow `operations/info/src/main.rs` for the pattern. **No** backing chain support, **no** LUKS decryption, **no** snapshot extraction — all out of scope. Cargo.toml deps minimal (no decompress / aes / argon2 features) to keep binary under the 384 KiB cap. Add `operations/measure` to the workspace `members` list in `src/Cargo.toml`. Run `make instar`, `make lint`, `make test-rust`, `make check-binary-sizes`. **High effort because:** this binary ties together every previous step — config reading, format dispatch, scanner invocation, calculator invocation, result construction, error mapping. Subtle bugs here surface as silent wrong numbers in phase 7. |
| 3g | low | sonnet | none | Update `src/build.sh` to include a "measure" section following the copy/check/compare/convert pattern (cargo build → rust-objcopy → measure.bin). Update `scripts/check-binary-sizes.sh` line 65 to add `measure` to the `for op in info copy check compare convert; do` loop. If `Makefile` enumerates operation binaries (search for the list explicitly), update accordingly. Run `make instar`, `make check-binary-sizes`. Confirm `measure.bin` lands < 384 KiB. Reports the actual size to verify margin. |
| 3h | low | sonnet | none | Update `ARCHITECTURE.md` to mention the new `operations/measure/` binary (one paragraph mirroring the other operation entries) and the new `MeasureConfig` / `MeasureResult` structs in shared. Update `CHANGELOG.md` Unreleased / Added with one line citing the new operation binary, new protobuf message, and CallTable version bump (13 → 14). Mention that the CLI surface ships in phase 4. Run `pre-commit run --all-files`. |

Total: 8 commits.

### Out of scope for phase 3

- `Commands::Measure` clap variant and `MeasureArgs` struct
  (phase 4).
- `run_measure()` host orchestration function (phase 4).
- `-o key=value,...` option parsing (phase 5).
- `--size` value parsing on the host (phase 4 — uses the
  existing `parse_memory_size` helper).
- `--snapshot` / `-l SNAPSHOT` (master-plan future work).
- Backing-chain composition (Open question #1; deferred).
- Native LUKS source decryption (master-plan future work).
- Cross-version baseline generation (phase 6).
- Integration tests against real testdata images (phase 7) —
  the new boundary is exercised end-to-end only after phase 4
  ships the CLI.
- Fuzz target updates (phase 8 — the new `measure_result_msg`
  helper is reachable but no fuzz target consumes it yet).

## Success criteria

- `src/shared/src/lib.rs` defines `MeasureConfig` and
  `MeasureResult` (magic, fields, `is_valid`, FLAGs, errors).
- `crates/guest-protocol/proto/guest.proto` carries
  `MeasureResultMessage` as field 10 in the `GuestMessage`
  oneof.
- `CallTable::VERSION == 14`. `send_measure_result` function
  pointer is the last field. Mock CallTable in `src/fuzz/`
  has a matching stub.
- `src/operations/measure/measure.bin` builds and lands well
  under 384 KiB (target: < 200 KiB).
- `make instar` builds the full toolchain.
- `make lint` is clean.
- `make test-rust` passes; new tests in `shared` increase
  total by ~4.
- `make check-binary-sizes` passes with `measure.bin` listed.
- `pre-commit run --all-files` passes.
- `ARCHITECTURE.md` and `CHANGELOG.md` are updated.
- No `Commands::Measure` clap variant exists yet (phase 4).

## Risks and mitigations

- **CallTable version bump breaks the build until every
  operation is rebuilt**. Mitigation: `build.sh` builds every
  operation in one pass; if a stale binary is loaded, the
  `validate_call_table!` macro returns 0 with a clear log
  message. The risk is failure-stop, not undefined behaviour.
- **`measure.bin` exceeds 384 KiB**. Mitigation: step 3f's
  brief calls out the feature-gate audit. The measure
  binary's code is ~600 LoC of orchestration plus the parsers'
  scan_allocation path only — well under the cap. If it
  exceeds, `cargo bloat -p measure --release` identifies the
  bloat source.
- **Magic value collisions**. Mitigation: 3a's brief checks
  uniqueness via grep across `shared/src/lib.rs`. The chosen
  values ("MEAS", "MRES") are visibly disjoint from existing
  ones.
- **Proto regen failure on micropb**. Mitigation: existing
  `build.rs` handles regen; if a sub-agent runs into an issue
  it is almost certainly a syntax problem in the proto file.
  Step 3b's brief includes a `make test-rust` checkpoint to
  surface regen errors immediately.
- **Silent ABI drift between guest and host CallTable**. The
  CallTable is consumed via raw memory cast, so a field-order
  mismatch between core's literal at line 261 and shared's
  struct definition is invisible to the compiler. Mitigation:
  3c's brief explicitly tells the sub-agent to diff-check the
  field order between `shared::CallTable` and core's literal.
- **`MeasureResult::ok` constructor cycle**: avoided by
  building the result in the guest binary, not in `shared`
  (Open question #2). The struct in `shared` is plain
  `#[repr(C)]` data; the guest binary owns the construction
  logic.

## Back brief

Before executing any step, the executing agent should
back-brief: which file is being edited, which existing
operation is the closest template, and which parts of the
new code involve raw memory casts (`MeasureConfig` read,
`CallTable` extension). The reviewer should verify no step
bleeds into phase 4 (clap), phase 5 (-o parsing), phase 6
(baselines), or phase 7 (integration tests).
