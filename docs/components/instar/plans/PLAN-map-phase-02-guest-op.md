# Phase 2: guest `map` operation + protobuf + call-table boundary

Master plan: [PLAN-map.md](/components/instar/plans/PLAN-map/) ·
Previous phase: [PLAN-map-phase-01-extent-iterators.md](/components/instar/plans/PLAN-map-phase-01-extent-iterators/)

## Status: Complete

New `src/operations/map/` guest binary builds at ~28 KiB / 384 KiB
(7%). `MapConfig` ("MAP_" magic) / `MapResult` ("MPRS" magic) /
`MapExtentRecord` ("MXET" magic) added to `src/shared/src/lib.rs`
alongside the matching `MapExtentMessage` / `MapResultMessage`
protobufs in `crates/guest-protocol/proto/guest.proto`. CallTable
ABI gained `send_map_extent` and `send_map_result` function
pointers and the VERSION bumped 15 → 16. Single-image v1 refuses
chain sources with `MAP_RESULT_ERROR_HAS_BACKING`.

## Mission

Ship every cross-boundary piece needed to actually run the
map operation inside the KVM guest, *except* the host CLI
surface (that lands in phase 3). After phase 2 the system
can:

- accept a `MapConfig` written to `OPERATION_CONFIG_ADDR`,
- launch a fresh `map.bin` guest binary at `0x20000`,
- have the guest read the config, detect the source format,
  refuse sources with backing-file / multi-extent / parent
  references (single-image v1 scope), walk the source via
  the phase 1 `map_extents` and stream per-extent records
  over the serial command channel, ending with a summary,
- have the host decode the `MapExtentMessage` /
  `MapResultMessage` protobuf payloads into structs that
  phase 3 can buffer / render.

Phase 2 deliberately does *not* add the `Commands::Map`
clap variant, the `run_map()` host orchestration, the
`MapArgs` struct, or the human / JSON renderers. Those land
in phases 3 and 4. The end-to-end functional test of map
therefore lives in phase 6. Phase 2's verification is
compile-and-link plus binary-size cap plus the fuzz mock
CallTable build.

## Why this is its own phase

This is the single phase where the call-table ABI changes
(two new function pointers: `send_map_extent`,
`send_map_result`; `CallTable::VERSION: u32 = 15` → `16`),
where two new protobuf payloads land in the `GuestMessage`
oneof (fields 15 and 16, reserved in PLAN-map.md), and where
a new guest operation binary appears in the build outputs.
Each of those is a small change in isolation but each ripples
through multiple files (`shared`, `core`, `vmm`,
`guest-protocol`, fuzz mock, `build.sh`, binary-size check,
operation `Cargo.toml` / `linker.ld` / `main.rs`). Bundling
them lets one phase's review check the whole boundary at
once.

The streaming-emit pattern is new to phase 2 — every other
operation emits exactly one result message after a single
result struct write. Map emits N extents followed by one
summary. The cleanest fit is two call-table functions:
`send_map_extent(*const MapExtentRecord)` invoked once per
coalesced extent, and `send_map_result(*const MapResult)`
once at end-of-walk. The guest's `MapExtentCoalescer` emit
closure simply calls the former; the binary calls the latter
once `_start` is about to return.

## Architecture

### Boundary changes (ABI)

#### `CallTable` extension

Append two new function pointers at the end of `CallTable`
in `src/shared/src/lib.rs`:

```rust
/// Send one coalesced map extent. Called once per extent
/// emitted by the guest's per-format `map_extents` walker.
/// Args: pointer to a `MapExtentRecord` carrying the extent's
/// virtual start, length, state code, and (for `Data`
/// extents) the source file offset.
pub send_map_extent: unsafe extern "C" fn(*const MapExtentRecord),

/// Send the map operation's terminator summary. Called once
/// per invocation, after the last `send_map_extent`. Args:
/// pointer to a `MapResult` carrying the extent count,
/// virtual size, source format name index, and error code.
pub send_map_result: unsafe extern "C" fn(*const MapResult),
```

Bump `CallTable::VERSION` from `15` to `16`. This is breaking
— every existing operation binary built against version 15
is rejected by `validate_call_table!` until rebuilt.
Acceptable because `build.sh` rebuilds every operation in one
pass.

The fuzz harness's mock CallTable in `src/fuzz/src/lib.rs`
gains two no-op stubs and adds them to `build_call_table()`
in the same order.

