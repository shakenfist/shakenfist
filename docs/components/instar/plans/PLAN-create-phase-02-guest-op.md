# PLAN-create phase 2: guest `create` operation binary

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, KVM, virtio,
disk image formats), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

This is a phase plan under `PLAN-create.md`. Refer to that master
plan for overall context, mission, and the multi-phase plan
structure. Phase 1 (`PLAN-create-phase-01-emitters.md`) shipped
the library code this phase consumes.

## Mission

Land a new bare-metal guest binary at `src/operations/create/`
that:

1. Reads a new `CreateConfig` struct from `OPERATION_CONFIG_ADDR`.
2. Optionally reads the *header* of a backing image from input
   device 0 (when `virtual_size == 0` and a backing reference is
   present) to recover the backing's `virtual_size`.
3. Calls the appropriate `crates/create::plan_*` function for the
   target format with options translated from `CreateConfig`.
4. Iterates the returned `MetadataPlan` and writes each entry to
   the output device via the call-table's existing
   `write_output_sector` function pointer.
5. Sends a `CreateResultMessage` over the serial command channel
   summarising what was written.

Phase 2 ships **compiled guest code only**. The host VMM is *not*
modified to call this binary — that wiring belongs to phase 3
(the host CLI subcommand). End-to-end validation across guest +
host happens in phase 3. Phase 2's success criteria are limited
to: the binary builds, fits the 384 KiB cap, the workspace
continues to build and test cleanly, and the new struct +
protobuf changes round-trip through their unit tests.

## What the survey turned up

### Memory layout (`src/shared/src/lib.rs`)

- Guest binaries load at `OPERATION_LOAD_ADDR = 0x20000` with a
  hard 384 KiB ceiling (`OP_MAX = 0x60000`) enforced by
  `src/build.sh`'s `check_size`.
- Config structs land at `OPERATION_CONFIG_ADDR = 0x81000`. The
  host writes; the guest reads.
- Call table lives at `CALL_TABLE_ADDR = 0x80000`.
- Operation-side scratch lives between `SCRATCH_MEM_BASE = 0x300000`
  (3 MiB) and `SCRATCH_MEM_END = 0xFF0000` (~16 MiB), minus a
  512 KiB allocator heap at the top. **Net usable scratch is
  ~11.9 MiB** — the relevant cap for create's working buffers.
- `MAX_SECTOR_SIZE = 64 KiB`; `MAX_CLUSTER_SIZE = 2 MiB`.

The ~11.9 MiB scratch budget is in tension with phase 1's
`QCOW2_MAX_METADATA_SCRATCH = 32 MiB`. See "Scratch budget"
below for how phase 2 resolves this.

### Existing operation binary pattern (`src/operations/measure/src/main.rs`)

`measure` is the closest precedent (200-line entry function, no
`std`, no allocator). The shape we follow:

- `#![no_std] #![no_main]` with a `_start` extern function and a
  panic handler that just spins.
- Read the operation config: `&*(OPERATION_CONFIG_ADDR as
  *const CreateConfig)`.
- Validate magic (`is_valid()`) and a couple of plausibility
  checks on critical fields (e.g. `sector_size`) — defence in
  depth against a corrupted config region. On failure, send a
  `Result` with an error code and `send_complete(..., false)`.
- Carve scratch into named regions using `const` byte offsets
  derived from `SCRATCH_MEM_BASE`.
- Send the result message via the matching call-table function
  (`send_measure_result` for measure; `send_create_result` for
  create — phase 2 adds it).
- Finish with `send_complete(b"create\0".as_ptr(), bytes_written,
  success)`.

### Call-table + serial pipeline

- `CallTable` (`src/shared/src/lib.rs:482`) is a `#[repr(C)]`
  struct of `unsafe extern "C" fn` pointers. Adding a new entry
  (e.g. `send_create_result`) requires touching `CallTable`,
  `core`'s `validate_call_table!` invocation, and `core`'s init
  code in `src/core/src/main.rs:274`-ish.
- `core` exposes serialisation helpers in
  `src/core/src/serial.rs`: each operation result type has its
  own `pub fn send_<op>_result(...)` that builds the matching
  protobuf message via `guest_protocol::<op>_result_message(...)`
  and sends it framed.
