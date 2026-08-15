# QCOW2 Snapshot System

QCOW2 supports internal snapshots that capture the state of the virtual disk
at a point in time. Each snapshot maintains its own L1 table, enabling
efficient storage through copy-on-write.

## Snapshot Table Location

The snapshot table is stored at `snapshots_offset` (header bytes 64-71).
The number of snapshots is in `nb_snapshots` (header bytes 60-63).

## Snapshot Table Format

The snapshot table is a contiguous area containing variable-length entries:

```
+------------------+
| Snapshot 1       | (variable size, 8-byte aligned)
+------------------+
| Padding          | (to 8-byte boundary)
+------------------+
| Snapshot 2       |
+------------------+
| Padding          |
+------------------+
| ...              |
+------------------+
```

## Snapshot Header Structure

```c
typedef struct QCowSnapshotHeader {
    uint64_t l1_table_offset;      // Snapshot's L1 table (cluster-aligned)
    uint32_t l1_size;              // Number of L1 entries
    uint16_t id_str_size;          // Unique ID string length
    uint16_t name_size;            // Snapshot name length
    uint32_t date_sec;             // Creation time (seconds since epoch)
    uint32_t date_nsec;            // Nanoseconds component
    uint64_t vm_clock_nsec;        // VM clock at snapshot time
    uint32_t vm_state_size;        // VM state size (32-bit, legacy)
    uint32_t extra_data_size;      // Size of extra data following header
    // Extra data follows (if extra_data_size > 0)
    // ID string follows (id_str_size bytes)
    // Name string follows (name_size bytes)
    // Padding to 8-byte boundary
} qemu_PACKED QCowSnapshotHeader;
```

**Size:** 48 bytes (base) + extra_data + id_str + name + padding

## Extended Snapshot Data

Version 3 images include extra data after the base header:

```c
typedef struct QCowSnapshotExtraData {
    uint64_t vm_state_size_large;  // 64-bit VM state size
    uint64_t disk_size;            // Virtual disk size at snapshot
    uint64_t icount;               // Instruction count (record/replay)
} qemu_PACKED QCowSnapshotExtraData;
```

**Size:** 24 bytes minimum for version 3

Version 3 images must have `extra_data_size >= 16` (at least bytes 0-15).

## In-Memory Snapshot Representation

```c
typedef struct QCowSnapshot {
    uint64_t l1_table_offset;
    uint32_t l1_size;
    char *id_str;                  // Unique identifier
    char *name;                    // Human-readable name
    uint64_t disk_size;
    uint64_t vm_state_size;
    uint32_t date_sec;
    uint32_t date_nsec;
    uint64_t vm_clock_nsec;
    uint64_t icount;
    uint32_t extra_data_size;
    void *unknown_extra_data;      // For forward compatibility
} QCowSnapshot;
```

## Parser surface

The qcow2 crate exposes two parser variants:

- **`parse_snapshot_table`** — bounded. Returns a `SnapshotTable`
  with up to `MAX_SNAPSHOTS` (16) entries on the caller's stack.
  Used by `info` (to set the `FLAG_HAS_SNAPSHOTS` bit) and
  `convert` (whose `--snapshot` resolver, `find_snapshot`, runs
  qemu's two-full-pass ID-then-name match over the parsed table
  before switching to the found snapshot's L1 — see
  docs/quirks.md's convert section, including the bounded
  16-entry lookup residual). Behaviour is byte-identical to
  the pre-streaming implementation; the bounded variant is now
  a thin wrapper over `for_each_snapshot_entry` that stops once
  the 16-entry array is full.
- **`for_each_snapshot_entry`** — streaming, no in-memory cap.
  Invokes a `FnMut(&SnapshotEntry) -> bool` callback once per
  entry. Bounded only by the qcow2 header's `nb_snapshots`
  field (spec cap 65536). Aborts and returns `false` on a read
  error or when the callback returns `false`. Used by the
  snapshot subcommand to emit one wire record per entry
  without ballooning the guest's stack. (An unused
  `find_snapshot_streaming` convenience wrapper with the old
  per-entry id-or-name semantics was removed during the PLAN-snapshot work.)
