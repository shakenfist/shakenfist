# PLAN-snapshot phase 01: shared ABI

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the `*Config` /
`*Result` family in `src/shared/src/lib.rs`, the `CallTable`
struct and its append-only convention, the protobuf `GuestMessage`
oneof in `crates/guest-protocol/proto/guest.proto`, the guest
call-table stubs in `src/core/src/main.rs`, the guest serial
helpers in `src/core/src/serial.rs`, the guest virtio driver in
`src/core/src/virtio.rs`, the host virtio block emulator in
`src/vmm/src/virtio/block.rs`, and the fuzz mock call table in
`src/fuzz/src/lib.rs`), and ground your answers in what the code
actually does today. Do not speculate about the codebase when you
could read it instead.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is the first of
fourteen phases.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

This phase establishes the shared ABI surface that every later
phase of `PLAN-snapshot` depends on. No planners, guest binary,
or host CLI entry point are added yet — phases 2–14 build on top
of what phase 1 lands. The phase is deliberately small and
mechanical so it can ship in a single PR and unblock phase 2
(parser extension) and phase 5 (refcount mutators) to be
developed in parallel.

The pattern is well-established. The most recent comparable
work — phase 1 of `PLAN-map.md` and phase 1 of
`PLAN-rebase-commit.md` — followed the exact same shape:

- `*Config` and `*Result` structs live in
  `src/shared/src/lib.rs`, are `#[repr(C)]`, carry a 4-byte
  ASCII magic, define error codes as `pub const` values, and
  expose `is_valid()`.
- `CallTable` function pointers for sending per-op results are
  appended at the very end of the struct (see the comment block
  at `src/shared/src/lib.rs:872-931` for the convention and the
  existing `read_output_sector` / `send_resize_result` /
  `send_rebase_result` / `send_commit_result` /
  `write_input_sector` / `send_map_extent` / `send_map_result`
  additions). `CallTable::VERSION` (at
  `src/shared/src/lib.rs:1254`) bumps each time the layout
  changes.
- `GuestMessage` in `crates/guest-protocol/proto/guest.proto`
  is the wire format. New per-op result messages are appended
  both as new message types and as new arms in the
  `oneof payload` block. Tags 13–16 are already taken by
  rebase / commit / map; phase 1 takes 17 and 18.

### Where the call table actually runs

A point worth flagging because it cost time during the rebase /
commit phase 1: the `CallTable` struct in `src/shared/src/lib.rs`
is *populated by the guest core* (`src/core/src/main.rs` at
lines 264–298), not by the VMM. Its function pointers point at
`ct_*` shims in `src/core/src/main.rs` that forward into the
`src/core/src/serial.rs` framing layer. The host VMM only sees
the resulting protobuf messages on the serial channel and
decodes them through `crates/guest-protocol/src/lib.rs`.

Phase 1 therefore touches **two** call-table installation sites
(the real one in `src/core/src/main.rs` and the test one in
`src/fuzz/src/lib.rs`), but **no** code in `src/vmm/src/main.rs`
to install the new pointers themselves. The VMM does gain a
small change to its serial-message debug formatter
(`src/vmm/src/main.rs` around lines 880–910) so the new payload
variants are pretty-printed in verbose logs, but that is
cosmetic.

### Host-side fsync support: already there

The master plan's open question 10 proposed adding an
`fsync_input` call-table primitive to give the snapshot guest
explicit durability points. Phase 1 research confirms that the
host-side VMM virtio block emulator (`src/vmm/src/virtio/block.rs`
at line 428–431) already handles `VIRTIO_BLK_T_FLUSH` by calling
`self.backing.sync()` (which maps to `File::sync_all()` in
`src/vmm/src/backing.rs:155`). The host side is therefore done.

What is missing is the *guest* path. `src/core/src/virtio.rs`
only issues `VIRTIO_BLK_T_IN` (read) and `VIRTIO_BLK_T_OUT`
(write); there is no `flush()` method on the guest's
`BlockDevice`. Phase 1 adds that method and exposes it through
the new `fsync_input` call-table pointer so the snapshot guest
(and, as a follow-up, the commit guest) can request durable
checkpoints between the metadata writes that have to be ordered.

### Why this is a single commit

Steps 1a–1l mutate eight files but cannot be split: bumping
`CallTable::VERSION` without populating the new pointers in
`src/core/src/main.rs` and `src/fuzz/src/lib.rs` breaks the
build, and the magic-value version mismatch check (`verify_call_table!`
at `src/shared/src/lib.rs:3439-3450`) would refuse to run any
operation. The whole bump-and-populate has to land together.