- `crates/guest-protocol/src/lib.rs` exposes pure builders for
  every `<Op>ResultMessage`. The protobuf source is
  `crates/guest-protocol/proto/guest.proto`; bindings regenerate
  on build via `micropb`.
- `core`'s `ct_send_<op>_result` thunks (in
  `src/core/src/main.rs:648` for measure) translate the
  `#[repr(C)]` shared struct into the
  `serial::send_<op>_result` call.

### Existing build wiring

- `src/Cargo.toml` workspace `members` lists every binary.
  `core`, `info`, `copy`, `check`, `compare`, `convert`,
  `measure-op` are excluded from `cargo test --workspace` runs
  in `scripts/check-rust.sh:58,68` and `Makefile:494-500`.
- `src/build.sh` has hand-rolled stanzas per binary
  (`BUILD/CONVERT/MEASURE_BIN`, `cd operations/<name> && cargo
  +nightly build --release`, `rust-objcopy -O binary`, copy to
  `target/release/`, `check_size`). Phase 2 adds a parallel
  `CREATE_BIN` block.
- `Makefile`'s `test-rust` target gained a `cargo test -p create`
  invocation in phase 1; this phase doesn't change that.

### Protobuf wire format (`MeasureResultMessage` as reference)

`guest.proto:194-207` defines `MeasureResultMessage` with a
mix of `string`/`uint64`/`uint32` fields. The wrapping
`GuestMessage.payload` oneof has it at field 10
(`measure_result = 10;`). Phase 2 adds
`create_result = 11`.

## Public types added in phase 2

### `CreateConfig` (in `src/shared/src/lib.rs`)

```rust
/// Configuration for the create operation.
///
/// Written to `OPERATION_CONFIG_ADDR` by the VMM before
/// launching the create guest binary. The guest reads this
/// directly via `&*(OPERATION_CONFIG_ADDR as *const CreateConfig)`.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct CreateConfig {
    /// Magic (`0x43524541` = "CREA").
    pub magic: u32,
    /// Target output format (`ImageFormat as u32`).
    pub target_format: u32,
    /// Flags (FLAG_EXTENDED_L2, FLAG_LAZY_REFCOUNTS,
    /// FLAG_COMPAT_V3, FLAG_VHD_FIXED, FLAG_VMDK_STREAM_OPT,
    /// FLAG_BACKING_UNSAFE, ...).
    pub flags: u32,
    /// Sector size for I/O (matches host sector_size).
    pub sector_size: u32,

    /// Virtual disk size in bytes. Zero means "default from
    /// backing file" — the guest reads input device 0's header
    /// to recover the backing virtual size.
    pub virtual_size: u64,

    /// qcow2 cluster size in bytes. 0 = default (65536).
    pub qcow2_cluster_size: u32,
    /// qcow2 refcount entry width in bits. 0 = default (16).
    pub qcow2_refcount_bits: u8,
    /// vmdk subformat: 0=MonolithicSparse, 1=StreamOptimized.
    pub vmdk_subformat: u8,
    /// vhd subformat: 0=Dynamic, 1=Fixed.
    pub vhd_subformat: u8,
    /// Reserved padding.
    pub _pad: u8,
    /// vmdk grain size in bytes. 0 = default (65536).
    pub vmdk_grain_size: u32,
    /// vhd/vhdx block size in bytes. 0 = format default.
    pub block_size: u32,

    /// Length of the backing-file path in bytes. 0 = no backing.
    pub backing_file_len: u32,
    /// Backing-file path (UTF-8 / locale-bytes, no NUL terminator).
    /// Only the first `backing_file_len` bytes are valid.
    pub backing_file: [u8; CREATE_CONFIG_MAX_BACKING_FILE],
    /// Backing-file format (`ImageFormat as u32`). 0 = unset.
    pub backing_format: u32,

    /// Reserved padding for forward compatibility (zero-init).
    pub _reserved: [u8; 64],
}

pub const CREATE_CONFIG_MAX_BACKING_FILE: usize = 1024;
```