- **`snapshot_entry_to_record`** — planner converter from the
  internal `SnapshotEntry` to the wire-FFI
  `shared::SnapshotEntryRecord`. Splits `date_sec` into hi/lo
  halves, truncates id (32) and name (256) to the wire buffer
  sizes, and resolves the v2 `disk_size` fallback by taking the
  active image's virtual size as a parameter.

### Extra-data fallback rules

qemu's `block/qcow2-snapshot.c::qcow2_read_snapshots` applies
progressive-reveal rules to the extra-data section: fields are
populated only when `extra_data_size` is large enough to carry
them, with sentinel values otherwise. The instar parser mirrors
this exactly.

| `extra_data_size` | `vm_state_size_large` | `disk_size`           | `icount`       |
|-------------------|-----------------------|-----------------------|----------------|
| `< 8`             | `vm_state_size as u64`| `0` (use header size) | `u64::MAX`     |
| `>= 8`            | from offset 0         | `0` (use header size) | `u64::MAX`     |
| `>= 16`           | from offset 0         | from offset 8         | `u64::MAX`     |
| `>= 24`           | from offset 0         | from offset 8         | from offset 16 |
| `> 1024`          | (rejected — entry is skipped per `QCOW_MAX_SNAPSHOT_EXTRA_DATA`) | | |

`disk_size == 0` is the v2 / short-v3 sentinel meaning "fall
back to the active header's virtual size"; the converter
`snapshot_entry_to_record` substitutes it at conversion time
because the parser is unaware of the active header.
`icount == u64::MAX` matches qemu's `qcow2_snapshot.icount = -1`
and the `SnapshotEntryRecord::ICOUNT_ABSENT` constant.

## Mutator surface

The mutating snapshot operations (`-c` / `-d` / `-a`) compose
their per-mode patch lists from pure mutator primitives in the
`src/crates/snapshot/` crate (parallel to `commit` and `rebase`;
landed during the PLAN-snapshot work). The crate is `no_std`, depends
only on `qcow2` for type / constant access, and operates on
caller-staged byte slices without any I/O.

- **`read_refcount_in_block`** / **`set_refcount_in_block`** —
 scalar accessors for every spec-permitted refcount width
 (1, 2, 4, 8, 16, 32, 64). The setter was lifted from
 `resize::qcow2::set_refcount`; resize calls it through a
 thin wrapper.
- **`check_refcount_after_addend`** — overflow-safe arithmetic
 used by the two-pass refcount mutator's dry-run pass.
- **`alloc_cluster_in_refblocks`** — cursor-driven linear scan
 over staged refcount blocks. Claims the first zero entry and
 returns its host byte offset. v1 supports 16-bit refcounts
 only.
- **`rewrite_l1_entry_copied_flag`** / **`rewrite_l2_entry_copied_flag`** —
 set or clear `OFLAG_COPIED` on one L1 / L2 entry. The L2
 helper handles both standard (8-byte stride) and extended-L2
 (16-byte stride; subcluster bitmap untouched) layouts.
- **`for_each_cluster_in_l1`** — visitor that walks the
 L1 -> L2 chain and yields one `L1ClusterRef` per allocated
 cluster (Standard or Compressed); unallocated L1 and L2
 entries are skipped.
- **`update_snapshot_refcount`** — two-pass composed mutator.
 Pass 1 walks the relevant L1(s) and runs
 `check_refcount_after_addend` for every cluster; on the first
 overflow it returns `RefcountOverflow { at_host_offset }`
 *before* mutating any refblock. Pass 2 walks again and applies
 the new refcounts via `set_refcount_in_block`. Handles
 `IncrementForCreate`, `DecrementForDelete`, and
 `SwapForApply { from_l1, to_l1 }`. Both passes adjust every
 reachable **data** cluster **and** each **L2 table cluster** —
 once per non-zero L1 entry, after that entry's data clusters —
 mirroring qemu's `qcow2_update_snapshot_refcount`
 (`block/qcow2-refcount.c`). The L2-table bump is mandatory for
 create: after a create the active L1 and the snapshot's L1 copy
 share the same physical L2 tables, so each L2 cluster's refcount
 must reach 2 for a later guest write to trigger the L2
 copy-on-write instead of silently overwriting the snapshot's L2
 in place. The function never touches the L1 table's own
 clusters — the caller owns those (create allocates the snapshot
 L1 copy at refcount 1; delete frees the snapshot L1 explicitly).
 *(The L2-table coverage was added during the PLAN-snapshot work,
 closing a correctness gap.)*