## Mission and problem statement

After phase 1 lands:

1. `src/shared/src/lib.rs` defines three new `#[repr(C)]` types
   in the existing "per-op config and result" section:
   - `SnapshotConfig` — input config, magic `"SNAP"`
     (`0x534E4150`), `MODE_*` discriminator, UTF-8 argument
     bytes, flags, `_reserved` tail.
   - `SnapshotEntryRecord` — one-per-snapshot record streamed
     by list mode, magic `"SNER"` (`0x534E4552`), carries the
     full v3 snapshot metadata (vm_state_size_large, disk_size,
     icount).
   - `SnapshotResult` — terminator, magic `"SNRS"`
     (`0x534E5253`), carries mode echo, error code,
     `snapshots_emitted` (list), and `assigned_id_*` (create).
   Each type has `MAGIC`, `is_valid()`, and an append-only
   error / mode / flag constant set.

2. `CallTable` in `src/shared/src/lib.rs` gains three new
   function-pointer fields, appended at the end of the struct in
   this order:
   - `send_snapshot_entry: unsafe extern "C" fn(*const SnapshotEntryRecord)`
   - `send_snapshot_result: unsafe extern "C" fn(*const SnapshotResult)`
   - `fsync_input: unsafe extern "C" fn(u32) -> bool`
   `CallTable::VERSION` bumps from 16 to 17.

3. `crates/guest-protocol/proto/guest.proto` gains two new
   message types (`SnapshotEntryMessage`, `SnapshotResultMessage`)
   and two new arms in the `oneof payload` block
   (`snapshot_entry = 17`, `snapshot_result = 18`).

4. `crates/guest-protocol/src/lib.rs` gains two new public
   constructor helpers `snapshot_entry_message(...)` and
   `snapshot_result_message(...)`, matching the
   `map_extent_message` / `map_result_message` shape.

5. `src/core/src/serial.rs` gains two new public functions
   `send_snapshot_entry(record: &shared::SnapshotEntryRecord)`
   and `send_snapshot_result(result: &shared::SnapshotResult)`
   that wrap the protobuf constructors and frame onto the
   serial channel.

6. `src/core/src/main.rs` gains three new `ct_*` shims
   (`ct_send_snapshot_entry`, `ct_send_snapshot_result`,
   `ct_fsync_input`) wired into the `CallTable` literal at
   lines 264–298.

7. `src/core/src/virtio.rs` gains a `flush()` method on
   `BlockDevice` that issues a `VIRTIO_BLK_T_FLUSH` request
   (request type `4`, per virtio spec; the host VMM block
   emulator already handles it). The flush path uses the same
   3-descriptor chain the read/write paths use, with an empty
   data buffer; the host ignores the data descriptor for FLUSH
   requests.

8. `src/fuzz/src/lib.rs` gains three matching mock function
   pointers (`mock_send_snapshot_entry`,
   `mock_send_snapshot_result`, `mock_fsync_input`) so the
   updated `CallTable` literal compiles.

9. The VMM serial-message debug formatter
   (`src/vmm/src/main.rs` `format_guest_message_payload` style
   block at lines 880–910) gains arms for the two new payload
   variants so verbose-mode log lines pretty-print them.

10. Unit tests in `src/shared/src/lib.rs` cover magic / size /
    `is_valid()` / error-code distinctness for the three new
    structs. Mirrors the existing `map_config_*` /
    `map_extent_record_*` / `map_result_*` block at lines
    4184–4310.

11. `docs/plans/PLAN-snapshot.md` is updated to reflect the
    chosen magic values (open question still open in the master
    plan), the `CallTable::VERSION` bump from 16 to 17, and the
    note that host-side fsync support already exists.

12. The build is green: `cargo build --workspace`,
    `make instar`, `make lint`, `make test-rust`,
    `make check-binary-sizes`, `pre-commit run --all-files`.
    Binary sizes are unchanged because no guest operation
    binary calls the new pointers yet.

Nothing in phase 1 changes user-visible behaviour. `instar
snapshot` still prints "unrecognized subcommand" because the
`Commands` enum in `src/vmm/src/main.rs` is not extended in
this phase — that happens in phase 4 (list host) and phase 9
(mutate host).

## Open questions

### 1. Magic values for the new structs

Working choices (4-byte ASCII):

- `SnapshotConfig::MAGIC = 0x534E4150` (`"SNAP"`)
- `SnapshotEntryRecord::MAGIC = 0x534E4552` (`"SNER"`)
- `SnapshotResult::MAGIC = 0x534E5253` (`"SNRS"`)