#### New shared types

Add to `src/shared/src/lib.rs` next to `MeasureConfig` /
`MeasureResult`:

```rust
/// Configuration for the map operation.
///
/// Written to OPERATION_CONFIG_ADDR by the VMM before
/// launching the map guest binary.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct MapConfig {
    /// Magic (`0x4D41505F` = "MAP_").
    pub magic: u32,
    /// Configuration flags. Bit 31 is FLAG_VERBOSE; all
    /// other bits are reserved for future use (chain mode,
    /// SEEK_HOLE host pre-pass, etc.).
    pub flags: u32,
    /// Sector size for input I/O (typically 65536).
    pub sector_size: u32,
    /// Number of input devices in the backing chain.
    /// Reserved for the chain follow-up; phase 2 enforces
    /// `input_device_count == 1`.
    pub input_device_count: u32,
    /// Start the emission window at this virtual byte offset.
    /// Zero means "start at the beginning of the image".
    pub start_offset: u64,
    /// Stop the emission window after this many virtual
    /// bytes from `start_offset`. Zero means "emit to
    /// virtual_size". A non-zero value smaller than one
    /// source cluster / grain / block still emits the
    /// extent that overlaps the window; trimming happens at
    /// extent boundaries, matching qemu-img map.
    pub max_length: u64,
    /// Reserved padding for forward compat. Future fields:
    /// snapshot ID length + bytes, image-opts descriptor.
    pub _reserved: [u8; 32],
}

impl MapConfig {
    pub const MAGIC: u32 = 0x4D41505F; // "MAP_"
    pub const FLAG_VERBOSE: u32 = 1 << 31;
    pub fn is_valid(&self) -> bool { self.magic == Self::MAGIC }
}

/// One coalesced map extent in the on-wire FFI representation.
///
/// The parser-facing `MapExtent` type (Rust enum) is converted
/// at the guest's emit boundary into this `#[repr(C)]` form
/// so the call-table function pointer can take a plain
/// pointer.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct MapExtentRecord {
    /// Magic (`0x4D584554` = "MXET").
    pub magic: u32,
    /// State code: 0 = Hole, 1 = ZeroAllocated, 2 = Data.
    /// Matches the host-side enum-tag convention used for
    /// JSON / human output in phase 4.
    pub state: u32,
    /// Virtual offset of the extent's first byte.
    pub start: u64,
    /// Extent length in bytes. Never zero.
    pub length: u64,
    /// Source file offset for `state == Data`; zero
    /// otherwise. (Phase 4's renderer omits the JSON field
    /// when `state != Data`.)
    pub file_offset: u64,
    /// Reserved padding for forward compat (compressed
    /// length, subcluster flags, chain depth).
    pub _reserved: [u8; 16],
}

impl MapExtentRecord {
    pub const MAGIC: u32 = 0x4D584554; // "MXET"
    pub const STATE_HOLE: u32 = 0;
    pub const STATE_ZERO_ALLOCATED: u32 = 1;
    pub const STATE_DATA: u32 = 2;
    pub fn is_valid(&self) -> bool { self.magic == Self::MAGIC }
}

/// Result structure for the map operation. One per invocation,
/// sent after every `send_map_extent`.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct MapResult {
    /// Magic (`0x4D505253` = "MPRS").
    pub magic: u32,
    /// Source format echoed back. ImageFormat-as-u32; the
    /// host translates to a name for the protobuf envelope.
    pub source_format: u32,
    /// Number of `send_map_extent` calls the guest made
    /// during this invocation. The host can sanity-check
    /// that it received exactly that many extent messages
    /// before the result.
    pub extents_emitted: u64,
    /// Virtual size of the source image, in bytes. The
    /// host uses this to verify the partition invariant
    /// (sum of received extent lengths == virtual_size
    /// minus any window trim).
    pub virtual_size: u64,
    /// Error code: 0 = ok, non-zero mirrors `MapResult::ERROR_*`
    /// below.
    pub error: u32,
    /// Reserved padding for forward compat.
    pub _reserved: u32,
}

