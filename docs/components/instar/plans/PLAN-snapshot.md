# `instar snapshot` subcommand

## Status: Complete

Phases 1-14.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2 snapshot table layout, refcount table
semantics, L1/L2 COPIED-flag invariants, `qemu-img snapshot`
behaviour, KVM / virtio), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

All planning documents go in `docs/plans/`. Phase plans for this
master plan are named `PLAN-snapshot-phase-NN-<descriptive>.md`
alongside this file and linked from the Execution table below.
They are *not* added to `docs/plans/order.yml` — only the master
plan is.

I prefer one commit per logical change, and at minimum one commit
per phase. Each commit should be self-contained: it should build,
pass tests, and have a clear commit message explaining what
changed and why.

## Situation

`PLAN-convert-followups.md` enumerates seven `qemu-img` subcommands
deferred from the original convert effort. `create`, `measure`,
`resize`, `rebase`, `commit`, and `map` have all shipped (see
their respective master plans). **`snapshot` is the sole remaining
item.**

`snapshot` is scheduled last because:

- It is the only deferred subcommand that is **format-restricted to
  qcow2**. `qemu-img snapshot` refuses every other format
  ("Format driver 'vmdk' does not support image snapshots"). Our
  test matrix collapses from "five formats × matrix" to "qcow2 ×
  matrix", but the qcow2 implementation has to be deeper than any
  prior plan because every mutating mode (`-a`/`-c`/`-d`) touches
  refcounts and the COPIED-flag invariant — the single most
  delicate piece of qcow2 metadata.
- Modern KVM/virtualisation workflows have largely moved away from
  internal qcow2 snapshots in favour of external overlay chains
  (which `rebase` and `commit` already cover). The day-to-day
  demand is correspondingly lower; this is the right item to land
  last rather than first.
- It nevertheless completes the convert-follow-ups roster and
  closes out `qemu-img` parity for the operations instar will
  ever care about. Downstream consumers — oVirt's `ovirt-imageio`
  upload sizing, Proxmox's PVE-backed disk migration scripts —
  exercise `qemu-img snapshot -l` for inventory and occasionally
  `-d` to clean up old snapshots before transfer. Listing is the
  high-value mode; mutation is the long-tail.
- It builds on existing infrastructure rather than introducing
  any new call-table primitives. Both `read_output_sector`
  (resize phase 7, `src/shared/src/lib.rs:879`) and
  `write_input_sector` (rebase/commit phase 1,
  `src/shared/src/lib.rs:914`) are already in the ABI; the
  snapshot image is opened RW as input device 0 and uses
  `write_input_sector` for all mutations, exactly like commit's
  overlay-RW path.
- The qcow2 snapshot table parser is *already in tree*
  (`src/crates/qcow2/src/lib.rs:684` `parse_snapshot_table`,
  with `SnapshotEntry` and `SnapshotTable` types and a
  16-snapshot in-memory cap). It is currently used only by
  `info` to set `FLAG_HAS_SNAPSHOTS`. Phase 1 of this plan
  extends it from "is there *any* snapshot?" to "emit one
  message per snapshot with full metadata".

The relevant existing infrastructure this plan builds on:

- **Snapshot table parser**:
  `parse_snapshot_table()` and `SnapshotEntry` / `SnapshotTable`
  in `src/crates/qcow2/src/lib.rs:599-869` plus
  `find_snapshot()` at `:874`. Already reads `l1_table_offset`,
  `l1_size`, id/name, `date_sec`, and the legacy 32-bit
  `vm_state_size`. The 64-bit `vm_state_size_large`, `disk_size`,
  and `icount` (extra-data section, qcow2 v3) are *not* yet
  surfaced — phase 1 extends the parser.
- **Refcount table walk infrastructure**: `scan_allocation()`
  already reads refcount blocks (phase 2 of PLAN-measure) and
  the `qcow2::lookup_cluster` / `qcow2::write_l2_entry`
  pattern in resize phases 2–3 handles L1/L2 mutation. We do
  not need a new walk; we need a *mutator* on top of the
  existing reader.
- **VMM subcommand scaffolding** in `src/vmm/src/main.rs`
  (clap `Commands` enum at lines 2482–2508, per-op `*Args`,
  `run_*`), call-table boundary in `src/shared/src/lib.rs`
  (`OPERATION_CONFIG_ADDR`, per-op `*Config` and `*Result`
  structs), protobuf wrapper in
  `crates/guest-protocol/proto/guest.proto` (`GuestMessage`
  oneof, next free tag is 17 after `map_extent`=15 /
  `map_result`=16).
- **Two-device-RW open path**: `open_chain_devices_rw` from
  PLAN-rebase-commit phase 1, which opens the input device RW
  so the guest can use `write_input_sector(0, …)`. Snapshot
  uses the single-device variant (no chain): one image opened
  RW on input slot 0, no output device.
- **Streaming guest-message channel** used by `info`, `check`,
  `convert`, `commit`, and `map` for unbounded record streams.
  Snapshot list mode reuses this for emitting one
  `SnapshotEntryMessage` per snapshot (bounded by qcow2's own
  cap of 65536 snapshots, in practice well under that).
- **Cross-version baseline generator**
  (`instar-testdata/scripts/generate-baselines.py`) and its
  `expected-outputs/{info,check,compare,measure,map}-{human,json}/`
  layout, which we extend with a `snapshot-list-{human,json}`
  profile pair. Mutating modes (`-a`/`-c`/`-d`) are validated
  by *post-operation* image equivalence (instar vs qemu-img
  ran on the same input), not by stdout baselines, because
  successful mutation produces no stdout.
- **Coverage-guided fuzz harnesses** in `src/fuzz/` and the
  **differential fuzzer** (`scripts/differential-fuzz.py`).
  Both extend naturally: the fuzz target drains
  `parse_snapshot_table` against random qcow2 fragments; the
  differential fuzzer applies a random sequence of
  `instar snapshot -c/-d/-a` and compares image bytes against
  the same sequence under qemu-img.

## Mission and problem statement

Implement `instar snapshot` such that:

1. It accepts the same surface area as `qemu-img snapshot`:
   - A required `FILENAME` (qcow2 only).
   - **Mutually exclusive mode flags**:
     - `-l` — list snapshots (read-only).
     - `-a SNAPSHOT` — apply ("goto") snapshot by ID or name.
     - `-c NAME` — create snapshot with the given name; qemu
       assigns the next available numeric ID.
     - `-d SNAPSHOT` — delete snapshot by ID or name.
   - `-f FMT` — format hint (qemu accepts; we accept and
     enforce qcow2-only).
   - `-q` — accepted for CLI compatibility; no visible effect
     for any snapshot mode under either tool (success is always
     silent; errors are always printed regardless of `-q`). See
     `docs/quirks.md` for the generalised no-op note (phase 9).
   - `-U` / `--force-share` — list-only no-op. instar takes no
     image locks, so `-U -l` is a no-op accepted for parity.
     `-U` combined with any mutating mode (`-c`/`-d`/`-a`) is
     refused host-side before any file access, matching qemu's
     substance (though not its exact stderr text). See
     `docs/quirks.md` for the D1 entry (phase 9).
   - `--output={human,json}` — instar extension; `qemu-img
     snapshot -l` is human-only. JSON is opt-in.
   - `--image-opts` — explicitly rejected with a clear error
     (consistent with `measure`, `map`, etc.).
2. The qcow2 parsing **and** mutation work runs entirely inside
   the KVM guest. Untrusted input metadata never touches the
   host. The host opens the image RW and dispatches; the guest
   does every read, every refcount mutation, every header
   rewrite.
3. **Format restriction**: qemu-img refuses non-qcow2 with
   `Format driver '<fmt>' does not support image snapshots`.
   instar matches this error verbatim modulo the leading
   binary name; document divergence in `docs/quirks.md` if
   any qemu-img version in the matrix differs in wording.
4. **List mode** matches `qemu-img snapshot -l` byte-for-byte
   across the matrix in
   `instar-testdata/qemu-img-binaries/x86_64/`, including the
   column layout (`ID`, `TAG`, `VM_SIZE`, `DATE`, `VM_CLOCK`,
   `ICOUNT` (v3 only)), the date formatting (`%Y-%m-%d
   %H:%M:%S`), and the VM-clock format (`HH:MM:SS.NNN`).
