# PLAN-snapshot phase 02: snapshot-table parser extension and list-mode planner

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (the snapshot-table
parser at `src/crates/qcow2/src/lib.rs:684`, the `SnapshotEntry`
and `SnapshotTable` types at lines 605–672, the per-format
parser pattern used by `count_allocated_in_l2_extended` and the
other pure slice-driven helpers, the call-table read helpers
`read_u8_cached` / `read_u16_be_cached` / `read_u32_be_cached`
/ `read_u64_be_cached`, the existing `parse_snapshot_table`
callers in `src/operations/info/src/main.rs:797` and
`src/operations/convert/src/main.rs:413`, the qcow2 snapshot
documentation in `docs/qcow2/qcow2-snapshots.md`, and the
phase-1 ABI surface in `src/shared/src/lib.rs`
(`SnapshotEntryRecord` at the new "Snapshot configuration and
result structures" block)), and ground your answers in what the
code actually does today. Do not speculate about the codebase
when you could read it instead. Where a question touches on
qemu behaviour (the extra-data fallback rules in
`block/qcow2-snapshot.c::qcow2_read_snapshots`,
`QCOW_MAX_SNAPSHOT_NAME = 1024`,
`QCOW_MAX_SNAPSHOT_EXTRA_DATA = 1024`), research as needed.

Phase plans for the parent master plan live alongside it in
`docs/plans/` and are named
`PLAN-snapshot-phase-NN-<descriptive>.md`. The master plan is
[PLAN-snapshot.md](/components/instar/plans/PLAN-snapshot/). This is phase 2 of
fourteen.

I prefer one commit per logical change, and at minimum one
commit per phase. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

Phase 1 landed the call-table and protobuf wire format. Phase 2
extends the qcow2 crate's snapshot-table parser so it surfaces
the v3 extra-data fields (`vm_state_size_large`, `disk_size`,
`icount`) that `qemu-img snapshot -l` needs, and introduces the
streaming primitive that the phase-3 guest binary will use to
emit one `SnapshotEntryRecord` per snapshot without holding the
whole table in memory.

The work is parser-only and no_std-only: this phase does not
touch the host VMM, the guest core, the protobuf layer, or any
operation binary. The exit point is a green
`cargo test -p qcow2` plus a green `make instar` (the existing
callers — `info` and `convert` — continue to use the same
public API surface and don't need to change).

### What the current parser does

`src/crates/qcow2/src/lib.rs:684` exposes:

```rust
pub unsafe fn parse_snapshot_table(
    call_table: &CallTable,
    device_idx: u32,
    nb_snapshots: u32,
    snapshots_offset: u64,
    sector_size: usize,
    input_capacity: u64,
    cache_buf: *mut u8,
    bytes_read: &mut u64,
) -> SnapshotTable;
```

It reads up to `MAX_SNAPSHOTS = 16` entries (line 603) into a
fixed-size `SnapshotTable` containing 16 `SnapshotEntry`
records. Each `SnapshotEntry` carries:

- `l1_table_offset: u64`, `l1_size: u32`
- `id_len: u16`, `name_len: u16`
- `id: [u8; 64]`, `name: [u8; 64]` (null-terminated, max 63
  effective chars)
- `date_sec: u32`, `vm_state_size: u32`

It reads `extra_data_size` only to know how many bytes to skip
before the id/name strings, and **does not surface any of the
extra-data fields**. It also doesn't surface `date_nsec`,
`vm_clock_nsec`, or `extra_data_size` itself.

There are zero unit tests in `src/crates/qcow2/src/lib.rs` for
the snapshot parser. Phase 2 fixes that gap as a side effect of
the refactor — pure slice-driven helpers are trivially unit-
testable, and the new helpers make `parse_snapshot_table`
testable without a mock `CallTable`.

### What phase 2 changes

1. **Extend the data model.** `SnapshotEntry` grows new fields
   for `date_nsec`, `vm_clock_nsec`, `vm_state_size_large`,
   `disk_size`, `icount`, and `extra_data_size`. The existing
   fields stay where they are so existing callers
   (`info`, `convert`) keep working. The id/name buffer stays at
   64 bytes; the larger 256-byte buffer lives in the wire
   `SnapshotEntryRecord` and is populated during the planner
   conversion (step 2f).

2. **Apply the qemu extra-data fallback rules.** Per
   `block/qcow2-snapshot.c::qcow2_read_snapshots`:
   - `extra_data_size >= 8`: read 64-bit `vm_state_size_large`,
     which *overrides* the legacy 32-bit `vm_state_size` for
     reporting. If less than 8, `vm_state_size_large` equals the
     legacy `vm_state_size`.
   - `extra_data_size >= 16`: read `disk_size`. If less than 16,
     `disk_size` falls back to the *current* `virtual_size` of
     the image (which the parser does not know; phase 2 stores
     0 as a sentinel and the planner step 2f resolves the
     fallback by passing in the header's `size` field).
   - `extra_data_size >= 24`: read `icount`. Otherwise
     `SnapshotEntry::icount = u64::MAX` (matches qemu's
     `sn->icount = -1` sentinel and the
     `SnapshotEntryRecord::ICOUNT_ABSENT` constant phase 1
     landed).

3. **Refactor toward pure slice-driven helpers.** A new private
   `parse_snapshot_header_bytes(buf: &[u8]) -> Option<SnapshotHeaderRaw>`
   parses the fixed 40-byte snapshot header from an in-memory
   slice. A new private `parse_snapshot_extra_data(buf: &[u8],
   extra_data_size: u32) -> SnapshotExtraData` applies the
   fallback rules above against a slice of the extra-data
   region. These are pure no_std `fn` that take `&[u8]` and
   return `Option<Struct>` — full unit-testability without a
   mock `CallTable`.

4. **Add `for_each_snapshot_entry`.** A new public function
   that streams snapshot entries one at a time through a
   caller-supplied `FnMut(&SnapshotEntry) -> StreamingAction`
   (or `bool`, see open question 4) closure. Bounded only by
   the `nb_snapshots` header field (qcow2 spec cap 65536); no
   in-memory `[SnapshotEntry; N]` array. This is what the
   phase-3 list-mode guest binary calls to emit
   `SnapshotEntryRecord` messages without filling the guest's
   stack.

5. **Keep `parse_snapshot_table` working.** The bounded
   16-entry variant stays as a thin wrapper over
   `for_each_snapshot_entry`. The wrapper exits the streaming
   loop early when 16 entries have been parsed. `info` and
   `convert` are byte-identical at the API boundary.

6. **Add `find_snapshot_streaming`.** A new public function
   that calls `for_each_snapshot_entry` with a closure that
   stops on the first id-or-name match and returns a single
   owned `SnapshotEntry`. Phases 6–8 (create / delete / apply)
   use this instead of `parse_snapshot_table` +
   `find_snapshot` so the in-memory cap doesn't limit them.

7. **Add a planner converter `snapshot_entry_to_record`.**
   Converts a parsed `SnapshotEntry` (qcow2 internal data
   model) into a `shared::SnapshotEntryRecord` (wire FFI
   model), splitting `date_sec` into hi/lo halves to match
   the phase-1 wire layout. Takes the active-image
   `disk_size` as a parameter so the v2 fallback (when the
   parser stored 0) can be resolved. Lives in
   `src/crates/qcow2/src/lib.rs` as a small public function;
   the phase-3 guest binary calls it inside the
   `for_each_snapshot_entry` callback.

8. **Resolve master plan open question 6.** The "bump
   MAX_SNAPSHOTS to 256" recommendation is moot once we
   stream: there is no in-memory cap at all in the list
   path. The bounded `parse_snapshot_table` keeps its 16-
   entry cap (untouched, for `info` / `convert`). The
   resolution is documented inline in the master plan and
   in `docs/qcow2/qcow2-snapshots.md`.

### What phase 2 does not change

- The wire format (phase 1 ABI is frozen).
- The host VMM, the guest core, the protobuf layer, the
  fuzz harness mock.
- Any operation binary (`info`, `convert`, etc.).
- The `MAX_SNAPSHOTS = 16` constant (no in-memory cap raise;
  streaming makes it irrelevant).
- The `id` / `name` buffer sizes inside the internal
  `SnapshotEntry`. Wire records carry 256-byte names; the
  internal data model stays at 64 bytes because that is what
  the bounded callers (`info`, `convert`) currently use, and
  bumping the internal buffer would balloon `SnapshotTable`'s
  16-entry stack footprint. The converter step 2f truncates
  to the wire buffer at conversion time (with explicit
  documentation).

### Why this is shippable as one commit per logical change

Phase 2 has no ABI bump and no version mismatch hazard. It can
ship as a single commit (parser refactor + helpers + tests +
docs) or split into two (refactor + helpers as one commit,
docs + master-plan resolution as a second). The plan
recommends **one commit** because the refactor and the helpers
are tightly coupled and the test suite would be incomplete on
either side of a split.

## Mission and problem statement

After phase 2 lands:

1. `src/crates/qcow2/src/lib.rs` has six new fields on
   `SnapshotEntry`: `date_nsec: u32`, `vm_clock_nsec: u64`,
   `vm_state_size_large: u64`, `disk_size: u64`, `icount: u64`,
   `extra_data_size: u32`. The legacy `date_sec: u32` and
   `vm_state_size: u32` fields remain in place. `SnapshotEntry`
   sizes to a power-of-two-or-near value; verify with a
   `size_of` assertion in tests so future drifts are caught.

2. Two new private pure functions exist:
   - `fn parse_snapshot_header_bytes(buf: &[u8]) -> Option<SnapshotHeaderRaw>`
     where `SnapshotHeaderRaw` is a private struct carrying
     the eight u8/u16/u32/u64 fields decoded from the fixed
     40-byte header. Returns `None` if `buf.len() < 40`.
   - `fn parse_snapshot_extra_data(buf: &[u8], extra_data_size: u32) -> SnapshotExtraData`
     where `SnapshotExtraData` carries
     `(vm_state_size_large, disk_size, icount)` after applying
     qemu's progressive-reveal rules. `disk_size` is 0 when
     `extra_data_size < 16` (sentinel "use header's
     virtual_size"); `icount` is `u64::MAX` when
     `extra_data_size < 24`.

3. A new public function exists:
   ```rust
   /// Stream snapshot entries one at a time. Returns `true` if
   /// the full table was visited; `false` if the callback
   /// returned `false` (early exit) or the parser hit a read
   /// error.
   pub unsafe fn for_each_snapshot_entry(
       call_table: &CallTable,
       device_idx: u32,
       nb_snapshots: u32,
       snapshots_offset: u64,
       sector_size: usize,
       input_capacity: u64,
       cache_buf: *mut u8,
       bytes_read: &mut u64,
       mut callback: impl FnMut(&SnapshotEntry) -> bool,
   ) -> bool;
   ```
   Bounded only by `nb_snapshots`. No in-memory `[SnapshotEntry; N]`
   array. The single in-flight `SnapshotEntry` lives on
   `for_each_snapshot_entry`'s own stack frame.

4. A new public function exists:
   ```rust
   /// Find a snapshot by id or name without building the full
   /// table. Returns the matched entry, or `None`.
   pub unsafe fn find_snapshot_streaming(
       call_table: &CallTable,
       device_idx: u32,
       nb_snapshots: u32,
       snapshots_offset: u64,
       sector_size: usize,
       input_capacity: u64,
       cache_buf: *mut u8,
       bytes_read: &mut u64,
       needle: &[u8],
   ) -> Option<SnapshotEntry>;
   ```

5. A new public converter exists:
   ```rust
   /// Convert a parsed snapshot entry into the wire-FFI
   /// representation. `header_virtual_size` is the active
   /// image's virtual size, used to resolve the v2-extra-
   /// data-absent `disk_size` fallback.
   pub fn snapshot_entry_to_record(
       entry: &SnapshotEntry,
       header_virtual_size: u64,
   ) -> shared::SnapshotEntryRecord;
   ```
   The converter splits `entry.date_sec` into `date_sec_hi` /
   `date_sec_lo` u32 halves (the phase-1 wire layout matches
   qcow2's on-disk hi/lo split; the parser stores the assembled
   u32 in `entry.date_sec`, the converter re-splits it
   trivially as `hi=0, lo=date_sec`). It truncates id/name to
   the wire-record buffers (32 bytes for id, 256 for name) and
   sets the `_len` fields to the truncated length, matching
   qemu's silent-truncation behaviour. If `entry.disk_size`
   is 0 (the v2 sentinel), `record.disk_size` =
   `header_virtual_size`.

6. The existing `parse_snapshot_table` is rewritten as a
   thin wrapper over `for_each_snapshot_entry` that stops
   after 16 entries. The function signature is unchanged; the
   bounded result is unchanged for `info` and `convert`.

7. New unit tests in `src/crates/qcow2/src/lib.rs` cover:
   - `parse_snapshot_header_bytes` happy path on a synthetic
     40-byte buffer.
   - `parse_snapshot_header_bytes` rejects buffers < 40 bytes.
   - `parse_snapshot_extra_data` v2 fallback
     (extra_data_size = 0): vm_state_size_large = 0,
     disk_size = 0, icount = u64::MAX.
   - `parse_snapshot_extra_data` partial v3 (extra_data_size
     = 8): vm_state_size_large populated, disk_size = 0,
     icount = u64::MAX.
   - `parse_snapshot_extra_data` full v3 (extra_data_size
     = 16): both populated, icount = u64::MAX.
   - `parse_snapshot_extra_data` v3-with-icount
     (extra_data_size = 24): all three populated.
   - `parse_snapshot_extra_data` over-sized extra-data
     (extra_data_size = 1024): same as 24, trailing bytes
     ignored.
   - `parse_snapshot_extra_data` truncated buffer
     (extra_data_size = 24 but buf.len() < 24): returns the
     prefix populated and the rest at sentinels.
   - `snapshot_entry_to_record` v2 fallback: `entry.disk_size
     = 0`, `header_virtual_size = 4096` → record.disk_size =
     4096.
   - `snapshot_entry_to_record` happy path: all fields copied
     with no truncation.
   - `snapshot_entry_to_record` long-name truncation: name
     of 300 bytes → record.name_len = 256, last 44 bytes
     dropped.
   - `snapshot_entry_to_record` long-id truncation: id of
     40 bytes → record.id_len = 32, last 8 bytes dropped.
   - `SnapshotEntry` `size_of` assertion to catch future
     drifts.
   - `SnapshotEntry::ICOUNT_ABSENT` mirrors
     `shared::SnapshotEntryRecord::ICOUNT_ABSENT` (both
     `u64::MAX`).

8. `docs/qcow2/qcow2-snapshots.md` adds a paragraph on the
   streaming parser surface (`for_each_snapshot_entry`,
   `find_snapshot_streaming`) and notes the extra-data
   fallback rules with their offsets. The bounded vs
   streaming distinction is documented.

9. `docs/plans/PLAN-snapshot.md` open question 6 is resolved
   inline (no cap raise; streaming used instead). The
   "Design overview > Architectural shape" bullet that
   mentions `parse_snapshot_table_extended` is reworded to
   reflect the actual function names.

10. `make instar` builds clean, `make lint` is clean,
    `make test-rust` passes (the new qcow2 tests raise the
    total by ~14), `make check-binary-sizes` is unchanged
    or only marginally affected (the qcow2 crate gains a
    handful of pure functions; `info` and `convert`
    operation binaries are recompiled with the extended
    `SnapshotEntry` but the field additions are read-only
    and not used by them, so optimisation should remove the
    dead code), `pre-commit run --all-files` is clean.

Nothing in phase 2 changes user-visible behaviour. `instar
snapshot` still prints "unrecognized subcommand"; `instar
info` and `instar convert` produce byte-identical output.

## Open questions

### 1. Should `SnapshotEntry` grow id/name buffers from 64 to 256 bytes?

Working answer: **no**. Growing the buffers from 64 to 256
balloons the bounded `SnapshotTable`'s footprint from
`16 × ~152 = 2.4 KiB` to `16 × ~536 = 8.6 KiB` on the stack of
every caller of `parse_snapshot_table`. The bounded callers
(`info`, `convert`) don't need long names. The streaming
callers can hold one 8.6 KiB record at a time if they need
it, but the converter to `SnapshotEntryRecord` is the natural
place for the 256-byte name buffer to appear: the wire record
already has it (phase 1).

This means the internal `SnapshotEntry` truncates names
exceeding 63 bytes (the current behaviour) and the converter
preserves the existing behaviour. qemu's
`QCOW_MAX_SNAPSHOT_NAME = 1024` means names *can* be up to
1024 bytes; we still truncate to 63 at the parser layer.

Open subquestion: do real-world workflows ever use snapshot
names > 63 bytes? Almost never. Proxmox names look like
`vzdump-qemu-100-2026_06_05-12_34_56` (~40 chars). oVirt's
follow a similar convention. libvirt's are usually short.
The truncation is documented as a known limitation in
`docs/quirks.md` and a follow-up can revisit if a user files
a bug.

(Alternative considered: grow only the streaming
`for_each_snapshot_entry` path to use a 256-byte name buffer.
This is possible but bifurcates the data model and makes the
converter conditional. Rejected as over-engineered for v1.)

### 2. Should the converter `snapshot_entry_to_record` live in `qcow2` or in `shared`?

Working answer: **in `qcow2`**. The converter reads
`SnapshotEntry` (qcow2-defined) and writes
`SnapshotEntryRecord` (shared-defined). `qcow2` already depends
on `shared`. Adding the converter to `shared` would invert the
dependency. The converter doesn't touch any qcow2-specific
runtime state, but it is qcow2-conceptual: every other format
either has no snapshots or rejects the operation. Living in
`qcow2` is correct.

### 3. Should the converter zero-initialise `_reserved` bytes?

Working answer: **yes**. The wire FFI struct has a 32-byte
`_reserved` tail that the phase-1 unit test
`snapshot_entry_record_is_valid_accepts_magic_rejects_zero`
expects to be all-zero by default. The converter constructs
the record with `SnapshotEntryRecord {... _reserved: [0; 32],
...}` explicitly. Any future field additions append inside
the reserved tail and the converter has to be updated to
populate them. This matches the convention used by the
`map_extent_record_*` and `commit_result_*` constructors.

### 4. `for_each_snapshot_entry` callback signature: `bool` vs custom enum

Working answer: **`bool`** (true = continue, false = stop).
Mirrors `Iterator::take_while` and is the convention used by
`for_each_l2_entry` and similar helpers in the qcow2 crate.
A custom `StreamingAction { Continue, Stop, Error }` enum
would be more expressive but harder to combine with `?`.

The function's *return value* is a `bool`: `true` means the
full table was visited, `false` means either the callback
stopped early or a read error aborted the loop. The latter
case sets `bytes_read` and may leave the cache in an
indeterminate state; callers that need to distinguish "stopped
by callback" from "read error" can check whether the visited
count equals `nb_snapshots`.

(Alternative considered: return `Result<usize, ParseError>`.
Rejected because the rest of the qcow2 parser surface uses
`Option`/`bool` and doesn't have a `ParseError` enum; adding
one is its own refactor.)

### 5. Should `find_snapshot_streaming` accept *id-only* or *name-only* needles?

Working answer: **id-or-name match**, matching the existing
bounded `find_snapshot` at line 874 and qemu's
`bdrv_snapshot_find_by_id_and_name` ordering. The needle is
compared first against `entry.id[..id_len]`, then against
`entry.name[..name_len]`. First match wins.

The existing `find_snapshot` checks id first then name; the
streaming version is byte-identical at the comparison level
so call sites that swap from bounded to streaming have the
same matching behaviour.

### 6. Should phase 2 also extend `info` to surface the new fields?

Working answer: **no**. `info` only sets
`FLAG_HAS_SNAPSHOTS`; surfacing per-snapshot metadata in
`info` output is a separate scope (`qemu-img info` *does*
include `Snapshot list:` in its human output when snapshots
exist, but instar's `info` does not match qemu's `info`
exactly today — that is its own divergence). Tracked as
future work; not phase 2.

### 7. Should we add a maximum extra-data-size cap?

Working answer: **yes**. qemu caps
`QCOW_MAX_SNAPSHOT_EXTRA_DATA = 1024`; we adopt the same cap.
The parser refuses an entry whose `extra_data_size > 1024` by
returning `None` from the per-entry parser. This stops the
phase-12 fuzz harness from blowing up on a `nb_snapshots = 1`
entry with `extra_data_size = u32::MAX`.

The fallback rule is: if the cap is exceeded, the streaming
visitor stops (returns `false`) and `bytes_read` reflects what
was consumed so far. The bounded `parse_snapshot_table` does
the same.

### 8. Should the per-entry pure parser take a separate "entry buffer" or use the existing sector-cache pattern?

Working answer: **use the existing sector-cache pattern for
I/O**, but extract the *parsing logic* into pure helpers that
take a `&[u8]`. The wrapper assembles the bytes (40-byte
header + extra_data + id + name) into a scratch buffer using
`read_u*_be_cached`, then calls the pure parser. This gives
us testability without changing the I/O model.

The scratch buffer for one entry is bounded at 40 (header) +
1024 (max extra_data per open question 7) + 64 (id) + 256
(name) = 1384 bytes. Round up to 1536 = 1.5 KiB for the
scratch slot; this fits in the existing `cache_buf` passed by
callers (which is `MAX_SECTOR_SIZE` = 65536 bytes today,
plenty of slack). The pure parser takes slices of the scratch
buffer.

Alternative considered: read the entire entry into a separate
local buffer on the wrapper's stack. Cleaner test boundary
but ~1.5 KiB stack per call. Rejected: the existing
`cache_buf` is already passed in and is sized for sector
reads; reusing it is consistent with the existing parser's
approach.

### 9. Should we add a "v2 disk_size fallback" parameter to `parse_snapshot_table`?

Working answer: **no, defer the fallback to the converter**.
The parser stores `0` in `SnapshotEntry::disk_size` when the
extra-data is too short to carry it. The converter
(`snapshot_entry_to_record`) takes the active header's
virtual size as a parameter and resolves the fallback at
conversion time. This keeps the parser pure and unaware of
the active header.

### 10. Should the docs/qcow2/qcow2-snapshots.md update describe the streaming surface or the bounded surface?

Working answer: **both**. The doc describes the bounded
`parse_snapshot_table` for backwards compatibility (it's
what `info` and `convert` use) and the streaming
`for_each_snapshot_entry` for unbounded scanning (what the
snapshot subcommand uses). A short paragraph explains when
to pick which. The extra-data fallback rules table moves
from the prose into the new section.

## Execution

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 2a | medium | sonnet | worktree | Extend `SnapshotEntry` in `src/crates/qcow2/src/lib.rs` (currently at lines 605–623) with six new fields in this order, appended at the end of the struct: `date_nsec: u32`, `vm_clock_nsec: u64`, `vm_state_size_large: u64`, `disk_size: u64`, `icount: u64`, `extra_data_size: u32`. Update `SnapshotEntry::zeroed` to initialise them: `date_nsec: 0`, `vm_clock_nsec: 0`, `vm_state_size_large: 0`, `disk_size: 0`, `icount: u64::MAX` (the qemu "absent" sentinel), `extra_data_size: 0`. Add a `pub const ICOUNT_ABSENT: u64 = u64::MAX;` associated constant. Do **not** change `MAX_SNAPSHOTS` (stays at 16). Do **not** change the existing field ordering. The existing callers (`info` at `src/operations/info/src/main.rs:797`, `convert` at `src/operations/convert/src/main.rs:413`) must continue to compile and run unchanged. Run `cargo build -p qcow2 --target x86_64-unknown-none` (or the workspace target for guest crates; check the qcow2 crate's Cargo.toml for `[lib]` settings) and `cargo build -p info -p convert` until clean. |
| 2b | high | opus | worktree | Add the pure slice-driven helpers to `src/crates/qcow2/src/lib.rs`, placed immediately before `parse_snapshot_table` at the current line 684. (1) A private struct `SnapshotHeaderRaw` carrying `l1_table_offset: u64`, `l1_size: u32`, `id_str_size: u16`, `name_size: u16`, `date_sec: u32`, `date_nsec: u32`, `vm_clock_nsec: u64`, `vm_state_size: u32`, `extra_data_size: u32` (mirrors the 40-byte on-disk header). (2) A private struct `SnapshotExtraData` carrying `vm_state_size_large: u64`, `disk_size: u64`, `icount: u64`. (3) `fn parse_snapshot_header_bytes(buf: &[u8]) -> Option<SnapshotHeaderRaw>` that returns `None` if `buf.len() < 40` and otherwise reads big-endian fields at the offsets documented in the existing parser (lines 700–714). The reads use `u64::from_be_bytes` / `u32::from_be_bytes` / `u16::from_be_bytes` directly on slices — no call-table involvement. (4) `fn parse_snapshot_extra_data(buf: &[u8], extra_data_size: u32) -> SnapshotExtraData` that applies qemu's progressive-reveal rules per open question 7: refuses `extra_data_size > QCOW_MAX_SNAPSHOT_EXTRA_DATA` (1024) by returning sentinels (vm_state_size_large=0, disk_size=0, icount=u64::MAX); reads each field if `extra_data_size >= 8 / >= 16 / >= 24` respectively; uses `buf.get(off..off+8).map(read_u64_be).unwrap_or(0)` style so a truncated buffer falls back gracefully. Add `pub const QCOW_MAX_SNAPSHOT_EXTRA_DATA: u32 = 1024;` next to `MAX_SNAPSHOTS`. Add 8 unit tests at the end of `mod tests` covering the cases enumerated in mission item 7 above (extra-data v2 / v3-8 / v3-16 / v3-24 / oversized / truncated / header happy path / header rejects-short). Use opus: the bit-level layout cross-reference against `block/qcow2-snapshot.c` benefits from broader context. |
| 2c | high | opus | worktree | Add the streaming public function `for_each_snapshot_entry` to `src/crates/qcow2/src/lib.rs`, placed immediately after `parse_snapshot_table`. Signature exactly per mission item 3 above. Implementation: loop `i in 0..nb_snapshots`, for each (i) advance `offset` to the next entry, (ii) read 40 bytes into the start of `cache_buf` via `read_u*_be_cached`, (iii) call `parse_snapshot_header_bytes`, (iv) abort with `false` if the header is invalid or `extra_data_size > QCOW_MAX_SNAPSHOT_EXTRA_DATA`, (v) read `extra_data_size` bytes into `cache_buf[40..]`, (vi) call `parse_snapshot_extra_data`, (vii) read `id_str_size` bytes (capped at 63) into a stack-local `SnapshotEntry`'s `id` field, (viii) same for `name`, (ix) assemble the full `SnapshotEntry`, (x) invoke `callback(&entry)`, (xi) if callback returned false, return `false` from the function. After the loop returns `true`. Update `bytes_read` cumulatively across reads. The function is `pub unsafe` and documented with the same `# Safety` block as `parse_snapshot_table`. Then rewrite `parse_snapshot_table` to a thin wrapper: call `for_each_snapshot_entry` with a closure that pushes into `SnapshotTable::entries[idx]` while `idx < MAX_SNAPSHOTS`, returning `false` from the closure once full. The wrapper keeps the existing public signature and return type. Verify `info` and `convert` continue to build and behave identically — run `make instar` and a manual `instar info` against an existing qcow2 fixture. Use opus: this step holds the cache-buffer layout, the sector-read invariants, and the streaming-callback ergonomics simultaneously. |
| 2d | medium | sonnet | worktree | Add `find_snapshot_streaming` to `src/crates/qcow2/src/lib.rs` immediately after `find_snapshot` (currently at line 874). Signature exactly per mission item 4 above. Implementation: call `for_each_snapshot_entry` with a closure that compares `entry.id[..entry.id_len as usize]` and `entry.name[..entry.name_len as usize]` against `needle`. On match, copy the entry into a `Cell<Option<SnapshotEntry>>`-style holder and return `false` from the closure to stop the iteration. Return the captured entry (or `None`) from the outer function. Note: since `SnapshotEntry` is `Copy` (it should be — fixed-size byte arrays and integers only), the holder can be a simple `Option<SnapshotEntry>` on the wrapper's stack, captured by `&mut` in the closure. Confirm `SnapshotEntry` derives `Copy` and `Clone`; if it does not, add the derive. Add unit tests: streaming-find by id, streaming-find by name, streaming-find no-match, streaming-find with corrupt entry mid-table (read error → `None`). |
| 2e | medium | sonnet | worktree | Add the planner converter `snapshot_entry_to_record` to `src/crates/qcow2/src/lib.rs` immediately after `find_snapshot_streaming`. Signature exactly per mission item 5 above. Implementation: construct a `shared::SnapshotEntryRecord` with `magic: shared::SnapshotEntryRecord::MAGIC`, split `entry.date_sec` into `date_sec_hi: 0, date_sec_lo: entry.date_sec` (qcow2 stores `date_sec` as a single u32; the wire layout has hi/lo halves but for the foreseeable future the hi half is 0 because Unix time fits in u32 until 2106). Copy `vm_clock_nsec`, `vm_state_size_large`, `icount`, `l1_table_offset`, `l1_size`, `extra_data_size` directly. Resolve `disk_size`: `if entry.disk_size == 0 { header_virtual_size } else { entry.disk_size }`. Copy `id_len = entry.id_len.min(32) as u32`, `name_len = entry.name_len.min(256) as u32`. Zero-initialise `id: [u8; 32]` and `name: [u8; 256]`, then `id[..id_len].copy_from_slice(&entry.id[..id_len])` and same for name. `_reserved: [0; 24]`. Add 4 unit tests covering the v2 fallback, happy path, long-name truncation, long-id truncation per mission item 7. Note: the wire record's id buffer is 32 bytes and the parser's is 64 — long-id truncation drops anything beyond byte 31. The wire record's name buffer is 256; parser's is 64 — name *grows* at the wire layer (zero-padded), no truncation in practice for v1. |
| 2f | low | sonnet | worktree | Update `docs/qcow2/qcow2-snapshots.md`. Add a new section "Parser surface" after the existing "In-Memory Snapshot Representation" section. It should describe: (1) `parse_snapshot_table` — bounded, capped at 16 entries, used by `info` and `convert`; (2) `for_each_snapshot_entry` — streaming, no in-memory cap, used by the snapshot subcommand; (3) `find_snapshot_streaming` — streaming find by id-or-name; (4) `snapshot_entry_to_record` — planner converter to the wire FFI. Add a short paragraph on the extra-data fallback rules with a small table (`extra_data_size`, fields populated). Keep the section under 60 lines. |
| 2g | low | sonnet | worktree | Update `docs/plans/PLAN-snapshot.md`. (1) In open question 6 (around the master plan's "MAX_SNAPSHOTS = 16 cap" bullet), append a "Resolved in phase 2: streaming used; no in-memory cap raise needed. `parse_snapshot_table` stays at 16 entries for `info` and `convert`; `for_each_snapshot_entry` handles arbitrary counts." (2) In the Execution table row for phase 2, change the description from `parse_snapshot_table_extended` to "`for_each_snapshot_entry` streaming primitive + extra-data fallback + planner converter" so it matches what actually landed. (3) In the Design overview > Architectural shape section, rename the bullet "`parse_snapshot_table_extended()` — extends..." to "`for_each_snapshot_entry()` — streaming variant; `parse_snapshot_table` remains the bounded variant for `info` / `convert` callers." Keep all edits under 20 lines total. |
| 2h | low | sonnet | worktree | Run `make instar`, `make test-rust`, `make check-binary-sizes`, `make lint`, and `pre-commit run --all-files` from the worktree root. Confirm `info` and `convert` operation binaries still build and `make check-binary-sizes` is within budget. Stage and present a single commit covering all of steps 2a–2g with the commit-message convention from `~/.claude/CLAUDE.md` (50-char first line ending in `.`, 75-char body wrap, Prompt paragraph, Signed-off-by, Co-Authored-By line that includes model + context window + effort + any other active settings). The commit message should explain the streaming refactor, the extra-data fallback rules adopted from qemu, the planner converter, and that the existing public API stays byte-compatible for `info` / `convert`. |

## Agent guidance

### Execution model

All implementation work for this phase is done by sub-agents,
never in the management session. The management session (this
conversation) is reserved for review and decision-making.
After each step the management session:

1. Reads the actual files that were supposed to change.
2. Confirms no unrelated files were modified.
3. Runs the lint / test commands that the step's brief names.
4. Either commits, asks for a retry with a sharper brief, or
   upgrades the model.

All steps in this phase use `isolation: "worktree"`. The
parser refactor cannot land half-applied: between steps 2c
and 2d, callers of `find_snapshot` continue to work but no
new streaming-find exists yet; intermediate states are not
shippable. A worktree per attempt keeps the main checkout
clean.

### Model and effort notes

- Steps 2a, 2d, 2e, 2f, 2g, 2h are mechanical extensions of
  well-established patterns. Sonnet at medium / low effort
  with the briefs above is enough.
- Steps 2b and 2c are the load-bearing reasoning steps:
  qemu's progressive-reveal extra-data rules have bit-level
  invariants that the unit tests have to mirror exactly, and
  the streaming refactor of `parse_snapshot_table` has to
  preserve byte-for-byte compatibility for the existing
  `info` and `convert` callers. Use opus.

### Management session review checklist

After each step:

- [ ] Read the changed files — don't trust the agent's
      summary.
- [ ] No unrelated files modified.
- [ ] `cargo build -p qcow2` (steps 2a, 2b, 2c, 2d, 2e).
- [ ] `cargo test -p qcow2` (steps 2b, 2c, 2d, 2e).
- [ ] `cargo build -p info -p convert` (steps 2a, 2c).
- [ ] `make instar` (step 2c, 2h).
- [ ] `make check-binary-sizes` (step 2h).
- [ ] `make lint` (step 2h).
- [ ] `pre-commit run --all-files` (step 2h).
- [ ] The new fields on `SnapshotEntry` are appended at the
      end (no existing field moved).
- [ ] The new public functions are documented with
      `# Safety` blocks where they take `*mut u8` (matches
      the existing `parse_snapshot_table` doc style).
- [ ] `for_each_snapshot_entry`'s callback signature is
      `FnMut(&SnapshotEntry) -> bool`, not a custom enum
      (open question 4).
- [ ] The converter handles the v2 fallback by accepting
      `header_virtual_size: u64` and substituting when
      `entry.disk_size == 0` (open question 9).
- [ ] No new `unsafe` outside the existing parser's safety
      contract.

### Pre-commit verification ritual (step 2h)

The single commit at the end of step 2h must build cleanly
through the entire stack:

1. `make instar` — full host VMM + core + all guest
   operation binaries.
2. `make test-rust` — workspace unit tests, including the
   new qcow2 tests from steps 2b, 2c, 2d, 2e.
3. `make check-binary-sizes` — confirm no operation binary
   regressed; `info` and `convert` should be within a few
   hundred bytes of their pre-phase sizes (the `SnapshotEntry`
   field additions are read-only from those callers' point of
   view).
4. `make lint` / `pre-commit run --all-files`.

If any of these fail, fix the failure in the *same* commit
(we are not amending a published commit; this is the
original commit's pre-push verification). Do not split into
a follow-up.

## Administration and logistics

### Success criteria

Phase 2 is complete when:

* All eight steps above land in one commit on the `snapshot`
  branch.
* `make instar` builds and `make lint` is clean.
* `make test-rust` passes, including the ~14 new qcow2 unit
  tests from steps 2b, 2c, 2d, 2e.
* `make check-binary-sizes` is within budget.
* `pre-commit run --all-files` is clean.
* `info` and `convert` operation binaries continue to behave
  byte-identically (manual smoke test on a known-good qcow2
  fixture with at least one snapshot).
* `docs/plans/PLAN-snapshot.md` open question 6 is resolved.
* `docs/qcow2/qcow2-snapshots.md` documents the streaming
  parser surface and the extra-data fallback rules.

### Future work created by this phase

- **`info` could surface per-snapshot metadata.** qemu-img's
  `info` includes a `Snapshot list:` section when snapshots
  exist; instar's `info` only sets the `FLAG_HAS_SNAPSHOTS`
  bit. Surfacing the list in `info` is a separate scope
  (touches the `InfoResult` wire shape, the host renderer,
  baselines, etc.). Tracked here as a follow-up.
- **Snapshot name buffer growth.** Internal `SnapshotEntry`
  truncates names to 63 bytes (existing behaviour). If a
  user reports a workflow that uses longer names, revisit;
  the wire record already supports 256 bytes, so only the
  internal parser-to-record path needs adjustment.
- **Snapshot-table > 16 entries in the bounded path.** The
  bounded `parse_snapshot_table` stays at 16 because that's
  what `info` and `convert` need. If a future caller needs
  a larger bounded variant, add `parse_snapshot_table_n<N>`
  with a const-generic cap. Streaming covers the unbounded
  case today.
- **Fuzz coverage of extra-data parsing.** Phase 12 wires
  the per-entry parsers into `fuzz_snapshot_parse`. The
  unit tests in steps 2b/2c are exhaustive for the
  fallback rules but only cover well-formed inputs; fuzz
  covers adversarial.

### Bugs fixed during this work

This section will list any bugs encountered during
development that we fix in passing. The existing
`parse_snapshot_table` has no test coverage at all; any
correctness gaps surfaced during the refactor count as
bugs fixed.

### Documentation index maintenance

This is a phase plan, not a master plan. It is **not**
added to `docs/plans/order.yml` per the convention. The
master plan links to it from the Execution table at
`docs/plans/PLAN-snapshot.md:866-882` (step 2g updates that
row to point at this file).

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