impl MapResult {
    pub const MAGIC: u32 = 0x4D505253; // "MPRS"
    pub const ERROR_OK: u32 = 0;
    /// Source format unrecognised or scan rejected the image.
    pub const ERROR_INVALID_SOURCE: u32 = 1;
    /// Invalid config (missing magic, bad sector_size,
    /// input_device_count != 1, oversized start_offset).
    pub const ERROR_INVALID_OPTION: u32 = 2;
    /// Source has a backing file / parent / multi-extent
    /// descriptor and chain composition is deferred.
    pub const ERROR_HAS_BACKING: u32 = 3;
    /// I/O failure during walk.
    pub const ERROR_IO: u32 = 4;
    pub fn is_valid(&self) -> bool { self.magic == Self::MAGIC }
}
```

Magic uniqueness (`grep MAGIC: u32` confirms `MAP_`, `MXET`,
`MPRS` do not collide with the 21 existing magic values).

#### Protobuf messages

Add to `crates/guest-protocol/proto/guest.proto`, as new
fields in the `GuestMessage` oneof:

```protobuf
// Single coalesced map extent (from map operation). Sent
// once per emitted extent; followed by a MapResultMessage
// terminator. The host accumulates these into the rendered
// human / JSON output in phase 4.
message MapExtentMessage {
  // Virtual offset of the extent's first byte.
  uint64 start = 1;
  // Extent length in bytes.
  uint64 length = 2;
  // State: "hole" | "zero" | "data". Matches
  // MapExtentRecord::STATE_* in shared/src/lib.rs.
  string state = 3;
  // Source file offset; only meaningful when state == "data".
  // Host renderer omits the JSON field when state != "data".
  uint64 file_offset = 4;
}

// Map operation summary (sent after the last MapExtentMessage).
message MapResultMessage {
  // Source format echoed back (e.g. "raw", "qcow2", "vmdk",
  // "vhd", "vhdx").
  string source_format = 1;
  // Number of MapExtentMessage records sent during this
  // invocation.
  uint64 extents_emitted = 2;
  // Virtual size of the source image, in bytes.
  uint64 virtual_size = 3;
  // Error code: 0 = ok, non-zero mirrors MapResult::ERROR_*
  // in shared/src/lib.rs (1=invalid_source, 2=invalid_option,
  // 3=has_backing, 4=io).
  uint32 error = 4;
}

// Then in GuestMessage:
message GuestMessage {
  Level level = 1;
  oneof payload {
    ...
    CommitResultMessage commit_result = 14;
    MapExtentMessage map_extent = 15;
    MapResultMessage map_result = 16;
  }
}
```

Add matching builder helpers in
`crates/guest-protocol/src/lib.rs`:

```rust
pub fn map_extent_message(
    start: u64,
    length: u64,
    state: &str,
    file_offset: u64,
) -> guest_::GuestMessage { ... }

pub fn map_result_message(
    source_format: &str,
    extents_emitted: u64,
    virtual_size: u64,
    error: u32,
) -> guest_::GuestMessage { ... }
```

### Core wiring

In `src/core/src/serial.rs`:

```rust
pub fn send_map_extent(record: &shared::MapExtentRecord) {
    let state = match record.state {
        shared::MapExtentRecord::STATE_HOLE => "hole",
        shared::MapExtentRecord::STATE_ZERO_ALLOCATED => "zero",
        shared::MapExtentRecord::STATE_DATA => "data",
        _ => "unknown",
    };
    let msg = guest_protocol::map_extent_message(
        record.start, record.length, state, record.file_offset,
    );
    send_message(&msg);
}