- **`update_copied_flags_for_l1`** — walks the L1, rewriting the
 `OFLAG_COPIED` flag on each L1 and L2 entry based on the
 cluster's current refcount (set when refcount==1, clear
 otherwise). Returns the number of entries rewritten. L2 entries
 that reference no cluster (UNALLOCATED, or ZERO_PLAIN on
 standard L2) are **scrubbed**, not skipped: qemu's
 `qcow2_update_snapshot_refcount` strips `OFLAG_COPIED` before
 classifying and assigns `refcount = 0` to those entry types, so
 a stale COPIED bit is actively cleared on every walk — the
 walker mirrors that (added during the PLAN-snapshot work, closing a
 fidelity gap; the extended-L2 subcluster bitmap is
 untouched).

The create planner adds these table-serialisation
helpers (`src/crates/snapshot/src/table.rs`):

- **`alloc_contiguous_clusters_in_refblocks`** — first-fit scan
  for `count` *consecutive* zero-refcount clusters (allowed to
  span refblock boundaries), claiming each. The single-cluster
  `alloc_cluster_in_refblocks` is now a `count = 1` wrapper.
- **`NewSnapshotEntry`** + **`serialize_snapshot_entry`** — emit
  one new on-disk entry: 40-byte big-endian header,
  `extra_data_size = 24`, the 24-byte extra data
  (`vm_state_size_large` / `disk_size` / `icount`), then the id
  and name strings, with no trailing pad. Matches `qemu-img`
  10.0.x byte-for-byte (`icount` written as `0`, not the
  `u64::MAX` "absent" sentinel the read side uses).
- **`snapshot_table_byte_len`** — walk the raw old table for
  `nb_snapshots` entries (8-aligned starts) and return its exact
  unpadded byte length, so the guest can stage / copy / free it.
- **`build_snapshot_table`** — copy the old entries verbatim
  (preserving any unknown trailing extra data), zero-pad to the
  next 8-byte boundary, and append the serialised new entry.
- **`parse_decimal_id`** / **`format_decimal_u64`** — strtoul- /
  `%lu`-style ID arithmetic for the `max(existing IDs) + 1`
  assignment qemu's `find_new_snapshot_id` performs.

The delete planner adds:

- **`snapshot_table_entry_bounds`** — the (start offset, unpadded
  length) of one raw table entry, walking entries exactly like
  `snapshot_table_byte_len`. Delete's find-by-name walk uses it
  to compare the full on-disk name (independent of the bounded
  parser's 63-byte truncation) and to locate the removed entry.
- **`build_snapshot_table_without`** — the table compaction:
  every entry except the removed one copied verbatim to the next
  8-aligned output offset (gaps zeroed, unpadded tail). Removing
  the sole remaining entry yields length 0; the caller then
  writes header `nb_snapshots = 0, snapshots_offset = 0` and
  allocates no table, matching qemu.
- **`precheck_snapshot_refcount`** (in `qcow2.rs`) — a public
  read-only wrapper over `update_snapshot_refcount`'s dry-run
  pass (pass 1), so delete can validate the decrement against the
  staged refblocks *before any disk write* while deferring the
  paired apply until after the commit-point header write.

The apply planner adds:

- **`MatchMode`** / **`FoundSnapshot`** /
  **`find_snapshot_in_table`** — the raw-table snapshot finder
  with per-mode matching semantics. `NameOnly` is delete's single
  name pass (the inline find was refactored onto it);
  `IdThenName` is apply's two-full-pass resolver (qemu's
  `find_snapshot_by_id_or_name`: a complete ID pass, then — only
  if no ID matched — a complete name pass, so a later ID match
  beats an earlier name match). Comparisons cover the full
  on-disk strings, independent of the bounded parser's 63-byte
  truncation. `FoundSnapshot` carries the entry's index, L1
  geometry, and `disk_size_or_zero` (extra-data offset 8 when
  `extra_data_size >= 16`, else a 0 "absent" sentinel that the
  caller treats as matching — mirroring `qcow2_read_snapshots`'
  default of the current virtual size).

