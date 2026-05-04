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