pub fn send_map_result(result: &shared::MapResult) {
    let source = shared::ImageFormat::from_u32(result.source_format).name();
    let msg = guest_protocol::map_result_message(
        source, result.extents_emitted, result.virtual_size, result.error,
    );
    send_message(&msg);
}
```

In `src/core/src/main.rs`:
- Import the new symbols at the top.
- Add `ct_send_map_extent` and `ct_send_map_result` wrappers
  (the `unsafe extern "C" fn(*const MapExtentRecord)` etc.
  shape) mirroring `ct_send_commit_result`.
- Append both to the `CallTable { ... }` literal in field
  order matching the struct.

In `src/vmm/src/main.rs`'s `format_message`:

```rust
Some(guest_::GuestMessage_::Payload::MapExtent(e)) => {
    format!("map_extent start={} length={} state={} file_offset={}",
            e.start, e.length, e.state, e.file_offset)
}
Some(guest_::GuestMessage_::Payload::MapResult(r)) => {
    format!("map_result source_format={} extents_emitted={} \
             virtual_size={} error={}",
            r.source_format, r.extents_emitted, r.virtual_size, r.error)
}
```

No new `run_map` function, no new `Commands::Map` variant —
those are phase 3.

### Guest binary algorithm

`src/operations/map/src/main.rs`:

1. Read the call table at `CALL_TABLE_ADDR`, validate via
   `validate_call_table!(call_table, "map")`.
2. Read `MapConfig` from `OPERATION_CONFIG_ADDR`. Validate
   magic + `sector_size >= 512 && <= MAX_SECTOR_SIZE && is_power_of_two`
   + `input_device_count == 1` (the chain follow-up will
   relax this). On invalid → send `MapResult { error:
   ERROR_INVALID_OPTION }` + `send_complete`.
3. Detect the source format via
   `detect_format_from_header(header_buf_first_sector,
   sector_size, false)`.
4. Refuse sources with backing / parent / multi-extent
   references — see "Backing-file refusal" below. On refusal
   → send `MapResult { error: ERROR_HAS_BACKING }`.
5. Initialise the matching `*State` (qcow2 / vmdk / vhd /
   vhdx / raw) with `init`, recover `virtual_size` the same
   way `operations/measure` does.
6. Build an `emit_closure` that:
   - Clips each `MapExtent` against the
     `[start_offset, start_offset + max_length)` window (zero
     `max_length` means "end of image"). Trimming happens
     at extent boundaries — the closure may discard, clip,
     or pass through.
   - Converts the parser-facing `MapExtent` enum into a
     `MapExtentRecord` (`state` code + `file_offset`).
   - Calls `(call_table.send_map_extent)(&record)`.
   - Increments an `extents_emitted: u64` counter.
   - Returns `true` while the window is not exhausted,
     `false` once `start + length >= window_end` so the
     walker stops emitting (the
     `MapExtentCoalescer::push -> bool` contract from
     phase 1).
7. Dispatch on detected source format, calling the matching
   `<format>::map_extents(...)` with the closure.
8. After the walker returns, send `MapResult { source_format,
   extents_emitted, virtual_size, error }` and signal
   `send_complete("map", bytes_read, success)`.

The closure design keeps the window filter out of the parser
crates (which already pushed enough complexity into phase 1).
Per-extent buffering is zero — the guest does not hold
extent state beyond the closure's small counter and the
coalescer's `pending: Option<MapExtent>`.

### Backing-file refusal

The v1 scope is single-image only. The guest refuses any
source with chain composition:

- **qcow2**: parse the header (`QcowHeader::parse(header_buf)`)
  and check `backing_file_offset != 0 &&
  backing_file_size != 0`. Either field zero means no backing
  file. (`info`'s no-chain path already checks this; mirror
  it.)
- **vhd**: after `VhdState::init`, check
  `state.disk_type == DISK_TYPE_DIFFERENCING`
  (`src/crates/vhd/src/lib.rs:93`). Fixed and Dynamic are
  accepted. (`DISK_TYPE_DIFFERENCING == 4`.)
- **vhdx**: after `VhdxState::init`, check
  `state.has_parent` (`src/crates/vhdx/src/lib.rs:349`,
  decoded from FileParameters HasParent bit 1).
- **vmdk**: the single-extent monolithicSparse /
  monolithicFlat / streamOptimized paths are
  `scan_allocation` / `map_extents`-compatible. Multi-extent
  descriptor-driven layouts are rejected by `VmdkState::init`
  for our purposes already (the descriptor walker stops at
  the first extent). To be safe, also reject any header that
  carries a parent reference. Phase 6 fuzz / integration
  tests confirm.
- **raw**: no backing concept; never rejected.

The detection happens after `init` for vhd / vhdx (they
need the parser state to expose the bit) and before `init`
for qcow2 (the header alone is enough). The guest emits a
single `MapResult { error: ERROR_HAS_BACKING }` and no
extents. The host renderer in phase 4 will produce an error
message pointing at the chain follow-up.

### Sector-size + scratch memory layout

Matches `operations/measure`:

```rust
const HEADER_BUF: usize = SCRATCH_MEM_BASE;
const CACHE_BUF_A: usize = HEADER_BUF + MAX_SECTOR_SIZE;
const CACHE_BUF_B: usize = CACHE_BUF_A + MAX_SECTOR_SIZE;
```

One header buffer plus two cache buffers shared across
parsers (only one parser runs per map invocation).

### Build infrastructure

- `src/Cargo.toml`: add `operations/map` to the workspace
  `members` list (after `operations/commit`).
- `src/build.sh`: add a `=== Building map operation ===`
  section following the measure / create / resize / rebase /
  commit pattern (cargo build → rust-objcopy →
  `map.bin`). Also add `map.bin` to the `target/release/`
  copy section and update the summary `echo`.
- `Makefile`: update `clean-instar` target if it enumerates
  operation binaries (search for `measure.bin\|commit.bin`).
  Update the `test-rust` `--exclude` list to include `map-op`
  (the operation crates are excluded from `cargo test` because
  they are `no_main`).
- `scripts/check-binary-sizes.sh` (or equivalent): add `map`
  alongside the existing operation list.

### `linker.ld` and `Cargo.toml`

Identical to `operations/measure/linker.ld` (loads at
`0x20000`, 384 KiB cap). The `Cargo.toml` brings in `shared`,
`qcow2`, `raw`, `vmdk`, `vhd`, `vhdx` — no `measure` /
`create` / `resize` / `rebase` / `commit` deps (map does no
writing and no target-format math).

```toml
[package]
name = "map-op"
version = "0.2.0"
edition = "2021"
description = "Map operation: emit per-extent allocation map"
license = "Apache-2.0"
publish = false