The crate emits no patch lists: the guest binaries write each
staged region directly (commit-binary style), because the
writeback needs `fsync` barriers *between* write groups, which a
flat patch list cannot express. (A speculative `SnapshotPatch` /
`SnapshotPlan` patch-list API sat unused in the crate root
through and was removed during the PLAN-snapshot work.)

### Create write ordering (crash safety)

`instar snapshot -c` writes back in four `fsync`-separated groups,
mirroring qemu's `qcow2_snapshot_create` + `qcow2_write_snapshots`:

```
A: L1 copy (verbatim pre-flag-rewrite bytes), dirty L2 tables,
   the rewritten active L1, and the dirty refcount blocks
   (covering the data / L2 increments and the new allocations)
   -> fsync
B: the new snapshot table (at a freshly allocated, contiguous
   region)
   -> fsync
C: the 12-byte header write at offset 60 — nb_snapshots (u32 BE)
   followed by snapshots_offset (u64 BE). THIS IS THE COMMIT
   POINT.
   -> fsync
D: free the old snapshot table's clusters (decrement their
   refcounts to 0 and write those refblocks back). Skipped when
   there was no old table (nb_snapshots was 0).
   -> fsync
```

The barrier ordering gives the same crash-safety contract as
qemu: a crash *before* group C leaves the old table authoritative
(the new clusters are orphaned garbage — `qemu-img check` reports
leaks, not corruption); a crash *after* group C leaves the new
table authoritative (the old table's clusters leak until group D
runs). Leaks are repairable with `qemu-img check -r`; dangling
references are not, and this ordering never produces them.

The snapshot's L1 copy is serialised from the active L1's bytes
captured **before** the COPIED-flag rewrite, so — exactly like
qemu — the stored copy keeps its (now stale) `OFLAG_COPIED` bits
even though the shared clusters are at refcount 2. `qemu-img
check` validates only the *active* L1/L2 flags, so this is
correct; the apply path refreshes the flags if the snapshot is
ever restored.

### Delete write ordering (crash safety)

`instar snapshot -d` finds the target by **name only, first
match in table order** (qemu 10's `bdrv_snapshot_find` — see
docs/quirks.md), stages BOTH chains (the deleted snapshot's
L1 + L2 set for the decrement walk; the active L1 + L2 set for
the COPIED refresh), then writes back in three `fsync`-separated
groups, mirroring `qcow2_snapshot_delete`:

```
precheck: precheck_snapshot_refcount(DecrementForDelete) over the
   snapshot's chain, plus refcount >= 1 checks on the snapshot's
   L1 clusters and the old table's clusters. Read-only, BEFORE
   any disk write: a corrupt image fails here with the file
   untouched. (qemu has no such check; its equivalent failure
   would surface after the commit point.)
A: the compacted snapshot table (built by
   build_snapshot_table_without at a freshly allocated,
   contiguous region) + all staged refblocks, which at this
   moment carry ONLY the table-allocation bumps. Skipped
   entirely when the remaining snapshot count is 0.
   -> fsync
B: the 12-byte header write at offset 60 — nb_snapshots - 1
   (u32 BE) followed by the new table offset, or 0 / 0 when the
   table is now empty. THIS IS THE COMMIT POINT.
   -> fsync
   (in-memory, qemu's "we won't recover but just leak clusters"
    zone: update_snapshot_refcount(DecrementForDelete) over the
    snapshot's chain, then decrement the snapshot's L1 clusters,
    then the old table's clusters — decrements, never set-to-0,
    so an underflow surfaces a double-free bug. Then the COPIED
    refresh over the ACTIVE chain against the post-decrement
    refcounts — shared data clusters that dropped 2 -> 1 get
    COPIED SET, the reverse direction from create — AND over the
    deleted chain's staged L2 set, mirroring qemu's -1 walk,
    which recomputes flags on every L2 entry it visits. The
    deleted snapshot's L1 buffer is mutated in place but never
    written — qemu's "update L1 only if addend >= 0" exemption,
    and it is being freed anyway.)
C: all staged refblocks (now carrying the decrements) + the
   active L1 + the active L2 set + the SURVIVING snap-set L2s
   (those whose own cluster's post-decrement refcount is
   non-zero, e.g. L2 tables shared with another snapshot, which
   land on disk with refreshed COPIED flags). Freed L2s are
   never written, matching qemu's cache discard.
   -> fsync
```