None collide with the existing inventory. Current magics in
`src/shared/src/lib.rs`:

- `CallTable::MAGIC` = `0x494D4147` (`"IMAG"`)
- `MapConfig::MAGIC` = `0x4D41505F` (`"MAP_"`)
- `MapExtentRecord::MAGIC` = `0x4D584554` (`"MXET"`)
- `MapResult::MAGIC` = `0x4D505253` (`"MPRS"`)
- `RebaseConfig::MAGIC` = `0x52454241` (`"REBA"`)
- `RebaseResult::MAGIC` = `0x52425253` (`"RBRS"`)
- `CommitConfig::MAGIC` = `0x434F4D4D` (`"COMM"`)
- `CommitResult::MAGIC` = `0x434F5253` (`"CORS"`)
- `ChainConfig::MAGIC` (around line 4214) and the other
  `*Config` magics from create / resize follow the same shape.

Confirm or pick different values before step 1a runs.

### 2. `MODE_*`, `FLAG_*`, `ERROR_*` constant sets

`SnapshotConfig::MODE_*`:

- `MODE_LIST = 0`
- `MODE_APPLY = 1`
- `MODE_CREATE = 2`
- `MODE_DELETE = 3`

`SnapshotConfig::FLAG_*`:

- `FLAG_QUIET = 1 << 0`
- `FLAG_FORCE_SHARE = 1 << 1`  (host-side, accepted no-op
  matching `-U` on `qemu-img snapshot`; the guest ignores it)
- `FLAG_VERBOSE = 1 << 31`  (matches the `MapConfig` convention)

`SnapshotResult::ERROR_*` (initial set; append-only):

- `ERROR_OK = 0`
- `ERROR_UNSUPPORTED_FORMAT = 1` — source is not qcow2
- `ERROR_UNSUPPORTED_FEATURE = 2` — qcow2 image has compressed
  clusters, encryption, external data file, or bitmaps; only
  the mutating modes refuse, list mode still works
- `ERROR_NOT_FOUND = 3` — `-a`/`-d` argument matches neither an
  ID nor a name
- `ERROR_DUPLICATE_NAME = 4` — `-c` with a name that already
  exists in the snapshot table
- `ERROR_REFCOUNT_OVERFLOW = 5` — a cluster's refcount would
  exceed `1 << refcount_bits` (caught by the phase 5 dry-run
  pass)
- `ERROR_ALLOCATION_FAILED = 6` — refcount table is full and
  cannot grow within v1's bounds
- `ERROR_SNAPSHOT_TABLE_FULL = 7` — would exceed
  `QCOW_MAX_SNAPSHOTS` (qcow2-spec cap 65536; phase 2 picks the
  in-memory cap)
- `ERROR_IO = 8` — sector read or write failed at the call-
  table boundary
- `ERROR_L1_SIZE_MISMATCH = 9` — `-a` target's L1 is larger
  than active L1's allocation and growing would exceed the
  qcow2 spec cap
- `ERROR_INVALID_UTF8 = 10` — name field in `SnapshotConfig.arg`
  is not valid UTF-8
- `ERROR_INVALID_CONFIG = 11` — magic / version mismatch in
  `SnapshotConfig`
- `ERROR_PARSE_FAILED = 12` — qcow2 header / snapshot-table
  byte-level parse failed

Confirm the lists; either can be trimmed if a code proves
unnecessary, or extended later by appending new ones.

### 3. Field layout of `SnapshotConfig`