Magic field uses 4 ASCII bytes `"CREA"` per the convention of
the other Config structs. `is_valid()` checks the magic alone;
the guest also validates `sector_size` and `target_format`
ranges defensively (matching measure's pattern).

The flag bits:
- `FLAG_EXTENDED_L2 = 1 << 0`
- `FLAG_LAZY_REFCOUNTS = 1 << 1`
- `FLAG_COMPAT_V3 = 1 << 2` — default-on if `flags == 0`; clear
  for qcow2 v2.
- `FLAG_VHD_FIXED = 1 << 3` — vhd_subformat selector (could be a
  byte instead — kept as a flag for symmetry with measure's
  approach; the byte field above is redundant in this case, so
  phase 2 picks one or the other when writing the struct).

Open: pick byte field vs flag bit consistently. Recommendation:
keep the `vhd_subformat` byte field and drop the flag bit;
matches `MeasureConfig.vhd_subformat`.

### `CreateResultMessage` (in `crates/guest-protocol/proto/guest.proto`)

```proto
message CreateResultMessage {
  // Target format echoed back (e.g. "raw", "qcow2", ...).
  string target_format = 1;
  // Resolved virtual size in bytes (echoes opts.virtual_size,
  // or the backing-file-derived size when virtual_size was 0).
  uint64 resolved_virtual_size = 2;
  // Bytes the guest actually wrote (sum of MetadataWrite lengths).
  uint64 metadata_bytes_written = 3;
  // File size after the guest finishes (max byte_offset + len
  // across the plan; the host may grow this for preallocation).
  uint64 file_size_after = 4;
  // Resolved cluster/grain/block size. 0 for raw.
  uint32 resolved_unit_size = 5;
  // Error code: 0 = ok, non-zero mirrors CreateResult::ERROR_*.
  uint32 error = 6;
}
```

Wrapped into the `GuestMessage.payload` oneof at field 11.

### `CreateResult` (in `src/shared/src/lib.rs`)

The `#[repr(C)]` companion to the protobuf message, passed by
the guest into `call_table.send_create_result`. Mirrors
`MeasureResult`'s shape. ERROR_* codes:

```rust
impl CreateResult {
    pub const MAGIC: u32 = 0x43524553; // "CRES"
    pub const ERROR_OK: u32 = 0;
    pub const ERROR_INVALID_OPTION: u32 = 1;
    pub const ERROR_INVALID_SIZE: u32 = 2;
    pub const ERROR_SCRATCH_TOO_SMALL: u32 = 3;
    pub const ERROR_BACKING_READ_FAILED: u32 = 4;
    pub const ERROR_BACKING_PARSE_FAILED: u32 = 5;
    pub const ERROR_BACKING_TOO_LONG: u32 = 6;
    pub const ERROR_WRITE_FAILED: u32 = 7;
    pub const ERROR_UNSUPPORTED_FORMAT: u32 = 8;
}
```

### `CallTable::send_create_result`

New function pointer in the `CallTable` struct:

```rust
/// Send create result message.
/// Args: create_result pointer containing what was written.
pub send_create_result: unsafe extern "C" fn(*const CreateResult),
```

`core` initialises it (`src/core/src/main.rs:274` block) and
implements the thunk (`ct_send_create_result`) that calls
`serial::send_create_result`.

`serial::send_create_result` builds the
`guest_protocol::create_result_message(...)` envelope and sends
it framed. A new builder lives in
`crates/guest-protocol/src/lib.rs` mirroring
`measure_result_message`.

## Guest scratch design

Phase 1's `*_MAX_METADATA_SCRATCH` consts are theoretical upper
bounds for the *library* — they include exotic combinations
like `cluster_size=512 + extended_l2 + virtual_size=32 GiB`
(needs ~16 MiB just for L1). The guest cannot afford a 32 MiB
static buffer.

Two-part resolution:

1. **Phase 2 tightens the guest envelope.** A new const
   `GUEST_CREATE_SCRATCH_LIMIT` (say 8 MiB) is what the guest
   actually allocates for the create scratch region. Calls that
   need more land back in `Err(CreateError::ScratchTooSmall)` →
   `ERROR_SCRATCH_TOO_SMALL`. The host (phase 3) renders this
   as `error: option combination exceeds guest scratch (try a
   larger cluster size)`.

2. **Phase 1's const is not modified in phase 2.** The integration
   test sweep in `crates/create/tests/round_trip.rs` already uses
   the public const; it should keep passing because it's a host-
   side allocation. Phase 2 only constrains what the *guest*
   chooses to allocate at runtime.

Concrete scratch layout for the create guest:

```text
SCRATCH_MEM_BASE                  : header probe buffer (1 sector)
SCRATCH_MEM_BASE + 64 KiB         : backing-file header buffer
                                    (parser cache A, 1 sector)
SCRATCH_MEM_BASE + 128 KiB        : parser cache B (1 sector)
SCRATCH_MEM_BASE + 192 KiB        : create scratch region
                                    (GUEST_CREATE_SCRATCH_LIMIT bytes)
... gap ...
ALLOC_HEAP_BASE                   : (allocator heap, not used by create)
```

With `GUEST_CREATE_SCRATCH_LIMIT = 8 MiB` we land at
`0x300000 + 192 KiB + 8 MiB ≈ 0x8B0000`, comfortably below
`ALLOC_HEAP_BASE`. Document the resulting envelope:

- qcow2 default (cluster_size=64 KiB): virtual_size up to
  petabyte-class (L1 stays tiny).
- qcow2 cluster_size=512: virtual_size up to ~32 GiB
  (extended_l2) or ~64 GiB (standard L2). Past this:
  `ERROR_SCRATCH_TOO_SMALL`.
- vmdk / vhd / vhdx: comfortably within the limit at every
  realistic option combination (their scratch needs are < 2 MiB).

## Backing-file header lookup

When `CreateConfig.virtual_size == 0` and `backing_file_len > 0`:

1. Read the first sector of input device 0 into the header
   probe buffer (one `read_input_sector` call).
2. Detect the format using `shared::format_detection::
   detect_format_from_header` — same helper measure already uses.
   Reject formats not in {raw, qcow2, vmdk, vhd, vhdx} with
   `ERROR_BACKING_PARSE_FAILED`.
3. For raw: virtual_size = `get_input_capacity(0) * sector_size`.
4. For qcow2: `qcow2::QcowHeader::parse(header)?.virtual_size`.
5. For vmdk: `vmdk::Vmdk4Header::parse(header)?.virtual_size`.
6. For vhd: parse the footer at `(capacity - 1) * sector_size`
   (vhd stores the footer at the *end*). Requires a second
   sector read.
7. For vhdx: read sector at offset 0x10000 (header 1) and parse
   via `vhdx::VhdxHeader::parse`. Two-step: header 1 gives the
   region table location → region table → metadata region →
   virtual size item. This is heavier than the others; for
   simplicity, phase 2 can defer vhdx-as-backing to phase 5 by
   returning `ERROR_BACKING_PARSE_FAILED` and noting the
   limitation in docs.

The backing path bytes are passed through into the corresponding
`plan_*` call's `BackingRef`. Phase 2 does *not* open the
backing file in the traditional sense — the host (phase 5)
attaches the backing file as input device 0; the guest only
parses what it reads.

## Per-format dispatch

```rust
let opts_backing = build_backing_ref(config)?;  // None or Some
let target = ImageFormat::from_u32(config.target_format);

let (plan, resolved_unit_size) = match target {
    ImageFormat::Raw => return raw_path(config),  // no metadata
    ImageFormat::Qcow2 => {
        let opts = qcow2_opts_from(config, opts_backing, virtual_size)?;
        (plan_qcow2(&opts, scratch)?, opts.cluster_size)
    }
    ImageFormat::Vmdk4 => {
        let opts = vmdk_opts_from(config, opts_backing, virtual_size)?;
        (plan_vmdk(&opts, scratch)?, opts.grain_size)
    }
    ImageFormat::Vhd => {
        let opts = vhd_opts_from(config, opts_backing, virtual_size)?;
        let unit = match opts.subformat {
            VhdSubformat::Fixed => 0,
            VhdSubformat::Dynamic => opts.block_size,
        };
        (plan_vhd(&opts, scratch)?, unit)
    }
    ImageFormat::Vhdx => {
        let opts = vhdx_opts_from(config, opts_backing, virtual_size)?;
        (plan_vhdx(&opts, scratch)?, opts.block_size)
    }
    _ => return invalid_format(config),
};

let mut bytes_written: u64 = 0;
for w in plan.writes() {
    write_metadata_chunk(call_table, w, sector_size)?;
    bytes_written += w.bytes.len() as u64;
}

send_create_result(call_table, target as u32, virtual_size,
    bytes_written, plan.minimum_file_size, resolved_unit_size,
    CreateResult::ERROR_OK);
```

### Raw path

For `target = Raw`, no metadata is emitted from the guest — the
host short-circuits raw to a simple `ftruncate` in phase 3.
Defensive behaviour in phase 2: if the guest *is* launched for
raw (e.g. host bug), send a success result with
`metadata_bytes_written = 0` and `file_size_after =
virtual_size`. No writes go to the output device. This way the
guest never panics on a misconfigured raw call.

### Write helper

`write_metadata_chunk` slices `MetadataWrite.bytes` into
`sector_size`-sized chunks aligned to `byte_offset / sector_size`
sectors and calls `write_output_sector` for each. When `bytes`
doesn't end on a sector boundary, the final sector is padded
with zeros to fill the sector — which is the correct behaviour
for our formats since the trailing portion is reserved zero
anyway.

Edge cases:
- `byte_offset` must be sector-aligned. All `plan_*` writes
  produced in phase 1 *are* sector-aligned (every region starts
  on a cluster / 4 KiB / sector boundary). A debug-assert
  catches violations.
- A write longer than `2 GiB` would overflow `i32` but
  metadata writes are far smaller; not a real concern.

## Open questions

These should be answered during execution; escalate to the
management session rather than guessing.

1. **`GUEST_CREATE_SCRATCH_LIMIT` precise value.** 8 MiB is the
   recommendation. Smaller (4 MiB) covers vhd/vhdx/vmdk fully
   and qcow2 default cluster sizes; only narrows the
   `cluster_size=512` ceiling. Larger (12 MiB) eats nearly all
   scratch. Recommend 8 MiB and tune in phase 7 if integration
   tests reveal a needed combination that doesn't fit.

2. **VHDX-as-backing deferral.** Reading the virtual-size item
   from a VHDX metadata region requires walking three regions
   (header → region table → metadata). The other backing
   formats are one-shot header parses. Recommend: phase 2
   returns `ERROR_BACKING_PARSE_FAILED` for vhdx backings; phase
   5 implements the full walk if a user actually wants it. The
   host can short-circuit by demanding `-F` explicitly for vhdx
   backings (uncommon).

3. **`magic` value for `CreateResult`.** Suggested `CRES`
   (`0x43524553`). Confirm no collision with existing Result
   magics: `MeasureResult::MAGIC = 0x4D524553` (MRES);
   `InfoResult / CheckResult / CompareResult` use distinct
   four-character codes. CRES is free.

4. **Should `CreateConfig.virtual_size` default to "from
   backing" when `backing_file_len > 0` and the user *also*
   passes `-o size=...`?** qemu-img: the explicit size wins.
   instar: same — only treat `virtual_size == 0` as "infer
   from backing." Document this in the `CreateConfig` field
   doc.

5. **VMDK subformat byte vs flag.** Master plan used a flag
   bit; measure uses a byte. Recommend the byte (matches
   measure, leaves flag space free).

6. **Backing-file path length validation.** `CreateConfig`
   reserves 1024 bytes; `crates/create::MAX_BACKING_FILE_LEN`
   is also 1024. The guest must reject `backing_file_len > 1024`
   defensively before slicing. Single check at the top of the
   backing path.

7. **Write progress reporting.** Should the guest emit
   `ProgressMessage` events while writing metadata? The
   metadata writes for a typical image are < 1 MiB total —
   instantaneous. Recommend: no progress events for the metadata
   write loop; the host's preallocation pass (phase 6) gets its
   own progress reporting since that's the slow part.

8. **Defensive validation on the protobuf side.** The host
   parses `CreateResultMessage` and presents to the user. The
   message has no magic; trust comes from the framing. Match
   `MeasureResultMessage`'s shape (no extra magic) and let the
   `error` field gate the host's output.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Add `CreateConfig` and `CreateResult` to `src/shared/src/lib.rs`. Place them after the existing `MeasureConfig` / `MeasureResult` blocks for proximity. Use the exact field list from the "Public types" section above (`magic`, `target_format`, `flags`, `sector_size`, `virtual_size`, `qcow2_cluster_size`, `qcow2_refcount_bits`, `vmdk_subformat`, `vhd_subformat`, `_pad`, `vmdk_grain_size`, `block_size`, `backing_file_len`, `backing_file`, `backing_format`, `_reserved`). Magic values: `CreateConfig::MAGIC = 0x43524541` ("CREA"); `CreateResult::MAGIC = 0x43524553` ("CRES"). Add the FLAG_ and ERROR_ consts. Add `pub const CREATE_CONFIG_MAX_BACKING_FILE: usize = 1024;`. Add unit tests in the existing `#[cfg(test)] mod tests` block at the bottom of `shared/lib.rs` covering: (1) `CreateConfig::MAGIC` and `CreateResult::MAGIC` differ from every existing config/result magic (mirror the assert_ne block measure has); (2) `CreateConfig::is_valid()` returns true for default + true magic and false for wrong magic; (3) flag bit-pattern tests. Confirm `cargo test -p shared` passes and `make lint` clean. |
| 2b | medium | sonnet | none | Add `CreateResultMessage` to `crates/guest-protocol/proto/guest.proto` (field block after `MeasureResultMessage`) with the field list from the "Public types" section. Add `CreateResult create_result = 11;` to the `GuestMessage.payload` oneof. Add `pub fn create_result_message(...) -> guest_::GuestMessage` to `crates/guest-protocol/src/lib.rs` mirroring `measure_result_message` (signature: `target_format: &str, resolved_virtual_size: u64, metadata_bytes_written: u64, file_size_after: u64, resolved_unit_size: u32, error: u32`). Run `make instar` to confirm the proto regenerates cleanly and the bindings compile. Then run `cargo test -p guest-protocol` to confirm no regressions. |
| 2c | high   | opus   | none | Wire `send_create_result` into the call table and the core guest. Three coordinated edits: (1) Add `pub send_create_result: unsafe extern "C" fn(*const CreateResult)` to `CallTable` in `src/shared/src/lib.rs` (after `send_measure_result`). (2) Add `send_create_result` implementation to `src/core/src/serial.rs` (model on `send_measure_result` — map `target_format` via `ImageFormat::from_u32(...).name()`, call `guest_protocol::create_result_message`, send framed). (3) Add `ct_send_create_result` thunk and `send_create_result: ct_send_create_result` entry to the CallTable init in `src/core/src/main.rs`. The `validate_call_table!` macro just checks magic; no change needed there. Run `make instar` to confirm everything builds (including the existing guest binaries — adding a CallTable field is ABI-compatible only if appended at the end; place the new fn pointer last). Verify with `make test-rust` that nothing regressed. |
| 2d | medium | sonnet | none | Scaffold `src/operations/create/` with the standard guest-binary layout (model: `src/operations/measure/`). Files: `Cargo.toml` (name `create-op`, bin name `create`, depends on `shared`, `create` (the lib), `qcow2`, `vmdk`, `vhd`, `vhdx`, `raw`; release profile `panic = "abort"`, `opt-level = "z"`, `lto = true`), `linker.ld` (verbatim copy of `src/operations/measure/linker.ld`), and `src/main.rs` (empty stub with `#![no_std] #![no_main]`, panic handler, and a `_start` that immediately calls `send_complete(b"create\0", 0, false)` so the binary builds). Add `"operations/create"` to `src/Cargo.toml` workspace members. Add `create-op` to the `--exclude` lists in `scripts/check-rust.sh` (lines 58 and 68) and `Makefile`'s `test-rust` target (line ~500). Run `make instar` and confirm `create.bin` appears in `target/release/`. Run `make test-rust` and `make lint` to confirm no regressions. |
| 2e | high   | opus   | none | Implement the full guest `_start` in `src/operations/create/src/main.rs`. Read it bottom-up from `measure/src/main.rs` as the structural template. Required behaviour: (1) read CallTable + CreateConfig, validate magic + sector_size + target_format range; (2) build a `BackingRef` from `CreateConfig.backing_file[..backing_file_len]` if `backing_file_len > 0`; (3) if `virtual_size == 0` and backing is present, read backing's first sector via `read_input_sector(0, 0, ...)`, dispatch on format-detect, parse the appropriate header to recover `virtual_size` (for vhdx, return `ERROR_BACKING_PARSE_FAILED` for now — phase 5 follow-up); (4) build the matching `*CreateOpts` from `CreateConfig` (mirror measure's `*_opts_from` helpers); (5) call the appropriate `plan_*` with a `&mut [u8]` carved from the `GUEST_CREATE_SCRATCH` static region (declared as `pub const GUEST_CREATE_SCRATCH_LIMIT: usize = 8 * 1024 * 1024;` in `shared/lib.rs`; carve via raw pointer like measure does for HEADER_BUF/CACHE_BUF_A/CACHE_BUF_B); (6) iterate `plan.writes()`, writing each via `write_output_sector` in sector-sized chunks; (7) send a `CreateResult` with the resolved values; (8) call `send_complete`. Error paths: map every `CreateError` variant to a `CreateResult::ERROR_*` code (table provided in the master plan). For target=Raw: short-circuit per the design (write nothing, return success with bytes_written=0, file_size_after=virtual_size). Run `make instar` and `make test-rust` and `make lint` and verify `create.bin` fits the 384 KiB cap reported by `check_size`. |
| 2f | low    | sonnet | none | Update `src/build.sh` to build, copy, and size-check `create.bin` alongside the other operation binaries. Add CREATE_BIN/CREATE_ELF stanzas modelled on MEASURE_BIN/MEASURE_ELF (~10 lines of build + ~3 lines for copy + 1 line for `check_size`). Update the trailing "Binaries" section to mention create. Verify by running `make instar` clean and checking `target/release/create.bin` exists with reasonable size (expect 20-40 KiB; measure.bin is ~25 KiB). |
| 2g | low    | sonnet | none | Documentation pass: update `AGENTS.md`'s operation list to mention create alongside measure, add a short bullet to `ARCHITECTURE.md`'s operation table noting that the create binary exists but is unwired in phase 2 (host CLI wiring is phase 3), and add a `## Unreleased` line in `CHANGELOG.md` flagging the new binary as internal-only. **Do not** touch `docs/usage.md` or `docs/create.md` — those land in phase 11 once the host CLI is in place. |