A crash before group B leaves the old table authoritative and at
worst an orphaned compacted table (a leak); a crash after group B
but before group C completes leaves the snapshot gone with
refcounts too *high* and/or stale COPIED flags — leaks and
repairable flag warnings, never a dangling reference. Because
delete writes no timestamps, the post-delete image is
byte-identical to qemu's given byte-identical inputs (modulo
freed-cluster contents and the file tail — docs/quirks.md). The
surviving-L2 write-back was added post-phase-13: the
differential fuzzer caught a deleted-snapshot L2 shared with a
surviving snapshot landing with stale COPIED-clear entries
(safe — a spurious COW at worst — but not byte-identical).

### Apply write ordering (crash safety)

`instar snapshot -a` finds the target by **ID first, then name —
two full passes** (qemu's `find_snapshot_by_id_or_name`; see
docs/quirks.md for the `-d` / `-a` asymmetry), refuses geometry
mismatches (a stored `disk_size` differing from the current
virtual size, or a snapshot L1 larger than the active L1 — qemu
truncates / grows respectively; docs/quirks.md), stages BOTH
chains (the target snapshot's L1 + L2 set; the old active L1 +
L2 set), then writes back in three `fsync`-separated groups,
mirroring `qcow2_snapshot_goto`. Apply rewrites the **active L1
in place** and never touches the snapshot table or the header:

```
precheck: precheck_snapshot_refcount(SwapForApply { from: active
   L1, to: snapshot L1 }) — both directions (decrement underflow
   on the outgoing chain, increment overflow on the incoming
   one) validated read-only, BEFORE any disk write.
   (in-memory: update_snapshot_refcount increment walk over the
    snapshot's chain — qemu's +1 walk)
A: all staged refblocks, carrying the increments only.
   -> fsync
B: the snapshot's RAW L1 content, zero-padded to the active L1's
   byte size (hdr.l1_size * 8), written at hdr.l1_table_offset —
   stale COPIED flags intact, mirroring qemu's bdrv_pwrite_sync.
   THIS IS THE COMMIT POINT: the active view is now the snapshot.
   -> fsync
   (in-memory: the -1 walk over the staged OLD active chain, then
    ONE final-state COPIED refresh over the padded new-L1 copy +
    the snapshot's L2 set, and over the staged old active chain —
    qemu's -1 walk also refreshes the old chain's surviving L2s)
C: all staged refblocks (now carrying the decrements) + the
   refreshed L1 written to BOTH locations — hdr.l1_table_offset
   at the padded length AND sn.l1_table_offset at sn.l1_size * 8
   (replicating the snapshot-stored-L1 flag write qemu's +1 walk
   performs) — + the dirty snapshot-set L2s + the surviving
   old-active L2s (final refcount > 0, e.g. shared with another
   snapshot). Freed old-active L2s are NEVER written (qemu runs
   the walks with cache_discards = true, so dirty cache entries
   for freed clusters are dropped, not flushed).
   -> fsync
```

**Why one flag pass suffices** (qemu performs three flag-bearing
writes: the snapshot's stored L1 mid-state during the +1 walk,
the raw padded copy, then the active L1 at final state in the
addend-0 walk): after an apply, every cluster reachable from the
new active chain has refcount >= 2 — the active L1 is a copy of
the snapshot's L1, so everything the active view references is
also referenced by the still-present snapshot. Every COPIED flag
on the new chain therefore ends *clear*, and the flags qemu
computes mid-state equal the flags at final state. instar
computes flags once, at final state, and writes the same bytes.

A crash before group B leaves the image unchanged except
over-referenced refcounts (repairable leaks); a crash between B
and C leaves the active view switched with leaks and stale
COPIED flags — repairable, never a dangling reference. One
window differs cosmetically from qemu (qemu scrubs the
snapshot's stored L1 *before* its active overwrite, instar
after); both orders leave only repairable states and the final
bytes are identical. Because apply writes no timestamps, no
snapshot-table bytes and no header bytes, post-apply images are
byte-identical to qemu's given byte-identical inputs across
every scenario, including diverged applies (modulo freed-cluster
contents and the file tail — docs/quirks.md).