Working draft (total 320 bytes):

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct SnapshotConfig {
    pub magic: u32,                 // 0x534E4150 "SNAP"
    pub mode: u32,                  // MODE_LIST | _APPLY | _CREATE | _DELETE
    pub flags: u32,                 // FLAG_QUIET | FLAG_FORCE_SHARE | FLAG_VERBOSE
    pub sector_size: u32,           // host sector size (typically 512 or 65536)

    pub arg_len: u32,               // bytes used in `arg` (0..=255)
    pub _pad: u32,                  // align `arg` to 8

    pub arg: [u8; 256],             // snapshot ID / name (UTF-8, no nul)

    pub _reserved: [u8; 32],        // future: --image-opts descriptor,
                                    //         chain-depth tag, etc.
}
```

The `arg` field is intentionally fixed-size so the guest can
parse without a heap. qemu-img caps snapshot names at 255 bytes
plus a nul terminator (`block/qcow2-snapshot.c::QCOW_MAX_SNAPSHOTS`
neighbours `QCOW_MAX_SNAPSHOT_NAME`); 256 bytes is enough. Names
are emitted nul-terminated where required at the qcow2 layer;
inside `SnapshotConfig` they are length-prefixed via `arg_len`
to avoid the "is the nul part of the length?" foot-gun. Length 0
is valid for `MODE_LIST`.

### 4. Field layout of `SnapshotEntryRecord`

Working draft (total 384 bytes — sized to fit one full v3
snapshot's metadata plus a generous name field):

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct SnapshotEntryRecord {
    pub magic: u32,                 // 0x534E4552 "SNER"
    pub date_sec_lo: u32,           // qcow2 stores date as two u32s
    pub date_sec_hi: u32,           // (big-endian on disk); guest passes
                                    // them split so we don't depend on
                                    // u64-load alignment on the FFI side
    pub date_nsec: u32,

    pub vm_clock_nsec: u64,
    pub vm_state_size_large: u64,   // v3 extra-data offset 0
    pub disk_size: u64,             // v3 extra-data offset 8
    pub icount: u64,                // v3 extra-data offset 16; u64::MAX
                                    // sentinel for "absent" (matches
                                    // qemu's `qcow2_snapshot.icount = -1`)

    pub l1_table_offset: u64,
    pub l1_size: u32,
    pub extra_data_size: u32,       // length of the extra-data section
                                    // for forward-compat reporting

    pub id_len: u32,                // bytes used in `id`
    pub name_len: u32,              // bytes used in `name`

    pub id: [u8; 32],               // snapshot ID (qemu uses small
                                    // decimal strings, 32 is generous)
    pub name: [u8; 256],            // snapshot tag/name (UTF-8, no nul)

    pub _reserved: [u8; 32],        // future
}
```

Date is split into two u32 halves so the guest's parser (which
reads the disk format big-endian field-by-field) can populate
it without a 64-bit aligned write. The host renderer reassembles
`(hi << 32) | lo` into a `time_t`. The qcow2 on-disk snapshot
header actually stores `date_sec` as two separate big-endian u32s
("date_sec_hi" and "date_sec_lo" in qemu source), so this matches
the disk shape one-to-one and means there is no host-side
endianness re-conversion.

### 5. Field layout of `SnapshotResult`

Working draft (total 192 bytes):

```rust
#[repr(C)]
#[derive(Clone, Copy)]
pub struct SnapshotResult {
    pub magic: u32,                 // 0x534E5253 "SNRS"
    pub mode: u32,                  // echo of SnapshotConfig.mode
    pub error: u32,
    pub _pad: u32,

    pub snapshots_emitted: u32,     // populated for MODE_LIST
    pub assigned_id_len: u32,       // populated for MODE_CREATE
    pub assigned_id: [u8; 64],      // populated for MODE_CREATE

    pub _reserved: [u8; 96],        // future
}
```

`assigned_id` is the auto-assigned numeric ID qemu hands back
on successful `-c` (e.g. `"1"`, `"2"`). It is a string because
qemu's IDs are strings, not numbers, and matching that exactly
is part of the parity goal.

### 6. CallTable function-pointer signatures

```rust
// Send one snapshot record during MODE_LIST. Called once per
// snapshot, before the terminating `send_snapshot_result`.
// Args: `*const SnapshotEntryRecord` carrying the full v3
// snapshot metadata (id, name, vm_state_size_large, disk_size,
// icount, date, vm_clock, l1 location).
pub send_snapshot_entry: unsafe extern "C" fn(*const SnapshotEntryRecord),

// Send the snapshot operation's terminator summary. Called
// once per invocation, after the last `send_snapshot_entry`
// (or as the only call for MODE_APPLY / _CREATE / _DELETE).
// Args: `*const SnapshotResult` carrying the mode echo, error
// code, emitted count (for list), and assigned id (for
// create).
pub send_snapshot_result: unsafe extern "C" fn(*const SnapshotResult),

// Request that the host fdatasync the named input device's
// backing file. Args: `device_index` (must refer to a slot
// opened RW via `open_chain_devices_rw`; the host stub
// returns `false` for read-only or invalid slots). Returns
// `true` on success.
//
// Mutating snapshot modes use this between the data-write
// pass and the header-pointer-flip to enforce qemu's
// "old table still valid until header updated" durability
// contract. Commit (added in PLAN-rebase-commit phase 8) is
// expected to migrate from process-exit fsync to an explicit
// `fsync_input` call as a follow-up.
pub fsync_input: unsafe extern "C" fn(u32) -> bool,
```