5. **Mutating modes** produce a qcow2 file byte-equivalent (or
   refcount-equivalent — see open question 7) to the result of
   running the same `qemu-img snapshot` command. Validated by:
   - Post-op `qemu-img check` clean.
   - Post-op `qemu-img info` snapshot count, name, ID match.
   - Post-op `instar info` matches qemu-img info on the same
     image.
   - Post-op `qemu-img compare` against a snapshot-applied
     reference image returns "Images are identical".
6. **Backing chain**: Internal snapshots and backing chains
   compose at the qcow2 spec level (the snapshot's L1 refers
   to the same data clusters; clusters not present in the
   overlay still resolve through the backing). v1 supports
   snapshots in images *with* a backing file but does not
   need to walk the backing chain for any operation —
   refcount manipulation only touches *this* image's clusters.
   The active L1 already refers to the overlay's clusters
   (not the backing's); the snapshot's L1 is a snapshot of the
   active L1; both refer only to overlay clusters.
7. Coverage-guided fuzzing exercises `parse_snapshot_table`
   and the refcount mutators (`update_snapshot_refcount`,
   `alloc_cluster`, `free_cluster`, COPIED-flag rewrite) on
   adversarial qcow2 fragments. The differential fuzzer runs
   `instar snapshot -c/-d/-a` sequences against `qemu-img
   snapshot` and compares post-op `qemu-img check` plus
   stripped-metadata image bytes.

## Design overview

### Architectural shape

The work decomposes along the same three-layer pattern as
prior plans (parser primitives → guest binary → host glue),
but the parser layer here is **mutating** for three of the
four modes, which materially changes the testing posture.

1. **Mutator primitives in `src/crates/qcow2/`** (and a new
   `src/crates/snapshot/` if the surface grows enough to
   warrant its own crate — see open question 3). Specifically:
   - `for_each_snapshot_entry()` — streaming variant; emits one
     `SnapshotEntry` at a time via a `FnMut` callback, surfacing
     `vm_state_size_large` (extra-data offset 0), `disk_size`
     (offset 8), and `icount` (offset 16). The bounded
     `parse_snapshot_table` remains for `info` / `convert`
     callers and is now a thin wrapper over the streaming
     primitive.
   - `update_snapshot_refcount(addend, l1_table_offset,
     l1_size, …)` — walks the snapshot's L1 → L2 chain and
     increments / decrements refcounts for every referenced
     data cluster *and* every referenced L2 table cluster.
     For compressed clusters, updates the refcount for the
     entire compressed extent (which may span more than one
     cluster).
   - `alloc_cluster(…) -> Option<u64>` — finds a free cluster
     by scanning refcount blocks (reuses the resize-grow
     allocator pattern), sets refcount=1, returns host offset.
     Handles refcount-table growth when the existing refcount
     table is full.
   - `free_cluster(host_offset, …)` — decrements refcount.
     Does *not* coalesce/release space at the file level
     (qcow2 doesn't either; freed clusters stay in the file
     until reused).
   - `write_snapshot_table_entry()` — serialises one
     `QCowSnapshotHeader` + extra-data + id/name strings into
     the snapshot-table area at a given offset; handles the
     8-byte alignment padding.
   - `rebuild_snapshot_table()` — for delete and create-when-
     full: allocates new cluster(s), rewrites the snapshot
     table, updates header's `snapshots_offset` /
     `nb_snapshots`, frees the old table clusters. The qcow2
     spec mandates an atomic header rewrite as the
     commit point.
   - `update_copied_flags_for_l1()` — after a refcount
     change crosses the 1-boundary, walks the *active* L1 → L2
     and rewrites the COPIED bit on each entry whose refcount
     state changed. (qemu does this incrementally inside
     `qcow2_update_snapshot_refcount`; we can do it as a
     separate pass to keep the planner readable.)
   The mutators take `(call_table, device_idx)` and use
   `write_input_sector(0, …)` for every write. They run
   inside the guest.

2. **Guest binary** `src/operations/snapshot/`. Reads
   `SnapshotConfig` (mode discriminator + arg strings) from
   `OPERATION_CONFIG_ADDR`, opens device 0, dispatches on
   mode:
   - `MODE_LIST`: calls `parse_snapshot_table_extended()`,
     emits one `SnapshotEntryMessage` per snapshot followed
     by a `SnapshotResult` terminator. Refuses non-qcow2 with
     `ERROR_UNSUPPORTED_FORMAT`.
   - `MODE_APPLY`: finds snapshot by ID/name, validates
     `l1_size <= active_l1_size_max`, performs refcount
     adjustments (inc snapshot's L1 chain, dec active L1's
     chain), copies snapshot's L1 contents into the active
     L1 area, updates COPIED flags, sends `SnapshotResult`.
   - `MODE_CREATE`: copies active L1 to a freshly allocated
     L1-table cluster, increments refcounts on all clusters
     reachable from active L1 (now reachable from two L1s →
     refcount goes from 1 to 2), clears COPIED flags on the
     active L1's entries that just crossed 1→2, appends new
     snapshot table entry (growing table if needed), updates
     header (`nb_snapshots`, possibly `snapshots_offset`),
     sends `SnapshotResult` with the auto-assigned ID.
   - `MODE_DELETE`: finds snapshot, decrements refcounts on
     its L1 chain (data clusters first, then L2 tables, then
     the L1 cluster itself), removes the entry from the
     snapshot table, rewrites the snapshot table (compacting
     to remove the gap), updates header, sets COPIED flags
     on active L1/L2 entries that just crossed 2→1, sends
     `SnapshotResult`.

3. **Host glue**: `run_snapshot()` in `src/vmm/src/main.rs`
   that wires up clap args, opens the image RW (single-device
   variant of `open_chain_devices_rw`), builds `SnapshotConfig`,
   launches the guest, consumes streamed messages, renders
   list output for `-l`, or the quiet/success line for the
   mutating modes.

Splitting mutator primitives into qcow2-crate functions keeps
them `cargo test`-able with synthetic images. The fuzz harness
for the refcount mutators is then trivial (no KVM, no serial
channel).

### Why qcow2-only

`qemu-img snapshot -l` on raw / vmdk / vhd / vhdx prints
nothing (or errors); `qemu-img snapshot -c`/`-d`/`-a` on
non-qcow2 errors with `Format driver '<fmt>' does not support
image snapshots`. The qcow2 format is the only one in our
parser set with an internal snapshot table.

- raw: no metadata at all.
- vmdk: descriptor mentions snapshots only in the
  `parentCID` / overlay sense (external snapshots, which are
  qemu-img *external* snapshots = `qemu-img create -b base`,
  not internal snapshots). `qemu-img snapshot` on vmdk errors.
- vhd: differencing VHD is conceptually similar to an
  external snapshot chain; no internal snapshot table.
- vhdx: VHDX has a log/journal but not user-facing
  snapshots; `qemu-img snapshot` errors.

This plan does not extend snapshot support to other formats.
The host-side dispatcher rejects non-qcow2 sources at
`run_snapshot` with the qemu-compatible error.

### Streaming vs. buffering

Only `-l` (list) produces variable-length output, and qcow2
caps `nb_snapshots` at 65536. In practice (oVirt, Proxmox,
typical libvirt workflows) the count is single digits.
Reusing the streaming `*Message`-per-entry pattern from `map`
costs nothing and keeps the guest's stack flat. Each
`SnapshotEntryMessage` carries id, name, l1_table_offset,
l1_size, date_sec, date_nsec, vm_clock_nsec,
vm_state_size_large, disk_size, icount, and extra_data_size
(for forward compat with future qemu extensions).

Mutating modes emit only a single `SnapshotResult` summary
at end-of-stream.

### Call-table and protobuf changes

Phase 1 landed the structs with magics `0x534E4150` ("SNAP"),
`0x534E4552` ("SNER"), and `0x534E5253` ("SNRS"); the
`CallTable::VERSION` bumped from 16 to 17 to cover the three
new function pointers appended at the end.

- New `SnapshotConfig` in `src/shared/src/lib.rs` next to
  `MapConfig`, with magic `SNAP`:
  ```rust
  #[repr(C)]
  pub struct SnapshotConfig {
      pub magic: [u8; 4],          // *b"SNAP"
      pub version: u32,
      pub mode: u32,               // MODE_LIST | _APPLY | _CREATE | _DELETE
      pub arg_len: u32,            // bytes used in `arg`
      pub arg: [u8; 256],          // snapshot ID / name (UTF-8, no nul)
      pub flags: u32,              // FLAG_QUIET | FLAG_FORCE_SHARE
      pub reserved: [u8; 240],     // future
  }
  ```
- New `SnapshotEntryMessage` in `guest.proto`:
  ```
  message SnapshotEntryMessage {
    string id = 1;                 // snapshot ID (qemu's "0", "1", ...)
    string name = 2;               // snapshot tag/name
    uint64 l1_table_offset = 3;
    uint32 l1_size = 4;
    uint32 date_sec = 5;
    uint32 date_nsec = 6;
    uint64 vm_clock_nsec = 7;
    uint64 vm_state_size = 8;      // 64-bit large value
    uint64 disk_size = 9;          // virtual disk size at snapshot
    uint64 icount = 10;            // record/replay; -1 if absent
    uint32 extra_data_size = 11;   // for forward compat
  }
  ```
- New `SnapshotResult` in `src/shared/src/lib.rs`:
  ```rust
  #[repr(C)]
  pub struct SnapshotResult {
      pub mode: u32,
      pub error: u32,
      pub snapshots_emitted: u32,  // populated for MODE_LIST
      pub assigned_id_len: u32,    // populated for MODE_CREATE
      pub assigned_id: [u8; 64],   // populated for MODE_CREATE
      pub reserved: [u8; 64],
  }
  ```
  with `ERROR_*` codes for: success, not-qcow2, snapshot-not-
  found, duplicate-name, refcount-overflow, allocation-failed
  (refcount table full and cannot grow), snapshot-table-full
  (would exceed `QCOW_MAX_SNAPSHOTS`), io-error, l1-size-
  mismatch (apply: snapshot's L1 doesn't fit in active L1 area
  and growing would exceed `QCOW_MAX_L1_SIZE`), invalid-utf8
  in name.
- New `SnapshotResultMessage` in `guest.proto`:
  ```
  message SnapshotResultMessage {
    uint32 mode = 1;
    uint32 error = 2;
    uint32 snapshots_emitted = 3;
    string assigned_id = 4;
  }
  ```
- `GuestMessage` oneof additions: `SnapshotEntryMessage` as
  field 17, `SnapshotResultMessage` as field 18.
- Three new `CallTable` function pointers (appended at the end,
  per the back-compat convention):
  - `send_snapshot_entry: unsafe extern "C" fn(*const
    SnapshotEntryRecord)` — host-side stub serialises the
    record into the protobuf message. The intermediate
    `SnapshotEntryRecord` is a plain struct in `shared`
    (parallel to `MapExtentRecord`) so the guest doesn't
    depend on protobuf.
  - `send_snapshot_result: unsafe extern "C" fn(*const
    SnapshotResult)` — same pattern as `send_resize_result`,
    `send_rebase_result`, `send_commit_result`,
    `send_map_result`.
  - `fsync_input: unsafe extern "C" fn(u32) -> bool` — see
    open question 10 below; phase 1 added the guest virtio
    flush path.

### Refcount management — the hard part

Every mutating mode boils down to walking an L1 table and
adjusting refcounts. The skeleton:

```rust
// pseudocode
fn update_snapshot_refcount(
    call_table, dev, l1_table_offset, l1_size, addend) -> Result {
    for l1_idx in 0..l1_size {
        let l1_entry = read_l1_entry(l1_table_offset, l1_idx);
        if l1_entry == 0 { continue; }   // unallocated subtree
        let l2_host_offset = l1_entry & L1E_OFFSET_MASK;

        // Per-L2 walk
        for l2_idx in 0..l2_entries_per_cluster {
            let l2_entry = read_l2_entry(l2_host_offset, l2_idx);
            match classify_l2(l2_entry) {
                Unallocated => continue,
                Standard(host_off) => {
                    update_refcount(host_off, addend)?;
                }
                Compressed { offset, sectors } => {
                    // Compressed extent may span >1 cluster.
                    update_compressed_refcount(offset, sectors, addend)?;
                }
                Zero => continue,  // pure zero clusters have no host alloc
            }
        }

        // The L2 table cluster itself
        update_refcount(l2_host_offset, addend)?;
    }
    // The L1 table cluster(s) themselves
    for cluster in l1_cluster_range(l1_table_offset, l1_size) {
        update_refcount(cluster, addend)?;
    }
    Ok(())
}
```

Three invariants must hold across the operation:

- **Refcount ≤ `1 << refcount_bits`**. qcow2 v3 default is
  16 bits → 65535. Overflow returns `ERROR_REFCOUNT_OVERFLOW`
  and the operation aborts before mutating anything (dry-run
  pass first, then apply pass — see open question 8).
- **COPIED bit on L1/L2 entries is correct**. Bit
  `QCOW_OFLAG_COPIED` (high bit of an L1 or L2 entry) is set
  iff the referenced cluster has refcount=1. After any
  refcount mutation that crosses 1, the corresponding L1/L2
  entry must be rewritten with the bit flipped. This is the
  invariant that makes COW correct; getting it wrong is what
  causes "qcow2 corruption" reports.
- **Atomicity of the snapshot-table swap**. New table at new
  location; new entries written; header pointer (`snapshots_
  offset` + `nb_snapshots`) updated in one 8-byte-aligned
  write *after* the table is durable; old table clusters
  freed last. Any crash before the header update leaves the
  old table intact; any crash after leaves the new table
  intact.

We adopt qemu's ordering verbatim; the planner expresses it
as an explicit step list and the guest just executes it.

### Host CLI dispatch

`qemu-img snapshot`'s clap surface is small. The mode flags
are mutually exclusive; clap's `ArgGroup` with `multiple=false`
and `required=true` enforces this at parse time.

```rust
#[derive(Args, Debug)]
struct SnapshotArgs {
    /// Image file to operate on (qcow2 only).
    filename: String,
    /// List snapshots.
    #[arg(short = 'l', long, group = "mode")]
    list: bool,
    /// Apply / "goto" the named snapshot.
    #[arg(short = 'a', long, group = "mode", value_name = "SNAPSHOT")]
    apply: Option<String>,
    /// Create a snapshot with the given name.
    #[arg(short = 'c', long, group = "mode", value_name = "NAME")]
    create: Option<String>,
    /// Delete the named snapshot (by ID or name).
    #[arg(short = 'd', long, group = "mode", value_name = "SNAPSHOT")]
    delete: Option<String>,
    /// Force the image format detection (must be qcow2 if set).
    #[arg(short = 'f', long)]
    format: Option<String>,
    /// Suppress success line on stdout.
    #[arg(short = 'q', long)]
    quiet: bool,
    /// Skip image-lock check (accepted for qemu-img compat; no-op).
    #[arg(short = 'U', long = "force-share")]
    force_share: bool,
    /// Reject `--image-opts` with a clear error.
    #[arg(long)]
    image_opts: bool,
    /// Output format (instar extension; qemu-img -l is human only).
    #[arg(long, default_value = "human", value_parser = ["human", "json"])]
    output: String,
}
```

clap's `ArgGroup` requires `clap >= 4.5`, which is already in
the dependency tree (used by resize / rebase / commit).

### qemu-img snapshot -l output format

`qemu-img snapshot -l image.qcow2` produces (for a v3 qcow2
with two snapshots):

```
Snapshot list:
ID        TAG               VM_SIZE                DATE     VM_CLOCK     ICOUNT
1         snap1                  0 B 2026-06-05 12:34:56  00:00:00.000          0
2         snap2                  0 B 2026-06-05 12:35:00  00:00:00.000          0
```

Important format details (verified against qemu source
`block/qcow2-snapshot.c::dump_one_snapshot` and
`qemu-img.c::collect_snapshots`):

- The header row is fixed text; the data rows are
  `printf("%-10s%-18s%7s%20s%13s%11s\n", ...)`.
- `ID` is left-aligned, width 10.
- `TAG` is left-aligned, width 18 (longer names are not
  truncated; column shifts).
- `VM_SIZE` is right-aligned, width 7, formatted via qemu's
  `size_to_str` (e.g. `0 B`, `4.0 KiB`, `1.0 MiB`).
- `DATE` is right-aligned, width 20, formatted as
  `%Y-%m-%d %H:%M:%S` in **local time** (this is a known
  gotcha for cross-version baselines — the qemu-img matrix
  runs under known TZ; we pin `TZ=UTC` in baseline gen and
  in tests, as the existing baseline harness already does for
  `qemu-img info`'s "date created" field for vhd).
- `VM_CLOCK` is right-aligned, width 13, formatted as
  `HH:MM:SS.NNN` (vm_clock_nsec divided into hours, minutes,
  seconds, ms).
- `ICOUNT` is right-aligned, width 11, only emitted for v3
  images that have icount in extra-data (and only emitted if
  *any* snapshot in the list has an icount; otherwise the
  column is omitted entirely from the header and rows).
- The leading `Snapshot list:` is printed *only* if there is
  at least one snapshot. Empty snapshot table: no output,
  exit 0.

Human output formatting lives in `src/vmm/src/main.rs` or
a new `src/vmm/src/snapshot_output.rs` (the latter is
cleaner if the column-width logic gets long).

JSON output (instar extension) is a flat array of objects:

```json
[
  { "id": "1", "name": "snap1", "vm-state-size": 0,
    "date": { "seconds": 1717589696, "nanoseconds": 0 },
    "vm-clock": { "seconds": 0, "nanoseconds": 0 },
    "icount": 0 }
]
```

The JSON key names mirror qemu's QMP `SnapshotInfo` struct
(`block/qapi.c::qmp_query_named_block_nodes`) so consumers
that already parse QMP snapshot listings can reuse their
parsers.

### Versioning and baseline strategy

We extend `instar-testdata/scripts/generate-baselines.py`
with a `snapshot-list` command entry. For each qemu-img
version and each qcow2 baseline image that has at least one
snapshot, capture `qemu-img snapshot -l` into
`expected-outputs/snapshot-list-human/qcow2/<version>/<image-id>.{stdout,stderr,meta.json}`.

**Phase 10 resolution of open question 1 (JSON baselines)**:
`qemu-img snapshot -l` has no `--output=json` equivalent, so
there is no qemu source of truth for a JSON baseline. JSON
golden files (`--output=json`) are deferred to phase 11 as
instar-side self-baselines in `tests/` — a self-baseline
catches schema regressions, which is all a self-baseline can
do. The testdata generation flow must not depend on instar
build artefacts.

**Phase 10 resolution of open question 2 (mutation baselines)**:
The "`-c` then `-l`" per-version baseline pairs are dropped.
Mutating modes print nothing (no stdout to baseline); column
drift is already captured by listing frozen fixtures under every
version; ID assignment is probe-confirmed stable across the
whole 6.0.0–10.2.0 matrix; and post-op image equivalence is
validated live by the phase 6–8 harnesses and phase 11/13
going forward. A generation-time mutation would also embed
wall-clock timestamps, breaking determinism.

Mutating modes continue to be validated by `qemu-img check` +
`qemu-img info` + `qemu-img compare` post-op assertions in
`tests/test_snapshot.py`.

Baseline matrix (phase 10 actuals):
- 80 qemu-img versions (6.0.0 through 10.2.0)
- 1 output type (snapshot-list-human)
- 12 images: 11 snapshot-bearing fixtures + 1 empty-case
- Total: 2880 files

Far smaller than the map baseline tree, as estimated.

### Test fixtures

We need a small set of qcow2 fixtures *with snapshots* in
`instar-testdata`. These do not exist today: the existing
qcow2 fixtures are snapshot-free. The fixture generator
(`generate-baselines.py`) runs `qemu-img create` then
`qemu-img snapshot -c` to produce each fixture. The fixtures
go into a new manifest tier (`snapshot-bearing-qcow2`)
because they're tied to the qcow2 driver's ID-assignment
behaviour, which has changed (very slightly) across qemu
versions and which our cross-version harness needs to handle.

### Per-mode plans

#### `-l` list mode

The simplest mode. v1 path:

1. Read header, refuse non-qcow2 with
   `ERROR_UNSUPPORTED_FORMAT`.
2. `parse_snapshot_table_extended()` walking up to
   `MAX_SNAPSHOTS` entries (16; see open question 6 on
   raising this).
3. For each entry, build a `SnapshotEntryRecord` and call
   `send_snapshot_entry`.
4. Send `SnapshotResult { mode: MODE_LIST, error: OK,
   snapshots_emitted: n }`.

Host renders the column-aligned table or the JSON array.

#### `-c` create mode

Reuses existing readers + new mutators. Plan:

1. Read header, refuse non-qcow2.
2. Parse snapshot table for the max existing ID. **Duplicate
   names are allowed** — this was empirically corrected in
   phase 6: `qemu-img snapshot -c` accepts a duplicate name and
   creates a second entry (the "already exists" error belongs to
   HMP `savevm`, not `qemu-img`). The earlier "refuse on
   duplicate name" claim here was wrong; see
   PLAN-snapshot-phase-06-create.md fact 2.
3. Assign next ID: max(existing IDs) + 1, formatted as a decimal
   string. qemu's `find_new_snapshot_id` takes `max` over
   `strtoul(id_str)` and renders `id_max + 1` with `%lu`; we
   replicate (`parse_decimal_id` / `format_decimal_u64`).
4. Allocate contiguous cluster(s) for the snapshot's L1 table
   copy. L1 size matches active L1 size. (A 0-byte virtual disk
   has `l1_size == 0`: no allocation; the snapshot's stored
   `l1_table_offset` mirrors the active L1's offset, matching
   qemu.)
5. Copy active L1 contents to the new L1 location, captured
   **before** the COPIED-flag rewrite (so the copy keeps stale
   COPIED bits, exactly like qemu).
6. Walk active L1 chain: increment refcount on every reachable
   data cluster *and* every L2 table cluster (these now have two
   L1s pointing at them).
7. Walk active L1 *again*, this time clearing
   `QCOW_OFLAG_COPIED` on every entry whose refcount is now
   > 1 (which is all of them, by definition of the create).
8. **Always reallocate the snapshot table.** qemu's
   `qcow2_write_snapshots` always writes a fresh, contiguous
   table and frees the old one — there is no "append in place"
   path (phase 6 fact 6 corrects the earlier claim here). The
   old table stays intact until the header pointer flips, which
   is the better crash-safety story.
9. Update header `nb_snapshots` + `snapshots_offset` as a single
   12-byte write at offset 60 (the commit point), then free the
   old table.
10. Send `SnapshotResult` with `assigned_id`.

(`vm_state_size`, `vm_clock_nsec`, and `vm_state_size_large` are
always 0 for `qemu-img`-style creates, and `icount` is written as
`0` — not the `u64::MAX` "absent" sentinel — confirmed
empirically in phase 6; see open question 5 and fact 4 of the
phase-6 plan.)

The order matters: steps 5–7 must complete before step 8,
because step 8 commits the new snapshot's existence and a
crash between 5 and 8 leaves orphaned clusters but no
dangling references.

#### `-d` delete mode

The opposite of create. **Two earlier claims here were corrected
empirically in phase 7** (see
PLAN-snapshot-phase-07-delete.md, facts 2/3/6): the argument
matches by **name only, first occurrence in table order**
(`bdrv_snapshot_find` has no ID path in qemu 10 — "by id or
name" was wrong for delete), and qemu always **rewrites the
whole table at a new allocation** via `qcow2_write_snapshots`
(there is no in-place shift-and-rewrite path).

1. Read header, refuse non-qcow2.
2. Parse snapshot table; find the first entry whose **name**
   equals the argument. Not-found exits 1 before any write.
3. Build the compacted table (every entry except the removed
   one, verbatim) at a fresh allocation; write it, then the
   header's `nb_snapshots - 1` + new `snapshots_offset` as the
   commit point (or `0 / 0` when the table empties — no
   allocation at all in that case).
4. Walk target's L1 chain, decrement refcount on every
   reachable data cluster, L2 table cluster, L1 table
   cluster; free the old table's clusters.
5. Walk active L1, set `QCOW_OFLAG_COPIED` on entries whose
   refcount now equals 1 (i.e. clusters that were shared
   with the deleted snapshot and are now sole-owned by
   active).

#### `-a` apply / goto mode

The trickiest because the L1 table is *overwritten*.
*(Corrected by phase 8 against `qcow2_snapshot_goto` /
`qcow2_update_snapshot_refcount` in qemu 10.0.x and the
installed qemu-img 10.0.8 — the original sketch assumed L1
growth support and did not know about qemu's disk-size
truncate or the snapshot-stored-L1 flag write. See
PLAN-snapshot-phase-08-apply.md for the landed design.)*

1. Read header, refuse non-qcow2; the uniform mutating-mode
   feature gates.
2. Find the target by **ID first, then name — two FULL
   passes** over the raw table (qemu's
   `find_snapshot_by_id_or_name`: a later entry matching by
   ID beats an earlier entry matching by name). This is the
   opposite asymmetry from delete's name-only matcher.
3. Geometry checks. A stored `disk_size` differing from the
   current virtual size means the image was resized after
   the snapshot was taken — qemu **truncates the image**
   here (`blk_truncate` inside `qcow2_snapshot_goto`);
   instar v1 **refuses** (`ERROR_L1_SIZE_MISMATCH`, with a
   resize-back workaround message). A snapshot L1 *larger*
   than the active L1 would need qemu's L1 grow — refused in
   v1 (only reachable on hand-crafted images given the
   disk-size refusal). A *smaller* snapshot L1 is supported:
   the copy is zero-padded to the active L1's size, like
   qemu.
4. Precheck both directions read-only (SwapForApply) before
   any write, then the in-memory +1 walk over the target's
   chain; write the refblocks (increments only); fsync.
5. Write the target's RAW L1 content (stale flags intact),
   zero-padded, over the active L1 offset — the commit
   point; fsync. The active L1 *cluster offset itself* does
   not change.
6. In-memory -1 walk over the staged OLD active chain, then
   one final-state COPIED refresh over the new chain and
   the surviving old chain.
7. Write the refblocks (now with decrements), the refreshed
   L1 to BOTH offsets — the active location at the padded
   length and the snapshot's stored L1 at its own length
   (qemu's +1 walk rewrites the stored L1's flags; instar
   replicates the same final bytes) — the dirty
   snapshot-set L2s, and the surviving old-active L2s
   (never the freed ones); fsync.

Apply's crash consistency is best-effort, like qemu's: a
crash before the commit point leaves only repairable leaks;
between the commit point and the final group, leaks plus
stale COPIED flags — never a dangling reference. Documented
in `docs/quirks.md`.

### Source format scope

v1 supports:
- qcow2 v2 and v3.
- qcow2 with/without backing file.
- qcow2 with extended L2 (subcluster bitmaps): refcount
  semantics are identical to standard L2 — the bitmap is
  internal to the L2 entry; the cluster is allocated or not
  at L2-entry granularity. Mutating snapshots on extended-L2
  images works without special handling.
- qcow2 with up to `MAX_SNAPSHOTS = 16` snapshots
  (matching the existing parser cap). The qcow2 spec
  allows 65536; raising the cap is open question 6.

v1 does not support (rejected with clear error):
- Compressed clusters (`QCOW2_INCOMPAT_COMPRESSION` flag) —
  refcount adjustment for compressed extents requires
  walking sub-cluster ranges; out of scope. v1 errors with
  `ERROR_UNSUPPORTED_FEATURE` on `-c`/`-d`/`-a` for any
  image whose active L1 chain contains a compressed entry.
  `-l` works regardless.
- Encrypted images (`QCOW2_INCOMPAT_ENCRYPTION`) — instar
  has no LUKS write path yet; refuse.
- External data file (`QCOW2_INCOMPAT_EXTERNAL_DATA`) —
  refcount semantics differ; refuse.
- Bitmaps extension (qcow2 v3 dirty bitmaps) — bitmaps
  reference clusters too, and a snapshot operation must
  also dec/inc the bitmap's clusters. Refuse with
  `ERROR_UNSUPPORTED_FEATURE`; future work.

These restrictions are checked host-side after `info` and
again guest-side as a defence-in-depth.

### Test matrix

| Mode | Compare against | Phase-11 test |
|------|-----------------|---------------|
| `-l` human  | qemu-img stdout per version | `TestSnapshotListHuman` (factory, 12 images) |
| `-l` json   | qemu-img stdout (instar extension; instar self-baseline) | `TestSnapshotListGoldens` (factory, 12 images) |
| `-c` then `-l`  | qemu-img stdout per version | `TestSnapshotCreate.test_create_list_agreement` |
| `-c` then qemu-img check | clean | `TestSnapshotCreate.test_create_check_clean` |
| `-c` then qemu-img info | matches qemu-img-applied reference | `TestSnapshotCreate.test_create_second_assigns_id_2_duplicate_name_accepted` |
| `-d` then qemu-img check | clean | `TestSnapshotDelete.test_delete_{first,last,sole}_check_clean` |
| `-d` then qemu-img info | matches | `TestSnapshotDelete.test_delete_name_only_matching_on_namecollision` |
| `-a` then qemu-img check | clean | `TestSnapshotApply.test_apply_check_clean` |
| `-a` then `instar info` vs `qemu-img info` | identical | `TestSnapshotApply.test_apply_by_id_on_namecollision` |
| `-a` then qemu-img compare against pre-`-c` image | identical | `TestSnapshotApply.test_apply_restores_content` |

The differential fuzzer extends with a random
create/delete/apply chain on a randomly generated qcow2
image, post-op-comparing against the same chain applied by
qemu-img.

## Open questions

1. **Should this be one master plan or split (`PLAN-snapshot-list`
   + `PLAN-snapshot-mutate`)?** Recommendation: **one plan,
   phased so list-mode lands first**. List mode is shippable
   on its own (~2 phases of work), but the planning and ABI
   work is shared with mutating modes. Splitting would
   duplicate the prompt/situation/design-overview sections
   and risks the mutating modes never landing. The phase
   table below front-loads list mode so it is mergeable
   independently after phase 4.

2. **`--image-opts`**: Same posture as `measure`, `map`,
   etc. — reject explicitly with a clear error; document in
   `docs/quirks.md`. Resolved.

3. **New `src/crates/snapshot/` crate, or extend
   `src/crates/qcow2/`?** **Resolved in phase 5**: new
   `src/crates/snapshot/` crate, parallel to `commit` and
   `rebase`. The original recommendation to extend `qcow2`
   was overturned because the project's actual convention
   (commit / rebase / resize / measure / create) is one
   crate per mutating operation; the qcow2 crate stays
   read-mostly. The phase also lifts `set_refcount` from
   `resize::qcow2` into the new crate as
   `snapshot::qcow2::set_refcount_in_block`; resize keeps
   its 14 existing call sites working through a thin
   wrapper. See PLAN-snapshot-phase-05-refcount-planners.md
   for the full rationale.

4. **`SnapshotResult` struct vs protobuf-only?** Every
   other mutating operation has a `*Result` struct in
   `src/shared/src/lib.rs` for the guest to populate. Mirror
   that pattern; the protobuf message is a serialised view.
   Resolved.

5. **VM state on `-c`**: `qemu-img snapshot -c` always
   creates a snapshot with `vm_state_size = 0` (the running
   VM state is the QEMU monitor's `savevm` command, not
   `qemu-img`'s). v1 mirrors this: `-c` always writes 0 for
   vm_state. Resolved.

6. **`MAX_SNAPSHOTS = 16` cap**: The existing parser caps
   at 16 in-memory entries (`src/crates/qcow2/src/lib.rs:603`).
   qcow2 allows 65536. We don't need 65536-entry capacity
   in the guest because (a) we only iterate, and the guest
   can emit entries one-at-a-time without holding them all
   in memory; (b) for `-c`/`-d`/`-a` we operate on one
   entry at a time. Recommendation: **bump to 256 for list
   mode** (which is more than any real-world workflow), but
   stream-emit so the guest's working set stays small.
   Confirm in phase 2.

   **Resolved in phase 2**: streaming used; no in-memory
   cap raise needed. `parse_snapshot_table` stays at 16
   entries (the bounded API used by `info` and `convert`);
   the new `for_each_snapshot_entry` streaming primitive
   handles arbitrary counts up to the qcow2 spec cap of
   65536. The snapshot subcommand's list / find paths use
   the streaming primitive so the guest's working set is
   one `SnapshotEntry` at a time regardless of
   `nb_snapshots`.

7. **Bit-exact image comparison post-op vs structural
   comparison**: `qemu-img snapshot -c` writes a
   `date_sec`/`date_nsec` that captures *wall-clock time at
   the moment of the call*. instar and qemu-img will
   produce different `date_sec` values. For diff-testing,
   we either (a) inject a fixed timestamp via env var
   (`SOURCE_DATE_EPOCH`-style) into both — qemu has
   `qemu-img --time-now=N` no, that doesn't exist —
   (b) strip the timestamp before comparing image bytes,
   or (c) compare structurally via `qemu-img info`. Approach
   (c) is simplest and matches what users care about; we
   adopt it. The fuzz harness then asserts:
   "post-instar `qemu-img info` ≡ post-qemu-img `qemu-img info`
   modulo the date fields."

8. **Dry-run pass before mutation**: refcount overflow can
   happen on `-c` if any cluster's existing refcount is
   already at the max (extremely rare but possible in
   contrived images and definitely possible in fuzz inputs).
   Aborting halfway leaves the image inconsistent.
   **Resolved in phase 5**:
   `snapshot::qcow2::update_snapshot_refcount` implements a
   two-pass mutator. Pass 1 walks the relevant L1(s) using
   `read_refcount_in_block` and `check_refcount_after_addend`,
   returning `RefcountOverflow { at_host_offset }` on the
   first overflow *without* touching the refblocks buffer.
   Pass 2 walks again and applies via `set_refcount_in_block`.
   `-d` paths still go through the dry-run (with addend `-1`)
   for symmetry — the underflow check catches caller
   bookkeeping bugs early.

9. **`-a` after a chain of `-c`s that referenced
   different cluster sets**: instar's refcount management
   is per-cluster, so this Just Works — refcount goes from
   2 (active + snapshot) back to 1 (active only) on dec,
   and the COPIED-flag rewrite sees the transition. No
   special handling needed beyond the per-mode plans above.
   **Resolved in phase 8** by the apply matrix's round-trip
   and cross-mode scenarios
   (`tools/snapshot-apply-matrix.sh`): snap → write → apply
   → write → apply again, and apply-middle-of-three →
   delete → apply, all byte-identical to qemu with
   `qemu-img check` clean and content restored
   (`qemu-img compare` against a snapshot-time reference).
   One subtlety surfaced: when the old active chain shares
   L2 tables with a *different* snapshot, those L2s survive
   the apply and qemu flushes them with refreshed COPIED
   flags — instar writes the surviving (refcount > 0)
   old-active L2s back in its final group to match.

10. **Atomicity of snapshot-table rewrite**: qemu's
    `qcow2_write_snapshots` allocates new cluster(s), writes
    the table, fdatasyncs, updates header, fdatasyncs, frees
    old. **Resolved in phase 1**: step 1f added the guest-side
    virtio `flush()` path that issues `VIRTIO_BLK_T_FLUSH`,
    exposed through the new `fsync_input(u32) -> bool` call-
    table pointer. The host-side FLUSH handler already existed
    in `src/vmm/src/virtio/block.rs:428`, which calls
    `self.backing.sync()` (`File::sync_all()` on the host FD).
    Snapshot, commit, and future writers benefit. (commit
    currently relies on process-exit fsync; switching it to
    use `fsync_input` at the appropriate checkpoint is the
    deferred follow-up tracked in "Future work".)

11. **Cluster-allocation strategy**: When allocating a new
    cluster for the snapshot's L1 or for a new snapshot
    table cluster, we scan refcount blocks for a 0 refcount.
    The resize-grow allocator does the same thing
    (`allocate_data_cluster` in
    `src/crates/qcow2/src/lib.rs`). Reuse it. Open question:
    does the existing allocator handle the case where the
    refcount table itself needs to grow? Resize phase 2
    answered "yes" for grow; we inherit that work.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. Shared ABI: `SnapshotConfig`, `SnapshotResult`, `SnapshotEntryRecord`, error codes, `send_snapshot_entry`/`send_snapshot_result` call-table pointers, `fsync_input` call-table primitive, `GuestMessage` arms | PLAN-snapshot-phase-01-abi.md | Complete |
| 2. Snapshot-table parser extension and list-mode planner: `for_each_snapshot_entry` streaming primitive + extra-data fallback + planner converter (`snapshot_entry_to_record`); `find_snapshot_streaming`; extended `SnapshotEntry` with v3 fields; ~14 new qcow2 unit tests | PLAN-snapshot-phase-02-list-planner.md | Complete |
| 3. Guest binary scaffolding + list mode (`src/operations/snapshot/`): config read, format check, dispatch, MODE_LIST emit loop | PLAN-snapshot-phase-03-list-guest.md | Complete |
| 4. Host CLI for list mode (`run_snapshot`, clap surface, human + JSON renderer, `qemu-img snapshot -l` byte-exact output) | PLAN-snapshot-phase-04-list-host.md | Complete |
| 5. Refcount mutators (planner crate): `update_snapshot_refcount`, `alloc_cluster_in_refblocks`, `set_refcount_in_block` (lifted from resize), `update_copied_flags_for_l1`, two-pass overflow check. New `src/crates/snapshot/` crate parallel to commit/rebase; ~60 unit tests | PLAN-snapshot-phase-05-refcount-planners.md | Complete |
| 6. Snapshot create planner + guest binary (MODE_CREATE), plus the minimal `-c` host dispatch pulled forward from phase 9 (see open question 1 in that plan) | PLAN-snapshot-phase-06-create.md | Complete |
| 7. Snapshot delete planner + guest binary (MODE_DELETE), plus the `-d` host dispatch (pulled forward like `-c` was) and the shared `run_snapshot_mutating_guest` launch helper | PLAN-snapshot-phase-07-delete.md | Complete |
| 8. Snapshot apply planner + guest binary (MODE_APPLY), plus the `-a` host dispatch (pulled forward like `-c` / `-d`), the shared raw-table finder (`find_snapshot_in_table`, ID-then-name for apply / name-only for delete), and the walker stale-flag scrub | PLAN-snapshot-phase-08-apply.md | Complete |
| 9. Host CLI consolidation and parity: D1 fix (`-U` with mutating modes refused before file access), D2 fix (bare `snapshot FILE` defaults to list), D3 documented (mixed-mode exit-code delta kept as-is), launch consolidation declined (renderer borrow fights the helper boundary), `tools/snapshot-cli-parity.sh` (30 assertions, all passing), quirks docs updated. | PLAN-snapshot-phase-09-mutate-host.md | Complete |
| 10. Cross-version baselines: snapshot-bearing qcow2 fixtures, `snapshot-list-human` profiles in `generate-baselines.py` (JSON deferred to phase 11 — no qemu source of truth; mutation baselines dropped — column drift already captured by listing frozen fixtures), baseline generation pass for 80 versions. FINDING: snapshot names >63 bytes are truncated by the list parser (bug fixed post-landing: `SnapshotEntry::name` widened to `[u8;256]`, cap raised to `.min(255)`; `snap-qcow2-longname` profile updated to full 200-byte baseline; see "Bugs fixed during this work"). | PLAN-snapshot-phase-10-baselines.md | Complete |
| 11. Integration tests (`tests/test_snapshot.py`): list matrix, create/delete/apply round-trips, error paths, qcow2-only enforcement, post-op `qemu-img check` clean. **Fixtures, profiles, and manifest tag `snapshots` are ready from phase 10.** JSON golden files for `--output=json` belong here (instar-side self-baseline; no qemu source of truth). 94 tests: 92 pass, 2 skip (qcow2-snapshots: no phase-10 baseline), 0 fail; suite wall time ~1s; JSON goldens in `tests/golden/snapshot-list/`. Test families: (a) list-matrix (b) JSON-goldens + vmstate structural + QMP-key schema (c) mutation round-trips (d) error paths + qcow2-only enforcement (e) empty-table. | PLAN-snapshot-phase-11-integration-tests.md | Complete |
| 12. Coverage-guided fuzz harnesses: `fuzz_snapshot_parse`, `fuzz_snapshot_refcount` | PLAN-snapshot-phase-12-fuzz-coverage.md | Complete |
| 13. Differential fuzzing extension: random `-c/-d/-a` chain vs qemu-img, structural `qemu-img info` comparison | PLAN-snapshot-phase-13-fuzz-differential.md | Complete |
| 14. Documentation, CHANGELOG, follow-ups (`docs/snapshot.md`, quirks, usage, ARCHITECTURE, README, AGENTS, `PLAN-convert-followups.md` final strike-through) plus the four deferred dispositions, three of which were code: `find_snapshot` reworked to qemu's two-full-pass ID-then-name matcher (fixing a real `convert --snapshot` collision bug; dead `find_snapshot_streaming` removed), zero-`date_sec` now renders the epoch like qemu, dead `SnapshotPlan`/`SnapshotPatch` API removed (with the fuzz-target op-7 and corpus-seeder ripple), and the seven shell harnesses wired into CI via `make snapshot-harnesses` + a functional-tests job | PLAN-snapshot-phase-14-docs.md | Complete |

### Phase notes (effort and model)

Each phase plan is written one at a time, immediately before
the phase is executed. Recommended planning effort and
recommended sub-agent model per phase:

- **Phase 1 (ABI)**: medium effort, sonnet. The pattern is
  identical to `CreateConfig` / `ResizeConfig` /
  `RebaseConfig` / `MapConfig`. The `fsync_input` extension
  is novel but mechanical. Worktree isolation recommended
  for the `version` bump.
- **Phase 2 (list planner)**: medium effort, opus.
  Extra-data parsing has alignment/endianness traps; opus
  for the qcow2-spec cross-reference.
- **Phase 3 (list guest)**: medium effort, sonnet. Mostly
  scaffolding mirroring `src/operations/map/`.
- **Phase 4 (list host)**: medium effort, sonnet. Column
  formatting against `block/qcow2-snapshot.c::dump_one_snapshot`
  is fiddly but well-specified.
- **Phase 5 (refcount mutators)**: **high effort, opus.**
  This is the riskiest phase in the plan. Refcount + COPIED-
  flag invariants are the single biggest source of qcow2
  corruption bugs; the mutators must be written carefully
  and unit-tested exhaustively. Worktree isolation
  mandatory.
- **Phase 6 (create)**: high effort, opus. Reuses phase 5
  mutators but coordinates the multi-step rewrite ordering.
  Worktree isolation.
- **Phase 7 (delete)**: high effort, **fable**. Snapshot-table
  compaction is the new work here. Worktree. (Originally
  "opus"; see the model-tier update below.)
- **Phase 8 (apply)**: high effort, opus. L1-overwrite
  ordering is delicate; the dec-then-inc-then-overwrite
  sequence has correctness subtleties. Worktree.
- **Phase 9 (mutate host)**: medium effort, sonnet.
  Dispatch over phases 6/7/8.
- **Phase 10 (baselines)**: low effort, sonnet. New fixture
  generation + script extension; long-running but
  mechanical.
- **Phase 11 (integration tests)**: medium effort, sonnet.
- **Phase 12 (coverage fuzz)**: medium effort, opus for
  harness invariants (the "no corruption regardless of
  input" assertion needs care), sonnet for the boilerplate.
  (As executed: a single fable agent did both — the
  invariant judgement was the phase's bulk, and two of the
  plan's pinned invariants needed source-grounded
  refinement before they were safe to assert.)
- **Phase 13 (differential fuzz)**: high effort, opus.
  Designing the structural-comparison assertion to be
  strict enough to catch bugs but lenient enough to allow
  legitimate ID-format differences across qemu-img versions
  is the hard part.
  (As executed: single fable agent for implementation, with
  verification completed in the management session past the
  sub-agent permission boundary. The strict byte-identity
  comparator earned its keep immediately — a real renderer
  bug and two dead-byte normalization rules on the first
  runs.)
- **Phase 14 (docs)**: low effort, haiku or sonnet.

When in doubt, skew to the more capable model. Phases 5–8
are the riskiest; the management session should review
sub-agent output against the qcow2 spec, not just against
the test suite (a failing test is a known unknown; a
quietly-broken refcount is an unknown unknown).

**Model-tier update (post phase 6):** Fable became available
as a sub-agent tier above opus after phase 6 landed. From
phase 7 onward, steps the notes above mark "opus" for risk
reasons should be considered for **fable** instead —
"skew to the more capable model" now points at fable.
Phase 7 runs as a single fable agent as a deliberate
experiment, chosen because its shape (crate helpers + guest
mode + host dispatch + verification matrix) is directly
comparable to the opus-run phase 6; evaluate the result
before updating `PLAN-TEMPLATE.md`.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making.

The workflow per step:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with
   the brief from the phase plan.
3. **Review** the sub-agent's output in the management
   session. Read the actual files; don't trust the summary.
   For phases 5–8 this includes manually walking through a
   small example: pick a 2-cluster qcow2 with one snapshot,
   trace what the mutator should do, and diff against what
   it did.
4. **Fix or retry** if the output is wrong.
5. **Commit** once the management session is satisfied.

Use `isolation: "worktree"` for any phase that mutates
refcounts (phases 5, 6, 7, 8). Use it also for phase 1
(ABI bump) and phase 10 (baseline generation, which writes
many fixture files into instar-testdata). Phases 2, 3, 4,
9, 11–14 can run in the main tree.

### Planning effort

The master plan itself is high effort. See the per-phase
notes above for phase-by-phase effort recommendations.

### Step-level guidance

Each phase plan should fill in the table:

```
| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
```

following `PLAN-TEMPLATE.md` conventions.

For this plan in particular, model choice should default
to **opus** for any step in phases 5–8 (refcount mutators,
create, delete, apply). Sonnet is fine for clap parsing,
output rendering, and integration test boilerplate.

### Management session review checklist

After a sub-agent completes, the management session
verifies:

- [ ] The files that were supposed to change actually
      changed (read them).
- [ ] No unrelated files were modified.
- [ ] `make instar` builds and `make lint` is clean.
- [ ] Guest binaries pass `make check-binary-sizes`
      (384 KB limit per operation).
- [ ] `make test-rust` and the relevant
      `make test-integration` targets pass.
- [ ] `pre-commit run --all-files` passes.
- [ ] The changes match the intent of the brief —
      semantically right, not just syntactically.
- [ ] For phases 5–8: `qemu-img check` on a post-op
      image is clean; `qemu-img info` reports the
      expected snapshot state.
- [ ] Commit message follows project conventions
      (Co-Authored-By with model + context window +
      effort, Signed-off-by, Prompt paragraph).

## Administration and logistics

### Success criteria

The plan is complete when (final sweep annotations from the
phase 14 close-out in brackets):

* All 14 phases complete and committed on the `snapshot`
  branch. [Met — see the Execution table; every row Landed.]
* `make instar` builds with `snapshot.bin` within the
  384 KiB operation-binary cap. [Met — 55 KiB / 384 KiB
  (14%) at close-out.]
* `make lint` clean across the workspace. [Met.]
* `make test-rust` passes; new tests in `qcow2` /
  `snapshot` raise totals as documented in each phase
  plan. [Met — at close-out the qcow2 crate runs 122 tests
  and the snapshot crate 127, including phase 14's
  collision-matcher and epoch-rendering pins.]
* `make test-integration` includes `tests/test_snapshot.py`;
  test count and pass/skip breakdown documented in each
  phase plan. [Met — 94 tests: 92 pass, 2 skip (phase 11),
  plus the phase 14 `convert --snapshot` collision
  regression test in `tests/test_convert.py`.]
* `make check-binary-sizes` includes `snapshot.bin`. [Met.]
* `pre-commit run --all-files` clean throughout. [Met.]
* For qcow2 sources: `instar snapshot -l` matches
  `qemu-img snapshot -l` byte-for-byte (both human and
  json, with the documented `--output=json` extension)
  across every qemu-img version in
  `instar-testdata/qemu-img-binaries/x86_64/`, modulo
  documented quirks. [Met — phase 10's 80-version baseline
  matrix + phase 11's list matrix; instar tracks the modern
  ≥9.0 layout, with the old-format profiles captured and the
  divergence documented in docs/quirks.md. The phase 14
  zero-`date_sec` fix removed the last known list-mode
  divergence.]
* For qcow2 sources: post-`instar snapshot -c/-d/-a`
  images satisfy `qemu-img check` clean and produce
  `qemu-img info` output identical to the same operation
  run under qemu-img (modulo `date_sec`/`date_nsec`).
  [Exceeded — phases 6-8 and 13 established full
  byte-identity of the post-op images under
  `file.discard=ignore`, not just info-equivalence; 241
  harness assertions + `op_snapshot` byte-identity after
  every chain element.]
* Coverage-guided fuzz targets `fuzz_snapshot_parse` and
  `fuzz_snapshot_refcount` registered in nightly CI.
  (Phase 12: both targets registered in the workflow's
  nightly list and the corpus seeder; the next nightly run
  picks them up.) [Met; phase 14 removed the
  never-adopted op 7 / invariant 8 with the SnapshotPlan
  API.]
* Differential fuzzer's random operation chain includes
  `snapshot -c/-d/-a`. (Phase 13: `op_snapshot` chains
  create/delete/apply/write with byte-identity after every
  element; its first runs found and led to fixing a real
  multibyte list-padding bug and surfaced two dead-byte
  normalization rules, both documented in docs/quirks.md.)
  [Met.]
* `docs/snapshot.md`, `docs/quirks.md`, `docs/usage.md`,
  `README.md`, `AGENTS.md`, `ARCHITECTURE.md`, and
  `CHANGELOG.md` all updated. [Met — phase 14; also
  `docs/index.md`, `docs/testing.md`, and
  `docs/qcow2/qcow2-snapshots.md`.]
* `PLAN-convert-followups.md` strikes `snapshot` from the
  deferred-subcommand list (it then has zero deferred
  subcommands left; phase 1 of that plan is complete
  pending only the `check --repair` phase 2 work). [Met —
  phase 14.]

Operational note: the instar-testdata `snapshot-baselines`
branch (phases 10-13 baselines) awaits operator review and
push; nothing in-tree depends on it, but the plan is not
operationally complete until it lands on testdata main.

### Future work

* **Compressed-cluster support.** v1 errors on `-c`/`-d`/
  `-a` for images with compressed clusters. The refcount
  update for compressed extents must walk sub-cluster
  byte ranges (an extent may span a partial cluster at
  both ends). qemu's `qcow2_update_refcount_for_compressed`
  is the reference. Probably a single follow-up phase.
* **Bitmaps extension.** qcow2 v3 dirty bitmaps reference
  clusters; snapshots must update bitmap refcounts too.
  Defer until any user asks.
* **External data file.** Snapshot semantics with an
  external data file are subtle (the L2 entries point at
  raw offsets in the external file, not at the qcow2
  file); needs dedicated thought.
* **Encrypted images.** Requires LUKS write path, which
  is not yet in instar. Tracked separately.
* **Snapshot counts beyond the 16-entry caps.** List mode
  already streams (one entry resident at a time, up to the
  qcow2 spec max of 65536 — the phase 2 resolution; an
  earlier version of this entry claiming a 256-entry list
  cap was stale). What remains capped at 16
  (`MAX_SNAPSHOTS`) is the mutating side — `-c` refuses to
  create a 17th snapshot — and the bounded
  `parse_snapshot_table` consumers (see the convert lookup
  entry below). Raising these needs an extreme-count
  fixture in the test matrix. Defer.
* **Convert's 16-entry snapshot lookup cap** (phase 14).
  `convert --snapshot` resolves its argument over the
  bounded 16-entry `parse_snapshot_table`, so a snapshot
  stored beyond the first 16 table entries is not-found
  under instar where `qemu-img convert -l` finds it. Same
  cap family as the create cap above; documented in
  docs/quirks.md (convert section). Defer.
* **`-l` with `--all-data-images`-style chain walk.**
  qemu-img doesn't have this; not a follow-up.
* **VM state on `-c`** (i.e. snapshot a running VM via
  instar). Not applicable — instar operates on stopped
  images only. Documented; not a follow-up.
* **`fsync_input` rollout to other writers.** Phase 1
  adds the call-table primitive for snapshot. commit
  currently relies on process-exit fsync — switching it
  to use `fsync_input` at the appropriate checkpoint
  would be a small follow-up.
* **disk_size-mismatch apply** (phase 8). qemu truncates
  the image to the snapshot's `disk_size` inside
  `qcow2_snapshot_goto`; instar v1 refuses with a
  resize-back workaround message. A follow-up could
  compose the resize planner with apply; only worth it if
  a user actually hits the refusal.
* ~~**`qcow2::find_snapshot` disposition** (phase 8).~~
  **Resolved in phase 14.** The "unused" claim was stale:
  `convert --snapshot` called it, and its per-entry
  id-or-name walk picked the wrong snapshot on
  ID/name-collision images (probe 1 of the phase 14 plan).
  Fixed by reworking it to qemu's two-full-pass
  ID-then-name shape (`find_snapshot_by_id_or_name`, the
  same semantics `-a` implements); the genuinely unused
  `find_snapshot_streaming` companion was deleted. The
  bounded-lookup residual is the convert 16-entry cap
  entry above.
* **`instar resize` on snapshot-bearing qcow2 images**
  (observed in passing during phase 8, *not* snapshot
  scope): fails with a confusing internal-inconsistency
  error (error 13) where qemu-img resizes successfully.
  Worth an eventual fix in the resize plan family — and it
  is the tooling path that creates the disk_size-mismatch
  fixtures above.

### Bugs fixed during this work

This section will list any bugs encountered during
development that we fix in passing.

* **Phase 8**: the flags walker
  (`update_copied_flags_for_l1`, phases 5–7) skipped
  UNALLOCATED / ZERO_PLAIN L2 entries entirely, where
  qemu's `qcow2_update_snapshot_refcount` assigns them
  `refcount = 0` and so actively scrubs a stale COPIED
  bit on every walk. Fixed (with unit tests); the
  create/delete byte-identity matrices are unaffected
  because qemu-maintained images never carry such stale
  bits — the scrub only improves fidelity on
  contrived-but-valid images.

* **Phase 10 finding / post-landing fix**: list-mode name
  truncation. The streaming parser's `SnapshotEntry::name`
  field was `[u8; 64]` with a `.min(63)` copy cap, so
  snapshot names longer than 63 bytes were silently
  truncated in `instar snapshot -l` output. Surfaced by
  the `snap-qcow2-longname` fixture (200-byte name) in the
  phase 10 cross-version baseline matrix. Fixed by widening
  `SnapshotEntry::name` to `[u8; 256]` and raising the cap
  to `.min(255)`, matching the wire record's 256-byte name
  field. All names qemu-img can create (≤255 bytes) now
  list byte-identically. See `docs/quirks.md` (snapshot
  subcommand section) for the residual note.

* **Phase 13**: multibyte list-column padding. The list
  renderer padded ID/TAG with Rust's char-counting `{:<7}`
  / `{:<16}` where qemu's C `printf` counts bytes,
  over-padding multibyte UTF-8 names. Fixed to byte-measured
  padding (commit `5f6a1b9`); found by the differential
  fuzzer's first smoke run.

* **Phase 13 (soak)**: delete left stale COPIED flags in
  L2 tables shared between the deleted and a surviving
  snapshot — safe (spurious COW at worst, check clean) but
  not byte-identical. Delete now refreshes the deleted
  chain's staged L2 set and writes back the surviving
  snap-set L2s, matching qemu's `-1` walk (commit
  `a5d0767`).

* **Phase 14**: `convert --snapshot` collision bug — the
  per-entry id-or-name `find_snapshot` returned the first
  hit of either kind, extracting the wrong snapshot when a
  name collides with a later ID. Fixed to qemu's two-pass
  shape (probe 1; collision unit tests + a convert
  integration regression test added).

* **Phase 14**: zero-`date_sec` snapshot entries rendered a
  blank DATE column where qemu renders the epoch. Early
  return removed (probe 2); unreachable via either tool's
  create, hand-crafted images only.

### Documentation index maintenance

This plan should be registered in `docs/plans/index.md`
and `docs/plans/order.yml` when it is created. Phase files
are linked from the Execution table above and are *not*
added to `order.yml`.

When all phases are complete, update the row in
`index.md` to *Complete*.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