[[bin]]
name = "map"
path = "src/main.rs"

[dependencies]
shared = { path = "../../shared" }
qcow2 = { path = "../../crates/qcow2" }
raw = { path = "../../crates/raw" }
vmdk = { path = "../../crates/vmdk" }
vhd = { path = "../../crates/vhd" }
vhdx = { path = "../../crates/vhdx" }

[profile.release]
panic = "abort"
opt-level = "z"
lto = true
```

The binary should land well under the 384 KiB cap — map does
strictly less work than measure (no target-format math, no
LUKS, no preallocation). Target: < 150 KiB.

## Open questions

1. **Window filter location**: closure-side (in
   `operations/map/src/main.rs`) or walker-side (pass
   `start_offset` / `max_length` into the per-format
   `map_extents` and let it skip / clamp before pushing into
   the coalescer)?
   - Closure-side keeps phase 1's `map_extents` signature
     untouched. The walker still does all the work; only
     emitted extents are filtered. For a fragmented image
     spanning the whole virtual size with a 1 MiB window,
     the walker still reads all of the L2 / GD / BAT
     metadata, which is wasteful.
   - Walker-side adds two `u64` parameters to every per-format
     `map_extents` and a "skip until window starts" fast-path
     inside each walker. Saves I/O when the window is small
     relative to the image.
   - **Recommendation**: closure-side for v1. The window
     filter is an optimisation, not a correctness
     requirement, and the walker-side change would touch
     every parser walker we just landed. Add walker-side
     pruning as a follow-up if profiling shows it matters.

2. **`MapExtent` enum → `MapExtentRecord` flat struct
   conversion**: where does the conversion live? Inline in
   the emit closure, or as a `From<MapExtent> for
   MapExtentRecord` impl in `shared`?
   - The closure does enough state work (window clipping,
     counter increment, abort signalling) that an inline
     conversion fits. But a `From` impl is more discoverable
     and reusable by the host's protobuf decoder.
   - **Recommendation**: inline conversion via a `fn
     encode_extent(e: MapExtent) -> MapExtentRecord` free
     function in the guest binary, plus a `state_code` const
     accessor for the (enum → `u32`) mapping. No `From` impl
     in `shared` — the host doesn't decode in this direction
     (it consumes the protobuf string field, not the FFI
     struct).

3. **Streaming vs. host-side accumulation**: the protobuf
   wire format is line-delimited; the host receives one
   `GuestMessage` per `send_message` call. Should the host
   render extents as they arrive (low memory, line-buffered
   output, but no JSON close-bracket until the end), or
   buffer them all then render (simpler but unbounded
   memory for fragmented images)?
   - The phase 4 plan (drafted in PLAN-map.md) commits to
     streaming the JSON array open `[`, then each extent as
     a comma-separated object, then close `]` on the
     `MapResultMessage`. Phase 2 needs no opinion — the
     guest emits per-extent regardless.

4. **`MapExtentRecord` magic on every record**: each FFI
   record carries a 4-byte magic. For 17 M fragmented extents
   that's 64 MiB of pure magic across the call boundary.
   Wasteful but mirrors the existing pattern; the magic acts
   as a defence-in-depth check against config-region
   corruption. Acceptable cost — the records live on the
   guest stack and are passed by pointer to a function that
   immediately copies them into the protobuf encoder.

5. **`send_complete` after `send_map_result`**: every
   operation calls `send_complete(op_name, bytes_read,
   success)` after its result. Map should too. `success` is
   `MapResult.error == ERROR_OK`.

6. **`virtual_size` for VMDK streamOptimized**: the existing
   `VmdkState::init` recovers it from the footer/descriptor.
   Reuse without modification.

7. **Sector-size validation**: the existing measure binary
   validates `sector_size >= 512 && <= MAX_SECTOR_SIZE &&
   is_power_of_two`. Map should do exactly the same.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | none | Add `MapConfig`, `MapExtentRecord`, and `MapResult` to `src/shared/src/lib.rs` right after `MeasureResult` (around line 2389). Use the exact schemas in the Architecture section. Magic constants `0x4D41505F` ("MAP_"), `0x4D584554` ("MXET"), `0x4D505253` ("MPRS"). `impl` blocks expose `MAGIC`, `is_valid`, `FLAG_VERBOSE`, `STATE_*`, and `ERROR_*` constants. Add ≥6 unit tests inside the existing `#[cfg(test)] mod tests` verifying: all three magic values are unique vs. the 21 existing magics; each `is_valid()` accepts the correct magic and rejects 0; the STATE_* and ERROR_* constants have the documented values. Run `make lint`, `make test-rust`, `pre-commit run --all-files`. Only `src/shared/src/lib.rs` changes. |