### 7. Protobuf message field layouts

```proto
message SnapshotEntryMessage {
  // Snapshot ID (qemu's decimal string: "0", "1", ...).
  string id = 1;
  // Snapshot tag/name (UTF-8).
  string name = 2;
  // Location of the snapshot's L1 table on disk.
  uint64 l1_table_offset = 3;
  // Snapshot L1 size in entries.
  uint32 l1_size = 4;
  // Creation date split as qcow2 stores it on disk.
  uint32 date_sec_hi = 5;
  uint32 date_sec_lo = 6;
  uint32 date_nsec = 7;
  // VM clock at snapshot creation (nanoseconds).
  uint64 vm_clock_nsec = 8;
  // 64-bit VM state size (v3 extra-data offset 0).
  uint64 vm_state_size = 9;
  // Virtual disk size at snapshot creation (v3 extra-data
  // offset 8).
  uint64 disk_size = 10;
  // qemu record/replay icount; u64::MAX means absent.
  uint64 icount = 11;
  // Length of the source extra-data section; reported for
  // forward-compat diagnostics.
  uint32 extra_data_size = 12;
}

message SnapshotResultMessage {
  // Echo of the requested mode (0=list, 1=apply, 2=create,
  // 3=delete). Mirrors SnapshotConfig::MODE_* in shared.
  uint32 mode = 1;
  // Error code: 0 = ok, non-zero mirrors
  // SnapshotResult::ERROR_* in src/shared/src/lib.rs.
  uint32 error = 2;
  // Number of SnapshotEntryMessage records emitted (list
  // mode only; 0 otherwise).
  uint32 snapshots_emitted = 3;
  // Auto-assigned snapshot ID returned by MODE_CREATE; empty
  // string for the other modes.
  string assigned_id = 4;
}
```

Oneof arm tags:

- `SnapshotEntryMessage snapshot_entry = 17;`
- `SnapshotResultMessage snapshot_result = 18;`

These follow the existing `map_extent = 15` / `map_result = 16`
tag span.

### 8. Should `fsync_input` enforce per-slot RW like `write_input_sector`?

Working answer: **yes, mirroring `write_input_sector`**. The
host stub looks up the requested slot in the device set; if
the slot was not opened RW, returns `false` without calling
`backing.sync()`. This keeps the security property that the
guest cannot escalate write access (or even durability) by
addressing the wrong slot. The guest treats `false` the same
way it treats `false` from `write_input_sector`: aborts the
operation with `ERROR_IO`.

For the snapshot guest this is a no-op invariant because the
single image is always opened RW for the mutating modes.

### 9. Should the guest virtio `flush()` reuse the 3-descriptor chain or use a 2-descriptor chain?

The virtio spec is permissive on FLUSH layout — the data
descriptor may be omitted. The current guest `do_request` at
`src/core/src/virtio.rs:225` always issues a 3-descriptor chain
(header + data + status). The host VMM at
`src/vmm/src/virtio/block.rs:393-433` reads the data descriptor
unconditionally but ignores its contents for FLUSH. So either
shape works; the simplest change is to keep the 3-descriptor
chain and pass any 0-length data buffer.

Working answer: **reuse the 3-descriptor chain** with a
dedicated `do_flush()` helper that mirrors `do_request` but
hard-codes `req_type = VIRTIO_BLK_T_FLUSH = 4` and uses the
existing data-area address with `sector_size` length (the host
will ignore the contents). This is a 30-line change in
`src/core/src/virtio.rs` and avoids touching the descriptor-
layout invariants.

### 10. Should phase 1 also add the guest binary scaffolding for `src/operations/snapshot/`?

Working answer: **no**. The master plan splits scaffolding from
ABI deliberately, and phase 3 (list-mode guest binary) owns the
scaffolding. Phase 1 is ABI-only.

### 11. Should phase 1 update `instar-testdata` baselines for the new payloads?