## Snapshot L1 Table

Each snapshot has its own L1 table, independent of the "active" L1 table.
This is the key to copy-on-write:

```
Active State:
  L1 (active) --> L2 tables --> Data clusters

After Snapshot:
  L1 (active) --> L2 tables --> Data clusters
                      ^              ^
  L1 (snapshot) ------+              |
                                     |
  (Both L1s may point to same L2/data until modified)
```

When data is written after a snapshot:
1. Check if cluster is shared (refcount > 1)
2. If shared, allocate new cluster (COW)
3. Copy data, write new data
4. Update active L1/L2 to point to new cluster
5. Decrement refcount on old cluster

## Snapshot Operations

### Creating a Snapshot

```
1. Flush all pending writes
2. Allocate space for snapshot L1 table
3. Copy current L1 table to snapshot location
4. Increment refcounts for all referenced clusters
5. Clear COPIED flags on shared clusters
6. Allocate new snapshot table entry
7. Write snapshot header with metadata
8. Update header (nb_snapshots, snapshots_offset)
9. Free old snapshot table if reallocated
```

### Restoring a Snapshot (goto)

```
1. Validate snapshot L1 table offset/size
2. Grow current L1 table if needed
3. Increment refcounts for snapshot's clusters
4. Decrement refcounts for current L1's clusters
5. Copy snapshot L1 to current L1
6. Update COPIED flags based on new refcounts
7. Clear DIRTY flag if set
```

### Deleting a Snapshot

```
1. Load snapshot's L1 table
2. Decrement refcounts for all referenced clusters
3. Free clusters with refcount reaching 0
4. Set COPIED flags where refcount becomes 1
5. Free snapshot's L1 table
6. Remove entry from snapshot table
7. Write updated snapshot table
8. Update header (nb_snapshots)
```

### Listing Snapshots

```
For each snapshot:
  - ID: unique identifier
  - Name: human-readable name
  - Date: creation timestamp
  - VM state size: saved RAM size
  - Disk size: virtual disk size at snapshot time
```

## VM State Storage

Snapshots can include VM state (memory, device state) for hibernation:

- VM state is stored as regular data in the image
- Located via L1 table entries beyond virtual disk size
- `vm_state_size` / `vm_state_size_large` indicates size
- Address: `l1_vm_state_index << (cluster_bits + l2_bits)`

The active image's VM state area is typically discarded after snapshot
creation to avoid unnecessary copy-on-write.

## Snapshot Table Limits

```c
#define QCOW_MAX_SNAPSHOTS           65536
#define QCOW_MAX_SNAPSHOTS_SIZE      (1024 * QCOW_MAX_SNAPSHOTS)  // 64 MB
#define QCOW_MAX_SNAPSHOT_EXTRA_DATA 1024
```

## Refcount Updates for Snapshots

The function `qcow2_update_snapshot_refcount()` handles bulk updates:

```c
int qcow2_update_snapshot_refcount(
    BlockDriverState *bs,
    int64_t l1_table_offset,   // Snapshot's L1 table
    int l1_size,               // Number of L1 entries
    int addend                 // +1 for create, -1 for delete
) {
    // For each L1 entry:
    //   Load L2 table
    //   For each L2 entry:
    //     If compressed: update refcount for compressed extent
    //     If normal: update refcount for cluster
    //     Update COPIED flag if needed
}
```

## Snapshot Consistency

Atomic updates are critical for snapshot table modifications:

1. Allocate new snapshot table at new location
2. Write all snapshot entries
3. Update header (nb_snapshots, snapshots_offset) atomically
4. Only after header update succeeds, free old table

This ensures crash recovery always finds a valid snapshot table.

## Forward Compatibility

The `extra_data_size` field enables future extensions:
- Unknown extra data is preserved on read
- Written back unchanged on update
- Allows older qemu to handle newer snapshot formats

## Common Issues

1. **Snapshot chain depth:** Too many snapshots degrades read performance
2. **Space consumption:** Deleted snapshots may not free space until refcounts drop
3. **VM state size:** Large VM state can significantly increase snapshot size
4. **Consistency:** Snapshots taken during writes may have inconsistent state