| 2b | medium | sonnet | none | Add `MapExtentMessage` and `MapResultMessage` to `crates/guest-protocol/proto/guest.proto` as fields 15 and 16 in the `GuestMessage` oneof (after `commit_result = 14`). Fields per the Architecture section. Then add `pub fn map_extent_message(...)` and `pub fn map_result_message(...)` helpers in `crates/guest-protocol/src/lib.rs` mirroring `measure_result_message` (around line 580). Run `make lint`, `make test-rust`. The proto regen runs as part of cargo build via `build.rs`; verify the generated module exposes `MapExtentMessage`, `MapResultMessage`, and the oneof variants `Payload::MapExtent`, `Payload::MapResult`. Touch only `crates/guest-protocol/proto/guest.proto` and `crates/guest-protocol/src/lib.rs`. |
| 2c | high | opus | none | Append `send_map_extent: unsafe extern "C" fn(*const MapExtentRecord)` and `send_map_result: unsafe extern "C" fn(*const MapResult)` to `CallTable` in `src/shared/src/lib.rs` immediately after `write_input_sector` (line 914 area). Bump `CallTable::VERSION` from 15 to 16. Add the matching `mock_send_map_extent` and `mock_send_map_result` no-op stubs to `src/fuzz/src/lib.rs` and include them in `build_call_table()` in the same order. Run `make lint`, `make test-rust`, `pre-commit run --all-files`. The version bump is the breaking change; everything must rebuild cleanly. **High effort because:** the CallTable layout is consumed via raw memory cast from the guest. Any field-ordering mistake between the struct definition (shared) and the literal initialiser (core, fuzz, vmm if any) silently miscompiles. The sub-agent must read the existing `CallTable { ... }` literal in `src/core/src/main.rs` and confirm field-order parity end-to-end. |
| 2d | medium | sonnet | none | Wire the two new function pointers through core. Add `pub fn send_map_extent(record: &shared::MapExtentRecord)` and `pub fn send_map_result(result: &shared::MapResult)` to `src/core/src/serial.rs` (build the protobuf via the new builders in `guest_protocol`, call `send_message(&msg)`). Add `ct_send_map_extent(*const MapExtentRecord)` and `ct_send_map_result(*const MapResult)` wrappers to `src/core/src/main.rs` mirroring `ct_send_commit_result`. Add both to the `CallTable { ... }` literal at the top of `main.rs` (around line 261) in the field order matching the struct in `shared`. Run `make lint`, `make test-rust`. Touches `src/core/src/main.rs` and `src/core/src/serial.rs`. |
| 2e | medium | sonnet | none | Extend `format_message` in `src/vmm/src/main.rs` (around lines 758–870) to handle `Some(guest_::GuestMessage_::Payload::MapExtent(e))` and `Some(guest_::GuestMessage_::Payload::MapResult(r))` per the Architecture section. No CLI / render — phase 3 owns those. The main event loop already routes anything in `format_message` through `debug!`. Run `make instar`, `make lint`. Touches only `src/vmm/src/main.rs`. |
| 2f | high | opus | none | Create the new guest binary `src/operations/map/` (`Cargo.toml`, `linker.ld`, `src/main.rs`) following the layout of `src/operations/measure/`. `Cargo.toml` and `linker.ld` per the Architecture section (no `measure`/`create` deps). `src/main.rs` implements the algorithm in "Guest entry-point algorithm": validate MapConfig, detect format, refuse backing/parent/multi-extent sources per "Backing-file refusal", init parser state, build an emit closure that performs window clipping + Rust→FFI conversion + abort signalling, dispatch to `<format>::map_extents` from phase 1, and send `MapResult` + `send_complete`. Cache buffers at `SCRATCH_MEM_BASE` matching `operations/measure`. **No** backing-chain support, **no** LUKS, **no** snapshot extraction. Add `operations/map` to the workspace `members` list in `src/Cargo.toml` (after `operations/commit`). Add `map-op` to the `cargo test --exclude` list in `Makefile`'s `test-rust` target. Run `make instar`, `make lint`, `make test-rust`, `make check-binary-sizes`. **High effort because:** this binary ties every preceding step together — config validation, format detection, backing refusal across four formats (each with a different signal), parser init + map_extents dispatch, FFI conversion, window filter, summary emission. Subtle bugs here surface as silent wrong output in phase 6. |
| 2g | low | sonnet | none | Update `src/build.sh` with a `=== Building map operation ===` section following the measure/create/resize/rebase/commit pattern (cargo build → rust-objcopy → map.bin). Add `cp "$MAP_BIN" target/release/` to the copy section and update the summary `echo` line. Update `scripts/check-binary-sizes.sh` (or the in-build.sh size check) to add `map` to the operation list. Run `make instar`, `make check-binary-sizes`. Confirm `map.bin` lands < 384 KiB; report the actual size so the reviewer can compare against the other operation binaries. |
| 2h | low | sonnet | none | Update `ARCHITECTURE.md` to mention the new `operations/map/` binary (one paragraph mirroring the other operation entries) and the new `MapConfig` / `MapExtentRecord` / `MapResult` structs in shared. Update `CHANGELOG.md` Unreleased / Added with one line citing the new operation binary, the two new protobuf messages, and the CallTable version bump (15 → 16). Mention that the CLI surface ships in phase 3. Run `pre-commit run --all-files`. |