Working answer: **no**. Baselines are generated by phase 10
once mutating modes exist. Phase 1 has no user-visible output.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | worktree | Add the three new structs to `src/shared/src/lib.rs` immediately after the existing `MapResult` block (current line 2554). Copy `MapConfig` (lines 2417–2458), `MapExtentRecord` (lines 2465–2504), and `MapResult` (lines 2510–2553) as templates. Define `SnapshotConfig`, `SnapshotEntryRecord`, `SnapshotResult` with the exact field layouts in open questions 3, 4, 5. Add the magic constants from open question 1, the `MODE_*` / `FLAG_*` / `ERROR_*` constants from open question 2, and an `is_valid(&self) -> bool` method on each. Do **not** add address constants — they reuse `OPERATION_CONFIG_ADDR = 0x00081000` (the existing per-op config slot). Run `cargo build -p shared` after writing; iterate until clean. |
| 1b | medium | sonnet | worktree | Append unit tests for the three new structs to the existing `#[cfg(test)] mod tests` block in `src/shared/src/lib.rs`. Mirror the existing `map_config_*` / `map_extent_record_*` / `map_result_*` tests at lines 4184–4310: assert `MAGIC` value, assert `is_valid()` returns true with magic set and false with magic zeroed, assert `size_of::<T>()` matches the size in the doc comment, assert no magic collides with any other in the file, and assert error / mode / flag constants are distinct. Run `cargo test -p shared` until clean. |
| 1c | high | opus | worktree | Extend `CallTable` in `src/shared/src/lib.rs`. The struct ends at line 932. Append three new function-pointer fields at the very end, after `send_map_result`. Field order, names, and signatures exactly per open question 6. Add doc-comment blocks immediately above each field using the existing `send_map_result` doc block at lines 925–930 as the model. Bump `CallTable::VERSION` from `16` to `17` at `src/shared/src/lib.rs:1254` and update the version-history comment at lines 1247–1253 with a one-line entry referencing snapshot phase 1. Run `cargo build -p shared` until clean. The build will *also* fail in `src/core/src/main.rs` and `src/fuzz/src/lib.rs` after this step because their `CallTable {...}` literals are now incomplete — that is expected; subsequent steps fix them. Use opus for context-window reasons: the change crosses shared / core / fuzz simultaneously, and opus' 1M context comfortably holds all three files while sonnet has to fragment. |
| 1d | medium | sonnet | worktree | Extend `crates/guest-protocol/proto/guest.proto`. Add two new message types `SnapshotEntryMessage` and `SnapshotResultMessage` with the field layouts in open question 7. Place them after the existing `MapResultMessage` at line 324. Append two new arms to the `GuestMessage` oneof at lines 329–345: `SnapshotEntryMessage snapshot_entry = 17;` and `SnapshotResultMessage snapshot_result = 18;`. Then add two public constructor helpers to `crates/guest-protocol/src/lib.rs` next to `map_extent_message` (line 722) and `map_result_message` (line 753): `pub fn snapshot_entry_message(...)` taking the same fields as the protobuf message, and `pub fn snapshot_result_message(...)` taking `(mode: u32, error: u32, snapshots_emitted: u32, assigned_id: &str)`. Use `push_str` for the string fields exactly as `map_extent_message` does. Run `cargo build -p guest-protocol` until clean. |
| 1e | medium | sonnet | worktree | Add two new public functions to `src/core/src/serial.rs` next to `send_map_extent` (line 603) and `send_map_result` (line 619): `pub fn send_snapshot_entry(record: &shared::SnapshotEntryRecord)` and `pub fn send_snapshot_result(result: &shared::SnapshotResult)`. The first reads the record's id/name as UTF-8 slices using `record.id_len`/`record.name_len`, the second does the same for `assigned_id`/`assigned_id_len`. Wrap the protobuf constructors from step 1d and call `send_message(&msg)`. Match the docstring style of `send_map_extent` / `send_map_result`. Re-export the two new functions in whichever `pub use` block already re-exports `send_map_extent` / `send_map_result` (currently lines 31–34 of `src/core/src/main.rs` via `use serial::{...};`). Run `cargo build -p core --target x86_64-unknown-none` (or whichever target the operation binaries use; check `src/core/Cargo.toml` or `Makefile`) until clean. |
| 1f | high | opus | worktree | Three changes in the guest core, all in one commit. **(i)** In `src/core/src/virtio.rs`, add a `pub fn flush(&mut self) -> bool` method on `BlockDevice` next to `write_sector` (line 216). It should call a new private helper `do_flush(&mut self)` that issues a `VIRTIO_BLK_T_FLUSH = 4` request on the same 3-descriptor chain `do_request` uses, with the existing `data_addr` and `sector_size`-byte length (the host VMM ignores the data buffer for FLUSH per `src/vmm/src/virtio/block.rs:428-431`). Add `const VIRTIO_BLK_T_FLUSH: u32 = 4;` next to the existing `VIRTIO_BLK_T_IN` / `VIRTIO_BLK_T_OUT` constants. Return the boolean status the same way `do_request` does. **(ii)** In `src/core/src/main.rs`, add three new `ct_*` shims next to `ct_send_map_result` (line 736): `ct_send_snapshot_entry`, `ct_send_snapshot_result`, `ct_fsync_input`. The first two mirror `ct_send_map_extent` / `ct_send_map_result` exactly (null-pointer guard, dereference, forward to the `serial::send_snapshot_*` helper from step 1e). `ct_fsync_input(device_index: u32) -> bool` looks up the slot in `INPUT_DEVICES` like `ct_write_input_sector` does (lines 709–724) and calls `dev.flush()`. **(iii)** Wire the three new pointers into the `CallTable {...}` literal at lines 264–298, in the same order they appear in the struct (after `send_map_result`). Update the `use serial::{...}` block at line 33 to import the two new functions. Run `make instar` until the guest binaries build clean. Use opus: this step holds the virtio descriptor invariants, the call-table layout, and the serial-framing layer in context simultaneously. |
| 1g | medium | sonnet | worktree | Update `src/fuzz/src/lib.rs`'s mock `CallTable` literal at lines 58–92. Add three new mock function pointers `mock_send_snapshot_entry`, `mock_send_snapshot_result`, `mock_fsync_input` in the same style as `mock_send_map_extent` / `mock_send_map_result` / `mock_write_input_sector`. The mocks should be no-ops (record nothing) — the fuzz harness doesn't care about snapshot output, the phase 12 fuzz target adds its own recording variant. `mock_fsync_input(_idx: u32) -> bool` returns `true` unconditionally. Wire all three into the `CallTable {...}` literal in the same order they appear in the struct. Run `cargo build -p fuzz` and `cargo test -p fuzz` until clean. |
| 1h | low | sonnet | worktree | Extend the VMM serial-message debug formatter in `src/vmm/src/main.rs`. Search for the existing `Some(guest_::GuestMessage_::Payload::MapResult(r)) => { ... }` arm (around line 899). Add two new arms after it for `Payload::SnapshotEntry(e)` and `Payload::SnapshotResult(r)`, printing the key fields one-line in the same `format!("name field={} ...", ...)` style as the surrounding arms. This is cosmetic and only affects `--verbose` log output. Run `cargo build --workspace` until clean. |
| 1i | low | sonnet | worktree | Update `docs/plans/PLAN-snapshot.md`. (1) In the "Call-table and protobuf changes" section (around lines 318–392), replace the placeholder magic with the concrete values from step 1a (`0x534E4150`, `0x534E4552`, `0x534E5253`) and add a note that `CallTable::VERSION` bumped from 16 to 17. (2) In open question 10, mark the `fsync_input` decision as resolved and link the resolution to "phase 1 step 1f added the guest path; host-side FLUSH support already existed in `src/vmm/src/virtio/block.rs:428`". (3) Add a one-line note to the Execution row for phase 1 pointing at this phase plan. (4) In the Phase notes "Phase 1 (ABI)" bullet, append "Step 1f also adds the guest-side virtio flush path." Keep all edits under 30 lines total. |
| 1j | low | sonnet | worktree | Run `make instar`, `make test-rust`, `make check-binary-sizes`, and `pre-commit run --all-files` from the worktree root. Resolve any rustfmt / clippy findings. Verify `make check-binary-sizes` is unchanged from the pre-phase baseline — the guest operation binaries should not need to recompile against the new pointers because no operation calls them yet (but `core.bin` *does* recompile because it references `CallTable` directly; check that its size delta is small and under-budget). Stage and present a single commit covering all of steps 1a–1i with the commit-message convention from `~/.claude/CLAUDE.md` (50-char first line ending in `.`, 75-char body wrap, Prompt paragraph, Signed-off-by, Co-Authored-By line that includes model + context window + effort + any other active settings). |