## Out of scope for phase 2

Reminders so a sub-agent doesn't drift:

- No host CLI changes (no `Commands::Create`, no `run_create`,
  no clap surface). That is phase 3.
- No `-o` option parsing — the host translates options into the
  `CreateConfig` struct; the guest just reads the struct.
- No preallocation handling. Phase 6 layers that on top.
- No real backing-file handling beyond reading one or two
  sectors of input device 0 and parsing the header. Phase 5
  attaches the backing file as an input device on the host
  side; phase 2 just consumes what's already attached.
- No integration tests under `tests/`. Phase 8 owns the python
  integration suite.
- No fuzz harnesses. Phase 9 adds them.
- No documentation under `docs/create.md` or `docs/usage.md`.
  Phase 11 owns user-facing docs.
- Do not modify the existing measure / info / check / compare /
  convert / copy operation binaries. The CallTable gains one
  *appended* field; their code does not need to change to work
  with the new layout (they index by field name; the C ABI
  layout for them is unchanged because their fields appear at
  the same offsets).

## Success criteria

- `make instar` builds cleanly and produces `target/release/
  create.bin`.
- `check_size "create.bin"` reports under 384 KiB (expect
  20-40 KiB based on the measure precedent).
- `make lint` clean across the workspace.
- `make test-rust` passes. Adds: shared tests for the new
  Config/Result types (step 2a); guest-protocol coverage for the
  new message (step 2b). Existing crate test counts unchanged.
- `pre-commit run --all-files` clean.
- `src/operations/convert/src/main.rs` still has zero
  modifications (verify with `git log --stat phase-2-base..HEAD
  -- src/operations/convert/`).
- `src/operations/measure/src/main.rs` and the other operation
  binaries are also unmodified (same verification command).
- The new guest binary is registered in the workspace and
  excluded from `cargo test --workspace` runs to mirror the
  pattern of the other operation binaries.

## Bugs fixed during this work

(To be filled in.)

## Back brief

Before executing each step of this phase, please back brief the
operator as to your understanding of the step and how the work
you intend to do aligns with the brief. In particular, flag if
the brief refers to file/line locations that don't match what
you find when you read them (the survey was a snapshot; the
codebase may have moved).