Total: 8 commits.

### Why 2c and 2f are high-effort opus

- **2c (CallTable extension)**: the CallTable is consumed
  via raw memory casts. The struct definition
  (shared/src/lib.rs), the literal initialiser (core/main.rs),
  and the mock initialiser (fuzz/lib.rs) must all match
  field-for-field in the same order. Adding two new fields
  triples the surface for a subtle mismatch. The version
  bump is the breaking change; every operation rebuilds
  against the new layout. Verifying field-order parity
  end-to-end is the kind of cross-file check opus handles
  cleanly.
- **2f (guest binary)**: the most code-heavy step in the
  phase. The binary is small but it threads together every
  preceding piece — config validation, format detection,
  per-format backing refusal (four different signals), parser
  state init, `map_extents` dispatch, FFI conversion, window
  filter (with the subtle "trim at extent boundaries"
  semantic), summary emission. Worth opus for the same reason
  PLAN-measure phase 3f's measure binary was opus.

### Out of scope for phase 2

- `Commands::Map` clap variant and `MapArgs` struct
  (phase 3).
- `run_map()` host orchestration function (phase 3).
- Human / JSON output rendering (phase 4).
- `--start-offset` / `--max-length` CLI parsing (phase 3 —
  values arrive in `MapConfig` already-validated).
- `--snapshot` / `-l SNAPSHOT` (master-plan future work).
- Backing-chain composition (master-plan follow-up; phase
  2 *refuses* chain sources, no traversal).
- Walker-side window pruning (Open question 1; deferred).
- Native LUKS source decryption (master-plan future work).
- Cross-version baseline generation (phase 5).
- Integration tests against real testdata images (phase 6).
- Fuzz target updates (phase 7 — `map_*_message` helpers
  are reachable but no fuzz target consumes them yet).
- Multi-extent VMDK propagation (master-plan future work;
  phase 2 refuses multi-extent vmdk sources).