## Agent guidance

### Execution model

All implementation work for this phase is done by sub-agents,
never in the management session. The management session (this
conversation) is reserved for review and decision-making. After
each step the management session:

1. Reads the actual files that were supposed to change.
2. Confirms no unrelated files were modified.
3. Runs the lint / test commands that the step's brief names.
4. Either commits, asks for a retry with a sharper brief, or
   upgrades the model.

All steps in this phase use `isolation: "worktree"`. The ABI
bump is the kind of change that breaks the build mid-step (after
step 1c, before steps 1e–1g land) and you do not want a half-
applied bump in your main checkout if the run aborts.

### Model and effort notes

- Steps 1a, 1b, 1d, 1e, 1g, 1h, 1i, 1j are mechanical extensions
  of well-established patterns. Sonnet at medium / low effort is
  enough, provided the briefs name exact line ranges and
  templates to copy. They do.
- Steps 1c and 1f cross the host/guest boundary and touch
  call-table or virtio-descriptor semantics. Use opus. Opus'
  context window also matters because step 1f has to hold the
  guest virtio driver, the host virtio emulator (for the FLUSH
  contract), and the call-table installation site in context
  simultaneously.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the agent's summary.
- [ ] No unrelated files modified.
- [ ] `cargo build -p shared` (steps 1a, 1b, 1c).
- [ ] `cargo test -p shared` (steps 1a, 1b).
- [ ] `cargo build -p guest-protocol` (step 1d).
- [ ] `make instar` (step 1f).
- [ ] `cargo build -p fuzz` and `cargo test -p fuzz` (step 1g).
- [ ] `cargo build --workspace` (steps 1f, 1h, 1j).
- [ ] `cargo clippy --workspace -- -D warnings` (step 1j).
- [ ] `make check-binary-sizes` (step 1j); confirm
      `core.bin` delta is under-budget and all other operation
      binaries are byte-identical.