- VHDX partial-present per-sector bitmap walk (master-plan
  future work; phase 1's classifier treats it as Data).

## Success criteria

- `src/shared/src/lib.rs` defines `MapConfig`,
  `MapExtentRecord`, and `MapResult` with magic constants,
  `is_valid`, FLAGs, STATE_* codes, and ERROR_* codes.
- `crates/guest-protocol/proto/guest.proto` carries
  `MapExtentMessage` as field 15 and `MapResultMessage` as
  field 16 in the `GuestMessage` oneof.
- `CallTable::VERSION == 16`. `send_map_extent` and
  `send_map_result` function pointers are the last two
  fields. Mock CallTable in `src/fuzz/` has matching stubs.
- `src/operations/map/map.bin` builds and lands well under
  384 KiB (target: < 150 KiB).
- `make instar` builds the full toolchain.
- `make lint` clean.
- `make test-rust` passes; new tests in `shared` add ≥6 (the
  parser-side map_extents tests from phase 1 are unchanged).
- `make check-binary-sizes` passes with `map.bin` listed.
- `pre-commit run --all-files` clean.
- `ARCHITECTURE.md` and `CHANGELOG.md` updated.
- No `Commands::Map` clap variant yet (phase 3).
- The fuzz mock CallTable builds (no compile errors after
  the two new fields land).

## Risks and mitigations

- **CallTable version bump breaks the build until every
  operation is rebuilt**. Mitigation: `build.sh` rebuilds
  every operation in one pass; if a stale binary is loaded,
  `validate_call_table!` returns 0 with a clear log message.
  Failure-stop, not undefined behaviour.

- **`map.bin` exceeds 384 KiB**. Mitigation: step 2f's brief
  audits the feature-gate / dependency list. The map binary
  is materially smaller than measure (no target math, no
  preallocation, no LUKS). `cargo bloat -p map-op --release`
  identifies any surprise bloat.

- **Magic value collisions**. Mitigation: 2a's brief
  cross-checks against `grep "MAGIC: u32" src/shared/src/lib.rs`
  before commit. The chosen values ("MAP_", "MXET", "MPRS")
  are visibly disjoint.

- **Silent ABI drift between guest and host CallTable**. The
  CallTable is consumed via raw memory cast, so a field-order
  mismatch between `shared::CallTable`, core's literal, and
  the fuzz mock is invisible to the compiler. Mitigation:
  2c's brief explicitly directs the sub-agent to diff-check
  all three sites and to run the fuzz mock build after the
  change.

- **Proto regen failure on micropb**. Mitigation: existing
  `build.rs` handles regen; if a sub-agent runs into an
  issue it is almost certainly a syntax problem in the proto
  file. Step 2b's brief includes a `make test-rust` checkpoint
  to surface regen errors immediately.

- **Backing-file refusal misses a chain source**. The four
  per-format detection signals (qcow2 header, vhd
  disk_type, vhdx has_parent, vmdk multi-extent) each carry
  their own risk of a missed case. Mitigation: step 2f's
  brief enumerates each one explicitly. Phase 6's integration
  tests run instar map against real chain images (qcow2
  with backing, vhd differencing, vhdx with parent locator)
  and assert ERROR_HAS_BACKING.

- **Window-filter edge case at end-of-image**: a window that
  extends past `virtual_size` should clip silently, not error.
  Mitigation: step 2f's brief calls this out; the closure
  bounds the window by `min(start_offset + max_length,
  virtual_size)`.

- **Stack overflow in the guest from a `MapExtentRecord`
  array**: the record is ~56 bytes; one record on the stack
  is fine. The closure does not buffer records — it sends
  each one immediately. No issue.

- **17M-extent worst-case runtime**: a maximally fragmented
  1 TiB qcow2 with 64 KiB clusters emits ~17M extents
  alternating data/hole. At ~50 bytes/extent on the wire,
  that's ~850 MiB of serial traffic and ~30 min wall time.
  Phase 2 accepts this as a known cost of the format. The
  differential fuzzer (phase 7) bounds corpus virtual size
  to keep test runtime sane.

## Back brief

Before executing any step, the executing agent should
back-brief: which file is being edited, which existing
operation is the closest template, and which parts of the
new code involve raw memory casts (`MapConfig` read,
`MapExtentRecord` / `MapResult` writes, `CallTable`
extension). The reviewer should verify no step bleeds into
phase 3 (clap), phase 4 (output rendering), phase 5
(baselines), or phase 6 (integration tests).