- [ ] `pre-commit run --all-files` (step 1j).
- [ ] The three new structs and three new pointers match the
      field layouts and signatures in open questions 3–6.
- [ ] No existing `CallTable` function pointer or struct field
      moved — all changes are append-only.
- [ ] `CallTable::VERSION` is `17`, not `16` or `18`.

### Pre-commit verification ritual (step 1j)

The single commit at the end of step 1j must build cleanly
through the entire stack. Specifically:

1. `make instar` — full host VMM + core + all guest operation
   binaries.
2. `make test-rust` — workspace unit tests, including the new
   shared tests from step 1b.
3. `make check-binary-sizes` — confirm no operation binary
   regressed; `core.bin` is expected to grow by ~tens of bytes
   for the three new function-pointer entries.
4. `pre-commit run --all-files` — rustfmt, clippy, trailing
   whitespace, etc.

If any of these fail, fix the failure in the *same* commit (we
are not amending a published commit; this is the original
commit's pre-push verification). Do not split into a follow-up.

## Administration and logistics

### Success criteria

Phase 1 is complete when:

* All ten steps above land in one commit on the `snapshot`
  branch.
* `make instar` builds and `make lint` is clean.
* `make test-rust` passes, including the new struct-layout
  unit tests from step 1b.
* `make check-binary-sizes` is within budget (core.bin grows
  by a small fixed amount, every operation binary unchanged).
* `pre-commit run --all-files` is clean.
* `docs/plans/PLAN-snapshot.md` reflects the chosen magic
  values, the `CallTable::VERSION` bump, and the resolution of
  open question 10.
* The three new structs and three new call-table pointers
  compile cleanly with no `dead_code` warnings (the build
  treats them as `pub`, which suppresses the warning for
  in-crate-only-unused items).

### Future work created by this phase

None directly. Subsequent phases consume the ABI:

- Phase 2 (parser extension) returns a Rust-level
  `SnapshotEntry` that the phase-3 guest binary copies into
  `SnapshotEntryRecord` for emission.
- Phase 3 (list-mode guest binary) reads `SnapshotConfig` from
  `OPERATION_CONFIG_ADDR` and calls `send_snapshot_entry` plus
  `send_snapshot_result`.
- Phase 5 (refcount mutators) and phases 6–8 (create / delete /
  apply guest binaries) use `fsync_input` between metadata
  writes and the header-pointer flip.
- Phase 9 (mutate host) consumes the `SnapshotResult` carried
  by `SnapshotResultMessage` to render the `qemu-img snapshot`
  success line or pass `-q` quiet mode.

If any later phase discovers it needs additional fields in any
of the three structs, append them inside the `_reserved` tail
and shrink the reserved padding accordingly — no other phase
has to change because the layout is `#[repr(C)]` and the
non-reserved fields keep their offsets.

### `fsync_input` rollout to commit (deferred follow-up)

Phase 1 adds `fsync_input` for the snapshot guest's needs. The
commit guest (which today relies on process-exit fsync, a
latent durability bug) should migrate to an explicit
`fsync_input` call between its data-write pass and overlay-clear
pass. This is intentionally **not** done in phase 1: the change
to commit needs its own integration tests against a crash-
injection harness, and bundling it into the ABI phase would
make the commit too large to review safely. Tracked in the
master plan's "Future work" section.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not** added to
`docs/plans/order.yml` per the convention. The master plan
links to it from the Execution table at
`docs/plans/PLAN-snapshot.md:866-882` (step 1i updates that
table to point at this file).

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
